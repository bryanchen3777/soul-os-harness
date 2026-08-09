"""
src/agency — Soul OS M5 Minimal Agency Layer

Bry 派工 2026-08-08 M5.2:
Awareness 已是 M3.4 frozen, Agency 是 M5 新增層,
在 AGENT_INTENT_PERCEIVED 跟 AGENT_SPEAK 中間,
4 個 sub-layers (Eligibility / Decision / Selection / Execution)。

M5.2 範圍:
  - Stage 1 (Eligibility):  最小 deterministic state (cooldown/dormant/busy)
  - Stage 2 (Decision):     最小 deterministic decision (act/not_act + reason)
  - Stage 3 (Selection):    最小 action mapping (no LLM, no persona)
  - Stage 4 (Execution):    STUB only (no production side effect, contract/trace only)

M5.2-H Phase 1: event scheduler trigger migration
  - EventHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type="event"
  - WRITER_ONLY: writer_executor(agent_id) 寫 diary, 不調 LLM

M5.2-H Phase 2: dream scheduler trigger migration
  - DreamHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type="dream"
  - WRITER_ONLY: dream_writer_executor(dreamer, target, all_agents) 寫 dream + relationship side effect
  - target_agent_id 透過 TriggerEnvelope.extra 傳遞 (C1: TriggerEnvelope frozen)

M5.2-H Phase 3: morning + night diary trigger migration
  - DiaryHandler 訂閱 AGENCY_TRIGGER, 過濾 trigger_type ∈ {morning, night}
  - WRITER_ONLY: diary_writer_executor(agent_id, slot) delegate 回既有 diary callback
  - 一個 Handler 同時處理兩個 slot (morning / night 都是 diary_callback_factory pattern)
  - 共用 AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler 四 handler 平行 pattern
"""
from .state import AgencyState
from .stages import (
    check_eligibility,
    make_decision,
    select_action,
    execute_action_stub,
    EligibilityResult,
    DecisionResult,
    ExecutionResult,
    AgencyTraceEntry,
)
from .trigger import TriggerEnvelope
from .agency import Agency, AgencyRunResult, run_agency
from .trigger_handler import AgencyTriggerHandler
from .event_handler import EventHandler
from .dream_handler import DreamHandler
from .diary_handler import DiaryHandler, SUPPORTED_DIARY_SLOTS

__all__ = [
    # state
    "AgencyState",
    # stage 1
    "EligibilityResult",
    "check_eligibility",
    # stage 2
    "DecisionResult",
    "make_decision",
    # stage 3
    "select_action",
    # stage 4
    "ExecutionResult",
    "execute_action_stub",
    # trace
    "AgencyTraceEntry",
    # trigger (M5.2-G)
    "TriggerEnvelope",
    # orchestrator
    "Agency",
    "AgencyRunResult",
    "run_agency",
    # handlers (M5.2-G / M5.2-H)
    "AgencyTriggerHandler",  # M5.2-G: 處理 proactive_dm
    "EventHandler",          # M5.2-H Phase 1: 處理 event
    "DreamHandler",          # M5.2-H Phase 2: 處理 dream
    "DiaryHandler",          # M5.2-H Phase 3: 處理 morning + night
    # constants
    "SUPPORTED_DIARY_SLOTS",  # M5.2-H Phase 3: ("morning", "night")
]
