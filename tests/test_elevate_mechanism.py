"""
Elevate 调用机制（EL-DD-2：diary/dream → elevation 打通 + elevate 调用）— 测试

工单「打通 diary/dream → elevation（substrate + elevate 机制）」：
  1. diary/dream 的 InnerLifeEvent 进 elevation（agent_id 归属正确，非 "default"）。
  2. elevate() 被调用：**独立证据累积达阈值（min_evidence=2）后**才 elevate
     pattern → belief/value/trait/essence（维度由 prior 表 / LLM 后验候选决定），
     不只是 pattern+belief。
  3. 触发方式：证据驱动（consume 后检查），不是 scheduled job；Submission Gate
     保持「只 consume 不 elevate」（elevate 独立于 Gate）。
  4. 不改 soul-elevation API、不改 frozen contract。

测试分节：
  A. 不提前：独立证据不足（<2）不 elevate，只留 pattern
  B. 阈值达标：两条独立 diary 事件 → elevate 产 value（prior 基调），agent 归属正确
  C. dream → trait：两条独立 dream:dream 事件 → elevate 产 trait
  D. world → belief：两条独立 world:news_event（default）→ elevate 产 belief，保持 "default"
  E. 幂等：已消化证据不重复计票，无新 consume 时再跑不产新节点
  F. agent 隔离：只聚合同一 agent 的 pattern 计票
  G. min_evidence 可配：3 条才升（min_evidence=3）
  H. 独立证据语义：同一事件重复 consume → 同 (source_id, event) 计 1，不凑数
  I. 端到端（Gate 路径）：submit(diary) 两次 → elevate → 落盘 node_type=value、agent_id 正确
  J. 生产接线 + AST 红线：run_server 每处 submit 后都有 elevate 检查；submission_gate 源码不调 elevate
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from src.inner_life import (
    InnerLifeWriter,
    Provenance,
    SubmissionGate,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
)
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    elevate_matured_patterns,
    run_elevation,
)
from src.inner_life.emergent_projection import load_elevation_nodes

_SOUL_TYPES = {"belief", "value", "trait", "essence"}


# ── helpers ──────────────────────────────────────────────────────────


def _make_event(writer: InnerLifeWriter, trigger_type: str, *, actor_id: str):
    return writer.create_event(
        provenance=Provenance(
            trigger_type=trigger_type,
            actor_id=actor_id,
            source_system="narrative",
        ),
    )


def _write_node_types(store_dir: Path) -> list[str]:
    nodes = load_elevation_nodes(store_dir)
    return [n["node_type"] for n in nodes]


# ── A. 不提前：独立证据不足不 elevate ─────────────────────────────────


def test_a1_single_event_no_elevate(tmp_path):
    """单条 diary 事件 → consume 产 pattern；elevate 检查不产灵魂结构（1 < 2）。"""
    writer = InnerLifeWriter()
    event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
    store_dir = tmp_path / "elevation"

    nodes = run_elevation(event, [], store_dir=store_dir)
    assert len(nodes) == 1
    assert nodes[0].node_type == "pattern"

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert elevated == []  # 独立证据 1 < 2（min_evidence）→ 不提前
    types = _write_node_types(store_dir)
    assert types == ["pattern"]


def test_a2_min_evidence_3_two_events_not_enough(tmp_path):
    """min_evidence=3：两条独立 diary 事件 → 独立证据 2 < 3 → 不升。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    for _ in range(2):
        event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        run_elevation(event, [], store_dir=store_dir)

    elevated = elevate_matured_patterns(store_dir=store_dir, min_evidence=3)
    assert elevated == []
    assert _write_node_types(store_dir) == ["pattern", "pattern"]

    # 第三条达标 → 升
    event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
    run_elevation(event, [], store_dir=store_dir)
    elevated = elevate_matured_patterns(store_dir=store_dir, min_evidence=3)
    assert len(elevated) == 1
    assert elevated[0].node_type in _SOUL_TYPES


