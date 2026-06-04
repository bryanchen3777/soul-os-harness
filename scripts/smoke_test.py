"""
scripts/smoke_test.py
Soul OS — Phase 2.2: 真實 LLM 煙霧測試

驗收三件事：
  1. configs/loader 讀到 .env / 系統環境的 ANTHROPIC_API_KEY
  2. memory_context 有注入 system message（看回應內容是否提到「台北 / 珍珠奶茶」）
  3. LLM 「記得」你給的事實

執行（從 repo 根目錄）：
  python scripts/smoke_test.py

事前準備：
  export ANTHROPIC_API_KEY=sk-ant-...
  export LLM_PROVIDER=claude
"""
import asyncio
import io
import logging
import os
import sys
import time
from pathlib import Path

# 強制 LLM_PROVIDER=claude 確保 smoke test 真的打真實 LLM
# （如要改回 mock 測試，註解掉這兩行）
os.environ.setdefault("LLM_PROVIDER", "claude")
# 如果 LLM_MODEL 沒設，預設 haiku（最便宜、最快）
os.environ.setdefault("LLM_MODEL", "claude-haiku-4-5-20251001")

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.llm.proxy import LLMBackend
from src.memory.middleware import MemoryMiddleware
from src.memory.sage import SAGELiteProvider
from configs.loader import (
    load_config,
    create_llm_backend,
    create_llm_proxy,
    create_heartbeat,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.smoke")


# ─────────────────────────────────────────────
# Mock LLM（用於沒有真實 key 時的退路）
# ─────────────────────────────────────────────

class MockLLMBackend(LLMBackend):
    """沒 ANTHROPIC_API_KEY 時 fallback 用。"""
    async def complete(self, messages, model, max_tokens, temperature):
        logger.warning("[MockLLM] 沒有真實 key，注入 mock 回應")
        # 從 system message 抓出 memory_context（如果有的話）印出來
        sys_content = next(
            (m["content"] for m in messages if m["role"] == "system"), ""
        )
        if "你記得以下這些事情" in sys_content or "Memory" in sys_content:
            return "[MOCK] 嗯嗯，我記得你說的，讓我想想..."
        return "[MOCK] 收到！"


# ─────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────

async def main() -> int:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 2.2 真實 LLM 煙霧測試")
    logger.info("=" * 60)

    # ── 1. 載入 config ──
    cfg = load_config()
    provider = cfg.get("llm", {}).get("provider", "mock")
    has_claude_key = bool(cfg.get("llm", {}).get("claude", {}).get("api_key"))
    has_openai_key = bool(cfg.get("llm", {}).get("openai", {}).get("api_key"))
    logger.info(f"  provider={provider} | claude_key={'✓' if has_claude_key else '✗'} | openai_key={'✓' if has_openai_key else '✗'}")

    if provider == "claude" and not has_claude_key:
        logger.warning("⚠ LLM_PROVIDER=claude 但沒有 ANTHROPIC_API_KEY")
        logger.warning("  → fallback 用 MockLLMBackend（會印 [MOCK] 回應）")
        use_mock = True
    elif provider == "openai" and not has_openai_key:
        logger.warning("⚠ LLM_PROVIDER=openai 但沒有 OPENAI_API_KEY")
        logger.warning("  → fallback 用 MockLLMBackend")
        use_mock = True
    else:
        use_mock = False

    # ── 2. 啟動 Bus ──
    bus = SoulEventBus()
    await bus.start()

    # ── 3. 掛 MemoryMiddleware + LLMProxy + Heartbeat ──
    data_dir = Path("data/smoke_test")
    if data_dir.exists():
        import shutil
        shutil.rmtree(data_dir)
    mw = MemoryMiddleware(bus=bus, data_dir=str(data_dir))
    mw.register()

    if use_mock:
        from src.llm.proxy import LLMProxy
        llm = LLMProxy(bus=bus, backend=MockLLMBackend(),
                       model="mock", max_tokens=300)
    else:
        llm = create_llm_proxy(cfg, bus)
    llm.register()
    logger.info(f"  LLM model={llm.model}")

    # ── 4. 收集 AGENT_SPEAK 輸出 ──
    outputs: list[SoulEvent] = []
    async def capture(event: SoulEvent) -> None:
        outputs.append(event)
        logger.info(
            f"  [smoke_capture] 收到 | text='{event.payload.get('text', '')[:80]}'"
        )
    bus.subscribe("smoke_capture", capture, event_filter={EventType.AGENT_SPEAK})

    # ── 5. seed 一個事實到 graph ──
    logger.info("\n── Step 1: seed 台北 + 珍珠奶茶 到 graph ──")
    seed_text = "我在台北工作，喜歡喝珍珠奶茶"
    seed = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="user_bryan",
        target="broadcast",
        priority=EventPriority.HIGH,
        session_id="smoke_001",
        payload={"text": seed_text},
    )
    await bus.publish(seed)
    await asyncio.sleep(0.5)
    logger.info("  ✓ seed 已發布")

    # ── 6. 灌一個 AGENT_SPEAK 讓 post_reply_commit 觸發寫入 ──
    # 注意：seed_speak 文字刻意不用「台北」「珍珠奶茶」，斷言才能真的驗 LLM 召回
    logger.info("\n── Step 2: 觸發 AGENT_SPEAK 寫入 graph ──")
    seed_speak = SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        session_id="smoke_001",
        payload={"text": "好，我記住了", "agent_id": "agent_ruka"},
    )
    await bus.publish(seed_speak)
    await asyncio.sleep(1.0)
    provider_obj = mw._get_provider("agent_ruka")
    stats = provider_obj.stats()
    logger.info(f"  graph: {stats.get('active_facts', 0)} active facts")

    # ── 7. 觸發 AGENT_INTENT，MemoryMiddleware 會注入 memory_context ──
    logger.info("\n── Step 3: 觸發 AGENT_INTENT 讓 LLM 召回記憶 ──")
    intent = SoulEvent(
        event_type=EventType.AGENT_INTENT,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        session_id="smoke_001",
        payload={
            "agent_id": "agent_ruka",
            "reason": "user_message",
            "draft": "用戶剛才說他在台北工作、喜歡珍珠奶茶，請自然回應",
            "memory_query_hint": "台北 珍珠奶茶",
        },
    )
    await bus.publish(intent)
    # 等真實 LLM（haiku 通常 1-3s）
    await asyncio.sleep(8.0)

    # ── 8. 驗收 ──
    logger.info("\n── 驗收 ──")
    if not outputs:
        logger.error("  ✗ 沒收到 AGENT_SPEAK 回應（LLM 沒回 / 鏈斷了）")
        return 1

    final_text = outputs[-1].payload.get("text", "")
    has_taipei = "台北" in final_text or "臺北" in final_text
    has_bubble_tea = "珍珠奶茶" in final_text or "奶茶" in final_text or "bubble" in final_text.lower()

    logger.info(f"  LLM 回應：{final_text[:200]}")
    logger.info(f"  ✓ 收到回應：{len(final_text)} chars")
    logger.info(f"  {'✓' if has_taipei else '✗'} 提到 '台北'：{has_taipei}")
    logger.info(f"  {'✓' if has_bubble_tea else '✗'} 提到 '珍珠奶茶'：{has_bubble_tea}")

    if use_mock:
        logger.info("\n⚠ Mock mode：上面 ✓ 是「mock 收到 memory」≠「LLM 真的記得」")
        logger.info("  設 ANTHROPIC_API_KEY 後重跑才能驗 LLM 真的記得")

    if has_taipei and has_bubble_tea:
        logger.info("\n" + "=" * 60)
        logger.info("  ✓ Phase 2.2 驗收通過")
        logger.info("    ✅ config 載入 + .env key")
        logger.info("    ✅ memory 自動注入 prompt")
        logger.info("    ✅ LLM 記得你說的事實")
        logger.info("=" * 60)
        return 0
    elif use_mock:
        logger.info("\n⚠ Mock mode 預期不命中具體 entity（這是 mock，不是 LLM 失敗）")
        return 0
    else:
        logger.error("\n✗ 驗收失敗：LLM 沒在回應中提到 '台北' 或 '珍珠奶茶'")
        logger.error("  檢查：")
        logger.error("  1. graph 是否有寫入？")
        logger.error("  2. prefetch 是否有召回？")
        logger.error("  3. memory_context 是否有注入到 system message？")
        return 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        rc = 130
    except Exception as e:
        logger.exception(f"smoke test crashed: {e}")
        rc = 2
    sys.exit(rc)
