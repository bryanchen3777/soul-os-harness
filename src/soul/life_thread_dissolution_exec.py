# src/soul/life_thread_dissolution_exec.py
# Soul OS — LIFE-THREAD-M2-EXEC：蔡戈尼記憶溶解管線的**沉澱執行層**（純執行、0 接線）
"""LIFE-THREAD-M2-EXEC — 契約 §3.2（LLM 沉澱）＋ §3.4（SAGE Fact 寫入）的**執行層 library**。

規格唯一來源：`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §3.2（`:238-279`）／§3.4（`:288-297`）。
上游真相來源（**只讀、不 import**）：
  - M2 決策層 `src/soul/life_thread_dissolution.py`：本模組**消耗**它已組好的
    `DissolutionEvaluation.reflection_prompt` 與 `extra_metadata["sedimentation_context"]`，
    **不重寫、不重組 prompt**（決策層已裁定 prompt 逐字內容）。
  - SAGE writer `src/memory/sage/writer.py:164 MemoryWriter.add_fact(fact) -> str`
    （失敗回 `""`）；`Fact` schema gate 見 `src/memory/sage/models.py:101-118`。

定位與邊界
──────────
- **執行層，不是決策層**：本模組不判「該不該溶解」（那是 M2），只把 M2 已裁定的終態線頭
  **沉澱一次**成 SAGE `Fact` 並落地。
- **0 生產接線**：無 scheduler／cron／entry point 掛載；全庫無任何檔案 import 本模組
  （唯一引用者是 `tests/soul/test_life_thread_dissolution_exec.py`）。接線由後續票裁定。
- **pure stdlib、0 `src.*` import**（比照 M3 `life_thread_wake_gate.py` 的既有風格）。
  所有外部依賴（LLM、SAGE 寫入、時鐘、預算）**一律參數注入**；本模組不 import
  `src.llm.proxy`、`requests`、`httpx`，因此**沒有任何自行發出網路請求的路徑**。
- **不寫 M1**：本模組不呼叫 `life_threads.append_*`，**線頭狀態不因本模組改變**（契約 §3.5
  的剔除／`dissolved_at` 回填屬呼叫端責任）。本模組**永不 mutate 入參**。
- **不改 schema**：不新增 SAGE 欄位、不碰 `src/memory/sage/**`、不呼叫 `post_reply_commit`
  （契約 §3.4 禁改清單）。
- **降級哲學（fail-open 於「事後整理」，契約 §3.2 失敗處理表）**：LLM 例外／逾時／解析失敗／
  寫入失敗一律 `logger.warning` ＋ 回對應 `status`，**永不向上拋例外**，主循環不中斷；
  契約 §3.2 明訂「下一輪可重試」⇒ 本模組的冪等 sentinel **只在成功時**登記（見下）。

行為規則（逐條）
────────────────
1. **終態閘門（fail-safe）**：僅當 `evaluation` 的 `target_status ∈ TERMINAL_TARGET_STATUSES`
   且 `should_mutate is True`（**嚴格 `is True`**，比照 M2 `allow_soft_archive` 的
   「只有嚴格 True 才是 opt-in」哲學）才繼續；其餘（含無法判定者）回
   `skipped_not_terminal`、`llm_calls=0`。**active／dormant／未到期一律不得進入呼叫路徑。**
2. **依賴缺席**：`llm_call is None` ⇒ `skipped_no_llm`；`fact_writer is None` ⇒
   `skipped_no_writer`（兩者皆在**呼叫 LLM 之前**判定，不得白花一次推理）。
3. **提示來源（不得自行拼 prompt）**：`evaluation.reflection_prompt`（M2 frozen dataclass 的
   **頂層欄位**，非 `extra_metadata` 內）；缺失／非字串／空白 ⇒ `skipped_no_prompt`（0 呼叫）。
4. **預算**：呼叫前先 `budget.try_consume(agent_id)`；失敗 ⇒ `skipped_budget`（0 呼叫）＋
   `logger.warning`（**不拋例外**）。`budget=None` 時**使用行程級預設預算**
   （`_default_budget()`；成本控制預設開啟，fail-safe 方向＝省錢）。
5. **冪等**：同一 `(thread_id, terminal_status)` 在**同一行程內成功沉澱過**即不再沉澱
   ⇒ 第二次回 `skipped_duplicate`（0 呼叫）。**只在成功時登記**：失敗（`llm_failed` /
   `parse_failed` / `write_failed`）**不登記**，以保留契約 §3.2「下一輪可重試」語意。
   **不改 M1 schema**（M1 不動）。
6. **單次呼叫（硬上限，契約 §3.6）**：本模組**只有一個** `llm_call` 呼叫點
   （`_invoke_llm_once`），呼叫形式固定 `llm_call(prompt, max_tokens=CONSOLIDATION_MAX_TOKENS)`；
   以 `asyncio.wait_for` 施加 `CONSOLIDATION_TIMEOUT_SECONDS`。回傳 awaitable ⇒ await；
   否則直接取用其值（讓測試能注入同步 fake）。**無重試、無迴圈、無第二次呼叫。**
   **呼叫端不得在排程 tick 內 inline await**：實測單次延遲可達 ~26 秒
   （2026-09-17 校準：26,335.6 ms）且未來可能更長 ⇒ 必須以**背景任務**執行。
7. **降級隔離**：任何例外一律 `logger.warning` ＋ 回對應 status，**不得向上拋出**；唯一例外是
   `asyncio.CancelledError`（`BaseException`，不屬 `Exception`）——**取消必須照常傳播**，
   本模組不吞取消訊號。
8. **解析容忍**：容忍 ```json 圍籬（含未閉合）、前後多餘文字、單引號、字串內裸換行等常見
   畸形；抽出**第一個平衡的 JSON 物件**（字串內大括號不誤判）。敘述文字取 `dissolution`
   （M2 prompt 契約欄位），退化接受 `object`；**必須為非空字串**（正規化空白、截斷 ≤200 字）
   ⇒ 否則 `parse_failed`。`confidence` **一律強制 `1.0`**（契約 §3.4：**不由 LLM 產生**）。
9. **隱私**：日誌**不得**輸出 prompt 或記憶原文（只記長度／狀態／thread_id／離散枚舉）。
10. **回傳**：成功 ⇒ `status="consolidated"`、`fact=<正規化後的 fact>`、`llm_calls=1`。

與票面描述的差異（以 grep 到的實際欄位為準）
──────────────────────────────────────────
- **D-1**：票面寫終態集合 `{completed, dissolved}`；**實際值域**是 M2 `TERMINAL_STATUSES`
  ＝ M1 `life_threads.py:74` ＝ `("completed", "abandoned")`（`dissolved` **不在**
  `STATUS_VALUES` 內，是「已溶解」的**動作**不是狀態值）。⇒ 本模組採
  `TERMINAL_TARGET_STATUSES = ("completed", "abandoned")`。
- **D-2**：票面寫 prompt 在 `extra_metadata["reflection_prompt"]`；**實際**是
  `DissolutionEvaluation.reflection_prompt` 頂層欄位（`life_thread_dissolution.py:125`）。
- **D-3**：票面寫解析後要求 `subject`／`predicate`／`object` 皆非空；**實際** M2 prompt 的
  輸出契約是 `{"dissolution", "meaning_kind"}`（`:130-133`），而 `subject`／`predicate` 是
  契約 §3.4 **固定常數**（`agent_id` / `"life_thread_dissolved"`），**不得由 LLM 產生**
  ⇒ 本模組只從 LLM 取敘述文字（`dissolution`，退化接受 `object`），其餘兩欄自行組裝。
- **D-4**：票面寫「各截斷至 ≤200 字」；實際 `Fact.validate`（`models.py:114-117`）另有
  **`subject ≤120` / `object ≤200`** 的 schema gate ⇒ 本模組對 `subject` 額外夾在 120 字，
  使產出的 fact **恆可被真實 writer 接受**（契約 §3.2 對 LLM 輸出寫 400 字元，但 SAGE
  `object` 上限是 200 ⇒ 取 200 為交集上界，否則整筆會被 writer 拒絕）。
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("soul_os.soul.life_thread_dissolution_exec")


# ──────────────────────────────────────────────────────────────
# §1 常數（全部具名，不得寫裸字面量）
# ──────────────────────────────────────────────────────────────

#: 契約 §3.6：每次沉澱的 LLM 輸出上限（單一 JSON 物件）。
CONSOLIDATION_MAX_TOKENS = 300

#: 單次 LLM 呼叫的逾時秒數（`asyncio.wait_for`）。
#:
#: 120 秒的依據（`docs/LIFE-THREAD-M2-EXEC-COST-CALIBRATION.md`；2026-09-17 實測 n=1，
#: 模型 `deepseek-v4.1-flash`）：單次沉澱 **實測 26,335.6 ms**（completion_tokens=1790、
#: finish_reason=stop）⇒ 舊值 20 秒會在**付費請求已送出後**才 abort：錢照樣花掉、
#: 結果卻被丟棄（雙重浪費）。120 秒 ≈ 實測值的 4.6 倍，留給更長敘述與模型降速的餘裕。
#: **此值不得改回 20**（下游有 `>= 60` 的護欄測試釘死）。
CONSOLIDATION_TIMEOUT_SECONDS = 120

#: 每 agent 每日沉澱呼叫上限（整數計數，**非評分**）。
#:
#: 3 ＝ 契約 §3.6 `DISSOLVE_MAX_PER_DAY = 3`（`docs/LIFE-THREAD-ENGINE-CONTRACT.md:309`
#: ＝ M1 `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`），與決策層
#: `src/soul/life_thread_dissolution.py:77` **逐字對齊**。**執行層不得比契約寬鬆**：
#: 舊值 24 是契約的 8 倍，屬「寬鬆防線」＝每日最多白花 8 倍推理成本。
MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY = 3

#: 全行程每日沉澱呼叫上限（同上）。
MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY = 200

#: §2.5／M2 `TERMINAL_STATUSES` 逐字相同的終態值域（見 docstring D-1）。
TERMINAL_TARGET_STATUSES: Tuple[str, ...] = ("completed", "abandoned")

#: 契約 §3.4 的 Fact 常數欄位（**不得由 LLM 產生**）。
FACT_PREDICATE = "life_thread_dissolved"
FACT_SOURCE = "inference"
FACT_ORIGIN = "lived_experience"
FACT_CONFIDENCE = 1.0

#: 敘述文字欄位名（M2 prompt 契約欄位優先；`object` 為退化容忍）。
NARRATIVE_KEYS: Tuple[str, ...] = ("dissolution", "object")

#: §3.2 `meaning_kind` 6 值離散枚舉（非法值降級為 `none`）。
MEANING_KINDS: Tuple[str, ...] = (
    "competence",
    "connection",
    "loss",
    "relief",
    "discovery",
    "none",
)
MEANING_KIND_FALLBACK = "none"

#: 文字欄位截斷上界（見 docstring D-4）。
MAX_FIELD_CHARS = 200
SUBJECT_MAX_CHARS = 120

#: 結果 status（離散字串，**不是**分數）。
STATUS_CONSOLIDATED = "consolidated"
STATUS_SKIPPED_NOT_TERMINAL = "skipped_not_terminal"
STATUS_SKIPPED_NO_LLM = "skipped_no_llm"
STATUS_SKIPPED_NO_WRITER = "skipped_no_writer"
STATUS_SKIPPED_NO_PROMPT = "skipped_no_prompt"
STATUS_SKIPPED_BUDGET = "skipped_budget"
STATUS_SKIPPED_DUPLICATE = "skipped_duplicate"
STATUS_INVALID_INPUT = "invalid_input"
STATUS_LLM_FAILED = "llm_failed"
STATUS_PARSE_FAILED = "parse_failed"
STATUS_WRITE_FAILED = "write_failed"

#: 日誌前綴（本模組唯一副作用之一）。
_LOG_PREFIX = "[LifeThreadDissolutionExec] "

#: ``` 圍籬（含語言標註）的剝除樣式（閉合與未閉合皆適用）。
_CODE_FENCE_RE = re.compile(r"```[A-Za-z0-9_+\-]*")

#: 空白正規化（含全形空白）。
_WHITESPACE_RE = re.compile(r"[ \t\r\n\u3000]+")

__all__ = [
    "CONSOLIDATION_MAX_TOKENS",
    "CONSOLIDATION_TIMEOUT_SECONDS",
    "MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY",
    "MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY",
    "TERMINAL_TARGET_STATUSES",
    "FACT_PREDICATE",
    "FACT_SOURCE",
    "FACT_ORIGIN",
    "FACT_CONFIDENCE",
    "MAX_FIELD_CHARS",
    "SUBJECT_MAX_CHARS",
    "STATUS_CONSOLIDATED",
    "STATUS_SKIPPED_NOT_TERMINAL",
    "STATUS_SKIPPED_NO_LLM",
    "STATUS_SKIPPED_NO_WRITER",
    "STATUS_SKIPPED_NO_PROMPT",
    "STATUS_SKIPPED_BUDGET",
    "STATUS_SKIPPED_DUPLICATE",
    "STATUS_INVALID_INPUT",
    "STATUS_LLM_FAILED",
    "STATUS_PARSE_FAILED",
    "STATUS_WRITE_FAILED",
    "ConsolidationBudget",
    "ConsolidationResult",
    "consolidate_terminal_thread",
]


# ──────────────────────────────────────────────────────────────
# §2 內部工具（純函式、永不 raise、永不 mutate 入參）
# ──────────────────────────────────────────────────────────────


def _field(source: Any, name: str) -> Any:
    """duck-typing 取值：`Mapping` 走 key，其餘走 attribute；失敗回 `None`（不 raise）。"""
    if isinstance(source, Mapping):
        try:
            return source.get(name)
        except Exception:  # pragma: no cover - defensive（畸形 Mapping）
            return None
    return getattr(source, name, None)


def _coerce_limit(value: Any, default: int) -> int:
    """預算上限正規化：非 bool 的非負整數才採用；其餘（含負數）回 `default`。"""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < 0:
        return default
    return value


def _default_now() -> datetime:
    """預設時鐘（UTC aware）。"""
    return datetime.now(timezone.utc)


def _as_aware_utc(moment: Any) -> Optional[datetime]:
    """時間輸入正規化為 **aware UTC**；不合格回 `None`（不 raise）。"""
    if isinstance(moment, bool):
        return None
    if isinstance(moment, datetime):
        if moment.tzinfo is None:
            return moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)
    if isinstance(moment, (int, float)):
        try:
            return datetime.fromtimestamp(moment, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _utc_day_key(moment: Any) -> str:
    """預算的「日」鍵（UTC `YYYY-MM-DD`）；無法解析時退回 wall clock（fail-safe）。"""
    aware = _as_aware_utc(moment)
    if aware is None:
        aware = _default_now()
    return aware.date().isoformat()


def _normalize_text(value: str, max_chars: int) -> str:
    """空白正規化（多空白／換行／全形空白 ⇒ 單一空格）後截斷至 `max_chars`。"""
    collapsed = _WHITESPACE_RE.sub(" ", value).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[:max_chars].strip()


def _detect_fence_markers(text: str) -> int:
    """回傳 ``` 標記數量（**只用於回報長度／計數，不記錄內容**）。"""
    return text.count("```")


