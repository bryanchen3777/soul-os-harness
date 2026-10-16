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
import tempfile
from pathlib import Path

import numpy as np
import pytest
from aiohttp import WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from clients.voice_companion.akane_voice_brain import AkaneVoiceBrain
from clients.voice_companion.asr_refiner import (
    build_refine_prompt,
    is_noise,
    local_refine,
    needs_refiner,
)
from clients.voice_companion.web_server import build_app

# ─────────────────────────────────────────────────────────────
# VC-REFINER-2（2026-10-16）：角色感知（companion-scoped）同音規則
#
# 舊版這支測試的 WEB_TEST_CONFIG 寫的是 **agent_rem / port 8767**，卻斷言
# `local_refine("小欠早安") == "小茜，早安。"` —— 當時 local_refine 完全沒有角色
# 感知，三個實例共用同一份寫死小茜的規則表，所以「雷姆實例把小欠修成小茜」
# 反而被釘成了契約。Owner 已裁示：這是**跨角色污染**，測試要改成對齊真實行為。
#
# 本檔現在分成兩層：
#   1. 預設 WEB_TEST_CONFIG = agent_akane（8765）—— 測「小茜的規則確實會生效」
#   2. TestAgentScopedIsolation —— 測「雷姆／麻衣實例不會被套上小茜的規則」
# ─────────────────────────────────────────────────────────────

# 三個實例的身分（與 clients/voice_companion/config.json、profiles/*.json 同源）
AKANE_COMPANION = {"id": "agent_akane", "display_name": "黑川茜", "short_name": "茜"}
MAI_COMPANION = {"id": "agent_mai", "display_name": "櫻島麻衣", "short_name": "麻衣"}
REM_COMPANION = {"id": "agent_rem", "display_name": "雷姆", "short_name": "雷姆"}


def _config_for(companion: dict, port: int) -> dict:
    """依角色產生一份 web_server config（小茜沒有 profiles/akane.json，她就是預設 config）。"""
    return {
        "companion": dict(companion),
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
        "web": {"host": "127.0.0.1", "port": port},
    }


WEB_TEST_CONFIG = _config_for(AKANE_COMPANION, 8765)
REM_TEST_CONFIG = _config_for(REM_COMPANION, 8767)
MAI_TEST_CONFIG = _config_for(MAI_COMPANION, 8766)

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


