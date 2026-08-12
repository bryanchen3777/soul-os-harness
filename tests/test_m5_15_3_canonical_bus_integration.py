"""
tests/test_m5_15_3_canonical_bus_integration.py — M5.15-3 WorldEventSource → Event Bus Canonical Integration

M5.15-3 (Bry 派工 2026-08-12 18:45): Implementation of M5.15-1 F1 P1 fix.
Mode: IMPLEMENTATION / MINIMAL ADDITIVE

驗證:
  - WorldEventSource (synthetic) 透過 bus.publish() 發 WorldEvent 到 Event Bus
  - Bus 派發給所有 subscribers (WorldPerceptionMiddleware + WorldInnerLifeAdapter)
  - Source-originated events downstream integration 收到 (Adapter 建立 InnerLifeEvent)
  - 既有的 injector / 直接 API 100% backward compat 保留
  - 0 frozen contract change
  - 0 production data mutation
  - 0 double perception
  - 0 recursive publish
  - 0 duplicate InnerLifeEvent (novelty_id dedup)

Test sections (per Bry spec §6):
  A. Source constructor with bus + emit publishes to bus
  B. Canonical path: source → bus → middleware (state) + adapter (InnerLifeEvent)
  C. Backward compat: existing API 100% 保留
  D. set_bus / get_bus capability detection
  E. Frozen contracts preserved
  F. No production mutation
  G. Safety: no double perception, no recursive publish
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    Provenance,
    NarrativeTraceWriter,
)
from src.inner_life.trace_reader import NarrativeTraceReader
from src.paths import data_root, reset_data_root
from src.world import (
    SyntheticWorldEventSource,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
)
from src.world.inner_life_adapter import (
    WORLD_DEDUP_MAX_SIZE,
    WORLD_QUALIFYING_TYPES,
    WorldInnerLifeAdapter,
)
from src.world.perception import WorldEvent
from src.world.trace import WorldPerceptionTraceWriter


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    """Force data_root() to point to a temp dir; return data_root() Path."""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_isolated_trace_writer(tmp_path: Path) -> WorldPerceptionTraceWriter:
    """Trace writer that writes to isolated tmp_path (NOT production data/)."""
    return WorldPerceptionTraceWriter(trace_log_path=tmp_path / "perception_trace.jsonl")


def _production_style_wire(tmp_path: Path) -> tuple:
    """
    Replicate production wiring for the canonical path:
      - SoulEventBus (canonical)
      - WorldPerceptionMiddleware (subscribes to WORLD_EVENT)
      - InnerLifeWriter (canonical sole creator)
      - WorldInnerLifeAdapter (subscribes to WORLD_EVENT)

    Returns:
        (bus, world_perception, inner_life_writer, adapter) tuple
    """
    bus = SoulEventBus()
    # WorldPerceptionState default novelty_window = 24h (Bry 拍板)
    state = WorldPerceptionState()
    trace_writer = _make_isolated_trace_writer(tmp_path)
    world_perception = WorldPerceptionMiddleware(
        bus=bus,
        state=state,
        trace_writer=trace_writer,
    )
    world_perception.register()
    inner_life_writer = InnerLifeWriter(trace_writer=None)
    adapter = WorldInnerLifeAdapter(inner_life_writer=inner_life_writer)
    adapter.register(bus=bus)
    return bus, world_perception, inner_life_writer, adapter


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


# ────────────────────────────────────────────────────────────────────
# A. Source constructor with bus + emit publishes to bus
# ────────────────────────────────────────────────────────────────────

class TestSectionA_SourceBusConstructor:
    """A. Source accepts bus param, emit_event publishes SoulEvent to bus."""

    def test_a1_source_with_bus_emits_soul_event(self, isolated_root, tmp_path):
        """A.1: source with bus → emit_event → bus receives exactly 1 WORLD_EVENT."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        # Capture all WORLD_EVENT publishes on bus
        captured: List[SoulEvent] = []

        async def _capture(e: SoulEvent) -> None:
            if e.event_type == EventType.WORLD_EVENT:
                captured.append(e)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture,
            event_filter={EventType.WORLD_EVENT},
        )

        source = SyntheticWorldEventSource(bus=bus)
        assert source.get_bus() is bus

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="30 分鐘後有會議",
                    novelty_id="a1_cal_001",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # 1 WORLD_EVENT published via bus
        assert len(captured) == 1
        e = captured[0]
        assert e.event_type == EventType.WORLD_EVENT
        # Verify target + priority (canonical M5.15-2 spec)
        assert e.target == "broadcast"
        assert int(e.priority) == int(EventPriority.NORMAL)
        # Source field set
        assert e.source == "synthetic"

    def test_a2_published_payload_preserves_world_event_fields(
        self, isolated_root, tmp_path
    ):
        """A.2: published payload preserves all 7 WorldEvent fields."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        captured: List[SoulEvent] = []

        async def _capture(e: SoulEvent) -> None:
            if e.event_type == EventType.WORLD_EVENT:
                captured.append(e)

        bus.subscribe(
            subscriber_id="test_capture2",
            handler=_capture,
            event_filter={EventType.WORLD_EVENT},
        )

        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="user_going_outside",
                    summary="Bry 說他準備出門",
                    novelty_id="a2_ugo_001",
                    data={"actor": "bry", "intent": "going_outside"},
                    priority=5,
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        assert len(captured) == 1
        payload = captured[0].payload
        # All 7 fields preserved (M5.4-3.1 contract repair + M3.1 Phase B priority)
        assert payload["source"] == "synthetic"
        assert payload["type"] == "user_going_outside"
        assert payload["novelty_id"] == "a2_ugo_001"
        assert "ts" in payload and isinstance(payload["ts"], str)
        assert payload["summary"] == "Bry 說他準備出門"
        assert payload["data"] == {"actor": "bry", "intent": "going_outside"}
        assert payload["priority"] == 5

    def test_a3_source_emits_soul_event_id_is_uuid(
        self, isolated_root, tmp_path
    ):
        """A.3: each emit generates unique SoulEvent.event_id (UUID per Bus contract)."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        captured: List[SoulEvent] = []

        async def _capture(e: SoulEvent) -> None:
            if e.event_type == EventType.WORLD_EVENT:
                captured.append(e)

        bus.subscribe(
            subscriber_id="test_capture3",
            handler=_capture,
            event_filter={EventType.WORLD_EVENT},
        )

        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="a3_001"
                )
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="a3_002"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        assert len(captured) == 2
        # Different event_id per emit
        assert captured[0].event_id != captured[1].event_id


