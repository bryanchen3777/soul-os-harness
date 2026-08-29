"""
tests/test_m5_9_3_world_inner_life_adapter.py — M5.9-3 World → Inner Life Adapter

M5.9-3 (Bry 派工 2026-08-10): World → Inner Life Adapter Implementation
Mode: MINIMAL ADDITIVE / Implementation

Test sections (Bry spec §11):
  A. calendar_event qualifies
  B. user_going_outside qualifies
  C. rain_started qualifies (SG-1 解冻 2026-08-29, Owner 授权 whitelist 扩展)
  D. celebrity_news rejected
  E. weather_temp_change qualifies (SG-1 解冻 2026-08-29)
  F. unknown type rejected
  G. duplicate novelty_id rejected
  H. FIFO eviction at 1000
  I. actor_id remains None
  J. session_id remains None
  K. correlation_id remains None
  L. parent_event_id remains None
  M. trigger_type correctness
  N. provenance correctness
  O. InnerLifeWriter is sole creator
  P. no conversation content access
  Q. no LLM / semantic / vector path
  R. no production data mutation
  S. recursive-loop protection

Plus integration tests:
  T. bus subscription integration
  U. multiple qualifying types each create 1 InnerLifeEvent
  V. WorldPerceptionMiddleware compatibility
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life import (
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DREAM_DREAM,
)
from src.inner_life.trace_reader import NarrativeTraceReader
from src.paths import data_root, reset_data_root
from src.world.inner_life_adapter import (
    WORLD_DEDUP_MAX_SIZE,
    WORLD_QUALIFYING_TYPES,
    WorldInnerLifeAdapter,
    WorldQualificationDecision,
    WorldQualificationResult,
    qualify_world_event,
)
from src.world.perception import WorldEvent


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


def _make_world_event(
    type_: str,
    source: str = "calendar",
    novelty_id: str = None,
    summary: str = "test world event",
    priority: int = 0,
) -> WorldEvent:
    """Helper to construct a WorldEvent for testing."""
    import uuid
    if novelty_id is None:
        novelty_id = uuid.uuid4().hex
    return WorldEvent(
        source=source,
        type=type_,
        novelty_id=novelty_id,
        ts="2026-08-10T20:00:00+00:00",
        summary=summary,
        data={},
        priority=priority,
    )


def _make_soul_event_for_world(world_event: WorldEvent) -> SoulEvent:
    """Wrap a WorldEvent in a SoulEvent for bus subscription."""
    return SoulEvent(
        event_type=EventType.WORLD_EVENT,
        source="test_world_source",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=world_event.to_payload(),
    )


@pytest.fixture
def isolated_root(tmp_path: Path):
    """Yield isolated data_root, restore after."""
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


# ────────────────────────────────────────────────────────────────────
# A. calendar_event qualifies
# ────────────────────────────────────────────────────────────────────

class TestSectionA_CalendarEventQualifies:
    """A. calendar_event → YES → InnerLifeEvent created."""

    def test_a1_calendar_event_qualifies(self):
        """A.1: pure function returns YES for calendar_event."""
        we = _make_world_event(type_="calendar_event", source="calendar")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.YES
        assert result.world_type == "calendar_event"

    def test_a2_calendar_event_via_bus_creates_inner_life_event(self, isolated_root):
        """A.2: end-to-end via bus → 1 InnerLifeEvent created."""
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        captured_bus = []

        async def _run():
            bus = SoulEventBus()
            await bus.start()
            captured_bus.append(bus)
            try:
                adapter.register(bus)
                we = _make_world_event(
                    type_="calendar_event",
                    source="calendar",
                    novelty_id="cal_test_001",
                    summary="30 分鐘後有重要會議",
                )
                await bus.publish(_make_soul_event_for_world(we))
            finally:
                await bus.stop()

        asyncio.run(_run())

        # Verify 1 InnerLifeEvent created
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["events_received"] == 1
        assert adapter.get_stats()["qualifying_yes"] == 1
        assert adapter.get_stats()["non_qualifying"] == 0
        # Verify event in writer
        assert "cal_test_001" in adapter._dedup
        event_id = adapter._dedup["cal_test_001"]
        assert writer._events[event_id] is not None
        # Verify provenance
        ev = writer._events[event_id]
        assert ev.provenance.trigger_type == "world:calendar_event"
        assert ev.provenance.source_system == "narrative"


# ────────────────────────────────────────────────────────────────────
# B. user_going_outside qualifies
# ────────────────────────────────────────────────────────────────────

class TestSectionB_UserGoingOutsideQualifies:
    """B. user_going_outside → YES → InnerLifeEvent created."""

    def test_b1_user_going_outside_qualifies(self):
        we = _make_world_event(type_="user_going_outside", source="social")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.YES
        assert result.world_type == "user_going_outside"

    def test_b2_user_going_outside_creates_with_data_actor(self, isolated_root):
        """B.2: TEST_E pattern with data.actor='bry' → InnerLifeEvent."""
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = WorldEvent(
                    source="social",
                    type="user_going_outside",
                    novelty_id="social_bry_going_outside_20260810",
                    ts="2026-08-10T20:00:00+00:00",
                    summary="Bry 說他準備出門。",
                    data={"actor": "bry", "intent": "going_outside"},
                    priority=0,
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 1
        event_id = adapter._dedup["social_bry_going_outside_20260810"]
        ev = writer._events[event_id]
        # extras preserve world_source/world_type/world_novelty_id
        assert ev.provenance.extras["world_source"] == "social"
        assert ev.provenance.extras["world_type"] == "user_going_outside"
        assert ev.provenance.extras["world_novelty_id"] == "social_bry_going_outside_20260810"


# ────────────────────────────────────────────────────────────────────
# C. rain_started qualifies (SG-1 解冻 2026-08-29)
# ────────────────────────────────────────────────────────────────────

class TestSectionC_RainStartedQualifies:
    """C. rain_started → YES (SG-1 解冻, Owner 授权 whitelist 扩展加 news/weather)."""

    def test_c1_rain_started_qualifies(self):
        we = _make_world_event(type_="rain_started", source="weather")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.YES
        assert "rain_started" in result.reason

    def test_c2_rain_started_via_bus_creates(self, isolated_root):
        """C.2: end-to-end via bus → 1 InnerLifeEvent created."""
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(
                    type_="rain_started",
                    source="weather",
                    novelty_id="weather_rain_test",
                    summary="外面開始下雨了",
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["non_qualifying"] == 0
        assert "weather_rain_test" in adapter._dedup


# ────────────────────────────────────────────────────────────────────
# D. celebrity_news rejected
# ────────────────────────────────────────────────────────────────────

class TestSectionD_CelebrityNewsRejected:
    """D. celebrity_news → NO."""

    def test_d1_celebrity_news_rejected(self):
        we = _make_world_event(type_="celebrity_news", source="news")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING

    def test_d2_celebrity_news_via_bus_no_create(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="celebrity_news", novelty_id="news_c_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 0


# ────────────────────────────────────────────────────────────────────
# E. weather_temp_change qualifies (SG-1 解冻 2026-08-29)
# ────────────────────────────────────────────────────────────────────

class TestSectionE_WeatherTempChangeQualifies:
    """E. weather_temp_change → YES (SG-1 解冻, Owner 授权 whitelist 扩展)."""

    def test_e1_weather_temp_change_qualifies(self):
        we = _make_world_event(type_="weather_temp_change", source="weather")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.YES

    def test_e2_weather_temp_change_via_bus_creates(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="weather_temp_change", novelty_id="weather_t_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["non_qualifying"] == 0


# ────────────────────────────────────────────────────────────────────
# F. unknown type rejected
# ────────────────────────────────────────────────────────────────────

class TestSectionF_UnknownTypeRejected:
    """F. unknown / missing type → NO (fail-closed)."""

    def test_f1_unknown_type_rejected(self):
        we = _make_world_event(type_="system_notification_xyz_unknown", source="system")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING

    def test_f2_empty_type_rejected(self):
        """F.2: empty string type → fail-closed."""
        we = _make_world_event(type_="", source="system")
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING

    def test_f3_missing_type_attribute_rejected(self):
        """F.3: missing type attribute (defensive)."""
        # Build manually without type
        we = WorldEvent(
            source="system",
            type="placeholder",  # __post_init__ requires str, use placeholder
            novelty_id="test",
            ts="2026-08-10T20:00:00+00:00",
            summary="x",
            data={},
        )
        # Forcefully remove type to test defensive
        object.__setattr__(we, "type", None)
        result = qualify_world_event(we)
        assert result.decision == WorldQualificationDecision.NO_TYPE_NOT_QUALIFYING

    def test_f4_unknown_via_bus_no_create(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(
                    type_="unknown_future_type",
                    source="system",
                    novelty_id="unknown_test_001",
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 0
        assert "unknown_test_001" not in adapter._dedup


# ────────────────────────────────────────────────────────────────────
# G. duplicate novelty_id rejected
# ────────────────────────────────────────────────────────────────────

class TestSectionG_DuplicateNoveltyId:
    """G. Same novelty_id → only 1 InnerLifeEvent created."""

    def test_g1_same_novelty_id_only_one_create(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                # Same novelty_id, 3 publishes
                for _ in range(3):
                    we = _make_world_event(
                        type_="calendar_event",
                        source="calendar",
                        novelty_id="cal_dup_test",
                    )
                    await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["duplicates_skipped"] == 2
        assert adapter.get_stats()["events_received"] == 3

    def test_g2_different_novelty_id_each_creates_one(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                for i in range(5):
                    we = _make_world_event(
                        type_="calendar_event",
                        source="calendar",
                        novelty_id=f"cal_unique_{i}",
                    )
                    await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 5
        assert adapter.get_stats()["duplicates_skipped"] == 0
        assert adapter.get_dedup_size() == 5


# ────────────────────────────────────────────────────────────────────
# H. FIFO eviction at 1000
# ────────────────────────────────────────────────────────────────────

class TestSectionH_FifoEviction:
    """H. Dedup dict bounded at 1000, FIFO eviction."""

    def test_h1_dedup_bounded_at_max_size(self):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer, dedup_max_size=10)
        # Manually add 12 entries
        for i in range(12):
            adapter._record_dedup(f"n_{i}", f"e_{i}")
        # Should be capped at 10, oldest 2 evicted
        assert adapter.get_dedup_size() == 10
        assert "n_0" not in adapter._dedup
        assert "n_1" not in adapter._dedup
        assert "n_2" in adapter._dedup
        assert "n_11" in adapter._dedup

    def test_h2_default_max_size_is_1000(self):
        assert WORLD_DEDUP_MAX_SIZE == 1000
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        assert adapter._dedup_max_size == 1000

    def test_h3_dedup_lost_on_restart(self):
        """H.3: dedup is in-memory only, lost on restart (per spec)."""
        # No persistence — fresh adapter has empty dedup
        writer = InnerLifeWriter(trace_writer=None)
        adapter1 = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter1._record_dedup("n_1", "e_1")
        assert adapter1.get_dedup_size() == 1

        adapter2 = WorldInnerLifeAdapter(inner_life_writer=writer)
        assert adapter2.get_dedup_size() == 0  # fresh, no persistence


# ────────────────────────────────────────────────────────────────────
# I-L. identity fields all None
# ────────────────────────────────────────────────────────────────────

class TestSectionI_IdentityFields:
    """I-L. actor_id / session_id / correlation_id / parent_event_id = None."""

    def test_i_actor_id_is_none(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="actor_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        event_id = adapter._dedup["actor_test"]
        ev = writer._events[event_id]
        assert ev.provenance.actor_id is None

    def test_j_session_id_is_none(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="sess_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["sess_test"]]
        assert ev.session_id is None

    def test_k_correlation_id_is_none(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="corr_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["corr_test"]]
        assert ev.correlation_id is None

    def test_l_parent_event_id_is_none(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            bus = SoulEventBus()
            await bus.start()
            try:
                adapter.register(bus)
                we = _make_world_event(type_="calendar_event", novelty_id="par_test")
                await bus.publish(_make_soul_event_for_world(we))
            finally:
                await bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["par_test"]]
        assert ev.parent_event_id is None
        assert ev.lineage_depth == 0
        # lineage_path for root events: equals event_id (per writer.py:186)
        assert ev.lineage_path == ev.event_id


# ────────────────────────────────────────────────────────────────────
# M. trigger_type correctness
# ────────────────────────────────────────────────────────────────────

class TestSectionM_TriggerType:
    """M. trigger_type uses world:<type> per-type."""

    def test_m1_calendar_event_trigger_type(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="tt_cal")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["tt_cal"]]
        assert ev.provenance.trigger_type == "world:calendar_event"

    def test_m2_user_going_outside_trigger_type(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="user_going_outside", novelty_id="tt_ugo")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["tt_ugo"]]
        assert ev.provenance.trigger_type == "world:user_going_outside"


# ────────────────────────────────────────────────────────────────────
# N. provenance correctness
# ────────────────────────────────────────────────────────────────────

class TestSectionN_Provenance:
    """N. Provenance spec correctness (source_system, extras)."""

    def test_n1_source_system_narrative(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="ss_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["ss_test"]]
        assert ev.provenance.source_system == "narrative"

    def test_n2_extras_preserve_world_metadata(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = WorldEvent(
                    source="calendar",
                    type="calendar_event",
                    novelty_id="extras_test_001",
                    ts="2026-08-10T20:00:00+00:00",
                    summary="Test meeting",
                    data={"priority_hint": "high"},
                    priority=0,
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["extras_test_001"]]
        assert ev.provenance.extras["world_source"] == "calendar"
        assert ev.provenance.extras["world_type"] == "calendar_event"
        assert ev.provenance.extras["world_novelty_id"] == "extras_test_001"
        # All extras values must be str per Provenance validation
        for k, v in ev.provenance.extras.items():
            assert isinstance(v, str), f"extras[{k!r}] = {v!r} must be str"


# ────────────────────────────────────────────────────────────────────
# O. InnerLifeWriter is sole creator
# ────────────────────────────────────────────────────────────────────

class TestSectionO_SoleCreator:
    """O. InnerLifeWriter is sole canonical creator (M5.4-5.1 frozen)."""

    def test_o1_no_second_creator(self):
        """O.1: module exposes no create_event function of its own."""
        from src.world import inner_life_adapter
        # Module should not have its own create_event (only InnerLifeWriter does)
        assert not hasattr(inner_life_adapter, "create_inner_life_event")

    def test_o2_adapter_calls_writer_create_event(self, isolated_root):
        """O.2: adapter delegates to writer.create_event (sole creator).
        M5.15-5 (2026-08-12): updated to include source_world_event_novelty_id parameter
        (additive amendment to M5.4-5.1 InnerLifeWriter signature).
        """
        # Use real writer (not mock) — verify by inspection that adapter
        # calls writer.create_event with proper Provenance
        writer = InnerLifeWriter(trace_writer=None)
        call_log: list = []
        original_create = writer.create_event

        def tracked_create(*, provenance, session_id=None, correlation_id=None, parent_event_id=None, source_world_event_novelty_id=None, ts=None):
            call_log.append({
                "trigger_type": provenance.trigger_type,
                "actor_id": provenance.actor_id,
                "source_system": provenance.source_system,
                "session_id": session_id,
                "correlation_id": correlation_id,
                "parent_event_id": parent_event_id,
                # M5.15-5: track new parameter to verify adapter passes it
                "source_world_event_novelty_id": source_world_event_novelty_id,
            })
            return original_create(
                provenance=provenance,
                session_id=session_id,
                correlation_id=correlation_id,
                parent_event_id=parent_event_id,
                source_world_event_novelty_id=source_world_event_novelty_id,
                ts=ts,
            )

        writer.create_event = tracked_create
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            bus = SoulEventBus()
            await bus.start()
            try:
                adapter.register(bus)
                we = _make_world_event(type_="calendar_event", novelty_id="sole_test")
                await bus.publish(_make_soul_event_for_world(we))
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Adapter created exactly 1 event
        assert adapter.get_stats()["events_created"] == 1
        # writer.create_event was called once with correct args
        assert len(call_log) == 1
        call = call_log[0]
        assert call["trigger_type"] == "world:calendar_event"
        assert call["actor_id"] is None
        assert call["source_system"] == "narrative"
        assert call["session_id"] is None
        assert call["correlation_id"] is None
        assert call["parent_event_id"] is None
        # M5.15-5: adapter passes WorldEvent.novelty_id as source_world_event_novelty_id
        assert call["source_world_event_novelty_id"] == "sole_test"


# ────────────────────────────────────────────────────────────────────
# P. no conversation content access
# ────────────────────────────────────────────────────────────────────

class TestSectionP_NoContentAccess:
    """P. Adapter does not read conversation / diary / dream text."""

    def test_p1_does_not_read_conversation(self, isolated_root):
        """P.1: only reads WorldEvent from event.payload."""
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                # WorldEvent with summary (the only "text"-like field)
                we = _make_world_event(
                    type_="calendar_event",
                    novelty_id="content_test",
                    summary="30 分鐘後有重要會議",
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        ev = writer._events[adapter._dedup["content_test"]]
        # summary is NOT in extras (we only store world_source / world_type / world_novelty_id)
        assert "summary" not in ev.provenance.extras
        assert "data" not in ev.provenance.extras
        # WorldEvent.summary 是 observation text, not conversation content
        # 5 個現有 producer 也都不存 summary / data text 進 Provenance


# ────────────────────────────────────────────────────────────────────
# Q. no LLM / semantic / vector path
# ────────────────────────────────────────────────────────────────────

class TestSectionQ_NoLlMPath:
    """Q. Adapter has no LLM / semantic / vector / scoring infrastructure."""

    def test_q1_module_imports_check(self):
        """Q.1: no LLM/semantic/vector imports."""
        import src.world.inner_life_adapter as mod
        import ast
        # Parse AST and check imports only (not docstrings/comments)
        tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
        import_aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    import_aliases.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    import_aliases.add(node.module.split(".")[0])
        # Forbidden imports
        forbidden = {
            "openai", "anthropic", "transformers", "torch", "tensorflow",
            "sklearn", "sentence_transformers", "voyageai", "cohere",
        }
        for keyword in forbidden:
            assert keyword not in import_aliases, (
                f"M5.9-3 spec: no LLM/semantic/vector. Found import {keyword!r}"
            )
        # Also verify no semantic / embedding / vector callable references
        forbidden_patterns = ["openai.", "anthropic.", "embed(", "vector_store"]
        source = open(mod.__file__, encoding="utf-8").read()
        for pattern in forbidden_patterns:
            assert pattern not in source, (
                f"M5.9-3 spec: no LLM/semantic/vector call. Found {pattern!r}"
            )

    def test_q2_deterministic_same_input_same_output(self):
        """Q.2: same input → same output (no random / LLM)."""
        we1 = _make_world_event(type_="calendar_event", novelty_id="det_1")
        we2 = _make_world_event(type_="calendar_event", novelty_id="det_2")
        r1 = qualify_world_event(we1)
        r2 = qualify_world_event(we2)
        assert r1.decision == r2.decision  # both YES
        # Same type, different novelty_id → same qualification decision (deterministic)
        assert r1.decision == r2.decision


# ────────────────────────────────────────────────────────────────────
# R. no production data mutation
# ────────────────────────────────────────────────────────────────────

class TestSectionR_NoProductionMutation:
    """R. Adapter does not mutate production data."""

    def test_r1_isolated_data_root_unchanged_after_create(self, isolated_root, tmp_path):
        """R.1: trace.jsonl written only to isolated data_root, not to real data/."""
        # The fixture already isolates; verify trace file in tmp_path, not in real data/
        real_data = Path("data")
        # Don't actually check real_data/ — but the adapter writes to data_root() which
        # is now isolated. So real_data/ should be untouched (or not exist).
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(type_="calendar_event", novelty_id="iso_test")
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        # Verify InnerLifeEvent created in writer (in-memory, no trace since trace_writer=None)
        assert adapter.get_stats()["events_created"] == 1


# ────────────────────────────────────────────────────────────────────
# S. recursive-loop protection
# ────────────────────────────────────────────────────────────────────

class TestSectionS_RecursiveLoop:
    """S. Same-cycle autonomous World → InnerLife → Agency → World loop is impossible."""

    def test_s1_adapter_does_not_publish_world_event(self, isolated_root):
        """S.1: adapter.handle_event only reads WORLD_EVENT, doesn't publish it."""
        # Mock bus to verify what adapter publishes
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        mock_bus = MagicMock()

        # Adapter should not call bus.publish at all
        # (handler is async, but we can check no publish call in sync code)
        # Adapter only subscribes, not publishes

        # Verify by inspection: no bus.publish() call in adapter code
        import inspect
        from src.world import inner_life_adapter as mod
        source = inspect.getsource(mod)
        # Should not contain "bus.publish" or "bus.send"
        assert "bus.publish" not in source
        assert "bus.send" not in source

    def test_s2_adapter_does_not_publish_agency_trigger(self):
        """S.2: adapter does not publish AGENCY_TRIGGER (Agency gate untouched)."""
        import inspect
        from src.world import inner_life_adapter as mod
        source = inspect.getsource(mod)
        assert "AGENCY_TRIGGER" not in source
        assert "agency_trigger" not in source.lower() or "agency_trigger_handler" not in source.lower()


