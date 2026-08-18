"""
test_m7_proactive_context.py
M7-context (Bry 拍板 2026-08-18): 主動傳訊 context-aware 三層修法

驗證:
  A. _get_bry_silence_minutes 讀 relationships.json (純讀, 無 side effect)
  B. scheduler.start() 復活 proactive_dm 計時器 (M5.2-O-3 bug)
  C. _fire_proactive_dm 在 Bry 最近有跟該 agent 講話時 skip (避免突兀)
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from src.paths import reset_data_root
from src.soul.scheduler import SoulScheduler


def _write_relationships(soul_dir, agent_id, minutes_ago):
    """寫一份 relationships.json, user_bryan.last_interaction_at = N 分鐘前。"""
    d = soul_dir / agent_id
    d.mkdir(parents=True, exist_ok=True)
    rel = {
        "agent_id": agent_id,
        "schema_version": "4.1",
        "others": {
            "user_bryan": {
                "last_interaction_at": (
                    datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
                ).isoformat(),
            }
        },
    }
    (d / "relationships.json").write_text(
        json.dumps(rel, ensure_ascii=False), encoding="utf-8"
    )


class TestBrySilenceMinutes:
    def test_no_relationships_file_returns_none(self, tmp_path):
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            assert sched._get_bry_silence_minutes("agent_ruka") is None
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_no_user_bryan_returns_none(self, tmp_path):
        soul_dir = tmp_path / "data" / "soul"
        (soul_dir / "agent_ruka").mkdir(parents=True, exist_ok=True)
        (soul_dir / "agent_ruka" / "relationships.json").write_text(
            json.dumps({"agent_id": "agent_ruka", "others": {}}),
            encoding="utf-8",
        )
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            assert sched._get_bry_silence_minutes("agent_ruka") is None
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_recent_interaction_returns_small_minutes(self, tmp_path):
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_ruka", minutes_ago=5)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            mins = sched._get_bry_silence_minutes("agent_ruka")
            assert mins is not None
            assert 0 <= mins < 10, f"5 分鐘前應該回 ~5, 實際 {mins}"
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_old_interaction_returns_large_minutes(self, tmp_path):
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_ruka", minutes_ago=120)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            mins = sched._get_bry_silence_minutes("agent_ruka")
            assert mins is not None
            assert mins >= 100, f"120 分鐘前應該回 >=100, 實際 {mins}"
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()


class TestTimerRevival:
    def test_start_initializes_next_proactive_dm_time(self):
        async def _run():
            sched = SoulScheduler(proactive_agents=["agent_ruka"])
            assert sched._next_proactive_dm_time is None
            await sched.start()
            try:
                assert sched._next_proactive_dm_time is not None, (
                    "start() 應該復活 proactive_dm 計時器 (M5.2-O-3 bug)"
                )
            finally:
                await sched.stop()
                await asyncio.sleep(0.1)

        asyncio.run(_run())

    def test_start_does_not_override_existing_timer(self):
        async def _run():
            sched = SoulScheduler(proactive_agents=["agent_ruka"])
            fixed = datetime.now(timezone.utc)
            sched._next_proactive_dm_time = fixed
            await sched.start()
            try:
                assert sched._next_proactive_dm_time == fixed, (
                    "已有計時器時, start() 不應覆蓋"
                )
            finally:
                await sched.stop()
                await asyncio.sleep(0.1)

        asyncio.run(_run())


class TestBryOnlineGate:
    def test_fire_proactive_dm_skips_when_bry_recent(self, tmp_path, monkeypatch):
        """Bry 最近有跟 agent 講話 → _fire_proactive_dm 應 skip, 不 publish。"""
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_ruka", minutes_ago=5)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            bus = MagicMock()
            sched = SoulScheduler(
                bus=bus,
                proactive_agents=["agent_ruka"],
                proactive_dm_cooldown_seconds=0,
                quiet_hours_start=0,
                quiet_hours_end=0,
            )
            sched._all_agents = ["agent_ruka"]

            published = []

            async def fake_publish(agent_id, trigger_type, extra=None):
                published.append((agent_id, trigger_type))

            monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)

            async def _run():
                await sched._fire_proactive_dm()

            asyncio.run(_run())
            assert published == [], (
                f"Bry 最近有講話, 不應 publish proactive_dm, 實際 {published}"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()
