"""
src/world/elevation_adapter.py — Soul OS world → elevation 直通 adapter（Option C）

工单：新建非 frozen 的 world→elevation 直通 adapter，把 WorldEvent
（news / weather / calendar）直接映射成 ``ElevationInput(source_type="world_event")``，
喂给 ``InternalizingEngine``，实现「看新闻 → 信念」路径。**0 frozen 变更，纯 additive。**

背景（已确认，直接采信）：
  - ``src/world/inner_life_adapter.py``（M5.9-3 WorldInnerLifeAdapter）是 frozen，
    且只 qualify ``{calendar_event, user_going_outside}``，news/weather 不在 whitelist
    （fail-closed）。所以「看新闻 → 信念」不能走它。
  - 已有 ``src/inner_life/elevation_adapter.py``：``run_elevation()`` +
    ``ElevationObserver`` + 三个映射函数（inner_life_event / v1_memory / sage_fact →
    ElevationInput）。本模块是**平行的 world 侧直通**，不经 InnerLifeEvent。
  - WorldEvent 经 M5.15-3 已 publish 到 SoulEventBus（``bus.publish(SoulEvent(WORLD_EVENT, ...))``）。
  - 设计文档 ``docs/MEMORY-ELEVATION-DESIGN.md`` §5「活动→灵魂维度内化映射」：
    在 world source 上层加「事件类型 → 升华维度」映射，不经过 InnerLifeEvent。

定位（照工单关键决策）：
  - **只读消费者 / 旁路观察者**：只读 WorldEvent，**不调用** ``InnerLifeWriter.create_event()``
    （不是第 6 个 producer），**不产 InnerLifeEvent**。
  - **不改任何 frozen contract**：不改 M5.9-3 WorldInnerLifeAdapter / InnerLifeEvent /
    TriggerEnvelope / Agency 4 stages / 4 handlers / SAGE 写入逻辑 / InnerLifeWriter /
    NarrativeTrace。
  - **只写自有 store**：写 ``data/elevation/``（elevation_trace.jsonl +
    elevation_nodes.jsonl + elevation_edges.jsonl），与 inner_life 侧 elevation_adapter
    共用同一目录与文件名（复用，不另起 store）。

映射（照工单关键决策 #2）：
  - ``event_type``  = ``"world:{world_event.type}"``（带 ``world:`` 前缀，命中
    soul-elevation prior.py 的 ``PRIOR_TABLE``：``world:news_event``→belief、
    ``world:calendar_event``→essence+trait；其余类型走 DEFAULT_PRIOR=belief）。
  - ``content``     = world 事件内容（news 用标题+摘要，weather 用温度/降雨描述，
    calendar 用事件标题）。
  - ``source_id``   = ``world_event.novelty_id``。
  - ``source_type`` = ``"world_event"``（soul-elevation models.py 已 additive 扩展）。
  - ``timestamp``   = ``world_event.ts``（已是 ISO 8601 UTC）。
  - ``provenance``  = dict（source_id / source_system / world_source / world_type /
    world_novelty_id + 原始 data 关键字段）。

接线（照工单关键决策 #4）：订阅 SoulEventBus 的 WORLD_EVENT（像 WorldInnerLifeAdapter
订阅 WORLD_EVENT 那样），收到 WORLD_EVENT 就映射 + 喂引擎。接线点在
``scripts/run_server.py``（非 frozen）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, List, Optional, Sequence

from soul_elevation import (
    ElevationInput,
    ElevationNode,
    ElevationTraceWriter,
    InternalizingEngine,
    StubElevationLLM,
)

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
    ELEVATION_DIR_NAME,
    NODES_FILENAME,
    TRACE_FILENAME,
)
from src.world.perception import WorldEvent

logger = logging.getLogger("soul_os.world.elevation_adapter")

# 直通 adapter 的 source_type（soul-elevation models.py 已 additive 扩展此值）。
SOURCE_TYPE_WORLD_EVENT = "world_event"

# 直通 adapter 的 source_system（world 层，非 InnerLifeEvent 的 VALID_SOURCE_SYSTEMS）。
SOURCE_SYSTEM_WORLD = "world"


# ─────────────────────────────────────────────────────────────────────
# 内容映射（WorldEvent → 自然语言内容，照工单关键决策 #2）
# ─────────────────────────────────────────────────────────────────────


def _world_event_content(world_event: WorldEvent) -> str:
    """把 WorldEvent 渲染成「事件内容」字符串（确定性，无 LLM）。

    照工单关键决策 #2：
      - news     → 标题 + 摘要（``data["news_title"]`` + ``data["news_summary"]``）
      - weather  → 温度 / 降雨描述（``data["weather_temperature_c"]`` /
        ``data["weather_precipitation_mm"]``）
      - calendar → 事件标题（``summary``，即 VEVENT.SUMMARY）
      - 其他     → ``summary``（一句话客观描述）
    """
    data = world_event.data or {}
    source = world_event.source

    if source == "news":
        title = str(data.get("news_title", "")).strip()
        summary = str(data.get("news_summary", "")).strip() or world_event.summary
        if title and summary:
            return f"{title}: {summary}"
        return title or summary or world_event.summary

    if source == "weather":
        temp = data.get("weather_temperature_c")
        precip = data.get("weather_precipitation_mm")
        parts: List[str] = []
        if temp is not None:
            parts.append(f"temperature={temp}°C")
        if precip is not None:
            parts.append(f"precipitation={precip}mm")
        if parts:
            return f"{world_event.summary} (" + ", ".join(parts) + ")"
        return world_event.summary

    if source == "calendar":
        # 事件标题即 summary（VEVENT.SUMMARY，M5.15-6 已存）。
        return world_event.summary

    return world_event.summary


# ─────────────────────────────────────────────────────────────────────
# 映射（WorldEvent → ElevationInput）
# ─────────────────────────────────────────────────────────────────────


def world_event_to_input(
    world_event: WorldEvent, *, agent_id: Optional[str] = None
) -> ElevationInput:
    """把 ``WorldEvent`` 映射成 ``ElevationInput``（source_type="world_event"）。

    字段映射（照工单关键决策 #2）：
      - event_type  = "world:{world_event.type}"（命中 prior.py PRIOR_TABLE）
      - content     = _world_event_content(world_event)
      - source_id   = world_event.novelty_id
      - source_type = "world_event"
      - timestamp   = world_event.ts（已是 ISO 8601 UTC）
      - provenance  = dict（source_id / source_system / world_source / world_type /
        world_novelty_id + 原始 data 关键字段）
    """
    provenance: dict[str, Any] = {
        "source_id": world_event.novelty_id,
        "source_system": SOURCE_SYSTEM_WORLD,
        "world_source": world_event.source,
        "world_type": world_event.type,
        "world_novelty_id": world_event.novelty_id,
    }
    # 原始 data 关键字段（news_title / weather_temperature_c / ical_uid 等）原样并入，
    # 供 prior / LLM 后验读取。data 来自 WorldEvent payload，JSON-serializable。
    provenance.update(dict(world_event.data or {}))

    # 归属 agent（灵魂本体）。显式传入 > 缺省（引擎 fallback 到 "default"）。
    if agent_id:
        provenance["agent_id"] = agent_id

    return ElevationInput(
        event_type=f"world:{world_event.type}",
        content=_world_event_content(world_event),
        source_id=world_event.novelty_id,
        source_type=SOURCE_TYPE_WORLD_EVENT,
        timestamp=world_event.ts,
        provenance=provenance,
    )


# ─────────────────────────────────────────────────────────────────────
# 自有 store 写入（data/elevation/，append-only，失败隔离）
# ─────────────────────────────────────────────────────────────────────


def _append_jsonl(path: Path, records: Sequence[dict]) -> bool:
    """append-only 追加 JSONL 行；写失败只告警 + 返回 False，绝不 raise。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return True
    except OSError as exc:  # 失败隔离：不阻断升华主路径
        logger.warning("elevation store write failed (%s): %s", path, exc)
        return False


