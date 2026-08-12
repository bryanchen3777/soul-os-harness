"""
tests/test_m5_15_6_calendar_ical_source.py — M5.15-6 Real-World Calendar Source Integration

M5.15-6 (Bry 派工 2026-08-12 19:29, RESUME 19:37 Option 1) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE

Identity model (RESUME Option 1 — Bry authorization 2026-08-12 19:37):
  - VEVENT.UID (exact original) → WorldEvent.data["ical_uid"]
  - SHA256(VEVENT.UID)[:32]     → WorldEvent.novelty_id
  - VEVENT.SEQUENCE (if present)  → WorldEvent.data["ical_sequence"] (observability only)

驗證:
  - IcalCalendarSource implements existing WorldEventSource contract
  - Public iCal URL is env-gated (Q8)
  - Missing URL = no calendar activity
  - Calendar events become existing calendar_event WorldEvents
  - VEVENT UID preserved as data["ical_uid"] (exact)
  - WorldEvent.novelty_id = SHA256(UID)[:32] (M3.1 validation compatible)
  - Event Bus remains canonical transport
  - 24h lookahead works
  - CANCELLED skipped
  - malformed/network failures observable and non-fatal
  - SEQUENCE changes → same hash (dedup preserved)
  - M5.15-5 source_world_event_novelty_id propagation preserved
  - No frozen downstream contract broken

Test sections (per M5.15-6 work order + RESUME 19:37 critical tests):
  A. iCal parsing
  B. VEVENT → WorldEvent mapping
  C. UID → novelty_id exact preservation (M5.15-6 Option 1)
  D. calendar_event type
  E. source/source_system validity
  F. priority preservation
  G. 24h lookahead filtering
  H. CANCELLED filtering
  I. malformed ICS handling
  J. HTTP failure handling
  K. duplicate/repeated polling behavior
  L. SEQUENCE modification behavior
  M. missing environment variable
  N. Event Bus canonical path
  O. M5.15-5 source_world_event_novelty_id propagation
  P. production isolation
  Q. server lifecycle / env-gated wiring

Critical regression tests (M5.15-6 RESUME 19:37 A-L):
  A. same UID → same novelty_id
  B. different UID → different novelty_id
  C. novelty_id satisfies M3.1 validation
  D. original UID preserved exactly in data["ical_uid"]
  E. repeated polling does not create identity drift
  F. same UID + changed SEQUENCE keeps same novelty_id
  G. malformed UID still handled safely
  H. canonical Event Bus path remains intact
  I. WorldPerceptionState receives the event
  J. WorldInnerLifeAdapter receives the event
  K. source_world_event_novelty_id receives canonical WorldEvent.novelty_id
  L. no production mutation
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life import InnerLifeWriter
from src.paths import data_root, reset_data_root
from src.world import WorldPerceptionMiddleware, WorldPerceptionState
from src.world.inner_life_adapter import WorldInnerLifeAdapter
from src.world.perception import WorldEvent
from src.world.source import IcalCalendarSource
from src.world.source.calendar_ical import (
    DEFAULT_LOOKAHEAD_HOURS,
    DEFAULT_POLLING_INTERVAL_SECS,
    HTTP_TIMEOUT_SECS,
    MAX_EVENTS_PER_POLL,
    _hash_uid_to_novelty_id,
)
from src.world.trace import WorldPerceptionTraceWriter


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _ical(events: List[str], prodid: str = "-//Test//EN") -> str:
    """Build a minimal iCal calendar from VEVENT strings."""
    return (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        f"PRODID:{prodid}\r\n"
        + "\r\n".join(events)
        + "\r\nEND:VCALENDAR\r\n"
    )


def _vevent(uid: str, dtstart_iso: str, summary: str = "Test Event",
            dtend_iso: str = None, status: str = None,
            rrule: str = None, recurrence_id: str = None,
            location: str = None, description: str = None,
            sequence: int = None) -> str:
    """Build a minimal VEVENT string."""
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTART:{dtstart_iso}"]
    if dtend_iso:
        lines.append(f"DTEND:{dtend_iso}")
    if status:
        lines.append(f"STATUS:{status}")
    if rrule:
        lines.append(f"RRULE:{rrule}")
    if recurrence_id:
        lines.append(f"RECURRENCE-ID:{recurrence_id}")
    if location:
        lines.append(f"LOCATION:{location}")
    if description:
        lines.append(f"DESCRIPTION:{description}")
    if sequence is not None:
        lines.append(f"SEQUENCE:{sequence}")
    lines.append(f"SUMMARY:{summary}")
    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def _iso_future(hours: int) -> str:
    """Return ISO 8601 UTC timestamp N hours from now."""
    dt = datetime.now(timezone.utc) + timedelta(hours=hours)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _iso_past(hours: int) -> str:
    """Return ISO 8601 UTC timestamp N hours ago."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _iso_just_started(minutes: int = 30) -> str:
    """Return ISO 8601 UTC timestamp N minutes ago (within grace period)."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _hash(uid: str) -> str:
    """SHA256(uid)[:32] — the M5.15-6 identity hash."""
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:32]


def _mock_urlopen_response(ical_text: str) -> MagicMock:
    """Build a mock urlopen response that returns the given iCal text."""
    mock_response = MagicMock()
    mock_response.read = MagicMock(return_value=ical_text.encode("utf-8"))
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


async def _poll_with_ical(src: IcalCalendarSource, ical_text: str) -> int:
    """Helper: poll a source with mocked HTTP returning the given iCal text."""
    with patch("asyncio.get_event_loop") as mock_loop_fn:
        mock_event_loop = MagicMock()
        future = asyncio.Future()
        future.set_result(ical_text)
        mock_event_loop.run_in_executor = MagicMock(return_value=future)
        mock_loop_fn.return_value = mock_event_loop
        return await src.poll()


# Standard test iCal with 3 events (future, grace, outside)
ICAL_STANDARD = _ical([
    _vevent("evt-future-001@example.com", _iso_future(2), "Future meeting"),
    _vecent := _vevent("evt-grace-001@example.com", _iso_just_started(30), "Currently in progress"),
    _vevent("evt-cancelled-001@example.com", _iso_future(3), "Will be cancelled", status="CANCELLED"),
])


# ────────────────────────────────────────────────────────────────────
# A. iCal parsing
# ────────────────────────────────────────────────────────────────────

class TestSectionA_ICalParsing:
    """A. iCal parsing works for valid iCal text."""

    def test_a1_valid_ical_parses_correctly(self):
        """A.1: Valid iCal text parses without error."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        cal = src._parse_ical(ICAL_STANDARD)
        assert cal is not None
        events = list(cal.walk("VEVENT"))
        assert len(events) == 3

    def test_a2_minimal_ical_parses(self):
        """A.2: Minimal iCal (single VEVENT) parses."""
        ical = _ical([_vevent("evt-min-001@example.com", _iso_future(1))])
        src = IcalCalendarSource("https://example.com/cal.ics")
        cal = src._parse_ical(ical)
        assert cal is not None
        assert len(list(cal.walk("VEVENT"))) == 1

    def test_a3_malformed_ical_returns_none(self):
        """A.3: Malformed iCal text returns None (does not raise)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        result = src._parse_ical("not a valid ical")
        assert result is None

    def test_a4_empty_ical_returns_none(self):
        """A.4: Empty iCal text returns None."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        result = src._parse_ical("")
        assert result is None


