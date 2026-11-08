"""EI-2A journal tests: durable dedupe, state machine, crash recovery.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.
"""

from __future__ import annotations

import ast
import sqlite3
import threading
from pathlib import Path

import pytest

from src.interfaces.journal import ClaimResult
from src.interfaces.journal import EXPIRY_SECONDS
from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError


T0 = 1_700_000_000.0
NS = "owner-01/branch-a/agent-01:req-001"
FP = "fp-payload-v1"
REQ = "request-001"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _journal(tmp_path, name="j.db"):
    conn = _open(tmp_path / name)
    return Journal(conn), conn


# -- claim basics ----------------------------------------------------------


def test_claim_new_then_replay_same_key(tmp_path):
    j, _ = _journal(tmp_path)
    first = j.claim(NS, FP, REQ, T0)
    assert first.outcome == "NEW"
    assert first.status == 201
    second = j.claim(NS, FP, "request-other-opportunity", T0 + 1.0)
    assert second.outcome == "IN_PROGRESS"
    assert second.status == 409
    # The original execution opportunity stays authoritative.
    assert second.request_id == REQ
    assert j.load(NS)["request_id"] == REQ
    # Once a response is fixed, the same claim replays it.
    assert j.begin(REQ, 1) is True
    assert j.record_generated(REQ, 1, "resp-1", "body-1") is True
    third = j.claim(NS, FP, "request-yet-another", T0 + 2.0)
    assert third.outcome == "REPLAY"
    assert (third.response_id, third.response_body) == ("resp-1", "body-1")


def test_parallel_dual_claim_exactly_one_new(tmp_path):
    path = tmp_path / "race.db"
    _open(path).close()
    barrier = threading.Barrier(2)
    outcomes = [None, None]

    def worker(i: int) -> None:
        conn = _open(path)
        try:
            jj = Journal(conn)
            barrier.wait(timeout=10)
            res = jj.claim(NS, FP, "request-race-%d" % i, T0)
            outcomes[i] = res.outcome
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(t.is_alive() is False for t in threads)
    assert sorted(outcomes) == ["IN_PROGRESS", "NEW"]
    # Both observers agree on the single stored opportunity.
    conn = _open(path)
    try:
        stored = Journal(conn).load(NS)["request_id"]
    finally:
        conn.close()
    assert stored in ("request-race-0", "request-race-1")


