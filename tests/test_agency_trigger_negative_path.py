"""
tests/test_agency_trigger_negative_path.py — M5.2-Q-2 Negative-Path Contract Hardening

Q-1 inventory 證明 production handler 的 negative-path 行為已存在 (見 Q-1-3 audit),
此 file 把既有行為正式變成 regression contract。

Scope (per Bry 派工 Q-2):
  - 4 handler × unknown trigger_type  (4 tests)
  - 4 handler × missing agent_id     (4 tests)
  - 4 handler × missing trigger_type (4 tests)
  - proactive_dm / event / morning / night × extra={} acceptance (4 tests)
  - event × decision=NO (1 test)

NOT in Q-2 scope (Bry 派工 Q-2 明確):
  - Dream × missing extra.target_agent_id → 已有 test_m5_2_h2_dream_bridge.test_h2_i13_missing_target_agent_id_rejected_safely
  - agency.py Stage 1-4 (M5.2-A 凍結)
  - TriggerEnvelope schema (M5.2-F 凍結)
  - 5 frozen trigger types
  - scheduler cooldown / quiet-hours / state
  - _callbacks
  - heartbeat
  - production handlers
  - O-2 / O-3 migration
  - 13 frozen v1 tests
"""
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.agency.agency import AgencyState
from src.agency.diary_handler import DiaryHandler
from src.agency.dream_handler import DreamHandler
from src.agency.event_handler import EventHandler
from src.agency.trigger_handler import AgencyTriggerHandler
from src.eventbus.schema import EventPriority, EventType, SoulEvent


# ─── Helpers ─────────────────────────────────────────────


def make_event(payload_overrides=None, trigger_type="proactive_dm", agent_id="agent_ruka", extra=None):
    """構造 AGENCY_TRIGGER event payload。"""
    payload = {
        "trigger_type": trigger_type,
        "agent_id": agent_id,
        "reason": "scheduler." + trigger_type,
        "elapsed_mins": 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "extra": extra if extra is not None else {},
    }
    if payload_overrides:
        payload.update(payload_overrides)
    return SoulEvent(
        event_type=EventType.AGENCY_TRIGGER,
        source="test",
        target=agent_id,
        priority=EventPriority.NORMAL,
        payload=payload,
    )


# ─── Q-2-1: 4 handler × unknown trigger_type ───────────────


def test_ath_unknown_trigger_type_rejected():
    """Q-2-1: AgencyTriggerHandler 收到非 proactive_dm 的 AGENCY_TRIGGER → 不 invoke executor"""
    captured = []

    async def mock_llm(agent_id, trigger):
        captured.append(agent_id)

    handler = AgencyTriggerHandler(llm_executor=mock_llm)
    event = make_event(trigger_type="unknown_type_xyz")

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "unknown trigger_type 不應 invoke llm_executor, got " + repr(captured)
    )


def test_eh_unknown_trigger_type_rejected():
    """Q-2-1: EventHandler 收到非 event 的 AGENCY_TRIGGER → 不 invoke writer"""
    captured = []

    async def mock_writer(agent_id):
        captured.append(agent_id)

    handler = EventHandler(writer_executor=mock_writer)
    event = make_event(trigger_type="unknown_type_xyz")

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "unknown trigger_type 不應 invoke writer_executor, got " + repr(captured)
    )


def test_dh_unknown_trigger_type_rejected():
    """Q-2-1: DreamHandler 收到非 dream 的 AGENCY_TRIGGER → 不 invoke dream writer"""
    captured = []

    async def mock_dream_writer(dreamer, target, all_agents):
        captured.append(dreamer)

    handler = DreamHandler(dream_writer_executor=mock_dream_writer)
    event = make_event(trigger_type="unknown_type_xyz")

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "unknown trigger_type 不應 invoke dream_writer_executor, got " + repr(captured)
    )


def test_dih_unknown_trigger_type_rejected():
    """Q-2-1: DiaryHandler 收到非 morning/night 的 AGENCY_TRIGGER → 不 invoke diary writer"""
    captured = []

    async def mock_diary_writer(agent_id, slot):
        captured.append(agent_id)

    handler = DiaryHandler(diary_writer_executor=mock_diary_writer)
    event = make_event(trigger_type="unknown_type_xyz")

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "unknown trigger_type 不應 invoke diary_writer_executor, got " + repr(captured)
    )


# ─── Q-2-2: 4 handler × missing agent_id ───────────────────


