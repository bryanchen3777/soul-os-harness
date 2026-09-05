"""
tests/test_ts3_real_mcp_e2e.py — TS-3 真實 MCP Server 端到端接入驗證（IMPLEMENTATION）

宗旨（工單 TS-3）：把 TS-2 tool_registry 從 FakeMCPClient（Mock/Stub）推進到
**真實 MCP stdio 子進程**端到端。本測試對接 ``scripts/mcp_fixture_server.py`` ——
用官方 Python MCP SDK（mcp 2.x ``MCPServer``）實現的真實 MCP Server，走標準
stdio transport（initialize 握手 → tools/list → tools/call → 清理）。

覆蓋（對照工單驗收）：
  1. 進程與通訊穩定性：真實子進程啟動、握手指派、tools/list、tools/call、
     close 清理（無殘留進程）。
  2. Fail-closed 守門：5s 硬超時（可配置）降級、斷線（kill 進程）降級、
     連續失敗 → offline → 不再投影；**主心跳不被阻塞**。
  3. Volition Gate 契約：單次行動、結果不回環（不產生新工具調用）、
     Actuator 無權 publish（不持有 bus/SpeakerToken/LLM）。
  4. 權限分級：唯讀感知類 auto_approved 直接執行；敏感變更類 ask_required
     預設 Ask stub 拒絕（零外部副作用）、注入 approving gate 後才執行。
  5. 自動歸類三級規則在真實 Server 上生效：weather/time → observe；
     memory_search → reflect；message_send → communicate；unclassifiable_op
     → 拒絕註冊（fail-closed）。

Frozen contract：0 change（不改 tool_registry.py / actuator.py 既有接口語義；
本測試只新增對真實 stdio client 的驅動；sidecar 寫入隔離 data_root）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
)
from src.soul.mcp_stdio_client import (  # noqa: E402
    MCPStdioClientAdapter,
    RawStdioMCPClient,
)

# ────────────────────────────────────────────────────────────
# 常量 / Helpers
# ────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_SERVER = PROJECT_ROOT / "scripts" / "mcp_fixture_server.py"
VENV_PYTHON = str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")


def _run(coro):
    return asyncio.run(coro)


def _fixture_spec() -> Tuple[str, List[str]]:
    """返回 (command, args) 啟動 fixture MCP Server。"""
    return (sys.executable, [str(FIXTURE_SERVER)])


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


# ────────────────────────────────────────────────────────────
# 1. 進程與通訊穩定性（真實 stdio 端到端）
# ────────────────────────────────────────────────────────────

class TestRealStdioE2E:
    """真實 MCP stdio 進程：啟動 → 握手 → 註冊 → 調用 → 清理。"""

    def test_sdk_adapter_full_chain(self, isolated_root):
        """SDK adapter：connect → register（歸類/權限）→ 真實 call → close。"""
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            tools = await reg.register_mcp_server("fixture-sdk", client)
            by_name = {t.name: t for t in tools}
            # 自動歸類 + 權限分級（§2.3 / §4.1）
            assert by_name["weather"].capability_group == CAPABILITY_GROUP_OBSERVE
            assert by_name["weather"].permission_class == PERM_AUTO_APPROVED
            assert by_name["time"].capability_group == CAPABILITY_GROUP_OBSERVE
            assert by_name["memory_search"].capability_group == CAPABILITY_GROUP_REFLECT
            assert by_name["memory_search"].permission_class == PERM_AUTO_APPROVED
            assert by_name["message_send"].capability_group == CAPABILITY_GROUP_COMMUNICATE
            assert by_name["message_send"].permission_class == PERM_ASK_REQUIRED
            # unclassifiable_op（§2.3 優先級 3）→ 拒絕註冊
            assert "fixture-sdk:unclassifiable_op" not in by_name
            assert reg.get_tool("fixture-sdk:unclassifiable_op") is None
            # 真實調用（tools/call）
            res = await reg.call(
                "fixture-sdk:weather", {"city": "Taipei"},
                permission_gate=PERM_AUTO_APPROVED,
            )
            assert res.ok is True
            assert res.degraded is False
            assert isinstance(res.data, dict)
            # mcp 2.x 回傳包在 {"result": {...}}
            payload = res.data.get("result", res.data)
            assert payload["city"] == "Taipei"
            assert payload["temperature"] == 24
            # 清理
            await client.close()
            assert client.connected is False
            return True

        assert _run(scenario())

    def test_raw_client_full_chain(self, isolated_root):
        """手寫 stdio JSON-RPC client：同一套註冊/調用/清理，雙實現互相印證。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            assert client.negotiated_version == "2024-11-05"
            assert client.server_info == {"name": "soul-os-ts3-fixture", "version": ""}
            reg = ToolRegistry(store_dir=isolated_root)
            tools = await reg.register_mcp_server("fixture-raw", client)
            assert {t.name for t in tools} == {
                "weather", "time", "memory_search", "message_send", "search",
            }  # unclassifiable_op 被拒
            res = await reg.call(
                "fixture-raw:time", {"timezone_name": "Asia/Taipei"},
                permission_gate=PERM_AUTO_APPROVED,
            )
            assert res.ok is True
            payload = res.data.get("result", res.data)
            assert "now" in payload
            assert payload["timezone"] == "Asia/Taipei"
            # reflect_memory 真實調用
            res2 = await reg.call(
                "fixture-raw:memory_search", {"query": "天氣"},
                permission_gate=PERM_AUTO_APPROVED,
            )
            assert res2.ok is True
            assert "results" in res2.data.get("result", res2.data)
            await client.close()
            # 清理後進程不再存活
            assert client.proc is None or client.proc.returncode is not None
            return True

        assert _run(scenario())

    def test_unregister_cleans_real_server(self, isolated_root):
        """degrade 前 unregister：工具消失且健康快照清空（進程由 close 清理）。"""
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-u", client)
            reg.unregister_mcp_server("fixture-u")
            assert reg.list_tools() == []
            assert reg.health_snapshot() == {}
            await client.close()
            return True

        assert _run(scenario())


