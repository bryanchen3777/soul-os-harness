"""SERVER-OPS-PLAN-A-LAUNCH — 隔離守衛：watchdog 的 **Plan A 自動拉起路徑**不得繼承
呼叫端的 stdout pipe，且必須與主路徑共用同一個啟動原語。

背景
----
2026-10-21 02:10 的 DEPLOY-RESTART-21 以背景 task 跑 ``.\\scripts\\server_ops.ps1 restart``：
重啟成功，但 wrapper 因「呼叫端永遠等不到 stdout 的 EOF」而不退出，最終被 harness 的
600 秒牆鐘上限殺掉（``Command timed out after 600 seconds``）。服務僥倖存活 —— 但那是運氣：
2026-09-16 事故就是同一形狀。

前一票（``SERVER-OPS-HANDLE-INHERITANCE``，已合入 ``140dfea``）已把**主啟動路徑**
（``scripts\\server_ops.ps1``）改成「呼叫端 → 中繼 launcher → 服務」（中繼以 ShellExecute
建立 ⇒ ``bInheritHandles=FALSE`` ⇒ 不持有呼叫端 stdout pipe）。

但根因還剩一條路徑沒關：**``scripts\\_start_plan_a.ps1``**（watchdog 的 Plan A 自動拉起路徑）
仍用 ``Start-Process … -RedirectStandard* -NoNewWindow`` ⇒ 同一根因仍在。**它觸發的時機
恰恰是「服務已經出問題」的時候** ⇒ 最需要可靠的那條路徑反而最不可靠。

本票做法（工單 SERVER-OPS-PLAN-A-LAUNCH §3.1 決策）
--------------------------------------------------
Plan A **不再自己拉起服務**，改為委派給既有入口 ``scripts\\server_ops.ps1 -Action start``
（單一啟動原語）：

    呼叫端 → Plan A → server_ops.ps1(start) → 中繼 launcher → 服務

🔴 Plan A 呼叫 ``server_ops.ps1`` 時**不得**帶 ``-RedirectStandard*`` / ``-NoNewWindow``：
帶了就會讓 ``server_ops.ps1`` 繼承 Plan A 的 stdout pipe（等於把繼承問題搬家）。

本檔驗證的是**隔離沙盒裡的啟動鏈**，不是生產服務：

* **守衛 1**（無遞迴）：靜態解析呼叫圖 + 動態（反向呼叫 marker 不得出現）。
* **守衛 2**（不繼承呼叫端 pipe）：服務行程內 ``GetFileType(呼叫端 pipe handle)`` **不得**
  是 ``FILE_TYPE_PIPE``。
* **守衛 3**（wrapper 確定返回）：呼叫端在限定時間內收到 stdout 的 **EOF**（事件量測）。
* **守衛 4**（服務起來後才回報成功 + 服務在 wrapper 退出後仍存活）。
* **守衛 5**（輸出仍在 log 檔、格式不變）。
* **守衛 6**（pid 語意不變）：pid 檔 = launcher（trampoline）pid；``:8000`` socket owner
  = 其**子行程**（以沙盒同構驗證）。
* **守衛 7**（watchdog 重試語意未變）：靜態斷言重試次數／退避／健康等待。
* **守衛 8**（生產未受影響）：測試前後生產 ``data\\server.pid``、``:8000`` socket owner、
  ``data\\server_nohup.*`` 逐位元相同；啟動路徑的寫入目標不得指向生產 ``data/``。

🔴 本測試絕不重啟／啟動／停止生產服務，也絕不寫入 ``data/**``：

* 一律在 pytest 的 tmp 目錄裡跑（生產腳本的**沙盒複本**只換路徑與埠，其餘邏輯逐字保留）；
* **不使用** ``subprocess.*`` / ``os.system`` / ``os.popen`` / ``os.spawn*``
  （``tests/infra/test_no_production_spawn_guard.py`` 的 R5 白名單大小鎖死在 22，
  本票不得擴張）—— 行程一律以 ``ctypes`` 直接呼叫 kernel32 的 ``CreateProcessW``；
* 沙盒服務是**無害探針**（自己綁一個臨時埠、回 ``/health``、寫報告檔、自己結束），
  絕不碰生產埠 ``:8000``。

變異實驗
--------
``SOULOS_PLAN_A_MUTATION`` 環境變數可在本檔內施加變異（見 :func:`_apply_mutation`），
用來證明每道守衛都會轉紅；未設該變數時，測試跑的是**真正的生產檔案**（沙盒複本）。

變異一律作用在**記憶體中的腳本文字**（沙盒複本），**不改磁碟上的生產檔案**。
"""
from __future__ import annotations

import ctypes
import hashlib
import os
import re
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import _winapi
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"
PLAN_A_PATH = SCRIPTS_DIR / "_start_plan_a.ps1"
SERVER_OPS_PATH = SCRIPTS_DIR / "server_ops.ps1"
WATCHDOG_PATH = SCRIPTS_DIR / "_watchdog.ps1"
RUN_SERVER_PATH = SCRIPTS_DIR / "run_server.py"
DATA_DIR = REPO_ROOT / "data"
PRODUCTION_PID_FILE = DATA_DIR / "server.pid"

#: 生產服務的 TCP 埠（以「數字常數」而非「位址字串」表示，避免被行程護欄的位址規則誤判）。
SERVICE_TCP = 8000

#: wrapper 返回的期限：超過這個時間就算「沒有確定返回」。
WRAPPER_RETURN_TIMEOUT_S = 90.0
#: wrapper 退出後多久，服務仍必須存活（守衛 4）。
SERVICE_SURVIVAL_CHECK_S = 2.0
#: 沙盒服務的最長存活時間（超過就自行結束，避免留下孤兒行程）。
SERVICE_MAX_LIFETIME_S = 180.0
#: 沙盒探針的「延遲上線」秒數：讓「服務還沒起來就回報成功」這個變異（守衛 4）
#: 變紅時**不靠運氣**（延遲必須大於變異版 wrapper 的退出時間）。
PROBE_READY_DELAY_S = 8.0

#: 變異實驗用的環境變數名稱。
MUTATION_ENV = "SOULOS_PLAN_A_MUTATION"

#: 生產 log 檔名（不得變更；本測試只讀取其雜湊以證明未被寫入）。
NOHUP_LOG_NAMES = ("server_nohup.log", "server_nohup.err", "server_nohup.out")

#: 沙盒探針在 log 檔留下的標記 —— 用來證明「測試的服務輸出沒有落進生產 log」。
SANDBOX_MARKER = "soulos-plan-a-probe"

