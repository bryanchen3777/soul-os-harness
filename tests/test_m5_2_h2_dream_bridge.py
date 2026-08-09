"""
tests/test_m5_2_h2_dream_bridge.py — M5.2-H Phase 2

Bry 派工 2026-08-08 M5.2-H Phase 2:
  - dream scheduler trigger migration
  - _fire_dream 從 AGENCY_BYPASS → Agency 4 stages
  - WRITER_ONLY: DreamHandler 過 Agency decision → writer.write_dream (含 relationship side effect)
  - 13 required tests (Bry 工單 §10)
  - 0 production diff 之外的 commit / push

Strategy:
  - Scheduler publish AGENCY_TRIGGER (trigger_type="dream", extra={target_agent_id, all_agents})
  - 過渡期: callback 仍被呼叫 (noop in production)
  - DreamHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type=="dream", 跑 4 stages
  - decision=YES → dream_writer_executor(dreamer, target, all_agents) → writer.write_dream
  - decision=NO → 0 writer call, 0 relationship side effect
  - 1 trigger → max 1 writer execution (H2-I11)

Hard Invariants (per Bry 工單 §10):
  H2-I1   scheduler dream → AGENCY_TRIGGER
  H2-I2   trigger_type 必須為 dream
  H2-I3   target_agent_id 存於 extra
  H2-I4   DreamHandler 正確解析 target
  H2-I5   wrong trigger type ignored
  H2-I6   decision=NO → 0 writer calls
  H2-I7   decision=NO → 0 relationship mutations
  H2-I8   decision=YES → exactly 1 writer call
  H2-I9   writer 收到正確 dreamer + target
  H2-I10  relationship side effect preserved
  H2-I11  1 trigger → max 1 execution
  H2-I12  proactive/event regression preserved
  H2-I13  missing target_agent_id → reject safely
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency import (
    AgencyState,
    DreamHandler,
    TriggerEnvelope,
)
from src.agency.dream_handler import DreamHandler as DirectDreamHandler
from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, EventPriority, SoulEvent
from src.soul.scheduler import SoulScheduler


# ─── Helpers ───────────────────────────────────────────────


def make_now(seconds_offset: int = 0) -> datetime:
    base = datetime(2026, 8, 8, 15, 0, 0, tzinfo=timezone.utc)
    return base + timedelta(seconds=seconds_offset)


def make_dream_payload(
    agent_id: str = "agent_yua",
    target_agent_id: str = "agent_ruka",
    all_agents: List[str] = None,
) -> Dict[str, Any]:
    """模擬 scheduler _publish_agency_trigger(trigger_type='dream') 的 payload."""
    if all_agents is None:
        all_agents = ["agent_yua", "agent_ruka", "agent_akane"]
    return {
        "trigger_type": "dream",
        "agent_id": agent_id,
        "reason": "scheduler.dream",
        "elapsed_mins": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extra": {
            "target_agent_id": target_agent_id,
            "all_agents": all_agents,
        },
    }


# ─── Test H2-I1: scheduler dream → AGENCY_TRIGGER ──────────


def test_h2_i1_scheduler_publishes_agency_trigger_dream():
    """H2-I1: _fire_dream 走 AGENCY_TRIGGER (取代 AGENT_INTENT only)."""
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
        scheduler = SoulScheduler(bus=bus)
        async def dream_cb(agent_id: str, target: str) -> None:
            pass
        async def event_cb(agent_id: str, slot: str) -> None:
            pass
        scheduler.register_dream_event(dream_cb, event_cb)
        # 註冊 dreamer
        scheduler._all_agents = ["agent_yua", "agent_ruka"]
        # mock _pick_dream_target / _pick_dream_agents 確保 deterministic
        from src.soul import dream_event
        with patch.object(dream_event, "_pick_dream_agents", return_value=["agent_yua"]), \
             patch.object(dream_event, "_pick_dream_target", return_value="agent_ruka"):
            await scheduler._fire_dream(today="2026-08-08")

    asyncio.run(_run())
    # 至少 1 個 AGENCY_TRIGGER
    assert len(captured_events) >= 1, "_fire_dream 沒 publish AGENCY_TRIGGER"


# ─── Test H2-I2: trigger_type 必須為 dream ──────────────


def test_h2_i2_trigger_type_is_dream():
    """H2-I2: AGENCY_TRIGGER 的 payload.trigger_type 必須是 'dream'."""
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
        scheduler = SoulScheduler(bus=bus)
        async def dream_cb(agent_id: str, target: str) -> None:
            pass
        async def event_cb(agent_id: str, slot: str) -> None:
            pass
        scheduler.register_dream_event(dream_cb, event_cb)
        scheduler._all_agents = ["agent_yua", "agent_ruka"]
        from src.soul import dream_event
        with patch.object(dream_event, "_pick_dream_agents", return_value=["agent_yua"]), \
             patch.object(dream_event, "_pick_dream_target", return_value="agent_ruka"):
            await scheduler._fire_dream(today="2026-08-08")

    asyncio.run(_run())
    assert captured_events[0].payload["trigger_type"] == "dream"


# ─── Test H2-I3: target_agent_id 存於 extra ──────────────


def test_h2_i3_target_agent_id_in_extra():
    """H2-I3: target_agent_id 必須在 payload.extra 內 (C1: TriggerEnvelope frozen)."""
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
        scheduler = SoulScheduler(bus=bus)
        async def dream_cb(agent_id: str, target: str) -> None:
            pass
        async def event_cb(agent_id: str, slot: str) -> None:
            pass
        scheduler.register_dream_event(dream_cb, event_cb)
        scheduler._all_agents = ["agent_yua", "agent_ruka", "agent_akane"]
        from src.soul import dream_event
        with patch.object(dream_event, "_pick_dream_agents", return_value=["agent_yua"]), \
             patch.object(dream_event, "_pick_dream_target", return_value="agent_akane"):
            await scheduler._fire_dream(today="2026-08-08")

    asyncio.run(_run())
    extra = captured_events[0].payload["extra"]
    assert "target_agent_id" in extra
    assert extra["target_agent_id"] == "agent_akane"
    # all_agents 也應該在 extra (writer.write_dream 需要)
    assert "all_agents" in extra
    assert set(extra["all_agents"]) == {"agent_yua", "agent_ruka", "agent_akane"}


# ─── Test H2-I4: DreamHandler 正確解析 target ──────────


def test_h2_i4_dream_handler_parses_target_correctly():
    """H2-I4: DreamHandler.handle_event 從 envelope.extra 拿 target_agent_id."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(
        dreamer: str,
        target_agent_id: str,
        all_agents: List[str],
    ) -> None:
        writer_calls.append({
            "dreamer": dreamer,
            "target": target_agent_id,
            "all_agents": all_agents,
        })

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(
            agent_id="agent_yua",
            target_agent_id="agent_mahiru",
            all_agents=["agent_yua", "agent_mahiru", "agent_rem"],
        ),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 1
    assert writer_calls[0]["dreamer"] == "agent_yua"
    assert writer_calls[0]["target"] == "agent_mahiru"
    assert writer_calls[0]["all_agents"] == ["agent_yua", "agent_mahiru", "agent_rem"]


