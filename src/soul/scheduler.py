"""
src/soul/scheduler.py — Soul OS Stage 4.2 (Part 1)

排程器 (SoulScheduler)

設計動機 (Bry 拍板 2026-07-18 18:24+):
- 「Bry 從來沒上線過, 角色世界也活」= 即使 Bry 沒來, 角色也要自己過日子
- diary 是角色自己活起來的證據, 不是 Bry 的提醒工具
- 「Bry 是被打斷的觸發之一, 不是主題」= Bry 不在觸發主路徑上
- 排程器只負責「時間到了觸發」, 不管「Bry 在不在」

最小可跑範圍 (Stage 4.2 第一刀):
- asyncio 排程器, cron-like (每天 08:00 morning + 22:00 night)
- 用本地時間 (Asia/Taipei, UTC+8)
- callback 由 register() 註冊
- Stage 4.3 LLM impression 留到下一刀, 這版先不做

Bry 19:35+ 拍板 (對 4.1 觀察期): 0.7% 機率觸發 = 不每天都觸發
- 4.2 第一刀先 100% 每天觸發, Bry 觀察 1 天後再決定要不要加 0.7% 機率

約束 (沿用 4.1 紀律):
- 「拒絕問, 強制讀」: callback 失敗 log warning, 不中斷排程器
- 「完成度標記要誠實」: 寫到哪就是哪
- 「拍板先設計再開工」: 觀察期 1 天後 Bry 拍板要不要加 4.2 缺口 (排程器夢境/事件觸發)
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, time, timedelta, timezone

# Bry 拍板 2026-08-03 18:21: 時區從 ASIA_TZ (UTC+8) 改 America/New_York (Bry 人在紐約)
# 動機: Bry 抓漏 8/2 案例 (akane 16:11 EDT 觸發 → 現狀餵 LLM 04:11 Asia/Taipei 凌晨)
# 跟 Bry 端下午脫節, mahiru 04:10 UTC 觸發 → 現狀餵 LLM 12:10 Asia/Taipei 中午
# 跟 Bry 端時間錯位 12 小時.
# 修法: 統一從 src.timezone_utils 拿 LOCAL_TZ (ZoneInfo("America/New_York")),
# 自動處理 EDT/EST 切換 (M0.4 跟 f9105f1 假設 "Windows 沒 zoneinfo" 錯了, Python 3.9+ 內建)
from src.timezone_utils import now_local
from typing import Any, Awaitable, Callable, Dict, List, Optional

# M1.1 (2026-07-31 23:30 Perplexity 派工): Event Bus 整合
# 觸發後、callback 跑之前發布 AGENT_INTENT, 讓 MemoryMiddleware 跟 SpeakerTokenManager
# 能像處理其他事件一樣接手。bus 為 Optional, 沒注入就 skip (向後相容)。
try:
    from src.eventbus import SoulEventBus
    from src.eventbus.schema import EventPriority, EventType, SoulEvent
    _EVENTBUS_AVAILABLE = True
except ImportError:
    _EVENTBUS_AVAILABLE = False

logger = logging.getLogger("soul_os.soul.scheduler")

# ───────────────────────────────────────────────────────────
# 常數 (Bry 拍板 2026-07-18 18:24+ + 19:35+)
# ───────────────────────────────────────────────────────────

# Morning / Night 觸發時間 (本地時間, Asia/Taipei UTC+8)
DEFAULT_MORNING_TIME = time(8, 0)     # 08:00 — Bry 起床前角色先醒
DEFAULT_NIGHT_TIME = time(22, 0)      # 22:00 — 睡前最後一次日記

# 觀察期 Bry 拍板 0.7% 機率觸發 (Stage 4.1 用過的概念, 4.2 預留接口)
# 第一刀先 100% 觸發, 之後 Bry 拍板要不要降到 0.7%
TRIGGER_PROBABILITY_DEFAULT = 1.0     # 4.2 第一刀: 100% 觸發
# TRIGGER_PROBABILITY_DEFAULT = 0.007  # 之後觀察期: 0.7% 機率

# 任務健康檢查 (Bry 拍板觀察期 log 頻率)
HEALTH_CHECK_INTERVAL_SECS = 300     # 5 分鐘 log 一次下次觸發時間


# ───────────────────────────────────────────────────────────
# 排程器本體
# ───────────────────────────────────────────────────────────

# Callback 簽名: async def cb(agent_id: str, slot: str) -> None
# slot ∈ {"morning", "night"}
DiaryCallback = Callable[[str, str], Awaitable[None]]


class SoulScheduler:
    """
    每天定時觸發 morning / night 兩次 callback 的 asyncio 排程器。

    用法:
        scheduler = SoulScheduler()
        scheduler.register("agent_mahiru", my_diary_callback)
        await scheduler.start()
        # ... server 跑著的時候每天 08:00 / 22:00 自動觸發
        await scheduler.stop()  # shutdown 時
    """

    def __init__(
        self,
        morning_time: time = DEFAULT_MORNING_TIME,
        night_time: time = DEFAULT_NIGHT_TIME,
        trigger_probability: float = TRIGGER_PROBABILITY_DEFAULT,
        dream_minutes_after_night: int = 5,
        event_min_interval_minutes: int = 240,
        event_max_interval_minutes: int = 480,
        # Lesson 39 (2026-07-30 Bry 拍板): heartbeat + proactive DM 設定
        # 修法 12 (Bry 拍板 2026-08-06 17:12): heartbeat 預設值保留 (Bry 可能未來想恢復)
        # 但 Bry 8/6 17:12 派工: heartbeat 對 Ruka 也拿掉, 只留 proactive_dm
        # 動機: Bry 派工「對話負擔不按訊息類型分」, heartbeat + proactive_dm 疊加對 Bry 同樣是對話量
        # heartbeat 機制 (register_heartbeat / _fire_heartbeat) 保留在 scheduler 內部,
        # 給未來 Bry 想恢復時不用重寫, run_server.py 不再 register_heartbeat 就完全關閉
        heartbeat_min_interval_minutes: int = 30,
        heartbeat_max_interval_minutes: int = 60,
        # 修法 12 (Bry 拍板 2026-08-06 17:12): proactive_dm 預設 2-4h → 3-5h
        # 動機: Bry 派工「5-8 條/天」期望區間
        #       24h/4h(平均) = 6 條/天, 落在 5-8 條範圍內
        #       Bry 派工「整段對話會很長」, 5-8 條已經是熱情度上限
        proactive_dm_min_interval_minutes: int = 180,  # 3 小時 (原 120)
        proactive_dm_max_interval_minutes: int = 300,  # 5 小時 (原 240)
        proactive_dm_cooldown_seconds: int = 7200,  # 2 小時冷卻
        quiet_hours_start: int = 23,                # 23:00 開始靜音
        quiet_hours_end: int = 8,                   # 08:00 結束靜音
        # M1.1 (2026-07-31 23:30 Perplexity 派工): Event Bus 注入
        # 沒注入就 skip 發布 (向後相容, 測試不依賴 bus)
        bus: Optional["SoulEventBus"] = None,
        # 修法 11 (Bry 拍板 2026-08-06 16:xx): proactive whitelist
        # 只允許白名單內的 agent 觸發 proactive_dm / heartbeat, 其他角色改回純被動
        # None = 不過濾 (向後相容, 預設行為); list = 白名單 (例: ["agent_ruka"])
        # 範圍: 只影響 _fire_heartbeat / _fire_proactive_dm 的隨機抽樣池
        #       diary / dream / event 仍對 _all_agents 全部觸發, 不受影響
        # 動機: 8/5 21:08 Bry 被連環訊息轟炸, DISABLE_PROACTIVE 緊急關閉後的下一刀
        proactive_agents: Optional[List[str]] = None,
    ):
        self.morning_time = morning_time
        self.night_time = night_time
        self.trigger_probability = trigger_probability
        # 4.2+缺口 1: 夢境/事件觸發 (Bry 拍板 2026-07-20 19:03)
        self.dream_minutes_after_night = dream_minutes_after_night
        self.event_min_interval_minutes = event_min_interval_minutes
        self.event_max_interval_minutes = event_max_interval_minutes
        # Lesson 39: heartbeat + proactive DM 設定
        self.heartbeat_min_interval_minutes = heartbeat_min_interval_minutes
        self.heartbeat_max_interval_minutes = heartbeat_max_interval_minutes
        self.proactive_dm_min_interval_minutes = proactive_dm_min_interval_minutes
        self.proactive_dm_max_interval_minutes = proactive_dm_max_interval_minutes
        self.proactive_dm_cooldown_seconds = proactive_dm_cooldown_seconds
        self.quiet_hours_start = quiet_hours_start
        self.quiet_hours_end = quiet_hours_end
        # M1.1: Event Bus 注入 (None = skip 發布, 向後相容)
        self._bus = bus
        # 修法 11: proactive whitelist 儲存 (None = 不過濾)
        # 實際可觸發清單在 _get_proactive_agents() lazy 算
        # (跟 _all_agents 動態 register() 對齊, 避免初始化時 whitelist 還沒對應到 agent)
        self._proactive_agents_whitelist: Optional[List[str]] = proactive_agents

        # agent_id -> {slot -> callback}
        self._callbacks: Dict[str, Dict[str, DiaryCallback]] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # 記錄上次觸發日期 (避免同日重複)
        self._last_trigger_date: Dict[str, str] = {}  # {f"{agent_id}:{slot}": "YYYY-MM-DD"}
        # 4.2+缺口 1: dream/event 觸發記錄
        self._last_dream_date: Optional[str] = None  # 夢境每天觸發一次
        self._next_event_time: Optional[datetime] = None  # 下次事件時間
        self._dream_callback: Optional[DiaryCallback] = None  # 夢境 callback (共用)
        self._event_callback: Optional[DiaryCallback] = None  # 事件 callback (共用)
        self._all_agents: List[str] = []  # 4.2+缺口 1 用的 agent list (夢境抽 target 用)
        # Lesson 39: heartbeat + proactive DM 狀態
        self._heartbeat_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._proactive_dm_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._next_heartbeat_time: Optional[datetime] = None
        self._next_proactive_dm_time: Optional[datetime] = None
        self._last_proactive_dm_time: Optional[datetime] = None

    # ───────────────────────────────────────────────────────────
    # M1.1: Event Bus 發布層
    # 觸發後、callback 跑之前發布 AGENT_INTENT, 讓 MemoryMiddleware 跟
    # SpeakerTokenManager 跟其他訂閱者能像處理其他事件一樣接手。
    # 失敗 log warning 不 raise (「拒絕問, 強制讀」原則)。
    # ───────────────────────────────────────────────────────────

    async def _publish_agency_trigger(
        self,
        agent_id: str,
        trigger_type: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        M5.2-G: Publish AGENCY_TRIGGER 到 bus。
        AgencyTriggerHandler 訂閱此 type, 跑 4 stages 決定是否 invoke LLM。

        跟 _publish_agent_intent 對比:
          - _publish_agent_intent  → AGENT_INTENT      (legacy, AGENCY_BYPASS)
          - _publish_agency_trigger → AGENCY_TRIGGER   (M5.2-G, 過 Agency decision)

        兩者語意分離 (M5.2-F 凍結):
          AGENT_INTENT   = "Agent 想發言的意圖" (搶奪發言權)
          AGENCY_TRIGGER = "Scheduler 提議現在 act" (M5.2-G 過 Agency)

        M5.2-H Phase 2 (Bry 拍板 2026-08-08): 加 optional `extra` 參數
        供 trigger_type 特定 context 傳遞 (例: dream 的 target_agent_id / all_agents)。
        TriggerEnvelope dataclass 仍 frozen (M5.2-F),extra 走 dict payload。
        """
        if self._bus is None:
            return  # 沒注入 bus 就 skip, 向後相容
        if not _EVENTBUS_AVAILABLE:
            return  # eventbus 模組沒裝, skip
        try:
            # 計算 elapsed_mins
            elapsed_mins = 0.0
            if self._last_proactive_dm_time is not None:
                elapsed_mins = (now_local() - self._last_proactive_dm_time).total_seconds() / 60.0
            trigger_event = SoulEvent(
                event_type=EventType.AGENCY_TRIGGER,
                source="soul_scheduler",
                target=agent_id,
                priority=EventPriority.NORMAL,
                payload={
                    "trigger_type": trigger_type,
                    "agent_id": agent_id,
                    "reason": f"scheduler.{trigger_type}",
                    "elapsed_mins": elapsed_mins,
                    "timestamp": now_local().isoformat(),
                    "extra": dict(extra) if extra else {},
                },
            )
            await self._bus.publish(trigger_event)
        except Exception as e:
            # 「拒絕問, 強制讀」: 發布失敗不影響 scheduler 排程
            logger.warning(
                f"[Scheduler] AGENCY_TRIGGER 發布失敗 (不影響觸發): "
                f"agent={agent_id} trigger_type={trigger_type} err={type(e).__name__}: {e}"
            )

    async def _publish_agent_intent(
        self,
        agent_id: str,
        reason: str,
        draft: str = "",
        elapsed_mins: float = 0.0,
    ) -> None:
        """
        @deprecated: M5.2-G (2026-08-08) 起, _fire_proactive_dm 改用 _publish_agency_trigger。
        其他 4 個 _fire_* (event / heartbeat / dream / morning / night) 仍用此方法 (legacy / migration candidate)。
        完整 AGENCY_BYPASS 標記見 M5.2-F。
        """
        if self._bus is None:
            return  # 沒注入 bus 就 skip, 向後相容
        if not _EVENTBUS_AVAILABLE:
            return  # eventbus 模組沒裝, skip
        try:
            intent = SoulEvent(
                event_type=EventType.AGENT_INTENT,
                source="soul_scheduler",
                target=agent_id,
                priority=EventPriority.NORMAL,
                payload={
                    "agent_id": agent_id,
                    "reason": reason,
                    "draft": draft,
                    "elapsed_mins": elapsed_mins,
                    "trigger_source": "scheduler",
                },
            )
            await self._bus.publish(intent)
        except Exception as e:
            # 「拒絕問, 強制讀」: 發布失敗不影響 scheduler 排程
            logger.warning(
                f"[Scheduler] AGENT_INTENT 發布失敗 (不影響觸發): "
                f"agent={agent_id} reason={reason} err={e}"
            )

    def register(self, agent_id: str, callback: Optional[DiaryCallback] = None) -> None:
        """
        註冊一個 agent 的 morning + night callback (同一個 callback 處理兩種 slot).

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): callback 改成 Optional.
        向後相容: 不傳 callback 也可註冊 (scheduler iteration source 仍記錄 agent_id).
        Scheduler 真實 trigger 透過 AGENCY_TRIGGER 觸發, callback 保留作為 backward compat.
        """
        self._callbacks.setdefault(agent_id, {})["morning"] = callback
        self._callbacks.setdefault(agent_id, {})["night"] = callback
        if agent_id not in self._all_agents:
            self._all_agents.append(agent_id)
        logger.info(f"[Scheduler] 註冊 {agent_id} (morning + night)")

    def register_dream_event(
        self,
        dream_callback: Optional[DiaryCallback] = None,
        event_callback: Optional[DiaryCallback] = None,
    ) -> None:
        """
        註冊 dream + event callback (4.2+缺口 1 用).
        Bry 拍板 2026-07-20 19:03: 夢境每晚 22:05, 事件隨機 4-8 小時.

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): callback 改成 Optional (default None).
        向後相容: 不傳 callback 也可註冊.
        """
        self._dream_callback = dream_callback
        self._event_callback = event_callback
        # 第一次事件時間: 隨機 4-8 小時後
        import random
        from datetime import timedelta
        mins = random.randint(
            self.event_min_interval_minutes,
            self.event_max_interval_minutes,
        )
        self._next_event_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 註冊 dream + event ✓ "
            f"next_event={self._next_event_time.strftime('%H:%M')} "
            f"dream_at_night+{self.dream_minutes_after_night}min"
        )

    def register_heartbeat(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """
        Lesson 39 (2026-07-30 Bry 拍板): 註冊 heartbeat callback.
        輕量背景存在感, 30-60 分鐘隨機觸發 1-2 隻角色的 check-in 訊息.
        Callback 內部用 LLM_CONCURRENCY_LIMIT 限流避免跟 diary/dream 疊加.

        修法 12 (Bry 拍板 2026-08-06 17:12): heartbeat 機制保留在 scheduler 內部
        (給未來 Bry 想恢復時不用重寫), 但 run_server.py 不再 register_heartbeat.
        動機: Bry 派工「對話負擔不按訊息類型分」, heartbeat + proactive_dm 疊加對 Bry 同樣是對話量.
        Bry 派工「5-8 條/天」期望區間, heartbeat 32 條/天遠超 Bry 上限.
        恢復方式: Bry 拍板後, run_server.py 加回 `scheduler.register_heartbeat(_heartbeat_callback)` 即可.
        """
        self._heartbeat_callback = callback
        mins = random.randint(
            self.heartbeat_min_interval_minutes,
            self.heartbeat_max_interval_minutes,
        )
        self._next_heartbeat_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 註冊 heartbeat ✓ "
            f"next={self._next_heartbeat_time.strftime('%H:%M:%S')} "
            f"interval={mins}min"
        )

    def register_proactive_dm(
        self, callback: Optional[Callable[[str], Awaitable[None]]] = None
    ) -> None:
        """
        Lesson 39: 註冊 proactive DM callback.
        角色主動透過 TG DM 找 Bryan, 隨機 2-4 小時觸發一次.

        三道防護 (Bry 拍板, 避免通知疲勞):
          1. 冷卻窗: 同一使用者 cooldown_seconds 內不再觸發
          2. 靜音時段: quiet_hours_start ~ quiet_hours_end 跳過
             (除非角色本身有夜間人設, 未來可加 per-character override)
          3. semaphore: callback 內用 LLM_CONCURRENCY_LIMIT 共用限流

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): callback 改成 Optional (default None).
        向後相容: 不傳 callback 也可註冊.
        """
        self._proactive_dm_callback = callback
        mins = random.randint(
            self.proactive_dm_min_interval_minutes,
            self.proactive_dm_max_interval_minutes,
        )
        self._next_proactive_dm_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 註冊 proactive_dm ✓ "
            f"next={self._next_proactive_dm_time.strftime('%H:%M:%S')} "
            f"interval={mins}min cooldown={self.proactive_dm_cooldown_seconds}s "
            f"quiet={self.quiet_hours_start}:00-{self.quiet_hours_end}:00"
        )

    def registered_agents(self) -> List[str]:
        return list(self._callbacks.keys())

    async def start(self) -> None:
        """啟動排程器背景 task."""
        if self._task is not None:
            logger.warning("[Scheduler] 已經在跑, 不重複 start")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="SoulScheduler")
        logger.info(
            f"[Scheduler] 啟動 ✓ morning={self.morning_time} "
            f"night={self.night_time} prob={self.trigger_probability} "
            f"agents={len(self._callbacks)}"
        )

    async def stop(self) -> None:
        """停止排程器."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("[Scheduler] 停止")

    # ───────────────────────────────────────────────────────────
    # 內部: 主迴圈 + 觸發判定
    # ───────────────────────────────────────────────────────────

    def _seconds_until_next_slot(self, now: datetime) -> timedelta:
        """算到下一個 morning/night slot 的秒數."""
        candidates = []
        for slot, t in [("morning", self.morning_time), ("night", self.night_time)]:
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            candidates.append((slot, target))
        # 找最近的
        candidates.sort(key=lambda x: x[1])
        return candidates[0][1] - now

    def _slot_for_time(self, now: datetime) -> Optional[str]:
        """給定當前時間, 回傳應該觸發哪個 slot (如果剛好到點), 否則 None."""
        today = now.date().isoformat()
        for slot, t in [("morning", self.morning_time), ("night", self.night_time)]:
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            diff = (now - target).total_seconds()
            # 在 target 時間 ± 60 秒內都算「到點」, 避免 sleep 漂移漏觸發
            if 0 <= diff < 60:
                return slot
        return None

    def _is_dream_time(self, now: datetime) -> bool:
        """
        4.2+缺口 1: 判斷是否該觸發夢境 (night slot 後 N 分鐘, ±60s 窗口).
        Bry 拍板 2026-07-20 19:03: dream 100% 每天觸發, 不做觀察期.

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 _dream_callback is None gate.
        Scheduler 不再依賴 callback 存在性, 只看時間窗口.
        真實 trigger 透過 AGENCY_TRIGGER publish, callback 只是 backward compat layer.
        """
        target = now.replace(
            hour=self.night_time.hour,
            minute=self.night_time.minute + self.dream_minutes_after_night,
            second=0,
            microsecond=0,
        )
        if target.minute >= 60:
            # 22:55 之類的跨小時情況
            target = target.replace(minute=target.minute - 60, hour=target.hour + 1)
        diff = (now - target).total_seconds()
        return 0 <= diff < 60

    def _is_event_time(self, now: datetime) -> bool:
        """
        4.2+缺口 1: 判斷是否該觸發事件 (隨機間隔, 過了 _next_event_time 就觸發).

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 _event_callback is None gate.
        只看 _next_event_time, 不看 callback 存在性.
        """
        if self._next_event_time is None:
            return False
        return now >= self._next_event_time

    async def _fire_dream(self, today: str) -> None:
        """
        4.2+缺口 1 + 4.3: 觸發夢境. 3-5 隻角色, 夢到 relationships 裡的其他角色.

        Mavis 拍板 2026-07-21 16:35: 1-3 → 3-5 覆蓋率↑

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 callback execution dependency.
        - 移除 `or self._dream_callback is None` early-return gate
        - 移除 `await self._dream_callback(...)` invocation
        真實 trigger 透過 AGENCY_TRIGGER publish 給 DreamHandler 處理.
        """
        if self._last_dream_date == today:
            return  # 一天一次
        if not self._all_agents:
            return
        from src.soul.dream_event import _pick_dream_agents, _pick_dream_target
        from pathlib import Path as _P
        data_dir = _P("data/soul")

        # Stage 4.3: 抽 N 隻角色 (3-5, 上限依 agents 數, hardcode 避免循環 import)
        n = min(5, max(3, len(self._all_agents) // 2))  # 10 隻 → 5, 6 隻 → 5, 4 隻 → 5 但會被 sample cap
        n = min(n, len(self._all_agents))  # 防 n > agents 數
        dreamers = _pick_dream_agents(self._all_agents, n)
        logger.info(f"[Scheduler] 🌙 夢境觸發: {len(dreamers)} 隻角色 ({dreamers})")

        for dreamer in dreamers:
            target = _pick_dream_target(dreamer, self._all_agents, data_dir)
            if target is None:
                continue
            # M5.2-H Phase 2 (Bry 拍板 2026-08-08): publish AGENCY_TRIGGER
            # M5.2-I Phase 6: 移除 callback invocation. 真實 writer.write_dream + relationship
            # side effect 由 DreamHandler 訂閱 AGENCY_TRIGGER 觸發.
            # Target 透過 extra 傳遞 (per C1: TriggerEnvelope frozen, extra 走 dict payload)
            await self._publish_agency_trigger(
                agent_id=dreamer,
                trigger_type="dream",
                extra={
                    "target_agent_id": target,
                    "all_agents": list(self._all_agents),  # snapshot, 避免 handler 看到後續 mutation
                },
            )

        self._last_dream_date = today

    async def _fire_event(self) -> None:
        """
        4.2+缺口 1 + 4.3: 觸發事件. 2 隻角色/次, 場景模板.

        Mavis 拍板 2026-07-21 16:35: 1 → 2

        M1.7 (Bry 拍板 2026-08-07 15:00): event 也過 proactive whitelist
        動機: 8/7 12:30 anna + ram 主動發訊息證實 event 觸發會繞過修法 11 whitelist
              (修法 11 narrow 派工時漏了 event, 只過濾了 proactive_dm / heartbeat)
        修法: event 從 _all_agents 抽, 然後 filter 過 candidates (跟 _fire_heartbeat 修法 11 pattern 一致)
        不用「從 candidates 抽」是為了跟修法 11 的 _fire_heartbeat 邏輯對齊,
        測試可以 mock sample return_value 強制回全名單, filter 自動過濾

        共用而不是新增獨立 event whitelist 參數: Bry 派工「目前系統裡實際會主動發訊息給 Bry 的路徑
              只有 proactive_dm + event 兩個, 共用同一個 whitelist 就足夠」

        Bry 派工精神:
        - 「A 方案就足夠, 不用做到 B」 (B 是在所有 agent_intent 出口套 whitelist, 改動更大)
        - 「不為假設中的未來灑過濾網」 (未來若長出第三條路徑再說, 不預先過度設計)
        - 「更貼合修法 11 當初 narrow 派工的精神」 (跟 proactive_dm / heartbeat 一樣 pattern, 不擴大)
        - 向後相容: whitelist=None → _get_proactive_agents() 回 _all_agents, agents 全部保留, 行為不變

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 callback execution dependency.
        - 移除 `or self._event_callback is None` early-return gate
        - 移除 `await self._event_callback(...)` invocation
        真實 trigger 透過 AGENCY_TRIGGER publish 給 EventHandler 處理.
        """
        if not self._all_agents:
            return
        # M1.7: 從 _all_agents 抽, 然後 filter 過 whitelist (跟 _fire_heartbeat 修法 11 一致)
        # 用 module-level random (跟 _fire_heartbeat 一致) 而非 local import, 方便測試 mock
        n = min(2, len(self._all_agents))
        raw_picks = random.sample(self._all_agents, n)
        # 過濾掉非 candidates 的 (跟 _fire_heartbeat 修法 11 的 `picks = [a for a in raw_picks if a in candidates]` 一致)
        candidates = self._get_proactive_agents()
        agents = [a for a in raw_picks if a in candidates]
        if not agents:
            # 全部被 filter 掉 (例如 whitelist 配錯或太嚴), silent skip
            logger.debug(
                f"[Scheduler] ✨ event 抽到的 {len(raw_picks)} 隻全部不在 whitelist, 跳過 "
                f"(raw={raw_picks}, whitelist={self._proactive_agents_whitelist})"
            )
            # 排下次 (跟原本一樣的排程邏輯, 避免下次又被卡住)
            mins = random.randint(
                self.event_min_interval_minutes,
                self.event_max_interval_minutes,
            )
            self._next_event_time = now_local() + timedelta(minutes=mins)
            logger.info(
                f"[Scheduler] ✨ 下次事件: {self._next_event_time.strftime('%Y-%m-%d %H:%M')}"
            )
            return
        logger.info(
            f"[Scheduler] ✨ 事件觸發: {len(agents)} 隻角色 ({agents}) "
            f"(whitelist={self._proactive_agents_whitelist}, raw={raw_picks})"
        )
        for agent_id in agents:
            # M5.2-H Phase 1 (Bry 拍板 2026-08-08): publish AGENCY_TRIGGER
            # M5.2-I Phase 6: 移除 callback invocation. 真實 writer.write_event
            # 由 EventHandler 訂閱 AGENCY_TRIGGER 觸發.
            await self._publish_agency_trigger(agent_id, trigger_type="event")

        # 排下次事件 (4-8 小時後)
        mins = random.randint(
            self.event_min_interval_minutes,
            self.event_max_interval_minutes,
        )
        self._next_event_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] ✨ 下次事件: {self._next_event_time.strftime('%Y-%m-%d %H:%M')}"
        )

    # ───────────────────────────────────────────────────────────
    # Lesson 39: Heartbeat + Proactive DM (Bry 拍板 2026-07-30)
    # ───────────────────────────────────────────────────────────

    def _is_heartbeat_time(self, now: datetime) -> bool:
        if self._heartbeat_callback is None or self._next_heartbeat_time is None:
            return False
        return now >= self._next_heartbeat_time

    def _is_proactive_dm_time(self, now: datetime) -> bool:
        """
        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 _proactive_dm_callback is None gate.
        只看 _next_proactive_dm_time, 不看 callback 存在性.
        """
        if self._next_proactive_dm_time is None:
            return False
        return now >= self._next_proactive_dm_time

    def _get_proactive_agents(self) -> List[str]:
        """
        修法 11 (Bry 拍板 2026-08-06 16:xx): 計算實際可觸發 proactive 的 agent 列表.

        規則:
          - 白名單 None → 回傳全部 _all_agents (向後相容, 預設行為)
          - 白名單 list → 回傳白名單 ∩ _all_agents (允許動態 register, 沒交集就空)
          - 空集合 + 有 _all_agents → log warning (避免靜默失效)

        Lazy 計算 (不在 __init__ 預先算) 的原因:
          _all_agents 是逐步 register() 加進來的, __init__ 時通常還是空的
          每次 _fire_* 觸發前重算, 才能反映最新註冊狀態
        """
        if self._proactive_agents_whitelist is None:
            return list(self._all_agents)
        eligible = [a for a in self._proactive_agents_whitelist if a in self._all_agents]
        if not eligible and self._all_agents:
            # 配錯了: 白名單跟已註冊 agents 沒交集 (例如 whitelist 列了 ruka 但 ruka 沒註冊)
            logger.warning(
                f"[Scheduler] ⚠️ proactive whitelist {self._proactive_agents_whitelist} "
                f"跟已註冊 agents {self._all_agents} 沒交集, "
                f"沒有 agent 可觸發 proactive (心跳 + 主動傳訊都會 skip)"
            )
        return eligible

    def _is_quiet_hours(self, now: datetime) -> bool:
        """Lesson 39: 23:00-08:00 靜音時段檢查 (含跨午夜的 wrap-around)."""
        h = now.hour
        if self.quiet_hours_start > self.quiet_hours_end:
            # 跨午夜的時段 (例如 23-8)
            return h >= self.quiet_hours_start or h < self.quiet_hours_end
        else:
            return self.quiet_hours_start <= h < self.quiet_hours_end

    async def _fire_heartbeat(self) -> None:
        """
        Lesson 39: 觸發 1-2 隻角色的 heartbeat.
        輕量 check-in 訊息, callback 內部應該用 LLM_CONCURRENCY_LIMIT.

        修法 11 (Bry 拍板 2026-08-06 16:xx): 從 proactive whitelist 抽
        whitelist 決定「誰有資格觸發」, 隨機抽樣只在 whitelist 內做
        """
        candidates = self._get_proactive_agents()
        if not candidates or self._heartbeat_callback is None:
            return
        n = min(random.randint(1, 2), len(candidates))
        raw_picks = random.sample(candidates, n)
        # 雙重保險: 過濾掉不在 candidates 的 (正常 random.sample 已是子集, 但 mock 測試場景要防呆)
        picks = [a for a in raw_picks if a in candidates]
        if not picks:
            return
        logger.info(
            f"[Scheduler] 💓 heartbeat 觸發: {picks} "
            f"(whitelist={self._proactive_agents_whitelist})"
        )
        for agent_id in picks:
            # M1.1: 觸發後、callback 之前發布 AGENT_INTENT
            # draft / elapsed_mins 從 callback 內部 _build_intent_payload 拿
            await self._publish_agent_intent(agent_id, reason="heartbeat")
            try:
                await self._heartbeat_callback(agent_id)
            except Exception as e:
                # 「拒絕問, 強制讀」: 失敗不中斷排程器
                logger.exception(f"[Scheduler] heartbeat {agent_id} 失敗: {e}")
        # 排下次 (隨機 30-60 分鐘)
        mins = random.randint(
            self.heartbeat_min_interval_minutes,
            self.heartbeat_max_interval_minutes,
        )
        self._next_heartbeat_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 💓 下次 heartbeat: {self._next_heartbeat_time.strftime('%H:%M:%S')}"
        )

    async def _fire_proactive_dm(self) -> None:
        """
        Lesson 39: 觸發 1 隻角色的 proactive DM (透過 TG DM 找 Bryan).

        三道防護 (依序檢查, 任一不通過就跳過 + 排下次):
          1. 冷卻窗: 上次 DM 到現在 < cooldown_seconds → 跳過
          2. 靜音時段: 23:00-08:00 → 跳過 (會自動排到 8:00 之後)
          3. semaphore: callback 內部用 LLM_CONCURRENCY_LIMIT (在 run_server.py)

        修法 11 (Bry 拍板 2026-08-06 16:xx): 加第 0 道防護 — proactive whitelist
        whitelist 決定「誰有資格觸發」, whitelist 外的角色 (即使 random 命中) 也 silent skip

        M5.2-G (Bry 拍板 2026-08-08): 改成 publish AGENCY_TRIGGER 而非直接 call callback。
        原本的 _proactive_dm_callback 欄位保留 (向後相容, 不再被呼叫)。
        AgencyTriggerHandler 訂閱 AGENCY_TRIGGER → 跑 4 stages → if YES, invoke LLM。

        仍然負責: 觸發時機 / quiet hours / scheduler cooldown / whitelist / target selection
        不再負責: 觸發後是否 act (Agency decision) / LLM call (Agency execution)
        """
        candidates = self._get_proactive_agents()
        # 修 M5.2-G: 不再依賴 _proactive_dm_callback, 只要有 candidates 就走
        if not candidates:
            return

        # 1. 冷卻窗檢查
        if self._last_proactive_dm_time is not None:
            elapsed = (now_local() - self._last_proactive_dm_time).total_seconds()
            if elapsed < self.proactive_dm_cooldown_seconds:
                remaining = int(self.proactive_dm_cooldown_seconds - elapsed)
                logger.debug(
                    f"[Scheduler] 💬 proactive_dm 冷卻中 (剩 {remaining}s), 跳過"
                )
                # 排下次但不要立刻再試
                mins = random.randint(
                    self.proactive_dm_min_interval_minutes,
                    self.proactive_dm_max_interval_minutes,
                )
                self._next_proactive_dm_time = now_local() + timedelta(minutes=mins)
                return

        # 2. 靜音時段檢查
        now = now_local()
        if self._is_quiet_hours(now):
            logger.debug(
                f"[Scheduler] 💬 proactive_dm 靜音時段 ({now.hour}:xx), 跳過"
            )
            # 30 分鐘後再試 (會自然落到 8:00 之後)
            self._next_proactive_dm_time = now + timedelta(minutes=30)
            return

        # 3. 觸發 (whitelist 過濾後)
        raw_choice = random.choice(candidates)
        # 雙重保險: 過濾掉不在 candidates 的 (防呆, mock 測試場景)
        if raw_choice not in candidates:
            logger.debug(
                f"[Scheduler] 💬 proactive_dm random.choice {raw_choice} "
                f"不在 candidates {candidates}, 跳過"
            )
            # 排下次但不要立刻再試
            mins = random.randint(
                self.proactive_dm_min_interval_minutes,
                self.proactive_dm_max_interval_minutes,
            )
            self._next_proactive_dm_time = now_local() + timedelta(minutes=mins)
            return
        agent_id = raw_choice
        logger.info(
            f"[Scheduler] 💬 proactive_dm 觸發: {agent_id} "
            f"(whitelist={self._proactive_agents_whitelist})"
        )
        # M5.2-G (Bry 拍板 2026-08-08): publish AGENCY_TRIGGER
        # M5.2-I Phase 6: 移除 callback invocation. 真實 LLM
        # 由 AgencyTriggerHandler 訂閱 AGENCY_TRIGGER 觸發.
        await self._publish_agency_trigger(agent_id, trigger_type="proactive_dm")
        # 記錄 last_proactive_dm_time (scheduler-level rate limit 不變)
        self._last_proactive_dm_time = now_local()
        # 排下次 (隨機 2-4 小時)
        mins = random.randint(
            self.proactive_dm_min_interval_minutes,
            self.proactive_dm_max_interval_minutes,
        )
        self._next_proactive_dm_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 💬 下次 proactive_dm: {self._next_proactive_dm_time.strftime('%H:%M:%S')}"
        )

    async def _run_loop(self) -> None:
        """主迴圈: 每秒醒一次, 檢查是否到點."""
        logger.info("[Scheduler] 進入主迴圈")
        last_health_log = 0.0
        while self._running:
            try:
                now = now_local()
                # 1. morning / night slot
                slot = self._slot_for_time(now)
                if slot:
                    await self._fire_all(slot, today=now.date().isoformat())
                # 2. 4.2+缺口 1: 夢境 (night + N 分鐘)
                if self._is_dream_time(now):
                    await self._fire_dream(today=now.date().isoformat())
                # 3. 4.2+缺口 1: 事件 (隨機 4-8 小時)
                if self._is_event_time(now):
                    await self._fire_event()
                # 4. Lesson 39: heartbeat (30-60 分鐘, 1-2 隻角色 check-in)
                if self._is_heartbeat_time(now):
                    await self._fire_heartbeat()
                # 5. Lesson 39: proactive DM (2-4 小時, 透過 TG DM 找 Bryan, 帶冷卻+靜音防護)
                if self._is_proactive_dm_time(now):
                    await self._fire_proactive_dm()
                # 健康檢查 log
                if (now.timestamp() - last_health_log) > HEALTH_CHECK_INTERVAL_SECS:
                    next_slot, next_time = self._compute_next_slot(now)
                    logger.debug(
                        f"[Scheduler] 健康檢查 | next={next_slot} at {next_time.isoformat()}"
                    )
                    last_health_log = now.timestamp()
                # 睡 30 秒再醒 (夠細, 60 秒觸發窗口不會漏)
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                logger.info("[Scheduler] 主迴圈被取消")
                break
            except Exception as e:
                logger.exception(f"[Scheduler] 主迴圈錯誤 (繼續跑): {e}")
                await asyncio.sleep(30)

    def _compute_next_slot(self, now: datetime) -> tuple[str, datetime]:
        candidates = []
        for slot, t in [("morning", self.morning_time), ("night", self.night_time)]:
            target = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            candidates.append((slot, target))
        candidates.sort(key=lambda x: x[1])
        return candidates[0]

    async def _fire_all(self, slot: str, today: str) -> None:
        """觸發所有 canonical agent 對應 slot 的 AGENCY_TRIGGER.

        M5.2-H Phase 3 (Bry 拍板 2026-08-08): 改成 publish AGENCY_TRIGGER (取代原本的 _publish_agent_intent)
        trigger_type 直接用 slot ("morning" / "night"), 跟 M5.2-F frozen contract 一致
        (不新增 AGENCY_TRIGGER_MORNING / AGENCY_TRIGGER_NIGHT)

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): 移除 callback invocation dependency.

        M5.2-I Phase 8 (Bry 拍板 2026-08-08): 移除 callback iteration-source dependency.
        - 改用 `self._all_agents` 作為 iteration source (canonical agent list)
        - 移除 `self._callbacks` iteration (per work order)
        - 移除 `cb = cbs.get(slot); if cb is None: continue` (callback 完全不參與)
        - 移除 `if not self._callbacks: return` early return

        結果:
          - scheduler._callbacks == {} 不再阻止 AGENCY_TRIGGER publish
          - 只要 _all_agents 還有 agent, _fire_all 就會 publish AGENCY_TRIGGER
          - callback registration 完全 optional (per I-6)
          - 真實 diary execution 由 DiaryHandler 訂閱 AGENCY_TRIGGER 觸發
          - 1 trigger → 1 handler writer call (handler.executor 仍可 lookup 真實 callback)

        保留既有:
          - dedup (_last_trigger_date per agent:slot per day)
          - morning / night slot semantics
          - agent iteration semantics (所有 _all_agents 內 agent 都觸發)
          - _publish_agency_trigger() trigger payload
        """
        if not self._all_agents:
            return
        for agent_id in self._all_agents:
            key = f"{agent_id}:{slot}"
            if self._last_trigger_date.get(key) == today:
                continue  # 同日不重觸
            # M5.2-I Phase 8: 完全脫離 callback dependency.
            # _fire_all 不再查 _callbacks, 不再 invoke callback.
            # 唯一路徑: 對每個 canonical agent publish AGENCY_TRIGGER.
            await self._publish_agency_trigger(agent_id, trigger_type=slot)
            # 標記今日已觸發
            self._last_trigger_date[key] = today
            logger.info(f"[Scheduler] ✓ 觸發 {agent_id} {slot} (AGENCY_TRIGGER published)")


# ───────────────────────────────────────────────────────────
# 全域 singleton (跟 4.1 relationships 同樣 pattern)
# ───────────────────────────────────────────────────────────

_scheduler: Optional[SoulScheduler] = None


def get_scheduler(**kwargs) -> SoulScheduler:
    """取得全域 scheduler, lazy 初始化 (跟 get_relationships_manager 同樣).

    接受 kwargs 傳給 SoulScheduler(),方便測試時覆寫預設值
    (例:get_scheduler(heartbeat_min_interval_minutes=1) 加速測試).
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = SoulScheduler(**kwargs)
    return _scheduler
