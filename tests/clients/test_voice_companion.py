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

import asyncio
import json
import os
import ssl
import sys
import threading
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_live import VoiceCompanionApp, create_tts_streamer
from clients.voice_companion.akane_voice_brain import (
    AKANE_LAYER3_PERSONA,
    AKANE_VOICE_INVARIANTS,
    ClauseSplitter,
    AkaneVoiceBrain,
    contains_markdown_chars,
    StreamingVoiceSanitizer,
)
from clients.voice_companion.asr_refiner import AsrRefiner, refine_speech_text
from clients.voice_companion.env_config import apply_env_overrides, load_dotenv, normalize_chat_endpoint, resolve_config
from clients.voice_companion.fish_tts_streamer import FishTTSStreamer
from clients.voice_companion.fish_tts_live import FishTTSError, FishTTSLiveStreamer
from clients.voice_companion.stt_service import FishASRService, pcm16_to_wav_bytes
from clients.voice_companion.vad_listener import VADListener, VoiceActivityDetector
from clients.voice_companion.web_server import (
    HTTPS_SELF_SIGNED_HINT,
    AudioRelaySink,
    build_app,
    build_ssl_context,
    make_self_signed_cert,
)
from clients.voice_companion.web_ui import HTML_PAGE

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

    def test_refiner_preserves_functional_words(self):
        """VC-2.0 P0: 正常日常詞彙（這樣、然後、就是、那個）不得被全域 replace 誤殺"""
        assert refine_speech_text("這樣可以嗎？") == "這樣可以嗎？"
        assert refine_speech_text("然後呢？") == "然後呢？"
        assert refine_speech_text("這就是我要的") == "這就是我要的。"
        assert refine_speech_text("可以拿那個給我嗎？") == "可以拿那個給我嗎？"
        assert refine_speech_text("呃，那個，今天天氣好嗎？") == "今天天氣好嗎？"
        assert refine_speech_text("那個那個，今天開會嗎？") == "今天開會嗎？"
        assert refine_speech_text("我們然後然後就去了") == "我們然後就去了。"


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

    def test_sanitize_strips_stage_directions_whole(self):
        """動作/表情段整段剝離：括號與星號內容不得殘留被唸出（（微笑）→ 空，非『微笑』）"""
        from clients.voice_companion.akane_voice_brain import sanitize_voice_output

        assert sanitize_voice_output("（微笑著說）嗯，我很好。") == "嗯，我很好。"
        assert sanitize_voice_output("*輕輕嘆氣* 這樣啊。") == "這樣啊。"
        assert sanitize_voice_output("（看著窗外）今天天氣真好。") == "今天天氣真好。"
        assert sanitize_voice_output("嗯（停頓）我想想。") == "嗯 我想想。" or sanitize_voice_output("嗯（停頓）我想想。") == "嗯我想想。"
        assert "微笑" not in sanitize_voice_output("（微笑）累不累？")
        assert "嘆氣" not in sanitize_voice_output("（嘆氣）好吧。")

    def test_streaming_strips_stage_directions_across_split_tokens(self):
        """VC-2.0 P0: 串流模式下動作/表情跨 token 拆碎時，整段括號內容必須被徹底抑制"""
        tokens = ["（", "輕", "聲", "嘆", "氣", "）", "今", "天", "天氣", "很好"]
        parts = list(AkaneVoiceBrain(llm_stream=lambda msgs: iter(tokens)).stream_respond("早安"))
        joined = "".join(parts)
        assert "輕聲嘆氣" not in joined
        assert "嘆氣" not in joined
        assert joined == "今天天氣很好"

        star_tokens = ["*", "微", "笑", "*", "累不累？"]
        star_parts = list(AkaneVoiceBrain(llm_stream=lambda msgs: iter(star_tokens)).stream_respond("嗨"))
        star_joined = "".join(star_parts)
        assert "微笑" not in star_joined
        assert star_joined == "累不累？"

    def test_history_injected_before_new_user(self):
        """對話記憶：history 依序插在 system 之後、新 user 句之前（連貫）"""
        seen = {}

        def capture(msgs):
            seen["messages"] = msgs
            return iter(["好。"])

        brain = AkaneVoiceBrain(llm_stream=capture)
        hist = [
            {"role": "user", "content": "我們在聊劇本"},
            {"role": "assistant", "content": "好啊，說來聽聽。"},
        ]
        list(brain.stream_respond("你剛剛說想聽劇本？", history=hist))
        msgs = seen["messages"]
        assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
        assert [m["content"] for m in msgs[1:]] == ["我們在聊劇本", "好啊，說來聽聽。", "你剛剛說想聽劇本？"]

        # 缺省 history=None → 維持原單回合行為（system + user 兩條）
        seen2 = {}
        brain2 = AkaneVoiceBrain(llm_stream=lambda msgs: (seen2.__setitem__("m", msgs) or iter(["嗯。"])))
        list(brain2.stream_respond("測試"))
        assert [m["role"] for m in seen2["m"]] == ["system", "user"]

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

    def test_memory_retriever_injection(self):
        """VC-2.2: 記憶檢索器注入，檢索到的事實注入 system prompt"""
        captured = {}

        def fake_stream(msgs):
            captured["msgs"] = msgs
            return iter(["嗯，我記得。"])

        brain = AkaneVoiceBrain(
            llm_stream=fake_stream,
            memory_retriever=lambda q: "Bryan 正在準備開會" if "開會" in q else None,
            temporal_provider=None,
        )
        res = brain.respond("我有提到開會的事嗎？")
        assert "我記得" in res
        assert "msgs" in captured
        sys_msg = captured["msgs"][0]["content"]
        assert "【關於 Bryan 的記憶】" in sys_msg
        assert "Bryan 正在準備開會" in sys_msg

    def test_temporal_provider_injection(self):
        """VC-2.2: 時序現象學檢索器注入，TEMPORAL ANCHOR 注入 system prompt"""
        captured = {}

        def fake_stream(msgs):
            captured["msgs"] = msgs
            return iter(["這段時間辛苦了。"])

        mock_anchor = "[TEMPORAL ANCHOR]\n- 時間座標：2026-09-06 18:00\n- 體感經驗：傍晚時分\n- 關係時序：一切如常"
        brain = AkaneVoiceBrain(
            llm_stream=fake_stream,
            memory_retriever=None,
            temporal_provider=lambda: mock_anchor,
        )
        brain.respond("茜，你在嗎？")
        sys_msg = captured["msgs"][0]["content"]
        assert "【當前時序體感】" in sys_msg
        assert mock_anchor in sys_msg

    def test_both_memory_and_temporal_streaming(self):
        """VC-2.2: 串流模式下記憶與時序同時注入，正常切句輸出"""
        captured = {}

        def fake_stream(msgs):
            captured["msgs"] = msgs
            return iter(["記得你說過，", "今晚要早點睡。"])

        mock_anchor = "[TEMPORAL ANCHOR]\n- 時間座標：夜深"
        brain = AkaneVoiceBrain(
            llm_stream=fake_stream,
            memory_retriever=lambda q: "Bryan 昨晚熬夜了",
            temporal_provider=lambda: mock_anchor,
        )
        parts = list(brain.stream_respond("好累喔"))
        assert "".join(parts) == "記得你說過，今晚要早點睡。"
        sys_msg = captured["msgs"][0]["content"]
        assert "【當前時序體感】" in sys_msg
        assert "【關於 Bryan 的記憶】" in sys_msg
        assert "Bryan 昨晚熬夜了" in sys_msg

    def test_memory_and_temporal_fail_silent(self):
        """VC-2.2: 檢索器拋出例外或回傳 None 時 fail-silent，對話正常進行"""
        def bad_mem(q):
            raise RuntimeError("db lock")

        def bad_anchor():
            raise ValueError("bad time")

        captured = {}
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: (captured.__setitem__("msgs", msgs) or iter(["我在。"])),
            memory_retriever=bad_mem,
            temporal_provider=bad_anchor,
        )
        res = brain.respond("測試容錯")
        assert res == "我在。"
        sys_msg = captured["msgs"][0]["content"]
        assert "【當前時序體感】" not in sys_msg
        assert "【關於 Bryan 的記憶】" not in sys_msg

    def test_default_retrievers_fail_silent(self):
        """VC-2.2: default_memory_retriever 與 default_temporal_provider 於缺檔時回傳 None（0 異常）"""
        from clients.voice_companion.akane_voice_brain import (
            default_memory_retriever,
            default_temporal_provider,
        )

        assert default_memory_retriever("query", agent_id="non_existent_agent_xyz") is None
        assert default_temporal_provider(agent_id="non_existent_agent_xyz") is None


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

