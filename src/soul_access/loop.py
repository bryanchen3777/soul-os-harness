"""SA-3 head/tail state machine: TOOL_LOOP holds no soul, TAIL rejoins clean.

Decision-complete scope (SA-3): this module + snapshot.py +
tests/soul_access/test_t3_loop.py are new; the proxy gains one additive
request-local junction (hooks only); SA-0 / SA-1 / SA-2 / EI-1 untouched.

Fixed path (SA-0 phases, has_tools=True)::

    HEAD_LOAD -> GEN1 -> TOOL_LOOP(no-soul) -> TAIL_REJOIN -> GEN2 -> admit

Rules (fixed):

  - HEAD_LOAD takes one live snapshot (load #1); GEN1 generates once.
  - TOOL_LOOP holds ONLY the harness context. It carries no soul prompt,
    no recall capability, no consciousness, no judge, and writes no soul
    state. Tool / provider handles must ALL join with a determined result
    before TAIL_REJOIN may run.
  - TOOL_LOOP provider cost is counted separately (``tool_provider_calls``)
    and is NEVER folded into the two soul generation counters.
  - Cancel is NOT a join. Timeout (not joined), undetermined result, or a
    crashed handle all settle the loop as UNKNOWN: the row is quarantined
    (SA-2 ``PoolStore.quarantine``), TAIL is banned, resend is banned, and
    no ``finally`` releases the row (pinned by AST: this module contains
    zero ``finally`` clauses).
  - TAIL_REJOIN takes a FRESH live snapshot (load #2) and admits ONLY a
    clean reply draft plus whitelisted Natural results
    (``NATURAL_WHITELIST``). GEN2 generates once. Admit completes the pool
    row so the same key replays with zero new loads / generations / writes.
  - Same-key replay returns the ORIGINAL text with
    ``loads == 0`` / ``generation_attempts == 0`` (zero new cost).

Purity of imports: stdlib + SA-0 ``schema`` + sibling ``pool`` /
``snapshot`` only. No EI-1 imports, no provider / backend imports, no
network, no clock, no threads, no event loop (Windows runner: the SA-0
conftest in tests/soul_access hard-blocks socket creation, and ANY event
loop creation needs a socketpair self-pipe). Late results / cancel races
are modelled with the synchronous ``ControlledFuture`` below.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from dataclasses import field

from src.soul_access import pool as _pool
from src.soul_access import snapshot as _snapshot_mod
from src.soul_access.schema import (
    Phase,
    Provenance,
    RequestLocalHolder,
    SoulSnapshot,
    SnapshotRejected,
    advance,
    full_path,
    snapshot_digest,
    validate_snapshot,
)

__all__ = [
    "SA3_LOOP_VERSION",
    "NATURAL_WHITELIST",
    "OUTCOME_JOINED",
    "OUTCOME_CANCELLED",
    "OUTCOME_TIMEOUT",
    "OUTCOME_CRASHED",
    "OUTCOME_UNKNOWN",
    "LoopRejected",
    "ToolHandle",
    "ToolLoop",
    "TailDraft",
    "LoopResult",
    "ControlledFuture",
    "build_tail_draft",
    "run_tool_request",
]

#: Loop version (bumped only by a decision-complete ticket).
SA3_LOOP_VERSION = "sa3/v1"

#: The ONLY Natural result kinds the tail may carry. Anything else
#: (tool internals, raw provider payloads, unlisted kinds) is refused.
NATURAL_WHITELIST = frozenset(("natural_note", "natural_summary", "natural_draft"))

#: Per-handle settle outcomes. Only OUTCOME_JOINED admits TAIL_REJOIN.
OUTCOME_JOINED = "joined"
OUTCOME_CANCELLED = "cancelled"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_CRASHED = "crashed"
OUTCOME_UNKNOWN = "unknown"


class LoopRejected(Exception):
    """Fail-closed refusal: bad tool use (400) or unjoined tail (409)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class ToolHandle:
    """One registered tool/provider handle (harness-side identity only)."""

    handle_id: str
    kind: str = "tool"


