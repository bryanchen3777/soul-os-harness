"""
tests/test_temporal_memory_mr2.py
MR-2 — Temporal Memory & Mem0 Primitives Implementation（MR-1 契約實作驗收）

驗收點（MR-2 工單）:
  A. Schema v7 遷移（valid_from 回填 timestamp，invalidated_at NULL）
  B. GraphStore invalidate_fact 軟刪 + get_facts_as_of 回溯
  C. primitives.py 顯式 add/update/delete/resolve_conflict
  D. Reader as_of 預設過濾 invalidated_at IS NULL（既有呼叫端自動享受軟刪紅利）

Frozen Contract 聲明: 本測試只驗證新 API 與 additive 行為，不觸碰
writer 隱式流程 / evolution 硬刪 / v1 schema。
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from src.memory.primitives import MemoryPrimitives
from src.memory.sage.graph_store import GraphStore, _SCHEMA_VERSION
from src.memory.sage.models import Fact
from src.memory.sage.reader import MemoryReader


def _make_fact(
    subject: str = "Bry",
    predicate: str = "likes",
    obj: str = "apples",
    weight: float = 1.0,
    timestamp: float | None = None,
    valid_from: float | None = None,
    invalidated_at: float | None = None,
) -> Fact:
    return Fact(
        subject=subject,
        predicate=predicate,
        object=obj,
        timestamp=timestamp if timestamp is not None else time.time(),
        weight=weight,
        source="user",
        session_id="mr2_test",
        valid_from=valid_from,
        invalidated_at=invalidated_at,
    )


# ─────────────────────────────────────────────────────────────────────
# A. Schema v7 遷移
# ─────────────────────────────────────────────────────────────────────


class TestSchemaV7Migration:
    def test_a1_schema_version_is_7(self):
        """A1: _SCHEMA_VERSION = 8 (TG-2: v8 新增 goals 表; 版本快照随迁移 bump 对齐)。"""
        assert _SCHEMA_VERSION == 8

    def test_a2_v6_db_migrates_to_v7_backfills_valid_from(self, tmp_path: Path):
        """A2: 既有 v6 DB 開 GraphStore → valid_from 回填 timestamp, invalidated_at NULL."""
        db = tmp_path / "graph.sqlite"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_meta VALUES ('version', '6')")
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
                merge_reason TEXT,
                inner_life_event_id TEXT NOT NULL DEFAULT ''
            )
        """)
        ts = time.time() - 1000.0
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            ("old-1", "Bry", "likes", "apples", ts),
        )
        conn.commit()
        conn.close()

        gs = GraphStore(db_path=db)
        f = gs.get_fact("old-1")
        assert f is not None
        # 回填: valid_from = timestamp（event_time 100% NULL 不可用）
        assert f.valid_from == ts
        # invalidated_at 不回填: NULL = 當前有效
        assert f.invalidated_at is None
        # 新列存在
        row = gs._get_conn().execute(
            "SELECT valid_from, invalidated_at FROM facts WHERE fact_id='old-1'"
        ).fetchone()
        assert row["valid_from"] == ts
        assert row["invalidated_at"] is None
        gs.close()

    def test_a3_migration_idempotent(self, tmp_path: Path):
        """A3: 重複開 GraphStore 不 raise（ALTER TABLE 冪等）。"""
        db = tmp_path / "graph.sqlite"
        gs1 = GraphStore(db_path=db)
        gs1.add_fact(_make_fact())
        gs1.close()
        gs2 = GraphStore(db_path=db)
        gs3 = GraphStore(db_path=db)
        assert gs2.get_fact(gs1.get_all_facts(include_invalidated=True)[0].fact_id) is not None
        gs2.close()
        gs3.close()

    def test_a4_new_db_has_columns_and_backfill_rule(self, tmp_path: Path):
        """A4: 新 DB 直接 v7 schema；新寫入 fact 未設 valid_from → NULL（向後相容）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        f = _make_fact(valid_from=None)
        gs.add_fact(f)
        f_read = gs.get_fact(f.fact_id)
        assert f_read.valid_from is None  # 隱式路徑不填 → NULL = 未知
        assert f_read.invalidated_at is None
        gs.close()


# ─────────────────────────────────────────────────────────────────────
# B. GraphStore invalidate_fact + get_facts_as_of
# ─────────────────────────────────────────────────────────────────────


class TestInvalidateFact:
    def test_b1_soft_delete_marks_invalidated_at(self, tmp_path: Path):
        """B1: invalidate_fact 軟刪 — 行保留, invalidated_at 設置, 預設路徑不可見."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        f = _make_fact(valid_from=time.time() - 100)
        gs.add_fact(f)
        now = time.time()
        ok = gs.invalidate_fact(f.fact_id, at_time=now)
        assert ok is True
        # 行還在（軟刪不是硬刪）
        f_read = gs.get_fact(f.fact_id)
        assert f_read is not None
        assert f_read.invalidated_at == now
        # 預設路徑自動過濾
        assert f.fact_id not in {x.fact_id for x in gs.get_all_facts()}
        assert f.fact_id not in {x.fact_id for x in gs.search_by_entity("Bry")}
        # include_invalidated=True 可見
        assert f.fact_id in {x.fact_id for x in gs.get_all_facts(include_invalidated=True)}
        gs.close()

    def test_b2_idempotent_keeps_earliest_invalidated_at(self, tmp_path: Path):
        """B2: 冪等 — 重複 invalidate 保留最早 invalidated_at（不往後推）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        f = _make_fact(valid_from=time.time() - 100)
        gs.add_fact(f)
        t1 = time.time() - 50
        t2 = time.time()
        assert gs.invalidate_fact(f.fact_id, at_time=t1) is True
        assert gs.invalidate_fact(f.fact_id, at_time=t2) is True  # 冪等 True
        f_read = gs.get_fact(f.fact_id)
        assert f_read.invalidated_at == t1  # 保留最早
        gs.close()

    def test_b3_missing_fact_returns_false(self, tmp_path: Path):
        """B3: fact_id 不存在 → False."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        assert gs.invalidate_fact("nope") is False
        gs.close()

    def test_b4_memory_graph_edge_synced(self, tmp_path: Path):
        """B4: 記憶體圖 edge 的 invalidated_at 同步（雙寫）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        f = _make_fact(valid_from=time.time() - 100)
        gs.add_fact(f)
        now = time.time()
        gs.invalidate_fact(f.fact_id, at_time=now)
        for _u, _v, _k, data in gs.graph.edges(keys=True, data=True):
            if data.get("fact_id") == f.fact_id:
                assert data.get("invalidated_at") == now
                break
        else:
            raise AssertionError("edge not found in memory graph")
        gs.close()


class TestGetFactsAsOf:
    def test_c1_half_open_interval_boundaries(self, tmp_path: Path):
        """C1: 半開區間 [valid_from, invalidated_at) 邊界語義（SQL 鎖定: invalidated_at > ? 嚴格大於）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        t0 = time.time() - 1000
        # 有效區間 [t0, t0+100)
        f = _make_fact(valid_from=t0, invalidated_at=t0 + 100)
        gs.add_fact(f)
        # 含左邊界: valid_from <= t
        assert f.fact_id in {x.fact_id for x in gs.get_facts_as_of(t0)}
        # 區間內
        assert f.fact_id in {x.fact_id for x in gs.get_facts_as_of(t0 + 50)}
        # 右端開: t == invalidated_at 時已失效（invalidated_at > t 為 False）
        assert f.fact_id not in {x.fact_id for x in gs.get_facts_as_of(t0 + 100)}
        # 失效後不可見
        assert f.fact_id not in {x.fact_id for x in gs.get_facts_as_of(t0 + 100.0001)}
        gs.close()

    def test_c2_null_valid_from_and_null_invalidated_at(self, tmp_path: Path):
        """C2: NULL valid_from 視為無起點; NULL invalidated_at 視為永不過期."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        f = _make_fact(valid_from=None, invalidated_at=None)
        gs.add_fact(f)
        assert f.fact_id in {x.fact_id for x in gs.get_facts_as_of(0.0)}   # 無起點
        assert f.fact_id in {x.fact_id for x in gs.get_facts_as_of(time.time() + 1e9)}  # 永不過期
        gs.close()

    def test_c3_invalidate_then_as_of(self, tmp_path: Path):
        """C3: 軟刪後 — as_of(失效前) 含該 fact, as_of(now) 不含（MR-1 契約 §5.4 驗收）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        t0 = time.time() - 100
        f = _make_fact(valid_from=t0)
        gs.add_fact(f)
        now = time.time()
        gs.invalidate_fact(f.fact_id, at_time=now)
        assert f.fact_id in {x.fact_id for x in gs.get_facts_as_of(now - 1)}
        assert f.fact_id not in {x.fact_id for x in gs.get_facts_as_of(now + 1)}
        gs.close()


