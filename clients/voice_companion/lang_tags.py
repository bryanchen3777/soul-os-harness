"""lang_tags.py — drama-3 語言標籤注入（VC 語音輸出層，2026-10-24 新增）。

**為什麼需要**：drama-3 / s2.1-pro 在句中出現外文時，都可能把整句「漂」成該語言。
本模組依實測資料（2026-10-24：3 音色 × 6 案例 × 3 次抽樣 × 2 模型 = 108 次 TTS）定策略：

| 案例                    | drama-3 漂移率        | s2.1-pro 漂移率      | 標籤有效？ |
|-------------------------|----------------------|----------------------|-----------|
| 純中文（不加標）        | 茜 0/3・麻衣 1/3・雷姆 0/3 | 茜 0/3・麻衣 0/3・雷姆 1/3 | — |
| 純中文（`<chinese>`）   | 茜 0/3・麻衣 0/3・雷姆 0/3 | 三者皆 0/3           | ✅ 消除麻衣偶發漂移 |
| 中英混（不加標）        | 茜 2/3・麻衣 0/3・雷姆 0/3 | 茜 0/3・麻衣 2/3・雷姆 1/3 | — |
| 中英混（交替標）        | 茜 0/3・麻衣 1/3・雷姆 0/3 | 茜 0/3・麻衣 3/3・雷姆 0/3 | ✅ 多數變好 |
| 中日混（任何標法）      | 三者皆 3/3           | 三者皆 3/3           | ❌ 無效 |

**誠實的限制（必須一起知道）**：
1. 語言標籤是 drama-3 的 **experimental** 功能，文件明載「模型可能在多語句中自行切換語言」。
2. **句中含日文時，兩種模型都會整句漂成日語** ⇒ 語言標籤解決不了；
   這是「音色本身帶日語口音 ＋ 句中有日文」的固有現象，**與本次模型切換無關**。
   唯一緩解是子句隔離：VC 本來就逐子句送 TTS ⇒ 漂移只影響該子句，不會汙染整段回覆。
3. 本模組**不巢狀**：基底語言與外文片段交替包標籤（`<chinese>…</chinese><english>…</english>`）。
   drama-3 文件未定義巢狀語言標籤的行為 ⇒ 不產生巢狀。
4. 文字若已含 drama-3 標記（方向指示 `[…]`／配對標籤 `<emphasis>`／語言標籤），
   本模組**原樣返回**（不疊加語言標籤），避免產生未定義的巢狀。

標籤白名單只收 drama-3 文件明確示範過的語言：chinese / english / japanese / korean。
"""

from __future__ import annotations

import re
from typing import List, Tuple

# 只有 drama-3 支援語言標籤；s2.1-pro 等模型會把 <english> 當一般文字唸出來。
MODELS_WITH_LANGUAGE_TAGS = frozenset({"drama-3"})

# 語言 → drama-3 標籤名
_LANG_TAGS = {
    "chinese": "chinese",
    "english": "english",
    "japanese": "japanese",
    "korean": "korean",
}

# 假名（平假名／片假名／半形假名）⇒ 日文
_JA_RE = re.compile(r"[\u3040-\u309F\u30A0-\u30FF\uFF66-\uFF9F]+")
# 韓文（諺文＋字母）⇒ 韓文
_KO_RE = re.compile(r"[\uAC00-\uD7AF\u1100-\u11FF\u3130-\u318F]+")
# 拉丁字母開頭的詞（可含版本號/縮寫的內部字元）：API、latency、deepseek-v4.1-flash 算英文；
# 純數字（80 毫秒）不算。
_EN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._'\u2019-]*")

# 已存在 drama-3 標記時不疊加（方向指示／配對標籤／語言標籤／說話者標籤）
_HAS_DRAMA3_MARKUP_RE = re.compile(
    r"\[[^\[\]]{1,80}\]"          # [方向指示]
    r"|</?[a-z_]+>"               # <emphasis>…</emphasis> 之類配對標籤
    r"|<\|[^|]*\|>"               # <|speaker:0|> / <|phoneme_*|>
)


def supports_language_tags(model: str) -> bool:
    """該模型是否支援語言標籤（drama-3）。"""
    return (model or "") in MODELS_WITH_LANGUAGE_TAGS


def _has_drama3_markup(text: str) -> bool:
    return bool(_HAS_DRAMA3_MARKUP_RE.search(text))


def _foreign_spans(text: str) -> List[Tuple[int, int, str]]:
    """找出外文片段的 (start, end, lang)。重疊者取先到者。"""
    spans: List[Tuple[int, int, str]] = []
    for rx, lang in ((_EN_RE, "english"), (_JA_RE, "japanese"), (_KO_RE, "korean")):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end(), lang))
    spans.sort(key=lambda s: (s[0], -s[1]))
    merged: List[Tuple[int, int, str]] = []
    for s, e, lang in spans:
        if merged and s < merged[-1][1]:
            continue  # 與前一段重疊（極少見）⇒ 併入前段
        merged.append((s, e, lang))
    return merged


def apply_language_tags(text: str, *, base: str = "chinese") -> str:
    """把基底語言片段與外文片段「交替」包上 drama-3 語言標籤。

    - 純中文句子 ⇒ `<chinese>整句</chinese>`（實測可消除麻衣音色的偶發語言漂移）
    - 中英混 ⇒ `<chinese>我先把</chinese><english>API</english><chinese>的…</chinese>`
    - 已含 drama-3 標記 ⇒ 原樣返回（不疊加、不巢狀）
    """
    if not text or not text.strip():
        return text
    if _has_drama3_markup(text):
        return text
    base_tag = _LANG_TAGS.get(base)
    if base_tag is None:
        return text

    spans = _foreign_spans(text)
    if not spans:
        return f"<{base_tag}>{text}</{base_tag}>"

    parts: List[Tuple[str, str]] = []
    pos = 0
    for s, e, lang in spans:
        if s > pos:
            parts.append((base, text[pos:s]))
        parts.append((lang, text[s:e]))
        pos = e
    if pos < len(text):
        parts.append((base, text[pos:]))

    # 合併相鄰同語言片段
    grouped: List[Tuple[str, str]] = []
    for lang, chunk in parts:
        if not chunk:
            continue
        if grouped and grouped[-1][0] == lang:
            grouped[-1] = (lang, grouped[-1][1] + chunk)
        else:
            grouped.append((lang, chunk))

    out: List[str] = []
    for lang, chunk in grouped:
        tag = _LANG_TAGS.get(lang)
        if tag is None:
            out.append(chunk)
        else:
            out.append(f"<{tag}>{chunk}</{tag}>")
    return "".join(out)
