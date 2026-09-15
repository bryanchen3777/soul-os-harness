# src/soul/life_thread_wake_gate.py
# Soul OS — LIFE-THREAD-M3-1: 二元存在性喚醒閘門（Salience Gate）獨立 library 模組
"""LIFE-THREAD-M3-1 — 契約 §4「二元存在性心智喚醒閘門（Salience Gate）」的**非接線 library 實作**。

核心判定（契約 §4.2 逐字）：`should_wake := check_points_due OR world_collision_detected`；
兩者皆 fail-silent → False（一律往安靜方向錯），不給模型編造瑣事的機會。
本模組**額外新增**且刻意比契約 §4.2 **更嚴（更安靜）**的容量防線
`ACTIVE_POOL_SATURATED`（來源＝ LIFE-THREAD-M3-1 工單 §3）：活躍池已滿一律 SLEEP。
未決張力**僅在到期時**才喚醒（due-based，契約 §4.2 為準）；未帶到期時間者不喚醒。
本模組為 pure function：**0 I/O、0 LLM、0 定時器、0 `src.*` import**，所有輸入一律由參數傳入。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.soul.life_thread_wake_gate")


# ──────────────────────────────────────────────────────────────
# §1 常數（全部具名，不得寫裸字面量）
# ──────────────────────────────────────────────────────────────

#: 合法時段（本 repo 排程器目前只產生 morning / night，其餘為未來擴充面）。
VALID_TIMESLOTS: Tuple[str, ...] = ("morning", "daytime", "evening", "night")

#: 留白時段：這兩個時段不成立時走「留白」語意（契約 §4.1 的兩個既有 checkpoint）。
REFLECTION_SLOTS: Tuple[str, ...] = ("morning", "night")

#: 起源型別（與 M1 `life_threads.ORIGIN_TYPES` 同值域，**不 import**）。
ORIGIN_TYPES: Tuple[str, ...] = (
    "goal_driven",
    "necessity_driven",
    "whim_driven",
    "world_collision",
)

#: 契約 §4.2 具名常數：世界碰撞視窗（小時）。**不得**寫裸字面量 `4`。
WORLD_COLLISION_WINDOW_HOURS = 4

#: 契約 §4.2 `SOURCES_QUALIFYING`（算數的來源，窮舉）。其餘（含測試用 `synthetic`）不算。
QUALIFYING_WORLD_SOURCES = frozenset(
    {"weather", "news", "news_event", "calendar", "calendar_event"}
)

#: seed_hint 的長度上限（筆數／文字字元）。
SEED_HINT_MAX_ITEMS = 5
SEED_HINT_MAX_TEXT_CHARS = 200

#: §7 觀測行硬上限（單行、不含換行）。
LOG_MAX_CHARS = 200

#: 契約 §4.2 之外的容量防線（工單 §3）：活躍池飽和 → 一律安靜。
REASON_ACTIVE_POOL_SATURATED = "ACTIVE_POOL_SATURATED"
#: 步驟 0 驗證不合格（fail-closed，往安靜方向錯）。
REASON_FAIL_CLOSED_DEFAULT_SLEEP = "FAIL_CLOSED_DEFAULT_SLEEP"
#: 判定 2：世界碰撞（契約 §4.2）。
REASON_WORLD_COLLISION_WAKE = "WORLD_COLLISION_WAKE"
#: 判定 1：線頭檢驗點到期（契約 §4.2）。
REASON_CHECKPOINT_DUE_WAKE = "CHECKPOINT_DUE_WAKE"
#: 判定 1 的張力面：未決張力**到期**才喚醒（due-based）。
REASON_TENSION_DUE_WAKE = "UNRESOLVED_TENSION_DUE_WAKE"
#: 留白：morning / night。
REASON_REFLECTION_SLOT_CLEAR = "REFLECTION_SLOT_CLEAR"
#: 留白：daytime / evening。
REASON_DAYTIME_WHITESPACE = "DAYTIME_WHITESPACE"

#: §7 觀測行前綴。
_LOG_PREFIX = "[LifeThreadGate] agent="

__all__ = [
    "WakeDecision",
    "evaluate_wake_gate",
    "VALID_TIMESLOTS",
    "REFLECTION_SLOTS",
    "ORIGIN_TYPES",
    "WORLD_COLLISION_WINDOW_HOURS",
    "QUALIFYING_WORLD_SOURCES",
    "SEED_HINT_MAX_ITEMS",
    "SEED_HINT_MAX_TEXT_CHARS",
    "LOG_MAX_CHARS",
    "REASON_ACTIVE_POOL_SATURATED",
    "REASON_FAIL_CLOSED_DEFAULT_SLEEP",
    "REASON_WORLD_COLLISION_WAKE",
    "REASON_CHECKPOINT_DUE_WAKE",
    "REASON_TENSION_DUE_WAKE",
    "REASON_REFLECTION_SLOT_CLEAR",
    "REASON_DAYTIME_WHITESPACE",
]


# ──────────────────────────────────────────────────────────────
# §2 資料結構
# ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WakeDecision:
    """閘門判定結果（二元：`should_wake` 僅 True / False，無第三態）。"""

    should_wake: bool
    origin_type: Optional[str]  # ∈ ORIGIN_TYPES，或 None（留白／fail-closed）
    reason: str                 # 上述 REASON_* 之一
    seed_hint: Optional[dict]   # JSON-serializable，或 None


# ──────────────────────────────────────────────────────────────
# §5 時間解析 helper（私有；**永不 raise**）
# ──────────────────────────────────────────────────────────────

def _parse_ts(value: Any) -> Optional[float]:
    """寬鬆地把時間值解析為 Unix epoch 秒（UTC）；無法解析 ⇒ `None`（不猜、不 raise）。

    - `datetime` ⇒ 轉 epoch 秒（naive 一律當 UTC）。
    - `int` / `float`（非 bool、finite）⇒ 直接當 epoch 秒。
    - `str` ⇒ strip 後尾端 `Z` 換成 `+00:00`，`fromisoformat()`；naive 當 UTC。
    - 其他型別／解析失敗 ⇒ `None`。
    """
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, datetime):
            moment = value
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.timestamp()
        if isinstance(value, (int, float)):
            if not math.isfinite(value):
                return None
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                moment = datetime.fromisoformat(text)
            except Exception:
                return None
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            return moment.timestamp()
        return None
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────
# 判定建構子
# ──────────────────────────────────────────────────────────────

def _sleep(reason: str) -> WakeDecision:
    return WakeDecision(should_wake=False, origin_type=None, reason=reason, seed_hint=None)


def _wake(origin_type: str, reason: str, seed_hint: Dict[str, Any]) -> WakeDecision:
    return WakeDecision(
        should_wake=True, origin_type=origin_type, reason=reason, seed_hint=seed_hint
    )


# ──────────────────────────────────────────────────────────────
# 步驟 0：Fail-Closed 驗證
# ──────────────────────────────────────────────────────────────

def _inputs_valid(
    agent_id: Any,
    current_time: Any,
    timeslot: Any,
    active_threads: Any,
    capacity_limit: Any,
) -> bool:
    """任一不合格 ⇒ 走 fail-closed（SLEEP / FAIL_CLOSED_DEFAULT_SLEEP）。"""
    if not isinstance(agent_id, str) or not agent_id.strip():
        return False
    if isinstance(current_time, bool) or not isinstance(current_time, (int, float)):
        return False
    if not math.isfinite(current_time):
        return False
    if timeslot not in VALID_TIMESLOTS:
        return False
    if isinstance(capacity_limit, bool) or not isinstance(capacity_limit, int):
        return False
    if capacity_limit < 1:
        return False
    if not isinstance(active_threads, list):
        return False
    return True


# ──────────────────────────────────────────────────────────────
# 步驟 1：容量防線（最高優先，壓過一切）
# ──────────────────────────────────────────────────────────────

def _pool_saturated(active_threads: List[Any], capacity_limit: int) -> bool:
    """`active_count ＝ 在 active_threads 中「是 mapping」的元素個數`。"""
    active_count = sum(1 for t in active_threads if isinstance(t, Mapping))
    return active_count >= capacity_limit


# ──────────────────────────────────────────────────────────────
# 步驟 2：世界碰撞防線（契約 §4.2 判定 2）
# ──────────────────────────────────────────────────────────────

def _scan_world_collision(
    recent_perceptions: List[Any], current_time: Any
) -> Optional[Dict[str, Any]]:
    """回 seed_hint（有命中）或 `None`（無命中）。讀不到 ⇒ 無證據 ⇒ `None`。"""
    floor_ts = current_time - WORLD_COLLISION_WINDOW_HOURS * 3600
    facts: List[str] = []
    event_ids: List[Optional[str]] = []

    for record in recent_perceptions:
        if not isinstance(record, Mapping):
            continue
        if record.get("accepted") is not True:
            continue
        source = record.get("source")
        if not isinstance(source, str) or source.strip() not in QUALIFYING_WORLD_SOURCES:
            continue
        extra = record.get("extra")
        if not isinstance(extra, Mapping):
            continue
        summary = extra.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        if record.get("consumed_by_wake") is True:
            continue
        parsed_ts = _parse_ts(record.get("timestamp"))
        if parsed_ts is None:
            continue
        if parsed_ts < floor_ts:
            continue  # 窗外（未來時間戳視為窗內）
        facts.append(summary.strip()[:SEED_HINT_MAX_TEXT_CHARS])
        event_id = record.get("event_id")
        event_ids.append(
            event_id if isinstance(event_id, str) and event_id.strip() else None
        )

    if not facts:
        return None

    return {
        "origin_type": "world_collision",
        "facts": facts[:SEED_HINT_MAX_ITEMS],
        "event_ids": [e for e in event_ids[:SEED_HINT_MAX_ITEMS] if e is not None],
        "source": "perception_trace",
    }


# ──────────────────────────────────────────────────────────────
# 步驟 3a：線頭檢驗點到期（契約 §4.2 判定 1）
# ──────────────────────────────────────────────────────────────

def _scan_threads_due(
    active_threads: List[Any], current_time: Any
) -> Optional[Dict[str, Any]]:
    """回 seed_hint（有到期線頭）或 `None`。時間解析失敗 ⇒ 跳過該線頭，不 raise。"""
    due: List[Tuple[float, int, Mapping]] = []
    for index, thread in enumerate(active_threads):
        if not isinstance(thread, Mapping):
            continue
        if thread.get("status") != "active":  # dormant 與終態不計
            continue
        raw = thread.get("check_after_ts")
        if raw is None:
            continue
        parsed_ts = _parse_ts(raw)
        if parsed_ts is None:
            continue
        if parsed_ts <= current_time:
            due.append((parsed_ts, index, thread))

    if not due:
        return None

    due.sort(key=lambda item: item[0])  # 最早到期在前（穩定排序；非評分）
    ordered = [item[2] for item in due]
    first = ordered[0]

    origin = first.get("origin_type")
    if origin not in ORIGIN_TYPES:
        origin = "goal_driven"

    due_thread_ids: List[str] = []
    for thread in ordered:
        thread_id = thread.get("thread_id")
        if isinstance(thread_id, str) and thread_id.strip():
            due_thread_ids.append(thread_id)
        if len(due_thread_ids) >= SEED_HINT_MAX_ITEMS:
            break

    return {
        "origin_type": origin,
        "due_thread_ids": due_thread_ids,
        "due_check_after_ts": first.get("check_after_ts"),
    }


# ──────────────────────────────────────────────────────────────
# 步驟 3b：未決張力到期（due-based，契約 §4.2 為準）
# ──────────────────────────────────────────────────────────────

def _scan_tensions_due(
    unresolved_tensions: List[Any], current_time: Any
) -> Optional[List[str]]:
    """回張力 id 清單（有到期張力，可能為空清單）或 `None`（無到期張力）。

    **只有「到期的」張力才會喚醒**；未帶到期時間的張力不喚醒。
    """
    tension_ids: List[str] = []
    any_due = False
    for tension in unresolved_tensions:
        if not isinstance(tension, Mapping):
            continue
        raw = tension.get("check_after_ts")
        if raw is None:
            raw = tension.get("due_at")
        if raw is None:
            continue
        parsed_ts = _parse_ts(raw)
        if parsed_ts is None:
            continue
        if parsed_ts > current_time:
            continue
        any_due = True
        tension_id = tension.get("tension_id")
        if not (isinstance(tension_id, str) and tension_id.strip()):
            tension_id = tension.get("id")
        if isinstance(tension_id, str) and tension_id.strip():
            tension_ids.append(tension_id)
        if len(tension_ids) >= SEED_HINT_MAX_ITEMS:
            break

    if not any_due:
        return None  # 無到期張力 ⇒ 不貢獻（未帶到期時間者不喚醒）
    return tension_ids  # 有到期張力（可能無可用 id，仍算命中）


# ──────────────────────────────────────────────────────────────
# 判定主體（步驟 0 → 4，順序即優先序）
# ──────────────────────────────────────────────────────────────

def _decide(
    agent_id: Any,
    current_time: Any,
    timeslot: Any,
    active_threads: Any,
    capacity_limit: Any,
    recent_perceptions: Any,
    unresolved_tensions: Any,
) -> WakeDecision:
    # ── 步驟 0：Fail-Closed 驗證 ──────────────────────────────
    if not _inputs_valid(agent_id, current_time, timeslot, active_threads, capacity_limit):
        return _sleep(REASON_FAIL_CLOSED_DEFAULT_SLEEP)

    # 讀不到 ≠ 有碰撞（契約 §4.2）：非 list 一律視為空，**不** fail-closed。
    perceptions = recent_perceptions if isinstance(recent_perceptions, list) else []
    tensions = unresolved_tensions if isinstance(unresolved_tensions, list) else []

    # ── 步驟 1：容量防線（最高優先，壓過一切）────────────────
    if _pool_saturated(active_threads, capacity_limit):
        return _sleep(REASON_ACTIVE_POOL_SATURATED)

    # ── 步驟 2：世界碰撞防線 ─────────────────────────────────
    collision_hint = _scan_world_collision(perceptions, current_time)
    if collision_hint is not None:
        return _wake("world_collision", REASON_WORLD_COLLISION_WAKE, collision_hint)

    # ── 步驟 3a：線頭檢驗點到期 ───────────────────────────────
    thread_hint = _scan_threads_due(active_threads, current_time)
    if thread_hint is not None:
        origin = thread_hint["origin_type"]
        return _wake(origin, REASON_CHECKPOINT_DUE_WAKE, thread_hint)

    # ── 步驟 3b：未決張力到期 ────────────────────────────────
    tension_ids = _scan_tensions_due(tensions, current_time)
    if tension_ids is not None:
        return _wake(
            "necessity_driven",
            REASON_TENSION_DUE_WAKE,
            {"origin_type": "necessity_driven", "tension_ids": tension_ids},
        )

    # ── 步驟 4：留白（兩者皆不成立）───────────────────────────
    if timeslot in REFLECTION_SLOTS:
        return _sleep(REASON_REFLECTION_SLOT_CLEAR)
    return _sleep(REASON_DAYTIME_WHITESPACE)


# ──────────────────────────────────────────────────────────────
# §7 可觀測性（每次呼叫**恰好**一行 INFO；≤ LOG_MAX_CHARS、不含換行）
# ──────────────────────────────────────────────────────────────

def _sanitize(value: Any, max_chars: int) -> str:
    text = value if isinstance(value, str) else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if max_chars <= 0:
        return ""
    return text[:max_chars]


def _emit_log(agent_id: Any, timeslot: Any, decision: WakeDecision) -> None:
    """§9 精神：每次呼叫恰好一行 INFO，fail-closed 路徑也留這一行。"""
    try:
        label = "WAKE" if decision.should_wake else "SLEEP"
        slot_text = _sanitize(timeslot, LOG_MAX_CHARS)
        reason_text = _sanitize(decision.reason, LOG_MAX_CHARS)
        origin_tail = (
            " origin=" + _sanitize(decision.origin_type, LOG_MAX_CHARS)
            if decision.origin_type is not None
            else ""
        )
        fixed_chars = (
            len(_LOG_PREFIX)
            + len(" slot=")
            + len(slot_text)
            + len(" decision=")
            + len(label)
            + len(" reason=")
            + len(reason_text)
            + len(origin_tail)
        )
        agent_text = _sanitize(agent_id, LOG_MAX_CHARS - fixed_chars)
        line = (
            f"{_LOG_PREFIX}{agent_text} slot={slot_text} decision={label}"
            f" reason={reason_text}{origin_tail}"
        )
        logger.info(_sanitize(line, LOG_MAX_CHARS))
    except Exception:  # 觀測失敗不得影響判定（永不 raise）
        return


# ──────────────────────────────────────────────────────────────
# 對外 API
# ──────────────────────────────────────────────────────────────

def evaluate_wake_gate(
    agent_id: str,
    current_time: float,          # Unix epoch 秒（UTC）
    timeslot: str,
    active_threads: list,
    capacity_limit: int,
    recent_perceptions: list = None,
    unresolved_tensions: list = None,
) -> WakeDecision:
    """契約 §4 Salience Gate 的二元判定（**pure function、永不 raise**）。

    輸入（全部由呼叫端傳入，本模組不讀任何檔案）：
      - `active_threads`：M1 `fold()` 後的線頭狀態列（`list_active()` 的產物形態），
        欄位 `thread_id` / `status` / `check_after_ts` / `origin_type`。
      - `capacity_limit`：M1 `capacity(agent_id)` 的整數結果。
      - `recent_perceptions`：`perception_trace.jsonl` 的記錄列。
      - `unresolved_tensions`：未決張力列（過期欄位 `check_after_ts` 或別名 `due_at`）。

    判定順序（即優先序）：容量防線 → 世界碰撞 → 線頭到期 → 張力到期 → 留白。
    `recent_perceptions` / `unresolved_tensions` 為 `None` 或非 list 一律視為空
    （讀不到 ≠ 有碰撞，不 fail-closed）。
    """
    try:
        decision = _decide(
            agent_id,
            current_time,
            timeslot,
            active_threads,
            capacity_limit,
            recent_perceptions,
            unresolved_tensions,
        )
    except Exception:  # 兜底 fail-closed（往安靜方向錯），永不 raise
        decision = _sleep(REASON_FAIL_CLOSED_DEFAULT_SLEEP)

    _emit_log(agent_id, timeslot, decision)
    return decision
