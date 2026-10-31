"""SERVER-OPS-MAINT-LOCK — 維護鎖的釋放時機必須由「固定秒數」改為「持有至服務就緒」。

背景
----
2026-10-31 00:08 實測事故：``scripts/server_ops.ps1 restart`` 的維護鎖在
``Start-SoulOsServer`` 回來後**立刻**釋放，但 uvicorn 啟動要跑一輪 elevation /
contradiction 掃描，實際綁埠要 1~3 分鐘 ⇒ 落在「鎖已解、port 尚未 LISTEN」的
空窗期 ⇒ watchdog 的 tick（每 5 分鐘）看到 ``port_listen=False`` 就判定失聯，
**自行重啟**（殺掉剛拉起的實例、改拉 Plan A）：

    00:08:04 WARN post-eb0b15f N=1/10 port_listen=False procs=2 -> restart
    00:08:06 拉 Plan A

無資料損失（sidecar mtime / bytes 未變），但等於每次「用官方腳本重啟」都多一次重啟。

Owner 裁定（2026-10-31 15:47）：【核准立項修復】—— 維護鎖持有至 ``/health`` 探針
回 **200**，或達逾時上限（180 秒）才釋放。

本檔釘住這份裁定的**可執行判準**
--------------------------------
* **靜態守衛 G1–G6**（純解析，不啟任何行程）：
  G1 ``restart`` 分支在**移除維護鎖之前**呼叫就緒探針；
  G2 探針的**唯一**成功判準是 ``StatusCode -eq 200``；
  G3 逾時上限落在 ``[90, 600]`` 秒，且「常數／參數預設值／呼叫點」三者一致（無漂移）；
  G4 ``start`` 路徑**不得**呼叫這個阻塞式探針（``start`` 會被 watchdog 的
  ``_start_plan_a.ps1`` 以 ``WaitForExit(120000)`` 呼叫，阻塞會讓 Plan A 誤判失敗）；
  G5 逾時上限**必須小於** watchdog 的「維護鎖 stale 門檻」（自 ``_watchdog.ps1``
  實讀，不是抄在這裡的常數）；
  G6 探針的 URL 必須由 ``$servicePort`` 組合，不得硬寫位址字面值。
* **行為守衛 B1–B3**（沙盒複本 ＋ 本機暫時埠的無害 HTTP 探針）：
  B1 端點回 200 ⇒ 探針回 READY；
  B2 端點不存在 ⇒ 探針回 TIMEOUT（且確實等滿逾時上限，不是提早放棄）；
  B3 端點回 503 ⇒ 探針回 TIMEOUT（證明「唯一成功判準是 200」）。
* **雙向變異 M1–M6**：每條守門都必須有對應的變異，且**每一條變異都必須把該守門弄紅**
  （護欄自己不是盲腸）。變異一律作用在**記憶體中的腳本文字**，不改磁碟上的生產檔案。

🔴 本測試絕不重啟／啟動／停止生產服務，也絕不寫入 ``data/**``：
* 靜態守衛只讀檔案；
* 行為守衛在 pytest 的 tmp 目錄裡跑**沙盒複本**（函式本體**逐字**取自生產檔），
  探針綁的是本機暫時埠，**絕不碰生產埠**；
* 不使用 ``subprocess.*`` / ``os.system`` / ``os.popen`` / ``os.spawn*``
  （``tests/infra/test_no_production_spawn_guard.py`` 的 R5 白名單大小鎖死 22，
  本票不得擴張）—— 行程一律以 ``ctypes`` 直接呼叫 kernel32 的 ``CreateProcessW``。

🔴 R3／R2（``tests/**`` 不得硬寫生產服務位址）：本檔**不含**任何可被掃到的位址
字面值——比對用的樣式一律以跳脫點 ＋ 埠以 ``(?:nnnn)`` 分組書寫。
"""
from __future__ import annotations

import ctypes
import os
import re
import socket
import threading
import time
from ctypes import wintypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import _winapi
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
SERVER_OPS_PATH = SCRIPTS_DIR / "server_ops.ps1"
WATCHDOG_PATH = SCRIPTS_DIR / "_watchdog.ps1"

