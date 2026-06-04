"""
temporal/core.py — Phase 3.5 vendored from chrono-social-engine v2.2
計算邏輯：build_temporal_context、compute_* 函式
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Literal

from .models import (
    TIME_PERIODS,
    AnticipatoryState,
    EmotionalCarryover,
    MomentumState,
    PersonaConfig,
    TemporalContext,
)


# ── Sleep Pressure Curve ─────────────────────────────────────────────────────

_SLEEP_PRESSURE_CURVE: tuple[tuple[int, float], ...] = tuple(
    (h, max(0.02, round(math.exp(-0.5 * ((h - 2) / 3) ** 2), 2)))
    for h in range(24)
)

# ── Emotional Inhibition Range ───────────────────────────────────────────────

_INHIBITION_RANGES: tuple[tuple[float, float, float], ...] = (
    (0.0,  2.0, 0.00),
    (2.0,  6.0, 0.15),
    (6.0,  12.0,0.35),
    (12.0, 24.0,0.55),
    (24.0, 72.0,0.75),
    (72.0, 200.0, 0.88),
    (200.0, float('inf'), 1.00),
)


# ── Computation Functions ─────────────────────────────────────────────────────

def compute_time_period(hour: int) -> str:
    for start, end, name in TIME_PERIODS:
        if start <= hour < end:
            return name
    return "deep_night"


def compute_sleep_pressure(current_hour: int) -> float:
    for h, p in _SLEEP_PRESSURE_CURVE:
        if h == current_hour:
            return max(0.0, min(1.0, p))
    return 0.0


def _get_inhibition_for_silence(silence_hours: float) -> float:
    for low, high, value in _INHIBITION_RANGES:
        if low <= silence_hours < high:
            return value
    return 0.88


def compute_emotional_inhibition(
    silence_hours: float,
    vulnerability_window: bool,
    sleep_deprivation: bool,
) -> float:
    base = _get_inhibition_for_silence(silence_hours)
    if vulnerability_window:
        base = max(0.0, base - 0.35)
    if sleep_deprivation:
        base = max(0.0, base - 0.25)
    return min(1.0, base)


def compute_vulnerability_window(
    current_hour: int,
    silence_hours: float,
    config: PersonaConfig,
) -> bool:
    start = config.vulnerability_hour_start
    end = config.vulnerability_hour_end
    if start <= end:
        in_hour_range = start <= current_hour < end
    else:
        in_hour_range = current_hour >= start or current_hour < end
    if not in_hour_range:
        return False
    return silence_hours >= config.vulnerability_silence_min


def build_temporal_context(
    persona_id: str,
    last_msg_ts: str | None,
    current_stress: int,
    carryover: EmotionalCarryover,
    config: PersonaConfig,
    now: datetime | None = None,
) -> TemporalContext:
    if now is None:
        now = datetime.now(config.timezone)
    current_hour = now.hour
    time_period = compute_time_period(current_hour)

    if last_msg_ts:
        try:
            prev = datetime.fromisoformat(last_msg_ts)
            silence_hours = (now - prev).total_seconds() / 3600.0
            silence_hours = max(0.0, silence_hours)
        except (ValueError, TypeError):
            silence_hours = 0.0
    else:
        silence_hours = 0.0

    vuln_window = compute_vulnerability_window(current_hour, silence_hours, config)
    sleep_pressure = compute_sleep_pressure(current_hour)
    sleep_dep = sleep_pressure > 0.7 and time_period in ("deep_night", "night")
    emocional_inhibition = compute_emotional_inhibition(silence_hours, vuln_window, sleep_dep)

    amplification = 0.0
    if vuln_window:
        amplification += 0.20
    if sleep_dep:
        amplification += 0.15
    if carryover.intimacy_afterglow > 0.6:
        amplification += 0.10
    if carryover.unresolved_worry > 0.5:
        amplification -= 0.15

    momentum = MomentumState(
        vulnerability_window=vuln_window,
        emotional_amplification=max(-0.5, min(0.5, amplification)),
    )

    if silence_hours > 24:
        flavor: Literal["none", "longing", "worried", "anxious"] = "longing"
        expected_prob = 0.3
    elif silence_hours > 8:
        flavor = "worried"
        expected_prob = 0.4
    else:
        flavor = "none"
        expected_prob = 0.6

    anticipatory = AnticipatoryState(
        preoccupation_flavor=flavor,
        expected_presence_prob=expected_prob,
        silence_hours=silence_hours,
        is_overdue=silence_hours > 48,
    )

    if time_period in ("deep_night", "night") and sleep_pressure > 0.7:
        deviation = "sleep_deprivation"
    elif silence_hours > 48:
        deviation = "longing"
    elif silence_hours > 24:
        deviation = "missing"
    else:
        deviation = "normal"

    return TemporalContext(
        persona_id=persona_id,
        current_hour=current_hour,
        time_period=time_period,
        silence_hours=silence_hours,
        carryover=carryover,
        momentum=momentum,
        anticipatory=anticipatory,
        deviation_interpretation=deviation,
        emocional_inhibition=emocional_inhibition,
        stress=current_stress,
    )


def merge_carryover(
    existing: EmotionalCarryover | None,
    new: EmotionalCarryover,
) -> EmotionalCarryover:
    if existing is None:
        return new

    def mx(a: float, b: float) -> float:
        return max(a, b)

    return EmotionalCarryover(
        intimacy_afterglow=mx(existing.intimacy_afterglow, new.intimacy_afterglow),
        unresolved_worry=mx(existing.unresolved_worry, new.unresolved_worry),
        emocional_openness_residue=mx(existing.emocional_openness_residue, new.emocional_openness_residue),
        attachment_heat=mx(existing.attachment_heat, new.attachment_heat),
        source_event=new.source_event if new.triggered_at >= existing.triggered_at else existing.source_event,
        triggered_at=max(existing.triggered_at, new.triggered_at),
        decay_rate=new.decay_rate,
    )


def decay_carryover(carryover: EmotionalCarryover, elapsed_hours: float) -> EmotionalCarryover:
    return carryover.apply_decay(elapsed_hours)
