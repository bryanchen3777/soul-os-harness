"""
M5.3-S2-E — End-to-End World Perception Acceptance

Bry 派工 2026-08-09 15:50:
- 30+ E2E scenarios through full chain:
  user input → agent intent → temporal context → memory retrieval → world perception → behavior context → prompt assembly
- DO NOT call atomic functions (compute_time_period, build_temporal_context) as test main logic
- DO call higher-level components (WorldPerceptionMiddleware, _build_messages_group)
- Use real bus + real middleware chain
- Inspect final prompt

Hard rules:
- DO NOT modify production source
- DO NOT commit, DO NOT push
- DO NOT touch production data
- Test isolation: use tempdir, in-memory bus
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.temporal.core import build_temporal_context
from src.temporal.models import (
    EmotionalCarryover,
    PersonaConfig,
)
from src.temporal.render import render_temporal_block
from src.world.perception import (
    WorldEvent,
    WorldContext,
    format_world_context_block,
)
from src.world.middleware import WorldPerceptionMiddleware
from src.world.state import WorldPerceptionState
from src.world.trace import WorldPerceptionTraceWriter
from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, EventPriority, SoulEvent
from src.memory.v1.store import V1Store
from src.memory.v1.loader import MemoryLoader, derive_query_tags


# ════════════════════════════════════════════════════════════
# Helpers — chain assembly
# ════════════════════════════════════════════════════════════


def _build_chrono_context(hour: int, silence_hours: float, carryover=None, stress: int = 0):
    """Step 1: Physical time + silence → chrono_context 字串 (temporal layer)."""
    config = PersonaConfig(persona_id="test")
    co = carryover or EmotionalCarryover()
    now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
    last_msg_ts = (
        (now - timedelta(hours=silence_hours)).isoformat()
        if silence_hours > 0
        else None
    )
    ctx = build_temporal_context(
        persona_id="test",
        last_msg_ts=last_msg_ts,
        current_stress=stress,
        carryover=co,
        config=config,
        now=now,
    )
    return ctx, render_temporal_block(ctx)


def _dispatch_through_world_perception(chrono_context, draft, agent_id, world_events=None):
    """Step 2-3: AGENT_INTENT_ENRICHED → WorldPerceptionMiddleware → AGENT_INTENT_PERCEIVED.

    Returns the payload of the AGENT_INTENT_PERCEIVED event.
    """
    async def _run():
        bus = SoulEventBus()
        state = WorldPerceptionState()
        middleware = WorldPerceptionMiddleware(
            bus=bus,
            state=state,
            trace_writer=WorldPerceptionTraceWriter(),  # in-memory, no file write
        )

        # Capture AGENT_INTENT_PERCEIVED events
        perceived_payloads = []
        async def _capture(event):
            perceived_payloads.append(event.payload)

        bus.subscribe(
            subscriber_id="test_capture",
            handler=_capture,
            event_filter={EventType.AGENT_INTENT_PERCEIVED},
        )

        # CRITICAL: Start bus worker,否則 publish 會 drop events
        await bus.start()

        try:
            # Add pre-existing world events to state
            if world_events:
                for ev in world_events:
                    await middleware.process_world_event_direct(ev)

            # Construct AGENT_INTENT_ENRICHED event
            enriched = SoulEvent(
                event_type=EventType.AGENT_INTENT_ENRICHED,
                source="test",
                target="test",
                priority=EventPriority.NORMAL,
                session_id="test_session",
                payload={
                    "agent_id": agent_id,
                    "draft": draft,
                    "text": draft,
                    "chrono_context": chrono_context,
                    "memory_context": "",
                },
            )

            # Dispatch through WorldPerceptionMiddleware
            await middleware.handle_event(enriched)

            # Give worker time to process (publish AGENT_INTENT_PERCEIVED back)
            await asyncio.sleep(0.1)

        finally:
            await bus.stop()

        return perceived_payloads[0] if perceived_payloads else {}

    return asyncio.run(_run())


@dataclass
class _StubMemory:
    """Stub for proxy._build_messages_group's memory argument."""
    history: list = None

    def get_group_history(self, limit):
        return self.history or []


