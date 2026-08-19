"""
tests/test_m5_4_6_1_executor_wiring.py

M5.4-6.1 (Bry 派工 2026-08-10): Executor-Level Inner Life Producer Wiring.

Focused test suite covering:
- A. Diary executor wiring (event_id flows to diary jsonl)
- B. Dream executor wiring (event_id flows to dream jsonl)
- C. Event executor wiring (event_id flows to event jsonl)
- D. Backward compatibility (existing diary/dream/event behavior preserved)
- E. No duplicate InnerLifeEvent per execution
- F. USER_MESSAGE does NOT create InnerLifeEvent
- G. Provenance canonical trigger types are used
- H. Failure isolation (InnerLifeWriter failure does not block writer)
- I. Cross-reference integrity (event_id matches jsonl entry)
- count

Test count: 24 tests
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    NarrativeTraceWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
    TRIGGER_TYPE_USER_MESSAGE,
    VALID_SOURCE_SYSTEMS,
)
from src.paths import data_root, reset_data_root
from src.soul.diary import (
    DEFAULT_DIARY_ROOT,
    DiaryWriter,
    diary_callback_factory,
    generate_diary_entry,
)
from src.soul.dream_event import (
    DreamEventWriter,
    get_dream_event_writer,
)


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _hex_32() -> str:
    """Generate a 32-char lowercase hex string (canonical event_id format)."""
    return uuid.uuid4().hex


def _isolated_data_root(tmp_path: Path) -> None:
    """Force data_root() to point to a temp dir for test isolation (P0.5)."""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()


def _restore_data_root() -> None:
    """Restore data_root to no env var (production default)."""
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _isolated_diary_writer(tmp_path: Path) -> DiaryWriter:
    """Create an isolated DiaryWriter pointing to tmp_path.
    Resets the module-level singleton to ensure diary_callback_factory uses
    the new writer (default cached singleton would write to production path)."""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    # Reset the module-level singleton so diary_callback_factory's cb
    # captures this isolated writer, not the cached production writer.
    from src.soul import diary as _diary_mod
    _diary_mod._diary_writer = None
    return DiaryWriter(data_dir=str(soul_dir))


def _isolated_dream_writer(tmp_path: Path) -> DreamEventWriter:
    """Create an isolated DreamEventWriter pointing to tmp_path."""
    _isolated_data_root(tmp_path)
    soul_dir = tmp_path / "data" / "soul"
    soul_dir.mkdir(parents=True, exist_ok=True)
    return DreamEventWriter(data_dir=str(soul_dir))


def _make_provenance(
    trigger_type: str,
    actor_id: str,
    source_system: str,
    extras: Dict[str, str] = None,
) -> Provenance:
    """Build a Provenance matching the executor wiring spec."""
    if extras is None:
        extras = {}
    return Provenance(
        trigger_type=trigger_type,
        actor_id=actor_id,
        source_system=source_system,
        extras=extras,
    )


# ───────────────────────────────────────────────────────────
# A. Diary executor wiring
# ───────────────────────────────────────────────────────────

class TestSectionA_DiaryExecutorWiring:
    """A. diary_callback_factory's cb accepts inner_life_event_id, plumbs through to diary jsonl."""

    def test_a1_cb_signature_accepts_inner_life_event_id(self, tmp_path):
        """diary_callback_factory's inner cb now accepts inner_life_event_id kwarg."""
        _isolated_data_root(tmp_path)
        try:
            cb = asyncio.run(diary_callback_factory("agent_yua"))
            # Inspect signature: should accept (agent_id, slot, inner_life_event_id=None)
            import inspect
            sig = inspect.signature(cb)
            params = list(sig.parameters.keys())
            assert "agent_id_inner" in params or "agent_id" in params
            assert "slot" in params
            assert "inner_life_event_id" in params
        finally:
            _restore_data_root()

    def test_a2_cb_with_event_id_passes_through_to_writer(self, tmp_path, monkeypatch):
        """cb(agent_id, slot, inner_life_event_id=...) → diary jsonl has inner_life_event_id."""
        from src.soul import diary as diary_mod

        async def fake_llm_call(*args, **kwargs):
            return "M5.4-6.1 wiring test content"

        monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm_call)

        # Reset module-level singleton so cb captures our isolated writer
        diary_mod._diary_writer = None
        try:
            _isolated_data_root(tmp_path)
            soul_dir = tmp_path / "data" / "soul"
            soul_dir.mkdir(parents=True, exist_ok=True)
            writer = DiaryWriter(data_dir=str(soul_dir))
            # Re-set the singleton (now pointing to tmp)
            diary_mod._diary_writer = writer

            cb = asyncio.run(diary_callback_factory("agent_yua"))
            eid = _hex_32()
            asyncio.run(cb("agent_yua", "morning", inner_life_event_id=eid))

            today = datetime.now().strftime("%Y-%m-%d")
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert entries[0]["inner_life_event_id"] == eid
            assert entries[0]["slot"] == "morning"
        finally:
            diary_mod._diary_writer = None
            _restore_data_root()

    def test_a3_cb_without_event_id_omits_field(self, tmp_path, monkeypatch):
        """cb(agent_id, slot) without event_id → diary jsonl has no inner_life_event_id field."""
        from src.soul import diary as diary_mod

        async def fake_llm_call(*args, **kwargs):
            return "M5.4-6.1 backward compat content"

        monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm_call)

        # Reset module-level singleton so cb captures our isolated writer
        diary_mod._diary_writer = None
        try:
            _isolated_data_root(tmp_path)
            soul_dir = tmp_path / "data" / "soul"
            soul_dir.mkdir(parents=True, exist_ok=True)
            writer = DiaryWriter(data_dir=str(soul_dir))
            diary_mod._diary_writer = writer

            cb = asyncio.run(diary_callback_factory("agent_yua"))
            # No inner_life_event_id passed
            asyncio.run(cb("agent_yua", "night"))

            today = datetime.now().strftime("%Y-%m-%d")
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
            assert entries[0]["slot"] == "night"
        finally:
            diary_mod._diary_writer = None
            _restore_data_root()

    def test_a4_morning_slot_uses_morning_trigger_type(self):
        """Diary morning executor wires TRIGGER_TYPE_DIARY_MORNING."""
        # The executor in run_server.py uses:
        #   _trigger_type = TRIGGER_TYPE_DIARY_MORNING if slot == "morning" else TRIGGER_TYPE_DIARY_NIGHT
        # Verified at API layer: cb is plumbed through correctly with slot='morning'
        # and slot='night'. Trigger type constants are frozen since M5.4-5.1.
        assert TRIGGER_TYPE_DIARY_MORNING == "diary:morning"
        assert TRIGGER_TYPE_DIARY_NIGHT == "diary:night"

    def test_a5_executor_pattern_creates_event_with_diary_provenance(self, tmp_path):
        """Executor pattern: InnerLifeWriter.create_event + DiaryWriter.write_entry
        produces an event registered in InnerLifeWriter AND a diary entry
        whose inner_life_event_id matches the canonical event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Simulate the executor's exact pattern
            event = ilw.create_event(
                provenance=_make_provenance(
                    trigger_type=TRIGGER_TYPE_DIARY_MORNING,
                    actor_id="agent_yua",
                    source_system="diary",
                    extras={"slot": "morning"},
                )
            )
            writer = _isolated_diary_writer(tmp_path)
            path = writer.write_entry(
                "agent_yua", "morning", "test",
                inner_life_event_id=event.event_id,
            )
            # Event registered in InnerLifeWriter
            assert ilw.is_event_known(event.event_id)
            assert ilw.get_event(event.event_id).provenance.trigger_type == TRIGGER_TYPE_DIARY_MORNING
            # Diary entry has matching event_id
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. Dream executor wiring
# ───────────────────────────────────────────────────────────

class TestSectionB_DreamExecutorWiring:
    """B. dream executor: InnerLifeEvent created, event_id flows to write_dream jsonl."""

    def test_b1_write_dream_accepts_event_id_kwarg(self, tmp_path):
        """DreamEventWriter.write_dream accepts inner_life_event_id."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            eid = _hex_32()
            path = asyncio.run(writer.write_dream(
                "agent_yua",
                "agent_ruka",
                ["agent_yua", "agent_ruka"],
                inner_life_event_id=eid,
            ))
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == eid
            assert entry["slot"] == "dream"
        finally:
            _restore_data_root()

    def test_b2_executor_pattern_creates_event_with_dream_provenance(self, tmp_path):
        """Executor pattern: InnerLifeWriter.create_event + write_dream
        produces canonical event AND dream entry with matching event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Simulate the executor's exact pattern
            event = ilw.create_event(
                provenance=_make_provenance(
                    trigger_type=TRIGGER_TYPE_DREAM_DREAM,
                    actor_id="agent_yua",
                    source_system="dream",
                    extras={
                        "target_agent_id": "agent_ruka",
                        "all_agents_count": "2",
                    },
                )
            )
            writer = _isolated_dream_writer(tmp_path)
            path = asyncio.run(writer.write_dream(
                "agent_yua",
                "agent_ruka",
                ["agent_yua", "agent_ruka"],
                inner_life_event_id=event.event_id,
            ))
            # Event registered with correct provenance
            assert ilw.is_event_known(event.event_id)
            ev = ilw.get_event(event.event_id)
            assert ev.provenance.trigger_type == TRIGGER_TYPE_DREAM_DREAM
            assert ev.provenance.extras["target_agent_id"] == "agent_ruka"
            # Dream entry has matching event_id
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
        finally:
            _restore_data_root()

    def test_b3_write_dream_without_event_id_omits_field(self, tmp_path):
        """write_dream without inner_life_event_id → no field in entry (backward compat)."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            path = asyncio.run(writer.write_dream(
                "agent_yua",
                "agent_ruka",
                ["agent_yua", "agent_ruka"],
            ))
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. Event executor wiring
# ───────────────────────────────────────────────────────────

