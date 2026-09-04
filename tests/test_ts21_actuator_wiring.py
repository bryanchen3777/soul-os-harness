"""
tests/test_ts21_actuator_wiring.py — TS-2.1 Actuator 接线：scheduler._decision_check 依赖注入

验收锚点（TS-2.1 工单）:
  - observe 决策 → Actuator 单次执行（真实工具调用）→ 结果回流感知（world_context）
    → 发布端仍 mark_rejected（不 publish AGENCY_TRIGGER / AGENT_SPEAK）
  - reflect 决策 → Actuator 单次执行 → 结果回流认知（memory_sink）→ 仍 mark_rejected
  - transmit → 既有 publish 通道（0 改动, payload 逐字段不变, Actuator 不被触碰）
  - do_nothing → 不执行（合法主动选择, 工具零呼叫）
  - 未注入 Actuator（默认 None）→ 行为与现状完全等价（空转决策, 向后兼容）
  - Actuator 执行异常 → fail-closed：motive 照常 rejected, 不 crash
  - 0 自主递归：单次 Decision = 单次 Actuator 调用 = 单次工具调用 + 一次回流

Frozen contract：0 change（不 import / 不改 Agency / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE；只验证 scheduler 的 additive 接线）。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root  # noqa: E402
from src.soul.actuator import Actuator  # noqa: E402
from src.soul.motive import (  # noqa: E402
    MOTIVE_STATUS_REJECTED,
    MOTIVE_STATUS_TRANSMITTED,
    MotiveTraceStore,
    new_motive_id,
    now_utc_iso,
)
from src.soul.scheduler import SoulScheduler  # noqa: E402
from src.soul.tool_registry import ToolRegistry  # noqa: E402


# ────────────────────────────────────────────────────────────
# Helpers（与 test_sm3_motive_decision / test_actuator_volition_gate 同款）
# ────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


def _seed_motive_trace(
    data_dir: Path,
    agent_id: str,
    content: str = "我想告诉你今天的事",
    provenance_ref: str = "evt_abc123",
) -> str:
    """直接写一条 pending motive trace 记录（跳过 interpretation 步骤）。"""
    trace_path = data_dir / "soul" / "motive_trace.jsonl"
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    mid = new_motive_id()
    record = {
        "motive_id": mid,
        "agent_id": agent_id,
        "status": "pending",
        "content": content,
        "target": "bryan",
        "provenance_ref": provenance_ref,
        "created_at": now_utc_iso(),
        "updated_at": now_utc_iso(),
    }
    with open(trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return mid


class FakeProxy:
    """Mock LLMProxy：generate_text 返回预设响应序列（Decision LLM 用）。"""

    def __init__(self, responses: List[Optional[str]]):
        self.responses = list(responses)
        self.calls: List[Dict[str, Any]] = []

    async def generate_text(
        self,
        messages,
        agent_id: str = "system",
        max_tokens: int = 200,
        temperature: float = 0.7,
    ) -> Optional[str]:
        self.calls.append({
            "messages": messages,
            "agent_id": agent_id,
            "max_tokens": max_tokens,
            "temperature": temperature,
        })
        if self.responses:
            return self.responses.pop(0)
        return None


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


class _MemorySink:
    def __init__(self):
        self.received: List[tuple] = []

    def __call__(self, result, agent_id: str) -> None:
        self.received.append((result, agent_id))


def _make_actuator(*, tools: List[Dict[str, Any]], call_data: Any,
                   client_behavior: str = "return"):
    """构造注入 scheduler 的 Actuator + 共用 registry / perception / sink。"""
    reg = ToolRegistry()
    client = FakeMCPClient(tools, call_data=call_data, call_behavior=client_behavior)
    _run(reg.register_mcp_server("srv", client))
    from src.world.state import WorldPerceptionState
    state = WorldPerceptionState()
    sink = _MemorySink()
    actuator = Actuator(reg, perception_state=state, memory_sink=sink)
    return actuator, client, state, sink


async def _publish(
    scheduler: SoulScheduler, agent_id: str = "agent_yua", trigger_type: str = "proactive_dm",
):
    from src.eventbus.bus import SoulEventBus
    from src.eventbus.schema import EventType
    bus = SoulEventBus()
    await bus.start()
    try:
        captured: List[Any] = []
        async def _capture(e):
            captured.append(e)
        bus.subscribe(
            subscriber_id="capture",
            handler=_capture,
            event_filter={EventType.AGENCY_TRIGGER},
        )
        # 同 test_sm3_motive_decision 的 SoulScheduler(bus=bus) 模式：
        # 把捕获 bus 挂到 scheduler, 让 proactive_dm 真正走 _decision_check + 发布路径
        scheduler._bus = bus
        await scheduler._publish_agency_trigger(agent_id=agent_id, trigger_type=trigger_type)
        return captured
    finally:
        await bus.stop()


# ────────────────────────────────────────────────────────────
# Actuator 新入口（execute_observe / execute_reflect）单测
# ────────────────────────────────────────────────────────────

class TestExecuteMethods:
    """execute_observe / execute_reflect：与 dispatch 同语义的单次执行入口。"""

    def test_execute_observe_dispatches_and_flows(self, isolated_root):
        from src.world.state import WorldPerceptionState
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("weather", "天氣")], call_data={"temp": 21})
        _run(reg.register_mcp_server("srv", client))
        state = WorldPerceptionState()
        actuator = Actuator(reg, perception_state=state)

        from src.soul.motive import Motive
        motive = Motive(
            motive_id="m1", content="外面下雨了想确认天气", target="bryan",
            provenance_ref="evt1", created_at=now_utc_iso(),
        )
        result = _run(actuator.execute_observe(motive, agent_id="agent_yua"))

        assert result is not None and result.ok is True
        assert client.calls == [("weather", {})]
        events = state.get_active_events()
        assert len(events) == 1
        assert events[0].source == "weather"

    def test_execute_reflect_dispatches_and_flows(self, isolated_root):
        reg = ToolRegistry()
        client = FakeMCPClient([_tool("memory_search", "查詢記憶")], call_data={"summary": "昨日片段"})
        _run(reg.register_mcp_server("srv", client))
        sink = _MemorySink()
        actuator = Actuator(reg, memory_sink=sink)

        from src.soul.motive import Motive
        motive = Motive(
            motive_id="m1", content="夜深了想翻回忆", target="bryan",
            provenance_ref="evt1", created_at=now_utc_iso(),
        )
        result = _run(actuator.execute_reflect(motive, agent_id="agent_yua"))

        assert result is not None and result.ok is True
        assert client.calls == [("memory_search", {"query": "夜深了想翻回忆"})]
        assert len(sink.received) == 1
        assert sink.received[0][0].data == {"summary": "昨日片段"}
        assert sink.received[0][1] == "agent_yua"


# ────────────────────────────────────────────────────────────
# observe：scheduler → Actuator → 工具调用 → 回流感知 → 仍 mark_rejected
# ────────────────────────────────────────────────────────────

class TestObserveWiring:
    def test_observe_decision_executes_tool_and_flows_to_perception(
        self, isolated_root, monkeypatch,
    ):
        data_dir = isolated_root
        mid = _seed_motive_trace(
            data_dir, "agent_yua", content="外面下雨了，想确认天气",
        )
        fake = FakeProxy(['{"decision": "observe", "reason": "先确认天气再决定"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        actuator, client, state, sink = _make_actuator(
            tools=[_tool("weather", "天氣")], call_data={"temp": 21, "cond": "rain"},
        )

        captured = _run(_publish(SoulScheduler(actuator=actuator)))

        # 真实工具调用发生（空转闭环兑现）→ 结果回流感知
        assert client.calls == [("weather", {})]
        events = state.get_active_events()
        assert len(events) == 1
        assert events[0].source == "weather"
        assert events[0].data.get("temp") == 21
        # 发布端仍不发
        assert len(captured) == 0
        # motive 仍 rejected（终态, 不重试）; memory_sink 不受 observe 影响
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED
        assert sink.received == []


# ────────────────────────────────────────────────────────────
# reflect：scheduler → Actuator → 工具调用 → 回流认知 → 仍 mark_rejected
# ────────────────────────────────────────────────────────────

class TestReflectWiring:
    def test_reflect_decision_executes_tool_and_flows_to_memory_sink(
        self, isolated_root, monkeypatch,
    ):
        data_dir = isolated_root
        mid = _seed_motive_trace(
            data_dir, "agent_yua", content="夜深了，想翻翻以前的回忆",
        )
        fake = FakeProxy(['{"decision": "reflect", "reason": "此刻想回顾记忆"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        actuator, client, state, sink = _make_actuator(
            tools=[_tool("memory_search", "查詢記憶")], call_data={"summary": "昨日片段"},
        )

        captured = _run(_publish(SoulScheduler(actuator=actuator)))

        # 真实工具调用 + 回流记忆摘要（agent_id 透传）
        assert client.calls == [("memory_search", {"query": "夜深了，想翻翻以前的回忆"})]
        assert len(sink.received) == 1
        assert sink.received[0][0].data == {"summary": "昨日片段"}
        assert sink.received[0][1] == "agent_yua"
        # 感知不受 reflect 影响; 发布端不发; motive 仍 rejected
        assert state.get_active_events() == []
        assert len(captured) == 0
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# transmit：既有 publish 通道（0 改动）; Actuator 不被触碰
# ────────────────────────────────────────────────────────────

class TestTransmitWiring:
    def test_transmit_keeps_publish_channel_actuator_untouched(
        self, isolated_root, monkeypatch,
    ):
        data_dir = isolated_root
        mid = _seed_motive_trace(data_dir, "agent_yua")
        fake = FakeProxy(['{"decision": "transmit", "reason": "这个念头值得此刻说"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        actuator, client, state, sink = _make_actuator(
            tools=[_tool("weather", "天氣"), _tool("memory_search", "查詢記憶")],
            call_data={"temp": 21},
        )

        captured = _run(_publish(SoulScheduler(actuator=actuator)))

        # 既有发布通道：AGENCY_TRIGGER 发布, payload 逐字段不变（M5.2-G frozen schema）
        assert len(captured) == 1
        payload = captured[0].payload
        assert set(payload.keys()) == {
            "trigger_type", "agent_id", "reason", "elapsed_mins", "timestamp", "extra",
        }
        assert payload["trigger_type"] == "proactive_dm"
        assert payload["agent_id"] == "agent_yua"
        assert payload["reason"] == "scheduler.proactive_dm"
        assert isinstance(payload["elapsed_mins"], float)
        assert isinstance(payload["timestamp"], str)
        assert payload["extra"] == {}
        # Actuator 完全不被触碰（transmit 走既有 Expression 路径, 0 工具调用）
        assert client.calls == []
        assert state.get_active_events() == []
        assert sink.received == []
        # motive 标记 transmitted
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_TRANSMITTED


# ────────────────────────────────────────────────────────────
# do_nothing：合法不执行
# ────────────────────────────────────────────────────────────

class TestDoNothingWiring:
    def test_do_nothing_no_execution_no_publish(self, isolated_root, monkeypatch):
        data_dir = isolated_root
        mid = _seed_motive_trace(data_dir, "agent_yua", content="只是平常小事")
        fake = FakeProxy(['{"decision": "do_nothing", "reason": "此刻想安静度日"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        actuator, client, state, sink = _make_actuator(
            tools=[_tool("weather", "天氣")], call_data={"temp": 21},
        )

        captured = _run(_publish(SoulScheduler(actuator=actuator)))

        assert client.calls == []          # 工具零呼叫
        assert state.get_active_events() == []
        assert sink.received == []
        assert len(captured) == 0          # 不发
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# 向后兼容：未注入 Actuator → 与现状完全等价（空转决策）
# ────────────────────────────────────────────────────────────

class TestNoInjection:
    def test_no_actuator_observe_still_no_publish_and_rejected(
        self, isolated_root, monkeypatch,
    ):
        data_dir = isolated_root
        mid = _seed_motive_trace(data_dir, "agent_yua", content="外面下雨了")
        fake = FakeProxy(['{"decision": "observe", "reason": "先看看"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        # 不注入 actuator（默认 None，与既有 36 条 SM-3 测试同构）
        scheduler = SoulScheduler()
        captured = _run(_publish(scheduler))

        assert len(captured) == 0
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED

    def test_no_actuator_reflect_still_no_publish_and_rejected(
        self, isolated_root, monkeypatch,
    ):
        data_dir = isolated_root
        mid = _seed_motive_trace(data_dir, "agent_yua", content="夜深了想回忆")
        fake = FakeProxy(['{"decision": "reflect", "reason": "先回顾"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        scheduler = SoulScheduler()
        captured = _run(_publish(scheduler))

        assert len(captured) == 0
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# fail-closed：Actuator 执行异常 → motive 照常 rejected, 不 crash
# ────────────────────────────────────────────────────────────

class TestFailClosedWiring:
    def test_actuator_exception_motive_still_rejected(self, isolated_root, monkeypatch):
        data_dir = isolated_root
        mid = _seed_motive_trace(data_dir, "agent_yua", content="外面下雨了")
        fake = FakeProxy(['{"decision": "observe", "reason": "先看看"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        class BoomActuator:
            async def execute_observe(self, motive, agent_id=""):
                raise RuntimeError("actuator boom")

        scheduler = SoulScheduler(actuator=BoomActuator())
        captured = _run(_publish(scheduler))

        # fail-closed：不发 + motive 生命周期照常收敛（不悬挂 pending）
        assert len(captured) == 0
        store = MotiveTraceStore(trace_path=data_dir / "soul" / "motive_trace.jsonl")
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# 0 自主递归：单次 Decision = 单次工具调用 = 单次回流
# ────────────────────────────────────────────────────────────

class TestNoRecursionWiring:
    def test_single_decision_single_tool_call(self, isolated_root, monkeypatch):
        data_dir = isolated_root
        _seed_motive_trace(data_dir, "agent_yua", content="外面下雨了")
        fake = FakeProxy(['{"decision": "observe", "reason": "先看看"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        actuator, client, state, sink = _make_actuator(
            tools=[_tool("weather", "天氣")], call_data={"temp": 21},
        )

        _run(_publish(SoulScheduler(actuator=actuator)))

        # 一次 Decision → 一次工具调用（无自激）；回流后无新工具
        assert len(client.calls) == 1
        assert len(state.get_active_events()) == 1
