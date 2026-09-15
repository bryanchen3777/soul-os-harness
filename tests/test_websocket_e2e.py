"""
test_websocket_e2e.py
WebSocket 端對端測試：server 啟動 → WS 連線 → inject tick → 收到 AGENT_SPEAK 廣播

TEST-INFRA-ISOLATION-1（2026-09-14，P0 止血）—— 本檔原本硬編碼**生產埠**：
  `SERVER_URL = "http://localhost:8000"` / `WS_URL = "ws://localhost:8000/ws"`，
  且 `wait_for_server()` 只要「埠上有 200 OK」就放行。
⇒ 測試自起 server 失敗（:8000 被生產占用）後，`wait_for_server()` 被**生產**的
  `/health` 滿足，遂直接對 **live 生產** 下
  `GET /_admin/fast_forward?minutes=35.0` ＋ `POST /inject/tick`（生產
  `data/server_nohup.log` 有 200 OK 實證）。

修法（三重防線）：
  1. **動態 ephemeral 埠**：每次測試以 `socket.bind(("127.0.0.1", 0))` 取一個
     當下確認無人監聽的埠，交由 `tests/_ws_e2e_server.py` 把**生產 app** 綁到
     `127.0.0.1:<該埠>`（不再有 `:8000` 常數；host 僅 loopback）。
  2. **身分綁定（只認自己剛起的那一個 server）**：`wait_for_server()` 必須同時
     滿足兩項**由我們子行程自己寫出**的證據，缺一不可 ——
       (a) 子行程 stderr 出現 `Started server process [<pid>]` 且 `<pid>` **等於
           我們 `subprocess.Popen` 起來的子行程 pid**（uvicorn `server.py:82`
           印的是 `os.getpid()`）；
       (b) 子行程 stderr 出現 `Uvicorn running on http://127.0.0.1:<我們的埠>`
           （uvicorn 在**綁定成功之後**才印，`server.py:222`）⇒ 證明**我們的**
           子行程真的擁有該埠。
     任一條件不成立、或子行程提前結束 ⇒ **`pytest.fail`**，
     **絕不 fallback** 到既有 listener（不存在「埠有回應就放行」這條路）。
  3. **所有請求只打自己的 base URL**：`fast_forward` / `inject/tick` 一律經
     `_base_url(port)`，該函式內建硬護欄：port == 8000 直接 fail。

🔴 紅線：本檔在任何情況下都**不得**對 `:8000` 發送任何請求。

註（為何子行程強制 Mock LLM）：生產 provider 為 `ollama` 且 `.env` 帶
`OLLAMA_API_KEY`，若原樣繼承環境，隔離 server 會走**真實 LLM**（每個 tick
10 個角色 ⇒ 慢、非決定性、且要花錢）。本檔把 provider key 於子行程環境內
顯式設為**空字串**（空字串＝「已設定」，`python-dotenv` 預設不覆寫 ⇒ 不會被
`.env` 補回），使 `run_server.py` 走既有 `MockLLMBackend` 分支 —— 離線、
決定性、0 成本。此舉不改任何斷言。

註（為何子行程一併關閉 Telegram）：同理由，隔離 server 會用同一批**生產**
`TELEGRAM_BOT_*` token 起 10 個 poller，與生產的 10 個 poller 搶同一批 update，
甚至可能真的送出 TG 訊息 ⇒ 本檔把 `TELEGRAM_BOT_*` 於子行程環境內同樣設為
空字串，觸發 `scripts/run_server.py:1657` 的**既有**閘門
（`if os.environ.get("TELEGRAM_BOT_YUA"):`）⇒ 整個 Telegram channel 被 skip
（**沿用既有生產分支，非新增機制**）。⇒ 隔離 server 0 對外輪詢、0 送訊。
"""
import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

import pytest
import websockets

ROOT = Path(__file__).resolve().parent.parent
#: 測試專用啟動器：以 __main__ 執行生產 entry point，只改綁定位址。
SERVER_LAUNCHER = str(ROOT / "tests" / "_ws_e2e_server.py")

#: 🔴 生產埠。本檔**永不**對它發送任何請求（僅作為負向護欄常數）。
PRODUCTION_PORT = 8000

