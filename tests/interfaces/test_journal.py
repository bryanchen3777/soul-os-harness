"""Journal offline tests: durable admission, idempotency, concurrency, recovery."""

import ast
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from src.interfaces.journal import ClaimResult
from src.interfaces.journal import EXPIRY_SECONDS
from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError
from src.interfaces.journal import OwnershipError


def _open(path):
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    return conn


def _ns(p="p1", a="a1", s="scope1", c="conv1", b="branch1", k="key1"):
    return (p, a, s, c, b, k)


def _mk(path, epoch=7):
    conn = _open(path)
    return conn, Journal(conn, epoch=epoch)


def _row(conn, rid):
    return conn.execute(
        "SELECT principal_id, agent_id, scope, conversation_id, branch_id, idempotency_key,"
        " fingerprint, request_id, state, epoch, response_id, response_body,"
        " transport_count, outcomes, created_ts, expires_ts, updated_ts"
        " FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()


def test_expiry_constant():
    assert EXPIRY_SECONDS == 86400


def test_concurrent_same_key_exactly_one_new(tmp_path):
    db = tmp_path / "c1.db"
    ns = _ns()
    n = 8
    barrier = threading.Barrier(n)
    kinds = [None] * n
    errors = []

    def worker(i):
        try:
            conn = _open(db)
            j = Journal(conn, epoch=7)
            barrier.wait(timeout=30)
            r = j.claim(ns, "fp1", "req-1", 1000.0)
            kinds[i] = r.kind
            conn.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert kinds.count("NEW") == 1
    assert kinds.count("IN_PROGRESS") == n - 1
    conn = _open(db)
    count = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
    assert count == 1
    conn.close()


def test_same_key_different_fingerprint_conflict_and_priority_over_expiry(tmp_path):
    conn, j = _mk(tmp_path / "c2.db")
    ns = _ns()
    r1 = j.claim(ns, "fp-A", "req-1", 1000.0)
    assert r1.kind == "NEW"
    r2 = j.claim(ns, "fp-B", "req-1", 1001.0)
    assert r2.kind == "CONFLICT"
    # finish first request so expiry branch is reachable, conflict still wins
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "body-1")
    j.record_transport("req-1", 7)
    r3 = j.claim(ns, "fp-B", "req-1", 1000.0 + 86400 + 50.0)
    assert r3.kind == "CONFLICT"


