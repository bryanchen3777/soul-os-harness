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
# SOUL-BUG-003（Bry 2026-10-16 裁示）：除役 /inject/tick、加固 /inject/yua
# ─────────────────────────────────────────────────────────────────────


async def test_inject_yua_is_403_in_production_and_publishes_nothing():
    """生產環境下 /inject/yua 必須 403，且**一個事件都不能發到 bus**。

    🔴 為什麼這支要連 bus 一起斷言：/inject/yua 的內容是帶真實 draft 的
    AGENT_INTENT —— 打進來會**觸發一次真實付費 LLM 呼叫**並 `sleep(8.0)`。
    所以守衛不能只斷言 HTTP status，還必須證明「403 路徑完全沒碰 bus」，
    否則一個寫錯的閘門（例如放在 try 內被 except 吞掉）會變成靜默付費出口。

    ⚠️ 本測試**只走 403 路徑**，絕不打到 dev 分支 —— 故零 LLM 成本、
       零對外請求，符合 AGENTS.md 的測試紅線。
    """
    import os

    from fastapi.testclient import TestClient

    os.environ["SOUL_ENV"] = "production"
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
            assert resp.status_code == 403, (
                f"SOUL_ENV=production 時 /inject/yua 應回 403, 實際 {resp.status_code} "
                f"（body={resp.text[:200]}）—— 這是未加防護的付費 LLM 出口"
            )
        finally:
            bus.unsubscribe("test_inject_yua_guard")
            await bus.stop()

        await asyncio.sleep(0.2)
        assert seen == [], (
            f"403 路徑不應向 bus 發任何事件, 實際收到 {len(seen)} 筆: {seen}"
        )
    finally:
        os.environ.pop("SOUL_ENV", None)


def test_inject_yua_gate_is_placed_before_the_try_block():
    """把閘門放在 try 內等於沒有閘門 —— HTTPException 會被 `except Exception` 吞成 200。

    /inject/yua 的函式體有一個包住整段 publish 的 `try/except Exception`（它把
    錯誤轉成 `{"error": ...}` 回 200）。若有人日後「整理程式碼」把 403 閘門
    挪進 try 裡，行為測試（上一支）會轉紅；這支則在**還沒有人改錯之前**
    先釘住結構，讓 diff review 時一眼看出閘門被移動。
    """
    import inspect

    src = inspect.getsource(IOGateway._build_routes) if hasattr(IOGateway, "_build_routes") else ""
    if not src:  # pragma: no cover — 路由組裝函式名稱若變更則回退到檔案級檢查
        src = Path(__file__).resolve().parent.parent.joinpath(
            "src", "io", "gateway.py"
        ).read_text(encoding="utf-8")

    assert '"/inject/tick"' not in src, (
        "/inject/tick 已於 SOUL-BUG-003 除役，若要恢復請先重開工單評估"
        "無認證 + sleep(6.0) 佔用 handler 的暴露面"
    )
    assert '"/inject/yua"' in src, "/inject/yua 端點不見了；若真的除役請更新本測試"

    # 取 /inject/yua 函式本體
    body = src.split('async def inject_yua', 1)[1]
    gate_idx = body.find('SOUL_ENV')
    try_idx = body.find("try:")
    assert gate_idx != -1, "/inject/yua 缺少 SOUL_ENV 生產閘門"
    assert try_idx != -1, "/inject/yua 找不到 try 區塊（結構已變，請人工確認）"
    assert gate_idx < try_idx, (
        "SOUL_ENV 閘門必須在 try 之前：/inject/yua 的 `except Exception` 會把 "
        "HTTPException 吞成 200 + {'error': ...}，放進 try 等於沒有防護"
    )


if __name__ == "__main__":
    asyncio.run(main())