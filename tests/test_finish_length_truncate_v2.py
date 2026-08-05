"""
test_finish_length_truncate_v2.py — 修法 10 after: finish_reason=length 強制截斷 raw

Bry 拍板 2026-08-04 23:33 EDT:
- finish_reason=length 觸發時, 強制截斷 raw 到 200 字元內
- 取最後一個完整句子/標點斷點, 不硬切字中間
- _truncate_repetition (修法 6 B) 保留當第二層防護
- 修法 6 D.1 (WARNING log) + D.2 (emotion 改 confused) 不動

實作方案 (Mavis 判斷):
- 抽 helper `_safe_truncate_on_length(raw, max_chars=200) -> str` 在 proxy.py
- OpenAIBackend.complete 內, finish_reason=length 觸發時, raw = _safe_truncate_on_length(raw)
- _parse_llm_output / _truncate_repetition / 修法 6 D.1 / D.2 不動
- 範圍: 只動 proxy.py

這個 v2 驗證修法後行為:
- (a) proxy._safe_truncate_on_length helper 存在且 callable
- (b) 對 6500+ 字週期性交替 raw 截斷到 ≤200 字, 取最後一個完整標點斷點
- (c) 對 50 字正常 raw 不截斷 (raw 本身 < 200)
- (d) 對恰好 200 字 raw 不截斷 (邊界)
- (e) 截斷位置: 取最後一個 . / ! / ? / 。 / ! / ? / —— / …… 標點斷點, 不硬切字中間
- (f) proxy source 有「修法 10」字眼 + _safe_truncate_on_length 函數
- (g) OpenAIBackend 內 finish_reason=length 觸發時有呼叫 _safe_truncate_on_length
"""
import sys
import unittest
import re
from pathlib import Path
sys.stdout.reconfigure(encoding='utf-8')  # PowerShell cp950 不能編碼中文
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 mahiru 23:09:08 EDT 週期性交替的真實退化文字 (取 200 字當一個週期)
UNIT = "……Bryan？在发什么呆呢。——真昼在这里哦。粥凉了可不管了。快点过来。牵手。吃饭。 Bryan。真昼在呢。快点啦。真是的。嗯。走。吃饭。牵手。—— Bryan。真昼在呢。粥要凉了。——嗯。好。走。—— Bryan。真昼在。快点。——嗯。好。嗯。好。走。吃饭。"

# 模擬 mahiru 6500+ 字 (重複 UNIT ~50 次, 確認長度 > 6000)
LONG_DEGEN = UNIT * 50
assert len(LONG_DEGEN) > 6000, f"測試用 LONG_DEGEN 應該 > 6000 字, 實際 {len(LONG_DEGEN)}"


