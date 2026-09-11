"""MEM-VISIBILITY-C2: current-version open 跳過 schema_meta 寫入守衛測試。

背景（C2-V 已實證，不重做）:
- GraphStore._init_db 在 schema 版本已是現行 v9 時，仍執行
  INSERT OR REPLACE INTO schema_meta + commit()；raw 逐步鏡像實驗顯示僅該
  INSERT 阻塞 11s → database is locked。
- 第二個 process 的 GraphStore() 因此變成寫者，撞上未提交 writer → locked
  ~11s → fail-silent None。

C2 修法（單點）: _init_db 中 current_version == _SCHEMA_VERSION 時跳過
schema_meta 的 INSERT/REPLACE 與其引起的 commit。

語意（本檔守住，不得誤讀）:
- C2 讓未提交 writer 存在時，新 Reader 能開連線並 SELECT 已提交快照。
- C2 不讓 Reader 看見 Writer 尚未 commit 的列（快照隔離）— 那不是 bug。
- Writer flush/commit 後，同一 Reader 的下一輪 SELECT 才看到新列。

隔離: 全部用 pytest tmp_path（TemporaryDirectory），0 production data。
"""

import sqlite3
import time
from pathlib import Path

import pytest

from src.memory.sage.graph_store import GraphStore, _SCHEMA_VERSION
from src.memory.sage.models import Fact


def _mk_fact(fact_id: str, subject: str = "測試主體", obj: str = "未提交的秘密") -> Fact:
    return Fact(
        fact_id=fact_id,
        subject=subject,
        predicate="記得",
        object=obj,
        timestamp=1750000000.0,
        weight=1.0,
    )


class TestC2ReaderOpenWhileWriterUncommitted:
    """A+B+C: writer 未 flush 時 reader 開連線瞬開、快照隔離、flush 後可見。"""

    def test_a_b_c_reader_open_select_flush_visibility(self, tmp_path: Path):
        db_path = tmp_path / "graph.sqlite"

        # Writer: 開庫（fresh → init 至 v9 並 commit）後 add_fact 一筆，不 flush 不 close
        writer = GraphStore(db_path=db_path)
        try:
            fact = _mk_fact("f_pending_1")
            writer.add_fact(fact)
            assert writer._pending_writes == 1  # 已 INSERT（未 commit）

            # A: 第二個 GraphStore 同 path — 建構 + SELECT 必須 < 1.0s、0 locked
            t0 = time.perf_counter()
            try:
                reader = GraphStore(db_path=db_path)
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc):
                    pytest.fail(
                        f"reader GraphStore() 撞 database is locked（C2 未生效）: {exc}"
                    )
                raise
            elapsed = time.perf_counter() - t0
            assert elapsed < 1.0, (
                f"reader 開連線耗時 {elapsed:.3f}s ≥ 1.0s（疑似仍在搶寫鎖）"
            )
            try:
                # B: 快照隔離 — reader 看不到未 commit 的列
                cnt = reader._get_conn().execute(
                    "SELECT COUNT(*) FROM facts"
                ).fetchone()[0]
                assert cnt == 0, f"reader 看見未 commit 的列（違反快照隔離）: {cnt}"
                assert reader.graph.number_of_edges() == 0

                # C: writer flush 後，同一 reader 下一輪 SELECT 看得到
                writer.flush()
                cnt = reader._get_conn().execute(
                    "SELECT COUNT(*) FROM facts"
                ).fetchone()[0]
                assert cnt == 1, f"flush 後 reader 仍看不到新列: {cnt}"
                row = reader._get_conn().execute(
                    "SELECT object FROM facts WHERE fact_id=?", (fact.fact_id,)
                ).fetchone()
                assert row is not None and row[0] == "未提交的秘密"
            finally:
                reader.close()
        finally:
            writer.close()

    def test_a_reader_open_one_fact_fast(self, tmp_path: Path):
        """A 獨立堅守: 多筆 pending 時 reader 開連線仍瞬開（batch 未滿不 commit）。"""
        db_path = tmp_path / "graph.sqlite"
        writer = GraphStore(db_path=db_path)
        try:
            for i in range(19):  # batch_size=20，19 筆仍不觸發自動 commit
                writer.add_fact(_mk_fact(f"f_{i}", obj=f"obj_{i}"))
            assert writer._pending_writes == 19
            t0 = time.perf_counter()
            reader = GraphStore(db_path=db_path)
            elapsed = time.perf_counter() - t0
            assert elapsed < 1.0, f"reader 開連線耗時 {elapsed:.3f}s ≥ 1.0s"
            cnt = reader._get_conn().execute(
                "SELECT COUNT(*) FROM facts"
            ).fetchone()[0]
            assert cnt == 0  # 19 筆全未 commit → 快照照樣看不到
            reader.close()
        finally:
            writer.close()