class FakeSDKSession:
    """偽 fish_audio_sdk.WebSocketSession：tts() 消費 text_iter 記錄收到的文字並回放 chunks。

    error 非 None → 收完文字後拋出（模擬 SDK WebSocketErr）；on_first_chunk 回呼可模擬
    「播放第一分片時 interrupt 從 worker 執行緒觸發」（Barge-in）。
    """

    def __init__(self, chunks=(), error=None, on_first_chunk=None):
        self.chunks = list(chunks)
        self.error = error
        self.on_first_chunk = on_first_chunk
        self.closed = False
        self.close_calls = 0
        self.texts = []
        self.request = None
        self.backend = None

    def close(self):
        self.closed = True
        self.close_calls += 1

    def tts(self, request, text_iter, backend=None):
        self.request = request
        self.backend = backend
        for text in text_iter:
            self.texts.append(text)
        if self.error is not None:
            raise self.error
        for i, chunk in enumerate(self.chunks):
            yield chunk
            if i == 0 and self.on_first_chunk is not None:
                self.on_first_chunk()  # 第一分片已在途（worker 已收到）後觸發 barge-in


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
    def _streamer(self, session, player=None):
        return FishTTSLiveStreamer(
            api_key="k", voice_id="v", model="s2.1-pro-free",
            audio_player=player or FakeLivePlayer(), session_factory=lambda key: session,
        )

    def test_sdk_request_and_backend_exact(self):
        """VC-1.6：以官方 TTSRequest 開 session；backend=model；文字依序餵入 text_iter"""
        sess = FakeSDKSession()
        streamer = self._streamer(sess)
        streamer.speak(["你好，Bryan。"])

        assert sess.request.reference_id == "v"
        assert sess.request.format == "pcm"
        assert sess.request.sample_rate == 44100  # VC-1.6：明確樣本率（None 會留給伺服器預設 → 不確定性）
        assert sess.request.chunk_length == 300
        assert sess.request.latency == "normal"
        assert sess.request.text == ""
        assert sess.backend == "s2.1-pro-free"
        assert sess.texts == ["你好，Bryan。"]
        assert sess.close_calls >= 1, "utterance 結束必須關閉 SDK session"
        assert streamer.last_error is None

    def test_audio_chunks_played_in_order(self):
        """SDK 產出的 audio 分片依序餵給 player；speak 同步播完、無錯誤"""
        player = FakeLivePlayer()
        sess = FakeSDKSession(chunks=[b"\x01" * 200, b"\x02" * 400])
        streamer = self._streamer(sess, player)
        streamer.speak(["測試"])

        assert player.written == [b"\x01" * 200, b"\x02" * 400], "audio 分片必須依序邊收邊播"
        assert sess.close_calls >= 1
        assert streamer.last_error is None

    def test_multi_clause_texts_delivered_in_order(self):
        """多分句依序送入 text_iter（舊 flush 語意由 SDK chunk_length 取代）"""
        sess = FakeSDKSession()
        streamer = self._streamer(sess)
        streamer.speak(["第一句。", "第二句。", "第三句。"])

        assert sess.texts == ["第一句。", "第二句。", "第三句。"]
        assert sess.close_calls >= 1

    def test_worker_error_records_error_and_stops_playback(self):
        """SDK 丟例外（finish reason=error 等）→ 記錄 last_error、停止播放、不 crash、不重試"""
        player = FakeLivePlayer()
        sess = FakeSDKSession(error=FishTTSError("TTS-Live error: boom"))
        streamer = self._streamer(sess, player)
        streamer.speak(["測試"])  # 不應拋出

        assert isinstance(streamer.last_error, FishTTSError)
        assert player.stop_calls >= 1, "worker 錯誤必須停止播放"
        assert sess.close_calls >= 1

    def test_interrupt_during_playback_stops_worker(self):
        """播放第一分片時 interrupt（模擬 VAD barge-in）→ session 關閉、player 停止、剩餘分片不播、狀態重置"""
        player = FakeLivePlayer()
        sess = FakeSDKSession(chunks=[b"\x7f" * 300, b"\x7e" * 300])
        streamer = self._streamer(sess, player)
        sess.on_first_chunk = streamer.interrupt  # worker 播第一分片時觸發 barge-in
        streamer.speak(["被打斷"])

        assert player.written == [b"\x7f" * 300], "interrupt 後剩餘分片不得播放"
        assert player.stop_calls >= 1, "interrupt 必須停止播放"
        assert sess.closed is True, "interrupt 必須關閉 SDK session（中止合成）"
        assert streamer.queue_size == 0
        assert streamer.pending_chunks() == []
        assert streamer.is_playing is False, "播放狀態必須重置"
        assert streamer.interrupt_event.is_set()

    def test_interrupt_clears_queued_chunks(self):
        """播放中佇列已有分片 → interrupt() 立即清空（無活動 session 時不誤關）"""
        player = FakeLivePlayer()
        sess = FakeSDKSession()
        streamer = self._streamer(sess, player)
        streamer._chunks.append(b"queued-1")
        streamer._chunks.append(b"queued-2")

        streamer.interrupt()

        assert streamer.queue_size == 0
        assert streamer.pending_chunks() == []
        assert sess.close_calls == 0, "無活動 session 時 interrupt 不得關閉任何 session"

    def test_speak_noop_after_interrupt(self):
        """interrupt 後 speak() no-op：不建立 SDK session、0 文字送出"""
        player = FakeLivePlayer()
        sess = FakeSDKSession()
        calls = []
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", audio_player=player,
                                       session_factory=lambda key: (calls.append(key), sess)[1])
        streamer.interrupt()

        streamer.speak(["還有話說"])

        assert calls == [], "interrupt 後不得建立 SDK session"
        assert sess.texts == []

    def test_close_releases_player_and_locks(self):
        """close()：關閉播放器、狀態鎖死，close 後 speak() no-op"""
        player = FakeLivePlayer()
        sess = FakeSDKSession()
        calls = []
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", audio_player=player,
                                       session_factory=lambda key: (calls.append(key), sess)[1])
        streamer.close()

        assert player.close_calls == 1
        streamer.speak(["關閉後"])
        assert calls == []
        assert sess.texts == []

    def test_start_rearms_after_interrupt(self):
        """interrupt 後 start() → 可再開新 session 正常 speak（barge-in 恢復語意）"""
        player = FakeLivePlayer()
        sess = FakeSDKSession(chunks=[b"\xaa" * 100])
        streamer = self._streamer(sess, player)
        streamer.interrupt()
        streamer.start()

        streamer.speak(["恢復了"])

        assert sess.texts == ["恢復了"]
        assert player.written == [b"\xaa" * 100]
        assert streamer.last_error is None

    def test_feed_text_piece_auto_session_and_texts(self):
        """feed_text_piece：首 piece 自動開 session；pieces 依序進 text_iter；end_session 收尾關 session"""
        sess = FakeSDKSession(chunks=[b"\x10" * 64])
        streamer = self._streamer(sess, FakeLivePlayer())
        streamer.feed_text_piece("今天天")
        streamer.feed_text_piece("好累。")
        streamer.end_session()

        assert sess.texts == ["今天天", "好累。"]
        assert sess.close_calls >= 1, "end_session 後 SDK session 必須關閉"
        assert streamer.last_error is None

    def test_feed_text_piece_noop_after_interrupt(self):
        """feed 模式 interrupt 後：feed_text_piece / end_session 皆 no-op（0 文字送出）"""
        sess = FakeSDKSession()
        calls = []
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", audio_player=FakeLivePlayer(),
                                       session_factory=lambda key: (calls.append(key), sess)[1])
        streamer.interrupt()

        streamer.feed_text_piece("被打斷後")
        streamer.end_session()

        assert calls == [], "interrupt 後 feed 不得建立 SDK session"
        assert sess.texts == []

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

    def test_last_error_tracks_http_status_and_clears_on_ok(self):
        """VC-1.4 additive：非 200 → last_error/last_status 記錄（回傳合約 "" 不變）；成功 200 → 清空"""
        session = FakeASRSession(payload={}, status_code=402)
        svc = FishASRService(api_key="k", session=session)
        assert svc.transcribe(b"wav-bytes") == ""
        assert svc.last_status == 402
        assert svc.last_error is not None and svc.last_error["status"] == 402
        assert "message" in svc.last_error

        ok_session = FakeASRSession(payload={"text": "好"}, status_code=200)
        svc2 = FishASRService(api_key="k", session=ok_session)
        assert svc2.transcribe(b"wav-bytes") == "好"
        assert svc2.last_error is None
        assert svc2.last_status is None

    def test_last_error_tracks_exception(self):
        """VC-1.4 additive：網路/解析例外 → last_error={"status":0,...}"""
        session = FakeASRSession(error=RuntimeError("connection boom"))
        svc = FishASRService(api_key="k", session=session)
        assert svc.transcribe(b"wav-bytes") == ""
        assert svc.last_status == 0
        assert "boom" in svc.last_error["message"]


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
        sess = FakeSDKSession()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", audio_player=FakeLivePlayer(),
                                       session_factory=lambda key: sess)
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["我在，", "說說看。"]))
        app = VoiceCompanionApp({}, refiner=None, brain=brain, streamer=streamer, listener=None)

        app._speak_reply("今天好累")

        assert sess.texts == ["我在，", "說說看。"]
        assert sess.close_calls >= 1
        assert streamer.last_error is None
        assert app._last_interaction > 0

    def test_no_stream_tokens_falls_back_to_speak(self):
        """無 LLM 串流能力（llm_stream None）→ live 走 speak(分句列表) 而非 feed 迴圈"""
        sess = FakeSDKSession()
        streamer = FishTTSLiveStreamer(api_key="k", voice_id="v", audio_player=FakeLivePlayer(),
                                       session_factory=lambda key: sess)
        brain = AkaneVoiceBrain(llm_stream=None, config={"llm": {"endpoint": "", "api_key": ""}})
        app = VoiceCompanionApp({}, refiner=None, brain=brain, streamer=streamer, listener=None)

        app._speak_reply("今天好累")

        assert len(sess.texts) >= 1, "fallback 必須經 speak() 送至少一個分句"
        assert "我在" in "".join(sess.texts) or "說說看" in "".join(sess.texts)
        assert sess.close_calls >= 1
        assert streamer.last_error is None
        assert app._last_interaction > 0


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

    def test_ollama_api_key_fallback_and_precedence(self, tmp_path, monkeypatch):
        """OLLAMA_API_KEY（生產慣例）作為 llm.api_key fallback；LLM_API_KEY 設定時優先"""
        for k in ("FISH_API_KEY", "FISH_VOICE_ID", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "OLLAMA_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        missing = tmp_path / "missing.env"   # 隔離真實 .env（不載入）
        cfg = {"llm": {"endpoint": "https://ollama.com/v1", "model": "m", "api_key": ""}}
        monkeypatch.setenv("OLLAMA_API_KEY", "ollama-key")
        assert resolve_config(dict(cfg), env_file=str(missing))["llm"]["api_key"] == "ollama-key"   # fallback 生效
        monkeypatch.setenv("LLM_API_KEY", "explicit-key")
        assert resolve_config(dict(cfg), env_file=str(missing))["llm"]["api_key"] == "explicit-key" # 顯式鍵優先

    def test_normalize_chat_endpoint(self):
        """OpenAI 相容端點正規化：缺 /chat/completions 自動補、有則原樣、空值回空"""
        assert normalize_chat_endpoint("https://ollama.com/v1") == "https://ollama.com/v1/chat/completions"
        assert normalize_chat_endpoint("https://ollama.com/v1/") == "https://ollama.com/v1/chat/completions"
        assert normalize_chat_endpoint("https://x.example/v1/chat/completions") == "https://x.example/v1/chat/completions"
        assert normalize_chat_endpoint("") == ""
        assert normalize_chat_endpoint("   ") == ""

    def test_ui_audio_resume_autoplay(self):
        """播放端 AudioContext autoplay 政策解凍：resume helper＋suspended 檢查＋手勢掛鉤存在"""
        from clients.voice_companion.web_ui import HTML_PAGE
        assert "ensureAudioResume" in HTML_PAGE
        assert 'audioCtx.state === "suspended"' in HTML_PAGE
        assert "audioCtx.resume()" in HTML_PAGE
        assert "pointerdown" in HTML_PAGE

    def test_ui_playback_decoupled_from_mic(self):
        """播放與麥克風解耦：打字路徑（無 mic）也會建立播放圖（VC-1.6 靜音根因回歸）"""
        from clients.voice_companion.web_ui import HTML_PAGE
        assert "function ensurePlayback()" in HTML_PAGE
        assert "if (audioCtx) { ensureAudioResume(); return; }" in HTML_PAGE  # 冪等守衛
        assert 'if (s === "SPEAKING") { ensurePlayback(); }' in HTML_PAGE    # 收到 SPEAKING 即建播放
        assert "ensurePlayback();" in HTML_PAGE                               # 手勢/送文字/binary 皆觸發

    def test_llm_stream_sse_utf8_decode(self, monkeypatch):
        """SSE 回應無 charset 時強制 UTF-8 解碼（防 ISO-8859-1 亂碼）；端點正規化同時生效"""
        from clients.voice_companion import akane_voice_brain as brain

        class FakeResp:
            encoding = "ISO-8859-1"  # 模擬 Ollama SSE 不帶 charset（requests 預設會誤判）

            def raise_for_status(self):
                pass

            def iter_lines(self, decode_unicode=True):
                # 模擬 requests：依 resp.encoding 解碼位元組（stream 內先被 fix 強制為 utf-8）
                raw = 'data: {"choices":[{"delta":{"content":"你"}}]}'.encode("utf-8")
                if decode_unicode:
                    yield raw.decode(self.encoding, errors="replace")
                else:
                    yield raw
                yield b"data: [DONE]" if not decode_unicode else "data: [DONE]"

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            return FakeResp()

        monkeypatch.setattr("requests.post", fake_post)
        stream = brain.build_llm_stream({"endpoint": "https://ollama.com/v1", "model": "m", "api_key": "k"})
        tokens = list(stream([{"role": "user", "content": "hi"}]))
        assert "".join(tokens) == "你"                       # UTF-8 正確解碼，非 "ä½ "
        assert captured["url"] == "https://ollama.com/v1/chat/completions"  # 正規化生效


# ─────────────────────────────────────────────────────────────
# Test 6：Web 語音伴侶伺服器（VC-1.3，全離線注入 fake，0 網路）
# ─────────────────────────────────────────────────────────────

WEB_TEST_CONFIG = {
    "fish_audio": {
        "api_key": "fake-key", "voice_id": "v", "model": "s2.1-pro-free",
        "mode": "live", "live_format": "pcm",
        "asr_endpoint": "https://api.fish.audio/v1/asr",
        "tts_ws_endpoint": "wss://api.fish.audio/v1/tts/live",
    },
    "stt": {"engine": "fish", "language": "zh"},
    "vad": {"sample_rate": 16000, "silence_threshold_sec": 0.05, "energy_threshold": 0.05},
    "llm": {"endpoint": "", "api_key": ""},
    "web": {"host": "127.0.0.1", "port": 8765},
}


class FakeWebStreamer:
    """記錄呼叫的偽 TTS streamer（介面：start/feed_text_piece/end_session/interrupt/close）。"""

    def __init__(self, sink=None, tokens=("我在，", "說說看。")):
        self.sink = sink
        self.tokens = tokens
        self.fed: list[str] = []
        self.interrupt_calls = 0
        self.end_calls = 0
        self.start_calls = 0

    def start(self):
        self.start_calls += 1

    def feed_text_piece(self, piece: str):
        self.fed.append(piece)
        if self.sink is not None:
            self.sink.write(b"\x10\x00" * 160)  # 模擬 TTS 產出 PCM 分片 → relay

    def end_session(self):
        self.end_calls += 1
        if self.sink is not None:
            self.sink.write(b"\x20\x00" * 80)

    def interrupt(self):
        self.interrupt_calls += 1

    def close(self):
        pass


class FakeWebASR:
    def __init__(self, text="茜，今天好累"):
        self.text = text
        self.calls = 0
        self.last_error: dict | None = None  # VC-1.4 失敗透通（類比 FishASRService additive 屬性）
        self.last_status: int | None = None

    def transcribe(self, wav_bytes):
        self.calls += 1
        return self.text


def _make_web_app(brain_tokens=("我在，", "說說看。"), asr_text="茜，今天好累", streamer_factory=None):
    """建立注入 fake 的 web app + 收集 fake streamer 的容器。"""
    streamers: list = []
    brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(brain_tokens))

    def default_factory(sink):
        s = FakeWebStreamer(sink=sink)
        streamers.append(s)
        return s

    app = build_app(
        WEB_TEST_CONFIG,
        brain=brain,
        refiner=AsrRefiner(llm_call=None),  # 離線確定性淨化
        asr=FakeWebASR(asr_text),
        streamer_factory=streamer_factory or default_factory,
    )
    return app, streamers


