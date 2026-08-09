"""
M5.4-5.2 — Memory Integration with Inner Life Foundation Tests
================================================================

派工: 2026-08-09 18:38 (Bry)
性質: IMPLEMENTATION (Memory ⇄ Inner Life integration)
目標: 讓現有 Memory persistence 能夠保存對應的 canonical Inner Life event identity

派工 派工核心:
  - Fact + v1 Memory 都加 inner_life_event_id: Optional[str] 欄位
  - v1 mirror / SQL graph 都保留 identity (M5.4-2 divergence fix)
  - 既有 records (pre-M5.4-5.2) 沒 inner_life_event_id, 讀取時 default None
  - 不修改 M5.3 retrieval behavior / SAGE / v1 contract meaning
  - 不修 M5.4-2 mirror/graph divergence architecture (只確保 identity 一致)

測試 8 sections:
  A. Fact dataclass (5 tests)
  B. v1 Memory dataclass (4 tests)
  C. GraphStore schema v6 migration (4 tests)
  D. GraphStore add_fact + round-trip (3 tests)
  E. v1 mirror preserves inner_life_event_id (2 tests)
  F. extract_and_write / write_turn 自動生成 (3 tests)
  G. graph <-> mirror identity consistency (M5.4-2 fix) (2 tests)
  H. backward compat with old records (3 tests)
  Z. foundation independence smoke (2 tests)
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from src.memory.sage.graph_store import GraphStore
from src.memory.sage.models import Fact
from src.memory.sage.writer import MemoryWriter
from src.memory.v1.schema import Memory
from src.memory.v1.store import V1Store


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def tmp_data_dir(tmp_path):
    """每次 test 一個全新的 data dir。"""
    data_dir = tmp_path / "memory_m5_4_5_2"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# ─────────────────────────────────────────────────────────────────────
# Section A — Fact dataclass
# ─────────────────────────────────────────────────────────────────────


class TestSectionA_FactDataclass:
    """Fact 加 inner_life_event_id: Optional[str] 欄位, backward compat 預設 None."""

    def test_a1_fact_with_inner_life_event_id(self):
        """A1: Fact 帶 inner_life_event_id 32 hex 正確儲存."""
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1", inner_life_event_id="a" * 32,
        )
        assert f.inner_life_event_id == "a" * 32

    def test_a2_fact_without_inner_life_event_id_defaults_to_none(self):
        """A2: Fact 不指定 inner_life_event_id → default None (M5.4-5.2 default)."""
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1",
        )
        assert f.inner_life_event_id is None

    def test_a3_fact_to_dict_includes_inner_life_event_id(self):
        """A3: Fact.to_dict() 包含 inner_life_event_id (向後相容: None 也序列化)."""
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1", inner_life_event_id="b" * 32,
        )
        d = f.to_dict()
        assert "inner_life_event_id" in d
        assert d["inner_life_event_id"] == "b" * 32
        # 既有欄位都還在
        for required in ("subject", "predicate", "object", "timestamp",
                          "event_time", "weight", "confidence", "source",
                          "fact_id", "session_id", "is_anchor",
                          "merged_from", "merge_reason", "source_pair"):
            assert required in d

    def test_a4_fact_from_dict_backward_compat(self):
        """A4: Fact.from_dict() 對舊 payload (沒 inner_life_event_id) 向後相容 → None."""
        old_payload = {
            "subject": "Bry", "predicate": "likes", "object": "apples",
            "timestamp": time.time(), "confidence": 0.9, "source": "user",
            "session_id": "s1", "fact_id": "x" * 32,
        }
        f = Fact.from_dict(old_payload)
        assert f.inner_life_event_id is None  # backward compat

    def test_a5_fact_from_dict_reads_inner_life_event_id(self):
        """A5: Fact.from_dict() 讀 inner_life_event_id 從 payload."""
        payload = {
            "subject": "Bry", "predicate": "likes", "object": "apples",
            "timestamp": time.time(), "confidence": 0.9, "source": "user",
            "session_id": "s1", "fact_id": "y" * 32,
            "inner_life_event_id": "c" * 32,
        }
        f = Fact.from_dict(payload)
        assert f.inner_life_event_id == "c" * 32


# ─────────────────────────────────────────────────────────────────────
# Section B — v1 Memory dataclass
# ─────────────────────────────────────────────────────────────────────


class TestSectionB_V1MemoryDataclass:
    """v1 Memory 加 inner_life_event_id: Optional[str] 欄位, 既有 records 為 None."""

    def test_b1_v1_memory_with_inner_life_event_id(self):
        """B1: v1 Memory 帶 inner_life_event_id 32 hex 正確儲存."""
        m = Memory(
            memory_id="x" * 32, agent_id="agent_rem", content="Bry likes apples",
            tags=["fact"], created_at=time.time(),
            category="fact", confidence=0.9,
            inner_life_event_id="a" * 32,
        )
        assert m.inner_life_event_id == "a" * 32

    def test_b2_v1_memory_without_inner_life_event_id_defaults_to_none(self):
        """B2: v1 Memory 不指定 inner_life_event_id → default None."""
        m = Memory(
            memory_id="x" * 32, agent_id="agent_rem", content="Bry likes apples",
            tags=["fact"], created_at=time.time(),
        )
        assert m.inner_life_event_id is None

    def test_b3_v1_memory_to_dict_includes_inner_life_event_id(self):
        """B3: v1 Memory.to_dict() (asdict) 包含 inner_life_event_id."""
        m = Memory(
            memory_id="x" * 32, agent_id="agent_rem", content="Bry likes apples",
            tags=["fact"], created_at=time.time(),
            inner_life_event_id="d" * 32,
        )
        d = m.to_dict()
        assert d["inner_life_event_id"] == "d" * 32

    def test_b4_v1_store_all_backward_compat_old_records(self, tmp_data_dir):
        """B4: V1Store.all() 對舊 jsonl records (沒 inner_life_event_id) 載入為 None."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        v1 = V1Store(tmp_data_dir, "agent_rem")
        # 寫一個「舊格式」jsonl (沒 inner_life_event_id 欄位)
        old_entry = {
            "memory_id": "x" * 32,
            "agent_id": "agent_rem",
            "content": "old fact",
            "tags": ["fact"],
            "created_at": time.time(),
            "category": "fact",
            "confidence": 0.9,
        }
        with open(v1.store_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_entry, ensure_ascii=False) + "\n")

        mems = v1.all()
        assert len(mems) == 1
        assert mems[0].inner_life_event_id is None  # backward compat


