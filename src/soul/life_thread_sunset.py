"""LIFE-THREAD-M2-SUNSET：確定性超期歸檔（Deterministic Sunset）的**純決策層**。

**為什麼存在**
──────────────
生活線頭的活躍池有硬上限（`LIFE_THREAD_ACTIVE_CAP_HARD_MAX`）。在現況下，**沒有任何
自動路徑**會讓一條 `active` 線頭轉入終態：

- M2 的存活軸在 `allow_soft_archive=False` 下**只產 advisory**（`should_mutate=False`、
  `target_status=active`）⇒ 時間流逝**不會**讓它完成；
- M4 的 `complete`／`abandon` 動作來自 LLM 決策 ⇒ 屬**機率**事件（SIM-ACCEL-STREAK-7D
  的沙盒實測定案：這是**容量／機率**問題，不是時序問題）；
- M1 終態不可復活 ⇒ 容量位元**只進不出**。

⇒ 容量永不釋放 ⇒ 每天無法建立新線頭 ⇒ 門檻三（連續 7 天可見性）在現況下**沒有路徑**可達。

本模組提供**確定性**（不依賴 LLM 決策、不依賴機率）的歸檔條件：當「最舊且明顯過期」
的線頭滿足全部條件時，讓它走**既有溶解路徑**轉為 `completed`，自動釋放容量位元。

**邊界（為什麼是純函式模組）**
──────────────────────────────
本模組**只算條件、不動資料**：

- **0 I/O**：不讀檔、不寫檔、不呼叫 LLM、不建立任務；
- **0 `src.*` import**：純標準庫（⇒ 可在沙盒裡脫離套件原地載入）；
- **不決定狀態**：sunset 的目標狀態由 M2（`life_thread_dissolution`）裁決；本模組只回答
  「**這一輪要不要 sunset、sunset 哪一條**」。

**與 M2 的分工（工單 §0.4）**
────────────────────────────
M2 **已經**支援 `checkpoints_exhausted=True` ⇒ `target_status=completed`／
`should_mutate=True`／`reason=CHECKPOINTS_EXHAUSTED`（並建確定性的 `reflection_prompt`）；
唯一缺口是那個旗標「**由呼叫端顯式注入**」（M1 fold 的 13 個欄位沒有檢驗點清單，
**無法從資料推斷**）⇒ **不得猜測**。

⇒ 本模組的存在意義就是**把那個旗標確定性地算出來**；M2 的純決策層**零改動**。

**槽的定義（工單 §0.5）**
────────────────────────
排程：`src/timezone_utils.py::LOCAL_TZ = ZoneInfo("America/New_York")`、
morning ＝ 08:00、night ＝ 22:00（**間隔不均**：14h／10h）。
⇒ **不可用「天數 × 2」或「小時 / 12」估算槽數**；本模組一律用**槽索引**
（`slot_index()`：槽＝從 `morning_hour` 起算到 `night_hour` 的時間區間，槽序號
`day_ordinal(t) * 2 + (0 morning / 1 night)`）。

⚠️ **工單 §3.1 的兩處表述之取捨（已登記，見實作紀錄）**：工單同時給了
(a) 公式 `day_ordinal(t) * 2 + (0 if t.hour < 12 else 1)` 與
(b) 邊界語意「`21:59:59` **仍是 morning 槽**；`22:00:00` 起進 night 槽」。
兩者**互斥**（21:59:59 在 (a) 是 night 槽）。本模組採 **(b)**：槽是**從 08:00 起算的
時間區間**（08:00→22:00 為 morning 槽，22:00→次日 08:00 為 night 槽），而 (b) 正是
工單要求「**必須寫成會紅的測試**」的那組邊界。⇒ `07:59:59` 屬於**前一日的 night 槽**
（`(day-1) * 2 + 1`），`21:59:59` 屬於**當日的 morning 槽**（`day * 2 + 0`）。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

# ══════════════════════════════════════════════════════════════
# §1 常數（全部具名，不得寫裸字面量）
# ══════════════════════════════════════════════════════════════

#: §3.1 超期門檻（單位＝天）。**嚴格大於**才算過期（恰好 21 天整不算）。
#: 為什麼取 21 而不是 14：生產 13/20 顆 active 線頭 age > 14，取 14 會對大量
#: 「仍年輕但被寫入」的線頭觸發；21 則只落在 21~33 天的明顯過期組（7/10 隻 agent）。
SUNSET_MAX_LIFETIME_DAYS: int = 21

#: §3.1 「連續無新線頭」門檻（單位＝槽）。連續 ≥ 3 槽未建立任何新線頭。
SUNSET_MIN_NO_NEW_THREAD_SLOTS: int = 3

#: 每天的槽數（morning ＋ night）。**僅供文件／報告口徑**；槽序號本身由
#: `slot_index()` 以「槽起始時刻」計算（不可用「天數 × 2」估算）。
SUNSET_SLOTS_PER_DAY: int = 2

#: 一天的秒數（`age_days` 的分母，單位＝秒）。
SUNSET_SECONDS_PER_DAY: int = 86400

#: 從未建立過任何線頭時的「距上次新建槽數」哨兵（= 無限大的可比較替代）。
SUNSET_NO_CREATION_SLOTS: int = 10 ** 9

#: morning 槽的起始小時（本地時區）。
SUNSET_MORNING_HOUR: int = 8

#: night 槽的起始小時（本地時區）。
SUNSET_NIGHT_HOUR: int = 22


# ══════════════════════════════════════════════════════════════
# §2 內部工具（純函式；不 I/O）
# ══════════════════════════════════════════════════════════════


def _parse_timestamp(value: Any) -> Optional[datetime]:
    """ISO 8601 字串 → aware `datetime`（naive 一律視為 UTC）；不可解析 ⇒ `None`。

    口徑對齊既有 M2（`life_thread_dissolution`）與沙盒測試的 `_parse_iso`：
    `"Z"` 尾綴先換成 `+00:00`；naive datetime 視為 UTC（**不猜測**本地時區）。
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _to_local(ts: datetime, tz: Any) -> datetime:
    """把（可能 naive 的）時間點轉成 `tz` 本地時間（naive 一律先視為 UTC）。"""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(tz)


