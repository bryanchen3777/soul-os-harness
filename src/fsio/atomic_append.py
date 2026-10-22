"""src/fsio/atomic_append.py — 行程安全的 append 寫入原語。

Owner 裁決（2026-10-22）：**開立 P1 票 INFRA-CONCURRENT-APPEND-MUTEX**，拒絕長期容忍
flaky 守衛；架構方向＝「輕量互斥鎖，拒絕過度工程（不要 Win32 長命 C handle 生命週期管理）」。

────────────────────────────────────────────────────────────────────────────
🔴 根因（實測定案，非推論）
────────────────────────────────────────────────────────────────────────────
Windows 上 CPython 的 ``open(path, "ab")`` **不是原子追加**：它走 MSVCRT 的 ``_O_APPEND``，
CRT 在每次寫入前用「**該 handle 自己快取的檔案大小**」seek 到 EOF。兩個先後開啟的
handle 各自快取到**同一個（過期）大小** ⇒ 寫到同一個偏移 ⇒ **後者整筆覆蓋前者**。

樣態：可完整解析、**零損壞行**、``write()`` 全部回傳成功 ⇒ 看起來像「整筆無痕跡消失」。

量測（6 個獨立行程產生真實 CPU 負載、30 輪、每輪 2 執行緒 × 30 筆、同一段 append 程式碼）：

| 寫法                                              | 結果              |
|---------------------------------------------------|-------------------|
| 現況 ``open(path,"ab")``（MSVCRT ``_O_APPEND``）   | 30 輪中 **10 輪遺失**（33.3%） |
| 本模組（行程內互斥 + Win32 核心層原子追加）        | **30/30 全對**     |
| 互斥 + ``fstat``/``lseek`` 自算偏移                 | 8/8 檔案大小多出 60 bytes ❌   |

（注意：**不要**用「自己算寫入位置」來修 append 缺陷 —— 那正是把過期 metadata 當真相。）

────────────────────────────────────────────────────────────────────────────
本模組的做法（符合 Owner 裁決的三個要求）
────────────────────────────────────────────────────────────────────────────
1. **行程內互斥鎖**（Owner 指定）：``threading.Lock`` 包住整段寫入序列。
   * asyncio 層刻意**不加**：``append_bytes`` 內部沒有任何 ``await``，同一 event loop
     上的兩個 coroutine 不可能交錯 ⇒ asyncio lock 不帶來任何額外保證，卻會引入
     「lock 綁定特定 loop」的跨 loop 風險。真正需要序列化的是**多執行緒** ⇒ threading.Lock
     才是正確的那一層。
2. **平台原生原子追加**（跨行程安全）：Windows 走 ``CreateFileW(access=FILE_APPEND_DATA)``
   ⇒ **核心層保證寫在真正的檔尾**（不是 CRT 的過期快取）。其他平台維持
   ``open(path, "ab")``（POSIX 的 ``O_APPEND`` 本身就是原子的）。
3. **不持有長命 handle** ⇒ 沒有 handle 生命週期管理、沒有 LRU、沒有淘汰邏輯
   （Owner 明確拒絕的過度工程）。每次寫入仍是 open → write → flush → close。

開檔時帶 ``FILE_SHARE_DELETE`` ⇒ Windows 上不阻擋 pytest 清理 tmp 目錄。

失敗語意：寫入失敗一律 **raise**（由呼叫端決定 fail-silent 與否），與原
``open(path,"ab")`` 的例外語意一致。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Union

__all__ = ["append_bytes", "append_lock", "uses_kernel_atomic_append"]

PathLike = Union[str, "os.PathLike[str]"]

#: 行程內序列化同一行程的所有 append（Owner 裁決指定的「行程內互斥鎖」）。
append_lock = threading.Lock()

_IS_WINDOWS = os.name == "nt"

# 變異實驗用的環境變數：設成 ``legacy_crt_append`` 時**故意**退回 MSVCRT ``_O_APPEND``
# （舊的、有缺陷的寫法），用來證明守衛測試確實會轉紅（雙向變異測試）。
# 只影響 tmp 目錄，**不可能**觸達生產。
MUTATION_ENV = "SOULOS_APPEND_MUTATION"


def _mutation_forces_legacy() -> bool:
    return os.environ.get(MUTATION_ENV, "").strip() == "legacy_crt_append"


if _IS_WINDOWS:  # pragma: no cover - 平台分支
    import ctypes
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _FILE_APPEND_DATA = 0x0004
    _FILE_SHARE_READ = 0x00000001
    _FILE_SHARE_WRITE = 0x00000002
    _FILE_SHARE_DELETE = 0x00000004
    _OPEN_ALWAYS = 4
    _FILE_ATTRIBUTE_NORMAL = 0x00000080
    _INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p,
    ]
    _k32.WriteFile.restype = wintypes.BOOL
    _k32.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    _k32.FlushFileBuffers.restype = wintypes.BOOL
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.CloseHandle.restype = wintypes.BOOL


def uses_kernel_atomic_append() -> bool:
    """本次 append 是否會走「核心層原子追加」。

    Windows（非變異模式）⇒ True（``FILE_APPEND_DATA``）；其他平台 ⇒ False
    （POSIX 的 ``O_APPEND`` 本身即原子，語意等價但走不同 syscall）。
    """
    if _mutation_forces_legacy():
        return False
    return _IS_WINDOWS


def _append_windows(path: Path, blob: bytes) -> None:
    """Windows：以 ``FILE_APPEND_DATA`` 追加（核心層保證落在真正的檔尾）。"""
    handle = _k32.CreateFileW(
        str(path),
        _FILE_APPEND_DATA,
        _FILE_SHARE_READ | _FILE_SHARE_WRITE | _FILE_SHARE_DELETE,
        None,
        _OPEN_ALWAYS,
        _FILE_ATTRIBUTE_NORMAL,
        None,
    )
    if handle == _INVALID_HANDLE_VALUE or handle is None:
        raise OSError(ctypes.get_last_error(), "CreateFileW failed: %s" % path)
    try:
        buf = ctypes.create_string_buffer(blob, len(blob))
        written = wintypes.DWORD(0)
        if not _k32.WriteFile(handle, buf, len(blob), ctypes.byref(written), None):
            raise OSError(ctypes.get_last_error(), "WriteFile failed: %s" % path)
        _k32.FlushFileBuffers(handle)
    finally:
        _k32.CloseHandle(handle)


def _append_posix(path: Path, blob: bytes) -> None:
    """POSIX：``O_APPEND`` 本身就是原子追加（寫入必落在檔尾）。"""
    with open(path, "ab") as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())


def append_bytes(path: PathLike, blob: bytes) -> None:
    """把 ``blob`` 追加到 ``path``（行程內互斥 + 平台原子追加）。

    - 父目錄不存在時**不會**自動建立（由呼叫端決定；與原行為一致）。
    - 寫入失敗一律 raise。
    """
    target = Path(path)
    if not blob:
        return
    with append_lock:
        if _IS_WINDOWS and not _mutation_forces_legacy():
            _append_windows(target, blob)
        else:
            _append_posix(target, blob)
