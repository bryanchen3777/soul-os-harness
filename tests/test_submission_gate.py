"""
Elevation Submission Gate（SG-1）— 测试

工单：SG-1 修复。加 Elevation Submission Gate：验证
``id → canonical InnerLifeEvent（查 inner_life store/trace）→ producer 合法 →
由 InnerLifeWriter 创建``，伪造 id **fail-closed**。只准 ``consume()``
（destination=pattern），**永不 ``elevate()``**。

测试分节：
  A. verify：伪造 id fail-closed（格式非法 / 非 writer 创建 / trace 缺失 / producer 非法）
  B. verify：合法 id 通过（writer 创建 + trace 存在 + producer 合法）
  C. submit：合法 id → consume 产 pattern 节点（destination=pattern）
  D. submit：伪造 id → fail-closed 返回 []，不产节点
  E. 只 consume 不 elevate（AST 红线 + 行为验证：产 pattern 而非灵魂结构）
  F. world:* trigger_type 通过（M5.9-3 WorldInnerLifeAdapter 产生的 world 事件）
  G. 失败隔离 + disabled no-op
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.inner_life import (
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    SubmissionGate,
    SubmissionVerdict,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_SYSTEM,
)

_GATE_SRC = Path(__file__).resolve().parents[1] / "src" / "inner_life" / "submission_gate.py"


# ── helpers ──────────────────────────────────────────────────────────


def _make_writer(tmp_path: Path) -> InnerLifeWriter:
    """带 trace 的 writer（trace 佐证可用）。"""
    trace_path = tmp_path / "inner_life" / "trace.jsonl"
    return InnerLifeWriter(trace_writer=NarrativeTraceWriter(trace_log_path=trace_path))


def _make_gate(
    writer: InnerLifeWriter,
    tmp_path: Path,
    *,
    trace_reader: bool = True,
    **kwargs,
) -> SubmissionGate:
    trace_path = tmp_path / "inner_life" / "trace.jsonl"
    reader = NarrativeTraceReader(trace_log_path=trace_path) if trace_reader else None
    return SubmissionGate(writer=writer, trace_reader=reader, **kwargs)


def _create_event(
    writer: InnerLifeWriter,
    trigger_type: str = TRIGGER_TYPE_DIARY_NIGHT,
    *,
    actor_id: str = "agent_rem",
    source_system: str = "diary",
    extras: dict | None = None,
):
    return writer.create_event(
        provenance=Provenance(
            trigger_type=trigger_type,
            actor_id=actor_id,
            source_system=source_system,
            extras=extras or {},
        ),
    )


# ── A. verify：伪造 id fail-closed ──────────────────────────────────


class TestVerifyForgedFailClosed:
    def test_a1_invalid_format_rejected(self, tmp_path):
        """A.1: 非 32-hex 格式 → REJECT（fail-closed）。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path)
        verdict = gate.verify("not-a-valid-event-id")
        assert not verdict.accepted
        assert "invalid event_id format" in verdict.reason

    def test_a2_forged_id_not_created_by_writer(self, tmp_path):
        """A.2: 伪造 id（格式合法但非 InnerLifeWriter 创建）→ REJECT。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path)
        forged = "f" * 32  # 32-hex 格式合法，但 writer 从未创建
        verdict = gate.verify(forged)
        assert not verdict.accepted
        assert "不是由 InnerLifeWriter 创建" in verdict.reason

    def test_a3_trace_missing_rejected(self, tmp_path):
        """A.3: writer 创建但 trace 佐证缺失 → REJECT（fail-closed）。"""
        writer = _make_writer(tmp_path)
        # 用另一个 writer 创建事件（trace 写到别处），当前 writer 不知道
        other_writer = InnerLifeWriter()  # 无 trace
        event = _create_event(other_writer)
        gate = _make_gate(writer, tmp_path)
        verdict = gate.verify(event.event_id)
        assert not verdict.accepted
        assert "不是由 InnerLifeWriter 创建" in verdict.reason

    def test_a4_producer_illegal_rejected(self, tmp_path):
        """A.4: producer trigger_type 不在合法集合 → REJECT（fail-closed）。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, trigger_type="hacker:exploit")
        gate = _make_gate(writer, tmp_path)
        verdict = gate.verify(event.event_id)
        assert not verdict.accepted
        assert "producer trigger_type" in verdict.reason

    def test_a5_verify_has_no_side_effect(self, tmp_path):
        """A.5: verify 是纯验证，不 consume（stats 不增长）。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path)
        gate.verify("f" * 32)
        assert gate.get_stats()["submissions"] == 0
        assert gate.get_stats()["consumed"] == 0


# ── B. verify：合法 id 通过 ──────────────────────────────────────────


class TestVerifyLegitPass:
    def test_b1_legit_event_passes(self, tmp_path):
        """B.1: writer 创建 + trace 存在 + producer 合法 → ACCEPT。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT)
        gate = _make_gate(writer, tmp_path)
        verdict = gate.verify(event.event_id)
        assert verdict.accepted
        assert verdict.event is not None
        assert verdict.event.event_id == event.event_id

    def test_b2_agent_reply_passes(self, tmp_path):
        """B.2: TRIGGER_TYPE_AGENT_REPLY（proactive 类 producer）→ ACCEPT。"""
        writer = _make_writer(tmp_path)
        event = _create_event(
            writer, TRIGGER_TYPE_AGENT_REPLY, source_system="narrative"
        )
        gate = _make_gate(writer, tmp_path)
        assert gate.verify(event.event_id).accepted

    def test_b3_system_passes(self, tmp_path):
        """B.3: TRIGGER_TYPE_SYSTEM → ACCEPT。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_SYSTEM, source_system="system")
        gate = _make_gate(writer, tmp_path)
        assert gate.verify(event.event_id).accepted

    def test_b4_trace_reader_optional(self, tmp_path):
        """B.4: 不配 trace_reader 时，writer 权威足够 → ACCEPT。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer)
        gate = _make_gate(writer, tmp_path, trace_reader=False)
        assert gate.verify(event.event_id).accepted


