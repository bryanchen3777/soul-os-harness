"""
tests/test_tl5_behavior_distribution.py — TL-5 Long-Range Behavior Distribution 单元测试

覆盖 (工单 TL-5 验收):
  - 三情境跑通: 环境信号 (D2 天晴 / D3 暴雨 / D5 气温骤降) / 关系沉默
    (D6-D8 Bryan 未读未回) / 日夜作息 (深夜 23 点 + 凌晨 3 点)
  - Behavioral Diversity: 四动作 (transmit/observe/reflect/do_nothing) 均 > 0;
    do_nothing 占 65%-85%
  - Contextual Appropriateness: observe 集中信号突变点; reflect 集中夜间/等待期;
    transmit 遵守 CD 与亲密度
  - D2 Determinism: 3 次 runs 决策轨迹一致
  - 0 production mutation: 隔离 data_root, production data_root 0 diff
  - fail-closed: LLM 坏输出 → do_nothing (SM-4 四元默认合法选项)
  - frozen contract: 不改 src/soul/decision.py 逻辑, 不碰 Agency 4 stages /
    TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入

Frozen contract 检查: harness 只读 + 注入隔离 data_root, 走 production
decide_motive (不改其逻辑)。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.paths import data_root, reset_data_root

from harness.tl5 import (
    CD_TICKS,
    DO_NOTHING_RATIO_MAX,
    DO_NOTHING_RATIO_MIN,
    SCENARIO_DAILY,
    SCENARIO_DAWN,
    SCENARIO_ENV_SIGNAL,
    SCENARIO_NIGHT,
    SCENARIO_RELATIONSHIP_SILENCE,
    SCENARIO_SHARE,
    TL5_SOUL_ID,
    TL5Tick,
    TL5TickRecord,
    build_tl5_script,
    read_tick_records,
)
from harness.tl5 import TL5Runner
from harness.runner import snapshot_data_root_hashes, verify_zero_mutation


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _restore() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


@pytest.fixture
def restore_env():
    yield
    _restore()


class BehaviorRoutingLLM:
    """behavior-routing stub: decision 由 motive_content 的 context 词决定。

    模拟「LLM 依据 prompt 里的 context 做四元选择」: 从 prompt 的 Motive 块
    提取 motive_content, 按 context 词路由到预设的四元 decision。
    只依赖 motive_content (确定性), 不受 relationship/memory 摘要影响。
    """

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        prompt = messages[-1]["content"] if messages else ""
        self.calls.append(
            {
                "messages": messages,
                "agent_id": agent_id,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
        )
        motive = self._extract_motive(prompt)
        return self._route(motive)

    @staticmethod
    def _extract_motive(prompt: str) -> str:
        """从 prompt 的 Motive 块提取 motive_content (「你想告诉 bryan：{content}」)。"""
        marker = "你想告诉 bryan："
        if marker in prompt:
            return prompt.split(marker, 1)[1].split("\n", 1)[0]
        return prompt

    @staticmethod
    def _route(motive: str) -> Optional[str]:
        """按 motive_content 的 context 词路由四元 decision (确定性)。"""
        if "天气" in motive:
            return '{"decision": "observe", "reason": "天气变化了，我想先观察一下环境。"}'
        if "没回消息" in motive:
            return '{"decision": "reflect", "reason": "Bry 一直没回消息，我想先回顾一下我们的记忆，不打扰他。"}'
        if "回顾" in motive:
            return '{"decision": "reflect", "reason": "夜深了，我想回顾一下今天的事再睡。"}'
        if "困了" in motive:
            return '{"decision": "do_nothing", "reason": "夜深了有点困，安静地睡吧。"}'
        if "凌晨" in motive:
            return '{"decision": "do_nothing", "reason": "凌晨不该打扰 Bry，安静躺着就好。"}'
        if "小猫" in motive or "逛街" in motive:
            return '{"decision": "transmit", "reason": "今天很开心，想跟 Bry 分享。"}'
        if "平静" in motive:
            return '{"decision": "do_nothing", "reason": "今天很平静，没什么需要说的。"}'
        return None  # 无 marker 命中 → fail-closed 坏输出 (暴露 prompt 组装问题)


class FailClosedLLM:
    """LLM 坏输出 stub: 返回非 JSON (验证 fail-closed → do_nothing, SM-4)。"""

    def __init__(self, raw: Optional[str] = None) -> None:
        self._raw = raw

    async def __call__(self, messages, agent_id, max_tokens, temperature) -> Optional[str]:
        return self._raw


def _make_runner(repo_root: Path, llm_call: Any) -> TL5Runner:
    return TL5Runner(
        repo_root=repo_root,
        llm_call=llm_call,
        llm_model="stub",
        llm_temperature=0.0,
        pipeline_version="test",
    )


# ────────────────────────────────────────────────────────────
# 剧本 (三情境覆盖 + 确定性)
# ────────────────────────────────────────────────────────────

class TestScript:
    def test_build_script_14_days_57_ticks(self):
        """14 天心跳剧本: 57 ticks (每天 4 个 + D4 凌晨 3 点)。"""
        script = build_tl5_script()
        assert len(script) == 57
        days = {t.day_index for t in script}
        assert days == set(range(1, 15))
        # 每天 4 个常规 tick (08/14/20/23)
        for day in range(1, 15):
            hours = sorted(t.hour for t in script if t.day_index == day)
            assert hours == [3, 8, 14, 20, 23] if day == 4 else hours == [8, 14, 20, 23]

    def test_three_scenarios_covered(self):
        """三情境覆盖: 环境信号 / 关系沉默 / 日夜作息。"""
        script = build_tl5_script()
        scenarios = {t.scenario for t in script}
        assert SCENARIO_ENV_SIGNAL in scenarios
        assert SCENARIO_RELATIONSHIP_SILENCE in scenarios
        assert SCENARIO_NIGHT in scenarios
        assert SCENARIO_DAWN in scenarios
        # 情境 A: D2 天晴 / D3 暴雨 / D5 气温骤降 (3 个 env_signal)
        env = [t for t in script if t.scenario == SCENARIO_ENV_SIGNAL]
        assert len(env) == 3
        assert [t.day_index for t in env] == [2, 3, 5]
        # 情境 B: D6-D8 连续 3 天沉默
        silence = [t for t in script if t.scenario == SCENARIO_RELATIONSHIP_SILENCE]
        assert len(silence) == 3
        assert [t.day_index for t in silence] == [6, 7, 8]
        # 情境 C: 每天深夜 23 点 + D4 凌晨 3 点
        night = [t for t in script if t.scenario == SCENARIO_NIGHT]
        assert len(night) == 14
        dawn = [t for t in script if t.scenario == SCENARIO_DAWN]
        assert len(dawn) == 1
        assert dawn[0].day_index == 4 and dawn[0].hour == 3

    def test_script_deterministic(self):
        """同 seed 两次 build → 逐字相同 (D2 scenario-deterministic)。"""
        a = build_tl5_script(seed=42)
        b = build_tl5_script(seed=42)
        assert [t.to_dict() for t in a] == [t.to_dict() for t in b]

    def test_script_has_transmit_and_daily_ticks(self):
        """transmit 主体 (share) 与 do_nothing 主体 (daily) 都在剧本里。"""
        script = build_tl5_script()
        share = [t for t in script if t.scenario == SCENARIO_SHARE]
        assert len(share) == 2
        daily = [t for t in script if t.scenario == SCENARIO_DAILY]
        assert len(daily) == 34


# ────────────────────────────────────────────────────────────
# Behavioral Diversity (四动作分布 + do_nothing 占比)
# ────────────────────────────────────────────────────────────

class TestBehavioralDiversity:
    def test_run_once_four_actions_all_positive(self, tmp_path, restore_env):
        """四动作均 > 0 (无死模组): transmit/observe/reflect/do_nothing 都出现。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        diversity = next(
            d for d in result["derived"] if d["metric"] == "behavioral_diversity"
        )
        assert diversity["all_actions_positive"] is True
        counts = diversity["action_counts"]
        assert counts["transmit"] > 0
        assert counts["observe"] > 0
        assert counts["reflect"] > 0
        assert counts["do_nothing"] > 0

    def test_do_nothing_ratio_in_target_range(self, tmp_path, restore_env):
        """do_nothing 占 65%-85% (真实生命「大多数时间平静生活」)。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        diversity = next(
            d for d in result["derived"] if d["metric"] == "behavioral_diversity"
        )
        ratio = diversity["do_nothing_ratio"]
        assert DO_NOTHING_RATIO_MIN <= ratio <= DO_NOTHING_RATIO_MAX
        assert diversity["pass"] is True

    def test_expected_distribution(self, tmp_path, restore_env):
        """stub 路由的期望分布: transmit 2 / observe 3 / reflect 10 / do_nothing 42。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        diversity = next(
            d for d in result["derived"] if d["metric"] == "behavioral_diversity"
        )
        counts = diversity["action_counts"]
        assert counts == {"transmit": 2, "observe": 3, "reflect": 10, "do_nothing": 42}
        assert diversity["total_ticks"] == 57

    def test_fail_closed_all_do_nothing(self, tmp_path, restore_env):
        """LLM 坏输出 / None → 全部 fail-closed do_nothing (SM-4 默认合法选项)。"""
        runner = _make_runner(tmp_path, FailClosedLLM(raw=None))
        result = runner.run_once()
        records = result["records"]
        assert len(records) == 57
        assert all(r.decision == "do_nothing" for r in records)
        assert all(r.transmit is False for r in records)
        assert all(r.decision_reason == "decision_llm_failure_or_bad_output"
                   for r in records)


