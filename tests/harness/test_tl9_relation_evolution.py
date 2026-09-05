"""
tests/harness/test_tl9_relation_evolution.py — TL-9 关系演化端到端长程实证验收

阶段 C-3「关系演化 + 他者心智」最终生产闭环钢印: 在受控多体模拟下完整实证
「公开发言 → 他者感知 → 相互 Reply → 关系带整数跃迁 → B5 种子触发 →
Motive.target 指向他者灵魂」端到端生命链路, 四大剧本硬断言全绿。

四大剧本:
  1. 剧本 1 关系正向跃迁: stranger→known→familiar→close (整数门槛);
     单次 24h 结算至多升 1 级 / 24h 窗口节流生效 (窗口内重复信号不重复结算)。
  2. 剧本 2 他者目标自发生成: band known + 印象标签 → B5 种子出现且
     Motive.target == "agent_akane" (make_motive 合法); stranger 0 种子;
     非法 target fail-closed; Decision 四元透传 (真实 parse, stub LLM)。
  3. 剧本 3 现象学自然冷却: 30 天整不降、31 天降 1 级 (familiar→known→
     stranger 方向), 不跌穿 stranger, band_updated_at 更新。
  4. 剧本 4 三大防线 + No-Scoring 刚性复核: AST 审计 SG-2 模块 0 直通
     publish / 0 定时器 / 0 float 权重; Direct Query (sqlite3 只读) 断言
     SAGE facts 0 关系域写入、自体情景记忆 0 他者事件; 候选 ≤1。
  + D2 宏确定性: 四剧本各连跑 3 次判定轨迹一致 + production 0 mutation。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/harness/test_tl9_relation_evolution.py -v

Frozen contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT。
本文件只新增 harness 测试; 运行时全部走真实实现 (settle_relations /
GoalSeedProvider.scan_seeds / make_motive / MotiveTraceStore / decide_motive),
LLM 以确定性 stub 注入 (0 网络调用)。
"""
from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.goals.motive_provider import reset_goal_providers
from src.goals.seed_provider import reset_seed_providers
from src.paths import reset_data_root
from src.soul.motive import (
    InvalidMotiveTargetError,
    make_motive,
    set_agent_ids,
)

from harness.tl9 import (
    SCENARIO_FIREWALL,
    SCENARIO_NATURAL_COOLING,
    SCENARIO_OTHER_TARGET,
    SCENARIO_RELATION_UP,
    TL9_AGENT_A,
    TL9_AGENT_B,
    TL9Runner,
)

ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# Fixture 隔离 (tmp_path 即假 repo_root → data/time_lapse 全隔离)
# ───────────────────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """TTL-9 pytest 隔离环境: 每用例独立 tmp_path (0 真实生产 data 接触)。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data" / "time_lapse" / "TL-9" / "fixture_probe"))
    reset_data_root()
    yield tmp_path
    reset_goal_providers()
    reset_seed_providers()
    import src.soul.relationships as rel_mod
    rel_mod._manager_singleton = None  # type: ignore[attr-defined]
    set_agent_ids([])
    reset_data_root()


def _runner(tmp_path: Path) -> TL9Runner:
    return TL9Runner(repo_root=tmp_path, seed=42)


# ───────────────────────────────────────────────────────────
# 剧本 1: 关系正向跃迁
# ───────────────────────────────────────────────────────────

