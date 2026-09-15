# tests/soul/test_life_thread_wake_gate_m3.py
# LIFE-THREAD-M3-1 — 二元存在性喚醒閘門（Salience Gate）測試矩陣 T1~T10。
# LIFE-THREAD-M3-2 — 追加 T11~T16（語意對齊：容量政策旋鈕／due-based 張力／`consumed_by_wake`
#                    ／No-Scoring 靜態檢查／偏離宣告釘死）。
#
# 受測模組（唯讀）：`src/soul/life_thread_wake_gate.py`
# 模組為 pure function（0 I/O / 0 LLM / 0 定時器 / 0 `src.*` import），
# 因此本檔**不讀寫 `data/**`**、不啟停任何服務、不動 `src/**`。
#
# 慣例對齊 `tests/soul/test_life_threads_m1.py`：`tests/soul/` 無 `__init__.py`，
# 本檔自行 `sys.path.insert(0, REPO_ROOT)`。
from __future__ import annotations

import ast
import inspect
import json
import logging
import re
import sys
from dataclasses import FrozenInstanceError, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.soul.life_thread_wake_gate import (  # noqa: E402
    ACTIVE_THREAD_STATUS,
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
    _emit_log,
    _json_safe_scalar,
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


def test_t1_dormant_mapping_does_not_count_toward_capacity():
    """T1：容量只數 `status == "active"` 的 mapping；dormant **不**佔位（契約 §2.6.3）。

    語意更新（LIFE-THREAD-M3-1 審計後修正）：舊版把「mapping 元素個數」當容量，
    現行口徑為「是 Mapping **且** `str(status).strip() == "active"`」。
    故 1 筆 dormant ＋ `cap=2`（甚至 `cap=1`）皆**不**飽和，閘門落到留白語意。
    """
    dormant = [_thread("th-1", status="dormant")]

    daytime = _call(active_threads=dormant, capacity_limit=2)
    assert daytime.should_wake is False
    assert daytime.reason == "DAYTIME_WHITESPACE"
    assert daytime.reason == REASON_DAYTIME_WHITESPACE
    assert daytime.origin_type is None
    assert daytime.seed_hint is None

    night = _call(timeslot="night", active_threads=dormant, capacity_limit=2)
    assert night.should_wake is False
    assert night.reason == "REFLECTION_SLOT_CLEAR"
    assert night.reason == REASON_REFLECTION_SLOT_CLEAR
    assert night.origin_type is None
    assert night.seed_hint is None

    # 更嚴：即使 `cap=1`，1 筆 dormant 也不足以觸發容量防線
    tight = _call(active_threads=dormant, capacity_limit=1)
    assert tight.should_wake is False
    assert tight.reason == "DAYTIME_WHITESPACE"
    assert tight.reason != "ACTIVE_POOL_SATURATED"


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


#: LIFE-THREAD-M5 接線後：M3 的**生產 importer 白名單恰為** orchestrator 一個。
#: 不變量未被放寬 —— 由「0 命中」升級成「**恰好** 1 個、且只能是它」，
#: 並以 AST 掃描 import 語句（註解／字串不算），比原始文字比對更嚴。
_M3_IMPORTER_WHITELIST = ("src/soul/life_thread_orchestrator.py",)

#: M3 的真正消費點必須**掛在 scheduler 的 slot 觸發窗**上（比對掛載函式名）。
_M3_SCHEDULER_MOUNT = "_fire_life_thread_slot"


def _imports_m3(tree: ast.AST) -> bool:
    """AST：該模組是否**真正 import** 了 `life_thread_wake_gate`。

    只認 `ast.Import` / `ast.ImportFrom` 的模組名與 alias 名 ——
    一行註解或字串提及**不算**。三種寫法都要 DETECT：

    - `import src.soul.life_thread_wake_gate`
    - `from src.soul import life_thread_wake_gate`
    - `from src.soul.life_thread_wake_gate import evaluate_wake_gate`
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == MODULE_QUALNAME or alias.name.endswith(
                    "." + MODULE_QUALNAME
                ):
                    return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == MODULE_QUALNAME or mod.endswith("." + MODULE_QUALNAME):
                return True
            for alias in node.names:
                if alias.name == MODULE_QUALNAME:
                    return True
    return False


def _scan_importers_in(root: Path) -> list[str]:
    """在**任意**目錄樹內掃「真正 import M3」的 `.py`（可餵 `tmp_path` 做牙齒證明）。"""
    hits: list[str] = []
    if not root.exists():
        return hits
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _imports_m3(tree):
            hits.append(str(path.relative_to(root)).replace("\\", "/"))
    return hits


def _m3_production_importers() -> list[str]:
    """`src/**` ＋ `scripts/**` 內**真正 import** M3 的檔案（相對路徑、排序）。"""
    hits: list[str] = []
    for base in ("src", "scripts"):
        hits += [
            f"{base}/{rel}" for rel in _scan_importers_in(_REPO_ROOT / base)
        ]
    return sorted(hits)


def test_t7_m3_importer_whitelist_is_exactly_orchestrator():
    """T7（M5 改寫）：M3 的生產 importer **白名單恰為** orchestrator。

    不變量強度：原「0 命中」在 M5 接線後無法成立（orchestrator 必須 import M3），
    故改寫成**恰好等於白名單**（多一個、少一個都紅），並且只認 AST import 語句 ——
    連「多加一行提到模組名的註解」都不算命中，反之真 import 一定命中。
    """
    assert _m3_production_importers() == list(_M3_IMPORTER_WHITELIST)


def test_t7_m3_importer_scan_has_teeth(tmp_path):
    """T7 牙齒證明（反例構造）：真 import ⇒ DETECT；純註解／字串 ⇒ MISS。

    在臨時目錄造 5 個反例檔，證明「新檢查」不是靠文字比對虛應故事：
    文字比對會被**註解**騙（這裡 `middleware.py:93` 就是活生生的例子）。
    """
    (tmp_path / "real_from.py").write_text(
        f"from src.soul import {MODULE_QUALNAME}\n", encoding="utf-8"
    )
    (tmp_path / "real_import.py").write_text(
        f"import src.soul.{MODULE_QUALNAME}\n", encoding="utf-8"
    )
    (tmp_path / "real_deep.py").write_text(
        f"from src.soul.{MODULE_QUALNAME} import evaluate_wake_gate\n", encoding="utf-8"
    )
    (tmp_path / "only_comment.py").write_text(
        f"# 上限與讀取端 `src/soul/{MODULE_QUALNAME}.py:103` 一致。\nx = 1\n",
        encoding="utf-8",
    )
    (tmp_path / "only_string.py").write_text(
        f'DOC = "src/soul/{MODULE_QUALNAME}.py"\n', encoding="utf-8"
    )

    detected = _scan_importers_in(tmp_path)
    assert detected == ["real_deep.py", "real_from.py", "real_import.py"], detected
    # 註解／字串檔**不得**被計入（否則不變量會被一行註解打穿）
    assert "only_comment.py" not in detected
    assert "only_string.py" not in detected
    # 對照：純文字比對**會**把註解檔誤判為命中 ⇒ 證明改用 AST 是必要的收緊
    comment_text = (tmp_path / "only_comment.py").read_text(encoding="utf-8")
    assert MODULE_QUALNAME in comment_text, "文字比對會假陽性（AST 版本才有牙）"


def test_t7_orchestrator_is_mounted_in_scheduler_exactly_once():
    """T7（M5 新增）：orchestrator 在 `scheduler.py` 內被**掛載恰一次**且落在 slot 窗。

    以「掛載函式名」比對（不用模組名，避免被註解提及誤導）。
    """
    src = (_REPO_ROOT / "src" / "soul" / "scheduler.py").read_text(encoding="utf-8")
    assert src.count(f"self.{_M3_SCHEDULER_MOUNT}(") == 1, "scheduler 掛載點必須恰 1 處"
    assert f"async def {_M3_SCHEDULER_MOUNT}(" in src
    # 掛載點必須在既有 slot 判據上（沿用 _slot_for_time，不新增定時器）
    assert "_slot_for_time" in src
    assert "life_thread_orchestrator" in src, "orchestrator 必須被 lazy import 進 scheduler"


@pytest.mark.parametrize("relative", ["src/llm/proxy.py", "configs/default.yaml"])
def test_t7_key_files_still_zero_m3_import(relative):
    """T7：LLM proxy 與設定檔對 M3 **0 import**（接線不經這兩處）。"""
    path = _REPO_ROOT / relative
    assert path.is_file(), relative
    if path.suffix == ".py":
        assert _imports_m3(ast.parse(path.read_text(encoding="utf-8"))) is False, relative
    else:
        assert MODULE_QUALNAME not in path.read_text(encoding="utf-8", errors="ignore"), (
            relative
        )


def test_t7_module_itself_exists_and_is_scanned_target():
    """T7 前置：受測模組本體存在（掃描才有意義）。"""
    assert MODULE_PATH.is_file()
    assert MODULE_QUALNAME in MODULE_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("subdir", ["configs", "clients"])
def test_t7_non_python_dirs_still_zero_reference(subdir):
    """T7（保留原覆蓋）：`configs/**`／`clients/**` 仍為 **0 命中**（涵蓋非 .py 檔）。

    M5 的接線只允許發生在 `src/soul/life_thread_orchestrator.py`（＋scheduler 掛載），
    設定檔與其他 client 一律不得引用 M3。
    """
    assert _scan_hits(_REPO_ROOT / subdir) == []


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


# ══════════════════════════════════════════════════════════════
# T11 seed_hint JSON-safe 正規化（`_json_safe_scalar`）— LIFE-THREAD-M3-1
# ══════════════════════════════════════════════════════════════
#
# helper 契約（唯讀 `src/soul/life_thread_wake_gate.py`，本檔不改 `src/**`）：
#   str                ⇒ 原樣回傳（**不截斷**，含超長字串）
#   bool               ⇒ `str(value)` 後截斷至 SEED_HINT_MAX_TEXT_CHARS
#   int / float（非 bool）⇒ 原樣回傳
#   datetime / date    ⇒ `.isoformat()`
#   None               ⇒ None
#   其他（list / dict / 自訂物件…）⇒ `str(value)` 後截斷至上限
# 且**永不 raise**（內部 `except Exception` ⇒ None）。
# 生產路徑：`CHECKPOINT_DUE_WAKE` 的 `seed_hint["due_check_after_ts"]` 會經過它。

_TZ_PLUS8 = timezone(timedelta(hours=8))
_DT_NAIVE = datetime(2026, 9, 14, 19, 28, 7, 851959)
_DT_AWARE = datetime(2026, 9, 14, 19, 28, 7, 851959, tzinfo=_TZ_PLUS8)
_DATE_ONLY = date(2026, 9, 14)

_LONG_STR = "長" * 500  # 遠超 SEED_HINT_MAX_TEXT_CHARS，且是 str ⇒ 必須原樣
_LONG_ISO = "2026-09-14T19:28:07+00:00" + " " * 250  # 同為 str ⇒ seed_hint 內不截斷

_DUE_AWARE = datetime.fromtimestamp(NOW - 30, tz=timezone.utc)
_DUE_NAIVE = _DUE_AWARE.replace(tzinfo=None)


class _LongStrObject:
    """自訂物件：`str()` 遠超上限 ⇒ 必須截斷至 SEED_HINT_MAX_TEXT_CHARS。"""

    def __str__(self) -> str:
        return "x" * 500


class _RaisingStrObject:
    """自訂物件：`str()` 直接 raise ⇒ helper 仍**不得**拋例外。"""

    def __str__(self) -> str:
        raise RuntimeError("boom-in-__str__")


_LONG_STR_OBJECT = _LongStrObject()

#: (id, 輸入, 期望回傳值, 期望回傳型別)
_HELPER_CASES = (
    ("str-ascii", "plain-text", "plain-text", str),
    ("str-chinese", "外頭颳起大風，線頭醒了", "外頭颳起大風，線頭醒了", str),
    ("str-empty", "", "", str),
    ("str-500", _LONG_STR, _LONG_STR, str),
    ("bool-true", True, "True", str),
    ("bool-false", False, "False", str),
    ("int", 42, 42, int),
    ("int-negative", -7, -7, int),
    ("float", 3.5, 3.5, float),
    ("float-zero", 0.0, 0.0, float),
    ("datetime-naive", _DT_NAIVE, _DT_NAIVE.isoformat(), str),
    ("datetime-aware", _DT_AWARE, _DT_AWARE.isoformat(), str),
    ("date", _DATE_ONLY, _DATE_ONLY.isoformat(), str),
    ("none", None, None, type(None)),
    ("list", [1, 2], "[1, 2]", str),
    ("dict", {"a": 1}, "{'a': 1}", str),
    ("tuple", (1, 2), "(1, 2)", str),
    ("bytes", b"bytes", "b'bytes'", str),
    ("frozenset-single", frozenset({"a"}), "frozenset({'a'})", str),
    ("object-long-str", _LONG_STR_OBJECT, "x" * SEED_HINT_MAX_TEXT_CHARS, str),
)


@pytest.mark.parametrize(
    "case_id,value,expected,expected_type",
    _HELPER_CASES,
    ids=[case[0] for case in _HELPER_CASES],
)
def test_t11_json_safe_scalar_branch_matrix(case_id, value, expected, expected_type):
    """T11：helper 每個分支的回傳值與型別；結果一律可 `json.dumps`。"""
    result = _json_safe_scalar(value)

    assert result == expected, case_id
    assert type(result) is expected_type, case_id

    # str 分支：原樣、零截斷（長度逐字相等）
    if isinstance(value, str):
        assert len(result) == len(value), case_id

    # 非純量字串化分支：一律 ≤ 上限
    if case_id in ("object-long-str", "bool-true", "bool-false"):
        assert len(result) <= SEED_HINT_MAX_TEXT_CHARS, case_id

    dumped = json.dumps(result, ensure_ascii=False)  # 不拋例外即通過
    assert isinstance(dumped, str), case_id
    assert json.loads(dumped) == result, case_id


def test_t11_json_safe_scalar_str_is_never_truncated_far_above_cap():
    """T11：500 字元 str ⇒ 原樣回傳，`len` 不被截到 200。"""
    assert len(_LONG_STR) == 500
    assert len(_LONG_STR) > SEED_HINT_MAX_TEXT_CHARS

    result = _json_safe_scalar(_LONG_STR)

    assert result == _LONG_STR
    assert len(result) == 500
    assert result[:20] == _LONG_STR[:20]
    assert result[-20:] == _LONG_STR[-20:]
    json.dumps(result, ensure_ascii=False)


def test_t11_json_safe_scalar_bool_is_string_not_int():
    """T11：bool 走字串化分支（`"True"`/`"False"`），不因 `bool ⊂ int` 放行為整數。"""
    result_true = _json_safe_scalar(True)
    result_false = _json_safe_scalar(False)

    assert result_true == "True"
    assert result_false == "False"
    assert result_true != 1 and result_false != 0
    assert type(result_true) is str and type(result_false) is str
    assert json.dumps([result_true, result_false]) == '["True", "False"]'


def test_t11_json_safe_scalar_int_float_are_not_stringified():
    """T11：int / float 原樣回傳（保持數值型別，不被字串化）。"""
    for value in (42, -7, 0, 3.5, 0.0, 1e300):
        result = _json_safe_scalar(value)
        assert result == value
        assert type(result) is type(value)
        json.dumps(result)


def test_t11_json_safe_scalar_datetime_and_date_use_isoformat():
    """T11：datetime（naive／帶 tz）與 date 一律 `.isoformat()`。"""
    for value in (_DT_NAIVE, _DT_AWARE, _DATE_ONLY):
        result = _json_safe_scalar(value)
        assert result == value.isoformat()
        assert type(result) is str
        json.dumps(result)

    assert _json_safe_scalar(_DATE_ONLY) == "2026-09-14"
    assert _json_safe_scalar(_DT_NAIVE).endswith("851959")  # 微秒保留
    assert _json_safe_scalar(_DT_AWARE).endswith("+08:00")  # tz 保留


def test_t11_json_safe_scalar_none_stays_none():
    """T11：`None` ⇒ `None`（不是字串 `"None"`）。"""
    result = _json_safe_scalar(None)

    assert result is None
    assert json.dumps(result) == "null"


@pytest.mark.parametrize("value", [[1, 2], {"a": 1}, (1, 2), [1, [2, [3]]]],
                         ids=["list", "dict", "tuple", "nested-list"])
def test_t11_json_safe_scalar_containers_are_stringified(value):
    """T11：容器一律 `str(value)` 字串化（投影為純量後才可 JSON 化）。"""
    result = _json_safe_scalar(value)

    assert type(result) is str
    assert result == str(value)
    assert len(result) <= SEED_HINT_MAX_TEXT_CHARS
    json.dumps(result)


def test_t11_json_safe_scalar_long_object_string_is_truncated_to_cap():
    """T11：自訂物件 `str()` 逾長 ⇒ 截斷至 200（`len <= 200`）。"""
    result = _json_safe_scalar(_LONG_STR_OBJECT)

    assert result == "x" * 200
    assert len(result) == SEED_HINT_MAX_TEXT_CHARS == 200
    assert len(result) <= 200
    json.dumps(result)


_WEIRD_INPUTS = (
    ("object", object()),
    ("bytes", b"bytes"),
    ("dict", {"a": 1}),
    ("list", [1, 2]),
    ("set", {1, 2}),
    ("complex", complex(1, 2)),
    ("lambda", lambda: None),
    ("raising-str", _RaisingStrObject()),
    ("nan", float("nan")),
    ("inf", float("inf")),
    ("bytes-long", b"y" * 500),
    ("list-long", list(range(500))),
)


@pytest.mark.parametrize("value", [item[1] for item in _WEIRD_INPUTS],
                         ids=[item[0] for item in _WEIRD_INPUTS])
def test_t11_json_safe_scalar_never_raises_on_weird_inputs(value):
    """T11：怪異輸入（含 `__str__` 會 raise 的物件）⇒ 不拋例外且結果可序列化。"""
    try:
        result = _json_safe_scalar(value)
    except BaseException as exc:  # pragma: no cover - 只在本測試失敗時走到
        pytest.fail(f"_json_safe_scalar raised {type(exc).__name__}: {exc}")

    json.dumps(result, ensure_ascii=False)


def test_t11_json_safe_scalar_weird_inputs_exact_values():
    """T11：工單指定怪異輸入的逐字結果。"""
    assert _json_safe_scalar(b"bytes") == "b'bytes'"
    assert _json_safe_scalar({"a": 1}) == "{'a': 1}"

    text = _json_safe_scalar(object())
    assert isinstance(text, str)
    assert text.startswith("<object object at")
    assert text.endswith(">")
    json.dumps(text)


def test_t11_json_safe_scalar_raising_str_returns_none():
    """T11：`__str__` raise ⇒ 吞掉例外回 `None`（fail-soft，不往上拋）。"""
    result = _json_safe_scalar(_RaisingStrObject())

    assert result is None
    json.dumps(result)


# ── 端到端：CHECKPOINT_DUE_WAKE 的 `due_check_after_ts` ────────────

@pytest.mark.parametrize("due_ts", [_DUE_AWARE, _DUE_NAIVE], ids=["aware", "naive"])
def test_t11_e2e_datetime_check_after_ts_wakes_with_isoformat(due_ts):
    """T11：`check_after_ts` 為 `datetime` 且到期 ⇒ WAKE 且 seed_hint 存 `.isoformat()`。"""
    decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-due", check_after_ts=due_ts, origin_type="goal_driven")],
    )

    assert decision.should_wake is True
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE
    assert decision.seed_hint["due_thread_ids"] == ["th-due"]

    dumped = json.dumps(decision.seed_hint, ensure_ascii=False)  # 不拋例外即通過
    assert isinstance(dumped, str)
    assert json.loads(dumped) == decision.seed_hint

    assert decision.seed_hint["due_check_after_ts"] == due_ts.isoformat()


def test_t11_e2e_datetime_seed_hint_value_is_plain_string():
    """T11：`datetime` 不落進 seed_hint（必須先正規化為 ISO 字串）。"""
    decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-due", check_after_ts=_DUE_AWARE, origin_type="goal_driven")],
    )

    value = decision.seed_hint["due_check_after_ts"]
    assert not isinstance(value, datetime)
    assert type(value) is str
    assert value == _DUE_AWARE.isoformat()


@pytest.mark.parametrize(
    "stamp",
    [_iso(NOW - 30), "2026-09-14T19:28:07.851959+08:00", "2026-09-14T19:28:07Z",
     "  2026-09-14T19:28:07+00:00  "],
    ids=["z-suffix", "offset", "second-precision", "padded"],
)
def test_t11_e2e_iso_string_check_after_ts_is_verbatim(stamp):
    """T11：ISO 字串 `check_after_ts` ⇒ seed_hint 內**逐字**等於原字串（不截斷／不改寫）。"""
    decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-due", check_after_ts=stamp, origin_type="goal_driven")],
    )

    assert decision.should_wake is True, stamp
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE, stamp

    value = decision.seed_hint["due_check_after_ts"]
    assert value == stamp
    assert len(value) == len(stamp)
    assert type(value) is str
    json.dumps(decision.seed_hint, ensure_ascii=False)


def test_t11_e2e_iso_string_above_cap_is_not_truncated():
    """T11：ISO 字串長於 200 字元 ⇒ seed_hint 內仍逐字完整（str 分支不截斷）。"""
    assert len(_LONG_ISO) > SEED_HINT_MAX_TEXT_CHARS

    decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-due", check_after_ts=_LONG_ISO, origin_type="goal_driven")],
    )

    assert decision.should_wake is True
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE

    value = decision.seed_hint["due_check_after_ts"]
    assert value == _LONG_ISO
    assert len(value) == len(_LONG_ISO)
    assert len(value) > SEED_HINT_MAX_TEXT_CHARS
    json.dumps(decision.seed_hint, ensure_ascii=False)


def test_t11_e2e_naive_datetime_and_iso_agree_on_same_instant():
    """T11：同一瞬間的 naive datetime 與其 ISO 字串 ⇒ 同一 WAKE 判定與同一 ISO 值。"""
    naive_decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-naive", check_after_ts=_DUE_NAIVE, origin_type="goal_driven")],
    )
    iso_decision = _call(
        timeslot="morning",
        active_threads=[_thread("th-iso", check_after_ts=_DUE_NAIVE.isoformat(),
                                origin_type="goal_driven")],
    )

    assert naive_decision.should_wake is iso_decision.should_wake is True
    assert naive_decision.reason == iso_decision.reason == REASON_CHECKPOINT_DUE_WAKE
    assert (naive_decision.seed_hint["due_check_after_ts"]
            == iso_decision.seed_hint["due_check_after_ts"]
            == _DUE_NAIVE.isoformat())


# ══════════════════════════════════════════════════════════════
# T12 容量計數語意矩陣（契約 §2.6.3：只有 `status == "active"` 佔活躍額度）
# ══════════════════════════════════════════════════════════════
#
# 受測口徑（唯讀 `src/soul/life_thread_wake_gate.py::_pool_saturated`）：
#   `active_count = Σ 1 for t in active_threads
#                   if isinstance(t, Mapping) and str(t.get("status", "")).strip() == "active"`
# 即：`dormant` / 終態（`completed` / `abandoned`）/ `status` 缺欄 / 非字串狀態
# 一律**不計**；非 Mapping 元素一律**不計**；`" active "`（前後空白）**算**。
# 界線仍為 `active_count >= capacity_limit`（`>=`，含等號）。

_PADDED_ACTIVE_STATUS = "  active  "

#: (id, 單一元素, 是否佔 1 個容量額度)
_CAPACITY_ELEMENT_CASES = (
    ("mapping-status-active", _thread("th-1", status="active"), True),
    ("mapping-status-active-padded", _thread("th-1", status=_PADDED_ACTIVE_STATUS), True),
    ("mapping-status-dormant", _thread("th-1", status="dormant"), False),
    ("mapping-status-completed", _thread("th-1", status="completed"), False),
    ("mapping-status-abandoned", _thread("th-1", status="abandoned"), False),
    ("mapping-status-none", _thread("th-1", status=None), False),
    ("mapping-status-int", _thread("th-1", status=123), False),
    ("mapping-status-missing", {"thread_id": "th-1"}, False),
    ("non-mapping-none", None, False),
    ("non-mapping-string", "th-1", False),
    ("non-mapping-list", ["th-1"], False),
)


@pytest.mark.parametrize("case_id,element,occupies", _CAPACITY_ELEMENT_CASES,
                         ids=[case[0] for case in _CAPACITY_ELEMENT_CASES])
def test_t12_capacity_counts_only_active_mappings(case_id, element, occupies):
    """T12：`cap=1` 下，只有「Mapping 且 `status` 去空白後 == `"active"`」觸發飽和。"""
    if case_id == "mapping-status-active-padded":
        # 釘死「去空白才比較」這件事：原始字串**不**等於常數，去空白後才等於
        assert _PADDED_ACTIVE_STATUS != ACTIVE_THREAD_STATUS
        assert _PADDED_ACTIVE_STATUS.strip() == ACTIVE_THREAD_STATUS

    decision = _call(active_threads=[element], capacity_limit=1)

    assert decision.should_wake is False, case_id
    expected = "ACTIVE_POOL_SATURATED" if occupies else "DAYTIME_WHITESPACE"
    assert decision.reason == expected, case_id
    assert decision.origin_type is None, case_id
    assert decision.seed_hint is None, case_id


def test_t12_active_thread_status_literal_is_active():
    """T12：計數口徑的唯一鑰匙 `ACTIVE_THREAD_STATUS` 逐字為 `"active"`。"""
    assert ACTIVE_THREAD_STATUS == "active"
    assert type(ACTIVE_THREAD_STATUS) is str
    assert ACTIVE_THREAD_STATUS.strip() == ACTIVE_THREAD_STATUS
    assert ACTIVE_THREAD_STATUS not in VALID_TIMESLOTS
    assert ACTIVE_THREAD_STATUS not in ORIGIN_TYPES


def test_t12_active_thread_status_is_exported_in_dunder_all():
    """T12：`ACTIVE_THREAD_STATUS` 必須已登錄 `__all__`（公開契約面）。"""
    module = sys.modules[evaluate_wake_gate.__module__]
    assert "ACTIVE_THREAD_STATUS" in module.__all__
    assert module.ACTIVE_THREAD_STATUS == ACTIVE_THREAD_STATUS


#: (id, 3 筆「不佔額度」狀態)
_MIXED_NON_ACTIVE_STATUSES = (
    ("dormant-x3", ["dormant", "dormant", "dormant"]),
    ("terminal-mixed", ["dormant", "completed", "abandoned"]),
)


@pytest.mark.parametrize("case_id,statuses", _MIXED_NON_ACTIVE_STATUSES,
                         ids=[case[0] for case in _MIXED_NON_ACTIVE_STATUSES])
def test_t12_mixed_one_active_plus_three_non_active_is_not_saturated(case_id, statuses):
    """T12：1 active ＋ 3 筆不佔額度、`cap=2` ⇒ **不**飽和（4 筆元素 ≠ 4 筆容量）。

    同時釘死：飽和判定看的是「active 計數」而非「元素總數」。
    """
    threads = [_thread("th-a1", status="active")]
    threads += [_thread(f"th-x{i}", status=status) for i, status in enumerate(statuses)]

    decision = _call(active_threads=threads, capacity_limit=2)

    assert len(threads) == 4, case_id
    assert len(threads) > 2, case_id
    assert decision.should_wake is False, case_id
    assert decision.reason != "ACTIVE_POOL_SATURATED", case_id
    assert decision.reason == "DAYTIME_WHITESPACE", case_id
    assert decision.reason == REASON_DAYTIME_WHITESPACE, case_id


def test_t12_mixed_two_active_plus_three_dormant_is_saturated():
    """T12：2 active ＋ 3 dormant、`cap=2` ⇒ `ACTIVE_POOL_SATURATED`（容量壓過一切擾動）。"""
    threads = [
        _thread("th-a1", status="active"),
        _thread("th-a2", status="active"),
        _thread("th-d1", status="dormant"),
        _thread("th-d2", status="dormant"),
        _thread("th-d3", status="dormant"),
    ]

    decision = _call(
        active_threads=threads,
        capacity_limit=2,
        recent_perceptions=list(_COLLISION),        # 世界碰撞擾動
        unresolved_tensions=list(_DUE_TENSION),     # 到期張力擾動
    )

    assert len(threads) == 5
    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"
    assert decision.reason == REASON_ACTIVE_POOL_SATURATED
    assert decision.origin_type is None
    assert decision.seed_hint is None


def test_t12_one_active_plus_three_dormant_still_lets_due_thread_wake():
    """T12：不飽和 ⇒ 閘門繼續往下判；該唯一 active 線頭到期即 WAKE（不是被容量擋掉）。"""
    threads = [
        _thread("th-due", status="active", check_after_ts=_iso(NOW - 60),
                origin_type="goal_driven"),
        _thread("th-d1", status="dormant"),
        _thread("th-d2", status="dormant"),
        _thread("th-d3", status="dormant"),
    ]

    decision = _call(timeslot="morning", active_threads=threads, capacity_limit=2)

    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE
    assert decision.seed_hint["due_thread_ids"] == ["th-due"]


def test_t12_second_active_flips_same_list_to_saturated():
    """T12：同一份清單再補 1 筆 active（總計 2、`cap=2`）⇒ 立刻翻成飽和。"""
    dormant = [_thread(f"th-d{i}", status="dormant") for i in range(1, 4)]
    due_active = _thread("th-due", status="active", check_after_ts=_iso(NOW - 60),
                         origin_type="goal_driven")

    one_active = _call(timeslot="morning", active_threads=[due_active] + dormant,
                       capacity_limit=2)
    two_active = _call(timeslot="morning",
                       active_threads=[due_active] + dormant + [_thread("th-a2")],
                       capacity_limit=2)

    assert one_active.reason == "CHECKPOINT_DUE_WAKE"
    assert two_active.should_wake is False
    assert two_active.reason == "ACTIVE_POOL_SATURATED"


# ── T12-F1 常數字面值釘死（不得再用常數自身推導位移）───────────────

#: (常數名, 執行期值, 契約明令的字面值)
_CONSTANT_LITERAL_CASES = (
    ("WORLD_COLLISION_WINDOW_HOURS", WORLD_COLLISION_WINDOW_HOURS, 4),
    ("SEED_HINT_MAX_ITEMS", SEED_HINT_MAX_ITEMS, 5),
    ("SEED_HINT_MAX_TEXT_CHARS", SEED_HINT_MAX_TEXT_CHARS, 200),
    ("LOG_MAX_CHARS", LOG_MAX_CHARS, 200),
    ("ACTIVE_THREAD_STATUS", ACTIVE_THREAD_STATUS, "active"),
    ("REFLECTION_SLOTS", REFLECTION_SLOTS, ("morning", "night")),
    ("VALID_TIMESLOTS", VALID_TIMESLOTS, ("morning", "daytime", "evening", "night")),
    ("QUALIFYING_WORLD_SOURCES", QUALIFYING_WORLD_SOURCES,
     frozenset({"weather", "news", "news_event", "calendar", "calendar_event"})),
    ("REASON_ACTIVE_POOL_SATURATED", REASON_ACTIVE_POOL_SATURATED, "ACTIVE_POOL_SATURATED"),
    ("REASON_FAIL_CLOSED_DEFAULT_SLEEP", REASON_FAIL_CLOSED_DEFAULT_SLEEP,
     "FAIL_CLOSED_DEFAULT_SLEEP"),
    ("REASON_WORLD_COLLISION_WAKE", REASON_WORLD_COLLISION_WAKE, "WORLD_COLLISION_WAKE"),
    ("REASON_CHECKPOINT_DUE_WAKE", REASON_CHECKPOINT_DUE_WAKE, "CHECKPOINT_DUE_WAKE"),
    ("REASON_TENSION_DUE_WAKE", REASON_TENSION_DUE_WAKE, "UNRESOLVED_TENSION_DUE_WAKE"),
    ("REASON_REFLECTION_SLOT_CLEAR", REASON_REFLECTION_SLOT_CLEAR, "REFLECTION_SLOT_CLEAR"),
    ("REASON_DAYTIME_WHITESPACE", REASON_DAYTIME_WHITESPACE, "DAYTIME_WHITESPACE"),
)


@pytest.mark.parametrize("name,value,literal", _CONSTANT_LITERAL_CASES,
                         ids=[case[0] for case in _CONSTANT_LITERAL_CASES])
def test_t12_f1_constant_literal_is_pinned(name, value, literal):
    """F1：常數的**字面值**逐字釘死（改值即紅燈；不使用常數自身推導期望值）。"""
    assert value == literal, name
    assert type(value) is type(literal), name
    if isinstance(literal, (str, tuple)):
        assert len(value) == len(literal), name  # 逐元素、含順序與長度
    if isinstance(literal, tuple):
        assert all(value[i] == literal[i] for i in range(len(literal))), name
    if isinstance(literal, frozenset):
        assert value == frozenset({"weather", "news", "news_event", "calendar",
                                  "calendar_event"}), name
        assert "synthetic" not in value, name


def test_t12_f1_collision_window_seconds_are_hardcoded_14400():
    """F1：視窗秒數以**獨立算式** 4*3600 釘死為 14400 秒（3 項皆不得位移）。"""
    assert 4 * 3600 == 14400
    assert WORLD_COLLISION_WINDOW_HOURS * 3600 == 4 * 3600
    assert WORLD_COLLISION_WINDOW_HOURS == 4


@pytest.mark.parametrize("encode", ["epoch_seconds", "iso_string"])
def test_t12_f1_e2e_exactly_four_hours_wakes_and_one_more_second_sleeps(encode):
    """F1 端到端（硬寫死 14400 秒，不引用常數）：恰 4h ⇒ WAKE；4h＋1s ⇒ SLEEP / 留白。"""
    def _record(offset_seconds: int) -> dict:
        stamp = NOW - offset_seconds
        return _world_record(timestamp=stamp if encode == "epoch_seconds" else _iso(stamp))

    at_edge = _call(timeslot="daytime", recent_perceptions=[_record(14400)])
    assert at_edge.should_wake is True, encode
    assert at_edge.reason == "WORLD_COLLISION_WAKE", encode
    assert at_edge.origin_type == "world_collision", encode

    past_edge = _call(timeslot="daytime", recent_perceptions=[_record(14401)])
    assert past_edge.should_wake is False, encode
    assert past_edge.reason == "DAYTIME_WHITESPACE", encode
    assert past_edge.origin_type is None, encode


# ══════════════════════════════════════════════════════════════
# T13-F6 日誌骨架不變量（任何長度下必含 `decision=` 與 `reason=`）
# ══════════════════════════════════════════════════════════════
#
# 契約骨架：`[LifeThreadGate] agent=<...> slot=<...> decision=WAKE|SLEEP reason=<REASON>`
# 不變量：恰好 1 行、≤ `LOG_MAX_CHARS`(200)、不含換行、**必含** `decision=` 與 `reason=`。
# `timeslot` 只能取 4 個合法值 ⇒ 超長 slot 無法經 `evaluate_wake_gate` 構造，
# 故另以 monkeypatch 級手段直接呼叫內部 `_emit_log` 測超長 slot。

_LONG_SLOT = "n" * 5000


def _assert_log_skeleton(lines: list[str], where: str) -> None:
    """骨架斷言共用體（私有 helper，pytest 不收集）。"""
    assert len(lines) == 1, f"{where}: 行數={len(lines)} {lines}"
    line = lines[0]
    assert "\n" not in line and "\r" not in line, f"{where}: 出現換行"
    assert len(line) <= 200, f"{where}: 長度={len(line)}"
    assert len(line) <= LOG_MAX_CHARS, f"{where}: 長度={len(line)}"
    assert "decision=" in line, f"{where}: 缺 decision= ⇒ {line}"
    assert "reason=" in line, f"{where}: 缺 reason= ⇒ {line}"
    assert line.index("decision=") < line.index("reason="), f"{where}: 骨架順序錯誤"
    assert line.startswith("[LifeThreadGate] agent="), f"{where}: 前綴錯誤"


@pytest.mark.parametrize("length", [200, 201, 1000, 5000, 20000])
def test_t13_f6_long_agent_id_keeps_log_skeleton(gate_logs, length):
    """F6：`agent_id` 長度 200/201/1k/5k/20k ⇒ 仍恰好 1 行、≤200、含雙標籤。"""
    gate_logs.clear()
    decision = _call(agent_id="a" * length)

    assert decision.should_wake is False, length
    _assert_log_skeleton(_gate_lines(gate_logs), f"agent_id len={length}")


def test_t13_f6_long_agent_id_on_wake_path_keeps_log_skeleton(gate_logs):
    """F6：WAKE 路徑（含 ` origin=` 尾綴）配 5,000 字元 `agent_id` ⇒ 骨架仍在。"""
    gate_logs.clear()
    _call(agent_id="b" * 5000, timeslot="night", unresolved_tensions=list(_DUE_TENSION))

    lines = _gate_lines(gate_logs)
    _assert_log_skeleton(lines, "wake-path long agent_id")
    assert " origin=necessity_driven" in lines[0]


def test_t13_f6_extreme_slot_via_emit_log_keeps_log_skeleton(gate_logs):
    """F6：直接餵 `_emit_log` 5,000 字元 slot ⇒ 骨架仍在（≤200、含雙標籤、不 raise）。"""
    gate_logs.clear()
    decision = WakeDecision(should_wake=False, origin_type=None,
                            reason=REASON_DAYTIME_WHITESPACE, seed_hint=None)
    _emit_log(AGENT, _LONG_SLOT, decision)

    lines = _gate_lines(gate_logs)
    _assert_log_skeleton(lines, "long slot")
    assert " slot=" in lines[0]


def test_t13_f6_extreme_slot_and_agent_via_emit_log_keeps_log_skeleton(gate_logs):
    """F6：超長 slot ＋ 超長 agent 同時出現 ⇒ 仍恰好 1 行且雙標籤俱在。"""
    gate_logs.clear()
    decision = WakeDecision(should_wake=True, origin_type="goal_driven",
                            reason=REASON_CHECKPOINT_DUE_WAKE, seed_hint=None)
    _emit_log("c" * 5000, _LONG_SLOT, decision)

    _assert_log_skeleton(_gate_lines(gate_logs), "long slot + long agent")


def test_t13_f6_extreme_reason_via_emit_log_keeps_log_skeleton(gate_logs):
    """F6：`reason` 逾長（5,000 字元）⇒ 內文可截斷，但 `reason=` 標籤**永不**被吃掉。"""
    gate_logs.clear()
    decision = WakeDecision(should_wake=True, origin_type="goal_driven",
                            reason="R" * 5000, seed_hint=None)
    _emit_log(AGENT, "daytime", decision)

    lines = _gate_lines(gate_logs)
    _assert_log_skeleton(lines, "long reason")
    assert " decision=WAKE" in lines[0]
    assert " origin=goal_driven" in lines[0]


def test_t13_f6_repeated_long_calls_emit_one_line_each(gate_logs):
    """F6：超長輸入連續 3 次 ⇒ 恰好 3 行（無漏記、無重複、無多行外洩）。"""
    gate_logs.clear()
    for _ in range(3):
        _call(agent_id="d" * 5000)
    lines = _gate_lines(gate_logs)

    assert len(lines) == 3
    for line in lines:
        assert "\n" not in line and len(line) <= LOG_MAX_CHARS
        assert "decision=" in line and "reason=" in line


# ══════════════════════════════════════════════════════════════
# T14-F7 例外兜底（`__str__` raise ⇒ 恰好一行固定骨架、永不 raise）
# ══════════════════════════════════════════════════════════════
#
# 契約：組行過程若拋例外，改發**固定的**兜底骨架行 ⇒ 永遠恰好一行、永不 raise。
# 生產路徑（`evaluate_wake_gate`）觸發兜底時，輸入必然已 fail-closed
# （`agent_id` 非 str / `timeslot` 非合法值），故兜底行的
# `decision=SLEEP reason=FAIL_CLOSED_DEFAULT_SLEEP` 與判定本身一致。

_FALLBACK_LINE = (
    "[LifeThreadGate] agent=<unprintable> slot=<unprintable>"
    " decision=SLEEP reason=FAIL_CLOSED_DEFAULT_SLEEP"
)


@pytest.mark.parametrize("bad_field", ["agent_id", "timeslot", "both"])
def test_t14_f7_unprintable_input_never_raises_and_emits_one_skeleton_line(gate_logs, bad_field):
    """F7：`__str__` 會 raise 的物件當 `agent_id` / `timeslot` ⇒ 不拋例外、恰好 1 行。"""
    gate_logs.clear()
    agent_id = _RaisingStrObject() if bad_field in ("agent_id", "both") else AGENT
    timeslot = _RaisingStrObject() if bad_field in ("timeslot", "both") else "daytime"

    try:
        decision = evaluate_wake_gate(agent_id, NOW, timeslot, [], CAP, None, None)
    except BaseException as exc:  # pragma: no cover - 只有回歸時才會到此
        pytest.fail(f"{bad_field}: evaluate_wake_gate raised {type(exc).__name__}: {exc}")

    lines = _gate_lines(gate_logs)
    _assert_log_skeleton(lines, f"unprintable {bad_field}")

    # 非 str 的 `agent_id` / 非法的 `timeslot` 一律 fail-closed
    assert decision.should_wake is False, bad_field
    assert decision.reason == "FAIL_CLOSED_DEFAULT_SLEEP", bad_field
    assert decision.reason == REASON_FAIL_CLOSED_DEFAULT_SLEEP, bad_field
    assert lines[0].endswith("decision=SLEEP reason=FAIL_CLOSED_DEFAULT_SLEEP"), bad_field


@pytest.mark.parametrize("bad_field", ["agent_id", "timeslot"])
def test_t14_f7_fallback_line_is_exact_fixed_skeleton(gate_logs, bad_field):
    """F7：兜底行逐字等於固定骨架（不得 0 行、不得改寫、不得多行）。"""
    gate_logs.clear()
    _call(**{bad_field: _RaisingStrObject()})

    lines = _gate_lines(gate_logs)
    assert len(lines) == 1, f"{bad_field}: {lines}"
    assert lines[0] == _FALLBACK_LINE, bad_field
    assert "<unprintable>" in lines[0], bad_field


def test_t14_f7_emit_log_fallback_for_unprintable_slot_and_agent(gate_logs):
    """F7：直接呼叫 `_emit_log`，slot 與 agent 皆不可列印 ⇒ 仍恰好一行固定骨架。"""
    gate_logs.clear()
    decision = WakeDecision(should_wake=True, origin_type="goal_driven",
                            reason=REASON_CHECKPOINT_DUE_WAKE, seed_hint=None)

    _emit_log(_RaisingStrObject(), _RaisingStrObject(), decision)

    lines = _gate_lines(gate_logs)
    assert len(lines) == 1, lines
    assert lines[0] == _FALLBACK_LINE
    assert "decision=" in lines[0] and "reason=" in lines[0]


def test_t14_f7_repeated_unprintable_calls_emit_one_line_each(gate_logs):
    """F7：不可列印輸入連續 3 次 ⇒ 恰好 3 行（每次呼叫各 1 行，無 0 行情形）。"""
    gate_logs.clear()
    for _ in range(3):
        _call(agent_id=_RaisingStrObject())

    lines = _gate_lines(gate_logs)
    assert len(lines) == 3, lines
    for line in lines:
        assert line == _FALLBACK_LINE
        assert "decision=" in line and "reason=" in line


# ══════════════════════════════════════════════════════════════
# T15 偏離宣告釘死（module docstring 三關鍵字不得被後人刪掉）
# ══════════════════════════════════════════════════════════════

_DEVIATION_KEYWORDS = ("§5.2.4", "consumed_by_wake", "unresolved_tensions")


def _module_docstring(gate_source: str) -> str:
    doc = ast.get_docstring(ast.parse(gate_source))
    assert doc is not None and doc.strip(), "module docstring 遺失"
    return doc


def test_t15_docstring_pins_all_three_deviation_keywords(gate_source):
    """T15：docstring **同時**含 `§5.2.4`、`consumed_by_wake`、`unresolved_tensions`。"""
    doc = _module_docstring(gate_source)

    for keyword in _DEVIATION_KEYWORDS:
        assert keyword in doc, f"偏離宣告關鍵字遺失：{keyword}"


@pytest.mark.parametrize("keyword", _DEVIATION_KEYWORDS)
def test_t15_each_deviation_keyword_survives_individually(gate_source, keyword):
    """T15：三個關鍵字各自獨立釘死（任一被刪即紅燈，並附 `summary` 實質內容檢查）。"""
    doc = _module_docstring(gate_source)

    assert keyword in doc, keyword

    if keyword == "§5.2.4":
        # §5.2.4 條款的實質內容＝fact text 必須有 summary
        assert "summary" in doc, "§5.2.4 條款的 summary 說明遺失"
        assert "偏離宣告" in doc, "偏離宣告區塊標題遺失"


def test_t15_docstring_declares_deviation_section_with_three_items(gate_source):
    """T15：偏離宣告區塊存在，且編號 1/2/3 三條齊備（不得只留標題）。"""
    doc = _module_docstring(gate_source)

    assert "偏離宣告" in doc
    for ordinal in ("1.", "2.", "3."):
        assert ordinal in doc, f"偏離宣告第 {ordinal} 條遺失"


def test_t15_docstring_is_module_first_statement_and_substantial(gate_source):
    """T15：docstring 為模組第一個敘述（`ast.get_docstring` 可取得）且非空殼。"""
    tree = ast.parse(gate_source)
    first = tree.body[0]

    assert isinstance(first, ast.Expr), "第一個敘述不是 Expr"
    assert isinstance(first.value, ast.Constant), "第一個敘述不是字串常數"
    assert isinstance(first.value.value, str)

    doc = ast.get_docstring(tree)
    # `ast.get_docstring` 會 `inspect.cleandoc`（去尾端空白／空行）；逐字比對去尾後內容
    assert doc is not None
    assert doc.rstrip() == first.value.value.rstrip()
    assert first.value.value.strip() in gate_source
    assert len(doc) > 200
    for keyword in _DEVIATION_KEYWORDS:
        assert keyword in first.value.value, keyword


# ══════════════════════════════════════════════════════════════
# T16 容量政策旋鈕 `enforce_strict_capacity`（LIFE-THREAD-M3-2）
# ══════════════════════════════════════════════════════════════
#
# 本票裁定（decision-complete，實作照做）：
#   `True`（**預設**）＝ 容量防線**最高優先**：活躍池飽和 ⇒ 恆 SLEEP / ACTIVE_POOL_SATURATED
#                        （即使同時有世界碰撞或到期檢驗點）—— 現行安全行為、逐位元不變。
#   `False`           ＝ 契約 §4.2 **純淨模式**：容量防線**不得否決**外部訊號，優先序改為
#                        世界碰撞 → 到期線頭 → 到期張力 → 容量飽和 → 留白
#                        （飽和仍回 ACTIVE_POOL_SATURATED，保留觀測痕跡）。
#   旗標必須是**真 bool**（`isinstance(x, bool)`）；非 bool ⇒ 步驟 0 fail-closed（不 raise）。

_FLAG_PARAM = "enforce_strict_capacity"

_SATURATED_QUIET = _saturated_case(2, "quiet")
_SATURATED_COLLISION = _saturated_case(2, "world_collision")


def _case_call(case: dict, **overrides) -> WakeDecision:
    """以 `_saturated_case(...)` 的欄位為底（可覆寫）呼叫閘門；鍵衝突在此一次解掉。"""
    params = {
        "agent_id": AGENT,
        "current_time": NOW,
        "timeslot": "daytime",
        "active_threads": [],
        "capacity_limit": CAP,
        "recent_perceptions": None,
        "unresolved_tensions": None,
    }
    params.update(case)
    params.update(overrides)
    return evaluate_wake_gate(**params)


def _find_func(tree: ast.AST, name: str) -> ast.FunctionDef:
    """取模組頂層函式節點（找不到即紅燈）。"""
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"找不到函式：{name}")


# ── T16-A 介面釘死（inspect.signature）─────────────────────────

def test_t16_a_signature_pins_flag_as_last_parameter_with_default_true():
    """T16-A：參數順序逐字不變、最後多一個 `enforce_strict_capacity`，且**預設為 True**。

    既有呼叫端（7 個位置參數）的相容性即由此釘死；順序被動到就紅燈。
    """
    params = inspect.signature(evaluate_wake_gate).parameters

    assert list(params) == [
        "agent_id", "current_time", "timeslot", "active_threads", "capacity_limit",
        "recent_perceptions", "unresolved_tensions", _FLAG_PARAM,
    ]

    flag = params[_FLAG_PARAM]
    assert flag.default is True, "預設值必須是 True（現行安全行為）"
    assert isinstance(flag.default, bool)
    assert flag.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD, "必須 keyword-or-positional"


def test_t16_a_legacy_seven_argument_call_still_uses_strict_default():
    """T16-A：舊式 7 位置參數呼叫 ⇒ 不傳旗標即走 `True`（飽和仍 SLEEP / ACTIVE_POOL_SATURATED）。"""
    decision = evaluate_wake_gate(AGENT, NOW, "morning",
                                  list(_SATURATED_COLLISION["active_threads"]), 2,
                                  list(_COLLISION), None)

    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"


def test_t16_a_flag_is_accepted_positionally_as_eighth_argument():
    """T16-A：旗標以**第 8 個位置參數**傳入 `False` ⇒ 與關鍵字傳入結果逐位元相同。"""
    threads = list(_SATURATED_COLLISION["active_threads"])
    perceptions = list(_COLLISION)

    positional = evaluate_wake_gate(AGENT, NOW, "morning", threads, 2, perceptions, None, False)
    keyword = _case_call(_SATURATED_COLLISION, enforce_strict_capacity=False)

    assert positional == keyword
    assert positional.should_wake is True
    assert positional.reason == "WORLD_COLLISION_WAKE"


# ── T16-B~E 純淨模式（`False`）：容量不得否決外部訊號 ─────────────

@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_t16_b_pure_mode_saturated_with_world_collision_wakes(capacity):
    """T16-B（純淨模式）：飽和 ＋ 合格世界碰撞 ⇒ WAKE / WORLD_COLLISION_WAKE / world_collision。"""
    decision = _case_call(_saturated_case(capacity, "world_collision"),
                          enforce_strict_capacity=False)

    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.reason == REASON_WORLD_COLLISION_WAKE
    assert decision.origin_type == "world_collision"
    assert decision.seed_hint["origin_type"] == "world_collision"
    assert decision.seed_hint["facts"] == ["外頭颳起大風"]


@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_t16_c_pure_mode_saturated_with_due_thread_wakes(capacity):
    """T16-C（純淨模式）：飽和 ＋ 到期線頭 ⇒ WAKE / CHECKPOINT_DUE_WAKE。"""
    decision = _case_call(_saturated_case(capacity, "thread_due"),
                          enforce_strict_capacity=False)

    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"
    assert decision.reason == REASON_CHECKPOINT_DUE_WAKE
    assert decision.origin_type == "goal_driven"
    assert decision.seed_hint["due_thread_ids"] == ["th-due"]


@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_t16_d_pure_mode_saturated_with_due_tension_wakes(capacity):
    """T16-D（純淨模式）：飽和 ＋ 到期張力 ⇒ WAKE / UNRESOLVED_TENSION_DUE_WAKE / necessity_driven。"""
    decision = _case_call(_saturated_case(capacity, "tension_due"),
                          enforce_strict_capacity=False)

    assert decision.should_wake is True
    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.reason == REASON_TENSION_DUE_WAKE
    assert decision.origin_type == "necessity_driven"
    assert decision.seed_hint["tension_ids"] == ["tn-due"]


@pytest.mark.parametrize("timeslot", list(VALID_TIMESLOTS))
def test_t16_e_pure_mode_saturated_without_other_signals_keeps_saturation_trace(gate_logs, timeslot):
    """T16-E（純淨模式）：飽和 ＋ 無其他訊號 ⇒ **保留觀測痕跡** SLEEP / ACTIVE_POOL_SATURATED。

    明確**不得**退化成 `DAYTIME_WHITESPACE` / `REFLECTION_SLOT_CLEAR`（容量飽和仍要看得見）。
    """
    gate_logs.clear()
    decision = _case_call(_SATURATED_QUIET, timeslot=timeslot, enforce_strict_capacity=False)

    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"
    assert decision.reason == REASON_ACTIVE_POOL_SATURATED
    assert decision.reason not in ("DAYTIME_WHITESPACE", "REFLECTION_SLOT_CLEAR")
    assert decision.origin_type is None
    assert decision.seed_hint is None
    assert _gate_lines(gate_logs)[0].endswith("reason=ACTIVE_POOL_SATURATED")


def test_t16_e_pure_mode_saturation_yields_to_due_tension():
    """T16-E：飽和 ＋ 到期張力 ⇒ 張力先於飽和（純淨模式的飽和只在無外部訊號時留痕）。"""
    decision = _case_call(_saturated_case(2, "tension_due"), timeslot="night",
                          enforce_strict_capacity=False)

    assert decision.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert decision.reason != "ACTIVE_POOL_SATURATED"


@pytest.mark.parametrize("timeslot,expected", [("daytime", "DAYTIME_WHITESPACE"),
                                               ("night", "REFLECTION_SLOT_CLEAR")])
def test_t16_f_pure_mode_unsaturated_matches_strict_bitwise(timeslot, expected):
    """T16-F（純淨模式）：**不飽和** ＋ 無訊號 ⇒ 與 `True` **逐位元相同**（留白語意不變）。"""
    idle = [_thread("th-idle")]

    strict = _call(timeslot=timeslot, enforce_strict_capacity=True,
                   active_threads=idle, capacity_limit=3)
    pure = _call(timeslot=timeslot, enforce_strict_capacity=False,
                 active_threads=idle, capacity_limit=3)

    assert strict == pure
    assert pure.should_wake is False
    assert pure.reason == expected
    assert pure.origin_type is None
    assert pure.seed_hint is None


# ── T16-G 對照組：同一輸入、旋鈕一開一關 ────────────────────────

@pytest.mark.parametrize("capacity", [1, 2, 3])
def test_t16_g_flag_contrast_strict_sleeps_pure_wakes(capacity):
    """T16-G（釘死旋鈕語意）：同一組「飽和 ＋ 碰撞」輸入下，
    `True` ⇒ SLEEP / ACTIVE_POOL_SATURATED；`False` ⇒ WAKE / WORLD_COLLISION_WAKE。"""
    case = _saturated_case(capacity, "world_collision")

    strict = _case_call(case, enforce_strict_capacity=True)
    pure = _case_call(case, enforce_strict_capacity=False)

    assert strict.should_wake is False
    assert strict.reason == "ACTIVE_POOL_SATURATED"
    assert strict.origin_type is None
    assert strict.seed_hint is None

    assert pure.should_wake is True
    assert pure.reason == "WORLD_COLLISION_WAKE"
    assert pure.origin_type == "world_collision"

    assert strict != pure


@pytest.mark.parametrize("disturbance",
                         ["quiet", "world_collision", "thread_due", "tension_due", "all_at_once"])
def test_t16_g_strict_mode_ignores_every_kind_of_disturbance_when_saturated(disturbance):
    """T16-G：`True`（預設）下，任何擾動都無法穿透容量防線（回歸：現行行為不變）。"""
    decision = _case_call(_saturated_case(2, disturbance), enforce_strict_capacity=True)

    assert decision.should_wake is False
    assert decision.reason == "ACTIVE_POOL_SATURATED"


def test_t16_g_pure_mode_priority_order_collision_first():
    """T16-G：純淨模式優先序 —— 世界碰撞壓過到期線頭與到期張力（兩者同時在場）。"""
    decision = _case_call(_saturated_case(2, "all_at_once"), enforce_strict_capacity=False)

    assert decision.should_wake is True
    assert decision.reason == "WORLD_COLLISION_WAKE"
    assert decision.origin_type == "world_collision"


def test_t16_g_pure_mode_priority_order_thread_before_tension():
    """T16-G：純淨模式優先序 —— 到期線頭壓過到期張力。"""
    threads = [_thread(f"th-{i}", check_after_ts=None) for i in range(2)]
    threads[-1] = _thread("th-due", check_after_ts=_iso(NOW - 300), origin_type="goal_driven")

    decision = _call(timeslot="night", enforce_strict_capacity=False, active_threads=threads,
                     capacity_limit=2, unresolved_tensions=list(_DUE_TENSION))

    assert decision.should_wake is True
    assert decision.reason == "CHECKPOINT_DUE_WAKE"


# ── T16-H 非 bool 旗標 ⇒ fail-closed（不 raise）────────────────

_NON_BOOL_FLAGS = (
    ("int-one", 1),
    ("int-zero", 0),
    ("string-true", "true"),
    ("string-yes", "yes"),
    ("string-false", "false"),
    ("none", None),
    ("empty-list", []),
    ("empty-dict", {}),
    ("empty-tuple", ()),
)


@pytest.mark.parametrize("flag", [case[1] for case in _NON_BOOL_FLAGS],
                         ids=[case[0] for case in _NON_BOOL_FLAGS])
def test_t16_h_non_bool_flag_fails_closed_without_raising(flag):
    """T16-H：旗標非真 bool（`1`／`0`／`"true"`／`"yes"`／`None`／`[]` …）⇒
    SLEEP / FAIL_CLOSED_DEFAULT_SLEEP，且**呼叫不拋例外**。"""
    assert not isinstance(flag, bool), "本矩陣只放非 bool"

    try:
        decision = _case_call(_SATURATED_COLLISION, enforce_strict_capacity=flag)
    except BaseException as exc:  # pragma: no cover - 只有回歸時才會到此
        pytest.fail(f"enforce_strict_capacity={flag!r}: raised {type(exc).__name__}: {exc}")

    assert decision.should_wake is False
    assert decision.reason == "FAIL_CLOSED_DEFAULT_SLEEP"
    assert decision.reason == REASON_FAIL_CLOSED_DEFAULT_SLEEP
    assert decision.origin_type is None
    assert decision.seed_hint is None


def test_t16_h_non_bool_flag_is_fail_closed_before_capacity_scan():
    """T16-H：非 bool 旗標走步驟 0；即使輸入本可 WAKE（不飽和＋碰撞）也一律 fail-closed。"""
    decision = _call(enforce_strict_capacity="true", recent_perceptions=list(_COLLISION))

    assert decision.should_wake is False
    assert decision.reason == "FAIL_CLOSED_DEFAULT_SLEEP"


def test_t16_h_bool_versus_int_is_distinguished():
    """T16-H：`isinstance(x, bool)` 語意釘死 —— `True`/`False` 合格，`1`/`0` 不合格（bool ⊂ int 陷阱）。"""
    assert isinstance(True, bool) and isinstance(False, bool)
    assert not isinstance(1, bool) and not isinstance(0, bool)

    assert _case_call(_SATURATED_QUIET,
                      enforce_strict_capacity=False).reason == "ACTIVE_POOL_SATURATED"
    assert _case_call(_SATURATED_QUIET,
                      enforce_strict_capacity=1).reason == "FAIL_CLOSED_DEFAULT_SLEEP"


# ── T16-I 結構不變量：單一私有容量判定、兩條路徑共用 ───────────────

def test_t16_i_saturation_is_computed_once_and_shared_by_both_paths(gate_tree):
    """T16-I：`_decide` 內 `_pool_saturated` **恰好呼叫一次**，其結果被兩條政策分支共用
    （不得複製貼上兩份計數邏輯 ⇒ 避免日後漂移）。"""
    decide = _find_func(gate_tree, "_decide")

    calls = [
        node for node in ast.walk(decide)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id == "_pool_saturated"
    ]
    assert len(calls) == 1, f"容量判定呼叫次數 = {len(calls)}（必須恰好 1）"

    loads = [
        node for node in ast.walk(decide)
        if isinstance(node, ast.Name) and node.id == "saturated"
        and isinstance(node.ctx, ast.Load)
    ]
    assert len(loads) >= 2, "單一判定結果必須被兩條政策分支共用"


def test_t16_i_pool_saturated_docstring_matches_implementation(gate_source):
    """T16-I（NEW-1 結清）：`_pool_saturated` docstring 逐字描述實作真正在做的事。"""
    tree = ast.parse(gate_source)
    doc = ast.get_docstring(_find_func(tree, "_pool_saturated"))
    assert doc is not None and doc.strip()

    for token in ("Mapping", "strip()", "ACTIVE_THREAD_STATUS", "active",
                  " active ", ">= capacity_limit"):
        assert token in doc, f"`_pool_saturated` docstring 缺：{token}"

    for token in ("多計", "fail-closed", "FAIL_CLOSED_DEFAULT_SLEEP", "不 raise"):
        assert token in doc, f"`_pool_saturated` docstring 缺方向性／例外宣告：{token}"

    # 舊字面（「非字串 ⇒ 不計」）必須已被改寫，不得再誤導後人
    assert '非字串、或非 `"active"`（含 `None`）⇒ **不計**' not in doc, "舊的誤導字面仍在"


def test_t16_i_module_docstring_pins_the_three_settled_rulings(gate_source):
    """T16-I（偏離 ①②③ 結清）：module docstring 明寫參數化容量政策、due-based 張力、
    `consumed_by_wake` 嚴格 `is True` 語意，且載明 M5 需就預設值做最終裁定。"""
    doc = _module_docstring(gate_source)

    assert _FLAG_PARAM in doc
    assert "參數化的刻意偏離" in doc
    assert "M5" in doc and "裁定" in doc

    assert "due-based" in doc
    assert "due_at" in doc and "check_after_ts" in doc and "tension_id" in doc
    assert "未帶到期時間的張力永不喚醒" in doc

    assert "consumed_by_wake" in doc
    assert "is True" in doc
    assert "WorldPerceptionTrace" in doc


# ── T16-J `consumed_by_wake` 矩陣（嚴格 `is True` 才跳過）────────

_CONSUMED_MATRIX = (
    ("missing", _DROP, True),
    ("false", False, True),
    ("none", None, True),
    ("true", True, False),
    ("int-one", 1, True),
    ("string-true", "true", True),
    ("string-yes", "yes", True),
)


@pytest.mark.parametrize("case_id,consumed,expects_wake", _CONSUMED_MATRIX,
                         ids=[case[0] for case in _CONSUMED_MATRIX])
def test_t16_j_consumed_by_wake_strict_identity_matrix(case_id, consumed, expects_wake):
    """T16-J：缺欄／`False`／`None`／`1`／`"true"`／`"yes"` ⇒ **仍可喚醒**；
    只有嚴格 `is True` ⇒ 跳過該筆（無其他命中 ⇒ SLEEP / DAYTIME_WHITESPACE）。"""
    record = _world_record(consumed_by_wake=consumed, extra={"summary": "唯一事實"})
    decision = _call(recent_perceptions=[record])

    if expects_wake:
        assert decision.should_wake is True, case_id
        assert decision.reason == "WORLD_COLLISION_WAKE", case_id
        assert decision.origin_type == "world_collision", case_id
        assert decision.seed_hint["facts"] == ["唯一事實"], case_id
    else:
        assert decision.should_wake is False, case_id
        assert decision.reason == "DAYTIME_WHITESPACE", case_id
        assert decision.origin_type is None, case_id
        assert decision.seed_hint is None, case_id


def test_t16_j_strict_true_skipped_but_other_fresh_record_still_wakes():
    """T16-J：嚴格 `True` 的那筆被跳過，同批其他筆照常命中（逐筆判定、非整批否決）。"""
    decision = _call(recent_perceptions=[
        _world_record(consumed_by_wake=True, extra={"summary": "已消費"}),
        _world_record(consumed_by_wake="yes", extra={"summary": "未消費"}),
    ])

    assert decision.should_wake is True
    assert decision.seed_hint["facts"] == ["未消費"]


@pytest.mark.parametrize("flag", [True, False])
def test_t16_j_consumed_matrix_is_flag_independent(flag):
    """T16-J：`consumed_by_wake` 語意不隨容量政策旋鈕改變（`True`／`False` 同結果）。"""
    consumed = _call(recent_perceptions=[_world_record(consumed_by_wake=True)],
                     enforce_strict_capacity=flag)
    fresh = _call(recent_perceptions=[_world_record(consumed_by_wake=1)],
                  enforce_strict_capacity=flag)

    assert consumed.reason == "DAYTIME_WHITESPACE"
    assert fresh.reason == "WORLD_COLLISION_WAKE"


# ── T16-K due-based 張力收斂（無到期欄 ⇒ 永不喚醒）────────────────

@pytest.mark.parametrize("timeslot", list(REFLECTION_SLOTS))
@pytest.mark.parametrize("flag", [True, False])
def test_t16_k_undated_tension_never_wakes(timeslot, flag):
    """T16-K：未帶到期時間的張力**永不喚醒**（`morning`/`night` ⇒ REFLECTION_SLOT_CLEAR）。"""
    decision = _call(
        timeslot=timeslot,
        enforce_strict_capacity=flag,
        unresolved_tensions=[
            {"tension_id": "tn-a", "text": "沒有到期時間"},
            {"tension_id": "tn-b", "text": "也沒有", "urgency": "high"},
            {"id": "tn-c", "status": "open"},
        ],
    )

    assert decision.should_wake is False, (timeslot, flag)
    assert decision.reason == "REFLECTION_SLOT_CLEAR", (timeslot, flag)
    assert decision.reason == REASON_REFLECTION_SLOT_CLEAR, (timeslot, flag)


@pytest.mark.parametrize("shape", ["check_after_ts_null", "no_due_field_at_all"])
def test_t16_k_tension_with_null_or_absent_due_never_wakes_in_daytime(shape):
    """T16-K：`check_after_ts=None` 或缺欄 ⇒ 不喚醒（留白）。"""
    row = ({"tension_id": "tn-1", "check_after_ts": None} if shape == "check_after_ts_null"
           else {"tension_id": "tn-1"})

    decision = _call(unresolved_tensions=[row])

    assert decision.should_wake is False
    assert decision.reason == "DAYTIME_WHITESPACE"


def test_t16_k_due_at_alias_wakes_in_pure_mode_too():
    """T16-K：別名 `due_at` 到期 ⇒ 喚醒（純淨模式亦同，且張力先於容量飽和）。"""
    tension = {"tension_id": "tn-alias", "due_at": _iso(NOW - 5)}

    pure = _call(timeslot="night", enforce_strict_capacity=False,
                 active_threads=[_thread(f"th-{i}") for i in range(2)], capacity_limit=2,
                 unresolved_tensions=[tension])
    strict = _call(timeslot="night", enforce_strict_capacity=True,
                   unresolved_tensions=[tension])

    assert pure.should_wake is True
    assert pure.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert pure.origin_type == "necessity_driven"
    assert pure.seed_hint["tension_ids"] == ["tn-alias"]

    assert strict.should_wake is True
    assert strict.reason == "UNRESOLVED_TENSION_DUE_WAKE"
    assert strict.origin_type == "necessity_driven"
