# src/soul/life_thread_dissolution.py
# Soul OS — LIFE-THREAD-ENGINE M2：蔡戈尼記憶溶解管線（Zeigarnik Dissolution Pipeline）純決策層
"""LIFE-THREAD-M2 — 契約 §3「蔡戈尼記憶溶解管線」的**純決策層 library**。

規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §3（`:228-311`）。
資料真相來源：M1 `src/soul/life_threads.py` 的 fold 後欄位與值域（**只讀、不 import**）。

核心裁定（逐條）
────────────────
1. **唯一觸發點（§3.1）**：線頭轉為 `completed` **或** `abandoned`。`active` / `dormant`
   **永不觸發溶解**（張力尚未解除）。非終態的一切「看起來該收掉了」的訊號，
   一律降級為**諮詢訊號**（`extra_metadata["advisory"] is True`）且**預設不變更任何狀態**。
2. **L1 冪等（§3.3）**：fold 後 `dissolved_at is not None` ⇒ 直接跳過（0 LLM 呼叫），
   回 `extra_metadata["skipped"] == "already_dissolved"`。
3. **INV-1（§3.5）**：`dormant` **不催**（不計入閘門的到期檢驗點）⇒ 把 `active` 轉 `dormant`
   ＝**靜默停掉該線頭未來的檢驗點**，屬生產存活性政策。故 `allow_soft_archive`
   **預設 `False`**（opt-in，最終政策留待 M5 接線票裁定）；且**只有嚴格 `is True`** 才是 opt-in，
   其餘（`1` / `"yes"` / `None` …）一律走諮詢訊號（fail-safe：往「不變更」方向錯）。
4. **每日配額（§3.6）**：`DISSOLVE_MAX_PER_DAY = 3` ＝ `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`。
   本模組只提供**純計數輔助**（`count_dissolved_on_date` / `remaining_daily_dissolution_budget`），
   **不在決策層套用配額**：配額屬「LLM 呼叫層」責任，決策層手上的線頭集合未必完整，
   用不完整資料套配額會往「多溶解」的吵方向錯。故本模組**不得因配額抑制任何 `should_mutate`**。
5. **邊界一律嚴格大於（`>`）**：`age == timedelta(days=14)`、`idle == timedelta(days=7)`
   **不算**超限；各 `+1` 秒才算（改成 `>=` 即錯）。
   🔴 **宣告的第七處衝突修正（`updated_at == created_at` 時不獨立判定 `STALE_NO_PROGRESS`）**：
   工單步驟 4 的兩軸在「線頭從未被更新過」時是**同一段時距**（`idle == age`），
   若兩軸都判，工單指定的邊界 smoke（`created_at == updated_at == T0`，
   `current_time = T0 + 14 天` ⇒ 期望 `advisory is False`）將無法成立。
   故本模組裁定：**只有 `updated_at > created_at`（存在「最後更新」這一獨立事實）時，
   `idle` 才是獨立的無進展訊號**；`updated_at == created_at` ⇒ 該段時距只由 `age` 軸代表
   （同一事實不重複判兩次）。此規則**不放寬**任何嚴格大於的邊界。
6. **決策序短路**：步驟 0（驗證）→ 1（冪等）→ 2（終態溶解）→ 3（呼叫端宣告檢驗點耗盡）
   → 4（諮詢訊號）→ 5（其餘保持活躍）；任一階段命中即回傳。

邊界宣告（本模組刻意不做的事）
──────────────────────────────
- **`checkpoints_exhausted` 由呼叫端顯式注入**（預設 `False`）：M1 fold 的 13 個欄位裡
  沒有任何「檢驗點清單／剩餘次數」，此事**無法從資料推斷**，故不得猜測；
  預設 `False` 讓契約 §3.1 保持純淨。
- **`reflection_prompt` 是確定性模板，不是 LLM 呼叫**（契約 §3.2 只定義 prompt 契約）；
  同一輸入**逐字**產生同一字串。
- **SAGE 寫入與 `Fact` 組裝 out of scope**（契約 §3.4 的組裝含浮點常數，本模組
  **不得出現任何浮點字面量**）；`add_fact` / `post_reply_commit` 一律不碰。
- **`sedimentation_context["soul_context"]` 恆為 `None`**：`load_persona(agent_id)` 是 I/O，
  屬 M5 呼叫端補值；本模組為 0 I/O 純函式。
- **No-Scoring 剛性邊界**（VISION §2.4）：本模組沒有任何分數／權重／信心／機率語意；
  批量輸出依 `thread_id` **字典序**（決定性，與輸入順序無關），**不是**重要性排序。
- 票面若干欄位與狀態值**在 M1 不存在**（進度狀態／已解決旗標／檢驗點清單三欄；
  已溶解／已歸檔／已暫停三個狀態值），本模組一律不使用 —— 值域以 `STATUS_VALUES` 為準。
- **0 生產接線**：無 scheduler／cron／entry point 掛載、0 `src.*` import、0 I/O、0 LLM、
  0 定時器、0 隨機、0 執行緒。唯一副作用是 `logging`；例外情境只 warning，**永不 raise**，
  且**永不 mutate 入參**。
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("soul_os.soul.life_thread_dissolution")


# ──────────────────────────────────────────────────────────────
# §1 常數（全部具名，不得寫裸字面量）
# ──────────────────────────────────────────────────────────────

#: §2.2 值域（與 M1 `life_threads.STATUS_VALUES` **逐字相同**）。
STATUS_VALUES: Tuple[str, ...] = ("active", "dormant", "completed", "abandoned")

#: §2.5 終態（不可復活、不再佔活躍額度；與 M1 **逐字相同**）。
TERMINAL_STATUSES: Tuple[str, ...] = ("completed", "abandoned")

#: §3.6 每日溶解上限（＝ M1 `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`）。
DISSOLVE_MAX_PER_DAY = 3

#: 一天的秒數（整數，**不用浮點**）。
SECONDS_PER_DAY = 86400

#: 步驟 4 諮詢訊號的預設門檻（天）。
DEFAULT_MAX_ACTIVE_DURATION_DAYS = 14
DEFAULT_STALE_CHECK_THRESHOLD_DAYS = 7

#: §3.3 L1 冪等跳過碼。
SKIPPED_ALREADY_DISSOLVED = "already_dissolved"

#: 日誌前綴（本模組唯一副作用）。
_LOG_PREFIX = "[LifeThreadDissolution] "


class ThreadStatus(str, Enum):
    """§2.2 狀態值域（與 M1 `STATUS_VALUES` 逐字相同）。"""

    ACTIVE = "active"
    DORMANT = "dormant"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class DissolutionReason(str, Enum):
    """溶解理由（離散枚舉，**不是**分數）。`NONE` 表示本輪不作任何變更。"""

    GOAL_ACHIEVED = "goal_achieved"
    CHECKPOINTS_EXHAUSTED = "checkpoints_exhausted"
    TIME_HORIZON_EXCEEDED = "time_horizon_exceeded"
    MANUAL_CLOSE = "manual_close"
    STALE_NO_PROGRESS = "stale_no_progress"
    NONE = "none"


@dataclass(frozen=True)
class DissolutionEvaluation:
    """單一線頭的溶解裁決（frozen：裁決不可就地改寫）。

    `extra_metadata` **恆**含三個鍵：`"advisory"` / `"sedimentation_context"` / `"skipped"`；
    另在適用時帶 `"invalid"` / `"age_days"` / `"idle_days"`。
    """

    thread_id: str
    target_status: ThreadStatus
    reason: DissolutionReason
    should_mutate: bool
    reflection_prompt: Optional[str] = None
    extra_metadata: Optional[Dict[str, Any]] = None


#: §3.2 `reflection_prompt` 的固定輸出契約尾綴（逐字，呼叫端據此解析 LLM 輸出）。
_PROMPT_OUTPUT_CONTRACT = (
    '請只回 JSON：{"dissolution": "<第一人稱 1~3 句、<=400 字元>", '
    '"meaning_kind": "competence|connection|loss|relief|discovery|none"}'
)

#: 三種語意的敘述句（確定性模板；`{title}` / `{days}` 為唯二佔位符）。
_PROMPT_SEMANTIC_ACHIEVED = "你剛完成一條生活線頭：「{title}」，歷時 {days} 天。"
_PROMPT_SEMANTIC_ABANDONED = "你剛放棄一條生活線頭：「{title}」，歷時 {days} 天。"
_PROMPT_SEMANTIC_DORMANT = "一條生活線頭「{title}」已擱置，歷時 {days} 天。"

#: 三種語意的提問句（契約 §3.2：「以角色視角回答…對我意味著什麼？」）。
_PROMPT_QUESTION_ACHIEVED = "以第一人稱回答：這段經歷，對「我」意味著什麼？"
_PROMPT_QUESTION_ABANDONED = "以第一人稱回答：這次放棄，對「我」意味著什麼？"
_PROMPT_QUESTION_DORMANT = "以第一人稱回答：這段懸而未決的經歷，對「我」意味著什麼？"

#: 標題缺失時的固定替代字串。
_TITLE_FALLBACK = "（無題）"

#: 語意歸類：完成／放棄／擱置（**離散歸類，不是分數**）。
_ACHIEVED_REASONS = (DissolutionReason.GOAL_ACHIEVED, DissolutionReason.CHECKPOINTS_EXHAUSTED)
_ABANDONED_REASONS = (DissolutionReason.MANUAL_CLOSE,)

#: §3.2 合法 `terminal_reason` 覆寫值（**不含** `NONE`；`NONE` 屬「不覆寫」）。
_OVERRIDE_REASON_VALUES: Tuple[str, ...] = (
    DissolutionReason.GOAL_ACHIEVED.value,
    DissolutionReason.CHECKPOINTS_EXHAUSTED.value,
    DissolutionReason.TIME_HORIZON_EXCEEDED.value,
    DissolutionReason.MANUAL_CLOSE.value,
    DissolutionReason.STALE_NO_PROGRESS.value,
)

__all__ = [
    "STATUS_VALUES",
    "TERMINAL_STATUSES",
    "DISSOLVE_MAX_PER_DAY",
    "SECONDS_PER_DAY",
    "DEFAULT_MAX_ACTIVE_DURATION_DAYS",
    "DEFAULT_STALE_CHECK_THRESHOLD_DAYS",
    "SKIPPED_ALREADY_DISSOLVED",
    "ThreadStatus",
    "DissolutionReason",
    "DissolutionEvaluation",
    "evaluate_thread_dissolution",
    "evaluate_batch_dissolution",
    "count_dissolved_on_date",
    "remaining_daily_dissolution_budget",
]


# ──────────────────────────────────────────────────────────────
# §2 內部工具（全部純函式、永不 raise、永不 mutate 入參）
# ──────────────────────────────────────────────────────────────


def _parse_time(value: Any) -> Optional[datetime]:
    """把時間輸入正規化為 **aware UTC** `datetime`；不合格回 `None`。

    規則：`datetime`（naive ⇒ 視為 UTC）／非 bool 的 `int` `float`（epoch 秒）／
    ISO-8601 字串（`fromisoformat`；尾端 `Z` 換成 `+00:00` 再試一次）。
    其餘型別或解析失敗 ⇒ `None`（**不 raise**）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed: Optional[datetime]
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            if not text.endswith("Z"):
                return None
            try:
                parsed = datetime.fromisoformat(text[:-1] + "+00:00")
            except ValueError:
                return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _is_non_negative_int(value: Any) -> bool:
    """**非 bool** 的非負整數（`True` / `-1` / `"14"` / `None` 一律不合格）。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _invalid(thread_id: str, invalid_reason: str) -> DissolutionEvaluation:
    """步驟 0 的 fail-safe 裁決：保持原狀（`ACTIVE` / `NONE` / 不變更）＋ 一行 WARNING。"""
    logger.warning("%sinvalid input: %s", _LOG_PREFIX, invalid_reason)
    return DissolutionEvaluation(
        thread_id=thread_id if isinstance(thread_id, str) else "",
        target_status=ThreadStatus.ACTIVE,
        reason=DissolutionReason.NONE,
        should_mutate=False,
        reflection_prompt=None,
        extra_metadata={
            "invalid": invalid_reason,
            "advisory": False,
            "sedimentation_context": None,
            "skipped": None,
        },
    )


def _safe_title(thread: Mapping) -> str:
    """`title` 缺失／非字串／空白 ⇒ `"（無題）"`（確定性）。"""
    title = thread.get("title")
    if isinstance(title, str) and title.strip():
        return title
    return _TITLE_FALLBACK


def _semantic_pair(reason: DissolutionReason) -> Tuple[str, str]:
    """依 `reason` 選（敘述句、提問句）語意：完成／放棄／擱置。

    `NONE` 為 defensive fallback（正常路徑不會產生 prompt），一律走「擱置」語意。
    """
    if reason in _ACHIEVED_REASONS:
        return (_PROMPT_SEMANTIC_ACHIEVED, _PROMPT_QUESTION_ACHIEVED)
    if reason in _ABANDONED_REASONS:
        return (_PROMPT_SEMANTIC_ABANDONED, _PROMPT_QUESTION_ABANDONED)
    return (_PROMPT_SEMANTIC_DORMANT, _PROMPT_QUESTION_DORMANT)


def _build_reflection_prompt(
    thread: Mapping,
    reason: DissolutionReason,
    now: datetime,
    created_at: datetime,
) -> str:
    """確定性 `reflection_prompt`（契約 §3.2）：含 `title`、持續天數、固定輸出契約尾綴。

    不是 LLM 呼叫；同一輸入逐字相同。持續天數為 `created_at → now` 的 `.days`
    （負值夾為 `0`，避免不合常理的輸入產生負天數）。
    """
    semantic, question = _semantic_pair(reason)
    days = (now - created_at).days
    if days < 0:
        days = 0
    head = semantic.format(title=_safe_title(thread), days=days)
    return "\n".join((head, question, _PROMPT_OUTPUT_CONTRACT))


def _build_sedimentation_context(
    thread: Mapping,
    agent_id: Optional[str],
    terminal_status: str,
) -> Dict[str, Any]:
    """契約 §3.2 的沉澱輸入欄位（逐欄；**不含** SAGE 寫入與 `Fact` 組裝）。

    `soul_context` **恆為 `None`**：`load_persona(agent_id)` 是 I/O，由 M5 呼叫端補上。
    `agent_id` 參數優先於線頭內的同名值（M1 fold 不含 `agent_id`，由呼叫端帶入）。
    """
    resolved_agent: Any = agent_id if agent_id is not None else thread.get("agent_id")
    return {
        "agent_id": resolved_agent,
        "title": thread.get("title"),
        "narrative_content": thread.get("narrative_content"),
        "origin_type": thread.get("origin_type"),
        "terminal_status": terminal_status,
        "soul_context": None,
        "created_at": thread.get("created_at"),
        "updated_at": thread.get("updated_at"),
    }


def _resolve_terminal_reason(terminal_reason: Any) -> Optional[DissolutionReason]:
    """§3.2「非法值降級」：合法覆寫值 ⇒ 回對應 enum；其餘（含 `NONE`／`None`）⇒ `None` ＋ WARNING。"""
    if terminal_reason is None:
        return None
    if isinstance(terminal_reason, DissolutionReason):
        candidate = terminal_reason.value
    elif isinstance(terminal_reason, str):
        candidate = terminal_reason
    else:
        logger.warning(
            "%signoring illegal terminal_reason %r (型別不合法)", _LOG_PREFIX, terminal_reason
        )
        return None
    if candidate in _OVERRIDE_REASON_VALUES:
        return DissolutionReason(candidate)
    logger.warning(
        "%signoring illegal terminal_reason %r (不在合法覆寫值域)", _LOG_PREFIX, terminal_reason
    )
    return None


def _mutating_evaluation(
    thread: Mapping,
    thread_id: str,
    target_status: ThreadStatus,
    reason: DissolutionReason,
    now: datetime,
    created_at: datetime,
    agent_id: Optional[str],
) -> DissolutionEvaluation:
    """步驟 2／3 的共用組裝：真實溶解（`should_mutate=True`，`advisory=False`）。"""
    logger.info(
        "%sdissolution candidate thread_id=%s target=%s reason=%s",
        _LOG_PREFIX,
        thread_id,
        target_status.value,
        reason.value,
    )
    return DissolutionEvaluation(
        thread_id=thread_id,
        target_status=target_status,
        reason=reason,
        should_mutate=True,
        reflection_prompt=_build_reflection_prompt(thread, reason, now, created_at),
        extra_metadata={
            "advisory": False,
            "sedimentation_context": _build_sedimentation_context(
                thread, agent_id, target_status.value
            ),
            "skipped": None,
        },
    )


# ──────────────────────────────────────────────────────────────
# §3 主函式（單筆裁決）
# ──────────────────────────────────────────────────────────────


def evaluate_thread_dissolution(
    thread: Dict[str, Any],
    current_time: Any,
    max_active_duration_days: int = 14,
    stale_check_threshold_days: int = 7,
    allow_soft_archive: bool = False,
    *,
    agent_id: Optional[str] = None,
    checkpoints_exhausted: bool = False,
    terminal_reason: Optional[str] = None,
) -> DissolutionEvaluation:
    """單一線頭的溶解裁決（**純決策、永不 raise、永不 mutate 入參**）。

    決策序（任一階段命中即回傳）
    ────────────────────────────
    0. **驗證與正規化**（fail-safe）：`thread` 為 `Mapping`、`thread_id` 非空 `str`、
       `status ∈ STATUS_VALUES`、`created_at` / `updated_at` / `current_time` 可解析、
       兩個天數門檻為非 bool 的非負整數；任一不合格 ⇒ `ACTIVE / NONE / 不變更`
       ＋ `extra_metadata["invalid"]` ＋ WARNING。
    1. **冪等（§3.3 L1）**：`status` 為終態**且** `dissolved_at is not None`
       ⇒ `skipped="already_dissolved"`、`should_mutate=False`、`reason=NONE`。
    2. **終態溶解（§3.1 唯一觸發點）**：`completed` ⇒ `GOAL_ACHIEVED`；
       `abandoned` ⇒ `MANUAL_CLOSE`；`target_status` ＝ 該終態、`should_mutate=True`；
       `terminal_reason` 為**合法覆寫值**（不含 `NONE`）時覆寫 `reason`，非法值忽略 ＋ WARNING。
    3. **呼叫端顯式宣告檢驗點耗盡**：僅 `status == "active"` **且**
       `checkpoints_exhausted is True` ⇒ `COMPLETED / CHECKPOINTS_EXHAUSTED / True`。
       非真 bool 一律視為 `False`（只 WARNING，不寫 `invalid`）。
    4. **諮詢訊號（預設不變更任何狀態）**：僅 `status == "active"`。
       `age = current_time - created_at > timedelta(days=max_active_duration_days)`
       ⇒ `TIME_HORIZON_EXCEEDED`（`extra["age_days"] = age.days`）；
       否則當 `updated_at > created_at`（存在獨立的「最後更新」事實）時，
       `idle = current_time - updated_at > timedelta(days=stale_check_threshold_days)`
       ⇒ `STALE_NO_PROGRESS`（`extra["idle_days"] = idle.days`）。
       **邊界嚴格大於**：14 天整、7 天整**不算**超限，`+1` 秒才算。
       `updated_at == created_at` ⇒ `idle` 與 `age` 同源（線頭自建立後從未更新），
       不獨立判定 `STALE_NO_PROGRESS`（同一段時距只由 `age` 軸代表）。
       順序：`TIME_HORIZON_EXCEEDED` 優先於 `STALE_NO_PROGRESS`。
       `allow_soft_archive is True`（opt-in，M5 政策）⇒ `target_status=DORMANT`、
       `should_mutate=True`、產生 prompt 與沉澱輸入（`terminal_status="dormant"`）；
       否則 ⇒ `target_status=ACTIVE`、`should_mutate=False`、`reflection_prompt=None`。
       兩種情況都 `extra["advisory"]=True` 且**保留 `reason`**（觀測痕跡不得抹掉）。
       `dormant` 狀態的線頭在此步**不被變更**（避免 `active ↔ dormant` 震盪）。
    5. **其餘保持活躍**：`ACTIVE / NONE / should_mutate=False / prompt=None`。
    """
    # ── 步驟 0：驗證與正規化（fail-safe，永不 raise）────────────
    if not isinstance(thread, Mapping):
        return _invalid("", "thread 不是 Mapping（非 Mapping 一律 fail-safe 保持原狀）")

    raw_thread_id = thread.get("thread_id")
    thread_id = raw_thread_id if isinstance(raw_thread_id, str) else ""
    if not isinstance(raw_thread_id, str) or not raw_thread_id:
        return _invalid(thread_id, "thread_id 缺失、非字串或空字串")

    status = thread.get("status")
    if not isinstance(status, str) or status not in STATUS_VALUES:
        return _invalid(thread_id, "status 不在 STATUS_VALUES 值域：" + repr(status))

    created_at = _parse_time(thread.get("created_at"))
    if created_at is None:
        return _invalid(thread_id, "created_at 缺失或不可解析")

    updated_at = _parse_time(thread.get("updated_at"))
    if updated_at is None:
        return _invalid(thread_id, "updated_at 缺失或不可解析")

    now = _parse_time(current_time)
    if now is None:
        return _invalid(thread_id, "current_time 不可解析")

    if not _is_non_negative_int(max_active_duration_days):
        return _invalid(thread_id, "max_active_duration_days 必須是非 bool 的非負整數")

    if not _is_non_negative_int(stale_check_threshold_days):
        return _invalid(thread_id, "stale_check_threshold_days 必須是非 bool 的非負整數")

    # ── 步驟 1：冪等（§3.3 L1）───────────────────────────────
    if status in TERMINAL_STATUSES and thread.get("dissolved_at") is not None:
        return DissolutionEvaluation(
            thread_id=thread_id,
            target_status=ThreadStatus(status),
            reason=DissolutionReason.NONE,
            should_mutate=False,
            reflection_prompt=None,
            extra_metadata={
                "advisory": False,
                "sedimentation_context": None,
                "skipped": SKIPPED_ALREADY_DISSOLVED,
            },
        )

    # ── 步驟 2：終態溶解候選（§3.1 唯一觸發點）─────────────────
    if status in TERMINAL_STATUSES:
        target_status = ThreadStatus(status)
        reason = (
            DissolutionReason.GOAL_ACHIEVED
            if status == ThreadStatus.COMPLETED.value
            else DissolutionReason.MANUAL_CLOSE
        )
        override = _resolve_terminal_reason(terminal_reason)
        if override is not None:
            reason = override
        return _mutating_evaluation(
            thread, thread_id, target_status, reason, now, created_at, agent_id
        )

    # ── 步驟 3：呼叫端顯式宣告檢驗點耗盡 ───────────────────────
    if not isinstance(checkpoints_exhausted, bool):
        logger.warning(
            "%scheckpoints_exhausted 非 bool（%r）⇒ fail-safe 視為 False",
            _LOG_PREFIX,
            checkpoints_exhausted,
        )
    if status == ThreadStatus.ACTIVE.value and checkpoints_exhausted is True:
        return _mutating_evaluation(
            thread,
            thread_id,
            ThreadStatus.COMPLETED,
            DissolutionReason.CHECKPOINTS_EXHAUSTED,
            now,
            created_at,
            agent_id,
        )

    # ── 步驟 4：諮詢訊號（預設不變更任何狀態）──────────────────
    if status == ThreadStatus.ACTIVE.value:
        reason = DissolutionReason.NONE
        extra: Dict[str, Any] = {
            "advisory": True,
            "sedimentation_context": None,
            "skipped": None,
        }
        age = now - created_at
        if age > timedelta(days=max_active_duration_days):
            reason = DissolutionReason.TIME_HORIZON_EXCEEDED
            extra["age_days"] = age.days
        elif updated_at > created_at:
            # `updated_at > created_at`：存在獨立的「最後更新」事實，idle 才是獨立訊號。
            # 兩者相等時 idle 與 age 同源（自建立後從未更新），只由 age 軸代表。
            idle = now - updated_at
            if idle > timedelta(days=stale_check_threshold_days):
                reason = DissolutionReason.STALE_NO_PROGRESS
                extra["idle_days"] = idle.days

        if reason is not DissolutionReason.NONE:
            if allow_soft_archive is True:
                extra["sedimentation_context"] = _build_sedimentation_context(
                    thread, agent_id, ThreadStatus.DORMANT.value
                )
                logger.info(
                    "%ssoft archive candidate thread_id=%s reason=%s",
                    _LOG_PREFIX,
                    thread_id,
                    reason.value,
                )
                return DissolutionEvaluation(
                    thread_id=thread_id,
                    target_status=ThreadStatus.DORMANT,
                    reason=reason,
                    should_mutate=True,
                    reflection_prompt=_build_reflection_prompt(
                        thread, reason, now, created_at
                    ),
                    extra_metadata=extra,
                )
            logger.info(
                "%sadvisory only thread_id=%s reason=%s",
                _LOG_PREFIX,
                thread_id,
                reason.value,
            )
            return DissolutionEvaluation(
                thread_id=thread_id,
                target_status=ThreadStatus.ACTIVE,
                reason=reason,
                should_mutate=False,
                reflection_prompt=None,
                extra_metadata=extra,
            )

    # ── 步驟 5：其餘保持活躍 ──────────────────────────────────
    return DissolutionEvaluation(
        thread_id=thread_id,
        target_status=ThreadStatus.ACTIVE,
        reason=DissolutionReason.NONE,
        should_mutate=False,
        reflection_prompt=None,
        extra_metadata={
            "advisory": False,
            "sedimentation_context": None,
            "skipped": None,
        },
    )


# ──────────────────────────────────────────────────────────────
# §4 批量裁決（決定性排序；不套用 §3.6 配額）
# ──────────────────────────────────────────────────────────────


def _batch_sort_key(item: Any, evaluation: DissolutionEvaluation) -> Tuple[int, str, str]:
    """決定性排序鍵：`thread_id` 字典序優先；取不到者排最後（依 `str()`）。

    最後一段 tiebreak 保證**與輸入順序無關**的逐位元一致輸出。
    """
    raw_thread_id = item.get("thread_id") if isinstance(item, Mapping) else None
    if isinstance(raw_thread_id, str) and raw_thread_id:
        return (0, raw_thread_id, repr(evaluation))
    return (1, str(item), repr(evaluation))


def evaluate_batch_dissolution(
    active_threads: List[Dict[str, Any]],
    current_time: Any,
    max_active_duration_days: int = 14,
    stale_check_threshold_days: int = 7,
    allow_soft_archive: bool = False,
) -> List[DissolutionEvaluation]:
    """批量裁決（**永不 raise、永不 mutate 入參**）。

    - 非 `list` ⇒ `[]` ＋ WARNING；空 `list` ⇒ `[]`。
    - 輸出**依 `thread_id` 字典序**（決定性、與輸入順序無關；**絕非**評分排序）；
      取不到 `thread_id` 者排最後。
    - 回傳長度**恆等於輸入長度**：每筆都要有裁決，不得靜默丟棄。
    - 🔴 **不套用 §3.6 每日配額**：配額屬 LLM 呼叫層責任；決策層手上的清單未必完整，
      用不完整資料套配額會往「多溶解」的吵方向錯。故**不得**因配額抑制任何 `should_mutate`
      （配額輔助見 `remaining_daily_dissolution_budget`，僅供呼叫端查詢）。
    """
    if not isinstance(active_threads, list):
        logger.warning(
            "%sactive_threads 非 list（%r）⇒ 回空清單", _LOG_PREFIX, type(active_threads).__name__
        )
        return []
    if not active_threads:
        return []

    evaluations: List[DissolutionEvaluation] = []
    keys: List[Tuple[int, str, str]] = []
    for item in active_threads:
        evaluation = evaluate_thread_dissolution(
            item,
            current_time,
            max_active_duration_days,
            stale_check_threshold_days,
            allow_soft_archive,
        )
        evaluations.append(evaluation)
        keys.append(_batch_sort_key(item, evaluation))

    order = sorted(range(len(evaluations)), key=lambda index: keys[index])
    return [evaluations[index] for index in order]


# ──────────────────────────────────────────────────────────────
# §5 契約 §3.6 配額的純輔助（可測化；**不做抑制**）
# ──────────────────────────────────────────────────────────────


def count_dissolved_on_date(threads: Any, current_time: Any) -> int:
    """數「`dissolved_at` 可解析且其 **UTC 日期** ＝ `current_time` 的 UTC 日期」的線頭筆數。

    不可解析／缺欄／非 `Mapping` 的項目**略過不計**（不 raise）；`threads` 非 `list` ⇒ `0`；
    `current_time` 不可解析 ⇒ `0`。純函式、不改入參。
    """
    if not isinstance(threads, list):
        return 0
    now = _parse_time(current_time)
    if now is None:
        return 0
    target_date = now.date()
    count = 0
    for item in threads:
        if isinstance(item, Mapping):
            dissolved_at = _parse_time(item.get("dissolved_at"))
            if dissolved_at is not None and dissolved_at.date() == target_date:
                count += 1
    return count


def remaining_daily_dissolution_budget(threads: Any, current_time: Any) -> int:
    """§3.6 剩餘額度：`max(0, DISSOLVE_MAX_PER_DAY - count_dissolved_on_date(...))`。

    `current_time` 不可解析 ⇒ 回 **0**（fail-quiet：不確定時不放行）。純函式、不改入參。
    本函式**只回報**，不做任何抑制（抑制是 LLM 呼叫層的責任）。
    """
    now = _parse_time(current_time)
    if now is None:
        return 0
    used = count_dissolved_on_date(threads, now)
    remaining = DISSOLVE_MAX_PER_DAY - used
    if remaining < 0:
        return 0
    return remaining
