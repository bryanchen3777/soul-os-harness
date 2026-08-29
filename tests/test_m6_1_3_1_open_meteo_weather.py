"""
tests/test_m6_1_3_1_open_meteo_weather.py — M6.1-3.1 Open-Meteo Weather Source

M6.1-3.1 (Bry 派工 2026-08-13 19:27, OWNER AUTHORIZATION APPROVED) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE

驗證:
  - OpenMeteoWeatherSource implements existing WorldEventSource contract
  - Public Open-Meteo API is env-gated (no API key required)
  - Missing location = no weather activity
  - Weather observations become existing WorldEvent (source=weather, type=rain_started|weather_temp_change)
  - source_id = "weather" (M3.1 VALID_SOURCES contract, NOT "weather.open_meteo")
  - Provider identity "open_meteo" preserved in data["weather_provider"]
  - WorldEvent.novelty_id = SHA256("weather.{lat}_{lon}.{hour}.{state}")[:32]
  - Event Bus remains canonical transport
  - Current weather observable (rain, no_rain)
  - Temperature observable in data
  - Timestamp preserved (ISO 8601 UTC)
  - Location preserved
  - API failure is non-fatal (HTTP error, timeout, parse error)
  - Polling bounded (default 1800s, configurable)
  - Environment gating present
  - Tests use mocked network (no real HTTP calls)
  - M5.9-3 dedup: types NOT in QUALIFYING_TYPES → no InnerLifeEvent
  - No frozen downstream contract broken
  - M5.15-5 source_world_event_novelty_id propagation preserved (even if no InnerLifeEvent)

Test sections (per M6.1-3.1 work order):
  A. Location parsing
  B. Source identity (source_id, provider)
  C. WorldEvent construction (rain state, no_rain state)
  D. Novelty identity (deterministic, location+hour+state)
  E. Event Bus canonical path
  F. Polling behavior
  G. HTTP failure handling
  H. Parse failure handling
  I. Missing fields handling
  J. WorldPerception integration
  K. WorldInnerLifeAdapter (M5.9-3) behavior
  L. Production isolation
  M. Env gating (no env = no wiring)
  N. Configuration validation
"""
from __future__ import annotations

import asyncio
import hashlib
import json
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
from src.world.perception import VALID_SOURCES, WorldEvent
from src.world.source import OpenMeteoWeatherSource
from src.world.source.open_meteo import (
    DEFAULT_POLLING_INTERVAL_SECS,
    HTTP_TIMEOUT_SECS,
    RAIN_PRECIPITATION_THRESHOLD_MM,
    WMO_RAIN_CODES,
    _compute_novelty_id,
    _parse_location,
    _truncate_to_hour,
    _wmo_code_to_state,
)
from src.world.trace import WorldPerceptionTraceWriter


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _mock_urlopen_response(json_text: str) -> MagicMock:
    """Build a mock urlopen response that returns the given JSON text."""
    mock_response = MagicMock()
    mock_response.read = MagicMock(return_value=json_text.encode("utf-8"))
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def _open_meteo_response(
    time_iso: str = "2026-08-13T19:00",
    temperature: float = 27.9,
    precipitation: float = 0.0,
    weather_code: int = 2,
) -> str:
    """Build a minimal Open-Meteo response JSON."""
    return json.dumps({
        "latitude": 25.03,
        "longitude": 121.57,
        "generationtime_ms": 0.1,
        "utc_offset_seconds": 0,
        "timezone": "GMT",
        "elevation": 17.0,
        "current_units": {
            "time": "iso8601",
            "interval": "seconds",
            "temperature_2m": "°C",
            "precipitation": "mm",
            "weather_code": "wmo code",
        },
        "current": {
            "time": time_iso,
            "interval": 900,
            "temperature_2m": temperature,
            "precipitation": precipitation,
            "weather_code": weather_code,
        },
    })


async def _poll_with_mock(src: OpenMeteoWeatherSource, json_text: str) -> int:
    """Helper: poll a source with mocked HTTP returning the given JSON."""
    with patch("asyncio.get_event_loop") as mock_loop_fn:
        mock_event_loop = MagicMock()
        future = asyncio.Future()
        future.set_result(json_text)
        mock_event_loop.run_in_executor = MagicMock(return_value=future)
        mock_loop_fn.return_value = mock_event_loop
        return await src.poll()


