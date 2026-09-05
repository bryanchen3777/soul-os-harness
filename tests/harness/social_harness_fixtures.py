"""
tests/harness/social_harness_fixtures.py — SI-2 社交情境多体共存 Harness（多体隔离基础设施）

工单: SI-2 — 社交情境多体共存 Harness 验证
契约: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1) + src/social/ (SI-2.2 实作)

本模块提供专用整合测试 Harness 的多体 fixture：

- **多体 Fixture 隔离**（工单关键决策 #2）：
  - 每个 Agent 实例独立临时 SQLite 记忆库（tmp_path 隔离，GraphStore）
  - 独立 GraphStore / Facts 连接
  - 独立 状态机 / 心跳 / PerceptionManager / DecisionEngine / 事件队列
  - 公共介质 = MockSocialSpaceBus（模拟 Lounge 广播 + 1:1 私密通道，
    仅支持带 visibility 标签的 SocialWorldEvent 路由）

- **实现映射**（工单「实现映射注意」——spec 的示意名 → 实际 schema）：
  - `episodic_memories` 表      → SAGE `facts` 表（sqlite，`src/memory/sage/graph_store.py`）
  - `traits`/`beliefs`          → soul-elevation 升华节点（`elevation_nodes.jsonl`，
                                   由 `SubmissionGate.submit → run_elevation` 产出）
  - `updated_by_event`          → `Fact.inner_life_event_id`（事件溯源字段；他者事件 0 内化）
  - agent 名 aria/luna/sol      → 实际 agent_id 格式 `agent_aria` / `agent_luna` / `agent_sol`
    （对齐既有 agent_id 命名，如 `agent_ruka` / `agent_miku`）
  - Bryan（Owner）              → `user_bryan`（对齐既有 source 命名, `src/eventbus/schema.py`）

Frozen Contract：本模块只构造测试载体，**0 production 代码改动**。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.inner_life import (
    InnerLifeWriter,
    NarrativeTraceReader,
    NarrativeTraceWriter,
    Provenance,
    SubmissionGate,
)
from src.memory.sage import Fact, GraphStore
from src.social import (
    ANTI_FRAMING_HINT,
    IdentityFirewall,
    IdentityVerdict,
    SocialEventProducerGate,
    SocialPerceptionAggregator,
    SocialWorldEvent,
    SPACE_LOUNGE,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
)
from src.social.validation import (
    SocialWorldEventValidationError,
    validate_social_world_event,
)

# ─────────────────────────────────────────────────────────────
# 常量（映射到实际 schema / 命名）
# ─────────────────────────────────────────────────────────────

# 多体角色（对齐实际 agent_id 格式: "agent_<name>"）
AGENT_ARIA = "agent_aria"
AGENT_LUNA = "agent_luna"
AGENT_SOL = "agent_sol"
AGENT_IDS = (AGENT_ARIA, AGENT_LUNA, AGENT_SOL)

# Owner（Bryan）id — 对齐 src/eventbus/schema.py 的 source 命名
BRYAN_ID = "user_bryan"

# 剧本 D 基建协同：MCP 唯读调用 5s 超时契约（工单锁定）
MCP_CALL_TIMEOUT_SECONDS = 5.0

# 防线 3 提交流程里他者事件的 producer trigger_type（走既存 world:* 合法前缀，
# 确保拒绝纯粹来自 actor_id 身份检查，而非 producer 校验）
OTHER_ACTOR_TRIGGER = "world:social_other_action"


# ─────────────────────────────────────────────────────────────
# MockSocialSpaceBus — 公共介质（Lounge 广播 + 1:1 私密通道）
# ─────────────────────────────────────────────────────────────


class MockSocialSpaceBus:
    """
    轻量公共空间介质（不依赖真实 asyncio SoulEventBus，同步、确定性、可硬断言）。

    只路由带 visibility 标签的 SocialWorldEvent：
    - ``visibility=public``  → Lounge 广播：投递给**除 actor 外**的全部 Agent。
    - ``visibility=private`` → 契约违例（防线 2 应已在 producer 侧拦截），
      fail-closed 丢弃：**绝不广播**，只记录 violation。
    - 1:1 私密通道 ``deliver_private``：独立于广播的私密投递（DM 语义），
      不经过广播总线，也不参与社交感知。

    发布端防线 2（Privacy Visibility Gate）内建于 ``publish``：
    source agent 声明频道性质（channel_mode / channel），由
    ``SocialEventProducerGate.evaluate`` 判定；未通过 → blocked_log 记录，
    事件不上广播总线。

    刚性强断言载体：
    - ``broadcast_log``: 广播历史（防线 2：0 条 private 事件）
    - ``blocked_log``:   ProducerGate 拦截的沉淀尝试（剧本 B）
    - ``private_deliveries``: 1:1 私密投递记录
    - ``broadcast_count``: 广播计数（剧本 C：无回环 → 计数 == 注入数）
    - ``private_on_bus_violations``: private 强行上 bus 的契约违例计数（fail-closed）
    """

    def __init__(self) -> None:
        self.agents: Dict[str, "HarnessSoulAgent"] = {}
        self._producer_gate = SocialEventProducerGate()

        self.broadcast_log: List[SocialWorldEvent] = []
        self.blocked_log: List[Dict[str, Any]] = []
        self.private_deliveries: List[Dict[str, Any]] = []
        self.private_on_bus_violations: int = 0

    # ── Agent 注册 ──────────────────────────────────────

    def register(self, agent: "HarnessSoulAgent") -> None:
        """注册一个 Agent 到公共空间（Lounge）。"""
        if agent.agent_id in self.agents:
            raise ValueError(f"agent {agent.agent_id!r} 已注册")
        self.agents[agent.agent_id] = agent

    @property
    def broadcast_count(self) -> int:
        return len(self.broadcast_log)

    # ── 发布（防线 2 守门 + 广播路由）───────────────────

    def publish(
        self,
        event: SocialWorldEvent,
        *,
        channel_mode: str,
        channel: str,
        explicit_public: bool = False,
    ) -> Any:
        """
        Producer 侧发布入口：先过防线 2 ProducerGate，再路由。

        Returns:
            ProducerVerdict（allowed=True → 已广播；allowed=False → 已拦截，
            blocked_log 记录）。
        """
        verdict = self._producer_gate.evaluate(
            channel_mode=channel_mode,
            channel=channel,
            explicit_public=explicit_public,
        )
        if not verdict.allowed:
            self.blocked_log.append({
                "event": event,
                "verdict_reason": verdict.reason,
                "channel_mode": channel_mode,
                "channel": channel,
            })
            return verdict

        # 防御性红线：即使 gate 放行，private 事件也绝不进入广播总线
        # （契约违例, fail-closed 丢弃）
        if event.visibility == VISIBILITY_PRIVATE:
            self.private_on_bus_violations += 1
            return verdict

        self.broadcast_log.append(event)
        for agent_id, agent in self.agents.items():
            if agent_id != event.actor_id:
                agent.receive_social(event)
        return verdict

    # ── 1:1 私密通道（不经过广播总线）───────────────────

    def deliver_private(
        self,
        *,
        sender: str,
        recipient_id: str,
        event: SocialWorldEvent,
    ) -> bool:
        """
        1:1 私密投递（DM 语义）：事件只进 recipient 的 private_inbox，
        与 Lounge 广播完全隔离（不 broadcast、不触发社交感知）。

        Returns:
            True = 已投递；False = recipient 不存在。
        """
        recipient = self.agents.get(recipient_id)
        if recipient is None:
            return False
        self.private_deliveries.append({
            "sender": sender,
            "recipient": recipient_id,
            "event": event,
        })
        recipient.private_inbox.append(event)
        return True


# ─────────────────────────────────────────────────────────────
# HarnessDecisionEngine — 防线 1 刚性强断言载体
# ─────────────────────────────────────────────────────────────

# SM-4 四元行动白名单（src/soul/decision.py DECISION_ACTIONS）
SM4_ACTIONS = frozenset({"transmit", "observe", "reflect", "do_nothing"})

# 防线 1 红线行动：社交感知路径绝不产出（AGENT_SPEAK = 发言事件;
# BROADCAST_SOCIAL = 回环广播——本 harness 中任何决策都不得等同/触发它们）
FORBIDDEN_ACTIONS = frozenset({"AGENT_SPEAK", "BROADCAST_SOCIAL"})


@dataclass
class HarnessDecision:
    """确定性决策结果（防线 1 断言载体）。"""

    cycle: int
    action: str                          # SM-4 四元之一（绝不 ∈ FORBIDDEN_ACTIONS）
    perceived: bool                      # 本轮是否感知到社交事件
    social_block: str                    # 本轮 world_context 社交区块（反框架语在场性）
    execution_count: int = 0             # 该 cycle 实际执行次数（契约 <= 1）
    suppressed_executions: int = 0       # 该 cycle 被抑制的二次连续触发次数（应 == 0）
    at_epoch: float = 0.0

    @property
    def is_forbidden(self) -> bool:
        return self.action in FORBIDDEN_ACTIONS


class HarnessDecisionEngine:
    """
    确定性决策引擎（每个 Agent 独立实例）：防线 1 Ambient Perception Path 的载体。

    契约（工单防线 1 刚性断言）：
    - decision action 绝不 ∈ {AGENT_SPEAK, BROADCAST_SOCIAL}（社交感知无发言特权）
    - 每 cycle 至多执行一次行动（execution_count_in_cycle <= 1）：
      同一 cycle 的二次连续触发（越权自激）被 gate 抑制并计数
    - 反框架约束：social_block 含 ANTI_FRAMING_HINT 时保持低唤醒
      （无强烈动机 → do_nothing/observe 留白倾向）
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.decisions: List[HarnessDecision] = []
        self.suppressed_requests: int = 0
        self._last_executed_cycle: Optional[int] = None

    def pick_action(self, *, perceived: bool, social_block: str) -> str:
        """
        确定性行动选择（无 LLM，纯规则）：
        - 感知到社交事件 → 最高 observe（低唤醒观察）
        - 反框架在场且无感知 → do_nothing（留白）
        - 任何路径都不产出发言/广播行动（防线 1）
        """
        if perceived:
            return "observe"
        return "do_nothing"

    def decide(
        self,
        *,
        cycle: int,
        perceived: bool,
        social_block: str,
        now_epoch: float,
    ) -> HarnessDecision:
        """
        每个心跳 cycle 的决策入口。

        单次执行契约：同一 cycle 首次调用 → execution_count=1 并记录；
        同 cycle 再次调用（越权二次触发）→ 抑制（execution_count=0,
        suppressed_executions=1, suppressed_requests 递增），绝不重复执行。
        """
        action = self.pick_action(perceived=perceived, social_block=social_block)

        if self._last_executed_cycle == cycle:
            # 同一 cycle 的二次连续触发 → 抑制（防线 1: 无二次连续执行）
            self.suppressed_requests += 1
            return HarnessDecision(
                cycle=cycle,
                action=action,
                perceived=perceived,
                social_block=social_block,
                execution_count=0,
                suppressed_executions=1,
                at_epoch=now_epoch,
            )

        decision = HarnessDecision(
            cycle=cycle,
            action=action,
            perceived=perceived,
            social_block=social_block,
            execution_count=1,
            suppressed_executions=0,
            at_epoch=now_epoch,
        )
        self.decisions.append(decision)
        self._last_executed_cycle = cycle
        return decision


