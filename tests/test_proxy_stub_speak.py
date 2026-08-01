"""
test_proxy_stub_speak.py
hotfix #11 (2026-07-16 Bry 拍板): proxy.py finally 區塊補發 stub AGENT_SPEAK 測試

回歸測試：
  場景 1: LLM 回 valid JSON 但 text="" audio_text="こんにちは" emotion="teasing_care"
           → 二次空檢查觸發 → return
           → finally 補發 stub AGENT_SPEAK (is_stub=True, source=agent_id)
           → stub 帶 tts_enabled=False / audio_text="" / text="" / is_stub=True
           → 下游 consumer 都能 skip (FishTTS tts_enabled=False, Memory text 空,
                                       IOGateway/ChannelRouter is_stub=True)

  場景 2: LLM 拋 exception
           → 同樣觸發 finally 補發 stub

  場景 3 (negative): LLM 回 valid JSON 且 text 有內容
           → 走正常 AGENT_SPEAK 路徑,沒 stub (stub 只在失敗時補)

驗收:
  - stub AGENT_SPEAK 一定被 bus 收到
  - stub 的 source == agent_id (讓 consciousness._on_agent_speak listener reset _pending)
  - stub 的 is_stub=True
  - stub 不會誤觸發 TTS/Memory 寫入 (用空欄位 + flag 雙重保險)

Bry 提醒:
  "stub 發出去的目的只是讓 _pending 能 reset、避免 listener 卡死,
   不是真的要讓 Fish TTS 去合成一段空氣或者讓 Memory 系統記一筆空白對話。
   改完之後除了跑 5 次卡死測試, 也順手確認一下 stub 事件有沒有意外
   讓 TTS 或 Memory 模組多跑了一次不該跑的流程。"
"""
import asyncio
import io
import logging
import sys
from pathlib import Path
from typing import Dict, List

# stdout already UTF-8
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.llm.proxy import LLMProxy, LLMBackend

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.stub_speak")


class TextEmptyAudioOKBackend(LLMBackend):
    """永遠回 text="" 但 audio_text="こんにちは" (有效 JSON)
    這是 hotfix #11 要修的關鍵場景。
    """

    async def complete(self, messages, model, max_tokens, temperature, **kwargs) -> str:
        return (
            '{"text": "", '
            '"audio_text": "こんにちは、元気ですか？", '
            '"emotion": "teasing_care"}'
        )


class NormalBackend(LLMBackend):
    """正常回 text + audio_text + emotion"""

    async def complete(self, messages, model, max_tokens, temperature, **kwargs) -> str:
        return (
            '{"text": "你好呀。\\n（你好呀。）", '
            '"audio_text": "こんにちは。", '
            '"emotion": "teasing_care"}'
        )


class ExceptionBackend(LLMBackend):
    """LLM 拋 exception"""

    async def complete(self, messages, model, max_tokens, temperature, **kwargs) -> str:
        raise RuntimeError("minimax 502 Bad Gateway (mock)")


def _make_token_granted(agent_id: str = "agent_mahiru", user_id: str = "bryan") -> SoulEvent:
    return SoulEvent(
        event_type=EventType.SPEAKER_TOKEN_GRANTED,
        source="token_manager",
        target="llm_proxy",
        priority=EventPriority.NORMAL,
        session_id=f"session_{user_id}_{agent_id}",
        payload={
            "agent_id": agent_id,
            "user_id": user_id,
            "reason": "user_message",
            "mode": "private",
            "draft": "你好",  # user 訊息
            "target_channel": "web",
            "target_user_id": user_id,
        },
    )


def _collect_speak_events(bus: SoulEventBus) -> List[SoulEvent]:
    """裝訂閱,把所有 AGENT_SPEAK 收到一 list"""
    captured: List[SoulEvent] = []

    async def _handler(event: SoulEvent) -> None:
        captured.append(event)

    bus.subscribe("test_stub_collector", _handler, event_filter={EventType.AGENT_SPEAK})
    return captured


