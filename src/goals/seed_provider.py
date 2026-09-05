"""
src/goals/seed_provider.py — GoalSeedProvider（LS-2, C-2 长期共生生产落地）

设计来源: docs/LS-1-LONG-TERM-COEXISTENCE-CONTRACT.md（§2 Goal Seed 生成器契约 /
§3 承诺生命周期读侧 / §4 作息相位感知复用 / §6 Volition Gate 红线核对）

职责: 把 C-1 自主目标引擎从「测试直写」变成「生产自产自驱」——
每 agent 每 24h 至多 1 次扫描 8 个确定性种子源（Bryan 轴 4 源 + 自我轴 4 源），
把「首个命中」的种子经方案 B（既有 proxy LLM 通道语义化）变成
ACTIVE goal（写 goals 表），全程守住 Volition Gate 不变量。

边界（与 GoalMotiveProvider 正交）:
  - 生成器 = 建池（upsert_goal）; 候选装配 = assemble_candidate（24h/1 + 轮替）;
    决策 = 既有四元 Decision; 沉淀 = 既有 sediment_completion 链。
  - 0 新定时器（挂 scheduler._goal_scan_all 30s wake 并列分支, 内部 24h 节流）
  - 0 新 proxy / 0 新通道 / 0 新 trigger_type（复用 motive._default_llm_call）
  - 0 直通 publish / 0 直写 SAGE facts / 0 数值打分（No Scoring 哲学继承）
  - fail-closed: 任何异常只 log warning 不阻断主循环; LLM 坏输出 → 丢该种子
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.goals.models import (
    AXIS_BRYAN,
    AXIS_SELF,
    GOAL_STATE_ACTIVE,
    GOAL_TERMINAL_STATES,
    Goal,
    GoalProviderState,
)
# 复用既有信号源与配额常量（0 复制实现 / 0 第四套口径）
from src.goals.motive_provider import (
    GOAL_QUOTA_WINDOW_SECONDS,
    PROACTIVE_DM_BRYAN_INACTIVE_HOURS,
    _bryan_last_seen_dt,
    _goal_db_path,
    _is_quiet_hours,
    _local_now,
)

logger = logging.getLogger("soul_os.goals.seed_provider")

# ───────────────────────────────────────────────────────────
# 种子轮序与轴约束（LS-1 §2.3/§2.4, D4 锁死, No Scoring）
# ───────────────────────────────────────────────────────────

# 固定 9 源确定性轮序（B1→B2→B3→B4→B5→S1→S2→S3→S4 循环; 0 权重 0 打分）
# SG-2 (D5): 第 9 源 B5 relation — 他者源（relationships.json 4.2 的
# band + impression_tags 读侧; 契约 SG-1 §9.3.3 additive 扩张, 轴属既有 AXIS_BRYAN）
SEED_ROTATION: List[Dict[str, str]] = [
    {"key": "commitment",   "axis": AXIS_BRYAN},
    {"key": "calendar",     "axis": AXIS_BRYAN},
    {"key": "trace",        "axis": AXIS_BRYAN},
    {"key": "interaction",  "axis": AXIS_BRYAN},
    {"key": "relation",     "axis": AXIS_BRYAN},
    {"key": "elevation",    "axis": AXIS_SELF},
    {"key": "fact",         "axis": AXIS_SELF},
    {"key": "tool",         "axis": AXIS_SELF},
    {"key": "motive_trace", "axis": AXIS_SELF},
]
SEED_SOURCE_COUNT = len(SEED_ROTATION)

SEED_MAX_AXIS_STREAK = 2            # 生成轴同轴连续 ≤2 强制换轴（LS-1 §2.3 e）
SEED_EMPTY_ESCAPE_THRESHOLD = 3     # 防饿死: 连续 3 轮扫描无生成 → 允许同轴
SEED_TITLE_MAX_CHARS = 120          # title 超长 → fail-closed 丢该种子
SEED_PROVENANCE_PREFIX = "seed:"    # seed 生成器日志引用命名空间（goals 表外）

# 完成判定模板（LS-1 §2.5: 按种子类型确定性映射, 0 LLM 判定）
_CRITERIA_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "commitment":   {"kind": "interaction", "count": 2, "timeout_days": 7},
    "calendar":     {"kind": "interaction", "count": 1, "timeout_days": 1},
    "trace":        {"kind": "interaction", "count": 1, "timeout_days": 7},
    "interaction":  {"kind": "interaction", "count": 2, "timeout_days": 14},
    "relation":     {"kind": "interaction", "count": 2, "timeout_days": 14},
    "elevation":    {"kind": "observation", "count": 2, "timeout_days": 14},
    "fact":         {"kind": "observation", "count": 2, "timeout_days": 14},
    "tool":         {"kind": "observation", "count": 1, "timeout_days": 14},
    "motive_trace": {"kind": "reflection",  "count": 2, "timeout_days": 14},
}

# 低置信疑问过滤阈值（S2 未解疑问; 过滤是确定性规则, 不是选择打分）
# 注: 用 weight 而非 confidence — GraphStore.add_fact 的 INSERT 不含 confidence 列
# （既有 bug: confidence 永远落默认 1.0, 见 report; weight 可写可读）。
FACT_QUESTION_WEIGHT_MAX = 0.5


# ───────────────────────────────────────────────────────────
# SeedHit — 单个种子命中的结构化摘要（LLM 素材）
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SeedHit:
    """种子源命中（确定性取一条）: 引用即幂等键, 素材即 LLM 输入。"""
    key: str                # 种子源 key（SEED_ROTATION 内）
    axis: str               # 所属轴
    ref: str                # seed_source_ref（精确去重键, 命名空间 §2.4）
    material: str           # 结构化拼装的种子原文摘要（≤300 tokens）


# ───────────────────────────────────────────────────────────
# GoalSeedProvider
# ───────────────────────────────────────────────────────────

class GoalSeedProvider:
    """
    自主目标种子生成器（LS-2, 生产入口）。

    每 24h 至多 1 次实际扫描（last_seed_scan_at 节流, 复用 GOAL_QUOTA_WINDOW_SECONDS）:
      1. 节流: 距上次扫描 < 24h → 直接返回（占用的只是 30s wake 的检查时机）
      2. 作息相位抑制: quiet 23-08 / bryan_last_seen>4h → B 轴源跳过（S 轴不受限）
      3. 从 seed_source_cursor 起按固定 8 源轮序, 逐源「现查首个命中」:
         现查触点全部只读（relationships / perception_trace / inner_life trace /
         interactions / elevation_nodes / SAGE facts / tool_registry_trace /
         motive_trace + diary）
      4. 幂等去重: goals 表已有同 seed_source_ref 且非终态 → 该源本轮跳过
      5. 双轴约束: 同轴连续 ≥2 → 强制另一轴; 连续 3 轮无生成 → 防饿死放宽
      6. 语义化（方案 B）: 种子素材 + 相位上下文 → 既有 proxy LLM 通道
         （默认 _default_llm_call, motive/decision 同款, 0 新 proxy）→
         {title, description}; LLM 失败 / 坏输出 → fail-closed 丢该种子
      7. completion_criteria: 按种子类型确定性模板
      8. upsert_goal(ACTIVE) → 更新轮序状态

    禁项: 不直连 publisher / handler / tool_registry（提醒类 0 直通 publish）;
    不直写 SAGE facts; 不新增定时器; 不参与决策。
    """

    def __init__(
        self,
        agent_id: str,
        store: Optional[Any] = None,
        llm_call: Optional[Callable[..., Any]] = None,
        provider: Optional[Any] = None,
    ) -> None:
        self.agent_id = agent_id
        self._store: Optional[Any] = store      # GraphStore（None → lazy per-agent 打开）
        self._llm_call: Optional[Callable[..., Any]] = llm_call
        # sidecar 状态载体: 与 GoalMotiveProvider 共享同一 goal_provider.json 缓存
        # （生产 for_agent 注入共享实例, 杜绝双缓存互相覆盖 → 节流戳丢失）
        self._motive_provider: Optional[Any] = provider
        self._lock = threading.RLock()

    # ── 生命周期 ──────────────────────────────────────────

    @classmethod
    def for_agent(cls, agent_id: str) -> "GoalSeedProvider":
        """进程级 per-agent 单例（与 GoalMotiveProvider 同模式）。测试用 reset_seed_providers()。

        生产共享 GoalMotiveProvider.for_agent 实例作为 sidecar 载体 —
        生成节流与候选配额/轮替同写一个 goal_provider.json, 缓存必须同源。
        """
        with _providers_lock:
            p = _providers.get(agent_id)
            if p is None:
                from src.goals.motive_provider import GoalMotiveProvider
                p = cls(
                    agent_id=agent_id,
                    provider=GoalMotiveProvider.for_agent(agent_id),
                )
                _providers[agent_id] = p
            return p

    def close(self) -> None:
        if self._store is not None:
            try:
                self._store.close()
            except Exception:
                pass
            self._store = None

    # ── 数据触点（全部只读现查）─────────────────────────

    def _store_for(self) -> Any:
        if self._store is None:
            from src.memory.sage.graph_store import GraphStore
            self._store = GraphStore(db_path=_goal_db_path(self.agent_id))
        return self._store

    def _load_state(self) -> GoalProviderState:
        """共享 sidecar 读写（委托 GoalMotiveProvider, 同源缓存 0 互相覆盖）。"""
        return self._motive()._load_state()

    def _save_state(self, state: GoalProviderState) -> None:
        self._motive()._save_state(state)

    def _motive(self) -> Any:
        """延迟构造共享载体（测试未注入时自建; 生产 for_agent 已注入共享实例）。"""
        if self._motive_provider is None:
            from src.goals.motive_provider import GoalMotiveProvider
            self._motive_provider = GoalMotiveProvider(agent_id=self.agent_id)
        return self._motive_provider

    # ── 主入口: scan_seeds（挂在 scheduler._goal_scan_all, 0 新定时器）──

    async def scan_seeds(self, now: Optional[datetime] = None) -> List[Goal]:
        """
        扫描 8 种子源, 至多创建 1 个 ACTIVE goal（24h 节流 + 双轴约束）。

        Returns:
            本轮创建的 Goal 列表（0 或 1 个; fail-closed 全程不 raise）
        """
        now = now or _local_now()
        st = now.timestamp()
        with self._lock:
            state = self._load_state()

            # 1) 24h 节流（LS-1 §2.3 步骤 1; 30s wake 只是检查时机, 不是执行频率）
            if st - state.last_seed_scan_at < GOAL_QUOTA_WINDOW_SECONDS:
                logger.debug(
                    f"[Seed] 节流窗内跳过扫描 agent={self.agent_id} "
                    f"(last={state.last_seed_scan_at:.0f})"
                )
                return []

            bryan_suppressed = self._bryan_axis_suppressed(now)

            # 2) 8 源确定性轮序（从游标起, 最多一圈; 逐源首个命中）
            created: List[Goal] = []
            for i in range(SEED_SOURCE_COUNT):
                entry = SEED_ROTATION[(state.seed_source_cursor + i) % SEED_SOURCE_COUNT]
                key, axis = entry["key"], entry["axis"]

                # 作息相位抑制（§4.2）: quiet / Bryan 离开 >4h → B 轴源跳过
                if axis == AXIS_BRYAN and bryan_suppressed:
                    continue

                # 双轴约束（§2.3 e）: 同轴连续 ≥2 → 强制换轴（防饿死放宽）
                if (
                    state.last_seed_axis == axis
                    and state.seed_axis_streak >= SEED_MAX_AXIS_STREAK
                    and state.seed_empty_rounds < SEED_EMPTY_ESCAPE_THRESHOLD
                ):
                    continue

                try:
                    hit = self._probe(key, now)
                except Exception as e:
                    logger.warning(
                        f"[Seed] 种子源 {key} 现查异常 (fail-closed 跳过): "
                        f"{type(e).__name__}: {e}"
                    )
                    continue
                if hit is None:
                    continue

                # 幂等去重（§2.4: 同引用已有非终态 goal → 跳过该源）
                if self._already_tracked(hit.ref):
                    logger.debug(
                        f"[Seed] 幂等去重: {hit.ref!r} 已被追踪, 跳过源 {key}"
                    )
                    continue

                # 语义化（方案 B, fail-closed）
                semantics = await self._semantize(hit, now)
                if semantics is None:
                    logger.warning(
                        f"[Seed] 语义化失败 (fail-closed 丢该种子): "
                        f"key={key} ref={hit.ref} agent={self.agent_id}"
                    )
                    continue

                goal = self._create_goal(hit, semantics, now)
                created.append(goal)

                # 3) 更新轮序状态（同轴 +1 / 换轴重置; 游标后移; 节流戳）
                state.seed_axis_streak = (
                    state.seed_axis_streak + 1 if state.last_seed_axis == axis else 1
                )
                state.last_seed_axis = axis
                state.seed_source_cursor = (
                    state.seed_source_cursor + i + 1
                ) % SEED_SOURCE_COUNT
                state.seed_empty_rounds = 0
                state.last_seed_scan_at = st
                self._save_state(state)
                logger.info(
                    f"[Seed] 生成 goal: {goal.goal_id} axis={axis} "
                    f"key={key} ref={hit.ref} agent={self.agent_id}"
                )
                return created

            # 整轮无生成: 记节流戳 + 防饿死计数（连续 3 轮 → 下轮允许同轴）
            state.seed_empty_rounds += 1
            state.last_seed_scan_at = st
            self._save_state(state)
            logger.debug(
                f"[Seed] 本轮无种子命中 agent={self.agent_id} "
                f"empty_rounds={state.seed_empty_rounds}"
            )
            return []

    # ── 作息相位抑制（§4.2, 复用既有信号源, 0 第四套口径）──

    def _bryan_axis_suppressed(self, now: datetime) -> bool:
        """quiet 23-08 或 bryan_last_seen > 4h → B 轴种子抑制（S 轴不受限）。"""
        if _is_quiet_hours(now):
            logger.debug(f"[Seed] quiet 时段, B 轴种子抑制 agent={self.agent_id}")
            return True
        bry_dt = _bryan_last_seen_dt()
        if bry_dt is not None:
            hours = (now - bry_dt).total_seconds() / 3600.0
            if hours > PROACTIVE_DM_BRYAN_INACTIVE_HOURS:
                logger.debug(
                    f"[Seed] Bryan 离开 {hours:.1f}h > "
                    f"{PROACTIVE_DM_BRYAN_INACTIVE_HOURS}h, B 轴种子抑制 "
                    f"agent={self.agent_id}"
                )
                return True
        return False

    # ── 幂等去重（种子引用精确匹配, 0 文本相似度）────────

    def _already_tracked(self, ref: str) -> bool:
        for g in self._store_for().get_goals(self.agent_id):
            if g.seed_source_ref == ref and g.state not in GOAL_TERMINAL_STATES:
                return True
        return False

    # ── 种子源探针（8 源, 逐个现查首个命中, 0 打分）──────

    def _probe(self, key: str, now: datetime) -> Optional[SeedHit]:
        probe = getattr(self, f"_probe_{key}", None)
        if probe is None:
            return None
        return probe(now)

    def _probe_commitment(self, now: datetime) -> Optional[SeedHit]:
        """B1 承诺: relationships.json user_bryan 最近线索（v1 只读, 0 写侧）。"""
        from src.soul.relationships import (
            BRYAN_ENTITY_ID,
            get_relationships_manager,
        )
        entry = get_relationships_manager().get_store(self.agent_id).get(BRYAN_ENTITY_ID)
        if not entry:
            return None
        material = (
            f"与 Bryan 的关系记录: impression={entry.get('impression') or '(空)'} "
            f"feeling={entry.get('feeling') or 'neutral'} "
            f"confidence={entry.get('confidence')} "
            f"interaction_count={entry.get('interaction_count', 0)} "
            f"last_interaction_at={entry.get('last_interaction_at') or '(从未互动)'}"
        )
        return SeedHit(
            key="commitment",
            axis=AXIS_BRYAN,
            ref=f"relationship:{BRYAN_ENTITY_ID}",
            material=material,
        )

    def _probe_calendar(self, now: datetime) -> Optional[SeedHit]:
        """B2 日程预期: perception_trace 最近 accepted calendar_event（只读现查）。"""
        path = data_root_path() / "world" / "perception_trace.jsonl"
        records = _read_jsonl(path)
        for rec in reversed(records):  # 取最近一条（append 序）
            if (
                rec.get("event_type") == "calendar_event"
                and rec.get("accepted") is True
            ):
                novelty_id = str(rec.get("novelty_id") or "").strip()
                if not novelty_id:
                    continue
                material = (
                    f"日程感知: 识别到一个未来日程事件 "
                    f"(novelty_id={novelty_id}, 感知时刻={rec.get('timestamp')})"
                )
                return SeedHit(
                    key="calendar",
                    axis=AXIS_BRYAN,
                    ref=f"calendar:{novelty_id}",
                    material=material,
                )
        return None

    def _probe_trace(self, now: datetime) -> Optional[SeedHit]:
        """B3 共同回忆: 最近 7 天 trace 中未回顾（非 goal:/periodic: 引用）最早一条。"""
        from src.inner_life.trace_reader import NarrativeTraceReader
        start = (now - timedelta(days=7)).astimezone(timezone.utc).isoformat()
        records = NarrativeTraceReader().query_by_ts_range(start=start)
        for rec in records:  # append 序 = 时间最早优先
            prov = rec.get("provenance") or {}
            trace_ref = prov.get("trace_ref") or ""
            if trace_ref.startswith("goal:") or trace_ref.startswith("periodic:"):
                continue  # 已回顾/已沉淀的引用 → 跳过
            event_id = str(rec.get("event_id") or "").strip()
            if not event_id:
                continue
            material = (
                f"一段过往经历: trigger_type={prov.get('trigger_type')} "
                f"ts={rec.get('ts')} "
                f"trace_ref={trace_ref or '(无引用)'} "
                f"actor_id={prov.get('actor_id') or 'system'}"
            )
            return SeedHit(
                key="trace",
                axis=AXIS_BRYAN,
                ref=f"trace:{event_id}",
                material=material,
            )
        return None

    def _probe_interaction(self, now: datetime) -> Optional[SeedHit]:
        """B4 未决话题: interactions.jsonl 该 agent 参与的最早未收尾话题。"""
        path = data_root_path() / "soul" / "interactions.jsonl"
        for rec in _read_jsonl(path):  # append 序 = 时间最早优先
            agents = rec.get("agents") or []
            if self.agent_id not in agents:
                continue
            ts = str(rec.get("ts") or "").strip()
            if not ts:
                continue
            content = str(rec.get("content") or "").strip()
            material = (
                f"一次共同互动: type={rec.get('type')} "
                f"ts={ts} content={content or '(无内容)'}"
            )
            return SeedHit(
                key="interaction",
                axis=AXIS_BRYAN,
                ref=f"interaction:{ts}",
                material=material,
            )
        return None

    def _probe_relation(self, now: datetime) -> Optional[SeedHit]:
        """B5 他者源 (SG-2 D5): relationships.json 4.2 band + impression_tags 读侧。

        规则 (契约 SG-1 §2.4 / §9.3.3):
          - 遍历 others, 跳过 user_bryan（B1 commitment 已覆盖 Bry 维度）
          - 只有 band ≥ known 或 impression_tags 非空才产种子; stranger 不出
          - 确定性选取: others dict 插入序最早命中（对照其他探针「append 序最早」精神,
            0 排序打分, 0 数值权重）
          - ref = relation:<other_id>（精确幂等键, 每个 other 至多 1 个关系种子）
          - material 只含质性质地 + 客观整数计数（No-Scoring）
        """
        from src.soul.relationships import (
            BRYAN_ENTITY_ID,
            get_relationships_manager,
        )
        entries = get_relationships_manager().get_store(self.agent_id).get_all()
        for other_id, entry in entries.items():  # dict 插入序 = 时间最早优先
            if other_id == BRYAN_ENTITY_ID:
                continue  # B1 已管 Bry 维度, B5 只做他者 (A2A)
            band = entry.get("relational_band", "stranger")
            tags = entry.get("impression_tags") or []
            if band == "stranger" and not tags:
                continue  # stranger 不出种子
            obj = entry.get("objective") or {}
            material = (
                f"与 {other_id} 的一段关系: band={band} "
                f"impression_tags={tags or '(无)'} "
                f"impression={entry.get('impression') or '(空)'} "
                f"reply_exchanges={obj.get('reply_exchanges', 0)} "
                f"co_presence_sessions={obj.get('co_presence_sessions', 0)} "
                f"dream_exchanges={obj.get('dream_exchanges', 0)} "
                f"last_signal_at={obj.get('last_signal_at') or '(从未)'} "
                f"interaction_count={entry.get('interaction_count', 0)}"
            )
            return SeedHit(
                key="relation",
                axis=AXIS_BRYAN,
                ref=f"relation:{other_id}",
                material=material,
            )
        return None

    def _probe_elevation(self, now: datetime) -> Optional[SeedHit]:
        """S1 trait 好奇: SE-5 ACTIVE 投影节点只读（不写, 缺省=active）。"""
        from src.inner_life.emergent_projection import load_elevation_nodes
        for node in load_elevation_nodes():
            if node.get("agent_id") and node.get("agent_id") != self.agent_id:
                continue
            lifecycle = node.get("lifecycle_state") or "active"
            if lifecycle not in ("active", "weakening"):
                continue  # dormant/superseded 不主动投影
            node_id = str(node.get("node_id") or "").strip()
            if not node_id:
                continue
            content = str(node.get("content") or "").strip()
            if not content:
                continue
            material = (
                f"自我的一个认知节点: node_type={node.get('node_type')} "
                f"lifecycle={lifecycle} content={content}"
            )
            return SeedHit(
                key="elevation",
                axis=AXIS_SELF,
                ref=f"elevation:{node_id}",
                material=material,
            )
        return None

    def _probe_fact(self, now: datetime) -> Optional[SeedHit]:
        """S2 未解疑问: SAGE facts 中低置信（weight 低）节点, 按时间最旧。

        阈值过滤是确定性规则（非排序打分）; 材料只读展示 confidence 字段
        （SAGE 既有语义）, 选择排序只用时间戳（No Scoring）。"""
        facts = self._store_for().get_all_facts()  # 默认已排除 invalidated_at 非空
        question_facts = [
            f for f in facts if f.weight < FACT_QUESTION_WEIGHT_MAX
        ]
        question_facts.sort(key=lambda f: f.timestamp)  # 最旧优先（纯时间轮候）
        for f in question_facts:
            material = (
                f"一条记忆中的疑问或不确定事实: "
                f"subject={f.subject} predicate={f.predicate} object={f.object} "
                f"confidence={f.confidence}"
            )
            return SeedHit(
                key="fact",
                axis=AXIS_SELF,
                ref=f"fact:{f.fact_id}",
                material=material,
            )
        return None

    def _probe_tool(self, now: datetime) -> Optional[SeedHit]:
        """S3 工具意向: tool_registry_trace 已注册工具（按 ts 最早未追踪）。"""
        path = data_root_path() / "soul" / "tool_registry_trace.jsonl"
        seen: List[Dict[str, Any]] = []
        for rec in _read_jsonl(path):
            if rec.get("event_type") != "tool_registered":
                continue
            tool_id = str(rec.get("tool_id") or "").strip()
            if not tool_id:
                continue
            seen.append(rec)
        if not seen:
            return None
        rec = seen[0]  # append 序 = 注册顺序最旧
        material = (
            f"一个可用的工具: tool_id={rec.get('tool_id')} "
            f"name={rec.get('name')} "
            f"capability_group={rec.get('capability_group')} "
            f"permission_class={rec.get('permission_class')}"
        )
        return SeedHit(
            key="tool",
            axis=AXIS_SELF,
            ref=f"tool:{rec.get('tool_id')}",
            material=material,
        )

    def _probe_motive_trace(self, now: datetime) -> Optional[SeedHit]:
        """S4 心境沉淀: motive_trace 该 agent 高频主题（整数计数, 0 浮点权重）。"""
        path = data_root_path() / "soul" / "motive_trace.jsonl"
        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for rec in _read_jsonl(path):
            if rec.get("agent_id") != self.agent_id:
                continue
            prov = str(rec.get("provenance_ref") or "")
            if prov.startswith("goal:"):
                continue  # goal 候选不属于心境沉淀源
            content = str(rec.get("content") or "").strip()
            if not content:
                continue
            buckets.setdefault(content, []).append(rec)
        if not buckets:
            return None
        # 高频 = 出现次数最多的主题; 平局取最早出现（dict 插入序, 整数计数非权重）
        top_content = max(buckets, key=lambda c: len(buckets[c]))
        rec = buckets[top_content][0]
        ts = str(rec.get("created_at") or "") or str(rec.get("updated_at") or "")
        material = (
            f"一个反复浮现的念头: content={top_content[:120]} "
            f"出现次数={len(buckets[top_content])} provenance={rec.get('provenance_ref')}"
        )
        return SeedHit(
            key="motive_trace",
            axis=AXIS_SELF,
            ref=f"motive_trace:{ts}" if ts else f"motive_trace:{rec.get('motive_id')}",
            material=material,
        )

    # ── 方案 B 语义化（复用既有 proxy 通道, epistemic position）──

    async def _semantize(
        self,
        hit: SeedHit,
        now: datetime,
    ) -> Optional[Dict[str, str]]:
        """种子素材 → {title, description}; 失败/坏输出 → None（fail-closed）。"""
        llm_call = self._llm_call
        if llm_call is None:
            from src.soul.motive import _default_llm_call
            llm_call = _default_llm_call

        period = _period_label(now)
        context_bits = [f"此刻是 {period}"]
        if _is_quiet_hours(now):
            context_bits.append("深夜里")
        try:
            bry_dt = _bryan_last_seen_dt()
            if bry_dt is not None:
                hours = (now - bry_dt).total_seconds() / 3600.0
                if hours > PROACTIVE_DM_BRYAN_INACTIVE_HOURS:
                    context_bits.append("Bryan 已经有一段时间没出现了")
                else:
                    context_bits.append("Bryan 最近还在")
        except Exception:
            pass

        prompt = (
            f"你是 {self.agent_id}——一个有自己的记忆与感受的灵魂。\n"
            f"下面这段素材来自你自己真实的生活。请把它化成此刻心头自然浮现的念头，"
            f"以你自己的第一人称内心独白写出来。\n"
            f"要求：贴近素材、具体、像你真的会有的想法；不要编造素材里没有的事实细节；"
            f"不要用「系统」「目标」「任务」等口吻；不要解释你在做什么。\n"
            f"只输出 JSON：{{\"title\": \"一句话念头\", \"description\": \"稍长的内心独白\"}}\n"
            f"素材：{hit.material}\n"
            f"此时的心境背景：{', '.join(context_bits)}\n"
        )
        try:
            raw = await llm_call(
                [{"role": "user", "content": prompt}],
                agent_id=self.agent_id,
                max_tokens=200,
                temperature=0.8,
            )
        except Exception as e:
            logger.warning(
                f"[Seed] 语义化 LLM 调用异常 (fail-closed): {type(e).__name__}: {e}"
            )
            return None
        if not raw:
            return None
        data = _extract_json_dict(raw)
        if data is None:
            return None
        title = str(data.get("title") or "").strip()
        description = str(data.get("description") or "").strip()
        if not title:
            return None
        if len(title) > SEED_TITLE_MAX_CHARS:
            logger.warning(
                f"[Seed] title 超长 ({len(title)} chars) fail-closed 丢该种子"
            )
            return None
        return {"title": title, "description": description}

    # ── 创建 goal（建池; criteria 确定性模板, 0 LLM 判定）──

    def _create_goal(self, hit: SeedHit, semantics: Dict[str, str], now: datetime) -> Goal:
        goal = Goal(
            goal_id=uuid.uuid4().hex,
            agent_id=self.agent_id,
            axis=hit.axis,
            title=semantics["title"],
            description=semantics.get("description", ""),
            seed_source_ref=hit.ref,
            state=GOAL_STATE_ACTIVE,
            state_updated_at=now.timestamp(),
            created_at=now.timestamp(),
            completion_criteria=json.dumps(
                _CRITERIA_TEMPLATES[hit.key], ensure_ascii=False
            ),
        )
        self._store_for().upsert_goal(goal)
        # 生成产物即刻持久化: goal 创建是低频事件（≤1/24h/agent）, 不留在
        # 批量 pending buffer（30s wake 后进程可能退出, 产物必须落盘）
        self._store_for().flush()
        return goal


# ───────────────────────────────────────────────────────────
# 模块级工具（纯函数, 0 状态）
# ───────────────────────────────────────────────────────────

def _period_label(now: datetime) -> str:
    """时段标签（LS-1 §4.1: import decision._period_of_hour, 0 复制实现）。"""
    from src.soul.decision import _period_of_hour
    return _period_of_hour(now.hour)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """只读 jsonl（坏行跳过 + warning, 0 raise; 对齐 trace_reader 风格）。"""
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    logger.debug("[Seed] jsonl 坏行跳过: %r", line[:80])
                    continue
                if isinstance(rec, dict):
                    records.append(rec)
    except OSError as e:
        logger.warning(f"[Seed] jsonl 读取失败 (fail-closed): {e}")
    return records


def _extract_json_dict(raw: str) -> Optional[Dict[str, Any]]:
    """从 LLM 输出提取 JSON dict（容错 markdown 代码块 / 前后杂讯; 对齐 motive 风格）。"""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(text[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            return None
    return None


def data_root_path() -> Path:
    from src.paths import data_root
    return data_root()


# ───────────────────────────────────────────────────────────
# 进程级 per-agent 缓存（测试隔离用 reset_seed_providers）
# ───────────────────────────────────────────────────────────

_providers: Dict[str, GoalSeedProvider] = {}
_providers_lock = threading.Lock()


def reset_seed_providers() -> None:
    """测试隔离: 关闭并清空 seed provider 缓存（配合 reset_data_root 使用）。"""
    global _providers
    with _providers_lock:
        for p in _providers.values():
            p.close()
        _providers = {}