"""
tests/test_sm3_motive_decision.py — SM-3/SM-4 Soul Motive & Decision Implementation

SM-3 (2026-08-30, IMPLEMENTATION): volition path 的 Motive + Decision 环。
SM-4 (2026-08-31, IMPLEMENTATION): Decision 四元行动 (transmit/observe/reflect/do_nothing)。

验收锚点 (DECISION-PROMPT-CONTRACT §7, 冻结):
  A. 有 trigger、无 motive → 不发
  B. 有 motive、非 transmit (observe/reflect/do_nothing) → 不发
  C. 有 motive、transmit → 才进既有 Agency/Expression (payload 逐字段不变)
  D. 同一 trigger、不同 Soul context → 结果可以不同 (Trigger ≠ Decision)
  E. Motive 不反向依赖 scheduler (prompt scheduler-agnostic)

SM-4 四元行动 (多元行动适配):
  - Decision 四元: transmit / observe / reflect / do_nothing, 互斥单选
  - do_nothing 是合法的主动选择 (不是失败兜底)
  - observe / reflect 的执行逻辑是后续工单, 本测试只验证选择层

Fail-closed (DECISION-PROMPT-CONTRACT §4, SM-4 扩展):
  F2. LLM 调用失败 → do_nothing
  F3. 输出非 JSON → do_nothing
  F4. 缺 decision / 非法值 → do_nothing
  F5. 禁止预设 YES (唯一默认是 do_nothing)
  F6. reason 缺失 → decision 照常生效 (不 gate), log warning

Frozen contract 检查:
  - transmit 时 AGENCY_TRIGGER payload 与现状逐字段一致 (验收 C)
  - 非 proactive_dm trigger_type 不受 Decision 层影响
  - motive 内容不进 payload
  - DecisionResult 保留 transmit: bool (scheduler 消费, 0 change)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root
from src.soul.decision import (
    DecisionResult,
    build_decision_prompt,
    decide_motive,
    parse_decision_output,
)
from src.soul.motive import (
    MOTIVE_STATUS_PENDING,
    MOTIVE_STATUS_REJECTED,
    MOTIVE_STATUS_TRANSMITTED,
    Motive,
    MotiveEngine,
    MotiveTraceStore,
    new_motive_id,
    now_utc_iso,
)
from src.soul.scheduler import SoulScheduler


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_trace_record(
    agent_id: str,
    ts: str,
    event_id: str = None,
    trigger_type: str = "diary:night",
) -> Dict[str, Any]:
    if event_id is None:
        event_id = uuid.uuid4().hex
    return {
        "event_id": event_id,
        "session_id": "sess-test",
        "correlation_id": "corr-test",
        "parent_event_id": None,
        "ts": ts,
        "provenance": {
            "trigger_type": trigger_type,
            "actor_id": agent_id,
            "source_system": "narrative",
            "trace_ref": None,
            "extras": {},
        },
        "lineage_depth": 0,
        "lineage_path": event_id,
    }


def _seed_trace_file(trace_path: Path, records: List[Dict[str, Any]]) -> None:
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trace_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _seed_motive_trace(
    motive_trace_path: Path,
    agent_id: str,
    content: str = "我想告诉你今天的事",
    provenance_ref: str = "evt_abc123",
    status: str = MOTIVE_STATUS_PENDING,
    motive_id: Optional[str] = None,
) -> str:
    """直接写一条 motive trace 记录 (pending)。"""
    motive_trace_path.parent.mkdir(parents=True, exist_ok=True)
    mid = motive_id or new_motive_id()
    record = {
        "motive_id": mid,
        "agent_id": agent_id,
        "status": status,
        "content": content,
        "target": "bryan",
        "provenance_ref": provenance_ref,
        "created_at": now_utc_iso(),
        "updated_at": now_utc_iso(),
    }
    with open(motive_trace_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return mid


class FakeProxy:
    """Mock LLMProxy: generate_text 返回预设响应序列。"""

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


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


def _run(coro):
    return asyncio.run(coro)


# ────────────────────────────────────────────────────────────
# A. 有 trigger、无 motive → 不发
# ────────────────────────────────────────────────────────────

class TestAcceptanceA_NoMotiveNoPublish:
    """A. 有 trigger、无 motive → 不发 (F1 / DECISION-PROMPT-CONTRACT §7-A)。"""

    def test_a1_no_trace_no_motive_no_publish(self, isolated_root, tmp_path):
        """A.1: trace 空 + motive trace 空 → proactive_dm 不发。"""
        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 0

    def test_a2_new_event_but_interpretation_no_motive(self, isolated_root, tmp_path, monkeypatch):
        """A.2: 有新 InnerLifeEvent, 但 interpretation 判定无念头 → 不发。"""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, trigger_type="diary:night"),
        ])
        fake = FakeProxy(['{"has_motive": false}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 0
        # interpretation 被调用一次 (有新 event), 但无 motive → 不发
        assert len(fake.calls) == 1

    def test_a3_interpretation_failure_no_motive(self, isolated_root, tmp_path, monkeypatch):
        """A.3: interpretation LLM 失败 (坏输出) → 无 motive → 不发 (fail-closed)。"""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, trigger_type="diary:night"),
        ])
        fake = FakeProxy(["not json at all"])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 0


# ────────────────────────────────────────────────────────────
# B. 有 motive、not_transmit → 不发
# ────────────────────────────────────────────────────────────

class TestAcceptanceB_MotiveNotTransmitNoPublish:
    """B. 有 motive、非 transmit (observe/reflect/do_nothing) → 不发 (验收 B)。"""

    def test_b1_motive_do_nothing_no_publish(self, isolated_root, tmp_path, monkeypatch):
        """B.1: seed pending motive + Decision LLM 返回 do_nothing → 不发 + motive rejected。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        mid = _seed_motive_trace(motive_trace_path, "agent_yua")
        fake = FakeProxy(['{"decision": "do_nothing", "reason": "此刻想安静度日"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 0
        # Decision LLM 被调用一次 (只有 decision, trace 空无 interpretation)
        assert len(fake.calls) == 1
        # motive 标记 rejected (终态, 不重试)
        store = MotiveTraceStore(trace_path=motive_trace_path)
        assert store.resolve_pending("agent_yua") is None
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# C. 有 motive、transmit → 才进既有 Agency/Expression
# ────────────────────────────────────────────────────────────

class TestAcceptanceC_MotiveTransmitPublishes:
    """C. 有 motive、transmit → 才进既有 Agency/Expression (payload 逐字段不变)。"""

    def test_c1_motive_transmit_publishes_payload_unchanged(self, isolated_root, tmp_path, monkeypatch):
        """C.1: seed motive + Decision LLM 返回 transmit → AGENCY_TRIGGER 发布, payload 逐字段不变。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        mid = _seed_motive_trace(motive_trace_path, "agent_yua")
        fake = FakeProxy(['{"decision": "transmit", "reason": "这个念头值得此刻说"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        # transmit → AGENCY_TRIGGER 发布 (既有 Agency/Expression 路径入口)
        assert len(captured) == 1
        payload = captured[0].payload
        # payload 与现状逐字段一致 (M5.2-G frozen schema, 6 字段, 无额外字段)
        assert set(payload.keys()) == {
            "trigger_type", "agent_id", "reason", "elapsed_mins", "timestamp", "extra",
        }
        assert payload["trigger_type"] == "proactive_dm"
        assert payload["agent_id"] == "agent_yua"
        assert payload["reason"] == "scheduler.proactive_dm"
        assert isinstance(payload["elapsed_mins"], float)
        assert isinstance(payload["timestamp"], str)
        # C-3.1 (2026-09-05, 契约 §2.3 #1): transmit 时 scheduler 把 motive.target
        # 写入既有 extra 通道 (TriggerEnvelope 0 结构变更, 0 新字段; extra 内容对齐
        # 新授权行为: extra={"motive_target": "bryan"})。seed target fixed="bryan"。
        assert payload["extra"] == {"motive_target": "bryan"}
        # motive 内容不进 payload (motive 是意图, 不是 payload 字段)
        assert "motive" not in payload
        assert "content" not in payload
        # motive 标记 transmitted
        store = MotiveTraceStore(trace_path=motive_trace_path)
        assert store.resolve_pending("agent_yua") is None
        latest = store._latest_by_motive_id()[mid]
        assert latest["status"] == MOTIVE_STATUS_TRANSMITTED


# ────────────────────────────────────────────────────────────
# D. 同一 trigger、不同 Soul context → 结果可以不同
# ────────────────────────────────────────────────────────────

class TestAcceptanceD_TriggerVsDecision:
    """D. Trigger ≠ Decision: prompt 无 trigger 字段, decision 是 (motive+context) 的函数。"""

    def test_d1_prompt_has_no_trigger_fields(self):
        """D.1 (契约级): build_decision_prompt 输出不含任何 trigger 字段。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(motive, provenance_desc="diary:night @ 2026-08-30")
        for forbidden in ["trigger_type", "elapsed_mins", "cooldown", "scheduler"]:
            assert forbidden not in prompt, f"prompt 不应含 trigger 字段: {forbidden}"

    def test_d2_different_motive_different_decision(self, isolated_root, tmp_path, monkeypatch):
        """D.2: 同一 trigger_type (proactive_dm), 两个不同 motive → 可返回不同 decision。"""
        m1 = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        m2 = Motive(
            motive_id="m2", content="我想告诉你一个秘密",
            target="bryan", provenance_ref="evt2", created_at=now_utc_iso(),
        )
        fake = FakeProxy([
            '{"decision": "transmit", "reason": "m1 值得说"}',
            '{"decision": "not_transmit", "reason": "m2 此刻不说"}',
        ])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _scenario():
            r1 = await decide_motive(m1, "agent_yua")
            r2 = await decide_motive(m2, "agent_yua")
            return r1, r2

        r1, r2 = _run(_scenario())
        # 同一 trigger 语境, 不同 motive → 不同 decision (Trigger ≠ Decision)
        assert r1.transmit is True
        assert r2.transmit is False
        # 两个 prompt 内容不同 (motive 是 decision 的输入)
        assert fake.calls[0]["messages"][0]["content"] != fake.calls[1]["messages"][0]["content"]


# ────────────────────────────────────────────────────────────
# E. Motive 不反向依赖 scheduler
# ────────────────────────────────────────────────────────────

class TestAcceptanceE_MotiveSchedulerAgnostic:
    """E. Motive 不反向依赖 scheduler (prompt scheduler-agnostic)。"""

    def test_e1_motive_record_has_no_trigger_type(self, isolated_root, tmp_path):
        """E.1: motive trace 记录不含 trigger_type 依赖。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_motive_trace(motive_trace_path, "agent_yua")
        lines = motive_trace_path.read_text(encoding="utf-8").splitlines()
        assert lines
        record = json.loads(lines[0])
        assert "trigger_type" not in record
        assert "elapsed_mins" not in record
        assert "cooldown" not in record

    def test_e2_prompt_scheduler_agnostic(self):
        """E.2 (契约级): Decision prompt 无 scheduler 字段。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(motive, provenance_desc="diary:night @ 2026-08-30")
        for forbidden in ["trigger_type", "elapsed_mins", "cooldown", "scheduler.proactive_dm"]:
            assert forbidden not in prompt


# ────────────────────────────────────────────────────────────
# Fail-closed (DECISION-PROMPT-CONTRACT §4)
# ────────────────────────────────────────────────────────────

class TestFailClosed:
    """F2-F6: fail-closed 规则。"""

    def test_f2_llm_failure_not_transmit(self, isolated_root, tmp_path, monkeypatch):
        """F2: Decision LLM 调用失败 (异常) → not_transmit → 不发。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_motive_trace(motive_trace_path, "agent_yua")

        class BoomProxy:
            async def generate_text(self, messages, agent_id="system", max_tokens=200, temperature=0.7):
                raise RuntimeError("LLM down")

        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: BoomProxy())

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 0

    def test_f3_non_json_do_nothing(self):
        """F3: 输出非 JSON → do_nothing (fail-closed, 默认合法选项)。"""
        result = parse_decision_output("I think you should send it")
        assert result is not None
        assert result["decision"] == "do_nothing"

    def test_f4_missing_decision_do_nothing(self):
        """F4: JSON 缺 decision / 非法值 → do_nothing (fail-closed)。"""
        r1 = parse_decision_output('{"reason": "no decision field"}')
        assert r1["decision"] == "do_nothing"
        r2 = parse_decision_output('{"decision": "maybe", "reason": "x"}')
        assert r2["decision"] == "do_nothing"

    def test_f5_no_preset_yes_default_not_transmit(self, isolated_root, tmp_path, monkeypatch):
        """F5: 无 LLM proxy (production 未注入) → LLM 返回 None → not_transmit → 不发。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        _seed_motive_trace(motive_trace_path, "agent_yua")
        # 不注入 proxy: _find_llm_proxy 返回 None → _default_llm_call 返回 None
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: None)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        # 唯一默认是 not_transmit: 无 LLM → 不发 (禁止预设 YES)
        assert len(captured) == 0

    def test_f6_reason_missing_decision_effective(self):
        """F6: reason 缺失 → decision 照常生效 (不 gate), 只 log warning。"""
        result = parse_decision_output('{"decision": "transmit"}')
        assert result is not None
        assert result["decision"] == "transmit"
        assert result["reason"] == ""


# ────────────────────────────────────────────────────────────
# Frozen contract 检查
# ────────────────────────────────────────────────────────────

class TestFrozenContract:
    """Frozen contract 0 change 验证。"""

    @pytest.mark.parametrize("trigger_type", ["event", "dream", "morning", "night"])
    def test_g1_non_proactive_dm_unaffected(self, isolated_root, tmp_path, trigger_type):
        """G.1: 非 proactive_dm trigger_type 不受 Decision 层影响 (照常 publish)。"""
        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type=trigger_type,
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        assert len(captured) == 1
        assert captured[0].payload["trigger_type"] == trigger_type

    def test_g2_decision_does_not_create_inner_life_event(self, isolated_root, tmp_path, monkeypatch):
        """G.2: Decision 层不写 InnerLifeEvent (只读 trace, 唯一写入是 motive trace)。"""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, trigger_type="diary:night"),
        ])
        fake = FakeProxy(['{"has_motive": true, "content": "我想告诉你今天的事"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _run_scenario():
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
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = _run(_run_scenario())
        # interpretation 产出 motive → decision LLM 失败 (responses 空 → None)
        # → fail-closed not_transmit → 不发
        assert len(captured) == 0
        # trace 文件行数不变 (Decision 层不写 InnerLifeEvent)
        lines = trace_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        # motive trace 有记录 (唯一写入 = motive trace, append-only 快照:
        # pending 行 + rejected 行, decision fail-closed → not_transmit → rejected)
        motive_trace = isolated_root / "soul" / "motive_trace.jsonl"
        assert motive_trace.exists()
        m_lines = motive_trace.read_text(encoding="utf-8").splitlines()
        assert len(m_lines) == 2
        latest = json.loads(m_lines[-1])
        assert latest["status"] == "rejected"


# ────────────────────────────────────────────────────────────
# Motive 模块单元测试
# ────────────────────────────────────────────────────────────

class TestMotiveModule:
    """Motive dataclass + interpretation 产生机制。"""

    def test_i1_motive_shape(self):
        """I.1: Motive dataclass 5 字段 (工单锁定)。"""
        m = Motive(
            motive_id="a" * 32, content="hi", target="bryan",
            provenance_ref="b" * 32, created_at=now_utc_iso(),
        )
        assert m.motive_id == "a" * 32
        assert m.content == "hi"
        assert m.target == "bryan"
        assert m.provenance_ref == "b" * 32
        assert m.created_at
        # frozen
        with pytest.raises(Exception):
            m.content = "changed"

    def test_i2_interpretation_produces_motive(self, isolated_root, tmp_path, monkeypatch):
        """I.2: 新 InnerLifeEvent + interpretation 有念头 → motive 写入 trace。"""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        event_id = uuid.uuid4().hex
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, event_id=event_id, trigger_type="diary:night"),
        ])
        fake = FakeProxy(['{"has_motive": true, "content": "我想告诉你今天的事"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        engine = MotiveEngine()
        produced = _run(engine.interpret_new_events("agent_yua"))
        assert len(produced) == 1
        m = produced[0]
        assert m.target == "bryan"
        assert m.provenance_ref == event_id
        assert m.content == "我想告诉你今天的事"
        # resolve pending 能拿到
        pending = engine.resolve_pending("agent_yua")
        assert pending is not None
        assert pending.motive_id == m.motive_id

    def test_i3_interpretation_idempotent(self, isolated_root, tmp_path, monkeypatch):
        """I.3: 同一 event 不重复 interpretation (provenance_ref 去重)。"""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        event_id = uuid.uuid4().hex
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, event_id=event_id, trigger_type="diary:night"),
        ])
        fake = FakeProxy(['{"has_motive": true, "content": "第一次"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        engine = MotiveEngine()
        produced1 = _run(engine.interpret_new_events("agent_yua"))
        assert len(produced1) == 1
        # 第二次: 同一 event 已解释 → 不调 LLM, 不重复产生
        produced2 = _run(engine.interpret_new_events("agent_yua"))
        assert len(produced2) == 0
        assert len(fake.calls) == 1

    def test_i4_motive_ttl_expired(self, isolated_root, tmp_path):
        """I.4: pending motive 超过 TTL → expired, 不 resolve。"""
        motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
        old_created = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        motive_trace_path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "motive_id": "m_ttl",
            "agent_id": "agent_yua",
            "status": MOTIVE_STATUS_PENDING,
            "content": "旧念头",
            "target": "bryan",
            "provenance_ref": "evt_old",
            "created_at": old_created,
            "updated_at": old_created,
        }
        with open(motive_trace_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        store = MotiveTraceStore(trace_path=motive_trace_path)
        # TTL 24h, 已 48h → expired
        assert store.resolve_pending("agent_yua", ttl_hours=24) is None
        latest = store._latest_by_motive_id()["m_ttl"]
        assert latest["status"] == "expired"


# ────────────────────────────────────────────────────────────
# Decision prompt 禁止句检查 (DECISION-PROMPT-CONTRACT §2.5)
# ────────────────────────────────────────────────────────────

class TestDecisionPromptForbiddenPhrases:
    """J. 禁止句清单逐条对照 (SM-2 §2.5 全表)。"""

    FORBIDDEN = [
        # Framing
        "You are the decision module", "你是决策模块",
        "This is your choice", "这是你的选择",
        "You are autonomous", "你是自主的",
        "请评估是否发送", "评估该不该发", "系统正在评估",
        "你想不想分享", "Do you want to share",
        # Motive
        "你应该分享", "这是值得分享的",
        "你很喜欢分享，所以请分享",
        # Boundary
        "请选择发送",
        # 全局
        "值得度", "worthiness", "priority",
        "later", "never",
    ]

    def test_j1_forbidden_phrases_absent(self):
        """J.1: build_decision_prompt 输出不含任何禁止句。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(
            motive,
            provenance_desc="diary:night @ 2026-08-30",
            relationship_summary="印象：温柔；信任度：0.60",
            memory_summary="昨天一起散步",
            emergent_summary="dream:dream @ 2026-08-30",
        )
        for phrase in self.FORBIDDEN:
            assert phrase not in prompt, f"prompt 含禁止句: {phrase!r}"

    def test_j2_prompt_has_four_blocks(self):
        """J.2: prompt 四块结构 (Framing / Motive / Context / Boundary)。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(
            motive,
            provenance_desc="diary:night @ 2026-08-30",
            relationship_summary="信任度：0.60",
        )
        # Framing
        assert "你心里有一个念头，已经成形" in prompt
        # Motive (content + target + provenance)
        assert "你想告诉 bryan：我想告诉你今天的事" in prompt
        assert "这个念头来自：diary:night @ 2026-08-30" in prompt
        # Context (relationship)
        assert "你与 bryan 的关系：信任度：0.60" in prompt
        # Boundary (四元, SM-4: transmit/observe/reflect/do_nothing, 互斥单选)
        assert "现在有四个选择，只能选一个" in prompt
        assert "transmit — 现在把念头化为讯息，传给 Bry" in prompt
        assert "observe — 现在不传，先观察环境" in prompt
        assert "reflect — 现在不传，先回顾记忆" in prompt
        assert "do_nothing — 现在不传，安静度日" in prompt
        assert "这是合法的主动选择，不是失败兜底" in prompt
        assert '{"decision": "transmit" | "observe" | "reflect" | "do_nothing", "reason": "..."}' in prompt


# ────────────────────────────────────────────────────────────
# SM-4 四元行动 (多元行动适配)
# ────────────────────────────────────────────────────────────

class TestSM4_QuadrupleDecision:
    """SM-4: 四元选择 + 互斥单选 + do_nothing 主动选择 + fail-closed。"""

    def test_k1_parse_all_four_actions(self):
        """K.1: 四元各自解析正确 (transmit/observe/reflect/do_nothing)。"""
        for action in ["transmit", "observe", "reflect", "do_nothing"]:
            result = parse_decision_output(f'{{"decision": "{action}", "reason": "r"}}')
            assert result["decision"] == action
            assert result["reason"] == "r"

    def test_k2_mutually_exclusive_single_choice(self):
        """K.2: 互斥单选 — prompt 明确只选一个; 复合动作不是合法值。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(motive, provenance_desc="diary:night @ 2026-08-30")
        assert "只能选一个" in prompt
        # 复合动作 (如 transmit+observe) 不是合法值 → fail-closed do_nothing
        result = parse_decision_output('{"decision": "transmit+observe", "reason": "x"}')
        assert result["decision"] == "do_nothing"

    def test_k3_do_nothing_is_active_choice(self):
        """K.3: do_nothing 是主动选择 — prompt 明确声明, 非失败兜底。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(motive, provenance_desc="diary:night @ 2026-08-30")
        assert "这是合法的主动选择，不是失败兜底" in prompt

    def test_k4_fail_closed_do_nothing(self):
        """K.4: fail-closed — 坏输出/非 JSON/缺 decision/旧值 not_transmit → do_nothing。"""
        assert parse_decision_output(None)["decision"] == "do_nothing"
        assert parse_decision_output("garbage")["decision"] == "do_nothing"
        assert parse_decision_output('{"reason": "no decision"}')["decision"] == "do_nothing"
        # 旧二元值 not_transmit 不再是合法值 → fail-closed do_nothing
        assert parse_decision_output('{"decision": "not_transmit", "reason": "旧值"}')["decision"] == "do_nothing"

    def test_k5_decision_result_carries_decision_and_transmit(self, isolated_root, tmp_path, monkeypatch):
        """K.5: DecisionResult 带四元 decision + transmit 兼容字段 (scheduler 0 change)。"""
        m = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        fake = FakeProxy(['{"decision": "observe", "reason": "先看看再说"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _scenario():
            return await decide_motive(m, "agent_yua")

        r = _run(_scenario())
        assert r.decision == "observe"
        assert r.transmit is False  # 兼容字段: 只有 transmit 才 True
        assert r.reason == "先看看再说"

    def test_k6_only_transmit_publishes(self, isolated_root, tmp_path, monkeypatch):
        """K.6: 只有 transmit 才 publish; observe/reflect/do_nothing 均不 publish (scheduler 行为不变)。"""
        for action, reason in [
            ("observe", "先观察"),
            ("reflect", "先回顾"),
            ("do_nothing", "安静度日"),
        ]:
            motive_trace_path = isolated_root / "soul" / "motive_trace.jsonl"
            mid = _seed_motive_trace(motive_trace_path, "agent_yua")
            fake = FakeProxy([f'{{"decision": "{action}", "reason": "{reason}"}}'])
            monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

            async def _run_scenario():
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
                    scheduler = SoulScheduler(bus=bus)
                    await scheduler._publish_agency_trigger(
                        agent_id="agent_yua",
                        trigger_type="proactive_dm",
                    )
                    return captured
                finally:
                    await bus.stop()

            captured = _run(_run_scenario())
            assert len(captured) == 0, f"{action} 不应 publish"
            store = MotiveTraceStore(trace_path=motive_trace_path)
            latest = store._latest_by_motive_id()[mid]
            assert latest["status"] == MOTIVE_STATUS_REJECTED


# ────────────────────────────────────────────────────────────
# SM-4.5 时间注入 (消除时间幻觉)
# ────────────────────────────────────────────────────────────

class TestSM45_TimeInjection:
    """SM-4.5: Decision prompt 注入当前时间感知 (Context 区块)。"""

    def test_l1_time_injected_in_context_block(self):
        """L.1: current_time 提供时, Context 区块注入 [當前時間感知] (時間 + 時段)。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(
            motive, provenance_desc="diary:night @ 2026-08-30",
            current_time="2026-09-02 14:00",
        )
        assert "[當前時間感知]" in prompt
        assert "當前時間：2026-09-02 14:00" in prompt
        assert "當前時段：afternoon" in prompt

    def test_l2_period_boundaries(self):
        """L.2: 时段判定边界 (05/11/17/22)。"""
        cases = [
            ("2026-09-02 05:00", "morning"),
            ("2026-09-02 10:59", "morning"),
            ("2026-09-02 11:00", "afternoon"),
            ("2026-09-02 16:59", "afternoon"),
            ("2026-09-02 17:00", "evening"),
            ("2026-09-02 21:59", "evening"),
            ("2026-09-02 22:00", "late_night"),
            ("2026-09-02 23:00", "late_night"),
            ("2026-09-02 00:00", "late_night"),
            ("2026-09-02 04:59", "late_night"),
        ]
        for ts, period in cases:
            prompt = build_decision_prompt(
                Motive(
                    motive_id="m1", content="x", target="bryan",
                    provenance_ref="evt1", created_at=now_utc_iso(),
                ),
                provenance_desc="",
                current_time=ts,
            )
            assert f"當前時段：{period}" in prompt, f"{ts} → {period}"

    def test_l3_default_none_no_injection(self):
        """L.3: 默认 current_time=None → 不注入时间 (向后兼容)。"""
        motive = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(motive, provenance_desc="diary:night @ 2026-08-30")
        assert "當前時間感知" not in prompt
        assert "當前時間" not in prompt
        assert "當前時段" not in prompt

    def test_l4_invalid_time_no_injection(self):
        """L.4: 无法解析的时间 → 不注入 (fail-safe)。"""
        motive = Motive(
            motive_id="m1", content="x", target="bryan",
            provenance_ref="evt1", created_at=now_utc_iso(),
        )
        prompt = build_decision_prompt(
            motive, provenance_desc="", current_time="not-a-time"
        )
        assert "當前時間感知" not in prompt

    def test_l5_decide_motive_passes_time(self, isolated_root, tmp_path, monkeypatch):
        """L.5: decide_motive 透传 current_time 进 prompt。"""
        m = Motive(
            motive_id="m1", content="我想告诉你今天的事",
            target="bryan", provenance_ref="evt1", created_at=now_utc_iso(),
        )
        fake = FakeProxy(['{"decision": "do_nothing", "reason": "安静"}'])
        monkeypatch.setattr("src.soul.motive._find_llm_proxy", lambda: fake)

        async def _scenario():
            return await decide_motive(m, "agent_yua", current_time="2026-09-02 14:00")

        r = _run(_scenario())
        assert r.decision == "do_nothing"
        prompt = fake.calls[0]["messages"][0]["content"]
        assert "[當前時間感知]" in prompt
        assert "當前時段：afternoon" in prompt
