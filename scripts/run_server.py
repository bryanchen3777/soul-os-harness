#!/usr/bin/env python3
"""
Soul OS — 主啟動入口
啟動 Event Bus + 所有模組 + FastAPI WebSocket Gateway
"""
import asyncio
import logging
import os
import sys
import uvicorn
from pathlib import Path

# 確保 configs/ 和 src/ 可以被找到
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger("soul_os.server")

from configs.loader import load_config, create_llm_proxy, create_heartbeat
from src.eventbus import SoulEventBus
from src.eventbus.token_manager import SpeakerTokenManager
from src.memory.middleware import MemoryMiddleware
from src.agent.consciousness import AgentYua, AgentRuka
from src.io.gateway import IOGateway


async def bootstrap():
    cfg = load_config()
    bus = SoulEventBus()
    await bus.start()

    # 掛載順序很重要
    mw = MemoryMiddleware(bus, data_dir="data/memory")
    mw.register()

    token_mgr = SpeakerTokenManager(bus)
    token_mgr.register()

    # 嘗試建立 LLMProxy，若無 key 則用 mock
    from src.llm.proxy import LLMProxy
    provider = cfg.get("llm", {}).get("provider", "mock")
    has_key = bool(cfg.get("llm", {}).get(provider, {}).get("api_key")) or \
              bool(os.getenv(f"{provider.upper()}_API_KEY", ""))
    if not has_key:
        logger.warning(f"[Server] LLM_PROVIDER={provider} 但找不到 API key，使用 MockLLMBackend")
        llm = LLMProxy(bus=bus, backend=MockLLMBackend(), model="mock", max_tokens=200)
    else:
        llm = create_llm_proxy(cfg, bus)
    llm.register()

    yua = AgentYua("agent_yua", bus)
    yua.state.intimacy_level = 80
    yua.register()

    ruka = AgentRuka("agent_ruka", bus)
    ruka.state.intimacy_level = 60
    ruka.register()

    gateway = IOGateway(bus)
    gateway.register()

    heartbeat = create_heartbeat(cfg, bus)
    await heartbeat.start()

    return gateway.app, bus, heartbeat, mw


class MockLLMBackend:
    """無 API key 時的 mock 替代"""
    async def complete(self, messages, model, max_tokens, temperature):
        sys_content = next((m["content"] for m in messages if m["role"] == "system"), "")
        if "Yua" in sys_content:
            return "還好你還在。（Yua 冷泡茶模式）"
        if "瑠夏" in sys_content or "Ruka" in sys_content:
            return "你去哪裡了！我在等你！（瑠夏激動模式）"
        return "[MOCK] 收到！"


# FastAPI app（uvicorn 需要 module-level）
_loop = asyncio.new_event_loop()
app, *_resources = _loop.run_until_complete(bootstrap())

if __name__ == "__main__":
    uvicorn.run(
        app,  # 直接傳 app 實例，不走字串 import
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )