"""Fake runtime port tests: ordering, replay, fail-closed uncertainty."""

import ast
import json
import sqlite3
from pathlib import Path

import pytest

from src.interfaces.journal import Journal
from src.interfaces.runtime_port import FakeCancelled
from src.interfaces.runtime_port import FakeClock
from src.interfaces.runtime_port import FakeRuntime
from src.interfaces.runtime_port import submit


def _open(path):
    return sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)


def _ns(p="p1", a="a1", s="scope1", c="conv1", b="branch1", k="key1"):
    return (p, a, s, c, b, k)


def _row(conn, rid):
    return conn.execute(
        "SELECT state, epoch, response_id, response_body, transport_count, outcomes"
        " FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()


def test_submit_success_completed_evidence(tmp_path):
    db = tmp_path / "p1.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    clock = FakeClock(1000.0, 7)
    fake = FakeRuntime().ok("resp-1", "hello", "mem-ev-1")
    out = submit(j, _ns(), "fp1", "req-1", fake, clock)
    assert out.kind == "NEW"
    assert out.response_id == "resp-1"
    assert out.response_body == "hello"
    assert fake.calls == [1]
    state, epoch, rid, body, tcount, outcomes = _row(conn, "req-1")
    assert state == "transport_sent"
    assert tcount == 1
    assert json.loads(outcomes) == {"memory": "completed"}


def test_submit_success_pending_without_evidence(tmp_path):
    db = tmp_path / "p2.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    clock = FakeClock(1000.0, 7)
    fake = FakeRuntime().ok("resp-2", "hi", None)
    out = submit(j, _ns(k="k2"), "fp1", "req-2", fake, clock)
    assert out.kind == "NEW"
    assert json.loads(_row(conn, "req-2")[5]) == {"memory": "pending"}
    fake2 = FakeRuntime().ok("resp-3", "hi2", "")
    out2 = submit(j, _ns(k="k3"), "fp1", "req-3", fake2, FakeClock(1001.0, 7))
    assert out2.kind == "NEW"
    assert json.loads(_row(conn, "req-3")[5]) == {"memory": "pending"}


def test_replay_does_not_call_runtime(tmp_path):
    db = tmp_path / "p3.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    clock = FakeClock(1000.0, 7)
    fake = FakeRuntime().ok("resp-1", "hello", "ev-1")
    first = submit(j, _ns(), "fp1", "req-1", fake, clock)
    assert first.kind == "NEW"
    fake2 = FakeRuntime().ok("resp-other", "other", "ev-x")
    second = submit(j, _ns(), "fp1", "req-1", fake2, FakeClock(1001.0, 7))
    assert second.kind == "REPLAY"
    assert second.response_id == "resp-1"
    assert second.response_body == "hello"
    assert fake2.calls == []


def test_non_new_kinds_do_not_call_runtime(tmp_path):
    db = tmp_path / "p4.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    # accepted -> IN_PROGRESS
    j.claim(ns, "fp1", "req-1", 1000.0)
    probe = FakeRuntime().ok("r", "b", "e")
    assert submit(j, ns, "fp1", "req-1", probe, FakeClock(1001.0, 7)).kind == "IN_PROGRESS"
    assert probe.calls == []
    # conflict
    probe2 = FakeRuntime().ok("r", "b", "e")
    assert submit(j, ns, "fp-other", "req-1", probe2, FakeClock(1002.0, 7)).kind == "CONFLICT"
    assert probe2.calls == []
    # uncertain sticky
    conn.execute("UPDATE requests SET state='uncertain' WHERE request_id='req-1'")
    conn.commit()
    probe3 = FakeRuntime().ok("r", "b", "e")
    assert submit(j, ns, "fp1", "req-1", probe3, FakeClock(1003.0, 7)).kind == "UNCERTAIN"
    assert probe3.calls == []


def test_expired_does_not_call_runtime(tmp_path):
    db = tmp_path / "p5.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    t0 = 5000.0
    fake = FakeRuntime().ok("resp-1", "b", "ev")
    assert submit(j, ns, "fp1", "req-1", fake, FakeClock(t0, 7)).kind == "NEW"
    probe = FakeRuntime().ok("r2", "b2", "e2")
    out = submit(j, ns, "fp1", "req-1", probe, FakeClock(t0 + 86400 + 5.0, 7))
    assert out.kind == "EXPIRED"
    assert probe.calls == []


def test_side_effect_exception_marks_uncertain_fail_closed(tmp_path):
    db = tmp_path / "p6.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    fake = FakeRuntime().raise_at(True, RuntimeError("boom-after-write"))
    out = submit(j, ns, "fp1", "req-1", fake, FakeClock(1000.0, 7))
    assert out.kind == "UNCERTAIN"
    assert fake.calls == [1]
    assert _row(conn, "req-1")[0] == "uncertain"
    conn.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=8)
    assert j2.claim(ns, "fp1", "req-1", 1001.0).kind == "UNCERTAIN"


