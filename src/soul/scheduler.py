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
- register() 註冊 agent 到 _all_agents (R-3 起, 不再收 callback 參數)
- Stage 4.3 LLM impression 留到下一刀, 這版先不做

Bry 19:35+ 拍板 (對 4.1 觀察期): 0.7% 機率觸發 = 不每天都觸發
- 4.2 第一刀先 100% 每天觸發, Bry 觀察 1 天後再決定要不要加 0.7% 機率

約束 (沿用 4.1 紀律):
- 「拒絕問, 強制讀」: 發布失敗 log warning, 不中斷排程器
- 「完成度標記要誠實」: 寫到哪就是哪
- 「拍板先設計再開工」: 觀察期 1 天後 Bry 拍板要不要加 4.2 缺口 (排程器夢境/事件觸發)
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, time, timedelta

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

# M7-longing (Bry 拍板 2026-08-18): 想念驅動主動傳訊 (取代定時器)。
# 想念 = 依戀(intimacy) × 有效沉默時長 (compute_longing, 見 src/agent/emotion.py)。
# - LONGING_THRESHOLD: 想念跨過此門檻才觸發。0.3 ≈ Ruka(60) 沉默 12h /
#   Yua(80) 沉默 9h / Ram(40) 沉默 18h → 角色差異化、時間不可預測, 不是鬧鐘。
# - LONGING_CHECK_INTERVAL_MINUTES: 想念未達門檻時, 多久後再查一次。
LONGING_THRESHOLD = 0.3
LONGING_CHECK_INTERVAL_MINUTES = 30


# ───────────────────────────────────────────────────────────
# 排程器本體
# ───────────────────────────────────────────────────────────

# Callback 簽名: async def cb(agent_id: str, slot: str) -> None
# slot ∈ {"morning", "night"}
DiaryCallback = Callable[[str, str], Awaitable[None]]


def _append_interaction(record: dict) -> None:
    """Cross-Agent (2026-08-22): append-only 記錄到 data_root()/soul/interactions.jsonl。

    Layer 2 (shared_event) / Layer 3 (cross_chat) 共用。
    失敗只 log warning, 不中斷觸發 (「拒絕問, 強制讀」)。
    """
    import json as _json
    from src.paths import data_root
    try:
        path = data_root() / "soul" / "interactions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(record, ensure_ascii=False) + "\n")
        logger.info(
            f"[Cross-Agent] interactions.jsonl 記錄: type={record.get('type')} "
            f"agents={record.get('agents')}"
        )
    except Exception as e:
        logger.warning(f"[Scheduler] interactions.jsonl 寫入失敗: {e}")