class TestScenario1RelationUp:
    def test_positive_transition_full_chain(self, iso_env):
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_RELATION_UP)
        d = out["derived"]
        assert d.passed, f"剧本 1 硬断言失败: {d.checks}"
        recs = out["records"]
        # 轨迹: stranger→known(首窗计数已满 familiar 门槛仍只升 1 级)→familiar→close
        trajectory = [r.band_after for r in recs]
        assert trajectory == ["known", "known", "familiar", "familiar", "close"]
        # 硬断言 1: 单次 24h 结算至多升 1 级 (step1 计数 reply=3/co=5 已够
        # familiar 门槛, 结算后仍只到 known)
        r1 = recs[0]
        assert r1.band_before == "stranger"
        assert r1.band_after == "known"
        assert r1.reply_exchanges == 3
        assert r1.co_presence_sessions == 5
        # 硬断言 2: 24h 窗口节流生效 (窗口内重复信号不重复结算)
        r2 = recs[1]
        assert r2.settle_skipped == "throttle"
        assert r2.reply_exchanges == 3  # 计数不变 (重复信号被吞)
        # close 门槛: reply=10 且 co=15
        assert recs[-1].reply_exchanges == 10
        assert recs[-1].co_presence_sessions == 15
        # 0 降带 / 0 浮点 (band_updated_at 只写带迁移时刻)
        assert all(r.settle_demoted == 0 for r in recs)
        assert all(r.band_updated_at for r in recs if r.band_after != r.band_before)

    def test_band_trajectory_evidence_persisted(self, iso_env):
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_RELATION_UP)
        bands_path = Path(out["run_dir"]) / "records" / "bands.jsonl"
        assert bands_path.is_file()
        lines = [json.loads(ln) for ln in bands_path.read_text(encoding="utf-8").splitlines()]
        assert len(lines) == len(out["records"])
        assert lines[0]["band_after"] == "known"


# ───────────────────────────────────────────────────────────
# 剧本 2: 他者目标自发生成
# ───────────────────────────────────────────────────────────

class TestScenario2OtherTarget:
    def test_b5_seed_and_motive_target(self, iso_env):
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_OTHER_TARGET)
        d = out["derived"]
        assert d.passed, f"剧本 2 硬断言失败: {d.checks}"
        # B5 种子: 静态 ref relation:<other>; 确定性轮序命中 relation 源
        assert d.key_numbers["b5_seeds"] == 1
        # Motive.target == 他者灵魂 (make_motive 合法, fail-closed 通过)
        assert d.key_numbers["motive_target"] == TL9_AGENT_B
        # stranger 0 种子 (B5 不发射)
        assert d.key_numbers["stranger_seeds"] == 0
        # 候选 ≤1
        assert d.key_numbers["pending_trace_records"] == 1
        # Decision: 四元 stub 透传 (真实 parse_decision_output)
        assert d.checks["decision_quadrant"] is True
        assert d.checks["prompt_target_passthrough"] is True

    def test_make_motive_fail_closed_unregistered(self, iso_env):
        """D2 值域 fail-closed: 未注册 target → make_motive 拒绝 (0 静默放行)。"""
        set_agent_ids([TL9_AGENT_B])
        with pytest.raises(InvalidMotiveTargetError):
            make_motive(
                motive_id="bad", content="x", target="agent_unknown",
                provenance_ref="x", created_at="2026-09-06T00:00:00+00:00",
            )
        m = make_motive(
            motive_id="ok", content="想找 Akane 聊聊音乐",
            target=TL9_AGENT_B, provenance_ref="x",
            created_at="2026-09-06T00:00:00+00:00",
        )
        assert m.target == TL9_AGENT_B
        assert list(m.to_dict().keys()) == [
            "motive_id", "content", "target", "provenance_ref", "created_at",
        ]


# ───────────────────────────────────────────────────────────
# 剧本 3: 现象学自然冷却
# ───────────────────────────────────────────────────────────

class TestScenario3NaturalCooling:
    def test_demote_chain_and_floor(self, iso_env):
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_NATURAL_COOLING)
        d = out["derived"]
        assert d.passed, f"剧本 3 硬断言失败: {d.checks}"
        recs = out["records"]
        # 30 天整不降 (>30 天才降, 契约已落地语义)
        assert recs[0].band_after == "familiar"
        assert recs[0].settle_demoted == 0
        # 31 天 → 降 1 级 familiar→known, band_updated_at 更新
        assert recs[1].band_before == "familiar"
        assert recs[1].band_after == "known"
        assert recs[1].settle_demoted == 1
        assert recs[1].band_updated_at == recs[1].sim_ts
        # 62 天 → known→stranger
        assert recs[2].band_after == "stranger"
        # 93 天 → 底带不跌穿 (不再执行降带, demoted==0)
        assert recs[3].settle_demoted == 0
        # 契约歧义如实记录 (TL-9 呈报主大脑): 无信号不降带时慢爬评估会把
        # 底带 stranger 补升回 known (落地语义, 非新降带, 确定性可复现)
        assert d.checks["slow_climb_rebound_documented"] is True
        # 计数冻结 (0 增量)
        assert recs[-1].reply_exchanges == 5
        assert recs[-1].co_presence_sessions == 6


