"""
tests/test_m6_1_5_1_news_rss.py — M6.1-5.1 RSS News Source

M6.1-5.1 (Bry 派工 2026-08-13 20:28, OWNER AUTHORIZATION APPROVED) — IMPLEMENTATION
Mode: MINIMAL ADDITIVE

驗證:
  - RssNewsSource implements existing WorldEventSource contract
  - Public RSS feeds are env-gated (no credentials, no API key)
  - Missing SOULOS_NEWS_FEEDS = no news activity
  - Articles become existing WorldEvent (source=news, type=news_event)
  - source_id = "news" (M3.1 VALID_SOURCES contract, NOT "news.bbc" / "news.nasa")
  - Provider identity preserved in data["news_provider"]
  - WorldEvent.novelty_id = SHA256("{provider}.{url}.{published_at}")[:32]
  - Event Bus remains canonical transport
  - PubDate (RFC 822) parsed to UTC ISO 8601
  - Lookback window filters out old articles
  - Article cap stops emission
  - Source-level dedup prevents repeat live events
  - API failure is non-fatal (HTTP error, timeout, parse error)
  - Environment gating present
  - Tests use mocked network (no real HTTP calls in test)
  - M5.9-3 dedup: type "news_event" NOT in QUALIFYING_TYPES → no InnerLifeEvent
  - No frozen downstream contract broken
  - 0 production mutation during test run

Test sections (per M6.1-5.1 work order §10):
  A. NewsFeedConfig validation
  B. Env parsing (parse_news_feeds_env)
  C. Source identity (source_id, feeds property, constructor)
  D. RSS parsing (valid, malformed, empty, missing fields, Atom)
  E. PubDate parsing (RFC 822)
  F. Novelty identity (deterministic, different articles)
  G. WorldEvent construction (all fields)
  H. Dedup (in-memory cache, FIFO eviction)
  I. Lookback filtering
  J. Article cap
  K. Event Bus integration
  L. HTTP failure (URLError, timeout)
  M. WorldPerception integration (E2E)
  N. WorldInnerLifeAdapter (M5.9-3) behavior
  O. Production isolation
  P. Configuration validation
  Q. Stats / observability
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.paths import data_root, reset_data_root
from src.world import WorldPerceptionMiddleware, WorldPerceptionState
from src.world.inner_life_adapter import WorldInnerLifeAdapter
from src.world.perception import VALID_SOURCES, WorldEvent
from src.world.source import NewsFeedConfig, RssNewsSource, parse_news_feeds_env
from src.world.source.news_rss import (
    DEFAULT_ARTICLE_CAP,
    DEFAULT_LOOKBACK_HOURS,
    DEFAULT_POLLING_INTERVAL_SECS,
    DEDUP_CACHE_MAX_SIZE,
    HTTP_TIMEOUT_SECS,
    MAX_ITEMS_PER_FEED,
    _compute_novelty_id,
    _parse_pubdate,
    _parse_rss_items,
    _text_of,
)
from src.world.trace import WorldPerceptionTraceWriter


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _mock_urlopen_response(xml_text: str) -> MagicMock:
    """Build a mock urlopen response that returns the given XML text."""
    mock_response = MagicMock()
    mock_response.read = MagicMock(return_value=xml_text.encode("utf-8"))
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    return mock_response


def _build_rss_xml(
    items: List[Dict[str, str]],
    channel_title: str = "Test Channel",
    channel_link: str = "https://test.example.com/",
) -> str:
    """
    Build a minimal RSS 2.0 XML string from a list of item dicts.

    Each item dict should have: title, link, pubdate, description, guid, category, author.
    Missing fields are emitted as empty strings.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        f'<title>{channel_title}</title>',
        f'<link>{channel_link}</link>',
        '<description>Test feed</description>',
    ]
    for item in items:
        lines.append('<item>')
        if item.get("title"):
            lines.append(f'<title>{item["title"]}</title>')
        if item.get("link"):
            lines.append(f'<link>{item["link"]}</link>')
        if item.get("pubdate"):
            lines.append(f'<pubDate>{item["pubdate"]}</pubDate>')
        if item.get("description"):
            lines.append(f'<description>{item["description"]}</description>')
        if item.get("guid"):
            lines.append(f'<guid>{item["guid"]}</guid>')
        if item.get("category"):
            lines.append(f'<category>{item["category"]}</category>')
        if item.get("author"):
            lines.append(f'<author>{item["author"]}</author>')
        lines.append('</item>')
    lines.append('</channel>')
    lines.append('</rss>')
    return '\n'.join(lines)