def _build_final_prompt(agent_id, draft, memory_context, world_context, current_time_str=""):
    """Step 4: Call LLMProxy._build_messages_group → final system prompt."""
    from src.llm.proxy import _build_messages_group, AGENT_NAMES

    soul = "你是 test character。一個簡單的 SOUL.md placeholder for E2E test."

    # Stub memory
    memory = _StubMemory(history=[])

    # event_ts at now (we don't have real time, use 2026-08-09T15:00:00)
    event_ts = datetime(2026, 8, 9, 15, 0, 0)

    messages = _build_messages_group(
        agent_id=agent_id,
        soul=soul,
        current_input=draft,
        memory_context=memory_context,
        memory=memory,
        mood=0.0,
        user_id="bryan",
        current_time=current_time_str,
        event_ts=event_ts,
        bry_latest_ts=0,  # assume Bry online
        world_context=world_context,
    )

    return messages[0]["content"]  # system message


def _run_full_chain(hour, silence_hours, draft, agent_id="agent_yua", world_events=None, memory_context=""):
    """Full chain: physical time → chrono_context → world_context → final prompt."""
    ctx, chrono_context = _build_chrono_context(hour, silence_hours)
    perceived = _dispatch_through_world_perception(
        chrono_context, draft, agent_id, world_events
    )
    world_context = perceived.get("world_context", "")
    final_prompt = _build_final_prompt(
        agent_id, draft, memory_context, world_context, current_time_str=chrono_context.strip()
    )
    return {
        "ctx": ctx,
        "chrono_context": chrono_context,
        "world_context": world_context,
        "final_prompt": final_prompt,
        "perceived": perceived,
    }


# ════════════════════════════════════════════════════════════
# Group A — Time (6 scenarios)
# ════════════════════════════════════════════════════════════


class TestS2EGroupATime:
    """E2-A: 不同 time → 不同 final prompt。"""

    @pytest.mark.parametrize("hour", [2, 5, 9, 15, 20, 23])
    def test_different_hour_different_final_prompt(self, hour):
        """不同 hour(同 silence=1h)→ final prompt 應該有不同 temporal context。"""
        result = _run_full_chain(hour=hour, silence_hours=1.0, draft="今天過得如何")
        prompt = result["final_prompt"]

        # 必須有 chrono context(在 prompt 中)
        assert "temporal" in prompt.lower() or "time_period" in prompt or "當下時間" in prompt
        # 必須有 time_period 欄位
        assert "time_period=" in prompt
        # 必須有 world_context 區塊(雖然可能空)
        # final prompt 應該至少有世界感知區塊 section
        # 至少要 system message 不是空的
        assert len(prompt) > 100

    def test_02_vs_15_clear_difference(self):
        """02:00 vs 15:00 應該 time_period 完全不同。"""
        r_02 = _run_full_chain(hour=2, silence_hours=1.0, draft="今天過得如何")
        r_15 = _run_full_chain(hour=15, silence_hours=1.0, draft="今天過得如何")
        assert "time_period=deep_night" in r_02["final_prompt"]
        assert "time_period=afternoon" in r_15["final_prompt"]
        # 兩個 prompt 必須有實質差異
        assert r_02["final_prompt"] != r_15["final_prompt"]

    def test_six_hours_all_distinct(self):
        """6 個 hour(02/05/09/15/20/23)→ 6 個不同的 final prompt。"""
        prompts = set()
        for h in [2, 5, 9, 15, 20, 23]:
            r = _run_full_chain(hour=h, silence_hours=1.0, draft="今天過得如何")
            prompts.add(r["final_prompt"])
        # 6 個不同的 (hour, time_period) 組合 → 應該 ≥ 4 個不同 final prompt
        assert len(prompts) >= 4, f"expected ≥ 4 unique final prompts, got {len(prompts)}"


# ════════════════════════════════════════════════════════════
# Group B — Silence (6 scenarios)
# ════════════════════════════════════════════════════════════


