"""EI-2B-JOURNAL-ADAPT-1 seam tests: coordinator against the authoritative
upstream (666f154) journal contract.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.

Covers work-order §5: six-tuple isolation, None-success begin,
OwnershipError, single-parameter uncertain, generated replay, recover
IDs, live holder never released, storage-read failure zero-send.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.interfaces import coordinator as coord
from src.interfaces.coordinator import (
    Journal,
    admit_entry,
    open_admission_context,
    prepare_publish,
)
from src.interfaces.journal import JournalError, OwnershipError

T0 = 1_700_000_000.0
SOUL = "agent_yua"
KEY = "turn-001"
FP = "fp-body-v1"
REQ = "request-001"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _setup(tmp_path, name="seam.db", epoch=1):
    conn = _open(tmp_path / name)
    return Journal(conn, epoch=epoch), conn


def test_six_tuple_namespace_isolation(tmp_path):
    """Same key under different souls, and different keys under one soul,
    map to independent journal rows; same six-tuple + new body conflicts."""
    j, _ = _setup(tmp_path)
    ns_a = coord.journal_namespace(SOUL, KEY)
    ns_b = coord.journal_namespace("agent_miku", KEY)
    ns_c = coord.journal_namespace(SOUL, "turn-002")
    assert j.claim(ns_a, FP, "req-a", T0).kind == "NEW"
    assert j.claim(ns_b, FP, "req-b", T0).kind == "NEW"
    assert j.claim(ns_c, FP, "req-c", T0).kind == "NEW"
    assert coord._read_request_by_id(j, "req-a")["request_id"] == "req-a"
    assert coord._read_request_by_id(j, "req-b")["request_id"] == "req-b"
    # Same six-tuple, different body: CONFLICT, original row untouched.
    assert j.claim(ns_a, "fp-other", "req-a2", T0).kind == "CONFLICT"
    assert coord._read_row_by_namespace(j, ns_a)["request_id"] == "req-a"


def test_begin_success_returns_none_never_truth_tested(tmp_path):
    """Upstream begin returns None on success — callers must never
    truth-test it (no `if won:`)."""
    j, _ = _setup(tmp_path)
    assert j.claim(coord.journal_namespace(SOUL, KEY), FP, REQ, T0).kind == "NEW"
    assert j.begin(REQ, 1) is None
    assert coord._read_request_by_id(j, REQ)["state"] == "executing"


def test_begin_ownership_error_on_replay_or_wrong_epoch(tmp_path):
    """Second begin, or begin at a non-owning epoch, raises OwnershipError
    (a JournalError subclass) — the coordinator maps it to busy."""
    j, _ = _setup(tmp_path)
    assert j.claim(coord.journal_namespace(SOUL, KEY), FP, REQ, T0).kind == "NEW"
    j.begin(REQ, 1)
    with pytest.raises(OwnershipError):
        j.begin(REQ, 1)
    with pytest.raises(JournalError):
        j.begin(REQ, 2)


def test_mark_uncertain_single_parameter(tmp_path):
    """Single-parameter mark: executing moves, generated/unknown raise —
    never assumed a no-op."""
    j, _ = _setup(tmp_path)
    assert j.claim(coord.journal_namespace(SOUL, KEY), FP, REQ, T0).kind == "NEW"
    j.begin(REQ, 1)
    j.mark_uncertain(REQ)
    assert coord._read_request_by_id(j, REQ)["state"] == "uncertain"
    # Already-uncertain is a no-op success.
    j.mark_uncertain(REQ)
    with pytest.raises(JournalError):
        j.mark_uncertain("req-ghost")
    # Generated rows are never forced uncertain.
    assert j.claim(
        coord.journal_namespace(SOUL, "turn-002"), FP, "req-g", T0).kind == "NEW"
    j.begin("req-g", 1)
    j.record_generated("req-g", 1, "r", "b")
    with pytest.raises(JournalError):
        j.mark_uncertain("req-g")


def test_generated_same_key_replays_fixed_response(tmp_path):
    """Claim + begin + record_generated, then a same-key same-body claim
    answers REPLAY with the fixed response (no republish)."""
    j, _ = _setup(tmp_path)
    ns = coord.journal_namespace(SOUL, KEY)
    assert j.claim(ns, FP, REQ, T0).kind == "NEW"
    j.begin(REQ, 1)
    j.record_generated(REQ, 1, "resp-1", "fixed-body")
    replayed = j.claim(ns, FP, "req-late", T0 + 1.0)
    assert replayed.kind == "REPLAY"
    assert (replayed.response_id, replayed.response_body) == ("resp-1", "fixed-body")


def test_recover_returns_ids_and_skips_generated(tmp_path):
    """recover() returns the moved request-id list; generated rows are
    never swept."""
    j, conn = _setup(tmp_path)
    for key, rid in (("k-1", "req-1"), ("k-2", "req-2"), ("k-3", "req-3")):
        assert j.claim(coord.journal_namespace(SOUL, key), FP, rid, T0).kind == "NEW"
        j.begin(rid, 1)
    j.record_generated("req-3", 1, "r", "b")
    j2 = Journal(conn, epoch=2)
    assert sorted(j2.recover()) == ["req-1", "req-2"]
    assert coord._read_request_by_id(j2, "req-1")["state"] == "uncertain"
    assert coord._read_request_by_id(j2, "req-3")["state"] == "generated"


def test_live_holder_never_released_on_restart(tmp_path):
    """Startup verification: a live holder stays HELD across epochs and
    newcomers stay busy (lease verified holder-by-holder, never by
    journal scan)."""
    from src.interfaces.soul_lease import Liveness, LIVENESS_ALIVE

    conn = _open(tmp_path / "live.db")
    ctx = open_admission_context(conn, epoch=1)
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="w1")
    assert out.should_publish is True
    fresh = open_admission_context(
        conn, epoch=2,
        liveness_fn=lambda pid, create: Liveness(LIVENESS_ALIVE, "still here"),
    )
    row = fresh.lease_store.load(SOUL)
    assert row is not None and row["state"] == "HELD"
    assert row["holder_request_id"] == ident.request_id
    nxt = admit_entry(
        fresh.journal, fresh.registry, soul_alias=SOUL, entry_kind="tg",
        fingerprint="fp-2", request_id="request-epoch-2",
        idempotency_key="turn-002", epoch=2, now=T0 + 1.0,
        lease_store=fresh.lease_store, held_map=fresh.held,
    )
    assert nxt.disposition == "IN_PROGRESS"
    assert nxt.should_publish is False


def test_storage_read_failure_is_zero_send(tmp_path):
    """Dead storage: coordinator-owned reads raise JournalError and every
    dispatch-side gate answers closed (zero send), never raising."""
    j, conn = _setup(tmp_path)
    assert j.claim(coord.journal_namespace(SOUL, KEY), FP, REQ, T0).kind == "NEW"
    j.begin(REQ, 1)
    conn.close()
    with pytest.raises(JournalError):
        coord._read_request_by_id(j, REQ)
    with pytest.raises(JournalError):
        coord._read_row_by_namespace(j, coord.journal_namespace(SOUL, KEY))
    assert coord.require_registered(j, REQ) is False