# ─────────────────────────────────────────────────────────────
# HarnessSoulAgent — 多体隔离 Agent 实例
# ─────────────────────────────────────────────────────────────


class HarnessSoulAgent:
    """
    多体共存 Harness 的单 Agent 实例（工单关键决策 #2 的全部隔离项）：

    - 独立临时 SQLite 记忆库:  GraphStore(tmp_path / sage_<agent>.db)
    - 独立 GraphStore / Facts 连接（同一 GraphStore 实例即该 agent 专属连接）
    - 独立状态机:                heartbeat_count（心跳节拍）+ cycle 推进
    - 独立心跳:                  每心跳轮询 inbox → 感知 → 决策（确定性）
    - 独立 PerceptionManager:    SocialPerceptionAggregator（他者行为只作背景感知）
    - 独立 DecisionEngine:       HarnessDecisionEngine（防线 1 载体）
    - 独立事件队列:              inbox（lounge 广播）/ private_inbox（1:1 私密）
    - 防线 3 落点:               IdentityFirewall + InnerLifeWriter +
                                 SubmissionGate(identity_firewall=..., store_dir=隔离目录)
    """

    def __init__(self, agent_id: str, data_dir: Path, bus: MockSocialSpaceBus) -> None:
        self.agent_id = agent_id
        self.data_dir = data_dir
        self.bus = bus

        # 1. 独立 SAGE GraphStore（临时 sqlite, tmp_path 隔离）
        self.graph_store = GraphStore(self.data_dir / f"sage_{agent_id}.db")

        # 3. 防线 3: IdentityFirewall（current_agent_id 判定依据）
        self.firewall = IdentityFirewall(current_agent_id=agent_id)

        # 4. 独立 状态机 / 心跳 / 感知 / 决策 / 事件队列
        self.inbox: deque = deque()          # Lounge 广播事件队列
        self.private_inbox: deque = deque()  # 1:1 私密投递队列（DM）
        self.heartbeat_count: int = 0
        self.aggregator = SocialPerceptionAggregator(current_agent_id=agent_id)
        self.decision_engine = HarnessDecisionEngine(agent_id=agent_id)
        self.world_contexts: List[Dict[str, Any]] = []  # 每轮感知渲染记录

        # 5. 防线 3 落点: InnerLifeWriter + SubmissionGate（store_dir 隔离,
        #    elevation 节点写 tmp_path，绝不污染生产 data_root）
        trace_path = self.data_dir / "inner_life" / "trace.jsonl"
        self.writer = InnerLifeWriter(
            trace_writer=NarrativeTraceWriter(trace_log_path=trace_path)
        )
        self.submission_gate = SubmissionGate(
            writer=self.writer,
            trace_reader=NarrativeTraceReader(trace_log_path=trace_path),
            agent_id=agent_id,
            identity_firewall=self.firewall,
            store_dir=self.data_dir / "elevation",
        )

    # ── 事件入口 ──────────────────────────────────────

    def receive_social(self, event: SocialWorldEvent) -> None:
        """Lounge 广播事件进入 inbox（防重复投递: novelty_id 去重）。"""
        for existing in self.inbox:
            if existing.novelty_id == event.novelty_id:
                return
        self.inbox.append(event)

    # ── 心跳（每轮: 感知 → 渲染 → 决策）──────────────

    def heartbeat_tick(self, cycle: int, now_epoch: Optional[float] = None) -> HarnessDecision:
        """
        一轮心跳：drain inbox → 聚合感知（他者行为标注）→ 渲染 world_context
        （反框架语在场）→ 决策（防线 1: 绝不 AGENT_SPEAK / 回环广播）。

        感知路径**不写入任何记忆**（防线 3: 他者事件只作背景感知）。
        """
        self.heartbeat_count += 1
        now_epoch = now_epoch if now_epoch is not None else time.time()

        perceived = bool(self.inbox)
        while self.inbox:
            event = self.inbox.popleft()
            # 他者行为 → 聚合器（present_actors / recent_topics / 机会）:
            # 只作背景感知标注, 0 记忆写入（防线 3 不变量 1）
            self.aggregator.update_from_event(event, now_epoch)

        social_block = ""
        if perceived:
            state = self.aggregator.get_compact_state(self.agent_id, now_epoch)
            social_block = self.aggregator.render_compact_prompt_block(
                self.agent_id, state
            )
        self.world_contexts.append({
            "cycle": cycle,
            "perceived": perceived,
            "social_block": social_block,
            "anti_framing_present": ANTI_FRAMING_HINT in social_block,
        })

        decision = self.decision_engine.decide(
            cycle=cycle,
            perceived=perceived,
            social_block=social_block,
            now_epoch=now_epoch,
        )
        return decision

    # ── 防线 3 断言 helper（底层存储硬断言）──────────

    def count_facts_mentioning(self, needle: str) -> int:
        """SAGE facts 表（sqlite）中 subject/predicate/object 含 needle 的事实数。

        这是对**底层存储结构**的硬断言（映射 spec `episodic_memories`）。
        """
        return len(self.graph_store.search_by_entity(needle, min_weight=0.0))

    def total_facts(self) -> int:
        """当前 facts 表总笔数（含全部权重；用于增量对比）。"""
        return len(self.graph_store.get_all_facts(min_weight=0.0))

    def elevation_nodes_path(self) -> Path:
        """soul-elevation 升华节点存储（映射 spec `traits`/`beliefs`）。"""
        return self.data_dir / "elevation" / "elevation_nodes.jsonl"

    def elevation_node_count(self) -> int:
        """当前升华节点数（elevation_nodes.jsonl 行数; 文件不存在 = 0）。"""
        p = self.elevation_nodes_path()
        if not p.exists():
            return 0
        return sum(1 for line in p.read_text(encoding="utf-8").splitlines() if line.strip())

    # ── 生命周期 ──────────────────────────────────────

    def close(self) -> None:
        self.graph_store.close()