# ── B. 阈值达标：diary → value，agent 归属正确 ────────────────────────


def test_b1_two_diary_events_elevate_to_value(tmp_path):
    """两条独立 diary:night → elevate 产 value（prior 基调），agent 归属该灵魂。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    events = [
        _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        for _ in range(2)
    ]

    pattern_ids = []
    for event in events:
        nodes = run_elevation(event, [], store_dir=store_dir)
        pattern_ids.append(nodes[0].node_id)

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert len(elevated) == 1
    soul = elevated[0]
    assert soul.node_type == "value"  # diary prior 基调 ("value","trait","belief")
    assert soul.agent_id == "agent_rem"  # 非 "default"
    assert soul.agent_id != "default"
    assert soul.parent_node_id in pattern_ids  # 因果树挂到被消化的 pattern
    assert soul.lineage_depth == 1

    # 落盘：nodes 文件含 soul node（value），edges 含新证据边
    nodes = load_elevation_nodes(store_dir)
    types = [n["node_type"] for n in nodes]
    assert types.count("value") == 1
    assert types.count("pattern") == 2
    edge_records = [
        json.loads(l)
        for l in (store_dir / EDGES_FILENAME).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    assert any(e["node_id"] == soul.node_id for e in edge_records)


# ── C. dream → trait ─────────────────────────────────────────────────


def test_c1_two_dream_events_elevate_to_trait(tmp_path):
    """两条独立 dream:dream → elevate 产 trait（prior 基调）。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    for _ in range(2):
        event = _make_event(writer, TRIGGER_TYPE_DREAM_DREAM, actor_id="agent_yua")
        run_elevation(event, [], store_dir=store_dir)

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert len(elevated) == 1
    assert elevated[0].node_type == "trait"
    assert elevated[0].agent_id == "agent_yua"


# ── D. world → belief（default 保持）─────────────────────────────────


def test_d1_two_world_events_elevate_belief_default(tmp_path):
    """两条独立 world:news_event（actor_id=None，不传 agent_id）→ elevate 产 belief，
    归属保持 "default"（system-level，无具体灵魂语义）。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    for _ in range(2):
        event = writer.create_event(
            provenance=Provenance(
                trigger_type="world:news_event",
                actor_id=None,
                source_system="narrative",
            ),
        )
        run_elevation(event, [], store_dir=store_dir)

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert len(elevated) == 1
    assert elevated[0].node_type == "belief"  # world:news_event prior ("belief",)
    assert elevated[0].agent_id == "default"


# ── E. 幂等：已消化证据不重复计票 ─────────────────────────────────────


def test_e1_no_double_elevate_without_new_consume(tmp_path):
    """elevate 后无新 consume 再跑 → 已消化证据不重复计票，不产新节点。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    for _ in range(2):
        event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        run_elevation(event, [], store_dir=store_dir)

    first = elevate_matured_patterns(store_dir=store_dir)
    assert len(first) == 1

    second = elevate_matured_patterns(store_dir=store_dir)
    assert second == []  # 幂等：同一批证据不能支持第二颗灵魂结构

    types = _write_node_types(store_dir)
    assert types.count("value") == 1  # 不重复产 value


# ── F. agent 隔离：只聚合同一 agent 的 pattern 计票 ───────────────────


def test_f1_agent_isolation(tmp_path):
    """agent_rem 2 条 diary（value 组 2 证据）→ 升；agent_yua 1 条 diary（value 1）→ 不升。"""
    writer = InnerLifeWriter()
    store_dir = tmp_path / "elevation"
    for _ in range(2):
        event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        run_elevation(event, [], store_dir=store_dir)
    event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_yua")
    run_elevation(event, [], store_dir=store_dir)

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert len(elevated) == 1
    assert elevated[0].agent_id == "agent_rem"  # 只有 agent_rem 达标
    # agent_yua 的证据不许帮 agent_rem 凑数，也不许反向被 agent_rem 拉抬
    nodes = load_elevation_nodes(store_dir)
    yua_souls = [
        n for n in nodes if n["node_type"] in _SOUL_TYPES and n["agent_id"] == "agent_yua"
    ]
    assert yua_souls == []