# ── C. submit：合法 id → consume 产 pattern ─────────────────────────


class TestSubmitConsume:
    def test_c1_legit_submit_consumes_pattern(self, tmp_path):
        """C.1: 合法 id → consume 产 pattern 节点（destination=pattern）。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit(event.event_id)

        assert len(nodes) == 1
        assert nodes[0].node_type == "pattern"  # consume 只产 pattern（SE-2）
        assert gate.get_stats()["accepted"] == 1
        assert gate.get_stats()["consumed"] == 1
        assert gate.get_stats()["rejected"] == 0

    def test_c2_submit_writes_elevation_store(self, tmp_path):
        """C.2: consume 后 data/elevation/ 有 trace + nodes + edges。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        gate.submit(event.event_id)

        assert (tmp_path / "elevation" / "elevation_trace.jsonl").exists()
        assert (tmp_path / "elevation" / "elevation_nodes.jsonl").exists()
        assert (tmp_path / "elevation" / "elevation_edges.jsonl").exists()

    def test_c3_submit_with_memory_facts(self, tmp_path):
        """C.3: 带 memory_facts → 事件 + fact 都 consume。"""
        from src.memory.v1.schema import Memory

        writer = _make_writer(tmp_path)
        event = _create_event(writer)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")
        mem = Memory(
            memory_id="mem-0001",
            agent_id="agent_rem",
            content="我重视自由",
            tags=["preference"],
            created_at=1756000000.0,
            category="preference",
            confidence=0.9,
            inner_life_event_id=None,
        )

        nodes = gate.submit(event.event_id, [mem])

        assert len(nodes) == 2  # 事件 1 个 + memory 1 个
        assert all(n.node_type == "pattern" for n in nodes)


# ── D. submit：伪造 id → fail-closed [] ─────────────────────────────


