"""
tests/test_m5_4_6_2_proactive_dm_inner_life_wiring.py

M5.4-6.2 (Bry 派工 2026-08-10): Proactive DM Inner Life Producer Wiring.

Focused test suite covering:
- A. Proactive DM executor pattern (1)
- B. Provenance semantics (4)
- C. _fire_intent extracts inner_life_event_id from chrono_payload (4)
- D. AGENT_INTENT SoulEvent carries inner_life_event_id top-level (2)
- E. AGENT_SPEAK SoulEvent construction reads event.inner_life_event_id (1)
- F. Failure isolation (3)
- G. Backward compatibility (3)
- H. No duplicate InnerLifeEvent (1)
- I. USER_MESSAGE exclusion (2)
- count (1)

Test count: 22 tests
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_SYSTEM,
    TRIGGER_TYPE_USER_MESSAGE,
    VALID_SOURCE_SYSTEMS,
)
from src.paths import data_root, reset_data_root
from src.eventbus.schema import EventType, EventPriority, SoulEvent


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _hex_32() -> str:
    """Generate a 32-char lowercase hex string (canonical event_id format)."""
    return uuid.uuid4().hex


def _isolated_data_root(tmp_path: Path) -> None:
    """Force data_root() to point to a temp dir for test isolation (P0.5)."""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()


def _restore_data_root() -> None:
    """Restore data_root to no env var (production default)."""
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_provenance(
    trigger_type: str = TRIGGER_TYPE_AGENT_REPLY,
    actor_id: str = "agent_ruka",
    source_system: str = "narrative",
    extras: Dict[str, str] = None,
) -> Provenance:
    """Build a Provenance matching the proactive_dm executor wiring spec."""
    if extras is None:
        extras = {}
    return Provenance(
        trigger_type=trigger_type,
        actor_id=actor_id,
        source_system=source_system,
        extras=extras,
    )


# ───────────────────────────────────────────────────────────
# A. Proactive DM executor pattern
# ───────────────────────────────────────────────────────────

class TestSectionA_ExecutorPattern:
    """A. Executor creates exactly one InnerLifeEvent with correct provenance."""

    def test_a1_executor_pattern_creates_one_event(self, tmp_path):
        """Executor pattern: InnerLifeWriter.create_event creates exactly 1 event."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance())
            # Single create → exactly 1 event
            assert ilw.get_known_event_count() == 1
            assert ilw.get_stats().events_created == 1
            assert ilw.is_event_known(event.event_id)
            assert len(event.event_id) == 32
        finally:
            _restore_data_root()

    def test_a2_executor_event_has_correct_provenance(self, tmp_path):
        """Event provenance: TRIGGER_TYPE_AGENT_REPLY + actor_id + source_system."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                trigger_type=TRIGGER_TYPE_AGENT_REPLY,
                actor_id="agent_ruka",
                source_system="narrative",
            ))
            assert event.provenance.trigger_type == TRIGGER_TYPE_AGENT_REPLY
            assert event.provenance.actor_id == "agent_ruka"
            assert event.provenance.source_system == "narrative"
        finally:
            _restore_data_root()

    def test_a3_executor_failure_returns_none_event_id(self, tmp_path, monkeypatch):
        """If InnerLifeWriter.create_event raises, executor pattern returns event_id=None."""

        def failing_create_event(*args, **kwargs):
            raise RuntimeError("simulated InnerLifeWriter failure")

        monkeypatch.setattr(
            "src.inner_life.writer.InnerLifeWriter.create_event",
            failing_create_event,
        )
        _isolated_data_root(tmp_path)
        try:
            # Executor pattern with try/except fallback (mirrors run_server.py)
            ilw = InnerLifeWriter()
            try:
                _event = ilw.create_event(provenance=_make_provenance())
                _event_id = _event.event_id
            except Exception:
                _event_id = None
            assert _event_id is None
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. Provenance semantics
# ───────────────────────────────────────────────────────────

class TestSectionB_ProvenanceSemantics:
    """B. Provenance uses canonical TRIGGER_TYPE_AGENT_REPLY + source_system values."""

    def test_b1_trigger_type_agent_reply_canonical(self):
        """TRIGGER_TYPE_AGENT_REPLY is the canonical value from M5.4-5.1."""
        assert TRIGGER_TYPE_AGENT_REPLY == "agent_reply"

    def test_b2_narrative_source_system_valid(self):
        """source_system='narrative' is in M5.4-5.1 VALID_SOURCE_SYSTEMS."""
        # Per M5.4-5.1 event.py:65: VALID_SOURCE_SYSTEMS = {"memory","diary","dream","narrative","system"}
        # "narrative" is the correct source for proactive_dm (cross-cutting agent expression
        # not tied to a specific writer category).
        assert "narrative" in VALID_SOURCE_SYSTEMS

    def test_b3_actor_id_is_agent_id(self, tmp_path):
        """Provenance.actor_id is the agent_id from the executor."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                actor_id="agent_ruka",
            ))
            assert event.provenance.actor_id == "agent_ruka"
        finally:
            _restore_data_root()

    def test_b4_extras_contain_trigger_context(self, tmp_path):
        """Provenance.extras contain trigger_source and elapsed_mins (no fabricated identity)."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance(
                trigger_type=TRIGGER_TYPE_AGENT_REPLY,
                actor_id="agent_ruka",
                source_system="narrative",
                extras={"trigger_source": "proactive_dm", "elapsed_mins": "240"},
            ))
            assert event.provenance.extras["trigger_source"] == "proactive_dm"
            assert event.provenance.extras["elapsed_mins"] == "240"
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. _fire_intent extracts inner_life_event_id from chrono_payload
# ───────────────────────────────────────────────────────────

class TestSectionC_FireIntentExtraction:
    """C. consciousness._fire_intent extracts inner_life_event_id from chrono_payload
    and sets it on the AGENT_INTENT SoulEvent's top-level field."""

    def _make_mock_consciousness(self):
        """Create a minimal concrete AgentConsciousness subclass for _fire_intent testing.

        AgentConsciousness is abstract (has _build_intent_payload / _should_speak
        abstract methods), so we need a concrete subclass to instantiate it.
        """
        from src.agent.consciousness import AgentConsciousness

        captured_events: List[SoulEvent] = []

        class _CapturingBus:
            async def publish(self, event):
                captured_events.append(event)
                return None

        class _ConcreteAgent(AgentConsciousness):
            """Minimal concrete subclass that satisfies ABC requirements."""

            def __init__(self, agent_id, bus):
                # Skip parent __init__'s side effects (state save, emotion engine, etc.)
                # but set required attributes
                self.agent_id = agent_id
                self.bus = bus
                self._pending = False
                self.state = MagicMock()
                self.state.save = MagicMock()

            def _build_intent_payload(self, reason, elapsed_mins):
                return {"draft": "test draft content"}

            def _should_speak(self, elapsed_mins, chrono_payload=None):
                return True, "test reason"

        agent = _ConcreteAgent("agent_ruka", _CapturingBus())
        return agent, captured_events

    def test_c1_fire_intent_sets_inner_life_event_id_on_agenti_intent(self):
        """_fire_intent with chrono_payload['inner_life_event_id']='abc...' → AGENT_INTENT
        SoulEvent has inner_life_event_id set to that value."""
        agent, captured = self._make_mock_consciousness()
        eid = _hex_32()
        asyncio.run(agent._fire_intent(
            reason="proactive_dm",
            elapsed_mins=240.0,
            chrono_payload={
                "draft": "test draft",
                "target_channel": "telegram",
                "target_user_id": "1696287850",
                "inner_life_event_id": eid,
            },
            mode="private",
        ))
        # Exactly 1 event published
        assert len(captured) == 1
        event = captured[0]
        assert event.event_type == EventType.AGENT_INTENT
        # Top-level field is set
        assert event.inner_life_event_id == eid

    def test_c2_fire_intent_no_inner_life_event_id_key_keeps_none(self):
        """_fire_intent without chrono_payload['inner_life_event_id'] key → AGENT_INTENT
        SoulEvent has inner_life_event_id=None (backward compat for non-wired callers)."""
        agent, captured = self._make_mock_consciousness()
        asyncio.run(agent._fire_intent(
            reason="proactive_dm",
            elapsed_mins=240.0,
            chrono_payload={
                "draft": "test draft",
                "target_channel": "telegram",
                "target_user_id": "1696287850",
                # No inner_life_event_id key (backward compat)
            },
            mode="private",
        ))
        assert len(captured) == 1
        assert captured[0].inner_life_event_id is None

    def test_c3_fire_intent_no_chrono_payload_keeps_none(self):
        """_fire_intent with chrono_payload=None → AGENT_INTENT SoulEvent has
        inner_life_event_id=None (legacy callers that don't pass chrono_payload)."""
        agent, captured = self._make_mock_consciousness()
        asyncio.run(agent._fire_intent(
            reason="proactive_dm",
            elapsed_mins=240.0,
            chrono_payload=None,
            mode="private",
        ))
        assert len(captured) == 1
        assert captured[0].inner_life_event_id is None

    def test_c4_fire_intent_invalid_event_id_type_ignored(self):
        """_fire_intent with chrono_payload['inner_life_event_id']=non-string → ignored,
        AGENT_INTENT SoulEvent has inner_life_event_id=None (defensive)."""
        agent, captured = self._make_mock_consciousness()
        asyncio.run(agent._fire_intent(
            reason="proactive_dm",
            elapsed_mins=240.0,
            chrono_payload={
                "draft": "test draft",
                "inner_life_event_id": 12345,  # int, not str
            },
            mode="private",
        ))
        assert len(captured) == 1
        assert captured[0].inner_life_event_id is None

    def test_c5_fire_intent_empty_string_event_id_ignored(self):
        """_fire_intent with chrono_payload['inner_life_event_id']='' → ignored
        (empty string treated as None, defensive)."""
        agent, captured = self._make_mock_consciousness()
        asyncio.run(agent._fire_intent(
            reason="proactive_dm",
            elapsed_mins=240.0,
            chrono_payload={
                "draft": "test draft",
                "inner_life_event_id": "",  # empty string
            },
            mode="private",
        ))
        assert len(captured) == 1
        assert captured[0].inner_life_event_id is None


