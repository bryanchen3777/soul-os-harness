# -*- coding: utf-8 -*-
"""
REM-LEGACY-CLEANSE (2026-10-12) — sanitize_legacy_runaway_turn 回歸測試

背景見 logs/SOUL-MEM-001-P0-PROMPT-BASELINE-AUDIT.md §7：
2026-09-03 遺留在 data/conversations/bryan_agent_mahiru_private.json 的 3 條
失控 assistant 回合（各約 6,900 字，佔該檔 97.5%）。本測試確保讀取端淨化：

1. 對正常訊息**零行為變化**（不 strip、不改字元）。
2. 能識別並截斷「未閉合 JSON 殼 + 異常長度」的遺留汙染。
3. 截斷結果對齊修法 10 的 200 字元規格。
4. **不影響**合法完整的 JSON 殼（那是 PERSONA-FIX-1 D1 的職責，不是本函式的）。
"""
from src.text_guard import (
    sanitize_legacy_runaway_turn,
    _LEGACY_RUNAWAY_TRIGGER_CHARS,
    _LEGACY_RUNAWAY_KEEP_CHARS,
)

RUNAWAY_PREFIX = '{"text": "'
# 真實案例的形狀：單行、0 換行、引號奇數、結尾為中文字、無閉合引號
RUNAWAY = RUNAWAY_PREFIX + "……Bryan？在吗？粥要凉了。" + "——嗯。好" * 500 + "嗯"


class TestZeroBehaviourChange:
    """未觸發時必須原樣回傳 —— 這是最重要的不變條件。"""

    def test_empty(self):
        assert sanitize_legacy_runaway_turn("") == ""

    def test_none(self):
        assert sanitize_legacy_runaway_turn(None) is None

    def test_normal_short_message_untouched(self):
        s = "嗯，在的。"
        assert sanitize_legacy_runaway_turn(s) == s

    def test_leading_trailing_whitespace_preserved(self):
        """未觸發時不得 strip —— 保持與歷史值完全一致。"""
        s = "  在的。 \n"
        assert sanitize_legacy_runaway_turn(s) == s

    def test_just_under_threshold_untouched(self):
        s = "在" * (_LEGACY_RUNAWAY_TRIGGER_CHARS - 1)
        assert sanitize_legacy_runaway_turn(s) == s


class TestValidJsonShellNotOurJob:
    """合法完整的 JSON 殼不屬本函式職責（PERSONA-FIX-1 D1 負責），不得誤傷。"""

    def test_valid_short_shell_untouched(self):
        s = '{"text": "你好"}'
        assert sanitize_legacy_runaway_turn(s) == s


class TestRunawaySanitised:
    def test_real_case_is_sanitised(self):
        out = sanitize_legacy_runaway_turn(RUNAWAY)
        assert out != RUNAWAY
        assert len(out) <= _LEGACY_RUNAWAY_KEEP_CHARS

    def test_shell_prefix_removed(self):
        out = sanitize_legacy_runaway_turn(RUNAWAY)
        assert not out.startswith(RUNAWAY_PREFIX)

    def test_pure_length_over_threshold_is_truncated(self):
        """即使沒有 JSON 殼，只要異常長度也要截斷（防其他形態的失控回合）。"""
        s = "嗯" * (_LEGACY_RUNAWAY_TRIGGER_CHARS + 500)
        out = sanitize_legacy_runaway_turn(s)
        assert len(out) <= _LEGACY_RUNAWAY_KEEP_CHARS

    def test_breaks_on_punctuation_not_mid_char(self):
        """截斷應落在完整標點斷點（對齊修法 10 語意）。"""
        s = "句子。" * 1000
        out = sanitize_legacy_runaway_turn(s)
        assert len(out) <= _LEGACY_RUNAWAY_KEEP_CHARS
        assert out[-1] in "。！？——，,.!?"

    def test_truncated_output_is_stable(self):
        """冪等：再跑一次不會再變（避免 prompt 每次組裝都抖動）。"""
        once = sanitize_legacy_runaway_turn(RUNAWAY)
        assert sanitize_legacy_runaway_turn(once) == once

    def test_no_newline_introduced(self):
        out = sanitize_legacy_runaway_turn(RUNAWAY)
        assert "\n" not in out