"""
tests/clients/test_vc_asr_refine_observability.py
VC-ASR-DIAG-1（P2：只加可觀測性、0 行為變更）驗收測試。

背景（2026-09-16 真實事件）：Owner 回報 VC 語音「有時候一句話還是會砍一兩個字」，
掉的字在句子開頭。舊日誌有 `[UTT] asr-ok text=`（原始轉錄）與
`[ASR-REFINE] decision=...`（決策），但**沒有淨化後的文字**、也**沒有產出 path**
（LLM 精煉 vs 規則式 local_refine），因此無法判斷掉字發生在上游 ASR 還是淨化層；
`18:08:58` 的 `[UTT] refine-drop` 更是只有一行、沒有原文、沒有原因。

本檔覆蓋（對應工單 D1 / D2 與驗收 1）：
  D1  `AsrRefiner.last_refine_path` 三種值（llm / local / drop），全部以**注入 llm_call**
      造出；並驗連續呼叫**不殘留**上一次的值（先 drop 再 local ⇒ 第二次必須是 local）。
  D1  **0 行為變更**：`refine_speech_text()` 回傳值逐字不變——預期值在本檔寫死成常數
      （常數取自本票前的行為：llm_call 未注入時 = local_refine()，注入時 = LLM 輸出）。
  D2  `[ASR-REFINE] decision=run` **同一則訊息**含
      `path=<llm|local|drop> raw='<原文>' -> refined='<淨化後>'`（print 與 log.info 各一條）。
  D2  run 但 DROP ⇒ `path=drop ... -> refined=None`，且既有 `[UTT] refine-drop` 保留。
  D2  bypass 輸出不變（不得長出 path/raw/refined 欄位）。
  D2  日誌文字各截到 80 字元（尾端加 `…`），且**送往大腦的文字不得被截斷**。

全離線：注入 fake brain/asr/refiner/streamer，0 網路（本機 aiohttp TestServer）。
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
from clients.voice_companion.asr_refiner import AsrRefiner, local_refine, needs_refiner
from clients.voice_companion.web_server import (
    ASR_LOG_TEXT_LIMIT,
    _truncate_for_log,
    build_app,
)

# ─────────────────────────────────────────────────────────────
# 常數（本票前的行為；刻意寫死，作為「輸出逐字不變」的鋼印）
# ─────────────────────────────────────────────────────────────

# 本票前 refine_speech_text()（llm_call=None）逐字輸出：寫死為常數
LOCAL_MODE_EXPECTED = [
    ("下午我去公園散步", "下午我去公園散步。"),
    ("下午拍成三點開會", "下午排程三點開會。"),
    ("小欠你好", "小茜，你好。"),
    ("呃，那個，我想說的是排成三點", "我想說的是排程三點。"),
    ("是是是是是是", "是是是是是是。"),
    ("嗯。", None),      # 純雜音熔斷 → DROP
    ("那個", None),      # 規則式剝離後為空 → DROP
]

# 本票前 refine_speech_text()（注入 llm_call）逐字輸出：LLM 非空回覆一律被採用
LLM_REPLY = "茜，我想排程。"
LLM_MODE_EXPECTED = [
    ("是是是是是是", LLM_REPLY),
    ("hello world", LLM_REPLY),
    ("嗯。", None),      # is_noise 短路，LLM 不得被呼叫
]

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

REFINE_TAG = "[ASR-REFINE] decision="

# run 條件觸發詞（needs_refiner → (True, "repeat")）
RUN_INPUT = "是是是是是是"
# run + 雜音熔斷（needs_refiner → (True, "noise") → refine 回 None → DROP）
DROP_INPUT = "嗯嗯嗯。"
# run + 規則式路徑（注入空回覆 LLM ⇒ 落到 local_refine）
LOCAL_MODE_INPUT = "是是是是是是"
LOCAL_MODE_CLEAN = "是是是是是是。"

# 長文本（> ASR_LOG_TEXT_LIMIT）：驗日誌截斷 **且** 送往大腦的文字不截斷
LONG_SENTENCE = "今天天氣很好我們一起去公園散步好嗎我想說的是明天早上九點記得提醒我帶傘出門"
LONG_LLM_REPLY = LONG_SENTENCE * 3  # 120 字元 > 80


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


def _run_voice_turn(asr_text: str, refiner, timeout: float = 3.0):
    """驅動一整個語音回合，回傳 (WS 事件清單, 送進大腦的 user 文字清單)。

    觸發 web_server.WebSession._handle_utterance()：
    ASR 轉錄 → needs_refiner() 決策 → Refiner（可能呼叫）→ Voice Brain 回覆 → IDLE。
    送進大腦的文字由 llm_stream 攔截 messages[-1]["content"] 取得（證明未被日誌截斷影響）。
    """
    seen_user_texts: list[str] = []

    def _llm_stream(messages):
        seen_user_texts.append(messages[-1]["content"])
        return iter(["好，我知道了。"])

    brain = AkaneVoiceBrain(llm_stream=_llm_stream)
    app = build_app(
        WEB_TEST_CONFIG,
        brain=brain,
        refiner=refiner,
        asr=FakeWebASR(asr_text),
        streamer_factory=lambda sink: FakeWebStreamer(sink=sink),
    )
    client = None

    async def _drive() -> list[dict]:
        nonlocal client
        client = TestClient(TestServer(app))  # 必須在 running loop 內建構（aiohttp 契約）
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

    return asyncio.run(_drive()), seen_user_texts


def _refine_records(caplog) -> list[logging.LogRecord]:
    """caplog 中所有 [ASR-REFINE] 結構化日誌。"""
    return [r for r in caplog.records if REFINE_TAG in r.getMessage()]


# ─────────────────────────────────────────────────────────────
# D1：last_refine_path 三種值
# ─────────────────────────────────────────────────────────────

class TestD1LastRefinePath:
    def test_initial_value_is_none_before_any_call(self):
        """尚未呼叫過 ⇒ None（不得謊稱任何路徑）。"""
        assert AsrRefiner(llm_call=None).last_refine_path is None
        assert AsrRefiner(llm_call=lambda p: LLM_REPLY).last_refine_path is None

    def test_path_llm_when_llm_output_adopted(self):
        """LLM 精煉成功且採用其輸出 ⇒ last_refine_path == 'llm'。"""
        refiner = AsrRefiner(llm_call=lambda prompt: LLM_REPLY)
        out = refiner.refine_speech_text("呃，那個，排成三點")
        assert out == LLM_REPLY
        assert refiner.last_refine_path == "llm"

    def test_path_local_when_llm_returns_empty(self):
        """LLM 回覆空白（無效）⇒ 落到 local_refine，如實標 'local'。"""
        refiner = AsrRefiner(llm_call=lambda prompt: "   ")
        out = refiner.refine_speech_text(LOCAL_MODE_INPUT)
        assert out == LOCAL_MODE_CLEAN
        assert refiner.last_refine_path == "local"

    def test_path_local_when_llm_raises(self):
        """LLM 呼叫失敗（例外）⇒ 落到 local_refine，如實標 'local'（不得標 llm）。"""
        def _boom(prompt):
            raise RuntimeError("LLM 掛了")

        refiner = AsrRefiner(llm_call=_boom)
        out = refiner.refine_speech_text(LOCAL_MODE_INPUT)
        assert out == LOCAL_MODE_CLEAN
        assert refiner.last_refine_path == "local"

    def test_path_local_when_llm_not_injected(self):
        """未注入 LLM（離線確定性模式）⇒ 規則式產出，標 'local'。"""
        refiner = AsrRefiner(llm_call=None)
        assert refiner.refine_speech_text("下午拍成三點開會") == "下午排程三點開會。"
        assert refiner.last_refine_path == "local"

    def test_path_drop_for_noise_input(self):
        """純雜音（is_noise 短路）⇒ 結果 None，標 'drop'。"""
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        assert refiner.refine_speech_text("嗯。") is None
        assert refiner.last_refine_path == "drop"

    def test_path_drop_when_llm_says_EMPTY(self):
        """LLM 判定雜音（回覆 EMPTY）⇒ 結果 None，標 'drop'。"""
        refiner = AsrRefiner(llm_call=lambda p: "EMPTY")
        assert refiner.refine_speech_text(LOCAL_MODE_INPUT) is None
        assert refiner.last_refine_path == "drop"

    def test_path_drop_when_local_refine_yields_none(self):
        """非雜音輸入但規則式剝離後為空 ⇒ 結果 None，標 'drop'（不得標 local）。"""
        refiner = AsrRefiner(llm_call=None)
        assert needs_refiner("那個")[0] is True  # 前提：此輸入確實會走 run 路徑
        assert refiner.refine_speech_text("那個") is None
        assert refiner.last_refine_path == "drop"

    def test_no_residue_drop_then_local(self):
        """連續呼叫不殘留：先 drop、再 local ⇒ 第二次必須是 'local'。"""
        refiner = AsrRefiner(llm_call=lambda p: "")
        assert refiner.refine_speech_text("嗯。") is None
        assert refiner.last_refine_path == "drop"
        assert refiner.refine_speech_text(LOCAL_MODE_INPUT) == LOCAL_MODE_CLEAN
        assert refiner.last_refine_path == "local", "第二次呼叫不得殘留上一次的 'drop'"

    def test_no_residue_llm_then_drop(self):
        """連續呼叫不殘留（反向）：先 llm、再 drop ⇒ 第二次必須是 'drop'。"""
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        assert refiner.refine_speech_text(LOCAL_MODE_INPUT) == LLM_REPLY
        assert refiner.last_refine_path == "llm"
        assert refiner.refine_speech_text("嗯。") is None
        assert refiner.last_refine_path == "drop", "第二次呼叫不得殘留上一次的 'llm'"

    def test_no_residue_llm_then_rule_based_drop(self):
        """殘留防護（純殘留情境）：先造一次 'llm'，再讓下一次的結果為 None（規則式剝離後
        為空）⇒ 第二次必須是 'drop'，絕不得殘留上一次的 'llm'。"""
        reply = {"text": LLM_REPLY}
        refiner = AsrRefiner(llm_call=lambda p: reply["text"])
        assert refiner.refine_speech_text(RUN_INPUT) == LLM_REPLY
        assert refiner.last_refine_path == "llm"
        reply["text"] = ""  # LLM 失效 → 落到規則式；"那個" 規則式剝離後為空 → DROP
        assert refiner.refine_speech_text("那個") is None
        assert refiner.last_refine_path == "drop", "第二次呼叫不得殘留上一次的 'llm'"

    def test_no_residue_local_then_drop_then_llm(self):
        """三段連續呼叫：local → drop → llm，路徑每次都換新。"""
        reply = {"text": ""}
        refiner = AsrRefiner(llm_call=lambda p: reply["text"])
        assert refiner.refine_speech_text(LOCAL_MODE_INPUT) == LOCAL_MODE_CLEAN
        assert refiner.last_refine_path == "local"
        assert refiner.refine_speech_text("嗯。") is None
        assert refiner.last_refine_path == "drop"
        reply["text"] = LLM_REPLY
        assert refiner.refine_speech_text(LOCAL_MODE_INPUT) == LLM_REPLY
        assert refiner.last_refine_path == "llm"

    def test_value_domain_is_limited_to_three_values(self):
        """值域只有 llm / local / drop（或尚未呼叫過的 None）。"""
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        seen = {refiner.last_refine_path}
        for text, _ in LOCAL_MODE_EXPECTED:
            refiner.refine_speech_text(text)
            seen.add(refiner.last_refine_path)
        assert seen <= {None, "llm", "local", "drop"}


# ─────────────────────────────────────────────────────────────
# D1：輸出逐字不變（0 行為變更）
# ─────────────────────────────────────────────────────────────

class TestD1OutputVerbatimUnchanged:
    @pytest.mark.parametrize("raw,expected", LOCAL_MODE_EXPECTED)
    def test_local_mode_output_verbatim(self, raw, expected):
        """未注入 LLM：回傳值必須逐字等於本票前寫死的常數。"""
        refiner = AsrRefiner(llm_call=None)
        assert refiner.refine_speech_text(raw) == expected

    @pytest.mark.parametrize("raw,expected", LLM_MODE_EXPECTED)
    def test_llm_mode_output_verbatim(self, raw, expected):
        """注入 LLM：回傳值必須逐字等於本票前寫死的常數（含雜音短路）。"""
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        assert refiner.refine_speech_text(raw) == expected

    @pytest.mark.parametrize("raw,expected", LOCAL_MODE_EXPECTED)
    def test_module_level_default_refiner_output_verbatim(self, raw, expected):
        """模組級 refine_speech_text()（無狀態預設實例）輸出同樣逐字不變。"""
        from clients.voice_companion.asr_refiner import refine_speech_text as module_refine

        assert module_refine(raw) == expected

    def test_local_refine_itself_untouched(self):
        """規則式核心 local_refine() 未被本票改動（觀測欄位不得污染它）。"""
        assert local_refine("下午我去公園散步") == "下午我去公園散步。"
        assert local_refine("上午排成十點") == "上午排程十點。"
        assert local_refine("嗯。") is None


# ─────────────────────────────────────────────────────────────
# D2：web_server 日誌（run 路徑：path + raw → refined）
# ─────────────────────────────────────────────────────────────

class TestD2RunPathLog:
    def test_run_success_logs_path_llm_and_raw_to_refined(self, caplog, capsys):
        """run 且成功（LLM）⇒ 同一則訊息含 path=llm 與 raw='..' -> refined='..'。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        events, seen_user_texts = _run_voice_turn(RUN_INPUT, refiner)

        assert refiner.last_refine_path == "llm"
        assert seen_user_texts == [LLM_REPLY], "送往大腦的必須是淨化後文字"

        records = _refine_records(caplog)
        assert len(records) == 1, "每回合必須剛好一筆 [ASR-REFINE] 決策日誌（不得另開一行）"
        msg = records[0].getMessage()
        assert "decision=run" in msg and "reason=repeat" in msg and "chars=6" in msg
        assert "conf=none" in msg and "elapsed_ms=" in msg
        assert "path=llm" in msg
        assert f"raw='{RUN_INPUT}' -> refined='{LLM_REPLY}'" in msg

        out = capsys.readouterr().out
        assert f"path=llm raw='{RUN_INPUT}' -> refined='{LLM_REPLY}'" in out, \
            "print 版本也必須帶 path 與 raw→refined"

    def test_run_local_path_logged_as_local(self, caplog):
        """run 但 LLM 無效 ⇒ 落到規則式，日誌 path=local（不得標 llm）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = AsrRefiner(llm_call=lambda p: "")
        _run_voice_turn(LOCAL_MODE_INPUT, refiner)

        assert refiner.last_refine_path == "local"
        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "decision=run" in msg and "path=local" in msg
        assert f"raw='{LOCAL_MODE_INPUT}' -> refined='{LOCAL_MODE_CLEAN}'" in msg

    def test_run_drop_logs_path_drop_and_keeps_refine_drop_line(self, caplog, capsys):
        """run 但 DROP ⇒ path=drop 且 refined=None；既有 [UTT] refine-drop 必須保留。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        events, seen_user_texts = _run_voice_turn(DROP_INPUT, refiner)

        assert refiner.last_refine_path == "drop"
        assert seen_user_texts == [], "DROP 回合不得把任何文字送進大腦"
        states = [e.get("state") for e in events if e.get("type") == "state"]
        assert states[-1] == "IDLE"

        records = _refine_records(caplog)
        assert len(records) == 1, "run 路徑仍必須剛好一筆 [ASR-REFINE] 決策日誌"
        msg = records[0].getMessage()
        assert "decision=run" in msg and "path=drop" in msg
        assert f"raw='{DROP_INPUT}' -> refined=None" in msg

        # 既有診斷行保留（可能有測試或維運習慣依賴）
        drop_records = [r for r in caplog.records if r.getMessage() == "[UTT] refine-drop"]
        assert len(drop_records) == 1
        out = capsys.readouterr().out
        assert "[UTT] refine-drop" in out
        assert f"path=drop raw='{DROP_INPUT}' -> refined=None" in out

    def test_bypass_line_unchanged(self, caplog, capsys):
        """bypass 輸出不變：既有格式逐字保留，且不得長出 path/raw/refined 欄位。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        clear = "下午我去公園散步"
        refiner = AsrRefiner(llm_call=lambda p: LLM_REPLY)
        _run_voice_turn(clear, refiner)

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert msg.startswith(f"[ASR-REFINE] decision=bypass reason=clear chars={len(clear)} conf=none elapsed_ms=")
        assert "path=" not in msg and "raw=" not in msg and "refined=" not in msg
        out = capsys.readouterr().out
        assert f"[ASR-REFINE] decision=bypass reason=clear chars={len(clear)} conf=none elapsed_ms=" in out

    def test_refiner_exception_is_logged_as_drop(self, caplog):
        """Refiner 拋例外（web_server 吞掉 → clean='') ⇒ 如實標 path=drop，不殘留舊值。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")

        class BoomRefiner:
            last_refine_path = "llm"  # 假裝上一回合是 llm，驗證呼叫端不得被殘值騙到

            def refine_speech_text(self, text):
                raise RuntimeError("refiner 爆了")

        _run_voice_turn(RUN_INPUT, BoomRefiner())

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert "decision=run" in msg and "path=drop" in msg
        assert f"raw='{RUN_INPUT}' -> refined=None" in msg


