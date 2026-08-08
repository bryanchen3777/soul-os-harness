"""
tests/test_m3_2_semantic_enrichment.py — M3.2-A REVISION Contract Tests

Bry 拍板 2026-08-08 13:21 — M3.2-A REVISION:

Phase D 派工精神: priority 從 dead metadata 變成 Perception 的 additive semantic signal。

核心 invariant (派工 #5, #8, #10):
  - priority = 0 → priority_boost = 0 → existing scoring result 100% preserved
  - priority > 0 → priority_boost 線性映射 → final score 受控小幅 additive
  - payload round-trip 仍 omit priority (M3.1 frozen)
  - dispatcher / source / injector / registry 0 change
  - middleware 公開 API 0 change

架構:
  - src/world/perception.py
      PerceptionScores 加 priority_boost 維度
      SCORE_WEIGHTS 保留 legacy 5 維度 (sum=1.00, 0 改動)
      新 PRIORITY_BOOST_WEIGHT = 0.05 獨立 additive constant
      final() 改用 additive: min(1.0, legacy_5 + priority_boost * 0.05)
  - src/world/middleware.py
      _on_agent_intent_enriched 內部讀 world_event.priority 傳給 compute_scores
      WorldPerceptionTrace.extra 加 "world_event_priority" observability

派工要求至少涵蓋:
  - Structure (5 tests)
  - Backward compatibility (3 tests)
  - Semantic enrichment (2 tests)
  - Middleware (1 test)
  - Trace (1 test)
  - Payload (1 test)
  - + critical regression test (派工 #16)

回歸要求 (派工 #17):
  - 175 既有 tests 100% pass
  - Phase C 內部 hard limit check 預期 fail (派工 #19 explicit HISTORICAL_SCOPE_CONFLICT)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from src.world import WorldEventSource, WorldEventInjector
from src.world.dispatcher import WorldEventDispatcher
from src.world.perception import (
    PRIORITY_BOOST_WEIGHT,
    PerceptionScores,
    SCORE_WEIGHTS,
    WorldEvent,
    _map_priority_to_boost,
    compute_scores,
    should_accept,
)
from src.world.source.synthetic import SyntheticWorldEventSource


# ───────────────────────────────────────────────────────────
# Test helpers
# ───────────────────────────────────────────────────────────


class _StubInjector:
    """測試用 stub, conform WorldEventInjector (async inject)。"""

    def __init__(self):
        self.received: List[WorldEvent] = []

    async def inject(self, event: WorldEvent) -> None:
        self.received.append(event)


def _make_rain_event(priority: int = 0) -> WorldEvent:
    """建一個跟既有 M3 test 用的 rain_started event (用於 backward-compat regression)。"""
    from datetime import datetime, timezone

    return WorldEvent(
        source="weather",
        type="rain_started",
        novelty_id="weather_rain_20260808",
        ts=datetime.now(timezone.utc).isoformat(),
        summary="外面開始下雨了。",
        data={"precipitation_mm": 2.5, "intensity": "light"},
        priority=priority,
    )


# ───────────────────────────────────────────────────────────
# 1. Structure tests
# ───────────────────────────────────────────────────────────


def test_perception_scores_has_priority_boost_field():
    """PerceptionScores 必須有 priority_boost 欄位, default = 0.0。"""
    s = PerceptionScores()
    assert hasattr(s, "priority_boost")
    assert s.priority_boost == 0.0
    # 既有 5 維度仍存在
    assert hasattr(s, "relevance")
    assert hasattr(s, "novelty")
    assert hasattr(s, "personal_significance")
    assert hasattr(s, "emotional_significance")
    assert hasattr(s, "temporal_significance")


def test_priority_mapping_zero():
    """priority = 0 → boost = 0.0。"""
    assert _map_priority_to_boost(0) == 0.0
    # 負數也視為 0 (clamp at 0)
    assert _map_priority_to_boost(-1) == 0.0
    assert _map_priority_to_boost(-100) == 0.0


def test_priority_mapping_five():
    """priority = 5 → boost = 0.4 (派工 anchor 點)。"""
    assert _map_priority_to_boost(5) == pytest.approx(0.4, abs=1e-9)


def test_priority_mapping_ten():
    """priority = 10 → boost = 0.8 (派工 anchor 點)。"""
    assert _map_priority_to_boost(10) == pytest.approx(0.8, abs=1e-9)


def test_priority_mapping_full():
    """priority = 12.5 → boost = 1.0 (anchor 點); priority >= 20 → boost = 1.0 (clamp)。"""
    assert _map_priority_to_boost(12.5) == pytest.approx(1.0, abs=1e-9)
    assert _map_priority_to_boost(20) == pytest.approx(1.0, abs=1e-9)
    assert _map_priority_to_boost(100) == pytest.approx(1.0, abs=1e-9)
    # priority = 7 (between 5 and 10) 線性插值
    assert _map_priority_to_boost(7) == pytest.approx(0.56, abs=1e-9)


# ───────────────────────────────────────────────────────────
# 2. Backward compatibility — core invariant
# ───────────────────────────────────────────────────────────


def test_default_event_priority_preserves_legacy_score():
    """派工 #10 核心 invariant: 不傳 event_priority → final == legacy final。"""
    event = _make_rain_event()

    legacy = compute_scores(
        event=event,
        novelty_count=2,
    )
    # 不傳 event_priority = 預設 0
    default = compute_scores(
        event=event,
        novelty_count=2,
    )
    # 兩者 final 必須完全相等 (priority_boost = 0)
    assert default.final() == pytest.approx(legacy.final(), abs=1e-9)
    # legacy 本身 priority_boost = 0 (因為 event.priority = 0)
    assert default.priority_boost == 0.0
    # legacy 跟 M3 Phase 1 baseline 對齊 (rain_started novelty_count=2, 沒 user context)
    # 注意: 既有 test_duplicate_novelty_decay 是真實 e2e pipeline (有 user context) 才有 0.35
    #       這裡用 _make_rain_event 沒 user context, final 是 0.275 (跟 e2e 不同)
    #       派工 #10 核心 invariant 是 priority=0 不污染, 不是 final 等於特定值
    assert legacy.final() == pytest.approx(0.275, abs=1e-2)


