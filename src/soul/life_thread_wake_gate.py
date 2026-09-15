# src/soul/life_thread_wake_gate.py
# Soul OS — LIFE-THREAD-M3-1（library 落地）／LIFE-THREAD-M3-2（語意對齊與接線準備）
"""LIFE-THREAD-M3-1 / M3-2 — 契約 §4「二元存在性心智喚醒閘門（Salience Gate）」的**非接線 library 實作**。

核心判定（契約 §4.2 逐字）：`should_wake := check_points_due OR world_collision_detected`；
兩者皆 fail-silent → False（一律往安靜方向錯），不給模型編造瑣事的機會。
本模組**額外新增**且可**參數化控制**的容量防線 `ACTIVE_POOL_SATURATED`
（來源＝ LIFE-THREAD-M3-1 工單 §3；旋鈕＝ `enforce_strict_capacity`，LIFE-THREAD-M3-2 落地）。
未決張力**僅在到期時**才喚醒（due-based，契約 §4.2 為準）；未帶到期時間者不喚醒。
本模組為 pure function：**0 I/O、0 LLM、0 定時器、0 `src.*` import**，所有輸入一律由參數傳入。

**偏離宣告（逐條已結清 —— LIFE-THREAD-M3-2 把以下三處由「隱含行為」改寫為「明文裁定」；
每條即「本模組 vs 契約 §4.2」的差異）**：
  1. `extra["summary"]` 為碰撞判定的**必要條件** —— 此條件出自契約 **§5.2.4 fact-text 規則**，
     §4.2 判定式本身沒有；效果**更嚴**（無 fact text 的世界事件不喚醒，寧可留白、不得捏造）。
  2. **參數化的刻意偏離（容量防線）** —— `enforce_strict_capacity=True`（**預設**）時，容量防線
     **壓過契約 §4.2 的世界碰撞與到期檢驗點**：活躍池飽和 ⇒ 恆 `SLEEP / ACTIVE_POOL_SATURATED`
     （即使同時有世界碰撞或到期檢驗點）。`enforce_strict_capacity=False` 時退回契約純淨語意：
     世界碰撞 → 到期檢驗點（線頭 → 張力）→ 容量飽和 → 留白。
     **M5 接線票必須就這個預設值做最終政策裁定**（本票不定案，只把旋鈕做出來）。
     旗標必須是**真 `bool`**（`isinstance(x, bool)`）；非 bool（`1` / `"yes"` / `None` …）
     ⇒ 步驟 0 fail-closed `SLEEP / FAIL_CLOSED_DEFAULT_SLEEP`（**不 raise**）。
  3. **due-based 張力** —— 第三個喚醒訊號 `unresolved_tensions` 的存在本身即偏離
     （契約 §4.2 只定義兩個布林 `check_points_due` / `world_collision_detected`）；
     且**只有到期的**張力才喚醒：到期欄位 `check_after_ts`（或別名 `due_at`）且 `<= current_time`；
     **未帶到期時間的張力永不喚醒**（此為對 LIFE-THREAD-M3-1 工單 §3 字面的刻意收緊，契約 §4.2 優先）。
     張力欄位別名清單：到期時間＝`check_after_ts` / `due_at`；識別碼＝`tension_id` / `id`。
     接線（M5）時必須確保它與線頭來源**不重複計數**。
  4. `consumed_by_wake` —— **不存在於** `WorldPerceptionTrace` schema，屬本模組**自訂**的選用旗標；
     **缺欄 ⇒ 視為未消費（該筆仍可喚醒）**；**只有嚴格 `is True` 才跳過**
     （`1` / `"true"` / `"yes"` 等 truthy **不**跳過）。
  另：`due.sort()` 僅為「最早到期優先」的**決定性排序**，**不是 salience 評分排序**
  （契約 §4.2「不排序」指的是後者）。
"""
from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
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

#: 契約 §2.6.3 的計數口徑：**只有** `status == "active"` 的線頭佔活躍額度
#: （`dormant` 不佔額度；`completed` / `abandoned` 為終態，亦不佔額度）。
ACTIVE_THREAD_STATUS = "active"

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

#: §7 組行失敗時的固定兜底骨架行（**永不 raise**、保證含 `decision=` / `reason=`）。
_FALLBACK_LOG_LINE = (
    "[LifeThreadGate] agent=<unprintable> slot=<unprintable>"
    " decision=SLEEP reason=FAIL_CLOSED_DEFAULT_SLEEP"
)

#: §7 有界截斷：`agent=` / `slot=` 各自保底的最少字元數（確保不會被整段吃掉）。
_LOG_AGENT_MIN_CHARS = 1
_LOG_SLOT_MIN_CHARS = 1

#: §7 有界截斷：`origin=` 尾綴可用的額度佔比分母（`budget // 4`，整數運算）。
_LOG_ORIGIN_BUDGET_DIVISOR = 4