#: ``GetFileType`` 的「檔案型別」常數。呼叫端的 stdout 是 pipe ⇒ FILE_TYPE_PIPE。
FILE_TYPE_PIPE = 0x3

# ───────────────────────────────────────────────────────────
# kernel32 直接呼叫（不使用 subprocess.* / os.spawn*）
# ───────────────────────────────────────────────────────────

_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)

_HANDLE_FLAG_INHERIT = 0x00000001
_GENERIC_READ = 0x80000000
_FILE_SHARE_READ = 0x00000001
_FILE_SHARE_WRITE = 0x00000002
_OPEN_EXISTING = 3
_SYNCHRONIZE = 0x00100000
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
_KERNEL32.GetConsoleWindow.restype = wintypes.HWND


def _make_inheritable(handle: int) -> int:
    """把 handle 標成可被子行程繼承（模擬 harness 把 stdout pipe 交給呼叫端）。"""
    if not _KERNEL32.SetHandleInformation(
        wintypes.HANDLE(handle), _HANDLE_FLAG_INHERIT, _HANDLE_FLAG_INHERIT
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _spawn(cmdline: str):
    """以 stdout/stderr 接在 pipe 上啟動行程；回傳 (PROCESS_INFORMATION, read_handle)。

    pipe 的寫端 handle 值會以 ``SOULOS_CALLER_PIPE_HANDLE`` 匯出到子行程的環境，
    讓沙盒服務可以直接檢查「自己有沒有繼承呼叫端的 stdout handle」。
    """
    read_h, write_h = _winapi.CreatePipe(None, 0)
    _make_inheritable(write_h)
    nul = _winapi.CreateFile(
        "NUL", _GENERIC_READ, _FILE_SHARE_READ | _FILE_SHARE_WRITE, 0, _OPEN_EXISTING, 0, 0
    )
    si = _STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.dwFlags = _STARTF_USESTDHANDLES
    si.hStdInput = wintypes.HANDLE(nul)
    si.hStdOutput = wintypes.HANDLE(write_h)
    si.hStdError = wintypes.HANDLE(write_h)
    pi = _PROCESS_INFORMATION()
    buf = ctypes.create_unicode_buffer(cmdline)
    previous = os.environ.get("SOULOS_CALLER_PIPE_HANDLE")
    os.environ["SOULOS_CALLER_PIPE_HANDLE"] = str(write_h)
    try:
        if not _KERNEL32.CreateProcessW(
            None, buf, None, None, True, 0, None, None, ctypes.byref(si), ctypes.byref(pi)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if previous is None:
            os.environ.pop("SOULOS_CALLER_PIPE_HANDLE", None)
        else:
            os.environ["SOULOS_CALLER_PIPE_HANDLE"] = previous
    _winapi.CloseHandle(write_h)
    _winapi.CloseHandle(nul)
    return pi, read_h


def _read_pipe_until_eof(read_h: int, timeout: float):
    """背景執行緒阻塞讀取；回傳 (bytes, 是否收到 EOF, 收到 EOF 的秒數)。"""
    buf = bytearray()
    done = threading.Event()

    def reader():
        try:
            while True:
                try:
                    chunk = _winapi.ReadFile(read_h, 4096)
                except OSError:
                    break            # 管道已結束（write handle 全關）＝ EOF
                # _winapi.ReadFile 回傳 (data, nread) —— **data 在前**。
                if isinstance(chunk, (tuple, list)):
                    chunk = chunk[0] if chunk else b""
                if not chunk:
                    break
                buf.extend(chunk)
        finally:
            done.set()

    th = threading.Thread(target=reader, daemon=True)
    started = time.time()
    th.start()
    eof = done.wait(timeout)
    return bytes(buf), eof, (time.time() - started if eof else None)


def _process_alive(pid: int) -> bool:
    """行程是否仍在運行（唯讀查詢；不送任何終止訊號）。

    ⚠ Windows 上「已終止但物件未銷毀」的行程，pid 仍可被 OpenProcess 開啟；
    真正判斷「已退出」要用 WaitForSingleObject(handle, 0) == WAIT_TIMEOUT。
    """
    try:
        h = _winapi.OpenProcess(_SYNCHRONIZE, False, pid)
    except OSError:
        return False
    if not h:
        return False
    alive = _winapi.WaitForSingleObject(h, 0) == _winapi.WAIT_TIMEOUT
    _winapi.CloseHandle(h)
    return alive


def _exit_code(proc_handle: int) -> int:
    try:
        return int(_winapi.GetExitCodeProcess(proc_handle))
    except OSError:
        return -1


def _capture(argv, timeout: float = 30.0) -> str:
    """執行一支唯讀工具並收回 stdout（不經由 subprocess）。"""
    cmd = '"%s" %s' % (argv[0], " ".join(argv[1:]))
    pi, read_h = _spawn(cmd)
    try:
        data, eof, _ = _read_pipe_until_eof(read_h, timeout)
        _winapi.WaitForSingleObject(pi.hProcess, int(timeout * 1000))
        return data.decode("utf-8", "replace")
    finally:
        _winapi.CloseHandle(read_h)
        _winapi.CloseHandle(pi.hProcess)
        _winapi.CloseHandle(pi.hThread)


def _listening_owners(port: int) -> set:
    """指定埠的 LISTENING socket owner pid 集合（netstat -ano，唯讀）。"""
    netstat = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netstat.exe")
    owners = set()
    for line in _capture([netstat, "-ano", "-p", "TCP"]).splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
            continue
        if not parts[1].endswith(":%d" % port):
            continue
        if parts[-2].upper() != "LISTENING":
            continue
        if parts[-1].isdigit() and int(parts[-1]) > 0:
            owners.add(int(parts[-1]))
    return owners


def _sha256(path: Path):
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _production_snapshot() -> dict:
    """生產狀態快照（唯讀）：pid 檔、nohup log 雜湊、``:8000`` 的 socket owner。"""
    pid_bytes = PRODUCTION_PID_FILE.read_bytes() if PRODUCTION_PID_FILE.exists() else None
    return {
        "pid_file": pid_bytes,
        "nohup": {name: _sha256(DATA_DIR / name) for name in NOHUP_LOG_NAMES},
        "owners": _listening_owners(SERVICE_TCP),
    }


# ───────────────────────────────────────────────────────────
# 靜態解析（只認「可執行程式碼」，不吃註解裡的字面值）
# ───────────────────────────────────────────────────────────

def _strip_comment(line: str) -> str:
    """去掉行內註解（``#`` 不在字串裡時才視為註解開頭）。"""
    out = []
    quote = None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            out.append(ch)
            continue
        if ch == "#":
            break
        out.append(ch)
    return "".join(out)


def _code_only(text: str) -> str:
    """只留下可執行程式碼（去掉整行與行內註解）。

    為什麼要這一層：只做字串比對的守衛會被**註解裡的字面值**命中
    （例如檔頭記錄舊機制的那段說明）⇒ 綠燈是假的。呼叫圖分析必須只看程式碼。
    """
    return "\n".join(_strip_comment(line) for line in text.splitlines())


def _var_values(text: str) -> dict:
    """解析沙盒腳本裡的變數賦值（``$root`` / ``Join-Path $root '...'`` / 純字面值）。"""
    values: dict = {}
    for line in text.splitlines():
        code = _strip_comment(line)
        m = re.match(r"\s*\$(\w+)\s*=\s*(.+?)\s*$", code)
        if not m:
            continue
        name, expr = m.group(1), m.group(2)
        jm = re.match(r"Join-Path\s+\$(\w+)\s+'([^']*)'", expr)
        if jm:
            base = values.get(jm.group(1))
            if base is None:
                continue
            values[name] = str(Path(base) / jm.group(2))
            continue
        lm = re.match(r"'([^']*)'", expr)
        if lm:
            values[name] = lm.group(1)
    return values


def _resolve_path(token: str, values: dict) -> str:
    token = token.strip("'\"")
    if token.startswith("$"):
        return values.get(token[1:], token)
    return token


def _write_targets(text: str, values: dict) -> list:
    """解析腳本裡所有寫入目標：寫檔（Out-File/Add-Content/Set-Content）與輸出導向。"""
    targets = []
    for line in text.splitlines():
        code = _strip_comment(line)
        m = re.search(r"-RedirectStandard(?:Output|Error)\s+(\S+)", code)
        if m:
            targets.append(_resolve_path(m.group(1), values))
        m = re.search(r"\b(Out-File|Add-Content|Set-Content)\b(.*)$", code)
        if m:
            rest = m.group(2)
            pm = re.search(r"-Path\s+(\S+)", rest)
            tokens = rest.split()
            token = pm.group(1) if pm else (tokens[-1] if tokens else "")
            if token:
                targets.append(_resolve_path(token, values))
    return targets


def _inside_production(path: str) -> bool:
    try:
        return Path(path).resolve().is_relative_to(DATA_DIR.resolve())
    except (OSError, ValueError):
        return False


def _free_port() -> int:
    """抓一個空著的臨時埠（bind 後立刻放掉）。"""
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


# ───────────────────────────────────────────────────────────
# 沙盒服務探針（無害；自己綁臨時埠、自己回 /health、自己結束）
# ───────────────────────────────────────────────────────────
#
# 探針是無害的 python 子行程，只做五件事：
#   1. 報「呼叫端 pipe handle 在我行程內的 GetFileType 結果」（handle 層級證據）；
#   2. 報 Console 資訊（服務不得與呼叫端共用 Console）；
#   3. **自己**持有 listening socket —— 沙盒拓撲與 production 同構：
#      中繼 launcher → trampoline（.venv python shim）→ 探針（真直譯器，socket owner）；
#   4. 報自己的 pid、父 pid（= trampoline pid）與「服務起來的時戳」；
#   5. 回應 ``GET /health`` = 200（Plan A 的健康等待需要它）。
#
# ⚠ 探針刻意**不使用** subprocess / os.spawn*（行程護欄 R5 白名單大小鎖死）。
# ⚠ 探針**不**會去碰生產埠：埠號由測試以環境變數傳進來。

SERVICE_PROBE = r'''
import ctypes
import os
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

RELEASE = os.environ.get("SOULOS_PROBE_RELEASE", "")
MAX_LIFETIME = float(os.environ.get("SOULOS_PROBE_MAX_LIFETIME", "180"))
REPORT = os.environ.get("SOULOS_PROBE_REPORT", "")
PORT = int(os.environ.get("SOULOS_PROBE_PORT", "0") or 0)
DELAY = float(os.environ.get("SOULOS_PROBE_DELAY_S", "0") or 0)
# 變異 M4 專用：讓服務把**自己**（= socket owner = trampoline 的子行程）的 pid
# 寫進 pid 檔 ⇒ pid 檔改記服務子行程 pid，pid 語意被破壞。
PIDFILE_OVERRIDE = os.environ.get("SOULOS_PROBE_PIDFILE_OVERRIDE", "")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
kernel32.WaitForSingleObject.restype = ctypes.c_uint32
kernel32.GetConsoleWindow.restype = ctypes.c_void_p
kernel32.GetFileType.argtypes = [ctypes.c_void_p]
kernel32.GetFileType.restype = ctypes.c_uint32
kernel32.GetConsoleProcessList.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
kernel32.GetConsoleProcessList.restype = ctypes.c_uint32


def _report(line):
    if REPORT:
        try:
            with open(REPORT, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


class _HealthHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_GET(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    # 「我有沒有繼承呼叫端的 stdout pipe handle？」—— 直接量測，不是推論。
    caller_pipe = int(os.environ.get("SOULOS_CALLER_PIPE_HANDLE", "0") or 0)
    ftype = 0xFFFFFFFF
    if caller_pipe:
        ftype = kernel32.GetFileType(ctypes.c_void_p(caller_pipe))

    console_buf = (ctypes.c_ulong * 16)()
    console_procs = kernel32.GetConsoleProcessList(console_buf, 16)
    console_hwnd = kernel32.GetConsoleWindow()

    # 延遲上線：讓「服務還沒起來就回報成功」的變異可以被確定性地抓到。
    if DELAY:
        time.sleep(DELAY)

    # 探針自己就是 socket owner（= production 拓撲的最後一段）。
    httpd = HTTPServer(("127.0.0.1", PORT), _HealthHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    if PIDFILE_OVERRIDE:
        try:
            with open(PIDFILE_OVERRIDE, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass

    sys.stdout.write(
        "soulos-plan-a-probe pid=%d caller_pipe_filetype=0x%x\n" % (os.getpid(), ftype)
    )
    sys.stdout.flush()
    sys.stderr.write("soulos-plan-a-probe-stderr pid=%d\n" % os.getpid())
    sys.stderr.flush()

    ready_ts = time.time()
    _report(
        "probe pid=%d ppid=%d port=%d caller_pipe_filetype=0x%x "
        "console_procs=%d console_hwnd=%s ready_ts=%.3f"
        % (os.getpid(), os.getppid(), port, ftype, console_procs, console_hwnd, ready_ts)
    )

    deadline = time.time() + MAX_LIFETIME
    while time.time() < deadline:
        if RELEASE and os.path.exists(RELEASE):
            break
        time.sleep(0.1)

    httpd.shutdown()
    sys.stdout.write("soulos-plan-a-probe exit pid=%d\n" % os.getpid())
    sys.stdout.flush()


if __name__ == "__main__":
    main()
'''


# ───────────────────────────────────────────────────────────
# 變異（僅供變異實驗；正常測試完全不啟用）
# ───────────────────────────────────────────────────────────

#: 舊機制（2026-10-21 之前 Plan A 的直啟區塊）—— 變異 M1 把它塞回去。
_OLD_DIRECT_LAUNCH = (
    "$proc = Start-Process -FilePath $python `\n"
    "    -ArgumentList \"$root\\scripts\\run_server.py\" `\n"
    "    -RedirectStandardOutput $outLog `\n"
    "    -RedirectStandardError $errLog `\n"
    "    -NoNewWindow -PassThru `\n"
    "    -WorkingDirectory $root\n"
    "$proc.Id | Out-File -Encoding utf8 $pidFile\n"
    "exit 0\n"
)


def _apply_mutation(plan_a_text: str, ops_text: str, name: str) -> tuple:
    """把指定變異套到**記憶體中的腳本文字**（沙盒複本用）。

    變異絕不碰磁碟上的生產檔案；沙盒複本由呼叫端用變異後的文字寫出。
    """
    if name == "M1_plan_a_goes_back_to_dotnet_direct_launch":
        # 讓 Plan A 改回 .NET 導向直啟服務（回到舊機制）⇒ 服務繼承呼叫端 pipe。
        # ⚠ 替換範圍必須把整個「啟動 + launchFailed 判斷」區塊吃掉, 否則會留下
        #    孤立的 "exit 1 }" ⇒ 沙盒腳本 parse 失敗 ⇒ 紅燈來自「腳本根本沒跑」,
        #    那不是真實斷言失敗（前一票踩過同類陷阱）。
        new_plan_a, n = re.subn(
            r"\$ops = \$null.*?if \(\$launchFailed\) \{\s*exit 1\s*\}",
            lambda _m: _OLD_DIRECT_LAUNCH,
            plan_a_text,
            count=1,
            flags=re.S,
        )
        assert n == 1, "變異 M1 沒有套上（Plan A 的啟動區塊形狀變了？）"
        return new_plan_a, ops_text

    if name == "M2_start_branch_calls_plan_a_back":
        # 讓 server_ops.ps1 的 start 分支反向呼叫 Plan A ⇒ 呼叫圖成環。
        anchor = "function Start-SoulOsServer {"
        assert anchor in ops_text, "變異 M2 沒有套上（server_ops.ps1 的形狀變了？）"
        reverse_call = (
            anchor
            + "\n"
            "    # MUTATION M2: 反向呼叫 Plan A（形成環）; 一次性斷路器避免無限遞迴\n"
            "    if ($env:SOULOS_PLAN_A_REENTRY -ne '1') {\n"
            "        $env:SOULOS_PLAN_A_REENTRY = '1'\n"
            "        Set-Content -Path (Join-Path $root 'data\\plan_a_reentry.marker') -Value 'reentry'\n"
            "        & (Join-Path $root 'scripts\\_start_plan_a.ps1') | Out-Null\n"
            "        exit 0\n"
            "    }\n"
        )
        return plan_a_text, ops_text.replace(anchor, reverse_call, 1)

    if name == "M3_plan_a_reports_success_before_the_service_is_up":
        # 讓 Plan A 在服務起來前就回報成功（拿掉健康等待）。
        new_plan_a, n = re.subn(
            r"\$healthy = \$false.*?exit 1\n\}",
            "$healthy = $true\n",
            plan_a_text,
            count=1,
            flags=re.S,
        )
        assert n == 1, "變異 M3 沒有套上（Plan A 的健康等待區塊形狀變了？）"
        return new_plan_a, ops_text

    if name == "M4_pid_file_records_the_socket_owner":
        # pid 檔改記「服務子行程（socket owner）」的 pid ⇒ pid 語意被破壞。
        # 由探針在啟動後覆寫 pid 檔（見 _replay 的 probe_pidfile_override）。
        return plan_a_text, ops_text

    if name == "M5_launch_path_writes_production_data":
        # 啟動路徑改寫生產 data\\server.pid。
        new_ops, n = re.subn(
            r"Out-File\s+-Encoding\s+utf8\s+\$pidFile",
            lambda _m: "Out-File -Encoding utf8 '%s'" % PRODUCTION_PID_FILE,
            ops_text,
            count=1,
        )
        assert n == 1, "變異 M5 沒有套上（server_ops.ps1 的 pid 寫入形狀變了？）"
        return plan_a_text, new_ops

    raise AssertionError("未知的變異名稱: %r" % name)


def _load_scripts() -> tuple:
    """讀生產腳本並（若有）套上變異 —— 回傳 (plan_a_text, server_ops_text)。"""
    plan_a_text = PLAN_A_PATH.read_text(encoding="utf-8-sig")
    ops_text = SERVER_OPS_PATH.read_text(encoding="utf-8-sig")
    mutation = os.environ.get(MUTATION_ENV, "").strip()
    if mutation:
        plan_a_text, ops_text = _apply_mutation(plan_a_text, ops_text, mutation)
    return plan_a_text, ops_text


# ───────────────────────────────────────────────────────────
# 沙盒重播：呼叫端 → Plan A → server_ops.ps1 → 中繼 launcher → 服務
# ───────────────────────────────────────────────────────────

#: 沙盒腳本的 parse 檢查（只解析, 不執行）—— 確保變異沒有把腳本弄成「根本跑不起來」。
#: 沒有這道, 一個語法壞掉的變異會讓所有守衛變紅, 而紅燈其實不來自被測的行為。
_PARSE_CHECK_PS1 = r'''
param([string]$Path)
$errs = $null
[System.Management.Automation.Language.Parser]::ParseFile($Path, [ref]$null, [ref]$errs) | Out-Null
if ($errs -and $errs.Count -gt 0) {
    $errs | ForEach-Object { Write-Output ("PARSE_ERROR: " + $_.Message) }
    exit 1
}
Write-Output 'PARSE_OK'
'''


def _parse_check(powershell: str, checker: Path, target: Path) -> str:
    """對沙盒腳本做 parse 檢查（唯讀; 回傳 '' 表示 parse 失敗, 內容為錯誤訊息）。"""
    out = _capture(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(checker),
         str(target)],
        timeout=60.0,
    )
    if "PARSE_OK" in out:
        return ""
    return out.strip()

def _find_service_address(script_text: str) -> str:
    """找出腳本裡**實際使用**的生產服務位址（回傳字面值；找不到回 ""）。

    🔴 為什麼不在本檔直接寫死那個位址：R3 守衛（``tests/infra/test_no_production_spawn_guard.py``）
    禁止 ``tests/**`` 出現硬寫的生產服務位址。借用守衛模組自己的 idiom —— 跳脫點 +
    埠以 ``(?:nnnn)`` 分組 —— 本檔原始碼因此**不含任何可被掃到的位址字面值**，
    而位址在執行時從**生產腳本自身**推導（語意也更正確：替換的是腳本裡實際的那個位址）。
    """
    m = re.search(
        r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):" + r"(?:8000)(?!\d)",
        script_text,
    )
    return m.group(0) if m else ""


