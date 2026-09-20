"""
tests/clients/test_vc_asr_channel_hint.py
VC-ASR-CHANNEL-HINT-1 驗收測試（H1–H5；主座裁定 ①C：提示走 ephemeral system 側）。

本票＝薄票：只標通道、加一段固定短指令、把現成名冊交給本輪推理。
**不改辨識、不改 VAD／onset／needs_refiner、不新增 LLM 呼叫。**

契約（①C）：
  * 提示（標 ＋ 4.2 三段 ＋ 名冊一行）放在 **persona 的 system 訊息之後、user 訊息之前** 的
    **一則 ephemeral system** 裡（只此一 call；不進 `self.persona`、不進任何持久狀態）。
  * **user content 逐位元＝轉寫本文**（不得被 SAGE／session 記成「用戶說的話」）。
  * `source != "voice_asr"` ⇒ 整份 messages 與改動前逐位元相同（fail-closed）。

驗收矩陣：
  H1  ASR 回合 → system 側（含 ephemeral system）含 `source=voice_asr` ＋ 4.2 **三段各自
      逐字** ＋ `【名冊】` 行；**user content == 轉寫原文逐字**且不含任何本票字串。
  H2  文字路徑（`on_text`）／未傳 source → 以**正規化 messages 快照**（json.dumps）證明與
      「不傳 source 的組裝」逐字相等；**system 側亦不得有任何本票字串**。
  H3  名冊可用 → `【名冊】` 行逐字釘住，且薄函式輸出 == `AGENT_DISPLAY_NAMES` 值集合
      （防兩處定義漂移；該 import **只出現在測試**）。
  H4  名冊讀取丟例外／空清單 → 仍有標＋4.2、**無** `【名冊】`、**0 raise 到呼叫端**。
  H5  來源缺席／未知（`""`／`"telegram"`／`None`）→ 行為同 H2。

隔離：全離線、0 網路、0 檔案系統寫入（memory／session_store／temporal 顯式停用並斷言
`session_store is None`）、0 伺服器（直接構造 `WebSession`，不綁任何埠、不打生產埠）。
"""

from __future__ import annotations

import asyncio
import ast
import json
from pathlib import Path

import numpy as np
import pytest

from clients.voice_companion import agent_roster
from clients.voice_companion import akane_voice_brain as brain_mod
from clients.voice_companion.akane_voice_brain import (
    VC_ASR_SOURCE,
    AkaneVoiceBrain,
    asr_channel_prefix,
)
from clients.voice_companion.web_server import WebSession

REPO_ROOT = Path(__file__).resolve().parents[2]

MARK = "source=voice_asr"
SEG_1 = "【通道】這一句來自語音辨識，不是打字。用字與專有名詞可能錯。"
SEG_2 = "不要糾正對方的拼寫，不要拿錯字當笑點或劇情。"
SEG_3 = "若某詞接近名冊中的人，當成那個人；對不上就問一句，不要猜一段故事。"
SEGMENTS = (SEG_1, SEG_2, SEG_3)  # 三段必須各自獨立、可測
ROSTER_HEAD = "【名冊】"
PINNED_ROSTER_LINE = "【名冊】Yua, 更科瑠夏, 黒川あかね, 雷姆, Ram, 真昼, 杏奈, 麻衣, 三玖, 葵"
#: 本票不得出現在 user content／文字路徑的任何字串
HINT_TOKENS = (MARK, SEG_1, SEG_2, SEG_3, ROSTER_HEAD)

USER_TEXT = "下午我去公園散步"  # needs_refiner() == (False, "clear") → Refiner 不介入

CONFIG = {
    "companion": {"id": "agent_akane", "display_name": "黒川あかね", "short_name": "茜"},
    "fish_audio": {"mode": "live", "api_key": "", "voice_id": "", "model": "s2.1-pro-free"},
    "stt": {"engine": "fish", "language": "zh"},
    "vad": {"sample_rate": 16000},
    "llm": {"endpoint": "", "api_key": ""},
    "web": {"host": "127.0.0.1", "port": 0},
}

