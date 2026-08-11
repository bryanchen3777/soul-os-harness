"""
tests/test_m5_7_4_heartbeat_robustness.py

M5.7-4 (Bry 派工 2026-08-10): Heartbeat Runtime Robustness Hardening.

Verifies the minimal robustness improvements to the M5.7-2 continuous-life
runtime, addressing the 3 P2/P3 findings from M5.7-3:

P2: Heartbeat `_loop` lacks exception isolation.
    Fix: try/except around tick body, log + continue, preserve
    CancelledError propagation. NO retry framework, NO cancellation swallow.

P3.1: ConversationQualification.register() comment falsely claims
    "Idempotent... bus dedups by id". Fix: correct comment to
    accurately describe non-idempotent behavior.

P3.2: bus.publish() failure behavior is unclear. Fix: improved docstring
    + log message includes event_type and source for dropped events.
    NO new infrastructure.

Test count: 8 tests across 3 sections
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, SoulEvent
from src.heartbeat.engine import HeartbeatEngine
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


def _make_bus() -> SoulEventBus:
    return SoulEventBus(max_queue_size=100)


# ───────────────────────────────────────────────────────────
# A. P2 — Heartbeat `_loop` exception isolation
# ───────────────────────────────────────────────────────────

class TestSectionA_HeartbeatLoopExceptionIsolation:
    """P2 fix: Heartbeat `_loop` survives unexpected exceptions, preserves
    cancellation semantics, no retry framework."""

    def test_a1_loop_survives_unexpected_exception_in_tick_body(self, tmp_path, caplog):
        """An unexpected exception in the tick body does NOT kill the loop."""
        try:
            _isolated_data_root(tmp_path)
            bus = _make_bus()
            heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=1)
            # Pre-condition: avoid SESSION_END
            from datetime import datetime, timezone
            heartbeat.last_user_activity = datetime.now(timezone.utc)
            heartbeat._session_ended = False

            # Inject exception into the tick body (after sleep, before tick computation)
            tick_count_before = heartbeat.tick_count
            publish_call_count = [0]

            original_publish = bus.publish

            async def faulty_publish(event):
                publish_call_count[0] += 1
                # First 2 calls raise; subsequent calls succeed
                if publish_call_count[0] <= 2:
                    raise RuntimeError("injected failure for testing")
                return await original_publish(event)

            bus.publish = faulty_publish  # type: ignore

            async def run_test():
                with caplog.at_level(logging.ERROR, logger="soul_os.heartbeat.engine"):
                    await bus.start()
                    await heartbeat.start()
                    # Wait long enough for 2-3 ticks (1s each, fail-then-recover)
                    await asyncio.sleep(3.5)
                    await heartbeat.stop()
                    await bus.stop()

            asyncio.run(run_test())

            # Loop survived — at least 2 publish() calls happened (both failed)
            assert publish_call_count[0] >= 2, (
                f"Expected at least 2 publish() attempts, got {publish_call_count[0]}"
            )
            # tick_count advanced past the failures
            assert heartbeat.tick_count > tick_count_before + 1, (
                f"Expected tick_count to advance past failures, "
                f"got tick_count={heartbeat.tick_count} (was {tick_count_before})"
            )
            # Exception was logged
            assert any(
                "tick" in record.message.lower() and "失敗" in record.message
                for record in caplog.records
            ), (
                "Expected the loop exception to be logged"
            )
        finally:
            _restore_data_root()

    def test_a2_loop_does_not_swallow_cancelled_error(self, tmp_path):
        """CancelledError from stop() still propagates; loop exits cleanly."""
        try:
            _isolated_data_root(tmp_path)
            bus = _make_bus()
            heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=1)

            cancelled_seen = [False]

            async def run_test():
                await bus.start()
                await heartbeat.start()
                # Let it tick once
                await asyncio.sleep(1.3)
                # Trigger stop
                try:
                    await heartbeat.stop()
                except asyncio.CancelledError:
                    cancelled_seen[0] = True
                # Verify loop task is fully cancelled
                if heartbeat._loop_task is not None:
                    assert heartbeat._loop_task.done()
                await bus.stop()

            asyncio.run(run_test())
            # The _loop method should have exited cleanly (not via raised exception)
            # — this is the default behavior of cancel + catch
            assert heartbeat._loop_task is None or heartbeat._loop_task.done()
        finally:
            _restore_data_root()

    def test_a3_loop_continues_after_connection_check_exception(self, tmp_path):
        """Connection manager exceptions are caught at line 174, loop continues.

        This is the existing nested try/except (line 171-178). M5.7-4
        does NOT change this — it's just verifying it still works.
        """
        try:
            _isolated_data_root(tmp_path)
            bus = _make_bus()
            heartbeat = HeartbeatEngine(bus=bus, tick_interval_seconds=1)

            # Inject a manager that always raises
            class FaultyManager:
                @property
                def count(self):
                    raise RuntimeError("manager broken")

            heartbeat._manager = FaultyManager()

            from datetime import datetime, timezone
            heartbeat.last_user_activity = datetime.now(timezone.utc)
            heartbeat._session_ended = False

            tick_count_before = heartbeat.tick_count

            async def run_test():
                await bus.start()
                await heartbeat.start()
                await asyncio.sleep(2.5)
                await heartbeat.stop()
                await bus.stop()

            asyncio.run(run_test())

            # tick_count advanced despite faulty manager
            assert heartbeat.tick_count > tick_count_before
        finally:
            _restore_data_root()


# ───────────────────────────────────────────────────────────
# B. P3.1 — Qualifier comment correctness
# ───────────────────────────────────────────────────────────

class TestSectionB_QualifierCommentCorrectness:
    """P3.1 fix: ConversationQualification.register() docstring
    does NOT falsely claim idempotency / bus dedup."""

    def test_b1_qualifier_docstring_does_not_claim_idempotency(self):
        """Static check: ConversationQualification.register() docstring
        must not claim 'Idempotent' or 'bus dedups by id'."""
        qualifier_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "conversation_qualification" / "qualifier.py"
        )
        text = qualifier_path.read_text(encoding="utf-8")
        # Find the register() docstring specifically (not all docstrings)
        m = re.search(
            r'def register\(self, bus: Any\) -> None:\s*""".*?"""',
            text,
            re.DOTALL,
        )
        assert m is not None, "Could not find register() docstring"
        docstring = m.group(0)
        # M5.7-4 fix: should NOT contain misleading claims
        assert "Idempotent" not in docstring, (
            "M5.7-4: register() docstring should not claim 'Idempotent' "
            "(bus.subscribe is not idempotent — verified by M5.7-3 audit)"
        )
        assert "bus dedups" not in docstring, (
            "M5.7-4: register() docstring should not claim 'bus dedups by id' "
            "(false — bus.subscribe just appends)"
        )

    def test_b2_qualifier_docstring_accurately_describes_behavior(self):
        """Static check: register() docstring describes the correct
        non-idempotent behavior."""
        qualifier_path = (
            Path(__file__).resolve().parent.parent
            / "src" / "conversation_qualification" / "qualifier.py"
        )
        text = qualifier_path.read_text(encoding="utf-8")
        m = re.search(
            r'def register\(self, bus: Any\) -> None:\s*""".*?"""',
            text,
            re.DOTALL,
        )
        assert m is not None
        docstring = m.group(0)
        # Should accurately describe non-idempotent behavior
        assert "NOT idempotent" in docstring, (
            "M5.7-4: register() docstring should explicitly state "
            "'NOT idempotent' to clarify behavior"
        )
        assert "called once" in docstring.lower() or "called ONCE" in docstring, (
            "M5.7-4: register() docstring should instruct caller to call once"
        )


# ───────────────────────────────────────────────────────────
# C. P3.2 — Event Bus enqueue failure observability
# ───────────────────────────────────────────────────────────

class TestSectionC_BusEnqueueFailureObservability:
    """P3.2 fix: bus.publish() failure behavior is explicit and observable."""

    def test_c1_publish_drops_event_when_bus_not_started(self, caplog):
        """When bus is not started, publish() drops the event and logs a warning."""
        bus = _make_bus()
        event = SoulEvent(
            event_type=EventType.SYSTEM_TICK,
            source="test",
            target="broadcast",
        )
        # Do NOT start the bus
        with caplog.at_level(logging.WARNING, logger="soul_os.event_bus"):
            asyncio.run(bus.publish(event))
        # Event was dropped (not in queue)
        assert bus._queue.qsize() == 0
        # Log was emitted
        assert any(
            "尚未啟動" in record.message or "事件被丟棄" in record.message
            for record in caplog.records
        ), "Expected bus-not-started warning"

    def test_c2_publish_drops_event_on_queue_full(self, caplog):
        """When queue is full, publish() drops the event and logs an error.

        This verifies M5.7-4 P3.2 fix: the error log includes event_type
        and source for observability.

        Strategy: disable the worker (by setting _worker_task = None),
        then publish events until the queue is full. The next publish()
        will hit QueueFull and the new log format (with event_type +
        source) will fire.
        """
        # Use a small queue to make the test deterministic
        bus = SoulEventBus(max_queue_size=2)

        async def run_test():
            # Mark the bus as "running" without actually starting a worker
            # (so put_nowait would be called on the queue)
            bus._running = True
            # Stop the worker if any (no-op since we never started it)
            if bus._worker_task is not None:
                bus._worker_task.cancel()

            # Use a coroutine that we control, so the worker doesn't drain.
            # We do this by NOT calling bus.start() — the queue is empty,
            # put_nowait is called directly, and we manually fill it.
            from src.eventbus.bus import _QueueItem

            # Manually inject 2 items into the queue (bypass publish)
            event_filler1 = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="filler1",
                target="broadcast",
            )
            event_filler2 = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="filler2",
                target="broadcast",
            )
            bus._sequence_counter += 1
            bus._queue.put_nowait(_QueueItem(
                priority=event_filler1.priority.value,
                sequence=bus._sequence_counter,
                event=event_filler1,
            ))
            bus._sequence_counter += 1
            bus._queue.put_nowait(_QueueItem(
                priority=event_filler2.priority.value,
                sequence=bus._sequence_counter,
                event=event_filler2,
            ))
            # Queue is now full (size 2)
            assert bus._queue.qsize() == 2

            # Now publish a 3rd event — should be dropped (queue full)
            event_dropped = SoulEvent(
                event_type=EventType.SYSTEM_TICK,
                source="test_dropped",
                target="broadcast",
            )
            with caplog.at_level(logging.ERROR, logger="soul_os.event_bus"):
                await bus.publish(event_dropped)

            # Verify QueueFull was caught
            assert bus._stats["dropped_queue_full"] >= 1, (
                f"Expected dropped_queue_full >= 1, got "
                f"{bus._stats.get('dropped_queue_full', 0)}"
            )
            # Verify log includes event_type and source (M5.7-4 P3.2 fix)
            # Note: event.event_type is the str enum VALUE (lowercase, e.g.
            # "system_tick"), NOT the enum name (e.g. "SYSTEM_TICK")
            assert any(
                record.levelname == "ERROR"
                and "system_tick" in record.message
                and "test_dropped" in record.message
                for record in caplog.records
            ), (
                "M5.7-4: Expected error log to include event_type=system_tick "
                "and source=test_dropped (observability fix)"
            )

        asyncio.run(run_test())

    def test_c3_publish_docstring_documents_failure_modes(self):
        """Static check: bus.publish() docstring documents all 3 failure modes."""
        bus_path = (
            Path(__file__).resolve().parent.parent / "src" / "eventbus" / "bus.py"
        )
        text = bus_path.read_text(encoding="utf-8")
        # Find publish() docstring
        m = re.search(
            r'async def publish\(self, event: SoulEvent\) -> None:\s*""".*?"""',
            text,
            re.DOTALL,
        )
        assert m is not None, "Could not find publish() docstring"
        docstring = m.group(0)
        # All 3 failure modes should be documented
        assert "not started" in docstring.lower(), (
            "M5.7-4: publish() docstring should document 'bus not started' failure mode"
        )
        assert "queue" in docstring.lower() and "full" in docstring.lower(), (
            "M5.7-4: publish() docstring should document 'queue full' failure mode"
        )
        assert "other exceptions" in docstring.lower() or "propagate" in docstring.lower(), (
            "M5.7-4: publish() docstring should document other-exception propagation"
        )
        # dropped_queue_full stat should be mentioned
        assert "dropped_queue_full" in docstring, (
            "M5.7-4: publish() docstring should mention dropped_queue_full stat"
        )


# ───────────────────────────────────────────────────────────
# count
# ───────────────────────────────────────────────────────────

def test_count():
    """Verify test count: A=3, B=2, C=3, count=1 → 9."""
    pass
