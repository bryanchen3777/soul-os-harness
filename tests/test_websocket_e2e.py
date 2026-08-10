"""
test_websocket_e2e.py
WebSocket 端對端測試：server 啟動 → WS 連線 → inject tick → 收到 AGENT_SPEAK 廣播
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
import websockets

ROOT = Path(__file__).resolve().parent.parent
SERVER_SCRIPT = str(ROOT / "scripts" / "run_server.py")
SERVER_URL = "http://localhost:8000"
WS_URL = "ws://localhost:8000/ws"


def wait_for_server(timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def _terminate_server(server_proc: subprocess.Popen) -> None:
    """P0.5 (Bry 派工 2026-08-09 19:48): ensure zombie cleanup.

    Popen.terminate on Windows is TerminateProcess (forceful) but subprocess
    children can take longer to exit. Use kill() after timeout.
    """
    try:
        server_proc.terminate()
        server_proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server_proc.kill()
        try:
            server_proc.wait(timeout=5)
        except Exception:
            pass


def _safe_rmtree(path) -> None:
    """P0.5 (Bry 派工 2026-08-09 19:48): tempdir cleanup with Windows file lock tolerance.

    SQLite WAL files may still be locked by the terminated subprocess for a brief
    moment after kill. Use ignore_errors + onerror to make cleanup best-effort.
    """
    import shutil
    import stat
    def _onerror(func, path, exc_info):
        # Try chmod then unlink
        try:
            os.chmod(path, stat.S_IWRITE)
        except Exception:
            pass
        try:
            func(path)
        except Exception:
            pass  # best effort, ignore
    try:
        shutil.rmtree(path, onerror=_onerror)
    except Exception:
        pass  # best effort, ignore


@pytest.mark.asyncio
async def test_inject_tick_triggers_agent_speak():
    """
    使用 /inject/tick 端點注入 SYSTEM_TICK，
    驗證 Agent -> Intent -> LLM(Mock) -> AGENT_SPEAK -> WebSocket broadcast 完整鏈路。
    """
    # P0.5 (Bry 派工 2026-08-09 19:48): isolate persistence via SOUL_OS_DATA_DIR
    # Use manual cleanup (not TemporaryDirectory context manager) to handle
    # Windows file lock race when server subprocess is terminated.
    tmp_data_dir = tempfile.mkdtemp(prefix="test_ws_e2e_")
    try:
        test_env = os.environ.copy()
        test_env["SOUL_OS_DATA_DIR"] = tmp_data_dir
        server_proc = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            cwd=str(ROOT),
            env=test_env,
            # P0.5: use DEVNULL to prevent uvicorn stdout from blocking the server
            # (PIPE buffer fills up since nothing reads it during test)
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            assert wait_for_server(60), "Server did not start"

            async with websockets.connect(WS_URL) as ws:
                # fast_forward 35 分鐘
                req = urllib.request.Request(
                    f"{SERVER_URL}/_admin/fast_forward?minutes=35.0",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass

                # inject tick — retry 機制（server 冷啟動時 async init 可能還沒完成）
                received = []
                for attempt in range(3):
                    req2 = urllib.request.Request(
                        f"{SERVER_URL}/inject/tick?elapsed_mins=35.0&time_period=morning",
                        method="POST",
                    )
                    with urllib.request.urlopen(req2, timeout=15):
                        pass

                    # 等最多 8 秒收 agent_speak（忽略 text='' 的過早回應）
                    deadline = asyncio.get_event_loop().time() + 8
                    while asyncio.get_event_loop().time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            data = json.loads(raw)
                            txt = data.get('text', '') or ''
                            print(f"[WS] recv: type={data.get('type')} agent={data.get('agent_id','')} text_len={len(txt)}")
                            if data.get("type") == "agent_speak" and data.get("text", ""):
                                received.append(data)
                                break  # 收到非空 text 才算成功
                        except asyncio.TimeoutError:
                            continue

                    if received:
                        break
                    print(f"[Retry] attempt {attempt+1}/3 no valid agent_speak, retrying...")
                    await asyncio.sleep(2.0)

                assert len(received) >= 1, "No agent_speak received after 3 retries"

        finally:
            _terminate_server(server_proc)
    finally:
        # P0.5: best-effort cleanup (server subprocess may still hold SQLite locks briefly)
        _safe_rmtree(tmp_data_dir)


@pytest.mark.asyncio
async def test_websocket_user_message_forwarding():
    """
    使用 /debug/broadcast 直接觸發 AGENT_SPEAK，驗證 WS 暢通。
    """
    # P0.5 (Bry 派工 2026-08-09 19:48): isolate persistence via SOUL_OS_DATA_DIR
    tmp_data_dir = tempfile.mkdtemp(prefix="test_ws_e2e_")
    try:
        test_env = os.environ.copy()
        test_env["SOUL_OS_DATA_DIR"] = tmp_data_dir
        server_proc = subprocess.Popen(
            [sys.executable, SERVER_SCRIPT],
            cwd=str(ROOT),
            env=test_env,
            # P0.5: use DEVNULL to prevent uvicorn stdout from blocking
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            assert wait_for_server(60), "Server did not start within 60s"

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
            _terminate_server(server_proc)
    finally:
        _safe_rmtree(tmp_data_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
