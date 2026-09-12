"""
tests/clients/test_vc_asr_conditional_refiner.py
VC-ASR-COND-1 階段 2 — 條件式 ASR Refiner 執行（預設 Bypass）驗收測試。

對應工單驗收：
  Test A（核心）：清晰完整語句 → needs_refiner() == (False, "clear")，且該路徑下
                 Refiner（LLM 淨化器）呼叫次數 == 0（原始轉錄直通 Voice Brain）。
  Test B（異常觸發）：too-short / noise / repeat / punct-only / punct-heavy
                 5 個 run 條件各自命中 → needs_refiner() == (True, <對應 reason>)。
  Test C（回歸）：既有 is_noise() 雜音熔斷行為未被破壞；空字串走 bypass reason=empty。
  日誌：`[ASR-REFINE] decision=...` 結構化日誌確實印出（log.info + stdout print 各一條），
        且 conf= 恆為 none（Fish ASR 回應沒有官方 confidence/score 欄位，不得捏造）。

全離線：注入 fake brain/asr/refiner/streamer，0 網路。

備註（原始碼缺陷與最小修正；修正與測試分屬兩個 commit）：
  原始 needs_refiner() 把 is_noise() 檢查放在「core 為空（全標點）」之前，而 is_noise()
  對空 core 恆回 True（既有熔斷器語義），導致 punct-only 分支成為不可達死碼——
  「！！！」「。！？」等全標點輸入實際回 (True, "noise")，reason 標籤與規格不符。
  最小修正：在 needs_refiner() 內把「core 為空 → punct-only」判定上移到 is_noise() 之前
  （is_noise() 本身 0 改動，Test C 守護）。修正只影響全標點輸入的 reason 標籤
  （noise → punct-only），Run/Bypass 決策完全不變。
"""

from __future__ import annotations

import asyncio
import json
import logging

import numpy as np
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.asr_refiner import (
    REPEAT_RUN_LIMIT,
    is_noise,
    needs_refiner,
)
from clients.voice_companion.web_server import build_app

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

# Test A 核心樣句：清晰、完整、無異常特徵的日常語句
CLEAR_SENTENCE = "下午我去公園散步"
REFINE_TAG = "[ASR-REFINE] decision="


# ─────────────────────────────────────────────────────────────
# 全離線 fakes（介面與既有 tests/clients 測試一致）
# ─────────────────────────────────────────────────────────────

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
    def __init__(self, text=""):
        self.text = text
        self.calls = 0

    def transcribe(self, wav_bytes):
        self.calls += 1
        return self.text


class CountingRefiner:
    """記錄呼叫次數與參數的偽 Refiner（介面：refine_speech_text）。"""

    def __init__(self, result="淨化後文字。"):
        self.calls = 0
        self.seen: list[str] = []
        self.result = result

    def refine_speech_text(self, text):
        self.calls += 1
        self.seen.append(text)
        return self.result


