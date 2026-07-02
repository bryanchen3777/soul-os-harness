"""
src/io/channels/telegram.py
Soul OS Phase 5a — Telegram Channel Adapter

十個 Bot 對應十個 Agent（Phase 5a ~ 12）:
  yua     → @Yua_Hermes_bot
  ruaka   → @Ruka_Clawra_bot
  akane   → @Akane_Clawra_bot
  rem     → @Rem_Hermes_bot
  ram     → <Ram 對應 bot, Bryan 從 BotFather 拿>
  mahiru  → <Mahiru 對應 bot>
  anna    → <Anna 對應 bot>
  mai     → <Mai 對應 bot>
  miku    → <Miku 對應 bot>
  aoi     → <Aoi 對應 bot>

Bot token 從環境變數讀（.env），不寫死 source code。
"""
from __future__ import annotations

import asyncio
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
ENV_TOKEN_REM = "TELEGRAM_BOT_REM"
# Phase 7-11: Ram / Mahiru / Anna / Mai / Miku（5 個新加 agent）
ENV_TOKEN_RAM = "TELEGRAM_BOT_RAM"
ENV_TOKEN_MAHIRU = "TELEGRAM_BOT_MAHIRU"
ENV_TOKEN_ANNA = "TELEGRAM_BOT_ANNA"
ENV_TOKEN_MAI = "TELEGRAM_BOT_MAI"
ENV_TOKEN_MIKU = "TELEGRAM_BOT_MIKU"
# Phase 12: Aoi (弱角友崎同學)
ENV_TOKEN_AOI = "TELEGRAM_BOT_AOI"

# Agent 對應環境變數
# 註: key 用短 ID (e.g. "yua", "ram") 不是 "agent_yua"
# 這跟 configs/default.yaml 用 "agent_yua" 脫鉤,
# AGENT_ENV_MAP 跟 config 對齊需另開票修(本票 scope: 補 5 個新加 agent 的 env 對應)
AGENT_ENV_MAP = {
    "yua":    ENV_TOKEN_YUA,
    "ruka":   ENV_TOKEN_RUKA,
    "akane":  ENV_TOKEN_AKANE,
    "rem":    ENV_TOKEN_REM,
    "ram":    ENV_TOKEN_RAM,
    "mahiru": ENV_TOKEN_MAHIRU,
    "anna":   ENV_TOKEN_ANNA,
    "mai":    ENV_TOKEN_MAI,
    "miku":   ENV_TOKEN_MIKU,
    "aoi":    ENV_TOKEN_AOI,
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
            f"請在 .env 設定 TELEGRAM_BOT_YUA / RUKA / AKANE / REM / RAM / MAHIRU / ANNA / MAI / MIKU / AOI。"
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
                # Phase 5d+：typing indicator — 立刻送 typing，然後背景每 4s 重送
                # 直到 callback 完成。LLM 慢的時候不會讓用戶以為 bot 壞了。
                typing_task = asyncio.create_task(
                    self._keep_typing(agent_id, user_id)
                )
                try:
                    await self._on_message(agent_id, text, user_id)
                except Exception as e:
                    logger.exception(
                        f"[TG:{agent_id}] on_message callback error: {e}"
                    )
                finally:
                    typing_task.cancel()
                    try:
                        await typing_task
                    except asyncio.CancelledError:
                        pass
        return handler

    async def start(self, on_message: OnMessageCallback) -> None:
        """啟動十個 bot 開始 polling（AGENT_ENV_MAP 列出多少就多少）。"""
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

    async def _keep_typing(self, agent_id: str, user_id: int) -> None:
        """Phase 5d+：每 4s 重送 typing，直到被 cancel。

        Telegram 的 typing indicator 5s 過期，所以 4s 重送比較安全。
        LLM 慢的時候（昨天看到 28s spike）用戶會一直看到「Yua 正在輸入」。
        """
        app = self._apps.get(agent_id)
        if not app:
            return
        try:
            while True:
                try:
                    await app.bot.send_chat_action(
                        chat_id=int(user_id),
                        action="typing",
                    )
                except Exception as e:
                    logger.debug(f"[TG:{agent_id}] typing send error: {e}")
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            logger.debug(f"[TG:{agent_id}] typing task cancelled")
            raise

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
