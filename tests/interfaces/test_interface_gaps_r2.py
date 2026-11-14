"""INTERFACE-GAPS-1 R2 closure tests: reject / concurrency / restart / cancel / types.

Covers the five closed gaps beyond the gap tests themselves:
- Q1 backend-dest gates (writable only on generated/transport_sent; completed
  rows only replay an already-stored backend completed; memory preserved).
- Q2 flip discipline (completed flips only in transport_sent; completed rows
  replay / expire / count transports; per-dest no-downgrade).
- Q3 clean-retry permit (single consume, refusal priority, epoch fence,
  restart loss, concurrency, no-begin-on-retry, no restore on storage
  failure).
- Q4 terminal-sticky cancel.
- Q5 runtime_port re-exports the backend_execution type objects.
"""

import json
import sqlite3
import threading

import pytest

from src.interfaces import backend_execution
from src.interfaces import runtime_port
from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError
from src.interfaces.journal import OwnershipError
from src.interfaces.runtime_port import FakeCancelled
from src.interfaces.runtime_port import FakeClock
from src.interfaces.runtime_port import FakeRuntime
from src.interfaces.runtime_port import submit


def _open(path):
    return sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)


def _ns(k="key1"):
    return ("p1", "a1", "scope1", "conv1", "branch1", k)


def _mk(path, epoch=7):
    conn = _open(path)
    return conn, Journal(conn, epoch=epoch)


def _row(conn, rid):
    return conn.execute(
        "SELECT state, epoch, response_id, response_body, transport_count, outcomes"
        " FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()


def _to_generated(j, rid, epoch=7, key="key1"):
    j.claim(_ns(key), "fp1", rid, 1000.0)
    j.begin(rid, epoch)
    j.record_generated(rid, epoch, "resp-1", "b1")


# -- Q1: backend-dest gates ----------------------------------------------------


def test_backend_outcome_shape_and_no_flip_on_generated(tmp_path):
    conn, j = _mk(tmp_path / "q1a.db")
    _to_generated(j, "req-1")
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    state, _, _, _, tcount, outcomes = _row(conn, "req-1")
    assert json.loads(outcomes) == {"memory": "pending", "backend": "completed"}
    assert state == "generated"
    assert tcount == 0


def test_backend_outcome_refused_off_writable_states(tmp_path):
    # accepted row
    _, j = _mk(tmp_path / "q1b.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    # executing row
    j.begin("req-1", 7)
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "backend", "pending")
    # uncertain row
    j.mark_uncertain("req-1")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    # failed_before_execution row
    conn2, j2 = _mk(tmp_path / "q1c.db")
    j2.claim(_ns(), "fp1", "req-2", 1000.0)
    j2.mark_failed_before_execution("req-2", 7)
    with pytest.raises(JournalError):
        j2.record_outcome("req-2", 7, "backend", "completed", {"completed": True})
    # cancelled row
    conn3, j3 = _mk(tmp_path / "q1d.db")
    j3.claim(_ns(), "fp1", "req-3", 1000.0)
    j3.cancel("req-3")
    with pytest.raises(JournalError):
        j3.record_outcome("req-3", 7, "backend", "completed", {"completed": True})
    for conn, rid in ((conn2, "req-2"), (conn3, "req-3")):
        assert json.loads(conn.execute(
            "SELECT outcomes FROM requests WHERE request_id=?", (rid,)).fetchone()[0]) == {"memory": "pending"}
    conn2.close()
    conn3.close()


def test_completed_row_backend_only_same_value_replay(tmp_path):
    conn, j = _mk(tmp_path / "q1e.db")
    _to_generated(j, "req-1")
    j.record_transport("req-1", 7)
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    assert _row(conn, "req-1")[0] == "completed"
    # Same-value resend is an idempotent success.
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "pending", "backend": "completed"}
    # Non-completed writes on the backend key of a completed row are refused.
    for status in ("pending", "skipped", "unknown"):
        with pytest.raises(JournalError):
            j.record_outcome("req-1", 7, "backend", status)
    # Status-level same value stays an idempotent success (O5 contract:
    # evidence bytes are not compared); durable bytes are untouched.
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True, "n": 2})
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "pending", "backend": "completed"}
    # Memory key still follows the independent per-dest rule: pending ->
    # completed is a legal upgrade even on a completed row (recorded, never
    # reflipped), and its own downgrade stays refused.
    j.record_outcome("req-1", 7, "memory", "completed", {"completed": True})
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "completed", "backend": "completed"}
    assert _row(conn, "req-1")[0] == "completed"
    with pytest.raises(JournalError) as excinfo:
        j.record_outcome("req-1", 7, "memory", "pending")
    assert str(excinfo.value) == "completed cannot downgrade"
    # Backend key is untouched by the memory lifecycle.
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "completed", "backend": "completed"}


