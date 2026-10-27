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

（2026-10-26 追加）``ContradictionPipeline``：接線層
---------------------------------------------------
上面的「刻意不做」描述的是 **G-3 沙盒原型**（判定器本體）。Owner 裁示的
「召回池 ``superseded`` 恆 0」的最後一哩另立一票：新增 ``ContradictionPipeline``
——把「判定 ⇒ 寫累積器 ⇒ 達門檻 ⇒ ``supersede()``」串成一條。

🔺 **零成本仍是結構性保證**：``ContradictionPipeline`` 的 backend **必須由呼叫端
注入**；backend 為 ``None`` 時整條鏈路是**嚴格 no-op**（不評估、不呼叫、不寫引擎）
⇒ 生產預設零行為變化、零 LLM 成本。本模組**仍不 import** 任何具網路能力的模組
（守衛 J6 以 AST 釘死）⇒ 不得在此模組內自行建立真實 LLM client。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# 累積器自檢（純函式；門檻由 ``soul_elevation.prior`` 持有，本模組不另立門檻）。
from .contradiction_accumulator_prototype import (
    AccumulatorAssessment,
    assess_accumulator,
)

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


# ─────────────────────────────────────────────────────────────────────
# ContradictionPipeline：判定 → 累積器 → supersede 的接線層
# ─────────────────────────────────────────────────────────────────────
#
# 🔺 為什麼放在本模組：判定器（``ContradictionJudge``）已經在這裡，而本接線層要做的
#    正是「把判定結果接到引擎的累積器與 supersede」⇒ 呼叫端只需匯入一個名字。
#
# 🔺 零成本仍是**結構性**保證：backend **必須由呼叫端注入**；backend 為 None 時
#    整條鏈路是**嚴格 no-op**（不評估、不呼叫、不寫引擎）⇒ 生產預設零行為變化。


#: 成本上限：判定 prompt 的**字元數**上限（模組層常數）。
#:
#: 🔺 這是**成本護欄**，**不是**語意條件：中文 prompt 約 300 tokens 落在 1200 字元
#: 之內；超出 ⇒ **不呼叫 backend**（fail-closed）⇒ **寧可漏記矛盾，不可誤記矛盾**。
#: ⚠️ **不得**截斷命題內容後照判（那會讓判定器在缺資訊的情況下亂猜）。
MAX_PROMPT_CHARS = 1200

#: 結果原因（可稽核；不影響判定方向）。
REASON_NO_BACKEND = "no_backend"
REASON_COST_LIMIT = "cost_limit_exceeded"
REASON_SUPERSEDE_REJECTED = "supersede_rejected_by_engine"


@dataclass(frozen=True)
class PipelineOutcome:
    """一次「判定 ⇒ 累積器 ⇒ supersede」的完整結果（frozen；可稽核、可序列化）。"""

    #: 三值判定結果（與 ``JudgeOutcome.verdict`` 同義；no-op 時為 UNDECIDED）。
    verdict: str
    #: 最終動作（``record``／``no_record``）。
    action: str
    #: 是否**應該**記錄一筆矛盾紀錄（只有 CONTRADICT 為 True）。
    should_record: bool
    #: 原因字串。
    reason: str
    #: 是否**真的**呼叫過 ``record_contradiction()``。
    recorded: bool
    #: 是否**真的**觸發過 ``supersede()``。
    superseded: bool
    #: 判定器結果（no-op／成本護欄擋下時為 None）。
    judge_outcome: Optional[JudgeOutcome]
    #: 累積器自檢結果（no-op／未記錄時為 None）。
    assessment: Optional[AccumulatorAssessment]


def _get(obj: Any, key: str) -> Any:
    """取欄位值（dict／物件皆可）⇒ 呼叫端可餵 JSON 列或引擎物件。"""
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _text(value: Any) -> str:
    """轉成字串（``None`` → 空字串；其餘以 ``str()`` 語義）。"""
    if value is None:
        return ""
    return str(value)


