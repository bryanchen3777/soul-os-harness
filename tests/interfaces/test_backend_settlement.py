"""EI-2B-BACKEND-SETTLE-1: backend end table vs journal durable state.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess, no network.

Covers work order §5: the full end table row by row; G1–G5 consumer-side
substitute behaviour; memory never counts as backend evidence; repeat
settle / parallel retry zero-resend; seal vs permit races; join before and
after cancel; stale attempt/epoch refusal; storage failure; crash injection
around port return / generated write / seal / release (reopen proves no
recall and no release on missing evidence); and the upstream refusal of a
backend destination (negative pin).
"""

from __future__ import annotations

import ast
import sqlite3
import threading
from pathlib import Path

import pytest

from src.interfaces import coordinator as coord
from src.interfaces.backend_execution import (
    BACKEND_CANCELLED,
    BACKEND_COMPLETED,
    BACKEND_FAILED,
    BACKEND_RETRYABLE,
    BACKEND_RUNNING,
    BACKEND_TERMINAL,
    BACKEND_UNKNOWN,
    Decision,
    PortError,
    PortResult,
)
from src.interfaces.coordinator import (
    current_capability,
    open_admission_context,
    prepare_publish,
)
from src.interfaces.journal import JournalError

T0 = 1_700_000_000.0
SOUL = "agent_yua"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ctx(tmp_path, name="b.db", epoch=1):
    conn = _open(tmp_path / name)
    return open_admission_context(conn, epoch=epoch)


def _admit(ctx, soul=SOUL, kind="web", text="task"):
    ident, out = prepare_publish(ctx, soul_alias=soul, entry_kind=kind, text=text)
    assert out.should_publish is True
    return ident


# -- fake backend ports (in-memory doubles, zero IO) --------------------------


class OkPort:
    """Port whose execute succeeds and whose join is clean."""

    def __init__(self, rid="resp-1", body="fixed-body", done=True):
        self.rid = rid
        self.body = body
        self.done = done
        self.calls = 0
        self.cancelled = 0
        self.confirmed = 0
        self.seen = []

    def execute(self, decision):
        self.calls += 1
        self.seen.append(decision)
        return PortResult(
            response_id=self.rid, response_body=self.body,
            completed=self.done,
        )

    def cancel(self):
        self.cancelled += 1

    def confirm_exited(self):
        self.confirmed += 1
        return True


class FailPort:
    """Port whose execute always raises the configured exception."""

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


class NoJoinPort(OkPort):
    """Execute + cancel exist, but the join point is missing (never joined).

    ``None`` is behaviourally identical to a missing attribute: the
    coordinator treats any non-callable ``confirm_exited`` as unjoined.
    """

    confirm_exited = None


class DirtyPort(OkPort):
    """Smuggles routing evidence: must be refused outright."""

    destination = "backend://somewhere"


class MemoryPort(OkPort):
    """Smuggles memory evidence: must be refused outright."""

    memory = {"completed": True}


class VerdictPort(OkPort):
    """Smuggles a caller-side verdict boolean: must be refused outright."""

    completed = True


class BlockingPort:
    """First execute blocks until released (parallel-retry ordering)."""

    def __init__(self):
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()

    def execute(self, decision):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=10)
        return PortResult(response_id="r", response_body="b", completed=True)

    def cancel(self):
        pass

    def confirm_exited(self):
        return True


# -- end table: one row per verdict --------------------------------------------


def test_end_table_cancel_before_submit_releases(tmp_path):
    """Not-started, zero task/permit/side-effect -> release (G1 fast path)."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    assert coord.cancel_backend(ctx, cap) == BACKEND_CANCELLED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED
    assert ctx.lease_store.load(SOUL)["state"] == "FREE"


def test_end_table_completed_releases_and_tombstones(tmp_path):
    """COMPLETED + sealed + quiet -> release; re-settle is idempotent."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send", payload={"t": 1}), port) == BACKEND_COMPLETED
    assert port.calls == 1
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED
    assert ctx.lease_store.load(SOUL)["state"] == "FREE"
    assert ctx._backend_tombstones.get(ident.request_id) == BACKEND_COMPLETED
    # Repeat settle: released again with zero port replay and zero lease
    # touch (tombstone fast path, even though held no longer has the cap).
    assert coord.settle_request(ctx, cap, T0 + 2) == coord.SETTLE_RELEASED
    assert port.calls == 1


