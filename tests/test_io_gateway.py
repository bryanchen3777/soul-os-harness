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


# ─────────────────────────────────────────────────────────────────────
# EI-SEC-P0-DEFAULT-OFF-IMPLEMENT-1：/inject/yua 除役（預設關閉）
# ─────────────────────────────────────────────────────────────────────
# 沿革：SOUL-BUG-003（Bry 2026-10-16 裁示）先為 /inject/yua 加生產 403 閘門；
# 本票直接除役路由（任何環境皆不可路由）。
#
# 核心斷言保留 SOUL-BUG-003 精神：/inject/yua 的內容曾是帶真實 draft 的
# AGENT_INTENT —— 打進來會**觸發一次真實付費 LLM 呼叫**並 `sleep(8.0)`。
# 所以除「404」之外，必須證明「零 bus 副作用」，否則一個寫錯的恢復
#（例如只刪閘門不刪 publish）會變成靜默付費出口。
#
# ⚠️ 本測試**只走 404 路徑**，絕不觸及任何 publish 分支 —— 故零 LLM 成本、
# 零對外請求，符合 AGENTS.md 的測試紅線。
import pytest


@pytest.mark.parametrize("env", ["dev", "production"])
async def test_inject_yua_is_404_in_any_env_and_publishes_nothing(env):
    """任何 SOUL_ENV 下 /inject/yua 皆 404，且**一個事件都不能發到 bus**。"""
    import os

    from fastapi.testclient import TestClient

    os.environ["SOUL_ENV"] = env
    try:
        bus = SoulEventBus()
        await bus.start()

        seen: list = []

        async def _collector(event: SoulEvent) -> None:
            seen.append(event)

        bus.subscribe(
            subscriber_id="test_inject_yua_guard",
            handler=_collector,
            event_filter={EventType.AGENT_INTENT},
        )

        gateway = IOGateway(bus)
        gateway.register()
        try:
            with TestClient(gateway.app) as client:
                resp = client.get("/inject/yua")
            assert resp.status_code == 404, (
                f"SOUL_ENV={env} 時 /inject/yua 應不可路由（404）, 實際 {resp.status_code} "
                f"（body={resp.text[:200]}）—— 這是未加防護的付費 LLM 出口"
            )
        finally:
            bus.unsubscribe("test_inject_yua_guard")
            await bus.stop()

        await asyncio.sleep(0.2)
        assert seen == [], (
            f"404 路徑不應向 bus 發任何事件, 實際收到 {len(seen)} 筆: {seen}"
        )
    finally:
        os.environ.pop("SOUL_ENV", None)


def test_inject_yua_route_is_absent_not_gated():
    """除役＝沒有路由註冊，不是 SOUL_ENV 閘門。

    decorator 與 handler 定義皆不得存在；也不得有 alias 重暴露。
    `/inject/yua` 字串只允許出現在除役註解／測試檔內。
    """
    src = Path(__file__).resolve().parent.parent.joinpath(
        "src", "io", "gateway.py"
    ).read_text(encoding="utf-8")

    assert '"/inject/tick"' not in src, (
        "/inject/tick 已於 SOUL-BUG-003 除役，若要恢復請先重開工單評估"
        "無認證 + sleep(6.0) 佔用 handler 的暴露面"
    )
    assert '@self.app.get("/inject/yua")' not in src, (
        "MUT-RED：/inject/yua 路由被重新註冊了（EI-SEC-P0 預設關閉要求無路由註冊）"
    )
    assert "async def inject_yua" not in src, (
        "MUT-RED：inject_yua handler 被恢復了；恢復需另開工單＋Owner 核准"
    )


if __name__ == "__main__":
    asyncio.run(main())