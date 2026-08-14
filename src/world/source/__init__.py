"""
src/world/source/ — M3.1 Phase A WorldEventSource abstraction subpackage。

Bry 拍板 2026-08-08 02:21 A' cleanup:
  source/ 是 M3.1 Phase A 的 source abstraction subpackage, 但只放
  SyntheticWorldEventSource 一個 implementation。
  WorldEventSource ABC 改放在 src/world/base.py (sibling, 唯一 canonical)。

  目前內容:
    - synthetic.py:  SyntheticWorldEventSource (M3 既有邏輯,
                     M3.1 Phase A 改 conform WorldEventSource)
    - calendar_ical.py: IcalCalendarSource (M5.15-6 IMPLEMENTATION)
                     Real-world WorldEventSource for iCal/ICS public feeds.
    - open_meteo.py:    OpenMeteoWeatherSource (M6.1-3.1 IMPLEMENTATION)
                     Real-world WorldEventSource for Open-Meteo public API.
                     First Physical-context signal producer.

  既有 M3 行為 100% 不變:
    - SyntheticWorldEventSource 的 build_*() factory methods 完全保留
    - SYNTHETIC_TEST_EVENTS spec 完全保留
    - 既有 M3 tests 透過 `from src.world import SyntheticWorldEventSource` 仍可拿到
    - 既有 path `from src.world.source import SyntheticWorldEventSource` 也可
      拿到 (透過本 __init__.py re-export, 因為 src.world.source 在這個
      subpackage 存在下被 Python 解析成 package 而非 module)

  NOTE:
    src/world/source.py (sibling module) 是一個 compatibility shim,
    內容是 re-export 邏輯。當 source/ subpackage 存在時, Python 對
    `src.world.source` 解析優先 package, 所以 source.py module
    永遠不會被加載; 但 compatibility shim 內容保留作為 path
    reference + documentation, 給未來 path 重組時 (例如刪除
    subpackage 把 source.py 重新啟用) 留伏筆。

M5.15-6 (Bry 派工 2026-08-12 19:29) — Real-World Calendar Source Integration:
  Added IcalCalendarSource (src/world/source/calendar_ical.py).
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent / M5.9-2 / M5.15-3 / M5.15-5 all preserved)
  - Public iCal URL (no OAuth, no credentials, no token store)
  - Env-gated via SOULOS_CALENDAR_ICAL_URL
  - Polling-driven (300s default), 24h lookahead default
  - Parent-only for RRULE (no recurrence engine)
  - CANCELLED skipped
  - Library: icalendar (PyPI, MIT, mature)

M6.1-3.1 (Bry 派工 2026-08-13 19:27, OWNER AUTHORIZATION APPROVED) — Weather Signal Source:
  Added OpenMeteoWeatherSource (src/world/source/open_meteo.py).
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent / M5.9-2 / M5.15-3 / M5.15-5 all preserved)
  - source_id = "weather" (per M3.1 VALID_SOURCES; provider "open_meteo" preserved in data["weather_provider"])
  - Public API (no API key, no credentials, no token store)
  - Env-gated via SOULOS_WEATHER_LOCATION (e.g., "25.03,121.57")
  - Polling-driven (1800s = 30min default, conservative)
  - Library: stdlib only (urllib + json, no new dependencies)
  - M3.1 Invariant E exception (same justification as Calendar M5.15-6)
  - Deterministic novelty_id = SHA256("weather.{lat}_{lon}.{hour}.{state}")[:32]
  - M5.9-3 dedup: types "rain_started" and "weather_temp_change" NOT in WORLD_QUALIFYING_TYPES,
    so adapter will reject (no InnerLifeEvent created); correct minimal scope

M6.1-5.1 (Bry 派工 2026-08-13 20:28, OWNER AUTHORIZATION APPROVED) — News Signal Source:
  Added RssNewsSource (src/world/source/news_rss.py).
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent
    / M5.9-2 / M5.9-3 / M5.15-3 / M5.15-5 all preserved)
  - source_id = "news" (per M3.1 VALID_SOURCES; provider identity preserved in
    data["news_provider"])
  - Public RSS feeds (no API key, no credentials, no token store)
  - Env-gated via SOULOS_NEWS_FEEDS (format: "provider1|url1,provider2|url2,...")
  - Polling-driven (1800s = 30min default, conservative)
  - Lookback window: 2h default (freshness boundary)
  - Article cap: 10 / poll default
  - Library: stdlib only (urllib + xml.etree.ElementTree + email.utils), no new deps
  - M3.1 Invariant E exception (same justification as Calendar + Weather)
  - Deterministic novelty_id = SHA256(f"{provider}.{url}.{published_at}")[:32]
  - Source-level dedup via in-memory OrderedDict (FIFO, 10000 entries, 0 persistent state)
  - M5.9-3 dedup: type "news_event" NOT in WORLD_QUALIFYING_TYPES,
    so adapter will reject (no InnerLifeEvent created); correct minimal scope
  - type = "news_event" (new type, uses DEFAULT_TYPE_BASELINE_RELEVANCE 0.10)
  - Supports RSS 2.0 (primary) + Atom (secondary) for free
  - Preferred feeds per work order (Reuters + AP) UNAVAILABLE from this machine
    (Reuters discontinued public RSS in 2020; AP blocks bot/scraper access).
    8 well-known public feeds verified working: BBC, NASA, HN, Guardian, Ars, NPR, Al Jazeera
"""
from .synthetic import SyntheticWorldEventSource, SYNTHETIC_TEST_EVENTS
from .calendar_ical import IcalCalendarSource
from .open_meteo import OpenMeteoWeatherSource
from .news_rss import NewsFeedConfig, RssNewsSource, parse_news_feeds_env

__all__ = [
    "SyntheticWorldEventSource",
    "SYNTHETIC_TEST_EVENTS",
    "IcalCalendarSource",  # M5.15-6
    "OpenMeteoWeatherSource",  # M6.1-3.1
    "RssNewsSource",  # M6.1-5.1
    "NewsFeedConfig",  # M6.1-5.1
    "parse_news_feeds_env",  # M6.1-5.1
]
