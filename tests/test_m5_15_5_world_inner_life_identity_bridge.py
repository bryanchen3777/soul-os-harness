"""
tests/test_m5_15_5_world_inner_life_identity_bridge.py — M5.15-5 WorldEvent ↔ InnerLifeEvent Identity Bridge

M5.15-5 (Bry 派工 2026-08-12 19:14) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE / M5.4-5.1 frozen-contract amendment

驗證:
  - source_world_event_novelty_id 存在 + 預設 None
  - WorldEvent → InnerLifeEvent identity bridge 正確
  - Serialization round-trip
  - 5 現有 producer (Diary / Dream / Event / ProactiveDM / Conversation) 0 改動
  - parent_event_id / lineage_depth / lineage_path / correlation_id / provenance 全部 unchanged
  - M5.15-3 canonical bus path 仍運作

Test sections (per M5.15-5 work order):
  A. WorldEvent → InnerLifeEvent identity propagation
  B. Exact novelty_id preservation
  C. Serialization round-trip
  D. Deserialization round-trip
  E. None behavior for non-WorldEvent producers
  F. parent_event_id remains unchanged
  G. lineage_depth remains unchanged
  H. lineage_path remains unchanged
  I. correlation_id remains unchanged
  J. provenance remains unchanged
  K. Multiple WorldEvents preserve independent identities
  L. M5.15-3 canonical Event Bus path still works
  M. WorldEvent.novelty_id ≠ InnerLifeEvent.event_id (regression guard)
  N. WorldEvent.novelty_id ≠ parent_event_id (regression guard)
  O. Edge cases: malformed / empty / non-str novelty_id (per WorldEvent domain)
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
    IdentityValidationError,
    InnerLifeEvent,
    InnerLifeWriter,
    Provenance,
    event_from_dict,
    event_to_dict,
    validate_source_world_event_novelty_id,
)
from src.inner_life.trace import NarrativeTraceWriter
from src.paths import data_root, reset_data_root
from src.world import SyntheticWorldEventSource
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


def _make_world_event(
    type_: str,
    source: str = "calendar",
    novelty_id: str = None,
    summary: str = "test world event",
    priority: int = 0,
) -> WorldEvent:
    import uuid
    if novelty_id is None:
        novelty_id = uuid.uuid4().hex
    return WorldEvent(
        source=source,
        type=type_,
        novelty_id=novelty_id,
        ts="2026-08-12T19:00:00+00:00",
        summary=summary,
        data={},
        priority=priority,
    )


def _make_soul_world_event(world_event: WorldEvent) -> SoulEvent:
    return SoulEvent(
        event_type=EventType.WORLD_EVENT,
        source="test_world_source",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=world_event.to_payload(),
    )


def _production_style_wire(tmp_path: Path) -> tuple:
    """
    Replicate production wiring: bus + writer + adapter.
    Returns (bus, writer, adapter) tuple.
    """
    bus = SoulEventBus()
    inner_life_writer = InnerLifeWriter(trace_writer=None)
    adapter = WorldInnerLifeAdapter(inner_life_writer=inner_life_writer)
    adapter.register(bus=bus)
    return bus, inner_life_writer, adapter


def _make_basic_prov() -> Provenance:
    """Standard narrative provenance for tests."""
    return Provenance(
        trigger_type="test:trigger",
        actor_id=None,
        source_system="narrative",
        trace_ref=None,
        extras={},
    )


@pytest.fixture
def isolated_root(tmp_path: Path):
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


# ────────────────────────────────────────────────────────────────────
# A. WorldEvent → InnerLifeEvent identity propagation
# ────────────────────────────────────────────────────────────────────

class TestSectionA_WorldEventToInnerLifeEventIdentity:
    """A. WorldEvent → InnerLifeEvent identity bridge (canonical path)."""

    def test_a1_adapter_propagates_world_event_novelty_id(
        self, isolated_root, tmp_path
    ):
        """A.1: WorldInnerLifeAdapter sets source_world_event_novelty_id from WorldEvent."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        we = _make_world_event(
            type_="calendar_event",
            source="calendar",
            novelty_id="a1_cal_weather_rain_2026",
        )

        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()

        asyncio.run(_run())
        # Adapter created 1 InnerLifeEvent
        assert adapter.get_stats()["events_created"] == 1
        # Identity bridge set
        assert "a1_cal_weather_rain_2026" in adapter._dedup
        event_id = adapter._dedup["a1_cal_weather_rain_2026"]
        ev = writer._events[event_id]
        # M5.15-5: source_world_event_novelty_id == WorldEvent.novelty_id
        assert ev.source_world_event_novelty_id == "a1_cal_weather_rain_2026"

    def test_a2_multiple_world_events_get_independent_bridges(
        self, isolated_root, tmp_path
    ):
        """A.2: 3 WorldEvents → 3 InnerLifeEvents, each with its own source_world_event_novelty_id."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        we_list = [
            _make_world_event(type_="calendar_event", novelty_id="a2_cal_001"),
            _make_world_event(type_="calendar_event", novelty_id="a2_cal_002"),
            _make_world_event(type_="user_going_outside", novelty_id="a2_ugo_001"),
        ]

        async def _run():
            await bus.start()
            try:
                for we in we_list:
                    await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()

        asyncio.run(_run())
        assert adapter.get_stats()["events_created"] == 3
        # Each InnerLifeEvent has its own source_world_event_novelty_id
        for we in we_list:
            assert we.novelty_id in adapter._dedup
            ev = writer._events[adapter._dedup[we.novelty_id]]
            assert ev.source_world_event_novelty_id == we.novelty_id

    def test_a3_has_world_event_source_helper(self, isolated_root, tmp_path):
        """A.3: InnerLifeEvent.has_world_event_source() helper works."""
        bus, writer, adapter = _production_style_wire(tmp_path)

        async def _run():
            await bus.start()
            try:
                # WorldEvent-triggered
                we = _make_world_event(type_="calendar_event", novelty_id="a3_world")
                await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()

        asyncio.run(_run())
        ev_with = writer._events[adapter._dedup["a3_world"]]
        assert ev_with.has_world_event_source() is True
        # Direct create (no WorldEvent) → no source
        ev_without = writer.create_event(provenance=_make_basic_prov())
        assert ev_without.has_world_event_source() is False
        assert ev_without.source_world_event_novelty_id is None


# ────────────────────────────────────────────────────────────────────
# B. Exact novelty_id preservation
# ────────────────────────────────────────────────────────────────────

class TestSectionB_ExactNoveltyIdPreservation:
    """B. novelty_id value is preserved exactly (no transformation)."""

    def test_b1_string_preserved_verbatim(self):
        """B.1: writer.create_event stores novelty_id verbatim."""
        writer = InnerLifeWriter(trace_writer=None)
        prov = Provenance(
            trigger_type="world:rain_started",
            source_system="narrative",
            extras={"world_novelty_id": "weather_rain_20260812"},
        )
        ev = writer.create_event(
            provenance=prov,
            source_world_event_novelty_id="weather_rain_20260812",
        )
        assert ev.source_world_event_novelty_id == "weather_rain_20260812"
        # Not transformed, not stripped, not converted
        assert isinstance(ev.source_world_event_novelty_id, str)

    def test_b2_unicode_and_dashes_preserved(self):
        """B.2: novelty_id with unicode, dashes, dots preserved."""
        writer = InnerLifeWriter(trace_writer=None)
        prov = _make_basic_prov()
        weird_ids = [
            "calendar_meeting-2026-08-12_15.30",
            "weather_rain_2026_08_12_TW",
            "social:bry-going-outside-下午",
        ]
        for nid in weird_ids:
            ev = writer.create_event(
                provenance=prov,
                source_world_event_novelty_id=nid,
            )
            assert ev.source_world_event_novelty_id == nid

    def test_b3_typical_world_event_ids(self):
        """B.3: typical WorldEvent.novelty_id formats all preserved."""
        writer = InnerLifeWriter(trace_writer=None)
        prov = _make_basic_prov()
        # Common patterns from SyntheticWorldEventSource
        ids = [
            "weather_rain_20260807",
            "news_celebrity_20260807_001",
            "calendar_meeting_20260807_1500",
            "weather_temp_20260807",
            "social_bry_going_outside_20260807",
        ]
        for nid in ids:
            ev = writer.create_event(
                provenance=prov,
                source_world_event_novelty_id=nid,
            )
            assert ev.source_world_event_novelty_id == nid


# ────────────────────────────────────────────────────────────────────
# C. Serialization round-trip
# ────────────────────────────────────────────────────────────────────

class TestSectionC_SerializationRoundTrip:
    """C. event_to_dict includes the new field."""

    def test_c1_event_to_dict_includes_field(self):
        """C.1: event_to_dict output contains source_world_event_novelty_id key."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="c1_weather_rain",
        )
        d = event_to_dict(ev)
        assert "source_world_event_novelty_id" in d
        assert d["source_world_event_novelty_id"] == "c1_weather_rain"

    def test_c2_event_to_dict_default_none(self):
        """C.2: event_to_dict with default None preserves None."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(provenance=_make_basic_prov())
        d = event_to_dict(ev)
        assert d["source_world_event_novelty_id"] is None

    def test_c3_event_to_dict_full_round_trip(self):
        """C.3: to_dict → from_dict → to_dict gives same dict."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="c3_calendar_15:00",
        )
        d1 = event_to_dict(ev)
        ev2 = event_from_dict(d1)
        d2 = event_to_dict(ev2)
        assert d1 == d2

    def test_c4_event_from_dict_with_field(self):
        """C.4: event_from_dict restores the field."""
        writer = InnerLifeWriter(trace_writer=None)
        ev_orig = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="c4_news_celeb",
        )
        d = event_to_dict(ev_orig)
        ev_restored = event_from_dict(d)
        assert ev_restored.source_world_event_novelty_id == "c4_news_celeb"
        assert ev_restored.event_id == ev_orig.event_id


