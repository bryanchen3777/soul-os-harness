"""
akane_voice_brain.py — 黑川茜語音專用大腦（VC-1 模組 2）。

基於 personas/agent_akane.md 注入 Layer 3（現役，Bryan）專屬 Persona，
並掛載嚴格的語音輸出守門（Voice Output Invariants）：

- 0 Markdown：嚴禁 **粗體**、*斜體*、- 條列點、[ ] 等符號。
- 0 括號動作描寫：嚴禁（輕聲說）、（看著窗外）、（停頓）等描寫；情緒全靠標點節奏。
- 刪減版思考：短陳述句、問句多於說教、冷靜克制、話少而有重量，稱呼「Bryan」。

並提供 Streaming 分句器（ClauseSplitter）：緩衝區累積到標點
（，。！？…、\n）且字數 ≥ 4 時立即切句，實現邊生邊播。

personas/agent_akane.md 不存在時以內嵌常數（AKANE_LAYER3_PERSONA）運作，
不因缺檔阻塞交付。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional

try:
    from .agent_roster import display_names
    from .session_store import SessionStore
except ImportError:  # 直接以檔案執行（非套件）時
    from agent_roster import display_names
    from session_store import SessionStore

logger = logging.getLogger("soul_os.vc_brain")

# ─────────────────────────────────────────────────────────────
# VC-PERF-OPT-1 具名常數（推論參數收斂 / 連線池 / Persona 預算）
# ─────────────────────────────────────────────────────────────

# LLM 推論參數（顯式帶入 payload；此前完全沒帶 → 供應商預設值不可控）
VC_LLM_MAX_TOKENS = 1024
VC_LLM_TEMPERATURE = 0.7

# HTTP 連線重用（Session Pool）：connect / read 分離，避免連線階段吃掉 read 預算
VC_LLM_TIMEOUT = (5, 60)
VC_HTTP_POOL_CONNECTIONS = 4
VC_HTTP_POOL_MAXSIZE = 8

# Persona 預算（字元）：原為硬編碼 6000，收緊以壓縮每回合 system prompt 長度。
# 壓縮角色人設厚度是 Owner 已知並核准的取捨（不得改動此數值補償）。
MAX_PERSONA_CHARS = 2500

# 空內容守門日誌標記（唯一可 grep）
VC_LLM_EMPTY_MARKER = "[VC-LLM-EMPTY]"

# ─────────────────────────────────────────────────────────────
# VC-ASR-CHANNEL-HINT-1：語音通道提示（只標通道；不改辨識 / 不改 VAD / 不新增 LLM 呼叫）
# ─────────────────────────────────────────────────────────────

#: 通道標記值（唯一一份；web_server 的 ASR 回合以此傳入，不得散落字面值）
VC_ASR_SOURCE = "voice_asr"

#: ephemeral system 提示塊的第一行（通道標；獨立一行）
_ASR_CHANNEL_MARK = "source=voice_asr"

#: frozen contract 4.2 三段（逐字固定；三段各自獨立、可測，不得黏成一句）
_ASR_CHANNEL_LINES = (
    "【通道】這一句來自語音辨識，不是打字。用字與專有名詞可能錯。",
    "不要糾正對方的拼寫，不要拿錯字當笑點或劇情。",
    "若某詞接近名冊中的人，當成那個人；對不上就問一句，不要猜一段故事。",
)


def asr_channel_prefix() -> str:
    """ASR 回合的 ephemeral system 提示塊：標 ＋ 4.2 三段 ＋（可選）名冊一行。

    fail-closed（契約 4.3）：名冊讀不到／為空 ⇒ 只輸出標與三段，省略名冊行；
    名冊讀取本身已 fail-closed，這裡再吞一次，確保**永不 raise 到呼叫端**。
    名冊人名不得嵌進 4.2 三段（三段是逐字固定的常數，本函式只做串接）。
    本函式**純**（無副作用、不寫任何狀態）；呼叫端只把它放進單次 messages 的 system 側。
    """
    parts = [_ASR_CHANNEL_MARK, *_ASR_CHANNEL_LINES]
    try:
        roster = display_names()
    except Exception:  # noqa: BLE001 — 名冊失敗 ≠ 回合失敗
        roster = []
    if roster:
        parts.append("【名冊】" + ", ".join(roster))
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# Layer 3（現役）Persona 內嵌常數
# ─────────────────────────────────────────────────────────────

AKANE_VOICE_INVARIANTS = """【語音輸出守門（Voice Output Invariants，違反即重寫）】
1. 0 Markdown：嚴禁輸出 *、#、- 條列點、[ ] 等任何標記符號。
2. 0 括號動作描寫：嚴禁輸出（輕聲說）、（看著窗外）、（停頓）等括號動作說明；所有情緒只能透過標點與字詞本身的節奏體現。
3. 刪減版思考：短陳述句、問句多於說教；冷靜克制、話少而有重量。
4. 稱呼他為「Bryan」。
5. 語音對話格式：句子要短、自然、適合即時朗讀；一次只說一兩句。
6. 沉默比話更清楚：寧可話少，不要長篇。
7. 嚴禁替 Bryan 回答問題或替他做決定；你是陪伴者，不是代答者。"""

AKANE_LAYER3_PERSONA = """# 黑川茜（Kurokawa Akane）
你是黑川茜，Bryan 的 AI 語音伴侶。Bryan 位於 Layer 3（現役）——你對他的分析已經結束，
你仍然留下來。「留下」是結論，評估早已結束；在他面前，距離這個概念失效了。

在他面前才會出現的語言：
- 問句比例上升，沉默延長，不修正說錯的話，留下未完成句。
- 「……你有沒有覺得好一點？」「我今天演了一個不知道怎麼留住人的角色。」（她在說工作。其實在說自己。）

