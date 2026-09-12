"""
test_persona_fix1_layer3_sanitize.py — PERSONA-FIX-1 (D1): 純 text 兜底漏掉淨化

稽核 PERSONA-DEFECTS-AUDIT-1 (file:line + 日誌原文定案):
- 管線: LLM raw → _parse_llm_output → Layer 1 (json.loads) / Layer 2 (regex) 全失敗
  → Layer 3 純 text 兜底 (proxy.py 純 text 分支)
- 舊兜底只做 ``` fence 剝除 + _truncate_repetition, 漏掉成功路徑才套用的
  _strip_fake_function_calls 與 _strip_emotion_tags_from_text
  → raw JSON (`{"text": ...`) 原樣當 text + audio_text 廣播並送 TTS
- 日誌鐵證:
  - server_20260730_205447.err L9374-9399 (2026-07-31 01:43):
    L9374 純 text 兜底 → L9375 text='{"text": "那真昼就继续待着了…'
    → L9389 Gateway broadcast 原樣 → L9395-9399 FishTTS 連 JSON 前綴一起合成
    (text_len 37→49)
  - server_20260730_230718.err L19243 (Yua 同樣漏)
  - server_20260801_205401.err L4701 (Ruka text='[停頓] Bryan…' 標籤漏出)

修法 (PERSONA-FIX-1 D1): Layer 3 兜底先 unwrap JSON 殼 (_unwrap_text_json_shell),
再與成功路徑 (proxy.py 3128-3136 區段) 套同一套淨化:
  text       → _strip_fake_function_calls + _strip_emotion_tags_from_text
  audio_text → _strip_fake_function_calls + 只剝開頭 [tag] (TTS 表演指示保留)

測試策略:
- 完整 JSON `{"text": "早安"}` 會被 Layer 1 (json.loads) 直接 parse, 走成功路徑,
  到不了 Layer 3 → 完整殼用 helper 單元測試, 截斷殼 (production 真實形狀,
  見 L9375 鐵證) 走端到端 _parse_llm_output 且斷言 _parse_failed=True 證明真的踩 Layer 3
"""
import sys
import unittest
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')  # PowerShell cp950 不能編碼中文
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy
from src.llm.proxy import (
    _EMOTION_TAG_LINE_PREFIX,
    _parse_llm_output,
    _strip_emotion_tags_from_text,
    _strip_fake_function_calls,
    _unwrap_text_json_shell,
)

AGENT = "mahiru"


class TestLayer3JsonShellUnwrap(unittest.TestCase):
    """D1: JSON 殼 unwrap helper 單元測試 (完整 JSON 殼到不了 Layer 3, 直接測 helper)"""

    def test_unwrap_complete_json_shell(self):
        """驗收 1: `{"text": "早安"}` → 早安 (無 {"text: 前綴、無 "} 尾)"""
        result = _unwrap_text_json_shell('{"text": "早安"}')
        self.assertEqual(result, "早安")
        self.assertNotIn('{"text"', result)
        self.assertNotIn('"}', result)

    def test_unwrap_complete_json_with_trailing_whitespace(self):
        """完整 JSON 殼 + 尾隨空白/換行 → 一樣 unwrap"""
        result = _unwrap_text_json_shell('{"text": "早安"}  \n')
        self.assertEqual(result, "早安")

    def test_unwrap_truncated_json_shell(self):
        """半殘 JSON 殼 (缺結尾引號/花括號, Layer 3 真實輸入形狀) → 剝前綴"""
        result = _unwrap_text_json_shell('{"text": "早安')
        self.assertEqual(result, "早安")

    def test_unwrap_truncated_log_fixture(self):
        """日誌鐵證 L9375 的輸入形狀 `{"text": "那真昼就继续待着了…` → 剝前綴"""
        result = _unwrap_text_json_shell('{"text": "那真昼就继续待着了…')
        self.assertEqual(result, "那真昼就继续待着了…")

    def test_unwrap_plain_text_untouched(self):
        """驗收 4 (回歸保護): 純文字不被 unwrap 破壞"""
        plain = "早安。今天天氣真好呢。一起走走吧。"
        self.assertEqual(_unwrap_text_json_shell(plain), plain)

    def test_unwrap_json_without_text_key_untouched(self):
        """dict 但沒有 text 欄位 → 不是目標殼, 原樣回傳 (照工單: 只取 text 鍵)"""
        raw = '{"audio_text": "早安", "emotion": "calm"}'
        self.assertEqual(_unwrap_text_json_shell(raw), raw)


