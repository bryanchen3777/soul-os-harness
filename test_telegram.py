"""
test_telegram.py — Phase 5a smoke test
啟動三個 Bot polling，等待 user 從 Telegram 傳訊息，echo 回傳。
不 print 任何 token 內容，只 log agent_id / user_id / text。
"""
import asyncio
import logging
from dotenv import load_dotenv

load_dotenv()

from src.io.channels.telegram import TelegramAdapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
)
logger = logging.getLogger("test_telegram")


async def on_msg(agent_id: str, text: str, user_id: int) -> None:
    """收到 user 訊息時：print + echo 回傳。"""
    print(f"[recv] agent={agent_id} user_id={user_id} text={text!r}")
    # 從 .env 拿 owner id（echo 只回給 owner，不 echo 給陌生人）
    import os
    OWNER_ID = int(os.environ.get("TELEGRAM_OWNER_ID", "0"))
    if OWNER_ID and user_id != OWNER_ID:
        # 陌生人 → 簡短回應，不浪費 token
        await adapter.send(
            agent_id,
            f"（{agent_id}：這是私人 bot，目前只回應 owner）",
            user_id,
        )
        return
    # owner → echo
    await adapter.send(agent_id, f"（echo {agent_id}）{text}", user_id)


async def main():
    global adapter

    # 抑制 httpx/telegram 把 URL（含 bot token）印進 log
    # python-telegram-bot 預設 INFO 會在每個 HTTP request log
    # 裡帶完整 https://api.telegram.org/bot<TOKEN>/...
    # → 設 WARNING 阻斷
    for noisy in ("httpx", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    adapter = TelegramAdapter()
    await adapter.start(on_msg)
    print("=" * 50)
    print("Telegram 3 bot polling started")
    print("  - @Yua_Hermes_bot")
    print("  - @Ruka_Clawra_bot")
    print("  - @Akane_Clawra_bot")
    print("Owner check: TELEGRAM_OWNER_ID =", "set" if adapter else "n/a")
    print("去 Telegram 對三個 bot 各發一條訊息")
    print("Ctrl+C 停止")
    print("=" * 50)

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("Stopping...")
    finally:
        await adapter.stop()
        print("Stopped.")


adapter = None
asyncio.run(main())