class TestC2NoWriteOnCurrentVersionOpen:
    """D: 已是 v9 的 DB，GraphStore() open 後 schema_meta 無新寫入。

    證明機制（SQLite 原生，未 mock）:
    1. 獨立監控連線的 PRAGMA data_version — SQLite 內建的「資料庫檔被
       其他連線修改」計數器；open 前後不變 ⇒ 本次 open 無任何 commit 落地。
    2. store 自身連線的 total_changes — 連線自開以來的 DML row 變更計數；
       0 ⇒ open 期間連一行 DML 都沒發（INSERT OR REPLACE 會計 1）。
    3. schema_meta version row 前後逐字不變。
    已知限制（誠實說明）: data_version 只反映「已提交」修改，不反映未提交
    寫入——但本測要證明的正是「無已提交寫入」；配合 A/B/C 行為測與
    _init_db 守衛 grep 佐證，三者合構成 C2 有效性的證據鏈。
    """

    def test_d_no_write_on_reopen_v9(self, tmp_path: Path):
        db_path = tmp_path / "graph.sqlite"

        # 先建一個已 v9 的庫（含一筆已提交 fact，確認庫「有內容」）
        base = GraphStore(db_path=db_path)
        try:
            base.add_fact(_mk_fact("f_committed_1", obj="已提交的事實"))
            base.flush()
        finally:
            base.close()

        monitor = sqlite3.connect(str(db_path))
        try:
            dv_before = monitor.execute("PRAGMA data_version").fetchone()[0]
            ver_before = monitor.execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()[0]

            store = GraphStore(db_path=db_path)  # C2: 不得寫入
            try:
                dv_after = monitor.execute("PRAGMA data_version").fetchone()[0]
                ver_after = monitor.execute(
                    "SELECT value FROM schema_meta WHERE key='version'"
                ).fetchone()[0]
                assert dv_after == dv_before, (
                    f"open 造成 DB 修改: data_version {dv_before} → {dv_after}"
                )
                assert ver_after == ver_before == str(_SCHEMA_VERSION), (
                    f"version row 被動過: {ver_before} → {ver_after}"
                )
                assert store._conn.total_changes == 0, (
                    f"open 期間該連線有 row change: total_changes="
                    f"{store._conn.total_changes}"
                )
                # 內容仍可讀（快照讀取正常）
                cnt = store._get_conn().execute(
                    "SELECT COUNT(*) FROM facts"
                ).fetchone()[0]
                assert cnt == 1
            finally:
                store.close()
        finally:
            monitor.close()


class TestC2InitAndMigration:
    """E+F: 新庫 initialize 到 v9；v8 → v9 migration 仍過（守衛不破壞 migration）。"""

    def test_e_fresh_db_initializes_to_v9(self, tmp_path: Path):
        db_path = tmp_path / "fresh.sqlite"
        store = GraphStore(db_path=db_path)
        try:
            row = store._get_conn().execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            assert row[0] == str(_SCHEMA_VERSION) == "9"
            cols = {
                c[1]
                for c in store._get_conn()
                .execute("PRAGMA table_info(facts)")
                .fetchall()
            }
            assert {"origin", "horizon_state", "learned_at"} <= cols
        finally:
            store.close()

    def test_f_v8_to_v9_migration_still_passes(self, tmp_path: Path):
        db_path = tmp_path / "v8.sqlite"
        _build_v8_db(db_path)
        store = GraphStore(db_path=db_path)
        try:
            row = store._get_conn().execute(
                "SELECT value FROM schema_meta WHERE key='version'"
            ).fetchone()
            assert row[0] == str(_SCHEMA_VERSION) == "9"
            cols = {
                c[1]
                for c in store._get_conn()
                .execute("PRAGMA table_info(facts)")
                .fetchall()
            }
            assert {"origin", "horizon_state", "learned_at"} <= cols
            cnt = store._get_conn().execute(
                "SELECT COUNT(*) FROM facts WHERE fact_id='f_old_1'"
            ).fetchone()[0]
            assert cnt == 1  # 既有資料 0 損傷
        finally:
            store.close()


def _build_v8_db(db_path: Path) -> None:
    """手動建造完整 v8 庫（schema_meta version=8 + facts 18 欄），模擬既有生產庫。

    與 tests/test_epistemic_horizon.py #_build_v8_db 同構（該檔為既有升級測）。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_meta VALUES ('version', '8')")
        conn.execute(
            """
            CREATE TABLE facts (
                fact_id    TEXT PRIMARY KEY,
                subject    TEXT NOT NULL,
                predicate  TEXT NOT NULL,
                object     TEXT NOT NULL,
                timestamp  REAL NOT NULL,
                weight     REAL NOT NULL DEFAULT 1.0,
                source     TEXT NOT NULL DEFAULT 'user',
                session_id TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                event_time REAL,
                is_anchor INTEGER NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 1.0,
                merged_from TEXT,
                merge_reason TEXT,
                source_pair TEXT NOT NULL DEFAULT '',
                inner_life_event_id TEXT NOT NULL DEFAULT '',
                valid_from REAL,
                invalidated_at REAL
            )
            """
        )
        conn.execute(
            "INSERT INTO facts (fact_id, subject, predicate, object, timestamp, weight)"
            " VALUES ('f_old_1', '雷姆', '記得', '羅茲瓦爾宅邸', 1750000000.0, 1.0)"
        )
        conn.commit()
    finally:
        conn.close()