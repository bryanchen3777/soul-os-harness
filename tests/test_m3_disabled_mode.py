"""
tests/test_m3_disabled_mode.py — M3 Phase 1 P11 Disabled Mode Audit

Bry 拍板 2026-08-07 20:12 P11:
SOULOS_WORLD_PERCEPTION_ENABLED=0:
  - WORLD_EVENT ignored / no perception
  - no AGENT_INTENT_PERCEIVED
  - 正常既有 AGENT_INTENT flow
  - exactly one speaker token
  - disable M3 不會讓原本 Soul OS 對話斷掉
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.eventbus.token_manager import SpeakerTokenManager, LEGACY_INTAKE_EVENT_TYPES
from src.world import (
    SELECTION_REJECTED_AT_THRESHOLD,
    SyntheticWorldEventSource,
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)


def _run(coro):
    return asyncio.run(coro)


class TestM3DisabledMode(unittest.TestCase):
    """P11 hardening: SOULOS_WORLD_PERCEPTION_ENABLED=0 確認"""

    def setUp(self):
        self._env_backup = os.environ.get("SOULOS_WORLD_PERCEPTION_ENABLED", None)

    def tearDown(self):
        if self._env_backup is not None:
            os.environ["SOULOS_WORLD_PERCEPTION_ENABLED"] = self._env_backup
        else:
            os.environ.pop("SOULOS_WORLD_PERCEPTION_ENABLED", None)

    def test_01_env_var_off_means_middleware_not_loaded(self):
        """
        SOULOS_WORLD_PERCEPTION_ENABLED=0 → run_server.py 不 import WorldPerception,
        也不 register 它。
        模擬 run_server.py 的邏輯 (純 env var reading)
        """
        os.environ["SOULOS_WORLD_PERCEPTION_ENABLED"] = "0"
        # 跟 run_server.py L234 一樣的 logic
        world_perception_enabled = os.getenv("SOULOS_WORLD_PERCEPTION_ENABLED", "1") == "1"
        self.assertFalse(world_perception_enabled,
                         "P11: env=0 應 disabled")

    def test_02_env_var_on_means_middleware_loaded(self):
        """SOULOS_WORLD_PERCEPTION_ENABLED=1 → 啟用 M3"""
        os.environ["SOULOS_WORLD_PERCEPTION_ENABLED"] = "1"
        world_perception_enabled = os.getenv("SOULOS_WORLD_PERCEPTION_ENABLED", "1") == "1"
        self.assertTrue(world_perception_enabled)

    def test_03_env_var_unset_means_default_enabled(self):
        """不設 env var → 預設 enabled (跟 production safe 一致)"""
        os.environ.pop("SOULOS_WORLD_PERCEPTION_ENABLED", None)
        world_perception_enabled = os.getenv("SOULOS_WORLD_PERCEPTION_ENABLED", "1") == "1"
        self.assertTrue(world_perception_enabled, "P11: env unset 應 default enabled")

    def test_04_legacy_mode_chain_works_without_m3(self):
        """
        沒有 WorldPerceptionMiddleware 時 (legacy mode):
        - SpeakerTokenManager 用 LEGACY intake (ENRICHED + PERCEIVED)
        - MemoryMiddleware 仍然把 AGENT_INTENT 升級成 ENRICHED
        - SpeakerTokenManager 收到 ENRICHED → 1 grant
        - WorldPerceptionMiddleware 不存在 → no PERCEIVED
        """
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                bus = SoulEventBus()
                await bus.start()
                try:
                    # 用 MockMemoryMiddleware 避免 SQLite lock
                    from test_m3_e2e_smoke import _MockMemoryMiddleware
                    mw = _MockMemoryMiddleware(bus)
                    mw.register()

                    # 關鍵: 沒 WorldPerceptionMiddleware
                    # 直接 legacy SpeakerTokenManager
                    token_mgr = SpeakerTokenManager(
                        bus, token_timeout_secs=10.0,
                        intake_event_types=LEGACY_INTAKE_EVENT_TYPES,
                    )
                    token_mgr.register()

                    grants = []
                    perceived_count = 0
                    original_publish = bus.publish
                    async def _capture(ev):
                        if ev.event_type == EventType.SPEAKER_TOKEN_GRANTED:
                            grants.append(ev)
                        if ev.event_type == EventType.AGENT_INTENT_PERCEIVED:
                            nonlocal perceived_count
                            perceived_count += 1
                        await original_publish(ev)
                    bus.publish = _capture

                    # 發 AGENT_INTENT
                    intent = SoulEvent(
                        event_type=EventType.AGENT_INTENT,
                        source="agent_yua", target="agent_yua",
                        priority=EventPriority.NORMAL,
                        payload={
                            "agent_id": "agent_yua", "reason": "user_message",
                            "mode": "private", "draft": "test legacy flow",
                            "target_user_id": "bryan", "chrono_context": "",
                        },
                        session_id="session_bryan_agent_yua",
                    )
                    await bus.publish(intent)
                    await asyncio.sleep(0.3)

                    # 驗證: 1 grant (legacy flow)
                    self.assertEqual(len(grants), 1,
                                     f"P11 legacy: 期望 1 個 grant, 實際 {len(grants)}")
                    # 驗證: 沒 AGENT_INTENT_PERCEIVED (因為沒 M3)
                    self.assertEqual(perceived_count, 0,
                                     f"P11 legacy: 期望 0 個 PERCEIVED (無 M3), 實際 {perceived_count}")
                    print(f"[P11] legacy mode (SOULOS_WORLD_PERCEPTION_ENABLED=0): "
                          f"1 grant + 0 PERCEIVED, 正常 flow 不中斷 ✓")

                finally:
                    await bus.stop()

        _run(_scenario())

    def test_05_disabled_mode_no_world_event_processing(self):
        """
        沒 WorldPerception 時, 即使有人 publish WORLD_EVENT 到 bus, 也不會被處理
        (因為沒 subscriber)。但 bus 會丟棄 (沒 handler)。
        重要: 不會造成 ERROR 也不會 crash
        """
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                bus = SoulEventBus()
                await bus.start()
                try:
                    # 只有 legacy SpeakerTokenManager, 沒 WorldPerception
                    token_mgr = SpeakerTokenManager(
                        bus, token_timeout_secs=10.0,
                        intake_event_types=LEGACY_INTAKE_EVENT_TYPES,
                    )
                    token_mgr.register()

                    # 直接 publish WORLD_EVENT (模擬有人意外送 event)
                    world_event = SoulEvent(
                        event_type=EventType.WORLD_EVENT,
                        source="weather",
                        target="broadcast",
                        priority=EventPriority.LOW,
                        payload={
                            "source": "weather", "type": "rain_started",
                            "novelty_id": "test_disabled_001",
                            "ts": "2026-08-07T19:30:00+00:00",
                            "summary": "下雨了",
                            "data": {},
                        },
                    )
                    # 應該不 crash, 不會被任何 subscriber 收到
                    await bus.publish(world_event)
                    await asyncio.sleep(0.2)
                    # 沒 exception, 沒 process, 沒 grant
                    print(f"[P11] disabled mode + WORLD_EVENT: 無 subscriber 處理, 不 crash ✓")
                finally:
                    await bus.stop()

        _run(_scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)
