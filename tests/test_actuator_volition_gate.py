"""
tests/test_actuator_volition_gate.py — TS-2 Tooling Volition Gate：observe/reflect 執行器測試

覆蓋（工單 TS-2 驗收 + 設計 §3）：
  - observe → observe_environment 組工具調用，結果回流 world_context 感知
    （WorldPerceptionState 收到 WorldEvent，經既有感知路徑進入認知）
  - reflect → reflect_memory 組工具調用，結果回流記憶摘要（memory_sink）
  - transmit → 不在此派發（走既有 Expression 路徑）
  - do_nothing → 不執行（合法主動選擇，工具零呼叫）
  - 0 自主遞迴（§3.2）：單次 dispatch = 單次工具調用；Actuator 不持有
    EventBus / SpeakerToken / LLM（無權 publish AGENT_SPEAK / AGENT_INTENT /
    AGENCY_TRIGGER）
  - 組內無可用工具 / 調用失敗 → Fail-closed 降級（不 crash、不注入髒感知）
  - Motive 內容路由（執行器層展開工具明細，§3.1）
  - ask_required 工具 → permission_denied（等同 do_nothing）

Frozen contract：0 change（不 import / 不改 Agency / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE / EventBus）。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul.actuator import Actuator  # noqa: E402
from src.soul.decision import DecisionResult  # noqa: E402
from src.soul.motive import Motive  # noqa: E402
from src.soul.tool_registry import (  # noqa: E402
    PERM_ASK_REQUIRED,
    ToolRegistry,
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
    def __init__(self, tools: List[Dict[str, Any]], *, call_data: Any = None,
                 call_behavior: str = "return"):
        self._tools = tools
        self.call_data = call_data
        self.call_behavior = call_behavior  # "return" | "raise"
        self.calls: List[tuple] = []

    async def list_tools(self) -> Any:
        return {"tools": list(self._tools)}

    async def call_tool(self, name: str, arguments: dict) -> Any:
        self.calls.append((name, arguments))
        if self.call_behavior == "raise":
            raise RuntimeError(self.call_data or "boom")
        return self.call_data


def _tool(name: str, description: str = "") -> Dict[str, Any]:
    return {"name": name, "description": description,
            "inputSchema": {"type": "object", "properties": {}}}


def _decision(action: str) -> DecisionResult:
    return DecisionResult(
        decision=action, transmit=(action == "transmit"),
        reason=f"test:{action}", motive_id="m1",
        motive_content="outside rain, want to check weather",
        provenance_ref="evt1",
    )


def _motive(content: str) -> Motive:
    return Motive(
        motive_id="m1", content=content, target="bryan",
        provenance_ref="evt1", created_at="2026-09-04T00:00:00+00:00",
    )


class _MemorySink:
    def __init__(self):
        self.received: List[tuple] = []

    def __call__(self, result, agent_id: str) -> None:
        self.received.append((result, agent_id))


# ────────────────────────────────────────────────────────────
# observe：派發 + 結果回流 world_context 感知
# ────────────────────────────────────────────────────────────

class TestObserve:
    def test_observe_dispatches_observe_group_tool(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "天氣查詢")], call_data={"temp": 21})
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("observe"), _motive("天气变了想确认")))

        assert result is not None and result.ok is True
        assert client.calls == [("weather", {})]  # observe_environment 組被調用

    def test_observe_flows_into_world_perception_state(self, isolated_root):
        from src.world.state import WorldPerceptionState
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "天氣查詢")],
                               call_data={"temp": 21, "cond": "sunny"})
        _run(reg.register_mcp_server("srv", client))
        state = WorldPerceptionState()
        actuator = Actuator(reg, perception_state=state)

        result = _run(actuator.dispatch(_decision("observe"), _motive("天气")))

        assert result.ok is True
        events = state.get_active_events()
        assert len(events) == 1
        ev = events[0]
        assert ev.source == "weather"          # VALID_SOURCES 白名單映射
        assert ev.type == "tool_weather"
        assert ev.data.get("temp") == 21
        assert ev.data.get("cached") is False  # 非快取 → 無 staleness 標註

    def test_observe_cached_result_marks_stale(self, isolated_root):
        from src.world.state import WorldPerceptionState
        reg = ToolRegistry(call_timeout=0.05, offline_after_consecutive_failures=10)
        # 先成功寫入快取
        _run(reg.register_mcp_server("srv", FakeMCPClient(
            [_tool("weather", "")], call_data={"temp": 21})))
        _run(reg.call("srv:weather", {}, permission_gate="auto_approved"))
        # 換成故障 client（快取仍在）→ 走降級路徑帶 stale 標註
        reg._servers["srv"]["client"] = FakeMCPClient(
            [_tool("weather", "")], call_behavior="raise", call_data=RuntimeError("x"))
        state = WorldPerceptionState()
        actuator = Actuator(reg, perception_state=state)
        _run(actuator.dispatch(_decision("observe"), _motive("天气")))
        events = state.get_active_events()
        assert len(events) == 1
        ev = events[0]
        assert ev.data.get("cached") is True
        assert "[快取" in ev.summary  # staleness 標註

    def test_observe_failure_does_not_pollute_perception(self, isolated_root):
        from src.world.state import WorldPerceptionState
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "")], call_behavior="raise",
                               call_data=RuntimeError("mcp down"))
        _run(reg.register_mcp_server("srv", client))
        state = WorldPerceptionState()
        actuator = Actuator(reg, perception_state=state)

        result = _run(actuator.dispatch(_decision("observe"), _motive("天气")))

        assert result.ok is False
        assert result.degraded is True
        assert state.get_active_events() == []  # 失敗 → 不注入（感知缺失靜默，§4.2）


# ────────────────────────────────────────────────────────────
# reflect：派發 + 結果回流記憶摘要
# ────────────────────────────────────────────────────────────

class TestReflect:
    def test_reflect_dispatches_reflect_group_tool(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("memory_search", "查詢記憶")], call_data={"summary": "昨日片段"})
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("reflect"), _motive("想回顾记忆")))

        assert result is not None and result.ok is True
        assert client.calls == [("memory_search", {"query": "想回顾记忆"})]  # query 帶 motive

    def test_reflect_flows_to_memory_sink(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("memory_search", "查詢記憶")], call_data={"summary": "昨日片段"})
        _run(reg.register_mcp_server("srv", client))
        sink = _MemorySink()
        actuator = Actuator(reg, memory_sink=sink)

        result = _run(actuator.dispatch(_decision("reflect"), _motive("回忆"), agent_id="agent_yua"))

        assert result.ok is True
        assert len(sink.received) == 1
        got_result, got_agent = sink.received[0]
        assert got_result.data == {"summary": "昨日片段"}
        assert got_agent == "agent_yua"

    def test_reflect_motive_content_query(self, isolated_root):
        # route 對 reflect 組：無關鍵詞命中 → 組內第一個工具
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("diary_read", "讀日記")], call_data="昨天寫了日記")
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("reflect"), _motive("夜深了想翻回忆")))

        assert result.ok is True
        # 關鍵詞「回忆」命中 → memory_search；沒有 memory_search → 換組內第一個 diary_read
        assert client.calls[0][0] == "diary_read"
        assert client.calls[0][1] == {"query": "夜深了想翻回忆"}
        # world perception 不受 reflect 影響（感知路徑隔離）
        assert actuator._perception_state is None


# ────────────────────────────────────────────────────────────
# transmit / do_nothing：不在此派發
# ────────────────────────────────────────────────────────────

class TestNoDispatch:
    def test_do_nothing_returns_none_no_tool_call(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", ""), _tool("message_send", "")])
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("do_nothing"), _motive("安静")))

        assert result is None
        assert client.calls == []  # 零工具呼叫

    def test_transmit_not_dispatched_here(self, isolated_root):
        # transmit 走既有 Expression 路徑，Actuator 不調 communicate 組（§3.1）
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("message_send", "")])
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("transmit"), _motive("重要的事想告诉 Bry")))

        assert result is None
        assert client.calls == []  # communicate 組不被 Actuator 觸碰


# ────────────────────────────────────────────────────────────
# 0 自主遞迴（§3.2 硬規則）
# ────────────────────────────────────────────────────────────

class TestNoRecursion:
    def test_single_dispatch_single_call(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "")], call_data={"temp": 21})
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        for _ in range(3):
            _run(actuator.dispatch(_decision("observe"), _motive("天气")))

        # 3 次 Decision = 3 次工具調用，無自激（結果不觸發新工具）
        assert len(client.calls) == 3

    def test_flowback_does_not_trigger_tools(self, isolated_root):
        from src.world.state import WorldPerceptionState
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "")], call_data={"temp": 21})
        _run(reg.register_mcp_server("srv", client))
        sink = _MemorySink()
        state = WorldPerceptionState()
        actuator = Actuator(reg, perception_state=state, memory_sink=sink)

        _run(actuator.dispatch(_decision("observe"), _motive("天气"), agent_id="a1"))
        # 回流後：感知 state 有 1 條、memory sink 空、工具僅 1 次
        assert len(state.get_active_events()) == 1
        assert sink.received == []
        assert len(client.calls) == 1

    def test_actuator_holds_no_publish_capability(self, isolated_root):
        # §3.2 硬規則 3：不持有 EventBus / SpeakerToken / LLM / bus 引用
        reg = ToolRegistry()
        actuator = Actuator(reg)
        keys = set(actuator.__dict__.keys())
        assert not any(k for k in keys if "bus" in k.lower() or "speaker" in k.lower())
        assert not hasattr(actuator, "publish")
        assert set(keys) <= {"_registry", "_perception_state", "_memory_sink"}


# ────────────────────────────────────────────────────────────
# Fail-closed 降級 / 權限
# ────────────────────────────────────────────────────────────

class TestFailClosed:
    def test_no_group_tools_degrades_without_crash(self, isolated_root):
        reg = ToolRegistry()  # 空註冊表
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("observe"), _motive("天气")))

        assert result is not None
        assert result.ok is False
        assert result.error == "no_tool_for_group"
        assert result.degraded is True

    def test_ask_required_tool_permission_denied(self, isolated_root):
        # v1 預設 Ask stub 拒絕 → 等同 do_nothing（工具零呼叫），fail-closed
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("message_send", "")], call_data="sent")
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        # communicate 不走 Actuator；此測試直接經 registry 驗證權限閘已生效：
        res = _run(reg.call("srv:message_send", {}, permission_gate=PERM_ASK_REQUIRED))
        assert res.error == "permission_denied"
        assert client.calls == []

    def test_motive_routing_picks_explicit_tool(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient(
            [_tool("time", "時間"), _tool("weather", "天氣"), _tool("news", "新聞")],
            call_data={"temp": 20},
        )
        _run(reg.register_mcp_server("srv", client))
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("observe"), _motive("外面下雨了，想确认天气")))

        assert result.ok is True
        assert client.calls[0][0] == "weather"  # motive 含「天气」→ weather 工具

    def test_offline_group_routes_to_none(self, isolated_root):
        reg = ToolRegistry()
        _run(reg.register_mcp_server("srv", FakeMCPClient([_tool("weather", "")])))
        reg.mark_offline("srv", "disconnected")
        actuator = Actuator(reg)

        result = _run(actuator.dispatch(_decision("observe"), _motive("天气")))

        assert result is not None
        assert result.error == "no_tool_for_group"  # offline 工具不路由（§2.4）
        assert result.degraded is True