def test_no_side_effect_exception_still_uncertain(tmp_path):
    db = tmp_path / "p7.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    fake = FakeRuntime().raise_at(False, ValueError("clean failure"))
    out = submit(j, _ns(), "fp1", "req-1", fake, FakeClock(1000.0, 7))
    assert out.kind == "UNCERTAIN"
    assert _row(conn, "req-1")[0] == "uncertain"


def test_cancel_collision_new_epoch_stays_uncertain(tmp_path):
    db = tmp_path / "p8.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    fake = FakeRuntime().cancel()
    out = submit(j, ns, "fp1", "req-1", fake, FakeClock(1000.0, 7))
    assert out.kind == "UNCERTAIN"
    assert _row(conn, "req-1")[0] == "uncertain"
    conn.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=8)
    fake2 = FakeRuntime().ok("resp-new", "b", "ev")
    out2 = submit(j2, ns, "fp1", "req-1", fake2, FakeClock(1001.0, 8))
    assert out2.kind == "UNCERTAIN"
    assert fake2.calls == []


def test_order_claim_before_begin_before_fake(tmp_path):
    db = tmp_path / "p9.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    order = []
    real_claim = j.claim
    real_begin = j.begin
    real_generated = j.record_generated

    def claim_spy(*a, **k):
        order.append("claim")
        return real_claim(*a, **k)

    def begin_spy(*a, **k):
        order.append("begin")
        return real_begin(*a, **k)

    def gen_spy(*a, **k):
        order.append("generated")
        return real_generated(*a, **k)

    j.claim = claim_spy
    j.begin = begin_spy
    j.record_generated = gen_spy

    class OrderFake(FakeRuntime):
        def __call__(self):
            order.append("fake")
            return super().__call__()

    fake = OrderFake().ok("resp-1", "b", "ev")
    out = submit(j, _ns(), "fp1", "req-1", fake, FakeClock(1000.0, 7))
    assert out.kind == "NEW"
    assert order[:3] == ["claim", "begin", "fake"]
    assert order.index("claim") < order.index("begin") < order.index("fake") < order.index("generated")


def test_replay_path_never_calls_begin_or_fake(tmp_path):
    db = tmp_path / "p10.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    fake = FakeRuntime().ok("resp-1", "b", "ev")
    assert submit(j, _ns(), "fp1", "req-1", fake, FakeClock(1000.0, 7)).kind == "NEW"

    def boom_begin(*a, **k):
        raise AssertionError("begin must not run on REPLAY")

    j.begin = boom_begin
    probe = FakeRuntime().ok("other", "o", "e")
    out = submit(j, _ns(), "fp1", "req-1", probe, FakeClock(1001.0, 7))
    assert out.kind == "REPLAY"
    assert probe.calls == []


