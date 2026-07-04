#!/usr/bin/env python3
"""
Soul OS — 主啟動入口
啟動 Event Bus + 所有模組 + FastAPI WebSocket Gateway
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI

# 確保 configs/ 和 src/ 可以被找到
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))

logger = logging.getLogger("soul_os.server")

# Phase 5b：基本 logging config，否則 logger.info 全部吞掉
# uvicorn 預設只接 WARNING+，需要明確指定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# Phase 5b：抑制 httpx/telegram 把 URL（含 bot token）印進 log
# 4-strike 教訓：python-telegram-bot 預設 INFO 級別會在每個 HTTP
# request log 帶完整 https://api.telegram.org/bot<TOKEN>/...
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)

# Phase 5b：load .env（讓 TELEGRAM_BOT_* / MINIMAX_API_KEY 等生效）
# 沒 .env 也不報錯
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class MockLLMBackend:
    async def complete(self, messages, model, max_tokens, temperature):
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        print(f"[MockLLM] sys_content[:50]={sys_content[:50]!r}", flush=True)
        print(f"[MockLLM] Yua={'Yua' in sys_content}", flush=True)
        if "Yua" in sys_content:
            return "還好你還在。（Yua 冷泡茶模式）"
        if "瑠夏" in sys_content or "Ruka" in sys_content:
            return "你去哪裡了！我在等你！（瑠夏激動模式）"
        return "[MOCK] 我在這裡。"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """所有初始化在同一個 event loop 裡，避免跨 loop 問題。"""
    from configs.loader import load_config, create_llm_proxy, create_heartbeat, create_agents
    from src.eventbus import SoulEventBus
    from src.eventbus.token_manager import SpeakerTokenManager
    from src.agent.speaker_token import SpeakerTokenBus
    from src.eventbus.schema import EventType, SoulEvent
    from src.memory.middleware import MemoryMiddleware
    from src.io.gateway import IOGateway

    cfg = load_config()

    # Phase 6.4：載入 live2d config（widget 的 model + voice 對應表）
    live2d_cfg = {}
    try:
        import yaml as _yaml
        _live2d_path = _root / "configs" / "live2d.yaml"
        if _live2d_path.exists():
            live2d_cfg = _yaml.safe_load(_live2d_path.read_text(encoding="utf-8")) or {}
            logger.info(f"[Server] Live2D config loaded: {len((live2d_cfg.get('live2d', {}) or {}).get('agents', {}) or {})} agents")
    except Exception as e:
        logger.warning(f"[Server] Live2D config 載入失敗（用 fallback）: {e}")

    bus = SoulEventBus()
    await bus.start()

    mw = MemoryMiddleware(bus=bus, data_dir="data/memory")
    mw.register()

    token_mgr = SpeakerTokenManager(bus, token_timeout_secs=120.0)
    token_mgr.register()

    # ── SpeakerTokenBus：USER_MESSAGE 仲裁 ─────────────────────
    speaker_token_bus = SpeakerTokenBus(cooldown_secs=4.0)
    # submit_bid 採用 lazy open，不需要單獨的 listener

    provider = cfg.get("llm", {}).get("provider", "mock")
    key = os.getenv(f"{provider.upper()}_API_KEY", "")
    if not key:
        logger.warning(f"[Server] LLM_PROVIDER={provider} 但找不到 API key，使用 MockLLMBackend")
        from src.llm.proxy import LLMProxy
        llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock", max_tokens=200)
    else:
        logger.info(f"[Server] LLM backend: real {provider}")
        llm = create_llm_proxy(cfg, bus)
    llm.register()

    # Phase 12 LLM-as-judge: 設定 process-global LLMProxy reference,
    # 讓 MemoryWriter._get_llm_judge() 跨模組邊界可以拿到
    from src.memory.sage.writer import set_llm_proxy
    set_llm_proxy(llm)
    logger.info("[Server] LLMProxy wired into MemoryWriter (LLM judge ready)")

    # Bry §11 shadow mode (2026-07-02): 對每一筆真實訊息 v6 並行 observation
    # 7 天自動到期, 不影響 prod 路徑結果
    from src.memory.shadow import init_shadow_observer
    shadow_dir = REPO / "data" / "shadow"
    shadow_obs = init_shadow_observer(shadow_dir, enabled=True, llm_proxy=llm)
    logger.info(f"[Server] Shadow mode 啟動 (7天): {shadow_dir}/shadow_log.jsonl")

    # 動態載入所有 enabled Agent（帶 SpeakerTokenBus）
    agents = create_agents(cfg, bus, speaker_token_bus=speaker_token_bus)
    agent_ids = [a.agent_id for a in agents]

    gateway = IOGateway(bus=bus, app=app, live2d_config=live2d_cfg)
    gateway.register()

    heartbeat = create_heartbeat(cfg, bus, agent_ids=agent_ids)
    # 注入 gateway 的 connection manager — heartbeat 會在無人連線時跳過 tick
    heartbeat._manager = gateway.manager
    await heartbeat.start()
    app.state._heartbeat = heartbeat  # expose for /_admin/fast_forward

    # ── ChannelRouter：聚合所有 channel（Telegram / Live2D / 之後）──
    # Phase 5b/5c：Telegram 走 TELEGRAM_BOT_YUA env flag 啟動
    # Phase 6.1：Live2D 永遠啟動（純前端，無外部依賴）
    tg_adapter = None
    live2d_adapter = None
    channel_router = None
    from src.io.channels.router import ChannelRouter

    # Live2D adapter 永遠建（純前端 WS 廣播）
    from src.io.channels.live2d_adapter import Live2DChannelAdapter
    live2d_adapter = Live2DChannelAdapter(gateway=gateway)

    if os.environ.get("TELEGRAM_BOT_YUA"):
        from src.io.channels.telegram import TelegramAdapter

        tg_adapter = TelegramAdapter()
        # Phase 5c：傳 gateway_manager 給 ChannelRouter，heartbeat 觸發時
        # 動態查 WebSocket 連線數，0 連線就 fallback 走 Telegram
        channel_router = ChannelRouter(bus, gateway_manager=gateway.manager)
        channel_router.register(tg_adapter)
        channel_router.register(live2d_adapter)  # Phase 6.1：Live2D 跟 Telegram 共用 router
        await channel_router.start()

        async def _on_tg_message(agent_id: str,
                                  text: str,
                                  user_id: int) -> None:
            """Telegram bot 收到 user 訊息 → 送進 Event Bus"""
            await channel_router.inbound(
                agent_id, text, user_id, channel="telegram"
            )

        await tg_adapter.start(_on_tg_message)
        # 10 個 bot (Phase 5a: 3 + Phase 6.5: 1 + Phase 7-11: 5 + Phase 12: 1)
        logger.info(f"[Server] Telegram channel started (10 bots polling)")
    else:
        # 沒 Telegram 也要有 ChannelRouter（給 Live2D 用）
        channel_router = ChannelRouter(bus, gateway_manager=gateway.manager)
        channel_router.register(live2d_adapter)
        await channel_router.start()
        logger.info("[Server] TELEGRAM_BOT_YUA not set, skip Telegram channel")
    logger.info("[Server] Live2D channel started (WS broadcast to frontend)")
    # ── Phase 5b + 6.1 end ──────────────────────────────────

    logger.info("[Server] 所有模組啟動完成")
    yield

    # ── Shutdown ────────────────────────────────────────────
    if channel_router is not None:
        await channel_router.stop()
    if tg_adapter is not None:
        await tg_adapter.stop()
        logger.info("[Server] Telegram channel stopped")
    # live2d_adapter 沒獨立 start/stop，直接跟著 ChannelRouter 收
    await heartbeat.stop()
    await bus.stop()
    logger.info("[Server] 關閉完成")


app = FastAPI(lifespan=lifespan)

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )
