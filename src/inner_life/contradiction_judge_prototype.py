"""src/inner_life/contradiction_judge_prototype.py — G-3 語意判定器「Mock 沙盒原型」。

工單：戰役二第三片 G-3（Mock 判定器沙盒原型）
契約：logs/CAMPAIGN-B-BATTLE2-G3-SEMANTIC-JUDGMENT-CONTRACT.md（§三／§三之二／§四／§六）

這個模組回答什麼問題
--------------------
契約 §四 的機制鏈把「最後裁決」交給**真實語意判斷**：判定結果是 ``CONTRADICT`` 或
``COMPATIBLE``，**只有前者**才允許呼叫端寫入一筆矛盾紀錄。

本模組把那條鏈拆成**五個可獨立測試的零件**（全部 0 LLM、0 網路、0 檔案 IO、0 生產寫入）：

1. ``build_judge_prompt()`` —— 判定**專用** prompt 骨架（明文要求只回覆單一 token）；
2. ``parse_verdict()`` —— **嚴格**二元解析器（只做字面比對，不做任何語意推測）；
3. ``decide_action()`` —— fail-closed 映射（``COMPATIBLE`` 與 ``UNDECIDED`` **同義**）；
4. ``new_call_counter()`` ＋ ``ContradictionJudge`` —— **每 agent 每日至多 1 次**的預算護欄；
5. **注入式** backend —— 讓測試可以假造回應 ⇒ 全鏈路可在 0 成本下驅動。

Fail-Closed 的**方向**（契約 §三之二，本片最關鍵的一條）
-------------------------------------------------------
不可判定（預算用盡／例外／空回應／解析失敗）⇒ **視為 ``COMPATIBLE``** ⇒
**不寫入任何矛盾紀錄** ⇒ **寧可漏記矛盾，不可誤記矛盾**。

漏記只是讓召回池少一顆命題；誤記會讓一個活著的命題被誤殺（不可逆）⇒ 方向必須是保守的那一邊。

預算護欄的形狀（契約 §三）
--------------------------
「每 Agent 每日至多 1 次」在程式裡是**呼叫次數上限**：計數器由呼叫端注入並**跨實例共用**
（與 ``src/inner_life/tension_thinking.py`` 的 ``calls`` 注入同一套路，但**本模組不 import 該模組**）。
「每日」的邊界由**計數器的生命週期**承載：呼叫端為每個 (agent, 當日) 建立一份新的計數器、
跨日即換新的一份 ⇒ 跨實例共用不等於跨日共用。

計數器**只計數**，**不發出任何請求**；請求只可能來自呼叫端注入的 backend。

本模組**刻意不做**的事
----------------------
- **不做「顯式衝突嫌疑」的預濾**：契約 §六 第 1 點明寫，預濾細節（「跨日」與「內容不同」的
  具體判定形狀）**必須以夜槽後的穩定態資料實測決定** ⇒ **不得**先寫死條件再找資料佐證。
  本模組因此**不含任何**候選內容比對／跨日比對／相似度度量（守衛 J7 以 AST 釘死）。
- **不做真實 LLM 接線**：不 import ``src.llm``／``configs.loader``、不建 proxy、不設逾時。
- **不寫任何引擎狀態**：不呼叫 ``record_contradiction()``／``supersede()``。
- **零成本是結構性保證**：本模組**不 import** 任何具網路能力的模組（守衛 J6 以 AST 釘死），
  唯一的外部互動點是呼叫端**注入**的 backend ⇒ 「0 真實 LLM 成本」不靠註解、靠結構。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Sequence

#: 判定結果的三個可能值：前兩個是契約 §三 的**二元輸出**，第三個是 fail-closed 的落點。
VERDICT_CONTRADICT = "CONTRADICT"
VERDICT_COMPATIBLE = "COMPATIBLE"
VERDICT_UNDECIDED = "UNDECIDED"

#: 最終動作：只有 ``ACTION_RECORD`` 會讓**呼叫端**寫入一筆矛盾紀錄（本模組自己不寫）。
ACTION_RECORD = "record"
ACTION_NO_RECORD = "no_record"

#: 結果原因（可稽核；不影響判定方向）。
REASON_VERDICT_CONTRADICT = "verdict_contradict"
REASON_VERDICT_COMPATIBLE = "verdict_compatible"
REASON_UNDECIDED = "undecided_fail_closed"
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
REASON_BACKEND_ERROR = "backend_failed"

#: 契約 §三：「每 Agent 每日至多 1 次」。這是**呼叫次數上限**，不是任何語意條件。
MAX_CALLS_PER_AGENT = 1


def build_judge_prompt(
    existing_claim: str,
    candidate_claim: str,
    evidence_sources: Optional[Sequence[str]] = None,
) -> str:
    """組出判定**專用**的單一 prompt（契約 §三：二元輸出、單一 token 回覆）。

    Args:
        existing_claim: 既有命題的內容。
        candidate_claim: 新候選命題的內容。
        evidence_sources: （可選）既有證據來源摘要。

    Returns:
        單一 prompt 字串。

    Notes:
        - prompt 內**只**出現兩段命題內容與格式要求；**不含**任何模型名稱、供應商名稱、
          URL、API key 或連線參數 ⇒ 呼叫端可以自行決定路由（契約 §三：路由沿用既有配置）。
        - 本函式**不**對命題內容做任何加工、比較或條件判斷（守衛 J7）。
    """
    evidence_block = ""
    if evidence_sources:
        evidence_block = (
            "\n【既有證據來源（摘要）】\n"
            + "；".join(str(item) for item in evidence_sources)
            + "\n"
        )
    return (
        "你是一位嚴格的命題互斥檢查員。\n"
        "\n"
        "【既有命題】\n"
        f"{existing_claim}\n"
        "\n"
        "【新候選命題】\n"
        f"{candidate_claim}\n"
        f"{evidence_block}"
        "\n"
        "【判斷題目】\n"
        "新候選命題與既有命題是否互斥（兩者不能同時為真）？\n"
        "- 互斥（不能同時為真）⇒ 只回覆 CONTRADICT\n"
        "- 不互斥（可以同時為真）⇒ 只回覆 COMPATIBLE\n"
        "\n"
        "嚴格要求：只回覆 CONTRADICT 或 COMPATIBLE 其中一個 token，"
        "不得輸出任何其他文字、標點、說明或引號。\n"
    )


def parse_verdict(response: Any) -> str:
    """把回應解析成二元判定（**只做字面比對**，不做任何語意推測）。

    規則（嚴格）：

    1. 非字串（``None``／數字／容器）⇒ ``UNDECIDED``；
    2. 去首尾空白後**完全等於** ``CONTRADICT`` ⇒ ``CONTRADICT``；
    3. 去首尾空白後**完全等於** ``COMPATIBLE``（大小寫不敏感）⇒ ``COMPATIBLE``；
    4. **其餘一切**（多餘文字、兩者並列、空字串）⇒ ``UNDECIDED``。

    ⇒ 本解析器**不會**把「不矛盾」推論成 ``COMPATIBLE``、也不會把「好像有矛盾」推論成
    ``CONTRADICT``：猜測一律落到 ``UNDECIDED``，再由 fail-closed 層處理。
    """
    if not isinstance(response, str):
        return VERDICT_UNDECIDED
    token = response.strip().upper()
    if token == VERDICT_CONTRADICT:
        return VERDICT_CONTRADICT
    if token == VERDICT_COMPATIBLE:
        return VERDICT_COMPATIBLE
    return VERDICT_UNDECIDED


def decide_action(verdict: str) -> str:
    """把判定映射成最終動作（**fail-closed**：只有 ``CONTRADICT`` 才記錄）。

    ``COMPATIBLE`` 與 ``UNDECIDED`` 在此**同義** ⇒ 都不記錄任何矛盾紀錄。
    任何無法辨識的判定值一律落到「不記錄」那一邊。
    """
    if verdict == VERDICT_CONTRADICT:
        return ACTION_RECORD
    return ACTION_NO_RECORD


def _reason_for(verdict: str) -> str:
    """判定值 → 可稽核的原因字串（不影響判定方向）。"""
    if verdict == VERDICT_CONTRADICT:
        return REASON_VERDICT_CONTRADICT
    if verdict == VERDICT_COMPATIBLE:
        return REASON_VERDICT_COMPATIBLE
    return REASON_UNDECIDED


def new_call_counter() -> Dict[str, Any]:
    """建立一份新的預算計數器（**每 (agent, 當日) 一份**，由呼叫端跨實例共用）。

    欄位形狀與 ``src/inner_life/tension_thinking.py`` 的 ``calls`` 同款：
    ``attempted``／``succeeded``／``failed``／``budget_exhausted``；
    另多一個 ``agents`` 子字典，記錄**每個 agent 各自**的嘗試次數
    ⇒ 即使呼叫端不小心讓多個 agent 共用同一份計數器，「每 agent 至多 1 次」仍然成立。
    """
    return {
        "attempted": 0,
        "succeeded": 0,
        "failed": 0,
        "budget_exhausted": 0,
        "agents": {},
    }


def _agent_attempts(calls: Dict[str, Any], agent_id: str) -> int:
    """某 agent 在這份計數器裡已經嘗試過幾次。"""
    agents = calls.setdefault("agents", {})
    entry = agents.setdefault(agent_id, {})
    return int(entry.get("attempted", 0))


def _register_attempt(calls: Dict[str, Any], agent_id: str) -> None:
    """登記一次嘗試（**只計數**，不發出任何請求）。"""
    calls["attempted"] = int(calls.get("attempted", 0)) + 1
    agents = calls.setdefault("agents", {})
    entry = agents.setdefault(agent_id, {})
    entry["attempted"] = int(entry.get("attempted", 0)) + 1


@dataclass(frozen=True)
class JudgeOutcome:
    """一次判定的完整結果（frozen；可稽核、可序列化）。"""

    #: 三值判定結果。
    verdict: str
    #: 最終動作（``record``／``no_record``）。
    action: str
    #: 是否**應由呼叫端**記錄一筆矛盾紀錄（只有 ``CONTRADICT`` 為 True）。
    should_record: bool
    #: 原因字串。
    reason: str
    #: 實際送出的 prompt（可稽核「到底送了什麼」）。
    prompt: str
    #: prompt 的 sha256。
    prompt_sha256: str
    #: backend 的原始回應（例外時為 None）。
    response: Any
    #: 是否真的呼叫過 backend（預算拒絕時為 False）。
    backend_invoked: bool


class ContradictionJudge:
    """語意判定器（backend **注入式**；本類自身不連網、不讀檔、不寫任何狀態）。

    典型用法::

        calls = new_call_counter()                 # 每 (agent, 當日) 一份
        judge = ContradictionJudge(backend, calls=calls, agent_id="agent-a")
        outcome = judge.judge(existing_claim, candidate_claim)
        if outcome.should_record:
            ...                                    # 由呼叫端寫入矛盾紀錄

    預算（契約 §三）：每 agent 每日至多 1 次；用盡 ⇒ **不再呼叫** backend，
    直接回 fail-closed（``reason="budget_exhausted"``）。
    """

    def __init__(
        self,
        backend: Callable[[str], Any],
        *,
        calls: Optional[Dict[str, Any]] = None,
        agent_id: str = "",
        max_calls: int = MAX_CALLS_PER_AGENT,
    ) -> None:
        #: 注入的 backend：唯一的外部互動點（回應可以是字串，也可以丟例外）。
        self._backend = backend
        #: 本判定器所屬的 agent（預算以它為單位）。
        self.agent_id = str(agent_id)
        #: 呼叫次數上限（契約 §三 ＝ 1）。
        self.max_calls = int(max_calls)
        #: 預算計數器（由呼叫端注入 ⇒ 跨實例共用）。
        self.calls: Dict[str, Any] = calls if calls is not None else new_call_counter()
        self.calls.setdefault("attempted", 0)
        self.calls.setdefault("succeeded", 0)
        self.calls.setdefault("failed", 0)
        self.calls.setdefault("budget_exhausted", 0)
        self.calls.setdefault("agents", {})

    def judge(
        self,
        existing_claim: str,
        candidate_claim: str,
        evidence_sources: Optional[Sequence[str]] = None,
    ) -> JudgeOutcome:
        """執行一次語意判定（fail-closed；任何不確定都落到「不記錄」）。

        流程：

        1. 組出判定專用 prompt（**由本次輸入決定性重建**；不信任外部傳入的 prompt）；
        2. 預算防禦線：該 agent 在這份計數器裡已用盡 ⇒ **不再呼叫** backend，回 fail-closed；
        3. 計次（計數器**只計數**）；
        4. 呼叫**注入的** backend（禁重試 ⇒ 恰好 1 次嘗試；例外 ⇒ fail-closed）；
        5. 嚴格解析 → 決定最終動作。
        """
        prompt = build_judge_prompt(existing_claim, candidate_claim, evidence_sources)
        prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()

        # ① 預算防禦線（執行期）：用盡 ⇒ **不再呼叫**。
        if _agent_attempts(self.calls, self.agent_id) >= self.max_calls:
            self.calls["budget_exhausted"] = int(self.calls.get("budget_exhausted", 0)) + 1
            return JudgeOutcome(
                verdict=VERDICT_UNDECIDED,
                action=ACTION_NO_RECORD,
                should_record=False,
                reason=REASON_BUDGET_EXHAUSTED,
                prompt=prompt,
                prompt_sha256=prompt_sha256,
                response=None,
                backend_invoked=False,
            )

        # ② 計次（計數器**只計數**，不發任何請求）。
        _register_attempt(self.calls, self.agent_id)

        # ③ 呼叫（**只**經由注入的 backend；禁重試 ⇒ 恰好 1 次嘗試）。
        try:
            raw = self._backend(prompt)
        except Exception:  # noqa: BLE001 — fail-closed（不讓例外冒泡到呼叫端）
            self.calls["failed"] = int(self.calls.get("failed", 0)) + 1
            return JudgeOutcome(
                verdict=VERDICT_UNDECIDED,
                action=ACTION_NO_RECORD,
                should_record=False,
                reason=REASON_BACKEND_ERROR,
                prompt=prompt,
                prompt_sha256=prompt_sha256,
                response=None,
                backend_invoked=True,
            )

        # ④ 嚴格解析 → 決定方向（解析不出來 ⇒ fail-closed ⇒ 不記錄）。
        verdict = parse_verdict(raw)
        if verdict == VERDICT_UNDECIDED:
            self.calls["failed"] = int(self.calls.get("failed", 0)) + 1
        else:
            self.calls["succeeded"] = int(self.calls.get("succeeded", 0)) + 1
        action = decide_action(verdict)
        return JudgeOutcome(
            verdict=verdict,
            action=action,
            should_record=action == ACTION_RECORD,
            reason=_reason_for(verdict),
            prompt=prompt,
            prompt_sha256=prompt_sha256,
            response=raw,
            backend_invoked=True,
        )


__all__ = [
    "ACTION_NO_RECORD",
    "ACTION_RECORD",
    "ContradictionJudge",
    "JudgeOutcome",
    "MAX_CALLS_PER_AGENT",
    "REASON_BACKEND_ERROR",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_UNDECIDED",
    "REASON_VERDICT_COMPATIBLE",
    "REASON_VERDICT_CONTRADICT",
    "VERDICT_COMPATIBLE",
    "VERDICT_CONTRADICT",
    "VERDICT_UNDECIDED",
    "build_judge_prompt",
    "decide_action",
    "new_call_counter",
    "parse_verdict",
]
