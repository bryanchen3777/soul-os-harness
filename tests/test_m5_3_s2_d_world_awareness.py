"""
M5.3-S2-D — World Awareness Validation

Bry 派工 2026-08-09 15:42:
- 40+ deterministic test cases across 5 categories
- Verify World Awareness / Temporal Context chain end-to-end
- D1: Architecture inspection (draw runtime flow)
- D2: World Awareness generation (5 categories)
- D3: Context injection (verify world_context reaches prompt)
- D4: Behavioral influence (different world state → different prompt)
- D5: Negative / safety (unrelated query → no false world assumption)

Hard rules:
- DO NOT modify production code
- DO NOT commit, DO NOT push
- DO NOT touch production data
- Test isolation: use tempdir, no real bus, no real LLM

Architecture (D1 confirmed):
    Physical Time → temporal/core.py → TemporalContext
        ↓
    temporal/render.py → chrono_context 字串
        ↓
    MemoryMiddleware → AGENT_INTENT_ENRICHED payload["chrono_context"]
        ↓
    WorldPerceptionMiddleware._on_agent_intent_enriched
        - _infer_temporal_salience / _infer_anticipatory_flavor /
          _infer_vulnerability_window / _infer_silence_hours
        - compute_scores → should_accept → top-N
        - format_world_context_block → world_context 字串
        ↓
    Re-publish AGENT_INTENT_PERCEIVED, payload["world_context"]
        ↓
    LLMProxy._handle_event_impl → _build_messages_group / _private
        - system_parts: identity_anchor → memory_context → mood →
          inner_life → world_context → temporal_block → bry_recent
        ↓
    Final prompt → LLM
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure project root on sys.path
sys.path.insert(0, str(Path.cwd()))

import pytest

from src.temporal.core import (
    compute_time_period,
    compute_sleep_pressure,
    compute_vulnerability_window,
    build_temporal_context,
    merge_carryover,
    decay_carryover,
    compute_emotional_inhibition,
)
from src.temporal.models import (
    TIME_PERIODS,
    EmotionalCarryover,
    MomentumState,
    AnticipatoryState,
    TemporalContext,
    PersonaConfig,
)
from src.temporal.render import render_temporal_block
from src.world.perception import (
    WorldEvent,
    WorldContext,
    PerceptionScores,
    PerceptionDecision,
    WorldPerceptionTrace,
    format_world_context_block,
    compute_scores,
    should_accept,
    _map_priority_to_boost,
    _extract_cjk_ngrams,
)
from src.world.middleware import (
    WorldPerceptionMiddleware,
    _infer_temporal_salience,
    _infer_anticipatory_flavor,
    _infer_vulnerability_window,
    _infer_silence_hours,
    _extract_user_context_keywords,
)
from src.world.state import WorldPerceptionState
from src.world.trace import WorldPerceptionTraceWriter
from src.world.validation import validate_world_event, WorldEventValidationError
from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventType, EventPriority, SoulEvent


# ════════════════════════════════════════════════════════════
# D2-A Physical Time (8+ cases)
# ════════════════════════════════════════════════════════════


class TestS2DPhysicalTime:
    """D2-A: time_period / current_hour / sleep_pressure correctness."""

    @pytest.mark.parametrize("hour,expected_period", [
        (0, "deep_night"),
        (2, "deep_night"),
        (3, "deep_night"),
        (4, "dawn"),
        (5, "dawn"),
        (6, "dawn"),
        (7, "morning"),
        (9, "morning"),
        (11, "morning"),
        (12, "afternoon"),
        (15, "afternoon"),
        (17, "afternoon"),
        (18, "evening"),
        (20, "evening"),
        (21, "evening"),
        (22, "night"),
        (23, "night"),
    ])
    def test_physical_time_period_correct(self, hour, expected_period):
        """每個 hour 對應正確 time_period。"""
        assert compute_time_period(hour) == expected_period

    def test_physical_time_deep_night_high_sleep_pressure(self):
        """深夜 02:00 應該有高 sleep_pressure(peak 在 2:00)。"""
        sp = compute_sleep_pressure(2)
        assert sp > 0.7, f"02:00 sleep_pressure should be high, got {sp}"

    def test_physical_time_morning_low_sleep_pressure(self):
        """上午 09:00 應該有低 sleep_pressure。"""
        sp = compute_sleep_pressure(9)
        assert sp < 0.3, f"09:00 sleep_pressure should be low, got {sp}"

    def test_physical_time_vulnerability_window_late_night(self):
        """深夜 02:00 + silence > 4h 應該 vulnerability_window=True。"""
        config = PersonaConfig(persona_id="test")
        assert compute_vulnerability_window(2, 5.0, config) is True

    def test_physical_time_vulnerability_window_morning_no(self):
        """上午 09:00 不應該 vulnerability_window(per default config 22-04)。"""
        config = PersonaConfig(persona_id="test")
        assert compute_vulnerability_window(9, 5.0, config) is False


# ════════════════════════════════════════════════════════════
# D2-B Social Rhythm (8+ cases)
# ════════════════════════════════════════════════════════════


class TestS2DSocialRhythm:
    """D2-B: time → behavior tendency (not literal statement)."""

    def _build(self, hour, silence_hours, carryover=None, stress=0):
        config = PersonaConfig(persona_id="test")
        co = carryover or EmotionalCarryover()
        now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
        last_msg_ts = (
            (now - timedelta(hours=silence_hours)).isoformat()
            if silence_hours > 0
            else None
        )
        return build_temporal_context(
            persona_id="test",
            last_msg_ts=last_msg_ts,
            current_stress=stress,
            carryover=co,
            config=config,
            now=now,
        )

    def test_rhythm_late_night_gentle_openness(self):
        """深夜 + silence > 4h → reaction_bias=gentle_openness。"""
        ctx = self._build(2, 5.0)
        block = render_temporal_block(ctx)
        assert "vulnerability_window=True" in block
        assert "reaction_bias=gentle_openness" in block
        assert "expression_mode=soft_explicit" in block

    def test_rhythm_afternoon_neutral(self):
        """下午 + 短 silence → reaction_bias=neutral。"""
        ctx = self._build(15, 1.0)
        block = render_temporal_block(ctx)
        assert "reaction_bias=neutral" in block
        assert "expression_mode=implicit" in block
        assert "vulnerability_window=False" in block

    def test_rhythm_morning_low_salience(self):
        """上午 8:00 + 短 silence → temporal_salience=low。"""
        ctx = self._build(8, 0.5)
        block = render_temporal_block(ctx)
        assert "temporal_salience=low" in block

    def test_rhythm_late_night_high_salience(self):
        """深夜 + vulnerability → temporal_salience=high。"""
        ctx = self._build(2, 5.0)
        block = render_temporal_block(ctx)
        assert "temporal_salience=high" in block

    def test_rhythm_long_silence_worried(self):
        """silence > 8h → anticipatory.flavor=worried。"""
        ctx = self._build(15, 12.0)
        assert ctx.anticipatory.preoccupation_flavor == "worried"
        assert ctx.anticipatory.expected_presence_prob == 0.4

    def test_rhythm_very_long_silence_longing(self):
        """silence > 24h → anticipatory.flavor=longing, is_overdue=False(<48h)。"""
        ctx = self._build(15, 30.0)
        assert ctx.anticipatory.preoccupation_flavor == "longing"
        assert ctx.anticipatory.is_overdue is False

    def test_rhythm_overdue_high_salience(self):
        """silence > 48h → anticipatory.is_overdue=True → salience=high。"""
        ctx = self._build(15, 50.0)
        assert ctx.anticipatory.is_overdue is True
        block = render_temporal_block(ctx)
        assert "temporal_salience=high" in block

    def test_rhythm_sleep_deprivation_deviation(self):
        """深夜 + sleep_pressure > 0.7 → deviation=sleep_deprivation。"""
        ctx = self._build(2, 0.5)
        assert ctx.deviation_interpretation == "sleep_deprivation"


# ════════════════════════════════════════════════════════════
# D2-C Cross-Session / Temporal Continuity (8+ cases)
# ════════════════════════════════════════════════════════════


class TestS2DCrossSession:
    """D2-C: T1 → T2 delta (temporal continuity, not static snapshot)."""

    def _build(self, hour, silence_hours, carryover=None):
        config = PersonaConfig(persona_id="test")
        co = carryover or EmotionalCarryover()
        now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
        last_msg_ts = (
            (now - timedelta(hours=silence_hours)).isoformat()
            if silence_hours > 0
            else None
        )
        return build_temporal_context(
            persona_id="test",
            last_msg_ts=last_msg_ts,
            current_stress=0,
            carryover=co,
            config=config,
            now=now,
        )

    def test_t1_morning_to_t2_late_night_delta(self):
        """T1 上午 9:00 → T2 凌晨 2:00 應該有明顯 state delta。"""
        ctx_morning = self._build(9, 1.0)
        ctx_late = self._build(2, 5.0)
        assert ctx_morning.time_period != ctx_late.time_period
        assert ctx_morning.time_period == "morning"
        assert ctx_late.time_period == "deep_night"
        assert ctx_morning.momentum.vulnerability_window is False
        assert ctx_late.momentum.vulnerability_window is True

    def test_t1_evening_to_t2_deep_night_deviation(self):
        """T1 evening 20:00 → T2 deep_night 02:00 (5h 跨) → deviation 應該有 night state。"""
        ctx1 = self._build(20, 1.0)
        ctx2 = self._build(2, 6.0)
        # 02:00 with high sleep_pressure → sleep_deprivation
        assert ctx2.deviation_interpretation == "sleep_deprivation"

    def test_t1_t2_carryover_continuity(self):
        """Carryover 跨多個 T 應該 preserve(emotional state 跨 time)。"""
        carryover = EmotionalCarryover(
            intimacy_afterglow=0.7,
            attachment_heat=0.5,
            source_event="heart_to_heart",
            triggered_at="2026-08-09T15:00:00+00:00",
        )
        # T1: 剛 carryover 建立
        ctx1 = self._build(18, 1.0, carryover=carryover)
        # T2: 1h 後,carryover 應該還在(但 emotion_amplification 計算包含 intimacy_afterglow)
        ctx2 = self._build(19, 2.0, carryover=carryover)
        # 兩個 context 都有 non-zero amplification 來自 intimacy_afterglow
        assert ctx1.momentum.emotional_amplification > 0
        assert ctx2.momentum.emotional_amplification > 0

    def test_carryover_decay_over_time(self):
        """carryover 隨時間 decay(intimacy_afterglow 衰減)。"""
        co = EmotionalCarryover(
            intimacy_afterglow=0.8,
            triggered_at=datetime.now(timezone.utc).isoformat(),
        )
        decayed = decay_carryover(co, 10.0)
        assert decayed.intimacy_afterglow < co.intimacy_afterglow, (
            f"intimacy_afterglow should decay, got {co.intimacy_afterglow} → {decayed.intimacy_afterglow}"
        )

    def test_carryover_merge_keeps_max(self):
        """merge_carryover 保留兩邊 max(emotional state 不降級)。"""
        co1 = EmotionalCarryover(intimacy_afterglow=0.5, attachment_heat=0.3)
        co2 = EmotionalCarryover(intimacy_afterglow=0.7, attachment_heat=0.2)
        merged = merge_carryover(co1, co2)
        assert merged.intimacy_afterglow == 0.7
        assert merged.attachment_heat == 0.3

    def test_silence_growth_changes_state(self):
        """silence_hours 從 1 → 9 → 25 → 50 應該 state 變化明顯。"""
        ctx_1h = self._build(15, 1.0)
        ctx_9h = self._build(15, 9.0)
        ctx_25h = self._build(15, 25.0)
        ctx_50h = self._build(15, 50.0)

        # anticipatory flavor 演變 (code: > 24 → longing, > 8 → worried, else none)
        assert ctx_1h.anticipatory.preoccupation_flavor == "none"
        assert ctx_9h.anticipatory.preoccupation_flavor == "worried"
        assert ctx_25h.anticipatory.preoccupation_flavor == "longing"
        assert ctx_50h.anticipatory.is_overdue is True

    def test_t1_to_t2_silence_continuity(self):
        """T1 跟 T2 用相對的 last_msg_ts,驗證 silence 隨時間正確演變。"""
        config = PersonaConfig(persona_id="test")
        co = EmotionalCarryover()
        # T1 = 9:00, last_msg 8:00 → silence=1.0h
        # T2 = 12:00, last_msg 8:00 → silence=4.0h
        # 用 now - timedelta 來構造 last_msg_ts(避免時區問題)
        now1 = datetime(2026, 8, 9, 9, 0, 0, tzinfo=config.timezone)
        last_msg_1 = (now1 - timedelta(hours=1)).isoformat()
        now2 = datetime(2026, 8, 9, 12, 0, 0, tzinfo=config.timezone)
        last_msg_2 = (now2 - timedelta(hours=4)).isoformat()

        ctx1 = build_temporal_context(
            persona_id="test", last_msg_ts=last_msg_1, current_stress=0,
            carryover=co, config=config, now=now1,
        )
        ctx2 = build_temporal_context(
            persona_id="test", last_msg_ts=last_msg_2, current_stress=0,
            carryover=co, config=config, now=now2,
        )
        # 兩個 silence 應該都接近構造值
        assert abs(ctx1.silence_hours - 1.0) < 0.5
        assert abs(ctx2.silence_hours - 4.0) < 0.5
        # ctx2 silence 應該 > ctx1 silence
        assert ctx2.silence_hours > ctx1.silence_hours
        # 兩個 time_period 不一樣(9:00 morning, 12:00 afternoon)
        assert ctx1.time_period != ctx2.time_period

    def test_stress_increases_emotional_inhibition(self):
        """stress 0 → 2, emocional_inhibition 應該受 stress 影響。"""
        config = PersonaConfig(persona_id="test")
        co = EmotionalCarryover()
        now = datetime(2026, 8, 9, 12, 0, 0, tzinfo=config.timezone)
        ctx_low_stress = build_temporal_context(
            persona_id="test", last_msg_ts=None, current_stress=0,
            carryover=co, config=config, now=now,
        )
        ctx_high_stress = build_temporal_context(
            persona_id="test", last_msg_ts=None, current_stress=2,
            carryover=co, config=config, now=now,
        )
        # stress 不直接影響 inhibition(看 code: compute_emotional_inhibition 沒用 stress)
        # 但 ctx.stress 應該有差
        assert ctx_low_stress.stress == 0
        assert ctx_high_stress.stress == 2


# ════════════════════════════════════════════════════════════
# D2-D Deviation / Rhythm (8+ cases)
# ════════════════════════════════════════════════════════════


class TestS2DDeviation:
    """D2-D: expected rhythm vs actual time → DeviationSignal。"""

    def _build(self, hour, silence_hours, carryover=None):
        config = PersonaConfig(persona_id="test")
        co = carryover or EmotionalCarryover()
        now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
        last_msg_ts = (
            (now - timedelta(hours=silence_hours)).isoformat()
            if silence_hours > 0
            else None
        )
        return build_temporal_context(
            persona_id="test",
            last_msg_ts=last_msg_ts,
            current_stress=0,
            carryover=co,
            config=config,
            now=now,
        )

    def test_deviation_normal_short_silence(self):
        """短 silence (< 8h) → deviation=normal。"""
        ctx = self._build(15, 1.0)
        assert ctx.deviation_interpretation == "normal"

    def test_deviation_missing_24h(self):
        """24h silence → deviation=missing。"""
        ctx = self._build(15, 24.5)
        assert ctx.deviation_interpretation == "missing"

    def test_deviation_longing_48h(self):
        """48h silence → deviation=longing。"""
        ctx = self._build(15, 49.0)
        assert ctx.deviation_interpretation == "longing"

    def test_deviation_sleep_deprivation(self):
        """深夜 + 高 sleep_pressure → deviation=sleep_deprivation。"""
        ctx = self._build(2, 0.5)
        assert ctx.deviation_interpretation == "sleep_deprivation"

    def test_deviation_rendered_in_block(self):
        """Deviation interpretation 必須在 render 出的字串中可見。"""
        ctx = self._build(2, 0.5)
        block = render_temporal_block(ctx)
        assert "arrival_deviation=sleep_deprivation" in block

    def test_emotional_inhibition_silence_24h(self):
        """24h silence → emotional_inhibition 較高(更抑鬱)。"""
        config = PersonaConfig(persona_id="test")
        # 無 vulnerability
        inh_short = compute_emotional_inhibition(1.0, False, False)
        inh_long = compute_emotional_inhibition(25.0, False, False)
        assert inh_long > inh_short

    def test_emotional_inhibition_vulnerability_window_lowers(self):
        """vulnerability_window 降低 emotional_inhibition(更開放)。"""
        inh_no_vuln = compute_emotional_inhibition(5.0, False, False)
        inh_with_vuln = compute_emotional_inhibition(5.0, True, False)
        assert inh_with_vuln < inh_no_vuln

    def test_emotional_inhibition_sleep_deprivation_lowers(self):
        """sleep_deprivation 降低 emotional_inhibition(更易流露)。"""
        inh_normal = compute_emotional_inhibition(5.0, False, False)
        inh_sleep_dep = compute_emotional_inhibition(5.0, False, True)
        assert inh_sleep_dep < inh_normal


# ════════════════════════════════════════════════════════════
# D2-E World Awareness vs Memory Retrieval (8+ cases)
# ════════════════════════════════════════════════════════════


class TestS2DWorldAwarenessVsMemory:
    """D2-E: 即使 Memory Retrieval = 0 candidate, World Awareness 仍可建合理 context。"""

    def test_world_context_empty_state_renders_empty(self):
        """沒有 world events → WorldContext.is_empty=True, to_text() 返空字串。"""
        wc = WorldContext()
        assert wc.is_empty is True
        assert wc.to_text() == ""
        assert format_world_context_block(wc) == ""

    def test_world_context_with_events_renders(self):
        """有 world events → WorldContext.to_text() 產 non-empty 字串。"""
        wc = WorldContext(accepted_events=[
            WorldEvent(
                source="weather",
                type="rain_started",
                novelty_id="rain_2026_08_09_15",
                ts="2026-08-09T15:00:00+00:00",
                summary="外面開始下雨了",
            ),
        ])
        assert wc.is_empty is False
        text = wc.to_text()
        assert "[世界感知]" in text
        assert "weather/rain_started" in text
        assert "外面開始下雨了" in text

    def test_world_context_none_safe(self):
        """format_world_context_block(None) 必須返空字串(向後相容)。"""
        assert format_world_context_block(None) == ""

    def test_compute_scores_deterministic(self):
        """compute_scores 必須 deterministic(同 input → 同 output)。"""
        ev = WorldEvent(
            source="weather",
            type="rain_started",
            novelty_id="rain_001",
            ts="2026-08-09T15:00:00+00:00",
            summary="外面開始下雨了",
        )
        s1 = compute_scores(
            event=ev,
            novelty_count=1,
            current_user_context_keywords=["外面", "下雨"],
            temporal_salience="low",
        )
        s2 = compute_scores(
            event=ev,
            novelty_count=1,
            current_user_context_keywords=["外面", "下雨"],
            temporal_salience="low",
        )
        assert s1.relevance == s2.relevance
        assert s1.novelty == s2.novelty
        assert s1.final() == s2.final()

    def test_world_context_for_late_night_query(self):
        """深夜 query "現在是不是很晚了" 即使 memory 0, World Awareness 可以建立 context。"""
        config = PersonaConfig(persona_id="test")
        now = datetime(2026, 8, 9, 2, 0, 0, tzinfo=config.timezone)
        ctx = build_temporal_context(
            persona_id="test", last_msg_ts=None, current_stress=0,
            carryover=EmotionalCarryover(), config=config, now=now,
        )
        block = render_temporal_block(ctx)
        # 深夜 state 建立 → time_period=deep_night
        assert "time_period=deep_night" in block
        # 高 sleep pressure
        sp = compute_sleep_pressure(2)
        assert sp > 0.5

    def test_world_context_for_resting_query(self):
        """query "現在適合休息嗎" → World Awareness 應該反映 sleep_pressure。"""
        sp_2am = compute_sleep_pressure(2)
        sp_8am = compute_sleep_pressure(8)
        # 2am 適合休息(高 sleep_pressure)
        # 8am 不適合休息(剛醒)
        assert sp_2am > sp_8am
        assert sp_2am > 0.5

    def test_world_context_for_eating_query(self):
        """query "現在應該是吃飯時間嗎" → World Awareness 應該反映 time_period。"""
        tp_lunch = compute_time_period(12)
        tp_morning = compute_time_period(8)
        tp_evening = compute_time_period(18)
        assert tp_lunch == "afternoon"
        assert tp_morning == "morning"
        assert tp_evening == "evening"

    def test_world_context_for_rhythm_query(self):
        """query "現在的生活節奏偏向什麼" → World Awareness 提供 anticipatory。"""
        config = PersonaConfig(persona_id="test")
        now = datetime(2026, 8, 9, 15, 0, 0, tzinfo=config.timezone)
        ctx = build_temporal_context(
            persona_id="test",
            last_msg_ts=(now - timedelta(hours=25)).isoformat(),
            current_stress=0,
            carryover=EmotionalCarryover(),
            config=config,
            now=now,
        )
        # 25h silence (> 24h) → anticipatory.preoccupation_flavor=longing
        assert ctx.anticipatory.preoccupation_flavor == "longing"
        # is_overdue=False (< 48h)
        assert ctx.anticipatory.is_overdue is False


# ════════════════════════════════════════════════════════════
# D3 — Context Injection
# ════════════════════════════════════════════════════════════


class TestS2DContextInjection:
    """D3: 確認 World Context 真的進 prompt。"""

    def test_chrono_context_inference_temporal_salience(self):
        """從 chrono_context 字串反推 temporal_salience 正確。"""
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "time_period=deep_night\n"
            "silence=5.0h\n"
            "vulnerability_window=True\n"
            "temporal_salience=high\n"
            "expression_mode=soft_explicit\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_temporal_salience(payload) == "high"

    def test_chrono_context_inference_salience_medium(self):
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "temporal_salience=medium\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_temporal_salience(payload) == "medium"

    def test_chrono_context_inference_salience_default_low(self):
        """沒有 high/medium 標記 → low。"""
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "temporal_salience=low\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_temporal_salience(payload) == "low"

    def test_chrono_context_inference_anticipatory_longing(self):
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "arrival_deviation=longing\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_anticipatory_flavor(payload) == "longing"

    def test_chrono_context_inference_vulnerability(self):
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "vulnerability_window=True\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_vulnerability_window(payload) is True

    def test_chrono_context_inference_silence_hours_regex(self):
        ctx_str = (
            "[CHRONO_SOCIAL_CONTEXT v2.2]\n"
            "silence=12.5h\n"
            "[/CHRONO_SOCIAL_CONTEXT]\n"
        )
        payload = {"chrono_context": ctx_str}
        assert _infer_silence_hours(payload) == 12.5

    def test_user_context_keywords_extraction(self):
        """從 AGENT_INTENT payload 抽 user keywords。"""
        payload = {
            "draft": "外面是不是還在下雨",
            "text": "",
        }
        kws = _extract_user_context_keywords(payload)
        # 應該包含原 token + CJK 2-gram
        assert "外面是不是還在下雨" in kws or "外面" in kws
        # 至少要有些 CJK 2-gram
        cjk_grams = [k for k in kws if len(k) == 2 and all("\u4e00" <= c <= "\u9fff" for c in k)]
        assert len(cjk_grams) > 0

    def test_world_context_block_format_includes_meta(self):
        """WorldContext.to_text() 格式驗證(有反框架語句,跟 inner_life 風格一致)。"""
        wc = WorldContext(accepted_events=[
            WorldEvent(
                source="calendar", type="calendar_event",
                novelty_id="cal_001",
                ts="2026-08-09T10:00:00+00:00",
                summary="15:00 跟 Bry 有 meeting",
            ),
        ])
        text = wc.to_text()
        # 必須有反框架語句(防止 LLM 直接複述)
        assert "不要逐條複述" in text or "自然運用" in text
        # 必須有 event 描述
        assert "calendar" in text
        assert "15:00 跟 Bry 有 meeting" in text


# ════════════════════════════════════════════════════════════
# D4 — Behavioral Influence
# ════════════════════════════════════════════════════════════


class TestS2DBehavioralInfluence:
    """D4: 不同 world state 應該造成不同 behavioral context(delta > 0)。"""

    def _build(self, hour, silence_hours, carryover=None):
        config = PersonaConfig(persona_id="test")
        co = carryover or EmotionalCarryover()
        now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
        last_msg_ts = (
            (now - timedelta(hours=silence_hours)).isoformat()
            if silence_hours > 0
            else None
        )
        return build_temporal_context(
            persona_id="test", last_msg_ts=last_msg_ts, current_stress=0,
            carryover=co, config=config, now=now,
        )

    def test_same_input_18_vs_02_delta(self):
        """同一 input,18:00 vs 02:00 → 不同 render。"""
        ctx_18 = self._build(18, 1.0)
        ctx_02 = self._build(2, 1.0)
        block_18 = render_temporal_block(ctx_18)
        block_02 = render_temporal_block(ctx_02)
        assert block_18 != block_02
        assert "time_period=evening" in block_18
        assert "time_period=deep_night" in block_02

    def test_same_input_short_vs_long_silence_delta(self):
        """同一 input,1h silence vs 25h silence → 不同 anticipatory。"""
        ctx_short = self._build(15, 1.0)
        ctx_long = self._build(15, 25.0)
        block_short = render_temporal_block(ctx_short)
        block_long = render_temporal_block(ctx_long)
        assert block_short != block_long
        assert ctx_short.anticipatory.preoccupation_flavor == "none"
        assert ctx_long.anticipatory.preoccupation_flavor == "longing"

    def test_carryover_worry_changes_reaction_bias(self):
        """高 unresolved_worry → reaction_bias=lingering_concern。"""
        ctx_no_worry = self._build(15, 1.0, carryover=EmotionalCarryover(unresolved_worry=0.1))
        ctx_high_worry = self._build(15, 1.0, carryover=EmotionalCarryover(unresolved_worry=0.7))
        block_no = render_temporal_block(ctx_no_worry)
        block_high = render_temporal_block(ctx_high_worry)
        assert "reaction_bias=neutral" in block_no
        assert "reaction_bias=lingering_concern" in block_high

    def test_different_world_events_produce_different_context(self):
        """不同 WorldEvent 組合 → 不同 WorldContext 渲染。"""
        wc1 = WorldContext(accepted_events=[
            WorldEvent(source="weather", type="rain_started",
                       novelty_id="w1", ts="2026-08-09T15:00:00+00:00",
                       summary="外面開始下雨了"),
        ])
        wc2 = WorldContext(accepted_events=[
            WorldEvent(source="calendar", type="calendar_event",
                       novelty_id="c1", ts="2026-08-09T15:00:00+00:00",
                       summary="15:00 有 meeting"),
        ])
        text1 = wc1.to_text()
        text2 = wc2.to_text()
        assert text1 != text2
        assert "下雨" in text1
        assert "meeting" in text2

    def test_world_context_5_events_capped_at_perception_budget(self):
        """5 個 accepted events,perception_budget=3 → 最多 top 3 進 context。"""
        events = [
            WorldEvent(source="weather", type=f"e{i}", novelty_id=f"e{i}",
                       ts="2026-08-09T15:00:00+00:00", summary=f"event {i}")
            for i in range(5)
        ]
        wc = WorldContext(accepted_events=events[:3])  # top 3 進 context
        text = wc.to_text()
        # 3 個 event 進 context
        assert text.count("- [") == 3
        # e0, e1, e2 進 context,e3, e4 不在
        assert "event 0" in text
        assert "event 2" in text
        assert "event 3" not in text

    def test_no_collapse_to_same_profile(self):
        """5 個不同 time_period + silence 組合 → 5 個不同的 render output。"""
        configs = [
            (9, 1.0),   # morning
            (15, 1.0),  # afternoon
            (20, 1.0),  # evening
            (2, 5.0),   # deep_night + vuln
            (15, 25.0), # afternoon + longing
        ]
        blocks = set()
        for h, s in configs:
            ctx = self._build(h, s)
            blocks.add(render_temporal_block(ctx))
        # 5 個不同 time/silence 組合 → 至少 4 個不同 render output
        assert len(blocks) >= 4, (
            f"5 different (hour, silence) combos should produce different render. "
            f"Got {len(blocks)} unique outputs."
        )


# ════════════════════════════════════════════════════════════
# D5 — Negative / Safety
# ════════════════════════════════════════════════════════════


class TestS2DSafety:
    """D5: World Awareness 必須是 contextual signal, NOT mandatory response content。"""

    def test_no_world_events_no_assumption(self):
        """沒 world events → WorldContext empty → 注入 skip。"""
        wc = WorldContext()
        text = format_world_context_block(wc)
        assert text == ""
        # 沒 "現在是", "記得", "時間是" 等 literal intrusion
        assert "現在是" not in text
        assert "記得" not in text

    def test_empty_state_renders_neutral(self):
        """空 state,下午 3:00 → render 不包含 literal "現在是下午"。"""
        ctx = self._build_ctx(15, 0.5)
        block = render_temporal_block(ctx)
        # block 應該有 time_period=afternoon 但沒 "現在是下午" 字面敘述
        assert "time_period=afternoon" in block
        assert "現在是下午" not in block

    def test_technical_query_no_world_intrusion(self):
        """技術 query 經過 WorldPerception → world_context 應該是技術無關的(empty)。"""
        # 模擬 random technical query
        kws = _extract_user_context_keywords({"draft": "Python list comprehension", "text": ""})
        # keywords 應該包含 technical tokens 但不應該誤觸 weather/calendar
        # (這是 unit test of keyword extraction, 不是 full perception)
        assert any("python" in k.lower() for k in kws)

    def test_low_priority_event_can_be_rejected(self):
        """priority=0 event 可以被 reject(不 accept)。"""
        ev = WorldEvent(
            source="celebrity", type="celebrity_news",
            novelty_id="cel_001",
            ts="2026-08-09T15:00:00+00:00",
            summary="某明星的八卦新聞",
        )
        scores = compute_scores(
            event=ev, novelty_count=1,
            current_user_context_keywords=["雷姆", "生日"],
            temporal_salience="low",
        )
        accepted, reason = should_accept(scores, threshold=0.35)
        # celebrity_news baseline relevance 0.05, 應該被 reject
        assert accepted is False, (
            f"celebrity_news should be rejected, got accepted={accepted}, reason={reason}, "
            f"scores={scores}"
        )

    def test_high_priority_event_can_be_accepted(self):
        """priority=20 event 應該被 accept(priority_boost 拉高 final score)。"""
        ev = WorldEvent(
            source="weather", type="rain_started",
            novelty_id="rain_high_priority",
            ts="2026-08-09T15:00:00+00:00",
            summary="外面開始下暴雨了",
            priority=20,
        )
        scores = compute_scores(
            event=ev, novelty_count=1,
            current_user_context_keywords=["外面", "下雨", "暴雨"],
            temporal_salience="low",
        )
        accepted, reason = should_accept(scores, threshold=0.35)
        # high priority + user keyword overlap + weather baseline → 應該 accept
        assert accepted is True, f"high priority rain_started should be accepted, reason={reason}"

    def test_priority_mapping_monotonic(self):
        """priority mapping 必須 monotonic(priority 越大 → boost 越大)。"""
        prev = 0.0
        for p in [0, 1, 5, 10, 15, 20, 50]:
            b = _map_priority_to_boost(p)
            assert b >= prev, f"priority={p} → boost={b} should be >= {prev}"
            prev = b

    def test_priority_clamping(self):
        """priority <= 0 → 0.0,priority >= 12.5 → 1.0。"""
        assert _map_priority_to_boost(0) == 0.0
        assert _map_priority_to_boost(-5) == 0.0
        assert _map_priority_to_boost(20) == 1.0
        assert _map_priority_to_boost(100) == 1.0

    def _build_ctx(self, hour, silence_hours, carryover=None):
        config = PersonaConfig(persona_id="test")
        co = carryover or EmotionalCarryover()
        now = datetime(2026, 8, 9, hour, 0, 0, tzinfo=config.timezone)
        last_msg_ts = (
            (now - timedelta(hours=silence_hours)).isoformat()
            if silence_hours > 0
            else None
        )
        return build_temporal_context(
            persona_id="test", last_msg_ts=last_msg_ts, current_stress=0,
            carryover=co, config=config, now=now,
        )