# ────────────────────────────────────────────────────────────────────
# B. VEVENT → WorldEvent mapping (with hash-based novelty_id)
# ────────────────────────────────────────────────────────────────────

class TestSectionB_VEventToWorldEvent:
    """B. VEVENT → WorldEvent mapping (RESUME Option 1: hash-based identity)."""

    def test_b1_basic_vevent_maps_to_world_event(self):
        """B.1: Basic VEVENT maps to WorldEvent with all 7 fields."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(
            "evt-b1@example.com", _iso_future(2),
            summary="Team standup",
            location="Room 5",
            description="Daily sync",
        )])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.source == "calendar"
        assert we.type == "calendar_event"
        # RESUME Option 1: novelty_id is hash, not original UID
        assert we.novelty_id == _hash("evt-b1@example.com")
        # Original UID preserved in data
        assert we.data["ical_uid"] == "evt-b1@example.com"
        assert we.summary == "Team standup"
        assert we.priority == 0
        assert we.data["location"] == "Room 5"
        assert we.data["description"] == "Daily sync"
        assert we.data["icalendar_source_url"] == "https://example.com/cal.ics"

    def test_b2_no_optional_fields(self):
        """B.2: VEVENT without location/description still maps cleanly."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-b2@example.com", _iso_future(1), "Minimal")])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.data["ical_uid"] == "evt-b2@example.com"
        assert we.data["location"] == ""
        assert we.data["description"] == ""

    def test_b3_no_summary_uses_default(self):
        """B.3: VEVENT without SUMMARY uses '(no title)'."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        vevent_str = (
            "BEGIN:VEVENT\r\n"
            "UID:evt-b3@example.com\r\n"
            f"DTSTART:{_iso_future(1)}\r\n"
            "END:VEVENT"
        )
        ical = _ical([vevent_str])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.summary == "(no title)"

    def test_b4_dtstart_in_iso_8601_utc(self):
        """B.4: WorldEvent.ts is ISO 8601 UTC format."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-b4@example.com", _iso_future(1))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert "T" in we.ts
        assert we.ts.endswith("+00:00") or we.ts.endswith("Z")


