"""tests/inner_life/test_contradiction_prefilter_guard.py — 矛盾預濾器與接線層守衛（K1～K10）。

工單：FEAT-SUPERSEDED-CONTRADICTION-PIPELINE（2026-10-26；「召回池 superseded 恆 0」的最後一哩）

這支守衛釘死三件事：

1. **結構預濾器**（``src/inner_life/contradiction_prefilter.py``）的七道條件
   —— 逐條用「100% 剔除」＋「對照組必須被選中」雙向斷言（剔除不是靠全砍）。
2. **接線層**（``ContradictionPipeline``）的 fail-closed 與成本上限行為。
3. **端到端閉環**：3 個不同日期的矛盾證據 ⇒ ``supersede()`` 真的被觸發、
   舊節點轉 SUPERSEDED、**新節點的信心度逐位元繼承舊節點**。

🔺 **零真實 LLM**：所有判定一律注入 mock backend；本檔**不連網、不讀寫生產
``data/**``**、不啟動任何服務行程。
"""

from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.inner_life.contradiction_judge_prototype import (
    REASON_BACKEND_ERROR,
    REASON_BUDGET_EXHAUSTED,
    REASON_COST_LIMIT,
    VERDICT_COMPATIBLE,
    VERDICT_CONTRADICT,
    ContradictionPipeline,
    new_call_counter,
)
from src.inner_life.contradiction_prefilter import (
    normalize_content,
    select_conflict_candidate_pairs,
)

# ─────────────────────────────────────────────────────────────────────
# 測試用資料與 stub（**不依賴**真實 soul_elevation 的持久化行為）
# ─────────────────────────────────────────────────────────────────────


def _soul(
    node_id: str = "soul-1",
    agent_id: str = "agent-a",
    node_type: str = "value",
    content: str = "他喜歡喝咖啡",
    created_ts: str = "2026-10-01T10:00:00Z",
    confidence: float = 0.7,
    valence: str = "neutral",
    source_ids: Sequence[str] = ("src-old",),
    candidate_node_type: Optional[str] = None,
) -> Dict[str, Any]:
    """靈魂節點（形狀與生產 ``elevation_nodes.jsonl`` 的列同款）。"""
    return {
        "node_id": node_id,
        "agent_id": agent_id,
        "node_type": node_type,
        "candidate_node_type": candidate_node_type,
        "content": content,
        "created_ts": created_ts,
        "confidence": confidence,
        "valence": valence,
        "source_ids": list(source_ids),
    }


def _pattern(
    node_id: str = "pat-1",
    agent_id: str = "agent-a",
    content: str = "他其實不喜歡喝咖啡",
    created_ts: str = "2026-10-05T10:00:00Z",
    dimension: str = "value",
    source_ids: Sequence[str] = ("src-new",),
) -> Dict[str, Any]:
    """成熟 pattern 節點（候選維度由 ``candidate_node_type`` 決定）。"""
    return {
        "node_id": node_id,
        "agent_id": agent_id,
        "node_type": "pattern",
        "candidate_node_type": dimension,
        "content": content,
        "created_ts": created_ts,
        "source_ids": list(source_ids),
    }


class _MockBackend:
    """注入式 mock backend（**零真實 LLM**）：記錄呼叫次數與收到的 prompt。"""

    def __init__(self, response: str = "CONTRADICT", error: Optional[Exception] = None):
        self.response = response
        self.error = error
        self.calls: List[str] = []

    def __call__(self, prompt: str) -> Any:
        self.calls.append(prompt)
        if self.error is not None:
            raise self.error
        return self.response


class _StubNode:
    """stub 節點（帶 ``contradiction_pressure`` 與 ``lifecycle_state``）。"""

    def __init__(self, data: Dict[str, Any]):
        self.node_id = data["node_id"]
        self.content = data.get("content", "")
        self.confidence = data.get("confidence", 0.5)
        self.valence = data.get("valence", "neutral")
        self.lifecycle_state = data.get("lifecycle_state", "active")
        self.contradiction_pressure: List[Dict[str, Any]] = []


