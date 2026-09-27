"""tests/clients/test_web_server_avatar_action_dispatch.py
驗證 VC-AVATAR-04：WebSocket 動作事件即時推播與端到端串接。
"""
import asyncio
import json
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.web_server import build_app
from tests.clients.test_voice_companion import WEB_TEST_CONFIG, FakeWebASR, FakeWebStreamer


def test_ws_avatar_action_dispatched_during_stream():
    """驗證當 LLM 輸出含有動作標籤時，WebSocket 會即時推播 avatar_action 事件。"""
    async def _run():
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(["（微笑）你好啊，", "（點頭）今天過得好嗎？"])
        )
        streamers = []

        def factory(sink):
            s = FakeWebStreamer(sink=sink)
            streamers.append(s)
            return s

        app = build_app(
            WEB_TEST_CONFIG,
            brain=brain,
            refiner=FakeWebASR(""),
            asr=FakeWebASR(""),
            streamer_factory=factory,
        )

        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "text", "text": "你好"})

            actions = []
            while True:
                msg = await ws.receive(timeout=3)
                if msg.type == WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("type") == "avatar_action" and "action" in data:
                        actions.append(data.get("action"))
                    if data.get("type") == "state" and data.get("state") == "IDLE":
                        break
                elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
            await ws.close()

            assert "happy" in actions
            assert "nod" in actions
        finally:
            await client.close()

    asyncio.run(_run())
