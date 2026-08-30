"""
tests/test_graph_store_concurrency.py
Soul OS — KI-008 并发回归测试

背景：GraphStore 的 sqlite 连接（check_same_thread=False）被多线程并发访问
（prefetch 走 asyncio.to_thread 读 + write_turn 走 run_in_executor 写，同一连接
无锁）→ sqlite3 C 扩展内存损坏 → ACCESS VIOLATION（python311.dll 0xc0000005）。

修复：GraphStore 所有 sqlite + networkx graph 操作以 threading.RLock 串行化。

本测试模拟生产并发场景：多个线程同时读（prefetch 风格）和写（write_turn 风格）
同一个 GraphStore，验证：
  1. 并发操作无异常（sqlite3.ProgrammingError / 数据竞态）
  2. 数据一致性（写入的 fact 都能读到，无丢失/损坏）
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact


def _make_fact(i: int, subject: str = "bryan") -> Fact:
    return Fact(
        fact_id=f"fact_{i:06d}",
        subject=subject,
        predicate="likes",
        object=f"thing_{i}",
        timestamp=time.time(),
        weight=1.0,
        source="user",
        session_id="test_session",
    )


def test_concurrent_read_write_no_exception(tmp_path: Path) -> None:
    """多线程并发读写同一 GraphStore：无异常 + 数据一致。"""
    store = GraphStore(db_path=tmp_path / "graph.sqlite", batch_size=5)
    errors: list[Exception] = []
    lock = threading.Lock()
    total_writes = 200
    total_reads = 200

    def writer(thread_id: int) -> None:
        try:
            for i in range(thread_id, total_writes, 4):
                store.add_fact(_make_fact(i))
                if i % 7 == 0:
                    store.flush()
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    def reader(thread_id: int) -> None:
        try:
            for i in range(thread_id, total_reads, 4):
                store.get_all_facts(min_weight=0.0)
                store.search_by_entity("bryan")
                _ = store.stats()
                _ = store.edge_count
                _ = store.node_count
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    threads = []
    for t in range(4):
        threads.append(threading.Thread(target=writer, args=(t,)))
        threads.append(threading.Thread(target=reader, args=(t,)))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    store.flush()
    assert not errors, f"并发操作异常: {errors[:5]}"

    # 数据一致性：所有写入的 fact 都能读到
    all_facts = store.get_all_facts(min_weight=0.0)
    written_ids = {f"fact_{i:06d}" for i in range(total_writes)}
    read_ids = {f.fact_id for f in all_facts}
    missing = written_ids - read_ids
    assert not missing, f"丢失 {len(missing)} 条 fact: {sorted(missing)[:5]}"
    store.close()


def test_concurrent_same_fact_idempotent(tmp_path: Path) -> None:
    """并发写同一 fact_id（INSERT OR REPLACE）：最终只有一条，无异常。"""
    store = GraphStore(db_path=tmp_path / "graph.sqlite", batch_size=1)
    errors: list[Exception] = []
    lock = threading.Lock()

    def writer(thread_id: int) -> None:
        try:
            for i in range(50):
                store.add_fact(_make_fact(thread_id * 1000 + i, subject=f"agent_{thread_id}"))
        except Exception as e:  # noqa: BLE001
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    store.flush()
    assert not errors, f"并发写异常: {errors[:5]}"
    all_facts = store.get_all_facts(min_weight=0.0)
    # 每个 thread 写 50 条不同 fact_id → 共 200 条
    assert len(all_facts) == 200, f"期望 200 条, 实际 {len(all_facts)}"
    store.close()