async def _run_voice_turn(asr_text: str, refiner, timeout: float = 3.0, config=None) -> tuple[list[dict], list]:
    """回傳 (WS 事件, Voice Brain 實際收到的 LLM messages)。

    `config` 預設用 WEB_TEST_CONFIG（agent_akane / 8765）；測其他角色時顯式傳入。

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

    # 【VC-REFINER-2 新增】注入 tmp session store。
    # 不注入時 SessionStore() 預設寫到 <repo>/data/sessions/——那是**生產狀態**
    # （AGENTS.md 第 8 條），而每跑一次測試就會把測試對話寫進 Bry 與角色的真實
    # 短期對話檔，並讓後續測試讀到前一次執行的殘留歷史（本次即因此轉紅）。
    # SessionStore 已支援 data_dir 注入；這裡改寫成測試專用暫存目錄。
    from clients.voice_companion.session_store import SessionStore

    brain = AkaneVoiceBrain(
        llm_stream=_llm_stream,
        session_store=SessionStore(data_dir=Path(tempfile.mkdtemp(prefix="so_vc_sessions_"))),
    )
    app = build_app(
        config or WEB_TEST_CONFIG,
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
        # 【VC-REFINER-2】PROBE_CASES 的兩筆都是小茜的規則，必須帶 AKANE_COMPANION。
        run, reason = needs_refiner(text, AKANE_COMPANION)
        assert (run, reason) == (True, "homophone"), (
            f"{label}：{text!r} 應命中 homophone run 條件（接通前是 (False, 'clear')）"
        )
        assert local_refine(text, AKANE_COMPANION) == expected, (
            "規則本身必須能修（否則就是偵得到、修不了）"
        )

    def test_clear_sentence_still_bypasses(self):
        assert needs_refiner(CLEAR_CASE, AKANE_COMPANION) == (False, "clear")

    @pytest.mark.parametrize("label,text,reason", NOISE_CASES)
    def test_existing_conditions_keep_priority(self, label, text, reason):
        """既有五個條件的優先序完全不動——homophone 只能搶走原本的 clear。"""
        assert needs_refiner(text, AKANE_COMPANION) == (True, reason)

    def test_empty_still_bypasses(self):
        assert needs_refiner("", AKANE_COMPANION) == (False, "empty")
        assert needs_refiner("   ", AKANE_COMPANION) == (False, "empty")

    def test_standalone_homophone_char_is_detected(self):
        """單獨出現的同音字（前後不接中文字）也命中——與角色的 char_re 同源。

        注意：字串必須夠長才輪得到 homophone 條件（≤2 字會先命中 too-short，
        這是刻意保留的既有優先序）。
        """
        assert needs_refiner("好的，欠。", AKANE_COMPANION) == (True, "homophone")


# ─────────────────────────────────────────────────────────────
# 1b. 【VC-REFINER-2】角色感知：規則必須綁定當前實例
# ─────────────────────────────────────────────────────────────


class TestAgentScopedIsolation:
    """Owner 2026-10-16 決策 1&2：跨角色不得誤修。

    背景（真實生產後果，非假想）：舊版 build_refine_prompt 對**雷姆實例**也下達
    「把千/欠/西改成茜」，實測把 ASR 本來就聽對的「雷姆」改寫成「蕾姆」
    （rem:3238 / rem:4162）與「茜」（rem:4227 / rem:5254）。
    """

    @pytest.mark.parametrize("companion", [MAI_COMPANION, REM_COMPANION])
    def test_akane_alias_does_not_fire_on_other_characters(self, companion):
        run, reason = needs_refiner("小欠早安", companion)
        assert (run, reason) == (False, "clear"), (
            f"{companion['id']}：小茜的別名規則不得在別的角色身上觸發"
            f"（實得 {(run, reason)}）"
        )
        assert local_refine("小欠早安", companion) == "小欠早安。", (
            f"{companion['id']}：不得把小欠修成小茜——那是跨角色污染"
        )

    def test_akane_alias_still_fires_on_akane(self):
        """反向守衛：隔離不是「把規則整個關掉」。"""
        assert local_refine("小欠早安", AKANE_COMPANION) == "小茜，早安。"

    @pytest.mark.parametrize("text", ["小欠早安", "小千早安", "小西早安", "好的，欠。"])
    def test_standalone_char_rule_also_scoped(self, text):
        assert needs_refiner(text, REM_COMPANION) == (False, "clear")

    def test_unknown_companion_falls_back_to_universal_only(self):
        """查不到角色時的安全預設：只跑通用規則，絕不猜。"""
        assert local_refine("小欠早安", {"id": "agent_unknown", "short_name": "誰"}) == "小欠早安。"
        assert local_refine("小欠早安") == "小欠早安。"  # 不傳 companion
        # 通用規則仍然生效
        assert local_refine("上午排成十點") == "上午排程十點。"

    # ── 通用規則（與角色無關）──────────────────────────────────
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("上午排成十點", "上午排程十點。"),
            ("拍成跑起来", "排程跑起来。"),
            ("姐姐在仙特塔里面。", "姐姐在贤者塔里面。"),
            ("姐姐在仙者塔里面。", "姐姐在贤者塔里面。"),
        ],
    )
    @pytest.mark.parametrize("companion", [None, AKANE_COMPANION, MAI_COMPANION, REM_COMPANION])
    def test_universal_rules_apply_to_everyone(self, text, expected, companion):
        assert local_refine(text, companion) == expected
        assert needs_refiner(text, companion) == (True, "homophone")


class TestRemRules:
    """雷姆專屬：正字保護 + 動詞同音（rem:8524 實測）。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("蕾姆想做自己的事情，好难。", "雷姆，想做自己的事情，好难。"),
            ("雷母在家好不好", "雷姆，在家好不好。"),
            ("裴雷姆在家，好不好？", "陪雷姆在家，好不好？"),
        ],
    )
    def test_rem_rules_fire(self, text, expected):
        assert needs_refiner(text, REM_COMPANION) == (True, "homophone")
        assert local_refine(text, REM_COMPANION) == expected

    def test_leimu_never_becomes_leiyin(self):
        """🔴 核心守衛：ASR 輸出**正確**的「雷姆」必須原封不動進大腦。

        這正是舊版 refiner 製造過的污染（雷姆 → 蕾姆）。守的是這個不變量，
        不是實作形狀。
        """
        assert local_refine("雷姆今天做了什麼", REM_COMPANION).startswith("雷姆")
        assert "蕾姆" not in (local_refine("雷姆今天做了什麼", REM_COMPANION) or "")

    def test_rem_rules_do_not_leak_to_other_characters(self):
        for c in (AKANE_COMPANION, MAI_COMPANION):
            assert local_refine("蕾姆想做自己的事情", c) == "蕾姆想做自己的事情。"

    def test_verb_fix_runs_before_name_fix(self):
        """順序敏感：裴雷姆 → 陪雷姆 必須先於任何雷姆規則，否則會變成「陪雷姆」再被二次處理。"""
        assert local_refine("裴雷姆", REM_COMPANION) == "陪雷姆。"