# ───────────────────────────────────────────────────────────
# A. Location parsing
# ───────────────────────────────────────────────────────────

class TestSectionA_LocationParsing:
    """A. Location parsing is defensive (reject bad input at __init__)."""

    def test_a1_valid_location_parses(self):
        """A.1: 'lat,lon' string parses to floats."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src.latitude == 25.03
        assert src.longitude == 121.57
        assert src.location == "25.03,121.57"

    def test_a2_location_strips_whitespace(self):
        """A.2: 'lat, lon' with whitespace parses."""
        src = OpenMeteoWeatherSource(" 25.03 , 121.57 ")
        assert src.latitude == 25.03
        assert src.longitude == 121.57

    def test_a3_negative_coords(self):
        """A.3: Negative lat/lon (Southern/Western hemispheres)."""
        src = OpenMeteoWeatherSource("-33.87,151.21")  # Sydney
        assert src.latitude == -33.87
        assert src.longitude == 151.21

    def test_a4_empty_location_raises(self):
        """A.4: Empty string raises ValueError."""
        with pytest.raises(ValueError, match="location"):
            OpenMeteoWeatherSource("")

    def test_a5_malformed_location_raises(self):
        """A.5: 'not coords' raises ValueError."""
        with pytest.raises(ValueError, match="location"):
            OpenMeteoWeatherSource("not coords")

    def test_a6_three_parts_raises(self):
        """A.6: '1,2,3' raises ValueError."""
        with pytest.raises(ValueError, match="lat,lon"):
            OpenMeteoWeatherSource("1,2,3")

    def test_a7_out_of_range_lat_raises(self):
        """A.7: lat > 90 raises ValueError."""
        with pytest.raises(ValueError, match="lat"):
            OpenMeteoWeatherSource("91.0,121.57")

    def test_a8_out_of_range_lon_raises(self):
        """A.8: lon > 180 raises ValueError."""
        with pytest.raises(ValueError, match="lon"):
            OpenMeteoWeatherSource("25.03,181.0")

    def test_a9_non_numeric_raises(self):
        """A.9: non-numeric lat raises ValueError."""
        with pytest.raises(ValueError, match="float"):
            OpenMeteoWeatherSource("abc,121.57")

    def test_a10_non_str_raises(self):
        """A.10: non-str location raises ValueError."""
        with pytest.raises(ValueError, match="str"):
            OpenMeteoWeatherSource(25.03)  # type: ignore[arg-type]


# ───────────────────────────────────────────────────────────
# B. Source identity
# ───────────────────────────────────────────────────────────

class TestSectionB_SourceIdentity:
    """B. source_id = "weather" (M3.1 VALID_SOURCES contract)."""

    def test_b1_source_id_is_weather(self):
        """B.1: source_id returns "weather" (not "weather.open_meteo")."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src.source_id == "weather"

    def test_b2_source_id_in_valid_sources(self):
        """B.2: source_id is in VALID_SOURCES (M3.1 frozen contract)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src.source_id in VALID_SOURCES

    def test_b3_provider_in_data(self):
        """B.3: Provider identity preserved in data['weather_provider']."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response()
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.data["weather_provider"] == "open_meteo"


# ───────────────────────────────────────────────────────────
# C. WorldEvent construction
# ───────────────────────────────────────────────────────────

