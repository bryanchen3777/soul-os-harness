# tests/soul/test_life_thread_wake_gate_m3.py
# LIFE-THREAD-M3-1 — 二元存在性喚醒閘門（Salience Gate）測試矩陣 T1~T10。
#
# 受測模組（唯讀）：`src/soul/life_thread_wake_gate.py`
# 模組為 pure function（0 I/O / 0 LLM / 0 定時器 / 0 `src.*` import），
# 因此本檔**不讀寫 `data/**`**、不啟停任何服務、不動 `src/**`。
#
# 慣例對齊 `tests/soul/test_life_threads_m1.py`：`tests/soul/` 無 `__init__.py`，
# 本檔自行 `sys.path.insert(0, REPO_ROOT)`。
from __future__ import annotations

import ast
import json
import logging
import re
import sys
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.soul.life_thread_wake_gate import (  # noqa: E402
    LOG_MAX_CHARS,
    ORIGIN_TYPES,
    QUALIFYING_WORLD_SOURCES,
    REASON_ACTIVE_POOL_SATURATED,
    REASON_CHECKPOINT_DUE_WAKE,
    REASON_DAYTIME_WHITESPACE,
    REASON_FAIL_CLOSED_DEFAULT_SLEEP,
    REASON_REFLECTION_SLOT_CLEAR,
    REASON_TENSION_DUE_WAKE,
    REASON_WORLD_COLLISION_WAKE,
    REFLECTION_SLOTS,
    SEED_HINT_MAX_ITEMS,
    SEED_HINT_MAX_TEXT_CHARS,
    VALID_TIMESLOTS,
    WORLD_COLLISION_WINDOW_HOURS,
    WakeDecision,
    evaluate_wake_gate,
)

MODULE_PATH = _REPO_ROOT / "src" / "soul" / "life_thread_wake_gate.py"
MODULE_QUALNAME = "life_thread_wake_gate"
LOGGER_NAME = "soul_os.soul.life_thread_wake_gate"

AGENT = "agent_ruka"
NOW = 1_800_000_000.0  # 固定 epoch 秒（UTC），決定性輸入
CAP = 3  # 預設容量上限（> 0，且大於本檔多數案例的 mapping 數）

_DROP = object()  # `_world_record(**overrides)` 的「刪欄」哨兵


# ──────────────────────────────────────────────────────────────
# 夾具（純 dict / list，0 I/O）
# ──────────────────────────────────────────────────────────────

def _iso(ts: float) -> str:
    """epoch 秒 → ISO-8601 UTC 字串（尾端 `Z`，M1 生產形狀）。"""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _thread(thread_id: str = "th-1", status: str = "active",
            check_after_ts=None, origin_type=None) -> dict:
    """M1 `list_active()` 的線頭列形狀。"""
    return {
        "thread_id": thread_id,
        "status": status,
        "check_after_ts": check_after_ts,
        "origin_type": origin_type,
    }


def _tension(tension_id: str = "tn-1", **overrides) -> dict:
    """未決張力列（到期欄位為 `check_after_ts` 或別名 `due_at`）。"""
    row = {"tension_id": tension_id, "check_after_ts": _iso(NOW - 60)}
    row.update(overrides)
    return row


def _world_record(**overrides) -> dict:
    """合格的 perception_trace 記錄；`overrides` 用 `_DROP` 可整欄刪除。"""
    record = {
        "accepted": True,
        "source": "weather",
        "extra": {"summary": "外頭颳起大風"},
        "timestamp": _iso(NOW - 60),
    }
    for key, value in overrides.items():
        if value is _DROP:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def _call(**overrides) -> WakeDecision:
    """以固定合法輸入呼叫閘門，只覆寫指定欄位。"""
    params = {
        "agent_id": AGENT,
        "current_time": NOW,
        "timeslot": "daytime",
        "active_threads": [],
        "capacity_limit": CAP,
        "recent_perceptions": None,
        "unresolved_tensions": None,
    }
    params.update(overrides)
    return evaluate_wake_gate(**params)


def _gate_lines(caplog) -> list[str]:
    """本次呼叫產生、屬於本模組 logger 的 INFO 觀測行。"""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == LOGGER_NAME and record.levelno == logging.INFO
    ]


@pytest.fixture
def gate_logs(caplog):
    """把本模組 logger 開到 INFO（root 預設 WARNING 否則收不到）。"""
    caplog.set_level(logging.INFO, logger=LOGGER_NAME)
    return caplog


# ══════════════════════════════════════════════════════════════
# T1 容量飽和（工單 §4）：容量防線壓過一切
# ══════════════════════════════════════════════════════════════

_COLLISION = [_world_record()]
_DUE_THREAD = [_thread("th-due", check_after_ts=_iso(NOW - 300), origin_type="goal_driven")]
_DUE_TENSION = [_tension("tn-due")]


def _saturated_case(capacity: int, disturbance: str) -> dict:
    """活躍 mapping 數恰好等於 `capacity`，再依情境疊加擾動。"""
    if disturbance == "quiet":
        threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(capacity)]
        perceptions, tensions = None, None
    elif disturbance == "world_collision":
        threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(capacity)]
        perceptions, tensions = list(_COLLISION), None
    elif disturbance == "thread_due":
        threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(capacity)]
        threads[-1] = _thread("th-due", check_after_ts=_iso(NOW - 300), origin_type="goal_driven")
        perceptions, tensions = None, None
    elif disturbance == "tension_due":
        threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(capacity)]
        perceptions, tensions = None, list(_DUE_TENSION)
    elif disturbance == "all_at_once":
        threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(capacity)]
        threads[-1] = _thread("th-due", check_after_ts=_iso(NOW - 300), origin_type="goal_driven")
        perceptions, tensions = list(_COLLISION), list(_DUE_TENSION)
    else:  # pragma: no cover - 參數表錯字才能到此
        raise AssertionError(f"unknown disturbance: {disturbance}")
    return {
        "timeslot": "morning",
        "active_threads": threads,
        "capacity_limit": capacity,
        "recent_perceptions": perceptions,
        "unresolved_tensions": tensions,
    }


