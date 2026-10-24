"""tests/clients/test_vc_lang_tags.py — drama-3 語言標籤層（VC-2026-10-24）。

策略依據見 clients/voice_companion/lang_tags.py 頂端的實測表（108 次 TTS）。
本檔測的是**策略與結構**，不測模型行為（那是線上實測的範疇）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from clients.voice_companion.fish_tts_streamer import FishTTSStreamer
from clients.voice_companion.lang_tags import (
    apply_language_tags,
    supports_language_tags,
)

_TAG_RE = re.compile(r"</?([a-z_]+)>")


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})

        class _R:
            status_code = 200
            content = b"\x00\x03MP3FAKE"

        return _R()


class TestSupportsLanguageTags:
    def test_only_drama3(self):
        assert supports_language_tags("drama-3") is True
        assert supports_language_tags("s2.1-pro") is False
        assert supports_language_tags("s2.1-pro-free") is False
        assert supports_language_tags("") is False


class TestApplyLanguageTags:
    def test_pure_chinese_is_wrapped_in_base_tag(self):
        out = apply_language_tags("今天天氣真好，我們等一下一起去散步好不好？")
        assert out == "<chinese>今天天氣真好，我們等一下一起去散步好不好？</chinese>"

    def test_english_runs_alternate_with_base(self):
        out = apply_language_tags("我先把 API 的 latency 調到 80 毫秒。")
        assert out == (
            "<chinese>我先把 </chinese>"
            "<english>API</english>"
            "<chinese> 的 </chinese>"
            "<english>latency</english>"
            "<chinese> 調到 80 毫秒。</chinese>"
        )

    def test_bare_numbers_stay_in_base_language(self):
        out = apply_language_tags("我等了 80 秒。")
        assert "<english>" not in out
        assert "80" in out

    def test_japanese_kana_gets_japanese_tag(self):
        out = apply_language_tags("記得跟我說 おやすみなさい 哦。")
        assert "<japanese>おやすみなさい</japanese>" in out
        assert out.count("<japanese>") == out.count("</japanese>") == 1

    def test_hangul_gets_korean_tag(self):
        out = apply_language_tags("안녕하세요 你好")
        assert "<korean>안녕하세요</korean>" in out

    def test_existing_drama3_markup_is_left_untouched(self):
        # 已有方向指示／配對標籤 ⇒ 不疊加語言標籤（避免未定義的巢狀）
        raw = "<chinese>我很好</chinese>"
        assert apply_language_tags(raw) == raw
        raw2 = "[shaky breath] 我沒事。"
        assert apply_language_tags(raw2) == raw2
        raw3 = "<emphasis>真的</emphasis> 我很好。"
        assert apply_language_tags(raw3) == raw3

    def test_empty_and_blank_unchanged(self):
        assert apply_language_tags("") == ""
        assert apply_language_tags("   ") == "   "

    def test_tags_are_balanced_and_nested_free(self):
        samples = [
            "今天天氣真好，我們等一下一起去散步好不好？",
            "我先把 API 的 latency 調到 80 毫秒。",
            "記得跟我說 おやすみなさい 哦。",
            "好，那我先 deepseek-v4.1-flash 跑一下。",
        ]
        for s in samples:
            out = apply_language_tags(s)
            opens = _TAG_RE.findall(out)
            stack = []
            for m in re.finditer(r"<(/?)([a-z_]+)>", out):
                if m.group(1) == "":
                    stack.append(m.group(2))
                else:
                    assert stack, f"標籤不對稱：{out!r}"
                    assert stack.pop() == m.group(2), f"巢狀錯配：{out!r}"
            assert not stack, f"標籤未閉合：{out!r}"
            assert len(opens) % 2 == 0


class TestStreamerIntegration:
    def test_drama3_request_carries_language_tags(self):
        session = FakeSession()
        st = FishTTSStreamer(api_key="k", voice_id="v", model="drama-3", session=session)
        st.synthesize("我先把 API 調好。")
        text = session.calls[0]["json"]["text"]
        assert "<chinese>" in text and "<english>API</english>" in text

    def test_unsupported_model_gets_no_tags(self):
        session = FakeSession()
        st = FishTTSStreamer(api_key="k", voice_id="v", model="s2.1-pro", session=session)
        st.synthesize("我先把 API 調好。")
        text = session.calls[0]["json"]["text"]
        assert text == "我先把 API 調好。"
        assert "<" not in text

    def test_empty_model_gets_no_tags(self):
        session = FakeSession()
        st = FishTTSStreamer(api_key="k", voice_id="v", session=session)
        st.synthesize("我先把 API 調好。")
        assert session.calls[0]["json"]["text"] == "我先把 API 調好。"