def test_double_begin_second_ownership_error(tmp_path):
    conn, j = _mk(tmp_path / "c3.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    with pytest.raises(OwnershipError):
        j.begin("req-1", 7)
    with pytest.raises(JournalError):
        j.begin("req-1", 7)
    with pytest.raises(OwnershipError):
        j.begin("req-1", 999)
    with pytest.raises(OwnershipError):
        j.begin("req-missing", 7)
    row = _row(conn, "req-1")
    assert row[8] == "executing"


def test_crash_before_begin_restart_claim_in_progress(tmp_path):
    db = tmp_path / "c4.db"
    conn1, j1 = _mk(db, epoch=1)
    ns = _ns()
    assert j1.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    r = j2.claim(ns, "fp1", "req-1", 1001.0)
    assert r.kind == "IN_PROGRESS"
    assert _row(conn2, "req-1")[8] == "accepted"


def test_crash_during_executing_recover_uncertain_sticky(tmp_path):
    db = tmp_path / "c5.db"
    conn1, j1 = _mk(db, epoch=1)
    ns = _ns()
    j1.claim(ns, "fp1", "req-1", 1000.0)
    j1.begin("req-1", 1)
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    ids = j2.recover()
    assert ids == ["req-1"]
    assert _row(conn2, "req-1")[8] == "uncertain"
    assert j2.claim(ns, "fp1", "req-1", 1002.0).kind == "UNCERTAIN"
    conn2.close()
    conn3 = _open(db)
    j3 = Journal(conn3, epoch=3)
    assert j3.claim(ns, "fp1", "req-1", 1003.0).kind == "UNCERTAIN"
    conn3.close()


def test_crash_after_generated_replays_same_response(tmp_path):
    db = tmp_path / "c6.db"
    conn1, j1 = _mk(db, epoch=1)
    ns = _ns()
    j1.claim(ns, "fp1", "req-1", 1000.0)
    j1.begin("req-1", 1)
    j1.record_generated("req-1", 1, "resp-9", "body-9")
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    r = j2.claim(ns, "fp1", "req-1", 1001.0)
    assert r.kind == "REPLAY"
    assert r.response_id == "resp-9"
    assert r.response_body == "body-9"


def test_crash_before_transport_replays_and_keeps_generated(tmp_path):
    db = tmp_path / "c7.db"
    conn1, j1 = _mk(db, epoch=1)
    ns = _ns()
    j1.claim(ns, "fp1", "req-1", 1000.0)
    j1.begin("req-1", 1)
    j1.record_generated("req-1", 1, "resp-1", "b1")
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    r = j2.claim(ns, "fp1", "req-1", 1001.0)
    assert r.kind == "REPLAY"
    row = _row(conn2, "req-1")
    assert row[8] == "generated"
    assert row[12] == 0


def test_duplicate_transport_idempotent_and_counts(tmp_path):
    conn, j = _mk(tmp_path / "c8.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_transport("req-1", 7)
    j.record_transport("req-1", 7)
    j.record_transport("req-1", 7)
    row = _row(conn, "req-1")
    assert row[8] == "transport_sent"
    assert row[12] == 3
    outcomes = json.loads(row[13])
    assert outcomes == {"memory": "pending"}
    with pytest.raises(JournalError):
        j.record_transport("req-1", 999)


def test_old_epoch_executing_claim_marks_uncertain(tmp_path):
    db = tmp_path / "c9.db"
    conn1, j1 = _mk(db, epoch=5)
    ns = _ns()
    j1.claim(ns, "fp1", "req-1", 1000.0)
    j1.begin("req-1", 5)
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=6)
    assert j2.claim(ns, "fp1", "req-1", 1001.0).kind == "UNCERTAIN"
    assert _row(conn2, "req-1")[8] == "uncertain"
    conn2.close()


def test_record_outcome_rejects_and_rules(tmp_path):
    conn, j = _mk(tmp_path / "c10.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "transport", "completed", "ev")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "bogus")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "completed", None)
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "completed", "")
    j.record_outcome("req-1", 7, "memory", "unknown")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "completed", None)
    j.record_outcome("req-1", 7, "memory", "completed", "ev-1")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "pending")
    with pytest.raises(JournalError):
        j.record_outcome("req-1", 7, "memory", "unknown")
    row = _row(conn, "req-1")
    assert json.loads(row[13]) == {"memory": "completed"}


def test_namespace_isolation(tmp_path):
    conn, j = _mk(tmp_path / "c11.db")
    ns_a = _ns(p="alice", b="branch-a", k="key1")
    ns_b = _ns(p="bob", b="branch-b", k="key1")
    ns_c = _ns(p="alice", a="agent-2", b="branch-a", k="key1")
    assert j.claim(ns_a, "fp1", "req-A", 1000.0).kind == "NEW"
    j.begin("req-A", 7)
    j.record_generated("req-A", 7, "resp-A", "body-A")
    j.record_transport("req-A", 7)
    rb = j.claim(ns_b, "fp1", "req-B", 1001.0)
    assert rb.kind == "NEW"
    assert rb.response_id is None
    rc = j.claim(ns_c, "fp1", "req-C", 1002.0)
    assert rc.kind == "NEW"
    ra = j.claim(ns_a, "fp1", "req-A", 1003.0)
    assert ra.kind == "REPLAY"
    assert ra.response_id == "resp-A"