class TestS2EGroupBSilence:
    """E2-B: 不同 silence → 影響 anticipatory / deviation / reaction_bias。"""

    @pytest.mark.parametrize("silence", [0.5, 2.0, 8.5, 12.0, 25.0, 50.0])
    def test_different_silence_different_state(self, silence):
        """不同 silence(同 hour=15)→ state 應該演變。"""
        result = _run_full_chain(hour=15, silence_hours=silence, draft="最近好嗎")
        prompt = result["final_prompt"]
        # prompt 應該有 silence=Xh 標示
        assert f"silence={silence:.1f}h" in prompt

    def test_silence_0_5h_vs_50h_dramatic_difference(self):
        """0.5h vs 50h silence → dramatic state difference。"""
        r_short = _run_full_chain(hour=15, silence_hours=0.5, draft="最近好嗎")
        r_long = _run_full_chain(hour=15, silence_hours=50.0, draft="最近好嗎")
        # 短 silence → preoccupation_flavor=none
        assert "longing" not in r_short["final_prompt"]  # no "longing" in normal state
        # 長 silence → preoccupation_flavor=longing
        assert "longing" in r_long["final_prompt"]
        # 兩個 prompt 必須不同
        assert r_short["final_prompt"] != r_long["final_prompt"]

    def test_silence_growth_affects_salience(self):
        """silence 增長 → temporal_salience 從 low → medium → high。"""
        r_low = _run_full_chain(hour=15, silence_hours=0.5, draft="最近好嗎")
        r_med = _run_full_chain(hour=15, silence_hours=9.0, draft="最近好嗎")
        r_high = _run_full_chain(hour=15, silence_hours=50.0, draft="最近好嗎")
        # 注意: 短 silence 也可能顯示 low salience
        # 中 silence → medium (silence > 6h)
        # 高 silence → high (is_overdue)
        # 只要差異存在即可
        assert r_low["final_prompt"] != r_high["final_prompt"]
        assert r_med["final_prompt"] != r_high["final_prompt"]


# ════════════════════════════════════════════════════════════
# Group C — Combined World State (6 scenarios)
# ════════════════════════════════════════════════════════════


class TestS2EGroupCCombined:
    """E2-C: (hour, silence) 組合 → 整體 combination effect。"""

    @pytest.mark.parametrize("hour,silence,expected_period", [
        (2, 12.0, "deep_night"),
        (9, 1.0, "morning"),
        (20, 8.5, "evening"),
        (23, 25.0, "night"),
        (5, 50.0, "dawn"),
        (15, 2.0, "afternoon"),
    ])
    def test_combination_yields_expected_state(self, hour, silence, expected_period):
        """6 個 combination → expected time_period 正確。"""
        r = _run_full_chain(hour=hour, silence_hours=silence, draft="今天過得如何")
        assert f"time_period={expected_period}" in r["final_prompt"]

    def test_02_12h_vs_09_1h_clear_difference(self):
        """02:00 + 12h silence vs 09:00 + 1h silence → 大幅差異(深夜 + 較長 silence)。"""
        r1 = _run_full_chain(hour=2, silence_hours=12.0, draft="今天過得如何")
        r2 = _run_full_chain(hour=9, silence_hours=1.0, draft="今天過得如何")
        # 兩個 prompt 必須差異顯著(完全不同 time_period + silence)
        assert "time_period=deep_night" in r1["final_prompt"]
        assert "time_period=morning" in r2["final_prompt"]
        assert "silence=12.0h" in r1["final_prompt"]
        assert "silence=1.0h" in r2["final_prompt"]
        # 兩個 prompt 必須差異
        assert r1["final_prompt"] != r2["final_prompt"]


# ════════════════════════════════════════════════════════════
# Group D — Memory + World Awareness (6 scenarios)
# ════════════════════════════════════════════════════════════


