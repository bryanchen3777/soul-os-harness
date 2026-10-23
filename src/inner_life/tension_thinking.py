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
- **不得**做任何網路呼叫、**不得**讀寫任何檔案（純函式層）。

紀律
----
- **純函式、決定性**：同一輸入 ⇒ 逐位元相同的提示詞與 outcome。
  🔴 不得引入 ``id()``／``time()``／``hash()``／物件雜湊等非確定性成分。
- **零 LLM 呼叫**：本模組是 Phase 2 的**離線沙盒版**；真實 LLM 呼叫屬 Phase 3。
- **零檔案 I/O**：提示詞建構只看傳入的 :class:`~src.inner_life.tension_closure_loop.ThinkRequest`。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Protocol, Tuple, runtime_checkable

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


__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_SOURCE_ROWS_IN_PROMPT",
    "PROMPT_SECTIONS",
    "SOURCE_ROW_IDENTIFYING_KEYS",
    "DeterministicMockThinker",
    "NullThinker",
    "ThinkOutcome",
    "Thinker",
    "build_thinking_prompt",
]