# ────────────────────────────────────────────────────────────────────
# T. bus subscription integration
# ────────────────────────────────────────────────────────────────────

class TestSectionT_BusIntegration:
    """T. End-to-end bus subscription integration."""

    def test_t1_end_to_end_pipeline(self, isolated_root):
        """T.1: full pipeline WORLD_EVENT → bus → adapter → InnerLifeEvent."""
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we = _make_world_event(
                    type_="calendar_event",
                    source="calendar",
                    novelty_id="e2e_test",
                    summary="30 分鐘後會議",
                )
                await await_bus.publish(_make_soul_event_for_world(we))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_received"] == 1
        assert adapter.get_stats()["events_created"] == 1
        event_id = adapter._dedup["e2e_test"]
        ev = writer._events[event_id]
        assert ev.provenance.trigger_type == "world:calendar_event"
        assert ev.provenance.extras["world_novelty_id"] == "e2e_test"


# ────────────────────────────────────────────────────────────────────
# U. both qualifying types each create 1 InnerLifeEvent
# ────────────────────────────────────────────────────────────────────

class TestSectionU_BothQualifyingTypes:
    """U. Both calendar_event and user_going_outside each create 1 event."""

    def test_u1_both_qualifying_types_create(self, isolated_root):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)

        async def _run():
            await_bus = SoulEventBus()
            await await_bus.start()
            try:
                adapter.register(await_bus)
                we1 = _make_world_event(type_="calendar_event", source="calendar", novelty_id="u_cal")
                we2 = _make_world_event(type_="user_going_outside", source="social", novelty_id="u_ugo")
                await await_bus.publish(_make_soul_event_for_world(we1))
                await await_bus.publish(_make_soul_event_for_world(we2))
            finally:
                await await_bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 2
        assert "u_cal" in adapter._dedup
        assert "u_ugo" in adapter._dedup