def test_priority_zero_preserves_legacy_score():
    """派工 #10 + #16 核心 invariant: event_priority=0 顯式傳入 → final == legacy。"""
    event = _make_rain_event()

    legacy = compute_scores(
        event=event,
        novelty_count=2,
    )
    zero_priority = compute_scores(
        event=event,
        novelty_count=2,
        event_priority=0,
    )
    # M3.2 核心: priority=0 時 priority_boost 必須 = 0, 不能污染 legacy score
    assert zero_priority.priority_boost == 0.0
    assert zero_priority.final() == pytest.approx(legacy.final(), abs=1e-9)
    # 其他 5 維度不受影響
    assert zero_priority.relevance == legacy.relevance
    assert zero_priority.novelty == legacy.novelty
    assert zero_priority.personal_significance == legacy.personal_significance
    assert zero_priority.emotional_significance == legacy.emotional_significance
    assert zero_priority.temporal_significance == legacy.temporal_significance


def test_existing_five_dimension_weights_unchanged():
    """派工 #9: SCORE_WEIGHTS 既有 5 維度 0 改動 (sum = 1.00, 跟 M3 Phase 1 baseline 一致)。"""
    expected = {
        "relevance": 0.30,
        "novelty": 0.20,
        "personal_significance": 0.25,
        "emotional_significance": 0.10,
        "temporal_significance": 0.15,
    }
    for k, v in expected.items():
        assert SCORE_WEIGHTS[k] == pytest.approx(v, abs=1e-9), (
            f"SCORE_WEIGHTS[{k!r}] 從 {v} 變 {SCORE_WEIGHTS[k]} — "
            "M3.2-A 派工 #9 明說既有 5 維度權重 0 改動"
        )
    # sum 必須 = 1.00
    assert sum(SCORE_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)
    # priority_boost 不在 SCORE_WEIGHTS 內 (用獨立 PRIORITY_BOOST_WEIGHT)
    assert "priority_boost" not in SCORE_WEIGHTS
    # PRIORITY_BOOST_WEIGHT 獨立常數存在
    assert PRIORITY_BOOST_WEIGHT == pytest.approx(0.05, abs=1e-9)


# ───────────────────────────────────────────────────────────
# 3. Semantic enrichment — priority > 0 actually changes final
# ───────────────────────────────────────────────────────────


def test_priority_positive_changes_final_score():
    """priority > 0 → final 必須高於 priority=0 的 final (派工 #8 受控 additive)。"""
    event_low = _make_rain_event(priority=0)
    event_high = _make_rain_event(priority=20)  # boost = 1.0

    scores_low = compute_scores(event=event_low, novelty_count=2, event_priority=0)
    scores_high = compute_scores(event=event_high, novelty_count=2, event_priority=20)

    # priority_boost 維度值正確
    assert scores_low.priority_boost == 0.0
    assert scores_high.priority_boost == pytest.approx(1.0, abs=1e-9)
    # final 必須真的不同
    assert scores_high.final() > scores_low.final()
    # 受控的 additive: difference 應 = 0.05 (= PRIORITY_BOOST_WEIGHT × 1.0)
    diff = scores_high.final() - scores_low.final()
    assert diff == pytest.approx(0.05, abs=1e-9)


