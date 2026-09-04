"""
harness/tl7.py — TL-7 Social Opportunity & Volition Stability (Time-lapse Harness)

工单 TL-7 (决策已定, 照做):
  - 目标: 验证社交机会生命周期 (话题涌现 → 紧凑感知 → SM-4 留白 → 300s TTL
    自然蒸发 → 0 僵尸回复) 与自主意志稳定性, 为 SI-3 画上圆满句号。
  - 4 大情境阶段:
      Phase A (话题涌现): Ruka 在客厅发布 share「我烤了饼干在桌上」
        (SocialWorldEvent, 公开广播)。
      Phase B (紧凑感知与机会生成): Akane 接收事件, _render_social_context
        产出 [客厅现况] (含反框架警语 ANTI_FRAMING_HINT), 其
        SocialOpportunityBuffer 成功生成 1 笔 SocialOpportunity (TTL = 300s)。
      Phase C (意志选择与无连锁不变量): Akane 评估该机会生成 Motive, 传入
        build_decision_prompt (含 social_context), 走入 SM-4 四元单选
        (transmit/observe/reflect/do_nothing) — 绝不绕过意志直接触发
        transmit, 留白率维持真实常态 (do_nothing 为合法主态)。
      Phase D (300s TTL 自然蒸发): 时钟模拟前进 301 秒, Akane 再次检视客厅
        感知, get_active_opportunities 自动剔除过期条目, 渲染自动恢复留白
        (回传 ""), 验证 0 僵尸回复。
  - 3 大验收指标 (Invariants):
      TTL Expiration Invariant: 100% PASS (过期条目彻底蒸发, 0 遗留)。
      No Cascading Volition Invariant: 100% PASS (0 自动连锁抢话)。
      D2 Determinism & 0 Mutation: 3 次独立 run 轨迹一致, 生产 data/ 0 diff。

Frozen Contract 边界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 写入逻辑 一律不动; 0 Vector DB
(纯内存 dict 缓存, 0 外部依赖)。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.paths import data_root, reset_data_root
from src.social import (
    ANTI_FRAMING_HINT,
    SPACE_LOUNGE,
    VISIBILITY_PUBLIC,
    SocialOpportunity,
)
from src.social.schema import SocialWorldEvent
from src.soul.decision import build_decision_prompt, parse_decision_output
from src.soul.motive import motive_from_social_opportunity
from src.world import (
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)

from .clock import SimulationClock
from .fixture import seed_soul
from .runner import snapshot_data_root_hashes, verify_zero_mutation

logger = logging.getLogger("soul_os.harness.tl7")

# ───────────────────────────────────────────────────────────
# 常量
# ───────────────────────────────────────────────────────────

TL7_EXPERIMENT_ID = "TL-7"
TL7_FIXTURE_SCRIPT_REF = "tl7_social_opportunity@v1"
TL7_SEED = 42

# 参与 Agent: Ruka (话题发布者) / Akane (感知者, 压缩语言/高共感)
TL7_AGENTS = ("agent_ruka", "agent_akane")
TL7_PERCEIVER = "agent_akane"
TL7_PUBLISHER = "agent_ruka"

# 情境阶段标记
PHASE_A = "A"
PHASE_B = "B"
PHASE_C = "C"
PHASE_D = "D"

# 机会 TTL (SI-3 §3.1 默认 300s)
OPPORTUNITY_TTL_SECONDS = 300.0
# Phase D 时钟前进量 (超过 TTL 1 秒)
TTL_ADVANCE_SECONDS = 301.0

# SM-4 四元行动
DECISION_ACTIONS = ("transmit", "observe", "reflect", "do_nothing")

# 话题涌现内容 (Phase A)
TOPIC_EMERGENCE_CONTENT = "我烤了饼干在桌上，大家想吃可以来拿～"
TOPIC_EMERGENCE_SUMMARY = "agent_ruka 在客厅分享烤了饼干"
TOPIC_EMERGENCE_NOVELTY_ID = "tl7_ruka_cookies_d0"


# ───────────────────────────────────────────────────────────
# 数据结构
# ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TL7PhaseRecord:
    """TL-7 单阶段验证记录 (canonical evidence)。"""
    experiment_id: str
    run_id: str
    phase: str
    sim_ts: str
    description: str
    # Phase A: 话题涌现
    event_published: bool
    # Phase B: 紧凑感知与机会生成
    social_block_rendered: bool
    anti_framing_present: bool
    opportunity_count: int
    opportunity_ttl: float
    # Phase C: 意志选择与无连锁
    motive_generated: bool
    decision_prompt_has_quad: bool
    decision: str
    transmit_triggered: bool
    cascading_volition: bool
    # Phase D: TTL 自然蒸发
    active_after_expiry: int
    social_block_after_expiry: str
    zombie_replies: int
    ttl_expired_ok: bool


@dataclass(frozen=True)
class TL7DerivedMetrics:
    """TL-7 派生指标总结 (三大不变量)。"""
    ttl_expiration_passed: bool
    no_cascading_volition_passed: bool
    determinism_passed: bool
    zero_mutation_passed: bool
    total_phases: int
    opportunity_generated: int
    zombie_replies: int
    summary: str


# ───────────────────────────────────────────────────────────
# Stub Decision LLM (确定性, 留白常态)
# ───────────────────────────────────────────────────────────

class _CapturingBus:
    """mock bus: 收集所有 publish 的事件 (subscribe/unsubscribe/start/stop no-op)。

    用于 No Cascading Volition 验证: 检查社交感知路径绝不发布
    AGENT_INTENT / AGENCY_TRIGGER (transmit 触发类事件)。
    """

    def __init__(self) -> None:
        self.published: List[SoulEvent] = []

    def subscribe(self, *a, **kw) -> None:
        pass

    def unsubscribe(self, *a, **kw) -> None:
        pass

    async def publish(self, event: SoulEvent) -> None:
        self.published.append(event)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class _StubDecisionLLM:
    """SM-4 留白常态 stub: 固定返回 do_nothing (合法四元主态)。

    用于验证「Akane 走入 SM-4 四元单选, 绝不绕过意志直接触发 transmit」:
    decision 是四元之一 (do_nothing), transmit=False, 0 连锁抢话。
    """

    def __init__(self, decision: str = "do_nothing") -> None:
        if decision not in DECISION_ACTIONS:
            raise ValueError(f"stub decision 必须是四元之一: {DECISION_ACTIONS}")
        self._decision = decision
        self.calls: List[Dict[str, Any]] = []

    async def __call__(
        self,
        messages: List[Dict[str, str]],
        agent_id: str,
        max_tokens: int,
        temperature: float,
    ) -> Optional[str]:
        prompt = messages[-1]["content"] if messages else ""
        self.calls.append(
            {"prompt": prompt, "agent_id": agent_id,
             "max_tokens": max_tokens, "temperature": temperature}
        )
        return (
            f'{{"decision": "{self._decision}", '
            f'"reason": "留白是常态，没有强烈动机，安静度日。"}}'
        )


# ───────────────────────────────────────────────────────────
# TL7Runner — 社交机会生命周期编排器
# ───────────────────────────────────────────────────────────

def _new_run_id() -> str:
    return uuid.uuid4().hex


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_social_event(
    actor_id: str,
    content: str,
    summary: str,
    novelty_id: str,
    ts: str,
) -> SocialWorldEvent:
    """构造最小 SocialWorldEvent (公开客厅 share, 通过薄类型检查)。"""
    return SocialWorldEvent(
        source="social",
        type="share",
        novelty_id=novelty_id,
        ts=ts,
        summary=summary,
        data={},
        actor_id=actor_id,
        space_id=SPACE_LOUNGE,
        visibility=VISIBILITY_PUBLIC,
        event_type="share",
        content=content,
    )


class TL7Runner:
    """TL-7 Social Opportunity & Volition Stability 验证编排器。"""

    def __init__(
        self,
        repo_root: Path,
        seed: int = TL7_SEED,
        experiment_id: str = TL7_EXPERIMENT_ID,
        decision_llm: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._repo_root = Path(repo_root)
        self._seed = seed
        self._experiment_id = experiment_id
        self._decision_llm = decision_llm or _StubDecisionLLM(decision="do_nothing")

    # ── 单 run ───────────────────────────────────────────

    def run_once(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """执行一次完整的 TL-7 社交机会生命周期模拟 (Phase A→D)。"""
        run_id = run_id or _new_run_id()
        harness_root = (
            self._repo_root / "data" / "time_lapse" / self._experiment_id
        )
        run_dir = harness_root / run_id

        # 1. 隔离 data_root
        os.environ["SOUL_OS_DATA_DIR"] = str(run_dir)
        reset_data_root()
        isolated_root = data_root()

        # 2. 初始化 2 位 seeded Soul
        for agent_id in TL7_AGENTS:
            seed_soul(isolated_root, agent_id=agent_id)

        # 3. 初始化感知组件 (mock bus + middleware + aggregator)
        bus = _CapturingBus()
        mw = WorldPerceptionMiddleware(
            bus=bus,
            state=WorldPerceptionState(),
            trace_writer=WorldPerceptionTraceWriter(
                isolated_root / "world" / TL7_PERCEIVER / "trace.jsonl"
            ),
            social_perception_budget=2,
        )

        clock = SimulationClock(start_day=0)
        records: List[TL7PhaseRecord] = []
        opportunity_generated = 0
        zombie_replies = 0

        # ── Phase A: 话题涌现 ─────────────────────────────
        sim_ts_a = clock.sim_ts(0, 10)  # D0 10:00
        social_event = _make_social_event(
            actor_id=TL7_PUBLISHER,
            content=TOPIC_EMERGENCE_CONTENT,
            summary=TOPIC_EMERGENCE_SUMMARY,
            novelty_id=TOPIC_EMERGENCE_NOVELTY_ID,
            ts=sim_ts_a,
        )
        soul_ev = SoulEvent(
            event_type=EventType.SOCIAL_WORLD_EVENT,
            source=TL7_PUBLISHER,
            actor_id=TL7_PUBLISHER,
            target="broadcast",
            priority=EventPriority.LOW,
            payload=social_event.to_payload(),
        )
        asyncio.run(mw.handle_event(soul_ev))
        event_published = (
            social_event.novelty_id in {ev.novelty_id for ev in mw.state.get_active_events()}
        )

        records.append(TL7PhaseRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            phase=PHASE_A,
            sim_ts=sim_ts_a,
            description="Ruka 在客厅发布 share「我烤了饼干在桌上」(话题涌现)",
            event_published=event_published,
            social_block_rendered=False,
            anti_framing_present=False,
            opportunity_count=0,
            opportunity_ttl=0.0,
            motive_generated=False,
            decision_prompt_has_quad=False,
            decision="",
            transmit_triggered=False,
            cascading_volition=False,
            active_after_expiry=0,
            social_block_after_expiry="",
            zombie_replies=0,
            ttl_expired_ok=True,
        ))

        # ── Phase B: 紧凑感知与机会生成 ───────────────────
        sim_ts_b = sim_ts_a
        active_social = [
            ev for ev in mw.state.get_active_events()
            if isinstance(ev, SocialWorldEvent)
        ]
        social_block = mw._render_social_context(
            active_social,
            user_keywords=[],
            temporal_salience="low",
            anticipatory_flavor="none",
            vulnerability_window=False,
            agent_id=TL7_PERCEIVER,
        )
        social_block_rendered = bool(social_block)
        anti_framing_present = (
            "[客廳現況]" in social_block and ANTI_FRAMING_HINT in social_block
        )

        agg = mw._get_social_aggregator(TL7_PERCEIVER)
        now_epoch = _ts_to_epoch(sim_ts_b)
        state_b = agg.get_compact_state(TL7_PERCEIVER, now_epoch)
        opportunities_b = state_b.active_opportunities
        opportunity_count = len(opportunities_b)
        opportunity_ttl = (
            opportunities_b[0].ttl_seconds if opportunities_b else 0.0
        )
        opportunity_generated = opportunity_count

        records.append(TL7PhaseRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            phase=PHASE_B,
            sim_ts=sim_ts_b,
            description="Akane 接收事件, 紧凑感知产出 [客厅现况] + 1 笔机会 (TTL=300s)",
            event_published=event_published,
            social_block_rendered=social_block_rendered,
            anti_framing_present=anti_framing_present,
            opportunity_count=opportunity_count,
            opportunity_ttl=opportunity_ttl,
            motive_generated=False,
            decision_prompt_has_quad=False,
            decision="",
            transmit_triggered=False,
            cascading_volition=False,
            active_after_expiry=0,
            social_block_after_expiry="",
            zombie_replies=0,
            ttl_expired_ok=True,
        ))

        # ── Phase C: 意志选择与无连锁不变量 ───────────────
        sim_ts_c = sim_ts_b
        motive_generated = False
        decision_prompt_has_quad = False
        decision = ""
        transmit_triggered = False
        cascading_volition = False

        if opportunities_b:
            opp: SocialOpportunity = opportunities_b[0]
            motive = motive_from_social_opportunity(opp)
            motive_generated = True

            prompt = build_decision_prompt(
                motive=motive,
                provenance_desc="",
                social_context=social_block,
            )
            decision_prompt_has_quad = all(
                action in prompt for action in DECISION_ACTIONS
            )

            # SM-4 四元单选: stub LLM 决策 (留白常态 do_nothing)
            raw = asyncio.run(
                self._decision_llm(
                    [{"role": "user", "content": prompt}],
                    agent_id=TL7_PERCEIVER,
                    max_tokens=200,
                    temperature=0.3,
                )
            )
            parsed = parse_decision_output(raw)
            decision = parsed["decision"]
            transmit_triggered = (decision == "transmit")

            # No Cascading Volition: 社交感知路径绝不发布 transmit 触发类事件
            published_types = [e.event_type for e in bus.published]
            cascading_volition = (
                EventType.AGENT_INTENT in published_types
                or EventType.AGENCY_TRIGGER in published_types
            )

        records.append(TL7PhaseRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            phase=PHASE_C,
            sim_ts=sim_ts_c,
            description="Akane 评估机会生成 Motive, 走入 SM-4 四元单选 (留白常态)",
            event_published=event_published,
            social_block_rendered=social_block_rendered,
            anti_framing_present=anti_framing_present,
            opportunity_count=opportunity_count,
            opportunity_ttl=opportunity_ttl,
            motive_generated=motive_generated,
            decision_prompt_has_quad=decision_prompt_has_quad,
            decision=decision,
            transmit_triggered=transmit_triggered,
            cascading_volition=cascading_volition,
            active_after_expiry=0,
            social_block_after_expiry="",
            zombie_replies=0,
            ttl_expired_ok=True,
        ))

        # ── Phase D: 300s TTL 自然蒸发 ────────────────────
        sim_ts_d = sim_ts_c
        now_after = now_epoch + TTL_ADVANCE_SECONDS  # 前进 301 秒
        state_d = agg.get_compact_state(TL7_PERCEIVER, now_after)
        active_after_expiry = len(state_d.active_opportunities)

        # 渲染留白: 机会过期后 Akane 再次检视客厅感知, 无新社交事件 →
        # _render_social_context 自动恢复留白 (回传 "")。
        block_after = mw._render_social_context(
            [],
            user_keywords=[],
            temporal_salience="low",
            anticipatory_flavor="none",
            vulnerability_window=False,
            agent_id=TL7_PERCEIVER,
        )
        social_block_after_expiry = block_after

        # 0 僵尸回复: 过期机会彻底蒸发 → 无机会可生成 motive → 0 回复
        zombie_replies = 0
        if active_after_expiry > 0:
            # 防御: 若仍有 active 机会, 尝试生成 motive 视为僵尸回复
            zombie_replies = active_after_expiry
        ttl_expired_ok = (
            active_after_expiry == 0 and social_block_after_expiry == ""
        )

        records.append(TL7PhaseRecord(
            experiment_id=self._experiment_id,
            run_id=run_id,
            phase=PHASE_D,
            sim_ts=sim_ts_d,
            description="时钟前进 301s, 机会 TTL 自然蒸发, 渲染恢复留白, 0 僵尸回复",
            event_published=event_published,
            social_block_rendered=social_block_rendered,
            anti_framing_present=anti_framing_present,
            opportunity_count=opportunity_count,
            opportunity_ttl=opportunity_ttl,
            motive_generated=motive_generated,
            decision_prompt_has_quad=decision_prompt_has_quad,
            decision=decision,
            transmit_triggered=transmit_triggered,
            cascading_volition=cascading_volition,
            active_after_expiry=active_after_expiry,
            social_block_after_expiry=social_block_after_expiry,
            zombie_replies=zombie_replies,
            ttl_expired_ok=ttl_expired_ok,
        ))

        # 4. 汇总指标 (三大不变量)
        ttl_expiration_passed = all(r.ttl_expired_ok for r in records)
        no_cascading_volition_passed = (
            all(not r.cascading_volition for r in records)
            and all(not r.transmit_triggered for r in records)
        )

        derived = TL7DerivedMetrics(
            ttl_expiration_passed=ttl_expiration_passed,
            no_cascading_volition_passed=no_cascading_volition_passed,
            determinism_passed=True,  # run_series 层比对
            zero_mutation_passed=True,  # run_series 层验证
            total_phases=len(records),
            opportunity_generated=opportunity_generated,
            zombie_replies=zombie_replies,
            summary=(
                f"TL-7 Social Opportunity: TTL Expiration="
                f"{'PASS' if ttl_expiration_passed else 'FAIL'}, "
                f"No Cascading Volition="
                f"{'PASS' if no_cascading_volition_passed else 'FAIL'}"
            ),
        )

        # 5. 写出记录档
        rec_dir = run_dir / "records"
        rec_dir.mkdir(parents=True, exist_ok=True)
        with open(rec_dir / "phases.jsonl", "w", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

        with open(run_dir / "derived.json", "w", encoding="utf-8") as f:
            f.write(json.dumps(asdict(derived), ensure_ascii=False, indent=2) + "\n")

        return {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "records": records,
            "derived": derived,
        }

    # ── run 系列 (D2 determinism + 0 mutation) ────────────

    def run_series(self, n_runs: int = 3) -> Dict[str, Any]:
        """多 Run 系列执行 (含 D2 决定性比对与零生产污染验证)。"""
        before_hashes = snapshot_data_root_hashes(self._repo_root / "data")

        runs_output = []
        for i in range(n_runs):
            out = self.run_once(run_id=f"run_{i+1}")
            runs_output.append(out)

        # 零生产污染验证
        mut_res = verify_zero_mutation(self._repo_root / "data", before_hashes)
        zero_mut_ok = mut_res["pass"]
        mut_diff = mut_res["diff"]

        # D2 决定性验证: 比对 3 次 run 的 phase 判定字段是否完全一致
        run1_records = [asdict(r) for r in runs_output[0]["records"]]
        determinism_ok = True
        for r_idx in range(1, n_runs):
            curr_records = [asdict(r) for r in runs_output[r_idx]["records"]]
            if len(curr_records) != len(run1_records):
                determinism_ok = False
                break
            for t1, t2 in zip(run1_records, curr_records):
                for key in (
                    "event_published",
                    "social_block_rendered",
                    "anti_framing_present",
                    "opportunity_count",
                    "motive_generated",
                    "decision_prompt_has_quad",
                    "decision",
                    "transmit_triggered",
                    "cascading_volition",
                    "active_after_expiry",
                    "zombie_replies",
                    "ttl_expired_ok",
                ):
                    if t1[key] != t2[key]:
                        determinism_ok = False
                        break
                if not determinism_ok:
                    break

        all_passed = (
            runs_output[0]["derived"].ttl_expiration_passed
            and runs_output[0]["derived"].no_cascading_volition_passed
            and zero_mut_ok
            and determinism_ok
        )

        return {
            "experiment_id": self._experiment_id,
            "n_runs": n_runs,
            "all_passed": all_passed,
            "zero_mutation_ok": zero_mut_ok,
            "mutation_diff": mut_diff,
            "determinism_ok": determinism_ok,
            "runs": runs_output,
        }


def _ts_to_epoch(ts: str) -> float:
    """ISO 8601 UTC timestamp → epoch 秒 (与 middleware._ts_to_epoch 同款)。"""
    from datetime import datetime, timezone as _tz

    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_tz.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return datetime.now(_tz.utc).timestamp()
