# tests/soul/test_world_collision_window.py
# WORLD-COLLISION-WINDOW-1 — 碰撞回溯窗 4h→14h（全合成，不讀生產、不靠真實時間）。
#
# 受測常數（兩處同口徑，須同值＝14）：
#   - `src/soul/life_thread_wake_gate.py::WORLD_COLLISION_WINDOW_HOURS`（M3 閘門側）
#   - `src/soul/life_thread_origins.py::WORLD_COLLISION_WINDOW_HOURS`（M4 起源側）
# 本檔只測「窗內納入／窗外丟棄＋兩處同值」，不碰評分表、不碰 0.35 門檻、
# 不碰 slot、不碰容量邏輯；全合成 in-memory（不讀 `data/**`、不寫檔、不靠真實時間）。
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.soul import life_thread_origins as m4  # noqa: E402
from src.soul import life_thread_wake_gate as m3  # noqa: E402

AGENT = "agent_synth_window"
NOW_EPOCH = 1_800_000_000.0  # 固定合成 epoch 秒（UTC），決定性輸入
NOW_UTC = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # 固定合成 now
CAP = 3


def _iso_epoch(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _gate_record(age_hours: float) -> dict:
    return {
        "accepted": True,
        "source": "weather",
        "extra": {"summary": "合成事實文字"},
        "timestamp": _iso_epoch(NOW_EPOCH - age_hours * 3600),
        "event_id": "evt-synth",
    }


def _origin_record(age_hours: float) -> dict:
    return {
        "accepted": True,
        "source": "news",
        "event_type": "news_event",
        "extra": {"summary": "合成事實文字"},
        "timestamp": (NOW_UTC - timedelta(hours=age_hours)).isoformat(),
    }


def test_constants_same_caliber_equal_14():
    """防單改一處：兩處同名常數須相等且同為 14。"""
    assert m3.WORLD_COLLISION_WINDOW_HOURS == 14
    assert m4.WORLD_COLLISION_WINDOW_HOURS == 14
    assert m3.WORLD_COLLISION_WINDOW_HOURS == m4.WORLD_COLLISION_WINDOW_HOURS


def test_gate_in_window_13_9h_wakes():
    """M3 閘門：13.9h（窗內）⇒ WAKE / world_collision。"""
    decision = m3.evaluate_wake_gate(
        agent_id=AGENT,
        current_time=NOW_EPOCH,
        timeslot="daytime",
        active_threads=[],
        capacity_limit=CAP,
        recent_perceptions=[_gate_record(13.9)],
    )
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.origin_type == "world_collision"


def test_gate_out_window_14_1h_sleeps():
    """M3 閘門：14.1h（窗外）⇒ SLEEP / 留白，不捏造。"""
    decision = m3.evaluate_wake_gate(
        agent_id=AGENT,
        current_time=NOW_EPOCH,
        timeslot="daytime",
        active_threads=[],
        capacity_limit=CAP,
        recent_perceptions=[_gate_record(14.1)],
    )
    assert decision.should_wake is False
    assert decision.origin_type is None
    assert decision.seed_hint is None


def test_origins_in_window_13_9h_included():
    """M4 起源：13.9h（窗內）⇒ 納入 facts。"""
    seeds = m4.collect_world_seeds(AGENT, now=NOW_UTC, records=[_origin_record(13.9)])
    assert len(seeds["facts"]) == 1
    assert seeds["facts"][0]["summary"] == "合成事實文字"


def test_origins_out_window_14_1h_excluded():
    """M4 起源：14.1h（窗外）⇒ 丟棄，facts 為空。"""
    seeds = m4.collect_world_seeds(AGENT, now=NOW_UTC, records=[_origin_record(14.1)])
    assert seeds["facts"] == []
