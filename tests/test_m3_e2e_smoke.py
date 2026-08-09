"""
tests/test_m3_e2e_smoke.py — M3 Phase 1 Production E2E Smoke Test

Bry 拍板 2026-08-07 20:02 hardening P9:
跑真實 pipeline:
    SyntheticWorldEvent
        ↓
    WORLD_EVENT
        ↓
    WorldPerceptionMiddleware
        ↓
    AGENT_INTENT_ENRICHED (MockMemoryMiddleware - 避免 SQLite lock)
        ↓
    AGENT_INTENT_PERCEIVED
        ↓
    SpeakerTokenManager (PRODUCTION mode)
        ↓
    SPEAKER_TOKEN_GRANTED
        ↓
    LLMProxy
        ↓
    Prompt (system_parts 包含 [世界感知])

3 cases:
- Case A — Relevant (rain + going outside)
- Case B — Irrelevant (celebrity + 看書)
- Case C — Duplicate (rain x3)

Bry 拍板 2026-08-07 20:12 P13: MemoryMiddleware 涉及 SQLite (data/memory/...),
E2E test 用 mock MemoryMiddleware 取代, 避免 Windows SQLite lock 問題。
M3 chain 部分 (WorldPerception + SpeakerTokenManager + LLMProxy) 全部用真實組件。

真實組件:
  - SoulEventBus (真實)
  - WorldPerceptionMiddleware (真實)
  - SpeakerTokenManager PRODUCTION mode (真實)
  - LLMProxy + MockLLM backend (真實 LLMProxy + mock backend)

Mock 組件:
  - MemoryMiddleware → MockMemoryMiddleware (只做 AGENT_INTENT → ENRICHED, 不碰 SQLite)
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
from src.eventbus.token_manager import SpeakerTokenManager, PRODUCTION_INTAKE_EVENT_TYPES
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


class _MockLLMBackend:
    """E2E test 用 mock LLM, 不打真實 API"""

    async def complete(self, messages, model, max_tokens, temperature, **kwargs):
        # 從 system content 抓 agent_id
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        agent_id = ""
        for marker in ["agent_yua", "agent_ruka", "agent_akane", "agent_mahiru", "agent_rem"]:
            if marker in sys_content[:200]:
                agent_id = marker
                break
        return json.dumps({
            "text": f"[MOCK-{agent_id or 'unknown'}] 回應: 我看到了你說的。",
            "audio_text": f"[MOCK-{agent_id or 'unknown'}] echo",
            "emotion": "neutral",
        })


class _MockMemoryMiddleware:
    """
    模擬 MemoryMiddleware 的 bus 行為:
    訂閱 AGENT_INTENT → re-publish 為 AGENT_INTENT_ENRICHED (附 mock memory_context)
    不碰 SQLite, 避免 Windows file lock。
    """
    def __init__(self, bus: SoulEventBus):
        self.bus = bus
        self.subscriber_id = "mock_memory_middleware"
        self._registered = False

    def register(self):
        self.bus.subscribe(
            subscriber_id=self.subscriber_id,
            handler=self.handle_event,
            event_filter={EventType.AGENT_INTENT},
        )
        self._registered = True

    def unregister(self):
        if self._registered:
            self.bus.unsubscribe(self.subscriber_id)
            self._registered = False

    async def handle_event(self, event: SoulEvent) -> None:
        agent_id = event.payload.get("agent_id", "unknown")
        # 模擬 memory retrieval (靜態 mock)
        mock_memory = "(mock memory: 過去跟 Bry 聊過一些事, 詳細略)"
        enriched = SoulEvent(
            event_type=EventType.AGENT_INTENT_ENRICHED,
            source=event.source,
            target=event.target,
            priority=event.priority,
            payload={**event.payload, "memory_context": mock_memory},
            session_id=event.session_id,
            correlation_id=event.correlation_id,
        )
        await self.bus.publish(enriched)


class _E2EPipeline:
    """
    完整 production chain setup (除 MemoryMiddleware 用 mock, 避免 SQLite lock)。

    P0 (Bry 派工 2026-08-09 19:18): LLMProxy 透過 memory_store + conversation_dir
    兩個 optional kwargs 注入 tmp_path 隔離的 store / dir, 避免污染 production
    data/memory.db 跟 data/conversations/。
    """

    def __init__(self, trace_dir: Path):
        self.bus = SoulEventBus()
        self.trace_path = trace_dir / "perception_trace.jsonl"
        self.state = WorldPerceptionState()
        self.trace_writer = WorldPerceptionTraceWriter(self.trace_path)

        # P0: 隔離 persistence dependencies (tmp_path)
        self.isolation_dir = trace_dir
        self.isolation_memory_path = trace_dir / "memory.db"
        self.isolation_conversations_dir = trace_dir / "conversations"

        # 觀察
        self.grants: List[SoulEvent] = []
        self.prompts: List[List[dict]] = []
        self.llm_outputs: List[str] = []
        self.enriched_count: int = 0
        self.perceived_count: int = 0

    async def start(self):
        await self.bus.start()

        # 註冊順序 (Bry 拍板 2026-08-07 20:02 P7):
        #   (Mock)MemoryMiddleware → WorldPerceptionMiddleware → SpeakerTokenManager → LLMProxy
        from src.llm.proxy import LLMProxy
        from src.memory.store import MemoryStore

        # 1. Mock MemoryMiddleware
        self.mw = _MockMemoryMiddleware(self.bus)
        self.mw.register()

        # 2. WorldPerceptionMiddleware
        self.world_perception = WorldPerceptionMiddleware(
            bus=self.bus, state=self.state, trace_writer=self.trace_writer,
        )
        self.world_perception.register()

        # 3. SpeakerTokenManager PRODUCTION mode
        self.token_mgr = SpeakerTokenManager(
            self.bus, token_timeout_secs=10.0,
            intake_event_types=PRODUCTION_INTAKE_EVENT_TYPES,
        )
        self.token_mgr.register()

        # 4. LLMProxy (真實 + MockLLM backend)
        # P0: 注入 isolation 用的 MemoryStore + conversation_dir (避免寫 production)
        self.isolation_memory = MemoryStore(db_path=self.isolation_memory_path)
        self.llm = LLMProxy(
            bus=self.bus,
            backend=_MockLLMBackend(),
            model="mock",
            max_tokens=200,
            memory_store=self.isolation_memory,
            conversation_dir=self.isolation_conversations_dir,
        )
        self.llm.register()

        # 觀察: 攔截所有 publishes
        captured = self
        original_publish = self.bus.publish
        async def _capture_publish(ev):
            if ev.event_type == EventType.SPEAKER_TOKEN_GRANTED:
                captured.grants.append(ev)
            elif ev.event_type == EventType.AGENT_INTENT_ENRICHED:
                captured.enriched_count += 1
            elif ev.event_type == EventType.AGENT_INTENT_PERCEIVED:
                captured.perceived_count += 1
            await original_publish(ev)
        self.bus.publish = _capture_publish

        # 攔截 LLM 訊息
        original_complete = _MockLLMBackend.complete

        async def patched_complete(self_backend, messages, model, max_tokens, temperature, **kwargs):
            captured.prompts.append(messages)
            result = await original_complete(self_backend, messages, model, max_tokens, temperature, **kwargs)
            captured.llm_outputs.append(result)
            return result

        _MockLLMBackend.complete = patched_complete

    async def stop(self):
        try:
            await self.bus.stop()
        except Exception:
            pass  # Bus stop 在測試環境可以寬容
        # P0 (Bry 派工 2026-08-09 19:18): 關閉 isolation MemoryStore 的 SQLite 連線
        # 否則 Windows 上 tempfile.TemporaryDirectory() cleanup 時會撞 file lock
        if hasattr(self, "isolation_memory") and self.isolation_memory is not None:
            try:
                self.isolation_memory.close()
            except Exception:
                pass

    async def publish_world_event(self, ev: WorldEvent):
        await self.world_perception.process_world_event_direct(ev)

    async def publish_user_message(self, agent_id: str, user_text: str):
        intent = SoulEvent(
            event_type=EventType.AGENT_INTENT,
            source=agent_id,
            target=agent_id,
            priority=EventPriority.NORMAL,
            payload={
                "agent_id": agent_id,
                "reason": "user_message",
                "mode": "private",
                "draft": user_text,
                "target_user_id": "bryan",
                "chrono_context": "",
            },
            session_id=f"session_bryan_{agent_id}",
        )
        await self.bus.publish(intent)


# ───────────────────────────────────────────────────────────
# Case A — Relevant
# ───────────────────────────────────────────────────────────

class TestE2ECaseARelevant(unittest.TestCase):
    """rain + going outside → ACCEPT → WorldContext 注入 LLM prompt"""

    def test_relevant_full_pipeline(self):
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                pipeline = _E2EPipeline(trace_dir=Path(tmp))
                await pipeline.start()
                try:
                    rain = SyntheticWorldEventSource.build_rain_started()
                    await pipeline.publish_world_event(rain)

                    await pipeline.publish_user_message(
                        "agent_yua",
                        "等等我要出門, 外面還在下雨嗎?",
                    )

                    await asyncio.sleep(0.5)

                    # 驗證 chain
                    self.assertEqual(
                        pipeline.enriched_count, 1,
                        f"Case A 期望 1 個 AGENT_INTENT_ENRICHED, 實際 {pipeline.enriched_count}"
                    )
                    self.assertEqual(
                        pipeline.perceived_count, 1,
                        f"Case A 期望 1 個 AGENT_INTENT_PERCEIVED, 實際 {pipeline.perceived_count}"
                    )
                    self.assertEqual(
                        len(pipeline.grants), 1,
                        f"Case A 期望 1 個 SPEAKER_TOKEN_GRANTED, 實際 {len(pipeline.grants)}"
                    )

                    # 驗證 LLM prompt
                    self.assertGreater(len(pipeline.prompts), 0, "Case A 期望 LLM 有被呼叫")
                    last_prompt = pipeline.prompts[-1]
                    sys_msgs = [m for m in last_prompt if m["role"] == "system"]
                    self.assertEqual(len(sys_msgs), 1)
                    sys_content = sys_msgs[0]["content"]
                    self.assertIn("[世界感知]", sys_content,
                                  f"Case A 期望 prompt 含 [世界感知], 實際: {sys_content[:500]}")
                    self.assertIn("下雨", sys_content,
                                  f"Case A 期望 prompt 含 rain 內容, 實際: {sys_content[:500]}")

                    # 驗證 trace
                    trace_lines = pipeline.trace_path.read_text(encoding="utf-8").strip().split("\n")
                    evaluated = [json.loads(l) for l in trace_lines
                                 if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
                    self.assertGreater(len(evaluated), 0)
                    selected = [t for t in evaluated if t.get("selection_reason", "").startswith("selected_top_N")]
                    self.assertGreater(len(selected), 0,
                                       f"Case A 期望至少 1 個 selected_top_N trace")

                    print(f"[P9 Case A] rain + going outside → 1 grant, "
                          f"LLM prompt 含 [世界感知], {len(selected)} selected_top_N trace")

                finally:
                    await pipeline.stop()

        _run(_scenario())


# ───────────────────────────────────────────────────────────
# Case B — Irrelevant
# ───────────────────────────────────────────────────────────

class TestE2ECaseBIrrelevant(unittest.TestCase):
    """celebrity + 看書 → REJECT → 沒 WorldContext"""

    def test_irrelevant_no_context(self):
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                pipeline = _E2EPipeline(trace_dir=Path(tmp))
                await pipeline.start()
                try:
                    celeb = SyntheticWorldEventSource.build_celebrity_news()
                    await pipeline.publish_world_event(celeb)

                    await pipeline.publish_user_message(
                        "agent_yua",
                        "我今天想看本小說",
                    )

                    await asyncio.sleep(0.5)

                    self.assertEqual(len(pipeline.grants), 1,
                                     f"Case B 期望 1 個 grant, 實際 {len(pipeline.grants)}")
                    self.assertEqual(pipeline.enriched_count, 1)
                    self.assertEqual(pipeline.perceived_count, 1)

                    last_prompt = pipeline.prompts[-1]
                    sys_msgs = [m for m in last_prompt if m["role"] == "system"]
                    sys_content = sys_msgs[0]["content"]
                    self.assertNotIn("[世界感知]", sys_content,
                                     f"Case B 期望 prompt 沒 [世界感知] (celebrity rejected)")

                    trace_lines = pipeline.trace_path.read_text(encoding="utf-8").strip().split("\n")
                    evaluated = [json.loads(l) for l in trace_lines
                                 if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
                    rejected = [t for t in evaluated if t.get("selection_reason") == SELECTION_REJECTED_AT_THRESHOLD]
                    self.assertGreater(len(rejected), 0,
                                       f"Case B 期望至少 1 個 rejected_at_threshold")

                    print(f"[P9 Case B] celebrity + 看書 → 1 grant, "
                          f"LLM prompt 沒 [世界感知], {len(rejected)} rejected_at_threshold")

                finally:
                    await pipeline.stop()

        _run(_scenario())


# ───────────────────────────────────────────────────────────
# Case C — Duplicate
# ───────────────────────────────────────────────────────────

class TestE2ECaseCDuplicate(unittest.TestCase):
    """
    Bry 拍板 2026-08-07 20:30 — P9 Case C final semantics:

    Same novelty_id × 3:
      Event #1: novelty_count=1, final≈0.45 → ACCEPT
      Event #2: novelty_count=2, final≈0.35 (== threshold) → ACCEPT (boundary == threshold 是合法 ACCEPT)
      Event #3: novelty_count=3, final≈0.3167 → REJECT

    Threshold rule (perception.py:455):
      ACCEPT iff final_score >= threshold
      REJECT iff final_score < threshold

    SPEAKER_TOKEN_GRANTED = 1 (P0 race 防 double-process)
    LLM processing = 1
    """

    def test_duplicate_novelty_decay(self):
        async def _scenario():
            with tempfile.TemporaryDirectory() as tmp:
                pipeline = _E2EPipeline(trace_dir=Path(tmp))
                await pipeline.start()
                try:
                    for _ in range(3):
                        await pipeline.publish_world_event(
                            SyntheticWorldEventSource.build_rain_started()
                        )

                    await pipeline.publish_user_message(
                        "agent_yua",
                        "等等我要出門, 外面是不是還在下雨?",
                    )

                    await asyncio.sleep(0.5)

                    # P0 race: rain x3 仍只 1 個 grant
                    self.assertEqual(len(pipeline.grants), 1,
                                     f"Case C 期望 1 個 SPEAKER_TOKEN_GRANTED, 實際 {len(pipeline.grants)}")
                    # LLM 處理也只 1 次
                    self.assertEqual(len(pipeline.prompts), 1,
                                     f"Case C 期望 1 次 LLM processing, 實際 {len(pipeline.prompts)}")

                    # 讀 trace, 按 novelty_count 索引
                    trace_lines = pipeline.trace_path.read_text(encoding="utf-8").strip().split("\n")
                    evaluated = [json.loads(l) for l in trace_lines
                                 if json.loads(l).get("extra", {}).get("phase") == "evaluated"]
                    novelty_counts = sorted(set(t["novelty_count_in_window"] for t in evaluated))
                    self.assertEqual(novelty_counts, [1, 2, 3],
                                     f"Case C 期望 novelty_counts [1, 2, 3], 實際: {novelty_counts}")

                    # 精確驗證每個 novelty_count 的 accept/reject (Bry 拍板 20:30)
                    by_count = {t["novelty_count_in_window"]: t for t in evaluated}
                    self.assertEqual(len(by_count), 3,
                                     f"Case C 期望 3 個 unique novelty_count, 實際: {list(by_count.keys())}")

                    # Event #1: novelty_count=1 → ACCEPT
                    self.assertTrue(by_count[1]["accepted"],
                                    f"Case C Event #1 (count=1) 應 ACCEPT, 實際: {by_count[1]}")
                    # Event #2: novelty_count=2, final==threshold → ACCEPT (boundary == threshold 合法)
                    self.assertTrue(by_count[2]["accepted"],
                                    f"Case C Event #2 (count=2) 應 ACCEPT (== threshold), 實際: {by_count[2]}")
                    # Event #3: novelty_count=3 → REJECT
                    self.assertFalse(by_count[3]["accepted"],
                                     f"Case C Event #3 (count=3) 應 REJECT, 實際: {by_count[3]}")

                    # LLM prompt 含 [世界感知] (因為 #1 + #2 進 top-N)
                    last_prompt = pipeline.prompts[-1]
                    sys_msgs = [m for m in last_prompt if m["role"] == "system"]
                    self.assertIn("[世界感知]", sys_msgs[0]["content"])

                    print(f"[P9 Case C] rain x3 → 1 grant + 1 LLM call, "
                          f"novelty [1,2,3]: #1 ACCEPT, #2 ACCEPT (boundary), #3 REJECT")

                finally:
                    await pipeline.stop()

        _run(_scenario())


if __name__ == "__main__":
    unittest.main(verbosity=2)