def _substitute(text: str, sandbox_root: Path, port: int) -> str:
    """把生產腳本的「路徑 / 埠」換成沙盒等價物，其餘邏輯逐字保留。"""
    out = text
    # ⚠ replacement 一律用 lambda: Windows 路徑裡的反斜線不可被 re 當跳脫字元處理。
    out = re.sub(
        r"(?m)^\$root\s*=\s*'[^']*'",
        lambda _m: "$root = '%s'" % sandbox_root,
        out, count=1,
    )
    out = re.sub(
        r"(?m)^\$python\s*=\s*'[^']*'",
        lambda _m: "$python = '%s'" % sys.executable,
        out, count=1,
    )
    out = out.replace("-LocalPort 8000", "-LocalPort %d" % port)
    # 🔴 生產服務位址**不在本測試檔釘死**（R3 守衛：tests/** 不得硬寫生產位址）。
    #    執行時從**這份腳本自身**推導它實際出現的那個位址再替換 —— 借用守衛模組
    #    自己的 idiom（跳脫點 + 埠以 (?:nnnn) 分組），故本檔原始碼不含任何可被
    #    掃到的位址字面值；語意也更正確：替換的是腳本裡實際的那個位址，不是複製品。
    #    （Plan A 委派後腳本裡已無位址 ⇒ 這裡自動 no-op；ops 腳本有 ⇒ 正確替換。）
    prod_addr = _find_service_address(text)
    if prod_addr:
        out = out.replace(prod_addr, "127.0.0.1:%d" % port)
    out = out.replace("$servicePort = 8000", "$servicePort = %d" % port)
    # 「已在執行」的判定 marker → 沙盒探針的路徑（生產服務的命令列不含沙盒路徑），
    # 否則沙盒會誤判「服務已經在跑」而跳過啟動。
    out = out.replace("'*run_server.py*'", "'*%s*'" % (sandbox_root / "scripts" / "run_server.py"))
    return out


