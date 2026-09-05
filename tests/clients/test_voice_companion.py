"""
tests/clients/test_voice_companion.py — VC-1 黑川茜即時語音伴侶客戶端驗收測試。

對應工單 §4（單元 + 全鏈路 Mock，外部網路與音訊硬體一律 Mock）：
- Test 1（ASR 淨化）：錯字/贅詞修復 + 雜音熔斷 → None
- Test 2（語音大腦格式審計）：0 Markdown / 0 括號動作人物 + 分句器即時切分
- Test 3（Fish Audio 請求構造）：Payload（text/reference_id/format）+ Bearer Token
- Test 4（Barge-in 打斷）：3 分片佇列 → interrupt() 即清空且播放狀態重置
- 附加：VAD wiring + 全鏈路離線管線驗證

執行：.venv\\Scripts\\python.exe -m pytest tests/clients/test_voice_companion.py -v
全離線：不呼叫 Fish Audio / LLM / 麥克風 / 音效卡。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import msgpack

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.akane_live import VoiceCompanionApp, create_tts_streamer
from clients.voice_companion.akane_voice_brain import (
    AKANE_LAYER3_PERSONA,
    AKANE_VOICE_INVARIANTS,
    ClauseSplitter,
    AkaneVoiceBrain,
    contains_markdown_chars,
)
from clients.voice_companion.asr_refiner import AsrRefiner, refine_speech_text
from clients.voice_companion.env_config import apply_env_overrides, load_dotenv, resolve_config
from clients.voice_companion.fish_tts_streamer import FishTTSStreamer
from clients.voice_companion.fish_tts_live import FishTTSError, FishTTSLiveStreamer
from clients.voice_companion.stt_service import FishASRService, pcm16_to_wav_bytes
from clients.voice_companion.vad_listener import VADListener, VoiceActivityDetector

VOICE_ENDPOINT = "https://api.fish.audio/v1/tts"


# ─────────────────────────────────────────────────────────────
# 共用 Mock（外部網路 / 音訊硬體）
# ─────────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, content: bytes = b"\x00\x03MP3FAKE", status_code: int = 200, text: str = ""):
        self.content = content
        self.status_code = status_code
        self.text = text


class FakeSession:
    """記錄 post 呼叫並回傳假 mp3 的偽 requests.Session。"""

    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse()


class ErrSession(FakeSession):
    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(content=b"", status_code=500, text="server boom")


class FakeAudioDevice:
    """偽音效卡：記錄 play/stop 呼叫。"""

    def __init__(self):
        self.played = []
        self.stop_calls = 0

    def play(self, chunk: bytes):
        self.played.append(chunk)

    def stop(self):
        self.stop_calls += 1


class FakeASRSession:
    """記錄 multipart post 呼叫的偽 requests.Session（Fish ASR 用）。"""

    def __init__(self, payload: dict | None = None, status_code: int = 200, error: Exception | None = None):
        self.calls = []
        self.payload = payload or {}
        self.status_code = status_code
        self.error = error

    def post(self, url, files=None, data=None, headers=None, timeout=None):
        self.calls.append({"url": url, "files": files, "data": data, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return FakeASRResponse(payload=self.payload, status_code=self.status_code)


class FakeASRResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


# ─────────────────────────────────────────────────────────────
# Test 1：ASR 淨化
# ─────────────────────────────────────────────────────────────

class TestAsrRefiner:
    def test_akane_homophone_recovery(self):
        """「欠...那个...今天好累」→「茜，今天好累。」（錯字 + 贅詞 + 標點）"""
        assert refine_speech_text("欠...那个...今天好累") == "茜，今天好累。"

    def test_more_homophone_and_filler_cases(self):
        assert refine_speech_text("排成记得下午开会") == "排程记得下午开会。"
        assert refine_speech_text("小欠......想你了") == "小茜，想你了。"
        assert refine_speech_text("西，你回來啦") == "茜，你回來啦。"
        assert refine_speech_text("呃...那個...就是說...嗯") is None  # 全贅詞 → 淨化後為空 → DROP
        assert refine_speech_text("嗯，我知道了") == "我知道了。"

    def test_noise_fuse_returns_none(self):
        """純雜音（感嘆詞/嘆氣/咳嗽）熔斷 → None（DROP 不打擾）"""
        assert refine_speech_text("呃...呼...") is None
        assert refine_speech_text("") is None
        assert refine_speech_text("     ") is None
        assert refine_speech_text("啊...唉") is None
        assert refine_speech_text("嗯，") is None
        assert refine_speech_text("...") is None

    def test_llm_channel_mock_empty_verdict(self):
        """LLM 通道可注入；輸出 EMPTY → 熔斷 None"""
        refiner = AsrRefiner(llm_call=lambda prompt: "EMPTY")
        assert refiner.refine_speech_text("啊...嗯") is None

    def test_llm_channel_mock_repaired_text(self):
        """LLM 通道可注入；回傳修復文本直接採用"""
        refiner = AsrRefiner(llm_call=lambda prompt: "排成好了，Bryan。")
        assert refiner.refine_speech_text("欠...那个...排成好了") == "排成好了，Bryan。"


# ─────────────────────────────────────────────────────────────
# Test 2：語音大腦格式審計
# ─────────────────────────────────────────────────────────────

class TestAkaneVoiceBrain:
    FORBIDDEN = "*#[(（)"

    def test_persona_embedded_layer3(self):
        """內嵌 Layer 3 Persona：稱呼 Bryan、短句克制；守門含 0 Markdown / 0 括號描寫"""
        assert "Bryan" in AKANE_LAYER3_PERSONA
        assert "Layer 3" in AKANE_LAYER3_PERSONA
        assert "留下" in AKANE_LAYER3_PERSONA  # Layer 3＝分析完了仍然留下
        assert "Markdown" in AKANE_VOICE_INVARIANTS
        assert "括號動作" in AKANE_VOICE_INVARIANTS
        assert "Bryan" in AKANE_VOICE_INVARIANTS

    def test_output_100_percent_free_of_markdown(self):
        """茜的輸出 100% 不含 *、#、[、(、（、） 等符號"""
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["**Bryan**，我", "（輕聲說）在。#今天# [很累]"]))
        out = brain.respond("今天好累")
        assert out, "回應不應為空"
        assert not contains_markdown_chars(out)
        assert all(ch not in out for ch in self.FORBIDDEN)
        assert "Bryan" in out

    def test_stream_output_guarded_token_by_token(self):
        """串流輸出逐 token 過守門，拼接後 0 Markdown"""
        tokens = ["- Bryan，", "（停頓）今晚月色真美，", "你覺得呢？"]
        parts = list(AkaneVoiceBrain(llm_stream=lambda msgs: iter(tokens)).stream_respond("問"))
        joined = "".join(parts)
        assert joined
        assert not contains_markdown_chars(joined)
        assert all(ch not in joined for ch in self.FORBIDDEN)

    def test_clause_splitter_splits_immediately_on_comma_period(self):
        """分句器：遇到逗號/句號（且字數 ≥ 4）即時切分"""
        splitter = ClauseSplitter()
        clauses: list[str] = []
        for tok in list("今晚月色真美，你覺得呢。"):  # 逐字模擬 streaming token
            clauses.extend(splitter.feed(tok))
        assert clauses == ["今晚月色真美，", "你覺得呢。"]

    def test_clause_splitter_min_length_hold(self):
        """字數未達 4 先不切（「好。」hold 住，補足後在後續標點切出）"""
        splitter = ClauseSplitter(min_chars=4)
        assert splitter.feed("好。") == []
        assert splitter.feed("累") == []
        assert splitter.feed("！") == ["好。累！"]

    def test_clause_splitter_stream_and_newline(self):
        """split_stream：\n 亦為切點；結尾 flush 尾部"""
        splitter = ClauseSplitter()
        out = list(splitter.split_stream(["先說一句", "長的話。\n再來", "短的！", "尾"]))
        assert out == ["先說一句長的話。", "再來短的！", "尾"]

    def test_clause_splitter_exclamation_and_ellipsis(self):
        splitter = ClauseSplitter()
        out = list(splitter.split_stream(["好累啊！", "明天再說……"]))
        assert out == ["好累啊！", "明天再說……"]


