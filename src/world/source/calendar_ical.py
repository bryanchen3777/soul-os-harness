"""
src/world/source/calendar_ical.py — Soul OS M5.15-6 Real-World Calendar Source

M5.15-6 (Bry 派工 2026-08-12 19:29, RESUME 19:37 Option 1):
Real-World Calendar Source Integration.
Mode: MINIMAL ADDITIVE / IMPLEMENTATION

Calendar iCal/ICS public feed → WorldEventSource → Event Bus → WorldPerception
→ WorldInnerLifeAdapter → InnerLifeEvent (with source_world_event_novelty_id).

派工精神:
  - First real-world WorldEventSource (F2 from M5.15-1 audit resolved)
  - Public iCal URL (no OAuth, no credentials, no token store)
  - Env-gated via SOULOS_CALENDAR_ICAL_URL (M5.15-3 smoke test pattern)
  - 0 frozen contract change (M3 WorldEvent / M3.1 WorldEventSource ABC /
    M3.1 WorldEventInjector Protocol / M3.1 Event Bus / M5.4-5.1 InnerLifeEvent /
    M5.4-5.1 parent_event_id / M5.4-5.1 lineage / M5.9-2 WORLD_QUALIFYING_TYPES /
    M5.15-3 canonical bus rule / M5.15-5 source_world_event_novelty_id all 0 change)
  - 1 new WorldEventSource subclass (this file)
  - Library: icalendar (PyPI, MIT, mature, stdlib urllib for HTTP GET)

Why calendar (Q1, Q2):
  - "calendar_event" already in WORLD_QUALIFYING_TYPES (M5.9-2 frozen)
  - iCal public feed = HTTP GET (no OAuth, no token store, no new infra)
  - Direct user value: Soul OS uses "you have a meeting in 30 min" to drive conversation
  - User controls publication (Google Calendar "Make available to public" or iCal URL)

Identity (M5.15-6 RESUME Option 1 — Bry authorization 2026-08-12 19:37):
  - novelty_id = SHA256(VEVENT.UID)[:32]  (32-char lowercase hex)
    - Deterministic, 128-bit collision space
    - Compatible with M3.1 validation `[a-z0-9_]{4,128}`
    - Same UID → same hash (Q1, Q5, Q6 — SEQUENCE excluded from hash)
  - data["ical_uid"] = VEVENT.UID (exact original, preserved for traceability)
  - data["ical_sequence"] = VEVENT.SEQUENCE (if present, observability only, not in identity)
  - Cross-handler identity bridge via M5.15-5 source_world_event_novelty_id

Why hash (M3.1 frozen contract reason):
  - M3.1 validation.py:34 _NOVELTY_ID_RE = re.compile(r"^[a-z0-9_]{4,128}$")
  - Real iCal UIDs contain @, ., - (RFC 5545) which fail M3.1 regex
  - SHA256[:32] maps arbitrary iCal UID → M3.1-compatible 32-hex string
  - Original UID preserved in data["ical_uid"] for full traceability

Temporal:
  - Lookahead window: 24h default (Q4)
  - Polling interval: 300s default (Q3, configurable)
  - RRULE: parent-only v1 (Q5, no recurrence engine)
  - CANCELLED: skip STATUS:CANCELLED in v1 (Q7)
  - SEQUENCE: re-emit (adapter dedupes by hash novelty_id, M5.9-3)

Failure handling (per work order §13):
  - HTTP error → log warning, skip poll, retry next interval
  - Parse error → log error, skip malformed VEVENT, continue
  - Missing UID → log error, skip VEVENT
  - All errors observable, never silent, never crash
  - Production safe (urllib timeout, no thread/process spawn)

Out of scope (per work order):
  - OAuth (Q2 explicitly rejects)
  - Google Calendar API, Microsoft Graph
  - Webhook infrastructure
  - Multi-calendar orchestration
  - Recurrence engine
  - New scheduler subsystem (uses asyncio.create_task in lifespan)
  - New WorldEvent type
  - Modifying frozen downstream contracts
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from icalendar import Calendar, Event

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

from ..base import WorldEventSource
from ..perception import WorldEvent

logger = logging.getLogger("soul_os.world.source.calendar")


# ───────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────

# Q3 default: polling interval 5 minutes
DEFAULT_POLLING_INTERVAL_SECS = 300

# Q4 default: lookahead horizon 24 hours
DEFAULT_LOOKAHEAD_HOURS = 24

# HTTP timeout: 30s (per work order §production safety)
HTTP_TIMEOUT_SECS = 30.0

# Max events per poll (defensive cap to prevent runaway)
MAX_EVENTS_PER_POLL = 500

# Grace period: include events that started up to 1h ago
GRACE_HOURS = 1


def _hash_uid_to_novelty_id(uid: str) -> str:
    """
    M5.15-6 (Bry RESUME 2026-08-12 19:37 — Option 1): Map VEVENT.UID to
    M3.1-compatible novelty_id via SHA256.

    Required identity model (per Owner authorization):
      - VEVENT.UID (exact original) → WorldEvent.data["ical_uid"]
      - SHA256(VEVENT.UID)[:32] → WorldEvent.novelty_id

    Properties:
      - Deterministic: same UID always produces same hash
      - 32-char lowercase hex (matches M3.1 validation `[a-z0-9_]{4,128}`)
      - No timestamp / randomness / SEQUENCE mixed in
      - 128-bit collision space (effectively unique for all practical iCal feeds)

    Original UID is preserved in data["ical_uid"] for traceability.
    """
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:32]


class IcalCalendarSource(WorldEventSource):
    """
    M5.15-6 (Bry 派工 2026-08-12 19:29, RESUME 19:37 Option 1):
    Real-world WorldEventSource for calendar iCal/ICS public feeds.

    Polling-driven, env-gated, no OAuth, no credentials.

    Identity model (RESUME Option 1):
      WorldEvent.novelty_id = SHA256(VEVENT.UID)[:32]
      WorldEvent.data["ical_uid"] = VEVENT.UID (exact original)
      WorldEvent.data["ical_sequence"] = VEVENT.SEQUENCE (if present, observability only)

    Lifecycle:
      1. __init__: configure ical_url, bus, polling_interval, lookahead_hours
      2. start(): log + initialize (no-op for v1)
      3. (external) await poll(): called by lifespan or scheduler task
      4. stop(): log + cleanup (no-op for v1)

    Architecture decision (per M5.15-6 work order):
      - Polling model (not push/webhook) — no new infrastructure
      - Polling interval: 300s default (configurable via constructor)
      - Lookahead window: 24h default (configurable via constructor)
      - 1 URL = 1 source (Q9, Q10)
      - Env-gated via SOULOS_CALENDAR_ICAL_URL (Q8)
    """

    def __init__(
        self,
        ical_url: str,
        bus: Optional[SoulEventBus] = None,
        polling_interval_secs: int = DEFAULT_POLLING_INTERVAL_SECS,
        lookahead_hours: int = DEFAULT_LOOKAHEAD_HOURS,
        http_timeout_secs: float = HTTP_TIMEOUT_SECS,
    ) -> None:
        """
        Args:
            ical_url: public iCal/ICS URL (e.g., Google Calendar "Make available
                to public" URL, or any iCal feed)
            bus: SoulEventBus for M5.15-3 canonical bus path
            polling_interval_secs: how often to poll (default 300 = 5 min)
            lookahead_hours: forward-looking window (default 24h)
            http_timeout_secs: HTTP request timeout (default 30s)
        """
        # Defensive: strip URL and reject empty / whitespace-only
        if not isinstance(ical_url, str):
            raise ValueError(
                f"ical_url 必須是 str, got: {type(ical_url).__name__}"
            )
        ical_url = ical_url.strip()
        if not ical_url:
            raise ValueError(
                f"ical_url 必須是非空 str (stripped), got: {ical_url!r}"
            )
        if polling_interval_secs < 60:
            raise ValueError(
                f"polling_interval_secs 必須 ≥ 60 (避免過度 polling), got: {polling_interval_secs}"
            )
        if lookahead_hours < 1 or lookahead_hours > 168:  # max 7 days
            raise ValueError(
                f"lookahead_hours 必須在 1-168 之間, got: {lookahead_hours}"
            )
        self._ical_url: str = ical_url
        self._bus: Optional[SoulEventBus] = bus
        self._polling_interval_secs: int = polling_interval_secs
        self._lookahead_hours: int = lookahead_hours
        self._http_timeout_secs: float = http_timeout_secs
        # Observability counters
        self._stats = {
            "polls_total": 0,
            "polls_failed": 0,
            "events_emitted": 0,
            "events_skipped_no_uid": 0,
            "events_skipped_cancelled": 0,
            "events_skipped_outside_window": 0,
            "events_skipped_recurrence": 0,
            "events_skipped_no_dtstart": 0,
            "events_skipped_max_cap": 0,
            "events_emission_failed": 0,
        }

    @property
    def source_id(self) -> str:
        """M5.15-6 Q12: source_id = 'calendar' (matches VALID_SOURCES)."""
        return "calendar"

    @property
    def ical_url(self) -> str:
        """Public iCal/ICS URL (read-only)."""
        return self._ical_url

    @property
    def polling_interval_secs(self) -> int:
        """Polling interval in seconds (read-only)."""
        return self._polling_interval_secs

    @property
    def lookahead_hours(self) -> int:
        """Lookahead horizon in hours (read-only)."""
        return self._lookahead_hours

    # ── WorldEventSource ABC (M3.1 Phase A) ──────────────────

    async def start(self) -> None:
        """
        M3.1 Phase A: start lifecycle.

        For IcalCalendarSource, start is a no-op (the actual polling is
        driven by external scheduler calling poll()).

        Logs the configuration for observability.
        """
        logger.info(
            f"[IcalCalendarSource] start() — "
            f"ical_url=...{self._mask_url(self._ical_url)} "
            f"polling_interval={self._polling_interval_secs}s "
            f"lookahead={self._lookahead_hours}h "
            f"bus={'set' if self._bus is not None else 'None'}"
        )

    async def stop(self) -> None:
        """
        M3.1 Phase A: stop lifecycle. Idempotent.

        For IcalCalendarSource, stop is a no-op (the lifespan manages
        the polling task lifecycle; this method exists for ABC conformance).
        """
        logger.info(
            f"[IcalCalendarSource] stop() — "
            f"final stats: {self._stats}"
        )

    # ── Polling (M5.15-6 IMPLEMENTATION) ──────────────────────

    async def poll(self) -> int:
        """
        Poll the iCal feed, parse events, emit WorldEvents.

        Returns:
            int: number of WorldEvents emitted in this poll cycle.

        Failure handling (per work order §13):
          - HTTP error (timeout, 4xx, 5xx) → log + skip + return 0
          - Parse error (malformed iCal) → log + skip + return 0
          - Missing UID → skip individual VEVENT, continue
          - STATUS:CANCELLED → skip individual VEVENT
          - RRULE recurrence instances → skip (parent-only v1, Q5)
          - Outside lookahead window → skip
          - Max events cap → stop emitting at MAX_EVENTS_PER_POLL
          - All errors logged, never silent, never crash
        """
        self._stats["polls_total"] += 1

        # 1. HTTP GET
        ical_text = await self._fetch_ical()
        if ical_text is None:
            self._stats["polls_failed"] += 1
            return 0

        # 2. Parse iCal
        calendar = self._parse_ical(ical_text)
        if calendar is None:
            self._stats["polls_failed"] += 1
            return 0

        # 3. Compute lookahead window (now to now + lookahead)
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(hours=self._lookahead_hours)

        # 4. Walk VEVENTs
        emitted_count = 0
        for component in calendar.walk("VEVENT"):
            if emitted_count >= MAX_EVENTS_PER_POLL:
                self._stats["events_skipped_max_cap"] += 1
                logger.warning(
                    f"[IcalCalendarSource] 達到 max events cap "
                    f"({MAX_EVENTS_PER_POLL}), 停止 emit 剩餘 events"
                )
                break

            # 4a. Build WorldEvent (None if skipped)
            we = self._vevent_to_world_event(component, now, window_end)
            if we is None:
                continue  # already counted in stats (skip reason)

            # 4b. Emit via bus (M5.15-3 canonical path, async)
            if await self._emit_via_bus(we):
                emitted_count += 1
                self._stats["events_emitted"] += 1

        logger.info(
            f"[IcalCalendarSource] poll() — "
            f"emitted={emitted_count} "
            f"stats={self._stats}"
        )
        return emitted_count

    def get_stats(self) -> Dict[str, int]:
        """Observability counters snapshot."""
        return dict(self._stats)

    # ── Internal helpers ─────────────────────────────────────

    def _mask_url(self, url: str) -> str:
        """Mask URL for logging (hide query params / API keys)."""
        # Show first 30 chars + last 10 chars; full URL is preserved in
        # self._ical_url for actual HTTP request
        if len(url) <= 50:
            return url
        return f"{url[:30]}...{url[-10:]}"

    async def _fetch_ical(self) -> Optional[str]:
        """
        HTTP GET the iCal URL with timeout (run in executor to avoid blocking).

        Returns:
            str: iCal text content
            None: HTTP error (logged, return None, caller counts as failed poll)

        Production safety: timeout enforced, no credential leaks in URL
        (iCal public feed should not have credentials; defensive masking
        in _mask_url).
        """
        loop = asyncio.get_event_loop()
        try:
            def _do_fetch() -> str:
                req = urllib.request.Request(
                    self._ical_url,
                    headers={"User-Agent": "SoulOS/1.0 (M5.15-6 Calendar Source)"},
                )
                with urllib.request.urlopen(
                    req, timeout=self._http_timeout_secs
                ) as response:
                    raw = response.read()
                    return raw.decode("utf-8", errors="replace")
            return await loop.run_in_executor(None, _do_fetch)
        except urllib.error.URLError as e:
            logger.warning(
                f"[IcalCalendarSource] HTTP error for "
                f"...{self._mask_url(self._ical_url)}: "
                f"{type(e).__name__}: {e}"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[IcalCalendarSource] Unexpected fetch error: "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _parse_ical(self, ical_text: str) -> Optional[Calendar]:
        """
        Parse iCal text into Calendar object.

        Returns:
            Calendar: parsed calendar
            None: parse error (logged, return None)

        Defensive: icalendar library may raise on malformed input.
        """
        try:
            return Calendar.from_ical(ical_text)
        except Exception as e:
            logger.warning(
                f"[IcalCalendarSource] Parse error: "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _vevent_to_world_event(
        self,
        vevent: Event,
        now: datetime,
        window_end: datetime,
    ) -> Optional[WorldEvent]:
        """
        Convert a single VEVENT to WorldEvent.

        Returns:
            WorldEvent: if VEVENT is valid and in window
            None: if skipped (reason logged in stats)

        Skip rules (per work order):
          - No UID → skip (events_skipped_no_uid)
          - STATUS:CANCELLED → skip (events_skipped_cancelled)
          - Has RECURRENCE-ID (child instance) → skip (parent-only v1, Q5)
          - No DTSTART → skip (events_skipped_no_dtstart)
          - DTSTART outside [now - grace, now + lookahead] → skip
        """
        # Required: UID
        uid = vevent.get("UID")
        if not uid or not isinstance(uid, str) or not uid.strip():
            self._stats["events_skipped_no_uid"] += 1
            logger.debug(
                f"[IcalCalendarSource] VEVENT without UID, skip: "
                f"summary={vevent.get('SUMMARY')!r}"
            )
            return None
        uid = str(uid).strip()

        # Skip CANCELLED (Q7)
        status = vevent.get("STATUS")
        if status and str(status).upper() == "CANCELLED":
            self._stats["events_skipped_cancelled"] += 1
            logger.debug(
                f"[IcalCalendarSource] VEVENT CANCELLED, skip: uid={uid}"
            )
            return None

        # Skip RECURRENCE-ID children (Q5: parent-only v1)
        if vevent.get("RECURRENCE-ID") is not None:
            self._stats["events_skipped_recurrence"] += 1
            logger.debug(
                f"[IcalCalendarSource] VEVENT is recurrence child, skip: "
                f"uid={uid} (parent-only v1)"
            )
            return None

        # DTSTART (required for time-based filter)
        dtstart = vevent.get("DTSTART")
        if dtstart is None:
            self._stats["events_skipped_no_dtstart"] += 1
            logger.debug(
                f"[IcalCalendarSource] VEVENT without DTSTART, skip: uid={uid}"
            )
            return None

        # Normalize to datetime
        if hasattr(dtstart, "dt"):
            dtstart_dt = dtstart.dt
        else:
            dtstart_dt = dtstart

        # Ensure datetime (date → midnight UTC)
        if not isinstance(dtstart_dt, datetime):
            try:
                dtstart_dt = datetime.combine(
                    dtstart_dt, datetime.min.time(), tzinfo=timezone.utc
                )
            except (TypeError, ValueError):
                self._stats["events_skipped_no_dtstart"] += 1
                return None

        # Ensure UTC
        if dtstart_dt.tzinfo is None:
            dtstart_dt = dtstart_dt.replace(tzinfo=timezone.utc)
        else:
            dtstart_dt = dtstart_dt.astimezone(timezone.utc)

        # Filter by lookahead window (with grace period)
        grace = timedelta(hours=GRACE_HOURS)
        if dtstart_dt < (now - grace):
            self._stats["events_skipped_outside_window"] += 1
            return None
        if dtstart_dt > window_end:
            self._stats["events_skipped_outside_window"] += 1
            return None

        # DTEND (optional, for data field)
        dtend = vevent.get("DTEND")
        dtend_iso = None
        if dtend is not None:
            if hasattr(dtend, "dt"):
                dtend_dt = dtend.dt
            else:
                dtend_dt = dtend
            if isinstance(dtend_dt, datetime):
                if dtend_dt.tzinfo is None:
                    dtend_dt = dtend_dt.replace(tzinfo=timezone.utc)
                else:
                    dtend_dt = dtend_dt.astimezone(timezone.utc)
                dtend_iso = dtend_dt.isoformat()

        # Build data field
        data: Dict[str, Any] = {
            "start": dtstart_dt.isoformat(),
            "end": dtend_iso,
            "location": str(vevent.get("LOCATION", "")) or "",
            "description": str(vevent.get("DESCRIPTION", "")) or "",
            "icalendar_source_url": self._ical_url,
            # M5.15-6 Option 1 (Bry RESUME 19:37): preserve exact original UID
            # for traceability. 32-char hex hash is in novelty_id for M3.1
            # validation compatibility.
            "ical_uid": uid,
        }
        # M5.15-6 Option 1 Q6: SEQUENCE preserved separately for observability,
        # NOT part of identity (different SEQUENCE → same novelty_id via hash).
        sequence = vevent.get("SEQUENCE")
        if sequence is not None:
            try:
                data["ical_sequence"] = int(sequence)
            except (ValueError, TypeError):
                # Defensive: if SEQUENCE is not numeric, skip it
                logger.debug(
                    f"[IcalCalendarSource] VEVENT SEQUENCE not numeric, skip: "
                    f"uid={uid} sequence={sequence!r}"
                )
        # Optional fields (only if present)
        organizer = vevent.get("ORGANIZER")
        if organizer is not None:
            data["organizer"] = str(organizer)
        url_field = vevent.get("URL")
        if url_field is not None:
            data["url"] = str(url_field)
        if vevent.get("RRULE") is not None:
            # Indicate this is a recurring parent (v1: emit parent only)
            data["is_recurring_parent"] = True

        # M5.15-6 Option 1: novelty_id = SHA256(VEVENT.UID)[:32]
        # Deterministic, M3.1 validation compatible (32 hex).
        # Original UID preserved in data["ical_uid"] for traceability.
        # SEQUENCE excluded from hash (Q4: same UID + different SEQUENCE → same hash).
        novelty_id = _hash_uid_to_novelty_id(uid)

        # Build WorldEvent (M3 frozen contract: 7 fields)
        return WorldEvent(
            source=self.source_id,  # "calendar" (Q12)
            type="calendar_event",   # M5.9-2 QUALIFYING_TYPE
            novelty_id=novelty_id,   # SHA256(VEVENT.UID)[:32] (M5.15-6 Option 1)
            ts=dtstart_dt.isoformat(),  # ISO 8601 UTC
            summary=str(vevent.get("SUMMARY", "")) or "(no title)",
            data=data,
            priority=0,               # M3.1 Phase B default
        )

    async def _emit_via_bus(self, world_event: WorldEvent) -> bool:
        """
        Emit WorldEvent via M5.15-3 canonical bus path (async).

        Returns:
            bool: True if emitted, False if bus is None or emit failed.

        If self._bus is None, this is a configuration error (source should
        always be wired with bus in production). Log warning and return False
        (event not emitted, but no crash).
        """
        if self._bus is None:
            logger.warning(
                f"[IcalCalendarSource] bus is None, cannot emit: "
                f"novelty_id={world_event.novelty_id}"
            )
            self._stats["events_emission_failed"] += 1
            return False
        try:
            soul_event = SoulEvent(
                event_type=EventType.WORLD_EVENT,
                source=self.source_id,
                target="broadcast",
                priority=EventPriority.NORMAL,
                payload=world_event.to_payload(),
            )
            await self._bus.publish(soul_event)
            return True
        except Exception as e:
            logger.warning(
                f"[IcalCalendarSource] emit failed: "
                f"{type(e).__name__}: {e}"
            )
            self._stats["events_emission_failed"] += 1
            return False