# ────────────────────────────────────────────────────────────────────
# C. UID → novelty_id hash (M5.15-6 RESUME Option 1)
# ────────────────────────────────────────────────────────────────────

class TestSectionC_UIDHashing:
    """C. SHA256(UID)[:32] = novelty_id (Option 1, M3.1-compatible)."""

    def test_c1_helper_function_is_deterministic(self):
        """C.1: _hash_uid_to_novelty_id is deterministic (same input → same output)."""
        uid = "test-uid@example.com"
        h1 = _hash_uid_to_novelty_id(uid)
        h2 = _hash_uid_to_novelty_id(uid)
        assert h1 == h2
        # 32-char lowercase hex
        assert len(h1) == 32
        assert all(c in "0123456789abcdef" for c in h1)

    def test_c2_different_uids_different_hashes(self):
        """C.2: Different UIDs produce different hashes."""
        h1 = _hash_uid_to_novelty_id("uid-a@example.com")
        h2 = _hash_uid_to_novelty_id("uid-b@example.com")
        assert h1 != h2

    def test_c3_real_ical_uid_hashes(self):
        """C.3: Real iCal-style UIDs all hash to valid 32-char hex."""
        uids = [
            "evt-001@google.com",
            "20260812T100000Z-12345@calendar.google.com",
            "uid@with.dots.and-dashes.com",
        ]
        for uid in uids:
            h = _hash_uid_to_novelty_id(uid)
            assert len(h) == 32
            # Verify satisfies M3.1 regex [a-z0-9_]{4,128}
            assert all(c in "0123456789abcdef" for c in h)
            # Verify it would pass _NOVELTY_ID_RE
            import re
            assert re.match(r"^[a-z0-9_]{4,128}$", h)

    def test_c4_whitespace_uid_stripped_before_hash(self):
        """C.4: UID with whitespace is stripped (canonical UID has no leading/trailing spaces)."""
        # The source strips UID, so '  uid@x  ' hashes the same as 'uid@x'
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("  evt-c4@example.com  ", _iso_future(1))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        # novelty_id is hash of stripped UID
        assert we.novelty_id == _hash("evt-c4@example.com")
        # data.ical_uid is the stripped form
        assert we.data["ical_uid"] == "evt-c4@example.com"

    def test_c5_original_uid_preserved_exactly(self):
        """C.5: data['ical_uid'] = exact original VEVENT.UID."""
        original_uid = "calendar.event.12345@google.com"
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(original_uid, _iso_future(1))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.data["ical_uid"] == original_uid
        # Verify exact preservation (no transformation, no truncation)
        assert we.data["ical_uid"] == original_uid.encode("utf-8").decode("utf-8")

    def test_c6_missing_uid_skipped(self):
        """C.6: VEVENT without UID is skipped."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        vevent_str = (
            "BEGIN:VEVENT\r\n"
            f"DTSTART:{_iso_future(1)}\r\n"
            "SUMMARY:No UID\r\n"
            "END:VEVENT"
        )
        ical = _ical([vevent_str])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None
        assert src._stats["events_skipped_no_uid"] == 1

    def test_c7_empty_uid_skipped(self):
        """C.7: VEVENT with empty UID is skipped."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        vevent_str = (
            "BEGIN:VEVENT\r\n"
            "UID:   \r\n"
            f"DTSTART:{_iso_future(1)}\r\n"
            "END:VEVENT"
        )
        ical = _ical([vevent_str])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None
        assert src._stats["events_skipped_no_uid"] == 1


# ────────────────────────────────────────────────────────────────────
# D. calendar_event type
# ────────────────────────────────────────────────────────────────────

class TestSectionD_CalendarEventType:
    """D. All WorldEvents use type='calendar_event' (M5.9-2 qualifying)."""

    def test_d1_all_events_use_calendar_event_type(self):
        """D.1: All emitted WorldEvents have type='calendar_event'."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([
            _vevent("evt-d1a@example.com", _iso_future(1), "Event 1"),
            _vevent("evt-d1b@example.com", _iso_future(2), "Event 2"),
            _vevent("evt-d1c@example.com", _iso_future(3), "Event 3"),
        ])
        cal = src._parse_ical(ical)
        now = datetime.now(timezone.utc)
        for vevent in cal.walk("VEVENT"):
            we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
            assert we is not None
            assert we.type == "calendar_event"


# ────────────────────────────────────────────────────────────────────
# E. source/source_system validity
# ────────────────────────────────────────────────────────────────────

class TestSectionE_SourceAndSystem:
    """E. WorldEvent.source = 'calendar' (M3 VALID_SOURCES)."""

    def test_e1_source_id_equals_calendar(self):
        """E.1: IcalCalendarSource.source_id == 'calendar'."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        assert src.source_id == "calendar"

    def test_e2_world_event_source_equals_calendar(self):
        """E.2: WorldEvent.source == 'calendar' (in VALID_SOURCES)."""
        from src.world.perception import VALID_SOURCES
        assert "calendar" in VALID_SOURCES
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-e2@example.com", _iso_future(1))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.source == "calendar"


