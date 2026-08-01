"""
src/llm/translate.py
Soul OS — 翻譯層 (JP rollback 後的 passthrough 殼, Bry 拍板 2026-07-22 20:59)

【現狀 (rollback 後)】
- Bry 看完日文翻譯效果「放棄了」, 整套 JP 翻譯 pipeline 砍掉
- translate_to_japanese() 保留 signature (Plan A 呼叫端: gateway.py / telegram.py)
  但 body 改成 return text (CN → CN 直通, 不再打 LLM)
- translate_to_chinese() 保留 signature (proxy.py 方向 C Stage 2 呼叫端)
  但 body 改成 return None (不再翻譯, broadcast 純原文)
- 為什麼保留空殼而不是直接刪:
  - 避免一次改太多檔 (Plan A 呼叫端 2 處 + Dir E 呼叫端 1 處都要改)
  - 給 Bry 之後想復活 JP pipeline 留路 (把 body 換回來就好)
- 之後 Bry 想完全清掉就手動刪, 我 (Mavis) 不擅自刪

【2026-07-16 Plan A 拍板背景 (已廢棄)】
原本設計: 中文 user_message 進 LLMProxy 之前, 經過 minimax 翻成日文,
避免 LLM mirror user 語言吐中文。
Plan A 跟方向 C Stage 2 整套砍掉原因: Bry 看到 Anna 反思性中文混「你好」,
Mahiru 「不知道為什麼突然想到」這類 LLM 結構性中文反彈, 覺得不可接受。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Dict, Optional

import httpx

logger = logging.getLogger("soul_os.llm.translate")

# ── 設定 ───────────────────────────────────────────────
# Bry 拍板 2026-07-20 20:12: 換 M3 via anthropic endpoint (M3 在 OpenAI 端點強制定 thinking,
# 3 種 disable 參數都失敗, anthropic endpoint thinking 預設 disabled)
# 備份: src/llm/_backup_m3_switch_20260720_201746/translate.py
MINIMAX_URL = "https://api.minimax.io/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-M3"  # M3 via anthropic endpoint (跟 LLMProxy 主 LLM 不衝突, LLMProxy 仍用 M2.7)
TRANSLATE_TIMEOUT_SECS = 20.0
TRANSLATE_MAX_TOKENS = 512  # Bry 拍板 2026-07-18 14:47, 方案 B 「絕對不要加中文」+ 加大預算避免截斷
TRANSLATE_TEMPERATURE = 0.3

# 中文 / 日文字符 Unicode range
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")                # CJK 漢字 (中 + 日 共用)
_HIRAGANA_RE = re.compile(r"[\u3040-\u309f]")           # 平假名
_KATAKANA_RE = re.compile(r"[\u30a0-\u30ff]")           # 片假名
_KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")  # 平+片假名

# ── 簡易翻譯快取 (in-process, 不持久化) ───────────────
# 設計: 短訊息 Bry 重複按同句話的機率高, 快取省 LLM 呼叫
# key: 原文 str, value: 翻譯後 str (翻譯失敗的 key 不快取, 允許重試)
_translate_cache: Dict[str, str] = {}
_CACHE_MAX_SIZE = 256


def should_translate(text: str) -> bool:
    """判斷 text 是否需要翻譯成日文。

    規則:
    - 必須有 CJK 漢字 (中 / 日共用)
    - 漢字中**中文比例高** (沒日文假名或日文假名少於 1/3)
      → 純日文漢字詞 (e.g. 「天気」) 不翻, 因為 LLM 看日文漢字也 OK
    - 純日文 / 純英文 / 純數字 / 純表情 → False
    - 長度 < 1 → False

    Args:
        text: 原始 user_message

    Returns:
        bool — True 表示要送 LLM 翻譯
    """
    if not text or not text.strip():
        return False

    cjk_chars = _CJK_RE.findall(text)
    if not cjk_chars:
        return False  # 沒漢字, 純英文 / 數字 / 表情

    kana_chars = _KANA_RE.findall(text)
    # 計算漢字中 CJK 漢字總數 (CJK_RE 重複匹配)
    total_cjk = len(cjk_chars) + len(kana_chars)

    if total_cjk < 1:
        return False

    # 日文假名比例 >= 30% → 已經是日文 (假名多), 跳過翻譯
    kana_ratio = len(kana_chars) / total_cjk
    if kana_ratio >= 0.3:
        return False

    return True


def _extract_japanese_segment(text: str) -> Optional[str]:
    """從 LLM 翻譯輸出中抽出第一個日文片段。

    處理 LLM 偶爾在翻譯後補中文翻譯或注解 (e.g. "こんにちは。\\n(你好)")
    → 我們只要第一行 / 第一段日文。

    Returns:
        第一段日文字串, 或 None (沒日文就 fallback)
    """
    if not text:
        return None
    # 先剝掉 <think>...</think> (minimax 預設 thinking mode)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if not text:
        return None

    # 抓第一行 (LLM 通常第一行就是翻譯)
    first_line = text.split("\n", 1)[0].strip()
    if not first_line:
        return None

    # 檢查有沒有日文假名 (平 or 片)
    if not _KANA_RE.search(first_line):
        return None

    # 移除尾部中文翻譯注解 (常見模式: "日文。\\n(中文)" 或 "日文 (中文)")
    first_line = re.sub(r"\s*[（(].*?[)）]\s*$", "", first_line).strip()
    if not first_line:
        return None

    return first_line


async def translate_to_japanese(
    text: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = TRANSLATE_TIMEOUT_SECS,
) -> str:
    """JP rollback (Bry 拍板 2026-07-22 20:59): passthrough, 不再翻。

    Args:
        text: 原文 (中文 user_message)
        api_key: 保留 signature 兼容呼叫端, 但不再使用
        model: 保留 signature 兼容呼叫端, 但不再使用
        timeout: 保留 signature 兼容呼叫端, 但不再使用

    Returns:
        原文字串 (CN→CN 直通, 不打 LLM)
    """
    return text


# ════════════════════════════════════════════════════════════════════
# 方向 C Stage 2: 日文 assistant 回應 → 中文翻譯 (Bry 拍板 2026-07-17)
# ════════════════════════════════════════════════════════════════════
# 結構: LLM 主輸出純日文 → 翻譯 LLM 翻成中文 → 兩者一起廣播
# 同步等 (Bry 拍板): await translate_to_chinese() 完了才 publish AGENT_SPEAK
# 語言職責完全拆開, 跨 10 角色一致 (LLM 不用同時管兩種語言)
# ════════════════════════════════════════════════════════════════════

# 獨立快取 (zh→jp 用 _translate_cache, jp→zh 用 _translate_cache_zh)
# key: 日文原文, value: 中文翻譯
_translate_cache_zh: Dict[str, str] = {}
_CACHE_ZH_MAX_SIZE = 256


def should_translate_to_chinese(text: str) -> bool:
    """判斷日文 text 是否需要翻譯成中文。

    規則 (跟 should_translate 鏡像對稱):
    - 必須有日文假名 (hiragana / katakana) — 純中文/純英文/純數字 skip
    - 假名比例 >= 10% (寬鬆點, 標點 + 漢字為主的日文也算)
      → 純英文詞 (e.g. "Hello world") 不翻, 純中文也不翻
    - 長度 < 1 → False

    Args:
        text: 日文主 LLM 輸出 (post-processed)

    Returns:
        bool — True 表示要送翻譯 LLM
    """
    if not text or not text.strip():
        return False

    kana_chars = _KANA_RE.findall(text)
    if not kana_chars:
        return False  # 沒日文假名, 純中文 / 純英文 / 純數字 / 純表情

    # 假名比例 (kana / total chars) >= 10% 才視為日文為主
    # 寬鬆點: 「嗯。好吃。」這種短中文也會通過 (假名 < 漢字), 不翻
    total_chars = len(text.strip())
    kana_ratio = len(kana_chars) / total_chars if total_chars > 0 else 0

    if kana_ratio < 0.10:
        return False

    return True


def _extract_chinese_segment(text: str) -> Optional[str]:
    """從翻譯 LLM 輸出中抽出第一個中文片段。

    處理 LLM 偶爾在中文翻譯後補日文翻譯 (e.g. "你好。\\n（こんにちは。）")
    → 我們只要第一段中文。

    Returns:
        第一段中文字串, 或 None (沒中文就 fallback)
    """
    if not text:
        return None
    # 先剝掉 think block (minimax 預設 thinking mode)
    # Bry 20:15 Option A (保底), 跟 Bry 19:15 修主 LLM 的 _strip_think_block 風格對齊:
    #   1) <think>...</think> 配對 (正常) → 剝掉
    #   2) 沒閉合 (LLM 忘了關, output 被 max_tokens 截斷) → 保留 raw, 用 fallback 抓 CJK
    # 注意: Bry 19:15 主 LLM 那邊也是用 r"<think>.*?</think>" 配對, 沒配對到保留 raw
    # 之前我 (Bry 20:15) 改成 r"<think>.*?(</think>|$)" 從 <think> 匹配到字串結尾,
    # 副作用: 如果 raw 是 "<think>[think][actual_translation]" 沒閉合,
    # 整段 (含實際翻譯) 被剝掉 → skip, 比 Bry 19:15 風格更糟
    # 改回 Bry 19:15 風格 + 加 fallback 抓 CJK 第一段
    m = re.search(r"<think>.*?</think>", text, flags=re.DOTALL)
    think_block: Optional[str] = m.group(0) if m else None
    if think_block:
        text = text[: m.start()] + text[m.end() :]
    text = text.strip()
    if not text:
        return None

    # Fallback (Bry 20:15): LLM 偶爾 think block 沒閉合, raw 開頭是 <think>[think]
    # 沒閉合, first_line 抓不到中文 → 改用「找 CJK 漢字第一段」
    first_line = text.split("\n", 1)[0].strip()
    if not _CJK_RE.search(first_line):
        m_cjk = _CJK_RE.search(text)
        if m_cjk:
            # 從第一個 CJK 漢字開始, 抓到該行結尾或 raw 結尾
            tail = text[m_cjk.start():]
            tail = tail.split("\n", 1)[0].strip()
            if _CJK_RE.search(tail):
                first_line = tail
    if not first_line:
        return None

    # ── F1+F2 雙保險 (Bry 21:00 拍板, Type A + Type B 對策) ──
    # 背景: Option A/B/C/J 5 輪迭代都失敗, 根因是 minimax M2.x 系列所有 model
    #   都強制 thinking mode (官方: "thinking cannot be disabled for M2.x models")
    #   → J no-op 是官方行為, 不可能靠 budget_tokens 治根
    #   → minimax 沒有 non-thinking variant (M2 / M2.1 / M2.5 / M2.7 / M3 / -highspeed 全是 reasoning)
    # F1: think block 內部抓 CJK (Type A 對策: think 內有漢字翻譯)
    # F2: think block 全假名無漢字 → silent fallback (Type B 對策: think 是日文翻譯)
    # 失敗模式說明:
    #   Type A raw: <think>\nThe user wants me to translate ... [中文翻譯] ...\n</think>\n[可能的日文原文]
    #     → 剝掉配對後, text 中可能沒有中文 (翻譯在 think 內)
    #     → F1 在 think_block 內抓 CJK 段
    #   Type B raw: <think>\n[整段日文翻譯]\n</think>
    #     → F1 找不到 CJK (think 內只有假名, 沒漢字)
    #     → F2 自動 fallback, broadcast 純日文 (跟 hotfix #11 silent failure 一致)
    if think_block and not _CJK_RE.search(first_line):
        # F1: think 內抓 CJK 段 (Type A 對策)
        # 用 [\u4e00-\u9fff] 開頭 + lazy 抓到 \s*</think> 結尾, 抓 group 1
        m_cjk_in_think = re.search(
            r"<think>.*?([\u4e00-\u9fff][\u4e00-\u9fff，。！？、…「」『』 ()（）0-9]*?)\s*</think>",
            think_block,
            flags=re.DOTALL,
        )
        if m_cjk_in_think:
            cjk_candidate = m_cjk_in_think.group(1).strip()
            # meta 詞過濾 (Bry 21:00 提醒: LLM think 可能夾雜中文引用)
            _META_WORDS = ("用戶", "這句話", "意思是", "這句", "原文", "用戶想要", "用戶說", "我需要")
            has_meta = any(w in cjk_candidate for w in _META_WORDS)
            # 簡體字過濾 (Bry 21:00 提醒: 繁體翻譯不應含簡體字型)
            _SIMPLIFIED = ("为", "时", "么", "们", "这", "过", "说", "对", "应", "动", "开", "会", "来", "后")
            has_simplified = any(c in cjk_candidate for c in _SIMPLIFIED)
            if not has_meta and not has_simplified and len(cjk_candidate) >= 1:
                first_line = cjk_candidate

    # 檢查有沒有 CJK 漢字 (中文必含) — F2 silent fallback
    if not _CJK_RE.search(first_line):
        # F2: silent fallback (Type B 對策 + 防 hotfix)
        # 整段都沒 CJK → broadcast 純日文, 不報錯
        return None

    # 移除尾部日文翻譯注解 (常見模式: "中文。\\n（中文・英文）" 或 "中文 (日文)")
    first_line = re.sub(r"\s*[（(].*?[)）]\s*$", "", first_line).strip()
    if not first_line:
        return None

    return first_line

    # 抓第一行 (LLM 通常第一行就是翻譯)
    first_line = text.split("\n", 1)[0].strip()
    if not first_line:
        return None

    # 檢查有沒有 CJK 漢字 (中文必含)
    if not _CJK_RE.search(first_line):
        return None

    # 移除尾部日文翻譯注解 (常見模式: "中文。\\n（中文・英文）" 或 "中文 (日文)")
    first_line = re.sub(r"\s*[（(].*?[)）]\s*$", "", first_line).strip()
    if not first_line:
        return None

    return first_line


async def translate_to_chinese(
    text: str,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = TRANSLATE_TIMEOUT_SECS,
) -> Optional[str]:
    """JP rollback (Bry 拍板 2026-07-22 20:59): passthrough, 不再翻。

    Args:
        text: 原文 (LLM 主輸出, 中文 — 因為 personas 也 rollback 回 CN)
        api_key: 保留 signature 兼容呼叫端, 但不再使用
        model: 保留 signature 兼容呼叫端, 但不再使用
        timeout: 保留 signature 兼容呼叫端, 但不再使用

    Returns:
        None (caller 填 translation=None, broadcast 純原文)
    """
    return None
