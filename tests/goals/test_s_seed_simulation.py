"""
tests/goals/test_s_seed_simulation.py — S-SEED-SIM-1 自軸種子模擬驗證

目標: tmp 隔離 + stub LLM，跑通 S 軸 seed→goal→candidate 全鏈，不碰生產。
只讀生產碼: src/goals/seed_provider.py（S1 elevation / S2 fact / S3 tool / S4 motive_trace）、
src/goals/motive_provider.py（assemble_candidate、輪替、P1 缺席守衛）。

鐵律: SOUL_OS_DATA_DIR 指 tmp；stub llm_caller 回固定結構；假時鐘；
真實 LLM / 網路 / 生產 data/** 零觸碰；不開新定時器。

運行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_s_seed_simulation.py -q
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List

import pytest

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_SUSPENDED,
    Goal,
)
from src.goals.motive_provider import (
    GOAL_PROVENANCE_PREFIX,
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.goals.seed_provider import (
    _CRITERIA_TEMPLATES,
    GoalSeedProvider,
    reset_seed_providers,
)
from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact
from src.paths import reset_data_root

AGENT = "agent_s_seed_sim"


# ── 隔離 fixture ──────────────────────────────────────────────

@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    reset_seed_providers()
    import src.soul.relationships as rel_mod
    monkeypatch.setattr(rel_mod, "_manager_singleton", None)
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path=db).close()
    yield tmp_path
    reset_seed_providers()
    reset_goal_providers()
    reset_data_root()


# ── helpers（假時鐘 / stub LLM / 播種）────────────────────────

def _local(hour: int = 12, day: int = 0) -> datetime:
    # 中午 12 點：避開 quiet hours（23-08），S 軸本就不受抑制
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _stub_llm(calls: List[str], title: str = "自軸念頭（stub）"):
    async def stub(messages, agent_id, max_tokens, temperature):
        calls.append(title)
        return json.dumps({"title": title, "description": "stub 內心獨白"})
    return stub


def _stores(tmp_path: Path, calls: List[str]):
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    motive_prov = GoalMotiveProvider(agent_id=AGENT, store=store)
    seed_prov = GoalSeedProvider(
        agent_id=AGENT, store=store, llm_call=_stub_llm(calls), provider=motive_prov
    )
    return seed_prov, motive_prov, store


def _seed_s1_elevation(tmp_path: Path) -> None:
    path = tmp_path / "elevation" / "elevation_nodes.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "node_id": "sim-node-s1",
        "node_type": "trait",
        "content": "好奇：想理解自我是如何成長的",
        "agent_id": AGENT,
    }, ensure_ascii=False) + "\n", encoding="utf-8")


def _seed_s2_fact(store: GraphStore) -> str:
    fact = Fact(
        subject="自我",
        predicate="困惑",
        object="為何總在深夜想起白天的對話",
        weight=0.3,  # < FACT_QUESTION_WEIGHT_MAX(0.5) → S2 未解疑問命中
        timestamp=_local(9).timestamp(),
    )
    fid = store.add_fact(fact)
    store.flush()
    return fid


def _seed_s3_tool(tmp_path: Path) -> None:
    path = tmp_path / "soul" / "tool_registry_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "event_type": "tool_registered",
        "tool_id": "sim-tool-s3",
        "name": "sim-tool",
        "capability_group": "memory",
        "permission_class": "read",
    }, ensure_ascii=False) + "\n", encoding="utf-8")


def _seed_s4_motive(tmp_path: Path) -> None:
    path = tmp_path / "soul" / "motive_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "motive_id": "sim-motive-seed-0001",
        "content": "反覆浮現的念頭：想成為更穩定可靠的自己",
        "target": "bryan",
        "provenance_ref": "diary:morning",
        "created_at": "2026-09-01T12:00:00+00:00",
        "agent_id": AGENT,
        "status": "pending",
        "updated_at": "2026-09-01T12:00:00+00:00",
    }, ensure_ascii=False) + "\n", encoding="utf-8")


def _seed_all_s(tmp_path: Path, store: GraphStore) -> None:
    _seed_s1_elevation(tmp_path)
    _seed_s2_fact(store)
    _seed_s3_tool(tmp_path)
    _seed_s4_motive(tmp_path)


def _write_last_seen(tmp_path: Path, ref: datetime, ago_days: float = 10.0) -> None:
    path = tmp_path / "state" / "bryan_last_seen.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "last_recv_ts": (ref - timedelta(days=ago_days)).isoformat(),
    }), encoding="utf-8")


def _make_goal(goal_id: str, axis: str, ref: str, ts: float) -> Goal:
    return Goal(
        goal_id=goal_id,
        agent_id=AGENT,
        axis=axis,
        title=f"模擬目標 {goal_id[:8]}",
        description="",
        seed_source_ref=ref,
        state=GOAL_STATE_ACTIVE,
        state_updated_at=ts,
        created_at=ts,
    )


# ── 測試 ──────────────────────────────────────────────────────

class TestSSourcesProbe:
    def test_s1_s4_probes_hit_real_code(self, iso_env):
        """S1-S4 探針（生產 _probe_* 原碼，只讀）各至少 1 筆命中，軸皆為 self。"""
        calls: List[str] = []
        seed_prov, _, store = _stores(iso_env, calls)
        _seed_all_s(iso_env, store)
        now = _local(12)
        hits = {
            "elevation": seed_prov._probe_elevation(now),
            "fact": seed_prov._probe_fact(now),
            "tool": seed_prov._probe_tool(now),
            "motive_trace": seed_prov._probe_motive_trace(now),
        }
        for key, hit in hits.items():
            assert hit is not None, f"S 源 {key} 應命中"
            assert hit.axis == AXIS_SELF, f"{key} 軸應為 self"
            assert hit.ref, f"{key} 應有冪等 ref"
        assert hits["elevation"].ref == "elevation:sim-node-s1"
        assert hits["fact"].ref.startswith("fact:")
        assert hits["tool"].ref == "tool:sim-tool-s3"
        assert hits["motive_trace"].ref.startswith("motive_trace:")


class TestSeedToGoal:
    def test_scan_seeds_creates_self_goal_end_to_end(self, iso_env):
        """整鏈 scan_seeds：S 種子經 stub 語義化落成 ACTIVE 自軸 goal。"""
        calls: List[str] = []
        seed_prov, _, store = _stores(iso_env, calls)
        _seed_all_s(iso_env, store)
        created = asyncio.run(seed_prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        goal = created[0]
        assert goal.axis == AXIS_SELF
        assert goal.state == GOAL_STATE_ACTIVE
        assert calls, "應走 stub LLM 語義化（無真實 LLM）"
        stored = store.get_goals(AGENT)
        assert len(stored) == 1
        assert stored[0].axis == AXIS_SELF

    def test_s1_s4_each_become_active_self_goal(self, iso_env):
        """S1-S4 各至少 1 筆 goal（經真實 _probe＋_semantize＋_create_goal），
        斷言 axis==AXIS_SELF 且狀態 ACTIVE。"""
        calls: List[str] = []
        seed_prov, _, store = _stores(iso_env, calls)
        _seed_all_s(iso_env, store)
        now = _local(12)
        probes = [
            seed_prov._probe_elevation(now),
            seed_prov._probe_fact(now),
            seed_prov._probe_tool(now),
            seed_prov._probe_motive_trace(now),
        ]
        goals: List[Goal] = []
        for hit in probes:
            assert hit is not None
            semantics = asyncio.run(seed_prov._semantize(hit, now))
            assert semantics is not None
            goals.append(seed_prov._create_goal(hit, semantics, now))
        assert len(goals) == 4
        keys = {g.seed_source_ref.split(":")[0] for g in goals}
        assert keys == {"elevation", "fact", "tool", "motive_trace"}
        for goal in goals:
            assert goal.axis == AXIS_SELF
            assert goal.state == GOAL_STATE_ACTIVE


class TestGoalToCandidate:
    def test_assemble_puts_self_candidate_in_pending(self, iso_env):
        """goal→candidate：S 軸 ACTIVE 能進 pending 池（resolve_pending 可取回）。"""
        calls: List[str] = []
        _, motive_prov, store = _stores(iso_env, calls)
        now = _local(12)
        ts = now.timestamp()
        store.upsert_goal(_make_goal("s1" + "0" * 30, AXIS_SELF, "elevation:sim-node-s1", ts))
        store.upsert_goal(_make_goal("s2" + "0" * 30, AXIS_SELF, "fact:sim-fact-1", ts))
        motive = motive_prov.assemble_candidate(now=now)
        assert motive is not None
        assert motive.provenance_ref.startswith(GOAL_PROVENANCE_PREFIX)
        pending = motive_prov._trace_store.resolve_pending(AGENT, now=now)
        assert pending is not None
        assert pending.provenance_ref == motive.provenance_ref

    def test_quota_one_per_24h(self, iso_env):
        """同窗內第二次 assemble 被配額擋下（24h/1）；次日輪替產出另一 goal。"""
        calls: List[str] = []
        _, motive_prov, store = _stores(iso_env, calls)
        now = _local(12)
        ts = now.timestamp()
        store.upsert_goal(_make_goal("q1" + "0" * 30, AXIS_SELF, "elevation:q1", ts))
        store.upsert_goal(_make_goal("q2" + "0" * 30, AXIS_SELF, "fact:q2", ts))
        first = motive_prov.assemble_candidate(now=now)
        assert first is not None
        assert motive_prov.assemble_candidate(now=now) is None
        second = motive_prov.assemble_candidate(now=_local(12, day=2))
        assert second is not None
        assert second.provenance_ref != first.provenance_ref


class TestP1GuardContrast:
    def test_bryan_absent_suspends_bryan_not_self_then_self_flows(self, iso_env):
        """P1（缺席守衛，last_seen=10 天前）：B 軸被掛起、S 軸豁免；
        同場 S 候選仍能進 pending 池。"""
        calls: List[str] = []
        _, motive_prov, store = _stores(iso_env, calls)
        now = _local(12)
        _write_last_seen(iso_env, now, ago_days=10.0)
        ts = now.timestamp()
        b_goal = _make_goal("b9" + "0" * 30, AXIS_BRYAN, "relationship:user_bryan", ts)
        s_goal = _make_goal("s9" + "0" * 30, AXIS_SELF, "elevation:sim-node-s1", ts)
        store.upsert_goal(b_goal)
        store.upsert_goal(s_goal)
        changed = motive_prov.apply_interrupt_signals(now=now)
        assert changed == ["b9" + "0" * 30]
        states = {g.goal_id: g.state for g in store.get_goals(AGENT)}
        assert states["b9" + "0" * 30] == GOAL_STATE_SUSPENDED
        assert states["s9" + "0" * 30] == GOAL_STATE_ACTIVE
        motive = motive_prov.assemble_candidate(now=now)
        assert motive is not None
        assert motive.provenance_ref == f"{GOAL_PROVENANCE_PREFIX}s9{'0' * 30}"
        pending = motive_prov._trace_store.resolve_pending(AGENT, now=now)
        assert pending is not None
        assert pending.provenance_ref == motive.provenance_ref


class TestCriteriaMapping:
    def test_s_criteria_kind_mapping_unbroken(self, iso_env):
        """criteria.kind 映射不斷：S1-S3 observation、S4 reflection；
        且落庫 goal 的 criteria 可解析、kind 合法。"""
        assert _CRITERIA_TEMPLATES["elevation"]["kind"] == "observation"
        assert _CRITERIA_TEMPLATES["fact"]["kind"] == "observation"
        assert _CRITERIA_TEMPLATES["tool"]["kind"] == "observation"
        assert _CRITERIA_TEMPLATES["motive_trace"]["kind"] == "reflection"
        calls: List[str] = []
        seed_prov, _, store = _stores(iso_env, calls)
        _seed_all_s(iso_env, store)
        now = _local(12)
        for key in ("elevation", "fact", "tool", "motive_trace"):
            hit = seed_prov._probe(key, now)
            assert hit is not None
            semantics = asyncio.run(seed_prov._semantize(hit, now))
            assert semantics is not None
            goal = seed_prov._create_goal(hit, semantics, now)
            crit = goal.completion_criteria_dict()
            assert crit is not None
            assert crit["kind"] == _CRITERIA_TEMPLATES[key]["kind"]
