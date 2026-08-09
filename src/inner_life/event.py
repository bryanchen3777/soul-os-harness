"""
src/inner_life/event.py — Inner Life Event & Provenance Dataclasses

M5.4-5.1 (Bry 派工 2026-08-09 18:25) — Inner Life Unified Architecture Foundation

派工精神:
  - canonical event model, not just fields
  - each dimension has explicit semantic meaning
  - identity 跟 lineage 是 foundation, 不是 wrapper

The Inner Life Event is the CANONICAL IDENTITY AUTHORITY for narrative
events that downstream systems (Memory, Diary, Dream, future) reference.

This is NOT a row in a DB. NOT a class to be persisted directly.
It's the SEMANTIC MODEL that future工單's persistence layers will
inherit / extend.

派工 6 個 concept dimensions:
  1. event_id: unique identity
  2. session_id: runtime session anchor
  3. correlation_id: narrative group (NOT causation)
  4. parent_event_id: causation chain (tree)
  5. provenance: structured WHO/WHAT/WHERE/WHY
  6. lineage: depth + path (denormalized)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .identity import (
    validate_correlation_id,
    validate_event_id,
    validate_parent_event_id,
    validate_session_id,
    validate_ts,
    IdentityValidationError,
)


# ─────────────────────────────────────────────────────────────────────
# Provenance — 結構化 WHO/WHAT/WHERE/WHY
# ─────────────────────────────────────────────────────────────────────

# 派工 派工: trigger_type 應該是 canonical vocabulary, 不是 raw Memory source / Diary slot
# 提供 namespace-style trigger type: "<system>:<type>"
# - "user_message"     → Bry 跟 agent 對話 (USER_MESSAGE event)
# - "agent_reply"      → agent 回應 (AGENT_SPEAK event)
# - "diary:morning"    → morning slot diary write
# - "diary:night"      → night slot diary write
# - "dream:dream"      → dream event
# - "dream:event"      → random event
# - "memory_fact"      → LLM judge 抽出的 fact
# - "system"           → 系統自動事件 (heartbeat 等)

TRIGGER_TYPE_USER_MESSAGE = "user_message"
TRIGGER_TYPE_AGENT_REPLY = "agent_reply"
TRIGGER_TYPE_DIARY_MORNING = "diary:morning"
TRIGGER_TYPE_DIARY_NIGHT = "diary:night"
TRIGGER_TYPE_DREAM_DREAM = "dream:dream"
TRIGGER_TYPE_DREAM_EVENT = "dream:event"
TRIGGER_TYPE_MEMORY_FACT = "memory_fact"
TRIGGER_TYPE_SYSTEM = "system"

VALID_SOURCE_SYSTEMS = frozenset({"memory", "diary", "dream", "narrative", "system"})


@dataclass(frozen=True)
class Provenance:
    """
    Canonical record of WHO/WHAT/WHERE/WHY produced an Inner Life event.

    派工 派工 semantic:
      - trigger_type: canonical vocabulary (NOT raw Memory.source / Diary.slot / Dream.slot)
                      這是 Inner Life 統一的 trigger namespace
      - actor_id: who caused this event (bryan / agent_rem / None for system)
      - source_system: which downstream system originated this event
      - trace_ref: optional debug/observability reference (correlation with other logs)
      - extras: extensible dict (no schema migration needed for new fields)

    frozen=True: provenance is immutable once set (immutable dataclass)
    """
    trigger_type: str
    actor_id: Optional[str] = None
    source_system: str = "narrative"
    trace_ref: Optional[str] = None
    extras: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.trigger_type, str) or not self.trigger_type.strip():
            raise IdentityValidationError(
                f"Provenance.trigger_type 必須是非空 str, got: {self.trigger_type!r}"
            )
        if self.actor_id is not None and not isinstance(self.actor_id, str):
            raise IdentityValidationError(
                f"Provenance.actor_id 必須是 str 或 None, got: {type(self.actor_id).__name__}"
            )
        if self.source_system not in VALID_SOURCE_SYSTEMS:
            raise IdentityValidationError(
                f"Provenance.source_system {self.source_system!r} 不在 {sorted(VALID_SOURCE_SYSTEMS)}"
            )
        if self.trace_ref is not None and not isinstance(self.trace_ref, str):
            raise IdentityValidationError(
                f"Provenance.trace_ref 必須是 str 或 None, got: {type(self.trace_ref).__name__}"
            )
        if not isinstance(self.extras, dict):
            raise IdentityValidationError(
                f"Provenance.extras 必須是 dict, got: {type(self.extras).__name__}"
            )
        # 所有 extras value 必須是 str (簡化序列化 / 避免複雜性)
        for k, v in self.extras.items():
            if not isinstance(v, str):
                raise IdentityValidationError(
                    f"Provenance.extras[{k!r}] 必須是 str, got: {type(v).__name__}"
                )


# ─────────────────────────────────────────────────────────────────────
# InnerLifeEvent — canonical narrative event
# ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class InnerLifeEvent:
    """
    Canonical Inner Life Event — atomic unit of lived experience.

    派工 派工 semantic (each field is a dimension, not just a field):

      1. event_id (str, 32 hex):
         - Unique identity, never re-issued
         - Globally unique within the InnerLifeWriter instance
         - Immutable

      2. session_id (Optional[str]):
         - Runtime session anchor
         - None for cross-session events
         - Multiple events can share a session

      3. correlation_id (Optional[str]):
         - Narrative group marker
         - "two events that should be considered the SAME narrative context"
         - NOT causation (use parent_event_id for that)
         - NOT a foreign key (events can have None)

      4. parent_event_id (Optional[str]):
         - Direct causation (B was caused by / derived from A)
         - Tree structure (one parent per event in v1)
         - lineage_depth + lineage_path denormalize the chain

      5. ts (str, ISO 8601 UTC):
         - Set at creation
         - Immutable
         - For cross-session events, ts is the actual occurrence time

      6. provenance (Provenance):
         - Structured WHO/WHAT/WHERE/WHY
         - See Provenance docstring

      7. lineage_depth (int):
         - 0 for root events
         - parent.lineage_depth + 1 for child events

      8. lineage_path (str):
         - Empty for root, "parent_path/own_id" otherwise
         - Denormalized for efficient query (no need to traverse)

    frozen=True: event is immutable once created. To "modify", create a new event
    that references the old one via parent_event_id.
    """
    event_id: str
    session_id: Optional[str]
    correlation_id: Optional[str]
    parent_event_id: Optional[str]
    ts: str
    provenance: Provenance
    lineage_depth: int = 0
    lineage_path: str = ""

    def __post_init__(self) -> None:
        # 驗證每個欄位 (delegated to identity.py)
        validate_event_id(self.event_id)
        validate_session_id(self.session_id)
        validate_correlation_id(self.correlation_id)
        validate_parent_event_id(self.parent_event_id)
        validate_ts(self.ts)
        if not isinstance(self.provenance, Provenance):
            raise IdentityValidationError(
                f"provenance 必須是 Provenance instance, got: {type(self.provenance).__name__}"
            )
        if not isinstance(self.lineage_depth, int) or self.lineage_depth < 0:
            raise IdentityValidationError(
                f"lineage_depth 必須是 ≥ 0 的 int, got: {self.lineage_depth}"
            )
        if not isinstance(self.lineage_path, str):
            raise IdentityValidationError(
                f"lineage_path 必須是 str, got: {type(self.lineage_path).__name__}"
            )

    def is_root(self) -> bool:
        """是否為 root event (no parent)。"""
        return self.parent_event_id is None

    def is_session_anchored(self) -> bool:
        """是否被 session anchor 綁定 (vs cross-session)。"""
        return self.session_id is not None

    def is_in_narrative(self) -> bool:
        """是否屬於某個 narrative group (vs standalone)。"""
        return self.correlation_id is not None

    def is_ancestor_of(self, other: "InnerLifeEvent") -> bool:
        """
        Check if this event is an ancestor of `other` in the lineage tree.

        Ancestor = self.event_id appears in other's lineage_path.
        """
        if other.lineage_path:
            return self.event_id in other.lineage_path.split("/")
        return False