# ─────────────────────────────────────────────────────────────
# Test 3：Fish Audio 請求構造
# ─────────────────────────────────────────────────────────────

class TestFishTTSStreamer:
    def test_payload_contains_text_reference_model_and_bearer(self):
        """Payload 正確包含 text/reference_id/format/model，Header 帶 Bearer Token"""
        session = FakeSession()
        streamer = FishTTSStreamer(api_key="fish-key-123", voice_id="akane-voice-9", model="s2.1-pro-free", session=session)
        audio = streamer.synthesize("你好，Bryan。")

        assert audio == b"\x00\x03MP3FAKE"
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["url"] == VOICE_ENDPOINT
        assert call["json"] == {
            "text": "你好，Bryan。",
            "reference_id": "akane-voice-9",
            "format": "mp3",
            "model": "s2.1-pro-free",
        }
        assert call["headers"]["Authorization"] == "Bearer fish-key-123"
        assert call["headers"]["Content-Type"] == "application/json"

    def test_http_error_raises_fish_tts_error(self):
        session = ErrSession()
        streamer = FishTTSStreamer(api_key="k", voice_id="v", session=session)
        try:
            streamer.synthesize("測試")
            assert False, "應拋出 FishTTSError"
        except Exception as exc:
            assert "500" in str(exc)


