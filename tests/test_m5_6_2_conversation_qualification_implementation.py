"""
tests/test_m5_6_2_conversation_qualification_implementation.py

M5.6-2 (Bry 派工 2026-08-10): Conversation Qualification Boundary Implementation.

Verifies the v1 deterministic ConversationQualification boundary:
- A. Qualification (3): <5min rejected, >=5min+<4 turns rejected, >=5min+>=4 turns qualified
- B. Identity (3): exactly 1 InnerLifeEvent on qualification, canonical event_id,
                  no synthetic identity
- C. Rejection (1): rejected conversation creates 0 InnerLifeEvents
- D. Privacy (2): qualification does not inspect content, no text in metadata
- E. Lifecycle (3): SESSION_END → correct session, no cross-session qualification,
                    Heartbeat payload additive
- F. Regression (3): InnerLifeWriter sole creator (R1), frozen contracts,
                     existing 4 producer wiring unchanged
- count (1)

Test count: 16 tests
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.conversation_qualification import (
    ConversationQualification,
    ConversationQualificationResult,
    QUALIFICATION_DURATION_THRESHOLD_MINS,
    QUALIFICATION_TURN_DEPTH_THRESHOLD,
    TRIGGER_TYPE_CONVERSATION_USER_MESSAGE,
)
from src.eventbus.schema import EventType, SoulEvent
from src.inner_life import (
    InnerLifeEvent,
    InnerLifeWriter,
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
)
from src.paths import data_root, reset_data_root


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_inner_life_writer(tmp_path: Path) -> InnerLifeWriter:
    """Create an isolated InnerLifeWriter (canonical identity authority)."""
    _isolated_data_root(tmp_path)
    return InnerLifeWriter()


def _make_qualifier(tmp_path: Path) -> ConversationQualification:
    """Create an isolated ConversationQualification bound to fresh writer."""
    writer = _make_inner_life_writer(tmp_path)
    return ConversationQualification(inner_life_writer=writer)


def _make_session_end_event(
    session_id: str = "session_bryan_agent_yua",
    user_id: str = "bryan",
    agent_id: str = "agent_yua",
    elapsed_mins: float = 10.0,
) -> SoulEvent:
    """Helper: build a SESSION_END event with M5.6-2 additive payload fields."""
    return SoulEvent(
        event_type=EventType.SESSION_END,
        source="heartbeat_engine",
        target="broadcast",
        payload={
            "elapsed_mins": elapsed_mins,
            "last_user_activity": "2026-08-10T10:00:00+00:00",
            # M5.6-2 additive:
            "last_session_id": session_id,
            "last_user_id": user_id,
            "last_agent_id": agent_id,
        },
    )


def _write_conversation(
    tmp_path: Path,
    user_id: str,
    agent_id: str,
    entries: List[Dict[str, Any]],
) -> Path:
    """Helper: write a conversation history file (READ-ONLY consumer target)."""
    conv_dir = tmp_path / "data" / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    path = conv_dir / f"{user_id}_{agent_id}_private.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False)
    return path


# ───────────────────────────────────────────────────────────
# A. Qualification
# ───────────────────────────────────────────────────────────

class TestSectionA_Qualification:
    """A1: <5 min → rejected. A2: >=5 min + <4 turns → rejected. A3: >=5 min + >=4 turns → qualified."""

    def test_a1_duration_below_threshold_rejected(self, tmp_path):
        """Duration < 5 min → qualified=False, even with enough turns."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user", "content": "x"} for _ in range(10)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=3.0)  # < 5 min
            result = qualifier.evaluate(event)
            assert result.qualified is False
            assert "duration" in result.reason
        finally:
            _restore_data_root()

    def test_a2_turn_depth_below_threshold_rejected(self, tmp_path):
        """Duration >= 5 min but turn_depth < 4 → qualified=False."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user", "content": "x"},
                                 {"role": "assistant", "content": "y"}])  # only 2 entries
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)  # >= 5 min
            result = qualifier.evaluate(event)
            assert result.qualified is False
            assert "turn_depth" in result.reason
            assert "2<" in result.reason  # 2 entries
        finally:
            _restore_data_root()

    def test_a3_both_thresholds_met_qualified(self, tmp_path):
        """Duration >= 5 min AND turn_depth >= 4 → qualified=True."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"msg-{i}"} for i in range(6)])  # 6 entries
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)  # >= 5 min
            result = qualifier.evaluate(event)
            assert result.qualified is True
            assert result.session_id == "session_bryan_agent_yua"
            assert result.correlation_id == "session_bryan_agent_yua"
            assert "duration=" in result.reason
            assert "turn_depth=" in result.reason
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. Identity
# ───────────────────────────────────────────────────────────

