"""src/inner_life/tension_thinking.py — 張力驅動思考層（**沙盒；不燒 LLM**）。

CAMPAIGN-B-P2 §3.1（工單：logs/CAMPAIGN-B-P2-D1-THINKING-LOOP-SANDBOX-WORK-ORDER.md）

這一層做什麼
------------
把 Phase 1 的「張力快照」翻成**可交給思考器的提詞**，並提供一個**決定性**的
思考器抽象，讓 D1（資訊缺口／時間態衰減）張力**真正驅動行為**：

    ThinkRequest → build_thinking_prompt() → Thinker.think() → ThinkOutcome

「部分消解」的具體語意
--------------------
:class:`DeterministicMockThinker` **每次思考只消解 1 個缺口**（主大腦拍板）：

- ``info_gap_count <= 0`` ⇒ ``decided=True``／``partial_resolution=1.0``／
  ``remaining_gap_count=0``／``reason="no_open_info_gap"``。
- ``info_gap_count > 0`` ⇒ ``partial_resolution = 1/info_gap_count``／
  ``remaining_gap_count = info_gap_count - 1``／``decided = (remaining == 0)``／
  ``reason="resolved_one_info_gap"``。

⇒ 缺口數 > 1 時**一次只消解一個** ⇒ 張力被分次消解 ⇒ 驅動「持續思考」，
而不是一步到位。

硬性禁令（**結構性保證**，不靠註解；守衛 P2-T15 以 AST 把關）
-----------------------------------------------------------
- **不得** import：``src.llm``（含 ``src.llm.proxy``）、``httpx``、``requests``、
  ``socket``、``subprocess``、``urllib``、``asyncio``。
- 本模組**本身**不做任何網路呼叫、**不得**讀寫任何檔案（純函式層）。
  ⚠️ Phase 3（``LLMThinker``）的網路呼叫發生在**注入的** proxy 內部
  ⇒ 本模組仍**零 import** ``src.llm``（守衛 P2-T15／P4-T12 以 AST 把關）。

紀律
----
- **純函式、決定性**：同一輸入 ⇒ 逐位元相同的提示詞與 outcome。
  🔴 不得引入 ``id()``／``time()``／``hash()``／物件雜湊等非確定性成分。
- **零 LLM 呼叫（Phase 2 的 Mock 路徑）**：:class:`DeterministicMockThinker` 與
  :func:`build_thinking_prompt` 仍**完全不碰 LLM**。
- **Phase 3 接線面**：:class:`LLMThinker` 只經由**注入的** proxy 呼叫
  ``LLMProxy.generate_text`` ⇒ **不 import** ``src.llm``、不自建 HTTP 客戶端、
  不自帶 API key；任何失敗（預算用盡／例外／空回應／不可解析／幻覺數字）
  一律 **fail-closed**（不消解、不拋例外）。
- **零檔案 I/O**：提示詞建構只看傳入的 :class:`~src.inner_life.tension_closure_loop.ThinkRequest`。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Protocol, Tuple, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover — 僅供型別檢查（執行期不匯入 ⇒ 無循環相依）
    from src.inner_life.tension_closure_loop import ThinkRequest


#: 資料列摘要時**只准**出現的「識別子欄位」白名單（工單 §3.1）。
#:
#: 🔴 白名單之外的欄位（``title``／``contradiction``／``content``…）**一律不得**
#: 進提示詞 —— 否則等於把 source_rows 原文整段貼入（無界膨脹；守衛 P2-T3）。
SOURCE_ROW_IDENTIFYING_KEYS: Tuple[str, ...] = (
    "factor",
    "thread_id",
    "created_at",
    "updated_at",
    "gap_kind",
    "question",
    "kind",
)

#: 提示詞內最多列出的資料列數（超出截斷並標註 ``(+N more)``）。
MAX_SOURCE_ROWS_IN_PROMPT = 20

#: 單一識別子欄位值的最大字元數（避免單列無界膨脹）。
MAX_FIELD_CHARS = 120

#: 提示詞五段模板的段落標題（固定；同一輸入必逐位元相同）。
PROMPT_SECTIONS: Tuple[str, ...] = ("ROLE", "CONTEXT", "FACTORS", "GAPS", "ASK")


# ── 資料模型 ─────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ThinkOutcome:
    """一次思考的結果（**純資料**）。

    partial_resolution : float  0.0～1.0（本回合消解的比例）
    remaining_gap_count : int    思考後仍開著的資訊缺口數
    decided : bool               是否已「想完」（缺口全消）
    reason : str                 機器可讀的原因碼
    """

    partial_resolution: float
    remaining_gap_count: int
    decided: bool
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partial_resolution": float(self.partial_resolution),
            "remaining_gap_count": int(self.remaining_gap_count),
            "decided": bool(self.decided),
            "reason": str(self.reason),
        }


# ── 提示詞建構 ───────────────────────────────────────────────────────────
def _summarize_source_row(row: Dict[str, Any]) -> str:
    """一列資料的可稽核**識別子欄位**摘要（單行；無長度無界）。

    - 只取 :data:`SOURCE_ROW_IDENTIFYING_KEYS` 白名單內、**該列實際存在**的鍵。
    - 值為 ``None``／空字串者略過（與「不存在」同義）。
    - 換行折成空白；超長值截斷（附 ``…``）。
    """
    parts: List[str] = []
    for key in SOURCE_ROW_IDENTIFYING_KEYS:
        if key not in row:
            continue
        value = row.get(key)
        if value is None:
            continue
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        if not text:
            continue
        if len(text) > MAX_FIELD_CHARS:
            text = text[:MAX_FIELD_CHARS] + "…"
        parts.append(f"{key}={text}")
    return " ".join(parts) if parts else "(no identifying fields)"


def build_thinking_prompt(req: "ThinkRequest") -> str:
    """由 ``ThinkRequest`` 建構「張力驅動思考」提示詞（**純函式、決定性**）。

    格式：固定五段（``ROLE``／``CONTEXT``／``FACTORS``／``GAPS``／``ASK``）。
    必含：``agent_id``、``drive_code``、``temporal_state``、``decay_bucket``、
    ``info_gap_count``、``unresolved_count``，以及 ``source_rows`` 的
    **可稽核識別欄位摘要**（每列一行；最多 20 列，超出標註 ``(+N more)``）。

    🔴 **不得**把 source_rows 原文整段貼入（守衛 P2-T3）。
    🔴 **不得**呼叫 LLM、不得讀檔、不得連網；**不得**含任何隨機／時間／雜湊成分
    （同一輸入必逐位元相同；守衛 P2-T1）。
    """
    lines: List[str] = []

    lines.append("ROLE")
    lines.append(
        f"你是 {req.agent_id} 的內在思考層；只依據下方事實推理，不得補造外部事實。"
    )
    lines.append("")

    lines.append("CONTEXT")
    lines.append(f"- agent_id: {req.agent_id}")
    lines.append(f"- drive_code: {req.drive_code}")
    lines.append(f"- temporal_state: {req.temporal_state}")
    lines.append(f"- decay_bucket: {req.decay_bucket}")
    lines.append("")

    lines.append("FACTORS")
    lines.append(f"- info_gap_count: {int(req.info_gap_count)}")
    lines.append(f"- unresolved_count: {int(req.unresolved_count)}")
    lines.append("")

    lines.append("GAPS")
    rows = list(req.source_rows or [])
    shown = rows[:MAX_SOURCE_ROWS_IN_PROMPT]
    if shown:
        for row in shown:
            lines.append(f"- {_summarize_source_row(row)}")
    else:
        lines.append("- (none)")
    extra = len(rows) - len(shown)
    if extra > 0:
        lines.append(f"- (+{extra} more)")
    lines.append("")

    lines.append("ASK")
    lines.append("請針對上述資訊缺口提出下一步思考方向；每次思考只消解 1 個缺口。")

    return "\n".join(lines)


# ── 思考器抽象 ───────────────────────────────────────────────────────────
@runtime_checkable
class Thinker(Protocol):
    """思考器抽象（**Protocol**，非 ABC —— 任何有 ``think`` 的物件皆可注入）。

    ``think`` **不得**自行呼叫 LLM（Phase 2 的沙盒思考器是決定性的）。
    """

    def think(self, req: "ThinkRequest", prompt: str) -> ThinkOutcome:  # pragma: no cover
        ...


class DeterministicMockThinker:
    """**不燒 LLM** 的決定性思考器（預設）。

    規則（主大腦拍板）::

        info_gap_count <= 0  ⇒ decided=True, partial_resolution=1.0,
                              remaining_gap_count=0, reason="no_open_info_gap"
        info_gap_count  > 0  ⇒ **每次思考只消解 1 個缺口**：
                              partial_resolution = 1/info_gap_count,
                              remaining_gap_count = info_gap_count - 1,
                              decided = (remaining_gap_count == 0),
                              reason = "resolved_one_info_gap"

    🔴 同一輸入兩次 :meth:`think` 必須**完全相同**（守衛 P2-T7）—— 不得引入
    ``id()``／``time()``／``hash()``／物件雜湊等非確定性成分。
    """

    def think(self, req: "ThinkRequest", prompt: str) -> ThinkOutcome:
        gap_count = int(req.info_gap_count)
        if gap_count <= 0:
            return ThinkOutcome(
                partial_resolution=1.0,
                remaining_gap_count=0,
                decided=True,
                reason="no_open_info_gap",
            )
        remaining = gap_count - 1
        return ThinkOutcome(
            partial_resolution=1.0 / gap_count,
            remaining_gap_count=remaining,
            decided=(remaining == 0),
            reason="resolved_one_info_gap",
        )


class NullThinker:
    """不消解（**對照組**）：``partial_resolution=0.0``、
    ``remaining_gap_count=req.info_gap_count``、``decided=False``、
    ``reason="null_thinker"``。
    """

    def think(self, req: "ThinkRequest", prompt: str) -> ThinkOutcome:
        gap_count = int(req.info_gap_count)
        if gap_count < 0:
            gap_count = 0
        return ThinkOutcome(
            partial_resolution=0.0,
            remaining_gap_count=gap_count,
            decided=False,
            reason="null_thinker",
        )


# ═══════════════════════════════════════════════════════════════════════
# CAMPAIGN-B-P3 — Phase 3 接線面：真實 LLM 思考器（**fail-closed 優先**）
# （工單：logs/CAMPAIGN-B-P3-LLM-THINKING-WIRING-WORK-ORDER.md）
#
# 🔴 本模組的**唯一安全邊界是「LLM 說了不算」**：
#    護欄不是裝飾 —— LLM 幻覺說「我已想完」，系統不許信。
# ═══════════════════════════════════════════════════════════════════════

#: LLM 提示詞的段落標題（既有五段 ＋ 第六段 ``OUTPUT``）。
#:
#: 🔴 前五段**逐位元沿用** :func:`build_thinking_prompt`（不得分叉 ⇒ Phase 2 守衛不受影響）。
LLM_PROMPT_SECTIONS: Tuple[str, ...] = PROMPT_SECTIONS + ("OUTPUT",)

#: LLM 回應 JSON 的**固定欄位**（缺一 ⇒ fail-closed）。
LLM_OUTCOME_FIELDS: Tuple[str, ...] = (
    "resolved_gap_count",
    "remaining_gap_count",
    "decided",
    "reason",
)

#: ``ThinkOutcome.reason`` 的長度上限（超出截斷 ⇒ 自由文字不得無界膨脹）。
MAX_REASON_CHARS = 64

#: ``LLMThinker`` 的 system 段（**不是角色發言**；是一次內省推理）。
LLM_THINKER_SYSTEM_PROMPT = (
    "你是一個內省推理引擎，不是對話角色：不得扮演任何角色發言，"
    "只依據使用者訊息列出的事實與資訊缺口推理。"
    "請嚴格依 OUTPUT 段的格式回覆單一 JSON 物件，不得輸出任何其他文字。"
)

#: ``OUTPUT`` 段的本文（**決定性**：固定字串 ⇒ 同一輸入必逐位元相同）。
_OUTPUT_SECTION_LINES: Tuple[str, ...] = (
    "OUTPUT",
    "只回覆一個 JSON 物件（不得多餘文字、不得加註解），欄位固定為四個：",
    "  resolved_gap_count   整數（int）：本次消解的缺口數",
    "  remaining_gap_count  整數（int）：思考後仍開著的缺口數",
    "  decided              布林（bool）：是否已想完",
    "  reason               字串（str）：短碼",
    "回覆格式（數值依實際缺口數替換）：",
    '{"resolved_gap_count": 0, "remaining_gap_count": 1, "decided": false, "reason": "one_gap_left"}',
    "硬性規則（違反即視為無效輸出，系統會拒收）：",
    "- 只能從上列 GAPS 的缺口逐項消解，不得臆造任何新的外部事實。",
    "- resolved_gap_count 不得大於 GAPS 中的缺口數。",
    "- resolved_gap_count + remaining_gap_count 不得大於 GAPS 中的缺口數。",
    "- decided 為 true 時，remaining_gap_count 必須為 0。",
)


def build_llm_thinking_prompt(req: "ThinkRequest") -> str:
    """由 ``ThinkRequest`` 建構「張力驅動思考」的 **LLM 版**提示詞（**決定性**）。

    格式：既有五段（``ROLE``／``CONTEXT``／``FACTORS``／``GAPS``／``ASK``）
    **逐位元沿用** :func:`build_thinking_prompt`，其後追加第六段 ``OUTPUT``
    （要求回單一 JSON 物件，欄位固定為 :data:`LLM_OUTCOME_FIELDS`）。

    🔴 ``OUTPUT`` 段明寫「只能從上列缺口逐項消解，不得臆造」⇒ 不得要求 LLM
    產生任何新的外部事實。
    🔴 同一輸入 ⇒ **逐位元相同**（無 uuid／time／hash 等非確定性成分）。
    """
    base = build_thinking_prompt(req)
    return base + "\n\n" + "\n".join(_OUTPUT_SECTION_LINES)


def _iter_json_candidates(text: str) -> List[str]:
    """列出文字中所有「以 ``{`` 開頭、括號平衡」的候選子字串。

    會尊重字串常值與跳脫字元（避免把字串裡的 ``}`` 當成結尾）。
    """
    candidates: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
            continue
        if char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start : index + 1])
                    start = -1
    return candidates


def _extract_first_json_object(text: Any) -> Optional[Dict[str, Any]]:
    """從回應文字中取出**第一個可解析的 JSON 物件**（找不到 ⇒ ``None``）。

    容忍：`` ```json `` 包裹、前後空白、前後雜訊文字。
    """
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("```"):
        newline = stripped.find("\n")
        if newline != -1:
            stripped = stripped[newline + 1 :]
        if stripped.endswith("```"):
            stripped = stripped[:-3]
        stripped = stripped.strip()
    for candidate in _iter_json_candidates(stripped):
        try:
            parsed = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _fail_closed_outcome(gap_count: int, reason: str) -> ThinkOutcome:
    """fail-closed 的 :class:`ThinkOutcome`（**不消解**、缺口全開著）。"""
    return ThinkOutcome(
        partial_resolution=0.0,
        remaining_gap_count=int(gap_count),
        decided=False,
        reason=str(reason),
    )


