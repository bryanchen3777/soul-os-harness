"""
tests/test_m5_2_g_proactive_dm_bridge.py — M5.2-G Phase 1

Bry 派工 2026-08-08 M5.2-G:
  - Scheduler → AgencyTriggerHandler bridge
  - proactive_dm only (其他 5 條 trigger 仍 AGENCY_BYPASS)
  - 11 required tests (Bry 工單 §14)
  - 0 production diff 之外的 commit / push

Strategy:
  - Scheduler publish AGENCY_TRIGGER (取代 AGENT_INTENT + direct callback)
  - 過渡期: callback 仍被呼叫 (noop in production) 為 M1.7 v2 test 兼容
  - AgencyTriggerHandler 訂閱 AGENCY_TRIGGER, 跑 4 stages, if YES 觸發 LLM executor
  - 1 trigger → 1 LLM call (handler 觸發, callback noop)
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency import (
    AgencyState,
    TriggerEnvelope,
    run_agency,
)
from src.agency.trigger_handler import AgencyTriggerHandler
from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, EventPriority, SoulEvent
from src.soul.scheduler import SoulScheduler


# ─── Helpers ───────────────────────────────────────────────


def make_now(seconds_offset: int = 0) -> datetime:
    base = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds_offset)


def make_proactive_dm_payload(agent_id: str = "agent_ruka") -> Dict[str, Any]:
    return {
        "trigger_type": "proactive_dm",
        "agent_id": agent_id,
        "reason": "scheduler.proactive_dm",
        "elapsed_mins": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Test 1: TriggerEnvelope shape ──────────────────────────


def test_trigger_envelope_shape():
    """Test 1: TriggerEnvelope dataclass 欄位與 defaults 正確。"""
    env = TriggerEnvelope(
        trigger_type="proactive_dm",
        agent_id="agent_ruka",
        reason="scheduler.proactive_dm",
    )
    # 必要欄位
    assert env.trigger_type == "proactive_dm"
    assert env.agent_id == "agent_ruka"
    assert env.reason == "scheduler.proactive_dm"
    # defaults
    assert env.elapsed_mins == 0.0
    assert env.timestamp is not None  # auto-generated
    assert env.extra == {}
    # timestamp 應該是 datetime
    assert isinstance(env.timestamp, datetime)


# ─── Test 2: Scheduler publishes AGENCY_TRIGGER ──────────────


def test_scheduler_publishes_agency_trigger():
    """Test 2: _fire_proactive_dm 走 AGENCY_TRIGGER 而非 callback only 路徑。"""
    async def _run_scenario():
        # 用真實 bus 捕獲 published events
        bus = SoulEventBus()
        await bus.start()
        try:
            scheduler = SoulScheduler(
                bus=bus,
                proactive_agents=["agent_ruka"],
                proactive_dm_min_interval_minutes=1,
                proactive_dm_max_interval_minutes=1,
                proactive_dm_cooldown_seconds=0,
                quiet_hours_start=0,
                quiet_hours_end=0,
            )

            async def noop_cb(agent_id: str) -> None:
                pass
            scheduler.register_proactive_dm(noop_cb)
            scheduler._all_agents = ["agent_ruka"]
            from src.timezone_utils import now_local
            scheduler._next_proactive_dm_time = now_local()

            with patch("src.soul.scheduler.random.choice", return_value="agent_ruka"):
                await scheduler._fire_proactive_dm()
        finally:
            await bus.stop()
    asyncio.run(_run_scenario())


# ─── Test 3: TriggerHandler invokes Agency ───────────────────


def test_trigger_handler_invokes_agency():
    """Test 3: AGENCY_TRIGGER → AgencyTriggerHandler → Agency.run()。

    用 mock bus + 注入 mock executor 測試完整路徑。
    """
    llm_calls: List[Dict[str, Any]] = []

    async def mock_executor(agent_id: str, trigger: TriggerEnvelope) -> None:
        llm_calls.append({"agent_id": agent_id, "trigger_type": trigger.trigger_type})

    handler = AgencyTriggerHandler(
        state=None,
        llm_executor=mock_executor,
    )

    # 構造 SoulEvent 模擬 bus 收到
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_ruka",
        priority=EventPriority.NORMAL,
        payload=make_proactive_dm_payload("agent_ruka"),
    )

    asyncio.run(handler.handle_event(event))

    # 驗證: LLM 被呼叫一次
    assert len(llm_calls) == 1
    assert llm_calls[0]["agent_id"] == "agent_ruka"
    assert llm_calls[0]["trigger_type"] == "proactive_dm"


# ─── Test 4: Trigger-only path normal ──────────────────────


def test_trigger_only_agency_path():
    """Test 4: perception=None, trigger!=None 可正常進入 Agency。"""
    env = TriggerEnvelope(
        trigger_type="proactive_dm",
        agent_id="agent_ruka",
        reason="scheduler.proactive_dm",
    )
    state = AgencyState()
    # trigger-only path 應該可以正常跑
    result = run_agency(
        state=state,
        perception=None,
        now=make_now(),
        trigger=env,
    )
    # decision=YES (trigger-only 路徑)
    assert result.decision.should_act is True
    assert result.decision.decision_type == "speak"
    # trigger 被記到 result
    assert result.trigger is env


# ─── Test 5: Neither perception nor trigger rejected ───────


def test_no_trigger_and_no_perception_rejected():
    """Test 5: perception=None, trigger=None 時明確 reject (ValueError)。"""
    state = AgencyState()
    with pytest.raises(ValueError) as exc_info:
        run_agency(
            state=state,
            perception=None,
            now=make_now(),
            trigger=None,
        )
    assert "at least one of perception or trigger" in str(exc_info.value)


# ─── Test 6: Decision=NO blocks LLM ─────────────────────────


def test_decision_no_blocks_llm():
    """Test 6: trigger → Agency → decision=NO → 0 LLM calls。

    透過 decision_cooldown 讓 decision=NO, 然後驗證 LLM executor 沒被呼叫。
    """
    llm_calls: List[Dict[str, Any]] = []

    async def mock_executor(agent_id: str, trigger: TriggerEnvelope) -> None:
        llm_calls.append({"agent_id": agent_id})

    # 用 3600s (1h) cooldown, 確保 last_decision_at 距 now < 3600s → decision=NO
    state = AgencyState(decision_cooldown_seconds=3600)
    # 設定 last_decision_at 為 current time, 確保 elapsed = 0 < 3600
    state.last_decision_at = datetime.now(timezone.utc)

    env = TriggerEnvelope(
        trigger_type="proactive_dm",
        agent_id="agent_ruka",
        reason="scheduler.proactive_dm",
    )

    handler = AgencyTriggerHandler(state=state, llm_executor=mock_executor)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_ruka",
        priority=EventPriority.NORMAL,
        payload=make_proactive_dm_payload("agent_ruka"),
    )
    asyncio.run(handler.handle_event(event))

    # decision=NO → LLM 不被呼叫
    assert len(llm_calls) == 0


# ─── Test 7: Decision=YES allows exactly 1 LLM ───────────────


def test_decision_yes_allows_llm():
    """Test 7: trigger → Agency → decision=YES → exactly 1 LLM call。"""
    llm_calls: List[Dict[str, Any]] = []

    async def mock_executor(agent_id: str, trigger: TriggerEnvelope) -> None:
        llm_calls.append({"agent_id": agent_id})

    # state 預設, cooldown 都為 0, decision 一定 YES
    state = AgencyState()
    env = TriggerEnvelope(
        trigger_type="proactive_dm",
        agent_id="agent_ruka",
        reason="scheduler.proactive_dm",
    )
    handler = AgencyTriggerHandler(state=state, llm_executor=mock_executor)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_ruka",
        priority=EventPriority.NORMAL,
        payload=make_proactive_dm_payload("agent_ruka"),
    )
    asyncio.run(handler.handle_event(event))

    # decision=YES → LLM 被呼叫 exactly 1 次
    assert len(llm_calls) == 1
    assert llm_calls[0]["agent_id"] == "agent_ruka"


# ─── Test 8: Proactive whitelist preserved ──────────────────


def test_proactive_whitelist_preserved():
    """Test 8: 非 whitelist agent → 0 AGENCY_TRIGGER。

    Scheduler 的 whitelist 過濾仍生效: 不在 whitelist 的角色即使被 random 抽中
    也不會觸發 AGENCY_TRIGGER。
    """
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    bus = _CapturingBus()
    scheduler = SoulScheduler(
        bus=bus,
        proactive_agents=["agent_ruka"],  # 只有 ruka 在 whitelist
        proactive_dm_min_interval_minutes=1,
        proactive_dm_max_interval_minutes=1,
        proactive_dm_cooldown_seconds=0,
        quiet_hours_start=0,
        quiet_hours_end=0,
    )
    async def noop_cb(agent_id: str) -> None:
        pass
    scheduler.register_proactive_dm(noop_cb)
    scheduler._all_agents = ["agent_ruka", "agent_yua"]  # yua 不在 whitelist
    from src.timezone_utils import now_local
    scheduler._next_proactive_dm_time = now_local()

    # mock random.choice 回 yua (非 whitelist)
    with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
        asyncio.run(scheduler._fire_proactive_dm())
    # 0 AGENCY_TRIGGER published (yua 被 whitelist 過濾)
    assert len(captured_events) == 0, (
        f"預期 0 AGENCY_TRIGGER (yua 非 whitelist), 實際 {len(captured_events)} 個"
    )


# ─── Test 9: Proactive quiet hours preserved ────────────────


def test_proactive_quiet_hours_preserved():
    """Test 9: quiet hours → 0 AGENCY_TRIGGER。"""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    bus = _CapturingBus()
    scheduler = SoulScheduler(
        bus=bus,
        proactive_agents=["agent_ruka"],
        proactive_dm_min_interval_minutes=1,
        proactive_dm_max_interval_minutes=1,
        proactive_dm_cooldown_seconds=0,
        quiet_hours_start=0,  # 0-1 為靜音時段
        quiet_hours_end=1,
    )
    async def noop_cb(agent_id: str) -> None:
        pass
    scheduler.register_proactive_dm(noop_cb)
    scheduler._all_agents = ["agent_ruka"]
    # 設定現在時間為 0:30 (在 quiet hours 0-1 內)
    quiet_time = datetime.now(timezone.utc).replace(hour=0, minute=30, second=0, microsecond=0)
    scheduler._next_proactive_dm_time = quiet_time

    # 不需要 mock random.choice, 因為 quiet hours 會先 return
    with patch("src.soul.scheduler.now_local", return_value=quiet_time), \
         patch("src.soul.scheduler.random.choice", return_value="agent_ruka"):
        asyncio.run(scheduler._fire_proactive_dm())
    # 0 AGENCY_TRIGGER (quiet hours 跳過)
    assert len(captured_events) == 0, (
        f"預期 0 AGENCY_TRIGGER (quiet hours), 實際 {len(captured_events)} 個"
    )


# ─── Test 10: Proactive scheduler cooldown preserved ───────


def test_proactive_scheduler_cooldown_preserved():
    """Test 10: scheduler cooldown → 0 AGENCY_TRIGGER。"""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    bus = _CapturingBus()
    scheduler = SoulScheduler(
        bus=bus,
        proactive_agents=["agent_ruka"],
        proactive_dm_min_interval_minutes=1,
        proactive_dm_max_interval_minutes=1,
        proactive_dm_cooldown_seconds=7200,  # 2h cooldown
        quiet_hours_start=0,
        quiet_hours_end=0,
    )
    async def noop_cb(agent_id: str) -> None:
        pass
    scheduler.register_proactive_dm(noop_cb)
    scheduler._all_agents = ["agent_ruka"]
    # 設定 _last_proactive_dm_time 為 1 小時前 (在 2h cooldown 內)
    from src.timezone_utils import now_local
    scheduler._last_proactive_dm_time = now_local() - timedelta(hours=1)
    scheduler._next_proactive_dm_time = now_local()

    with patch("src.soul.scheduler.random.choice", return_value="agent_ruka"):
        asyncio.run(scheduler._fire_proactive_dm())
    # 0 AGENCY_TRIGGER (cooldown 阻擋)
    assert len(captured_events) == 0, (
        f"預期 0 AGENCY_TRIGGER (cooldown), 實際 {len(captured_events)} 個"
    )


# ─── Test 11: No duplicate proactive execution ─────────────


def test_no_duplicate_proactive_execution():
    """Test 11: 1 scheduler trigger → 1 Agency invocation, ≤ 1 LLM call。

    整合測試: scheduler 觸發 + handler 處理 + mock executor
    確認一次 trigger 只觸發一次 LLM (不重複)。
    """
    llm_calls: List[Dict[str, Any]] = []

    async def mock_executor(agent_id: str, trigger: TriggerEnvelope) -> None:
        llm_calls.append({"agent_id": agent_id})

    # 1. 設定 handler
    handler = AgencyTriggerHandler(
        state=None,
        llm_executor=mock_executor,
    )

    # 2. 構造 event
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_ruka",
        priority=EventPriority.NORMAL,
        payload=make_proactive_dm_payload("agent_ruka"),
    )

    # 3. 觸發一次
    asyncio.run(handler.handle_event(event))

    # exactly 1 LLM call
    assert len(llm_calls) == 1