# ─────────────────────────────────────────────────────────────
# Test 3b：Fish Audio WebSocket TTS-Live（VC-1.2，全離線 Mock）
# ─────────────────────────────────────────────────────────────

class FakeLiveSocket:
    """預錄 msgpack 訊息序列回放的偽 WebSocket：記錄 send / close。

    recv() 依序回放 replies（msgpack 封裝）；after_recv 回呼可模擬
    「接收循環中 interrupt 從另一執行緒觸發」；已關閉或回放完畢 → None（連線結束語意）。
    """

    def __init__(self, replies=None, after_recv=None):
        self.replies = [msgpack.packb(r) for r in (replies or [])]
        self.after_recv = after_recv
        self.sent: list[bytes] = []
        self.close_calls = 0
        self.closed = False

    def send(self, data: bytes):
        self.sent.append(data)

    def recv(self):
        if self.after_recv is not None:
            self.after_recv()
        if self.closed or not self.replies:
            return None
        return self.replies.pop(0)

    def close(self):
        self.closed = True
        self.close_calls += 1


class FakeLivePlayer:
    """偽音效卡（對應 sounddevice OutputStream 介面）：記錄 open/write/stop/close 呼叫。"""

    def __init__(self):
        self.written: list[bytes] = []
        self.open_calls = 0
        self.stop_calls = 0
        self.close_calls = 0

    def open(self):
        self.open_calls += 1

    def write(self, chunk: bytes):
        self.written.append(chunk)

    def stop(self):
        self.stop_calls += 1

    def close(self):
        self.close_calls += 1