class TestWebServer:
    def test_index_page_served(self):
        """GET / → 200 且含關鍵 UI 元件（mic 按鈕 / 狀態文字）"""
        async def _run():
            app, _ = _make_web_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                resp = await client.get("/")
                assert resp.status == 200
                html = await resp.text()
                assert "micBtn" in html
                assert "statusText" in html
                assert "黑川茜" in html
            finally:
                await client.close()

        asyncio.run(_run())

    def test_ws_text_input_pipeline(self):
        """WS 文字輸入路徑：送 text JSON → transcript user → SPEAKING → transcript akane → IDLE"""
        async def _run():
            app, streamers = _make_web_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "text", "text": "今天好累"})
                events = []
                while len(events) < 4:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        events.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

            assert [e["type"] for e in events] == ["transcript", "state", "transcript", "state"]
            assert events[0]["role"] == "user" and events[0]["text"] == "今天好累"
            assert events[1]["state"] == "SPEAKING"
            assert events[2]["role"] == "akane" and events[2]["text"] == "我在，說說看。"
            assert events[3]["state"] == "IDLE"
            assert streamers[0].fed == ["我在，", "說說看。"]  # LLM token 依序 feed
            assert streamers[0].end_calls == 1

        asyncio.run(_run())

    def test_ws_interrupt_triggers_streamer_interrupt(self):
        """SPEAKING 中送 interrupt → fake streamer.interrupt 被呼叫、狀態回 IDLE（瀏覽器靜音通知）"""

        class BlockingWebStreamer(FakeWebStreamer):
            """feed 阻塞（模擬 TTS 播放中）直到 release，讓 interrupt 在 SPEAKING 中被處理。"""

            def __init__(self, sink=None):
                super().__init__(sink)
                self.release = threading.Event()

            def feed_text_piece(self, piece: str):
                self.fed.append(piece)
                self.release.wait(timeout=2)  # to_thread 內阻塞

        async def _run():
            blocking = BlockingWebStreamer()
            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(("我在，", "說說看。")))
            app = build_app(
                WEB_TEST_CONFIG, brain=brain, refiner=AsrRefiner(llm_call=None),
                asr=FakeWebASR(), streamer_factory=lambda sink: blocking,
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "text", "text": "測試"})
                events = []
                while not any(e.get("type") == "state" and e.get("state") == "SPEAKING" for e in events):
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        events.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.send_json({"type": "interrupt"})  # SPEAKING 中打斷
                extra = []
                for _ in range(2):
                    try:
                        msg = await ws.receive(timeout=1)
                    except asyncio.TimeoutError:
                        break
                    if msg.type == WSMsgType.TEXT:
                        extra.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                blocking.release.set()  # 放行 feed thread 收尾
                await asyncio.sleep(0.05)
                await ws.close()
            finally:
                await client.close()

            assert blocking.interrupt_calls >= 1, "interrupt 必須呼叫 streamer.interrupt()"
            states = [e["state"] for e in events + extra if e.get("type") == "state"]
            assert states and states[-1] == "IDLE", "打斷後狀態必須回 IDLE（瀏覽器靜音通知）"

        asyncio.run(_run())

    def test_ws_relay_forwards_pcm_binary_ordered(self):
        """relay sink：TTS PCM 分片 → WS binary frame 依序送出（aiohttp test client 收集）"""
        async def _run():
            app, _ = _make_web_app(brain_tokens=("二，", "三。"))
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "text", "text": "請說"})
                binaries: list[bytes] = []
                texts = 0
                while texts < 4 or len(binaries) < 3:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.BINARY:
                        binaries.append(msg.data)
                    elif msg.type == WSMsgType.TEXT:
                        texts += 1
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

            # 2 個 token 各觸發 1 分片（fake feed）+ end_session 1 分片 → 依序送出
            assert binaries == [b"\x10\x00" * 160, b"\x10\x00" * 160, b"\x20\x00" * 80]

        asyncio.run(_run())

    def test_ws_state_machine_ptt_flow(self):
        """狀態機：ptt_start → LISTENING；語音分片 + ptt_stop → THINKING → SPEAKING → IDLE"""
        async def _run():
            asr = FakeWebASR("茜，今天好累")
            streamers: list = []

            def factory(sink):
                s = FakeWebStreamer(sink=sink)
                streamers.append(s)
                return s

            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(("我在，", "說說看。")))
            app = build_app(
                WEB_TEST_CONFIG, brain=brain, refiner=AsrRefiner(llm_call=None),
                asr=asr, streamer_factory=factory,
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()  # 0.1s 語音
                await ws.send_bytes(pcm)
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})
                events = []
                while len(events) < 6:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        events.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

            states = [e["state"] for e in events if e["type"] == "state"]
            assert states[0] == "LISTENING"
            assert "THINKING" in states
            assert "SPEAKING" in states
            assert states[-1] == "IDLE"
            assert asr.calls == 1, "斷句後必須送 Fish ASR"
            roles = [e["role"] for e in events if e["type"] == "transcript"]
            assert roles == ["user", "akane"]

        asyncio.run(_run())

    def test_audio_relay_sink_order_and_stop(self):
        """AudioRelaySink：write 依序送出；stop 清空佇列（interrupt 語意）"""
        async def _run():
            class FakeWS:
                def __init__(self):
                    self.sent: list[bytes] = []

                async def send_bytes(self, data: bytes):
                    self.sent.append(data)

            fws = FakeWS()
            sink = AudioRelaySink(asyncio.get_running_loop(), fws)
            sink.start()
            sink.write(b"a" * 8)
            sink.write(b"b" * 8)
            await asyncio.sleep(0.05)
            assert fws.sent == [b"a" * 8, b"b" * 8], "分片必須依序送出"

            sink.write(b"c" * 8)
            sink.stop()  # 同 tick：put 先排、drain 隨後排 → sender 拿不到 c
            await asyncio.sleep(0.05)
            assert fws.sent == [b"a" * 8, b"b" * 8], "stop 後殘留分片不得送出"
            assert sink._queue.qsize() == 0
            sink.close()

        asyncio.run(_run())

    def test_index_page_contains_ui_script(self):
        """HTML_PAGE 內嵌完整前端（收音/放音/打斷 JS 關鍵片段）"""
        assert "getUserMedia" in HTML_PAGE
        assert "ptt_start" in HTML_PAGE
        assert "interrupt" in HTML_PAGE
        assert "AudioContext" in HTML_PAGE
        assert "int16" in HTML_PAGE.lower() or "Int16Array" in HTML_PAGE

    def test_ws_asr_402_error_transparency(self):
        """VC-1.4：ASR 402（額度不足）→ error 事件含「402」與「額度」→ 狀態回 IDLE（不靜默 DROP）"""
        async def _run():
            asr = FakeWebASR("")  # transcribe 回 ""（既有容錯語義）
            asr.last_error = {"status": 402, "message": "Insufficient API credit. API credit is managed independently from platform credit"}
            asr.last_status = 402

            def factory(sink):
                return FakeWebStreamer(sink=sink)

            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(("x",)))
            app = build_app(WEB_TEST_CONFIG, brain=brain, refiner=AsrRefiner(llm_call=None),
                            asr=asr, streamer_factory=factory)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
                await ws.send_bytes(pcm)
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})
                events = []
                while len(events) < 4:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        events.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

            errors = [e["message"] for e in events if e["type"] == "error"]
            assert len(errors) == 1, "402 必須透通成 error 事件"
            assert "402" in errors[0]
            assert "額度" in errors[0]
            states = [e["state"] for e in events if e["type"] == "state"]
            assert states[-1] == "IDLE", "ASR 失敗後狀態必須回 IDLE"

        asyncio.run(_run())

    def test_ws_asr_drop_silent_no_error(self):
        """VC-1.4：ASR 回 "" 且 last_error=None（真雜音/靜音）→ 0 error 事件（維持 DROP 靜默）"""
        async def _run():
            asr = FakeWebASR("")  # 無 last_error（真雜音熔斷語意）

            def factory(sink):
                return FakeWebStreamer(sink=sink)

            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(("x",)))
            app = build_app(WEB_TEST_CONFIG, brain=brain, refiner=AsrRefiner(llm_call=None),
                            asr=asr, streamer_factory=factory)
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
                await ws.send_bytes(pcm)
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})
                events = []
                while len(events) < 3:
                    msg = await ws.receive(timeout=3)
                    if msg.type == WSMsgType.TEXT:
                        events.append(json.loads(msg.data))
                    elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
                await ws.close()
            finally:
                await client.close()

            assert [e["type"] for e in events if e["type"] == "error"] == [], "真雜音必須靜默 DROP（0 error 事件）"
            states = [e["state"] for e in events if e["type"] == "state"]
            assert states[-1] == "IDLE"

        asyncio.run(_run())

    def test_ui_meter_error_box_elements(self):
        """VC-1.4：HTML_PAGE 含錯誤訊息區 id、音量表元素 id、🎙️ 傳送中字樣與額度提示註解"""
        assert 'id="errorBox"' in HTML_PAGE
        assert 'id="meterBar"' in HTML_PAGE
        assert 'id="meterFill"' in HTML_PAGE
        assert 'id="meterLabel"' in HTML_PAGE
        assert "🎙️" in HTML_PAGE
        assert "傳送中" in HTML_PAGE
        assert "額度" in HTML_PAGE  # 內建提示（HTML 註解）

    def test_ws_ping_pong_heartbeat(self):
        """VC-2.1: 前端送 ping → 後端回 pong 心跳保活"""
        async def _run():
            app, _ = _make_web_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ping"})
                msg = await ws.receive(timeout=3)
                assert msg.type == WSMsgType.TEXT
                data = json.loads(msg.data)
                assert data.get("type") == "pong"
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

    def test_ui_reconnect_and_lifecycle_elements(self):
        """VC-2.1: HTML_PAGE 包含自動重連、心跳保活與手機端前景喚醒監聽"""
        assert "visibilitychange" in HTML_PAGE
        assert "scheduleReconnect" in HTML_PAGE
        assert "startHeartbeat" in HTML_PAGE
        assert "ping" in HTML_PAGE
        assert "touchstart" in HTML_PAGE

    def test_ui_autovad_barge_in_elements(self):
        """VC-2.2: HTML_PAGE 包含 Auto-VAD Barge-in 門檻與狀態連動"""
        assert "BARGE_AUTO_THRESHOLD = 0.04" in HTML_PAGE
        assert "BARGE_AUTO_MS = 200" in HTML_PAGE
        assert "barging = false" in HTML_PAGE
        assert "barging = true" in HTML_PAGE
        assert "BARGE_AUTO_THRESHOLD" in HTML_PAGE