# ─────────────────────────────────────────────────────────────────────
# Section C — GraphStore schema v6 migration
# ─────────────────────────────────────────────────────────────────────


class TestSectionC_GraphStoreSchemaV6:
    """GraphStore schema v6 migration (idempotent, NOT data migration)."""

    def test_c1_schema_version_bumped_to_6(self):
        """C1: _SCHEMA_VERSION = 6 (M5.4-5.2 adds inner_life_event_id column)."""
        from src.memory.sage import graph_store
        assert graph_store._SCHEMA_VERSION == 6

    def test_c2_v6_migration_adds_column_to_existing_v5_db(self, tmp_data_dir):
        """C2: 既有 v5 database (沒有 inner_life_event_id column) 自動 ALTER TABLE 加上去."""
        import sqlite3
        # 建立 v5 schema DB (手動)
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        graph_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(graph_db))
        conn.execute("""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """)
        conn.execute("INSERT INTO schema_meta VALUES ('version', '5')")
        conn.execute("""
            CREATE TABLE facts (
                fact_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                timestamp REAL NOT NULL,
                event_time REAL,
                weight REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'user',
                session_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                is_anchor INTEGER NOT NULL DEFAULT 0,
                source_pair TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                merged_from TEXT,
                merge_reason TEXT
            )
        """)
        # 寫一筆既有 v5 fact
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old-fact-001", "Bry", "likes", "apples", time.time()),
        )
        conn.commit()
        conn.close()

        # 開 GraphStore 觸發 v5 → v6 migration
        gs = GraphStore(db_path=graph_db)
        # 既有 fact 應該還在, inner_life_event_id 應該 None
        f = gs.get_fact("old-fact-001")
        assert f is not None
        assert f.subject == "Bry"
        assert f.inner_life_event_id is None  # backward compat: 既有 rows 沒 event_id

    def test_c3_v6_migration_idempotent(self, tmp_data_dir):
        """C3: 重複開 GraphStore 不會 raise (idempotent ALTER TABLE)."""
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        graph_db.parent.mkdir(parents=True, exist_ok=True)
        gs1 = GraphStore(db_path=graph_db)
        gs2 = GraphStore(db_path=graph_db)  # 第二次開
        gs3 = GraphStore(db_path=graph_db)  # 第三次
        # 都成功, 沒 OperationalError
        assert gs1 is not None
        assert gs2 is not None
        assert gs3 is not None

    def test_c4_new_db_created_at_v6(self, tmp_data_dir):
        """C4: 新 DB 直接以 v6 schema 建立 (沒經過 migration)."""
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        graph_db.parent.mkdir(parents=True, exist_ok=True)
        gs = GraphStore(db_path=graph_db)
        # 寫一筆, 確認 inner_life_event_id 欄位存在
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1", inner_life_event_id="e" * 32,
        )
        gs.add_fact(f)
        f_read = gs.get_fact(f.fact_id)
        assert f_read.inner_life_event_id == "e" * 32