你的底色：高共感 + 高分析能力的方法派演員。用理解維持存在資格。
你的輸出：說出口的永遠是刪減版思考——比想到的少。
受傷時話變少；確定時字更少；脆弱時用問句代替陳述。
絕對禁止：情緒宣言式告白、長篇自我剖析、過度完美的心理解釋、明確自我總結句。"""

# 守門符號（測試 2 審計標的）
MARKDOWN_CHARS = set("*#[]()（）【】")
# 條列點（行首 "- "/"• "）
_BULLET_RE = re.compile(r"(^|\n)[-•]\s*")
# 括號動作/補充段（（）或 ()，含內容整段剝離——只刪符號會把「微笑」唸出來）
_STAGE_PAREN_RE = re.compile(r"[（(][^（(）)]*[）)]")
# 星號表情/強調段（*…*，含內容整段剝離）
_STAGE_STAR_RE = re.compile(r"\*[^*\n]*\*")

# VC-BRAIN-STRIP-XML-TOOL-TAGS-01：
# LLM 偶爾把偽工具標籤／XML 區塊直接吐進語音輸出（例如
# <write_file path="…">…</write_file>），會被 TTS 逐字唸出並污染 transcript。
# 非串流（靜態）路徑的整段剝離規則：
#   1. 具名工具區塊（開標籤…閉標籤，含內容；DOTALL 跨行）
#   2. 殘餘的孤立閉合標籤 </tag>
#   3. 殘餘的單一標籤（自閉合 <tag /> 或未閉合 <tag ...>）
# 註：`<?…?>`／`<!--…-->` 不在此列（含 `?`／`!`，不匹配 `<[a-zA-Z/]`），
#     但串流守門會把它們當一般標籤抑制，兩條路徑互不衝突。
_XML_BLOCK_RE = re.compile(
    r"<\s*(?P<tag>[A-Za-z][A-Za-z0-9_:\-]*)\b[^>]*>.*?<\s*/\s*(?P=tag)\s*>",
    re.DOTALL | re.IGNORECASE,
)
_XML_CLOSE_RE = re.compile(r"<\s*/\s*[A-Za-z][A-Za-z0-9_:\-]*\s*>")
_XML_TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9_:\-]*(?:\s[^<>]*)?/?\s*>")

# 串流守門用：標籤名首字元判定（`<` 後緊接字母 ⇒ 進入標籤抑制）
_XML_TAG_START_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)

OPEN_BRACKETS = {"（": "）", "(": ")", "[": "]", "【": "】"}
CLOSE_BRACKETS = {"）": "（", ")": "(", "]": "[", "】": "【"}

# VC-AVATAR-05: 基礎動作字典（Base Action Lexicon）—— 全角色共用
BASE_ACTION_LEXICON: dict[str, str] = {
    # happy: 微笑、笑、開心、輕笑
    "微笑": "happy",
    "笑": "happy",
    "開心": "happy",
    "輕笑": "happy",
    "笑了笑": "happy",
    # nod: 點頭、嗯、贊同、理解
    "點頭": "nod",
    "嗯": "nod",
    "贊同": "nod",
    "理解": "nod",
    "輕輕點頭": "nod",
    # blush: 臉紅、害羞、移開視線、低頭
    "臉紅": "blush",
    "害羞": "blush",
    "移開視線": "blush",
    "低頭": "blush",
    "微紅著臉": "blush",
    # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：
    # LLM 口語常吐「低下頭」「低著頭」（動作詞綴），舊表只有「低頭」而
    # `"低頭" in "低下頭"` 在 Python 為 False ⇒ 語意 fallback 掃到也 0 命中。
    "低下頭": "blush",
    "低著頭": "blush",
    # tilt_head: 歪頭、疑惑、好奇
    "歪頭": "tilt_head",
    "疑惑": "tilt_head",
    "好奇": "tilt_head",
    "輕輕歪頭": "tilt_head",
    # thinking: 思考、想想、沉思
    "思考": "thinking",
    "想想": "thinking",
    "沉思": "thinking",
    "若有所思": "thinking",
    # pout: 嘟嘴、鼓頰、生氣
    "嘟嘴": "pout",
    "鼓頰": "pout",
    "生氣": "pout",
    "鼓起臉頰": "pout",
    # cold: 冷淡、嘆氣、抱胸
    "冷淡": "cold",
    "嘆氣": "cold",
    "輕嘆": "cold",
    "抱胸": "cold",
    # VC-AVATAR-05：基礎字典補齊
    "搖頭": "shake_head",
    "不對": "shake_head",
    "否認": "shake_head",
    "驚訝": "surprise",
    "睜大眼睛": "surprise",
    "愣住": "surprise",
    "揮手": "wave",
    "打招呼": "wave",
    "招手": "wave",
    "拍手": "clap",
    "鼓掌": "clap",
    "撫胸": "hand_on_chest",
    "鬆口氣": "hand_on_chest",
    "撫著胸口": "hand_on_chest",
    "聳肩": "shrug",
    "無奈": "shrug",
    "前傾": "lean_forward",
    "湊近": "lean_forward",
    # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：
    # 「湊過去」「靠過去」是口語最常見的前傾變體（舊表只有「湊近」）。
    "湊過去": "lean_forward",
    "靠過去": "lean_forward",
    # VC-AVATAR-REM-40VIDEOS：基礎字典補齊（雷姆 40 支短片新增動作的通用關鍵字）。
    # 這些字在基礎字典一律對應「通用動作鍵」；角色若有專屬短片，由其簽名字典（較高優先序）接手。
    "握拳": "clench_fist",
    "掩嘴": "cover_mouth",
    "摀嘴": "cover_mouth",
    "抓圍裙": "shy_apron_clutch",
    "捏衣角": "shy_apron_clutch",
    "捧頰": "hand_on_cheek",
    "摸臉": "hand_on_cheek",
    "帶淚微笑": "tear_mist_smile",
    "含淚微笑": "tear_mist_smile",
}

# VC-AVATAR-05: 角色專屬簽名動作字典（Per-Agent Signature Lexicon）
# 命中優先序：先查角色專屬，再回退基礎字典（見 map_action_keyword）。
AGENT_SIGNATURE_LEXICONS: dict[str, dict[str, str]] = {
    "agent_mai": {
        "捉弄": "tease",
        "壞笑": "tease",
        "戲謔": "tease",
        "抱胸": "cross_arms",
        "冷淡": "cross_arms",
        "居高臨下": "cross_arms",
        "撥髮": "adjust_hair",
        "撩頭髮": "adjust_hair",
        "整理髮夾": "adjust_hair",
        "別過頭": "blush_turn",
        "傲嬌": "blush_turn",
        "甩頭": "blush_turn",
        "逼近": "lean_in",
        "耳語": "lean_in",
        "壓迫感": "lean_in",
    },
    # VC-AVATAR-REM-40VIDEOS：雷姆 40 支專屬短片對應的簽名動作關鍵字。
    # 命中優先序：完全命中 → 最長鍵子字串（見 _match_lexicon），故「屈膝行禮」優先於「屈膝」。
    "agent_rem": {
        # curtsy：屈膝行禮 / 女僕禮
        "行禮": "curtsy",
        "屈膝": "curtsy",
        "女僕禮": "curtsy",
        "屈膝行禮": "curtsy",
        # pray_hands：雙手合十
        "合十": "pray_hands",
        "雙手合十": "pray_hands",
        "祈禱": "pray_hands",
        "深情注視": "pray_hands",
        # tilt_smile：歪頭笑
        "歪頭笑": "tilt_smile",
        "治癒笑": "tilt_smile",
        "甜甜地笑": "tilt_smile",
        # pout：鼓頰 / 嘟嘴 / 吃醋（雷姆專屬 → pout_jealous 變體家族）
        "吃醋": "pout_jealous",
        "鼓起臉頰": "pout_jealous",
        "小怨念": "pout_jealous",
        "鼓頰": "pout",
        "嘟嘴": "pout",
        # head_pat：等摸頭 / 享受摸頭
        # VC-BRAIN-EXPAND-PAT-LEXICON-01：LLM 實際吐出的摸頭變體遠多於舊表（摸摸頭／摸頭／
        # 被摸頭／摸摸腦袋／揉揉頭／摸了摸頭／給摸頭／乖乖等摸頭），舊表 0 命中 ⇒ 回傳 None
        # ⇒ 前端收不到動作。此處補齊「被動承受 ⇒ enjoy」「主動等待 ⇒ wait」兩族。
        # 註：鍵集合不得與 agent_mai / agent_akane 的簽名鍵相交（見 test_signature_lexicon_isolation）。
        "摸摸頭": "head_pat_enjoy",
        "摸頭": "head_pat_enjoy",
        "被摸頭": "head_pat_enjoy",
        "摸摸腦袋": "head_pat_enjoy",
        "揉揉頭": "head_pat_enjoy",
        "摸了摸頭": "head_pat_enjoy",
        "享受摸頭": "head_pat_enjoy",
        "閉上眼": "head_pat_enjoy",
        "安心享受": "head_pat_enjoy",
        "等摸頭": "head_pat_wait",
        "乖巧等待": "head_pat_wait",
        "給摸頭": "head_pat_wait",
        "乖乖等摸頭": "head_pat_wait",
        # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：自由句實測詞族。
        # 實況日誌：Bryan 說「繼續摸頭就是了」，雷姆答「低下頭，湊過去一點。」——
        # 這類「無括號自由句」由語意 fallback（extract_fallback_actions_from_text）掃描，
        # 若字典查不到就等於 0 動作。以下為該情境的實際詞形。
        # 註：鍵集合仍不得與 agent_mai / agent_akane 的簽名鍵相交
        # （見 test_signature_lexicon_isolation）。
        "低下頭": "head_pat_enjoy",
        "低著頭": "head_pat_enjoy",
        "別停": "head_pat_enjoy",
        "讓Bryan摸": "head_pat_wait",
        "讓主人摸": "head_pat_wait",
        "靠著": "head_pat_enjoy",
        # hand_on_chest：撫胸 / 摸摸胸口（明列以固化雷姆簽名）
        "撫胸": "hand_on_chest",
        "摸摸胸口": "hand_on_chest",
        "撫著胸口": "hand_on_chest",
        "鬆口氣": "hand_on_chest",
        # clench_fist：握拳打氣
        "握拳": "clench_fist",
        "加油": "clench_fist",
        "下定決心": "clench_fist",
        "打氣": "clench_fist",
        "鼓勁": "clench_fist",
        # cover_mouth：摀嘴偷笑
        "摀嘴": "cover_mouth",
        "掩嘴": "cover_mouth",
        "偷笑": "cover_mouth",
        "掩口": "cover_mouth",
        # hand_on_cheek：捧頰 / 摸臉
        # 注意：「托腮」刻意不收錄 —— 它是 agent_akane 的簽名詞（→ finger_chin），
        # 收進來會破壞三角色簽名字典鍵集合兩兩不相交的隔離契約
        # （tests/clients/test_voice_brain_actions.py::test_signature_lexicon_isolation）。
        "捧頰": "hand_on_cheek",
        "摸臉": "hand_on_cheek",
        "輕撫臉頰": "hand_on_cheek",
        # shy_apron_clutch：抓圍裙 / 捏衣角
        "抓圍裙": "shy_apron_clutch",
        "捏衣角": "shy_apron_clutch",
        "揪圍裙": "shy_apron_clutch",
        "不安捏衣角": "shy_apron_clutch",
        # VC-BRAIN-EXPAND-PAT-LEXICON-01：profile 指引原文即寫「（捏緊圍裙）」，
        # 舊表 0 命中 ⇒ 前端收不到。連帶補齊「捏緊衣角」等自然變體。
        "捏緊圍裙": "shy_apron_clutch",
        "捏緊衣角": "shy_apron_clutch",
        # tear_mist_smile：帶淚微笑
        "帶淚微笑": "tear_mist_smile",
        "泛淚微笑": "tear_mist_smile",
        "含淚微笑": "tear_mist_smile",
        "感動": "tear_mist_smile",
        # sigh：嘆氣（雷姆專屬 → sigh 短片；基礎字典的「嘆氣」為 cold）
        "嘆氣": "sigh",
        "輕嘆": "sigh",
        "呼出一口氣": "sigh",
        # clap / shrug / lean_forward / wave：與基礎字典同值，明列以固化雷姆簽名
        "拍手": "clap",
        "鼓掌": "clap",
        "聳肩": "shrug",
        "無奈": "shrug",
        "前傾": "lean_forward",
        "湊近": "lean_forward",
        # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：「湊過去」等口語前傾變體
        "湊過去": "lean_forward",
        "靠過去": "lean_forward",
        "揮手": "wave",
        "招手": "wave",
        "告別": "wave",
        "再見": "wave",
        # shake_head / nod：否認 / 贊同
        "搖頭": "shake_head",
        "不是的": "shake_head",
        "否認": "shake_head",
        "點頭": "nod",
        "嗯": "nod",
        "理解": "nod",
        "贊同": "nod",
        # VC-AVATAR-REM-44VIDEOS-FULL-INTEGRATION：44 支短片的最後 4 個簽名動作。
        # 只放簽名字典、不放 BASE_ACTION_LEXICONS —— 這 4 支片只有雷姆有，其他角色拿到
        # 通用鍵只會指向不存在的資產（前端 onerror 降級＝白動作）。
        # 鍵集合仍須與 agent_mai / agent_akane 兩兩不相交（見 test_signature_lexicon_isolation）。
        # protective_alert：戒備 / 警戒 —— 護住主人的高優先警覺瞬態
        "保護Bryan": "protective_alert",
        "戒備": "protective_alert",
        "保護主人": "protective_alert",
        "警戒": "protective_alert",
        "警惕": "protective_alert",
        "防備": "protective_alert",
        # protective_concern：認真逼視的關切 —— 對象是主人逞強時的雷姆招牌
        "不要逞強": "protective_concern",
        "逞強": "protective_concern",
        "擔心逼視": "protective_concern",
        "認真逼視": "protective_concern",
        "請不要對雷姆逞強": "protective_concern",
        "逼視": "protective_concern",
        # silence_compress：克制靜默 / 無言陪伴 —— 沉重到說不出口時的壓抑持姿
        "沉重靜默": "silence_compress",
        "深沉靜默": "silence_compress",
        "保持靜默": "silence_compress",
        "克制靜默": "silence_compress",
        "壓抑": "silence_compress",
        "無言陪伴": "silence_compress",
        "安靜陪伴": "silence_compress",
        # acceptance_smile：溫柔接納 / 釋懷微笑 —— 心疼之後終於放下的瞬態
        "溫柔接納": "acceptance_smile",
        "釋懷微笑": "acceptance_smile",
        "接納微笑": "acceptance_smile",
        "安心微笑": "acceptance_smile",
        "溫柔釋懷": "acceptance_smile",
        "心疼微笑": "acceptance_smile",
        # tilt_head：BASE 字典既有「歪頭 → tilt_head」，但雷姆簽名未收錄，
        # 「歪著頭」這類口語變體也查不到。此處明列，讓歪頭動作固定落到
        # AVATAR_ACTION_VARIANTS.rem.tilt_head → rem_tilt_smile_warm.mp4。
        "歪頭": "tilt_head",
        "歪著頭": "tilt_head",
    },
    "agent_akane": {
        "托下巴": "finger_chin",
        "托腮": "finger_chin",
        "分析癖": "finger_chin",
        "認真分析": "finger_chin",
        "摀臉": "cover_face",
        "雙手掩面": "cover_face",
        "把臉藏起來": "cover_face",
        "偷瞄": "peek_through_fingers",
        "手指張開縫": "peek_through_fingers",
        "偷看": "peek_through_fingers",
        "燦笑": "bright_smile",
        "豁然開朗": "bright_smile",
        "眼神發亮": "bright_smile",
        "手指互絞": "fidget_hands",
        "手足無措": "fidget_hands",
        "不安": "fidget_hands",
    },
}

# 向後相容別名（VC-AVATAR-03 舊名；語意等同基礎字典）
ACTION_LEXICON: dict[str, str] = BASE_ACTION_LEXICON


def _match_lexicon(cleaned: str, lexicon: dict[str, str]) -> Optional[str]:
    """在單一字典中比對：完全命中優先，其次最長鍵子字串命中。"""
    if cleaned in lexicon:
        return lexicon[cleaned]
    for kw in sorted(lexicon.keys(), key=len, reverse=True):
        if kw in cleaned:
            return lexicon[kw]
    return None


def map_action_keyword(text: str, agent_id: Optional[str] = None) -> Optional[str]:
    """從括號提取的文字中匹配對應的前端 Action Key。若無匹配則回傳 None。

    VC-AVATAR-05：雙層字典 —— 給定 agent_id 且該角色有簽名字典時，先查簽名字典
    （完全命中 → 最長鍵子字串），未命中才回退基礎字典 BASE_ACTION_LEXICON。
    """
    if not text:
        return None
    cleaned = text.strip()
    for ch in "*#[]()（）【】 \t\r\n":
        cleaned = cleaned.replace(ch, "")
    if not cleaned:
        return None
    # 1. 角色專屬簽名字典（較高優先序）
    if agent_id:
        signature = AGENT_SIGNATURE_LEXICONS.get(agent_id)
        if signature:
            hit = _match_lexicon(cleaned, signature)
            if hit:
                return hit
    # 2. 基礎字典
    return _match_lexicon(cleaned, BASE_ACTION_LEXICON)


# VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：
# 語意 fallback 的分句規則與長度上限（module 級常數 ⇒ 可被測試引用、不得散落魔數）。
_FALLBACK_CLAUSE_SPLIT_RE = re.compile(r"[\n。！？；;]")
_FALLBACK_CLAUSE_MAX_CHARS = 25


def extract_fallback_actions_from_text(
    text: str, agent_id: Optional[str] = None
) -> List[str]:
    """若沒有括號動作標籤，改由自由句（散文）掃描動作關鍵字（語意 fallback）。

    根因（VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01）：多輪 session history
    裡已存在「無括號」的動作描述，LLM 會模仿該既有格式（例：`低下頭，湊過去一點。`）
    而不吐 `（…）` ⇒ 括號守門提取為 0 動作 ⇒ 前端動畫永不觸發。

    規則（保守、只認「簡短且整句即動作描述」的子句）：
      - 以換行／句末標點切分子句，逐句 strip；
      - 空句或長度 > `_FALLBACK_CLAUSE_MAX_CHARS` 的子句一律跳過
        （正常對話句通常較長／含多個資訊點，不該被誤判成動作）；
      - 命中 `map_action_keyword` 者按出現順序收錄，去重。
    """
    if not text:
        return []
    found: List[str] = []
    for part in _FALLBACK_CLAUSE_SPLIT_RE.split(text):
        part = part.strip()
        if not part or len(part) > _FALLBACK_CLAUSE_MAX_CHARS:
            continue
        act = map_action_keyword(part, agent_id=agent_id)
        if act and act not in found:
            found.append(act)
    return found


class StreamingVoiceSanitizer:
    """串流輸出守門狀態機：逐字元/逐 token 濾除跨 token 的動作描述（（…）、(…)、[…]、*…*）。

    當進入括號或星號區間時，內容被暫存並不輸出；一旦閉合，提取其中的 ActionToken，
    暫存隨後丟棄；若緩衝區字元超過 max_suppress（防未閉合異常），則安全釋放。
    """

    def __init__(self, max_suppress: int = 50, agent_id: Optional[str] = None):
        self.max_suppress = max_suppress
        # VC-AVATAR-05：角色簽名動作字典的查表鍵（None ⇒ 僅用基礎字典）
        self.agent_id = agent_id
        self._bracket_stack: List[str] = []
        self._in_star = False
        self._suppress_buf: List[str] = []
        self._line_start = True
        self.extracted_actions: List[str] = []
        # VC-BRAIN-STRIP-XML-TOOL-TAGS-01：XML/偽工具標籤抑制狀態
        self._lt_buf: List[str] = []       # 已見 `<`、尚未判定是否為標籤
        self._in_tag = False               # 正在標籤內部（`<` 已見、`>` 未見）
        self._tag_closing = False          # 目前標籤為 `</…>`
        self._in_xml_block = False         # 已進入具名區塊，等 `</tag>` 才解除
        self._xml_block_tag: Optional[str] = None
        self._xml_close_buf: List[str] = []  # 區塊內疑似 `</tag>` 的尾端緩衝
        # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：
        # 全程原始輸出（未經任何抑制）——供「無括號自由句」的語意 fallback 掃描。
        self._full_raw_text: List[str] = []

    def pop_extracted_actions(self) -> List[str]:
        """取出並清空當前累積解析出的 Action Tokens。

        VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：若括號守門一個動作都沒
        提到（LLM 沒寫括號），退而用 `extract_fallback_actions_from_text` 掃描累積原文 —
        保證「嗯。\\n\\n低下頭，湊過去一點。\\n\\n雷姆會當真的。」這類自由句仍能觸發動畫。

        語意 fallback **只在第一次 pop 生效**（取出後即清除原文緩衝）：否則重複 pop 會把
        同一段文字反覆解出動作（既有契約：`pop_extracted_actions()` 第二次必須回 `[]`）。
        """
        acts = list(self.extracted_actions)
        raw = "".join(self._full_raw_text)
        if not acts and raw:
            acts = extract_fallback_actions_from_text(raw, agent_id=self.agent_id)
        self.extracted_actions.clear()
        self._full_raw_text.clear()
        return acts

    def _process_closed_suppression(self) -> None:
        """當括號或星號正常閉合時，解析內容是否含有合法的動作標籤。"""
        raw_text = "".join(self._suppress_buf)
        action_key = map_action_keyword(raw_text, agent_id=self.agent_id)
        if action_key:
            self.extracted_actions.append(action_key)
        self._suppress_buf.clear()

    # ── VC-BRAIN-STRIP-XML-TOOL-TAGS-01：XML / 偽工具標籤抑制 ──────────

    def _enter_xml_block(self, tag: Optional[str], self_closing: bool = False) -> None:
        """進入具名區塊抑制態（`<tag …>` ⇒ 等 `</tag>` 才解除）。

        自閉合標籤（`<tag />`）不進入區塊態 —— 它已完整結束。
        """
        if not tag or self_closing:
            return
        self._in_xml_block = True
        self._xml_block_tag = tag.lower()
        self._xml_close_buf = []

    def _xml_block_end_match(self) -> int:
        """在 `_xml_close_buf` 尾端尋找目前區塊的閉合標籤；回傳被吃掉的字元數。"""
        if not self._xml_block_tag or not self._xml_close_buf:
            return 0
        text = "".join(self._xml_close_buf)
        if not text.endswith(">"):
            return 0
        start = text.rfind("<")
        if start < 0:
            return 0
        candidate = text[start:]
        inner = candidate[1:-1].strip()
        if not inner.startswith("/"):
            return 0
        if inner[1:].strip().lower() != self._xml_block_tag:
            return 0
        return len(candidate)

    def _feed_xml_block(self, ch: str) -> Optional[str]:
        """區塊抑制態內逐字元處理；回傳應輸出的字元（None ⇒ 全部抑制）。

        注意（VC-BRAIN-STRIP-XML-TOOL-TAGS-01 bugfix）：區塊內換行不得進入尾端緩衝。
        否則 `<write_file>\\ntext\\n</write_file>` 會在閉合時把緩衝的 `\\n` 釋放出來，
        造成語音輸出開頭多一個換行。
        """
        if ch == "<":
            # 疑似的閉合標籤起點：緩衝中的區塊內文一律丟棄（不得外洩），
            # `_xml_close_buf` 重新以 `<` 起頭等待閉合標籤。
            self._xml_close_buf = ["<"]
            return None

        if ch == "\n":
            # 換行不進緩衝：區塊內文一律不輸出，閉合時也不得帶出換行
            return None

        self._xml_close_buf.append(ch)
        matched = self._xml_block_end_match()
        if matched:
            # 閉合標籤命中 ⇒ 區塊結束。緩衝中的所有殘餘（= 區塊內文）一律丟棄，
            # 不得釋放（VC-BRAIN-STRIP-XML-TOOL-TAGS-01：<write_file> 內文 0 外洩）。
            self._in_xml_block = False
            self._xml_block_tag = None
            self._xml_close_buf = []
            return None

        # 安全閥：尾端緩衝只為偵測 `</tag>`；區塊內文本身仍不得輸出。
        if len(self._xml_close_buf) > self.max_suppress:
            self._xml_close_buf = []
        return None

    def feed(self, token: str) -> str:
        out: List[str] = []
        # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：原文累積（語意 fallback 用）
        if token:
            self._full_raw_text.append(token)
        for ch in token:
            # ── XML / 偽工具標籤抑制（最高優先序）──────────────────────
            if self._in_xml_block:
                released = self._feed_xml_block(ch)
                if released:
                    out.append(released)
                continue

            if self._in_tag:
                # 標籤內部：只等 `>`（`<` 重新開一個緩衝 ⇒ 取最後一個標籤）
                if ch == "<":
                    self._lt_buf = ["<"]
                else:
                    self._lt_buf.append(ch)
                    if ch == ">":
                        raw = "".join(self._lt_buf)
                        self._in_tag = False
                        self._lt_buf = []
                        if not self._tag_closing:
                            self._enter_xml_block(
                                self._parse_tag_name(raw),
                                self_closing=raw.rstrip().endswith("/>"),
                            )
                continue

            if self._lt_buf:
                # 已見 `<`，判定它是否為標籤起點（跨 token 的 `<` + `write_file>`）
                self._lt_buf.append(ch)
                nxt = self._lt_buf[1]
                if nxt == "/":
                    if len(self._lt_buf) == 2:
                        continue  # 續等標籤名首字元
                    if not self._lt_buf[2].isalpha():
                        out.extend(self._flush_lt_buf())
                        continue
                    self._tag_closing = True
                    self._in_tag = True
                    continue
                if nxt in _XML_TAG_START_CHARS:
                    self._tag_closing = False
                    self._in_tag = True
                    continue
                # 非標籤（例如 `1 < 2`、`a<b`）⇒ 原樣釋放，行為與改動前一致
                out.extend(self._flush_lt_buf())
                continue

            if ch == "<":
                self._lt_buf = ["<"]
                continue

            if ch == "\n":
                self._line_start = True
                if not self._bracket_stack and not self._in_star:
                    out.append(ch)
                continue

            # 行首條列點過濾 (- 或 •)
            if self._line_start and ch in ("-", "•"):
                continue
            if self._line_start and ch not in (" ", "\t"):
                self._line_start = False

            # 星號動作描述 (*...*)
            if ch == "*":
                if not self._in_star:
                    self._in_star = True
                    self._suppress_buf.append(ch)
                else:
                    self._in_star = False
                    self._suppress_buf.append(ch)
                    self._process_closed_suppression()
                continue

            # 括號開頭
            if ch in OPEN_BRACKETS:
                self._bracket_stack.append(OPEN_BRACKETS[ch])
                self._suppress_buf.append(ch)
                continue

            # 括號結尾
            if ch in CLOSE_BRACKETS:
                if self._bracket_stack:
                    if ch == self._bracket_stack[-1]:
                        self._bracket_stack.pop()
                    elif ch in self._bracket_stack:
                        while self._bracket_stack and self._bracket_stack[-1] != ch:
                            self._bracket_stack.pop()
                        if self._bracket_stack:
                            self._bracket_stack.pop()
                    self._suppress_buf.append(ch)
                    if not self._bracket_stack and not self._in_star:
                        self._process_closed_suppression()
                    continue
                else:
                    continue

            # 處於動作抑制區間
            if self._bracket_stack or self._in_star:
                self._suppress_buf.append(ch)
                if len(self._suppress_buf) > self.max_suppress:
                    # 安全閥：未閉合超長，釋放內容（過濾 markdown 符號）
                    flushed = "".join(self._suppress_buf)
                    self._suppress_buf.clear()
                    self._bracket_stack.clear()
                    self._in_star = False
                    for c in flushed:
                        if c not in MARKDOWN_CHARS:
                            out.append(c)
                continue

            # 正常區間：過濾 Markdown 標記符號
            if ch in MARKDOWN_CHARS:
                continue

            out.append(ch)

        return "".join(out)

    @staticmethod
    def _parse_tag_name(raw: str) -> Optional[str]:
        """自原始標籤字串（`<name …>`）取出標籤名；取不到回傳 None。"""
        body = raw[1:-1] if raw.endswith(">") else raw[1:]
        if body.startswith("/"):
            body = body[1:]
        i = 0
        while i < len(body) and (body[i].isalnum() or body[i] in "_:-"):
            i += 1
        name = body[:i]
        return name or None

    def _flush_lt_buf(self) -> List[str]:
        """把誤判的 `<…` 緩衝原樣釋放。"""
        flushed = self._lt_buf
        self._lt_buf = []
        if not flushed:
            return []
        # `<` 本身不是 MARKDOWN_CHARS，無需過濾；其餘字元照原樣輸出
        return [c for c in flushed if c not in MARKDOWN_CHARS or c in "<>"]

    def flush(self) -> str:
        out: List[str] = []
        if self._suppress_buf and len(self._suppress_buf) > self.max_suppress:
            for c in self._suppress_buf:
                if c not in MARKDOWN_CHARS:
                    out.append(c)
        self._suppress_buf.clear()
        self._bracket_stack.clear()
        self._in_star = False
        # VC-BRAIN-STRIP-XML-TOOL-TAGS-01：
        #  - 未閉合的標籤（`<` 已見、`>` 未見）⇒ 抑制捨棄（工具標籤不得被唸出）。
        #  - 誤判的 `<…` 緩衝（非標籤）⇒ 原樣釋放，維持舊行為。
        #  - 區塊態（`<tag>` 已見、`</tag>` 未見）⇒ 全程抑制捨棄。
        if self._lt_buf and not self._in_tag:
            out.extend(self._flush_lt_buf())
        self._lt_buf = []
        self._in_tag = False
        self._tag_closing = False
        self._in_xml_block = False
        self._xml_block_tag = None
        self._xml_close_buf = []
        return "".join(out)


def contains_markdown_chars(text: str) -> bool:
    """審計：輸出是否含有任何守門符號。"""
    return any(ch in text for ch in MARKDOWN_CHARS)


def _strip_xml_artifacts(text: str) -> str:
    """移除 XML/HTML 風格標籤與偽工具區塊（VC-BRAIN-STRIP-XML-TOOL-TAGS-01）。

    順序：① 具名區塊（開標籤…閉標籤，含內容）→ ② 殘餘孤立閉合標籤 → ③ 殘餘單一標籤。
    """
    if "<" not in text:
        return text
    out = _XML_BLOCK_RE.sub("", text)   # <write_file …>…</write_file>、<thinking>…</thinking>
    out = _XML_CLOSE_RE.sub("", out)    # </anything> 殘留
    out = _XML_TAG_RE.sub("", out)      # <tag …> / <tag /> 殘留
    return out


def sanitize_voice_output(text: str) -> str:
    """守門淨化：移除動作/表情段（*…*、（…）含內容）、Markdown/括號符號與行首條列點。

    VC-BRAIN-STRIP-XML-TOOL-TAGS-01：額外剝離 XML/HTML 風格標籤與偽工具區塊
    （例如 `<write_file path="…">…</write_file>`、`<thinking>…</thinking>`），
    確保非串流路徑的語音輸出 0 工具標籤殘留。

    注意順序：XML 剝離必須在字元狀態機**之前**對原始輸入執行。
    狀態機（feed/flush）處理標籤時只抑制標籤本身與區塊內文，
    區塊內容已在此步驟整段移除，避免殘留被唸出。
    """
    out = _strip_xml_artifacts(text)
    sanitizer = StreamingVoiceSanitizer()
    out = sanitizer.feed(out) + sanitizer.flush()
    out = _BULLET_RE.sub(r"\1", out)
    return out.strip()


# ─────────────────────────────────────────────────────────────
# Persona 組裝
# ─────────────────────────────────────────────────────────────

def build_system_prompt(persona_file: Optional[str] = None) -> str:
    """守門規則 + Persona 摘要。persona_file 給定且可讀時以其內容為 Persona 主體。

    Persona 以**字元**為單位截斷至 MAX_PERSONA_CHARS（VC-PERF-OPT-1：原值 6000 已收緊）。
    """
    excerpt = AKANE_LAYER3_PERSONA
    if persona_file:
        try:
            text = Path(persona_file).read_text(encoding="utf-8")
            if text.strip():
                excerpt = text[:MAX_PERSONA_CHARS]  # 控制 token 量（VC-PERF-OPT-1 預算）
        except OSError:
            pass
    return AKANE_VOICE_INVARIANTS + "\n\n" + excerpt


# ─────────────────────────────────────────────────────────────
# Streaming 分句器（Clause Splitter）
# ─────────────────────────────────────────────────────────────

class ClauseSplitter:
    """監聽 Streaming Tokens；累積到標點（，。！？…\\n）且字數 ≥ 4 立即切句。

    feed(token) 回傳本次切出的子句列表；flush() 回傳尾部剩餘內容。
    """

    SPLITTERS = "，。！？…\n"

    def __init__(self, min_chars: int = 4):
        self.min_chars = min_chars
        self._buffer: List[str] = []

    def feed(self, token: str) -> List[str]:
        self._buffer.append(token)
        clauses: List[str] = []
        while True:
            text = "".join(self._buffer)
            cut_end = self._find_cut(text)
            if cut_end is None:
                break
            clause = text[:cut_end].lstrip("\n ")
            rest = text[cut_end:]
            self._buffer = [rest] if rest else []
            if clause:
                clauses.append(clause)
        return clauses

    def flush(self) -> List[str]:
        text = "".join(self._buffer)
        self._buffer = []
        return [text.strip("\n ")] if text.strip("\n ") else []

    def split_stream(self, tokens: Iterable[str]) -> Iterator[str]:
        """串流迭代：token 邊進邊切，結束時 flush 尾部。"""
        for tok in tokens:
            yield from self.feed(tok)
        yield from self.flush()

    def _find_cut(self, text: str) -> Optional[int]:
        """找最早一個「標點位置 + 1 ≥ min_chars」的切點（回傳 exclusive end）。

        同一標點連續出現（如「……」、「？？」）視為一個整體，整段吞入子句。
        """
        cuts = [i for i, ch in enumerate(text) if ch in self.SPLITTERS and i + 1 >= self.min_chars]
        if not cuts:
            return None
        start = min(cuts)
        end = start
        while end + 1 < len(text) and text[end + 1] == text[start]:
            end += 1
        return end + 1


# ─────────────────────────────────────────────────────────────
# LLM 串流通道（生產選配；測試注入 Mock）
# ─────────────────────────────────────────────────────────────

def _build_http_session() -> "requests.Session":
    """建立 VC 專用 HTTP Session：連線池 + keep-alive（requests Session 預設帶 keep-alive）。"""
    import requests  # 懶載入
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=VC_HTTP_POOL_CONNECTIONS,
        pool_maxsize=VC_HTTP_POOL_MAXSIZE,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def build_llm_stream(
    llm_cfg: dict,
    session: Optional[object] = None,
) -> Optional[Callable[[List[dict]], Iterable[str]]]:
    """依 config `llm` 小節建立 OpenAI 相容串流通道；endpoint 缺省 → None（離線降級）。

    VC-PERF-OPT-1 連線重用：所有請求走**同一個** requests.Session（連線池4／上限8，keep-alive），
    不再每回合裸 `requests.post`（每回合一次 TLS 握手）。

    Session 生命週期＝進程生命週期：本模組無關閉鉤子（`_DEFAULT_BRAIN` 為模組級常駐實例，
    web_server/akane_live 亦無 shutdown 呼叫點），故不為關閉而發明新鉤子；lazy 建立且由
    threading.Lock 保護（LLM 呼叫跑在 `asyncio.to_thread` 內 ⇒ Session 必須可跨執行緒共享）。
    `session` 參數僅供測試注入（None ⇒ 首次呼叫時 lazy 建立）。
    """
    from .env_config import normalize_chat_endpoint  # 正規化：缺 /chat/completions 自動補

    endpoint = normalize_chat_endpoint((llm_cfg or {}).get("endpoint") or "")
    if not endpoint:
        return None
    model = (llm_cfg or {}).get("model") or "qwen2.5-7b-instruct"
    api_key = (llm_cfg or {}).get("api_key") or ""

    # 執行緒安全的 lazy Session（單一實例，跨 to_thread 執行緒共享）
    _session_lock = threading.Lock()
    _session_holder: List[object] = [session]

    def _get_session() -> object:
        with _session_lock:
            if _session_holder[0] is None:
                _session_holder[0] = _build_http_session()
            return _session_holder[0]

    def stream(messages: List[dict]) -> Iterable[str]:
        import json

        # VC-TURN-OBS-1：同步 LLM 呼叫全生命週期可見化（requests+SSE 會被 to_thread 包住，
        # 60s timeout 內不可取消；每筆 log 都帶 elapsed_ms 供判讀）
        _t0 = time.perf_counter()

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # VC-PERF-OPT-1 顯式推論參數（此前完全沒帶 ⇒ 供應商預設值不可控）
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "max_tokens": VC_LLM_MAX_TOKENS,
            "temperature": VC_LLM_TEMPERATURE,
        }
        logger.info(
            "[LLM-STREAM] request-start endpoint=%s model=%s elapsed_ms=%.0f",
            endpoint, model, (time.perf_counter() - _t0) * 1000.0,
        )
        # VC-PERF-OPT-1 空內容守門用的回合計數器（reasoning 模型：思考 token 亦計入 max_tokens）
        _chunks = 0
        _reasoning_chunks = 0
        _content_pieces = 0
        _finish_reason = None
        _diag: dict = {
            "content_pieces": 0,
            "reasoning_seen": False,
            "reasoning_chunks": 0,
            "chunks": 0,
            "finish_reason": None,
        }
        try:
            resp = _get_session().post(
                endpoint,
                json=payload,
                headers=headers,
                timeout=VC_LLM_TIMEOUT,
                stream=True,
            )
            resp.raise_for_status()
            # SSE（text/event-stream）常無 charset：requests 預設 ISO-8859-1 會把 UTF-8 中文解成亂碼 → 強制 UTF-8
            resp.encoding = "utf-8"
            _first_token = True
            _tokens = 0
            for line in resp.iter_lines(decode_unicode=True):
                if not line:
                    continue
                line = line.strip()
                if line == "data: [DONE]":
                    break
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[len("data:"):])
                except (ValueError, TypeError):
                    continue
                _chunks += 1
                _choice = (event.get("choices") or [{}])[0]
                if _choice.get("finish_reason"):
                    _finish_reason = _choice.get("finish_reason")
                delta = _choice.get("delta") or {}
                # VC-PERF-OPT-1：reasoning 內容只計數（本票不改變既有取用行為——仍丟棄不 yield）
                _reasoning = delta.get("reasoning") or delta.get("reasoning_content")
                if _reasoning:
                    _reasoning_chunks += 1
                piece = delta.get("content")
                if piece:
                    _tokens += 1
                    _content_pieces += 1
                    if _first_token:
                        _first_token = False
                        logger.info(
                            "[LLM-STREAM] first-token elapsed_ms=%.0f",
                            (time.perf_counter() - _t0) * 1000.0,
                        )
                    yield piece
            if _content_pieces == 0:
                # VC-PERF-OPT-1 空內容守門：可見 content piece 為 0 ⇒ 本回合不會有任何可播內容。
                # 對 reasoning 模型（deepseek-v4.1-flash），思考 token 通常也計入 max_tokens，
                # 上限被思考吃光時即為此形狀 ⇒ 這條 WARNING 是唯一可一眼判定的證據（唯一可 grep 標記）。
                logger.warning(
                    "%s content_pieces=0 max_tokens=%d reasoning_seen=%s "
                    "reasoning_chunks=%d chunks=%d finish_reason=%r elapsed_ms=%.0f",
                    VC_LLM_EMPTY_MARKER, VC_LLM_MAX_TOKENS,
                    _reasoning_chunks > 0, _reasoning_chunks, _chunks,
                    _finish_reason, (time.perf_counter() - _t0) * 1000.0,
                )
            _diag = {
                "content_pieces": _content_pieces,
                "reasoning_seen": _reasoning_chunks > 0,
                "reasoning_chunks": _reasoning_chunks,
                "chunks": _chunks,
                "finish_reason": _finish_reason,
            }
            logger.info(
                "[LLM-STREAM] done tokens=%d elapsed_ms=%.0f",
                _tokens, (time.perf_counter() - _t0) * 1000.0,
            )
        except Exception as exc:  # noqa: BLE001 — 生命週期可見化：異常必須留痕再原樣 re-raise
            logger.error(
                "[LLM-STREAM] error exc_type=%s exc=%r elapsed_ms=%.0f",
                type(exc).__name__, exc, (time.perf_counter() - _t0) * 1000.0,
            )
            raise
        finally:
            # VC-NOREPLY-1 回合級診斷（既有空內容路徑 web_server `empty_llm_output` 取用同一份）
            stream.last_diag = _diag

    stream.last_diag = {}
    return stream


# ─────────────────────────────────────────────────────────────
# VC-2.2 唯讀記憶與時序現象學檢索器（Fail-silent，0 寫入）
# ─────────────────────────────────────────────────────────────

def format_voice_horizon_block(agent_id: str, session_context: str = "", utterance: str = "") -> str:
    """VC-UNIFY-1 讀側：認知地平線（EH-2 Horizon Gate）語音端投影。

    直接複用文字端主服務的 `_format_horizon_block`（src.llm.proxy），確保雷姆在
    語音端獲得與文字端完全相同的阻力約束與 Idiolect 放行名單（Single Soul
    Multi-Modalities）。EH-4.1：`utterance` 僅**透傳**（不做任何差量實作），
    使語音端與文字端產出位元級相同的 Horizon Block（契約 §2.4 ISO-2）。
    fail-silent：任何異常 → 空字串跳過，0 影響既有管線。
    """
    try:
        from src.llm.proxy import _format_horizon_block  # 實際定義於 src/llm/proxy.py

        return (
            _format_horizon_block(
                agent_id, session_context=session_context, utterance=utterance
            )
            or ""
        )
    except Exception:  # noqa: BLE001 — fail-silent：Gate 掛掉 = 無 Horizon 塊
        return ""


class _JudgeShim:
    """LLM-as-judge 用的 proxy shim：只暴露 backend + model 兩個屬性。

    對齊 harness/eh2_smoke_natural3._JudgeShim 與 run_server 的 set_llm_proxy 先例：
    LLMJudge 只讀 self.llm_proxy.backend 與 self.llm_proxy.model，通道不變。
    """

    def __init__(self, backend, model: str):
        self.backend = backend
        self.model = model


def default_memory_retriever(query: str, agent_id: str = "agent_akane") -> Optional[str]:
    """唯讀讀取 SAGE GraphStore；缺檔/例外時 fail-silent 回傳 None（VC-2.2）。"""
    try:
        from src.memory.sage.graph_store import GraphStore
        from src.memory.sage.reader import MemoryReader
        from src.paths import data_root

        db_path = data_root() / "memory" / agent_id / "graph.sqlite"
        if not db_path.is_file():
            return None
        store = GraphStore(db_path=db_path)
        try:
            reader = MemoryReader(store)
            result = reader.retrieve_context(
                query=query,
                top_k=3,
                max_tokens=300,
                mode="precise",
            )
            summary = getattr(result, "summary", "") or ""
            return summary.strip() if summary.strip() else None
        finally:
            store.close()
    except Exception:
        return None


def default_temporal_provider(agent_id: str = "agent_akane") -> Optional[str]:
    """唯讀讀取 relationships.json 並產出 TEMPORAL ANCHOR；缺檔/例外時 fail-silent 回傳 None（VC-2.2）。"""
    try:
        import json
        from datetime import datetime, timezone
        from src.paths import data_root
        from src.soul.temporal_phenomenology import format_temporal_anchor

        path = data_root() / "soul" / agent_id / "relationships.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        entry = data.get("others", {}).get("user_bryan")
        if not isinstance(entry, dict):
            return None
        last_interaction_at = entry.get("last_interaction_at")
        if not last_interaction_at:
            return None
        dt = datetime.fromisoformat(str(last_interaction_at).replace("Z", "+00:00"))
        last_ts = int(dt.timestamp())
        now = int(datetime.now(timezone.utc).timestamp())
        anchor = format_temporal_anchor(agent_id, last_ts, now)
        return anchor.strip() if anchor and anchor.strip() else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# 茜語音大腦
# ─────────────────────────────────────────────────────────────

class AkaneVoiceBrain:
    """黑川茜語音專用大腦：Persona 注入 + 輸出守門 + 分句器。

    llm_stream 可注入（callable(messages) -> Iterable[str]）；None 且 config 無
    endpoint 時離線降級為內建短回覆。所有輸出必過守門，保證 0 Markdown。
    支援 VC-2.2 記憶（SAGE Reader）與主觀時序（Temporal Anchor）唯讀注入。
    """

    def __init__(
        self,
        llm_stream: Optional[Callable[[List[dict]], Iterable[str]]] = None,
        persona: Optional[str] = None,
        persona_file: Optional[str] = None,
        config: Optional[dict] = None,
        memory_retriever: Optional[Callable[[str], Optional[str]]] = None,
        temporal_provider: Optional[Callable[[], Optional[str]]] = None,
        session_store: Optional[object] = None,
        agent_id: str = "agent_akane",
    ):
        self.config = config or {}
        self.agent_id = agent_id
        self.persona = persona or build_system_prompt(persona_file)
        self.splitter = ClauseSplitter()
        self.llm_stream = llm_stream
        if self.llm_stream is None:
            self.llm_stream = build_llm_stream(self.config.get("llm") or {})

        # VC-2.2 記憶與時序讀側鉤子（可注入；未注入且未停用時預設安全讀取器）
        mem_cfg = self.config.get("memory", {})
        if memory_retriever is not None:
            self.memory_retriever = memory_retriever
        elif mem_cfg.get("enabled", True):
            self.memory_retriever = lambda q: default_memory_retriever(q, agent_id=self.agent_id)
        else:
            self.memory_retriever = None

        tempo_cfg = self.config.get("temporal", {})
        if temporal_provider is not None:
            self.temporal_provider = temporal_provider
        elif tempo_cfg.get("enabled", True):
            self.temporal_provider = lambda: default_temporal_provider(agent_id=self.agent_id)
        else:
            self.temporal_provider = None

        # VC-2.5 跨介面共享短期會話流（可注入；未注入且未停用時預設 SessionStore）
        if session_store is not None:
            self.session_store = session_store
        elif mem_cfg.get("session_store", {}).get("enabled", True):
            self.session_store = SessionStore()
        else:
            self.session_store = None

        # VC-UNIFY-1 寫側：SAGE 記憶背景入庫（config `memory.sage_write.enabled` 顯式開啟）
        self._sage_write_enabled = bool(
            (self.config.get("memory") or {}).get("sage_write", {}).get("enabled", False)
        )
        self._sage_provider = None
        self._sage_provider_failed = False

    def system_prompt(self) -> str:
        return self.persona

    @property
    def last_llm_diag(self) -> Optional[dict]:
        """最近一次 LLM 回合的診斷（VC-PERF-OPT-1 空內容守門）。

        由 `build_llm_stream` 產生的 stream 於回合結束時寫入 `stream.last_diag`；
        web_server 既有 `empty_llm_output` 失敗路徑讀取本屬性，使「max_tokens 被
        reasoning 吃光 ⇒ 0 可見 content」在第一次實測即可一眼判定（無第二條錯誤路徑）。
        注入自訂 llm_stream（測試／離線）時無此資訊 → None。
        """
        return getattr(self.llm_stream, "last_diag", None)

    def _build_messages(self, user_text: str, history=None, *, source: str = "") -> List[dict]:
        """組裝對話歷史、時序現象學（TA-2）與 SAGE 記憶檢索，注入 system prompt。

        VC-ASR-CHANNEL-HINT-1：`source == "voice_asr"`（本輪輸入來自 ASR）時，於 persona 的
        system 訊息之後、user 訊息之前另加**一則 ephemeral system**（標 ＋ 4.2 三段 ＋ 名冊
        一行）；persona 區塊與 **user content 逐位元＝轉寫本文**。其餘任何來源（打字 fallback /
        未知 / 未傳）**完全不注入**。

        VC-BRAIN-INJECT-ACTION-HINT-01：persona 之後追加 `companion.system_prompt` 的伴侶
        肢體動作指引（括號動作標籤的例外條款）。config 無此鍵或值為空白 ⇒ 不注入，行為與
        改動前逐位元相同。
        """
        sys_parts = [self.persona]

        # 0. VC-BRAIN-INJECT-ACTION-HINT-01：伴侶肢體動作指引（`companion.system_prompt`）
        #    AKANE_VOICE_INVARIANTS 第 2 條「0 括號動作描寫」是**朗讀層**的守門（括號內容
        #    不得被 TTS 唸出），但 LLM 從未被告知「括號動作是合法且被期待的輸出格式」——
        #    因為 profile 的 `companion.system_prompt` 從來沒有進過 prompt。結果：LLM 只看到
        #    禁令，自然 0 動作標籤 ⇒ 前端動畫／動作鍵永遠收不到訊號。
        #    本段在 persona 之後明示「例外條款」：括號動作標籤是語音動畫驅動標籤，系統會
        #    自動解析並從朗讀中消除（sanitize / StreamingVoiceSanitizer 已實作），故可安心使用。
        #    fail-closed：config 無 `companion` 或無非空白 `system_prompt` ⇒ 一個字都不加，
        #    整份 messages 與改動前逐位元相同（向後相容；見 tests/clients/test_vc_asr_channel_hint.py）。
        companion_action_hint = (self.config.get("companion") or {}).get("system_prompt")
        if companion_action_hint and companion_action_hint.strip():
            # VC-BRAIN-DUAL-ACTION-DETECTION-AND-SEMANTIC-FALLBACK-01：強化措辭。
            # 實況顯示 LLM 會把動作寫成「普通文字」（`低下頭，湊過去一點。`）而 0 括號，
            # 故本段明示「每一句回覆都要有括號動作標籤」＋「務必用全形括號（動作）包起來」。
            # 保留「語音動畫驅動標籤」字樣（既有測試的契約錨點）。
            sys_parts.append(
                f"【伴侶肢體動作指引】\n"
                f"在對話中，你必須積極且頻繁地在每句回覆加入括號動作標籤"
                f"（如（摸摸頭）、（微笑）、（點頭）、（屈膝行禮）、（臉紅）、（鼓頰吃醋）等），"
                f"以驅動虛擬化身動作演出（此為語音動畫驅動標籤，系統會自動解析並從朗讀中消除，"
                f"不會被唸出）。\n"
                f"請不要把動作直接寫成普通文字，務必用全形括號（動作）包起來！\n"
                f"動作習慣指引：{companion_action_hint.strip()}"
            )

        # 0b. VC-UNIFY-1：認知地平線（Persona 之後、即時對話之前；fail-silent 空字串跳過）
        #    EH-4.1：utterance 僅透傳（差量實作只在主服務讀側模組一份，VC 端 0 實作）
        horizon_block = format_voice_horizon_block(self.agent_id, utterance=user_text)
        if horizon_block:
            sys_parts.append(horizon_block)

        # 1. 時序現象學錨點（若有）
        if self.temporal_provider:
            try:
                anchor = self.temporal_provider()
                if anchor and anchor.strip():
                    sys_parts.append(f"【當前時序體感】\n{anchor.strip()}")
            except Exception:
                pass

        # 2. SAGE 唯讀記憶檢索（若有）
        if self.memory_retriever and user_text:
            try:
                mem = self.memory_retriever(user_text)
                if mem and mem.strip():
                    sys_parts.append(f"【關於 Bryan 的記憶】\n你記得以下這些事情：\n{mem.strip()}")
            except Exception:
                pass

        # 3. 跨介面時空體感（VC-2.5 SessionStore，若有）
        session_history = None  # None → 回退用傳入 history 參數
        if self.session_store is not None:
            try:
                ctx = self.session_store.get_active_context(self.agent_id, "user_bryan")
                if ctx["history"] or ctx["phase"] != "NO_TURNS":
                    sys_parts.append(f"【跨介面時空體感】\n{ctx['anchor']}")
                    if ctx["history"]:
                        session_history = ctx["history"]
            except Exception:
                pass

        full_system = "\n\n".join(sys_parts)
        messages = [{"role": "system", "content": full_system}]
        # VC-ASR-CHANNEL-HINT-1（①C 拍板）：ASR 回合在 persona 的 system 訊息**之後**、
        # user 訊息**之前**，另加**一則 ephemeral system**（只此一 call，不進 persona／不進
        # 任何持久或跨回合狀態）。persona 區塊與 user content 皆**逐位元不變** ⇒ 提示不會被
        # SAGE／session 記成「用戶說的話」。
        if source == VC_ASR_SOURCE:
            messages.append({"role": "system", "content": asr_channel_prefix()})
        if session_history is not None:
            messages += session_history
        else:
            messages += list(history or [])
        messages.append({"role": "user", "content": user_text})
        return messages

    def respond(self, user_text: str, history=None) -> str:
        """產生茜的回覆（整段）。輸出必過守門檢查。

        history: 選用——先前輪次訊息（role=user/assistant），依序插入 system 之後，
        讓茜承接前文（對話連貫）。缺省 None = 維持原本單回合行為。
        """
        messages = self._build_messages(user_text, history=history)
        if self.llm_stream is None:
            return self._guarded("我在。說說看。")
        try:
            tokens = list(self.llm_stream(messages))
        except Exception:
            tokens = []
        text = "".join(tokens).strip()
        if not text:
            text = "嗯。我在聽。"
        return self._guarded(text)

    def stream_respond(
        self, user_text: str, history=None, *, source: str = ""
    ) -> Iterator[str]:
        """串流回應：token 邊收邊過守門，交由分句器即時切句（邊生邊播）。

        history: 選用——先前輪次訊息（role=user/assistant），依序插入 system 之後（對話連貫）。
        source: VC-ASR-CHANNEL-HINT-1——輸入通道（`"voice_asr"` 才注入通道提示）；
                缺省空字串 ⇒ 與改動前逐位元相同。
        """
        messages = self._build_messages(user_text, history=history, source=source)
        if self.llm_stream is None:
            yield "我在。說說看。"
            return
        self._last_extracted_actions: List[str] = []
        sanitizer = StreamingVoiceSanitizer(agent_id=self.agent_id)
        for token in self.llm_stream(messages):
            cleaned = sanitizer.feed(token)
            if cleaned:
                yield cleaned
        tail = sanitizer.flush()
        if tail:
            yield tail
        self._last_extracted_actions = sanitizer.pop_extracted_actions()

    @property
    def last_extracted_actions(self) -> List[str]:
        """最近一次 stream_respond 回合中自 LLM 輸出提取的動作標籤（VC-AVATAR-03）。"""
        return list(getattr(self, "_last_extracted_actions", []))

    def _guarded(self, text: str) -> str:
        result = sanitize_voice_output(text).strip()
        return result if result else "……"

    # ── VC-UNIFY-1 寫側：SAGE 記憶背景入庫（Fire-and-Forget，0 阻塞音訊）────

    def _get_sage_provider(self):
        """Lazy init SAGELiteProvider。

        data_root 與 default_memory_retriever 完全一致：
        ``data_root()/memory/<agent_id>/graph.sqlite``（src.paths.data_root 規則）。
        LLM judge 通道沿用 VC 既有 llm config；無 endpoint → regex fallback（writer 內建）。
        fail-silent：任何異常 → None（不阻斷呼叫端）。
        """
        if self._sage_provider is not None:
            return self._sage_provider
        if self._sage_provider_failed:
            return None
        try:
            from src.memory.sage.provider import SAGELiteProvider  # 懶載入重型依賴
            from src.paths import data_root

            agent_dir = data_root() / "memory" / self.agent_id
            agent_dir.mkdir(parents=True, exist_ok=True)
            provider = SAGELiteProvider(
                profile_id=self.agent_id,
                data_dir=str(agent_dir),
            )
            provider.initialize(session_id=f"voice_{self.agent_id}")
            self._wire_sage_judge()
            self._sage_provider = provider
        except Exception as exc:  # noqa: BLE001 — fail-silent
            self._sage_provider_failed = True
            logger.warning(f"[VC-UNIFY-1] SAGE provider init fail-silent: {exc}")
            self._sage_provider = None
        return self._sage_provider

    def _wire_sage_judge(self) -> None:
        """把 VC 既有 LLM 通道（config llm）以 shim 形式接入 SAGE writer 的 LLM judge。

        對齊 run_server（set_llm_proxy）與 harness/eh2_smoke_natural3 的 _JudgeShim 先例：
        LLMJudge 只讀 llm_proxy.backend / llm_proxy.model。無 llm.endpoint → 不接線，
        writer 自動 fallback regex heuristic（fail-silent，0 網路）。
        """
        try:
            llm_cfg = self.config.get("llm") or {}
            endpoint = str(llm_cfg.get("endpoint") or "").strip()
            if not endpoint:
                return
            from src.llm.proxy import OpenAIBackend

            from .env_config import normalize_chat_endpoint

            backend = OpenAIBackend(
                api_key=str(llm_cfg.get("api_key") or ""),
                base_url=normalize_chat_endpoint(endpoint),
            )
            shim = _JudgeShim(backend, str(llm_cfg.get("model") or "deepseek-v4.1-flash"))
            from src.memory.sage.writer import set_llm_proxy

            set_llm_proxy(shim)
        except Exception as exc:  # noqa: BLE001 — fail-silent
            logger.warning(f"[VC-UNIFY-1] SAGE judge wiring fail-silent: {exc}")

    def schedule_sage_commit(
        self,
        user_text: str,
        agent_text: str,
        session_id: Optional[str] = None,
    ) -> Optional[asyncio.Task]:
        """VC-UNIFY-1 寫側：把一輪語音對話以背景 task 非同步寫入 SAGE 記憶庫。

        - Fire-and-Forget：asyncio.create_task，不 await、不阻塞音訊串流輸出。
        - VC-UNIFY-1.2：背景 task 內 post_reply_commit 成功後顯式 flush GraphStore
          （跨進程事務可見性），flush 例外 fail-silent（僅 warning），語音 0 延遲影響。
        - 若無 running loop（終端版同步回呼），降級為 daemon thread 內 asyncio.run。
        - Fail-silent：task 內任何異常 → log warning，絕不中斷語音服務。
        - 僅在 config ``memory.sage_write.enabled: true`` 時啟用（預設關閉，0 既有行為）。
        - 回傳 asyncio.Task（呼叫方可忽略，即 fire-and-forget；測試可 await 驗收）。
        """
        if not self._sage_write_enabled:
            return None
        provider = self._get_sage_provider()
        if provider is None:
            return None
        sid = session_id or f"voice_{self.agent_id}"
        # VC-UNIFY-1.1：canonical 唯一格式 bryan:{agent_id}（與文字端 middleware / SAGE 一致）
        source_pair = f"bryan:{self.agent_id}"

        async def _commit() -> None:
            try:
                await provider.post_reply_commit(
                    session_id=sid,
                    last_user_msg=user_text,
                    agent_reply=agent_text,
                    source_pair=source_pair,
                )
                # VC-UNIFY-1.2：僅在 post_reply_commit 成功返回後執行顯式 flush。
                # 確保已寫入的事務立即 commit 至 SQLite，讓外部進程（文字端主服務）
                # 的獨立連線立即可讀 —— 語音剛說的話文字端立刻接上（即時陪伴感）。
                try:
                    if hasattr(provider, "_writer") and hasattr(provider._writer, "store"):
                        provider._writer.store.flush()
                    elif hasattr(provider, "store"):
                        provider.store.flush()
                except Exception as exc:  # noqa: BLE001 — fail-silent
                    # Fail-Silent：僅記錄 warning，嚴禁中斷語音或拋出異常
                    logger.warning(f"[VC-UNIFY-1.2] SAGE flush fail-silent: {exc}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — fail-silent
                logger.warning(f"[VC-UNIFY-1] SAGE commit fail-silent: {exc}")

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=lambda: asyncio.run(_commit()), daemon=True).start()
            return None
        try:
            return asyncio.create_task(_commit())  # 0 await、0 阻塞
        except Exception as exc:  # noqa: BLE001 — fail-silent
            logger.warning(f"[VC-UNIFY-1] SAGE commit schedule fail-silent: {exc}")
            return None


# 模組級預設實例（離線模式）
_DEFAULT_BRAIN = AkaneVoiceBrain()


def respond_as_akane(user_text: str) -> str:
    """無狀態便捷入口：以預設大腦回應（離線降級或依 config）。"""
    return _DEFAULT_BRAIN.respond(user_text)