def _persist_result(
    store_dir: Path,
    nodes: Sequence[ElevationNode],
    edges: Sequence[Any],
) -> None:
    """把升华节点 + 证据边持久化到 data/elevation/（自有 store，与 inner_life 侧共用）。"""
    node_records = [asdict(n) for n in nodes]
    edge_records = [asdict(e) for e in edges]
    if node_records:
        _append_jsonl(store_dir / NODES_FILENAME, node_records)
    if edge_records:
        _append_jsonl(store_dir / EDGES_FILENAME, edge_records)


def _resolve_store_dir(store_dir: Optional[Any]) -> Path:
    """解析 store 目录：显式传入 > data_root()/elevation。"""
    if store_dir is not None:
        return Path(store_dir)
    from src.paths import data_root

    return data_root() / ELEVATION_DIR_NAME


# ─────────────────────────────────────────────────────────────────────
# 触发函数（收到 WORLD_EVENT 后调用）
# ─────────────────────────────────────────────────────────────────────


def run_world_elevation(
    world_event: WorldEvent,
    *,
    llm: Any = None,
    store_dir: Optional[Any] = None,
    agent_id: Optional[str] = None,
) -> List[ElevationNode]:
    """把 WorldEvent 喂给 InternalizingEngine，产出升华节点。

    Args:
        world_event: 已 publish 到 bus 的 WorldEvent（news/weather/calendar）。
        llm: 可选 ElevationLLM 实现；缺省用 StubElevationLLM（确定性桩）。
        store_dir: 可选 store 目录（缺省 ``data_root()/elevation``）。
        agent_id: 可选归属 agent（灵魂本体）覆盖。

    Returns:
        产出的 ``ElevationNode`` 列表。失败隔离：异常时记录 warning 并返回 []
        （不 raise，不阻断 bus 主路径）。
    """
    try:
        resolved_dir = _resolve_store_dir(store_dir)
        inp = world_event_to_input(world_event, agent_id=agent_id)

        engine = InternalizingEngine(
            llm=llm if llm is not None else StubElevationLLM(),
            trace_writer=ElevationTraceWriter(str(resolved_dir / TRACE_FILENAME)),
        )

        nodes: List[ElevationNode] = list(engine.consume(inp))

        _persist_result(resolved_dir, nodes, engine.evidence_edges)

        return nodes
    except Exception as exc:  # noqa: BLE001 — 失败隔离：升华失败不阻断 bus 主路径
        logger.warning(
            "run_world_elevation failed (不影響 WORLD_EVENT 主路径): %s: %s",
            type(exc).__name__,
            exc,
        )
        return []