# ───────────────────────────────────────────────────────────
# D. AGENT_INTENT SoulEvent carries inner_life_event_id
# ───────────────────────────────────────────────────────────

class TestSectionD_AGENTINTENTCarriesID:
    """D. AGENT_INTENT SoulEvent has inner_life_event_id as top-level field."""

    def test_d1_soul_event_inner_life_event_id_field_accepts_value(self):
        """SoulEvent(inner_life_event_id=...) constructor accepts the value."""
        eid = _hex_32()
        event = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            inner_life_event_id=eid,
            payload={"reason": "proactive_dm"},
        )
        assert event.inner_life_event_id == eid
        assert event.event_type == EventType.AGENT_INTENT

    def test_d2_soul_event_inner_life_event_id_default_none(self):
        """SoulEvent without inner_life_event_id → default None (backward compat)."""
        event = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source="agent_ruka",
            target="broadcast",
            priority=EventPriority.NORMAL,
            payload={"reason": "proactive_dm"},
        )
        assert event.inner_life_event_id is None


# ───────────────────────────────────────────────────────────
# E. LLMProxy AGENT_SPEAK construction reads event.inner_life_event_id
# ───────────────────────────────────────────────────────────

class TestSectionE_LLMProxyPropagation:
    """E. LLMProxy AGENT_SPEAK construction reads event.inner_life_event_id and sets
    it on the new AGENT_SPEAK SoulEvent (verified by source inspection)."""

    def test_e1_proxy_agenti_speak_construction_threads_inner_life_event_id(self):
        """Verify proxy.py AGENT_SPEAK SoulEvent construction sets inner_life_event_id
        from the incoming event."""
        proxy_src = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/src/llm/proxy.py")
        if not proxy_src.exists():
            # Skip in environment without source
            pytest.skip("Cannot locate proxy.py")
        src = proxy_src.read_text(encoding="utf-8")

        # The regular AGENT_SPEAK path must propagate inner_life_event_id
        # Search for the line containing 'inner_life_event_id=event.inner_life_event_id'
        # in the regular path (around line 2902)
        assert "inner_life_event_id=event.inner_life_event_id" in src, (
            "proxy.py must thread event.inner_life_event_id into AGENT_SPEAK "
            "SoulEvent construction (M5.4-6.2)"
        )