def parse_llm_outcome(text: str, req: "ThinkRequest") -> ThinkOutcome:
    """把 LLM 回應解析成 :class:`ThinkOutcome`（**fail-closed 優先**）。

    流程：

    1. 抽出 JSON（容忍 `` ```json `` 包裹；**取第一個可解析的 JSON 物件**）；
    2. 抽不到／解析失敗 ⇒ fail-closed（``reason="llm_parse_failed"``）；
    3. 欄位型別校驗（任一不符 ⇒ fail-closed，``reason="llm_outcome_invalid"``）：
       ``resolved_gap_count``／``remaining_gap_count`` 為 **int（排除 bool）且 ≥ 0**、
       ``decided`` 為 **bool**、``reason`` 為 **str**、四欄位齊備；
    4. 🔴 **幻覺護欄**（本案最重要的一條；共 **4 條規則、5 個檢查點**）：

       ① ``resolved_gap_count`` 不得大於 ``req.info_gap_count``；
       ② ``remaining_gap_count`` 不得大於 ``req.info_gap_count``；
       ③ ``resolved_gap_count + remaining_gap_count`` 不得大於 ``req.info_gap_count``；
       ④ **decided ⇔ remaining 必須一致**（兩個方向都查）：

       - ④-a ``decided is True`` ⇒ ``remaining_gap_count`` 必須為 0；
       - ④-b ``decided is False`` ⇒ ``remaining_gap_count`` 必須 ≥ 1
         （說「還沒想完」就不得宣稱「一個缺口都不剩」⇒ **自相矛盾不可信**）；

       任一違反 ⇒ fail-closed（``reason="llm_outcome_invalid"``，**未新增 reason 代碼**）；
    5. 通過 ⇒ ``partial_resolution = resolved / info_gap_count``（缺口數為 0 ⇒ 1.0）、
       ``reason`` 為空字串時填 ``"llm_outcome"``、長度上限 :data:`MAX_REASON_CHARS`。
    """
    gap_count = max(0, int(req.info_gap_count))

    payload = _extract_first_json_object(text)
    if payload is None:
        return _fail_closed_outcome(gap_count, "llm_parse_failed")

    if any(field not in payload for field in LLM_OUTCOME_FIELDS):
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")

    resolved = payload.get("resolved_gap_count")
    remaining = payload.get("remaining_gap_count")
    decided = payload.get("decided")
    reason = payload.get("reason")

    if isinstance(resolved, bool) or not isinstance(resolved, int) or resolved < 0:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    if isinstance(remaining, bool) or not isinstance(remaining, int) or remaining < 0:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    if not isinstance(decided, bool):
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    if not isinstance(reason, str):
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")

    # 🔴 幻覺護欄 ①：LLM 不得聲稱消解了**不存在的**缺口。
    if resolved > gap_count:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    # 🔴 幻覺護欄 ②：LLM 不得聲稱還有比實際**更多**的缺口。
    if remaining > gap_count:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    # 🔴 幻覺護欄 ③：消解數 ＋ 剩餘數不得超過實際缺口數。
    if resolved + remaining > gap_count:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    # 🔴 幻覺護欄 ④：decided ⇔ remaining 必須一致（**兩個方向都查**）。
    #    ④-a：說「想完了」⇒ 剩餘缺口必須為 0。
    if decided and remaining != 0:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")
    #    ④-b：說「還沒想完」⇒ 不得宣稱「一個缺口都不剩」（**自相矛盾不可信**：
    #    同時說「沒消解任何缺口」與「不用再想了」）⇒ remaining 必須 ≥ 1。
    if not decided and remaining == 0:
        return _fail_closed_outcome(gap_count, "llm_outcome_invalid")

    partial = 1.0 if gap_count == 0 else (resolved / gap_count)
    return ThinkOutcome(
        partial_resolution=float(partial),
        remaining_gap_count=int(remaining),
        decided=bool(decided),
        reason=(reason[:MAX_REASON_CHARS] if reason else "llm_outcome"),
    )