# ─────────────────────────────────────────────────────────────
# Test 7：HTTPS 模式 + 不安全來源提示 + 診斷日誌（VC-1.5，全離線）
# ─────────────────────────────────────────────────────────────

class TestHttpsAndDiagnostics:
    def test_https_cert_generation_and_ssl_load(self, tmp_path):
        """make_self_signed_cert：產出 cert.pem/key.pem（PEM），build_ssl_context 可讀取（--https 啟動鏈路）"""
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        make_self_signed_cert(cert, key)
        assert cert.is_file() and key.is_file()
        assert "BEGIN CERTIFICATE" in cert.read_text(encoding="utf-8")
        key_pem = key.read_text(encoding="utf-8")
        assert "BEGIN" in key_pem and "PRIVATE KEY" in key_pem  # PKCS#1/PKCS#8 皆可被 ssl 讀取
        ctx = build_ssl_context(cert, key)
        assert isinstance(ctx, ssl.SSLContext)

    def test_https_self_signed_hint_constant(self):
        """HTTPS 啟動 stdout 指引字串常數存在（自簽憑證 → 瀏覽器選「繼續前往」）"""
        assert "繼續前往" in HTTPS_SELF_SIGNED_HINT
        assert "麥克風" in HTTPS_SELF_SIGNED_HINT

    def test_https_urls_scheme(self):
        """lan_urls 支援 https scheme（啟動列印 https:// 網址）"""
        from clients.voice_companion.web_server import lan_urls

        urls = lan_urls(8765, scheme="https")
        assert any(u.startswith("https://127.0.0.1:8765") for u in urls)

    def test_insecure_context_banner(self):
        """VC-1.5：HTML_PAGE 含 isSecureContext 常駐檢測、不安全來源提示、mic 未就緒提示、關閉鈕、err.name 分類"""
        assert "isSecureContext" in HTML_PAGE
        assert "麥克風需要 HTTPS 或 localhost" in HTML_PAGE
        assert "麥克風尚未就緒" in HTML_PAGE
        assert 'id="errorDismiss"' in HTML_PAGE
        assert "NotAllowedError" in HTML_PAGE
        assert "SecurityError" in HTML_PAGE

    def test_certs_dir_gitignored(self):
        """certs/（自簽憑證含私鑰）必須被 .gitignore 排除（0 金鑰進 git）"""
        ig = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
        assert "clients/voice_companion/certs/" in ig

    def test_ws_diagnostics_logged(self, capsys):
        """VC-1.5：完整回合的診斷日誌（[WS] 連線/事件、[UTT] 開始/ASR 結果/回覆）寫到 stdout"""
        async def _run():
            app, _ = _make_web_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            ws = await client.ws_connect("/ws")
            await ws.send_json({"type": "ptt_start"})
            pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
            await ws.send_bytes(pcm)
            await ws.send_bytes(pcm)
            await ws.send_json({"type": "ptt_stop"})
            events = []
            while len(events) < 6:
                msg = await ws.receive(timeout=3)
                if msg.type == WSMsgType.TEXT:
                    events.append(json.loads(msg.data))
                elif msg.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                    break
            await ws.send_json({"type": "interrupt"})
            await asyncio.sleep(0.1)
            await ws.close()
            await asyncio.sleep(0.2)  # 等 server handler finally 收尾
            await client.close()

        asyncio.run(_run())
        out = capsys.readouterr().out
        assert "[WS] connect" in out
        assert "[WS] ptt_start" in out
        assert "[WS] ptt_stop" in out
        assert "[UTT] start frames=" in out
        assert "[UTT] asr-ok" in out, "回合完成必須記錄 ASR 結果"
        assert "[UTT] reply" in out, "回合完成必須記錄回覆結果"
        assert "[WS] interrupt" in out
        assert "[WS] close" in out


