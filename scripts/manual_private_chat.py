"""
scripts/test_private_chat.py
Soul OS - Interactive Private/Group Chat Test

用法:
    python scripts/test_private_chat.py                   # 交互模式
    python scripts/test_private_chat.py --msg "你好"       # 单条测试
    python scripts/test_private_chat.py --agent ruka      # 指定 agent
    python scripts/test_private_chat.py --mode group      # 群聊模式

前置条件: server 必须在 localhost:8000 运行
    python scripts/run_server.py
"""
import argparse
import asyncio
import json
import sys
import time
import threading
from pathlib import Path

# allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets

SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"


def check_server():
    import urllib.request
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as r:
            data = json.loads(r.read())
            print(f"  [server] status={data.get('status')} connections={data.get('connections')}")
            return True
    except Exception as e:
        print(f"  [server] NOT reachable: {e}")
        return False


async def ws_listener(ws, results, stop_event):
    """在后台线程收集 WS 消息"""
    try:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                data = json.loads(raw)
                if data.get("type") == "agent_speak":
                    results.append(data)
            except asyncio.TimeoutError:
                continue
    except Exception:
        pass


async def send_message(text, agent_id, mode, participants=None):
    """发送消息并等待响应"""
    results = []
    stop_event = threading.Event()

    async with websockets.connect(WS_URL, ping_interval=None) as ws:
        # 启动 listener
        listener = asyncio.create_task(ws_listener(ws, results, stop_event))

        # 发送消息
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

        print(f"\n  [send] mode={mode} agent={agent_id}")
        print(f"  [send] text: {text[:60]}{'...' if len(text) > 60 else ''}")
        await ws.send(json.dumps(payload))

        # 等待响应（最多 15 秒）
        deadline = time.time() + 15
        while time.time() < deadline:
            if results:
                break
            await asyncio.sleep(0.3)

        stop_event.set()
        await listener

    return results


def main():
    import urllib.request

    parser = argparse.ArgumentParser(description="Soul OS Chat Test")
    parser.add_argument("--msg", "-m", type=str, help="发送的消息")
    parser.add_argument("--agent", "-a", type=str, default="yua",
                        choices=["yua", "ruka", "akane"],
                        help="目标 agent (default: yua)")
    parser.add_argument("--mode", type=str, default="private",
                        choices=["private", "group"],
                        help="模式: private 或 group (default: private)")
    parser.add_argument("--group-members", type=str, default="",
                        help="群聊成员，逗号分隔 (e.g. yua,ruka)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Soul OS - Chat Test")
    print("=" * 60)
    print(f"  Server: {SERVER_URL}")

    if not check_server():
        print("\n  ERROR: Server not running!")
        print("  请先启动: python scripts/run_server.py")
        sys.exit(1)

    if args.msg:
        # 单条测试模式
        agent_id = f"agent_{args.agent}"
        mode = args.mode

        if mode == "group":
            members = [f"agent_{a.strip()}" for a in args.group_members.split(",") if a.strip()]
            if not members:
                members = ["agent_yua", "agent_ruka"]
        else:
            members = None

        print(f"\n  [test] Sending message to {agent_id} in {mode} mode")
        results = asyncio.run(send_message(args.msg, agent_id, mode, members))

        if results:
            for r in results:
                print(f"\n  [response] agent={r.get('agent_id')} text={r.get('text', '')[:80]}")
            print("\n  [PASS] Got response from agent")
        else:
            print("\n  [FAIL] No response received within 15s")
            sys.exit(1)

    else:
        # 交互模式
        print("\n  Interactive mode - type your messages")
        print("  Commands:")
        print("    /agent <yua|ruka|akane>  - 切换私聊目标")
        print("    /mode <private|group>    - 切换模式")
        print("    /members <a,b,c>         - 设置群聊成员")
        print("    /quit                   - 退出")
        print()

        agent_id = "agent_yua"
        mode = "private"
        members = ["agent_yua"]

        while True:
            try:
                text = input(f"[{mode}] {agent_id.replace('agent_','')}> ").strip()
            except EOFError:
                break

            if not text:
                continue

            if text.startswith("/"):
                cmd = text.split()
                if cmd[0] == "/quit":
                    break
                elif cmd[0] == "/agent" and len(cmd) > 1:
                    agent_id = f"agent_{cmd[1]}"
                    print(f"  -> Switched to {agent_id}")
                elif cmd[0] == "/mode" and len(cmd) > 1:
                    mode = cmd[1]
                    print(f"  -> Mode set to {mode}")
                elif cmd[0] == "/members" and len(cmd) > 1:
                    members = [f"agent_{a}" for a in cmd[1].split(",")]
                    print(f"  -> Members: {members}")
                continue

            results = asyncio.run(send_message(text, agent_id, mode, members if mode == "group" else None))
            if results:
                for r in results:
                    print(f"\n  [{r.get('agent_id','').replace('agent_','')}] {r.get('text','')}")
            else:
                print("\n  (no response)")
            print()


if __name__ == "__main__":
    main()