#: uvicorn 啟動行（身分）：`Started server process [<pid>]`，pid = 該行程 os.getpid()
#: ⚠️ uvicorn 的 `ColourizedFormatter` 在本機 stderr 上會**啟用顏色**，此時它改印
#: `color_message` ⇒ pid 會被 ANSI 色碼包住（`Started server process [\x1b[36m<pid>\x1b[0m]`）。
#: 故所有比對前一律先剝除 ANSI 序列（見 `_read_child_stderr`）。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STARTED_RE = re.compile(r"Started server process \[(\d+)\]")
#: 啟動器自報行（一次性 nonce 握手）：`[TEST-INFRA-ISOLATION-1] launcher_pid=<pid> port=<port> nonce=<nonce>`
_LAUNCHER_RE = re.compile(
    r"\[TEST-INFRA-ISOLATION-1\] launcher_pid=(\d+) port=(\d+) nonce=([0-9a-f]+)"
)
#: uvicorn 綁定成功行（擁有權）：`Uvicorn running on http://127.0.0.1:<port> (...)`
_LISTENING_RE = re.compile(r"Uvicorn running on https?://[^\s/]+:(\d+)")

#: 於子行程環境內強制清空的 provider key（⇒ run_server.py 走 MockLLMBackend）
_MOCK_FORCED_KEY_VARS = (
    "OLLAMA_API_KEY",
    "DEEPSEEK_API_KEY",
    "MINIMAX_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "XAI_API_KEY",
)

#: 於子行程環境內強制清空的 Telegram bot token。這走**既有生產閘門**
#: `scripts/run_server.py:1657`（`if os.environ.get("TELEGRAM_BOT_YUA"):`）——
#: 空字串為 falsy ⇒ **整個 Telegram channel 被 skip**（既有分支，非新機制）。
#: 必要性：否則隔離 server 會用同一批生產 token 起 10 個 poller，與**生產**的
#: 10 個 poller 搶同一批 update（且可能真的送出 TG 訊息）。
#: 註：空字串在 `os.environ` 內＝「已設定」，`python-dotenv` 預設不覆寫 ⇒
#: `run_server.py` 的 `load_dotenv()` 不會把 `.env` 的值補回來。
_TELEGRAM_OFF_KEY_VARS = (
    "TELEGRAM_BOT_YUA",
    "TELEGRAM_BOT_RUKA",
    "TELEGRAM_BOT_AKANE",
    "TELEGRAM_BOT_REM",
    "TELEGRAM_BOT_RAM",
    "TELEGRAM_BOT_MAHIRU",
    "TELEGRAM_BOT_ANNA",
    "TELEGRAM_BOT_MAI",
    "TELEGRAM_BOT_MIKU",
    "TELEGRAM_BOT_AOI",
)


# ───────────────────────────────────────────────────────────
# 埠與 URL（含「絕不打生產」硬護欄）
# ───────────────────────────────────────────────────────────

def _base_url(port: int) -> str:
    """本測試唯一的 base URL 來源；內建生產埠硬護欄。"""
    if port == PRODUCTION_PORT:
        pytest.fail(
            f"TEST-INFRA-ISOLATION-1: 拒絕對生產埠 :{PRODUCTION_PORT} 發送任何請求"
        )
    return f"http://127.0.0.1:{port}"


def _ws_url(port: int) -> str:
    if port == PRODUCTION_PORT:
        pytest.fail(
            f"TEST-INFRA-ISOLATION-1: 拒絕對生產埠 :{PRODUCTION_PORT} 建立 WS 連線"
        )
    return f"ws://127.0.0.1:{port}/ws"


