# -*- coding: utf-8 -*-
"""
tests/clients/test_vc_asr_homophone_gate.py
VC-ASR-HOMOPHONE-1（2026-10-14，Owner 裁示）：同音錯字必須真的被修，且**不打遠端 LLM**。

問題（唯讀審計實測，`scripts/manual_probe_vc_refiner_gate.py` 的發現）：
    needs_refiner("小欠早安")   -> (False, "clear")    ← 直接 bypass
    needs_refiner("排成跑起來") -> (False, "clear")    ← 直接 bypass
    local_refine("小欠早安")    -> "小茜，早安。"        ← 規則本身是好的
⇒ 模組職責 #1（同音錯字校準）在生產路徑上**從未被呼叫**，是死碼。

修法（零成本）：
- `needs_refiner()` 新增第 6 個 run 條件 `homophone`，只用**既有規則表**偵測，
  不新增規則（偵測與修補必須同一份真相）。
- `web_server` 對 `reason == "homophone"` **只跑本地 `local_refine()`**，
  不呼叫 `refine_speech_text()` ⇒ 不打遠端 LLM（VC-ASR-COND-1 的省碼意圖不得被破壞）。

⚠️ 本檔同時釘死一個容易漏的陷阱：homophone 分支**不得**讀 `last_refine_path`
——本回合根本沒呼叫 `refine_speech_text()`，該欄位會殘留**上一回合**的值。
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
from clients.voice_companion.asr_refiner import local_refine, needs_refiner
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
    # endpoint 有值時，生產 AsrRefiner.from_config() 會建出真實 LLM 通道。
    # 本檔用注入的 CountingRefiner 取代之，並斷言它 **0 次被呼叫**。
    "llm": {"endpoint": "", "api_key": ""},
    "web": {"host": "127.0.0.1", "port": 8767},
}

REFINE_TAG = "[ASR-REFINE] decision="

#: 審計探針的五個案例（逐字沿用 `scripts/manual_probe_vc_refiner_gate.py`）
PROBE_CASES = [
    ("同音錯字（稱呼）", "小欠早安", "小茜，早安。"),
    ("同音錯字（詞彙）", "排成跑起來", "排程跑起來。"),
]
NOISE_CASES = [
    ("太短", "嗯嗯", "too-short"),
    ("幻想重複", "呵呵呵呵呵呵呵呵", "repeat"),
]
CLEAR_CASE = "今天天氣還不錯"


# ─────────────────────────────────────────────────────────────
# 全離線 fakes（與 tests/clients 既有測試同介面）
# ─────────────────────────────────────────────────────────────


class FakeWebStreamer:
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
    """記錄呼叫次數的偽 Refiner；`last_refine_path` 可預設成殘留值以測陷阱。"""

    def __init__(self, result="淨化後文字。", stale_path: str | None = None):
        self.calls = 0
        self.seen: list[str] = []
        self.result = result
        self.last_refine_path = stale_path

    def refine_speech_text(self, text):
        self.calls += 1
        self.seen.append(text)
        self.last_refine_path = "llm"
        return self.result


async def _run_voice_turn(asr_text: str, refiner, timeout: float = 3.0) -> tuple[list[dict], list]:
    """回傳 (WS 事件, Voice Brain 實際收到的 LLM messages)。

    ⚠️ WS 的 `transcript role=user` 事件**本來就送原始 ASR 文字**（那是給 UI 顯示
    「他說了什麼」用的，VC-ASR-DIAG-1 的 raw→refined 只寫在日誌）。
    要驗證「校準後的文字真的進到角色眼前」，唯一正確的觀測點是
    Voice Brain 送給 LLM 的 messages —— 這也是既有 test_vc_asr_conditional_refiner.py
    的斷言方式。
    """
    seen_messages: list = []

    def _llm_stream(msgs):
        seen_messages.append(msgs)
        return iter(["好，我知道了。"])

    brain = AkaneVoiceBrain(llm_stream=_llm_stream)
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
    return events, seen_messages


def _brain_user_texts(seen_messages) -> list[str]:
    """Voice Brain 送給 LLM 的所有 user 內容（已是校準後的文字）。"""
    out: list[str] = []
    for msgs in seen_messages:
        for m in msgs:
            if m.get("role") == "user":
                out.append(m.get("content", ""))
    return out


def _refine_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if REFINE_TAG in r.getMessage()]


def _user_texts(events) -> list[str]:
    return [e.get("text") for e in events if e.get("type") == "transcript" and e.get("role") == "user"]


# ─────────────────────────────────────────────────────────────
# 1. 閘門決策：同音錯字必須放行，其餘語意完全不變
# ─────────────────────────────────────────────────────────────


class TestGateDecision:
    @pytest.mark.parametrize("label,text,expected", [(l, t, e) for l, t, e in PROBE_CASES])
    def test_homophone_now_runs(self, label, text, expected):
        run, reason = needs_refiner(text)
        assert (run, reason) == (True, "homophone"), (
            f"{label}：{text!r} 應命中 homophone run 條件（接通前是 (False, 'clear')）"
        )
        assert local_refine(text) == expected, "規則本身必須能修（否則就是偵得到、修不了）"

    def test_clear_sentence_still_bypasses(self):
        assert needs_refiner(CLEAR_CASE) == (False, "clear")

    @pytest.mark.parametrize("label,text,reason", NOISE_CASES)
    def test_existing_conditions_keep_priority(self, label, text, reason):
        """既有五個條件的優先序完全不動——homophone 只能搶走原本的 clear。"""
        assert needs_refiner(text) == (True, reason)

    def test_empty_still_bypasses(self):
        assert needs_refiner("") == (False, "empty")
        assert needs_refiner("   ") == (False, "empty")

    def test_standalone_homophone_char_is_detected(self):
        """單獨出現的同音字（前後不接中文字）也命中——與 AKANE_ALIAS_CHAR_RE 同源。

        注意：字串必須夠長才輪得到 homophone 條件（≤2 字會先命中 too-short，
        這是刻意保留的既有優先序）。
        """
        assert needs_refiner("好的，欠。") == (True, "homophone")


# ─────────────────────────────────────────────────────────────
# 2. 整合：homophone 路徑只跑本地規則，**零次**呼叫 LLM Refiner
# ─────────────────────────────────────────────────────────────


class TestVoiceTurnHomophonePath:
    @pytest.mark.parametrize("label,text,expected", PROBE_CASES)
    def test_refiner_llm_never_called(self, label, text, expected, caplog):
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        _events, seen = asyncio.run(_run_voice_turn(text, refiner))

        assert refiner.calls == 0, (
            f"{label}：homophone 路徑不得呼叫 refine_speech_text()"
            "（那是打遠端 LLM 的路徑，會多一次 HTTP + token）"
        )
        brain_texts = _brain_user_texts(seen)
        assert any(expected in t for t in brain_texts), (
            f"{label}：送進 Voice Brain 的應是校準後文字 {expected!r}，實得 {brain_texts}"
        )
        assert not any(text in t and expected not in t for t in brain_texts), (
            f"{label}：原始錯字不應直接進到角色眼前（實得 {brain_texts}）"
        )

    def test_decision_logged_as_run_with_local_path(self, caplog):
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        asyncio.run(_run_voice_turn(PROBE_CASES[0][1], refiner))

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "decision=run" in msg and "reason=homophone" in msg
        assert "path=local" in msg, f"應如實標本地規則路徑，實得：{msg}"

    def test_stale_last_refine_path_is_not_reported(self, caplog):
        """陷阱守衛：上一回合留下的 last_refine_path 不得被當成本回合的路徑。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner(stale_path="llm")  # 模擬殘留值
        asyncio.run(_run_voice_turn(PROBE_CASES[0][1], refiner))

        msg = _refine_records(caplog)[0].getMessage()
        assert "path=local" in msg, (
            f"homophone 分支沒呼叫 refine_speech_text()，last_refine_path 是上一回合的殘留值；"
            f"不得直接採用。實得：{msg}"
        )
        assert "path=llm" not in msg


# ─────────────────────────────────────────────────────────────
# 3. 回歸：clear 句仍然 0 次呼叫（省碼意圖不得被破壞）
# ─────────────────────────────────────────────────────────────


class TestBypassRegression:
    def test_clear_turn_still_never_calls_refiner(self, caplog):
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = CountingRefiner()
        _events, seen = asyncio.run(_run_voice_turn(CLEAR_CASE, refiner))

        assert refiner.calls == 0
        assert any(CLEAR_CASE in t for t in _brain_user_texts(seen)), "clear 句應原文直通"
        msg = _refine_records(caplog)[0].getMessage()
        assert "decision=bypass" in msg