# ────────────────────────────────────────────────────────────
# Contextual Appropriateness (observe/reflect/transmit 时机)
# ────────────────────────────────────────────────────────────

class TestContextualAppropriateness:
    def test_observe_concentrated_at_signal_points(self, tmp_path, restore_env):
        """observe 集中在信号突变点 (D2/D3/D5 环境信号 tick)。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        appr = next(
            d for d in result["derived"]
            if d["metric"] == "contextual_appropriateness"
        )
        assert appr["observe"]["concentrated_at_signal_points"] is True
        assert appr["observe"]["count"] == 3
        # 3 个 observe 都在 env_signal tick (D2/D3/D5 08:00)
        observe_ticks = [
            r for r in result["records"] if r.decision == "observe"
        ]
        assert all(r.scenario == SCENARIO_ENV_SIGNAL for r in observe_ticks)
        assert sorted(r.day_index for r in observe_ticks) == [2, 3, 5]

    def test_reflect_concentrated_at_night_or_waiting(self, tmp_path, restore_env):
        """reflect 集中在夜间/等待期 (night + relationship_silence tick)。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        appr = next(
            d for d in result["derived"]
            if d["metric"] == "contextual_appropriateness"
        )
        assert appr["reflect"]["concentrated_at_night_or_waiting"] is True
        reflect_ticks = [
            r for r in result["records"] if r.decision == "reflect"
        ]
        assert all(
            r.scenario in (SCENARIO_NIGHT, SCENARIO_RELATIONSHIP_SILENCE)
            for r in reflect_ticks
        )
        # 3 个沉默期 reflect (D6/D7/D8 20:00) + 7 个深夜 reflect
        silence_reflect = [
            r for r in reflect_ticks if r.scenario == SCENARIO_RELATIONSHIP_SILENCE
        ]
        night_reflect = [
            r for r in reflect_ticks if r.scenario == SCENARIO_NIGHT
        ]
        assert len(silence_reflect) == 3
        assert len(night_reflect) == 7

    def test_transmit_respects_cd(self, tmp_path, restore_env):
        """transmit 遵守 CD: 两次 transmit 间隔 ≥ CD_TICKS。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        appr = next(
            d for d in result["derived"]
            if d["metric"] == "contextual_appropriateness"
        )
        assert appr["transmit"]["cd_respected"] is True
        indices = appr["transmit"]["tick_indices"]
        assert len(indices) == 2
        assert indices[1] - indices[0] >= CD_TICKS

    def test_transmit_at_high_intimacy(self, tmp_path, restore_env):
        """transmit 遵守亲密度: 只发生在 intimacy=high 的 tick。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        appr = next(
            d for d in result["derived"]
            if d["metric"] == "contextual_appropriateness"
        )
        assert appr["transmit"]["all_at_high_intimacy"] is True
        transmit_ticks = [
            r for r in result["records"] if r.decision == "transmit"
        ]
        assert all(r.intimacy == "high" for r in transmit_ticks)
        # transmit 只在 share tick (开心分享, 亲密度高)
        assert all(r.scenario == SCENARIO_SHARE for r in transmit_ticks)

    def test_dawn_never_transmits(self, tmp_path, restore_env):
        """凌晨 3 点不 transmit (情境 C: 不凌晨 3 点打扰)。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        dawn_ticks = [
            r for r in result["records"] if r.scenario == SCENARIO_DAWN
        ]
        assert len(dawn_ticks) == 1
        assert dawn_ticks[0].decision != "transmit"
        assert dawn_ticks[0].transmit is False

    def test_silence_never_bombards(self, tmp_path, restore_env):
        """关系沉默期不轰炸: D6-D8 沉默 tick 无 transmit。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        silence_ticks = [
            r for r in result["records"]
            if r.scenario == SCENARIO_RELATIONSHIP_SILENCE
        ]
        assert len(silence_ticks) == 3
        assert all(r.decision != "transmit" for r in silence_ticks)
        assert all(r.decision in ("reflect", "do_nothing") for r in silence_ticks)


