"""
src/goals — Soul OS Goal Engine 包（C-1 自主目标规划）

TG-2 (2026-09-05): 自主目标引擎实现。
设计来源: docs/TG-1-GOAL-ENGINE-CONTRACT.md（10 项架构决策锁定）。

模块:
  - models: Goal dataclass + 状态机（ACTIVE/IN_PROGRESS/SUSPENDED + COMPLETED/ABANDONED）
  - motive_provider: GoalMotiveProvider（方案 B — 产候选/引用/不决策/状态同步）

治理:
  - frozen contract 0 破坏（纯 Additive）: Agency 4 stages / TriggerEnvelope /
    InnerLifeEvent 9 字段 / 4 handlers / SAGE 写入逻辑 / Motive 5 字段 均不触碰
  - No Scoring 哲学: 双轴平衡靠结构配额（24h/1、N=3、streak=2），无数值权重
"""
from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ABANDONED,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    Goal,
    InvalidGoalTransitionError,
)
from src.goals.motive_provider import GoalMotiveProvider, reset_goal_providers

__all__ = [
    "AXIS_BRYAN",
    "AXIS_SELF",
    "GOAL_STATE_ABANDONED",
    "GOAL_STATE_ACTIVE",
    "GOAL_STATE_COMPLETED",
    "GOAL_STATE_IN_PROGRESS",
    "GOAL_STATE_SUSPENDED",
    "Goal",
    "GoalMotiveProvider",
    "InvalidGoalTransitionError",
    "reset_goal_providers",
]