def _port_has_listener(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _pick_ephemeral_port() -> int:
    """取一個當下確定無人監聽的 ephemeral 埠（且絕不等於生產埠）。

    先 `bind(("127.0.0.1", 0))` 讓核心指派，關閉後再以 connect 反證「無人監聽」；
    任一輪不合格就重取（有界迴圈）。
    """
    for _ in range(32):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        if port == PRODUCTION_PORT:
            continue
        if not _port_has_listener(port):
            return port
    pytest.fail("TEST-INFRA-ISOLATION-1: 無法取得可用的 ephemeral 測試埠")


# ───────────────────────────────────────────────────────────
# 身分綁定：只認「自己剛起的那一個」server
# ───────────────────────────────────────────────────────────

def _read_child_stderr(stderr_path: Path) -> str:
    """子行程 stderr 全文（已剝除 ANSI 色碼，供比對與除錯）。"""
    try:
        text = Path(stderr_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return _ANSI_RE.sub("", text)


def _own_server_identity(stderr_path: Path, nonce: str, port: int) -> str:
    """回傳 "ok" 或「尚未成立／不符」的原因字串。**只認我們剛起的那條程序鏈。**

    ⚠️ 本機 `.venv\Scripts\python.exe` 是 **uv trampoline（shim）**：實測
    `subprocess.Popen(...).pid = 7088` 而真正跑 uvicorn 的子行程自報 `pid = 12016`
    ⇒ 「uvicorn log pid == Popen.pid」在本環境**必然失敗**，不可當身分判據。

    改用**一次性 nonce 握手**（nonce 由本測試產生，只經 argv 傳給我們起的子行程）：
      (a) 我們子行程的 stderr 必須含啟動器自報行
          `[TEST-INFRA-ISOLATION-1] launcher_pid=<pid> port=<port> nonce=<nonce>`，
          且 nonce 與本測試選定值**完全相同**（外人無從得知）⇒ 證明這段 stderr
          來自**我們剛起的那條程序鏈**；
      (b) uvicorn 自報 `Started server process [<pid>]` 的 pid 必須等於 (a) 的
          `launcher_pid`（啟動器自報 vs uvicorn 自報 ⇒ 同一行程）；
      (c) uvicorn 必須印出 `Uvicorn running on http://127.0.0.1:<port>`（該行在
          **綁定成功之後**才印，`uvicorn/server.py:222`）⇒ 證明該埠確實由**我們
          這條程序鏈**擁有（兩個行程無法同時 bind 同一 127.0.0.1:port）。
    三者皆成立才回 "ok"；任一不成立即不放行。
    """
    text = _read_child_stderr(stderr_path)

    m0 = _LAUNCHER_RE.search(text)
    if m0 is None:
        return "child stderr 尚無啟動器自報行（launcher 尚未接手）"
    launcher_pid = int(m0.group(1))
    launcher_port = int(m0.group(2))
    launcher_nonce = m0.group(3)
    if launcher_nonce != nonce:
        return f"nonce 不符：子行程自報 {launcher_nonce!r} ≠ 本測試選定 {nonce!r}"
    if launcher_port != port:
        return f"埠不符：launcher 自報 {launcher_port} ≠ 測試埠 {port}"

    m = _STARTED_RE.search(text)
    if m is None:
        return "child stderr 尚無 'Started server process [pid]'"
    served_pid = int(m.group(1))
    if served_pid != launcher_pid:
        return (
            f"身分不符：uvicorn 宣告 'Started server process [{served_pid}]'，"
            f"但我們起的程序鏈自報 pid = {launcher_pid}"
        )

    m2 = _LISTENING_RE.search(text)
    if m2 is None:
        return "child stderr 尚無 'Uvicorn running on ...'（尚未綁定成功）"
    logged_port = int(m2.group(1))
    if logged_port != port:
        return f"埠不符：子行程綁定 {logged_port}，但本測試請求的埠 = {port}"

    return "ok"


def wait_for_server(port: int, server_proc: subprocess.Popen, stderr_path: Path,
                    nonce: str, timeout: float = 60) -> None:
    """等到**我們剛起的那一個** server 就緒；否則 `pytest.fail`（絕不 fallback）。

    放行條件（缺一不可）：
      1. 子行程仍存活（`poll() is None`）；
      2. `_own_server_identity()` == "ok"（nonce 握手 ＋ uvicorn pid ＋ 綁定埠）；
      3. 對**我們自己的** `http://127.0.0.1:<port>/health` 取得 200。
    """
    deadline = time.time() + timeout
    last_reason = "尚未開始探測"
    while time.time() < deadline:
        rc = server_proc.poll()
        if rc is not None:
            pytest.fail(
                f"TEST-INFRA-ISOLATION-1: 自己起的測試 server 提前結束 (rc={rc})；"
                f"依紅線**絕不 fallback** 到既有 listener。child stderr 尾段：\n"
                f"{_read_child_stderr(stderr_path)[-2000:]}"
            )

        reason = _own_server_identity(stderr_path, nonce, port)
        if reason != "ok":
            last_reason = reason
            time.sleep(0.5)
            continue

        try:
            with urllib.request.urlopen(f"{_base_url(port)}/health", timeout=2) as r:
                if r.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 — 尚未就緒，繼續等
            last_reason = f"自己的 server /health 尚未就緒: {exc}"
        time.sleep(0.5)

    pytest.fail(
        f"TEST-INFRA-ISOLATION-1: 自己的測試 server 未能在 {timeout}s 內於 "
        f"127.0.0.1:{port} 就緒（最後原因：{last_reason}）；**絕不 fallback** 到既有 "
        f"listener。child stderr 尾段：\n{_read_child_stderr(stderr_path)[-2000:]}"
    )


# ───────────────────────────────────────────────────────────
# 隔離 server 生命週期
# ───────────────────────────────────────────────────────────

class _IsolatedServer:
    """把**生產 app** 起在 127.0.0.1:<ephemeral>，資料根指向呼叫端給的 tmp。

    `SOUL_OS_DATA_DIR` 顯式指向 tmp ⇒ 子行程的 `data_root()`（含
    `faulthandler.log`）全部落在 tmp，**0 生產 `data/**` 寫入**。
    """

    def __init__(self, tmp_data_dir: str) -> None:
        self.port = _pick_ephemeral_port()
        # 一次性 nonce：只經 argv 交給我們起的子行程，用來做身分握手。
        self.nonce = uuid.uuid4().hex
        self.stderr_path = Path(tmp_data_dir) / "_ws_e2e_server.stderr.log"

        env = os.environ.copy()
        env["SOUL_OS_DATA_DIR"] = tmp_data_dir
        # 空字串＝「已設定」⇒ load_dotenv() 不會補回 ⇒ 走 Mock LLM / skip Telegram
        for var in _MOCK_FORCED_KEY_VARS + _TELEGRAM_OFF_KEY_VARS:
            env[var] = ""

        self._stderr_handle = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(
            [sys.executable, SERVER_LAUNCHER, str(self.port), self.nonce],
            cwd=str(ROOT),
            env=env,
            # DEVNULL/檔案（非 PIPE）避免 uvicorn stdout 填滿緩衝阻塞；
            # stderr 落檔是為了做身分綁定（解析 pid 與綁定埠）。
            stdout=subprocess.DEVNULL,
            stderr=self._stderr_handle,
        )

    def wait_ready(self, timeout: float = 60) -> None:
        wait_for_server(
            self.port, self.proc, self.stderr_path, nonce=self.nonce, timeout=timeout
        )

    def base_url(self) -> str:
        return _base_url(self.port)

    def ws_url(self) -> str:
        return _ws_url(self.port)

    def close(self) -> None:
        _terminate_server(self.proc)
        try:
            self._stderr_handle.close()
        except Exception:  # noqa: BLE001 — best effort
            pass


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
    # TEST-INFRA-ISOLATION-1: server 一律起在自己的 ephemeral 埠，永不碰生產 :8000。
    # Use manual cleanup (not TemporaryDirectory context manager) to handle
    # Windows file lock race when server subprocess is terminated.
    tmp_data_dir = tempfile.mkdtemp(prefix="test_ws_e2e_")
    server: Optional[_IsolatedServer] = None
    try:
        server = _IsolatedServer(tmp_data_dir)
        try:
            server.wait_ready(60)

            async with websockets.connect(server.ws_url()) as ws:
                # fast_forward 35 分鐘（打自己的 server）
                req = urllib.request.Request(
                    f"{server.base_url()}/_admin/fast_forward?minutes=35.0",
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5):
                    pass

                # inject tick — retry 機制（server 冷啟動時 async init 可能還沒完成）
                received = []
                for attempt in range(3):
                    req2 = urllib.request.Request(
                        f"{server.base_url()}/inject/tick?elapsed_mins=35.0&time_period=morning",
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
            if server is not None:
                server.close()
    finally:
        # P0.5: best-effort cleanup (server subprocess may still hold SQLite locks briefly)
        _safe_rmtree(tmp_data_dir)


@pytest.mark.asyncio
async def test_websocket_user_message_forwarding():
    """
    使用 /debug/broadcast 直接觸發 AGENT_SPEAK，驗證 WS 暢通。
    """
    # P0.5 (Bry 派工 2026-08-09 19:48): isolate persistence via SOUL_OS_DATA_DIR
    # TEST-INFRA-ISOLATION-1: 同上，只打自己的 ephemeral server。
    tmp_data_dir = tempfile.mkdtemp(prefix="test_ws_e2e_")
    server: Optional[_IsolatedServer] = None
    try:
        server = _IsolatedServer(tmp_data_dir)

        try:
            server.wait_ready(60)

            async with websockets.connect(server.ws_url()) as ws:
                # 直接廣播（打自己的 server）
                req = urllib.request.Request(
                    f"{server.base_url()}/debug/broadcast", method="GET"
                )
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
            if server is not None:
                server.close()
    finally:
        _safe_rmtree(tmp_data_dir)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