def test_24h_boundary_and_unresolved_never_expire(tmp_path):
    conn, j = _mk(tmp_path / "c12.db")
    ns = _ns()
    t0 = 2000.0
    assert j.claim(ns, "fp1", "req-1", t0).kind == "NEW"
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_transport("req-1", 7)
    assert j.claim(ns, "fp1", "req-1", t0 + 86400).kind == "REPLAY"
    assert j.claim(ns, "fp1", "req-1", t0 + 86400 + 0.001).kind == "EXPIRED"
    assert _row(conn, "req-1") is not None
    # unresolved never expires: accepted
    conn2, j2 = _mk(tmp_path / "c12b.db")
    ns2 = _ns(k="k2")
    assert j2.claim(ns2, "fp1", "req-2", t0).kind == "NEW"
    assert j2.claim(ns2, "fp1", "req-2", t0 + 86400 + 100.0).kind == "IN_PROGRESS"
    # unresolved never expires: uncertain
    conn3, j3 = _mk(tmp_path / "c12c.db", epoch=1)
    ns3 = _ns(k="k3")
    j3.claim(ns3, "fp1", "req-3", t0)
    j3.begin("req-3", 1)
    conn3.close()
    conn4 = _open(tmp_path / "c12c.db")
    j4 = Journal(conn4, epoch=2)
    assert j4.claim(ns3, "fp1", "req-3", t0 + 1).kind == "UNCERTAIN"
    assert j4.claim(ns3, "fp1", "req-3", t0 + 86400 + 500.0).kind == "UNCERTAIN"


def test_failed_before_execution_resets_to_new(tmp_path):
    db = tmp_path / "c13.db"
    conn = _open(db)
    j = Journal(conn, epoch=1)
    ns = _ns()
    t0 = 3000.0
    conn.execute(
        "INSERT INTO requests (principal_id, agent_id, scope, conversation_id, branch_id,"
        " idempotency_key, fingerprint, request_id, state, epoch, response_id, response_body,"
        " transport_count, outcomes, created_ts, expires_ts, updated_ts)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ns[0], ns[1], ns[2], ns[3], ns[4], ns[5], "fp1", "req-1", "failed_before_execution",
         1, None, None, 0, '{"memory": "pending"}', t0, t0 + 86400, t0),
    )
    conn.commit()
    conn.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    r = j2.claim(ns, "fp1", "req-1", t0 + 10.0)
    assert r.kind == "NEW"
    row = _row(conn2, "req-1")
    assert row[8] == "accepted"
    assert row[9] == 2
    conn2.close()


def test_commit_failure_raises_and_writes_nothing(tmp_path):
    db = tmp_path / "c14.db"

    class ExplodingConn:
        def __init__(self, real):
            self._real = real

        def execute(self, *a, **k):
            return self._real.execute(*a, **k)

        def commit(self):
            raise RuntimeError("boom-commit")

        def rollback(self):
            return self._real.rollback()

    real = _open(db)
    wrapped = ExplodingConn(real)
    j = Journal.__new__(Journal)
    j.conn = wrapped
    j.epoch = 7
    # create schema via real connection first
    real.execute(
        "CREATE TABLE IF NOT EXISTS requests (principal_id TEXT NOT NULL, agent_id TEXT NOT NULL,"
        " scope TEXT NOT NULL, conversation_id TEXT NOT NULL, branch_id TEXT NOT NULL,"
        " idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE,"
        " state TEXT NOT NULL, epoch INTEGER NOT NULL, response_id TEXT, response_body TEXT,"
        " transport_count INTEGER NOT NULL DEFAULT 0, outcomes TEXT NOT NULL,"
        " created_ts REAL NOT NULL, expires_ts REAL NOT NULL, updated_ts REAL NOT NULL,"
        " UNIQUE (principal_id, agent_id, scope, conversation_id, branch_id, idempotency_key))"
    )
    real.commit()
    with pytest.raises(JournalError):
        j.claim(_ns(), "fp1", "req-1", 1000.0)
    try:
        real.rollback()
    except Exception:
        pass
    assert real.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 0
    real.close()