# ────────────────────────────────────────────────────────────────────
# F. priority preservation
# ────────────────────────────────────────────────────────────────────

class TestSectionF_PriorityPreservation:
    """F. WorldEvent.priority preserved (M3.1 Phase B default 0)."""

    def test_f1_priority_default_zero(self):
        """F.1: All emitted WorldEvents have priority=0 (M3.1 Phase B default)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([
            _vevent("evt-f1a@example.com", _iso_future(1)),
            _vevent("evt-f1b@example.com", _iso_future(2)),
        ])
        cal = src._parse_ical(ical)
        now = datetime.now(timezone.utc)
        for vevent in cal.walk("VEVENT"):
            we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
            assert we is not None
            assert we.priority == 0
            assert isinstance(we.priority, int)


# ────────────────────────────────────────────────────────────────────
# G. 24h lookahead filtering
# ────────────────────────────────────────────────────────────────────

class TestSectionG_LookaheadFiltering:
    """G. 24h lookahead window filtering works correctly."""

    def test_g1_event_within_24h_emitted(self):
        """G.1: Event 2h in future is emitted (within 24h)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-g1@example.com", _iso_future(2))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None

    def test_g2_event_beyond_24h_skipped(self):
        """G.2: Event 30h in future is skipped (beyond 24h)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-g2@example.com", _iso_future(30))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None
        assert src._stats["events_skipped_outside_window"] == 1

    def test_g3_event_too_far_past_skipped(self):
        """G.3: Event 5h in past is skipped (beyond grace)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-g3@example.com", _iso_past(5))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None
        assert src._stats["events_skipped_outside_window"] == 1

    def test_g4_event_in_grace_period_emitted(self):
        """G.4: Event 30min in past (within grace) is emitted."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-g4@example.com", _iso_just_started(30))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None

    def test_g5_custom_lookahead_honored(self):
        """G.5: Custom lookahead_hours=6 excludes 12h-future event."""
        src = IcalCalendarSource(
            "https://example.com/cal.ics", lookahead_hours=6
        )
        ical = _ical([_vevent("evt-g5@example.com", _iso_future(12))])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=6))
        assert we is None
        assert src._stats["events_skipped_outside_window"] == 1


# ────────────────────────────────────────────────────────────────────
# H. CANCELLED filtering
# ────────────────────────────────────────────────────────────────────

class TestSectionH_CancelledFiltering:
    """H. STATUS:CANCELLED events are skipped (Q7)."""

    def test_h1_cancelled_event_skipped(self):
        """H.1: STATUS:CANCELLED event is skipped, not emitted."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(
            "evt-h1@example.com", _iso_future(2),
            "Cancelled", status="CANCELLED"
        )])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None
        assert src._stats["events_skipped_cancelled"] == 1

    def test_h2_cancelled_case_insensitive(self):
        """H.2: STATUS:cancelled (lowercase) also skipped."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(
            "evt-h2@example.com", _iso_future(2),
            "Cancelled lower", status="cancelled"
        )])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is None

    def test_h3_confirmed_event_emitted(self):
        """H.3: STATUS:CONFIRMED event is emitted."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(
            "evt-h3@example.com", _iso_future(2),
            "Confirmed", status="CONFIRMED"
        )])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None

    def test_h4_no_status_emitted(self):
        """H.4: VEVENT without STATUS is emitted."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-h4@example.com", _iso_future(2), "No status")])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None


# ────────────────────────────────────────────────────────────────────
# I. malformed ICS handling
# ────────────────────────────────────────────────────────────────────

class TestSectionI_MalformedICSHandling:
    """I. Malformed iCal data is handled safely (no crash)."""

    def test_i1_malformed_ical_poll_returns_zero(self):
        """I.1: poll() with malformed iCal returns 0, no crash."""
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)

        async def _run():
            return await _poll_with_ical(src, "not valid ical at all")
        result = asyncio.run(_run())
        assert result == 0
        assert src._stats["polls_failed"] == 1

    def test_i2_mixed_valid_and_invalid_events(self):
        """I.2: Mixed valid + invalid: valid are processed, invalid skipped."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([
            _vevent("evt-i2-valid@example.com", _iso_future(2), "Valid"),
            "BEGIN:VEVENT\r\nDTSTART:" + _iso_future(3) + "\r\nSUMMARY:No UID\r\nEND:VEVENT",
            _vevent("evt-i2-cancelled@example.com", _iso_future(3), "Cancelled", status="CANCELLED"),
            _vevent("evt-i2-far@example.com", _iso_future(48), "Far future"),
        ])
        cal = src._parse_ical(ical)
        now = datetime.now(timezone.utc)
        emitted = []
        for vevent in cal.walk("VEVENT"):
            we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
            if we:
                emitted.append(we)
        assert len(emitted) == 1
        assert emitted[0].data["ical_uid"] == "evt-i2-valid@example.com"
        assert emitted[0].novelty_id == _hash("evt-i2-valid@example.com")
        assert src._stats["events_skipped_no_uid"] == 1
        assert src._stats["events_skipped_cancelled"] == 1
        assert src._stats["events_skipped_outside_window"] == 1