# ────────────────────────────────────────────────────────────────────
# D. Deserialization round-trip (backward compat with old payloads)
# ────────────────────────────────────────────────────────────────────

class TestSectionD_DeserializationBackwardCompat:
    """D. Old payloads (without the new field) deserialize correctly with default None."""

    def test_d1_old_payload_without_field(self):
        """D.1: Old payload (no source_world_event_novelty_id) → field=None."""
        old_d = {
            "event_id": "a1b2c3d4e5f6789012345678abcdef00",
            "session_id": None,
            "correlation_id": None,
            "parent_event_id": None,
            "ts": "2026-08-12T19:00:00+00:00",
            "provenance": {
                "trigger_type": "test:trigger",
                "actor_id": None,
                "source_system": "narrative",
                "trace_ref": None,
                "extras": {},
            },
            "lineage_depth": 0,
            "lineage_path": "a1b2c3d4e5f6789012345678abcdef00",
        }
        ev = event_from_dict(old_d)
        assert ev.source_world_event_novelty_id is None
        # All other fields preserved
        assert ev.event_id == "a1b2c3d4e5f6789012345678abcdef00"
        assert ev.parent_event_id is None
        assert ev.lineage_depth == 0

    def test_d2_payload_with_explicit_none(self):
        """D.2: Payload with explicit source_world_event_novelty_id=None works."""
        d = {
            "event_id": "b2c3d4e5f6789012345678abcdef0012",
            "session_id": None,
            "correlation_id": None,
            "parent_event_id": None,
            "ts": "2026-08-12T19:00:00+00:00",
            "provenance": {
                "trigger_type": "test:trigger",
                "actor_id": None,
                "source_system": "narrative",
                "trace_ref": None,
                "extras": {},
            },
            "lineage_depth": 0,
            "lineage_path": "b2c3d4e5f6789012345678abcdef0012",
            "source_world_event_novelty_id": None,
        }
        ev = event_from_dict(d)
        assert ev.source_world_event_novelty_id is None

    def test_d3_payload_with_field(self):
        """D.3: Payload with field value deserializes correctly."""
        d = {
            "event_id": "c3d4e5f6789012345678abcdef001234",
            "session_id": None,
            "correlation_id": None,
            "parent_event_id": None,
            "ts": "2026-08-12T19:00:00+00:00",
            "provenance": {
                "trigger_type": "test:trigger",
                "actor_id": None,
                "source_system": "narrative",
                "trace_ref": None,
                "extras": {},
            },
            "lineage_depth": 0,
            "lineage_path": "c3d4e5f6789012345678abcdef001234",
            "source_world_event_novelty_id": "weather_rain_d3",
        }
        ev = event_from_dict(d)
        assert ev.source_world_event_novelty_id == "weather_rain_d3"


