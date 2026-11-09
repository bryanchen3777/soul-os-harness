"""EI-2B-PROGRAM-3 lease table tests: identity capability, no TTL takeover.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.

Contract under test (work order EI-2B-PROGRAM-3 §§2-4):
- persistent FREE/HELD/QUARANTINED rows, never deleted;
- generation +1 on every FREE->HELD, never reset;
- capability (alias,holder_token,request_id,generation) + process
  nonce / pid+create-time / journal epoch; token never returned/logged;
- acquire: only FREE->HELD; HELD with a stale heartbeat still busy
  (expiry never takes over);
- renew: full-capability match on HELD only, generation unchanged;
- release: match + proven quiescence, HELD->FREE;
- recover: ALIVE untouched, UNKNOWN->QUARANTINED, DEAD+quiesced->FREE,
  DEAD+working->QUARANTINED; QUARANTINED leaves only on DEAD+quiesced;
- each op refuses a caller transaction, runs its own BEGIN IMMEDIATE
  with conditional UPDATE + rowcount==1, commit-or-rollback.
"""

from __future__ import annotations

import logging
import sqlite3
import threading

import pytest

from src.interfaces.soul_lease import (
    LEASE_DEFAULT_TTL_SECONDS,
    LIVENESS_ALIVE,
    LIVENESS_DEAD,
    LIVENESS_UNKNOWN,
    STATE_FREE,
    STATE_HELD,
    STATE_QUARANTINED,
    AcquireResult,
    Capability,
    Liveness,
    SoulLeaseError,
    SoulLeaseStore,
    own_process_create_time,
    query_process_liveness,
)

T0 = 1_700_000_000.0
SOUL = "agent_yua"
OTHER = "agent_miku"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _store(tmp_path, name="lease.db") -> SoulLeaseStore:
    return SoulLeaseStore(_open(tmp_path / name))


def _cap(store_tuple) -> Capability:
    soul, token, req, generation = store_tuple
    return Capability(soul, token, req, generation)


def _acquire(store, soul=SOUL, token="tok-1", req="req-1", gen_epoch=1, now=T0):
    return store.acquire(soul, token, req, "nonce-1", 1234, 999.0, gen_epoch, now)


def test_default_ttl_kept_as_observation_only():
    # The TTL constant survives as a stale-heartbeat observation knob;
    # it no longer authorises any takeover (see stale-heartbeat test).
    assert LEASE_DEFAULT_TTL_SECONDS == 1800.0


def test_first_acquire_held_at_generation_one(tmp_path):
    store = _store(tmp_path)
    res = _acquire(store)
    assert isinstance(res, AcquireResult)
    assert res.won is True
    assert res.holder_request_id == "req-1"
    assert res.holder_generation == 1
    assert res.holder_state == STATE_HELD
    row = store.load(SOUL)
    assert row is not None
    assert row["state"] == STATE_HELD
    assert row["generation"] == 1
    assert row["has_token"] is True


def test_acquire_result_carries_no_token(tmp_path):
    store = _store(tmp_path)
    res = _acquire(store, token="super-secret-token")
    blob = repr(res)
    assert "super-secret-token" not in blob
    assert "token" not in blob.lower()
    cap = Capability(SOUL, "super-secret-token", "req-1", 1)
    assert "super-secret-token" not in repr(cap)


def test_second_acquire_loses_with_holder_identity(tmp_path):
    store = _store(tmp_path)
    _acquire(store)
    res = _acquire(store, token="tok-2", req="req-2", now=T0 + 1.0)
    assert res.won is False
    assert res.holder_request_id == "req-1"
    assert res.holder_generation == 1
    assert res.holder_state == STATE_HELD


def test_stale_heartbeat_never_takes_over(tmp_path):
    """PROGRAM-3 reversal: HELD past any TTL stays busy (no takeover)."""
    store = _store(tmp_path)
    _acquire(store, now=T0)
    res = _acquire(store, token="tok-2", req="req-2", now=T0 + 10 * 86400.0)
    assert res.won is False
    assert res.holder_request_id == "req-1"
    row = store.load(SOUL)
    assert row is not None
    assert row["state"] == STATE_HELD
    assert row["holder_request_id"] == "req-1"


