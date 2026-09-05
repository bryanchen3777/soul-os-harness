"""
src/goals/models.py — Goal Engine 数据模型（TG-2, C-1 自主目标规划）

设计来源: docs/TG-1-GOAL-ENGINE-CONTRACT.md（§2 存储 / §3 状态机）

本模块只含纯数据模型 + 状态机校验, 不依赖任何既有 frozen contract 模块
（Agency / TriggerEnvelope / InnerLifeEvent / handlers / SAGE 写入逻辑 0 触点）。

锁定内容:
  - goals 表列 与 Goal dataclass 字段一一对应（TG-1 §2.2 DDL v1）
  - 状态机: ACTIVE / IN_PROGRESS / SUSPENDED + COMPLETED / ABANDONED 终态
  - SUSPENDED 无损暂停（TG-1 §3.4: state + suspend_snapshot + state_updated_at 三字段）
  - 终态无出边（对齐 Work state_machine.py 终态空表哲学）
  - 非法转移一律拒绝（对齐 Work validate_transition 防御风格; InvalidGoalTransitionError）
  - 明确排除: DORMANT 不入 v1（TG-1 §3.2）
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional

# ───────────────────────────────────────────────────────────
# 状态常量（TG-1 §3.2 v1 终态集）
# ───────────────────────────────────────────────────────────

GOAL_STATE_ACTIVE = "ACTIVE"            # 可被选为 Motive 候选
GOAL_STATE_IN_PROGRESS = "IN_PROGRESS"  # 曾被 Decision 选中推进过 ≥1 次
GOAL_STATE_SUSPENDED = "SUSPENDED"      # 外部突发/静默/高冲突 → 无损暂停（non-terminal）
GOAL_STATE_COMPLETED = "COMPLETED"      # 完成判定满足（终态）
GOAL_STATE_ABANDONED = "ABANDONED"      # 决定不追（终态, 保留 record, 软删哲学 MR-2 继承）

GOAL_STATES = frozenset({
    GOAL_STATE_ACTIVE,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_ABANDONED,
})
GOAL_TERMINAL_STATES = frozenset({GOAL_STATE_COMPLETED, GOAL_STATE_ABANDONED})
GOAL_NON_TERMINAL_STATES = GOAL_STATES - GOAL_TERMINAL_STATES

# ───────────────────────────────────────────────────────────
# 双轴（TG-1 §7: Bryan 轴 4 源 + 自我轴 4 源）
# ───────────────────────────────────────────────────────────

AXIS_BRYAN = "bryan"
AXIS_SELF = "self"
GOAL_AXES = frozenset({AXIS_BRYAN, AXIS_SELF})

# completion_criteria.kind 枚举（TG-1 §9.3; v1 只定义结构与枚举, 不做数值打分）
CRITERIA_KIND_INTERACTION = "interaction"
CRITERIA_KIND_OBSERVATION = "observation"
CRITERIA_KIND_REFLECTION = "reflection"
CRITERIA_KIND_MIXED = "mixed"
CRITERIA_KINDS = frozenset({
    CRITERIA_KIND_INTERACTION,
    CRITERIA_KIND_OBSERVATION,
    CRITERIA_KIND_REFLECTION,
    CRITERIA_KIND_MIXED,
})

# ───────────────────────────────────────────────────────────
# 转移表（TG-1 §3.3 Transition Table, v1 锁定）
# ───────────────────────────────────────────────────────────

_GOAL_TRANSITIONS: Dict[str, frozenset] = {
    GOAL_STATE_ACTIVE: frozenset({
        GOAL_STATE_IN_PROGRESS,   # 被 decide_motive 选中（transmit/observe/reflect 任一）
        GOAL_STATE_SUSPENDED,     # 命中 §7 中断信号集
    }),
    GOAL_STATE_IN_PROGRESS: frozenset({
        GOAL_STATE_SUSPENDED,     # 命中中断信号集
        GOAL_STATE_COMPLETED,     # completion_criteria 结构化条件全满足
        GOAL_STATE_ABANDONED,     # 永久性失效判据（v1: timeout_days 超时兜底）
    }),
    GOAL_STATE_SUSPENDED: frozenset({
        GOAL_STATE_ACTIVE,        # 唤醒条件满足（resume 回 ACTIVE, 唯一 resume target）
    }),
    GOAL_STATE_COMPLETED: frozenset(),   # 终态无出边
    GOAL_STATE_ABANDONED: frozenset(),   # 终态无出边
}


class InvalidGoalTransitionError(Exception):
    """非法 goal 状态转移（对齐 Work InvalidTransitionError 防御风格）。"""

    def __init__(self, from_state: str, to_state: str) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"非法 goal 状态转移: {from_state} → {to_state} "
            f"(合法目标: {sorted(_GOAL_TRANSITIONS.get(from_state, frozenset()))})"
        )


def validate_goal_transition(from_state: str, to_state: str) -> None:
    """转移校验: 非法转移抛 InvalidGoalTransitionError（终态无出边）。"""
    allowed = _GOAL_TRANSITIONS.get(from_state)
    if allowed is None:
        raise InvalidGoalTransitionError(from_state, to_state)
    if to_state not in allowed:
        raise InvalidGoalTransitionError(from_state, to_state)


def is_terminal_state(state: str) -> bool:
    return state in GOAL_TERMINAL_STATES


# ───────────────────────────────────────────────────────────
# Goal dataclass（与 graph_store.goals 表 DDL 一一对应, TG-1 §2.2）
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Goal:
    """
    自主目标记录（per-agent, 存于 agent 自有 graph.sqlite 的 goals 表）。

    字段 = TG-1 §2.2 DDL v1（goal_id / agent_id / axis / title / description /
    seed_source_ref / state / state_updated_at / created_at / last_advanced_at /
    advance_count / suspend_snapshot / completion_criteria / superseded_by）。
    """
    goal_id: str
    agent_id: str
    axis: str
    title: str
    description: str
    seed_source_ref: str
    state: str = GOAL_STATE_ACTIVE
    state_updated_at: float = 0.0
    created_at: float = 0.0
    last_advanced_at: Optional[float] = None
    advance_count: int = 0
    suspend_snapshot: Optional[str] = None
    completion_criteria: Optional[str] = None
    superseded_by: Optional[str] = None

    def __post_init__(self) -> None:
        if self.axis not in GOAL_AXES:
            raise ValueError(f"axis 非法: {self.axis!r} (合法: {sorted(GOAL_AXES)})")
        if self.state not in GOAL_STATES:
            raise ValueError(f"state 非法: {self.state!r} (合法: {sorted(GOAL_STATES)})")
        if self.advance_count < 0:
            raise ValueError(f"advance_count 不能为负: {self.advance_count}")

    # ── criteria 工具 ──────────────────────────────────────

    def completion_criteria_dict(self) -> Optional[Dict[str, Any]]:
        """解析 completion_criteria JSON（坏 JSON → None, 不 crash）。"""
        if not self.completion_criteria:
            return None
        try:
            data = json.loads(self.completion_criteria)
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    def to_row(self) -> tuple:
        """转 sqlite 行参数（列序与 upsert_goal SQL 一致）。"""
        return (
            self.goal_id,
            self.agent_id,
            self.axis,
            self.title,
            self.description,
            self.seed_source_ref,
            self.state,
            self.state_updated_at,
            self.created_at,
            self.last_advanced_at,
            self.advance_count,
            self.suspend_snapshot,
            self.completion_criteria,
            self.superseded_by,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "agent_id": self.agent_id,
            "axis": self.axis,
            "title": self.title,
            "description": self.description,
            "seed_source_ref": self.seed_source_ref,
            "state": self.state,
            "state_updated_at": self.state_updated_at,
            "created_at": self.created_at,
            "last_advanced_at": self.last_advanced_at,
            "advance_count": self.advance_count,
            "suspend_snapshot": self.suspend_snapshot,
            "completion_criteria": self.completion_criteria,
            "superseded_by": self.superseded_by,
        }

    def advanced(self, now_ts: float) -> "Goal":
        """推进一次（G2: advance_count ≤+1/心跳, 仅当 Decision 选中）:
        ACTIVE → IN_PROGRESS; IN_PROGRESS 保持; 终态不变（调用方已挡）。"""
        new_state = (
            GOAL_STATE_IN_PROGRESS
            if self.state == GOAL_STATE_ACTIVE
            else self.state
        )
        return replace(
            self,
            state=new_state,
            state_updated_at=now_ts,
            last_advanced_at=now_ts,
            advance_count=self.advance_count + 1,
        )

    def with_state(self, new_state: str, now_ts: float) -> "Goal":
        """状态转移（含 state_updated_at; 保留其余字段, 无损语义 TG-1 §3.4）。"""
        return replace(self, state=new_state, state_updated_at=now_ts)


# ───────────────────────────────────────────────────────────
# GoalProviderState — 结构配额轮替记忆（sidecar JSON）
# ───────────────────────────────────────────────────────────

@dataclass
class GoalProviderState:
    """
    GoalMotiveProvider 的 per-agent 配额/轮替状态（data_root()/memory/{agent}/goal_provider.json）。

    纯结构记录（No Scoring 哲学: 无任何数值权重/打分字段）:
      - last_candidate_at: 24h 配额窗（上次成功产候选时刻, epoch）
      - rotation: 最近 N 次已产候选的轴分布（N = GOAL_ROTATION_WINDOW）
      - consecutive_do_nothing: per-goal 连续 do_nothing 计数（§8.1 信号 6）
      - consecutive_skips: 因强制换轴失败连续放弃次数（防饿死兜底）
      - last_seed_scan_at: LS-2 种子扫描节流（上次实际扫描时刻, epoch; 24h 窗复用
        GOAL_QUOTA_WINDOW_SECONDS, 同构第二配额计数——候选配额 vs 创建配额）
      - seed_source_cursor: LS-2 8 源确定性轮序游标（0-7, 记录上次扫到哪）
      - seed_axis_streak: LS-2 生成轴同轴连续计数（≤2 强制换轴）
      - last_seed_axis: LS-2 上次生成的轴（"bryan"|"self"）
      - seed_empty_rounds: LS-2 生成轴连续无命中轮数（防饿死兜底, 对齐
        consecutive_skips 同精神）
    """
    last_candidate_at: float = 0.0
    rotation: List[Dict[str, Any]] = field(default_factory=list)
    consecutive_do_nothing: Dict[str, int] = field(default_factory=dict)
    consecutive_skips: int = 0
    # ── LS-2 种子生成器状态（additive, 旧文件缺省兼容 0 迁移成本）──
    last_seed_scan_at: float = 0.0
    seed_source_cursor: int = 0
    seed_axis_streak: int = 0
    last_seed_axis: Optional[str] = None
    seed_empty_rounds: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "last_candidate_at": self.last_candidate_at,
            "rotation": self.rotation,
            "consecutive_do_nothing": self.consecutive_do_nothing,
            "consecutive_skips": self.consecutive_skips,
            "last_seed_scan_at": self.last_seed_scan_at,
            "seed_source_cursor": self.seed_source_cursor,
            "seed_axis_streak": self.seed_axis_streak,
            "last_seed_axis": self.last_seed_axis,
            "seed_empty_rounds": self.seed_empty_rounds,
        }

    @classmethod
    def from_dict(cls, d: Any) -> "GoalProviderState":
        if not isinstance(d, dict):
            return cls()
        last_axis = d.get("last_seed_axis")
        return cls(
            last_candidate_at=float(d.get("last_candidate_at", 0.0) or 0.0),
            rotation=[
                r for r in d.get("rotation", [])
                if isinstance(r, dict) and isinstance(r.get("axis"), str)
            ][-GOAL_ROTATION_WINDOW_DEFAULT:],
            consecutive_do_nothing={
                str(k): int(v) for k, v in (d.get("consecutive_do_nothing") or {}).items()
                if isinstance(v, (int, float)) and v > 0
            },
            consecutive_skips=int(d.get("consecutive_skips", 0) or 0),
            last_seed_scan_at=float(d.get("last_seed_scan_at", 0.0) or 0.0),
            seed_source_cursor=int(d.get("seed_source_cursor", 0) or 0),
            seed_axis_streak=int(d.get("seed_axis_streak", 0) or 0),
            last_seed_axis=(
                last_axis if isinstance(last_axis, str) and last_axis in (
                    "bryan", "self"
                ) else None
            ),
            seed_empty_rounds=int(d.get("seed_empty_rounds", 0) or 0),
        )


# 供 from_dict 裁剪轮替窗口用的默认值（真实值在 motive_provider.py 定义）
# —— 避免 models ↔ provider 循环 import, 此处用字面量保持默认一致。
GOAL_ROTATION_WINDOW_DEFAULT = 3