class TestSubmitFailClosed:
    def test_d1_forged_id_returns_empty(self, tmp_path):
        """D.1: 伪造 id → submit 返回 []，不 consume。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit("f" * 32)

        assert nodes == []
        assert gate.get_stats()["rejected"] == 1
        assert gate.get_stats()["consumed"] == 0
        assert not (tmp_path / "elevation" / "elevation_trace.jsonl").exists()

    def test_d2_invalid_format_returns_empty(self, tmp_path):
        """D.2: 格式非法 → submit 返回 []。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path)
        assert gate.submit("garbage-id") == []

    def test_d3_illegal_producer_returns_empty(self, tmp_path):
        """D.3: producer 非法 → submit 返回 []。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, trigger_type="hacker:exploit")
        gate = _make_gate(writer, tmp_path)
        assert gate.submit(event.event_id) == []
        assert gate.get_stats()["rejected"] == 1


# ── E. 只 consume 不 elevate ────────────────────────────────────────


class TestConsumeOnlyNeverElevate:
    def test_e1_gate_source_never_calls_elevate(self):
        """E.1: AST 红线 — submission_gate.py 源码不调用 elevate()。"""
        tree = ast.parse(_GATE_SRC.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr == "elevate":
                    raise AssertionError("SubmissionGate 不得调用 elevate()")
                if isinstance(func, ast.Name) and func.id == "elevate":
                    raise AssertionError("SubmissionGate 不得调用 elevate()")

    def test_e2_consume_produces_pattern_not_soul_node(self, tmp_path):
        """E.2: 行为验证 — consume 产 pattern（候选），不产灵魂结构（belief 等）。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit(event.event_id)

        assert nodes
        assert all(n.node_type == "pattern" for n in nodes)
        assert all(n.candidate_node_type in {"belief", "value", "trait", "essence"}
                   for n in nodes)  # LLM 后验只作候选解释，不直接成为灵魂结构


# ── F. world:* trigger_type 通过（M5.9-3）────────────────────────────


class TestWorldTrigger:
    def test_f1_world_trigger_passes(self, tmp_path):
        """F.1: world:news_event（M5.9-3 WorldInnerLifeAdapter 产生）→ ACCEPT。"""
        writer = _make_writer(tmp_path)
        event = _create_event(
            writer,
            trigger_type="world:news_event",
            source_system="narrative",
            extras={"world_source": "news", "world_type": "news_event"},
        )
        gate = _make_gate(writer, tmp_path)
        assert gate.verify(event.event_id).accepted

    def test_f2_world_trigger_consumes(self, tmp_path):
        """F.2: world:rain_started → submit consume 产 pattern。"""
        writer = _make_writer(tmp_path)
        event = _create_event(
            writer,
            trigger_type="world:rain_started",
            source_system="narrative",
            extras={"world_source": "weather", "world_type": "rain_started"},
        )
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit(event.event_id)

        assert len(nodes) == 1
        assert nodes[0].node_type == "pattern"


# ── G. 失败隔离 + disabled no-op ────────────────────────────────────


