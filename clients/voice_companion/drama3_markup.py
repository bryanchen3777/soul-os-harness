"""drama3_markup.py — drama-3 表演標記白名單與守門工具（Owner 2026-10-24 裁示）。

drama-3 可用的「表演標記」（官方文件 drama-3 頁）：

- **方向指示／聲音**（方括號）：``[shaky breath]``、``[small laugh]``、
  ``[像是在强忍着眼泪，却努力笑着]``（方向可用任何語言書寫）。
- **配對標籤**：``<whisper>`` ``<emphasis>`` ``<soft>`` ``<fast>`` ``<slow>``
  ``<stress>`` ``<forceful>``（只改被框住那幾個字的演出方式）。
- **語言標籤**：``<chinese>`` ``<english>`` ``<japanese>`` ``<korean>``
  ``<spanish>`` ``<german>`` ``<french>``（句子中切換語言；experimental）。

**為什麼需要這個模組**：VC 原本的語音守門會把 ``[]`` 與 ``<…>`` 當 Markdown／XML 雜訊
整段剝除（連 ``<emphasis>真的</emphasis>`` 裡的「真的」都被吃掉）⇒ drama-3 的表演能力
完全用不上。本模組只做四件事：

1. **白名單判定**：哪些標記可以留到 TTS（其餘照舊剝除）。
2. **保護／還原**（靜態路徑）：先把允許的標記與方向指示換成惰性佔位符，
   讓既有的剝除管線不去碰它們，剝完再還原。
3. **方向指示合法性**：避免把 Markdown（``[1]``、``[註]``）誤當成表演方向。
4. **數量上限**：避免 LLM 標籤上癮（tag soup）。

⚠️ 語言標籤由 `lang_tags.py` 自動注入（依實測策略），**LLM 不需要自己寫語言標籤**；
本模組保留語言標籤在白名單裡，只是為了「萬一 LLM 寫了也不會被整段吃掉」。
"""

from __future__ import annotations

import re
from typing import List, Tuple

try:  # pragma: no cover - 導入路徑相容
    from .model_registry import MODELS_WITH_DRAMA3_MARKUP
except ImportError:  # pragma: no cover
    from model_registry import MODELS_WITH_DRAMA3_MARKUP

# drama-3 配對標籤（官方文件列舉）
DRAMA3_PAIRED_TAGS = frozenset({
    "whisper", "emphasis", "soft", "fast", "slow", "stress", "forceful",
})

# drama-3 語言標籤（官方文件示範過的語言）
DRAMA3_LANG_TAGS = frozenset({
    "chinese", "english", "japanese", "korean", "spanish", "german", "french",
})

ALLOWED_TAGS = DRAMA3_PAIRED_TAGS | DRAMA3_LANG_TAGS

# 支援表演標記的模型（只有它會「演出」標記；其他模型會把標記唸出來）
# 白名單的單一事實來源＝model_registry.py（勿在此另立一份）。
MODELS_WITH_DRAMA3_MARKUP = MODELS_WITH_DRAMA3_MARKUP

# 整句最多幾個方向指示（LLM 濫用 ⇒ tag soup；1 個是保守值）
MAX_DIRECTIONS_PER_REPLY = 1

# 方向指示長度上限（字元）
MAX_DIRECTION_CHARS = 40

# 方向指示至少要幾個字（1 字太容易是 Markdown，例如 [1]）
MIN_DIRECTION_CHARS = 2

_CJK_RE = re.compile(r"[\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_ALLOWED_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9_-]*)\s*/?>")


def supports_drama3_markup(model: str) -> bool:
    """該模型是否會演出 drama-3 表演標記（否則標記會被唸出來 ⇒ 一律剝除）。"""
    return (model or "") in MODELS_WITH_DRAMA3_MARKUP


def is_allowed_tag(name: str) -> bool:
    """標籤名是否在白名單（不含 <> 與斜線）。"""
    return (name or "").lower() in ALLOWED_TAGS


def is_valid_direction(content: str) -> bool:
    """方括號內容是否可作為 drama-3 方向指示。

    判定（避免 Markdown 誤判）：
    - 長度 2..MAX_DIRECTION_CHARS
    - 不得含 ``[`` 或 ``]``
    - 不得是純數字／純標點（那是 Markdown 引用或清單編號）
    - 必須含至少 1 個中日韓漢字，或含英文字母（方向可用任何語言書寫）
    """
    if not content:
        return False
    if "[" in content or "]" in content:
        return False
    if not (MIN_DIRECTION_CHARS <= len(content) <= MAX_DIRECTION_CHARS):
        return False
    if content.strip(" \t\r\n.。,，、：:；;！!？?（）()【】…-—_/\\\"'“”‘’") == "":
        return False
    if content.strip().isdigit():
        return False
    if _CJK_RE.search(content):
        return True
    return bool(_LATIN_RE.search(content))