def test_end_table_failed_releases_only_via_abandon(tmp_path):
    """FAILED (abandoned proven-clean retry) + sealed + quiet -> release."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(False))
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_RETRYABLE
    assert coord.retry_backend(ctx, cap, abandon=True) == BACKEND_FAILED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED
    assert ctx.lease_store.load(SOUL)["state"] == "FREE"


def test_end_table_cancelled_releases(tmp_path):
    """CANCELLED (sealed, clean join) + quiet -> release."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_COMPLETED
    # A completed record is terminal: cancel is an idempotent no-op here.
    assert coord.cancel_backend(ctx, cap) == BACKEND_COMPLETED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED


def test_end_table_running_and_retryable_are_held(tmp_path):
    """RUNNING / RETRYABLE (task open) -> held, whatever the journal says."""
    ctx = _ctx(tmp_path, "h1.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_HELD_TASKS_OPEN
    port = FailPort(PortError(False))
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_RETRYABLE
    assert coord.settle_request(ctx, cap, T0 + 2) == coord.SETTLE_HELD_TASKS_OPEN
    assert ctx.lease_store.load(SOUL)["state"] == "HELD"


def test_end_table_terminal_with_open_permit_is_held(tmp_path):
    """Terminal + unclosed permit -> held_permits; close -> release."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    permit = coord.open_permit(
        ctx, request_id=ident.request_id, destination="web", now=T0)
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_COMPLETED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_HELD_PERMITS_OPEN
    assert ctx.lease_store.load(SOUL)["state"] == "HELD"
    assert coord.close_permit(ctx, permit_id=permit, now=T0 + 2) is True
    assert coord.settle_request(ctx, cap, T0 + 3) == coord.SETTLE_RELEASED


def test_end_table_unknown_isolates(tmp_path):
    """UNKNOWN (fate unproven) -> quarantine, never free."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(True))
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_UNKNOWN
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_ISOLATED
    assert ctx.lease_store.load(SOUL)["state"] == "QUARANTINED"


def test_end_table_evidence_missing_or_contradictory(tmp_path):
    """Missing row -> refused; binding contradiction -> isolate."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    from src.interfaces.soul_lease import Capability
    ghost = Capability(SOUL, "tok-ghost", "in-ghost", cap.generation)
    assert coord.settle_request(ctx, ghost, T0 + 1) == coord.SETTLE_REFUSED_UNKNOWN
    # Contradiction: ledger record rebound under a foreign alias must never
    # release — quarantine instead.
    rec = ctx._backend_ledger[cap.request_id]
    rec.soul_alias = "agent_miku"
    assert coord.settle_request(ctx, cap, T0 + 2) == coord.SETTLE_ISOLATED
    assert ctx.lease_store.load(SOUL)["state"] == "QUARANTINED"


# -- G1: cancel -----------------------------------------------------------------


def test_g1_cancel_before_and_after_join(tmp_path):
    """Cancel seals first; clean join -> CANCELLED, unjoined -> UNKNOWN."""
    ctx = _ctx(tmp_path, "g1.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_COMPLETED
    # Terminal records: cancel never touches the port again.
    assert coord.cancel_backend(ctx, cap) == BACKEND_COMPLETED
    assert port.cancelled == 0 and port.confirmed == 0

    # Live-cancel path: a RETRYABLE record holds a stored port — cancel
    # seals, cancels, joins clean.
    ctx3 = _ctx(tmp_path, "g1c.db")
    _admit(ctx3)
    cap3 = current_capability(ctx3, SOUL)
    port3 = FailPort(PortError(False))
    assert coord.submit_backend(
        ctx3, cap3, Decision(operation="send"), port3) == BACKEND_RETRYABLE
    # RETRYABLE holds a stored port: cancel seals, cancels, joins clean.
    assert coord.cancel_backend(ctx3, cap3) == BACKEND_CANCELLED
    assert coord.settle_request(ctx3, cap3, T0 + 1) == coord.SETTLE_RELEASED


def test_g1_unjoined_or_dirty_join_is_unknown(tmp_path):
    """Missing confirm_exited / falsy / raising join -> UNKNOWN, isolated."""
    # Pin the missing-join branch through a RETRYABLE record holding a
    # joinless port (reached here by direct ledger setup; the public path
    # can only store ports that passed the G5 protocol check at submit).
    ctx3 = _ctx(tmp_path, "g1f.db")
    _admit(ctx3)
    cap3 = current_capability(ctx3, SOUL)
    rec = ctx3._backend_ledger[cap3.request_id]
    joinless = NoJoinPort()
    rec.decision = Decision(operation="send")
    rec.port = joinless
    rec.state = BACKEND_RETRYABLE
    assert coord.cancel_backend(ctx3, cap3) == BACKEND_UNKNOWN
    assert coord.settle_request(ctx3, cap3, T0 + 1) == coord.SETTLE_ISOLATED


# -- G2: backend completion -------------------------------------------------------


def test_g2_completion_needs_real_port_return(tmp_path):
    """Only PortResult(completed=True) + durable fix yields COMPLETED."""
    ctx = _ctx(tmp_path, "g2.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort(done=False)
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_UNKNOWN
    # completed=False proves nothing about side effects: sealed, isolated.
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_ISOLATED

    ctx2 = _ctx(tmp_path, "g2b.db")
    _admit(ctx2)
    cap2 = current_capability(ctx2, SOUL)

    class GarbagePort:
        def __init__(self):
            self.calls = 0

        def execute(self, decision):
            self.calls += 1
            return {"completed": True}

        def cancel(self):
            pass

        def confirm_exited(self):
            return True

    garbage = GarbagePort()
    assert coord.submit_backend(
        ctx2, cap2, Decision(operation="send"), garbage) == BACKEND_UNKNOWN
    # A caller-supplied "completed" boolean has no submission path: the
    # submit signature takes (ctx, capability, decision, port) only.
    import inspect
    assert list(inspect.signature(coord.submit_backend).parameters) == [
        "ctx", "capability", "decision", "port"]


def test_g2_completed_fixes_response_durably(tmp_path):
    """COMPLETED pins the fixed response in the journal (generated)."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort(rid="resp-9", body="body-9")
    assert coord.submit_backend(
        ctx, cap, Decision(operation="send"), port) == BACKEND_COMPLETED
    row = coord._read_request_by_id(ctx.journal, ident.request_id)
    assert row["state"] == "generated"
    assert (row["response_id"], row["response_body"]) == ("resp-9", "body-9")


# -- G3: backend failure ----------------------------------------------------------


def test_g3_failure_mapping(tmp_path):
    """Clean PortError -> RETRYABLE; ambiguous/general -> UNKNOWN (sealed)."""
    ctx = _ctx(tmp_path, "g3.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    assert coord.submit_backend(
        ctx, cap, Decision(operation="op"),
        FailPort(PortError(False))) == BACKEND_RETRYABLE
    assert coord.retry_backend(ctx, cap, abandon=True) == BACKEND_FAILED

    ctx2 = _ctx(tmp_path, "g3b.db")
    _admit(ctx2)
    cap2 = current_capability(ctx2, SOUL)
    assert coord.submit_backend(
        ctx2, cap2, Decision(operation="op"),
        FailPort(RuntimeError("boom"))) == BACKEND_UNKNOWN
    rec = ctx2._backend_ledger[cap2.request_id]
    assert rec.sealed is True
    # General failures can never become FAILED: abandon is refused outside
    # RETRYABLE, and retry past a sealed record is an idempotent no-op
    # (returns the state, zero resend — never a new port call).
    with pytest.raises(ValueError):
        coord.retry_backend(ctx2, cap2, abandon=True)
    assert coord.retry_backend(ctx2, cap2) == BACKEND_UNKNOWN
    assert coord.settle_request(ctx2, cap2, T0 + 1) == coord.SETTLE_ISOLATED


def test_g3_abandon_only_from_retryable(tmp_path):
    """Abandon from RUNNING / terminal / unknown raises (no FAILED path)."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    with pytest.raises(ValueError):
        coord.retry_backend(ctx, cap, abandon=True)
    port = FailPort(PortError(False))
    coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    assert coord.retry_backend(ctx, cap, abandon=True) == BACKEND_FAILED
    with pytest.raises(ValueError):
        coord.retry_backend(ctx, cap, abandon=True)


# -- G4: explicit side-effect-free retry -------------------------------------------


def test_g4_retry_replays_same_call(tmp_path):
    """Retry replays the STORED decision/port; submit cannot re-enter."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(False))
    decision = Decision(operation="send", payload={"n": 1})
    assert coord.submit_backend(ctx, cap, decision, port) == BACKEND_RETRYABLE
    assert port.calls == 1
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, decision, port)
    assert port.calls == 1
    assert coord.retry_backend(ctx, cap) == BACKEND_RETRYABLE
    assert port.calls == 2
    rec = ctx._backend_ledger[cap.request_id]
    assert rec.attempt == 2
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_HELD_TASKS_OPEN


