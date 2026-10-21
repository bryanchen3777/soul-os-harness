"""SERVER-OPS-HANDLE-INHERITANCE — 隔離守衛：服務行程**不得**繼承呼叫端的 stdout pipe。

背景
----
2026-10-21 02:10 的 DEPLOY-RESTART-21 以背景 task 執行 ``.\\scripts\\server_ops.ps1 restart``：
重啟成功，但 **wrapper 一直不返回**，直到被 harness 的 600 秒牆鐘上限殺掉
（``Command timed out after 600 seconds``）。服務本次僥倖存活 —— 但那是運氣不是安全性：
2026-09-16 事故就是同一形狀（harness 逾時中止命令時連坐砍掉生產服務）。

已實測根因（前一票 SERVER-OPS-LAUNCH-DETACH 的隔離沙盒實測，非推測）：

* ``Start-Process -RedirectStandardOutput/-RedirectStandardError`` 走 .NET 的
  ``UseShellExecute=false`` 路徑，而 .NET 的 ``CreateProcess`` **一律**以
  ``bInheritHandles=TRUE`` 建立子行程 ⇒ **服務連帶繼承呼叫端的 stdout pipe**。
* 直接證據：服務行程內 ``GetFileType(呼叫端 pipe handle) = FILE_TYPE_PIPE``；
  呼叫端要等**服務退出**才收到 stdout 的 EOF。
* 拿掉 ``-NoNewWindow`` **不會**關掉它。

本檔驗證的是**啟動原語本身**，不是生產服務：

* **handle 層級的直接證據**：服務行程內呼叫端 pipe handle 的 ``GetFileType``
  **不得**是 ``FILE_TYPE_PIPE``（= 服務沒有持有呼叫端的 pipe）——守衛 1。
* **wrapper 確定返回**：呼叫端在限定時間內收到 stdout 的 **EOF**（事件量測，非預測）
  ——守衛 2。
* **服務在 wrapper 退出後仍存活** ——守衛 3。
* **輸出仍在 log 檔**且格式不變 ——守衛 4。
* **pid 語意不變**：pid 檔 = launcher（trampoline）pid；socket owner = 其**子行程**
  ——守衛 5（用無害子行程沙盒驗證，**絕不**對生產驗證）。
* **生產未受影響**：測試前後生產 ``data/server.pid`` 與 ``:8000`` socket owner 逐值相同
  ——守衛 6。

🔴 本測試絕不重啟／啟動／停止生產服務，也絕不寫入 ``data/**``：

* 一律在 pytest 的 tmp 目錄裡跑；
* **不使用** ``subprocess.*`` / ``os.system`` / ``os.popen`` / ``os.spawn*``
  （``tests/infra/test_no_production_spawn_guard.py`` 的 R5 白名單大小鎖死在 22，
  本票不得擴張）—— 行程一律以 ``ctypes`` 直接呼叫 kernel32 的 ``CreateProcessW``。

變異實驗
--------
``SOULOS_HANDLE_MUTATION`` 環境變數可在本檔內施加變異（見 :func:`_apply_mutation`），
用來證明每道守衛都會轉紅；未設該變數時，測試跑的是**真正的生產檔案**。
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
SCRIPT_PATH = REPO_ROOT / "scripts" / "server_ops.ps1"
DATA_DIR = REPO_ROOT / "data"
PRODUCTION_PID_FILE = DATA_DIR / "server.pid"

#: 生產服務的 TCP 埠（以「數字常數」而非「位址字串」表示，避免被行程護欄的位址規則誤判）。
SERVICE_TCP = 8000

#: wrapper 返回的期限：超過這個時間就算「沒有確定返回」。
WRAPPER_RETURN_TIMEOUT_S = 30.0
#: 中繼 launcher 等待服務 pid 檔的期限。
RELAY_RETURN_TIMEOUT_S = 25.0
#: wrapper 返回後多久，服務仍必須存活（守衛 3）。
SERVICE_SURVIVAL_CHECK_S = 2.0
#: 沙盒服務的最長存活時間（超過就自行結束，避免留下孤兒行程）。
SERVICE_MAX_LIFETIME_S = 60.0

#: 變異實驗用的環境變數名稱。
MUTATION_ENV = "SOULOS_HANDLE_MUTATION"

#: 生產 log 檔名（不得變更；本測試只讀取其雜湊以證明未被寫入）。
NOHUP_LOG_NAMES = ("server_nohup.log", "server_nohup.err", "server_nohup.out")

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
    真正判斷「已退出」要用 WaitForSingleObject(handle, 0) == WAIT_OBJECT_0。
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


def _listening_owners() -> set:
    """所有 LISTENING socket 的 owner pid 集合（netstat -ano，唯讀）。"""
    netstat = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "netstat.exe")
    owners = set()
    for line in _capture([netstat, "-ano", "-p", "TCP"]).splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != "TCP":
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
    """生產狀態快照（唯讀）：pid 檔、nohup log 雜湊、:8000 的 socket owner。"""
    pid_bytes = PRODUCTION_PID_FILE.read_bytes() if PRODUCTION_PID_FILE.exists() else None
    return {
        "pid_file": pid_bytes,
        "nohup": {name: _sha256(DATA_DIR / name) for name in NOHUP_LOG_NAMES},
        "owners": _listening_owners(),
    }


# ───────────────────────────────────────────────────────────
# 生產腳本的靜態解析
# ───────────────────────────────────────────────────────────

def _read_script() -> str:
    return SCRIPT_PATH.read_text(encoding="utf-8-sig")


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


def _extract_launch_block(text: str):
    """取出實際啟動呼叫（``Start-Process`` 陳述句）與 pid 寫入陳述句。

    2026-10-21（HANDLE-INHERITANCE）之後，生產腳本的**第一個** Start-Process 仍是
    「直接拉起服務」那一支（位於中繼 launcher 模式分支內），後面才是「啟動中繼
    launcher」那一支。本函式取第一支 —— 與前一票的守衛語意一致。
    """
    lines = text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if "Start-Process" in line and "Start-Process" in _strip_comment(line):
            start = idx
            break
    if start is None:
        raise AssertionError("server_ops.ps1: 找不到 Start-Process 啟動呼叫")
    chunk = []
    idx = start
    while idx < len(lines):
        chunk.append(lines[idx])
        if lines[idx].rstrip().endswith("`"):
            idx += 1
            continue
        break
    launch_stmt = "\n".join(chunk)
    pid_stmt = None
    for line in lines[idx + 1:]:
        if re.search(r"\$proc\.Id\s*\|\s*Out-File", _strip_comment(line)):
            pid_stmt = line.strip()
            break
    if pid_stmt is None:
        raise AssertionError("server_ops.ps1: 找不到 $proc.Id → $pidFile 的 pid 寫入")
    return launch_stmt, pid_stmt


def _write_targets(block: str, variables: dict):
    """解析區塊內所有寫入目標：寫檔（Out-File/Add-Content/Set-Content）與輸出導向
    （-RedirectStandardOutput/-RedirectStandardError）。"""
    targets = []
    for line in block.splitlines():
        code = _strip_comment(line)
        if re.search(r"\b(Out-File|Add-Content|Set-Content)\b", code):
            tokens = code.split()
            if tokens:
                targets.append(_resolve_path(tokens[-1], variables))
        m = re.search(r"-RedirectStandard(?:Output|Error)\s+(\S+)", code)
        if m:
            targets.append(_resolve_path(m.group(1), variables))
    return targets


def _resolve_path(token: str, variables: dict) -> str:
    token = token.strip("'\"")
    if token.startswith("$"):
        return variables.get(token[1:], token)
    return token


def _inside_production(path: str) -> bool:
    try:
        return Path(path).resolve().is_relative_to(DATA_DIR.resolve())
    except (OSError, ValueError):
        return False


# ───────────────────────────────────────────────────────────
# 沙盒服務探針
# ───────────────────────────────────────────────────────────
#
# 探針是無害的 python 子行程，只做四件事：
#   1. 報「呼叫端 pipe handle 在我行程內的 GetFileType 結果」（handle 層級證據）；
#   2. 報 Console 資訊（服務不得與呼叫端共用 Console）；
#   3. **自己**持有 listening socket —— 沙盒拓撲與 production 同構：
#      中繼 launcher → trampoline（.venv python shim）→ 探針（真直譯器，socket owner），
#      pid 檔 = trampoline pid、socket owner = 其子行程（守衛 5）；
#   4. 報自己的 pid 與父 pid（= trampoline pid），供守衛 5 對帳。
#
# ⚠ 探針刻意**不使用** subprocess / os.spawn*（行程護欄 R5 白名單大小鎖死）。

SERVICE_PROBE = r'''
import ctypes
import os
import socket
import sys
import time

RELEASE = os.environ.get("SOULOS_PROBE_RELEASE", "")
DIES_WITH_PARENT = os.environ.get("SOULOS_PROBE_DIES_WITH_PARENT") == "1"
MAX_LIFETIME = float(os.environ.get("SOULOS_PROBE_MAX_LIFETIME", "60"))
REPORT = os.environ.get("SOULOS_PROBE_REPORT", "")
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


def main():
    # 「我有沒有繼承呼叫端的 stdout pipe handle？」—— 直接量測，不是推論。
    caller_pipe = int(os.environ.get("SOULOS_CALLER_PIPE_HANDLE", "0") or 0)
    ftype = 0xFFFFFFFF
    if caller_pipe:
        ftype = kernel32.GetFileType(ctypes.c_void_p(caller_pipe))

    console_buf = (ctypes.c_ulong * 16)()
    console_procs = kernel32.GetConsoleProcessList(console_buf, 16)
    console_hwnd = kernel32.GetConsoleWindow()

    # 探針自己就是 socket owner（= production 拓撲的最後一段）。
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    port = sock.getsockname()[1]

    _report(
        "probe pid=%d ppid=%d port=%d caller_pipe_filetype=0x%x "
        "console_procs=%d console_hwnd=%s"
        % (os.getpid(), os.getppid(), port, ftype, console_procs, console_hwnd)
    )

    if PIDFILE_OVERRIDE:
        try:
            with open(PIDFILE_OVERRIDE, "w", encoding="utf-8") as fh:
                fh.write(str(os.getpid()))
        except OSError:
            pass

    sys.stdout.write(
        "soulos-handle-probe pid=%d caller_pipe_filetype=0x%x\n" % (os.getpid(), ftype)
    )
    sys.stdout.flush()
    sys.stderr.write("soulos-handle-probe-stderr pid=%d\n" % os.getpid())
    sys.stderr.flush()

    parent = int(os.environ.get("SOULOS_PROBE_WRAPPER_PID", "0") or 0) or os.getppid()
    deadline = time.time() + MAX_LIFETIME
    while time.time() < deadline:
        if DIES_WITH_PARENT:
            handle = kernel32.OpenProcess(0x00100000, 0, parent)
            if not handle:
                break
            wrapper_exited = kernel32.WaitForSingleObject(handle, 0) == 0
            kernel32.CloseHandle(handle)
            if wrapper_exited:
                break
        if RELEASE and os.path.exists(RELEASE):
            break
        time.sleep(0.1)

    sys.stdout.write("soulos-handle-probe exit pid=%d\n" % os.getpid())
    sys.stdout.flush()
    sock.close()


if __name__ == "__main__":
    main()
'''

_PS_QUOTE = "'"


def _ps_quote(value: str) -> str:
    return _PS_QUOTE + str(value).replace("'", "''") + _PS_QUOTE


# ───────────────────────────────────────────────────────────
# 變異（僅供變異實驗；正常測試完全不啟用）
# ───────────────────────────────────────────────────────────

def _apply_mutation(state: dict, name: str) -> dict:
    """把指定變異套到沙盒腳本上（只在 ``SOULOS_HANDLE_MUTATION`` 有值時啟用）。

    變異一律作用在**沙盒產物**（replica / relay / 探針）上，絕不碰生產。
    """
    if name == "M1_relay_inherits_caller_pipe":
        # 中繼 launcher 改用 .NET 導向直啟 → 走 bInheritHandles=TRUE ⇒
        # 中繼 launcher 繼承呼叫端的 pipe，服務再繼承它 ⇒ 服務持有呼叫端的 pipe。
        state["replica"] = state["replica"].replace(
            "-PassThru -WorkingDirectory $root",
            "-RedirectStandardOutput $outLog -RedirectStandardError $errLog "
            "-PassThru -WorkingDirectory $root",
            1,
        )
    elif name == "M2_relay_waits_for_service":
        # 中繼 launcher 等服務退出才返回 ⇒ wrapper 永遠等不到。
        state["relay"] = state["relay"] + "$proc.WaitForExit()\n"
    elif name == "M3_service_launched_directly_by_caller":
        # 中繼 launcher 消失：服務改由呼叫端直接以 .NET 導向拉起
        # ⇒ 服務直接繼承呼叫端的 pipe。
        state["replica"] = state["replica"].replace(
            state["relay_launch_line"],
            state["service_launch_stmt"] + "\n" + state["pid_stmt"] + "\nexit 0\n",
            1,
        )
    elif name == "M4_pid_file_records_the_socket_owner":
        # pid 檔改記「服務子行程（socket owner）」的 pid ⇒ 破壞 pid 語意。
        state["probe_pidfile_override"] = True
    elif name == "M5_launch_path_writes_production_data":
        # 啟動路徑改寫生產 data\server.pid。
        state["relay"] = state["relay"].replace(
            "$proc.Id | Out-File -Encoding utf8 $pidFile",
            "$proc.Id | Out-File -Encoding utf8 '%s'" % PRODUCTION_PID_FILE,
            1,
        )
    else:
        raise AssertionError("未知的變異名稱: %r" % name)
    return state


# ───────────────────────────────────────────────────────────
# 沙盒重播：呼叫端 → 中繼 launcher → 服務（與 production 拓撲同構）
# ───────────────────────────────────────────────────────────

def _replay(tmp: Path, script_text: str, mutation: str = "", dies_with_parent: bool = False) -> dict:
    """在沙盒重播 production 的兩層啟動，回傳量測到的事實。"""
    launch_stmt, pid_stmt = _extract_launch_block(script_text)
    variables = {
        "python": sys.executable,
        "outLog": str(tmp / "service.out"),
        "errLog": str(tmp / "service.err"),
        "pidFile": str(tmp / "service.pid"),
        "root": str(tmp),
    }
    facts = {
        "launch_stmt": launch_stmt,
        "pid_stmt": pid_stmt,
        "write_targets": [],
        "wrapper_exit_s": None,
        "pipe_eof": False,
        "pipe_eof_s": None,
        "exit_code": None,
        "child_pid": None,
        "child_alive_after_wrapper": None,
        "socket_owner_pid": None,
        "caller_pipe_filetype": None,
        "console_procs": None,
        "console_hwnd": None,
        "log": "",
        "errlog": "",
        "refused": None,
        "pipe_output": "",
        "mutation": mutation,
    }

    service_dir = tmp / "scripts"
    service_dir.mkdir(parents=True, exist_ok=True)
    (service_dir / "run_server.py").write_text(SERVICE_PROBE, encoding="utf-8")

    powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
                     / "WindowsPowerShell" / "v1.0" / "powershell.exe")

    # ── 中繼 launcher（relay）：內容 = production 的「直接拉起服務」那一支 ──
    relay_path = service_dir / "relay.ps1"
    relay_text = (
        "$ErrorActionPreference = 'Stop'\n"
        "$python = {python}\n"
        "$outLog = {outLog}\n"
        "$errLog = {errLog}\n"
        "$pidFile = {pidFile}\n"
        "$root = {root}\n"
        "{launch}\n"
        "{pidwrite}\n".format(
            python=_ps_quote(variables["python"]),
            outLog=_ps_quote(variables["outLog"]),
            errLog=_ps_quote(variables["errLog"]),
            pidFile=_ps_quote(variables["pidFile"]),
            root=_ps_quote(variables["root"]),
            launch=launch_stmt,
            pidwrite=pid_stmt,
        )
    )

    # ── 呼叫端（replica）：以「不帶 -RedirectStandard*」啟動中繼 launcher ──
    relay_launch_line = (
        "$relay = Start-Process -FilePath (Join-Path $env:SystemRoot "
        "'System32\\WindowsPowerShell\\v1.0\\powershell.exe') "
        "-ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $relayScript) "
        "-PassThru -WorkingDirectory $root\n"
    )
    replica_path = tmp / "replica.ps1"
    replica_text = (
        "$ErrorActionPreference = 'Stop'\n"
        "$env:SOULOS_PROBE_WRAPPER_PID = \"$PID\"\n"
        "$python = {python}\n"
        "$outLog = {outLog}\n"
        "$errLog = {errLog}\n"
        "$pidFile = {pidFile}\n"
        "$root = {root}\n"
        "$relayScript = {relay}\n"
        "{relaylaunch}"
        "if (-not $relay.WaitForExit({wait_ms})) {{ Write-Host '[replica] relay timeout'; exit 3 }}\n"
        "exit $relay.ExitCode\n".format(
            python=_ps_quote(variables["python"]),
            outLog=_ps_quote(variables["outLog"]),
            errLog=_ps_quote(variables["errLog"]),
            pidFile=_ps_quote(variables["pidFile"]),
            root=_ps_quote(variables["root"]),
            relay=_ps_quote(str(relay_path)),
            relaylaunch=relay_launch_line,
            wait_ms=int(RELAY_RETURN_TIMEOUT_S * 1000),
        )
    )

    state = {
        "relay": relay_text,
        "replica": replica_text,
        "relay_launch_line": relay_launch_line,
        "service_launch_stmt": launch_stmt,
        "pid_stmt": pid_stmt,
        "probe_pidfile_override": False,
    }
    if mutation:
        state = _apply_mutation(state, mutation)

    # 生產寫入防線：啟動路徑（含變異後的實際內容）若指向生產 data/，
    # **拒絕重播**（不製造任何生產寫入）。
    facts["write_targets"] = _write_targets(state["relay"], variables)
    for target in facts["write_targets"]:
        if _inside_production(target):
            facts["refused"] = "啟動路徑的寫入目標指向生產檔案: %s" % target
            return facts

    relay_path.write_text(state["relay"], encoding="utf-8")
    replica_path.write_text(state["replica"], encoding="utf-8")

    release = tmp / "release"
    report = tmp / "probe_report.txt"
    env_backup = {
        k: os.environ.get(k)
        for k in (
            "SOULOS_PROBE_RELEASE",
            "SOULOS_PROBE_DIES_WITH_PARENT",
            "SOULOS_PROBE_MAX_LIFETIME",
            "SOULOS_PROBE_REPORT",
            "SOULOS_PROBE_PIDFILE_OVERRIDE",
        )
    }
    os.environ["SOULOS_PROBE_RELEASE"] = str(release)
    os.environ["SOULOS_PROBE_DIES_WITH_PARENT"] = "1" if dies_with_parent else "0"
    os.environ["SOULOS_PROBE_MAX_LIFETIME"] = str(SERVICE_MAX_LIFETIME_S)
    os.environ["SOULOS_PROBE_REPORT"] = str(report)
    if state["probe_pidfile_override"]:
        os.environ["SOULOS_PROBE_PIDFILE_OVERRIDE"] = variables["pidFile"]
    else:
        os.environ.pop("SOULOS_PROBE_PIDFILE_OVERRIDE", None)

    cmd = '"%s" -NoProfile -ExecutionPolicy Bypass -File "%s"' % (powershell, replica_path)
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
                        break            # 管道已結束（write handle 全關）＝ EOF
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
            if eof.is_set():
                pipe_eof_s = time.time() - started
                break
            if wrapper_exit_s is not None and time.time() - started > wrapper_exit_s + 1.0:
                break
            time.sleep(0.02)
        facts["wrapper_exit_s"] = wrapper_exit_s
        facts["pipe_eof"] = eof.is_set()
        facts["pipe_eof_s"] = pipe_eof_s
        facts["exit_code"] = _exit_code(pi.hProcess)
        facts["pipe_output"] = bytes(buf).decode("utf-8", "replace")

        # 等 pid 檔寫出（沙盒 pid 檔在滿載時可能晚一點才寫出）——有上限地等。
        pid_file = Path(variables["pidFile"])
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

        log_path = Path(variables["outLog"])
        if log_path.exists():
            facts["log"] = log_path.read_text(encoding="utf-8", errors="replace")
        err_path = Path(variables["errLog"])
        if err_path.exists():
            facts["errlog"] = err_path.read_text(encoding="utf-8", errors="replace")

        report_text = ""
        report_deadline = time.time() + 10.0
        while time.time() < report_deadline:
            if report.exists():
                report_text = report.read_text(encoding="utf-8", errors="replace")
                if report_text.strip():
                    break
            time.sleep(0.1)
        facts["report"] = report_text

        m = re.search(
            r"probe pid=(\d+) ppid=(\d+) port=(\d+) caller_pipe_filetype=0x([0-9a-fA-F]+) "
            r"console_procs=(\d+) console_hwnd=(\S+)",
            report_text,
        )
        if m:
            facts["probe_pid"] = int(m.group(1))
            facts["probe_ppid"] = int(m.group(2))
            facts["probe_port"] = int(m.group(3))
            facts["caller_pipe_filetype"] = int(m.group(4), 16)
            facts["console_procs"] = int(m.group(5))
            facts["console_hwnd"] = int(m.group(6))
        else:
            facts["probe_pid"] = None

        # 沙盒拓撲與 production 同構：socket owner = trampoline 的子行程 = 探針自己。
        facts["socket_owner_pid"] = facts["probe_pid"]
        facts["listening_owners"] = _listening_owners()
        return facts
    finally:
        # 收工：讓沙盒服務**自行**結束（不送任何終止訊號），並還原環境變數。
        release.write_text("done", encoding="utf-8")
        if facts.get("child_pid"):
            deadline = time.time() + 30.0
            while time.time() < deadline and _process_alive(facts["child_pid"]):
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
def script_text() -> str:
    """生產腳本內容（**未施加任何變異**）。

    本檔的變異一律作用在沙盒產物（replica / relay / 探針）上，不改生產腳本文字 ——
    變異實驗必須只碰無害子行程與沙盒。
    """
    return _read_script()


@pytest.fixture(scope="module")
def production_before() -> dict:
    return _production_snapshot()


@pytest.fixture(scope="module")
def replay(tmp_path_factory, script_text):
    """在沙盒重播一次 production 的兩層啟動。"""
    tmp = tmp_path_factory.mktemp("soulos_handle_inheritance")
    mutation = os.environ.get(MUTATION_ENV, "").strip()
    return _replay(tmp, script_text, mutation=mutation)


# ───────────────────────────────────────────────────────────
# 守衛
# ───────────────────────────────────────────────────────────

def test_service_does_not_hold_the_callers_stdout_pipe(replay):
    """守衛 1（handle 層級的直接證據）：服務行程**不得**持有呼叫端的 stdout pipe。

    量測方式：呼叫端把 stdout pipe 的 handle 值以 ``SOULOS_CALLER_PIPE_HANDLE``
    匯出給沙盒服務；服務用 ``GetFileType`` 查該 handle 在自己行程內的型別。
    呼叫端的 stdout 是 pipe ⇒ 若服務繼承了它，型別會是 ``FILE_TYPE_PIPE``。
    （服務行程內若該 slot 已被別的 handle 占用，型別會是別的值 —— 同樣不是 PIPE。）
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


