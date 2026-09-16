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

import atexit
import re
import threading
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
# 條件式執行判定（VC-ASR-COND-1）：預設 Bypass，異常才 Run
# ─────────────────────────────────────────────────────────────

# 異常重複字元連續長度門檻（轉錄模型幻想/口吃標記，如「呵呵呵呵呵呵」）
REPEAT_RUN_LIMIT = 6


def needs_refiner(raw_text: str) -> tuple[bool, str]:
    """判定 ASR 轉錄是否需要 Refiner：回傳 (False=bypass, reason) / (True=run, reason)。

    預設 Bypass：清晰、高信心的日常完整語句直接直通 Voice Brain（每回合 1 次 LLM 呼叫）。
    任一異常條件命中才 Run（Refiner 保留為第二道防線）：
      1. too-short  — ≤ 2 個字元（極短/可能為語助詞或雜音片段）
      2. punct-only — 無中日韓字/拉丁字母/數字（全標點或符號殘渣）
      3. noise      — 命中既有雜音熔斷器候選特徵（is_noise）
      4. repeat     — 單字元連續重複 ≥ REPEAT_RUN_LIMIT（幻想/口吃標記）
      5. punct-heavy— 標點/非文字符號數 > 文字字元數（格式明顯損毀）
    空白輸入 → (False, "empty")：Refiner 無內容可淨化，呼叫端沿用既有空轉錄處理，
    （VC-ASR-COND-1 的 decision 日誌仍會印出 reason=empty）。
    """
    if not raw_text or not raw_text.strip():
        return False, "empty"
    chars = len(raw_text.strip())
    if chars <= 2:
        return True, "too-short"
    core = re.sub(r"[\W_]+", "", raw_text)  # 保留中日韓字/拉丁字母/數字
    if not core:
        # 全標點/符號殘渣：is_noise() 對空 core 恆回 True（既有熔斷語義），
        # 須先於雜音判定區分 punct-only，否則此分支不可達（VC-ASR-COND-1 修正）。
        return True, "punct-only"
    if is_noise(raw_text):
        return True, "noise"
    m = re.search(r"(.)\1{%d,}" % (REPEAT_RUN_LIMIT - 1), core)
    if m:
        return True, "repeat"
    non_core = re.findall(r"[\W_]", raw_text)
    if len(non_core) > len(core):
        return True, "punct-heavy"
    return False, "clear"


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

    # 4. 點點點 / 省略號正規化
    text = re.sub(r"[.\．]{2,}", "…", text)

    # 5. 連續口吃重複消除（例如：那個那個 → 那個；然後然後 → 然後）
    text = re.sub(r"(那個|那个|然後|然后|就是說|就是说|就是){2,}", r"\1", text)

    # 6. 句首停頓贅詞消除（例如：呃、那個、就是說...，或「茜...那個...」）
    call_match = re.match(r"^(茜|小茜|Akane|あかね)(?:[，,、…]+)?", text)
    prefix = ""
    rest = text
    if call_match:
        prefix = call_match.group(1) + "，"
        rest = text[call_match.end():]

    # 逐次剝離開頭停頓詞與感嘆詞
    hesitation_re = re.compile(r"^[，,、…\s]*(?:[嗯呃啊哦喔唉呼哼]|那個|那个|就是說|就是说)+(?:[，,、…\s]*|$)")
    while True:
        m = hesitation_re.match(rest)
        if not m or not m.group(0):
            break
        rest = rest[m.end():]

    # 去除句中夾在標點間的純語氣詞（如「，呃，」「，嗯，」）
    rest = re.sub(r"([，,、…])[嗯呃啊唉呼哼]+([，,、…])", r"\1", rest)

    # 收斂重複標點與開頭殘留標點
    rest = re.sub(r"…{2,}", "…", rest)
    rest = re.sub(r"[，,]{2,}", "，", rest)
    rest = re.sub(r"[。．]{2,}", "。", rest)
    rest = re.sub(r"^[\s，,、。．！？!?~～…]+", "", rest)

    if not rest:
        if prefix:
            text = prefix.rstrip("，")
        else:
            return None
    else:
        text = prefix + rest if prefix else rest

    if is_noise(text):
        return None

    # 7. 稱呼後補逗號：茜/小茜/Akane/あかね 直接接中文字時插入「，」
    text = re.sub(r"^(茜|小茜|Akane|あかね)(?=[一-龥])", r"\1，", text)

    # 8. 終點標點
    text = re.sub(r"[，,、~～…]+$", "", text).rstrip()
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


# ─────────────────────────────────────────────────────────────
# VC-ASR-REUSE：LLM 呼叫的 HTTP 連線池重用
# ─────────────────────────────────────────────────────────────

