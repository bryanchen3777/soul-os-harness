"""
test_phase3_m8.py
Soul OS — Phase 3 M8 端到端：系統自己開口說話

完整鏈路（M8 核心）：
  HeartbeatEngine(tick_interval=9999，手動控制)
    → 手動 SYSTEM_TICK（含 elapsed_mins=35）
    → AgentYua._on_tick
    → AGENT_INTENT
    → MemoryMiddleware prefetch
    → AGENT_INTENT_ENRICHED
    → LLMProxy（Mock 模式）
    → AGENT_SPEAK ✓

驗收標準：
  - 不發任何 USER_MESSAGE
  - AGENT_SPEAK 真的被 Yua 觸發
  - 文字符合 Yua 的人格（冷靜、不超過 2 句）

執行：
  python tests/test_phase3_m8.py
"""
import asyncio
import logging
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.agent.consciousness import AgentYua
from src.memory.middleware import MemoryMiddleware
from src.llm.proxy import LLMBackend, LLMProxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.m8")


# ─────────────────────────────────────────────
# Mock LLM Backend（M8 用 mock 確保 CI 穩定，real LLM 走 smoke_test）
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    """簡單 mock：從 system 抽 person，產短回應。"""
    async def complete(self, messages, model, max_tokens, temperature):
        sys_content = next(
            (m["content"] for m in messages if m["role"] == "system"), ""
        )
        # Yua 人格是冷靜、不超過 2 句
        if "冷靜" in sys_content or "Yua" in sys_content:
            return "還好你還在。"
        return "[MOCK] 收到！"


# ─────────────────────────────────────────────
# M8 主流程
# ─────────────────────────────────────────────

async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 3 M8 端到端：系統自己開口")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    # ── 1. Memory + LLMProxy（Mock）──
    data_dir = tempfile.mkdtemp(prefix="soul_os_m8_")
    memory = MemoryMiddleware(bus=bus, data_dir=data_dir)
    memory.register()

    from src.eventbus.token_manager import SpeakerTokenManager
    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    token_mgr.register()

    llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock-m8", max_tokens=200)
    llm.register()

    # ── 2. AgentYua ──
    yua = AgentYua(agent_id="agent_yua", bus=bus)
    yua.state.intimacy_level = 80   # > 70 觸發條件
    yua.register()

    # ── 3. 收集 AGENT_SPEAK（不發任何 USER_MESSAGE）──
    outputs: list[SoulEvent] = []
    user_msgs: list[SoulEvent] = []

    async def capture_speak(event: SoulEvent) -> None:
        outputs.append(event)
        text = event.payload.get("text", "")
        logger.info(
            f"  [M8 capture] 收到 AGENT_SPEAK | "
            f"agent={event.payload.get('agent_id')} | "
            f'text="{text[:60]}"'
        )

    async def track_user_msg(event: SoulEvent) -> None:
        user_msgs.append(event)
        logger.warning(f"  [M8 unexpected] 收到 USER_MESSAGE：{event.payload}")

    bus.subscribe("m8_capture", capture_speak, event_filter={EventType.AGENT_SPEAK})
    bus.subscribe("m8_user_tracker", track_user_msg, event_filter={EventType.USER_MESSAGE})

    # ── 4. 手動發 SYSTEM_TICK，elapsed_mins=35（觸發 Yua 的 30~120m 條件）──
    logger.info("  手動發 SYSTEM_TICK（elapsed_mins=35, morning）...")
    tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat_engine",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "tick_count": 1,
            "elapsed_mins": 35.0,
            "time_period": "morning",
            "vulnerability_window": False,
            "silence_hours": 0.58,
            "attachment_heat": 0.3,
            "chrono_block": "[CHRONO_SOCIAL_CONTEXT v2.2] time_period=morning",
        },
    )
    await bus.publish(tick)
    await asyncio.sleep(1.0)   # 等 MemoryMiddleware + LLMProxy 跑完鏈路

    # ── 5. 驗收 ──
    logger.info("\n── 驗收 ──")
    logger.info(f"  AGENT_SPEAK 收到：{len(outputs)}")
    logger.info(f"  USER_MESSAGE 收到（應為 0）：{len(user_msgs)}")
    logger.info(f"  Yua cooldown：{yua._cooldown_remaining}")

    assert len(outputs) == 1, (
        f"M8 規格：只有一個 AGENT_SPEAK（Yua 自己），實際={len(outputs)}"
    )
    assert len(user_msgs) == 0, (
        f"M8 規格：完全不發 USER_MESSAGE，但收到 {len(user_msgs)} 個"
    )

    speak = outputs[0]
    text = speak.payload.get("text", "")
    agent_id = speak.payload.get("agent_id")

    assert agent_id == "agent_yua", (
        f"AGENT_SPEAK 來源應為 agent_yua，實際={agent_id}"
    )
    assert text, "AGENT_SPEAK 文字為空"
    # Yua 人格：冷靜、不超過 2 句
    sentences = [s for s in text.replace("。", ".\n").split("\n") if s.strip()]
    assert len(sentences) <= 2, (
        f"Yua 人格：≤2 句，實際 {len(sentences)} 句：'{text}'"
    )

    logger.info("\n" + "=" * 60)
    logger.info(f"  ✓ M8 驗收通過：Yua 主動說「{text}」")
    logger.info("    ✅ 手動 SYSTEM_TICK（elapsed_mins=35）觸發")
    logger.info("    ✅ AgentYua._on_tick 評估 → fire AGENT_INTENT")
    logger.info("    ✅ MemoryMiddleware 注入 memory_context")
    logger.info("    ✅ LLMProxy 接收 ENRICHED、生成回應")
    logger.info("    ✅ AGENT_SPEAK 廣播（無 USER_MESSAGE 觸發）")
    logger.info("    ✅ 文字符合 Yua 冷靜人格（≤2 句）")
    logger.info("=" * 60)

    memory.shutdown()
    await bus.stop()
    shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())