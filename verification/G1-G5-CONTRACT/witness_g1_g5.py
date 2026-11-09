"""G1-G5 contract-disposition witness (offline, stdlib + repo src only).

Covers the five in-place gaps with a runnable normal + rejection case each,
proving the substitute path (coordinator / backend_execution) already carries
the capability while the old Journal / submit surface stays as-is.

Run (repo root, PowerShell 5.1):
  $env:SOUL_OS_DATA_DIR = '<fresh TEMP>'; .venv\\Scripts\\python.exe -B verification\\G1-G5-CONTRACT\\witness_g1_g5.py

No server, no ports, no real API/LLM, no network. DBs live in a fresh
tempfile tempdir (never data/**).
"""

from __future__ import annotations

import inspect
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.interfaces import coordinator as coord  # noqa: E402
from src.interfaces.backend_execution import (  # noqa: E402
    BACKEND_CANCELLED,
    BACKEND_COMPLETED,
    BACKEND_RETRYABLE,
    BACKEND_UNKNOWN,
    Decision,
    PortError,
    PortResult,
)
from src.interfaces.coordinator import (  # noqa: E402
    current_capability,
    open_admission_context,
    prepare_publish,
)
from src.interfaces.journal import Journal, JournalError  # noqa: E402
from src.interfaces.runtime_port import FakeClock, FakeRuntime, submit  # noqa: E402

T0 = 1_700_000_000.0
SOUL = "agent_yua"
PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + ((" -- " + detail) if detail else ""))


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ctx(tmp: Path, name: str, epoch: int = 1):
    return open_admission_context(_open(tmp / name), epoch=epoch)


def _admit(ctx, soul: str = SOUL):
    ident, out = prepare_publish(ctx, soul_alias=soul, entry_kind="web", text="task")
    assert out.should_publish is True
    return ident


class OkPort:
    def __init__(self, rid="resp-1", body="fixed-body", done=True):
        self.rid, self.body, self.done = rid, body, done
        self.calls = 0
        self.cancelled = 0
        self.confirmed = 0

    def execute(self, decision):
        self.calls += 1
        return PortResult(response_id=self.rid, response_body=self.body, completed=self.done)

    def cancel(self):
        self.cancelled += 1

    def confirm_exited(self):
        self.confirmed += 1
        return True


class FailPort:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def execute(self, decision):
        self.calls += 1
        raise self.exc

    def cancel(self):
        pass

    def confirm_exited(self):
        return True


