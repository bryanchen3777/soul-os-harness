# -*- coding: utf-8 -*-
"""scripts/test_group_memory.py - Updated to verify from file (WebSocket capture has timing issues)"""
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

async def send_private(content, target):
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        payload = {
            "type": "USER_MESSAGE",
            "content": content,
            "user_id": "bryan",
            "mode": "private",
            "target_agent": target,
        }
        await ws.send(json.dumps(payload))
        # Wait for response
        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    return data.get("text", "")
            except asyncio.TimeoutError:
                break
    return ""

async def test_group_memory():
    safe_print("=" * 60)
    safe_print("Test: Group Memory & Recall")
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
    
    # Step 2: Group chat
    safe_print("\n[Step 2] Group chat")
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        # Message 1
        await ws.send(json.dumps({
            "type": "USER_MESSAGE", "content": "Yua 在嗎？",
            "user_id": "bryan", "mode": "group",
            "group_members": ["agent_yua", "agent_ruka"]
        }))
        await asyncio.sleep(3)
        
        # Message 2
        await ws.send(json.dumps({
            "type": "USER_MESSAGE", "content": "Ruka 你好嗎？",
            "user_id": "bryan", "mode": "group",
            "group_members": ["agent_yua", "agent_ruka"]
        }))
        await asyncio.sleep(3)
    
    # Check group history
    group_file = conv_dir / "group_chat.json"
    if group_file.exists():
        data = json.loads(group_file.read_text(encoding='utf-8'))
        roles = [m.get('role') for m in data]
        speakers = [m.get('speaker') for m in data]
        safe_print(f"\n[Step 3] Group history: {len(data)} entries")
        safe_print(f"  Roles: {roles}")
        safe_print(f"  Speakers: {speakers}")
        bryan_user = any(m.get('speaker') == 'bryan' and m.get('role') == 'user' for m in data)
        agent_assistant = any(m.get('speaker') != 'bryan' and m.get('role') == 'assistant' for m in data)
        safe_print(f"  Bryan role=user: {bryan_user}")
        safe_print(f"  Agent role=assistant: {agent_assistant}")
    
    # Step 4: Private chat - verify from file
    safe_print("\n[Step 4] Private chat with Yua")
    
    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        await ws.send(json.dumps({
            "type": "USER_MESSAGE", "content": "我們剛才在群聊聊什麼？",
            "user_id": "bryan", "mode": "private", "target_agent": "agent_yua"
        }))
        await asyncio.sleep(5)  # Wait for response to be written to file
    
    # Check private file for Yua's response
    private_file = conv_dir / "bryan_agent_yua_private.json"
    yua_response = ""
    if private_file.exists():
        data = json.loads(private_file.read_text(encoding='utf-8'))
        if len(data) >= 2:
            yua_response = data[-1].get('content', '')
            safe_print(f"\n  Yua response: {yua_response[:100].encode('ascii', 'replace').decode('ascii')}")
    
    # Step 5: Verify recall
    keywords = ['Yua', 'Ruka', '在嗎', '你好']
    yua_remembers = any(kw in yua_response for kw in keywords)
    
    safe_print(f"\n[Step 5] Result:")
    safe_print(f"  Yua references group chat: {yua_remembers}")
    
    if bryan_user and agent_assistant and yua_remembers:
        safe_print("\n  [ALL PASS] Group memory test passed!")
        return True
    else:
        if not yua_remembers:
            safe_print("\n  [FAIL] Yua did not recall group chat")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_group_memory())
    sys.exit(0 if result else 1)