def find_directions(text: str) -> List[Tuple[int, int, str]]:
    """找出所有方括號片段 (start, end, content)。"""
    out: List[Tuple[int, int, str]] = []
    for m in re.finditer(r"\[([^\[\]]*)\]", text):
        out.append((m.start(), m.end(), m.group(1)))
    return out


def _mask(i: int) -> str:
    return f"\x00D3:{i}\x00"


def protect_drama3_markup(text: str) -> Tuple[str, List[str]]:
    """把允許的表演標記與合法的方向指示換成惰性佔位符。

    回傳 (masked_text, segments)；segments[i] 是第 i 個佔位符對應的原文。
    """
    if not text:
        return text, []

    segments: List[str] = []
    out = text

    # ① 配對／語言標籤（只換標籤本身，內文照常處理）
    def _tag_sub(m: re.Match) -> str:
        if not is_allowed_tag(m.group(1)):
            return m.group(0)
        segments.append(m.group(0))
        return _mask(len(segments) - 1)

    out = _ALLOWED_TAG_RE.sub(_tag_sub, out)

    # ② 方向指示（方括號整段）：最多保護 MAX_DIRECTIONS_PER_REPLY 個；
    #    超過上限者**整段移除**（與串流路徑「超過上限 ⇒ 內容被抑制」一致）。
    protected_dirs = 0

    def _dir_sub(m: re.Match) -> str:
        nonlocal protected_dirs
        content = m.group(1)
        if not is_valid_direction(content):
            return m.group(0)
        if protected_dirs >= MAX_DIRECTIONS_PER_REPLY:
            return ""
        protected_dirs += 1
        segments.append(m.group(0))
        return _mask(len(segments) - 1)

    out = re.sub(r"\[([^\[\]]*)\]", _dir_sub, out)
    return out, segments


def restore_drama3_markup(masked: str, segments: List[str]) -> str:
    """把 protect 產生的佔位符還原回原文。"""
    if not segments:
        return masked
    for i, seg in enumerate(segments):
        masked = masked.replace(_mask(i), seg)
    return masked


def markup_spans(text: str) -> set:
    """回傳被 drama-3 標記佔用的字元索引集合（切句時**不得**切在裡面）。

    方向指示 `[像是在强忍着眼泪，却努力笑着]` 內含「，」，配對標籤 `<whisper>晚安。</whisper>`
    內含「。」，若在標記內切句 ⇒ 會送出未閉合的標籤（引擎會把標籤文字唸出來或整段丟失）。
    ⇒ **整個元素**（開標籤＋內容＋閉標籤）都要保護，不只是標籤本身。
    未閉合的標記一律視為佔用到句尾（fail-safe：寧可整句不切）。
    """
    spans = set()
    n = len(text)

    # ① 方括號方向指示（自身成對）
    idx = 0
    while True:
        start = text.find("[", idx)
        if start < 0:
            break
        end = text.find("]", start + 1)
        if end < 0:
            spans.update(range(start, n))
            break
        spans.update(range(start, end + 1))
        idx = end + 1

    # ② 成對標籤 <name>…</name>（保護整個元素）
    idx = 0
    while True:
        start = text.find("<", idx)
        if start < 0:
            break
        m = re.match(r"<([A-Za-z][A-Za-z0-9_-]*)\s*>", text[start:])
        if not m:
            idx = start + 1
            continue
        name = m.group(1)
        open_end = start + m.end()
        close = re.search(r"</\s*" + re.escape(name) + r"\s*>", text[open_end:])
        if not close:
            spans.update(range(start, n))
            break
        close_end = open_end + close.end()
        spans.update(range(start, close_end))
        idx = close_end

    return spans


def strip_drama3_markup(text: str) -> str:
    """移除 drama-3 表演標記（顯示／記憶／審計用）。

    - 配對／語言標籤：**只拿掉標籤本身**，保留內文（`<whisper>真的</whisper>` → `真的`）。
    - 方向指示：**整段移除**（它是演出指示，不是內容）。
    - 不合法的方括號（Markdown 如 `[1]`）**不動**（審計時必須看得出來）。
    """
    if not text:
        return text

    def _tag_sub(m: re.Match) -> str:
        return "" if is_allowed_tag(m.group(1)) else m.group(0)

    out = _ALLOWED_TAG_RE.sub(_tag_sub, text)

    def _dir_sub(m: re.Match) -> str:
        content = m.group(1)
        if is_valid_direction(content):
            return ""
        return m.group(0)

    out = re.sub(r"\[([^\[\]]*)\]", _dir_sub, out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()