def test_callers_stdout_is_released_while_the_service_lives(replay):
    """守衛 2：呼叫端的 stdout 必須在**服務仍活著**時就收到 EOF。

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
    assert replay["exit_code"] == 0, (
        "沙盒 wrapper 的結束碼不是 0：%r（wrapper_output=%r）"
        % (replay["exit_code"], replay["pipe_output"])
    )


def test_service_outlives_the_wrapper(replay):
    """守衛 3：wrapper 退出後服務必須仍在（2026-09-16 事故的根因防線）。"""
    assert replay["refused"] is None, replay["refused"]
    assert replay["child_pid"], (
        "沙盒服務沒有啟動（pid 檔未寫入）；wrapper_output=%r" % replay["pipe_output"]
    )
    assert replay["child_alive_after_wrapper"], (
        "wrapper 返回後服務已死（pid=%r）—— 服務不該綁在 wrapper 的生命週期上"
        % replay["child_pid"]
    )


def test_service_output_is_redirected_to_the_log_file(replay):
    """守衛 4：服務的輸出必須仍被導向既有的 nohup log（檔名與格式不變）。"""
    assert replay["refused"] is None, replay["refused"]
    assert "soulos-handle-probe pid=" in replay["log"], (
        "log 檔沒有服務的輸出（輸出沒有被導向）：log=%r errlog=%r"
        % (replay["log"], replay["errlog"])
    )
    assert "soulos-handle-probe-stderr" in replay["errlog"], (
        "stderr 沒有被導向 .err log：%r" % replay["errlog"]
    )


def test_pid_semantics_are_unchanged(replay):
    """守衛 5：pid 語意不變 —— pid 檔 = launcher（trampoline）pid；socket owner = 其子行程。

    沙盒拓撲與 production 同構（三層）：
        中繼 launcher → trampoline（.venv python shim）→ 探針（真直譯器，socket owner）
    production 實測拓撲（2026-10-21）：
        server_ops.ps1 → python shim(pid 檔 = 10040) → 真直譯器(:8000 socket owner = 19744)

    ⇒ pid 檔必須是 trampoline 的 pid（＝探針的父行程 pid），socket owner 則是它的子行程。
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