#: 大腦端隔離設定：停用 SessionStore / 記憶檢索 / 時序讀取（三者都會碰 data/**），
#: 使 `_build_messages` 只由 persona ＋ 傳入 history 決定（可重放的確定性組裝）。
ISOLATED_BRAIN_CONFIG = {
    "llm": {"endpoint": "", "api_key": ""},
    "memory": {
        "enabled": False,
        "session_store": {"enabled": False},
        "sage_write": {"enabled": False},
    },
    "temporal": {"enabled": False},
}


# ─────────────────────────────────────────────────────────────
# 全離線 fakes（0 網路 / 0 伺服器 / 0 檔案寫入）
# ─────────────────────────────────────────────────────────────

class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class _FakeStreamer:
    last_error = None

    def __init__(self) -> None:
        self.fed: list[str] = []
        self.started = 0

    def start(self) -> None:
        self.started += 1

    def feed_text_piece(self, piece: str) -> None:
        self.fed.append(piece)

    def end_session(self) -> None:
        pass

    def interrupt(self) -> None:
        pass

    def close(self) -> None:
        pass


class _FakeSink:
    def __init__(self) -> None:
        self.first_chunk_time = None
        self._written_chunks = 0
        self._written_bytes = 0

    def close(self) -> None:
        pass


class _FakeVAD:
    def __init__(self) -> None:
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1


class _FakeASR:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0
        self.last_error = None

    def transcribe(self, wav: bytes) -> str:
        self.calls += 1
        return self.text


def _make_brain(seen: list[list[dict]]) -> AkaneVoiceBrain:
    """真大腦（真 `_build_messages`）＋ 捕獲 messages 的假 llm_stream。

    **測試隔離（硬性）**：`AkaneVoiceBrain` 未注入時會自建 `SessionStore()`，而
    `web_server._finish` 會對它 `append_turn` ⇒ 直寫 `data/sessions/*.json`（生產狀態）。
    本測試以 config 顯式停用 `memory.session_store` / `memory` / `temporal`，
    並斷言真的停用了（隔離失效要大聲紅，不得靜默污染 data/）。
    """

    def _stream(messages):
        seen.append(messages)
        yield "收到。"

    brain = AkaneVoiceBrain(llm_stream=_stream, config=ISOLATED_BRAIN_CONFIG)
    assert brain.session_store is None, "隔離失敗：SessionStore 會讀寫 data/sessions/**"
    assert brain.memory_retriever is None, "隔離失敗：記憶檢索會讀 data/**"
    assert brain.temporal_provider is None
    brain.schedule_sage_commit = lambda *a, **k: None  # 0 SAGE 寫入（測試隔離）
    return brain


def _make_session(brain: AkaneVoiceBrain, asr_text: str = USER_TEXT) -> WebSession:
    return WebSession(
        _FakeWS(),
        config=CONFIG,
        brain=brain,
        refiner=object(),
        asr=_FakeASR(asr_text),
        streamer=_FakeStreamer(),
        detector=_FakeVAD(),
        sink=_FakeSink(),
    )


def _drive_asr_turn(session: WebSession) -> None:
    """走真實語音入口：`_handle_utterance`（ASR → bypass → _run_reply）。"""

    async def _run() -> None:
        session.state = session.STATE_LISTENING
        session._frames = [np.full(1600, 0.5, dtype=np.float32)]
        await session._handle_utterance()
        task = session._reply_task
        assert task is not None, "ASR 回合必須產生回覆任務（否則測不到組裝）"
        await task

    asyncio.run(_run())


def _drive_text_turn(session: WebSession) -> None:
    """走真實打字入口：`on_text`（同一條 `_run_reply` → `stream_respond` 鏈）。"""

    async def _run() -> None:
        await session.on_text(USER_TEXT)
        task = session._reply_task
        assert task is not None, "打字回合必須產生回覆任務"
        await task

    asyncio.run(_run())