# ─────────────────────────────────────────────────────────────────────
# Adapter（订阅 WORLD_EVENT + 映射 + 喂引擎）
# ─────────────────────────────────────────────────────────────────────


class WorldElevationAdapter:
    """world → elevation 直通 adapter（Option C）。

    订阅 EventType.WORLD_EVENT on existing bus。对每个 WorldEvent 映射成
    ElevationInput(source_type="world_event") 后喂给 InternalizingEngine，
    产出 ElevationNode 并写入 data/elevation/（自有 store）。

    Pattern: 对齐 WorldInnerLifeAdapter 的 bus 订阅（subscriber_id 独立），
    但**不产 InnerLifeEvent**（直通，不经 M5.9-3 whitelist）。

    Lifecycle:
      1. __init__: 注入可选 llm / store_dir / agent_id / enabled
      2. register(bus): 订阅 WORLD_EVENT on bus
      3. unregister(bus): 取消订阅
      4. handle_event(event): per-WORLD_EVENT 入口 → parse → on_world_event
      5. on_world_event(world_event): 映射 + 喂引擎（失败隔离，永不 raise）
    """

    def __init__(
        self,
        *,
        llm: Any = None,
        store_dir: Optional[Any] = None,
        agent_id: Optional[str] = None,
        enabled: bool = True,
    ) -> None:
        self._llm = llm
        self._store_dir = store_dir
        self._agent_id = agent_id
        self.enabled = enabled
        # Observability counters
        self._stats = {
            "events_received": 0,
            "parse_failures": 0,
            "nodes_produced": 0,
            "elevation_failures": 0,
        }

    def register(self, bus: SoulEventBus) -> None:
        """订阅 WORLD_EVENT on bus（与 WorldInnerLifeAdapter 平行，multi-subscriber）。"""
        bus.subscribe(
            subscriber_id="world_elevation_adapter",
            handler=self.handle_event,
            event_filter={EventType.WORLD_EVENT},
        )
        logger.info(
            "[world→elevation] WorldElevationAdapter subscribed to WORLD_EVENT ✓"
        )

    def unregister(self, bus: SoulEventBus) -> None:
        bus.unsubscribe("world_elevation_adapter")

    async def handle_event(self, event: SoulEvent) -> None:
        """Per-WORLD_EVENT 入口：parse WorldEvent → on_world_event。"""
        if event.event_type != EventType.WORLD_EVENT:
            return

        self._stats["events_received"] += 1

        try:
            world_event = WorldEvent.from_payload(event.payload)
        except Exception as e:
            self._stats["parse_failures"] += 1
            logger.warning(
                f"[world→elevation] WorldEvent.from_payload 失敗, skip "
                f"(不影響主路徑): {type(e).__name__}: {e}"
            )
            return

        self.on_world_event(world_event)

    def on_world_event(self, world_event: WorldEvent) -> List[ElevationNode]:
        """映射 + 喂引擎（fire-and-forget，失败隔离，永不 raise）。"""
        if not self.enabled:
            return []
        try:
            nodes = run_world_elevation(
                world_event,
                llm=self._llm,
                store_dir=self._store_dir,
                agent_id=self._agent_id,
            )
            self._stats["nodes_produced"] += len(nodes)
            if nodes:
                logger.info(
                    f"[world→elevation] ElevationNode produced ✓ "
                    f"world_novelty_id={world_event.novelty_id} "
                    f"world_type={world_event.type} "
                    f"nodes={len(nodes)}"
                )
            return nodes
        except Exception as exc:  # noqa: BLE001 — 双保险，绝不阻断调用方
            self._stats["elevation_failures"] += 1
            logger.warning("WorldElevationAdapter.on_world_event failed: %s", exc)
            return []

    def get_stats(self) -> dict:
        """Observability counters."""
        return dict(self._stats)


__all__ = [
    "SOURCE_TYPE_WORLD_EVENT",
    "SOURCE_SYSTEM_WORLD",
    "ElevationInput",
    "ElevationNode",
    "WorldElevationAdapter",
    "world_event_to_input",
    "run_world_elevation",
]