@pytest.mark.parametrize("disturbance",
                         ["quiet", "world_collision", "thread_due", "tension_due", "all_at_once"])
@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_t1_active_pool_saturated_always_sleeps(capacity, disturbance):
    """T1：容量防線最高優先，即使同時有碰撞／到期線頭／到期張力也一律 SLEEP。"""
    decision = _call(**_saturated_case(capacity, disturbance))
    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"
    assert decision.reason == REASON_ACTIVE_POOL_SATURATED
    assert decision.origin_type is None
    assert decision.seed_hint is None


def test_t1_capacity_threshold_is_inclusive_equality():
    """T1：`active_count == capacity_limit` 即飽和（>= 為界）。"""
    decision = _call(active_threads=[_thread("th-1")], capacity_limit=1)
    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"


def test_t1_dormant_mapping_still_counts_toward_capacity():
    """T1：容量只數「mapping 元素個數」，dormant 的 mapping 一樣佔位。"""
    decision = _call(active_threads=[_thread("th-1", status="dormant")], capacity_limit=1)
    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"


def test_t1_non_mapping_elements_do_not_count_toward_capacity():
    """T1：非 mapping 元素不佔容量；未飽和時回到留白語意。"""
    decision = _call(active_threads=["not-a-mapping", 42, None], capacity_limit=1)
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


# ══════════════════════════════════════════════════════════════
# T2 自省槽點（morning / night）
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("timeslot", ["morning", "night"])
def test_t2_reflection_slot_due_thread_wakes(timeslot):
    """T2：槽點 + 到期線頭 ⇒ WAKE / CHECKPOINT_DUE_WAKE（origin 取線頭自身）。"""
    decision = _call(
        timeslot=timeslot,
        active_threads=[_thread("th-due", check_after_ts=_iso(NOW - 10),
                                origin_type="necessity_driven")],
    )
    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE
    assert decision.origin_type == "necessity_driven"
    assert decision.seed_hint["due_thread_ids"] == ["th-due"]


@pytest.mark.parametrize("timeslot", ["morning", "night"])
def test_t2_reflection_slot_due_tension_wakes(timeslot):
    """T2：槽點 + 到期張力 ⇒ WAKE / UNRESOLVED_TENSION_DUE_WAKE / necessity_driven。"""
    decision = _call(timeslot=timeslot, unresolved_tensions=[_tension("tn-due")])
    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.reason == REASON_TENSION_DUE_WAKE
    assert decision.origin_type == "necessity_driven"
    assert decision.seed_hint["tension_ids"] == ["tn-due"]


@pytest.mark.parametrize("timeslot", ["morning", "night"])
def test_t2_reflection_slot_clear_sleeps(timeslot):
    """T2：槽點無擾動無到期 ⇒ SLEEP / REFLECTION_SLOT_CLEAR。"""
    decision = _call(
        timeslot=timeslot,
        active_threads=[_thread("th-idle", check_after_ts=None)],
    )
    assert decision.should_wake is False
    assert decision.reason == "REFLECTION_SLOT_CLEAR"
    assert decision.reason == REASON_REFLECTION_SLOT_CLEAR
    assert decision.origin_type is None
    assert decision.seed_hint is None


def test_t2_reflection_slots_are_morning_and_night():
    """T2：槽點常數值域釘死。"""
    assert tuple(REFLECTION_SLOTS) == ("morning", "night")
    assert tuple(VALID_TIMESLOTS) == ("morning", "daytime", "evening", "night")


# ══════════════════════════════════════════════════════════════
# T3 非槽點留白（daytime / evening）＋ 到期挑選細節
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("timeslot", ["daytime", "evening"])
def test_t3_whitespace_slot_sleeps(timeslot):
    """T3：非槽點、無擾動無到期 ⇒ SLEEP / DAYTIME_WHITESPACE（留白率守護）。"""
    decision = _call(
        timeslot=timeslot,
        active_threads=[_thread("th-idle", check_after_ts=None)],
    )
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"
    assert decision.reason == REASON_DAYTIME_WHITESPACE
    assert decision.origin_type is None
    assert decision.seed_hint is None


@pytest.mark.parametrize("timeslot", ["daytime", "evening"])
def test_t3_whitespace_slot_world_collision_wakes(timeslot):
    """T3：非槽點注入世界碰撞 ⇒ WAKE（碰撞壓過留白）。"""
    decision = _call(timeslot=timeslot, recent_perceptions=list(_COLLISION))
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.origin_type == "world_collision"


@pytest.mark.parametrize("timeslot", ["daytime", "evening"])
def test_t3_whitespace_slot_due_thread_wakes(timeslot):
    """T3：非槽點注入到期線頭 ⇒ WAKE。"""
    decision = _call(timeslot=timeslot, active_threads=list(_DUE_THREAD))
    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.origin_type == "goal_driven"


@pytest.mark.parametrize("timeslot", ["daytime", "evening"])
def test_t3_whitespace_slot_due_tension_wakes(timeslot):
    """T3：非槽點注入到期張力 ⇒ WAKE。"""
    decision = _call(timeslot=timeslot, unresolved_tensions=list(_DUE_TENSION))
    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.origin_type == "necessity_driven"


def test_t3_earliest_due_thread_origin_wins():
    """T3：多筆到期 ⇒ origin_type 取**最早到期**那筆；清單亦以其為首。"""
    threads = [
        _thread("th-late", check_after_ts=_iso(NOW - 10), origin_type="whim_driven"),
        _thread("th-early", check_after_ts=_iso(NOW - 3000), origin_type="necessity_driven"),
    ]
    decision = _call(active_threads=threads)
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.origin_type == "necessity_driven"
    assert decision.seed_hint["due_thread_ids"] == ["th-early", "th-late"]