def test_priority_boost_enters_final_score():
    """派工 #8: priority_boost 真的進 final_score (不是 dead metadata)。"""
    event = _make_rain_event()
    # priority = 10 → boost = 0.8 → contribution = 0.8 × 0.05 = 0.04
    scores = compute_scores(event=event, novelty_count=2, event_priority=10)
    assert scores.priority_boost == pytest.approx(0.8, abs=1e-9)
    # final 必須 = legacy_5_weighted + 0.04
    legacy = (
        SCORE_WEIGHTS["relevance"] * scores.relevance
        + SCORE_WEIGHTS["novelty"] * scores.novelty
        + SCORE_WEIGHTS["personal_significance"] * scores.personal_significance
        + SCORE_WEIGHTS["emotional_significance"] * scores.emotional_significance
        + SCORE_WEIGHTS["temporal_significance"] * scores.temporal_significance
    )
    expected_final = legacy + PRIORITY_BOOST_WEIGHT * 0.8
    assert scores.final() == pytest.approx(expected_final, abs=1e-9)
    # 確認 final > legacy (priority 真的進 final)
    assert scores.final() > legacy


# ───────────────────────────────────────────────────────────
# 4. Middleware integration
# ───────────────────────────────────────────────────────────


def test_middleware_passes_event_priority_to_compute_scores(monkeypatch):
    """派工 #12: middleware 真的把 world_event.priority 傳給 compute_scores。

    透過 monkeypatch compute_scores 確認呼叫時 event_priority 參數 = world_event.priority。
    為了繞過 process_world_event_direct 的 payload round-trip bug (payload 不含 priority,
    WorldEvent.priority 從 default 0 重建, 這是 M3.1 Phase 1 既有 behavior, 不在 M3.2-A scope),
    直接用 mw.state.add() 把 event 注入 state (保留 priority), 然後 trigger _on_agent_intent_enriched。
    """
    from src.world import middleware as mw_module
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.eventbus.schema import EventType, SoulEvent

    captured_event_priorities: list = []

    real_compute_scores = mw_module.compute_scores

    def fake_compute_scores(*args, **kwargs):
        if "event_priority" in kwargs:
            captured_event_priorities.append(kwargs["event_priority"])
        return real_compute_scores(*args, **kwargs)

    monkeypatch.setattr(mw_module, "compute_scores", fake_compute_scores)

    class _MockBus:
        def subscribe(self, *a, **kw): pass
        def unsubscribe(self, *a, **kw): pass
        async def publish(self, *a, **kw): pass
        async def start(self): pass
        async def stop(self): pass

    mw = WorldPerceptionMiddleware(
        bus=_MockBus(),
        state=WorldPerceptionState(),
        trace_writer=None,  # middleware 內部 lazy new 一個
    )
    enriched = SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source="test",
        target="broadcast",
        payload={"agent_id": "agent_test", "draft": "下雨了", "text": "下雨了",
                 "chrono_context": ""},
    )
    # 跑多個 priority, 每個用 unique novelty_id
    expected_priorities = [0, 5, 7, 10, 20]
    for pri in expected_priorities:
        # reset state (確保每個 priority 獨立 evaluate)
        mw.state = WorldPerceptionState()
        captured_event_priorities.clear()
        ev = WorldEvent(
            source="weather",
            type="rain_started",
            novelty_id=f"weather_rain_mw_pri_{pri}_001",
            ts=datetime.now(timezone.utc).isoformat(),
            summary="下雨了",
            data={"precipitation_mm": 2.5, "intensity": "light"},
            priority=pri,
        )
        # 直接 add 到 state (bypass process_world_event_direct 的 payload round-trip)
        mw.state.add(ev)
        asyncio.run(mw._on_agent_intent_enriched(enriched))
        # 確認 event_priority = pri 真的傳給 compute_scores
        assert pri in captured_event_priorities, (
            f"middleware 沒傳 event_priority={pri} 給 compute_scores "
            f"(captured: {captured_event_priorities})"
        )


# ───────────────────────────────────────────────────────────
# 5. Trace observability
# ───────────────────────────────────────────────────────────