class TestFishTTSLiveStreamer:
    def test_start_payload_exact(self):
        """start 訊息 payload 精確：event=start，request 含 reference_id/format=pcm/sample_rate/chunk_length/latency"""
        sock = FakeLiveSocket([{"event": "finish", "reason": "stop"}])
        streamer = FishTTSLiveStreamer(
            api_key="fish-key-123", voice_id="akane-voice-9", model="s2.1-pro-free",
            socket_factory=lambda: sock, audio_player=FakeLivePlayer(),
        )
        streamer.speak(["你好，Bryan。"])

        assert len(sock.sent) == 4  # start / text / flush / stop
        start_msg = msgpack.unpackb(sock.sent[0])
        assert start_msg["event"] == "start"
        req = start_msg["request"]
        assert req["reference_id"] == "akane-voice-9"
        assert req["format"] == "pcm"
        assert req["sample_rate"] == 44100
        assert req["chunk_length"] == 300
        assert req["latency"] == "normal"
        assert req["text"] == ""

    def test_text_flush_stop_sequence(self):
        """多個 text 依分句順序送出；flush 與 stop 次序正確"""
        sock = FakeLiveSocket([{"event": "finish", "reason": "stop"}])
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        streamer.speak(["第一句。", "第二句。", "第三句。"])

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        assert [m["event"] for m in msgs] == ["start", "text", "text", "text", "flush", "stop"]
        assert [m["text"] for m in msgs if m["event"] == "text"] == ["第一句。", "第二句。", "第三句。"]

    def test_audio_chunks_played_in_order_then_finish(self):
        """audio 分片依序餵給 player；finish(stop) 正常結束 session（連線關閉、無錯誤）"""
        player = FakeLivePlayer()
        sock = FakeLiveSocket([
            {"event": "audio", "audio": b"\x01" * 200},
            {"event": "audio", "audio": b"\x02" * 400},
            {"event": "finish", "reason": "stop"},
        ])
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        streamer.speak(["測試"])

        assert player.written == [b"\x01" * 200, b"\x02" * 400], "audio 分片必須依序邊收邊播"
        assert sock.close_calls == 1, "finish(stop) 後 session 結束、連線關閉"
        assert streamer.last_error is None

    def test_finish_error_records_error_and_stops_playback(self):
        """finish reason=error → 停止播放、關閉連線、記錄錯誤，不 crash、不重試"""
        player = FakeLivePlayer()
        sock = FakeLiveSocket([{"event": "finish", "reason": "error", "message": "boom"}])
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        streamer.speak(["測試"])  # 不應拋出

        assert isinstance(streamer.last_error, FishTTSError)
        assert "error" in str(streamer.last_error)
        assert player.stop_calls == 1, "finish error 必須停止播放"
        assert sock.close_calls == 1, "finish error 後連線必須關閉"

    def test_interrupt_during_session_closes_socket_and_stops_player(self):
        """speak 接收循環中 interrupt（模擬 VAD barge-in）→ socket 立即關閉、player 停止、狀態重置"""
        player = FakeLivePlayer()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        sock = FakeLiveSocket(
            [{"event": "audio", "audio": b"\x7f" * 300}, {"event": "audio", "audio": b"\x7e" * 300}],
            after_recv=streamer.interrupt,  # 第一次 recv 時觸發 barge-in
        )
        streamer.speak(["被打斷"])

        assert sock.close_calls == 1, "interrupt 必須立即關閉 WS 連線（中止合成）"
        assert player.stop_calls == 1, "interrupt 必須立即停止播放"
        assert streamer.queue_size == 0, "interrupt 必須清空佇列"
        assert streamer.pending_chunks() == []
        assert streamer.is_playing is False, "播放狀態必須重置"
        assert streamer.interrupt_event.is_set()

    def test_interrupt_clears_queued_chunks(self):
        """播放中佇列已有分片 → interrupt() 立即清空"""
        player = FakeLivePlayer()
        sock = FakeLiveSocket([{"event": "finish", "reason": "stop"}])
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        streamer._chunks.append(b"queued-1")
        streamer._chunks.append(b"queued-2")

        streamer.interrupt()

        assert streamer.queue_size == 0
        assert streamer.pending_chunks() == []

    def test_speak_noop_after_interrupt(self):
        """interrupt 後 speak() no-op：不建連線、0 訊息送出"""
        player = FakeLivePlayer()
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        streamer.interrupt()

        streamer.speak(["還有話說"])

        assert sock.sent == [], "interrupt 後不得送出任何協定訊息"
        assert sock.close_calls == 0, "interrupt 後不得建立/關閉新連線"

    def test_close_releases_ws_and_player(self):
        """close()：關閉播放器、狀態鎖死，close 後 speak() no-op"""
        player = FakeLivePlayer()
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=player)
        streamer.close()

        assert player.close_calls == 1
        streamer.speak(["關閉後"])
        assert sock.sent == []

    def test_feed_text_piece_no_punctuation_no_flush(self):
        """feed_text_piece：無標點 piece → 只送 text，不觸發 flush（自動開 session）"""
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        streamer.feed_text_piece("今天天")

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        assert [m["event"] for m in msgs] == ["start", "text"]
        assert msgs[1]["text"] == "今天天"

    def test_feed_text_piece_punctuation_triggers_flush(self):
        """feed_text_piece：含標點（，。、！？…\\n）的 piece → text 後立即送 flush"""
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        for piece in ["你好，", "今天好累。", "等一下！"]:
            streamer.feed_text_piece(piece)

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        assert [m["event"] for m in msgs] == ["start", "text", "flush", "text", "flush", "text", "flush"]
        assert [m["text"] for m in msgs if m["event"] == "text"] == ["你好，", "今天好累。", "等一下！"]

    def test_end_session_flush_stop_finish(self):
        """串流 feed 收尾：flush → stop → 等 finish → 關連線"""
        sock = FakeLiveSocket([{"event": "finish", "reason": "stop"}])
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        streamer.feed_text_piece("最後一句")
        streamer.end_session()

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        assert [m["event"] for m in msgs] == ["start", "text", "flush", "stop"]
        assert sock.close_calls == 1, "end_session 後連線必須關閉"

    def test_feed_text_piece_noop_after_interrupt(self):
        """feed 模式 interrupt 後：feed_text_piece / end_session 皆 no-op（0 訊息送出）"""
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        streamer.interrupt()

        streamer.feed_text_piece("被打斷後")
        streamer.end_session()

        assert sock.sent == [], "interrupt 後 feed 不得送出任何訊息"

    def test_mode_live_selects_live_streamer(self):
        cfg = {"fish_audio": {"api_key": "k", "voice_id": "v", "model": "s2.1-pro-free", "mode": "live", "live_format": "pcm"}}
        assert isinstance(create_tts_streamer(cfg), FishTTSLiveStreamer)

    def test_mode_rest_selects_rest_streamer(self):
        cfg = {"fish_audio": {"api_key": "k", "voice_id": "v", "model": "s2.1-pro-free", "mode": "rest"}}
        assert isinstance(create_tts_streamer(cfg), FishTTSStreamer)

    def test_mode_default_is_live(self):
        cfg = {"fish_audio": {"api_key": "k", "voice_id": "v"}}
        assert isinstance(create_tts_streamer(cfg), FishTTSLiveStreamer)