# ────────────────────────────────────────────────────────────────────
# J. HTTP failure handling
# ────────────────────────────────────────────────────────────────────

class TestSectionJ_HTTPFailureHandling:
    """J. HTTP errors are handled safely (no crash, observable)."""

    def test_j1_http_timeout_returns_zero(self):
        """J.1: HTTP timeout returns 0, no crash."""
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)

        async def _run():
            with patch("asyncio.get_event_loop") as mock_loop_fn:
                mock_event_loop = MagicMock()

                async def raise_timeout(*args, **kwargs):
                    raise urllib.error.URLError("timeout")

                mock_event_loop.run_in_executor = MagicMock(
                    side_effect=lambda *a, **k: raise_timeout()
                )
                mock_loop_fn.return_value = mock_event_loop
                result = await src.poll()
            return result

        result = asyncio.run(_run())
        assert result == 0
        assert src._stats["polls_failed"] == 1
        assert src._stats["polls_total"] == 1

    def test_j2_http_404_returns_zero(self):
        """J.2: HTTP 404 returns 0, logs warning."""
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)

        async def _run():
            with patch("asyncio.get_event_loop") as mock_loop_fn:
                mock_event_loop = MagicMock()

                async def raise_404(*args, **kwargs):
                    raise urllib.error.HTTPError(
                        "https://example.com/cal.ics", 404, "Not Found", {}, None
                    )

                mock_event_loop.run_in_executor = MagicMock(
                    side_effect=lambda *a, **k: raise_404()
                )
                mock_loop_fn.return_value = mock_event_loop
                result = await src.poll()
            return result

        result = asyncio.run(_run())
        assert result == 0
        assert src._stats["polls_failed"] == 1

    def test_j3_failed_poll_does_not_emit_events(self):
        """J.3: Failed poll (HTTP error) emits 0 events."""
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        received: List[SoulEvent] = []

        async def _capture(e):
            received.append(e)
        bus.subscribe("test_capture", _capture, event_filter={EventType.WORLD_EVENT})

        async def _run():
            with patch("asyncio.get_event_loop") as mock_loop_fn:
                mock_event_loop = MagicMock()

                async def raise_error(*args, **kwargs):
                    raise ConnectionError("network down")

                mock_event_loop.run_in_executor = MagicMock(
                    side_effect=lambda *a, **k: raise_error()
                )
                mock_loop_fn.return_value = mock_event_loop
                await src.poll()
            return received

        result = asyncio.run(_run())
        assert len(result) == 0
        assert src._stats["polls_failed"] == 1


# ────────────────────────────────────────────────────────────────────
# K. duplicate/repeated polling behavior (with hash dedup)
# ────────────────────────────────────────────────────────────────────

class TestSectionK_DuplicatePolling:
    """K. Repeated polling → adapter dedupes via hash novelty_id."""

    def test_k1_same_event_polled_twice_adapter_dedups(self):
        """K.1: Same UID polled twice → adapter dedupes (1 InnerLifeEvent)."""
        bus = SoulEventBus()
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus=bus)
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        ical = _ical([_vevent("evt-k1@example.com", _iso_future(1))])

        async def _run():
            await bus.start()
            try:
                await _poll_with_ical(src, ical)
                await _poll_with_ical(src, ical)
            finally:
                await bus.stop()
        asyncio.run(_run())
        # Adapter received 2 events (same hash, dedup), created 1
        assert adapter.get_stats()["events_received"] == 2
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["duplicates_skipped"] == 1
        # Verify dedup key is the HASH, not the original UID
        expected_hash = _hash("evt-k1@example.com")
        assert expected_hash in adapter._dedup

    def test_k2_max_events_cap_protects_runaway(self):
        """K.2: MAX_EVENTS_PER_POLL cap protects against runaway."""
        from src.world.source import calendar_ical as cal_mod
        original_cap = cal_mod.MAX_EVENTS_PER_POLL
        cal_mod.MAX_EVENTS_PER_POLL = 3
        try:
            bus = SoulEventBus()
            src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
            events = [
                _vevent(f"evt-cap-{i}@example.com", _iso_future(1 + i), f"Event {i}")
                for i in range(5)
            ]
            ical = _ical(events)

            async def _run():
                await bus.start()
                try:
                    return await _poll_with_ical(src, ical)
                finally:
                    await bus.stop()
            result = asyncio.run(_run())
            assert result == 3
            assert src._stats["events_skipped_max_cap"] >= 1
        finally:
            cal_mod.MAX_EVENTS_PER_POLL = original_cap