# ─────────────────────────────────────────────────────────────────────
# C. MemoryPrimitives
# ─────────────────────────────────────────────────────────────────────


class TestMemoryPrimitives:
    def test_d1_add_fact_defaults_valid_from(self, tmp_path: Path):
        """D1: add_fact 未設 valid_from → 寫入前填充 time.time()."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        f = _make_fact(valid_from=None)
        fid = prim.add_fact(f)
        assert fid != ""
        f_read = gs.get_fact(fid)
        assert f_read.valid_from is not None
        assert f_read.valid_from <= time.time() + 1
        gs.close()

    def test_d2_update_fact_writes_new_version_and_invalidates_old(self, tmp_path: Path):
        """D2: update_fact — 新版本(新 fact_id) + 舊失效 + lineage."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        old = _make_fact(subject="Bry", predicate="likes", obj="apples")
        old_id = prim.add_fact(old)
        new = _make_fact(subject="Bry", predicate="likes", obj="oranges")
        new_id = prim.update_fact(old_id, new, reason="correction")
        assert new_id != "" and new_id != old_id
        # 舊版本軟刪
        old_read = gs.get_fact(old_id)
        assert old_read.invalidated_at is not None
        # 新版本 lineage（update_merge_lineage 以 JSON 寫入 merge_reason 列，與 evolution._merge 同款）
        new_read = gs.get_fact(new_id)
        lineage = json.loads(new_read.merge_reason)
        assert lineage["merged_from"] == [old_id]
        assert lineage["reason"] == "correction"
        assert new_read.valid_from is not None
        # 預設路徑只看到新版本
        visible = {x.fact_id for x in gs.get_all_facts()}
        assert new_id in visible and old_id not in visible
        gs.close()

    def test_d3_update_fact_missing_old_returns_empty(self, tmp_path: Path):
        """D3: update_fact 舊 fact_id 不存在 → ""（不靜默建立）。"""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        assert prim.update_fact("missing", _make_fact()) == ""
        gs.close()

    def test_d4_delete_fact_soft_delete(self, tmp_path: Path):
        """D4: delete_fact 軟刪 — 行保留, invalidated_at 設置."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        f = _make_fact()
        fid = prim.add_fact(f)
        assert prim.delete_fact(fid, reason="user_removed") is True
        f_read = gs.get_fact(fid)
        assert f_read is not None and f_read.invalidated_at is not None
        gs.close()

    def test_d5_resolve_conflict_winner_kept_loser_invalidated(self, tmp_path: Path):
        """D5: resolve_conflict — winner 保留 + loser 失效 + lineage."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        w = _make_fact(subject="Bry", predicate="lives_in", obj="Taipei")
        l = _make_fact(subject="Bry", predicate="lives_in", obj="Kaohsiung")
        wid = prim.add_fact(w)
        lid = prim.add_fact(l)
        assert prim.resolve_conflict(wid, lid, reason="newer_wins") is True
        # loser 軟刪
        l_read = gs.get_fact(lid)
        assert l_read.invalidated_at is not None
        # winner lineage（update_merge_lineage 以 JSON 寫入 merge_reason 列）
        w_read = gs.get_fact(wid)
        lineage = json.loads(w_read.merge_reason)
        assert lineage["merged_from"] == [lid]
        assert lineage["reason"] == "newer_wins"
        gs.close()

    def test_d6_resolve_conflict_missing_returns_false(self, tmp_path: Path):
        """D6: resolve_conflict winner/loser 任一不存在 → False."""
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        prim = MemoryPrimitives(gs)
        f = _make_fact()
        fid = prim.add_fact(f)
        assert prim.resolve_conflict(fid, "missing") is False
        assert prim.resolve_conflict("missing", fid) is False
        gs.close()


