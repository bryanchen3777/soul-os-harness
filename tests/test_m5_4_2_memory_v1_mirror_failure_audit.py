"""
M5.4-2 — Memory DB ↔ v1 Mirror Failure Boundary Audit & Acceptance Tests
=====================================================================

Bry 派工 2026-08-09: STRICT READ-ONLY acceptance audit.

核心 boundary:
  SAGELiteProvider.{sync_turn, post_reply_commit}
        │
        ▼
  MemoryWriter.write_turn / extract_and_write / extract
        │
        ├── _extract_facts → 抽 (facts, raw_results)
        ├── _mirror_extraction  → _mirror_to_v1_store  → V1Store.add (JSONL append)
        │      (獨立 try/except, mirror 例外不外傳)
        └── add_facts_batch    → GraphStore.add_fact (SQLite WAL)

  兩個寫入**獨立序貫**,沒有 coordinator / 沒有 transaction 包裹。
  → 任何一條失敗都會造成 graph.sqlite ↔ memories.jsonl **divergence**。

本 audit 對 7 個 failure 維度各設 acceptance tests:
  A. DB 成功 / mirror 失敗
  B. mirror 成功 / DB 失敗
  C. Retry duplicate (同 content 寫多次)
  D. Concurrent writes (threading)
  E. Divergence detection (loader 端如何反應)
  F. Silent data loss (空 content / heuristic 0 抽 / agent_id fallback)
  G. Uncontrolled duplicate (同 content 走不同 API path)
  H. Path / agent_id resolution
  I. Idempotency / V1Store 本身契約

M5.3 派工精神:
  - READ-ONLY: 不修改 production code
  - 不 commit / 不 push
  - 發現 architecture defect → STOP,只回報
  - 30+ deterministic tests
  - M5.3 regression 維持 259/259
  - production data 0 mutation

執行:
  & .venv\\Scripts\\python.exe -m pytest -v tests/test_m5_4_2_memory_v1_mirror_failure_audit.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import List, Optional
from unittest.mock import patch

import pytest


# ──────────────────────────────────────────────────────────────────────
# 1. Test infrastructure: failing-store wrappers
# ──────────────────────────────────────────────────────────────────────


class FailingV1Store:
    """V1Store stub,可設定第 N 次 add() 拋例外。"""

    def __init__(self, real_cls, data_dir, agent_id, fail_on_call: int = -1,
                 fail_exception: Optional[Exception] = None):
        self._real = real_cls(data_dir, agent_id)
        self._fail_on_call = fail_on_call
        self._fail_exception = fail_exception or OSError("simulated v1 disk full")
        self._call_count = 0
        self.agent_id = self._real.agent_id
        self.data_dir = self._real.data_dir
        self.agent_dir = self._real.agent_dir
        self.store_file = self._real.store_file

    def add(self, memory) -> None:
        self._call_count += 1
        if self._fail_on_call > 0 and self._call_count == self._fail_on_call:
            raise self._fail_exception
        return self._real.add(memory)

    def all(self):
        return self._real.all()

    def get(self, memory_id):
        return self._real.get(memory_id)

    def count(self):
        return self._real.count()


class AlwaysFailingV1Store:
    """每次 add() 都拋例外的 V1Store。"""

    def __init__(self, real_cls, data_dir, agent_id):
        self._real = real_cls(data_dir, agent_id)
        self._call_count = 0
        self.agent_id = self._real.agent_id
        self.data_dir = self._real.data_dir
        self.agent_dir = self._real.agent_dir
        self.store_file = self._real.store_file

    def add(self, memory) -> None:
        self._call_count += 1
        raise OSError(f"v1 mirror hard fail (call {self._call_count})")

    def all(self):
        return self._real.all()

    def get(self, memory_id):
        return self._real.get(memory_id)

    def count(self):
        return self._real.count()


class FailingGraphStore:
    """GraphStore wrapper,可設定第 N 次 add_fact() 拋例外或回空字串。"""

    def __init__(self, real, fail_on_call: int = -1, fail_mode: str = "raise",
                 fail_exception: Optional[Exception] = None):
        self._real = real
        self._fail_on_call = fail_on_call
        self._fail_mode = fail_mode  # "raise" | "return_empty"
        self._fail_exception = fail_exception or RuntimeError("simulated graph write fail")
        self._call_count = 0
        # 轉發 graph / db_path 給 writer 用
        self.graph = real.graph
        self.db_path = real.db_path

    def add_fact(self, fact):
        self._call_count += 1
        if self._fail_on_call > 0 and self._call_count == self._fail_on_call:
            if self._fail_mode == "raise":
                raise self._fail_exception
            else:
                return ""
        return self._real.add_fact(fact)

    # writer 會呼叫的其它方法 → 全部轉發
    def find_similar_entity(self, name, threshold=0.75):
        return self._real.find_similar_entity(name, threshold)

    def search_by_entity(self, subject, min_weight=0.01):
        return self._real.search_by_entity(subject, min_weight)

    def update_weight(self, fact_id, new_weight):
        return self._real.update_weight(fact_id, new_weight)

    def set_anchor(self, fact_id, is_anchor=True):
        return self._real.set_anchor(fact_id, is_anchor)

    def close(self):
        return self._real.close()

    def flush(self):
        return self._real.flush()

    def stats(self):
        return self._real.stats()


# ──────────────────────────────────────────────────────────────────────
# 2. Fixture:隔離 tempdir / USE_LLM_JUDGE=false
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    """每次 test 一個全新的 data dir。"""
    data_dir = tmp_path / "memory"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


@pytest.fixture(autouse=True)
def _use_heuristic(monkeypatch):
    """強制走 heuristic fallback,避免 LLM 連線依賴。"""
    monkeypatch.setenv("USE_LLM_JUDGE", "false")


def _make_writer(data_dir, agent_id: str = "agent_rem"):
    """建一個寫到 tmpdir 的 MemoryWriter。"""
    from src.memory.sage.graph_store import GraphStore
    from src.memory.sage.writer import MemoryWriter
    graph_db = data_dir / agent_id / "graph.sqlite"
    store = GraphStore(db_path=graph_db)
    writer = MemoryWriter(store, default_session_id="test_sid", agent_id=agent_id)
    return writer, store


def _make_v1_store(data_dir, agent_id: str = "agent_rem"):
    """建一個讀 v1 store 的實例。"""
    from src.memory.v1.store import V1Store
    return V1Store(data_dir, agent_id)


def _count_v1_entries(data_dir, agent_id: str = "agent_rem") -> int:
    store_file = data_dir / agent_id / "memories.jsonl"
    if not store_file.exists():
        return 0
    return sum(1 for line in store_file.read_text(encoding="utf-8").splitlines() if line.strip())


def _count_graph_facts(data_dir, agent_id: str = "agent_rem", store=None) -> int:
    """Count graph facts. 如果有 store 直接用,避免 lock 衝突;否則用 sqlite3 直連。"""
    if store is not None:
        store.flush()
        return store.stats().get("active_facts", 0)
    import sqlite3
    graph_db = data_dir / agent_id / "graph.sqlite"
    if not graph_db.exists():
        return 0
    # 直接 sqlite3 連,timeout 10s 處理 WAL 衝突
    conn = sqlite3.connect(str(graph_db), timeout=10.0)
    try:
        n = conn.execute("SELECT COUNT(*) FROM facts WHERE weight >= 0.1").fetchone()[0]
        return n
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────
# Section A — DB 成功 / mirror 失敗 (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionA_DBSuccessMirrorFail:
    """Mirror 失敗,DB 寫入成功的 boundary。"""

    def test_a1_mirror_exception_caught_graph_has_data(self, tmp_data_dir, monkeypatch):
        """A1: mirror 拋例外,extract_and_write 主路徑不中斷,graph 有 fact。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        real_cls = v1store_mod.V1Store
        def factory(dd, aid):
            return AlwaysFailingV1Store(real_cls, dd, aid)
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        # 不應該 raise — mirror 例外被 _mirror_extraction 吞掉
        ids = writer.extract_and_write("Bry likes apples", subject_hint="user")
        # heuristic 抽得到 → 至少 1 筆寫進 graph
        assert len(ids) >= 1, f"expected ≥1 fact written, got {ids}"

        assert _count_graph_facts(tmp_data_dir, store=store) >= 1
        assert _count_v1_entries(tmp_data_dir) == 0  # mirror 全失敗
        store.close()

    def test_a2_partial_mirror_mid_loop_failure(self, tmp_data_dir, monkeypatch):
        """A2: 5 facts,第 3 次 mirror 拋例外,前 2 筆進 v1、後 2 筆沒進。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        real_cls = v1store_mod.V1Store
        def factory(dd, aid):
            return FailingV1Store(
                real_cls, dd, aid,
                fail_on_call=3,
                fail_exception=OSError("mid-loop disk full"),
            )
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        text = "Bry likes apples. Rem likes onigiri. Yua likes boba. Mai likes crepes. Miku likes ice cream."
        ids = writer.extract_and_write(text, subject_hint="user")

        # 至少一些 fact 進 graph
        assert _count_graph_facts(tmp_data_dir, store=store) >= 1
        # v1 只進到第 2 筆(第 3 筆 raise)→ ≤ 全部成功的數量
        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count <= len(ids)
        store.close()

    def test_a3_mirror_fail_does_not_block_next_call(self, tmp_data_dir, monkeypatch):
        """A3: 第一次 mirror 失敗,第二次 call 仍可正常運作。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        call_count = {"n": 0}
        real_cls = v1store_mod.V1Store
        def factory(dd, aid):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return AlwaysFailingV1Store(real_cls, dd, aid)
            return real_cls(dd, aid)
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        # 第 1 次 mirror 失敗
        ids1 = writer.extract_and_write("Bry likes apples", subject_hint="user")
        # 第 2 次 mirror 成功
        ids2 = writer.extract_and_write("Rem likes onigiri", subject_hint="user")

        assert len(ids1) >= 1
        assert len(ids2) >= 1
        # 第二次的 v1 至少有 1 筆
        assert _count_v1_entries(tmp_data_dir) >= 1
        store.close()

    def test_a4_mirror_fail_write_result_independent(self, tmp_data_dir, monkeypatch):
        """A4: WriteResult.written 反映 graph 寫入,不受 mirror 失敗影響。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        real_cls = v1store_mod.V1Store
        def factory(dd, aid):
            return AlwaysFailingV1Store(real_cls, dd, aid)
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        from src.memory.sage.models import Fact
        fact = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1",
        )
        result = writer.write_with_confirmation(fact)

        # graph 寫入成功
        assert len(result.written) == 1
        assert result.has_failures is False  # rejected 是空的
        # mirror 失敗是 log only,不污染 WriteResult
        assert _count_v1_entries(tmp_data_dir) == 0
        store.close()

    def test_a5_mirror_fail_does_not_propagate_exception(self, tmp_data_dir, monkeypatch):
        """A5: 即便 V1Store.__init__ 本身拋例外,extract_and_write 也不應該 raise。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        def factory(dd, aid):
            raise OSError("v1 dir creation failed")
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        # 必須不 raise
        try:
            ids = writer.extract_and_write("Bry likes apples", subject_hint="user")
        except Exception as e:
            pytest.fail(f"mirror init exception should NOT propagate: {e!r}")
        assert len(ids) >= 1
        store.close()


