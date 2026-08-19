"""
test_m7_forgetting.py
M7-forgetting (Bry 拍板 2026-08-19): 真正遺忘 + 正向強化

驗證:
  A. _reinforce 正向強化 (被 recall 的記憶 weight 上升)
  B. apply_correction("reinforce") 走新 action
  C. run_scheduled_decay 真的 prune 老 + 低 weight 的事實 (修掉永不 prune bug)
  D. run_scheduled_decay 不 prune 年輕 / 高 weight (有強化) 的事實
"""
import time

from src.memory.sage.evolution import (
    MemoryEvolution,
    PRUNE_AGE_DAYS,
    PRUNE_WEIGHT_THRESHOLD,
    REINFORCEMENT_DELTA,
)
from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact


def _make_fact(age_days, weight, source="user", is_anchor=False):
    return Fact(
        subject="Bry",
        predicate="喜歡",
        object="草莓蛋糕",
        timestamp=time.time() - age_days * 86400,
        weight=weight,
        source=source,
        is_anchor=is_anchor,
    )


class TestReinforce:
    def test_reinforce_boosts_weight(self, tmp_path):
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        fact = _make_fact(age_days=1, weight=1.0)
        fid = store.add_fact(fact)

        ok = evo.apply_correction(fid, "reinforce", delta=REINFORCEMENT_DELTA, reason="test")
        assert ok
        assert store.get_fact(fid).weight == 1.0 + REINFORCEMENT_DELTA
        store.close()

    def test_reinforce_caps_at_2(self, tmp_path):
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        fact = _make_fact(age_days=1, weight=1.99)
        fid = store.add_fact(fact)

        evo.apply_correction(fid, "reinforce", delta=REINFORCEMENT_DELTA, reason="test")
        assert store.get_fact(fid).weight <= 2.0
        store.close()


class TestTruePruning:
    def test_scheduled_decay_prunes_old_low_weight(self, tmp_path):
        """老 (30+ 天) 且 weight 低於門檻 → 真的 prune (修掉永不 prune bug)。"""
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        # 60 天前, weight 0.14 (略低於 PRUNE_WEIGHT_THRESHOLD 0.15)
        fact = _make_fact(age_days=60, weight=0.14, source="user")
        fid = store.add_fact(fact)

        stats = evo.run_scheduled_decay(age_days_threshold=7.0)
        assert stats["pruned"] >= 1, f"老+低 weight 應被 prune, stats={stats}"
        assert store.get_fact(fid) is None
        store.close()

    def test_scheduled_decay_keeps_young_fact(self, tmp_path):
        """年輕 (<=7 天) 的事實不 decay 也不 prune。"""
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        fact = _make_fact(age_days=3, weight=0.14)
        fid = store.add_fact(fact)

        evo.run_scheduled_decay(age_days_threshold=7.0)
        assert store.get_fact(fid) is not None  # 年輕事實保留
        store.close()

    def test_scheduled_decay_keeps_reinforced_fact(self, tmp_path):
        """老但 weight 高 (有被強化) 的事實不 prune。"""
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        fact = _make_fact(age_days=60, weight=0.6)  # 高 weight = 有強化過
        fid = store.add_fact(fact)

        evo.run_scheduled_decay(age_days_threshold=7.0)
        assert store.get_fact(fid) is not None  # 高 weight 不 prune
        store.close()

    def test_scheduled_decay_keeps_anchor(self, tmp_path):
        """anchor 事實永遠不 decay/prune。"""
        store = GraphStore(db_path=tmp_path / "graph.sqlite")
        evo = MemoryEvolution(store)
        fact = _make_fact(age_days=60, weight=0.14, is_anchor=True)
        fid = store.add_fact(fact)

        evo.run_scheduled_decay(age_days_threshold=7.0)
        assert store.get_fact(fid) is not None
        store.close()
