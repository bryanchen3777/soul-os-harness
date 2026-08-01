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
        """API 成功 → 回傳日文, 寫快取"""
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

        assert result == "こんにちは"
        # 確認寫快取
        assert _translate_cache.get("你好") == "こんにちは"

    @pytest.mark.asyncio
    async def test_cache_hit_no_api_call(self):
        """快取命中 → 不打 API"""
        _translate_cache["你好"] = "キャッシュ済み"

        with patch.dict(os.environ, {"MINIMAX_API_KEY": "fake-key"}):
            with patch("src.llm.translate.httpx.AsyncClient") as mock_client_cls:
                mock_client = AsyncMock()
                mock_client.post = AsyncMock()
                mock_client_cls.return_value = mock_client

                result = await translate_to_japanese("你好")

        assert result == "キャッシュ済み"
        mock_client.post.assert_not_called()

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
        """LLM 翻譯含 <think> → 正確剝掉, 取日文"""
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

        assert result == "こんにちは"


if __name__ == "__main__":
    # 直接跑也支援
    pytest.main([__file__, "-v"])