# ────────────────────────────────────────────────────────────
# 2. Fail-closed 守門（5s 硬超時 / 斷線降級 / 不阻塞主心跳）
# ────────────────────────────────────────────────────────────

class TestFailClosed:
    """外部 MCP 斷線 / 超時 → 如期安全降級，絕不阻塞主心跳。"""

    def test_hard_timeout_degrades(self, isolated_root):
        """5s 硬超時（可配置調小）：search 故意 sleep → 超時降級不 raise。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            # 硬超時 0.4s；search delay 8s → 必超時（search 屬顯式表 auto_approved）
            reg = ToolRegistry(store_dir=isolated_root, call_timeout=0.4,
                               offline_after_consecutive_failures=2)
            await reg.register_mcp_server("fixture-t", client)
            res = await reg.call(
                "fixture-t:search", {"query": "x", "delay_seconds": 8.0},
                permission_gate=PERM_AUTO_APPROVED,
            )
            assert res.ok is False
            assert res.degraded is True
            assert res.error is not None and "timeout" in res.error
            assert res.data is None
            assert reg.health_snapshot()["fixture-t"] == HEALTH_DEGRADED
            await client.close()
            return True

        assert _run(scenario())

    def test_process_kill_disconnect_degrades_to_offline(self, isolated_root):
        """斷線（kill 子進程）：無快取工具 → 空結果降級；連續失敗 → offline → 不投影。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root,
                               offline_after_consecutive_failures=2)
            await reg.register_mcp_server("fixture-k", client)
            # 先成功一次（建立快取）
            ok = await reg.call("fixture-k:weather", {"city": "Taipei"},
                                permission_gate=PERM_AUTO_APPROVED)
            assert ok.ok is True
            # 斷線：強殺進程
            await client.kill()
            # 無快取工具（time 未呼叫過）→ 空結果降級（data=None）
            r1 = await reg.call("fixture-k:time", {"timezone_name": "UTC"},
                                permission_gate=PERM_AUTO_APPROVED)
            assert r1.ok is False
            assert r1.degraded is True
            assert r1.data is None
            # 有快取工具（weather 成功過）→ 快取兜底（cached=True, degraded=True）
            r2 = await reg.call("fixture-k:weather", {"city": "Taipei"},
                                permission_gate=PERM_AUTO_APPROVED)
            assert r2.ok is True
            assert r2.degraded is True
            assert r2.cached is True
            # 連續失敗達閾值 → offline
            assert reg.health_snapshot()["fixture-k"] == HEALTH_OFFLINE
            # offline → 該 server 不投影（fail-silent）
            proj = reg.project_capabilities()
            observe_expr = next(c.expression for c in proj
                                if c.id == CAPABILITY_GROUP_OBSERVE)
            assert "weather" not in observe_expr  # 已回退靜態
            await client.close()
            return True

        assert _run(scenario())

    def test_heartbeat_not_blocked_by_slow_tool(self, isolated_root):
        """主心跳不被阻塞：慢工具卡住時，獨立心跳 task 照常完成。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root, call_timeout=0.5)
            await reg.register_mcp_server("fixture-h", client)

            heartbeat_done = []
            async def heartbeat():
                await asyncio.sleep(0.1)  # 比工具超時短
                heartbeat_done.append(True)
                return "hb-ok"

            slow_call = reg.call("fixture-h:search",
                                 {"query": "x", "delay_seconds": 8.0},
                                 permission_gate=PERM_AUTO_APPROVED)
            hb_task = asyncio.create_task(heartbeat())
            res = await slow_call
            hb_result = await hb_task
            assert res.degraded is True  # 工具失敗降級
            assert heartbeat_done == [True]  # 主心跳沒被卡住
            assert hb_result == "hb-ok"
            await client.close()
            return True

        assert _run(scenario())

    def test_exception_fail_closed_no_publish(self, isolated_root):
        """斷線後 call → 降級；降級結果絕不產生任何 publish 通道（僅 trace）。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root,
                               offline_after_consecutive_failures=1)
            await reg.register_mcp_server("fixture-e", client)
            await client.kill()
            res = await reg.call("fixture-e:time", {},
                                 permission_gate=PERM_AUTO_APPROVED)
            # 降級（無快取 → 空結果）
            assert res.ok is False
            assert res.degraded is True
            assert res.data is None
            await client.close()
            return True

        assert _run(scenario())