@dataclass
class ToolLoop:
    """One request's TOOL_LOOP: harness context only, never soul.

    ``harness_context`` is opaque harness data (never a soul prompt, never
    a recall capability). Handles settle via ``join`` (determined result),
    ``cancel`` (never a join), ``timeout`` / ``crash`` / ``mark_unknown``.
    """

    harness_context: tuple = ()
    _records: dict = field(default_factory=dict)

    def register(self, handle_id: str, *, kind: str = "tool") -> ToolHandle:
        """Register one handle as pending (duplicate / empty => 400)."""
        if not isinstance(handle_id, str) or not handle_id:
            raise LoopRejected(400, "invalid_request", "handle id required")
        if not isinstance(kind, str) or not kind:
            raise LoopRejected(400, "invalid_request", "handle kind required")
        if handle_id in self._records:
            raise LoopRejected(400, "invalid_request", "duplicate handle")
        self._records[handle_id] = {
            "kind": kind,
            "outcome": None,
            "result": None,
            "evidence": "",
        }
        return ToolHandle(handle_id=handle_id, kind=kind)

    def join(self, handle_id: str, *, kind: str, content: str) -> str:
        """Join one handle with its DETERMINED result (returns evidence).

        Natural kinds (``natural_*``) must be whitelisted; every content
        must be a non-empty str (undetermined => 400, never joined).
        Joining a non-pending handle (late result after settle) is refused
        (409): a late result can never revive an UNKNOWN loop.
        """
        record = self._records.get(handle_id)
        if record is None:
            raise LoopRejected(400, "invalid_request", "unknown handle")
        if record["outcome"] is not None:
            raise LoopRejected(409, "already_settled", "handle already settled")
        if not isinstance(kind, str) or not kind:
            raise LoopRejected(400, "invalid_request", "result kind required")
        if kind.startswith("natural_") and kind not in NATURAL_WHITELIST:
            raise LoopRejected(400, "invalid_request", "natural kind not whitelisted")
        if not isinstance(content, str) or not content:
            raise LoopRejected(400, "invalid_request", "result undetermined")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        evidence = "%s:%s:%s" % (handle_id, kind, digest)
        record["outcome"] = OUTCOME_JOINED
        record["result"] = (kind, content)
        record["evidence"] = evidence
        return evidence

    def cancel(self, handle_id: str) -> None:
        """Cancel one handle. Cancel is NOT a join (tail stays banned)."""
        record = self._records.get(handle_id)
        if record is None:
            raise LoopRejected(400, "invalid_request", "unknown handle")
        if record["outcome"] is not None:
            raise LoopRejected(409, "already_settled", "handle already settled")
        record["outcome"] = OUTCOME_CANCELLED

    def timeout(self, handle_id: str) -> None:
        """Mark one handle timed-out (not joined => UNKNOWN family)."""
        record = self._records.get(handle_id)
        if record is None:
            raise LoopRejected(400, "invalid_request", "unknown handle")
        if record["outcome"] is not None:
            raise LoopRejected(409, "already_settled", "handle already settled")
        record["outcome"] = OUTCOME_TIMEOUT

    def crash(self, handle_id: str) -> None:
        """Mark one handle crashed (not joined => UNKNOWN family)."""
        record = self._records.get(handle_id)
        if record is None:
            raise LoopRejected(400, "invalid_request", "unknown handle")
        if record["outcome"] is not None:
            raise LoopRejected(409, "already_settled", "handle already settled")
        record["outcome"] = OUTCOME_CRASHED

    def mark_unknown(self, handle_id: str) -> None:
        """Mark one handle outcome-unknown (not joined => UNKNOWN family)."""
        record = self._records.get(handle_id)
        if record is None:
            raise LoopRejected(400, "invalid_request", "unknown handle")
        if record["outcome"] is not None:
            raise LoopRejected(409, "already_settled", "handle already settled")
        record["outcome"] = OUTCOME_UNKNOWN

    @property
    def handle_ids(self) -> tuple:
        """Registered handle ids in registration order."""
        return tuple(self._records)

    @property
    def is_fully_joined(self) -> bool:
        """True only when >= 1 handle exists and EVERY handle joined."""
        if not self._records:
            return False
        return all(
            record["outcome"] == OUTCOME_JOINED for record in self._records.values()
        )

    @property
    def join_evidence(self) -> tuple:
        """Join evidence in registration order (empty unless fully joined)."""
        if not self.is_fully_joined:
            return ()
        return tuple(self._records[hid]["evidence"] for hid in self._records)


@dataclass(frozen=True)
class TailDraft:
    """The ONLY payload TAIL_REJOIN may carry: clean draft + whitelist."""

    text: str
    natural: tuple = ()


def build_tail_draft(text: str, natural: tuple = ()) -> TailDraft:
    """Validate a tail draft (clean text + whitelisted Natural results)."""
    if not isinstance(text, str) or not text:
        raise LoopRejected(400, "invalid_request", "tail draft text required")
    if not isinstance(natural, tuple):
        raise LoopRejected(400, "invalid_request", "tail natural must be a tuple")
    clean = []
    for item in natural:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or not isinstance(item[1], str)
            or not item[0]
            or not item[1]
        ):
            raise LoopRejected(400, "invalid_request", "tail natural mistyped")
        if item[0] not in NATURAL_WHITELIST:
            raise LoopRejected(400, "invalid_request", "natural kind not whitelisted")
        clean.append(item)
    return TailDraft(text=text, natural=tuple(clean))


