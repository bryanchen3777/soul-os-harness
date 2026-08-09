"""
src/agency/stages.py — Soul OS M5.2 Agency 4 Stages

Stage 1: check_eligibility      — state check (cooldown/dormant/busy)
Stage 2: make_decision         — perception + decision cooldown check
Stage 3: select_action         — minimal deterministic mapping
Stage 4: execute_action_stub   — STUB only, no production side effect

每個 stage 都是 pure function, 接收 input 回 result + reason。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .state import AgencyState
from .trigger import TriggerEnvelope


# ─── Result dataclasses (per-stage output) ────────────────


@dataclass
class EligibilityResult:
    """Stage 1 output: boolean + reason."""
    eligible: bool
    reason: str = ""


@dataclass
class DecisionResult:
    """Stage 2 output: should_act + reason + decision_type."""
    should_act: bool
    reason: str = ""
    decision_type: Optional[str] = None  # e.g., "speak", "dm", "nudge"


@dataclass
class ExecutionResult:
    """Stage 4 output: stub execution result."""
    executed: bool
    reason: str = ""
    action_type: str = ""


@dataclass
class AgencyTraceEntry:
    """Trace record for each stage decision (observability artifact)."""
    timestamp: str
    stage: str
    input: Dict[str, Any]
    output: Dict[str, Any]
    reason: str


# ─── Stage 1: Eligibility ──────────────────────────────────


def check_eligibility(state: AgencyState, now: datetime) -> EligibilityResult:
    """
    Stage 1: Agency Eligibility Check.

    M5.1 contract: 只回答 eligible / not eligible, 不決定「想不想做」。

    Refuses if:
      - character is_dormant
      - character is_busy
      - last_action_at 距 now 仍在 action_cooldown_seconds 內
    """
    if state.is_dormant:
        return EligibilityResult(False, "character is dormant")
    if state.is_busy:
        return EligibilityResult(False, "character is busy")
    if state.last_action_at is not None:
        elapsed = (now - state.last_action_at).total_seconds()
        if elapsed < state.action_cooldown_seconds:
            return EligibilityResult(
                False,
                f"action cooldown active ({elapsed:.1f}s < {state.action_cooldown_seconds}s)"
            )
    return EligibilityResult(True, "eligible")


# ─── Stage 2: Decision ─────────────────────────────────────


def make_decision(
    eligibility: EligibilityResult,
    perception: Optional[Dict[str, Any]],
    state: AgencyState,
    now: datetime,
    trigger: Optional[TriggerEnvelope] = None,
) -> DecisionResult:
    """
    Stage 2: Agency Decision.

    M5.1 + M5.2-F contract:
      - 最小 deterministic decision
      - act / not_act + reason
      - 不能修改 perception score (input 是 dict, 不會 mutate)
      - 不能因 priority 高就直接 act (priority > 0 是必要非充分)
      - M5.2-G: 支援 trigger-only path (perception=None, trigger=TriggerEnvelope)
      - perception 跟 trigger 至少一個必須存在, 否則 reject

    Refuses if:
      - perception is None AND trigger is None (Bry: 不要 silent accept)
      - not eligibility.eligible
      - perception-only path: accepted=False / priority<=0
      - last_decision_at 距 now 仍在 decision_cooldown_seconds 內
    """
    # Input contract: 至少一個必須存在
    if perception is None and trigger is None:
        # 不要 silent accept — 明確 reject
        raise ValueError(
            "Agency make_decision: at least one of perception or trigger must be provided"
        )

    # Eligibility 沒過 → not act (任何 path 都一樣)
    if not eligibility.eligible:
        return DecisionResult(False, f"not eligible: {eligibility.reason}", None)

    # Trigger-only path (M5.2-G)
    # Scheduler 提出 trigger, 但 Agency 仍必須做 decision
    if trigger is not None and perception is None:
        # decision cooldown: 防止 decision 計算過於頻繁
        if state.last_decision_at is not None:
            elapsed = (now - state.last_decision_at).total_seconds()
            if elapsed < state.decision_cooldown_seconds:
                return DecisionResult(
                    False,
                    f"decision cooldown active ({elapsed:.1f}s < {state.decision_cooldown_seconds}s)",
                    None
                )
        # trigger 是 sufficient signal 讓 Agency 考慮 act
        # 但仍必須跑 Stage 1 eligibility (上面已 check) 才能 YES
        return DecisionResult(
            True,
            f"trigger-only path met (trigger_type={trigger.trigger_type})",
            "speak"
        )

    # Perception-only path (M5.2-A 既有)
    # Composite (both) 也走這條 (M5.x+ future)
    if perception is not None:
        # I-A5: rejected perception 不得 act
        if not perception.get("accepted", False):
            return DecisionResult(False, "perception rejected", None)
        # I-A2: priority > 0 是必要條件, 但 priority 高 ≠ 自動 act
        priority = perception.get("priority", 0)
        if priority <= 0:
            return DecisionResult(False, "no priority signal (priority <= 0)", None)
        # decision cooldown
        if state.last_decision_at is not None:
            elapsed = (now - state.last_decision_at).total_seconds()
            if elapsed < state.decision_cooldown_seconds:
                return DecisionResult(
                    False,
                    f"decision cooldown active ({elapsed:.1f}s < {state.decision_cooldown_seconds}s)",
                    None
                )
        # 全部條件通過
        return DecisionResult(True, "all conditions met", "speak")

    # 到這裡理論上不會到, 因為前面已 reject None+None
    return DecisionResult(False, "no valid input path", None)


# ─── Stage 3: Action Selection ────────────────────────────


def select_action(decision_type: str) -> str:
    """
    Stage 3: Action Selection.

    M5.1 contract: 最小 action mapping, 不做 persona complexity, 不接 LLM。
    M5.2 範圍: 1:1 mapping (decision_type → action_type)。
    """
    mapping = {
        "speak": "speak",
        "dm": "dm",
        "nudge": "nudge",
    }
    return mapping.get(decision_type, "speak")  # default fallback


# ─── Stage 4: Action Execution (STUB) ──────────────────────


def execute_action_stub(action_type: str) -> ExecutionResult:
    """
    Stage 4: Action Execution (STUB ONLY).

    M5.1 contract: STUB only, 不真正觸發 production side effect。
    M5.2 範圍: 只驗證 execution contract / trace。
    """
    return ExecutionResult(
        executed=True,
        reason=f"STUB: would publish AGENT_SPEAK for action_type={action_type}",
        action_type=action_type,
    )
