"""
test_m7_4_event_schedule.py
M7-4 (Bry 拍板 2026-08-2x): 復活 event schedule timer

根因: register_dream_event 只在 run_server.py 呼叫一次, 若沒呼叫,
      _next_event_time 停在 None → _is_event_time 永遠 False → event 永不觸發。
修法: start() 時若 _next_event_time is None, 照 register_dream_event 的
      間隔 pattern (event_min_interval_minutes=240 / max=480) 排首次 event。

驗證 (對應工單驗收):
  A. _next_event_time is None → _is_event_time 為 False
  B. start() 之後 _next_event_time 為未來時間 (240-480 min 範圍)
  C. start() 不覆蓋既有計時器
  D. 時間到達後可走既有 _fire_event; 觸發後會重排 (_next_event_time 更新為更未來)
  E. dream / proactive / morning / night 回歸不漂
"""
import asyncio
from datetime import datetime, time, timedelta

from src.soul.scheduler import SoulScheduler
from src.timezone_utils import now_local


class TestEventTimerState:
    def test_none_event_time_is_not_event_time(self):
        """驗收 A: _next_event_time is None → _is_event_time 為 False."""
        sched = SoulScheduler()
        assert sched._next_event_time is None
        assert sched._is_event_time(now_local()) is False

    def test_start_initializes_next_event_time_to_future(self):
        """驗收 B: start() 之後 _next_event_time 為未來時間 (240-480 min 範圍)."""
        async def _run():
            sched = SoulScheduler()
            assert sched._next_event_time is None
            await sched.start()
            try:
                assert sched._next_event_time is not None, (
                    "start() 應該排定首次 event (M7-4)"
                )
                before = now_local()
                t = sched._next_event_time
                # 240-480 min pattern (不是 30 min longing interval)
                assert t > before, f"next_event_time 應該是未來, 實際 {t} vs {before}"
                assert (
                    before + timedelta(minutes=239)
                    <= t
                    <= before + timedelta(minutes=481)
                ), (
                    f"間隔應落在 240-480 min (register_dream_event pattern), "
                    f"實際 {(t - before).total_seconds() / 60:.1f} min"
                )
            finally:
                await sched.stop()
                await asyncio.sleep(0.1)

        asyncio.run(_run())

    def test_start_does_not_override_existing_timer(self):
        """驗收 C: 已有計時器時, start() 不覆蓋."""
        async def _run():
            sched = SoulScheduler()
            fixed = now_local() + timedelta(hours=10)
            sched._next_event_time = fixed
            await sched.start()
            try:
                assert sched._next_event_time == fixed, (
                    "已有 event 計時器時, start() 不應覆蓋"
                )
            finally:
                await sched.stop()
                await asyncio.sleep(0.1)

        asyncio.run(_run())


class TestEventFiresAndReschedules:
    def test_fire_event_reschedules_to_future(self):
        """驗收 D: 時間到達 → _fire_event 觸發後重排為更未來."""
        async def _run():
            sched = SoulScheduler()
            sched._all_agents = ["agent_ruka", "agent_yua"]
            # 模擬時間已到達: 上次排程時間在過去
            sched._next_event_time = now_local() - timedelta(minutes=1)
            assert sched._is_event_time(now_local()) is True, (
                "now >= _next_event_time 應該判定為 event 時間"
            )
            await sched._fire_event()
            before = now_local()
            t = sched._next_event_time
            assert t is not None
            assert t > before, f"觸發後應重排為未來, 實際 {t} vs {before}"
            assert (
                before + timedelta(minutes=239)
                <= t
                <= before + timedelta(minutes=481)
            ), (
                f"重排間隔應落在 240-480 min, "
                f"實際 {(t - before).total_seconds() / 60:.1f} min"
            )

        asyncio.run(_run())

    def test_whitelist_skip_still_reschedules(self):
        """M1.7 語意保留: 抽中全部被 whitelist 過濾時 silent skip 但仍重排."""
        async def _run():
            sched = SoulScheduler(proactive_agents=["agent_zzz"])
            sched._all_agents.append("agent_ruka")
            sched._all_agents.append("agent_yua")
            sched._next_event_time = now_local() - timedelta(minutes=1)
            await sched._fire_event()
            before = now_local()
            t = sched._next_event_time
            assert t is not None and t > before, (
                "whitelist 全過濾也應重排 (避免下次又被卡住)"
            )

        asyncio.run(_run())


class TestRegressionNoDrift:
    def test_dream_semantics_unchanged(self):
        """驗收 E: dream = _is_dream_time (時鐘窗口) 不漂."""
        sched = SoulScheduler(night_time=time(22, 0), dream_minutes_after_night=5)
        # night 22:00 + 5 min = 22:05 窗口內 → True
        inside = datetime(2026, 1, 1, 22, 5, 30)
        assert sched._is_dream_time(inside) is True
        # 窗口外 → False
        outside = datetime(2026, 1, 1, 22, 20, 0)
        assert sched._is_dream_time(outside) is False
        # 未到 night slot → False
        before_night = datetime(2026, 1, 1, 21, 0, 0)
        assert sched._is_dream_time(before_night) is False

    def test_morning_night_slots_unchanged(self):
        """驗收 4: morning / night slot 判定不漂."""
        sched = SoulScheduler(morning_time=time(8, 0), night_time=time(22, 0))
        assert sched._slot_for_time(datetime(2026, 1, 1, 8, 0, 30)) == "morning"
        assert sched._slot_for_time(datetime(2026, 1, 1, 22, 0, 30)) == "night"
        assert sched._slot_for_time(datetime(2026, 1, 1, 12, 0, 0)) is None

    def test_proactive_timer_regression(self):
        """驗收 4: proactive_dm 計時器語義不漂 (None → False, start 後初始化)."""
        async def _run():
            sched = SoulScheduler(proactive_agents=["agent_ruka"])
            # None → False (M7-longing 修法前的既有行為)
            assert sched._is_proactive_dm_time(now_local()) is False
            await sched.start()
            try:
                # M7-longing 回歸: start() 仍初始化 proactive_dm 計時器
                assert sched._next_proactive_dm_time is not None
            finally:
                await sched.stop()
                await asyncio.sleep(0.1)

        asyncio.run(_run())