# ────────────────────────────────────────────────────────────────────
# E. None behavior for non-WorldEvent producers
# ────────────────────────────────────────────────────────────────────

class TestSectionE_NoneForNonWorldEventProducers:
    """E. 5 existing producers (Diary / Dream / Event / ProactiveDM / Conversation) keep None."""

    def test_e1_create_event_default_none(self):
        """E.1: create_event without new param → field=None (backward compat)."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(provenance=_make_basic_prov())
        assert ev.source_world_event_novelty_id is None

    def test_e2_create_event_explicit_none(self):
        """E.2: create_event with explicit source_world_event_novelty_id=None."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id=None,
        )
        assert ev.source_world_event_novelty_id is None

    def test_e3_diary_style_event_unchanged(self):
        """E.3: Diary-style event (trigger_type=diary:morning) has None."""
        writer = InnerLifeWriter(trace_writer=None)
        prov = Provenance(
            trigger_type="diary:morning",
            source_system="diary",
        )
        ev = writer.create_event(provenance=prov)
        assert ev.source_world_event_novelty_id is None

    def test_e4_dream_style_event_unchanged(self):
        """E.4: Dream-style event (trigger_type=dream:dream) has None."""
        writer = InnerLifeWriter(trace_writer=None)
        prov = Provenance(trigger_type="dream:dream", source_system="dream")
        ev = writer.create_event(provenance=prov)
        assert ev.source_world_event_novelty_id is None

    def test_e5_agency_trigger_style_event_unchanged(self):
        """E.5: ProactiveDM / Event style (trigger_type=proactive_dm or event) has None."""
        writer = InnerLifeWriter(trace_writer=None)
        for trigger in ("proactive_dm", "event"):
            prov = Provenance(
                trigger_type=trigger,
                source_system="narrative",
            )
            ev = writer.create_event(provenance=prov)
            assert ev.source_world_event_novelty_id is None

    def test_e6_conversation_qualifier_style_unchanged(self):
        """E.6: ConversationQualifier-style (user_message/agent_reply) has None."""
        writer = InnerLifeWriter(trace_writer=None)
        for trigger in ("user_message", "agent_reply"):
            prov = Provenance(
                trigger_type=trigger,
                source_system="memory",
            )
            ev = writer.create_event(provenance=prov)
            assert ev.source_world_event_novelty_id is None


