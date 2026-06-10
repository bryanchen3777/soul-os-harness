# test_memory_persist.py
# Soul OS — Phase 2 記憶持久化測試

import asyncio
import json
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.memory.store import MemoryStore

async def test():
    print("=== Phase 2 記憶持久化測試 ===\n")

    # 測試 1：MemoryStore 直接讀寫
    print("【測試 1】MemoryStore 直接讀寫")
    store = MemoryStore()
    initial_count = store.count()
    print(f"  初始訊息數: {initial_count}")

    store.append("session_agent_yua", "user", "你好 Yua，這是測試", "bryan")
    store.append("session_agent_yua", "assistant", "嗨，你好。", "agent_yua")
    store.append("session_agent_yua", "user", "你還記得我說什麼嗎？", "bryan")
    
    recent = store.get_recent("session_agent_yua", limit=10)
    print(f"  寫入後讀取 {len(recent)} 條:")
    for m in recent:
        print(f"    [{m['role']}] {m['content'][:50]}")

    # 測試 2：關閉後重啟
    print("\n【測試 2】關閉後重啟，確認資料還在")
    store.close()
    
    store2 = MemoryStore()
    after_reopen = store2.get_recent("session_agent_yua", limit=10)
    print(f"  重啟後讀取 {len(after_reopen)} 條:")
    for m in after_reopen:
        print(f"    [{m['role']}] {m['content'][:50]}")
    
    if len(after_reopen) >= 3:
        print("\n✅ SQLite 持久化成功！")
    else:
        print("\n❌ 持久化失敗")
        return

    # 測試 3：WebSocket 端到端
    print("\n【測試 3】WebSocket 端到端")
    try:
        import websockets
        async with websockets.connect("ws://localhost:8000/ws") as ws:
            msg = {
                "type": "USER_MESSAGE",
                "content": "你記得我說過什麼嗎？這是 Phase 2 測試。",
                "user_id": "bryan_test",
                "mode": "private",
                "target_agent": "agent_yua",
            }
            await ws.send(json.dumps(msg))
            print("  已發送，等待回應...")
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    print(f"  Yua 回應：{data.get('text','')[:80]}")
            except asyncio.TimeoutError:
                print("  ⚠️  逾時")
    except Exception as e:
        print(f"  ⚠️  WebSocket 失敗: {e}")

    # 測試 4：確認 DB 內容
    print("\n【測試 4】確認 DB 內容")
    store3 = MemoryStore()
    print(f"  DB 總訊息數: {store3.count()}")
    store3.close()
    
    print("\n=== 測試完成 ===")

if __name__ == "__main__":
    asyncio.run(test())