# ─────────────────────────────────────────────────────────────────────
# D. Reader as_of
# ─────────────────────────────────────────────────────────────────────


class TestReaderAsOf:
    def _seed(self, tmp_path: Path) -> tuple[GraphStore, str, float]:
        gs = GraphStore(db_path=tmp_path / "graph.sqlite")
        t0 = time.time() - 100
        f = _make_fact(subject="Bry", predicate="likes", obj="apples",
                       weight=1.0, valid_from=t0)
        fid = gs.add_fact(f)
        return gs, fid, t0

    def test_e1_default_filters_invalidated(self, tmp_path: Path):
        """E1: invalidate 後, as_of=None 的 retrieve_context 不再返回該 fact（軟刪紅利）。"""
        gs, fid, t0 = self._seed(tmp_path)
        reader = MemoryReader(gs)
        # 軟刪前: 可檢索到
        r1 = reader.retrieve_context("Bry likes apples", top_k=5)
        assert fid in {x.fact_id for x in r1.facts}
        # 軟刪後: 預設路徑自動過濾
        gs.invalidate_fact(fid, at_time=time.time())
        r2 = reader.retrieve_context("Bry likes apples", top_k=5)
        assert fid not in {x.fact_id for x in r2.facts}
        gs.close()

    def test_e2_as_of_before_invalidation_returns_fact(self, tmp_path: Path):
        """E2: as_of(失效前) 含該 fact; as_of(now) 不含。"""
        gs, fid, t0 = self._seed(tmp_path)
        reader = MemoryReader(gs)
        now = time.time()
        gs.invalidate_fact(fid, at_time=now)
        r_before = reader.retrieve_context("Bry likes apples", top_k=5, as_of=now - 1)
        assert fid in {x.fact_id for x in r_before.facts}
        r_after = reader.retrieve_context("Bry likes apples", top_k=5, as_of=now + 1)
        assert fid not in {x.fact_id for x in r_after.facts}
        gs.close()

    def test_e3_export_json_includes_invalidated(self, tmp_path: Path):
        """E3: export_json 豁免 — 匯出全部（含已作廢）。"""
        gs, fid, t0 = self._seed(tmp_path)
        gs.invalidate_fact(fid, at_time=time.time())
        out = tmp_path / "export.jsonl"
        n = gs.export_json(out)
        lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert n == 1
        assert lines[0]["fact_id"] == fid
        assert lines[0]["invalidated_at"] is not None
        gs.close()

    def test_e4_fallback_path_filters_invalidated(self, tmp_path: Path):
        """E4: fallback 路徑（無關鍵詞）也自動過濾已作廢。"""
        gs, fid, t0 = self._seed(tmp_path)
        reader = MemoryReader(gs)
        gs.invalidate_fact(fid, at_time=time.time())
        r = reader.retrieve_context("???", top_k=5)  # 無關鍵詞 → fallback
        assert fid not in {x.fact_id for x in r.facts}
        gs.close()
