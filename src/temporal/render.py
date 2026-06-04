"""
temporal/render.py — Phase 3.5 vendored from chrono-social-engine v2.2
renderer：把 TemporalContext 轉成可注入 prompt 的字串
"""
from __future__ import annotations

from .models import TemporalContext

CHARS_PER_TOKEN = 4


def render_temporal_block(ctx: TemporalContext) -> str:
    """
    將 TemporalContext 渲染為可插入 system prompt 的字串區塊（v2.2）。
    不再輸出 current_time / weekday 等時鐘資訊，改為 behavior-bias 欄位。
    """
    return (
        f"[CHRONO_SOCIAL_CONTEXT v2.2]\n"
        f"time_period={ctx.time_period}\n"
        f"silence={ctx.silence_hours:.1f}h\n"
        f"arrival_deviation={ctx.deviation_interpretation or 'none'}\n"
        f"vulnerability_window={ctx.momentum.vulnerability_window}\n"
        f"carryover_worry={ctx.carryover.unresolved_worry:.2f}\n"
        f"attachment_heat={ctx.carryover.attachment_heat:.2f}\n"
        f"reaction_bias={_compute_reaction_bias(ctx)}\n"
        f"temporal_salience={_compute_temporal_salience(ctx)}\n"
        f"expression_mode={_compute_expression_mode(ctx)}\n"
        f"[/CHRONO_SOCIAL_CONTEXT]\n"
    )


def _compute_reaction_bias(ctx: TemporalContext) -> str:
    if ctx.carryover.unresolved_worry > 0.5:
        return "lingering_concern"
    if ctx.momentum.vulnerability_window:
        return "gentle_openness"
    if ctx.deviation_interpretation == "sleep_deprivation":
        return "quiet_worry"
    if ctx.anticipatory.preoccupation_flavor == "longing" and ctx.silence_hours > 24:
        return "subdued_longing"
    if ctx.anticipatory.is_overdue:
        return "relief_mixed_reproach"
    return "neutral"


def _compute_temporal_salience(ctx: TemporalContext) -> str:
    if ctx.anticipatory.is_overdue:
        return "high"
    if ctx.momentum.vulnerability_window:
        return "high"
    if ctx.deviation_interpretation is not None and ctx.deviation_interpretation != "normal":
        return "medium"
    if ctx.silence_hours > 6:
        return "medium"
    return "low"


def _compute_expression_mode(ctx: TemporalContext) -> str:
    salience = _compute_temporal_salience(ctx)
    if salience == "high":
        return "soft_explicit"
    return "implicit"