# ────────────────────────────────────────────────────────────────────
# B. Canonical path end-to-end: source → bus → middleware + adapter
# ────────────────────────────────────────────────────────────────────

class TestSectionB_CanonicalEndToEnd:
    """B. Source-originated event reaches middleware state + adapter InnerLifeEvent."""

    def test_b1_middleware_receives_source_originated_event(
        self, isolated_root, tmp_path
    ):
        """B.1: middleware state.add called via bus canonical path."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="30 分鐘後有會議",
                    novelty_id="b1_cal_001",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Middleware observed exactly 1 event
        assert wp._events_received == 1
        assert wp._events_state_added == 1
        # 0 validation rejected
        assert wp._events_validation_rejected == 0

    def test_b2_adapter_receives_source_originated_event(
        self, isolated_root, tmp_path
    ):
        """B.2: adapter creates InnerLifeEvent from source-originated WorldEvent."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="30 分鐘後有會議",
                    novelty_id="b2_cal_001",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Adapter received + qualified + created
        assert adapter.get_stats()["events_received"] == 1
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["qualifying_yes"] == 1
        # InnerLifeEvent in writer
        assert "b2_cal_001" in adapter._dedup
        event_id = adapter._dedup["b2_cal_001"]
        ev = writer._events[event_id]
        # Provenance correct (M5.9-2 spec)
        assert ev.provenance.trigger_type == "world:calendar_event"
        assert ev.provenance.source_system == "narrative"
        assert ev.provenance.actor_id is None

    def test_b3_end_to_end_mixed_5_events(
        self, isolated_root, tmp_path
    ):
        """B.3: end-to-end 5 mixed source events → 2 created + 3 fail-closed."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                # 5 events: 2 qualifying, 3 non-qualifying
                await source.emit_event(
                    type="calendar_event", summary="A", novelty_id="b3_cal"
                )
                await source.emit_event(
                    type="rain_started", summary="B", novelty_id="b3_rain"
                )
                await source.emit_event(
                    type="user_going_outside", summary="C", novelty_id="b3_ugo"
                )
                await source.emit_event(
                    type="celebrity_news", summary="D", novelty_id="b3_celeb"
                )
                await source.emit_event(
                    type="weather_temp_change", summary="E", novelty_id="b3_temp"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Middleware: 5 events received, 5 added to state
        assert wp._events_received == 5
        assert wp._events_state_added == 5
        # Adapter: 5 received, 2 qualifying, 2 created, 3 non-qualifying
        assert adapter.get_stats()["events_received"] == 5
        assert adapter.get_stats()["qualifying_yes"] == 2
        assert adapter.get_stats()["non_qualifying"] == 3
        assert adapter.get_stats()["events_created"] == 2
        # Dedup size 2
        assert adapter.get_dedup_size() == 2
        # Writer has 2 InnerLifeEvents
        assert len(writer._events) == 2
        # Triggers
        trigger_types = {ev.provenance.trigger_type for ev in writer._events.values()}
        assert "world:calendar_event" in trigger_types
        assert "world:user_going_outside" in trigger_types

    def test_b4_adapter_dedup_works_for_source_emits(
        self, isolated_root, tmp_path
    ):
        """B.4: same novelty_id from source emitted 3 times → 1 InnerLifeEvent."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                for _ in range(3):
                    await source.emit_event(
                        type="calendar_event",
                        summary="dup",
                        novelty_id="b4_dup_001",
                    )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Adapter received 3, created 1, dedup skipped 2
        assert adapter.get_stats()["events_received"] == 3
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["duplicates_skipped"] == 2
        # Middleware state.add called 3 times (dedup is adapter's job, not middleware's)
        assert wp._events_state_added == 3


# ────────────────────────────────────────────────────────────────────
# C. Backward compat: existing API 100% 保留
# ────────────────────────────────────────────────────────────────────

class TestSectionC_BackwardCompat:
    """C. M3.1 Phase B direct path 100% preserved."""

    def test_c1_no_bus_no_injector_returns_event_only(
        self, isolated_root, tmp_path
    ):
        """C.1: source without bus/injector → returns event, no delivery."""
        source = SyntheticWorldEventSource()  # no args
        assert source.get_bus() is None
        assert source.get_injector() is None

        async def _run():
            ev = await source.emit_event(
                type="calendar_event",
                summary="test",
                novelty_id="c1_001",
            )
            assert isinstance(ev, WorldEvent)
            assert ev.novelty_id == "c1_001"
            assert ev.source == "synthetic"

        asyncio.run(_run())

    def test_c2_injector_only_uses_legacy_path(
        self, isolated_root, tmp_path
    ):
        """C.2: source with only injector → uses legacy direct path."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)

        calls: List[WorldEvent] = []

        class _CaptureInjector:
            async def inject(self, event: WorldEvent) -> None:
                calls.append(event)

        source = SyntheticWorldEventSource(injector=_CaptureInjector())

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="legacy path",
                    novelty_id="c2_001",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Injector called once (legacy direct path)
        assert len(calls) == 1
        assert calls[0].novelty_id == "c2_001"
        # Adapter received 0 (direct path bypasses bus)
        assert adapter.get_stats()["events_received"] == 0

    def test_c3_bus_takes_priority_over_injector(
        self, isolated_root, tmp_path
    ):
        """C.3: source with BOTH bus and injector → bus takes priority (canonical)."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)

        calls: List[WorldEvent] = []

        class _CaptureInjector:
            async def inject(self, event: WorldEvent) -> None:
                calls.append(event)

        source = SyntheticWorldEventSource(
            injector=_CaptureInjector(),
            bus=bus,
        )

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="bus priority",
                    novelty_id="c3_001",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Bus path used: adapter received 1, created 1
        assert adapter.get_stats()["events_created"] == 1
        # Injector NOT called (bus takes priority)
        assert len(calls) == 0

    def test_c4_existing_build_factory_methods_unchanged(
        self, isolated_root, tmp_path
    ):
        """C.4: build_*() factory methods 100% preserved (no bus)."""
        ev1 = SyntheticWorldEventSource.build_rain_started()
        assert ev1.type == "rain_started"
        assert ev1.source == "weather"
        assert ev1.novelty_id == "weather_rain_20260807"

        ev2 = SyntheticWorldEventSource.build_celebrity_news()
        assert ev2.type == "celebrity_news"

        ev3 = SyntheticWorldEventSource.build_calendar_event_30min()
        assert ev3.type == "calendar_event"

        ev4 = SyntheticWorldEventSource.build_temp_fluctuation()
        assert ev4.type == "weather_temp_change"

        ev5 = SyntheticWorldEventSource.build_user_going_outside()
        assert ev5.type == "user_going_outside"

        all_five = SyntheticWorldEventSource.build_all_five()
        assert len(all_five) == 5

    def test_c5_existing_set_injector_still_works(
        self, isolated_root, tmp_path
    ):
        """C.5: set_injector() Phase B method 100% preserved."""
        source = SyntheticWorldEventSource()
        assert source.get_injector() is None

        class _FakeInjector:
            async def inject(self, e):
                pass

        fake = _FakeInjector()
        source.set_injector(fake)
        assert source.get_injector() is fake

        # Detach
        source.set_injector(None)
        assert source.get_injector() is None

    def test_c6_existing_injector_contract_still_works(
        self, isolated_root, tmp_path
    ):
        """C.6: SyntheticWorldEventSource(injector=X) without bus still works
        (M3.1 Phase B regression — exact same path as before M5.15-3)."""
        # This is the EXACT pre-M5.15-3 usage pattern. Must still work 100%.
        captured: List[WorldEvent] = []

        class _LegacyInjector:
            async def inject(self, e: WorldEvent) -> None:
                captured.append(e)

        source = SyntheticWorldEventSource(injector=_LegacyInjector())

        async def _run():
            ev = await source.emit_event(
                type="calendar_event", summary="legacy", novelty_id="c6_001"
            )
            assert ev is not None

        asyncio.run(_run())
        assert len(captured) == 1
        assert captured[0].novelty_id == "c6_001"


