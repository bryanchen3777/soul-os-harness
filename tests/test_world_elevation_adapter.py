"""
world → elevation 直通 adapter（**SG-1 降级：观察 only**）— 测试

工单：SG-1 修复。降级 world→elevation 直通 adapter：**不再直通 consume raw
WorldEvent**（P1 修复）。world 事件的升华改走正确路径
``WorldInnerLifeAdapter → InnerLifeEvent → Submission Gate → consume()``
（M5.9-3 whitelist 已解冻加 news/weather）。

降级后测试分节：
  A. WorldEvent → ElevationInput 映射保留（观察/审计用，只读无副作用）
  B. run_world_elevation() 观察 only（不再 consume，恒返回 []）
  C. 不再写 data/elevation/（直通 store 写入已移除）
  D. Adapter bus 订阅保留 + on_world_event 观察 only（nodes_produced 恒 0）
  E. 失败隔离（观察失败不阻断 bus 主路径）
  F. frozen contract 只读红线（adapter 不引用 InnerLifeWriter / SAGE 写入路径 /
     M5.9-3 WorldInnerLifeAdapter）
"""
from __future__ import annotations

import asyncio
import ast
import os
from pathlib import Path

import pytest

from src.eventbus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.paths import data_root, reset_data_root
from src.world.elevation_adapter import (
    EDGES_FILENAME,
    NODES_FILENAME,
    TRACE_FILENAME,
    WorldElevationAdapter,
    run_world_elevation,
    world_event_to_input,
)
from src.world.perception import WorldEvent

_ADAPTER_SRC = Path(__file__).resolve().parents[1] / "src" / "world" / "elevation_adapter.py"


# ── helpers ──────────────────────────────────────────────────────────


def _make_world_event(
    source: str,
    type_: str,
    *,
    novelty_id: str = "novelty-0001",
    summary: str = "test world event",
    data: dict | None = None,
) -> WorldEvent:
    return WorldEvent(
        source=source,
        type=type_,
        novelty_id=novelty_id,
        ts="2026-08-13T19:30:00+00:00",
        summary=summary,
        data=data or {},
    )


def _make_news_event() -> WorldEvent:
    return _make_world_event(
        "news",
        "news_event",
        novelty_id="news-0001",
        summary="Breaking news headline",
        data={
            "news_provider": "bbc_world",
            "news_title": "Some headline",
            "news_summary": "Some summary",
            "news_url": "http://feeds.bbci.co.uk/news/world/rss.xml",
            "news_published_at": "2026-08-13T19:30:00+00:00",
        },
    )


def _make_weather_event() -> WorldEvent:
    return _make_world_event(
        "weather",
        "rain_started",
        novelty_id="weather.25.03_121.57.2026-08-13T19.rain",
        summary="Rain started in Taipei",
        data={
            "weather_provider": "open_meteo",
            "weather_location": "25.03,121.57",
            "weather_temperature_c": 28.5,
            "weather_precipitation_mm": 2.1,
            "weather_code": 61,
        },
    )


def _make_calendar_event() -> WorldEvent:
    return _make_world_event(
        "calendar",
        "calendar_event",
        novelty_id="cal-0001",
        summary="Team meeting",
        data={"ical_uid": "some-uid", "ical_sequence": 0},
    )


def _make_soul_event(world_event: WorldEvent) -> SoulEvent:
    return SoulEvent(
        event_type=EventType.WORLD_EVENT,
        source="test_world_source",
        target="broadcast",
        priority=EventPriority.NORMAL,
        payload=world_event.to_payload(),
    )


@pytest.fixture
def isolated_root(tmp_path: Path):
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    yield data_root()
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


# ── A. WorldEvent → ElevationInput 映射 ──────────────────────────────