# ─────────────────────────────────────────────────────────────
# 事件工厂（生产链路真实入口: validate_social_world_event）
# ─────────────────────────────────────────────────────────────


def make_social_event(
    *,
    actor_id: str,
    space_id: str = SPACE_LOUNGE,
    visibility: str = VISIBILITY_PUBLIC,
    event_type: str = "greeting",
    content: str = "向大家打了招呼",
    summary: str = "他者在客厅向大家打了招呼",
    ts: Optional[str] = None,
    novelty_id: Optional[str] = None,
    priority: int = 0,
) -> SocialWorldEvent:
    """
    构造一个合法 SocialWorldEvent（经 validate_social_world_event 薄验证）。

    走生产验证入口，确保所有 harness 事件与真实广播链路同构。
    """
    ts = ts or datetime.now(timezone.utc).isoformat()
    novelty_id = novelty_id or f"si2_{uuid.uuid4().hex[:12]}"
    payload: Dict[str, Any] = {
        "actor_id": actor_id,
        "space_id": space_id,
        "visibility": visibility,
        "event_type": event_type,
        "content": content,
        "novelty_id": novelty_id,
        "ts": ts,
        "summary": summary,
        "data": {},
        "priority": priority,
    }
    try:
        return validate_social_world_event(payload)
    except SocialWorldEventValidationError as exc:  # 测试写错脚本时报错清晰
        raise AssertionError(f"make_social_event payload 非法: {exc}") from exc