class TestMaiAntGuard:
    """Owner 決策 3：排除昆蟲量詞，涵蓋呼喚語境（mai:3917/4478/4635 實測）。"""

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("我的蚂蚁老婆呢？", "我的麻衣老婆呢？"),
            ("是蚂蚁，不是蚂蚁。", "是麻衣，不是麻衣。"),
            ("蚂蚁早安", "麻衣，早安。"),
            ("蚂蚁你在幹嘛", "麻衣，你在幹嘛。"),
        ],
    )
    def test_vocative_context_is_fixed(self, text, expected):
        assert needs_refiner(text, MAI_COMPANION) == (True, "homophone")
        assert local_refine(text, MAI_COMPANION) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "地上有一隻螞蟻。",
            "好幾隻螞蟻爬過去。",
            "這群螞蟻真多",
            "那隻螞蟻爬走了",
        ],
    )
    def test_insect_quantifier_context_is_untouched(self, text):
        """真在講昆蟲時**不得**改。這是本規則最危險的失敗模式。

        守的是「螞蟻沒被換成麻衣」這個不變量，不綁 local_refine 的句尾標點
        正規化（那與本規則無關）。
        """
        result = local_refine(text, MAI_COMPANION)
        assert "螞蟻" in result and "麻衣" not in result, (
            f"昆蟲語境的「螞蟻」不得被改成「麻衣」（實得 {result!r}）"
        )
        assert needs_refiner(text, MAI_COMPANION) == (False, "clear")

    @pytest.mark.parametrize("companion", [AKANE_COMPANION, REM_COMPANION])
    def test_ant_rule_is_mai_only(self, companion):
        assert local_refine("我的蚂蚁老婆呢？", companion) == "我的蚂蚁老婆呢？"

    def test_sakurashima_mai_is_fixed(self):
        """mai:4821 實測：櫻島麻衣被聽成「桜島舞」。"""
        assert local_refine("桜島舞。", MAI_COMPANION) == "麻衣。"
        assert needs_refiner("桜島舞。", MAI_COMPANION) == (True, "homophone")


class TestSessionStoreIsolation:
    """🔴 VC-REFINER-2：測試不得再把對話寫進生產 `data/sessions/`。

    2026-10-16 實測：SessionStore 原本寫死 `_repo_root()/data/sessions`，繞過全專案
    的資料根隔離機制，導致每跑一次 pytest 就改動 Bry 與角色的真實短期對話檔
    （agent_akane / agent_mai / agent_rem 三檔 mtime 隨每次執行前進），
    且下一次執行會讀到上次的殘留歷史 —— 測試變成執行間相依。

    守兩個不變量：**生產路徑不變**（env 未設時逐位元組相同）＋
    **測試路徑必須改道**（env 有設時）。
    """

    def test_production_path_unchanged_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("SOUL_OS_DATA_DIR", raising=False)
        from clients.voice_companion import session_store as ss

        expected = ss._repo_root() / "data" / "sessions"
        assert ss.SessionStore().data_dir == expected, (
            "生產（env 未設）時必須維持原本的 repo data/sessions 路徑"
        )

    def test_test_run_is_redirected_to_env_data_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SOUL_OS_DATA_DIR", str(tmp_path))
        from clients.voice_companion import session_store as ss

        got = ss.SessionStore().data_dir
        assert got == tmp_path.resolve() / "sessions", (
            f"測試必須寫到暫存資料根，實得 {got}"
        )
        # 反向守衛：不得寫回生產目錄
        assert "soul-os-harness" not in str(got)

    def test_autouse_fixture_already_isolates_this_module(self):
        """本模組的測試跑在 autouse fixture 的 tmp 資料根下（這是上面那條會生效的前提）。"""
        import os

        assert os.environ.get("SOUL_OS_DATA_DIR"), (
            "tests/conftest.py 的 autouse fixture 應已設定 SOUL_OS_DATA_DIR；"
            "若沒設定，SessionStore 隔離會失效並寫進生產資料"
        )


class TestHallucinationFuse:
    """rem:10119 實測：越南文幻覺曾穿過整套閘門直送大腦。"""

    @pytest.mark.parametrize("text", ["nếu như không à", "มึงไรหมูพูดเย้าไรหมู", "😀"])
    def test_foreign_script_hallucination_is_dropped(self, text):
        assert is_noise(text) is True
        assert local_refine(text, REM_COMPANION) is None

    @pytest.mark.parametrize("text", ["こんにちは", "あかね早安", "안녕하세요", "下午我去公園散步", "123", "OK", "Rust 1.0"])
    def test_legitimate_text_is_not_dropped(self, text):
        """守衛：假名／韓文／純數字／純 ASCII 都必須放行。

        特別是「あかね」——那是小茜的喚醒詞之一，只收漢字的 CJK 判準會誤殺它。
        """
        assert is_noise(text) is False


