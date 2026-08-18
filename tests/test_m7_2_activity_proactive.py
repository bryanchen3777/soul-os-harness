"""
test_m7_2_activity_proactive.py
M7-2 (Bry 拍板 2026-08-18): 活動驅動主動傳訊 (enrichment)

驗證 scheduler._get_recent_shareable_activity:
  A. 無 diary / 無 shareable 活動 → None
  B. 只挑 slot=="event" + shareable==True + source=="llm"
  C. 多個 shareable 活動 → 回最新 (ts 最大)
"""
import json
import os
from datetime import datetime
from pathlib import Path

from src.paths import data_root, reset_data_root
from src.soul.scheduler import SoulScheduler


def _isolated(tmp_path: Path):
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return soul_dir


def _restore():
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _write_entry(soul_dir: Path, agent_id: str, entry: dict):
    path = soul_dir / agent_id / "diary" / f"{_today()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _entry(slot="event", shareable=True, source="llm", activity="工作",
           category="work", content="今日は仕事を進めた。", ts="2026-08-18T10:00:00+00:00"):
    return {
        "ts": ts,
        "slot": slot,
        "content": content,
        "source": source,
        "activity": activity,
        "category": category,
        "shareable": shareable,
    }


class TestGetRecentShareableActivity:
    def test_a1_no_diary_returns_none(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            sched = SoulScheduler()
            assert sched._get_recent_shareable_activity("agent_ruka") is None
        finally:
            _restore()

    def test_a2_no_shareable_returns_none(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            # shareable=False
            _write_entry(soul_dir, "agent_ruka", _entry(shareable=False))
            sched = SoulScheduler()
            assert sched._get_recent_shareable_activity("agent_ruka") is None
        finally:
            _restore()

    def test_a3_filters_non_llm_source(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            # source=placeholder 不應被挑 (只有 llm 是真實活動)
            _write_entry(soul_dir, "agent_ruka", _entry(source="placeholder"))
            sched = SoulScheduler()
            assert sched._get_recent_shareable_activity("agent_ruka") is None
        finally:
            _restore()

    def test_a4_filters_non_event_slot(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            # diary (morning/night/dream) 不是 event, 不應被挑
            _write_entry(soul_dir, "agent_ruka", _entry(slot="morning"))
            _write_entry(soul_dir, "agent_ruka", _entry(slot="night"))
            _write_entry(soul_dir, "agent_ruka", _entry(slot="dream"))
            sched = SoulScheduler()
            assert sched._get_recent_shareable_activity("agent_ruka") is None
        finally:
            _restore()

    def test_b1_returns_latest_shareable_activity(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            _write_entry(soul_dir, "agent_ruka", _entry(ts="2026-08-18T09:00:00+00:00", activity="做飯", content="パンケーキを作った。"))
            _write_entry(soul_dir, "agent_ruka", _entry(ts="2026-08-18T12:00:00+00:00", activity="工作", content="締め切りを越えた。"))
            sched = SoulScheduler()
            result = sched._get_recent_shareable_activity("agent_ruka")
            assert result is not None
            assert result["activity"] == "工作"
            assert result["content"] == "締め切りを越えた。"
            assert result["ts"] == "2026-08-18T12:00:00+00:00"
        finally:
            _restore()

    def test_b2_returns_metadata_fields(self, tmp_path):
        soul_dir = _isolated(tmp_path)
        try:
            _write_entry(soul_dir, "agent_ruka", _entry(activity="散步", category="leisure"))
            sched = SoulScheduler()
            result = sched._get_recent_shareable_activity("agent_ruka")
            assert result is not None
            assert set(result.keys()) == {"activity", "category", "content", "ts"}
            assert result["activity"] == "散步"
            assert result["category"] == "leisure"
        finally:
            _restore()