__all__ = [
    "WakeDecision",
    "evaluate_wake_gate",
    "VALID_TIMESLOTS",
    "REFLECTION_SLOTS",
    "ORIGIN_TYPES",
    "ACTIVE_THREAD_STATUS",
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


def _json_safe_scalar(value: Any) -> Any:
    """把輸入值正規化為 JSON 可序列化的純量。永不 raise。

    - `str` ⇒ 原樣回傳（**不截斷**，避免改變既有 ISO 字串行為）。
    - `int` / `float`（非 `bool`）⇒ 原樣回傳。
    - `datetime` / `date` ⇒ `.isoformat()`。
    - 其他（含 `None`、`bool`、list、dict）⇒ `None` 保持 `None`，
      其餘 `str(value)` 後截斷至 `SEED_HINT_MAX_TEXT_CHARS`。
    """
    try:
        if isinstance(value, str):
            return value
        if isinstance(value, bool):
            return str(value)[:SEED_HINT_MAX_TEXT_CHARS]
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, (datetime, date)):
            return value.isoformat()
        if value is None:
            return None
        return str(value)[:SEED_HINT_MAX_TEXT_CHARS]
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
    enforce_strict_capacity: Any,
) -> bool:
    """任一不合格 ⇒ 走 fail-closed（SLEEP / FAIL_CLOSED_DEFAULT_SLEEP）。

    `enforce_strict_capacity` 必須是**真 `bool`**（`isinstance(x, bool)`）：
    `1` / `0` / `"yes"` / `None` / `[]` 等一律不合格（fail-closed，往安靜方向錯）。
    """
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
    if not isinstance(enforce_strict_capacity, bool):
        return False
    return True


# ──────────────────────────────────────────────────────────────
# 容量防線（單一私有判定：`enforce_strict_capacity` 兩條路徑共用）
# 步驟 1（`True`：最高優先）／步驟 3c（`False`：排在外部訊號之後）
# ──────────────────────────────────────────────────────────────

