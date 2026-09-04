"""
tests/test_si3_phase2_integration.py — SI-3 Phase 2 集成測試

工单: TICKET-SI-3-PHASE-2 (Dual-Brain Edition)
设计: docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md (v1.0, 2026-09-03, 已鎖定)

覆蓋 (工單驗收 4 項):
  1. test_middleware_compact_social_state_rendering: WorldPerceptionMiddleware
     處理 SocialWorldEvent 後, social_block 是 Compact Social State 格式,
     含反框架提示語, 估算 token <= 150。
  2. test_middleware_social_opportunity_ttl_expiry_rendering: 機會過期且客廳
     無他人時, social_block 自動返回 "" (留白)。
  3. test_social_opportunity_to_motive_decision_prompt: motive_from_social_opportunity
     產出的 Motive 傳入 build_decision_prompt, Prompt 含四元決策選項
     (transmit/observe/reflect/do_nothing) 與客廳背景情境。
  4. test_zero_cascading_volition_invariant: 外部社交事件輸入後, 絕不修改
     agent 自傳檔案或記憶庫, 僅存於記憶體緩存 (No Cascading Volition)。

Frozen Contract 邊界 (0 change): Agency 4 stages / TriggerEnvelope /
InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 一律不動。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.eventbus.schema import EventType, SoulEvent
from src.social import (
    ANTI_FRAMING_HINT,
    CompactSocialState,
    SocialOpportunity,
    SocialPerceptionAggregator,
)
from src.social.schema import SocialWorldEvent
from src.soul.decision import build_decision_prompt
from src.soul.motive import motive_from_social_opportunity
from src.world.middleware import WorldPerceptionMiddleware
from src.world.state import WorldPerceptionState


# ─────────────────────────────────────────────────────────────
# 測試輔助
# ─────────────────────────────────────────────────────────────

def _make_event(
    novelty_id: str,
    actor_id: str,
    content: str,
    space_id: str = "lounge",
    summary: str = "",
    ts: str = "2026-09-03T00:00:00Z",
    data: Optional[dict] = None,
) -> SocialWorldEvent:
    """最小 SocialWorldEvent 工廠 (通過薄型別檢查即可, 不走完整 validation)。"""
    return SocialWorldEvent(
        source="social",
        type="share",
        novelty_id=novelty_id,
        ts=ts,
        summary=summary or content,
        data=data or {},
        actor_id=actor_id,
        space_id=space_id,
        visibility="public",
        event_type="share",
        content=content,
    )


def _estimate_tokens(text: str) -> int:
    """保守 token 估算: 非 ASCII (中文) 每字 2 token, ASCII 每字 1 token。"""
    return sum(2 if ord(c) > 127 else 1 for c in text)


class _CapturingBus:
    """mock bus: 收集所有 publish 的事件 (subscribe/unsubscribe/start/stop no-op)。"""

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


class _CollectingWriter:
    """mock trace writer: 收集所有寫入的 WorldPerceptionTrace (不落盤)。"""

    def __init__(self) -> None:
        self.traces: List[Any] = []

    def write(self, trace: Any) -> bool:
        self.traces.append(trace)
        return True

    def clear(self) -> None:
        self.traces.clear()


def _make_middleware(bus: Optional[_CapturingBus] = None) -> WorldPerceptionMiddleware:
    """構造隔離的 WorldPerceptionMiddleware (mock bus + 收集 trace)。"""
    return WorldPerceptionMiddleware(
        bus=bus or _CapturingBus(),
        state=WorldPerceptionState(),
        trace_writer=_CollectingWriter(),
    )


def _make_enriched(agent_id: str = "agent_bryan") -> SoulEvent:
    """最小 AGENT_INTENT_ENRICHED (跟既有 middleware 測試同款 payload)。"""
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source="test",
        target="broadcast",
        payload={"agent_id": agent_id, "draft": "", "text": "", "chrono_context": ""},
    )


# ─────────────────────────────────────────────────────────────
# 1. Middleware 緊湊社交狀態渲染 (Compact Social State, <=150 tokens)
# ─────────────────────────────────────────────────────────────

def test_middleware_compact_social_state_rendering():
    """WorldPerceptionMiddleware 處理 SocialWorldEvent 後, social_block 是
    Compact Social State 格式 (含反框架提示語), 估算 token <= 150。"""
    bus = _CapturingBus()
    mw = _make_middleware(bus)

    social_event = _make_event(
        "evt_1", "agent_ruka", "烤了餅乾", summary="瑠夏烤了餅乾"
    )
    mw.state.add(social_event)
    asyncio.run(mw._on_agent_intent_enriched(_make_enriched()))

    # 捕獲 AGENT_INTENT_PERCEIVED 的 world_context (social_block 拼入其中)
    perceived = [
        e for e in bus.published if e.event_type == EventType.AGENT_INTENT_PERCEIVED
    ]
    assert len(perceived) == 1
    world_context = perceived[0].payload["world_context"]

    # Compact Social State 格式: [客廳現況] 區塊 + 反框架提示語
    assert "[客廳現況]" in world_context
    assert ANTI_FRAMING_HINT in world_context
    assert "agent_ruka" in world_context
    assert "烤了餅乾" in world_context

    # Token 預算受控 (<=150)
    assert _estimate_tokens(world_context) <= 150

    # trace 仍寫入 (observability 保留)
    assert len(mw.trace_writer.traces) >= 1


# ─────────────────────────────────────────────────────────────
# 2. 機會過期 + 客廳無他人 → 留白 ""
# ─────────────────────────────────────────────────────────────

def test_middleware_social_opportunity_ttl_expiry_rendering():
    """機會過期 (TTL 300s) 且客廳無他人時, social_block 自動返回 "" (留白)。"""
    # aggregator 層面: 機會過期後被修剪, active_opportunities 為空
    agg = SocialPerceptionAggregator(current_agent_id="agent_bryan")
    now = 1000.0
    agg.update_from_event(_make_event("evt_1", "agent_ruka", "烤了餅乾"), now)
    state = agg.get_compact_state("agent_bryan", now + 301.0)
    assert state.active_opportunities == []  # TTL 過期修剪 (fail-closed)

    # 機會過期 + 客廳無他人 (只有自己, 無話題, 無有效機會) → render 留白 ""
    expired_state = CompactSocialState(
        present_actors=["agent_bryan"],  # 只有自己 → 無他人
        recent_topics=[],                 # 無活躍話題
        active_opportunities=[],          # 機會已過期被修剪
    )
    assert agg.render_compact_prompt_block("agent_bryan", expired_state) == ""

    # middleware 層面: 客廳無他人動態 (無 social_events) → social_block 留白 ""
    mw = _make_middleware()
    block = mw._render_social_context(
        [],
        user_keywords=[],
        temporal_salience="low",
        anticipatory_flavor="none",
        vulnerability_window=False,
        agent_id="agent_bryan",
    )
    assert block == ""


# ─────────────────────────────────────────────────────────────
# 3. 社交機會 → Motive → Decision Prompt (四元 + 客廳背景)
# ─────────────────────────────────────────────────────────────

def test_social_opportunity_to_motive_decision_prompt():
    """motive_from_social_opportunity 產出的 Motive 傳入 build_decision_prompt,
    Prompt 含四元決策選項與客廳背景情境 (social_context 只進 Relevant context)。"""
    opp = SocialOpportunity(
        opportunity_id="opp_abc123",
        source_event_id="evt_1",
        actor_id="agent_ruka",
        space_id="lounge",
        topic="烤了餅乾",
        summary="瑠夏烤了餅乾",
        created_at=1000.0,
    )
    motive = motive_from_social_opportunity(opp)

    # Motive 5 字段凍結 (motive_id / content / target / provenance_ref / created_at)
    assert motive.motive_id.startswith("mot_")
    assert "agent_ruka" in motive.content
    assert "烤了餅乾" in motive.content
    assert motive.target == "bryan"
    assert motive.provenance_ref == "opp:opp_abc123"
    assert isinstance(motive.created_at, str)  # ISO 8601 UTC (非 epoch float)
    assert motive.created_at.endswith("+00:00")

    # 客廳背景情境 (CompactSocialState 渲染塊, 含反框架提示語)
    social_block = (
        "[客廳現況]\n"
        "- 在場: agent_ruka\n"
        "- 話題: 烤了餅乾 (agent_ruka)\n"
        "- 氛圍: 活躍\n"
        f"- 提示: {ANTI_FRAMING_HINT}"
    )
    prompt = build_decision_prompt(
        motive=motive,
        provenance_desc="",
        social_context=social_block,
    )

    # 四元決策選項 (SM-4)
    assert "transmit" in prompt
    assert "observe" in prompt
    assert "reflect" in prompt
    assert "do_nothing" in prompt

    # 客廳背景情境注入
    assert "[客廳現況]" in prompt
    assert "agent_ruka" in prompt
    assert "烤了餅乾" in prompt
    assert ANTI_FRAMING_HINT in prompt

    # 向後兼容: 不傳 social_context 也能構建, 且不注入客廳情境
    prompt_no_social = build_decision_prompt(motive=motive, provenance_desc="")
    assert "[客廳現況]" not in prompt_no_social
    assert "transmit" in prompt_no_social  # 四元選項仍在 (Boundary 不變)


# ─────────────────────────────────────────────────────────────
# 4. 0 連鎖意志不變量 (No Cascading Volition)
# ─────────────────────────────────────────────────────────────

def test_zero_cascading_volition_invariant(tmp_path, monkeypatch):
    """外部社交事件輸入後, 絕不修改 agent 自傳檔案或記憶庫, 僅存於記憶體緩存。"""
    # 隔離 data_root: 任何自傳/記憶寫入都會落在 tmp_path 下
    monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))

    bus = _CapturingBus()
    mw = _make_middleware(bus)

    social_event = _make_event(
        "evt_1", "agent_ruka", "烤了餅乾", summary="瑠夏烤了餅乾"
    )
    mw.state.add(social_event)
    asyncio.run(mw._on_agent_intent_enriched(_make_enriched()))

    # 0 連鎖意志: 不發布任何 transmit 觸發類事件 (AGENT_INTENT / AGENCY_TRIGGER)
    published_types = [e.event_type for e in bus.published]
    assert EventType.AGENT_INTENT not in published_types
    assert EventType.AGENCY_TRIGGER not in published_types
    # 只發布感知結果 (AGENT_INTENT_PERCEIVED 是感知輸出, 不是 transmit 觸發)
    assert EventType.AGENT_INTENT_PERCEIVED in published_types

    # 0 自傳檔案/記憶庫修改: 隔離 data_root 下無 diary/dream/relationships/memory
    produced = [str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")]
    for rel in produced:
        assert not any(
            k in rel for k in ("diary", "dream", "relationships", "memory")
        ), f"自傳/記憶庫被修改: {rel}"

    # 機會僅存於記憶體緩存 (aggregator buffer, 0 檔案 IO)
    agg = mw._get_social_aggregator("agent_bryan")
    state = agg.get_compact_state("agent_bryan", 1000.0)
    assert len(state.active_opportunities) == 1
    assert state.active_opportunities[0].actor_id == "agent_ruka"
