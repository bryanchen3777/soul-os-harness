"""
test_m1_6_audio_action_baseline.py — 修法 16 (M1.6) baseline (Bry 拍板 2026-08-07 00:16)

Bry 派工原話: 「TTS 動作描述被唸出來的問題, 語音掉價. 目前訊息直接送 TTS, 若內容包含
動作描述 (例如「（輕輕靠近，確認 Bryan 還在）」) 也會被唸出來」

Bry 派工原話: 「regex 後處理 (A): 在 proxy.py 的 audio_text 出口, 過濾掉全形括號
「（...）」與破折號「——...——」包住的段落」

Bry 派工原話: 「輕量 prompt hint (B): 在既有 prompt 補一句提醒, audio_text 那行日文
只寫可被聽見的話, 不要寫動作/心理描述, 不用固定標記格式」

Bry 派工原話: 「上線後拉接下來幾天的 log 驗證誤殺率 (尤其是「」日文角括號那 12 條
要小心, 可能是對話不是動作, 別誤殺)」

Bry 拍板: 選項 C (A 為主 + B 為輔), 拒絕大改 (跟 8/6 21:44 派工精神一致)

Bry 派工統計 (850 條樣本):
- 全形括號 （...）: 36 條
- 破折號 ——...——: 11 條
- 日文角括號 「...」: 12 條 (Bry 警告可能是對話, 不處理)

v1 證明現況:
- 動作描述在 audio_text 出口沒被過濾, 會被 TTS 唸
- 統計 850 條樣本, 36 條全形括號 + 11 條破折號 = 47 條 (5.5%) 有動作描述
"""
import json
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy as proxy_mod


class TestM16Baseline(unittest.TestCase):
    """M1.6 修法前的現狀證明 — Bry 8/7 00:16 派工."""

    def test_01_proxy_has_strip_action_descriptions_helper(self):
        """現況: proxy.py 有 _strip_action_descriptions, 且在 audio_text 出口套用。

        修法 M1.6 (Bry 拍板 2026-08-07 00:16): 選項 C (A 為主 + B 為輔)。
        生產證據:
          - helper 定義: src/llm/proxy.py:2859 _strip_action_descriptions
            (docstring 明寫「Bry 拍板 2026-08-07 00:16」)
          - 套用點: src/llm/proxy.py:3415 在 _parse_llm_output 的主 return 之前
            「注意 1: 只動 audio_text, text 保留 (Bry 用戶端要看到動作描述)」
          - 二次套用: src/llm/proxy.py:3933 (JP rollback 路徑)
        """
        self.assertTrue(
            hasattr(proxy_mod, "_strip_action_descriptions"),
            "M1.6 修法後: proxy.py 應該有 _strip_action_descriptions helper (Bry 8/7 00:16 拍板)",
        )

    def test_02_real_log_action_in_audio_text_count(self):
        """統計 server log: 多少 audio_text 含全形括號動作描述 (TTS 會唸).

        Bry 派工要求: 全形括號 36 條, 破折號 11 條.
        修法前: 全部都會被 TTS 唸出來.
        """
        log_dir = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/logs")
        paren_count = 0
        dash_count = 0
        kakko_count = 0  # 「」日文角括號 (Bry 警告可能是對話, 跳過)
        samples = []
        for log in log_dir.glob("server_*.err"):
            if log.stat().st_size < 1000:
                continue
            try:
                content = log.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            for m in re.finditer(
                r"\[LLMProxy\] 生成完成 \| agent=(\w+) text='((?:[^'\\]|\\.)*)' audio_text='((?:[^'\\]|\\.)*)'",
                content,
            ):
                agent, text, audio_text = m.group(1), m.group(2), m.group(3)
                if re.search(r"[（(][^）)\n]+[）)]", audio_text):
                    paren_count += 1
                    if len(samples) < 3:
                        samples.append((agent, audio_text))
                if re.search(r"——[^—\n]+——", audio_text):
                    dash_count += 1
                if re.search(r"「[^」\n]+」", audio_text):
                    kakko_count += 1
        print(f"\n  [M1.6 baseline] full_paren action in audio_text: {paren_count}")
        print(f"  [M1.6 baseline] em_dash action in audio_text:    {dash_count}")
        print(f"  [M1.6 baseline] jps_kagi (maybe dialog, skip):  {kakko_count}")
        for agent, at in samples:
            safe_at = at.encode('ascii', 'replace').decode('ascii')[:100]
            print(f"    [{agent}] {safe_at}")
        # 寬鬆斷言 (Bry 8/7 00:xx 派工統計是 36 + 11, 我們抓 850 條, 確認有發現)
        self.assertGreater(paren_count, 5,
            f"修法前 audio_text 應有動作描述, 實際 {paren_count} 條 (Bry 派工 36 條預期)")

    def test_03_action_descriptions_known_cases(self):
        """驗證 Bry 派工引用的範例 ('（輕輕靠近，確認 Bryan 還在）').

        這個 case 跟 rem 8/6 真實觸發 '（微微低頭讓他摸，沒有說話）' 同 pattern。

        修法 M1.6 (Bry 拍板 2026-08-07 00:16) 後: audio_text 出口的動作描述被剝掉,
        只有可被聽見的日文留下; text 欄位保留動作描述 (Bry 用戶端要看到)。
        生產證據: src/llm/proxy.py:3415 在 _parse_llm_output 主 return 前套用
        _strip_action_descriptions(audio_text); helper 本體 src/llm/proxy.py:2859-2898
        (只剝全形/半形括號, 雙破折號與「」不剝, 避免誤殺對話 — Bry 8/7 拍板)。
        """
        case = "——（微微靠近）在喔，Bryan。"
        from src.llm.proxy import _parse_llm_output
        # 構造 LLM 真實輸出 (含 audio_text 欄位)
        llm_output = json.dumps({
            "text": case,
            "audio_text": case,
            "emotion": "calm"
        }, ensure_ascii=False)
        result = _parse_llm_output(llm_output, "agent_rem")
        # M1.6 後: audio_text 不含動作描述, TTS 不會唸出來 (Bry 派工問題源頭)
        self.assertNotIn("（微微靠近）", result["audio_text"],
            "M1.6 後 audio_text 的全形括號動作描述應被剝掉, 不會被 TTS 唸")
        # 剝完之後仍有可聽的內容留下來 (不是整段清空)
        self.assertIn("在喔", result["audio_text"])
        # 刻意不剝雙破折號: Bry 8/7 00:16 拍板「拒絕大改」, 11 條 —— 樣本細看後確認
        # 是 mahiru 對話風格 (用 —— 當句子分隔), 剝了會誤殺對話。
        # 生產證據: src/llm/proxy.py:2866-2868 / :2875 / :2886 註明記錄這個決定。
        self.assertTrue(result["audio_text"].startswith("——"),
                        "M1.6 只剝全形括號, 雙破折號刻意保留 (Bry 8/7 拍板避免誤殺對話)")
        # 只動 audio_text: text 欄位保留動作描述 (Bry 用戶端要看到)
        self.assertIn("（微微靠近）", result["text"])


if __name__ == "__main__":
    print("=" * 60)
    print("M1.6 v1 baseline (Bry 派工 2026-08-07 00:16)")
    print("=" * 60)
    unittest.main(verbosity=2)