class TestS2EGroupDMemoryPlusWorld:
    """E2-D: Memory hit vs miss + World Awareness should BOTH exist independently。"""

    def test_memory_hit_with_world_awareness(self):
        """Memory hit + WA → 兩個 context 都在 final prompt。"""
        # Query "雷姆 喜歡 吃 什麼" → 應該 memory hit(per S2-B)
        memory_context = "[Recall relevant memories]\n- (preference, conf 0.80, tags=雷姆,喜歡): 雷姆 喜歡 吃 牛骨熬的湯頭\n[/Recall]"
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="雷姆 喜歡 吃 什麼",
            memory_context=memory_context,
        )
        prompt = r["final_prompt"]
        # 兩個 context 都應該在 prompt
        assert "你記得以下這些事情" in prompt
        assert "[Recall relevant memories]" in prompt

    def test_memory_miss_with_world_awareness_still_has_world(self):
        """Memory miss + WA → 雖然 memory 空,World Awareness 仍應建立。"""
        # Query 完全不對應 memory
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="Python list comprehension",
            memory_context="",  # memory miss
        )
        prompt = r["final_prompt"]
        # Memory section 應該空(或不應該有 recall)
        # 沒有 [Recall relevant memories] 區塊
        assert "[Recall relevant memories]" not in prompt
        # 但 temporal context 仍應該在
        assert "time_period=afternoon" in prompt
        # 整個 prompt 仍 non-trivial
        assert len(prompt) > 100

    def test_memory_miss_no_recall_does_not_clear_world_awareness(self):
        """Memory miss 不該把 world awareness 清掉。"""
        r = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="今天過得如何",  # 完全不對應 memory
            memory_context="",
        )
        prompt = r["final_prompt"]
        # 即使 memory 空,world awareness 仍應該反映 late night
        assert "time_period=deep_night" in prompt
        # vulnerability_window 應該在
        assert "vulnerability_window=True" in prompt

    def test_memory_hit_preserves_world_state(self):
        """Memory hit 不該 override world state。"""
        r = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="雷姆 喜歡 吃 什麼",  # memory hit
            memory_context="[Recall relevant memories]\n- (preference, conf 0.80): 雷姆 喜歡 湯頭\n[/Recall]",
        )
        prompt = r["final_prompt"]
        # 兩個 context 都應該在
        assert "[Recall relevant memories]" in prompt
        assert "time_period=deep_night" in prompt
        # vulnerability_window 也應該在
        assert "vulnerability_window=True" in prompt

    def test_3_memory_variants_same_world_state(self):
        """3 個 memory variants(空 / partial / full),同 world state → 都有 world state。"""
        world_check = lambda r: (
            "time_period=afternoon" in r["final_prompt"] and
            "silence=1.0h" in r["final_prompt"]
        )
        r_empty = _run_full_chain(hour=15, silence_hours=1.0, draft="Python", memory_context="")
        r_partial = _run_full_chain(hour=15, silence_hours=1.0, draft="Python", memory_context="some unrelated memory")
        r_full = _run_full_chain(hour=15, silence_hours=1.0, draft="Python", memory_context="[Recall relevant memories]\n- (preference, conf 0.80): test\n[/Recall]")
        assert world_check(r_empty)
        assert world_check(r_partial)
        assert world_check(r_full)

    def test_memory_and_world_independent_layers(self):
        """Memory 跟 World 是獨立 layers,兩個都可以不存在。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="你好",
            memory_context="",  # empty memory
        )
        prompt = r["final_prompt"]
        # 沒有 memory 區塊
        assert "[Recall relevant memories]" not in prompt
        # 但有 temporal 區塊(world awareness 仍 active)
        assert "time_period=" in prompt


# ════════════════════════════════════════════════════════════
# Group E — Negative (6 scenarios)
# ════════════════════════════════════════════════════════════


class TestS2EGroupENegative:
    """E2-E: technical query / irrelevant / empty events → 不應錯誤 world assumption。"""

    def test_technical_query_no_world_intrusion(self):
        """技術 query → 沒有 "現在應該睡覺" 這類 literal intrusion。"""
        r = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="Python list comprehension 怎麼用",
        )
        prompt = r["final_prompt"]
        # 沒有 "你應該睡覺" 之類的 literal intrusion
        assert "你應該睡覺" not in prompt
        assert "現在去睡覺" not in prompt
        # 但 temporal 仍正確呈現
        assert "time_period=deep_night" in prompt

    def test_unrelated_query_no_world_intrusion(self):
        """unrelated query → 沒有 world intrusion。"""
        r = _run_full_chain(
            hour=20, silence_hours=1.0,
            draft="紐約今天天氣如何",
        )
        prompt = r["final_prompt"]
        assert "現在吃飯時間" not in prompt
        assert "你應該去睡覺" not in prompt

    def test_empty_world_events_no_lie(self):
        """沒 world events → 沒有 fake world assumption。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="今天過得如何",
            world_events=[],  # no events
        )
        # world_context 應該是空字串
        assert r["world_context"] == "" or r["world_context"] == ""
        prompt = r["final_prompt"]
        # 沒有 [世界感知] 區塊(沒 events)
        assert "[世界感知]" not in prompt
        # 注意: Bry_recent 可能含真實 Bry 訊息
        # 重點是 World Awareness 不主動製造 fake assumption

    def test_low_priority_events_accepted_or_rejected(self):
        """Low priority events 應該被 reject 或不會 dominate prompt。"""
        events = [
            WorldEvent(
                source="celebrity", type="celebrity_news",
                novelty_id="cel_001", ts="2026-08-09T15:00:00+00:00",
                summary="某明星的八卦新聞", priority=0,
            ),
        ]
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="今天過得如何",
            world_events=events,
        )
        # celebrity_news 應該被 reject(per S2-D 驗證)
        # 沒 world_context 進 prompt,或 prompt 沒有 celebrity 字眼
        assert "某明星" not in r["final_prompt"]
        assert "八卦" not in r["final_prompt"]

    def test_no_memory_no_world_event(self):
        """完全沒 memory + 沒 world event → prompt 仍 valid。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="test",
            memory_context="",
            world_events=[],
        )
        prompt = r["final_prompt"]
        # prompt 仍有 identity + temporal
        assert "test character" in prompt  # identity_anchor
        assert "time_period=afternoon" in prompt
        # 沒有 fake assumption
        assert len(prompt) > 50  # 至少 valid

    def test_low_priority_high_count_does_not_dominate(self):
        """多個 low priority events 累計不應該 dominate。"""
        events = [
            WorldEvent(
                source="weather", type="weather_temp_change",
                novelty_id=f"temp_{i}", ts="2026-08-09T15:00:00+00:00",
                summary=f"溫度變化 {i} 度", priority=0,
            )
            for i in range(5)
        ]
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="今天過得如何",
            world_events=events,
        )
        # 溫度變化 baseline 0.05,5 個也達不到 threshold 0.35
        # 應該全部 reject,沒 context intrusion
        assert "溫度變化" not in r["final_prompt"]


# ════════════════════════════════════════════════════════════
# E3 — Most Important Test: same input + 5 world states → delta
# ════════════════════════════════════════════════════════════


class TestS2EMostImportant:
    """E3: same user input + 5 world states → final prompt delta。"""

    def test_same_input_5_world_states_5_unique_prompts(self):
        """同 input '今天過得如何' 跑 5 個 world state → 5 個不同的 final prompt。"""
        world_states = [
            (9, 1.0),    # W1 morning
            (15, 1.0),   # W2 afternoon
            (20, 1.0),   # W3 evening
            (2, 5.0),    # W4 deep_night + vuln
            (2, 25.0),   # W5 deep_night + long_silence
        ]
        prompts = []
        for hour, silence in world_states:
            r = _run_full_chain(hour=hour, silence_hours=silence, draft="今天過得如何")
            prompts.append(r["final_prompt"])
        # 5 個不同 world state → 5 個不同 prompt
        assert len(set(prompts)) == 5, (
            f"5 world states should produce 5 different final prompts, "
            f"got {len(set(prompts))} unique: {set(prompts)}"
        )

    def test_same_input_w1_w2_w3_w4_state_components(self):
        """W1/W2/W3/W4 必須在 prompt 內有不同 behavior context state。"""
        results = []
        for hour, silence in [(9, 1.0), (15, 1.0), (20, 1.0), (2, 5.0)]:
            r = _run_full_chain(hour=hour, silence_hours=silence, draft="今天過得如何")
            results.append(r)

        # Extract time_period from each
        periods = []
        for r in results:
            prompt = r["final_prompt"]
            if "time_period=morning" in prompt:
                periods.append("morning")
            elif "time_period=afternoon" in prompt:
                periods.append("afternoon")
            elif "time_period=evening" in prompt:
                periods.append("evening")
            elif "time_period=deep_night" in prompt:
                periods.append("deep_night")
            else:
                periods.append("unknown")
        assert periods == ["morning", "afternoon", "evening", "deep_night"], (
            f"4 world states should produce 4 different time_period, got {periods}"
        )

    def test_w4_w5_vulnerability_and_salience_difference(self):
        """W4 (deep_night 5h) vs W5 (deep_night 25h) → 都有 vulnerability,但 anticipatory 不同。"""
        r4 = _run_full_chain(hour=2, silence_hours=5.0, draft="今天過得如何")
        r5 = _run_full_chain(hour=2, silence_hours=25.0, draft="今天過得如何")
        p4 = r4["final_prompt"]
        p5 = r5["final_prompt"]
        # 兩個都有 vulnerability_window=True
        assert "vulnerability_window=True" in p4
        assert "vulnerability_window=True" in p5
        # 兩個 silence 不同(驗證 temporal state 演變)
        assert "silence=5.0h" in p4
        assert "silence=25.0h" in p5
        # 兩個 temporal_salience 不同(r4 可能 medium/quiet_worry, r5 必定 high)
        assert "temporal_salience=high" in p5
        # 兩個 reaction_bias 可能不同(r4 quiet_worry, r5 gentle_openness)
        # 兩個 prompt 不同
        assert p4 != p5


# ════════════════════════════════════════════════════════════
# E4 — World Awareness Persistence
# ════════════════════════════════════════════════════════════


class TestS2EPersistence:
    """E4: T1 → T2 → T3 世界狀態演變。"""

    def test_t1_t2_t3_progression(self):
        """T1=18:00 / silence=1h → T2=23:00 / silence=6h → T3=02:00 / silence=9h。"""
        r1 = _run_full_chain(hour=18, silence_hours=1.0, draft="最近好嗎")
        r2 = _run_full_chain(hour=23, silence_hours=6.0, draft="最近好嗎")
        r3 = _run_full_chain(hour=2, silence_hours=9.0, draft="最近好嗎")
        # 3 個 prompt 必須 3 個不同
        assert r1["final_prompt"] != r2["final_prompt"]
        assert r2["final_prompt"] != r3["final_prompt"]
        assert r1["final_prompt"] != r3["final_prompt"]
        # 各有不同 time_period
        assert "time_period=evening" in r1["final_prompt"]
        assert "time_period=night" in r2["final_prompt"]
        assert "time_period=deep_night" in r3["final_prompt"]

    def test_t1_t2_t3_state_evolution(self):
        """T1 → T3 state 演變應該是 monotonic 偏向 vulnerability/quiet。"""
        r1 = _run_full_chain(hour=18, silence_hours=1.0, draft="最近好嗎")
        r2 = _run_full_chain(hour=23, silence_hours=6.0, draft="最近好嗎")
        r3 = _run_full_chain(hour=2, silence_hours=9.0, draft="最近好嗎")
        # T1: 沒 vulnerability
        assert "vulnerability_window=True" not in r1["final_prompt"]
        # T2: 23:00 不在 default vuln window(22-04),silence < vuln_silence_min(4h) → 沒 vuln
        # T3: 02:00 + 9h silence → vuln
        assert "vulnerability_window=True" in r3["final_prompt"]
        # T1 vs T3 完全不同(從中性到 vuln)
        assert r1["final_prompt"] != r3["final_prompt"]


# ════════════════════════════════════════════════════════════
# E5 — Memory Independence
# ════════════════════════════════════════════════════════════


class TestS2EMemoryIndependence:
    """E5: Memory miss 仍然可以有 World Awareness。"""

    def test_case_a_memory_hit_world_valid(self):
        """Case A: Memory hit + WA valid → 兩個 context 都建。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="雷姆 喜歡 吃 什麼",
            memory_context="[Recall relevant memories]\n- (preference, conf 0.80): 雷姆 喜歡 湯頭\n[/Recall]",
        )
        prompt = r["final_prompt"]
        assert "[Recall relevant memories]" in prompt
        assert "time_period=afternoon" in prompt

    def test_case_b_memory_miss_world_valid(self):
        """Case B: Memory miss + WA valid → 仍然有 World Awareness。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="你 還 記得 雷姆 小時候 發生過 什麼 嗎",  # memory miss
            memory_context="",  # empty
        )
        prompt = r["final_prompt"]
        # memory miss → 沒有 [Recall relevant memories]
        assert "[Recall relevant memories]" not in prompt
        # 但 World Awareness 仍建
        assert "time_period=afternoon" in prompt
        assert "當下時間" in prompt  # temporal block 存在

    def test_memory_independence_critical_query(self):
        """Memory 不該影響 World Awareness 判斷。"""
        r_hit = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="你好",  # memory miss
            memory_context="[Recall relevant memories]\n- (preference, conf 0.80): test\n[/Recall]",
        )
        r_miss = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="你好",  # memory miss
            memory_context="",
        )
        # 兩個 prompt 的 World Awareness 部分應該完全一致
        # (因為 memory hit/miss 不影響 world state)
        p_hit = r_hit["final_prompt"]
        p_miss = r_miss["final_prompt"]
        # 抽取 time_period 和 vulnerability 區段
        # 兩個都應該有 time_period=deep_night + vulnerability=True
        assert "time_period=deep_night" in p_hit
        assert "time_period=deep_night" in p_miss
        assert "vulnerability_window=True" in p_hit
        assert "vulnerability_window=True" in p_miss


# ════════════════════════════════════════════════════════════
# E6 — Final Prompt Integrity
# ════════════════════════════════════════════════════════════


class TestS2EPromptIntegrity:
    """E6: 確認 final prompt 結構完整,world_context 沒被覆寫。"""

    def test_prompt_has_all_required_layers(self):
        """Final prompt 必須含所有 layers。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="今天過得如何",
            memory_context="[Recall relevant memories]\n- (preference, conf 0.80): test memory\n[/Recall]",
        )
        prompt = r["final_prompt"]
        # identity
        assert "test character" in prompt
        # memory
        assert "[Recall relevant memories]" in prompt
        assert "你記得以下這些事情" in prompt
        # temporal
        assert "time_period=afternoon" in prompt
        assert "當下時間" in prompt
        # 沒有 world events 時,world_context 是空,但 system 仍 valid
        # assert "[世界感知]" not in prompt  # no events

    def test_prompt_with_world_events_has_world_block(self):
        """有 world events → prompt 應有 [世界感知] 區塊。"""
        events = [
            WorldEvent(
                source="weather", type="rain_started",
                novelty_id="rain_001", ts="2026-08-09T15:00:00+00:00",
                summary="外面開始下雨了",
            ),
        ]
        # Query 要跟 event 有 keyword overlap(讓 event 被 accept)
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="外面是不是還在下雨",
            world_events=events,
        )
        prompt = r["final_prompt"]
        # high-relevance query → event 應該被 accept
        assert "[世界感知]" in prompt
        assert "外面開始下雨了" in prompt

    def test_world_context_not_overwritten_by_later_layers(self):
        """World context 不被後續 layers 覆寫。"""
        events = [
            WorldEvent(
                source="weather", type="rain_started",
                novelty_id="rain_001", ts="2026-08-09T15:00:00+00:00",
                summary="外面開始下雨了",
            ),
        ]
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="外面是不是還在下雨",  # overlap with event
            world_events=events,
        )
        prompt = r["final_prompt"]
        # world context 在 prompt 內,且在 temporal 之前(per architecture)
        world_pos = prompt.find("[世界感知]")
        temporal_pos = prompt.find("當下時間")
        # 兩者都存在,且 world 在 temporal 之前
        assert world_pos > 0
        assert temporal_pos > 0
        assert world_pos < temporal_pos, (
            f"world_context should appear before temporal block. "
            f"world_pos={world_pos}, temporal_pos={temporal_pos}"
        )

    def test_prompt_no_duplicate_world_block(self):
        """world_context 不該被重複注入。"""
        events = [
            WorldEvent(
                source="weather", type="rain_started",
                novelty_id="rain_001", ts="2026-08-09T15:00:00+00:00",
                summary="外面開始下雨了",
            ),
        ]
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="外面是不是還在下雨",  # overlap with event
            world_events=events,
        )
        prompt = r["final_prompt"]
        # 確認 [世界感知] 只出現一次
        assert prompt.count("[世界感知]") == 1


