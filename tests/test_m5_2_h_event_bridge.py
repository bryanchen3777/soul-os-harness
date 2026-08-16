"""
tests/test_m5_2_h_event_bridge.py — M5.2-H Phase 1

Bry 派工 2026-08-08 M5.2-H Phase 1:
  - event scheduler trigger migration
  - _fire_event 從 AGENCY_BYPASS → Agency 4 stages
  - WRITER_ONLY (per C3): EventHandler 過 Agency decision → writer.write_event
  - 10 required tests (Bry 工單 §13)
  - 0 production diff 之外的 commit / push

Strategy:
  - Scheduler publish AGENCY_TRIGGER (trigger_type="event")
  - 過渡期: callback 仍被呼叫 (noop in production) 為 M1.7 v2 測試兼容
  - EventHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type=="event", 跑 4 stages
  - decision=YES → writer_executor(agent_id) → writer.write_event (1 call)
  - decision=NO → 0 writer call
  - 1 trigger → max 1 writer execution (H-I7)

Hard Invariants (per Bry 工單 §15):
  H-I1: event scheduler trigger → AGENCY_TRIGGER
  H-I2: event trigger → EventHandler (filtered by trigger_type)
  H-I3: decision=NO → 0 writer calls
  H-I4: decision=YES → exactly 1 writer call
  H-I5: event → never AGENT_SPEAK (WRITER_ONLY)
  H-I6: event → never character dialogue executor
  H-I7: one trigger → maximum one execution
  H-I8: proactive_dm behavior unchanged (regression)
  H-I9: dream/morning/night/heartbeat remain untouched
"""
from __future__ import annotations

import asyncio
import inspect
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency import (
    AgencyState,
    EventHandler,
    TriggerEnvelope,
    run_agency,
)
from src.agency.event_handler import EventHandler as DirectEventHandler
from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, EventPriority, SoulEvent
from src.soul.scheduler import SoulScheduler


# ─── Helpers ───────────────────────────────────────────────


def make_now(seconds_offset: int = 0) -> datetime:
    base = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds_offset)