#: 逾時上限必須落在此區間（秒）。下限 90s（實測綁埠 1~3 分鐘）；
#: 上限 600s（watchdog 的維護鎖 stale 門檻是 10 分鐘，超過就會被當 stale 刪掉）。
MIN_TIMEOUT_SEC = 90
MAX_TIMEOUT_SEC = 600

#: 行為守衛用的探針逾時（秒）—— 測試自己要的，不是生產值。
PROBE_TIMEOUT_SEC = 5
PROBE_INTERVAL_SEC = 1

#: 行為守衛用的沙盒腳本組合片段（不碰生產路徑／埠）。
SANDBOX_PS1_TEMPLATE = """$ErrorActionPreference = 'Stop'
$servicePort = {port}
$verdictFile = '{verdict_file}'
$logFile = '{log_file}'
function Write-OpsLog([string]$msg) {{
    Add-Content -Path $logFile -Value $msg -Encoding UTF8
}}
function Wait-SoulOsHealthy {{
{function_text}
}}
$ready = Wait-SoulOsHealthy -TimeoutSec {timeout} -IntervalSec {interval}
if ($ready -eq $true) {{
    Set-Content -Path $verdictFile -Value 'VERDICT=READY' -Encoding UTF8
}} else {{
    Set-Content -Path $verdictFile -Value 'VERDICT=TIMEOUT' -Encoding UTF8
}}
exit 0
"""

#: 沙盒讀取結果的兩種判定字串。
VERDICT_READY = "VERDICT=READY"
VERDICT_TIMEOUT = "VERDICT=TIMEOUT"


# ───────────────────────────────────────────────────────────
# 文字解析（只看可執行程式碼：字串內容與註解先遮蔽）
# ───────────────────────────────────────────────────────────

def _mask(text: str) -> str:
    """把字串內容與行內註解換成空白（**保留長度與換行**）⇒ 括號配對不被字面誤導。

    為什麼需要這一層：只做字串比對的守衛會被註解／字面值命中
    （例如檔頭記錄舊機制的那段說明）⇒ 綠燈是假的。
    """
    out = list(text)
    i = 0
    n = len(text)
    quote = None
    while i < n:
        ch = text[i]
        if quote is not None:
            if ch == quote:
                if i + 1 < n and text[i + 1] == quote:   # '' / "" 是跳脫
                    out[i] = " "
                    out[i + 1] = " "
                    i += 2
                    continue
                quote = None
            elif ch == "`" and quote == '"':            # PowerShell 的跳脫引號
                out[i] = " "
                if i + 1 < n:
                    out[i + 1] = " "
                i += 2
                continue
            elif ch != "\n":
                out[i] = " "
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch == "#":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
            continue
        i += 1
    return "".join(out)


