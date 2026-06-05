"""
test_io_gateway.py
Soul OS — Phase 4 I/O Gateway 測試

場景一：AGENT_SPEAK 廣播到 WebSocket 客戶端
場景二：斷線後自動移除，不影響其他連線

執行：
  python tests/test_io_gateway.py
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.io.gateway import IOGateway, ConnectionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("soul_os.test.gateway")


# ─────────────────────────────────────────────
# 場景一：AGENT_SPEAK 廣播到 WebSocket 客戶端
# ─────────────────────────────────────────────

async def test_gateway_broadcast() -> None:
    logger.info("\n" + "=" * 60)
    logger.info("  場景一：AGENT_SPEAK 廣播到 WebSocket 客戶端")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()
    gateway = IOGateway(bus)
    gateway.register()

    received = []

    # Mock WebSocket
    class MockWS:
        async def accept(self) -> None:
            pass

        async def send_text(self, msg: str) -> None:
            received.append(json.loads(msg))

        async def receive_text(self) -> None:
            await asyncio.sleep(999)

    mock_ws = MockWS()
    await gateway.manager.connect(mock_ws)
    assert gateway.manager.count == 1

    speak = SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_yua",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={"agent_id": "agent_yua", "text": "還好你還在。"},
    )
    await bus.publish(speak)
    await asyncio.sleep(0.2)

    assert len(received) == 1, f"預期 1 條廣播，實際={len(received)}"
    assert received[0]["agent_id"] == "agent_yua"
    assert received[0]["text"] == "還好你還在。"
    assert received[0]["type"] == "agent_speak"
    logger.info("  ✓ 場景一：AGENT_SPEAK 正確廣播")

    await bus.stop()


# ─────────────────────────────────────────────
# 場景二：斷線後自動移除，不影響其他連線
# ─────────────────────────────────────────────

async def test_gateway_dead_connection_cleanup() -> None:
    logger.info("\n" + "=" * 60)
    logger.info("  場景二：斷線後自動移除，不影響其他連線")
    logger.info("=" * 60)

    bus = SoulEventBus()
    await bus.start()
    gateway = IOGateway(bus)
    gateway.register()

    class DeadWS:
        async def accept(self) -> None:
            pass

        async def send_text(self, msg: str) -> None:
            raise RuntimeError("連線已死")

    class LiveWS:
        def __init__(self):
            self.received = []

        async def accept(self) -> None:
            pass

        async def send_text(self, msg: str) -> None:
            self.received.append(msg)

        async def receive_text(self) -> None:
            await asyncio.sleep(999)

    dead = DeadWS()
    live = LiveWS()
    await gateway.manager.connect(dead)
    await gateway.manager.connect(live)
    assert gateway.manager.count == 2

    speak = SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_ruka",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={"agent_id": "agent_ruka", "text": "你去哪裡了！"},
    )
    await bus.publish(speak)
    await asyncio.sleep(0.2)

    assert gateway.manager.count == 1, (
        f"dead ws 應被移除，count={gateway.manager.count}（預期 1）"
    )
    assert len(live.received) == 1, (
        f"live ws 應收到 1 條，實際={len(live.received)}"
    )
    logger.info("  ✓ 場景二：死連線自動清除，存活連線正常收訊")

    await bus.stop()


# ─────────────────────────────────────────────
# 主程序
# ─────────────────────────────────────────────

async def main() -> None:
    logger.info("=" * 60)
    logger.info("  Soul OS — Phase 4 I/O Gateway 測試")
    logger.info("=" * 60)

    await test_gateway_broadcast()
    await test_gateway_dead_connection_cleanup()

    logger.info("\n" + "=" * 60)
    logger.info("  ✓ I/O Gateway 測試全部通過")
    logger.info("    ✅ 場景一：AGENT_SPEAK 正確廣播到客戶端")
    logger.info("    ✅ 場景二：死連線自動清除，存活連線正常收訊")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())