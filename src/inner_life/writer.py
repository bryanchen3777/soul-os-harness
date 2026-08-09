"""
src/inner_life/writer.py — InnerLifeWriter (Canonical Identity Authority)

M5.4-5.1 (Bry 派工 2026-08-09 18:25) — Inner Life Unified Architecture Foundation

派工精神:
  - "Don't just be a wrapper for the three existing writers"
  - "Need to clearly define: ownership, event identity, provenance, correlation,
     lineage, timestamp semantics, persistence responsibility, error semantics"
  - "Memory failure MUST NOT block Diary behavior"
  - "Unified architecture ≠ shared failure dependency"

InnerLifeWriter is the CANONICAL IDENTITY AUTHORITY for narrative events.
It does NOT wrap Memory/Diary/Dream writers. It is a STANDALONE component
that downstream systems will OPTIONALLY consume (future工單).

Key design:
  - All 3 downstream writers (Memory, Diary, Dream) work WITHOUT InnerLifeWriter
    (preserves M5.4-1 independence contract — no failure dependency)
  - InnerLifeWriter ONLY assigns canonical identity (event_id, parent linkage,
    lineage derivation)
  - InnerLifeWriter does NOT persist to any DB (foundation only, no storage layer)
  - InnerLifeWriter does NOT replace existing writers
  - Future工單 will introduce an OPTIONAL integration where existing writers
    call InnerLifeWriter.create_event() to get an event_id, then store the
    event_id in their own payload (ADDITIVE, non-breaking)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from .event import InnerLifeEvent, Provenance
from .identity import (
    IdentityValidationError,
    derive_lineage,
    generate_event_id,
    now_utc_iso,
    validate_correlation_id,
    validate_event_id,
    validate_parent_event_id,
    validate_session_id,
)

logger = logging.getLogger("soul_os.inner_life.writer")


@dataclass
class InnerLifeWriterStats:
    """Observability counters for the writer instance."""
    events_created: int = 0
    root_events: int = 0
    child_events: int = 0
    cross_session_events: int = 0
    distinct_sessions: int = 0
    distinct_correlations: int = 0
    lineage_chains: int = 0  # 至少有一個 child 的 parent_event_id 數量


class InnerLifeWriter:
    """
    Canonical identity authority for Inner Life events.

    This is the FOUNDATION layer of the unified Inner Life architecture.
    It assigns canonical event_id, parent linkage, and lineage derivation
    to narrative events that downstream systems (Memory, Diary, Dream,
    future) optionally reference.

    派工派工 design constraints (preserved):
      - Memory/Diary/Dream work without this class
      - No persistence layer (future工單)
      - No wrapper behavior (派工明列禁止)
      - No shared failure dependency

    Per-instance state:
      - _known_event_ids: set of all event_ids created by this instance
      - _index_by_session: session_id → list of event_ids
      - _index_by_correlation: correlation_id → list of event_ids
      - _children_by_parent: parent_event_id → list of child event_ids
      - _stats: observability counters

    Per-instance state is IN-MEMORY ONLY, process-lifetime, no persistence.
    Restarting the process = fresh state (consistent with M5.3 EPHEMERAL
    invariant for WorldPerceptionState — InnerLifeWriter follows same pattern).
    """

    def __init__(self) -> None:
        # 所有 known event_id (用於 existence check)
        self._known_event_ids: Set[str] = set()
        # event_id → InnerLifeEvent (event lookup)
        self._events: Dict[str, InnerLifeEvent] = {}
        # 已知 session_id 索引
        self._index_by_session: Dict[str, List[str]] = {}
        # 已知 correlation_id 索引
        self._index_by_correlation: Dict[str, List[str]] = {}
        # parent → children 索引
        self._children_by_parent: Dict[str, List[str]] = {}
        # observability
        self._stats = InnerLifeWriterStats()

    # ─────────────────────────────────────────────────────────────
    # Event creation — the main public API
    # ─────────────────────────────────────────────────────────────

    def create_event(
        self,
        *,
        provenance: Provenance,
        session_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> InnerLifeEvent:
        """
        Create a new Inner Life event with canonical identity assignment.

        Args:
            provenance: structured WHO/WHAT/WHERE/WHY (Provenance instance)
            session_id: optional runtime session anchor (None for cross-session)
            correlation_id: optional narrative group marker
            parent_event_id: optional causation parent (must reference known event)
            ts: optional explicit ISO 8601 UTC timestamp (default: now)

        Returns:
            InnerLifeEvent with assigned event_id and computed lineage

        Raises:
            IdentityValidationError: on invalid identity fields or unknown parent
            TypeError: on wrong types
        """
        # 1. Validate all input fields (delegated)
        if not isinstance(provenance, Provenance):
            raise IdentityValidationError(
                f"provenance 必須是 Provenance, got: {type(provenance).__name__}"
            )
        validate_session_id(session_id)
        validate_correlation_id(correlation_id)
        validate_parent_event_id(parent_event_id)
        # ts default to now
        if ts is None:
            ts = now_utc_iso()
        else:
            # Will be validated inside InnerLifeEvent.__post_init__
            pass

        # 2. Validate parent existence (per-instance, not format-only)
        # 派工: "must reference known event if set"
        if parent_event_id is not None and parent_event_id not in self._known_event_ids:
            raise IdentityValidationError(
                f"parent_event_id {parent_event_id!r} 不在已知事件清單內 "
                f"({len(self._known_event_ids)} known events). "
                f"InnerLifeWriter 是 per-instance 權威, 跨 instance parent 不可用"
            )

        # 3. Generate canonical event_id (uuid4 hex, never re-issued)
        # 派工: "event_id uniqueness" + "UUID collision probability 2^-122"
        new_event_id = generate_event_id()

        # 4. Compute lineage from parent
        if parent_event_id is None:
            # Root event
            lineage_depth, lineage_path = 0, new_event_id
        else:
            parent = self._events[parent_event_id]
            parent_depth = parent.lineage_depth
            parent_path = parent.lineage_path
            lineage_depth, lineage_path = derive_lineage(
                parent_depth, parent_path, new_event_id
            )

        # 5. Build event
        event = InnerLifeEvent(
            event_id=new_event_id,
            session_id=session_id,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            ts=ts,
            provenance=provenance,
            lineage_depth=lineage_depth,
            lineage_path=lineage_path,
        )

        # 6. Register in instance state
        self._register_event(event)

        # 7. Update stats
        self._stats.events_created += 1
        if event.is_root():
            self._stats.root_events += 1
        else:
            self._stats.child_events += 1
            if parent_event_id in self._children_by_parent:
                # parent already had children
                pass
            else:
                self._stats.lineage_chains += 1
        if not event.is_session_anchored():
            self._stats.cross_session_events += 1
        self._stats.distinct_sessions = len(self._index_by_session)
        self._stats.distinct_correlations = len(self._index_by_correlation)

        logger.info(
            f"[InnerLifeWriter] ✓ created event_id={new_event_id[:12]}... "
            f"depth={lineage_depth} session={session_id} "
            f"correlation={correlation_id} trigger={provenance.trigger_type}"
        )

        return event

    # ─────────────────────────────────────────────────────────────
    # Query (per派工: events with same session / same correlation /
    #                   descendant of / etc.)
    # ─────────────────────────────────────────────────────────────

    def is_event_known(self, event_id: str) -> bool:
        """
        Check if an event_id was created by this writer instance.

        Note: InnerLifeWriter is per-instance authority.
        Cross-instance event_id references are NOT valid.
        """
        validate_event_id(event_id)
        return event_id in self._known_event_ids

    def get_event(self, event_id: str) -> Optional[InnerLifeEvent]:
        """
        Get a previously-created event by event_id.

        Returns None if not found (caller should distinguish from "unknown event_id"
        vs "deleted event" — neither is supported in this foundation).
        """
        validate_event_id(event_id)
        return self._events.get(event_id)  # type: ignore[attr-defined]

    def get_events_by_session(self, session_id: str) -> List[str]:
        """
        Get all event_ids in a session (in creation order).

        Returns:
            list of event_id (in insertion order)
        """
        validate_session_id(session_id, allow_none=False)
        return list(self._index_by_session.get(session_id, []))

    def get_events_by_correlation(self, correlation_id: str) -> List[str]:
        """
        Get all event_ids in a narrative correlation group (in creation order).

        Returns:
            list of event_id (in insertion order)
        """
        validate_correlation_id(correlation_id, allow_none=False)
        return list(self._index_by_correlation.get(correlation_id, []))

    def get_children(self, parent_event_id: str) -> List[str]:
        """
        Get all event_ids whose parent is parent_event_id (in creation order).

        Returns:
            list of event_id (in insertion order)
        """
        validate_parent_event_id(parent_event_id, allow_none=False)
        return list(self._children_by_parent.get(parent_event_id, []))

    # ─────────────────────────────────────────────────────────────
    # Observability
    # ─────────────────────────────────────────────────────────────

    def get_stats(self) -> InnerLifeWriterStats:
        """Get observability counters snapshot."""
        # Recompute dynamic stats
        self._stats.distinct_sessions = len(self._index_by_session)
        self._stats.distinct_correlations = len(self._index_by_correlation)
        self._stats.lineage_chains = len(self._children_by_parent)
        return self._stats

    def get_known_event_count(self) -> int:
        """Total known event_ids (per-instance)."""
        return len(self._known_event_ids)

    # ─────────────────────────────────────────────────────────────
    # Internal: instance state management
    # ─────────────────────────────────────────────────────────────

    def _register_event(self, event: InnerLifeEvent) -> None:
        """
        Register a newly-created event in per-instance indexes.

        Internal use only. Called from create_event() after event validation.
        """
        self._events[event.event_id] = event
        self._known_event_ids.add(event.event_id)
        if event.session_id is not None:
            self._index_by_session.setdefault(
                event.session_id, []
            ).append(event.event_id)
        if event.correlation_id is not None:
            self._index_by_correlation.setdefault(
                event.correlation_id, []
            ).append(event.event_id)
        if event.parent_event_id is not None:
            self._children_by_parent.setdefault(
                event.parent_event_id, []
            ).append(event.event_id)
