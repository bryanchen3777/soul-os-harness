"""
src/agency/agency.py — Soul OS M5.2 Agency Orchestrator

4 stages chained: Eligibility → Decision → Selection → Execution
每個 stage 都記錄 trace entry。
Pure function: input (state, perception, now) → output (AgencyRunResult)
No side effects, no bus integration, no scheduler.
Stage 4 is STUB only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

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


@dataclass
class AgencyRunResult:
    """Orchestrator output: 4 stage results + trace."""
    eligibility: EligibilityResult
    decision: DecisionResult
    action_type: Optional[str] = None
    execution: Optional[ExecutionResult] = None
    trace: List[AgencyTraceEntry] = field(default_factory=list)
    trigger: Optional[TriggerEnvelope] = None  # M5.2-G: 新增, 若 trigger-only path 會記


class Agency:
    """
    M5.2 + M5.2-G Agency orchestrator.

    Pure function: input (state, perception, trigger, now) → output (AgencyRunResult).
    No side effects, no bus integration, no scheduler.
    Stage 4 is STUB only.

    M5.2-G input contract:
      - perception: Optional[Dict] — 來自 perception layer (M3.4)
      - trigger: Optional[TriggerEnvelope] — 來自 scheduler (M5.2-F)
      - 至少一個必須存在, 否則 ValueError
      - 兩者都給 → composite path (M5.x+ future, 暫走 perception)

    State updates ONLY happen on actual execution (Stage 4):
      - last_action_at 設為 now (執行後)
      - last_decision_at 設為 now (每次 decision 計算後, 不論 yes/no)
    """
    def __init__(self, state: Optional[AgencyState] = None):
        self.state = state or AgencyState()
        self._trace: List[AgencyTraceEntry] = []

    def get_trace(self) -> List[AgencyTraceEntry]:
        return list(self._trace)

    def clear_trace(self) -> None:
        self._trace.clear()

    def run(
        self,
        perception: Optional[Dict[str, Any]],
        now: datetime,
        trigger: Optional[TriggerEnvelope] = None,
    ) -> AgencyRunResult:
        """
        Run 4 stages in order, recording trace.
        State updates only on actual execution.

        M5.2-G: 支援 trigger-only path. trigger 會被記到 AgencyRunResult.trigger。
        """
        self._trace = []
        ts = now.isoformat()

        # Stage 1: Eligibility
        eligibility = check_eligibility(self.state, now)
        self._trace.append(AgencyTraceEntry(
            timestamp=ts, stage="eligibility",
            input={"state_summary": self._state_summary()},
            output={"eligible": eligibility.eligible, "reason": eligibility.reason},
            reason=eligibility.reason,
        ))

        # Stage 2: Decision
        decision = make_decision(eligibility, perception, self.state, now, trigger=trigger)
        self._trace.append(AgencyTraceEntry(
            timestamp=ts, stage="decision",
            input={
                "perception_accepted": perception.get("accepted") if perception else None,
                "perception_priority": perception.get("priority") if perception else None,
                "trigger_type": trigger.trigger_type if trigger else None,
            },
            output={
                "should_act": decision.should_act,
                "reason": decision.reason,
                "decision_type": decision.decision_type,
            },
            reason=decision.reason,
        ))

        # Update last_decision_at (每次 decision 都記, 防止 decision spam)
        self.state.last_decision_at = now

        action_type: Optional[str] = None
        execution: Optional[ExecutionResult] = None

        if decision.should_act:
            # Stage 3: Selection
            action_type = select_action(decision.decision_type or "")
            self._trace.append(AgencyTraceEntry(
                timestamp=ts, stage="selection",
                input={"decision_type": decision.decision_type},
                output={"action_type": action_type},
                reason=f"selected {action_type} for decision_type={decision.decision_type}",
            ))

            # Stage 4: Execution (STUB)
            execution = execute_action_stub(action_type)
            self._trace.append(AgencyTraceEntry(
                timestamp=ts, stage="execution",
                input={"action_type": action_type},
                output={"executed": execution.executed, "reason": execution.reason},
                reason=execution.reason,
            ))

            # State 更新: last_action_at only on actual execution
            self.state.last_action_at = now

        return AgencyRunResult(
            eligibility=eligibility,
            decision=decision,
            action_type=action_type,
            execution=execution,
            trace=self._trace,
            trigger=trigger,
        )

    def _state_summary(self) -> Dict[str, Any]:
        return {
            "is_dormant": self.state.is_dormant,
            "is_busy": self.state.is_busy,
            "action_cooldown_seconds": self.state.action_cooldown_seconds,
            "decision_cooldown_seconds": self.state.decision_cooldown_seconds,
        }


def run_agency(
    state: AgencyState,
    perception: Optional[Dict[str, Any]],
    now: datetime,
    trigger: Optional[TriggerEnvelope] = None,
) -> AgencyRunResult:
    """
    Functional entry point: 等價於 Agency(state).run(perception, now, trigger=trigger)。
    """
    return Agency(state).run(perception, now, trigger=trigger)