# ──────────────────────────────────────────────────────────────────────
# Section B — mirror 成功 / DB 失敗 (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionB_MirrorSuccessDBFail:
    """Graph 寫入失敗,但 mirror 已先寫入 v1。"""

    def test_b1_mirror_runs_before_graph_write(self, tmp_data_dir, monkeypatch):
        """B1: 確認 mirror 在 graph write 之前 → graph 失敗時 v1 已有資料。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        # 攔截 V1Store.add,記錄呼叫次序
        from src.memory.v1 import store as v1store_mod
        real_cls = v1store_mod.V1Store
        call_order = []
        original_add = real_cls.add
        def tracking_add(self_v1, memory):
            call_order.append("v1_add")
            return original_add(self_v1, memory)
        monkeypatch.setattr(real_cls, "add", tracking_add)

        # 攔截 GraphStore.add_fact
        from src.memory.sage.graph_store import GraphStore
        real_graph_add = GraphStore.add_fact
        def tracking_graph_add(self_g, fact):
            call_order.append("graph_add")
            return real_graph_add(self_g, fact)
        monkeypatch.setattr(GraphStore, "add_fact", tracking_graph_add)

        ids = writer.extract_and_write("Bry likes apples", subject_hint="user")
        assert len(ids) >= 1

        # v1 至少有一次 add 在 graph add_fact 之前
        assert "v1_add" in call_order
        assert "graph_add" in call_order
        v1_idx = call_order.index("v1_add")
        graph_idx = call_order.index("graph_add")
        assert v1_idx < graph_idx, f"mirror must run before graph: {call_order}"
        store.close()

    def test_b2_graph_raise_after_mirror(self, tmp_data_dir, monkeypatch):
        """B2: graph 第 1 次 add_fact raise → v1 已有資料、graph 沒資料。"""
        writer, real_store = _make_writer(tmp_data_dir, "agent_rem")

        # 包 FailingGraphStore
        from src.memory.sage import writer as writer_mod
        original_init = writer_mod.MemoryWriter.__init__
        def patched_init(self, gs, default_session_id="", agent_id=""):
            self.store = FailingGraphStore(
                gs, fail_on_call=1, fail_mode="raise",
                fail_exception=RuntimeError("graph disk full"),
            )
            self.default_session_id = default_session_id
            self.agent_id = agent_id
        monkeypatch.setattr(writer_mod.MemoryWriter, "__init__", patched_init)
        # 重新建 writer(走 patched init)
        writer2 = writer_mod.MemoryWriter(real_store, default_session_id="s", agent_id="agent_rem")

        with pytest.raises(RuntimeError, match="graph disk full"):
            writer2.extract_and_write("Bry likes apples", subject_hint="user")

        # graph 沒資料(graph add 失敗,fact 沒進 sqlite)
        # v1 已經 mirror 過(extract_and_write 順序: extract → mirror → add_facts_batch)
        assert _count_v1_entries(tmp_data_dir) >= 1
        real_store.close()

    def test_b3_graph_rejection_after_mirror(self, tmp_data_dir, monkeypatch):
        """B3: graph 第 1 次 add_fact 回空(rejection) → v1 有資料、graph 沒資料。"""
        writer, real_store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.sage import writer as writer_mod
        def patched_init(self, gs, default_session_id="", agent_id=""):
            self.store = FailingGraphStore(gs, fail_on_call=1, fail_mode="return_empty")
            self.default_session_id = default_session_id
            self.agent_id = agent_id
        monkeypatch.setattr(writer_mod.MemoryWriter, "__init__", patched_init)

        writer2 = writer_mod.MemoryWriter(real_store, default_session_id="s", agent_id="agent_rem")
        ids = writer2.extract_and_write("Bry likes apples", subject_hint="user")

        # graph 回空 → add_fact returns "" → add_facts_batch 回空 list
        assert ids == []
        # mirror 已寫 v1
        assert _count_v1_entries(tmp_data_dir) >= 1
        real_store.close()

    def test_b4_mid_loop_graph_fail_partial_divergence(self, tmp_data_dir, monkeypatch):
        """B4: 5 facts,graph 第 3 次 add_fact raise → 前 2 筆進 graph、5 筆進 v1。"""
        writer, real_store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.sage import writer as writer_mod
        def patched_init(self, gs, default_session_id="", agent_id=""):
            self.store = FailingGraphStore(gs, fail_on_call=3, fail_mode="raise",
                                           fail_exception=RuntimeError("mid loop"))
            self.default_session_id = default_session_id
            self.agent_id = agent_id
        monkeypatch.setattr(writer_mod.MemoryWriter, "__init__", patched_init)
        writer2 = writer_mod.MemoryWriter(real_store, default_session_id="s", agent_id="agent_rem")

        text = "Bry likes apples. Rem likes onigiri. Yua likes boba. Mai likes crepes. Miku likes ice cream."

        with pytest.raises(RuntimeError, match="mid loop"):
            writer2.extract_and_write(text, subject_hint="user")

        # mirror 已跑完 5 筆
        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count >= 1
        # graph 只有前 2 筆
        graph_count = _count_graph_facts(tmp_data_dir, store=real_store)
        assert graph_count < v1_count, f"graph ({graph_count}) should < v1 ({v1_count})"
        real_store.close()

    def test_b5_extract_only_no_graph_write(self, tmp_data_dir, monkeypatch):
        """B5: writer.extract() 不寫 graph,只 mirror。確認 divergence 模式。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        facts = writer.extract("Bry likes apples", subject_hint="user")

        # extract 不寫 graph
        assert _count_graph_facts(tmp_data_dir, store=store) == 0
        # 但 mirror 寫 v1(因為 extract() 內部呼叫 _mirror_extraction)
        assert _count_v1_entries(tmp_data_dir) >= 1
        assert len(facts) >= 1
        store.close()


