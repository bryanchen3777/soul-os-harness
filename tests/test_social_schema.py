"""
tests/test_social_schema.py — SI-2.1 Social Diffusion Contract: Schema 扩展

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §3)

验收项:
  - EventType.SOCIAL_WORLD_EVENT 枚举值存在 (additive, 既有枚举 0 变更)
  - SoulEvent.actor_id 可选字段, 默认 None (向后兼容)
  - SocialWorldEvent dataclass (继承 WorldEvent, 带社交字段)
  - payload round-trip (to_payload / from_payload)
"""
from __future__ import annotations

import pytest

from src.eventbus.schema import EventType, SoulEvent
from src.social import (
    EXTERNAL_OTHER_ACTION,
    SOCIAL_EVENT_TYPES,
    SPACE_LOUNGE,
    SPACE_SOUL_WALL,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    SocialWorldEvent,
)
from src.world.perception import WorldEvent


# ───────────────────────────────────────────────────────────
# 1. EventType.SOCIAL_WORLD_EVENT (additive)
# ───────────────────────────────────────────────────────────

def test_event_type_social_world_event_exists():
    """SI-2.1 §3.1: EventType 新增 SOCIAL_WORLD_EVENT 枚举值。"""
    assert EventType.SOCIAL_WORLD_EVENT == "social_world_event"


def test_event_type_existing_values_unchanged():
    """additive: 既有枚举值语义 0 变更 (抽查关键值)。"""
    assert EventType.WORLD_EVENT == "world_event"
    assert EventType.AGENT_INTENT_ENRICHED == "agent_intent_enriched"
    assert EventType.AGENCY_TRIGGER == "agency_trigger"
    assert EventType.USER_MESSAGE == "user_message"


# ───────────────────────────────────────────────────────────
# 2. SoulEvent.actor_id (additive 可选字段)
# ───────────────────────────────────────────────────────────

def test_soul_event_actor_id_default_none():
    """SI-2.1 §3.2: actor_id 默认 None, 向后兼容 (既有 producer 不填不受影响)。"""
    ev = SoulEvent(event_type=EventType.WORLD_EVENT, source="weather")
    assert ev.actor_id is None


def test_soul_event_actor_id_explicit():
    """显式设置 actor_id 应保留。"""
    ev = SoulEvent(
        event_type=EventType.SOCIAL_WORLD_EVENT,
        source="agent_miku",
        actor_id="agent_miku",
    )
    assert ev.actor_id == "agent_miku"


def test_soul_event_actor_id_roundtrip():
    """actor_id 经 model_dump 序列化后保留。"""
    ev = SoulEvent(
        event_type=EventType.SOCIAL_WORLD_EVENT,
        source="agent_miku",
        actor_id="agent_miku",
    )
    dumped = ev.model_dump()
    assert dumped["actor_id"] == "agent_miku"


def test_soul_event_social_world_event_constructible():
    """SOCIAL_WORLD_EVENT 可正常构造 SoulEvent (payload 契约见 validation 测试)。"""
    ev = SoulEvent(
        event_type=EventType.SOCIAL_WORLD_EVENT,
        source="agent_miku",
        actor_id="agent_miku",
        payload={
            "actor_id": "agent_miku",
            "space_id": SPACE_LOUNGE,
            "visibility": VISIBILITY_PUBLIC,
            "event_type": "greeting",
            "content": "大家好",
            "novelty_id": "social_greeting_miku_001",
            "ts": "2026-09-03T00:00:00Z",
            "summary": "agent_miku 向大家打了招呼",
        },
    )
    assert ev.event_type == EventType.SOCIAL_WORLD_EVENT
    assert ev.actor_id == "agent_miku"


# ───────────────────────────────────────────────────────────
# 3. 枚举常量 (SI-2.1 §3.5)
# ───────────────────────────────────────────────────────────

def test_space_id_constants():
    assert SPACE_LOUNGE == "lounge"
    assert SPACE_SOUL_WALL == "soul_wall"


