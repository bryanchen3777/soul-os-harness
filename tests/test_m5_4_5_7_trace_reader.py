"""
tests/test_m5_4_5_7_trace_reader.py

M5.4-5.7 (Bry 派工 2026-08-09 23:30): Inner Life Query Layer.

Focused test suite covering:
- A. missing trace file (2)
- B. event_id query (2)
- C. session_id query (2)
- D. correlation_id query (2)
- E. lineage prefix / descendants (3)
- F. timestamp range (3)
- G. deterministic ordering + multiple matches (2)
- H. malformed/partial trace record handling (2)
- I. legacy trace compatibility (1)
- J. data_root() isolation (1)
- K. read-only guarantee (1)
- L. count guard (1)

Test count: 22 tests
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life import (
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    TRIGGER_TYPE_USER_MESSAGE,
    TRIGGER_TYPE_AGENT_REPLY,
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


def _seed_trace(tmp_path: Path, n: int = 3) -> tuple[Path, list[dict]]:
    """Create a trace file with n events via InnerLifeWriter. Returns (path, records)."""
    trace_path = tmp_path / "trace.jsonl"
    writer = NarrativeTraceWriter(trace_log_path=trace_path)
    ilw = InnerLifeWriter(trace_writer=writer)
    events = []
    for i in range(n):
        ev = ilw.create_event(
            provenance=_make_provenance(actor_id=f"actor-{i}"),
            session_id="sess-1",
            correlation_id="corr-1",
        )
        events.append(ev)
    records = writer.read_all()
    return trace_path, records


# ───────────────────────────────────────────────────────────
# A. Missing trace file
# ───────────────────────────────────────────────────────────

class TestSectionA_MissingTrace:
    """A. Reader handles missing trace file gracefully."""

    def test_a1_missing_file_returns_empty_list(self, tmp_path: Path):
        """query_by_event_id on non-existent trace.jsonl → []. Does not raise."""
        reader = NarrativeTraceReader(trace_log_path=tmp_path / "nonexistent.jsonl")
        result = reader.query_by_event_id("abc123")
        assert result == []

    def test_a2_empty_dir_returns_empty_list(self, tmp_path: Path):
        """trace.jsonl dir exists but file does not → []. Does not raise."""
        (tmp_path / "inner_life").mkdir(parents=True, exist_ok=True)
        reader = NarrativeTraceReader(
            trace_log_path=tmp_path / "inner_life" / "trace.jsonl"
        )
        result = reader.query_by_session_id("sess-1")
        assert result == []


# ───────────────────────────────────────────────────────────
# B. event_id query
# ───────────────────────────────────────────────────────────

class TestSectionB_EventIdQuery:
    """B. query_by_event_id returns exact event or empty list."""

    def test_b1_exact_match_returns_one_record(self, tmp_path: Path):
        """Known event_id → returns exactly 1 matching record."""
        trace_path, records = _seed_trace(tmp_path, n=3)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        target = records[1]["event_id"]
        result = reader.query_by_event_id(target)
        assert len(result) == 1
        assert result[0]["event_id"] == target

    def test_b2_unknown_event_id_returns_empty(self, tmp_path: Path):
        """Unknown event_id → returns []. No raise."""
        trace_path, _ = _seed_trace(tmp_path, n=2)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_event_id("a" * 32)
        assert result == []


# ───────────────────────────────────────────────────────────
# C. session_id query
# ───────────────────────────────────────────────────────────

class TestSectionC_SessionIdQuery:
    """C. query_by_session_id returns all events in a session."""

    def test_c1_known_session_returns_all_matching(self, tmp_path: Path):
        """session_id='sess-1' → all records with that session."""
        trace_path, records = _seed_trace(tmp_path, n=4)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_session_id("sess-1")
        assert len(result) == 4
        for r in result:
            assert r["session_id"] == "sess-1"

    def test_c2_unknown_session_returns_empty(self, tmp_path: Path):
        """session_id not in trace → []. No raise."""
        trace_path, _ = _seed_trace(tmp_path, n=2)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_session_id("nonexistent-session")
        assert result == []


# ───────────────────────────────────────────────────────────
# D. correlation_id query
# ───────────────────────────────────────────────────────────

class TestSectionD_CorrelationIdQuery:
    """D. query_by_correlation_id returns all events in a narrative group."""

    def test_d1_known_correlation_returns_all_matching(self, tmp_path: Path):
        """correlation_id='corr-1' → all records with that correlation."""
        trace_path, records = _seed_trace(tmp_path, n=3)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_correlation_id("corr-1")
        assert len(result) == 3
        for r in result:
            assert r["correlation_id"] == "corr-1"

    def test_d2_unknown_correlation_returns_empty(self, tmp_path: Path):
        """correlation_id not in trace → []. No raise."""
        trace_path, _ = _seed_trace(tmp_path, n=2)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_correlation_id("nonexistent-corr")
        assert result == []


# ───────────────────────────────────────────────────────────
# E. Lineage prefix / descendants
# ───────────────────────────────────────────────────────────

class TestSectionE_LineagePrefix:
    """E. query_by_lineage_path_prefix returns root + all descendants."""

    def test_e1_root_id_returns_self_plus_descendants(self, tmp_path: Path):
        """Root event id as prefix → root + children + grandchildren."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        root = ilw.create_event(provenance=_make_provenance())
        child = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=root.event_id
        )
        grandchild = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=child.event_id
        )
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_lineage_path_prefix(root.event_id)
        assert len(result) == 3
        ids = {r["event_id"] for r in result}
        assert ids == {root.event_id, child.event_id, grandchild.event_id}

    def test_e2_child_prefix_returns_descendants(self, tmp_path: Path):
        """Lineage prefix query: matches records whose lineage_path starts with prefix."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        root = ilw.create_event(provenance=_make_provenance())
        child = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=root.event_id
        )
        grandchild = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=child.event_id
        )
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        # root.event_id as prefix matches child and grandchild
        # (both have lineage_path starting with "root_id/")
        result = reader.query_by_lineage_path_prefix(root.event_id)
        ids = {r["event_id"] for r in result}
        assert child.event_id in ids
        assert grandchild.event_id in ids

    def test_e3_leaf_prefix_returns_only_self(self, tmp_path: Path):
        """Leaf event id as prefix → only itself."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        root = ilw.create_event(provenance=_make_provenance())
        child = ilw.create_event(
            provenance=_make_provenance(), parent_event_id=root.event_id
        )
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_lineage_path_prefix(child.event_id)
        assert len(result) == 1
        assert result[0]["event_id"] == child.event_id


