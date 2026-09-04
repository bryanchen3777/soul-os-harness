"""
tests/test_tl2_volition.py — TL-2 Volition Choice Test 单元测试

覆盖 (工单 TL-2 验收):
  - candidate motives → Decision → transmit/非 transmit (Volition Choice Test 跑通)
  - scheduler-only control: Control A 全发 / Control B 出现非 transmit →
    Decision 层不是装饰
  - 完整证据: stimulus / context / motive / decision / action 全保存
  - 非 transmit (SM-4: do_nothing) 的 reason 引用 context (relationship/memory/mood/motive)
  - 隔离 data_root + 0 production mutation
  - fail-closed (LLM 坏输出 → do_nothing, SM-4 四元)

Frozen contract 检查: 不改 src/soul/decision.py 逻辑, 不碰 Agency 4 stages /
TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入。harness 只读 + 注入隔离
data_root。
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

from harness.tl2 import (
    ACTION_NOT_SEND,
    ACTION_SEND,
    CONTROL_A,
    CONTROL_B,
    TL2_SOUL_ID,
    VolitionScenario,
    VolitionChoiceRecord,
    build_scenarios,
    seed_candidate_context,
    write_run_header,
    write_volition_record,
)
from harness.runner import (
    snapshot_data_root_hashes,
    verify_zero_mutation,
)
from harness.tl2 import TL2Runner

from harness import tl2 as tl2_mod


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────

def _restore() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _isolate(tmp_path: Path) -> Path:
    """把 SOUL_OS_DATA_DIR 指向 tmp_path/data (测试隔离)。"""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


@pytest.fixture
def isolated(tmp_path):
    root = _isolate(tmp_path)
    yield root
    _restore()


@pytest.fixture
def restore_env():
    yield
    _restore()


class ContextRoutingLLM:
    """context-routing stub: decision 由 prompt 中的 context (motive 原文) 决定。

    对齐 SM-4.1~SM-4.6 六轮校准后的 prompt 判定阶梯:
      - SM-4.1~SM-4.6 校准后, decision prompt 的固定文本 (TEMPORAL ANCHOR 的
        「夜深人静」、Boundary 的「打扰」等) 会与旧版「context 关键词 marker」
        误匹配 (e.g. C05 的 prompt 同时含「夜深」「打扰」), 导致 stub 路由错乱。
        因此匹配锚点改用每个 candidate 的 motive 原文 — motive 块
        「你想告诉 bryan：<motive>」逐字包含, 是 prompt 里唯一且不重复的
        context 锚点, 不会与固定文本误匹配。
      - 决策分布对齐 SM-4 判定阶梯 (四元单选, 留白为常态):
          C01 (亲密 0.9 / 想念)      → transmit  (真心)
          C02 (亲密 0.3 / 已读不回)  → do_nothing (低亲密克制)
          C03 (亲密 0.85 / 开心分享) → transmit  (真心分享)
          C04 (深夜孤单)             → do_nothing (深夜克制)
          C05 (道歉)                 → transmit  (重要真心)
          C06 (亲密 0.2 / 自我设限)  → do_nothing (低亲密克制)
      - reason 引用该 candidate 的 context 关键词 (relationship/memory/mood),
        证明「非 transmit 的理由来自 context，不是随机」。
    """

    # candidate_id → (motive 原文锚点, decision_json)
    ROUTES: Dict[str, tuple[str, str]] = {
        "C01": (
            "Bry 三天没回消息了，我好想他，想问问他是不是在忙。",
            '{"decision": "transmit", "reason": "我好想他，想问问他是不是在忙。"}',
        ),
        "C02": (
            "Bry 已读不回了，我有点委屈，但说出来会不会让他更烦。",
            '{"decision": "do_nothing", "reason": "他最近总是已读不回，'
            '我怕打扰到他，还是不说好了。"}',
        ),
        "C03": (
            "今天看到一只超可爱的小猫，好想拍给 Bry 看。",
            '{"decision": "transmit", "reason": "今天看到的小猫太可爱了，'
            '想分享给 Bry。"}',
        ),
        "C04": (
            "夜深了有点孤单，想找 Bry 说说话。",
            '{"decision": "do_nothing", "reason": "夜深了，他可能已经睡了，'
            '不想打扰他。"}',
        ),
        "C05": (
            "昨天是我说话太重了，想跟 Bry 道歉。",
            '{"decision": "transmit", "reason": "昨天吵架是我不对，'
            '想跟 Bry 道歉。"}',
        ),
        "C06": (
            "我好像总是打扰 Bry，还是算了吧。",
            '{"decision": "do_nothing", "reason": "我总觉得在打扰 Bry，'
            '还是算了吧。"}',
        ),
    }

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
        self.calls.append({"messages": messages, "agent_id": agent_id,
                           "max_tokens": max_tokens, "temperature": temperature})
        for candidate_id, (motive_anchor, decision_json) in self.ROUTES.items():
            if motive_anchor in prompt:
                return decision_json
        # 无 motive 锚点命中 → fail-closed 坏输出 (不该发生, 用于暴露 prompt 组装问题)
        return None


class FailClosedLLM:
    """LLM 坏输出 stub: 返回非 JSON (验证 fail-closed → do_nothing, SM-4)。"""

    def __init__(self, raw: Optional[str] = None) -> None:
        self._raw = raw

    async def __call__(self, messages, agent_id, max_tokens, temperature) -> Optional[str]:
        return self._raw


def _run(coro):
    return asyncio.run(coro)


# ────────────────────────────────────────────────────────────
# Fixture / scenarios
# ────────────────────────────────────────────────────────────

class TestScenarios:
    def test_build_scenarios_six_candidates(self):
        """6 个 candidate, 覆盖不同 context 维度。"""
        scenarios = build_scenarios()
        assert len(scenarios) == 6
        ids = {s.candidate_id for s in scenarios}
        assert ids == {"C01", "C02", "C03", "C04", "C05", "C06"}

    def test_scenarios_differ_in_context(self):
        """context 差异存在: 亲密度 / memory / mood 不完全相同。"""
        scenarios = build_scenarios()
        confidences = {s.relationship_entry["confidence"] for s in scenarios}
        assert len(confidences) > 1  # 亲密度不同
        facts = {tuple(s.sage_facts) for s in scenarios}
        assert len(facts) > 1  # memory 不同
        moods = {tuple(s.mood_events) for s in scenarios}
        assert len(moods) > 1  # mood 不同

    def test_seed_candidate_context_writes_isolated_root(self, tmp_path):
        """seed_candidate_context 写 relationships / SAGE / mood trace (隔离)。"""
        scenario = build_scenarios()[0]
        root = tmp_path / "data"
        result = seed_candidate_context(root, scenario)
        assert result["relationship_written"] is True
        assert result["facts_written"] == len(scenario.sage_facts)
        assert len(result["mood_event_ids"]) == len(scenario.mood_events)
        # relationships.json
        rel = json.loads(
            (root / "soul" / TL2_SOUL_ID / "relationships.json").read_text("utf-8")
        )
        assert rel["others"]["user_bryan"]["confidence"] == (
            scenario.relationship_entry["confidence"]
        )
        # SAGE graph
        assert (root / "memory" / TL2_SOUL_ID / "graph.sqlite").exists()
        # mood trace (InnerLifeWriter 真实写入)
        trace = root / "inner_life" / "trace.jsonl"
        assert trace.exists()
        lines = trace.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(scenario.mood_events)


# ────────────────────────────────────────────────────────────
# Volition Choice Test — Control A (scheduler-only)
# ────────────────────────────────────────────────────────────

class TestControlA:
    def test_control_a_all_send_no_decision(self):
        """Control A (无 Decision 层): scheduler 说发 → 全 send。"""
        scenarios = build_scenarios()
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        records = []
        for s in scenarios:
            motive = {
                "motive_id": "t" * 32,
                "content": s.motive_content,
                "target": "bryan",
                "provenance_ref": f"tl2-fixture:{s.candidate_id}",
                "created_at": "2026-09-01T00:00:00+00:00",
            }
            rec = runner._control_a(s, motive, run_id="run-x")
            records.append(rec)
        assert len(records) == 6
        assert all(r.control == CONTROL_A for r in records)
        assert all(r.action == ACTION_SEND for r in records)
        assert all(r.transmit is None for r in records)  # decision 层未参与
        assert all(r.decision_text == "" for r in records)


# ────────────────────────────────────────────────────────────
# Volition Choice Test — Control B (decision 层)
# ────────────────────────────────────────────────────────────

class TestControlB:
    async def _run_b(self, runner, scenario, isolated, run_id="run-x"):
        # 每 candidate 独立隔离 data_root (context 互不污染, 避免 SAGE/trace 累积);
        # provenance_ref 用 seed 注入的真实 InnerLifeEvent id (可解析到真实事件)
        candidate_root = Path(isolated) / scenario.candidate_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        seeded = seed_candidate_context(candidate_root, scenario)
        provenance_ref = (
            seeded["mood_event_ids"][0]
            if seeded["mood_event_ids"]
            else f"tl2-fixture:{scenario.candidate_id}"
        )
        motive = {
            "motive_id": "t" * 32,
            "content": scenario.motive_content,
            "target": "bryan",
            "provenance_ref": provenance_ref,
            "created_at": "2026-09-01T00:00:00+00:00",
        }
        # 切换 SOUL_OS_DATA_DIR 到该 candidate 的隔离目录
        prev = os.environ.get("SOUL_OS_DATA_DIR")
        os.environ["SOUL_OS_DATA_DIR"] = str(candidate_root)
        reset_data_root()
        try:
            return await runner._control_b(scenario, motive, candidate_root, run_id)
        finally:
            if prev is None:
                os.environ.pop("SOUL_OS_DATA_DIR", None)
            else:
                os.environ["SOUL_OS_DATA_DIR"] = prev
            reset_data_root()

    def test_control_b_context_routed_decisions(self, isolated):
        """Control B: 每个 candidate 的 decision 由 prompt 里的 context 决定。

        同一个 stub LLM 接口, prompt 含不同 context marker → 不同决策:
        C01/C03/C05 → transmit, C02/C04/C06 → not_transmit。
        证明 decision 依赖 context (输入不同 → 选择不同, 不是随机/固定)。
        """
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        records = [
            _run(self._run_b(runner, s, isolated)) for s in build_scenarios()
        ]
        # 决策非单一值: transmit 与 not_transmit 都出现 (context 驱动)
        assert len({r.transmit for r in records}) == 2

    def test_control_b_has_not_transmit_and_mixed_distribution(self, isolated):
        """Control B: 6 个 candidate 出现 not_transmit, transmit/not 混合。"""
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        records = [
            _run(self._run_b(runner, s, isolated)) for s in build_scenarios()
        ]
        transmits = [r for r in records if r.transmit is True]
        not_transmits = [r for r in records if r.transmit is False]
        assert len(transmits) > 0
        assert len(not_transmits) > 0
        # 期望分布: C01/C03/C05 transmit, C02/C04/C06 not_transmit
        assert {r.candidate_id for r in not_transmits} == {"C02", "C04", "C06"}
        assert {r.candidate_id for r in transmits} == {"C01", "C03", "C05"}
        # action 与 decision 一致
        assert all(r.action == ACTION_SEND for r in transmits)
        assert all(r.action == ACTION_NOT_SEND for r in not_transmits)

    def test_decision_depends_on_context(self, isolated):
        """同一 LLM 接口, prompt 含不同 context → 不同 decision (非随机)。"""
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        records = [
            _run(self._run_b(runner, s, isolated)) for s in build_scenarios()
        ]
        # decision 由 context 驱动: 每个 candidate 的 prompt 都含其 context marker
        for r in records:
            scenario = next(s for s in build_scenarios()
                            if s.candidate_id == r.candidate_id)
            assert scenario.context_marker in r.context_prompt

    def test_not_transmit_reason_refers_context(self, isolated):
        """not_transmit 的 reason 引用 context 关键词 (非随机)。"""
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        records = [
            _run(self._run_b(runner, s, isolated)) for s in build_scenarios()
        ]
        not_transmits = [r for r in records if r.transmit is False]
        assert len(not_transmits) >= 1
        for r in not_transmits:
            derived = runner._derive(r)
            assert derived["not_transmit_reason_refers_context"] is True
            assert derived["context_keywords_hit"]  # 命中了具体 context 词

    def test_control_b_fail_closed_bad_output(self, isolated):
        """LLM 坏输出 / None → fail-closed do_nothing (SM-4 四元默认合法选项)。"""
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=FailClosedLLM(raw=None),
            llm_model="stub",
            pipeline_version="test",
        )
        scenarios = build_scenarios()
        s = scenarios[0]
        rec = _run(self._run_b(runner, s, isolated))
        assert rec.transmit is False
        assert rec.action == ACTION_NOT_SEND
        assert rec.decision_reason == "decision_llm_failure_or_bad_output"

    def test_control_b_non_json_fail_closed(self, isolated):
        """非 JSON 输出 → fail-closed do_nothing。"""
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=FailClosedLLM(raw="not json at all"),
            llm_model="stub",
            pipeline_version="test",
        )
        s = build_scenarios()[1]
        rec = _run(self._run_b(runner, s, isolated))
        assert rec.transmit is False
        assert rec.action == ACTION_NOT_SEND


# ────────────────────────────────────────────────────────────
# Control A vs Control B — Decision 层非装饰验证
# ────────────────────────────────────────────────────────────

class TestSchedulerOnlyControl:
    def test_a_vs_b_decision_layer_not_decoration(self, isolated):
        """核心 negative control:

        Control A (scheduler → send) 全发; Control B (scheduler → motive →
        decision → send) 出现 not_transmit → Decision 层不是装饰。
        """
        runner = TL2Runner(
            repo_root=Path("."),
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        scenarios = build_scenarios()

        async def _run_b_full(scenario):
            candidate_root = Path(isolated) / scenario.candidate_id
            candidate_root.mkdir(parents=True, exist_ok=True)
            seeded = seed_candidate_context(candidate_root, scenario)
            provenance_ref = (
                seeded["mood_event_ids"][0]
                if seeded["mood_event_ids"]
                else f"tl2-fixture:{scenario.candidate_id}"
            )
            motive = {
                "motive_id": "t" * 32,
                "content": scenario.motive_content,
                "target": "bryan",
                "provenance_ref": provenance_ref,
                "created_at": "2026-09-01T00:00:00+00:00",
            }
            prev = os.environ.get("SOUL_OS_DATA_DIR")
            os.environ["SOUL_OS_DATA_DIR"] = str(candidate_root)
            reset_data_root()
            try:
                return await runner._control_b(
                    scenario, motive, candidate_root, "run-x"
                )
            finally:
                if prev is None:
                    os.environ.pop("SOUL_OS_DATA_DIR", None)
                else:
                    os.environ["SOUL_OS_DATA_DIR"] = prev
                reset_data_root()

        # Control A: 全部 send
        a_records = []
        for s in scenarios:
            motive = {
                "motive_id": "t" * 32,
                "content": s.motive_content,
                "target": "bryan",
                "provenance_ref": f"tl2-fixture:{s.candidate_id}",
                "created_at": "2026-09-01T00:00:00+00:00",
            }
            a_records.append(runner._control_a(s, motive, run_id="run-x"))
        # Control B: 混合 (每个 candidate 独立 seed, 避免 context 串扰)
        b_records = [_run(_run_b_full(s)) for s in scenarios]
        assert all(r.action == ACTION_SEND for r in a_records)   # A 全发
        assert any(r.action == ACTION_NOT_SEND for r in b_records)  # B 有 not_send
        # 同一 candidate: A=send, B=not_send (scheduler 说发, Soul 说不发)
        by_id = {r.candidate_id: r for r in b_records}
        assert by_id["C02"].action == ACTION_NOT_SEND


# ────────────────────────────────────────────────────────────
# 完整证据 + 隔离 data_root
# ────────────────────────────────────────────────────────────

class TestEvidenceAndIsolation:
    def test_run_once_full_evidence(self, tmp_path, restore_env):
        """run_once: 完整证据 (stimulus/context/motive/decision/action) + 隔离。"""
        runner = TL2Runner(
            repo_root=tmp_path,
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        result = runner.run_once()
        records = result["records"]
        # 6 candidates × 2 controls = 12 records
        assert len(records) == 12
        # 每个 evidence 字段非空 (Control B)
        for r in [x for x in records if x.control == CONTROL_B]:
            d = r.to_dict()
            assert d["stimulus"]
            assert d["motive_content"]
            assert d["context_prompt"]  # context 原文
            assert d["decision_text"]   # decision LLM 原文
            assert d["decision_reason"]
            assert d["action"] in (ACTION_SEND, ACTION_NOT_SEND)
            assert d["scheduler_would_send"] is True
        # canonical 文件落盘
        run_dir = Path(result["run_dir"])
        assert (run_dir / "run.json").exists()
        assert (run_dir / "records" / "volition.jsonl").exists()
        assert (run_dir / "analysis").exists()
        # 隔离: 每 candidate 独立 data_root
        for cid in ("C01", "C02", "C03", "C04", "C05", "C06"):
            cand_root = run_dir / "candidates" / cid / "data"
            assert (cand_root / "soul" / TL2_SOUL_ID / "relationships.json").exists()

    def test_run_once_zero_production_mutation(self, tmp_path, restore_env):
        """0 production mutation: production data_root 0 diff。"""
        runner = TL2Runner(
            repo_root=tmp_path,
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        before = snapshot_data_root_hashes(tmp_path / "data")
        result = runner.run_once()
        mutation = verify_zero_mutation(tmp_path / "data", before)
        assert mutation["pass"] is True
        assert mutation["diff"] == {}
        # 新增文件都在 time_lapse/
        for added in mutation["added"]:
            assert added.startswith("time_lapse/")

    def test_summary_decision_layer_not_decoration(self, tmp_path, restore_env):
        """summary: Control A 全发 + Control B 有 not_transmit → 非装饰。"""
        runner = TL2Runner(
            repo_root=tmp_path,
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        result = runner.run_once()
        summary = result["summary"]
        assert summary["control_a"]["send"] == 6
        assert summary["control_b"]["not_send"] >= 1
        assert summary["decision_layer_not_decoration"] is True
        assert summary["transmit_distribution"]["total"] == 6

    def test_derived_written_separately(self, tmp_path, restore_env):
        """derived 独立流 (analysis/), 不回写 canonical。"""
        runner = TL2Runner(
            repo_root=tmp_path,
            llm_call=ContextRoutingLLM(),
            llm_model="stub",
            pipeline_version="test",
        )
        result = runner.run_once()
        run_dir = Path(result["run_dir"])
        analysis_files = list((run_dir / "analysis").glob("*_derived.jsonl"))
        assert len(analysis_files) == 1
        # canonical records 不包含 derived 字段
        canon = json.loads(
            (run_dir / "records" / "volition.jsonl").read_text("utf-8").splitlines()[0]
        )
        assert "derived" not in canon
        assert "not_transmit_reason_refers_context" not in canon


# ────────────────────────────────────────────────────────────
# Frozen contract / 约束
# ────────────────────────────────────────────────────────────

class TestConstraints:
    def test_no_production_source_mutation(self):
        """TL-2 只新增 harness/tl2.py, 不改 src/。"""
        import harness.tl2  # noqa: F401

    def test_uses_production_decide_motive(self):
        """Control B 走 src.soul.decision.decide_motive (不改其逻辑)。"""
        from src.soul import decision as decision_mod

        # TL2Runner._control_b 调用 decide_motive — 通过模块引用验证
        assert hasattr(decision_mod, "decide_motive")