async def _run_voice_turn(asr_text: str, refiner, timeout: float = 3.0) -> list[dict]:
    """以語音路徑（ptt_start → PCM → ptt_stop）驅動一整個回合，回傳 WS 事件清單。

    觸發 web_server.WebSession._handle_utterance()：
    ASR 轉錄 → needs_refiner() 決策 → Refiner（可能呼叫）→ Voice Brain 回覆 → IDLE。
    """
    brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["好，我知道了。"]))
    app = build_app(
        WEB_TEST_CONFIG,
        brain=brain,
        refiner=refiner,
        asr=FakeWebASR(asr_text),
        streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    events: list[dict] = []
    try:
        ws = await client.ws_connect("/ws")
        await ws.send_json({"type": "ptt_start"})
        pcm = (np.full(1600, 0.5) * 32767).astype(np.int16).tobytes()
        await ws.send_bytes(pcm)
        await ws.send_bytes(pcm)
        await ws.send_json({"type": "ptt_stop"})
        while True:
            try:
                msg = await ws.receive(timeout=timeout)
            except asyncio.TimeoutError:
                break
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
    return events


def _refine_records(caplog) -> list[logging.LogRecord]:
    """caplog 中所有 [ASR-REFINE] 結構化日誌。"""
    return [r for r in caplog.records if REFINE_TAG in r.getMessage()]


# ─────────────────────────────────────────────────────────────
# Test A：清晰句子預設 Bypass（核心）
# ─────────────────────────────────────────────────────────────

class TestAClearSentenceBypass:
    def test_clear_sentence_decides_bypass_clear(self):
        """「下午我去公園散步」→ (False, "clear")：預設 bypass，Refiner 不介入。"""
        run, reason = needs_refiner(CLEAR_SENTENCE)
        assert run is False
        assert reason == "clear"

    def test_clear_sentence_with_punct_also_bypasses(self):
        """含正常標點的完整語句 → 仍為 clear（標點數不超過文字數）。"""
        run, reason = needs_refiner("下午我去公園散步，就五點吧。")
        assert (run, reason) == (False, "clear")

    def test_voice_turn_bypass_never_calls_refiner(self, caplog, capsys):
        """整合：語音路徑下清晰語句 → Refiner 呼叫次數 == 0，回合正常完成。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        events = asyncio.run(_run_voice_turn(CLEAR_SENTENCE, refiner))

        assert refiner.calls == 0, "bypass 路徑 Refiner（LLM 淨化器）不得被呼叫"
        user_texts = [e.get("text") for e in events if e.get("type") == "transcript" and e.get("role") == "user"]
        assert CLEAR_SENTENCE in user_texts, "原始轉錄應直通 Voice Brain（transcript 未經淨化）"
        states = [e.get("state") for e in events if e.get("type") == "state"]
        assert states[-1] == "IDLE", "回合必須有明確結束路徑"

        records = _refine_records(caplog)
        assert len(records) == 1, "每回合必須剛好一筆 [ASR-REFINE] 決策日誌"
        msg = records[0].getMessage()
        assert "decision=bypass" in msg and "reason=clear" in msg
        assert f"chars={len(CLEAR_SENTENCE)}" in msg
        assert "conf=none" in msg, "Fish ASR 無官方 confidence 欄位，conf 恆為 none"
        assert "elapsed_ms=" in msg

        out = capsys.readouterr().out
        assert f"[ASR-REFINE] decision=bypass reason=clear chars={len(CLEAR_SENTENCE)} conf=none elapsed_ms=" in out, \
            "print 版本的決策日誌也必須印到 stdout"


# ─────────────────────────────────────────────────────────────
# Test B：異常條件觸發 Refiner（5 個 run 條件）
# ─────────────────────────────────────────────────────────────

class TestBAbnormalTriggersRun:
    @pytest.mark.parametrize(
        "text,reason",
        [
            ("hi", "too-short"),            # ≤2 字元
            ("嗯。", "too-short"),           # ≤2 字元（先於 noise 判定）
            ("嗯嗯嗯。", "noise"),           # 純雜音音節（≥3 字元才不被 too-short 攔截）
            ("啊...啊", "noise"),           # 雜音 + 省略號 → 仍為 noise 熔斷
            ("是是是是是是", "repeat"),      # 單字元連續重複 = REPEAT_RUN_LIMIT
            ("呵呵呵呵呵呵", "repeat"),      # 6 連「呵」（非 NOISE_SYLLABLES 成員）
            ("！！！", "punct-only"),        # 全標點（core 為空）
            ("。！？", "punct-only"),        # 全標點（core 為空）
            ("A，，，，。", "punct-heavy"),   # 非文字符號數 > 文字字元數
        ],
    )
    def test_run_conditions(self, text, reason):
        run, actual = needs_refiner(text)
        assert (run, actual) == (True, reason), f"{text!r} 應命中 run 條件 {reason!r}"

    def test_repeat_threshold_is_six(self):
        """REPEAT_RUN_LIMIT == 6：5 連不觸發（clear），6 連觸發 repeat。"""
        assert REPEAT_RUN_LIMIT == 6
        assert needs_refiner("是" * (REPEAT_RUN_LIMIT - 1)) == (False, "clear")
        assert needs_refiner("是" * REPEAT_RUN_LIMIT) == (True, "repeat")

    def test_voice_turn_run_path_calls_refiner_once(self, caplog):
        """整合：異常輸入（repeat）→ decision=run，Refiner 剛好呼叫一次。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        asyncio.run(_run_voice_turn("是是是是是是", refiner))

        assert refiner.calls == 1, "run 路徑 Refiner 必須被呼叫"
        assert refiner.seen == ["是是是是是是"], "Refiner 收到的必須是原始轉錄文本"

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "decision=run" in msg and "reason=repeat" in msg
        assert "chars=6" in msg
        assert "conf=none" in msg and "elapsed_ms=" in msg


# ─────────────────────────────────────────────────────────────
# Test C：既有雜音熔斷回歸 + 空字串 bypass
# ─────────────────────────────────────────────────────────────

class TestCNoiseFuseRegression:
    def test_is_noise_preserved_for_pure_noise(self):
        """既有 is_noise() 熔斷行為未被破壞：純雜音/感嘆詞 → True。"""
        for text in ("嗯。", "啊。", "呼", "嗯嗯嗯", "呃……", "哈哈"):
            assert is_noise(text) is True, f"is_noise({text!r}) 應為 True"

    def test_is_noise_still_false_for_real_speech(self):
        """既有 is_noise() 對真實語句仍回 False（不被誤熔斷）。"""
        for text in ("下午我去公園散步", "嗯，我想想", "哈囉，茜"):
            assert is_noise(text) is False, f"is_noise({text!r}) 應為 False"

    def test_empty_string_bypasses_with_empty_reason(self):
        """空/全空白輸入 → (False, "empty") 走 bypass（不是 run）。"""
        assert needs_refiner("") == (False, "empty")
        assert needs_refiner("   ") == (False, "empty")
        assert needs_refiner("\n\t ") == (False, "empty")
        assert needs_refiner("")[0] is False, "空字串不得觸發 Refiner"

    def test_empty_voice_turn_logs_bypass_empty(self, caplog):
        """整合：ASR 空轉錄 → [ASR-REFINE] decision=bypass reason=empty chars=0，Refiner 0 呼叫。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        asyncio.run(_run_voice_turn("", refiner))

        assert refiner.calls == 0
        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "decision=bypass" in msg and "reason=empty" in msg
        assert "chars=0" in msg
        assert "conf=none" in msg and "elapsed_ms=" in msg