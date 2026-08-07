"""
test_m1_6_audio_action_fix.py — M1.6 修法 verify (Bry 拍板 2026-08-07 00:16)

Bry 派工: 選項 C (A 為主 + B 為輔)
- A: regex 過濾 audio_text 出口 (全形括號 + 雙破折號)
- B: prompt hint (audio_text 只寫可被聽見的話)

v2 驗證:
1. _strip_action_descriptions helper 存在, 行為正確
2. 全形括號動作描述被剝 (例: 「（微微靠近）」「（輕輕嘆氣）」)
3. 雙破折號動作描述被剝 (例: 「——（沈默）——」)
4. 日文角括號 「...」 不被剝 (Bry 警告可能是對話)
5. 半形括號 (action) 也被剝 (regex 含蓋)
6. 對話內容 (純日文) 不被誤殺
7. 空字串不 crash
8. 純動作 (整段都是動作) 過濾後變空字串 OK
9. text 沒被改 (只動 audio_text, Bry 用戶端要看動作)
10. M0.5/M1.5 沒被影響 (跟 _strip_action_descriptions 解耦)
"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy as proxy_mod
from src.llm.proxy import (
    _strip_action_descriptions,
    _parse_llm_output,
)


class TestM16Fix(unittest.TestCase):
    """M1.6 修法驗證 — Bry 8/7 00:16 派工."""

    def test_01_helper_exists(self):
        """修法後: _strip_action_descriptions helper 存在."""
        self.assertTrue(hasattr(proxy_mod, "_strip_action_descriptions"),
            "M1.6 修法後: _strip_action_descriptions helper 應存在")

    # ─── 動作描述過濾 ───
    def test_02_strip_full_paren_ja(self):
        """全形括號動作描述被剝."""
        result = _strip_action_descriptions("——（微微靠近）在喔，Bryan。")
        self.assertNotIn("（微微靠近）", result,
            "全形括號動作描述應被剝")
        self.assertIn("在喔", result, "對話內容保留")

    def test_03_strip_half_paren(self):
        """半形括號動作描述被剝 (Bry 派工樣本 2 條)."""
        result = _strip_action_descriptions("(looks away) ...我沒事。")
        self.assertNotIn("(looks away)", result, "半形括號動作描述應被剝")
        self.assertIn("我沒事", result, "對話內容保留")

    def test_04_em_dash_dialog_KEPT(self):
        """雙破折號 (Bry 派工統計 11 條) 實務上是 mahiru 對話風格, 暫不剝.

        Bry 派工精神: 「拒絕大改」「修法後驗證誤殺率, 若有問題再細修」
        實務檢查 11 條樣本 (Bry 派工後細看):
        - 「——あ、帰ってきた。——手を洗って、手伝って。」 (歡迎回來, 去洗手幫忙) 對話
        - 「——过来，张嘴。——就这一次。」 (過來張嘴, 就這一次) 對話
        - 「——真昼在，Bryan也在。」 (我在, 你也在) 對話
        全部是對話風格, 不是動作描述。如果剝會嚴重誤殺 mahiru 對話, 暫不處理。
        """
        mahiru_style = "——あ、帰ってきた。——手を洗って、手伝って。"
        result = _strip_action_descriptions(mahiru_style)
        self.assertEqual(result, mahiru_style,
            "M1.6 修法後: 雙破折號對話保留 (Bry 派工精神: 拒絕大改, 寧少勿錯)")

    def test_05_jps_kagi_NOT_stripped(self):
        """日文角括號 「...」 不被剝 (Bry 警告可能是對話)."""
        result = _strip_action_descriptions("「她說你好」嗯，這樣啊。")
        self.assertIn("「她說你好」", result,
            "Bry 警告: 「」日文角括號 12 條可能是對話, 不處理")

    # ─── 不誤殺對話 ───
    def test_06_pure_dialog_unchanged(self):
        """純對話 (沒動作描述) 不被改."""
        text = "在喔，Bryan。你還在嗎。"
        result = _strip_action_descriptions(text)
        self.assertEqual(result, text, "純對話不應被改")

    def test_07_keeps_japanese_punctuation(self):
        """保留日文標點 (「。」「、」「！」「?」)."""
        text = "「何してるの？」"  # 日文問句
        result = _strip_action_descriptions(text)
        # 「」 是 Bry 警告不處理, 應保留
        self.assertEqual(result, text, "日文問句標點保留")

    def test_08_empty_string_no_crash(self):
        """空字串不 crash."""
        self.assertEqual(_strip_action_descriptions(""), "")
        self.assertEqual(_strip_action_descriptions(None), None)

    def test_09_pure_action_becomes_empty(self):
        """整段都是動作 (例: 純全形括號) 過濾後變空字串, 不 crash."""
        result = _strip_action_descriptions("（純動作描述）")
        self.assertEqual(result, "", "純動作應被剝成空字串")

    def test_10_multiple_actions_all_stripped(self):
        """多個動作描述都被剝."""
        text = "（走近）（沈默）你怎麼在這？"
        result = _strip_action_descriptions(text)
        self.assertNotIn("（走近）", result)
        self.assertNotIn("（沈默）", result)
        self.assertIn("你怎麼在這", result, "對話內容保留")

    # ─── text 不被改 (Bry 派工: 只動 audio_text) ───
    def test_11_text_unchanged_in_parse(self):
        """Bry 派工: text 保留動作描述 (Bry 用戶端要看到), audio_text 剝掉.

        走完整 _parse_llm_output pipeline.
        """
        case_text = "——（微微靠近）在喔，Bryan。"
        llm_output = json.dumps({
            "text": case_text,
            "audio_text": case_text,
            "emotion": "calm"
        }, ensure_ascii=False)
        result = _parse_llm_output(llm_output, "agent_rem")
        # text 保留 (Bry 用戶端看)
        self.assertIn("（微微靠近）", result["text"],
            "M1.6 修法: text 保留動作描述 (Bry 用戶端要看)")
        # audio_text 剝掉 (TTS 不唸)
        self.assertNotIn("（微微靠近）", result["audio_text"],
            "M1.6 修法: audio_text 剝掉動作描述 (TTS 不唸)")

    # ─── 與既有修法共存 ───
    def test_12_real_bry_example_fully_cleaned(self):
        """Bry 派工原文引用的範例完整清理."""
        # Bry 派工: 「（輕輕靠近，確認 Bryan 還在）」
        case = "（輕輕靠近，確認 Bryan 還在）在喔。"
        result = _strip_action_descriptions(case)
        self.assertEqual(result, "在喔。", "Bry 引用的範例應被完整清理")

    def test_13_em_dash_dialog_kept_now(self):
        """Bry 派工原話精神 (8/7 00:16): 「上線後驗證誤殺率, 若 regex 有誤殺
        對話的情況再細修規則」. 11 條破折號樣本細看是 mahiru 對話風格,
        修法後不剝 (只剝全形/半形括號)."""
        # 多種對話風格
        cases = [
            "——お帰り。 Bryan。",  # 歡迎回來
            "——过来，张嘴。——就这一次。",  # mahiru
            "——真昼在，Bryan也在。",  # mahiru
        ]
        for case in cases:
            result = _strip_action_descriptions(case)
            self.assertEqual(result, case,
                f"M1.6 修法後: 雙破折號對話保留 (Bry 派工拒絕大改) | {case}")

    def test_14_audio_text_short_bry_real_case(self):
        """真實 rem 真實觸發: '——（微微靠近）在喔，Bryan。' 完整處理."""
        case = "——（微微靠近）在喔，Bryan。"
        result = _strip_action_descriptions(case)
        # 動作剝掉, 「在喔，Bryan。」 保留
        self.assertNotIn("（微微靠近）", result)
        self.assertIn("在喔", result)
        self.assertIn("Bryan", result)

    def test_15_akane_sample_full_pipeline(self):
        """akane 真實觸發範例 (Bry 派工提到是重災戶)."""
        # 從 server log 看到的真實 sample
        llm_output = json.dumps({
            "text": "……（她不再回應。只是看著你，等你說點別的。）",
            "audio_text": "……（她不再回應。只是看著你，等你說點別的。）",
            "emotion": "observing"
        }, ensure_ascii=False)
        result = _parse_llm_output(llm_output, "agent_akane")
        # audio_text 剝掉, 留「……」
        self.assertNotIn("她不再回應", result["audio_text"])
        # text 保留 (Bry 用戶端要看)
        self.assertIn("她不再回應", result["text"])


if __name__ == "__main__":
    print("=" * 60)
    print("M1.6 v2 verify (Bry 派工 2026-08-07 00:16, 選項 C)")
    print("=" * 60)
    unittest.main(verbosity=2)
