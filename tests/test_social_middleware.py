"""
tests/test_social_middleware.py — SI-2.1 防线 1: Ambient Perception Path

工单: SI-2.2 — Social Diffusion Implementation
设计: docs/SOCIAL-DIFFUSION-CONTRACT.md (SI-2.1, 2026-09-03, §6)

验收项:
  - 平行订阅: register() event_filter 含 SOCIAL_WORLD_EVENT (additive)
  - SOCIAL_WORLD_EVENT 进 WorldPerceptionState (ephemeral, 24h novelty window)
  - world_context 注入 [社交感知] 区块, 带「他者行为、非我经历」反框架语
  - 不触发 transmit: 处理 SOCIAL_WORLD_EVENT 本身不 publish 任何事件
  - private 契约违例 (visibility=private 出现在 bus 上) → fail-closed 丢弃
  - 既有 WORLD_EVENT 路径行为不变 (回归)
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from typing import List
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.eventbus.bus import SoulEventBus
from src.eventbus.schema import EventPriority, EventType, SoulEvent
from src.social import (
    SPACE_LOUNGE,
    VISIBILITY_PRIVATE,
    VISIBILITY_PUBLIC,
    SocialWorldEvent,
)
from src.world import (
    WorldPerceptionMiddleware,
    WorldPerceptionState,
    WorldPerceptionTraceWriter,
)


def _run(coro):
    """在 sync test 内跑 async coroutine。"""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if sys.version_info < (3, 10) \
        else asyncio.run(coro)


def _make_social_soul_event(
    *,
    actor_id: str = "agent_miku",
    space_id: str = SPACE_LOUNGE,
    visibility: str = VISIBILITY_PUBLIC,
    event_type: str = "greeting",
    content: str = "大家好",
    novelty_id: str = "social_greeting_miku_001",
    summary: str = "agent_miku 向大家打了招呼",
    priority: int = 0,
) -> SoulEvent:
    """构造一个 SOCIAL_WORLD_EVENT SoulEvent (走 bus 的形态)。"""
    return SoulEvent(
        event_type=EventType.SOCIAL_WORLD_EVENT,
        source=actor_id,
        actor_id=actor_id,
        target="broadcast",
        priority=EventPriority.LOW,
        payload={
            "actor_id": actor_id,
            "space_id": space_id,
            "visibility": visibility,
            "event_type": event_type,
            "content": content,
            "novelty_id": novelty_id,
            "ts": "2026-09-03T00:00:00Z",
            "summary": summary,
            "priority": priority,
        },
    )


def _make_enriched_event(agent_id: str = "agent_ruka", draft: str = "") -> SoulEvent:
    """构造 AGENT_INTENT_ENRICHED event (触发 world_context 注入)。"""
    return SoulEvent(
        event_type=EventType.AGENT_INTENT_ENRICHED,
        source=agent_id,
        target=agent_id,
        priority=EventPriority.NORMAL,
        payload={
            "agent_id": agent_id,
            "reason": "user_message",
            "mode": "private",
            "draft": draft,
            "target_user_id": "bryan",
            "chrono_context": "",
            "memory_context": "",
        },
    )


def _make_middleware(tmp_path: Path, bus=None):
    """构造 middleware (bus 默认 MagicMock, 跟 test_m3_world_awareness 一致)。"""
    trace_path = tmp_path / "trace.jsonl"
    state = WorldPerceptionState()
    writer = WorldPerceptionTraceWriter(trace_path)
    mw = WorldPerceptionMiddleware(
        bus=bus if bus is not None else MagicMock(),
        state=state,
        trace_writer=writer,
    )
    return mw, state


# ───────────────────────────────────────────────────────────
# 1. 平行订阅 (additive)
# ───────────────────────────────────────────────────────────

def test_register_subscribes_social_world_event():
    """SI-2.1 §6.2: register() event_filter 含 SOCIAL_WORLD_EVENT (additive)。"""
    bus = MagicMock()
    mw, _ = _make_middleware(Path(tempfile.mkdtemp()), bus=bus)
    mw.register()
    # 抓 subscribe 调用
    assert bus.subscribe.called
    call_kwargs = bus.subscribe.call_args.kwargs
    event_filter = call_kwargs["event_filter"]
    assert EventType.SOCIAL_WORLD_EVENT in event_filter
    # 既有订阅不动
    assert EventType.WORLD_EVENT in event_filter
    assert EventType.AGENT_INTENT_ENRICHED in event_filter


# ───────────────────────────────────────────────────────────
# 2. SOCIAL_WORLD_EVENT 进 state (不触发 transmit)
# ───────────────────────────────────────────────────────────

def test_social_event_enters_state_without_publish():
    """
    防线 1: 处理 SOCIAL_WORLD_EVENT 本身不 publish 任何事件 (无即时唤醒,
    不触发 transmit / AGENT_INTENT / AGENCY_TRIGGER), 只进 state。
    """
    with tempfile.TemporaryDirectory() as tmp:
        bus = MagicMock()
        mw, state = _make_middleware(Path(tmp), bus=bus)
        social_ev = _make_social_soul_event()
        _run(mw.handle_event(social_ev))

        # 进 state
        active = state.get_active_events()
        assert len(active) == 1
        assert isinstance(active[0], SocialWorldEvent)
        assert active[0].actor_id == "agent_miku"

        # 不 publish (bus.publish 未被调用)
        assert not bus.publish.called, "SOCIAL_WORLD_EVENT 处理不应触发任何 publish"


def test_social_event_invalid_rejected_no_state():
    """invalid social event → reject → 不进 state (fail-closed)。"""
    with tempfile.TemporaryDirectory() as tmp:
        mw, state = _make_middleware(Path(tmp))
        bad = _make_social_soul_event(event_type="hack")  # 不在白名单
        _run(mw.handle_event(bad))
        assert state.get_state_size() == 0


def test_social_event_private_on_bus_dropped():
    """
    SI-2.1 §3.4: visibility=private 出现在 bus 上 = 契约违例 → fail-closed 丢弃
    (防线 2 应已在 producer 侧拦截; 订阅端再兜底)。
    """
    with tempfile.TemporaryDirectory() as tmp:
        mw, state = _make_middleware(Path(tmp))
        private_ev = _make_social_soul_event(visibility=VISIBILITY_PRIVATE)
        _run(mw.handle_event(private_ev))
        assert state.get_state_size() == 0, "private 契约违例应被丢弃"


# ───────────────────────────────────────────────────────────
# 3. world_context 注入 [社交感知] 区块
# ───────────────────────────────────────────────────────────

def test_social_context_injected_into_world_context():
    """
    防线 1: AGENT_INTENT_ENRICHED 处理后, world_context 含 [社交感知] 区块,
    带「他者行为、非我经历」反框架语。
    """
    with tempfile.TemporaryDirectory() as tmp:
        bus = MagicMock()
        mw, _ = _make_middleware(Path(tmp), bus=bus)
        # 先喂 social event
        _run(mw.handle_event(_make_social_soul_event()))

        # 再喂 AGENT_INTENT_ENRICHED
        published: List[SoulEvent] = []
        async def _capture_publish(ev):
            published.append(ev)
        bus.publish = _capture_publish

        _run(mw.handle_event(_make_enriched_event(agent_id="agent_ruka")))

        assert len(published) == 1
        perceived = published[0]
        assert perceived.event_type == EventType.AGENT_INTENT_PERCEIVED
        world_context_text = perceived.payload.get("world_context", "")
        assert "[社交感知]" in world_context_text
        assert "他" in world_context_text  # 反框架语: 他人的行为
        assert "不是你的經歷" in world_context_text
        assert "agent_miku" in world_context_text
        assert "[lounge/greeting]" in world_context_text


def test_social_context_does_not_trigger_transmit():
    """
    防线 1: 社交事件只进 world_context, 不直接触发 transmit —
    处理链只产出 AGENT_INTENT_PERCEIVED (供 SpeakerToken 仲裁), 不产出
    AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER。
    """
    with tempfile.TemporaryDirectory() as tmp:
        bus = MagicMock()
        mw, _ = _make_middleware(Path(tmp), bus=bus)
        _run(mw.handle_event(_make_social_soul_event()))

        published: List[SoulEvent] = []
        async def _capture_publish(ev):
            published.append(ev)
        bus.publish = _capture_publish

        _run(mw.handle_event(_make_enriched_event(agent_id="agent_ruka")))

        for ev in published:
            assert ev.event_type not in (
                EventType.AGENT_SPEAK,
                EventType.AGENT_INTENT,
                EventType.AGENCY_TRIGGER,
            ), f"社交事件不应触发 {ev.event_type}"


def test_social_context_empty_when_no_social_events():
    """没有 social events 时, world_context 不含 [社交感知] 区块 (既有行为不变)。"""
    with tempfile.TemporaryDirectory() as tmp:
        bus = MagicMock()
        mw, _ = _make_middleware(Path(tmp), bus=bus)
        published: List[SoulEvent] = []
        async def _capture_publish(ev):
            published.append(ev)
        bus.publish = _capture_publish

        _run(mw.handle_event(_make_enriched_event(agent_id="agent_ruka")))

        assert len(published) == 1
        world_context_text = published[0].payload.get("world_context", "")
        assert "[社交感知]" not in world_context_text


# ───────────────────────────────────────────────────────────
# 4. 既有 WORLD_EVENT 路径回归
# ───────────────────────────────────────────────────────────

def test_world_event_path_unchanged():
    """既有 WORLD_EVENT 仍走 [世界感知] 区块 (additive 不破坏)。"""
    with tempfile.TemporaryDirectory() as tmp:
        bus = MagicMock()
        mw, _ = _make_middleware(Path(tmp), bus=bus)
        # 喂一个 WORLD_EVENT (复用 WorldEvent 形态)
        from src.world import SyntheticWorldEventSource
        rain = SyntheticWorldEventSource.build_rain_started()
        _run(mw.process_world_event_direct(rain))

        published: List[SoulEvent] = []
        async def _capture_publish(ev):
            published.append(ev)
        bus.publish = _capture_publish

        _run(mw.handle_event(_make_enriched_event(
            agent_id="agent_ruka", draft="外面下雨了吗",
        )))

        assert len(published) == 1
        world_context_text = published[0].payload.get("world_context", "")
        assert "[世界感知]" in world_context_text
        assert "[社交感知]" not in world_context_text  # 没有 social events


# ───────────────────────────────────────────────────────────
# 5. 真实 bus 端到端 (平行订阅生效)
# ───────────────────────────────────────────────────────────

def test_e2e_via_real_bus():
    """真实 bus: SOCIAL_WORLD_EVENT 广播 → middleware 订阅 → state 有记录。"""
    async def scenario():
        bus = SoulEventBus()
        mw, state = _make_middleware(Path(tempfile.mkdtemp()), bus=bus)
        mw.register()
        await bus.start()  # 启动 worker 才会派发
        await bus.publish(_make_social_soul_event())
        # 给 bus 一点时间派发
        await asyncio.sleep(0.05)
        await bus.stop()
        return state

    state = _run(scenario())
    assert state.get_state_size() == 1
    active = state.get_active_events()
    assert isinstance(active[0], SocialWorldEvent)
    assert active[0].actor_id == "agent_miku"
