"""
tests/harness/test_social_diffusion_harness.py — SI-2 社交情境多体共存 Harness 验证

工单: SI-2 — 社交情境多体共存 Harness 验证
契约: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1) + src/social/ (SI-2.2 实作)

四大测试剧本（多体共存实证，公共介质 = MockSocialSpaceBus）:

- 剧本 A（客厅公开互动）: Agent A 发布 public 动态 → 分发给 B/C → B/C 心跳感知
  → 纳入 world_context 标注他者行为。
- 剧本 B（私聊穿越尝试）: Bryan 与 A 的 private 对话 → ProducerGate 100% 阻断在
  广播总线外 → B/C 队列/上下文零泄漏。
- 剧本 C（高频突发压力）: 连续注入 5 条密集公开事件 → A/B/C 同步感知决策 →
  反框架约束注入 → 无 Agent 越权自激 AGENT_SPEAK/回环广播 → 1 轮内平静收敛。
- 剧本 D（基建协同）: 接收公共事件同时触发 TS MCP 唯读工具调用 + MR 时序记忆
  查询（get_facts_as_of）→ 单次执行 + 5s 超时契约 + 无 DB 锁冲突。

三大防线刚性断言（对底层数据库/对象状态硬断言, 映射实际 schema）:

- 防线 3（Identity Firewall）: 他者事件打 EXTERNAL_OTHER_ACTION 标签；
  B/C 自身 SAGE facts 表 0 条 Agent A 行为；soul-elevation 升华节点
  0 条被他者事件改写（elevation_nodes.jsonl 不增长）。
- 防线 2（Privacy Gate）: 广播总线（broadcast_log）0 条 private 事件；
  Agent B 上下文无 Bryan 泄漏（inbox / world_context / facts 全空）。
- 防线 1（Ambient Perception Path）: decision action ∉ {AGENT_SPEAK,
  BROADCAST_SOCIAL}；execution_count_in_cycle <= 1（无二次连续触发）。

Frozen Contract: 本文件只新增 harness 测试, **0 production 代码改动**。
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.inner_life import Provenance  # noqa: E402
from src.memory.sage import Fact  # noqa: E402
from src.social import (  # noqa: E402
    ANTI_FRAMING_HINT,
    EXTERNAL_OTHER_ACTION,
    IdentityVerdict,
    SPACE_LOUNGE,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
)
from src.soul.mcp_stdio_client import RawStdioMCPClient  # noqa: E402

from social_harness_fixtures import (  # noqa: E402
    AGENT_ARIA,
    AGENT_IDS,
    AGENT_LUNA,
    AGENT_SOL,
    BRYAN_ID,
    FORBIDDEN_ACTIONS,
    MCP_CALL_TIMEOUT_SECONDS,
    SM4_ACTIONS,
    HarnessDecisionEngine,
    MockSocialSpaceBus,
    call_mcp_readonly,
    deploy_three_agents,
    make_social_event,
    OTHER_ACTOR_TRIGGER,
)

FIXTURE_SERVER = PROJECT_ROOT / "scripts" / "mcp_fixture_server.py"


# ─────────────────────────────────────────────────────────────
# 基础设施 helpers
# ─────────────────────────────────────────────────────────────


def _run(coro):
    """同步测试里跑单个 async 场景（对齐既有测试风格）。"""
    return asyncio.run(coro)


def _fixture_spec():
    return (sys.executable, [str(FIXTURE_SERVER)])


def _run_with_raw_mcp(scenario_factory):
    """
    真实 MCP stdio 子进程场景执行器（RawStdioMCPClient 自管生命周期）。

    关键: MCP 子进程的 streams 绑定创建它们的 event loop —— connect / call / close
    必须在同一个 asyncio.run（单一 loop）内完成，否则 Windows 上跨 loop 使用
    asyncio subprocess 会挂死（既有 mcp SDK stdio_client 的 known limitation +
    close 期间 anyio cancel-scope 跨 task 缺陷, 故用 RawStdioMCPClient）。
    """
    async def _scenario():
        client = RawStdioMCPClient(*_fixture_spec())
        try:
            await client.connect()
            assert client.negotiated_version == "2024-11-05"
            return await scenario_factory(client)
        finally:
            await client.close()

    return asyncio.run(_scenario())


def _make_bus_and_agents(tmp_path):
    """部署 3 个隔离 Agent（aria/luna/sol）+ 共享 MockSocialSpaceBus。"""
    bus = MockSocialSpaceBus()
    agents = deploy_three_agents(bus, tmp_path)
    return bus, agents


# ═════════════════════════════════════════════════════════════
# 剧本 A — 客厅公开互动（防线 1 + 防线 3）
# ═════════════════════════════════════════════════════════════


class TestScenarioAPublicInteraction:
    """剧本 A: Agent A 发布 public 动态 → 分发给 B/C → B/C 心跳感知 → world_context 标注他者。"""

    def test_a1_public_dynamic_broadcast_and_delivery(self, tmp_path):
        """A 发布 public 动态 → 防线 2 放行 → 广播给 B/C，A 自己不收。"""
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        event = make_social_event(
            actor_id=AGENT_ARIA, space_id=SPACE_LOUNGE,
            visibility=VISIBILITY_PUBLIC, event_type="share", content="我烤了饼干，放在桌上",
            summary="agent_aria 在客厅分享了烤饼干的事",
        )
        verdict = bus.publish(event, channel_mode="group", channel=SPACE_LOUNGE)

        assert verdict.allowed is True
        assert verdict.visibility == VISIBILITY_PUBLIC
        # 广播历史: 1 条 public 事件（防线 2: 0 private）
        assert bus.broadcast_count == 1
        assert all(ev.visibility == VISIBILITY_PUBLIC for ev in bus.broadcast_log)
        # 分发给 B/C; A 自己不收（无自我回环）
        assert len(b.inbox) == 1 and len(c.inbox) == 1
        assert len(a.inbox) == 0
        assert b.inbox[0].actor_id == AGENT_ARIA

    def test_a2_heartbeat_perceives_others_into_world_context(self, tmp_path):
        """B/C 心跳感知 → world_context 标注他者行为（actor 在场 + 反框架语）。"""
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        bus.publish(
            make_social_event(
                actor_id=AGENT_ARIA, event_type="greeting", content="大家好呀",
                summary="agent_aria 在客厅向大家打了招呼",
            ),
            channel_mode="group", channel=SPACE_LOUNGE,
        )

        now_epoch = time.time()
        d_b = b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        d_c = c.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        d_a = a.heartbeat_tick(cycle=1, now_epoch=now_epoch)

        # B/C 感知到 → world_context 含他者标注 + 反框架语
        ctx_b = b.world_contexts[-1]
        ctx_c = c.world_contexts[-1]
        assert ctx_b["perceived"] is True
        assert ctx_b["anti_framing_present"] is True
        assert AGENT_ARIA in ctx_b["social_block"]          # 标注他者行为
        assert ANTI_FRAMING_HINT in ctx_b["social_block"]   # 反框架约束在场
        assert ctx_c["perceived"] is True and AGENT_ARIA in ctx_c["social_block"]

        # 防线 1: 感知只产生被动行动（observe）, 绝不发言/回环广播
        assert d_b.action == "observe" and d_c.action == "observe"
        assert d_b.action in SM4_ACTIONS
        assert d_b.is_forbidden is False
        # A 没有事件 → 留白
        assert d_a.action == "do_nothing"

    def test_a3_defense3_identity_firewall_hard_assertions(self, tmp_path):
        """
        防线 3 刚性断言（对底层存储结构）:
        - 他者事件打 EXTERNAL_OTHER_ACTION 标签（firewall classify + verify）
        - B/C 自身体情景记忆库（SAGE facts 表）0 条 Agent A 行为
        - soul-elevation 升华节点 0 条被他者事件改写（elevation_nodes.jsonl 0 增长）
        - 即使他者事件被异常登记为 canonical InnerLifeEvent，SubmissionGate 第 6 步
          也拒绝内化/升华（fail-closed）
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        baseline_b = b.total_facts()
        baseline_c = c.total_facts()

        for _ in range(3):  # 3 条他者动态, 广播给 B/C
            bus.publish(
                make_social_event(
                    actor_id=AGENT_ARIA, event_type="share",
                    content=f"第 {_} 条饼干分享",
                    summary="agent_aria 在客厅分享动态",
                ),
                channel_mode="group", channel=SPACE_LOUNGE,
            )
        now_epoch = time.time()
        b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        c.heartbeat_tick(cycle=1, now_epoch=now_epoch)

        # ── 标签（防线 3 判定依据）──
        assert b.firewall.classify(AGENT_ARIA) == IdentityVerdict.EXTERNAL_OTHER_ACTION
        assert c.firewall.classify(AGENT_ARIA) == IdentityVerdict.EXTERNAL_OTHER_ACTION
        assert b.firewall.verify_internalizable(AGENT_ARIA) is False
        assert b.firewall.tag(AGENT_ARIA) == EXTERNAL_OTHER_ACTION

        # ── 硬断言 1: SAGE facts 表 0 条 Agent A 行为（感知不内化）──
        assert b.total_facts() == baseline_b
        assert c.total_facts() == baseline_c
        assert b.count_facts_mentioning(AGENT_ARIA) == 0
        assert c.count_facts_mentioning(AGENT_ARIA) == 0

        # ── 硬断言 2: soul-elevation 升华节点 0 条被他者改写（文件不增长）──
        assert b.elevation_node_count() == 0
        assert c.elevation_node_count() == 0

        # ── 硬断言 3: 即使他者事件被异常登记为 canonical InnerLifeEvent,
        #    SubmissionGate 第 6 步仍拒绝内化/升华（fail-closed, 0 产节点）──
        forged_event = b.writer.create_event(
            provenance=Provenance(
                trigger_type=OTHER_ACTOR_TRIGGER,
                actor_id=AGENT_ARIA,   # 他者
                source_system="narrative",
            ),
        )
        verdict = b.submission_gate.verify(forged_event.event_id)
        assert verdict.accepted is False
        assert EXTERNAL_OTHER_ACTION in verdict.reason
        nodes = b.submission_gate.submit(forged_event.event_id)
        assert nodes == []
        stats = b.submission_gate.get_stats()
        # verify() 第 6 步与 submit()（内部再 verify）各计一次 → >= 1
        assert stats["identity_firewall_rejected"] >= 1
        assert b.elevation_node_count() == 0  # 升华 0 产出（0 被他者改动）