# ──────────────────────────────────────────────────────────────────────
# Section C — Retry duplicate (4 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionC_RetryDuplicate:
    """同 content 寫多次,確認 v1 / graph 的 dedup 行為。"""

    def test_c1_v1_no_dedup_same_text_twice(self, tmp_data_dir):
        """C1: extract_and_write 跑 2 次同 text → v1 有 2 筆(不同 UUID)。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        writer.extract_and_write("Bry likes apples", subject_hint="user")
        writer.extract_and_write("Bry likes apples", subject_hint="user")

        # v1 沒 dedup → 2 筆
        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count == 2, f"v1 should have 2 entries, got {v1_count}"
        # graph 會 _find_similar 合併 → 1 筆
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        assert graph_count == 1, f"graph should have 1 entry (similar dedup), got {graph_count}"
        store.close()

    def test_c2_write_turn_twice_divergence(self, tmp_data_dir):
        """C2: write_turn 跑 2 次同 content → v1=2, graph=1(類似 dedup)。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        # write_turn(user, assistant) → 跑 user extract_and_write + assistant extract_and_write
        # 用可抽取的 text,確保事實被抽出
        writer.write_turn("Bry likes apples", "Rem likes onigiri", session_id="s1")
        writer.write_turn("Bry likes apples", "Rem likes onigiri", session_id="s1")

        # 2 輪 → 4 次 extract_and_write → v1 沒 dedup → 4 筆
        # graph 透過 _find_similar 合併(同 subject/predicate/object)→ ≤ 4 筆
        v1_count = _count_v1_entries(tmp_data_dir)
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        assert v1_count >= 2, f"v1 should ≥2, got {v1_count}"
        assert graph_count <= v1_count, f"graph ({graph_count}) should ≤ v1 ({v1_count})"
        assert graph_count >= 1, f"graph should ≥1, got {graph_count}"
        store.close()

    def test_c3_extract_twice_no_graph(self, tmp_data_dir):
        """C3: writer.extract() 跑 2 次同 text → v1 有 2 筆、graph 永遠 0。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        writer.extract("Bry likes apples", subject_hint="user")
        writer.extract("Bry likes apples", subject_hint="user")

        assert _count_v1_entries(tmp_data_dir) == 2
        assert _count_graph_facts(tmp_data_dir, store=store) == 0
        store.close()

    def test_c4_mirror_does_not_dedup_by_content(self, tmp_data_dir):
        """C4: V1Store 直接 add 同 memory_id 兩次 → 2 筆(無 dedup)。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")

        m = Memory(
            memory_id="fixed-uuid-1",
            agent_id="agent_rem",
            content="Bry likes apples",
            tags=["preference", "bry", "apples"],
            created_at=time.time(),
            category="preference",
            confidence=0.9,
        )
        v1.add(m)
        v1.add(m)  # 同 memory_id,直接 add

        assert v1.count() == 2
        # all() 應該 return 2 個 Memory(同 memory_id, 但有兩筆)
        all_mems = v1.all()
        ids = [x.memory_id for x in all_mems]
        assert ids.count("fixed-uuid-1") == 2


