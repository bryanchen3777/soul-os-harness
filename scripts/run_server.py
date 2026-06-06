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

    # 動態載入所有 enabled Agent（帶 SpeakerTokenBus）
    agents = create_agents(cfg, bus, speaker_token_bus=speaker_token_bus)
    agent_ids = [a.agent_id for a in agents]

    gateway = IOGateway(bus=bus, app=app)
    gateway.register()

    heartbeat = create_heartbeat(cfg, bus, agent_ids=agent_ids)
    await heartbeat.start()
    app.state._heartbeat = heartbeat  # expose for /_admin/fast_forward

    logger.info("[Server] 所有模組啟動完成")
    yield

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