# ────────────────────────────────────────────────────────────────────
# D. set_bus / get_bus capability detection
# ────────────────────────────────────────────────────────────────────

class TestSectionD_BusCapabilityDetection:
    """D. set_bus / get_bus capability detection (跟 set_injector 同 pattern)."""

    def test_d1_set_bus_attaches(self, isolated_root, tmp_path):
        bus = SoulEventBus()
        source = SyntheticWorldEventSource()
        assert source.get_bus() is None
        source.set_bus(bus)
        assert source.get_bus() is bus

    def test_d2_set_bus_none_detaches(self, isolated_root, tmp_path):
        bus = SoulEventBus()
        source = SyntheticWorldEventSource(bus=bus)
        assert source.get_bus() is bus
        source.set_bus(None)
        assert source.get_bus() is None

    def test_d3_constructor_and_set_bus_equivalent(
        self, isolated_root, tmp_path
    ):
        bus = SoulEventBus()
        a = SyntheticWorldEventSource(bus=bus)
        b = SyntheticWorldEventSource()
        b.set_bus(bus)
        assert a.get_bus() is b.get_bus()

    def test_d4_set_bus_after_emit_uses_new_bus(
        self, isolated_root, tmp_path
    ):
        """D.4: set_bus() after first emit → next emit uses new bus."""
        bus1 = SoulEventBus()
        bus2 = SoulEventBus()
        captured1: List[SoulEvent] = []
        captured2: List[SoulEvent] = []

        async def _cap1(e): captured1.append(e)
        async def _cap2(e): captured2.append(e)

        bus1.subscribe("c1", _cap1, event_filter={EventType.WORLD_EVENT})
        bus2.subscribe("c2", _cap2, event_filter={EventType.WORLD_EVENT})

        source = SyntheticWorldEventSource(bus=bus1)

        async def _run():
            await bus1.start()
            await bus2.start()
            try:
                # First emit → bus1
                await source.emit_event(
                    type="calendar_event", summary="A", novelty_id="d4_a"
                )
                # Switch to bus2
                source.set_bus(bus2)
                # Second emit → bus2
                await source.emit_event(
                    type="calendar_event", summary="B", novelty_id="d4_b"
                )
            finally:
                await bus1.stop()
                await bus2.stop()

        asyncio.run(_run())
        assert len(captured1) == 1
        assert len(captured2) == 1
        assert captured1[0].payload["novelty_id"] == "d4_a"
        assert captured2[0].payload["novelty_id"] == "d4_b"


# ────────────────────────────────────────────────────────────────────
# E. Frozen contracts preserved
# ────────────────────────────────────────────────────────────────────