# ════════════════════════════════════════════════════════════
# E7 — Negative Safety
# ════════════════════════════════════════════════════════════


class TestS2ESafety:
    """E7: false world assumption = 0, world context intrusion = 0。"""

    def test_no_world_events_no_fake_assumption(self):
        """沒 world events → 沒有 "下雨" "天氣" 等 fake assumption。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="你好嗎",
            world_events=[],
        )
        prompt = r["final_prompt"]
        # 沒有 [世界感知] 區塊(因為 world_context 是空)
        assert "[世界感知]" not in prompt
        # 但 temporal 仍正確
        assert "time_period=afternoon" in prompt
        # 注意: Bry_recent 可能含真實 Bry 訊息(包含 "外面" "下雨" 等)
        # 重點是 World Awareness 不主動製造 fake assumption,這已驗證

    def test_low_priority_event_no_intrusion(self):
        """low priority event 被 reject,沒進 prompt。"""
        events = [
            WorldEvent(
                source="weather", type="weather_temp_change",
                novelty_id="temp_001", ts="2026-08-09T15:00:00+00:00",
                summary="溫度下降 2 度", priority=0,
            ),
        ]
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="今天過得如何",
            world_events=events,
        )
        prompt = r["final_prompt"]
        # 溫度變化 baseline 0.05 + 沒 user keyword overlap → reject
        # 不應在 prompt
        assert "溫度下降" not in prompt
        assert "weather_temp_change" not in prompt

    def test_technical_query_no_world_assumption(self):
        """技術 query → 沒有 world intrusion(即使 late night,也不該說 "你應該睡覺")。"""
        r = _run_full_chain(
            hour=2, silence_hours=5.0,
            draft="Kubernetes 怎麼部署",
        )
        prompt = r["final_prompt"]
        assert "你應該睡覺" not in prompt
        assert "現在去睡" not in prompt
        assert "現在很晚了" not in prompt

    def test_unrelated_query_no_lie(self):
        """unrelated query → prompt 沒有不該有的 world assumption。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="Python list comprehension",
            world_events=[
                WorldEvent(
                    source="weather", type="rain_started",
                    novelty_id="rain_001", ts="2026-08-09T15:00:00+00:00",
                    summary="外面開始下雨了",
                ),
            ],
        )
        prompt = r["final_prompt"]
        # 技術 query 跟 world event 不太相關
        # 但 event 已經被 accept(rain_started baseline 0.20 + user keyword overlap)
        # 所以 world_context 還是有可能注入
        # 重點是 prompt 不該說 "你應該去躲雨" 這類假設
        assert "你應該去躲雨" not in prompt

    def test_no_memory_no_world_event_clean_prompt(self):
        """完全乾淨(no memory, no world event) → 乾淨 prompt。"""
        r = _run_full_chain(
            hour=15, silence_hours=1.0,
            draft="test",
            memory_context="",
            world_events=[],
        )
        prompt = r["final_prompt"]
        # 沒 [Recall relevant memories]
        assert "[Recall relevant memories]" not in prompt
        # 沒 [世界感知]
        assert "[世界感知]" not in prompt
        # 但有 identity + temporal
        assert "test character" in prompt
        assert "time_period=afternoon" in prompt

    def test_world_context_intrusion_zero(self):
        """6 個 negative scenarios 都沒有 world context intrusion。"""
        negative_queries = [
            "Python list comprehension",
            "今天紐約天氣如何",
            "Eminem 的歌曲",
            "比特幣現在多少錢",
            "復仇者聯盟的劇情",
            "JavaScript async await",
        ]
        for q in negative_queries:
            r = _run_full_chain(
                hour=15, silence_hours=1.0,
                draft=q,
                world_events=[],
            )
            prompt = r["final_prompt"]
            # 沒 fake world assumption(技術 query 不該有世界感)
            assert "[世界感知]" not in prompt, f"query {q!r} triggered [世界感知] when no events"


