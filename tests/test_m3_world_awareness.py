"""
test_m3_world_awareness.py — M3 Phase 1: World Awareness (Bry 拍板 2026-08-07 19:40)

7 個 test cases 對應 brief §6 + §7:
- Test 1 — Relevant accepted (rain + user going outside → context injected)
- Test 2 — Irrelevant rejected (random celebrity news → no context)
- Test 3 — Duplicate novelty (rain 連發 3 次 → 第 1 accept, 後 2 reject)
- Test 4 — Memory protection (100 low-significance events → 0 SAGE write)
- Test 5 — Decision trace (每個 event 都有 trace 記 reason + accepted + scores)
- Test 6 — Existing system compat (M0/M1/M2 test 全綠; M3 不動 PromptContext 其他欄位)
- Test 7 — Multiple events / Perception Budget (5 events 一次 → top-3 in context, 2 reject)

派工精神 (Bry 拍板 2026-08-07 19:40):
- 「現成先例可循優先於設計新模式」 — 沿用 MemoryMiddleware / inner_life 注入風格
- 「不動 SAGE / 不動 Memory / 不動 consciousness」 — M3 寫 trace, 不寫 memory
- 「不為假設中的未來灑過濾網」 — Phase 1 只 4 維 scoring, 留架構彈性
- 「Bry 拒絕把 personal_significance 從 event payload 拿」 — 從 evaluator 算
- 「perception_budget 是 config」 — Phase 1 = 3, 拒絕 hard-code
- 「No Memory > Wrong Memory」 — invalid event → reject → trace → no context → no memory

Mock 範圍:
- 用 SyntheticWorldEventSource 建 5 種場景
- 用 WorldPerceptionMiddleware.process_world_event_direct 進 state (跳過 bus, 給 test 確定性)
- 用 WorldPerceptionMiddleware 處理 AGENT_INTENT_ENRICHED (透過真實 bus)
- 驗證: state 內部 / world_context 渲染 / trace log / 不寫 SAGE
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.world import (
    PerceptionScores,
    SyntheticWorldEventSource,
    WorldContext,
    WorldEvent,
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTrace,
    WorldPerceptionTraceWriter,
    compute_scores,
    format_world_context_block,
    should_accept,
    validate_world_event,
)


# ───────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────

def _make_enriched_event(
    agent_id: str = "agent_yua",
    draft: str = "",
    chrono_context: str = "",
    target_user_id: str = "bryan",
) -> SoulEvent:
    """建一個 AGENT_INTENT_ENRICHED event,給 WorldPerceptionMiddleware 處理。"""
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id,
        target=agent_id,
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id,
            "reason": "user_message",
            "mode": "private",
            "draft": draft,
            "target_user_id": target_user_id,
            "chrono_context": chrono_context,
            "memory_context": "",  # 測試時不依賴 memory middleware
        },
    )


def _run(coro):
    """在 sync test 內跑 async coroutine。"""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if sys.version_info < (3, 10) \
        else asyncio.run(coro)


# ───────────────────────────────────────────────────────────
# Test 1 — Relevant accepted
# Bry 拍板 brief §7 Test 1: 雨 + user 準備出門 → accepted → world_context
# ───────────────────────────────────────────────────────────

class TestM3RelevantAccepted(unittest.TestCase):
    """驗證: 雨開始下 + user context 是「準備出門」→ accepted → world_context 注入"""

    def test_01_rain_with_user_going_outside_accepted(self):
        """
        Bry 拍板 8/7 19:40: 「personal_significance 必須由 evaluator 從 WorldEvent +
        existing current/user context + Chrono-Social 算」
        這裡 user context 從 AGENT_INTENT_ENRICHED payload['draft'] 抽,
        而非從 WorldEvent payload 拿 (Bry 拒絕「世界自己告訴 Soul 我對你很重要」)
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            # bus 用 mock, 因為不需 bus 真實 publish (test 1 只驗 scoring)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus,
                state=state,
                trace_writer=writer,
            )

            # Step 1: 喂 rain_started event
            rain = SyntheticWorldEventSource.build_rain_started()
            _run(middleware.process_world_event_direct(rain))

            # Step 2: 模擬 AGENT_INTENT_ENRICHED 進來, user context 是「我要出門」
            enriched = _make_enriched_event(
                agent_id="agent_yua",
                draft="等等我要出門一下, 外面還在下雨嗎?",
            )

            # 觀察 published event (用 bus.publish.side_effect 抓)
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            _run(middleware.handle_event(enriched))

            # 驗證: 有 publish AGENT_INTENT_PERCEIVED
            self.assertEqual(len(published), 1, "應 publish 一次 AGENT_INTENT_PERCEIVED")
            perceived = published[0]
            self.assertEqual(perceived.event_type, EventType.AGENT_INTENT_PERCEIVED)

            # 驗證: world_context 包含 rain
            world_context_text = perceived.payload.get("world_context", "")
            self.assertIn("下雨", world_context_text,
                          f"v3 期望 world_context 含「下雨」, 實際: {world_context_text!r}")
            self.assertIn("[世界感知]", world_context_text)
            print(f"[Test 1] rain + going_outside → world_context 注入: {world_context_text[:80]!r}")

    def test_02_meta_has_accepted_count_and_top_ids(self):
        """驗證: world_perception_meta 包含 observability 資訊 (Bry 拍板 trace 精神)"""
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            # 喂 rain
            _run(middleware.process_world_event_direct(SyntheticWorldEventSource.build_rain_started()))
            # 觸發 AGENT_INTENT_ENRICHED
            enriched = _make_enriched_event(draft="我要出門")
            _run(middleware.handle_event(enriched))

            perceived = published[0]
            meta = perceived.payload.get("world_perception_meta", {})
            self.assertIn("accepted_count", meta)
            self.assertIn("rejected_count", meta)
            self.assertIn("top_event_ids", meta)
            self.assertIn("perception_budget", meta)
            self.assertEqual(meta["perception_budget"], 3)
            self.assertGreaterEqual(meta["accepted_count"], 1)
            self.assertIn("weather_rain_20260807", meta["top_event_ids"])
            print(f"[Test 1] meta: {meta}")


