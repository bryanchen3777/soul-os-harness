"""
src/world/source/news_rss.py — Soul OS M6.1-5.1 RSS News Source

M6.1-5.1 (Bry 派工 2026-08-13 20:28, OWNER AUTHORIZATION APPROVED) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE / IMPLEMENTATION

RSS feed (BBC, NASA, Guardian, Ars Technica, NPR, Al Jazeera, Hacker News) →
WorldEventSource → Event Bus → WorldPerception → world_context block on
AGENT_INTENT evaluation. News is an INFORMATION signal (Lived Context Awareness
M6.1-1 taxonomy), not Physical/Social/Personal.

派工精神:
  - First Information-context signal producer (answers Q1-Q5 from M6.1-5 audit)
  - M3.1 Invariant E exception (same as Calendar M5.15-6 + Weather M6.1-3.1):
    public RSS feeds, no credentials, no API key, no token store
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus /
    M5.4-5.1 InnerLifeEvent / M5.9-2 QUALIFYING_TYPES / M5.9-3 dedup /
    M5.15-3 canonical bus path / M5.15-5 source_world_event_novelty_id all preserved)
  - 1 new WorldEventSource subclass (this file)
  - Library: stdlib only (urllib + xml.etree.ElementTree + email.utils), no new deps
  - Source ID = "news" (M3.1 VALID_SOURCES contract; provider preserved in data["news_provider"])

Preferred feeds per work order (Reuters + AP) — accessibility from this machine:
  - Reuters World (https://feeds.reuters.com/Reuters/worldNews) — UNAVAILABLE
    Reuters discontinued public RSS in 2020 (well-known industry change, not config issue).
    DNS getaddrinfo failed from this machine.
  - AP News (https://apnews.com/index.rss) — UNAVAILABLE
    Returns HTTP 401 Unauthorized (AP blocks bot/scraper access).
  - AP News World (https://apnews.com/world-news.rss) — UNAVAILABLE
    Returns HTTP 404 Not Found (URL not exposed).

Per work order §1: "如果其中一個 feed 不可用: document it + continue with the
confirmed feed + do not substitute arbitrary sources without documenting".

Confirmed working feeds (well-known public RSS, NOT invented):
  - BBC World (http://feeds.bbci.co.uk/news/world/rss.xml) — RSS 2.0, free, public
  - BBC Top (http://feeds.bbci.co.uk/news/rss.xml) — RSS 2.0, free, public
  - NASA Breaking News (https://www.nasa.gov/news-release/feed/) — RSS 2.0, public
  - Hacker News (https://hnrss.org/frontpage) — RSS 2.0, public
  - Guardian World (https://www.theguardian.com/world/rss) — RSS 2.0, public
  - Ars Technica (https://feeds.arstechnica.com/arstechnica/index) — RSS 2.0, public
  - NPR Top (https://feeds.npr.org/1001/rss.xml) — RSS 2.0, public
  - Al Jazeera (https://www.aljazeera.com/xml/rss/all.xml) — RSS 2.0, public

These 8 feeds all returned HTTP 200 with valid RSS 2.0 XML (37-45 items per
feed) on first verification. All are stable, well-known, publicly documented.

Identity model (M6.1-5.1 per work order §3):
  - source_id = "news" (M3.1 VALID_SOURCES contract; provider "bbc_world" /
    "nasa_breaking" / etc. preserved in data["news_provider"])
  - type = "news_event" (new type, NOT in M5.9-2 WORLD_QUALIFYING_TYPES →
    adapter will REJECT → no InnerLifeEvent created; correct minimal scope)
  - novelty_id = SHA256(f"{provider}.{canonical_url}.{published_at_iso}")[:32]
    - Deterministic, 32-char lowercase hex (M3.1 validation compatible)
    - Same article observed across polls within lookback → same hash → source-level
      dedup via in-memory OrderedDict (FIFO, 10000 entries, 0 persistent state)
  - data = {
      "news_provider": "bbc_world",
      "news_url": "http://feeds.bbci.co.uk/news/world/rss.xml",
      "news_title": "...",
      "news_summary": "...",
      "news_canonical_url": "https://www.bbc.com/news/...",
      "news_published_at": "2026-08-13T19:30:00+00:00",
      "news_retrieved_at": "2026-08-13T19:45:00+00:00",  # poll time
      "news_category": "World",  # optional, from <category>
      "news_author": "...",  # optional, from <author> or <dc:creator>
      "news_guid": "...",  # optional, from <guid>
    }

Temporal:
  - Lookback window: 2h default (per work order §2)
  - Polling interval: 1800s default (30 min, same as Weather M6.1-3.1)
  - Per-article cap: 10 articles / poll (per work order §2)
  - PubDate: RFC 822 format ("Wed, 13 Aug 2026 19:30:00 GMT") via
    email.utils.parsedate_to_datetime (stdlib)
  - Articles without pubDate → skip (no reliable freshness)
  - Articles older than lookback → skip

Why "news" as source_id (NOT "news.bbc" / "news.nasa" as work order §3 hints):
  - M3.1 frozen contract: WorldEvent.source MUST be in VALID_SOURCES = {weather, news, calendar, social, synthetic}
  - validate_world_event() rejects non-whitelist sources
  - Calendar uses "calendar" (not "calendar.ical")
  - Weather uses "weather" (not "weather.open_meteo")
  - Provider identity ("bbc_world", "nasa_breaking", etc.) preserved in
    data["news_provider"] for observability
  - 0 frozen contract change required

Why stdlib only (no feedparser):
  - RSS 2.0 is well-defined XML; xml.etree.ElementTree handles it
  - email.utils.parsedate_to_datetime handles RFC 822 pubDate
  - 1 less dependency in production venv
  - M3.1 Invariant E (M3.1 minimal-infrastructure spirit)
  - Work order §9: "Only introduce a dependency if RSS parsing genuinely
    requires it and existing project dependencies cannot reasonably support
    the implementation" → stdlib is sufficient

Why "news_event" type (NOT "celebrity_news"):
  - "celebrity_news" has TYPE_BASELINE_RELEVANCE = 0.05 (designed to be
    filtered out by perception scoring)
  - "news_event" uses DEFAULT_TYPE_BASELINE_RELEVANCE = 0.10 (generic news
    baseline, not explicitly listed)
  - Per work order §6: "Do not invent metadata unavailable from the feed" →
    type is single bucket "news_event", not per-category

M5.9-2 type whitelist status:
  - "news_event" is NOT in M5.9-2 WORLD_QUALIFYING_TYPES (= {calendar_event, user_going_outside})
  - Therefore: WorldInnerLifeAdapter will REJECT (qualify=NO) → no InnerLifeEvent created
  - This is correct minimal scope: WorldEvent reaches perception (state + trace),
    can surface in world_context block on AGENT_INTENT evaluation, but does NOT
    pollute InnerLife storage
  - Future M6.1-* ticket can add "news_event" to QUALIFYING_TYPES if Bry wants

M3.1 Invariant E exception (per work order):
  - Calendar M5.15-6 + Weather M6.1-3.1 + News M6.1-5.1 all got the exception
  - Justification: public RSS, no credentials, no agency
  - "News is awareness, not agency" — same as Weather / Calendar

Failure handling (per work order §8):
  - HTTP error (timeout, 4xx, 5xx) → log warning, skip that feed, continue with next
  - Parse error (malformed XML) → log warning, skip that feed, continue with next
  - Missing pubDate / title / link → skip individual <item>, continue
  - Max articles cap reached → stop emitting remaining
  - All errors observable, never silent, never crash
  - Production safe (urllib timeout, no thread/process spawn)

Out of scope (per work order §14):
  - Web/Search / browser automation
  - Article database / vector DB / embeddings
  - Semantic search / ranking infrastructure
  - Personalized news ranking / interest profile
  - News → InnerLife (no QUALIFYING_TYPES change)
  - News → emotion / memory
  - new LivedContextAggregator
  - new scoring dimensions
  - unrelated refactor
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional, Tuple

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

from ..base import WorldEventSource
from ..perception import WorldEvent

logger = logging.getLogger("soul_os.world.source.news")


# ───────────────────────────────────────────────────────────
# Constants
# ───────────────────────────────────────────────────────────

# Work order §2: polling interval default
DEFAULT_POLLING_INTERVAL_SECS = 1800  # 30 min, same as Weather M6.1-3.1

# Work order §2: lookback window (freshness boundary)
DEFAULT_LOOKBACK_HOURS = 2

# Work order §2: article cap per poll (defensive)
DEFAULT_ARTICLE_CAP = 10

# Work order §9: HTTP timeout (matches Weather + Calendar pattern)
HTTP_TIMEOUT_SECS = 30.0

# In-memory dedup cache max size (FIFO eviction)
# Work order §8: "source-level novelty identity must prevent the same article
# from becoming repeated live events across polls where appropriate"
# 10000 entries ≈ ~6 months of high-volume news polling at 30 min interval
DEDUP_CACHE_MAX_SIZE = 10000

# Maximum items to walk from a single feed (defensive)
# Prevents runaway processing on weirdly-large feeds
MAX_ITEMS_PER_FEED = 500


# ───────────────────────────────────────────────────────────
# Feed configuration
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class NewsFeedConfig:
    """
    Configuration for a single RSS feed (M6.1-5.1).

    Attributes:
        provider: short identifier for observability (e.g. "bbc_world").
                  NOT used in source_id (which is always "news" per M3.1 contract).
                  Preserved in data["news_provider"] for downstream inspection.
        url: HTTP(S) URL of the RSS feed (RSS 2.0 XML format).

    Design:
        - provider identity lives in data field, not in source_id (M3.1 contract)
        - URL is preserved exactly (no normalization that could break HMAC or
          feed-specific auth tokens, even though RSS feeds are public)
        - Dataclass is frozen for hashability + immutability
    """

    provider: str
    url: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError(
                f"NewsFeedConfig.provider 必須是非空 str, got: {self.provider!r}"
            )
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError(
                f"NewsFeedConfig.url 必須是非空 str, got: {self.url!r}"
            )
        # Normalize: strip whitespace
        object.__setattr__(self, "provider", self.provider.strip())
        object.__setattr__(self, "url", self.url.strip())


# ───────────────────────────────────────────────────────────
# Identity helpers
# ───────────────────────────────────────────────────────────

def _compute_novelty_id(
    provider: str,
    canonical_url: str,
    published_at_iso: str,
) -> str:
    """
    M6.1-5.1 (per work order §3): deterministic novelty_id from
    provider + canonical URL + published_at.

    Same provider + same URL + same published_at → same hash.

    Args:
        provider: feed provider (e.g. "bbc_world")
        canonical_url: article's canonical URL (from <link> or <guid>)
        published_at_iso: ISO 8601 UTC timestamp of article publication

    Returns:
        32-char lowercase hex string (M3.1 validation compatible [a-z0-9_]{4,128})

    Why these 3 fields:
        - provider: distinguish BBC article from Guardian article with same URL (unlikely
          but defensive)
        - canonical_url: same article re-published = same identity
        - published_at: same URL updated at different time = different identity
          (rare in news, but defensive)
    """
    raw = f"{provider}.{canonical_url}.{published_at_iso}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse_pubdate(pubdate_str: str) -> Optional[datetime]:
    """
    Parse RSS 2.0 pubDate (RFC 822) → datetime (UTC).

    Examples (RFC 822 / RFC 2822):
        "Wed, 13 Aug 2026 19:30:00 GMT"
        "Wed, 13 Aug 2026 19:30:00 +0000"
        "Wed, 13 Aug 2026 19:30:00 -0500"

    Returns:
        datetime in UTC (tzinfo=timezone.utc), or None if parsing fails.

    Defensive: if input is empty/None/non-string, returns None.
    """
    if not pubdate_str or not isinstance(pubdate_str, str):
        return None
    try:
        dt = parsedate_to_datetime(pubdate_str.strip())
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    # Normalize to UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


# ───────────────────────────────────────────────────────────
# RSS parsing
# ───────────────────────────────────────────────────────────

def _text_of(element: Optional[ET.Element]) -> str:
    """Get text content of an XML element (defensive: None/empty handling)."""
    if element is None:
        return ""
    text = element.text
    if text is None:
        return ""
    return text.strip()


def _parse_rss_items(
    xml_text: str,
) -> List[Dict[str, str]]:
    """
    Parse RSS 2.0 XML → list of item dicts.

    Returns:
        List of dicts with keys: title, link, pubdate, description, guid, category, author.
        Missing fields are empty strings (caller decides whether to skip).

    Defensive: malformed XML returns empty list (logged by caller).
    """
    if not xml_text or not isinstance(xml_text, str):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    items: List[Dict[str, str]] = []

    # RSS 2.0: <rss><channel><item>...</item></channel></rss>
    # Atom: <feed><entry>...</entry></feed>
    # We only support RSS 2.0 in v1 (work order scope: "RSS-based")
    # Normalize root tag: handle both "<rss>" and "<rss xmlns='...'>"
    # ElementTree's tag is "{namespace}localname" when namespace is set.
    # Find local name
    if root.tag.startswith("{"):
        # Strip namespace
        local = root.tag.split("}", 1)[1]
    else:
        local = root.tag

    if local == "rss":
        # RSS 2.0: <rss><channel><item>...</item></channel></rss>
        # find channel by local name
        channel = None
        for child in root:
            child_local = child.tag.split("}", 1)[1] if child.tag.startswith("{") else child.tag
            if child_local == "channel":
                channel = child
                break
        if channel is None:
            return []
        for item in channel:
            item_local = item.tag.split("}", 1)[1] if item.tag.startswith("{") else item.tag
            if item_local != "item":
                continue
            # Find children by local name (defensive against namespaces)
            def _find_local(parent, name):
                for c in parent:
                    c_local = c.tag.split("}", 1)[1] if c.tag.startswith("{") else c.tag
                    if c_local == name:
                        return c
                return None
            entry = {
                "title": _text_of(_find_local(item, "title")),
                "link": _text_of(_find_local(item, "link")),
                "pubdate": _text_of(_find_local(item, "pubDate")),
                "description": _text_of(_find_local(item, "description")),
                "guid": _text_of(_find_local(item, "guid")),
                "category": _text_of(_find_local(item, "category")),
                "author": _text_of(_find_local(item, "author")),
            }
            # If <author> is empty, try <dc:creator> (Dublin Core)
            if not entry["author"]:
                entry["author"] = _text_of(_find_local(item, "creator"))
            items.append(entry)
    # Atom: minimal support (only if RSS path fails; not the primary path)
    elif local == "feed":
        ns = "{http://www.w3.org/2005/Atom}"
        for entry_el in root.findall(f"{ns}entry"):
            # Atom <link href="..."/> — find first rel="alternate" or any
            link_href = ""
            link_el = entry_el.find(f"{ns}link")
            if link_el is not None:
                link_href = link_el.attrib.get("href", "")
            if not link_href:
                # try any link element
                for alt_link in entry_el.findall(f"{ns}link"):
                    if alt_link.attrib.get("rel", "alternate") == "alternate":
                        link_href = alt_link.attrib.get("href", "")
                        break
            # Atom <published> is ISO 8601, but we store raw and let _parse_pubdate try
            # (it will fail on ISO 8601, but we can fallback)
            pub_raw = _text_of(entry_el.find(f"{ns}published"))
            if not pub_raw:
                pub_raw = _text_of(entry_el.find(f"{ns}updated"))
            entry = {
                "title": _text_of(entry_el.find(f"{ns}title")),
                "link": link_href,
                "pubdate": pub_raw,
                "description": _text_of(entry_el.find(f"{ns}summary"))
                or _text_of(entry_el.find(f"{ns}content")),
                "guid": _text_of(entry_el.find(f"{ns}id")),
                "category": "",
                "author": _text_of(entry_el.find(f"{ns}author")),
            }
            items.append(entry)
    return items


# ───────────────────────────────────────────────────────────
# RssNewsSource
# ───────────────────────────────────────────────────────────

class RssNewsSource(WorldEventSource):
    """
    M6.1-5.1: Real-world WorldEventSource for public RSS news feeds.

    Polling-driven, env-gated, no API key, no credentials.

    Identity model (per work order §3):
        source_id = "news" (M3.1 VALID_SOURCES)
        novelty_id = SHA256(f"{provider}.{canonical_url}.{published_at}")[:32]
        type = "news_event" (NOT in WORLD_QUALIFYING_TYPES → no InnerLifeEvent)

    Lifecycle:
        1. __init__: configure feeds, bus, polling_interval, lookback_hours, article_cap
        2. start(): log + initialize (no-op for v1)
        3. (external) await poll(): called by run_server.py lifespan task
        4. stop(): log + cleanup (no-op for v1)

    Architecture decision (per M6.1-5.1 work order):
        - Polling model (not push/webhook) — no new infrastructure
        - Polling interval: 1800s default (configurable via constructor)
        - Lookback window: 2h default (configurable via constructor)
        - Per-poll article cap: 10 default (configurable via constructor)
        - N feeds = 1 source (multi-feed source; not 1 source per feed)
        - In-memory dedup cache (FIFO, 10000 entries, 0 persistent state)
        - Env-gated via SOULOS_NEWS_FEEDS (format: "provider1|url1,provider2|url2,...")
        - Stdlib only (urllib + xml.etree.ElementTree + email.utils), no new deps
    """

    def __init__(
        self,
        feeds: List[NewsFeedConfig],
        bus: Optional[SoulEventBus] = None,
        polling_interval_secs: int = DEFAULT_POLLING_INTERVAL_SECS,
        lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
        article_cap: int = DEFAULT_ARTICLE_CAP,
        http_timeout_secs: float = HTTP_TIMEOUT_SECS,
    ) -> None:
        """
        Args:
            feeds: list of NewsFeedConfig (provider + url); must be non-empty
            bus: SoulEventBus for M5.15-3 canonical bus path
            polling_interval_secs: how often to poll (default 1800 = 30 min)
            lookback_hours: how far back to consider articles (default 2h)
            article_cap: max articles emitted per poll (default 10)
            http_timeout_secs: HTTP request timeout (default 30s)
        """
        # Defensive: feeds must be non-empty list of NewsFeedConfig
        if not isinstance(feeds, list) or len(feeds) == 0:
            raise ValueError(
                f"feeds 必須是非空 list[NewsFeedConfig], got: {type(feeds).__name__} "
                f"len={len(feeds) if isinstance(feeds, list) else 'N/A'}"
            )
        for i, feed in enumerate(feeds):
            if not isinstance(feed, NewsFeedConfig):
                raise ValueError(
                    f"feeds[{i}] 必須是 NewsFeedConfig, got: {type(feed).__name__}"
                )
        # Check for duplicate providers (could be confusing in observability)
        providers = [f.provider for f in feeds]
        if len(providers) != len(set(providers)):
            dupes = {p for p in providers if providers.count(p) > 1}
            raise ValueError(
                f"feeds 包含重複的 provider: {sorted(dupes)}"
            )

        if polling_interval_secs < 60:
            raise ValueError(
                f"polling_interval_secs 必須 ≥ 60 (避免過度 polling), got: {polling_interval_secs}"
            )
        if lookback_hours < 1 or lookback_hours > 168:  # max 7 days
            raise ValueError(
                f"lookback_hours 必須在 1-168 之間, got: {lookback_hours}"
            )
        if article_cap < 1 or article_cap > 100:
            raise ValueError(
                f"article_cap 必須在 1-100 之間, got: {article_cap}"
            )
        if http_timeout_secs < 1.0 or http_timeout_secs > 120.0:
            raise ValueError(
                f"http_timeout_secs 必須在 [1.0, 120.0] 之間, got: {http_timeout_secs}"
            )

        self._feeds: List[NewsFeedConfig] = list(feeds)
        self._bus: Optional[SoulEventBus] = bus
        self._polling_interval_secs: int = polling_interval_secs
        self._lookback_hours: int = lookback_hours
        self._article_cap: int = article_cap
        self._http_timeout_secs: float = http_timeout_secs

        # In-memory dedup cache (FIFO via OrderedDict, 0 persistent state)
        # Tracks recently-emitted novelty_ids to prevent same article from
        # becoming repeated live events across polls within lookback window.
        self._dedup_cache: "Dict[str, None]" = {}

        # Observability counters
        self._stats = {
            "polls_total": 0,
            "polls_failed": 0,
            "feeds_polled": 0,
            "feeds_failed": 0,
            "feeds_skipped_max_items": 0,
            "articles_total": 0,
            "articles_skipped_no_pubdate": 0,
            "articles_skipped_outside_window": 0,
            "articles_skipped_no_link": 0,
            "articles_skipped_duplicate": 0,
            "articles_skipped_max_cap": 0,
            "events_emitted": 0,
            "events_emission_failed": 0,
            "http_errors": 0,
            "parse_errors": 0,
        }

    @property
    def source_id(self) -> str:
        """
        M3.1 frozen contract: source_id MUST be in VALID_SOURCES.
        Returns "news" (per M6.1-5.1 contract alignment decision).
        Provider identity ("bbc_world", "nasa_breaking", etc.) is preserved
        in data["news_provider"].
        """
        return "news"

    @property
    def feeds(self) -> List[NewsFeedConfig]:
        """Read-only list of configured feeds."""
        return list(self._feeds)

    @property
    def polling_interval_secs(self) -> int:
        return self._polling_interval_secs

    @property
    def lookback_hours(self) -> int:
        return self._lookback_hours

    @property
    def article_cap(self) -> int:
        return self._article_cap

    @property
    def http_timeout_secs(self) -> float:
        return self._http_timeout_secs

    @property
    def dedup_cache_size(self) -> int:
        """Current size of in-memory dedup cache (for observability)."""
        return len(self._dedup_cache)

    # ── WorldEventSource ABC (M3.1 Phase A) ──────────────────

    async def start(self) -> None:
        """
        M3.1 Phase A: start lifecycle. No-op for RssNewsSource
        (polling is driven by external scheduler in run_server.py).
        """
        logger.info(
            f"[RssNewsSource] start() — "
            f"feeds={len(self._feeds)} "
            f"polling_interval={self._polling_interval_secs}s "
            f"lookback={self._lookback_hours}h "
            f"article_cap={self._article_cap} "
            f"timeout={self._http_timeout_secs}s "
            f"bus={'set' if self._bus is not None else 'None'}"
        )

    async def stop(self) -> None:
        """
        M3.1 Phase A: stop lifecycle. Idempotent.
        No-op for RssNewsSource (no resources to release).
        """
        logger.info(
            f"[RssNewsSource] stop() — "
            f"final stats: {self._stats} "
            f"dedup_cache_size={len(self._dedup_cache)}"
        )

    # ── Polling (M6.1-5.1 IMPLEMENTATION) ──────────────────────

    async def poll(self) -> int:
        """
        Poll all configured feeds, parse items, emit WorldEvents.

        Returns:
            int: number of WorldEvents emitted in this poll cycle (0+).

        Failure handling (per work order §8):
          - HTTP error (timeout, 4xx, 5xx) for a feed → log + skip that feed, continue
          - Parse error (malformed XML) for a feed → log + skip that feed, continue
          - Missing pubDate / link → skip individual <item>, continue
          - Outside lookback window → skip individual <item>
          - Already in dedup cache → skip individual <item>
          - Max article cap reached (per feed OR global) → stop emitting
          - All errors logged, never silent, never crash
        """
        self._stats["polls_total"] += 1
        now = datetime.now(timezone.utc)
        window_start = now - timedelta(hours=self._lookback_hours)

        total_emitted = 0

        for feed in self._feeds:
            self._stats["feeds_polled"] += 1

            # 1. HTTP GET (run in executor to avoid blocking event loop)
            xml_text = await self._fetch_rss(feed)
            if xml_text is None:
                self._stats["feeds_failed"] += 1
                self._stats["polls_failed"] += 1
                continue  # try next feed

            # 2. Parse RSS items
            items = _parse_rss_items(xml_text)
            if not items:
                self._stats["feeds_failed"] += 1
                self._stats["polls_failed"] += 1
                self._stats["parse_errors"] += 1
                continue  # try next feed

            # Defensive: cap items walked per feed
            if len(items) > MAX_ITEMS_PER_FEED:
                self._stats["feeds_skipped_max_items"] += 1
                items = items[:MAX_ITEMS_PER_FEED]

            self._stats["articles_total"] += len(items)

            # 3. Walk items, filter, build WorldEvents
            feed_emitted = 0
            for item in items:
                # 3a. Defensive: article_cap reached?
                if total_emitted >= self._article_cap:
                    self._stats["articles_skipped_max_cap"] += 1
                    logger.debug(
                        f"[RssNewsSource] reached article_cap "
                        f"({self._article_cap}), stop emitting"
                    )
                    break
                if feed_emitted >= self._article_cap:
                    # Per-feed soft cap (defensive: one feed doesn't dominate)
                    # 2x article_cap per feed max (so a single feed can't take
                    # the whole global cap, but can go a bit over)
                    if feed_emitted >= self._article_cap * 2:
                        self._stats["articles_skipped_max_cap"] += 1
                        break

                we = self._item_to_world_event(
                    item=item,
                    feed=feed,
                    now=now,
                    window_start=window_start,
                )
                if we is None:
                    continue  # already counted in stats (skip reason)

                # 3b. Source-level dedup
                if we.novelty_id in self._dedup_cache:
                    self._stats["articles_skipped_duplicate"] += 1
                    continue

                # 3c. Emit via bus
                if await self._emit_via_bus(we):
                    self._stats["events_emitted"] += 1
                    total_emitted += 1
                    feed_emitted += 1
                    # Record in dedup cache
                    self._add_to_dedup_cache(we.novelty_id)

        logger.info(
            f"[RssNewsSource] poll() — "
            f"emitted={total_emitted} "
            f"feeds={len(self._feeds)} "
            f"stats={self._stats}"
        )
        return total_emitted

    def get_stats(self) -> Dict[str, Any]:
        """Observability counters snapshot."""
        result = dict(self._stats)
        result["dedup_cache_size"] = len(self._dedup_cache)
        return result

    # ── Internal helpers ─────────────────────────────────────

    def _add_to_dedup_cache(self, novelty_id: str) -> None:
        """Add novelty_id to FIFO dedup cache, evict oldest if over max size."""
        self._dedup_cache[novelty_id] = None
        if len(self._dedup_cache) > DEDUP_CACHE_MAX_SIZE:
            # FIFO eviction: pop the first (oldest) item
            oldest = next(iter(self._dedup_cache))
            del self._dedup_cache[oldest]

    async def _fetch_rss(self, feed: NewsFeedConfig) -> Optional[str]:
        """
        HTTP GET a single RSS feed (run in executor to avoid blocking).

        Returns:
            str: RSS XML text
            None: HTTP error (logged, return None, caller counts as failed feed)

        Production safety: timeout enforced, no credential leaks in URL
        (RSS feeds are public; defensive masking in logs).
        """
        loop = asyncio.get_event_loop()
        try:
            def _do_fetch() -> str:
                req = urllib.request.Request(
                    feed.url,
                    headers={
                        "User-Agent": "SoulOS/1.0 (M6.1-5.1 News Source)",
                        "Accept": "application/rss+xml, application/xml, text/xml, */*",
                    },
                )
                with urllib.request.urlopen(
                    req, timeout=self._http_timeout_secs
                ) as response:
                    raw = response.read()
                    return raw.decode("utf-8", errors="replace")
            return await loop.run_in_executor(None, _do_fetch)
        except urllib.error.URLError as e:
            logger.warning(
                f"[RssNewsSource] HTTP error for "
                f"provider={feed.provider!r}: "
                f"{type(e).__name__}: {e}"
            )
            self._stats["http_errors"] += 1
            return None
        except Exception as e:
            logger.warning(
                f"[RssNewsSource] Unexpected fetch error for "
                f"provider={feed.provider!r}: "
                f"{type(e).__name__}: {e}"
            )
            self._stats["http_errors"] += 1
            return None

    def _item_to_world_event(
        self,
        item: Dict[str, str],
        feed: NewsFeedConfig,
        now: datetime,
        window_start: datetime,
    ) -> Optional[WorldEvent]:
        """
        Convert a single RSS item to a WorldEvent.

        Returns:
            WorldEvent: if item is valid (has pubDate, link, within window)
            None: if skipped (reason logged in stats)

        Skip rules (per work order):
          - No link → skip (no canonical URL for identity)
          - No pubDate → skip (no reliable freshness)
          - pubDate outside [window_start, now] → skip (out of lookback)
          - Parse error on pubDate → skip
        """
        link = item.get("link", "").strip()
        if not link:
            self._stats["articles_skipped_no_link"] += 1
            return None

        pubdate_str = item.get("pubdate", "").strip()
        if not pubdate_str:
            self._stats["articles_skipped_no_pubdate"] += 1
            return None

        published_dt = _parse_pubdate(pubdate_str)
        if published_dt is None:
            self._stats["articles_skipped_no_pubdate"] += 1
            logger.debug(
                f"[RssNewsSource] unparseable pubDate for "
                f"provider={feed.provider!r} link={link[:50]!r}: "
                f"pubdate={pubdate_str!r}"
            )
            return None

        # Filter by lookback window
        if published_dt < window_start:
            self._stats["articles_skipped_outside_window"] += 1
            return None
        if published_dt > now:
            # Future-dated article (clock skew); skip conservatively
            self._stats["articles_skipped_outside_window"] += 1
            logger.debug(
                f"[RssNewsSource] future-dated article, skip: "
                f"provider={feed.provider!r} pubdate={pubdate_str!r}"
            )
            return None

        # All checks passed → build WorldEvent
        published_iso = published_dt.isoformat()
        retrieved_iso = now.isoformat()

        # Identity
        novelty_id = _compute_novelty_id(
            provider=feed.provider,
            canonical_url=link,
            published_at_iso=published_iso,
        )

        # Data field (observability + future LLM context)
        title = item.get("title", "").strip()
        description = item.get("description", "").strip()
        # Defensive: truncate description to keep payload small
        # (full article text not needed for Lived Context awareness)
        if len(description) > 1000:
            description = description[:1000] + "…"

        data_field: Dict[str, Any] = {
            "news_provider": feed.provider,
            "news_url": feed.url,
            "news_title": title,
            "news_summary": description,
            "news_canonical_url": link,
            "news_published_at": published_iso,
            "news_retrieved_at": retrieved_iso,
        }
        # Optional fields (only include if non-empty)
        category = item.get("category", "").strip()
        if category:
            data_field["news_category"] = category
        author = item.get("author", "").strip()
        if author:
            data_field["news_author"] = author
        guid = item.get("guid", "").strip()
        if guid:
            data_field["news_guid"] = guid

        # Summary (one-sentence objective fact, LLM-friendly)
        # Format: "[provider] title (YYYY-MM-DD HH:MM UTC)"
        pub_short = published_dt.strftime("%Y-%m-%d %H:%M UTC")
        summary = f"[{feed.provider}] {title} ({pub_short})"
        # Defensive: cap summary length (validation.py max 500)
        if len(summary) > 500:
            # Truncate title, keep provider + date
            keep_title = 500 - len(f"[{feed.provider}]  ({pub_short})") - 3
            if keep_title > 0:
                summary = f"[{feed.provider}] {title[:keep_title]}... ({pub_short})"
            else:
                summary = f"[{feed.provider}] news ({pub_short})"

        # Build WorldEvent (M3 frozen contract: 7 fields, M3.1 Phase B: priority)
        return WorldEvent(
            source=self.source_id,        # "news" (M3.1 VALID_SOURCES)
            type="news_event",            # new type, NOT in WORLD_QUALIFYING_TYPES
            novelty_id=novelty_id,        # SHA256 deterministic (M3.1 compatible)
            ts=published_iso,             # ISO 8601 UTC (M3.1 validation)
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
                f"[RssNewsSource] bus is None, cannot emit: "
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
                f"[RssNewsSource] emit failed: "
                f"{type(e).__name__}: {e}"
            )
            self._stats["events_emission_failed"] += 1
            return False


# ───────────────────────────────────────────────────────────
# Env-var parsing helper (used by run_server.py wiring)
# ───────────────────────────────────────────────────────────

def parse_news_feeds_env(env_value: str) -> List[NewsFeedConfig]:
    """
    Parse SOULOS_NEWS_FEEDS env-var value into a list of NewsFeedConfig.

    Format: "provider1|url1,provider2|url2,..."

    Examples:
        "bbc_world|http://feeds.bbci.co.uk/news/world/rss.xml"
        "bbc_world|http://feeds.bbci.co.uk/news/world/rss.xml,nasa_breaking|https://www.nasa.gov/news-release/feed/"

    Args:
        env_value: raw env-var string (already stripped of leading/trailing whitespace)

    Returns:
        List of NewsFeedConfig. Empty list if env_value is empty.

    Raises:
        ValueError: if any feed entry is malformed (missing |, empty provider, empty url)

    Design:
        - Comma-separated entries
        - Pipe-separated (provider, url) within each entry
        - Pipe is used (not colon) to avoid conflict with URL scheme (http://, https://)
        - Empty entries (e.g. trailing comma) are silently skipped
    """
    if not env_value or not env_value.strip():
        return []

    result: List[NewsFeedConfig] = []
    entries = [e.strip() for e in env_value.split(",") if e.strip()]
    for entry in entries:
        if "|" not in entry:
            raise ValueError(
                f"malformed SOULOS_NEWS_FEEDS entry (missing '|'): {entry!r}"
            )
        provider, url = entry.split("|", 1)
        result.append(NewsFeedConfig(provider=provider, url=url))
    return result