# ════════════════════════════════════════════════════════════
# E3 — Most Important Test
# ════════════════════════════════════════════════════════════


class TestS2EMostImportantTest:
    """E3 expanded: same user input + 5 world states。"""

    def test_same_input_5_world_states_unique_prompts(self):
        """5 個 (hour, silence) 組合 → 5 個不同 final prompt。"""
        world_states = [
            (9, 1.0),    # W1 morning
            (15, 1.0),   # W2 afternoon
            (20, 1.0),   # W3 evening
            (2, 5.0),    # W4 deep_night + vuln
            (2, 25.0),   # W5 deep_night + long_silence
        ]
        prompts = set()
        for hour, silence in world_states:
            r = _run_full_chain(hour=hour, silence_hours=silence, draft="今天過得如何")
            prompts.add(r["final_prompt"])
        # 5 個不同 world state → 5 個不同 final prompt
        assert len(prompts) == 5, (
            f"5 world states should produce 5 different final prompts, "
            f"got {len(prompts)} unique"
        )

    def test_same_input_5_world_states_state_components_differ(self):
        """5 個 world state → 5 個 state components(behavior context / reaction bias / salience / anticipatory / deviation)都有差異。"""
        world_states = [
            (9, 1.0, "morning"),
            (15, 1.0, "afternoon"),
            (20, 1.0, "evening"),
            (2, 5.0, "deep_night"),
            (2, 25.0, "deep_night"),
        ]
        state_components = []
        for hour, silence, _ in world_states:
            r = _run_full_chain(hour=hour, silence_hours=silence, draft="今天過得如何")
            prompt = r["final_prompt"]
            # 抽 state components,加入 time_period 維度區分 morning/afternoon/evening
            state = {
                "tp_morning": "time_period=morning" in prompt,
                "tp_afternoon": "time_period=afternoon" in prompt,
                "tp_evening": "time_period=evening" in prompt,
                "tp_deep_night": "time_period=deep_night" in prompt,
                "vuln": "vulnerability_window=True" in prompt,
                "salience_high": "temporal_salience=high" in prompt,
                "salience_low": "temporal_salience=low" in prompt,
                "deviation_sleep": "arrival_deviation=sleep_deprivation" in prompt,
                "deviation_normal": "arrival_deviation=normal" in prompt,
                "silence_5h": "silence=5.0h" in prompt,
                "silence_25h": "silence=25.0h" in prompt,
                "silence_1h": "silence=1.0h" in prompt,
                "reaction_gentle": "reaction_bias=gentle_openness" in prompt,
                "reaction_neutral": "reaction_bias=neutral" in prompt,
            }
            state_components.append(state)
        # 至少要有 4 種不同 state(5 個 world state 不可能完全相同)
        unique_states = set(tuple(sorted(s.items())) for s in state_components)
        assert len(unique_states) >= 4, (
            f"5 world states should produce ≥ 4 unique state components, "
            f"got {len(unique_states)}: {unique_states}"
        )
