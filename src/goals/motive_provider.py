"""
src/goals/motive_provider.py — GoalMotiveProvider（TG-2, C-1 自主目标规划）

设计来源: docs/TG-1-GOAL-ENGINE-CONTRACT.md（§4 方案 B / §5 轮替配额 / §7 中断 /
§8 唤醒 / §9 沉淀）

模块边界（方案 B, Bryan 拍板, 0 侵入核心 MotiveEngine）:
  - 独立新模块, 不调 MotiveEngine 内部方法、不改 src/soul/motive.py 任何 frozen 内容
  - 只用 Motive dataclass（5 字段冻结）与 MotiveTraceStore.append_motive（汇入 pending 池,
    与普通 motive 同池竞争, resolve_pending 单条选取语义 0 变更）
  - 只做 4 件事（TG-1 §4.3）: 产候选 / 引用（provenance_ref = goal:{goal_id}）/ 不决策 /
    状态同步（观察 Decision 结果 + 中断信号 → transition_goal）

No Scoring 哲学（TG-1 宪法 1）:
  - 全模块 0 数值权重 / 打分字段: 双轴平衡靠结构配额（24h/1、N=3、streak=2）,
    候选仲裁靠既有 Decision 自然选择。轮替记忆只记 {axis, goal_id, ts} 结构。

心跳接线（TG-1 §10）:
  - _decision_check 内扩: interpret_new_events → apply_interrupt_signals /
    scheduled_wakeup_scan → assemble_candidate（≤1）→ resolve_pending → decide →
    on_decision（状态同步）。0 新定时器 0 新 tick。
  - 主循环 _goal_scan_all: 每 30s wake 顺带跑中断/唤醒扫描（§8.3 schedule scan）。

不变量: G1（≤1 候选/心跳）G2（advance_count ≤+1 且仅当选中）G3（0 执行权）
G4（0 自主递归）N2（SUSPENDED 无损: 只写三字段, 0 删除 0 计数重置）
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    CRITERIA_KINDS,
    GOAL_AXES,
    GOAL_STATE_ACTIVE,
    GOAL_STATE_ABANDONED,
    GOAL_STATE_COMPLETED,
    GOAL_STATE_IN_PROGRESS,
    GOAL_STATE_SUSPENDED,
    GOAL_TERMINAL_STATES,
    Goal,
    GoalProviderState,
    is_terminal_state,
)
# 复用既有信号源常量（§8.1 信号 5: Bry last-seen 超时阈值, 单一事实来源）
from src.io.channels.bryan_state import PROACTIVE_DM_BRYAN_INACTIVE_HOURS

logger = logging.getLogger("soul_os.goals.motive_provider")

# ───────────────────────────────────────────────────────────
# 配额 / 轮替参数（TG-1 §5.2, v1 锁定, 均可调参）
# ───────────────────────────────────────────────────────────

GOAL_QUOTA_WINDOW_SECONDS = 24 * 3600    # 配额时间窗 24h
GOAL_QUOTA_PER_WINDOW = 1                # 每窗至多 1 个 Goal Motive 候选
GOAL_ROTATION_WINDOW = 3                 # 轮替记忆窗口 N=3（最近 3 次已产候选的轴分布）
GOAL_MAX_ROTATION_STREAK = 2             # 同轴连续产候选上限（超过则强制换轴）
GOAL_SKIP_ESCAPE_THRESHOLD = 2           # 防饿死: 连续放弃 2 次后允许原轴再产一次（§5.3）
GOAL_SUSPEND_NOT_TRANSMIT_STREAK = 3     # §8.1 信号 6: 连续 not_transmit ≥3 → SUSPENDED
GOAL_WAKE_FORCE_SECONDS = 7 * 86400      # §8.3 条件 4: 强制最长暂停 7 天
QUIET_HOURS_START = 23                   # 静默时段（与 scheduler._is_quiet_hours 同语义）
QUIET_HOURS_END = 8

# provenance 引用命名空间（TG-1 §4.3: provenance_ref = "goal:{goal_id}"）
GOAL_PROVENANCE_PREFIX = "goal:"


# ───────────────────────────────────────────────────────────
# 工具函数（复用既有信号源, 0 新信号通道）
# ───────────────────────────────────────────────────────────

def _is_quiet_hours(now: datetime) -> bool:
    """静默时段判定（§8.1 信号 3; 与 scheduler.py:_is_quiet_hours 同语义 23:00-08:00,
    参照本地时间 — scheduler 用 now_local(), 本模块默认 now 亦取本地 aware 时间）。"""
    h = now.hour
    if QUIET_HOURS_START > QUIET_HOURS_END:
        return h >= QUIET_HOURS_START or h < QUIET_HOURS_END
    return QUIET_HOURS_START <= h < QUIET_HOURS_END


def _local_now() -> datetime:
    """默认时钟: 本地 aware 时间（与 scheduler now_local() 同语义, quiet/跨日对齐）。"""
    return datetime.now().astimezone()


def _bryan_last_seen_dt() -> Optional[datetime]:
    """读 Bry 最后可见时间（统一信号源 bryan_last_seen.json; 失败/无记录 → None）。"""
    try:
        from src.io.channels.bryan_state import read_bryan_last_seen
        last = read_bryan_last_seen()
        if last is None:
            return None
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return last
    except Exception:
        return None


def _find_goal(goals: List[Goal], goal_id: str) -> Optional[Goal]:
    for g in goals:
        if g.goal_id == goal_id:
            return g
    return None


def _goal_db_path(agent_id: str) -> Path:
    """per-agent graph.sqlite（与 decision.py:438 同路径模式）。"""
    return data_root_path() / "memory" / agent_id / "graph.sqlite"


def _goal_state_path(agent_id: str) -> Path:
    """GoalMotiveProvider sidecar state（配额/轮替记忆, 与 graph.sqlite 同目录）。"""
    return data_root_path() / "memory" / agent_id / "goal_provider.json"


def data_root_path() -> Path:
    from src.paths import data_root
    return data_root()


# ───────────────────────────────────────────────────────────
# GoalMotiveProvider（方案 B, §4.3 职责边界）
# ───────────────────────────────────────────────────────────

class GoalMotiveProvider:
    """
    Goal 引擎的 Motive 候选供应商。

    只做 4 件事（TG-1 §4.3）:
      1. 产候选: 从 ledger 读 ACTIVE goals, 受配额约束每心跳至多 1 个, 构造 Motive 候选
      2. 引用: 候选以 Motive 5 字段形态存在, provenance_ref = "goal:{goal_id}"
      3. 不决策: 候选汇入 pending 池, 一切选择权归既有 decide_motive
      4. 状态同步: 观察 Decision 结果与中断信号, 执行 transition_goal

    禁止项（§4.3 边界写死）:
      - ❌ 不调 MotiveEngine 内部方法 / 不改 motive.py frozen 内容
      - ❌ 不直连 tool_registry / 不 publish / 不调 handler（G3）
      - ❌ 不新增执行通道 — transmit/observe/reflect 全部复用既有接线
    """

    def __init__(
        self,
        agent_id: str,
        store: Optional[Any] = None,
        trace_store: Optional[Any] = None,
    ) -> None:
        self.agent_id = agent_id
        self._store: Optional[Any] = store      # GraphStore（None → lazy per-agent 打开）
        self._trace_store: Any = trace_store
        if self._trace_store is None:
            from src.soul.motive import MotiveTraceStore
            self._trace_store = MotiveTraceStore()
        self._lock = threading.RLock()
        self._state_cache: Optional[GoalProviderState] = None

    # ── 缓存 / 生命周期 ─────────────────────────────────────

    @classmethod
    def for_agent(cls, agent_id: str) -> "GoalMotiveProvider":
        """进程级 per-agent 单例（scheduler singleton 同模式）。测试用 reset_goal_providers()。"""
        with _providers_lock:
            p = _providers.get(agent_id)
            if p is None:
                p = cls(agent_id=agent_id)
                _providers[agent_id] = p
            return p

    def close(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None

    # ── ledger 访问 ─────────────────────────────────────────

    def _store_for(self) -> Any:
        """lazy GraphStore（per-agent graph.sqlite; 复用 WAL / RLock / 事务）。"""
        if self._store is None:
            from src.memory.sage.graph_store import GraphStore
            self._store = GraphStore(db_path=_goal_db_path(self.agent_id))
        return self._store

    # ── 配额状态 sidecar（纯结构记录, No Scoring）────────────

    def _load_state(self) -> GoalProviderState:
        with self._lock:
            if self._state_cache is not None:
                return self._state_cache
            path = _goal_state_path(self.agent_id)
            try:
                if path.is_file():
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self._state_cache = GoalProviderState.from_dict(data)
                else:
                    self._state_cache = GoalProviderState()
            except Exception as e:
                logger.warning(
                    f"[Goal] 读 provider state 失败 (fail-closed = 空态): "
                    f"{type(e).__name__}: {e}"
                )
                self._state_cache = GoalProviderState()
            return self._state_cache

    def _save_state(self, state: GoalProviderState) -> None:
        with self._lock:
            path = _goal_state_path(self.agent_id)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(
                    json.dumps(state.to_dict(), ensure_ascii=False),
                    encoding="utf-8",
                )
                tmp.replace(path)
                self._state_cache = state
            except OSError as e:
                logger.warning(f"[Goal] 写 provider state 失败: {e}")

    # ── 1. 产候选（≤1/心跳; 24h 配额 + N=3 轮替 + streak=2, 纯结构规则）────────

    def assemble_candidate(self, now: Optional[datetime] = None) -> Optional[Any]:
        """
        从 ledger 装配至多 1 个 Goal Motive 候选, 汇入 pending 池。

        轮替算法（TG-1 §5.3, 0 权重）:
          1. 24h 配额窗检查（每窗至多 1 候选）
          2. 候选池 = 非终态且非 SUSPENDED goals（ACTIVE + IN_PROGRESS, §3.2 状态机闭环;
             排除上一次已产候选的 goal → 防单目标霸占; 空池回退全量 → 防饿死）
          3. 轴选择: 同轴连续 ≥2 → 强制另一轴（保底: 另一轴无候选 → 放弃本心跳;
             连续放弃 2 次 → 防饿死兜底允许原轴）; 否则优先「窗口内最后出现更早/未出现」的轴
          4. 轴内: last_advanced_at 最旧者优先（纯时间轮候, 非打分）
          5. 产 1 候选 → append_motive 汇入 pending 池 → 写轮替记忆 + 配额时间戳

        Returns:
            Motive（provenance_ref = "goal:{goal_id}"）或 None（配额挡 / 无候选 / 轮替放弃）
        """
        now = now or _local_now()
        st = now.timestamp()
        with self._lock:
            state = self._load_state()

            # 1) 配额窗（TG-1 §5.2: 24h 窗 / 每窗 1）
            if st - state.last_candidate_at < GOAL_QUOTA_WINDOW_SECONDS:
                logger.debug(
                    f"[Goal] 配额窗内跳过装配 agent={self.agent_id} "
                    f"(last={state.last_candidate_at:.0f}, still {GOAL_QUOTA_PER_WINDOW}/24h)"
                )
                return None

            store = self._store_for()
            # 可装配池 = ACTIVE + IN_PROGRESS（§3.2 状态机闭环: IN_PROGRESS 需
            # 继续被选中推进直到完成判定; SUSPENDED 暂停中不装配; 终态无出边不装配）
            active_pool = [
                g for g in store.get_goals(self.agent_id)
                if g.state in (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS)
            ]
            if not active_pool:
                return None

            # 2) 排除上一次已产候选的 goal（§5.3 防单目标霸占; 空池回退 → 防饿死）
            last_goal_id = state.rotation[-1]["goal_id"] if state.rotation else None
            pool = [g for g in active_pool if g.goal_id != last_goal_id]
            if not pool:
                pool = active_pool

            # 3) 轴选择（轮替 + 强制换轴）
            axis = self._pick_axis(pool, state, st)
            if axis is None:
                return None  # 强制换轴失败, 放弃本心跳

            # 4) 轴内: last_advanced_at 最旧优先（纯时间轮候）
            axis_goals = [g for g in pool if g.axis == axis]
            axis_goals.sort(
                key=lambda g: (g.last_advanced_at if g.last_advanced_at is not None else 0.0)
            )
            goal = axis_goals[0]

            # 5) 产候选（Motive 5 字段冻结; provenance_ref 命名空间 goal:{id}）
            from src.soul.motive import TARGET_BRYAN, Motive, new_motive_id
            motive = Motive(
                motive_id=new_motive_id(),
                content=goal.title,
                target=TARGET_BRYAN,
                provenance_ref=f"{GOAL_PROVENANCE_PREFIX}{goal.goal_id}",
                created_at=now.isoformat(),
            )
            # 汇入 pending 池（与普通 motive 同池; resolve_pending 取最新语义 0 变更）
            self._trace_store.append_motive(motive, self.agent_id)

            # 6) 写轮替记忆 + 配额时间戳
            state.rotation.append({"axis": axis, "goal_id": goal.goal_id, "ts": st})
            del state.rotation[:-GOAL_ROTATION_WINDOW]
            state.last_candidate_at = st
            state.consecutive_skips = 0
            self._save_state(state)

            logger.info(
                f"[Goal] 候选装配: goal={goal.goal_id} axis={axis} agent={self.agent_id} "
                f"motive={motive.motive_id}"
            )
            return motive

    def _pick_axis(
        self,
        pool: List[Goal],
        state: GoalProviderState,
        now_ts: float,
    ) -> Optional[str]:
        """轴选择（§5.3 步骤 2）: 返回选中轴或 None（放弃本心跳）。

        - 同轴连续 ≥2 次 → 强制另一轴（Bryan 保底: 另一轴无候选 → 放弃本心跳）
        - 防饿死兜底（§5.3「每 24h 至少有机会出现 1 次」）: 连续 2 次因强制轴
          无候选而放弃后, 允许从原轴再产一次（避免单轴系统永久饥饿）
        - 常规: 优先「窗口内最后出现更早 / 未出现」的轴（轮替）
        """
        by_axis: Dict[str, List[Goal]] = {}
        for g in pool:
            by_axis.setdefault(g.axis, []).append(g)
        if not by_axis:
            return None

        recent = [r["axis"] for r in state.rotation]
        if len(recent) >= 2 and recent[-1] == recent[-2]:
            # streak ≥2 → 强制换轴
            last_axis = recent[-1]
            forced = AXIS_SELF if last_axis == AXIS_BRYAN else AXIS_BRYAN
            if by_axis.get(forced):
                return forced
            # 防饿死兜底
            state.consecutive_skips += 1
            if state.consecutive_skips >= GOAL_SKIP_ESCAPE_THRESHOLD:
                logger.info(
                    f"[Goal] 防饿死兜底: 连续放弃 {state.consecutive_skips} 次, "
                    f"允许原轴 {last_axis} 再产候选 agent={self.agent_id}"
                )
                return last_axis
            self._save_state(state)
            logger.info(
                f"[Goal] 轮替放弃: streak≥{GOAL_MAX_ROTATION_STREAK} 且强制轴 {forced} "
                f"无候选 agent={self.agent_id} (skip={state.consecutive_skips})"
            )
            return None

        # 常规轮替: 优先「窗口内最后出现更早 / 未出现」的轴
        last_ts = {a: 0.0 for a in GOAL_AXES}
        for r in state.rotation:
            last_ts[r["axis"]] = r["ts"]
        return min(by_axis.keys(), key=lambda a: (last_ts[a], 0 if a == AXIS_BRYAN else 1))

    # ── 4. 状态同步（观察 Decision 结果; 普通 motive → no-op）────────────────

    def on_decision(
        self,
        motive: Any,
        result: Any,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        观察一次 Decision 结果并同步 goal 状态（只认 provenance_ref = goal:{id} 的候选）。

        Decision 动作映射（TG-1 §6.1）:
          - transmit / observe / reflect → 推进: ACTIVE→IN_PROGRESS, advance_count +1
            （G2: 最多 +1/心跳, 仅当选中）, last_advanced_at 更新; 完成判定满足 → COMPLETED
          - do_nothing → 不推进、不标记失败（保持候选）; 计入连续 not_transmit 计数
            （≥3 → SUSPENDED, §8.1 信号 6）

        Returns:
            转移后的新状态（"SUSPENDED" / "COMPLETED" / 推进后 state）或 None（no-op）
        """
        prov = getattr(motive, "provenance_ref", "")
        if not isinstance(prov, str) or not prov.startswith(GOAL_PROVENANCE_PREFIX):
            return None  # 普通 motive → no-op（不决策原则）
        goal_id = prov[len(GOAL_PROVENANCE_PREFIX):]

        now = now or _local_now()
        st = now.timestamp()
        with self._lock:
            store = self._store_for()
            goal = _find_goal(store.get_goals(self.agent_id), goal_id)
            if goal is None or is_terminal_state(goal.state):
                return None  # 终态无出边（§3.3）

            action = getattr(result, "decision", "")
            if action == "do_nothing":
                return self._handle_do_nothing(goal_id, st, store)
            return self._advance_goal(goal, st, store, now)

    def _handle_do_nothing(self, goal_id: str, st: float, store: Any) -> Optional[str]:
        """do_nothing: 不推进、不标记失败; 连续 ≥3 → SUSPENDED（§6.1/§8.1 信号 6）。"""
        state = self._load_state()
        n = state.consecutive_do_nothing.get(goal_id, 0) + 1
        if n >= GOAL_SUSPEND_NOT_TRANSMIT_STREAK:
            state.consecutive_do_nothing.pop(goal_id, None)
            self._save_state(state)
            store.transition_goal(
                goal_id,
                GOAL_STATE_SUSPENDED,
                meta={"suspend_snapshot": json.dumps({
                    "reason": "decision_not_transmit_streak",
                    "streak": n,
                    "ts": st,
                })},
            )
            logger.info(
                f"[Goal] do_nothing ×{n} → SUSPENDED goal={goal_id} agent={self.agent_id}"
            )
            return GOAL_STATE_SUSPENDED
        state.consecutive_do_nothing[goal_id] = n
        self._save_state(state)
        return None

    def _advance_goal(
        self,
        goal: Goal,
        st: float,
        store: Any,
        now: datetime,
    ) -> str:
        """推进（G2: advance_count ≤+1/心跳）→ 完成判定 → COMPLETED + 沉淀通道。"""
        advanced = goal.advanced(st)
        store.upsert_goal(advanced)
        state = self._load_state()
        if state.consecutive_do_nothing.pop(goal.goal_id, None) is not None:
            self._save_state(state)
        logger.info(
            f"[Goal] 推进: goal={goal.goal_id} advance={advanced.advance_count} "
            f"agent={self.agent_id}"
        )
        if self._completion_met(advanced):
            store.transition_goal(goal.goal_id, GOAL_STATE_COMPLETED)
            self.sediment_completion(advanced, now)  # 沉淀失败 fail-closed, 不影响状态
            logger.info(
                f"[Goal] 完成: goal={goal.goal_id} advance={advanced.advance_count} "
                f"agent={self.agent_id}"
            )
            return GOAL_STATE_COMPLETED
        return advanced.state

    # ── 完成判定（§9.3: 结构化 criteria, 规则可验证, 0 LLM 评分）─────────────

    def _completion_met(self, goal: Goal) -> bool:
        """completion_criteria 结构化条件全满足 → True。

        v1 判定执行器（TG-1 §9.3）:
          - kind 必须合法（枚举校验; v1 判定只看 count, kind 是结构约束）
          - count: 至少推进 N 次（advance_count >= count 且 count >= 1）
          无 criteria / 坏 JSON / 非法 kind → False（fail-closed, 不自动完成）
        """
        crit = goal.completion_criteria_dict()
        if not crit:
            return False
        kind = crit.get("kind")
        if kind is not None and kind not in CRITERIA_KINDS:
            logger.warning(
                f"[Goal] criteria.kind 非法 (判定失败): {kind!r} goal={goal.goal_id}"
            )
            return False
        count = crit.get("count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            logger.warning(
                f"[Goal] criteria.count 非法 (判定失败): {count!r} goal={goal.goal_id}"
            )
            return False
        return goal.advance_count >= count

    # ── 沉淀通道（§9.2: 走既有 producer → InnerLifeEvent → SAGE 链）──────────

    def sediment_completion(
        self,
        goal: Goal,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """
        COMPLETED 经验沉淀: 既有 producer 路径（InnerLifeWriter.create_event → trace）,
        由既有 MotiveEngine interpretation 链自然消费 → SAGE。

        原则（§9.1 锁死）:
          - goal 状态写 goals 表（GraphStore 方法）, 不改 facts 写入路径
          - 0 新增 trigger_type（复用 "system", §11.2）; InnerLifeEvent 9 字段不加第 10 个
          - goal 经 trace_ref 引用, 不进 lineage tree
          失败 fail-closed: log warning, 不影响 COMPLETED 状态。
        """
        now = now or _local_now()
        try:
            from src.inner_life.event import Provenance, TRIGGER_TYPE_SYSTEM
            from src.inner_life.trace import NarrativeTraceWriter
            from src.inner_life.writer import InnerLifeWriter
            writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
            event = writer.create_event(
                provenance=Provenance(
                    trigger_type=TRIGGER_TYPE_SYSTEM,
                    actor_id=self.agent_id,
                    source_system="system",
                    trace_ref=f"{GOAL_PROVENANCE_PREFIX}{goal.goal_id}",
                    extras={
                        "goal_id": goal.goal_id,
                        "axis": goal.axis,
                        "title": goal.title[:200],
                        "advance_count": str(goal.advance_count),
                    },
                ),
                ts=now.isoformat(),
            )
            logger.info(
                f"[Goal] 沉淀完成经验: goal={goal.goal_id} event={event.event_id} "
                f"agent={self.agent_id}"
            )
            return event.event_id
        except Exception as e:
            logger.warning(
                f"[Goal] 沉淀失败 (fail-closed): {type(e).__name__}: {e} "
                f"goal={goal.goal_id}"
            )
            return None

    # ── 中断信号（§8.1/§8.2, 心跳侧扫描）────────────────────────────────────

    def apply_interrupt_signals(self, now: Optional[datetime] = None) -> List[str]:
        """
        心跳侧中断信号（每心跳顺带检查, §8.2 无损挂起 0 删除 0 计数重置）:

          - 信号 5: Bryan last-seen 超时（> 4h; 冷启动无记录不挂, 跟 scheduler 一致）
          - 信号 6: Decision 连续 not_transmit ≥3（sidecar 计数兜底, 幂等）
          - ABANDON 周期判定（§3.4/§9.3 v1 规则）: IN_PROGRESS + criteria.timeout_days
            超时 → ABANDONED（终态, 保留 record）

        信号 1/2（私聊突发/多模态高优）: 由 suspend_on_takeover() API 提供,
        未来 event-driven 接线点调用（本次 0 新 tick）。
        信号 3（quiet hours）: §8.1 注, 用唤醒侧过滤（不显式批量挂起）。
        信号 4（proactive_dm cooldown）: scheduler 外层既有检查天然挡（不进本方法）。

        Returns:
            发生转移的 goal_id 列表
        """
        now = now or _local_now()
        st = now.timestamp()
        changed: List[str] = []
        with self._lock:
            store = self._store_for()

            # 信号 5: Bryan last-seen 超时
            bry_dt = _bryan_last_seen_dt()
            if bry_dt is not None:
                last_seen_hours = (now - bry_dt).total_seconds() / 3600.0
                if last_seen_hours > PROACTIVE_DM_BRYAN_INACTIVE_HOURS:
                    for g in store.get_goals(self.agent_id):
                        if g.state in (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS):
                            store.transition_goal(
                                g.goal_id,
                                GOAL_STATE_SUSPENDED,
                                meta={"suspend_snapshot": json.dumps({
                                    "reason": "bryan_last_seen_timeout",
                                    "last_seen_hours": round(last_seen_hours, 1),
                                    "ts": st,
                                })},
                            )
                            changed.append(g.goal_id)

            # 信号 6: 连续 not_transmit ≥3（幂等兜底; on_decision 已实时处理）
            state = self._load_state()
            dirty = False
            for goal_id, n in list(state.consecutive_do_nothing.items()):
                if n >= GOAL_SUSPEND_NOT_TRANSMIT_STREAK:
                    goal = _find_goal(store.get_goals(self.agent_id), goal_id)
                    if goal is not None and goal.state in (
                        GOAL_STATE_ACTIVE,
                        GOAL_STATE_IN_PROGRESS,
                    ):
                        store.transition_goal(
                            goal_id,
                            GOAL_STATE_SUSPENDED,
                            meta={"suspend_snapshot": json.dumps({
                                "reason": "decision_not_transmit_streak",
                                "streak": n,
                                "ts": st,
                            })},
                        )
                        changed.append(goal_id)
                    state.consecutive_do_nothing.pop(goal_id, None)
                    dirty = True
            if dirty:
                self._save_state(state)

            # ABANDON 周期判定（timeout_days 超时 → ABANDONED, 保留 record）
            for g in store.get_goals(self.agent_id, state=GOAL_STATE_IN_PROGRESS):
                crit = g.completion_criteria_dict()
                if not crit:
                    continue
                td = crit.get("timeout_days")
                if isinstance(td, (int, float)) and not isinstance(td, bool) and td > 0:
                    base = g.last_advanced_at if g.last_advanced_at is not None else g.created_at
                    if st - base > td * 86400.0:
                        store.transition_goal(g.goal_id, GOAL_STATE_ABANDONED)
                        changed.append(g.goal_id)
                        logger.info(
                            f"[Goal] ABANDONED (timeout): goal={g.goal_id} "
                            f"timeout_days={td} agent={self.agent_id}"
                        )
            return changed

    def suspend_on_takeover(
        self,
        reason: str = "session_takeover",
        snapshot: Optional[str] = None,
    ) -> List[str]:
        """信号 1/2（Bryan 私聊突发 / 高优先级多模态事件）: 立即挂起。

        v1 提供 API（0 新 tick）; 由未来 event-driven 接线点（voice/router 会话接管处）
        调用。无损挂起: 只写 state + suspend_snapshot + state_updated_at 三字段。
        """
        snapshot_text = snapshot if snapshot is not None else json.dumps({
            "reason": reason,
            "ts": time.time(),
        })
        changed: List[str] = []
        with self._lock:
            store = self._store_for()
            for g in store.get_goals(self.agent_id):
                if g.state in (GOAL_STATE_ACTIVE, GOAL_STATE_IN_PROGRESS):
                    store.transition_goal(
                        g.goal_id,
                        GOAL_STATE_SUSPENDED,
                        meta={"suspend_snapshot": snapshot_text},
                    )
                    changed.append(g.goal_id)
        return changed

    # ── 唤醒扫描（§8.3, 心跳 schedule scan 顺带检查）────────────────────────

    def scheduled_wakeup_scan(self, now: Optional[datetime] = None) -> List[str]:
        """
        SUSPENDED → ACTIVE 唤醒扫描。

        唤醒条件（任一满足, §8.3）:
          ① 静默时段结束 → 由 _is_quiet_hours 前置过滤隐含（夜间一律不唤醒, §8.1 注）
          ② 新一天开始（state_updated_at 跨日 → 过夜重置自然节奏）
          ③ 外部信号解除（Bryan 重新互动, last_seen < 4h）
          ④ 强制最长暂停（> 7 天, 防永久冻结）

        恢复 = 仅 state + state_updated_at（无损, 无需回放; suspend_snapshot 保留为启发）。
        """
        now = now or _local_now()
        if _is_quiet_hours(now):
            return []  # 唤醒侧过滤: 夜间不唤醒
        changed: List[str] = []
        with self._lock:
            store = self._store_for()
            for g in store.get_goals(self.agent_id, state=GOAL_STATE_SUSPENDED):
                if self._wakeup_condition_met(g, now):
                    store.transition_goal(g.goal_id, GOAL_STATE_ACTIVE)
                    changed.append(g.goal_id)
                    logger.info(
                        f"[Goal] 唤醒: goal={g.goal_id} agent={self.agent_id}"
                    )
        return changed

    def _wakeup_condition_met(self, goal: Goal, now: datetime) -> bool:
        st = now.timestamp()
        # ② 新一天开始（跨日; 本地日界, 与 scheduler 作息语义一致）
        try:
            updated_dt = datetime.fromtimestamp(goal.state_updated_at)
            if updated_dt.date() != now.date():
                return True
        except (OSError, ValueError, OverflowError):
            pass
        # ③ 外部信号解除（Bryan 重新互动 < 4h）
        try:
            bry_dt = _bryan_last_seen_dt()
            if bry_dt is not None and st - bry_dt.timestamp() < (
                PROACTIVE_DM_BRYAN_INACTIVE_HOURS * 3600.0
            ):
                return True
        except Exception:
            pass
        # ④ 强制最长暂停（> 7 天）
        if st - goal.state_updated_at >= GOAL_WAKE_FORCE_SECONDS:
            return True
        return False


# ───────────────────────────────────────────────────────────
# 进程级 per-agent 缓存
# ───────────────────────────────────────────────────────────

_providers: Dict[str, GoalMotiveProvider] = {}
_providers_lock = threading.Lock()


def reset_goal_providers() -> None:
    """测试隔离: 关闭并清空 provider 缓存（配合 reset_data_root 使用）。"""
    global _providers
    with _providers_lock:
        for p in _providers.values():
            p.close()
        _providers = {}