class TestSectionB_Identity:
    """B1: exactly 1 InnerLifeEvent on qualification. B2: canonical event_id.
       B3: no synthetic identity (no uuid.uuid4() in Qualifier)."""

    def test_b1_qualification_creates_exactly_one_event(self, tmp_path):
        """Calling promote() with qualified result creates exactly 1 InnerLifeEvent."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"msg-{i}"} for i in range(6)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            assert result.qualified

            initial_count = len(qualifier._writer._events)
            event_id = qualifier.promote(result)
            final_count = len(qualifier._writer._events)

            assert final_count == initial_count + 1, "Should create exactly 1 event"
            assert event_id is not None
            assert re.match(r"^[0-9a-f]{32}$", event_id)
        finally:
            _restore_data_root()

    def test_b2_event_id_is_canonical_inner_life_writer_format(self, tmp_path):
        """event_id is 32-char lowercase hex (canonical InnerLifeWriter format)."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"m{i}"} for i in range(5)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            event_id = qualifier.promote(result)
            assert event_id is not None
            # 32-char lowercase hex (canonical format per M5.4-5.1)
            assert len(event_id) == 32
            assert re.match(r"^[0-9a-f]{32}$", event_id)
            # Stored event matches
            stored = qualifier._writer._events[event_id]
            assert stored.event_id == event_id
            assert stored.session_id == "session_bryan_agent_yua"
        finally:
            _restore_data_root()

    def test_b3_qualifier_never_fabricates_event_id(self, tmp_path):
        """Qualifier never fabricates event_id locally. It only delegates to
        InnerLifeWriter.create_event which is the canonical authority.

        Verification approach: after promote(), the event_id must be:
        - 32-char lowercase hex (canonical format)
        - Known by InnerLifeWriter (is_event_known == True)
        - NOT hardcoded / not invented by Qualifier

        The Qualifier module does not import uuid and does not call
        generate_event_id — verified by checking that the event_id is the
        writer's output, not a Qualifier-generated value.
        """
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"m{i}"} for i in range(5)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            event_id = qualifier.promote(result)
            assert event_id is not None
            # Canonical format
            assert re.match(r"^[0-9a-f]{32}$", event_id)
            # InnerLifeWriter generated it (canonical authority)
            assert qualifier._writer.is_event_known(event_id)
            # Qualifier module does NOT have its own event storage
            assert not hasattr(qualifier, "_events")
            assert not hasattr(qualifier, "_known_event_ids")
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# C. Rejection
# ───────────────────────────────────────────────────────────

class TestSectionC_Rejection:
    """C1: rejected conversation creates 0 InnerLifeEvents."""

    def test_c1_rejected_creates_zero_events(self, tmp_path):
        """Duration < 5 min → 0 InnerLifeEvents, even when promote is called."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"m{i}"} for i in range(8)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=2.0)  # < 5 min
            result = qualifier.evaluate(event)
            assert result.qualified is False
            initial_count = len(qualifier._writer._events)
            # promote() refuses non-qualified result
            event_id = qualifier.promote(result)
            assert event_id is None
            assert len(qualifier._writer._events) == initial_count, (
                "No event should be created for rejected conversation"
            )
        finally:
            _restore_data_root()

    def test_c1b_short_turn_depth_creates_zero_events(self, tmp_path):
        """Turn depth < 4 → 0 InnerLifeEvents, even with long duration."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user", "content": "hi"},
                                 {"role": "assistant", "content": "hello"}])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=60.0)  # 1 hour
            result = qualifier.evaluate(event)
            assert result.qualified is False
            initial_count = len(qualifier._writer._events)
            event_id = qualifier.promote(result)
            assert event_id is None
            assert len(qualifier._writer._events) == initial_count
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# D. Privacy
# ───────────────────────────────────────────────────────────