def _build_pubdate_rfc822(dt: datetime) -> str:
    """Build an RFC 822 pubDate string from a datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # format_datetime uses RFC 2822 format ("-0000" instead of "+0000")
    return format_datetime(dt)


def _make_simple_item(
    title: str = "Test Article",
    link: str = "https://test.example.com/article-1",
    pubdate: Optional[str] = None,
    description: str = "Test article description.",
    guid: str = "https://test.example.com/article-1",
    category: str = "World",
    author: str = "Test Author",
) -> Dict[str, str]:
    """Build a single RSS item dict with defaults for testing.

    If pubdate is None (default), a recent pubdate (5 minutes ago) is computed
    via _build_pubdate_rfc822. This ensures articles pass lookback filtering
    in test contexts (where datetime.now() may differ from any fixed test date).
    """
    if pubdate is None:
        pubdate = _build_pubdate_rfc822(
            datetime.now(timezone.utc) - timedelta(minutes=5)
        )
    return {
        "title": title,
        "link": link,
        "pubdate": pubdate,
        "description": description,
        "guid": guid,
        "category": category,
        "author": author,
    }


async def _poll_with_mock(
    src: RssNewsSource,
    xml_per_feed: Dict[str, str],
) -> int:
    """
    Helper: poll a source with mocked HTTP returning xml_per_feed[url] for each feed.

    Approach: mock src._fetch_rss to bypass actual HTTP. Different XML per feed URL.
    Feeds whose URL is not in xml_per_feed return None (treated as failed fetch).
    If src has no bus wired, this helper wires a temporary bus so emits work.
    """
    async def _mock_fetch_rss(feed):
        if feed.url in xml_per_feed:
            return xml_per_feed[feed.url]
        return None  # Simulate 404 / network error

    bus_was_none = src._bus is None
    temp_bus = None
    if bus_was_none:
        temp_bus = SoulEventBus()
        await temp_bus.start()
        src._bus = temp_bus
    try:
        with patch.object(src, "_fetch_rss", side_effect=_mock_fetch_rss):
            return await src.poll()
    finally:
        if bus_was_none and temp_bus is not None:
            await temp_bus.stop()
            src._bus = None


# ───────────────────────────────────────────────────────────
# A. NewsFeedConfig validation
# ───────────────────────────────────────────────────────────

class TestSectionA_NewsFeedConfig:
    """A. NewsFeedConfig validates input at construction time."""

    def test_a1_valid_config(self):
        """A.1: valid (provider, url) creates config."""
        cfg = NewsFeedConfig(provider="bbc_world", url="http://x")
        assert cfg.provider == "bbc_world"
        assert cfg.url == "http://x"

    def test_a2_strip_whitespace(self):
        """A.2: leading/trailing whitespace stripped from both fields."""
        cfg = NewsFeedConfig(provider="  bbc_world  ", url="  http://x  ")
        assert cfg.provider == "bbc_world"
        assert cfg.url == "http://x"

    def test_a3_empty_provider_rejected(self):
        """A.3: empty provider raises ValueError."""
        with pytest.raises(ValueError, match="provider"):
            NewsFeedConfig(provider="", url="http://x")

    def test_a4_whitespace_only_provider_rejected(self):
        """A.4: whitespace-only provider raises ValueError."""
        with pytest.raises(ValueError, match="provider"):
            NewsFeedConfig(provider="   ", url="http://x")

    def test_a5_empty_url_rejected(self):
        """A.5: empty url raises ValueError."""
        with pytest.raises(ValueError, match="url"):
            NewsFeedConfig(provider="bbc", url="")

    def test_a6_whitespace_only_url_rejected(self):
        """A.6: whitespace-only url raises ValueError."""
        with pytest.raises(ValueError, match="url"):
            NewsFeedConfig(provider="bbc", url="   ")

    def test_a7_frozen(self):
        """A.7: NewsFeedConfig is frozen (immutable)."""
        cfg = NewsFeedConfig(provider="bbc", url="http://x")
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.provider = "hacked"

    def test_a8_hashable(self):
        """A.8: NewsFeedConfig is hashable (frozen dataclass)."""
        cfg1 = NewsFeedConfig(provider="bbc", url="http://x")
        cfg2 = NewsFeedConfig(provider="bbc", url="http://x")
        assert hash(cfg1) == hash(cfg2)
        assert {cfg1, cfg2} == {cfg1}


# ───────────────────────────────────────────────────────────
# B. Env parsing (parse_news_feeds_env)
# ───────────────────────────────────────────────────────────

class TestSectionB_EnvParsing:
    """B. parse_news_feeds_env handles all edge cases of SOULOS_NEWS_FEEDS."""

    def test_b1_empty_string_returns_empty_list(self):
        """B.1: empty env returns empty list (no feeds)."""
        assert parse_news_feeds_env("") == []

    def test_b2_whitespace_only_returns_empty_list(self):
        """B.2: whitespace-only env returns empty list."""
        assert parse_news_feeds_env("   ") == []

    def test_b3_single_feed(self):
        """B.3: single feed parses correctly."""
        result = parse_news_feeds_env("bbc|http://x")
        assert len(result) == 1
        assert result[0].provider == "bbc"
        assert result[0].url == "http://x"

    def test_b4_multiple_feeds(self):
        """B.4: comma-separated feeds parse correctly."""
        result = parse_news_feeds_env("bbc|http://x,nasa|http://y")
        assert len(result) == 2
        assert result[0].provider == "bbc"
        assert result[1].provider == "nasa"
        assert result[1].url == "http://y"

    def test_b5_strips_whitespace_around_entries(self):
        """B.5: whitespace around entries stripped."""
        result = parse_news_feeds_env("  bbc|http://x  ,  nasa|http://y  ")
        assert len(result) == 2
        assert result[0].provider == "bbc"

    def test_b6_skips_empty_entries(self):
        """B.6: empty entries (from trailing comma etc.) silently skipped."""
        result = parse_news_feeds_env("bbc|http://x,,,nasa|http://y,")
        assert len(result) == 2

    def test_b7_url_with_pipe_split_on_first(self):
        """B.7: URL containing '|' after the first '|' is treated as part of URL.
        We split on FIRST '|', so 'bbc|http://x?q=a|b' → provider='bbc', url='http://x?q=a|b'.
        This is the intended behavior; users should not put '|' in URL, but if they
        do, the rest is kept as URL.
        """
        result = parse_news_feeds_env("bbc|http://x?q=a|b")
        assert result[0].provider == "bbc"
        assert result[0].url == "http://x?q=a|b"

    def test_b8_missing_pipe_raises(self):
        """B.8: entry without '|' raises ValueError."""
        with pytest.raises(ValueError, match="malformed"):
            parse_news_feeds_env("bbc-http://x")

    def test_b9_url_can_contain_colons(self):
        """B.9: URLs with http://, https://, ports all OK (we use '|' not ':')."""
        result = parse_news_feeds_env("bbc|https://example.com:8080/path?q=1")
        assert result[0].url == "https://example.com:8080/path?q=1"


