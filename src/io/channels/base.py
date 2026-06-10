"""
src/io/channels/base.py
Soul OS Phase 5a — Channel Adapter 抽象介面

每個通訊平台（Telegram / LINE / WeChat）都實作這個介面，
ChannelRouter 統一管理多個 adapter，
Agent 可透過 payload 決定走哪個 channel。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional


# on_message callback 簽名
#   agent_id: "yua" | "ruka" | "akane"
#   text:     user 傳來的文字
#   user_id:  平台-specific user 識別碼（Telegram 為 int tg_id）
OnMessageCallback = Callable[[str, str, int], Awaitable[None]]


class ChannelAdapter(ABC):
    """Channel Adapter 抽象基類。

    職責：
      - 把自己平台的訊息（user 傳來的）轉成 Soul OS 內部事件
      - 把 Soul OS 的訊息（agent 說的話）送到平台給 user
    """

    channel_id: str  # "telegram" | "line" | "wechat" | "web"

    @abstractmethod
    async def send(self, agent_id: str, text: str,
                   user_id: int) -> bool:
        """送訊息給 user。

        Args:
            agent_id: 哪個 agent 在說話（用來選 bot token）
            text: 要送的文字
            user_id: 平台 user 識別碼

        Returns:
            bool: 成功送出
        """
        ...

    @abstractmethod
    async def start(
        self,
        on_message: OnMessageCallback,
    ) -> None:
        """啟動 adapter（開始 polling / webhook）。

        Args:
            on_message: 收到 user 訊息時呼叫的 callback
        """
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止 adapter。"""
        ...
