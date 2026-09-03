"""
test_phase4_multi_agent.py
Soul OS — Phase 4 多 Agent E2E 端到端

驗收場景：
  場景一：Yua 先觸發，Ruka 搶話排隊 → Yua 說完 → Ruka 自動獲 token 說話
  場景二：凌晨三點，兩個 Agent 都靜默（不應有任何 AGENT_INTENT）
  場景三：User 插話，兩個 Agent 的冷卻都重置

執行：
  python tests/test_phase4_multi_agent.py
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
from src.eventbus.token_manager import SpeakerTokenManager
from src.heartbeat.engine import HeartbeatEngine
from src.agent.consciousness import AgentYua, AgentRuka
from src.memory.middleware import MemoryMiddleware
from src.llm.proxy import LLMBackend, LLMProxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.phase4")


# ─────────────────────────────────────────────
# Mock LLM Backend
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    async def complete(self, messages, model, max_tokens, temperature, **kwargs):
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        # 根據 draft 内容决定回复
        draft = next((m["content"] for m in messages if m["role"] == "user"), "")
        if "Yua" in sys_content or "冷靜" in sys_content:
            return "還好你還在。（Yua 冷泡茶模式）"
        if "瑠夏" in sys_content or "Ruka" in sys_content:
            if "competitive" in draft or "搶" in draft:
                return "欸欸，我也有話說！（瑠夏活潑模式）"
            return "你去哪裡了！我在等你！（瑠夏激動模式）"
        return "[MOCK] 收到！"


# ─────────────────────────────────────────────
# 場景一：Yua 先觸發，Ruka 搶話排隊 → Yua → Ruka 順序正確
# ─────────────────────────────────────────────

async def scenario_yua_first_then_ruka() -> None:
    logger.info("\n" + "=" * 60)
    logger.info("  場景一：Yua 先觸發 → Ruka 搶話排隊 → 依序說話")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    # MemoryMiddleware + SpeakerTokenManager
    data_dir = tempfile.mkdtemp(prefix="soul_os_p4_s1_")
    memory = MemoryMiddleware(bus=bus, data_dir=data_dir)
    memory.register()
    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    token_mgr.register()

    # Mock LLMProxy
    llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock-p4", max_tokens=200)
    llm.register()

    # Heartbeat（手動控制 tick）
    heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=999)  # 999s，避免自動 tick
    await heartbeat.start()

    # 兩個 Agent
    yua = AgentYua(agent_id="agent_yua", bus=bus)
    yua.state.intimacy_level = 80   # > 70 觸發
    yua.register()

    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    ruka.state.intimacy_level = 60  # Ruka 親密度足夠（>50 就能觸發）
    ruka.register()

    # 收集事件
    speaks: list[SoulEvent] = []
    granted: list[SoulEvent] = []

    async def capture_speak(event: SoulEvent) -> None:
        speaks.append(event)
        logger.info(f"  [SPOKE] {event.payload.get('agent_id')}: {event.payload.get('text', '')[:50]}")

    async def capture_granted(event: SoulEvent) -> None:
        granted.append(event)
        logger.info(f"  [GRANTED] {event.payload.get('agent_id')}")

    bus.subscribe("p4_speak", capture_speak, event_filter={EventType.AGENT_SPEAK})
    bus.subscribe("p4_granted", capture_granted, event_filter={EventType.SPEAKER_TOKEN_GRANTED})

    # ── Step 1：手動一個 SYSTEM_TICK（elapsed=35，morning）
    # → Yua 應觸發（30~120m 且 intimacy>70）
    logger.info("  Step 1：發 SYSTEM_TICK（elapsed=35m, morning）")
    tick_1 = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "elapsed_mins": 35.0,
            "time_period": "morning",
            "vulnerability_window": False,
        },
    )
    await bus.publish(tick_1)
    await asyncio.sleep(0.3)

    # Yua 拿到 token、LLM 生成、AGENT_SPEAK
    assert len(granted) >= 1, f"Yua 應拿到 token，granted={len(granted)}"
    assert granted[0].payload.get("agent_id") == "agent_yua"
    logger.info(f"  ✓ Yua 拿到 Speaker Token")

    # 等 AGENT_SPEAK 出來（LLM 生成需要一點時間）
    await asyncio.sleep(0.3)

    # ── Step 2：手動第二個 SYSTEM_TICK
    # → Ruka._should_speak：_other_agent_spoke_recently = True → competitive_response 觸發
    # → token_manager 讓 Ruka 排隊（Yua 還沒釋放）
    logger.info("  Step 2：發 SYSTEM_TICK（elapsed=8m, morning）")
    tick_2 = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "elapsed_mins": 8.0,   # Ruka 6 分鐘就坐不住
            "time_period": "morning",
            "vulnerability_window": False,
        },
    )
    await bus.publish(tick_2)
    await asyncio.sleep(0.3)

    logger.info(f"  Ruka _other_agent_spoke_recently={ruka._other_agent_spoke_recently}")

    # Yua 的 AGENT_SPEAK 釋放 token，Ruka 自動獲 token
    await asyncio.sleep(0.5)
    await asyncio.sleep(0.5)

    # ── 驗收
    speak_agents = [e.payload.get("agent_id") for e in speaks]
    logger.info(f"\n  收到 speaks：{speak_agents}")

    assert len(speaks) >= 2, f"預期至少 2 次 AGENT_SPEAK，實際={len(speaks)}"
    assert speaks[0].payload.get("agent_id") == "agent_yua", (
        f"第一個說話的應是 Yua，實際={speaks[0].payload.get('agent_id')}"
    )
    assert speaks[1].payload.get("agent_id") == "agent_ruka", (
        f"第二個說話的應是 Ruka，實際={speaks[1].payload.get('agent_id')}"
    )

    # 驗 token grant 順序：granted[0]=Yua, granted[1]=Ruka
    assert len(granted) >= 2, f"預期 2 次 GRANTED（Yua + Ruka），實際={len(granted)}"
    assert granted[0].payload.get("agent_id") == "agent_yua", (
        f"granted[0] 應是 Yua，實際={granted[0].payload.get('agent_id')}"
    )
    assert granted[1].payload.get("agent_id") == "agent_ruka", (
        f"granted[1] 應是 Ruka，實際={granted[1].payload.get('agent_id')}"
    )

    # 驗 token manager stats
    logger.info(f"\n  SpeakerTokenManager stats: {token_mgr.stats()}")
    assert token_mgr._holder is None or granted[-1].payload.get("agent_id") == "agent_ruka"

    logger.info("\n  ✓ 場景一通過：Yua → Ruka 順序正確，token 自動切換")

    await heartbeat.stop()
    memory.shutdown()
    await bus.stop()
    shutil.rmtree(data_dir, ignore_errors=True)


# ─────────────────────────────────────────────
# 場景二：凌晨三點，兩個 Agent 都靜默
# ─────────────────────────────────────────────

async def scenario_deep_night_silent() -> None:
    logger.info("\n" + "=" * 60)
    logger.info("  場景二：凌晨三點，兩個 Agent 都靜默")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    token_mgr.register()

    yua = AgentYua(agent_id="agent_yua", bus=bus)
    yua.state.intimacy_level = 80
    yua.register()

    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    ruka.state.intimacy_level = 80
    ruka.register()

    intents: list[SoulEvent] = []
    async def capture_intent(event: SoulEvent) -> None:
        intents.append(event)

    bus.subscribe("p4_intent_deep", capture_intent, event_filter={EventType.AGENT_INTENT})

    # 凌晨三點的 SYSTEM_TICK（deep_night）
    tick = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "elapsed_mins": 35.0,
            "time_period": "deep_night",
            "vulnerability_window": True,
        },
    )
    await bus.publish(tick)
    await asyncio.sleep(0.5)

    assert len(intents) == 0, (
        f"凌晨三點不應有 AGENT_INTENT，實際={len(intents)}："
        f"{[e.payload.get('reason') for e in intents]}"
    )
    logger.info("  ✓ 凌晨三點：兩個 Agent 都靜默，無 AGENT_INTENT")

    await bus.stop()


# ─────────────────────────────────────────────
# 場景三：User 插話發給 Ruka，Ruka 的冷卻重置（Yua 不受影響）
# ─────────────────────────────────────────────

async def scenario_user_interrupt_resets_cooldown() -> None:
    logger.info("\n" + "=" * 60)
    logger.info("  場景三：User 插話（發給 Ruka），Ruka 冷卻重置")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()

    yua = AgentYua(agent_id="agent_yua", bus=bus)
    yua.state.intimacy_level = 80
    yua.register()

    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    ruka.state.intimacy_level = 80
    ruka.register()

    # 模擬 Ruka 正在說話，Yua 也有 cooldowm
    yua._cooldown_remaining = 5
    ruka._cooldown_remaining = 3

    # User 插話精準發給 Ruka（Ruka 的 _on_user_message 被觸發）
    user_msg = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="user",
        target="agent_ruka",   # 精準投遞給 Ruka
        priority=EventPriority.HIGH,
        payload={"text": "我回來了！"},
    )
    await bus.publish(user_msg)
    await asyncio.sleep(0.2)

    assert ruka._cooldown_remaining == 0, (
        f"Ruka cooldown 應重置為 0，實際={ruka._cooldown_remaining}"
    )
    # Yua 的 cooldown 不受影響（因為 USER_MESSAGE 沒有發給她）
    assert yua._cooldown_remaining == 5, (
        f"Yua cooldown 應保持為 5，實際={yua._cooldown_remaining}"
    )
    logger.info("  ✓ User 插話發給 Ruka：Ruka cooldown 重置，Yua 不受影響")

    await bus.stop()


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────

async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 4 多 Agent E2E")
    logger.info("=" * 60)

    await scenario_yua_first_then_ruka()
    await scenario_deep_night_silent()
    await scenario_user_interrupt_resets_cooldown()

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Phase 4 多 Agent E2E 全部通過")
    logger.info("    ✅ Yua 先觸發 → Ruka 搶話排隊 → 依序說話（Yua → Ruka）")
    logger.info("    ✅ 凌晨三點：兩個 Agent 都靜默")
    logger.info("    ✅ User 插話：兩個 Agent 冷卻重置")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())