@pytest.mark.parametrize("bad_origin", ["bogus", None, "", 7])
def test_t3_illegal_thread_origin_falls_back_to_goal_driven(bad_origin):
    """T3：到期線頭 origin_type 非法 ⇒ 回退 "goal_driven"。"""
    decision = _call(
        active_threads=[_thread("th-due", check_after_ts=_iso(NOW - 10), origin_type=bad_origin)]
    )
    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.origin_type == "goal_driven"


@pytest.mark.parametrize("status", ["dormant", "completed", "abandoned", "expired"])
def test_t3_non_active_thread_status_never_wakes(status):
    """T3：非 "active" 的線頭即使到期也不喚醒。"""
    decision = _call(
        active_threads=[_thread("th-x", status=status, check_after_ts=_iso(NOW - 10))]
    )
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


@pytest.mark.parametrize("raw", [None, "not-a-timestamp", "", "   ", NOW + 3600, []])
def test_t3_thread_check_after_unusable_never_wakes(raw):
    """T3：`check_after_ts` 缺欄／不可解析／未到期 ⇒ 跳過該線頭，不 raise。"""
    decision = _call(active_threads=[_thread("th-x", check_after_ts=raw)])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


@pytest.mark.parametrize("element", ["plain-string", 42, None, ["nested"]])
def test_t3_non_mapping_thread_element_is_skipped(element):
    """T3：`active_threads` 內非 mapping 元素一律跳過。"""
    decision = _call(active_threads=[element])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t3_thread_due_boundary_is_inclusive():
    """T3：`check_after_ts == current_time` 算到期（<= 為界）。"""
    decision = _call(
        active_threads=[_thread("th-due", check_after_ts=NOW, origin_type="goal_driven")]
    )
    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"


def test_t3_thread_check_after_accepts_epoch_and_datetime():
    """T3：`check_after_ts` 接受 epoch 數值與 datetime（M1 生產形狀為 ISO 字串）。"""
    numeric = _call(active_threads=[
        _thread("th-num", check_after_ts=int(NOW) - 60, origin_type="goal_driven")
    ])
    moment = _call(active_threads=[
        _thread("th-dt", check_after_ts=datetime.fromtimestamp(NOW - 60, tz=timezone.utc),
                origin_type="goal_driven")
    ])
    assert (numeric.should_wake, numeric.reason) == (True, "CHECKPOINT_DUE_WAKE")
    assert (moment.should_wake, moment.reason) == (True, "CHECKPOINT_DUE_WAKE")


# ── T3b：張力到期語意（due-based）─────────────────────────────

def test_t3_tension_due_at_alias_wakes():
    """T3b：別名 `due_at` 到期 ⇒ WAKE。"""
    decision = _call(unresolved_tensions=[
        {"tension_id": "tn-alias", "due_at": _iso(NOW - 5)}
    ])
    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.origin_type == "necessity_driven"
    assert decision.seed_hint["tension_ids"] == ["tn-alias"]


def test_t3_tension_without_due_time_never_wakes():
    """T3b：未帶到期時間的張力**不喚醒**（契約 §4.2 due-based）。"""
    decision = _call(unresolved_tensions=[
        {"tension_id": "tn-undated", "text": "還沒想清楚的事"}
    ])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


@pytest.mark.parametrize("raw", ["not-a-timestamp", "", 7.5e9, [], {}])
def test_t3_tension_unusable_due_never_wakes(raw):
    """T3b：張力到期時間不可解析／未到期 ⇒ 不喚醒、不 raise。"""
    decision = _call(unresolved_tensions=[{"tension_id": "tn-x", "check_after_ts": raw}])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t3_tension_due_boundary_is_inclusive():
    """T3b：`due == current_time` 算到期（<= 為界）。"""
    decision = _call(unresolved_tensions=[
        {"tension_id": "tn-now", "check_after_ts": _iso(NOW)}
    ])
    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"


@pytest.mark.parametrize("element", ["plain-string", 42, None])
def test_t3_non_mapping_tension_is_skipped(element):
    """T3b：張力列非 mapping ⇒ 跳過。"""
    decision = _call(unresolved_tensions=[element])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t3_due_tension_without_id_still_wakes():
    """T3b：到期張力即使沒有可用 id 仍算命中（tension_ids 可為空清單）。"""
    decision = _call(unresolved_tensions=[{"check_after_ts": _iso(NOW - 1)}])
    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.seed_hint["tension_ids"] == []


def test_t3_tension_id_falls_back_to_id_field():
    """T3b：`tension_id` 缺失時回退讀 `id`。"""
    decision = _call(unresolved_tensions=[{"id": "tn-fallback", "due_at": _iso(NOW - 1)}])
    assert decision.should_wake is True
    assert decision.seed_hint["tension_ids"] == ["tn-fallback"]


# ══════════════════════════════════════════════════════════════
# T4 世界事實撞擊（world collision）
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("source", sorted(QUALIFYING_WORLD_SOURCES))
def test_t4_qualifying_source_wakes(source):
    """T4：白名單來源 + accepted + 新鮮時間戳 + 非空 summary ⇒ WAKE / world_collision。"""
    record = _world_record(source=source, extra={"summary": "外頭颳起大風"})
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.reason == REASON_WORLD_COLLISION_WAKE
    assert decision.origin_type == "world_collision"
    assert "外頭颳起大風" in decision.seed_hint["facts"]


def test_t4_collision_wins_over_reflection_slot():
    """T4：世界碰撞壓過留白（含自省槽點）。"""
    decision = _call(timeslot="night", recent_perceptions=list(_COLLISION))
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.origin_type == "world_collision"


