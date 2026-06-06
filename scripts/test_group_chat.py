"""
scripts/test_group_chat.py
自動化群聊分佈測試
"""
import asyncio
import json
import websockets
from collections import Counter

TEST_MESSAGES = [
    "大家好",
    "我今天很累",
    "有人在嗎",
    "你們在做什麼",
    "今天天氣真好",
    "我想聊天",
    "最近怎麼樣",
    "好無聊",
]

async def run_test():
    speaker_count = Counter()
    responses = []

    print("連接中...")
    async with websockets.connect("ws://localhost:8000/ws") as ws:
        print("已連線，開始測試\n")
        for msg in TEST_MESSAGES:
            print(f"[送出] {msg}")
            await ws.send(json.dumps({
                "type": "USER_MESSAGE",
                "content": msg,
                "user_id": "test_user",
                "mode": "group"
            }))

            deadline = asyncio.get_event_loop().time() + 8
            got_response = False
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(raw)
                    if data.get("type") == "agent_speak":
                        agent = data.get("agent_id", "unknown")
                        text = data.get("text", "")[:50]
                        speaker_count[agent] += 1
                        responses.append({"msg": msg, "agent": agent, "text": text})
                        print(f"  → {agent}: {text}")
                        got_response = True
                except asyncio.TimeoutError:
                    if got_response:
                        break

            if not got_response:
                print(f"  → (無回應)")
            await asyncio.sleep(0.5)

    print("\n" + "=" * 50)
    print("測試結果")
    print("=" * 50)
    total = sum(speaker_count.values())
    print(f"總回應數：{total}")
    for agent, count in speaker_count.most_common():
        pct = count / total * 100 if total else 0
        print(f"  {agent}: {count} 次 ({pct:.0f}%)")

    yua_count = speaker_count.get("agent_yua", 0)
    ruka_count = speaker_count.get("agent_ruka", 0)
    akane_count = speaker_count.get("agent_akane", 0)

    print(f"\nYua:   {'✅ PASS' if yua_count >= 2 else '❌ FAIL'} ({yua_count} 次，目標 >= 2)")
    print(f"Ruka:  {'✅ PASS' if ruka_count >= 1 else '❌ FAIL'} ({ruka_count} 次，目標 >= 1)")
    print(f"Akane: {'✅ PASS' if akane_count >= 1 else '⚠️  WARN'} ({akane_count} 次，目標 >= 1)")

    return speaker_count


if __name__ == "__main__":
    asyncio.run(run_test())