class TestFailureIsolation:
    def test_g1_disabled_is_noop(self, tmp_path):
        """G.1: enabled=False → submit 直接返回 []，不验证不 consume。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer)
        gate = _make_gate(writer, tmp_path, enabled=False)
        assert gate.submit(event.event_id) == []
        assert gate.get_stats()["submissions"] == 0

    def test_g2_consume_failure_isolated(self, tmp_path):
        """G.2: consume 内部异常 → 返回 []，不 raise（双层失败隔离）。

        run_elevation 内部有失败隔离（异常 → warning + []），Gate 的 submit
        正常返回 []，不 raise，不阻断调用方主路径。
        """
        writer = _make_writer(tmp_path)
        event = _create_event(writer)

        class _BoomLLM:
            def classify(self, content, provenance, prior_node_type):
                raise RuntimeError("boom")

        gate = _make_gate(
            writer, tmp_path, llm=_BoomLLM(), store_dir=tmp_path / "elevation"
        )
        nodes = gate.submit(event.event_id)
        assert nodes == []  # 失败隔离：不 raise，返回 []

    def test_g3_writer_required(self, tmp_path):
        """G.3: writer 必填（InnerLifeWriter 是 sole canonical creator）。"""
        with pytest.raises(ValueError):
            SubmissionGate(writer=None)


# ── H. agent_id 归属（EL-OWN-0：diary/dream/event → 具体灵魂，world → default）────


class TestAgentIdAttribution:
    def test_h1_submit_explicit_agent_id_wins(self, tmp_path):
        """H.1: submit(agent_id=...) → 节点 agent_id 归属该灵魂（显式 > actor 兜底）。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit(event.event_id, agent_id="agent_yua")

        assert len(nodes) == 1
        assert nodes[0].agent_id == "agent_yua"
        assert nodes[0].agent_id != "default"

    def test_h2_diary_dream_event_actors_attributed(self, tmp_path):
        """H.2: diary/dream/event（actor_id=灵魂）→ 节点归属该灵魂（非 default）。"""
        writer = _make_writer(tmp_path)
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")
        events = [
            _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem"),
            _create_event(writer, TRIGGER_TYPE_DREAM_DREAM, actor_id="agent_yua"),
            _create_event(
                writer,
                TRIGGER_TYPE_AGENT_REPLY,
                actor_id="agent_akai",
                source_system="narrative",
            ),
        ]
        for event in events:
            nodes = gate.submit(event.event_id, agent_id=event.provenance.actor_id)
            assert len(nodes) == 1
            assert nodes[0].agent_id == event.provenance.actor_id
            assert nodes[0].agent_id != "default"

    def test_h3_world_keeps_default(self, tmp_path):
        """H.3: world 事件（actor_id=None，不传 agent_id）→ 节点保持 "default"。"""
        writer = _make_writer(tmp_path)
        event = _create_event(
            writer,
            trigger_type="world:news_event",
            actor_id=None,  # world 语义：无 agent actor
            source_system="narrative",
            extras={"world_source": "news", "world_type": "news_event"},
        )
        assert event.provenance.actor_id is None
        gate = _make_gate(writer, tmp_path, store_dir=tmp_path / "elevation")

        nodes = gate.submit(event.event_id)  # 不传 agent_id（EL-OWN-0 决策 #2）

        assert len(nodes) == 1
        assert nodes[0].agent_id == "default"  # system-level，无 agent 语义

    def test_h4_gate_constructor_agent_id_default(self, tmp_path):
        """H.4: Gate 构造 agent_id 作默认；submit 不传时用之，显式传则覆盖。"""
        writer = _make_writer(tmp_path)
        event = _create_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        gate = _make_gate(
            writer, tmp_path, agent_id="agent_sys", store_dir=tmp_path / "elevation"
        )

        nodes_default = gate.submit(event.event_id)  # 用构造默认
        assert nodes_default[0].agent_id == "agent_sys"

        nodes_override = gate.submit(event.event_id, agent_id="agent_yua")  # 显式覆盖
        assert nodes_override[0].agent_id == "agent_yua"

    def test_h5_run_server_submit_wiring(self):
        """H.5: run_server.py 4 处 submit — diary/dream/event 传 agent_id，world 不传。"""
        src = Path(__file__).resolve().parents[1] / "scripts" / "run_server.py"
        tree = ast.parse(src.read_text(encoding="utf-8"))
        submits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr != "submit":
                continue
            value = func.value
            is_gate = (
                isinstance(value, ast.Name) and value.id == "submission_gate"
            ) or (
                isinstance(value, ast.Attribute) and value.attr == "_submission_gate"
            )
            if not is_gate:
                continue
            kwargs = {kw.arg: kw.value for kw in node.keywords if kw.arg}
            submits.append(kwargs)

        assert len(submits) == 4, f"预期 4 处 gate submit，实际 {len(submits)}"
        with_agent = [k for k in submits if "agent_id" in k]
        without_agent = [k for k in submits if "agent_id" not in k]
        assert len(with_agent) == 3, "diary/dream/event 3 处 submit 都应传 agent_id"
        assert len(without_agent) == 1, "world 1 处 submit 应保持不传 agent_id"