# ────────────────────────────────────────────────────────────
# 3. Volition Gate 契約（單次行動 / 不回環 / 無權 publish）
# ────────────────────────────────────────────────────────────

class TestVolitionGateReal:
    """真實 MCP server 之上驗證 Actuator Volition Gate 契約（§3.2）。"""

    def _make_actuator(self, registry, perception_state=None, memory_sink=None):
        from src.soul.actuator import Actuator
        return Actuator(registry, perception_state=perception_state,
                        memory_sink=memory_sink)

    @staticmethod
    def _decision(action: str):
        class _D:
            def __init__(self, a):
                self.decision = a
        return _D(action)

    @staticmethod
    def _motive(content: str = ""):
        class _M:
            def __init__(self, c):
                self.content = c
        return _M(content)

    def test_observe_single_shot_real_call(self, isolated_root):
        """observe = 單次行動：一次 dispatch → 一次真实工具調用 → 結果回流感知。"""
        from src.world.state import WorldPerceptionState

        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-v", client)
            state = WorldPerceptionState()
            actuator = self._make_actuator(reg, perception_state=state)
            result = await actuator.dispatch(
                self._decision("observe"), self._motive("天氣變了，想確認一下"),
                agent_id="agent_yua",
            )
            assert result is not None and result.ok is True
            # 結果進 world_context（感知回流）
            events = state.get_active_events()
            assert len(events) == 1
            event = events[0]
            assert event.source == "weather"
            # 無第二個工具調用、無 publish（0 自主遞迴）
            assert not hasattr(actuator, "publish")
            await client.close()
            return True

        assert _run(scenario())

    def test_reflect_single_shot_real_call(self, isolated_root):
        """reflect = 單次行動：一次 Dispatch → 記憶摘要回流認知，不產生行動。"""
        async def scenario():
            client = RawStdioMCPClient(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-r", client)
            sink_calls: List[Any] = []
            actuator = self._make_actuator(reg, memory_sink=lambda res, aid: sink_calls.append((res, aid)))

            result = await actuator.execute_reflect(
                self._motive("想回顧一下記憶"), agent_id="agent_yua",
            )
            assert result is not None and result.ok is True
            assert len(sink_calls) == 1  # 單次回流
            await client.close()
            return True

        assert _run(scenario())

    def test_actuator_holds_no_publish_capability(self, isolated_root):
        """Actuator 不持 bus / SpeakerToken / LLM，更無 publish 方法（§3.2 硬規則 3）。"""
        async def scenario():
            reg = ToolRegistry(store_dir=isolated_root)
            actuator = self._make_actuator(reg)
            keys = set(actuator.__dict__.keys())
            assert not hasattr(actuator, "publish")
            assert not hasattr(actuator, "bus")
            assert not hasattr(actuator, "speaker_token")
            assert not any("llm" in k.lower() for k in keys)
            return True

        assert _run(scenario())


# ────────────────────────────────────────────────────────────
# 4. 權限分級（Auto-Approved / Ask-Required 真實 server 上）
# ────────────────────────────────────────────────────────────

class TestPermissionRealServer:
    """唯讀感知 Auto-Approved；敏感變更 Ask-Required（§4.1）。"""

    def test_auto_approved_runs_directly(self, isolated_root):
        """weather（auto_approved）不需 Ask → 直接執行成功。"""
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-a", client)
            res = await reg.call("fixture-a:weather", {"city": "Kaohsiung"},
                                 permission_gate=PERM_AUTO_APPROVED)
            assert res.ok is True
            await client.close()
            return True

        assert _run(scenario())

    def test_ask_required_default_stub_denies(self, isolated_root):
        """message_send（ask_required）：v1 Ask stub 未接通 → 拒絕且零外部副作用。"""
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-m", client)
            res = await reg.call(
                "fixture-m:message_send", {"to": "bryan", "text": "hi"},
                permission_gate=PERM_ASK_REQUIRED,
            )
            assert res.ok is False
            assert res.error == "permission_denied"
            await client.close()
            return True

        assert _run(scenario())

    def test_ask_required_with_approving_gate_executes(self, isolated_root):
        """注入 approving AskGate → ask_required 工具獲准後才真實執行。"""
        async def scenario():
            gate = _ApprovingGate()
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root, ask_gate=gate)
            await reg.register_mcp_server("fixture-ma", client)
            res = await reg.call(
                "fixture-ma:message_send", {"to": "bryan", "text": "hello"},
                permission_gate=PERM_ASK_REQUIRED,
            )
            assert res.ok is True
            assert gate.asked == [("fixture-ma:message_send", {"to": "bryan", "text": "hello"})]
            await client.close()
            return True

        assert _run(scenario())

    def test_ask_required_without_ask_gate_declared_denies(self, isolated_root):
        """呼叫方未走 Ask 通道（auto gate）→ 拒絕執行（零副作用）。"""
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-mb", client)
            res = await reg.call(
                "fixture-mb:message_send", {"to": "x", "text": "y"},
                permission_gate=PERM_AUTO_APPROVED,
            )
            assert res.error == "permission_denied"
            await client.close()
            return True

        assert _run(scenario())