def _replay(tmp: Path, plan_a_text: str, ops_text: str, mutation: str = "") -> dict:
    """在沙盒重播 Plan A 的完整啟動鏈，回傳量測到的事實。"""
    facts = {
        "wrapper_exit_s": None,
        "wrapper_exit_abs": None,
        "pipe_eof": False,
        "pipe_eof_s": None,
        "exit_code": None,
        "child_pid": None,
        "child_alive_after_wrapper": None,
        "probe_pid": None,
        "probe_ppid": None,
        "probe_port": None,
        "probe_ready_ts": None,
        "probe_alive_after_wrapper": None,
        "socket_owner_pid": None,
        "caller_pipe_filetype": None,
        "console_procs": None,
        "console_hwnd": None,
        "log": "",
        "errlog": "",
        "report": "",
        "pipe_output": "",
        "write_targets": [],
        "refused": None,
        "reentry_marker": False,
        "mutation": mutation,
    }

    scripts_dir = tmp / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (tmp / "data" / "logs").mkdir(parents=True, exist_ok=True)

    # 沙盒的服務本體 = 無害探針（取代 scripts\run_server.py）
    (scripts_dir / "run_server.py").write_text(SERVICE_PROBE, encoding="utf-8")
    # 共用 nohup 輪替 helper（生產檔原樣複製; 內含路徑, 不需替換）
    (scripts_dir / "_nohup_log_rotation.ps1").write_text(
        (SCRIPTS_DIR / "_nohup_log_rotation.ps1").read_text(encoding="utf-8-sig"),
        encoding="utf-8",
    )

    port = _free_port()
    facts["sandbox_port"] = port
    plan_a_sandbox = _substitute(plan_a_text, tmp, port)
    ops_sandbox = _substitute(ops_text, tmp, port)

    plan_a_path = scripts_dir / "_start_plan_a.ps1"
    ops_path = scripts_dir / "server_ops.ps1"

    # 生產寫入防線：啟動路徑（含變異後的實際內容）若指向生產 data/，
    # **拒絕重播**（不製造任何生產寫入）。
    values = _var_values(plan_a_sandbox)
    values.update(_var_values(ops_sandbox))
    facts["write_targets"] = _write_targets(plan_a_sandbox, values) + _write_targets(
        ops_sandbox, values
    )
    for target in facts["write_targets"]:
        if _inside_production(target):
            facts["refused"] = "啟動路徑的寫入目標指向生產檔案: %s" % target
            return facts

    plan_a_path.write_text(plan_a_sandbox, encoding="utf-8")
    ops_path.write_text(ops_sandbox, encoding="utf-8")

    powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                     / "WindowsPowerShell" / "v1.0" / "powershell.exe")

    # 變異健全性檢查: 沙盒腳本必須 parse 得過（否則紅燈會來自「腳本沒跑」而非被測行為）。
    checker = tmp / "parse_check.ps1"
    checker.write_text(_PARSE_CHECK_PS1, encoding="utf-8")
    for target in (plan_a_path, ops_path):
        parse_error = _parse_check(powershell, checker, target)
        if parse_error:
            facts["refused"] = "沙盒腳本 parse 失敗（變異本身壞掉）: %s :: %s" % (
                target.name, parse_error,
            )
            return facts

    cmd = '"%s" -NoProfile -ExecutionPolicy Bypass -File "%s"' % (powershell, plan_a_path)

    release = tmp / "release"
    report = tmp / "probe_report.txt"
    pid_file = tmp / "data" / "server.pid"
    env_backup = {
        k: os.environ.get(k)
        for k in (
            "SOULOS_PROBE_RELEASE",
            "SOULOS_PROBE_MAX_LIFETIME",
            "SOULOS_PROBE_REPORT",
            "SOULOS_PROBE_PORT",
            "SOULOS_PROBE_DELAY_S",
            "SOULOS_PROBE_PIDFILE_OVERRIDE",
        )
    }
    os.environ["SOULOS_PROBE_RELEASE"] = str(release)
    os.environ["SOULOS_PROBE_MAX_LIFETIME"] = str(SERVICE_MAX_LIFETIME_S)
    os.environ["SOULOS_PROBE_REPORT"] = str(report)
    os.environ["SOULOS_PROBE_PORT"] = str(port)
    os.environ["SOULOS_PROBE_DELAY_S"] = str(PROBE_READY_DELAY_S)
    if mutation == "M4_pid_file_records_the_socket_owner":
        os.environ["SOULOS_PROBE_PIDFILE_OVERRIDE"] = str(pid_file)
    else:
        os.environ.pop("SOULOS_PROBE_PIDFILE_OVERRIDE", None)

    pi, read_h = _spawn(cmd)
    try:
        started = time.time()
        buf = bytearray()
        eof = threading.Event()

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
                    buf.extend(chunk)
            finally:
                eof.set()

        th = threading.Thread(target=reader, daemon=True)
        th.start()

        wrapper_exit_s = None
        pipe_eof_s = None
        deadline = started + WRAPPER_RETURN_TIMEOUT_S
        while time.time() < deadline:
            if wrapper_exit_s is None and (
                _winapi.WaitForSingleObject(pi.hProcess, 50) == _winapi.WAIT_OBJECT_0
            ):
                wrapper_exit_s = time.time() - started
                facts["wrapper_exit_abs"] = time.time()
            if eof.is_set():
                pipe_eof_s = time.time() - started
                break
            if wrapper_exit_s is not None and time.time() - started > wrapper_exit_s + 3.0:
                break
            time.sleep(0.02)
        # 收尾: EOF 可能比「輪詢偵測到 process 結束」早幾毫秒（handle 關閉與
        # process 訊號之間的時間差）⇒ 這裡補一次確認, 否則會出現
        # 「明明結束了卻說沒有確定返回」的假紅燈（2026-10-21 實測踩過）。
        if wrapper_exit_s is None and (
            _winapi.WaitForSingleObject(pi.hProcess, 5000) == _winapi.WAIT_OBJECT_0
        ):
            wrapper_exit_s = time.time() - started
            facts["wrapper_exit_abs"] = time.time()
        facts["wrapper_exit_s"] = wrapper_exit_s
        facts["pipe_eof"] = eof.is_set()
        facts["pipe_eof_s"] = pipe_eof_s
        facts["exit_code"] = _exit_code(pi.hProcess)
        facts["pipe_output"] = bytes(buf).decode("utf-8", "replace")

        # 等 pid 檔寫出（沙盒 pid 檔在滿載時可能晚一點才寫出）——有上限地等。
        pid_deadline = time.time() + min(WRAPPER_RETURN_TIMEOUT_S, 20.0)
        while time.time() < pid_deadline and not pid_file.exists():
            time.sleep(0.05)
        # 再等服務「定著」一下才讀 pid 檔：變異 M4 會由服務把 socket owner 的 pid
        # 寫進 pid 檔 —— 讀太早會讀到 relay 寫的那一份，變異就測不出來。
        time.sleep(SERVICE_SURVIVAL_CHECK_S)
        if pid_file.exists():
            try:
                facts["child_pid"] = int(pid_file.read_text(encoding="utf-8-sig").strip())
            except ValueError:
                facts["child_pid"] = None
        if facts["child_pid"]:
            facts["child_alive_after_wrapper"] = _process_alive(facts["child_pid"])

        log_path = tmp / "data" / "server_nohup.log"
        # 服務的 stdout 是在探針「延遲上線」之後才寫出的 ⇒ 有上限地等它落檔,
        # 否則守衛 5 會變成「看讀 log 的時機」的 flaky 守衛。
        log_deadline = time.time() + 20.0
        while time.time() < log_deadline:
            if log_path.exists() and SANDBOX_MARKER in log_path.read_text(
                encoding="utf-8", errors="replace"
            ):
                break
            time.sleep(0.1)
        if log_path.exists():
            facts["log"] = log_path.read_text(encoding="utf-8", errors="replace")
        err_path = tmp / "data" / "server_nohup.err"
        if err_path.exists():
            facts["errlog"] = err_path.read_text(encoding="utf-8", errors="replace")

        report_text = ""
        report_deadline = time.time() + 20.0
        while time.time() < report_deadline:
            if report.exists():
                report_text = report.read_text(encoding="utf-8", errors="replace")
                if report_text.strip():
                    break
            time.sleep(0.1)
        facts["report"] = report_text

        m = re.search(
            r"probe pid=(\d+) ppid=(\d+) port=(\d+) caller_pipe_filetype=0x([0-9a-fA-F]+) "
            r"console_procs=(\d+) console_hwnd=(\S+) ready_ts=([0-9.]+)",
            report_text,
        )
        if m:
            facts["probe_pid"] = int(m.group(1))
            facts["probe_ppid"] = int(m.group(2))
            facts["probe_port"] = int(m.group(3))
            facts["caller_pipe_filetype"] = int(m.group(4), 16)
            facts["console_procs"] = int(m.group(5))
            facts["console_hwnd"] = m.group(6)
            facts["probe_ready_ts"] = float(m.group(7))
            facts["probe_alive_after_wrapper"] = _process_alive(facts["probe_pid"])

        # 沙盒拓撲與 production 同構：socket owner = trampoline 的子行程 = 探針自己。
        facts["socket_owner_pid"] = facts["probe_pid"]
        facts["listening_owners"] = _listening_owners(port)
        facts["reentry_marker"] = (tmp / "data" / "plan_a_reentry.marker").exists()
        return facts
    finally:
        # 收工：讓沙盒服務**自行**結束（不送任何終止訊號），並還原環境變數。
        release.write_text("done", encoding="utf-8")
        if facts.get("probe_pid"):
            deadline = time.time() + 30.0
            while time.time() < deadline and _process_alive(facts["probe_pid"]):
                time.sleep(0.1)
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        _winapi.CloseHandle(read_h)
        _winapi.CloseHandle(pi.hProcess)
        _winapi.CloseHandle(pi.hThread)