# ────────────────────────────────────────────────────────────────────
# F. parent_event_id remains unchanged
# ────────────────────────────────────────────────────────────────────

class TestSectionF_ParentEventIdUnchanged:
    """F. parent_event_id contract (M5.4-5.1 frozen) is preserved 100%."""

    def test_f1_parent_event_id_validation_unchanged(self):
        """F.1: parent_event_id must still be a valid 32-hex event_id format."""
        writer = InnerLifeWriter(trace_writer=None)
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                parent_event_id="not-a-32-hex-id",  # 32-hex format required
                source_world_event_novelty_id="f1_weather",
            )

    def test_f2_parent_event_id_existence_check_unchanged(self):
        """F.2: parent_event_id must still be in _known_event_ids."""
        writer = InnerLifeWriter(trace_writer=None)
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                parent_event_id="a1b2c3d4e5f6789012345678abcdef01",  # 32 hex but not in writer
                source_world_event_novelty_id="f2_weather",
            )

    def test_f3_parent_event_id_still_works_when_valid(self):
        """F.3: Valid parent_event_id + new field both work together."""
        writer = InnerLifeWriter(trace_writer=None)
        # Create a parent first
        parent = writer.create_event(provenance=_make_basic_prov())
        # Create a child with valid parent + new field
        child = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=parent.event_id,
            source_world_event_novelty_id="f3_weather",
        )
        # Both fields work
        assert child.parent_event_id == parent.event_id
        assert child.source_world_event_novelty_id == "f3_weather"
        # Lineage still derived from parent (Layer 2 unchanged)
        assert child.lineage_depth == 1
        assert child.lineage_path == f"{parent.event_id}/{child.event_id}"

    def test_f4_source_world_event_novelty_id_does_not_count_for_parent_validation(
        self, isolated_root, tmp_path
    ):
        """F.4: New field is NOT in _known_event_ids; doesn't satisfy parent existence check."""
        writer = InnerLifeWriter(trace_writer=None)
        # Create event with source_world_event_novelty_id set
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="f4_weather_rain",
        )
        # Try to use the source_world_event_novelty_id as parent_event_id
        # Should FAIL because it's not in _known_event_ids
        # (and also doesn't match 32-hex format)
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                parent_event_id="f4_weather_rain",  # NOT a 32-hex id
            )


# ────────────────────────────────────────────────────────────────────
# G. lineage_depth remains unchanged
# ────────────────────────────────────────────────────────────────────