@dataclass(frozen=True)
class LoopResult:
    """Admitted tool-path reply + accounting + travelled evidence."""

    text: str
    loads: int
    generation_attempts: int
    tool_provider_calls: int
    replayed: bool
    provenance: Provenance
    join_evidence: tuple = ()


@dataclass
class ControlledFuture:
    """Synchronous in-process stand-in for one tool/provider future.

    No threads, no event loop: the test drives completion manually, so
    late-result / cancel races are fully deterministic and in-process.
    """

    _state: str = "pending"
    _result: object = None
    _late: bool = False

    def set_result(self, result) -> None:
        """Complete a pending future; completing a settled one marks LATE."""
        if self._state != "pending":
            self._late = True
            return
        self._state = "done"
        self._result = result

    def set_exception(self) -> None:
        """Fail a pending future; failing a settled one marks LATE."""
        if self._state != "pending":
            self._late = True
            return
        self._state = "failed"

    def cancel(self) -> bool:
        """Cancel a pending future (False when already settled)."""
        if self._state != "pending":
            return False
        self._state = "cancelled"
        return True

    def done(self) -> bool:
        """True only for a determined (done) future."""
        return self._state == "done"

    def result(self):
        """Return the determined result (raises when not determined)."""
        if self._state != "done":
            raise LoopRejected(409, "undetermined", "future has no determined result")
        return self._result


def _settle_future(loop: ToolLoop, handle_id: str, future: ControlledFuture,
                   tool_provider_calls: list) -> None:
    """Settle one handle from its controlled future (sync, in-process)."""
    if not isinstance(future, ControlledFuture):
        raise LoopRejected(400, "invalid_request", "future mistyped")
    tool_provider_calls.append(handle_id)
    if future._state == "cancelled":
        loop.cancel(handle_id)
    elif future._state == "done":
        result = future.result()
        if not isinstance(result, tuple) or len(result) != 2:
            loop.mark_unknown(handle_id)
        else:
            kind, content = result
            try:
                loop.join(handle_id, kind=kind, content=content)
            except LoopRejected:
                loop.mark_unknown(handle_id)
    else:
        # pending / failed: not joined, result不明 or crashed.
        if future._state == "failed":
            loop.crash(handle_id)
        else:
            loop.timeout(handle_id)