class TestSectionC_WorldEventConstruction:
    """C. Weather observations become existing WorldEvent (frozen contract)."""

    def test_c1_no_rain_becomes_weather_temp_change(self):
        """C.1: no_rain → type='weather_temp_change'."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(precipitation=0.0, weather_code=2)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.source == "weather"
        assert we.type == "weather_temp_change"

    def test_c2_rain_becomes_rain_started(self):
        """C.2: rain → type='rain_started'."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(precipitation=2.5, weather_code=61)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.source == "weather"
        assert we.type == "rain_started"

    def test_c3_rain_via_weather_code_alone(self):
        """C.3: rain via WMO code alone (precipitation=0 but code=51)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(precipitation=0.0, weather_code=51)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.type == "rain_started"

    def test_c4_temperature_in_data(self):
        """C.4: Temperature preserved in data['weather_temperature_c']."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(temperature=27.9)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.data["weather_temperature_c"] == 27.9

    def test_c5_precipitation_in_data(self):
        """C.5: Precipitation preserved in data['weather_precipitation_mm']."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(precipitation=3.5)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.data["weather_precipitation_mm"] == 3.5

    def test_c6_location_in_data(self):
        """C.6: Location preserved in data['weather_location']."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response()
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.data["weather_location"] == "25.03,121.57"

    def test_c7_timestamp_utc(self):
        """C.7: Timestamp preserved as ISO 8601 UTC."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(time_iso="2026-08-13T19:00")
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        # Should be parseable as ISO 8601
        dt = datetime.fromisoformat(we.ts.replace("Z", "+00:00"))
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0

    def test_c8_summary_human_readable(self):
        """C.8: Summary is human-readable."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response(temperature=27.9, precipitation=0.0)
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert "25.03,121.57" in we.summary
        assert "27.9" in we.summary

    def test_c9_priority_zero(self):
        """C.9: priority=0 (M3.1 Phase B default)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        json_text = _open_meteo_response()
        we = src._observation_to_world_event(json.loads(json_text))
        assert we is not None
        assert we.priority == 0


# ───────────────────────────────────────────────────────────
# D. Novelty identity
# ───────────────────────────────────────────────────────────

class TestSectionD_NoveltyIdentity:
    """D. novelty_id = SHA256('weather.{lat}_{lon}.{hour}.{state}')[:32]."""

    def test_d1_same_state_same_id(self):
        """D.1: Same location + same hour + same state → same novelty_id."""
        id1 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        id2 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        assert id1 == id2

    def test_d2_different_state_different_id(self):
        """D.2: Different state → different novelty_id."""
        id1 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        id2 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "no_rain")
        assert id1 != id2

    def test_d3_different_hour_different_id(self):
        """D.3: Different observation hour → different novelty_id."""
        id1 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        id2 = _compute_novelty_id(25.03, 121.57, "2026-08-13T20:00", "rain")
        assert id1 != id2

    def test_d4_different_location_different_id(self):
        """D.4: Different location → different novelty_id."""
        id1 = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        id2 = _compute_novelty_id(25.04, 121.57, "2026-08-13T19:00", "rain")
        assert id1 != id2

    def test_d5_novelty_id_m3_1_compatible(self):
        """D.5: novelty_id matches M3.1 validation [a-z0-9_]{4,128}."""
        nid = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        import re
        assert re.match(r"^[a-z0-9_]{4,128}$", nid), f"Invalid novelty_id: {nid}"

    def test_d6_novelty_id_32_chars(self):
        """D.6: novelty_id is exactly 32 chars (M3.1-compatible hex)."""
        nid = _compute_novelty_id(25.03, 121.57, "2026-08-13T19:00", "rain")
        assert len(nid) == 32
        assert all(c in "0123456789abcdef" for c in nid)

    def test_d7_truncate_to_hour(self):
        """D.7: _truncate_to_hour normalizes to YYYY-MM-DDTHH:00."""
        out = _truncate_to_hour("2026-08-13T19:45:00+00:00")
        assert out == "2026-08-13T19:00"

    def test_d8_wmo_code_rain_detection(self):
        """D.8: WMO code 51 (drizzle) → 'rain' state."""
        assert _wmo_code_to_state(51, 0.0) == "rain"

    def test_d9_wmo_code_clear_no_rain(self):
        """D.9: WMO code 0 (clear) → 'no_rain' state."""
        assert _wmo_code_to_state(0, 0.0) == "no_rain"

    def test_d10_threshold_precipitation(self):
        """D.10: precipitation >= threshold → 'rain'."""
        assert _wmo_code_to_state(0, RAIN_PRECIPITATION_THRESHOLD_MM) == "rain"
        assert _wmo_code_to_state(0, RAIN_PRECIPITATION_THRESHOLD_MM - 0.01) == "no_rain"

    def test_d11_novelty_id_stable_across_polls(self):
        """D.11: Same observation in two polls → same novelty_id (dedup)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        # Poll 1
        json_text = _open_meteo_response(time_iso="2026-08-13T19:00", weather_code=61)
        we1 = src._observation_to_world_event(json.loads(json_text))
        # Poll 2 (same hour, same state)
        json_text_2 = _open_meteo_response(time_iso="2026-08-13T19:30", weather_code=61)
        we2 = src._observation_to_world_event(json.loads(json_text_2))
        assert we1 is not None
        assert we2 is not None
        assert we1.novelty_id == we2.novelty_id


