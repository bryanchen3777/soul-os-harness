"""
src/social/aggregator.py — SI-3 Social Perception Aggregator (緊湊社交狀態聚合器)

工单: TICKET-SI-3-PHASE-1 (Dual-Brain Edition)
设计: docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md (v1.0, 2026-09-03, 已鎖定)

核心哲學 (SI-3 §1.2): Events are history; social state is perception.
  用「誰在場 + 最近活躍話題 + 客廳氛圍 + 有效機會」的緊湊物件取代無窮無盡的
  Event Feed, 徹底固定 Token 預算。

Frozen Invariants (SI-3 §2):
  - No Cascading Volition: 聚合器只產出「感知狀態」與「機會候選」, 絕不直接
    觸發發言; 發言與否由 SM-4 Volition 決定 (0 Auto-Cascading)。
  - Identity Quarantine (SI-2 防線 3): 外部 actor 的事件只作為背景感知
    (present_actors / recent_topics / active_opportunities), 本模組 0 檔案
    IO、0 記憶寫入, 絕不修改任何靈魂的自傳狀態。
  - No Vector DB: 純記憶體聚合, 0 外部依賴。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .opportunity import SocialOpportunity, SocialOpportunityBuffer
from .schema import SocialWorldEvent

# 反框架警示語 (SI-3 §3.4, 渲染區塊必須逐字包含)
ANTI_FRAMING_HINT = "他人動態屬環境背景，無需逐條回覆；若無強烈動機，保持留白。"

# 長度上限 (SI-3 §3.1)
TOPIC_MAX_CHARS = 20
SUMMARY_MAX_CHARS = 100

# 氛圍語彙 (SI-3 §3.3)
MOOD_CALM = "calm"
MOOD_LIVELY = "lively"
MOOD_QUIET = "quiet"

_MOOD_LABELS = {
    MOOD_CALM: "平靜留白",
    MOOD_LIVELY: "活躍",
    MOOD_QUIET: "安靜",
}


@dataclass
class CompactSocialState:
    """緊湊社交感知狀態 (SI-3 §3.3)。

    Attributes:
        present_actors: 當前在場角色名單 (如 ["bryan", "ruka", "akane"]).
        recent_topics: 最近熱門話題 (Top 3, 最新在前).
        last_speaker: 最近一位發言者.
        last_speech_ts: 最近一次發言時間 (epoch seconds).
        active_opportunities: 當前有效機會清單.
        lounge_mood: 客廳氛圍 ("calm" | "lively" | "quiet").
    """

    present_actors: List[str] = field(default_factory=list)
    recent_topics: List[str] = field(default_factory=list)
    last_speaker: Optional[str] = None
    last_speech_ts: float = 0.0
    active_opportunities: List[SocialOpportunity] = field(default_factory=list)
    lounge_mood: str = MOOD_CALM


class SocialPerceptionAggregator:
    """把社交事件流聚合為緊湊感知狀態 + 機會緩存 (SI-3 §3.4)。

    用法:
        agg = SocialPerceptionAggregator(current_agent_id="agent_bryan")
        agg.update_from_event(event, now)          # 吸收事件
        state = agg.get_compact_state("agent_bryan", now)
        block = agg.render_compact_prompt_block("agent_bryan", state)
    """

    def __init__(
        self,
        current_agent_id: str,
        buffer: Optional[SocialOpportunityBuffer] = None,
    ) -> None:
        if not isinstance(current_agent_id, str) or not current_agent_id.strip():
            raise ValueError(
                f"current_agent_id 必填且為非空 str, got: {current_agent_id!r}"
            )
        self.current_agent_id = current_agent_id
        self._buffer = buffer if buffer is not None else SocialOpportunityBuffer()
        self._present_actors: List[str] = []
        self._recent_topics: List[str] = []
        self._topic_speakers: Dict[str, str] = {}
        self._last_speaker: Optional[str] = None
        self._last_speech_ts: float = 0.0

    # ── 事件吸收 ──────────────────────────────────────────────

    def update_from_event(
        self, event: SocialWorldEvent, now: float
    ) -> Optional[SocialOpportunity]:
        """吸收一個社交事件, 更新感知狀態; 有話題則生成機會入 buffer。

        Args:
            event: 已通過 Identity Firewall 的社交事件 (外部他者或自己).
            now: 當前 epoch timestamp (秒).

        Returns:
            生成的 SocialOpportunity; 無有效話題時回傳 None。
        """
        actor = event.actor_id
        if actor:
            if actor not in self._present_actors:
                self._present_actors.append(actor)
            self._last_speaker = actor
            self._last_speech_ts = now

        topic = _extract_topic(event.content)
        if topic is None:
            return None

        self._register_topic(topic, actor)
        opp = SocialOpportunity(
            opportunity_id=f"opp_{uuid.uuid4().hex[:12]}",
            source_event_id=event.novelty_id,
            actor_id=actor,
            space_id=event.space_id,
            topic=topic,
            summary=event.summary[:SUMMARY_MAX_CHARS],
            created_at=now,
            world_occurrence_id=event.data.get("world_occurrence_id"),
        )
        self._buffer.add_opportunity(opp)
        return opp

    # ── 狀態讀取 ──────────────────────────────────────────────

    def get_compact_state(self, agent_id: str, now: float) -> CompactSocialState:
        """回傳給該 agent 的當前緊湊客廳感知狀態 (含有效機會, 已修剪過期)。"""
        active = self._buffer.get_active_opportunities(now)
        return CompactSocialState(
            present_actors=list(self._present_actors),
            recent_topics=list(self._recent_topics),
            last_speaker=self._last_speaker,
            last_speech_ts=self._last_speech_ts,
            active_opportunities=active,
            lounge_mood=self._compute_mood(active),
        )

    def render_compact_prompt_block(
        self, agent_id: str, state: CompactSocialState
    ) -> str:
        """渲染固定預算之 Prompt 區塊 (<=150 tokens, 含反框架警示語)。

        若無在場他人且無活躍話題 → 回傳 "" (留白, 不注入噪音)。
        """
        others = [a for a in state.present_actors if a != agent_id]
        has_topic = bool(state.recent_topics) or bool(state.active_opportunities)
        if not others and not has_topic:
            return ""

        lines = ["[客廳現況]"]
        if others:
            lines.append(f"- 在場: {', '.join(others)}")
        if state.recent_topics:
            parts = []
            for topic in state.recent_topics:
                speaker = self._topic_speakers.get(topic)
                parts.append(f"{topic} ({speaker})" if speaker else topic)
            lines.append(f"- 話題: {'; '.join(parts)}")
        lines.append(
            f"- 氛圍: {_MOOD_LABELS.get(state.lounge_mood, state.lounge_mood)}"
        )
        lines.append(f"- 提示: {ANTI_FRAMING_HINT}")
        return "\n".join(lines)

    # ── 內部 ──────────────────────────────────────────────────

    def _register_topic(self, topic: str, actor: str) -> None:
        """登記話題 (去重, 最新在前, 只留 Top 3) 與其提及者。"""
        if topic in self._recent_topics:
            self._recent_topics.remove(topic)
        self._recent_topics.insert(0, topic)
        del self._recent_topics[3:]
        if actor:
            self._topic_speakers[topic] = actor

    def _compute_mood(self, active: List[SocialOpportunity]) -> str:
        """氛圍規則 (簡單可測): 無人在場 → quiet; 有活躍機會 → lively; 否則 calm。"""
        if not self._present_actors:
            return MOOD_QUIET
        if active:
            return MOOD_LIVELY
        return MOOD_CALM


def _extract_topic(content: str) -> Optional[str]:
    """從 content 提取簡明話題 (<=20 chars); 空白內容視為無話題。"""
    text = content.strip()
    if not text:
        return None
    return text[:TOPIC_MAX_CHARS]


__all__ = [
    "CompactSocialState",
    "SocialPerceptionAggregator",
    "ANTI_FRAMING_HINT",
]
