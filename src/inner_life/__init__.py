"""
src/inner_life/ — Soul OS Inner Life Unified Architecture Foundation

M5.4-5.1 (Bry 派工 2026-08-09 18:25)

派工 派工 motivation:
  - 目標:建立 canonical Inner Life narrative/event identity boundary
  - 讓後續 Memory / Diary / Dream 可以真正掛在同一個 architecture 上
  - 不是三個獨立 subsystem 再互相加欄位
  - 是真正的 unified Inner Life architecture foundation

派工 派工 architecture principle (from派工):
                  Lived Experience
                         │
                         ▼
                  Inner Life Event
                         │
                  ┌──────┴──────┐
                  │             │
                identity      lineage
                  │             │
            ┌─────┼─────────────┼─────┐
            ▼     ▼             ▼     ▼
         Memory  Diary        Dream  Future

派工 派工 frozen contracts preservation:
  - SAGE / v1 schema 不動
  - M5.3 frozen contracts 不動
  - M5.4-1 narrative independence contract 不動 (Memory failure MUST NOT block Diary/Dream)
  - existing Memory/Diary/Dream behavior 不動
  - "Unified architecture ≠ shared failure dependency" — InnerLifeWriter 是
    optional, downstream writers 可不消費

派工 派工 identity semantics (派工明列):
  - event_id: 32 char lowercase hex (uuid4 no dashes), 不可重用
  - session_id: optional runtime session anchor
  - correlation_id: optional narrative group (NOT causation)
  - parent_event_id: optional causation chain (tree)
  - provenance: structured WHO/WHAT/WHERE/WHY
  - lineage_depth + lineage_path: 從 parent 衍生

Public API (this module exports):
  - InnerLifeEvent:  canonical event dataclass
  - Provenance:      structured provenance dataclass
  - InnerLifeWriter: canonical identity authority
  - InnerLifeWriterStats: observability counters
  - generate_event_id / now_utc_iso: id + ts generation
  - validate_*: field-level validators
  - derive_lineage: lineage depth + path derivation
  - IdentityValidationError: validation exception
  - event_to_dict / event_from_dict: serialization round-trip
  - provenance_to_dict / provenance_from_dict: provenance round-trip
  - Trigger type constants: TRIGGER_TYPE_USER_MESSAGE / _AGENT_REPLY /
                            _DIARY_MORNING / _DIARY_NIGHT / _DREAM_DREAM /
                            _DREAM_EVENT / _MEMORY_FACT / _SYSTEM
  - VALID_SOURCE_SYSTEMS: frozenset of valid source_system values

Out of scope (派工派工):
  - 完整 Memory migration
  - 完整 Diary migration
  - 完整 Dream migration
  - production data migration
  - historical backfill
  - Narrative Trace analytics UI
  - vector / embedding / semantic search
  - Event Bus redesign
  - SAGE redesign
  - unrelated refactor
"""
from __future__ import annotations

from .event import (
    InnerLifeEvent,
    Provenance,
    VALID_SOURCE_SYSTEMS,
    TRIGGER_TYPE_USER_MESSAGE,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
    TRIGGER_TYPE_MEMORY_FACT,
    TRIGGER_TYPE_SYSTEM,
)
from .identity import (
    EVENT_ID_LENGTH,
    EVENT_ID_PATTERN,
    TS_PATTERN,
    IdentityValidationError,
    derive_lineage,
    generate_event_id,
    now_utc_iso,
    validate_correlation_id,
    validate_event_id,
    validate_parent_event_id,
    validate_session_id,
    validate_source_world_event_novelty_id,
    validate_ts,
)
from .serialization import (
    event_from_dict,
    event_to_dict,
    provenance_from_dict,
    provenance_to_dict,
)
from .grouping import (
    group_by_correlation,
    group_by_session,
    group_by_world_occurrence,
)
from .submission_gate import (
    VALID_PRODUCER_TRIGGER_TYPES,
    SubmissionGate,
    SubmissionVerdict,
)
from .trace import NarrativeTraceWriter
from .trace_reader import NarrativeTraceReader
from .writer import InnerLifeWriter, InnerLifeWriterStats
from .emergent_projection import (
    DEFAULT_AGENT_ID,
    PROJECTABLE_NODE_TYPES,
    PROJECTION_TRACE_FILENAME,
    format_emergent_block,
    load_elevation_nodes,
    project_emergent,
)

__all__ = [
    # Core types
    "InnerLifeEvent",
    "Provenance",
    "InnerLifeWriter",
    "InnerLifeWriterStats",
    "NarrativeTraceWriter",
    "NarrativeTraceReader",
    # Emergent read-side projection (靈魂成長閉環 read side)
    "DEFAULT_AGENT_ID",
    "PROJECTABLE_NODE_TYPES",
    "PROJECTION_TRACE_FILENAME",
    "format_emergent_block",
    "load_elevation_nodes",
    "project_emergent",
    # SI-1: Shared Life Read-Side Grouping (pure functions, ephemeral)
    "group_by_world_occurrence",
    "group_by_correlation",
    "group_by_session",
    # SG-1: Elevation Submission Gate
    "SubmissionGate",
    "SubmissionVerdict",
    "VALID_PRODUCER_TRIGGER_TYPES",
    # Validation
    "IdentityValidationError",
    "EVENT_ID_LENGTH",
    "EVENT_ID_PATTERN",
    "TS_PATTERN",
    # Generation
    "generate_event_id",
    "now_utc_iso",
    "validate_event_id",
    "validate_session_id",
    "validate_correlation_id",
    "validate_parent_event_id",
    "validate_source_world_event_novelty_id",
    "validate_ts",
    "derive_lineage",
    # Serialization
    "event_to_dict",
    "event_from_dict",
    "provenance_to_dict",
    "provenance_from_dict",
    # Constants
    "VALID_SOURCE_SYSTEMS",
    "TRIGGER_TYPE_USER_MESSAGE",
    "TRIGGER_TYPE_AGENT_REPLY",
    "TRIGGER_TYPE_DIARY_MORNING",
    "TRIGGER_TYPE_DIARY_NIGHT",
    "TRIGGER_TYPE_DREAM_DREAM",
    "TRIGGER_TYPE_DREAM_EVENT",
    "TRIGGER_TYPE_MEMORY_FACT",
    "TRIGGER_TYPE_SYSTEM",
]

# ─────────────────────────────────────────────────────────────────────
# 升华层 adapter seam（可选，guarded import）
#
# elevation_adapter 是 Soul OS 侧唯一接触 soul-elevation 的地方。soul-elevation
# 是 path dependency；若未安装则 adapter 不可用，但不影响 inner_life 其余 API。
# 因此这里用 guarded import：失败只跳过导出，绝不 raise。
# ─────────────────────────────────────────────────────────────────────
try:
    from .elevation_adapter import (  # noqa: F401
        ElevationObserver,
        inner_life_event_to_input,
        run_elevation,
        sage_fact_to_input,
        v1_memory_to_input,
    )

    __all__ += [
        "ElevationObserver",
        "inner_life_event_to_input",
        "v1_memory_to_input",
        "sage_fact_to_input",
        "run_elevation",
    ]
except ImportError:  # soul_elevation 未安装 → adapter 不可用，静默跳过
    pass
