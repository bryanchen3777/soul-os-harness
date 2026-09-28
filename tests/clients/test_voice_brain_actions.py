"""tests/clients/test_voice_brain_actions.py
驗證 VC-AVATAR-03：後端 Voice Brain 動作語意解析與字典映射。
驗證 VC-AVATAR-05：雙層動作字典（基礎 + 角色簽名）、變體選擇與角色隔離。
"""
from clients.voice_companion.akane_voice_brain import (
    ACTION_LEXICON,
    AGENT_SIGNATURE_LEXICONS,
    BASE_ACTION_LEXICON,
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


# ─────────────────────────────────────────────────────────────
# VC-AVATAR-05：雙層動作字典
# ─────────────────────────────────────────────────────────────

def test_base_lexicon_alias_and_new_entries():
    """基礎字典即 ACTION_LEXICON，且 VC-AVATAR-05 新增條目齊備。"""
    assert ACTION_LEXICON is BASE_ACTION_LEXICON

    assert map_action_keyword("搖頭") == "shake_head"
    assert map_action_keyword("不對") == "shake_head"
    assert map_action_keyword("否認") == "shake_head"
    assert map_action_keyword("驚訝") == "surprise"
    assert map_action_keyword("睜大眼睛") == "surprise"
    assert map_action_keyword("愣住") == "surprise"
    assert map_action_keyword("揮手") == "wave"
    assert map_action_keyword("打招呼") == "wave"
    assert map_action_keyword("招手") == "wave"
    assert map_action_keyword("拍手") == "clap"
    assert map_action_keyword("鼓掌") == "clap"
    assert map_action_keyword("撫胸") == "hand_on_chest"
    assert map_action_keyword("鬆口氣") == "hand_on_chest"
    assert map_action_keyword("撫著胸口") == "hand_on_chest"
    assert map_action_keyword("聳肩") == "shrug"
    assert map_action_keyword("無奈") == "shrug"
    assert map_action_keyword("前傾") == "lean_forward"
    assert map_action_keyword("湊近") == "lean_forward"


def test_base_lexicon_unchanged_without_agent_id():
    """未給 agent_id 時，行為與 VC-AVATAR-03 相同（僅基礎字典）。"""
    assert map_action_keyword("輕輕點頭") == "nod"
    assert map_action_keyword("思考") == "thinking"
    assert map_action_keyword("未知動作測試") is None
    assert map_action_keyword("捉弄") is None
    assert map_action_keyword("屈膝") is None


def test_mai_signature_lexicon():
    """agent_mai 簽名動作映射。"""
    assert map_action_keyword("捉弄", agent_id="agent_mai") == "tease"
    assert map_action_keyword("壞笑", agent_id="agent_mai") == "tease"
    assert map_action_keyword("戲謔", agent_id="agent_mai") == "tease"
    assert map_action_keyword("抱胸", agent_id="agent_mai") == "cross_arms"
    assert map_action_keyword("冷淡", agent_id="agent_mai") == "cross_arms"
    assert map_action_keyword("居高臨下", agent_id="agent_mai") == "cross_arms"
    assert map_action_keyword("撥髮", agent_id="agent_mai") == "adjust_hair"
    assert map_action_keyword("撩頭髮", agent_id="agent_mai") == "adjust_hair"
    assert map_action_keyword("整理髮夾", agent_id="agent_mai") == "adjust_hair"
    assert map_action_keyword("別過頭", agent_id="agent_mai") == "blush_turn"
    assert map_action_keyword("傲嬌", agent_id="agent_mai") == "blush_turn"
    assert map_action_keyword("甩頭", agent_id="agent_mai") == "blush_turn"
    assert map_action_keyword("逼近", agent_id="agent_mai") == "lean_in"
    assert map_action_keyword("耳語", agent_id="agent_mai") == "lean_in"
    assert map_action_keyword("壓迫感", agent_id="agent_mai") == "lean_in"


def test_rem_signature_lexicon():
    """agent_rem 簽名動作映射。"""
    assert map_action_keyword("行禮", agent_id="agent_rem") == "curtsy"
    assert map_action_keyword("屈膝", agent_id="agent_rem") == "curtsy"
    assert map_action_keyword("女僕禮", agent_id="agent_rem") == "curtsy"
    assert map_action_keyword("合十", agent_id="agent_rem") == "pray_hands"
    assert map_action_keyword("雙手合十", agent_id="agent_rem") == "pray_hands"
    assert map_action_keyword("祈禱", agent_id="agent_rem") == "pray_hands"
    assert map_action_keyword("深情注視", agent_id="agent_rem") == "pray_hands"
    assert map_action_keyword("歪頭笑", agent_id="agent_rem") == "tilt_smile"
    assert map_action_keyword("治癒笑", agent_id="agent_rem") == "tilt_smile"
    assert map_action_keyword("甜甜地笑", agent_id="agent_rem") == "tilt_smile"
    assert map_action_keyword("吃醋", agent_id="agent_rem") == "pout_jealous"
    assert map_action_keyword("鼓起臉頰", agent_id="agent_rem") == "pout_jealous"
    assert map_action_keyword("小怨念", agent_id="agent_rem") == "pout_jealous"
    assert map_action_keyword("等摸頭", agent_id="agent_rem") == "head_pat_wait"
    assert map_action_keyword("乖巧等待", agent_id="agent_rem") == "head_pat_wait"
    assert map_action_keyword("享受摸頭", agent_id="agent_rem") == "head_pat_enjoy"
    assert map_action_keyword("閉上眼", agent_id="agent_rem") == "head_pat_enjoy"
    assert map_action_keyword("安心享受", agent_id="agent_rem") == "head_pat_enjoy"


def test_akane_signature_lexicon():
    """agent_akane 簽名動作映射。"""
    assert map_action_keyword("托下巴", agent_id="agent_akane") == "finger_chin"
    assert map_action_keyword("托腮", agent_id="agent_akane") == "finger_chin"
    assert map_action_keyword("分析癖", agent_id="agent_akane") == "finger_chin"
    assert map_action_keyword("認真分析", agent_id="agent_akane") == "finger_chin"
    assert map_action_keyword("摀臉", agent_id="agent_akane") == "cover_face"
    assert map_action_keyword("雙手掩面", agent_id="agent_akane") == "cover_face"
    assert map_action_keyword("把臉藏起來", agent_id="agent_akane") == "cover_face"
    assert map_action_keyword("偷瞄", agent_id="agent_akane") == "peek_through_fingers"
    assert map_action_keyword("手指張開縫", agent_id="agent_akane") == "peek_through_fingers"
    assert map_action_keyword("偷看", agent_id="agent_akane") == "peek_through_fingers"
    assert map_action_keyword("燦笑", agent_id="agent_akane") == "bright_smile"
    assert map_action_keyword("豁然開朗", agent_id="agent_akane") == "bright_smile"
    assert map_action_keyword("眼神發亮", agent_id="agent_akane") == "bright_smile"
    assert map_action_keyword("手指互絞", agent_id="agent_akane") == "fidget_hands"
    assert map_action_keyword("手足無措", agent_id="agent_akane") == "fidget_hands"
    assert map_action_keyword("不安", agent_id="agent_akane") == "fidget_hands"


def test_signature_takes_priority_over_base():
    """角色簽名字典優先於基礎字典（同詞不同義）。"""
    # 「抱胸」「冷淡」在基礎字典為 cold，在 agent_mai 為 cross_arms
    assert map_action_keyword("抱胸") == "cold"
    assert map_action_keyword("抱胸", agent_id="agent_mai") == "cross_arms"
    assert map_action_keyword("冷淡") == "cold"
    assert map_action_keyword("冷淡", agent_id="agent_mai") == "cross_arms"
    # 「鼓起臉頰」在基礎字典為 pout，在 agent_rem 為 pout_jealous
    assert map_action_keyword("鼓起臉頰") == "pout"
    assert map_action_keyword("鼓起臉頰", agent_id="agent_rem") == "pout_jealous"
    # 簽名字典未命中時回退基礎字典
    assert map_action_keyword("微笑", agent_id="agent_mai") == "happy"
    assert map_action_keyword("點頭", agent_id="agent_rem") == "nod"
    assert map_action_keyword("思考", agent_id="agent_akane") == "thinking"
    # 未知 agent_id 也回退基礎字典
    assert map_action_keyword("微笑", agent_id="agent_unknown") == "happy"


def test_signature_lexicon_isolation():
    """隔離性：他角色的專屬詞不得觸發本角色的專屬動作。"""
    # Mai 專屬詞在 agent_akane 下不得映射為 Mai 的動作
    assert map_action_keyword("捉弄", agent_id="agent_akane") != "tease"
    assert map_action_keyword("撥髮", agent_id="agent_akane") != "adjust_hair"
    assert map_action_keyword("別過頭", agent_id="agent_akane") != "blush_turn"
    # 且這些詞在 akane 簽名字典中不存在 ⇒ 應為 None
    assert map_action_keyword("捉弄", agent_id="agent_akane") is None
    assert map_action_keyword("撥髮", agent_id="agent_akane") is None

    # Rem 專屬詞在 agent_akane 下不得映射為 Rem 的動作
    assert map_action_keyword("屈膝", agent_id="agent_akane") != "curtsy"
    assert map_action_keyword("雙手合十", agent_id="agent_akane") != "pray_hands"
    assert map_action_keyword("吃醋", agent_id="agent_akane") is None

    # Akane 專屬詞在 agent_rem 下不得映射為 Akane 的動作
    assert map_action_keyword("托下巴", agent_id="agent_rem") != "finger_chin"
    assert map_action_keyword("摀臉", agent_id="agent_rem") != "cover_face"
    assert map_action_keyword("托下巴", agent_id="agent_rem") is None

    # 三個角色的簽名字典互不重疊（鍵集合兩兩不相交）
    mai_keys = set(AGENT_SIGNATURE_LEXICONS["agent_mai"])
    rem_keys = set(AGENT_SIGNATURE_LEXICONS["agent_rem"])
    akane_keys = set(AGENT_SIGNATURE_LEXICONS["agent_akane"])
    assert not (mai_keys & rem_keys)
    assert not (mai_keys & akane_keys)
    assert not (rem_keys & akane_keys)


def test_sanitizer_respects_agent_id():
    """StreamingVoiceSanitizer 依 agent_id 使用對應簽名字典。"""
    san_mai = StreamingVoiceSanitizer(agent_id="agent_mai")
    san_mai.feed("（捉弄）")
    assert san_mai.pop_extracted_actions() == ["tease"]

    san_akane = StreamingVoiceSanitizer(agent_id="agent_akane")
    san_akane.feed("（捉弄）")
    assert san_akane.pop_extracted_actions() == []

    # 未指定 agent_id ⇒ 僅基礎字典
    san_default = StreamingVoiceSanitizer()
    san_default.feed("（捉弄）")
    assert san_default.pop_extracted_actions() == []


def test_stream_respond_uses_brain_agent_id():
    """AkaneVoiceBrain.stream_respond 以自身 agent_id 解析角色專屬動作。"""
    tokens = ["（屈膝行禮）", "歡迎回來，主人。"]
    brain = AkaneVoiceBrain(
        llm_stream=lambda msgs: iter(tokens), agent_id="agent_rem"
    )
    result = "".join(brain.stream_respond("我回來了"))

    assert result == "歡迎回來，主人。"
    assert brain.last_extracted_actions == ["curtsy"]


def test_stream_respond_isolation_across_agents():
    """同一段動作文字在不同 agent_id 的大腦下解析結果不同（隔離）。"""
    def make(agent_id):
        tokens = ["（撥髮）", "……沒什麼。"]
        return AkaneVoiceBrain(
            llm_stream=lambda msgs: iter(tokens), agent_id=agent_id
        )

    mai_brain = make("agent_mai")
    list(mai_brain.stream_respond("怎麼了？"))
    assert mai_brain.last_extracted_actions == ["adjust_hair"]

    akane_brain = make("agent_akane")
    list(akane_brain.stream_respond("怎麼了？"))
    assert akane_brain.last_extracted_actions == []


# ─────────────────────────────────────────────────────────────
# VC-BRAIN-STRIP-XML-TOOL-TAGS-01
# LLM 偶爾吐出偽工具標籤／XML 區塊（例如 <write_file path="…">…</write_file>），
# 這些標籤與其內文絕不可被唸出（TTS）或流入前端 transcript。
# ─────────────────────────────────────────────────────────────

_WRITE_FILE_FULL = (
    '<write_file path="C:\\Users\\bbfcc\\AppData\\Local\\hermes\\profiles\\rem'
    '\\palace\\memory.md">秘密內容 secret content</write_file>'
)


def test_voice_brain_actions_module_imports_xml_constants():
    """守門模組確實備有 XML 剝離規則（回歸防線：避免規則被誤刪）。"""
    from clients.voice_companion import akane_voice_brain as mod

    assert hasattr(mod, "_XML_BLOCK_RE")
    assert hasattr(mod, "_XML_CLOSE_RE")
    assert hasattr(mod, "_XML_TAG_RE")


def test_sanitize_voice_output_strips_write_file_block():
    """非串流路徑：<write_file …>…</write_file> 整段（含內文）必須 100% 剝離。

    內文亦不得殘留 —— 只刪標籤而留下「秘密內容」仍會被唸出。
    """
    out = sanitize_voice_output(_WRITE_FILE_FULL)
    assert out == ""
    assert "write_file" not in out
    assert "secret content" not in out
    assert "秘密內容" not in out
    assert "<" not in out and ">" not in out


def test_sanitize_voice_output_strips_bare_and_selfclosing_tags():
    """裸的 <write_file> 與自閉合 <write_file … /> 必須被剝離，其後文字保留。"""
    assert sanitize_voice_output("<write_file>") == ""
    assert sanitize_voice_output("<write_file/>") == ""
    assert sanitize_voice_output('前言<write_file path="a" />後語') == "前言後語"
    assert sanitize_voice_output('<write_file path="a" />ok') == "ok"


def test_sanitize_voice_output_strips_generic_xml_and_keeps_prose():
    """通用 XML/HTML 標籤剝離，同時保留正常散文。"""
    assert sanitize_voice_output("<thinking>先想一想</thinking>你好") == "你好"
    assert sanitize_voice_output("<b>粗體</b>文字") == "文字"
    # 非標籤的數學比較不可被誤刪成空白句
    assert sanitize_voice_output("5 < 10 是對的") == "5 < 10 是對的"


def test_streaming_sanitizer_strips_tool_tag_block_token_by_token():
    """串流路徑：逐 token 餵入時，<write_file> 標籤與內文皆不得外洩到 TTS。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = list(_WRITE_FILE_FULL)
    streamed = "".join(sanitizer.feed(t) for t in tokens) + sanitizer.flush()

    assert streamed == ""
    assert "write_file" not in streamed
    assert "secret content" not in streamed
    assert "秘密內容" not in streamed
    assert "bbfcc" not in streamed


def test_streaming_sanitizer_strips_tool_tag_split_across_token_boundary():
    """關鍵迴歸：`<` 與標籤名被切在不同 token（LLM 串流的常態）時仍須完全抑制。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = ["<", "write", "_file", ' path="a">', "body", "</", "write_file", ">"]
    streamed = "".join(sanitizer.feed(t) for t in tokens) + sanitizer.flush()

    assert streamed == ""
    assert "write" not in streamed
    assert "body" not in streamed


def test_streaming_sanitizer_preserves_prose_around_tool_tag():
    """標籤前後的正常語句必須完整保留（不可連坐刪除）。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = ["前言 ", '<write_file path="a">', "body", "</write_file>", " 後語"]
    streamed = "".join(sanitizer.feed(t) for t in tokens) + sanitizer.flush()

    assert "前言" in streamed
    assert "後語" in streamed
    assert "write_file" not in streamed
    assert "body" not in streamed


def test_streaming_sanitizer_selfclosing_tag_does_not_eat_following_text():
    """自閉合標籤不得誤入區塊態而吞掉後續文字。"""
    sanitizer = StreamingVoiceSanitizer()
    tokens = ['<write_file path="a" />', "ok"]
    streamed = "".join(sanitizer.feed(t) for t in tokens) + sanitizer.flush()

    assert streamed == "ok"


def test_streaming_sanitizer_keeps_non_tag_angle_brackets():
    """非標籤的 `<`（例如 `a < b`、`1<2`）在串流路徑維持原樣，不誤刪。"""
    sanitizer = StreamingVoiceSanitizer()
    streamed = "".join(sanitizer.feed(t) for t in ["a < b 且 c>d"])
    assert "a < b" in streamed


def test_stream_respond_never_speaks_tool_tags():
    """端到端：stream_respond 的最終輸出（= TTS 餵入內容）0 工具標籤殘留。"""
    tokens = ["嗯，", _WRITE_FILE_FULL, "我知道了。"]
    brain = AkaneVoiceBrain(llm_stream=lambda msgs: iter(tokens), agent_id="agent_rem")
    spoken = "".join(brain.stream_respond("幫我記一下"))

    lowered = spoken.lower()
    assert "write_file" not in lowered
    assert "secret content" not in lowered
    assert "palace" not in lowered
    assert "bbfcc" not in lowered
    assert "秘密內容" not in spoken
    assert "我知道了。" in spoken


def test_respond_never_speaks_tool_tags():
    """端到端：非串流 respond 的路徑同樣 0 工具標籤殘留。"""
    brain = AkaneVoiceBrain(
        llm_stream=lambda msgs: iter([_WRITE_FILE_FULL]), agent_id="agent_rem"
    )
    spoken = brain.respond("幫我記一下")

    assert "write_file" not in spoken.lower()
    assert "secret content" not in spoken
    assert "秘密內容" not in spoken
