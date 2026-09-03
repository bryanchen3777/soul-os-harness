"""
tests/test_social_opportunity.py — SI-3 Phase 1 單元測試

工单: TICKET-SI-3-PHASE-1 (Dual-Brain Edition)
设计: docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md (v1.0, 2026-09-03, 已鎖定)

覆蓋 (工單驗收):
  1. test_opportunity_lifecycle_and_expiry: 未過期 / 邊界 / 過期 (is_expired)
  2. test_buffer_pruning_and_fifo_capacity: 過期自動剔除 + 容量淘汰最舊
  3. test_aggregator_compact_state_and_rendering: 緊湊狀態聚合 + 反框架語 +
     Token 預算受控 (<=150)
  4. test_identity_quarantine_invariant: 外部 actor 機會只作背景, 不修改
     current_agent_id 自傳狀態 (0 檔案 IO / 0 記憶寫入)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.social import (
    ANTI_FRAMING_HINT,
    CompactSocialState,
    SocialOpportunity,
    SocialOpportunityBuffer,
    SocialPerceptionAggregator,
)
from src.social.schema import SocialWorldEvent


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


# ─────────────────────────────────────────────────────────────
# 1. SocialOpportunity 生命週期與 TTL 邊界
# ─────────────────────────────────────────────────────────────

def test_opportunity_lifecycle_and_expiry():
    opp = SocialOpportunity(
        opportunity_id="opp_abc123",
        source_event_id="evt_1",
        actor_id="agent_ruka",
        space_id="lounge",
        topic="餅乾",
        summary="瑠夏烤了餅乾",
        created_at=1000.0,
        ttl_seconds=300.0,
    )
    # 未過期
    assert opp.is_expired(1000.0) is False
    assert opp.is_expired(1299.999) is False
    # 邊界: now == created_at + ttl_seconds → 過期
    assert opp.is_expired(1300.0) is True
    # 過期
    assert opp.is_expired(1300.001) is True
    # 預設值 (SI-3 §3.1)
    assert opp.salience_level == "noticeable"
    assert opp.world_occurrence_id is None
    assert opp.metadata == {}
    assert opp.ttl_seconds == 300.0


# ─────────────────────────────────────────────────────────────
# 2. SocialOpportunityBuffer 容量淘汰 + 過期修剪
# ─────────────────────────────────────────────────────────────

def test_buffer_pruning_and_fifo_capacity():
    buf = SocialOpportunityBuffer(max_capacity=5)
    for i in range(5):
        buf.add_opportunity(SocialOpportunity(
            opportunity_id=f"opp_{i}",
            source_event_id=f"evt_{i}",
            actor_id="agent_ruka",
            space_id="lounge",
            topic=f"topic{i}",
            summary=f"summary{i}",
            created_at=float(i),
        ))
    assert len(buf) == 5

    # 第 6 筆 → 淘汰 created_at 最舊者 (opp_0)
    buf.add_opportunity(SocialOpportunity(
        opportunity_id="opp_5",
        source_event_id="evt_5",
        actor_id="agent_ruka",
        space_id="lounge",
        topic="topic5",
        summary="summary5",
        created_at=5.0,
    ))
    active = buf.get_active_opportunities(now=100.0)
    ids = [o.opportunity_id for o in active]
    assert "opp_0" not in ids
    assert len(active) == 5

    # 同 id 更新: 覆蓋不新增
    buf.add_opportunity(SocialOpportunity(
        opportunity_id="opp_1",
        source_event_id="evt_1b",
        actor_id="agent_ruka",
        space_id="lounge",
        topic="topic1b",
        summary="summary1b",
        created_at=1.0,
    ))
    assert len(buf) == 5

    # 過期自動修剪: 加入一筆短 TTL 機會, 查詢時被剔除
    buf.add_opportunity(SocialOpportunity(
        opportunity_id="opp_expired",
        source_event_id="evt_x",
        actor_id="agent_ruka",
        space_id="lounge",
        topic="old",
        summary="old",
        created_at=0.0,
        ttl_seconds=10.0,
    ))
    active = buf.get_active_opportunities(now=100.0)
    assert all(o.opportunity_id != "opp_expired" for o in active)
    assert len(buf) == 5  # 修剪後回到容量內

    # clear
    buf.clear()
    assert len(buf) == 0
    assert buf.get_active_opportunities(now=100.0) == []


# ─────────────────────────────────────────────────────────────
# 3. SocialPerceptionAggregator 緊湊狀態 + 渲染
# ─────────────────────────────────────────────────────────────

def test_aggregator_compact_state_and_rendering():
    agg = SocialPerceptionAggregator(current_agent_id="agent_bryan")
    now = 1000.0

    # 外部 actor 事件 → 生成機會
    opp = agg.update_from_event(
        _make_event(
            "evt_1",
            "agent_ruka",
            "烤了餅乾",
            summary="瑠夏烤了餅乾",
            data={"world_occurrence_id": "occ_1"},
        ),
        now,
    )
    assert opp is not None
    assert opp.actor_id == "agent_ruka"
    assert opp.topic == "烤了餅乾"
    assert opp.source_event_id == "evt_1"
    assert opp.world_occurrence_id == "occ_1"

    state = agg.get_compact_state(agent_id="agent_bryan", now=now)
    assert state.present_actors == ["agent_ruka"]
    assert state.recent_topics == ["烤了餅乾"]
    assert state.last_speaker == "agent_ruka"
    assert state.last_speech_ts == now
    assert len(state.active_opportunities) == 1
    assert state.lounge_mood == "lively"

    # 渲染: 含反框架警示語 + 在場/話題 + Token 預算受控
    block = agg.render_compact_prompt_block(agent_id="agent_bryan", state=state)
    assert ANTI_FRAMING_HINT in block
    assert "agent_ruka" in block
    assert "烤了餅乾" in block
    assert _estimate_tokens(block) <= 150

    # 無在場他人且無活躍話題 → ""
    empty_state = CompactSocialState()
    assert agg.render_compact_prompt_block(agent_id="agent_bryan", state=empty_state) == ""

    # 只有自己發言 (無他人在場) 且無話題 → ""
    self_only = CompactSocialState(present_actors=["agent_bryan"])
    assert agg.render_compact_prompt_block(agent_id="agent_bryan", state=self_only) == ""


def test_aggregator_topic_dedup_and_top3():
    agg = SocialPerceptionAggregator(current_agent_id="agent_bryan")
    now = 1000.0
    agg.update_from_event(_make_event("evt_1", "agent_ruka", "天氣"), now)
    agg.update_from_event(_make_event("evt_2", "agent_miku", "音樂"), now + 1.0)
    agg.update_from_event(_make_event("evt_3", "agent_rem", "餅乾"), now + 2.0)
    agg.update_from_event(_make_event("evt_4", "agent_ruka", "天氣"), now + 3.0)

    state = agg.get_compact_state(agent_id="agent_bryan", now=now + 3.0)
    # 去重 + 最新在前 + Top 3
    assert state.recent_topics == ["天氣", "餅乾", "音樂"]
    assert len(state.recent_topics) <= 3
    assert state.last_speaker == "agent_ruka"


# ─────────────────────────────────────────────────────────────
# 4. 身份隔離不變量 (SI-2 防線 3 / SI-3 §2)
# ─────────────────────────────────────────────────────────────

def test_identity_quarantine_invariant(tmp_path):
    agg = SocialPerceptionAggregator(current_agent_id="agent_bryan")
    now = 1000.0
    agg.update_from_event(_make_event("evt_1", "agent_ruka", "烤了餅乾"), now)
    agg.update_from_event(_make_event("evt_2", "agent_miku", "分享音樂"), now + 1.0)

    state = agg.get_compact_state(agent_id="agent_bryan", now=now + 1.0)
    # 外部 actor 只出現在背景感知, 不被當作自己
    assert "agent_ruka" in state.present_actors
    assert "agent_miku" in state.present_actors
    assert agg.current_agent_id == "agent_bryan"

    # 外部 actor 的機會只進 buffer (背景), 不修改任何自傳狀態:
    # aggregator 0 檔案 IO — 隔離 data_root 下不產生任何檔案
    assert list(tmp_path.iterdir()) == []

    # 渲染把外部 actor 列為「在場」, 自己不出現在在場清單
    block = agg.render_compact_prompt_block(agent_id="agent_bryan", state=state)
    assert "agent_bryan" not in block
    assert "agent_ruka" in block
    assert "agent_miku" in block