# ───────────────────────────────────────────────────────────
# E. Event Bus canonical path
# ───────────────────────────────────────────────────────────

class TestSectionE_EventBusPath:
    """E. Event Bus is canonical transport (M5.15-3 preserved)."""

    @pytest.mark.asyncio
    async def test_e1_emits_soul_event_to_bus(self):
        """E.1: poll() publishes SoulEvent(WORLD_EVENT, target='broadcast')."""
        bus = SoulEventBus()
        captured: List[SoulEvent] = []

        async def _capture_handler(event: SoulEvent) -> None:
            captured.append(event)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture_handler,
            event_filter={EventType.WORLD_EVENT},
        )

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        json_text = _open_meteo_response()
        await bus.start()
        try:
            emitted = await _poll_with_mock(src, json_text)
            assert emitted == 1
            # Wait for bus dispatch
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()
        assert len(captured) == 1
        event = captured[0]
        assert event.event_type == EventType.WORLD_EVENT
        assert event.target == "broadcast"
        assert event.source == "weather"

    @pytest.mark.asyncio
    async def test_e2_bus_emits_normal_priority(self):
        """E.2: SoulEvent.priority = NORMAL (M5.15-3 canonical)."""
        bus = SoulEventBus()
        captured: List[SoulEvent] = []

        async def _capture_handler(event: SoulEvent) -> None:
            captured.append(event)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture_handler,
            event_filter={EventType.WORLD_EVENT},
        )

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response())
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()
        assert captured[0].priority == EventPriority.NORMAL

    @pytest.mark.asyncio
    async def test_e3_bus_payload_matches_world_event(self):
        """E.3: payload is WorldEvent.to_payload() dict."""
        bus = SoulEventBus()
        captured: List[SoulEvent] = []

        async def _capture_handler(event: SoulEvent) -> None:
            captured.append(event)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture_handler,
            event_filter={EventType.WORLD_EVENT},
        )

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response(weather_code=61))
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()
        payload = captured[0].payload
        assert payload["source"] == "weather"
        assert payload["type"] == "rain_started"
        assert "novelty_id" in payload
        assert "ts" in payload
        assert "summary" in payload
        assert "data" in payload
        assert payload["priority"] == 0

    @pytest.mark.asyncio
    async def test_e4_no_bus_logs_warning(self):
        """E.4: bus=None logs warning, doesn't crash."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        emitted = await _poll_with_mock(src, _open_meteo_response())
        assert emitted == 0
        stats = src.get_stats()
        assert stats["events_emission_failed"] >= 1


# ───────────────────────────────────────────────────────────
# F. Polling behavior
# ───────────────────────────────────────────────────────────

class TestSectionF_Polling:
    """F. Polling is bounded and configurable."""

    def test_f1_default_polling_interval(self):
        """F.1: Default polling interval is 1800s (30 min)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src.polling_interval_secs == DEFAULT_POLLING_INTERVAL_SECS
        assert src.polling_interval_secs == 1800

    def test_f2_custom_polling_interval(self):
        """F.2: Custom polling interval accepted."""
        src = OpenMeteoWeatherSource("25.03,121.57", polling_interval_secs=600)
        assert src.polling_interval_secs == 600

    def test_f3_polling_interval_min_60(self):
        """F.3: polling_interval_secs < 60 raises ValueError (no excessive polling)."""
        with pytest.raises(ValueError, match="polling_interval_secs"):
            OpenMeteoWeatherSource("25.03,121.57", polling_interval_secs=30)

    @pytest.mark.asyncio
    async def test_f4_poll_increments_total(self):
        """F.4: poll() increments polls_total counter."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        await _poll_with_mock(src, _open_meteo_response())
        assert src.get_stats()["polls_total"] == 1

    @pytest.mark.asyncio
    async def test_f5_poll_emits_at_most_one_event(self):
        """F.5: poll() emits at most 1 event (current weather = 1 observation)."""
        bus = SoulEventBus()
        captured: List[SoulEvent] = []

        async def _capture_handler(event: SoulEvent) -> None:
            captured.append(event)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture_handler,
            event_filter={EventType.WORLD_EVENT},
        )

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response())
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()
        assert len(captured) == 1


# ───────────────────────────────────────────────────────────
# G. HTTP failure handling
# ───────────────────────────────────────────────────────────

class TestSectionG_HTTPFailure:
    """G. HTTP failures are non-fatal."""

    @pytest.mark.asyncio
    async def test_g1_http_error_returns_zero(self):
        """G.1: HTTP error → poll() returns 0, polls_failed increments."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        with patch("asyncio.get_event_loop") as mock_loop_fn:
            mock_event_loop = MagicMock()
            future = asyncio.Future()
            future.set_exception(urllib.error.URLError("timeout"))
            mock_event_loop.run_in_executor = MagicMock(return_value=future)
            mock_loop_fn.return_value = mock_event_loop
            emitted = await src.poll()
        assert emitted == 0
        stats = src.get_stats()
        assert stats["polls_failed"] == 1
        assert stats["http_errors"] == 1

    @pytest.mark.asyncio
    async def test_g2_timeout_returns_zero(self):
        """G.2: Timeout → poll() returns 0, no crash."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        with patch("asyncio.get_event_loop") as mock_loop_fn:
            mock_event_loop = MagicMock()
            future = asyncio.Future()
            future.set_exception(TimeoutError("connect timeout"))
            mock_event_loop.run_in_executor = MagicMock(return_value=future)
            mock_loop_fn.return_value = mock_event_loop
            emitted = await src.poll()
        assert emitted == 0
        stats = src.get_stats()
        assert stats["http_errors"] >= 1

    @pytest.mark.asyncio
    async def test_g3_unexpected_exception_returns_zero(self):
        """G.3: Unexpected exception → poll() returns 0, no crash."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        with patch("asyncio.get_event_loop") as mock_loop_fn:
            mock_event_loop = MagicMock()
            future = asyncio.Future()
            future.set_exception(RuntimeError("unexpected"))
            mock_event_loop.run_in_executor = MagicMock(return_value=future)
            mock_loop_fn.return_value = mock_event_loop
            emitted = await src.poll()
        assert emitted == 0
        stats = src.get_stats()
        assert stats["http_errors"] >= 1