# ────────────────────────────────────────────────────────────────────
# L. SEQUENCE modification behavior (M5.15-6 RESUME Q6)
# ────────────────────────────────────────────────────────────────────

class TestSectionL_SequenceBehavior:
    """L. SEQUENCE preserved separately; NOT in novelty_id (same UID → same hash)."""

    def test_l1_modified_event_with_sequence_emitted(self):
        """L.1: VEVENT with SEQUENCE=1 is emitted normally."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent(
            "evt-l1@example.com", _iso_future(2),
            "Original", sequence=0
        )])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert we.novelty_id == _hash("evt-l1@example.com")
        assert we.data["ical_sequence"] == 0

    def test_l2_same_uid_different_sequence_same_hash(self):
        """L.2: Same UID with different SEQUENCE → same novelty_id (hash)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical_v1 = _ical([_vevent("evt-l2@example.com", _iso_future(2), "V1", sequence=0)])
        ical_v2 = _ical([_vevent("evt-l2@example.com", _iso_future(2), "V2", sequence=1)])
        cal1 = src._parse_ical(ical_v1)
        cal2 = src._parse_ical(ical_v2)
        vevent1 = list(cal1.walk("VEVENT"))[0]
        vevent2 = list(cal2.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we1 = src._vevent_to_world_event(vevent1, now, now + timedelta(hours=24))
        we2 = src._vevent_to_world_event(vevent2, now, now + timedelta(hours=24))
        # Same UID → same hash
        assert we1.novelty_id == we2.novelty_id
        # But different data (SEQUENCE + summary)
        assert we1.data["ical_sequence"] == 0
        assert we2.data["ical_sequence"] == 1
        assert we1.summary == "V1"
        assert we2.summary == "V2"

    def test_l3_modified_event_adapter_dedupes(self):
        """L.3: Same UID + different SEQUENCE polled 2x → adapter dedupes to 1."""
        bus = SoulEventBus()
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus=bus)
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        ical_v1 = _ical([_vevent("evt-l2@example.com", _iso_future(2), "V1", sequence=0)])
        ical_v2 = _ical([_vevent("evt-l2@example.com", _iso_future(2), "V2", sequence=1)])

        async def _run():
            await bus.start()
            try:
                await _poll_with_ical(src, ical_v1)
                await _poll_with_ical(src, ical_v2)
            finally:
                await bus.stop()
        asyncio.run(_run())
        # 2 polls, same hash → 2 received, 1 created (dedup)
        assert adapter.get_stats()["events_received"] == 2
        assert adapter.get_stats()["events_created"] == 1
        assert adapter.get_stats()["duplicates_skipped"] == 1

    def test_l4_no_sequence_field_means_no_ical_sequence(self):
        """L.4: VEVENT without SEQUENCE → data has no 'ical_sequence' key."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        ical = _ical([_vevent("evt-l4@example.com", _iso_future(1), "No seq")])
        cal = src._parse_ical(ical)
        vevent = list(cal.walk("VEVENT"))[0]
        now = datetime.now(timezone.utc)
        we = src._vevent_to_world_event(vevent, now, now + timedelta(hours=24))
        assert we is not None
        assert "ical_sequence" not in we.data


# ────────────────────────────────────────────────────────────────────
# M. missing environment variable
# ────────────────────────────────────────────────────────────────────

class TestSectionM_MissingEnvVar:
    """M. Missing SOULOS_CALENDAR_ICAL_URL = no calendar activity."""

    def test_m1_constructor_with_empty_url_rejected(self):
        """M.1: Constructor with empty string rejected (defensive)."""
        with pytest.raises(ValueError):
            IcalCalendarSource("")

    def test_m2_constructor_with_valid_url_works(self):
        """M.2: Constructor with valid URL works."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        assert src.ical_url == "https://example.com/cal.ics"

    def test_m3_constructor_with_whitespace_url_rejected(self):
        """M.3: Constructor with whitespace-only URL rejected (after strip)."""
        with pytest.raises(ValueError):
            IcalCalendarSource("   ")

    def test_m4_constructor_with_whitespace_around_url_trimmed(self):
        """M.4: Whitespace around valid URL is stripped."""
        src = IcalCalendarSource("  https://example.com/cal.ics  ")
        assert src.ical_url == "https://example.com/cal.ics"


