"""
src/inner_life/serialization.py — Inner Life Event Serialization

M5.4-5.1 (Bry 派工 2026-08-09 18:25) — Inner Life Unified Architecture Foundation
M5.15-5 (Bry 派工 2026-08-12 19:14) — WorldEvent ↔ InnerLifeEvent Identity Bridge
  Adds source_world_event_novelty_id to event_to_dict / event_from_dict (round-trip).
  Backward compat: missing field → None (per M5.4-5.1 missing-field default pattern).
  0 change to existing 8 fields' serialization.

派工 派工 acceptance criteria:
  - serialization/deserialization
  - invalid identity handling
  - backward compatibility where applicable

This module provides:
  - to_dict / from_dict for JSON-safe round-trip
  - validation on deserialization (no silent type coercion)
  - backward compat: missing fields default to None / 0 / empty
"""
from __future__ import annotations

from typing import Any, Dict

from .event import InnerLifeEvent, Provenance
from .identity import (
    IdentityValidationError,
    validate_correlation_id,
    validate_event_id,
    validate_parent_event_id,
    validate_session_id,
    validate_source_world_event_novelty_id,
    validate_ts,
)


# ─────────────────────────────────────────────────────────────────────
# Provenance serialization
# ─────────────────────────────────────────────────────────────────────

def provenance_to_dict(p: Provenance) -> Dict[str, Any]:
    """Serialize a Provenance to a JSON-safe dict."""
    return {
        "trigger_type": p.trigger_type,
        "actor_id": p.actor_id,
        "source_system": p.source_system,
        "trace_ref": p.trace_ref,
        "extras": dict(p.extras),  # 複製避免外部修改
    }


def provenance_from_dict(d: Dict[str, Any]) -> Provenance:
    """
    Deserialize a Provenance from a dict.

    Raises:
        IdentityValidationError: on invalid fields
    """
    if not isinstance(d, dict):
        raise IdentityValidationError(
            f"Provenance dict 必須是 dict, got: {type(d).__name__}"
        )
    trigger_type = d.get("trigger_type")
    if not isinstance(trigger_type, str) or not trigger_type.strip():
        raise IdentityValidationError(
            f"trigger_type 必填且為非空 str, got: {trigger_type!r}"
        )
    actor_id = d.get("actor_id")
    if actor_id is not None and not isinstance(actor_id, str):
        raise IdentityValidationError(
            f"actor_id 必須是 str 或 None, got: {type(actor_id).__name__}"
        )
    source_system = d.get("source_system", "narrative")
    if not isinstance(source_system, str):
        raise IdentityValidationError(
            f"source_system 必須是 str, got: {type(source_system).__name__}"
        )
    trace_ref = d.get("trace_ref")
    if trace_ref is not None and not isinstance(trace_ref, str):
        raise IdentityValidationError(
            f"trace_ref 必須是 str 或 None, got: {type(trace_ref).__name__}"
        )
    extras = d.get("extras", {})
    if not isinstance(extras, dict):
        raise IdentityValidationError(
            f"extras 必須是 dict, got: {type(extras).__name__}"
        )
    return Provenance(
        trigger_type=trigger_type,
        actor_id=actor_id,
        source_system=source_system,
        trace_ref=trace_ref,
        extras=extras,
    )


# ─────────────────────────────────────────────────────────────────────
# InnerLifeEvent serialization
# ─────────────────────────────────────────────────────────────────────

def event_to_dict(event: InnerLifeEvent) -> Dict[str, Any]:
    """
    Serialize an InnerLifeEvent to a JSON-safe dict.

    Format:
      {
        "event_id": "32hex",
        "session_id": str | None,
        "correlation_id": str | None,
        "parent_event_id": "32hex" | None,
        "ts": "ISO 8601 UTC",
        "provenance": {...},
        "lineage_depth": int,
        "lineage_path": str,
        "source_world_event_novelty_id": str | None,  # M5.15-5
      }
    """
    return {
        "event_id": event.event_id,
        "session_id": event.session_id,
        "correlation_id": event.correlation_id,
        "parent_event_id": event.parent_event_id,
        "ts": event.ts,
        "provenance": provenance_to_dict(event.provenance),
        "lineage_depth": event.lineage_depth,
        "lineage_path": event.lineage_path,
        # M5.15-5: cross-system causality (Layer 1), free string or None
        "source_world_event_novelty_id": event.source_world_event_novelty_id,
    }


def event_from_dict(d: Dict[str, Any]) -> InnerLifeEvent:
    """
    Deserialize an InnerLifeEvent from a dict.

    Backward compatibility:
      - Missing lineage_depth → 0 (default)
      - Missing lineage_path → "" (default)
      - Missing session_id/correlation_id/parent_event_id → None
      - Missing source_world_event_novelty_id (M5.15-5) → None (default)
        (old payloads without this field deserialize correctly)

    Raises:
        IdentityValidationError: on invalid fields
    """
    if not isinstance(d, dict):
        raise IdentityValidationError(
            f"event dict 必須是 dict, got: {type(d).__name__}"
        )

    # 必填欄位
    event_id = validate_event_id(d.get("event_id"))
    ts = validate_ts(d.get("ts"))
    provenance = provenance_from_dict(d.get("provenance", {}))

    # Optional 欄位 (向後相容: 沒有就 None)
    session_id = validate_session_id(d.get("session_id"))
    correlation_id = validate_correlation_id(d.get("correlation_id"))
    parent_event_id = validate_parent_event_id(d.get("parent_event_id"))
    # M5.15-5: new field, missing → None (backward compat with old payloads)
    source_world_event_novelty_id = validate_source_world_event_novelty_id(
        d.get("source_world_event_novelty_id")
    )

    # 衍生欄位 (lineage_depth + lineage_path 可能在舊 payload 缺)
    # 派工: "backward compatibility where applicable"
    # 若 lineage_path 已有 (新格式), 直接用
    # 若沒有, 從 parent_event_id 推算 (但這需要 instance 資訊, 在這裡
    # 不做 — deserializer 只保證欄位齊全, lineage 一致性是 instance 的責任)
    lineage_depth = d.get("lineage_depth", 0)
    if not isinstance(lineage_depth, int) or lineage_depth < 0:
        raise IdentityValidationError(
            f"lineage_depth 必須是 ≥ 0 的 int, got: {lineage_depth}"
        )
    lineage_path = d.get("lineage_path", "")
    if not isinstance(lineage_path, str):
        raise IdentityValidationError(
            f"lineage_path 必須是 str, got: {type(lineage_path).__name__}"
        )

    return InnerLifeEvent(
        event_id=event_id,
        session_id=session_id,
        correlation_id=correlation_id,
        parent_event_id=parent_event_id,
        ts=ts,
        provenance=provenance,
        lineage_depth=lineage_depth,
        lineage_path=lineage_path,
        # M5.15-5: pass through cross-system causality
        source_world_event_novelty_id=source_world_event_novelty_id,
    )