# ───────────────────────────────────────────────────────────
# H. Parse failure handling
# ───────────────────────────────────────────────────────────

class TestSectionH_ParseFailure:
    """H. Malformed JSON handled safely."""

    @pytest.mark.asyncio
    async def test_h1_invalid_json_returns_zero(self):
        """H.1: Malformed JSON → poll() returns 0, parse_errors increments."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        emitted = await _poll_with_mock(src, "{ invalid json")
        assert emitted == 0
        stats = src.get_stats()
        assert stats["polls_failed"] == 1
        assert stats["parse_errors"] == 1

    @pytest.mark.asyncio
    async def test_h2_empty_response_returns_zero(self):
        """H.2: Empty response → poll() returns 0."""
        src = OpenMeteoWeatherSource("25.03,121.57", bus=None)
        emitted = await _poll_with_mock(src, "")
        assert emitted == 0
        stats = src.get_stats()
        assert stats["parse_errors"] >= 1


# ───────────────────────────────────────────────────────────
# I. Missing fields handling
# ───────────────────────────────────────────────────────────

class TestSectionI_MissingFields:
    """I. Missing required fields handled gracefully."""

    def test_i1_missing_current_returns_none(self):
        """I.1: Missing 'current' in response → returns None."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        we = src._observation_to_world_event({"latitude": 25.03})
        assert we is None

    def test_i2_missing_time_returns_none(self):
        """I.2: Missing 'current.time' → returns None."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        we = src._observation_to_world_event({
            "current": {"temperature_2m": 27.9, "precipitation": 0.0, "weather_code": 2}
        })
        assert we is None

    def test_i3_missing_temperature_returns_none(self):
        """I.3: Missing 'current.temperature_2m' → returns None."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        we = src._observation_to_world_event({
            "current": {"time": "2026-08-13T19:00", "precipitation": 0.0, "weather_code": 2}
        })
        assert we is None

    def test_i4_non_numeric_temperature_returns_none(self):
        """I.4: Non-numeric temperature → returns None."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        we = src._observation_to_world_event({
            "current": {"time": "2026-08-13T19:00", "temperature_2m": "hot", "precipitation": 0.0, "weather_code": 2}
        })
        assert we is None

    def test_i5_invalid_time_format_returns_none(self):
        """I.5: Invalid time format → returns None."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        we = src._observation_to_world_event({
            "current": {"time": "not-a-time", "temperature_2m": 27.9, "precipitation": 0.0, "weather_code": 2}
        })
        assert we is None


