"""
tests/goals/test_c21_commitment_closure.py — C-2.1 B6 承諾閉環種子源測試

目标: 驗證 B6 commitment_closure（契約 docs/C-2.1-COMMITMENT-AND-NARRATIVE-CONTRACT.md
§3.3 決策 D1/D8/D9）:

  1. 候選識別: axis==bryan + 終態（COMPLETED/ABANDONED）+ seed_source_ref 前綴
     {relationship:, commitment:}; relation:（B5 他者源）不進 B6（C-3 系列）
  2. 冪等窗口: state_updated_at ∈ (last_seed_scan_at, now] 一次進窗（終態無出邊）;
     重複掃描 0 重複; 既有非終態同引用去重 = 雙保險
  3. criteria 模板 {interaction, 1, 7d}; 選取最舊優先（時間輪候 0 打分）;
     axis=bryan 抑制（quiet 時段天然繼承）
  4. SEED_ROTATION 第 10 源常量形狀（B5 之後、S1 之前）; 0 新 sidecar 字段

Frozen contract 边界 (0 change): Agency / TriggerEnvelope / InnerLifeEvent /
4 handlers / SAGE 写入逻辑 / goals 表 DDL / GoalProviderState 全部不触碰。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_c21_commitment_closure.py -v
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ABANDONED,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    Goal,
)
from src.goals.motive_provider import (
    GoalMotiveProvider,
    reset_goal_providers,
)
from src.goals.seed_provider import (
    SEED_ROTATION,
    _CRITERIA_TEMPLATES,
    GoalSeedProvider,
    reset_seed_providers,
)
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root

AGENT = "agent_c21"
ROOT = Path(__file__).resolve().parents[2]


# ───────────────────────────────────────────────────────────
# Fixture: 隔离 data_root + 生成器/供应商复位
# ───────────────────────────────────────────────────────────


@pytest.fixture()
def iso_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
    reset_data_root()
    reset_goal_providers()
    reset_seed_providers()
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    db.parent.mkdir(parents=True, exist_ok=True)
    GraphStore(db_path=db).close()
    yield tmp_path
    reset_seed_providers()
    reset_goal_providers()
    reset_data_root()


# ───────────────────────────────────────────────────────────
# 数据触点 helpers
# ───────────────────────────────────────────────────────────


def _seed_provider(tmp_path: Path, llm: Any) -> GoalSeedProvider:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    return GoalSeedProvider(agent_id=AGENT, store=store, llm_call=llm)


def _llm(title: str, description: str = "内心独白（stub）"):
    async def stub(messages, agent_id, max_tokens, temperature):
        return json.dumps({"title": title, "description": description})
    return stub


def _local(hour: int = 10, day: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _gid(seed: str) -> str:
    """确定性 32-hex goal_id（uuid4 同格式）。"""
    return (uuid.uuid4().hex + seed)[:32]


def _insert_goal(
    tmp_path: Path,
    *,
    goal_id: str,
    state: str,
    ref: str,
    axis: str = AXIS_BRYAN,
    state_updated_at: float,
    title: str = "一份承諾",
    advance_count: int = 2,
) -> None:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    g = Goal(
        goal_id=goal_id,
        agent_id=AGENT,
        axis=axis,
        title=title,
        description="",
        seed_source_ref=ref,
        state=state,
        state_updated_at=state_updated_at,
        created_at=state_updated_at,
        advance_count=advance_count,
    )
    store.upsert_goal(g)
    store.flush()
    store.close()


def _set_scan_state(prov: GoalSeedProvider, now: datetime, *, cursor: int = 5) -> None:
    """把 sidecar 設為「距 now 25h 前掃描過、游標在 B6」——跨過 24h 節流窗。"""
    st = prov._load_state()
    st.last_seed_scan_at = now.timestamp() - 25 * 3600
    st.seed_source_cursor = cursor
    st.seed_axis_streak = 0
    st.seed_empty_rounds = 0
    st.last_seed_axis = None
    prov._save_state(st)


def _goals(tmp_path: Path) -> List[Goal]:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()
        return [Goal(**dict(r)) for r in rows]
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────
# 常量形状: SEED_ROTATION 第 10 源 + criteria 模板
# ───────────────────────────────────────────────────────────


class TestB6Constants:
    def test_seed_rotation_b6_10th_source(self):
        assert len(SEED_ROTATION) == 10
        # B6 插在 B5（relation）之後、S1（elevation）之前
        assert SEED_ROTATION[4]["key"] == "relation"
        assert SEED_ROTATION[5] == {"key": "commitment_closure", "axis": AXIS_BRYAN}
        assert SEED_ROTATION[6]["key"] == "elevation"
        assert SEED_ROTATION[6]["axis"] == AXIS_SELF

    def test_criteria_template_interaction_1_7d(self):
        assert _CRITERIA_TEMPLATES["commitment_closure"] == {
            "kind": "interaction",
            "count": 1,
            "timeout_days": 7,
        }


# ───────────────────────────────────────────────────────────
# 候选识别（probe 层直测）
# ───────────────────────────────────────────────────────────


class TestProbeCandidate:
    def test_probe_terminal_relationship_hit(self, iso_env):
        tmp = iso_env
        now = _local(10)
        gid = _gid("x")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
            title="找机会关心 Bry 的面试",
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        hit = prov._probe_commitment_closure(now)
        assert hit is not None
        assert hit.key == "commitment_closure"
        assert hit.axis == AXIS_BRYAN
        assert hit.ref == f"commitment_closure:{gid}"

    def test_probe_terminal_commitment_prefix_hit(self, iso_env):
        """未來 commitment:{id} 寫側命名空間自然涵蓋（§3.2 理由 4）。"""
        tmp = iso_env
        now = _local(10)
        gid = _gid("c")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_ABANDONED,
            ref="commitment:abc123", state_updated_at=now.timestamp() - 7200,
            title="逾期的一件事", advance_count=0,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        hit = prov._probe_commitment_closure(now)
        assert hit is not None
        assert hit.ref == f"commitment_closure:{gid}"

    def test_probe_relation_prefix_excluded(self, iso_env):
        """relation: 為 B5 他者源 → 他者閉環回饋屬 C-3, 不進 B6（§3.3）。"""
        tmp = iso_env
        now = _local(10)
        _insert_goal(
            tmp, goal_id=_gid("r"), state=GOAL_STATE_COMPLETED,
            ref="relation:agent_other", state_updated_at=now.timestamp() - 3600,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        assert prov._probe_commitment_closure(now) is None

    def test_probe_non_terminal_excluded(self, iso_env):
        tmp = iso_env
        now = _local(10)
        for st in (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS):
            _insert_goal(
                tmp, goal_id=_gid(st), state=st,
                ref="relationship:user_bryan",
                state_updated_at=now.timestamp() - 3600,
            )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        assert prov._probe_commitment_closure(now) is None

    def test_probe_self_axis_excluded(self, iso_env):
        tmp = iso_env
        now = _local(10)
        _insert_goal(
            tmp, goal_id=_gid("s"), state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", axis=AXIS_SELF,
            state_updated_at=now.timestamp() - 3600,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        assert prov._probe_commitment_closure(now) is None

    def test_probe_out_of_window_excluded(self, iso_env):
        """窗口下界之前已進入終態 → 不產種子（上窗已輪候過/應已處理）。"""
        tmp = iso_env
        now = _local(10)
        _insert_goal(
            tmp, goal_id=_gid("o"), state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan",
            state_updated_at=now.timestamp() - 26 * 3600,  # last_seed_scan_at 之前
        )
        prov = _seed_provider(tmp, _llm("stub"))
        st = prov._load_state()
        st.last_seed_scan_at = now.timestamp() - 25 * 3600
        st.seed_source_cursor = 5
        prov._save_state(st)
        assert prov._probe_commitment_closure(now) is None

    def test_probe_oldest_first(self, iso_env):
        """窗內多候選 → state_updated_at 最舊優先（時間輪候, 0 打分）。"""
        tmp = iso_env
        now = _local(10)
        newer = _gid("n")
        older = _gid("o2")
        _insert_goal(
            tmp, goal_id=newer, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
        )
        _insert_goal(
            tmp, goal_id=older, state=GOAL_STATE_COMPLETED,
            ref="commitment:zzz", state_updated_at=now.timestamp() - 5 * 3600,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        hit = prov._probe_commitment_closure(now)
        assert hit is not None
        assert hit.ref == f"commitment_closure:{older}"

    def test_probe_material_terminal_facts(self, iso_env):
        """素材只含結構事實（No-Scoring §4.3 不變式 3）: 終態標記/次數/時點。"""
        tmp = iso_env
        now = _local(10)
        gid = _gid("m")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
            title="面试关心", advance_count=2,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        hit = prov._probe_commitment_closure(now)
        assert "已達成" in hit.material
        assert "面试关心" in hit.material
        assert "推進次數=2" in hit.material


# ───────────────────────────────────────────────────────────
# 闭环保流程（scan_seeds 整链）: 一次终态一次种子 + 双保险
# ───────────────────────────────────────────────────────────


class TestClosureScanFlow:
    def test_scan_creates_closure_goal_once(self, iso_env):
        """B6 命中 → 建 ACTIVE goal: ref=commitment_closure:{id}, bryan 轴,
        criteria {interaction, 1, 7d}。"""
        tmp = iso_env
        now = _local(10)
        gid = _gid("f")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
            title="面试关心", advance_count=2,
        )
        prov = _seed_provider(tmp, _llm("找机会回访面试进展", "上次答应过"))
        _set_scan_state(prov, now)
        created = asyncio.run(prov.scan_seeds(now=now))
        assert len(created) == 1
        g = created[0]
        assert g.seed_source_ref == f"commitment_closure:{gid}"
        assert g.axis == AXIS_BRYAN
        assert g.state == GOAL_STATE_ACTIVE
        crit = json.loads(g.completion_criteria)
        assert crit == {"kind": "interaction", "count": 1, "timeout_days": 7}
        assert len(_goals(tmp)) == 2  # 原承諾 + 回饋 goal

    def test_rescan_no_duplicate_cross_window(self, iso_env):
        """窗口判據（主）: 下窗掃描時終態已不在窗內 → 0 重複回饋。"""
        tmp = iso_env
        now1 = _local(10)
        gid = _gid("x")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now1.timestamp() - 3600,
        )
        prov = _seed_provider(tmp, _llm("回饋", "一次關懷"))
        _set_scan_state(prov, now1)
        created = asyncio.run(prov.scan_seeds(now=now1))
        assert len(created) == 1
        # 跨 24h 節流窗再掃描（+26h）: 窗口上移, 終態不再窗內
        now2 = now1 + timedelta(hours=26)
        again = asyncio.run(prov.scan_seeds(now=now2))
        assert again == []
        assert len(_goals(tmp)) == 2  # 0 新增

    def test_existing_non_terminal_closure_blocks(self, iso_env):
        """去重雙保險之二: 已創建未完成的回饋 goal（同 ref 非終態）→ 跳過。"""
        tmp = iso_env
        now = _local(10)
        gid = _gid("y")
        _insert_goal(
            tmp, goal_id=gid, state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
        )
        # 已存在的回饋 goal（上次窗口已建, 尚未完成）
        _insert_goal(
            tmp, goal_id=_gid("fb"), state=GOAL_STATE_ACTIVE,
            ref=f"commitment_closure:{gid}", state_updated_at=now.timestamp() - 100,
            title="回饋中", advance_count=0,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        created = asyncio.run(prov.scan_seeds(now=now))
        assert created == []
        assert len(_goals(tmp)) == 2  # 0 新 goal

    def test_quiet_hours_suppress_b6(self, iso_env):
        """作息相位抑制天然繼承: quiet（23-08）→ B 軸（含 B6）不產種子。"""
        tmp = iso_env
        now = _local(23)  # 23:00 quiet
        _insert_goal(
            tmp, goal_id=_gid("q"), state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        created = asyncio.run(prov.scan_seeds(now=now))
        assert created == []
        assert len(_goals(tmp)) == 1  # 0 新增

    def test_cursor_advances_past_b6(self, iso_env):
        """命中後游標後移（B6 index 5 + 1 = 6 → 下一輪從 S1 起）: 輪序確定性。"""
        tmp = iso_env
        now = _local(10)
        _insert_goal(
            tmp, goal_id=_gid("z"), state=GOAL_STATE_COMPLETED,
            ref="relationship:user_bryan", state_updated_at=now.timestamp() - 3600,
        )
        prov = _seed_provider(tmp, _llm("stub"))
        _set_scan_state(prov, now)
        created = asyncio.run(prov.scan_seeds(now=now))
        assert len(created) == 1
        state_file = tmp / "memory" / AGENT / "goal_provider.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        assert state["seed_source_cursor"] == 6
        assert state["last_seed_axis"] == AXIS_BRYAN
        assert state["seed_axis_streak"] == 1


# ───────────────────────────────────────────────────────────
# 0 新 sidecar 字段（GoalProviderState 未被 C-2.1 擴充）
# ───────────────────────────────────────────────────────────


class TestZeroNewSidecar:
    def test_goal_provider_state_fields_unchanged(self):
        """B6 0 新 sidecar 字段: GoalProviderState 字段集與 LS-2+SG-2 相同。"""
        from dataclasses import fields
        from src.goals.models import GoalProviderState
        names = {f.name for f in fields(GoalProviderState)}
        assert names == {
            "last_candidate_at", "rotation", "consecutive_do_nothing",
            "consecutive_skips", "last_seed_scan_at", "seed_source_cursor",
            "seed_axis_streak", "last_seed_axis", "seed_empty_rounds",
            "last_relation_update_at",
        }