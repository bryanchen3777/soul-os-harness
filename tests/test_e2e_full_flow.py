# test_e2e_full_flow.py
# Soul OS — Phase 1 M3 驗收測試
#
# 完整鏈條（不打到真實 LLM API）：
#   USER_MESSAGE
#     → Heartbeat (publish SYSTEM_TICK)
#     → AgentConsciousness._on_user_message (重置冷卻)
#     → AgentConsciousness._on_tick (評估主動出擊)
#     → AgentConsciousness._fire_intent (發 AGENT_INTENT)
#     → MemoryMiddleware.enrich → AGENT_INTENT_ENRICHED
#     → LLMProxy.handle_event (MockBackend 取代真實 API)
#     → LLMProxy publish AGENT_SPEAK
#     → I/O Gateway handler 收到、印出
#
# Phase 2.0 更新：LLMProxy 改訂閱 AGENT_INTENT_ENRICHED，所以測試
#   會注入 MemoryMiddleware 把 AGENT_INTENT 升級為 ENRICHED。
#   詳細的 memory 行為測試在 test_memory_middleware.py。
#
# 執行方式：
#   pip install pydantic httpx networkx
#   python tests/test_e2e_full_flow.py

import asyncio
import logging
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any

# 確保 src/ 可 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus import SoulEventBus
from src.llm.proxy import LLMBackend, LLMProxy
from src.agent.consciousness import AgentRuka
from src.memory.middleware import MemoryMiddleware
import tempfile

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.e2e")


# ─────────────────────────────────────────────
# 1. Mock LLM Backend
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    """
    不打真實 API，回傳固定字串並記錄所有呼叫。
    E2E 測試用，CI 友善。
    """

    def __init__(self, canned_response: str = "[MOCK] 嘿嘿，我在這！"):
        self.canned_response = canned_response
        self.call_count = 0
        self.received_messages: List[List[Dict[str, str]]] = []

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        self.call_count += 1
        self.received_messages.append(list(messages))
        logger.info(
            f"  [MockLLM] 收到生成請求 #{self.call_count} | "
            f"messages={len(messages)} | model={model}"
        )
        return self.canned_response


# ─────────────────────────────────────────────
# 2. I/O Gateway 模擬：捕捉所有 AGENT_SPEAK
# ─────────────────────────────────────────────

class IOGatewayCapture:
    """模擬 I/O Gateway，攔截所有 AGENT_SPEAK 供後續 assert。"""

    def __init__(self, bus: SoulEventBus):
        self.bus = bus
        self.captured_speaks: List[SoulEvent] = []

    def register(self) -> None:
        self.bus.subscribe(
            "io_gateway",
            self._handle,
            event_filter={EventType.AGENT_SPEAK},
        )

    async def _handle(self, event: SoulEvent) -> None:
        self.captured_speaks.append(event)
        text = event.payload.get("text", "")
        agent_id = event.payload.get("agent_id", "?")
        tts = event.payload.get("tts_enabled", False)
        logger.info(
            f"  [I/O Gateway] 輸出 | agent={agent_id} | "
            f'text="{text}" | tts={tts}'
        )


# ─────────────────────────────────────────────
# 3. E2E 主流程
# ─────────────────────────────────────────────