def _oldest_key(thread: Mapping[str, Any]) -> Tuple[datetime, str]:
    """「最舊」排序鍵：`created_at` 最小者；同值時以 `thread_id` 字典序取最小。

    🔴 **決定性**：同一組輸入恆選同一條（`thread_id` 字典序 tiebreak）。
    `created_at` 不可解析者排到**最後**（⇒ 永遠不會被當成最舊 ⇒ 不會被 sunset）。
    """
    created = _parse_timestamp(thread.get("created_at"))
    sentinel = datetime.max.replace(tzinfo=timezone.utc)
    return (created if created is not None else sentinel, str(thread.get("thread_id") or ""))


# ══════════════════════════════════════════════════════════════
# §3 純函式（工單 §3.1 的四個）
# ══════════════════════════════════════════════════════════════


def slot_index(
    ts: datetime,
    *,
    tz: Any,
    morning_hour: int = SUNSET_MORNING_HOUR,
    night_hour: int = SUNSET_NIGHT_HOUR,
) -> int:
    """該時間點所屬槽的全域序號（morning＝偶數槽、night＝奇數槽）。

    槽＝**從起始小時起算的時間區間**（本地時區）：

    - `[morning_hour, night_hour)` ⇒ **當日**第 0 槽（morning）；
    - `[night_hour, 24:00)` ⇒ **當日**第 1 槽（night）；
    - `[00:00, morning_hour)` ⇒ **前一日**第 1 槽（night 槽尚未結束）。

    邊界語意（工單 §3.1）：`08:00:00` 整點本身屬於 morning 槽；`08:00:01` 亦然；
    `21:59:59` 仍是 morning 槽；`22:00:00` 起進 night 槽；`07:59:59` 屬前一日 night 槽。

    理由（§0.5）：一天兩槽但間隔 14h／10h，**不可**用「天數 × 2」或「小時 / 12」估算。
    """
    local = _to_local(ts, tz)
    day = local.date().toordinal()
    if morning_hour <= local.hour < night_hour:
        return day * SUNSET_SLOTS_PER_DAY
    if local.hour >= night_hour:
        return day * SUNSET_SLOTS_PER_DAY + 1
    return (day - 1) * SUNSET_SLOTS_PER_DAY + 1


def slots_since_last_creation(
    agent_events: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    tz: Any,
    morning_hour: int = SUNSET_MORNING_HOUR,
    night_hour: int = SUNSET_NIGHT_HOUR,
) -> int:
    """該 agent 自「最後一次建立新線頭」以來經過的槽數（單位＝槽，下限 0）。

    - 輸入＝該 agent 的 M1 事件流**或**已 fold 的 thread 集合（兩者都帶 `created_at`）；
      **要的是「最後一次建立」＝ `created_at` 最大值**（不是最小值）。
    - 槽數 ＝ `slot_index(now) − slot_index(last_created_at)`，**下限 0**。
    - 從未建立過 ⇒ 回傳 `SUNSET_NO_CREATION_SLOTS`（極大值；**不**回 0，以免把
      「從未建立」誤讀成「剛剛建立」而**放行** sunset）。
    """
    last: Optional[datetime] = None
    for item in agent_events or ():
        if not isinstance(item, Mapping):
            continue
        created = _parse_timestamp(item.get("created_at"))
        if created is None:
            continue
        if last is None or created > last:
            last = created
    if last is None:
        return SUNSET_NO_CREATION_SLOTS
    delta = slot_index(
        now, tz=tz, morning_hour=morning_hour, night_hour=night_hour
    ) - slot_index(last, tz=tz, morning_hour=morning_hour, night_hour=night_hour)
    return max(0, delta)