class _StubEngine:
    """stub 引擎：記錄 ``record_contradiction``／``supersede`` 的呼叫與參數。

    🔺 **不依賴**真實 ``soul_elevation.engine`` 的持久化行為（不碰磁碟、不碰生產
    ``data/**``）；只模擬接線層需要的三件事：節點表、壓力累積、supersede 後的
    「舊節點轉 SUPERSEDED ＋ 新節點沿用舊信心度」。
    """

    def __init__(self, soul_nodes: Sequence[Dict[str, Any]] = ()):
        self._nodes: Dict[str, _StubNode] = {
            n["node_id"]: _StubNode(n) for n in soul_nodes
        }
        self.record_calls: List[Dict[str, Any]] = []
        self.supersede_calls: List[Dict[str, Any]] = []

    def record_contradiction(
        self,
        node_id: str,
        *,
        source_id: str,
        ts: Optional[str] = None,
        event_identity: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        self.record_calls.append(
            {
                "node_id": node_id,
                "source_id": source_id,
                "ts": ts,
                "event_identity": event_identity,
            }
        )
        node = self._nodes.get(node_id)
        if node is not None:
            node.contradiction_pressure.append(
                {
                    "source_id": source_id,
                    "ts": ts,
                    "event_identity": event_identity,
                }
            )

    def supersede(
        self,
        node_id: str,
        *,
        source_ids: Sequence[str],
        new_content: str,
        new_confidence: float,
        valence: str,
        ts: Optional[str] = None,
        **_kwargs: Any,
    ) -> None:
        new_node_id = f"{node_id}-superseded-by"
        self.supersede_calls.append(
            {
                "node_id": node_id,
                "source_ids": list(source_ids),
                "new_content": new_content,
                "new_confidence": new_confidence,
                "valence": valence,
                "ts": ts,
                "new_node_id": new_node_id,
            }
        )
        old = self._nodes.get(node_id)
        if old is not None:
            old.lifecycle_state = "superseded"
        self._nodes[new_node_id] = _StubNode(
            {
                "node_id": new_node_id,
                "content": new_content,
                "confidence": new_confidence,
                "valence": valence,
                "lifecycle_state": "active",
            }
        )


def _pipeline(
    engine: _StubEngine,
    backend: Any,
    agent_id: str = "agent-a",
    **kwargs: Any,
) -> ContradictionPipeline:
    """組一支接線層（**每次用新計數器** ＝ 新的 (agent, 當日) 預算）。"""
    return ContradictionPipeline(
        engine=engine,
        backend=backend,
        calls=new_call_counter(),
        agent_id=agent_id,
        **kwargs,
    )


def _same_bits(left: float, right: float) -> bool:
    """浮點數**逐位元**比較（信心度繼承必須是位元級等價，不是近似）。"""
    return struct.pack(">d", float(left)) == struct.pack(">d", float(right))


# ─────────────────────────────────────────────────────────────────────
# K1～K5：結構預濾器的七道條件
# ─────────────────────────────────────────────────────────────────────


def test_k1_cross_agent_pairs_are_fully_eliminated():
    """K1：跨 agent 的配對被 **100%** 剔除（同 agent 的對照組必須被選中）。"""
    soul = _soul(node_id="soul-1", agent_id="agent-a")
    same_agent = _pattern(node_id="pat-same", agent_id="agent-a")
    cross_agent = _pattern(node_id="pat-cross", agent_id="agent-b")
    cross_agent2 = _pattern(node_id="pat-cross-2", agent_id="agent-c")

    assert select_conflict_candidate_pairs([soul], [cross_agent]) == {}
    assert select_conflict_candidate_pairs([soul], [cross_agent2]) == {}

    result = select_conflict_candidate_pairs(
        [soul], [same_agent, cross_agent, cross_agent2]
    )
    assert sorted(result.keys()) == ["agent-a"], "跨 agent 的配對必須 100% 剔除"
    assert len(result["agent-a"]) == 1
    assert result["agent-a"][0][0] is soul
    assert result["agent-a"][0][1] is same_agent, "同 agent 的對照組必須被選中"


def test_k2_dimension_mismatch_pairs_are_fully_eliminated():
    """K2：候選維度不符的配對被 **100%** 剔除。"""
    soul = _soul(node_id="soul-1", node_type="value")  # 維度 = value
    wrong = _pattern(node_id="pat-trait", dimension="trait")
    right = _pattern(node_id="pat-value", dimension="value")

    assert select_conflict_candidate_pairs([soul], [wrong]) == {}

    result = select_conflict_candidate_pairs([soul], [wrong, right])
    assert len(result["agent-a"]) == 1
    assert result["agent-a"][0][1] is right, "維度不符者不得被選中"
    assert result["agent-a"][0][1] is not wrong


def test_k3_same_utc_date_pairs_are_fully_eliminated():
    """K3：**同一 UTC 日期**的配對被 100% 剔除（跨時區以 UTC 口徑判斷）。"""
    soul = _soul(created_ts="2026-10-01T10:00:00Z")
    same_day = _pattern(node_id="pat-same-day", created_ts="2026-10-01T23:30:00Z")
    later = _pattern(node_id="pat-later", created_ts="2026-10-03T10:00:00Z")
    # 本地仍是 10-01，但 UTC 已是 10-02 ⇒ 以 UTC 口徑視為**跨日**。
    tz_shift = _pattern(node_id="pat-tz", created_ts="2026-10-01T23:00:00-05:00")

    assert select_conflict_candidate_pairs([soul], [same_day]) == {}

    result = select_conflict_candidate_pairs([soul], [same_day, later])
    assert len(result["agent-a"]) == 1
    assert result["agent-a"][0][1] is later

    result_tz = select_conflict_candidate_pairs([soul], [tz_shift])
    assert len(result_tz["agent-a"]) == 1
    assert result_tz["agent-a"][0][1] is tz_shift, "跨日判定必須用 UTC 口徑"


def test_k4_normalized_equal_content_pairs_are_fully_eliminated():
    """K4：內容**正規化後相等**的配對被 100% 剔除。"""
    soul = _soul(content="他喜歡喝咖啡。")
    punct_only = _pattern(node_id="pat-punct", content="他喜歡喝咖啡!")
    space_only = _pattern(node_id="pat-space", content=" 他喜歡喝咖啡 ")
    real_diff = _pattern(node_id="pat-real", content="他其實不喜歡喝咖啡")

    assert normalize_content("他喜歡喝咖啡。") == normalize_content("他喜歡喝咖啡!")
    assert normalize_content("他喜歡喝咖啡。") == normalize_content(" 他喜歡喝咖啡 ")

    assert select_conflict_candidate_pairs([soul], [punct_only]) == {}
    assert select_conflict_candidate_pairs([soul], [space_only]) == {}

    result = select_conflict_candidate_pairs(
        [soul], [punct_only, space_only, real_diff]
    )
    assert len(result["agent-a"]) == 1
    assert result["agent-a"][0][1] is real_diff


def test_k5_one_pair_per_agent_lexicographically_smallest():
    """K5：單一 agent 多組候選 ⇒ **恰好 1 組**，且是字典序最小者。"""
    soul_b = _soul(node_id="soul-b")
    soul_a = _soul(node_id="soul-a")
    pat_1 = _pattern(node_id="pat-1")
    pat_2 = _pattern(node_id="pat-2")

    result = select_conflict_candidate_pairs([soul_b, soul_a], [pat_1, pat_2])
    assert list(result.keys()) == ["agent-a"]
    assert len(result["agent-a"]) == 1, "每 agent 至多 1 對"

    chosen_soul, chosen_pattern = result["agent-a"][0]
    assert chosen_soul["node_id"] == "soul-a", "必須取字典序最小者"
    assert chosen_pattern["node_id"] == "pat-1", "必須取字典序最小者"

    # 決定性：輸入順序顛倒 ⇒ 輸出逐位元相同（預濾器是純函式）。
    reversed_result = select_conflict_candidate_pairs(
        [soul_a, soul_b], [pat_2, pat_1]
    )
    assert reversed_result == result


# ─────────────────────────────────────────────────────────────────────
# K6～K10：接線層（fail-closed、成本上限、預算、參數、閉環）
# ─────────────────────────────────────────────────────────────────────


def test_k6_compatible_verdict_records_nothing():
    """K6：backend 回 ``COMPATIBLE`` ⇒ ``record_contradiction()`` 呼叫數恆為 0。"""
    backend = _MockBackend(response="COMPATIBLE")
    engine = _StubEngine([_soul()])
    outcome = _pipeline(engine, backend).run(
        soul_node=_soul(),
        candidate_pattern=_pattern(),
        ts="2026-10-05T09:30:00Z",
    )

    assert len(backend.calls) == 1
    assert outcome.verdict == VERDICT_COMPATIBLE
    assert outcome.should_record is False
    assert outcome.recorded is False
    assert outcome.superseded is False
    assert outcome.assessment is None
    assert engine.record_calls == [], "COMPATIBLE ⇒ 0 次寫入"
    assert engine.supersede_calls == []


def test_k7_backend_exception_and_cost_cap_fail_closed():
    """K7：backend 拋例外 / prompt 超過成本上限 ⇒ 一律 fail-closed、0 寫入。"""
    # (a) backend 拋例外 ⇒ 不冒泡、不記錄。
    backend = _MockBackend(error=RuntimeError("boom"))
    engine = _StubEngine([_soul()])
    outcome = _pipeline(engine, backend).run(
        soul_node=_soul(),
        candidate_pattern=_pattern(),
        ts="2026-10-05T09:30:00Z",
    )
    assert outcome.should_record is False
    assert outcome.recorded is False
    assert outcome.superseded is False
    assert outcome.reason == REASON_BACKEND_ERROR
    assert engine.record_calls == [], "backend 失敗 ⇒ 0 次寫入"
    assert engine.supersede_calls == []

    # (b) prompt 超過成本上限 ⇒ **連 backend 都不呼叫**（成本護欄）。
    backend2 = _MockBackend(response="CONTRADICT")
    engine2 = _StubEngine([_soul()])
    outcome2 = _pipeline(engine2, backend2, max_prompt_chars=80).run(
        soul_node=_soul(content="很長的既有命題" * 20),
        candidate_pattern=_pattern(content="很長的新候選命題" * 20),
        ts="2026-10-05T09:30:00Z",
    )
    assert backend2.calls == [], "成本上限 ⇒ 不呼叫 backend"
    assert outcome2.should_record is False
    assert outcome2.recorded is False
    assert outcome2.reason == REASON_COST_LIMIT
    assert engine2.record_calls == []
    assert engine2.supersede_calls == []


def test_k8_second_call_same_agent_same_day_is_budget_exhausted():
    """K8：同一 agent 當日第二次 ⇒ 預算用盡，backend 呼叫次數**不增加**。"""
    calls = new_call_counter()
    backend = _MockBackend(response="COMPATIBLE")
    engine = _StubEngine([_soul()])
    pipeline = ContradictionPipeline(
        engine=engine, backend=backend, calls=calls, agent_id="agent-a"
    )

    first = pipeline.run(
        soul_node=_soul(),
        candidate_pattern=_pattern(),
        ts="2026-10-05T09:00:00Z",
    )
    assert first.judge_outcome is not None
    assert first.judge_outcome.backend_invoked is True
    assert len(backend.calls) == 1

    second = pipeline.run(
        soul_node=_soul(),
        candidate_pattern=_pattern(node_id="pat-2"),
        ts="2026-10-05T21:00:00Z",
    )
    assert second.reason == REASON_BUDGET_EXHAUSTED
    assert second.should_record is False
    assert second.recorded is False
    assert second.judge_outcome is not None
    assert second.judge_outcome.backend_invoked is False
    assert len(backend.calls) == 1, "預算用盡 ⇒ backend 呼叫次數不增加"
    assert calls["budget_exhausted"] == 1


def test_k9_contradict_records_exactly_once_with_exact_arguments():
    """K9：backend 回 ``CONTRADICT`` ⇒ ``record_contradiction()`` **恰好 1 次**，參數逐項正確。"""
    backend = _MockBackend(response="CONTRADICT")
    soul = _soul(node_id="soul-9")
    pattern = _pattern(node_id="pat-9", source_ids=("src-contradiction-9",))
    engine = _StubEngine([soul])

    outcome = _pipeline(engine, backend).run(
        soul_node=soul,
        candidate_pattern=pattern,
        ts="2026-10-05T09:30:00Z",
        event_identity="evt-9",
    )

    assert outcome.verdict == VERDICT_CONTRADICT
    assert outcome.should_record is True
    assert outcome.recorded is True
    assert len(engine.record_calls) == 1, "恰好 1 次寫入"

    call = engine.record_calls[0]
    assert call["node_id"] == "soul-9"
    assert call["source_id"] == "src-contradiction-9"
    assert call["event_identity"] == "evt-9"
    assert call["ts"] == "2026-10-05T09:30:00Z"

    # 未達門檻 ⇒ 不 supersede（且累積器自檢結果必須存在、可稽核）。
    assert outcome.superseded is False
    assert engine.supersede_calls == []
    assert outcome.assessment is not None
    assert outcome.assessment.record_count == 1
    assert outcome.assessment.independent_contradictions == 1


def test_k10_three_days_of_contradictions_trigger_supersede():
    """K10：3 個不同日期的矛盾證據 ⇒ ``supersede()`` 被觸發；舊節點轉 SUPERSEDED；
    **新節點的信心度逐位元等於舊節點的信心度**。"""
    soul = _soul(node_id="soul-10", confidence=0.83, valence="negative")
    engine = _StubEngine([soul])
    pattern = _pattern(node_id="pat-10", source_ids=("src-d1",))

    outcomes = []
    for day, source_id in (
        ("2026-10-01", "src-d1"),
        ("2026-10-02", "src-d2"),
        ("2026-10-03", "src-d3"),
    ):
        backend = _MockBackend(response="CONTRADICT")
        outcome = _pipeline(engine, backend).run(
            soul_node=soul,
            candidate_pattern=dict(pattern, source_ids=[source_id]),
            ts=f"{day}T12:00:00Z",
            event_identity=f"evt-{day}",
        )
        outcomes.append(outcome)

    assert [o.recorded for o in outcomes] == [True, True, True]
    assert [o.superseded for o in outcomes] == [False, False, True]
    assert len(engine.record_calls) == 3
    assert len(engine.supersede_calls) == 1

    call = engine.supersede_calls[0]
    assert call["node_id"] == "soul-10"
    assert call["new_content"] == pattern["content"]
    assert call["valence"] == "negative", "valence 必須原樣帶入（引擎必填參數）"
    assert call["source_ids"] == ["src-d3"]

    assert engine._nodes["soul-10"].lifecycle_state == "superseded"

    new_node = engine._nodes[call["new_node_id"]]
    assert new_node.confidence == soul["confidence"]
    assert _same_bits(new_node.confidence, soul["confidence"]), (
        "新節點的信心度必須**逐位元**繼承舊節點"
    )
    assert new_node.valence == "negative"