def test_news_event_to_input_mapping():
    inp = world_event_to_input(_make_news_event())

    assert inp.event_type == "world:news_event"  # 带 world: 前缀，命中 prior 表
    assert inp.source_id == "news-0001"
    assert inp.source_type == "world_event"
    assert inp.timestamp == "2026-08-13T19:30:00+00:00"
    assert inp.provenance["source_id"] == "news-0001"
    assert inp.provenance["source_system"] == "world"
    assert inp.provenance["world_source"] == "news"
    assert inp.provenance["world_type"] == "news_event"
    assert inp.provenance["news_title"] == "Some headline"  # data 关键字段并入
    assert inp.content == "Some headline: Some summary"  # 标题 + 摘要


def test_weather_event_to_input_mapping():
    inp = world_event_to_input(_make_weather_event())

    assert inp.event_type == "world:rain_started"
    assert inp.source_type == "world_event"
    assert inp.source_id == "weather.25.03_121.57.2026-08-13T19.rain"
    assert inp.provenance["world_source"] == "weather"
    assert inp.provenance["weather_temperature_c"] == 28.5
    assert "temperature=28.5°C" in inp.content  # 温度描述
    assert "precipitation=2.1mm" in inp.content  # 降雨描述


def test_calendar_event_to_input_mapping():
    inp = world_event_to_input(_make_calendar_event())

    assert inp.event_type == "world:calendar_event"
    assert inp.source_type == "world_event"
    assert inp.source_id == "cal-0001"
    assert inp.provenance["world_source"] == "calendar"
    assert inp.provenance["ical_uid"] == "some-uid"
    assert inp.content == "Team meeting"  # 事件标题


def test_world_event_to_input_agent_override():
    inp = world_event_to_input(_make_news_event(), agent_id="agent_rem")
    assert inp.provenance["agent_id"] == "agent_rem"


# ── B. run_world_elevation() 观察 only（不再 consume，恒返回 []）──────


def test_run_world_elevation_news_observe_only_returns_empty(tmp_path):
    """B.1: SG-1 降级 — news 事件不再直通 consume，返回 []。"""
    nodes = run_world_elevation(_make_news_event(), store_dir=tmp_path / "elevation")
    assert nodes == []  # 观察 only，不产节点


def test_run_world_elevation_calendar_observe_only_returns_empty(tmp_path):
    """B.2: SG-1 降级 — calendar 事件不再直通 consume，返回 []。"""
    nodes = run_world_elevation(_make_calendar_event(), store_dir=tmp_path / "elevation")
    assert nodes == []


def test_run_world_elevation_weather_observe_only_returns_empty(tmp_path):
    """B.3: SG-1 降级 — weather 事件不再直通 consume，返回 []。"""
    nodes = run_world_elevation(_make_weather_event(), store_dir=tmp_path / "elevation")
    assert nodes == []


# ── C. 不再写 data/elevation/（直通 store 写入已移除）────────────────


def test_run_world_elevation_no_longer_writes_store(tmp_path):
    """C.1: SG-1 降级 — 直通 adapter 不再写 data/elevation/（无 consume 无 store）。"""
    store_dir = tmp_path / "elevation"
    nodes = run_world_elevation(_make_news_event(), store_dir=store_dir)

    assert nodes == []
    assert not (store_dir / TRACE_FILENAME).exists()
    assert not (store_dir / NODES_FILENAME).exists()
    assert not (store_dir / EDGES_FILENAME).exists()


def test_default_store_dir_no_longer_written(tmp_path, monkeypatch):
    """C.2: SG-1 降级 — 缺省 store 目录也不再被直通 adapter 写入。"""
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path / "data"))
    reset_data_root()

    nodes = run_world_elevation(_make_news_event(), store_dir=None)

    assert nodes == []
    assert not (tmp_path / "data" / "elevation" / TRACE_FILENAME).exists()
    reset_data_root()


# ── D. Adapter bus 订阅 + on_world_event 接线 ─────────────────────────


def test_adapter_register_subscribes_to_world_event():
    bus = SoulEventBus()
    adapter = WorldElevationAdapter()
    adapter.register(bus)
    assert "world_elevation_adapter" in bus.get_subscribers()
    adapter.unregister(bus)
    assert "world_elevation_adapter" not in bus.get_subscribers()