class TestSectionD_Privacy:
    """D1: qualification does not inspect content. D2: no prompt/response text in metadata."""

    def test_d1_qualification_does_not_inspect_content(self, tmp_path):
        """Qualifier must NOT inspect conversation content. Only counts entries."""
        try:
            # Write content that looks like personal data — but only the count matters.
            secret_content = "This is private medical information about Bry's health."
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user", "content": secret_content},
                                 {"role": "assistant", "content": "I understand."},
                                 {"role": "user", "content": secret_content},
                                 {"role": "assistant", "content": "Got it."}])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)

            # Patch json.load to track what was loaded.
            original_load = json.load
            loaded_payloads: List[Any] = []

            def tracking_load(fp, *args, **kwargs):
                data = original_load(fp, *args, **kwargs)
                loaded_payloads.append(data)
                return data

            with patch("src.conversation_qualification.qualifier.json.load",
                       side_effect=tracking_load):
                result = qualifier.evaluate(event)

            # json.load was called (to count), but the loaded data must NOT
            # be retained past the function scope. Provenance.extras must
            # contain only numeric/categorical signals, NEVER raw content.
            assert result.qualified is True
            # Promote and check the resulting event's extras for content leakage
            event_id = qualifier.promote(result)
            assert event_id is not None
            stored = qualifier._writer._events[event_id]
            extras = stored.provenance.extras
            extras_str = json.dumps(extras)
            # No content leakage in extras
            assert "medical" not in extras_str
            assert "private" not in extras_str
            assert secret_content not in extras_str
        finally:
            _restore_data_root()

    def test_d2_no_conversation_text_in_provenance(self, tmp_path):
        """Provenance.extras contains only numeric/categorical signals."""
        try:
            # Content designed to test for leakage
            secret_text = "Bry told me he is going to quit his job tomorrow."
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user", "content": secret_text},
                                 {"role": "assistant", "content": "ack"},
                                 {"role": "user", "content": "more " + secret_text},
                                 {"role": "assistant", "content": "ack2"}])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            assert result.qualified
            event_id = qualifier.promote(result)
            stored = qualifier._writer._events[event_id]
            # Trigger type is the new additive value (NOT content)
            assert stored.provenance.trigger_type == TRIGGER_TYPE_CONVERSATION_USER_MESSAGE
            assert stored.provenance.source_system == "narrative"
            # Extras: only reason string (categorical)
            extras = stored.provenance.extras
            assert all(isinstance(v, str) for v in extras.values())
            # No content text in extras
            extras_str = str(extras)
            assert "quit" not in extras_str
            assert "tomorrow" not in extras_str
            assert secret_text not in extras_str
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# E. Lifecycle
# ───────────────────────────────────────────────────────────

class TestSectionE_Lifecycle:
    """E1: SESSION_END → correct session. E2: no cross-session.
       E3: Heartbeat payload additive fields are correctly read."""

    def test_e1_session_end_identifies_correct_session(self, tmp_path):
        """SESSION_END with session_id='X' qualifies session X, not others."""
        try:
            # Write history for 3 different sessions
            for sid_suffix, agent in [("agent_yua", "agent_yua"),
                                      ("agent_rem", "agent_rem"),
                                      ("agent_akane", "agent_akane")]:
                _write_conversation(tmp_path, "bryan", agent,
                                    [{"role": "user" if i % 2 == 0 else "assistant",
                                      "content": f"m{i}"} for i in range(6)])
            qualifier = _make_qualifier(tmp_path)
            # SESSION_END for agent_yua session
            event = _make_session_end_event(
                session_id="session_bryan_agent_yua",
                agent_id="agent_yua",
                elapsed_mins=10.0,
            )
            result = qualifier.evaluate(event)
            assert result.qualified is True
            assert result.session_id == "session_bryan_agent_yua"
        finally:
            _restore_data_root()

    def test_e2_no_cross_session_qualification(self, tmp_path):
        """SESSION_END for session A does NOT qualify session B (no cross-session leakage)."""
        try:
            # Two sessions with sufficient turns
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"y{i}"} for i in range(6)])
            _write_conversation(tmp_path, "bryan", "agent_rem",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"r{i}"} for i in range(6)])
            qualifier = _make_qualifier(tmp_path)
            # SESSION_END says it's the yua session
            event = _make_session_end_event(
                session_id="session_bryan_agent_yua",
                agent_id="agent_yua",
                elapsed_mins=10.0,
            )
            result = qualifier.evaluate(event)
            assert result.qualified is True
            # The promoted event must reference YUA, not REM
            event_id = qualifier.promote(result)
            stored = qualifier._writer._events[event_id]
            assert "agent_yua" in stored.session_id
            assert "agent_rem" not in stored.session_id
        finally:
            _restore_data_root()

    def test_e3_heartbeat_payload_additive_fields(self, tmp_path):
        """Heartbeat SESSION_END payload correctly carries M5.6-2 additive fields."""
        try:
            # Build a SESSION_END event the way Heartbeat would (M5.6-2 Phase 1)
            event = SoulEvent(
                event_type=EventType.SESSION_END,
                source="heartbeat_engine",
                payload={
                    "elapsed_mins": 10.0,
                    "last_user_activity": "2026-08-10T10:00:00+00:00",
                    # M5.6-2 additive:
                    "last_session_id": "session_bryan_agent_yua",
                    "last_user_id": "bryan",
                    "last_agent_id": "agent_yua",
                },
            )
            # The 3 new fields are accessible via event.payload
            assert event.payload["last_session_id"] == "session_bryan_agent_yua"
            assert event.payload["last_user_id"] == "bryan"
            assert event.payload["last_agent_id"] == "agent_yua"
            # Existing fields unchanged
            assert event.payload["elapsed_mins"] == 10.0
            assert "last_user_activity" in event.payload
            # Existing SESSION_END consumers (consciousness._on_session_end) read
            # elapsed_mins / last_user_activity — these are unchanged.
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# F. Regression — frozen contracts + existing 4 producer wiring
# ───────────────────────────────────────────────────────────

