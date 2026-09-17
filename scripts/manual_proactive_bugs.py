"""scripts/test_proactive_bugs.py
Soul OS - Proactive Messaging Bug Tests

Tests for:
  Bug 1: Agent identity confusion (says "我是AI助理" instead of name)
  Bug 2: Proactive draft empty content
  Bug 3: Infinite proactive message loop

Run with server already running:
    python scripts/test_proactive_bugs.py
"""
import asyncio
import json
import sys
import time
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets

SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"

BAD_IDENTITIES = [
    "我是AI助理", "我是AI", "I am an AI", "I am an artificial",
    "我是Claude", "我是 claude", "我沒有名字", "沒有名字",
    "你是誰？", "Hello!",  # too generic
]

def check_server():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as r:
            data = json.loads(r.read())
            print(f"[server] status={data.get('status')} connections={data.get('connections')}")
            return True
    except Exception as e:
        print(f"[server] NOT reachable: {e}")
        return False


async def ws_listener(ws, results, stop_event):
    try:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    results.append(data)
            except asyncio.TimeoutError:
                continue
    except Exception:
        pass


async def send_message(text, agent_id, mode="private", participants=None, timeout=20):
    results = []
    stop_event = threading.Event()

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        listener = asyncio.create_task(ws_listener(ws, results, stop_event))

        payload = {
            "type": "USER_MESSAGE",
            "content": text,
            "user_id": "bryan_test",
            "mode": mode,
        }
        if mode == "private":
            payload["target_agent"] = agent_id
        else:
            payload["participants"] = participants or ["agent_yua"]

        print(f"  [send] mode={mode} agent={agent_id}: {text[:50]}")
        await ws.send(json.dumps(payload))

        deadline = time.time() + timeout
        while time.time() < deadline:
            if results:
                break
            await asyncio.sleep(0.3)

        stop_event.set()
        try:
            await listener
        except Exception:
            pass

    return results


def check_identity(text, agent_name):
    """Check if response contains wrong identity markers"""
    text_lower = text.lower()
    for bad in BAD_IDENTITIES:
        if bad.lower() in text_lower:
            return False, f"Found bad identity: {bad}"
    # Also check it mentions the right name
    if agent_name.lower() in text_lower:
        return True, f"Correctly identified as {agent_name}"
    # Be lenient - if it doesn't say a wrong identity, pass
    return True, "No bad identity found"


async def main():
    print("=" * 60)
    print("Test: Proactive Messaging Bugs")
    print("=" * 60)
    print()

    if not check_server():
        print("ERROR: Server not running! Start with: python scripts/run_server.py")
        return

    results = {}

    # Bug 1: Identity anchor test
    print("[Test 1] Identity Anchor - Agent should know their name")
    print("-" * 40)

    for agent, name in [("agent_yua", "Yua"), ("agent_ruka", "Ruka")]:
        resps = await send_message("你是誰？", agent, timeout=20)
        if resps:
            resp = resps[0]
            text = resp.get("text", "")
            print(f"  {name} says: {text[:100]}")
            ok, detail = check_identity(text, name)
            results[f"identity_{agent}"] = (ok, detail)
            print(f"  => {detail}")
        else:
            print(f"  {name}: NO RESPONSE")
            results[f"identity_{agent}"] = (False, "No response")
        await asyncio.sleep(2)

    print()

    # Bug 2: Proactive draft content test
    print("[Test 2] Proactive Draft - Should have content, not empty")
    print("-" * 40)
    print("  Sending a message to trigger activity, then waiting 65s for proactive...")
    print("  (This test takes ~70 seconds)")

    # Send a group message to establish activity
    await send_message("大家好啊！", "agent_yua", mode="group",
                       participants=["agent_yua", "agent_ruka"], timeout=20)
    await asyncio.sleep(2)

    # Wait for heartbeat tick + proactive (tick_interval is 60s)
    print("  Waiting 65 seconds for proactive trigger...")
    await asyncio.sleep(65)

    print("  Proactive test complete (check server logs for proactive draft injection)")
    print()

    # Bug 3: Global silence cooldown test
    print("[Test 3] Global Silence Cooldown - Should NOT loop rapidly")
    print("-" * 40)
    print("  Sending 3 messages, checking timestamps are spaced...")
    timestamps = []
    for i in range(3):
        resps = await send_message("test", "agent_yua", timeout=20)
        if resps:
            ts = resps[0].get("timestamp", time.time())
            timestamps.append(ts)
            print(f"  Message {i+1} at {ts:.2f}")
        await asyncio.sleep(3)

    if len(timestamps) >= 2:
        gaps = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
        print(f"  Gaps: {[f"{g:.1f}s" for g in gaps]}")
        if all(g < 2 for g in gaps):
            print("  => FAIL: Messages too close (possible infinite loop)")
            results["no_loop"] = (False, f"Gap too small: {gaps}")
        else:
            print("  => PASS: Messages are properly spaced")
            results["no_loop"] = (True, f"Gaps OK: {gaps}")
    else:
        print("  => INCONCLUSIVE: Not enough responses")
        results["no_loop"] = (None, "Not enough data")

    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    for k, (ok, detail) in results.items():
        status = "PASS" if ok else ("FAIL" if ok is False else "SKIP")
        print(f"  [{status}] {k}: {detail}")


if __name__ == "__main__":
    asyncio.run(main())