def test_visibility_constants():
    assert VISIBILITY_PUBLIC == "public"
    assert VISIBILITY_PRIVATE == "private"


def test_social_event_types_whitelist():
    """v1 白名单: greeting/share/reply/mood/activity。"""
    assert SOCIAL_EVENT_TYPES == frozenset({
        "greeting", "share", "reply", "mood", "activity",
    })


def test_external_other_action_tag():
    """SI-2.1 §4.2: EXTERNAL_OTHER_ACTION 标签常量。"""
    assert EXTERNAL_OTHER_ACTION == "external_other_action"


# ───────────────────────────────────────────────────────────
# 4. SocialWorldEvent dataclass
# ───────────────────────────────────────────────────────────

def _make_social_event(**overrides) -> SocialWorldEvent:
    base = dict(
        actor_id="agent_miku",
        space_id=SPACE_LOUNGE,
        visibility=VISIBILITY_PUBLIC,
        event_type="greeting",
        content="大家好",
        novelty_id="social_greeting_miku_001",
        ts="2026-09-03T00:00:00Z",
        summary="agent_miku 向大家打了招呼",
    )
    base.update(overrides)
    return SocialWorldEvent(
        source="social",
        type=base["event_type"],
        novelty_id=base["novelty_id"],
        ts=base["ts"],
        summary=base["summary"],
        **{k: v for k, v in base.items() if k not in ("novelty_id", "ts", "summary")},
    )


def test_social_world_event_is_world_event():
    """SocialWorldEvent 继承 WorldEvent (可进 WorldPerceptionState / scoring 管道)。"""
    ev = _make_social_event()
    assert isinstance(ev, WorldEvent)
    assert isinstance(ev, SocialWorldEvent)


def test_social_world_event_fields():
    """社交专属字段齐全。"""
    ev = _make_social_event()
    assert ev.actor_id == "agent_miku"
    assert ev.space_id == SPACE_LOUNGE
    assert ev.visibility == VISIBILITY_PUBLIC
    assert ev.event_type == "greeting"
    assert ev.content == "大家好"
    # 继承字段
    assert ev.source == "social"
    assert ev.type == "greeting"
    assert ev.novelty_id == "social_greeting_miku_001"
    assert ev.priority == 0  # 默认 0 (低刺激度, 防线 1)


def test_social_world_event_priority_default_zero():
    """默认 priority=0 (SI-2.1 §3.4: 低刺激度 hint)。"""
    ev = _make_social_event()
    assert ev.priority == 0


def test_social_world_event_priority_must_be_int():
    """priority 必须是 int (拒绝 str/float/bool, 复用 M3.1 规则)。"""
    with pytest.raises(TypeError):
        _make_social_event(priority="5")
    with pytest.raises(TypeError):
        _make_social_event(priority=5.0)
    with pytest.raises(TypeError):
        _make_social_event(priority=True)


def test_social_world_event_to_payload():
    """to_payload 含 WorldEvent 字段 + 社交字段。"""
    ev = _make_social_event()
    payload = ev.to_payload()
    assert payload["actor_id"] == "agent_miku"
    assert payload["space_id"] == SPACE_LOUNGE
    assert payload["visibility"] == VISIBILITY_PUBLIC
    assert payload["event_type"] == "greeting"
    assert payload["content"] == "大家好"
    assert payload["novelty_id"] == "social_greeting_miku_001"
    assert payload["ts"] == "2026-09-03T00:00:00Z"
    assert payload["summary"] == "agent_miku 向大家打了招呼"
    assert payload["priority"] == 0


def test_social_world_event_from_payload_roundtrip():
    """from_payload 还原 to_payload 的产物 (round-trip)。"""
    ev = _make_social_event()
    restored = SocialWorldEvent.from_payload(ev.to_payload())
    assert restored == ev


def test_social_world_event_from_payload_priority_defensive():
    """from_payload 防御性: 非 int priority 视为 0 (不 crash)。"""
    payload = _make_social_event().to_payload()
    payload["priority"] = "high"
    restored = SocialWorldEvent.from_payload(payload)
    assert restored.priority == 0