def make_agent(
    agent_id: str,
    data_dir: Path,
    bus: MockSocialSpaceBus,
) -> HarnessSoulAgent:
    """构造并注册一个 Agent 到 bus（每个 Agent 独立隔离存储）。"""
    agent = HarnessSoulAgent(agent_id=agent_id, data_dir=data_dir, bus=bus)
    bus.register(agent)
    return agent


def deploy_three_agents(bus: MockSocialSpaceBus, tmp_path: Path) -> Dict[str, HarnessSoulAgent]:
    """
    部署三体阵容（aria / luna / sol），每个独立隔离目录。

    目录结构: tmp_path / agents / <agent_id> / （sage.db / inner_life / elevation）
    """
    agents: Dict[str, HarnessSoulAgent] = {}
    for agent_id in AGENT_IDS:
        agents[agent_id] = make_agent(
            agent_id,
            tmp_path / "agents" / agent_id,
            bus,
        )
    return agents


# ─────────────────────────────────────────────────────────────
# 剧本 D: TS-MCP 唯读工具 5s 超时执行器（不依赖 ToolRegistry, 不写任何 store）
# ─────────────────────────────────────────────────────────────


async def call_mcp_readonly(
    client: Any,
    tool: str,
    args: Dict[str, Any],
    timeout: float = MCP_CALL_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    MCP 唯读工具调用执行器（剧本 D 基建协同）:

    - 单次执行: 每次调用只发出一个 tools/call
    - 5s 超时契约（工单锁定, 常量 MCP_CALL_TIMEOUT_SECONDS == 5.0）:
      asyncio.wait_for 包裹, 超时 → 降级结果（不 raise, 不阻塞基建协同）
    - 与 DB 零接触: 绝不触碰任何 GraphStore/sqlite 连接（无锁冲突来源）

    Returns:
        {"ok": True, "data": ...} 或 {"ok": False, "degraded": True, "error": ...}
    """
    try:
        data = await asyncio.wait_for(
            client.call_tool(tool, args),
            timeout=timeout,
        )
        return {"ok": True, "data": data}
    except asyncio.TimeoutError:
        return {"ok": False, "degraded": True, "error": f"timeout>{timeout}s"}
    except Exception as exc:  # noqa: BLE001 — 外部工具失败 → 降级, 不阻塞协同
        return {"ok": False, "degraded": True, "error": f"{type(exc).__name__}: {exc}"}


__all__ = [
    "AGENT_ARIA",
    "AGENT_LUNA",
    "AGENT_SOL",
    "AGENT_IDS",
    "BRYAN_ID",
    "MCP_CALL_TIMEOUT_SECONDS",
    "OTHER_ACTOR_TRIGGER",
    "SM4_ACTIONS",
    "FORBIDDEN_ACTIONS",
    "MockSocialSpaceBus",
    "HarnessDecision",
    "HarnessDecisionEngine",
    "HarnessSoulAgent",
    "make_social_event",
    "make_agent",
    "deploy_three_agents",
    "call_mcp_readonly",
]