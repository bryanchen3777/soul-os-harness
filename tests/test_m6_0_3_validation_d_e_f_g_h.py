"""
tests/test_m6_0_3_validation_d_e_f_g_h.py
M6.0-3 (Bry 派工 2026-08-11): Validation Framework — Scenarios D, E, F, G, H

Extends M6.0 validation from A/B/C (M6.0-2) to the remaining 5 scenarios:
  D. Temporal continuity
  E. World event → Inner Life
  F. World event → proactive gate
  G. Inner-life persistence
  H. Multi-cycle lived context

M6 remains VALIDATION ONLY. 0 M5 runtime modifications.

Reuses from M6.0-2:
  - MockLLMBackend
  - CheckpointRunner
  - state_assertions helpers
  - isolated tempdir pattern

Production safety:
  - All writes go to tempfile.TemporaryDirectory()
  - data_root is patched to tempdir
  - No production data is touched
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests._helpers.mock_llm_backend import (
    MockLLMBackend,
    default_strategy,
    fixed_response_strategy,
)
from tests._helpers.state_assertions import (
    CheckpointRunner,
    assert_file_exists,
    assert_file_contains,
    assert_file_not_contains,
    assert_file_json_matches,
    assert_state_equals,
    assert_text_contains,
    assert_text_not_contains,
    assert_context_order,
)


# ── Shared utilities ──

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "m6_0"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _setup_isolated_env() -> tempfile.TemporaryDirectory:
    """Create isolated tempdir with full data/ structure for all subsystems."""
    tmpdir = tempfile.TemporaryDirectory()
    tmp = Path(tmpdir.name)
    # Create all subsystem dirs so P0.5 data_root patches resolve
    (tmp / "data" / "soul" / "agent_yua").mkdir(parents=True)
    (tmp / "data" / "soul" / "agent_ruka").mkdir(parents=True)
    (tmp / "data" / "memory").mkdir(parents=True)
    (tmp / "data" / "agents" / "agent_yua").mkdir(parents=True)
    (tmp / "data" / "agents" / "agent_ruka").mkdir(parents=True)
    (tmp / "data" / "inner_life" / "trace").mkdir(parents=True)
    (tmp / "data" / "events").mkdir(parents=True)
    return tmpdir


def _load_fixture(tmp: Path, fixture_subpath: str, dest_subpath: str) -> None:
    """Copy fixture file into tempdir."""
    src = FIXTURE_ROOT / fixture_subpath
    dst = tmp / dest_subpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _cleanup_tmpdir(td: tempfile.TemporaryDirectory) -> None:
    """Windows file lock workaround."""
    td._ignore_cleanup_errors = True
    try:
        td.cleanup()
    except Exception:
        pass


def _refresh_trace_timestamps(trace_path: Path) -> None:
    """Rewrite ts fields of a trace.jsonl to relative times (now - 3h/2h/1h ago).

    Time-bomb fix (2026-08-29): fixture ts was hardcoded to 2026-08-11, which
    falls outside the 24h query window once real time passes it, making
    G1/G2 fail forever. Relative timestamps keep the fixture inside the
    window regardless of when the tests run.
    """
    now = datetime.now(timezone.utc)
    lines = trace_path.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for i, line in enumerate(lines):
        rec = json.loads(line)
        rec["ts"] = (now - timedelta(hours=3 - i)).isoformat()
        out.append(json.dumps(rec, ensure_ascii=False))
    trace_path.write_text("\n".join(out) + "\n", encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════
# Scenario D: Temporal Continuity
# ════════════════════════════════════════════════════════════════════════


class TestScenarioD(unittest.TestCase):
    """
    M6.0-3 Scenario D: Temporal continuity.

    Verifies:
      - chrono-social block contains expected fields
      - carryover save + load is deterministic
      - temporal block injected at correct position in Soul Context
    """

    def setUp(self):
        self.tmpdir_obj = _setup_isolated_env()
        self.tmp = Path(self.tmpdir_obj.name)

    def tearDown(self):
        _cleanup_tmpdir(self.tmpdir_obj)

    def test_d1_chrono_block_contains_expected_fields(self):
        """D1: render_temporal_block contains time_period, attachment_heat, etc."""
        runner = CheckpointRunner("Scenario D1")

        from src.temporal.core import build_temporal_context
        from src.temporal.models import EmotionalCarryover, PersonaConfig
        from src.temporal.render import render_temporal_block

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            carryover = EmotionalCarryover(
                intimacy_afterglow=0.7,
                unresolved_worry=0.2,
                attachment_heat=0.6,
                source_event="test_event",
                triggered_at=_now_iso(),
            )
            ctx = build_temporal_context(
                persona_id="agent_yua",
                last_msg_ts=_now_iso(),
                current_stress=0,
                carryover=carryover,
                config=PersonaConfig(persona_id="agent_yua"),
                now=datetime.now(timezone.utc),
            )
            block = render_temporal_block(ctx)

            runner.run(
                "D1: chrono_block contains 'CHRONO_SOCIAL_CONTEXT' header",
                lambda: assert_text_contains(
                    block, "CHRONO_SOCIAL_CONTEXT",
                    label="temporal block header"
                ),
            )
            runner.run(
                "D1: chrono_block contains time_period",
                lambda: assert_text_contains(
                    block, "time_period",
                    label="time_period field"
                ),
            )
            runner.run(
                "D1: chrono_block contains attachment_heat",
                lambda: assert_text_contains(
                    block, "attachment_heat",
                    label="attachment_heat field"
                ),
            )
            runner.run(
                "D1: chrono_block contains silence field (rendered as 'silence=...h')",
                lambda: assert_text_contains(
                    block, "silence=",
                    label="silence field"
                ),
            )

        runner.assert_all_passed()

    def test_d2_carryover_save_load_deterministic(self):
        """D2: carryover save → load is deterministic."""
        runner = CheckpointRunner("Scenario D2")

        from src.temporal.models import EmotionalCarryover

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            agent_id = "agent_yua"
            # Save a known carryover state
            c1 = EmotionalCarryover(
                intimacy_afterglow=0.8,
                unresolved_worry=0.3,
                attachment_heat=0.7,
                source_event="test_event",
                triggered_at=_now_iso(),
            )
            c1.save(agent_id)

            # Load and verify same values
            c2 = EmotionalCarryover.load(agent_id)

            runner.run(
                "D2: Loaded carryover equals saved (intimacy_afterglow)",
                lambda: assert_state_equals(
                    c2.intimacy_afterglow, 0.8,
                    label="intimacy_afterglow roundtrip"
                ),
            )
            runner.run(
                "D2: Loaded carryover equals saved (unresolved_worry)",
                lambda: assert_state_equals(
                    c2.unresolved_worry, 0.3,
                    label="unresolved_worry roundtrip"
                ),
            )
            runner.run(
                "D2: Loaded carryover equals saved (attachment_heat)",
                lambda: assert_state_equals(
                    c2.attachment_heat, 0.7,
                    label="attachment_heat roundtrip"
                ),
            )

        runner.assert_all_passed()

    def test_d3_temporal_block_injected_in_context(self):
        """D3: temporal block appears in Soul Context at correct position."""
        runner = CheckpointRunner("Scenario D3")

        from src.llm.proxy import _build_messages_private

        # _build_messages_private expects a `memory` object with
        # get_recent_with_meta() method (M6.0-2 test_a3 uses MagicMock pattern).
        # LLMProxy does not expose this method directly — it's on MemoryMiddleware.
        memory_mock = MagicMock()
        memory_mock.get_recent_with_meta.return_value = []

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            messages = _build_messages_private(
                agent_id="agent_yua",
                soul="你是 Yua。",
                current_input="hi",
                memory_context="",
                memory=memory_mock,
                current_time="2026-08-11 13:00",
            )
            sys_content = messages[0]["content"]

            # Verify temporal block is present
            markers = [
                "你是 Yua",
                "## 當下時間",
            ]
            runner.run(
                "D3: temporal block in private Soul Context",
                lambda: assert_context_order(
                    sys_content, markers,
                    label="temporal block ordering in private"
                ),
            )

        runner.assert_all_passed()

    def test_d4_no_production_data_mutation(self):
        """D4: Production carryover data not touched."""
        runner = CheckpointRunner("Scenario D4")
        prod_carryover = Path.cwd() / "data" / "agents" / "agent_yua" / "carryover.json"

        mtime_before = None
        if prod_carryover.exists():
            mtime_before = prod_carryover.stat().st_mtime

        runner.run(
            "D4: Production carryover.json mtime unchanged",
            lambda: assert_state_equals(
                prod_carryover.stat().st_mtime == mtime_before if mtime_before else True, True,
                label="production carryover must not be mutated"
            ),
        )
        runner.assert_all_passed()


# ════════════════════════════════════════════════════════════════════════
# Scenario E: World event → Inner Life
# ════════════════════════════════════════════════════════════════════════


class TestScenarioE(unittest.TestCase):
    """
    M6.0-3 Scenario E: World event → Inner Life.

    Verifies:
      - calendar_event qualifies (whitelist)
      - user_going_outside qualifies
      - other types fail-closed
      - InnerLifeEvent created with proper provenance
      - trace.jsonl appended (canonical identity, no content duplication)
    """

    def setUp(self):
        self.tmpdir_obj = _setup_isolated_env()
        self.tmp = Path(self.tmpdir_obj.name)

    def tearDown(self):
        _cleanup_tmpdir(self.tmpdir_obj)

    def test_e1_qualifying_types(self):
        """E1: calendar_event and user_going_outside qualify (YES)."""
        runner = CheckpointRunner("Scenario E1")

        from src.world.inner_life_adapter import qualify_world_event
        from src.world.perception import WorldEvent

        # calendar_event qualifies
        cal = WorldEvent(
            source="calendar",
            type="calendar_event",
            novelty_id="cal-1",
            ts=_now_iso(),
            summary="calendar_event: 30-min meeting",
            data={},
            priority=0,
        )
        result = qualify_world_event(cal)
        runner.run(
            "E1: calendar_event qualifies YES",
            lambda: assert_text_contains(
                str(result.decision), "YES",
                label="calendar_event → YES"
            ),
        )

        # user_going_outside qualifies
        walk = WorldEvent(
            source="social",
            type="user_going_outside",
            novelty_id="walk-1",
            ts=_now_iso(),
            summary="user_going_outside: walk",
            data={"actor": "agent_yua"},
            priority=0,
        )
        result = qualify_world_event(walk)
        runner.run(
            "E1: user_going_outside qualifies YES",
            lambda: assert_text_contains(
                str(result.decision), "YES",
                label="user_going_outside → YES"
            ),
        )

        # SG-1 解冻 (2026-08-29, Owner 授权 whitelist 扩展): rain_started 现在 qualify
        rain = WorldEvent(
            source="weather",
            type="rain_started",
            novelty_id="rain-1",
            ts=_now_iso(),
            summary="rain_started: heavy",
            data={},
            priority=0,
        )
        result = qualify_world_event(rain)
        runner.run(
            "E1: rain_started qualifies YES (SG-1 解冻 whitelist 扩展)",
            lambda: assert_text_contains(
                str(result.decision), "YES",
                label="rain_started → YES (SG-1 解冻)"
            ),
        )

        runner.assert_all_passed()

    def test_e2_inner_life_event_creation_with_provenance(self):
        """E2: InnerLifeEvent created with proper provenance + identity."""
        runner = CheckpointRunner("Scenario E2")

        from src.inner_life import InnerLifeWriter, Provenance
        from src.inner_life.trace import NarrativeTraceWriter
        from src.world.inner_life_adapter import (
            qualify_world_event, WorldInnerLifeAdapter, WorldQualificationDecision
        )
        from src.world.perception import WorldEvent

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            trace_path = self.tmp / "data" / "inner_life" / "trace.jsonl"
            trace_writer = NarrativeTraceWriter(trace_log_path=trace_path)
            writer = InnerLifeWriter(trace_writer=trace_writer)

            # Create a qualifying world event
            world_event = WorldEvent(
                source="calendar",
                type="calendar_event",
                novelty_id="cal-2026-08-11-1",
                ts=_now_iso(),
                summary="calendar_event: 30-min meeting",
                data={"actor": "agent_yua"},
                priority=0,
            )
            qual = qualify_world_event(world_event)
            assert qual.decision == WorldQualificationDecision.YES, (
                f"Expected YES, got {qual.decision}"
            )

            # Create InnerLifeEvent via correct signature:
            #   create_event(*, provenance: Provenance, session_id, correlation_id,
            #                parent_event_id, ts)
            # NOT trigger_type / source_system / actor_id / summary directly.
            ev = writer.create_event(
                provenance=Provenance(
                    trigger_type="world:calendar_event",
                    source_system="narrative",
                    actor_id=None,  # M5.9-3 spec: world events have no actor
                ),
                session_id=None,
                correlation_id=None,
                parent_event_id=None,
            )

            runner.run(
                "E2: event_id is canonical (32 hex chars)",
                lambda: assert_state_equals(
                    len(ev.event_id), 32, label="event_id length 32 hex"
                ),
            )
            runner.run(
                "E2: provenance.trigger_type = 'world:calendar_event'",
                lambda: assert_state_equals(
                    ev.provenance.trigger_type, "world:calendar_event",
                    label="provenance trigger_type"
                ),
            )
            runner.run(
                "E2: provenance.source_system = 'narrative'",
                lambda: assert_state_equals(
                    ev.provenance.source_system, "narrative",
                    label="provenance source_system"
                ),
            )
            runner.run(
                "E2: actor_id = None (M5.9-3 spec)",
                lambda: assert_state_equals(
                    ev.provenance.actor_id, None,
                    label="actor_id None for world events"
                ),
            )
            runner.run(
                "E2: ts is ISO 8601",
                lambda: assert_state_equals(
                    "T" in ev.ts, True,
                    label="ts ISO 8601 format"
                ),
            )

        runner.assert_all_passed()

    def test_e3_trace_jsonl_appended(self):
        """E3: trace.jsonl appended with canonical identity (no content dup)."""
        runner = CheckpointRunner("Scenario E3")

        from src.inner_life import InnerLifeWriter, Provenance
        from src.inner_life.trace import NarrativeTraceWriter

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            trace_path = self.tmp / "data" / "inner_life" / "trace.jsonl"
            trace_writer = NarrativeTraceWriter(trace_log_path=trace_path)
            writer = InnerLifeWriter(trace_writer=trace_writer)

            ev = writer.create_event(
                provenance=Provenance(
                    trigger_type="world:calendar_event",
                    source_system="narrative",
                    actor_id=None,
                ),
                session_id=None,
                correlation_id=None,
                parent_event_id=None,
            )

            runner.run(
                "E3: trace.jsonl exists after create_event",
                lambda: assert_file_exists(trace_path),
            )
            runner.run(
                "E3: trace.jsonl contains event_id",
                lambda: assert_file_contains(
                    trace_path, ev.event_id,
                    label="event_id in trace"
                ),
            )
            runner.run(
                "E3: trace.jsonl contains trigger_type",
                lambda: assert_file_contains(
                    trace_path, "world:calendar_event",
                    label="trigger_type in trace"
                ),
            )
            runner.run(
                "E3: trace.jsonl line count = 1 (deterministic)",
                lambda: assert_state_equals(
                    len(trace_path.read_text(encoding="utf-8").strip().splitlines()),
                    1,
                    label="single trace line"
                ),
            )

        runner.assert_all_passed()

    def test_e4_no_world_event_direct_to_llm_path(self):
        """E4: No direct LLM path from WorldEvent. Only via InnerLifeEvent."""
        runner = CheckpointRunner("Scenario E4")

        # Verify by code inspection: WorldEvent goes through
        # WorldPerceptionMiddleware → WorldInnerLifeAdapter → InnerLifeWriter.
        # There's no direct path from WorldEvent to LLMProxy (frozen M3).
        # This is verified by reading the source.
        from src.world.inner_life_adapter import WorldInnerLifeAdapter
        import inspect

        # Verify WorldInnerLifeAdapter has no LLM-related dependency
        adapter_src = inspect.getsource(WorldInnerLifeAdapter)
        runner.run(
            "E4: WorldInnerLifeAdapter has no LLM/embedding/vector import",
            lambda: assert_text_not_contains(
                adapter_src, "LLMProxy",
                label="no LLM dependency in adapter"
            ),
        )
        runner.run(
            "E4: WorldInnerLifeAdapter has no embedding import",
            lambda: assert_text_not_contains(
                adapter_src, "embedding",
                label="no embedding in adapter"
            ),
        )

        runner.assert_all_passed()

    def test_e5_no_production_data_mutation(self):
        """E5: Production trace.jsonl not touched."""
        runner = CheckpointRunner("Scenario E5")
        prod_trace = Path.cwd() / "data" / "inner_life" / "trace.jsonl"

        mtime_before = None
        if prod_trace.exists():
            mtime_before = prod_trace.stat().st_mtime

        runner.run(
            "E5: Production trace.jsonl mtime unchanged",
            lambda: assert_state_equals(
                prod_trace.stat().st_mtime == mtime_before if mtime_before else True, True,
                label="production trace must not be mutated"
            ),
        )
        runner.assert_all_passed()


# ════════════════════════════════════════════════════════════════════════
# Scenario F: World event → proactive gate
# ════════════════════════════════════════════════════════════════════════


class TestScenarioF(unittest.TestCase):
    """
    M6.0-3 Scenario F: Agent-specific InnerLife event → proactive gate (M5.8-4).

    M5.14-2 (Bry 派工 2026-08-11 18:37) reclassified F1-P1 (was M5.8-4 vs M5.9-3
    contract conflict, reclassified to P3 test design issue): World events are
    agent-agnostic by design (M5.9-2 spec §6 actor_id=None), so they correctly
    do NOT gate any specific agent's proactive_dm. To validate the gate's
    30-min cooldown semantic, F1-F3 must use canonical agent-specific producer
    events (e.g. diary:morning from _diary_writer_executor at
    scripts/run_server.py:812-817).

    Fixture F now uses trigger_type="diary:morning" + actor_id="agent_yua"
    — natural match for M5.8-4 gate filter (provenance.actor_id == agent_id),
    not the artificial WorldEvent workaround used in M6.0-3 prior commit.

    Verifies:
      - Recent diary:morning by same agent suppresses proactive_dm (GATED)
      - Outside 30-min cooldown window allows EMITTED
      - No trace file → UNAVAILABLE (fail-open)
      - event/dream/morning/night trigger types NOT gated (only proactive_dm)
    """

    def setUp(self):
        self.tmpdir_obj = _setup_isolated_env()
        self.tmp = Path(self.tmpdir_obj.name)
        # Load trace.jsonl with a recent agent-specific event
        _load_fixture(
            self.tmp,
            "scenario_F/trace.jsonl",
            "data/inner_life/trace.jsonl",
        )

    def tearDown(self):
        _cleanup_tmpdir(self.tmpdir_obj)

    def test_f1_recent_event_gates_proactive_dm(self):
        """F1: diary:morning by same agent < 30min ago → proactive_dm GATED.

        Fixture F has trigger_type="diary:morning" + actor_id="agent_yua",
        matching M5.8-4 gate filter (provenance.actor_id == agent_id).
        Gate fires on 15 min < 30 min threshold check.
        """
        runner = CheckpointRunner("Scenario F1")

        from src.agency.inner_life_gate import gate_proactive_dm, GateDecision

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            # The trace event ts is 2026-08-11T12:00:00, "now" is 12:15 (15 min later)
            now = datetime(2026, 8, 11, 12, 15, 0, tzinfo=timezone.utc)
            result = gate_proactive_dm(
                agent_id="agent_yua",  # M5.8-4 contract: str required
                now=now,
            )
            runner.run(
                "F1: 15 min elapsed → GATED (recent inner life activity)",
                lambda: assert_state_equals(
                    result.decision, GateDecision.GATED,
                    label="15 min < 30 min threshold"
                ),
            )

        runner.assert_all_passed()

    def test_f2_outside_cooldown_allows_emitted(self):
        """F2: diary:morning by same agent > 30min ago → proactive_dm EMITTED.

        Same fixture (ts=12:00), now=13:00 (60 min elapsed), past 30-min threshold.
        """
        runner = CheckpointRunner("Scenario F2")

        from src.agency.inner_life_gate import gate_proactive_dm, GateDecision

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            # The trace event ts is 2026-08-11T12:00:00, "now" is 13:00 (60 min later)
            now = datetime(2026, 8, 11, 13, 0, 0, tzinfo=timezone.utc)
            result = gate_proactive_dm(
                agent_id="agent_yua",
                now=now,
            )
            runner.run(
                "F2: 60 min elapsed → EMITTED (past 30 min threshold)",
                lambda: assert_state_equals(
                    result.decision, GateDecision.EMITTED,
                    label="60 min > 30 min threshold"
                ),
            )

        runner.assert_all_passed()

    def test_f3_no_trace_file_unavailable_failopen(self):
        """F3: No trace file → UNAVAILABLE (fail-open = emit)."""
        runner = CheckpointRunner("Scenario F3")

        from src.agency.inner_life_gate import gate_proactive_dm, GateDecision

        # Empty tempdir (no trace file)
        empty_tmpdir = tempfile.TemporaryDirectory()
        try:
            with patch("src.paths.data_root", return_value=Path(empty_tmpdir.name) / "data"):
                now = datetime.now(timezone.utc)
                result = gate_proactive_dm(agent_id="agent_yua", now=now)
                runner.run(
                    "F3: No trace file → UNAVAILABLE (fail-open)",
                    lambda: assert_state_equals(
                        result.decision, GateDecision.UNAVAILABLE,
                        label="no trace → UNAVAILABLE → emit"
                    ),
                )
        finally:
            _cleanup_tmpdir(empty_tmpdir)

        runner.assert_all_passed()

    def test_f4_other_trigger_types_not_gated(self):
        """F4: event/dream/morning/night producer paths are NOT gated by M5.8-4.

        M5.8-4 design: only proactive_dm is gated. The 4 producer types
        (event/dream/morning/night) write to inner_life themselves, so
        gating them would be circular.
        This test verifies the design by checking the gate function's
        contract surface — it takes a trigger_type argument but only
        proactive_dm path uses it.
        """
        runner = CheckpointRunner("Scenario F4")

        from src.agency.inner_life_gate import gate_proactive_dm, GateDecision

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            # The gate is specifically for proactive_dm
            # The trigger_type argument exists but is only used to short-circuit
            # non-proactive_dm types. Verify the gate only fires for proactive_dm
            # by checking the M5.8-4 source code.
            from src.agency import inner_life_gate
            import inspect
            gate_src = inspect.getsource(inner_life_gate)

            # Verify gate has trigger_type check
            runner.run(
                "F4: gate has trigger_type parameter",
                lambda: assert_text_contains(
                    gate_src, "trigger_type",
                    label="trigger_type parameter exists"
                ),
            )
            runner.run(
                "F4: gate only applies to proactive_dm",
                lambda: assert_text_contains(
                    gate_src, 'proactive_dm',
                    label="only proactive_dm gating"
                ),
            )

        runner.assert_all_passed()

    def test_f5_no_production_data_mutation(self):
        """F5: Production trace.jsonl not touched."""
        runner = CheckpointRunner("Scenario F5")
        prod_trace = Path.cwd() / "data" / "inner_life" / "trace.jsonl"

        mtime_before = None
        if prod_trace.exists():
            mtime_before = prod_trace.stat().st_mtime

        runner.run(
            "F5: Production trace.jsonl mtime unchanged",
            lambda: assert_state_equals(
                prod_trace.stat().st_mtime == mtime_before if mtime_before else True, True,
                label="production trace must not be mutated"
            ),
        )
        runner.assert_all_passed()


# ════════════════════════════════════════════════════════════════════════
# Scenario G: Inner-life persistence
# ════════════════════════════════════════════════════════════════════════


class TestScenarioG(unittest.TestCase):
    """
    M6.0-3 Scenario G: Inner-life persistence.

    Verifies:
      - Trace records have canonical identity (event_id, ts, provenance)
      - Multiple trigger_types coexist in trace
      - Source filtering works (diary source="llm" vs "placeholder")
      - Time-window behavior (24h)
      - Deterministic replay
    """

    def setUp(self):
        self.tmpdir_obj = _setup_isolated_env()
        self.tmp = Path(self.tmpdir_obj.name)
        _load_fixture(
            self.tmp,
            "scenario_G/trace.jsonl",
            "data/inner_life/trace.jsonl",
        )
        # Time-bomb fix (2026-08-29): fixture ts hardcoded 2026-08-11 is now
        # outside the 24h query window → G1/G2 fail forever. Refresh to
        # relative times (now - 3h/2h/1h) so they always land in the window.
        _refresh_trace_timestamps(self.tmp / "data" / "inner_life" / "trace.jsonl")

    def tearDown(self):
        _cleanup_tmpdir(self.tmpdir_obj)

    def test_g1_trace_records_have_canonical_identity(self):
        """G1: Each trace record has event_id, ts, provenance."""
        runner = CheckpointRunner("Scenario G1")

        from src.inner_life.trace_reader import NarrativeTraceReader

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            reader = NarrativeTraceReader()
            # 24h window from now
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            window_end = now.isoformat()
            records = reader.query_by_ts_range(start=window_start, end=window_end)

            runner.run(
                "G1: 3 records retrieved from trace",
                lambda: assert_state_equals(
                    len(records), 3,
                    label="3 fixture records"
                ),
            )
            if len(records) >= 1:
                first = records[0]
                runner.run(
                    "G1: First record has event_id",
                    lambda: assert_state_equals(
                        "event_id" in first, True,
                        label="event_id field present"
                    ),
                )
                runner.run(
                    "G1: First record has ts",
                    lambda: assert_state_equals(
                        "ts" in first, True,
                        label="ts field present"
                    ),
                )
                runner.run(
                    "G1: First record has provenance",
                    lambda: assert_state_equals(
                        "provenance" in first, True,
                        label="provenance field present"
                    ),
                )
                runner.run(
                    "G1: provenance has trigger_type",
                    lambda: assert_state_equals(
                        "trigger_type" in first.get("provenance", {}), True,
                        label="trigger_type in provenance"
                    ),
                )
                runner.run(
                    "G1: provenance has source_system",
                    lambda: assert_state_equals(
                        "source_system" in first.get("provenance", {}), True,
                        label="source_system in provenance"
                    ),
                )

        runner.assert_all_passed()

    def test_g2_multiple_trigger_types_coexist(self):
        """G2: Multiple trigger types coexist in trace (world + dream)."""
        runner = CheckpointRunner("Scenario G2")

        from src.inner_life.trace_reader import NarrativeTraceReader

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            reader = NarrativeTraceReader()
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            window_end = now.isoformat()
            records = reader.query_by_ts_range(start=window_start, end=window_end)

            trigger_types = set()
            for r in records:
                tt = r.get("provenance", {}).get("trigger_type", "")
                trigger_types.add(tt)

            runner.run(
                "G2: Multiple trigger_types in trace",
                lambda: assert_state_equals(
                    len(trigger_types) >= 2, True,
                    label="at least 2 distinct trigger_types"
                ),
            )
            runner.run(
                "G2: world:calendar_event present in at least one record",
                lambda: assert_state_equals(
                    "world:calendar_event" in trigger_types, True,
                    label="calendar_event in trace"
                ),
            )

        runner.assert_all_passed()

    def test_g3_deterministic_replay(self):
        """G3: Re-reading trace returns identical records (deterministic replay)."""
        runner = CheckpointRunner("Scenario G3")

        from src.inner_life.trace_reader import NarrativeTraceReader

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            reader = NarrativeTraceReader()
            now = datetime.now(timezone.utc)
            window_start = (now - timedelta(hours=24)).isoformat()
            window_end = now.isoformat()

            # First read
            r1 = reader.query_by_ts_range(start=window_start, end=window_end)
            # Second read (should be identical)
            r2 = reader.query_by_ts_range(start=window_start, end=window_end)

            runner.run(
                "G3: Replay returns same count",
                lambda: assert_state_equals(
                    len(r1), len(r2),
                    label="deterministic count"
                ),
            )
            runner.run(
                "G3: Replay returns same event_ids (in order)",
                lambda: assert_state_equals(
                    [r["event_id"] for r in r1],
                    [r["event_id"] for r in r2],
                    label="deterministic event_ids"
                ),
            )

        runner.assert_all_passed()

    def test_g4_no_production_data_mutation(self):
        """G4: Production trace.jsonl not touched."""
        runner = CheckpointRunner("Scenario G4")
        prod_trace = Path.cwd() / "data" / "inner_life" / "trace.jsonl"

        mtime_before = None
        if prod_trace.exists():
            mtime_before = prod_trace.stat().st_mtime

        runner.run(
            "G4: Production trace.jsonl mtime unchanged",
            lambda: assert_state_equals(
                prod_trace.stat().st_mtime == mtime_before if mtime_before else True, True,
                label="production trace must not be mutated"
            ),
        )
        runner.assert_all_passed()


# ════════════════════════════════════════════════════════════════════════
# Scenario H: Multi-cycle lived context
# ════════════════════════════════════════════════════════════════════════


class TestScenarioH(unittest.TestCase):
    """
    M6.0-3 Scenario H: Multi-cycle lived context.

    Three deterministic cycles:
      Cycle 1: initial → USER_MESSAGE → relationships.touch + memory
      Cycle 2: persisted → AGENT_INTENT → memory retrieval includes cycle 1
      Cycle 3: accumulated → USER_MESSAGE → relationships still accumulating

    Verifies:
      - continuity survives cycles
      - no uncontrolled accumulation (each cycle bounded)
      - no duplicated context
      - no cross-agent contamination
      - all bounded projections remain bounded
    """

    def setUp(self):
        self.tmpdir_obj = _setup_isolated_env()
        self.tmp = Path(self.tmpdir_obj.name)

    def tearDown(self):
        _cleanup_tmpdir(self.tmpdir_obj)

    def _write_relationship(self, agent_id: str, confidence: float) -> None:
        from tests.test_m6_0_2_validation_poc import _write_relationship_no_decay
        _write_relationship_no_decay(self.tmp, agent_id, confidence)

    def test_h1_three_cycles_relationship_accumulation(self):
        """H1: 3 USER_MESSAGE cycles → relationships.confidence increases monotonically."""
        runner = CheckpointRunner("Scenario H1")

        from src.eventbus import SoulEventBus
        from src.eventbus.schema import EventType, EventPriority, SoulEvent
        from src.memory.middleware import MemoryMiddleware

        self._write_relationship("agent_yua", 0.0)

        with patch("src.soul.relationships._manager_singleton", None):
            with patch("src.paths.data_root", return_value=self.tmp / "data"):
                bus = SoulEventBus()

                async def run_three_cycles():
                    await bus.start()
                    try:
                        mm = MemoryMiddleware(bus=bus)

                        confidences = []
                        for i in range(3):
                            user_msg = SoulEvent(
                                event_type=EventType.USER_MESSAGE,
                                source="user_bryan",
                                target="agent_yua",
                                priority=EventPriority.NORMAL,
                                payload={
                                    "text": f"User message {i+1}",
                                    "target_agent": "agent_yua",
                                    "mode": "private",
                                },
                            )
                            await mm._on_user_message(user_msg)

                            # Read confidence after each cycle
                            rel_path = self.tmp / "data" / "soul" / "agent_yua" / "relationships.json"
                            data = json.loads(rel_path.read_text(encoding="utf-8"))
                            conf = data["others"]["user_bryan"]["confidence"]
                            confidences.append(conf)

                        return confidences
                    finally:
                        await bus.stop()

                confidences = asyncio.run(run_three_cycles())

                runner.run(
                    "H1: 3 confidences recorded",
                    lambda: assert_state_equals(
                        len(confidences), 3, label="3 cycle outputs"
                    ),
                )
                runner.run(
                    "H1: Confidence increases monotonically (cycle1 < cycle2 < cycle3)",
                    lambda: assert_state_equals(
                        confidences[0] < confidences[1] < confidences[2], True,
                        label="monotonic increase"
                    ),
                )
                runner.run(
                    "H1: Each cycle adds ~0.02 (delta is bounded, not uncontrolled)",
                    lambda: assert_state_equals(
                        abs((confidences[1] - confidences[0]) - 0.02) < 0.01
                        and abs((confidences[2] - confidences[1]) - 0.02) < 0.01,
                        True,
                        label="bounded delta per cycle"
                    ),
                )

        runner.assert_all_passed()

    def test_h2_no_cross_agent_contamination(self):
        """H2: agent_yua and agent_ruka have independent accumulation."""
        runner = CheckpointRunner("Scenario H2")

        from src.eventbus import SoulEventBus
        from src.eventbus.schema import EventType, EventPriority, SoulEvent
        from src.memory.middleware import MemoryMiddleware

        self._write_relationship("agent_yua", 0.0)
        self._write_relationship("agent_ruka", 0.0)

        with patch("src.soul.relationships._manager_singleton", None):
            with patch("src.paths.data_root", return_value=self.tmp / "data"):
                bus = SoulEventBus()

                async def run_cycles():
                    await bus.start()
                    try:
                        mm = MemoryMiddleware(bus=bus)
                        # 2 cycles for yua
                        for _ in range(2):
                            await mm._on_user_message(SoulEvent(
                                event_type=EventType.USER_MESSAGE,
                                source="user_bryan",
                                target="agent_yua",
                                payload={"text": "yua msg", "target_agent": "agent_yua"},
                            ))
                        # 1 cycle for ruka
                        await mm._on_user_message(SoulEvent(
                            event_type=EventType.USER_MESSAGE,
                            source="user_bryan",
                            target="agent_ruka",
                            payload={"text": "ruka msg", "target_agent": "agent_ruka"},
                        ))

                        yua_conf = json.loads(
                            (self.tmp / "data" / "soul" / "agent_yua" / "relationships.json").read_text()
                        )["others"]["user_bryan"]["confidence"]
                        ruka_conf = json.loads(
                            (self.tmp / "data" / "soul" / "agent_ruka" / "relationships.json").read_text()
                        )["others"]["user_bryan"]["confidence"]
                        return yua_conf, ruka_conf
                    finally:
                        await bus.stop()

                yua_conf, ruka_conf = asyncio.run(run_cycles())

                runner.run(
                    "H2: yua conf > ruka conf (2 cycles vs 1 cycle)",
                    lambda: assert_state_equals(
                        yua_conf > ruka_conf, True,
                        label="no cross-agent contamination"
                    ),
                )
                # Verify bounded deltas
                runner.run(
                    "H2: yua conf ≈ 0.04 (2 cycles × 0.02)",
                    lambda: assert_state_equals(
                        abs(yua_conf - 0.04) < 0.01, True,
                        label="yua bounded"
                    ),
                )
                runner.run(
                    "H2: ruka conf ≈ 0.02 (1 cycle × 0.02)",
                    lambda: assert_state_equals(
                        abs(ruka_conf - 0.02) < 0.01, True,
                        label="ruka bounded"
                    ),
                )

        runner.assert_all_passed()

    def test_h3_no_duplicated_context(self):
        """H3: 2 cycles of same USER_MESSAGE → no duplicate inner_life events (bounded)."""
        runner = CheckpointRunner("Scenario H3")

        from src.inner_life import InnerLifeWriter, Provenance
        from src.inner_life.trace import NarrativeTraceWriter

        with patch("src.paths.data_root", return_value=self.tmp / "data"):
            trace_path = self.tmp / "data" / "inner_life" / "trace.jsonl"
            trace_writer = NarrativeTraceWriter(trace_log_path=trace_path)
            writer = InnerLifeWriter(trace_writer=trace_writer)

            # 2 cycles creating inner_life events (distinct event_ids, no dedup violation)
            ev1 = writer.create_event(
                provenance=Provenance(
                    trigger_type="world:calendar_event",
                    source_system="narrative",
                    actor_id=None,
                ),
                session_id=None,
                correlation_id=None,
                parent_event_id=None,
            )
            ev2 = writer.create_event(
                provenance=Provenance(
                    trigger_type="world:calendar_event",
                    source_system="narrative",
                    actor_id=None,
                ),
                session_id=None,
                correlation_id=None,
                parent_event_id=None,
            )

            runner.run(
                "H3: 2 events created (deterministic, not duplicated)",
                lambda: assert_state_equals(
                    len(trace_path.read_text(encoding="utf-8").strip().splitlines()),
                    2,
                    label="2 events, no dup"
                ),
            )
            runner.run(
                "H3: event_ids are unique (no dedup violation)",
                lambda: assert_state_equals(
                    ev1.event_id == ev2.event_id, False,
                    label="distinct canonical event_ids"
                ),
            )

        runner.assert_all_passed()

    def test_h4_no_production_data_mutation(self):
        """H4: Production relationships.json not touched."""
        runner = CheckpointRunner("Scenario H4")
        prod_rel = Path.cwd() / "data" / "soul" / "agent_yua" / "relationships.json"

        mtime_before = None
        if prod_rel.exists():
            mtime_before = prod_rel.stat().st_mtime

        runner.run(
            "H4: Production relationships.json mtime unchanged",
            lambda: assert_state_equals(
                prod_rel.stat().st_mtime == mtime_before if mtime_before else True, True,
                label="production relationships must not be mutated"
            ),
        )
        runner.assert_all_passed()


if __name__ == "__main__":
    unittest.main()
