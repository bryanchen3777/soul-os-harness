"""
src/voice/input_router.py — VoiceInputRouter：语音 USER_MESSAGE 发布构造（MS-3 IMPLEMENTATION）

设计文档：docs/MS-3-VOICE-INTERACTION-CONTRACT.md §5.1（USER_MESSAGE 发布契约复用）
         §5.2（Volition Gate 相容 / 无旁路注入）/ §5.4（可观测性）/ §2.4（矩阵）。

职责：
  1. 组装 gate 判定的 USER_MESSAGE → 发布构造**严格对齐既有通道契约**（§5.1 契约表）：
     - EventType.USER_MESSAGE（0 新增 EventType）
     - payload schema：content/text 双写 + user_id/target_user_id/target_agent/agent_id
       + mode="private" + participants=None（对齐 gateway.py:860-875 / router.py:852-868）
     - session_id = `session_{voice_user_id}_{full_agent_id}`
       （对齐 gateway.py:858 / router.py:851 / LLMProxy._session_key）
     - priority = EventPriority.HIGH（对齐 gateway.py:864）
     - source = `voice:{device_ref}:{owner_hash}`（§5.1 additive 习惯标记，不冲突）
     - additive 可选字段 input_channel="voice"（§5.4；既有消费端未使用该 key）
     - 副作用链原样触发：touch_bryan_last_seen（对齐 gateway.py:882-884），memory /
       relationships / heartbeat 等由 bus 既有 handler 原样处理
     - bus.publish 唯一入口
  2. AMBIENT → 不 publish USER_MESSAGE，返回结构化结果给集成层走 MS-2 observe；
  3. DROP → 仅计数 + trace；
  4. 身份防线：VOICE_OWNER_IDS 白名单（§5.1，对齐 router.py:806-816 TELEGRAM_OWNER_ID）；
  5. 防洪（§4.5）：超限降级 AMBIENT / 硬上限 DROP。

无旁路注入：本模块不 import consciousness / LLMProxy / memory；语音文本进入 LLM 的
唯一路径 = 既有 USER_MESSAGE 链 → consciousness `_fire_intent(reason="user_message")`
→ LLMProxy `_build_messages_private`（reason=="user_message" 分支）。

frozen contract：事件类型 / payload / 消费链 0 新增、0 修改。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence

logger = logging.getLogger("soul_os.voice")

try:  # 事件构造（lazy import 避免 import 面污染；已有模块均如此）
    from src.eventbus.schema import EventPriority, EventType, SoulEvent
except Exception:  # pragma: no cover — 纯构造路径兜底（测试通常能 import）
    EventPriority = None  # type: ignore
    EventType = None  # type: ignore
    SoulEvent = None  # type: ignore

from src.voice.gate import (
    RouteDecision,
    RouteOutcome,
    RoutingFeatures,
    VoiceGateConfig,
    VoiceGateTracer,
    VoiceRateLimiter,
    route,
)

_ENV_OWNERS = "VOICE_OWNER_IDS"


def load_voice_owner_ids(env: Optional[dict] = None) -> Optional[frozenset[str]]:
    """VOICE_OWNER_IDS 白名单（§5.1）：类比 TELEGRAM_OWNER_ID，逗号分隔。

    未配置 → None（不拦，向后兼容；集成层可按需强制配置）；配置 → 严格白名单。
    """
    env = env if env is not None else os.environ
    raw = (env.get(_ENV_OWNERS) or "").strip()
    if not raw:
        return None
    owners = frozenset(o.strip() for o in re.split(r"[,，]", raw) if o.strip())
    return owners or None


def owner_hash(user_id: str) -> str:
    """owner 短 hash（source 追溯用；不暴露明文 user_id）。"""
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:8]


@dataclass(frozen=True)
class VoiceRouterTarget:
    """语音输入的目标 agent / 身份映射（§5.1 契约项）。

    voice_user_id = 语音 owner 白名单中的映射 user（类比 gateway 的 ws_user_id）。
    """

    agent_id: str = "agent_yua"        # 完整 agent_id（对齐 router.py:823-826 归一化）
    voice_user_id: str = "bryan"       # voice owner 映射 user
    device_ref: str = "default"        # 采集设备引用


@dataclass
class VoiceInputRouter:
    """语音输入路由器：gate 判定 → 执行（发布 / AMBIENT 回传 / DROP 计数）。

    可测性：bus 可注入假对象（记录 publish 调用）；now_fn 可注入固定时间。
    """

    bus: Any = None
    config: VoiceGateConfig = field(default_factory=VoiceGateConfig)
    target: VoiceRouterTarget = field(default_factory=VoiceRouterTarget)
    limiter: VoiceRateLimiter = field(default_factory=VoiceRateLimiter)
    tracer: VoiceGateTracer = field(default_factory=VoiceGateTracer)
    transport: Optional[Any] = None    # 预留：跨进程 transport（当前直接 publish）

    # 统计（§4.5 计数 / §5.4 观测）
    user_message_count: int = 0
    ambient_count: int = 0
    drop_count: int = 0

    # ── 身份防线（§5.1）─────────────────────────────────────
    @property
    def owner_allowed(self) -> Optional[frozenset[str]]:
        return self.config.voice_owner_ids

    def owner_ok_for_user(self, user_id: str) -> bool:
        """VOICE_OWNER_IDS 白名单判定（对齐 router.py:806-816 语义）。

        未配置 → True（不拦，向后兼容）；配置 → 仅在白名单内为 True，
        非成员 → False（gate 阶 1 会据此判 AMBIENT，身份防线优先于内容判定）。
        """
        allowed = self.owner_allowed
        if allowed is None:
            return True
        return user_id in allowed

    # ── source 构造（§5.1）──────────────────────────────────
    def build_source(self) -> str:
        uid = self.target.voice_user_id
        return f"voice:{self.target.device_ref}:{owner_hash(uid)}"

    # ── payload / 事件构造（§5.1 契约表）──────────────────────
    def build_user_message_payload(self, features: RoutingFeatures) -> Dict[str, Any]:
        """payload schema 对齐 gateway.py:866-874 + router.py:856-864（并集）。"""
        base: Dict[str, Any] = {
            "content": features.text,          # USER_MESSAGE 慣用 content
            "text": features.text,             # LLMProxy 慣用 text
            "user_id": self.target.voice_user_id,
            "target_user_id": self.target.voice_user_id,   # 透傳給 router outbound
            "agent_id": self.target.agent_id,              # 对齐 router.py:861
            "target_agent": self.target.agent_id,          # 完整 agent_id
            "mode": "private",                             # 语音 = 一对一私聊
            "participants": None,                          # 对齐 gateway.py:843
            # §5.4 additive：现有消费端未使用该 key，0 冲突；仅 provenance 可辨
            "input_channel": "voice",
        }
        # 透传可用的额外特征（非破坏）
        if features.lang:
            base["lang"] = features.lang
        return base

    def build_user_message_event(
        self, features: RoutingFeatures, session_id: Optional[str] = None
    ) -> Any:
        """构造 USER_MESSAGE SoulEvent（100% 对齐既有通道契约，§5.1 契约表）。"""
        full_agent = (
            self.target.agent_id
            if self.target.agent_id.startswith("agent_")
            else f"agent_{self.target.agent_id}"
        )
        sid = session_id or f"session_{self.target.voice_user_id}_{full_agent}"
        return SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source=self.build_source(),
            target=self.target.agent_id,        # 私讯模式：target = agent_id
            priority=EventPriority.HIGH,        # 用户真人输入，不能排在心跳后面
            session_id=sid,
            payload=self.build_user_message_payload(features),
        )

    # ── 编排执行 ────────────────────────────────────────────
    async def route_features(self, features: RoutingFeatures) -> RouteOutcome:
        """gate 判定 → 执行：USER_MESSAGE 发布 / AMBIENT 回传 / DROP 计数。"""
        outcome = route(features, self.config)
        self.tracer.record(outcome)
        return await self._dispatch(outcome)

    async def _dispatch(self, outcome: RouteOutcome) -> RouteOutcome:
        if outcome.decision == RouteDecision.USER_MESSAGE:
            # §4.5 防洪：发布前检查 limiter（时间源 = outcome.ts_ms，可注入 mock）
            now = outcome.ts_ms or 0
            if self.limiter.is_hard_limited(now):
                self.drop_count += 1
                return RouteOutcome(
                    RouteDecision.DROP, outcome.address_score, "flood",
                    "速率硬上限（§4.5）→ 暂 DROP", outcome.features, outcome.ts_ms,
                )
            if not self.limiter.allow_publish(now):
                self.ambient_count += 1
                self.limiter.note_rejection(now)
                return RouteOutcome(
                    RouteDecision.AMBIENT, outcome.address_score, "flood",
                    "速率超限/单轮冷却/backoff（§4.5）→ 降级 AMBIENT",
                    outcome.features, outcome.ts_ms,
                )
            if not outcome.features.owner_ok:
                self.ambient_count += 1
                return RouteOutcome(
                    RouteDecision.AMBIENT, outcome.address_score, "identity",
                    "voice owner 不在白名单（§5.1）", outcome.features, outcome.ts_ms,
                )
            await self._publish_user_message(outcome.features)
            self.user_message_count += 1
            self.limiter.record_publication(now)
            self.limiter.note_acceptance()
            return outcome
        if outcome.decision == RouteDecision.AMBIENT:
            self.ambient_count += 1
            self.limiter.note_rejection(outcome.ts_ms or 0)
            return outcome
        self.drop_count += 1
        return outcome

    async def _publish_user_message(self, features: RoutingFeatures) -> None:
        """bus.publish 唯一入口（对齐 gateway.py:884 / router.py:869），副作用链原样。"""
        event = self.build_user_message_event(features)
        if self.bus is None:
            logger.info(f"[VoiceInputRouter] (no bus) USER_MESSAGE: {features.text[:30]!r}")
            return
        # §5.1 副作用链 1：touch_bryan_last_seen（对齐 gateway.py:882-884；语音=用户发话）
        try:
            from src.io.channels.bryan_state import touch_bryan_last_seen
            touch_bryan_last_seen(self.target.agent_id, features.text)
        except Exception as e:
            logger.warning(f"[VoiceInputRouter] touch_bryan_last_seen 失败：{e}")
        await self.bus.publish(event)
        logger.info(
            f"[VoiceInputRouter] USER_MESSAGE published | source={event.source} "
            f"session={event.session_id} text={features.text[:40]!r}"
        )