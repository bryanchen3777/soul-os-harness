"""
src/social/producer.py — SI-2.1 防線 2 生產端: SocialWorldEvent Producer

工單: OQ5-SI21-WIRING-1（SI-2.1 三道防線歷史遺留缺口接線）
契約: docs/SOCIAL-DIFFUSION-CONTRACT.md（SI-2.1, 2026-09-03）
  - §5.1 位置: 「SocialWorldEvent 發布端（Producer 側），在 ``bus.publish()`` 之前」
  - §5.1 判定表（fail-closed）: 1:1 私聊 DM → 攔截; lounge/soul_wall（group）→ 允許;
        顯式公開 → 允許; **無法判定頻道性質 → 拒絕（不廣播）**
  - §5.2 實作載體: ``SocialEventProducerGate``（``src/social/producer_gate.py``）
  - §7   端到端資料流第 1~3 步:
        [Agent 在公共頻道發言] → [防線 2] ProducerGate.evaluate → [Producer] 發布
        SOCIAL_WORLD_EVENT
  - payload 契約: ``src/eventbus/schema.py:337-355``
        source=管道發送者, actor_id=行為主體, target="broadcast",
        priority=EventPriority.LOW（低刺激度, 防線 1）, payload.priority=0

本模組的定位（**只補接線, 不改判定**）:
  - 判定語意 100% 由既有 ``SocialEventProducerGate`` 承載 → 本模組 0 改判定。
  - payload 契約驗證 100% 由既有 ``validate_social_world_event`` 承載（fail-closed）。
  - **只有 gate 判定 allowed 才 publish；其餘一律不 publish**（fail-closed,
    絕不靜默放行）。
  - 本模組**不新增** EventType（SOCIAL_WORLD_EVENT 是 SI-2.2 既有的 additive 枚舉,
    ``src/eventbus/schema.py:61``）、**不新增定時器**、**不新增網路監聽**。

為什麼需要「頻道性質」的 fail-closed 解析（``resolve_channel_from_agent_speak``）:
  SI-2.1 §5.2 說 ProducerGate 直接消費既有 I/O 層信號（``mode`` / ``is_private``）,
  不另建頻道模型。生產端唯一同時持有「最終文本 + 頻道信號」的點是 AGENT_SPEAK。
  但 AGENT_SPEAK 的 ``mode`` 有一個**結構性陷阱**:
  ``src/llm/proxy.py:3576`` 是 ``mode = event.payload.get("mode", "group")`` ——
  缺值會被**預設為 "group"（公開）**。實際上游存在「無 mode 但定向 Bryan 1:1 TG」
  的路徑（``scripts/run_server.py`` 的 admin 測試端點 ``spawn_cold_intents`` /
  ``spawn_intent`` 呼叫 ``_fire_intent`` 時不傳 mode → 預設 "group"，
  而 chrono_payload 帶 ``target_channel="telegram"`` + ``target_user_id=<Bry chat_id>``）。
  若直接以 ``mode == "group"`` 判定公開, 會把**與 Bryan 的 1:1 私聊內容廣播成公開
  社交事件** —— 正是 SI-2.1 §5.1 與 SG-3 INV-9 的紅線。
  故本模組採「**正證據才放行 + 矛盾訊號 fail-closed**」:
    - ``mode == "private"``                    → (private, dm)      → gate BLOCK
    - ``mode`` 缺失 / 未知                      → (None/未知, None)  → gate BLOCK
    - ``mode == "group"`` 但同時定向 telegram 收件人（矛盾）→ BLOCK
      （依 §5.1 最後一行「無法判定頻道性質 → fail-closed 拒絕」）
    - ``mode == "group"`` 且無定向私聊訊號        → (group, lounge)   → gate ALLOW
  這是**接線層的判定輸入組裝**, 不是防線判定本身; gate 的判定邏輯一字未動。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from src.eventbus.schema import EventPriority, EventType, SoulEvent

from .producer_gate import MODE_GROUP, MODE_PRIVATE, SocialEventProducerGate
from .schema import CONTENT_MAX_CHARS, SPACE_LOUNGE, VISIBILITY_PUBLIC
from .validation import (
    SocialWorldEventValidationError,
    validate_social_world_event,
)

logger = logging.getLogger("soul_os.social.producer")

# 私聊頻道標識（對齊 SI-2.1 §5.2 的 channel 值域 "lounge" | "soul_wall" | "dm"）。
CHANNEL_DM = "dm"

# AGENT_SPEAK 的定向私聊通道標識（既有值, 不新增模型）。
_TARGET_CHANNEL_TELEGRAM = "telegram"

# reason → event_type（SI-2.1 §3.5 v1 白名單內取值, 未知一律 "activity"）。
_REASON_TO_EVENT_TYPE: Dict[str, str] = {
    "user_message": "reply",       # 回覆他人（公共頻道對話）
    "proactive_dm": "greeting",    # 主動開場
}
_DEFAULT_EVENT_TYPE = "activity"

_NOVELTY_SANITIZE_RE = re.compile(r"[^a-z0-9_]")


def resolve_channel_from_agent_speak(
    payload: Optional[Dict[str, Any]],
) -> Tuple[Optional[str], Optional[str]]:
    """把 AGENT_SPEAK payload 的既有頻道信號解析成 ProducerGate 的輸入。

    回傳 ``(channel_mode, channel)``:
      - ``("private", "dm")``    → 1:1 私聊 DM（gate 會 BLOCK）
      - ``("group", "lounge")``  → 公共頻道（gate 會 ALLOW）
      - ``(None, None)`` / 其他  → 頻道性質無法判定（gate fail-closed BLOCK）

    **刻意不對 ``mode`` 套任何預設值** —— 缺值必須維持「無法判定」語意,
    否則會繼承 ``src/llm/proxy.py:3576`` 的 ``"group"`` 預設而變成 fail-open。
    """
    if not isinstance(payload, dict):
        return (None, None)

    mode = payload.get("mode")
    if mode == MODE_PRIVATE:
        return (MODE_PRIVATE, CHANNEL_DM)
    if mode != MODE_GROUP:
        # 缺失 / 未知 mode → 無法判定 → gate fail-closed BLOCK
        return (mode if isinstance(mode, str) else None, None)

    # mode == "group": 排除「宣稱公開但定向特定私聊收件人」的矛盾訊號。
    # 依 SI-2.1 §5.1 最後一行（無法判定頻道性質 → fail-closed 拒絕）。
    if (
        payload.get("target_channel") == _TARGET_CHANNEL_TELEGRAM
        and payload.get("target_user_id")
    ):
        logger.info(
            "[SocialProducer] 矛盾頻道訊號 (mode=group 但定向 telegram 收件人) — "
            "頻道性質無法判定 → fail-closed BLOCK"
        )
        return (None, None)

    return (MODE_GROUP, SPACE_LOUNGE)


def _build_novelty_id(
    space_id: str, actor_id: str, source_event_id: Optional[str],
) -> str:
    """組 novelty_id（既有規則 ``[a-z0-9_]{4,128}``, 復用 world/validation）。"""
    raw = f"social_{space_id}_{actor_id}_{source_event_id or ''}"
    return _NOVELTY_SANITIZE_RE.sub("", raw.lower())[:128]


def _event_type_for_reason(reason: Any) -> str:
    """reason → SI-2.1 §3.5 v1 白名單 event_type（未知 → "activity"）。"""
    if isinstance(reason, str):
        return _REASON_TO_EVENT_TYPE.get(reason, _DEFAULT_EVENT_TYPE)
    return _DEFAULT_EVENT_TYPE


class SocialWorldEventProducer:
    """SI-2.1 §5.1 的 SocialWorldEvent 發布端（Producer 側）。

    用法（production wiring, ``scripts/run_server.py``）::

        producer = SocialWorldEventProducer(
            bus=bus, gate=SocialEventProducerGate(),
        )
        bus.subscribe(
            subscriber_id="social_world_event_producer",
            handler=producer.on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )

    Fail-closed 保證:
      - ``gate is None`` → 建構即 raise（不允許「沒有守門人」的 producer 存在）。
      - ``enabled=False`` → 一律不 publish（回 None）。
      - gate BLOCK / payload 驗證失敗 / 任何內部例外 → 不 publish（回 None）。
    """

    def __init__(self, *, bus: Any, gate: SocialEventProducerGate,
                 enabled: bool = True) -> None:
        if gate is None:
            raise ValueError(
                "gate 必填 (SocialEventProducerGate) — 沒有守門人的 producer "
                "一律拒絕建構 (fail-closed, 不得靜默放行)"
            )
        if bus is None:
            raise ValueError("bus 必填 (canonical SoulEventBus)")
        self._bus = bus
        self._gate = gate
        self.enabled = bool(enabled)
        self._stats = {
            "attempted": 0,            # emit() 被呼叫次數
            "published": 0,            # 實際 publish 次數
            "blocked": 0,              # gate BLOCK 次數
            "validation_rejected": 0,  # payload 驗證失敗次數
            "skipped": 0,              # 事件本身不符合生產條件（stub/空文本/dry_run/停用）
            "failed": 0,               # 內部例外（fail-closed, 不 publish）
        }

    # ── 事件入口（唯一生產點）──────────────────────────────

    async def on_agent_speak(self, event: Any) -> None:
        """AGENT_SPEAK → （gate 通過才）publish SOCIAL_WORLD_EVENT。

        只處理「角色在公共頻道發言」; 其餘（stub / 空文本 / dry_run /
        無 actor_id）一律 skip（不嘗試、不 publish）。
        """
        if not self.enabled:
            self._stats["skipped"] += 1
            return

        payload = getattr(event, "payload", None)
        if not isinstance(payload, dict):
            self._stats["skipped"] += 1
            return

        # stub（proxy 補發的空白事件）與 dry_run（測試觸發）不得成為社交事件。
        if payload.get("is_stub") or payload.get("dry_run"):
            self._stats["skipped"] += 1
            return

        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            self._stats["skipped"] += 1
            return

        actor_id = payload.get("agent_id") or getattr(event, "source", None)
        if not isinstance(actor_id, str) or not actor_id.strip():
            # 身份不明 → fail-closed（不 publish）
            self._stats["skipped"] += 1
            logger.warning(
                "[SocialProducer] AGENT_SPEAK 缺 actor_id — fail-closed skip "
                "(不 publish)"
            )
            return

        channel_mode, channel = resolve_channel_from_agent_speak(payload)
        await self.emit(
            actor_id=actor_id.strip(),
            channel_mode=channel_mode,
            channel=channel,
            content=text,
            event_type=_event_type_for_reason(payload.get("reason")),
            source_event_id=getattr(event, "event_id", None),
        )

    # ── 發布（gate 前置, fail-closed）──────────────────────

    async def emit(
        self,
        *,
        actor_id: str,
        channel_mode: Optional[str],
        channel: Optional[str],
        content: str,
        event_type: str = _DEFAULT_EVENT_TYPE,
        explicit_public: bool = False,
        space_id: Optional[str] = None,
        source_event_id: Optional[str] = None,
        ts: Optional[str] = None,
    ) -> Optional[SoulEvent]:
        """判定 → 組 payload → 驗證 → publish。任一關失敗即回 None（不 publish）。

        ``space_id``: 事件發生空間（SI-2.1 §3.3 白名單 "lounge" | "soul_wall"）。
        缺省時沿用 ``channel``。注意 ``channel`` 值域含 "dm"（私聊），而 "dm"
        **不是**合法 space_id → 若不顯式給 space_id 而 channel="dm"，payload 驗證
        會 fail-closed 拒絕（正確方向: 寧可不發, 不得捏造空間）。
        """
        self._stats["attempted"] += 1

        if not self.enabled:
            self._stats["skipped"] += 1
            return None

        # 1. 防線 2 判定（既有 gate, 0 改判定語意）
        verdict = self._gate.evaluate(
            channel_mode=channel_mode,
            channel=channel,
            explicit_public=explicit_public,
        )
        if not verdict.allowed:
            self._stats["blocked"] += 1
            logger.info(
                f"[SocialProducer] BLOCK (防線 2): actor={actor_id!r} "
                f"channel_mode={channel_mode!r} channel={channel!r} — {verdict.reason}"
            )
            return None

        # 2. 組 payload（違反契約的內容決不 publish）
        if not isinstance(content, str) or not content.strip():
            self._stats["validation_rejected"] += 1
            logger.warning("[SocialProducer] 空 content — fail-closed 不 publish")
            return None
        body = content.strip()[:CONTENT_MAX_CHARS]
        space = space_id if space_id is not None else channel
        payload: Dict[str, Any] = {
            "source": "social",
            "type": event_type,
            "actor_id": actor_id,
            "space_id": space,
            "visibility": VISIBILITY_PUBLIC,
            "event_type": event_type,
            "content": body,
            "novelty_id": _build_novelty_id(str(space), actor_id, source_event_id),
            "ts": ts or datetime.now(timezone.utc).isoformat(),
            "summary": f"{actor_id} 在 {space} 的社交動態（{event_type}）",
            "data": {},
            "priority": 0,
        }

        # 3. 契約驗證（既有薄驗證器, fail-closed）
        try:
            validate_social_world_event(payload)
        except SocialWorldEventValidationError as exc:
            self._stats["validation_rejected"] += 1
            logger.warning(
                f"[SocialProducer] payload 驗證失敗 — fail-closed 不 publish: {exc}"
            )
            return None

        # 4. publish（target=broadcast, priority=LOW; 對齊 schema.py:337-341）
        event = SoulEvent(
            event_type=EventType.SOCIAL_WORLD_EVENT,
            source=actor_id,
            target="broadcast",
            priority=EventPriority.LOW,
            actor_id=actor_id,
            payload=payload,
        )
        try:
            await self._bus.publish(event)
        except Exception as exc:  # noqa: BLE001 — 失敗隔離: 絕不影響 AGENT_SPEAK 主路徑
            self._stats["failed"] += 1
            logger.warning(
                f"[SocialProducer] publish 失敗 (不影響主路徑): "
                f"{type(exc).__name__}: {exc}"
            )
            return None

        self._stats["published"] += 1
        logger.info(
            f"[SocialProducer] publish SOCIAL_WORLD_EVENT ✓ actor={actor_id} "
            f"space={space} event_type={event_type} "
            f"visibility={VISIBILITY_PUBLIC} content_len={len(body)}"
        )
        return event

    def get_stats(self) -> Dict[str, int]:
        """Observability counters."""
        return dict(self._stats)


__all__ = [
    "CHANNEL_DM",
    "SocialWorldEventProducer",
    "resolve_channel_from_agent_speak",
]
