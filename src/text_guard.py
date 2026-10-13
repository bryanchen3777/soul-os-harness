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


# ─────────────────────────────────────────────────────────────────────────────
# REM-LEGACY-CLEANSE (2026-10-12): legacy runaway turn 讀取端淨化
# ─────────────────────────────────────────────────────────────────────────────

# 觸發門檻：>1,000 字元（Bryn SOUL-MEM-001 Phase 0 工單原訂）。
# 依據: 量測全部 153 個對話檔，正常 assistant 訊息 max ≤ 97 字元（修法 10 於
# 2026-08-04 以 check_response_length_dist.py 量得 p99=94、0 條 >200），
# 汙染則為 6,690–6,938 字元。1,000 落在兩者之間的極寬空白帶，不會誤切正常訊息。
_LEGACY_RUNAWAY_TRIGGER_CHARS = 1000

# 截斷目標：200 字元（對齊修法 10 `_safe_truncate_on_length` 的 Bry 拍板規格）
_LEGACY_RUNAWAY_KEEP_CHARS = 200

# 未閉合 JSON 殼前綴：同 PERSONA-FIX-1 D1 (`proxy.py:_TEXT_JSON_SHELL_PREFIX`)
_LEGACY_BROKEN_SHELL_RE = re.compile(r'^\{\s*"text"\s*:\s*"')

# 截斷斷點標點（與修法 10 `_safe_truncate_on_length` 同清單）
_LEGACY_CUT_POINTS = ("。", "！", "？", "?", "!", "——", "……", "，", ",", ".")


def sanitize_legacy_runaway_turn(
    text: str,
    trigger_chars: int = _LEGACY_RUNAWAY_TRIGGER_CHARS,
    keep_chars: int = _LEGACY_RUNAWAY_KEEP_CHARS,
) -> str:
    """REM-LEGACY-CLEANSE: 淨化遺留的失控 assistant 回合（純函式、無 IO）。

    背景（SOUL-MEM-001 Phase 0 實測）:
      `data/conversations/bryan_agent_mahiru_private.json` 內有 3 條各約 6,900
      字元的 assistant 訊息，佔該檔 97.5%、約 15,848 tokens。根因為 2026-09-03
      當時 `response_format=json_object` 模式下模型輸出 `{"text": "` 外殼後退化重複、
      打滿 `max_tokens: 4000` 導致 JSON 字串未閉合即被截斷，而純 text 兜底路徑
      當時尚未 unwrap 外殼，遂原樣持久化並逐輪重餵。

      該 bug 的**寫入端**已於兩處修補：修法 10（2026-08-04，`finish_reason=length`
      強制截斷）與 PERSONA-FIX-1 D1（2026-09-05，`_unwrap_text_json_shell`）。
      汙染檔最後寫入 2026-09-03，全庫 153 個對話檔僅此 1 個命中，且已 38 天未活動。
      ⇒ 這是**死資料**，不是仍在運作的迴圈。故本函式只做**讀取端**淨化，
      不新增寫入端防線（避免對已修好的 bug 疊第三道牆）。

    觸發條件（任一）:
      1. 開頭為 `{"text": "` 外殼且 `json.loads` 失敗（未閉合／半殘 JSON）
      2. 長度 > `trigger_chars`（遠超正常對話長度）

    動作: 取**最後** `keep_chars` 字元（對齊修法 10 語意：保留尾部、取最後一個
    完整標點斷點，不硬切字中間），並在前綴移除後才截斷。

    Returns:
        淨化後的字串；未觸發時原樣回傳（不 strip、不改動，零行為變化）。
    """
    if not text:
        return text

    body = text
    # 條件 1：未閉合 / 半殘 JSON 殼 → 先剝殼，剝完仍過長則續走條件 2
    stripped = text.strip()
    if _LEGACY_BROKEN_SHELL_RE.match(stripped):
        import json as _json

        try:
            _json.loads(stripped)
        except (ValueError, TypeError):
            body = stripped[_LEGACY_BROKEN_SHELL_RE.match(stripped).end():].strip()

    # 條件 2：異常長度 → 對齊修法 10 截斷語意
    if len(body) > trigger_chars:
        tail = body[-keep_chars:]
        cuts = [tail.rfind(p) + len(p) for p in _LEGACY_CUT_POINTS if tail.rfind(p) >= 0]
        if cuts:
            return tail[: max(cuts)]
        return tail

    return body