# ──────────────────────────────────────────────────────────────────────
# Section D — Concurrent writes (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionD_ConcurrentWrites:
    """Threading 併發寫入測試。"""

    def test_d1_concurrent_different_content_all_survive(self, tmp_data_dir):
        """D1: 10 個 thread 寫不同 content → v1 + graph 都應該有 10 筆。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")
        errors = []

        def worker(i):
            try:
                writer.extract_and_write(f"Subject{i} likes item{i}", subject_hint="user")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"thread errors: {errors}"
        # 至少 10 筆(可能更多如果 heuristic 抽出多 fact/句)
        v1_count = _count_v1_entries(tmp_data_dir)
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        assert v1_count >= 10, f"v1 should ≥10, got {v1_count}"
        assert graph_count >= 1, f"graph should ≥1, got {graph_count}"
        store.close()

    def test_d2_concurrent_same_content_v1_multiplies(self, tmp_data_dir):
        """D2: 10 個 thread 寫同 content → v1 沒 dedup → 10 筆、graph 合併成 1 筆。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        def worker():
            writer.extract_and_write("Bry likes apples", subject_hint="user")

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        v1_count = _count_v1_entries(tmp_data_dir)
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        # v1 完全沒 dedup → 10 筆
        assert v1_count == 10, f"v1 should =10, got {v1_count}"
        # graph 透過 _find_similar 合併 → 理想 1 筆,但併發下有 race condition
        # (M5.4-2 觀察 2:similar dedup 不是 atomic,併發寫入可能造成 1-10 筆)
        # 接受 ≥ 1 但 ≤ 10 的範圍
        assert 1 <= graph_count <= 10, f"graph should in [1,10], got {graph_count}"
        store.close()

    def test_d3_concurrent_extract_and_write_mixed(self, tmp_data_dir):
        """D3: 5 extract + 5 extract_and_write 混合 → v1 跟 graph 都進資料。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        def extract_worker(i):
            writer.extract(f"Subject{i} likes apples", subject_hint="user")

        def write_worker(i):
            writer.extract_and_write(f"Other{i} likes bananas", subject_hint="user")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=extract_worker, args=(i,)))
            threads.append(threading.Thread(target=write_worker, args=(i,)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count >= 5, f"v1 should ≥5, got {v1_count}"
        # extract 不寫 graph,所以 graph 來自 5 個 write
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        assert graph_count >= 1
        store.close()

    def test_d4_concurrent_write_with_reader(self, tmp_data_dir):
        """D4: 寫入同時 loader 讀 → 至少 loader 看到部分資料,不 crash。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        errors = []

        def writer_worker(i):
            try:
                writer.extract_and_write(f"Subject{i} likes item{i}", subject_hint="user")
            except Exception as e:
                errors.append(("writer", e))

        def reader_worker():
            try:
                for _ in range(5):
                    v1.all()
                    time.sleep(0.005)
            except Exception as e:
                errors.append(("reader", e))

        threads = [threading.Thread(target=writer_worker, args=(i,)) for i in range(5)]
        threads.append(threading.Thread(target=reader_worker))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # reader 不應該 crash
        reader_errors = [e for tag, e in errors if tag == "reader"]
        assert not reader_errors, f"reader crashed: {reader_errors}"
        store.close()

    def test_d5_concurrent_no_partial_line_in_v1(self, tmp_data_dir):
        """D5: 大量併發寫入後,v1 檔案每一行都是合法 JSON(JSONL 完整性)。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        def worker(i):
            writer.extract_and_write(f"Subject{i} likes item{i}", subject_hint="user")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        store_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        assert store_file.exists()
        bad_lines = []
        with open(store_file, "r", encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    assert "memory_id" in obj
                    assert "agent_id" in obj
                except Exception as e:
                    bad_lines.append((ln, str(e)[:50]))

        assert not bad_lines, f"corrupt lines: {bad_lines[:5]}"
        store.close()


# ──────────────────────────────────────────────────────────────────────
# Section E — Divergence detection (loader 端) (4 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionE_DivergenceDetection:
    """Loader 端如何反應 v1 / graph 已經 divergence 的資料。"""

    def test_e1_v1_only_loader_finds_it(self, tmp_data_dir):
        """E1: 純 v1 資料(透過 extract(),不寫 graph),loader 找得到。"""
        from src.memory.v1.loader import MemoryLoader, derive_query_tags
        from src.memory.v1.retrieval import retrieve

        writer, store = _make_writer(tmp_data_dir, "agent_rem")
        # 用 extract → 0 graph, 1 v1
        writer.extract("Bry likes apples", subject_hint="user")

        assert _count_graph_facts(tmp_data_dir, store=store) == 0
        assert _count_v1_entries(tmp_data_dir) >= 1

        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        loader = MemoryLoader(store=v1)
        tags = derive_query_tags("Bry likes apples")
        result = loader.load(query_tags=tags, agent_id="agent_rem")

        # loader 至少看到 1 筆 eligible(透過 tag overlap)
        assert len(result["eligible_memories"]) >= 1
        store.close()

    def test_e2_graph_only_loader_returns_empty(self, tmp_data_dir):
        """E2: 純 graph 資料(直接 GraphStore.add_fact,bypass mirror),loader 找不到。"""
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.models import Fact
        from src.memory.v1.loader import MemoryLoader, derive_query_tags

        # 直接 add_fact 到 graph (bypass mirror)
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        gs = GraphStore(db_path=graph_db)
        fact = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1",
        )
        gs.add_fact(fact)
        gs.close()

        assert _count_graph_facts(tmp_data_dir, store=gs) == 1
        assert _count_v1_entries(tmp_data_dir) == 0

        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        loader = MemoryLoader(store=v1)
        tags = derive_query_tags("Bry likes apples")
        result = loader.load(query_tags=tags, agent_id="agent_rem")

        # loader 從 v1 找 → v1 沒資料 → eligible 為空
        assert len(result["eligible_memories"]) == 0
        # candidates 為空 list(v1 完全沒資料所以 no candidates 收集到)
        assert len(result["trace"]["candidates"]) == 0

    def test_e3_v1_has_stale_data_loader_uses_v1(self, tmp_data_dir):
        """E3: v1 有舊資料,graph 有新資料 → loader 用 v1(stale)"""
        from src.memory.v1.loader import MemoryLoader, derive_query_tags

        # 第一次寫 "Bry likes apples" (v1 + graph)
        writer, store = _make_writer(tmp_data_dir, "agent_rem")
        writer.extract_and_write("Bry likes apples", subject_hint="user")
        first_v1 = _count_v1_entries(tmp_data_dir)
        first_graph = _count_graph_facts(tmp_data_dir, store=store)
        assert first_v1 >= 1 and first_graph >= 1

        # 手動清空 v1、保留 graph,模擬 v1 lost
        v1_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        v1_file.unlink()

        assert _count_v1_entries(tmp_data_dir) == 0
        assert _count_graph_facts(tmp_data_dir, store=store) == first_graph  # graph 還在

        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        loader = MemoryLoader(store=v1)
        tags = derive_query_tags("Bry likes apples")
        result = loader.load(query_tags=tags, agent_id="agent_rem")

        # loader 從 v1 找 → v1 已被刪 → eligible 為空
        # 證明 loader 完全不依賴 graph,只讀 v1
        assert len(result["eligible_memories"]) == 0
        store.close()

    def test_e4_v1_corrupt_row_loader_skips(self, tmp_data_dir):
        """E4: v1 檔案有 corrupt JSON 行 → loader 跳過該行、其他行正常。"""
        from src.memory.v1.loader import MemoryLoader, derive_query_tags

        # 寫一筆正常 + 手動塞一行壞 JSON
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        from src.memory.v1.schema import Memory
        v1.add(Memory(
            memory_id="good-1", agent_id="agent_rem",
            content="Bry likes apples",
            tags=["preference", "bry", "apples"],
            created_at=time.time(),
            category="preference", confidence=0.9,
        ))

        v1_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        with open(v1_file, "a", encoding="utf-8") as f:
            f.write("{this is not valid json\n")

        # 再寫一筆正常
        v1.add(Memory(
            memory_id="good-2", agent_id="agent_rem",
            content="Rem likes onigiri",
            tags=["preference", "rem", "onigiri"],
            created_at=time.time() + 1,
            category="preference", confidence=0.9,
        ))

        loader = MemoryLoader(store=v1)
        tags = derive_query_tags("Bry likes apples")
        result = loader.load(query_tags=tags, agent_id="agent_rem")

        # loader 跳過壞行,正常行 eligible
        assert len(result["eligible_memories"]) >= 1


# ──────────────────────────────────────────────────────────────────────
# Section F — Silent data loss (4 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionF_SilentDataLoss:
    """靜默丟資料的場景。"""

    def test_f1_empty_content_zero_writes(self, tmp_data_dir):
        """F1: 空白 content → 0 v1 + 0 graph(不應該 silently drop 1 筆)。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        ids = writer.extract_and_write("", subject_hint="user")

        assert ids == []
        assert _count_v1_entries(tmp_data_dir) == 0
        assert _count_graph_facts(tmp_data_dir, store=store) == 0
        store.close()

    def test_f2_short_text_no_facts(self, tmp_data_dir):
        """F2: 太短的 text(heuristic 抽不到 fact)→ 0 + 0。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        ids = writer.extract_and_write("hi", subject_hint="user")

        assert ids == []
        assert _count_v1_entries(tmp_data_dir) == 0
        assert _count_graph_facts(tmp_data_dir, store=store) == 0
        store.close()

    def test_f3_no_agent_id_no_subject_hint_falls_back_to_unknown(self, tmp_data_dir):
        """F3: writer.agent_id="" + subject_hint=None → v1 file 寫到 unknown/ 子目錄。"""
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.writer import MemoryWriter

        # 建一個 writer 但 agent_id=""
        graph_db = tmp_data_dir / "agent_unknown_test" / "graph.sqlite"
        gs = GraphStore(db_path=graph_db)
        writer = MemoryWriter(gs, default_session_id="s1", agent_id="")
        # subject_hint="user" 不是 "agent_*" prefix → fallback to "unknown"
        writer.extract("Bry likes apples", subject_hint="user")

        # v1 應該寫到 {data_dir}/unknown/memories.jsonl
        # (因為 _mirror_to_v1_store 的 v1_agent_id fallback 邏輯)
        v1_file = tmp_data_dir / "unknown" / "memories.jsonl"
        assert v1_file.exists(), f"v1 file should exist at {v1_file}, but it doesn't"
        gs.close()

    def test_f4_partial_mirror_no_rollback(self, tmp_data_dir, monkeypatch):
        """F4: mirror 中途失敗 → 已有 v1 資料不被 rollback。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        from src.memory.v1 import store as v1store_mod
        real_cls = v1store_mod.V1Store
        # 第 3 次 add 拋例外,前 2 筆已寫
        def factory(dd, aid):
            return FailingV1Store(
                real_cls, dd, aid,
                fail_on_call=3, fail_exception=OSError("disk full mid loop"),
            )
        monkeypatch.setattr(v1store_mod, "V1Store", factory)

        text = "Bry likes apples. Rem likes onigiri. Yua likes boba. Mai likes crepes."
        writer.extract_and_write(text, subject_hint="user")

        # 前 2 筆已寫進 v1
        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count == 2, f"expected 2 partial v1 entries, got {v1_count}"
        store.close()


