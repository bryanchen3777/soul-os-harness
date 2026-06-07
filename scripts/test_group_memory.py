# -*- coding: utf-8 -*-
"""scripts/test_group_memory.py
Group memory test - verify agent can remember group context
"""
import asyncio
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import websockets

WS_URL = "ws://localhost:8000/ws"
SERVER_URL = "http://localhost:8000"

def safe_print(msg):
    try:
        print(msg)
    except:
        print(msg.encode('ascii', 'replace').decode('ascii'))

def check_server():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as r:
            data = json.loads(r.read())
            return data.get('status') == 'ok'
    except:
        return False

async def send_and_collect(content, mode="group", target=None, members=None):
    responses = []
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        payload = {
            "type": "USER_MESSAGE",
            "content": content,
            "user_id": "bryan",
            "mode": mode,
        }
        if target:
            payload["target_agent"] = target
        if members:
            payload["group_members"] = members
        
        await ws.send(json.dumps(payload))
        
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    agent = data.get("agent_id", "").replace("agent_", "").capitalize()
                    text = data.get("text", "")[:80]
                    responses.append((agent, text))
                    safe_print(f"  [{agent}] {text.encode('ascii', 'replace').decode('ascii')}")
            except asyncio.TimeoutError:
                break
    return responses

async def test_group_memory():
    safe_print("=" * 60)
    safe_print("Test: Group Memory")
    safe_print("=" * 60)
    
    if not check_server():
        safe_print("ERROR: Server not running!")
        return False
    
    # Clear history
    conv_dir = Path(r'C:\Users\bbfcc\.local\bin\soul-os-harness\data\conversations')
    for f in conv_dir.glob('*.json'):
        try:
            f.unlink()
        except:
            pass
    safe_print("[Step 1] History cleared")
    
    # Test group chat
    safe_print("\n[Step 2] Group chat: Bryan with Yua and Ruka")
    
    safe_print("\n  Bryan: Yua 在嗎？")
    await send_and_collect("Yua 在嗎？", mode="group", members=["agent_yua", "agent_ruka"])
    await asyncio.sleep(2)
    
    safe_print("\n  Bryan: Ruka 你好嗎？")
    await send_and_collect("Ruka 你好嗎？", mode="group", members=["agent_yua", "agent_ruka"])
    await asyncio.sleep(2)
    
    # Check group history
    group_file = conv_dir / "group_chat.json"
    if group_file.exists():
        data = json.loads(group_file.read_text(encoding='utf-8'))
        safe_print(f"\n[Step 3] Group history: {len(data)} entries")
        roles = [m.get('role') for m in data]
        speakers = [m.get('speaker') for m in data]
        safe_print(f"  Roles: {roles}")
        safe_print(f"  Speakers: {speakers}")
        
        # Check role correctness
        user_count = roles.count('user')
        assistant_count = roles.count('assistant')
        safe_print(f"  user={user_count} assistant={assistant_count}")
    
    safe_print("\n" + "=" * 60)
    return True

if __name__ == "__main__":
    result = asyncio.run(test_group_memory())
    sys.exit(0 if result else 1)