def _snapshot(messages: list[dict]) -> str:
    """正規化 prompt 快照：json.dumps 後字串（逐位元比對用，非 grep）。"""
    return json.dumps(messages, ensure_ascii=False, sort_keys=False)


def _system_contents(messages: list[dict]) -> list[str]:
    return [m["content"] for m in messages if m["role"] == "system"]


def _system_blob(messages: list[dict]) -> str:
    return "\n".join(_system_contents(messages))


def _user_content(messages: list[dict]) -> str:
    users = [m for m in messages if m["role"] == "user"]
    assert len(users) == 1, f"本測試情境應只有 1 則 user 訊息，實得 {len(users)}"
    return users[0]["content"]


def _assert_no_hint(text: str, where: str) -> None:
    for token in HINT_TOKENS:
        assert token not in text, f"{where} 不得出現本票字串 {token!r}"


# ─────────────────────────────────────────────────────────────
# H1：ASR 回合 → ephemeral system 側注入（user 逐字不變）
# ─────────────────────────────────────────────────────────────

class TestH1AsrTurnInjectsChannelHint:
    def test_asr_turn_ephemeral_system_has_mark_and_three_segments(self):
        seen: list[list[dict]] = []
        session = _make_session(_make_brain(seen))
        _drive_asr_turn(session)

        assert len(seen) == 1, "本回合應恰好組裝一次 messages"
        messages = seen[0]

        # 兩則 system：persona ＋ 一則 ephemeral hint（順序＝persona → hint → user）
        system_contents = _system_contents(messages)
        assert len(system_contents) == 2, (
            f"應為 persona ＋ ephemeral system，實得 {len(system_contents)}"
        )
        assert [m["role"] for m in messages[:2]] == ["system", "system"]
        assert messages[-1]["role"] == "user"

        hint = system_contents[1]
        system_blob = _system_blob(messages)

        assert MARK in system_blob, "ASR 回合必須帶通道標（system 側）"
        # 三段各自 assert（不得只 assert 一句、不得黏成一句）
        assert SEG_1 in system_blob
        assert SEG_2 in system_blob
        assert SEG_3 in system_blob
        assert ROSTER_HEAD in system_blob, "名冊行必須在 system 側"
        # 三段必須是獨立子字串（彼此不得黏成一句）
        assert SEG_1 + SEG_2 not in system_blob
        assert SEG_2 + SEG_3 not in system_blob

        # ephemeral 塊逐位元＝ asr_channel_prefix()（標第一行、三段各自一行）
        assert hint == asr_channel_prefix()
        assert hint.startswith(MARK)

    def test_asr_turn_user_content_is_verbatim_transcript(self):
        """①C 的核心護欄：提示不得進 user 側（否則 SAGE／session 記成用戶說的話）。"""
        seen: list[list[dict]] = []
        brain = _make_brain(seen)
        session = _make_session(brain)
        _drive_asr_turn(session)

        user_content = _user_content(seen[0])
        assert user_content == USER_TEXT, "user content 必須逐位元＝轉寫本文"
        _assert_no_hint(user_content, "user content")

        # 結構證明：拿掉那則 ephemeral system 後，整份 messages 與『不傳 source』逐字相同
        stripped = [m for i, m in enumerate(seen[0]) if not (m["role"] == "system" and i == 1)]
        assert _snapshot(stripped) == _snapshot(brain._build_messages(USER_TEXT))
        # persona 區塊本身逐位元不變（hint 不得寫進 persona）
        assert _system_contents(seen[0])[0] == _system_contents(brain._build_messages(USER_TEXT))[0]
        assert MARK not in brain.persona and "【通道】" not in brain.persona

    def test_asr_turn_reply_still_produced(self):
        """注入不得破壞回合（仍正常串流、正常收尾）。"""
        seen: list[list[dict]] = []
        session = _make_session(_make_brain(seen))
        _drive_asr_turn(session)
        assert session._streamer.fed == ["收到。"]
        assert session.state == session.STATE_IDLE


