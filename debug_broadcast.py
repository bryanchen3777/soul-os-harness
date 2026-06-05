import sys
sys.path.insert(0, '.')
import asyncio
import json
from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.io.gateway import IOGateway

async def main():
    bus = SoulEventBus()
    await bus.start()

    gateway = IOGateway(bus)
    gateway.register()

    # Manually connect a mock WS to capture events
    received = []
    class MockWS:
        async def accept(self): pass
        async def send_text(self, msg):
            print(f"MOCK WS GOT: {msg}")
            received.append(json.loads(msg))
        async def receive_text(self):
            await asyncio.sleep(999)

    mock_ws = MockWS()
    await gateway.manager.connect(mock_ws)

    # Publish AGENT_SPEAK directly
    speak = SoulEvent(
        event_type=EventType.AGENT_SPEAK,
        source="agent_yua",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload={"agent_id": "agent_yua", "text": "還好你還在。"},
    )
    print("Publishing AGENT_SPEAK directly...")
    await bus.publish(speak)
    await asyncio.sleep(1.0)

    print(f"Received: {len(received)} messages")
    await bus.stop()

if __name__ == "__main__":
    asyncio.run(main())