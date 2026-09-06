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

import re
from pathlib import Path
from typing import Callable, Iterable, Iterator, List, Optional

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
MARKDOWN_CHARS = set("*#[]()（）")
# 條列點（行首 "- "/"• "）
_BULLET_RE = re.compile(r"(^|\n)[-•]\s*")
# 括號動作/補充段（（）或 ()，含內容整段剝離——只刪符號會把「微笑」唸出來）
_STAGE_PAREN_RE = re.compile(r"[（(][^（(）)]*[）)]")
# 星號表情/強調段（*…*，含內容整段剝離）
_STAGE_STAR_RE = re.compile(r"\*[^*\n]*\*")


def contains_markdown_chars(text: str) -> bool:
    """審計：輸出是否含有任何守門符號。"""
    return any(ch in text for ch in MARKDOWN_CHARS)


def sanitize_voice_output(text: str) -> str:
    """守門淨化：移除動作/表情段（*…*、（…）含內容）、Markdown/括號符號與行首條列點。"""
    out = text
    for _ in range(3):  # 巢狀括號收斂
        nxt = _STAGE_STAR_RE.sub("", out)
        nxt = _STAGE_PAREN_RE.sub("", nxt)
        if nxt == out:
            break
        out = nxt
    out = "".join(ch for ch in out if ch not in MARKDOWN_CHARS)
    out = _BULLET_RE.sub(r"\1", out)
    return out.strip()


# ─────────────────────────────────────────────────────────────
# Persona 組裝
# ─────────────────────────────────────────────────────────────

def build_system_prompt(persona_file: Optional[str] = None) -> str:
    """守門規則 + Persona 摘要。persona_file 給定且可讀時以其內容為 Persona 主體。"""
    excerpt = AKANE_LAYER3_PERSONA
    if persona_file:
        try:
            text = Path(persona_file).read_text(encoding="utf-8")
            if text.strip():
                excerpt = text[:6000]  # 控制 token 量
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

def build_llm_stream(llm_cfg: dict) -> Optional[Callable[[List[dict]], Iterable[str]]]:
    """依 config `llm` 小節建立 OpenAI 相容串流通道；endpoint 缺省 → None（離線降級）。"""
    from .env_config import normalize_chat_endpoint  # 正規化：缺 /chat/completions 自動補

    endpoint = normalize_chat_endpoint((llm_cfg or {}).get("endpoint") or "")
    if not endpoint:
        return None
    model = (llm_cfg or {}).get("model") or "qwen2.5-7b-instruct"
    api_key = (llm_cfg or {}).get("api_key") or ""

    def stream(messages: List[dict]) -> Iterable[str]:
        import json

        import requests  # 懶載入

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.post(
            endpoint,
            json={"model": model, "messages": messages, "stream": True},
            headers=headers,
            timeout=60,
        )
        resp.raise_for_status()
        # SSE（text/event-stream）常無 charset：requests 預設 ISO-8859-1 會把 UTF-8 中文解成亂碼 → 強制 UTF-8
        resp.encoding = "utf-8"
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            line = line.strip()
            if line == "data: [DONE]":
                break
            if not line.startswith("data:"):
                continue
            try:
                payload = json.loads(line[len("data:"):])
            except (ValueError, TypeError):
                continue
            delta = (payload.get("choices") or [{}])[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                yield piece

    return stream


# ─────────────────────────────────────────────────────────────
# 茜語音大腦
# ─────────────────────────────────────────────────────────────

class AkaneVoiceBrain:
    """黑川茜語音專用大腦：Persona 注入 + 輸出守門 + 分句器。

    llm_stream 可注入（callable(messages) -> Iterable[str]）；None 且 config 無
    endpoint 時離線降級為內建短回覆。所有輸出必過守門，保證 0 Markdown。
    """

    def __init__(
        self,
        llm_stream: Optional[Callable[[List[dict]], Iterable[str]]] = None,
        persona: Optional[str] = None,
        persona_file: Optional[str] = None,
        config: Optional[dict] = None,
    ):
        self.config = config or {}
        self.persona = persona or build_system_prompt(persona_file)
        self.splitter = ClauseSplitter()
        self.llm_stream = llm_stream
        if self.llm_stream is None:
            self.llm_stream = build_llm_stream(self.config.get("llm") or {})

    def system_prompt(self) -> str:
        return self.persona

    def respond(self, user_text: str, history=None) -> str:
        """產生茜的回覆（整段）。輸出必過守門檢查。

        history: 選用——先前輪次訊息（role=user/assistant），依序插入 system 之後，
        讓茜承接前文（對話連貫）。缺省 None = 維持原本單回合行為。
        """
        messages = [{"role": "system", "content": self.persona}]
        messages += list(history or [])
        messages.append({"role": "user", "content": user_text})
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

    def stream_respond(self, user_text: str, history=None) -> Iterator[str]:
        """串流回應：token 邊收邊過守門，交由分句器即時切句（邊生邊播）。

        history: 選用——先前輪次訊息（role=user/assistant），依序插入 system 之後（對話連貫）。
        """
        messages = [{"role": "system", "content": self.persona}]
        messages += list(history or [])
        messages.append({"role": "user", "content": user_text})
        if self.llm_stream is None:
            yield "我在。說說看。"
            return
        for token in self.llm_stream(messages):
            cleaned = sanitize_voice_output(token)
            if cleaned:
                yield cleaned

    def _guarded(self, text: str) -> str:
        result = sanitize_voice_output(text).strip()
        return result if result else "……"


# 模組級預設實例（離線模式）
_DEFAULT_BRAIN = AkaneVoiceBrain()


def respond_as_akane(user_text: str) -> str:
    """無狀態便捷入口：以預設大腦回應（離線降級或依 config）。"""
    return _DEFAULT_BRAIN.respond(user_text)