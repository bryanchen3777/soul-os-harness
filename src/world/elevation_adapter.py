"""
src/world/elevation_adapter.py — Soul OS world → elevation 直通 adapter（已降级）

**SG-1 降级（2026-08-29）**：本直通 adapter **不再直通 consume raw WorldEvent**。
SG-1 审计确认 P1：直通 adapter 对每个 WorldEvent（无 whitelist）直接 consume()，
bypass InnerLifeEvent。定稿修复：world 事件改走正确路径
``WorldInnerLifeAdapter → InnerLifeEvent → Submission Gate → consume()``
（M5.9-3 whitelist 已解冻加 news/weather，见 ``src/world/inner_life_adapter.py``）。

降级后本模块定位：
  - **观察 only / 已废弃**：``run_world_elevation()`` 不再构造引擎、不再 consume，
    只记录观察日志并返回 []。``WorldElevationAdapter`` 保留 bus 订阅接口但
    ``on_world_event`` 不再产节点（nodes_produced 恒 0）。
  - **保留映射函数**（``world_event_to_input`` / ``_world_event_content``）供
    观察 / 审计 / 测试回查（只读，无副作用）。
  - **wiring 已移除**：``scripts/run_server.py`` 不再注册 WorldElevationAdapter
    （直通 adapter 失去生产作用）。

历史背景（降级前，保留供审计）：
  - 工单：新建非 frozen 的 world→elevation 直通 adapter，把 WorldEvent
    （news / weather / calendar）直接映射成 ``ElevationInput(source_type="world_event")``，
    喂给 ``InternalizingEngine``，实现「看新闻 → 信念」路径。**0 frozen 变更，纯 additive。**
  - 背景：``src/world/inner_life_adapter.py``（M5.9-3 WorldInnerLifeAdapter）是 frozen，
    且只 qualify ``{calendar_event, user_going_outside}``，news/weather 不在 whitelist
    （fail-closed）。所以「看新闻 → 信念」不能走它。
  - WorldEvent 经 M5.15-3 已 publish 到 SoulEventBus（``bus.publish(SoulEvent(WORLD_EVENT, ...))``）。
  - 设计文档 ``docs/MEMORY-ELEVATION-DESIGN.md`` §5「活动→灵魂维度内化映射」：
    在 world source 上层加「事件类型 → 升华维度」映射，不经过 InnerLifeEvent。

映射（保留，供观察/审计）：
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
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from soul_elevation import (
    ElevationInput,
    ElevationNode,
)

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.inner_life.elevation_adapter import (
    EDGES_FILENAME,
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
# 触发函数（收到 WORLD_EVENT 后调用）
# ─────────────────────────────────────────────────────────────────────


def run_world_elevation(
    world_event: WorldEvent,
    *,
    llm: Any = None,
    store_dir: Optional[Any] = None,
    agent_id: Optional[str] = None,
) -> List[ElevationNode]:
    """**SG-1 降级**：观察 only，不再直通 consume raw WorldEvent。

    降级前：把 WorldEvent 喂给 InternalizingEngine 产出升华节点（P1：无 whitelist
    直接 consume，bypass InnerLifeEvent）。
    降级后：**不再构造引擎、不再 consume**。只记录观察日志（world 事件类型 /
    novelty_id / 映射后的 event_type），返回 []。world 事件的升华改走正确路径
    ``WorldInnerLifeAdapter → InnerLifeEvent → Submission Gate → consume()``
    （M5.9-3 whitelist 已解冻加 news/weather）。

    Args:
        world_event: 已 publish 到 bus 的 WorldEvent（news/weather/calendar）。
        llm / store_dir / agent_id: 保留签名（向后兼容），降级后不使用。

    Returns:
        恒 []（观察 only，不产节点）。失败隔离：异常时记录 warning 并返回 []。
    """
    try:
        inp = world_event_to_input(world_event, agent_id=agent_id)
        logger.info(
            f"[world→elevation][OBSERVE-ONLY] WorldEvent 不再直通 consume "
            f"(SG-1 降级, 改走 InnerLifeEvent → Submission Gate): "
            f"world_type={world_event.type} novelty_id={world_event.novelty_id} "
            f"mapped_event_type={inp.event_type}"
        )
        return []
    except Exception as exc:  # noqa: BLE001 — 失败隔离：观察失败不阻断 bus 主路径
        logger.warning(
            "run_world_elevation observe failed (不影響 WORLD_EVENT 主路径): "
            "%s: %s",
            type(exc).__name__,
            exc,
        )
        return []


# ─────────────────────────────────────────────────────────────────────
# Adapter（订阅 WORLD_EVENT + 映射 + 喂引擎）
# ─────────────────────────────────────────────────────────────────────


class WorldElevationAdapter:
    """world → elevation 直通 adapter（**SG-1 降级：观察 only**）。

    降级前：订阅 EventType.WORLD_EVENT，对每个 WorldEvent 映射成
    ElevationInput(source_type="world_event") 后喂给 InternalizingEngine，
    产出 ElevationNode 并写入 data/elevation/（P1：无 whitelist 直接 consume，
    bypass InnerLifeEvent）。

    降级后：**不再直通 consume raw WorldEvent**。保留 bus 订阅接口（register /
    unregister / handle_event）与 observability 计数，但 ``on_world_event`` 只做
    观察（记录日志），**不产节点**（nodes_produced 恒 0）。world 事件的升华改走
    正确路径 ``WorldInnerLifeAdapter → InnerLifeEvent → Submission Gate → consume()``
    （M5.9-3 whitelist 已解冻加 news/weather）。

    Lifecycle:
      1. __init__: 注入可选 llm / store_dir / agent_id / enabled（保留签名，降级后不使用）
      2. register(bus): 订阅 WORLD_EVENT on bus
      3. unregister(bus): 取消订阅
      4. handle_event(event): per-WORLD_EVENT 入口 → parse → on_world_event
      5. on_world_event(world_event): 观察 only（不再 consume，永不 raise）
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
            "[world→elevation] WorldElevationAdapter subscribed to WORLD_EVENT "
            "(OBSERVE-ONLY, SG-1 降级)"
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
        """**SG-1 降级**：观察 only，不再 consume（永不 raise）。

        降级前：映射 + 喂引擎产节点。降级后：只记录观察日志，返回 []。
        world 事件的升华改走 WorldInnerLifeAdapter → InnerLifeEvent →
        Submission Gate → consume()。
        """
        if not self.enabled:
            return []
        try:
            nodes = run_world_elevation(
                world_event,
                llm=self._llm,
                store_dir=self._store_dir,
                agent_id=self._agent_id,
            )
            # 降级后 run_world_elevation 恒返回 []（观察 only）
            self._stats["nodes_produced"] += len(nodes)
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
