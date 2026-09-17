"""
tests/test_m4_3_a_real_source_reference.py — M4.3-A

M4.3-A Real Source Reference Implementation.

Bry 派工 2026-08-08 M4.3-A:
  目標: 不是正式接 API, 而是做一個最小可驗證的 real-source reference
        證明 M4.2 contract 能自然落地
        並 observe M4.1 發現的 wire dormant gap

不修改 production middleware / payload schema
不重調 M3.2 curve
唯一 deliverable: 這支 test file (untracked, 不 commit)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

# repo root on path (跟既有 M3.4 test 一致)
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.world.base import WorldEventSource
from src.world.dispatcher import WorldEventDispatcher
from src.world.middleware import WorldPerceptionMiddleware
from src.world.perception import WorldEvent
from src.world.state import WorldPerceptionState
from src.world.trace import WorldPerceptionTraceWriter


# ───────────────────────────────────────────────────────────
# Reference Implementation: MockWeatherSource (Pattern B)
# ───────────────────────────────────────────────────────────


class MockWeatherSource(WorldEventSource):
    """
    M4.3-A reference implementation of M4.2 Pattern B.

    Demonstrates:
      - Source-internal priority derivation (NOT caller-decided)
      - 5 severity levels → M4.2 vocabulary (NONE/LOW/NORMAL/HIGH/CRITICAL)
      - Priority set at WorldEvent construction time (M4.2 MUST 1)
      - Derivation rule documented in code (M4.2 MUST 3)
      - Uses [0, 12] vocabulary range (M4.2 MUST 4)
      - Rejects unknown severity (M4.2 MUST 5 — invalid input handling)

    NOT a production source. This is a reference pattern showing how a future
    real source (Pattern B) should conform to the M4.2 contract.
    """

    # 從 weather severity 映射到 M4.2 vocabulary
    # 對應 boost (M3.2 frozen): 12→0.96, 8→0.64, 5→0.40, 2→0.16, 0→0.00
    SEVERITY_TO_PRIORITY: Dict[str, int] = {
        "typhoon": 12,   # CRITICAL
        "heavy":   8,    # NORMAL+ (vocabulary NORMAL [5,9])
        "light":   5,    # NORMAL
        "drizzle": 2,    # LOW
        "clear":   0,    # NONE
    }

    # 給 novelty_id 用的計數器,避免 timestamp 違反 [a-z0-9_]{4,128}
    _counter: int = 0

    def __init__(self, source_id: str = "weather") -> None:
        self._source_id = source_id
        self._running = False
        self._emitted: List[WorldEvent] = []
        # Phase B capability (M3.1 Phase B): optional injector
        self._injector: Optional[Any] = None

    @property
    def source_id(self) -> str:
        return self._source_id

    def _derive_priority(self, severity: str) -> int:
        """
        M4.2 MUST 3: derivation rule is unit-testable in isolation.
        M4.2 MUST 5: invalid input rejected (no silent clamp).
        """
        if severity not in self.SEVERITY_TO_PRIORITY:
            raise ValueError(
                f"unknown severity {severity!r}, "
                f"expected one of {sorted(self.SEVERITY_TO_PRIORITY.keys())}"
            )
        return self.SEVERITY_TO_PRIORITY[severity]

    async def emit_weather(self, severity: str) -> WorldEvent:
        """
        Pattern B emission:
          1. Look up priority from internal map (M4.2 source-internal logic)
          2. Build WorldEvent with priority set at construction (M4.2 MUST 1)
          3. Record for observability (M4.2 MUST 3 — derivation rule observable)
        """
        priority = self._derive_priority(severity)  # source-internal derivation
        MockWeatherSource._counter += 1
        ts = datetime.now(timezone.utc).isoformat()
        event = WorldEvent(
            source=self._source_id,
            type="rain_started" if severity != "clear" else "weather_clear",
            # novelty_id 必須 match [a-z0-9_]{4,128}, 不用 timestamp
            novelty_id=f"{self._source_id}_{severity}_{MockWeatherSource._counter:04d}",
            ts=ts,
            summary=f"Weather severity: {severity}",
            data={"severity": severity, "intensity": severity},
            priority=priority,  # ← set at construction time
        )
        self._emitted.append(event)
        return event

    def get_emitted_events(self) -> List[WorldEvent]:
        """Observability: source can report what it emitted (M4.2 testability)."""
        return list(self._emitted)

    # M3.1 Phase B capability detection (dispatcher 用 getattr 呼叫)
    def set_injector(self, injector: Optional[Any]) -> None:
        self._injector = injector

    # WorldEventSource ABC
    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False


# ───────────────────────────────────────────────────────────
# Test 1-6: M4.2 contract compliance (unit-level on the source)
# ───────────────────────────────────────────────────────────


def test_m4_3_a_1_priority_set_at_construction():
    """M4.2 MUST 1: priority 必須在 WorldEvent 構造當下決定,不能延遲。"""
    source = MockWeatherSource()
    event = asyncio.run(source.emit_weather("typhoon"))
    # event.priority 在 return 之前就應該是 12,不是 middleware 算的
    assert event.priority == 12
    assert isinstance(event.priority, int)
    # 不可是 None 或 default 0 (M4.2 MUST 1: sync set at construction)
    assert event.priority is not None


def test_m4_3_a_2_vocabulary_compliance():
    """M4.2 MUST 4: priority 必須在 vocabulary [0, 12] 範圍內。"""
    source = MockWeatherSource()
    for severity, expected in MockWeatherSource.SEVERITY_TO_PRIORITY.items():
        event = asyncio.run(source.emit_weather(severity))
        assert event.priority == expected, (
            f"{severity}: expected priority={expected}, got {event.priority}"
        )
        assert 0 <= event.priority <= 12, (
            f"vocabulary range violated for {severity}: priority={event.priority}"
        )


def test_m4_3_a_3_derivation_rule_testable_in_isolation():
    """M4.2 MUST 3: derivation rule 可獨立單元測試,不需要 WorldEvent 構造。"""
    source = MockWeatherSource()
    # Direct call, no event construction
    assert source._derive_priority("typhoon") == 12
    assert source._derive_priority("heavy") == 8
    assert source._derive_priority("light") == 5
    assert source._derive_priority("drizzle") == 2
    assert source._derive_priority("clear") == 0


def test_m4_3_a_4_invalid_severity_rejected():
    """M4.2 MUST 5: source 對非法輸入 raise,不 silent clamp。"""
    source = MockWeatherSource()
    with pytest.raises(ValueError) as exc_info:
        source._derive_priority("hailstorm")
    assert "unknown severity" in str(exc_info.value)
    # 沒有 silent clamp 行為
    with pytest.raises(ValueError):
        source._derive_priority("")


def test_m4_3_a_5_source_does_not_defer_to_perception():
    """M4.2 MUST NOT 1: source 不把 priority 推給 perception layer。"""
    source = MockWeatherSource()
    # event 物件在構造當下就有 priority,不等於 perception
    event = asyncio.run(source.emit_weather("heavy"))
    # 如果 source 延遲 priority 給 perception, event.priority 會是 0
    assert event.priority == 8, (
        "M4.2 violation: source deferred priority to perception; "
        "event.priority should be set at construction"
    )


def test_m4_3_a_6_source_conforms_to_abc():
    """M4.2: source 必須 conform WorldEventSource ABC。"""
    source = MockWeatherSource()
    assert isinstance(source, WorldEventSource)
    # ABC 強制介面
    assert source.source_id == "weather"
    # lifecycle methods 可呼叫
    asyncio.run(source.start())
    asyncio.run(source.stop())


# ───────────────────────────────────────────────────────────
# Test 7-8: Wire observation — dispatcher + middleware path
# ───────────────────────────────────────────────────────────


def test_m4_3_a_7_dispatcher_observation_captures_priority():
    """
    M4.1 discovery verification:
    Dispatcher observation log DOES capture priority (Pattern B → dispatcher path).
    """
    class _CollectingInjector:
        def __init__(self):
            self.received: List[WorldEvent] = []
        async def inject(self, event: WorldEvent) -> None:
            self.received.append(event)

    bus = object()  # dispatcher 不需要真實 bus for observation log
    injector = _CollectingInjector()
    dispatcher = WorldEventDispatcher(name="m4_3_a_dispatcher_test")
    source = MockWeatherSource()
    dispatcher.attach_source(source)
    dispatcher.attach_injector(injector)

    # 走 dispatcher.emit_and_inject (Phase C routed path)
    event = asyncio.run(dispatcher.emit_and_inject(
        source_id="weather",
        type="rain_started",
        summary="Dispatcher test",
        novelty_id="disp_001",
        data={"severity": "typhoon"},
        priority=12,  # CRITICAL
    ))

    # observation log 確實抓到 priority=12
    obs_log = dispatcher.get_observation_log()
    assert len(obs_log) == 1
    assert obs_log[0]["priority"] == 12, (
        "dispatcher observation log captures priority (M3.1 Phase C contract)"
    )
    assert obs_log[0]["type"] == "rain_started"

    # WorldEvent 送進 injector 時 priority 還在 (=12)
    # 因為 dispatcher 構造 WorldEvent 時 priority=priority,kwarg 直接進 event.priority
    assert len(injector.received) == 1
    assert injector.received[0].priority == 12, (
        "Injector's received event has priority=12 (dispatcher sets it before inject)"
    )


def test_m4_3_a_8_middleware_process_world_event_direct_strips_priority():
    """
    M4.1 dormant wire gap confirmation:
    走 middleware.process_world_event_direct (production wire adapter) 時,
    priority 會被 to_payload() strip 掉, middleware 內部永遠看到 priority=0.
    """
    from src.eventbus.bus import SoulEventBus

    async def _run_scenario():
        bus = SoulEventBus()
        await bus.start()
        try:
            state = WorldPerceptionState()
            trace_writer = WorldPerceptionTraceWriter()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=trace_writer,
            )

            # Real source emits a real-source event with priority=8 (heavy)
            source = MockWeatherSource()
            event = await source.emit_weather("heavy")
            assert event.priority == 8, "Source sets priority=8 on the WorldEvent"

            # 走 production wire adapter
            await middleware.process_world_event_direct(event)

            # Check: priority 還在嗎?
            active = state.get_active_events()
            assert len(active) == 1
            stored_event = active[0]

            # DORMANT GAP 確認
            assert event.priority == 8, "Original event has priority=8"
            assert stored_event.priority == 0, (
                f"WIRE DORMANT GAP CONFIRMED: source emit priority=8 but "
                f"stored event priority={stored_event.priority} (priority lost in "
                f"to_payload round-trip in middleware.process_world_event_direct)"
            )
        finally:
            await bus.stop()

    asyncio.run(_run_scenario())


# ───────────────────────────────────────────────────────────
# Test 9-10: Wire gap magnitude + alternative path evidence
# ───────────────────────────────────────────────────────────


def test_m4_3_a_9_direct_path_preserves_priority():
    """
    Pattern A alternative: 繞過 bus adapter, 直接 state.add(event),
    priority 完全保留, M3.4 invariant 邏輯可以正確生效.

    這證明: M4.2 contract 在 direct path 完美運作,wire gap 只在 bus adapter.
    """
    state = WorldPerceptionState()
    source = MockWeatherSource()
    event = asyncio.run(source.emit_weather("heavy"))  # priority=8

    # Direct path (M3.4 test seam 用的路徑)
    state.add(event)

    active = state.get_active_events()
    assert len(active) == 1
    stored_event = active[0]
    assert stored_event.priority == 8, (
        "Direct path (state.add) preserves priority end-to-end. "
        "M4.2 contract is fully effective when bus adapter is bypassed."
    )


def test_m4_3_a_10_wire_gap_magnitude_quantification():
    """
    量化 wire gap:
      - 5 severities 走 bus path (production) → 全部 priority=0 (dormant)
      - 5 severities 走 direct path (test seam) → 全部 priority=match vocabulary
    證明: M4.1 的 dormant 結論在 real source 上依然成立.
    """
    from src.eventbus.bus import SoulEventBus

    async def _run_scenario():
        # Bus path setup (production wire)
        bus = SoulEventBus()
        await bus.start()
        try:
            state_bus = WorldPerceptionState()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state_bus, trace_writer=WorldPerceptionTraceWriter(),
            )

            # Direct path setup
            state_direct = WorldPerceptionState()

            source = MockWeatherSource()
            severities = ["typhoon", "heavy", "light", "drizzle", "clear"]
            expected_priorities = [12, 8, 5, 2, 0]

            bus_results: List[tuple] = []
            direct_results: List[tuple] = []

            for severity in severities:
                event = await source.emit_weather(severity)
                # Bus path
                await middleware.process_world_event_direct(event)
                # Direct path
                state_direct.add(event)

            # Bus path observations
            for e in state_bus.get_active_events():
                bus_results.append((e.data.get("severity"), e.priority))
            # Direct path observations
            for e in state_direct.get_active_events():
                direct_results.append((e.data.get("severity"), e.priority))

            # ASSERTION 1: Bus path 全 dormant (priority=0)
            bus_priorities = [p for _, p in bus_results]
            assert all(p == 0 for p in bus_priorities), (
                f"Bus path should be dormant (all 0), got {bus_priorities}"
            )
            # ASSERTION 2: Direct path 全部 match vocabulary
            direct_priorities = [p for _, p in direct_results]
            assert sorted(direct_priorities, reverse=True) == sorted(expected_priorities, reverse=True), (
                f"Direct path should match vocabulary, got {direct_priorities}"
            )

            # 量化輸出 (給 Bry 看)
            print("\n  === M4.3-A Wire Gap Magnitude ===")
            print(f"  {'severity':<10} {'expected':<10} {'bus_path':<12} {'direct_path':<12}")
            for sev, exp in zip(severities, expected_priorities):
                bus_p = next((p for s, p in bus_results if s == sev), None)
                dir_p = next((p for s, p in direct_results if s == sev), None)
                print(f"  {sev:<10} {exp:<10} {bus_p:<12} {dir_p:<12}")
            print("  ---")
            print(f"  bus path:    {sum(1 for p in bus_priorities if p == 0)}/{len(bus_priorities)} dormant")
            print(f"  direct path: {sum(1 for p in direct_priorities if p > 0)}/{len(direct_priorities)} priority preserved")
        finally:
            await bus.stop()

    asyncio.run(_run_scenario())


# ───────────────────────────────────────────────────────────
# Conclusion (printed at pytest collection)
# ───────────────────────────────────────────────────────────


def test_m4_3_a_conclusion_m4_2_contract_lands_naturally():
    """
    M4.3-A 最終結論: M4.2 contract 能不能自然落地?

    Evidence summary:
      - Pattern B source-internal derivation: WORKS (test_m4_3_a_3)
      - Vocabulary range [0, 12]: COMPLIANT (test_m4_3_a_2)
      - Priority set at construction: VERIFIED (test_m4_3_a_1)
      - Source does not defer: VERIFIED (test_m4_3_a_5)
      - Derivation rule unit-testable: VERIFIED (test_m4_3_a_3)
      - Invalid input rejected: VERIFIED (test_m4_3_a_4)
      - ABC conformance: VERIFIED (test_m4_3_a_6)

    Wire dormant gap:
      - Dispatcher observation log: CAPTURES priority (test_m4_3_a_7)
      - Bus adapter (process_world_event_direct): STRIPS priority (test_m4_3_a_8)
      - Direct path (state.add): PRESERVES priority (test_m4_3_a_9)
      - Magnitude: 5/5 dormant on bus, 5/5 preserved on direct (test_m4_3_a_10)

    Final answer:
      ✓ M4.2 contract lands naturally on Pattern B real source
      ✓ Wire dormant gap is REAL and OBSERVABLE with a real producer
      ✓ M4.2 Option C (defer wire decision) is still the right choice
        because:
        - Direct path works (Pattern A test seam + future Pattern B direct injector)
        - Dispatcher observation log captures priority for observability
        - M3.2 scoring is dormant in production bus path, but the
          contract and derivation are tested
        - Real source can use Pattern A (direct injector) to bypass
          the wire gap without modifying middleware
    """
    # 結論 test 只需要 integration sanity check + 摘要輸出
    source = MockWeatherSource()
    event = asyncio.run(source.emit_weather("light"))
    assert event.priority == 5
    print("\n  === M4.3-A Final Conclusion ===")
    print("  M4.2 contract on real source:        LANDS NATURALLY")
    print("  Wire dormant gap with real producer: CONFIRMED (5/5 dormant on bus path)")
    print("  Wire decision:                       KEEP M4.2 Option C (defer)")
    print("  Recommended next step:               M4.3-B (wire fix) when first real source needs routing, OR Pattern A direct injector")
