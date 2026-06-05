"""
test_heartbeat_trigger.py
Soul OS — Phase 3 Step 2: Heartbeat 真實 Tick 觸發測試

驗證 HeartbeatEngine + AgentRuka 的主動觸發鏈路真的會跑通：
  HeartbeatEngine(tick_interval=9999，完全手動控制)
    → 手動 SYSTEM_TICK（含 elapsed_mins=10）
    → AgentRuka._on_tick(elapsed_mins) 收到
    → _should_speak() 評估 → True（條件滿足）
    → _fire_intent() 廣播 AGENT_INTENT ✓

執行：
  python tests/test_heartbeat_trigger.py
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus.token_manager import SpeakerTokenManager
from src.heartbeat.engine import HeartbeatEngine
from src.agent.consciousness import AgentRuka

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.heartbeat")


async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 3 Step 2 Heartbeat 觸發測試")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    # ── 關鍵：tick_interval=9999，不自動 tick ──
    heartbeat = HeartbeatEngine(bus, tick_interval_seconds=9999)
    token_mgr = SpeakerTokenManager(bus)
    token_mgr.register()

    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    ruka.state.intimacy_level = 60
    ruka.register()

    await heartbeat.start()

    # 收集 AGENT_INTENT
    intents: list[SoulEvent] = []

    async def capture_intent(event: SoulEvent) -> None:
        intents.append(event)
        logger.info(
            f"  [capture] 收到 AGENT_INTENT | agent={event.payload.get('agent_id')} "
            f"reason={event.payload.get('reason')}"
        )

    bus.subscribe(
        "test_capture",
        capture_intent,
        event_filter={EventType.AGENT_INTENT},
    )

    # 模擬用戶 10 分鐘前最後說話（讓 elapsed_mins = 10）
    heartbeat.last_user_activity = datetime.now(timezone.utc) - timedelta(minutes=10)
    logger.info(
        f"  last_user_activity = {heartbeat.last_user_activity.isoformat()}（10 分鐘前）"
    )

    # ── 手動發 SYSTEM_TICK，elapsed_mins=10（超過 Ruka 的 6m 閾值）──
    tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat_engine",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "tick_count": 1,
            "elapsed_mins": 10.0,
            "time_period": "morning",
            "vulnerability_window": False,
            "silence_hours": 0.17,
            "attachment_heat": 0.1,
            "chrono_block": "",
        },
    )
    await bus.publish(tick)
    await asyncio.sleep(0.3)

    # ── 驗收 ──
    logger.info("\n── 驗收 ──")
    logger.info(f"  AgentRuka 收到 AGENT_INTENT 次數：{len(intents)}")
    logger.info(f"  AgentRuka 冷卻計時：{ruka._cooldown_remaining}")

    assert len(intents) >= 1, (
        f"Ruka 應該發出 AGENT_INTENT，但 intents={len(intents)}"
    )
    assert intents[0].payload.get("agent_id") == "agent_ruka"
    assert intents[0].payload.get("reason") == "silence_timeout"
    assert ruka._cooldown_remaining == ruka.COOLDOWN_TICKS, (
        f"冷卻應該被設成 {ruka.COOLDOWN_TICKS}，實際 {ruka._cooldown_remaining}"
    )

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Heartbeat 觸發鏈路完整通過")
    logger.info("    ✅ 手動 SYSTEM_TICK（elapsed_mins=10）觸發")
    logger.info("    ✅ AgentRuka._on_tick 收到並評估")
    logger.info("    ✅ _should_speak() 回傳 True（silence_timeout）")
    logger.info("    ✅ AGENT_INTENT 真的發出")
    logger.info("    ✅ 冷卻計時正確設成 COOLDOWN_TICKS")
    logger.info("=" * 60)

    bus.unsubscribe("test_capture")
    await heartbeat.stop()
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())