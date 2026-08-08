"""
src/world/source.py — Soul OS M3 Phase 1

SyntheticWorldEventSource (Bry 拍板 2026-08-07 19:40):
- 5 個 deterministic test scenarios (跟 brief 對齊)
- 透過 WorldPerceptionMiddleware.publish_world_event() 進 bus
- 不接外部 API, 不打 network
- 用於 unit test + integration test + (未來) manual live test
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .perception import WorldEvent

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


class SyntheticWorldEventSource:
    """
    確定性 test event source, 發布 5 個 scenarios 跟任何 caller-constructed event。

    設計:
    - build_*() helper: 回 WorldEvent (不直接 publish, 給 test 驗資料用)
    - publish_*() helper: 透過 callback (通常是 WorldPerceptionMiddleware._publish_event_to_bus) 進 bus
    - 不寫死 user context: user context 由 WorldPerceptionMiddleware 從 event bus 拿
      (這也是 Bry 拍板: 「personal_significance 不能從 event payload 拿」)

    用法 (test):
        source = SyntheticWorldEventSource()
        event = source.build_rain_started()  # WorldEvent, not published
        await middleware.process_world_event(event)  # 透過 middleware 進 bus
    """

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

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
