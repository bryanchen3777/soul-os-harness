"""
tests/test_m5_8_4_producer_gating.py — M5.8-4 Inner Life → Agency Producer Gating

M5.8-4 (Bry 派工 2026-08-10): Inner Life → Agency Producer Gating
Mode: MINIMAL ADDITIVE BOUNDARY (Option Y from M5.8-3 audit)

Test sections:
  A. Gate decision: 4 distinct states (EMITTED / GATED / UNAVAILABLE / FAILURE)
  B. Producer-side: scheduler._publish_agency_trigger call gate for proactive_dm
  C. Non-proactive_dm trigger_type NOT affected (event / dream / morning / night)
  D. Gate properties: deterministic / observable / fail-safe / no event creation
  E. Frozen contract verification (Stage 1-4 / TriggerEnvelope / 4 handlers untouched)
  F. data_root() isolation

Test count: ~20 tests
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agency.inner_life_gate import (
    GateDecision,
    GateResult,
    GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES,
    GATE_QUERY_WINDOW_HOURS,
    gate_proactive_dm,
)
from src.inner_life.trace_reader import NarrativeTraceReader
from src.paths import data_root, reset_data_root
from src.soul.scheduler import SoulScheduler


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────

def _isolated_data_root(tmp_path: Path) -> Path:
    """Force data_root() to point to a temp dir; return data_root() Path."""
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()
    return data_root()


def _restore_data_root() -> None:
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


def _make_trace_record(
    agent_id: str,
    ts: str,
    event_id: str = None,
    trigger_type: str = "agent_reply",
    source_system: str = "narrative",
) -> Dict[str, Any]:
    """Construct a single trace record (event_to_dict() shape) for testing."""
    if event_id is None:
        import uuid
        event_id = uuid.uuid4().hex
    return {
        "event_id": event_id,
        "session_id": "sess-test",
        "correlation_id": "corr-test",
        "parent_event_id": None,
        "ts": ts,
        "provenance": {
            "trigger_type": trigger_type,
            "actor_id": agent_id,
            "source_system": source_system,
            "trace_ref": None,
            "extras": {},
        },
        "lineage_depth": 0,
        "lineage_path": event_id,
    }


def _seed_trace_file(trace_path: Path, records: List[Dict[str, Any]]) -> None:
    """Write records to a trace.jsonl file."""
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    with open(trace_path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


@pytest.fixture
def isolated_root(tmp_path: Path):
    """Yield isolated data_root, restore after."""
    data_dir = _isolated_data_root(tmp_path)
    yield data_dir
    _restore_data_root()


# ────────────────────────────────────────────────────────────────────
# A. Gate decision: 4 distinct states
# ────────────────────────────────────────────────────────────────────

class TestSectionA_GateStates:
    """A. 4 distinct gate states per Bry spec §8."""

    def test_a1_emitted_when_no_recent_event(self, isolated_root, tmp_path):
        """A.1: agent has old event (> 30 min ago) → EMITTED."""
        trace_path = tmp_path / "inner_life" / "trace.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=old_ts),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.EMITTED
        assert result.last_event_id is not None
        assert result.elapsed_minutes is not None
        assert result.elapsed_minutes > GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES

    def test_a2_gated_when_recent_event(self, isolated_root, tmp_path):
        """A.2: agent has recent event (< 30 min ago) → GATED."""
        trace_path = tmp_path / "inner_life" / "trace.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=recent_ts),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.GATED
        assert result.last_event_id is not None
        assert result.elapsed_minutes is not None
        assert result.elapsed_minutes < GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES
        assert "threshold" in result.reason or "<" in result.reason

    def test_a3_unavailable_when_no_trace_file(self, isolated_root, tmp_path):
        """A.3: no trace.jsonl exists → UNAVAILABLE (fail-open = emit)."""
        non_existent = tmp_path / "no_trace_here" / "trace.jsonl"
        reader = NarrativeTraceReader(trace_log_path=non_existent)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.UNAVAILABLE
        assert result.last_event_id is None
        assert result.elapsed_minutes is None

    def test_a4_unavailable_when_no_agent_events(self, isolated_root, tmp_path):
        """A.4: trace exists but no events for this agent → UNAVAILABLE (fail-open)."""
        trace_path = tmp_path / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        # Only events for other agent
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_akane", ts=ts),
            _make_trace_record(agent_id="agent_rem", ts=ts),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.UNAVAILABLE
        assert "agent_yua" in result.reason

    def test_a5_failure_on_malformed_record(self, isolated_root, tmp_path):
        """A.5: record missing event_id or ts → FAILURE (fail-open = emit)."""
        trace_path = tmp_path / "inner_life" / "trace.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        # Malformed: no event_id
        _seed_trace_file(trace_path, [
            {
                "session_id": "sess",
                "ts": recent_ts,
                "provenance": {"actor_id": "agent_yua", "trigger_type": "agent_reply", "source_system": "narrative"},
            },
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.FAILURE
        assert "malformed" in result.reason

    def test_a6_failure_on_query_exception(self, isolated_root, tmp_path):
        """A.6: query raises exception → FAILURE (fail-open = emit)."""
        # Use a path that will cause OSError on read
        bad_path = tmp_path / "bad.jsonl"
        # Create a directory where file is expected → read will fail
        bad_path.mkdir(parents=True, exist_ok=True)
        # NarrativeTraceReader should handle this gracefully
        # but we want to test the FAILURE state via a different path
        reader = NarrativeTraceReader(trace_log_path=bad_path)
        # Even with directory-as-file, the reader should not raise (per M5.4-5.7 contract)
        # So we test with a mock that raises
        mock_reader = MagicMock()
        mock_reader.query_by_ts_range.side_effect = RuntimeError("test exception")
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=mock_reader,
        )
        assert result.decision == GateDecision.FAILURE
        assert "exception" in result.reason.lower() or "RuntimeError" in result.reason


# ────────────────────────────────────────────────────────────────────
# B. Producer-side: scheduler._publish_agency_trigger integration
# ────────────────────────────────────────────────────────────────────

class TestSectionB_SchedulerIntegration:
    """B. scheduler._publish_agency_trigger calls gate for proactive_dm."""

    def test_b1_gated_proactive_dm_skips_publish(self, isolated_root, tmp_path):
        """B.1: proactive_dm gated → no event on bus."""
        # Seed trace with recent event for agent_yua
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=recent_ts),
        ])

        async def _run():
            from src.eventbus.bus import SoulEventBus
            from src.eventbus.schema import EventType
            bus = SoulEventBus()
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = asyncio.run(_run())
        # Gated → no AGENCY_TRIGGER published
        assert len(captured) == 0

    def test_b2_emitted_proactive_dm_publishes_normally(self, isolated_root, tmp_path):
        """B.2: proactive_dm not gated → AGENCY_TRIGGER published as usual."""
        # Seed trace with old event for agent_yua (> 30 min ago)
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=old_ts),
        ])

        async def _run():
            from src.eventbus.bus import SoulEventBus
            from src.eventbus.schema import EventType
            bus = SoulEventBus()
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = asyncio.run(_run())
        # EMITTED → AGENCY_TRIGGER published
        assert len(captured) == 1
        assert captured[0].payload["trigger_type"] == "proactive_dm"
        assert captured[0].payload["agent_id"] == "agent_yua"

    def test_b3_unavailable_proactive_dm_publishes_normally(self, isolated_root, tmp_path):
        """B.3: no trace file → fail-open = publish normally."""
        # Don't seed any trace file

        async def _run():
            from src.eventbus.bus import SoulEventBus
            from src.eventbus.schema import EventType
            bus = SoulEventBus()
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type="proactive_dm",
                )
                return captured
            finally:
                await bus.stop()

        captured = asyncio.run(_run())
        # UNAVAILABLE (fail-open) → AGENCY_TRIGGER published
        assert len(captured) == 1
        assert captured[0].payload["trigger_type"] == "proactive_dm"


# ────────────────────────────────────────────────────────────────────
# C. Non-proactive_dm trigger_type NOT affected
# ────────────────────────────────────────────────────────────────────

class TestSectionC_NonProactiveDmUnaffected:
    """C. 4 other trigger types (event / dream / morning / night) not gated."""

    @pytest.mark.parametrize("trigger_type", ["event", "dream", "morning", "night"])
    def test_c1_other_triggers_publish_unconditionally(
        self, isolated_root, tmp_path, trigger_type
    ):
        """C.1: Even with recent inner life activity, other triggers are not gated."""
        # Seed recent event for the agent
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=recent_ts),
        ])

        async def _run():
            from src.eventbus.bus import SoulEventBus
            from src.eventbus.schema import EventType
            bus = SoulEventBus()
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(bus=bus)
                await scheduler._publish_agency_trigger(
                    agent_id="agent_yua",
                    trigger_type=trigger_type,
                )
                return captured
            finally:
                await bus.stop()

        captured = asyncio.run(_run())
        # Even with recent inner life, other trigger_types publish normally
        assert len(captured) == 1
        assert captured[0].payload["trigger_type"] == trigger_type


# ────────────────────────────────────────────────────────────────────
# D. Gate properties
# ────────────────────────────────────────────────────────────────────

class TestSectionD_GateProperties:
    """D. deterministic / observable / fail-safe / no event creation."""

    def test_d1_deterministic_same_input_same_output(self, isolated_root, tmp_path):
        """D.1: same input → same output (deterministic)."""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        ts = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts, event_id="fixed_event_id"),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        now = datetime.now(timezone.utc)
        r1 = gate_proactive_dm(agent_id="agent_yua", now=now, trace_reader=reader)
        r2 = gate_proactive_dm(agent_id="agent_yua", now=now, trace_reader=reader)
        assert r1.decision == r2.decision
        assert r1.last_event_id == r2.last_event_id
        assert r1.reason == r2.reason

    def test_d2_no_inner_life_event_created(self, isolated_root, tmp_path):
        """D.2: gate function does NOT create InnerLifeEvent."""
        # No trace file at all
        reader = NarrativeTraceReader(trace_log_path=tmp_path / "no.jsonl")
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        # No event created in any path
        assert result.decision == GateDecision.UNAVAILABLE
        # Verify trace file still doesn't exist (gate is read-only)
        assert not (tmp_path / "no.jsonl").exists()

    def test_d3_does_not_read_conversation_content(self, isolated_root, tmp_path):
        """D.3: gate only reads identity + lineage + ts, no narrative/diary/dream text."""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        # Records with NO text content (just identity metadata)
        # Per M5.4-5.6 design: trace records = event_to_dict() = identity + lineage only
        ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(agent_id="agent_yua", ts=ts),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        # Gated (5 min < 30 min threshold)
        assert result.decision == GateDecision.GATED
        # Result contains only metadata, no text content
        assert result.last_event_id is not None
        assert result.last_event_ts is not None
        # No conversation / diary / dream text fields

    def test_d4_observable_metadata(self, isolated_root, tmp_path):
        """D.4: GATED result carries observability metadata."""
        trace_path = isolated_root / "inner_life" / "trace.jsonl"
        recent_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        _seed_trace_file(trace_path, [
            _make_trace_record(
                agent_id="agent_yua",
                ts=recent_ts,
                event_id="abcd1234abcd1234abcd1234abcd1234",
            ),
        ])
        reader = NarrativeTraceReader(trace_log_path=trace_path)
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.GATED
        assert result.last_event_id == "abcd1234abcd1234abcd1234abcd1234"
        assert result.last_event_ts == recent_ts
        assert result.elapsed_minutes is not None
        assert 4.0 < result.elapsed_minutes < 6.0  # ~5 min ago

    def test_d5_invalid_agent_id_returns_failure(self, isolated_root, tmp_path):
        """D.5: invalid agent_id (empty / non-str) → FAILURE (fail-open)."""
        reader = NarrativeTraceReader(trace_log_path=tmp_path / "no.jsonl")
        result = gate_proactive_dm(
            agent_id="",
            now=datetime.now(timezone.utc),
            trace_reader=reader,
        )
        assert result.decision == GateDecision.FAILURE

    def test_d6_invalid_now_returns_failure(self, isolated_root, tmp_path):
        """D.6: invalid now (not datetime) → FAILURE (fail-open)."""
        reader = NarrativeTraceReader(trace_log_path=tmp_path / "no.jsonl")
        result = gate_proactive_dm(
            agent_id="agent_yua",
            now="not a datetime",  # type: ignore
            trace_reader=reader,
        )
        assert result.decision == GateDecision.FAILURE


# ────────────────────────────────────────────────────────────────────
# E. Frozen contract verification
# ────────────────────────────────────────────────────────────────────

class TestSectionE_FrozenContracts:
    """E. Verify 0 frozen contract change."""

    def test_e1_trigger_envelope_frozen_schema(self):
        """E.1: TriggerEnvelope schema unchanged."""
        from src.agency import TriggerEnvelope
        # Original fields per M5.2-F
        import dataclasses
        field_names = {f.name for f in dataclasses.fields(TriggerEnvelope)}
        assert field_names == {
            "trigger_type", "agent_id", "reason", "elapsed_mins",
            "timestamp", "extra",
        }

    def test_e2_agency_run_signature_unchanged(self):
        """E.2: run_agency / Agency.run signature unchanged."""
        from src.agency import run_agency, Agency
        import inspect
        run_sig = inspect.signature(run_agency)
        run_params = list(run_sig.parameters.keys())
        # Original per M5.2-G: state, perception, now, trigger
        assert "state" in run_params
        assert "perception" in run_params
        assert "now" in run_params
        assert "trigger" in run_params
        # NO new inner_life params
        assert not any("inner_life" in p for p in run_params)
        # NO new context params
        assert not any("context" in p for p in run_params)

        # Agency.run
        agency_run_sig = inspect.signature(Agency.run)
        agency_run_params = list(agency_run_sig.parameters.keys())
        # Original: self, perception, now, trigger
        assert "perception" in agency_run_params
        assert "now" in agency_run_params
        assert "trigger" in agency_run_params
        assert not any("inner_life" in p for p in agency_run_params)
        assert not any("context" in p for p in agency_run_params)

    def test_e3_stages_unchanged(self):
        """E.3: Stage 1-4 functions unchanged (signature)."""
        from src.agency import (
            check_eligibility,
            make_decision,
            select_action,
            execute_action_stub,
        )
        import inspect

        # Stage 1
        s1 = inspect.signature(check_eligibility)
        assert list(s1.parameters.keys()) == ["state", "now"]

        # Stage 2
        s2 = inspect.signature(make_decision)
        s2_params = list(s2.parameters.keys())
        assert "eligibility" in s2_params
        assert "perception" in s2_params
        assert "state" in s2_params
        assert "now" in s2_params
        assert "trigger" in s2_params
        # No inner_life param
        assert not any("inner_life" in p for p in s2_params)

        # Stage 3
        s3 = inspect.signature(select_action)
        assert list(s3.parameters.keys()) == ["decision_type"]

        # Stage 4
        s4 = inspect.signature(execute_action_stub)
        assert list(s4.parameters.keys()) == ["action_type"]

    def test_e4_gate_result_is_frozen(self):
        """E.4: GateResult is frozen (immutable)."""
        from dataclasses import FrozenInstanceError
        result = GateResult(
            decision=GateDecision.EMITTED,
            reason="test",
        )
        with pytest.raises(FrozenInstanceError):
            result.decision = GateDecision.GATED  # type: ignore

    def test_e5_4_handlers_unchanged(self):
        """E.5: 4 handlers (AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler) unchanged."""
        from src.agency import (
            AgencyTriggerHandler,
            EventHandler,
            DreamHandler,
            DiaryHandler,
        )
        # All 4 exist + still accept same args
        import inspect
        for cls in [AgencyTriggerHandler, EventHandler, DreamHandler, DiaryHandler]:
            sig = inspect.signature(cls.__init__)
            params = list(sig.parameters.keys())
            # Original pattern: self, agency, state, executor
            assert "agency" in params or "state" in params  # one of them is required
            # NO inner_life param
            assert not any("inner_life" in p for p in params)


# ────────────────────────────────────────────────────────────────────
# F. data_root() isolation
# ────────────────────────────────────────────────────────────────────

class TestSectionF_DataRootIsolation:
    """F. gate uses data_root() correctly, test isolated."""

    def test_f1_default_uses_data_root(self):
        """F.1: gate_proactive_dm default uses data_root() path."""
        # Just verify the default reader is created via data_root()
        # When trace doesn't exist at data_root, returns UNAVAILABLE
        with patch.dict(os.environ, {"SOUL_OS_DATA_DIR": "/tmp/nonexistent_test_dir_xyz"}):
            reset_data_root()
            try:
                result = gate_proactive_dm(
                    agent_id="agent_yua",
                    now=datetime.now(timezone.utc),
                )
                # No trace → UNAVAILABLE (fail-open)
                assert result.decision == GateDecision.UNAVAILABLE
            finally:
                _restore_data_root()


# ────────────────────────────────────────────────────────────────────
# G. Existing scheduler tests still pass (M5.2-G baseline behavior)
# ────────────────────────────────────────────────────────────────────

class TestSectionG_SchedulerBackwardCompat:
    """G. M5.2-G baseline: scheduler still publishes AGENCY_TRIGGER."""

    def test_g1_scheduler_fire_proactive_dm_path_unchanged_when_trace_empty(self, isolated_root, tmp_path):
        """G.1: With empty trace, _fire_proactive_dm flow still publishes AGENCY_TRIGGER."""
        async def _run():
            from src.eventbus.bus import SoulEventBus
            from src.eventbus.schema import EventType
            bus = SoulEventBus()
            await bus.start()
            try:
                captured: List[Any] = []

                async def _capture(e):
                    captured.append(e)

                bus.subscribe(
                    subscriber_id="capture",
                    handler=_capture,
                    event_filter={EventType.AGENCY_TRIGGER},
                )
                scheduler = SoulScheduler(
                    bus=bus,
                    proactive_agents=["agent_yua"],
                    proactive_dm_min_interval_minutes=1,
                    proactive_dm_max_interval_minutes=1,
                    proactive_dm_cooldown_seconds=0,
                    quiet_hours_start=0,
                    quiet_hours_end=0,
                )

                async def noop_cb(agent_id):
                    pass
                scheduler.register_proactive_dm(noop_cb)
                scheduler._all_agents = ["agent_yua"]
                from src.timezone_utils import now_local
                scheduler._next_proactive_dm_time = now_local()

                # M7-longing (Bry 拍板 2026-08-18): 寫一份 24h 前互動的 relationships.json,
                # 讓想念門檻 (LONGING_THRESHOLD=0.3) 通過, 這樣 test 才能測到它真正要測的
                # M5.8-4 inner-life gate (不是被想念 gate 擋在前面)。
                soul_dir = isolated_root / "soul" / "agent_yua"
                soul_dir.mkdir(parents=True, exist_ok=True)
                rel = {
                    "agent_id": "agent_yua",
                    "schema_version": "4.1",
                    "others": {
                        "user_bryan": {
                            "last_interaction_at": (
                                datetime.now(timezone.utc) - timedelta(hours=24)
                            ).isoformat(),
                        }
                    },
                }
                (soul_dir / "relationships.json").write_text(
                    json.dumps(rel, ensure_ascii=False), encoding="utf-8"
                )

                with patch("src.soul.scheduler.random.choice", return_value="agent_yua"):
                    await scheduler._fire_proactive_dm()
                return captured
            finally:
                await bus.stop()

        captured = asyncio.run(_run())
        # M5.2-G baseline behavior: AGENCY_TRIGGER published
        # (gate fail-open = emit when no trace)
        assert len(captured) == 1
        assert captured[0].payload["trigger_type"] == "proactive_dm"
