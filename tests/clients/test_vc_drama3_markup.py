"""tests/clients/test_vc_drama3_markup.py — drama-3 表演標記白名單與守門（Owner 2026-10-24）。

背景：drama-3 能「演出」方向指示與配對標籤，但 VC 原本的語音守門會把它們整段吃掉
（連 `<emphasis>真的</emphasis>` 裡的「真的」都不見）。Owner 裁示開放，
本檔守住兩件事：
  ① 允許的標記**必須活到 TTS**（引擎才演得出來）；
  ② 不允許的東西**照舊被剝除**（工具標籤／Markdown／（動作））。
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.akane_voice_brain import (
    AKANE_DRAMA3_MARKUP_RULES,
    AKANE_VOICE_INVARIANTS,
    AkaneVoiceBrain,
    ClauseSplitter,
    StreamingVoiceSanitizer,
    contains_markdown_chars,
    sanitize_voice_output,
)
from clients.voice_companion.drama3_markup import (
    MAX_DIRECTIONS_PER_REPLY,
    is_allowed_tag,
    is_valid_direction,
    strip_drama3_markup,
    supports_drama3_markup,
)


class TestWhitelist:
    def test_paired_tags_allowed(self):
        for t in ("whisper", "emphasis", "soft", "fast", "slow", "stress", "forceful"):
            assert is_allowed_tag(t) is True

    def test_language_tags_allowed(self):
        for t in ("chinese", "english", "japanese", "korean"):
            assert is_allowed_tag(t) is True

    def test_unknown_tags_not_allowed(self):
        for t in ("write_file", "thinking", "b", "script", "tool_call"):
            assert is_allowed_tag(t) is False

    def test_only_drama3_supports_markup(self):
        assert supports_drama3_markup("drama-3") is True
        assert supports_drama3_markup("s2.1-pro") is False
        assert supports_drama3_markup("") is False


class TestDirectionValidation:
    def test_valid_chinese_direction(self):
        assert is_valid_direction("像是在强忍着眼泪，却努力笑着") is True

    def test_valid_english_sound(self):
        assert is_valid_direction("shaky breath") is True
        assert is_valid_direction("small laugh") is True

    def test_markdown_like_rejected(self):
        assert is_valid_direction("1") is False          # 清單編號
        assert is_valid_direction("") is False
        assert is_valid_direction("註") is False          # 太短
        assert is_valid_direction("a" * 60) is False      # 太長
        assert is_valid_direction("...") is False         # 純標點

    def test_nested_brackets_rejected(self):
        assert is_valid_direction("像[a]這樣") is False


class TestStripForDisplay:
    def test_paired_tag_removed_but_content_kept(self):
        assert strip_drama3_markup("<whisper>真的</whisper> 我很好。") == "真的 我很好。"

    def test_direction_removed_entirely(self):
        assert strip_drama3_markup("[像是在哭]我没事。") == "我没事。"

    def test_invalid_brackets_kept(self):
        assert strip_drama3_markup("[1] 條列。") == "[1] 條列。"


class TestStaticGuard:
    def test_allowed_markup_survives(self):
        out = sanitize_voice_output("<emphasis>真的</emphasis> 我很好。", allow_drama3=True)
        assert out == "<emphasis>真的</emphasis> 我很好。"

    def test_direction_survives(self):
        out = sanitize_voice_output("[像是在强忍着眼泪，却努力笑着] 我没事。", allow_drama3=True)
        assert out == "[像是在强忍着眼泪，却努力笑着] 我没事。"

    def test_tool_tags_still_stripped(self):
        out = sanitize_voice_output("<write_file path='x'>壞內容</write_file>你好。", allow_drama3=True)
        assert out == "你好。"
        assert "write_file" not in out

    def test_markdown_still_stripped(self):
        out = sanitize_voice_output("**粗體** <b>x</b> 條列。", allow_drama3=True)
        assert "**" not in out and "<b>" not in out

    def test_stage_actions_still_stripped(self):
        out = sanitize_voice_output("（托下巴思考）我想想。", allow_drama3=True)
        assert out == "我想想。"

    def test_default_path_keeps_legacy_behaviour(self):
        assert sanitize_voice_output("<emphasis>真的</emphasis> 我很好。") == "我很好。"
        assert sanitize_voice_output("[像是在哭]我没事。") == "我没事。"

    def test_direction_cap_enforced(self):
        text = "[像是在哭]然後[小聲笑]笑了。"
        out = sanitize_voice_output(text, allow_drama3=True)
        assert out.count("[") == MAX_DIRECTIONS_PER_REPLY
        assert "笑了。" in out          # 超過上限的方向被移除，但後續文字不得消失


class TestStreamingGuard:
    @staticmethod
    def _run(text: str, allow_drama3: bool) -> str:
        san = StreamingVoiceSanitizer(allow_drama3=allow_drama3)
        out = []
        for ch in text:
            out.append(san.feed(ch))
        out.append(san.flush())
        return "".join(out)

    def test_allowed_markup_survives_streaming(self):
        assert self._run("<emphasis>真的</emphasis> 我很好。", True) == "<emphasis>真的</emphasis> 我很好。"

    def test_direction_survives_streaming(self):
        assert self._run("[像是在强忍着眼泪，却努力笑着] 我没事。", True) == "[像是在强忍着眼泪，却努力笑着] 我没事。"

    def test_text_after_over_cap_direction_survives(self):
        """回歸：超過上限的方向不得讓後續文字整段消失（2026-10-24 實測 bug）。"""
        out = self._run("[像是在哭]然後[小聲笑]笑了。", True)
        assert "笑了。" in out
        assert out.count("[") == MAX_DIRECTIONS_PER_REPLY

    def test_text_after_invalid_bracket_survives(self):
        """回歸：不合法方括號走舊行為時，後續文字不得消失。"""
        out = self._run("**粗體** <b>x</b> [1] 條列。", True)
        assert "條列" in out

    def test_tool_tags_still_stripped_streaming(self):
        out = self._run("<write_file path='x'>壞內容</write_file>你好。", True)
        assert out == "你好。"

    def test_legacy_streaming_unchanged(self):
        assert self._run("<emphasis>真的</emphasis> 我很好。", False) == " 我很好。"
        assert self._run("[像是在哭]我没事。", False) == "我没事。"


class TestAudit:
    def test_allowed_markup_is_not_a_violation(self):
        assert contains_markdown_chars("<whisper>真的</whisper> 我很好。") is False
        assert contains_markdown_chars("[像是在哭]我没事。") is False

    def test_real_markdown_is_a_violation(self):
        assert contains_markdown_chars("**粗體**") is True
        assert contains_markdown_chars("[1] 條列") is True


class TestClauseSplitting:
    """切句不得落在表演標記內（否則會送出未閉合的標籤）。"""

    def test_direction_with_comma_is_not_split(self):
        sp = ClauseSplitter()
        clauses = sp.feed("[像是在强忍着眼泪，却努力笑着] 我没事。")
        assert clauses == ["[像是在强忍着眼泪，却努力笑着] 我没事。"]

    def test_paired_tag_with_period_is_not_split(self):
        sp = ClauseSplitter()
        clauses = sp.feed("<whisper>晚安。</whisper> 我先睡了。")
        assert clauses == ["<whisper>晚安。</whisper> 我先睡了。"]

    def test_plain_text_still_splits(self):
        sp = ClauseSplitter()
        clauses = sp.feed("我很好。你呢？") + sp.flush()
        assert clauses == ["我很好。", "你呢？"]

    def test_unclosed_markup_is_fail_safe(self):
        sp = ClauseSplitter()
        clauses = sp.feed("<whisper 我很好。") + sp.flush()
        assert len(clauses) == 1
        assert "我很好。" in clauses[0]


class TestBrainWiring:
    def test_drama3_model_gets_markup_rules_in_prompt(self):
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(["好。"]),
            config={"fish_audio": {"model": "drama-3"}},
        )
        assert brain.drama3_enabled is True
        assert AKANE_DRAMA3_MARKUP_RULES in brain.persona
        assert brain.persona.startswith(AKANE_VOICE_INVARIANTS)

    def test_non_drama3_model_gets_no_markup_rules(self):
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(["好。"]),
            config={"fish_audio": {"model": "s2.1-pro"}},
        )
        assert brain.drama3_enabled is False
        assert AKANE_DRAMA3_MARKUP_RULES not in brain.persona

    def test_no_model_keeps_legacy_prompt(self):
        brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(["好。"]))
        assert brain.drama3_enabled is False
        assert AKANE_DRAMA3_MARKUP_RULES not in brain.persona

    def test_streamed_reply_keeps_markup_for_tts(self):
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(["<whisper>晚安</whisper> 我先睡了。"]),
            config={"fish_audio": {"model": "drama-3"}},
        )
        chunks = "".join(brain.stream_respond("晚安"))
        assert "<whisper>" in chunks
        assert "晚安" in chunks

    def test_streamed_reply_without_drama3_loses_markup(self):
        brain = AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(["<whisper>晚安</whisper> 我先睡了。"]),
            config={"fish_audio": {"model": "s2.1-pro"}},
        )
        chunks = "".join(brain.stream_respond("晚安"))
        assert "<whisper>" not in chunks