def _ns():
    return ("p1", "a1", "scope1", "conv1", "branch1", "key1")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="g1g5-witness-"))

    # -- G1: backend-completed unreachable in Journal; substitute _apply_port_return --
    ctx = _ctx(tmp, "g1.db")
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort(rid="resp-9", body="body-9")
    st = coord.submit_backend(ctx, cap, Decision(operation="send"), port)
    row = coord._read_request_by_id(ctx.journal, ident.request_id)
    check("G1-normal-submit-completes", st == BACKEND_COMPLETED, f"state={st}")
    check(
        "G1-normal-response-fixed-durably",
        row["state"] == "generated" and (row["response_id"], row["response_body"]) == ("resp-9", "body-9"),
        f"state={row['state']}",
    )
    # Gap pin: the Journal row has no 'completed' state; completion lives in outcomes.
    conn = sqlite3.connect(str(tmp / "g1j.db"), timeout=10.0)
    j = Journal(conn, epoch=7)
    assert j.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j.begin("req-1", 7)
    j.record_generated("req-1", 7, "resp-1", "b1")
    j.record_transport("req-1", 7)
    j.record_outcome("req-1", 7, "memory", "completed", "ev-1")
    state = conn.execute("SELECT state FROM requests WHERE request_id='req-1'").fetchone()[0]
    check("G1-gap-no-completed-row-state", state == "transport_sent", f"row state={state}")

    # -- G2: backend dest rejected by Journal; substitute submit_backend --
    conn2 = sqlite3.connect(str(tmp / "g2j.db"), timeout=10.0)
    j2 = Journal(conn2, epoch=7)
    assert j2.claim(_ns(), "fp1", "req-1", 1000.0).kind == "NEW"
    j2.begin("req-1", 7)
    j2.record_generated("req-1", 7, "resp-1", "b1")
    try:
        j2.record_outcome("req-1", 7, "backend", "completed", {"completed": True})
        rejected = False
    except JournalError as exc:
        rejected = "bad dest" in str(exc)
    check("G2-gap-backend-dest-rejected", rejected, "record_outcome(dest='backend') raises 'bad dest'")
    sig = list(inspect.signature(coord.submit_backend).parameters)
    check(
        "G2-substitute-signature-dest-free",
        sig == ["ctx", "capability", "decision", "port"],
        f"params={sig}",
    )
    d = Decision(operation="send")
    check("G2-decision-carries-no-dest", not hasattr(d, "destination"), "Decision has no destination field")
    ctx = _ctx(tmp, "g2.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    st = coord.submit_backend(ctx, cap, Decision(operation="send"), OkPort())
    check("G2-normal-submit-completes", st == BACKEND_COMPLETED, f"state={st}")

    # -- G3: Journal has no cancel; substitute cancel_backend --
    check("G3-gap-journal-has-no-cancel", not hasattr(Journal, "cancel"), "Journal.cancel absent")
    ctx = _ctx(tmp, "g3.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    st = coord.cancel_backend(ctx, cap)  # never submitted: zero side effects structurally
    check("G3-normal-cancel-before-submit", st == BACKEND_CANCELLED, f"state={st}")
    check(
        "G3-normal-cancelled-releases",
        coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED,
        "terminal CANCELLED + zero permits -> released",
    )
    ctx = _ctx(tmp, "g3b.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    fport = FailPort(PortError(False))
    assert coord.submit_backend(ctx, cap, Decision(operation="send"), fport) == BACKEND_RETRYABLE
    st = coord.cancel_backend(ctx, cap)  # sealed first, then join clean -> CANCELLED
    check("G3-normal-cancel-after-submit-joins-clean", st == BACKEND_CANCELLED, f"state={st}")
    ctx = _ctx(tmp, "g3c.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    st = coord.submit_backend(ctx, cap, Decision(operation="send"), OkPort(done=False))
    check("G3-normal-completed-false-is-unknown", st == BACKEND_UNKNOWN, f"state={st}")
    check(
        "G3-normal-unknown-never-releases",
        coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_ISOLATED,
        "UNKNOWN -> isolated (no release)",
    )

    # -- G4: old submit() maps every general exception to UNCERTAIN; substitute retry path --
    conn4 = sqlite3.connect(str(tmp / "g4.db"), timeout=10.0)
    j4 = Journal(conn4, epoch=7)
    bad = FakeRuntime().raise_at(False, ValueError("clean failure"))
    out = submit(j4, _ns(), "fp1", "req-1", bad, FakeClock(1000.0, 7))
    check("G4-gap-old-submit-general-exc-is-uncertain", out.kind == "UNCERTAIN", f"kind={out.kind}")
    retry = FakeRuntime().ok("resp-1", "b", "ev")
    out2 = submit(j4, _ns(), "fp1", "req-1", retry, FakeClock(1001.0, 7))
    check(
        "G4-gap-old-submit-has-no-retryable",
        out2.kind != "RETRYABLE" and out.kind != "RETRYABLE",
        f"first kind={out.kind}, retry kind={out2.kind} (never RETRYABLE)",
    )
    check(
        "G4-substitute-general-exc-never-retryable",
        coord._apply_port_error.__doc__ is not None
        and _apply_general_is_unknown(),
        "ValueError -> UNKNOWN via _apply_port_error",
    )
    ctx = _ctx(tmp, "g4b.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    cport = FailPort(PortError(False))
    decision = Decision(operation="send", payload={"n": 1})
    st = coord.submit_backend(ctx, cap, decision, cport)
    check("G4-normal-clean-failure-is-retryable", st == BACKEND_RETRYABLE, f"state={st}")
    try:
        coord.submit_backend(ctx, cap, decision, cport)
        resub_ok = False
    except ValueError:
        resub_ok = True
    check("G4-normal-submit-cannot-reenter", resub_ok and cport.calls == 1, f"calls={cport.calls}")
    st = coord.retry_backend(ctx, cap)
    rec = ctx._backend_ledger[cap.request_id]
    check(
        "G4-normal-retry-replays-stored-call",
        st == BACKEND_RETRYABLE and cport.calls == 2 and rec.attempt == 2,
        f"state={st} calls={cport.calls} attempt={rec.attempt}",
    )
    try:
        coord.retry_backend(ctx, cap, attempt=0)
        stale_ok = False
    except ValueError:
        stale_ok = True
    check("G4-normal-stale-attempt-fence", stale_ok and cport.calls == 2, f"calls={cport.calls}")

    # -- G5: Decision/PortResult/PortError absent from runtime_port; live in backend_execution --
    for name in ("Decision", "PortResult"):
        try:
            __import__("src.interfaces.runtime_port", fromlist=[name]).__getattribute__(name)
            missing = False
        except (ImportError, AttributeError):
            missing = True
        check(f"G5-gap-{name}-absent-from-runtime-port", missing, f"import {name} from runtime_port fails")
    ctx = _ctx(tmp, "g5.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    refused = 0
    for bad_port in (object(),):
        try:
            coord.submit_backend(ctx, cap, Decision(operation="op"), bad_port)
        except (ValueError, TypeError, AttributeError):
            refused += 1
    check("G5-normal-port-without-execute-refused", refused == 1, "object() port refused")
    check(
        "G5-normal-non-decision-refused",
        _refuses_non_decision(ctx, cap),
        "submit with a non-Decision raises",
    )
    check(
        "G5-settlement-g-numbers-are-different-scheme",
        True,
        "test_backend_settlement G1..G5 = cancel/completion/failure/retry/protocol, NOT this ticket's G1..G5; do not map by number",
    )

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


def _apply_general_is_unknown() -> bool:
    from src.interfaces.backend_execution import BackendRecord

    rec = BackendRecord(soul_alias="s", request_id="r", generation=1, epoch=1)
    return coord._apply_port_error(rec, ValueError("boom")) == BACKEND_UNKNOWN


def _refuses_non_decision(ctx, cap) -> bool:
    try:
        coord.submit_backend(ctx, cap, "not-a-decision", OkPort())
        return False
    except (ValueError, TypeError):
        return True


if __name__ == "__main__":
    raise SystemExit(main())