# ─────────────────────────────────────────────────────────────
# H2：文字路徑與「未傳 source」逐位元不變（system 側亦無本票字串）
# ─────────────────────────────────────────────────────────────

class TestH2TextPathUntouched:
    def test_text_turn_snapshot_equals_no_source_assembly(self):
        seen: list[list[dict]] = []
        brain = _make_brain(seen)
        session = _make_session(brain)
        _drive_text_turn(session)

        assert len(seen) == 1
        text_path = _snapshot(seen[0])
        baseline = _snapshot(brain._build_messages(USER_TEXT))
        assert text_path == baseline, "打字路徑的組裝必須與『不傳 source』逐字相等"

        assert len(_system_contents(seen[0])) == 1, "打字路徑不得多出 ephemeral system"
        assert _user_content(seen[0]) == USER_TEXT, "打字路徑 user content 必須逐位元不變"
        _assert_no_hint(_system_blob(seen[0]), "打字路徑 system 側")
        _assert_no_hint(_snapshot(seen[0]), "打字路徑整份 messages")

    def test_unpassed_source_equals_empty_source(self):
        brain = _make_brain([])
        assert _snapshot(brain._build_messages(USER_TEXT)) == _snapshot(
            brain._build_messages(USER_TEXT, source="")
        )

    def test_only_asr_entry_marks_the_channel(self):
        """靜態護欄：web_server 內只有一處把通道標記傳下去（注入點唯一）。"""
        src = (REPO_ROOT / "clients" / "voice_companion" / "web_server.py").read_text(
            encoding="utf-8"
        )
        assert src.count("source=VC_ASR_SOURCE") == 1
        assert VC_ASR_SOURCE == "voice_asr"


# ─────────────────────────────────────────────────────────────
# H3：名冊可用 → 真實顯示名（並防兩處定義漂移）
# ─────────────────────────────────────────────────────────────

class TestH3RosterLine:
    def test_roster_line_present_with_real_display_names(self):
        brain = _make_brain([])
        messages = brain._build_messages(USER_TEXT, source=VC_ASR_SOURCE)
        hint = _system_contents(messages)[1]

        roster_lines = [ln for ln in hint.split("\n") if ln.startswith(ROSTER_HEAD)]
        assert len(roster_lines) == 1, f"應恰好一行名冊，實得 {roster_lines}"
        assert roster_lines[0] == PINNED_ROSTER_LINE
        assert _user_content(messages) == USER_TEXT

        names = agent_roster.display_names()
        assert names, "名冊來源必須讀得到（config agents 表 × gateway 顯示名對照）"
        assert any(n in roster_lines[0] for n in ("更科瑠夏", "黒川あかね", "雷姆", "三玖", "葵"))

        # 人名不得嵌進 4.2 三段（三段逐字固定，名冊只獨立成行）
        for seg in SEGMENTS:
            for name in names:
                assert name not in seg

    def test_display_names_match_gateway_roster(self):
        """薄函式輸出 == AGENT_DISPLAY_NAMES 值集合 ⇒ 防兩處定義漂移。

        `src.io.gateway` 的 import **只准出現在測試**（生產碼讀字面值，0 重依賴）。
        """
        from src.io.gateway import AGENT_DISPLAY_NAMES  # test-only import

        names = agent_roster.display_names()
        assert names, "名冊不得為空（否則本檢驗無意義）"
        assert set(names) == set(AGENT_DISPLAY_NAMES.values())
        assert [n for n in AGENT_DISPLAY_NAMES.values()] == names, "順序須與名冊宣告一致"

    def test_roster_module_imports_no_heavy_service_deps(self):
        """薄函式讀名冊**不得**把主服務重依賴（fastapi / src.io.gateway）拉進 VC。

        以 AST 掃描（repo 既有風格）而非 subprocess：`tests/infra` 的 R5 護欄要求
        掃描樹內每個行程衍生呼叫點都必須明文列管，本票不得改動該 allowlist。
        """
        src = (REPO_ROOT / "clients" / "voice_companion" / "agent_roster.py").read_text(
            encoding="utf-8"
        )
        imported: set[str] = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        banned = {"fastapi", "src.io.gateway"}
        assert not (imported & banned), f"不得 import 主服務重依賴：{sorted(imported & banned)}"
        assert not [m for m in imported if m.startswith("src.io")], (
            f"不得 import src.io.*（gateway 頂層拉 fastapi/eventbus）：{sorted(imported)}"
        )


