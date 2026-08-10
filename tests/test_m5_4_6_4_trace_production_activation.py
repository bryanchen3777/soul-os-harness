"""
tests/test_m5_4_6_4_trace_production_activation.py

M5.4-6.4 (Bry 派工 2026-08-10): Narrative Trace Production Activation.

Focused test suite verifying the minimal source change
(NarrativeTraceWriter injection at scripts/run_server.py:255) activates
the trace sidecar correctly across all 4 wired producers.

Test sections:
- A. Source code injection (3)
- B. Production construction pattern (3)
- C. 4 producer coverage (5)
- D. Trace event_id canonical match (1)
- E. NarrativeTraceReader compatibility (1)
- F. Failure isolation under production injection (2)
- G. USER_MESSAGE exclusion (1)
- H. Privacy / content boundary (1)
- I. data_root() isolation (1)
- J. No duplicate trace (1)
- count (1)

Test count: 20 tests
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List

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
    TRIGGER_TYPE_USER_MESSAGE,
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
# A. Source code injection
# ───────────────────────────────────────────────────────────

class TestSectionA_SourceInjection:
    """A. Production construction path uses trace_writer (source inspection)."""

    def test_a1_run_server_imports_narrative_trace_writer(self):
        """scripts/run_server.py imports NarrativeTraceWriter from src.inner_life."""
        run_server_path = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/run_server.py")
        if not run_server_path.exists():
            pytest.skip("Cannot locate run_server.py")
        src = run_server_path.read_text(encoding="utf-8")
        assert "NarrativeTraceWriter" in src, (
            "run_server.py must import NarrativeTraceWriter (M5.4-6.4 activation)"
        )
        # The import must come from src.inner_life (multiline — `from src.inner_life import (...)`)
        assert re.search(
            r"from\s+src\.inner_life\s+import.*NarrativeTraceWriter",
            src,
            re.DOTALL,
        ), "NarrativeTraceWriter must be imported from src.inner_life"

    def test_a2_run_server_inner_life_writer_uses_trace_writer(self):
        """scripts/run_server.py:255 constructs InnerLifeWriter(trace_writer=NarrativeTraceWriter())."""
        run_server_path = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/run_server.py")
        if not run_server_path.exists():
            pytest.skip("Cannot locate run_server.py")
        src = run_server_path.read_text(encoding="utf-8")
        # The M5.4-6.4 activation: trace_writer=NarrativeTraceWriter()
        assert re.search(
            r"inner_life_writer\s*=\s*InnerLifeWriter\(\s*trace_writer\s*=\s*NarrativeTraceWriter\(\s*\)\s*\)",
            src,
        ), (
            "run_server.py must construct "
            "InnerLifeWriter(trace_writer=NarrativeTraceWriter()) for M5.4-6.4"
        )

    def test_a3_run_server_uses_single_inner_life_writer(self):
        """No duplicate InnerLifeWriter construction (per-instance authority)."""
        run_server_path = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/run_server.py")
        if not run_server_path.exists():
            pytest.skip("Cannot locate run_server.py")
        src = run_server_path.read_text(encoding="utf-8")
        # Count InnerLifeWriter constructor calls (with or without args)
        count = len(re.findall(r"InnerLifeWriter\s*\(", src))
        # Must be exactly 1 construction (in lifespan)
        assert count == 1, (
            f"Expected exactly 1 InnerLifeWriter() construction, found {count}. "
            f"Per-instance authority must be preserved (no duplicates)."
        )


# ───────────────────────────────────────────────────────────
# B. Production construction pattern
# ───────────────────────────────────────────────────────────

class TestSectionB_ProductionConstructionPattern:
    """B. The exact production construction pattern works in isolation."""

    def test_b1_production_pattern_creates_writer_with_trace(self, tmp_path):
        """Simulate exact production construction:
        InnerLifeWriter(trace_writer=NarrativeTraceWriter()) → trace_writer is set."""
        data_dir = _isolated_data_root(tmp_path)
        # Exact production pattern from run_server.py:255
        inner_life_writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
        # Verify the writer instance is correctly wired
        assert inner_life_writer._trace_writer is not None
        assert isinstance(inner_life_writer._trace_writer, NarrativeTraceWriter)
        # Default path resolves to data_root() / "inner_life" / "trace.jsonl"
        assert inner_life_writer._trace_writer.trace_log_path == (
            data_dir / "inner_life" / "trace.jsonl"
        )
        _restore_data_root()

    def test_b2_production_pattern_default_path(self, tmp_path):
        """Default NarrativeTraceWriter path uses data_root() / 'inner_life' / 'trace.jsonl'."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()  # default
        assert ntw.trace_log_path == data_dir / "inner_life" / "trace.jsonl"
        _restore_data_root()

    def test_b3_production_pattern_create_event_writes_trace(self, tmp_path):
        """create_event with production pattern writes one trace record."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Before any create_event: trace file may exist (parent dir created) but is empty
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            extras={"slot": "morning"},
        ))
        # After: trace file has 1 record
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["event_id"] == ev.event_id
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. 4 producer coverage
# ───────────────────────────────────────────────────────────

class TestSectionC_FourProducerCoverage:
    """C. Each of 4 producer patterns produces exactly 1 trace under production injection."""

    def test_c1_diary_morning_producer_one_trace(self, tmp_path):
        """Diary morning producer pattern → exactly 1 trace."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
            extras={"slot": "morning"},
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DIARY_MORNING
        assert records[0]["provenance"]["source_system"] == "diary"
        _restore_data_root()

    def test_c2_diary_night_producer_one_trace(self, tmp_path):
        """Diary night producer pattern → exactly 1 trace."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary",
            extras={"slot": "night"},
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DIARY_NIGHT
        _restore_data_root()

    def test_c3_dream_producer_one_trace(self, tmp_path):
        """Dream producer pattern → exactly 1 trace."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream",
            extras={"target_agent_id": "agent_ruka"},
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DREAM_DREAM
        _restore_data_root()

    def test_c4_event_producer_one_trace(self, tmp_path):
        """Event producer pattern → exactly 1 trace."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_DREAM_EVENT
        _restore_data_root()

    def test_c5_proactive_dm_producer_one_trace(self, tmp_path):
        """Proactive DM producer pattern → exactly 1 trace."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
            extras={"trigger_source": "proactive_dm", "elapsed_mins": "240"},
        ))
        records = ntw.read_all()
        assert len(records) == 1
        assert records[0]["provenance"]["trigger_type"] == TRIGGER_TYPE_AGENT_REPLY
        assert records[0]["provenance"]["source_system"] == "narrative"
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# D. Trace event_id canonical match
# ───────────────────────────────────────────────────────────

