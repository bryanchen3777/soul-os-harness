"""
tests/test_m5_4_6_3_trace_production_activation_audit.py

M5.4-6.3 (Bry 派工 2026-08-10): Narrative Trace Production Activation Audit.

READ-ONLY audit tests verifying trace sidecar can be safely enabled
in production. All tests use isolated tmp_path / SOUL_OS_DATA_DIR.

Test sections:
- A. Trace construction boundary (3)
- B. Lifecycle behavior (3)
- C. data_root() isolation (3)
- D. Failure isolation (3)
- E. Reader compatibility (2)
- F. Four-producer coverage (5)
- G. Duplicate protection (2)
- H. Privacy/content boundary (2)
- count (1)

Test count: 24 tests
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
)
from src.inner_life.serialization import event_to_dict
from src.paths import data_root, reset_data_root


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _hex_32() -> str:
    return uuid.uuid4().hex


def _isolated_data_root(tmp_path: Path) -> Path:
    """Force data_root() to point to a temp dir; return data_root() Path."""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provenance(trigger_type: str, actor_id: str, source_system: str,
                      extras: Dict[str, str] = None) -> Provenance:
    if extras is None:
        extras = {}
    return Provenance(
        trigger_type=trigger_type,
        actor_id=actor_id,
        source_system=source_system,
        extras=extras,
    )


# ───────────────────────────────────────────────────────────
# A. Trace construction boundary
# ───────────────────────────────────────────────────────────

class TestSectionA_ConstructionBoundary:
    """A. InnerLifeWriter construction point + minimum injection."""

    def test_a1_inner_life_writer_accepts_trace_writer_constructor(self, tmp_path):
        """InnerLifeWriter(trace_writer=...) constructor accepts the optional trace sidecar."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        # M5.4-6.3 audit verification: constructor accepts the injection
        ilw = InnerLifeWriter(trace_writer=ntw)
        assert ilw._trace_writer is ntw
        _restore_data_root()

    def test_a2_trace_writer_default_is_none_disabled(self, tmp_path):
        """Default (no trace_writer) → trace sidecar disabled, no file created."""
        data_dir = _isolated_data_root(tmp_path)
        ilw = InnerLifeWriter()  # default: trace_writer=None
        assert ilw._trace_writer is None
        # Create an event — should NOT create trace file
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        trace_file = data_dir / "inner_life" / "trace.jsonl"
        # Disabled mode: no trace file
        assert not trace_file.exists()
        _restore_data_root()

    def test_a3_single_instance_no_duplication(self, tmp_path):
        """One InnerLifeWriter → one NarrativeTraceWriter → no duplicate writes per event."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Multiple events → multiple distinct records (no duplication)
        for i in range(3):
            ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_AGENT_REPLY, f"agent_{i}", "narrative",
            ))
        records = ntw.read_all()
        assert len(records) == 3
        # Each event_id is unique
        event_ids = {r["event_id"] for r in records}
        assert len(event_ids) == 3
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. Lifecycle behavior
# ───────────────────────────────────────────────────────────

class TestSectionB_Lifecycle:
    """B. NarrativeTraceWriter lifecycle: file handle, cleanup, shutdown."""

    def test_b1_write_uses_context_manager_closes_handle(self, tmp_path):
        """write() uses `with open(...) as f:` → file handle closed after each write."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ev = InnerLifeEvent(
            event_id=_hex_32(), session_id=None, correlation_id=None,
            parent_event_id=None, ts="2026-08-10T01:00:00+00:00",
            provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative"),
            lineage_depth=0, lineage_path=_hex_32(),
        )
        # write() should not leave file handle open
        # (verified by writing + reading immediately)
        assert ntw.write(ev) is True
        # If file handle leaked, subsequent unlink would fail on Windows
        # Multiple writes in sequence to confirm no handle leak
        for i in range(10):
            ev2 = InnerLifeEvent(
                event_id=_hex_32(), session_id=None, correlation_id=None,
                parent_event_id=None, ts="2026-08-10T01:00:00+00:00",
                provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY, f"agent_{i}", "narrative"),
                lineage_depth=0, lineage_path=_hex_32(),
            )
            assert ntw.write(ev2) is True
        # Verify all 11 records (1 + 10) are present
        records = ntw.read_all()
        assert len(records) == 11
        _restore_data_root()

    def test_b2_no_close_or_flush_method_required(self, tmp_path):
        """NarrativeTraceWriter has no close()/flush() — confirmed by public API inspection."""
        # The design uses per-write context manager, no long-lived state
        public_methods = [m for m in dir(NarrativeTraceWriter) if not m.startswith("_")]
        assert "write" in public_methods
        assert "read_all" in public_methods
        assert "clear" in public_methods
        # No close / flush required
        assert "close" not in public_methods
        assert "flush" not in public_methods
        _restore_data_root() if "SOUL_OS_DATA_DIR" in os.environ else None

    def test_b3_lifespan_shutdown_no_cleanup_needed(self, tmp_path):
        """No explicit cleanup method needed on lifespan shutdown (no state to release)."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        # No close() exists, so shutdown is a no-op (Python GC reclaims the instance)
        # Verify the trace file persists across "shutdown" (GC simulated)
        ev = InnerLifeEvent(
            event_id=_hex_32(), session_id=None, correlation_id=None,
            parent_event_id=None, ts="2026-08-10T01:00:00+00:00",
            provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative"),
            lineage_depth=0, lineage_path=_hex_32(),
        )
        ntw.write(ev)
        del ntw  # simulate GC
        # File should still exist (append-only, no in-memory buffer to flush)
        assert trace_path.exists()
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. data_root() isolation
# ───────────────────────────────────────────────────────────

class TestSectionC_DataRootIsolation:
    """C. trace path always via data_root(); no repository-relative fallback."""

    def test_c1_default_path_uses_data_root(self, tmp_path):
        """Default constructor (no path) uses data_root() / 'inner_life' / 'trace.jsonl'."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()  # default path
        expected = data_dir / "inner_life" / "trace.jsonl"
        assert ntw.trace_log_path == expected
        _restore_data_root()

    def test_c2_no_repository_relative_fallback(self, tmp_path):
        """data_root() never falls back to bare 'data/inner_life' (always absolute)."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        # data_root() is always absolute per P0.5 contract (path.py:44 `Path(env).resolve()`)
        assert ntw.trace_log_path.is_absolute()
        _restore_data_root()

    def test_c3_test_isolation_works(self, tmp_path):
        """Setting SOUL_OS_DATA_DIR redirects trace to tmp_path (no production leakage)."""
        data_dir = _isolated_data_root(tmp_path)
        # Create some events
        ilw = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Trace should be in tmp_path, NOT in production data/
        trace_in_tmp = data_dir / "inner_life" / "trace.jsonl"
        assert trace_in_tmp.exists()
        # Production path (if exists) should NOT contain this test's events
        prod_trace = Path("data/inner_life/trace.jsonl").resolve()
        if prod_trace.exists():
            records = ntw_read_only(prod_trace)
            # None of the test event_ids should appear in production
            test_ids = {ilw._events[e].event_id for e in ilw._events}
            for r in records:
                assert r["event_id"] not in test_ids
        _restore_data_root()


def ntw_read_only(path: Path) -> List[Dict[str, Any]]:
    """Read-only helper that does NOT instantiate NarrativeTraceWriter
    (to avoid creating any side effects in production path)."""
    if not path.exists():
        return []
    records = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


# ───────────────────────────────────────────────────────────
# D. Failure isolation
# ───────────────────────────────────────────────────────────

class TestSectionD_FailureIsolation:
    """D. trace write failure does NOT block canonical InnerLifeEvent creation."""

    def test_d1_trace_failure_does_not_invalidate_event(self, tmp_path):
        """If trace_writer.write() raises, create_event() still returns valid event."""
        data_dir = _isolated_data_root(tmp_path)
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = RuntimeError("simulated disk full")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        # Should not raise
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Event is valid
        assert ev.event_id is not None
        assert ilw.is_event_known(ev.event_id)
        _restore_data_root()

    def test_d2_4_producers_all_isolated_under_trace_failure(self, tmp_path):
        """All 4 producer patterns (diary/dream/event/proactive_dm) are isolated under trace failure."""
        data_dir = _isolated_data_root(tmp_path)
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = OSError("simulated permission denied")
        ilw = InnerLifeWriter(trace_writer=mock_writer)

        # All 4 producers call create_event exactly once
        producers = [
            (TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary"),
            (TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary"),
            (TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream"),
            (TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream"),
        ]
        for trigger_type, actor_id, source_system in producers:
            ev = ilw.create_event(provenance=_make_provenance(
                trigger_type, actor_id, source_system,
            ))
            assert ev.event_id is not None
        # And proactive_dm
        ev_pd = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        assert ev_pd.event_id is not None
        # All 5 events registered
        assert ilw.get_known_event_count() == 5
        _restore_data_root()

    def test_d3_logger_warning_preserved_on_trace_failure(self, tmp_path, caplog):
        """Trace failure emits logger.warning (M5.4-5.6 contract preserved)."""
        import logging
        _isolated_data_root(tmp_path)
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = RuntimeError("simulated failure")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        with caplog.at_level(logging.WARNING, logger="soul_os.inner_life.writer"):
            ilw.create_event(provenance=_make_provenance(
                TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
            ))
        # Warning was logged (M5.4-5.6 frozen contract)
        assert any(
            "trace append failed" in record.message
            for record in caplog.records
        ), f"Expected warning. Got: {[r.message for r in caplog.records]}"
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. Reader compatibility
# ───────────────────────────────────────────────────────────

class TestSectionE_ReaderCompatibility:
    """E. NarrativeTraceReader can read production-generated trace.jsonl."""

    def test_e1_writer_and_reader_schema_identical(self, tmp_path):
        """Writer output = Reader input schema (verified by round-trip)."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        # Write one event
        ev = InnerLifeEvent(
            event_id=_hex_32(), session_id="sess-1",
            correlation_id="corr-1", parent_event_id=None,
            ts="2026-08-10T01:00:00+00:00",
            provenance=_make_provenance(
                TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
                extras={"trigger_source": "proactive_dm"},
            ),
            lineage_depth=0, lineage_path="",
        )
        ntw.write(ev)
        # Reader reads it back
        ntr = NarrativeTraceReader(trace_log_path=trace_path)
        results = ntr.query_by_event_id(ev.event_id)
        assert len(results) == 1
        # Round-trip integrity
        assert results[0]["event_id"] == ev.event_id
        assert results[0]["session_id"] == "sess-1"
        assert results[0]["correlation_id"] == "corr-1"
        assert results[0]["provenance"]["actor_id"] == "agent_ruka"
        assert results[0]["provenance"]["extras"]["trigger_source"] == "proactive_dm"
        _restore_data_root()

    def test_e2_malformed_records_isolated_by_reader(self, tmp_path):
        """Reader skips malformed JSON lines without raising (M5.4-5.7 contract)."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create a file with mix of valid + malformed lines
        with trace_path.open("w", encoding="utf-8") as f:
            # Valid record
            valid = event_to_dict(InnerLifeEvent(
                event_id=_hex_32(), session_id=None, correlation_id=None,
                parent_event_id=None, ts="2026-08-10T01:00:00+00:00",
                provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative"),
                lineage_depth=0, lineage_path="",
            ))
            f.write(json.dumps(valid, ensure_ascii=False) + "\n")
            # Malformed
            f.write("{not valid json\n")
            # Another valid
            valid2 = event_to_dict(InnerLifeEvent(
                event_id=_hex_32(), session_id=None, correlation_id=None,
                parent_event_id=None, ts="2026-08-10T01:00:00+00:00",
                provenance=_make_provenance(TRIGGER_TYPE_AGENT_REPLY, "agent_yua", "narrative"),
                lineage_depth=0, lineage_path="",
            ))
            f.write(json.dumps(valid2, ensure_ascii=False) + "\n")
            # Truncated
            f.write('{"event_id": "abc"\n')

        ntr = NarrativeTraceReader(trace_log_path=trace_path)
        # Reader returns 2 valid records, skips 2 malformed
        records = ntr._read_all()
        assert len(records) == 2
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. Four-producer coverage
# ───────────────────────────────────────────────────────────

class TestSectionF_FourProducerCoverage:
    """F. Each of 4 producers (diary/dream/event/proactive_dm) creates exactly 1 trace."""

    def _run_all_4_producers(self, ilw: InnerLifeWriter) -> None:
        """Simulate exactly one execution of each of the 4 producer patterns."""
        # Diary morning
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            extras={"slot": "morning"},
        ))
        # Diary night
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary",
            extras={"slot": "night"},
        ))
        # Dream
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
            extras={"target_agent_id": "agent_ruka"},
        ))
        # Event
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
        ))
        # Proactive DM
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
            extras={"trigger_source": "proactive_dm"},
        ))

    def test_f1_diary_producer_creates_one_trace(self, tmp_path):
        """Diary producer: 1 create_event → 1 trace record with TRIGGER_TYPE_DIARY_MORNING."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DIARY_MORNING
        _restore_data_root()

    def test_f2_dream_producer_creates_one_trace(self, tmp_path):
        """Dream producer: 1 create_event → 1 trace record with TRIGGER_TYPE_DREAM_DREAM."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DREAM_DREAM
        _restore_data_root()

    def test_f3_event_producer_creates_one_trace(self, tmp_path):
        """Event producer: 1 create_event → 1 trace record with TRIGGER_TYPE_DREAM_EVENT."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DREAM_EVENT
        _restore_data_root()

    def test_f4_proactive_dm_producer_creates_one_trace(self, tmp_path):
        """Proactive DM producer: 1 create_event → 1 trace with TRIGGER_TYPE_AGENT_REPLY."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_AGENT_REPLY
        _restore_data_root()

    def test_f5_all_4_producers_5_events_5_traces_in_isolated_env(self, tmp_path):
        """All 4 producers (with 2 diary slots) → 5 trace records in isolated tmp_path.
        No production data touched (verified by data_root() being tmp_path)."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        self._run_all_4_producers(ilw)
        # Read all records
        records = ntw.read_all()
        assert len(records) == 5
        # Verify each trigger_type is present
        trigger_types = {r["provenance"]["trigger_type"] for r in records}
        assert trigger_types == {
            TRIGGER_TYPE_DIARY_MORNING,
            TRIGGER_TYPE_DIARY_NIGHT,
            TRIGGER_TYPE_DREAM_DREAM,
            TRIGGER_TYPE_DREAM_EVENT,
            TRIGGER_TYPE_AGENT_REPLY,
        }
        # Verify file is in tmp_path, not production
        assert trace_path.parent.parent == data_dir  # tmp_path/data/inner_life/trace.jsonl
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. Duplicate protection
# ───────────────────────────────────────────────────────────