def run_tool_request(
    *,
    snapshot: SoulSnapshot,
    provenance: Provenance,
    holder: RequestLocalHolder,
    pool: _pool.PoolStore,
    fingerprint: str,
    harness_context: tuple,
    handle_ids: tuple,
    tool_runner,
    generate,
    make_tail_draft,
    tail_live: _snapshot_mod.LiveState,
    soul=None,
    created_utc: str,
) -> LoopResult:
    """Run one HEAD_LOAD -> GEN1 -> TOOL_LOOP -> TAIL_REJOIN -> GEN2 request.

    ``tool_runner(handle_id, harness_context, gen1_text)`` runs harness-side
    only and returns ``(kind, content)`` / a ``ControlledFuture`` / raises.
    ``generate(snapshot, holder, phase_name)`` is the soul generation hook
    (called exactly twice: ``gen1`` + ``gen2``). ``make_tail_draft`` builds
    the tail draft from the join evidence. ``soul`` (optional spies) is NEVER
    touched by the TOOL_LOOP section below. ``tail_live`` is the fresh TAIL
    live read (load #2).
    """
    if not isinstance(holder, RequestLocalHolder):
        raise SnapshotRejected("holder mistyped")
    if not holder.capability:
        raise SnapshotRejected("holder capability required")
    if not isinstance(pool, _pool.PoolStore):
        raise LoopRejected(400, "invalid_request", "pool mistyped")
    if not isinstance(snapshot, SoulSnapshot):
        raise SnapshotRejected("not a SoulSnapshot")
    if not isinstance(provenance, Provenance):
        raise SnapshotRejected("provenance mistyped")
    if not isinstance(harness_context, tuple):
        raise LoopRejected(400, "invalid_request", "harness context must be a tuple")
    if not isinstance(handle_ids, tuple) or not handle_ids:
        raise LoopRejected(400, "invalid_request", "tool handles required")
    if not callable(tool_runner) or not callable(generate) or not callable(make_tail_draft):
        raise LoopRejected(500, "internal", "tool request not wired")
    if not isinstance(created_utc, str) or not created_utc:
        raise SnapshotRejected("provenance created_utc required")

    outcome = pool.claim(snapshot.dedup, fingerprint)
    if outcome.disposition == _pool.DISPOSITION_REPLAY:
        return LoopResult(
            text=outcome.response_text,
            loads=0,
            generation_attempts=0,
            tool_provider_calls=0,
            replayed=True,
            provenance=Provenance(
                created_utc=created_utc,
                snapshot_digest=provenance.snapshot_digest,
                transitions=tuple(phase.value for phase in full_path(has_tools=True)),
            ),
            join_evidence=(),
        )
    if outcome.disposition != _pool.DISPOSITION_NEW:
        raise LoopRejected(
            outcome.http_status, outcome.code, "pool claim: " + outcome.message
        )

    # Load #1 seal: the head snapshot must validate and match its seal.
    validate_snapshot(snapshot)
    head_digest = snapshot_digest(snapshot)
    if provenance.snapshot_digest != head_digest:
        pool.quarantine(snapshot.dedup)
        raise SnapshotRejected("provenance digest seal broken")

    phase = Phase.HEAD_LOAD
    phase = advance(phase, Phase.GEN1, has_tools=True)
    gen1_text = generate(snapshot, holder, "gen1")
    if not isinstance(gen1_text, str) or not gen1_text:
        pool.quarantine(snapshot.dedup)
        raise LoopRejected(500, "internal", "gen1 mistyped")
    phase = advance(phase, Phase.TOOL_LOOP, has_tools=True)

    # -- TOOL_LOOP (no-soul) -------------------------------------------------
    # This block touches ONLY harness_context + tool_runner + the ToolLoop.
    # It must never reach the live side: no prompts, no recall, no
    # synthesis, no judgement, no state writes.
    # (Pinned by the T3 section-is-free test.)
    loop = ToolLoop(harness_context=harness_context)
    try:
        for handle_id in handle_ids:
            loop.register(handle_id)
    except LoopRejected:
        pool.quarantine(snapshot.dedup)
        raise
    tool_provider_calls: list = []
    for handle_id in handle_ids:
        try:
            produced = tool_runner(handle_id, harness_context, gen1_text)
        except Exception:
            loop.crash(handle_id)
            tool_provider_calls.append(handle_id)
            continue
        if isinstance(produced, ControlledFuture):
            _settle_future(loop, handle_id, produced, tool_provider_calls)
        elif isinstance(produced, tuple) and len(produced) == 2:
            kind, content = produced
            tool_provider_calls.append(handle_id)
            try:
                loop.join(handle_id, kind=kind, content=content)
            except LoopRejected:
                loop.mark_unknown(handle_id)
        else:
            # Cancel token / timeout token / undetermined shape: never joined.
            tool_provider_calls.append(handle_id)
            if produced == "cancel":
                loop.cancel(handle_id)
            elif produced == "timeout":
                loop.timeout(handle_id)
            elif produced == "crash":
                loop.crash(handle_id)
            else:
                loop.mark_unknown(handle_id)
    # -- TAIL_REJOIN ---------------------------------------------------------
    if not loop.is_fully_joined:
        # Not all handles joined (cancel / timeout / unknown / crash):
        # quarantine the row, ban TAIL, ban resend, never release.
        pool.quarantine(snapshot.dedup)
        raise LoopRejected(409, "tools_not_joined", "tool loop not fully joined")

    tail_snapshot = _snapshot_mod.take_tail_snapshot(head=snapshot, live=tail_live)
    tail_digest = snapshot_digest(tail_snapshot)
    phase = advance(phase, Phase.TAIL_REJOIN, has_tools=True)
    try:
        draft = make_tail_draft(loop.join_evidence)
    except LoopRejected:
        pool.quarantine(snapshot.dedup)
        raise
    except Exception as exc:
        pool.quarantine(snapshot.dedup)
        raise LoopRejected(500, "internal", "tail draft failed: %s" % (exc,)) from exc
    if not isinstance(draft, TailDraft):
        pool.quarantine(snapshot.dedup)
        raise LoopRejected(500, "internal", "tail draft mistyped")
    phase = advance(phase, Phase.GEN2, has_tools=True)
    gen2_text = generate(tail_snapshot, holder, "gen2")
    if not isinstance(gen2_text, str) or not gen2_text:
        pool.quarantine(snapshot.dedup)
        raise LoopRejected(500, "internal", "gen2 mistyped")
    phase = advance(phase, Phase.ADMIT, has_tools=True)
    assert phase is Phase.ADMIT

    pool.complete(snapshot.dedup, fingerprint, gen2_text)
    return LoopResult(
        text=gen2_text,
        loads=2,
        generation_attempts=2,
        tool_provider_calls=len(tool_provider_calls),
        replayed=False,
        provenance=Provenance(
            created_utc=created_utc,
            snapshot_digest=tail_digest,
            transitions=tuple(phase.value for phase in full_path(has_tools=True)),
        ),
        join_evidence=loop.join_evidence,
    )
