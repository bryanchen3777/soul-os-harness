"""scripts/trigger_yua.py — 觸發 Yua 主動說話，測試 Live Feed"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.agent.consciousness import AgentYua
from src.io.gateway import IOGateway
from src.eventbus.token_manager import SpeakerTokenManager
from src.memory.middleware import MemoryMiddleware

async def main():
    bus = SoulEventBus()
    await bus.start()

    mw = MemoryMiddleware(bus, data_dir="data/memory")
    mw.register()
    token_mgr = SpeakerTokenManager(bus)
    token_mgr.register()

    gateway = IOGateway(bus)
    gateway.register()

    yua = AgentYua("agent_yua", bus)
    yua.state.intimacy_level = 80
    yua.register()

    # 手動發 SYSTEM_TICK，elapsed_mins=35（觸發 Yua silence_timeout）
    from datetime import datetime, timezone
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
            "chrono_block": "",
        },
    )
    print("Publishing SYSTEM_TICK (elapsed_mins=35)...")
    await bus.publish(tick)
    await asyncio.sleep(5.0)
    print("Done.")
    await bus.stop()

if __name__ == "__main__":
    asyncio.run(main())