def test_destinations_downgrade_independently(tmp_path):
    conn, j = _mk(tmp_path / "q1f.db")
    _to_generated(j, "req-1")
    j.record_outcome("req-1", 7, "memory", "completed", "ev-m")
    # One destination completed never blocks the other key's own lifecycle.
    j.record_outcome("req-1", 7, "backend", "skipped")
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "completed", "backend": "skipped"}
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
    assert json.loads(_row(conn, "req-1")[5]) == {"memory": "completed", "backend": "completed"}
    # Each key then enforces its own no-downgrade rule independently.
    with pytest.raises(JournalError) as exc_m:
        j.record_outcome("req-1", 7, "memory", "pending")
    assert str(exc_m.value) == "completed cannot downgrade"
    with pytest.raises(JournalError) as exc_b:
        j.record_outcome("req-1", 7, "backend", "unknown")
    assert str(exc_b.value) == "completed cannot downgrade"


# -- Q2: flip discipline --------------------------------------------------------


def test_completed_flips_only_in_transport_sent(tmp_path):
    # generated: record only.
    conn, j = _mk(tmp_path / "q2a.db")
    _to_generated(j, "req-1")
    j.record_outcome("req-1", 7, "memory", "completed", "ev-1")
    assert _row(conn, "req-1")[0] == "generated"
    # accepted: record only.
    conn2, j2 = _mk(tmp_path / "q2b.db")
    j2.claim(_ns(), "fp1", "req-2", 1000.0)
    j2.record_outcome("req-2", 7, "memory", "completed", "ev-2")
    row = conn2.execute(
        "SELECT state, outcomes FROM requests WHERE request_id=?", ("req-2",)).fetchone()
    assert row[0] == "accepted" and json.loads(row[1]) == {"memory": "completed"}
    # executing: record only.
    conn3, j3 = _mk(tmp_path / "q2c.db")
    j3.claim(_ns(), "fp1", "req-3", 1000.0)
    j3.begin("req-3", 7)
    j3.record_outcome("req-3", 7, "memory", "completed", "ev-3")
    row3 = conn3.execute(
        "SELECT state, outcomes FROM requests WHERE request_id=?", ("req-3",)).fetchone()
    assert row3[0] == "executing" and json.loads(row3[1]) == {"memory": "completed"}
    conn2.close()
    conn3.close()


def test_completed_row_replay_expiry_transport_and_generated_resend(tmp_path):
    db = tmp_path / "q2d.db"
    conn, j = _mk(db)
    ns = _ns()
    t0 = 5000.0
    j.claim(ns, "fp1", "req-1", t0)
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_transport("req-1", 7)
    j.record_outcome("req-1", 7, "memory", "completed", "ev-1")
    assert _row(conn, "req-1")[0] == "completed"
    # Same-value generated resend succeeds on a completed row; mismatch fails.
    j.record_generated("req-1", 7, "resp-1", "b1")
    with pytest.raises(JournalError):
        j.record_generated("req-1", 7, "resp-2", "b1")
    # Transport keeps counting without leaving completed.
    j.record_transport("req-1", 7)
    j.record_transport("req-1", 7)
    assert _row(conn, "req-1")[0] == "completed"
    assert _row(conn, "req-1")[4] == 3
    # Replay within TTL, EXPIRED past it — completed is in the replay set.
    r = j.claim(ns, "fp1", "req-1", t0 + 100.0)
    assert r.kind == "REPLAY" and (r.response_id, r.response_body) == ("resp-1", "b1")
    assert j.claim(ns, "fp1", "req-1", t0 + 86400 + 5.0).kind == "EXPIRED"
    conn.close()


# -- Q3: clean-retry permit ------------------------------------------------------


def test_retry_path_never_calls_begin(tmp_path):
    conn, j = _mk(tmp_path / "q3a.db")
    ns = _ns()
    bad = FakeRuntime().raise_at(False, ValueError("clean"))
    assert submit(j, ns, "fp1", "req-1", bad, FakeClock(1000.0, 7)).kind == "UNCERTAIN"

    def _boom_begin(*a, **k):
        raise AssertionError("begin must not run on the permitted retry path")

    j.begin = _boom_begin
    retry = FakeRuntime().ok("resp-1", "b", "ev")
    out = submit(j, ns, "fp1", "req-1", retry, FakeClock(1001.0, 7))
    assert out.kind == "NEW"
    assert retry.calls == [1]


