"""
src/io/channels/telegram.py
Soul OS Phase 5a — Telegram Channel Adapter

三個 Bot 對應三個 Agent：
  yua   → @Yua_Hermes_bot
  ruaka → @Ruka_Clawra_bot
  akane → @Akane_Clawra_bot

Bot token 從環境變數讀（.env），不寫死 source code。
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Awaitable, Optional

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

from .base import ChannelAdapter, OnMessageCallback

logger = logging.getLogger("soul_os.channels.telegram")


# 環境變數名稱常數（方便測試時 mock）
ENV_TOKEN_YUA = "TELEGRAM_BOT_YUA"
ENV_TOKEN_RUKA = "TELEGRAM_BOT_RUKA"
ENV_TOKEN_AKANE = "TELEGRAM_BOT_AKANE"

# Agent 對應環境變數
AGENT_ENV_MAP = {
    "yua":   ENV_TOKEN_YUA,
    "ruka":  ENV_TOKEN_RUKA,
    "akane": ENV_TOKEN_AKANE,
}


def _load_tokens() -> dict[str, str]:
    """從環境變數讀三個 bot token。

    缺一就 raise（fail-fast，避免啟動後某個 agent 默默不能通訊）。
    """
    tokens = {}
    missing = []
    for agent_id, env_key in AGENT_ENV_MAP.items():
        val = os.environ.get(env_key)
        if not val:
            missing.append(env_key)
        else:
            tokens[agent_id] = val
    if missing:
        raise RuntimeError(
            f"Missing Telegram bot tokens in env: {missing}. "
            f"請在 .env 設定 TELEGRAM_BOT_YUA / RUKA / AKANE。"
        )
    return tokens


class TelegramAdapter(ChannelAdapter):
    channel_id = "telegram"

    def __init__(self, tokens: Optional[dict[str, str]] = None):
        """Args:
            tokens: 測試用覆寫；正式環境省略 → 從 env 讀
        """
        self._tokens = tokens or _load_tokens()
        self._apps: dict[str, Application] = {}
        self._on_message: Optional[OnMessageCallback] = None

    def _make_handler(self, agent_id: str):
        """每個 bot 一個 handler，closure 帶 agent_id。"""
        async def handler(
            update: Update,
            ctx: ContextTypes.DEFAULT_TYPE,
        ):
            if not update.message:
                return
            text = update.message.text or ""
            user = update.message.from_user
            if not user:
                return
            user_id = user.id
            logger.info(
                f"[TG:{agent_id}] recv from {user_id} "
                f"(@{user.username or '?'}): {text[:50]!r}"
            )
            if self._on_message:
                try:
                    await self._on_message(agent_id, text, user_id)
                except Exception as e:
                    logger.exception(
                        f"[TG:{agent_id}] on_message callback error: {e}"
                    )
        return handler

    async def start(self, on_message: OnMessageCallback) -> None:
        """啟動三個 bot 開始 polling。"""
        self._on_message = on_message
        for agent_id, token in self._tokens.items():
            app = ApplicationBuilder().token(token).build()
            app.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self._make_handler(agent_id),
                )
            )
            self._apps[agent_id] = app

            await app.initialize()
            await app.start()
            await app.updater.start_polling()
            logger.info(
                f"[TG:{agent_id}] polling started "
                f"(token={token[:8]}...)"
            )

    async def send(self, agent_id: str, text: str,
                   user_id: "int | str") -> bool:
        """送訊息給指定 user（user_id = Telegram user id, int）。"""
        app = self._apps.get(agent_id)
        if not app:
            logger.warning(f"[TG] No app for agent [{agent_id}]")
            return False
        try:
            await app.bot.send_message(chat_id=int(user_id), text=text)
            logger.info(
                f"[TG:{agent_id}] sent to {user_id}: {text[:50]!r}"
            )
            return True
        except Exception as e:
            logger.error(f"[TG:{agent_id}] send error: {e}")
            return False

    async def stop(self) -> None:
        """停止所有 bot。"""
        for agent_id, app in self._apps.items():
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
                logger.info(f"[TG:{agent_id}] stopped")
            except Exception as e:
                logger.error(f"[TG:{agent_id}] stop error: {e}")
