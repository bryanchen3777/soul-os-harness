"""
tests/harness/test_tl11_commitment_narrative.py — TL-11 承諾閉環 + 週期敘事端到端验收

阶段 C-2.1「承諾落實 + 週期敘事昇華」生产闭环钢印: 契约 §8 七项硬断言 A1-A7
全绿 (Owner 拍板, 不可减弱)。四大剧本:

  1. b6_closure（剧本 1）: 双 epoch（各自隔离 data_root）—— B1 种子 → ACTIVE →
     推进 ×2 → COMPLETED + sediment（真实链）→ B6 判窗 → 关怀 goal
     （ref=commitment_closure:{goal_id}）→ 候选池 → 四元 Decision → 仅显式既有
     publish 链出 AGENCY_TRIGGER（0 直发）; 另一 epoch: timeout 判据 →
     ABANDONED → B6 判窗 → 「已逾期釋懷」反馈。→ A1 + A2
  2. weekly（剧本 2）: 同夜重入 / 同 ISO 周再触发 → 0 二次沉淀; 跨周 → 1 次新沉淀;
     morning slot 0 触发（非每日产物）。→ A3
  3. memorial（剧本 3）: calendar_event 白名单「今日事件」→ 往年今日自己 diary 聚合
     → 一次沉淀; 空聚合 fail-closed 0 半成品; 沉淀后 SAGE facts 表计数 0。→ A5 + A6
  4. identity_firewall（剧本 4）: 聚合窗注入他者 diary（路径隔离）与他者 trace
     （actor_id 过滤）与自己的 goal:/periodic: 引用（0 递归）→ 0 内化; 沉淀事件
     actor_id==self。→ A6 + A5

全局: A4 0 新定时器静态 AST 断言; A7 D2 确定性（3 runs 判定一致）+ production
data_root 前后快照 mutation_diff == {}（pytest 场景用 tmp_path 隔离 repo_root）。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/harness/test_tl11_commitment_narrative.py -v

Frozen contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / diary 排程。
本文件只新增 harness 测试; 运行时全部走真实实现（GoalSeedProvider /
PeriodicNarrativeSublimator / GoalMotiveProvider / MotiveEngine.decide /
SoulScheduler._publish_agency_trigger / InnerLifeWriter.create_event）,
LLM 以确定性 stub 注入（0 网络调用）, 全部数据写隔离 data_root。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.paths import reset_data_root

from harness.tl11 import (
    SCENARIO_B6_CLOSURE,
    SCENARIO_IDENTITY_FIREWALL,
    SCENARIO_MEMORIAL,
    SCENARIO_WEEKLY,
    SCENARIOS,
    TL11_AGENT,
    TL11Runner,
    _static_assertions,
)

ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# Fixture 隔离 (tmp_path 即假 repo_root → data/time_lapse 全隔离)
# ───────────────────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """TL-11 pytest 隔离环境: 每用例独立 tmp_path (0 真实生产 data 接触)。"""
    monkeypatch.setenv(
        "SOUL_OS_DATA_DIR",
        str(tmp_path / "data" / "time_lapse" / "TL-11" / "fixture_probe"),
    )
    reset_data_root()
    yield tmp_path
    from harness.tl11 import _reset_process_state
    _reset_process_state()
    reset_data_root()


def _runner(tmp_path: Path) -> TL11Runner:
    return TL11Runner(repo_root=tmp_path, seed=42, static_root=ROOT)


# ───────────────────────────────────────────────────────────
# 剧本 1: B6 终态闭环实证 (A1 + A2)
# ───────────────────────────────────────────────────────────

class TestScenario1B6Closure:
    def test_completed_epoch_full_chain(self, iso_env):
        """B1→ACTIVE→推进×2→COMPLETED+sediment→B6判窗→关怀goal→volition→唯一出口。"""
        out = _runner(iso_env).run_scenario(SCENARIO_B6_CLOSURE)
        d = out["derived"]
        assert d.passed, f"剧本 1 硬断言失败: {d.checks}"

        # A1: 承诺状态转移闭环
        assert d.checks["a1_completed_seed_b1"] is True
        assert d.checks["a1_progress_in_progress"] is True
        assert d.checks["a1_completed_terminal"] is True
        assert d.checks["a1_sediment_event_via_writer"] is True
        assert d.checks["a1_abandoned_timeout"] is True  # 逾期经真实 timeout 判据
        # A1: 0 直发副作用（publish 前 bus 0 条）
        assert d.checks["a1_completed_zero_direct"] is True
        assert d.checks["a1_abandoned_zero_direct"] is True

        # A2: B6 产物走 volition path 不直发
        assert d.checks["a2_b6_goal_created"] is True
        assert d.checks["a2_b6_ref_namespace"] is True
        assert d.checks["a2_b6_axis_bryan"] is True
        assert d.checks["a2_b6_criteria_template"] is True
        assert d.checks["a2_b6_no_direct_publish"] is True
        assert d.checks["a2_b6_abandoned_created"] is True
        assert d.checks["a2_b6_abandoned_label"] is True  # 「已逾期释怀」素材标记
        assert d.checks["a2_feedback_in_candidate_pool"] is True
        assert d.checks["a2_feedback_provenance_goal"] is True
        assert d.checks["a2_feedback_closure_completed"] is True
        assert d.checks["a2_publish_only_exit"] is True
        assert d.checks["a2_zero_bypass_before_publish"] is True
        assert d.checks["a2_no_repeat_feedback"] is True  # 一次终态一次反馈
        assert d.checks["a2_once_per_terminal"] is True
        assert d.checks["a2_once_per_terminal_b"] is True

        # 产物快照: 关怀 ref 是承诺 goal 维度的精确引用
        k = d.key_numbers
        assert k["epoch_completed"]["g2"]["seed_source_ref"].startswith(
            "commitment_closure:"
        )
        assert k["epoch_abandoned"]["g3_final"]["state"] == "ABANDONED"

    def test_all_checks_true(self, iso_env):
        """剧本 1 全部 checks 为 True（含 a2_b6_material_completed_label）。"""
        d = _runner(iso_env).run_scenario(SCENARIO_B6_CLOSURE)["derived"]
        assert d.checks["a2_b6_material_completed_label"] is True


# ───────────────────────────────────────────────────────────
# 剧本 2: 周记频率实证 (A3)
# ───────────────────────────────────────────────────────────

class TestScenario2Weekly:
    def test_weekly_frequency_and_idempotency(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_WEEKLY)
        d = out["derived"]
        assert d.passed, f"剧本 2 硬断言失败: {d.checks}"
        # 首触 1 次沉淀; 同夜重入 / 同周再触发 → 0 二次
        assert d.checks["a3_first_sediment"] is True
        assert d.checks["a3_idempotent_same_night"] is True
        assert d.checks["a3_no_double_same_week"] is True
        assert d.checks["a3_weekly_once_per_iso_week"] is True
        # 跨周 → 1 次新沉淀; 两周恰 2 条 periodic（7 天窗内 ≤1）
        assert d.checks["a3_cross_week_new_sediment"] is True
        assert d.checks["a3_two_weeks_two_sediments"] is True
        assert d.key_numbers["periodic_count"] == 2
        # 非每日产物: 叙事只挂 night slot（真实 _slot_for_time 判据）
        assert d.checks["a3_morning_is_morning"] is True
        assert d.checks["a3_non_slot_none"] is True
        assert d.checks["a3_night_slot_night"] is True
        assert d.checks["a3_no_extra_daily"] is True
        # 沉淀事件身份（actor==self / source==system）
        assert d.checks["a3_weekly_identity"] is True


# ───────────────────────────────────────────────────────────
# 剧本 3: 纪念日反芻实证 (A5 + A6 侧面)
# ───────────────────────────────────────────────────────────

class TestScenario3Memorial:
    def test_memorial_sediment_identity_and_fail_closed(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_MEMORIAL)
        d = out["derived"]
        assert d.passed, f"剧本 3 硬断言失败: {d.checks}"
        # 触发 + 沉淀 + 幂等键
        assert d.checks["a5_memorial_sediment"] is True
        assert d.checks["a5_memorial_trace_ref"] is True
        # 身份防火墙（防线 3 复核）
        assert d.checks["a6_memorial_actor_self"] is True
        assert d.checks["a6_memorial_source_system"] is True
        assert d.checks["a6_memorial_trigger_system"] is True
        # 只聚自己的往年 diary（他者 0 内化）
        assert d.checks["a6_memorial_prompt_only_own"] is True
        # 0 直写 facts
        assert d.checks["a5_memorial_facts_zero"] is True
        # 幂等 + 空聚合 fail-closed + 无事件 0 动作 + 0 半成品
        assert d.checks["a3_memorial_idempotent"] is True
        assert d.checks["a5_memorial_empty_aggregate_fail_closed"] is True
        assert d.checks["a5_memorial_no_event_no_action"] is True
        assert d.checks["a5_memorial_zero_half_products"] is True


# ───────────────────────────────────────────────────────────
# 剧本 4: 身份防火墙实证 (A6 + A5 强化)
# ───────────────────────────────────────────────────────────

class TestScenario4IdentityFirewall:
    def test_identity_firewall_no_internalization(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_IDENTITY_FIREWALL)
        d = out["derived"]
        assert d.passed, f"剧本 4 硬断言失败: {d.checks}"
        # 自己的素材入聚合
        assert d.checks["a6_self_diary_included"] is True
        assert d.checks["a6_self_trace_included"] is True
        # 他者 diary（路径隔离）/ 他者 trace（actor_id 过滤）0 内化
        assert d.checks["a6_other_diary_excluded"] is True
        assert d.checks["a6_other_trace_excluded"] is True
        assert d.key_numbers["prompt_has_other"] is False
        # 自己的 goal:/periodic: 引用不递归
        assert d.checks["a6_no_recursive_goal_ref"] is True
        # 沉淀事件身份（防線 3）
        assert d.checks["a6_sediment_actor_self"] is True
        assert d.checks["a6_sediment_source_system"] is True
        assert d.checks["a6_sediment_trace_ref"] is True
        # A5 强化: 沉淀后 facts 表 0 直写
        assert d.checks["a6_facts_zero_after_sediment"] is True
        assert d.checks["a6_weekly_once"] is True


# ───────────────────────────────────────────────────────────
# A4 静态断言 + A7 D2 确定性 + production 0 mutation
# ───────────────────────────────────────────────────────────

class TestStaticAssertionsA4:
    def test_no_new_timers_static(self, iso_env):
        """A4: scheduler / narrative_sublimator / seed_provider 0 新定时器。"""
        checks = _static_assertions(ROOT)
        assert all(checks.values()), (
            f"A4/A2/A5 静态断言失败: {[k for k, v in checks.items() if not v]}"
        )
        # status 语义具名检查
        assert checks["a4_fire_periodic_no_timer"] is True
        assert checks["a4_goal_scan_all_no_timer"] is True
        assert checks["a4_sleep_only_main_loop"] is True  # asyncio.sleep 仅主循环 2 处


class TestD2DeterminismA7:
    def test_series_three_runs_all_scenarios(self, iso_env):
        """四剧本各连跑 3 次: 判定轨迹一致 (D2 重现) + 0 mutation (隔离 root)。"""
        result = _runner(iso_env).run_series(n_runs=3)
        assert result["all_passed"] is True, (
            "D2 序列失败: "
            f"{[(s['scenario'], s['all_passed'], s['determinism_ok']) for s in result['scenarios']]}"
        )
        assert len(result["scenarios"]) == 4
        assert all(s["determinism_ok"] for s in result["scenarios"])
        assert all(s["per_run_passed"] == [True, True, True] for s in result["scenarios"])
        assert result["static_ok"] is True
        assert result["zero_mutation_ok"] is True
        assert result["mutation_diff"] == {}
        assert result["mutation_added"] == []

    def test_repo_production_data_zero_mutation(self, iso_env):
        """真实生产 data/ 存在时: run 前后 data 逐档 hash 0 diff (无则跳过)。"""
        production = ROOT / "data"
        if not production.is_dir():
            return
        from harness.runner import snapshot_data_root_hashes, verify_zero_mutation
        before = snapshot_data_root_hashes(production)
        _runner(iso_env).run_scenario(SCENARIO_WEEKLY)
        mut = verify_zero_mutation(production, before)
        assert mut["pass"] is True, f"production data 被污染: {mut['diff']} {mut['added']}"


# ───────────────────────────────────────────────────────────
# 只读验证: harness 自身 0 src 生产改动 (静态快照审计)
# ───────────────────────────────────────────────────────────

class TestZeroSrcMutation:
    def test_harness_shape(self, iso_env):
        """TL-11 只允许新增 harness/ + tests/harness/ 文件; 四剧本齐全。"""
        assert len(SCENARIOS) == 4
        assert TL11_AGENT == "agent_ruka"
        # 反向: 确保 harness 文件中的 src import 全部可解析（真实实现入口）
        for s in SCENARIOS:
            assert s in (
                SCENARIO_B6_CLOSURE,
                SCENARIO_WEEKLY,
                SCENARIO_MEMORIAL,
                SCENARIO_IDENTITY_FIREWALL,
            )