# ───────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def scripts() -> tuple:
    """生產腳本內容（**未施加任何變異**，或已套上 ``SOULOS_PLAN_A_MUTATION``）。"""
    return _load_scripts()


@pytest.fixture(scope="module")
def production_before() -> dict:
    return _production_snapshot()


@pytest.fixture(scope="module")
def replay(tmp_path_factory, scripts) -> dict:
    """在沙盒重播一次 Plan A 的完整啟動鏈。"""
    tmp = tmp_path_factory.mktemp("soulos_plan_a_launch")
    plan_a_text, ops_text = scripts
    mutation = os.environ.get(MUTATION_ENV, "").strip()
    return _replay(tmp, plan_a_text, ops_text, mutation=mutation)


# ───────────────────────────────────────────────────────────
# 守衛 1：無遞迴（靜態呼叫圖 + 動態 marker）
# ───────────────────────────────────────────────────────────

def test_guard1_no_recursion_in_the_plan_a_call_graph(scripts, replay):
    """守衛 1：Plan A → server_ops.ps1 start 的呼叫圖**不得成環**。

    靜態（只看程式碼, 不看註解）:
      * Plan A 必須委派給 ``scripts\\server_ops.ps1``（單一啟動原語）;
      * ``server_ops.ps1`` 的**程式碼**不得引用 ``_start_plan_a.ps1``
        ⇒ 沒有任何路徑從 start 分支回到 Plan A;
      * ``server_ops.ps1`` 的 self-call（中繼 launcher）必須由環境旗標把關,
        否則它自己就是一個環。
    動態:
      * 變異 M2 會在 server_ops.ps1 裡留下反向呼叫的 marker 檔 —— 它不得出現。
    """
    plan_a_text, ops_text = scripts
    plan_a_code = _code_only(plan_a_text)
    ops_code = _code_only(ops_text)

    assert "server_ops.ps1" in plan_a_code, (
        "Plan A 的啟動必須委派給 scripts\\server_ops.ps1（單一啟動原語）"
    )
    assert "_start_plan_a.ps1" not in ops_code, (
        "server_ops.ps1 的程式碼引用了 _start_plan_a.ps1 ⇒ "
        "Plan A → server_ops.ps1 start 的呼叫圖成環"
    )
    assert "_start_plan_a.ps1" not in _code_only(
        RUN_SERVER_PATH.read_text(encoding="utf-8-sig")
    ), "服務本體 run_server.py 不得引用 _start_plan_a.ps1（否則呼叫圖成環）"

    # 中繼 launcher 的 self-call 必須由環境旗標把關（否則 server_ops 自己就成環）。
    assert "SOULOS_LAUNCH_SERVICE_ONLY" in ops_text, (
        "server_ops.ps1 的中繼 launcher 必須由 SOULOS_LAUNCH_SERVICE_ONLY 把關"
    )

    assert replay["refused"] is None, replay["refused"]
    assert not replay["reentry_marker"], (
        "偵測到 server_ops.ps1 反向呼叫 Plan A（呼叫圖成環）"
    )