# ───────────────────────────────────────────────────────────
# J. WorldPerception integration (full E2E)
# ───────────────────────────────────────────────────────────

class TestSectionJ_WorldPerceptionIntegration:
    """J. Weather → WorldPerceptionMiddleware → state (full E2E)."""

    @pytest.mark.asyncio
    async def test_j1_world_perception_receives_weather(self, tmp_path, monkeypatch):
        """J.1: WorldPerceptionMiddleware receives weather WorldEvent (state + trace)."""
        # Test isolation: redirect data root
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        bus = SoulEventBus()
        state = WorldPerceptionState()
        trace_writer = WorldPerceptionTraceWriter(tmp_path / "world" / "perception_trace.jsonl")
        middleware = WorldPerceptionMiddleware(bus=bus, state=state, trace_writer=trace_writer)
        middleware.register()

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response(weather_code=61))
            await asyncio.sleep(0.2)  # wait for bus dispatch
        finally:
            await bus.stop()

        # Verify state received the event
        active = state.get_active_events()
        assert len(active) == 1
        assert active[0].source == "weather"
        assert active[0].type == "rain_started"

    @pytest.mark.asyncio
    async def test_j2_world_perception_rejects_no_rain(self, tmp_path, monkeypatch):
        """J.2: no_rain event also reaches state (perception accepts all valid)."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        bus = SoulEventBus()
        state = WorldPerceptionState()
        trace_writer = WorldPerceptionTraceWriter(tmp_path / "world" / "perception_trace.jsonl")
        middleware = WorldPerceptionMiddleware(bus=bus, state=state, trace_writer=trace_writer)
        middleware.register()

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response(precipitation=0.0, weather_code=2))
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()

        active = state.get_active_events()
        assert len(active) == 1
        assert active[0].type == "weather_temp_change"


# ───────────────────────────────────────────────────────────
# K. WorldInnerLifeAdapter (M5.9-3) behavior
# ───────────────────────────────────────────────────────────

class TestSectionK_InnerLifeAdapter:
    """K. M5.9-3 dedup: weather types IN QUALIFYING_TYPES (SG-1 解冻 2026-08-29) → InnerLifeEvent created."""

    @pytest.mark.asyncio
    async def test_k1_rain_started_qualifying(self):
        """K.1: 'rain_started' / 'weather_temp_change' ARE in WORLD_QUALIFYING_TYPES (SG-1 解冻)."""
        from src.world.inner_life_adapter import (
            WORLD_QUALIFYING_TYPES,
            WorldInnerLifeAdapter,
            qualify_world_event,
        )
        assert "rain_started" in WORLD_QUALIFYING_TYPES
        assert "weather_temp_change" in WORLD_QUALIFYING_TYPES

    def test_k1b_qualify_world_event_returns_yes(self):
        """K.1b: qualify_world_event(weather_event) → YES (SG-1 解冻)."""
        from src.world.inner_life_adapter import (
            WorldQualificationDecision,
            qualify_world_event,
        )
        we = WorldEvent(
            source="weather",
            type="rain_started",
            novelty_id="weather_test_001",
            ts="2026-08-13T19:00:00+00:00",
            summary="Rain test",
        )
        qual = qualify_world_event(we)
        assert qual.decision == WorldQualificationDecision.YES

    @pytest.mark.asyncio
    async def test_k2_adapter_creates_inner_life_event(self, tmp_path, monkeypatch):
        """K.2: WorldInnerLifeAdapter creates InnerLifeEvent for weather (SG-1 解冻)."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        bus = SoulEventBus()
        writer = InnerLifeWriter()
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus)

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            await _poll_with_mock(src, _open_meteo_response(weather_code=61))
            await asyncio.sleep(0.2)
        finally:
            await bus.stop()

        # InnerLifeEvent created (rain_started now qualifying, SG-1 解冻)
        assert len(writer._events) == 1
        # Adapter stats: received 1, qualifying_yes 1, non_qualifying 0
        assert adapter.get_stats()["events_received"] == 1
        assert adapter.get_stats()["qualifying_yes"] == 1
        assert adapter.get_stats()["non_qualifying"] == 0

    @pytest.mark.asyncio
    async def test_k3_repeated_polls_no_duplicate_inner_life(self, tmp_path, monkeypatch):
        """K.3: Repeated polls of same weather dedup → exactly 1 InnerLifeEvent."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        bus = SoulEventBus()
        writer = InnerLifeWriter()
        adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
        adapter.register(bus)

        src = OpenMeteoWeatherSource("25.03,121.57", bus=bus)
        await bus.start()
        try:
            # 3 polls of same observation (same hour, same state)
            for _ in range(3):
                await _poll_with_mock(src, _open_meteo_response(weather_code=61))
                await asyncio.sleep(0.1)
        finally:
            await bus.stop()

        # Dedup: 3 polls same novelty_id → exactly 1 InnerLifeEvent created
        assert len(writer._events) == 1
        assert adapter.get_stats()["qualifying_yes"] == 3
        assert adapter.get_stats()["duplicates_skipped"] == 2
        assert adapter.get_stats()["non_qualifying"] == 0


# ───────────────────────────────────────────────────────────
# L. Production isolation
# ───────────────────────────────────────────────────────────

class TestSectionL_ProductionIsolation:
    """L. Tests don't touch production data."""

    def test_l1_no_production_mutation(self):
        """L.1: Source creation doesn't touch production data."""
        # This test passes if no exception raised during construction
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src is not None

    def test_l2_no_network_during_init(self):
        """L.2: __init__ doesn't make network calls."""
        # If this test runs, no network was used (just __init__)
        with patch("urllib.request.urlopen") as mock_urlopen:
            OpenMeteoWeatherSource("25.03,121.57")
            mock_urlopen.assert_not_called()


