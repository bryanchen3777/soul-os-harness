"""
src/io/channels/live2d_adapter.py
Soul OS Phase 6.1 — Live2D Channel Adapter

把 ChannelRouter 的 send() 廣播成 type=live2d 的 WS 訊息，
前端 index.html 監聽後轉 postMessage 給 widget iframe。

完整鏈：
  Soul OS agent 講話
    → LLMProxy → AGENT_SPEAK (payload 帶 target_channel="live2d")
    → ChannelRouter._on_agent_speak
    → Live2DChannelAdapter.send()
    → IOGateway.broadcast()  →  WS 送所有連線
    → index.html 監聽 type==="live2d"
    → window.AvatarWidget.say(text)  (embed.js 內部用 postMessage 送 widget)
    → widget.html 內 say() → 顯示對話泡泡

設計原則：
  - ChannelAdapter ABC 的 send() 簽名是 (agent_id, text, user_id)
  - 本 adapter 還沒正式 extend ABC（v0.1 scaffold）— 直接接 IOGateway.broadcast()
  - emotion 預設 "neutral"（Phase 6.2+ 從 mood 計算）
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.io.gateway import IOGateway

logger = logging.getLogger("soul_os.channels.live2d")


class Live2DChannelAdapter:
    """Phase 6.1：ChannelRouter send() → IOGateway WS broadcast → 前端轉 postMessage。"""

    channel_id = "live2d"

    def __init__(self, gateway: "IOGateway") -> None:
        self._gw = gateway

    async def send(
        self,
        agent_id: str,
        text: str,
        user_id: Optional[int] = None,
        *,
        emotion: str = "neutral",
        lip_sync: bool = False,  # v0.1 必填 false
    ) -> bool:
        """
        組 payload 並透過 IOGateway broadcast 給所有 WebSocket 連線。

        Args:
            agent_id: "agent_yua" / "agent_ruka" / "agent_akane"
            text: agent 說的話
            user_id: Live2D 場景下暫無用（前端轉 postMessage 不需要）
            emotion: 預設 "neutral"（v0.1 沒從 mood 推導）
            lip_sync: v0.1 必填 false（契約 v0.1）

        Returns:
            bool: broadcast 成功（v0.1 直接 return True，IOGateway 內部 try/except）
        """
        payload = {
            "type": "live2d",
            "agent_id": agent_id,
            "text": text,
            "emotion": emotion,
            "lip_sync": lip_sync,
        }
        logger.info(
            f"[Live2DChannel] broadcast | agent={agent_id} "
            f"emotion={emotion} lip_sync={lip_sync} text={text[:30]!r}"
        )
        await self._gw.broadcast(payload)
        return True
