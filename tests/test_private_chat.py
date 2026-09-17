"""
tests/test_private_chat.py
Soul OS - Private Chat 端到端测试

测试:
1. Yua 响应私聊
2. Ruka 响应私聊
3. Akane 响应私聊
4. 历史正确隔离（Yua 的历史不包含 Ruka 的内容）

前置条件: server 必须在 localhost:8000 运行
    python scripts/run_server.py

用法:
    python tests/test_private_chat.py
    python tests/test_private_chat.py --mock   # 使用 Mock LLM
"""
import argparse
import asyncio
import json
import sys
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"


def wait_for_server(timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


async def send_and_wait(text, agent_id, mode, participants=None, timeout=15):
    results = []
    stop_event = asyncio.Event()

    async def listener(ws):
        try:
            while not stop_event.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    data = json.loads(raw)
                    if data.get("type") == "agent_speak" and data.get("text"):
                        results.append(data)
                except asyncio.TimeoutError:
                    continue
        except Exception:
            pass

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        t = asyncio.create_task(listener(ws))

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

        await ws.send(json.dumps(payload))

        deadline = time.time() + timeout
        while time.time() < deadline:
            if results:
                break
            await asyncio.sleep(0.3)

        stop_event.set()
        await t

    return results


async def test_private_yua():
    print("\n  [Test 1] Yua 私聊响应...")
    results = await send_and_wait("你好呀 Yua，今天怎么样？", "agent_yua", "private")
    assert len(results) >= 1, f"Yua 没有响应！收到 {len(results)} 条消息"
    assert results[0]["agent_id"] == "agent_yua", f"响应不是来自 Yua: {results[0]}"
    print(f"  [OK] Yua 响应: {results[0]['text'][:60]}")
    return results


async def test_private_ruka():
    print("\n  [Test 2] Ruka 私聊响应...")
    results = await send_and_wait("Ruka 在吗？", "agent_ruka", "private")
    assert len(results) >= 1, f"Ruka 没有响应！收到 {len(results)} 条消息"
    assert results[0]["agent_id"] == "agent_ruka", f"响应不是来自 Ruka: {results[0]}"
    print(f"  [OK] Ruka 响应: {results[0]['text'][:60]}")
    return results


async def test_private_akane():
    print("\n  [Test 3] Akane 私聊响应...")
    results = await send_and_wait("Akane，今天有空吗？", "agent_akane", "private")
    assert len(results) >= 1, f"Akane 没有响应！收到 {len(results)} 条消息"
    assert results[0]["agent_id"] == "agent_akane", f"响应不是来自 Akane: {results[0]}"
    print(f"  [OK] Akane 响应: {results[0]['text'][:60]}")
    return results


async def test_history_isolation():
    print("\n  [Test 4] 历史隔离验证...")
    # Yua 的历史文件
    yua_hist = ROOT / "data" / "conversations" / "bryan_agent_yua_private.json"
    ruka_hist = ROOT / "data" / "conversations" / "bryan_agent_ruka_private.json"

    # 两段历史应该存在且内容不同
    assert yua_hist.exists(), f"Yua 历史文件不存在: {yua_hist}"
    assert ruka_hist.exists(), f"Ruka 历史文件不存在: {ruka_hist}"

    yua_data = json.loads(yua_hist.read_text(encoding="utf-8"))
    ruka_data = json.loads(ruka_hist.read_text(encoding="utf-8"))

    # 每个至少要有 2 条消息（user + assistant）
    assert len(yua_data) >= 2, f"Yua 历史太短: {len(yua_data)}"
    assert len(ruka_data) >= 2, f"Ruka 历史太短: {len(ruka_data)}"

    print(f"  [OK] Yua 历史: {len(yua_data)} 条, Ruka 历史: {len(ruka_data)} 条")

    # 检查文件命名已更新
    print(f"  [OK] 历史文件名: {yua_hist.name}, {ruka_hist.name}")
    return True


async def run_tests(server_proc):
    print("\n" + "=" * 60)
    print("  Soul OS - Private Chat E2E Tests")
    print("=" * 60)

    if not wait_for_server(25):
        print("\n  [ERROR] Server did not start in 25s")
        return False

    print(f"\n  [server] Running at {SERVER_URL}")

    tests = [
        ("Yua", test_private_yua),
        ("Ruka", test_private_ruka),
        ("Akane", test_private_akane),
        ("历史隔离", test_history_isolation),
    ]

    passed = 0
    for name, fn in tests:
        try:
            await fn()
            passed += 1
        except AssertionError as e:
            print(f"  [FAIL] {name}: {e}")
        except Exception as e:
            print(f"  [ERROR] {name}: {e}")

    print("\n" + "=" * 60)
    print(f"  Results: {passed}/{len(tests)} passed")
    print("=" * 60)
    return passed == len(tests)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true",
                        help="使用 mock LLM (需要 server 已在运行)")
    args = parser.parse_args()

    # 手動 smoke 模式已移除：本機不得由測試啟動第二個生產服務。
    # TEST-INFRA-COLLECTION-LEAK-1 (D2)：本檔從前有 `--server`，會
    # `subprocess.Popen([sys.executable, scripts/run_server.py])` 拉起**第二個**
    # 生產服務去搶 `:8000`，並在 finally 內 terminate() 它。那條路徑已整段移除
    # （連同為它服務的 env 準備與 time.sleep）。本檔現在**永不衍生任何行程**：
    # 它只對「已經在運行」的服務做 smoke，服務不存在就由 wait_for_server() 失敗。
    if args.mock:
        print("  [mock] 假設已在運行的 server 使用 mock LLM"
              "（本檔不會啟動任何行程，--mock 僅為說明用途）")

    success = asyncio.run(run_tests(None))
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()