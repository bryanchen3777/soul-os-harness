"""
tests/test_m5_2_h3_diary_bridge.py — M5.2-H Phase 3

Bry 派工 2026-08-08 M5.2-H Phase 3:
  - morning + night scheduler trigger migration
  - _fire_all("morning") + _fire_all("night") 從 AGENCY_BYPASS → Agency 4 stages
  - 一個 DiaryHandler 同時負責 morning + night
  - writer_executor(agent_id, slot) delegate 回既有 diary callback
  - 12 required tests (Bry 工單 §必測 Invariants)
  - 0 production diff 之外的 commit / push

Strategy:
  - Scheduler _fire_all 仍做 registered agent iteration + dedup
  - publish AGENCY_TRIGGER (trigger_type=slot)
  - 過渡期: callback 仍被呼叫 (noop in production) 為向後相容
  - DiaryHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type ∈ {morning, night}, 跑 4 stages
  - decision=YES → diary_writer_executor(agent_id, slot) → 既有 callback (1 call)
  - decision=NO → 0 diary call, 0 LLM call
  - 1 trigger → max 1 real diary execution (H3-I11)

Hard Invariants (per Bry 工單 §必測 Invariants):
  H3-I1   morning → AGENCY_TRIGGER
  H3-I2   night → AGENCY_TRIGGER
  H3-I3   wrong trigger type ignored
  H3-I4   morning decision=NO → 0 execution
  H3-I5   night decision=NO → 0 execution
  H3-I6   morning YES → exactly 1 diary
  H3-I7   night YES → exactly 1 diary
  H3-I8   dedup preserved
  H3-I9   registered-agent iteration preserved
  H3-I10  proactive/event/dream regression
  H3-I11  morning/night callback cannot double execute
  H3-I12  heartbeat remains untouched
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency import (
    AgencyState,
    DiaryHandler,
    SUPPORTED_DIARY_SLOTS,
    TriggerEnvelope,
)
from src.agency.diary_handler import DiaryHandler as DirectDiaryHandler
from src.eventbus.schema import EventType, EventPriority, SoulEvent
from src.soul.scheduler import SoulScheduler


# ─── Helpers ───────────────────────────────────────────────




def make_diary_payload(agent_id: str = "agent_yua", slot: str = "morning") -> Dict[str, Any]:
    """模擬 scheduler _publish_agency_trigger(trigger_type=slot) 的 payload."""
    return {
        "trigger_type": slot,
        "agent_id": agent_id,
        "reason": f"scheduler.{slot}",
        "elapsed_mins": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extra": {},
    }


def make_recording_scheduler(
    agents: List[str]
):
    """建一個 scheduler 並註冊 agents (R-3 起不再註冊 callback)。"""
    scheduler = SoulScheduler()
    for aid in agents:
        scheduler.register(aid)
    return scheduler


# ─── Test H3-I1: morning → AGENCY_TRIGGER ──────────────


def test_h3_i1_morning_publishes_agency_trigger():
    """H3-I1: _fire_all('morning') 走 AGENCY_TRIGGER (取代 AGENT_INTENT only)."""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    async def _run():
        bus = _CapturingBus()
        scheduler = make_recording_scheduler(["agent_yua", "agent_ruka"])
        scheduler._bus = bus
        await scheduler._fire_all("morning", today="2026-08-08")

    asyncio.run(_run())
    assert len(captured_events) == 2  # 2 agents
    for event in captured_events:
        assert event.payload["trigger_type"] == "morning"
        assert event.payload["reason"] == "scheduler.morning"


# ─── Test H3-I2: night → AGENCY_TRIGGER ────────────────


def test_h3_i2_night_publishes_agency_trigger():
    """H3-I2: _fire_all('night') 走 AGENCY_TRIGGER."""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    async def _run():
        bus = _CapturingBus()
        scheduler = make_recording_scheduler(["agent_yua", "agent_ruka"])
        scheduler._bus = bus
        await scheduler._fire_all("night", today="2026-08-08")

    asyncio.run(_run())
    assert len(captured_events) == 2
    for event in captured_events:
        assert event.payload["trigger_type"] == "night"
        assert event.payload["reason"] == "scheduler.night"


# ─── Test H3-I3: wrong trigger type ignored ───────────


def test_h3_i3_wrong_trigger_type_ignored():
    """H3-I3: DiaryHandler 只處理 trigger_type ∈ {morning, night}, 其他 trigger 類型 skip."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    handler = DiaryHandler(state=None, diary_writer_executor=mock_writer)

    for wrong_type in ["proactive_dm", "event", "dream", "heartbeat"]:
        event = SoulEvent(
            event_type=EventType.AGENCY_TRIGGER,
            source="test",
            target="agent_yua",
            priority=EventPriority.NORMAL,
            payload=make_diary_payload(agent_id="agent_yua", slot=wrong_type),
        )
        asyncio.run(handler.handle_event(event))

    # 全部非 morning/night trigger 都被 skip
    assert len(writer_calls) == 0


