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
    """

    def __init__(self, injector: Optional[WorldEventInjector] = None) -> None:
        """
        M3.1 Phase B: optional injector constructor.

        向後兼容: SyntheticWorldEventSource() 仍可呼叫 (Phase A 行為 100% 保留)
        """
        self._injector: Optional[WorldEventInjector] = injector

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

        Flow:
          1. construct M3 WorldEvent (source 自動 hardcode 為 "synthetic")
          2. __post_init__ validation 自動跑 (priority 必須是 int)
          3. 如果 self._injector is not None → await injector.inject(event)
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
          - Injector exception 必須 propagate (不 silent swallow, 不 retry)
          - 沒有 injector 時仍 return event (只 build, 不 inject)
          - 不改既有 build_*() factory methods
          - 不發起 background task / scheduler
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

        if self._injector is not None:
            # 必須 propagate, 不 silent swallow
            await self._injector.inject(event)

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