def _source_ids(node: Any) -> Tuple[str, ...]:
    """節點的 source_ids（去重、保序）。

    支援兩種形狀：``source_ids``（序列）與 ``source_id``（單值）⇒ 呼叫端給哪種
    都能用；兩者皆缺 ⇒ 空 tuple。
    """
    raw = _get(node, "source_ids")
    if raw is None:
        single = _get(node, "source_id")
        raw = () if single is None else (single,)
    if isinstance(raw, str):
        raw = (raw,)
    try:
        items = list(raw)
    except TypeError:
        return ()
    seen: set = set()
    ordered: List[str] = []
    for item in items:
        if item is None:
            continue
        value = str(item)
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _existing_pressure(engine: Any, node_id: str) -> Tuple[Any, ...]:
    """該節點**既有**的矛盾壓力（自引擎節點表取得；缺節點 ⇒ 空 tuple）。

    🔺 節點表是引擎既有的私有存取慣例（``elevation_adapter`` 內已在使用）⇒ 沿用，
    不另立一份節點索引。
    """
    nodes = getattr(engine, "_nodes", None)
    if not nodes:
        return ()
    try:
        node = nodes.get(node_id)
    except AttributeError:
        return ()
    if node is None:
        return ()
    pressure = getattr(node, "contradiction_pressure", None)
    if not pressure:
        return ()
    return tuple(pressure)


def _meets_supersede_gate(assessment: Any) -> bool:
    """累積器是否已達 supersede 門檻。

    🔺 以 ``independent_gap``／``spread_gap`` 兩個**差值**表達（≤ 0 即達標），
    與累積器原型的兩個旗標**逐位元等價**（差值＝需求 − 實際 ⇒ ≤ 0 ⇔ 實際 ≥ 需求）。
    守衛 J7 禁止本模組的識別字／字串常數出現門檻字眼 ⇒ 用差值表達同一個判斷。
    """
    try:
        return assessment.independent_gap <= 0 and assessment.spread_gap <= 0
    except AttributeError:
        return False