# ─────────────────────────────────────────────────────────────────────
# Section D — GraphStore add_fact + round-trip
# ─────────────────────────────────────────────────────────────────────


class TestSectionD_GraphStoreRoundTrip:
    """GraphStore add_fact 包含 inner_life_event_id 欄位."""

    def test_d1_add_fact_with_inner_life_event_id(self, tmp_data_dir):
        """D1: add_fact 帶 inner_life_event_id 寫入 SQL 並 round-trip."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1", inner_life_event_id="f" * 32,
        )
        gs.add_fact(f)
        f2 = gs.get_fact(f.fact_id)
        assert f2.inner_life_event_id == "f" * 32

    def test_d2_add_fact_without_inner_life_event_id(self, tmp_data_dir):
        """D2: add_fact 不指定 inner_life_event_id → SQL 存空字串, 讀回 None."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1",
        )
        gs.add_fact(f)
        f2 = gs.get_fact(f.fact_id)
        assert f2.inner_life_event_id is None  # 空字串 → None

    def test_d3_add_fact_with_empty_string_inner_life_event_id(self, tmp_data_dir):
        """D3: inner_life_event_id="" (空字串, 不該用) → 視同 None."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        # 強行建一個 inner_life_event_id="" 的 fact (雖然不太合規)
        f = Fact(
            subject="Bry", predicate="likes", object="apples",
            timestamp=time.time(), confidence=0.9, source="user",
            session_id="s1", inner_life_event_id="",
        )
        gs.add_fact(f)
        f2 = gs.get_fact(f.fact_id)
        # 空字串讀回應該是 None (跟 source_pair 處理方式對齊)
        assert f2.inner_life_event_id is None


# ─────────────────────────────────────────────────────────────────────
# Section E — v1 mirror preserves inner_life_event_id
# ─────────────────────────────────────────────────────────────────────


class TestSectionE_V1MirrorPreservation:
    """_mirror_to_v1_store 從 raw_result 讀 inner_life_event_id 寫到 v1 Memory."""

    def test_e1_mirror_preserves_inner_life_event_id(self, tmp_data_dir):
        """E1: extract_and_write 自動生成 event_id, v1 mirror 保留."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        v1 = V1Store(tmp_data_dir, "agent_rem")
        mems = v1.all()
        assert len(mems) >= 1
        for m in mems:
            assert m.inner_life_event_id is not None
            assert len(m.inner_life_event_id) == 32

    def test_e2_mirror_and_graph_share_same_event_id(self, tmp_data_dir):
        """E2: 同一個 fact 在 graph 跟 v1 mirror 對應同一個 inner_life_event_id (M5.4-2 fix)."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        ids = writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        assert len(ids) == 1
        fact_in_graph = gs.get_fact(ids[0])

        v1 = V1Store(tmp_data_dir, "agent_rem")
        mems = v1.all()
        assert len(mems) == 1
        mem_in_v1 = mems[0]
        # 派工 派工: 同一個 fact 的 inner_life_event_id 應該一致 (graph <-> mirror)
        assert fact_in_graph.inner_life_event_id == mem_in_v1.inner_life_event_id
        assert fact_in_graph.inner_life_event_id is not None


# ─────────────────────────────────────────────────────────────────────
# Section F — extract_and_write / write_turn 自動生成
# ─────────────────────────────────────────────────────────────────────


class TestSectionF_AutoGeneration:
    """extract_and_write 跟 write_turn 自動為每個 fact 生成 inner_life_event_id."""

    def test_f1_extract_and_write_generates_unique_event_ids(self, tmp_data_dir):
        """F1: 一個 text 抽出多個 fact → 每個 fact 有自己的 inner_life_event_id (unique)."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        writer.extract_and_write(
            "Bry likes apples. Rem likes onigiri. Yua likes boba.",
            subject_hint="user", session_id="s1",
        )
        v1 = V1Store(tmp_data_dir, "agent_rem")
        mems = v1.all()
        # heuristic 至少抽出 3 個 fact
        assert len(mems) >= 3
        event_ids = [m.inner_life_event_id for m in mems]
        assert all(eid is not None for eid in event_ids)
        # 全部 unique
        assert len(set(event_ids)) == len(event_ids)

    def test_f2_write_turn_propagates_to_both_calls(self, tmp_data_dir):
        """F2: write_turn(user, assistant) 對 user 跟 assistant 都生成 event_ids (各自)."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        writer.write_turn(
            user_content="Bry likes apples",
            assistant_content="Rem likes onigiri",
            session_id="s1",
        )
        # write_turn 對 user + assistant 各跑一次 extract_and_write
        v1 = V1Store(tmp_data_dir, "agent_rem")
        mems = v1.all()
        event_ids = [m.inner_life_event_id for m in mems]
        # 至少 2 個 event_ids (user + assistant 各有 fact)
        assert len(event_ids) >= 2
        assert all(eid is not None for eid in event_ids)
        assert len(set(event_ids)) == len(event_ids)  # 全部 unique

    def test_f3_extract_only_no_write_also_generates_event_id(self, tmp_data_dir):
        """F3: writer.extract() (no graph write) 也生成 inner_life_event_id + mirror 保留."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        # extract (no write)
        facts = writer.extract("Bry likes apples", subject_hint="user", session_id="s1")
        # extract 應生成 event_id
        assert len(facts) >= 1
        for f in facts:
            assert f.inner_life_event_id is not None
        # mirror 也有 (extract 也要 mirror, per M5.4-2 finding)
        v1 = V1Store(tmp_data_dir, "agent_rem")
        mems = v1.all()
        for m in mems:
            assert m.inner_life_event_id is not None


