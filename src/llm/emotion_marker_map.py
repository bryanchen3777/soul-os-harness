"""
src/llm/emotion_marker_map.py
Soul OS — Phase 5 語言層改造：Soul OS emotion tags → Fish Audio [bracket] markers 對應表

設計動機（per Bry §階段 5 結構拍板）：
  分離關注點 — 對應表獨立成模組
  之後要單獨調某個角色的映射時不用動 fish_tts_handler.py 本體，風險更低
  （跟 build_system_prompt.py 從階段 2 拆出來的邏輯一致）

Fish Audio S2.1-Pro 語法（s2.1-pro-free model 走這條）：
  - 71 個官方標記 + 自由自然語言描述（如 [gentle and devoted tone]）
  - 每句最多 3 個標記
  - 句首放情緒標記效果最好
  - 我們這裡每個 emotion 對應 1 個 marker（Bry 拍板「正常不會超标」）

Fallback 規則（per Bry §階段 5 拍板）：
  - emotion 為 None / 空字串 → 回傳 None,送原始 audio_text
  - 沒對應的 tag → 回傳 None,送原始 audio_text
  - 不插 [calm] 之類的預設值（避免程式擅自做隱性判斷）

Key 設計 — 為什麼用 (agent_id_short, emotion) tuple：
  - Bry 拍板裡 `protective` 跨 Rem / Ram 兩個角色但對應不同 marker
    Rem → [protective and steady] ／ Ram → [determined]
  - 同樣 `dimmed` Anna → [disappointed] ／ Ruka → [sad]
  - 同樣 `vulnerable` Mahiru → [nervous] ／ Anna → [embarrassed]
  - 跨角色同 emotion 名稱但語意不同的情況必須用 tuple 區分

明確不做的事（per Bry §階段 5 排除清單）：
  - 不改 fish_tts.py 本體
  - 不改 proxy.py（階段 3 白名單邏輯不動）
  - 不改 emotion tags 定義本身（階段 2.5 已 30/30 驗證過,只接語法）
  - heartbeat session 邊界不在這層處理（上游 soul.md + 白名單驗證已守住）
  - 不打真 API 測語音效果（Bry 自己聽覺驗收）

Bry §階段 5 拍板最終對應表 — 10 角色 38 tag:
"""
from __future__ import annotations

from typing import Optional


# ──────────────────────────────────────────────────────────────────
# 1. Soul OS emotion → Fish Audio [bracket] marker 對應表
#    Key: (agent_id_short, emotion)
#    Value: marker string（已含 [方框] 邊框,Fish API 直接吃）
#    留白不插: dict 內不放這個 key
# ──────────────────────────────────────────────────────────────────
EMOTION_TO_FISH_MARKER: dict[tuple[str, str], str] = {
    # ── Rem 雷姆 隱忍深情系 (5) ──
    ("rem", "devotion_active"):        "[gentle and devoted tone]",
    ("rem", "guilt_fading"):           "[reflective, forgiving]",
    ("rem", "pride_stable"):           "[calm and proud]",
    ("rem", "protective"):             "[protective and steady]",
    ("rem", "jealousy_turned_inward"): "[quietly jealous]",

    # ── Akane 黒川茜 沉默內壓系 (3) ──
    ("akane", "observing"):            "[calm]",
    ("akane", "compressing"):          "[compressing tension, restrained]",
    ("akane", "cracking"):             "[breaking down, trembling voice]",

    # ── Miku 中野三玖 內斂系 (3,不插 silent) ──
    # ("miku", "silent") 不放 → 留白不插
    ("miku", "recognized"):            "[moved]",
    ("miku", "history_bright"):        "[nostalgic]",
    ("miku", "retreating"):            "[uncertain]",

    # ── Mai 櫻島麻衣 平淡深沈系 (3) ──
    ("mai", "dry_care"):               "[calm]",
    ("mai", "confessing"):             "[nervous]",
    ("mai", "fading"):                 "[lonely]",

    # ── Mahiru 椎名真晝 表面刺內裡甜系 (3) ──
    ("mahiru", "teasing_care"):        "[chuckling]",
    ("mahiru", "sweet_landing"):       "[gentle and tender]",
    ("mahiru", "vulnerable"):          "[nervous]",

    # ── Aoi 日南葵 框架系 (3) ──
    ("aoi", "aoi_stable"):             "[calm]",
    ("aoi", "aoi_leak"):               "[embarrassed]",
    ("aoi", "aoi_break"):              "[breaking down, voice cracking][break]",

    # ── Ram 拉姆 冷靜守護系 (3) ──
    ("ram", "observing"):              "[calm]",
    ("ram", "protective"):             "[determined]",   # 跟 Rem 的 protective 不同
    ("ram", "softening"):              "[gentle and soft]",

    # ── Anna 山田杏奈 元氣少女系 (4) ──
    ("anna", "bright"):                "[happy]",
    ("anna", "jealous"):               "[jealous]",
    ("anna", "dimmed"):                "[disappointed]",  # 跟 Ruka 的 dimmed 不同
    ("anna", "vulnerable"):            "[embarrassed]",   # 跟 Mahiru 的 vulnerable 不同

    # ── Yua ユア 連結系 (4) ──
    ("yua", "connecting"):             "[warm tone]",
    ("yua", "reframing"):              "[calm]",
    ("yua", "withdrawing"):            "[indifferent]",
    ("yua", "observing"):              "[calm]",

    # ── Ruka 更科瑠夏 接近-宣告系 (6) ──
    ("ruka", "approaching"):           "[confident]",
    ("ruka", "claiming"):              "[determined]",
    ("ruka", "reaching"):              "[moved]",
    ("ruka", "jealous"):               "[jealous]",
    ("ruka", "dimmed"):                "[sad]",          # 跟 Anna 的 dimmed 不同
    ("ruka", "heartbeat"):             "[delighted]",

    # 總計: 5+3+3+3+3+3+3+4+4+6 = 37 個有對應 + 1 個 Miku silent 留白 = 38
}