def test_ath_missing_agent_id_rejected():
    """Q-2-2: AgencyTriggerHandler 收到 missing agent_id → _parse_envelope return None → 不 invoke"""
    captured = []

    async def mock_llm(agent_id, trigger):
        captured.append(agent_id)

    handler = AgencyTriggerHandler(llm_executor=mock_llm)
    # 構造 missing agent_id (None, _parse_envelope 內 isinstance(agent_id, str) 會 fail)
    event = make_event(payload_overrides={"agent_id": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing agent_id 不應 invoke llm_executor, got " + repr(captured)
    )


def test_eh_missing_agent_id_rejected():
    """Q-2-2: EventHandler 收到 missing agent_id → 不 invoke writer"""
    captured = []

    async def mock_writer(agent_id):
        captured.append(agent_id)

    handler = EventHandler(writer_executor=mock_writer)
    event = make_event(payload_overrides={"agent_id": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing agent_id 不應 invoke writer_executor, got " + repr(captured)
    )


def test_dh_missing_agent_id_rejected():
    """Q-2-2: DreamHandler 收到 missing agent_id → 不 invoke dream writer"""
    captured = []

    async def mock_dream_writer(dreamer, target, all_agents):
        captured.append(dreamer)

    handler = DreamHandler(dream_writer_executor=mock_dream_writer)
    event = make_event(payload_overrides={"agent_id": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing agent_id 不應 invoke dream_writer_executor, got " + repr(captured)
    )


def test_dih_missing_agent_id_rejected():
    """Q-2-2: DiaryHandler 收到 missing agent_id → 不 invoke diary writer"""
    captured = []

    async def mock_diary_writer(agent_id, slot):
        captured.append(agent_id)

    handler = DiaryHandler(diary_writer_executor=mock_diary_writer)
    event = make_event(payload_overrides={"agent_id": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing agent_id 不應 invoke diary_writer_executor, got " + repr(captured)
    )


# ─── Q-2-3: 4 handler × missing trigger_type ────────────────


def test_ath_missing_trigger_type_rejected():
    """Q-2-3: AgencyTriggerHandler 收到 missing trigger_type → _parse_envelope return None → 不 invoke"""
    captured = []

    async def mock_llm(agent_id, trigger):
        captured.append(agent_id)

    handler = AgencyTriggerHandler(llm_executor=mock_llm)
    event = make_event(payload_overrides={"trigger_type": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing trigger_type 不應 invoke llm_executor, got " + repr(captured)
    )


def test_eh_missing_trigger_type_rejected():
    """Q-2-3: EventHandler 收到 missing trigger_type → 不 invoke writer"""
    captured = []

    async def mock_writer(agent_id):
        captured.append(agent_id)

    handler = EventHandler(writer_executor=mock_writer)
    event = make_event(payload_overrides={"trigger_type": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing trigger_type 不應 invoke writer_executor, got " + repr(captured)
    )


def test_dh_missing_trigger_type_rejected():
    """Q-2-3: DreamHandler 收到 missing trigger_type → 不 invoke dream writer"""
    captured = []

    async def mock_dream_writer(dreamer, target, all_agents):
        captured.append(dreamer)

    handler = DreamHandler(dream_writer_executor=mock_dream_writer)
    event = make_event(payload_overrides={"trigger_type": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing trigger_type 不應 invoke dream_writer_executor, got " + repr(captured)
    )


def test_dih_missing_trigger_type_rejected():
    """Q-2-3: DiaryHandler 收到 missing trigger_type → 不 invoke diary writer"""
    captured = []

    async def mock_diary_writer(agent_id, slot):
        captured.append(agent_id)

    handler = DiaryHandler(diary_writer_executor=mock_diary_writer)
    event = make_event(payload_overrides={"trigger_type": None})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "missing trigger_type 不應 invoke diary_writer_executor, got " + repr(captured)
    )


# ─── Q-2-4: 4 trigger × extra={} acceptance ──────────────────


def test_proactive_dm_accepts_empty_extra():
    """Q-2-4: proactive_dm trigger 接受 extra={} (proactive_dm 不需 extra context)"""
    captured = []

    async def mock_llm(agent_id, trigger):
        captured.append({"agent_id": agent_id, "extra": dict(trigger.extra)})

    handler = AgencyTriggerHandler(llm_executor=mock_llm)
    event = make_event(trigger_type="proactive_dm", extra={})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 1
    assert captured[0]["extra"] == {}


def test_event_accepts_empty_extra():
    """Q-2-4: event trigger 接受 extra={}"""
    captured = []

    async def mock_writer(agent_id):
        captured.append(agent_id)

    handler = EventHandler(writer_executor=mock_writer)
    event = make_event(trigger_type="event", extra={})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 1, (
        "event trigger extra={} 應正常 invoke writer, got " + repr(captured)
    )


def test_morning_accepts_empty_extra():
    """Q-2-4: morning trigger 接受 extra={}"""
    captured = []

    async def mock_diary_writer(agent_id, slot):
        captured.append({"agent_id": agent_id, "slot": slot})

    handler = DiaryHandler(diary_writer_executor=mock_diary_writer)
    event = make_event(trigger_type="morning", extra={})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 1
    assert captured[0]["slot"] == "morning"


def test_night_accepts_empty_extra():
    """Q-2-4: night trigger 接受 extra={}"""
    captured = []

    async def mock_diary_writer(agent_id, slot):
        captured.append({"agent_id": agent_id, "slot": slot})

    handler = DiaryHandler(diary_writer_executor=mock_diary_writer)
    event = make_event(trigger_type="night", extra={})

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 1
    assert captured[0]["slot"] == "night"


# ─── Q-2-5: event × decision=NO ────────────────────────────


def test_event_decision_no_blocks_writer():
    """Q-2-5: event trigger decision=NO (decision_cooldown 還在) → 0 writer calls

    補 event trigger decision=NO coverage。
    proactive_dm: test_m5_2_g Test 6 已涵蓋
    dream: test_m5_2_h2 H2-I6 已涵蓋
    morning: test_m5_2_h3 H3-I4 已涵蓋
    night: test_m5_2_h3 H3-I5 已涵蓋
    event: 本 test 補完
    """
    captured = []

    async def mock_writer(agent_id):
        captured.append(agent_id)

    # 用 fresh AgencyState + decision_cooldown 1h, last_decision_at = now → decision=NO
    state = AgencyState(decision_cooldown_seconds=3600)
    state.last_decision_at = datetime.now(timezone.utc)

    handler = EventHandler(state=state, writer_executor=mock_writer)
    event = make_event(trigger_type="event")

    asyncio.run(handler.handle_event(event))
    assert len(captured) == 0, (
        "event decision=NO 不應 invoke writer_executor, got " + repr(captured)
    )


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
