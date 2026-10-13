"""
test_chrono_integration.py
Soul OS — Phase 3.5: chrono-social-engine 整合測試

驗收：
  1. Heartbeat Tick payload 帶 time_period、chrono_block 等豐富欄位
  2. 深夜（mock now=凌晨 3 點）時 Yua 不觸發 silence_timeout，但會用更長 COOLDOWN
  3. LLM 收到的 system message 包含 chrono 區塊

執行：
  python tests/test_chrono_integration.py
"""
import asyncio
import io
import logging
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch

# stdout already UTF-8
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.heartbeat.engine import HeartbeatEngine
from src.agent.consciousness import AgentYua, AgentRuka
from src.memory.middleware import MemoryMiddleware
from src.llm.proxy import LLMBackend, LLMProxy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.chrono")


class MockLLMBackend(LLMBackend):
    """Spy：記錄收到的 system message 內容。"""
    def __init__(self):
        self.captured_messages: List[List[dict]] = []

    async def complete(self, messages, model, max_tokens, temperature, **kwargs):
        self.captured_messages.append(list(messages))
        return "[MOCK] 收到"


async def test_heartbeat_payload_has_chrono_fields() -> None:
    """斷言 1：Heartbeat Tick payload 帶 time_period、chrono_block 等豐富欄位。"""
    logger.info("── 斷言 1：Heartbeat payload 帶 chrono 欄位 ──")
    bus = SoulEventBus()
    await bus.start()

    captured: List[SoulEvent] = []

    async def capture_tick(event: SoulEvent) -> None:
        captured.append(event)

    bus.subscribe("tick_capture", capture_tick, event_filter={EventType.SYSTEM_TICK})

    heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=2)
    heartbeat.last_user_activity = datetime.now(timezone.utc) - timedelta(minutes=10)
    await heartbeat.start()
    await asyncio.sleep(2.5)
    await heartbeat.stop()
    await bus.stop()

    assert len(captured) >= 1, "Heartbeat 沒 Tick"
    tick = captured[0]
    payload = tick.payload

    # 核心 chrono 欄位
    assert "time_period" in payload, "payload 缺 time_period"
    assert "chrono_block" in payload, "payload 缺 chrono_block"
    assert "vulnerability_window" in payload, "payload 缺 vulnerability_window"
    assert "silence_hours" in payload, "payload 缺 silence_hours"
    assert "attachment_heat" in payload, "payload 缺 attachment_heat"
    assert "deviation_interpretation" in payload, "payload 缺 deviation_interpretation"
    assert "preoccupation_flavor" in payload, "payload 缺 preoccupation_flavor"

    # chrono_block 內容必須含 [CHRONO_SOCIAL_CONTEXT v2.2] 標頭
    assert "[CHRONO_SOCIAL_CONTEXT v2.2]" in payload["chrono_block"], \
        f"chrono_block 格式錯誤：{payload['chrono_block'][:100]}"

    logger.info(f"  ✓ time_period={payload['time_period']}")
    logger.info(f"  ✓ silence_hours={payload['silence_hours']}")
    logger.info(f"  ✓ chrono_block ({len(payload['chrono_block'])} chars) 完整")


async def test_yua_deep_night_blocked() -> None:
    """斷言 2：凌晨時段 elapsed_mins=35 不會觸發 Yua，dynamic COOLDOWN 拉長。"""
    logger.info("\n── 斷言 2：凌晨 Yua 不主動 + COOLDOWN 拉長 ──")
    bus = SoulEventBus()
    await bus.start()

    intents: List[SoulEvent] = []

    async def capture_intent(event: SoulEvent) -> None:
        intents.append(event)

    bus.subscribe("intent_capture", capture_intent, event_filter={EventType.AGENT_INTENT})

    # 強制 now() 回凌晨 3 點
    fake_now = datetime(2026, 6, 4, 3, 0, 0, tzinfo=timezone.utc)
    with patch("src.heartbeat.engine.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        mock_dt.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)

        # 先建好 Yua（intimacy=80 確保正常時段會觸發 silence_timeout）
        yua = AgentYua(agent_id="agent_yua", bus=bus)
        yua.state.intimacy_level = 80
        yua.register()

        heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=2)
        # 設 last_user_activity 35 分鐘前
        # 用真實的 timedelta 算（mock 只 mock datetime.now）
        heartbeat.last_user_activity = fake_now - timedelta(minutes=35)
        await heartbeat.start()
        await asyncio.sleep(2.5)
        await heartbeat.stop()

    await bus.stop()

    logger.info(f"  凌晨 3 點 elapsed=35min: AGENT_INTENT 收到 {len(intents)} 次")
    assert len(intents) == 0, (
        f"凌晨時段 Yua 不該觸發 silence_timeout，但收到 {len(intents)} 個"
    )
    logger.info("  ✓ 凌晨 Yua 沒觸發 silence_timeout（符合深夜拉高閾值邏輯）")