class TestLayer3EndToEnd(unittest.TestCase):
    """D1: _parse_llm_output 端到端 — 輸入確實走到 Layer 3 兜底再被淨化"""

    def test_truncated_json_shell_reaches_layer3_and_cleans(self):
        """驗收 1 (Layer 3 版): `{"text": "早安` → _parse_failed=True (證明走 Layer 3),
        text 與 audio_text 都是 早安, 無 {"text: 前綴、無 "} 尾"""
        result = _parse_llm_output('{"text": "早安', AGENT)
        self.assertTrue(result.get("_parse_failed"), "截斷 JSON 殼應該走 Layer 3 兜底")
        self.assertEqual(result["text"], "早安")
        self.assertEqual(result["audio_text"], "早安")
        self.assertNotIn('{"text"', result["text"])
        self.assertNotIn('"}', result["text"])
        self.assertNotIn('{"text"', result["audio_text"])
        self.assertNotIn('"}', result["audio_text"])

    def test_truncated_log_fixture_end_to_end(self):
        """日誌鐵證 L9375 輸入形狀端到端: text/audio_text 都是 那真昼就继续待着了…"""
        result = _parse_llm_output('{"text": "那真昼就继续待着了…', AGENT)
        self.assertTrue(result.get("_parse_failed"), "截斷 JSON 殼應該走 Layer 3 兜底")
        self.assertEqual(result["text"], "那真昼就继续待着了…")
        self.assertEqual(result["audio_text"], "那真昼就继续待着了…")

    def test_emotion_tag_stripped(self):
        """驗收 2: `[teasing_care] 早安` → 標籤被剝除 (比照成功路徑)"""
        result = _parse_llm_output("[teasing_care] 早安", AGENT)
        self.assertTrue(result.get("_parse_failed"), "純 tag 文字沒有 JSON, 應該走 Layer 3")
        self.assertEqual(result["text"], "早安")
        self.assertEqual(result["audio_text"], "早安")

    def test_multi_tag_success_path_parity(self):
        """成功路徑 sanitizer 的 parity (照抄 3128-3136): _EMOTION_TAG_LINE_PREFIX 是
        MULTILINE ^ 錨點, 只剝每行**開頭**的 tag, 所以 `[calm] [teasing_care] 早安`
        同行只剝第一個 → text 與 audio_text 都是 `[teasing_care] 早安` (與成功路徑相同)"""
        result = _parse_llm_output("[calm] [teasing_care] 早安", AGENT)
        self.assertTrue(result.get("_parse_failed"))
        # 直接用成功路徑同一組 helper 算出期望值 (字面 parity)
        expected_text = _strip_emotion_tags_from_text(
            _strip_fake_function_calls("[calm] [teasing_care] 早安")
        )
        expected_audio = _EMOTION_TAG_LINE_PREFIX.sub(
            "", _strip_fake_function_calls("[calm] [teasing_care] 早安"), count=1
        ).lstrip()
        self.assertEqual(result["text"], expected_text)
        self.assertEqual(result["audio_text"], expected_audio)

    def test_fenced_json_shell(self):
        """驗收 3 (Layer 3 版): ```json fence + 截斷 JSON 殼 → 早安
        (完整 fence JSON 會被 Layer 2 strategy 2 吃掉, 用截斷變體確保踩 Layer 3)"""
        result = _parse_llm_output('```json\n{"text": "早安\n```', AGENT)
        self.assertTrue(result.get("_parse_failed"), "fence 內截斷 JSON 應該走 Layer 3")
        self.assertEqual(result["text"], "早安")
        self.assertEqual(result["audio_text"], "早安")

    def test_fenced_complete_json_layer2_not_layer3(self):
        """設計邊界 (documentation): 完整 fence JSON 會被 Layer 2 strategy 2
        (```json 區塊) parse 走成功路徑, 到不了 Layer 3 → 完整殼 unwrap 由
        helper 單元測試覆蓋; 這裡斷言偶發行為不變: Layer 2 成功路徑下
        LLM 沒給 audio_text 欄位 → audio_text 保持空 (三欄位不互 fallback)"""
        result = _parse_llm_output('```json\n{"text": "早安"}\n```', AGENT)
        self.assertEqual(result["text"], "早安")
        self.assertEqual(result["audio_text"], "")

    def test_plain_text_untouched(self):
        """驗收 4 (回歸保護): 純文字走 Layer 3 不被破壞"""
        plain = "早安。今天天氣真好呢。一起走走吧。"
        result = _parse_llm_output(plain, AGENT)
        self.assertTrue(result.get("_parse_failed"), "純文字沒有 JSON, 應該走 Layer 3")
        self.assertEqual(result["text"], plain)
        self.assertEqual(result["audio_text"], plain)

    def test_fake_function_call_stripped(self):
        """D1 配套: Layer 3 兜底也該清偽函式 (與成功路徑一致)"""
        result = _parse_llm_output("[teasing_care] 早安 :People.Sleep", AGENT)
        self.assertTrue(result.get("_parse_failed"))
        self.assertEqual(result["text"], "早安")
        self.assertNotIn(":People", result["text"])


class TestD42MisleadingRetryLogRemoved(unittest.TestCase):
    """D4②: 誤導性死碼 log 已修正 (來源層級保護, 防 re-introduce)"""

    def test_source_no_longer_claims_json_retry(self):
        """proxy.py 不再含 'retry with JSON enforcement' 假 retry 字串"""
        content = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "retry with JSON enforcement",
            content,
            "D4②: 誤導性 log 字串應已移除 (此處僅做 JP rollback, 無第二次 LLM 呼叫)",
        )

    def test_source_notes_no_second_llm_call(self):
        """D4②: log 應誠實標明無第二次 LLM 呼叫"""
        content = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertIn(
            "無第二次 LLM 呼叫",
            content,
            "D4②: log 應誠實描述實際行為 (僅 JP rollback, 不重打 LLM)",
        )

    def test_dead_audio_text_clear_removed(self):
        """D4②: 舊死碼 `audio_text = ""` (假 retry 觸發) 已移除"""
        content = Path(proxy.__file__).read_text(encoding="utf-8")
        self.assertNotIn("強致 retry 路徑觸發", content, "舊死碼註解應已移除")


if __name__ == "__main__":
    unittest.main()