# ─────────────────────────────────────────────────────────────
# H4：名冊失敗 fail-closed（仍有標＋4.2、無名冊、0 raise）
# ─────────────────────────────────────────────────────────────

class TestH4RosterFailClosed:
    def test_roster_raises_prefix_still_has_mark_and_segments(self, monkeypatch):
        def _boom():
            raise RuntimeError("roster read failed")

        monkeypatch.setattr(brain_mod, "display_names", _boom)
        prefix = asr_channel_prefix()  # 0 raise 到呼叫端

        assert prefix.startswith(MARK)
        assert SEG_1 in prefix and SEG_2 in prefix and SEG_3 in prefix
        assert ROSTER_HEAD not in prefix

    def test_roster_raises_through_build_messages(self, monkeypatch):
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(brain_mod, "display_names", _boom)
        brain = _make_brain([])
        messages = brain._build_messages(USER_TEXT, source=VC_ASR_SOURCE)
        hint = _system_contents(messages)[1]

        assert MARK in hint
        assert SEG_1 in hint and SEG_2 in hint and SEG_3 in hint
        assert ROSTER_HEAD not in hint
        assert _user_content(messages) == USER_TEXT

    def test_empty_roster_omits_roster_line(self, monkeypatch):
        """名冊為空 ⇒ 只輸出標＋三段（逐位元），不得吐空名冊行（契約 4.3）。"""
        monkeypatch.setattr(brain_mod, "display_names", lambda: [])
        prefix = asr_channel_prefix()
        assert prefix == "\n".join([MARK, SEG_1, SEG_2, SEG_3])
        assert ROSTER_HEAD not in prefix

    def test_roster_module_exception_returns_empty_list(self, monkeypatch):
        def _boom():
            raise RuntimeError("boom")

        monkeypatch.setattr(agent_roster, "_display_name_map", _boom)
        assert agent_roster.display_names() == []

    def test_roster_module_missing_source_returns_empty_list(self, monkeypatch, tmp_path):
        monkeypatch.setattr(agent_roster, "_GATEWAY_SRC", tmp_path / "does_not_exist.py")
        assert agent_roster.display_names() == []


# ─────────────────────────────────────────────────────────────
# H5：來源缺席／未知 ⇒ 同 H2
# ─────────────────────────────────────────────────────────────

class TestH5AbsentOrUnknownSource:
    @pytest.mark.parametrize("source", ["", "telegram", None])
    def test_absent_or_unknown_source_equals_unmarked(self, source):
        brain = _make_brain([])
        messages = brain._build_messages(USER_TEXT, source=source)
        assert _snapshot(messages) == _snapshot(brain._build_messages(USER_TEXT))

        assert len(_system_contents(messages)) == 1
        assert _user_content(messages) == USER_TEXT
        _assert_no_hint(_snapshot(messages), f"source={source!r} 的整份 messages")

    @pytest.mark.parametrize("source", ["", "telegram", None])
    def test_stream_respond_without_asr_source_never_injects(self, source):
        seen: list[list[dict]] = []
        brain = _make_brain(seen)
        assert list(brain.stream_respond(USER_TEXT, source=source)) == ["收到。"]
        assert _snapshot(seen[0]) == _snapshot(brain._build_messages(USER_TEXT))
        _assert_no_hint(_snapshot(seen[0]), f"source={source!r} 的 stream_respond messages")