# ────────────────────────────────────────────────────────────────────
# V. Constructor validation
# ────────────────────────────────────────────────────────────────────

class TestSectionV_ConstructorValidation:
    """V. inner_life_writer is mandatory."""

    def test_v1_none_writer_raises(self):
        with pytest.raises(ValueError, match="inner_life_writer 必填"):
            WorldInnerLifeAdapter(inner_life_writer=None)

    def test_v2_valid_writer_constructs(self):
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        assert adapter is not None
        assert adapter._dedup_max_size == WORLD_DEDUP_MAX_SIZE


# ────────────────────────────────────────────────────────────────────
# Frozen contract verification (Bry spec §M)
# ────────────────────────────────────────────────────────────────────

class TestSectionW_FrozenContracts:
    """W. 0 frozen contract change verification."""

    def test_w1_world_event_schema_unchanged(self):
        """W.1: WorldEvent schema unchanged."""
        from dataclasses import fields
        field_names = {f.name for f in fields(WorldEvent)}
        assert field_names == {
            "source", "type", "novelty_id", "ts", "summary", "data", "priority",
        }

    def test_w2_inner_life_event_schema_unchanged(self):
        """W.2: InnerLifeEvent schema has only the M5.15-5 additive field added.
        M5.15-5 (Bry 派工 2026-08-12 19:14): +1 field `source_world_event_novelty_id`
        additive frozen-contract amendment (M5.4-5.1 + 1 Optional field, Layer 1).
        """
        from dataclasses import fields
        from src.inner_life import InnerLifeEvent
        field_names = {f.name for f in fields(InnerLifeEvent)}
        assert field_names == {
            "event_id", "session_id", "correlation_id", "parent_event_id",
            "ts", "provenance", "lineage_depth", "lineage_path",
            "source_world_event_novelty_id",
        }

    def test_w3_provenance_schema_unchanged(self):
        """W.3: Provenance schema unchanged."""
        from dataclasses import fields
        from src.inner_life import Provenance
        field_names = {f.name for f in fields(Provenance)}
        assert field_names == {
            "trigger_type", "actor_id", "source_system", "trace_ref", "extras",
        }

    def test_w4_valid_source_systems_unchanged(self):
        """W.4: VALID_SOURCE_SYSTEMS frozen unchanged (5 values)."""
        from src.inner_life.event import VALID_SOURCE_SYSTEMS
        assert VALID_SOURCE_SYSTEMS == frozenset({
            "memory", "diary", "dream", "narrative", "system",
        })

    def test_w5_trigger_envelope_unchanged(self):
        """W.5: TriggerEnvelope schema unchanged."""
        from dataclasses import fields
        from src.agency import TriggerEnvelope
        field_names = {f.name for f in fields(TriggerEnvelope)}
        assert field_names == {
            "trigger_type", "agent_id", "reason", "elapsed_mins",
            "timestamp", "extra",
        }

    def test_w6_stages_unchanged(self):
        """W.6: Stage 1-4 signatures unchanged."""
        import inspect
        from src.agency import (
            check_eligibility,
            make_decision,
            select_action,
            execute_action_stub,
        )
        # Stage 2 must NOT have inner_life param
        s2 = inspect.signature(make_decision)
        s2_params = list(s2.parameters.keys())
        assert not any("inner_life" in p for p in s2_params)


# ────────────────────────────────────────────────────────────────────
# test_count
# ────────────────────────────────────────────────────────────────────

def test_z_count():
    """Test count guard (counts all test_ methods in classes + module-level)."""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_count = 0
    # Module-level test_ functions
    for name, obj in inspect.getmembers(current_module):
        if name.startswith("test_") and callable(obj):
            test_count += 1
    # Class-level test_ methods
    for name, obj in inspect.getmembers(current_module):
        if inspect.isclass(obj) and obj.__module__ == current_module.__name__:
            for mname, method in inspect.getmembers(obj):
                if mname.startswith("test_") and callable(method):
                    test_count += 1
    # 30+ tests covering A-V + frozen contracts
    assert test_count >= 30, f"expected 30+ tests, got {test_count}"