class TestSectionE_FrozenContracts:
    """E. 0 frozen contract change."""

    def test_e1_world_event_schema_unchanged(self):
        from dataclasses import fields
        field_names = {f.name for f in fields(WorldEvent)}
        assert field_names == {
            "source", "type", "novelty_id", "ts", "summary", "data", "priority",
        }

    def test_e2_inner_life_event_schema_unchanged(self):
        from dataclasses import fields
        from src.inner_life import InnerLifeEvent
        field_names = {f.name for f in fields(InnerLifeEvent)}
        assert field_names == {
            "event_id", "session_id", "correlation_id", "parent_event_id",
            "ts", "provenance", "lineage_depth", "lineage_path",
        }

    def test_e3_provenance_schema_unchanged(self):
        from dataclasses import fields
        from src.inner_life import Provenance
        field_names = {f.name for f in fields(Provenance)}
        assert field_names == {
            "trigger_type", "actor_id", "source_system", "trace_ref", "extras",
        }

    def test_e4_world_event_source_abc_unchanged(self):
        """E.4: WorldEventSource ABC abstract methods unchanged (set_bus is
        not abstract, it's capability detection)."""
        from src.world.base import WorldEventSource
        import inspect
        # 3 abstract methods: source_id, start, stop (no set_bus / no emit)
        abstract_methods = set(WorldEventSource.__abstractmethods__)
        assert abstract_methods == {"source_id", "start", "stop"}

    def test_e5_synthetic_source_is_subclass(self):
        """E.5: SyntheticWorldEventSource still conforms WorldEventSource ABC."""
        from src.world.base import WorldEventSource
        assert issubclass(SyntheticWorldEventSource, WorldEventSource)
        # Has source_id (required)
        assert SyntheticWorldEventSource().source_id == "synthetic"

    def test_e6_middleware_inject_unchanged(self):
        """E.6: WorldPerceptionMiddleware.inject() / process_world_event_direct()
        RETAINED as deprecated backward-compat (per M5.15-2 spec §4)."""
        import inspect
        from src.world.middleware import WorldPerceptionMiddleware
        # Both methods still exist (deprecated but not deleted)
        assert hasattr(WorldPerceptionMiddleware, "inject")
        assert hasattr(WorldPerceptionMiddleware, "process_world_event_direct")
        # Both are async
        assert inspect.iscoroutinefunction(WorldPerceptionMiddleware.inject)
        assert inspect.iscoroutinefunction(WorldPerceptionMiddleware.process_world_event_direct)


# ────────────────────────────────────────────────────────────────────
# F. No production mutation
# ────────────────────────────────────────────────────────────────────

