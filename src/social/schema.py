"""
src/social/schema.py — SI-2.1 Social Diffusion Contract 最小 Schema

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, 已锁定)

本模块提供:
- 枚举常量: SPACE_* / VISIBILITY_* / SOCIAL_EVENT_TYPES / EXTERNAL_OTHER_ACTION
- SocialWorldEvent dataclass: 靈魂間社交事件的最小資料結構

設計原則 (SI-2.1 §2):
- Additive 優先: 全部新增, 既有語意 0 變更
- Fail-closed: 無法判定 → 拒絕
- 防線 3 最高優先: 身份認知防污染是不可妥協的絕對不變量

SocialWorldEvent 繼承 WorldEvent (src/world/perception.py):
- 為什麼繼承: 能直接進 WorldPerceptionState (ephemeral 容器, 0 改動) 與
  compute_scores 評分管道 (防線 1 復用既有感知管道), 同時帶社交專屬欄位。
- source 固定語意 "social" (管道標識, 對齊 WorldEvent.VALID_SOURCES 既有值);
  type = event_type (社交行為細分類), 讓既有 scoring 管道可讀。
- 不混用: WorldEvent source="social" 只模擬「Bryan 出門」這類外部社交行為,
  靈魂間社交事件一律走 SOCIAL_WORLD_EVENT (SI-2.1 §3.6)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict

from src.world.perception import WorldEvent

# ─────────────────────────────────────────────────────────────
# 1. 枚舉常量 (SI-2.1 §3.5, v1 白名單)
# ─────────────────────────────────────────────────────────────

# space_id 枚舉
SPACE_LOUNGE = "lounge"        # 客廳群聊 (公共)
SPACE_SOUL_WALL = "soul_wall"  # 靈魂牆 (公共)

# visibility 枚舉
VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"

# event_type v1 白名單 (可擴展, 未知值 fail-closed 拒絕)
SOCIAL_EVENT_TYPES = frozenset({
    "greeting",   # 打招呼 / 問候
    "share",      # 分享動態 / 想法
    "reply",      # 回覆他人
    "mood",       # 情緒表達
    "activity",   # 活動 / 行為
})

# 防線 3 標籤 (SI-2.1 §4.2): 外部他者行為, 不是本靈魂的經歷
EXTERNAL_OTHER_ACTION = "external_other_action"

# 合法 space_id 集合 (fail-closed 判定用)
VALID_SPACE_IDS = frozenset({SPACE_LOUNGE, SPACE_SOUL_WALL})

# 合法 visibility 集合
VALID_VISIBILITIES = frozenset({VISIBILITY_PUBLIC, VISIBILITY_PRIVATE})

# content / summary 長度上限 (SI-2.1 §3.4, 防超大 payload)
CONTENT_MAX_CHARS = 200
SUMMARY_MAX_CHARS = 500


# ─────────────────────────────────────────────────────────────
# 2. SocialWorldEvent (靈魂間社交事件)
# ─────────────────────────────────────────────────────────────

@dataclass
class SocialWorldEvent(WorldEvent):
    """
    靈魂間社交事件 (SI-2.1 §3.3 最小 Schema)。

    繼承 WorldEvent 的客觀事實欄位 (source/type/novelty_id/ts/summary/data/priority),
    新增社交專屬欄位:
      - actor_id:   行為主體靈魂 id (防線 3 判定依據)
      - space_id:   發生空間 ("lounge" | "soul_wall")
      - visibility: 可見性 ("public" | "private"; 到 bus 時必為 "public")
      - event_type: 社交行為細分類 (v1 白名單)
      - content:    簡明內容 (<= 200 chars)

    注意: 本 dataclass 只做最小欄位型別檢查 (薄), 完整契約驗證在
    src/social/validation.py (validate_social_world_event, fail-closed)。
    """

    # 社交專屬欄位 (必填語意由 validation 層保證; dataclass 給預設值以維持
    # dataclass 繼承順序合法 — WorldEvent 的 data/priority 已有預設值)
    actor_id: str = ""
    space_id: str = ""
    visibility: str = ""
    event_type: str = ""
    content: str = ""

    def __post_init__(self) -> None:
        # 先跑父類 (WorldEvent) 的 priority int 檢查
        super().__post_init__()
        # 薄型別檢查 (完整契約驗證在 validation.py)
        for field_name, value in (
            ("actor_id", self.actor_id),
            ("space_id", self.space_id),
            ("visibility", self.visibility),
            ("event_type", self.event_type),
            ("content", self.content),
        ):
            if not isinstance(value, str):
                raise TypeError(
                    f"SocialWorldEvent.{field_name} 必須是 str, "
                    f"得到 {type(value).__name__}"
                )

    def to_payload(self) -> Dict[str, Any]:
        """轉成 SoulEvent.payload dict (WorldEvent 欄位 + 社交欄位)。"""
        base = super().to_payload()
        base.update({
            "actor_id": self.actor_id,
            "space_id": self.space_id,
            "visibility": self.visibility,
            "event_type": self.event_type,
            "content": self.content,
        })
        return base

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "SocialWorldEvent":
        """從 SoulEvent.payload 還原 (防禦性: 非 int priority 視為 0)。"""
        priority_raw = payload.get("priority", 0)
        priority = (
            priority_raw
            if isinstance(priority_raw, int) and not isinstance(priority_raw, bool)
            else 0
        )
        return cls(
            source=payload.get("source", "social"),
            type=payload.get("type", payload.get("event_type", "")),
            novelty_id=payload["novelty_id"],
            ts=payload["ts"],
            summary=payload["summary"],
            data=payload.get("data", {}),
            priority=priority,
            actor_id=payload["actor_id"],
            space_id=payload["space_id"],
            visibility=payload["visibility"],
            event_type=payload["event_type"],
            content=payload["content"],
        )


__all__ = [
    "SPACE_LOUNGE",
    "SPACE_SOUL_WALL",
    "VISIBILITY_PUBLIC",
    "VISIBILITY_PRIVATE",
    "SOCIAL_EVENT_TYPES",
    "EXTERNAL_OTHER_ACTION",
    "VALID_SPACE_IDS",
    "VALID_VISIBILITIES",
    "CONTENT_MAX_CHARS",
    "SUMMARY_MAX_CHARS",
    "SocialWorldEvent",
]