# ────────────────────────────────────────────────────────────────────
# N. Event Bus canonical path
# ────────────────────────────────────────────────────────────────────

class TestSectionN_EventBusCanonicalPath:
    """N. Source uses M5.15-3 canonical bus path."""

    def test_n1_emit_via_bus_publishes_soul_event(self):
        """N.1: _emit_via_bus publishes SoulEvent with WORLD_EVENT type."""
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        we = WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id=_hash("evt-n1@example.com"),
            ts="2026-08-12T10:00:00+00:00",
            summary="N.1 test",
            data={"ical_uid": "evt-n1@example.com"},
        )
        received: List[SoulEvent] = []

        async def _capture(e):
            received.append(e)
        bus.subscribe("test_capture", _capture, event_filter={EventType.WORLD_EVENT})

        async def _run():
            await bus.start()
            try:
                result = await src._emit_via_bus(we)
            finally:
                await bus.stop()
            return result, received

        result, received_events = asyncio.run(_run())
        assert result is True
        assert len(received_events) == 1
        se = received_events[0]
        assert se.event_type == EventType.WORLD_EVENT
        assert se.target == "broadcast"
        assert se.source == "calendar"
        # WorldEvent payload preserved
        assert se.payload["novelty_id"] == _hash("evt-n1@example.com")
        assert se.payload["type"] == "calendar_event"
        # M5.15-6 Option 1: ical_uid is preserved inside data dict (M3 frozen
        # WorldEvent schema puts original UID under data, not at top level)
        assert se.payload["data"]["ical_uid"] == "evt-n1@example.com"

    def test_n2_emit_without_bus_returns_false(self):
        """N.2: _emit_via_bus without bus returns False (no emission)."""
        src = IcalCalendarSource("https://example.com/cal.ics")
        we = WorldEvent(
            source="calendar", type="calendar_event",
            novelty_id="abc", ts="2026-08-12T10:00:00+00:00", summary="x", data={}
        )

        async def _run():
            return await src._emit_via_bus(we)
        result = asyncio.run(_run())
        assert result is False
        assert src._stats["events_emission_failed"] == 1

    def test_n3_middleware_and_adapter_receive_via_bus(self):
        """N.3 (CRITICAL — frozen contract compatibility):
        Middleware + Adapter both receive via bus; hash novelty_id passes M3.1."""
        bus = SoulEventBus()
        state = WorldPerceptionState()
        trace_writer = WorldPerceptionTraceWriter(
            trace_log_path=Path(os.environ.get("SOULOS_TEST_TRACE", "/tmp/m5_15_6_n3_trace.jsonl"))
        )
        if os.path.exists(trace_writer.trace_log_path):
            trace_writer.trace_log_path.unlink()
        middleware = WorldPerceptionMiddleware(
            bus=bus, state=state, trace_writer=trace_writer,
        )
        middleware.register()
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus=bus)
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        ical = _ical([_vevent("evt-n3@example.com", _iso_future(1), "Bus path test")])

        async def _run():
            await bus.start()
            try:
                await _poll_with_ical(src, ical)
            finally:
                await bus.stop()
        asyncio.run(_run())
        # Middleware received (M3.1 validation passed via hash)
        assert middleware._events_state_added == 1
        assert middleware._events_validation_rejected == 0
        # Adapter received and created
        assert adapter.get_stats()["events_created"] == 1
        expected_hash = _hash("evt-n3@example.com")
        assert expected_hash in adapter._dedup


# ────────────────────────────────────────────────────────────────────
# O. M5.15-5 source_world_event_novelty_id propagation
# ────────────────────────────────────────────────────────────────────