def test_exception_does_not_blind_retry(tmp_path):
    db = tmp_path / "p11.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    bad = FakeRuntime().raise_at(True, RuntimeError("x"))
    assert submit(j, ns, "fp1", "req-1", bad, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    calls_before = len(bad.calls)
    retry = FakeRuntime().ok("resp-1", "b", "ev")
    out = submit(j, ns, "fp1", "req-1", retry, FakeClock(1001.0, 7))
    assert out.kind == "UNCERTAIN"
    assert retry.calls == []
    assert len(bad.calls) == calls_before


def test_crash_before_begin_submit_sees_in_progress_no_call(tmp_path):
    db = tmp_path / "p12.db"
    conn1 = _open(db)
    j1 = Journal(conn1, epoch=1)
    ns = _ns()
    assert j1.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    probe = FakeRuntime().ok("r", "b", "e")
    out = submit(j2, ns, "fp1", "req-1", probe, FakeClock(1001.0, 2))
    assert out.kind == "IN_PROGRESS"
    assert probe.calls == []


def test_crash_after_generated_submit_replays_no_call(tmp_path):
    db = tmp_path / "p13.db"
    conn1 = _open(db)
    j1 = Journal(conn1, epoch=1)
    ns = _ns()
    j1.claim(ns, "fp1", "req-1", 1000.0)
    j1.begin("req-1", 1)
    j1.record_generated("req-1", 1, "resp-9", "body-9")
    conn1.close()
    conn2 = _open(db)
    j2 = Journal(conn2, epoch=2)
    probe = FakeRuntime().ok("other", "o", "e")
    out = submit(j2, ns, "fp1", "req-1", probe, FakeClock(1001.0, 2))
    assert out.kind == "REPLAY"
    assert out.response_id == "resp-9"
    assert out.response_body == "body-9"
    assert probe.calls == []


def test_fake_cancelled_type(tmp_path):
    with pytest.raises(FakeCancelled):
        raise FakeCancelled("cancelled")


def test_submit_uses_stored_request_identity(tmp_path):
    # main-brain hardening: after a failed_before_execution reset, the
    # caller may pass a fresh request_id; submit must still drive the
    # stored immutable identity to completion.
    from src.interfaces.journal import OwnershipError

    db = tmp_path / "p14.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    assert j.claim(ns, "fp1", "req-orig", 1000.0).kind == "NEW"
    j.mark_failed_before_execution("req-orig", 7)
    fake = FakeRuntime().ok("resp-1", "b", "ev")
    out = submit(j, ns, "fp1", "req-fresh", fake, FakeClock(1001.0, 7))
    assert out.kind == "NEW"
    assert out.request_id == "req-orig"
    assert out.response_id == "resp-1"
    assert fake.calls == [1]
    assert _row(conn, "req-orig")[0] == "transport_sent"


def test_submit_begin_failure_marks_failed_before_execution(tmp_path):
    # main-brain hardening: begin can only fail here under a real race;
    # the row must be left failed_before_execution (re-claimable), never
    # stuck, and the ownership error must propagate (no silent swallow).
    from src.interfaces.journal import OwnershipError

    db = tmp_path / "p15.db"
    conn = _open(db)
    real = Journal(conn, epoch=7)
    ns = _ns()

    class BeginBomb:
        def __init__(self, inner):
            self._inner = inner
            self.conn = inner.conn
            self.epoch = inner.epoch

        def claim(self, *a, **k):
            return self._inner.claim(*a, **k)

        def begin(self, *a, **k):
            raise OwnershipError("injected race")

        def mark_failed_before_execution(self, *a, **k):
            return self._inner.mark_failed_before_execution(*a, **k)

        def mark_uncertain(self, *a, **k):
            return self._inner.mark_uncertain(*a, **k)

    wrapped = BeginBomb(real)
    fake = FakeRuntime().ok("resp-1", "b", "ev")
    with pytest.raises(OwnershipError):
        submit(wrapped, ns, "fp1", "req-1", fake, FakeClock(1000.0, 7))
    assert fake.calls == []
    assert _row(conn, "req-1")[0] == "failed_before_execution"
    # key stays re-claimable through the real journal afterwards
    assert real.claim(ns, "fp1", "req-1", 1001.0).kind == "NEW"


# -- EI-2B retained cases (rewritten to the 666f154 contract) -------------------
#
# The upstream skeleton above is verbatim; the cases below preserve the
# EI-2B-side coverage that has an equivalent mapping: crash-during-executing
# submit isolation via recover(), and the module-hygiene AST case.


def test_retained_submit_after_recover_never_recalls_runtime(tmp_path):
    db = tmp_path / "r1.db"
    conn1 = _open(db)
    j1 = Journal(conn1, epoch=1)
    ns = _ns()
    assert j1.claim(ns, "fp1", "req-1", 1000.0).kind == "NEW"
    j1.begin("req-1", 1)
    # Crash before the runtime answers.
    conn1.close()
    conn2 = _open(db)
    try:
        j2 = Journal(conn2, epoch=2)
        assert j2.recover() == ["req-1"]
        probe = FakeRuntime().ok("resp-x", "b", "ev")
        out = submit(j2, ns, "fp1", "req-1", probe, FakeClock(1001.0, 2))
        assert out.kind == "UNCERTAIN"
        assert probe.calls == []
        # Sticky: a fresh runtime on the new epoch still never runs.
        probe2 = FakeRuntime().ok("resp-y", "b2", "ev2")
        out2 = submit(j2, ns, "fp1", "req-1", probe2, FakeClock(1002.0, 2))
        assert out2.kind == "UNCERTAIN"
        assert probe2.calls == []
    finally:
        conn2.close()


def test_runtime_port_module_has_no_io_or_runtime_startup():
    path = Path(__file__).resolve().parent.parent.parent / "src" / "interfaces" / "runtime_port.py"
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
        "sqlite3",
    }
    assert not (imported & banned), "banned imports: %s" % sorted(imported & banned)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            if node.attr in ("environ", "getenv", "getenviron", "socket",
                             "Popen", "run", "call", "check_output", "connect"):
                raise AssertionError(
                    "runtime_port.py must not touch IO: %s" % node.attr)


# -- Declared contract gaps (must stay RED; see work-order section 9) ------------
#
# G4 proved side-effect-free retry stays UNCERTAIN (no RETRYABLE), G5 the old
# Decision/PortResult/PortError vocabulary is absent from the upstream port.
# These are collected but expected-red evidence for the BLOCKED verdict. Do
# not skip/xfail them into green; closing them needs a follow-up main-brain
# decision ticket.


def test_gap_no_side_effect_retry_still_uncertain(tmp_path):
    db = tmp_path / "g4.db"
    conn = _open(db)
    j = Journal(conn, epoch=7)
    ns = _ns()
    bad = FakeRuntime().raise_at(False, ValueError("clean failure"))
    assert submit(j, ns, "fp1", "req-1", bad, FakeClock(1000.0, 7)).kind == "UNCERTAIN"
    retry = FakeRuntime().ok("resp-1", "b", "ev")
    out = submit(j, ns, "fp1", "req-1", retry, FakeClock(1001.0, 7))
    assert retry.calls == [1]
    assert out.kind == "NEW"


def test_gap_decision_port_result_absent():
    from src.interfaces.runtime_port import Decision  # noqa: F401
    from src.interfaces.runtime_port import PortResult  # noqa: F401