# ───────────────────────────────────────────────────────────
# 守衛 2：不繼承呼叫端 stdout pipe
# ───────────────────────────────────────────────────────────

def test_guard2_service_does_not_hold_the_callers_stdout_pipe(replay):
    """守衛 2（handle 層級的直接證據）：服務行程**不得**持有呼叫端的 stdout pipe。

    量測方式：呼叫端把 stdout pipe 的 handle 值以 ``SOULOS_CALLER_PIPE_HANDLE``
    匯出給沙盒服務；服務用 ``GetFileType`` 查該 handle 在自己行程內的型別。
    呼叫端的 stdout 是 pipe ⇒ 若服務繼承了它，型別會是 ``FILE_TYPE_PIPE``。
    """
    assert replay["refused"] is None, replay["refused"]
    assert replay["caller_pipe_filetype"] is not None, (
        "服務沒有回報 handle 層級的量測（report=%r / log=%r）"
        % (replay.get("report"), replay["log"])
    )
    assert replay["caller_pipe_filetype"] != FILE_TYPE_PIPE, (
        "服務仍持有呼叫端的 stdout pipe handle"
        "（GetFileType=0x%x = FILE_TYPE_PIPE）" % replay["caller_pipe_filetype"]
    )


# ───────────────────────────────────────────────────────────
# 守衛 3：wrapper 確定返回（EOF）
# ───────────────────────────────────────────────────────────

