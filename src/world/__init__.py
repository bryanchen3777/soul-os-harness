"""
src/world/ — Soul OS M3 Phase 1: World Awareness

Bry 拍板 2026-08-07 19:40 + 2026-08-07 20:02 hardening:

ARCHITECTURE INVARIANTS (Phase 1, 不可違反):

  I. Perception ≠ Memory (P3 拍板 2026-08-07 20:02)
     ┌──────────────────────────────────────────────┐
     │ WORLD_EVENT                                   │
     │     ↓                                         │
     │ EPHEMERAL WORLD STATE (process lifetime)      │
     │     ↓                                         │
     │ PERCEPTION (deterministic, no LLM judge)      │
     │     ↓                                         │
     │ WORLD CONTEXT (注入 LLM prompt)                │
     │     ↓                                         │
     │ RESPONSE (LLM 自由運用)                         │
     └──────────────────────────────────────────────┘
     World Event 不直接進: SAGE / v1 memory / diary / dream / long-term memory
     Phase 1: memory_written == False 必須維持
     未來若要寫長期 memory, 必須走獨立 Memory Judge / Memory pipeline,
     不在本模組實作, 不在 Phase 1 預先建立 memory write path。

  II. Perception First (Invariant A from brief)
     World Event 必須先「感知」, 決定是否進入 context。
     不是 Tool Result → Immediate Action。

  III. Limited Awareness (Invariant B)
     Perception Budget (default 3) 限制每次進 context 的 event 數量。
     4 維 scoring (relevance / novelty / personal_significance /
     emotional_significance / temporal_significance) + threshold gate。

  IV. Quality > Quantity (Invariant C)
     Memory 是 optional outcome, 不是 default outcome。
     100 world events ≠ 100 memories。

  V. No Memory > Wrong Memory (Invariant D)
     Invalid event / low confidence / unclear significance → 不寫, 寧可不記。

  VI. World Awareness ≠ Agency (Invariant E, P3 hardening)
     Phase 1 只建立 Awareness, 不行動。
     不接 Weather / News / Calendar / Browser / Telegram / external action。
     不打 LLM judge (deterministic scoring only)。
     不代表 Bry 做事, Bry 仍是唯一 authority。

模組分工:
- perception.py : WorldEvent / PerceptionDecision / WorldContext / WorldPerceptionTrace
                  + deterministic scoring helpers (no LLM)
- validation.py : 薄 input validation (source whitelist / required fields / novelty_id)
- state.py      : WorldPerceptionState (EPHEMERAL, in-memory, process lifetime, restart 清空,
                  novelty index, expiry, bounded retention via max_active_events)
- trace.py      : WorldPerceptionTraceWriter (sidecar log, observability artifact, NOT memory)
- source.py     : SyntheticWorldEventSource (deterministic test driver, 5 scenarios)
- middleware.py : WorldPerceptionMiddleware (Bus subscriber, 對齊 MemoryMiddleware pattern)
"""
from .perception import (
    WorldEvent,
    PerceptionDecision,
    WorldContext,
    WorldPerceptionTrace,
    PerceptionScores,
    SCORE_WEIGHTS,
    DEFAULT_ACCEPT_THRESHOLD,
    TYPE_BASELINE_RELEVANCE,
    DEFAULT_TYPE_BASELINE_RELEVANCE,
    SELECTION_REJECTED_AT_VALIDATION,
    SELECTION_REJECTED_AT_THRESHOLD,
    SELECTION_BELOW_BUDGET,
    SELECTION_SELECTED_TOP_N,
    compute_scores,
    should_accept,
    format_world_context_block,
)
from .state import WorldPerceptionState
from .trace import WorldPerceptionTraceWriter
from .validation import validate_world_event, WorldEventValidationError
from .source import SyntheticWorldEventSource, SYNTHETIC_TEST_EVENTS
from .base import WorldEventSource
from .injector import WorldEventInjector
from .registry import WorldEventSourceRegistry, SourceStatus
from .middleware import WorldPerceptionMiddleware

__all__ = [
    # M3 Phase 1
    "WorldEvent",
    "PerceptionDecision",
    "WorldContext",
    "WorldPerceptionTrace",
    "PerceptionScores",
    "SCORE_WEIGHTS",
    "DEFAULT_ACCEPT_THRESHOLD",
    "TYPE_BASELINE_RELEVANCE",
    "DEFAULT_TYPE_BASELINE_RELEVANCE",
    "SELECTION_REJECTED_AT_VALIDATION",
    "SELECTION_REJECTED_AT_THRESHOLD",
    "SELECTION_BELOW_BUDGET",
    "SELECTION_SELECTED_TOP_N",
    "compute_scores",
    "should_accept",
    "format_world_context_block",
    "WorldPerceptionState",
    "WorldPerceptionTraceWriter",
    "validate_world_event",
    "WorldEventValidationError",
    "SyntheticWorldEventSource",
    "SYNTHETIC_TEST_EVENTS",
    "WorldPerceptionMiddleware",
    # M3.1 Phase A (Bry 拍板 2026-08-08 01:57)
    "WorldEventSource",
    "WorldEventInjector",
    "WorldEventSourceRegistry",
    "SourceStatus",
]