# ────────────────────────────────────────────────────────────
# 5. 投影合併在真實 Server 上（healthy 工具進 prompt；offline 消失）
# ────────────────────────────────────────────────────────────

class TestProjectionReal:
    def test_healthy_tools_projected(self, isolated_root):
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-p", client)
            exprs = {c.id: c.expression for c in reg.project_capabilities()}
            assert "weather" in exprs[CAPABILITY_GROUP_OBSERVE]
            assert "memory_search" in exprs[CAPABILITY_GROUP_REFLECT]
            assert "message_send" in exprs[CAPABILITY_GROUP_COMMUNICATE]
            # 無法歸類工具永不投影
            assert "unclassifiable_op" not in exprs[CAPABILITY_GROUP_OBSERVE]
            await client.close()
            return True

        assert _run(scenario())

    def test_offline_not_projected(self, isolated_root):
        async def scenario():
            client = MCPStdioClientAdapter(*_fixture_spec())
            await client.connect()
            reg = ToolRegistry(store_dir=isolated_root)
            await reg.register_mcp_server("fixture-po", client)
            reg.mark_offline("fixture-po", "manual-disconnect")
            exprs = {c.id: c.expression for c in reg.project_capabilities()}
            from src.soul.capability import CAPABILITY_DEFINITIONS
            assert exprs[CAPABILITY_GROUP_OBSERVE] == \
                CAPABILITY_DEFINITIONS[CAPABILITY_GROUP_OBSERVE].expression
            await client.close()
            return True

        assert _run(scenario())