# ──────────────────────────────────────────────────────────────
# §3 預算（行程級記憶體計數器，時鐘可注入）
# ──────────────────────────────────────────────────────────────


class ConsolidationBudget:
    """行程級記憶體預算計數器（**per-agent 每日** ＋ **全行程每日** 兩道）。

    設計
    ────
    - 時鐘可注入（`now` 回傳 `datetime` / epoch 秒；預設 UTC wall clock），
      跨「日」時自動歸零（`_utc_day_key`）。
    - 上限可注入（`per_agent_limit` / `global_limit`），讓測試不必跑滿 200 次真呼叫。
    - **非執行緒安全**（純記憶體計數、單一 event loop 內無 await 點）；跨行程不共享
      ⇒ 崩潰後計數歸零（屬保守方向：不會多花錢）。
    - 只管「**嘗試呼叫**」次數，不管成敗（呼叫已發生即計數）。
    """

    def __init__(
        self,
        *,
        now: Optional[Callable[[], Any]] = None,
        per_agent_limit: int = MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY,
        global_limit: int = MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY,
    ) -> None:
        self._now: Callable[[], Any] = now if callable(now) else _default_now
        self._per_agent_limit = _coerce_limit(
            per_agent_limit, MAX_CONSOLIDATION_CALLS_PER_AGENT_PER_DAY
        )
        self._global_limit = _coerce_limit(
            global_limit, MAX_CONSOLIDATION_CALLS_GLOBAL_PER_DAY
        )
        self._day: Optional[str] = None
        self._global_count = 0
        self._per_agent: Dict[str, int] = {}

    # ── 唯讀檢視（測試／觀測用；不改變狀態） ──
    @property
    def per_agent_limit(self) -> int:
        return self._per_agent_limit

    @property
    def global_limit(self) -> int:
        return self._global_limit

    @property
    def global_count(self) -> int:
        return self._global_count

    @property
    def day(self) -> Optional[str]:
        return self._day

    def count_for(self, agent_id: str) -> int:
        key = agent_id if isinstance(agent_id, str) else ""
        return self._per_agent.get(key, 0)

    # ── 主介面 ──
    def try_consume(self, agent_id: str) -> bool:
        """全域與 per-agent 皆未超限才回 `True` 並計數（任一超限即 `False`，不計數）。"""
        key = agent_id if isinstance(agent_id, str) else ""
        try:
            moment = self._now()
        except Exception:  # pragma: no cover - defensive（畸形時鐘）
            moment = None
        day = _utc_day_key(moment)
        if day != self._day:
            self._day = day
            self._per_agent.clear()
            self._global_count = 0
        if self._global_count >= self._global_limit:
            return False
        if self._per_agent.get(key, 0) >= self._per_agent_limit:
            return False
        self._per_agent[key] = self._per_agent.get(key, 0) + 1
        self._global_count += 1
        return True