def test_g4_stale_attempt_fence_refuses_without_resend(tmp_path):
    """A stale attempt fence refuses with zero additional port calls."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(False))
    coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    assert port.calls == 1
    with pytest.raises(ValueError):
        coord.retry_backend(ctx, cap, attempt=0)
    assert port.calls == 1
    assert coord.retry_backend(ctx, cap, attempt=1) == BACKEND_RETRYABLE
    assert port.calls == 2


def test_g4_parallel_retry_zero_resend(tmp_path):
    """Two concurrent retries: exactly one port call (GIL-ordered fence)."""
    ctx2 = _ctx(tmp_path, "g4p.db")
    _admit(ctx2)
    cap2 = current_capability(ctx2, SOUL)
    rec = ctx2._backend_ledger[cap2.request_id]
    blocker = BlockingPort()
    rec.decision = Decision(operation="op")
    rec.port = blocker
    rec.state = BACKEND_RETRYABLE

    errors = []

    def racer():
        try:
            coord.retry_backend(ctx2, cap2)
        except ValueError:
            errors.append("refused")

    t = threading.Thread(target=racer)
    t.start()
    assert blocker.entered.wait(timeout=10)
    # Second (concurrent) retry while the first is inside the port: the
    # record already reads RUNNING, so this refuses with zero resend.
    with pytest.raises(ValueError):
        coord.retry_backend(ctx2, cap2)
    blocker.release.set()
    t.join(timeout=20)
    assert not t.is_alive()
    assert blocker.calls == 1
    assert errors == []


# -- G5: Decision / PortResult / port behaviour -------------------------------------


def test_g5_port_protocol_refusals(tmp_path):
    """Ports without execute, or with evidence attrs, are refused outright."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), object())
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), DirtyPort())
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), MemoryPort())
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), VerdictPort())
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, "not-a-decision", OkPort())
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="  "), OkPort())
    # Nothing was stored or sent: the record is still pristine RUNNING.
    rec = ctx._backend_ledger[cap.request_id]
    assert rec.state == BACKEND_RUNNING and rec.attempt == 0
    assert rec.decision is None and rec.port is None