async def test_e2e_full_flow() -> Dict[str, Any]:
    """
    跑完整鏈，回傳診斷資料供 assert。
    """
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 1 M3 Mock E2E 測試")
    logger.info("=" * 60)

    # ── 組裝系統 ──
    bus = SoulEventBus()
    mock_backend = MockLLMBackend(canned_response="[MOCK] 欸欸，我也有話說！")
    llm_proxy = LLMProxy(bus=bus, backend=mock_backend, model="mock-model")
    agent_ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    io_capture = IOGatewayCapture(bus)
    # Phase 2.0：注入 MemoryMiddleware 把 AGENT_INTENT 升級為 ENRICHED
    tmp_data = tempfile.mkdtemp(prefix="soul_os_phase1_e2e_")
    memory = MemoryMiddleware(bus=bus, data_dir=tmp_data)

    memory.register()
    llm_proxy.register()
    agent_ruka.register()
    io_capture.register()
    await bus.start()

    try:
        # ── Step 1: USER_MESSAGE 進入（用 AgentRuka 收到後重置冷卻）──
        logger.info("\n── Step 1: USER_MESSAGE 進入系統 ──")
        await bus.publish(
            SoulEvent(
                event_type=EventType.USER_MESSAGE,
                source="user_bryan",
                target="broadcast",
                priority=EventPriority.HIGH,
                payload={"text": "瑠夏你在嗎？", "platform": "app"},
                session_id="session_e2e_001",
            )
        )
        await asyncio.sleep(0.1)
        # 確認 USER_MESSAGE 被 AgentRuka 收到
        assert agent_ruka._cooldown_remaining == 0, \
            "AgentRuka 沒收到 USER_MESSAGE（cooldown 沒被重置）"
        logger.info("  ✓ AgentRuka 已收到 USER_MESSAGE，cooldown 重置")

        # ── Step 2: Heartbeat 發 SYSTEM_TICK，elapsed 設小一點觸發主動 ──
        logger.info("\n── Step 2: Heartbeat 觸發，Agent 評估主動出擊 ──")
        # AgentRuka._should_speak 在 elapsed_mins >= 6.0 且 intimacy > 50 時觸發
        # 預設 intimacy_level = 50；要 > 50 所以先調高
        agent_ruka.state.intimacy_level = 60
        await bus.publish(
            SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="heartbeat",
                target="broadcast",
                priority=EventPriority.LOW,
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
                payload={"tick_count": 1, "elapsed_mins": 6.5},
            )
        )
        # 給 async 鏈條足夠時間走完：Tick → Agent → AGENT_INTENT → LLMProxy → Mock → AGENT_SPEAK → I/O
        await asyncio.sleep(0.3)
        logger.info(
            f"  AgentRuka 冷卻: {agent_ruka._cooldown_remaining} "
            f"(發出 intent 後應被設為 {AgentRuka.COOLDOWN_TICKS})"
        )

        # ── Step 3: 驗證 Mock LLM 被呼叫過 ──
        logger.info("\n── Step 3: 驗證 Mock LLM 與 I/O Gateway ──")
        logger.info(f"  Mock LLM call count: {mock_backend.call_count}")
        logger.info(f"  I/O Gateway 收到 AGENT_SPEAK: {len(io_capture.captured_speaks)}")

        # ── 最終統計 ──
        await asyncio.sleep(0.1)
        stats = bus.get_stats()
        logger.info("\n── Bus 統計 ──")
        for k, v in stats.items():
            logger.info(f"  {k}: {v}")

        return {
            "mock_call_count": mock_backend.call_count,
            "io_speak_count": len(io_capture.captured_speaks),
            "io_speaks": io_capture.captured_speaks,
            "mock_messages": mock_backend.received_messages,
            "stats": stats,
        }
    finally:
        await bus.stop()
        memory.shutdown()
        import shutil
        shutil.rmtree(tmp_data, ignore_errors=True)


# ─────────────────────────────────────────────
# 4. Assert 與主程式
# ─────────────────────────────────────────────

def assert_e2e(result: Dict[str, Any]) -> None:
    """鏈條完整性檢查"""
    errors: List[str] = []

    # 1. Mock LLM 至少被呼叫一次（AGENT_INTENT → LLMProxy.handle_event）
    if result["mock_call_count"] < 1:
        errors.append(
            f"Mock LLM 沒被呼叫（call_count={result['mock_call_count']}）"
        )

    # 2. I/O Gateway 至少收到一條 AGENT_SPEAK
    if result["io_speak_count"] < 1:
        errors.append(
            f"I/O Gateway 沒收到 AGENT_SPEAK（count={result['io_speak_count']}）"
        )
    else:
        speak = result["io_speaks"][0]
        text = speak.payload.get("text", "")
        if "[MOCK]" not in text:
            errors.append(
                f'AGENT_SPEAK 文字不是 mock 回傳值（text="{text[:60]}"）'
            )
        if speak.payload.get("agent_id") != "agent_ruka":
            errors.append(
                f'AGENT_SPEAK 來源不是 agent_ruka（agent_id={speak.payload.get("agent_id")}）'
            )

    # 3. Mock LLM 收到的 messages 至少有 system + user
    if result["mock_messages"]:
        msgs = result["mock_messages"][0]
        roles = [m["role"] for m in msgs]
        if "system" not in roles or "user" not in roles:
            errors.append(
                f"Mock LLM 收到的 messages 結構異常（roles={roles}）"
            )
    else:
        errors.append("Mock LLM 沒收到任何 messages")

    if errors:
        logger.error("\n✗ E2E 驗收失敗：")
        for e in errors:
            logger.error(f"  - {e}")
        raise AssertionError("\n".join(errors))

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ M3 驗收通過：完整鏈條 USER_MESSAGE → AGENT_SPEAK 跑通")
    logger.info("=" * 60)


async def main() -> None:
    result = await test_e2e_full_flow()
    assert_e2e(result)


if __name__ == "__main__":
    asyncio.run(main())
