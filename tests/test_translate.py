"""
tests/test_translate.py
Soul OS — Plan A 翻譯層 unit test (Bry 拍板 2026-07-16)

場景:
1. should_translate: 純中文 → True
2. should_translate: 純日文 → False
3. should_translate: 純英文 → False
4. should_translate: 中日混合 (假名多) → False
5. should_translate: 空字串 / None / 純表情 → False
6. should_translate: 純數字 / 純標點 → False
7. translate_to_japanese: API 成功 → 回傳日文, 寫快取
8. translate_to_japanese: API timeout → fallback 原文
9. translate_to_japanese: API 5xx → fallback 原文
10. translate_to_japanese: 結果不像日文 → fallback 原文
11. translate_to_japanese: 結果太短 (< 30%) → fallback 原文
12. translate_to_japanese: 快取命中 → 不打 API
13. translate_to_japanese: 沒設 MINIMAX_API_KEY → fallback 原文
14. _extract_japanese_segment: 剝 <think>, 抓第一行, 移除尾部 () 中文注解
"""
from __future__ import annotations

import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# 把專案根加到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.llm.translate import (
    should_translate,
    translate_to_japanese,
    _extract_japanese_segment,
    _translate_cache,
)


# ════════════════════════════════════════════════════════════════════
# 🔴 SOUL-TECH-DEBT-001（stale-test calibration, class (a)）— 本檔現況
# ════════════════════════════════════════════════════════════════════
# JP 翻譯 pipeline 已被 Bry 拍板**整條砍掉**（2026-07-22 20:59, rollback）。
# 見 src/llm/translate.py 檔頭 docstring 第 1-21 行的逐字說明：
#   - `translate_to_japanese()` body 改成 `return text`（translate.py:145），
#     CN → CN 直通，**不打 LLM**。
#   - `translate_to_chinese()` 改成 `return None`（translate.py:317）。
#   - `api_key / model / timeout` 參數保留只為簽名相容，body 完全不使用。
# 所以本類裡所有「API 成功 / 快取命中 / think block 抽取」三支舊斷言
# 描述的是**已被刪除的行為**，本批次校準為釘住「rollback 是徹底的」這個
# 現行契約，而不是把斷言反轉成「應該翻譯」。
#
# 仍保留活著的純函式（無副作用，本檔上半段照測）：
#   should_translate()            translate.py:57
#   _extract_japanese_segment()   translate.py:95  ← 仍存在，但 pipeline 不再呼叫它
#
# ⚠️ 網路安全（已實測確認，非假設）:
# 本檔所有 async 測試都用 `patch("src.llm.translate.httpx.AsyncClient")` 把
# 唯一的出網點封掉，且現在額外斷言「連 client 都不被建構」。
# 這同時是一道 tripwire：若日後有人把 translate.py 的 client 換成別的符號
# （例如 `httpx.Client`），這個 patch target 會靜默失效、真實請求會逃出去，
# 而下面 `mock_client_cls.assert_not_called()` / `post.assert_not_called()`
# 會立刻變紅擋下來。


# ── should_translate 場景 ────────────────────────────────

class TestShouldTranslate:
    def test_chinese_only(self):
        """純中文 → True (要翻)"""
        assert should_translate("你好嗎") is True
        assert should_translate("今天天氣真好") is True
        assert should_translate("我在等你喔") is True

    def test_japanese_only(self):
        """純日文 (有假名) → False (不用翻)"""
        assert should_translate("こんにちは") is False
        assert should_translate("お元気ですか") is False
        assert should_translate("ありがとう") is False

    def test_english_only(self):
        """純英文 → False (不用翻)"""
        assert should_translate("hello") is False
        assert should_translate("how are you") is False

    def test_chinese_with_emotion(self):
        """中文 + 表情 → True (要翻, 表情保留)"""
        assert should_translate("你好呀~") is True
        assert should_translate("哈哈 早安！") is True

    def test_japanese_kanji_with_kana(self):
        """日文漢字詞 + 假名 (假名 >= 30%) → False (已經是日文)"""
        # 「天気」是日文漢字, 假名 ratio 應該 < 1, 但 CJK 漢字 CJK RE 抓到
        # 「今日は良い天気です」整段假名多
        assert should_translate("今日は良い天気です") is False
        assert should_translate("おやすみなさい") is False

    def test_empty_or_none(self):
        """空字串 / None → False"""
        assert should_translate("") is False
        assert should_translate("   ") is False
        assert should_translate(None) is False  # type: ignore

    def test_pure_punctuation(self):
        """純標點 / 表情符號 → False"""
        assert should_translate("……") is False
        assert should_translate("!!!") is False
        assert should_translate("🤔💕") is False

    def test_pure_numbers(self):
        """純數字 → False"""
        assert should_translate("12345") is False
        assert should_translate("2026") is False

    def test_short_mixed(self):
        """短中日混合 (e.g. 「中文 + 假名」) → 看假名比例"""
        # 「中文 こんにちは」: CJK 漢字 2 (中, 文), 假名 5 (こ,ん,に,ち,は)
        # kana_ratio = 5/7 ≈ 71% >= 30% → False (已經是日文)
        assert should_translate("中文 こんにちは") is False

    def test_chinese_dominant_mixed(self):
        """中文為主 + 一點日文假名 → 假名比例 < 30%, 仍要翻"""
        # 「你好 こ」: CJK 漢字 2 (你, 好), 假名 1 (こ)
        # kana_ratio = 1/3 ≈ 33% → False (剛好 >= 30% 邊界)
        # 為了確保 30% threshold 真的有效, 用更極端的 case
        # 「你好呀 こ」: CJK 漢字 3, 假名 1 → 25% < 30% → True
        assert should_translate("你好呀 こ") is True

    def test_chinese_with_english_word(self):
        """中文 + 英文 (Japanese 拼字) → 英文不算假名, kana_ratio = 0%, 要翻"""
        # 「中文 Japanese」: CJK 漢字 2 (中, 文), 假名 0 (Japanese 是英文字母)
        # kana_ratio = 0/2 = 0% < 30% → True (要翻, 因為大部分是中文)
        assert should_translate("中文 Japanese") is True