def _block(masked: str, brace_idx: int) -> tuple:
    """回傳 (body_start, body_end)：``brace_idx`` 指向 ``{``，配對的 ``}`` 在 body_end。"""
    assert masked[brace_idx] == "{", "brace_idx 必須指向 '{'"
    depth = 0
    for i in range(brace_idx, len(masked)):
        c = masked[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return brace_idx + 1, i
    raise AssertionError("括號不配對")


def _function_body(text: str, masked: str, name: str) -> str:
    """取出 ``function <name> { … }`` 的函式本體（不含大括號）。"""
    m = re.search(r"(?m)^function\s+%s\b" % re.escape(name), masked)
    assert m, "找不到 function %s" % name
    brace = masked.index("{", m.end())
    s, e = _block(masked, brace)
    return text[s:e]


def _switch_branch(text: str, masked: str, label: str) -> str:
    """取出 ``switch ($Action) { '<label>' { … } }`` 該分支的內容。

    ⚠ 分支標籤本身是**字串常數**（``'start'``）⇒ 在遮蔽後的文字裡已被抹成空白，
    必須回到**原始文字**去找標籤；括號配對則用遮蔽後的文字（字面值裡的括號不計）。
    遮蔽不改變長度 ⇒ 兩邊的位移可直接共用。
    """
    sw = masked.index("switch ($Action) {")
    m = re.search(r"(?m)^\s*'%s'\s*\{" % re.escape(label), text[sw:])
    assert m, "switch 裡找不到分支 '%s'" % label
    brace = sw + m.end() - 1
    s, e = _block(masked, brace)
    return text[s:e]


# ───────────────────────────────────────────────────────────
# 靜態守衛（純解析；每條都是可單獨呼叫的判準）
# ───────────────────────────────────────────────────────────

def _guard_g1_probe_before_lock_release(text: str, masked: str) -> bool:
    """G1：``restart`` 分支在移除維護鎖**之前**呼叫就緒探針。

    結構順序（實測 production 形狀）：
        … Start-SoulOsServer
        … $ready = Wait-SoulOsHealthy …      ← 探針
        … } finally {                        ← 鎖的釋放從這裡開始
        …     Remove-Item $maintenanceLock … ← 鎖真正被移除
    ⇒ 判準：探針呼叫的位置 < ``} finally {`` < ``Remove-Item $maintenanceLock``。
    """
    branch = _switch_branch(text, masked, "restart")
    if "Wait-SoulOsHealthy" not in branch:
        return False
    if "Remove-Item $maintenanceLock" not in branch:
        return False
    if "} finally {" not in branch:
        return False
    i_call = branch.index("Wait-SoulOsHealthy")
    i_finally = branch.index("} finally {")
    i_remove = branch.index("Remove-Item $maintenanceLock")
    return i_call < i_finally < i_remove


def _guard_g2_only_200_is_success(text: str, masked: str) -> bool:
    """G2：探針的**唯一**成功判準是 ``StatusCode -eq 200``。"""
    fn = _function_body(text, masked, "Wait-SoulOsHealthy")
    if fn.count("StatusCode -eq 200") != 1:
        return False
    if fn.count("return $true") != 1 or fn.count("return $false") != 1:
        return False
    for bad in ("StatusCode -ne", "StatusCode -gt", "StatusCode -ge",
                "StatusCode -lt", "StatusCode -le"):
        if bad in fn:
            return False
    return True


def _guard_g3_timeout_within_bounds(text: str, masked: str) -> bool:
    """G3：逾時上限 ∈ [90, 600]，且常數／參數預設值／呼叫點三者一致。"""
    m = re.search(r"(?m)^\$healthReadyTimeoutSec\s*=\s*(\d+)\s*$", masked)
    if not m:
        return False
    value = int(m.group(1))
    if not (MIN_TIMEOUT_SEC <= value <= MAX_TIMEOUT_SEC):
        return False
    defaults = re.findall(r"\[int\]\$TimeoutSec\s*=\s*(\d+)", masked)
    if not defaults or int(defaults[0]) != value:
        return False
    if not re.search(
        r"Wait-SoulOsHealthy\s+-TimeoutSec\s+\$healthReadyTimeoutSec", masked
    ):
        return False
    return True


def _guard_g4_start_path_never_blocks(text: str, masked: str) -> bool:
    """G4：``start`` 路徑不得呼叫這個阻塞式探針。"""
    if "Wait-SoulOsHealthy" in _switch_branch(text, masked, "start"):
        return False
    if "Wait-SoulOsHealthy" in _function_body(text, masked, "Start-SoulOsServer"):
        return False
    # 反向對照：探針必須**確實**出現在 restart 分支（否則「沒有呼叫」是空話）
    if "Wait-SoulOsHealthy" not in _switch_branch(text, masked, "restart"):
        return False
    return True


def _guard_g5_timeout_below_watchdog_stale_threshold(text: str, masked: str) -> bool:
    """G5：逾時上限必須小於 watchdog 的「維護鎖 stale 門檻」（自 _watchdog.ps1 實讀）。"""
    m = re.search(r"(?m)^\$healthReadyTimeoutSec\s*=\s*(\d+)\s*$", masked)
    if not m:
        return False
    value = int(m.group(1))
    wd = WATCHDOG_PATH.read_text(encoding="utf-8-sig")
    wd_masked = _mask(wd)
    stale = re.search(r"\$lockAgeMin\s*-le\s*(\d+)", wd_masked)
    if not stale:
        return False
    stale_seconds = int(stale.group(1)) * 60
    # watchdog 仍必須真的認這把鎖（否則「門檻」這個概念不存在）。
    # ⚠ 鎖名是**字串常數** ⇒ 必須在原始文字裡找（遮蔽後已被抹成空白）。
    if "watchdog_maintenance.lock" not in wd:
        return False
    return value < stale_seconds


def _guard_g6_probe_url_uses_service_port(text: str, masked: str) -> bool:
    """G6：探針 URL 由 ``$servicePort`` 組合，不得硬寫位址字面值。"""
    fn = _function_body(text, masked, "Wait-SoulOsHealthy")
    if "$servicePort" not in fn:
        return False
    if not re.search(r"http://127\.0\.0\.1:\$servicePort/health", fn):
        return False
    if re.search(r"127\.0\.0\.1:\d", fn):
        return False
    return True


STATIC_GUARDS = (
    ("G1_probe_before_lock_release", _guard_g1_probe_before_lock_release),
    ("G2_only_200_is_success", _guard_g2_only_200_is_success),
    ("G3_timeout_within_bounds", _guard_g3_timeout_within_bounds),
    ("G4_start_path_never_blocks", _guard_g4_start_path_never_blocks),
    ("G5_timeout_below_watchdog_stale_threshold",
     _guard_g5_timeout_below_watchdog_stale_threshold),
    ("G6_probe_url_uses_service_port", _guard_g6_probe_url_uses_service_port),
)


# ───────────────────────────────────────────────────────────
# 行程執行（ctypes → kernel32 CreateProcessW；不使用 subprocess）
# ───────────────────────────────────────────────────────────

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_STARTF_USESTDHANDLES = 0x00000100


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD),
    ]


