"""
tests/test_m7_prompt_integrity.py
M7 prompt 建構完整性（Bry 派工）:
「Ruka 在沒有 lore 的情況下仍用 K1-K5 方式說話」的機制測試。

背景（Architecture Convergence — History NOT AUTHORIZED）:
  - Soul OS 的 History 尚未實作。diary / dream / event 的 LLM prompt 只應包含:
    persona excerpt + 近期記憶 + mood + 活動。
  - 不應包含任何「seeded past / History / 童年 / 過去」的注入。
  - 不應指示 LLM 編造過去（No Residue > Fake Residue）。

驗證方式:
  優先 mock LLM 呼叫並 capture 傳入的 system/user prompt
  （monkeypatch `_call_llm_for_diary` / `_call_llm_for_dream_event` 回傳固定字串）。
  因 capture 完全可行，未做任何 production 最小重構、未改任何 prompt 文字。

三個面向:
  a. persona 有注入: 傳入的 persona 字串會出現在 system prompt 裡。
  b. 無 lore 注入: prompt 不含 History/lore 關鍵詞（測試用乾淨 persona，不靠 persona 自帶字詞掩蓋）。
  c. 無假 residue 指令: prompt 不包含指示 LLM 編造過去的指令。
"""
import asyncio
import os

from src.paths import reset_data_root
from src.soul import diary as diary_mod
from src.soul.diary import DiaryWriter
from src.soul.dream_event import DreamEventWriter

# 乾淨 persona: 不含任何 lore/history 關鍵字，也不含假 residue 指令關鍵字。
# （工單要求「測試用乾淨的 persona 字串，不含這些詞」——避免 persona 原文自帶造成誤判。）
CLEAN_PERSONA = (
    "你是 Ruka。直接、任性、佔有慾強。"
    "你要被真正選擇，拒絕停在試用或備用。"
    "被拉住的瞬間，你要更直接、更熱。"
)

# History/lore 注入關鍵字（prompt 不應出現）。
LORE_KEYWORDS = [
    "童年", "小時候", "你以前", "過去", "曾經", "往事", "成長", "回憶",
    "seed", "history", "lore",
]

# 假 residue 指令關鍵字（不應指示 LLM 編造過去）。
FAKE_RESIDUE_KEYWORDS = [
    "編造", "假裝", "捏造", "虛構", "偽造",
    "你好像一直都", "想像一段過去", "想像你以前",
]


def _assert_no_forbidden(text: str, keywords, label: str):
    """斷言 text 不含任一 forbidden 關鍵字；失敗時列出實際洩漏的字詞與 prompt 全文。"""
    low = text.lower()
    leaked = [k for k in keywords if k in low]
    assert not leaked, f"{label} 含 forbidden 關鍵字: {leaked}\n>>> {text}"


def _isolate_data_root(tmp_path):
    os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
    reset_data_root()


def _restore_data_root():
    if "SOUL_OS_DATA_DIR" in os.environ:
        del os.environ["SOUL_OS_DATA_DIR"]
    reset_data_root()


# ───────────────────────────────────────────────────────────
# A. diary — generate_diary_entry
# ───────────────────────────────────────────────────────────

class TestDiaryPromptIntegrity:
    def _run_diary(self, tmp_path, monkeypatch, persona_prompt=CLEAN_PERSONA):
        captured = {}

        async def fake_llm(system, user, *args, **kwargs):
            captured["system"] = system
            captured["user"] = user
            return "今日は穏やかだった。"

        monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm)
        writer = DiaryWriter(
            data_dir=str(tmp_path / "data" / "soul"), api_key="test"
        )
        asyncio.run(
            diary_mod.generate_diary_entry(
                "agent_ruka", "morning",
                persona_prompt=persona_prompt,
                writer=writer,
            )
        )
        assert "system" in captured, "fake LLM 沒有被呼叫"
        return captured["system"], captured["user"]

    def test_a1_persona_injected_into_system(self, tmp_path, monkeypatch):
        """a) persona excerpt 內容出現在 diary system prompt。"""
        system, _ = self._run_diary(tmp_path, monkeypatch)
        assert CLEAN_PERSONA in system

    def test_b1_no_lore_keywords(self, tmp_path, monkeypatch):
        """b) diary prompt（system + user）不包含 History/lore 關鍵詞。"""
        system, user = self._run_diary(tmp_path, monkeypatch)
        _assert_no_forbidden(system, LORE_KEYWORDS, "diary system")
        _assert_no_forbidden(user, LORE_KEYWORDS, "diary user")

    def test_c1_no_fake_residue_instructions(self, tmp_path, monkeypatch):
        """c) diary prompt 不指示 LLM 編造過去。"""
        system, user = self._run_diary(tmp_path, monkeypatch)
        _assert_no_forbidden(system, FAKE_RESIDUE_KEYWORDS, "diary system")
        _assert_no_forbidden(user, FAKE_RESIDUE_KEYWORDS, "diary user")