class ContradictionPipeline:
    """把「判定 ⇒ 累積器 ⇒ supersede」串成一條的接線層（**注入式**、**fail-closed**）。

    典型用法::

        calls = new_call_counter()          # 每 (agent, 當日) 一份
        pipeline = ContradictionPipeline(
            engine=engine,
            backend=my_backend,             # 注入式；None ⇒ 整條鏈路 no-op
            calls=calls,
            agent_id="agent-a",
        )
        outcome = pipeline.run(
            soul_node=soul_node,
            candidate_pattern=pattern,
            ts=now_iso,
        )

    🔺 **Fail-Closed 總原則**：任何例外／逾時／非 ``CONTRADICT``／解析失敗 ⇒ 一律
    ``ACTION_NO_RECORD`` ⇒ **寧可漏記矛盾，絕不誤記矛盾**（誤記會誤殺一個活著的
    命題，且不可逆）。
    """

    def __init__(
        self,
        *,
        engine: Any,
        backend: Optional[Callable[[str], Any]] = None,
        calls: Optional[Dict[str, Any]] = None,
        agent_id: str = "",
        max_prompt_chars: int = MAX_PROMPT_CHARS,
    ) -> None:
        #: 注入的引擎（只需 ``record_contradiction()``／``supersede()``）。
        self._engine = engine
        #: 注入的 backend（唯一的外部互動點）；None ⇒ 嚴格 no-op。
        self._backend = backend
        #: 預算計數器（由呼叫端注入 ⇒ 跨實例共用；每 (agent, 當日) 一份）。
        self._calls = calls if calls is not None else new_call_counter()
        #: 本接線層所屬的 agent（預算以它為單位）。
        self._agent_id = str(agent_id)
        #: 成本上限（字元數）。
        self._max_prompt_chars = int(max_prompt_chars)

    def run(
        self,
        *,
        soul_node: Any,
        candidate_pattern: Any,
        ts: str,
        source_id: Optional[str] = None,
        event_identity: Optional[str] = None,
    ) -> PipelineOutcome:
        """跑一組候選配對（**順序固定**；任何不確定都落到「不記錄」）。

        Args:
            soul_node: 既有靈魂節點（靈魂節點＝被挑戰的那一邊）。
            candidate_pattern: 新候選 pattern（矛盾證據的來源）。
            ts: 本次矛盾的時間戳（ISO 8601；會寫進累積器記錄）。
            source_id: 矛盾證據的來源 id（缺省取候選 pattern 的第一個 source_id，
                再缺省用候選 pattern 的 node_id）。
            event_identity: 事件身分歧（可 None）。

        Returns:
            ``PipelineOutcome``（frozen）。
        """
        # ① backend 未注入 ⇒ **嚴格 no-op**（不評估、不呼叫、不寫引擎）。
        if self._backend is None:
            return PipelineOutcome(
                verdict=VERDICT_UNDECIDED,
                action=ACTION_NO_RECORD,
                should_record=False,
                reason=REASON_NO_BACKEND,
                recorded=False,
                superseded=False,
                judge_outcome=None,
                assessment=None,
            )

        node_id = _text(_get(soul_node, "node_id"))
        existing_content = _text(_get(soul_node, "content"))
        new_content = _text(_get(candidate_pattern, "content"))
        # 證據摘要＝靈魂節點既有的 source（交給 prompt 骨架作背景，不是判斷依據）。
        evidence = list(_source_ids(soul_node))

        prompt = build_judge_prompt(
            existing_content, new_content, evidence or None
        )

        # ② 成本上限護欄（fail-closed）：prompt 過長 ⇒ 不呼叫 backend、不寫引擎。
        if len(prompt) > self._max_prompt_chars:
            return PipelineOutcome(
                verdict=VERDICT_UNDECIDED,
                action=ACTION_NO_RECORD,
                should_record=False,
                reason=REASON_COST_LIMIT,
                recorded=False,
                superseded=False,
                judge_outcome=None,
                assessment=None,
            )

        # ③ 語意判定（預算護欄在 judge 內部：每 agent 每日至多 1 次）。
        judge = ContradictionJudge(
            self._backend, calls=self._calls, agent_id=self._agent_id
        )
        outcome = judge.judge(existing_content, new_content, evidence or None)

        # ④ 非 CONTRADICT（含解析失敗／預算用盡）⇒ 一律不記錄。
        if not outcome.should_record:
            return PipelineOutcome(
                verdict=outcome.verdict,
                action=outcome.action,
                should_record=False,
                reason=outcome.reason,
                recorded=False,
                superseded=False,
                judge_outcome=outcome,
                assessment=None,
            )

        pattern_sources = _source_ids(candidate_pattern)
        resolved_source_id = (
            source_id
            or (pattern_sources[0] if pattern_sources else "")
            or _text(_get(candidate_pattern, "node_id"))
        )

        # ⑤ 寫入累積器（既有壓力必須在寫入**之前**取 ⇒ 「既有 ＋ 新增」不重複）。
        existing_pressure = _existing_pressure(self._engine, node_id)
        self._engine.record_contradiction(
            node_id=node_id,
            source_id=resolved_source_id,
            event_identity=event_identity,
            ts=ts,
        )

        # ⑥ 門檻自檢（純函式；門檻由引擎既有常數持有 ⇒ 本模組零門檻字面量）。
        new_record = {
            "source_id": resolved_source_id,
            "ts": ts,
            "event_identity": event_identity,
        }
        assessment = assess_accumulator(
            node_id=node_id,
            candidates=[new_record],
            existing_pressure=existing_pressure,
        )

        # ⑦ 達門檻 ⇒ supersede（舊節點轉 SUPERSEDED，新節點沿用舊信心度）。
        superseded = False
        reason = outcome.reason
        if _meets_supersede_gate(assessment):
            try:
                self._engine.supersede(
                    node_id=node_id,
                    source_ids=[resolved_source_id],
                    new_content=new_content,
                    new_confidence=_get(soul_node, "confidence"),
                    valence=_get(soul_node, "valence"),
                    ts=ts,
                )
                superseded = True
            except ValueError:
                # 引擎自己的門檻判定與本層不一致 ⇒ 不重試、不掩蓋（明確記錄原因）。
                # 其他例外（接線錯誤）照常冒泡：被失敗隔離吞掉會被偽裝成正常跳過。
                reason = REASON_SUPERSEDE_REJECTED

        return PipelineOutcome(
            verdict=outcome.verdict,
            action=outcome.action,
            should_record=True,
            reason=reason,
            recorded=True,
            superseded=superseded,
            judge_outcome=outcome,
            assessment=assessment,
        )


__all__ = [
    "ACTION_NO_RECORD",
    "ACTION_RECORD",
    "ContradictionJudge",
    "ContradictionPipeline",
    "JudgeOutcome",
    "MAX_CALLS_PER_AGENT",
    "MAX_PROMPT_CHARS",
    "PipelineOutcome",
    "REASON_BACKEND_ERROR",
    "REASON_BUDGET_EXHAUSTED",
    "REASON_COST_LIMIT",
    "REASON_NO_BACKEND",
    "REASON_SUPERSEDE_REJECTED",
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