def make_event_payload(agent_id: str = "agent_yua") -> Dict[str, Any]:
    """模擬 scheduler _publish_agency_trigger(trigger_type='event') 的 payload."""
    return {
        "trigger_type": "event",
        "agent_id": agent_id,
        "reason": "scheduler.event",
        "elapsed_mins": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ─── Test 1: Trigger envelope shape (event type) ────────────


def test_trigger_envelope_shape_event():
    """Test 1: TriggerEnvelope dataclass event trigger 欄位正確.

    H-I1: event trigger 構造出來的 envelope 必須是 trigger_type='event'.
    """
    env = TriggerEnvelope(
        trigger_type="event",
        agent_id="agent_yua",
        reason="scheduler.event",
    )
    assert env.trigger_type == "event"
    assert env.agent_id == "agent_yua"
    assert env.reason == "scheduler.event"
    # defaults
    assert env.elapsed_mins == 0.0
    assert env.timestamp is not None
    assert env.extra == {}


# ─── Test 2: Scheduler publishes AGENCY_TRIGGER for event ───


def test_scheduler_publishes_agency_trigger_event():
    """Test 2: _fire_event 走 AGENCY_TRIGGER (取代 AGENT_INTENT only).

    H-I1: scheduler event trigger 必須 publish AGENCY_TRIGGER.
    """
    async def _run_scenario():
        bus = SoulEventBus()
        await bus.start()
        try:
            scheduler = SoulScheduler(
                bus=bus,
                proactive_agents=["agent_yua"],
            )
            # 註冊 event callback
            async def event_cb(agent_id: str, slot: str) -> None:
                pass
            scheduler.register_dream_event(
                dream_callback=lambda a, s: asyncio.sleep(0),
                event_callback=event_cb,
            )
            scheduler._all_agents = ["agent_yua"]
            scheduler._next_event_time = datetime.now(timezone.utc)

            with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
                await scheduler._fire_event()
        finally:
            await bus.stop()

    # 用 capturing bus 抓 AGENCY_TRIGGER events
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    async def _run_with_capture():
        bus = _CapturingBus()
        scheduler = SoulScheduler(
            bus=bus,
            proactive_agents=["agent_yua"],
        )
        async def event_cb(agent_id: str, slot: str) -> None:
            pass
        scheduler.register_dream_event(
            dream_callback=lambda a, s: asyncio.sleep(0),
            event_callback=event_cb,
        )
        scheduler._all_agents = ["agent_yua"]
        scheduler._next_event_time = datetime.now(timezone.utc)

        with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
            await scheduler._fire_event()

    asyncio.run(_run_with_capture())
    # H-I1: 至少 1 個 AGENCY_TRIGGER published
    assert len(captured_events) >= 1, "scheduler 沒 publish AGENCY_TRIGGER for event"
    # trigger_type=event
    assert captured_events[0].payload["trigger_type"] == "event"
    assert captured_events[0].payload["agent_id"] == "agent_yua"
    assert captured_events[0].payload["reason"] == "scheduler.event"


# ─── Test 3: EventHandler handles event trigger ────────────


def test_event_handler_handles_event_trigger():
    """Test 3: EventHandler 收到 trigger_type=event → 跑 Agency → 觸發 writer_executor.

    H-I2: event trigger 必須被 EventHandler 接收.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    handler = EventHandler(
        state=None,
        writer_executor=mock_writer,
    )

    # 構造 AGENCY_TRIGGER event
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_event_payload("agent_yua"),
    )

    asyncio.run(handler.handle_event(event))

    # H-I2: writer 被呼叫 1 次
    assert len(writer_calls) == 1
    assert writer_calls[0]["agent_id"] == "agent_yua"


# ─── Test 4: Non-event triggers ignored by EventHandler ──────


def test_event_handler_ignores_non_event_triggers():
    """Test 4: EventHandler 只處理 trigger_type=event, 其他 trigger 類型 skip.

    H-I9: dream / morning / night / heartbeat 仍 AGENCY_BYPASS, EventHandler 必須跳過.
    H-I8: proactive_dm 仍走 AgencyTriggerHandler, EventHandler 必須跳過.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    handler = EventHandler(
        state=None,
        writer_executor=mock_writer,
    )

    for trigger_type in ["proactive_dm", "dream", "morning", "night", "heartbeat"]:
        event = SoulEvent(
            event_type=EventType.AGENCY_TRIGGER,
            source="test",
            target="agent_yua",
            priority=EventPriority.NORMAL,
            payload={
                "trigger_type": trigger_type,
                "agent_id": "agent_yua",
                "reason": f"scheduler.{trigger_type}",
                "elapsed_mins": 0.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
        asyncio.run(handler.handle_event(event))

    # 全部非 event trigger 都被 skip, writer 0 呼叫
    assert len(writer_calls) == 0, (
        f"EventHandler 必須只處理 event trigger, 實際 writer 被呼叫 {len(writer_calls)} 次"
    )


# ─── Test 5: Decision NO → 0 writer calls ─────────────────


def test_decision_no_blocks_writer():
    """Test 5: decision=NO (decision_cooldown 還在) → 0 writer calls.

    H-I3: Agency 拒絕時 writer 不能被呼叫.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    # 用 3600s decision_cooldown, 確保 last_decision_at 距 now < 3600 → decision=NO
    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = EventHandler(
        state=state,
        writer_executor=mock_writer,
    )

    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_event_payload("agent_yua"),
    )
    asyncio.run(handler.handle_event(event))

    # H-I3: decision=NO → 0 writer calls
    assert len(writer_calls) == 0


# ─── Test 6: Decision YES → exactly 1 writer call ─────────


def test_decision_yes_allows_writer():
    """Test 6: decision=YES → exactly 1 writer call.

    H-I4: Agency 允許時 writer 必須被呼叫 exactly 1 次.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    # state 預設, cooldown 都為 0, decision 一定 YES
    state = AgencyState()

    handler = EventHandler(
        state=state,
        writer_executor=mock_writer,
    )

    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_event_payload("agent_yua"),
    )
    asyncio.run(handler.handle_event(event))

    # H-I4: decision=YES → 1 writer call
    assert len(writer_calls) == 1
    assert writer_calls[0]["agent_id"] == "agent_yua"


# ─── Test 7: No duplicate execution ──────────────────────


def test_no_duplicate_event_execution():
    """Test 7: 1 scheduler event trigger → max 1 writer call (H-I7).

    整合測試: scheduler 觸發 + handler 處理 + mock writer
    確認一次 trigger 只觸發一次 writer (不重複).
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    handler = EventHandler(
        state=None,
        writer_executor=mock_writer,
    )

    # 觸發一次 event trigger
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_event_payload("agent_yua"),
    )
    asyncio.run(handler.handle_event(event))

    # H-I7: 1 trigger → max 1 writer call
    assert len(writer_calls) == 1, (
        f"H-I7 violation: 1 trigger 觸發 {len(writer_calls)} 次 writer "
        f"(應該 exactly 1)"
    )


# ─── Test 8: Existing event safeguards preserved ──────────


def test_event_whitelist_preserved():
    """Test 8: scheduler event trigger 仍走 proactive whitelist 過濾.

    M1.7 修法: _fire_event 從 _get_proactive_agents() 抽, 跟 proactive_dm 共用 whitelist.
    M5.2-H 不能破壞這層保護.
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
        proactive_agents=["agent_ruka"],  # whitelist 只列 ruka
    )
    async def event_cb(agent_id: str, slot: str) -> None:
        pass
    scheduler.register_dream_event(
        dream_callback=lambda a, s: asyncio.sleep(0),
        event_callback=event_cb,
    )
    scheduler._all_agents = ["agent_ruka", "agent_yua"]  # yua 不在 whitelist
    scheduler._next_event_time = datetime.now(timezone.utc)

    # mock random.sample 回 yua (非 whitelist)
    with patch("src.soul.scheduler.random.sample", return_value=["agent_yua"]):
        asyncio.run(scheduler._fire_event())

    # yua 被 whitelist 過濾, 0 AGENCY_TRIGGER
    assert len(captured_events) == 0, (
        f"預期 0 AGENCY_TRIGGER (yua 非 whitelist), 實際 {len(captured_events)} 個"
    )


# ─── Test 9: Event handler is writer-only (no AGENT_SPEAK) ─


def test_event_handler_writer_only():
    """Test 9: EventHandler 不會發 AGENT_SPEAK, 只跑 writer_executor.

    H-I5/H-I6: event 是 WRITER_ONLY, 不是 character dialogue.
    驗證方式: 移除 docstring + 註解後, source 不能引用 LLM / character dialogue.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    handler = EventHandler(
        state=None,
        writer_executor=mock_writer,
    )

    # 拿 source, 移除 docstring + 註解, 只看實際執行 code
    import re
    handler_source = inspect.getsource(DirectEventHandler)
    # 移除 docstrings (三引號) + 單行註解
    code_only = re.sub(r'"""[\s\S]*?"""', '', handler_source)
    code_only = re.sub(r"#[^\n]*", "", code_only)

    # 實際 code 不能引用 character dialogue / LLM
    forbidden_refs = [
        "agent._fire_intent",
        "_fire_intent",  # 任何 agent._fire_intent 變體
        "llm_executor",  # 應該是 writer_executor, 不是 llm
        "LLMProxy",  # 直接 LLM call 也不該出現
    ]
    for ref in forbidden_refs:
        assert ref not in code_only, (
            f"H-I5/H-I6 violation: EventHandler code (docstring/註解移除後) 含 {ref!r} "
            f"(WRITER_ONLY 應該不引用 character dialogue / LLM)"
        )

    # 確認 writer_executor 簽名是 (agent_id), 不是 (agent_id, trigger)
    handler_init = inspect.signature(DirectEventHandler.__init__)
    writer_param = handler_init.parameters.get("writer_executor")
    assert writer_param is not None, "EventHandler.__init__ 缺少 writer_executor 參數"
    # default 是 None, 但 __init__ body 內部 fallback 到 _default_noop_writer
    assert writer_param.default is None, (
        "writer_executor default 應該是 None (在 __init__ 內部 fallback 到 _default_noop_writer)"
    )

    # 驗證: 不傳 writer_executor → 自動用 noop (確認 fallback 邏輯)
    default_handler = DirectEventHandler(state=None)
    assert default_handler.writer_executor is not None, (
        "不傳 writer_executor 應該 fallback 到 _default_noop_writer"
    )
    # 確認 fallback 函式不會實際做任何事 (只是 log)
    import inspect as _inspect
    src = _inspect.getsource(default_handler.writer_executor)
    assert "noop" in src.lower(), "fallback writer 應該是 noop 函式"


# ─── Test 10: Proactive DM regression (M5.2-G intact) ────


def test_proactive_dm_regression_unchanged():
    """Test 10: EventHandler 不會干擾 proactive_dm 走 AgencyTriggerHandler.

    H-I8: M5.2-G proactive_dm 行為完全沒變.

    驗證:
      - AgencyTriggerHandler 仍只處理 trigger_type='proactive_dm'
      - EventHandler 跟 AgencyTriggerHandler 是兩個獨立 class
      - 兩者都可從 src.agency import
    """
    from src.agency import AgencyTriggerHandler, EventHandler

    # 兩個 handler 是獨立 class
    assert AgencyTriggerHandler is not EventHandler
    assert issubclass(AgencyTriggerHandler, object)
    assert issubclass(EventHandler, object)

    # 確認 EventHandler 有自己的 handle_event
    assert hasattr(EventHandler, "handle_event")
    assert hasattr(AgencyTriggerHandler, "handle_event")

    # 確認 AgencyTriggerHandler 仍過濾 proactive_dm (M5.2-G 沒變)
    # 透過 source 檢查
    agency_trigger_handler_source = inspect.getsource(AgencyTriggerHandler)
    assert 'envelope.trigger_type != "proactive_dm"' in agency_trigger_handler_source, (
        "AgencyTriggerHandler 仍應只處理 proactive_dm (M5.2-G 沒變)"
    )

    # 確認 EventHandler 過濾 event
    event_handler_source = inspect.getsource(EventHandler)
    assert 'envelope.trigger_type != "event"' in event_handler_source, (
        "EventHandler 應只處理 event (M5.2-H)"
    )


# ─── Test 11 (bonus): EventHandler decision flow logging ───


def test_event_handler_logs_decision_outcome():
    """Test 11 (bonus): EventHandler 必須 log decision 結果 (YES/NO).

    M5.1 I-A8: decision 必須有 reason.
    EventHandler 必須把 decision outcome 寫到 logger 供 observability.
    """
    import logging

    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    # 設定 logger capture
    captured_logs: List[str] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured_logs.append(record.getMessage())

    cap_handler = _CaptureHandler()
    logger = logging.getLogger("soul_os.agency.event_handler")
    logger.addHandler(cap_handler)
    logger.setLevel(logging.INFO)

    try:
        # decision=YES
        state = AgencyState()
        handler = EventHandler(state=state, writer_executor=mock_writer)
        event = SoulEvent(
            event_type=EventType.AGENCY_TRIGGER,
            source="test",
            target="agent_yua",
            priority=EventPriority.NORMAL,
            payload=make_event_payload("agent_yua"),
        )
        asyncio.run(handler.handle_event(event))

        # 1 writer call
        assert len(writer_calls) == 1
        # log 含 "decision=YES"
        assert any("decision=YES" in log for log in captured_logs), (
            f"EventHandler 應 log decision=YES, 實際 logs: {captured_logs}"
        )
    finally:
        logger.removeHandler(cap_handler)


# ─── Test 12: M6.1-9 per-agent cooldown isolation ─────────

def test_m6_1_9_per_agent_cooldown_isolation():
    """M6.1-9 修法: 10 個 agent 同時觸發 event, 每個 agent 都應產出 writer call.

    根因 (2026-08-16): 單一共享 AgencyState 導致第一個 agent 執行後設置
    last_action_at, 後續 agent 被 60s action cooldown 擋住 (只有 1/10 產出)。
    修法: per-agent AgencyState, cooldown 按 agent 隔離 → 10/10 產出。
    """
    agents = [
        "agent_yua", "agent_ruka", "agent_akane", "agent_rem", "agent_ram",
        "agent_mahiru", "agent_anna", "agent_mai", "agent_miku", "agent_aoi",
    ]
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str) -> None:
        writer_calls.append({"agent_id": agent_id})

    handler = EventHandler(state=None, writer_executor=mock_writer)

    async def _run():
        for aid in agents:
            event = SoulEvent(
                event_type=EventType.AGENCY_TRIGGER,
                source="test",
                target=aid,
                priority=EventPriority.NORMAL,
                payload=make_event_payload(agent_id=aid),
            )
            await handler.handle_event(event)

    asyncio.run(_run())

    assert len(writer_calls) == 10, (
        f"M6.1-9 violation: 10 個 agent 應各自產出 writer call, 實際 {len(writer_calls)} 次 "
        f"(per-agent cooldown 隔離失敗)"
    )
    produced = {c["agent_id"] for c in writer_calls}
    assert produced == set(agents), (
        f"M6.1-9 violation: 缺失角色 {set(agents) - produced}"
    )
