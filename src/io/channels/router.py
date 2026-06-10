"""
src/io/channels/router.py
Soul OS Phase 5b — Channel Router

把 AGENT_SPEAK 事件依 payload 內的 target_channel 分發到對應 adapter。
WebSocket 由現有 IOGateway 處理（不重複），ChannelRouter 只管其他 channel
（Telegram / LINE / WeChat）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from src.eventbus.schema import EventType, SoulEvent

if TYPE_CHECKING:
    from .base import ChannelAdapter

logger = logging.getLogger("soul_os.channels.router")


class ChannelRouter:
    """Phase 5b：把 AGENT_SPEAK 依 target_channel 分發到對應 ChannelAdapter。"""

    def __init__(self, bus):
        self._bus = bus
        self._adapters: dict[str, "ChannelAdapter"] = {}

    def register(self, adapter: "ChannelAdapter") -> None:
        """註冊一個 channel adapter（如 TelegramAdapter）。"""
        self._adapters[adapter.channel_id] = adapter
        logger.info(
            f"[ChannelRouter] registered [{adapter.channel_id}] "
            f"({type(adapter).__name__})"
        )

    async def start(self) -> None:
        """訂閱 AGENT_SPEAK，分發到對應 channel。"""
        self._bus.subscribe(
            "channel_router",
            self._on_agent_speak,
            event_filter={EventType.AGENT_SPEAK},
        )
        logger.info("[ChannelRouter] subscribed AGENT_SPEAK")

    async def stop(self) -> None:
        """取消訂閱（給 shutdown 用）。"""
        self._bus.unsubscribe("channel_router")
        logger.info("[ChannelRouter] unsubscribed")

    async def _on_agent_speak(self, event: SoulEvent) -> None:
        target_channel = event.payload.get("target_channel", "web")

        # Web 由 IOGateway 處理，這裡跳過避免重複送出
        if target_channel == "web":
            return

        adapter = self._adapters.get(target_channel)
        if not adapter:
            logger.warning(
                f"[ChannelRouter] no adapter for channel "
                f"[{target_channel}] — 訊息丟棄"
            )
            return

        text = event.payload.get("text", "")
        target_user_id = event.payload.get("target_user_id")
        agent_id = event.payload.get("agent_id", event.source)

        if target_user_id is None:
            logger.warning(
                f"[ChannelRouter] AGENT_SPEAK missing target_user_id, "
                f"skip channel=[{target_channel}] agent=[{agent_id}]"
            )
            return

        try:
            # Phase 5b：ChannelAdapter.send() 簽名收 int（Telegram），
            # 但 LINE/WeChat 之後的 user_id 是 string。嘗試 int()，
            # 失敗就退到 str — adapter 端再自己 cast。
            try:
                user_id_arg: object = int(target_user_id)
            except (ValueError, TypeError):
                user_id_arg = str(target_user_id)
            success = await adapter.send(
                agent_id=agent_id,
                text=text,
                user_id=user_id_arg,
            )
            if success:
                logger.info(
                    f"[ChannelRouter:{target_channel}] sent to "
                    f"{target_user_id} from {agent_id}: {text[:50]!r}"
                )
            else:
                logger.warning(
                    f"[ChannelRouter:{target_channel}] send failed "
                    f"agent={agent_id} user={target_user_id}"
                )
        except Exception as e:
            logger.exception(
                f"[ChannelRouter:{target_channel}] send error: {e}"
            )

    async def inbound(
        self,
        agent_id: str,
        text: str,
        user_id: int,
        channel: str = "telegram",
    ) -> None:
        """Telegram / LINE / WeChat 收到 user 訊息 → 發 USER_MESSAGE 進 Event Bus。

        session_id 策略（A 方案，Phase 5b 簡單版）：
          session_id = f"tg_{agent_id}_{user_id}"
        之後要支援跨時間保留 session 再升級到 mapping。
        """
        session_id = f"tg_{agent_id}_{user_id}"
        event = SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source=f"{channel}:{user_id}",
            target=agent_id,  # 私訊模式，target = agent_id
            payload={
                "content": text,        # USER_MESSAGE 慣例用 content（consciousness.py 看得到）
                "text": text,           # 跟 LLMProxy 的 _on_user_message 一致
                "user_id": str(user_id),
                "agent_id": agent_id,
                "target_agent": agent_id,
                "channel": channel,
                "target_channel": channel,   # 透傳到 AGENT_INTENT → AGENT_SPEAK
                "target_user_id": user_id,   # 透傳，給 router outbound 用
                "mode": "private",           # Telegram 預設一對一私聊
            },
            session_id=session_id,
        )
        await self._bus.publish(event)
        logger.info(
            f"[inbound:{channel}] {agent_id} ← user_id={user_id} "
            f"session={session_id} text={text[:50]!r}"
        )