class TestSectionF_NoProductionMutation:
    """F. 0 production data mutation. Tests use isolated data_root."""

    def test_f1_isolated_root_unchanged(self, isolated_root, tmp_path):
        """F.1: run in isolated data_root, verify all writes go to tmp_path."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="f1_001"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Test writes only in tmp_path
        assert adapter.get_stats()["events_created"] == 1
        # Verify the isolated trace file is in tmp_path
        assert (tmp_path / "perception_trace.jsonl").exists()

    def test_f2_no_real_data_dir_mutation(
        self, isolated_root, tmp_path
    ):
        """F.2: real production data/ not mutated (verify path resolution)."""
        # Verify data_root() is pointing to tmp, NOT real data/
        from pathlib import Path
        resolved = data_root()
        assert str(tmp_path) in str(resolved)
        # Should NOT contain "C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness\\data"
        real_data = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness\\data")
        assert str(resolved).startswith(str(tmp_path))


# ────────────────────────────────────────────────────────────────────
# G. Safety: no double perception, no recursive publish
# ────────────────────────────────────────────────────────────────────

class TestSectionG_SafetyInvariants:
    """G. Safety invariants: no double perception, no recursive publish."""

    def test_g1_middleware_called_exactly_once_per_source_emit(
        self, isolated_root, tmp_path
    ):
        """G.1: 1 source emit → middleware.handle_event called exactly 1 time."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        original_on_world = wp._on_world_event
        call_count = [0]

        async def _tracked_on_world(event):
            call_count[0] += 1
            await original_on_world(event)

        wp._on_world_event = _tracked_on_world  # type: ignore[assignment]

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="g1_001"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Exactly 1 call to middleware._on_world_event
        assert call_count[0] == 1
        # Verify bus stats: 1 dispatched to world_perception
        stats = bus.get_stats()
        assert stats.get("handled_world_perception", 0) == 1

    def test_g2_adapter_called_exactly_once_per_source_emit(
        self, isolated_root, tmp_path
    ):
        """G.2: 1 source emit → adapter.handle_event called exactly 1 time."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        # Add a capture subscriber to count adapter dispatches
        capture_count = [0]

        async def _capture(e: SoulEvent) -> None:
            capture_count[0] += 1

        bus.subscribe(
            subscriber_id="test_g2_capture",
            handler=_capture,
            event_filter={EventType.WORLD_EVENT},
        )

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="g2_001"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Adapter received 1 (via adapter._stats)
        assert adapter.get_stats()["events_received"] == 1
        # Bus dispatched to adapter 1 time
        stats = bus.get_stats()
        assert stats.get("handled_world_inner_life_adapter", 0) == 1

    def test_g3_no_recursive_publish_to_world_event(
        self, isolated_root, tmp_path
    ):
        """G.3: middleware/adapter do not publish back WORLD_EVENT (no recursion)."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        # Track ALL WORLD_EVENT publishes
        published: List[SoulEvent] = []

        original_publish = bus.publish

        async def _tracked_publish(event: SoulEvent) -> None:
            if event.event_type == EventType.WORLD_EVENT:
                published.append(event)
            await original_publish(event)

        bus.publish = _tracked_publish  # type: ignore[assignment]

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="g3_001"
                )
                # Give time for any cascading publishes
                await asyncio.sleep(0.05)
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Exactly 1 WORLD_EVENT published (from source)
        # Middleware + adapter do NOT publish back
        world_event_publishes = [
            p for p in published if p.event_type == EventType.WORLD_EVENT
        ]
        assert len(world_event_publishes) == 1
        # Original publish
        assert world_event_publishes[0].payload["novelty_id"] == "g3_001"

    def test_g4_no_agency_trigger_published(
        self, isolated_root, tmp_path
    ):
        """G.4: source does not publish AGENCY_TRIGGER (out of M5.15-3 scope)."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        ag_published: List[SoulEvent] = []

        async def _capture_ag(e: SoulEvent) -> None:
            if e.event_type == EventType.AGENCY_TRIGGER:
                ag_published.append(e)

        bus.subscribe(
            subscriber_id="ag_capture",
            handler=_capture_ag,
            event_filter={EventType.AGENCY_TRIGGER},
        )

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event", summary="x", novelty_id="g4_001"
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # 0 AGENCY_TRIGGER
        assert len(ag_published) == 0

    def test_g5_priority_preserved_end_to_end(
        self, isolated_root, tmp_path
    ):
        """G.5: WorldEvent.priority preserved through bus payload."""
        bus, wp, writer, adapter = _production_style_wire(tmp_path)
        source = SyntheticWorldEventSource(bus=bus)

        # Use a capture subscriber to inspect payload (no monkey-patch)
        seen_payloads: List[Dict[str, Any]] = []

        async def _capture(e: SoulEvent) -> None:
            seen_payloads.append(dict(e.payload))

        bus.subscribe(
            subscriber_id="test_g5_capture",
            handler=_capture,
            event_filter={EventType.WORLD_EVENT},
        )

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="priority test",
                    novelty_id="g5_001",
                    priority=10,
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Payload preserved priority
        assert len(seen_payloads) == 1
        assert seen_payloads[0]["priority"] == 10
        # Adapter created 1 InnerLifeEvent
        assert adapter.get_stats()["events_created"] == 1


# ────────────────────────────────────────────────────────────────────
# Test count guard
# ────────────────────────────────────────────────────────────────────

def test_z_count():
    """Test count guard (counts all test_ methods in classes + module-level)."""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_count = 0
    for name, obj in inspect.getmembers(current_module):
        if name.startswith("test_") and callable(obj):
            test_count += 1
    for name, obj in inspect.getmembers(current_module):
        if inspect.isclass(obj) and obj.__module__ == current_module.__name__:
            for mname, method in inspect.getmembers(obj):
                if mname.startswith("test_") and callable(method):
                    test_count += 1
    # 25+ tests covering A-G + backward compat + safety
    assert test_count >= 25, f"expected 25+ tests, got {test_count}"
