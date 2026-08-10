"""
tests/test_m5_4_5_6_narrative_trace_sidecar.py

M5.4-5.6 (Bry 派工 2026-08-09 22:30): Inner Life Narrative Trace Sidecar.

Focused test suite covering:
- A. Trace path (2)
- B. Event creation (3)
- C. Identity consistency (3)
- D. Optional identity fields (3)
- E. Serialization (2)
- F. Multiple events (3)
- G. Failure isolation (3)
- H. Backward compatibility (2)
- count (1)

Test count: 22 tests
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    NarrativeTraceWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_USER_MESSAGE,
)
from src.paths import data_root, reset_data_root


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _make_provenance(
    trigger_type: str = TRIGGER_TYPE_USER_MESSAGE,
    actor_id: str = "bryan",
    source_system: str = "narrative",
) -> Provenance:
    return Provenance(
        trigger_type=trigger_type,
        actor_id=actor_id,
        source_system=source_system,
    )


# ───────────────────────────────────────────────────────────
# A. Trace path
# ───────────────────────────────────────────────────────────

class TestSectionA_TracePath:
    """A. trace.jsonl path resolution + lazy creation."""

    def test_a1_explicit_path_creates_file_lazily(self, tmp_path: Path):
        """Passing explicit trace_log_path → file is created lazily on first write."""
        trace_path = tmp_path / "trace.jsonl"
        assert not trace_path.exists()
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        # __init__ mkdirs parent but does NOT create the file
        assert not trace_path.exists()
        assert trace_path.parent.exists()
        # First write creates the file
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance())
        assert trace_path.exists()
        assert trace_path.stat().st_size > 0

    def test_a2_default_path_uses_data_root(self, tmp_path: Path, monkeypatch):
        """Default constructor uses data_root() / "inner_life" / "trace.jsonl"."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()
        try:
            writer = NarrativeTraceWriter()
            expected = data_root() / "inner_life" / "trace.jsonl"
            assert writer.trace_log_path == expected
            # inner_life/ directory is created lazily
            assert writer.trace_log_path.parent.exists()
        finally:
            reset_data_root()


# ───────────────────────────────────────────────────────────
# B. Event creation
# ───────────────────────────────────────────────────────────

class TestSectionB_EventCreation:
    """B. create_event() produces exactly one trace record per event."""

    def test_b1_create_event_produces_one_trace_record(self, tmp_path: Path):
        """One create_event() call → exactly one line in trace.jsonl."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance())
        records = writer.read_all()
        assert len(records) == 1

    def test_b2_trace_event_id_matches_canonical_event(self, tmp_path: Path):
        """Trace record event_id == InnerLifeEvent.event_id."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance())
        records = writer.read_all()
        assert records[0]["event_id"] == ev.event_id
        assert len(ev.event_id) == 32  # canonical 32-char hex

    def test_b3_trace_contains_lineage_fields(self, tmp_path: Path):
        """Trace record contains all required lineage fields."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(
            provenance=_make_provenance(),
            session_id="sess-1",
            correlation_id="corr-1",
        )
        records = writer.read_all()
        rec = records[0]
        # Identity fields
        assert rec["event_id"] == ev.event_id
        assert rec["session_id"] == "sess-1"
        assert rec["correlation_id"] == "corr-1"
        assert rec["parent_event_id"] is None
        assert rec["ts"] == ev.ts
        # Lineage fields
        assert rec["lineage_depth"] == 0  # root
        assert rec["lineage_path"] == ev.event_id


# ───────────────────────────────────────────────────────────
# C. Identity consistency
# ───────────────────────────────────────────────────────────

class TestSectionC_IdentityConsistency:
    """C. Trace record event_id/parent/lineage match canonical event exactly."""

    def test_c1_trace_event_id_exact_match(self, tmp_path: Path):
        """Trace event_id exactly matches canonical event.event_id (no transform)."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance())
        rec = writer.read_all()[0]
        # Byte-exact match
        assert rec["event_id"] == ev.event_id
        assert len(rec["event_id"]) == 32
        assert all(c in "0123456789abcdef" for c in rec["event_id"])

    def test_c2_parent_event_id_preserved(self, tmp_path: Path):
        """Parent → child event: child's parent_event_id matches parent's event_id."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        parent = ilw.create_event(provenance=_make_provenance())
        child = ilw.create_event(
            provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY),
            parent_event_id=parent.event_id,
        )
        records = writer.read_all()
        assert len(records) == 2
        assert records[0]["event_id"] == parent.event_id
        assert records[1]["event_id"] == child.event_id
        assert records[1]["parent_event_id"] == parent.event_id

    def test_c3_lineage_depth_and_path_preserved(self, tmp_path: Path):
        """Lineage depth + path preserved through trace records."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        root = ilw.create_event(provenance=_make_provenance())
        child = ilw.create_event(
            provenance=_make_provenance(),
            parent_event_id=root.event_id,
        )
        grandchild = ilw.create_event(
            provenance=_make_provenance(),
            parent_event_id=child.event_id,
        )
        records = writer.read_all()
        # root: depth 0, path = own_id
        assert records[0]["lineage_depth"] == 0
        assert records[0]["lineage_path"] == root.event_id
        # child: depth 1, path = root_id/own_id
        assert records[1]["lineage_depth"] == 1
        assert records[1]["lineage_path"] == f"{root.event_id}/{child.event_id}"
        # grandchild: depth 2, path = root_id/child_id/own_id
        assert records[2]["lineage_depth"] == 2
        assert records[2]["lineage_path"] == (
            f"{root.event_id}/{child.event_id}/{grandchild.event_id}"
        )


