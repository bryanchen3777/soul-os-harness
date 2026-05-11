# heartbeat_engine.py
# Soul OS — Phase 1.b: 系統心跳引擎
#
# 設計原則：
#   Heartbeat 是「無情的時間派發器」，唯一職責是廣播 SYSTEM_TICK。
#   它不讀任何 Agent 的狀態，不做任何決策。
#   所有主動行為的決策，交給各 Agent 的 consciousness handler 自己判斷。

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

logger = logging.getLogger("soul_os.heartbeat")


class HeartbeatEngine:
    """
    系統心跳引擎，全局唯一。

    每隔 tick_interval 秒，在 Event Bus 上廣播一個 SYSTEM_TICK 事件。
    Tick 事件帶有 TTL = tick_interval，確保積壓時舊 Tick 自動過期，
    防止系統恢復後爆發雪崩效應 (Thundering Herd)。

    自動更新 last_user_activity：
    HeartbeatEngine 同時訂閱 USER_MESSAGE，
    每次使用者說話時重置活動時間戳，確保 elapsed_mins 計算準確。
    """

    def __init__(
        self,
        bus: SoulEventBus,
        tick_interval_seconds: int = 60,
    ):
        self.bus = bus
        self.tick_interval = tick_interval_seconds
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self.tick_count = 0
        self.last_user_activity: datetime = datetime.now(timezone.utc)

    async def start(self) -> None:
        if self._running:
            return

        # 訂閱 USER_MESSAGE，讓 Heartbeat 能自己更新最後活動時間
        # 這樣 elapsed_mins 永遠準確，不需要外部更新
        self.bus.subscribe(
            subscriber_id="heartbeat_activity_tracker",
            handler=self._on_user_message,
            event_filter={EventType.USER_MESSAGE},
        )

        self._running = True
        self._loop_task = asyncio.create_task(
            self._loop(), name="heartbeat_engine_loop"
        )
        logger.info(
            f"[Heartbeat] 啟動 ✓  間隔={self.tick_interval}s"
        )

    async def stop(self) -> None:
        self._running = False
        self.bus.unsubscribe("heartbeat_activity_tracker")
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[Heartbeat] 停止  總 Tick 數={self.tick_count}")

    async def _on_user_message(self, event: SoulEvent) -> None:
        """每次使用者說話，重置活動計時器"""
        self.last_user_activity = event.timestamp
        logger.debug("[Heartbeat] 活動計時器已重置")

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.tick_interval)
            if not self._running:
                break

            self.tick_count += 1
            now = datetime.now(timezone.utc)
            elapsed_mins = (now - self.last_user_activity).total_seconds() / 60.0

            tick = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="heartbeat_engine",
                target="broadcast",
                priority=EventPriority.LOW,
                # ✅ expires_at 必須是 datetime 物件，不是 float
                # TTL = 一個 tick_interval，確保積壓的舊 Tick 自動失效
                expires_at=now + timedelta(seconds=self.tick_interval),
                payload={
                    "tick_count": self.tick_count,
                    "elapsed_mins": round(elapsed_mins, 2),
                    "timestamp_utc": now.isoformat(),
                },
            )

            await self.bus.publish(tick)
            logger.debug(
                f"[Heartbeat] Tick #{self.tick_count}  "
                f"elapsed={elapsed_mins:.1f}m"
            )