# ── _extract_japanese_segment 場景 ──────────────────────

class TestExtractJapaneseSegment:
    def test_simple_japanese(self):
        assert _extract_japanese_segment("こんにちは") == "こんにちは"

    def test_with_think_block(self):
        """剝掉 <think>...</think>"""
        raw = "<think>怎麼翻譯...</think>\nこんにちは"
        assert _extract_japanese_segment(raw) == "こんにちは"

    def test_first_line_only(self):
        """只取第一行"""
        raw = "こんにちは\n(你好)"
        assert _extract_japanese_segment(raw) == "こんにちは"

    def test_remove_trailing_paren_chinese(self):
        """移除尾部 () 中文注解"""
        assert _extract_japanese_segment("おはよう（早安）") == "おはよう"
        assert _extract_japanese_segment("こんにちは (你好)") == "こんにちは"

    def test_no_kana_returns_none(self):
        """沒有假名 → None (fallback 原文)"""
        assert _extract_japanese_segment("Hello world") is None
        assert _extract_japanese_segment("") is None
        assert _extract_japanese_segment(None) is None  # type: ignore

    def test_kanji_only_with_kanji_chars(self):
        """純漢字無假名 → None (無法確認是中文還是日文)"""
        # CJK 漢字 CJK RE 會抓, 但 KANA RE 抓不到 → 返回 None
        # 這個 case 在 should_translate 階段就會被判 True (純漢字無假名)
        # 但 _extract_japanese_segment 拿來驗證 LLM 翻譯結果時, 純漢字也算通過
        # 等等, 純漢字可能是中文也可能是日文, 沒假名就無法確認
        # 我們要求: 必須有假名才算日文 (防止 LLM 偷懶吐純漢字視同日文)
        assert _extract_japanese_segment("你好") is None
        assert _extract_japanese_segment("天気") is None


# ── translate_to_japanese 場景 ─────────────────────────