# ─────────────────────────────────────────────────────────────────────
# Section G — graph <-> mirror identity consistency (M5.4-2 fix)
# ─────────────────────────────────────────────────────────────────────


class TestSectionG_GraphMirrorConsistency:
    """M5.4-2 mirror/graph divergence fix: 同一 fact 在兩個 path 有相同 inner_life_event_id."""

    def test_g1_mirror_does_not_diverge_from_graph(self, tmp_data_dir):
        """G1: 寫入 path (graph + mirror) 對同一個 fact 的 inner_life_event_id 一致."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        # 從 graph 拿 fact
        all_facts = []
        for row in gs._get_conn().execute("SELECT * FROM facts").fetchall():
            all_facts.append(gs._row_to_fact(row))
        # 從 v1 拿 memory
        v1 = V1Store(tmp_data_dir, "agent_rem")
        v1_mems = v1.all()
        # 配對: content 相同 (heuristic 用 subject+predicate+object 組 content)
        for fact in all_facts:
            expected_content = f"{fact.subject} {fact.predicate} {fact.object}"
            for m in v1_mems:
                if m.content == expected_content:
                    assert fact.inner_life_event_id == m.inner_life_event_id, (
                        f"M5.4-2 divergence: graph={fact.inner_life_event_id} "
                        f"v1={m.inner_life_event_id}"
                    )

    def test_g2_multiple_facts_consistent(self, tmp_data_dir):
        """G2: 多個 facts 寫入後, graph <-> mirror identity 全部一致."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        # 多次 extract_and_write
        writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        writer.extract_and_write("Rem likes onigiri", subject_hint="user", session_id="s1")
        # 從 graph 拿全部 facts
        graph_facts = []
        for row in gs._get_conn().execute("SELECT * FROM facts").fetchall():
            graph_facts.append(gs._row_to_fact(row))
        # 從 v1 拿全部 memories
        v1 = V1Store(tmp_data_dir, "agent_rem")
        v1_mems = v1.all()
        # 配對
        for fact in graph_facts:
            expected_content = f"{fact.subject} {fact.predicate} {fact.object}"
            matched = [m for m in v1_mems if m.content == expected_content]
            assert len(matched) >= 1, f"v1 missing mirror for {expected_content}"
            for m in matched:
                assert fact.inner_life_event_id == m.inner_life_event_id, (
                    f"divergence: graph={fact.inner_life_event_id} v1={m.inner_life_event_id}"
                )