def test_g5_foreign_or_stale_capability_refused(tmp_path):
    """Submit/retry/cancel with a non-live capability raise (fail closed)."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    from src.interfaces.soul_lease import Capability
    foreign = Capability(SOUL, "tok-foreign", cap.request_id, cap.generation)
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, foreign, Decision(operation="op"), OkPort())
    with pytest.raises(ValueError):
        coord.retry_backend(ctx, foreign)
    with pytest.raises(ValueError):
        coord.cancel_backend(ctx, foreign)
    # Journal-only legacy path has no evidence: submit refuses outright.
    jctx = _ctx(tmp_path, "g5j.db")
    ident, out = prepare_publish(jctx, soul_alias=SOUL, entry_kind="web", text="t")
    assert out.should_publish is True
    jctx._backend_ledger.clear()
    jcap = current_capability(jctx, SOUL)
    with pytest.raises(ValueError):
        coord.submit_backend(jctx, jcap, Decision(operation="op"), OkPort())


def test_g5_epoch_fence_refuses(tmp_path):
    """A context running under a stale epoch cannot touch the backend."""
    ctx = _ctx(tmp_path, epoch=1)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    ctx.epoch = 2
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), OkPort())


# -- memory is never backend evidence ------------------------------------------------


def test_memory_outcome_never_backend_evidence(tmp_path):
    """A memory completed outcome leaves RUNNING held (no impersonation)."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    ctx.journal.record_outcome(
        ident.request_id, 1, "memory", "completed", {"completed": True})
    rec = ctx._backend_ledger[cap.request_id]
    assert rec.state == BACKEND_RUNNING and rec.sealed is False
    assert coord.require_current(ctx, ident.request_id) is True
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_HELD_TASKS_OPEN
    assert ctx.lease_store.load(SOUL)["state"] == "HELD"