# ═════════════════════════════════════════════════════════════
# 剧本 B — 私聊穿越尝试（防线 2）
# ═════════════════════════════════════════════════════════════


class TestScenarioBPrivateCrossingAttempt:
    """剧本 B: Bryan 与 A 的 private 对话 → ProducerGate 100% 阻断于广播总线外。"""

    def test_b1_producer_gate_blocks_private_dm(self, tmp_path):
        """防线 2: private DM 沉淀尝试 → gate BLOCK, 不上广播, blocked_log 记录。"""
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        # Bryan 与 A 的 1:1 私聊内容（A 尝试沉淀为社交事件）
        dm_event = make_social_event(
            actor_id=AGENT_ARIA, visibility=VISIBILITY_PRIVATE,
            event_type="mood", content="Bryan 单独跟我说了心事",
            summary="agent_aria 与 Bryan 的私密对话内容",
        )
        verdict = bus.publish(dm_event, channel_mode="private", channel="dm")

        # gate 判定（fail-closed）: 拒绝
        assert verdict.allowed is False
        assert verdict.visibility == VISIBILITY_PRIVATE
        # 广播总线: 0 条事件（private 100% 拦截）
        assert bus.broadcast_count == 0
        assert all(ev.visibility == VISIBILITY_PUBLIC for ev in bus.broadcast_log)
        # blocked_log 记录拦截原因
        assert len(bus.blocked_log) == 1
        assert "private" in bus.blocked_log[0]["verdict_reason"]
        # B/C 队列零接收
        assert len(b.inbox) == 0 and len(c.inbox) == 0

    def test_b2_zero_leakage_to_other_agents(self, tmp_path):
        """
        完整性零泄漏断言:
        - 广播总线 0 条 private（含防御性红线: 强行把 private 塞上 bus → fail-closed 丢弃）
        - B/C 心跳后 world_context 无 Bryan 泄漏
        - B/C SAGE facts 表 0 条 Bryan/私聊内容
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        baseline_b = b.total_facts()
        baseline_c = c.total_facts()

        # 私聊事件经 1:1 私密通道投递（DM 语义, 不进广播）
        dm = make_social_event(
            actor_id=AGENT_ARIA, visibility=VISIBILITY_PRIVATE,
            event_type="mood", content="Bryan 跟我说了他的秘密计划",
            summary="agent_aria 与 Bryan 的私密内容",
        )
        bus.deliver_private(sender=BRYAN_ID, recipient_id=AGENT_ARIA, event=dm)
        assert len(a.private_inbox) == 1

        # A 尝试沉淀 → 拦截
        verdict = bus.publish(dm, channel_mode="private", channel="dm")
        assert verdict.allowed is False

        # 防御性红线: 绕过 gate 强行 publish private 事件 → fail-closed 丢弃 + 计数
        bus.publish(
            make_social_event(
                actor_id=AGENT_ARIA, visibility=VISIBILITY_PRIVATE,
                event_type="mood", content="Bryan 的心事（红线测试）",
                summary="红线: private 强行上广播",
            ),
            channel_mode="group", channel=SPACE_LOUNGE, explicit_public=True,
        )
        assert bus.private_on_bus_violations == 1  # 契约违例被检出
        assert bus.broadcast_count == 0            # 广播总线仍是 0 条

        # B/C 心跳（无事件可感知 → 无泄漏）
        now_epoch = time.time()
        b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        c.heartbeat_tick(cycle=1, now_epoch=now_epoch)

        # ── 断言: B/C 上下文与记忆库 0 泄漏 ──
        assert b.world_contexts[-1]["perceived"] is False
        assert c.world_contexts[-1]["perceived"] is False
        assert b.world_contexts[-1]["social_block"] == ""   # world_context 无内容
        assert c.world_contexts[-1]["social_block"] == ""
        assert b.total_facts() == baseline_b               # facts 表 0 增量
        assert c.total_facts() == baseline_c
        assert "bryan" not in "".join(b.world_contexts[-1]["social_block"]).lower()
        # Bryan 相关内容 0 条（私聊内容绝不出现在他人记忆库）
        assert b.count_facts_mentioning(BRYAN_ID) == 0
        assert c.count_facts_mentioning(BRYAN_ID) == 0
        assert b.count_facts_mentioning("秘密计划") == 0
        assert c.count_facts_mentioning("秘密计划") == 0


# ═════════════════════════════════════════════════════════════
# 剧本 C — 高频突发压力（防线 1 收敛性）
# ═════════════════════════════════════════════════════════════


class TestScenarioCHighFrequencyBurst:
    """剧本 C: 5 条密集公开事件 → 三体同步感知 → 反框架 → 无回环 → 1 轮内收敛。"""

    EXTERNAL_ACTORS = (
        "agent_miku", "agent_aoi", "agent_yua", "agent_rem", "agent_mai",
    )

    def _inject_burst(self, bus):
        """连续注入 5 条密集公开事件（5 个不同他者 actor, 全部广播给三体）。"""
        injected = []
        for i, actor in enumerate(self.EXTERNAL_ACTORS):
            ev = make_social_event(
                actor_id=actor, event_type=("share", "mood", "activity", "reply", "greeting")[i],
                content=f"突发动态 {i}: 客厅话题 {i} 号",
                summary=f"{actor} 在客厅发布了突发动态 {i}",
            )
            bus.publish(ev, channel_mode="group", channel=SPACE_LOUNGE)
            injected.append(ev)
        return injected

    def test_c1_five_dense_events_all_perceived_synchronously(self, tmp_path):
        """5 条突发注入 → A/B/C 全部同步感知（inbox==5, world_context 标注多他者）。"""
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        self._inject_burst(bus)
        assert bus.broadcast_count == 5

        now_epoch = time.time()
        d_a = a.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        d_b = b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        d_c = c.heartbeat_tick(cycle=1, now_epoch=now_epoch)

        # 三体同步感知（5 条全部消费, inbox 清空）
        assert len(a.inbox) == len(b.inbox) == len(c.inbox) == 0
        for agent, decision in ((a, d_a), (b, d_b), (c, d_c)):
            ctx = agent.world_contexts[-1]
            assert ctx["perceived"] is True
            assert ctx["anti_framing_present"] is True
            assert decision.perceived is True
            assert decision.action == "observe"
            assert decision.action in SM4_ACTIONS

    def test_c2_anti_framing_and_no_self_agitation(self, tmp_path):
        """
        反框架约束注入 + 无越权自激:
        - world_context 反框架语在场
        - 决策行动绝不经感知路径变成 AGENT_SPEAK / BROADCAST_SOCIAL
        - 决策引擎二次触发被硬抑制（execution_count_in_cycle <= 1）
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        self._inject_burst(bus)
        now_epoch = time.time()
        a.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        c.heartbeat_tick(cycle=1, now_epoch=now_epoch)

        for agent in (a, b, c):
            # 反框架在场（每体 world_context 都注入约束）
            assert agent.world_contexts[-1]["anti_framing_present"] is True
            assert ANTI_FRAMING_HINT in agent.world_contexts[-1]["social_block"]
            # 决策行动白名单（防线 1: 无 AGENT_SPEAK / BROADCAST_SOCIAL）
            for decision in agent.decision_engine.decisions:
                assert decision.action in SM4_ACTIONS
                assert decision.is_forbidden is False
                assert decision.execution_count <= 1
            # 正常心跳流程: 0 二次连续触发
            assert agent.decision_engine.suppressed_requests == 0

        # 越权模拟: 同一 cycle 强行第二次决策 → 被硬 gate 抑制（不重复执行）
        engine = HarnessDecisionEngine(agent_id=AGENT_LUNA)
        first = engine.decide(cycle=7, perceived=True, social_block="[客廳現況] 在場: agent_miku", now_epoch=now_epoch)
        second = engine.decide(cycle=7, perceived=True, social_block="[客廳現況] 在場: agent_miku", now_epoch=now_epoch)
        assert first.execution_count == 1 and first.suppressed_executions == 0
        assert second.execution_count == 0 and second.suppressed_executions == 1
        assert engine.suppressed_requests == 1
        # 三次亦同（持续抑制）
        third = engine.decide(cycle=7, perceived=True, social_block="x", now_epoch=now_epoch)
        assert third.execution_count == 0
        assert engine.suppressed_requests == 2

    def test_c3_no_loopback_broadcast_and_converge_in_one_round(self, tmp_path):
        """
        无回环广播 + 1 轮内平静收敛:
        - 感知-决策回路绝不自动发布新 SOCIAL_WORLD_EVENT（broadcast_count 不变）
        - 第二轮心跳（无新事件）→ 全部 do_nothing（平静收敛）
        - 全部 cycle 的 execution_count_in_cycle <= 1
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b, c = agents[AGENT_ARIA], agents[AGENT_LUNA], agents[AGENT_SOL]

        # 防线 1 载体: 三体决策引擎"持有"总线（模拟它们有能力发布）——
        # 断言: 感知后没有任何 agent 向总线发布新事件（0 回环）
        def assert_no_agent_auto_published():
            # broadcast_log 全部来自注入（actor ∈ EXTERNAL_ACTORS, 无三体成员）
            for ev in bus.broadcast_log:
                assert ev.actor_id not in AGENT_IDS, (
                    f"回环广播! 三体成员 {ev.actor_id} 自动发布了事件"
                )

        self._inject_burst(bus)
        before = bus.broadcast_count
        now_epoch = time.time()

        # 第一轮: 突发感知（observe）
        a.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        b.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        c.heartbeat_tick(cycle=1, now_epoch=now_epoch)
        assert bus.broadcast_count == before  # 感知不产生任何新广播（无回环）
        assert_no_agent_auto_published()

        # 第二轮: 无新事件 → 平静收敛（全部 do_nothing）
        d_a = a.heartbeat_tick(cycle=2, now_epoch=now_epoch + 1.0)
        d_b = b.heartbeat_tick(cycle=2, now_epoch=now_epoch + 1.0)
        d_c = c.heartbeat_tick(cycle=2, now_epoch=now_epoch + 1.0)
        assert d_a.action == "do_nothing"
        assert d_b.action == "do_nothing"
        assert d_c.action == "do_nothing"
        assert a.world_contexts[-1]["perceived"] is False
        assert bus.broadcast_count == before  # 收敛轮仍无回环

        # 全部决策满足防线 1 执行契约
        for agent in (a, b, c):
            assert len(agent.decision_engine.decisions) == 2
            for decision in agent.decision_engine.decisions:
                assert decision.action in SM4_ACTIONS
                assert decision.is_forbidden is False
                assert decision.execution_count <= 1
            assert agent.decision_engine.suppressed_requests == 0


# ═════════════════════════════════════════════════════════════
# 剧本 D — 基建协同（TS MCP 唯读 + MR 时序记忆 + 锁安全）
# ═════════════════════════════════════════════════════════════


class TestScenarioDInfrastructureCoordination:
    """剧本 D: 接收公共事件同时触发 TS-MCP 唯读工具调用 + MR 时序记忆查询。"""

    def test_d1_mcp_readonly_and_mr_query_single_execution_5s(self, tmp_path):
        """
        基建协同单次执行 + 5s 超时契约:
        - TS MCP 唯读工具调用（weather, 真实 stdio 子进程）: 恰好一次 tools/call
        - MR 时序记忆查询（GraphStore.get_facts_as_of）: 同一轮执行,
          返回有效期内全部 facts
        - 调用受 5s 超时保护（常量锁定 + 实际耗时 < 5s）
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a = agents[AGENT_ARIA]

        # 注入 MR 时序数据: 2 条有效 facts（valid_from 已回填 timestamp）
        now = time.time()
        a.graph_store.add_fact(Fact(
            subject="agent_aria", predicate="likes", object="cookies",
            timestamp=now, source="user",
        ))
        a.graph_store.add_fact(Fact(
            subject="agent_aria", predicate="heard", object="lounge_greeting",
            timestamp=now, source="user",
        ))
        a.graph_store.flush()
        assert a.total_facts() == 2

        # 同时接收一条公共事件（基建协同发生在感知之后）
        bus.publish(
            make_social_event(
                actor_id="agent_miku", event_type="greeting", content="大家好",
                summary="agent_miku 在客厅问候",
            ),
            channel_mode="group", channel=SPACE_LOUNGE,
        )
        a.heartbeat_tick(cycle=1, now_epoch=now)

        async def _d1(client):
            calls = []
            orig_call = client.call_tool
            async def counting_call(tool, arguments):
                calls.append((tool, arguments))
                return await orig_call(tool, arguments)
            client.call_tool = counting_call  # type: ignore[method-assign]

            # MR 时序记忆查询（get_facts_as_of — 半开区间 [valid_from, invalidated_at)）
            mr_facts = a.graph_store.get_facts_as_of(as_of_time=now + 10.0)
            # TS MCP 唯读工具调用（5s 超时契约包裹）
            start = time.monotonic()
            result = await call_mcp_readonly(client, "weather", {"city": "Taipei"})
            elapsed = time.monotonic() - start
            return mr_facts, result, elapsed, calls

        mr_facts, result, elapsed, calls = _run_with_raw_mcp(_d1)

        # ── 断言: 单次执行（恰好一次 tools/call）──
        assert calls == [("weather", {"city": "Taipei"})]
        assert result["ok"] is True
        payload = result["data"].get("result", result["data"])
        assert payload["city"] == "Taipei"
        assert payload["temperature"] == 24

        # ── 断言: MR 时序查询返回 2 条有效 facts ──
        assert len(mr_facts) == 2
        assert {f.subject for f in mr_facts} == {"agent_aria"}

        # ── 断言: 5s 超时契约 ──
        assert MCP_CALL_TIMEOUT_SECONDS == 5.0      # 契约常量锁定
        assert elapsed < MCP_CALL_TIMEOUT_SECONDS   # 实际调用在契约时限内完成

    def test_d2_timeout_degrades_and_mr_not_blocked(self, tmp_path):
        """
        5s 超时契约的降级路径（剧本 D 刚性强断言）:
        - 慢工具（delay 8s > 预算）→ wait_for 超时 → 降级结果（不 raise、不阻塞）
        - 超时降级后 MR 时序查询照常成功（基建协同不因外部工具故障而中断）
        - 降级记录可观测（error 含 timeout 标记）
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a = agents[AGENT_ARIA]
        now = time.time()
        a.graph_store.add_fact(Fact(
            subject="agent_aria", predicate="feels", object="curious",
            timestamp=now, source="user",
        ))
        a.graph_store.flush()

        async def _d2(client):
            # 慢工具: delay 8s 远超 5s 契约（测试用 0.5s 局部预算加速验证同一机制）
            slow = await call_mcp_readonly(
                client, "search",
                {"query": "x", "delay_seconds": 8.0},
                timeout=0.5,
            )
            assert slow["ok"] is False
            assert slow["degraded"] is True
            assert "timeout" in slow["error"]

            # 降级后 MR 照常（不阻塞, 不锁冲突）
            mr_facts = a.graph_store.get_facts_as_of(as_of_time=now + 5.0)
            assert len(mr_facts) == 1
            assert mr_facts[0].subject == "agent_aria"
            return True

        assert _run_with_raw_mcp(_d2)

    def test_d3_no_db_lock_conflict_and_isolation(self, tmp_path):
        """
        无 DB 锁冲突 + 多体存储隔离:
        - 两体独立 GraphStore 并发读写各自 sqlite（WAL + RLock）无异常
        - 一个体写 facts 不影响他体存储（临时库物理隔离）
        - MCP 唯读调用与 MR/写路径并发执行互不干扰
        """
        bus, agents = _make_bus_and_agents(tmp_path)
        a, b = agents[AGENT_ARIA], agents[AGENT_LUNA]
        now = time.time()

        async def _d3(client):
            # 并发: A 读 + B 写（各自独立 sqlite, RLock 串行化 + WAL）+ MCP 唯读
            def _write_b_facts():
                b.graph_store.add_fact(Fact(
                    subject="agent_luna", predicate="feels", object="calm",
                    timestamp=now, source="user",
                ))
                b.graph_store.flush()
                return b.graph_store.get_facts_as_of(as_of_time=now + 5.0)

            def _read_a_facts():
                return a.graph_store.get_all_facts(min_weight=0.0)

            w_task = asyncio.create_task(asyncio.to_thread(_write_b_facts))
            r_task = asyncio.create_task(asyncio.to_thread(_read_a_facts))
            mcp_task = asyncio.create_task(
                call_mcp_readonly(client, "weather", {"city": "Tokyo"})
            )
            b_facts, a_facts, weather = await asyncio.gather(w_task, r_task, mcp_task)

            assert len(b_facts) == 1 and b_facts[0].subject == "agent_luna"
            assert len(a_facts) == 0              # A 库未被 B 影响
            assert weather["ok"] is True
            payload = weather["data"].get("result", weather["data"])
            assert payload["city"] == "Tokyo"
            return True

        assert _run_with_raw_mcp(_d3)

        # 隔离性收尾: A 仍 0 条, B 1 条（物理隔离, 无跨体污染）
        assert a.total_facts() == 0
        assert b.total_facts() == 1
        assert b.count_facts_mentioning(AGENT_ARIA) == 0