"""
scripts/test_memory_split.py
測試群聊/私聊記憶分離

流程：
1. 群聊送「大家好」「Ruka 你好啊」→ 等各 Agent 說完
2. 私聊問 Yua「你知道 Ruka 剛才說什麼嗎」→ 預期知道群聊摘要
3. 問 Ruka「你知道我跟 Yua 私聊嗎」→ 預期知道有私聊但不知道內容
"""
import asyncio
import json
import websockets


async def wait_for_response(ws, target_agent=None, timeout=45):
    """等 agent_speak，可選只等特定 agent"""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            if data.get("type") == "agent_speak":
                if target_agent is None or data.get("agent_id") == target_agent:
                    return data
        except asyncio.TimeoutError:
            continue
    return None


async def send_group(ws, text):
    """發群聊，等回應（可能多個），印出來"""
    print(f"  [送出] {text}")
    await ws.send(json.dumps({
        "type": "USER_MESSAGE", "content": text,
        "user_id": "bryan", "mode": "group"
    }))
    responses = []
    deadline = asyncio.get_event_loop().time() + 8
    while asyncio.get_event_loop().time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
            if data.get("type") == "agent_speak":
                agent = data.get("agent_id", "?")
                content = data.get("text", "")[:50]
                print(f"    → {agent}: {content}")
                responses.append(data)
        except asyncio.TimeoutError:
            break
    return responses


async def send_private(ws, agent_id, text):
    """發私聊，等指定 agent 的回應"""
    print(f"  [送出→{agent_id}] {text}")
    await ws.send(json.dumps({
        "type": "USER_MESSAGE", "content": text,
        "user_id": "bryan", "mode": "private",
        "target_agent": agent_id
    }))
    data = await wait_for_response(ws, target_agent=agent_id, timeout=60)
    if data:
        print(f"    → {data.get('agent_id', '?')}: {data.get('text', '')[:80]}")
    else:
        print(f"    → (無回應)")
    return data


async def run_test():
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        print("=== 測試：群聊/私聊記憶分離 ===\n")

        # ── Step 1: 群聊送話 ─────────────────────────
        print("【Step 1】群聊說話，等 Agent 回應")
        await send_group(ws, "大家好")
        await asyncio.sleep(0.5)
        await send_group(ws, "Ruka 你好啊")
        await asyncio.sleep(2)  # 確保寫入磁碟完成

        # ── Step 2: 私聊問 Yua ───────────────────────
        print("\n【Step 2】私聊問 Yua")
        yua = await send_private(ws, "agent_yua", "你知道 Ruka 剛才說什麼嗎？")

        # ── Step 3: 私聊問 Ruka ──────────────────────
        print("\n【Step 3】私聊問 Ruka")
        ruka = await send_private(ws, "agent_ruka", "嗨～你現在跟誰在對話？")

        # ── 判定 ─────────────────────────────────────
        print("\n=== 結果 ===")
        yua_t = yua.get("text", "")
        ruka_t = ruka.get("text", "")

        yua_ok = any(k in yua_t for k in ["Ruka", "你好", "知道", "說"])
        print(f"  Yua 知道群聊內容：{'✅' if yua_ok else '❌'}")
        print(f"    → {yua_t[:100]}")

        ruka_sees = "私聊" in ruka_t or "Yua" in ruka_t
        print(f"  Ruka 識別自己身份：{'✅' if 'Ruka' in ruka_t or '瑠夏' in ruka_t or '女朋友' in ruka_t else '❌'}")
        print(f"  Ruka 不知道私聊內容：{'✅' if '私聊' not in ruka_t or '不知道' in ruka_t else '⚠️'}")
        print(f"    → {ruka_t[:120]}")


if __name__ == "__main__":
    asyncio.run(run_test())