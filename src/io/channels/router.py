"""
src/io/channels/router.py
Soul OS Phase 5b + 5c — Channel Router

把 AGENT_SPEAK 事件依 payload 內的 target_channel 分發到對應 adapter。
WebSocket 由現有 IOGateway 處理（不重複），ChannelRouter 只管其他 channel
（Telegram / LINE / WeChat）。

Phase 5c 新增：fallback 邏輯
  - 如果 target_channel=web 且 WebSocket 0 連線（gateway_manager.count == 0）
    → 改送 telegram（最近一次 inbound 的 user）
  - 如果 user 不在 web → 透過 Telegram 找她
  - 用「最近 tg user」mapping，避免每次都要 query 記憶表
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

    def __init__(self, bus, gateway_manager=None):
        self._bus = bus
        self._adapters: dict[str, "ChannelAdapter"] = {}
        # Phase 5c：拿到 IOGateway 的 ConnectionManager，動態查 WebSocket 連線數
        self._gateway_manager = gateway_manager
        # Phase 5c：記「每個 agent 最近一次互動的 tg user」，
        # 給主動觸發（沒帶 target_user_id 的 AGENT_SPEAK）當 fallback 對象
        self._last_tg_user: dict[str, int] = {}
        # Phase 5c+：記最近 tg session 對應的 session_id（給主動觸發帶上下文用）
        self._last_tg_session: dict[str, str] = {}

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
        target_user_id = event.payload.get("target_user_id")
        agent_id = event.payload.get("agent_id", event.source)

        # ── Phase 5c fallback ──────────────────────────────
        # 主動觸發（heartbeat）的 AGENT_SPEAK 預設走 web，
        # 但如果 user 不在 Web UI → 改成走 Telegram（她「找得到你」的方式）
        if target_channel == "web" and self._gateway_manager is not None:
            if self._gateway_manager.count == 0:
                last_user = self._last_tg_user.get(agent_id)
                if last_user:
                    logger.info(
                        f"[ChannelRouter] web 0 conn, "
                        f"fallback telegram: {agent_id} → user {last_user}"
                    )
                    target_channel = "telegram"
                    target_user_id = last_user
                else:
                    # 從來沒跟 user 互動過，沒辦法送
                    # 留給 web 廣播（雖然沒人接，總比丟掉好）
                    logger.info(
                        f"[ChannelRouter] {agent_id} 主動觸發，"
                        f"web 0 conn 但沒有 last_tg_user, 留 web"
                    )
        # ── Phase 5c fallback end ──────────────────────────

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

        if target_user_id is None:
            logger.warning(
                f"[ChannelRouter] AGENT_SPEAK missing target_user_id, "
                f"skip channel=[{target_channel}] agent=[{agent_id}]"
            )
            return

        # Phase 5b：TelegramAdapter 內 _apps key 是短碼（"yua"），
        # 但 bus 上的 agent_id 是 full（"agent_yua"）。strip prefix 對齊。
        # 之後 LINE / WeChat 接 full id 直接用，這段只動 telegram 邏輯。
        if target_channel == "telegram" and agent_id.startswith("agent_"):
            adapter_agent_id = agent_id[len("agent_"):]
        else:
            adapter_agent_id = agent_id

        try:
            # Phase 5b：ChannelAdapter.send() 簽名收 int（Telegram），
            # 但 LINE/WeChat 之後的 user_id 是 string。嘗試 int()，
            # 失敗就退到 str — adapter 端再自己 cast。
            try:
                user_id_arg: object = int(target_user_id)
            except (ValueError, TypeError):
                user_id_arg = str(target_user_id)
            success = await adapter.send(
                agent_id=adapter_agent_id,
                text=text,
                user_id=user_id_arg,
            )
            if success:
                logger.info(
                    f"[ChannelRouter:{target_channel}] sent to "
                    f"{target_user_id} from {adapter_agent_id}: {text[:50]!r}"
                )
            else:
                logger.warning(
                    f"[ChannelRouter:{target_channel}] send failed "
                    f"agent={adapter_agent_id} user={target_user_id}"
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

        session_id 對齊 LLMProxy 讀 history 的 key（_session_key(agent_id)
        回傳 "session_{agent_id}"），這樣 Telegram 跟 WebSocket 的對話歷史
        會寫進同一個 session，LLM 看得到。

        target 必須是「完整 agent_id」（如 "agent_yua"），因為
        AgentConsciousness.register() 用 target_filter=agent_id
        （完整前綴）。早期 code 用 target=agent_id（短碼），bus match 不到。
        """
        # Phase 5c：記「這個 agent 最近一次互動的 tg user」
        # 給主動觸發（heartbeat）fallback 用
        # 注意：key 必須是「完整 agent_id」（full_agent_id）
        # 因為 _on_agent_speak 拿到的 agent_id 是 "agent_yua"（consciousness 來的）
        # 而 inbound 拿到的可能是 "yua"（Telegram callback 短碼）→ 統一用 full
        full_agent_id = (
            agent_id if agent_id.startswith("agent_")
            else f"agent_{agent_id}"
        )
        if channel == "telegram":
            self._last_tg_user[full_agent_id] = user_id
            self._last_tg_session[full_agent_id] = f"session_{full_agent_id}"

        # Phase 5c+ fix：session_id 跟 LLMProxy _session_key 對齊
        # 之前用 f"tg_{full_agent_id}_{user_id}" 變成獨立 session
        # LLM 讀 history 是用固定 "session_{agent_id}"，看不到 tg 對話
        session_id = f"session_{full_agent_id}"
        event = SoulEvent(
            event_type=EventType.USER_MESSAGE,
            source=f"{channel}:{user_id}",
            target=full_agent_id,  # 私訊模式，target = 完整 agent_id
            payload={
                "content": text,        # USER_MESSAGE 慣例用 content（consciousness.py 看得到）
                "text": text,           # 跟 LLMProxy 的 _on_user_message 一致
                "user_id": str(user_id),
                "agent_id": full_agent_id,
                "target_agent": full_agent_id,
                "channel": channel,
                "target_channel": channel,   # 透傳到 AGENT_INTENT → AGENT_SPEAK
                "target_user_id": user_id,   # 透傳，給 router outbound 用
                "mode": "private",           # Telegram 預設一對一私聊
            },
            session_id=session_id,
        )
        await self._bus.publish(event)
        logger.info(
            f"[inbound:{channel}] {full_agent_id} ← user_id={user_id} "
            f"session={session_id} text={text[:50]!r}"
        )