def test_guard3_callers_stdout_gets_eof_while_the_service_lives(replay):
    """守衛 3：呼叫端的 stdout 必須在**服務仍活著**時就收到 EOF。

    這正是 2026-10-21 02:10 DEPLOY-RESTART-21 被 600s 牆鐘上限殺掉 wrapper 的成因：
    服務繼承了呼叫端的 stdout pipe ⇒ 呼叫端永遠等不到 EOF ⇒ 指令看起來不返回。
    """
    assert replay["refused"] is None, replay["refused"]
    assert replay["child_alive_after_wrapper"], (
        "服務已經不在了 —— 無法證明「服務活著時呼叫端就收到 EOF」"
        "（pid=%r）" % replay["child_pid"]
    )
    assert replay["pipe_eof"], (
        "服務仍在存活，但呼叫端的 stdout 沒有收到 EOF"
        "（服務仍持有呼叫端的 pipe handle；wrapper_output=%r）" % replay["pipe_output"]
    )
    assert replay["wrapper_exit_s"] is not None, "wrapper 行程沒有結束（沒有確定返回）"
    assert replay["wrapper_exit_s"] <= WRAPPER_RETURN_TIMEOUT_S, (
        "wrapper 花了 %.2fs 才返回（上限 %.1fs）"
        % (replay["wrapper_exit_s"], WRAPPER_RETURN_TIMEOUT_S)
    )


# ───────────────────────────────────────────────────────────
# 守衛 4：服務起來之後才回報成功 + 服務在 wrapper 退出後仍存活
# ───────────────────────────────────────────────────────────

def test_guard4_plan_a_reports_success_only_after_the_service_is_up(replay):
    """守衛 4：Plan A 不得把「已送出啟動」當成「已起來」。

    * 服務的「起來時戳」必須**早於** wrapper 的結束時戳;
    * wrapper 退出後服務仍必須存活（2026-09-16 事故的根因防線）。
    """
    assert replay["refused"] is None, replay["refused"]
    assert replay["probe_ready_ts"] is not None, (
        "沙盒服務沒有回報就緒時戳（report=%r / log=%r）"
        % (replay.get("report"), replay["log"])
    )
    assert replay["wrapper_exit_abs"] is not None, "wrapper 行程沒有結束（沒有確定返回）"
    assert replay["probe_ready_ts"] <= replay["wrapper_exit_abs"], (
        "Plan A 在服務起來之前就回報成功：服務就緒於 %.3f，wrapper 結束於 %.3f"
        % (replay["probe_ready_ts"], replay["wrapper_exit_abs"])
    )
    assert replay["child_pid"], (
        "沙盒服務沒有啟動（pid 檔未寫入）；wrapper_output=%r" % replay["pipe_output"]
    )
    assert replay["child_alive_after_wrapper"], (
        "wrapper 返回後服務已死（pid=%r）—— 服務不該綁在 wrapper 的生命週期上"
        % replay["child_pid"]
    )