# ───────────────────────────────────────────────────────────
# D. Optional identity fields
# ───────────────────────────────────────────────────────────

class TestSectionD_OptionalFields:
    """D. session_id, correlation_id, parent_event_id can all be None."""

    def test_d1_session_id_none(self, tmp_path: Path):
        """session_id None → trace record has session_id=None."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance(), session_id=None)
        rec = writer.read_all()[0]
        assert rec["session_id"] is None
        assert ev.is_session_anchored() is False

    def test_d2_correlation_id_none(self, tmp_path: Path):
        """correlation_id None → trace record has correlation_id=None."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance(), correlation_id=None)
        rec = writer.read_all()[0]
        assert rec["correlation_id"] is None
        assert ev.is_in_narrative() is False

    def test_d3_parent_event_id_none(self, tmp_path: Path):
        """parent_event_id None → trace record has parent_event_id=None, depth=0, path=own_id."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance(), parent_event_id=None)
        rec = writer.read_all()[0]
        assert rec["parent_event_id"] is None
        assert rec["lineage_depth"] == 0
        assert rec["lineage_path"] == ev.event_id
        assert ev.is_root() is True


# ───────────────────────────────────────────────────────────
# E. Serialization
# ───────────────────────────────────────────────────────────

class TestSectionE_Serialization:
    """E. Provenance survives trace serialization; trace JSON is valid."""

    def test_e1_provenance_survives_serialization(self, tmp_path: Path):
        """Provenance structured fields preserved through trace.jsonl round-trip."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        prov = Provenance(
            trigger_type=TRIGGER_TYPE_DIARY_NIGHT,
            actor_id="agent_rem",
            source_system="diary",
            trace_ref="ref-abc",
            extras={"mood": "tired", "weather": "rainy"},
        )
        ev = ilw.create_event(provenance=prov)
        rec = writer.read_all()[0]
        assert rec["provenance"]["trigger_type"] == TRIGGER_TYPE_DIARY_NIGHT
        assert rec["provenance"]["actor_id"] == "agent_rem"
        assert rec["provenance"]["source_system"] == "diary"
        assert rec["provenance"]["trace_ref"] == "ref-abc"
        assert rec["provenance"]["extras"]["mood"] == "tired"
        assert rec["provenance"]["extras"]["weather"] == "rainy"

    def test_e2_trace_json_is_valid(self, tmp_path: Path):
        """Each line in trace.jsonl is a valid JSON object."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ilw.create_event(provenance=_make_provenance())
        ilw.create_event(
            provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY),
            session_id="s1",
        )
        # Read raw file and parse each line
        with open(trace_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)  # must not raise
            assert "event_id" in obj
            assert "ts" in obj
            assert "provenance" in obj


# ───────────────────────────────────────────────────────────
# F. Multiple events
# ───────────────────────────────────────────────────────────

class TestSectionF_MultipleEvents:
    """F. Multiple events append in order; no overwrite; lineage preserved."""

    def test_f1_multiple_events_append_in_order(self, tmp_path: Path):
        """3 events created in order → 3 records in same order."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        evs = [ilw.create_event(provenance=_make_provenance()) for _ in range(3)]
        records = writer.read_all()
        assert len(records) == 3
        for ev, rec in zip(evs, records):
            assert rec["event_id"] == ev.event_id

    def test_f2_no_overwrite_on_repeated_writes(self, tmp_path: Path):
        """Repeated write() calls append, never overwrite."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        for i in range(5):
            ilw.create_event(provenance=_make_provenance(actor_id=f"actor-{i}"))
        records = writer.read_all()
        assert len(records) == 5
        # All 5 actor_ids distinct
        actor_ids = {r["provenance"]["actor_id"] for r in records}
        assert actor_ids == {f"actor-{i}" for i in range(5)}

    def test_f3_parent_child_lineage_chain_preserved(self, tmp_path: Path):
        """3-level parent/child/grandchild chain: each level's lineage preserved."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        a = ilw.create_event(provenance=_make_provenance())
        b = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=a.event_id
        )
        c = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=b.event_id
        )
        records = writer.read_all()
        # Each record's parent_event_id matches previous record's event_id
        assert records[0]["event_id"] == a.event_id
        assert records[1]["parent_event_id"] == a.event_id
        assert records[1]["event_id"] == b.event_id
        assert records[2]["parent_event_id"] == b.event_id
        assert records[2]["event_id"] == c.event_id
        # Depth chain: 0 → 1 → 2
        assert [r["lineage_depth"] for r in records] == [0, 1, 2]