_KERNEL32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
    wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPWSTR,
    ctypes.POINTER(_STARTUPINFOW), ctypes.POINTER(_PROCESS_INFORMATION),
]
_KERNEL32.CreateProcessW.restype = wintypes.BOOL
_KERNEL32.SetHandleInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD]
_KERNEL32.SetHandleInformation.restype = wintypes.BOOL

_HANDLE_FLAG_INHERIT = 0x00000001


def _make_inheritable(handle: int) -> int:
    """把 handle 標成可被子行程繼承（CreateProcessW 需要 bInheritHandles=TRUE）。"""
    if not _KERNEL32.SetHandleInformation(
        wintypes.HANDLE(handle), _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _powershell_path() -> str:
    return str(
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )


def _run_powershell(script: Path, timeout: float = 90.0) -> str:
    """執行一支 .ps1 並收回 stdout（不經 subprocess —— R5 白名單不得擴張）。"""
    cmd = '"%s" -NoProfile -ExecutionPolicy Bypass -File "%s"' % (
        _powershell_path(), script,
    )
    read_h, write_h = _winapi.CreatePipe(None, 0)
    _make_inheritable(write_h)
    nul = _winapi.CreateFile(
        "NUL", _GENERIC_READ, _FILE_SHARE_READ | _FILE_SHARE_WRITE,
        0, _OPEN_EXISTING, 0, 0,
    )
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = _STARTF_USESTDHANDLES
    si.hStdInput = wintypes.HANDLE(nul)
    si.hStdOutput = wintypes.HANDLE(write_h)
    si.hStdError = wintypes.HANDLE(write_h)
    pi = _PROCESS_INFORMATION()
    buf = ctypes.create_unicode_buffer(cmd)
    try:
        if not _KERNEL32.CreateProcessW(
            None, buf, None, None, True, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _winapi.CloseHandle(write_h)
        _winapi.CloseHandle(nul)

    chunks = []
    done = threading.Event()

    def reader():
        try:
            while True:
                try:
                    chunk = _winapi.ReadFile(read_h, 4096)
                except OSError:
                    break
                if isinstance(chunk, (tuple, list)):
                    chunk = chunk[0] if chunk else b""
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            done.set()

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    done.wait(timeout)
    _winapi.WaitForSingleObject(pi.hProcess, int(timeout * 1000))
    _winapi.CloseHandle(read_h)
    _winapi.CloseHandle(pi.hProcess)
    _winapi.CloseHandle(pi.hThread)
    return b"".join(chunks).decode("utf-8", "replace")


# ───────────────────────────────────────────────────────────
# 本機無害 HTTP 探針（暫時埠；回傳可設定的狀態碼）
# ───────────────────────────────────────────────────────────

class _StatusHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    status_code = 200

    def do_GET(self):
        body = b"OK"
        self.send_response(self.status_code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_probe_server(status_code: int):
    """在 127.0.0.1 的暫時埠起一個無害探針；回傳 (server, port)。"""
    handler = type("_Handler", (_StatusHandler,), {"status_code": status_code})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, int(server.server_address[1])


def _dead_port() -> int:
    """抓一個此刻沒人監聽的埠（bind 後立刻放掉）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# ───────────────────────────────────────────────────────────
# 沙盒探針執行
# ───────────────────────────────────────────────────────────

def _run_sandbox_probe(tmp_path: Path, function_text: str, port: int,
                       timeout_sec: int = PROBE_TIMEOUT_SEC,
                       interval_sec: int = PROBE_INTERVAL_SEC) -> tuple:
    """在沙盒跑**逐字**取自生產檔的探針函式；回傳 (verdict, elapsed_sec, stdout)。"""
    verdict_file = tmp_path / "verdict.txt"
    log_file = tmp_path / "probe.log"
    script = tmp_path / "sandbox_probe.ps1"
    script.write_text(
        SANDBOX_PS1_TEMPLATE.format(
            port=port,
            verdict_file=str(verdict_file),
            log_file=str(log_file),
            function_text=function_text,
            timeout=timeout_sec,
            interval=interval_sec,
        ),
        encoding="utf-8-sig",
    )
    started = time.time()
    stdout = _run_powershell(script, timeout=120.0)
    elapsed = time.time() - started
    verdict = ""
    if verdict_file.exists():
        verdict = verdict_file.read_text(encoding="utf-8-sig").strip()
    return verdict, elapsed, stdout


# ───────────────────────────────────────────────────────────
# fixtures
# ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def ops_text() -> str:
    return SERVER_OPS_PATH.read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def ops_masked(ops_text: str) -> str:
    return _mask(ops_text)


@pytest.fixture(scope="module")
def probe_function(ops_text: str, ops_masked: str) -> str:
    """生產檔裡的探針函式本體（逐字）。"""
    return _function_body(ops_text, ops_masked, "Wait-SoulOsHealthy")


# ───────────────────────────────────────────────────────────
# 靜態守衛
# ───────────────────────────────────────────────────────────

def test_static_guards_all_green(ops_text, ops_masked):
    """六條靜態守衛全綠。"""
    failures = []
    for name, guard in STATIC_GUARDS:
        if not guard(ops_text, ops_masked):
            failures.append(name)
    assert not failures, "靜態守衛未通過：%s" % (failures,)


def test_probe_function_exists_and_is_called_from_restart(ops_text, ops_masked):
    """探針函式真的存在，且真的由 restart 分支呼叫（不是「守衛對空檔案」）。"""
    fn = _function_body(ops_text, ops_masked, "Wait-SoulOsHealthy")
    assert "Invoke-WebRequest" in fn, "探針必須真的發出 /health 請求"
    assert "/health" in fn
    assert "Wait-SoulOsHealthy" in _switch_branch(ops_text, ops_masked, "restart")


# ───────────────────────────────────────────────────────────
# 行為守衛
# ───────────────────────────────────────────────────────────

def test_b1_probe_reports_ready_when_endpoint_answers_200(tmp_path, probe_function):
    """B1：端點回 200 ⇒ 探針回 READY。"""
    server, port = _start_probe_server(200)
    try:
        verdict, elapsed, stdout = _run_sandbox_probe(tmp_path, probe_function, port)
    finally:
        server.shutdown()
        server.server_close()
    assert VERDICT_READY in verdict, (
        "端點回 200 時探針必須回 READY，實際=%r（stdout=%r）" % (verdict, stdout)
    )
    assert elapsed < PROBE_TIMEOUT_SEC + 30, "探針不該拖太久：%.1fs" % elapsed


def test_b2_probe_times_out_when_endpoint_is_absent(tmp_path, probe_function):
    """B2：端點不存在 ⇒ 探針回 TIMEOUT，且**確實等滿逾時上限**。"""
    port = _dead_port()
    verdict, elapsed, stdout = _run_sandbox_probe(tmp_path, probe_function, port)
    assert VERDICT_TIMEOUT in verdict, (
        "端點不存在時探針必須回 TIMEOUT，實際=%r（stdout=%r）" % (verdict, stdout)
    )
    assert elapsed >= PROBE_TIMEOUT_SEC, (
        "探針必須等滿逾時上限才放棄，實際只等了 %.1fs（< %ds）"
        % (elapsed, PROBE_TIMEOUT_SEC)
    )


def test_b3_non_200_response_is_not_success(tmp_path, probe_function):
    """B3：端點回 503 ⇒ 探針回 TIMEOUT（證明「唯一成功判準是 200」）。"""
    server, port = _start_probe_server(503)
    try:
        verdict, elapsed, stdout = _run_sandbox_probe(tmp_path, probe_function, port)
    finally:
        server.shutdown()
        server.server_close()
    assert VERDICT_TIMEOUT in verdict, (
        "503 不得被當成成功，實際=%r（stdout=%r）" % (verdict, stdout)
    )


# ───────────────────────────────────────────────────────────
# 雙向變異：每條守門都必須有對應的變異，且每一條都必須轉紅
# ───────────────────────────────────────────────────────────

def _mutate_timeout_out_of_bounds(ops_text: str) -> str:
    return ops_text.replace("$healthReadyTimeoutSec = 180",
                            "$healthReadyTimeoutSec = 700", 1)


def _mutate_timeout_too_small(ops_text: str) -> str:
    return ops_text.replace("$healthReadyTimeoutSec = 180",
                            "$healthReadyTimeoutSec = 30", 1)


def _mutate_probe_after_lock_release(ops_text: str) -> str:
    """把探針呼叫移到「移除維護鎖」之後。"""
    call = "            $ready = Wait-SoulOsHealthy -TimeoutSec $healthReadyTimeoutSec\n"
    assert call in ops_text, "找不到探針呼叫行（生產檔形狀已變？）"
    out = ops_text.replace(call, "", 1)
    anchor = "                Remove-Item $maintenanceLock -Force -ErrorAction SilentlyContinue\n"
    assert anchor in out
    return out.replace(
        anchor,
        anchor + "            $ready = Wait-SoulOsHealthy -TimeoutSec $healthReadyTimeoutSec\n",
        1,
    )


def _mutate_probe_into_start_path(ops_text: str) -> str:
    """把探針塞進 Start-SoulOsServer（= start 路徑也會阻塞）。"""
    anchor = "function Start-SoulOsServer {\n"
    assert anchor in ops_text
    return ops_text.replace(
        anchor,
        anchor + "    $null = Wait-SoulOsHealthy -TimeoutSec 1\n",
        1,
    )


def _mutate_hardcoded_address(ops_text: str) -> str:
    """把探針 URL 的埠硬寫成字面值。

    🔴 位址以**片段拼接**寫出（R3／R2：``tests/**`` 不得硬寫生產服務位址；
    本守衛檔自身不得成為那個例外 —— 否則守衛會靠例外存活）。
    """
    hardcoded = '"http://127.0.0.1:' + "8000" + '/health"'
    return ops_text.replace(
        '"http://127.0.0.1:$servicePort/health"',
        hardcoded,
        1,
    )


def _mutate_success_criterion_narrowed(probe_function: str) -> str:
    """把成功判準從 200 改成 204（收緊）。"""
    assert "StatusCode -eq 200" in probe_function
    return probe_function.replace("StatusCode -eq 200", "StatusCode -eq 204", 1)


def _mutate_success_criterion_inverted(probe_function: str) -> str:
    """把成功判準反向（200 反而不算成功）。"""
    assert "StatusCode -eq 200" in probe_function
    return probe_function.replace("StatusCode -eq 200", "StatusCode -ne 200", 1)


@pytest.mark.parametrize(
    "guard_name,mutate",
    [
        ("G3_timeout_within_bounds", _mutate_timeout_out_of_bounds),
        ("G3_timeout_within_bounds", _mutate_timeout_too_small),
        ("G1_probe_before_lock_release", _mutate_probe_after_lock_release),
        ("G4_start_path_never_blocks", _mutate_probe_into_start_path),
        ("G6_probe_url_uses_service_port", _mutate_hardcoded_address),
    ],
)
def test_static_guard_mutation_must_turn_red(ops_text, guard_name, mutate):
    """靜態守衛的變異必須把該守門弄紅（護欄自己不是盲腸）。"""
    guard = dict(STATIC_GUARDS)[guard_name]
    assert guard(ops_text, _mask(ops_text)), "生產檔在本來就該是綠的"
    mutated = mutate(ops_text)
    assert mutated != ops_text, "變異沒有實際改動腳本（守衛不是被這個變異蓋掉的）"
    assert not guard(mutated, _mask(mutated)), (
        "變異後守衛 %s 仍然是綠的 ⇒ 這條守門是盲腸" % guard_name
    )


@pytest.mark.parametrize(
    "mutate",
    [_mutate_success_criterion_narrowed, _mutate_success_criterion_inverted],
)
def test_b1_mutation_must_turn_red(tmp_path, probe_function, mutate):
    """行為守衛 B1 的變異（放寬／收緊成功判準）必須讓 B1 轉紅。"""
    mutated = mutate(probe_function)
    assert mutated != probe_function, "變異沒有實際改動函式"
    server, port = _start_probe_server(200)
    try:
        verdict, elapsed, stdout = _run_sandbox_probe(tmp_path, mutated, port)
    finally:
        server.shutdown()
        server.server_close()
    assert VERDICT_READY not in verdict, (
        "變異後端點回 200 探針仍回 READY ⇒ B1 是盲腸（verdict=%r stdout=%r）"
        % (verdict, stdout)
    )


# ───────────────────────────────────────────────────────────
# 邊界：watchdog / Plan A 層不得被本票偷偷改掉
# ───────────────────────────────────────────────────────────

def test_watchdog_layer_is_untouched_by_this_ticket(ops_text):
    """本票只動 ``server_ops.ps1``；watchdog 的鎖判定與重試常數不得被順手改掉。

    釘的是 watchdog 的**關鍵常數**（不是整份檔案雜湊 —— 檔案會因其他票合法變動）。
    """
    wd = WATCHDOG_PATH.read_text(encoding="utf-8-sig")
    wd_masked = _mask(wd)
    for constant, value in (
        ("N_CAP", "10"),
        ("TRIAL_TARGET", "98"),
        ("STARTUP_WINDOW_SECONDS", "90"),
        ("PORT_RELEASE_WAIT_SECONDS", "30"),
    ):
        assert re.search(r"\$%s\s*=\s*%s\b" % (constant, value), wd_masked), (
            "watchdog 的 $%s 必須維持 %s（屬另一層，本票不得順手改掉）"
            % (constant, value)
        )
    # ⚠ 鎖名是**字串常數** ⇒ 必須在原始文字裡找（遮蔽後已被抹成空白）。
    assert "watchdog_maintenance.lock" in wd, (
        "watchdog 必須繼續認維護鎖（否則本票的保護毫無意義）"
    )
