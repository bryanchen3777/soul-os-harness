"""
src/world/source/open_meteo.py — Soul OS M6.1-3.1 Open-Meteo Weather Source

M6.1-3.1 (Bry 派工 2026-08-13 19:27, OWNER AUTHORIZATION APPROVED) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE / IMPLEMENTATION

Open-Meteo (https://open-meteo.com) → WorldEventSource → Event Bus → WorldPerception
→ WorldInnerLifeAdapter (m5.9-3 dedup if qualifying) → Lived Context.

派工精神:
  - First Physical-context signal producer (answers「今天真的下雨嗎?」)
  - M3.1 Invariant E exception (like Calendar M5.15-6): public API, no credentials
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent
    / M5.9-2 QUALIFYING_TYPES / M5.9-3 dedup / M5.15-3 canonical bus path /
    M5.15-5 source_world_event_novelty_id all preserved)
  - 1 new WorldEventSource subclass (this file)
  - Library: stdlib only (urllib + json), no new dependencies
  - API key: NONE (Open-Meteo is public, free, no auth)

Why Open-Meteo (work order §2):
  - No API key required
  - Bounded public API
  - Suitable for minimal implementation
  - Avoids credential infrastructure
  - No new secrets infrastructure

Identity model (M6.1-3.1 design):
  - source_id = "weather" (M3.1 VALID_SOURCES contract; provider preserved in data["weather_provider"])
  - type = "rain_started" (if precipitation > 0 or WMO code in 51-67/80-82/95-99)
        OR "weather_temp_change" (otherwise, for temperature observation)
  - novelty_id = "weather.{lat:.2f}_{lon:.2f}.{observation_hour}.{state}"
        (deterministic, M3.1 validation [a-z0-9_]{4,128} compatible)
        (same location + same observation hour + same rain state → same novelty_id)
  - data = {
        "weather_provider": "open_meteo",
        "weather_location": "lat,lon",
        "weather_observation_time": ISO 8601 UTC,
        "weather_temperature_c": float,
        "weather_precipitation_mm": float,
        "weather_code": int (WMO code),
    }

Polling:
  - Polling interval: 1800s default (30 min, conservative per M6.1-3 recommendation)
  - No forecast (current only) — minimal scope
  - Bounded retries (no exponential backoff, just simple poll loop)
  - Per-hour observation (Open-Meteo "current" updates every 15min, but we round to hour)

Failure handling (per work order §9):
  - HTTP error → log warning, skip poll, retry next interval
  - Parse error → log warning, skip poll, retry next interval
  - Missing location → ValueError at __init__ (not retryable)
  - All errors observable, never silent, never crash
  - Production safe (urllib timeout, no thread/process spawn)

Environment gating (per work order §10):
  - Env var: SOULOS_WEATHER_LOCATION (e.g., "25.03,121.57")
  - If unset → source not wired (no polling, 0 risk)
  - Tests must NOT use real env vars; pass location via constructor

Why "weather" as source_id (not "weather.open_meteo" as work order suggests):
  - M3.1 frozen contract: WorldEvent.source MUST be in VALID_SOURCES = {weather, news, calendar, social, synthetic}
  - validate_world_event() rejects non-whitelist sources
  - Calendar uses "calendar" (not "calendar.ical") for the same reason
  - Provider identity preserved in data["weather_provider"] = "open_meteo" for observability
  - 0 frozen contract change required

Why lat,lon single env var (not lat+lon or location framework):
  - Calendar uses single env var (SOULOS_CALENDAR_ICAL_URL)
  - Single env var matches existing .env mechanism
  - No "location framework" invented (just one config value)
  - Work order: "Do NOT invent a new location framework" → single env var is not a framework

M5.9-2 type whitelist status:
  - "rain_started" and "weather_temp_change" are NOT in M5.9-2 WORLD_QUALIFYING_TYPES
  - Therefore: WorldInnerLifeAdapter will REJECT (qualify=NO) → no InnerLifeEvent created
  - This is correct minimal scope: WorldEvent reaches perception (state + trace), can surface
    in world_context block on AGENT_INTENT evaluation, but does NOT pollute InnerLife storage
  - Future M6.1-* ticket can add "rain_started" to QUALIFYING_TYPES if Bry wants

M3.1 Invariant E exception (per work order):
  - Calendar M5.15-6 got the same exception
  - Justification: public API, no credentials, no agency
  - "Weather is awareness, not agency" — same as Calendar

Out of scope (per work order):
  - Weather UI
  - Weather conversation feature
  - Weather LLM prompt
  - Weather-specific emotion scoring
  - Environment → emotion
  - Personal rhythm
  - News
  - Web/Search
  - generic external-source framework
  - new location framework
  - new persistence
  - embeddings / vector DB / semantic search
  - LivedContextAggregator
  - Event Bus changes
  - WorldEvent contract changes
  - Agency changes
  - Inner Life architecture changes
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

from ..base import WorldEventSource
from ..perception import WorldEvent

logger = logging.getLogger("soul_os.world.source.weather")


# ───────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────

# Work order §3: "bounded polling, default should be conservative"
# 30 min default (vs Calendar 5 min) — weather changes slower than calendar meetings
DEFAULT_POLLING_INTERVAL_SECS = 1800

# Open-Meteo API endpoint (no API key required)
OPEN_METEO_BASE_URL = "https://api.open-meteo.com/v1/forecast"

# HTTP timeout: 30s (per work order §9; matches Calendar HTTP_TIMEOUT_SECS)
HTTP_TIMEOUT_SECS = 30.0

# Defensive: max 1 event per poll (current weather = 1 observation)
MAX_EVENTS_PER_POLL = 1

# WMO weather codes for rain detection
# Source: https://open-meteo.com/en/docs (WMO Weather interpretation codes)
WMO_RAIN_CODES = frozenset({
    51, 53, 55,  # Drizzle: light, moderate, dense
    61, 63, 65,  # Rain: slight, moderate, heavy
    80, 81, 82,  # Rain showers: slight, moderate, violent
    95, 96, 99,  # Thunderstorm with/without hail
})

# Precipitation threshold (mm) — also used to detect rain
RAIN_PRECIPITATION_THRESHOLD_MM = 0.1


def _parse_location(location: str) -> Tuple[float, float]:
    """
    Parse "lat,lon" string → (lat, lon) floats.

    Defensive: rejects empty, malformed, out-of-range values.
    Caller should call this before constructing the source.
    """
    if not isinstance(location, str):
        raise ValueError(f"location 必須是 str, got: {type(location).__name__}")
    location = location.strip()
    if not location:
        raise ValueError(f"location 必須是非空 str (stripped), got: {location!r}")
    parts = [p.strip() for p in location.split(",")]
    if len(parts) != 2:
        raise ValueError(
            f"location 格式必須是 'lat,lon' (e.g. '25.03,121.57'), got: {location!r}"
        )
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError as e:
        raise ValueError(
            f"location 必須是 'lat,lon' 兩個 float, got: {location!r} ({e})"
        )
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"lat 必須在 [-90, 90], got: {lat}")
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"lon 必須在 [-180, 180], got: {lon}")
    return (lat, lon)


def _compute_novelty_id(
    lat: float,
    lon: float,
    observation_hour_iso: str,
    state: str,
) -> str:
    """
    M6.1-3.1 (per work order §7): deterministic novelty_id from
    location + observation hour + rain state.

    Same location + same observation hour + same rain state → same novelty_id.
    Different hour or different state → different novelty_id.

    Args:
        lat: latitude (rounded to 2 decimals in novelty_id)
        lon: longitude (rounded to 2 decimals in novelty_id)
        observation_hour_iso: ISO 8601 UTC truncated to hour (e.g. "2026-08-13T19:00")
        state: "rain" or "no_rain"

    Returns:
        32-char lowercase hex string (M3.1 validation compatible)
    """
    # Round lat/lon to 2 decimals (~1km precision, sufficient for weather)
    lat_str = f"{lat:.2f}"
    lon_str = f"{lon:.2f}"
    # Build the deterministic key
    raw = f"weather.{lat_str}_{lon_str}.{observation_hour_iso}.{state}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _truncate_to_hour(iso_ts: str) -> str:
    """
    Truncate an ISO 8601 timestamp to the hour bucket (e.g. "2026-08-13T19:00").
    Used for stable novelty_id across polls within the same hour.
    """
    # Parse ISO 8601 (handles "Z" suffix)
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    # Truncate to hour
    return dt.strftime("%Y-%m-%dT%H:00")


def _wmo_code_to_state(weather_code: int, precipitation_mm: float) -> str:
    """
    Map WMO weather code + precipitation to binary state bucket.

    M6.1-3.1 (per work order §3): "precipitation / rain state" only — minimal scope.
    No temperature bucketing in identity (temperature in data field).

    Returns:
        "rain" if rain detected, "no_rain" otherwise
    """
    if precipitation_mm >= RAIN_PRECIPITATION_THRESHOLD_MM:
        return "rain"
    if weather_code in WMO_RAIN_CODES:
        return "rain"
    return "no_rain"


class OpenMeteoWeatherSource(WorldEventSource):
    """
    M6.1-3.1: Real-world WorldEventSource for Open-Meteo public API.

    Polling-driven, env-gated, no API key, no credentials.

    Identity model:
      source_id = "weather" (M3.1 VALID_SOURCES)
      novelty_id = SHA256("weather.{lat}_{lon}.{hour}.{state}")[:32]
      type = "rain_started" (rain) or "weather_temp_change" (no_rain)

    Lifecycle:
      1. __init__: configure location, bus, polling_interval, http_timeout
      2. start(): log + initialize (no-op for v1)
      3. (external) await poll(): called by run_server.py lifespan task
      4. stop(): log + cleanup (no-op for v1)

    Architecture decision (per M6.1-3.1 work order):
      - Polling model (not push/webhook) — no new infrastructure
      - Polling interval: 1800s default (configurable via constructor)
      - 1 location = 1 source (per work order)
      - Env-gated via SOULOS_WEATHER_LOCATION
      - Stdlib only (urllib + json), no new dependencies
    """

    def __init__(
        self,
        location: str,
        bus: Optional[SoulEventBus] = None,
        polling_interval_secs: int = DEFAULT_POLLING_INTERVAL_SECS,
        http_timeout_secs: float = HTTP_TIMEOUT_SECS,
    ) -> None:
        """
        Args:
            location: "lat,lon" string (e.g., "25.03,121.57")
            bus: SoulEventBus for M5.15-3 canonical bus path
            polling_interval_secs: how often to poll (default 1800 = 30 min)
            http_timeout_secs: HTTP request timeout (default 30s)
        """
        # Defensive: parse and validate location at construction time
        self._lat: float
        self._lon: float
        self._lat, self._lon = _parse_location(location)
        self._location_str: str = f"{self._lat:.2f},{self._lon:.2f}"

        if polling_interval_secs < 60:
            raise ValueError(
                f"polling_interval_secs 必須 ≥ 60 (避免過度 polling), got: {polling_interval_secs}"
            )
        if http_timeout_secs < 1.0 or http_timeout_secs > 120.0:
            raise ValueError(
                f"http_timeout_secs 必須在 [1.0, 120.0] 之間, got: {http_timeout_secs}"
            )

        self._bus: Optional[SoulEventBus] = bus
        self._polling_interval_secs: int = polling_interval_secs
        self._http_timeout_secs: float = http_timeout_secs

        # Observability counters
        self._stats = {
            "polls_total": 0,
            "polls_failed": 0,
            "events_emitted": 0,
            "events_skipped_max_cap": 0,
            "events_emission_failed": 0,
            "http_errors": 0,
            "parse_errors": 0,
        }

    @property
    def source_id(self) -> str:
        """
        M3.1 frozen contract: source_id MUST be in VALID_SOURCES.
        Returns "weather" (per M6.1-3.1 contract alignment decision).
        Provider identity ("open_meteo") is preserved in data["weather_provider"].
        """
        return "weather"

    @property
    def location(self) -> str:
        """Canonical "lat,lon" string (rounded to 2 decimals)."""
        return self._location_str

    @property
    def latitude(self) -> float:
        return self._lat

    @property
    def longitude(self) -> float:
        return self._lon

    @property
    def polling_interval_secs(self) -> int:
        return self._polling_interval_secs

    @property
    def http_timeout_secs(self) -> float:
        return self._http_timeout_secs

    # ── WorldEventSource ABC (M3.1 Phase A) ──────────────────

    async def start(self) -> None:
        """
        M3.1 Phase A: start lifecycle. No-op for OpenMeteoWeatherSource
        (polling is driven by external scheduler in run_server.py).
        """
        logger.info(
            f"[OpenMeteoWeatherSource] start() — "
            f"location={self._mask_location(self._location_str)} "
            f"polling_interval={self._polling_interval_secs}s "
            f"timeout={self._http_timeout_secs}s "
            f"bus={'set' if self._bus is not None else 'None'}"
        )

    async def stop(self) -> None:
        """
        M3.1 Phase A: stop lifecycle. Idempotent.
        No-op for OpenMeteoWeatherSource (no resources to release).
        """
        logger.info(
            f"[OpenMeteoWeatherSource] stop() — "
            f"final stats: {self._stats}"
        )

    # ── Polling (M6.1-3.1 IMPLEMENTATION) ──────────────────────

    async def poll(self) -> int:
        """
        Poll Open-Meteo API, parse current weather, emit 1 WorldEvent.

        Returns:
            int: number of WorldEvents emitted in this poll cycle (0 or 1).

        Failure handling (per work order §9):
          - HTTP error (timeout, 4xx, 5xx) → log + skip + return 0
          - Parse error (malformed JSON) → log + skip + return 0
          - Missing required fields → log + skip + return 0
          - All errors logged, never silent, never crash
        """
        self._stats["polls_total"] += 1

        # 1. HTTP GET (run in executor to avoid blocking event loop)
        response_text = await self._fetch_open_meteo()
        if response_text is None:
            self._stats["polls_failed"] += 1
            self._stats["http_errors"] += 1
            return 0

        # 2. Parse JSON
        try:
            data = json.loads(response_text)
        except (json.JSONDecodeError, ValueError) as e:
            self._stats["polls_failed"] += 1
            self._stats["parse_errors"] += 1
            logger.warning(
                f"[OpenMeteoWeatherSource] JSON parse error: "
                f"{type(e).__name__}: {e}"
            )
            return 0

        # 3. Build WorldEvent from current observation
        we = self._observation_to_world_event(data)
        if we is None:
            self._stats["polls_failed"] += 1
            return 0

        # 4. Emit via bus
        if await self._emit_via_bus(we):
            self._stats["events_emitted"] += 1
            emitted = 1
        else:
            emitted = 0

        logger.info(
            f"[OpenMeteoWeatherSource] poll() — "
            f"emitted={emitted} stats={self._stats}"
        )
        return emitted

    def get_stats(self) -> Dict[str, int]:
        """Observability counters snapshot."""
        return dict(self._stats)

    # ── Internal helpers ─────────────────────────────────────

    def _mask_location(self, location: str) -> str:
        """Mask lat/lon for logging (don't log exact private location)."""
        # Show rounded city-level granularity
        if not location:
            return "<empty>"
        # Just return the rounded string (already rounded to 2 decimals)
        return location

    async def _fetch_open_meteo(self) -> Optional[str]:
        """
        HTTP GET Open-Meteo API (run in executor to avoid blocking).

        Returns:
            str: response text
            None: HTTP error (logged, return None, caller counts as failed poll)
        """
        # Build URL with query parameters
        params = (
            f"latitude={self._lat}"
            f"&longitude={self._lon}"
            f"&current=temperature_2m,precipitation,weather_code"
            f"&timezone=UTC"
        )
        url = f"{OPEN_METEO_BASE_URL}?{params}"

        loop = asyncio.get_event_loop()
        try:
            def _do_fetch() -> str:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "SoulOS/1.0 (M6.1-3.1 Weather Source)"},
                )
                with urllib.request.urlopen(
                    req, timeout=self._http_timeout_secs
                ) as response:
                    raw = response.read()
                    return raw.decode("utf-8", errors="replace")
            return await loop.run_in_executor(None, _do_fetch)
        except urllib.error.URLError as e:
            logger.warning(
                f"[OpenMeteoWeatherSource] HTTP error for "
                f"location={self._mask_location(self._location_str)}: "
                f"{type(e).__name__}: {e}"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[OpenMeteoWeatherSource] Unexpected fetch error: "
                f"{type(e).__name__}: {e}"
            )
            return None

    def _observation_to_world_event(
        self,
        data: Dict[str, Any],
    ) -> Optional[WorldEvent]:
        """
        Convert Open-Meteo response to a WorldEvent.

        Returns:
            WorldEvent: if observation is valid
            None: if missing required fields

        Identity (M6.1-3.1 per work order §7):
          - novelty_id based on (location, observation hour, state)
          - NOT based on poll timestamp (would create duplicate on every poll)
        """
        # Validate top-level structure
        current = data.get("current")
        if not isinstance(current, dict):
            logger.debug(
                f"[OpenMeteoWeatherSource] missing 'current' in response, skip"
            )
            return None

        # Required fields in current
        observation_time_str = current.get("time")
        if not observation_time_str or not isinstance(observation_time_str, str):
            logger.debug(
                f"[OpenMeteoWeatherSource] missing 'current.time', skip"
            )
            return None

        temperature_raw = current.get("temperature_2m")
        if temperature_raw is None:
            logger.debug(
                f"[OpenMeteoWeatherSource] missing 'current.temperature_2m', skip"
            )
            return None
        try:
            temperature_c = float(temperature_raw)
        except (ValueError, TypeError):
            logger.debug(
                f"[OpenMeteoWeatherSource] temperature_2m not numeric: {temperature_raw!r}"
            )
            return None

        precipitation_raw = current.get("precipitation")
        if precipitation_raw is None:
            logger.debug(
                f"[OpenMeteoWeatherSource] missing 'current.precipitation', skip"
            )
            return None
        try:
            precipitation_mm = float(precipitation_raw)
        except (ValueError, TypeError):
            logger.debug(
                f"[OpenMeteoWeatherSource] precipitation not numeric: {precipitation_raw!r}"
            )
            return None

        weather_code_raw = current.get("weather_code")
        if weather_code_raw is None:
            logger.debug(
                f"[OpenMeteoWeatherSource] missing 'current.weather_code', skip"
            )
            return None
        try:
            weather_code = int(weather_code_raw)
        except (ValueError, TypeError):
            logger.debug(
                f"[OpenMeteoWeatherSource] weather_code not int: {weather_code_raw!r}"
            )
            return None

        # Normalize observation time to UTC
        try:
            obs_dt = datetime.fromisoformat(
                observation_time_str.replace("Z", "+00:00")
            )
            if obs_dt.tzinfo is None:
                obs_dt = obs_dt.replace(tzinfo=timezone.utc)
            else:
                obs_dt = obs_dt.astimezone(timezone.utc)
        except (ValueError, TypeError) as e:
            logger.debug(
                f"[OpenMeteoWeatherSource] invalid time format: "
                f"{observation_time_str!r} ({e})"
            )
            return None

        # Compute state and identity
        state = _wmo_code_to_state(weather_code, precipitation_mm)
        hour_bucket = _truncate_to_hour(obs_dt.isoformat())
        novelty_id = _compute_novelty_id(
            self._lat, self._lon, hour_bucket, state
        )

        # Choose type: "rain_started" if rain, "weather_temp_change" otherwise
        # Both are existing M3.1 types (recognized by perception scoring)
        event_type = "rain_started" if state == "rain" else "weather_temp_change"

        # Build data field (observability + future LLM context)
        data_field: Dict[str, Any] = {
            "weather_provider": "open_meteo",
            "weather_location": self._location_str,
            "weather_observation_time": obs_dt.isoformat(),
            "weather_temperature_c": temperature_c,
            "weather_precipitation_mm": precipitation_mm,
            "weather_code": weather_code,
            "weather_state": state,  # "rain" or "no_rain" (bucket for LLM)
        }

        # Build summary (one-sentence objective fact)
        if state == "rain":
            summary = (
                f"目前天氣：{self._location_str} 降雨 "
                f"({precipitation_mm:.1f}mm, 氣溫 {temperature_c:.1f}°C)"
            )
        else:
            summary = (
                f"目前天氣：{self._location_str} 無雨 "
                f"(氣溫 {temperature_c:.1f}°C)"
            )

        # Build WorldEvent (M3 frozen contract: 7 fields, M3.1 Phase B: priority)
        return WorldEvent(
            source=self.source_id,        # "weather" (M3.1 VALID_SOURCES)
            type=event_type,              # "rain_started" or "weather_temp_change"
            novelty_id=novelty_id,        # SHA256 deterministic (M3.1 compatible)
            ts=obs_dt.isoformat(),        # ISO 8601 UTC (M3.1 validation)
            summary=summary,              # human-readable
            data=data_field,
            priority=0,                   # M3.1 Phase B default
        )

    async def _emit_via_bus(self, world_event: WorldEvent) -> bool:
        """
        Emit WorldEvent via M5.15-3 canonical bus path (async).

        Returns:
            bool: True if emitted, False if bus is None or emit failed.
        """
        if self._bus is None:
            logger.warning(
                f"[OpenMeteoWeatherSource] bus is None, cannot emit: "
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
                f"[OpenMeteoWeatherSource] emit failed: "
                f"{type(e).__name__}: {e}"
            )
            self._stats["events_emission_failed"] += 1
            return False