class TestPromptIsProfileAware:
    """決策 4：build_refine_prompt 不得再硬寫黑川茜（那是污染的根因）。"""

    def test_rem_prompt_names_rem(self):
        prompt = build_refine_prompt("測試", REM_COMPANION)
        assert "雷姆" in prompt
        assert "黑川茜" not in prompt
        assert "小茜" not in prompt, "雷姆的 prompt 不得列出小茜的名字"

    def test_mai_prompt_names_mai(self):
        prompt = build_refine_prompt("測試", MAI_COMPANION)
        assert "麻衣" in prompt
        assert "黑川茜" not in prompt

    def test_akane_prompt_names_akane(self):
        prompt = build_refine_prompt("測試", AKANE_COMPANION)
        assert "黑川茜" in prompt

    def test_unknown_companion_does_not_invent_a_name(self):
        """寧可少修，不可改錯角色。"""
        prompt = build_refine_prompt("測試", None)
        assert "黑川茜" not in prompt and "麻衣" not in prompt and "雷姆" not in prompt


class TestEndToEndAgentIsolation:
    """【VC-REFINER-2】端到端：整條 WS 管線下，角色隔離真的成立。

    單元測試只證明規則函式正確；這裡證明 **web_server 真的有把 companion
    傳下去**——那條接線是本次改動最容易漏、也最會靜默失效的地方
    （漏掉時所有單元測試仍全綠，雷姆實例照樣被改成小茜）。
    """

    def test_rem_instance_never_gets_akane_alias(self):
        # ⚠️ 哨兵輸入刻意選「小西晚安」而非 PROBE_CASES 用的「小欠早安」：
        #    Voice Brain 會把回合寫進跨回合 session 記錄，而本專案的測試在同一
        #    行程內共用該記錄。若這裡送進「小欠早安」，後續
        #    TestVoiceTurnHomophonePath::test_refiner_llm_never_called 的
        #    「原始錯字不應出現在任何 brain 訊息」斷言會被**本測試的前一筆對話**
        #    誤觸而轉紅 —— 那會讓測試套件變成順序相依。用不重疊的輸入消除這個耦合。
        refiner = CountingRefiner()
        _events, seen = asyncio.run(
            _run_voice_turn("小西晚安", refiner, config=REM_TEST_CONFIG)
        )
        # 只看**本回合**送進大腦的最後一則 user 訊息：Voice Brain 會帶入跨回合
        # session 歷史（先前的對話紀錄），那是合法的既有內容，不是本回合的轉寫結果。
        # 用「整份清單不得出現小茜」會誤判——那是測試隔離問題，不是規則問題。
        assert _brain_user_texts(seen), "本測試需要觀察到送進 Voice Brain 的文字"
        current_turn = _brain_user_texts(seen)[-1]
        assert "小西" in current_turn, (
            f"雷姆實例的本回合原文應保留「小西」，實得 {current_turn!r}"
        )
        assert "小茜" not in current_turn, (
            f"雷姆實例不得收到小茜的字（實得 {current_turn!r}）"
        )

    def test_rem_instance_applies_its_own_rules(self):
        refiner = CountingRefiner()
        _events, seen = asyncio.run(
            _run_voice_turn("蕾姆今天做了什麼", refiner, config=REM_TEST_CONFIG)
        )
        assert any("雷姆" in t for t in _brain_user_texts(seen))

    def test_mai_instance_applies_its_own_rules(self):
        refiner = CountingRefiner()
        _events, seen = asyncio.run(
            _run_voice_turn("我的蚂蚁老婆呢", refiner, config=MAI_TEST_CONFIG)
        )
        assert any("麻衣" in t for t in _brain_user_texts(seen))

    def test_akane_instance_applies_its_own_rules(self):
        refiner = CountingRefiner()
        _events, seen = asyncio.run(
            _run_voice_turn("小欠早安", refiner, config=WEB_TEST_CONFIG)
        )
        assert any("小茜" in t for t in _brain_user_texts(seen))

    def test_bypass_decision_logs_the_raw_text(self, caplog):
        """【VC-REFINER-2】bypass 也要留下原文。

        真實考古發現：那 145 回合全是 bypass，而舊日誌只印 reason 不印原文，
        導致「被聽錯卻繞過整個 refiner」的句子在日誌裡完全不可見。
        """
        caplog.set_level(logging.INFO, logger="vc.web_server")
        asyncio.run(_run_voice_turn(CLEAR_CASE, CountingRefiner()))
        msg = _refine_records(caplog)[0].getMessage()
        assert "decision=bypass" in msg
        assert CLEAR_CASE in msg, f"bypass 日誌必須帶原文，實得：{msg}"


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