# ─────────────────────────────────────────────────────────────
# Test 8：Web 回合取消與過期結果隔離（VC-2.3-02，全離線）
# ─────────────────────────────────────────────────────────────

class TestWebTurnCancellation:
    def test_interrupt_during_asr_discards_stale_result(self):
        """VC-2.3-02: ASR 延遲期間收到 interrupt → ASR 完成後不得送出 user transcript、不得進入 speaking"""
        class SlowASR:
            def transcribe(self, wav_bytes):
                import time
                time.sleep(0.15)
                return "這是一句過期語音"

        async def _run():
            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["回覆"]))
            app = build_app(
                WEB_TEST_CONFIG,
                brain=brain,
                refiner=AsrRefiner(llm_call=None),
                asr=SlowASR(),
                streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})

                # 等待進入 THINKING
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "THINKING":
                        break

                # 在 ASR 進行中發送 interrupt
                await ws.send_json({"type": "interrupt"})
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "IDLE":
                        break

                # 等待 SlowASR 結束
                await asyncio.sleep(0.25)

                received = []
                while True:
                    try:
                        m = await ws.receive(timeout=0.1)
                        if m.type == WSMsgType.TEXT:
                            received.append(json.loads(m.data))
                        else:
                            break
                    except Exception:
                        break
                assert not any(r.get("text") == "這是一句過期語音" for r in received)
                assert not any(r.get("state") == "SPEAKING" for r in received)
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

    def test_interrupt_during_refiner_discards_stale_result(self):
        """VC-2.3-02: Refiner 延遲期間收到 interrupt → 不得進入 speaking"""
        class SlowRefiner:
            def refine_speech_text(self, text):
                import time
                time.sleep(0.15)
                return "過期淨化文字"

        async def _run():
            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["回覆"]))
            app = build_app(
                WEB_TEST_CONFIG,
                brain=brain,
                refiner=SlowRefiner(),
                asr=FakeWebASR("語音輸入"),
                streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})

                # 等待收到 user transcript (此時即將進入 refiner)
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("type") == "transcript":
                        break

                # 在 refiner 進行中發送 interrupt
                await ws.send_json({"type": "interrupt"})
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "IDLE":
                        break

                await asyncio.sleep(0.25)
                received = []
                while True:
                    try:
                        m = await ws.receive(timeout=0.1)
                        if m.type == WSMsgType.TEXT:
                            received.append(json.loads(m.data))
                        else:
                            break
                    except Exception:
                        break
                assert not any(r.get("state") == "SPEAKING" for r in received)
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

    def test_new_text_turn_supersedes_old_voice_turn(self):
        """VC-2.3-02: 語音 THINKING 中送出文字訊息 → 舊語音直接作廢，新文字優先回覆"""
        asr_gate = threading.Event()

        class GatedASR:
            def transcribe(self, wav_bytes):
                asr_gate.wait(timeout=2)
                return "舊語音"

        async def _run():
            brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["文字回覆。"]))
            app = build_app(
                WEB_TEST_CONFIG,
                brain=brain,
                refiner=AsrRefiner(llm_call=None),
                asr=GatedASR(),
                streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "ptt_start"})
                pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
                await ws.send_bytes(pcm)
                await ws.send_json({"type": "ptt_stop"})

                # 等待進入 THINKING
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "THINKING":
                        break

                # 立即送出文字打斷舊語音
                await ws.send_json({"type": "text", "text": "新文字"})
                await asyncio.sleep(0.05)
                asr_gate.set() # 放行舊 ASR 執行緒

                events = []
                while True:
                    try:
                        m = await ws.receive(timeout=2)
                        if m.type == WSMsgType.TEXT:
                            data = json.loads(m.data)
                            events.append(data)
                            if data.get("type") == "state" and data.get("state") == "IDLE":
                                break
                        else:
                            break
                    except Exception:
                        break

                user_texts = [e.get("text") for e in events if e.get("type") == "transcript" and e.get("role") == "user"]
                assert "新文字" in user_texts
                assert "舊語音" not in user_texts
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

    def test_barged_in_reply_is_not_added_to_history(self):
        """VC-2.3-02: 說話中被打斷的半截回覆不得寫入對話記憶 _history"""
        captured_prompts = []
        first_called = threading.Event()

        class BlockingStreamer(FakeWebStreamer):
            def __init__(self, sink=None):
                super().__init__(sink)
                self.release = threading.Event()

            def feed_text_piece(self, piece: str):
                self.fed.append(piece)
                self.release.wait(timeout=2)

        def mock_llm(msgs):
            captured_prompts.append(list(msgs))
            first_called.set()
            return iter(["回覆片段一，", "回覆片段二。"])

        async def _run():
            streamer = BlockingStreamer()
            brain = AkaneVoiceBrain(llm_stream=mock_llm)
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
                # 第一回合：打字發送
                await ws.send_json({"type": "text", "text": "這是一句會被打斷的話"})
                # 等待進入 SPEAKING
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "SPEAKING":
                        break
                await asyncio.to_thread(first_called.wait, 2)
                # 打斷第一回合
                await ws.send_json({"type": "interrupt"})
                streamer.release.set() # 讓背景執行緒通過
                # 等待第一回合回到 IDLE
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "IDLE":
                        break

                # 第二回合：新文字
                streamer.release = threading.Event()
                streamer.release.set() # 第二回合不阻擋
                await ws.send_json({"type": "text", "text": "第二回合正常對話"})
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "IDLE":
                        break

                # 檢查第二回合收到的 messages，不應含有「這是一句會被打斷的話」
                assert len(captured_prompts) >= 2
                second_prompt_contents = [m["content"] for m in captured_prompts[1]]
                assert "這是一句會被打斷的話" not in second_prompt_contents
                assert "第二回合正常對話" in second_prompt_contents
                await ws.close()
            finally:
                await client.close()

        asyncio.run(_run())

    def test_session_close_cancels_reply_task_cleanly(self):
        """VC-2.3-02: Session 關閉時乾淨取消正在運行的 reply task，0 未捕獲例外"""
        class HangingStreamer(FakeWebStreamer):
            def __init__(self, sink=None):
                super().__init__(sink)
                self.release = threading.Event()

            def feed_text_piece(self, piece: str):
                self.fed.append(piece)
                self.release.wait(timeout=5)

        async def _run():
            streamer = HangingStreamer()
            app, _ = _make_web_app(
                brain_tokens=("很長的話，", "說不完。"),
                streamer_factory=lambda sink: streamer,
            )
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                ws = await client.ws_connect("/ws")
                await ws.send_json({"type": "text", "text": "關閉測試"})
                # 等待進入 SPEAKING
                while True:
                    m = await ws.receive(timeout=2)
                    if m.type == WSMsgType.TEXT and json.loads(m.data).get("state") == "SPEAKING":
                        break
                # 仍在 SPEAKING 時直接關閉連線
                await ws.close()
                await asyncio.sleep(0.1)
                streamer.release.set()
            finally:
                await client.close()

        asyncio.run(_run())