def test_renew_refreshes_heartbeat_generation_unchanged(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    cap = Capability(SOUL, "tok-1", "req-1", 1)
    assert store.renew(cap, T0 + 5.0) is True
    row = store.load(SOUL)
    assert row is not None
    assert row["heartbeat"] == T0 + 5.0
    assert row["generation"] == 1


def test_renew_rejects_any_mismatch(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    assert store.renew(Capability(SOUL, "wrong", "req-1", 1), T0 + 1.0) is False
    assert store.renew(Capability(SOUL, "tok-1", "req-other", 1), T0 + 1.0) is False
    assert store.renew(Capability(SOUL, "tok-1", "req-1", 999), T0 + 1.0) is False
    assert store.renew(Capability(OTHER, "tok-1", "req-1", 1), T0 + 1.0) is False
    row = store.load(SOUL)
    assert row is not None
    assert row["heartbeat"] == T0


def test_release_needs_match_plus_quiescence(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    cap = Capability(SOUL, "tok-1", "req-1", 1)
    # Proven working (permits open upstream) is not quiesced: refused.
    assert store.release(cap, T0 + 1.0, quiesced=False) is False
    assert store.load(SOUL)["state"] == STATE_HELD
    # Wrong token: refused even with quiescence claimed.
    assert store.release(Capability(SOUL, "wrong", "req-1", 1), T0 + 1.0, quiesced=True) is False
    assert store.load(SOUL)["state"] == STATE_HELD
    # Match + quiesced: HELD -> FREE, row persists.
    assert store.release(cap, T0 + 2.0, quiesced=True) is True
    row = store.load(SOUL)
    assert row is not None
    assert row["state"] == STATE_FREE
    assert row["generation"] == 1


def test_generation_monotonic_across_holds(tmp_path):
    store = _store(tmp_path)
    _acquire(store, token="tok-1", req="req-1", now=T0)
    store.release(Capability(SOUL, "tok-1", "req-1", 1), T0 + 1.0, quiesced=True)
    res = _acquire(store, token="tok-2", req="req-2", now=T0 + 2.0)
    assert res.won is True
    assert res.holder_generation == 2
    store.release(Capability(SOUL, "tok-2", "req-2", 2), T0 + 3.0, quiesced=True)
    res = _acquire(store, token="tok-3", req="req-3", now=T0 + 4.0)
    assert res.holder_generation == 3
    # FREE rows persist: never deleted, never reset.
    assert store.load(SOUL) is not None


def test_release_missing_row_is_false_not_error(tmp_path):
    store = _store(tmp_path)
    assert store.release(Capability(SOUL, "tok-x", "req-ghost", 1), T0, quiesced=True) is False


def test_recover_alive_never_moves(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    assert store.recover(SOUL, Liveness(LIVENESS_ALIVE, "probe"), T0 + 1.0, True) == STATE_HELD
    assert store.load(SOUL)["holder_request_id"] == "req-1"


def test_recover_unknown_quarantines(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    assert store.recover(SOUL, Liveness(LIVENESS_UNKNOWN, "denied"), T0 + 1.0, True) == STATE_QUARANTINED
    row = store.load(SOUL)
    assert row is not None
    assert row["state"] == STATE_QUARANTINED
    # Quarantined rows block acquisition (busy, never takeover).
    res = _acquire(store, token="tok-2", req="req-2", now=T0 + 2.0)
    assert res.won is False
    assert res.holder_state == STATE_QUARANTINED


def test_recover_dead_quiesced_frees(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    assert store.recover(SOUL, Liveness(LIVENESS_DEAD, "exit 1"), T0 + 1.0, True) == STATE_FREE
    row = store.load(SOUL)
    assert row is not None
    assert row["state"] == STATE_FREE
    # Next hold continues the generation sequence (never reset).
    res = _acquire(store, token="tok-2", req="req-2", now=T0 + 2.0)
    assert res.won is True
    assert res.holder_generation == 2


def test_recover_dead_working_quarantines(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    assert store.recover(SOUL, Liveness(LIVENESS_DEAD, "exit 1"), T0 + 1.0, False) == STATE_QUARANTINED


def test_recover_quarantined_leaves_only_on_dead_quiesced(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    store.recover(SOUL, Liveness(LIVENESS_UNKNOWN, "x"), T0 + 1.0, True)
    # Time + liveness-unknown never frees a quarantine.
    assert store.recover(SOUL, Liveness(LIVENESS_UNKNOWN, "x"), T0 + 10 * 86400.0, True) == STATE_QUARANTINED
    # Alive never frees it either.
    assert store.recover(SOUL, Liveness(LIVENESS_ALIVE, "x"), T0 + 11 * 86400.0, True) == STATE_QUARANTINED
    # Only DEAD + quiesced evidence frees.
    assert store.recover(SOUL, Liveness(LIVENESS_DEAD, "exit 3"), T0 + 12 * 86400.0, True) == STATE_FREE


def test_recover_free_is_noop(tmp_path):
    store = _store(tmp_path)
    assert store.recover(SOUL, Liveness(LIVENESS_DEAD, "x"), T0, True) == STATE_FREE
    assert store.load(SOUL) is None


def test_souls_do_not_interfere(tmp_path):
    store = _store(tmp_path)
    assert _acquire(store, soul=SOUL, token="tok-a", req="req-a").won is True
    assert _acquire(store, soul=OTHER, token="tok-b", req="req-b").won is True
    row = store.load(SOUL)
    assert row is not None
    assert row["holder_request_id"] == "req-a"


def test_load_missing_is_none(tmp_path):
    assert _store(tmp_path).load(SOUL) is None


def test_quarantine_self_isolates_live_holder(tmp_path):
    store = _store(tmp_path)
    _acquire(store, now=T0)
    cap = Capability(SOUL, "tok-1", "req-1", 1)
    assert store.quarantine_self(cap, T0 + 1.0) is True
    assert store.load(SOUL)["state"] == STATE_QUARANTINED
    assert store.quarantine_self(Capability(SOUL, "wrong", "req-1", 1), T0 + 2.0) is False


def test_dual_connection_competition_exactly_one_winner(tmp_path):
    """Two connections, one soul, barrier start: exactly one wins.
    NOTE(test-only): barrier/join timeouts are rendezvous wall-clock
    tolerance only, not product semantics. Schema is pre-created once in
    the main thread: two workers running DDL (CREATE TABLE IF NOT EXISTS
    + WAL setup inside SoulLeaseStore.__init__) against a FRESH file can
    contend on the schema lock, so the loser raises SoulLeaseError before
    reaching the barrier and the partner hangs until barrier timeout
    (reproduced 2/10 with per-thread capture: PRE-BARRIER-EXC +
    BrokenBarrierError; 10/10 pass with schema pre-created, winner varies
    so the race stays genuine). Schema setup is not the property under
    test here; production files always have pre-existing schema.
    """
    path = tmp_path / "race.db"
    _open(path).close()
    # Pre-create schema once in the main thread (see NOTE above).
    _pre = _open(path)
    try:
        SoulLeaseStore(_pre)
    finally:
        _pre.close()
    barrier = threading.Barrier(2)
    outs = [None, None]

    def worker(i: int) -> None:
        conn = _open(path)
        try:
            store = SoulLeaseStore(conn)
            barrier.wait(timeout=120)
            outs[i] = store.acquire(
                SOUL, "tok-%d" % i, "req-%d" % i, "nonce-%d" % i,
                1000 + i, 999.0, 1, T0,
            )
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=180)

    winners = [o for o in outs if o.won]
    losers = [o for o in outs if not o.won]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0].holder_state == STATE_HELD


def test_caller_transaction_refused_not_joined(tmp_path):
    store = _store(tmp_path)
    conn = store._conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(SoulLeaseError):
            _acquire(store)
        with pytest.raises(SoulLeaseError):
            store.renew(Capability(SOUL, "t", "r", 1), T0)
        with pytest.raises(SoulLeaseError):
            store.release(Capability(SOUL, "t", "r", 1), T0, quiesced=True)
        with pytest.raises(SoulLeaseError):
            store.recover(SOUL, Liveness(LIVENESS_DEAD, "x"), T0, True)
    finally:
        conn.execute("ROLLBACK")
    # The refused ops left no trace.
    assert store.load(SOUL) is None


def test_broken_connection_maps_to_store_error(tmp_path):
    store = _store(tmp_path)
    store._conn.close()
    with pytest.raises(SoulLeaseError):
        _acquire(store)
    with pytest.raises(SoulLeaseError):
        store.renew(Capability(SOUL, "t", "r", 1), T0)
    with pytest.raises(SoulLeaseError):
        store.release(Capability(SOUL, "t", "r", 1), T0, quiesced=True)
    with pytest.raises(SoulLeaseError):
        store.recover(SOUL, Liveness(LIVENESS_DEAD, "x"), T0, True)
    with pytest.raises(SoulLeaseError):
        store.load(SOUL)


def test_validation_rejects_empty(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.acquire("", "tok", "req", "nonce", 1, 1.0, 1, T0)
    with pytest.raises(ValueError):
        store.acquire(SOUL, "  ", "req", "nonce", 1, 1.0, 1, T0)
    with pytest.raises(ValueError):
        store.acquire(SOUL, "tok", "req", "nonce", 1, 1.0, -1, T0)
    with pytest.raises(ValueError):
        store.renew("not-a-capability", T0)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        store.recover(SOUL, "DEAD", T0, True)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        from src.interfaces.soul_lease import Liveness as _L

        store.recover(SOUL, _L("BOGUS", "x"), T0, True)


def test_requires_caller_connection():
    with pytest.raises(TypeError):
        SoulLeaseStore("not-a-connection")  # type: ignore[arg-type]


def test_liveness_probe_never_claims_death_lightly():
    # Invalid pids are UNKNOWN, never DEAD (PID alone is not evidence).
    assert query_process_liveness(-1).state == LIVENESS_UNKNOWN
    assert query_process_liveness(0).state == LIVENESS_UNKNOWN
    # own_process_create_time never raises; probe of the current process
    # with a mismatched creation time is UNKNOWN (PID-reuse guard).
    create = own_process_create_time()
    assert isinstance(create, float)
    assert query_process_liveness(2**31 - 1, (create or T0) + 10**9).state == LIVENESS_UNKNOWN


def test_liveness_probe_read_only_no_terminate_rights(tmp_path, caplog):
    """The probe must not request terminate rights (AST-level ban)."""
    import ast
    from pathlib import Path

    tree = ast.parse(Path("src/interfaces/soul_lease.py").read_text(encoding="utf-8"))
    blob = ast.dump(tree)
    assert "TerminateProcess" not in blob
    assert "PROCESS_TERMINATE" not in blob
    with caplog.at_level(logging.WARNING, logger="soul_os.soul_lease"):
        _acquire(_store(tmp_path))
    assert all("tok-1" not in r.message for r in caplog.records)
