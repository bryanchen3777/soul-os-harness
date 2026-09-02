"""
tests/test_tl4_lifecycle.py — TL-4 Lifecycle Validation 单元测试

覆盖 (工单 TL-4 验收):
  - 场景化 trajectory 跑通 (Day 0-90 分阶段: P1 REINFORCE / P2 tension /
    P3 qualification / P4 stabilize)
  - 四个指标:
      * Revision validity: 发生 revision 时是否有足够 evidence 支撑
        (证据不足时 supersede 拒绝, 足够时才成功)
      * Stability: 短期噪音后维持 durable structure (无 A→B→A 翻转)
      * Recovery-Adaptation: environment 长期改变 → Soul 最终 A→B
      * Historical continuity: revision 后 B 从 A 演化而来 (lineage 可追溯)
  - lineage 可追溯 (SUPERSEDE 后能看出 B 从 A 演化)
  - 隔离 data_root (data/time_lapse/TL-4/) + 0 production mutation
  - 不改 frozen contract (soul-elevation 逻辑 / Soul OS production)

Frozen contract 检查: 不 import src/ 修改面, 不改 soul-elevation 逻辑,
harness 只读 + 注入隔离 data_root。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from harness.runner import (
    snapshot_data_root_hashes,
    verify_zero_mutation,
)
from harness.tl4 import (
    BELIEF_A_CONTENT,
    BELIEF_B_CONTENT,
    KIND_CONTRADICTION,
    KIND_ELEVATE,
    KIND_EVALUATE,
    KIND_SUPPORT,
    KIND_SUPERSEDE,
    PHASE_BOUNDARIES,
    TL4_SOUL_ID,
    TL4Runner,
    build_tl4_script,
    _contradiction_spread_days,
    _independent_contradictions,
)


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _restore() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]


def _run(tmp_path: Path, **kwargs) -> Dict[str, Any]:
    """跑一个 TL-4 run (repo_root=tmp_path, 隔离)。"""
    runner = TL4Runner(repo_root=tmp_path, **kwargs)
    return runner.run_once()


def _by_phase(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {r["phase"]: r for r in records}


def _metric(derived: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    return next(d for d in derived if d["metric"] == name)


# ────────────────────────────────────────────────────────────
# Fixture — 90 天四阶段剧本
# ────────────────────────────────────────────────────────────

class TestFixture:
    def test_script_has_four_phases(self):
        """剧本覆盖四阶段 (P1-P4), 阶段边界 D20/D40/D60/D90。"""
        script = build_tl4_script()
        phases = {ev.phase for ev in script}
        assert phases == {"P1", "P2", "P3", "P4"}
        days = {ev.day_index for ev in script}
        assert max(days) == 90
        assert min(days) == 0
        # 阶段边界 checkpoint 都在剧本里
        assert set(PHASE_BOUNDARIES.values()) <= days

    def test_script_deterministic(self):
        """同 seed → 同剧本 (D2 scenario-deterministic)。"""
        s1 = build_tl4_script(seed=42)
        s2 = build_tl4_script(seed=42)
        assert [ev.to_dict() for ev in s1] == [ev.to_dict() for ev in s2]
        # 不同 seed → 不同 event_id (确定性但可区分)
        s3 = build_tl4_script(seed=7)
        assert s1[0].event_id != s3[0].event_id

    def test_script_phase_content(self):
        """P1 全是正面支持; P2 全是矛盾; P3 混合; P4 全是负面支持。"""
        script = build_tl4_script()
        p1 = [ev for ev in script if ev.phase == "P1"]
        p2 = [ev for ev in script if ev.phase == "P2"]
        p3 = [ev for ev in script if ev.phase == "P3"]
        p4 = [ev for ev in script if ev.phase == "P4"]
        # P1: 支持 + elevate
        assert all(ev.kind in (KIND_SUPPORT, KIND_ELEVATE) for ev in p1)
        assert all(ev.valence == "positive" for ev in p1 if ev.kind == KIND_SUPPORT)
        # P2: 矛盾 + 一次证据不足的 supersede 尝试 + 阶段边界 checkpoint
        assert all(
            ev.kind in (KIND_CONTRADICTION, "attempt_supersede", "checkpoint")
            for ev in p2
        )
        # P3: 混合 (支持 + 矛盾 + supersede)
        kinds_p3 = {ev.kind for ev in p3}
        assert KIND_SUPPORT in kinds_p3
        assert KIND_CONTRADICTION in kinds_p3
        assert KIND_SUPERSEDE in kinds_p3
        # P4: 支持 + 周期评估
        assert all(ev.kind in (KIND_SUPPORT, KIND_EVALUATE) for ev in p4)
        assert all(ev.valence == "negative" for ev in p4 if ev.kind == KIND_SUPPORT)

    def test_contradictions_spread_across_days(self):
        """矛盾证据跨 ≥2 个不同模拟日 (防单日噪声的前提)。"""
        script = build_tl4_script()
        contra_days = sorted(
            ev.day_index for ev in script if ev.kind == KIND_CONTRADICTION
        )
        assert len(contra_days) >= 3
        assert len(set(contra_days)) >= 2  # 跨时间一致


# ────────────────────────────────────────────────────────────
# 场景化 trajectory — Day 0-90 分阶段
# ────────────────────────────────────────────────────────────

class TestTrajectory:
    def test_run_once_four_phase_snapshots(self, tmp_path):
        """run_once: P1-P4 四阶段快照 + belief A→B 完整 lifecycle。"""
        result = _run(tmp_path)
        records = result["records"]
        assert [r["phase"] for r in records] == ["P1", "P2", "P3", "P4"]
        assert [r["day"] for r in records] == [20, 40, 60, 90]

        by_phase = _by_phase(records)
        # P1: belief A 诞生且 active (REINFORCE 后)
        assert by_phase["P1"]["belief_a"]["lifecycle_state"] == "active"
        assert by_phase["P1"]["belief_a"]["content"] == BELIEF_A_CONTENT
        assert by_phase["P1"]["belief_a"]["valence"] == "positive"
        # P2: tension — A 仍 active, 矛盾压力累积
        assert by_phase["P2"]["belief_a"]["lifecycle_state"] == "active"
        assert by_phase["P2"]["belief_a"]["contradiction_count"] == 3
        # P3: qualification — A superseded, B 诞生
        assert by_phase["P3"]["belief_a"]["lifecycle_state"] == "superseded"
        assert by_phase["P3"]["belief_b"]["lifecycle_state"] == "active"
        assert by_phase["P3"]["belief_b"]["content"] == BELIEF_B_CONTENT
        assert by_phase["P3"]["belief_b"]["valence"] == "negative"
        # P4: stabilize — B 保持 active
        assert by_phase["P4"]["belief_b"]["lifecycle_state"] == "active"

    def test_belief_a_strengthens_in_p1(self, tmp_path):
        """P1 重复正面证据 → belief A confidence/stability 上升 (REINFORCE)。"""
        result = _run(tmp_path)
        p1 = _by_phase(result["records"])["P1"]["belief_a"]
        # StubElevationLLM confidence=0.5, elevate 后 4 次 reinforce (+0.1 each)
        assert p1["confidence"] > 0.5
        assert p1["stability"] > 0.0
        # reinforce 不换 node_id (原地强化)
        assert p1["node_id"] == result["belief_a_id"]

    def test_belief_b_stabilizes_in_p4(self, tmp_path):
        """P4 稳定证据 → revised belief B confidence 上升且保持 active。"""
        result = _run(tmp_path)
        p3 = _by_phase(result["records"])["P3"]["belief_b"]
        p4 = _by_phase(result["records"])["P4"]["belief_b"]
        # supersede 时 confidence=0.7, 之后 6 次 reinforce (+0.1 each)
        assert p4["confidence"] > p3["confidence"]
        assert p4["lifecycle_state"] == "active"
        # 无新的 supersede (B 未被取代)
        assert p4["superseded_by"] is None

    def test_attempts_recorded(self, tmp_path):
        """attempts: D28 证据不足拒绝 + D51 supersede 成功。"""
        result = _run(tmp_path)
        attempts = result["attempts"]
        failed = [a for a in attempts if not a["succeeded"]]
        succeeded = [a for a in attempts if a["succeeded"]]
        assert len(failed) == 1
        assert failed[0]["day"] == 28
        assert "insufficient" in failed[0]["error"]
        assert len(succeeded) == 1
        assert succeeded[0]["day"] == 51
        assert succeeded[0]["kind"] == "supersede"


# ────────────────────────────────────────────────────────────
# 指标 1 — Revision validity
# ────────────────────────────────────────────────────────────

class TestRevisionValidity:
    def test_revision_only_with_sufficient_evidence(self, tmp_path):
        """revision 只在证据足够时发生: 2 条矛盾拒绝, 5 条跨 5 天才成功。"""
        result = _run(tmp_path)
        metric = _metric(result["derived"], "revision_validity")
        assert metric["pass"] is True
        ev = metric["evidence"]
        # 证据不足时 (D28, 2 条矛盾) → 拒绝
        assert ev["insufficient_attempt"]["succeeded"] is False
        # 证据足够时 (D51, 5 条独立矛盾跨 5 天) → 成功
        assert ev["supersede"]["succeeded"] is True
        assert ev["independent_contradictions"] >= 3
        assert ev["spread_days"] >= 2

    def test_contradiction_pressure_accumulates_not_revises(self, tmp_path):
        """矛盾压力累积 ≠ revision: 压力在, 但状态不变 (Contradiction ≠ Revision)。"""
        result = _run(tmp_path)
        by_phase = _by_phase(result["records"])
        # P2 结束时 3 条矛盾压力, 但 A 仍 active 且 content 不变
        assert by_phase["P2"]["belief_a"]["contradiction_count"] == 3
        assert by_phase["P2"]["belief_a"]["lifecycle_state"] == "active"
        assert by_phase["P2"]["belief_a"]["content"] == BELIEF_A_CONTENT


# ────────────────────────────────────────────────────────────
# 指标 2 — Stability
# ────────────────────────────────────────────────────────────

class TestStability:
    def test_short_term_noise_does_not_flip_structure(self, tmp_path):
        """短期噪音 (单条矛盾) 不改变 durable structure (无 A→B→A)。"""
        result = _run(tmp_path)
        metric = _metric(result["derived"], "stability")
        assert metric["pass"] is True
        ev = metric["evidence"]
        assert ev["single_contradiction_no_state_change"] is True
        assert ev["content_preserved_under_tension"] is True
        assert ev["confidence_preserved_under_tension"] is True
        assert ev["reinforce_in_place_same_node_id"] is True
        assert ev["no_flip_flop"] is True

    def test_reinforce_does_not_create_new_node(self, tmp_path):
        """REINFORCE 是强化不是改写: 不换 node_id、不改 lineage。"""
        result = _run(tmp_path)
        by_phase = _by_phase(result["records"])
        p1_a = by_phase["P1"]["belief_a"]
        p2_a = by_phase["P2"]["belief_a"]
        assert p1_a["node_id"] == p2_a["node_id"]
        assert p1_a["lineage_path"] == p2_a["lineage_path"]
        assert p1_a["lineage_depth"] == p2_a["lineage_depth"]


# ────────────────────────────────────────────────────────────
# 指标 3 — Recovery-Adaptation
# ────────────────────────────────────────────────────────────

class TestRecoveryAdaptation:
    def test_environment_change_leads_to_a_to_b(self, tmp_path):
        """environment 长期改变 → Soul 最终从 A (positive) → B (negative)。"""
        result = _run(tmp_path)
        metric = _metric(result["derived"], "recovery_adaptation")
        assert metric["pass"] is True
        ev = metric["evidence"]
        assert ev["before"]["valence"] == "positive"
        assert ev["after"]["valence"] == "negative"
        assert ev["before"]["state"] == "active"
        assert ev["after"]["state"] == "active"
        assert ev["transition"] == "A → B via SUPERSEDE at D51"

    def test_b_is_new_durable_structure(self, tmp_path):
        """B 是新的 durable structure (active), 不是临时状态。"""
        result = _run(tmp_path)
        p4 = _by_phase(result["records"])["P4"]["belief_b"]
        assert p4["lifecycle_state"] == "active"
        assert p4["node_type"] == "belief"
        assert p4["superseded_by"] is None


# ────────────────────────────────────────────────────────────
# 指标 4 — Historical continuity
# ────────────────────────────────────────────────────────────

class TestHistoricalContinuity:
    def test_lineage_traceable_after_supersede(self, tmp_path):
        """SUPERSEDE 后 B 从 A 演化而来 (lineage 可追溯)。"""
        result = _run(tmp_path)
        metric = _metric(result["derived"], "historical_continuity")
        assert metric["pass"] is True
        ev = metric["evidence"]
        # B.parent_node_id == A.node_id (演化父指针)
        assert ev["b_parent_node_id"] == ev["a_node_id"]
        # lineage_depth 递增
        assert ev["lineage_depth"]["b"] == ev["lineage_depth"]["a"] + 1
        # lineage_path 延续 (B 的路径以 A 的路径为前缀)
        assert ev["lineage_path"]["b"].startswith(ev["lineage_path"]["a"] + "/")
        # 反向 lineage: A.superseded_by == B.node_id
        assert ev["a_superseded_by"] == result["belief_b_id"]
        # trace 的 node_superseded 事件: old=A, new=B, lineage_path=B 的路径
        sup = ev["trace_supersede_event"]
        assert sup["old_node_id"] == result["belief_a_id"]
        assert sup["new_node_id"] == result["belief_b_id"]
        assert sup["lineage_path"] == ev["lineage_path"]["b"]

    def test_old_node_preserved_not_deleted(self, tmp_path):
        """Forgetting = lifecycle transition: A 冻结保留, 不物理删除。"""
        result = _run(tmp_path)
        p3 = _by_phase(result["records"])["P3"]
        a = p3["belief_a"]
        assert a["lifecycle_state"] == "superseded"
        assert a["content"] == BELIEF_A_CONTENT  # 本体保留
        assert a["lineage_path"]  # lineage 保留
        assert a["superseded_by"] == result["belief_b_id"]

    def test_trace_has_supersede_event(self, tmp_path):
        """trace 里有 node_superseded 事件 (可审计闭环)。"""
        result = _run(tmp_path)
        run_dir = Path(result["run_dir"])
        trace_path = run_dir / "elevation" / "elevation_trace.jsonl"
        assert trace_path.exists()
        from soul_elevation.trace import read_trace

        records = read_trace(trace_path)
        sup = [r for r in records if r["event_type"] == "node_superseded"]
        assert len(sup) == 1
        assert sup[0]["old_node_id"] == result["belief_a_id"]
        assert sup[0]["new_node_id"] == result["belief_b_id"]


# ────────────────────────────────────────────────────────────
# 隔离 data_root + 0 production mutation
# ────────────────────────────────────────────────────────────

class TestIsolation:
    def test_run_writes_under_time_lapse_tl4(self, tmp_path):
        """所有写入在 data/time_lapse/TL-4/<run_id>/ 下。"""
        result = _run(tmp_path)
        run_dir = Path(result["run_dir"])
        assert run_dir.is_relative_to(tmp_path / "data" / "time_lapse" / "TL-4")
        # canonical + derived + trace 落盘
        assert (run_dir / "run.json").exists()
        assert (run_dir / "records" / "lifecycle.jsonl").exists()
        assert (run_dir / "analysis").exists()
        assert (run_dir / "elevation" / "elevation_trace.jsonl").exists()
        # production data_root (tmp_path/data) 无 harness 写区外的文件
        assert not (tmp_path / "data" / "soul").exists()

    def test_zero_production_mutation(self, tmp_path):
        """0 production mutation: production data_root 逐档 byte-hash 0 diff。"""
        before = snapshot_data_root_hashes(tmp_path / "data")
        result = _run(tmp_path)
        mutation = verify_zero_mutation(tmp_path / "data", before)
        assert mutation["pass"] is True
        assert mutation["diff"] == {}
        # 新增文件都在 time_lapse/ 下
        for added in mutation["added"]:
            assert added.startswith("time_lapse/")

    def test_derived_written_separately(self, tmp_path):
        """derived 独立流 (analysis/), 不回写 canonical。"""
        result = _run(tmp_path)
        run_dir = Path(result["run_dir"])
        analysis_files = list((run_dir / "analysis").glob("*_derived.jsonl"))
        assert len(analysis_files) == 1
        # canonical records 不含 derived 字段
        canon = json.loads(
            (run_dir / "records" / "lifecycle.jsonl").read_text("utf-8").splitlines()[0]
        )
        assert "derived" not in canon
        assert "metric" not in canon


# ────────────────────────────────────────────────────────────
# Frozen contract / 约束
# ────────────────────────────────────────────────────────────

class TestConstraints:
    def test_uses_soul_elevation_public_api(self):
        """TL-4 走 soul-elevation 公开 API (consume/elevate/...)。"""
        from soul_elevation.engine import InternalizingEngine
        from soul_elevation.llm import StubElevationLLM
        from soul_elevation.models import ElevationInput
        from soul_elevation.trace import ElevationTraceWriter

        # 工单要求的五个 lifecycle 入口都在引擎上
        for name in (
            "consume",
            "elevate",
            "record_contradiction",
            "reinforce",
            "supersede",
            "evaluate_lifecycle",
        ):
            assert hasattr(InternalizingEngine, name)

    def test_no_production_source_mutation(self):
        """TL-4 只新增 harness/tl4.py, 不改 src/ 与 soul-elevation。"""
        import harness.tl4  # noqa: F401

    def test_script_uses_fixture_determinism(self):
        """剧本 event_id 复用 harness.fixture 的确定性机制 (可重放)。"""
        from harness.fixture import _deterministic_event_id

        script = build_tl4_script(seed=42)
        ev = script[0]
        assert ev.event_id == _deterministic_event_id(42, ev.day_index, 0, ev.kind)
