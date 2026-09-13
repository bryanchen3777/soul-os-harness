"""
tests/social/test_sg3_band_recalibration.py — SG-3 §5 关系带重标定 + 结算层机制 (NEW)

覆盖（工单 SG-3-IMPL 新增测试要求）:
  1. 新门槛逐值在**结算全链**上的行为（stranger→known co=1 可升;
     known→familiar 需 co≥2 且 reply≥2; familiar→close 需 co≥4 且 reply≥4;
     dream 行已移除）
  2. 降带 DEMOTE_DAYS=90（含 90*86400 == 7776000 整数断言）
  3. **单窗增量上限=1**（同一窗多笔信号不得让计数跳 2）→ 结构上满足
     SG-3 §5.1「≥2 个不同 24h 窗（累计制）」
  4. reply 动力**读侧折抵**（每个共在 session 折抵 1 个 reply_exchange;
     0 新增 LLM 调用 / 0 新生产端）
  5. **OQ-3 带迁移结构化 log**（升带/降带各一行 BAND_MIGRATION, 含
     agent_id/other/from_band/to_band/counts/window_deltas/ts）
  6. **0 新文件 / 0 新 sqlite 表 / 0 新 schema 字段**（迁移历史只靠既有 logger）
  7. 预期生产效果载体: co=1 的既有对子在新门槛下升 known（含 SG-2.1 nuance）

运行: .\\.venv\\Scripts\\python.exe -m pytest tests/social/test_sg3_band_recalibration.py -q
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.goals.motive_provider import reset_goal_providers
from src.goals.seed_provider import reset_seed_providers
from src.memory.sage.graph_store import GraphStore
from src.paths import reset_data_root
from src.social.relation_settlement import settle_relations
from src.social.relational_bands import DEMOTE_DAYS

AGENT = "agent_sg3"
OTHER = "agent_akane"
ROOT = Path(__file__).resolve().parents[2]
LOGGER_NAME = "soul_os.social.relation_settlement"


# ───────────────────────────────────────────────────────────
# fixtures / helpers（对齐 tests/social/test_sg2_guardrails.py 隔离纪律）
# ───────────────────────────────────────────────────────────

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


def _entry(band: str = "stranger", reply: int = 0, co: int = 0, dream: int = 0,
           last_signal_at: str | None = None) -> Dict[str, Any]:
    return {
        "impression": "静かな人", "feeling": "neutral", "confidence": 0.0,
        "interaction_count": 3,
        "last_interaction_at": "2026-08-01T00:00:00+00:00",
        "last_updated": "2026-08-01T00:00:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
        "objective": {
            "reply_exchanges": reply, "co_presence_sessions": co,
            "dream_exchanges": dream, "last_signal_at": last_signal_at,
        },
        "impression_tags": [], "relational_band": band,
        "band_updated_at": None, "last_relation_update_ref": None,
    }


def _write_relationships(tmp_path: Path, others: Dict[str, Dict[str, Any]]) -> None:
    path = tmp_path / "soul" / AGENT / "relationships.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "agent_id": AGENT, "schema_version": "4.2",
        "created_at": "2026-09-01T00:00:00+00:00",
        "last_decay_at": "2026-09-01T00:00:00+00:00",
        "others": others,
    }, ensure_ascii=False), encoding="utf-8")


def _read_entry(tmp_path: Path) -> Dict[str, Any]:
    data = json.loads(
        (tmp_path / "soul" / AGENT / "relationships.json").read_text(encoding="utf-8")
    )
    return data["others"][OTHER]


def _write_co_presence(tmp_path: Path, ts: str, n: int = 1, other: str = OTHER) -> None:
    """写 n 笔共在 session（cross_chat 载体, 既有格式 0 变更）。"""
    path = tmp_path / "soul" / "interactions.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({
                "ts": ts, "type": "cross_chat", "seq": i,
                "agents": [AGENT, other], "content": "聊了音乐",
            }, ensure_ascii=False) + "\n")


def _write_reply(tmp_path: Path, ts: str, actor: str, n: int = 1) -> None:
    path = tmp_path / "world" / "perception_trace.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({
                "event_id": f"ev-{actor}-{i}", "timestamp": ts,
                "event_type": "reply",
                "extra": {"event_kind": "social", "actor_id": actor},
            }, ensure_ascii=False) + "\n")


def _snapshot(tmp_path: Path) -> List[str]:
    return sorted(
        str(p.relative_to(tmp_path)).replace("\\", "/")
        for p in tmp_path.rglob("*") if p.is_file()
    )


# ───────────────────────────────────────────────────────────
# 1. 新门槛在结算全链上的行为
# ───────────────────────────────────────────────────────────

class TestSettleThresholds:
    def test_co1_entry_upgrades_to_known_on_next_signal_window(self, iso_env):
        """門檻重標定的**預期生產效果載體**: 既有 co=1 的 stranger 對子
        （舊表 co≥2 差 1 而永遠卡住）在**下一個帶訊號的結算窗**升 known。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("stranger", co=1, reply=0,
                                                 last_signal_at="2026-09-05T10:00:00+00:00")})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 1)
        res = settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                               base_dir=tmp)
        assert res["skipped"] is None
        entry = _read_entry(tmp)
        assert entry["relational_band"] == "known"
        assert entry["objective"]["co_presence_sessions"] == 2  # 1 + 1（單窗上限）
        assert entry["band_updated_at"] == "2026-09-06T10:00:00+00:00"

    def test_co1_entry_without_new_signal_does_not_climb(self, iso_env):
        """誠實記載 SG-2.1 nuance: 底帶 stranger **無訊號窗不慢爬回升**
        → co=1 的既有對子遇到 0 訊號窗不會升帶; 效果落在第一次 pair 觸碰後。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("stranger", co=1, reply=0,
                                                 last_signal_at="2026-09-05T10:00:00+00:00")})
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        entry = _read_entry(tmp)
        assert entry["relational_band"] == "stranger"  # SG-2.1: 無訊號底帶不回升
        assert entry["objective"]["co_presence_sessions"] == 1  # 計數 0 增量

    def test_known_to_familiar_needs_two_windows(self, iso_env):
        """known→familiar 需 co≥2 **且** reply≥2; 靠單窗增量上限 1 累計跨 ≥2 窗。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=0, reply=0)})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 1)
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        e1 = _read_entry(tmp)
        assert (e1["objective"]["co_presence_sessions"],
                e1["objective"]["reply_exchanges"]) == (1, 1)
        assert e1["relational_band"] == "known"  # 窗 1: co=1/reply=1 < 2 → 不升
        # 窗 2（跨 24h, 新訊號）→ co=2 / reply=2 → familiar
        _write_co_presence(tmp, "2026-09-07T08:00:00+00:00", 1)
        settle_relations(AGENT, now=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        e2 = _read_entry(tmp)
        assert (e2["objective"]["co_presence_sessions"],
                e2["objective"]["reply_exchanges"]) == (2, 2)
        assert e2["relational_band"] == "familiar"

    def test_familiar_to_close_needs_four_windows(self, iso_env):
        """familiar→close 需 co≥4 且 reply≥4 → 至少 4 個不同結算窗。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("familiar", co=0, reply=0)})
        bands = []
        for day in (6, 7, 8, 9):
            _write_co_presence(tmp, f"2026-09-{day:02d}T08:00:00+00:00", 1)
            settle_relations(
                AGENT, now=datetime(2026, 9, day, 10, tzinfo=timezone.utc), base_dir=tmp,
            )
            bands.append(_read_entry(tmp)["relational_band"])
        assert bands == ["familiar", "familiar", "familiar", "close"]
        e = _read_entry(tmp)
        assert (e["objective"]["co_presence_sessions"],
                e["objective"]["reply_exchanges"]) == (4, 4)

    def test_dream_row_no_longer_upgrades(self, iso_env):
        """OQ-4: dream 行已刪除 → 夢境訊號不再單獨推升 familiar→close。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("familiar", co=0, reply=0, dream=4)})
        _write_reply(tmp, "2026-09-06T09:00:00+00:00", AGENT, 5)
        _write_reply(tmp, "2026-09-06T09:00:00+00:00", OTHER, 5)
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        entry = _read_entry(tmp)
        # dream=4 已存在 + reply 折抵 1 → 若舊 dream 行仍在則升 close; 現應保持
        assert entry["relational_band"] == "familiar"
        assert [t for t, _m in __import__(
            "src.social.relational_bands", fromlist=["x"]
        )._CLOSE_THRESHOLDS] == [{"reply_exchanges": 4, "co_presence_sessions": 4}]