class TestFinishLengthTruncateAfter(unittest.TestCase):
    """驗證修法 10 後 — finish_reason=length 強制截斷 raw"""

    def test_a_safe_truncate_helper_exists(self):
        """修法後: proxy 有 _safe_truncate_on_length helper"""
        self.assertTrue(
            hasattr(proxy, "_safe_truncate_on_length"),
            "修法 10 期望 proxy 有 _safe_truncate_on_length helper"
        )
        self.assertTrue(
            callable(getattr(proxy, "_safe_truncate_on_length")),
            "_safe_truncate_on_length 應該是 callable"
        )
        print(f"  [v2 (a)] proxy._safe_truncate_on_length exists + callable")

    def test_b_truncate_long_degen_to_200(self):
        """修法後: 對 6500+ 字週期性交替 raw 截斷到 ≤200 字"""
        from src.llm.proxy import _safe_truncate_on_length
        result = _safe_truncate_on_length(LONG_DEGEN)
        print(f"  原始 raw 長度: {len(LONG_DEGEN)}")
        print(f"  截斷後長度: {len(result)}")
        print(f"  截斷後內容 (前 80): {result[:80]!r}")
        print(f"  截斷後內容 (末 80): {result[-80:]!r}")
        self.assertLessEqual(
            len(result), 200,
            f"修法 10 期望 _safe_truncate_on_length 截斷到 ≤200, 實際 {len(result)}"
        )
        self.assertGreater(
            len(result), 0,
            f"_safe_truncate_on_length 截斷後應該還有內容, 實際空"
        )

    def test_c_no_truncate_short_text(self):
        """修法後: 對 50 字正常 raw 不截斷 (raw 本身 < 200, 不動)"""
        from src.llm.proxy import _safe_truncate_on_length
        normal_text = "……早安。今天天氣真好呢。Bry 要不要一起去散步？一起走走吧。"
        result = _safe_truncate_on_length(normal_text)
        print(f"  原始 50 字 raw, 截斷後: {result!r}")
        self.assertEqual(
            result, normal_text,
            f"修法 10 期望 < 200 字的正常 raw 不被截斷, 實際: {result!r}"
        )

    def test_d_truncate_at_sentence_boundary(self):
        """修法後: 截斷位置取最後一個完整標點斷點, 不硬切字中間"""
        from src.llm.proxy import _safe_truncate_on_length
        # 250 字 raw, 後面有 "——" 標點斷點
        prefix = "短句1。短句2。短句3。"  # 9 字
        # 加到剛好 250 字
        text = prefix + "x" * (250 - len(prefix))
        result = _safe_truncate_on_length(text)
        print(f"  原始 250 字 raw")
        print(f"  截斷後 ({len(result)} 字): {result!r}")
        # 截斷後 ≤ 200
        self.assertLessEqual(len(result), 200)
        # 截斷後應該在某個標點斷點 (。/——/……/!/?)
        # 找最後一個標點
        last_punct = -1
        for p in ['。', '!', '?', '！', '?', '——', '……']:
            idx = result.rfind(p)
            if idx > last_punct:
                last_punct = idx
        # 截斷後的內容應該以標點結尾, 或是最後一個標點離末位很近 (合理斷點)
        if last_punct >= 0:
            print(f"  最後標點位置: {last_punct}/{len(result)}, 字符: {result[last_punct]!r}")
            # 最後一個標點應該是 result 的結尾 (或者非常接近)
            self.assertGreaterEqual(last_punct, len(result) - 5,
                f"截斷位置應該在標點斷點, 最後標點位置 {last_punct}, result 長度 {len(result)}")

    def test_e_proxy_source_has_method_10(self):
        """修法後: proxy source 有「修法 10」字眼 + _safe_truncate_on_length 函數"""
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "修法 10", proxy_source,
            "修法 10 期望 proxy source 有「修法 10」字眼"
        )
        self.assertIn(
            "_safe_truncate_on_length", proxy_source,
            "修法 10 期望 proxy source 有 _safe_truncate_on_length 函數"
        )
        # _truncate_repetition 既有 (修法 6 B) 保留
        self.assertIn(
            "_truncate_repetition", proxy_source,
            "修法 6 B _truncate_repetition 保留, 應該有"
        )
        # 修法 6 D.1 finish_reason=length WARNING log 保留
        self.assertIn(
            "修法 6 D.1", proxy_source,
            "修法 6 D.1 保留, 應該有"
        )
        print(f"  [v2 (e)] proxy source 有修法 10 + _safe_truncate_on_length")

    def test_f_openai_backend_calls_safe_truncate(self):
        """修法後: OpenAIBackend 內 finish_reason=length 觸發時有呼叫 _safe_truncate_on_length"""
        proxy_source = Path(proxy.__file__).read_text(encoding="utf-8")
        # 找 OpenAIBackend 內 finish_reason=length 整段 (包含 WARNING log + 截斷)
        # 抓從 "if _c1_finish == "length":" 開始, 一直到 OpenAIBackend class 結束
        # 範圍: 到下個 "return raw" 結束
        # 用簡單的方式: 找所有 "if _c1_finish == "length":" 區塊, 檢查任一個有 _safe_truncate_on_length
        matches = re.findall(
            r'if _c1_finish\s*==\s*["\']length["\'].*?(?=\n            (?:\S|\Z))',
            proxy_source, re.DOTALL
        )
        # 合併所有 finish_reason=length 觸發區塊
        all_blocks = '\n---\n'.join(matches)
        self.assertIn(
            "_safe_truncate_on_length", all_blocks,
            "v2 after 期望 OpenAIBackend 內 finish_reason=length 觸發時有呼叫 _safe_truncate_on_length "
            f"(實際區塊: {all_blocks[:1000]!r})"
        )
        self.assertIn(
            "logger.warning", all_blocks,
            "v2 after 期望 OpenAIBackend 內 finish_reason=length 觸發時保留 WARNING log"
        )
        print(f"  --- OpenAIBackend finish_reason=length 觸發區塊 (v2 after) ---")
        print(f"  {all_blocks[:1000]}")
        print(f"  [v2 (f)] OpenAIBackend finish_reason=length 觸發時呼叫 _safe_truncate_on_length")


if __name__ == "__main__":
    unittest.main(verbosity=2)