_NEGATIVE_WORLD_RECORDS = [
    ("source_synthetic", _world_record(source="synthetic")),
    ("source_unknown", _world_record(source="world_fact")),
    ("source_none", _world_record(source=None)),
    ("source_number", _world_record(source=42)),
    ("source_uppercase", _world_record(source="Weather")),
    ("accepted_false", _world_record(accepted=False)),
    ("accepted_string_true", _world_record(accepted="true")),
    ("accepted_missing", _world_record(accepted=_DROP)),
    ("accepted_int_one", _world_record(accepted=1)),
    ("extra_missing", _world_record(extra=_DROP)),
    ("extra_not_mapping", _world_record(extra="summary=外頭下雨")),
    ("summary_missing", _world_record(extra={})),
    ("summary_empty", _world_record(extra={"summary": ""})),
    ("summary_whitespace", _world_record(extra={"summary": " \t  "})),
    ("summary_not_string", _world_record(extra={"summary": 42})),
    ("consumed_by_wake_true", _world_record(consumed_by_wake=True)),
    ("timestamp_stale", _world_record(timestamp=_iso(NOW - WORLD_COLLISION_WINDOW_HOURS * 3600 - 1))),
    ("timestamp_missing", _world_record(timestamp=_DROP)),
    ("timestamp_none", _world_record(timestamp=None)),
    ("timestamp_unparseable", _world_record(timestamp="not-a-timestamp")),
    ("timestamp_empty", _world_record(timestamp="")),
    ("timestamp_bool", _world_record(timestamp=True)),
    ("event_type_only_no_summary",
     _world_record(extra={"event_type": "rain", "novelty_id": "nv-1", "reason": "大雨"})),
    ("top_level_summary_not_in_extra",
     _world_record(extra={}, summary="外頭颳起大風")),
]


@pytest.mark.parametrize("name,record", _NEGATIVE_WORLD_RECORDS,
                         ids=[name for name, _ in _NEGATIVE_WORLD_RECORDS])
def test_t4_non_qualifying_record_never_fabricates_wake(name, record):
    """T4 反向：任一必要條件不成立 ⇒ 不捏造碰撞（SLEEP / DAYTIME_WHITESPACE）。"""
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is False, name
    assert decision.reason == "DAYTIME_WHITESPACE", name
    assert decision.origin_type is None, name
    assert decision.seed_hint is None, name


@pytest.mark.parametrize("record", ["plain-string", 42, None, ["nested"], ("tuple",)],
                         ids=["str", "int", "none", "list", "tuple"])
def test_t4_non_mapping_perception_record_is_skipped(record):
    """T4 反向：記錄不是 mapping ⇒ 跳過該筆。"""
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t4_future_timestamp_counts_as_in_window():
    """T4：未來時間戳視為在窗內 ⇒ 算命中。"""
    record = _world_record(timestamp=_iso(NOW + 6 * 3600))
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"


def test_t4_window_floor_is_inclusive():
    """T4：`timestamp == current_time - 4h` 仍在窗內（>= 為界）。"""
    record = _world_record(timestamp=_iso(NOW - WORLD_COLLISION_WINDOW_HOURS * 3600))
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"


def test_t4_one_second_past_window_is_ignored():
    """T4：超出窗 1 秒 ⇒ 不命中。"""
    record = _world_record(timestamp=_iso(NOW - WORLD_COLLISION_WINDOW_HOURS * 3600 - 1))
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


@pytest.mark.parametrize("stamp", ["epoch_number", "datetime_object"])
def test_t4_timestamp_accepts_epoch_and_datetime(stamp):
    """T4：時間戳接受 epoch 數值與 datetime 物件。"""
    if stamp == "epoch_number":
        record = _world_record(timestamp=int(NOW) - 60)
    else:
        record = _world_record(timestamp=datetime.fromtimestamp(NOW - 60, tz=timezone.utc))
    decision = _call(recent_perceptions=[record])
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"


def test_t4_facts_capped_at_five():
    """T4：`seed_hint["facts"]` 上限 5 筆。"""
    records = [_world_record(extra={"summary": f"事實{i}"}) for i in range(7)]
    decision = _call(recent_perceptions=records)
    assert decision.should_wake is True
    assert SEED_HINT_MAX_ITEMS == 5
    assert len(decision.seed_hint["facts"]) == 5
    assert decision.seed_hint["facts"][0] == "事實0"


def test_t4_summary_truncated_to_200_chars():
    """T4：單筆 summary 截斷至 200 字。"""
    long_summary = "嗡" * (SEED_HINT_MAX_TEXT_CHARS + 100)
    decision = _call(recent_perceptions=[_world_record(extra={"summary": long_summary})])
    assert SEED_HINT_MAX_TEXT_CHARS == 200
    assert decision.seed_hint["facts"][0] == "嗡" * SEED_HINT_MAX_TEXT_CHARS


def test_t4_summary_is_stripped():
    """T4：summary 去空白後才進 facts。"""
    decision = _call(recent_perceptions=[
        _world_record(extra={"summary": "  外頭下起大雨  "})
    ])
    assert decision.seed_hint["facts"] == ["外頭下起大雨"]


def test_t4_event_ids_kept_only_when_non_empty_strings():
    """T4：event_ids 只保留非空字串，長度同步受 5 筆上限。"""
    records = [
        _world_record(event_id="evt-1", extra={"summary": "一"}),
        _world_record(event_id="", extra={"summary": "二"}),
        _world_record(event_id=7, extra={"summary": "三"}),
        _world_record(extra={"summary": "四"}),
    ]
    decision = _call(recent_perceptions=records)
    assert decision.seed_hint["event_ids"] == ["evt-1"]
    assert decision.seed_hint["facts"] == ["一", "二", "三", "四"]
    assert decision.seed_hint["source"] == "perception_trace"
    assert decision.seed_hint["origin_type"] == "world_collision"


