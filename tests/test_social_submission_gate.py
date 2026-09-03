"""
tests/test_social_submission_gate.py — SI-2.1 防线 3: Submission Gate 第 6 步

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §4.3)

验收项 (additive, 既有 5 步 0 改动):
  - 配置 agent_id → 第 6 步生效:
      * 他者事件 (actor_id != current_agent_id) → EXTERNAL_OTHER_ACTION,
        fail-closed 拒绝内化/升华 (verify 拒绝 + submit 返回 [])
      * 自己事件 (actor_id == current_agent_id) → 通过
      * 系统事件 (actor_id is None) → 维持现状 (通过)
  - 未配置 agent_id → 第 6 步跳过 (向后兼容, 既有行为不变)
  - 显式注入 IdentityFirewall
  - 既有 5 步验证链仍生效 (伪造 id 仍拒绝)
"""
from __future__ import annotations

from pathlib import Path

from src.inner_life import (
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    SubmissionGate,
    TRIGGER_TYPE_DIARY_NIGHT,
)
from src.social import EXTERNAL_OTHER_ACTION, IdentityFirewall


# ── helpers (对齐 test_submission_gate.py 风格) ─────────────


def _make_writer(tmp_path: Path) -> InnerLifeWriter:
    trace_path = tmp_path / "inner_life" / "trace.jsonl"
    return InnerLifeWriter(trace_writer=NarrativeTraceWriter(trace_log_path=trace_path))


def _make_gate(
    writer: InnerLifeWriter,
    tmp_path: Path,
    *,
    agent_id: str | None = None,
    identity_firewall=None,
) -> SubmissionGate:
    trace_path = tmp_path / "inner_life" / "trace.jsonl"
    reader = NarrativeTraceReader(trace_log_path=trace_path)
    # 防线 3 仅显式注入启用 (additive): agent_id 提供时显式构造 IdentityFirewall
    if identity_firewall is None and agent_id is not None:
        identity_firewall = IdentityFirewall(current_agent_id=agent_id)
    return SubmissionGate(
        writer=writer,
        trace_reader=reader,
        agent_id=agent_id,
        identity_firewall=identity_firewall,
    )


def _create_event(
    writer: InnerLifeWriter,
    *,
    actor_id: str | None = "agent_miku",
    trigger_type: str = TRIGGER_TYPE_DIARY_NIGHT,
):
    return writer.create_event(
        provenance=Provenance(
            trigger_type=trigger_type,
            actor_id=actor_id,
            source_system="diary",
        ),
    )


# ───────────────────────────────────────────────────────────
# 1. 第 6 步生效: 他者事件拒绝 (fail-closed)
# ───────────────────────────────────────────────────────────

def test_external_other_action_rejected(tmp_path):
    """actor_id != current_agent_id → EXTERNAL_OTHER_ACTION, 拒绝内化/升华。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_miku")  # 他者
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    verdict = gate.verify(event.event_id)
    assert not verdict.accepted
    assert EXTERNAL_OTHER_ACTION in verdict.reason
    assert "agent_miku" in verdict.reason
    assert "agent_ruka" in verdict.reason


def test_external_other_action_submit_returns_empty(tmp_path):
    """他者事件 submit → fail-closed 返回 [] (不 consume, 不产节点)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_miku")
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    nodes = gate.submit(event.event_id)
    assert nodes == []
    stats = gate.get_stats()
    assert stats["identity_firewall_rejected"] == 1


# ───────────────────────────────────────────────────────────
# 2. 第 6 步: 自己事件通过
# ───────────────────────────────────────────────────────────

def test_self_action_accepted(tmp_path):
    """actor_id == current_agent_id → 通过 (正常内化路径)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_ruka")  # 自己
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    verdict = gate.verify(event.event_id)
    assert verdict.accepted
    assert verdict.event is not None


# ───────────────────────────────────────────────────────────
# 3. 第 6 步: 系统事件维持现状
# ───────────────────────────────────────────────────────────

def test_system_action_accepted(tmp_path):
    """actor_id is None (系统事件) → 维持现状 (既有 world:* 路径不受影响)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id=None)
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    verdict = gate.verify(event.event_id)
    assert verdict.accepted


# ───────────────────────────────────────────────────────────
# 4. 向后兼容: 未配置 agent_id → 第 6 步跳过
# ───────────────────────────────────────────────────────────

def test_no_agent_id_skips_step6(tmp_path):
    """未配置 agent_id → 第 6 步跳过, 他者事件仍通过 (既有行为 0 变更)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_miku")
    gate = _make_gate(writer, tmp_path)  # 不传 agent_id

    verdict = gate.verify(event.event_id)
    assert verdict.accepted, "未配置防线 3 时既有行为应保持不变"


# ───────────────────────────────────────────────────────────
# 5. 显式注入 IdentityFirewall
# ───────────────────────────────────────────────────────────

def test_explicit_identity_firewall_injection(tmp_path):
    """显式注入 IdentityFirewall (current_agent_id 与 agent_id 可不同)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_yua")
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    gate = _make_gate(writer, tmp_path, identity_firewall=fw)

    verdict = gate.verify(event.event_id)
    assert not verdict.accepted
    assert EXTERNAL_OTHER_ACTION in verdict.reason


def test_explicit_firewall_self_passes(tmp_path):
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_ruka")
    fw = IdentityFirewall(current_agent_id="agent_ruka")
    gate = _make_gate(writer, tmp_path, identity_firewall=fw)

    verdict = gate.verify(event.event_id)
    assert verdict.accepted


# ───────────────────────────────────────────────────────────
# 6. 既有 5 步验证链仍生效 (回归)
# ───────────────────────────────────────────────────────────

def test_existing_5_steps_still_enforced(tmp_path):
    """既有 5 步验证链 0 改动: 伪造 id 仍拒绝 (即使配置了防线 3)。"""
    writer = _make_writer(tmp_path)
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    # 伪造 id (格式合法但非 writer 创建)
    forged = "f" * 32
    verdict = gate.verify(forged)
    assert not verdict.accepted
    assert "不是由 InnerLifeWriter 创建" in verdict.reason

    # 非法 producer
    event = _create_event(writer, actor_id="agent_ruka", trigger_type="hacker:exploit")
    verdict = gate.verify(event.event_id)
    assert not verdict.accepted
    assert "producer trigger_type" in verdict.reason


def test_self_action_submit_consumes(tmp_path):
    """自己事件 submit → 正常 consume 路径 (产 pattern 节点或至少不 fail-closed)。"""
    writer = _make_writer(tmp_path)
    event = _create_event(writer, actor_id="agent_ruka")
    gate = _make_gate(writer, tmp_path, agent_id="agent_ruka")

    nodes = gate.submit(event.event_id)
    # consume 可能因 soul-elevation 未安装返回 [] (fail-closed), 但不应是
    # 防线 3 拒绝 — 验证 stats 里 identity_firewall_rejected 为 0
    stats = gate.get_stats()
    assert stats["identity_firewall_rejected"] == 0
    assert stats["accepted"] == 1
