#!/usr/bin/env python3
"""Trace the full event flow from USER_MESSAGE to AGENT_SPEAK"""
import sys, asyncio
sys.path.insert(0, ".")

async def test_flow():
    from src.eventbus import SoulEventBus
    from src.eventbus.schema import EventType, SoulEvent, EventPriority
    from src.memory.middleware import MemoryMiddleware
    from src.eventbus.token_manager import SpeakerTokenManager
    from src.agent.registry import get_agent_class
    from src.agent.speaker_token import SpeakerTokenBus

    print("=== Building event bus ===")
    bus = SoulEventBus()
    await bus.start()

    print("=== Adding Memory Middleware ===")
    mw = MemoryMiddleware(bus=bus, data_dir="data/memory")
    mw.register()

    print("=== Adding Token Manager ===")
    token_mgr = SpeakerTokenManager(bus=bus, token_timeout_secs=120.0)
    token_mgr.register()

    print("=== Adding Speaker Token Bus ===")
    stb = SpeakerTokenBus(cooldown_secs=4.0)

    print("=== Adding Agent ===")
    agent_cls = get_agent_class("AgentYua")
    agent = agent_cls("agent_yua", bus, speaker_token_bus=stb)
    agent.register()
    print("  Registered agent_yua")

    print()
    print("=== Bus Subscribers ===")
    for sid in bus.get_subscribers():
        print("  " + sid)

    print()
    print("=== Publishing USER_MESSAGE ===")
    event = SoulEvent(
        event_type=EventType.USER_MESSAGE,
        source="bryan_test",
        target="agent_yua",
        priority=EventPriority.HIGH,
        payload={
            "content": "test message",
            "user_id": "bryan_test",
            "target_agent": "agent_yua",
            "mode": "private",
            "participants": None,
        },
    )
    await bus.publish(event)
    print("Published USER_MESSAGE")

    print()
    print("=== Waiting 15s for processing ===")
    await asyncio.sleep(15)

    print()
    print("=== Bus Stats ===")
    stats = bus.get_stats()
    for k, v in sorted(stats.items()):
        print("  " + str(k) + ": " + str(v))

    await bus.stop()

if __name__ == "__main__":
    asyncio.run(test_flow())