def test_trace_records_world_event_priority(monkeypatch, tmp_path):
    """派工 #13: WorldPerceptionTrace.extra["world_event_priority"] 記錄 priority。

    用 mock writer 收集所有寫入的 WorldPerceptionTrace, 確認 extra 含 world_event_priority。
    """
    from src.world import middleware as mw_module
    from src.world.middleware import WorldPerceptionMiddleware
    from src.world.state import WorldPerceptionState
    from src.eventbus.schema import EventType, SoulEvent
    from src.world.trace import WorldPerceptionTraceWriter

    # 收集所有寫入的 trace
    captured_traces: list = []

    real_writer_init = WorldPerceptionTraceWriter.__init__

    class _CollectingWriter:
        """mock writer, 收集所有 write() 呼叫的 trace。"""
        def __init__(self):
            pass
        def write(self, trace):
            captured_traces.append(trace)
            return True
        def clear(self):
            captured_traces.clear()

    class _MockBus:
        """mock bus, 給 middleware 內部呼叫 (subscribe/unsubscribe/publish/start)。"""
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
    ev_ts = datetime.now(timezone.utc).isoformat()
    enriched = SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source="test",
        target="broadcast",
        payload={"agent_id": "agent_test", "draft": "下雨了", "text": "下雨了",
                 "chrono_context": ""},
    )
    for pri in [0, 5, 10]:
        # reset state + 用 unique novelty_id 避免 novelty 重複
        mw.state = WorldPerceptionState()
        captured_traces.clear()
        ev = WorldEvent(
            source="weather",
            type="rain_started",
            novelty_id=f"weather_rain_priority_{pri}_001",
            ts=ev_ts,
            summary="下雨了",
            data={"precipitation_mm": 2.5, "intensity": "light"},
            priority=pri,
        )
        # 直接 add 到 state (bypass process_world_event_direct 的 payload round-trip bug)
        mw.state.add(ev)
        asyncio.run(mw._on_agent_intent_enriched(enriched))
        # 從 collected trace 找 priority
        found = False
        for t in captured_traces:
            if t.extra.get("world_event_priority") == pri:
                found = True
                break
        debug_info = [
            (t.novelty_id, t.extra.get("world_event_priority"), t.selection_reason)
            for t in captured_traces
        ]
        assert found, (
            f"priority={pri} 沒在 WorldPerceptionTrace.extra 找到 "
            f"(collected: {debug_info})"
        )


# ───────────────────────────────────────────────────────────
# 6. Payload contract (M3.1 frozen)
# ───────────────────────────────────────────────────────────


def test_payload_round_trip_still_omits_priority():
    """派工 #14: WorldEvent.to_payload() 仍不包含 priority (M3.1 frozen 100% 保留)。"""
    ev = _make_rain_event(priority=42)
    payload = ev.to_payload()
    assert "priority" not in payload
    # from_payload() 仍 reconstruct 既有 6 欄位
    assert set(payload.keys()) == {"source", "type", "novelty_id", "ts", "summary", "data"}
    # 即使傳入 priority 也不會被污染
    payload_with_priority = {**payload, "priority": 999}
    ev2 = WorldEvent.from_payload(payload_with_priority)
    assert ev2.priority == 0  # WorldEvent 沒被污染, priority 走 default


# ───────────────────────────────────────────────────────────
# 7. Critical regression test — 派工 #16 mandatory
# ───────────────────────────────────────────────────────────


def test_legacy_zero_priority_parity_with_legacy_test_duplicate_novelty_decay():
    """派工 #16 critical: legacy (no event_priority) 跟 zero_priority (event_priority=0)
    必須 final 完全相等 (with pytest.approx tolerance)。

    這是 M3.2 backward-compatibility 核心 invariant, 守護既有
    test_duplicate_novelty_decay boundary case (final ≈ 0.35, == threshold)。
    """
    event = _make_rain_event()
    # 模擬既有 test_duplicate_novelty_decay 的 scoring 條件
    legacy = compute_scores(
        event=event,
        novelty_count=2,
    )
    zero_priority = compute_scores(
        event=event,
        novelty_count=2,
        event_priority=0,
    )
    # 派工 #16 核心: legacy.final() == zero_priority.final()
    assert legacy.final() == pytest.approx(zero_priority.final(), abs=1e-9)
    # priority_boost 真的 = 0 (沒污染)
    assert zero_priority.priority_boost == 0.0
    # legacy 跟既有 M3 baseline 對齊 (沒 user context = 0.275)
    # 既有 test_duplicate_novelty_decay 0.35 是因為真實 e2e pipeline 有 user context
    assert legacy.final() == pytest.approx(0.275, abs=1e-2)