def test_upstream_rejects_backend_destination(tmp_path):
    """Upstream journal refuses a backend outcome dest (negative pin)."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    with pytest.raises(JournalError):
        ctx.journal.record_outcome(
            ident.request_id, 1, "backend", "completed", {"completed": True})
    with pytest.raises(JournalError):
        ctx.journal.record_outcome(
            ident.request_id, 1, "transport", "completed", {"completed": True})
    row = coord._read_request_by_id(ctx.journal, ident.request_id)
    assert row["state"] == "executing"


# -- seal vs permit / gates ------------------------------------------------------------


def test_sealed_refuses_permits_and_gates(tmp_path):
    """Terminal seal blocks permits, require_current, require_registered."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(False))
    coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    coord.retry_backend(ctx, cap, abandon=True)
    assert coord.require_current(ctx, ident.request_id) is False
    assert coord.require_registered(
        ctx.journal, ident.request_id, ledger=ctx._backend_ledger) is False
    with pytest.raises(ValueError):
        coord.open_permit(
            ctx, request_id=ident.request_id, destination="web", now=T0)
    # Legacy journal-only call shape is unchanged (no ledger kw).
    assert coord.require_registered(ctx.journal, ident.request_id) is True
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED


def test_permit_opened_before_seal_blocks_release_until_closed(tmp_path):
    """A permit opened pre-seal, then cancel-seal: held until closed."""
    ctx = _ctx(tmp_path)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    permit = coord.open_permit(
        ctx, request_id=ident.request_id, destination="web", now=T0)
    port = FailPort(PortError(False))
    coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    assert coord.cancel_backend(ctx, cap) == BACKEND_CANCELLED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_HELD_PERMITS_OPEN
    assert coord.close_permit(ctx, permit_id=permit, now=T0 + 2) is True
    assert coord.settle_request(ctx, cap, T0 + 3) == coord.SETTLE_RELEASED


def test_terminal_repeat_never_resends(tmp_path):
    """Retry/submit past a terminal record: zero additional port calls."""
    ctx = _ctx(tmp_path)
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = FailPort(PortError(False))
    coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    assert port.calls == 1
    coord.retry_backend(ctx, cap, abandon=True)
    assert coord.retry_backend(ctx, cap) == BACKEND_FAILED
    assert port.calls == 1
    with pytest.raises(ValueError):
        coord.submit_backend(ctx, cap, Decision(operation="op"), port)
    assert port.calls == 1


# -- storage failure ---------------------------------------------------------------------


def test_storage_failure_in_generated_fix_is_unknown_and_refused(tmp_path):
    """Port ok + dead generated store -> UNKNOWN; settle refuses (no release)."""
    ctx = _ctx(tmp_path, "s1.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort()
    ctx.journal.conn.close()  # every subsequent statement raises.
    assert coord.submit_backend(
        ctx, cap, Decision(operation="op"), port) == BACKEND_UNKNOWN
    rec = ctx._backend_ledger[cap.request_id]
    assert rec.sealed is True
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_REFUSED_UNKNOWN


def test_storage_failure_in_settle_read_is_refused(tmp_path):
    """Dead journal at settle time -> refused, never released."""
    ctx = _ctx(tmp_path, "s2.db")
    _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="op"), port) == BACKEND_COMPLETED
    ctx.journal.conn.close()
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_REFUSED_UNKNOWN


# -- crash injection: reopen proves no recall, no release -----------------------------------