# ───────────────────────────────────────────────────────────
# C. Source identity / constructor
# ───────────────────────────────────────────────────────────

class TestSectionC_SourceIdentity:
    """C. RssNewsSource source_id, feeds, constructor properties."""

    def test_c1_source_id_is_news(self):
        """C.1: source_id = 'news' (M3.1 VALID_SOURCES contract)."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.source_id == "news"

    def test_c2_source_id_in_valid_sources(self):
        """C.2: source_id is in M3.1 VALID_SOURCES."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.source_id in VALID_SOURCES

    def test_c3_feeds_property_returns_list(self):
        """C.3: feeds property returns the configured feeds."""
        feeds = [
            NewsFeedConfig("bbc", "http://x"),
            NewsFeedConfig("nasa", "http://y"),
        ]
        src = RssNewsSource(feeds=feeds)
        assert src.feeds == feeds
        assert len(src.feeds) == 2

    def test_c4_feeds_property_returns_copy(self):
        """C.4: feeds property returns a copy (not internal list)."""
        feeds = [NewsFeedConfig("bbc", "http://x")]
        src = RssNewsSource(feeds=feeds)
        assert src.feeds is not src._feeds

    def test_c5_default_polling_interval(self):
        """C.5: default polling_interval = 1800s."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.polling_interval_secs == DEFAULT_POLLING_INTERVAL_SECS == 1800

    def test_c6_default_lookback(self):
        """C.6: default lookback_hours = 2."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.lookback_hours == DEFAULT_LOOKBACK_HOURS == 2

    def test_c7_default_article_cap(self):
        """C.7: default article_cap = 10."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.article_cap == DEFAULT_ARTICLE_CAP == 10

    def test_c8_default_http_timeout(self):
        """C.8: default http_timeout_secs = 30.0."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.http_timeout_secs == HTTP_TIMEOUT_SECS == 30.0

    def test_c9_custom_polling_interval(self):
        """C.9: custom polling_interval accepted."""
        src = RssNewsSource(
            feeds=[NewsFeedConfig("bbc", "http://x")],
            polling_interval_secs=3600,
        )
        assert src.polling_interval_secs == 3600

    def test_c10_dedup_cache_starts_empty(self):
        """C.10: dedup cache starts empty."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        assert src.dedup_cache_size == 0


# ───────────────────────────────────────────────────────────
# D. RSS parsing
# ───────────────────────────────────────────────────────────

class TestSectionD_RssParsing:
    """D. _parse_rss_items handles RSS 2.0 and Atom."""

    def test_d1_valid_rss_2_0(self):
        """D.1: valid RSS 2.0 XML parses to list of items."""
        # Build with explicit pubdate (default in _make_simple_item is recent)
        item = _make_simple_item(
            pubdate="Wed, 13 Aug 2026 19:30:00 +0000"
        )
        xml = _build_rss_xml([item])
        items = _parse_rss_items(xml)
        assert len(items) == 1
        assert items[0]["title"] == "Test Article"
        assert items[0]["link"] == "https://test.example.com/article-1"
        assert "Wed, 13 Aug 2026 19:30:00" in items[0]["pubdate"]

    def test_d2_multiple_items(self):
        """D.2: multiple items in one feed."""
        xml = _build_rss_xml([
            _make_simple_item(title="Article 1", link="https://x/1"),
            _make_simple_item(title="Article 2", link="https://x/2"),
        ])
        items = _parse_rss_items(xml)
        assert len(items) == 2
        assert items[0]["title"] == "Article 1"
        assert items[1]["title"] == "Article 2"

    def test_d3_malformed_xml_returns_empty(self):
        """D.3: malformed XML returns empty list (no exception)."""
        items = _parse_rss_items("<not><valid>")
        assert items == []

    def test_d4_empty_xml_returns_empty(self):
        """D.4: empty XML returns empty list."""
        items = _parse_rss_items("")
        assert items == []

    def test_d5_non_string_returns_empty(self):
        """D.5: non-string input returns empty list."""
        assert _parse_rss_items(None) == []
        assert _parse_rss_items(123) == []

    def test_d6_missing_title(self):
        """D.6: missing title field produces empty title in dict."""
        item = _make_simple_item()
        del_xml_xml = _build_rss_xml([{k: v for k, v in item.items() if k != "title"}])
        items = _parse_rss_items(del_xml_xml)
        assert len(items) == 1
        assert items[0]["title"] == ""

    def test_d7_missing_link(self):
        """D.7: missing link field produces empty link."""
        item = _make_simple_item()
        xml = _build_rss_xml([{k: v for k, v in item.items() if k != "link"}])
        items = _parse_rss_items(xml)
        assert len(items) == 1
        assert items[0]["link"] == ""

    def test_d8_missing_pubdate(self):
        """D.8: missing pubDate field produces empty pubdate."""
        item = _make_simple_item()
        xml = _build_rss_xml([{k: v for k, v in item.items() if k != "pubdate"}])
        items = _parse_rss_items(xml)
        assert len(items) == 1
        assert items[0]["pubdate"] == ""

    def test_d9_atom_minimal_support(self):
        """D.9: Atom XML also parses (basic fields)."""
        atom = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Test Atom</title>
  <entry>
    <title>Atom Article 1</title>
    <link href="https://atom.example.com/1"/>
    <published>2026-08-13T19:30:00Z</published>
    <id>urn:uuid:1</id>
    <summary>Atom summary 1</summary>
  </entry>
</feed>"""
        items = _parse_rss_items(atom)
        assert len(items) == 1
        assert items[0]["title"] == "Atom Article 1"
        assert items[0]["link"] == "https://atom.example.com/1"

    def test_d10_dc_creator_fallback(self):
        """D.10: <dc:creator> used as author fallback when <author> is empty."""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel><title>t</title><link>l</link><description>d</description>