_DEFAULT_BUDGET: Optional[ConsolidationBudget] = None


def _default_budget() -> ConsolidationBudget:
    """行程級預設預算（延遲建立；`budget=None` 時使用，成本控制預設開啟）。"""
    global _DEFAULT_BUDGET
    if _DEFAULT_BUDGET is None:
        _DEFAULT_BUDGET = ConsolidationBudget()
    return _DEFAULT_BUDGET


def _reset_default_budget() -> None:
    """**測試專用**私有 helper：丟棄行程級預設預算（生產路徑不呼叫）。"""
    global _DEFAULT_BUDGET
    _DEFAULT_BUDGET = None


# ──────────────────────────────────────────────────────────────
# §4 冪等 sentinel（行程內記憶體；只在**成功**時登記）
# ──────────────────────────────────────────────────────────────

_CONSOLIDATED: set = set()


def _dedupe_key(thread_id: str, terminal_status: str) -> Tuple[str, str]:
    """冪等鍵：`(thread_id, terminal_status)`（契約 §3.3 L1 的行程內版本）。"""
    return (thread_id, terminal_status)


def _register_consolidated(thread_id: str, terminal_status: str) -> None:
    _CONSOLIDATED.add(_dedupe_key(thread_id, terminal_status))


def _is_consolidated(thread_id: str, terminal_status: str) -> bool:
    return _dedupe_key(thread_id, terminal_status) in _CONSOLIDATED


