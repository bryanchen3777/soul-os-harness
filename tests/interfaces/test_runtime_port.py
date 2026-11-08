"""EI-2A runtime-port tests: fake runtime submit semantics.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from src.interfaces.journal import Journal
from src.interfaces.runtime_port import Decision
from src.interfaces.runtime_port import FakeRuntime
from src.interfaces.runtime_port import PortError
from src.interfaces.runtime_port import PortResult
from src.interfaces.runtime_port import submit


T0 = 1_700_000_000.0
NS = "owner-01/branch-a/agent-01:req-101"
FP = "fp-decision-v1"
REQ = "request-101"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _begun(tmp_path, name="p.db", epoch: int = 1):
    conn = _open(tmp_path / name)
    j = Journal(conn)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, epoch) is True
    return j, conn


def _ok_port(response_id="resp-1", body="body-1", **kw):
    return FakeRuntime([PortResult(response_id, body, **kw)])


# -- begin gate -----------------------------------------------------------------


def test_submit_requires_begin_and_never_calls_port(tmp_path):
    conn = _open(tmp_path / "p.db")
    j = Journal(conn)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    port = _ok_port()
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("op", "payload"), port=port, now=T0 + 1.0)
    assert res.outcome == "NOT_EXECUTING"
    assert res.status == 409
    assert port.call_count == 0
    assert j.load(NS)["state"] == "accepted"


def test_submit_wrong_epoch_never_calls_port(tmp_path):
    j, _ = _begun(tmp_path, epoch=1)
    port = _ok_port()
    res = submit(j, namespace=NS, request_id=REQ, epoch=2,
                 decision=Decision("op", "payload"), port=port, now=T0 + 1.0)
    assert res.outcome == "NOT_EXECUTING"
    assert port.call_count == 0


def test_submit_unknown_namespace_or_request(tmp_path):
    j, _ = _begun(tmp_path)
    port = _ok_port()
    res = submit(j, namespace="nobody:key", request_id=REQ, epoch=1,
                 decision=Decision("op"), port=port, now=T0)
    assert res.outcome == "NO_CLAIM"
    assert port.call_count == 0
    res2 = submit(j, namespace=NS, request_id="request-impostor", epoch=1,
                  decision=Decision("op"), port=port, now=T0)
    assert res2.outcome == "CONFLICT"
    assert port.call_count == 0


def test_submit_after_cancel_never_reuses_opportunity(tmp_path):
    j, _ = _begun(tmp_path)
    assert j.cancel(REQ) is True
    port = _ok_port()
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("op"), port=port, now=T0 + 1.0)
    assert res.outcome == "TERMINAL"
    assert port.call_count == 0
    assert j.begin(REQ, 1) is False


# -- happy path ------------------------------------------------------------------


def test_submit_ok_flow_completes_with_explicit_evidence(tmp_path):
    j, conn = _begun(tmp_path)
    port = _ok_port("resp-7", "body-7")
    decision = Decision("generate", "hello")
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=decision, port=port, now=T0 + 1.0)
    assert res.outcome == "OK"
    assert (res.response_id, res.response_body) == ("resp-7", "body-7")
    assert res.completed is True
    assert port.call_count == 1
    assert port.calls[0] == decision
    entry = j.load(NS)
    assert entry["state"] == "completed"
    assert (entry["response_id"], entry["response_body"]) == ("resp-7", "body-7")
    transports = conn.execute(
        "SELECT destination, ok FROM journal_transports WHERE request_id=?",
        (REQ,),
    ).fetchall()
    assert transports == [("backend", 1)]
    outcomes = conn.execute(
        "SELECT destination, state FROM journal_outcomes WHERE request_id=?",
        (REQ,),
    ).fetchall()
    assert outcomes == [("backend", "ok")]


def test_submit_incomplete_backend_result_stays_generated(tmp_path):
    j, _ = _begun(tmp_path)
    port = FakeRuntime([PortResult("resp-i", "body-i", completed=False)])
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("op"), port=port, now=T0 + 1.0)
    assert res.outcome == "OK"
    assert res.completed is False
    # Evidence stored, but the entry is NOT completed: no explicit
    # completion evidence was returned.
    assert j.load(NS)["state"] == "generated"


def test_memory_destination_cannot_complete_entry(tmp_path):
    j, _ = _begun(tmp_path)
    port = FakeRuntime([PortResult("resp-m", "body-m", completed=True,
                                   destination="memory")])
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("memorize"), port=port, now=T0 + 1.0)
    assert res.outcome == "OK"
    assert res.completed is False
    # Memory evidence must never impersonate a backend commit.
    assert j.load(NS)["state"] == "generated"


# -- failure paths -----------------------------------------------------------------


def test_submit_ambiguous_error_marks_uncertain_once(tmp_path):
    j, _ = _begun(tmp_path)
    port = FakeRuntime([PortError("boom after dispatch", ambiguous=True)])
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("op"), port=port, now=T0 + 1.0)
    assert res.outcome == "UNCERTAIN"
    assert j.load(NS)["state"] == "uncertain"
    # Uncertain entries never recall the runtime.
    again = submit(j, namespace=NS, request_id=REQ, epoch=1,
                   decision=Decision("op"), port=port, now=T0 + 2.0)
    assert again.outcome == "UNCERTAIN"
    assert port.call_count == 1


def test_submit_proved_side_effect_free_is_explicitly_retryable(tmp_path):
    j, _ = _begun(tmp_path)
    port = FakeRuntime([
        PortError("validation before dispatch", ambiguous=False),
        PortResult("resp-r", "body-r"),
    ])
    first = submit(j, namespace=NS, request_id=REQ, epoch=1,
                   decision=Decision("op"), port=port, now=T0 + 1.0)
    assert first.outcome == "RETRYABLE"
    assert j.load(NS)["state"] == "executing"
    second = submit(j, namespace=NS, request_id=REQ, epoch=1,
                    decision=Decision("op"), port=port, now=T0 + 2.0)
    assert second.outcome == "OK"
    assert (second.response_id, second.response_body) == ("resp-r", "body-r")
    assert port.call_count == 2


def test_submit_unknown_exception_is_uncertain(tmp_path):
    j, _ = _begun(tmp_path)

    class WeirdPort:
        def __init__(self):
            self.calls = []

        def execute(self, decision):
            self.calls.append(decision)
            raise RuntimeError("unclassifiable")

    port = WeirdPort()
    res = submit(j, namespace=NS, request_id=REQ, epoch=1,
                 decision=Decision("op"), port=port, now=T0 + 1.0)
    assert res.outcome == "UNCERTAIN"
    assert j.load(NS)["state"] == "uncertain"


# -- replay / recovery never recall -------------------------------------------------


def test_generated_replay_never_recalls_runtime(tmp_path):
    j, _ = _begun(tmp_path)
    port = _ok_port("resp-3", "body-3")
    first = submit(j, namespace=NS, request_id=REQ, epoch=1,
                   decision=Decision("op"), port=port, now=T0 + 1.0)
    assert first.outcome == "OK"
    # completed replays without touching the runtime or memory again.
    second = submit(j, namespace=NS, request_id=REQ, epoch=1,
                    decision=Decision("op-changed"), port=port, now=T0 + 2.0)
    assert second.outcome == "REPLAY"
    assert (second.response_id, second.response_body) == ("resp-3", "body-3")
    assert port.call_count == 1
    # claim() agrees: same fixed response.
    replay = j.claim(NS, FP, "request-late", T0 + 3.0)
    assert replay.outcome == "REPLAY"
    assert (replay.response_id, replay.response_body) == ("resp-3", "body-3")


def test_submit_after_crash_recovery_never_recalls_runtime(tmp_path):
    path = tmp_path / "p.db"
    conn = _open(path)
    j = Journal(conn)
    assert j.claim(NS, FP, REQ, T0).outcome == "NEW"
    assert j.begin(REQ, 1) is True
    # Crash before the runtime answers.
    del j
    conn.close()

    conn2 = _open(path)
    try:
        j2 = Journal(conn2)
        assert j2.recover_executing(2) == 1
        port = _ok_port()
        # Same epoch: uncertain, no call. New epoch: not executing, no call.
        assert submit(j2, namespace=NS, request_id=REQ, epoch=1,
                      decision=Decision("op"), port=port, now=T0 + 5.0).outcome == "UNCERTAIN"
        assert submit(j2, namespace=NS, request_id=REQ, epoch=2,
                      decision=Decision("op"), port=port, now=T0 + 6.0).outcome == "UNCERTAIN"
        assert port.call_count == 0
    finally:
        conn2.close()


# -- FakeRuntime unit behavior ---------------------------------------------------------


def test_fake_runtime_script_and_validation():
    with pytest.raises(ValueError):
        FakeRuntime([])
    with pytest.raises(TypeError):
        FakeRuntime(["not-a-result"])
    port = FakeRuntime([PortResult("r", "b")])
    with pytest.raises(TypeError):
        port.execute("not-a-decision")
    # Exhausted scripts repeat the last item deterministically.
    d = Decision("op")
    assert port.execute(d).response_id == "r"
    assert port.execute(d).response_id == "r"
    assert port.call_count == 2


def test_submit_validates_port_result_shape(tmp_path):
    j, _ = _begun(tmp_path)

    class BadPort:
        def execute(self, decision):
            return "a string is not evidence"

    with pytest.raises(TypeError):
        submit(j, namespace=NS, request_id=REQ, epoch=1,
               decision=Decision("op"), port=BadPort(), now=T0)
    # The entry is untouched: a shapeless answer proves nothing.
    assert j.load(NS)["state"] == "executing"


# -- module hygiene ----------------------------------------------------------------------


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