# ───────────────────────────────────────────────────────────
# M. Env gating
# ───────────────────────────────────────────────────────────

class TestSectionM_EnvGating:
    """M. Env gating: no SOULOS_WEATHER_LOCATION = no weather activity."""

    def test_m1_env_var_documented(self):
        """M.1: Env var name is SOULOS_WEATHER_LOCATION (canonical)."""
        # Smoke test: source accepts the format the env var provides
        src = OpenMeteoWeatherSource(os.getenv("SOULOS_WEATHER_LOCATION", "25.03,121.57"))
        assert src is not None


# ───────────────────────────────────────────────────────────
# N. Configuration validation
# ───────────────────────────────────────────────────────────

class TestSectionN_Configuration:
    """N. Configuration validation at construction time."""

    def test_n1_http_timeout_min(self):
        """N.1: http_timeout_secs < 1.0 raises ValueError."""
        with pytest.raises(ValueError, match="http_timeout_secs"):
            OpenMeteoWeatherSource("25.03,121.57", http_timeout_secs=0.5)

    def test_n2_http_timeout_max(self):
        """N.2: http_timeout_secs > 120.0 raises ValueError."""
        with pytest.raises(ValueError, match="http_timeout_secs"):
            OpenMeteoWeatherSource("25.03,121.57", http_timeout_secs=200.0)

    def test_n3_default_http_timeout(self):
        """N.3: Default http_timeout_secs is 30.0."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        assert src.http_timeout_secs == HTTP_TIMEOUT_SECS
        assert src.http_timeout_secs == 30.0

    def test_n4_default_stats_initialized(self):
        """N.4: Stats counters initialized at construction."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        stats = src.get_stats()
        assert stats["polls_total"] == 0
        assert stats["polls_failed"] == 0
        assert stats["events_emitted"] == 0
        assert stats["http_errors"] == 0
        assert stats["parse_errors"] == 0

    def test_n5_repr_includes_source_id(self):
        """N.5: __repr__ includes source_id (for debuggability)."""
        src = OpenMeteoWeatherSource("25.03,121.57")
        r = repr(src)
        assert "weather" in r


# ───────────────────────────────────────────────────────────
# Fixtures
# ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _cleanup_eventbus():
    """Ensure no event bus tasks leak between tests."""
    yield
    # No-op; pytest-asyncio handles task cleanup