class TestSectionG_LineageDepthUnchanged:
    """G. lineage_depth semantics (M5.4-5.1 frozen) preserved."""

    def test_g1_root_event_with_source_field_has_depth_zero(self):
        """G.1: WorldEvent-triggered event (no parent) has lineage_depth=0."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="g1_weather",
        )
        assert ev.lineage_depth == 0  # Root, M5.4-5.1 frozen semantics

    def test_g2_child_event_uses_parent_depth_plus_one(self):
        """G.2: lineage_depth = parent.lineage_depth + 1 (parent-only, not source)."""
        writer = InnerLifeWriter(trace_writer=None)
        parent = writer.create_event(provenance=_make_basic_prov())
        child = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=parent.event_id,
            source_world_event_novelty_id="g2_weather",
        )
        assert child.lineage_depth == parent.lineage_depth + 1
        assert child.lineage_depth == 1

    def test_g3_grandchild_event_depth_two(self):
        """G.3: Grandchild lineage_depth=2 (parent chain, not source chain)."""
        writer = InnerLifeWriter(trace_writer=None)
        a = writer.create_event(provenance=_make_basic_prov())
        b = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=a.event_id,
        )
        c = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=b.event_id,
            source_world_event_novelty_id="g3_grandchild_world",
        )
        assert a.lineage_depth == 0
        assert b.lineage_depth == 1
        assert c.lineage_depth == 2


# ────────────────────────────────────────────────────────────────────
# H. lineage_path remains unchanged
# ────────────────────────────────────────────────────────────────────

class TestSectionH_LineagePathUnchanged:
    """H. lineage_path semantics (M5.4-5.1 frozen) preserved."""

    def test_h1_root_event_path_equals_event_id(self):
        """H.1: Root event lineage_path = own event_id (regardless of source field)."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="h1_weather",
        )
        assert ev.lineage_path == ev.event_id

    def test_h2_child_event_path_is_parent_path_plus_own_id(self):
        """H.2: Child lineage_path = parent.lineage_path + "/" + own event_id."""
        writer = InnerLifeWriter(trace_writer=None)
        parent = writer.create_event(provenance=_make_basic_prov())
        child = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=parent.event_id,
            source_world_event_novelty_id="h2_weather",
        )
        assert child.lineage_path == f"{parent.event_id}/{child.event_id}"

    def test_h3_grandchild_event_path_3_levels(self):
        """H.3: Grandchild path is 3-level (parent_path + own_id), no source in path."""
        writer = InnerLifeWriter(trace_writer=None)
        a = writer.create_event(provenance=_make_basic_prov())
        b = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=a.event_id,
        )
        c = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=b.event_id,
            source_world_event_novelty_id="h3_weather",
        )
        assert c.lineage_path == f"{a.event_id}/{b.event_id}/{c.event_id}"
        # source_world_event_novelty_id is NOT in lineage_path (Layer 2 is parent-only)


# ────────────────────────────────────────────────────────────────────
# I. correlation_id remains unchanged
# ────────────────────────────────────────────────────────────────────