# ─────────────────────────────────────────────────────────────
# Test 3c：Fish Audio 官方 ASR（VC-1.1 輸入側，全離線 Mock）
# ─────────────────────────────────────────────────────────────

class TestFishASRService:
    ASR_URL = "https://api.fish.audio/v1/asr"

    def test_transcribe_200_returns_text(self):
        """200 → resp.json().text.strip()；multipart 構造（files/data/headers/timeout）精確斷言"""
        session = FakeASRSession(payload={"text": "  你好，Bryan。  "})
        svc = FishASRService(api_key="asr-key-1", session=session)
        out = svc.transcribe(b"RIFF....WAVE-data")

        assert out == "你好，Bryan。"
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["url"] == self.ASR_URL
        assert call["headers"]["Authorization"] == "Bearer asr-key-1"
        assert call["data"] == {"language": "zh"}
        assert call["timeout"] == 10.0
        fname, fdata, ftype = call["files"]["audio"]
        assert fname == "speech.wav"
        assert fdata == b"RIFF....WAVE-data"
        assert ftype == "audio/wav"

    def test_transcribe_non200_returns_empty(self):
        session = FakeASRSession(payload={}, status_code=500)
        svc = FishASRService(api_key="k", session=session)
        assert svc.transcribe(b"wav-bytes") == ""

    def test_transcribe_network_error_returns_empty(self):
        """網路異常 → ""（0 崩潰）"""
        session = FakeASRSession(error=RuntimeError("connection boom"))
        svc = FishASRService(api_key="k", session=session)
        assert svc.transcribe(b"wav-bytes") == ""

    def test_transcribe_empty_input_returns_empty(self):
        svc = FishASRService(api_key="k", session=FakeASRSession(payload={"text": "x"}))
        assert svc.transcribe(b"") == ""
        assert svc.transcribe(None) == ""

    def test_transcribe_invalid_json_returns_empty(self):
        """非 200 外的失敗（json 解析失敗）→ ""（0 崩潰）"""
        session = FakeASRSession(payload={"text": "x"})

        class BadJsonResponse(FakeASRResponse):
            def json(self):
                raise ValueError("bad json")

        session.post = lambda url, files=None, data=None, headers=None, timeout=None: (session.calls.append({"url": url}) or BadJsonResponse(payload={}))
        svc = FishASRService(api_key="k", session=session)
        assert svc.transcribe(b"wav-bytes") == ""


class TestPcm16ToWav:
    def test_header_roundtrip_correct(self):
        """pcm16_to_wav_bytes：RIFF/WAVE/fmt PCM16 單聲道、data size 正確、內容可 wave 讀回"""
        pcm = bytes(range(0, 256)) * 50  # 12800 bytes = 6400 frames @16k（0.4s）
        wav = pcm16_to_wav_bytes(pcm, sample_rate=16000)

        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"
        assert wav[12:16] == b"fmt "
        # RIFF size = 檔案長度 - 8
        assert int.from_bytes(wav[4:8], "little") == len(wav) - 8
        # fmt chunk：audio format=PCM(1)、channels=1、sample width=2
        assert int.from_bytes(wav[20:22], "little") == 1  # PCM
        assert int.from_bytes(wav[22:24], "little") == 1  # mono
        assert int.from_bytes(wav[24:28], "little") == 16000
        assert int.from_bytes(wav[34:36], "little") == 16  # bits per sample 16（sampwidth 2 × 8）

        import io
        import wave as wave_mod

        with wave_mod.open(io.BytesIO(wav)) as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.getnframes() == 6400
            assert wf.readframes(6400) == pcm  # 資料位元組不變

    def test_empty_input_returns_empty(self):
        assert pcm16_to_wav_bytes(b"") == b""