# ───────────────────────────────────────────────────────────
# 2. 单窗增量上限 = 1（SG-3 §5.1「≥2 個不同 24h 窗」實作機制）
# ───────────────────────────────────────────────────────────

class TestSingleWindowDeltaCap:
    def test_many_signals_in_one_window_cap_at_one(self, iso_env):
        """同一窗 5 筆共在 + 雙方各 5 筆 reply → 增量仍為 1/1（不得跳 2）。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("stranger", co=0, reply=0)})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 5)
        _write_reply(tmp, "2026-09-06T08:30:00+00:00", AGENT, 5)
        _write_reply(tmp, "2026-09-06T08:30:00+00:00", OTHER, 5)
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        e = _read_entry(tmp)
        assert e["objective"]["co_presence_sessions"] == 1
        assert e["objective"]["reply_exchanges"] == 1
        # 單窗不得直達 familiar 門檻（co≥2 且 reply≥2）
        assert e["relational_band"] == "known"

    def test_cap_holds_across_two_windows(self, iso_env):
        """窗 1 上限 1 + 窗 2 上限 1 → 累計 2（結構上必然跨 ≥2 窗）。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=0, reply=0)})
        for day, cap in ((6, 1), (7, 2)):
            _write_co_presence(tmp, f"2026-09-{day:02d}T08:00:00+00:00", 9)
            settle_relations(
                AGENT, now=datetime(2026, 9, day, 10, tzinfo=timezone.utc), base_dir=tmp,
            )
            e = _read_entry(tmp)
            assert e["objective"]["co_presence_sessions"] == cap
            assert e["objective"]["reply_exchanges"] == cap

    def test_dream_delta_not_capped_but_always_zero(self, iso_env):
        """dream 增量不受窗上限（無門可命中, v1 恆 0）。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=0, reply=0)})
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp, force=True)
        assert _read_entry(tmp)["objective"]["dream_exchanges"] == 0


# ───────────────────────────────────────────────────────────
# 3. reply 动力读侧折抵（SG-3 §4.3）
# ───────────────────────────────────────────────────────────

class TestReplyReadSideFolding:
    def test_co_session_folds_into_one_reply_exchange(self, iso_env):
        """每個既有共在 session 折抵 1 個 reply_exchange（0 新增 LLM 調用）。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=0, reply=0)})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 1)  # 無任何 reply 事件
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        e = _read_entry(tmp)
        assert e["objective"]["reply_exchanges"] == 1  # 折抵來源 = 共在 session
        assert e["objective"]["co_presence_sessions"] == 1

    def test_reply_and_co_are_same_source_len(self, iso_env):
        """明示後果（SG-3 §4.3 設計, 非偷懶）: 現行 R 下 reply 與 co 逐窗恆等
        → known→familiar 的 `且 reply≥2` 目前與 `co≥2` 等價（該 and 不具約束力）。
        本測試把該等價**釘住**, 以免未來讀者誤解為 bug。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=0, reply=0)})
        for day in (6, 7, 8):
            _write_co_presence(tmp, f"2026-09-{day:02d}T08:00:00+00:00", 3)
            settle_relations(
                AGENT, now=datetime(2026, 9, day, 10, tzinfo=timezone.utc), base_dir=tmp,
            )
            e = _read_entry(tmp)
            assert e["objective"]["reply_exchanges"] == e["objective"]["co_presence_sessions"]


# ───────────────────────────────────────────────────────────
# 4. 降带 90 天（SG-3 §5.2）
# ───────────────────────────────────────────────────────────

class TestDemoteViaSettle:
    def test_integer_seconds_pin(self):
        assert DEMOTE_DAYS == 90
        assert DEMOTE_DAYS * 86400 == 7776000

    def test_demote_after_91_days_no_signal(self, iso_env):
        tmp = iso_env
        # 計數 (2,2) 剛過 familiar 門檻但未達 close（co≥4 且 reply≥4）→
        # 無訊號窗不會被慢爬順帶推到 close, 確保本測試只量降帶。
        _write_relationships(tmp, {OTHER: _entry(
            "familiar", co=2, reply=2, last_signal_at="2026-06-01T10:00:00+00:00")})
        now = datetime(2026, 6, 1, 10, tzinfo=timezone.utc) + timedelta(days=91)
        res = settle_relations(AGENT, now=now, base_dir=tmp)
        assert res["demoted"] == 1
        assert _read_entry(tmp)["relational_band"] == "known"

    def test_no_demote_at_exactly_90_days(self, iso_env):
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry(
            "familiar", co=2, reply=2, last_signal_at="2026-06-01T10:00:00+00:00")})
        now = datetime(2026, 6, 1, 10, tzinfo=timezone.utc) + timedelta(days=90)
        res = settle_relations(AGENT, now=now, base_dir=tmp)
        assert res["demoted"] == 0
        assert _read_entry(tmp)["relational_band"] == "familiar"

    def test_no_demote_at_45_days_old_30_day_rule(self, iso_env):
        """舊 30 天規則下 45 天必降; 新 90 天規則下 45 天不降（回歸釘住）。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry(
            "familiar", co=2, reply=2, last_signal_at="2026-06-01T10:00:00+00:00")})
        now = datetime(2026, 6, 1, 10, tzinfo=timezone.utc) + timedelta(days=45)
        res = settle_relations(AGENT, now=now, base_dir=tmp)
        assert res["demoted"] == 0
        assert _read_entry(tmp)["relational_band"] == "familiar"


