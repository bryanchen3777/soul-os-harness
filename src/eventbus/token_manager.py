"""
speaker_token_manager.py
Soul OS — Phase 4: 發言權仲裁器

設計：
  - 同一時間只有一個 Agent 持有 token
  - 訂閱 AGENT_INTENT_ENRICHED（申請）與 AGENT_SPEAK（釋放）
  - 授權後 re-publish 為 SPEAKER_TOKEN_GRANTED（新 event type 避免 re-publish 迴圈）
  - queue 容量 maxsize=100，滿了丟新進來的（推薦選項 1-C）
  - 持有超時自動強制釋放（推薦選項 2-A，同時 emit SPEAKER_TOKEN_RELEASED）
  - 非 holder 的 AGENT_SPEAK 靜默忽略（推薦選項 3-A）
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from .bus import SoulEventBus
from .schema import EventType, SoulEvent

logger = logging.getLogger("soul_os.token_manager")


class SpeakerTokenManager:
    """
    發言權仲裁器。

    事件流：
      AGENT_INTENT_ENRICHED
        → 授予（若無 holder）或 queue
        → 授予時 re-publish: SPEAKER_TOKEN_GRANTED
          → LLMProxy 收到、生產、發 AGENT_SPEAK
            → 我收到 AGENT_SPEAK、釋放 token、處理 queue
    """

    QUEUE_MAXSIZE = 100

    def __init__(
        self,
        bus: SoulEventBus,
        token_timeout_secs: float = 10.0,
    ):
        self.bus = bus
        self.timeout = token_timeout_secs
        self._holder: Optional[str] = None
        self._queue: deque = deque(maxlen=self.QUEUE_MAXSIZE)
        self._held_since: Optional[datetime] = None
        self._grants = 0
        self._releases = 0
        self._timeouts = 0
        self._queue_drops = 0

    def register(self) -> None:
        """向 Event Bus 註冊，開始仲裁。"""
        self.bus.subscribe(
            subscriber_id="speaker_token_manager",
            handler=self.handle_event,
            event_filter={
                EventType.AGENT_INTENT_ENRICHED,  # 只訂閱已 enrichment 的版本
                EventType.AGENT_SPEAK,
            },
        )
        logger.info("[Token] 發言權仲裁器已上線 ✓")

    # ── 事件分派 ─────────────────────────────────────────────

    async def handle_event(self, event: SoulEvent) -> None:
        if event.event_type in (EventType.AGENT_INTENT, EventType.AGENT_INTENT_ENRICHED):
            await self._request_token(event)
        elif event.event_type == EventType.AGENT_SPEAK:
            speaker = event.payload.get("agent_id", event.source)
            await self._release_token(speaker, reason="spoke_done")

    # ── 申請 token ─────────────────────────────────────────────

    async def _request_token(self, intent_event: SoulEvent) -> None:
        agent_id = intent_event.payload.get("agent_id")
        if not agent_id:
            logger.warning("[Token] AGENT_INTENT_ENRICHED 缺 agent_id，略過")
            return

        logger.info(f"[Token] _request_token: agent_id={agent_id} holder={self._holder}")
        # 1. 檢查 timeout（防 holder 死鎖）
        if self._holder is not None and self._held_since is not None:
            held_secs = (
                datetime.now(timezone.utc) - self._held_since
            ).total_seconds()
            if held_secs > self.timeout:
                logger.warning(
                    f"[Token] {self._holder} 持有 {held_secs:.1f}s 超時，強制釋放"
                )
                await self._force_release(reason="timeout")

        # 2. 授予或排隊
        if self._holder is None:
            self._grant(agent_id)
            # re-publish 為 SPEAKER_TOKEN_GRANTED（避免迴圈用新 event type）
            # 🔴 Bug 1 fix: 明確寫入 agent_id，確保不被舊 event.source 覆蓋
            enriched_payload = {**intent_event.payload, "agent_id": agent_id}
            await self.bus.publish(
                SoulEvent(
                    event_type=EventType.SPEAKER_TOKEN_GRANTED,
                    source=agent_id,  # ← 用 agent_id，不用 intent_event.source
                    target=intent_event.target,
                    priority=intent_event.priority,
                    payload=enriched_payload,
                    session_id=intent_event.session_id,
                    correlation_id=intent_event.correlation_id,
                )
            )
        else:
            # 1-C：queue 滿了丟新進來的
            if len(self._queue) >= self.QUEUE_MAXSIZE:
                self._queue_drops += 1
                logger.warning(
                    f"[Token] queue 滿了（{len(self._queue)}），"
                    f"丟棄 {agent_id} 的請求"
                )
                return
            self._queue.append(intent_event)
            logger.info(
                f"[Token] {agent_id} 排隊（holder={self._holder}, "
                f"queue={len(self._queue)}）"
            )

    def _grant(self, agent_id: str) -> None:
        self._holder = agent_id
        self._held_since = datetime.now(timezone.utc)
        self._grants += 1
        logger.info(f"[Token] 授予 {agent_id}")

    # ── 釋放 token ─────────────────────────────────────────────

    async def _release_token(self, agent_id: str, reason: str = "spoke_done") -> None:
        # 3-A：非 holder 的 AGENT_SPEAK 靜默忽略
        if self._holder != agent_id:
            logger.debug(
                f"[Token] {agent_id} 不是 holder（current={self._holder}），忽略"
            )
            return
        await self._force_release(reason=reason)

    async def _force_release(self, reason: str) -> None:
        """強制釋放：發 SPEAKER_TOKEN_RELEASED、處理 queue 下一位。"""
        released_holder = self._holder
        self._holder = None
        self._held_since = None
        self._releases += 1
        if reason == "timeout":
            self._timeouts += 1

        # 2-A：emit SPEAKER_TOKEN_RELEASED（透明、debug 友善）
        next_holder: Optional[str] = None
        if self._queue:
            next_intent = self._queue.popleft()
            next_holder = next_intent.payload.get("agent_id")
            # 直接授予下一位（避免 re-publish 迴圈）
            self._grant(next_holder)
            # 重新包裝成 SPEAKER_TOKEN_GRANTED 發出去
            await self.bus.publish(
                SoulEvent(
                    event_type=EventType.SPEAKER_TOKEN_GRANTED,
                    source=next_intent.source,
                    target=next_intent.target,
                    priority=next_intent.priority,
                    payload=next_intent.payload,
                    session_id=next_intent.session_id,
                    correlation_id=next_intent.correlation_id,
                )
            )

        await self.bus.publish(
            SoulEvent(
                event_type=EventType.SPEAKER_TOKEN_RELEASED,
                source="speaker_token_manager",
                target="broadcast",
                payload={
                    "agent_id": released_holder,
                    "reason": reason,
                    "next_holder": next_holder,
                },
            )
        )
        logger.info(
            f"[Token] {released_holder} 釋放（reason={reason}, "
            f"next={next_holder}）"
        )

    # ── 觀察者用 ─────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            "current_holder": self._holder,
            "queue_size": len(self._queue),
            "grants": self._grants,
            "releases": self._releases,
            "timeouts": self._timeouts,
            "queue_drops": self._queue_drops,
        }