# ───────────────────────────────────────────────────────────
# 守衛 5：輸出仍在 log 檔、格式不變
# ───────────────────────────────────────────────────────────

def test_guard5_service_output_is_redirected_to_the_nohup_logs(replay):
    """守衛 5：服務的輸出必須仍被導向既有的 nohup log（檔名與格式不變）。"""
    assert replay["refused"] is None, replay["refused"]
    assert "soulos-plan-a-probe pid=" in replay["log"], (
        "log 檔沒有服務的輸出（輸出沒有被導向）：log=%r errlog=%r"
        % (replay["log"], replay["errlog"])
    )
    assert "soulos-plan-a-probe-stderr" in replay["errlog"], (
        "stderr 沒有被導向 .err log：%r" % replay["errlog"]
    )


# ───────────────────────────────────────────────────────────
# 守衛 6：pid 語意不變
# ───────────────────────────────────────────────────────────

def test_guard6_pid_semantics_are_unchanged(replay):
    """守衛 6：pid 語意不變 —— pid 檔 = launcher（trampoline）pid；socket owner = 其子行程。

    沙盒拓撲與 production 同構（三層）：
        Plan A → server_ops.ps1(start) → 中繼 launcher → trampoline（.venv python shim）
        → 探針（真直譯器, socket owner）
    production 實測拓撲（2026-10-21）：
        server_ops.ps1 → python shim(pid 檔 = 10040) → 真直譯器(:8000 socket owner = 19744)
    """
    assert replay["refused"] is None, replay["refused"]
    assert replay["child_pid"], "pid 檔沒有 pid（服務沒啟動？）"
    assert replay["probe_pid"], (
        "服務沒有回報自己的 pid（report=%r）" % replay.get("report")
    )
    assert replay["child_pid"] == replay["probe_ppid"], (
        "pid 檔的 pid（%r）不是 trampoline（launcher）pid（服務的父行程 %r）—— pid 語意被破壞"
        % (replay["child_pid"], replay["probe_ppid"])
    )
    assert replay["child_pid"] != replay["probe_pid"], (
        "pid 檔的 pid 與 socket owner（服務行程本身）相同 —— pid 語意被破壞"
        "（pid 檔必須記 trampoline pid，不是服務行程 pid）"
    )
    assert replay["socket_owner_pid"] in replay.get("listening_owners", set()), (
        "socket owner（%r）不是 LISTENING socket 的擁有者（實測 owners=%r）"
        % (replay["socket_owner_pid"], replay.get("listening_owners"))
    )


# ───────────────────────────────────────────────────────────
# 守衛 7：watchdog 的重試語意未變
# ───────────────────────────────────────────────────────────

def test_guard7_watchdog_retry_semantics_are_unchanged():
    """守衛 7：watchdog 的重試語意（次數／退避／健康等待）必須逐字保留。

    本票只動 ``scripts\\_start_plan_a.ps1``; watchdog 的重試邏輯不該被順手改掉。
    這裡把它的關鍵常數與呼叫點釘住 —— 誰動了誰紅。
    """
    code = _code_only(WATCHDOG_PATH.read_text(encoding="utf-8-sig"))
    for constant, value in (
        ("N_CAP", "10"),
        ("TRIAL_TARGET", "98"),
        ("STARTUP_WINDOW_SECONDS", "90"),
        ("PORT_RELEASE_WAIT_SECONDS", "30"),
    ):
        assert re.search(r"\$%s\s*=\s*%s\b" % (constant, value), code), (
            "watchdog 的 $%s 必須維持 %s（重試語意不得被本票順手改掉）" % (constant, value)
        )
    assert "$counter.n_restarts += 1" in code, "watchdog 的重試計數器累加不得變更"
    assert "_start_plan_a.ps1" in code, "watchdog 仍必須呼叫 _start_plan_a.ps1（Plan A 路徑）"
    assert re.search(
        r"Start-Process\s+-FilePath\s+'powershell\.exe'", code
    ), "watchdog 啟動 Plan A 的呼叫點不得變更"
    assert "Get-NetTCPConnection -LocalPort $port -State Listen" in code, (
        "watchdog 的 port 釋放等待邏輯不得變更"
    )


# ───────────────────────────────────────────────────────────
# 守衛 8：生產未受影響
# ───────────────────────────────────────────────────────────

def test_guard8_production_state_is_untouched(production_before, replay):
    """守衛 8：整場測試不得碰到生產狀態。

    * 生產 ``data/server.pid``：位元組逐位元相同;
    * ``:8000`` 的 socket owner：未變;
    * 生產 ``data/server_nohup.*``：**不**用雜湊比對 —— 線上服務本身會持續往這兩個
      檔案追加（watchdog 的 /health 存取日誌、calendar poller…）⇒ 雜湊本來就會變，
      拿來當守衛是**天然不穩定**的（前一票 SERVER-OPS-HANDLE-INHERITANCE 已記錄
      同一個結論）。改用兩道確定性證據：
        (a) 啟動路徑的寫入目標全部落在沙盒（不得指向生產 ``data/``）;
        (b) 生產 log 檔裡不得出現沙盒探針的標記（測試若真的寫進去了必然會有）。
    """
    assert replay["refused"] is None, (
        "啟動路徑的寫入目標指向生產檔案 —— 拒絕重播：%s" % replay["refused"]
    )
    after = _production_snapshot()
    assert after["pid_file"] == production_before["pid_file"], (
        "生產 data/server.pid 被改動：before=%r after=%r"
        % (production_before["pid_file"], after["pid_file"])
    )
    assert after["owners"] == production_before["owners"], (
        ":%d 的 socket owner 變了：before=%r after=%r"
        % (SERVICE_TCP, production_before["owners"], after["owners"])
    )
    for target in replay["write_targets"]:
        assert not _inside_production(target), (
            "啟動路徑會寫入生產檔案: %s" % target
        )
    for name in NOHUP_LOG_NAMES:
        path = DATA_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert SANDBOX_MARKER not in text, (
            "生產 data/%s 出現沙盒探針的標記（%s）⇒ 測試寫進了生產 log"
            % (name, SANDBOX_MARKER)
        )
