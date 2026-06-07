# heartbeat_engine.py
# Soul OS — Phase 1.b: 系統心跳引擎
#
# 設計原則：
#   Heartbeat 是「無情的時間派發器」，唯一職責是廣播 SYSTEM_TICK。
#   它不讀任何 Agent 的狀態，不做任何決策。
#   所有主動行為的決策，交給各 Agent 的 consciousness handler 自己判斷。

import asyncio
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.temporal import (
    build_temporal_context,
    render_temporal_block,
    EmotionalCarryover,
    PersonaConfig,
)

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

    Phase 4 carryover 持久化：
    每次心跳檢查 elapsed_mins >= 30 時廣播 SESSION_END，
    讓 Agent 將 carryover 寫入磁碟；下次啟動時載入並 apply_decay。
    """

    SESSION_END_THRESHOLD_MINS = 30.0

    def __init__(
        self,
        bus: SoulEventBus,
        tick_interval_seconds: int = 60,
        data_dir: str = "data/agents",
        agent_ids: list[str] | None = None,
    ):
        self.bus = bus
        self.tick_interval = tick_interval_seconds
        self.data_dir = data_dir
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        self.tick_count = 0
        self.last_user_activity: datetime = datetime.now(timezone.utc)
        self._session_ended = False  # 防止 SESSION_END 重複觸發
        self._carryovers: dict[str, EmotionalCarryover] = {}
        self._agent_ids: list[str] = agent_ids or []  # 由外部注入，不 hardcode
        # Fix Bug 3: 全局靜默冷卻 — 任何人說話後 60 秒內不發 SYSTEM_TICK（避免連續觸發）
        self._last_any_speak: float = 0.0
        self.global_silence_secs: float = 60.0
        self._pending_agents: set = set()  # 正在等 LLM 回應的 agent

        # 訂閱 AGENT_SPEAK，更新靜默計時器
        self.bus.subscribe(
            subscriber_id="heartbeat_silence_tracker",
            handler=self._on_any_speak,
            event_filter={EventType.AGENT_SPEAK},
        )

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

        # Phase 4 carryover：啟動時載入所有已註冊 Agent 的 carryover 並 apply_decay
        for agent_id in self._agent_ids:
            self._carryovers[agent_id] = (
                EmotionalCarryover.load(agent_id, self.data_dir)
                .apply_decay(elapsed_hours=0.0)
            )
            c = self._carryovers[agent_id]
            logger.info(
                f"[Heartbeat] {agent_id} carryover 載入："
                f" heat={c.attachment_heat:.2f} afterglow={c.intimacy_afterglow:.2f}"
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
        self.bus.unsubscribe("heartbeat_silence_tracker")
        if self._loop_task and not self._loop_task.done():
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        logger.info(f"[Heartbeat] 停止  總 Tick 數={self.tick_count}")

    async def _on_user_message(self, event: SoulEvent) -> None:
        """每次使用者說話，重置活動計時器與 session 结束标记"""
        self.last_user_activity = event.timestamp
        self._session_ended = False  # 新訊息到來，代表新 session 開始
        logger.debug("[Heartbeat] 活動計時器已重置，_session_ended=False")

    async def _on_any_speak(self, event: SoulEvent) -> None:
        self._last_any_speak = time.time()
        logger.debug("[Heartbeat] 說話事件已更新靜默計時器")

    async def _loop(self) -> None:
        while self._running:
            await asyncio.sleep(self.tick_interval)
            if not self._running:
                break

            # Fix Bug 3: 全局靜默保護 — 說話後 60 秒內不廣播 tick
            now_local = time.time()
            if now_local - self._last_any_speak < self.global_silence_secs:
                logger.debug("[Heartbeat] 全局靜默中，跳過本輪 tick")
                continue

            self.tick_count += 1
            now = datetime.now(timezone.utc)
            elapsed_mins = (now - self.last_user_activity).total_seconds() / 60.0

            # Phase 3.5：chrono-social-engine 時間感知（含 carryover 注入）
            # Phase 4：取第一個已註冊 Agent 的 carryover inject 到 chrono_ctx
            primary_agent = self._agent_ids[0] if self._agent_ids else None
            carryover = (
                self._carryovers.get(primary_agent, EmotionalCarryover())
                if primary_agent
                else EmotionalCarryover()
            )
            chrono_cfg = PersonaConfig(persona_id="heartbeat_system")
            chrono_ctx = build_temporal_context(
                persona_id="heartbeat_system",
                last_msg_ts=self.last_user_activity.isoformat(),
                current_stress=0,
                carryover=carryover,
                config=chrono_cfg,
                now=now,
            )
            chrono_block = render_temporal_block(chrono_ctx)

            tick = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="heartbeat_engine",
                target="broadcast",
                priority=EventPriority.LOW,
                expires_at=now + timedelta(seconds=self.tick_interval),
                payload={
                    "tick_count": self.tick_count,
                    "elapsed_mins": round(elapsed_mins, 2),
                    "timestamp_utc": now.isoformat(),
                    # Phase 3.5 chrono 豐富欄位
                    "time_period": chrono_ctx.time_period,
                    "vulnerability_window": chrono_ctx.momentum.vulnerability_window,
                    "silence_hours": round(chrono_ctx.silence_hours, 2),
                    "attachment_heat": round(chrono_ctx.carryover.attachment_heat, 2),
                    "deviation_interpretation": chrono_ctx.deviation_interpretation,
                    "preoccupation_flavor": chrono_ctx.anticipatory.preoccupation_flavor,
                    "chrono_block": chrono_block,
                },
            )

            await self.bus.publish(tick)

            # Phase 4 carryover 持久化：SESSION_END 偵測
            if elapsed_mins >= self.SESSION_END_THRESHOLD_MINS and not self._session_ended:
                self._session_ended = True
                session_end_event = SoulEvent(
                    event_type=EventType.SESSION_END,
                    source="heartbeat_engine",
                    target="broadcast",
                    priority=EventPriority.LOW,
                    payload={
                        "elapsed_mins": round(elapsed_mins, 2),
                        "last_user_activity": self.last_user_activity.isoformat(),
                    },
                )
                await self.bus.publish(session_end_event)
                logger.info(
                    f"[Heartbeat] SESSION_END 廣播（elapsed={elapsed_mins:.1f}m）"
                )

            logger.debug(
                f"[Heartbeat] Tick #{self.tick_count}  "
                f"elapsed={elapsed_mins:.1f}m  period={chrono_ctx.time_period}"
            )