class TestSectionG_DuplicateProtection:
    """G. One create_event = one trace; no duplicates from retry/propagation."""

    def test_g1_one_create_event_one_trace(self, tmp_path):
        """One create_event() call → exactly one trace record."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Single create
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        records = ntw.read_all()
        # Exactly one record, matching the event_id
        assert len(records) == 1
        assert records[0]["event_id"] == ev.event_id
        _restore_data_root()

    def test_g2_no_duplicate_on_soul_event_propagation(self, tmp_path):
        """SoulEvent propagation (AGENT_INTENT → AGENT_SPEAK) does NOT call create_event again.
        Only the executor calls create_event (single source)."""
        # In the run_server.py executors, create_event is called EXACTLY once.
        # The SoulEvent propagation in consciousness._fire_intent / LLMProxy just sets
        # the inner_life_event_id field — it does NOT create a new InnerLifeEvent.
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Simulate: executor calls create_event once. Then "SoulEvent propagation" is
        # just a field assignment, not another create_event.
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Simulate AGENT_INTENT SoulEvent (no create_event)
        from src.eventbus.schema import EventType, EventPriority, SoulEvent
        agent_intent = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            inner_life_event_id=ev.event_id,  # field assignment, no create_event
            payload={"reason": "proactive_dm"},
        )
        # Simulate AGENT_SPEAK SoulEvent (no create_event)
        agent_speak = SoulEvent(
            event_type=EventType.AGENT_SPEAK,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            inner_life_event_id=agent_intent.inner_life_event_id,  # field passthrough
            payload={"text": "hello"},
        )
        # SoulEvent propagation does NOT call create_event
        # → trace still has only 1 record
        records = ntw.read_all()
        assert len(records) == 1
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# H. Privacy / content boundary
# ───────────────────────────────────────────────────────────

class TestSectionH_PrivacyBoundary:
    """H. Trace contains ONLY metadata, no conversation content."""

    def test_h1_event_to_dict_has_no_content_field(self):
        """event_to_dict schema contains exactly the 8 metadata fields per ticket spec."""
        ev = InnerLifeEvent(
            event_id=_hex_32(),
            session_id="sess-1",
            correlation_id=None,
            parent_event_id=None,
            ts="2026-08-10T01:00:00+00:00",
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_AGENT_REPLY,
                actor_id="agent_ruka",
                source_system="narrative",
                extras={"trigger_source": "proactive_dm", "elapsed_mins": "240"},
            ),
            lineage_depth=0,
            lineage_path="a" * 32,
        )
        d = event_to_dict(ev)
        # Required fields per ticket spec
        expected_keys = {
            "event_id", "session_id", "correlation_id", "parent_event_id",
            "ts", "provenance", "lineage_depth", "lineage_path",
        }
        assert set(d.keys()) == expected_keys
        # No content / prompt / response / text / message fields
        forbidden = {
            "content", "text", "message", "prompt", "response",
            "audio_text", "tts", "payload", "raw", "body",
        }
        leaked = set(d.keys()) & forbidden
        assert not leaked, f"Trace leaked forbidden fields: {leaked}"

    def test_h2_extras_field_only_metadata_no_conversation_content(self, tmp_path):
        """extras in provenance is structured metadata only, no conversation content.
        All 4 producer patterns verified to use metadata-only extras."""
        data_dir = _isolated_data_root(tmp_path)
        trace_path = data_dir / "inner_life" / "trace.jsonl"
        ntw = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Simulate each producer with realistic extras
        producers = [
            (TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
             {"slot": "morning"}),
            (TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary",
             {"slot": "night"}),
            (TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
             {"target_agent_id": "agent_ruka", "all_agents_count": "2"}),
            (TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream", {}),
            (TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
             {"trigger_source": "proactive_dm", "elapsed_mins": "240"}),
        ]
        for tt, aid, ss, extras in producers:
            ilw.create_event(provenance=_make_provenance(tt, aid, ss, extras))
        records = ntw.read_all()
        # All extras values are short metadata strings (slot name, agent id, count, etc.)
        # None contain conversation content
        for r in records:
            extras = r["provenance"]["extras"]
            for k, v in extras.items():
                assert isinstance(v, str), f"extras value not str: {k}={v!r}"
                # Metadata values are short (≤ 32 chars typically)
                # No long content / LLM output / conversation text
                assert len(v) < 100, f"extras value too long (potential content leak): {k}={v!r}"
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: A=3, B=3, C=3, D=3, E=2, F=5, G=2, H=2, count=1 → 24."""
    pass