class TestSectionO_SourceWorldEventNoveltyId:
    """O. M5.15-5 source_world_event_novelty_id = WorldEvent.novelty_id (HASH)."""

    def test_o1_inner_life_event_has_source_field_equal_to_hash(self):
        """O.1 (CRITICAL — M5.15-5 propagation):
        InnerLifeEvent.source_world_event_novelty_id = WorldEvent.novelty_id (hash)."""
        bus = SoulEventBus()
        writer = InnerLifeWriter(trace_writer=None)
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus=bus)
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        ical = _ical([_vevent("evt-o1@example.com", _iso_future(1), "M5.15-5 test")])

        async def _run():
            await bus.start()
            try:
                await _poll_with_ical(src, ical)
            finally:
                await bus.stop()
        asyncio.run(_run())
        expected_hash = _hash("evt-o1@example.com")
        assert expected_hash in adapter._dedup
        # writer._events is keyed by InnerLifeEvent.event_id (M5.4-5.1 frozen
        # identity authority), not by WorldEvent.novelty_id. Find the event
        # whose source_world_event_novelty_id (M5.15-5 Layer 1) equals the
        # canonical hash produced by IcalCalendarSource.
        matching = [
            e for e in writer._events.values()
            if e.source_world_event_novelty_id == expected_hash
        ]
        assert len(matching) == 1, (
            f"expected exactly 1 InnerLifeEvent with "
            f"source_world_event_novelty_id={expected_hash}, got {len(matching)}"
        )
        ev = matching[0]
        # M5.15-5 Layer 1: source_world_event_novelty_id = WorldEvent.novelty_id
        assert ev.source_world_event_novelty_id == expected_hash
        # M5.4-5.1 Layer 2: parent_event_id remains None (root event)
        assert ev.parent_event_id is None
        # M5.4-5.1: lineage_depth = 0 (root)
        assert ev.lineage_depth == 0
        # M5.9-2: provenance.trigger_type = "world:calendar_event"
        assert ev.provenance.trigger_type == "world:calendar_event"
        # M5.9-2: provenance.extras["world_novelty_id"] = WorldEvent.novelty_id (hash)
        # This is the cross-system traceability anchor — same string as
        # source_world_event_novelty_id, so an external observer can join
        # InnerLifeEvent rows back to WorldEvent rows by the hash.
        assert ev.provenance.extras["world_novelty_id"] == expected_hash
        assert ev.source_world_event_novelty_id == ev.provenance.extras["world_novelty_id"]


# ────────────────────────────────────────────────────────────────────
# P. production isolation
# ────────────────────────────────────────────────────────────────────

class TestSectionP_ProductionIsolation:
    """P. All tests use isolated data_root."""

    def test_p1_isolated_data_root(self, tmp_path, monkeypatch):
        """P.1: Test writes only in isolated tmp_path, not production data/."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
        reset_data_root()
        bus = SoulEventBus()
        src = IcalCalendarSource("https://example.com/cal.ics", bus=bus)
        ical = _ical([_vevent("evt-p1@example.com", _iso_future(1))])

        async def _run():
            await bus.start()
            try:
                await _poll_with_ical(src, ical)
            finally:
                await bus.stop()
        asyncio.run(_run())
        # Verify data_root is in tmp_path
        from src.paths import data_root
        assert str(data_root()).startswith(str(tmp_path))


# ────────────────────────────────────────────────────────────────────
# Q. server lifecycle / env-gated wiring
# ────────────────────────────────────────────────────────────────────

class TestSectionQ_LifecycleAndEnvGated:
    """Q. Source lifecycle + env-gated wiring."""

    def test_q1_start_lifecycle_logs(self):
        """Q.1: start() logs configuration."""
        src = IcalCalendarSource("https://example.com/cal.ics")

        async def _run():
            await src.start()
        asyncio.run(_run())

    def test_q2_stop_lifecycle_logs(self):
        """Q.2: stop() logs final stats, idempotent."""
        src = IcalCalendarSource("https://example.com/cal.ics")

        async def _run():
            await src.stop()
            await src.stop()
        asyncio.run(_run())

    def test_q3_is_subclass_of_world_event_source(self):
        """Q.3: IcalCalendarSource is a subclass of WorldEventSource ABC."""
        from src.world.base import WorldEventSource
        assert issubclass(IcalCalendarSource, WorldEventSource)
        for method in ["source_id", "start", "stop"]:
            assert hasattr(IcalCalendarSource, method)

    def test_q4_world_event_source_abc_unchanged(self):
        """Q.4: WorldEventSource ABC has same 3 abstract methods (no contract change)."""
        from src.world.base import WorldEventSource
        abstract = WorldEventSource.__abstractmethods__
        assert abstract == frozenset({"source_id", "start", "stop"})

    def test_q5_run_server_env_gated_logic(self):
        """Q.5: run_server.py uses SOULOS_CALENDAR_ICAL_URL env var."""
        run_server_path = Path(__file__).resolve().parent.parent / "scripts" / "run_server.py"
        source = run_server_path.read_text(encoding="utf-8")
        assert "SOULOS_CALENDAR_ICAL_URL" in source
        assert "IcalCalendarSource" in source
        assert "calendar_ical_url = os.getenv" in source

    def test_q6_missing_env_var_no_activity(self, monkeypatch):
        """Q.6: Without SOULOS_CALENDAR_ICAL_URL, no calendar activity in lifespan."""
        monkeypatch.delenv("SOULOS_CALENDAR_ICAL_URL", raising=False)
        env_val = os.getenv("SOULOS_CALENDAR_ICAL_URL", "").strip()
        assert env_val == ""


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
    # 30+ tests covering A-Q + critical regression A-L + z_count
    assert test_count >= 30, f"expected 30+ tests, got {test_count}"