# 連線池參數。數值沿用同套件既有慣例（clients/voice_companion/akane_voice_brain.py
# 的 VC_HTTP_POOL_CONNECTIONS=4 / VC_HTTP_POOL_MAXSIZE=8），此處以同名語意的模組
# 常數各自宣告——刻意**不**跨模組 import 那些常數，避免兩個模組互相耦合。
ASR_HTTP_POOL_CONNECTIONS = 4
ASR_HTTP_POOL_MAXSIZE = 8

# 行程級共用 Session（lazy singleton）。
# 為什麼是模組層級而非 per-instance：build_llm_call() 的呼叫時機（是否
# per-request）不由本模組決定；只有行程級 singleton 才能**保證**跨回合重用
# 同一條連線，免去每回合一次 TLS 握手（約 18–20ms）。Session 預設帶
# keep-alive，故不手動塞連線相關 header。
_LLM_SESSION: Optional[object] = None

# FUP-1 Part A：lazy 建立必須由 threading.Lock 保護。
# 比照姊妹模組 akane_voice_brain.py:320-328（明文：「LLM 呼叫跑在 asyncio.to_thread
# 內 ⇒ Session 必須可跨執行緒共享」，以 threading.Lock 保護 lazy 建立）——本模組的
# 真實呼叫路徑亦然：web_server.py:557 以 `await asyncio.to_thread(...refine_speech_text,
# text)` 呼叫，精煉跑在 to_thread 的工作執行緒。無鎖時 2 個執行緒可能各建一個 Session，
# 其中一個成為孤兒（close_llm_session() 只關存活的那個 ⇒ 孤兒永不關閉）。
_LLM_SESSION_LOCK = threading.Lock()


def _build_llm_session() -> "requests.Session":
    """建立 ASR Refiner 專用 HTTP Session：連線池 + keep-alive。"""
    import requests  # 懶載入：測試環境不需安裝（維持既有契約，不得移到模組頂層）
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    adapter = HTTPAdapter(
        pool_connections=ASR_HTTP_POOL_CONNECTIONS,
        pool_maxsize=ASR_HTTP_POOL_MAXSIZE,
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _get_llm_session() -> "requests.Session":
    """取得行程級共用 Session（首次呼叫才建立，之後跨回合重用同一物件）。

    建構失敗（例如環境沒裝 requests）時讓例外照原語意往上拋——**不**靜默吞成
    None，否則呼叫端會拿到不明錯誤；此時 `_LLM_SESSION` 維持 None，下次重試。

    鎖內做「檢查 → 建構 → 賦值」：兩個執行緒同時首次呼叫時只會建出一個物件
    （無鎖版本會各建一個，其中一個成孤兒）。鎖內只建 Session 物件、**無網路 I/O**。
    """
    global _LLM_SESSION
    with _LLM_SESSION_LOCK:
        if _LLM_SESSION is None:
            _LLM_SESSION = _build_llm_session()
        return _LLM_SESSION


def close_llm_session() -> None:
    """關閉並丟棄行程級共用 Session（冪等）。

    重複呼叫安全；關閉時的例外一律吞掉；呼叫後把 `_LLM_SESSION` 設回 None，
    使得下次 `_get_llm_session()` 會重建——**不得**重用已關閉的 session。

    與 lazy 建立共用同一把鎖做狀態切換（擷取＋歸零在鎖內），避免與 `_get_llm_session()`
    交錯而把別的执行緒剛建好的 Session 丟掉；實際 `close()` 在鎖外執行（不在鎖內做 I/O）。
    """
    global _LLM_SESSION
    with _LLM_SESSION_LOCK:
        session = _LLM_SESSION
        _LLM_SESSION = None
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


# 冪等：模組只載入一次，故本註冊只發生一次；close_llm_session() 本身冪等且吞例外，
# 直譯器結束時（含未建立過 session 的情況）不會拋錯。
atexit.register(close_llm_session)


def build_llm_call(llm_cfg: dict) -> Callable[[str], Optional[str]]:
    """依 config `llm` 小節建立 OpenAI 相容 chat/completions 呼叫（requests 懶載入）。"""
    from .env_config import normalize_chat_endpoint  # 正規化：缺 /chat/completions 自動補

    endpoint = normalize_chat_endpoint((llm_cfg or {}).get("endpoint") or "")
    model = (llm_cfg or {}).get("model") or "qwen2.5-7b-instruct"
    api_key = (llm_cfg or {}).get("api_key") or ""

    def call(prompt: str) -> Optional[str]:
        session = _get_llm_session()  # VC-ASR-REUSE：行程級共用連線池（跨回合重用）

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        resp = session.post(
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