# ─────────────────────────────────────────────────────────────
# Test 4：Barge-in 打斷
# ─────────────────────────────────────────────────────────────

class TestBargeIn:
    def test_interrupt_clears_queue_and_resets_state(self):
        """播放佇列 3 個音訊分片 → interrupt() → 佇列立即清空、播放狀態重置"""
        session = FakeSession()
        device = FakeAudioDevice()
        streamer = FishTTSStreamer(api_key="k", voice_id="v", session=session, audio_device=device)

        streamer.enqueue_audio(b"chunk-1")
        streamer.enqueue_audio(b"chunk-2")
        streamer.enqueue_audio(b"chunk-3")
        assert streamer.queue_size == 3

        streamer.interrupt()

        assert streamer.queue_size == 0, "佇列必須立即清空"
        assert streamer.pending_chunks() == []
        assert device.stop_calls == 1, "必須立即停止音訊輸出（sd.stop）"
        assert streamer.is_playing is False, "播放狀態必須重置"
        assert streamer.interrupt_event.is_set()
        assert streamer._cancel_event.is_set()

    def test_interrupt_cancels_queued_http_requests(self):
        """interrupt 後 speak() 為 no-op：不再發出排隊中的 HTTP 請求"""
        session = FakeSession()
        streamer = FishTTSStreamer(api_key="k", voice_id="v", session=session, audio_device=FakeAudioDevice())
        streamer.interrupt()
        streamer.speak("還有話要說")
        assert session.calls == []

    def test_resume_allows_new_synthesis(self):
        session = FakeSession()
        streamer = FishTTSStreamer(api_key="k", voice_id="v", session=session, audio_device=FakeAudioDevice())
        streamer.interrupt()
        streamer.resume()
        audio = streamer.synthesize("繼續說")
        assert audio and len(session.calls) == 1


# ─────────────────────────────────────────────────────────────
# 附加：VAD 佈線 + 全鏈路離線管線
# ─────────────────────────────────────────────────────────────

class TestVADWiring:
    @staticmethod
    def make_detector(sr: int = 16000) -> VoiceActivityDetector:
        return VoiceActivityDetector(sample_rate=sr, silence_threshold_sec=0.05, energy_threshold=0.01)

    @staticmethod
    def frames(seconds: float, sr: int = 16000, level: float = 0.5) -> list[float]:
        return [level] * int(sr * seconds)

    def test_barge_in_fired_when_akane_speaking(self):
        """茜說話中（playing_check=True）Bryan 開口 → barge_in 觸發"""
        fired: list[int] = []
        listener = VADListener(
            detector=self.make_detector(),
            stt_engine=lambda s: "茜，今天好累",
            on_transcript=lambda t: None,
            on_barge_in=lambda: fired.append(1),
            playing_check=lambda: True,
        )
        events = listener.feed_frame(self.frames(0.1))
        assert "barge_in" in events
        assert fired == [1]

    def test_utterance_end_transcript_flow(self):
        """靜音斷句 → STT → on_transcript 收到轉錄文本"""
        got: list[str] = []
        listener = VADListener(
            detector=self.make_detector(),
            stt_engine=lambda s: "茜，今天好累",
            on_transcript=lambda t: got.append(t),
            on_barge_in=lambda: None,
            playing_check=lambda: False,
        )
        listener.feed_frame(self.frames(0.1, level=0.5))   # speech_start
        listener.feed_frame(self.frames(0.03, level=0.5))  # 語音持續
        events = listener.feed_frame(self.frames(0.06, level=0.0))  # 靜音 60ms ≥ 50ms → speech_end
        assert "speech_end" in events
        assert got == ["茜，今天好累"]

    def test_utterance_end_transcribes_via_fish_asr(self):
        """VC-1.1：utterance 結束 → float32 樣本量化 PCM16 → WAV → Fish ASR transcribe → on_transcript"""
        got: list[str] = []
        session = FakeASRSession(payload={"text": "茜，今天好累"})
        listener = VADListener(
            detector=self.make_detector(),
            asr_service=FishASRService(api_key="k", session=session),
            on_transcript=lambda t: got.append(t),
            on_barge_in=lambda: None,
            playing_check=lambda: False,
        )
        listener.feed_frame(self.frames(0.1, level=0.5))   # speech_start
        listener.feed_frame(self.frames(0.03, level=0.5))  # 語音持續
        events = listener.feed_frame(self.frames(0.06, level=0.0))  # 靜音 → speech_end
        assert "speech_end" in events
        assert got == ["茜，今天好累"]
        assert session.calls[0]["files"]["audio"][0] == "speech.wav"  # pcm→wav 已發生