# ─────────────────────────────────────────────────────────────────────
# Section H — backward compat with old records
# ─────────────────────────────────────────────────────────────────────


class TestSectionH_BackwardCompat:
    """既有 records (pre-M5.4-5.2) 沒 inner_life_event_id → 讀回 None, 不 crash."""

    def test_h1_old_fact_in_db_loads_with_none(self, tmp_data_dir):
        """H1: 既有 SQL row 沒 inner_life_event_id 欄位 → _row_to_fact 處理為 None."""
        import os
        import sqlite3
        os.environ["USE_LLM_JUDGE"] = "false"
        graph_db = tmp_data_dir / "agent_rem" / "graph.sqlite"
        graph_db.parent.mkdir(parents=True, exist_ok=True)
        # 直接用 sqlite3 寫一筆「舊格式」(沒 inner_life_event_id column)
        conn = sqlite3.connect(str(graph_db))
        conn.execute("""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """)
        conn.execute("INSERT INTO schema_meta VALUES ('version', '5')")
        conn.execute("""
            CREATE TABLE facts (
                fact_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                object TEXT NOT NULL,
                timestamp REAL NOT NULL,
                event_time REAL,
                weight REAL NOT NULL DEFAULT 1.0,
                source TEXT NOT NULL DEFAULT 'user',
                session_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                is_anchor INTEGER NOT NULL DEFAULT 0,
                source_pair TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                merged_from TEXT,
                merge_reason TEXT
            )
        """)
        # 寫入「舊格式」fact (沒 inner_life_event_id column, column 根本不存在)
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("legacy-fact-001", "Bry", "likes", "apples", time.time()),
        )
        conn.commit()
        conn.close()

        # 觸發 migration
        gs = GraphStore(db_path=graph_db)
        # 讀回 (從 v5 → v6 migration 已經跑了, _row_to_fact 應該 default inner_life_event_id=None)
        f = gs.get_fact("legacy-fact-001")
        assert f is not None
        assert f.subject == "Bry"
        assert f.inner_life_event_id is None  # backward compat

    def test_h2_old_v1_jsonl_loads_with_none(self, tmp_data_dir):
        """H2: 既有 v1 jsonl line 沒 inner_life_event_id 欄位 → V1Store.all() 讀回 None."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        v1 = V1Store(tmp_data_dir, "agent_rem")
        # 寫「舊格式」jsonl (沒 inner_life_event_id)
        old_entry = {
            "memory_id": "x" * 32, "agent_id": "agent_rem",
            "content": "old fact", "tags": ["fact"], "created_at": time.time(),
            "category": "fact", "confidence": 0.9,
        }
        with open(v1.store_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(old_entry, ensure_ascii=False) + "\n")
        mems = v1.all()
        assert len(mems) == 1
        assert mems[0].inner_life_event_id is None

    def test_h3_mixed_old_new_records(self, tmp_data_dir):
        """H3: 同一個 v1 store 裡舊 records (None) + 新 records (有 event_id) 共存."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        v1 = V1Store(tmp_data_dir, "agent_rem")
        # 寫 1 舊 + 1 新
        old = {
            "memory_id": "old" * 16, "agent_id": "agent_rem",
            "content": "old", "tags": [], "created_at": 1.0,
            "category": "fact", "confidence": 0.5,
        }
        new = {
            "memory_id": "new" * 16, "agent_id": "agent_rem",
            "content": "new", "tags": [], "created_at": 2.0,
            "category": "fact", "confidence": 0.5,
            "inner_life_event_id": "a" * 32,
        }
        with open(v1.store_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(old, ensure_ascii=False) + "\n")
            f.write(json.dumps(new, ensure_ascii=False) + "\n")
        mems = v1.all()
        assert len(mems) == 2
        by_id = {m.memory_id: m for m in mems}
        assert by_id["old" * 16].inner_life_event_id is None
        assert by_id["new" * 16].inner_life_event_id == "a" * 32