async def _wait_for_speak(captured: List[SoulEvent], timeout: float = 5.0) -> bool:
    """等 AGENT_SPEAK 出現在 captured list (或 timeout)"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if captured:
            return True
        await asyncio.sleep(0.05)
    return False


async def test_text_empty_audio_ok_triggers_stub():
    """場景 1: text="" + audio_text="こんにちは" → 補 stub AGENT_SPEAK"""
    bus = SoulEventBus()
    await bus.start()
    captured = _collect_speak_events(bus)
    proxy = LLMProxy(bus=bus, backend=TextEmptyAudioOKBackend(), model="mock")
    proxy.register()

    # 送 SPEAKER_TOKEN_GRANTED
    await bus.publish(_make_token_granted())

    # 等 stub 觸發
    assert await _wait_for_speak(captured, timeout=5.0), \
        "no AGENT_SPEAK event received within 5s"
    assert len(captured) == 1, \
        f"expected 1 AGENT_SPEAK (stub), got {len(captured)}"

    stub = captured[0]
    assert stub.source == "agent_mahiru", \
        f"stub source must equal agent_id for _pending reset, got {stub.source}"
    assert stub.payload.get("is_stub") is True, \
        f"stub must have is_stub=True, got {stub.payload.get('is_stub')}"
    assert stub.payload.get("tts_enabled") is False, \
        f"stub must have tts_enabled=False (防 FishTTS 合成空氣), got {stub.payload.get('tts_enabled')}"
    assert stub.payload.get("text") == "", \
        f"stub text should be empty, got {stub.payload.get('text')!r}"
    assert stub.payload.get("audio_text") == "", \
        f"stub audio_text should be empty, got {stub.payload.get('audio_text')!r}"
    assert stub.payload.get("emotion") == "", \
        f"stub emotion should be empty, got {stub.payload.get('emotion')!r}"
    assert stub.payload.get("stub_reason") == "llm_failed", \
        f"stub_reason should be 'llm_failed', got {stub.payload.get('stub_reason')!r}"
    logger.info(
        f"[TEST PASS] 場景 1: stub AGENT_SPEAK 觸發, "
        f"is_stub={stub.payload.get('is_stub')}, "
        f"source={stub.source}, tts_enabled={stub.payload.get('tts_enabled')}"
    )
    await bus.stop()


async def test_llm_exception_triggers_stub():
    """場景 2: LLM 拋 exception → finally 補 stub"""
    bus = SoulEventBus()
    await bus.start()
    captured = _collect_speak_events(bus)
    proxy = LLMProxy(bus=bus, backend=ExceptionBackend(), model="mock")
    proxy.register()

    await bus.publish(_make_token_granted())

    # ExceptionBackend 拋錯 → 走 _complete_with_retry 的 3 次 retry → 全部失敗
    # → generated_text = "" (or None) → 二次空檢查 → return → finally stub
    # 或 Exception 直接 raise 進 finally (沒 try 攔?) → 也走 finally
    assert await _wait_for_speak(captured, timeout=10.0), \
        "no AGENT_SPEAK stub after LLM exception"
    assert len(captured) == 1, \
        f"expected 1 stub, got {len(captured)}"

    stub = captured[0]
    assert stub.payload.get("is_stub") is True
    assert stub.source == "agent_mahiru"
    logger.info(f"[TEST PASS] 場景 2: exception 觸發 stub")
    await bus.stop()


async def test_normal_response_does_not_trigger_stub():
    """場景 3 (negative): LLM 正常回 → 走正常 AGENT_SPEAK,沒 stub"""
    bus = SoulEventBus()
    await bus.start()
    captured = _collect_speak_events(bus)
    proxy = LLMProxy(bus=bus, backend=NormalBackend(), model="mock")
    proxy.register()

    await bus.publish(_make_token_granted())

    assert await _wait_for_speak(captured, timeout=5.0)
    assert len(captured) == 1, \
        f"expected 1 normal AGENT_SPEAK, got {len(captured)}"

    speak = captured[0]
    assert speak.payload.get("is_stub") is None or speak.payload.get("is_stub") is False, \
        f"normal response should NOT have is_stub=True, got {speak.payload.get('is_stub')!r}"
    assert speak.payload.get("text") != "", \
        f"normal response should have non-empty text, got {speak.payload.get('text')!r}"
    assert speak.payload.get("audio_text") != "", \
        f"normal response should have non-empty audio_text, got {speak.payload.get('audio_text')!r}"
    logger.info(
        f"[TEST PASS] 場景 3: 正常路徑沒 stub, "
        f"text={speak.payload.get('text')[:30]!r}"
    )
    await bus.stop()


async def main():
    # 場景 1: 核心場景
    try:
        await test_text_empty_audio_ok_triggers_stub()
    except Exception as e:
        logger.error(f"[TEST FAIL] 場景 1 失敗: {e}", exc_info=True)
        raise

    # 場景 2: exception 場景
    try:
        await test_llm_exception_triggers_stub()
    except Exception as e:
        logger.error(f"[TEST FAIL] 場景 2 失敗: {e}", exc_info=True)
        raise

    # 場景 3: negative 測試
    try:
        await test_normal_response_does_not_trigger_stub()
    except Exception as e:
        logger.error(f"[TEST FAIL] 場景 3 失敗: {e}", exc_info=True)
        raise

    logger.info("=" * 60)
    logger.info("ALL 3 場景 PASS, hotfix #11 邏輯正確")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