def _pool_saturated(active_threads: List[Any], capacity_limit: int) -> bool:
    """容量是否飽和 —— 本模組**唯一**的容量判定（`enforce_strict_capacity` 兩條路徑共用同一份計數）。

    計數（逐字對應下方運算式）：
        `active_count = Σ 1 for t in active_threads`
        `               if isinstance(t, Mapping)`
        `               and str(t.get("status", "")).strip() == ACTIVE_THREAD_STATUS`
    其中 `ACTIVE_THREAD_STATUS == "active"`；比較式為 `active_count >= capacity_limit`（`>=`，含等號）。

    **會被計入**（各佔 1 個容量額度）：
      - `status == "active"`；
      - `status == " active "`（前後空白被 `.strip()` 吃掉 ⇒ **仍計入**）；
      - `status` 為**非字串**、但 `str(status).strip()` 等於 `"active"` 的物件（自訂 `__str__`）。

    **不計**：`status` 缺欄（`t.get("status", "")` ⇒ `""`）、`None`、`123`、`"dormant"`、
    `"completed"`、`"abandoned"`、任何 strip 後不等於 `"active"` 的值，以及非 `Mapping` 元素
    （契約 §2.6.3：只有 `status == "active"` 的線頭佔活躍額度）。

    **方向性聲明**：與「`status` 非字串即不計、且須逐字相等」的寬鬆字面讀法相比，上述偏差
    **只可能多計 ⇒ 偏安靜（fail-closed）**；不存在「少計而誤放行」的方向。

    例外：本函式自身**不吞例外** —— 若 `t.get` / `str(status)` 拋例外（例如自訂 `__str__` raise），
    由 `evaluate_wake_gate` 的外層兜底為 `SLEEP / FAIL_CLOSED_DEFAULT_SLEEP`（**不 raise**）。
    """
    active_count = sum(
        1
        for t in active_threads
        if isinstance(t, Mapping)
        and str(t.get("status", "")).strip() == ACTIVE_THREAD_STATUS
    )
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
        if thread.get("status") != ACTIVE_THREAD_STATUS:  # dormant 與終態不計
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
        "due_check_after_ts": _json_safe_scalar(first.get("check_after_ts")),
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
    enforce_strict_capacity: Any,
) -> WakeDecision:
    # ── 步驟 0：Fail-Closed 驗證 ──────────────────────────────
    if not _inputs_valid(agent_id, current_time, timeslot, active_threads,
                         capacity_limit, enforce_strict_capacity):
        return _sleep(REASON_FAIL_CLOSED_DEFAULT_SLEEP)

    # 讀不到 ≠ 有碰撞（契約 §4.2）：非 list 一律視為空，**不** fail-closed。
    perceptions = recent_perceptions if isinstance(recent_perceptions, list) else []
    tensions = unresolved_tensions if isinstance(unresolved_tensions, list) else []

    # 容量判定**只算一次**：`True` / `False` 兩條政策路徑共用同一份計數邏輯（避免日後漂移）。
    saturated = _pool_saturated(active_threads, capacity_limit)

    # ── 步驟 1：容量防線（僅 `enforce_strict_capacity=True`；最高優先，壓過一切）──
    if enforce_strict_capacity and saturated:
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

    # ── 步驟 3c：容量飽和（僅 `enforce_strict_capacity=False`）──────────
    # 契約 §4.2 純淨模式：容量防線**不得否決**外部訊號，故排在世界碰撞／到期檢驗點之後；
    # 但飽和仍須留下觀測痕跡（回 `ACTIVE_POOL_SATURATED`，不退化成 `DAYTIME_WHITESPACE`）。
    if not enforce_strict_capacity and saturated:
        return _sleep(REASON_ACTIVE_POOL_SATURATED)

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
    """§9 精神：每次呼叫**恰好**一行 INFO，fail-closed 路徑也留這一行。

    骨架：`[LifeThreadGate] agent=<...> slot=<...> decision=WAKE|SLEEP reason=<REASON>`
    （WAKE 再補 ` origin=<origin_type>`）。

    保證（任何輸入下）：
      - `agent_id` / `slot` **先各自有界截斷**，才組骨架；最後才對整行截斷；
      - 該行**必含** `decision=` 與 `reason=` 子字串、≤ `LOG_MAX_CHARS`、不含換行；
      - 組行過程若拋例外，改發固定的兜底骨架行 ⇒ **永遠恰好一行**、永不 raise。
    """
    try:
        label = "WAKE" if decision.should_wake else "SLEEP"
        have_origin = decision.origin_type is not None
        # 骨架固定段（`decision=` / `reason=` 的標籤本身永不被截斷）。
        fixed_chars = (
            len(_LOG_PREFIX)
            + len(" slot=")
            + len(" decision=")
            + len(label)
            + len(" reason=")
        )
        budget = LOG_MAX_CHARS - fixed_chars
        if budget < 0:
            budget = 0

        # 1) origin 尾綴先有界（不得吃光額度）。
        origin_tail = ""
        if have_origin:
            origin_room = budget // _LOG_ORIGIN_BUDGET_DIVISOR
            origin_tail = " origin=" + _sanitize(decision.origin_type, origin_room)
            if len(origin_tail) > budget:
                origin_tail = origin_tail[:budget]

        # 2) reason 內文有界，且先替 slot / agent 留下保底額度。
        reason_room = max(
            0, budget - len(origin_tail) - _LOG_SLOT_MIN_CHARS - _LOG_AGENT_MIN_CHARS
        )
        reason_text = _sanitize(decision.reason, reason_room)

        # 3) 剩下的額度對半分給 slot 與 agent（短值不截斷）。
        remaining = budget - len(origin_tail) - len(reason_text)
        if remaining < 0:
            remaining = 0
        slot_room = max(_LOG_SLOT_MIN_CHARS, remaining // 2)
        slot_text = _sanitize(timeslot, min(slot_room, remaining))
        agent_text = _sanitize(agent_id, max(0, remaining - len(slot_text)))

        line = (
            f"{_LOG_PREFIX}{agent_text} slot={slot_text} decision={label}"
            f" reason={reason_text}{origin_tail}"
        )
        logger.info(_sanitize(line, LOG_MAX_CHARS))
    except Exception:  # 觀測失敗不得影響判定（永不 raise）；但仍必須恰好一行
        try:
            logger.info(_FALLBACK_LOG_LINE)
        except Exception:
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
    enforce_strict_capacity: bool = True,
) -> WakeDecision:
    """契約 §4 Salience Gate 的二元判定（**pure function、永不 raise**）。

    輸入（全部由呼叫端傳入，本模組不讀任何檔案）：
      - `active_threads`：M1 `fold()` 後的線頭狀態列（`list_active()` 的產物形態），
        欄位 `thread_id` / `status` / `check_after_ts` / `origin_type`。
      - `capacity_limit`：M1 `capacity(agent_id)` 的整數結果。
      - `recent_perceptions`：`perception_trace.jsonl` 的記錄列。
      - `unresolved_tensions`：未決張力列（過期欄位 `check_after_ts` 或別名 `due_at`）。
      - `enforce_strict_capacity`：**容量政策旋鈕**（LIFE-THREAD-M3-2；**預設 `True` ＝ 現行安全行為**）。
        * `True` ⇒ 容量防線**最高優先**，活躍池飽和即 `SLEEP / ACTIVE_POOL_SATURATED`
          （即使同時有世界碰撞或到期檢驗點）；
          判定順序＝容量防線 → 世界碰撞 → 線頭到期 → 張力到期 → 留白。
        * `False` ⇒ 契約 §4.2 **純淨模式**，容量防線**不得否決**外部訊號；
          判定順序＝世界碰撞 → 線頭到期 → 張力到期 → 容量飽和 → 留白
          （飽和仍回 `ACTIVE_POOL_SATURATED`，保留觀測痕跡）。
        * 必須是**真 `bool`**（`isinstance(x, bool)`）；非 bool（`1` / `0` / `"yes"` / `None` / `[]`）
          ⇒ `SLEEP / FAIL_CLOSED_DEFAULT_SLEEP`（**不 raise**）。
        * 註：預設值的最終政策裁定屬 **M5 接線票**；本票只提供旋鈕，不定案。

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
            enforce_strict_capacity,
        )
    except Exception:  # 兜底 fail-closed（往安靜方向錯），永不 raise
        decision = _sleep(REASON_FAIL_CLOSED_DEFAULT_SLEEP)

    _emit_log(agent_id, timeslot, decision)
    return decision