# ─── Test H2-I5: wrong trigger type ignored ──────────────


def test_h2_i5_wrong_trigger_type_ignored():
    """H2-I5: DreamHandler 只處理 trigger_type='dream', 其他 trigger 類型 skip."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer})

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

    for wrong_type in ["proactive_dm", "event", "morning", "night", "heartbeat"]:
        event = SoulEvent(
            event_type=EventType.AGENCY_TRIGGER,
            source="test",
            target="agent_yua",
            priority=EventPriority.NORMAL,
            payload={
                "trigger_type": wrong_type,
                "agent_id": "agent_yua",
                "reason": f"scheduler.{wrong_type}",
                "elapsed_mins": 0.0,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "extra": {
                    "target_agent_id": "agent_ruka",
                    "all_agents": ["agent_yua", "agent_ruka"],
                },
            },
        )
        asyncio.run(handler.handle_event(event))

    # 全部非 dream trigger 都被 skip
    assert len(writer_calls) == 0


# ─── Test H2-I6: decision=NO → 0 writer calls ──────────


def test_h2_i6_decision_no_blocks_writer():
    """H2-I6: decision=NO (decision_cooldown 還在) → 0 writer calls."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer})

    # 3600s decision_cooldown, 確保 last_decision_at 距 now < 3600 → decision=NO
    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = DreamHandler(state=state, dream_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 0


# ─── Test H2-I7: decision=NO → 0 relationship mutations ────


def test_h2_i7_decision_no_blocks_relationship_side_effect():
    """H2-I7: decision=NO → 0 relationship side effect (writer 0 calls, 內部 on_dream 不觸發).

    驗證策略: 透過 writer 呼叫次數推論 relationship side effect 次數,
    因為 writer.write_dream 內部 on_dream touch 是 1 次, 0 writer call = 0 on_dream。
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer, "target": target})

    # decision=NO state
    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = DreamHandler(state=state, dream_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(),
    )
    asyncio.run(handler.handle_event(event))

    # 0 writer calls → 0 relationship mutations (writer 內部 on_dream 不會被觸發)
    assert len(writer_calls) == 0


# ─── Test H2-I8: decision=YES → exactly 1 writer call ────


def test_h2_i8_decision_yes_allows_exactly_one_writer_call():
    """H2-I8: decision=YES → exactly 1 writer call."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer})

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 1


# ─── Test H2-I9: writer 收到正確 dreamer + target ────────


def test_h2_i9_writer_receives_correct_dreamer_target():
    """H2-I9: writer 收到的參數跟 envelope 完全對齊 (不重新 random)."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({
            "dreamer": dreamer,
            "target": target,
            "all_agents": all_agents,
        })

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

    # 故意給一組不太常見的 target
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_akane",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(
            agent_id="agent_akane",
            target_agent_id="agent_miku",
            all_agents=["agent_yua", "agent_ruka", "agent_akane", "agent_miku"],
        ),
    )
    asyncio.run(handler.handle_event(event))

    assert len(writer_calls) == 1
    call = writer_calls[0]
    # dreamer == envelope.agent_id
    assert call["dreamer"] == "agent_akane"
    # target == envelope.extra["target_agent_id"]
    assert call["target"] == "agent_miku"
    # all_agents == envelope.extra["all_agents"]
    assert call["all_agents"] == ["agent_yua", "agent_ruka", "agent_akane", "agent_miku"]


# ─── Test H2-I10: relationship side effect preserved ────


def test_h2_i10_relationship_side_effect_preserved():
    """H2-I10: relationship side effect 必須完整保留.

    驗證策略:
      - decision=YES → writer 被呼叫 1 次, 而 writer.write_dream 內部包含 on_dream touch
        (relationship side effect 是 writer 內部的事, handler 不該額外做)
      - handler source 確認不引用 relationships 模組 (避免繞過 writer 做第二次)
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer, "target": target})

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload=make_dream_payload(
            agent_id="agent_yua",
            target_agent_id="agent_ruka",
        ),
    )
    asyncio.run(handler.handle_event(event))

    # 1 writer call → writer 內部會做 1 次 on_dream touch
    assert len(writer_calls) == 1
    assert writer_calls[0]["dreamer"] == "agent_yua"
    assert writer_calls[0]["target"] == "agent_ruka"

    # 確認 handler 本身不繞過 writer 做 relationship 操作
    # 移除 docstring + 註解, 檢查實際 code
    handler_source = inspect.getsource(DirectDreamHandler)
    code_only = re.sub(r'"""[\s\S]*?"""', '', handler_source)
    code_only = re.sub(r"#[^\n]*", "", code_only)
    forbidden_refs = [
        "get_relationships_manager",  # handler 不該直接摸 relationships
        "on_dream",  # 關鍵字也禁止, 避免誤會
        "relationships",  # 任何 relationships 模組引用都禁止
    ]
    for ref in forbidden_refs:
        assert ref.lower() not in code_only.lower(), (
            f"H2-I10 violation: DreamHandler code 引用 {ref!r} "
            f"(relationship side effect 應由 writer 內部統一處理)"
        )