def test_record_generated_idempotent_same_value_and_mismatch(tmp_path):
    conn, j = _mk(tmp_path / "c15.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_generated("req-1", 7, "resp-1", "b1")
    with pytest.raises(JournalError):
        j.record_generated("req-1", 7, "resp-2", "b1")
    with pytest.raises(JournalError):
        j.record_generated("req-1", 7, "resp-1", "b1-diff")
    with pytest.raises(OwnershipError):
        j.record_generated("req-1", 999, "resp-1", "b1")
    with pytest.raises(JournalError):
        j.record_generated("req-missing", 7, "r", "b")
    row = _row(conn, "req-1")
    assert row[8] == "generated"


def test_transport_wrong_state_and_claim_validation(tmp_path):
    conn, j = _mk(tmp_path / "c16.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    with pytest.raises(JournalError):
        j.record_transport("req-1", 7)
    with pytest.raises(JournalError):
        j.claim(("only", "two"), "fp1", "req-1", 1000.0)
    with pytest.raises(JournalError):
        j.claim(_ns(), "fp1", "req-1", "not-a-time")
    assert isinstance(ClaimResult("NEW", "x"), ClaimResult)


def test_mark_uncertain_api(tmp_path):
    # main-brain hardening: public fail-closed path, no raw-SQL writes.
    conn, j = _mk(tmp_path / "c17.db")
    j.claim(_ns(), "fp1", "req-1", 1000.0)
    j.begin("req-1", 7)
    j.mark_uncertain("req-1")
    assert _row(conn, "req-1")[8] == "uncertain"
    # already-uncertain is a no-op success
    j.mark_uncertain("req-1")
    assert _row(conn, "req-1")[8] == "uncertain"
    assert j.claim(_ns(), "fp1", "req-1", 1001.0).kind == "UNCERTAIN"
    with pytest.raises(JournalError):
        j.mark_uncertain("req-missing")
    with pytest.raises(JournalError):
        j.mark_uncertain("")
    # wrong state: accepted must not move directly to uncertain
    conn2, j2 = _mk(tmp_path / "c17b.db")
    j2.claim(_ns(), "fp1", "req-2", 1000.0)
    with pytest.raises(JournalError):
        j2.mark_uncertain("req-2")
    assert _row(conn2, "req-2")[8] == "accepted"


def test_mark_failed_before_execution_api(tmp_path):
    # main-brain hardening: real producer for the failed_before_execution state.
    conn, j = _mk(tmp_path / "c18.db")
    ns = _ns()
    assert j.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    j.mark_failed_before_execution("req-1", 7)
    assert _row(conn, "req-1")[8] == "failed_before_execution"
    with pytest.raises(OwnershipError):
        j.mark_failed_before_execution("req-1", 999)
    with pytest.raises(JournalError):
        j.mark_failed_before_execution("req-missing", 7)
    # re-claim resets to accepted with the new epoch and stays re-claimable
    r = j.claim(ns, "fp1", "req-1", 1001.0)
    assert r.kind == "NEW"
    assert r.request_id == "req-1"
    row = _row(conn, "req-1")
    assert row[8] == "accepted"
    assert row[9] == 7
    # executing rows must not move to failed_before_execution
    j.begin("req-1", 7)
    with pytest.raises(JournalError):
        j.mark_failed_before_execution("req-1", 7)


# -- EI-2B retained cases (rewritten to the 666f154 contract) -------------------
#
# The upstream skeleton above is verbatim; the cases below preserve the
# EI-2B-side coverage that has an equivalent mapping under the new API:
# competition, six-tuple namespace isolation, fixed replay stability, crash
# recovery without begin, and the module-hygiene AST case.


def test_retained_parallel_dual_claim_exactly_one_new(tmp_path):
    path = tmp_path / "r-race.db"
    _open(path).close()
    ns = _ns()
    barrier = threading.Barrier(2)
    kinds = [None, None]
    rids = [None, None]
    errors = []

    def worker(i):
        try:
            conn = _open(path)
            try:
                jj = Journal(conn, epoch=7)
                barrier.wait(timeout=30)
                res = jj.claim(ns, "fp1", "request-race-%d" % i, 1000.0)
                kinds[i] = res.kind
                rids[i] = res.request_id
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    assert all(t.is_alive() is False for t in threads)
    assert sorted(kinds) == ["IN_PROGRESS", "NEW"]
    # Both observers agree on the single stored opportunity.
    assert rids[0] == rids[1]
    assert rids[0] in ("request-race-0", "request-race-1")
    conn = _open(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1
    finally:
        conn.close()


def test_retained_six_tuple_namespace_isolation(tmp_path):
    conn, j = _mk(tmp_path / "r-ns.db")
    namespaces = [
        ("ei2b-local", "agent-a", "soul-lease", "", "", "k1"),
        ("ei2b-local", "agent-b", "soul-lease", "", "", "k1"),
        ("ei2b-local", "agent-a", "soul-lease", "", "branch-b", "k1"),
        ("ei2b-local", "agent-a", "soul-lease", "conv-c", "", "k1"),
    ]
    for i, ns in enumerate(namespaces):
        rid = "req-ns-%d" % i
        assert j.claim(ns, "fp-%d" % i, rid, 1000.0).kind == "NEW"
        j.begin(rid, 7)
        j.record_generated(rid, 7, "resp-%d" % i, "body-%d" % i)
        j.record_transport(rid, 7)
    for i, ns in enumerate(namespaces):
        r = j.claim(ns, "fp-%d" % i, "req-other-%d" % i, 1001.0)
        assert r.kind == "REPLAY"
        assert r.request_id == "req-ns-%d" % i
        assert (r.response_id, r.response_body) == ("resp-%d" % i, "body-%d" % i)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 4


def test_retained_generated_replay_response_stable(tmp_path):
    conn, j = _mk(tmp_path / "r-replay.db")
    assert j.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-9", "body-9")
    for i in range(3):
        r = j.claim(_ns(), "fp1", "req-replay-%d" % i, 1010.0 + i)
        assert r.kind == "REPLAY"
        assert r.request_id == "req-1"
        assert (r.response_id, r.response_body) == ("resp-9", "body-9")
    assert _row(conn, "req-1")[8] == "generated"


def test_retained_executing_restart_never_replays_without_begin(tmp_path):
    db = tmp_path / "r-crash.db"
    conn, j = _mk(db, epoch=1)
    ns = _ns()
    assert j.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    j.begin("req-1", 1)
    conn.close()
    conn2 = _open(db)
    try:
        j2 = Journal(conn2, epoch=2)
        assert j2.recover() == ["req-1"]
        # The old epoch can no longer fix a response on an uncertain row.
        with pytest.raises(JournalError):
            j2.record_generated("req-1", 1, "resp-late", "body-late")
        # Neither epoch can begin an uncertain row: recovery is explicit.
        with pytest.raises(OwnershipError):
            j2.begin("req-1", 1)
        with pytest.raises(OwnershipError):
            j2.begin("req-1", 2)
        assert _row(conn2, "req-1")[8] == "uncertain"
    finally:
        conn2.close()


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


# -- Declared contract gaps (must stay RED; see work-order section 9) ------------
#
# G1 backend-completed unreachable, G2 backend dest rejected, G3 cancel absent.
# These are collected but expected-red evidence for the BLOCKED verdict: the
# upstream journal has no completed/failed/cancelled row state and
# record_outcome only accepts the memory dest. Do not skip/xfail them into
# green; closing them needs a follow-up main-brain decision ticket.


def test_gap_backend_completed_unreachable(tmp_path):
    conn, j = _mk(tmp_path / "g1.db")
    assert j.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_transport("req-1", 7)
    j.record_outcome("req-1", 7, "memory", "completed", "ev-1")
    assert _row(conn, "req-1")[8] == "completed"


def test_gap_backend_dest_completion_rejected(tmp_path):
    _, j = _mk(tmp_path / "g2.db")
    assert j.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_outcome("req-1", 7, "backend", "completed", {"completed": True})


def test_gap_cancel_absent(tmp_path):
    _, j = _mk(tmp_path / "g3.db")
    assert j.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j.cancel("req-1")