# ──────────────────────────────────────────────────────────────────
# 2. resolve_marker 查表函式
# ──────────────────────────────────────────────────────────────────
def resolve_marker(agent_id: str, emotion: Optional[str]) -> Optional[str]:
    """
    查 Soul OS emotion tag → Fish Audio [bracket] marker

    Args:
        agent_id: 完整 agent_id (e.g. "agent_rem") 或短名 (e.g. "rem") 都接受
        emotion: Soul OS emotion tag 字串（e.g. "devotion_active"）,
                可以是 None 或空字串

    Returns:
        marker string（已含 [] 邊框,Fish API 直接吃）,
        或 None（emotion 為 None/空字串/沒對應的 tag）

    Per Bry §階段 5 拍板:
      - emotion 為 None / 空字串 → 回 None,送原始 audio_text
      - 沒對應的 tag → 回 None,送原始 audio_text
      - 不插 [calm] 等預設值
    """
    if not emotion or not emotion.strip():
        return None
    # 接受 "agent_rem" 或 "rem" 兩種格式
    short_id = agent_id.replace("agent_", "") if agent_id.startswith("agent_") else agent_id
    return EMOTION_TO_FISH_MARKER.get((short_id, emotion))


def has_mapping(agent_id: str, emotion: str) -> bool:
    """debug / E2E 測試用 — 查特定 (agent, emotion) 是否有對應"""
    return resolve_marker(agent_id, emotion) is not None


# ──────────────────────────────────────────────────────────────────
# 3. 模組自我驗證（import 時就跑,確保對應表結構正確）
# ──────────────────────────────────────────────────────────────────
def _self_check() -> None:
    """import 時自動驗證對應表 — fail loud 避免 Bry 拍板後改錯"""
    expected_count = 37  # 38 tags - 1 silent 留白
    actual_count = len(EMOTION_TO_FISH_MARKER)
    assert actual_count == expected_count, (
        f"EMOTION_TO_FISH_MARKER 數量錯了: 預期 {expected_count}, 實際 {actual_count}"
    )
    # 驗證 marker 都有 [方框] 邊框（marker 字串內必須含 [ 跟 ]）
    for (aid, emo), marker in EMOTION_TO_FISH_MARKER.items():
        assert marker.startswith("["), f"{aid}.{emo} marker 缺左邊框: {marker!r}"
        # 雙標記情況（如 aoi_break）每個都要有邊框
        for chunk in marker.split("]["):
            if not chunk.startswith("["):
                chunk = "[" + chunk
            if not chunk.endswith("]"):
                chunk = chunk + "]"
            assert chunk.startswith("[") and chunk.endswith("]"), (
                f"{aid}.{emo} marker chunk 邊框缺: {chunk!r}"
            )


_self_check()