class SoulScheduler:
    """
    每天定時觸發 morning / night 兩次 callback 的 asyncio 排程器。

    用法:
        scheduler = SoulScheduler()
        scheduler.register("agent_mahiru")
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
        # Cross-Agent (2026-08-22): shared_event / cross_chat 排程
        # 每 6-12h 隨機一次, 全體共用冷卻 (各一個 timer, 跟 event 的 4-8h 分開)
        # Layer 2 (shared_event): 抽 2 隻一起做一件事, 兩隻寫 diary
        # Layer 3 (cross_chat): 抽 2 隻做 3 輪封閉對話, scheduler 明確驅動
        shared_activity_min_interval_minutes: int = 360,   # 6h
        shared_activity_max_interval_minutes: int = 720,   # 12h
        cross_chat_min_interval_minutes: int = 360,     # 6h
        cross_chat_max_interval_minutes: int = 720,     # 12h
        # TS-2.1 (2026-09-04): Actuator 依赖注入（observe/reflect 决策后真执行 + 回流）。
        # 只作用于 _decision_check 的内部动作分支; 默认 None 向后兼容——
        # 不注入时 observe/reflect 决策行为与现状完全等价（空转, 直接 mark_rejected）。
        actuator: Optional[Any] = None,
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
        # Cross-Agent (2026-08-22): shared_event / cross_chat 間隔設定
        self.shared_activity_min_interval_minutes = shared_activity_min_interval_minutes
        self.shared_activity_max_interval_minutes = shared_activity_max_interval_minutes
        self.cross_chat_min_interval_minutes = cross_chat_min_interval_minutes
        self.cross_chat_max_interval_minutes = cross_chat_max_interval_minutes

        # M5.2-P-3 (Bry 拍板 2026-08-08): _callbacks field 移除
        # (production 0 invocation 從 M5.2-I-8 後, _callbacks 純 DEAD storage)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        # 記錄上次觸發日期 (避免同日重複)
        self._last_trigger_date: Dict[str, str] = {}  # {f"{agent_id}:{slot}": "YYYY-MM-DD"}
        # 4.2+缺口 1: dream/event 觸發記錄
        self._last_dream_date: Optional[str] = None  # 夢境每天觸發一次
        self._next_event_time: Optional[datetime] = None  # 下次事件時間
        # M5.2-R-3 (Bry 拍板 2026-08-09): _dream_callback / _event_callback field 移除
        # (0 read / 0 invocation 從 M5.2-I-6 後, setter 仍是 dead code)
        self._all_agents: List[str] = []  # 4.2+缺口 1 用的 agent list (夢境抽 target 用)
        # Lesson 39: heartbeat + proactive DM 狀態
        self._heartbeat_callback: Optional[Callable[[str], Awaitable[None]]] = None
        # M5.2-O-3 (Bry 拍板 2026-08-08): _proactive_dm_callback field 移除
        # (production 0 invocation 從 M5.2-G/I-6 後)
        self._next_heartbeat_time: Optional[datetime] = None
        self._next_proactive_dm_time: Optional[datetime] = None
        self._last_proactive_dm_time: Optional[datetime] = None
        # M7-2 (Bry 拍板 2026-08-18): 活動驅動主動傳訊 — 記錄每 agent 已分享活動的 ts (去重)
        self._last_shared_activity_ts: Dict[str, str] = {}
        # Cross-Agent (2026-08-22): shared_event / cross_chat 計時器
        # (各一個全體共用 timer = 全體共用冷卻, 不會 10 隻同時開聊)
        self._next_shared_event_time: Optional[datetime] = None
        self._next_cross_chat_time: Optional[datetime] = None
        # TS-2.1: Actuator（observe/reflect 执行器）注入; None = 空转（向前兼容）
        self._actuator = actuator

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

        M5.8-4 (Bry 派工 2026-08-10): Inner Life → Agency Producer Gating
        - 對 proactive_dm trigger_type, 在 bus.publish 之前 query 該 agent 的
          Inner Life state。如果過去 GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES
          分鐘內有 InnerLifeEvent, 則 GATED, skip publish。
        - 其他 4 trigger_type (event / dream / morning / night) 不受影響 —
          它們本身是 inner-life activity, 不是 inner-life consumer。
        - Fail-open: gate query 失敗 → fall-through publish (preserve existing)。
        - 0 frozen contract 變動 (Agency 4 stages, TriggerEnvelope, 4 handlers)。
        """
        if self._bus is None:
            return  # 沒注入 bus 就 skip, 向後相容
        if not _EVENTBUS_AVAILABLE:
            return  # eventbus 模組沒裝, skip

        # M5.8-4: Inner Life producer-side gate (proactive_dm only)
        if trigger_type == "proactive_dm":
            should_publish = await self._inner_life_gate_check(agent_id)
            if not should_publish:
                # Gate already logged observability. Skip publish.
                return
            # SM-3 (2026-08-30): Motive + Decision producer-side check (proactive_dm only)
            # Fail-closed: 无 motive / Decision not_transmit / 异常 → skip publish。
            # 与 M5.8-4 gate 的 fail-open 对比: Decision 是 volition 层,
            # 坏掉的 Decision 绝不能自动放行 (auto-send 正是反自动化要消灭的)。
            should_publish = await self._decision_check(agent_id)
            if not should_publish:
                # Decision already logged observability. Skip publish.
                return

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

    async def _inner_life_gate_check(self, agent_id: str) -> bool:
        """
        M5.8-4 (Bry 派工 2026-08-10): Inner Life → Agency producer-side gate.

        Returns True if trigger should be published, False if gated.

        Fail-open: any failure → True (preserve existing Agency execution path).

        不得 fabricate identity / create InnerLifeEvent / read conversation content。
        不得引入 LLM / semantic / vector。
        不得發明 mood score / confidence score / weighting。

        Implementation note (M5.8-4):
          必須使用 UTC `now` (不是 now_local()) 因為 InnerLifeEvent.ts
          透過 `now_utc_iso()` 寫入 trace.jsonl (per inner_life/identity.py)。
          若用 now_local() (EDT/EST) 計算 elapsed, 跟 UTC ts 比較會
          出現 4-5 小時漂移, 導致 elapsed 永遠 >= 30 min 永遠 EMITTED,
          gate 失去作用. 這是 P2-3 風險 (TZ drift), 必須用 UTC.
        """
        try:
            from src.agency.inner_life_gate import (
                gate_proactive_dm,
                GateDecision,
            )
            from src.inner_life.trace_reader import NarrativeTraceReader
            from datetime import timezone
            gate_result = gate_proactive_dm(
                agent_id=agent_id,
                now=datetime.now(timezone.utc),
                trace_reader=NarrativeTraceReader(),
            )
            if gate_result.decision == GateDecision.GATED:
                # 觀察用 log (Bry 派工 §8 要求可區分狀態)
                logger.info(
                    f"[M5.8-4 Inner Life Gate] proactive_dm GATED: "
                    f"agent={agent_id} "
                    f"last_event_id={gate_result.last_event_id} "
                    f"elapsed={gate_result.elapsed_minutes:.1f}min "
                    f"reason={gate_result.reason}"
                )
                return False  # skip publish
            elif gate_result.decision == GateDecision.UNAVAILABLE:
                logger.debug(
                    f"[M5.8-4 Inner Life Gate] proactive_dm UNAVAILABLE (fail-open = emit): "
                    f"agent={agent_id} reason={gate_result.reason}"
                )
                return True
            elif gate_result.decision == GateDecision.FAILURE:
                logger.warning(
                    f"[M5.8-4 Inner Life Gate] proactive_dm FAILURE (fail-open = emit): "
                    f"agent={agent_id} reason={gate_result.reason}"
                )
                return True
            else:  # EMITTED
                logger.debug(
                    f"[M5.8-4 Inner Life Gate] proactive_dm EMITTED: "
                    f"agent={agent_id} "
                    f"last_event_id={gate_result.last_event_id} "
                    f"elapsed={gate_result.elapsed_minutes:.1f}min"
                )
                return True
        except Exception as gate_err:
            # 「拒絕問, 強制讀」: gate 失敗不影響 trigger 發布
            logger.warning(
                f"[M5.8-4 Inner Life Gate] gate exception (fail-open = emit): "
                f"agent={agent_id} err={type(gate_err).__name__}: {gate_err}"
            )
            return True  # preserve existing behavior

    async def _decision_check(self, agent_id: str) -> bool:
        """
        SM-3 (2026-08-30): Motive + Decision producer-side check.

        Fail-closed (DECISION-PROMPT-CONTRACT §4):
          - 无 pending motive → skip publish (F1 / 验收 A)
          - Decision LLM 失败 / 坏输出 → not_transmit → skip publish (F2-F4)
          - 只有明确 transmit 才继续 publish (验收 C)
          - 任何异常 → skip publish (fail-closed, 不 auto-send)

        与 M5.8-4 gate 的 fail-open 对比: Decision 是 volition 层,
        坏掉的 Decision 绝不能自动放行 (auto-send 正是反自动化要消灭的)。

        只作用于 proactive_dm (motive 的「传」= 主动传讯给 Bry)。
        其他 4 个 trigger_type (morning / night / dream / event) 是 inner-life
        activity, 不受 Decision 层影响 (与 M5.8-4 gate 同范围策略)。
        """
        try:
            from src.soul.motive import MotiveEngine
            engine = MotiveEngine()
            # 1. interpretation: 检查新 InnerLifeEvent → 产出 motive (若有)
            await engine.interpret_new_events(agent_id)
            # 2. resolve pending motive
            motive = engine.resolve_pending(agent_id)
            if motive is None:
                logger.info(
                    f"[SM-3 Decision] {agent_id} 无 pending motive → skip publish (F1)"
                )
                return False
            # 3. Decision LLM
            result = await engine.decide(motive, agent_id)
            if result.transmit:
                engine.mark_transmitted(motive.motive_id)
                logger.info(
                    f"[SM-3 Decision] {agent_id} transmit motive={motive.motive_id} "
                    f"reason={result.reason!r}"
                )
                return True
            # TS-2.1 (2026-09-04): observe/reflect 决策 → Actuator 单次执行 + 结果回流。
            # 发布端仍 mark_rejected（observe/reflect 是内部动作, 不 publish AGENT_SPEAK）;
            # Actuator 未注入（默认 None）→ 完全跳过, 与现状等价（空转决策）。
            await self._execute_internal_action(result, motive, agent_id)
            engine.mark_rejected(motive.motive_id)
            logger.info(
                f"[SM-3 Decision] {agent_id} not_transmit motive={motive.motive_id} "
                f"reason={result.reason!r}"
            )
            return False
        except Exception as e:
            # fail-closed: 任何异常 → 不发 (不 auto-send)
            logger.warning(
                f"[SM-3 Decision] {agent_id} exception (fail-closed = skip): "
                f"{type(e).__name__}: {e}"
            )
            return False

    async def _execute_internal_action(
        self,
        result: Any,
        motive: Any,
        agent_id: str,
    ) -> None:
        """
        TS-2.1 (2026-09-04): observe/reflect 决策 → Actuator 单次执行 + 结果回流。

        接线铁律（兑現「空转决策」闭环, 0 自主递归）:
          - actuator 未注入（默认 None）→ 完全跳过, 行为与现状等价（向后兼容）。
          - actuator 注入 + observe → execute_observe → 结果回流感知（world_context）。
          - actuator 注入 + reflect → execute_reflect → 结果回流认知（memory_sink）。
          - transmit → 既有 publish 通道（本方法不处理, _decision_check 已 return True）。
          - do_nothing → 不执行（合法主动选择）。
          - 单次调用、结果只回写感知/认知, 不产生新工具调用、不 publish
            （Actuator 自身持有 0 递归硬规则, 这里只负责按 action 接线）。
          - 任何异常 fail-closed: log warning, 不阻断调用方 mark_rejected
            （motive 生命周期照常收敛, 不因执行器坏掉而悬挂 pending）。
        """
        if self._actuator is None:
            return
        try:
            action = getattr(result, "decision", "")
            if action == "observe":
                await self._actuator.execute_observe(motive, agent_id)
            elif action == "reflect":
                await self._actuator.execute_reflect(motive, agent_id)
            # transmit → publish 通道; do_nothing → 合法不执行（两者都不进 Actuator）
        except Exception as e:
            logger.warning(
                f"[TS-2.1 Actuator] {agent_id} 内部动作执行异常 (fail-closed, "
                f"motive 照常 rejected): action={getattr(result, 'decision', '')} "
                f"err={type(e).__name__}: {e}"
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
        M5.2-H Phase 1 (event) + Phase 2 (dream) + Phase 3 (morning/night via _fire_all) 也已 migrate。
        目前 production 只剩 _fire_heartbeat 仍用此方法 (legacy / migration candidate)。
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

    def register(self, agent_id: str) -> None:
        """
        註冊一個 agent 到 scheduler 的 canonical agent list.

        M5.2-I Phase 6 (Bry 拍板 2026-08-08): callback 改成 Optional.
        M5.2-P-3 (Bry 拍板 2026-08-08): callback 不再被儲存 (_callbacks 移除).
        M5.2-R-3 (Bry 拍板 2026-08-09): callback 參數完全移除 (0 effect since M5.2-P-3).
        真實 trigger 路徑是 AGENCY_TRIGGER (M5.2-H Phase 3+), 跟 callback storage 解耦.
        """
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

        M5.2-R-3 (Bry 拍板 2026-08-09): callback 不再保存 (compat shim 接受 kwargs 但 discard).
        0 read / 0 invocation 從 M5.2-I-6 後. 真實觸發透過 AGENCY_TRIGGER publish 給 handler.
        signature 保留給 frozen v1 兼容 (test_m1_7_event_whitelist_v1 L70 kwargs 呼叫).
        """
        # M5.2-R-3: 不再保存 dream_callback / event_callback (accept-and-discard compat shim)
        # _next_event_time scheduling behavior 必須保留 (scheduler event 排程需要)
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

        M5.2-O-3 (Bry 拍板 2026-08-08): API 改成 compatibility no-op.
        0 production invocation 從 M5.2-G/I-6 後. production 真正路徑是
        AGENCY_TRIGGER event bridge (AgencyTriggerHandler → decision=YES
        → _proactive_dm_llm_executor). API 保留給 legacy test fixture +
        v1 frozen baseline 兼容, callback 參數已不再保存 (compat no-op).
        """
        # M5.2-O-3: 不再保存 callback (compatibility no-op)
        # 排下次時間保留 (跟原本行為一致, 確保 scheduler 排程不變)
        mins = random.randint(
            self.proactive_dm_min_interval_minutes,
            self.proactive_dm_max_interval_minutes,
        )
        self._next_proactive_dm_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 註冊 proactive_dm ✓ (compatibility no-op) "
            f"next={self._next_proactive_dm_time.strftime('%H:%M:%S')} "
            f"interval={mins}min cooldown={self.proactive_dm_cooldown_seconds}s "
            f"quiet={self.quiet_hours_start}:00-{self.quiet_hours_end}:00"
        )

    # M5.2-P-3 (Bry 拍板 2026-08-08): registered_agents() 移除
    # (P-1/P-2 已確認 0 production caller + 0 test caller + 0 external caller,
    #  唯一資料來源 _callbacks 已 DEAD, accessor 本身也 DEAD)

    async def start(self) -> None:
        """啟動排程器背景 task."""
        if self._task is not None:
            logger.warning("[Scheduler] 已經在跑, 不重複 start")
            return
        self._running = True
        # M7-longing (Bry 拍板 2026-08-18): 首次檢查排定。
        # 根因: register_proactive_dm 在 M5.2-O-3 (8/8) 被移除後, _next_proactive_dm_time
        #       永遠停在 None → _is_proactive_dm_time 永遠 False → proactive_dm 完全不會觸發。
        # 修法: start() 時若 None, 排短一點 (30 min) 讓想念驅動能盡快開始評估,
        #       不是等 3-5h 才第一次檢查 (那是 cooldown, 不是首次檢查的意義)。
        if self._next_proactive_dm_time is None:
            self._next_proactive_dm_time = now_local() + timedelta(
                minutes=LONGING_CHECK_INTERVAL_MINUTES
            )
            logger.info(
                f"[M7-longing] proactive_dm 首次檢查排定: "
                f"next={self._next_proactive_dm_time.strftime('%H:%M:%S')} "
                f"interval={LONGING_CHECK_INTERVAL_MINUTES}min"
            )
        # M7-4 (Bry 拍板 2026-08-21): 復活 event schedule timer.
        # 根因: register_dream_event 只在 run_server.py 呼叫一次, 若沒呼叫,
        #       _next_event_time 停在 None → _is_event_time 永遠 False → event 永不觸發.
        # 修法: start() 時若 None, 照 register_dream_event 間隔 pattern (4-8h) 排首次檢查.
        if self._next_event_time is None:
            mins = random.randint(
                self.event_min_interval_minutes,
                self.event_max_interval_minutes,
            )
            self._next_event_time = now_local() + timedelta(minutes=mins)
            logger.info(
                f"[M7-4] event 首次時間排定: "
                f"next={self._next_event_time.strftime('%Y-%m-%d %H:%M')} "
                f"interval={mins}min"
            )
        # Cross-Agent (2026-08-22): shared_event / cross_chat 首次時間排定。
        # 沿用 M7-4 同 pattern (None → 照 register 間隔排首次, 避免永不觸發):
        # 每 6-12h 隨機一次, 全體共用冷卻。
        if self._next_shared_event_time is None:
            mins = random.randint(
                self.shared_activity_min_interval_minutes,
                self.shared_activity_max_interval_minutes,
            )
            self._next_shared_event_time = now_local() + timedelta(minutes=mins)
            logger.info(
                f"[Cross-Agent] shared_event 首次時間排定: "
                f"next={self._next_shared_event_time.strftime('%Y-%m-%d %H:%M')} "
                f"interval={mins}min"
            )
        if self._next_cross_chat_time is None:
            mins = random.randint(
                self.cross_chat_min_interval_minutes,
                self.cross_chat_max_interval_minutes,
            )
            self._next_cross_chat_time = now_local() + timedelta(minutes=mins)
            logger.info(
                f"[Cross-Agent] cross_chat 首次時間排定: "
                f"next={self._next_cross_chat_time.strftime('%Y-%m-%d %H:%M')} "
                f"interval={mins}min"
            )
        self._task = asyncio.create_task(self._run_loop(), name="SoulScheduler")
        logger.info(
            f"[Scheduler] 啟動 ✓ morning={self.morning_time} "
            f"night={self.night_time} prob={self.trigger_probability} "
            f"agents={len(self._all_agents)}"
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
        # P0.5 (Bry 派工 2026-08-09 19:48): use data_root() for test isolation
        from src.paths import data_root
        data_dir = data_root() / "soul"

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
    # Cross-Agent (2026-08-22): Layer 2 shared_event + Layer 3 cross_chat
    # ───────────────────────────────────────────────────────────
    # 迴圈防護 (核心, 不可違反):
    #   - scheduler 驅動, 不是事件驅動: 對話/活動由 timer 主動開, 訊息不會觸發對方
    #   - 有界: shared_event 單一事件; cross_chat 最多 3 輪
    #   - 限頻 + 全體共用冷卻: 各一個 timer (6-12h 隨機), 不會連續觸發
    #   - 不自我觸發: 兩者都只 call LLM (dream_event writer), 不 publish
    #     AGENT_INTENT / AGENCY_TRIGGER / AGENT_SPEAK, 不進 Bryan 群聊路徑
    #   - 與 Bryan 隔離: cross_chat 是封閉事件, 不走 event bus

    def _is_shared_event_time(self, now: datetime) -> bool:
        """Layer 2: shared_event 是否到點 (全體共用冷卻 timer)。"""
        if self._next_shared_event_time is None:
            return False
        return now >= self._next_shared_event_time

    def _is_cross_chat_time(self, now: datetime) -> bool:
        """Layer 3: cross_chat 是否到點 (全體共用冷卻 timer, 跟 shared_event 分開)。"""
        if self._next_cross_chat_time is None:
            return False
        return now >= self._next_cross_chat_time

    def _reschedule_shared_event(self) -> None:
        """排下次 shared_event (6-12h 隨機, 全體共用冷卻)。"""
        mins = random.randint(
            self.shared_activity_min_interval_minutes,
            self.shared_activity_max_interval_minutes,
        )
        self._next_shared_event_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 🤝 下次 shared_event: "
            f"{self._next_shared_event_time.strftime('%Y-%m-%d %H:%M')} "
            f"interval={mins}min"
        )

    def _reschedule_cross_chat(self) -> None:
        """排下次 cross_chat 6-12h 隨機 (全體共用冷卻, 跟 shared_event 分開)。"""
        mins = random.randint(
            self.cross_chat_min_interval_minutes,
            self.cross_chat_max_interval_minutes,
        )
        self._next_cross_chat_time = now_local() + timedelta(minutes=mins)
        logger.info(
            f"[Scheduler] 💬 下次 cross_chat: "
            f"{self._next_cross_chat_time.strftime('%Y-%m-%d %H:%M')} "
            f"interval={mins}min"
        )

    async def _fire_shared_event(self) -> None:
        """
        Layer 2 (2026-08-22): 抽 2 隻角色 + 1 個活動, 兩隻各自寫 diary (slot=event),
        記錄到 interactions.jsonl。單一事件、無對話、零迴圈風險。

        迴圈防護: 只 call writer.write_shared_event (內部只 call LLM), 不 publish
        AGENT_INTENT / AGENCY_TRIGGER / AGENT_SPEAK, 不觸發其他角色。
        """
        if len(self._all_agents) < 2:
            logger.debug(
                f"[Scheduler] 🤝 shared_event 跳過: 少於 2 隻角色 "
                f"({len(self._all_agents)})"
            )
            self._reschedule_shared_event()
            return
        try:
            from src.soul.dream_event import ACTIVITY_POOL, get_dream_event_writer
            a, b = random.sample(self._all_agents, 2)
            activity = random.choice(ACTIVITY_POOL)
        except Exception as e:
            logger.warning(f"[Scheduler] shared_event 抽樣/載入失敗: {e}")
            self._reschedule_shared_event()
            return
        logger.info(
            f"[Scheduler] 🤝 shared_event 觸發: {a} + {b} 一起 {activity['name']} "
            f"(category={activity['category']})"
        )
        content = ""
        try:
            writer = get_dream_event_writer()
            _, content = await writer.write_shared_event(a, b, activity)
            await writer.write_shared_event(b, a, activity)
        except Exception as e:
            logger.warning(f"[Scheduler] shared_event diary 寫入失敗: {e}")
        # 記錄 interactions.jsonl (append-only)
        try:
            from datetime import timezone as _tz
            record = {
                "ts": datetime.now(_tz.utc).isoformat(),
                "type": "shared_event",
                "agents": [a, b],
                "activity": activity["name"],
                "content": content or f"今天和 {b} 一起做了 {activity['name']}",
            }
            _append_interaction(record)
        except Exception as e:
            logger.warning(f"[Scheduler] shared_event 記錄失敗: {e}")
        self._reschedule_shared_event()

    async def _fire_cross_chat(self) -> None:
        """
        Layer 3 (2026-08-22): 3 輪封閉對話 (A 開場 → B 回應 → A 收尾)。

        迴圈防護 (硬性):
          - 3 輪封頂: 只 call LLM 3 次, 到點就結束
          - scheduler 明確驅動: 每輪由這裡逐輪 call writer.generate_chat_turn,
            A 的訊息不會觸發 B — 是 scheduler 叫 B 回
          - 不 publish AGENT_INTENT / AGENCY_TRIGGER / AGENT_SPEAK: 對話是
            「封閉事件」, 直接 call LLM, 不走 event bus, 不觸發其他角色,
            不進 Bryan 群聊路徑
          - 結束後 touch 兩隻的 relationship (可選, 失敗不中斷)
        """
        if len(self._all_agents) < 2:
            logger.debug(
                f"[Scheduler] 💬 cross_chat 跳過: 少於 2 隻角色 ({len(self._all_agents)})"
            )
            self._reschedule_cross_chat()
            return
        a, b = random.sample(self._all_agents, 2)
        logger.info(f"[Scheduler] 💬 cross_chat 觸發: {a} ↔ {b} (3 輪封頂)")
        messages = []
        try:
            from src.soul.dream_event import get_dream_event_writer
            writer = get_dream_event_writer()
            # Turn 1: A 開場
            m1 = await writer.generate_chat_turn(a, b, turn=1)
            messages.append({"agent": a, "content": m1})
            # Turn 2: B 回應
            m2 = await writer.generate_chat_turn(b, a, turn=2, partner_message=m1)
            messages.append({"agent": b, "content": m2})
            # Turn 3: A 收尾
            m3 = await writer.generate_chat_turn(a, b, turn=3, partner_message=m2)
            messages.append({"agent": a, "content": m3})
        except Exception as e:
            logger.warning(f"[Scheduler] cross_chat LLM 失敗: {e}")
        # 記錄 interactions.jsonl (append-only)
        try:
            from datetime import timezone as _tz
            record = {
                "ts": datetime.now(_tz.utc).isoformat(),
                "type": "cross_chat",
                "agents": [a, b],
                "messages": messages,
            }
            _append_interaction(record)
        except Exception as e:
            logger.warning(f"[Scheduler] cross_chat 記錄失敗: {e}")
        # 結束後 touch 兩隻的 relationship (可選, 失敗不中斷)
        try:
            from src.soul.relationships import get_relationships_manager
            mgr = get_relationships_manager()
            mgr.on_agent_speak(a, [a, b])
            mgr.on_agent_speak(b, [a, b])
        except Exception as e:
            logger.warning(f"[Scheduler] cross_chat relationship touch 失敗: {e}")
        self._reschedule_cross_chat()

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

    def _get_recent_shareable_activity(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """
        M7-2 (Bry 拍板 2026-08-18): 找 agent 最近一件「值得分享」的活動。

        讀 data_root()/soul/{agent_id}/diary/{today}.jsonl, 找最新
        slot=="event" 且 shareable==True 且 source=="llm" 的 entry。

        Returns:
            {"activity": str, "category": str, "content": str, "ts": str}
            或 None (沒 diary / 沒 shareable 活動 / 解析失敗)。

        設計註記 (M7-2 拍板):
          主動傳訊與活動「密不可分」採「enrichment」而非「新觸發源」—
          因為 event (活動) 對單一 agent 頻率太低 (約 20-40h 一次),
          不足以單獨驅動 5-8 條/天; 改在既有 3-5h random 節奏上,
          把 agent 最近 shareable 活動帶進 draft。這樣也自然避開
          M5.8-4 inner-life gate (30min) 的衝突 (random timer 觸發時
          活動早已超過 30min)。
        """
        from src.paths import data_root
        import json as _json
        today = datetime.now().strftime("%Y-%m-%d")
        path = data_root() / "soul" / agent_id / "diary" / f"{today}.jsonl"
        if not path.is_file():
            return None
        latest: Optional[Dict[str, Any]] = None
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                if entry.get("slot") != "event":
                    continue
                if entry.get("shareable") is not True:
                    continue
                if entry.get("source") != "llm":
                    continue
                if not entry.get("activity"):
                    continue
                if latest is None or entry.get("ts", "") > latest.get("ts", ""):
                    latest = entry
        except Exception as e:
            logger.warning(f"[M7-2] 讀 diary 失敗 ({agent_id}): {e}")
            return None
        if latest is None:
            return None
        return {
            "activity": latest.get("activity", ""),
            "category": latest.get("category", ""),
            "content": latest.get("content", ""),
            "ts": latest.get("ts", ""),
        }

    def _get_bry_silence_minutes(self, agent_id: str) -> Optional[float]:
        """
        M7-context (Bry 拍板 2026-08-18): 讀 Bry 最後跟該 agent 互動的時間, 算沉默分鐘數。

        純讀 relationships.json (跟 _get_recent_shareable_activity 同 pattern, 無 side effect)。
        MemoryMiddleware._on_user_message 會在每次 Bry 對該 agent 發話時 touch
        user_bryan.last_interaction_at (UTC ISO), 所以這是可靠的 per-agent 訊號。

        Returns:
            沉默分鐘數 (float), 或 None (沒 relationships 檔 / 沒 user_bryan / 沒互動 / 解析失敗)。
        """
        from src.paths import data_root
        import json as _json
        from datetime import timezone as _tz
        path = data_root() / "soul" / agent_id / "relationships.json"
        if not path.is_file():
            return None
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            last_at = data.get("others", {}).get("user_bryan", {}).get("last_interaction_at")
        except Exception:
            return None
        if not last_at:
            return None
        try:
            last_dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=_tz.utc)
        now_utc = datetime.now(_tz.utc)
        return (now_utc - last_dt).total_seconds() / 60.0

    def _bryan_last_seen_minutes(self) -> Optional[float]:
        """
        Proactive DM 三件修復 #1 (Bry 拍板 2026-08-29): Bry 最後看見時間距現在的分鐘數。

        統一信號源: bryan_state.read_bryan_last_seen() 讀 data/state/bryan_last_seen.json
        (TG inbound + web inbound 都更新它, 見 bryan_state.py)。

        Returns:
            分鐘數 (float), 或 None (冷啟動: 沒檔案 / 解析失敗)。
            None 代表「Bry 從沒被看見過」→ 不 throttle (跟 router M0.5 一致)。
        """
        from src.io.channels.bryan_state import read_bryan_last_seen
        from datetime import timezone as _tz
        last = read_bryan_last_seen()
        if last is None:
            return None
        now_utc = datetime.now(_tz.utc)
        return (now_utc - last).total_seconds() / 60.0

    def _get_base_intimacy(self, agent_id: str) -> float:
        """
        M7-longing: 讀 config 的 intimacy_level (靜態基礎親密度) 當「依戀」來源。

        不用 emotion_engine.get() 的原因: 它會隨互動累積、全都漂到 100,
        失去 Yua(80)/Ruka(60)/Ram(40) 的角色差異化。config 的 intimacy_level 才是
        「這隻角色天生多黏 Bry」的穩定訊號 (per 決策 #3)。
        fail-silent: 讀不到 config → 50.0 預設。
        """
        if not hasattr(self, "_base_intimacy_cache"):
            self._base_intimacy_cache: Dict[str, float] = {}
            try:
                from configs.loader import load_config
                cfg = load_config()
                for agent_cfg in cfg.get("agents", []):
                    aid = agent_cfg.get("id")
                    if aid:
                        self._base_intimacy_cache[aid] = float(
                            agent_cfg.get("intimacy_level", 50)
                        )
            except Exception as e:
                logger.warning(f"[M7-longing] 讀 config intimacy 失敗: {e}")
        return self._base_intimacy_cache.get(agent_id, 50.0)

    def _get_agent_longing(self, agent_id: str) -> float:
        """
        M7-longing: 想念 = 依戀(intimacy) × 有效沉默時長 (compute_longing 現算, 不持久化)。

        有效沉默 = min(Bry 上次講話的沉默, 上次主動傳訊的沉默):
          - Bry 剛講話 → 沉默小 → 不想念 (正在聊天不突兀)。
          - 剛主動傳過 → 沉默小 → 不想念 (表達後緩解, 避免 Bry 一直不回就每 3-5h 轟炸)。
        從未互動 (Bry 從沒跟該 agent 講過話) → 0.0 (不想念)。
        """
        bry_silence = self._get_bry_silence_minutes(agent_id)
        if bry_silence is None:
            return 0.0
        effective = bry_silence
        if self._last_proactive_dm_time is not None:
            proactive_silence = (
                now_local() - self._last_proactive_dm_time
            ).total_seconds() / 60.0
            effective = min(effective, max(0.0, proactive_silence))
        intimacy = self._get_base_intimacy(agent_id)
        from src.agent.emotion import compute_longing
        return compute_longing(intimacy, effective)

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
        (M5.2-O-3 移除 _proactive_dm_callback field, production 0 invocation 從 M5.2-G/I-6 後)
        AgencyTriggerHandler 訂閱 AGENCY_TRIGGER → 跑 4 stages → if YES, invoke LLM。

        仍然負責: 觸發時機 / quiet hours / scheduler cooldown / whitelist / target selection
        不再負責: 觸發後是否 act (Agency decision) / LLM call (Agency execution)
        """
        candidates = self._get_proactive_agents()
        # M5.2-G: AGENCY_TRIGGER path, 不再 invoke callback.
        # (M5.2-O-3 移除 _proactive_dm_callback field, 0 invocation 從 M5.2-G/I-6 後)
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

        # 2.5 可送達檢查 (Proactive DM 三件修復 #1, Bry 拍板 2026-08-29):
        # Bry 最後看見時間 > PROACTIVE_DM_BRYAN_INACTIVE_HOURS (4h) → skip,
        # 不 publish AGENCY_TRIGGER、不觸發 LLM (省 token)。
        # 背景: 8/19-8/29 共 23 次 proactive_dm 觸發, 每次 = 真實 LLM 調用
        # (~14k tokens) + 創建 InnerLifeEvent, 然後被 router M0.5 THROTTLED 丟棄。
        # 修法: 把 router 的 throttle 邏輯提前到 scheduler 層 (router 的 throttle
        # 保留作兜底)。統一信號源 = bryan_last_seen.json (TG + web inbound 都更新)。
        # 冷啟動 (bryan_last_seen 不存在) → 不 skip (跟 router M0.5 一致)。
        from src.io.channels.bryan_state import PROACTIVE_DM_BRYAN_INACTIVE_HOURS
        bry_minutes = self._bryan_last_seen_minutes()
        if bry_minutes is not None and bry_minutes > PROACTIVE_DM_BRYAN_INACTIVE_HOURS * 60:
            logger.info(
                f"[Scheduler] 💬 proactive_dm 不可送達: Bry 最後看見 "
                f"{bry_minutes / 60.0:.1f}h 前 > {PROACTIVE_DM_BRYAN_INACTIVE_HOURS}h, "
                f"skip (不 publish AGENCY_TRIGGER、不觸發 LLM)"
            )
            # 排 30 min 後再查 (Bry 可能隨時上線, 跟 longing gate 同節奏)
            self._next_proactive_dm_time = now_local() + timedelta(
                minutes=LONGING_CHECK_INTERVAL_MINUTES
            )
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
        # M7-longing (Bry 拍板 2026-08-18): 想念驅動 — 取代定時器 + 在線 gate。
        # 想念 = 依戀(intimacy) × 有效沉默時長。想念跨過門檻才觸發,
        # 否則排 30 min 後再查 (不是等 3-5h)。這同時涵蓋:
        #   - 「正在聊天突兀」: 沉默=0 → 想念=0 → 不觸發。
        #   - 「角色差異化節奏」: Yua(80) 比 Ram(40) 更早、更常想 Bry。
        #   - 「表達後緩解」: 剛主動傳過 → 有效沉默歸零 → 不會連續轟炸。
        longing = self._get_agent_longing(agent_id)
        if longing < LONGING_THRESHOLD:
            logger.info(
                f"[M7-longing] {agent_id} 想念 {longing:.2f} < {LONGING_THRESHOLD}, "
                f"排 {LONGING_CHECK_INTERVAL_MINUTES} min 後再查"
            )
            self._next_proactive_dm_time = now_local() + timedelta(
                minutes=LONGING_CHECK_INTERVAL_MINUTES
            )
            return
        logger.info(
            f"[M7-longing] {agent_id} 想念 {longing:.2f} >= {LONGING_THRESHOLD}, 觸發主動傳訊"
        )
        # M5.2-G (Bry 拍板 2026-08-08): publish AGENCY_TRIGGER
        # M5.2-I Phase 6: 移除 callback invocation. 真實 LLM
        # 由 AgencyTriggerHandler 訂閱 AGENCY_TRIGGER 觸發.
        # M7-2 (Bry 拍板 2026-08-18): 活動驅動 — 把 agent 最近 shareable 活動帶進 extra
        # (去重: 同一活動 ts 只帶一次, 之後 fall back 到通用草稿)
        extra: Dict[str, Any] = {}
        activity = self._get_recent_shareable_activity(agent_id)
        if activity and activity.get("ts") != self._last_shared_activity_ts.get(agent_id):
            extra = {"trigger_source": "activity", "activity": activity}
            self._last_shared_activity_ts[agent_id] = activity.get("ts", "")
            logger.info(
                f"[M7-2] proactive_dm 帶活動: {agent_id} "
                f"activity={activity.get('activity')} ts={activity.get('ts')}"
            )
        await self._publish_agency_trigger(agent_id, trigger_type="proactive_dm", extra=extra)
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
                # 3.5 Cross-Agent (2026-08-22): shared_event (每 6-12h 一次, 全體共用冷卻)
                if self._is_shared_event_time(now):
                    await self._fire_shared_event()
                # 3.6 Cross-Agent (2026-08-22): cross_chat (每 6-12h 一次, 獨立冷卻)
                if self._is_cross_chat_time(now):
                    await self._fire_cross_chat()
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
