"""
tests/test_m3_4_priority_semantic_boundary.py — M3.4 Priority Semantic Boundary

Bry 派工 2026-08-08 M3.4:
驗證 M3.2 priority_boost 是 Awareness enrichment, 不是 Agency override。

核心問題:
  priority 可以讓事件「更值得注意」
  但不能讓低品質事件無條件變成 ACCEPT
  也不能破壞既有 novelty / threshold semantics

Success Definition:
  M3.4 = prove Awareness ≠ Agency.

架構:
  ┌──────────┐
  │ Priority │
  └─────┬────┘
        ▼
  ┌──────────────┐
  │  Perception  │   <- priority 只能在這層加 additive score
  │   Enrichment │
  └──────┬───────┘
         │
   ┌─────┼─────┐
   ▼     ▼     ▼
Relevance Novelty Context   <- 既有 5 維度 scoring 邏輯
   │     │     │
   └─────┼─────┘
         ▼
    Acceptance              <- 仍由 scoring + threshold 決定
         │
         ▼
      Agency

本檔只做 integration test, 不修改任何 production code, 不 mock scoring/acceptance。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List

import pytest

from src.world import (
    DEFAULT_ACCEPT_THRESHOLD,
    PerceptionScores,
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    compute_scores,
    should_accept,
)
from src.world.perception import PRIORITY_BOOST_WEIGHT, SCORE_WEIGHTS


# ───────────────────────────────────────────────────────────
# Test helpers
# ───────────────────────────────────────────────────────────


def _make_event(
    *,
    source: str = "weather",
    type_: str = "rain_started",
    novelty_id: str = "test_event_001",
    summary: str = "外面開始下雨了。",
    priority: int = 0,
    data: dict = None,
) -> WorldEvent:
    """建一個 WorldEvent 給 M3.4 test 用, default 跟既有 M3.2 test 對齊。"""
    if data is None:
        data = {"precipitation_mm": 2.5, "intensity": "light"}
    return WorldEvent(
        source=source,
        type=type_,
        novelty_id=novelty_id,
        ts=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        data=data,
        priority=priority,
    )


def _legacy_5_weighted(s: PerceptionScores) -> float:
    """重算 5 維度 legacy 加權 score, 跟 PerceptionScores.final() 的 legacy 部分對齊。"""
    return (
        SCORE_WEIGHTS["relevance"] * s.relevance
        + SCORE_WEIGHTS["novelty"] * s.novelty
        + SCORE_WEIGHTS["personal_significance"] * s.personal_significance
        + SCORE_WEIGHTS["emotional_significance"] * s.emotional_significance
        + SCORE_WEIGHTS["temporal_significance"] * s.temporal_significance
    )


# ───────────────────────────────────────────────────────────
# Case 5 — Duplicate novelty + high priority
# ───────────────────────────────────────────────────────────


def test_5a_priority_boost_present_with_duplicate_novelty():
    """Case 5a: 同一 novelty_id 第二次, priority_boost 仍然存在 (priority 不被 novelty 蓋掉)。

    證明 priority_boost 跟 novelty 維度獨立運算。
    """
    event = _make_event(novelty_id="dup_001", priority=20)
    s1 = compute_scores(event, novelty_count=1, event_priority=20)
    s2 = compute_scores(event, novelty_count=2, event_priority=20)

    # novelty 維度照公式下降 (1/1=1.0 → 1/2=0.5)
    assert s1.novelty == 1.0
    assert s2.novelty == 0.5
    # priority_boost 兩次都 = 1.0, 不受 novelty_count 影響
    assert s1.priority_boost == 1.0
    assert s2.priority_boost == 1.0
    # 兩次的 priority contribution 都是 0.05 (= PRIORITY_BOOST_WEIGHT * 1.0)
    assert PRIORITY_BOOST_WEIGHT * s1.priority_boost == PRIORITY_BOOST_WEIGHT * s2.priority_boost


def test_5b_novelty_decay_visible_in_final_score_despite_high_priority():
    """Case 5b: priority=20 不會讓 duplicate 的 final score 跟第一次一樣。

    novelty_decay 必須反映在 final score, 不被 priority 覆寫。
    第二次 event 的 final 必須嚴格 < 第一次 event 的 final。
    """
    event = _make_event(novelty_id="dup_002", priority=20)
    s1 = compute_scores(event, novelty_count=1, event_priority=20)
    s2 = compute_scores(event, novelty_count=2, event_priority=20)

    # 第二次的 final 必須嚴格 < 第一次
    assert s2.final() < s1.final(), (
        f"novelty decay 沒反映在 final score: "
        f"s1.final()={s1.final():.4f}, s2.final()={s2.final():.4f}"
    )
    # 差異量 = (1.0 - 0.5) * 0.20 (novelty weight) = 0.10
    expected_drop = (1.0 - 0.5) * SCORE_WEIGHTS["novelty"]
    actual_drop = s1.final() - s2.final()
    assert abs(actual_drop - expected_drop) < 1e-9, (
        f"final score drop 應該 = {expected_drop:.4f}, 實際 = {actual_drop:.4f}"
    )


def test_5c_state_add_tracks_duplicate_count_independent_of_priority():
    """Case 5c: WorldPerceptionState.add() 對 priority=20 的事件仍然正確累加 novelty_count。

    證明 state layer 不知道 priority 存在, duplicate detection 邏輯 0 變動。
    """
    state = WorldPerceptionState()
    ev1 = _make_event(novelty_id="dup_state_001", priority=20)
    ev2 = _make_event(novelty_id="dup_state_001", priority=20)
    ev3 = _make_event(novelty_id="dup_state_001", priority=20)

    c1 = state.add(ev1)
    c2 = state.add(ev2)
    c3 = state.add(ev3)

    assert c1 == 1, f"第一次 add 應回 1, 實際 {c1}"
    assert c2 == 2, f"第二次 add 應回 2, 實際 {c2}"
    assert c3 == 3, f"第三次 add 應回 3, 實際 {c3}"
    # 對照組: priority=0 的同一 novelty_id
    state_zero = WorldPerceptionState()
    for ev in [
        _make_event(novelty_id="dup_state_002", priority=0),
        _make_event(novelty_id="dup_state_002", priority=0),
    ]:
        state_zero.add(ev)
    # priority=0 跟 priority=20 對 state.count 完全沒差
    assert state.get_novelty_count("dup_state_001") == 3
    assert state_zero.get_novelty_count("dup_state_002") == 2


# ───────────────────────────────────────────────────────────
# Case 6 — Low quality + high priority
# ───────────────────────────────────────────────────────────


def test_6a_priority_boost_is_present_but_bounded():
    """Case 6a: priority=20 一定會給 priority_boost=1.0, 但 contribution 永遠 ≤ PRIORITY_BOOST_WEIGHT (0.05)。

    證明 priority 是 bounded additive signal, 不可能 dominance legacy 5 維度。
    """
    # 用 celebrity_news (lowest baseline relevance) + 重複 (low novelty)
    event = _make_event(
        source="news", type_="celebrity_news",
        novelty_id="ce_001", priority=20,
        summary="某明星新戀情曝光",
    )
    s = compute_scores(event, novelty_count=2, event_priority=20)
    assert s.priority_boost == 1.0
    priority_contribution = PRIORITY_BOOST_WEIGHT * s.priority_boost
    assert priority_contribution == PRIORITY_BOOST_WEIGHT  # = 0.05
    # 對照: 既有 5 維度中, 最小維度權重 0.10 (emotional) 已經是 priority 的 2 倍
    assert SCORE_WEIGHTS["emotional_significance"] > PRIORITY_BOOST_WEIGHT


def test_6b_low_quality_high_priority_still_rejected():
    """Case 6b: 真正的低品質事件 + priority=20 仍然 reject (Awareness ≠ Agency)。

    設計: celebrity_news (lowest baseline relevance) + novelty_count=2 (low novelty)
    既有 5 維度 legacy 算下來 < 0.30, 即使 +0.05 priority boost 仍 < 0.35 threshold。
    """
    event = _make_event(
        source="news", type_="celebrity_news",
        novelty_id="ce_reject_001", priority=20,
        summary="某明星新戀情曝光",
    )
    s = compute_scores(
        event, novelty_count=2, event_priority=20,
        current_user_context_keywords=None,  # 沒 user context
        temporal_salience="low", anticipatory_flavor="none",
        vulnerability_window=False, silence_hours=0.0,
    )
    # priority_boost 確實進場
    assert s.priority_boost == 1.0
    # legacy < 0.30 (證明: 0.30*0.05 + 0.20*0.5 + 0.25*0.2 + 0.10*0.2 + 0.15*0.3 = 0.23)
    legacy = _legacy_5_weighted(s)
    assert legacy < 0.30, f"預期 legacy < 0.30, 實際 {legacy:.4f}"
    # final < threshold (即使 +0.05 priority boost 也不夠)
    assert s.final() < DEFAULT_ACCEPT_THRESHOLD
    # acceptance decision: reject
    accepted, reason = should_accept(s, threshold=DEFAULT_ACCEPT_THRESHOLD)
    assert not accepted
    # reason 必須提及 threshold + final_score, 不提 priority 直接 accept
    assert "threshold=0.35" in reason
    assert "final_score=" in reason


def test_6c_low_quality_baseline_rejection_control():
    """Case 6c: 對照組 — 同一低品質事件但 priority=0, 仍然 reject (證明 Case 6b 的 reject 不是 priority-driven)。

    同一個 event type + 同一個 novelty_count, 差別只在 priority=0 vs priority=20,
    兩個都 reject, 證明 priority 並沒有偷偷把 event 救起來。
    """
    event_p20 = _make_event(
        source="news", type_="celebrity_news",
        novelty_id="ce_control_001", priority=20,
        summary="某明星新戀情曝光",
    )
    event_p0 = _make_event(
        source="news", type_="celebrity_news",
        novelty_id="ce_control_002", priority=0,
        summary="某明星新戀情曝光",
    )
    s_p20 = compute_scores(
        event_p20, novelty_count=2, event_priority=20,
        current_user_context_keywords=None, temporal_salience="low",
    )
    s_p0 = compute_scores(
        event_p0, novelty_count=2, event_priority=0,
        current_user_context_keywords=None, temporal_salience="low",
    )
    # 對照: priority=0 的 priority_boost=0, final 純 legacy
    assert s_p0.priority_boost == 0.0
    # 兩個都 reject
    a_p20, _ = should_accept(s_p20)
    a_p0, _ = should_accept(s_p0)
    assert not a_p20
    assert not a_p0
    # priority_boost 在 priority=20 的 case 確實被加進 final
    assert s_p20.final() > s_p0.final()
    # 但 +0.05 仍不足以越過 threshold
    assert s_p20.final() < DEFAULT_ACCEPT_THRESHOLD


# ───────────────────────────────────────────────────────────
# Case 7 — High quality + low priority (priority=0)
# ───────────────────────────────────────────────────────────


def test_7a_priority_zero_final_equals_legacy_5_weighted():
    """Case 7a: priority=0 → priority_boost=0 → final 純 legacy 5 維度加權。

    既有 scoring 100% 保留, priority 完全不參與。
    """
    event = _make_event(
        source="calendar", type_="calendar_event",
        novelty_id="cal_001", priority=0,
        summary="30 分鐘後有會議",
    )
    s = compute_scores(
        event, novelty_count=1, event_priority=0,
        current_user_context_keywords=["會議", "meeting", "calendar"],
        temporal_salience="high", anticipatory_flavor="none",
        vulnerability_window=False,
    )
    assert s.priority_boost == 0.0
    legacy = _legacy_5_weighted(s)
    # final == legacy (因為 priority_contribution = 0)
    assert abs(s.final() - legacy) < 1e-9, (
        f"priority=0 應 final == legacy, 實際 final={s.final():.4f} legacy={legacy:.4f}"
    )


def test_7b_high_quality_priority_zero_accepted_via_legacy():
    """Case 7b: 高品質 + priority=0, 既有 legacy 5 維度已足夠 accept (priority 不參與 acceptance 決定)。

    證明 acceptance 完全是既有 scoring + threshold 邏輯的結果, priority 不 carry 決定性角色。
    """
    event = _make_event(
        source="calendar", type_="calendar_event",
        novelty_id="cal_002", priority=0,
        summary="30 分鐘後有會議",
    )
    s = compute_scores(
        event, novelty_count=1, event_priority=0,
        current_user_context_keywords=["會議", "meeting", "calendar"],
        temporal_salience="high",
    )
    # legacy 自己就 ≥ threshold
    legacy = _legacy_5_weighted(s)
    assert legacy >= DEFAULT_ACCEPT_THRESHOLD
    accepted, reason = should_accept(s)
    assert accepted
    # reason 必須提及 final_score >= threshold, pri=0.00
    assert "final_score=" in reason
    assert "pri=0.00" in reason


def test_7c_high_quality_priority_increases_but_does_not_change_decision():
    """Case 7c: 對照 — 同一高品質事件 + priority=20, final 比 priority=0 高但 accept 決策不變。

    證明 priority 對 acceptance decision 沒有「跨過/不跨過」邊界的效果,
    它只能把已經 accept 的事件推得更高 (排名影響), 已經 reject 的事件救不起來。
    """
    base_kwargs = dict(
        source="calendar", type_="calendar_event",
        summary="30 分鐘後有會議",
    )
    ev_p0 = _make_event(novelty_id="cal_p0", priority=0, **base_kwargs)
    ev_p20 = _make_event(novelty_id="cal_p20", priority=20, **base_kwargs)

    user_ctx = ["會議", "meeting", "calendar"]
    s_p0 = compute_scores(
        ev_p0, novelty_count=1, event_priority=0,
        current_user_context_keywords=user_ctx, temporal_salience="high",
    )
    s_p20 = compute_scores(
        ev_p20, novelty_count=1, event_priority=20,
        current_user_context_keywords=user_ctx, temporal_salience="high",
    )

    # priority=20 的 final 嚴格 > priority=0 的 final
    assert s_p20.final() > s_p0.final()
    # 差 = PRIORITY_BOOST_WEIGHT * 1.0 = 0.05
    assert abs(s_p20.final() - s_p0.final() - PRIORITY_BOOST_WEIGHT) < 1e-9
    # 兩個都 accept (因為 legacy 已經夠)
    a_p0, _ = should_accept(s_p0)
    a_p20, _ = should_accept(s_p20)
    assert a_p0 and a_p20


# ───────────────────────────────────────────────────────────
# Invariants I1–I8
# ───────────────────────────────────────────────────────────


def test_I1_priority_zero_final_equals_legacy_final():
    """I1: priority=0 → final == legacy final (Awareness 0 介入)。"""
    # 跨多個 event type 都成立
    for type_ in ("rain_started", "calendar_event", "user_going_outside", "celebrity_news"):
        for nc in (1, 2, 3):
            event = _make_event(type_=type_, novelty_id=f"i1_{type_}_{nc}", priority=0)
            s = compute_scores(event, novelty_count=nc, event_priority=0)
            legacy = _legacy_5_weighted(s)
            assert abs(s.final() - legacy) < 1e-9, (
                f"I1 failed: type={type_} nc={nc} "
                f"final={s.final():.6f} legacy={legacy:.6f}"
            )


def test_I2_priority_does_not_modify_legacy_5_dimensions():
    """I2: priority 只能加 score, 不能修改既有 5 維度。

    同一 event, priority=0 vs priority=20, 5 維度數值必須逐項完全相同。
    """
    event = _make_event(novelty_id="i2_001")
    s0 = compute_scores(event, novelty_count=2, event_priority=0)
    s20 = compute_scores(event, novelty_count=2, event_priority=20)

    # 5 維度逐項對比
    for dim in (
        "relevance", "novelty", "personal_significance",
        "emotional_significance", "temporal_significance",
    ):
        v0 = getattr(s0, dim)
        v20 = getattr(s20, dim)
        assert v0 == v20, f"I2 failed: {dim} differs (priority=0: {v0}, priority=20: {v20})"
    # 只有 priority_boost 應該不同
    assert s0.priority_boost == 0.0
    assert s20.priority_boost == 1.0


def test_I3_priority_does_not_modify_threshold():
    """I3: priority 不改寫 accept_threshold。

    同一 scoring (legacy 5 dims + priority_boost) 用同一個 threshold 評,
    priority 不能讓 should_accept 看到不同的 threshold。
    """
    event = _make_event(novelty_id="i3_001", priority=20)
    s = compute_scores(event, novelty_count=2, event_priority=20)
    # reason 必須包含 "threshold=0.35" (DEFAULT_ACCEPT_THRESHOLD), 不是 priority-related
    accepted, reason = should_accept(s)
    assert DEFAULT_ACCEPT_THRESHOLD == 0.35
    assert f"threshold={DEFAULT_ACCEPT_THRESHOLD:.2f}" in reason
    # 同一 event 用 priority=0 評, threshold 仍 0.35
    s0 = compute_scores(event, novelty_count=2, event_priority=0)
    _, reason0 = should_accept(s0)
    assert f"threshold={DEFAULT_ACCEPT_THRESHOLD:.2f}" in reason0


def test_I4_priority_does_not_bypass_novelty_decay():
    """I4: priority 不能繞過 novelty decay (1/n 公式必須保持)。

    對任意 priority ∈ {0, 5, 7, 10, 20, 100},
    novelty 維度都必須 = 1.0 / max(1, novelty_count), 跟 priority 無關。
    """
    for pri in (0, 5, 7, 10, 20, 100):
        for nc in (1, 2, 3, 5, 10):
            event = _make_event(novelty_id=f"i4_p{pri}_n{nc}", priority=pri)
            s = compute_scores(event, novelty_count=nc, event_priority=pri)
            expected_novelty = 1.0 / nc
            assert abs(s.novelty - expected_novelty) < 1e-9, (
                f"I4 failed: priority={pri}, novelty_count={nc}: "
                f"expected novelty={expected_novelty:.4f}, got {s.novelty:.4f}"
            )


def test_I5_priority_does_not_bypass_duplicate_semantics():
    """I5: priority 不能 reset 既有 duplicate detection (state._novelty_index)。

    state.add() 對 priority 的大小/正負零感知,
    priority=20 的事件在 duplicate counting 上跟 priority=0 沒差別。
    """
    # 兩條獨立 novelty_id, 各自 add 一次
    state = WorldPerceptionState()
    state.add(_make_event(novelty_id="i5_p20", priority=20))
    state.add(_make_event(novelty_id="i5_p20", priority=20))  # duplicate
    state.add(_make_event(novelty_id="i5_p0", priority=0))
    state.add(_make_event(novelty_id="i5_p0", priority=0))    # duplicate
    state.add(_make_event(novelty_id="i5_neg", priority=-5))  # 負 priority 也算 duplicate
    state.add(_make_event(novelty_id="i5_neg", priority=-5))

    assert state.get_novelty_count("i5_p20") == 2
    assert state.get_novelty_count("i5_p0") == 2
    assert state.get_novelty_count("i5_neg") == 2


def test_I6_acceptance_decision_driven_by_scoring_threshold():
    """I6: acceptance 仍由既有 scoring + threshold 決定, priority 不 carry 決定性角色。

    reason 必須提及 final_score + threshold, 沒有 "priority accept" 之類的捷徑語意。
    """
    event = _make_event(novelty_id="i6_001", priority=20)
    s = compute_scores(event, novelty_count=2, event_priority=20)
    accepted, reason = should_accept(s)
    # reason 必須是「final vs threshold」的格式, 沒有「因為 priority 所以 accept」之類的 bypass 邏輯
    assert "final_score=" in reason
    assert "threshold=" in reason
    # 沒有 priority 捷徑字眼
    forbidden_phrases = [
        "priority_override", "priority_accept", "bypass_threshold",
        "force_accept", "priority_agency",
    ]
    for phrase in forbidden_phrases:
        assert phrase not in reason.lower(), (
            f"I6 failed: reason 含 priority-bypass 暗示語意: {phrase!r}"
        )


def test_I7_worldevent_payload_contract_unchanged():
    """I7: WorldEvent 序列化契約 (to_payload) — M5.4-3.1 升級後 priority 為 additive 欄位。

    M5.4-3.1 contract repair (Bry 派工 2026-08-09 17:43, 見 src/world/perception.py
    to_payload/from_payload): WorldEvent.priority 正式納入 bus-payload 序列化,
    讓 M3.2-A priority_boost 在 E2E path 上恢復作用。向後相容: 舊 payload
    (無 priority key) 仍可 round-trip (fallback 0), 既有 5 維度 scoring 0 變更。
    本測試對齊 M5.4-3.1: 驗證 priority 完整 round-trip (payload 含 priority,
    from_payload 還原同值)。
    """
    event = _make_event(
        source="weather", type_="rain_started",
        novelty_id="i7_001", priority=20,
    )
    payload = event.to_payload()
    # M5.4-3.1: payload 必須含 priority (additive 欄位, 向下相容)
    assert payload["priority"] == 20, f"I7 failed: payload 缺 priority=20 ({payload})"
    # 反向: from_payload 還原 priority (M5.4-3.1 round-trip 保證)
    restored = WorldEvent.from_payload(payload)
    assert restored.priority == 20, (
        f"I7 failed: from_payload 未還原 priority, got {restored.priority}"
    )
    # 向後相容: 舊 payload (無 priority key) → fallback 0, 既有行為 100% 保留
    legacy_payload = {k: v for k, v in payload.items() if k != "priority"}
    legacy_restored = WorldEvent.from_payload(legacy_payload)
    assert legacy_restored.priority == 0


def test_I8_trace_observability_intact(monkeypatch, tmp_path):
    """I8: middleware 寫出的 trace 仍然含 scores.priority_boost 跟 extra.world_event_priority。

    透過 mock writer 收集 trace, 確認兩個欄位都在,
    證明 observability pipeline 沒被 M3.2 之後任何改動破壞。
    """
    from src.eventbus.schema import EventType, SoulEvent
    from src.world.trace import WorldPerceptionTraceWriter

    captured_traces: list = []

    class _CollectingWriter:
        def __init__(self): pass
        def write(self, trace):
            captured_traces.append(trace)
            return True
        def clear(self):
            captured_traces.clear()

    class _MockBus:
        def subscribe(self, *a, **kw): pass
        def unsubscribe(self, *a, **kw): pass
        async def publish(self, *a, **kw): pass
        async def start(self): pass
        async def stop(self): pass

    mw = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=WorldPerceptionState(),
        trace_writer=_CollectingWriter(),
    )
    enriched = SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source="test", target="broadcast",
        payload={"agent_id": "agent_test", "draft": "下雨了", "text": "下雨了",
                 "chrono_context": ""},
    )
    ev = _make_event(
        source="weather", type_="rain_started",
        novelty_id="weather_rain_i8_001", priority=20,
        summary="外面開始下雨了",
    )
    mw.state.add(ev)
    asyncio.run(mw._on_agent_intent_enriched(enriched))

    # 至少一條 evaluated trace
    evaluated = [
        t for t in captured_traces
        if t.extra.get("phase") == "evaluated"
    ]
    assert len(evaluated) >= 1, f"沒 evaluated trace, 全部: {captured_traces}"
    t = evaluated[0]
    # scores.priority_boost 必須存在
    assert hasattr(t, "scores")
    assert t.scores.priority_boost == 1.0  # priority=20 → boost=1.0
    # extra.world_event_priority 必須存在
    assert "world_event_priority" in t.extra
    assert t.extra["world_event_priority"] == 20
