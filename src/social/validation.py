"""
src/social/validation.py — SI-2.1 Social Diffusion Contract 薄驗證器

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03)

SocialWorldEvent 薄 input validation (SI-2.1 §6.2):
- 欄位必填 / space_id 白名單 / visibility 白名單 / event_type 白名單 /
  ts UTC / novelty_id 格式 / content<=200 / summary<=500 / priority int

Invalid event → reject → trace → no context → no memory (fail-closed)。
復用 src/world/validation.py 的 _NOVELTY_ID_RE / _validate_timestamp 規則 (0 改動)。
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from .schema import (
    CONTENT_MAX_CHARS,
    SOCIAL_EVENT_TYPES,
    SUMMARY_MAX_CHARS,
    VALID_SPACE_IDS,
    VALID_VISIBILITIES,
    VISIBILITY_PRIVATE,
    SocialWorldEvent,
)
from src.world.validation import (
    WorldEventValidationError,
    _validate_novelty_id,
    _validate_timestamp,
)

logger = logging.getLogger("soul_os.social.validation")


class SocialWorldEventValidationError(ValueError):
    """Raised when SocialWorldEvent fails thin validation. Caller should reject + trace."""
    pass


def validate_social_world_event(payload: Dict[str, Any]) -> SocialWorldEvent:
    """
    薄 input validation: 通過後回 SocialWorldEvent, 失敗 raise
    SocialWorldEventValidationError (fail-closed)。

    SI-2.1 §3.4 必查:
      1. payload 是 dict
      2. 必填欄位: actor_id / space_id / visibility / event_type / content /
         novelty_id / ts / summary
      3. space_id 白名單 (lounge | soul_wall), 未知值 fail-closed 拒絕
      4. visibility 白名單 (public | private)
      5. event_type v1 白名單, 未知值 fail-closed 拒絕
      6. content <= 200 chars (防超大 payload)
      7. summary <= 500 chars
      8. ts ISO 8601 UTC (復用 _validate_timestamp)
      9. novelty_id 格式 (復用 _validate_novelty_id)
      10. priority 必須是 int (拒絕 str/float/bool, 預設 0)
      11. data 必須是 dict (預設 {})
    """
    if not isinstance(payload, dict):
        raise SocialWorldEventValidationError(
            f"payload 必須是 dict, got: {type(payload).__name__}"
        )

    # ── 1. 必填欄位
    for required in (
        "actor_id", "space_id", "visibility", "event_type",
        "content", "novelty_id", "ts", "summary",
    ):
        if required not in payload:
            raise SocialWorldEventValidationError(f"缺必填欄位: {required!r}")

    # ── 2. actor_id 非空 str
    actor_id = payload["actor_id"]
    if not isinstance(actor_id, str) or not actor_id.strip():
        raise SocialWorldEventValidationError(
            f"actor_id 必填且為非空 str, got: {actor_id!r}"
        )

    # ── 3. space_id 白名單 (未知值 fail-closed)
    space_id = payload["space_id"]
    if space_id not in VALID_SPACE_IDS:
        raise SocialWorldEventValidationError(
            f"space_id {space_id!r} 不在白名單 {sorted(VALID_SPACE_IDS)} — fail-closed"
        )

    # ── 4. visibility 白名單
    visibility = payload["visibility"]
    if visibility not in VALID_VISIBILITIES:
        raise SocialWorldEventValidationError(
            f"visibility {visibility!r} 不在白名單 {sorted(VALID_VISIBILITIES)}"
        )

    # ── 5. event_type v1 白名單 (未知值 fail-closed)
    event_type = payload["event_type"]
    if event_type not in SOCIAL_EVENT_TYPES:
        raise SocialWorldEventValidationError(
            f"event_type {event_type!r} 不在 v1 白名單 {sorted(SOCIAL_EVENT_TYPES)} "
            f"— fail-closed"
        )

    # ── 6. content <= 200 chars
    content = payload["content"]
    if not isinstance(content, str) or not content.strip():
        raise SocialWorldEventValidationError(
            f"content 必填且為非空 str, got: {content!r}"
        )
    if len(content) > CONTENT_MAX_CHARS:
        raise SocialWorldEventValidationError(
            f"content 太長 ({len(content)} chars, 上限 {CONTENT_MAX_CHARS})"
        )

    # ── 7. summary <= 500 chars
    summary = payload["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise SocialWorldEventValidationError(
            f"summary 必填且為非空 str, got: {summary!r}"
        )
    if len(summary) > SUMMARY_MAX_CHARS:
        raise SocialWorldEventValidationError(
            f"summary 太長 ({len(summary)} chars, 上限 {SUMMARY_MAX_CHARS})"
        )

    # ── 8. ts ISO 8601 UTC (復用既有規則, 異常轉成 social 異常)
    try:
        _validate_timestamp(payload["ts"])
    except WorldEventValidationError as e:
        raise SocialWorldEventValidationError(str(e)) from e

    # ── 9. novelty_id 格式 (復用既有規則, 異常轉成 social 異常)
    try:
        normalized_nid = _validate_novelty_id(payload["novelty_id"])
    except WorldEventValidationError as e:
        raise SocialWorldEventValidationError(str(e)) from e

    # ── 10. priority 必須是 int (拒絕 str/float/bool, 預設 0)
    priority_raw = payload.get("priority", 0)
    if not isinstance(priority_raw, int) or isinstance(priority_raw, bool):
        raise SocialWorldEventValidationError(
            f"priority 必須是 int, got: {type(priority_raw).__name__} "
            f"(拒絕 str/float/bool, SI-2.1 §3.4)"
        )
    priority = priority_raw

    # ── 11. data 必須是 dict (預設 {})
    data = payload.get("data", {})
    if not isinstance(data, dict):
        raise SocialWorldEventValidationError(
            f"data 必須是 dict, got: {type(data).__name__}"
        )

    return SocialWorldEvent(
        source=payload.get("source", "social"),
        type=event_type,
        novelty_id=normalized_nid,
        ts=payload["ts"],
        summary=summary.strip(),
        data=data,
        priority=priority,
        actor_id=actor_id.strip(),
        space_id=space_id,
        visibility=visibility,
        event_type=event_type,
        content=content.strip(),
    )


def is_private_on_bus(payload: Dict[str, Any]) -> bool:
    """
    SI-2.1 §3.4: visibility=private 出現在 bus 上 = 契約違例 (防線 2 已把 private
    攔截在廣播總線之外)。訂閱端 fail-closed 丟棄。

    回 True 表示該 payload 是 private (訂閱端應丟棄)。
    """
    return payload.get("visibility") == VISIBILITY_PRIVATE


__all__ = [
    "SocialWorldEventValidationError",
    "validate_social_world_event",
    "is_private_on_bus",
]