def test_same_key_different_fingerprint_always_conflict(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    clash = j.claim(NS, "fp-other-payload", "request-002", T0 + 1.0)
    assert clash.outcome == "CONFLICT"
    assert clash.status == 409
    assert clash.code == "idempotency_conflict"
    assert clash.response_body is None
    # The stored fingerprint is untouched.
    assert j.load(NS)["fingerprint"] == FP


def test_conflict_survives_terminal_and_expiry(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_generated(REQ, 1, "resp-1", "body-1") is True
    assert j.record_outcome(REQ, "backend", "ok", {"completed": True}, T0 + 2.0) is True
    assert j.load(NS)["state"] == "completed"
    late = T0 + EXPIRY_SECONDS + 3600.0
    # Same fingerprint on a terminal row past the window: EXPIRED.
    assert j.claim(NS, FP, REQ, late).outcome == "EXPIRED"
    # Different fingerprint on the same key: still CONFLICT, never EXPIRED.
    clash = j.claim(NS, "fp-sneaky", "request-999", late)
    assert clash.outcome == "CONFLICT"
    assert j.load(NS)["state"] == "completed"


# -- begin / generated guards ----------------------------------------------


def test_begin_dual_executor_exactly_one_wins(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.begin(REQ, 2) is False
    entry = j.load(NS)
    assert entry["state"] == "executing"
    assert entry["epoch"] == 1


def test_begin_only_from_accepted(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.begin("request-ghost", 1) is False
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.cancel(REQ) is True
    # A cancelled claim can never begin (again): the opportunity is spent.
    assert j.begin(REQ, 1) is False
    assert j.load(NS)["state"] == "cancelled"


def test_record_generated_requires_holding_epoch(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    # Not begun: no executor holds the claim.
    assert j.record_generated(REQ, 1, "resp-x", "body-x") is False
    assert j.begin(REQ, 1) is True
    # Wrong epoch: a different executor must not fix the response.
    assert j.record_generated(REQ, 2, "resp-x", "body-x") is False
    assert j.record_generated(REQ, 1, "resp-x", "body-x") is True
    # Generated twice: second fix is refused, first response stands.
    assert j.record_generated(REQ, 1, "resp-y", "body-y") is False
    entry = j.load(NS)
    assert (entry["response_id"], entry["response_body"]) == ("resp-x", "body-x")


# -- crash windows ----------------------------------------------------------


def _drive_to_state(j: Journal, state: str, now: float) -> None:
    assert j.claim(NS, FP, REQ, now).outcome == "NEW"
    if state == "accepted":
        return
    assert j.begin(REQ, 1) is True
    if state == "executing":
        return
    if state == "uncertain":
        # Ambiguous backend outcome while executing: no response is fixed.
        assert j.mark_uncertain(REQ, 1) is True
        return
    assert j.record_generated(REQ, 1, "resp-1", "body-1") is True
    if state == "generated":
        return
    assert state == "completed"
    assert j.record_outcome(REQ, "backend", "ok", {"completed": True}, now + 1.0) is True


@pytest.mark.parametrize(
    ("setup", "after_claim", "begin_ok"),
    [
        ("accepted", "IN_PROGRESS", True),
        ("executing", "UNCERTAIN", False),
        ("generated", "REPLAY", False),
        ("uncertain", "UNCERTAIN", False),
        ("completed", "REPLAY", False),
    ],
)
def test_crash_window_recovery(tmp_path, setup, after_claim, begin_ok):
    path = tmp_path / "crash.db"
    conn = _open(path)
    j = Journal(conn)
    _drive_to_state(j, setup, T0)
    # Crash: drop the objects without any teardown protocol.
    del j
    conn.close()

    # Restart on the same durable file with a newer epoch.
    conn2 = _open(path)
    try:
        j2 = Journal(conn2)
        marked = j2.recover_executing(2)
        entry = j2.load(NS)
        if setup == "executing":
            assert marked == 1
            assert entry["state"] == "uncertain"
        else:
            assert marked == 0
            assert entry["state"] == setup
        assert j2.claim(NS, FP, "request-after-crash", T0 + 5.0).outcome == after_claim
        assert j2.begin(REQ, 2) is begin_ok
        if setup == "generated":
            assert entry["response_id"] == "resp-1"
            assert entry["response_body"] == "body-1"
    finally:
        conn2.close()


def test_executing_restart_never_replays_without_begin(tmp_path):
    # After a crash, the uncertain entry must not accept the OLD epoch
    # and must not let a fresh begin slip in: recovery is explicit.
    j, conn = _journal(tmp_path)
    _drive_to_state(j, "executing", T0)
    conn.close()
    conn2 = _open(tmp_path / "j.db")
    try:
        j2 = Journal(conn2)
        assert j2.recover_executing(2) == 1
        assert j2.record_generated(REQ, 1, "resp-late", "body-late") is False
        assert j2.begin(REQ, 2) is False
        assert j2.load(NS)["state"] == "uncertain"
    finally:
        conn2.close()


# -- generated replay stability ----------------------------------------------


def test_generated_replay_response_stable(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_generated(REQ, 1, "resp-9", "body-9") is True
    for i in range(3):
        r = j.claim(NS, FP, "request-replay-%d" % i, T0 + 10.0 + i)
        assert r.outcome == "REPLAY"
        assert (r.response_id, r.response_body) == ("resp-9", "body-9")
    assert j.load(NS)["state"] == "generated"


# -- namespace isolation ------------------------------------------------------


def test_two_principal_branch_agent_isolation(tmp_path):
    namespaces = [
        "principal-a/branch-a/agent-a:key-1",
        "principal-b/branch-a/agent-a:key-1",
        "principal-a/branch-b/agent-a:key-1",
        "principal-a/branch-a/agent-b:key-1",
    ]
    j, _ = _journal(tmp_path)
    for i, ns in enumerate(namespaces):
        assert j.claim(ns, "fp-%d" % i, "request-%d" % i, T0).outcome == "NEW"
        assert j.begin("request-%d" % i, 1) is True
        assert j.record_generated(
            "request-%d" % i, 1, "resp-%d" % i, "body-%d" % i) is True
    for i, ns in enumerate(namespaces):
        r = j.claim(ns, "fp-%d" % i, "request-other-%d" % i, T0 + 1.0)
        assert r.outcome == "REPLAY"
        assert (r.response_id, r.response_body) == ("resp-%d" % i, "body-%d" % i)
        entry = j.load(ns)
        assert entry["request_id"] == "request-%d" % i
    # A cross-namespace begin is impossible: request ids are journal-unique.
    assert j.begin("request-0", 1) is False
    assert j.load(namespaces[1])["state"] == "generated"


# -- expiry: terminal vs unresolved --------------------------------------------


def test_expiry_terminal_reports_expired_without_redo(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_generated(REQ, 1, "resp-1", "body-1") is True
    assert j.record_outcome(REQ, "backend", "ok", {"completed": True}, T0 + 1.0) is True
    late = T0 + EXPIRY_SECONDS + 1.0
    expired = j.claim(NS, FP, REQ, late)
    assert expired.outcome == "EXPIRED"
    assert expired.status == 410
    assert expired.code == "idempotency_expired"
    # No automatic redo: the old opportunity stays spent.
    assert j.begin(REQ, 1) is False
    assert j.load(NS)["state"] == "completed"
    # A fresh event must mint a fresh key.
    assert j.claim(NS + ":next", FP, "request-002", late).outcome == "NEW"


@pytest.mark.parametrize("setup", ["accepted", "executing", "generated", "uncertain"])
def test_expiry_never_clears_unresolved(tmp_path, setup):
    j, _ = _journal(tmp_path)
    _drive_to_state(j, setup, T0)
    late = T0 + EXPIRY_SECONDS + 3600.0
    entry_before = j.load(NS)
    result = j.claim(NS, FP, "request-late", late)
    assert result.outcome != "EXPIRED"
    entry_after = j.load(NS)
    assert entry_after["state"] == setup
    assert entry_after["request_id"] == entry_before["request_id"]
    assert entry_after["response_id"] == entry_before["response_id"]


# -- transport vs memory vs outcome --------------------------------------------


def test_transport_evidence_never_changes_commit_state(tmp_path):
    j, conn = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_transport(REQ, "backend", True, {"k": "v"}, T0 + 1.0) is True
    assert j.load(NS)["state"] == "executing"
    rows = conn.execute(
        "SELECT destination, ok FROM journal_transports WHERE request_id=?",
        (REQ,),
    ).fetchall()
    assert rows == [("backend", 1)]


def test_memory_outcome_cannot_impersonate_backend_commit(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_generated(REQ, 1, "resp-1", "body-1") is True
    # Memory "ok" with a completed flag: stored as evidence, no promotion.
    assert j.record_outcome(
        REQ, "memory", "ok", {"completed": True}, T0 + 1.0) is True
    assert j.load(NS)["state"] == "generated"
    # Bus "ok" likewise never completes.
    assert j.record_outcome(
        REQ, "bus", "ok", {"completed": True}, T0 + 2.0) is True
    assert j.load(NS)["state"] == "generated"
    # Backend "ok" WITHOUT explicit completion evidence: no promotion.
    assert j.record_outcome(REQ, "backend", "ok", {"note": "sent"}, T0 + 3.0) is True
    assert j.load(NS)["state"] == "generated"
    # Backend "unknown": never promotes, even with a completed flag.
    assert j.record_outcome(
        REQ, "backend", "unknown", {"completed": True}, T0 + 4.0) is True
    assert j.load(NS)["state"] == "generated"
    # Only explicit same-request backend completion evidence completes.
    assert j.record_outcome(
        REQ, "backend", "ok", {"completed": True}, T0 + 5.0) is True
    assert j.load(NS)["state"] == "completed"


def test_definitive_backend_failure_marks_failed(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    assert j.record_outcome(
        REQ, "backend", "failed", {"definitive": True}, T0 + 1.0) is True
    assert j.load(NS)["state"] == "failed"
    # Non-definitive failure is evidence only.
    j2, _ = _journal(tmp_path, "j2.db")
    assert j2.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j2.begin(REQ, 1) is True
    assert j2.record_outcome(
        REQ, "backend", "failed", {"note": "timeout?"}, T0 + 1.0) is True
    assert j2.load(NS)["state"] == "executing"


def test_outcome_rejects_unknown_enums(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    with pytest.raises(ValueError):
        j.record_outcome(REQ, "pigeon", "ok", {}, T0)
    with pytest.raises(ValueError):
        j.record_outcome(REQ, "backend", "maybe", {}, T0)
    with pytest.raises(ValueError):
        j.record_transport(REQ, "pigeon", True, {}, T0)


def test_unknown_request_returns_false_without_leak(tmp_path):
    j, _ = _journal(tmp_path)
    assert j.record_transport("ghost", "backend", True, {}, T0) is False
    assert j.record_outcome("ghost", "backend", "ok", {"completed": True}, T0) is False
    assert j.mark_uncertain("ghost", 1) is False
    assert j.cancel("ghost") is False
    assert j.load_by_request("ghost") is None


def test_errors_never_expose_storage_internals(tmp_path):
    j, _ = _journal(tmp_path)
    with pytest.raises(ValueError) as exc:
        j.claim("", FP, REQ, T0)
    assert "sqlite" not in str(exc.value).lower()
    with pytest.raises(JournalError) as exc2:
        broken = Journal.__new__(Journal)
        broken._conn = sqlite3.connect(":memory:")
        broken._conn.close()
        broken.load(NS)
    assert str(exc2.value) == "journal storage unavailable."
    with pytest.raises(TypeError):
        Journal("not-a-connection")


# -- module hygiene: no IO / env at import --------------------------------------


def test_journal_module_has_no_io_env_or_connect():
    path = Path(__file__).resolve().parent.parent.parent / "src" / "interfaces" / "journal.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                continue
            if node.module:
                imported.add(node.module.split(".")[0])
    banned = {
        "socket", "subprocess", "os", "sys", "http", "urllib", "pathlib",
        "io", "threading", "multiprocessing", "ssl", "asyncio", "signal",
    }
    assert not (imported & banned), "banned imports: %s" % sorted(imported & banned)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "connect":
                value = func.value
                if isinstance(value, ast.Name) and value.id == "sqlite3":
                    raise AssertionError("journal.py must not call sqlite3.connect")
        if isinstance(node, ast.Attribute):
            if node.attr in ("environ", "getenv", "getenviron"):
                raise AssertionError("journal.py must not read the environment")
