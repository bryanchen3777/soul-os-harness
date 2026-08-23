# src/work/__init__.py
# Soul OS — Work Contract domain（DSH Multi-Agent MVP 的 durable truth 地基）
#
# 純 Python domain，零 DSH coupling：
# - 不 import 任何 DSH type / id
# - owner / assigned_agents 存 role，不存 DSH agent/session id
# - capability 名稱是 capability-neutral，不是 DSH tool 名
#
# Canonical 來源：
# - docs/DSH-WORK-CONTRACT.md（2A）§3 Work Object schema、§4 state machine、§6 Handoff
# - docs/DSH-PERSISTENCE.md（2D）§3 WorkEvent log
from .schema import (
    HandoffResult,
    HandoffStatus,
    Provenance,
    ResumeState,
    ResultType,
    WorkEvent,
    WorkEventType,
    WorkObject,
    WorkState,
)
from .state_machine import (
    InvalidTransitionError,
    can_transition,
    requires_human_approval,
    validate_transition,
)
from .store import WorkNotFoundError, WorkStore

__all__ = [
    "WorkState",
    "WorkEventType",
    "ResultType",
    "HandoffStatus",
    "Provenance",
    "ResumeState",
    "WorkObject",
    "WorkEvent",
    "HandoffResult",
    "InvalidTransitionError",
    "can_transition",
    "requires_human_approval",
    "validate_transition",
    "WorkStore",
    "WorkNotFoundError",
]