# ───────────────────────────────────────────────────────────
# 5. OQ-3 带迁移结构化 log（0 新文件 / 0 新表 / 0 新字段）
# ───────────────────────────────────────────────────────────

class TestBandMigrationStructuredLog:
    def _migrations(self, caplog) -> List[Dict[str, Any]]:
        out = []
        for rec in caplog.records:
            msg = rec.getMessage()
            marker = "[RelSettle][BAND_MIGRATION] "
            if marker in msg:
                out.append(json.loads(msg.split(marker, 1)[1]))
        return out

    def test_promote_emits_one_structured_line(self, iso_env, caplog):
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("stranger", co=1, reply=0,
                                                 last_signal_at="2026-09-05T10:00:00+00:00")})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 1)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                             base_dir=tmp)
        ms = self._migrations(caplog)
        assert len(ms) == 1
        m = ms[0]
        assert m["agent_id"] == AGENT
        assert m["other"] == OTHER
        assert m["from_band"] == "stranger"
        assert m["to_band"] == "known"
        assert m["direction"] == "promote"
        assert m["counts"] == {"reply_exchanges": 1, "co_presence_sessions": 2,
                               "dream_exchanges": 0}
        assert m["window_deltas"] == {"reply_exchanges": 1, "co_presence_sessions": 1,
                                      "dream_exchanges": 0}
        assert m["ts"] == "2026-09-06T10:00:00+00:00"
        assert m["ref"] == f"rel:{OTHER}:2026-09-06T10:00:00+00:00"

    def test_demote_emits_one_structured_line(self, iso_env, caplog):
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry(
            "familiar", co=5, reply=5, last_signal_at="2026-06-01T10:00:00+00:00")})
        now = datetime(2026, 6, 1, 10, tzinfo=timezone.utc) + timedelta(days=91)
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            settle_relations(AGENT, now=now, base_dir=tmp)
        ms = self._migrations(caplog)
        assert len(ms) == 1
        assert (ms[0]["from_band"], ms[0]["to_band"]) == ("familiar", "known")
        assert ms[0]["direction"] == "demote"
        assert ms[0]["window_deltas"] == {"reply_exchanges": 0, "co_presence_sessions": 0,
                                          "dream_exchanges": 0}

    def test_no_migration_no_structured_line(self, iso_env, caplog):
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("known", co=1, reply=1,
                                                 last_signal_at="2026-09-05T10:00:00+00:00")})
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                             base_dir=tmp)
        assert self._migrations(caplog) == []

    def test_zero_new_files_tables_fields(self, iso_env):
        """OQ-3 裁定 = 不建歷史層: 遷移只寫既有 logger。
        0 新 jsonl trace 檔 / 0 新 sqlite 表 / 0 新 schema 欄位。"""
        tmp = iso_env
        _write_relationships(tmp, {OTHER: _entry("stranger", co=1, reply=0,
                                                 last_signal_at="2026-09-05T10:00:00+00:00")})
        _write_co_presence(tmp, "2026-09-06T08:00:00+00:00", 1)
        before = _snapshot(tmp)  # fixture 寫完後才取快照（只量 settle 的產物）
        settle_relations(AGENT, now=datetime(2026, 9, 6, 10, tzinfo=timezone.utc),
                         base_dir=tmp)
        after = _snapshot(tmp)
        created = [p for p in after if p not in before]
        # 只允許既有檔案（sidecar goal_provider.json）被建立; 0 新 trace / 0 新 db
        assert created == ["memory/agent_sg3/goal_provider.json"], created
        assert not any("band" in p or "migration" in p for p in created)
        # schema 欄位 0 變更（entry 鍵集合與遷移前一致）
        entry = _read_entry(tmp)
        assert set(entry.keys()) == {
            "impression", "feeling", "confidence", "interaction_count",
            "last_interaction_at", "last_updated", "created_at", "objective",
            "impression_tags", "relational_band", "band_updated_at",
            "last_relation_update_ref",
        }
        # 0 新 sqlite 表
        db = tmp / "memory" / AGENT / "graph.sqlite"
        conn = sqlite3.connect(db)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
        assert not any("band" in t or "migration" in t for t in tables)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