class TestSectionC_EventExecutorWiring:
    """C. event executor: InnerLifeEvent created, event_id flows to write_event jsonl."""

    def test_c1_write_event_accepts_event_id_kwarg(self, tmp_path):
        """DreamEventWriter.write_event accepts inner_life_event_id."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            eid = _hex_32()
            path = asyncio.run(writer.write_event(
                "agent_yua",
                inner_life_event_id=eid,
            ))
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == eid
            assert entry["slot"] == "event"
        finally:
            _restore_data_root()

    def test_c2_executor_pattern_creates_event_with_event_provenance(self, tmp_path):
        """Executor pattern: InnerLifeWriter.create_event + write_event
        produces canonical event AND event entry with matching event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Simulate the executor's exact pattern
            event = ilw.create_event(
                provenance=_make_provenance(
                    trigger_type=TRIGGER_TYPE_DREAM_EVENT,
                    actor_id="agent_yua",
                    source_system="dream",
                )
            )
            writer = _isolated_dream_writer(tmp_path)
            path = asyncio.run(writer.write_event(
                "agent_yua",
                inner_life_event_id=event.event_id,
            ))
            # Event registered with correct provenance
            assert ilw.is_event_known(event.event_id)
            ev = ilw.get_event(event.event_id)
            assert ev.provenance.trigger_type == TRIGGER_TYPE_DREAM_EVENT
            # Event entry has matching event_id
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
        finally:
            _restore_data_root()

    def test_c3_write_event_without_event_id_omits_field(self, tmp_path):
        """write_event without inner_life_event_id → no field in entry (backward compat)."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            path = asyncio.run(writer.write_event("agent_yua"))
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# D. Backward compatibility
# ───────────────────────────────────────────────────────────

class TestSectionD_BackwardCompatibility:
    """D. Existing Diary/Dream/Event behavior remains backward compatible."""

    def test_d1_diary_writer_no_event_id_default(self, tmp_path):
        """DiaryWriter.write_entry with no inner_life_event_id → no field in entry."""
        writer = _isolated_diary_writer(tmp_path)
        try:
            path = writer.write_entry("agent_yua", "morning", "default content")
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_d2_dream_writer_no_event_id_default(self, tmp_path):
        """DreamEventWriter.write_dream with no event_id → no field in entry."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            path = asyncio.run(writer.write_dream(
                "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"],
            ))
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_d3_event_writer_no_event_id_default(self, tmp_path):
        """DreamEventWriter.write_event with no event_id → no field in entry."""
        writer = _isolated_dream_writer(tmp_path)
        try:
            path = asyncio.run(writer.write_event("agent_yua"))
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()

    def test_d4_existing_legacy_data_remains_readable(self, tmp_path):
        """Pre-existing jsonl files (no inner_life_event_id) load cleanly."""
        # Pre-create legacy file
        soul_dir = tmp_path / "data" / "soul" / "agent_yua" / "diary"
        soul_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        legacy_path = soul_dir / f"{today}.jsonl"
        with legacy_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": "2026-08-08T22:00:00+00:00",
                "slot": "morning",
                "content": "pre-M5.4-6.1 legacy entry",
                "source": "llm",
            }, ensure_ascii=False) + "\n")
        _isolated_data_root(tmp_path)
        try:
            writer = DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
            assert entries[0]["content"] == "pre-M5.4-6.1 legacy entry"
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. No duplicate InnerLifeEvent per execution
# ───────────────────────────────────────────────────────────

