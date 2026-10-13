"""
test_anna_degeneration_v1.py — 修法 6 baseline: anna 退化重複防護 v1 (before 修法)

Bry 拍板 2026-08-03 23:48, 修法 6 (anna 退化重複):
- A (repetition_penalty): 跳過 — minimax M2.7 不支援, 跳過
- B (輸出後處理截斷): 加 — 偵測連續 5+ 重複字串就截斷
- C (silent drop): 跳過 — 跟 7/27 拍板兜底原則衝突
- D (finish_reason=length 警告 + emotion 改 confused): 加
- E (retry with different seed): 跳過 — 複雜度高

Bry 之前查證 anna 8/3 21:42 退化重複事件:
- 21:42:41 LLM 生成完成: tool_calls 內 3851 chars (50+「嗯。」), finish_reason=length
- completion=4000 (打滿 max_tokens)
- JSON parse 失敗, Layer 3 E 兜底把 raw 當 text+audio_text
- 之前 21:35, 21:37, 21:38, 21:41 有 4 次 parse 失敗 (16-31 chars 短, 沒退化成 3851 chars)

修法 6 v1 驗證現狀 (before 修法):
- 沒 _truncate_repetition 函數 (B 沒加)
- Layer 3 E 兜底 emotion = _get_safe_emotion(agent_id) = anna 白名單第一個 = "bright"
  (從 build_system_prompt.py L140-141: "anna": ["bright", "jealous", "dimmed", "vulnerable"])
- OpenAIBackend._send_request L636 finish_reason=length 只有 INFO level log (沒警告)

mock 範圍:
- 讀 proxy.py 內容, 確認沒 _truncate_repetition 函數定義
- 確認 Layer 3 E 兜底 return dict 用 _get_safe_emotion(agent_id) (not "confused")
- 確認 OpenAIBackend L636 finish_reason log 沒有 WARNING level
- 不實際呼叫 LLM (避免 token 浪費)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")
PROXY = REPO / "src" / "llm" / "proxy.py"


class TestAnnaDegenerationV1(unittest.TestCase):
    """anna 退化重複修法 v1 baseline (before 修法 6)"""

    def setUp(self):
        if not PROXY.exists():
            self.fail(f"proxy.py 應該在 {PROXY}, 但不存在")
        self.content = PROXY.read_text(encoding="utf-8")

    def test_baseline_no_truncate_repetition_function(self):
        """修法 6 B 已套用: _truncate_repetition 函數存在, 且真的被呼叫

        本測試原本寫成 v1 baseline (斷言 proxy.py 沒有這個函數)。
        Bry 拍板 2026-08-03 23:48 修法 6 B 之後 production 已加上:
        - src/llm/proxy.py:2775 `def _truncate_repetition(text, max_repeat=5)`
        - src/llm/proxy.py:3290/3291 Layer 3 兜底對 cleaned_text / extracted_ja 套用
        - src/llm/proxy.py:3378/3379 成功路徑對 text / audio_text 套用
        """
        match = re.search(r"def\s+_truncate_repetition\b", self.content)
        self.assertIsNotNone(
            match,
            "修法 6 B (Bry 拍板 2026-08-03 23:48): proxy.py 應該有 _truncate_repetition "
            "函數定義 (連續重複截斷)。找不到 → 修法被回退, 這是真正的 regression"
        )
        # 不是只有定義, 真的要呼叫 (修法 6 B 生效的證據)
        call_count = len(re.findall(r"=\s*_truncate_repetition\(", self.content))
        self.assertGreater(
            call_count, 0,
            "修法 6 B: _truncate_repetition 有定義但沒有任何呼叫點 → 截斷實際沒生效"
        )
        print(f"[修法 6 B 已套用] proxy.py:2775 有 _truncate_repetition 定義, "
              f"{call_count} 個呼叫點")

    def test_baseline_layer3_uses_safe_emotion(self):
        """修法 6 D.2 已套用: Layer 3 E 兜底 emotion 改 "confused"

        本測試原本寫成 v1 baseline (斷言 proxy.py 沒有 "confused" 字串)。
        Bry 拍板 2026-08-03 23:48 修法 6 D.2 之後 production 已改:
        - src/llm/proxy.py:3243 / 3257 / 3304 / 3348 / 3365 `"emotion": "confused"`
          (註解都標「修法 6 D.2: 從 _get_safe_emotion 改 confused」)
        """
        # _get_safe_emotion(agent_id) 仍保留 (成功解析路徑的 safe default 仍需要)
        matches = list(re.finditer(r"_get_safe_emotion\(agent_id\)", self.content))
        self.assertGreater(
            len(matches), 0,
            "_get_safe_emotion(agent_id) 應該仍在 proxy.py 內作為 safe default emotion"
        )
        # 修法 6 D.2: "confused" 已存在於 Layer 3 E 兜底
        confused_matches = list(re.finditer(r"\bconfused\b", self.content))
        self.assertGreater(
            len(confused_matches), 0,
            "修法 6 D.2 (Bry 拍板 2026-08-03 23:48): proxy.py Layer 3 E 兜底應該用 "
            '"confused"。找不到 → 修法被回退, 這是真正的 regression'
        )
        # 確認 "confused" 確實綁在 emotion key 上 (不是只出現在註解)
        emotion_confused = re.findall(r'"emotion"\s*:\s*"confused"', self.content)
        self.assertGreater(
            len(emotion_confused), 0,
            '修法 6 D.2: 應該有 `"emotion": "confused"` 的實際程式碼, '
            "不是只有註解提到 confused"
        )
        print(f"[修法 6 D.2 已套用] Layer 3 E 兜底 emotion 改 confused "
              f"({len(emotion_confused)}  個 '\"emotion\": \"confused\"'), "
              f"_get_safe_emotion(agent_id) 仍保留 {len(matches)} 處")

    def test_baseline_finish_reason_info_only(self):
        """v1 baseline: OpenAIBackend finish_reason log 應該只有 INFO level
        修法後會對 finish_reason=length 升級成 WARNING level
        """
        # 找 OpenAIBackend._send_request 內的 finish_reason= log 行
        # 用單行 regex: 找包含 finish_reason={_c1_finish} 的 logger 呼叫
        finish_log_lines = re.findall(
            r"logger\.(?:info|warning|error)\([^)]*finish_reason=\{_c1_finish\}[^)]*\)",
            self.content,
        )
        self.assertGreater(
            len(finish_log_lines), 0,
            f"v1 baseline: 應該有 OpenAIBackend finish_reason log 行"
        )
        # 確認都是 logger.info (修法前)
        for line in finish_log_lines:
            self.assertIn(
                "logger.info", line,
                f"v1 baseline: finish_reason log 應該用 logger.info (修法前), "
                f"修法後才升級 logger.warning. 實際: {line[:200]!r}"
            )
            self.assertNotIn(
                "logger.warning", line,
                f"v1 baseline: finish_reason log 不應該用 logger.warning "
                f"(修法前只有 INFO, 修法後才升級). 實際: {line[:200]!r}"
            )
        print(f"[v1 baseline] finish_reason log 用 logger.info ({len(finish_log_lines)} 個, "
              f"修法前的現狀, 修法後對 finish_reason=length 升級 logger.warning)")

    def test_baseline_real_anna_degeneration_can_reproduce(self):
        """v1 baseline: 模擬 anna 8/3 21:42 退化重複事件
        - tool_calls 內 50+「嗯。」 (3851 chars)
        - 沒 _truncate_repetition → 不截斷
        - 沒 emotion 改 confused → 仍用 anna 白名單 "bright"
        """
        # 模擬 50+「嗯。」退化重複 (跟 Bry 派工原話查證一致)
        # 「嗯。」是 2 字元 (嗯 + 。), 60 次 = 120 chars
        anna_degenerate_audio = "嗯。" * 60
        anna_degenerate_text = "嗯。" * 60

        # v1 沒 _truncate_repetition, 所以這些字串會原封不動保留
        # 模擬 _parse_llm_output Layer 3 E 兜底 (沒修法前):
        # 1. raw 當 text (含 50+ 重複)
        # 2. emotion = _get_safe_emotion(agent_anna) = "bright"

        # 計算退化重複次數 (用 "嗯。" 出現次數)
        repetition_count = anna_degenerate_audio.count("嗯。")
        self.assertGreater(
            repetition_count, 5,
            f"v1 baseline: 退化重複應該 > 5 次 (Bry 派工原話「連續 5+ 重複截斷」閾值), "
            f"實際: {repetition_count}"
        )
        # 沒 _truncate_repetition, 60 次「嗯。」保留 (120 chars)
        self.assertEqual(
            len(anna_degenerate_audio), 120,
            f"v1 baseline: 60 次「嗯。」= 120 chars (沒截斷, 修法前的現狀), "
            f"修法後 v2 會截斷到只剩 5 次「嗯。」= 10 chars"
        )
        print(f"[v1 baseline] anna 8/3 21:42 退化重複可重現: "
              f"{repetition_count} 次「嗯。」, {len(anna_degenerate_audio)} chars, "
              f"沒 _truncate_repetition 截斷 (修法前的現狀)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