class TestTranslateToJapanese:
    def setup_method(self):
        """每個 case 前清快取"""
        _translate_cache.clear()

    @pytest.mark.asyncio
    async def test_successful_translation(self):
        """[已校準] JP rollback 後: 即使 API key 有效且 client 可用, 也**原樣直通**。

        舊版此斷言是「API 成功 → 回傳日文, 寫快取」。該行為已被 Bry 於
        2026-07-22 20:59 砍掉（translate.py:145 `return text`）。
        現行為 = 零 LLM 呼叫的 passthrough。

        強化點（原斷言只驗回傳值）:
          1. 具體現行值: 回傳值必須**逐字等於原文**「你好」。
          2. 邊界: 不只 `post` 沒被呼叫，連 `httpx.AsyncClient` 都**不該被建構**
             （`mock_client_cls.assert_not_called()`）—— 這比
             post-not-called 更強，且能擋下「patch target 漂移」這個靜默失效。
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "こんにちは"}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好")

        # 現行契約: 原樣直通（不翻譯）
        assert result == "你好", (
            f"JP rollback 後應原樣直通, 實際 {result!r} "
            f"（若此處變日文, 代表 translate.py 的 rollback 被回退了）"
        )
        # 邊界: 連 HTTP client 都不該被建構
        mock_client_cls.assert_not_called()
        mock_client.post.assert_not_called()
        # 快取不該被寫入（rollback 後沒有「翻譯結果」可快取）
        assert "你好" not in _translate_cache, (
            f"rollback 後不該寫翻譯快取, 實際內容: {_translate_cache!r}"
        )

    @pytest.mark.asyncio
    async def test_cache_hit_no_api_call(self):
        """[已校準] JP rollback 後: 快取**完全不再被讀取**, 一律原樣直通。

        舊版此斷言是「快取命中 → 回傳快取值」。rollback 之後
        `translate_to_japanese` 根本不碰 `_translate_cache`
        （translate.py:128-145 整個 body 只有 `return text`）,
        快取模組是整條 pipeline 的化石（仍 import、仍存在、但無呼叫者）。

        強化點: 把快取**預先塞滿**再斷言回傳值仍是原文 —— 這同時是
        「快取對現行行為 0 影響」的邊界證明；若日後有人把快取接回來,
        本斷言會立刻變紅。
        """
        _translate_cache["你好"] = "キャッシュ済み"
        try:
            with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
                with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.post = AsyncMock()
                    mock_client_cls.return_value = mock_client

                    result = await translate_to_japanese("你好")

            # 現行契約: 快取被忽略, 回原文
            assert result == "你好", (
                f"rollback 後不該讀翻譯快取, 應原樣直通; 實際 {result!r}"
            )
            mock_client_cls.assert_not_called()
            mock_client.post.assert_not_called()
        finally:
            _translate_cache.clear()

    @pytest.mark.asyncio
    async def test_skip_japanese_text(self):
        """純日文 → 直接回原文, 不打 API"""
        with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await translate_to_japanese("こんにちは")

        assert result == "こんにちは"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_skip_english_text(self):
        """純英文 → 直接回原文, 不打 API"""
        with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post = AsyncMock()
            mock_client_cls.return_value = mock_client

            result = await translate_to_japanese("hello world")

        assert result == "hello world"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_timeout_fallback(self):
        """API timeout → fallback 原文"""
        import httpx
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(side_effect=asyncio.TimeoutError())
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好嗎")

        assert result == "你好嗎"

    @pytest.mark.asyncio
    async def test_api_5xx_fallback(self):
        """API 5xx → fallback 原文"""
        import httpx
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock()
        )

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("今天天氣真好")

        assert result == "今天天氣真好"

    @pytest.mark.asyncio
    async def test_no_japanese_in_result_fallback(self):
        """LLM 結果沒日文 (偷懶吐中文) → fallback 原文"""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "你好"}}],  # 純中文, 沒假名
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好嗎")

        assert result == "你好嗎"

    @pytest.mark.asyncio
    async def test_too_short_result_fallback(self):
        """翻譯結果太短 (< 原文 30%) → fallback 原文"""
        # 原文 10 chars, 翻譯 2 chars → < 30%
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "嗯"}}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好嗎,今天天氣真好啊")

        assert result == "你好嗎,今天天氣真好啊"

    @pytest.mark.asyncio
    async def test_no_api_key_fallback(self):
        """沒設 MINIMAX_API_KEY → fallback 原文"""
        # 確保 env 沒有 key
        env_without_key = {k: v for k, v in os.environ.items() if k != "MINIMAX_API_KEY"}
        with patch.dict(os.environ, env_without_key, clear=True):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock()
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好")

        assert result == "你好"
        mock_client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_think_block_extracts_correctly(self):
        """[已校準] JP rollback 後: think block 抽取在**管線層**不可能發生,
        但底層 helper `_extract_japanese_segment` 仍完好 —— 兩側都釘死。

        舊版此斷言假設 LLM 會回含 <think> 的內容再由管線剝掉。rollback 後
        沒有 LLM 呼叫，所以「抽取」這件事在 `translate_to_japanese` 層已無從發生。
        但 helper 本身（translate.py:95）仍是活程式碼，且 pipeline 不再呼叫它。

        強化點（把原本只驗一條的字串變成雙向契約）:
          1. 管線層: 即使 backend 備好含 think block 的回應, 結果仍是原文直通,
             且 client 從未被建構 → 證明抽取迴路真的沒接回去。
          2. helper 層: `_extract_japanese_segment` 仍正確剝 think block 並
             抽出日文 → 沒有把仍活著的底層能力一併誤刪。
        """
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {
                "content": "<think>用戶在問候,翻譯成日文こんにちは</think>\nこんにちは"
            }}],
        }
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock(return_value=mock_response)
                mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                mock_client.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好")

        # (1) 管線層: 不會去讀那個含 think block 的回應
        assert result == "你好", (
            f"rollback 後應原樣直通, 不該回 LLM 的 think block 內容; 實際 {result!r}"
        )
        mock_client_cls.assert_not_called()
        mock_client.post.assert_not_called()

        # (2) helper 層: 剝 think block 的能力仍在（pipeline 不呼叫它, 但函式本身沒壞）
        assert _extract_japanese_segment(
            "<think>怎麼翻譯...</think>\nこんにちは"
        ) == "こんにちは"
        assert _extract_japanese_segment(
            "<think>用戶在問候,翻譯成日文こんにちは</think>\nこんにちは"
        ) == "こんにちは"


if __name__ == "__main__":
    # 直接跑也支援
    pytest.main([__file__, "-v"])