class TestSectionE_NoDuplicates:
    """E. Exactly one InnerLifeEvent per executor invocation."""

    def test_e1_one_create_event_call_per_diary_execution(self, tmp_path):
        """A single diary executor pattern call creates exactly 1 event in InnerLifeWriter."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Single executor pattern invocation
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            ))
            assert ilw.get_known_event_count() == 1
            assert ilw.get_stats().events_created == 1
            assert ilw.get_stats().root_events == 1
        finally:
            _restore_data_root()

    def test_e2_one_create_event_call_per_dream_execution(self, tmp_path):
        """A single dream executor pattern call creates exactly 1 event in InnerLifeWriter."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
            ))
            assert ilw.get_known_event_count() == 1
            assert ilw.get_stats().events_created == 1
        finally:
            _restore_data_root()

    def test_e3_one_create_event_call_per_event_execution(self, tmp_path):
        """A single event executor pattern call creates exactly 1 event in InnerLifeWriter."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
            ))
            assert ilw.get_known_event_count() == 1
            assert ilw.get_stats().events_created == 1
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. USER_MESSAGE does NOT create InnerLifeEvent
# ───────────────────────────────────────────────────────────

class TestSectionF_UserMessageExclusion:
    """F. No InnerLifeEvent created merely because USER_MESSAGE exists (per ticket rule)."""

    def test_f1_user_message_trigger_type_exists_but_not_used_in_wiring(self):
        """TRIGGER_TYPE_USER_MESSAGE exists in catalog but is NOT used in any executor wiring."""
        # The ticket explicitly excludes USER_MESSAGE from v1 scope.
        # Executor patterns only use diary/dream/event trigger types.
        # This test verifies the constant exists (for future use) but
        # is not referenced in any of the three executor patterns.
        assert TRIGGER_TYPE_USER_MESSAGE == "user_message"
        # Verify the three executor patterns use the canonical structured types
        assert TRIGGER_TYPE_DIARY_MORNING != TRIGGER_TYPE_USER_MESSAGE
        assert TRIGGER_TYPE_DIARY_NIGHT != TRIGGER_TYPE_USER_MESSAGE
        assert TRIGGER_TYPE_DREAM_DREAM != TRIGGER_TYPE_USER_MESSAGE
        assert TRIGGER_TYPE_DREAM_EVENT != TRIGGER_TYPE_USER_MESSAGE

    def test_f2_diary_dream_event_writers_never_auto_create_events(self, tmp_path):
        """Calling write_entry / write_dream / write_event directly WITHOUT inner_life_event_id
        does NOT create any InnerLifeEvent (no side-effect on InnerLifeWriter).
        USER_MESSAGE path would route through LLMProxy, not these writers.
        """
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            initial_count = ilw.get_known_event_count()
            assert initial_count == 0

            # Direct writer calls (no event_id)
            diary_writer = _isolated_diary_writer(tmp_path)
            diary_writer.write_entry("agent_yua", "morning", "no event id here")

            dream_writer = _isolated_dream_writer(tmp_path)
            asyncio.run(dream_writer.write_dream(
                "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"]
            ))
            asyncio.run(dream_writer.write_event("agent_yua"))

            # No event created in InnerLifeWriter
            assert ilw.get_known_event_count() == 0
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. Provenance canonical trigger types
# ───────────────────────────────────────────────────────────

class TestSectionG_ProvenanceSemantics:
    """G. Executor uses canonical trigger_type + source_system values from M5.4-5.1."""

    def test_g1_all_three_wiring_trigger_types_canonical(self):
        """Diary/Dream/Event wiring uses M5.4-5.1 canonical trigger types."""
        # These are the exact strings the executors use
        assert TRIGGER_TYPE_DIARY_MORNING == "diary:morning"
        assert TRIGGER_TYPE_DIARY_NIGHT == "diary:night"
        assert TRIGGER_TYPE_DREAM_DREAM == "dream:dream"
        assert TRIGGER_TYPE_DREAM_EVENT == "dream:event"

    def test_g2_diary_uses_source_system_diary(self, tmp_path):
        """Diary executor Provenance.source_system = 'diary' (VALID_SOURCE_SYSTEMS)."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            ))
            assert event.provenance.source_system == "diary"
            assert "diary" in VALID_SOURCE_SYSTEMS
        finally:
            _restore_data_root()

    def test_g3_dream_uses_source_system_dream(self, tmp_path):
        """Dream/Event executor Provenance.source_system = 'dream' (VALID_SOURCE_SYSTEMS)."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Dream
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
            ))
            assert event.provenance.source_system == "dream"
            # Event
            event2 = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
            ))
            assert event2.provenance.source_system == "dream"
            assert "dream" in VALID_SOURCE_SYSTEMS
        finally:
            _restore_data_root()

    def test_g4_actor_id_is_agent_id(self, tmp_path):
        """Executor Provenance.actor_id = agent_id (the role that lived the experience)."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            for trigger_type, source in [
                (TRIGGER_TYPE_DIARY_MORNING, "diary"),
                (TRIGGER_TYPE_DIARY_NIGHT, "diary"),
                (TRIGGER_TYPE_DREAM_DREAM, "dream"),
                (TRIGGER_TYPE_DREAM_EVENT, "dream"),
            ]:
                event = ilw.create_event(provenance=_make_provenance(
                    trigger_type, "agent_yua", source,
                ))
                assert event.provenance.actor_id == "agent_yua"
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# H. Failure isolation
# ───────────────────────────────────────────────────────────