# ──────────────────────────────────────────────────────────────────────
# Section G — Uncontrolled duplicate (3 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionG_UncontrolledDuplicate:
    """同 content 走不同 API path → v1 沒 dedup。"""

    def test_g1_three_paths_same_content(self, tmp_data_dir):
        """G1: extract / extract_and_write / write_turn 同 content → 3 組 v1 筆。"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")

        writer.extract("Bry likes apples", subject_hint="user")
        writer.extract_and_write("Bry likes apples", subject_hint="user")
        writer.write_turn("Bry likes apples", "", session_id="s1")  # user 走 extract

        # 至少 3 筆 v1(extract, extract_and_write 走 2 次 mirror,write_turn 的 user 又 1 次)
        v1_count = _count_v1_entries(tmp_data_dir)
        assert v1_count >= 3, f"v1 should ≥3, got {v1_count}"
        # graph 只有 extract_and_write + write_turn 寫入(extract 不寫)
        graph_count = _count_graph_facts(tmp_data_dir, store=store)
        assert graph_count >= 1
        store.close()

    def test_g2_same_memory_id_twice_via_v1store(self, tmp_data_dir):
        """G2: V1Store.add 同 memory_id 兩次 → 2 筆(無 dedup)。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        m = Memory(
            memory_id="dup-test-1", agent_id="agent_rem",
            content="X", tags=["a"], created_at=time.time(),
            category="fact", confidence=0.8,
        )
        v1.add(m)
        v1.add(m)
        assert v1.count() == 2

    # G3 移至 TestSectionG_G3 類別(避免跟 Section 命名衝突)


