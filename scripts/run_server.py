#!/usr/bin/env python3
"""
Soul OS — 主啟動入口
啟動 Event Bus + 所有模組 + FastAPI WebSocket Gateway
"""
import asyncio
import uvicorn

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


# FastAPI app（uvicorn 需要 module-level）
_loop = asyncio.new_event_loop()
app, *_resources = _loop.run_until_complete(bootstrap())

if __name__ == "__main__":
    uvicorn.run(
        "scripts.run_server:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info",
    )