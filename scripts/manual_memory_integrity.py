# -*- coding: utf-8 -*-
"""scripts/test_memory_integrity.py
測試記憶寫入完整性：
1. 清掉所有歷史
2. 私聊 Yua 送 5 條訊息
3. 驗證歷史條數 = sent * 2（user + assistant 各一條）
4. 確認沒有重複 user message
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

async def send_and_wait(text, agent_id, mode="private"):
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
        
        await ws.send(json.dumps(payload))
        
        deadline = time.time() + 20
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

def check_history_integrity(agent_id):
    """檢查歷史檔案是否完整"""
    from pathlib import Path
    conv_dir = Path(r'C:\Users\bbfcc\.local\bin\soul-os-harness\data\conversations')
    private_file = conv_dir / f"bryan_{agent_id}_private.json"
    
    if not private_file.exists():
        return False, 0, "File not found", [], False
    
    try:
        history = json.loads(private_file.read_text(encoding='utf-8'))
    except:
        return False, 0, "JSON parse error", [], False
    
    roles = [m.get("role") for m in history]
    
    # Check for duplicate user messages (sequential duplicates)
    has_sequential_dup = False
    for i in range(len(roles) - 1):
        if roles[i] == 'user' and roles[i+1] == 'user':
            has_sequential_dup = True
            break
    
    return True, len(history), f"user={roles.count('user')} assistant={roles.count('assistant')}", roles, has_sequential_dup

async def test_memory_integrity():
    safe_print("=" * 60)
    safe_print("Test: Memory Integrity")
    safe_print("=" * 60)
    
    if not check_server():
        safe_print("ERROR: Server not running!")
        return False
    
    # Step 1: 清掉所有歷史
    from pathlib import Path
    conv_dir = Path(r'C:\Users\bbfcc\.local\bin\soul-os-harness\data\conversations')
    for f in conv_dir.glob('*.json'):
        try:
            f.unlink()
        except:
            pass
    safe_print("[Step 1] History cleared")
    
    # Step 2: 送 5 條訊息
    agent_id = "agent_yua"
    messages = ["嗨", "今天好嗎", "你在做什麼", "想跟你聊天", "晚安"]
    sent_count = 0
    
    safe_print(f"[Step 2] Sending {len(messages)} messages to {agent_id}")
    
    for i, text in enumerate(messages):
        safe_print(f"  [{i+1}] Sending: {text}")
        results = await send_and_wait(text, agent_id)
        if results:
            resp = results[0]
            resp_text = resp.get('text', '')[:50].encode('ascii', 'replace').decode('ascii')
            safe_print(f"       Response: {resp_text}...")
            sent_count += 1
        else:
            safe_print(f"       [FAIL] No response")
        await asyncio.sleep(1)
    
    safe_print(f"[Step 3] Checking history file...")
    
    # Step 3: 驗證歷史
    ok, total_len, detail, roles, has_dup = check_history_integrity(agent_id)
    
    safe_print(f"  History file: bryan_{agent_id}_private.json")
    safe_print(f"  Total entries: {total_len}")
    safe_print(f"  Detail: {detail}")
    safe_print(f"  Sequential duplicate (user+user): {has_dup}")
    
    # Step 4: 驗證標準
    expected_len = sent_count * 2
    safe_print(f"\n[Step 4] Validation:")
    safe_print(f"  Expected entries: {expected_len} (sent_count={sent_count} * 2)")
    safe_print(f"  Actual entries: {total_len}")
    safe_print(f"  Match: {total_len == expected_len}")
    safe_print(f"  No sequential duplicate: {not has_dup}")
    
    if total_len == expected_len and not has_dup:
        safe_print("\n  [PASS] Memory integrity test passed!")
        return True
    else:
        safe_print("\n  [FAIL] Memory integrity test failed!")
        if has_dup:
            safe_print("  ISSUE: Sequential duplicate user messages detected")
        if total_len != expected_len:
            safe_print(f"  ISSUE: Entry count mismatch (expected {expected_len}, got {total_len})")
        return False

if __name__ == "__main__":
    result = asyncio.run(test_memory_integrity())
    sys.exit(0 if result else 1)