def test_t4_consumed_record_ignored_while_fresh_record_still_wakes():
    """T4：已消費的舊碰撞被跳過，但不影響同批新鮮碰撞照常命中。"""
    records = [
        _world_record(consumed_by_wake=True, extra={"summary": "舊的"}),
        _world_record(extra={"summary": "新的"}),
    ]
    decision = _call(recent_perceptions=records)
    assert decision.should_wake is True
    assert decision.seed_hint["facts"] == ["新的"]


def test_t4_collision_precedes_due_thread_and_tension():
    """T4：優先序 —— 世界碰撞排在到期線頭／到期張力之前。"""
    decision = _call(
        active_threads=list(_DUE_THREAD),
        unresolved_tensions=list(_DUE_TENSION),
        recent_perceptions=list(_COLLISION),
    )
    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.origin_type == "world_collision"


def test_t4_due_thread_precedes_due_tension():
    """T4：優先序 —— 線頭到期排在張力到期之前。"""
    decision = _call(active_threads=list(_DUE_THREAD), unresolved_tensions=list(_DUE_TENSION))
    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"


# ══════════════════════════════════════════════════════════════
# T5 Fail-Closed（一律 SLEEP / FAIL_CLOSED_DEFAULT_SLEEP、永不 raise）
# ══════════════════════════════════════════════════════════════

_FAIL_CLOSED_CASES = [
    ("agent_id_none", {"agent_id": None}),
    ("agent_id_empty", {"agent_id": ""}),
    ("agent_id_whitespace", {"agent_id": "   "}),
    ("agent_id_newline_only", {"agent_id": "\n"}),
    ("agent_id_int", {"agent_id": 123}),
    ("agent_id_list", {"agent_id": ["agent_ruka"]}),
    ("agent_id_dict", {"agent_id": {"id": "agent_ruka"}}),
    ("current_time_none", {"current_time": None}),
    ("current_time_string", {"current_time": "1800000000"}),
    ("current_time_iso_string", {"current_time": _iso(NOW)}),
    ("current_time_bool_true", {"current_time": True}),
    ("current_time_bool_false", {"current_time": False}),
    ("current_time_nan", {"current_time": float("nan")}),
    ("current_time_inf", {"current_time": float("inf")}),
    ("current_time_negative_inf", {"current_time": float("-inf")}),
    ("current_time_none_type", {"current_time": []}),
    ("timeslot_noon", {"timeslot": "noon"}),
    ("timeslot_uppercase", {"timeslot": "MORNING"}),
    ("timeslot_none", {"timeslot": None}),
    ("timeslot_empty", {"timeslot": ""}),
    ("timeslot_int", {"timeslot": 7}),
    ("timeslot_trailing_space", {"timeslot": "morning "}),
    ("capacity_limit_zero", {"capacity_limit": 0}),
    ("capacity_limit_negative", {"capacity_limit": -1}),
    ("capacity_limit_string", {"capacity_limit": "3"}),
    ("capacity_limit_bool_true", {"capacity_limit": True}),
    ("capacity_limit_float", {"capacity_limit": 2.0}),
    ("capacity_limit_none", {"capacity_limit": None}),
    ("active_threads_none", {"active_threads": None}),
    ("active_threads_dict", {"active_threads": {"a": 1}}),
    ("active_threads_string", {"active_threads": "threads"}),
    ("active_threads_tuple", {"active_threads": ()}),
    ("active_threads_set", {"active_threads": set()}),
]


@pytest.mark.parametrize("name,overrides", _FAIL_CLOSED_CASES,
                         ids=[name for name, _ in _FAIL_CLOSED_CASES])
def test_t5_fail_closed_never_raises_and_sleeps(name, overrides):
    """T5：任一非法輸入 ⇒ SLEEP / FAIL_CLOSED_DEFAULT_SLEEP，且**呼叫不拋例外**。"""
    params = {
        "agent_id": AGENT,
        "current_time": NOW,
        "timeslot": "daytime",
        "active_threads": [],
        "capacity_limit": CAP,
        "recent_perceptions": None,
        "unresolved_tensions": None,
    }
    params.update(overrides)
    try:
        decision = evaluate_wake_gate(**params)
    except Exception as exc:  # pragma: no cover - 只有回歸時才會到此
        raise AssertionError(f"{name}: evaluate_wake_gate raised {exc!r}") from exc
    assert decision.should_wake is False, name
    assert decision.reason == "FAIL_CLOSED_DEFAULT_SLEEP", name
    assert decision.reason == REASON_FAIL_CLOSED_DEFAULT_SLEEP, name
    assert decision.origin_type is None, name
    assert decision.seed_hint is None, name


def test_t5_fail_closed_ignores_disturbances():
    """T5：即使同時有碰撞與到期線頭，非法輸入仍 fail-closed。"""
    decision = _call(agent_id="", active_threads=list(_DUE_THREAD),
                     recent_perceptions=list(_COLLISION))
    assert decision.should_wake is False
    assert decision.reason == "FAIL_CLOSED_DEFAULT_SLEEP"


@pytest.mark.parametrize("bad_optional", [None, {}, "x", 0, (), {"k": "v"}],
                         ids=["none", "dict", "str", "int", "tuple", "dict2"])
def test_t5_optional_inputs_are_not_fail_closed(bad_optional):
    """T5：`recent_perceptions` / `unresolved_tensions` 非 list ⇒ 視為 []，**不** fail-closed。"""
    decision = _call(recent_perceptions=bad_optional, unresolved_tensions=bad_optional)
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t5_default_optional_arguments_are_none_safe():
    """T5：省略兩個可選參數（預設 None）不 fail-closed。"""
    decision = evaluate_wake_gate(AGENT, NOW, "daytime", [], CAP)
    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


