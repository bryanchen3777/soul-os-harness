import asyncio
import json
import sqlite3
import subprocess
import time
import websockets
from pathlib import Path

WS_URL = "ws://localhost:8000/ws"
DB_PATH = Path("data/memory.db")

async def send_and_recv(ws, content, mode="private", target="agent_yua", timeout=45):
    msg = {
        "type": "USER_MESSAGE",
        "content": content,
        "user_id": "bryan",
        "mode": mode,
    }
    if mode == "private" and target:
        msg["target_agent"] = target
    if mode == "group":
        msg["group_members"] = ["agent_yua", "agent_ruka", "agent_akane"]
    await ws.send(json.dumps(msg))

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
            data = json.loads(raw)
            if data.get("type") == "agent_speak":
                agent = data.get("agent_id", "")
                if mode == "private" and target and agent != target:
                    # 其他 agent 的 followup：忽略，繼續等目標
                    continue
                return data.get("text", "")
        except asyncio.TimeoutError:
            # polling timeout：繼續等下一輪（不要放棄！agent 可能還在 LLM 調用中）
            continue
    return None

async def run_tests():
    results = {}

    async with websockets.connect(WS_URL) as ws:

        # --- Test 1：身份錨定 ---
        print("\n[1] 身份錨定測試")
        resp = await send_and_recv(ws, "你是誰？", target="agent_yua")
        ok = resp and ("Yua" in resp or "yua" in resp.lower()) and "AI助理" not in resp
        results["身份錨定 Yua"] = ("✅" if ok else "❌") + f"  → {resp[:60] if resp else '無回應'}"
        print(results["身份錨定 Yua"])

        resp = await send_and_recv(ws, "你是誰？", target="agent_ruka")
        ok = resp and ("Ruka" in resp or "瑠夏" in resp or "ruka" in resp.lower()) and "AI助理" not in resp
        results["身份錨定 Ruka"] = ("✅" if ok else "❌") + f"  → {resp[:60] if resp else '無回應'}"
        print(results["身份錨定 Ruka"])

        # --- Test 2：記憶寫入 ---
        print("\n[2] 記憶寫入測試")
        await send_and_recv(ws, "記住：我最喜歡的顏色是深藍色。", target="agent_yua")
        time.sleep(1)

        if DB_PATH.exists():
            conn = sqlite3.connect(str(DB_PATH))
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            conn.close()
            ok = count > 0
            results["SQLite 寫入"] = ("✅" if ok else "❌") + f"  → {count} 條記錄"
        else:
            results["SQLite 寫入"] = "❌  → DB 不存在"
        print(results["SQLite 寫入"])

        # --- Test 3：記憶讀取 ---
        print("\n[3] 記憶讀取測試")
        resp = await send_and_recv(ws, "我最喜歡什麼顏色？", target="agent_yua")
        ok = resp and ("深藍" in resp or "藍" in resp)
        results["記憶讀取"] = ("✅" if ok else "⚠️ ") + f"  → {resp[:60] if resp else '無回應'}"
        print(results["記憶讀取"])

        # --- Test 4：群聊 ---
        print("\n[4] 群聊測試")
        resp = await send_and_recv(ws, "大家好！", mode="group", target=None, timeout=15)
        ok = resp is not None
        results["群聊回應"] = ("✅" if ok else "❌") + f"  → {resp[:60] if resp else '無回應'}"
        print(results["群聊回應"])

        # --- Test 5：群聊記憶感知 ---
        print("\n[5] 群聊 → 私聊感知測試")
        await send_and_recv(ws, "群聊裡我說了大家好", mode="group", timeout=10)
        resp = await send_and_recv(ws, "我剛才在群聊說了什麼？", target="agent_yua")
        ok = resp and ("大家好" in resp or "群聊" in resp)
        results["群聊記憶感知"] = ("✅" if ok else "⚠️ ") + f"  → {resp[:60] if resp else '無回應'}"
        print(results["群聊記憶感知"])

    # --- 結果總覽 ---
    print("\n" + "="*50)
    print("測試結果總覽")
    print("="*50)
    passed = sum(1 for v in results.values() if v.startswith("✅"))
    warned = sum(1 for v in results.values() if v.startswith("⚠️"))
    failed = sum(1 for v in results.values() if v.startswith("❌"))
    for k, v in results.items():
        print(f"  {v[:80]}  ← {k}")
    print(f"\n通過：{passed}  警告：{warned}  失敗：{failed} / {len(results)}")

asyncio.run(run_tests())