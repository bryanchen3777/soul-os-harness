"""
src/work/state_machine.py
Work State Machine（2A §4）— transition 驗證。

唯一 authoritative source：docs/DSH-WORK-CONTRACT.md §4。

規則：
- 只有兩個 transition 需要 Human approval：
    - proposed → approved（開工前）
    - awaiting_approval → done（commit/push/deploy 前）
- 其餘（assigned、in_progress、awaiting_review、blocked → resume）都是 autonomous。
- blocked 是 non-terminal：任何 active state 都可能進入 blocked，
  解除阻塞後 resume 回 resume_state.current_phase 指定的 target state。
- 終態：rejected / cancelled / done。
- 非法 transition 拋 InvalidTransitionError。

本模組只管「Work 的生命週期走到哪」，不管 capability authorization（2A §1 正交）。
"""
from __future__ import annotations

from .schema import WorkState

# active（non-terminal、non-blocked）states：可進入 blocked，也是 blocked 的 resume target
ACTIVE_STATES = frozenset({
    WorkState.PROPOSED,
    WorkState.APPROVED,
    WorkState.ASSIGNED,
    WorkState.IN_PROGRESS,
    WorkState.AWAITING_REVIEW,
    WorkState.AWAITING_APPROVAL,
})

# 終態（2A §4）
TERMINAL_STATES = frozenset({
    WorkState.DONE,
    WorkState.REJECTED,
    WorkState.CANCELLED,
})

# 合法 transition 表（2A §4）。blocked 的 resume 走 ACTIVE_STATES，不在此表。
TRANSITIONS: dict[WorkState, frozenset[WorkState]] = {
    WorkState.PROPOSED: frozenset({
        WorkState.APPROVED,      # Human approval #1
        WorkState.REJECTED,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.APPROVED: frozenset({
        WorkState.ASSIGNED,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.ASSIGNED: frozenset({
        WorkState.IN_PROGRESS,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.IN_PROGRESS: frozenset({
        WorkState.AWAITING_REVIEW,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.AWAITING_REVIEW: frozenset({
        WorkState.AWAITING_APPROVAL,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.AWAITING_APPROVAL: frozenset({
        WorkState.DONE,          # Human approval #2
        WorkState.REJECTED,
        WorkState.CANCELLED,
        WorkState.BLOCKED,
    }),
    WorkState.BLOCKED: frozenset(),   # resume 由 can_transition 特判
    WorkState.DONE: frozenset(),      # 終態
    WorkState.REJECTED: frozenset(),  # 終態
    WorkState.CANCELLED: frozenset(),  # 終態
}

# 需要 Human approval 的 transition（2A §4）
HUMAN_APPROVAL_TRANSITIONS = frozenset({
    (WorkState.PROPOSED, WorkState.APPROVED),
    (WorkState.AWAITING_APPROVAL, WorkState.DONE),
})


class InvalidTransitionError(ValueError):
    """非法 WorkState transition（未列於 2A §4）。"""


def can_transition(from_state: WorkState, to_state: WorkState) -> bool:
    """回傳 from_state → to_state 是否為合法 transition（2A §4）。"""
    if from_state == WorkState.BLOCKED:
        # blocked 是 non-terminal：resume 回任何 active state
        return to_state in ACTIVE_STATES
    return to_state in TRANSITIONS.get(from_state, frozenset())


def requires_human_approval(from_state: WorkState, to_state: WorkState) -> bool:
    """回傳此 transition 是否需要 Human approval（2A §4）。"""
    return (from_state, to_state) in HUMAN_APPROVAL_TRANSITIONS


def validate_transition(from_state: WorkState, to_state: WorkState) -> None:
    """驗證 transition；非法則拋 InvalidTransitionError。"""
    if not can_transition(from_state, to_state):
        raise InvalidTransitionError(
            f"illegal WorkState transition: {from_state.value} -> {to_state.value}"
        )