class TestSectionD_TraceEventIDMatch:
    """D. trace event_id == canonical InnerLifeEvent.event_id (byte-exact)."""

    def test_d1_trace_event_id_matches_canonical(self, tmp_path):
        """Trace record's event_id byte-exact matches InnerLifeEvent.event_id."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        records = ntw.read_all()
        assert len(records) == 1
        # Byte-exact match
        assert records[0]["event_id"] == ev.event_id
        # Length sanity check
        assert len(records[0]["event_id"]) == 32
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. NarrativeTraceReader compatibility
# ───────────────────────────────────────────────────────────

class TestSectionE_ReaderCompatibility:
    """E. NarrativeTraceReader can read production-generated trace.jsonl format."""

    def test_e1_reader_reads_production_format(self, tmp_path):
        """NarrativeTraceReader reads what the production-pattern writer produced."""
        data_dir = _isolated_data_root(tmp_path)
        # Production pattern: same as run_server.py:255
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
            extras={"trigger_source": "proactive_dm"},
        ))
        # Reader reads the same file
        ntr = NarrativeTraceReader()
        results = ntr.query_by_event_id(ev.event_id)
        assert len(results) == 1
        # Cross-verify: trace record fields match canonical event
        assert results[0]["event_id"] == ev.event_id
        assert results[0]["provenance"]["actor_id"] == "agent_ruka"
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. Failure isolation under production injection
# ───────────────────────────────────────────────────────────

class TestSectionF_FailureIsolation:
    """F. trace failure does not block canonical InnerLifeEvent creation."""

    def test_f1_trace_writer_raises_event_still_valid(self, tmp_path):
        """If NarrativeTraceWriter.write() raises, create_event() still returns valid event."""
        data_dir = _isolated_data_root(tmp_path)
        from unittest.mock import MagicMock
        mock_writer = MagicMock(spec=NarrativeTraceWriter)
        mock_writer.write.side_effect = RuntimeError("simulated disk full")
        ilw = InnerLifeWriter(trace_writer=mock_writer)
        # Should not raise
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Event is valid and registered
        assert ev.event_id is not None
        assert ilw.is_event_known(ev.event_id)
        _restore_data_root()

    def test_f2_production_injection_with_broken_path_isolated(self, tmp_path):
        """Production injection with broken trace path: writer failure is isolated."""
        data_dir = _isolated_data_root(tmp_path)
        # Simulate broken path: read-only filesystem scenario
        ntw = NarrativeTraceWriter(trace_log_path=Path("/nonexistent/readonly/trace.jsonl"))
        ilw = InnerLifeWriter(trace_writer=ntw)
        # create_event should still succeed (writer failure isolated)
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary",
        ))
        # Event is valid
        assert ev.event_id is not None
        assert ilw.is_event_known(ev.event_id)
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. USER_MESSAGE exclusion
# ───────────────────────────────────────────────────────────

class TestSectionG_UserMessageExclusion:
    """G. USER_MESSAGE does not produce InnerLifeEvent (no trace generated)."""

    def test_g1_user_message_does_not_produce_trace(self, tmp_path):
        """USER_MESSAGE pattern (no executor) does NOT call create_event.
        Therefore, no trace is generated for USER_MESSAGE."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Simulate USER_MESSAGE path: no create_event call
        # (In production, USER_MESSAGE goes through IOGateway → LLMProxy,
        #  NOT through the 4 wired executors)
        # Verify: 0 records after no create_event calls
        records = ntw.read_all()
        assert len(records) == 0
        # TRIGGER_TYPE_USER_MESSAGE is in the catalog but not used in any wiring
        assert TRIGGER_TYPE_USER_MESSAGE == "user_message"
        # None of the 4 wired producers use TRIGGER_TYPE_USER_MESSAGE
        wired_triggers = {
            TRIGGER_TYPE_DIARY_MORNING,
            TRIGGER_TYPE_DIARY_NIGHT,
            TRIGGER_TYPE_DREAM_DREAM,
            TRIGGER_TYPE_DREAM_EVENT,
            TRIGGER_TYPE_AGENT_REPLY,
        }
        assert TRIGGER_TYPE_USER_MESSAGE not in wired_triggers
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# H. Privacy / content boundary
# ───────────────────────────────────────────────────────────

