"""
tests/clients/test_vc_turn_failure_visibility.py
VC-NOREPLY-1 — 消除 VC 回合「靜默死亡」的驗收測試。

斷言（對應工單驗收定義）：
  (a) LLM 回合最終失敗 → 有 ERROR 級別日誌（[VC-TURN-FAIL]，含 agent_id / 回合標識 gen /
      總耗時 elapsed_ms / 例外型別 exc_type）
  (b) 有一則 error WS 訊息送給 client（沿用既有 {"type":"error","message":...} 協定，
      內容「連線異常，請再說一次」）
  (c) 回合有明確結束路徑（state 回到 IDLE，不靜默 return）

涵蓋兩種「最終失敗」：
  - 例外逃逸：模擬上游必逾時（TimeoutError，型別名與 httpx/requests 的 ReadTimeout 同構）
  - 回傳空值：LLM 串流 0 token → 無法產生回覆

全離線：注入 fake brain/refiner/asr/streamer，0 網路。
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.asr_refiner import AsrRefiner
from clients.voice_companion.web_server import TURN_FAILURE_HINT, build_app
from clients.voice_companion.web_ui import HTML_PAGE

WEB_TEST_CONFIG = {
    "companion": {"id": "agent_rem", "display_name": "雷姆", "short_name": "雷姆"},
    "fish_audio": {
        "api_key": "fake-key", "voice_id": "v", "model": "s2.1-pro-free",
        "mode": "live", "live_format": "pcm",
        "asr_endpoint": "https://api.fish.audio/v1/asr",
        "tts_ws_endpoint": "wss://api.fish.audio/v1/tts/live",
    },
    "stt": {"engine": "fish", "language": "zh"},
    "vad": {"sample_rate": 16000, "silence_threshold_sec": 0.05, "energy_threshold": 0.05},
    "llm": {"endpoint": "", "api_key": ""},
    "web": {"host": "127.0.0.1", "port": 8767},
}


class FakeWebStreamer:
    """記錄呼叫的偽 TTS streamer（介面：start/feed_text_piece/end_session/interrupt/close）。"""

    def __init__(self, sink=None):
        self.sink = sink
        self.fed: list[str] = []
        self.interrupt_calls = 0
        self.end_calls = 0
        self.start_calls = 0

    def start(self):
        self.start_calls += 1

    def feed_text_piece(self, piece: str):
        self.fed.append(piece)

    def end_session(self):
        self.end_calls += 1

    def interrupt(self):
        self.interrupt_calls += 1

    def close(self):
        pass


class FakeWebASR:
    def __init__(self, text="雷姆，你怎麼了"):
        self.text = text
        self.calls = 0
        self.last_error: dict | None = None
        self.last_status: int | None = None

    def transcribe(self, wav_bytes):
        self.calls += 1
        return self.text


class AlwaysTimeoutBrain:
    """模擬上游必逾時：每次 LLM 呼叫都丟 TimeoutError（型別名 ReadTimeout 的類比）。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages):
        self.calls += 1
        raise TimeoutError("simulated upstream timeout (network ReadTimeout)")


async def _run_turn(brain, caplog) -> tuple[list[dict], list[logging.LogRecord]]:
    """打字路徑驅動一整個回合，回傳 (WS 事件清單, ERROR 級別日誌記錄)。"""
    caplog.set_level(logging.ERROR, logger="vc.web_server")
    app = build_app(
        WEB_TEST_CONFIG,
        brain=brain,
        refiner=AsrRefiner(llm_call=None),  # 離線確定性淨化
        asr=FakeWebASR(),
        streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    events: list[dict] = []
    try:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "text", "text": "雷姆，你怎麼了"})
        while True:
            msg = await ws.receive(timeout=3)
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                events.append(data)
                if data.get("type") == "state" and data.get("state") == "IDLE":
                    break
            elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                break
        await ws.close()
    finally:
        await client.close()
    fail_records = [
        r for r in caplog.records
        if r.levelno == logging.ERROR and "[VC-TURN-FAIL]" in r.getMessage()
    ]
    return events, fail_records


def test_llm_exception_surfaces_error_log_message_and_idle(caplog):
    """例外逃逸（模擬上游 ReadTimeout）→ (a) ERROR 日誌 (b) error 訊息 (c) 回 IDLE。"""
    brain = AkaneVoiceBrain(llm_stream=AlwaysTimeoutBrain())
    events, fail_records = asyncio.run(_run_turn(brain, caplog))

    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1, "回合失敗必須剛好一則 error 訊息"
    assert errors[0]["message"] == TURN_FAILURE_HINT

    states = [e["state"] for e in events if e.get("type") == "state"]
    assert states[-1] == "IDLE", "回合必須有明確結束路徑（回 IDLE，不靜默）"

    assert len(fail_records) == 1, "必須有且僅有一筆 [VC-TURN-FAIL] ERROR 日誌"
    msg = fail_records[0].getMessage()
    assert "agent_id=agent_rem" in msg
    assert "gen=" in msg
    assert "elapsed_ms=" in msg
    assert "exc_type=TimeoutError" in msg


def test_llm_empty_reply_surfaces_error_log_message_and_idle(caplog):
    """LLM 回傳空值（0 token）→ (a) ERROR 日誌 (b) error 訊息 (c) 回 IDLE。"""
    brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(()))  # 串流 0 token
    events, fail_records = asyncio.run(_run_turn(brain, caplog))

    errors = [e for e in events if e.get("type") == "error"]
    assert len(errors) == 1, "空值回合必須剛好一則 error 訊息"
    assert errors[0]["message"] == TURN_FAILURE_HINT

    states = [e["state"] for e in events if e.get("type") == "state"]
    assert states[-1] == "IDLE", "回合必須有明確結束路徑（回 IDLE，不靜默）"

    assert len(fail_records) == 1, "必須有且僅有一筆 [VC-TURN-FAIL] ERROR 日誌"
    msg = fail_records[0].getMessage()
    assert "agent_id=agent_rem" in msg
    assert "gen=" in msg
    assert "elapsed_ms=" in msg
    assert "exc_type=empty_llm_output" in msg


def test_ui_renders_turn_failure_error_msg():
    """前端處理 error 型別並在畫面上呈現（非僅 console.log）：#errorBox + 聊天區系統列。"""
    assert 'msg.type === "error"' in HTML_PAGE
    assert "showError(msg.message || \"錯誤\")" in HTML_PAGE      # #errorBox 暫態顯示（既有）
    assert 'addMsg("error"' in HTML_PAGE                          # 聊天區系統列（VC-NOREPLY-1）
    assert ".msg.error" in HTML_PAGE                               # 系統列樣式存在
    assert "系統" in HTML_PAGE                                     # 系統列署名