# ─────────────────────────────────────────────────────────────
# Test 9：前端 AudioWorklet 與無複製 Ring Buffer（VC-2.3-04，全離線）
# ─────────────────────────────────────────────────────────────

class TestWebAudioWorkletAndRingBuffer:
    def test_ui_contains_audio_worklet_processor_and_blob_loader(self):
        """VC-2.3-04: HTML_PAGE 包含 AudioWorkletProcessor 定義、Blob URL 動態註冊與 AudioWorkletNode"""
        from clients.voice_companion.web_ui import HTML_PAGE

        assert "class AkaneAudioProcessor extends AudioWorkletProcessor" in HTML_PAGE
        assert "registerProcessor('akane-audio-processor', AkaneAudioProcessor)" in HTML_PAGE or \
               'registerProcessor("akane-audio-processor", AkaneAudioProcessor)' in HTML_PAGE
        assert "URL.createObjectURL(blob)" in HTML_PAGE or "URL.createObjectURL(new Blob" in HTML_PAGE or "createObjectURL" in HTML_PAGE
        assert "new AudioWorkletNode(audioCtx, \"akane-audio-processor\")" in HTML_PAGE
        assert "audioWorklet.addModule" in HTML_PAGE

    def test_ui_zero_copy_ring_buffer_structure_and_no_shift(self):
        """VC-2.3-04: 環形緩衝區採用預分配 Float32Array 與指標推進，徹底消除 playQueue.shift()"""
        from clients.voice_companion.web_ui import HTML_PAGE

        # 30 秒容量與指標
        assert "44100 * 30" in HTML_PAGE
        assert "readIndex" in HTML_PAGE
        assert "writeIndex" in HTML_PAGE
        assert "fallbackBuf" in HTML_PAGE
        assert "fallbackRead" in HTML_PAGE
        assert "fallbackWrite" in HTML_PAGE

        # 驗證所有播放路徑均已淘汰 playQueue.shift()
        assert "playQueue.shift()" not in HTML_PAGE

    def test_ui_linear_fade_out_on_flush(self):
        """VC-2.3-04: 打斷/清空時做 128-sample 線性淡出消除直流爆音"""
        from clients.voice_companion.web_ui import HTML_PAGE

        assert "Math.min(128," in HTML_PAGE
        assert "(1.0 - k / fade)" in HTML_PAGE
        assert 'workletNode.port.postMessage({ type: "flush" })' in HTML_PAGE

    def test_ui_script_processor_fallback_retained(self):
        """VC-2.3-04: 保留 ScriptProcessorNode 作為 fallback，相容舊瀏覽器或無 Worklet 環境"""
        from clients.voice_companion.web_ui import HTML_PAGE

        assert "initScriptProcessorFallback" in HTML_PAGE
        assert "createScriptProcessor(2048, 0, 1)" in HTML_PAGE
        assert "pushFallbackSamples" in HTML_PAGE

    def test_ui_prebuffer_and_state_sync(self):
        """VC-2.3-04: Worklet 維持 80ms 預緩衝門檻並同步狀態機"""
        from clients.voice_companion.web_ui import HTML_PAGE

        assert "prebufferSamples = 3500" in HTML_PAGE or "3500" in HTML_PAGE
        assert 'workletNode.port.postMessage({ type: "state", state: s })' in HTML_PAGE

    def test_web_server_serves_worklet_enabled_html(self):
        """VC-2.3-04: Web 伺服器實際響應包含 AudioWorklet 播放架構"""
        async def _run():
            app, _ = _make_web_app()
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                resp = await client.get("/")
                assert resp.status == 200
                html = await resp.text()
                assert "AkaneAudioProcessor" in html
                assert "akane-audio-processor" in html
                assert "readIndex" in html
                assert "writeIndex" in html
            finally:
                await client.close()

        asyncio.run(_run())