# ─── Test H3-I4: morning decision=NO → 0 execution ────────


def test_h3_i4_morning_decision_no_blocks_writer():
    """H3-I4: morning decision=NO (decision_cooldown 還在) → 0 writer calls."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    # 3600s decision_cooldown, 確保 last_decision_at 距 now < 3600 → decision=NO
    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = DiaryHandler(state=state, diary_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_diary_payload(agent_id="agent_yua", slot="morning"),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 0


# ─── Test H3-I5: night decision=NO → 0 execution ──────────


def test_h3_i5_night_decision_no_blocks_writer():
    """H3-I5: night decision=NO → 0 writer calls."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = DiaryHandler(state=state, diary_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_diary_payload(agent_id="agent_yua", slot="night"),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 0


# ─── Test H3-I6: morning YES → exactly 1 diary ──────────


def test_h3_i6_morning_decision_yes_allows_exactly_one_writer_call():
    """H3-I6: morning decision=YES → exactly 1 writer call."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    handler = DiaryHandler(state=None, diary_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_diary_payload(agent_id="agent_yua", slot="morning"),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 1
    assert writer_calls[0]["agent_id"] == "agent_yua"
    assert writer_calls[0]["slot"] == "morning"


# ─── Test H3-I7: night YES → exactly 1 diary ────────────


def test_h3_i7_night_decision_yes_allows_exactly_one_writer_call():
    """H3-I7: night decision=YES → exactly 1 writer call."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    handler = DiaryHandler(state=None, diary_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_diary_payload(agent_id="agent_yua", slot="night"),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 1
    assert writer_calls[0]["slot"] == "night"


# ─── Test H3-I8: dedup preserved ───────────────────────


def test_h3_i8_dedup_preserved():
    """H3-I8: 同一日同 slot 二次觸發 _fire_all → 只 1 次 trigger published (dedup 仍生效)."""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    async def _run():
        bus = _CapturingBus()
        scheduler = make_recording_scheduler(["agent_yua", "agent_ruka"])
        scheduler._bus = bus

        # 第一次 morning
        await scheduler._fire_all("morning", today="2026-08-08")
        first_count = len(captured_events)

        # 同一天再 morning → dedup 應讓 trigger 數不增加
        await scheduler._fire_all("morning", today="2026-08-08")
        second_count = len(captured_events)

        assert first_count == 2  # 2 agents
        assert second_count == 2, (
            f"H3-I8 violation: 同日同 slot 二次 _fire_all 應該 dedup, "
            f"但 trigger 從 {first_count} 變 {second_count}"
        )

    asyncio.run(_run())


# ─── Test H3-I9: registered-agent iteration preserved ───


def test_h3_i9_registered_agent_iteration_preserved():
    """H3-I9: 所有註冊 agent 都要收到 trigger (iteration 完整保留)."""
    captured_events: List[SoulEvent] = []

    class _CapturingBus:
        async def publish(self, event: SoulEvent) -> None:
            if event.event_type == EventType.AGENCY_TRIGGER:
                captured_events.append(event)
        async def start(self) -> None:
            pass
        async def stop(self) -> None:
            pass

    async def _run():
        bus = _CapturingBus()
        agents = ["agent_yua", "agent_ruka", "agent_akane", "agent_rem"]
        scheduler = make_recording_scheduler(agents)
        scheduler._bus = bus

        await scheduler._fire_all("morning", today="2026-08-08")

        # 每個 agent 都應該收到 1 個 trigger
        triggered_agents = {e.payload["agent_id"] for e in captured_events}
        assert triggered_agents == set(agents), (
            f"H3-I9 violation: 預期全部 {len(agents)} 隻 agent 收到 trigger, "
            f"實際 {len(triggered_agents)} 隻: {triggered_agents}"
        )

    asyncio.run(_run())


# ─── Test H3-I10: proactive/event/dream regression ────


def test_h3_i10_other_handlers_regression_preserved():
    """H3-I10: M5.2-G (proactive_dm) + M5.2-H1 (event) + M5.2-H2 (dream) 行為完全沒變."""
    from src.agency import (
        AgencyTriggerHandler,
        EventHandler,
        DreamHandler,
        DiaryHandler,
    )
    # 四個 handler 是獨立 class
    assert AgencyTriggerHandler is not EventHandler
    assert EventHandler is not DreamHandler
    assert DreamHandler is not DiaryHandler
    assert AgencyTriggerHandler is not DiaryHandler

    # 確認每個 handler 仍過濾自己的 trigger_type
    sources = {
        "AgencyTriggerHandler": inspect.getsource(AgencyTriggerHandler),
        "EventHandler": inspect.getsource(EventHandler),
        "DreamHandler": inspect.getsource(DreamHandler),
        "DiaryHandler": inspect.getsource(DiaryHandler),
    }
    filters = {
        "AgencyTriggerHandler": '"proactive_dm"',
        "EventHandler": '"event"',
        "DreamHandler": '"dream"',
        "DiaryHandler": '"morning", "night"',
    }
    for cls_name, filter_str in filters.items():
        assert filter_str in sources[cls_name], (
            f"{cls_name} 應包含 trigger_type filter {filter_str!r}"
        )

    # 確認 SUPPORTED_DIARY_SLOTS 是 (morning, night)
    assert SUPPORTED_DIARY_SLOTS == ("morning", "night")