def _clear_consolidated_registry() -> None:
    """**測試專用**私有 helper：清空行程內 sentinel（生產路徑不呼叫）。"""
    _CONSOLIDATED.clear()


# ──────────────────────────────────────────────────────────────
# §5 解析（容忍畸形輸出；永不 raise）
# ──────────────────────────────────────────────────────────────


def _strip_code_fences(text: str) -> str:
    """剝除 ``` 圍籬標記（閉合／未閉合皆可），保留圍籬內文。"""
    return _CODE_FENCE_RE.sub("", text)


def _first_balanced_object_span(text: str) -> Optional[Tuple[int, int]]:
    """第一個**平衡**的 `{...}` 區間（字串內的大括號不計；未平衡回 `None`）。"""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in ("\"", "'"):
            in_string = True
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return (start, index + 1)
    return None


def _loads_tolerant(candidate: str) -> Any:
    """JSON（`strict=False`，容忍字串內裸換行等控制字元）→ `ast.literal_eval`（容忍單引號）。

    `strict=False` 是刻意的：LLM 常在 JSON 字串內直接放**未轉義的換行**，
    嚴格模式會整筆判廢；放寬僅限「字串內控制字元」，其餘語法仍須合法。
    失敗回 `None`（不 raise）。
    """
    try:
        return json.loads(candidate, strict=False)
    except (ValueError, TypeError):
        pass
    try:
        return ast.literal_eval(candidate)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return None