class TestD2Truncation:
    def test_truncate_helper_limits_and_marks(self):
        """截斷只作用於日誌字串：≤80 原樣，>80 取前 80 加 '…'。"""
        assert ASR_LOG_TEXT_LIMIT == 80
        assert _truncate_for_log("短句") == "短句"
        exactly = "字" * 80
        assert _truncate_for_log(exactly) == exactly, "剛好 80 字元不得被截"
        over = "字" * 81
        assert _truncate_for_log(over) == "字" * 80 + "…"
        assert len(_truncate_for_log("字" * 500)) == 81

    def test_long_text_truncated_in_log_only(self, caplog, capsys):
        """長文本：日誌 raw/refined 各截到 80 + '…'；送往大腦的文字必須完整（120 字）。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        assert len(LONG_LLM_REPLY) > ASR_LOG_TEXT_LIMIT
        refiner = AsrRefiner(llm_call=lambda p: LONG_LLM_REPLY)
        events, seen_user_texts = _run_voice_turn(RUN_INPUT, refiner)

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert f"raw='{RUN_INPUT}'" in msg, "短的 raw 不應被截"
        assert f"-> refined='{LONG_LLM_REPLY[:80]}…'" in msg, "refined 應截到 80 字元 + '…'"
        assert LONG_LLM_REPLY not in msg, "完整長文本不得進日誌"

        # 送往大腦的文字完整（未被日誌截斷影響）
        assert seen_user_texts == [LONG_LLM_REPLY]

    def test_long_raw_truncated_in_log_and_full_text_reaches_brain(self, caplog):
        """長 raw（>80）：日誌 raw 截到 80 + '…'，但 Refiner 收到的是完整原文。"""
        caplog.set_level(logging.INFO, logger="vc.web_server")
        long_raw = "是" * 6 + "，" + LONG_SENTENCE * 2
        assert len(long_raw) > ASR_LOG_TEXT_LIMIT
        seen_raws: list[str] = []

        def _llm(prompt: str):
            seen_raws.append(prompt)
            return LLM_REPLY

        _run_voice_turn(long_raw, AsrRefiner(llm_call=_llm))

        assert len(seen_raws) == 1
        assert long_raw in seen_raws[0], "Refiner 必須收到完整原文（截斷只發生在日誌）"

        records = _refine_records(caplog)
        assert len(records) == 1
        msg = records[0].getMessage()
        assert f"raw='{long_raw[:80]}…'" in msg
        assert long_raw not in msg