# ══════════════════════════════════════════════════════════════
# T6 No-Scoring 靜態檢查（ast）
# ══════════════════════════════════════════════════════════════

_FORBIDDEN_IDENT = re.compile(r"score|weight|confidence|probab", re.IGNORECASE)

_STDLIB_WHITELIST = frozenset({
    "__future__",
    "logging",
    "math",
    "collections",
    "dataclasses",
    "datetime",
    "typing",
})


@pytest.fixture(scope="module")
def gate_source() -> str:
    return MODULE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def gate_tree(gate_source):
    return ast.parse(gate_source)


def test_t6_parse_target_anchor(gate_tree):
    """T6 前置：確認 ast 解析到的確實是受測模組（公開介面存在）。"""
    funcs = {n.name for n in gate_tree.body if isinstance(n, ast.FunctionDef)}
    assert "evaluate_wake_gate" in funcs
    classes = {n.name for n in gate_tree.body if isinstance(n, ast.ClassDef)}
    assert "WakeDecision" in classes


def test_t6_zero_float_literals(gate_tree):
    """T6①：0 個浮點字面量（`ast.Constant` 值為 float）。"""
    floats = [
        node for node in ast.walk(gate_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, float)
    ]
    assert floats == []


def test_t6_zero_forbidden_identifiers(gate_tree):
    """T6②：0 個禁用識別字（score/weight/confidence/probab，不分大小寫）。"""
    hits = []
    for node in ast.walk(gate_tree):
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, ast.arg):
            name = node.arg
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = node.name
        else:
            continue
        if _FORBIDDEN_IDENT.search(name):
            hits.append((name, getattr(node, "lineno", -1)))
    assert hits == []


