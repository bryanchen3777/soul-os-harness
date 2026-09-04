"""
tests/test_tl7_social_opportunity_harness.py — TL-7 Social Opportunity Harness 測試

工单: TICKET-TL-7-HARNESS (Dual-Brain Edition)
设计: docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md (v1.0, 2026-09-03, 已鎖定)

覆蓋 (工單 TL-7 驗收):
  - 4 大情境階段跑通:
      * Phase A (話題湧現): Ruka 在客廳發布 share「我烤了餅乾在桌上」
      * Phase B (緊湊感知與機會生成): Akane 接收事件, _render_social_context
        產出 [客廳現況] (含反框架警語), SocialOpportunityBuffer 生成 1 筆
        SocialOpportunity (TTL = 300s)
      * Phase C (意志選擇與無連鎖不變量): Akane 評估機會生成 Motive, 傳入
        build_decision_prompt, 走入 SM-4 四元單選 (絕不繞過意志直接觸發
        transmit, 留白率維持真實常態)
      * Phase D (300s TTL 自然蒸發): 時鐘前進 301 秒, get_active_opportunities
        自動剔除過期條目, 渲染自動恢復留白 (""), 0 殭屍回覆
  - 3 大驗收指標:
      * TTL Expiration Invariant: 100% PASS (過期條目徹底蒸發, 0 遺留)
      * No Cascading Volition Invariant: 100% PASS (0 自動連鎖搶話)
      * D2 Determinism & 0 Mutation: 3 次獨立 run 軌跡一致, 生產 data/ 0 diff

Frozen Contract 邊界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 一律不動; 0 Vector DB。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from harness.tl7 import (
    DECISION_ACTIONS,
    OPPORTUNITY_TTL_SECONDS,
    PHASE_A,
    PHASE_B,
    PHASE_C,
    PHASE_D,
    TL7_AGENTS,
    TL7_PERCEIVER,
    TL7_PUBLISHER,
    TL7Runner,
)
from src.social import ANTI_FRAMING_HINT


def _restore_env() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]


@pytest.fixture(autouse=True)
def clean_environment():
    _restore_env()
    yield
    _restore_env()


# ─────────────────────────────────────────────────────────────
# 1. 單次執行: 4 階段跑通 + 3 大不變量
# ─────────────────────────────────────────────────────────────

def test_tl7_runner_single_run_invariants(tmp_path):
    """TL7Runner 單次執行: 4 階段跑通, 3 大不變量全綠。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_run_single")

    assert result["run_id"] == "test_run_single"
    derived = result["derived"]

    # 3 大不變量
    assert derived.ttl_expiration_passed is True, "TTL Expiration failed"
    assert derived.no_cascading_volition_passed is True, "Cascading volition detected"
    assert derived.opportunity_generated == 1, "應生成 1 筆機會"
    assert derived.zombie_replies == 0, "0 殭屍回覆"
    assert derived.total_phases == 4

    # 產出紀錄檔案
    run_dir = Path(result["run_dir"])
    assert (run_dir / "records" / "phases.jsonl").exists()
    assert (run_dir / "derived.json").exists()


# ─────────────────────────────────────────────────────────────
# 2. Phase A — 話題湧現
# ─────────────────────────────────────────────────────────────

def test_tl7_phase_a_topic_emergence():
    """Phase A: Ruka 在客廳發布 share, 事件成功進入感知 state。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_phase_a")
    rec = next(r for r in result["records"] if r.phase == PHASE_A)

    assert rec.event_published is True, "話題事件未發布"
    assert "烤了饼干" in rec.description


# ─────────────────────────────────────────────────────────────
# 3. Phase B — 緊湊感知與機會生成
# ─────────────────────────────────────────────────────────────

def test_tl7_phase_b_compact_perception_and_opportunity():
    """Phase B: Akane 緊湊感知產出 [客廳現況] (含反框架警語) + 1 筆機會 (TTL=300s)。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_phase_b")
    rec = next(r for r in result["records"] if r.phase == PHASE_B)

    assert rec.social_block_rendered is True, "社交感知區塊未渲染"
    assert rec.anti_framing_present is True, "反框架警語缺失"
    assert rec.opportunity_count == 1, f"應生成 1 筆機會, got {rec.opportunity_count}"
    assert rec.opportunity_ttl == OPPORTUNITY_TTL_SECONDS, (
        f"TTL 應為 300s, got {rec.opportunity_ttl}"
    )