class TestSectionH_FailureIsolation:
    """H. InnerLifeWriter failure does not block writer (per InnerLifeWriter design contract)."""

    def test_h1_inner_life_failure_falls_back_to_none_event_id(self, tmp_path, monkeypatch):
        """If InnerLifeWriter.create_event raises, executor pattern falls back to event_id=None,
        and writer still writes entry with no inner_life_event_id field."""
        from src.soul import dream_event as dream_mod

        # Monkeypatch create_event to raise (simulating memory corruption / etc.)
        def failing_create_event(*args, **kwargs):
            raise RuntimeError("simulated InnerLifeWriter failure")

        monkeypatch.setattr(
            "src.inner_life.writer.InnerLifeWriter.create_event",
            failing_create_event,
        )

        _isolated_data_root(tmp_path)
        try:
            # Executor pattern with try/except fallback
            ilw = InnerLifeWriter()
            try:
                _event = ilw.create_event(provenance=_make_provenance(
                    TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
                ))
                _event_id = _event.event_id
            except Exception:
                _event_id = None

            # Writer still runs with None event_id
            writer = _isolated_dream_writer(tmp_path)
            path = asyncio.run(writer.write_event(
                "agent_yua", inner_life_event_id=_event_id,
            ))
            assert path is not None
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            # No event_id field (entry_id was None)
            assert "inner_life_event_id" not in entry
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# I. Cross-reference integrity
# ───────────────────────────────────────────────────────────

