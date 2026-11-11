"""SA-3 head/tail live-state snapshots (one live read per phase, never cached).

Decision-complete scope (SA-3): only this module + loop.py +
tests/soul_access/test_t3_loop.py are new; the proxy gains one additive
request-local junction (state-machine hooks only, no generation change);
SA-0 / SA-1 / SA-2 / EI-1 untouched.

Rules (fixed):

  - HEAD_LOAD takes exactly one live-state snapshot (load #1); TAIL_REJOIN
    takes exactly one fresh live-state snapshot (load #2). Neither snapshot
    is cached, reused, or carried across phases: the tail snapshot is built
    from the head snapshot's carried columns plus a NEW live read.
  - A snapshot carries NO execution capability (SA-0 separation): capability
    + join evidence live ONLY in the request-local holder.
  - Purity: stdlib + SA-0 ``schema`` only. No EI-1 imports, no provider /
    backend imports, no network, no clock, no global mutable state. The live
    values are PASSED IN by the caller (the caller owns the live instances);
    this module never constructs or touches a bus / llm / middleware.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.soul_access.schema import (
    DedupKey,
    RequestIdentity,
    SoulSnapshot,
    SnapshotRejected,
    validate_snapshot,
)

__all__ = [
    "SA3_SNAPSHOT_VERSION",
    "LiveState",
    "take_head_snapshot",
    "take_tail_snapshot",
]

#: Snapshot-taker version (bumped only by a decision-complete ticket).
SA3_SNAPSHOT_VERSION = "sa3/v1"


@dataclass(frozen=True)
class LiveState:
    """One live read of the soul-side state (per-phase, never cached).

    The caller reads these from the live instances (HEAD once, TAIL once)
    and passes them in. This module stores nothing between calls.
    """

    personality_version: str
    emotion_state: str
    relationship_state: str
    chrono_state: str
    inner_life_state: str
    projection_state: str
    recall_version: str
    recall_items: tuple
    history_pool_version: str
    clean_history: tuple


def _require_live(live: LiveState) -> LiveState:
    if not isinstance(live, LiveState):
        raise SnapshotRejected("live state mistyped")
    for name in (
        "personality_version",
        "emotion_state",
        "relationship_state",
        "chrono_state",
        "inner_life_state",
        "projection_state",
        "recall_version",
        "history_pool_version",
    ):
        value = getattr(live, name)
        if not isinstance(value, str) or not value:
            raise SnapshotRejected("live %s required" % name)
    for name in ("recall_items", "clean_history"):
        if not isinstance(getattr(live, name), tuple):
            raise SnapshotRejected("live %s must be a tuple" % name)
    return live


def take_head_snapshot(
    *,
    identity: RequestIdentity,
    dedup: DedupKey,
    backend_alias: str,
    model: str,
    params: tuple = (),
    budget_tokens: int = 0,
    deadline_utc: str = "",
    live: LiveState,
) -> SoulSnapshot:
    """Build the HEAD_LOAD snapshot from one live read (load #1)."""
    _require_live(live)
    if not isinstance(identity, RequestIdentity) or not isinstance(dedup, DedupKey):
        raise SnapshotRejected("head identity/dedup mistyped")
    snapshot = SoulSnapshot(
        identity=identity,
        dedup=dedup,
        backend_alias=backend_alias,
        model=model,
        params=params,
        budget_tokens=budget_tokens,
        deadline_utc=deadline_utc,
        personality_version=live.personality_version,
        emotion_state=live.emotion_state,
        relationship_state=live.relationship_state,
        chrono_state=live.chrono_state,
        inner_life_state=live.inner_life_state,
        projection_state=live.projection_state,
        recall_version=live.recall_version,
        recall_items=live.recall_items,
        history_pool_version=live.history_pool_version,
        clean_history=live.clean_history,
    )
    return validate_snapshot(snapshot)


def take_tail_snapshot(*, head: SoulSnapshot, live: LiveState) -> SoulSnapshot:
    """Build the TAIL_REJOIN snapshot from a FRESH live read (load #2).

    Carried columns (identity / dedup / backend / model / params / C
    budget / deadline) come from the validated head snapshot unchanged;
    every live-state column comes from the new read. The head snapshot is
    re-validated first so a tampered head can never seed a tail.
    """
    _require_live(live)
    validate_snapshot(head)
    snapshot = SoulSnapshot(
        identity=head.identity,
        dedup=head.dedup,
        backend_alias=head.backend_alias,
        model=head.model,
        params=head.params,
        budget_tokens=head.budget_tokens,
        deadline_utc=head.deadline_utc,
        personality_version=live.personality_version,
        emotion_state=live.emotion_state,
        relationship_state=live.relationship_state,
        chrono_state=live.chrono_state,
        inner_life_state=live.inner_life_state,
        projection_state=live.projection_state,
        recall_version=live.recall_version,
        recall_items=live.recall_items,
        history_pool_version=live.history_pool_version,
        clean_history=live.clean_history,
    )
    return validate_snapshot(snapshot)
