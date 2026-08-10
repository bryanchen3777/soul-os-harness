"""
src/inner_life/trace_reader.py

M5.4-5.7 (Bry 派工 2026-08-09 23:30) — Inner Life Query Layer

Read-only query APIs over Narrative Trace (data/inner_life/trace.jsonl).

Constraints:
  - READ-ONLY (no writes, no mutation)
  - No database, no embeddings, no vector search, no caching
  - Malformed JSONL records do not corrupt valid records
  - Missing trace file handled cleanly
  - Deterministic ordering preserved
  - data_root() isolation respected

Module follows same pattern as:
  - src/world/trace.py (WorldPerceptionTraceWriter)
  - src/memory/v1/loader.py + middleware.py (loader_trace.jsonl)
  - src/memory/v1/store.py (canonical append-only jsonl)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("soul_os.inner_life.trace_reader")


class NarrativeTraceReader:
    """
    Read-only query layer over data/inner_life/trace.jsonl.

    This is NOT a source of truth — it only reads what
    NarrativeTraceWriter (M5.4-5.6) has already written.

    Public API (5 query methods):
      - query_by_event_id(event_id)       → list[dict] (0 or 1)
      - query_by_session_id(session_id)   → list[dict]
      - query_by_correlation_id(corr_id)  → list[dict]
      - query_by_lineage_path_prefix(prefix) → list[dict]
      - query_by_ts_range(start, end)    → list[dict]

    All methods return dicts, not InnerLifeEvent objects.
    Malformed lines in trace.jsonl are skipped (logged warning).
    Missing trace file returns empty list.
    Ordering: deterministic (append order preserved).
    """

    def __init__(self, trace_log_path: Optional[Path] = None) -> None:
        """
        Args:
            trace_log_path: explicit path to trace.jsonl (optional).
                            None = use data_root() / "inner_life" / "trace.jsonl".
        """
        if trace_log_path is None:
            from src.paths import data_root

            trace_log_path = data_root() / "inner_life" / "trace.jsonl"
        self._trace_path = Path(trace_log_path)

    # ─────────────────────────────────────────────────────────────
    # Core: read all records (with malformed-line resilience)
    # ─────────────────────────────────────────────────────────────

    def _read_all(self) -> list[dict]:
        """
        Read all valid JSON records from trace.jsonl.

        Handles:
          - missing file → returns []
          - malformed JSON line → skips + logs warning
          - empty lines → skips

        Returns:
            list of dict (each = one trace record, in append order)
        """
        if not self._trace_path.exists():
            return []
        records: list[dict] = []
        try:
            with open(self._trace_path, "r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        records.append(json.loads(stripped))
                    except json.JSONDecodeError as e:
                        logger.warning(
                            f"[NarrativeTraceReader] skipping malformed line "
                            f"{lineno} in {self._trace_path}: {e}"
                        )
        except OSError as e:
            logger.warning(
                f"[NarrativeTraceReader] read failed for {self._trace_path}: {e}"
            )
        return records

    # ─────────────────────────────────────────────────────────────
    # Query APIs
    # ─────────────────────────────────────────────────────────────

    def query_by_event_id(self, event_id: str) -> list[dict]:
        """
        Get all trace records matching event_id.

        Returns 0 or 1 record (event_id is unique).
        Ordering: append order (deterministic).
        """
        if not event_id:
            return []
        return [r for r in self._read_all() if r.get("event_id") == event_id]

    def query_by_session_id(self, session_id: str) -> list[dict]:
        """
        Get all trace records for a given session_id.

        Returns records in append order (deterministic).
        """
        if not session_id:
            return []
        return [r for r in self._read_all() if r.get("session_id") == session_id]

    def query_by_correlation_id(self, correlation_id: str) -> list[dict]:
        """
        Get all trace records for a given correlation_id (narrative group).

        Returns records in append order (deterministic).
        """
        if not correlation_id:
            return []
        return [
            r for r in self._read_all() if r.get("correlation_id") == correlation_id
        ]

    def query_by_lineage_path_prefix(self, prefix: str) -> list[dict]:
        """
        Get all trace records whose lineage_path matches the given prefix.

        For prefix = "root_event_id" this returns root + all descendants
        (any record whose lineage_path begins with "root_event_id/...").

        For prefix = "child_event_id" this returns the child + all its
        descendants (lineage_path begins with "child_event_id/...").

        For prefix = "leaf_event_id" (root leaf) this returns just itself.

        Returns records in append order (deterministic).
        """
        if not prefix:
            return []
        prefix_slash = prefix.rstrip("/") + "/"
        return [
            r
            for r in self._read_all()
            if r.get("lineage_path", "").startswith(prefix_slash)
            or r.get("lineage_path", "") == prefix
            # Also match the event itself (root events: path == event_id)
            or r.get("event_id") == prefix
        ]

    def query_by_ts_range(
        self, start: Optional[str] = None, end: Optional[str] = None
    ) -> list[dict]:
        """
        Get all trace records where start <= ts <= end (ISO 8601 strings).

        Args:
            start: ISO 8601 lower bound (inclusive), e.g. "2026-08-01T00:00:00Z"
            end:   ISO 8601 upper bound (inclusive), e.g. "2026-08-09T23:59:59Z"

        Returns records in append order (deterministic).
        If start is None, matches all records up to end.
        If end is None, matches all records from start.
        Both None → returns all records.
        """
        records = self._read_all()
        result = []
        for r in records:
            ts = r.get("ts", "")
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            result.append(r)
        return result