def test_permit_single_use_and_no_restore_on_lost_cas(tmp_path):
    _, j = _mk(tmp_path / "q3b.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    attempt = j._next_submit_attempt("req-1")
    j.mark_uncertain("req-1")
    j._grant_clean_retry("req-1", 7, attempt)
    assert j.try_consume_clean_retry("req-1") is True
    # Single use: already popped.
    assert j.try_consume_clean_retry("req-1") is False
    # Lost CAS consumes without restore: sabotage the row, grant again.
    j2conn, j2 = _mk(tmp_path / "q3c.db")
    j2.claim(_ns(), "fp1", "req-2", 1000.0)
    j2.begin("req-2", 7)
    a2 = j2._next_submit_attempt("req-2")
    j2.mark_uncertain("req-2")
    j2._grant_clean_retry("req-2", 7, a2)
    j2conn.execute("UPDATE requests SET state='generated' WHERE request_id='req-2'")
    j2conn.commit()
    assert j2.try_consume_clean_retry("req-2") is False
    assert j2.try_consume_clean_retry("req-2") is False
    j2conn.close()
    with pytest.raises(JournalError):
        j.try_consume_clean_retry("")


def test_refusal_priority_seals_retry(tmp_path):
    # FakeCancelled seals.
    _, j = _mk(tmp_path / "q3d.db")
    ns = _ns()
    assert submit(j, ns, "fp1", "req-1", FakeRuntime().cancel(),
                  FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    probe = FakeRuntime().ok("r", "b", "e")
    assert submit(j, ns, "fp1", "req-1", probe, FakeClock(1001.0, 7)).kind == "UNCERTAIN"
    assert probe.calls == []
    # Ambiguous PortError seals.
    from src.interfaces.backend_execution import PortError
    _, j2 = _mk(tmp_path / "q3e.db")
    assert submit(j2, ns, "fp1", "req-2", FakeRuntime().raise_at(False, PortError(True)),
                  FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    probe2 = FakeRuntime().ok("r", "b", "e")
    assert submit(j2, ns, "fp1", "req-2", probe2, FakeClock(1001.0, 7)).kind == "UNCERTAIN"
    assert probe2.calls == []
    # Explicit side-effect evidence seals even a formally clean PortError.
    _, j3 = _mk(tmp_path / "q3f.db")
    assert submit(j3, ns, "fp1", "req-3", FakeRuntime().raise_at(True, PortError(False)),
                  FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    probe3 = FakeRuntime().ok("r", "b", "e")
    assert submit(j3, ns, "fp1", "req-3", probe3, FakeClock(1001.0, 7)).kind == "UNCERTAIN"
    assert probe3.calls == []
    # Missing side_effect attribute is never treated as False.
    _, j4 = _mk(tmp_path / "q3g.db")

    def _plain():
        raise ValueError("uncontrolled failure")

    assert submit(j4, ns, "fp1", "req-4", _plain, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    probe4 = FakeRuntime().ok("r", "b", "e")
    assert submit(j4, ns, "fp1", "req-4", probe4, FakeClock(1001.0, 7)).kind == "UNCERTAIN"
    assert probe4.calls == []
    # Formal PortError(False) alone grants, even off a non-FakeRuntime fake.
    _, j5 = _mk(tmp_path / "q3h.db")

    def _formal():
        raise PortError(False)

    assert submit(j5, ns, "fp1", "req-5", _formal, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    probe5 = FakeRuntime().ok("r", "b", "e")
    out5 = submit(j5, ns, "fp1", "req-5", probe5, FakeClock(1001.0, 7))
    assert out5.kind == "NEW"
    assert probe5.calls == [1]


def test_concurrent_retry_exactly_one_runtime_call(tmp_path):
    db = tmp_path / "q3i.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    bad = FakeRuntime().raise_at(False, ValueError("clean"))
    assert submit(j, ns, "fp1", "req-1", bad, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    barrier = threading.Barrier(2)
    results = [None, None]
    fakes = [FakeRuntime().ok("resp-%d" % i, "b", "ev") for i in (0, 1)]

    def worker(i):
        try:
            barrier.wait(timeout=30)
            results[i] = submit(j, ns, "fp1", "req-1", fakes[i], FakeClock(1001.0, 7))
        except Exception as exc:  # noqa: BLE001
            results[i] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert all(not t.is_alive() for t in threads)
    assert all(not isinstance(r, Exception) for r in results)
    # Exactly one thread wins the single-use permit and settles NEW; the
    # loser never touches the runtime. Depending on interleaving the loser
    # may observe UNCERTAIN (no permit left) or the in-flight executing row
    # (IN_PROGRESS) — both are fail-closed, zero-call outcomes.
    assert sum(len(f.calls) for f in fakes) == 1
    assert sum(1 for r in results if r.kind == "NEW") == 1
    for r, f in zip(results, fakes):
        if r.kind != "NEW":
            assert r.kind in ("UNCERTAIN", "IN_PROGRESS")
            assert f.calls == []
    conn.close()


def test_restart_drops_permit_and_epoch_fence_blocks_consume(tmp_path):
    db = tmp_path / "q3j.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    bad = FakeRuntime().raise_at(False, ValueError("clean"))
    assert submit(j, ns, "fp1", "req-1", bad, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    conn.close()
    # Restart: a fresh Journal instance on the same DB has no memory permit.
    conn2 = _open(db)
    try:
        j2 = Journal(conn2, epoch=8)
        probe = FakeRuntime().ok("resp-x", "b", "ev")
        assert submit(j2, ns, "fp1", "req-1", probe, FakeClock(1001.0, 8)).kind == "UNCERTAIN"
        assert probe.calls == []
    finally:
        conn2.close()
    # Same instance, clock moved on: clock.epoch mismatch never enters consume.
    conn3 = _open(db)
    try:
        j3 = Journal(conn3, epoch=7)
        probe3 = FakeRuntime().ok("resp-y", "b2", "ev2")
        # Row is uncertain from the first epoch-7 failure, but this clock is
        # epoch 8 while the journal is epoch 7: no consume, no call.
        assert submit(j3, ns, "fp1", "req-1", probe3, FakeClock(1002.0, 8)).kind == "UNCERTAIN"
        assert probe3.calls == []
    finally:
        conn3.close()


# -- Q4: terminal-sticky cancel ---------------------------------------------------


def test_cancel_happy_path_idempotent_and_sticky(tmp_path):
    db = tmp_path / "q4a.db"
    conn, j = _mk(db)
    ns = _ns()
    assert j.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    j.cancel("req-1")
    row = conn.execute(
        "SELECT state, epoch FROM requests WHERE request_id=?", ("req-1",)).fetchone()
    assert row == ("cancelled", 7)
    # Idempotent success.
    j.cancel("req-1")
    # Same key/fingerprint stays CANCELLED across epochs and past expiry.
    assert j.claim(ns, "fp1", "req-1", 1001.0).kind == "CANCELLED"
    conn.close()
    conn2 = _open(db)
    try:
        j2 = Journal(conn2, epoch=9)
        assert j2.claim(ns, "fp1", "req-1", 1001.0 + 86400 + 50.0).kind == "CANCELLED"
        assert j2.claim(ns, "fp1", "req-1", 1002.0).kind == "CANCELLED"
        # Fingerprint conflict still takes priority over the terminal state.
        assert j2.claim(ns, "fp-other", "req-1", 1003.0).kind == "CONFLICT"
    finally:
        conn2.close()


def test_cancel_refused_states_and_ownership(tmp_path):
    _, j = _mk(tmp_path / "q4b.db")
    with pytest.raises(JournalError):
        j.cancel("req-missing")
    with pytest.raises(JournalError):
        j.cancel("")
    # executing / uncertain / generated / transport_sent / completed / failed
    j.claim(_ns("k-e"), "fp1", "req-e", 1000.0)
    j.begin("req-e", 7)
    with pytest.raises(JournalError):
        j.cancel("req-e")
    j.mark_uncertain("req-e")
    with pytest.raises(JournalError):
        j.cancel("req-e")
    _to_generated(j, "req-g", key="k-g")
    with pytest.raises(JournalError):
        j.cancel("req-g")
    j.record_transport("req-g", 7)
    with pytest.raises(JournalError):
        j.cancel("req-g")
    j.record_outcome("req-g", 7, "memory", "completed", "ev")
    assert j.claim(_ns("k-g"), "fp1", "req-g", 1001.0).kind == "REPLAY"
    with pytest.raises(JournalError):
        j.cancel("req-g")
    # accepted row owned by another epoch: OwnershipError, row untouched.
    conn2, j2 = _mk(tmp_path / "q4c.db", epoch=1)
    j2.claim(_ns(), "fp1", "req-x", 1000.0)
    conn2.close()
    conn3 = _open(tmp_path / "q4c.db")
    try:
        j3 = Journal(conn3, epoch=2)
        with pytest.raises(OwnershipError):
            j3.cancel("req-x")
        assert conn3.execute(
            "SELECT state FROM requests WHERE request_id=?", ("req-x",)).fetchone()[0] == "accepted"
    finally:
        conn3.close()


# -- Q5: type identity -------------------------------------------------------------


def test_runtime_port_reexports_backend_execution_types():
    assert runtime_port.Decision is backend_execution.Decision
    assert runtime_port.PortResult is backend_execution.PortResult
    assert not hasattr(runtime_port, "PortError")
    # The private alias still distinguishes formal PortError values.
    assert isinstance(backend_execution.PortError(False), runtime_port._PortError)