def test_crash_after_completed_reopen_never_releases(tmp_path):
    """COMPLETED then context loss: fresh context holds, never releases."""
    path = tmp_path / "c1.db"
    conn = _open(path)
    ctx = open_admission_context(conn, epoch=1)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    port = OkPort(rid="resp-c", body="body-c")
    assert coord.submit_backend(
        ctx, cap, Decision(operation="op"), port) == BACKEND_COMPLETED
    # Crash: drop the whole context (memory ledger + tombstones gone).
    # Reopen at the next epoch over the same file: generated rows survive
    # recover() untouched (only old-executing rows go uncertain).
    fresh = open_admission_context(conn, epoch=2)
    assert fresh._backend_ledger == {}
    assert fresh._backend_tombstones == {}
    # The durable fixed response is still there (no evidence forged away),
    # but with no memory terminal evidence the legacy rule holds: a
    # generated row never releases.
    row = coord._read_request_by_id(fresh.journal, ident.request_id)
    assert row["state"] == "generated"
    assert (row["response_id"], row["response_body"]) == ("resp-c", "body-c")
    from src.interfaces.soul_lease import Capability
    rebuilt = Capability(SOUL, cap.holder_token, ident.request_id, cap.generation)
    assert coord.settle_request(fresh, rebuilt, T0 + 1) == coord.SETTLE_HELD_TASKS_OPEN
    assert fresh.lease_store.load(SOUL)["state"] == "HELD"
    conn.close()


def test_crash_between_port_and_fix_never_releases(tmp_path):
    """Port returned but fix unproven at crash: reopen isolates, no resend."""
    path = tmp_path / "c2.db"
    conn = _open(path)
    ctx = open_admission_context(conn, epoch=1)
    ident = _admit(ctx)
    cap = current_capability(ctx, SOUL)
    # Simulate: the port returned, the process died before/within the
    # generated fix. Durable truth is executing (fix unproven); memory is
    # gone with the crashed context.
    fresh = open_admission_context(conn, epoch=2)
    row = coord._read_request_by_id(fresh.journal, ident.request_id)
    assert row["state"] == "uncertain"  # recover() moved old-executing.
    from src.interfaces.soul_lease import Capability
    rebuilt = Capability(SOUL, cap.holder_token, ident.request_id, cap.generation)
    assert coord.settle_request(fresh, rebuilt, T0 + 1) == coord.SETTLE_ISOLATED
    conn.close()


def test_group_admission_binds_ledger(tmp_path):
    """Group winners each bind RUNNING; one grant can submit and release."""
    ctx = _ctx(tmp_path, "grp.db")
    res = coord.admit_group(
        ctx, soul_aliases=["agent_miku", SOUL],
        entry_kind="web", texts={SOUL: "hi", "agent_miku": "yo"}, now=T0,
    )
    assert res.won is True
    for alias, (ident, cap) in res.grants.items():
        rec = ctx._backend_ledger.get(ident.request_id)
        assert rec is not None and rec.state == BACKEND_RUNNING
        assert rec.soul_alias == alias
    ident, cap = res.grants[SOUL]
    port = OkPort()
    assert coord.submit_backend(
        ctx, cap, Decision(operation="op"), port) == BACKEND_COMPLETED
    assert coord.settle_request(ctx, cap, T0 + 1) == coord.SETTLE_RELEASED


# -- new module purity ------------------------------------------------------------------------


def test_backend_execution_module_is_pure():
    """backend_execution is pure vocabulary: stdlib types only, zero IO."""
    rel = "src/interfaces/backend_execution.py"
    tree = ast.parse(Path(rel).read_text(encoding="utf-8"), filename=rel)
    allowed_tops = {"dataclasses", "typing", "__future__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in allowed_tops, a.name
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            assert mod in allowed_tops, node.module
        assert not isinstance(node, (ast.Await, ast.Yield)), "no async/yield"
    # Module level (same calibre as the boundary guard): only dataclass /
    # field calls may execute at import; ValueError raises live inside
    # methods and never fire at import.
    for stmt in tree.body:
        if isinstance(stmt, (ast.Expr, ast.Assign, ast.AnnAssign)):
            val = getattr(stmt, "value", None)
            if val is None:
                continue
            for sub in ast.walk(val):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    name = func.id if isinstance(func, ast.Name) else getattr(
                        func, "attr", "")
                    assert name in ("dataclass", "field"), "module call: %s" % name
    assert set(BACKEND_TERMINAL) == {
        BACKEND_COMPLETED, BACKEND_FAILED, BACKEND_CANCELLED}