class TestFullChainOffline:
    def test_transcript_to_speech_pipeline(self):
        """全鏈路離線管線：原始 ASR 文本 → 淨化 → 茜回應 → TTS 請求 → 播放佇列"""
        config = {
            "fish_audio": {"api_key": "k", "voice_id": "v", "endpoint": VOICE_ENDPOINT},
            "stt": {"model_size": "base", "language": "zh"},
            "vad": {"sample_rate": 16000, "silence_threshold_sec": 0.8, "energy_threshold": 0.015},
            "dialogue": {
                "wake_words": ["茜", "小茜", "あかね", "akane"],
                "continuous_timeout_sec": 30.0,
                "ephemeral_mode": True,
            },
            "llm": {"endpoint": "http://127.0.0.1:11434/v1/chat/completions", "model": "m", "api_key": ""},
        }
        session = FakeSession()
        streamer = FishTTSStreamer(api_key="k", voice_id="v", endpoint=VOICE_ENDPOINT, session=session, audio_device=FakeAudioDevice())
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["我在。", "說說看。"]))
        app = VoiceCompanionApp(
            config,
            refiner=AsrRefiner(llm_call=None),  # 離線確定性淨化
            brain=brain,
            streamer=streamer,
            listener=None,
        )
        app._handle_transcript("欠...那个...今天好累")

        assert app.state == "LISTENING"  # 處理完回到聆聽
        assert session.calls, "TTS 合成請求必須發生"
        assert session.calls[0]["json"]["reference_id"] == "v"
        assert session.calls[0]["json"]["text"] == "我在。說說看。"  # 分句整句交給 TTS
        assert streamer.queue_size == 1  # 1 個子句 → 1 個音訊分片入隊
        assert not contains_markdown_chars(session.calls[0]["json"]["text"])

    def test_live_reply_streams_tokens_via_feed_text_piece(self):
        """VC-1.1：live streamer + LLM 串流 token → _speak_reply 走 feed_text_piece 逐 token 邊生邊送"""
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["我在，", "說說看。"]))
        app = VoiceCompanionApp({}, refiner=None, brain=brain, streamer=streamer, listener=None)

        app._speak_reply("今天好累")

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        events = [m["event"] for m in msgs]
        assert events == ["start", "text", "flush", "text", "flush", "flush", "stop"]
        assert [m["text"] for m in msgs if m["event"] == "text"] == ["我在，", "說說看。"]
        assert sock.close_calls == 1
        assert app._last_interaction > 0

    def test_no_stream_tokens_falls_back_to_speak(self):
        """無 LLM 串流能力（llm_stream None）→ live 走 speak(分句列表) 而非 feed 迴圈"""
        sock = FakeLiveSocket()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", socket_factory=lambda: sock, audio_player=FakeLivePlayer())
        brain = AkaneVoiceBrain(llm_stream=None, config={"llm": {"endpoint": "", "api_key": ""}})
        app = VoiceCompanionApp({}, refiner=None, brain=brain, streamer=streamer, listener=None)

        app._speak_reply("今天好累")

        msgs = [msgpack.unpackb(s) for s in sock.sent]
        events = [m["event"] for m in msgs]
        # llm_stream None → stream_respond yield 內建「我在。說說看。」（無標點 token → 不 flush）
        assert events[0] == "start" and events[-1] == "stop"
        assert [m["event"] for m in msgs] == ["start", "text", "flush", "stop"]


# ─────────────────────────────────────────────────────────────
# Test 5：執行期配置解析（env 覆寫 + .env 載入，全離線 Mock）
# ─────────────────────────────────────────────────────────────

