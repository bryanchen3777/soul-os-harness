"""
tests/test_graph_store_confidence.py
Soul OS — LS-2 派生微修复: GraphStore.add_fact 写入 confidence 列

背景: add_fact 的 INSERT 列清单漏列 confidence（DDL v4 迁即有该列, 默认 1.0），
导致所有 fact 的 confidence 恒为 1.0（0.85 等自定义值写不进去）。
修复: INSERT 补上 confidence 列 + 参数元组; 未传时写默认 1.0（与原行为逐字节一致）。

验证:
  1. add_fact(confidence=0.85) → 各读取端精确返回 0.85，reopen 后仍在
  2. 不传 confidence → 1.0（回归保护）
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact


def _make_fact(
    fact_id: str,
    subject: str = "bryan",
    predicate: str = "likes",
    object: str = "coffee",
    confidence: float = 1.0,
    timestamp: Optional[float] = None,
) -> Fact:
    # 相对时间戳 fixture: 相对当前时刻偏移, 避免绝对 epoch 时间炸弹
    return Fact(
        fact_id=fact_id,
        subject=subject,
        predicate=predicate,
        object=object,
        timestamp=time.time() if timestamp is None else timestamp,
        weight=1.0,
        confidence=confidence,
        source="user",
        session_id="test_session",
    )


def test_add_fact_writes_custom_confidence(tmp_path: Path) -> None:
    """add_fact(confidence=0.85) → 各读取端精确返回 0.85，reopen 后仍持久化。"""
    db = tmp_path / "graph.sqlite"
    store = GraphStore(db_path=db)
    store.add_fact(_make_fact("fact_085", confidence=0.85))
    store.flush()

    # 读取端 1: get_fact
    assert store.get_fact("fact_085").confidence == 0.85

    # 读取端 2: get_all_facts
    all_facts = store.get_all_facts(min_weight=0.0)
    assert {f.fact_id: f.confidence for f in all_facts}["fact_085"] == 0.85

    # 读取端 3: get_facts_as_of（retrieve 时序查询路径）
    as_of = store.get_facts_as_of(time.time())
    assert {f.fact_id: f.confidence for f in as_of}["fact_085"] == 0.85

    # 持久化: 重开 store 后 confidence 仍在（证明真的写入 DB，而非内存幻影）
    store.close()
    reopened = GraphStore(db_path=db)
    assert reopened.get_fact("fact_085").confidence == 0.85
    reopened.close()


def test_add_fact_default_confidence_is_1_0(tmp_path: Path) -> None:
    """不传 confidence → 1.0（与 DDL 默认一致，回归保护）。"""
    store = GraphStore(db_path=tmp_path / "graph.sqlite")
    store.add_fact(_make_fact("fact_default"))
    store.flush()

    assert store.get_fact("fact_default").confidence == 1.0
    all_facts = store.get_all_facts(min_weight=0.0)
    assert {f.fact_id: f.confidence for f in all_facts}["fact_default"] == 1.0
    store.close()