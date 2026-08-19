"""
test_m7_3_longing.py
M7-3 (Bry 拍板 2026-08-18): 思念情感驅動 (依戀 × 沉默, 現算不存)

驗證:
  A. compute_longing 純函式 (依戀 × 沉默, clamp)
  B. _format_attachment_str 注入依戀事實 + fail-silent
  C. diary prompt 決策 #1 全落地 (「重要的人」取代「不是主題」)
"""
import asyncio
import os
from pathlib import Path

from src.agent.emotion import compute_longing
from src.llm.proxy import _format_attachment_str
from src.paths import reset_data_root
from src.soul import diary as diary_mod
from src.soul.diary import DiaryWriter


class TestComputeLonging:
    def test_formula(self):
        # 0.8 (80 intimacy) × 0.5 (12h silence) = 0.4
        assert compute_longing(80, 720) == 0.4
        # 0.4 × 0.5 = 0.2
        assert compute_longing(40, 720) == 0.2

    def test_zero_silence_is_zero_longing(self):
        assert compute_longing(80, 0) == 0.0

    def test_silence_saturates_at_24h(self):
        # 2880 min (48h) → silence_factor clamp 到 1.0 → 1.0 × 1.0 = 1.0
        assert compute_longing(100, 2880) == 1.0

    def test_intimacy_clamped_to_100(self):
        # 150 intimacy → clamp 1.0 → 1.0 × 0.5 = 0.5
        assert compute_longing(150, 720) == 0.5

    def test_zero_intimacy_is_zero_longing(self):
        assert compute_longing(0, 1440) == 0.0


class TestFormatAttachmentStr:
    def test_returns_attachment_fact(self, monkeypatch):
        import src.llm.proxy as proxy_mod

        class FakeEngine:
            def get(self, agent_id):
                return (0.5, 80)

        monkeypatch.setattr(proxy_mod, "emotion_engine", FakeEngine())
        assert _format_attachment_str("agent_ruka") == "你對 Bry 的親密度目前是 80/100。"

    def test_fail_silent_returns_empty(self, monkeypatch):
        import src.llm.proxy as proxy_mod

        class FakeEngineFail:
            def get(self, agent_id):
                raise Exception("boom")

        monkeypatch.setattr(proxy_mod, "emotion_engine", FakeEngineFail())
        assert _format_attachment_str("agent_ruka") == ""


class TestDiaryPromptDecision1:
    def test_prompt_uses_important_person_not_theme(self, tmp_path, monkeypatch):
        captured = {}

        async def fake_llm(system, user, api_key, *args, **kwargs):
            captured["system"] = system
            return "今日は穏やかだった。"

        monkeypatch.setattr(diary_mod, "_call_llm_for_diary", fake_llm)

        soul_dir = tmp_path / "data" / "soul"
        os.environ["SOUL_OS_DATA_DIR"] = str(tmp_path / "data")
        reset_data_root()
        try:
            writer = DiaryWriter(data_dir=str(soul_dir), api_key="test")
            asyncio.run(
                diary_mod.generate_diary_entry(
                    "agent_yua", "morning", persona_prompt="test persona", writer=writer
                )
            )
            sys_prompt = captured["system"]
            assert "重要的人" in sys_prompt
            assert "不是主題" not in sys_prompt
        finally:
            if "SOUL_OS_DATA_DIR" in os.environ:
                del os.environ["SOUL_OS_DATA_DIR"]
            reset_data_root()