# ───────────────────────────────────────────────────────────
# G. Failure isolation
# ───────────────────────────────────────────────────────────

class TestSectionG_FailureIsolation:
    """G. Trace failure does NOT invalidate canonical event creation."""

    def test_g1_trace_writer_exception_does_not_invalidate_event(self, tmp_path: Path):
        """trace_writer.write() raises → create_event() still returns valid event."""
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = RuntimeError("simulated disk full")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        ev = ilw.create_event(provenance=_make_provenance())
        # Event is valid
        assert ev.event_id is not None
        assert len(ev.event_id) == 32
        # Event is registered in writer
        assert ilw.is_event_known(ev.event_id)
        assert ilw.get_event(ev.event_id) is ev

    def test_g2_create_event_still_succeeds_after_trace_failure(self, tmp_path: Path):
        """Multiple create_event() calls succeed even if trace_writer always fails."""
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = OSError("simulated permission denied")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        evs = [
            ilw.create_event(provenance=_make_provenance())
            for _ in range(3)
        ]
        assert len(evs) == 3
        assert ilw.get_known_event_count() == 3
        # All 3 events are valid
        for ev in evs:
            assert ilw.is_event_known(ev.event_id)

    def test_g3_trace_failure_observable_via_logger(self, tmp_path: Path, caplog):
        """Trace failure is logged via logger.warning (NOT raised)."""
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = ValueError("simulated bad input")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        with caplog.at_level("WARNING", logger="soul_os.inner_life.writer"):
            ev = ilw.create_event(provenance=_make_provenance())
        # Event still created
        assert ev.event_id is not None
        # Warning was logged
        assert any(
            "trace append failed" in record.message
            for record in caplog.records
        ), f"Expected warning not found. Got: {[r.message for r in caplog.records]}"


# ───────────────────────────────────────────────────────────
# H. Backward compatibility
# ───────────────────────────────────────────────────────────

class TestSectionH_BackwardCompat:
    """H. InnerLifeWriter() without trace_writer works exactly as before."""

    def test_h1_inner_life_writer_no_trace_default_behavior(self, tmp_path: Path, monkeypatch):
        """InnerLifeWriter() with default trace_writer=None → no trace file, behavior unchanged."""
        # Isolate data_root to confirm NO trace file is created in default mode
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()
        try:
            ilw = InnerLifeWriter()  # default: trace_writer=None
            assert ilw._trace_writer is None
            ev = ilw.create_event(provenance=_make_provenance())
            # Event valid + registered
            assert ev.event_id is not None
            assert ilw.is_event_known(ev.event_id)
            # NO trace file created
            inner_life_dir = tmp_path / "inner_life"
            assert not inner_life_dir.exists() or not any(inner_life_dir.iterdir())
        finally:
            reset_data_root()

    def test_h2_m5_4_5_1_foundation_invariants_preserved(self):
        """M5.4-5.1 foundation invariants still hold (stats, indexes, lineage)."""
        ilw = InnerLifeWriter()
        a = ilw.create_event(provenance=_make_provenance())
        b = ilw.create_event(
            provenance=_make_provenance(),
            parent_event_id=a.event_id,
        )
        # Stats invariants from M5.4-5.1
        stats = ilw.get_stats()
        assert stats.events_created == 2
        assert stats.root_events == 1
        assert stats.child_events == 1
        # Index invariants from M5.4-5.1
        assert ilw.get_children(a.event_id) == [b.event_id]
        assert ilw.get_event(b.event_id) is b
        # Lineage invariants from M5.4-5.1
        assert b.lineage_depth == 1
        assert b.lineage_path == f"{a.event_id}/{b.event_id}"


# ───────────────────────────────────────────────────────────
# Test count guard
# ───────────────────────────────────────────────────────────

def test_count_22_tests():
    """Guard: 22 focused tests across 8 sections + count."""
    import inspect
    import sys
    current_module = sys.modules[__name__]
    test_funcs = [
        name
        for name, obj in inspect.getmembers(current_module, inspect.isclass)
        for name, method in inspect.getmembers(obj, inspect.isfunction)
        if name.startswith("test_")
    ]
    # Plus the module-level count test
    expected = 22
    actual = len(test_funcs) + 1  # +1 for test_count itself
    assert actual == expected, f"Expected {expected} tests, found {actual}"