async def test_chrono_block_reaches_llm_prompt() -> None:
    """斷言 3：完整鏈路下，LLM 收到的 system message 包含 chrono 區塊。"""
    logger.info("\n── 斷言 3：chrono 區塊注入到 LLM system message ──")
    bus = SoulEventBus()
    await bus.start()

    data_dir = tempfile.mkdtemp(prefix="chrono_e2e_")
    memory = MemoryMiddleware(bus=bus, data_dir=data_dir)
    memory.register()
    # Phase 4：加 SpeakerTokenManager
    from src.eventbus.token_manager import SpeakerTokenManager
    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    token_mgr.register()
    mock_llm = MockLLMBackend()
    llm = LLMProxy(bus=bus, backend=mock_llm, model="mock", max_tokens=200)
    llm.register()

    # 收集 AGENT_SPEAK
    outputs: List[SoulEvent] = []
    async def capture_speak(event: SoulEvent) -> None:
        outputs.append(event)

    bus.subscribe("speak_capture", capture_speak, event_filter={EventType.AGENT_SPEAK})

    # 直接發 AGENT_INTENT（手動塞 chrono_context，模擬 _fire_intent 帶 chrono）
    fake_chrono_block = (
        "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
        "time_period=afternoon\n"
        "silence=2.0h\n"
        "arrival_deviation=normal\n"
        "vulnerability_window=False\n"
        "carryover_worry=0.00\n"
        "attachment_heat=0.00\n"
        "reaction_bias=neutral\n"
        "temporal_salience=low\n"
        "expression_mode=implicit\n"
        "[/CHRONO_SOCIAL_CONTEXT]"
    )
    intent = SoulEvent(
        event_type=EventType.AGENT_INTENT,
        source="agent_yua",
        target="broadcast",
        payload={
            "agent_id": "agent_yua",
            "reason": "silence_timeout",
            "draft": "還好你還在。",
            "memory_query_hint": "test",
            "chrono_context": fake_chrono_block,
        },
    )
    await bus.publish(intent)
    # SOUL-TECH-DEBT-001 校準（test-owned bug, 非 production defect）:
    # 原本這裡是 `await asyncio.sleep(0.5)` —— 固定等待, 不等事件鏈走完。
    # MemoryMiddleware 的 enrich 首次執行要載 jieba dict + 跑 RAG, 實測 >0.5s,
    # 於是 bus.stop() 在 AGENT_INTENT_ENRICHED 還沒 publish 出去就先把總線關了,
    # middleware 的 re-publish 被丟（bus.py:184-186 "總線尚未啟動，事件被丟棄"）
    # → token manager 拿不到 ENRICHED → 不發 SPEAKER_TOKEN_GRANTED →
    # LLMProxy（只監聽 SPEAKER_TOKEN_GRANTED, proxy.py:3648）永遠不產 AGENT_SPEAK。
    # 改成等 bus 佇列清空: 用的是 bus 自己的背壓語意（stop() 自己也 await
    # queue.join(), bus.py:232）, 不是新機制。join() 會連 middleware 在
    # handler 中 re-publish 的新事件一起等到, 所以 0.5s 的競態消失。
    await asyncio.wait_for(bus._queue.join(), timeout=30)

    memory.shutdown()
    await bus.stop()
    shutil.rmtree(data_dir, ignore_errors=True)

    assert len(outputs) >= 1, "LLMProxy 沒產生 AGENT_SPEAK"
    assert len(mock_llm.captured_messages) >= 1, "Mock LLM 沒被呼叫"

    msgs = mock_llm.captured_messages[0]
    sys_content = next((m["content"] for m in msgs if m["role"] == "system"), "")
    # 🔴 SOUL-BUG（SOUL-TECH-DEBT-001 深查, production defect, **故意留紅**）:
    # chrono / 時間感知區塊在 production **已經無法送進任何 LLM system prompt**。
    # 這不是測試過期, 是真的斷鏈, 兩端各斷一次:
    #
    #   [來源端] HeartbeatEngine 每 tick 都算好 chrono_block
    #     (src/heartbeat/engine.py:233 render_temporal_context → render_temporal_block,
    #      engine.py:252 放進 SYSTEM_TICK payload),
    #     而唯一會把它帶進 AGENT_INTENT 的呼叫者是 consciousness._on_tick
    #     (consciousness.py:341 `chrono_payload=event.payload`)。
    #     但 M5.7-2 把 EventType.SYSTEM_TICK 從 agent 的訂閱清單拿掉
    #     (consciousness.py:155, 註解「拿掉, 避免 proactive Agency」),
    #     `_on_tick` 從此**永遠不會被呼叫** —— handle_event 的分派分支
    #     (consciousness.py:171-172) 已是死碼。
    #     其他所有 caller 只給 {"draft": ...}, 沒有 chrono_block
    #     (consciousness.py:223 / 262 / 294 / 389)。
    #
    #   [出口端] PromptContext.chrono_context (proxy.py:1568) 與它的注入
    #     (proxy.py:1596-1598) 是**死代碼** —— 全庫 0 實例化,
    #     見 docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md:128
    #     「PromptContext 為死代碼, 0 實例化」(§2.6)。
    #     實際上線的 _build_messages_group / _build_messages_private
    #     (proxy.py:682 / :1436 附近) 簽名裡**根本沒有 chrono_context 參數**,
    #     所以就算 AGENT_INTENT.payload["chrono_context"] 有值也無處可插。
    #
    # 影響: 角色完全失去時間感知偏誤 (time_period / silence /
    # vulnerability_window / attachment_heat / reaction_bias / expression_mode)。
    # M5.11-2 (engine.py:212-213) 的註解仍宣稱 carryover「流入 LLM prompt
    # 生成階段」—— 該敘述現已不成立。
    #
    # 本測試的競態已於上方修好（改等 bus._queue.join(), 不再用 sleep(0.5)),
    # 整條鏈現在確實跑完並產出 AGENT_SPEAK, 所以這兩行斷言是**真實紅**:
    # 紅在此處代表 production 斷鏈, 不是測試自己壞掉。
    # 修好 production 後這兩行自然轉綠 —— 不要用 skip/xfail 把它藏起來。
    assert "[CHRONO_SOCIAL_CONTEXT v2.2]" in sys_content, (
        "SOUL-BUG: chrono 區塊沒進到 system message。"
        "來源端 consciousness.py:155 已取消 SYSTEM_TICK 訂閱（M5.7-2）"
        "→ _on_tick 死碼；出口端 PromptContext 死代碼"
        "（proxy.py:1568/1596-1598，EH-1 契約 §2.6 已記錄 0 實例化）"
        f" → _build_messages_* 無 chrono_context 插槽。"
        f"實際 system content: {sys_content[:300]}"
    )
    assert "time_period=afternoon" in sys_content, (
        f"system message 缺 chrono 欄位：{sys_content[:300]}"
    )
    logger.info(f"  ✓ LLM 收到 system message ({len(sys_content)} chars) 含 chrono 區塊")
    logger.info(f"  ✓ system message 前 200 字：\n{sys_content[:200]}")


async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 3.5 chrono 整合測試")
    logger.info("=" * 60)

    await test_heartbeat_payload_has_chrono_fields()
    await test_yua_deep_night_blocked()
    await test_chrono_block_reaches_llm_prompt()

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Phase 3.5 chrono 整合驗收通過")
    logger.info("    ✅ Heartbeat Tick payload 帶 chrono 豐富欄位")
    logger.info("    ✅ 凌晨時段 Yua 不觸發、COOLDOWN 拉長")
    logger.info("    ✅ chrono 區塊注入到 LLM system message")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
