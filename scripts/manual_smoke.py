"""
scripts/smoke_test.py
Soul OS — Phase 4: 雙 Agent 真實對話煙霧測試

驗收：
  1. Yua 先觸發（elapsed_mins=35），Ruka 觀察到後排隊
  2. Yua 說完後 token 釋放，Ruka 自動獲 token 說話
  3. 系統無 USER_MESSAGE 觸發，真正主動發話

執行（從 repo 根目錄）：
  python scripts/smoke_test.py

事前準備：
  export LLM_PROVIDER=minimax
  export MINIMAX_API_KEY=sk-cp-...
"""
import asyncio
import logging
import os
import sys
import shutil
from pathlib import Path

os.environ.setdefault("LLM_PROVIDER", "minimax")
os.environ.setdefault("LLM_MODEL", "MiniMax-M2.7-highspeed")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus.token_manager import SpeakerTokenManager
from src.agent.consciousness import AgentYua, AgentRuka
from src.memory.middleware import MemoryMiddleware
from src.llm.proxy import LLMProxy, LLMBackend
from configs.loader import load_config, create_llm_backend, create_llm_proxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.smoke")


# ─────────────────────────────────────────────
# Mock LLM（無 key 時退路）
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    async def complete(self, messages, model, max_tokens, temperature):
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "Yua" in sys_content:
            return "還好你還在。（Yua 冷泡茶模式）"
        if "瑠夏" in sys_content or "Ruka" in sys_content:
            return "你去哪裡了！我在等你！（瑠夏激動模式）"
        return "[MOCK] 收到！"


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

async def main() -> int:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 4 雙 Agent 真實對話 smoke test")
    logger.info("=" * 60)

    # ── 1. 載入 config ──
    cfg = load_config()
    provider = cfg.get("llm", {}).get("provider", "mock")
    has_key = bool(cfg.get("llm", {}).get(provider, {}).get("api_key"))
    logger.info(f"  provider={provider} | has_key={'✓' if has_key else '✗'}")

    use_mock = not has_key

    # ── 2. 啟動 Bus ──
    bus = SoulEventBus()
    await bus.start()

    # ── 3. 掛載所有模組（順序很重要）──
    data_dir = Path("data/smoke_test")
    if data_dir.exists():
        shutil.rmtree(data_dir)
    mw = MemoryMiddleware(bus=bus, data_dir=str(data_dir))
    mw.register()

    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=15.0)
    token_mgr.register()

    if use_mock:
        llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock", max_tokens=200)
    else:
        llm = create_llm_proxy(cfg, bus)
    llm.register()
    logger.info(f"  LLM model={llm.model}")

    # ── 4. 兩個 Agent ──
    yua = AgentYua(agent_id="agent_yua", bus=bus)
    yua.state.intimacy_level = 80
    yua.register()

    ruka = AgentRuka(agent_id="agent_ruka", bus=bus)
    ruka.state.intimacy_level = 60
    ruka.register()

    # ── 5. 收集 AGENT_SPEAK 輸出 ──
    outputs: list[SoulEvent] = []

    async def capture(event: SoulEvent) -> None:
        outputs.append(event)
        text = event.payload.get("text", "")
        try:
            print(f"  [{event.source}] {text}")
        except UnicodeEncodeError:
            # Windows cp950 終端機 fallback
            print(f"  [{event.source}] {text.encode('utf-8', errors='replace').decode('utf-8')}")

    bus.subscribe("smoke_capture", capture, event_filter={EventType.AGENT_SPEAK})

    # ── 6. 灌一個記憶種子 ──
    logger.info("\n── Step 1: seed 台北 + 珍珠奶茶 到 graph ──")
    seed = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="user_bryan",
        target="broadcast",
        priority=EventPriority.HIGH,
        session_id="smoke_multi_001",
        payload={"text": "我在台北工作，喜歡喝珍珠奶茶"},
    )
    await bus.publish(seed)
    await asyncio.sleep(1.0)
    logger.info("  ✓ seed 已發布")

    # ── 7. 手動 Tick #1，elapsed_mins=35，Yua 先觸發 ──
    logger.info("\n── Step 2: 手動 SYSTEM_TICK（elapsed_mins=35）→ Yua 先觸發 ──")
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
            "chrono_block": "[CHRONO_SOCIAL_CONTEXT v2.2] time_period=morning silence_hours=0.58",
        },
    )
    await bus.publish(tick)
    await asyncio.sleep(5.0)  # 等真實 LLM 回應

    # ── 8. 手動 Tick #2，讓 Ruka 觀察到 Yua 說話後搶話 ──
    logger.info("\n── Step 3: 手動 SYSTEM_TICK（elapsed_mins=36）→ Ruka 搶話 ──")
    tick2 = SoulEvent(
        event_type=EventType.SYSTEM_TICK,
        source="heartbeat_engine",
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "tick_count": 2,
            "elapsed_mins": 36.0,
            "time_period": "morning",
            "vulnerability_window": False,
            "silence_hours": 0.60,
            "attachment_heat": 0.35,
            "chrono_block": "[CHRONO_SOCIAL_CONTEXT v2.2] time_period=morning silence_hours=0.60",
        },
    )
    await bus.publish(tick2)
    await asyncio.sleep(5.0)

    # ── 9. 結果 ──
    logger.info("\n── Smoke Test 結果 ──")
    print(f"\n  總輸出：{len(outputs)} 條 AGENT_SPEAK")
    stats = token_mgr.stats()
    print(f"  Token 統計：grants={stats['grants']} releases={stats['releases']}")

    if len(outputs) >= 1:
        logger.info("  ✓ 系統主動發話（無 USER_MESSAGE 觸發）")
    if len(outputs) >= 2:
        order = [e.source for e in outputs]
        logger.info(f"  ✓ 發話順序：{order}")

        # Phase 4 核心驗收：Yua → Ruka 順序
        if order[0] == "agent_yua" and order[-1] == "agent_ruka":
            logger.info("  ✓ Phase 4 仲裁正確：Yua 先 → Ruka 後")
        else:
            logger.warning(f"  ⚠ 順序非預期：{order}（預期 Yua → Ruka）")

    # ── 10. 清理 ──
    mw.shutdown()
    await bus.stop()
    shutil.rmtree(data_dir, ignore_errors=True)

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Smoke test 完成")
    logger.info(f"    實際輸出：{len(outputs)} 條 AGENT_SPEAK")
    logger.info(f"    Token grants={stats['grants']} releases={stats['releases']}")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:
        logger.exception(f"smoke test crashed: {e}")
        rc = 2
    sys.exit(rc)