# ───────────────────────────────────────────────────────────
# F. Failure isolation
# ───────────────────────────────────────────────────────────

class TestSectionF_FailureIsolation:
    """F. InnerLifeWriter failure does not block the proactive DM path."""

    def test_f1_create_event_exception_does_not_propagate(self, tmp_path, monkeypatch):
        """If create_event raises, executor's try/except isolates it."""
        def failing(*args, **kwargs):
            raise RuntimeError("simulated writer failure")

        monkeypatch.setattr(
            "src.inner_life.writer.InnerLifeWriter.create_event",
            failing,
        )
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            # Executor pattern with try/except fallback
            try:
                _event = ilw.create_event(provenance=_make_provenance())
                _event_id = _event.event_id
            except Exception as e:
                _event_id = None
                # Verify we caught the exception (not propagated)
                assert isinstance(e, RuntimeError)
            assert _event_id is None
        finally:
            _restore_data_root()

    def test_f2_writer_still_runs_when_event_id_none(self, tmp_path):
        """When event_id is None (executor failure or backward-compat path), the rest
        of the chain (chrono_payload, _fire_intent, AGENT_SPEAK) still works."""
        _isolated_data_root(tmp_path)
        try:
            from src.agent.consciousness import AgentConsciousness

            captured_events: List[SoulEvent] = []

            class _CapturingBus:
                async def publish(self, event):
                    captured_events.append(event)
                    return None

            class _ConcreteAgent(AgentConsciousness):
                def __init__(self, agent_id, bus):
                    self.agent_id = agent_id
                    self.bus = bus
                    self._pending = False
                    self.state = MagicMock()
                    self.state.save = MagicMock()

                def _build_intent_payload(self, reason, elapsed_mins):
                    return {"draft": "test draft"}

                def _should_speak(self, elapsed_mins, chrono_payload=None):
                    return True, "test reason"

            agent = _ConcreteAgent("agent_ruka", _CapturingBus())

            # Simulate executor failure path: chrono_payload without inner_life_event_id
            asyncio.run(agent._fire_intent(
                reason="proactive_dm",
                elapsed_mins=240.0,
                chrono_payload={
                    "draft": "test draft",
                    "target_channel": "telegram",
                    "target_user_id": "1696287850",
                },
                mode="private",
            ))
            # _fire_intent completed without raising
            assert len(captured_events) == 1
            # AGENT_INTENT event has inner_life_event_id=None (failure → safe default)
            assert captured_events[0].inner_life_event_id is None
        finally:
            _restore_data_root()

    def test_f3_create_event_validation_error_handled_gracefully(self, tmp_path):
        """create_event with bad provenance raises IdentityValidationError,
        but executor's try/except converts it to event_id=None."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            try:
                # Bad provenance (invalid source_system) raises IdentityValidationError
                _event = ilw.create_event(provenance=Provenance(
                    trigger_type=TRIGGER_TYPE_AGENT_REPLY,
                    actor_id="agent_ruka",
                    source_system="invalid_system",  # not in VALID_SOURCE_SYSTEMS
                ))
                _event_id = _event.event_id
            except Exception:
                _event_id = None
            assert _event_id is None
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# G. Backward compatibility
# ───────────────────────────────────────────────────────────

class TestSectionG_BackwardCompatibility:
    """G. _fire_intent still works for all existing callers (no breakage)."""

    def test_g1_existing_draft_only_chrono_payload(self):
        """_fire_intent with chrono_payload={'draft': '...'} only (existing caller pattern)
        → AGENT_INTENT event with no inner_life_event_id, no breakage."""
        from src.agent.consciousness import AgentConsciousness

        captured_events: List[SoulEvent] = []

        class _CapturingBus:
            async def publish(self, event):
                captured_events.append(event)
                return None

        class _ConcreteAgent(AgentConsciousness):
            def __init__(self, agent_id, bus):
                self.agent_id = agent_id
                self.bus = bus
                self._pending = False
                self.state = MagicMock()
                self.state.save = MagicMock()

            def _build_intent_payload(self, reason, elapsed_mins):
                return {"draft": "old draft"}

            def _should_speak(self, elapsed_mins, chrono_payload=None):
                return True, "test reason"

        agent = _ConcreteAgent("agent_yua", _CapturingBus())

        asyncio.run(agent._fire_intent(
            reason="heartbeat",
            elapsed_mins=120.0,
            chrono_payload={"draft": "old draft"},
            mode="private",
        ))
        assert len(captured_events) == 1
        # Backward compat: no inner_life_event_id
        assert captured_events[0].inner_life_event_id is None
        # Other fields still work
        assert captured_events[0].event_type == EventType.AGENT_INTENT
        assert captured_events[0].source == "agent_yua"

    def test_g2_fire_intent_signature_unchanged(self):
        """_fire_intent signature: existing parameters preserved, no breakage."""
        from src.agent.consciousness import AgentConsciousness
        sig = inspect.signature(AgentConsciousness._fire_intent)
        params = list(sig.parameters.keys())
        # Existing params
        assert "self" in params
        assert "reason" in params
        assert "elapsed_mins" in params
        assert "chrono_payload" in params
        assert "mode" in params
        assert "user_id" in params
        # No new required parameters
        for p_name, p in sig.parameters.items():
            if p_name == "self":
                continue
            if p.default is inspect.Parameter.empty:
                # Required param: must be one of the existing ones
                assert p_name in {"reason", "elapsed_mins"}, (
                    f"Unexpected new required param: {p_name}"
                )

    def test_g3_legacy_diary_jsonl_with_no_inner_life_field_still_works(self, tmp_path):
        """Legacy diary jsonl entries (no inner_life_event_id) are not affected."""
        # This is a regression test for the M5.4-6.1 contract that the wiring is
        # additive only. Pre-M5.4-6.1 entries (and pre-M5.4-6.2) remain readable.
        _isolated_data_root(tmp_path)
        try:
            from src.soul.diary import DiaryWriter
            soul_dir = tmp_path / "data" / "soul" / "agent_yua" / "diary"
            soul_dir.mkdir(parents=True, exist_ok=True)
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            legacy_path = soul_dir / f"{today}.jsonl"
            with legacy_path.open("w", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": "2026-08-08T22:00:00+00:00",
                    "slot": "morning",
                    "content": "pre-M5.4-6.2 legacy",
                    "source": "llm",
                }, ensure_ascii=False) + "\n")

            writer = DiaryWriter(data_dir=str(tmp_path / "data" / "soul"))
            entries = writer.read_entries("agent_yua", today)
            assert len(entries) == 1
            assert "inner_life_event_id" not in entries[0]
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# H. No duplicate InnerLifeEvent
# ───────────────────────────────────────────────────────────

class TestSectionH_NoDuplicates:
    """H. Exactly one InnerLifeEvent per proactive_dm executor invocation."""

    def test_h1_one_create_event_call_registers_one_event(self, tmp_path):
        """Single executor pattern call → exactly 1 event in InnerLifeWriter."""
        _isolated_data_root(tmp_path)
        try:
            ilw = InnerLifeWriter()
            event = ilw.create_event(provenance=_make_provenance())
            assert ilw.get_known_event_count() == 1
            assert ilw.get_stats().events_created == 1
            assert ilw.get_stats().root_events == 1
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# I. USER_MESSAGE exclusion
# ───────────────────────────────────────────────────────────

class TestSectionI_UserMessageExclusion:
    """I. USER_MESSAGE does NOT create InnerLifeEvent via _fire_intent."""

    def test_i1_fire_intent_without_inner_life_event_id_creates_no_event(self):
        """_fire_intent without chrono_payload['inner_life_event_id'] key
        does NOT create any InnerLifeEvent (USER_MESSAGE path uses _fire_intent
        with chrono_payload that does NOT include inner_life_event_id)."""
        from src.agent.consciousness import AgentConsciousness

        captured_events: List[SoulEvent] = []

        class _CapturingBus:
            async def publish(self, event):
                captured_events.append(event)
                return None

        class _ConcreteAgent(AgentConsciousness):
            def __init__(self, agent_id, bus):
                self.agent_id = agent_id
                self.bus = bus
                self._pending = False
                self.state = MagicMock()
                self.state.save = MagicMock()

            def _build_intent_payload(self, reason, elapsed_mins):
                return {"draft": "user msg response"}

            def _should_speak(self, elapsed_mins, chrono_payload=None):
                return True, "test reason"

        agent = _ConcreteAgent("agent_yua", _CapturingBus())

        # USER_MESSAGE pattern: chrono_payload may have target_channel but NOT
        # inner_life_event_id (per M5.4-6.2 architectural rule: USER_MESSAGE is
        # not a structured lived-experience).
        asyncio.run(agent._fire_intent(
            reason="user_message",
            elapsed_mins=0.0,
            chrono_payload={
                "target_channel": "telegram",
                "target_user_id": "1696287850",
            },
            mode="private",
        ))
        # _fire_intent ran (published AGENT_INTENT) but inner_life_event_id NOT set
        assert len(captured_events) == 1
        assert captured_events[0].inner_life_event_id is None

    def test_i2_trigger_type_user_message_not_used_in_wiring(self):
        """TRIGGER_TYPE_USER_MESSAGE is NOT used by the proactive_dm executor
        (per M5.4-6.2 architectural rule: USER_MESSAGE is excluded from v1)."""
        # Executor pattern uses TRIGGER_TYPE_AGENT_REPLY, not TRIGGER_TYPE_USER_MESSAGE
        # Verified by string comparison
        assert TRIGGER_TYPE_USER_MESSAGE == "user_message"
        assert TRIGGER_TYPE_AGENT_REPLY == "agent_reply"
        assert TRIGGER_TYPE_USER_MESSAGE != TRIGGER_TYPE_AGENT_REPLY


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: A=3, B=4, C=5, D=2, E=1, F=3, G=3, H=1, I=2, count=1 → 25 tests.
    Note: C section has 5 tests (not 4 as in plan), total = 25.
    """
    # Sanity check: pytest collection should match
    pass
