"""
test_heartbeat_trigger.py
Soul OS — Phase 3 Step 2: Heartbeat 真實 Tick 觸發測試

驗證 HeartbeatEngine + AgentRuka 的主動觸發鏈路真的會跑通：
  HeartbeatEngine(tick_interval=2s)
    → SYSTEM_TICK 廣播（含 elapsed_mins）
    → AgentRuka._on_tick(elapsed_mins) 收到
    → _should_speak() 評估 → True（條件滿足）
    → _fire_intent() 廣播 AGENT_INTENT ✓

關鍵：手動設 last_user_activity 為過去 10 分鐘，
讓 elapsed_mins 自動 ≥ 6 觸發 Ruka 的 silence_timeout 條件。

執行：
  python tests/test_heartbeat_trigger.py
"""
import asyncio
import io
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# stdout already UTF-8
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
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

    # ── 1. HeartbeatEngine 2s tick（不用等 60s）──
    heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=2)

    # ── 2. AgentRuka 註冊（會訂閱 SYSTEM_TICK）──
    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    # intimacy 預設 50，需要 > 50 才觸發；測試環境直接設 60
    ruka.state.intimacy_level = 60
    ruka.register()

    # ── 3. 收集 AGENT_INTENT 事件 ──
    captured: list[SoulEvent] = []

    async def capture_intent(event: SoulEvent) -> None:
        captured.append(event)
        logger.info(
            f"  [capture] 收到 AGENT_INTENT | reason={event.payload.get('reason')}"
        )

    bus.subscribe(
        "intent_capture",
        capture_intent,
        event_filter={EventType.AGENT_INTENT},
    )

    # ── 4. 開始 heartbeat，馬上把 last_user_activity 設成 10 分鐘前 ──
    await heartbeat.start()
    # elapsed_mins = (now - last_user_activity) / 60
    # 設成 10 分鐘前 → 第一個 tick 的 elapsed_mins ≈ 10
    heartbeat.last_user_activity = datetime.now(timezone.utc) - timedelta(minutes=10)
    logger.info(
        f"  設 last_user_activity = {heartbeat.last_user_activity.isoformat()}（10 分鐘前）"
    )

    # ── 5. 等 3 秒讓至少 1 個 tick 觸發 ──
    logger.info("  等 3s 讓 tick_interval=2s 至少觸發一次...")
    await asyncio.sleep(3.5)

    # ── 6. 驗收 ──
    logger.info("\n── 驗收 ──")
    logger.info(f"  Heartbeat 觸發 Tick 數：{heartbeat.tick_count}")
    logger.info(f"  AgentRuka 收到 AGENT_INTENT 次數：{len(captured)}")
    logger.info(f"  AgentRuka 冷卻計時：{ruka._cooldown_remaining}")

    assert heartbeat.tick_count >= 1, (
        f"Heartbeat 沒觸發 Tick（tick_count={heartbeat.tick_count}）"
    )
    assert len(captured) >= 1, (
        f"AgentRuka 沒被觸發主動說話（captured={len(captured)}）"
    )
    assert captured[0].payload.get("reason") == "silence_timeout", (
        f"預期 reason='silence_timeout'，實際={captured[0].payload.get('reason')}"
    )
    assert ruka._cooldown_remaining == ruka.COOLDOWN_TICKS, (
        f"冷卻應該被設成 {ruka.COOLDOWN_TICKS}，實際 {ruka._cooldown_remaining}"
    )

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Heartbeat 觸發鏈路完整通過")
    logger.info("    ✅ HeartbeatEngine 真實 Tick 廣播")
    logger.info("    ✅ AgentRuka._on_tick 收到並評估 elapsed_mins")
    logger.info("    ✅ _should_speak() 回傳 True（silence_timeout）")
    logger.info("    ✅ AGENT_INTENT 真的發出")
    logger.info("    ✅ 冷卻計時正確設成 COOLDOWN_TICKS")
    logger.info("=" * 60)

    await heartbeat.stop()
    await bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