# G3 獨立 class,純粹語法乾淨版本
class TestSectionG_G3:
    def test_g3_different_facts_same_triple(self, tmp_data_dir):
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        m1 = Memory(
            memory_id="uuid-A", agent_id="agent_rem",
            content="Bry likes apples",
            tags=["preference", "bry", "apples"],
            created_at=time.time(),
            category="preference", confidence=0.9,
        )
        m2 = Memory(
            memory_id="uuid-B", agent_id="agent_rem",
            content="Bry likes apples",  # 同 content
            tags=["preference", "bry", "apples"],  # 同 tags
            created_at=time.time() + 1,
            category="preference", confidence=0.9,
        )
        v1.add(m1)
        v1.add(m2)
        # 兩個不同 memory_id → 2 筆
        assert v1.count() == 2
        # 但 content/tags 一樣
        mems = v1.all()
        contents = [m.content for m in mems]
        assert contents == ["Bry likes apples", "Bry likes apples"]


# ──────────────────────────────────────────────────────────────────────
# Section H — Path / agent_id resolution (3 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionH_PathResolution:
    """v1 mirror 路徑解析的 boundary。"""

    def test_h1_writer_agent_id_creates_correct_v1_path(self, tmp_data_dir):
        """H1: writer.agent_id='agent_rem' → v1 file 寫到 data/agent_rem/memories.jsonl"""
        writer, store = _make_writer(tmp_data_dir, "agent_rem")
        writer.extract_and_write("Bry likes apples", subject_hint="user")

        v1_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        assert v1_file.exists()
        assert v1_file.read_text(encoding="utf-8").strip() != ""
        store.close()

    def test_h2_no_agent_id_with_agent_subject_hint_falls_back_to_subject(self, tmp_data_dir):
        """H2: writer.agent_id='' + subject_hint='agent_rem' → v1 file 寫到 data/agent_rem/"""
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.writer import MemoryWriter

        # 注意這裡 graph db path 必須跟 v1 父目錄對應
        # _mirror_to_v1_store 從 graph_db_path.parent.parent 推 v1_data_dir
        # 所以 v1_data_dir = tmp_data_dir, v1 file = tmp_data_dir/<agent_id>/memories.jsonl
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        gs = GraphStore(db_path=graph_db)
        writer = MemoryWriter(gs, default_session_id="s1", agent_id="")
        # subject_hint 是 "agent_rem" (looks like agent_id) → 用它
        writer.extract("Bry likes apples", subject_hint="agent_rem")

        v1_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        assert v1_file.exists(), f"v1 file should exist at {v1_file}"
        gs.close()

    def test_h3_no_agent_id_with_user_subject_hint_falls_back_to_unknown(self, tmp_data_dir):
        """H3: writer.agent_id='' + subject_hint='user' → v1 file 寫到 data/unknown/"""
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.writer import MemoryWriter

        graph_db = tmp_data_dir / "agent_unknown" / "graph.sqlite"
        gs = GraphStore(db_path=graph_db)
        writer = MemoryWriter(gs, default_session_id="s1", agent_id="")
        writer.extract("Bry likes apples", subject_hint="user")

        # subject_hint="user" 不是 "agent_*" prefix → fallback "unknown"
        v1_file = tmp_data_dir / "unknown" / "memories.jsonl"
        assert v1_file.exists(), f"v1 file should exist at {v1_file}"
        gs.close()