def _extract_mapping(raw: Any) -> Optional[Mapping]:
    """把 LLM 原始回應抽成 `Mapping`；失敗回 `None`（不 raise）。"""
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            return None
    if not isinstance(raw, str):
        return None
    cleaned = _strip_code_fences(raw).strip()
    if not cleaned:
        return None
    candidates = [cleaned]
    span = _first_balanced_object_span(cleaned)
    if span is not None:
        candidates.append(cleaned[span[0]:span[1]])
    for candidate in candidates:
        parsed = _loads_tolerant(candidate)
        if isinstance(parsed, Mapping):
            return parsed
    return None


def _narrative_from(mapping: Mapping) -> Optional[str]:
    """敘述文字：`dissolution` 優先，退化接受 `object`；非空字串才合格。"""
    for key in NARRATIVE_KEYS:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return _normalize_text(value, MAX_FIELD_CHARS)
    return None


def _meaning_kind_from(mapping: Mapping) -> str:
    """`meaning_kind`：合法 6 值（正規化大小寫）才採用，其餘降級 `none`（契約 §3.2）。"""
    value = mapping.get("meaning_kind")
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in MEANING_KINDS:
            return candidate
    return MEANING_KIND_FALLBACK


def _parse_llm_output(raw: Any) -> Optional[Tuple[str, str]]:
    """回傳 `(敘述文字, meaning_kind)`；解析失敗回 `None`（呼叫端轉 `parse_failed`）。"""
    mapping = _extract_mapping(raw)
    if mapping is None:
        return None
    narrative = _narrative_from(mapping)
    if narrative is None:
        return None
    return (narrative, _meaning_kind_from(mapping))