def age_days(thread: Mapping[str, Any], *, now: datetime) -> Optional[float]:
    """線頭的存活天數（`(now − created_at) / 86400`，單位＝天）。

    `created_at` 不可解析 ⇒ `None`（呼叫端須視為「不可判定」⇒ 不得 sunset）。
    """
    if not isinstance(thread, Mapping):
        return None
    created = _parse_timestamp(thread.get("created_at"))
    if created is None:
        return None
    current = _to_local(now, timezone.utc) if now.tzinfo is None else now
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (current - created).total_seconds() / SUNSET_SECONDS_PER_DAY


def should_sunset(
    thread: Mapping[str, Any],
    *,
    now: datetime,
    agent_active_count: int,
    agent_cap: int,
    slots_since_new_thread: int,
    oldest_thread_id: Optional[str] = None,
) -> bool:
    """複合條件，**四項全部成立**才回 `True`。

    1. `age_days > SUNSET_MAX_LIFETIME_DAYS`（**嚴格大於**；恰好 21 天不算）；
    2. `agent_active_count >= agent_cap`（**容量壓力**：沒有壓力就沒有釋放的必要）；
    3. `slots_since_new_thread >= SUNSET_MIN_NO_NEW_THREAD_SLOTS`；
    4. 該線頭是該 agent 的**最舊** active 線頭（`created_at` 最小、同值以 `thread_id`
       字典序 tiebreak ⇒ 決定性）。

    🔴 條件 4 需要「該 agent 的全體 active 集合」才能判定 ⇒ 以 `oldest_thread_id`
    **顯式注入**（由 `pick_sunset_target` 計算）。`oldest_thread_id=None`（未注入）時
    本函式**只**判定條件 1~3 ⇒ 呼叫端必須用 `pick_sunset_target` 取得完整四條件裁決。

    線頭必須是 `active`（非 active 一律 `False`；**`dormant` 永不 sunset** ——
    INV-1：`dormant` 不催，轉 `dormant` 會靜默停掉未來的檢驗點）。
    """
    if not isinstance(thread, Mapping):
        return False
    if thread.get("status") != "active":
        return False
    age = age_days(thread, now=now)
    if age is None:
        return False
    if not age > SUNSET_MAX_LIFETIME_DAYS:
        return False
    if agent_active_count < agent_cap:
        return False
    if slots_since_new_thread < SUNSET_MIN_NO_NEW_THREAD_SLOTS:
        return False
    if oldest_thread_id is not None:
        if str(thread.get("thread_id") or "") != str(oldest_thread_id):
            return False
    return True


def pick_sunset_target(
    threads: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    agent_active_count: int,
    agent_cap: int,
    slots_since_new_thread: int,
) -> Optional[Dict[str, Any]]:
    """回傳應被 sunset 的**那一條**（或 `None`）。**每次評估最多釋放一個位元。**

    - 先取該 agent 的 `active` 線頭；挑**最舊**的一條（`created_at` 最小、同值以
      `thread_id` 字典序 tiebreak ⇒ **決定性**）。
    - 該最舊線頭必須**同時**滿足條件 1~3（條件 4 由本函式的挑選**結構性**保證）。
    - 全部不成立 ⇒ `None`（本輪不 sunset）。
    """
    active = [
        t
        for t in (threads or ())
        if isinstance(t, Mapping) and t.get("status") == "active"
    ]
    if not active:
        return None
    oldest = min(active, key=_oldest_key)
    if not should_sunset(
        oldest,
        now=now,
        agent_active_count=agent_active_count,
        agent_cap=agent_cap,
        slots_since_new_thread=slots_since_new_thread,
        oldest_thread_id=str(oldest.get("thread_id") or ""),
    ):
        return None
    return dict(oldest)


def should_sunset_for_agent(
    threads: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
    agent_active_count: int,
    agent_cap: int,
    tz: Any,
    morning_hour: int = SUNSET_MORNING_HOUR,
    night_hour: int = SUNSET_NIGHT_HOUR,
) -> bool:
    """agent 層級的 sunset 裁決（布林）：本輪該 agent 是否**有一條**線頭該被 sunset。

    這是接線層（M2 呼叫點）用的**單一入口**：`checkpoints_exhausted = 本函式(...)`。
    實作**委派**給 `pick_sunset_target`（不另立一套判定 ⇒ 兩者不可能漂移）。
    """
    slots_since = slots_since_last_creation(
        threads,
        now=now,
        tz=tz,
        morning_hour=morning_hour,
        night_hour=night_hour,
    )
    return (
        pick_sunset_target(
            threads,
            now=now,
            agent_active_count=agent_active_count,
            agent_cap=agent_cap,
            slots_since_new_thread=slots_since,
        )
        is not None
    )