def test_t6_imports_are_stdlib_whitelisted_only(gate_tree):
    """T6③：import 的頂層模組全在標準庫白名單內、且無任何 `src.` 前綴。"""
    tops = set()
    for node in ast.walk(gate_tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                tops.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, f"不得使用相對 import：level={node.level}"
            module = node.module or ""
            tops.add(module.split(".")[0])
    assert tops, "預期至少有一個 import"
    assert tops <= _STDLIB_WHITELIST, f"非白名單 import：{sorted(tops - _STDLIB_WHITELIST)}"
    assert all(not top.startswith("src") for top in tops), f"出現 src.* import：{sorted(tops)}"


def test_t6_zero_open_calls(gate_tree):
    """T6（No-Scoring 鐵律）：0 個 `open(` 呼叫 —— 模組不得自行讀寫任何檔案。"""
    calls = [
        node for node in ast.walk(gate_tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    ]
    assert calls == []


# ══════════════════════════════════════════════════════════════
# T7 孤立性（未接線）：生產路徑 0 命中
# ══════════════════════════════════════════════════════════════

_SCAN_SUFFIXES = frozenset({
    ".py", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".md", ".txt",
    ".cfg", ".ini", ".ps1", ".js", ".ts", ".bat", ".cmd",
})


def _scan_hits(base: Path) -> list[str]:
    """純 Python 文字掃描（不用 shell grep），排除受測模組自身。"""
    hits: list[str] = []
    if not base.exists():
        return hits
    self_path = MODULE_PATH.resolve()
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if path.resolve() == self_path:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if MODULE_QUALNAME in text:
            hits.append(str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))
    return sorted(hits)


@pytest.mark.parametrize("subdir", ["src", "scripts", "configs", "clients"])
def test_t7_no_production_reference(subdir):
    """T7：`src/**`（排除自身）／`scripts/**`／`configs/**`／`clients/**` 0 命中。"""
    assert _scan_hits(_REPO_ROOT / subdir) == []


def test_t7_module_itself_exists_and_is_scanned_target():
    """T7 前置：受測模組本體存在（掃描才有意義）。"""
    assert MODULE_PATH.is_file()
    assert MODULE_QUALNAME in MODULE_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("relative", ["src/soul/scheduler.py", "src/llm/proxy.py"])
def test_t7_key_files_zero_hits(relative):
    """T7：排程器與 LLM proxy 各 0 命中（未接線鐵證）。"""
    path = _REPO_ROOT / relative
    assert path.is_file(), relative
    text = path.read_text(encoding="utf-8", errors="ignore")
    assert text.count(MODULE_QUALNAME) == 0, relative


# ══════════════════════════════════════════════════════════════
# T8 決策物件（WakeDecision）
# ══════════════════════════════════════════════════════════════

_WAKE_CASE_NAMES = (
    "world_collision",
    "checkpoint_due_iso",
    "checkpoint_due_epoch",
    "checkpoint_due_datetime",
    "tension_due",
)

_SLEEP_CASE_NAMES = ("reflection_clear", "daytime_whitespace", "saturated", "fail_closed")


def _decision_for(name: str) -> WakeDecision:
    if name == "world_collision":
        return _call(timeslot="daytime", recent_perceptions=list(_COLLISION))
    if name == "checkpoint_due_iso":
        return _call(timeslot="morning",
                     active_threads=[_thread("th-due", check_after_ts=_iso(NOW - 30),
                                             origin_type="goal_driven")])
    if name == "checkpoint_due_epoch":
        return _call(timeslot="morning",
                     active_threads=[_thread("th-due", check_after_ts=int(NOW) - 30,
                                             origin_type="goal_driven")])
    if name == "checkpoint_due_datetime":
        return _call(timeslot="morning",
                     active_threads=[_thread(
                         "th-due",
                         check_after_ts=datetime.fromtimestamp(NOW - 30, tz=timezone.utc),
                         origin_type="goal_driven")])
    if name == "tension_due":
        return _call(timeslot="night", unresolved_tensions=list(_DUE_TENSION))
    if name == "reflection_clear":
        return _call(timeslot="morning")
    if name == "daytime_whitespace":
        return _call(timeslot="daytime")
    if name == "saturated":
        return _call(timeslot="daytime", active_threads=[_thread("th-1")], capacity_limit=1)
    if name == "fail_closed":
        return _call(agent_id=None)
    raise AssertionError(f"unknown case: {name}")  # pragma: no cover


def test_t8_is_frozen_dataclass():
    """T8：`WakeDecision` 為 frozen dataclass，賦值即 raise FrozenInstanceError。"""
    assert is_dataclass(WakeDecision)
    decision = _decision_for("daytime_whitespace")
    with pytest.raises(FrozenInstanceError):
        decision.should_wake = True  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        decision.reason = "X"  # type: ignore[misc]


def test_t8_field_set_is_exact():
    """T8：四個欄位齊備且順序固定。"""
    assert [f.name for f in fields(WakeDecision)] == [
        "should_wake", "origin_type", "reason", "seed_hint",
    ]


def test_t8_should_wake_is_strict_bool():
    """T8：`should_wake` 只有 True / False 兩態（無第三態）。"""
    for name in _WAKE_CASE_NAMES + _SLEEP_CASE_NAMES:
        assert _decision_for(name).should_wake in (True, False)


@pytest.mark.parametrize("name", _WAKE_CASE_NAMES + _SLEEP_CASE_NAMES)
def test_t8_origin_type_is_legal_or_none(name):
    """T8：`origin_type` 只會是 4 個合法值之一或 None。"""
    origin = _decision_for(name).origin_type
    assert origin is None or origin in ORIGIN_TYPES


_SERIALIZABLE_WAKE_CASE_NAMES = (
    "world_collision",
    "checkpoint_due_iso",
    "checkpoint_due_epoch",
    "tension_due",
)
# 註：`checkpoint_due_datetime` 不列入本矩陣 —— `datetime` 物件**不是** M1 JSONL
# 的真實形狀（JSONL 讀出必為 ISO 字串），故不屬於工單 T8 指定的「M1 真實形狀」範圍。


@pytest.mark.parametrize("name", _SERIALIZABLE_WAKE_CASE_NAMES)
def test_t8_wake_seed_hint_is_json_serializable(name):
    """T8：**所有 WAKE 路徑**的 seed_hint 都能 `json.dumps`（M1 真實形狀）。"""
    decision = _decision_for(name)
    assert decision.should_wake is True, name
    dumped = json.dumps(decision.seed_hint, ensure_ascii=False)
    assert isinstance(dumped, str)
    assert json.loads(dumped) == decision.seed_hint


@pytest.mark.parametrize("name", _SLEEP_CASE_NAMES)
def test_t8_sleep_seed_hint_and_origin_are_none(name):
    """T8：SLEEP 一律 origin_type is None 且 seed_hint is None。"""
    decision = _decision_for(name)
    assert decision.should_wake is False, name
    assert decision.origin_type is None, name
    assert decision.seed_hint is None, name


def test_t8_origin_types_value_set_pinned():
    """T8：origin 值域與張力／碰撞的固定 origin 釘死。"""
    assert set(ORIGIN_TYPES) == {
        "goal_driven", "necessity_driven", "whim_driven", "world_collision",
    }
    assert _decision_for("world_collision").origin_type == "world_collision"
    assert _decision_for("tension_due").origin_type == "necessity_driven"


def test_t8_seed_hint_for_iso_check_after_ts_shape():
    """T8：M1 真實形狀（ISO 字串 `check_after_ts`）原樣放進 seed_hint 且可序列化。"""
    decision = _decision_for("checkpoint_due_iso")
    assert decision.seed_hint["due_thread_ids"] == ["th-due"]
    assert decision.seed_hint["due_check_after_ts"] == _iso(NOW - 30)
    json.dumps(decision.seed_hint)


# ══════════════════════════════════════════════════════════════
# T9 可觀測性（恰好一行 INFO、≤200 字元、不含換行）
# ══════════════════════════════════════════════════════════════

_LOG_LINE_RE = re.compile(
    r"^\[LifeThreadGate\] agent=.* slot=\S+ decision=(WAKE|SLEEP)"
    r" reason=[A-Z_]+(?: origin=\w+)?$"
)

_OBSERVABILITY_CASES = (
    ("world_collision", {"recent_perceptions": list(_COLLISION)}),
    ("checkpoint_due", {"timeslot": "morning", "active_threads": list(_DUE_THREAD)}),
    ("tension_due", {"timeslot": "night", "unresolved_tensions": list(_DUE_TENSION)}),
    ("saturated", {"active_threads": [_thread("th-1")], "capacity_limit": 1}),
    ("reflection_clear", {"timeslot": "morning"}),
    ("daytime_whitespace", {}),
    ("fail_closed", {"agent_id": ""}),
)


@pytest.mark.parametrize("name,kwargs", _OBSERVABILITY_CASES,
                         ids=[name for name, _ in _OBSERVABILITY_CASES])
def test_t9_exactly_one_info_line_per_call(gate_logs, name, kwargs):
    """T9：每次呼叫恰好一行 INFO，≤200 字元、不含換行、格式相符。"""
    gate_logs.clear()
    _call(**kwargs)
    lines = _gate_lines(gate_logs)
    assert len(lines) == 1, f"{name}: {lines}"
    line = lines[0]
    assert "\n" not in line and "\r" not in line, name
    assert len(line) <= LOG_MAX_CHARS, f"{name}: {len(line)}"
    assert LOG_MAX_CHARS == 200
    assert _LOG_LINE_RE.match(line), f"{name}: {line}"


@pytest.mark.parametrize("name,kwargs", _OBSERVABILITY_CASES,
                         ids=[name for name, _ in _OBSERVABILITY_CASES])
def test_t9_wake_lines_carry_origin_sleep_lines_do_not(gate_logs, name, kwargs):
    """T9：WAKE 行含 `origin=`；SLEEP 行不含（origin_type is None）。"""
    gate_logs.clear()
    decision = _call(**kwargs)
    line = _gate_lines(gate_logs)[0]
    if decision.should_wake:
        assert " origin=" in line, name
        assert f"origin={decision.origin_type}" in line, name
    else:
        assert " origin=" not in line, name


def test_t9_fail_closed_line_ends_with_reason(gate_logs):
    """T9：fail-closed 行以 `FAIL_CLOSED_DEFAULT_SLEEP` 收尾。"""
    gate_logs.clear()
    _call(timeslot="noon")
    line = _gate_lines(gate_logs)[0]
    assert line.endswith("reason=FAIL_CLOSED_DEFAULT_SLEEP")
    assert line == (
        "[LifeThreadGate] agent=agent_ruka slot=noon decision=SLEEP"
        " reason=FAIL_CLOSED_DEFAULT_SLEEP"
    )


def test_t9_wake_line_exact_shape(gate_logs):
    """T9：WAKE 行的逐字形狀（decision=WAKE 且尾接 origin）。"""
    gate_logs.clear()
    _call(timeslot="morning", active_threads=list(_DUE_THREAD))
    line = _gate_lines(gate_logs)[0]
    assert line == (
        "[LifeThreadGate] agent=agent_ruka slot=morning decision=WAKE"
        " reason=CHECKPOINT_DUE_WAKE origin=goal_driven"
    )


def test_t9_very_long_agent_id_still_within_limit(gate_logs):
    """T9：5000 字元 agent_id 仍 ≤200 且單行。"""
    gate_logs.clear()
    _call(agent_id="a" * 5000)
    lines = _gate_lines(gate_logs)
    assert len(lines) == 1
    assert len(lines[0]) <= LOG_MAX_CHARS
    assert "\n" not in lines[0]


def test_t9_very_long_agent_id_on_wake_path_still_within_limit(gate_logs):
    """T9：WAKE 路徑（含 origin 尾綴）配超長 agent_id 仍 ≤200。"""
    gate_logs.clear()
    _call(agent_id="b" * 5000, timeslot="night", unresolved_tensions=list(_DUE_TENSION))
    lines = _gate_lines(gate_logs)
    assert len(lines) == 1
    assert len(lines[0]) <= LOG_MAX_CHARS
    assert " origin=necessity_driven" in lines[0]


@pytest.mark.parametrize("bad_agent", ["agent\nX", "agent\r\nX", "a\nb\nc", "\t" * 3 + "x"],
                         ids=["lf", "crlf", "multi", "tabs"])
def test_t9_newlines_never_leak_into_log_line(gate_logs, bad_agent):
    """T9：agent_id 內含換行／回車 ⇒ 觀測行仍為單行。"""
    gate_logs.clear()
    _call(agent_id=bad_agent)
    lines = _gate_lines(gate_logs)
    assert len(lines) == 1
    assert "\n" not in lines[0] and "\r" not in lines[0]
    assert len(lines[0]) <= LOG_MAX_CHARS


def test_t9_repeated_calls_emit_one_line_each(gate_logs):
    """T9：連續 5 次呼叫 ⇒ 恰好 5 行（無漏記、無重複）。"""
    gate_logs.clear()
    for _ in range(5):
        _call(timeslot="morning")
    assert len(_gate_lines(gate_logs)) == 5


def test_t9_logger_name_is_module_scoped():
    """T9：觀測 logger 名稱即模組 logger（可被外部 handler 精準掛載）。"""
    logger = logging.getLogger(LOGGER_NAME)
    assert logger.name == LOGGER_NAME
    assert logger.name.startswith("soul_os.")


# ══════════════════════════════════════════════════════════════
# T10 決定性（無隨機、無狀態殘留）
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("name", _WAKE_CASE_NAMES + _SLEEP_CASE_NAMES)
def test_t10_three_calls_are_identical(name):
    """T10：同一輸入連續呼叫 3 次，三者完全相等。"""
    first = _decision_for(name)
    second = _decision_for(name)
    third = _decision_for(name)
    assert first == second == third
    assert (first.should_wake, first.origin_type, first.reason, first.seed_hint) == (
        second.should_wake, second.origin_type, second.reason, second.seed_hint,
    )


def test_t10_repeated_calls_do_not_mutate_inputs():
    """T10：閘門不會改動呼叫端傳入的 list / dict（無狀態殘留）。"""
    threads = [
        _thread("th-late", check_after_ts=_iso(NOW - 10), origin_type="whim_driven"),
        _thread("th-early", check_after_ts=_iso(NOW - 3000), origin_type="necessity_driven"),
    ]
    perceptions = [_world_record(event_id="evt-1")]
    tensions = [_tension("tn-1")]
    snapshot = json.dumps([threads, perceptions, tensions], sort_keys=True, ensure_ascii=False)
    for _ in range(3):
        _call(active_threads=threads, recent_perceptions=perceptions,
              unresolved_tensions=tensions)
    assert json.dumps([threads, perceptions, tensions], sort_keys=True,
                      ensure_ascii=False) == snapshot


def test_t10_same_input_same_answer_across_different_caller_prefixes():
    """T10：前一次呼叫的結果不影響下一次（無殘留狀態）。"""
    wake_first = _call(timeslot="night", unresolved_tensions=list(_DUE_TENSION))
    sleep_after = _call(timeslot="night")
    wake_again = _call(timeslot="night", unresolved_tensions=list(_DUE_TENSION))
    assert wake_first == wake_again
    assert wake_first.should_wake is True
    assert sleep_after.should_wake is False
    assert sleep_after.reason == "REFLECTION_SLOT_CLEAR"
