"""
tests/goals/test_sg2_b5_relation_seed.py — SG-2 B5 他者源种子验收（D5）

覆盖: band≥known 出种子 / stranger 0 种子 / impression_tags 非空也出 /
24h 节流 / 幂等去重 / B 轴作息抑制 / 0 直写 facts。

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/goals/test_sg2_b5_relation_seed.py -v
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.goals.models import AXIS_BRYAN, GOAL_STATE_ACTIVE, Goal
from src.goals.motive_provider import reset_goal_providers
from src.goals.seed_provider import GoalSeedProvider, reset_seed_providers
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root

AGENT = "agent_sg2"
ROOT = Path(__file__).resolve().parents[2]


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


# ───────────────────────────────────────────────────────────
# helpers
# ───────────────────────────────────────────────────────────

def _write_relationships(tmp_path: Path, others: Dict[str, Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "agent_id": AGENT,
        "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": others,
    }
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _62_entry(band: str, tags: List[str], **kwargs) -> Dict[str, Any]:
    entry = {
        "impression": "優しい人",
        "feeling": "neutral",
        "confidence": 0.0,
        "interaction_count": 3,
        "last_interaction_at": "2026-09-05T10:00:00+00:00",
        "last_updated": "2026-09-05T10:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
        "objective": {
            "reply_exchanges": 2,
            "co_presence_sessions": 4,
            "dream_exchanges": 0,
            "last_signal_at": "2026-09-05T10:00:00+00:00",
        },
        "impression_tags": tags,
        "relational_band": band,
        "band_updated_at": "2026-09-05T10:00:00+00:00",
        "last_relation_update_ref": "rel:2026-09-05T10:00:00+00:00",
    }
    entry.update(kwargs)
    return entry


def _llm(title: str = "想支持 Rem 的想法", desc: str = "内心独白（stub）"):
    async def stub(messages, agent_id, max_tokens, temperature):
        return json.dumps({"title": title, "description": desc})
    return stub


def _local(hour: int = 12, day: int = 0) -> datetime:
    return datetime(2026, 9, 6, hour).astimezone() + timedelta(days=day)


def _seed_provider(tmp_path: Path, llm: Any) -> GoalSeedProvider:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    store = GraphStore(db_path=db)
    return GoalSeedProvider(agent_id=AGENT, store=store, llm_call=llm)


def _goals(tmp_path: Path) -> List[Goal]:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()
        return [Goal(**dict(r)) for r in rows]
    finally:
        conn.close()


def _facts_count(tmp_path: Path) -> int:
    db = tmp_path / "memory" / AGENT / "graph.sqlite"
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0])
    finally:
        conn.close()


# ───────────────────────────────────────────────────────────
# B5 出种子规则
# ───────────────────────────────────────────────────────────

class TestRelationSeedEmission:
    def test_band_known_emits_seed(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("known", [])})
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        g = created[0]
        assert g.seed_source_ref == "relation:agent_rem"
        assert g.axis == AXIS_BRYAN
        assert g.state == GOAL_STATE_ACTIVE
        assert "agent_rem" in g.title or "Rem" in g.title or g.title == "想支持 Rem 的想法"
        # criteria 模板: relation → interaction/2/14
        crit = json.loads(g.completion_criteria)
        assert crit == {"kind": "interaction", "count": 2, "timeout_days": 14}

    def test_band_familiar_and_close_emit(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {
            "agent_rem": _62_entry("familiar", ["warm"]),
            "agent_ruka": _62_entry("close", ["brilliant"]),
        })
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        # 插入序最早命中 → agent_rem（确定性, 0 排序打分）
        assert created[0].seed_source_ref == "relation:agent_rem"
        # 静态 ref 幂等: agent_rem 追踪后该源空转（对齐 B1 relationship:user_bryan
        # 先例: 每个 other 至多 1 个关系种子）; S 轴无数据 → 后续轮 0 新 goal
        for day in (1, 2, 3):
            got = asyncio.run(prov.scan_seeds(now=_local(12, day=day)))
            assert got == []
        refs = [g.seed_source_ref for g in _goals(tmp)]
        assert refs == ["relation:agent_rem"]

    def test_stranger_no_seed(self, iso_env):
        """stranger 不出种子（band=stranger 且无 tags）。"""
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("stranger", [])})
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        # B1-B5 全部空（无 Bry entry / 无合格他者）→ 0 创建
        assert created == []
        assert _goals(tmp) == []

    def test_stranger_with_tags_emits(self, iso_env):
        """契约: 只有 band≥known 或 impression_tags 非空才产种子（stranger+tags 也出）。"""
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("stranger", ["quiet"])})
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        assert created[0].seed_source_ref == "relation:agent_rem"

    def test_user_bryan_skipped_by_relation_source(self, iso_env):
        """B5 只管他者（user_bryan 维度由 B1 commitment 覆盖）。"""
        tmp = iso_env
        _write_relationships(tmp, {
            "user_bryan": _62_entry("close", ["warm"]),
        })
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        # B1 commitment 会命中 user_bryan（既有行为）→ 种子来自 B1 而非 B5
        assert len(created) == 1
        assert created[0].seed_source_ref == "relationship:user_bryan"

    def test_material_contains_band_and_tags(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("familiar", ["warm", "quiet"])})
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        hit = prov._probe("relation", _local(12))
        assert hit is not None
        assert "band=familiar" in hit.material
        assert "warm" in hit.material


# ───────────────────────────────────────────────────────────
# 节流 / 幂等 / B 轴抑制
# ───────────────────────────────────────────────────────────

class TestRelationSeedThrottle:
    def test_24h_throttle(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("known", [])})
        prov = _seed_provider(tmp, _llm())
        created = asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(created) == 1
        # 同窗（+1h）→ 节流 0 创建
        again = asyncio.run(prov.scan_seeds(now=_local(13)))
        assert again == []
        assert len(_goals(tmp)) == 1

    def test_idempotent_same_ref_cross_window(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("known", [])})
        prov = _seed_provider(tmp, _llm())
        asyncio.run(prov.scan_seeds(now=_local(12)))
        # 跨 24h 后同 other 同 ref → 幂等 0 新 goal
        after = asyncio.run(prov.scan_seeds(now=_local(12, day=1)))
        assert after == []
        assert len(_goals(tmp)) == 1
        assert _goals(tmp)[0].seed_source_ref == "relation:agent_rem"

    def test_quiet_hours_suppress_relation(self, iso_env):
        """B 轴作息抑制自然继承: quiet 时段 B5 relation 不出种子。"""
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("close", ["warm"])})
        prov = _seed_provider(tmp, _llm())
        # 凌晨 02:00 → bryan_suppressed → B1-B5 全跳过 → 0 创建
        created = asyncio.run(prov.scan_seeds(now=_local(2)))
        assert created == []
        assert _goals(tmp) == []

    def test_zero_direct_facts(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {"agent_rem": _62_entry("familiar", ["warm"])})
        prov = _seed_provider(tmp, _llm())
        asyncio.run(prov.scan_seeds(now=_local(12)))
        assert len(_goals(tmp)) == 1
        assert _facts_count(tmp) == 0  # 0 直写 SAGE facts