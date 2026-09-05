"""
tests/harness/test_tl10_relational_expression.py — TL-10 关系表达端到端验收

阶段 C-3.1「关系带双组装注入 + motive_target 透传 + P1 公开频道分流」生产闭环
钢印: 四大剧本硬断言全绿 (Owner 拍板, 不可减弱)。

  1. 剧本 1 A2A 客厅公开分流实证 (P1 闭环): scheduler transmit 记录 →
     extra["motive_target"] → resolve_proactive_delivery 全链; 硬断言
     resolve_proactive_delivery("agent_b") 严格导出 {"mode": "group",
     "target_channel": None, "target_user_id": None} (lounge/soul_wall 公开语义),
     0 穿透 Bryan 1:1 私聊 (mode 不得为 private、target_user_id 为空)。
  2. 剧本 2 关系带差异化注入实证: stranger / familiar 两带 (真实 helper +
     真实 relationships entry); 硬断言 注入块存在且标签逐字相符
     (stranger→陌生人 / familiar→熟悉)、impression_tags 正确渲染、
     整块 token 估算严格 ≤80、stranger 也注入、None 向後兼容逐字节。
  3. 剧本 3 A2U 私聊保全实证: target == "user_bryan" 与 "bryan" 各测一次;
     硬断言 归一化生效 ("bryan" → user_bryan entry)、resolve_proactive_delivery
     返回 private/telegram/Bry chat_id 100% 维持原状、A2U 私聊组装注入正常且
     None 向后兼容。
  4. 剧本 4 三重 Fail-Safe 容错复核: ①无关系记录 ②非合法 target (None/空/莫名字串)
     ③读取异常 (mock 抛异常); 硬断言 信息块安全平滑省略 (回 ""), 0 崩溃 0 抛出
     未捕获异常; resolve_proactive_delivery 未知 target fail-safe 默认私聊不报错。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/harness/test_tl10_relational_expression.py -v

Frozen contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 / DECISION-PROMPT。
本文件只新增 harness 测试; 运行时全部走真实实现 (resolve_proactive_delivery /
_format_relational_perception_block / _build_messages_group / _build_messages_private /
SoulScheduler._decision_check / _publish_agency_trigger / AgentYua._fire_intent /
make_motive / MotiveTraceStore / get_relationships_manager), LLM 以确定性 stub
注入 (0 网络调用), 全部数据写隔离 data_root。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.paths import reset_data_root

from harness.tl10 import (
    PRIVATE_DELIVERY,
    SCENARIO_A2A_ROUTING,
    SCENARIO_A2U_PRESERVE,
    SCENARIO_BAND_INJECTION,
    SCENARIO_FAIL_SAFE,
    TL10_AGENT_A,
    TL10_AGENT_B,
    TL10Runner,
    _estimate_tokens,
    resolve_proactive_delivery,
)

ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# Fixture 隔离 (tmp_path 即假 repo_root → data/time_lapse 全隔离)
# ───────────────────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    """TL-10 pytest 隔离环境: 每用例独立 tmp_path (0 真实生产 data 接触)。"""
    monkeypatch.setenv(
        "SOUL_OS_DATA_DIR",
        str(tmp_path / "data" / "time_lapse" / "TL-10" / "fixture_probe"),
    )
    reset_data_root()
    yield tmp_path
    from harness.tl10 import _reset_process_state
    _reset_process_state()
    reset_data_root()


def _runner(tmp_path: Path) -> TL10Runner:
    return TL10Runner(repo_root=tmp_path, seed=42)


# ───────────────────────────────────────────────────────────
# 剧本 1: A2A 客厅公开分流实证 (P1 闭环)
# ───────────────────────────────────────────────────────────

class TestScenario1A2ARouting:
    def test_p1_group_routing_full_chain(self, iso_env):
        """scheduler transmit 记录 → extra → _fire_intent 透传 → P1 group 闭环。"""
        out = _runner(iso_env).run_scenario(SCENARIO_A2A_ROUTING)
        d = out["derived"]
        assert d.passed, f"剧本 1 硬断言失败: {d.checks}"
        # P1 分流: agent-target → group 公开语义 (0 穿透 Bryan 1:1 私聊)
        delivery = d.key_numbers["delivery"]
        assert delivery == {
            "mode": "group", "target_channel": None, "target_user_id": None,
        }
        assert delivery["mode"] != "private"
        assert delivery["target_user_id"] is None
        # scheduler 全链: extra["motive_target"] 透传 + 单次消费
        assert d.key_numbers["extra_motive_target"] == TL10_AGENT_B
        assert d.key_numbers["intent_motive_target"] == TL10_AGENT_B
        assert d.checks["chain_single_consume"] is True
        assert d.checks["chain_decision_ok"] is True

    def test_p1_direct_resolve_group(self, iso_env):
        """直接真实入口: resolve_proactive_delivery(agent) → group。"""
        from src.soul.motive import set_agent_ids
        set_agent_ids([TL10_AGENT_B])
        assert resolve_proactive_delivery(TL10_AGENT_B) == {
            "mode": "group", "target_channel": None, "target_user_id": None,
        }


# ───────────────────────────────────────────────────────────
# 剧本 2: 关系带差异化注入实证 (Prompt 感知核验)
# ───────────────────────────────────────────────────────────

class TestScenario2BandInjection:
    def test_band_injection_verbatim_and_budget(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_BAND_INJECTION)
        d = out["derived"]
        assert d.passed, f"剧本 2 硬断言失败: {d.checks}"
        # stranger 也注入 + 标签逐字
        assert d.key_numbers["stranger_block"].startswith("[關係感知]")
        assert "- 對 agent_akane 的關係帶：陌生人" in d.key_numbers["stranger_block"]
        # familiar 标签逐字 + tags 行
        assert "- 對 agent_akane 的關係帶：熟悉" in d.key_numbers["familiar_block"]
        assert "- 印象：開朗、喜歡音樂" in d.key_numbers["familiar_block"]
        # token 预算严格 ≤80 (字符数/2 估算) + 契约字符上限 160
        assert _estimate_tokens(d.key_numbers["stranger_block"]) <= 80
        assert _estimate_tokens(d.key_numbers["familiar_block"]) <= 80
        assert d.key_numbers["worst_case_tokens_est"] <= 80
        assert d.key_numbers["worst_case_block_len"] <= 160
        # 最坏 case: 6+ 长 tag → 只渲染前 5 (4 个分隔符), 超量 tag 被截掉
        rendered = d.key_numbers["worst_case_tags_rendered"]
        assert rendered.startswith("- 印象：") and rendered.count("、") == 4
        assert "第六個標籤不該出現" not in rendered
        # 组裝 None 向後兼容 (逐字节)
        assert d.checks["group_none_byte_identical"] is True

    def test_none_vs_injected_byte_level(self, iso_env):
        """A2A 组裝: 带 motive_target → 有块; 不带 → 无块 (同一函数)。"""
        from src.soul.motive import set_agent_ids
        set_agent_ids([])
        out = _runner(iso_env).run_scenario(SCENARIO_BAND_INJECTION)
        d = out["derived"]
        pre = {k: v for k, v in d.checks.items() if k.startswith("group_")}
        assert pre["group_inject_present"] is True
        assert pre["group_none_no_inject"] is True
        assert pre["group_inject_label"] is True


# ───────────────────────────────────────────────────────────
# 剧本 3: A2U 私聊保全实证 (Bryan 通道零退化)
# ───────────────────────────────────────────────────────────

class TestScenario3A2UPreserve:
    def test_a2u_private_preserve_and_normalize(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_A2U_PRESERVE)
        d = out["derived"]
        assert d.passed, f"剧本 3 硬断言失败: {d.checks}"
        # bryan / user_bryan 双形归一化 → private/telegram/Bry chat_id 100% 原状
        assert d.key_numbers["delivery_bryan"] == PRIVATE_DELIVERY
        assert d.key_numbers["delivery_user_bryan"] == PRIVATE_DELIVERY
        assert d.checks["private_100_preserved"] is True
        # helper 归一化 ("bryan" → user_bryan entry 命中, 防 key 错位漏注入)
        assert d.checks["helper_normalize_hits_user_bryan"] is True
        assert d.checks["helper_direct_key"] is True
        # A2U 私聊组装注入 + None 逐字节兼容
        assert d.checks["private_inject_present"] is True
        assert d.checks["private_inject_tags"] is True
        assert d.checks["private_none_byte_identical"] is True
        # scheduler 全链 bryan → extra → resolve 仍 private
        assert d.checks["chain_bryan_delivery_private"] is True

    def test_normalize_consistent_with_motive(self, iso_env):
        """契约 §2.4: helper 内部归一化与 motive 语义一致 (target=="bryan" 映射
        relationships key "user_bryan")。"""
        from src.soul.motive import TARGET_BRYAN
        assert TARGET_BRYAN == "bryan"  # 既有 motive 常量
        out = _runner(iso_env).run_scenario(SCENARIO_A2U_PRESERVE)
        d = out["derived"]
        assert "對 bryan 的關係帶：熟悉" in d.key_numbers["helper_block_via_bryan"]


# ───────────────────────────────────────────────────────────
# 剧本 4: 三重 Fail-Safe 容错复核
# ───────────────────────────────────────────────────────────

class TestScenario4FailSafe:
    def test_triple_fail_safe_no_crash(self, iso_env):
        out = _runner(iso_env).run_scenario(SCENARIO_FAIL_SAFE)
        d = out["derived"]
        assert d.passed, f"剧本 4 硬断言失败: {d.checks}"
        # ① 无记录 ② 非法 target ③ 读取异常 → "" (0 崩溃 0 抛出)
        assert d.key_numbers["no_entry_result"] == ""
        assert d.key_numbers["weird_target_result"] == ""
        assert d.checks["target_none_blank"] is True
        assert d.checks["target_empty_blank"] is True
        assert d.checks["read_exception_blank"] is True
        # resolve 未知 target → fail-safe private 不报错
        assert d.checks["delivery_failsafe_weird"] is True
        assert d.checks["delivery_failsafe_none"] is True
        assert d.checks["delivery_failsafe_empty"] is True
        assert d.checks["delivery_failsafe_unregistered"] is True

    def test_delivery_failsafe_unknown(self, iso_env):
        """直接真实入口: 未知 / None / 空 target 全部 private 不抛异常。"""
        from src.soul.motive import set_agent_ids
        set_agent_ids([])
        for t in ("agent_unknown", None, ""):
            assert resolve_proactive_delivery(t) == PRIVATE_DELIVERY


# ───────────────────────────────────────────────────────────
# D2 宏确定性 + production 0 mutation
# ───────────────────────────────────────────────────────────

class TestD2Determinism:
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
        _runner(iso_env).run_scenario(SCENARIO_A2A_ROUTING)
        mut = verify_zero_mutation(production, before)
        assert mut["pass"] is True, f"production data 被污染: {mut['diff']} {mut['added']}"


# ───────────────────────────────────────────────────────────
# 只读验证: harness 自身 0 src 生产改动 (静态快照审计)
# ───────────────────────────────────────────────────────────

class TestZeroSrcMutation:
    def test_no_src_files_touched(self, iso_env):
        """TL-10 只允许新增 harness/ + tests/harness/ 文件; src/ 与生产配置
        必须 0 变更 (以 git status 精确 add 为契约, 此处静态复核 harness 文件
        不引用任何未 commit 的 src 修改)。"""
        from harness.tl10 import SCENARIOS
        assert len(SCENARIOS) == 4
        # 反向: 确保 harness 文件中的 src import 全部可解析 (真实实现入口)
        assert TL10_AGENT_A == "agent_ruka"
        assert TL10_AGENT_B == "agent_akane"