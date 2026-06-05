"""
test_speaker_token.py
Soul OS — Phase 4: SpeakerTokenManager 測試

3 個場景：
  1. 單一 AGENT_INTENT_ENRICHED → 授予 token、re-publish SPEAKER_TOKEN_GRANTED
  2. AGENT_SPEAK 進來 → 釋放 token、emit SPEAKER_TOKEN_RELEASED
  3. 兩個 AGENT_INTENT_ENRICHED 同時進來 → 第一個授予、第二個排隊；第一個說完後第二個自動被授予
"""
import asyncio
import io
import logging
import sys
from pathlib import Path

# stdout already UTF-8
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus.token_manager import SpeakerTokenManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.token")


def _make_intent(agent_id: str, reason: str = "silence_timeout") -> SoulEvent:
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id,
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id,
            "reason": reason,
            "draft": f"hi from {agent_id}",
        },
    )


def _make_speak(agent_id: str, text: str = "ok") -> SoulEvent:
    return SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source=agent_id,
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={"agent_id": agent_id, "text": text},
    )


async def test_grant() -> None:
    """場景 1：單一申請 → 授予並 re-publish SPEAKER_TOKEN_GRANTED。"""
    logger.info("── 場景 1：單一申請授予 ──")
    bus = SoulEventBus()
    await bus.start()

    granted: list[SoulEvent] = []
    async def capture_granted(event: SoulEvent) -> None:
        granted.append(event)

    bus.subscribe(
        "capture_granted", capture_granted,
        event_filter={EventType.SPEAKER_TOKEN_GRANTED},
    )

    mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    mgr.register()

    await bus.publish(_make_intent("agent_yua"))
    await asyncio.sleep(0.2)

    assert len(granted) == 1, f"預期 1 個 GRANTED，收到 {len(granted)}"
    assert granted[0].payload.get("agent_id") == "agent_yua", \
        f"agent_id 應為 agent_yua，實際={granted[0].payload.get('agent_id')}"
    assert mgr._holder == "agent_yua", f"holder 應為 agent_yua，實際={mgr._holder}"
    logger.info(f"  ✓ agent_yua 獲得 token、re-publish SPEAKER_TOKEN_GRANTED")
    logger.info(f"  ✓ {mgr.stats()}")

    await bus.stop()


async def test_release() -> None:
    """場景 2：說完話釋放 token、emit SPEAKER_TOKEN_RELEASED。"""
    logger.info("\n── 場景 2：AGENT_SPEAK 釋放 token ──")
    bus = SoulEventBus()
    await bus.start()

    released: list[SoulEvent] = []
    async def capture_released(event: SoulEvent) -> None:
        released.append(event)

    bus.subscribe(
        "capture_released", capture_released,
        event_filter={EventType.SPEAKER_TOKEN_RELEASED},
    )

    mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    mgr.register()

    # 申請並授予
    await bus.publish(_make_intent("agent_yua"))
    await asyncio.sleep(0.1)
    assert mgr._holder == "agent_yua"

    # 說完話
    await bus.publish(_make_speak("agent_yua"))
    await asyncio.sleep(0.2)

    assert len(released) == 1, f"預期 1 個 RELEASED，收到 {len(released)}"
    assert released[0].payload.get("agent_id") == "agent_yua"
    assert released[0].payload.get("reason") == "spoke_done"
    assert mgr._holder is None, f"holder 應為 None，實際={mgr._holder}"
    logger.info(f"  ✓ SPEAKER_TOKEN_RELEASED emitted（reason=spoke_done）")
    logger.info(f"  ✓ {mgr.stats()}")

    # 非 holder 的 AGENT_SPEAK 應被忽略
    await bus.publish(_make_speak("agent_ruka"))
    await asyncio.sleep(0.1)
    assert len(released) == 1, f"非 holder 的 SPEAK 應被忽略，但多觸發了 {len(released)}"
    logger.info(f"  ✓ 非 holder 的 AGENT_SPEAK 靜默忽略（不刷屏）")

    await bus.stop()


async def test_queue() -> None:
    """場景 3：兩個申請同時進來 → 第一個授予、第二個排隊；說完後自動授予下一位。"""
    logger.info("\n── 場景 3：queue 與自動切換 ──")
    bus = SoulEventBus()
    await bus.start()

    granted: list[SoulEvent] = []
    async def capture_granted(event: SoulEvent) -> None:
        granted.append(event)

    bus.subscribe(
        "capture_granted", capture_granted,
        event_filter={EventType.SPEAKER_TOKEN_GRANTED},
    )

    mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=10.0)
    mgr.register()

    # 兩個同時申請：yua 拿到、ruka 排隊
    await bus.publish(_make_intent("agent_yua"))
    await bus.publish(_make_intent("agent_ruka"))
    await asyncio.sleep(0.2)

    assert mgr._holder == "agent_yua"
    assert len(granted) == 1, f"預期 1 個 GRANTED（yua 拿到），收到 {len(granted)}"
    assert len(mgr._queue) == 1, f"預期 queue=1，實際={len(mgr._queue)}"
    logger.info(f"  ✓ yua 拿到 token、ruka 排隊（queue={len(mgr._queue)}）")

    # yua 說完話，ruka 自動被授予
    await bus.publish(_make_speak("agent_yua"))
    await asyncio.sleep(0.2)

    assert mgr._holder == "agent_ruka", f"holder 應自動切到 ruka，實際={mgr._holder}"
    assert len(granted) == 2, f"預期 2 個 GRANTED，收到 {len(granted)}"
    assert granted[1].payload.get("agent_id") == "agent_ruka"
    logger.info(f"  ✓ yua 說完話後，ruka 自動獲得 token")

    await bus.stop()


async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 4 SpeakerTokenManager 測試")
    logger.info("=" * 60)

    await test_grant()
    await test_release()
    await test_queue()

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ Phase 4 Speaker Token 驗收通過")
    logger.info("    ✅ 單一申請 → 授予並 re-publish")
    logger.info("    ✅ AGENT_SPEAK → 釋放 + emit SPEAKER_TOKEN_RELEASED")
    logger.info("    ✅ Queue 自動切換 holder")
    logger.info("    ✅ 非 holder 的 SPEAK 靜默忽略")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
