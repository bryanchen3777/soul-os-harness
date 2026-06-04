"""
temporal/__init__.py
Soul OS — Phase 3.5 vendored from chrono-social-engine v2.2

時間-社交感知引擎。提供：
  - build_temporal_context()：根據 persona 與最後互動時間建立 TemporalContext
  - render_temporal_block()：把 TemporalContext 渲染成可注入 prompt 的字串
  - 資料類別：TemporalContext、EmotionalCarryover、MomentumState、AnticipatoryState、PersonaConfig
"""
from .core import (
    build_temporal_context,
    compute_time_period,
    compute_sleep_pressure,
    compute_emotional_inhibition,
    compute_vulnerability_window,
    merge_carryover,
    decay_carryover,
)
from .models import (
    TIME_PERIODS,
    EmotionalCarryover,
    MomentumState,
    AnticipatoryState,
    TemporalContext,
    PersonaConfig,
)
from .render import render_temporal_block

__all__ = [
    "build_temporal_context",
    "compute_time_period",
    "compute_sleep_pressure",
    "compute_emotional_inhibition",
    "compute_vulnerability_window",
    "merge_carryover",
    "decay_carryover",
    "render_temporal_block",
    "TIME_PERIODS",
    "EmotionalCarryover",
    "MomentumState",
    "AnticipatoryState",
    "TemporalContext",
    "PersonaConfig",
]