# ───────────────────────────────────────────────────────────
# 剧本 4: 三大防线 + No-Scoring 刚性复核
# ───────────────────────────────────────────────────────────

class TestScenario4Firewall:
    def test_firewall_and_no_scoring(self, iso_env):
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_FIREWALL)
        d = out["derived"]
        assert d.passed, f"剧本 4 硬断言失败: {d.checks}"
        # SAGE facts 0 关系域写入 (Direct Query, sqlite3 只读)
        assert d.key_numbers["facts_count"] == 0
        # 候选 ≤1 (goals ≤1 / pending motive ≤1)
        assert d.key_numbers["goals_count"] == 1
        assert d.key_numbers["pending_motive_records"] == 1
        # AST 审计
        assert d.checks["audit_no_publish"] is True
        assert d.checks["audit_no_timers"] is True
        assert d.checks["audit_no_scoring"] is True
        # 自体情景记忆 0 他者事件 (防线 3 Identity Firewall)
        assert d.checks["self_memory_no_other_events"] is True

    def test_static_audit_sg2_modules(self, iso_env):
        """直接 AST 复核 SG-2 相关模块 (0 直通 publish / 0 float 权重新增)。"""
        from harness.tl9 import (
            _audit_no_publish,
            _audit_no_scoring,
            _audit_no_timers,
        )
        for name in ("relation_settlement.py", "relational_bands.py"):
            path = ROOT / "src" / "social" / name
            assert _audit_no_publish(path) == [], f"{name}: 直通 publish 违规"
            assert _audit_no_timers(path) == [], f"{name}: 定时器违规"
            assert _audit_no_scoring(path) == [], f"{name}: No-Scoring 违规"

    def test_direct_query_sage_facts_zero(self, iso_env):
        """隔离 DB 只读直查: settle + seed 全链路后 facts 表仍 0 行。"""
        tmp = iso_env
        out = _runner(tmp).run_scenario(SCENARIO_FIREWALL)
        db = tmp / "data" / "time_lapse" / "TL-9" / SCENARIO_FIREWALL / out["run_id"] \
            / "memory" / TL9_AGENT_A / "graph.sqlite"
        assert db.is_file()
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            facts = int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
            goals = int(conn.execute("SELECT COUNT(*) FROM goals").fetchone()[0])
        finally:
            conn.close()
        assert facts == 0
        assert goals == 1


# ───────────────────────────────────────────────────────────
# D2 宏确定性 + production 0 mutation
# ───────────────────────────────────────────────────────────

class TestD2Determinism:
    def test_series_three_runs_all_scenarios(self, iso_env):
        """四剧本各连跑 3 次: decision/band 轨迹一致 (D2 重现) + 0 mutation。"""
        tmp = iso_env
        result = TL9Runner(repo_root=tmp, seed=42).run_series(n_runs=3)
        assert result["all_passed"] is True, (
            f"D2 序列失败: {[(s['scenario'], s['all_passed'], s['determinism_ok']) for s in result['scenarios']]}"
        )
        # 四剧本全绿 + 3-run 轨道一致
        assert len(result["scenarios"]) == 4
        assert all(s["determinism_ok"] for s in result["scenarios"])
        assert all(s["per_run_passed"] == [True, True, True] for s in result["scenarios"])
        # 0 production mutation (隔离 repo_root 下只有 time_lapse 写区)
        assert result["zero_mutation_ok"] is True
        assert result["mutation_diff"] == {}
        assert result["mutation_added"] == []

    def test_repo_production_soul_zero_mutation(self, iso_env):
        """真实生产 data/ 存在时: run 前后 data/soul 逐档 hash 0 diff (可选, 无则跳过)。"""
        production = ROOT / "data" / "soul"
        if not production.is_dir():
            return  # 开发环境无生产数据 → 隔离性质由 tmp_path 保证
        from harness.runner import snapshot_data_root_hashes, verify_zero_mutation

        before = snapshot_data_root_hashes(ROOT / "data")
        _runner(iso_env).run_scenario(SCENARIO_RELATION_UP)
        mut = verify_zero_mutation(ROOT / "data", before)
        assert mut["pass"] is True, f"production data 被污染: {mut['diff']} {mut['added']}"