def test_adapter_on_world_event_observe_only(tmp_path):
    """D.2: SG-1 降级 — on_world_event 观察 only，nodes_produced 恒 0。"""
    adapter = WorldElevationAdapter(store_dir=tmp_path / "elevation")
    nodes = adapter.on_world_event(_make_news_event())
    assert nodes == []  # 不再产节点
    assert adapter.get_stats()["nodes_produced"] == 0


def test_adapter_via_bus_end_to_end(isolated_root):
    """D.3: SG-1 降级 — bus 收到 WORLD_EVENT 但只观察，不产节点。"""
    adapter = WorldElevationAdapter()

    async def _run():
        bus = SoulEventBus()
        await bus.start()
        try:
            adapter.register(bus)
            await bus.publish(_make_soul_event(_make_news_event()))
        finally:
            await bus.stop()

    asyncio.run(_run())

    assert adapter.get_stats()["events_received"] == 1
    assert adapter.get_stats()["nodes_produced"] == 0  # 观察 only


def test_adapter_disabled_is_noop(tmp_path):
    adapter = WorldElevationAdapter(enabled=False, store_dir=tmp_path / "elevation")
    nodes = adapter.on_world_event(_make_news_event())
    assert nodes == []
    assert not (tmp_path / "elevation" / TRACE_FILENAME).exists()


# ── E. 失败隔离 ─────────────────────────────────────────────────────


def test_run_world_elevation_failure_isolation(tmp_path):
    class _BoomLLM:
        def classify(self, content, provenance, prior_node_type):
            raise RuntimeError("boom")

    nodes = run_world_elevation(
        _make_news_event(), llm=_BoomLLM(), store_dir=tmp_path / "elevation"
    )
    assert nodes == []


def test_adapter_failure_isolation(tmp_path):
    class _BoomLLM:
        def classify(self, content, provenance, prior_node_type):
            raise RuntimeError("boom")

    adapter = WorldElevationAdapter(llm=_BoomLLM(), store_dir=tmp_path / "elevation")
    nodes = adapter.on_world_event(_make_news_event())
    assert nodes == []  # 失败隔离在 run_world_elevation 内部，不 raise
    assert adapter.get_stats()["nodes_produced"] == 0


# ── F. frozen contract 只读红线 ──────────────────────────────────────


def _adapter_ast() -> ast.Module:
    return ast.parse(_ADAPTER_SRC.read_text(encoding="utf-8"))


def _assert_no_import(tree: ast.Module, forbidden: set[str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported = {alias.name for alias in node.names}
            assert not (imported & forbidden), (
                f"adapter 不得 import frozen 写入路径符号：{imported & forbidden}"
            )


def _assert_no_method_call(tree: ast.Module, forbidden: set[str]) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in forbidden:
                raise AssertionError(f"adapter 不得调用 {func.attr}()")
            if isinstance(func, ast.Name) and func.id in forbidden:
                raise AssertionError(f"adapter 不得调用 {func.id}()")


def test_adapter_does_not_import_or_call_inner_life_writer():
    """直通 adapter 是只读消费者，不得 import/调用 InnerLifeWriter 写入路径。"""
    tree = _adapter_ast()
    _assert_no_import(tree, {"InnerLifeWriter", "InnerLifeWriterStats"})
    _assert_no_method_call(tree, {"create_event", "_append_trace"})


def test_adapter_does_not_reference_sage_write_path():
    """adapter 不得 import/触发 SAGE 写入逻辑。"""
    tree = _adapter_ast()
    _assert_no_import(tree, {"MemoryEvolution", "MemoryWriter"})
    _assert_no_method_call(tree, {"apply_correction", "add_fact", "update_weight", "remove_fact"})


def test_adapter_does_not_import_frozen_world_inner_life_adapter():
    """直通 adapter 不得 import M5.9-3 WorldInnerLifeAdapter（frozen）。"""
    tree = _adapter_ast()
    _assert_no_import(tree, {"WorldInnerLifeAdapter"})
