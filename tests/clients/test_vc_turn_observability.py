"""
tests/clients/test_vc_turn_observability.py
VC-TURN-OBS-1 — 語音回合生命週期全面可見化（含 barge 取消路徑）的驗收測試。

斷言（對應工單驗收定義）：
  (a) 回合被 barge 取消 → 產生 WARNING `[UTT] reply-cancelled gen=.. curr=..`（+ barge 記錄）
  (b) 世代不符 early-return → 產生 WARNING `[UTT] reply-superseded gen=.. curr=..`
  (c) 前端 THINKING 逾 8 秒 → 升級等待提示（具名常數 + 還原邏輯），純文字 0 後端風險

全離線：注入 fake brain/refiner/asr/streamer，0 外部網路（僅本機 127.0.0.1:1 拒絕連線測試）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.asr_refiner import AsrRefiner
from clients.voice_companion.web_server import WebSession, build_app
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


class BlockingStreamer(FakeWebStreamer):
    """feed_text_piece 阻擋在 threading.Event→ 讓 _finish 卡在 asyncio.to_thread（LLM 同步段）。"""

    def __init__(self, sink=None):
        super().__init__(sink)
        self.first_piece = threading.Event()
        self.release = threading.Event()

    def feed_text_piece(self, piece: str):
        self.fed.append(piece)
        self.first_piece.set()
        self.release.wait(timeout=5)


class FakeWebASR:
    def __init__(self, text="雷姆，你怎麼了"):
        self.text = text
        self.calls = 0

    def transcribe(self, wav_bytes):
        self.calls += 1
        return self.text


def _records_with(caplog, tag: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if tag in r.getMessage()]


def test_barge_cancel_produces_reply_cancelled_log(caplog):
    """回合被 interrupt barge 取消 → WARNING reply-cancelled + barge 記錄（取消路徑可見化）。"""
    caplog.set_level(logging.INFO, logger="vc.web_server")

    streamer = BlockingStreamer()

    async def _run():
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["先說一句，", "再說第二句。"]))
        app = build_app(
            WEB_TEST_CONFIG,
            brain=brain,
            refiner=AsrRefiner(llm_call=None),
            asr=FakeWebASR(),
            streamer_factory=lambda sink: streamer,
        )
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "text", "text": "會被中斷的話"})
            # 等待進入 SPEAKING 且 LLM 首 token 已被 feed（_finish 確定卡在 to_thread）
            while True:
                m = await ws.receive(timeout=2)
                if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "SPEAKING":
                    break
            await asyncio.to_thread(streamer.first_piece.wait, 2)
            assert streamer.first_piece.is_set(), "LLM 首 token 應已 feed（_finish 卡在 to_thread）"
            # interrupt → _barge：generation 1→2 + reply_task.cancel()
            await ws.send_json({"type": "interrupt"})
            while True:
                m = await ws.receive(timeout=2)
                if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "IDLE":
                    break
            await asyncio.sleep(0.05)  # 讓被取消的 task 跑完 except 處理（reply-cancelled log）
            streamer.release.set()  # 放行背景執行緒收尾
            await ws.close()
        finally:
            await client.close()

    asyncio.run(_run())

    # 注意：ws.close() → session.close() 也會 _barge()（預設 reason=interrupt），故只鎖定 gen:1->2 那筆
    barge_records = [
        r for r in _records_with(caplog, "[UTT] barge reason=interrupt")
        if "gen:1->2" in r.getMessage()
    ]
    assert len(barge_records) == 1, "interrupt 必須剛好一筆 barge 記錄 (gen:1->2)"
    assert "gen:1->2" in barge_records[0].getMessage()

    cancelled = _records_with(caplog, "[UTT] reply-cancelled")
    assert len(cancelled) == 1, "取消必須留下 reply-cancelled 記錄"
    msg = cancelled[0].getMessage()
    assert cancelled[0].levelno == logging.WARNING, "取消類必須 WARNING 級別"
    assert "gen=1" in msg and "curr=2" in msg

    # 正常生命週期（INFO）也應留痕：reply-start / reply-awaiting-llm
    assert len(_records_with(caplog, "[UTT] reply-start")) == 1
    assert len(_records_with(caplog, "[UTT] reply-awaiting-llm")) == 1


def test_stale_generation_early_return_produces_reply_superseded_log(caplog):
    """世代不符 early-return（_run_reply 進 task 前守衛）→ WARNING reply-superseded。"""
    caplog.set_level(logging.WARNING, logger="vc.web_server")

    async def _run():
        session = WebSession(
            ws=object(),
            config=WEB_TEST_CONFIG,
            brain=object(),
            refiner=object(),
            asr=object(),
            streamer=FakeWebStreamer(),
            detector=object(),
            sink=object(),
        )
        session._generation = 7  # 模擬舊回合已被 barge 推進世代
        await session._run_reply("過期的話", gen=3, t_start=1.0, t_asr_done=1.0)

    asyncio.run(_run())

    superseded = _records_with(caplog, "[UTT] reply-superseded")
    assert len(superseded) == 1, "世代不符 early-return 必須留下 reply-superseded 記錄"
    record = superseded[0]
    assert record.levelno == logging.WARNING, "丟棄類必須 WARNING 級別"
    msg = record.getMessage()
    assert "gen=3" in msg and "curr=7" in msg and "stage=pre-task" in msg


def test_llm_stream_error_log_carries_elapsed_ms(caplog):
    """akane build_llm_stream 異常路徑 → [LLM-STREAM] error 含 elapsed_ms（本機拒絕連線，0 外部網路）。"""
    caplog.set_level(logging.ERROR, logger="soul_os.vc_brain")

    from clients.voice_companion.akane_voice_brain import build_llm_stream

    # 127.0.0.1:1 → 立即 ECONNREFUSED（本機、無外部網路）
    stream = build_llm_stream({"endpoint": "http://127.0.0.1:1/v1", "model": "m", "api_key": "k"})
    assert stream is not None

    def _drain():
        try:
            list(stream([{"role": "user", "content": "hi"}]))
        except Exception:
            pass  # 連線失敗即為預期；異常路徑已由 log 覆蓋

    asyncio.run(asyncio.to_thread(_drain))

    errors = _records_with(caplog, "[LLM-STREAM] error")
    assert len(errors) == 1, "LLM 串流異常必須留痕"
    msg = errors[0].getMessage()
    assert "elapsed_ms=" in msg
    assert "exc_type=" in msg


def test_ui_thinking_slow_escalation_markers():
    """前端 THINKING 逾 8 秒升級提示：具名常數 + 提示文字 + 還原邏輯（setState 既有機制）。"""
    assert "THINKING_SLOW_MS = 8000" in HTML_PAGE
    assert "上游較慢，還在處理" in HTML_PAGE
    assert "thinkingSlowTimer" in HTML_PAGE
    assert "clearTimeout(thinkingSlowTimer)" in HTML_PAGE