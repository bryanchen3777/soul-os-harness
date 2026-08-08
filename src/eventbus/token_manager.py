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

M3 Phase 1 (Bry 拍板 2026-08-07 19:40 + 2026-08-07 20:02 hardening):
  - Production 路徑: 訂閱 AGENT_INTENT_PERCEIVED 為唯一正常入口
  - Fallback: 測試 / isolated test environment (無 WorldPerceptionMiddleware) 可訂閱 ENRICHED
  - 透過 intake_event_types config 控制, production 必須顯式傳 [AGENT_INTENT_PERCEIVED]
    避免 double-process (一個 intent 走 ENRICHED + PERCEIVED 兩條路徑)
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import Iterable, Optional, Set

from .bus import SoulEventBus
from .schema import EventType, SoulEvent

logger = logging.getLogger("soul_os.token_manager")

# Bry 拍板 2026-08-07 20:02 hardening:
# Production 預設入口 — 必須是 AGENT_INTENT_PERCEIVED (經過 M3 WorldPerceptionMiddleware)
# Fallback (測試 / 無 M3 環境) 走 AGENT_INTENT_ENRICHED
# default 保留兩個 = 向後相容既有 test_e2e_full_flow 等測試
# run_server.py (production) 必須顯式傳 [AGENT_INTENT_PERCEIVED]
PRODUCTION_INTAKE_EVENT_TYPES: Set[EventType] = {EventType.AGENT_INTENT_PERCEIVED}
LEGACY_INTAKE_EVENT_TYPES: Set[EventType] = {
    EventType.AGENT_INTENT_PERCEIVED,
    EventType.AGENT_INTENT_ENRICHED,
}


class SpeakerTokenManager:
    """
    發言權仲裁器。

    事件流 (M3 Phase 1 production):
      AGENT_INTENT
        → MemoryMiddleware 注入 memory_context
        → AGENT_INTENT_ENRICHED
        → WorldPerceptionMiddleware 注入 world_context
        → AGENT_INTENT_PERCEIVED  ← 我訂閱這個 (唯一入口, 避免 double-process)
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
        # M3 Phase 1 hardening: 預設兩種都訂 (向後相容), production 應顯式傳 PRODUCTION_INTAKE_EVENT_TYPES
        intake_event_types: Optional[Iterable[EventType]] = None,
    ):
        self.bus = bus
        self.timeout = token_timeout_secs
        # 規範化為 set[EventType] (handles list, set, frozenset 傳入)
        if intake_event_types is None:
            self.intake_event_types: Set[EventType] = set(LEGACY_INTAKE_EVENT_TYPES)
        else:
            self.intake_event_types = set(intake_event_types)
        self._holder: Optional[str] = None
        self._queue: deque = deque(maxlen=self.QUEUE_MAXSIZE)
        self._held_since: Optional[datetime] = None
        self._grants = 0
        self._releases = 0
        self._timeouts = 0
        self._queue_drops = 0

    def register(self) -> None:
        """向 Event Bus 註冊，開始仲裁。"""
        # Bry 拍板 2026-08-07 20:02: event_filter 從 self.intake_event_types 拿
        # production 應只含 AGENT_INTENT_PERCEIVED (避免 double-process)
        # test / isolated env 可含 AGENT_INTENT_ENRICHED (fallback)
        event_filter = set(self.intake_event_types)
        event_filter.add(EventType.AGENT_SPEAK)  # 永遠訂 AGENT_SPEAK 釋放
        self.bus.subscribe(
            subscriber_id="speaker_token_manager",
            handler=self.handle_event,
            event_filter=event_filter,
        )
        # 觀察性: 明確記下 production vs fallback 模式
        if self.intake_event_types == PRODUCTION_INTAKE_EVENT_TYPES:
            mode = "PRODUCTION (PERCEIVED only)"
        elif self.intake_event_types == LEGACY_INTAKE_EVENT_TYPES:
            mode = "LEGACY (PERCEIVED + ENRICHED fallback)"
        else:
            mode = f"CUSTOM ({sorted(self.intake_event_types)})"
        logger.info(
            f"[Token] 發言權仲裁器已上線 ✓ mode={mode} "
            f"intake_event_types={[e.value for e in self.intake_event_types]}"
        )

    # ── 事件分派 ─────────────────────────────────────────────

    async def handle_event(self, event: SoulEvent) -> None:
        # Bry 拍板 2026-08-07 20:02: 從 self.intake_event_types 動態判斷
        # (Bus 已經先 filter, 這裡是雙重保險)
        if event.event_type in self.intake_event_types or event.event_type == EventType.AGENT_INTENT:
            # AGENT_INTENT 永遠不應該到這裡 (不在 intake_event_types), 但保留 fallback
            await self._request_token(event)
        elif event.event_type == EventType.AGENT_SPEAK:
            speaker = event.payload.get("agent_id", event.source)
            await self._release_token(speaker, reason="spoke_done")

    # ── 申請 token ─────────────────────────────────────────────

    async def _request_token(self, intent_event: SoulEvent) -> None:
        agent_id = intent_event.payload.get("agent_id")
        if not agent_id:
            logger.warning(f"[Token] {event.event_type} 缺 agent_id，略過")
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