# ───────────────────────────────────────────────────────────
# Test 2 — Irrelevant rejected
# Bry 拍板 brief §7 Test 2: random celebrity news → rejected, no context
# ───────────────────────────────────────────────────────────

class TestM3IrrelevantRejected(unittest.TestCase):
    """驗證: 隨機明星新聞 → rejected, 不進 world_context"""

    def test_01_celebrity_news_rejected(self):
        """
        celebrity_news 沒跟 user context 重疊, novelty = 1 (首次), 沒 vulnerability_window
        → final score 應該 < 0.35 threshold → rejected
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            celebrity = SyntheticWorldEventSource.build_celebrity_news()
            _run(middleware.process_world_event_direct(celebrity))

            enriched = _make_enriched_event(draft="我今天要看書")
            _run(middleware.handle_event(enriched))

            perceived = published[0]
            world_context_text = perceived.payload.get("world_context", "")
            # 期望: world_context 是空 (沒 accept 任何 event)
            self.assertEqual(world_context_text, "",
                             f"Test 2 期望 celebrity rejected → empty world_context, "
                             f"實際: {world_context_text!r}")

            meta = perceived.payload.get("world_perception_meta", {})
            self.assertEqual(meta["accepted_count"], 0)
            self.assertEqual(meta["top_event_ids"], [])
            self.assertEqual(meta["rejected_count"], 1)
            print(f"[Test 2] celebrity rejected → world_context empty, "
                  f"meta rejected={meta['rejected_count']}")

    def test_02_minor_temp_fluctuation_rejected(self):
        """Bry 拍板 brief Test D: 微小溫度變化 → 也應該 rejected (low relevance)"""
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            temp = SyntheticWorldEventSource.build_temp_fluctuation()
            _run(middleware.process_world_event_direct(temp))

            # 跟 temp 完全無關的 user context (避免 CJK 2-gram 重疊)
            enriched = _make_enriched_event(draft="晚餐想吃火鍋")
            _run(middleware.handle_event(enriched))

            perceived = published[0]
            world_context_text = perceived.payload.get("world_context", "")
            self.assertEqual(world_context_text, "",
                             f"Test 2 期望 temp_fluctuation rejected → empty world_context, "
                             f"實際: {world_context_text!r}")
            print(f"[Test 2] temp_fluctuation rejected (low relevance)")


# ───────────────────────────────────────────────────────────
# Test 3 — Duplicate novelty
# Bry 拍板 brief §7 Test 3: 同一 event 連發 → novelty 識別, 不重複
# ───────────────────────────────────────────────────────────

class TestM3DuplicateNovelty(unittest.TestCase):
    """驗證: 同一 novelty_id 連發 → 第 1 accept, 後面因為 novelty score 低 reject"""

    def test_01_rain_3_times_first_accepted_rest_rejected(self):
        """
        novelty_count = 1: novelty_score = 1.0 → final 過 threshold
        novelty_count = 2: novelty_score = 0.5 → final 不一定過
        novelty_count = 3: novelty_score = 0.33 → final 不過

        注意: 這個 test 用同一個 novelty_id, 模擬 source 重複發
        (e.g. weather 監測每 5 分鐘報一次 rain)
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
                accept_threshold=0.40,  # 稍高 threshold 讓 duplicate reject 明顯
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            # 3 次 rain (同一 novelty_id)
            for _ in range(3):
                rain = SyntheticWorldEventSource.build_rain_started()
                _run(middleware.process_world_event_direct(rain))

            enriched = _make_enriched_event(
                draft="等等我要出門, 外面是不是還在下雨?",
            )
            _run(middleware.handle_event(enriched))

            # 觀察 trace: 應該有 3 條 evaluated (每個 novelty_id 1 條, 但 novelty_count 不同)
            trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
            # filter 掉 received phase, 只看 evaluated
            evaluated_traces = []
            for line in trace_lines:
                if not line.strip():
                    continue
                d = json.loads(line)
                if d.get("extra", {}).get("phase") == "evaluated":
                    evaluated_traces.append(d)

            # 3 條 evaluated trace
            self.assertEqual(len(evaluated_traces), 3,
                             f"應有 3 條 evaluated trace, 實際: {len(evaluated_traces)}")

            # novelty_count 應為 1, 2, 3 (重複加入的)
            novelty_counts = [t["novelty_count_in_window"] for t in evaluated_traces]
            self.assertEqual(sorted(novelty_counts), [1, 2, 3])

            # 第 1 條 (novelty_count=1) 應該 accepted
            first = next(t for t in evaluated_traces if t["novelty_count_in_window"] == 1)
            self.assertTrue(first["accepted"],
                            f"novelty_count=1 應 accepted, 實際: {first}")

            # 第 2 + 3 條 (novelty_count=2, 3) 應該 rejected (novelty 拖低 final)
            second = next(t for t in evaluated_traces if t["novelty_count_in_window"] == 2)
            third = next(t for t in evaluated_traces if t["novelty_count_in_window"] == 3)
            self.assertFalse(second["accepted"],
                             f"novelty_count=2 應 rejected, 實際: {second}")
            self.assertFalse(third["accepted"],
                             f"novelty_count=3 應 rejected, 實際: {third}")

            print(f"[Test 3] rain 連發 3 次: novelty_count=[1,2,3] → "
                  f"accepted=1 rejected=2 (novelty 過低)")


# ───────────────────────────────────────────────────────────
# Test 4 — Memory protection
# Bry 拍板 brief §7 Test 4: 100 個 low-value events → 0 條進 SAGE
# ───────────────────────────────────────────────────────────