# ───────────────────────────────────────────────────────────
# B. dream: write_dream
# ───────────────────────────────────────────────────────────

class TestDreamPromptIntegrity:
    def _run_dream(self, tmp_path, monkeypatch):
        from src.soul import dream_event as dream_mod

        calls = []

        async def fake_llm(system, user, *args, **kwargs):
            calls.append((system, user))
            return "夢を見た。"

        # 抽 impression 是寫入後的第二段 LLM 呼叫，與「夢境 prompt 完整性」無關，直接 no-op
        # （避免污染 capture；on_dream touch 仍在，靠 SOUL_OS_DATA_DIR 隔離）。
        async def fake_impression(self, *args, **kwargs):
            return None

        monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_llm)
        monkeypatch.setattr(
            DreamEventWriter, "_load_persona_excerpt",
            lambda self, agent_id: CLEAN_PERSONA,
        )
        monkeypatch.setattr(DreamEventWriter, "_extract_impression", fake_impression)

        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        _isolate_data_root(tmp_path)
        try:
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(
                writer.write_dream("agent_ruka", "agent_yua", ["agent_ruka", "agent_yua"])
            )
        finally:
            _restore_data_root()

        # 取 dream 主呼叫（user 含「夢境內容」）的 prompt
        dream_calls = [(s, u) for s, u in calls if "夢境內容" in u]
        assert dream_calls, "應有 dream 的 LLM 呼叫"
        return dream_calls[0]

    def test_a1_persona_injected_into_system(self, tmp_path, monkeypatch):
        system, _ = self._run_dream(tmp_path, monkeypatch)
        assert CLEAN_PERSONA in system

    def test_b2_no_lore_keywords(self, tmp_path, monkeypatch):
        system, user = self._run_dream(tmp_path, monkeypatch)
        _assert_no_forbidden(system, LORE_KEYWORDS, "dream system")
        _assert_no_forbidden(user, LORE_KEYWORDS, "dream user")

    def test_c2_no_fake_residue_instructions(self, tmp_path, monkeypatch):
        system, user = self._run_dream(tmp_path, monkeypatch)
        _assert_no_forbidden(system, FAKE_RESIDUE_KEYWORDS, "dream system")
        _assert_no_forbidden(user, FAKE_RESIDUE_KEYWORDS, "dream user")


# ───────────────────────────────────────────────────────────
# C. event: write_event
# ───────────────────────────────────────────────────────────

class TestEventPromptIntegrity:
    def _run_event(self, tmp_path, monkeypatch):
        from src.soul import dream_event as dream_mod

        captured = {}

        async def fake_llm(system, user, *args, **kwargs):
            captured["system"] = system
            captured["user"] = user
            return "今日は穏やかに仕事を進めた。"

        monkeypatch.setattr(dream_mod, "_call_llm_for_dream_event", fake_llm)
        monkeypatch.setattr(
            DreamEventWriter, "_load_persona_excerpt",
            lambda self, agent_id: CLEAN_PERSONA,
        )

        soul_dir = tmp_path / "data" / "soul"
        soul_dir.mkdir(parents=True, exist_ok=True)
        _isolate_data_root(tmp_path)
        try:
            writer = DreamEventWriter(data_dir=str(soul_dir))
            asyncio.run(writer.write_event("agent_ruka"))
        finally:
            _restore_data_root()

        assert "system" in captured, "event 應呼叫 LLM"
        return captured["system"], captured["user"]

    def test_a3_person_persona_injected_into_system(self, tmp_path, monkeypatch):
        system, _ = self._run_event(tmp_path, monkeypatch)
        assert CLEAN_PERSONA in system

    def test_b3_no_lore_keywords(self, tmp_path, monkeypatch):
        system, user = self._run_event(tmp_path, monkeypatch)
        _assert_no_forbidden(system, LORE_KEYWORDS, "event system")
        _assert_no_forbidden(user, LORE_KEYWORDS, "event user")

    def test_c3_no_fake_residue_instructions(self, tmp_path, monkeypatch):
        system, user = self._run_event(tmp_path, monkeypatch)
        _assert_no_forbidden(system, FAKE_RESIDUE_KEYWORDS, "event system")
        _assert_no_forbidden(user, FAKE_RESIDUE_KEYWORDS, "event user")