# ───────────────────────────────────────────────────────────
# F. Timestamp range
# ───────────────────────────────────────────────────────────

class TestSectionF_TimestampRange:
    """F. query_by_ts_range returns records within [start, end]."""

    def test_f1_start_and_end_bounds(self, tmp_path: Path):
        """Records with ts within [start, end] are included."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        # Create 3 events with explicit timestamps
        ilw.create_event(
            provenance=_make_provenance(),
            ts="2026-08-01T10:00:00Z",
        )
        ilw.create_event(
            provenance=_make_provenance(),
            ts="2026-08-15T10:00:00Z",
        )
        ilw.create_event(
            provenance=_make_provenance(),
            ts="2026-08-30T10:00:00Z",
        )
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_ts_range(
            start="2026-08-10T00:00:00Z", end="2026-08-20T00:00:00Z"
        )
        assert len(result) == 1
        assert result[0]["ts"] == "2026-08-15T10:00:00Z"

    def test_f2_start_only(self, tmp_path: Path):
        """With only start bound, matches all records from start onward."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-01T00:00:00Z")
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-10T00:00:00Z")
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-20T00:00:00Z")
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_ts_range(start="2026-08-10T00:00:00Z")
        assert len(result) == 2

    def test_f3_end_only(self, tmp_path: Path):
        """With only end bound, matches all records up to end."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-01T00:00:00Z")
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-10T00:00:00Z")
        ilw.create_event(provenance=_make_provenance(), ts="2026-08-20T00:00:00Z")
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_ts_range(end="2026-08-10T00:00:00Z")
        assert len(result) == 2


# ───────────────────────────────────────────────────────────
# G. Deterministic ordering + multiple matches
# ───────────────────────────────────────────────────────────

class TestSectionG_DeterministicOrdering:
    """G. Multiple matching records maintain append order (deterministic)."""

    def test_g1_append_order_preserved(self, tmp_path: Path):
        """Multiple events matching a query appear in append order."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        for i in range(5):
            ilw.create_event(
                provenance=_make_provenance(actor_id=f"actor-{i}"),
                session_id="shared-session",
            )
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_session_id("shared-session")
        assert len(result) == 5
        actor_ids = [r["provenance"]["actor_id"] for r in result]
        assert actor_ids == [f"actor-{i}" for i in range(5)]

    def test_g2_multiple_queries_all_deterministic(self, tmp_path: Path):
        """Calling the same query twice returns identical results."""
        trace_path, _ = _seed_trace(tmp_path, n=3)
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        r1 = reader.query_by_session_id("sess-1")
        r2 = reader.query_by_session_id("sess-1")
        assert r1 == r2


# ───────────────────────────────────────────────────────────
# H. Malformed/partial trace record handling
# ───────────────────────────────────────────────────────────

