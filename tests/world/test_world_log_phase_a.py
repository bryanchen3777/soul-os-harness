"""
tests/world/test_world_log_phase_a.py — [Step 59/60] World Log Phase A

Owner-locked contract under test:

    Adapter fact → World Log → Event Bus → Perception

World Log 的唯一合法 runtime 寫入點是三個 production adapter 的
``_emit_via_bus()`` **之前**（``bus.publish()`` 之前）。理由：Event Bus
``QueueFull`` 時 subscriber 未必看到事件，World Log 必須保留 adapter 已接到
事實的證據。

本檔測試紀律（全部為本票硬性要求）:
  - repo 內 ``.venv`` interpreter（``.venv\\Scripts\\python.exe -m pytest``）
  - 每個測試的 ``SOUL_OS_DATA_DIR`` 都指向 pytest tmp（repo ``tests/conftest.py``
    的全域 autouse fixture；本檔另有 ``test_data_root_is_isolated_from_production``
    直接斷言這件事）
  - 0 network / 0 real adapter poll / 0 external API / 0 server / 0 live port
    （三個 adapter 都只走「已建構的 fixture WorldEvent」，HTTP fetch 從未被呼叫）
  - 0 LLM / 0 真 Event Bus server / 0 queue worker（一律 fake bus）
  - 不寫 production ``data/**``

測試段落:
  A. writer 契約（record schema / identity / observed_at 獨立性 / bounded provenance）
  B. 三個 adapter 的 adapter-first 寫入（publish 前恰 1 筆、shard 分日）
  C. QueueFull / publish 失敗（adapter-first 保留 record；subscriber 未看到）
  D. synthetic / test-source runtime 排除（0 新增列；既有 synthetic 行為不變）
  E. retention（30 shard / 檔名日期 cutoff / 只碰 world_log 自己的 shard）
  F. 不變量（perception trace bytes、trace timestamp 語意、score/threshold/
     collision/wake 行為、World Fact Text 行為）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest
from icalendar import Calendar as IcalCalendar

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.inner_life import InnerLifeWriter
from src.paths import data_root
from src.world import world_log as wl
from src.world.inner_life_adapter import WorldInnerLifeAdapter
from src.world.middleware import (
    DEFAULT_ACCEPT_THRESHOLD,
    DEFAULT_PERCEPTION_BUDGET,
    WorldPerceptionMiddleware,
    _fact_summary_extra,
)
from src.world.perception import WorldEvent
from src.world.source import (
    IcalCalendarSource,
    NewsFeedConfig,
    OpenMeteoWeatherSource,
    RssNewsSource,
    SyntheticWorldEventSource,
)
from src.world.source.news_rss import _parse_rss_items
from src.world.state import WorldPerceptionState
from src.world.trace import WorldPerceptionTraceWriter


# ────────────────────────────────────────────────────────────────────
# Fake bus (0 server / 0 queue worker / 0 production Event Bus)
# ────────────────────────────────────────────────────────────────────

class RecordingBus:
    """Controllable bus double.

    ``raise_exc`` simulates the documented Event Bus QueueFull failure mode:
    ``bus.publish`` raises, so the subscriber is NEVER invoked.
    """

    def __init__(self, raise_exc: Optional[BaseException] = None) -> None:
        self.published: List[SoulEvent] = []
        self.publish_calls = 0
        self._raise_exc = raise_exc

    async def publish(self, event: SoulEvent) -> None:
        self.publish_calls += 1
        if self._raise_exc is not None:
            raise self._raise_exc
        self.published.append(event)

    async def start(self) -> None:  # pragma: no cover - compatibility only
        return None

    async def stop(self) -> None:  # pragma: no cover - compatibility only
        return None


# ────────────────────────────────────────────────────────────────────
# Fixture WorldEvents (built directly; no synthetic runtime source)
# ────────────────────────────────────────────────────────────────────

_CAL_URL = "https://example.invalid/world-log-fixture.ics"
_WEATHER_LOCATION = "25.03,121.57"
_NEWS_FEED = NewsFeedConfig(
    provider="fixture_feed", url="https://example.invalid/rss.xml"
)


def _iso8601_z(hours_from_now: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(hours=hours_from_now)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def calendar_world_event(
    uid: str = "world-log-fixture@example.com",
    summary: str = "World Log fixture meeting",
) -> WorldEvent:
    """Validated calendar WorldEvent built through the production adapter.

    No HTTP: the iCal text is a fixture string and ``_vevent_to_world_event``
    is a pure mapping function.
    """
    ical_text = (
        "BEGIN:VCALENDAR\r\n"
        "VERSION:2.0\r\n"
        "PRODID:-//SoulOS WorldLog Test//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\n"
        f"DTSTART:{_iso8601_z(2)}\r\n"
        f"SUMMARY:{summary}\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    source = IcalCalendarSource(_CAL_URL)
    now = datetime.now(timezone.utc)
    calendar = IcalCalendar.from_ical(ical_text)
    component = list(calendar.walk("VEVENT"))[0]
    we = source._vevent_to_world_event(component, now, now + timedelta(hours=24))
    assert we is not None, "calendar fixture must produce a valid WorldEvent"
    return we


def weather_world_event() -> WorldEvent:
    """Validated weather WorldEvent built through the production adapter."""
    source = OpenMeteoWeatherSource(_WEATHER_LOCATION)
    observation = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0
    )
    payload = {
        "current": {
            "time": observation.isoformat(),
            "temperature_2m": 27.5,
            "precipitation": 0.0,
            "weather_code": 1,
        }
    }
    we = source._observation_to_world_event(payload)
    assert we is not None, "weather fixture must produce a valid WorldEvent"
    return we


_RSS_XML_TEMPLATE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<rss version="2.0"><channel><title>fixture</title>'
    "<item><title>World Log fixture headline</title>"
    "<link>https://example.invalid/articles/world-log-fixture</link>"
    "<pubDate>{pubdate}</pubDate>"
    "<description>fixture description</description>"
    "<guid>fixture-guid-1</guid></item>"
    "</channel></rss>"
)


def news_world_event() -> WorldEvent:
    """Validated news WorldEvent built through the production adapter."""
    source = RssNewsSource([_NEWS_FEED])
    now = datetime.now(timezone.utc)
    published = now - timedelta(minutes=30)
    items = _parse_rss_items(
        _RSS_XML_TEMPLATE.format(
            pubdate=published.strftime("%a, %d %b %Y %H:%M:%S +0000")
        )
    )
    assert items, "RSS fixture must parse"
    we = source._item_to_world_event(
        item=items[0],
        feed=_NEWS_FEED,
        now=now,
        window_start=now - timedelta(hours=2),
    )
    assert we is not None, "news fixture must produce a valid WorldEvent"
    return we


def _make_calendar_case(bus=None) -> Tuple[Any, WorldEvent]:
    return IcalCalendarSource(_CAL_URL, bus=bus), calendar_world_event()


def _make_weather_case(bus=None) -> Tuple[Any, WorldEvent]:
    return OpenMeteoWeatherSource(_WEATHER_LOCATION, bus=bus), weather_world_event()


def _make_news_case(bus=None) -> Tuple[Any, WorldEvent]:
    return RssNewsSource([_NEWS_FEED], bus=bus), news_world_event()


ADAPTER_CASES = [
    ("calendar", _make_calendar_case),
    ("weather", _make_weather_case),
    ("news", _make_news_case),
]
ADAPTER_IDS = [case[0] for case in ADAPTER_CASES]


def _soul_event(we: WorldEvent) -> SoulEvent:
    return SoulEvent(
        event_type=EventType.WORLD_EVENT,
        source=we.source,
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=we.to_payload(),
    )


# ────────────────────────────────────────────────────────────────────
# World Log readers (always under the pytest-isolated data root)
# ────────────────────────────────────────────────────────────────────

def _world_log_dir() -> Path:
    return data_root() / "world" / "world_log"


def _shard_files() -> List[Path]:
    directory = _world_log_dir()
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.glob("*.jsonl") if p.is_file())


def _all_records() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in _shard_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    return records


def _records_in_shard(name: str) -> List[Dict[str, Any]]:
    path = _world_log_dir() / name
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_shard_dir(directory: Path) -> List[Dict[str, Any]]:
    """Read every JSONL shard in an explicit directory (sorted by name)."""
    base = Path(directory)
    if not base.is_dir():
        return []
    records: List[Dict[str, Any]] = []
    for path in sorted(base.glob("*.jsonl")):
        if not path.is_file():
            continue
        records.extend(
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    return records


def _run(coro):
    return asyncio.run(coro)


# ────────────────────────────────────────────────────────────────────
# 0. Test isolation guard
# ────────────────────────────────────────────────────────────────────

def test_data_root_is_isolated_from_production():
    """所有測試都必須落在 pytest tmp data root，不得是 repo ``data/``。

    這是本檔「0 production data mutation」的直接證據來源；它同時驗證
    ``tests/conftest.py`` 的 autouse 隔離 fixture 真的生效。
    """
    root = data_root()
    production_root = (_REPO_ROOT / "data").resolve()
    assert root != production_root, (
        f"data_root() 指向生產資料根 {root}；測試隔離失效，禁止繼續"
    )

    env_value = os.environ.get("SOUL_OS_DATA_DIR")
    assert env_value, "SOUL_OS_DATA_DIR 未設定：測試隔離失效"
    assert Path(env_value).resolve() == root
    assert _world_log_dir().is_relative_to(root)


# ────────────────────────────────────────────────────────────────────
# A. Writer contract
# ────────────────────────────────────────────────────────────────────

class TestSectionA_WriterContract:
    """A. record schema / identity / timestamp independence / bounded provenance."""

    def test_a0_default_writer_is_adapter_first_singleton(self):
        """A.0: default writer 不快取路徑（每次寫入重新解析 data_root()）。"""
        writer = wl.get_world_log_writer()
        assert isinstance(writer, wl.WorldLogWriter)
        assert writer.log_dir == _world_log_dir()
        assert wl.get_world_log_writer() is writer

    def test_a1_writer_appends_exactly_one_record_with_fixed_keys(self):
        """A.1: 一筆 WorldEvent ⇒ 恰一筆 JSONL，欄位集合逐字符合固定契約。"""
        we = calendar_world_event()
        writer = wl.WorldLogWriter()

        assert writer.write(we) is True

        records = _all_records()
        assert len(records) == 1
        record = records[0]
        assert set(record) == {
            "world_event_id",
            "source",
            "event_type",
            "novelty_id",
            "happened_at",
            "observed_at",
            "summary",
            "priority",
            "provenance",
        }
        assert record["source"] == we.source == "calendar"
        assert record["event_type"] == we.type == "calendar_event"
        assert record["novelty_id"] == we.novelty_id
        assert record["summary"] == we.summary
        assert record["priority"] == we.priority == 0
        assert set(record["provenance"]) == {"source", "payload_reference"}
        assert record["provenance"]["source"] == we.source

    def test_a2_world_event_id_is_exact_and_not_a_bus_uuid(self):
        """A.2: ``world_event_id`` 逐字 = ``world:{source}:{novelty_id}``。

        不是 ``SoulEvent.event_id``（UUID）；也不得只留 novelty_id。
        """
        we = weather_world_event()
        soul_event = _soul_event(we)
        writer = wl.WorldLogWriter()
        assert writer.write(we) is True

        record = _all_records()[0]
        expected = f"world:{we.source}:{we.novelty_id}"
        assert record["world_event_id"] == expected
        assert record["world_event_id"].startswith("world:")
        assert record["world_event_id"].count(":") == 2
        assert record["world_event_id"] != we.novelty_id
        assert record["world_event_id"] != soul_event.event_id
        assert we.novelty_id not in soul_event.event_id

    def test_a3_happened_at_is_verbatim_world_event_ts(self):
        """A.3: ``happened_at`` 逐字來自 ``WorldEvent.ts``（不重解釋）。"""
        we = calendar_world_event()
        writer = wl.WorldLogWriter()
        assert writer.write(we) is True
        record = _all_records()[0]
        assert record["happened_at"] == we.ts
        # calendar 的未來 DTSTART 仍是合法的 source-native happened_at
        assert record["happened_at"].startswith(
            we.ts[:10]
        ), "happened_at 不得被正規化/改寫"

    def test_a4_observed_at_is_an_independent_write_clock(self, tmp_path):
        """A.4 (mutation teeth #2): ``observed_at`` 必須獨立於 ``happened_at``。

        注入一個與 ``WorldEvent.ts`` 完全不同的寫入時刻 ⇒ record 必須用注入
        時刻，且兩者不相等；不注入時則必須是「現在」附近的 UTC timestamp。
        """
        we = calendar_world_event()
        injected = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        # 兩個獨立 shard 目錄：避免注入的歷史日期與 wall clock 日期互相觸發
        # retention（retention 語意本身由 Section E 專門覆蓋）。
        writer = wl.WorldLogWriter(log_dir=tmp_path / "wl_injected")
        wall_writer = wl.WorldLogWriter(log_dir=tmp_path / "wl_wall")

        assert writer.write(we, now=injected) is True
        record = _read_shard_dir(tmp_path / "wl_injected")
        assert len(record) == 1
        record = record[0]
        assert record["observed_at"] == injected.isoformat()
        assert record["observed_at"] != record["happened_at"]
        assert record["observed_at"].endswith("+00:00")

        # 不注入 ⇒ 寫入當下的 wall clock（與 happened_at 無關）
        we2 = weather_world_event()
        before = datetime.now(timezone.utc) - timedelta(seconds=5)
        assert wall_writer.write(we2) is True
        after = datetime.now(timezone.utc) + timedelta(seconds=5)
        utc_today = datetime.now(timezone.utc).date().isoformat()
        rows = _read_shard_dir(tmp_path / "wl_wall")
        assert len(rows) == 1
        observed = datetime.fromisoformat(rows[0]["observed_at"])
        assert before <= observed <= after
        assert observed.isoformat() != we2.ts
        assert (tmp_path / "wl_wall" / f"{utc_today}.jsonl").is_file()

    def test_a5_provenance_payload_reference_is_bounded_and_content_free(self):
        """A.5: provenance 有界、不可逆、不落盤原始 provider payload。"""
        marker = "RAW-PROVIDER-SECRET-MARKER"
        we = WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id="bounded_provenance_probe",
            ts="2026-09-20T00:00:00+00:00",
            summary="bounded provenance probe",
            data={
                "raw_provider_response": ("X" * 100000) + marker,
                "authorization": f"Bearer {marker}",
                "ical_uid": "probe@example.com",
            },
        )
        writer = wl.WorldLogWriter()
        assert writer.write(we) is True

        path = _shard_files()[-1]
        raw_line = path.read_text(encoding="utf-8")

        assert marker not in raw_line, "原始 payload / secret 不得落盤"
        assert "Bearer" not in raw_line
        assert len(raw_line) < 4096, "record 必須有界"

        record = json.loads(raw_line.strip())
        reference = record["provenance"]["payload_reference"]
        assert isinstance(reference, str)
        assert reference.startswith("sha256:")
        assert len(reference) == len("sha256:") + 64
        assert all(c in "0123456789abcdef" for c in reference.split(":", 1)[1])

    def test_a6_empty_payload_reference_is_null(self):
        """A.6: 沒有 payload ⇒ ``payload_reference`` 為 null（不是空字串）。"""
        we = WorldEvent(
            source="social",
            type="celebrity_news",
            novelty_id="null_provenance_probe",
            ts="2026-09-20T00:00:00+00:00",
            summary="null provenance probe",
            data={},
        )
        assert wl.build_payload_reference({}) is None
        assert wl.build_payload_reference(None) is None
        writer = wl.WorldLogWriter()
        assert writer.write(we) is True
        assert _all_records()[0]["provenance"]["payload_reference"] is None

    def test_a7_writer_never_raises_and_returns_false_on_failure(self, tmp_path):
        """A.7: 寫入失敗 fail-quiet（回 False，不 raise，不影響主路徑）。"""
        blocker = tmp_path / "blocker"
        blocker.write_text("not a directory", encoding="utf-8")
        writer = wl.WorldLogWriter(log_dir=blocker / "world_log")
        we = weather_world_event()
        assert writer.write(we) is False
        assert _all_records() == []

    def test_a8_ineligible_input_is_not_written(self):
        """A.8: 非 WorldEvent / 空 source ⇒ 不寫入。"""
        writer = wl.WorldLogWriter()
        assert writer.write(object()) is False
        assert wl.is_world_log_eligible(object()) is False
        assert _all_records() == []


# ────────────────────────────────────────────────────────────────────
# B. Adapter-first write (before publish)
# ────────────────────────────────────────────────────────────────────

class TestSectionB_AdapterFirstWrite:
    """B. 三個 adapter 在 publish 之前各寫恰 1 筆；shard 依 UTC 日期。"""

    @pytest.mark.parametrize("label,make_case", ADAPTER_CASES, ids=ADAPTER_IDS)
    def test_b1_each_adapter_writes_exactly_one_record_before_publish(
        self, label, make_case
    ):
        """B.1: 合法 validated WorldEvent ⇒ publish 前恰寫 1 筆 World Log。"""
        bus = RecordingBus()
        source, we = make_case(bus)

        emitted = _run(source._emit_via_bus(we))

        assert emitted is True
        assert bus.publish_calls == 1
        assert len(bus.published) == 1
        assert bus.published[0].event_type == EventType.WORLD_EVENT
        assert bus.published[0].payload["novelty_id"] == we.novelty_id

        records = _all_records()
        assert len(records) == 1, f"{label}: 應恰寫 1 筆 World Log"
        record = records[0]
        assert record["world_event_id"] == f"world:{we.source}:{we.novelty_id}"
        assert record["source"] == we.source
        assert record["event_type"] == we.type
        assert record["novelty_id"] == we.novelty_id
        assert record["happened_at"] == we.ts
        assert record["observed_at"] != record["happened_at"]

        assert _shard_files() == [
            _world_log_dir()
            / (datetime.now(timezone.utc).date().isoformat() + ".jsonl")
        ]

    @pytest.mark.parametrize("label,make_case", ADAPTER_CASES, ids=ADAPTER_IDS)
    def test_b2_write_happens_even_without_a_bus(self, label, make_case):
        """B.2: ``self._bus is None``（未 publish）仍必須留下事實證據。"""
        source, we = make_case(None)
        emitted = _run(source._emit_via_bus(we))
        assert emitted is False
        assert source._stats["events_emission_failed"] == 1
        records = _all_records()
        assert len(records) == 1, f"{label}: bus=None 仍必須寫 1 筆"

    def test_b3_same_utc_day_events_share_one_shard(self):
        """B.3: 同一 UTC 日的事件進同一個 shard。"""
        writer = wl.WorldLogWriter()
        day = datetime(2026, 3, 4, tzinfo=timezone.utc)
        we_a = weather_world_event()
        we_b = news_world_event()

        assert writer.write(we_a, now=day + timedelta(hours=1)) is True
        assert writer.write(we_b, now=day + timedelta(hours=23)) is True

        assert [p.name for p in _shard_files()] == ["2026-03-04.jsonl"]
        assert len(_records_in_shard("2026-03-04.jsonl")) == 2

    def test_b4_cross_utc_day_events_go_to_different_shards(self):
        """B.4: 跨 UTC 日的事件進正確的不同 shard。"""
        writer = wl.WorldLogWriter()
        we_a = weather_world_event()
        we_b = news_world_event()

        assert writer.write(
            we_a, now=datetime(2026, 3, 4, 23, 59, 59, tzinfo=timezone.utc)
        ) is True
        assert writer.write(
            we_b, now=datetime(2026, 3, 5, 0, 0, 1, tzinfo=timezone.utc)
        ) is True

        assert [p.name for p in _shard_files()] == [
            "2026-03-04.jsonl",
            "2026-03-05.jsonl",
        ]
        assert len(_records_in_shard("2026-03-04.jsonl")) == 1
        assert len(_records_in_shard("2026-03-05.jsonl")) == 1
        assert _records_in_shard("2026-03-04.jsonl")[0]["novelty_id"] == we_a.novelty_id
        assert _records_in_shard("2026-03-05.jsonl")[0]["novelty_id"] == we_b.novelty_id

    def test_b5_repeated_observation_keeps_every_observation(self, monkeypatch):
        """B.5: append-only，**不是** dedup cache。

        同一 ``source + novelty_id`` 重複 emit ⇒ 保留各次 adapter observation；
        canonical ``world_event_id`` 逐字不變；可區分重複觀測只能靠獨立
        ``observed_at``（不得改 canonical id，也不得新增 dedup 行為）。
        """
        # 本測試刻意混用「注入的歷史日期」與 adapter 的 wall-clock 寫入；
        # retention 語意由 Section E 專門覆蓋，這裡關掉 writer-path prune 以免
        # 歷史 shard 被合法的 30 天策略刪掉而干擾本測試的計數斷言。
        monkeypatch.setattr(
            wl, "prune_world_log_shards", lambda log_dir=None, now=None: []
        )
        bus = RecordingBus()
        source, we = _make_weather_case(bus)
        writer = wl.WorldLogWriter()
        first = datetime(2026, 3, 4, 10, 0, 0, tzinfo=timezone.utc)
        second = first + timedelta(hours=1)

        assert writer.write(we, now=first) is True
        assert writer.write(we, now=second) is True

        records = _all_records()
        assert len(records) == 2, "重複觀測不得被吞掉（World Log 非 dedup cache）"
        assert records[0]["world_event_id"] == records[1]["world_event_id"]
        assert records[0]["world_event_id"] == f"world:{we.source}:{we.novelty_id}"
        assert records[0]["observed_at"] != records[1]["observed_at"]
        assert records[0]["happened_at"] == records[1]["happened_at"] == we.ts

        # adapter 層重複 emit × 2 也必須留下 2 筆（source 端 dedup 不干預 World Log）
        assert _run(source._emit_via_bus(we)) is True
        assert _run(source._emit_via_bus(we)) is True
        assert len(_all_records()) == 4


# ────────────────────────────────────────────────────────────────────
# C. QueueFull / publish failure
# ────────────────────────────────────────────────────────────────────

class TestSectionC_QueueFullEvidence:
    """C. adapter-first：publish 失敗時 World Log 仍保留該 WorldEvent record。"""

    @pytest.mark.parametrize("label,make_case", ADAPTER_CASES, ids=ADAPTER_IDS)
    def test_c1_queue_full_still_leaves_the_adapter_record(self, label, make_case):
        """C.1 (mutation teeth #1): QueueFull ⇒ subscriber 沒看到，但 record 在。"""
        bus = RecordingBus(raise_exc=asyncio.QueueFull())
        source, we = make_case(bus)

        emitted = _run(source._emit_via_bus(we))

        assert emitted is False
        assert source._stats["events_emission_failed"] == 1
        assert bus.published == [], "QueueFull ⇒ subscriber 不得收到事件"

        records = _all_records()
        assert len(records) == 1, (
            f"{label}: publish 失敗時 adapter-first World Log 必須保留事實證據"
        )
        assert records[0]["world_event_id"] == f"world:{we.source}:{we.novelty_id}"
        assert records[0]["happened_at"] == we.ts

    @pytest.mark.parametrize("label,make_case", ADAPTER_CASES, ids=ADAPTER_IDS)
    def test_c2_generic_publish_exception_still_leaves_the_record(
        self, label, make_case
    ):
        """C.2: publish 拋一般例外（bus 內部失敗）⇒ 一樣保留 record。"""
        bus = RecordingBus(raise_exc=RuntimeError("bus not running"))
        source, we = make_case(bus)

        assert _run(source._emit_via_bus(we)) is False
        assert bus.published == []
        assert len(_all_records()) == 1, label

    def test_c3_subscribers_and_inner_life_are_never_a_world_log_precondition(self):
        """C.3: World Log 不得靠 subscriber / Inner Life / middleware 才會寫。

        用 fake bus 手動把 SoulEvent 交給 middleware 與 Inner Life adapter
        （模擬 subscriber 被叫到），World Log 列數必須完全不變。
        """
        bus = RecordingBus()
        source, we = _make_news_case(bus)
        assert _run(source._emit_via_bus(we)) is True
        before = _all_records()
        assert len(before) == 1

        trace_writer = WorldPerceptionTraceWriter(
            trace_log_path=data_root() / "world" / "perception_trace.jsonl"
        )
        middleware = WorldPerceptionMiddleware(
            bus=bus,
            state=WorldPerceptionState(),
            trace_writer=trace_writer,
        )
        inner_life = WorldInnerLifeAdapter(
            inner_life_writer=InnerLifeWriter(trace_writer=None)
        )
        soul_event = bus.published[0]

        _run(middleware.handle_event(soul_event))
        _run(inner_life.handle_event(soul_event))

        assert _all_records() == before, (
            "subscriber 路徑不得寫 World Log（唯一寫入點是 adapter 的 _emit_via_bus 之前）"
        )


# ────────────────────────────────────────────────────────────────────
# D. Synthetic / test-source runtime exclusion
# ────────────────────────────────────────────────────────────────────

class TestSectionD_SyntheticExclusion:
    """D. synthetic / ``SOULOS_WORLD_PERCEPTION_TEST_SOURCE`` ⇒ 0 新增列。"""

    def test_d1_synthetic_source_runtime_route_writes_zero_records(self):
        """D.1 (mutation teeth #5): SyntheticWorldEventSource runtime ⇒ 0 列。

        既有 synthetic 行為必須不變：仍照樣 publish 到 bus、仍回傳 WorldEvent。
        """
        bus = RecordingBus()
        source = SyntheticWorldEventSource(bus=bus)

        emitted = _run(
            source.emit_event(
                type="rain_started",
                summary="synthetic contamination probe",
                novelty_id="synthetic_probe_1",
            )
        )

        assert emitted.source == "synthetic"
        assert bus.publish_calls == 1, "既有 synthetic publish 行為不得改變"
        assert _all_records() == [], "synthetic runtime route 不得寫 World Log"

    def test_d2_smoke_test_injection_route_writes_zero_records(self, monkeypatch):
        """D.2: ``SOULOS_WORLD_PERCEPTION_TEST_SOURCE=1`` 的 production smoke
        注入路徑（``inject_synthetic_events_for_smoke_test``）⇒ 0 列。"""
        monkeypatch.setenv("SOULOS_WORLD_PERCEPTION_TEST_SOURCE", "1")
        bus = RecordingBus()
        trace_writer = WorldPerceptionTraceWriter(
            trace_log_path=data_root() / "world" / "perception_trace.jsonl"
        )
        middleware = WorldPerceptionMiddleware(
            bus=bus,
            state=WorldPerceptionState(),
            trace_writer=trace_writer,
        )
        ssource = SyntheticWorldEventSource(bus=bus)
        events = [
            ssource.build_rain_started(),
            ssource.build_celebrity_news(),
        ]

        injected = _run(middleware.inject_synthetic_events_for_smoke_test(events))

        assert injected == 2, "既有 synthetic 注入行為不得改變"
        assert middleware.state_snapshot()["events_received"] == 2
        assert trace_writer.trace_log_path.exists(), "既有 trace 行為不得改變"
        assert _all_records() == [], "test-source runtime route 不得寫 World Log"

    def test_d3_process_wide_direct_injection_writes_zero_records(self):
        """D.3: ``process_world_event_direct``（另一條 synthetic/直接注入路徑）⇒ 0 列。"""
        bus = RecordingBus()
        trace_writer = WorldPerceptionTraceWriter(
            trace_log_path=data_root() / "world" / "perception_trace.jsonl"
        )
        middleware = WorldPerceptionMiddleware(
            bus=bus,
            state=WorldPerceptionState(),
            trace_writer=trace_writer,
        )
        we = SyntheticWorldEventSource.build_rain_started()

        _run(middleware.process_world_event_direct(we))

        assert middleware.state_snapshot()["events_received"] == 1
        assert _all_records() == []

    def test_d4_writer_level_exclusion_is_explicit_and_fail_closed(self):
        """D.4: writer-level 防線——synthetic source 一律 ineligible（冗餘但必要）。"""
        # 只有 runtime emit 路徑會 hardcode source="synthetic"
        # （build_*() 是 fixture factories，保留 spec 的 source）
        synthetic_event = _run(
            SyntheticWorldEventSource().emit_event(
                type="rain_started",
                summary="writer-level exclusion probe",
                novelty_id="synthetic_probe_d4",
            )
        )
        assert synthetic_event.source == "synthetic"
        assert wl.is_world_log_eligible(synthetic_event) is False
        assert wl.EXCLUDED_WORLD_LOG_SOURCES == frozenset({"synthetic"})

        writer = wl.WorldLogWriter()
        assert writer.write(synthetic_event) is False
        assert wl.record_world_event(synthetic_event) is False
        assert _all_records() == []

    def test_d5_non_synthetic_sources_stay_eligible(self):
        """D.5 (負向對照): 三個 production source 仍必須 eligible。"""
        for label, make_case in ADAPTER_CASES:
            _source, we = make_case(None)
            assert wl.is_world_log_eligible(we) is True, label


# ────────────────────────────────────────────────────────────────────
# E. Retention (A2)
# ────────────────────────────────────────────────────────────────────

class TestSectionE_Retention:
    """E. 30 個 UTC 日曆日；僅刪完整 shard；cutoff 依檔名日期，不看 mtime。"""

    _NOW = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)

    def _make_shards(self, directory: Path, days: int) -> Dict[str, str]:
        """建立 days 個連續日期 shard（今天往回數），回傳 name → content。"""
        directory.mkdir(parents=True, exist_ok=True)
        content: Dict[str, str] = {}
        for offset in range(days):
            day = self._NOW.date() - timedelta(days=offset)
            name = day.isoformat() + ".jsonl"
            text = json.dumps({"shard": name, "filler": "y" * (offset + 1)}) + "\n"
            (directory / name).write_text(text, encoding="utf-8")
            content[name] = text
        return content

    @staticmethod
    def _sha_map(directory: Path) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for path in sorted(Path(directory).rglob("*")):
            if path.is_file():
                out[path.relative_to(directory).as_posix()] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return out

    def test_e1_retention_keeps_thirty_shards_and_deletes_the_oldest(
        self, tmp_path
    ):
        """E.1: 31 個 shard ⇒ 保留 30，最早且超過 cutoff 的完整 shard 被刪。

        cutoff 內的 shard 必須 **byte-for-byte** 不變。
        """
        directory = tmp_path / "world_log"
        content = self._make_shards(directory, days=31)
        before_hashes = self._sha_map(directory)

        deleted = wl.prune_world_log_shards(log_dir=directory, now=self._NOW)

        oldest = (self._NOW.date() - timedelta(days=30)).isoformat() + ".jsonl"
        assert deleted == [oldest], f"只應刪除最早那個 shard，實際 {deleted}"
        assert not (directory / oldest).exists()

        remaining = sorted(p.name for p in directory.glob("*.jsonl"))
        assert len(remaining) == 30
        assert wl.WORLD_LOG_RETENTION_DAYS == 30

        for name in remaining:
            assert (
                directory / name
            ).read_text(encoding="utf-8") == content[name]
            assert self._sha_map(directory)[name] == before_hashes[name]

    def test_e2_cutoff_definition_is_thirty_utc_calendar_days(self):
        """E.2: cutoff = today_utc - 29 天（含今天共 30 個日曆日）。"""
        now = datetime(2026, 9, 20, 23, 59, 59, tzinfo=timezone.utc)
        assert wl.retention_cutoff(now) == date(2026, 8, 22)
        assert wl.retention_cutoff(now) == date(2026, 9, 20) - timedelta(days=29)
        # 日期邊界：UTC 跨日 ⇒ cutoff 跟著跨日
        rollover = datetime(2026, 9, 21, 0, 0, 1, tzinfo=timezone.utc)
        assert wl.retention_cutoff(rollover) == date(2026, 8, 23)

    def test_e3_decision_is_filename_date_not_mtime(self, tmp_path):
        """E.3 (mutation teeth #4): 判定必須依檔名日期，不得依 mtime。

        - 檔名很舊但 mtime 很新 ⇒ 必須刪（mtime 判定會誤留）。
        - 檔名是今天但 mtime 很舊 ⇒ 必須留（mtime 判定會誤刪）。
        """
        directory = tmp_path / "world_log"
        directory.mkdir(parents=True)
        old_name = (self._NOW.date() - timedelta(days=40)).isoformat() + ".jsonl"
        fresh_name = self._NOW.date().isoformat() + ".jsonl"
        for name in (old_name, fresh_name):
            (directory / name).write_text('{"shard": "x"}\n', encoding="utf-8")

        ancient = (self._NOW - timedelta(days=120)).timestamp()
        os.utime(directory / old_name, (ancient + 86400 * 119, ancient + 86400 * 119))
        os.utime(directory / fresh_name, (ancient, ancient))

        deleted = wl.prune_world_log_shards(log_dir=directory, now=self._NOW)

        assert deleted == [old_name], "檔名過舊者必須被刪（不得因 mtime 新而留存）"
        assert (directory / fresh_name).exists(), (
            "檔名在 cutoff 內者必須保留（不得因 mtime 舊而被刪）"
        )

    def test_e4_retention_never_touches_anything_but_world_log_shards(
        self, tmp_path
    ):
        """E.4 (mutation teeth #4): malformed / 未知副檔名 / 子目錄 / trace /
        其他 world artifact 一律不得被刪或改動。"""
        data_root_dir = tmp_path / "data"
        log_dir = data_root_dir / "world" / "world_log"
        log_dir.mkdir(parents=True)

        # 一個真正過期的合法 shard —— 證明掃描真的有跑（anti-vacuity）
        doomed = log_dir / ((self._NOW.date() - timedelta(days=60)).isoformat() + ".jsonl")
        doomed.write_text('{"legit": "old"}\n', encoding="utf-8")

        protected: Dict[Path, str] = {}

        def _protect(path: Path, text: str) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            protected[path] = text

        # 1) malformed filename（非日期）
        _protect(log_dir / "not-a-date.jsonl", '{"malformed": true}\n')
        _protect(log_dir / "2026-9-1.jsonl", '{"malformed": "loose"}\n')
        # 2) 不存在的日期（格式像但不是合法 date）
        _protect(log_dir / "2026-02-30.jsonl", '{"malformed": "impossible date"}\n')
        # 3) 未知副檔名
        _protect(log_dir / "2026-01-01.txt", "not jsonl\n")
        _protect(log_dir / "2026-01-01.jsonl.bak", '{"backup": true}\n')
        # 4) 未預期子目錄（含巢狀合法 shard 名）
        _protect(log_dir / "archive" / "2020-01-01.jsonl", '{"nested": true}\n')
        # 5) 目錄本身叫 shard 名
        (log_dir / "2020-01-02.jsonl").mkdir(parents=True, exist_ok=True)
        # 6) perception trace（world_log 之外）
        trace = data_root_dir / "world" / "perception_trace.jsonl"
        _protect(trace, '{"phase": "received"}\n')
        # 7) 其他 world artifact / fact text
        _protect(
            data_root_dir / "world" / "world_fact_text" / "2020-01-01.jsonl",
            '{"fact": "text"}\n',
        )
        # 8) 完全在 world/ 之外的舊 jsonl
        _protect(data_root_dir / "memory" / "loader_trace.jsonl", '{"loader": 1}\n')

        before = self._sha_map(tmp_path)

        deleted = wl.prune_world_log_shards(log_dir=log_dir, now=self._NOW)

        assert deleted == [doomed.name]
        assert not doomed.exists()
        assert (log_dir / "2020-01-02.jsonl").is_dir(), "目錄不得被當 shard 刪除"

        after = self._sha_map(tmp_path)
        for path, text in protected.items():
            assert path.exists(), f"{path.name} 不得被 retention 刪除"
            assert path.read_text(encoding="utf-8") == text
            rel = path.relative_to(tmp_path).as_posix()
            assert after[rel] == before[rel], f"{path.name} 內容不得被改寫"

    def test_e5_retention_frequency_in_writer_path_is_once_per_utc_day(
        self, monkeypatch, tmp_path
    ):
        """E.5: writer path 的 retention frequency 明確可測 = 每 UTC 日曆日至多一次，
        且掃描範圍精確限定 ``data_root()/world/world_log``（不掃整個 data root）。"""
        calls: List[Dict[str, Any]] = []
        real_prune = wl.prune_world_log_shards

        def _spy(log_dir=None, now=None):
            calls.append({"log_dir": log_dir, "now": now})
            return real_prune(log_dir=log_dir, now=now)

        monkeypatch.setattr(wl, "prune_world_log_shards", _spy)

        writer = wl.WorldLogWriter()
        we = weather_world_event()
        day1 = datetime(2026, 3, 4, 1, 0, 0, tzinfo=timezone.utc)

        for hours in (0, 5, 11):
            assert writer.write(we, now=day1 + timedelta(hours=hours)) is True
        assert len(calls) == 1, f"同一 UTC 日不得反覆掃描，實際 {len(calls)} 次"
        assert calls[0]["log_dir"] == _world_log_dir()
        assert calls[0]["log_dir"].name == "world_log"
        assert calls[0]["log_dir"].parent.name == "world"
        assert calls[0]["log_dir"] != data_root()

        # 跨 UTC 日 ⇒ 允許再掃一次
        assert writer.write(we, now=day1 + timedelta(hours=24)) is True
        assert len(calls) == 2
        assert writer.last_retention_date == (day1 + timedelta(hours=24)).date()

    def test_e6_retention_is_idempotent_and_no_op_without_directory(self, tmp_path):
        """E.6: 目錄不存在 ⇒ no-op；重跑 ⇒ 不報錯、不再刪任何東西。"""
        missing = tmp_path / "nope" / "world_log"
        assert wl.prune_world_log_shards(log_dir=missing, now=self._NOW) == []

        directory = tmp_path / "world_log"
        self._make_shards(directory, days=31)
        first = wl.prune_world_log_shards(log_dir=directory, now=self._NOW)
        second = wl.prune_world_log_shards(log_dir=directory, now=self._NOW)
        assert len(first) == 1
        assert second == []

    def test_e8_writer_path_actually_prunes_old_shards(self, tmp_path):
        """E.8: writer path 真的接上 retention（不是空接線）。

        用獨立 ``log_dir`` 的 writer，內含一個過期 shard + 一個 cutoff 內 shard；
        writer 的第一次寫入必須刪掉過期者、留下 cutoff 內者（byte 不變）。
        """
        directory = tmp_path / "world_log"
        directory.mkdir(parents=True)
        stale = directory / (
            (self._NOW.date() - timedelta(days=45)).isoformat() + ".jsonl"
        )
        keep = directory / (
            (self._NOW.date() - timedelta(days=5)).isoformat() + ".jsonl"
        )
        stale.write_text('{"stale": true}\n', encoding="utf-8")
        keep_text = '{"keep": true}\n'
        keep.write_text(keep_text, encoding="utf-8")
        keep_before = hashlib.sha256(keep.read_bytes()).hexdigest()

        writer = wl.WorldLogWriter(log_dir=directory)
        assert writer.write(weather_world_event(), now=self._NOW) is True

        assert not stale.exists(), "writer path 必須真的執行 retention"
        assert keep.exists()
        assert hashlib.sha256(keep.read_bytes()).hexdigest() == keep_before
        assert keep.read_text(encoding="utf-8") == keep_text

    def test_e7_malformed_shard_names_are_fail_quiet(self):
        """E.7: ``parse_shard_date`` 對任何非嚴格 shard 名回 None（不得猜）。"""
        assert wl.parse_shard_date("2026-09-20.jsonl") == date(2026, 9, 20)
        for bad in (
            "2026-09-20",
            "2026-09-20.txt",
            "2026-09-20.jsonl.bak",
            "2026-9-20.jsonl",
            "20260920.jsonl",
            "2026-02-30.jsonl",
            "2026-13-01.jsonl",
            "archive/2026-09-20.jsonl",
            "",
            "not-a-date.jsonl",
        ):
            assert wl.parse_shard_date(bad) is None, bad


# ────────────────────────────────────────────────────────────────────
# F. Invariants (trace bytes / timestamp semantics / scoring / fact text)
# ────────────────────────────────────────────────────────────────────

def _trace_rows(path: Path) -> List[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestSectionF_Invariants:
    """F. Phase A 前後：trace bytes、trace 語意、score/threshold/collision/wake
    行為、World Fact Text 行為全部不變。"""

    def _middleware(self, tmp_path: Path) -> Tuple[Any, Path]:
        trace_path = tmp_path / "perception_trace.jsonl"
        middleware = WorldPerceptionMiddleware(
            bus=RecordingBus(),
            state=WorldPerceptionState(),
            trace_writer=WorldPerceptionTraceWriter(trace_log_path=trace_path),
        )
        return middleware, trace_path

    def test_f1_perception_trace_bytes_are_unchanged_by_world_log_writes(
        self, tmp_path
    ):
        """F.1 (mutation teeth #6): World Log 寫入不得改動 perception trace bytes。

        並且 trace 語意必須維持：received 列的 ``event_id`` 是 bus UUID（不是
        ``world_event_id``），``timestamp`` 是 perception-time（不是
        ``WorldEvent.ts``）。
        """
        middleware, trace_path = self._middleware(tmp_path)
        bus = RecordingBus()
        source, we = _make_calendar_case(bus)
        assert _run(source._emit_via_bus(we)) is True
        soul_event = bus.published[0]

        _run(middleware.handle_event(soul_event))

        rows = _trace_rows(trace_path)
        assert len(rows) == 1
        received = rows[0]
        assert received["extra"]["phase"] == "received"
        assert received["event_id"] == soul_event.event_id, (
            "received 列 event_id 必須是 bus UUID（既有 trace 契約）"
        )
        assert received["event_id"] != f"world:{we.source}:{we.novelty_id}"
        assert received["novelty_id"] == we.novelty_id
        assert received["source"] == we.source
        assert received["event_type"] == we.type
        assert received["timestamp"] != we.ts, (
            "trace timestamp 是 perception-time，不得等於事件自身的 ts"
        )
        perceived = datetime.fromisoformat(received["timestamp"])
        assert perceived.tzinfo is not None
        assert abs(
            (perceived - datetime.now(timezone.utc)).total_seconds()
        ) < 60, "trace timestamp 必須是感知當下時間"

        trace_bytes_before = trace_path.read_bytes()
        world_log_before = len(_all_records())

        for _ in range(3):
            assert wl.record_world_event(we) is True

        assert trace_path.read_bytes() == trace_bytes_before, (
            "World Log 寫入不得碰既有 perception trace（bytes 必須相同）"
        )
        assert len(_all_records()) == world_log_before + 3

    def test_f2_scoring_threshold_and_state_behaviour_unchanged(self, tmp_path):
        """F.2: score / threshold / novelty-window（collision）行為不因 World Log 改變。"""
        middleware_a, trace_a = self._middleware(tmp_path / "a")
        bus = RecordingBus()
        source, we = _make_calendar_case(bus)
        assert _run(source._emit_via_bus(we)) is True
        soul_event = bus.published[0]

        _run(middleware_a.handle_event(soul_event))
        row_a = _trace_rows(trace_a)[0]

        # 中間塞入多次 World Log 寫入
        for _ in range(5):
            assert wl.record_world_event(we) is True

        middleware_b, trace_b = self._middleware(tmp_path / "b")
        _run(middleware_b.handle_event(soul_event))
        row_b = _trace_rows(trace_b)[0]

        row_a.pop("timestamp")
        row_b.pop("timestamp")
        assert row_a == row_b, "World Log 不得改變 perception 判定（score/reason/phase）"

        assert middleware_a.accept_threshold == DEFAULT_ACCEPT_THRESHOLD
        assert middleware_a.perception_budget == DEFAULT_PERCEPTION_BUDGET
        assert middleware_a.state.novelty_window == timedelta(hours=24)

        # novelty / collision 計數語意不變：同 novelty_id 第二次進 state ⇒ count 2
        state = WorldPerceptionState()
        assert state.add(we) == 1
        assert state.add(we) == 2
        assert state.snapshot()["novelty_window_seconds"] == 24 * 3600
        assert state.snapshot()["active_events"] == 2

        # 4h world_collision / wake gate 契約常數不變
        from src.soul import life_thread_origins, life_thread_wake_gate

        assert life_thread_wake_gate.WORLD_COLLISION_WINDOW_HOURS == 4
        assert life_thread_origins.WORLD_COLLISION_WINDOW_HOURS == 4

    def test_f3_wake_gate_decision_is_unaffected_by_world_log(self, tmp_path):
        """F.3: wake gate 的輸入面（perception trace 行）不受 World Log 影響，
        且 ``world_collision`` 喚醒判定前後完全一致。"""
        middleware, trace_path = self._middleware(tmp_path)
        bus = RecordingBus()
        source, we = _make_news_case(bus)
        assert _run(source._emit_via_bus(we)) is True
        _run(middleware.handle_event(bus.published[0]))

        before = trace_path.read_bytes()

        from src.soul.life_thread_wake_gate import evaluate_wake_gate

        now_epoch = datetime.now(timezone.utc).timestamp()
        collision_rows = [
            {
                "accepted": True,
                "source": "calendar",
                "event_id": "world-collision-probe",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "extra": {"summary": "world collision probe (inside 4h window)"},
            }
        ]
        decision_before = evaluate_wake_gate(
            agent_id="agent_world_log_test",
            current_time=now_epoch,
            timeslot="daytime",
            active_threads=[],
            capacity_limit=1,
            recent_perceptions=collision_rows,
        )
        assert decision_before.should_wake is True, "anti-vacuity: 碰撞路徑必須真的被走到"
        assert decision_before.origin_type == "world_collision"

        for _ in range(4):
            assert wl.record_world_event(we) is True

        assert trace_path.read_bytes() == before

        decision_after = evaluate_wake_gate(
            agent_id="agent_world_log_test",
            current_time=now_epoch,
            timeslot="daytime",
            active_threads=[],
            capacity_limit=1,
            recent_perceptions=collision_rows,
        )
        assert decision_after.should_wake == decision_before.should_wake
        assert decision_after.origin_type == decision_before.origin_type
        assert decision_after.reason == decision_before.reason
        assert decision_after.seed_hint == decision_before.seed_hint

    def test_f4_world_fact_text_helper_behaviour_unchanged(self):
        """F.4: World Fact Text（``_fact_summary_extra``）行為逐字不變。"""
        assert _fact_summary_extra(False, "anything") == {}
        assert _fact_summary_extra(True, "") == {}
        assert _fact_summary_extra(True, "   ") == {}
        assert _fact_summary_extra(True, None) == {}

        long_summary = "事" * 500
        extra = _fact_summary_extra(True, long_summary)
        assert extra, "accepted=True 且有文字 ⇒ 必須寫 fact text"
        value = next(iter(extra.values()))
        assert isinstance(value, str)
        assert len(value) <= 200, "fact text 必須 <= 200 字元（既有契約）"

    def test_f5_world_log_does_not_touch_production_world_artifacts(self):
        """F.5: 寫入只落在 ``data_root()/world/world_log/`` 之下，別無他處。"""
        root = data_root()
        world_dir = root / "world"
        world_dir.mkdir(parents=True, exist_ok=True)
        trace = world_dir / "perception_trace.jsonl"
        trace.write_text('{"phase": "sentinel"}\n', encoding="utf-8")
        before = trace.read_bytes()

        we = weather_world_event()
        assert wl.record_world_event(we) is True

        assert trace.read_bytes() == before
        created = sorted(
            p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()
        )
        assert "world/perception_trace.jsonl" in created
        for name in created:
            assert name == "world/perception_trace.jsonl" or name.startswith(
                "world/world_log/"
            ), f"World Log 寫入不得在別處產生檔案：{name}"
