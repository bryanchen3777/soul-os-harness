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

Frozen contract：0 change。
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _run(coro):
    return asyncio.run(coro)


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


NPM_PKG = "@modelcontextprotocol/server-filesystem"


def _npx_available() -> bool:
    return shutil.which("npx") is not None


pytestmark = pytest.mark.skipif(
    not _npx_available(),
    reason="npx not available in this environment",
)


class TestOfficialFilesystemServer:
    @staticmethod
    def _spawn(allowed_dir: str) -> MCPStdioClientAdapter:
        return MCPStdioClientAdapter(
            "npx", ["-y", NPM_PKG, allowed_dir],
            env={"NPM_CONFIG_CACHE": os.environ.get("NPM_CONFIG_CACHE", "")},
        )

    def test_npx_connection_and_list_tools(self, tmp_path):
        """官方 server 真實進程：npx 啟動 → 握手 → tools/list → close。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                await client.connect()
            except Exception as exc:
                pytest.skip(f"官方 MCP server 無法啟動（斷網/包不可用?）: {exc}")
            names = [t.name for t in await client.list_tools()]
            assert "read_file" in names
            assert "search_files" in names
            assert "write_file" in names
            await client.close()
            assert client.connected is False
            return True

        assert _run(scenario())

    def test_official_server_register_and_classify(self, tmp_path, isolated_root):
        """Registry 對官方 server 工具走 §2.3 三級歸類 + §4.1 權限兜底。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                await client.connect()
            except Exception as exc:
                pytest.skip(f"官方 MCP server 無法啟動: {exc}")

            reg = ToolRegistry(store_dir=isolated_root)
            tools = await reg.register_mcp_server("official-fs", client)
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
            await client.close()
            return True

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
                await client1.connect()
            except Exception as exc:
                pytest.skip(f"官方 MCP server 無法啟動: {exc}")
            reg1 = ToolRegistry(store_dir=isolated_root)
            await reg1.register_mcp_server("official-deny", client1)
            res = await reg1.call(
                "official-deny:search_files", {"path": str(allowed), "pattern": "*.txt"},
                permission_gate=PERM_ASK_REQUIRED,
            )
            assert res.ok is False
            assert res.error == "permission_denied"
            await client1.close()

            # 2) 注入 approving AskGate → 真實調用成功（白名單目錄內）
            gate = _ApprovingGate()
            client2 = self._spawn(str(allowed))
            await client2.connect()
            reg2 = ToolRegistry(store_dir=isolated_root, ask_gate=gate)
            await reg2.register_mcp_server("official-ok", client2)
            res2 = await reg2.call(
                "official-ok:search_files", {"path": str(allowed), "pattern": "*"},
                permission_gate=PERM_ASK_REQUIRED,
            )
            assert res2.ok is True
            assert gate.asked[0][0] == "official-ok:search_files"
            await client2.close()
            return True

        assert _run(scenario())

    def test_official_server_not_projected_after_offline(self, tmp_path, isolated_root):
        """offline → 官方 server 工具不投影（fail-silent，§2.4）。"""
        async def scenario():
            allowed = tmp_path / "whitelist"
            allowed.mkdir(exist_ok=True)
            client = self._spawn(str(allowed))
            try:
                await client.connect()
            except Exception as exc:
                pytest.skip(f"官方 MCP server 無法啟動: {exc}")
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("official-off", client)
            reg.mark_offline("official-off", "disconnected")
            exprs = {c.id: c.expression for c in reg.project_capabilities()}
            assert "search_files" not in exprs[CAPABILITY_GROUP_OBSERVE]
            await client.close()
            return True

        assert _run(scenario())