# ─── Test H2-I11: 1 trigger → max 1 execution ───────────


def test_h2_i11_no_duplicate_execution():
    """H2-I11: 1 scheduler dream trigger → max 1 writer call.

    防止 legacy callback + new handler 雙重執行。
    整合測試: scheduler 觸發 + handler 接收 + mock writer 確認只有 1 次 writer 呼叫。
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

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer, "target": target})

    async def _run():
        bus = _CapturingBus()
        scheduler = SoulScheduler(bus=bus)
        async def dream_cb(agent_id: str, target: str) -> None:
            # 過渡期 callback 仍被呼叫, 但 production 改 noop
            pass
        async def event_cb(agent_id: str, slot: str) -> None:
            pass
        scheduler.register_dream_event(dream_cb, event_cb)
        scheduler._all_agents = ["agent_yua", "agent_ruka"]

        # Wire DreamHandler
        handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

        # mock _pick_dream_* 強制 1 個 dreamer + 1 個 target
        from src.soul import dream_event
        with patch.object(dream_event, "_pick_dream_agents", return_value=["agent_yua"]), \
             patch.object(dream_event, "_pick_dream_target", return_value="agent_ruka"):
            await scheduler._fire_dream(today="2026-08-08")

        # 把 publish 出去的 AGENCY_TRIGGER 餵給 handler
        for event in captured_events:
            if event.event_type == EventType.AGENCY_TRIGGER:
                await handler.handle_event(event)

    asyncio.run(_run())
    # 1 個 dreamer, 1 個 target → 1 writer call
    assert len(writer_calls) == 1, (
        f"H2-I11 violation: 1 dream trigger 觸發 {len(writer_calls)} 次 writer "
        f"(應該 exactly 1, 防止 legacy callback + new handler 雙重執行)"
    )


# ─── Test H2-I12: proactive/event regression preserved ──


def test_h2_i12_proactive_event_regression_preserved():
    """H2-I12: M5.2-G (proactive_dm) + M5.2-H1 (event) 行為完全沒變."""
    from src.agency import (
        AgencyTriggerHandler,
        EventHandler,
        DreamHandler,
    )
    # 三個 handler 是獨立 class
    assert AgencyTriggerHandler is not EventHandler
    assert EventHandler is not DreamHandler
    assert AgencyTriggerHandler is not DreamHandler

    # 確認 AgencyTriggerHandler 仍過濾 proactive_dm
    agency_trigger_handler_source = inspect.getsource(AgencyTriggerHandler)
    assert 'envelope.trigger_type != "proactive_dm"' in agency_trigger_handler_source

    # 確認 EventHandler 仍過濾 event
    event_handler_source = inspect.getsource(EventHandler)
    assert 'envelope.trigger_type != "event"' in event_handler_source

    # 確認 DreamHandler 過濾 dream
    dream_handler_source = inspect.getsource(DreamHandler)
    assert 'envelope.trigger_type != "dream"' in dream_handler_source


# ─── Test H2-I13: missing target_agent_id → reject safely ──


def test_h2_i13_missing_target_agent_id_rejected_safely():
    """H2-I13: extra 缺 target_agent_id → handler 必須 reject safely, 0 writer call."""
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({"dreamer": dreamer, "target": target})

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

    # 構造 envelope 但 extra 沒 target_agent_id
    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload={
            "trigger_type": "dream",
            "agent_id": "agent_yua",
            "reason": "scheduler.dream",
            "elapsed_mins": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extra": {
                # 故意缺 target_agent_id
                "all_agents": ["agent_yua", "agent_ruka"],
            },
        },
    )
    asyncio.run(handler.handle_event(event))

    # 必須 0 writer call
    assert len(writer_calls) == 0, (
        f"H2-I13 violation: extra 缺 target_agent_id 還觸發 writer "
        f"(應 reject safely, 0 writer call)"
    )


# ─── Test H2 bonus: extra 缺 all_agents → fallback ──


def test_h2_bonus_missing_all_agents_falls_back_to_singleton():
    """H2 bonus: extra 缺 all_agents → handler 必須 fallback 到 [dreamer] 單元素 list.

    writer.write_dream 對 all_agents 是 list 用途, 缺不會 crash, 但語意上應該 fallback.
    """
    writer_calls: List[Dict[str, Any]] = []

    async def mock_writer(dreamer: str, target: str, all_agents: List[str]) -> None:
        writer_calls.append({
            "dreamer": dreamer,
            "target": target,
            "all_agents": all_agents,
        })

    handler = DreamHandler(state=None, dream_writer_executor=mock_writer)

    event = SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target="agent_yua",
        priority=EventPriority.NORMAL,
        payload={
            "trigger_type": "dream",
            "agent_id": "agent_yua",
            "reason": "scheduler.dream",
            "elapsed_mins": 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "extra": {
                "target_agent_id": "agent_ruka",
                # 故意缺 all_agents
            },
        },
    )
    asyncio.run(handler.handle_event(event))

    # 必須 1 writer call, all_agents fallback 到 [dreamer]
    assert len(writer_calls) == 1
    assert writer_calls[0]["dreamer"] == "agent_yua"
    assert writer_calls[0]["target"] == "agent_ruka"
    assert writer_calls[0]["all_agents"] == ["agent_yua"]