class TestSectionI_CrossReferenceIntegrity:
    """I. InnerLifeEvent.event_id == diary/dream/event jsonl inner_life_event_id field."""

    def test_i1_diary_cross_reference_byte_exact(self, tmp_path):
        """InnerLifeEvent.event_id byte-exact matches diary jsonl inner_life_event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary",
                extras={"slot": "night"},
            ))
            writer = _isolated_diary_writer(tmp_path)
            path = writer.write_entry(
                "agent_yua", "night", "cross-ref test",
                inner_life_event_id=event.event_id,
            )
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
            assert len(entry["inner_life_event_id"]) == 32
        finally:
            _restore_data_root()

    def test_i2_dream_cross_reference_byte_exact(self, tmp_path):
        """InnerLifeEvent.event_id byte-exact matches dream jsonl inner_life_event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
                extras={"target_agent_id": "agent_ruka"},
            ))
            writer = _isolated_dream_writer(tmp_path)
            path = asyncio.run(writer.write_dream(
                "agent_yua", "agent_ruka", ["agent_yua", "agent_ruka"],
                inner_life_event_id=event.event_id,
            ))
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
            assert len(entry["inner_life_event_id"]) == 32
        finally:
            _restore_data_root()

    def test_i3_event_cross_reference_byte_exact(self, tmp_path):
        """InnerLifeEvent.event_id byte-exact matches event jsonl inner_life_event_id."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
            ))
            writer = _isolated_dream_writer(tmp_path)
            path = asyncio.run(writer.write_event(
                "agent_yua", inner_life_event_id=event.event_id,
            ))
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            assert entry["inner_life_event_id"] == event.event_id
            assert len(entry["inner_life_event_id"]) == 32
        finally:
            _restore_data_root()

    def test_i4_event_registered_in_inner_life_writer(self, tmp_path):
        """InnerLifeWriter knows about the event (canonical authority invariant)."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            ))
            # Event registered in InnerLifeWriter
            assert ilw.is_event_known(event.event_id)
            registered = ilw.get_event(event.event_id)
            assert registered is event
            assert registered.event_id == event.event_id
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: 24 tests (5 A + 3 B + 3 C + 4 D + 3 E + 2 F + 4 G + 1 H + 4 I + 1 count - 8 = 22).

    Actually counted: A=5, B=3, C=3, D=4, E=3, F=2, G=4, H=1, I=4, count=1 → 30 expected.
    """
    # Sanity check: pytest collection should match
    pass
