"""SA-0 soul-access contract base (decision-complete, FROZEN DESIGN).

Status machine (fixed, no redesign in later tickets):
    HEAD_LOAD -> GEN1 -> TOOL_LOOP(no-soul) -> TAIL_REJOIN -> GEN2 -> admit.
    No tools: HEAD_LOAD -> GEN1 -> admit (direct, TOOL_LOOP never entered).

Separation rules (frozen):
  - Request / Phase / Provenance are distinct frozen types (this module only).
  - Snapshot carries: 5 identity cols, 6 dedup cols, backend/model/params,
    C budget/deadline, personality version, emotion/relationship/chrono/
    inner-life/projection states, recall, clean history + pool version.
  - Execution capability + join evidence live ONLY in a request-local holder
    (RequestLocalHolder, one instance per request). This module keeps NO
    global holder and NEVER touches the global backend.
  - Old EI-1 stays exactly: one provider attempt, zero tool rounds
    (src/interfaces/context_policy.py:101). This module does not import,
    wrap, or alter any of the six EI-1 modules.

Purity: stdlib only, no IO, no network, no clock, no global mutable state.
All data types are frozen dataclasses / Enum / tuples.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum


__all__ = [
    "Phase",
    "PhaseViolation",
    "SnapshotRejected",
    "RequestIdentity",
    "DedupKey",
    "SoulSnapshot",
    "RequestLocalHolder",
    "Provenance",
    "SNAPSHOT_SCHEMA_VERSION",
    "advance",
    "full_path",
    "new_holder",
    "snapshot_digest",
    "validate_snapshot",
    "is_legal_transition",
]


#: Snapshot schema version (bumped only by a new decision-complete ticket).
SNAPSHOT_SCHEMA_VERSION = "sa0/v1"


class Phase(str, Enum):
    """Fixed SA-0 lifecycle phases (order frozen, names frozen)."""

    HEAD_LOAD = "head_load"
    GEN1 = "gen1"
    TOOL_LOOP = "tool_loop"
    TAIL_REJOIN = "tail_rejoin"
    GEN2 = "gen2"
    ADMIT = "admit"


class PhaseViolation(Exception):
    """Raised when a phase transition breaks the fixed SA-0 machine."""


class SnapshotRejected(Exception):
    """Raised when a snapshot fails SA-0 validation (tamper / illegal build)."""


# Transition table: (from, to, tools_required).
# tools_required None = allowed regardless; True = only with tools;
# False = only without tools. Tuple-only: no mutable module global.
_EDGES = (
    (Phase.HEAD_LOAD, Phase.GEN1, None),
    (Phase.GEN1, Phase.TOOL_LOOP, True),
    (Phase.GEN1, Phase.ADMIT, False),
    (Phase.TOOL_LOOP, Phase.TAIL_REJOIN, None),
    (Phase.TAIL_REJOIN, Phase.GEN2, None),
    (Phase.GEN2, Phase.ADMIT, None),
)


def is_legal_transition(current: Phase, nxt: Phase, *, has_tools: bool) -> bool:
    """Pure predicate: is current -> nxt legal under has_tools?"""
    for frm, to, need in _EDGES:
        if frm is current and to is nxt:
            if need is True and not has_tools:
                return False
            if need is False and has_tools:
                return False
            return True
    return False


def advance(current: Phase, nxt: Phase, *, has_tools: bool) -> Phase:
    """Advance one step; raise PhaseViolation on any off-machine move."""
    if is_legal_transition(current, nxt, has_tools=has_tools):
        return nxt
    raise PhaseViolation(
        "illegal SA-0 transition: %s -> %s (has_tools=%r)" % (current, nxt, has_tools)
    )


def full_path(*, has_tools: bool) -> tuple:
    """The single legal end-to-end path for has_tools True/False."""
    if has_tools:
        return (
            Phase.HEAD_LOAD,
            Phase.GEN1,
            Phase.TOOL_LOOP,
            Phase.TAIL_REJOIN,
            Phase.GEN2,
            Phase.ADMIT,
        )
    return (Phase.HEAD_LOAD, Phase.GEN1, Phase.ADMIT)


@dataclass(frozen=True)
class RequestIdentity:
    """Five identity columns (mirrors EI-1 admission headers + request id)."""

    request_id: str
    conversation_id: str | None
    branch_id: str
    turn_id: str | None
    idempotency_key: str | None


@dataclass(frozen=True)
class DedupKey:
    """Six dedup columns (mirrors EI-1 idempotency namespace)."""

    principal_id: str
    agent_id: str
    scope: str
    conversation_id: str | None
    branch_id: str
    idempotency_key: str | None


@dataclass(frozen=True)
class SoulSnapshot:
    """Immutable per-request soul-access snapshot (no capability inside)."""

    # Five identity cols.
    identity: RequestIdentity
    # Six dedup cols.
    dedup: DedupKey
    # Backend / model / params.
    backend_alias: str
    model: str
    params: tuple = ()
    # C budget / deadline.
    budget_tokens: int = 0
    deadline_utc: str = ""
    # Personality version.
    personality_version: str = ""
    # Emotion / relationship / chrono / inner-life / projection.
    emotion_state: str = ""
    relationship_state: str = ""
    chrono_state: str = ""
    inner_life_state: str = ""
    projection_state: str = ""
    # Recall.
    recall_version: str = ""
    recall_items: tuple = ()
    # Clean history + pool version.
    history_pool_version: str = ""
    clean_history: tuple = ()


@dataclass(frozen=True)
class RequestLocalHolder:
    """Request-local execution capability + join evidence (never global).

    One fresh instance per request via new_holder(). The snapshot never
    carries capability; the global backend is never touched here.
    """

    capability: str
    join_evidence: tuple = ()


@dataclass(frozen=True)
class Provenance:
    """Where a snapshot came from: digest + travelled phases."""

    created_utc: str
    snapshot_digest: str
    transitions: tuple = ()


def new_holder(capability: str, join_evidence: tuple = ()) -> RequestLocalHolder:
    """Create one fresh request-local holder (no sharing, no globals)."""
    if not isinstance(capability, str) or not capability:
        raise SnapshotRejected("holder capability must be a non-empty str")
    if not isinstance(join_evidence, tuple):
        raise SnapshotRejected("holder join_evidence must be a tuple")
    return RequestLocalHolder(capability=capability, join_evidence=join_evidence)


def _non_empty_str(value: object) -> bool:
    return isinstance(value, str) and len(value) > 0


def validate_snapshot(snapshot: SoulSnapshot) -> SoulSnapshot:
    """Accept a legally built snapshot; raise SnapshotRejected on tamper."""
    if not isinstance(snapshot, SoulSnapshot):
        raise SnapshotRejected("not a SoulSnapshot")
    ident = snapshot.identity
    dedup = snapshot.dedup
    if not isinstance(ident, RequestIdentity) or not isinstance(dedup, DedupKey):
        raise SnapshotRejected("snapshot identity/dedup mistyped")
    # Five identity cols: request/branch required; conv/turn/key optional.
    if not _non_empty_str(ident.request_id) or not _non_empty_str(ident.branch_id):
        raise SnapshotRejected("identity request_id/branch_id required")
    for opt in (ident.conversation_id, ident.turn_id, ident.idempotency_key):
        if opt is not None and not _non_empty_str(opt):
            raise SnapshotRejected("identity optional col must be None or non-empty")
    # Six dedup cols: principal/agent/scope/branch required; conv/key optional.
    if not _non_empty_str(dedup.principal_id) or not _non_empty_str(dedup.agent_id):
        raise SnapshotRejected("dedup principal_id/agent_id required")
    if not _non_empty_str(dedup.scope) or not _non_empty_str(dedup.branch_id):
        raise SnapshotRejected("dedup scope/branch_id required")
    for opt in (dedup.conversation_id, dedup.idempotency_key):
        if opt is not None and not _non_empty_str(opt):
            raise SnapshotRejected("dedup optional col must be None or non-empty")
    # Backend / model / params.
    if not _non_empty_str(snapshot.backend_alias) or not _non_empty_str(snapshot.model):
        raise SnapshotRejected("backend_alias/model required")
    if not isinstance(snapshot.params, tuple):
        raise SnapshotRejected("params must be a tuple")
    # C budget / deadline.
    if not isinstance(snapshot.budget_tokens, int) or snapshot.budget_tokens <= 0:
        raise SnapshotRejected("budget_tokens must be a positive int")
    if not _non_empty_str(snapshot.deadline_utc):
        raise SnapshotRejected("deadline_utc required")
    # Personality version.
    if not _non_empty_str(snapshot.personality_version):
        raise SnapshotRejected("personality_version required")
    # Five carried states.
    for name in (
        "emotion_state",
        "relationship_state",
        "chrono_state",
        "inner_life_state",
        "projection_state",
    ):
        if not _non_empty_str(getattr(snapshot, name)):
            raise SnapshotRejected("%s required" % name)
    # Recall + clean history + pool versions.
    if not _non_empty_str(snapshot.recall_version):
        raise SnapshotRejected("recall_version required")
    if not isinstance(snapshot.recall_items, tuple):
        raise SnapshotRejected("recall_items must be a tuple")
    if not _non_empty_str(snapshot.history_pool_version):
        raise SnapshotRejected("history_pool_version required")
    if not isinstance(snapshot.clean_history, tuple):
        raise SnapshotRejected("clean_history must be a tuple")
    return snapshot


def snapshot_digest(snapshot: SoulSnapshot) -> str:
    """Canonical sha256 over the snapshot (tamper-evident seal)."""
    ident = snapshot.identity
    dedup = snapshot.dedup
    payload = {
        "schema": SNAPSHOT_SCHEMA_VERSION,
        "identity": {
            "request_id": ident.request_id,
            "conversation_id": ident.conversation_id,
            "branch_id": ident.branch_id,
            "turn_id": ident.turn_id,
            "idempotency_key": ident.idempotency_key,
        },
        "dedup": {
            "principal_id": dedup.principal_id,
            "agent_id": dedup.agent_id,
            "scope": dedup.scope,
            "conversation_id": dedup.conversation_id,
            "branch_id": dedup.branch_id,
            "idempotency_key": dedup.idempotency_key,
        },
        "backend_alias": snapshot.backend_alias,
        "model": snapshot.model,
        "params": list(snapshot.params),
        "budget_tokens": snapshot.budget_tokens,
        "deadline_utc": snapshot.deadline_utc,
        "personality_version": snapshot.personality_version,
        "emotion_state": snapshot.emotion_state,
        "relationship_state": snapshot.relationship_state,
        "chrono_state": snapshot.chrono_state,
        "inner_life_state": snapshot.inner_life_state,
        "projection_state": snapshot.projection_state,
        "recall_version": snapshot.recall_version,
        "recall_items": list(snapshot.recall_items),
        "history_pool_version": snapshot.history_pool_version,
        "clean_history": list(snapshot.clean_history),
    }
    data = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
