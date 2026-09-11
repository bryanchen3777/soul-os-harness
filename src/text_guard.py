"""
src/text_guard.py — REM-TEXT-1 文字頻道輸出守門（純函式，無 IO、無狀態）

語意對齊 `clients/voice_companion/akane_voice_brain.py` 的 `sanitize_voice_output`
（VC 語音守門），但這是放在 src/ 側的可重用版本——不從 clients/ import，
避免 src/ → clients/ 的反向依賴。兩者共享的正則語意：

- 剝除全形/半形括號動作段（`（…）`／`(…)`）**含內容**（VC `_STAGE_PAREN_RE`）
- 剝除 `*…*` 表情/強調段**含內容**（VC 星號處理）
- 未閉合括號/星號走**安全閥**：未閉合段 ≤ `_MAX_SUPPRESS_CHARS`（對齊 VC
  `StreamingVoiceSanitizer.max_suppress = 50`）整段丟棄；超過則釋放內容
  （只剝開括號字元），避免把長段真實講話誤殺
- 剝除後行首孤立標點正規化：剝掉 `（…）` 若在行首留下孤立半形標點
  （例如 `（手上的事停了，抬眼）.嗯。……` → `.嗯。……` 的那個 `.`），
  移除它，避免製造新的怪異開頭。只處理「半形標點後接 CJK 字元」的型態，
  不碰 `……`（U+2026）／`——` 等全形合法開頭，也不碰 `.env` 這類半形詞。

per-agent 開關由呼叫端決定（`configs/default.yaml` agents[].text_channel_stage_guard，
預設缺省 = False = 維持現狀不剝；目前只對 `agent_rem` 開啟）。
"""

from __future__ import annotations

import re

# VC akane_voice_brain._STAGE_PAREN_RE 同語意：括號動作段整段剝離（含內容）
_STAGE_PAREN_RE = re.compile(r"[（(][^（(）)]*[）)]")
# VC 星號表情/強調段（單行內）整段剝離
_STAGE_STAR_RE = re.compile(r"\*[^*\n]*\*")
# 未閉合安全閥臨界：對齊 VC StreamingVoiceSanitizer.max_suppress = 50
_MAX_SUPPRESS_CHARS = 50
# 行首孤立半形標點正規化：連續 1-3 個半形標點 + 後接 CJK 字元（MULTILINE 逐行）
_LEADING_ORPHAN_PUNCT_RE = re.compile(
    r"^[.,;:!?]{1,3}(?=[\u4e00-\u9fff\u3400-\u4dbf])", re.MULTILINE
)
# CJK 判斷用（_resolve_unclosed 不需要，僅供未來擴充）


def _resolve_unclosed(text: str) -> str:
    """未閉合括號/星號安全閥（對齊 VC 的 max_suppress 語意）。

    - 未閉合段 ≤ _MAX_SUPPRESS_CHARS：整段丟棄（視為動作段）
    - 未閉合段 > _MAX_SUPPRESS_CHARS：釋放內容（剝除開括號/星號字元），
      因為長段內容更可能是真實講話而非動作描寫

    反覆掃描直到穩定（每次處理最後一個未閉合段，字串嚴格變短，保證終止）。
    """
    prev = None
    while prev != text:
        prev = text
        for open_ch, close_ch in (("（", "）"), ("(", ")"), ("*", "*")):
            idx = text.rfind(open_ch)
            if idx == -1:
                continue
            if close_ch in text[idx + 1:]:
                continue  # 之後還有閉合 → 屬於已閉合段，由 _STAGE_*_RE 處理
            tail = text[idx:]
            if len(tail) <= _MAX_SUPPRESS_CHARS:
                text = text[:idx]
            else:
                # 安全釋放：只剝開括號字元，內容保留
                text = text[:idx] + tail[1:]
    return text


def sanitize_text_channel_output(text: str) -> str:
    """文字頻道輸出守門：剝除括號動作段（（…）/(…)）、*…* 表情段與殘留標點。

    回傳剝除後的乾淨文字。輸入為空/無括號時原樣回傳（.strip() 後）。
    """
    if not text:
        return text
    # 1) 剝閉合括號段（含內容）
    text = _STAGE_PAREN_RE.sub("", text)
    # 2) 剝閉合 *…* 段（含內容）
    text = _STAGE_STAR_RE.sub("", text)
    # 3) 未閉合安全閥
    text = _resolve_unclosed(text)
    # 4) 行首孤立半形標點正規化（剝除後殘留的怪異開頭）
    text = _LEADING_ORPHAN_PUNCT_RE.sub("", text)
    return text.strip()