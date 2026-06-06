"""
test_websocket_e2e.py
WebSocket 端對端測試：server 啟動 → WS 連線 → inject tick → 收到 AGENT_SPEAK 廣播
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import websockets

ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = str(ROOT / "scripts" / "run_server.py")
SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"


def wait_for_server(timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


@pytest.mark.asyncio
async def test_inject_tick_triggers_agent_speak():
    """
    使用 /inject/tick 端點注入 SYSTEM_TICK，
    驗證 Agent -> Intent -> LLM(Mock) -> AGENT_SPEAK -> WebSocket broadcast 完整鏈路。
    """
    server_proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        assert wait_for_server(20), "Server did not start"

        async with websockets.connect(WS_URL) as ws:
            # fast_forward 35 分鐘
            req = urllib.request.Request(
                f"{SERVER_URL}/_admin/fast_forward?minutes=35.0",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5):
                pass

            # inject tick
            req2 = urllib.request.Request(
                f"{SERVER_URL}/inject/tick?elapsed_mins=35.0&time_period=morning",
                method="POST",
            )
            with urllib.request.urlopen(req2, timeout=15):
                pass

            # 純 asyncio 等待，最多 15 秒
            received = []
            deadline = asyncio.get_event_loop().time() + 15
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(raw)
                    print(f"[WS] recv: {data.get('type')} agent={data.get('agent_id','')}")
                    if data.get("type") == "agent_speak":
                        received.append(data)
                        break  # 收到一條就夠
                except asyncio.TimeoutError:
                    continue  # 繼續等

            assert len(received) >= 1, f"No agent_speak received in 15s"
            assert len(received[0].get("text", "")) > 0

    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_websocket_user_message_forwarding():
    """
    使用 /debug/broadcast 直接觸發 AGENT_SPEAK，驗證 WS 暢通。
    """
    server_proc = subprocess.Popen(
        [sys.executable, SERVER_SCRIPT],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    try:
        assert wait_for_server(20), "Server did not start within 20s"

        async with websockets.connect(WS_URL) as ws:
            # 直接廣播
            req = urllib.request.Request(f"{SERVER_URL}/debug/broadcast", method="GET")
            with urllib.request.urlopen(req, timeout=5) as r:
                result = json.loads(r.read())
            print(f"[debug/broadcast] result={result}")

            # 等廣播過來（最多 5 秒）
            received = []
            deadline = asyncio.get_event_loop().time() + 5
            while asyncio.get_event_loop().time() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    data = json.loads(raw)
                    if data.get("type") == "agent_speak":
                        received.append(data)
                        break
                except asyncio.TimeoutError:
                    continue

            assert len(received) >= 1, f"Expected broadcast from /debug/broadcast, got {len(received)}"
            first = received[0]
            assert "yua" in first.get("agent_id", "").lower(), f"Expected yua, got {first}"
            print(f"[OK] WebSocket broadcast received: {first}")

    finally:
        server_proc.terminate()
        server_proc.wait(timeout=5)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])