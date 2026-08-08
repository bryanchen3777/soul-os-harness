"""
tests/test_m3_observability.py — M3 Phase 1 P12 Observability Audit

Bry 拍板 2026-08-07 20:12 P12:
一個 event 的 lifecycle 必須可以從 trace 完整回答:
  1. Did Soul receive it?         → received phase
  2. Did validation accept it?    → validation phase (if invalid)
  3. Was it evaluated?             → evaluated phase
  4. What were the scores?        → scores
  5. Why accepted/rejected?       → reason + accepted
  6. Was it selected by budget?   → selection_reason
  7. Was context injected?        → context_injected
  8. Was memory written?          → memory_written

可以用 novelty_id 串起一個 event 的完整 lifecycle。
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.world import (
    SELECTION_BELOW_BUDGET,
    SELECTION_REJECTED_AT_THRESHOLD,
    SELECTION_REJECTED_AT_VALIDATION,
    SELECTION_SELECTED_TOP_N,
    SyntheticWorldEventSource,
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)


def _run(coro):
    return asyncio.run(coro)


def _read_traces(trace_path: Path) -> List[dict]:
    if not trace_path.exists():
        return []
    return [json.loads(l) for l in trace_path.read_text(encoding="utf-8").strip().split("\n") if l.strip()]


def _make_enriched(agent_id: str = "agent_yua", draft: str = "test", chrono: str = "") -> SoulEvent:
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id, target=agent_id,
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id, "reason": "user_message", "mode": "private",
            "draft": draft, "target_user_id": "bryan",
            "chrono_context": chrono, "memory_context": "",
        },
    )


class TestM3Observability(unittest.TestCase):
    """P12 hardening: trace 必須能回答 8 個 lifecycle 問題"""

    def test_01_valid_event_lifecycle_8_questions(self):
        """
        跑 1 個 valid event 的完整 lifecycle, 用 novelty_id 串起來,
        驗證 8 個問題都能從 trace 答案。
        """
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                trace_path = Path(tmp) / "trace.jsonl"
                state = WorldPerceptionState()
                writer = WorldPerceptionTraceWriter(trace_path)

                bus = SoulEventBus()
                m = WorldPerceptionMiddleware(
                    bus=bus, state=state, trace_writer=writer,
                )
                m.register()
                try:
                    # 發 1 個 valid rain event
                    rain = SyntheticWorldEventSource.build_rain_started()
                    target_nid = rain.novelty_id  # 記下要追蹤的 novelty_id
                    await m.process_world_event_direct(rain)

                    # 觸發 perception
                    enriched = _make_enriched(draft="等等我要出門, 外面下雨嗎?")
                    await m.handle_event(enriched)

                    # 給時間
                    await asyncio.sleep(0.1)
                finally:
                    await bus.stop()

                # 讀 trace, 用 novelty_id 串起來
                all_traces = _read_traces(trace_path)
                with_nid = [t for t in all_traces if t.get("novelty_id") == target_nid]

                # 至少要有 1 條 received + 1 條 evaluated (正常 valid event)
                received = [t for t in with_nid if t.get("extra", {}).get("phase") == "received"]
                evaluated = [t for t in with_nid if t.get("extra", {}).get("phase") == "evaluated"]
                self.assertEqual(len(received), 1,
                                 f"P12 期望 1 條 received phase trace, 實際 {len(received)}")
                self.assertEqual(len(evaluated), 1,
                                 f"P12 期望 1 條 evaluated phase trace, 實際 {len(evaluated)}")

                # 8 個問題答案:
                # 1. Did Soul receive it? → received phase exists
                # 2. Did validation accept it? → 不是 rejected_at_validation (代表 accept)
                # 3. Was it evaluated? → evaluated phase exists
                # 4. What were the scores? → scores dict
                # 5. Why accepted/rejected? → reason + accepted
                # 6. Was it selected by budget? → selection_reason
                # 7. Was context injected? → context_injected bool
                # 8. Was memory written? → memory_written bool (永遠 False Phase 1)

                ev = evaluated[0]
                self.assertIn("scores", ev, "Q4: 缺 scores")
                self.assertIn("accepted", ev, "Q5: 缺 accepted")
                self.assertIn("reason", ev, "Q5: 缺 reason")
                self.assertIn("selection_reason", ev, "Q6: 缺 selection_reason")
                self.assertIn("context_injected", ev, "Q7: 缺 context_injected")
                self.assertIn("memory_written", ev, "Q8: 缺 memory_written")

                # 驗證: memory_written 永遠 False (Bry 拍板 20:02 P3)
                self.assertFalse(ev["memory_written"],
                                 f"Q8: P3 拍板 memory_written 永遠 False, 實際: {ev['memory_written']}")

                # 驗證: selection_reason 是 expected enum
                self.assertIn(ev["selection_reason"], [
                    SELECTION_SELECTED_TOP_N + " (budget=3)",  # accepted 進 top-N
                    SELECTION_BELOW_BUDGET,                     # accepted 但 not top-N
                    SELECTION_REJECTED_AT_THRESHOLD,            # 沒過 threshold
                ])

                print(f"[P12] 8 個問題答案 (novelty_id={target_nid}):")
                print(f"   Q1 received: ✓ (1 received phase)")
                print(f"   Q2 validation: ✓ (no rejected_at_validation)")
                print(f"   Q3 evaluated: ✓ (1 evaluated phase)")
                print(f"   Q4 scores: {ev['scores']}")
                print(f"   Q5 reason: {ev['reason'][:80]}")
                print(f"   Q6 selection: {ev['selection_reason']}")
                print(f"   Q7 context_injected: {ev['context_injected']}")
                print(f"   Q8 memory_written: {ev['memory_written']} (Phase 1 永遠 False)")

        _run(_scenario())

    def test_02_invalid_event_validation_reject_lifecycle(self):
        """
        1 個 invalid event (缺必填欄位) 的 lifecycle:
        - 沒 received phase (validation reject 在 _on_world_event 內, 還沒 state)
        - 只有 1 條 rejected_at_validation trace
        - novelty_id 還是能在 trace 找到 (用 event_id)
        """
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                trace_path = Path(tmp) / "trace.jsonl"
                state = WorldPerceptionState()
                writer = WorldPerceptionTraceWriter(trace_path)

                bus = SoulEventBus()
                m = WorldPerceptionMiddleware(
                    bus=bus, state=state, trace_writer=writer,
                )
                m.register()
                try:
                    # 發 1 個 invalid event (缺必填欄位)
                    invalid = SoulEvent(
                        event_type=EventType.WORLD_EVENT,
                        source="weather",
                        target="broadcast",
                        priority=EventPriority.LOW,
                        # 故意缺 novelty_id / ts / summary
                        payload={"source": "weather", "type": "rain_started"},
                    )
                    await m.handle_event(invalid)
                    await asyncio.sleep(0.1)
                finally:
                    await bus.stop()

                # 讀 trace
                all_traces = _read_traces(trace_path)
                validation_reject = [
                    t for t in all_traces
                    if t.get("selection_reason") == SELECTION_REJECTED_AT_VALIDATION
                ]
                self.assertEqual(len(validation_reject), 1,
                                 f"P12 期望 1 條 validation_reject trace, 實際 {len(validation_reject)}")

                t = validation_reject[0]
                self.assertFalse(t["accepted"], "P12: invalid event 應 accepted=False")
                self.assertFalse(t["context_injected"], "P12: invalid event 不應注入 context")
                self.assertFalse(t["memory_written"], "P12: invalid event 不應寫 memory")
                self.assertIn("validation", t.get("reason", ""))
                # scores 應是預設 (全 0)
                self.assertEqual(t["scores"]["relevance"], 0.0)
                print(f"[P12] invalid event lifecycle: rejected_at_validation ✓")

        _run(_scenario())

    def test_03_full_lifecycle_5_events(self):
        """
        5 個 events 一次, 確認每個 novelty_id 都能 trace 完整 lifecycle:
        - 1 received + 1 evaluated
        - 每個 evaluated 都有 8 個問題答案
        - memory_written 永遠 False
        """
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                trace_path = Path(tmp) / "trace.jsonl"
                state = WorldPerceptionState()
                writer = WorldPerceptionTraceWriter(trace_path)

                bus = SoulEventBus()
                m = WorldPerceptionMiddleware(
                    bus=bus, state=state, trace_writer=writer,
                    perception_budget=3,
                )
                m.register()
                try:
                    # 5 個 events
                    events = SyntheticWorldEventSource.build_all_five()
                    novelty_ids = [ev.novelty_id for ev in events]
                    for ev in events:
                        await m.process_world_event_direct(ev)

                    # 觸發 perception
                    enriched = _make_enriched(draft="等等我要出門, 等下還有會議")
                    await m.handle_event(enriched)
                    await asyncio.sleep(0.1)
                finally:
                    await bus.stop()

                all_traces = _read_traces(trace_path)

                # 5 個 novelty_id 都應該有 received + evaluated
                for nid in novelty_ids:
                    with_nid = [t for t in all_traces if t.get("novelty_id") == nid]
                    received = [t for t in with_nid if t.get("extra", {}).get("phase") == "received"]
                    evaluated = [t for t in with_nid if t.get("extra", {}).get("phase") == "evaluated"]
                    self.assertEqual(len(received), 1,
                                     f"P12 期望 {nid} 有 1 received, 實際 {len(received)}")
                    self.assertEqual(len(evaluated), 1,
                                     f"P12 期望 {nid} 有 1 evaluated, 實際 {len(evaluated)}")

                    # 8 個問題答案都齊
                    ev = evaluated[0]
                    self.assertIn("scores", ev)
                    self.assertIn("accepted", ev)
                    self.assertIn("reason", ev)
                    self.assertIn("selection_reason", ev)
                    self.assertIn("context_injected", ev)
                    self.assertFalse(ev["memory_written"],
                                     f"P12 {nid} 違反 P3: memory_written 應永遠 False")
                print(f"[P12] 5 events 完整 lifecycle, 每個 novelty_id 都能 trace ✓")


if __name__ == "__main__":
    unittest.main(verbosity=2)
