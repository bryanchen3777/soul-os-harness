"""
src/world/source/synthetic.py — Soul OS M3.1 Phase A + Phase B

SyntheticWorldEventSource (M3 Phase 1 既有邏輯, 完全保留):

- 5 個 deterministic test scenarios (跟 brief 對齊)
- 透過 WorldPerceptionMiddleware.publish_world_event() 進 bus
- 不接外部 API, 不打 network
- 用於 unit test + integration test + (未來) manual live test

M3.1 Phase A (Bry 拍板 2026-08-08 01:57): conform WorldEventSource 介面
- source_id = "synthetic"
- start() / stop() 都是 no-op (沒有 background process)
- build_*() factory methods 全部保留 (不重寫 event generation logic)

M3.1 Phase B (Bry 拍板 2026-08-08 02:59): injection capability
- 新增 __init__(injector=None) optional constructor
- 新增 set_injector(injector) capability detection method
  (Registry 透過 getattr(source, "set_injector", None) 呼叫)
- 新增 emit_event(...) async method: 建 M3 WorldEvent → 透過已 attach injector 注入
- injector.inject() exception 必須 propagate (不 silent swallow, 不 retry)
- 既有 build_*() 100% 保留

M5.15-3 (Bry 派工 2026-08-12 18:45): Event Bus canonical integration
- 新增 __init__(bus=None) optional constructor (additive, 跟 injector 並存)
- 新增 set_bus(bus) capability detection method
  (Registry / Dispatcher 透過 getattr(source, "set_bus", None) 呼叫)
- emit_event 路徑解析 (Bry 拍板):
    1. self._bus is not None → bus.publish(SoulEvent(WORLD_EVENT, target="broadcast",
       priority=NORMAL, payload=event.to_payload()))
       → canonical Event Bus path (M5.15-1 F1 P1 architecture fix)
       → bus dispatch 給所有 subscribers (WorldPerceptionMiddleware + WorldInnerLifeAdapter)
       → single processing path per subscriber, no double perception
    2. self._bus is None and self._injector is not None → injector.inject(event)
       → M3.1 Phase B 既有 direct path (backward compat 100% 保留)
    3. both None → return event without delivering (test / build-only mode)
- bus publish 失敗 (bus not running / queue full) 由 bus 自己 log warning / error
  (M5.7-4 documented failure mode, 不 silent swallow 在 source 端, 不 retry)
- 既有的 build_*() 100% 保留 (跟 Phase A 一致)
- 既有的 set_injector / get_injector 100% 保留
- 既有的 WorldEventSource ABC 0 改 (set_bus 是 capability detection,
  不在 ABC abstract method 列表, real source 不強制有 set_bus)

位置說明:
  Bry 派工 A' cleanup (2026-08-08 02:21):
  - 這個檔案是 SyntheticWorldEventSource 邏輯的 canonical 位置
  - src/world/source.py 是最薄 compatibility shim
  - 從 src.world.source.synthetic 拿 class (subpackage 內)
  - 既有 M3 tests 從 `from src.world import SyntheticWorldEventSource`
    透過 src/world/source/__init__.py re-export 仍可拿到, 行為 100% 不變
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent

from ..base import WorldEventSource
from ..injector import WorldEventInjector
from ..perception import WorldEvent

logger = logging.getLogger("soul_os.world.source")


# ───────────────────────────────────────────────────────────
# 5 個 Synthetic Test Scenarios
# Bry 拍板 2026-08-07 19:40 對齊 brief §6:
# Test A — Weather: Rain started
# Test B — News: Random celebrity news
# Test C — Calendar: User has important event in 30 minutes
# Test D — Weather: Minor temperature fluctuation
# Test E — Social: User sent message indicating they are going outside
# ───────────────────────────────────────────────────────────

SYNTHETIC_TEST_EVENTS: Dict[str, Dict[str, Any]] = {
    "TEST_A_rain_started": {
        "source": "weather",
        "type": "rain_started",
        "novelty_id": "weather_rain_20260807",
        "summary": "外面開始下雨了。",
        "data": {"precipitation_mm": 2.5, "intensity": "light"},
    },
    "TEST_B_celebrity_news": {
        "source": "news",
        "type": "celebrity_news",
        "novelty_id": "news_celebrity_20260807_001",
        "summary": "某明星在節目上說了句無聊的話。",
        "data": {"celebrity": "unknown", "topic": "smalltalk"},
    },
    "TEST_C_calendar_event_30min": {
        "source": "calendar",
        "type": "calendar_event",
        "novelty_id": "calendar_meeting_20260807_1500",
        "summary": "30 分鐘後有重要會議。",
        "data": {"event_name": "重要會議", "minutes_until": 30},
    },
    "TEST_D_temp_fluctuation": {
        "source": "weather",
        "type": "weather_temp_change",
        "novelty_id": "weather_temp_20260807",
        "summary": "今天氣溫比昨天低了 1 度。",
        "data": {"delta_c": -1, "absolute_c": 22},
    },
    "TEST_E_user_going_outside": {
        "source": "social",
        "type": "user_going_outside",
        "novelty_id": "social_bry_going_outside_20260807",
        "summary": "Bry 說他準備出門。",
        "data": {"actor": "bry", "intent": "going_outside"},
    },
}


class SyntheticWorldEventSource(WorldEventSource):
    """
    確定性 test event source, 發布 5 個 scenarios 跟任何 caller-constructed event。

    設計:
    - build_*() helper: 回 WorldEvent (不直接 publish, 給 test 驗資料用)
    - emit_event() async: 建 WorldEvent + 透過已 attach injector 注入
    - 不寫死 user context: user context 由 WorldPerceptionMiddleware 從 event bus 拿
      (這也是 Bry 拍板: 「personal_significance 不能從 event payload 拿」)

    M3.1 Phase A (Bry 拍板 2026-08-08 01:57): conform WorldEventSource 介面
    - source_id = "synthetic"
    - start() / stop() 都是 no-op (沒有 background process)
    - build_*() factory methods 全部保留 (不重寫 event generation logic)

    M3.1 Phase B (Bry 拍板 2026-08-08 02:59): injection capability
    - __init__(injector=None) optional constructor (向後兼容 Phase A 的 SyntheticWorldEventSource())
    - set_injector(injector) capability detection method (Registry 用 getattr 呼叫)
    - emit_event(...) async method
    - 沒有 injector 時 emit_event 仍可 work (建 event 但不 inject, return event 給 caller)

    M5.15-3 (Bry 派工 2026-08-12 18:45): Event Bus canonical integration (additive)
    - __init__(bus=None) optional constructor (跟 injector 並存, 向後兼容)
    - set_bus(bus) capability detection method (Registry / Dispatcher 用 getattr 呼叫)
    - emit_event 路徑解析:
        1. self._bus is not None → bus.publish(SoulEvent(WORLD_EVENT, ...))
           (M5.15-3 canonical path, downstream integration 看到 source-originated events)
        2. self._bus is None and self._injector is not None → injector.inject(event)
           (M3.1 Phase B direct path, backward compat 100% 保留)
        3. both None → return event without delivering (test / build-only)
    - 既有 build_*() 100% 保留
    - 既有 set_injector / get_injector 100% 保留
    - WorldEventSource ABC 0 改 (set_bus 是 capability detection, 不在 ABC)
    """

    def __init__(
        self,
        injector: Optional[WorldEventInjector] = None,
        bus: Optional[SoulEventBus] = None,
    ) -> None:
        """
        M3.1 Phase B: optional injector constructor.
        M5.15-3: optional bus constructor (additive, 跟 injector 並存).

        向後兼容: SyntheticWorldEventSource() 仍可呼叫 (Phase A 行為 100% 保留)
        向後兼容: SyntheticWorldEventSource(injector=X) 仍可呼叫 (Phase B 行為 100% 保留)

        Path resolution (emit_event 內部):
          1. self._bus is not None → bus publish (canonical)
          2. self._bus is None and self._injector is not None → injector (legacy direct)
          3. both None → return event without delivering
        """
        self._injector: Optional[WorldEventInjector] = injector
        self._bus: Optional[SoulEventBus] = bus

    @property
    def source_id(self) -> str:
        """M3.1 Phase A: source category identifier。對齊 VALID_SOURCES。"""
        return "synthetic"

    # ── M3.1 Phase B: capability detection method ──

    def set_injector(self, injector: Optional[WorldEventInjector]) -> None:
        """
        M3.1 Phase B: capability detection method.

        Registry.attach_injector() 用 getattr(source, "set_injector", None)
        呼叫這個 method (如果有), 沒有的 source 跳過。

        Args:
            injector: WorldEventInjector 實作, 或 None (detach)
        """
        self._injector = injector
        logger.debug(
            f"[SyntheticWorldEventSource] set_injector({injector!r})"
        )

    def get_injector(self) -> Optional[WorldEventInjector]:
        """Phase B: 給 test / debug 拿當前 injector (Optional[None] = 沒 attach)。"""
        return self._injector

    # ── M5.15-3: bus capability detection (additive) ──

    def set_bus(self, bus: Optional[SoulEventBus]) -> None:
        """
        M5.15-3: capability detection method for bus attachment.

        Registry / Dispatcher 用 getattr(source, "set_bus", None)
        呼叫這個 method (如果有), 沒有的 source 跳過 (跟 set_injector 同 pattern)。

        Args:
            bus: SoulEventBus 實作, 或 None (detach)
        """
        self._bus = bus
        logger.debug(
            f"[SyntheticWorldEventSource] set_bus({bus!r})"
        )

    def get_bus(self) -> Optional[SoulEventBus]:
        """M5.15-3: 給 test / debug 拿當前 bus (None = 沒 attach)。"""
        return self._bus

    # ── M3.1 Phase A: lifecycle (no-op) ──

    async def start(self) -> None:
        """
        M3.1 Phase A: 對 synthetic 是 no-op (沒有 background process)。

        對未來 real source 將會建立 connection / 設定 webhook / 初始化。
        """
        logger.debug("[SyntheticWorldEventSource] start() — no-op (synthetic)")

    async def stop(self) -> None:
        """
        M3.1 Phase A: 對 synthetic 是 no-op。

        Idempotent contract: 多次呼叫安全 (Bry 拍板)。
        """
        logger.debug("[SyntheticWorldEventSource] stop() — no-op (synthetic)")

    # ── helpers ──

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── M3.1 Phase B: emit_event ──

    async def emit_event(
        self,
        type: str,
        summary: str,
        novelty_id: str,
        data: Optional[Dict[str, Any]] = None,
        priority: int = 0,
        ts: Optional[str] = None,
    ) -> WorldEvent:
        """
        M3.1 Phase B (Bry 拍板 02:59): emit a new WorldEvent and inject via attached injector.
        M5.15-3 (Bry 派工 2026-08-12 18:45): emit a new WorldEvent via Event Bus
        when bus is attached (canonical path).

        Flow (path resolution 派工, Bry 拍板):
          1. construct M3 WorldEvent (source 自動 hardcode 為 "synthetic")
          2. __post_init__ validation 自動跑 (priority 必須是 int)
          3. Path resolution:
             a. self._bus is not None → bus.publish(SoulEvent(WORLD_EVENT, target="broadcast",
                priority=NORMAL, payload=event.to_payload()))
                → canonical Event Bus path
                → bus dispatch 給所有 subscribers (WorldPerceptionMiddleware + WorldInnerLifeAdapter)
             b. elif self._injector is not None → await injector.inject(event)
                → legacy M3.1 Phase B direct path (backward compat)
             c. else (both None) → 跳過 delivery, 只 return event
          4. return event

        Args:
            type: 細分類型 e.g. "rain_started"
            summary: 一句話客觀描述
            novelty_id: 同一事實識別 (去重 key, 沿用 M3 既有欄位)
            data: optional 額外 payload, default = {} (沿用 M3 default_factory)
            priority: 預設 0, 必須是 int (M3.1 Phase B 新增)
            ts: optional ISO 8601 UTC timestamp, default = datetime.now()

        Returns:
            WorldEvent (M3 既有 class, 多了 priority 欄位)

        Contract:
          - Injector / bus publish exception 必須 propagate (不 silent swallow, 不 retry)
          - 沒有 injector 沒有 bus 時仍 return event (只 build, 不 deliver)
          - 不改既有 build_*() factory methods
          - 不發起 background task / scheduler
          - M5.15-3: bus 失敗 (bus not running / queue full) 由 bus 內部 log warning / error
            (M5.7-4 documented failure mode), source 不 silent swallow, 不 retry

        M5.15-3 Bus dispatch contract:
          - Source 發 SoulEvent(WORLD_EVENT, target="broadcast", priority=NORMAL)
          - SoulEvent.priority = NORMAL (跟 M5.15-2 spec 對齊)
          - WorldEvent.priority 經 payload 傳遞 (M5.4-3.1 contract repair, 向後相容)
          - Bus dispatch 給 WorldPerceptionMiddleware (subscriber_id="world_perception")
            + WorldInnerLifeAdapter (subscriber_id="world_inner_life_adapter")
          - Each subscriber 收到 exactly 1 次 (bus 內部 match 一次, dispatch 一次)
          - Middleware 內 _on_world_event 處理 (state.add + trace.write), 不 publish 回 bus
            (no recursive publish)
          - Adapter 內 qualify + dedup + create_event, 不 publish 回 bus
            (no recursive publish, no duplicate InnerLifeEvent 因為 novelty_id dedup)
        """
        if data is None:
            data = {}
        if ts is None:
            ts = SyntheticWorldEventSource._now_iso()

        event = WorldEvent(
            source=self.source_id,  # 永遠 "synthetic"
            type=type,
            novelty_id=novelty_id,
            ts=ts,
            summary=summary,
            data=data,
            priority=priority,
        )

        # M5.15-3: bus path takes priority over injector path
        if self._bus is not None:
            # Canonical Event Bus path (M5.15-3 + M5.15-2 architecture decision)
            # WorldEventSchema 100% preserved (to_payload is read-only on event fields)
            soul_event = SoulEvent(
                event_type=EventType.WORLD_EVENT,
                source=self.source_id,
                target="broadcast",
                priority=EventPriority.NORMAL,
                payload=event.to_payload(),
            )
            # bus.publish failure modes per M5.7-4 (logged in bus, not silent swallow
            # in source, not retry) — caller responsibility
            await self._bus.publish(soul_event)
        elif self._injector is not None:
            # M3.1 Phase B legacy direct path (backward compat 100% 保留)
            # 必須 propagate, 不 silent swallow
            await self._injector.inject(event)
        # else: 兩者都 None, 跳過 delivery, 只 return event (test / build-only mode)

        return event

    # ── M3 既有 build_*() factory methods (100% 保留) ──

    @staticmethod
    def build_rain_started() -> WorldEvent:
        """Test A: 開始下雨。"""
        spec = SYNTHETIC_TEST_EVENTS["TEST_A_rain_started"]
        return WorldEvent(
            source=spec["source"],
            type=spec["type"],
            novelty_id=spec["novelty_id"],
            ts=SyntheticWorldEventSource._now_iso(),
            summary=spec["summary"],
            data=spec["data"],
        )

    @staticmethod
    def build_celebrity_news() -> WorldEvent:
        """Test B: 隨機明星新聞。"""
        spec = SYNTHETIC_TEST_EVENTS["TEST_B_celebrity_news"]
        return WorldEvent(
            source=spec["source"],
            type=spec["type"],
            novelty_id=spec["novelty_id"],
            ts=SyntheticWorldEventSource._now_iso(),
            summary=spec["summary"],
            data=spec["data"],
        )

    @staticmethod
    def build_calendar_event_30min() -> WorldEvent:
        """Test C: 30 分鐘後重要會議。"""
        spec = SYNTHETIC_TEST_EVENTS["TEST_C_calendar_event_30min"]
        return WorldEvent(
            source=spec["source"],
            type=spec["type"],
            novelty_id=spec["novelty_id"],
            ts=SyntheticWorldEventSource._now_iso(),
            summary=spec["summary"],
            data=spec["data"],
        )

    @staticmethod
    def build_temp_fluctuation() -> WorldEvent:
        """Test D: 微小溫度變化。"""
        spec = SYNTHETIC_TEST_EVENTS["TEST_D_temp_fluctuation"]
        return WorldEvent(
            source=spec["source"],
            type=spec["type"],
            novelty_id=spec["novelty_id"],
            ts=SyntheticWorldEventSource._now_iso(),
            summary=spec["summary"],
            data=spec["data"],
        )

    @staticmethod
    def build_user_going_outside() -> WorldEvent:
        """Test E: Bry 說要出門。"""
        spec = SYNTHETIC_TEST_EVENTS["TEST_E_user_going_outside"]
        return WorldEvent(
            source=spec["source"],
            type=spec["type"],
            novelty_id=spec["novelty_id"],
            ts=SyntheticWorldEventSource._now_iso(),
            summary=spec["summary"],
            data=spec["data"],
        )

    @staticmethod
    def build_all_five() -> List[WorldEvent]:
        """一次建 5 個 (給 Test 7 — Perception Budget 用)。"""
        return [
            SyntheticWorldEventSource.build_rain_started(),
            SyntheticWorldEventSource.build_temp_fluctuation(),
            SyntheticWorldEventSource.build_celebrity_news(),
            SyntheticWorldEventSource.build_calendar_event_30min(),
            SyntheticWorldEventSource.build_user_going_outside(),
        ]