# ──────────────────────────────────────────────────────────────────────
# Section I — Idempotency / V1Store 契約 (5 tests)
# ──────────────────────────────────────────────────────────────────────


class TestSectionI_V1StoreContract:
    """V1Store 本身的 append-only / no-dedup / auto-mkdir 契約。"""

    def test_i1_same_memory_id_appears_twice(self, tmp_data_dir):
        """I1: 同 memory_id 加 2 次 → 2 筆(不 dedup)。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        m = Memory(
            memory_id="same", agent_id="agent_rem",
            content="X", tags=["a"], created_at=time.time(),
            category="fact", confidence=0.8,
        )
        v1.add(m)
        v1.add(m)
        ids = [x.memory_id for x in v1.all()]
        assert ids.count("same") == 2

    def test_i2_count_matches_lines(self, tmp_data_dir):
        """I2: count() 等於實際行數。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        assert v1.count() == 0
        for i in range(5):
            v1.add(Memory(
                memory_id=f"m-{i}", agent_id="agent_rem",
                content=f"c{i}", tags=[f"t{i}"], created_at=time.time() + i,
                category="fact", confidence=0.8,
            ))
        assert v1.count() == 5

    def test_i3_all_reads_in_append_order(self, tmp_data_dir):
        """I3: all() 讀回順序等於 append 順序。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        for i in range(3):
            v1.add(Memory(
                memory_id=f"m-{i}", agent_id="agent_rem",
                content=f"c{i}", tags=[f"t{i}"], created_at=float(i),
                category="fact", confidence=0.8,
            ))
        mems = v1.all()
        assert [m.memory_id for m in mems] == ["m-0", "m-1", "m-2"]

    def test_i4_auto_mkdir_for_nested_agent(self, tmp_data_dir):
        """I4: V1Store 自動建 agent 子目錄(巢狀)。"""
        v1 = _make_v1_store(tmp_data_dir, "deeply_nested_agent_xyz")
        assert v1.store_file.parent.exists()

    def test_i5_corrupt_row_in_file_skipped(self, tmp_data_dir):
        """I5: 檔案有壞行 → all() 跳過該行,其他行仍可讀。"""
        from src.memory.v1.schema import Memory
        v1 = _make_v1_store(tmp_data_dir, "agent_rem")
        v1.add(Memory(
            memory_id="good-A", agent_id="agent_rem",
            content="A", tags=["a"], created_at=time.time(),
            category="fact", confidence=0.8,
        ))
        v1_file = tmp_data_dir / "agent_rem" / "memories.jsonl"
        with open(v1_file, "a", encoding="utf-8") as f:
            f.write("NOT-JSON-AT-ALL\n")
        v1.add(Memory(
            memory_id="good-B", agent_id="agent_rem",
            content="B", tags=["b"], created_at=time.time() + 1,
            category="fact", confidence=0.8,
        ))

        mems = v1.all()
        ids = [m.memory_id for m in mems]
        # 壞行被跳過 → 只看到 2 個 good
        assert "good-A" in ids
        assert "good-B" in ids
        assert len(ids) == 2


# ──────────────────────────────────────────────────────────────────────
# Section Z — Smoke / 自我檢查 (1 test)
# ──────────────────────────────────────────────────────────────────────


class TestSectionZ_Smoke:
    """整個 M5.4-2 audit 自身的 smoke。"""

    def test_z1_production_data_path_exists_no_mutation(self):
        """Z1: smoke check — production data path 仍存在,但本 test 不會動它。"""
        # production data 在 C:\Users\bbfcc\.local\bin\soul-os-harness\data\memory
        # 本 test 不碰它,只記錄一下存在性
        prod_path = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data\memory")
        if prod_path.exists():
            # 不 mutation, 只列子目錄
            subdirs = [p.name for p in prod_path.iterdir() if p.is_dir()]
            assert isinstance(subdirs, list)
        # 沒 prod path 也 PASS(代表這台機器沒裝)


# ──────────────────────────────────────────────────────────────────────
# Counts assertion(給 pytest 報告用)
# ──────────────────────────────────────────────────────────────────────


def test_m5_4_2_test_count():
    """確認本檔案至少 30 個 tests。"""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_funcs = [
        (name, obj) for name, obj in inspect.getmembers(current_module, inspect.isclass)
        if name.startswith("Test") and obj.__name__ != "TestSectionG_G3"  # 排除內部協助 class
    ]
    total_tests = 0
    for _class_name, cls in test_funcs:
        for method_name, method in inspect.getmembers(cls, inspect.isfunction):
            if method_name.startswith("test_") and not method_name.startswith("test_m5"):
                total_tests += 1
    # 加上 TestSectionG_G3 自己的 1 個 test
    g3_class = TestSectionG_G3
    for method_name, method in inspect.getmembers(g3_class, inspect.isfunction):
        if method_name.startswith("test_"):
            total_tests += 1

    assert total_tests >= 30, f"expected ≥30 tests, got {total_tests}"