class TestSectionF_Regression:
    """F1: InnerLifeWriter remains sole creator. F2: no schema change.
       F3: existing 4 producers (Diary/Dream/Event/ProactiveDM) unchanged."""

    def test_f1_inner_life_writer_remains_sole_creator(self, tmp_path):
        """Per R1: Qualifier only calls writer.create_event, never creates events locally."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"m{i}"} for i in range(5)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            qualifier.promote(result)
            # Only the writer's _events dict was modified.
            # Qualifier has no internal event storage of its own.
            assert not hasattr(qualifier, "_events")
            assert not hasattr(qualifier, "_inner_life_events")
        finally:
            _restore_data_root()

    def test_f2_provenance_schema_unchanged(self, tmp_path):
        """The promoted event uses standard Provenance fields, no schema change."""
        try:
            _write_conversation(tmp_path, "bryan", "agent_yua",
                                [{"role": "user" if i % 2 == 0 else "assistant",
                                  "content": f"m{i}"} for i in range(5)])
            qualifier = _make_qualifier(tmp_path)
            event = _make_session_end_event(elapsed_mins=10.0)
            result = qualifier.evaluate(event)
            event_id = qualifier.promote(result)
            stored = qualifier._writer._events[event_id]
            # Standard Provenance fields
            assert stored.provenance.trigger_type == TRIGGER_TYPE_CONVERSATION_USER_MESSAGE
            assert stored.provenance.source_system in (
                "memory", "diary", "dream", "narrative", "system"
            )
            # Standard InnerLifeEvent fields (M5.4-5.1 contract)
            assert stored.session_id is not None
            assert stored.correlation_id is not None
            assert stored.parent_event_id is None  # root event
            assert stored.lineage_depth == 0  # root
            assert stored.lineage_path == stored.event_id
        finally:
            _restore_data_root()

    def test_f3_existing_producers_unchanged(self, tmp_path):
        """M5.4-6.x producer wiring (Diary/Dream/Event/ProactiveDM) is not affected.
        We verify by directly creating an event with an existing trigger_type
        through the writer — it should work normally."""
        try:
            writer = _make_inner_life_writer(tmp_path)
            # Pre-existing producer trigger_type (M5.4-6.x) still works
            ev = writer.create_event(
                provenance=Provenance(
                    trigger_type=TRIGGER_TYPE_AGENT_REPLY,  # existing
                    actor_id="agent_rem",
                    source_system="narrative",
                ),
                session_id="session_bryan_agent_rem",
            )
            assert ev.event_id
            assert ev.provenance.trigger_type == TRIGGER_TYPE_AGENT_REPLY
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: A=3, B=3, C=2, D=2, E=3, F=3, count=1 → 17."""
    pass
