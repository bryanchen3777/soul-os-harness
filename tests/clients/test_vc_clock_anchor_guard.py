"""
tests/clients/test_vc_clock_anchor_guard.py
VC-CLOCK-ANCHOR（2026-10-28 Owner 裁定）驗收測試（G1–G6）。

故障實測（2026-10-28）
----------------------
真實紐約時間 **11:27:18**（UTC 15:27:18）時，使用者問「现在才几点」，
代理答「**現在嗎。一點二十七分。**」⇒ 報成 13:27＝**快 2 小時**（分鐘 27 對、小時 11→13 錯）。
全 repo 時區管路已逐層驗證正確（`src/timezone_utils` / `src.llm.proxy`）⇒ **不是時區轉換缺陷**；
真正的成因有兩個，兩者都必須處理（Owner 裁定「兩個都做」）。

兩道機關（分工明確，互不越界）
------------------------------
* **D1 機械式擋截**（`AkaneVoiceBrain._guarded`）：輸出含「具體時鐘時間」⇒ **整句剔除**
  （🔴 不得改寫成另一個時間數字）；剔除後為空 ⇒ 走**既有 fallback**。
  🔴 不得誤傷模糊表述（「今天比較晚出現呢」「都這麼晚了還不睡嗎」「下午的光線很舒服」）。
  守門 **fail-closed**：偵測不到就原樣放行；任何例外 ⇒ 放行原文並記 log。
* **D2 權威時間注入**（`AkaneVoiceBrain._build_messages`）：system prompt 新增
  【當前本地時間（權威）】區塊（放在【當前時序體感】之後、歷史之前）。
  時間值**必須**由 `src.timezone_utils` 的既有工具（`now_local()`／`format_localized()`）取得
  ⇒ 不得寫死 offset、不得自行組時區字面值。區塊是**權威事實**，不是報時指令
  ⇒ 不得出現「必須報時」之類的語句（報不報由 persona 的 `expression_mode` 決定）。

驗收矩陣
--------
* **G1** 含「X點Y分」的句子**被剔除**，且最終輸出**不含任何時鐘數字**（＋ D3 觀測 log）。
* **G2** 含「HH:MM」／「現在是 3 點」的句子同樣被剔除。
* **G3** **合法**的模糊時間表述**不被剔除**（逐字保留）。
* **G4** 剔除後為空 ⇒ 走**既有 fallback**，且 fallback **不含時鐘數字**。
* **G5** 注入的時間值與 `now_local()` **同刻一致**且**帶時區鍵**。
* **G6** 注入區塊**不含**「必須報時」之類的指令語句。

隔離：全離線、0 網路、**0 真實 LLM**（fake `llm_stream` 注入）、0 檔案系統寫入
（memory／session_store／temporal 顯式停用並斷言）、0 伺服器（不綁埠、不打生產埠）。
本檔**不得**使用 `subprocess`／`os.system`／`os.popen`／`os.spawn*`
（`tests/infra/test_no_production_spawn_guard.py` R5：整棵掃描樹零白名單新增）。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import pytest

from clients.voice_companion.akane_voice_brain import (
    VC_CLOCK_ANCHOR_HEADING,
    AkaneVoiceBrain,
)
from src.timezone_utils import LOCAL_TZ, now_local

# ─────────────────────────────────────────────────────────────
# 隔離設定：停用 SessionStore / 記憶檢索 / 時序讀取（三者都會碰 data/**）
# ─────────────────────────────────────────────────────────────

ISOLATED_BRAIN_CONFIG = {
    "llm": {"endpoint": "", "api_key": ""},
    "memory": {
        "enabled": False,
        "session_store": {"enabled": False},
        "sage_write": {"enabled": False},
    },
    "temporal": {"enabled": False},
}

#: **獨立**的「時鐘數字」偵測（不重用生產碼的判準 ⇒ 守衛不與被守者共謀）
_CLOCK_NUMBER_RE = re.compile(
    r"\d{1,2}\s*[:：]\s*\d{2}"                     # HH:MM（13:27）
    r"|\d{1,2}\s*點"                               # 3點 / 13點
    r"|[零〇一二兩三四五六七八九十]{1,3}\s*點"     # 一點 / 十一點
)

#: 注入區塊的時間戳樣式（`format_localized()` 的輸出：日期 ＋ 星期 ＋ HH:MM ＋ 時區鍵）
_STAMP_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})\s+週[一二三四五六日]\s+(\d{2}:\d{2})\s+([A-Za-z_]+(?:/[A-Za-z_]+)?)"
)

#: G6：注入區塊**不得**出現的「報時指令」語句
_FORBIDDEN_REPORT_MANDATES = (
    "必須報時",
    "務必報時",
    "必須報出",
    "務必報出",
    "必須回答",
    "務必回答",
    "一定要報",
    "請報時",
    "必須告訴",
    "務必告訴",
    "必須提及",
    "必須說出",
    "你必須",
)


def _has_clock_number(text: str) -> bool:
    """文字是否含**任何**時鐘數字（獨立判準）。"""
    return bool(_CLOCK_NUMBER_RE.search(text or ""))


class _ScriptedStream:
    """假 `llm_stream`：回放預設輸出（0 網路、0 真實 LLM、0 成本）。"""

    def __init__(self, script: str) -> None:
        self.script = script
        self.calls = 0
        self.seen: list[list[dict]] = []

    def __call__(self, messages):
        self.calls += 1
        self.seen.append(messages)
        yield self.script


def _make_brain(script: str) -> AkaneVoiceBrain:
    """真大腦（真 `_build_messages` ＋ 真 `_guarded`）＋ 捕獲 messages 的假 llm_stream。

    **測試隔離（硬性）**：`AkaneVoiceBrain` 未注入時會自建 `SessionStore()` ⇒ 讀寫
    `data/sessions/*.json`（生產狀態）。本檔以 config 顯式停用 memory／session_store／
    temporal，並斷言真的停用了（隔離失效要大聲紅，不得靜默污染 data/）。
    """
    brain = AkaneVoiceBrain(
        llm_stream=_ScriptedStream(script),
        config=ISOLATED_BRAIN_CONFIG,
    )
    assert brain.session_store is None, "隔離失敗：SessionStore 會讀寫 data/sessions/**"
    assert brain.memory_retriever is None, "隔離失敗：記憶檢索會讀 data/**"
    assert brain.temporal_provider is None, "隔離失敗：時序讀取會讀 data/**"
    brain.schedule_sage_commit = lambda *a, **k: None  # 0 SAGE 寫入（測試隔離）
    return brain


def _respond(script: str, user_text: str = "现在才几点") -> str:
    """走真 `respond()`（內含真 `_guarded`）。"""
    return _make_brain(script).respond(user_text)


def _system_content(brain: AkaneVoiceBrain, user_text: str = "现在才几点") -> str:
    """真 `_build_messages()` 的 system 側全文。"""
    messages = brain._build_messages(user_text)
    return "\n".join(m["content"] for m in messages if m["role"] == "system")


def _clock_block(system: str) -> str:
    """取出【當前本地時間（權威）】區塊本體（含標題行）。"""
    assert VC_CLOCK_ANCHOR_HEADING in system, (
        f"system prompt 必須含權威時間區塊：{system!r}"
    )
    start = system.index(VC_CLOCK_ANCHOR_HEADING)
    rest = system[start + len(VC_CLOCK_ANCHOR_HEADING):]
    end = rest.find("\n\n")  # 區塊之間以空行分隔
    return VC_CLOCK_ANCHOR_HEADING + (rest if end == -1 else rest[:end])


# ─────────────────────────────────────────────────────────────
# G1：中文「X點Y分」被剔除 ＋ 輸出不含任何時鐘數字（＋ D3 觀測 log）
# ─────────────────────────────────────────────────────────────

class TestG1ChineseClockRemoved:
    def test_g1_chinese_clock_sentence_is_removed(self, caplog):
        script = "現在嗎。一點二十七分。晚餐還早。"
        with caplog.at_level(logging.WARNING, logger="soul_os.vc_brain"):
            out = _respond(script)

        assert "一點二十七分" not in out, f"含時鐘數字的句子必須被剔除：{out!r}"
        assert not _has_clock_number(out), f"輸出不得含任何時鐘數字：{out!r}"
        assert "現在嗎" in out, f"非報時內容必須保留：{out!r}"

        # D3：每次擋截寫一行 log（命中句、型態、最終輸出長度）
        logs = [
            r.getMessage()
            for r in caplog.records
            if "VC-CLOCK-GUARD" in r.getMessage()
        ]
        assert any(
            "一點二十七分" in msg and "type=" in msg and "final_len=" in msg
            for msg in logs
        ), f"擋截必須留下可 grep 的觀測 log：{logs}"


# ─────────────────────────────────────────────────────────────
# G2：阿拉伯數字形式（HH:MM ／ 3 點）同樣被剔除
# ─────────────────────────────────────────────────────────────

class TestG2ArabicClockRemoved:
    @pytest.mark.parametrize(
        "script,forbidden",
        [
            ("現在是 13:27。晚安。", "13:27"),
            ("現在是 3 點。晚安。", "3 點"),
        ],
    )
    def test_g2_arabic_clock_sentences_are_removed(self, script, forbidden):
        out = _respond(script)

        assert forbidden not in out, f"含時鐘數字的句子必須被剔除：{out!r}"
        assert not _has_clock_number(out), f"輸出不得含任何時鐘數字：{out!r}"


# ─────────────────────────────────────────────────────────────
# G3：合法模糊表述**不被剔除**（逐字保留）
# ─────────────────────────────────────────────────────────────

class TestG3LegitVagueKept:
    @pytest.mark.parametrize(
        "script",
        [
            "今天比較晚出現呢。",
            "都這麼晚了還不睡嗎。",
            "下午的光線很舒服。",
        ],
    )
    def test_g3_legit_vague_expressions_are_kept(self, script):
        out = _respond(script, user_text="你在嗎")

        assert out == script, f"合法模糊表述必須逐字保留：{out!r}"
        assert not _has_clock_number(out), f"合法模糊表述不得含時鐘數字：{out!r}"


# ─────────────────────────────────────────────────────────────
# G4：剔除後為空 ⇒ 走既有 fallback，且 fallback 不含時鐘數字
# ─────────────────────────────────────────────────────────────

class TestG4EmptyFallsBack:
    def test_g4_empty_after_removal_uses_existing_fallback(self):
        brain = _make_brain("現在是 13:27。")
        out = brain.respond("现在才几点")

        # 「走既有 fallback」＝ 與守門既有的空輸出 fallback 逐位元相同
        assert out == brain._guarded(""), f"剔除後應走既有 fallback：{out!r}"
        assert out, "fallback 不得為空"
        # 「fallback 不含時鐘數字」
        assert not _has_clock_number(out), f"fallback 不得含時鐘數字：{out!r}"


# ─────────────────────────────────────────────────────────────
# G5：注入值與 now_local() 同刻一致且帶時區鍵
# ─────────────────────────────────────────────────────────────

class TestG5AuthoritativeTime:
    def test_g5_injected_time_matches_now_local_and_carries_tz_key(self):
        brain = _make_brain("……")

        before = now_local()
        system = _system_content(brain)
        after = now_local()

        block = _clock_block(system)
        match = _STAMP_RE.search(block)
        assert match, f"權威區塊必須含時間戳：{block!r}"

        date_s, hhmm, tz_key = match.group(1), match.group(2), match.group(3)
        injected = datetime.strptime(
            f"{date_s} {hhmm}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=LOCAL_TZ)

        # 同刻一致（注入值為分鐘解析度 ⇒ 允許 1 分鐘誤差、且不得晚於取值當刻）
        assert before - timedelta(minutes=1) <= injected <= after + timedelta(seconds=5), (
            f"注入值 {injected.isoformat()} 必須與 now_local() 同刻一致"
            f"（before={before.isoformat()} after={after.isoformat()}）"
        )

        # 帶時區鍵（且必須是本地時區，不得是 UTC）
        assert tz_key == LOCAL_TZ.key, f"注入值必須帶本地時區鍵：{tz_key}"
        assert tz_key in block


# ─────────────────────────────────────────────────────────────
# G6：注入區塊不含「必須報時」指令語句
# ─────────────────────────────────────────────────────────────

class TestG6NoReportMandate:
    def test_g6_injected_block_has_no_report_mandate(self):
        brain = _make_brain("……")
        system = _system_content(brain)
        block = _clock_block(system)

        for token in _FORBIDDEN_REPORT_MANDATES:
            assert token not in block, (
                f"權威區塊是**事實**不是報時指令，不得含 {token!r}：{block!r}"
            )
