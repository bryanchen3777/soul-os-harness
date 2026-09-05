"""
asr_refiner.py — ASR 語意淨化層（VC-1 模組 1）。

責任：接收原始 STT 文本 →
  1. 同音錯字校準（千/欠/西 → 茜；排成 → 排程）
  2. 口吃/重複贅字過濾（呃、那個、就是說、然後 …）
  3. 標點還原，重現真實語意
  4. 【雜音熔斷】純雜音（啊/嗯/呼/嘆氣/咳嗽）→ None（觸發 DROP，不打擾茜）

設計：LLM 通道可注入（llm_call），未注入時走內建確定性規則（極速模式），
因此驗收測試完全離線可跑，0 依賴重型套件。LLM 通道僅依 config.json 的
`llm` 小節建置（clients/ 自用，0 修改 src/）。
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────
# 內建確定性規則表
# ─────────────────────────────────────────────────────────────

# 多字詞同音錯字（先於單字規則比對，避免誤拆）
HOMOPHONE_PHRASE_FIXES = [
    ("拍成", "排程"),
    ("排成", "排程"),
]

# 茜 的稱呼化同音字（「小X」→「小茜」）
AKANE_ALIAS_PAIR_FIXES = [
    ("小欠", "小茜"),
    ("小千", "小茜"),
    ("小西", "小茜"),
]

# 單獨出現的茜同音字（前後不接中文字時才修正，避免誤傷「東西」「千字文」等）
AKANE_ALIAS_CHAR_RE = re.compile(r"(?<![一-龥])[欠千西籤签](?![一-龥])")

# 口吃/重複贅字（語音對話高頻 filler）
FILLER_TOKENS = [
    "就是說", "就是说", "然後", "然后", "那個", "那个",
    "就是", "這樣", "这样", "嗯", "呃", "啊", "哦", "喔", "唉",
]

# 無意義感嘆詞 / 嘆氣 / 咳嗽 / 背景雜音音節
NOISE_SYLLABLES = set("啊嗯呼呃唉哦喔哈嘿咳哼咦唔嘛吧呢呐啦哟欸哎")

# 終點標點（不缺句號時保留）
TERMINAL_PUNCT = "。！？…"


# ─────────────────────────────────────────────────────────────
# 雜音熔斷
# ─────────────────────────────────────────────────────────────

def is_noise(raw_text: str) -> bool:
    """判斷輸入是否為純環境雜音（僅感嘆詞/嘆氣/無意義音節）。"""
    if not raw_text or not raw_text.strip():
        return True
    core = re.sub(r"[\W_]+", "", raw_text)  # 去空白/標點/點點點，保留中日韓字與拉丁字母
    if not core:
        return True
    return all(ch in NOISE_SYLLABLES for ch in core)


# ─────────────────────────────────────────────────────────────
# 內建確定性修復（極速模式，離線可用）
# ─────────────────────────────────────────────────────────────

def local_refine(raw_text: str) -> Optional[str]:
    """規則式淨化：錯字 → 贅詞 → 標點。雜音回傳 None。"""
    if is_noise(raw_text):
        return None

    text = re.sub(r"\s+", "", raw_text)

    # 1. 多字詞同音校準
    for wrong, right in HOMOPHONE_PHRASE_FIXES:
        text = text.replace(wrong, right)

    # 2. 稱呼化同音字（小欠/小千/小西 → 小茜）
    for wrong, right in AKANE_ALIAS_PAIR_FIXES:
        text = text.replace(wrong, right)

    # 3. 單獨同音字（千/欠/西/籤/签 → 茜）
    text = AKANE_ALIAS_CHAR_RE.sub("茜", text)

    # 4. 口吃/贅詞移除
    for filler in FILLER_TOKENS:
        text = text.replace(filler, "")

    # 5. 省略號與重複標點收斂
    text = re.sub(r"[.\．]{2,}", "", text)          # "..." → ""
    text = re.sub(r"…{2,}", "…", text)              # "……" → "…"
    text = re.sub(r"[，,]{2,}", "，", text)
    text = re.sub(r"[。．]{2,}", "。", text)
    text = re.sub(r"^[\s，,、。．！？!?~～…]+", "", text)  # 去開頭殘留標點

    if not text:
        return None

    # 6. 稱呼後補逗號：茜/小茜/Akane/あかね 直接接中文字時插入「，」
    text = re.sub(r"^(茜|小茜|Akane|あかね)(?=[一-龥])", r"\1，", text)

    # 7. 終點標點
    text = re.sub(r"[，,、~～]+$", "", text).rstrip()
    if text and text[-1] not in TERMINAL_PUNCT:
        text += "。"

    return text


# ─────────────────────────────────────────────────────────────
# LLM 通道（生產選配；測試一律注入 Mock 或不注入）
# ─────────────────────────────────────────────────────────────

def build_refine_prompt(raw_stt_text: str) -> str:
    """組裝修復器 Prompt（工單 §3 模組 1 模板）。"""
    return (
        "你是一個極速語音識別修復器。使用者叫 Bryan，正在與 AI 伴侶黑川茜（Akane）進行即時語音對話。\n"
        "【任務】：\n"
        "1. 修正語音轉寫常見的同音錯字（例如將「千/欠/西」修正為「茜/小茜/Akane」，將「排成」修正為「排程」）。\n"
        "2. 去除口吃、重複贅字（如「呃...那個...就是說...」）。\n"
        "3. 補充正確標點符號，還原說話者的真實語意。\n"
        "4. 【雜音熔斷】：若輸入僅為無意義感嘆詞、嘆氣、咳嗽、背景雜音片段（如「啊」、「嗯」、「呼」），直接輸出 EMPTY。\n"
        "5. 嚴禁替使用者回答，僅輸出修復後的純文字。\n"
        f"\n輸入：{raw_stt_text}\n修復："
    )


def build_llm_call(llm_cfg: dict) -> Callable[[str], Optional[str]]:
    """依 config `llm` 小節建立 OpenAI 相容 chat/completions 呼叫（requests 懶載入）。"""
    endpoint = (llm_cfg or {}).get("endpoint") or ""
    model = (llm_cfg or {}).get("model") or "qwen2.5-7b-instruct"
    api_key = (llm_cfg or {}).get("api_key") or ""

    def call(prompt: str) -> Optional[str]:
        import requests  # 懶載入：測試環境不需安裝

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = requests.post(
            endpoint,
            json={"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    return call


# ─────────────────────────────────────────────────────────────
# 淨化器主體
# ─────────────────────────────────────────────────────────────

class AsrRefiner:
    """ASR 語意淨化器。

    llm_call 注入 LLM 通道（可 Mock）；為 None 時走內建確定性規則。
    """

    def __init__(self, llm_call: Optional[Callable[[str], Optional[str]]] = None, config: Optional[dict] = None):
        self.config = config or {}
        self.llm_call = llm_call
        if self.llm_call is None:
            llm_cfg = self.config.get("llm") or {}
            if llm_cfg.get("endpoint"):
                self.llm_call = build_llm_call(llm_cfg)

    @classmethod
    def from_config(cls, config: dict) -> "AsrRefiner":
        return cls(config=config)

    def refine_speech_text(self, raw_text: str) -> Optional[str]:
        """回傳修復後的純淨文字；若為雜音或空白則回傳 None（觸發 DROP）。"""
        if is_noise(raw_text):
            return None
        if self.llm_call is not None:
            try:
                result = (self.llm_call(build_refine_prompt(raw_text)) or "").strip()
            except Exception:
                result = ""
            if result:
                if result.upper() == "EMPTY":  # 模型判定雜音 → 熔斷
                    return None
                return result
        return local_refine(raw_text)


# 模組級預設實例：離線確定性模式（極速），供無狀態呼叫與驗收測試使用
_DEFAULT_REFINER = AsrRefiner(llm_call=None)


def refine_speech_text(raw_text: str) -> Optional[str]:
    """回傳修復後的純淨文字；若為雜音或空白則回傳 None（觸發 DROP）。"""
    return _DEFAULT_REFINER.refine_speech_text(raw_text)