# ─────────────────────────────────────────────────────────────────────
# Section Z — foundation independence smoke
# ─────────────────────────────────────────────────────────────────────


class TestSectionZ_FoundationIndependence:
    """派工 派工派工: 'Memory failure MUST NOT block Diary/Dream' + 'no shared failure dependency'."""

    def test_z1_memory_integration_works_without_inner_life_writer(self, tmp_data_dir):
        """Z1: 沒有 inner_life_writer → 仍可寫入, event_id 由 writer 自動生成 (32 hex)."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")  # 沒 inner_life_writer
        ids = writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        assert len(ids) >= 1
        # 自動生成 event_id (writer 邏輯, 不依賴 InnerLifeWriter)
        fact = gs.get_fact(ids[0])
        assert fact.inner_life_event_id is not None
        assert len(fact.inner_life_event_id) == 32

    def test_z2_inner_life_module_not_required_for_memory(self, tmp_data_dir):
        """Z2: Memory 整合不依賴 InnerLifeWriter (派工 派工: 'Unified architecture ≠ shared failure dependency')."""
        import os
        os.environ["USE_LLM_JUDGE"] = "false"
        # 不 import src.inner_life — Memory 仍可運作
        from src.memory.sage.writer import MemoryWriter
        from src.memory.v1.store import V1Store
        # sanity check: 沒 inner_life_writer, Memory 仍 work
        gs = GraphStore(db_path=tmp_data_dir / "agent_rem" / "graph.sqlite")
        writer = MemoryWriter(gs, "s1", "agent_rem")
        writer.extract_and_write("Bry likes apples", subject_hint="user", session_id="s1")
        # v1 mirror 也 work
        v1 = V1Store(tmp_data_dir, "agent_rem")
        assert len(v1.all()) >= 1


# ─────────────────────────────────────────────────────────────────────
# Counts assertion
# ─────────────────────────────────────────────────────────────────────


def test_m5_4_5_2_test_count():
    """確認本檔案至少 25 個 tests."""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_funcs = []
    for name, obj in inspect.getmembers(current_module, inspect.isclass):
        if name.startswith("Test") and inspect.isclass(obj):
            for method_name, method in inspect.getmembers(obj, inspect.isfunction):
                if method_name.startswith("test_"):
                    test_funcs.append(method_name)
    total = len(test_funcs) + 1  # +1 for this test
    assert total >= 25, f"expected ≥25 tests, got {total}"