class TestEnvConfig:
    def test_env_overrides_beat_config_defaults(self, monkeypatch):
        """os.environ 優先於 config.json：五個覆寫鍵全部生效（FISH_MODEL 不存在）"""
        cfg = {
            "fish_audio": {"api_key": "", "voice_id": "4c11d21b14284d428074f76a1cf32298", "model": "s2.1-pro-free"},
            "llm": {"endpoint": "https://ollama.com/v1", "model": "deepseek-v4-flash:0731", "api_key": ""},
        }
        monkeypatch.setenv("FISH_API_KEY", "env-fish-key")
        monkeypatch.setenv("FISH_VOICE_ID", "env-voice-id")
        monkeypatch.setenv("LLM_BASE_URL", "https://ollama.com/v1")
        monkeypatch.setenv("LLM_API_KEY", "env-llm-key")
        monkeypatch.setenv("LLM_MODEL", "env-model")
        monkeypatch.delenv("FISH_MODEL", raising=False)  # 拍板：不支援 FISH_MODEL 覆寫
        resolved = apply_env_overrides(cfg)
        assert resolved["fish_audio"]["api_key"] == "env-fish-key"
        assert resolved["fish_audio"]["voice_id"] == "env-voice-id"
        assert resolved["fish_audio"]["model"] == "s2.1-pro-free"  # config 默認保留（無 FISH_MODEL 覆寫）
        assert resolved["llm"]["endpoint"] == "https://ollama.com/v1"
        assert resolved["llm"]["api_key"] == "env-llm-key"
        assert resolved["llm"]["model"] == "env-model"

    def test_missing_env_keeps_config_defaults(self, monkeypatch):
        """環境變數缺席時 config.json 默認值原樣保留（含空 api_key 不被污染）"""
        for env_name in ("FISH_API_KEY", "FISH_VOICE_ID", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
            monkeypatch.delenv(env_name, raising=False)
        cfg = {
            "fish_audio": {"api_key": "", "voice_id": "4c11d21b14284d428074f76a1cf32298", "model": "s2.1-pro-free"},
            "llm": {"endpoint": "https://ollama.com/v1", "model": "deepseek-v4-flash:0731", "api_key": ""},
        }
        resolved = apply_env_overrides(cfg)
        assert resolved == cfg

    def test_dotenv_loaded_but_existing_env_wins(self, tmp_path, monkeypatch):
        """.env 載入變數；已存在的環境變數不被 .env 覆蓋"""
        env_file = tmp_path / ".env"
        env_file.write_text(
            "FISH_API_KEY=dotenv-fish\n"
            "# 註解行\n"
            "FISH_VOICE_ID=\"dotenv-voice\"\n"
            "LLM_MODEL=dotenv-model\n",
            encoding="utf-8",
        )
        # 確保 .env 目標鍵不在環境中（自包含，且 test teardown 會清理載入的鍵）
        monkeypatch.delenv("FISH_VOICE_ID", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.setenv("FISH_API_KEY", "existing-fish")  # 已存在 → 不覆蓋
        loaded = load_dotenv(str(env_file))
        assert loaded is True
        assert os.environ["FISH_API_KEY"] == "existing-fish"
        assert os.environ["FISH_VOICE_ID"] == "dotenv-voice"
        assert os.environ["LLM_MODEL"] == "dotenv-model"

    def test_dotenv_missing_is_noop(self, tmp_path):
        assert load_dotenv(str(tmp_path / "nope.env")) is False

    def test_resolve_config_full_pipeline(self, tmp_path, monkeypatch):
        """.env 載入 + env 覆寫完整流程：env > .env > config 默認"""
        # 自包含隔離：測試過程載入/設定到的鍵在 teardown 全部清理
        monkeypatch.delenv("FISH_VOICE_ID", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("FISH_VOICE_ID=dotenv-voice\n", encoding="utf-8")
        cfg = {
            "fish_audio": {"api_key": "", "voice_id": "config-default-voice", "model": "s2.1-pro-free"},
            "llm": {"endpoint": "https://ollama.com/v1", "model": "deepseek-v4-flash:0731", "api_key": ""},
        }
        monkeypatch.setenv("FISH_API_KEY", "env-fish")
        resolved = resolve_config(cfg, env_file=str(env_file))
        assert resolved["fish_audio"]["api_key"] == "env-fish"        # env > .env
        assert resolved["fish_audio"]["voice_id"] == "dotenv-voice"   # .env > config 默認
        assert resolved["fish_audio"]["model"] == "s2.1-pro-free"     # config 默認保留
        assert resolved["llm"]["model"] == "deepseek-v4-flash:0731"   # config 默認保留