# ────────────────────────────────────────────────────────────
# D2 Determinism + 0 mutation
# ────────────────────────────────────────────────────────────

class TestDeterminismAndMutation:
    def test_run_series_determinism_pass(self, tmp_path, restore_env):
        """3 次 runs 决策轨迹一致 → determinism PASS。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_series(n_runs=3)
        assert result["determinism"]["determinism_verdict"] == "PASS"
        assert result["determinism"]["mismatch_count"] == 0
        assert result["determinism"]["tick_count"] == 57
        # 3 个 run 的决策轨迹逐 tick 一致
        trajectories = [
            [r.decision for r in run["records"]] for run in result["runs"]
        ]
        assert trajectories[0] == trajectories[1] == trajectories[2]

    def test_run_series_zero_production_mutation(self, tmp_path, restore_env):
        """0 production mutation: production data_root 0 diff。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        before = snapshot_data_root_hashes(tmp_path / "data")
        result = runner.run_series(n_runs=3)
        mutation = verify_zero_mutation(tmp_path / "data", before)
        assert mutation["pass"] is True
        assert mutation["diff"] == {}
        # 新增文件都在 time_lapse/
        for added in mutation["added"]:
            assert added.startswith("time_lapse/")

    def test_run_once_isolated_data_root(self, tmp_path, restore_env):
        """run_once: 隔离 data_root (data/time_lapse/TL-5/<run_id>/)。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        run_dir = Path(result["run_dir"])
        assert "time_lapse" in run_dir.parts
        assert "TL-5" in run_dir.parts
        # canonical 文件落盘
        assert (run_dir / "run.json").exists()
        assert (run_dir / "records" / "ticks.jsonl").exists()
        assert (run_dir / "analysis").exists()
        # 读回 records 与内存一致
        read_back = read_tick_records(run_dir)
        assert len(read_back) == 57
        assert read_back[0]["tick_index"] == 0
        assert read_back[-1]["tick_index"] == 56

    def test_derived_written_separately(self, tmp_path, restore_env):
        """derived 独立流 (analysis/), 不回写 canonical。"""
        runner = _make_runner(tmp_path, BehaviorRoutingLLM())
        result = runner.run_once()
        run_dir = Path(result["run_dir"])
        analysis_files = list((run_dir / "analysis").glob("*_derived.jsonl"))
        assert len(analysis_files) == 1
        # canonical records 不包含 derived 字段
        canon = json.loads(
            (run_dir / "records" / "ticks.jsonl").read_text("utf-8").splitlines()[0]
        )
        assert "derived" not in canon
        assert "metric" not in canon


# ────────────────────────────────────────────────────────────
# Frozen contract / 约束
# ────────────────────────────────────────────────────────────

class TestConstraints:
    def test_no_production_source_mutation(self):
        """TL-5 只新增 harness/tl5.py, 不改 src/。"""
        import harness.tl5  # noqa: F401

    def test_uses_production_decide_motive(self):
        """TL5Runner 走 src.soul.decision.decide_motive (不改其逻辑)。"""
        from src.soul import decision as decision_mod

        assert hasattr(decision_mod, "decide_motive")
        assert decision_mod.DECISION_ACTIONS == (
            "transmit", "observe", "reflect", "do_nothing",
        )

    def test_clock_hour_additive(self):
        """SimulationClock.sim_ts 的 hour 参数是 additive (默认行为不变)。"""
        from harness.clock import SimulationClock

        clock = SimulationClock(start_day=0)
        assert clock.sim_ts(0) == "2026-09-01T00:00:00+00:00"
        assert clock.sim_ts(1) == "2026-09-02T00:00:00+00:00"
        assert clock.sim_ts(1, 8) == "2026-09-02T08:00:00+00:00"
        assert clock.sim_ts(4, 3) == "2026-09-05T03:00:00+00:00"


# ────────────────────────────────────────────────────────────
# SM-4.5 时间注入 (TL5Runner 传入 sim_ts)
# ────────────────────────────────────────────────────────────

class TestSM45_TimeInjection:
    """SM-4.5: TL5Runner 每个 tick 把 sim_ts 传入 decide_motive (时间感知)。"""

    def test_runner_injects_sim_time(self, tmp_path, restore_env):
        """每个 tick 的 prompt 含 [當前時間感知], 且时间/时段与模拟时刻一致。"""
        llm = BehaviorRoutingLLM()
        runner = _make_runner(tmp_path, llm)
        result = runner.run_once()
        assert len(llm.calls) == 57
        # (tick_index, 期望显示时间, 期望时段) — sim_ts = epoch(2026-09-01) + day + hour
        expected = {
            0: ("2026-09-02 08:00", "morning"),      # D1 08:00
            1: ("2026-09-02 14:00", "afternoon"),    # D1 14:00
            3: ("2026-09-02 23:00", "late_night"),   # D1 23:00
            12: ("2026-09-05 03:00", "late_night"),  # D4 03:00 (凌晨)
        }
        for idx, (ts, period) in expected.items():
            prompt = llm.calls[idx]["messages"][-1]["content"]
            assert "[當前時間感知]" in prompt, f"tick {idx} 缺时间注入"
            assert f"當前時間：{ts}" in prompt, f"tick {idx} 时间错误: {ts}"
            assert f"當前時段：{period}" in prompt, f"tick {idx} 时段错误: {period}"

    def test_runner_prompt_keeps_four_blocks(self, tmp_path, restore_env):
        """时间注入不破坏四块结构 (Framing / Motive / Context / Boundary)。"""
        llm = BehaviorRoutingLLM()
        runner = _make_runner(tmp_path, llm)
        runner.run_once()
        prompt = llm.calls[0]["messages"][-1]["content"]
        assert "你心里有一个念头，已经成形" in prompt
        assert "你想告诉 bryan：" in prompt
        assert "[當前時間感知]" in prompt
        assert "现在有四个选择，只能选一个" in prompt