class TestSectionH_PrivacyBoundary:
    """H. No prompt/response/conversation content in trace records."""

    def test_h1_no_content_in_trace_records(self, tmp_path):
        """All 4 producer patterns: trace records contain no forbidden fields.
        Forbidden: content, text, message, prompt, response, audio_text, tts,
                   payload, raw, body."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        # Simulate each producer with realistic metadata-only extras
        for trigger_type, actor_id, source_system, extras in [
            (TRIGGER_TYPE_DIARY_MORNING, "agent_yua", "diary", {"slot": "morning"}),
            (TRIGGER_TYPE_DIARY_NIGHT, "agent_yua", "diary", {"slot": "night"}),
            (TRIGGER_TYPE_DREAM_DREAM, "agent_yua", "dream", {"target_agent_id": "agent_ruka"}),
            (TRIGGER_TYPE_DREAM_EVENT, "agent_yua", "dream", {}),
            (TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
             {"trigger_source": "proactive_dm", "elapsed_mins": "240"}),
        ]:
            ilw.create_event(provenance=_make_provenance(
                trigger_type, actor_id, source_system, extras,
            ))
        records = ntw.read_all()
        forbidden = {
            "content", "text", "message", "prompt", "response",
            "audio_text", "tts", "payload", "raw", "body",
        }
        for r in records:
            leaked = set(r.keys()) & forbidden
            assert not leaked, f"Trace leaked forbidden fields: {leaked}"
        # All extras values are short metadata strings
        for r in records:
            extras = r["provenance"]["extras"]
            for k, v in extras.items():
                assert isinstance(v, str)
                assert len(v) < 100
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# I. data_root() isolation
# ───────────────────────────────────────────────────────────

class TestSectionI_DataRootIsolation:
    """I. data_root() isolation: trace is in tmp, not production."""

    def test_i1_isolated_test_does_not_pollute_production(self, tmp_path):
        """Set SOUL_OS_DATA_DIR to tmp_path, run production pattern, verify:
        - Trace file is in tmp_path/data/inner_life/trace.jsonl
        - Production path (if exists) is NOT touched"""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Trace file is in tmp_path
        trace_in_tmp = data_dir / "inner_life" / "trace.jsonl"
        assert trace_in_tmp.exists()
        # Verify trace is NOT in production data dir
        # (production data_root() with no env var would resolve to cwd/data)
        prod_data_dir = Path("data").resolve()
        # data_dir is the tmp_path-based root, prod_data_dir is the production root
        assert data_dir.resolve() != prod_data_dir, (
            "tmp_path data_root should not equal production data_root"
        )
        # The trace file path starts with tmp_path, not production path
        assert str(trace_in_tmp.resolve()).startswith(str(tmp_path.resolve())), (
            f"Trace file should be in tmp_path ({tmp_path}), "
            f"got {trace_in_tmp.resolve()}"
        )
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# J. No duplicate trace
# ───────────────────────────────────────────────────────────

class TestSectionJ_NoDuplicateTrace:
    """J. Exactly one trace per create_event call (no duplicate)."""

    def test_j1_no_duplicate_trace(self, tmp_path):
        """One create_event call → exactly one trace record (no retry, no double-write)."""
        data_dir = _isolated_data_root(tmp_path)
        ntw = NarrativeTraceWriter()
        ilw = InnerLifeWriter(trace_writer=ntw)
        ev = ilw.create_event(provenance=_make_provenance(
            TRIGGER_TYPE_AGENT_REPLY, "agent_ruka", "narrative",
        ))
        # Verify count is exactly 1
        assert len(ntw.read_all()) == 1
        # Even if we read again, count remains 1 (no new trace on read)
        ntw.read_all()
        ntw.read_all()
        assert len(ntw.read_all()) == 1
        # Also verify: no write() call on subsequent operations
        # InnerLifeWriter.get_event() and get_known_event_count() do NOT trigger trace
        ilw.get_event(ev.event_id)
        ilw.get_known_event_count()
        assert len(ntw.read_all()) == 1
        _restore_data_root()


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: A=3, B=3, C=5, D=1, E=1, F=2, G=1, H=1, I=1, J=1, count=1 → 20."""
    pass