# ──────────────────────────────────────────────────────────────
# §6 Fact 組裝（契約 §3.4 逐欄；**不由 LLM 產生** subject／predicate／confidence）
# ──────────────────────────────────────────────────────────────


def _build_fact(agent_id: str, narrative: str, moment: datetime) -> Optional[Dict[str, Any]]:
    """組出可被 SAGE `MemoryWriter.add_fact` 接受的 `Fact` kwargs（失敗回 `None`）。

    - `subject` = `agent_id`（≤120 字，對齊 `Fact.validate`）
    - `predicate` = `FACT_PREDICATE`（固定常數）
    - `object` = 沉澱敘述（≤200 字，對齊 `Fact.validate`）
    - `source` = `"inference"`（角色自身詮釋）／`origin` = `"lived_experience"`（親身經歷）
    - `confidence` = `1.0`（**強制常數**）／`timestamp` = `valid_from` = 溶解時刻
    - `invalidated_at` = `None`；**不填** `weight`／`check_after_ts`／`origin_type`／`status`
    """
    subject = _normalize_text(agent_id, SUBJECT_MAX_CHARS)
    predicate = FACT_PREDICATE
    object_text = _normalize_text(narrative, MAX_FIELD_CHARS)
    if not subject or not predicate or not object_text:
        return None
    epoch = moment.timestamp()
    return {
        "subject": subject,
        "predicate": predicate,
        "object": object_text,
        "source": FACT_SOURCE,
        "origin": FACT_ORIGIN,
        "confidence": FACT_CONFIDENCE,
        "timestamp": epoch,
        "valid_from": epoch,
        "invalidated_at": None,
    }


# ──────────────────────────────────────────────────────────────
# §7 閘門／取值輔助
# ──────────────────────────────────────────────────────────────


def _target_status_value(evaluation: Any) -> Optional[str]:
    """取 `evaluation.target_status` 的字串值（容忍 `Enum`／`str`）；無法判定回 `None`。"""
    target = _field(evaluation, "target_status")
    if isinstance(target, Enum):
        target = target.value
    if isinstance(target, str):
        normalized = target.strip().lower()
        return normalized or None
    return None


def _is_terminal_mutation(evaluation: Any) -> bool:
    """fail-safe 終態閘門：終態值域 **且** `should_mutate is True`（嚴格）才放行。"""
    if _field(evaluation, "should_mutate") is not True:
        return False
    target = _target_status_value(evaluation)
    if target is None:
        return False
    return target in TERMINAL_TARGET_STATUSES


def _extract_prompt(evaluation: Any) -> Optional[str]:
    """契約 §3.2 的 `reflection_prompt`（M2 已組好；本模組**不重組**）。"""
    prompt = _field(evaluation, "reflection_prompt")
    if isinstance(prompt, str) and prompt.strip():
        return prompt
    return None