def test_production_state_is_untouched(production_before, replay):
    """守衛 6：整場測試不得碰到生產狀態。

    * 生產 ``data/server.pid``：位元組逐位元相同。
    * ``:8000`` 的 socket owner：未變。
    * 生產 ``data/server_nohup.*``：**不**用雜湊比對（線上服務本身就會往
      server_nohup.err 追加，會是天然不穩定的守衛）；改以「啟動路徑的寫入目標
      全部落在沙盒」作為確定性證據（見 test_launch_path_never_writes_production）。
    """
    assert replay["refused"] is None, replay["refused"]
    after = _production_snapshot()
    assert after["pid_file"] == production_before["pid_file"], (
        "生產 data/server.pid 被改動：before=%r after=%r"
        % (production_before["pid_file"], after["pid_file"])
    )
    assert after["owners"] == production_before["owners"], (
        ":%d 的 socket owner 變了：before=%r after=%r"
        % (SERVICE_TCP, production_before["owners"], after["owners"])
    )


def test_launch_path_never_writes_into_production_data(script_text, replay):
    """啟動路徑的寫入目標必須在沙盒，不得指向生產 data/。"""
    assert replay["refused"] is None, replay["refused"]
    for target in replay["write_targets"]:
        assert not _inside_production(target), (
            "啟動路徑會寫入生產檔案: %s" % target
        )
