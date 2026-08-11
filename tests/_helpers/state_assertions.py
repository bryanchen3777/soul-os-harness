"""
tests/_helpers/state_assertions.py

M6.0-2 (Bry 派工 2026-08-11): Reusable state assertion helpers.

Supports:
  - file state (D-File): file exists, content matches
  - DB state (D-DB): SQLite query returns expected value
  - runtime state (D-State): in-memory value matches expected
  - event state (D-Event): specific event was published
  - context ordering (D-Order): context blocks appear in expected order

Each assertion returns True on success, raises AssertionError on failure.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("tests.helpers.state_assertions")


# ── D-File: file state assertions ──

def assert_file_exists(path: Path, label: str = "") -> None:
    """D-File: Assert file exists at given path."""
    if not path.exists():
        raise AssertionError(
            f"[D-File] Expected file to exist: {path}"
            f"{f' ({label})' if label else ''}"
        )


def assert_file_contains(path: Path, substring: str, label: str = "") -> None:
    """D-File: Assert file content contains substring."""
    assert_file_exists(path, label)
    content = path.read_text(encoding="utf-8")
    if substring not in content:
        raise AssertionError(
            f"[D-File] Expected '{path}' to contain: {substring!r}"
            f"{f' ({label})' if label else ''}\n"
            f"  Actual content (first 500 chars):\n  {content[:500]}"
        )


def assert_file_not_contains(path: Path, substring: str, label: str = "") -> None:
    """D-File: Assert file content does NOT contain substring."""
    if not path.exists():
        return  # nothing to check
    content = path.read_text(encoding="utf-8")
    if substring in content:
        raise AssertionError(
            f"[D-File] Expected '{path}' to NOT contain: {substring!r}"
            f"{f' ({label})' if label else ''}"
        )


def assert_file_json_matches(path: Path, key: str, expected: Any, label: str = "") -> None:
    """D-File: Assert JSON file has expected value at key path (dot notation)."""
    assert_file_exists(path, label)
    data = json.loads(path.read_text(encoding="utf-8"))
    actual = _drill(data, key)
    if actual != expected:
        raise AssertionError(
            f"[D-File] Expected '{path}' JSON at '{key}' = {expected!r},"
            f" got {actual!r}{f' ({label})' if label else ''}"
        )


# ── D-DB: SQLite state assertions ──

def assert_db_row_count(db_path: Path, table: str, expected: int, label: str = "") -> None:
    """D-DB: Assert SQLite table has expected row count."""
    if not db_path.exists():
        raise AssertionError(f"[D-DB] DB not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(f"SELECT COUNT(*) FROM {table}")
        actual = cursor.fetchone()[0]
    if actual != expected:
        raise AssertionError(
            f"[D-DB] Expected {table} count = {expected}, got {actual}"
            f"{f' ({label})' if label else ''}"
        )


def assert_db_value(
    db_path: Path, table: str, column: str,
    where: str, where_args: Tuple, expected: Any, label: str = "",
) -> None:
    """D-DB: Assert SQLite query returns expected value at column."""
    if not db_path.exists():
        raise AssertionError(f"[D-DB] DB not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        cursor = conn.execute(
            f"SELECT {column} FROM {table} WHERE {where}", where_args
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError(
            f"[D-DB] No row in {table} WHERE {where} {where_args}"
            f"{f' ({label})' if label else ''}"
        )
    actual = row[0]
    if actual != expected:
        raise AssertionError(
            f"[D-DB] Expected {table}.{column} = {expected!r}, got {actual!r}"
            f"{f' ({label})' if label else ''}"
        )


# ── D-State: runtime state assertions ──

def assert_state_equals(actual: Any, expected: Any, label: str = "") -> None:
    """D-State: Assert two values are equal."""
    if actual != expected:
        raise AssertionError(
            f"[D-State] Expected {expected!r}, got {actual!r}"
            f"{f' ({label})' if label else ''}"
        )


def assert_state_approx(actual: float, expected: float, tolerance: float = 1e-6, label: str = "") -> None:
    """D-State: Assert two floats are within tolerance (for decay-affected values)."""
    if abs(actual - expected) > tolerance:
        raise AssertionError(
            f"[D-State] Expected {expected} (±{tolerance}), got {actual}"
            f"{f' ({label})' if label else ''}"
        )


def assert_state_in_range(actual: float, min_val: float, max_val: float, label: str = "") -> None:
    """D-State: Assert value is in range [min, max]."""
    if not (min_val <= actual <= max_val):
        raise AssertionError(
            f"[D-State] Expected {min_val} <= {actual} <= {max_val}"
            f"{f' ({label})' if label else ''}"
        )


def assert_call_count(call_log: List[Any], expected_min: int, label: str = "") -> None:
    """D-State: Assert call count is at least expected_min."""
    actual = len(call_log)
    if actual < expected_min:
        raise AssertionError(
            f"[D-State] Expected at least {expected_min} calls, got {actual}"
            f"{f' ({label})' if label else ''}"
        )


# ── D-Event: event publication assertions ──

def assert_event_published(
    events: List[Any],
    event_type: Optional[str] = None,
    source: Optional[str] = None,
    label: str = "",
) -> Any:
    """D-Event: Assert at least one event matching criteria was published.

    Returns the matching event.
    """
    matches = events
    if event_type is not None:
        # event_type might be enum or str
        matches = [
            e for e in matches
            if getattr(e, "event_type", None) is not None
            and (
                e.event_type == event_type
                or str(e.event_type) == event_type
                or (hasattr(e.event_type, "value") and e.event_type.value == event_type)
            )
        ]
    if source is not None:
        matches = [e for e in matches if getattr(e, "source", None) == source]
    if not matches:
        raise AssertionError(
            f"[D-Event] Expected event with type={event_type} source={source}"
            f"{f' ({label})' if label else ''}. "
            f"Got {len(events)} events total."
        )
    return matches[0]


# ── D-Order: context ordering assertions ──

def assert_context_order(
    text: str,
    markers: List[str],
    label: str = "",
) -> None:
    """D-Order: Assert markers appear in text in expected order.

    Each marker is a substring. Markers must appear in the given order.
    """
    positions = []
    for marker in markers:
        pos = text.find(marker)
        if pos < 0:
            raise AssertionError(
                f"[D-Order] Marker not found: {marker!r}{f' ({label})' if label else ''}"
            )
        positions.append(pos)

    if positions != sorted(positions):
        # Show actual order
        sorted_markers = [markers[i] for i in sorted(range(len(positions)), key=lambda i: positions[i])]
        raise AssertionError(
            f"[D-Order] Markers not in expected order."
            f"{f' ({label})' if label else ''}\n"
            f"  Expected: {markers}\n"
            f"  Actual:   {sorted_markers}"
        )


def assert_text_contains(text: str, substring: str, label: str = "") -> None:
    """Generic: assert text contains substring."""
    if substring not in text:
        raise AssertionError(
            f"Expected text to contain: {substring!r}"
            f"{f' ({label})' if label else ''}\n"
            f"  Actual text (first 500 chars):\n  {text[:500]}"
        )


def assert_text_not_contains(text: str, substring: str, label: str = "") -> None:
    """Generic: assert text does NOT contain substring."""
    if substring in text:
        raise AssertionError(
            f"Expected text to NOT contain: {substring!r}"
            f"{f' ({label})' if label else ''}"
        )


# ── Checkpoint runner ──

class CheckpointRunner:
    """
    Helper to run a sequence of checkpoints and report which passed/failed.

    Usage:
        runner = CheckpointRunner("Scenario A")
        runner.run("A1", lambda: assert_file_exists(...))
        runner.run("A2", lambda: assert_state_equals(...))
        runner.run("A3", lambda: assert_event_published(...))

        if runner.failures:
            raise AssertionError(...)
    """

    def __init__(self, scenario_name: str):
        self.scenario_name = scenario_name
        self.passed: List[str] = []
        self.failures: List[Tuple[str, str]] = []

    def run(self, checkpoint_id: str, assertion_fn) -> None:
        """Run a single checkpoint. Records pass/fail."""
        try:
            assertion_fn()
            self.passed.append(checkpoint_id)
            logger.info(f"[{self.scenario_name}] ✓ {checkpoint_id}")
        except AssertionError as e:
            self.failures.append((checkpoint_id, str(e)))
            logger.warning(f"[{self.scenario_name}] ✗ {checkpoint_id}: {e}")

    def summary(self) -> Dict[str, Any]:
        return {
            "scenario": self.scenario_name,
            "passed": self.passed,
            "passed_count": len(self.passed),
            "failed": self.failures,
            "failed_count": len(self.failures),
        }

    def assert_all_passed(self) -> None:
        if self.failures:
            error_lines = [f"  - {cid}: {msg}" for cid, msg in self.failures]
            raise AssertionError(
                f"[{self.scenario_name}] {len(self.failures)} checkpoint(s) failed:\n"
                + "\n".join(error_lines)
            )


# ── Private helpers ──

def _drill(data: Dict, key: str) -> Any:
    """Drill into nested dict using dot notation."""
    parts = key.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
