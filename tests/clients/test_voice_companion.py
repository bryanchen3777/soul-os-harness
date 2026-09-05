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

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.akane_live import VoiceCompanionApp
from clients.voice_companion.akane_voice_brain import (
    AKANE_LAYER3_PERSONA,
    AKANE_VOICE_INVARIANTS,
    ClauseSplitter,
    AkaneVoiceBrain,
    contains_markdown_chars,
)
from clients.voice_companion.asr_refiner import AsrRefiner, refine_speech_text
from clients.voice_companion.fish_tts_streamer import FishTTSStreamer
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
    def test_payload_contains_text_reference_and_bearer(self):
        """Payload 正確包含 text、reference_id、format=mp3，Header 帶 Bearer Token"""
        session = FakeSession()
        streamer = FishTTSStreamer(api_key="fish-key-123", voice_id="akane-voice-9", session=session)
        audio = streamer.synthesize("你好，Bryan。")

        assert audio == b"\x00\x03MP3FAKE"
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["url"] == VOICE_ENDPOINT
        assert call["json"] == {"text": "你好，Bryan。", "reference_id": "akane-voice-9", "format": "mp3"}
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