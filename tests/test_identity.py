# -*- coding: utf-8 -*-
import asyncio
import json
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets

WS_URL = "ws://localhost:8000/ws"
SERVER_URL = "http://localhost:8000"

def check_server():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as r:
            data = json.loads(r.read())
            print(f"[server] status={data.get('status')}")
            return True
    except Exception as e:
        print(f"[server] NOT reachable: {e}")
        return False

async def send_and_get_response(text, agent_id, mode):
    results = []
    
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        payload = {
            "type": "USER_MESSAGE",
            "content": text,
            "user_id": "bryan_test",
            "mode": mode,
        }
        if mode == "private":
            payload["target_agent"] = agent_id
        else:
            payload["participants"] = [agent_id]
        
        print(f"  [send] mode={mode} agent={agent_id} text={text}")
        await ws.send(json.dumps(payload))
        
        # Wait for response
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    results.append(data)
                    break
            except asyncio.TimeoutError:
                continue
    
    return results

async def test_identity():
    print("=" * 60)
    print("Test: Identity Anchor")
    print("=" * 60)
    
    if not check_server():
        print("ERROR: Server not running!")
        return
    
    # Test 1: Yua "你是誰"
    print("\n[Test 1] 私聊 Yua: 你是誰")
    results = await send_and_get_response("你是誰", "agent_yua", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")
    
    await asyncio.sleep(1)
    
    # Test 2: Ruka "你是誰"
    print("\n[Test 2] 私聊 Ruka: 你是誰")
    results = await send_and_get_response("你是誰", "agent_ruka", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")
    
    await asyncio.sleep(1)
    
    # Test 3: Yua "你是 Ruka 嗎？"
    print("\n[Test 3] 私聊 Yua: 你是 Ruka 嗎？")
    results = await send_and_get_response("你是 Ruka 嗎？", "agent_yua", "private")
    if results:
        r = results[0]
        text = r.get("text", "")
        agent = r.get("agent_id", "")
        print(f"  [result] agent={agent} text={text[:100]}")
    else:
        print("  [FAIL] No response")
    
    print("\n" + "=" * 60)

if __name__ == "__main__":
    asyncio.run(test_identity())