def _extract_thread_id(thread: Any, evaluation: Any) -> str:
    """線頭 id：線頭優先，退而取裁決上的同名值；無法判定回 `""`（**只用於識別**）。"""
    for source in (thread, evaluation):
        value = _field(source, "thread_id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _resolve_moment(now: Any) -> datetime:
    """溶解時刻（`timestamp`／`valid_from` 的來源）；不合格輸入回 wall clock（不 raise）。"""
    aware = _as_aware_utc(now)
    if aware is None:
        if now is not None:
            logger.warning("%signoring non-datetime now=%r (型別不合法)", _LOG_PREFIX, type(now))
        return _default_now()
    return aware


# ──────────────────────────────────────────────────────────────
# §8 結果型別
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ConsolidationResult:
    """單次沉澱執行的結果（frozen：結果不可就地改寫）。

    - `thread_id`：受處理線頭 id（無法判定時為 `""`）
    - `status`：`STATUS_*` 之一（離散字串，**不是**分數）
    - `fact`：成功時的 §3.4 Fact kwargs；其餘為 `None`
    - `llm_calls`：**只可能是 0 或 1**（0＝呼叫前即擋下；1＝確實嘗試過一次呼叫）
    - `reason`：短碼（失敗時為例外**類名**，**不含**任何 prompt／記憶原文）
    """

    thread_id: str
    status: str
    fact: Optional[Dict[str, Any]]
    llm_calls: int
    reason: str


def _result(
    thread_id: str,
    status: str,
    fact: Optional[Dict[str, Any]] = None,
    llm_calls: int = 0,
    reason: str = "",
) -> ConsolidationResult:
    """結果工廠（唯一組裝點）。"""
    return ConsolidationResult(
        thread_id=thread_id,
        status=status,
        fact=fact,
        llm_calls=llm_calls,
        reason=reason,
    )


# ──────────────────────────────────────────────────────────────
# §9 主函式（單一 LLM 呼叫點；永不 raise，CancelledError 除外）
# ──────────────────────────────────────────────────────────────


async def _invoke_llm_once(llm_call: Callable[..., Any], prompt: str) -> Any:
    """**本模組唯一的 `llm_call` 呼叫點**（無重試、無迴圈）。

    回傳 awaitable ⇒ await；否則直接回傳其值（支援同步 fake）。
    """
    value = llm_call(prompt, max_tokens=CONSOLIDATION_MAX_TOKENS)
    if inspect.isawaitable(value):
        value = await value
    return value


async def consolidate_terminal_thread(
    *,
    agent_id: str,
    thread: Mapping[str, Any],
    evaluation: Any,
    llm_call: Optional[Callable[..., Any]] = None,
    fact_writer: Optional[Callable[[Mapping[str, Any]], Any]] = None,
    budget: Optional[ConsolidationBudget] = None,
    now: Optional[datetime] = None,
) -> ConsolidationResult:
    """把一個**已裁定為終態**的線頭沉澱一次成 SAGE `Fact` 並落地（fail-open、永不 raise）。

    決策序（任一階段命中即回傳；全部 0 呼叫路徑都在呼叫 LLM 之前）
    ──────────────────────────────────────────────────────────
    1. 終態閘門（fail-safe）⇒ `skipped_not_terminal`
    2. 入參識別（`agent_id` / `thread_id` 非空字串）⇒ `invalid_input`
    3. `llm_call is None` ⇒ `skipped_no_llm`
    4. `fact_writer is None` ⇒ `skipped_no_writer`
    5. `reflection_prompt` 缺失／空白 ⇒ `skipped_no_prompt`
    6. 行程內已成功沉澱過 ⇒ `skipped_duplicate`
    7. `budget.try_consume(agent_id)` 失敗 ⇒ `skipped_budget`（WARNING，不拋例外）
    8. 單次 LLM 呼叫（`asyncio.wait_for`）⇒ 例外／逾時 ⇒ `llm_failed`
    9. 解析（容忍畸形）⇒ 失敗 ⇒ `parse_failed`
    10. Fact 組裝 ＋ `fact_writer` 落地 ⇒ 例外／空 fact_id ⇒ `write_failed`
    11. 成功 ⇒ 登記 sentinel ⇒ `consolidated`（`llm_calls=1`）
    """
    thread_id = _extract_thread_id(thread, evaluation)

    # 1. 終態閘門（唯一進入呼叫路徑的前提；無法判定者一律擋下）
    if not _is_terminal_mutation(evaluation):
        target = _target_status_value(evaluation)
        reason = "target_status=%s" % (target,) if target is not None else "undetermined"
        if _field(evaluation, "should_mutate") is not True:
            reason = "should_mutate_not_true"
        logger.info(
            "%sskip thread_id=%s status=%s reason=%s",
            _LOG_PREFIX,
            thread_id,
            STATUS_SKIPPED_NOT_TERMINAL,
            reason,
        )
        return _result(thread_id, STATUS_SKIPPED_NOT_TERMINAL, None, 0, reason)

    # 2. 入參識別（無法識別就無法履行 §3.3 冪等與 §3.4 的 subject 契約）
    if not (isinstance(agent_id, str) and agent_id.strip()):
        logger.warning("%sskip thread_id=%s invalid agent_id", _LOG_PREFIX, thread_id)
        return _result(
            thread_id, STATUS_INVALID_INPUT, None, 0, "agent_id_not_non_empty_str"
        )
    if not thread_id:
        logger.warning("%sskip invalid thread_id (empty)", _LOG_PREFIX)
        return _result(
            thread_id, STATUS_INVALID_INPUT, None, 0, "thread_id_not_non_empty_str"
        )
    terminal_status = _target_status_value(evaluation)

    # 3. LLM 注入缺席（fail-closed，0 呼叫）
    if llm_call is None:
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_NO_LLM
        )
        return _result(
            thread_id, STATUS_SKIPPED_NO_LLM, None, 0, "llm_call_not_injected"
        )

    # 4. 寫入器注入缺席（**必須在呼叫 LLM 之前**判定，不得白花一次推理）
    if fact_writer is None:
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_NO_WRITER
        )
        return _result(
            thread_id, STATUS_SKIPPED_NO_WRITER, None, 0, "fact_writer_not_injected"
        )

    # 5. prompt 由 M2 提供（本模組不自行拼 prompt）
    prompt = _extract_prompt(evaluation)
    if prompt is None:
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_NO_PROMPT
        )
        return _result(
            thread_id, STATUS_SKIPPED_NO_PROMPT, None, 0, "reflection_prompt_missing"
        )

    # 6. 冪等（行程內；**不消耗預算**、0 呼叫）
    key_status = terminal_status if terminal_status is not None else ""
    if _is_consolidated(thread_id, key_status):
        logger.info(
            "%sskip thread_id=%s status=%s", _LOG_PREFIX, thread_id, STATUS_SKIPPED_DUPLICATE
        )
        return _result(
            thread_id, STATUS_SKIPPED_DUPLICATE, None, 0, "already_consolidated"
        )

    # 7. 預算（呼叫前；失敗只 WARNING，不拋例外）
    active_budget = budget if budget is not None else _default_budget()
    try:
        allowed = bool(active_budget.try_consume(agent_id))
    except Exception as exc:  # pragma: no cover - defensive（畸形預算物件）
        logger.warning(
            "%sbudget error thread_id=%s type=%s", _LOG_PREFIX, thread_id, type(exc).__name__
        )
        allowed = False
    if not allowed:
        logger.warning(
            "%sbudget exhausted thread_id=%s status=%s",
            _LOG_PREFIX,
            thread_id,
            STATUS_SKIPPED_BUDGET,
        )
        return _result(thread_id, STATUS_SKIPPED_BUDGET, None, 0, "budget_exhausted")

    moment = _resolve_moment(now)

    # 8. 單次 LLM 呼叫（唯一的呼叫點；逾時／例外皆降級）
    try:
        raw = await asyncio.wait_for(
            _invoke_llm_once(llm_call, prompt),
            timeout=CONSOLIDATION_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "%sllm timeout thread_id=%s prompt_chars=%d timeout_s=%s",
            _LOG_PREFIX,
            thread_id,
            len(prompt),
            CONSOLIDATION_TIMEOUT_SECONDS,
        )
        return _result(thread_id, STATUS_LLM_FAILED, None, 1, "timeout")
    except Exception as exc:
        logger.warning(
            "%sllm error thread_id=%s prompt_chars=%d type=%s",
            _LOG_PREFIX,
            thread_id,
            len(prompt),
            type(exc).__name__,
        )
        return _result(thread_id, STATUS_LLM_FAILED, None, 1, type(exc).__name__)

    # 9. 解析（容忍 ```json 圍籬／前後贅字／單引號）
    parsed = _parse_llm_output(raw)
    if parsed is None:
        logger.warning(
            "%sparse failed thread_id=%s raw_type=%s raw_chars=%d fences=%d",
            _LOG_PREFIX,
            thread_id,
            type(raw).__name__,
            len(raw) if isinstance(raw, (str, bytes)) else 0,
            _detect_fence_markers(raw) if isinstance(raw, str) else 0,
        )
        return _result(thread_id, STATUS_PARSE_FAILED, None, 1, "narrative_not_found")
    narrative, meaning_kind = parsed

    # 10. Fact 組裝（§3.4 逐欄）
    fact = _build_fact(agent_id, narrative, moment)
    if fact is None:
        logger.warning("%sfact assembly failed thread_id=%s", _LOG_PREFIX, thread_id)
        return _result(thread_id, STATUS_PARSE_FAILED, None, 1, "empty_required_field")

    # 11. 落地（寫入失敗隔離：不重試、不拋例外）
    try:
        written = fact_writer(dict(fact))
        if inspect.isawaitable(written):
            written = await written
    except Exception as exc:
        logger.warning(
            "%swrite failed thread_id=%s type=%s", _LOG_PREFIX, thread_id, type(exc).__name__
        )
        return _result(thread_id, STATUS_WRITE_FAILED, None, 1, type(exc).__name__)
    if isinstance(written, str) and not written.strip():
        logger.warning("%swrite failed thread_id=%s reason=empty_fact_id", _LOG_PREFIX, thread_id)
        return _result(thread_id, STATUS_WRITE_FAILED, None, 1, "empty_fact_id")

    # 12. 成功 ⇒ 登記行程內 sentinel（**只在成功時**，保留 §3.2 重試語意）
    _register_consolidated(thread_id, key_status)
    logger.info(
        "%sconsolidated thread_id=%s prompt_chars=%d narrative_chars=%d meaning_kind=%s",
        _LOG_PREFIX,
        thread_id,
        len(prompt),
        len(fact["object"]),
        meaning_kind,
    )
    return _result(thread_id, STATUS_CONSOLIDATED, fact, 1, "ok")
