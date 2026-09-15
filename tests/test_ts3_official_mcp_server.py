"""
tests/test_ts3_official_mcp_server.py — TS-3 官方標準開源 MCP Server 端到端對照

對照對象：``@modelcontextprotocol/server-filesystem``（官方參考實作，npm，
「最安全輕量」白名單目錄型 server——只允許讀寫 args 指定的目錄）。

對照目的（工單 TS-3：「接入 1-2 個真實 MCP Server」，fixture server 是自建
真實 server，本檔補官方第三方 server 的端到端實證）：
  1. 真實第三方 MCP 進程：npx 啟動 → stdio 握手 → tools/list → close 清理。
  2. Registry 對官方 server 的工具照常走 §2.3 自動歸類三級規則：
     - ``search_files``（description 含 search）   → observe_environment
     - ``get_file_info``（description 含 list/size）→ observe_environment
     - 其餘（read_file / write_file / edit_file 等）→ **無法歸類 → 拒絕註冊**
       （fail-closed：官方 server 的寫檔/讀檔工具不會被 registry 收編）
  3. 歸類成功者若不在顯式權限表 → 語義兜底 ``ask_required``（fail-closed 權限），
     未經 Ask 批准調用 → permission_denied。
  4. 注入 approving AskGate 後，observe 工具真實調用成功（白名單目錄內）。

網路依賴：本檔需要 npx 拉取 npm 包。若 npx 不可用/斷網 → 該組測試 skip
（核心 fixture 端到端驗證不受影響，見 test_ts3_real_mcp_e2e.py）。

有界性（INFRA-TEST-TS3-TIMEOUT，2026-09-14）：
  本檔曾在全庫回歸中**永久卡死**於 npx/npm 拉包路徑（``_spawn`` 走官方 MCP
  SDK 的 ``stdio_client``，子進程 handle 由 SDK 持有 ⇒ 測試側拿不到 pid，且
  ``connect()`` 進入 async context 本身無界）。修法為三層防線：
    1. **模組級前置探針**（``_probe_official_server``，跑一次並記憶化）：用純
       stdlib ``subprocess.run`` 在有界時間內確認 npx 能拉起該 npm 包；失敗即
       ``allow_module_level`` skip 整個模組 ⇒ 後續測試不可能再卡在拉包上。
    2. **每個 await 都有界**（``_bounded``）：connect / list_tools /
       register_mcp_server / call_tool / close 全部經 ``asyncio.wait_for``。
    3. **樹狀強殺 + 一律 finally 清理**：``close()`` 必須在 finally ⇒ 斷言失敗或
       skip 都不再洩漏殭屍進程；超時路徑以 ``_kill_tree`` / 樹狀掃描強殺。

逃生閥：``SOUL_OS_SKIP_NPX_E2E=1`` ⇒ 本模組全數 skip（4 筆；離線/無人值守時
顯式關閉；預設路徑仍會真實嘗試連線，不得作為常用路徑）。

Frozen contract：0 change（只改本測試檔的有界性與清理，斷言語意逐條保留）。
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul.tool_registry import (  # noqa: E402
    CAPABILITY_GROUP_OBSERVE,
    PERM_ASK_REQUIRED,
    PERM_AUTO_APPROVED,
    ToolRegistry,
)
from src.soul.mcp_stdio_client import MCPStdioClientAdapter  # noqa: E402


# ───────────────────────────────────────────────────────────
# 有界性常數（環境變數可覆寫）
# ───────────────────────────────────────────────────────────

NPM_PKG = "@modelcontextprotocol/server-filesystem"

#: 顯式逃生閥：設為 "1" ⇒ 整個模組 skip（離線／無人值守時使用）。
SOUL_OS_SKIP_NPX_E2E_ENV = "SOUL_OS_SKIP_NPX_E2E"

#: 模組級前置探針上限（秒）。npx 冷快取拉包實測約 15s，90s 留足餘裕。
TS3_NPX_PROBE_TIMEOUT_SECS = float(os.environ.get("TS3_NPX_PROBE_TIMEOUT_SECS", "90"))

#: 單一 await 步驟上限（秒）。超過 ⇒ 強制清理並 skip（環境不可用語意）。
TS3_NPX_STEP_TIMEOUT_SECS = float(os.environ.get("TS3_NPX_STEP_TIMEOUT_SECS", "45"))


def _run(coro):
    return asyncio.run(coro)


# ───────────────────────────────────────────────────────────
# 樹狀強殺 / 殘留子進程清理（純 stdlib，永不 raise）
# ───────────────────────────────────────────────────────────


def _clip(text: Any, limit: int = 300) -> str:
    """壓成單行並截斷（供 skip reason 使用，≤300 字元）。"""
    flat = " ".join(str(text).split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 3] + "..."


def _kill_tree(pid: int) -> None:
    """殺掉 pid 及其整棵子樹（best-effort，永不 raise）。

    Windows 用 ``taskkill /PID <pid> /T /F``（``/T`` 帶走 npx → npm → node
    整條鏈）；非 Windows 退化成 ``os.kill(pid, SIGKILL)``。
    """
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
            )
        else:
            os.kill(int(pid), signal.SIGKILL)
    except Exception:
        # 進程可能已死（taskkill 會回非零）——best-effort，一律吞掉。
        pass


#: 一次查詢取得完整進程表（含 CommandLine，供精準過濾 npx/npm 鏈）。
_PS_PROCESS_CENSUS = (
    "Get-CimInstance Win32_Process | "
    "Select-Object ProcessId,ParentProcessId,Name,CommandLine | "
    "ConvertTo-Csv -NoTypeInformation"
)


def _descendant_pids(rows: List[Dict[str, Any]], root_pid: int) -> set:
    """由進程表算出 root_pid 的**遞迴**後代集合。

    必須遞迴：``npx.CMD`` 經 CreateProcess 走 cmd.exe 執行，實測鏈為
    ``python → cmd.exe → node.exe → cmd.exe → node.exe``——真正要殺的
    node.exe 是**孫代**，只比對直接子進程會漏殺（見 INFRA-TEST-TS3-TIMEOUT）。
    """
    children: Dict[int, List[int]] = {}
    for row in rows:
        children.setdefault(row["ParentProcessId"], []).append(row["ProcessId"])
    seen: set = set()
    stack: List[int] = [root_pid]
    while stack:
        current = stack.pop()
        for child_pid in children.get(current, ()):
            if child_pid in seen or child_pid == root_pid:
                continue
            seen.add(child_pid)
            stack.append(child_pid)
    return seen


def _kill_stray_node_children() -> int:
    """強殺本 pytest 進程樹中殘留的 node.exe（以及 npx.CMD 的 cmd.exe 外殼）。

    以**一次**有界 PowerShell 查詢取得進程表，於 Python 端算出後代集合，再逐一
    ``_kill_tree``（Windows ``taskkill /T /F``）。回傳被處理的 pid 數。
    只在超時路徑呼叫（非每次測試，避免拖慢）。永不 raise。
    """
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_PROCESS_CENSUS],
            capture_output=True,
            text=True,
            timeout=20,
        )
        rows: List[Dict[str, Any]] = []
        for raw in csv.DictReader(io.StringIO(result.stdout or "")):
            try:
                rows.append({
                    "ProcessId": int(raw["ProcessId"]),
                    "ParentProcessId": int(raw["ParentProcessId"] or 0),
                    "Name": (raw.get("Name") or ""),
                    "CommandLine": (raw.get("CommandLine") or ""),
                })
            except (TypeError, ValueError, KeyError):
                continue

        stray = _descendant_pids(rows, os.getpid())
        victims: List[int] = []
        for row in rows:
            if row["ProcessId"] not in stray:
                continue
            name = row["Name"].lower()
            # node.exe：真正的 server / npm 執行體。
            # cmd.exe：npx.CMD / npm.CMD 的外殼（僅限本票的 npm 包，避免誤殺）。
            if name == "node.exe" or (name in ("cmd.exe", "powershell.exe") and NPM_PKG in row["CommandLine"]):
                victims.append(row["ProcessId"])

        for pid in victims:
            _kill_tree(pid)
        return len(victims)
    except Exception:
        return 0


async def _bounded(awaitable, secs: float, what: str):
    """所有 await 的唯一入口：有界等待；超時 ⇒ 清理殘留子進程後 skip。

    **必須用 ``asyncio.timeout``（不是 ``asyncio.wait_for``）**：``wait_for`` 會
    把 awaitable 包進**新的 Task**，而官方 MCP SDK 的 ``stdio_client`` 內部用
    ``anyio.create_task_group()``，其 CancelScope 綁定「進入它的那個 task」——
    ``connect()`` 在 wait_for 的子 task 進入、``close()`` 在另一個 task 退出，
    即拋 ``RuntimeError: Attempted to exit cancel scope in a different task
    than it was entered in``（本檔實測 3/4 測試因此失敗）。``asyncio.timeout``
    在原 task 內以 ``call_at`` + ``task.cancel()`` 計時，task 身分不變 ⇒ 邊界
    相同（有界、超時即 TimeoutError）且不破壞 SDK 的 task 親和性。
    """
    try:
        async with asyncio.timeout(secs):
            return await awaitable
    except asyncio.TimeoutError:
        _kill_stray_node_children()
        pytest.skip(f"官方 MCP server {what} 超時（>{secs:.0f}s），已強制清理子進程")


async def _safe_close(client) -> None:
    """finally 專用清理：有界 close ＋ 吞例外。

    ``close`` 超時由 ``_bounded`` 負責呼叫 ``_kill_stray_node_children()``。
    """
    try:
        await _bounded(client.close(), TS3_NPX_STEP_TIMEOUT_SECS, "close")
    except Exception:
        pass


# ───────────────────────────────────────────────────────────
# 模組級前置探針（跑一次並記憶化）
# ───────────────────────────────────────────────────────────

_NPX_PROBE_RESULT: Optional[Tuple[bool, str]] = None


def _probe_official_server() -> Tuple[bool, str]:
    """有界探針：確認 npx 能在 ``TS3_NPX_PROBE_TIMEOUT_SECS`` 內拉起官方 server。

    純 stdlib ``subprocess.run``；executable 取自 ``shutil.which("npx")``（勿硬寫
    npx.ps1）。``stdin=DEVNULL`` ⇒ server 讀到 EOF 後自行退出，因此 run() 會在
    「啟動完成」後立即返回（實測 warm 1.7s / cold cache 15.4s）。

    回傳 ``(ok, detail)``，``detail`` 為截斷 ≤300 字元的摘要（供 skip reason）。
    結果記憶化（module-level cache）⇒ 只跑一次。
    """
    global _NPX_PROBE_RESULT
    if _NPX_PROBE_RESULT is not None:
        return _NPX_PROBE_RESULT

    npx = shutil.which("npx")
    if not npx:
        _NPX_PROBE_RESULT = (False, "shutil.which('npx') 找不到 npx")
        return _NPX_PROBE_RESULT

    whitelist = tempfile.mkdtemp(prefix="ts3_npx_probe_")
    try:
        proc = subprocess.run(
            [npx, "-y", NPM_PKG, whitelist],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=TS3_NPX_PROBE_TIMEOUT_SECS,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"")
        _kill_stray_node_children()
        _NPX_PROBE_RESULT = (False, _clip(
            f"npx -y {NPM_PKG} 超過 {TS3_NPX_PROBE_TIMEOUT_SECS:.0f}s 未啟動"
            f"（TimeoutExpired）; stderr={partial!r}"
        ))
        return _NPX_PROBE_RESULT
    except (FileNotFoundError, OSError) as exc:
        _NPX_PROBE_RESULT = (False, _clip(f"{type(exc).__name__}: {exc}"))
        return _NPX_PROBE_RESULT

    stderr = proc.stderr or ""
    error_markers = ("npm error", "npm ERR!", "ERR_MODULE_NOT_FOUND", "EACCES", "ENOTFOUND")
    if proc.returncode != 0 or any(marker in stderr for marker in error_markers):
        _NPX_PROBE_RESULT = (False, _clip(f"rc={proc.returncode}; stderr={stderr}"))
        return _NPX_PROBE_RESULT

    _NPX_PROBE_RESULT = (True, _clip(f"rc=0; stderr={stderr}"))
    return _NPX_PROBE_RESULT


#: 顯式逃生閥（預設 False ⇒ 真實連線；設 "1" ⇒ 本模組全數 skip）。
_SKIP_NPX_E2E = os.environ.get(SOUL_OS_SKIP_NPX_E2E_ENV) == "1"

# ── 有界前置探針：失敗 ⇒ 整個模組 skip（杜絕卡在 npx/npm 拉包）──
# 逃生閥開啟時不跑探針（離線環境不該白等 90s）；逃生閥本身由下方 pytestmark
# 逐筆 skip（4 筆各自 skip，而非模組級單筆），語意同「跳過整個模組」。
if not _SKIP_NPX_E2E:
    _NPX_PROBE_OK, _NPX_PROBE_DETAIL = _probe_official_server()
    if not _NPX_PROBE_OK:
        pytest.skip(
            "官方 MCP server 無法在有界時間內啟動（npx/npm 拉包或握手超時）："
            f"{_NPX_PROBE_DETAIL}",
            allow_module_level=True,
        )


@pytest.fixture
def isolated_root(tmp_path: Path):
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    yield data_root()
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


class _ApprovingGate:
    def __init__(self):
        self.asked: List[tuple] = []

    def approve(self, tool, args):
        self.asked.append((tool.tool_id, args))
        return True


def _npx_available() -> bool:
    return shutil.which("npx") is not None


pytestmark = [
    pytest.mark.skipif(
        not _npx_available(),
        reason="npx not available in this environment",
    ),
    pytest.mark.skipif(
        _SKIP_NPX_E2E,
        reason=(
            f"顯式逃生閥 {SOUL_OS_SKIP_NPX_E2E_ENV}=1：跳過官方 MCP server 端到端測試"
            "（離線／無人值守用；預設路徑仍會真實連線）"
        ),
    ),
]


class TestOfficialFilesystemServer:
    @staticmethod
    def _spawn(allowed_dir: str) -> MCPStdioClientAdapter:
        """spawn 官方 filesystem server。

        曾因傳入 ``env={"NPM_CONFIG_CACHE": os.environ.get("NPM_CONFIG_CACHE", "")}``
        （本機該變數未設定 ⇒ 實際傳入空字串）疑似導致 npx 異常，改為不覆寫 env：
        讓 adapter 沿用 ambient 環境與使用者既有 npm cache（有 cache 時啟動最快）。
        """
        return MCPStdioClientAdapter("npx", ["-y", NPM_PKG, allowed_dir])

    def test_npx_connection_and_list_tools(self, tmp_path):
        """官方 server 真實進程：npx 啟動 → 握手 → tools/list → close。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                try:
                    await _bounded(client.connect(), TS3_NPX_STEP_TIMEOUT_SECS, "connect")
                except Exception as exc:
                    pytest.skip(f"官方 MCP server 無法啟動（斷網/包不可用?）: {exc}")
                names = [
                    t.name
                    for t in await _bounded(
                        client.list_tools(), TS3_NPX_STEP_TIMEOUT_SECS, "list_tools"
                    )
                ]
                assert "read_file" in names
                assert "search_files" in names
                assert "write_file" in names
                await _bounded(client.close(), TS3_NPX_STEP_TIMEOUT_SECS, "close")
                assert client.connected is False
                return True
            finally:
                await _safe_close(client)

        assert _run(scenario())

    def test_official_server_register_and_classify(self, tmp_path, isolated_root):
        """Registry 對官方 server 工具走 §2.3 三級歸類 + §4.1 權限兜底。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                try:
                    await _bounded(client.connect(), TS3_NPX_STEP_TIMEOUT_SECS, "connect")
                except Exception as exc:
                    pytest.skip(f"官方 MCP server 無法啟動: {exc}")

                reg = ToolRegistry(store_dir=isolated_root)
                tools = await _bounded(
                    reg.register_mcp_server("official-fs", client),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "register_mcp_server",
                )
                by_name = {t.name: t for t in tools}

                # search_files → observe（語義關鍵詞命中 §2.3）
                assert by_name["search_files"].capability_group == CAPABILITY_GROUP_OBSERVE
                assert by_name["get_file_info"].capability_group == CAPABILITY_GROUP_OBSERVE
                # 非顯式表 → 語義兜底權限 ask_required（fail-closed §4.1.1）
                assert by_name["search_files"].permission_class == PERM_ASK_REQUIRED

                # 寫檔/讀檔類無法歸類 → 拒絕註冊（fail-closed，防止寫檔工具被收編）
                assert "official-fs:write_file" not in by_name
                assert "official-fs:read_file" not in by_name
                assert "official-fs:edit_file" not in by_name
                assert reg.get_tool("official-fs:write_file") is None
                await _bounded(client.close(), TS3_NPX_STEP_TIMEOUT_SECS, "close")
                return True
            finally:
                await _safe_close(client)

        assert _run(scenario())

    def test_search_files_ask_required_gate_flow(self, tmp_path, isolated_root):
        """ask_required 真實流程：未經 Ask → 拒絕；注入 approving gate → 真實調用。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            (allowed / "world.txt").write_text("hello from whitelist", encoding="utf-8")

            # 1) 預設 Ask stub → permission_denied（未接真實 Ask UI = fail-closed）
            client1 = self._spawn(str(allowed))
            try:
                try:
                    await _bounded(client1.connect(), TS3_NPX_STEP_TIMEOUT_SECS, "connect")
                except Exception as exc:
                    pytest.skip(f"官方 MCP server 無法啟動: {exc}")
                reg1 = ToolRegistry(store_dir=isolated_root)
                await _bounded(
                    reg1.register_mcp_server("official-deny", client1),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "register_mcp_server",
                )
                res = await _bounded(
                    reg1.call(
                        "official-deny:search_files",
                        {"path": str(allowed), "pattern": "*.txt"},
                        permission_gate=PERM_ASK_REQUIRED,
                    ),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "call_tool",
                )
                assert res.ok is False
                assert res.error == "permission_denied"
            finally:
                await _safe_close(client1)

            # 2) 注入 approving AskGate → 真實調用成功（白名單目錄內）
            gate = _ApprovingGate()
            client2 = self._spawn(str(allowed))
            try:
                await _bounded(client2.connect(), TS3_NPX_STEP_TIMEOUT_SECS, "connect")
                reg2 = ToolRegistry(store_dir=isolated_root, ask_gate=gate)
                await _bounded(
                    reg2.register_mcp_server("official-ok", client2),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "register_mcp_server",
                )
                res2 = await _bounded(
                    reg2.call(
                        "official-ok:search_files",
                        {"path": str(allowed), "pattern": "*"},
                        permission_gate=PERM_ASK_REQUIRED,
                    ),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "call_tool",
                )
                assert res2.ok is True
                assert gate.asked[0][0] == "official-ok:search_files"
            finally:
                await _safe_close(client2)
            return True

        assert _run(scenario())

    def test_official_server_not_projected_after_offline(self, tmp_path, isolated_root):
        """offline → 官方 server 工具不投影（fail-silent，§2.4）。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                try:
                    await _bounded(client.connect(), TS3_NPX_STEP_TIMEOUT_SECS, "connect")
                except Exception as exc:
                    pytest.skip(f"官方 MCP server 無法啟動: {exc}")
                reg = ToolRegistry(store_dir=isolated_root)
                await _bounded(
                    reg.register_mcp_server("official-off", client),
                    TS3_NPX_STEP_TIMEOUT_SECS,
                    "register_mcp_server",
                )
                reg.mark_offline("official-off", "disconnected")
                exprs = {c.id: c.expression for c in reg.project_capabilities()}
                assert "search_files" not in exprs[CAPABILITY_GROUP_OBSERVE]
                await _bounded(client.close(), TS3_NPX_STEP_TIMEOUT_SECS, "close")
                return True
            finally:
                await _safe_close(client)

        assert _run(scenario())
