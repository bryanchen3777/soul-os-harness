"""
tests/test_tool_registry.py — TS-2 Tooling & MCP Contract：tool_registry 單元測試

覆蓋（工單 TS-2 驗收）：
  - 動態註冊表：register_mcp_server（唯一入口）/ unregister_mcp_server（唯一入口）
  - 自動歸類三級規則（§2.3）：顯式映射表 > 語義關鍵詞兜底 > 無法歸類拒絕註冊（fail-closed）
  - 分組聚合（§2.5）：project_capabilities = 靜態 3 組 + 動態 healthy 組合併投影；
    註冊表空 → 與現狀完全等價（fail-silent）
  - 健康三態（§2.4）：healthy / degraded / offline；offline → 不投影 + 拒絕調用
  - 5s 硬超時 + Fail-closed 降級（§4.2/§4.3）：超時/異常 → 降級至空結果或預設快取，
    絕不 raise、絕不重試風暴（預設 0 自動重試）
  - 權限分級（§4.1）：唯讀感知類 auto_approved；敏感類 ask_required；
    Ask 守門 stub（v1 未接通 → 一律拒絕 = fail-closed）

Frozen contract：0 change（本測試不改任何 production 檔案；sidecar 寫入
隔離 data_root 的 tmp 目錄）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul.tool_registry import (  # noqa: E402
    CAPABILITY_GROUP_COMMUNICATE,
    CAPABILITY_GROUP_OBSERVE,
    CAPABILITY_GROUP_REFLECT,
    HEALTH_DEGRADED,
    HEALTH_HEALTHY,
    HEALTH_OFFLINE,
    PERM_ASK_REQUIRED,
    PERM_AUTO_APPROVED,
    ToolRegistry,
    classify_tool,
    permission_class_for,
)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

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


class FakeMCPClient:
    """可編程 MCP client 假件（async，符合 ToolRegistry 的 MCPClient 鴨子型別）。"""

    def __init__(
        self,
        tools: List[Dict[str, Any]],
        *,
        call_behavior: str = "return",
        call_data: Any = None,
        call_delay: float = 0.0,
        list_tools_error: Optional[Exception] = None,
    ) -> None:
        self._tools = tools
        self.call_behavior = call_behavior  # "return" | "raise" | "sleep"
        self.call_data = call_data
        self.call_delay = call_delay
        self.list_tools_error = list_tools_error
        self.calls: List[tuple] = []  # (name, args)

    async def list_tools(self) -> Any:
        if self.list_tools_error is not None:
            raise self.list_tools_error
        return {"tools": list(self._tools)}

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self.calls.append((name, arguments))
        if self.call_behavior == "sleep":
            await asyncio.sleep(self.call_delay)
            return self.call_data
        if self.call_behavior == "raise":
            raise self.call_data if isinstance(self.call_data, Exception) else RuntimeError("boom")
        return self.call_data


def _tool(name: str, description: str = "", input_schema: Optional[dict] = None) -> Dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": input_schema or {"type": "object", "properties": {}},
    }


def _registry(**kwargs) -> ToolRegistry:
    return ToolRegistry(**kwargs)


def _expressions(projected) -> Dict[str, str]:
    return {c.id: c.expression for c in projected}


# ────────────────────────────────────────────────────────────
# §2.3 自動歸類三級規則
# ────────────────────────────────────────────────────────────

class TestClassify:
    def test_explicit_map_wins(self):
        assert classify_tool("weather", "unrelated desc") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("calendar", "") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("news", "") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("web_search", "") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("time", "") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("search", "") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("message_send", "") == CAPABILITY_GROUP_COMMUNICATE
        assert classify_tool("telegram_send", "") == CAPABILITY_GROUP_COMMUNICATE
        assert classify_tool("dm_send", "") == CAPABILITY_GROUP_COMMUNICATE
        assert classify_tool("memory_search", "") == CAPABILITY_GROUP_REFLECT
        assert classify_tool("diary_read", "") == CAPABILITY_GROUP_REFLECT
        assert classify_tool("memory_retrieve", "") == CAPABILITY_GROUP_REFLECT

    def test_semantic_keyword_fallback(self):
        # 感知類（description 命中）
        assert classify_tool("get_current", "查詢最新天氣狀況") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("fetch", "Get the news headlines") == CAPABILITY_GROUP_OBSERVE
        assert classify_tool("lookup", "搜索網頁") == CAPABILITY_GROUP_OBSERVE
        # 發送類
        assert classify_tool("broadcast", "Send a message to user") == CAPABILITY_GROUP_COMMUNICATE
        assert classify_tool("notify_user", "發送通知") == CAPABILITY_GROUP_COMMUNICATE
        # 記憶類
        assert classify_tool("recall", "查詢記憶庫") == CAPABILITY_GROUP_REFLECT
        assert classify_tool("read_note", "Read diary entries") == CAPABILITY_GROUP_REFLECT

    def test_memory_search_ambiguous_prefers_reflect(self):
        # 「memory search」同時含 memory（reflect）與 search（observe）→ 最特異的記憶類優先
        assert classify_tool("memory_search_v2", "Search my memory") == CAPABILITY_GROUP_REFLECT

    def test_unclassifiable_returns_none(self):
        assert classify_tool("foo_bar", "a completely unrelated description") is None
        assert classify_tool("x123", "") is None


class TestPermissionClass:
    def test_explicit_map_permissions(self):
        assert permission_class_for("weather", None) == PERM_AUTO_APPROVED
        assert permission_class_for("diary_read", None) == PERM_AUTO_APPROVED
        assert permission_class_for("message_send", None) == PERM_ASK_REQUIRED
        assert permission_class_for("telegram_send", None) == PERM_ASK_REQUIRED

    def test_semantic_fallback_defaults_ask_required(self):
        # 語義兜底（非顯式表）無法確認唯讀性 → 一律敏感（fail-closed，§4.1.1）
        assert permission_class_for("get_current", CAPABILITY_GROUP_OBSERVE) == PERM_ASK_REQUIRED


# ────────────────────────────────────────────────────────────
# 註冊 / 註銷（§2.2 唯一入口）
# ────────────────────────────────────────────────────────────

class TestRegisterUnregister:
    def test_register_categorizes_all_three_groups(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([
            _tool("weather", "天氣查詢"),
            _tool("message_send", ""),
            _tool("memory_search", ""),
        ])
        tools = _run(reg.register_mcp_server("server-a", client))
        assert len(tools) == 3
        by_name = {t.name: t for t in tools}
        assert by_name["weather"].capability_group == CAPABILITY_GROUP_OBSERVE
        assert by_name["message_send"].capability_group == CAPABILITY_GROUP_COMMUNICATE
        assert by_name["memory_search"].capability_group == CAPABILITY_GROUP_REFLECT
        assert by_name["weather"].tool_id == "server-a:weather"

    def test_unclassifiable_tool_rejected_others_registered(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([
            _tool("weather", ""),
            _tool("weird_internal_op", "completely unrelated internal thing"),
            _tool("calendar", ""),
        ])
        tools = _run(reg.register_mcp_server("server-b", client))
        names = {t.name for t in tools}
        assert names == {"weather", "calendar"}  # 無法歸類者被拒，其餘照常（fail-closed 單工具）
        assert reg.get_tool("server-b:weird_internal_op") is None

    def test_register_failure_marks_offline(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([_tool("weather", "")], list_tools_error=RuntimeError("conn refused"))
        tools = _run(reg.register_mcp_server("server-bad", client))
        assert tools == []
        assert reg.health_snapshot()["server-bad"] == HEALTH_OFFLINE

    def test_unregister_removes_tools_and_cache(self, isolated_root):
        reg = _registry()
        _run(reg.register_mcp_server("server-c", FakeMCPClient([_tool("weather", ""), _tool("calendar", "")])))
        _run(reg.call("server-c:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        reg.unregister_mcp_server("server-c")
        assert reg.list_tools() == []
        assert reg.health_snapshot() == {}


# ────────────────────────────────────────────────────────────
# §2.5 投影合併（capability.py 0 改動）
# ────────────────────────────────────────────────────────────

class TestProjectCapabilities:
    def test_empty_registry_equals_static(self, isolated_root):
        reg = _registry()
        projected = reg.project_capabilities()
        assert [c.id for c in projected] == [
            CAPABILITY_GROUP_COMMUNICATE, CAPABILITY_GROUP_OBSERVE, CAPABILITY_GROUP_REFLECT,
        ]
        # 註冊表空 → expression 與靜態定義完全等價（fail-silent）
        from src.soul.capability import CAPABILITY_DEFINITIONS
        for c in projected:
            assert c.expression == CAPABILITY_DEFINITIONS[c.id].expression

    def test_healthy_tools_merge_into_expression(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([_tool("weather", ""), _tool("memory_search", "")])
        _run(reg.register_mcp_server("server-d", client))
        exprs = _expressions(reg.project_capabilities())
        assert "weather" in exprs[CAPABILITY_GROUP_OBSERVE]
        assert "memory_search" in exprs[CAPABILITY_GROUP_REFLECT]
        assert "message_send" not in exprs[CAPABILITY_GROUP_COMMUNICATE]  # 組內無 healthy 工具

    def test_offline_group_not_projected(self, isolated_root):
        reg = _registry()
        _run(reg.register_mcp_server("server-e", FakeMCPClient([_tool("weather", "")])))
        reg.mark_offline("server-e", "disconnected")
        exprs = _expressions(reg.project_capabilities())
        # offline → 不投影：observe 組回退到靜態 expression（無 weather 明細）
        from src.soul.capability import CAPABILITY_DEFINITIONS
        assert exprs[CAPABILITY_GROUP_OBSERVE] == CAPABILITY_DEFINITIONS[CAPABILITY_GROUP_OBSERVE].expression

    def test_capabilities_deterministic(self, isolated_root):
        reg = _registry()
        _run(reg.register_mcp_server("server-f", FakeMCPClient([_tool("news", ""), _tool("weather", "")])))
        a = _expressions(reg.project_capabilities())
        b = _expressions(reg.project_capabilities())
        assert a == b


# ────────────────────────────────────────────────────────────
# §4.2/§4.3 調用：成功 / 硬超時 / 異常 / offline / 快取
# ────────────────────────────────────────────────────────────

class TestCall:
    def test_call_success(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([_tool("weather", "")], call_data={"temp": 21, "cond": "sunny"})
        _run(reg.register_mcp_server("server-s", client))
        result = _run(reg.call("server-s:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert result.ok is True
        assert result.data == {"temp": 21, "cond": "sunny"}
        assert result.degraded is False
        assert result.cached is False
        assert client.calls == [("weather", {})]

    def test_call_timeout_degrades_no_crash(self, isolated_root):
        reg = _registry(call_timeout=0.05, offline_after_consecutive_failures=2)
        client = FakeMCPClient([_tool("weather", "")], call_behavior="sleep", call_delay=1.0)
        _run(reg.register_mcp_server("server-t", client))
        # 第一次：degraded（超時 → 放棄 → 降級空結果，不 raise）
        res1 = _run(reg.call("server-t:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res1.ok is False
        assert res1.data is None
        assert res1.degraded is True
        assert res1.error is not None and "timeout" in res1.error
        assert reg.health_snapshot()["server-t"] == HEALTH_DEGRADED
        # 第二次：連續失敗達閾值 → offline
        res2 = _run(reg.call("server-t:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res2.degraded is True
        assert reg.health_snapshot()["server-t"] == HEALTH_OFFLINE

    def test_call_exception_degrades(self, isolated_root):
        reg = _registry(offline_after_consecutive_failures=2)
        client = FakeMCPClient([_tool("calendar", "")], call_behavior="raise",
                               call_data=RuntimeError("mcp gone"))
        _run(reg.register_mcp_server("server-x", client))
        res = _run(reg.call("server-x:calendar", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res.ok is False
        assert res.degraded is True
        assert "RuntimeError" in (res.error or "")
        assert reg.health_snapshot()["server-x"] == HEALTH_DEGRADED

    def test_call_offline_rejected(self, isolated_root):
        reg = _registry()
        _run(reg.register_mcp_server("server-o", FakeMCPClient([_tool("weather", "")])))
        reg.mark_offline("server-o", "disconnected")
        res = _run(reg.call("server-o:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res.ok is False
        assert res.error == "server_offline: disconnected"
        assert res.degraded is True

    def test_call_unknown_tool_degrades(self, isolated_root):
        reg = _registry()
        res = _run(reg.call("nope:nope", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res.ok is False
        assert res.error == "tool_not_found"

    def test_call_cached_fallback_on_timeout(self, isolated_root):
        reg = _registry(call_timeout=0.05, offline_after_consecutive_failures=5)
        # 第一次成功 → 快取
        ok_client = FakeMCPClient([_tool("weather", "")], call_data={"temp": 21})
        _run(reg.register_mcp_server("server-c1", ok_client))
        res_ok = _run(reg.call("server-c1:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res_ok.ok is True
        # 同一 server 換成掛掉的 client（快取仍在）→ 超時降級走預設快取
        reg._servers["server-c1"]["client"] = FakeMCPClient(
            [_tool("weather", "")], call_behavior="sleep", call_delay=1.0)
        res_cached = _run(reg.call("server-c1:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res_cached.degraded is True
        assert res_cached.cached is True
        assert res_cached.data == {"temp": 21}  # 快取兜底
        assert res_cached.ok is True  # 資料仍可用（staleness 由呼叫方標註）

    def test_call_no_auto_retry_default(self, isolated_root):
        # 預設 0 自動重試：異常不重試風暴（§3.2）
        reg = _registry()
        client = FakeMCPClient([_tool("news", "")], call_behavior="raise",
                               call_data=RuntimeError("fail"))
        _run(reg.register_mcp_server("server-r", client))
        _run(reg.call("server-r:news", {}, permission_gate=PERM_AUTO_APPROVED))
        assert len(client.calls) == 1


# ────────────────────────────────────────────────────────────
# §4.1 權限分級 + Ask 守門 stub
# ────────────────────────────────────────────────────────────

class _ApprovingGate:
    def __init__(self):
        self.asked: List[tuple] = []

    def approve(self, tool, args):
        self.asked.append((tool.tool_id, args))
        return True


class _DenyingGate:
    def approve(self, tool, args):
        return False


class TestPermissionGate:
    def test_auto_approved_runs_without_ask(self, isolated_root):
        reg = _registry()
        client = FakeMCPClient([_tool("weather", "")], call_data="ok")
        _run(reg.register_mcp_server("server-p1", client))
        res = _run(reg.call("server-p1:weather", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res.ok is True
        assert len(client.calls) == 1

    def test_ask_required_default_stub_denies(self, isolated_root):
        # v1 Ask stub 未接通 → 一律拒絕（fail-closed，§4.1.3），且工具未被執行
        reg = _registry()
        client = FakeMCPClient([_tool("message_send", "")], call_data="sent")
        _run(reg.register_mcp_server("server-p2", client))
        res = _run(reg.call("server-p2:message_send", {"to": "bryan"},
                            permission_gate=PERM_ASK_REQUIRED))
        assert res.ok is False
        assert res.error == "permission_denied"
        assert client.calls == []  # 守門拒絕 → 工具零呼叫

    def test_ask_required_without_ask_gate_declared(self, isolated_root):
        # 呼叫方未走 Ask 通道（permission_gate 不是 ask_required）→ 拒絕
        reg = _registry()
        client = FakeMCPClient([_tool("message_send", "")])
        _run(reg.register_mcp_server("server-p3", client))
        res = _run(reg.call("server-p3:message_send", {}, permission_gate=PERM_AUTO_APPROVED))
        assert res.error == "permission_denied"
        assert client.calls == []

    def test_ask_required_with_approving_gate_runs(self, isolated_root):
        gate = _ApprovingGate()
        reg = _registry(ask_gate=gate)
        client = FakeMCPClient([_tool("message_send", "")], call_data="sent")
        _run(reg.register_mcp_server("server-p4", client))
        res = _run(reg.call("server-p4:message_send", {"to": "bryan"},
                            permission_gate=PERM_ASK_REQUIRED))
        assert res.ok is True
        assert res.data == "sent"
        assert gate.asked == [("server-p4:message_send", {"to": "bryan"})]

    def test_ask_required_with_denying_gate_denies(self, isolated_root):
        reg = _registry(ask_gate=_DenyingGate())
        client = FakeMCPClient([_tool("telegram_send", "")])
        _run(reg.register_mcp_server("server-p5", client))
        res = _run(reg.call("server-p5:telegram_send", {}, permission_gate=PERM_ASK_REQUIRED))
        assert res.error == "permission_denied"
        assert client.calls == []


# ────────────────────────────────────────────────────────────
# §2.4 健康快照
# ────────────────────────────────────────────────────────────

class TestHealthSnapshot:
    def test_health_snapshot_states(self, isolated_root):
        reg = _registry()
        _run(reg.register_mcp_server("srv-a", FakeMCPClient([_tool("weather", "")])))
        assert reg.health_snapshot()["srv-a"] == HEALTH_HEALTHY
        reg.mark_offline("srv-a", "manual")
        assert reg.health_snapshot()["srv-a"] == HEALTH_OFFLINE
        reg.mark_healthy("srv-a", "manual")
        assert reg.health_snapshot()["srv-a"] == HEALTH_HEALTHY