class LLMThinker:
    """以**真實 LLM** 執行的思考器（沙盒；**任何失敗一律 fail-closed**）。

    🔴 **呼叫面**：只經由**注入的** ``proxy``（任何有 ``generate_text`` 的物件）
    ⇒ 本模組**不 import** ``src.llm``、**不**自建 HTTP 客戶端、**不**自帶 API key、
    **不**繞過 ``LLMProxy``（繞過會失去 proxy 的計費／審計／人格注入）。
    ⇒ 守衛測試可注入 script backend ⇒ **零成本**、可重現。

    🔴 **async**：`LLMProxy.generate_text` 是 async ⇒ :meth:`think` 必須是 async
    ⇒ **不滿足**同步的 :class:`Thinker` Protocol（同步閉環的橋接見守衛測試檔）。

    成本護欄（執行期）：

    - ``max_calls``（預設 1）＝ 每個實例最多呼叫次數 ⇒ 用盡 ⇒ **不再呼叫**，
      直接回 fail-closed（``reason="llm_call_budget_exhausted"``）；
    - ``max_tokens``（預設 400）＝ 每次呼叫的輸出上限；
    - ``max_retries``（預設 0）＝ **禁止重試**（傳給 proxy ⇒ 恰好 1 次 HTTP 嘗試）。
    """

    def __init__(
        self,
        proxy: Any,
        *,
        model: str,
        max_tokens: int = 400,
        max_calls: int = 1,
        calls: Optional[Dict[str, Any]] = None,
        max_retries: int = 0,
    ) -> None:
        self._proxy = proxy
        self.model = str(model)
        self.max_tokens = int(max_tokens)
        self.max_calls = int(max_calls)
        self.max_retries = int(max_retries)
        #: 呼叫稽核計數（可由呼叫端注入同一個 dict 以即時觀察）。
        self.calls: Dict[str, Any] = calls if calls is not None else {}
        self.calls.setdefault("attempted", 0)
        self.calls.setdefault("succeeded", 0)
        self.calls.setdefault("failed", 0)
        self.calls.setdefault("budget_exhausted", 0)
        self.calls.setdefault("max_calls", self.max_calls)
        self.calls.setdefault("max_tokens", self.max_tokens)
        self.calls.setdefault("max_retries", self.max_retries)
        self.calls.setdefault("model", self.model)
        #: 實際送出的提示詞（與其 sha256）⇒ 可稽核「到底送了什麼」。
        self.last_prompt: str = ""
        self.last_prompt_sha256: str = ""

    async def think(self, req: "ThinkRequest", prompt: str) -> ThinkOutcome:
        """跑一次真實 LLM 思考（**async**；與 ``LLMProxy.generate_text`` 對齊）。

        🔴 **fail-closed**：預算用盡／proxy 例外／回 ``None``／回空字串／回應不可解析
        ⇒ 一律回「不消解」的 :class:`ThinkOutcome`，**不拋例外**（失敗不污染訊息路徑）。
        """
        gap_count = max(0, int(req.info_gap_count))

        # ① 預算防禦線（執行期）：用盡 ⇒ **不再呼叫**。
        if int(self.calls.get("attempted", 0)) >= self.max_calls:
            self.calls["budget_exhausted"] = int(self.calls.get("budget_exhausted", 0)) + 1
            return _fail_closed_outcome(gap_count, "llm_call_budget_exhausted")

        # ② 組提示詞（**由 req 決定性重建**；不信任呼叫端傳來的 prompt）。
        llm_prompt = build_llm_thinking_prompt(req)
        self.last_prompt = llm_prompt
        self.last_prompt_sha256 = hashlib.sha256(llm_prompt.encode("utf-8")).hexdigest()
        messages = [
            {"role": "system", "content": LLM_THINKER_SYSTEM_PROMPT},
            {"role": "user", "content": llm_prompt},
        ]

        # ③ 呼叫（**只**經由注入的 proxy；禁重試 ⇒ 恰好 1 次嘗試）。
        self.calls["attempted"] = int(self.calls.get("attempted", 0)) + 1
        try:
            raw = await self._proxy.generate_text(
                messages,
                agent_id=str(req.agent_id),
                max_tokens=self.max_tokens,
                max_retries=self.max_retries,
            )
        except Exception:  # noqa: BLE001 — fail-closed（不讓例外冒泡到呼叫端）
            self.calls["failed"] = int(self.calls.get("failed", 0)) + 1
            return _fail_closed_outcome(gap_count, "llm_call_failed")

        if raw is None:
            self.calls["failed"] = int(self.calls.get("failed", 0)) + 1
            return _fail_closed_outcome(gap_count, "llm_call_failed")

        text = str(raw).strip()
        if not text:
            self.calls["failed"] = int(self.calls.get("failed", 0)) + 1
            return _fail_closed_outcome(gap_count, "llm_parse_failed")

        self.calls["succeeded"] = int(self.calls.get("succeeded", 0)) + 1
        return parse_llm_outcome(text, req)


__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_REASON_CHARS",
    "MAX_SOURCE_ROWS_IN_PROMPT",
    "PROMPT_SECTIONS",
    "SOURCE_ROW_IDENTIFYING_KEYS",
    "DeterministicMockThinker",
    "LLMThinker",
    "LLM_OUTCOME_FIELDS",
    "LLM_PROMPT_SECTIONS",
    "LLM_THINKER_SYSTEM_PROMPT",
    "NullThinker",
    "ThinkOutcome",
    "Thinker",
    "build_llm_thinking_prompt",
    "build_thinking_prompt",
    "parse_llm_outcome",
]
