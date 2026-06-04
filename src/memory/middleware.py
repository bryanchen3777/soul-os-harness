"""
MemoryMiddleware — Soul OS Phase 2.0
連接 Soul Event Bus 與 vendored SAGE-lite 圖譜記憶

設計：
  - 每個 agent_id 維護一個獨立的 SAGELiteProvider（獨立 graph）
  - 訂閱三種事件：
      USER_MESSAGE        → 暫存 user_text（key = (session_id, agent_id)）
      AGENT_INTENT        → prefetch + 注入 memory_context，re-publish 為 ENRICHED
      AGENT_SPEAK         → post_reply_commit 寫入 graph（全寫，含觀察）
  - prefetch 是 sync；用 asyncio.to_thread 包
  - post_reply_commit 是 async；直接 await
  - 避免 AGENT_INTENT 無限迴圈：re-publish 為新的 AGENT_INTENT_ENRICHED event type
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.memory.sage import SAGELiteProvider

logger = logging.getLogger("soul_os.memory.middleware")


class MemoryMiddleware:
    """
    Bus subscriber，介接 SAGE-lite 圖譜記憶。

    每個 agent_id 一個 SAGELiteProvider（lazy init）。
    data_dir 結構：
        {data_dir}/{agent_id}/graph.sqlite
    """

    def __init__(self, bus: SoulEventBus, data_dir: str = "data/memory"):
        self.bus = bus
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._providers: Dict[str, SAGELiteProvider] = {}
        # key = session_id → user_text（等 AGENT_SPEAK 配對寫入）
        # Phase 2 假設單 session 單 agent；Phase 4 多 agent 同一 session 時
        # 需改成 (session_id, agent_id) 並設計配對策略
        self._pending_user_text: Dict[str, str] = {}

        # Phase 4：寫入節流，防止 N² 寫入爆炸
        # 多 agent 同時說話時（Speaker Token 釋放後 queue 觸發連發），
        # 同一 agent 5s 內的 AGENT_SPEAK 只寫一次
        self._last_commit: Dict[str, datetime] = {}
        self.COMMIT_COOLDOWN_SECS = 5.0

    def register(self) -> None:
        """向 Event Bus 註冊，開始接收三種事件。"""
        self.bus.subscribe(
            subscriber_id="memory_middleware",
            handler=self.handle_event,
            event_filter={
                EventType.USER_MESSAGE,
                EventType.AGENT_INTENT,
                EventType.AGENT_SPEAK,
            },
        )
        logger.info(
            f"[MemoryMiddleware] 已掛載，data_dir={self.data_dir} ✓"
        )

    def _get_provider(self, agent_id: str) -> SAGELiteProvider:
        """Lazy init：每個 agent 一個獨立 SAGELiteProvider。"""
        if agent_id not in self._providers:
            agent_dir = self.data_dir / agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            provider = SAGELiteProvider(
                profile_id=agent_id,
                data_dir=str(agent_dir),
            )
            provider.initialize(session_id="default")
            self._providers[agent_id] = provider
            logger.info(f"[MemoryMiddleware] 建立新 provider for {agent_id}")
        return self._providers[agent_id]

    # ── 事件分派 ─────────────────────────────────────────────

    async def handle_event(self, event: SoulEvent) -> None:
        if event.event_type == EventType.USER_MESSAGE:
            await self._on_user_message(event)
        elif event.event_type == EventType.AGENT_INTENT:
            await self._on_agent_intent(event)
        elif event.event_type == EventType.AGENT_SPEAK:
            await self._on_agent_speak(event)

    async def _on_user_message(self, event: SoulEvent) -> None:
        """暫存 user_text，等對應 session 的 AGENT_SPEAK 來配對寫入。"""
        session_id = event.session_id or "_no_session"
        text = event.payload.get("text", "")
        if text:
            self._pending_user_text[session_id] = text
            logger.debug(
                f"[MemoryMiddleware] 暫存 user_text | session={session_id}"
            )

    async def _on_agent_intent(self, event: SoulEvent) -> None:
        """prefetch → 注入 memory_context → re-publish 為 AGENT_INTENT_ENRICHED。

        為什麼用新 event type 而不是 flag：避免 re-publish 造成無限迴圈
        （若 LLMProxy 跟 MemoryMiddleware 都訂閱 AGENT_INTENT，
         MemoryMiddleware 處理完 re-publish，自己又會收到）。
        """
        agent_id = event.payload.get("agent_id", event.source)
        # query 優先順序：memory_query_hint > draft > 整個 payload 文字
        query = (
            event.payload.get("memory_query_hint")
            or event.payload.get("draft")
            or event.payload.get("text", "")
        )
        if not query:
            query = f"{agent_id} conversation"

        provider = self._get_provider(agent_id)

        # prefetch 是 sync；包進 thread executor 不阻塞 event loop
        context = await asyncio.to_thread(
            provider.prefetch, query, session_id=event.session_id or "default"
        )

        # 把記憶注入 payload，re-publish 為新事件
        event.payload["memory_context"] = context
        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source=event.source,
            target=event.target,
            priority=event.priority,
            payload=event.payload,
            session_id=event.session_id,
            correlation_id=event.correlation_id or event.event_id,
        )
        await self.bus.publish(enriched)

        logger.info(
            f"[MemoryMiddleware] enrich | agent={agent_id} | "
            f"query='{query[:30]}' | context_len={len(context)}"
        )

    async def _on_agent_speak(self, event: SoulEvent) -> None:
        """AGENT_SPEAK 來了 → 配對暫存的 user_text → 寫入 graph。

        採「全寫」策略：包含其他 agent 的 speak 也寫進自己的 graph，
        建立社交記憶（Yua 的 graph 也會記得瑠夏說過什麼）。

        Phase 4 加節流：同 agent 5s 內只寫一次，防 N² 寫入爆炸。
        """
        agent_id = event.payload.get("agent_id", event.source)
        session_id = event.session_id or "_no_session"

        # Phase 4 節流：同 agent 在 COMMIT_COOLDOWN_SECS 內的 AGENT_SPEAK 跳過寫入
        now = datetime.now(timezone.utc)
        last = self._last_commit.get(agent_id)
        if last and (now - last).total_seconds() < self.COMMIT_COOLDOWN_SECS:
            logger.debug(
                f"[Memory] {agent_id} 寫入節流（距上次 {(now-last).total_seconds():.1f}s），跳過"
            )
            return
        self._last_commit[agent_id] = now

        # 配對同一 session 的 user_text
        user_text = self._pending_user_text.pop(session_id, "")

        agent_text = event.payload.get("text", "")
        if not agent_text:
            logger.debug(
                f"[MemoryMiddleware] AGENT_SPEAK 沒 text，跳過寫入 | "
                f"agent={agent_id}"
            )
            return

        provider = self._get_provider(agent_id)
        await provider.post_reply_commit(
            session_id, user_text, agent_text
        )
        logger.info(
            f"[MemoryMiddleware] 寫入 graph | agent={agent_id} | "
            f"user_len={len(user_text)} | agent_len={len(agent_text)}"
        )

    # ── 維護 ─────────────────────────────────────────────────

    def shutdown(self) -> None:
        """收尾所有 provider 的 SQLite connection。"""
        for agent_id, provider in self._providers.items():
            try:
                provider.shutdown()
                logger.info(f"[MemoryMiddleware] shutdown {agent_id}")
            except Exception as e:
                logger.error(
                    f"[MemoryMiddleware] shutdown {agent_id} 失敗: {e}"
                )
        self._providers.clear()
        self._pending_user_text.clear()

    def get_stats(self) -> Dict:
        """回傳所有 agent 的圖譜健康指標。"""
        return {
            agent_id: provider.stats()
            for agent_id, provider in self._providers.items()
        }