# ── H. 独立证据语义：同一事件重复 consume 计 1，不凑数 ─────────────────


def test_h1_same_event_twice_no_elevate(tmp_path):
    """同一事件重复 consume（两次）→ 同 (source_id, event_identity) 计 1 独立证据 → 不升。"""
    writer = InnerLifeWriter()
    event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
    store_dir = tmp_path / "elevation"

    run_elevation(event, [], store_dir=store_dir)
    run_elevation(event, [], store_dir=store_dir)  # 同源重复 ingest

    elevated = elevate_matured_patterns(store_dir=store_dir)
    assert elevated == []  # 独立证据 1（同 source_id / 同 event）< 2
    assert _write_node_types(store_dir) == ["pattern", "pattern"]


# ── I. 端到端（Gate 路径）：diary/dream 进 elevation + elevate ────────


def test_i1_gate_submit_two_diary_events_elevates_with_agent(tmp_path):
    """Gate.submit(diary, agent_id=灵魂) 两次 → elevate 产 value，agent_id 归属正确（非 default）。"""
    from src.inner_life import NarrativeTraceWriter

    writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
    gate = SubmissionGate(writer=writer, store_dir=tmp_path / "elevation")

    for _ in range(2):
        event = _make_event(writer, TRIGGER_TYPE_DIARY_NIGHT, actor_id="agent_rem")
        nodes = gate.submit(event.event_id, agent_id=event.provenance.actor_id)
        assert len(nodes) == 1
        assert nodes[0].node_type == "pattern"  # Gate 只 consume
        assert nodes[0].agent_id == "agent_rem"  # diary/dream → 非 default

    elevated = elevate_matured_patterns(store_dir=tmp_path / "elevation")
    assert len(elevated) == 1
    assert elevated[0].node_type == "value"
    assert elevated[0].agent_id == "agent_rem"
    assert elevated[0].agent_id != "default"

    # Gate 路径下落盘节点同样归属（emergent 属性归属的验收面）
    node_records = load_elevation_nodes(tmp_path / "elevation")
    soul_records = [n for n in node_records if n["node_type"] in _SOUL_TYPES]
    assert len(soul_records) == 1
    assert soul_records[0]["agent_id"] == "agent_rem"


# ── J. 生产接线 + AST 红线 ───────────────────────────────────────────


def test_j1_submission_gate_source_never_calls_elevate():
    """AST 红线保持：submission_gate.py 源码仍不调用 elevate()（Gate 只 consume）。"""
    gate_src = (
        Path(__file__).resolve().parents[1]
        / "src" / "inner_life" / "submission_gate.py"
    )
    tree = ast.parse(gate_src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "elevate":
                raise AssertionError("SubmissionGate 不得调用 elevate()")
            if isinstance(func, ast.Name) and func.id == "elevate":
                raise AssertionError("SubmissionGate 不得调用 elevate()")


def test_j2_run_server_wires_elevate_check_after_every_submit():
    """run_server.py：4 处 submit 之后都有证据驱动 elevate 检查（3 Name + 1 Attribute）。"""
    src = Path(__file__).resolve().parents[1] / "scripts" / "run_server.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    name_calls = 0
    attr_calls = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "_elevate_check":
            name_calls += 1
        if isinstance(func, ast.Attribute) and func.attr == "_elevate_check":
            attr_calls += 1
    assert name_calls >= 3, "diary/dream/event 3 处 executor 都应调用 _elevate_check()"
    assert attr_calls >= 1, "world wrapper 应调用 self._elevate_check()"
