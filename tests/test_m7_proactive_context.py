"""
test_m7_proactive_context.py
M7-context / M7-longing (Bry 拍板 2026-08-18): 主動傳訊 context-aware + 想念驅動

驗證:
  A. _get_bry_silence_minutes 讀 relationships.json (純讀)
  B. scheduler.start() 復活 proactive_dm 計時器 (M5.2-O-3 bug)
  C. _get_base_intimacy 讀 config intimacy_level (角色差異化)
  D. _get_agent_longing = 依戀 × 有效沉默 (現算)
  E. _fire_proactive_dm 想念未達門檻時 skip (不轟炸)
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from src.paths import reset_data_root
from src.soul.scheduler import (
    LONGING_THRESHOLD,
    SoulScheduler,
)


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
                    "start() 應該排定首次檢查 (M5.2-O-3 bug)"
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


class TestBaseIntimacy:
    def test_returns_config_intimacy(self):
        sched = SoulScheduler()
        assert sched._get_base_intimacy("agent_ruka") == 60.0
        assert sched._get_base_intimacy("agent_yua") == 80.0
        assert sched._get_base_intimacy("agent_ram") == 40.0

    def test_unknown_agent_returns_default(self):
        sched = SoulScheduler()
        assert sched._get_base_intimacy("agent_unknown") == 50.0


class TestAgentLonging:
    def test_no_interaction_returns_zero(self, tmp_path):
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            assert sched._get_agent_longing("agent_ruka") == 0.0
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_long_silence_high_intimacy_crosses_threshold(self, tmp_path):
        """Yua(80) 沉默 12h → 想念 0.4 >= 0.3 門檻。"""
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_yua", minutes_ago=720)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            longing = sched._get_agent_longing("agent_yua")
            # 0.8 × (720/1440) = 0.4
            assert abs(longing - 0.4) < 0.05, f"Yua 12h 應 ~0.4, 實際 {longing}"
            assert longing >= LONGING_THRESHOLD
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_same_silence_low_intimacy_below_threshold(self, tmp_path):
        """Ram(40) 同樣 12h 沉默 → 想念 0.2 < 0.3 (角色差異化)。"""
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_ram", minutes_ago=720)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            sched = SoulScheduler()
            longing = sched._get_agent_longing("agent_ram")
            # 0.4 × (720/1440) = 0.2
            assert abs(longing - 0.2) < 0.05, f"Ram 12h 應 ~0.2, 實際 {longing}"
            assert longing < LONGING_THRESHOLD
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()


class TestLongingGate:
    def test_fire_proactive_dm_skips_when_longing_below_threshold(self, tmp_path, monkeypatch):
        """Bry 最近有講話 → 想念未達門檻 → _fire_proactive_dm skip, 不 publish。"""
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
                f"想念未達門檻, 不應 publish proactive_dm, 實際 {published}"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()

    def test_fire_proactive_dm_publishes_when_longing_reached(self, tmp_path, monkeypatch):
        """Yua(80) 沉默 12h → 想念 0.4 >= 0.3 → 應 publish。"""
        soul_dir = tmp_path / "data" / "soul"
        _write_relationships(soul_dir, "agent_yua", minutes_ago=720)
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            bus = MagicMock()
            sched = SoulScheduler(
                bus=bus,
                proactive_agents=["agent_yua"],
                proactive_dm_cooldown_seconds=0,
                quiet_hours_start=0,
                quiet_hours_end=0,
            )
            sched._all_agents = ["agent_yua"]

            published = []

            async def fake_publish(agent_id, trigger_type, extra=None):
                published.append((agent_id, trigger_type))

            monkeypatch.setattr(sched, "_publish_agency_trigger", fake_publish)

            async def _run():
                await sched._fire_proactive_dm()

            asyncio.run(_run())
            assert ("agent_yua", "proactive_dm") in published, (
                f"想念達門檻應 publish, 實際 {published}"
            )
        finally:
            del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()