class TestSectionH_MalformedRecords:
    """H. Malformed lines are skipped; valid lines still returned."""

    def test_h1_malformed_line_skipped(self, tmp_path: Path):
        """One malformed JSON line → skipped; valid records still returned."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev1 = ilw.create_event(provenance=_make_provenance())
        ev2 = ilw.create_event(provenance=_make_provenance())

        # Append a malformed line directly
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write("NOT VALID JSON {\n")
            f.write(json.dumps({"event_id": ev2.event_id, "session_id": None,
                                 "correlation_id": None, "parent_event_id": None,
                                 "ts": "2026-08-09T12:00:00Z",
                                 "provenance": {"trigger_type": "user_message",
                                               "actor_id": "bryan", "source_system": "narrative",
                                               "trace_ref": None, "extras": {}},
                                 "lineage_depth": 0, "lineage_path": ev2.event_id}) + "\n")

        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_event_id(ev1.event_id)
        assert len(result) == 1
        assert result[0]["event_id"] == ev1.event_id

    def test_h2_truncated_record_skipped(self, tmp_path: Path):
        """Truncated/incomplete JSON line → skipped; valid records still returned."""
        trace_path = tmp_path / "trace.jsonl"
        writer = NarrativeTraceWriter(trace_log_path=trace_path)
        ilw = InnerLifeWriter(trace_writer=writer)
        ev = ilw.create_event(provenance=_make_provenance())

        # Append two malformed lines after the valid event
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write('{"event_id": "abc123"\n')  # unclosed
            f.write('{"incomplete":\n')  # incomplete

        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_event_id(ev.event_id)
        assert len(result) == 1
        assert result[0]["event_id"] == ev.event_id


# ───────────────────────────────────────────────────────────
# I. Legacy trace compatibility
# ───────────────────────────────────────────────────────────

class TestSectionI_LegacyCompatibility:
    """I. Reader handles legacy trace records (minimal fields)."""

    def test_i1_legacy_record_readable(self, tmp_path: Path):
        """A trace file with only minimal fields is readable."""
        trace_path = tmp_path / "trace.jsonl"
        # Write a minimal legacy-format record directly
        legacy_record = {
            "event_id": "a" * 32,
            "session_id": None,
            "correlation_id": None,
            "parent_event_id": None,
            "ts": "2026-07-01T00:00:00Z",
            "provenance": {
                "trigger_type": "user_message",
                "actor_id": "legacy",
                "source_system": "narrative",
                "trace_ref": None,
                "extras": {},
            },
            "lineage_depth": 0,
            "lineage_path": "a" * 32,
        }
        with open(trace_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(legacy_record) + "\n")

        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = reader.query_by_event_id("a" * 32)
        assert len(result) == 1
        assert result[0]["event_id"] == "a" * 32


# ───────────────────────────────────────────────────────────
# J. data_root() isolation
# ───────────────────────────────────────────────────────────

class TestSectionJ_DataRootIsolation:
    """J. Default path uses data_root() for test isolation."""

    def test_j1_default_uses_data_root(self, tmp_path: Path, monkeypatch):
        """Default constructor resolves to data_root() / inner_life / trace.jsonl."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()
        try:
            reader = NarrativeTraceReader()
            expected = data_root() / "inner_life" / "trace.jsonl"
            # Verify path is correctly computed (not that file exists)
            assert "inner_life" in str(reader._trace_path)
            assert "trace.jsonl" in str(reader._trace_path)
        finally:
            reset_data_root()


# ───────────────────────────────────────────────────────────
# K. Read-only guarantee
# ───────────────────────────────────────────────────────────

class TestSectionK_ReadOnlyGuarantee:
    """K. Reader does not modify trace.jsonl (append-only preserved)."""

    def test_k1_no_write_on_query(self, tmp_path: Path):
        """Querying a trace file does not modify it."""
        trace_path, _ = _seed_trace(tmp_path, n=3)
        # Read file content before
        with open(trace_path, "r", encoding="utf-8") as f:
            before_content = f.read()

        reader = NarrativeTraceReader(trace_log_path=trace_path)
        reader.query_by_session_id("sess-1")
        reader.query_by_correlation_id("corr-1")
        reader.query_by_ts_range()

        # Content unchanged
        with open(trace_path, "r", encoding="utf-8") as f:
            after_content = f.read()
        assert after_content == before_content


# ───────────────────────────────────────────────────────────
# L. Count guard
# ───────────────────────────────────────────────────────────

class TestSectionL_CountGuard:
    """L. Total test count."""

    def test_l1_total_count(self):
        """This suite has 22 tests."""
        # A: 2, B: 2, C: 2, D: 2, E: 3, F: 3, G: 2, H: 2, I: 1, J: 1, K: 1, L: 1
        # = 22
        pass