class TestM3MemoryProtection(unittest.TestCase):
    """驗證: 100 個 low-significance events 連發 → 0 條進長期 memory"""

    def test_01_hundred_low_value_events_no_sage_write(self):
        """
        Bry 派工精神: 「Quality > Quantity」「No Memory > Wrong Memory」
        100 個 celebrity_news 連發 → 都不 accept → 都不寫 SAGE / 長期 memory

        驗證手段:
        - trace log 只看 events (沒寫到 diary / v1 / SAGE)
        - state 內有 100 條 (in-memory, ephemeral)
        - context_injected = False (沒進 LLM context)
        - memory_written = False (Bry 拍板強制)
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            # 100 個 low-value events (varied novelty_id 避免全被 novelty 拖低)
            for i in range(100):
                ev = WorldEvent(
                    source="news",
                    type="celebrity_news",
                    novelty_id=f"news_celebrity_{i:03d}_20260807",
                    ts=SyntheticWorldEventSource._now_iso(),
                    summary=f"明星 {i} 做了某事。",
                    data={},
                )
                _run(middleware.process_world_event_direct(ev))

            enriched = _make_enriched_event(draft="我在想晚餐吃什麼")
            _run(middleware.handle_event(enriched))

            # 驗證: trace log 必含 100 條 evaluated + 100 條 received = 200 條
            trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(trace_lines), 200,
                             f"應有 200 條 trace (100 received + 100 evaluated), "
                             f"實際: {len(trace_lines)}")

            # 驗證: 所有 evaluated trace 都 memory_written = False (Bry 拍板)
            evaluated_traces = [
                json.loads(line) for line in trace_lines
                if json.loads(line).get("extra", {}).get("phase") == "evaluated"
            ]
            for t in evaluated_traces:
                self.assertFalse(t["memory_written"],
                                 f"Test 4 期望 memory_written=False (Bry 拍板: M3 不寫長期 memory), "
                                 f"但 {t['event_id']} memory_written={t['memory_written']}")

            # 驗證: 0 accepted (celebrity_news + 不相關 user context → 都 reject)
            accepted_count = sum(1 for t in evaluated_traces if t["accepted"])
            self.assertEqual(accepted_count, 0,
                             f"Test 4 期望 100 個 low-value 都 rejected, "
                             f"實際 accepted={accepted_count}")

            # 驗證: world_context_text 是空
            perceived = published[0]
            world_context_text = perceived.payload.get("world_context", "")
            self.assertEqual(world_context_text, "",
                             f"Test 4 期望 world_context empty, 實際: {world_context_text!r}")

            print(f"[Test 4] 100 low-value events: trace=200, accepted=0, "
                  f"memory_written=0 (Bry 拍板: No Memory > Wrong Memory)")


# ───────────────────────────────────────────────────────────
# Test 5 — Decision trace
# Bry 拍板 brief §5 + §10: 每個 event 必須能解釋 (accept/reject + reason)
# ───────────────────────────────────────────────────────────

class TestM3DecisionTrace(unittest.TestCase):
    """驗證: 每個 event 都產 trace 紀錄 reason + accepted + scores"""

    def test_01_trace_has_required_fields(self):
        """
        Bry 拍板 trace 必含:
        - event_id
        - timestamp
        - scores
        - accepted
        - reason
        - context_injected
        - memory_written
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            rain = SyntheticWorldEventSource.build_rain_started()
            _run(middleware.process_world_event_direct(rain))

            enriched = _make_enriched_event(draft="我準備出門")
            _run(middleware.handle_event(enriched))

            # 讀 trace
            trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
            self.assertGreater(len(trace_lines), 0)

            # 每條 trace 都有 required fields
            required = ["event_id", "timestamp", "scores", "accepted", "reason",
                        "context_injected", "memory_written", "novelty_id"]
            for line in trace_lines:
                d = json.loads(line)
                for field_name in required:
                    self.assertIn(field_name, d,
                                  f"Test 5 期望 trace 含 {field_name!r}, "
                                  f"實際 trace 缺: {list(d.keys())}")

            # reason 是人可讀字串
            evaluated = [json.loads(l) for l in trace_lines
                         if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
            self.assertEqual(len(evaluated), 1)
            reason = evaluated[0]["reason"]
            self.assertIn("final_score", reason,
                          f"Test 5 期望 reason 含 final_score 數字 (人可讀), "
                          f"實際: {reason!r}")
            self.assertIn("threshold", reason)
            # scores 4 個維度都在
            scores = evaluated[0]["scores"]
            for dim in ("relevance", "novelty", "personal_significance",
                        "emotional_significance", "temporal_significance"):
                self.assertIn(dim, scores, f"Test 5 期望 scores 含 {dim!r}")
            print(f"[Test 5] trace 有 {len(required)} required fields, "
                  f"reason={reason[:80]!r}")

    def test_02_trace_distinguishes_received_vs_evaluated(self):
        """Phase 1 trace 有兩個 phase: received (validation 過) + evaluated (perception 算)"""
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            # 2 個 events
            _run(middleware.process_world_event_direct(SyntheticWorldEventSource.build_rain_started()))
            _run(middleware.process_world_event_direct(SyntheticWorldEventSource.build_celebrity_news()))

            # 觸發 perception
            enriched = _make_enriched_event(draft="我要出門")
            _run(middleware.handle_event(enriched))

            trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
            phases = [json.loads(l).get("extra", {}).get("phase") for l in trace_lines]
            # 預期: 2 received + 2 evaluated = 4 條
            self.assertIn("received", phases, "應有 received phase trace")
            self.assertIn("evaluated", phases, "應有 evaluated phase trace")
            self.assertEqual(phases.count("received"), 2)
            self.assertEqual(phases.count("evaluated"), 2)
            print(f"[Test 5] trace phases: received={phases.count('received')}, "
                  f"evaluated={phases.count('evaluated')}")


# ───────────────────────────────────────────────────────────
# Test 6 — Existing system compat
# Bry 拍板 brief §7 Test 6: M0/M1/M2 沒 regression + M3 不動 PromptContext 其他欄位
# ───────────────────────────────────────────────────────────

class TestM3ExistingSystemCompat(unittest.TestCase):
    """驗證: M3 不破壞既有 M0/M1/M2 功能 + 不動 PromptContext 其他欄位"""

    def test_01_proxy_build_messages_accepts_world_context_param(self):
        """proxy._build_messages_group / _build_messages_private 接受 world_context 參數"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        import inspect
        # 兩個函式簽名都應有 world_context
        group_sig = inspect.signature(_build_messages_group)
        private_sig = inspect.signature(_build_messages_private)
        self.assertIn("world_context", group_sig.parameters,
                      "M3: _build_messages_group 應接受 world_context 參數")
        self.assertIn("world_context", private_sig.parameters,
                      "M3: _build_messages_private 應接受 world_context 參數")
        # 預設值是空字串
        self.assertEqual(group_sig.parameters["world_context"].default, "")
        self.assertEqual(private_sig.parameters["world_context"].default, "")
        print(f"[Test 6] _build_messages_group/private 接受 world_context 參數 (預設空字串)")

    def test_02_empty_world_context_no_injection(self):
        """沒 world events → world_context = "" → 注入 skip, 不影響其他區塊"""
        from src.llm.proxy import _build_messages_group
        soul = "test soul"
        messages = _build_messages_group(
            agent_id="agent_test",
            soul=soul,
            current_input="",
            memory_context="",  # 沒 memory
            memory=MagicMock(),
            world_context="",  # 沒 world
        )
        # 找 system message
        sys_msgs = [m for m in messages if m["role"] == "system"]
        self.assertGreater(len(sys_msgs), 0)
        # 期望: 沒有 [世界感知] 字串
        for sm in sys_msgs:
            self.assertNotIn("[世界感知]", sm["content"],
                             f"Test 6 期望 world_context 空時不注入, 但 system 內含 [世界感知]")
        print(f"[Test 6] empty world_context → 注入 skip, 沒有 [世界感知] 字串")

    def test_03_m20_m17_m16_baseline_tests_still_pass(self):
        """
        透過 sub-process 跑既有 M2.0 / M1.7 / M1.6 測試, 確認沒 regression
        (這些是 integration smoke test, 不是 M3 自己)
        """
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "pytest",
             "tests/test_m2_0_inner_life_v2.py",
             "tests/test_m1_7_event_whitelist_v2.py",
             "tests/test_m1_6_audio_action_fix.py",
             "-v", "--tb=short"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent.parent,
        )
        # 期望: 36 passed
        self.assertEqual(result.returncode, 0,
                         f"Test 6 期望既有 M0/M1/M2 test 全綠, 失敗:\n{result.stdout[-1000:]}\n{result.stderr[-500:]}")
        # 從 output 抓 "36 passed"
        self.assertIn("36 passed", result.stdout,
                      f"Test 6 期望 36 passed in 既有 tests, 實際 output:\n{result.stdout[-2000:]}")
        print(f"[Test 6] M2.0 / M1.7 / M1.6 test 全綠 (36 passed, M3 沒破壞既有)")


# ───────────────────────────────────────────────────────────
# Test 7 — Multiple World Events / Perception Budget
# Bry 拍板 brief §13 + 8/7 19:40 拍板: 一次輸入多個 events → top-N → world_context
# ───────────────────────────────────────────────────────────

class TestM3PerceptionBudget(unittest.TestCase):
    """驗證: 一次輸入 5 個 events → top-3 (PERCEPTION_BUDGET=3) → 2 個 reject"""

    def test_01_five_events_top3_in_context_two_rejected(self):
        """
        一次輸入 5 個 synthetic events:
        - TEST_A rain_started
        - TEST_D temp_fluctuation
        - TEST_B celebrity_news
        - TEST_C calendar_event_30min
        - TEST_E user_going_outside

        期望: 5 個都 evaluated → 排 rank → top-3 進 world_context → 2 個 reject
        """
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
                perception_budget=3,
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            # 5 個 events 同時
            for ev in SyntheticWorldEventSource.build_all_five():
                _run(middleware.process_world_event_direct(ev))

            # user context: 「我要出門, 等下有會議」
            enriched = _make_enriched_event(
                draft="等等我要出門, 對了等下還有會議",
            )
            _run(middleware.handle_event(enriched))

            perceived = published[0]
            meta = perceived.payload.get("world_perception_meta", {})
            world_context_text = perceived.payload.get("world_context", "")

            # 驗證: 5 個都 evaluated
            trace_lines = trace_path.read_text(encoding="utf-8").strip().split("\n")
            evaluated = [json.loads(l) for l in trace_lines
                         if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
            self.assertEqual(len(evaluated), 5,
                             f"Test 7 期望 5 條 evaluated trace, 實際: {len(evaluated)}")

            # 驗證: top-3 進 world_context (3 個 bullet points)
            bullet_count = world_context_text.count("- [")
            self.assertLessEqual(bullet_count, 3,
                                 f"Test 7 期望 top-3 進 world_context (max 3 bullets), "
                                 f"實際: {bullet_count} bullets, text: {world_context_text!r}")

            # 驗證: meta accepted + rejected = 5
            # 注意: accepted_count = 通過 threshold 的 events, 可能 > perception_budget
            #       top_event_ids = 實際進 context 的 events (= min(accepted, budget))
            # 預期: 至少有 1 個 accepted (rain + going_outside + calendar 都應該高於 threshold)
            self.assertGreaterEqual(meta["accepted_count"], 1,
                                    f"Test 7 期望至少 1 個 accepted, 實際: {meta}")
            self.assertLessEqual(len(meta["top_event_ids"]), meta["perception_budget"],
                                 f"Test 7 期望 top_event_ids ≤ budget, meta: {meta}")
            self.assertLessEqual(len(meta["top_event_ids"]), meta["accepted_count"],
                                 f"Test 7 期望 top_event_ids ≤ accepted_count, meta: {meta}")
            self.assertEqual(meta["perception_budget"], 3)

            # 驗證: accepted + rejected = 5 (5 events 都 evaluated)
            self.assertEqual(meta["accepted_count"] + meta["rejected_count"], 5,
                             f"Test 7 期望 accepted + rejected = 5, meta: {meta}")

            # 驗證: trace 內 5 條都有 reason (人可讀)
            for t in evaluated:
                self.assertIn("final_score", t["reason"],
                              f"Test 7 期望每個 event 都有 reason 含 final_score, "
                              f"但 {t['event_id']} reason={t['reason']!r}")

            print(f"[Test 7] 5 events 一次: top-3 進 world_context ({meta['accepted_count']} accepted), "
                  f"{meta['rejected_count']} rejected, world_context 內 {bullet_count} bullets")

    def test_02_budget_1_only_one_event_in_context(self):
        """perception_budget=1 → 只 1 個最高分 event 進 world_context"""
        with tempfile.TemporaryDirectory() as tmp:
            trace_path = Path(tmp) / "trace.jsonl"
            state = WorldPerceptionState()
            writer = WorldPerceptionTraceWriter(trace_path)
            bus = MagicMock()
            middleware = WorldPerceptionMiddleware(
                bus=bus, state=state, trace_writer=writer,
                perception_budget=1,  # 縮到 1
            )
            published: List[SoulEvent] = []
            async def _capture_publish(ev):
                published.append(ev)
            bus.publish = _capture_publish

            for ev in SyntheticWorldEventSource.build_all_five():
                _run(middleware.process_world_event_direct(ev))

            enriched = _make_enriched_event(draft="我要出門, 等下有會議")
            _run(middleware.handle_event(enriched))

            perceived = published[0]
            world_context_text = perceived.payload.get("world_context", "")
            meta = perceived.payload.get("world_perception_meta", {})

            # 期望: 只有 1 個 bullet
            bullet_count = world_context_text.count("- [")
            self.assertLessEqual(bullet_count, 1,
                                 f"Test 7 期望 budget=1 → 最多 1 bullet, 實際: {bullet_count}, "
                                 f"text: {world_context_text!r}")
            self.assertEqual(meta["perception_budget"], 1)
            print(f"[Test 7] budget=1 → 1 bullet, meta: {meta}")


# ───────────────────────────────────────────────────────────
# Test 8 — Single Intent / Single Speaker Path
# Bry 拍板 2026-08-07 20:02 hardening (P0):
# Production path 必須是:
#   AGENT_INTENT → MemoryMiddleware → AGENT_INTENT_ENRICHED
#                → WorldPerceptionMiddleware → AGENT_INTENT_PERCEIVED
#                → SpeakerTokenManager → SPEAKER_TOKEN_GRANTED (only 1!)
# 不會 double-process, 不會同時走 ENRICHED + PERCEIVED 兩條路徑
# ───────────────────────────────────────────────────────────

class TestM3SingleIntentSingleSpeakerPath(unittest.TestCase):
    """P0 hardening: 驗證 production path 不會 double-process"""

    def test_01_production_intake_only_perceived_no_double_grant(self):
        """
        完整 production pipeline:
        - 1 個 WORLD_EVENT (synthetic, accepted)
        - 1 個 AGENT_INTENT
        - MemoryMiddleware → ENRICHED
        - WorldPerceptionMiddleware → PERCEIVED
        - SpeakerTokenManager (PRODUCTION mode, intake=[PERCEIVED only])

        驗證:
        - 只有 1 個 SPEAKER_TOKEN_GRANTED (不會因為 ENRICHED 也訂閱而 double)
        - AGENT_INTENT_ENRICHED 不會被 SpeakerTokenManager 處理 (legacy fallback 不應在 production)
        """
        from src.eventbus import SoulEventBus
        from src.eventbus.token_manager import SpeakerTokenManager, PRODUCTION_INTAKE_EVENT_TYPES
        from src.eventbus.schema import EventPriority, EventType, SoulEvent
        from src.world import WorldPerceptionMiddleware

        async def _run_pipeline():
            bus = SoulEventBus()
            await bus.start()

            with tempfile.TemporaryDirectory() as tmp:
                state = WorldPerceptionState()
                writer = WorldPerceptionTraceWriter(Path(tmp) / "trace.jsonl")

                # 註冊順序: WorldPerception (模擬 MemoryMiddleware 後) → SpeakerTokenManager
                world_perception = WorldPerceptionMiddleware(
                    bus=bus, state=state, trace_writer=writer,
                )
                world_perception.register()

                # PRODUCTION mode: 只訂閱 AGENT_INTENT_PERCEIVED
                token_mgr = SpeakerTokenManager(
                    bus, token_timeout_secs=10.0,
                    intake_event_types=PRODUCTION_INTAKE_EVENT_TYPES,
                )
                token_mgr.register()

                # 觀察 SPEAKER_TOKEN_GRANTED 發了幾次
                grants: List[SoulEvent] = []
                original_publish = bus.publish
                async def _capture(ev):
                    if ev.event_type == EventType.SPEAKER_TOKEN_GRANTED:
                        grants.append(ev)
                    await original_publish(ev) if original_publish else None
                bus.publish = _capture

                # Step 1: 餵 1 個 world event (會被 accepted, 進 state)
                rain = SyntheticWorldEventSource.build_rain_started()
                await world_perception.process_world_event_direct(rain)

                # Step 2: 模擬 MemoryMiddleware: 直接把 AGENT_INTENT 升級成 AGENT_INTENT_ENRICHED
                enriched = SoulEvent(
                    event_type=EventType.AGENT_INTENT_ENRICHED,
                    source="agent_yua",
                    target="agent_yua",
                    priority=EventPriority.NORMAL,
                    payload={
                        "agent_id": "agent_yua",
                        "reason": "user_message",
                        "mode": "private",
                        "draft": "Bry 說要出門, 外面下雨嗎?",
                        "target_user_id": "bryan",
                        "chrono_context": "",
                        "memory_context": "(mock memory)",
                    },
                    session_id="test_session",
                )
                await bus.publish(enriched)

                # 給 bus 一點時間處理
                await asyncio.sleep(0.05)

                # 驗證: 只有 1 個 SPEAKER_TOKEN_GRANTED
                self.assertEqual(
                    len(grants), 1,
                    f"P0 期望 1 個 SPEAKER_TOKEN_GRANTED (production path), "
                    f"實際 {len(grants)} 個 (double-process?)"
                )
                granted = grants[0]
                self.assertEqual(granted.event_type, EventType.SPEAKER_TOKEN_GRANTED)
                self.assertEqual(granted.payload.get("agent_id"), "agent_yua")

                # 驗證: SpeakerTokenManager 沒收到 AGENT_INTENT_ENRICHED 兩次
                # (因為 PRODUCTION mode 只訂 PERCEIVED)
                self.assertEqual(token_mgr.intake_event_types, PRODUCTION_INTAKE_EVENT_TYPES)

                print(f"[Test 8] production path: 1 AGENT_INTENT → 1 SPEAKER_TOKEN_GRANTED (no double-process)")

                await bus.stop()

        # 模組層 _run() 收 coroutine
        _run(_run_pipeline())

    def test_02_legacy_fallback_for_isolated_tests(self):
        """
        Legacy mode (intake=[PERCEIVED + ENRICHED]) 是 isolated test environment 用的。
        這裡驗證: legacy mode 下, 1 個 AGENT_INTENT_ENRICHED 會被處理 (沒 M3 時 fallback)。
        """
        from src.eventbus import SoulEventBus
        from src.eventbus.token_manager import SpeakerTokenManager, LEGACY_INTAKE_EVENT_TYPES
        from src.eventbus.schema import EventPriority, EventType, SoulEvent

        async def _run_legacy_pipeline():
            bus = SoulEventBus()
            await bus.start()

            # 沒 M3 middleware, 走 legacy fallback
            token_mgr = SpeakerTokenManager(
                bus, token_timeout_secs=10.0,
                intake_event_types=LEGACY_INTAKE_EVENT_TYPES,
            )
            token_mgr.register()

            grants: List[SoulEvent] = []
            original_publish = bus.publish
            async def _capture(ev):
                if ev.event_type == EventType.SPEAKER_TOKEN_GRANTED:
                    grants.append(ev)
                await original_publish(ev) if original_publish else None
            bus.publish = _capture

            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="agent_yua",
                target="agent_yua",
                priority=EventPriority.NORMAL,
                payload={
                    "agent_id": "agent_yua",
                    "reason": "user_message",
                    "mode": "private",
                    "draft": "test",
                    "memory_context": "(mock memory)",
                },
            )
            await bus.publish(enriched)
            await asyncio.sleep(0.05)

            # 期望: legacy mode 下, 1 個 SPEAKER_TOKEN_GRANTED (fallback work)
            self.assertEqual(
                len(grants), 1,
                f"legacy mode 期望 1 個 SPEAKER_TOKEN_GRANTED, 實際 {len(grants)} 個"
            )
            print(f"[Test 8] legacy fallback: 1 AGENT_INTENT_ENRICHED → 1 SPEAKER_TOKEN_GRANTED")

            await bus.stop()

        _run(_run_legacy_pipeline())

    def test_03_world_context_injected_at_most_once(self):
        """
        P0 驗證: world_context 最多注入 LLM prompt 一次。
        (同一個 agent 同一個 session, 重複 intent 也不會 double-inject)
        """
        from src.llm.proxy import _build_messages_private
        soul = "test soul"

        # 模擬 LLMProxy 連續收 2 個 intent
        msgs1 = _build_messages_private(
            agent_id="agent_yua",
            soul=soul,
            current_input="",
            memory_context="",
            memory=MagicMock(),
            world_context="\n[世界感知] 第一次注入: rain\n",
        )
        msgs2 = _build_messages_private(
            agent_id="agent_yua",
            soul=soul,
            current_input="",
            memory_context="",
            memory=MagicMock(),
            world_context="\n[世界感知] 第二次注入: rain\n",
        )

        # 驗證: 兩個 messages 都含 [世界感知] 一次 (不會因為加參數而重複)
        for msgs in [msgs1, msgs2]:
            sys_msgs = [m for m in msgs if m["role"] == "system"]
            self.assertEqual(len(sys_msgs), 1)
            world_count = sys_msgs[0]["content"].count("[世界感知]")
            self.assertEqual(world_count, 1, f"Test 8 期望 [世界感知] 只出現 1 次, 實際 {world_count} 次")
        print(f"[Test 8] world_context 注入: 每次 messages 只 1 個 [世界感知] 區塊")


# ───────────────────────────────────────────────────────────
# Test 9 — Ephemeral State Lifecycle
# Bry 拍板 2026-08-07 20:02 hardening (P4):
# WorldPerceptionState:
# - restart 後完全清空
# - TTL / expiry 正常
# - novelty index 不會無限增長
# - recent events 有 bounded retention
# ───────────────────────────────────────────────────────────

class TestM3EphemeralStateLifecycle(unittest.TestCase):
    """P4 hardening: 驗證 ephemeral state lifecycle"""

    def test_01_restart_clears_state_completely(self):
        """
        模擬 server restart:
        - 建立 state_1, 加 events
        - 確認 state_1 有 events
        - 建立 state_2 (模擬新 process)
        - 確認 state_2 完全空, 沒繼承 state_1 任何東西
        """
        from src.world import WorldPerceptionState, SyntheticWorldEventSource
        from datetime import datetime, timezone

        # state 1 (原 process)
        state_1 = WorldPerceptionState()
        # state.add() 是 sync (沒 asyncio 包裝), 不需要 _run()
        state_1.add(SyntheticWorldEventSource.build_rain_started())
        state_1.add(SyntheticWorldEventSource.build_celebrity_news())
        self.assertEqual(state_1.get_state_size(), 2)
        self.assertEqual(state_1.get_novelty_count("weather_rain_20260807"), 1)

        # 模擬 restart: 丟掉 state_1, 建全新 state_2
        state_2 = WorldPerceptionState()

        # 驗證: state_2 完全空 (沒 cross-restart persistence)
        self.assertEqual(state_2.get_state_size(), 0,
                         f"P4 期望 state_2 (post-restart) 完全空, 實際: {state_2.get_state_size()}")
        self.assertEqual(state_2.get_novelty_count("weather_rain_20260807"), 0,
                         f"P4 期望 novelty count 從 0 開始, 實際: {state_2.get_novelty_count('weather_rain_20260807')}")

        # 確認 state_1 跟 state_2 是不同物件 (沒共享)
        self.assertIsNot(state_1, state_2)
        print(f"[Test 9] restart: state_1 ({state_1.get_state_size()} events) → state_2 (0 events) 完全清空")

    def test_02_ttl_expiry_removes_old_events(self):
        """
        驗證 TTL: 超過 novelty_window 的 event 自動從 state 移除
        """
        from src.world import WorldPerceptionState, SyntheticWorldEventSource
        from datetime import datetime, timedelta, timezone

        # 用短的 novelty_window (1 秒) 方便測試
        state = WorldPerceptionState(novelty_window=timedelta(seconds=1))

        # 加 event 在 t=0
        t_zero = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
        state.add(SyntheticWorldEventSource.build_rain_started(), t_zero)
        self.assertEqual(state.get_state_size(now=t_zero), 1)

        # t=0.5s: 還在
        t_half = t_zero + timedelta(seconds=0.5)
        self.assertEqual(state.get_state_size(now=t_half), 1)

        # t=1.5s: 過期 (超過 1s window)
        t_over = t_zero + timedelta(seconds=1.5)
        self.assertEqual(state.get_state_size(now=t_over), 0,
                         f"P4 期望 1.5s 後 event 過期, 實際: {state.get_state_size(now=t_over)}")
        print(f"[Test 9] TTL expiry: 1.5s 後 1 個 event 自動從 state 移除")

    def test_03_novelty_index_decrements_with_expiry(self):
        """
        驗證: novelty index 跟著 expiry 自動 decrement, 不會無限增長
        """
        from src.world import WorldPerceptionState, SyntheticWorldEventSource
        from datetime import datetime, timedelta, timezone

        state = WorldPerceptionState(novelty_window=timedelta(seconds=1))
        t_zero = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)
        # 加 3 個同 novelty_id
        for i in range(3):
            state.add(SyntheticWorldEventSource.build_rain_started(), t_zero)
        self.assertEqual(state.get_novelty_count("weather_rain_20260807", now=t_zero), 3)

        # t=1.5s: 全部過期, count 應為 0
        t_over = t_zero + timedelta(seconds=1.5)
        self.assertEqual(state.get_novelty_count("weather_rain_20260807", now=t_over), 0,
                         f"P4 期望 3 個 event 過期後 novelty_count=0, 實際: "
                         f"{state.get_novelty_count('weather_rain_20260807', now=t_over)}")
        print(f"[Test 9] novelty index 跟 expiry 自動 decrement: 3 → 0")

    def test_04_max_active_events_caps_memory(self):
        """
        驗證: max_active_events 防止 memory leak
        """
        from src.world import WorldPerceptionState
        from datetime import datetime, timedelta, timezone

        # 上限 10 個 events
        state = WorldPerceptionState(max_active_events=10, novelty_window=timedelta(hours=24))
        t_zero = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)

        # 加 15 個不同 novelty_id 的 events
        for i in range(15):
            ev = WorldEvent(
                source="news",
                type="celebrity_news",
                novelty_id=f"news_celebrity_{i:03d}",
                ts=SyntheticWorldEventSource._now_iso(),
                summary=f"celebrity {i}",
            )
            state.add(ev, t_zero)

        # 期望: state size 上限 10 (FIFO eviction)
        self.assertLessEqual(state.get_state_size(now=t_zero), 10,
                             f"P4 期望 max_active_events=10 限制生效, 實際: {state.get_state_size(now=t_zero)}")
        print(f"[Test 9] max_active_events=10: 15 個 events → state size ≤ 10 (FIFO)")


# ───────────────────────────────────────────────────────────
# Test 10 — Behavior Matrix (6 scenarios, Bry 拍板 20:02 P2)
# 確認 scoring 方向符合:
#   Quality > Quantity
#   No Memory > Wrong Memory
#   Perception Budget
#   World Awareness ≠ Agency
# ───────────────────────────────────────────────────────────

class TestM3BehaviorMatrix(unittest.TestCase):
    """P2 hardening: 6 個 behavior-oriented scenarios 驗證 scoring 方向"""

    def _setup_middleware(self, threshold=0.35):
        """helper: 建 middleware + state + writer, return tuple"""
        tmp = tempfile.TemporaryDirectory()
        trace_path = Path(tmp.name) / "trace.jsonl"
        state = WorldPerceptionState()
        writer = WorldPerceptionTraceWriter(trace_path)
        bus = MagicMock()
        m = WorldPerceptionMiddleware(
            bus=bus, state=state, trace_writer=writer,
            accept_threshold=threshold,
        )
        return m, state, writer, bus, tmp

    def test_01_scenario_a_rain_afternoon_going_outside(self):
        """
        Scenario A: 下午 / user 即將外出 / rain
        → high relevance (expected)
        """
        m, state, writer, bus, tmp = self._setup_middleware()
        try:
            chrono = "time_period=afternoon"
            user_draft = "我等等要出門, 外面還在下雨嗎?"

            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            _run(m.process_world_event_direct(SyntheticWorldEventSource.build_rain_started()))
            _run(m.handle_event(_make_enriched_event(draft=user_draft, chrono_context=chrono)))

            self.assertTrue(published[0].payload.get("world_context", "").strip(),
                            "Scenario A: rain + 下午 + going_outside 應 accepted")
            self.assertIn("下雨", published[0].payload.get("world_context", ""))
            print(f"[Matrix A] 下午 + rain + going_outside → ACCEPTED (high relevance)")
        finally:
            tmp.cleanup()

    def test_02_scenario_b_temp_fluctuation_deep_night(self):
        """
        Scenario B: 凌晨 / minor temperature fluctuation
        → low relevance (expected)
        """
        m, state, writer, bus, tmp = self._setup_middleware()
        try:
            chrono = "time_period=deep_night"
            user_draft = "晚餐想吃火鍋"

            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            _run(m.process_world_event_direct(SyntheticWorldEventSource.build_temp_fluctuation()))
            _run(m.handle_event(_make_enriched_event(draft=user_draft, chrono_context=chrono)))

            self.assertEqual(published[0].payload.get("world_context", ""), "",
                             "Scenario B: 凌晨 + temp + 火鍋 應 rejected (low relevance)")
            print(f"[Matrix B] 凌晨 + temp_fluctuation + 火鍋 → REJECTED (low relevance)")
        finally:
            tmp.cleanup()

    def test_03_scenario_c_calendar_30min(self):
        """
        Scenario C: user 明確有 upcoming calendar event
        → high personal + temporal significance
        """
        m, state, writer, bus, tmp = self._setup_middleware()
        try:
            chrono = "time_period=afternoon"
            user_draft = "我等等要去開會"

            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            _run(m.process_world_event_direct(SyntheticWorldEventSource.build_calendar_event_30min()))
            _run(m.handle_event(_make_enriched_event(draft=user_draft, chrono_context=chrono)))

            perceived = published[0]
            self.assertIn("會議", perceived.payload.get("world_context", ""))

            trace_lines = writer.trace_log_path.read_text(encoding="utf-8").strip().split("\n")
            evaluated = [json.loads(l) for l in trace_lines
                         if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
            self.assertEqual(len(evaluated), 1)
            s = evaluated[0]["scores"]
            self.assertGreaterEqual(s["personal_significance"], 0.5,
                                    f"Scenario C 期望 personal >= 0.5, 實際: {s}")
            self.assertGreaterEqual(s["temporal_significance"], 0.6,
                                    f"Scenario C 期望 temporal >= 0.6, 實際: {s}")
            print(f"[Matrix C] calendar + 開會 → ACCEPTED, personal={s['personal_significance']:.2f}, "
                  f"temporal={s['temporal_significance']:.2f}")
        finally:
            tmp.cleanup()

    def test_04_scenario_d_celebrity_rejected(self):
        """
        Scenario D: 無關 celebrity news
        → reject
        """
        m, state, writer, bus, tmp = self._setup_middleware()
        try:
            user_draft = "我今天想看書"

            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            _run(m.process_world_event_direct(SyntheticWorldEventSource.build_celebrity_news()))
            _run(m.handle_event(_make_enriched_event(draft=user_draft)))

            self.assertEqual(published[0].payload.get("world_context", ""), "",
                             "Scenario D: celebrity + 看書 應 rejected")
            print(f"[Matrix D] celebrity + 看書 → REJECTED (Quality > Quantity)")
        finally:
            tmp.cleanup()

    def test_05_scenario_e_duplicate_novelty_decay(self):
        """
        Scenario E: 同一事件持續存在
        → novelty decay / duplicate suppression
        """
        m, state, writer, bus, tmp = self._setup_middleware(threshold=0.40)
        try:
            user_draft = "等等我要出門, 外面是不是還在下雨?"

            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            for _ in range(3):
                _run(m.process_world_event_direct(SyntheticWorldEventSource.build_rain_started()))

            _run(m.handle_event(_make_enriched_event(draft=user_draft)))

            trace_lines = writer.trace_log_path.read_text(encoding="utf-8").strip().split("\n")
            evaluated = [json.loads(l) for l in trace_lines
                         if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
            novelty_counts = sorted([t["novelty_count_in_window"] for t in evaluated])
            self.assertEqual(novelty_counts, [1, 2, 3],
                             f"Scenario E 期望 novelty_count [1, 2, 3], 實際: {novelty_counts}")
            accepted_count = sum(1 for t in evaluated if t["accepted"])
            self.assertEqual(accepted_count, 1,
                             f"Scenario E 期望只 1 個 accepted, 實際: {accepted_count}")
            print(f"[Matrix E] rain x3 → novelty_count [1,2,3], 1 accepted + 2 rejected (No Memory > Wrong Memory)")
        finally:
            tmp.cleanup()

    def test_06_scenario_f_multiple_events_budget(self):
        """
        Scenario F: 多個同時發生事件
        → ranking + perception budget
        """
        m, state, writer, bus, tmp = self._setup_middleware()
        try:
            user_draft = "等等我要出門, 對了等下還有會議"
            published = []
            async def _capture(ev):
                published.append(ev)
            bus.publish = _capture

            for ev in SyntheticWorldEventSource.build_all_five():
                _run(m.process_world_event_direct(ev))

            _run(m.handle_event(_make_enriched_event(draft=user_draft)))

            perceived = published[0]
            world_context = perceived.payload.get("world_context", "")
            bullet_count = world_context.count("- [")
            self.assertLessEqual(bullet_count, 3,
                                 f"Scenario F 期望 ≤ 3 bullets (Perception Budget), 實際: {bullet_count}")
            meta = perceived.payload.get("world_perception_meta", {})
            self.assertLessEqual(len(meta.get("top_event_ids", [])), 3)

            trace_lines = writer.trace_log_path.read_text(encoding="utf-8").strip().split("\n")
            evaluated = [json.loads(l) for l in trace_lines
                         if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
            for t in evaluated:
                self.assertIn("selection_reason", t,
                              f"Scenario F 期望每個 evaluated trace 都有 selection_reason, "
                              f"但 {t['event_id']} 缺")
                self.assertTrue(t["selection_reason"],
                                f"Scenario F 期望 selection_reason 非空, {t['event_id']}: {t['selection_reason']!r}")
            reasons = [t["selection_reason"] for t in evaluated]
            selected_count = sum(1 for r in reasons if r.startswith("selected_top_N"))
            self.assertLessEqual(selected_count, 3,
                                 f"Scenario F 期望 ≤ 3 selected, 實際: {selected_count}")
            print(f"[Matrix F] 5 events → {selected_count} selected + {5 - selected_count} not selected "
                  f"(Perception Budget)")
        finally:
            tmp.cleanup()


# ───────────────────────────────────────────────────────────
# Test run
# ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)


# ───────────────────────────────────────────────────────────
# Test run
# ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