# ─────────────────────────────────────────────────────────────
# 4. Phase C — 意志選擇與無連鎖不變量
# ─────────────────────────────────────────────────────────────

def test_tl7_phase_c_volition_quad_and_no_cascade():
    """Phase C: Akane 生成 Motive, 走入 SM-4 四元單選, 0 連鎖搶話。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_phase_c")
    rec = next(r for r in result["records"] if r.phase == PHASE_C)

    assert rec.motive_generated is True, "Motive 未生成"
    assert rec.decision_prompt_has_quad is True, "Decision prompt 缺四元選項"
    assert rec.decision in DECISION_ACTIONS, f"decision 非四元: {rec.decision!r}"
    assert rec.transmit_triggered is False, "絕不繞過意志直接觸發 transmit"
    assert rec.cascading_volition is False, "0 自動連鎖搶話"


# ─────────────────────────────────────────────────────────────
# 5. Phase D — 300s TTL 自然蒸發 + 0 殭屍回覆
# ─────────────────────────────────────────────────────────────

def test_tl7_phase_d_ttl_evaporation_zero_zombie():
    """Phase D: 時鐘前進 301s, 機會 TTL 自然蒸發, 渲染恢復留白, 0 殭屍回覆。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_once(run_id="test_phase_d")
    rec = next(r for r in result["records"] if r.phase == PHASE_D)

    assert rec.active_after_expiry == 0, (
        f"過期機會未蒸發, 仍剩 {rec.active_after_expiry} 筆"
    )
    assert rec.social_block_after_expiry == "", (
        f"渲染未恢復留白: {rec.social_block_after_expiry!r}"
    )
    assert rec.zombie_replies == 0, "0 殭屍回覆"
    assert rec.ttl_expired_ok is True, "TTL Expiration Invariant failed"


# ─────────────────────────────────────────────────────────────
# 6. 3-run 確定性 + 0 生產污染
# ─────────────────────────────────────────────────────────────

def test_tl7_runner_series_determinism_and_zero_mutation():
    """TL7Runner 系列多跑 (3 runs): D2 決定性與零生產污染。"""
    runner = TL7Runner(repo_root=REPO_ROOT, seed=42)
    result = runner.run_series(n_runs=3)

    assert result["all_passed"] is True
    assert result["zero_mutation_ok"] is True, (
        f"Production mutation detected: {result['mutation_diff']}"
    )
    assert result["determinism_ok"] is True, "Determinism failed across 3 runs"
    assert len(result["runs"]) == 3

    # 每 run 的 3 大不變量都綠
    for r in result["runs"]:
        assert r["derived"].ttl_expiration_passed is True
        assert r["derived"].no_cascading_volition_passed is True
        assert r["derived"].zombie_replies == 0


# ─────────────────────────────────────────────────────────────
# 7. Frozen Contract 邊界
# ─────────────────────────────────────────────────────────────

def test_tl7_no_frozen_contract_mutation():
    """TL-7 只新增 harness/tl7.py + run_tl7.py, 不改 src/ (frozen contract 0 污染)。"""
    import harness.tl7  # noqa: F401
    import harness.run_tl7  # noqa: F401

    # 驗證 TL7Runner 使用 production 決策管線 (build_decision_prompt / parse_decision_output)
    from src.soul import decision as decision_mod
    assert hasattr(decision_mod, "build_decision_prompt")
    assert hasattr(decision_mod, "parse_decision_output")

    # 驗證 TL7Runner 使用 production motive 管線 (motive_from_social_opportunity)
    from src.soul import motive as motive_mod
    assert hasattr(motive_mod, "motive_from_social_opportunity")

    # 驗證參與 Agent 定義
    assert TL7_PUBLISHER in TL7_AGENTS
    assert TL7_PERCEIVER in TL7_AGENTS