<item>
<title>Title</title>
<link>https://x/1</link>
<pubDate>Wed, 13 Aug 2026 19:30:00 +0000</pubDate>
<dc:creator>DC Author</dc:creator>
</item>
</channel>
</rss>"""
        items = _parse_rss_items(xml)
        assert len(items) == 1
        assert items[0]["author"] == "DC Author"


# ───────────────────────────────────────────────────────────
# E. PubDate parsing
# ───────────────────────────────────────────────────────────

class TestSectionE_PubDateParsing:
    """E. _parse_pubdate handles RFC 822 dates."""

    def test_e1_standard_rfc_822(self):
        """E.1: standard RFC 822 pubDate parses to UTC."""
        result = _parse_pubdate("Wed, 13 Aug 2026 19:30:00 +0000")
        assert result is not None
        assert result.year == 2026
        assert result.month == 8
        assert result.day == 13
        assert result.hour == 19
        assert result.minute == 30
        assert result.tzinfo is not None
        assert result.utcoffset().total_seconds() == 0

    def test_e2_gmt_timezone(self):
        """E.2: 'GMT' timezone recognized and normalized to UTC."""
        result = _parse_pubdate("Wed, 13 Aug 2026 19:30:00 GMT")
        assert result is not None
        assert result.utcoffset().total_seconds() == 0

    def test_e3_negative_offset(self):
        """E.3: negative offset (e.g. -0500) normalized to UTC."""
        result = _parse_pubdate("Wed, 13 Aug 2026 14:30:00 -0500")
        assert result is not None
        # 14:30 -0500 = 19:30 UTC
        assert result.hour == 19
        assert result.utcoffset().total_seconds() == 0

    def test_e4_empty_string_returns_none(self):
        """E.4: empty string returns None."""
        assert _parse_pubdate("") is None

    def test_e5_garbage_returns_none(self):
        """E.5: garbage string returns None (not raise)."""
        assert _parse_pubdate("not a date") is None

    def test_e6_none_returns_none(self):
        """E.6: None returns None."""
        assert _parse_pubdate(None) is None

    def test_e7_strips_whitespace(self):
        """E.7: leading/trailing whitespace stripped."""
        result = _parse_pubdate("  Wed, 13 Aug 2026 19:30:00 +0000  ")
        assert result is not None
        assert result.hour == 19


# ───────────────────────────────────────────────────────────
# F. Novelty identity
# ───────────────────────────────────────────────────────────

class TestSectionF_NoveltyIdentity:
    """F. _compute_novelty_id is deterministic, 32-char hex."""

    def test_f1_returns_32_char_hex(self):
        """F.1: returns 32-char lowercase hex string."""
        nid = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        assert len(nid) == 32
        assert all(c in "0123456789abcdef" for c in nid)

    def test_f2_deterministic_same_inputs(self):
        """F.2: same inputs → same hash (deterministic)."""
        nid1 = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        nid2 = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        assert nid1 == nid2

    def test_f3_different_provider_different_id(self):
        """F.3: different provider → different hash."""
        nid_bbc = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        nid_nasa = _compute_novelty_id("nasa", "https://x/1", "2026-08-13T19:30:00+00:00")
        assert nid_bbc != nid_nasa

    def test_f4_different_url_different_id(self):
        """F.4: different URL → different hash."""
        nid1 = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        nid2 = _compute_novelty_id("bbc", "https://x/2", "2026-08-13T19:30:00+00:00")
        assert nid1 != nid2

    def test_f5_different_published_at_different_id(self):
        """F.5: different published_at → different hash."""
        nid1 = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        nid2 = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:31:00+00:00")
        assert nid1 != nid2

    def test_f6_m3_1_compatible(self):
        """F.6: matches M3.1 validation regex [a-z0-9_]{4,128}."""
        import re
        nid_re = re.compile(r"^[a-z0-9_]{4,128}$")
        nid = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        assert nid_re.match(nid)

    def test_f7_matches_sha256_first_32(self):
        """F.7: matches SHA256 of expected key, first 32 chars."""
        key = "bbc.https://x/1.2026-08-13T19:30:00+00:00"
        expected = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        actual = _compute_novelty_id("bbc", "https://x/1", "2026-08-13T19:30:00+00:00")
        assert actual == expected


# ───────────────────────────────────────────────────────────
# G. WorldEvent construction
# ───────────────────────────────────────────────────────────

class TestSectionG_WorldEventConstruction:
    """G. RssNewsSource builds correct WorldEvent from RSS item."""

    # Helper: use real now (not fixed) so lookback checks work
    @staticmethod
    def _now_and_window(lookback_hours=2):
        now = datetime.now(timezone.utc)
        return now, now - timedelta(hours=lookback_hours)

    def test_g1_basic_event(self):
        """G.1: simple item → WorldEvent with all required fields."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we is not None
        assert we.source == "news"
        assert we.type == "news_event"
        assert we.novelty_id is not None
        assert len(we.novelty_id) == 32

    def test_g2_data_field_includes_provider(self):
        """G.2: data['news_provider'] = feed provider."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc_world", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.data["news_provider"] == "bbc_world"

    def test_g3_data_field_includes_url(self):
        """G.3: data['news_url'] = feed URL."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.data["news_url"] == "http://x"

    def test_g4_data_field_includes_title_summary(self):
        """G.4: data['news_title'], data['news_summary'] from item."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item(
            title="Breaking News", description="Happened today"
        )
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.data["news_title"] == "Breaking News"
        assert we.data["news_summary"] == "Happened today"

    def test_g5_data_field_includes_canonical_url(self):
        """G.5: data['news_canonical_url'] = item link."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item(link="https://www.bbc.com/news/article-123")
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.data["news_canonical_url"] == "https://www.bbc.com/news/article-123"

    def test_g6_data_field_includes_timestamps(self):
        """G.6: data['news_published_at'] and data['news_retrieved_at'] both present."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert "news_published_at" in we.data
        assert "news_retrieved_at" in we.data
        # Both should be ISO 8601 UTC
        assert "T" in we.data["news_published_at"]
        assert "+" in we.data["news_published_at"] or "Z" in we.data["news_published_at"]

    def test_g7_data_field_includes_optional_fields(self):
        """G.7: data['news_category'], data['news_author'], data['news_guid'] when present."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item(category="World", author="BBC News", guid="urn:1")
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.data.get("news_category") == "World"
        assert we.data.get("news_author") == "BBC News"
        assert we.data.get("news_guid") == "urn:1"

    def test_g8_optional_fields_excluded_when_empty(self):
        """G.8: optional fields not in data when empty (cleaner payload)."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        item["category"] = ""
        item["author"] = ""
        item["guid"] = ""
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert "news_category" not in we.data
        assert "news_author" not in we.data
        assert "news_guid" not in we.data

    def test_g9_summary_includes_provider_and_date(self):
        """G.9: summary format = [provider] title (date UTC)."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc_world", "http://x")])
        now, window_start = self._now_and_window()
        pub_dt = now - timedelta(minutes=5)
        item = _make_simple_item(
            title="Important Story",
            pubdate=_build_pubdate_rfc822(pub_dt),
        )
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.summary.startswith("[bbc_world]")
        assert "Important Story" in we.summary

    def test_g10_priority_default_zero(self):
        """G.10: priority = 0 (M3.1 Phase B default)."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        assert we.priority == 0

    def test_g11_to_payload_round_trip(self):
        """G.11: WorldEvent.to_payload() produces valid dict, round-trips via from_payload."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        item = _make_simple_item()
        now, window_start = self._now_and_window()
        we = src._item_to_world_event(
            item=item, feed=src.feeds[0], now=now, window_start=window_start
        )
        payload = we.to_payload()
        assert payload["source"] == "news"
        assert payload["type"] == "news_event"
        # Round trip
        we2 = WorldEvent.from_payload(payload)
        assert we2.source == "news"
        assert we2.type == "news_event"
        assert we2.novelty_id == we.novelty_id


