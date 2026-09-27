"""tests/clients/test_voice_brain_actions.py
驗證 VC-AVATAR-03：後端 Voice Brain 動作語意解析與字典映射。
"""
from clients.voice_companion.akane_voice_brain import (
    ACTION_LEXICON,
    AkaneVoiceBrain,
    StreamingVoiceSanitizer,
    map_action_keyword,
    sanitize_voice_output,
)


def test_action_lexicon_keys():
    """驗證動作字典包含標準英文 Action Keys。"""
    assert map_action_keyword("微笑") == "happy"
    assert map_action_keyword("笑") == "happy"
    assert map_action_keyword("開心") == "happy"
    assert map_action_keyword("輕笑") == "happy"
    assert map_action_keyword("點頭") == "nod"
    assert map_action_keyword("嗯") == "nod"
    assert map_action_keyword("贊同") == "nod"
    assert map_action_keyword("理解") == "nod"
    assert map_action_keyword("臉紅") == "blush"
    assert map_action_keyword("害羞") == "blush"
    assert map_action_keyword("移開視線") == "blush"
    assert map_action_keyword("低頭") == "blush"
    assert map_action_keyword("歪頭") == "tilt_head"
    assert map_action_keyword("疑惑") == "tilt_head"
    assert map_action_keyword("好奇") == "tilt_head"
    assert map_action_keyword("思考") == "thinking"
    assert map_action_keyword("想想") == "thinking"
    assert map_action_keyword("沉思") == "thinking"
    assert map_action_keyword("嘟嘴") == "pout"
    assert map_action_keyword("鼓頰") == "pout"
    assert map_action_keyword("生氣") == "pout"
    assert map_action_keyword("冷淡") == "cold"
    assert map_action_keyword("嘆氣") == "cold"
    assert map_action_keyword("抱胸") == "cold"
    assert map_action_keyword("未知動作測試") is None


def test_streaming_sanitizer_action_extraction():
    """驗證 StreamingVoiceSanitizer 邊過濾括號邊提取動作。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = ["你好啊", "（微笑）", "，今天", "過得好嗎？"]
    cleaned_parts = [sanitizer.feed(t) for t in tokens]
    cleaned_parts.append(sanitizer.flush())
    cleaned_text = "".join(cleaned_parts)

    assert cleaned_text == "你好啊，今天過得好嗎？"
    assert sanitizer.pop_extracted_actions() == ["happy"]
    assert sanitizer.pop_extracted_actions() == []


def test_streaming_sanitizer_multiple_and_ascii_brackets():
    """驗證多動作與半形括號提取。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = ["(點頭)我明白了，", "這件事確實值得(思考)。"]
    cleaned_parts = [sanitizer.feed(t) for t in tokens]
    cleaned_parts.append(sanitizer.flush())
    cleaned_text = "".join(cleaned_parts)

    assert cleaned_text == "我明白了，這件事確實值得。"
    assert sanitizer.pop_extracted_actions() == ["nod", "thinking"]


def test_voice_brain_stream_respond_actions():
    """驗證 AkaneVoiceBrain.stream_respond 能正確保留 last_extracted_actions。"""
    tokens = ["（輕輕點頭）", "Bryan，你回來了。"]
    brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(tokens))
    result = "".join(brain.stream_respond("我回來了"))

    assert result == "Bryan，你回來了。"
    assert brain.last_extracted_actions == ["nod"]