class TestSectionI_CorrelationIdUnchanged:
    """I. correlation_id semantics (M5.4-5.1: NOT causation) preserved."""

    def test_i1_correlation_id_independent_from_source_field(self):
        """I.1: correlation_id and source_world_event_novelty_id are independent."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            correlation_id="i1_narrative_group",
            source_world_event_novelty_id="i1_weather_rain",
        )
        assert ev.correlation_id == "i1_narrative_group"
        assert ev.source_world_event_novelty_id == "i1_weather_rain"
        # correlation_id is NOT causation (M5.4-5.1 explicit)
        # source_world_event_novelty_id IS causality (Layer 1)
        # Two separate concepts, both present

    def test_i2_correlation_id_can_be_same_across_world_events(self):
        """I.2: Multiple WorldEvents can share correlation_id (narrative group)."""
        writer = InnerLifeWriter(trace_writer=None)
        a = writer.create_event(
            provenance=_make_basic_prov(),
            correlation_id="i2_morning_routine",
            source_world_event_novelty_id="i2_weather_001",
        )
        b = writer.create_event(
            provenance=_make_basic_prov(),
            correlation_id="i2_morning_routine",
            source_world_event_novelty_id="i2_weather_002",
        )
        assert a.correlation_id == b.correlation_id == "i2_morning_routine"
        assert a.source_world_event_novelty_id != b.source_world_event_novelty_id


# ────────────────────────────────────────────────────────────────────
# J. provenance remains unchanged
# ────────────────────────────────────────────────────────────────────

class TestSectionJ_ProvenanceUnchanged:
    """J. Provenance schema (M5.4-5.1 frozen) preserved."""

    def test_j1_provenance_structure_unchanged(self):
        """J.1: Provenance 5 fields preserved; new field is on InnerLifeEvent, not Provenance."""
        from dataclasses import fields
        prov_fields = {f.name for f in fields(Provenance)}
        assert prov_fields == {
            "trigger_type",
            "actor_id",
            "source_system",
            "trace_ref",
            "extras",
        }

    def test_j2_provenance_extras_unchanged(self):
        """J.2: Adapter still uses extras={world_source, world_type, world_novelty_id} (no change)."""
        bus, writer, adapter = _production_style_wire(Path("/tmp"))
        we = _make_world_event(type_="calendar_event", novelty_id="j2_calendar")
        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()
        asyncio.run(_run())
        ev = writer._events[adapter._dedup["j2_calendar"]]
        # Provenance unchanged
        assert ev.provenance.trigger_type == "world:calendar_event"
        assert ev.provenance.source_system == "narrative"
        assert ev.provenance.extras == {
            "world_source": "calendar",
            "world_type": "calendar_event",
            "world_novelty_id": "j2_calendar",
        }
        # The new field is on InnerLifeEvent, NOT in Provenance
        assert ev.source_world_event_novelty_id == "j2_calendar"
        assert "source_world_event_novelty_id" not in ev.provenance.extras

    def test_j3_valid_source_systems_unchanged(self):
        """J.3: VALID_SOURCE_SYSTEMS unchanged (5 values)."""
        from src.inner_life import VALID_SOURCE_SYSTEMS
        assert VALID_SOURCE_SYSTEMS == frozenset(
            {"memory", "diary", "dream", "narrative", "system"}
        )


# ────────────────────────────────────────────────────────────────────
# K. Multiple WorldEvents preserve independent identities
# ────────────────────────────────────────────────────────────────────

class TestSectionK_MultipleWorldEventsIndependent:
    """K. Multiple WorldEvents each get independent identity bridges."""

    def test_k1_two_world_events_different_novelty_ids(
        self, isolated_root, tmp_path
    ):
        """K.1: Two WorldEvents with different novelty_ids → two independent bridges."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        we1 = _make_world_event(type_="calendar_event", novelty_id="k1_cal")
        we2 = _make_world_event(type_="calendar_event", novelty_id="k1_ugo")

        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we1))
                await bus.publish(_make_soul_world_event(we2))
            finally:
                await bus.stop()

        asyncio.run(_run())
        ev1 = writer._events[adapter._dedup["k1_cal"]]
        ev2 = writer._events[adapter._dedup["k1_ugo"]]
        # Different novelty_id
        assert ev1.source_world_event_novelty_id == "k1_cal"
        assert ev2.source_world_event_novelty_id == "k1_ugo"
        # Different event_id
        assert ev1.event_id != ev2.event_id
        # Different provenance
        assert ev1.event_id != ev2.event_id

    def test_k2_dedup_replay_same_world_event_only_creates_one(
        self, isolated_root, tmp_path
    ):
        """K.2: Same novelty_id published 3x → 1 InnerLifeEvent (adapter dedup)."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        async def _run():
            await bus.start()
            try:
                for _ in range(3):
                    we = _make_world_event(type_="calendar_event", novelty_id="k2_dup")
                    await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()
        asyncio.run(_run())
        # Adapter received 3, created 1
        assert adapter.get_stats()["events_received"] == 3
        assert adapter.get_stats()["events_created"] == 1
        # Writer has 1 InnerLifeEvent with the novelty_id
        assert len(adapter._dedup) == 1
        ev = writer._events[adapter._dedup["k2_dup"]]
        assert ev.source_world_event_novelty_id == "k2_dup"


# ────────────────────────────────────────────────────────────────────
# L. M5.15-3 canonical Event Bus path still works
# ────────────────────────────────────────────────────────────────────

class TestSectionL_M5_15_3_CanonicalBusPath:
    """L. M5.15-3 canonical bus path (Source → bus → middleware + adapter) preserved."""

    def test_l1_source_to_bus_to_middleware_to_adapter_end_to_end(
        self, isolated_root, tmp_path
    ):
        """L.1: Full canonical path: SyntheticSource(bus) → bus → middleware + adapter → InnerLifeEvent."""
        from src.world import WorldPerceptionMiddleware, WorldPerceptionState
        bus = SoulEventBus()
        state = WorldPerceptionState()
        trace_writer = WorldPerceptionTraceWriter(
            trace_log_path=tmp_path / "perception_trace.jsonl"
        )
        world_perception = WorldPerceptionMiddleware(
            bus=bus, state=state, trace_writer=trace_writer,
        )
        world_perception.register()
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus=bus)
        source = SyntheticWorldEventSource(bus=bus)

        async def _run():
            await bus.start()
            try:
                await source.emit_event(
                    type="calendar_event",
                    summary="end to end test",
                    novelty_id="l1_e2e_calendar",
                )
            finally:
                await bus.stop()

        asyncio.run(_run())
        # M5.15-3 path: source → bus → middleware + adapter
        assert world_perception._events_state_added == 1
        assert adapter.get_stats()["events_created"] == 1
        # M5.15-5 path: InnerLifeEvent has identity bridge
        ev = writer._events[adapter._dedup["l1_e2e_calendar"]]
        assert ev.source_world_event_novelty_id == "l1_e2e_calendar"
        # M5.15-3 path: middleware received via bus (1x)
        assert world_perception._events_received == 1
        # M5.15-3 path: no double perception
        # (verified by 1 == events_state_added above)


# ────────────────────────────────────────────────────────────────────
# M. WorldEvent.novelty_id ≠ InnerLifeEvent.event_id (regression guard)
# ────────────────────────────────────────────────────────────────────

class TestSectionM_RegressionGuardNoveltyVsEventId:
    """M. WorldEvent.novelty_id is NOT the same as InnerLifeEvent.event_id."""

    def test_m1_world_event_novelty_id_distinct_from_inner_life_event_id(
        self, isolated_root, tmp_path
    ):
        """M.1: WorldEvent.novelty_id ('weather_rain_001') ≠ InnerLifeEvent.event_id (32 hex)."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        # Use a clearly-distinguishable WorldEvent.novelty_id
        we = _make_world_event(type_="calendar_event", novelty_id="m1_NOT_HEX_weather_rain_001")
        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()
        asyncio.run(_run())
        ev = writer._events[adapter._dedup["m1_NOT_HEX_weather_rain_001"]]
        # InnerLifeEvent.event_id is 32-char lowercase hex
        import re
        assert re.match(r"^[0-9a-f]{32}$", ev.event_id), \
            f"InnerLifeEvent.event_id should be 32-hex, got: {ev.event_id!r}"
        # source_world_event_novelty_id is the WorldEvent.novelty_id (NOT 32-hex)
        assert ev.source_world_event_novelty_id == "m1_NOT_HEX_weather_rain_001"
        # Clearly different domains
        assert ev.event_id != ev.source_world_event_novelty_id

    def test_m2_world_event_novelty_id_not_used_as_event_id(self):
        """M.2: InnerLifeWriter does NOT use source_world_event_novelty_id as event_id."""
        writer = InnerLifeWriter(trace_writer=None)
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="m2_NOT_32_HEX_weather_rain",
        )
        # event_id is auto-generated, NOT taken from source_world_event_novelty_id
        import re
        assert re.match(r"^[0-9a-f]{32}$", ev.event_id)
        assert ev.event_id != "m2_NOT_32_HEX_weather_rain"

    def test_m3_writer_does_not_collapse_two_world_events_to_same_inner_life(
        self, isolated_root, tmp_path
    ):
        """M.3: 2 WorldEvents with very different novelty_ids → 2 distinct InnerLifeEvents."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        we1 = _make_world_event(type_="calendar_event", novelty_id="m3_alpha")
        we2 = _make_world_event(type_="user_going_outside", novelty_id="m3_beta")
        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we1))
                await bus.publish(_make_soul_world_event(we2))
            finally:
                await bus.stop()
        asyncio.run(_run())
        ev1 = writer._events[adapter._dedup["m3_alpha"]]
        ev2 = writer._events[adapter._dedup["m3_beta"]]
        # Distinct InnerLifeEvent.event_ids (32 hex each)
        assert ev1.event_id != ev2.event_id
        # Distinct source_world_event_novelty_ids (free string)
        assert ev1.source_world_event_novelty_id == "m3_alpha"
        assert ev2.source_world_event_novelty_id == "m3_beta"
        # Neither event_id equals the other's source_world_event_novelty_id
        assert ev1.event_id != ev2.source_world_event_novelty_id
        assert ev2.event_id != ev1.source_world_event_novelty_id


# ────────────────────────────────────────────────────────────────────
# N. WorldEvent.novelty_id ≠ parent_event_id (regression guard)
# ────────────────────────────────────────────────────────────────────

class TestSectionN_RegressionGuardNoveltyVsParentId:
    """N. WorldEvent.novelty_id is NOT used as parent_event_id."""

    def test_n1_world_event_novelty_id_cannot_be_parent_event_id(
        self, isolated_root, tmp_path
    ):
        """N.1: Using WorldEvent.novelty_id as parent_event_id fails (format + existence)."""
        bus, writer, adapter = _production_style_wire(tmp_path)
        we = _make_world_event(type_="calendar_event", novelty_id="n1_weather")
        async def _run():
            await bus.start()
            try:
                await bus.publish(_make_soul_world_event(we))
            finally:
                await bus.stop()
        asyncio.run(_run())
        ev = writer._events[adapter._dedup["n1_weather"]]
        # source_world_event_novelty_id is set
        assert ev.source_world_event_novelty_id == "n1_weather"
        # But it CANNOT be used as parent_event_id
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                parent_event_id="n1_weather",  # NOT 32-hex, NOT in _known_event_ids
            )

    def test_n2_parent_event_id_unchanged_even_with_source_field_set(self):
        """N.2: Setting source field does NOT affect parent_event_id (Layer 2 unchanged)."""
        writer = InnerLifeWriter(trace_writer=None)
        # Root event with source field set
        ev = writer.create_event(
            provenance=_make_basic_prov(),
            source_world_event_novelty_id="n2_weather_rain",
        )
        # parent_event_id remains None (M5.4-5.1 frozen: root event has no parent)
        assert ev.parent_event_id is None
        # Two layers are independent
        assert ev.source_world_event_novelty_id == "n2_weather_rain"

    def test_n3_legitimate_parent_event_id_still_works(self):
        """N.3: Real InnerLifeEvent parent + source field both work together."""
        writer = InnerLifeWriter(trace_writer=None)
        parent = writer.create_event(provenance=_make_basic_prov())
        child = writer.create_event(
            provenance=_make_basic_prov(),
            parent_event_id=parent.event_id,
            source_world_event_novelty_id="n3_weather",
        )
        # parent_event_id is 32-hex (InnerLifeEvent.event_id)
        assert child.parent_event_id == parent.event_id
        import re
        assert re.match(r"^[0-9-a-f]{32}$", child.parent_event_id)
        # source_world_event_novelty_id is free string
        assert child.source_world_event_novelty_id == "n3_weather"


# ────────────────────────────────────────────────────────────────────
# O. Edge cases: malformed / empty / non-str
# ────────────────────────────────────────────────────────────────────

class TestSectionO_EdgeCases:
    """O. Validator rejects malformed values; production rules preserved."""

    def test_o1_validator_rejects_non_string(self):
        """O.1: Non-string (int, list, dict) rejected."""
        for bad in [123, 1.5, [], {}, True]:
            with pytest.raises(IdentityValidationError):
                validate_source_world_event_novelty_id(bad)

    def test_o2_validator_rejects_empty_string(self):
        """O.2: Empty string rejected."""
        with pytest.raises(IdentityValidationError):
            validate_source_world_event_novelty_id("")

    def test_o3_validator_rejects_whitespace_only(self):
        """O.3: Whitespace-only string rejected."""
        with pytest.raises(IdentityValidationError):
            validate_source_world_event_novelty_id("   ")

    def test_o4_validator_accepts_none(self):
        """O.4: None accepted (default for non-WorldEvent producers)."""
        assert validate_source_world_event_novelty_id(None) is None

    def test_o5_validator_accepts_typical_world_event_novelty_id(self):
        """O.5: Typical WorldEvent.novelty_id formats accepted."""
        valid = [
            "weather_rain_20260812",
            "calendar_meeting_20260812_15.30",
            "social:bry-going-outside-下午",
            "news:celebrity-2026-08-12-001",
        ]
        for v in valid:
            assert validate_source_world_event_novelty_id(v) == v

    def test_o6_validator_does_not_impose_32_hex_format(self):
        """O.6: Validator does NOT require 32-hex (that's event_id, different field)."""
        # Non-32-hex strings are VALID here (per M5.15-5 design)
        for v in [
            "weather_rain",  # short
            "x" * 100,  # long
            "UPPERCASE_WEATHER",  # uppercase
            "Mixed-Case-With-Dashes",
        ]:
            assert validate_source_world_event_novelty_id(v) == v

    def test_o7_writer_propagates_validator_errors(self):
        """O.7: InnerLifeWriter.create_event raises IdentityValidationError on bad input."""
        writer = InnerLifeWriter(trace_writer=None)
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                source_world_event_novelty_id="",  # empty
            )
        with pytest.raises(IdentityValidationError):
            writer.create_event(
                provenance=_make_basic_prov(),
                source_world_event_novelty_id=12345,  # non-str
            )

    def test_o8_novelty_id_domain_preserved_not_inner_life_event_id(self):
        """O.8: WorldEvent.novelty_id domain is NOT collapsed into InnerLifeEvent.event_id domain."""
        # Per M5.15-5: novelty_id free string, event_id 32-hex — distinct
        # Per M5.15-5 work order §13: don't accidentally impose 32-hex on novelty_id
        # This test ensures the validator does NOT require 32-hex
        nid = "calendar_meeting_2026-08-12_15.00_NOT_32HEX"
        result = validate_source_world_event_novelty_id(nid)
        assert result == nid  # accepted as-is
        # NOT transformed, NOT validated as 32-hex


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
    # 30+ tests covering A-O + z_count
    assert test_count >= 30, f"expected 30+ tests, got {test_count}"