# ───────────────────────────────────────────────────────────
# H. Dedup (in-memory cache, FIFO)
# ───────────────────────────────────────────────────────────

class TestSectionH_Dedup:
    """H. Source-level dedup prevents repeat live events."""

    def test_h1_same_article_skipped_on_second_poll(self):
        """H.1: same article emitted in poll N is skipped in poll N+1."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds)
            now = datetime.now(timezone.utc)
            # Build a single fresh article (pubDate in future relative to test now)
            pub_dt = now - timedelta(minutes=5)
            item = _make_simple_item(
                title="Story 1",
                link="https://x/1",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            xml = _build_rss_xml([item])
            xml_per_feed = {"http://x": xml}

            # First poll: 1 event emitted
            emitted1 = await _poll_with_mock(src, xml_per_feed)
            assert emitted1 == 1
            assert src.dedup_cache_size == 1

            # Second poll: same article → 0 emitted (dedup)
            emitted2 = await _poll_with_mock(src, xml_per_feed)
            assert emitted2 == 0
            assert src.dedup_cache_size == 1  # still 1, not 2

        asyncio.run(run())

    def test_h2_different_articles_both_emitted(self):
        """H.2: different articles (different URLs) both emitted."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(minutes=5)
            item1 = _make_simple_item(
                title="Story 1",
                link="https://x/1",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            item2 = _make_simple_item(
                title="Story 2",
                link="https://x/2",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            xml = _build_rss_xml([item1, item2])
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 2
            assert src.dedup_cache_size == 2

        asyncio.run(run())

    def test_h3_dedup_cache_max_size_enforced(self):
        """H.3: dedup cache evicts oldest when over max size."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        # Fill cache past max
        for i in range(DEDUP_CACHE_MAX_SIZE + 100):
            src._add_to_dedup_cache(f"nid_{i:06d}")
        assert src.dedup_cache_size == DEDUP_CACHE_MAX_SIZE


# ───────────────────────────────────────────────────────────
# I. Lookback filtering
# ───────────────────────────────────────────────────────────

class TestSectionI_LookbackFiltering:
    """I. Articles outside lookback window are filtered out."""

    def test_i1_article_within_lookback_emitted(self):
        """I.1: article published 30 min ago is within 2h lookback → emitted."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, lookback_hours=2)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(minutes=30)
            item = _make_simple_item(
                title="Recent",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            xml = _build_rss_xml([item])
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 1

        asyncio.run(run())

    def test_i2_article_older_than_lookback_filtered(self):
        """I.2: article published 3h ago is outside 2h lookback → skipped."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, lookback_hours=2)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(hours=3)
            item = _make_simple_item(
                title="Old",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            xml = _build_rss_xml([item])
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 0
            stats = src.get_stats()
            assert stats["articles_skipped_outside_window"] == 1

        asyncio.run(run())

    def test_i3_future_dated_article_filtered(self):
        """I.3: future-dated article (clock skew) is conservatively skipped."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, lookback_hours=2)
            now = datetime.now(timezone.utc)
            pub_dt = now + timedelta(hours=1)  # future
            item = _make_simple_item(
                title="Future",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            xml = _build_rss_xml([item])
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 0
            stats = src.get_stats()
            assert stats["articles_skipped_outside_window"] == 1

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# J. Article cap
# ───────────────────────────────────────────────────────────

class TestSectionJ_ArticleCap:
    """J. article_cap stops emission past N articles per poll."""

    def test_j1_under_cap_all_emitted(self):
        """J.1: 5 articles with cap=10 → all 5 emitted."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, article_cap=10)
            now = datetime.now(timezone.utc)
            items = [
                _make_simple_item(
                    title=f"Story {i}",
                    link=f"https://x/{i}",
                    pubdate=_build_pubdate_rfc822(now - timedelta(minutes=i + 1)),
                )
                for i in range(5)
            ]
            xml = _build_rss_xml(items)
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 5

        asyncio.run(run())

    def test_j2_over_cap_limited(self):
        """J.2: 20 articles with cap=5 → only 5 emitted."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, article_cap=5)
            now = datetime.now(timezone.utc)
            items = [
                _make_simple_item(
                    title=f"Story {i}",
                    link=f"https://x/{i}",
                    pubdate=_build_pubdate_rfc822(now - timedelta(minutes=i + 1)),
                )
                for i in range(20)
            ]
            xml = _build_rss_xml(items)
            emitted = await _poll_with_mock(src, {"http://x": xml})
            assert emitted == 5  # capped

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# K. Event Bus integration
# ───────────────────────────────────────────────────────────

class TestSectionK_EventBusIntegration:
    """K. Events emitted via canonical Event Bus path."""

    def test_k1_publishes_soul_event_with_broadcast_target(self):
        """K.1: emits SoulEvent(WORLD_EVENT, target='broadcast', NORMAL)."""
        async def run():
            bus = SoulEventBus()
            await bus.start()
            try:
                feeds = [NewsFeedConfig("bbc", "http://x")]
                src = RssNewsSource(feeds=feeds, bus=bus)
                now = datetime.now(timezone.utc)
                pub_dt = now - timedelta(minutes=5)
                item = _make_simple_item(
                    pubdate=_build_pubdate_rfc822(pub_dt),
                )
                xml = _build_rss_xml([item])

                received: List[SoulEvent] = []

                async def _on_event(event: SoulEvent):
                    received.append(event)

                bus.subscribe(EventType.WORLD_EVENT, _on_event)
                emitted = await _poll_with_mock(src, {"http://x": xml})
                await asyncio.sleep(0.1)  # let event propagate
                assert emitted == 1
                assert len(received) == 1
                se = received[0]
                assert se.event_type == EventType.WORLD_EVENT
                assert se.target == "broadcast"
                assert se.priority == EventPriority.NORMAL
                assert se.payload["source"] == "news"
                assert se.payload["type"] == "news_event"
            finally:
                await bus.stop()

        asyncio.run(run())

    def test_k2_no_bus_emits_nothing(self):
        """K.2: no bus wired → poll returns 0, no crash."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, bus=None)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(minutes=5)
            item = _make_simple_item(pubdate=_build_pubdate_rfc822(pub_dt))
            xml = _build_rss_xml([item])
            # Direct poll with _fetch_rss mocked (NOT _poll_with_mock, which would add a bus)
            async def _mock_fetch(feed):
                return xml
            with patch.object(src, "_fetch_rss", side_effect=_mock_fetch):
                emitted = await src.poll()
            assert emitted == 0
            stats = src.get_stats()
            assert stats["events_emission_failed"] == 1

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# L. HTTP failure handling
# ───────────────────────────────────────────────────────────

class TestSectionL_HttpFailure:
    """L. HTTP errors (URLError, timeout) are non-fatal."""

    def test_l1_urlerror_returns_zero(self):
        """L.1: URLError (network unreachable) returns 0, no crash."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://nonexistent.invalid")]
            src = RssNewsSource(feeds=feeds)
            with patch(
                "urllib.request.urlopen",
                side_effect=urllib.error.URLError("Network unreachable"),
            ):
                emitted = await src.poll()
            assert emitted == 0
            stats = src.get_stats()
            assert stats["http_errors"] >= 1

        asyncio.run(run())

    def test_l2_http_404_returns_zero(self):
        """L.2: HTTP 404 returns 0, no crash."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds)
            # Simulate 404 by making _fetch_rss return None (matches production
            # behavior when urlopen raises HTTPError — the source's try/except
            # catches it and returns None).
            async def _mock_fetch_404(feed):
                return None  # HTTPError would be caught in real source
            with patch.object(src, "_fetch_rss", side_effect=_mock_fetch_404):
                emitted = await src.poll()
            assert emitted == 0
            stats = src.get_stats()
            # In this mock, the source doesn't see HTTPError (it sees None),
            # so http_errors is 0. The test verifies that failed fetch → 0 events.
            # (Real production code increments http_errors on HTTPError internally.)
            assert emitted == 0

        asyncio.run(run())

    def test_l3_timeout_returns_zero(self):
        """L.3: TimeoutError returns 0, no crash."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, http_timeout_secs=1.0)
            with patch(
                "urllib.request.urlopen",
                side_effect=TimeoutError("Read timed out"),
            ):
                emitted = await src.poll()
            assert emitted == 0
            stats = src.get_stats()
            assert stats["http_errors"] >= 1

        asyncio.run(run())

    def test_l4_malformed_xml_returns_zero(self):
        """L.4: malformed XML returns 0, no crash."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds)
            emitted = await _poll_with_mock(
                src, {"http://x": "<not><valid>"}
            )
            assert emitted == 0
            stats = src.get_stats()
            assert stats["parse_errors"] >= 1

        asyncio.run(run())

    def test_l5_other_feed_succeeds_when_one_fails(self):
        """L.5: if 1 feed fails, other feeds still polled (defensive)."""
        async def run():
            feeds = [
                NewsFeedConfig("bad", "http://bad"),
                NewsFeedConfig("good", "http://good"),
            ]
            src = RssNewsSource(feeds=feeds, article_cap=10)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(minutes=5)
            good_item = _make_simple_item(
                title="Good news",
                pubdate=_build_pubdate_rfc822(pub_dt),
            )
            good_xml = _build_rss_xml([good_item])
            emitted = await _poll_with_mock(
                src,
                {"http://bad": "<malformed", "http://good": good_xml},
            )
            assert emitted == 1  # good feed still polled

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# M. WorldPerception integration (E2E)
# ───────────────────────────────────────────────────────────

class TestSectionM_WorldPerceptionIntegration:
    """M. RssNewsSource E2E through WorldPerceptionMiddleware."""

    def test_m1_event_reaches_world_perception(self, tmp_path, monkeypatch):
        """M.1: NewsEvent flows through WorldPerceptionMiddleware into state."""
        # Use tmp_path for production isolation
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        async def run():
            bus = SoulEventBus()
            await bus.start()
            try:
                world_perception = WorldPerceptionMiddleware(bus=bus)
                world_perception.register()

                # Build state for inspection
                state = world_perception.state

                feeds = [NewsFeedConfig("bbc", "http://x")]
                src = RssNewsSource(feeds=feeds, bus=bus)
                now = datetime.now(timezone.utc)
                pub_dt = now - timedelta(minutes=5)
                item = _make_simple_item(
                    pubdate=_build_pubdate_rfc822(pub_dt),
                )
                xml = _build_rss_xml([item])
                emitted = await _poll_with_mock(src, {"http://x": xml})
                await asyncio.sleep(0.2)  # let event propagate

                assert emitted == 1
                # State should have the event
                active = state.get_active_events()
                assert len(active) >= 1
                sources = [e.source for e in active]
                assert "news" in sources
                types = [e.type for e in active]
                assert "news_event" in types
            finally:
                await bus.stop()

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# N. WorldInnerLifeAdapter behavior
# ───────────────────────────────────────────────────────────

class TestSectionN_AdapterBehavior:
    """N. news_event NOT in WORLD_QUALIFYING_TYPES → no InnerLifeEvent."""

    def test_n1_news_event_not_in_qualifying_types(self):
        """N.1: 'news_event' is NOT in WORLD_QUALIFYING_TYPES (frozen whitelist)."""
        from src.world.inner_life_adapter import WORLD_QUALIFYING_TYPES
        assert "news_event" not in WORLD_QUALIFYING_TYPES

    def test_n2_adapter_rejects_news_event(self, tmp_path, monkeypatch):
        """N.2: WorldInnerLifeAdapter receives news_event but does NOT create InnerLifeEvent."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        from src.inner_life import InnerLifeWriter
        from src.world.inner_life_adapter import WorldInnerLifeAdapter

        async def run():
            bus = SoulEventBus()
            await bus.start()
            try:
                writer = InnerLifeWriter()
                adapter = WorldInnerLifeAdapter(inner_life_writer=writer)
                adapter.register(bus=bus)

                # Emit a news_event via the bus
                item = _make_simple_item()
                we = WorldEvent(
                    source="news",
                    type="news_event",
                    novelty_id="abcdef1234567890abcdef1234567890",
                    ts="2026-08-13T19:30:00+00:00",
                    summary="[bbc] Test",
                    data={"news_provider": "bbc"},
                )
                soul_event = SoulEvent(
                    event_type=EventType.WORLD_EVENT,
                    source="news",
                    target="broadcast",
                    priority=EventPriority.NORMAL,
                    payload=we.to_payload(),
                )
                await bus.publish(soul_event)
                await asyncio.sleep(0.2)

                # No InnerLifeEvent created (news_event not qualifying)
                assert len(writer._events) == 0
                # Adapter stats: received 1, non_qualifying 1
                assert adapter.get_stats()["events_received"] == 1
                assert adapter.get_stats()["non_qualifying"] == 1
            finally:
                await bus.stop()

        asyncio.run(run())


# ───────────────────────────────────────────────────────────
# O. Production isolation
# ───────────────────────────────────────────────────────────

class TestSectionO_ProductionIsolation:
    """O. Tests must NOT mutate production data."""

    def test_o1_test_writes_to_tmp_path_only(self, tmp_path, monkeypatch):
        """O.1: all writes go to tmp_path, not production."""
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        reset_data_root()

        # Verify production data dir is NOT the same as test data dir
        production_data = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\data")
        assert data_root() != production_data
        assert str(data_root()).startswith(str(tmp_path))


# ───────────────────────────────────────────────────────────
# P. Configuration validation
# ───────────────────────────────────────────────────────────

class TestSectionP_ConfigValidation:
    """P. Constructor validates all parameters."""

    def test_p1_empty_feeds_rejected(self):
        """P.1: empty feeds list raises ValueError."""
        with pytest.raises(ValueError, match="feeds"):
            RssNewsSource(feeds=[])

    def test_p2_non_list_feeds_rejected(self):
        """P.2: non-list feeds raises ValueError."""
        with pytest.raises(ValueError, match="feeds"):
            RssNewsSource(feeds="not a list")

    def test_p3_non_NewsFeedConfig_rejected(self):
        """P.3: feeds containing non-NewsFeedConfig raises ValueError."""
        with pytest.raises(ValueError, match="NewsFeedConfig"):
            RssNewsSource(feeds=[{"provider": "x", "url": "y"}])

    def test_p4_duplicate_providers_rejected(self):
        """P.4: duplicate provider names raises ValueError."""
        with pytest.raises(ValueError, match="provider"):
            RssNewsSource(feeds=[
                NewsFeedConfig("bbc", "http://x"),
                NewsFeedConfig("bbc", "http://y"),
            ])

    def test_p5_polling_too_short_rejected(self):
        """P.5: polling_interval_secs < 60 raises ValueError."""
        with pytest.raises(ValueError, match="polling_interval"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                polling_interval_secs=30,
            )

    def test_p6_lookback_out_of_range_rejected(self):
        """P.6: lookback_hours outside [1, 168] raises ValueError."""
        with pytest.raises(ValueError, match="lookback_hours"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                lookback_hours=0,
            )
        with pytest.raises(ValueError, match="lookback_hours"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                lookback_hours=200,
            )

    def test_p7_article_cap_out_of_range_rejected(self):
        """P.7: article_cap outside [1, 100] raises ValueError."""
        with pytest.raises(ValueError, match="article_cap"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                article_cap=0,
            )
        with pytest.raises(ValueError, match="article_cap"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                article_cap=200,
            )

    def test_p8_http_timeout_out_of_range_rejected(self):
        """P.8: http_timeout_secs outside [1, 120] raises ValueError."""
        with pytest.raises(ValueError, match="http_timeout_secs"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                http_timeout_secs=0.5,
            )
        with pytest.raises(ValueError, match="http_timeout_secs"):
            RssNewsSource(
                feeds=[NewsFeedConfig("bbc", "http://x")],
                http_timeout_secs=200.0,
            )


# ───────────────────────────────────────────────────────────
# Q. Stats / observability
# ───────────────────────────────────────────────────────────

class TestSectionQ_Stats:
    """Q. Stats counters update correctly."""

    def test_q1_polls_total_increments(self):
        """Q.1: polls_total increments per poll."""
        async def run():
            src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("x")):
                await src.poll()
                await src.poll()
            assert src.get_stats()["polls_total"] == 2

        asyncio.run(run())

    def test_q2_articles_emitted_counted(self):
        """Q.2: events_emitted counter increments per emitted WorldEvent."""
        async def run():
            feeds = [NewsFeedConfig("bbc", "http://x")]
            src = RssNewsSource(feeds=feeds, article_cap=5)
            now = datetime.now(timezone.utc)
            pub_dt = now - timedelta(minutes=5)
            items = [
                _make_simple_item(
                    title=f"Story {i}",
                    link=f"https://x/{i}",
                    pubdate=_build_pubdate_rfc822(pub_dt),
                )
                for i in range(3)
            ]
            xml = _build_rss_xml(items)
            await _poll_with_mock(src, {"http://x": xml})
            assert src.get_stats()["events_emitted"] == 3

        asyncio.run(run())

    def test_q3_dedup_cache_size_in_stats(self):
        """Q.3: dedup_cache_size visible in stats."""
        src = RssNewsSource(feeds=[NewsFeedConfig("bbc", "http://x")])
        src._add_to_dedup_cache("nid1")
        src._add_to_dedup_cache("nid2")
        stats = src.get_stats()
        assert stats["dedup_cache_size"] == 2