# ─── Test H3-I11: morning/night callback cannot double execute ──


def test_h3_i11_callback_cannot_double_execute():
    """H3-I11: 1 trigger → max 1 real diary execution (防止 legacy callback + handler 雙重執行).

    整合測試: scheduler _fire_all + DiaryHandler + mock writer
    確認一次 _fire_all 只觸發一次真實 writer call。
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

    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(agent_id: str, slot: str) -> None:
        writer_calls.append({"agent_id": agent_id, "slot": slot})

    async def _run():
        bus = _CapturingBus()
        scheduler = make_recording_scheduler(["agent_yua"])
        scheduler._bus = bus

        # Wire DiaryHandler
        handler = DiaryHandler(state=None, diary_writer_executor=mock_writer)

        # 1. 觸發 scheduler _fire_all("morning")
        await scheduler._fire_all("morning", today="2026-08-08")

        # 2. 把 published AGENCY_TRIGGER 餵給 handler
        for event in captured_events:
            if event.event_type == EventType.AGENCY_TRIGGER:
                await handler.handle_event(event)

    asyncio.run(_run())
    # 1 agent + 1 slot → 1 writer call (不是 2: scheduler noop + handler 1 = 1)
    assert len(writer_calls) == 1, (
        f"H3-I11 violation: 1 trigger 觸發 {len(writer_calls)} 次 writer "
        f"(應該 exactly 1, 防止 legacy callback + handler 雙重執行)"
    )
    assert writer_calls[0]["agent_id"] == "agent_yua"
    assert writer_calls[0]["slot"] == "morning"


# ─── Test H3-I12: heartbeat remains untouched ────────────


def test_h3_i12_heartbeat_remains_untouched():
    """H3-I12: heartbeat 仍 suspended, 沒被 Phase 3 動到."""
    # 驗證 scheduler.py 內 heartbeat 實作仍存在但 production 不 register
    scheduler_source = inspect.getsource(SoulScheduler)
    # _fire_heartbeat 應仍存在 (Bry 8/6 派工「保留在 scheduler 內部」)
    assert "async def _fire_heartbeat" in scheduler_source, (
        "H3-I12 violation: _fire_heartbeat 應仍存在 (Bry 派工: 保留在 scheduler 內部)"
    )
    # _heartbeat_callback 屬性應仍存在
    assert "_heartbeat_callback" in scheduler_source, (
        "H3-I12 violation: _heartbeat_callback 屬性應仍存在"
    )

    # 驗證 DiaryHandler source 不含 heartbeat 相關引用
    diary_handler_source = inspect.getsource(DirectDiaryHandler)
    code_only = re.sub(r'"""[\s\S]*?"""', '', diary_handler_source)
    code_only = re.sub(r"#[^\n]*", "", code_only)
    assert "heartbeat" not in code_only.lower(), (
        "H3-I12 violation: DiaryHandler 不應引用 heartbeat (suspended, 不 migrate)"
    )


# ─── Test H3 bonus: decision=NO → 0 LLM call (source-level) ──


def test_h3_bonus_decision_no_blocks_llm_source_check():
    """H3 bonus: decision=NO → 0 LLM call.

    驗證策略: DiaryHandler source 移除 docstring + 註解後, 沒有任何 LLM 相關引用
    (decision=NO 時 handler 直接 return, 不會 invoke writer_executor → 不會觸發 LLM)
    """
    handler_source = inspect.getsource(DirectDiaryHandler)
    code_only = re.sub(r'"""[\s\S]*?"""', '', handler_source)
    code_only = re.sub(r"#[^\n]*", "", code_only)

    # 實際 code 不能引用 LLM (Handler 不該自己調 LLM, 只 delegate 給 callback)
    forbidden_refs = [
        "minimax",  # 不該直接 hit LLM API
        "openai",  # 同上
        "llama_cpp",  # 本地 LLM
        "anthropic",  # Claude
        "generate_diary_entry",  # Handler 不該自己跑 diary generation
    ]
    for ref in forbidden_refs:
        assert ref not in code_only.lower(), (
            f"H3 bonus violation: DiaryHandler code 引用 {ref!r} "
            f"(Handler 只負責 delegate, 不該自己跑 LLM / diary generation)"
        )

    # 確認 trigger-only path 在 decision=NO 時直接 skip (不 invoke writer)
    # 透過 source pattern 確認 "should_act" 是 if 條件
    assert "if result.decision.should_act:" in code_only, (
        "DiaryHandler 應有 if should_act: gate, decision=NO 時不 invoke writer"
    )
