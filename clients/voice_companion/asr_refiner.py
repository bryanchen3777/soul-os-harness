"""
asr_refiner.py — ASR 語意淨化層（VC-1 模組 1）。

責任：接收原始 STT 文本 →
  1. 同音錯字校準（排成 → 排程；角色別名 依當前 companion 決定）
  2. 口吃/重複贅字過濾（呃、那個、就是說、然後 …）
  3. 標點還原，重現真實語意
  4. 【雜音熔斷】純雜音（啊/嗯/呼/嘆氣/咳嗽、非 CJK/ASCII 幻覺）→ None（觸發 DROP）

設計：LLM 通道可注入（llm_call），未注入時走內建確定性規則（極速模式），
因此驗收測試完全離線可跑，0 依賴重型套件。LLM 通道僅依 config.json 的
`llm` 小節建置（clients/ 自用，0 修改 src/）。

── VC-REFINER-2（2026-10-16）：角色感知（companion-scoped）──
本模組原本**完全不知道**自己在跟誰說話：規則表寫死小茜（`AKANE_ALIAS_*`），
而 8765 小茜 / 8766 麻衣 / 8767 雷姆 **三個實例共用同一份 module-level 常量**。
真實語料的後果（皆見 clients/voice_companion/logs/）：
  - `build_refine_prompt` 對**雷姆實例**也下達「把千/欠/西改成茜」的指令，
    實測把 `雷姆` 改寫成 `蕾姆`（rem:3238）與 `茜`（rem:4227/5254）——**跨角色污染**。
  - 麻衣被 ASR 聽成 `蚂蚁` 連爆 3 次（mai:3917/4478/4635），規則表裡卻沒有。
現況：`companion=None` ⇒ **只跑通用規則，不跑任何角色專屬規則**（安全預設，
絕不跨角色亂改）。角色專屬規則一律從呼叫端傳入的 `cfg["companion"]` 推導，
**不從 profiles/*.json 查檔**（小茜沒有 profiles/akane.json——她的 config.json
就是預設 config，web_server.py CONFIG_PATH）。
"""

from __future__ import annotations

import atexit
import re
import threading
from typing import Callable, Optional

# ─────────────────────────────────────────────────────────────
# 內建確定性規則表
# ─────────────────────────────────────────────────────────────

# 【通用】多字詞同音錯字（先於單字規則比對，避免誤拆）
# 與角色無關，任何實例都適用。
# ⚠️ 一律以**簡體**書寫：2026-10-16 唯讀考古實測（145 個真實語音回合）顯示
#    Fish ASR 輸出 **100% 簡體**（含簡體專屬字者 60 回合、繁體專屬字 0 回合），
#    用繁體書寫的規則在 ASR 路徑上是死碼。`排成/拍成` 之所以有效只是因為它們簡繁同形。
HOMOPHONE_PHRASE_FIXES = [
    ("拍成", "排程"),
    ("排成", "排程"),
    # rem:29217 / rem:29374 實測（Re:Zero 專有名詞）。兩種錯形都不是合法中文詞，
    # 因此放通用表而非雷姆專屬表——任何角色聊到它都該被修。
    ("仙特塔", "贤者塔"),
    ("仙者塔", "贤者塔"),
]

# ─────────────────────────────────────────────────────────────
# 【VC-REFINER-2】角色專屬規則表（companion-scoped）
# ─────────────────────────────────────────────────────────────
# 每個角色一張表；**只**在該 companion.id 的實例生效。
#   pairs — 依序執行的字面替換（同音專屬）
#   regex — 需上下文守衛的替換 (regex, replacement)
#   char_re / char_to — 單字同音（保留 AKANE 的前後不接中文字守衛）
#   names — 句首稱呼（逗號插入／剝停頓詞用），取代原本硬寫的 ^(茜|小茜|Akane|あかね)
#
# ⚠️ 角色表裡**不得**出現「把另一個角色的名字改掉」的規則：那正是本票要消滅的病。
_AGENT_HOMOPHONE_RULES = {
    # 8765 黒川あかね / 黑川茜（clients/voice_companion/config.json，預設 config）
    "agent_akane": {
        "pairs": [
            ("小欠", "小茜"),
            ("小千", "小茜"),
            ("小西", "小茜"),
        ],
        "regex": [],
        # 單獨出現的茜同音字（前後不接中文字時才修正，避免誤傷「東西」「千字文」等）
        "char_re": re.compile(r"(?<![一-龥])[欠千西籤签](?![一-龥])"),
        "char_to": "茜",
        "names": ["茜", "小茜", "Akane", "akane", "あかね"],
    },
    # 8766 櫻島麻衣（profiles/mai.json）
    "agent_mai": {
        "pairs": [],
        "regex": [
            # mai:3917/4478/4635 實測：麻衣被聽成「蚂蚁」，3 分鐘內連爆 3 次。
            # 守衛（Owner 2026-10-16 裁定）：只要前面不是**昆蟲量詞**就當成呼喚。
            #   「我的蚂蚁老婆呢」  → 前字「的」→ 修 ✓
            #   「是蚂蚁，不是蚂蚁」  → 句首      → 修 ✓
            #   「地上有一隻螞蟻」    → 前字「隻」→ **不修** ✓
            #   「好幾隻螞蟻爬過去」  → 前字「隻」→ **不修** ✓
            (r"(?<![一兩两幾几這这那隻只群堆])[蚂螞][蚁蟻]", "麻衣"),
            # mai:4821 實測：櫻島麻衣被聽成「桜島舞」
            (r"[桜樱][島岛]舞", "麻衣"),
        ],
        "char_re": None,
        "char_to": None,
        "names": ["麻衣", "學姐", "學姊", "まい", "Mai", "mai"],
    },
    # 8767 雷姆（profiles/rem.json）
    "agent_rem": {
        "pairs": [
            # 🔴 順序敏感：`裴雷姆`（錯在動詞「陪」）必須排在任何雷姆相關規則之前，
            #    否則會被後面的規則二次處理。rem:8524 實測。
            ("裴雷姆", "陪雷姆"),
            # 🔴 正字保護：2026-09-11/12 舊 LLM refiner 曾把 ASR 輸出**正確的**「雷姆」
            #    主動改寫成「蕾姆」／「茜」（rem:3238 / rem:4162）。這兩條是把那次
            #    退化改回去的**反向守衛**，不是新發明的同音模式——雷姆 45/45 全部正確。
            ("蕾姆", "雷姆"),
            ("雷母", "雷姆"),
            ("类姆", "雷姆"),
            ("類姆", "雷姆"),
        ],
        "regex": [],
        "char_re": None,
        "char_to": None,
        "names": ["雷姆", "Rem", "rem"],
    },
}

# 未在表中的 companion.id（含 None）⇒ 只跑通用規則（安全預設）


def _agent_rules(companion: Optional[dict]) -> Optional[dict]:
    """由 `cfg["companion"]` 取出當前角色的專屬規則表；查不到回 None（不套用任何角色規則）。

    只認 `companion["id"]`。**刻意不去讀 profiles/*.json**：小茜沒有
    profiles/akane.json（她的 clients/voice_companion/config.json 本身就是預設
    config，見 web_server.py CONFIG_PATH），任何路徑式查表都會安靜地漏掉她。
    """
    if not isinstance(companion, dict):
        return None
    agent_id = companion.get("id")
    if not isinstance(agent_id, str):
        return None
    return _AGENT_HOMOPHONE_RULES.get(agent_id.strip().lower())


def _agent_names(companion: Optional[dict]) -> list:
    """當前角色的句首稱呼清單（取代原本硬寫的 ^(茜|小茜|Akane|あかね)）。

    以表內名單為準，並把 companion 自己宣告的 display_name / short_name 併進去
    （config 是單一事實來源；表只是保底，兩者聯集不會漏掉使用者實際會喊的名字）。
    """
    rules = _agent_rules(companion)
    if not rules:
        return []
    names = list(rules.get("names") or [])
    if isinstance(companion, dict):
        for key in ("short_name", "display_name"):
            v = companion.get(key)
            if isinstance(v, str) and v.strip() and v.strip() not in names:
                names.append(v.strip())
    return names


def _name_alternation(companion: Optional[dict]) -> Optional[str]:
    """把稱呼清單組成 regex 交替字串（供 ^(...) 使用）；無角色時回 None（該步驟跳過）。"""
    names = _agent_names(companion)
    if not names:
        return None
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


# 口吃/重複贅字（語音對話高頻 filler）
FILLER_TOKENS = [
    "就是說", "就是说", "然後", "然后", "那個", "那个",
    "就是", "這樣", "这样", "嗯", "呃", "啊", "哦", "喔", "唉",
]

# 無意義感嘆詞 / 嘆氣 / 咳嗽 / 背景雜音音節
NOISE_SYLLABLES = set("啊嗯呼呃唉哦喔哈嘿咳哼咦唔嘛吧呢呐啦哟欸哎")

# 終點標點（不缺句號時保留）
TERMINAL_PUNCT = "。！？…"

# 【VC-REFINER-2】幻覺熔斷用的腳本判準（定義於 is_noise 之前，供該處引用）
_CJK_CHAR_RE = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]")
_ASCII_ALNUM_RE = re.compile(r"[0-9A-Za-z]")


# ─────────────────────────────────────────────────────────────
# 雜音熔斷
# ─────────────────────────────────────────────────────────────

def is_noise(raw_text: str) -> bool:
    """判斷輸入是否為純環境雜音（僅感嘆詞/嘆氣/無意義音節/非 CJK 幻覺）。"""
    if not raw_text or not raw_text.strip():
        return True
    core = re.sub(r"[\W_]+", "", raw_text)  # 去空白/標點/點點點，保留中日韓字與拉丁字母
    if not core:
        return True
    # 【VC-REFINER-2】非 CJK／非 ASCII 的幻覺熔斷。
    # 2026-10-16 考古實測：rem:10119 的越南文幻覺 `nếu như không à` **穿過整套閘門
    # 直送大腦**（既有 NOISE_SYLLABLES 只認 CJK 嘆氣字，對外文幻覺完全無效）。
    # 判準：核心既無 CJK、也無 ASCII 字母/數字 ⇒ 非本系統語境可解讀，視為幻覺。
    # 純數字（"123"）與中英夾雜不受影響。
    if not _CJK_CHAR_RE.search(core):
        # 沒有 CJK 時再分兩種幻覺：帶音符的外文（越南文 `nếu như không à` 實測漏接）、
        # 以及純符號/emoji。純 ASCII（`OK` / `123` / `Rust`）是合法輸入，必須放行。
        if any((not ch.isascii()) and ch.isalpha() for ch in core):
            return True
        if not _ASCII_ALNUM_RE.search(core):
            return True
    return all(ch in NOISE_SYLLABLES for ch in core)


# ─────────────────────────────────────────────────────────────
# 條件式執行判定（VC-ASR-COND-1）：預設 Bypass，異常才 Run
# ─────────────────────────────────────────────────────────────

# 異常重複字元連續長度門檻（轉錄模型幻想/口吃標記，如「呵呵呵呵呵呵」）
REPEAT_RUN_LIMIT = 6


def needs_refiner(raw_text: str, companion: Optional[dict] = None) -> tuple[bool, str]:
    """判定 ASR 轉錄是否需要 Refiner：回傳 (False=bypass, reason) / (True=run, reason)。

    `companion`（`cfg["companion"]`）為 **VC-REFINER-2 新增**：決定「homophone」
    條件要看哪一張規則表。傳 None（預設）⇒ 只看通用表。

    預設 Bypass：清晰、高信心的日常完整語句直接直通 Voice Brain（每回合 1 次 LLM 呼叫）。
    任一異常條件命中才 Run（Refiner 保留為第二道防線）：
      1. too-short  — ≤ 2 個字元（極短/可能為語助詞或雜音片段）
      2. punct-only — 無中日韓字/拉丁字母/數字（全標點或符號殘渣）
      3. noise      — 命中既有雜音熔斷器候選特徵（is_noise）
      4. repeat     — 單字元連續重複 ≥ REPEAT_RUN_LIMIT（幻想/口吃標記）
      5. punct-heavy— 標點/非文字符號數 > 文字字元數（格式明顯損毀）
      6. homophone  — 命中本模組既有的同音錯字規則表（VC-ASR-HOMOPHONE-1，2026-10-14）
    空白輸入 → (False, "empty")：Refiner 無內容可淨化，呼叫端沿用既有空轉錄處理，
    （VC-ASR-COND-1 的 decision 日誌仍會印出 reason=empty）。

    條件 6 的由來：同音錯字是「長度正常、無標點異常」的高信心轉錄，本來會被判成
    `clear` 直接 bypass，導致**模組職責 #1（同音校準）在生產路徑上形同死碼**——
    規則表寫得好好的卻永遠不會被呼叫。放在最後是刻意的：既有五個條件的優先序完全不動。
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
    if _has_homophone_error(raw_text, companion):
        return True, "homophone"
    return False, "clear"


# ─────────────────────────────────────────────────────────────
# 同音錯字偵測（VC-ASR-HOMOPHONE-1，2026-10-14）
# ─────────────────────────────────────────────────────────────

def _has_homophone_error(raw_text: str, companion: Optional[dict] = None) -> bool:
    """轉錄是否命中本模組既有的同音錯字規則表。

    **只偵測、不新增規則**：偵測與修補必須共用同一份真相表，否則會出現
    「偵得到卻修不了」或反之的缺口——那正是本條件要補的洞。

    `companion`（**VC-REFINER-2 新增**）決定要不要看當前角色的專屬表。
    舊實作只有一張寫死小茜的表，三個實例共用 ⇒ 雷姆實例會被判成
    「小欠早安」需要修補、然後把小欠改成**小茜**（跨角色污染）。

    為什麼需要它（2026-10-14 唯讀審計實測）：
        needs_refiner("小欠早安", AKANE)   -> (True,  "homophone")
        needs_refiner("小欠早安", REM)     -> (False, "clear")   # 不是小茜，不該觸發
        needs_refiner("排成跑起來")         -> (True,  "homophone")  # 通用表
        local_refine("小欠早安", AKANE)    -> "小茜，早安。"
    ⇒ 規則從未被呼叫＝死碼；接通後這兩句會回 (True, "homophone")。
    """
    text = re.sub(r"\s+", "", raw_text or "")
    if not text:
        return False
    for wrong, _ in HOMOPHONE_PHRASE_FIXES:
        if wrong in text:
            return True
    rules = _agent_rules(companion)
    if rules:
        for wrong, _ in rules.get("pairs") or []:
            if wrong in text:
                return True
        for pattern, _ in rules.get("regex") or []:
            if re.search(pattern, text):
                return True
        char_re = rules.get("char_re")
        if char_re is not None and char_re.search(text):
            return True
    return False


# ─────────────────────────────────────────────────────────────
# 內建確定性修復（極速模式，離線可用）
# ─────────────────────────────────────────────────────────────

def local_refine(raw_text: str, companion: Optional[dict] = None) -> Optional[str]:
    """規則式淨化：錯字 → 贅詞 → 標點。雜音回傳 None。

    `companion`（**VC-REFINER-2 新增**，`cfg["companion"]`）：決定套用哪一張角色
    專屬規則表與哪一組句首稱呼。傳 None（預設）⇒ **只跑通用規則**，不碰任何角色
    別名。這是安全預設：舊實作三個實例共用寫死小茜的表，導致雷姆實例會把
    「小欠早安」修成「小茜，早安。」（跨角色污染）。
    """
    if is_noise(raw_text):
        return None

    text = re.sub(r"\s+", "", raw_text)

    # 1. 【通用】多字詞同音校準
    for wrong, right in HOMOPHONE_PHRASE_FIXES:
        text = text.replace(wrong, right)

    # 2. 【角色專屬】稱呼化同音字 + 需上下文守衛的替換
    #    （順序敏感：表內由具體到一般，逐條 replace）
    rules = _agent_rules(companion)
    if rules:
        for wrong, right in rules.get("pairs") or []:
            text = text.replace(wrong, right)
        for pattern, right in rules.get("regex") or []:
            text = re.sub(pattern, right, text)

    # 3. 【角色專屬】單獨同音字（千/欠/西/籤/签 → 茜；僅小茜有）
    char_re = (rules or {}).get("char_re")
    if char_re is not None:
        text = char_re.sub((rules or {}).get("char_to") or "茜", text)

    # 4. 點點點 / 省略號正規化
    text = re.sub(r"[.\．]{2,}", "…", text)

    # 5. 連續口吃重複消除（例如：那個那個 → 那個；然後然後 → 然後）
    text = re.sub(r"(那個|那个|然後|然后|就是說|就是说|就是){2,}", r"\1", text)

    # 6. 句首停頓贅詞消除（例如：呃、那個、就是說...，或「茜...那個...」）
    #    【VC-REFINER-2】稱呼交替改為**當前角色動態產生**（原為硬寫
    #    ^(茜|小茜|Akane|あかね)，使麻衣／雷姆拿不到自己的稱呼處理）。
    prefix = ""
    rest = text
    name_alt = _name_alternation(companion)
    if name_alt:
        call_match = re.match(r"^(%s)(?:[，,、…]+)?" % name_alt, text)
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

    # 7. 稱呼後補逗號：當前角色的名字直接接中文字時插入「，」
    #    【VC-REFINER-2】原為硬寫 ^(茜|小茜|Akane|あかね)，麻衣／雷姆拿不到同等待遇。
    if name_alt:
        text = re.sub(r"^(%s)(?=[一-龥])" % name_alt, r"\1，", text)

    # 8. 終點標點
    text = re.sub(r"[，,、~～…]+$", "", text).rstrip()
    if text and text[-1] not in TERMINAL_PUNCT:
        text += "。"

    return text


# ─────────────────────────────────────────────────────────────
# LLM 通道（生產選配；測試一律注入 Mock 或不注入）
# ─────────────────────────────────────────────────────────────

def build_refine_prompt(raw_stt_text: str, companion: Optional[dict] = None) -> str:
    """組裝修復器 Prompt（VC-REFINER-2：角色身分改為動態注入）。

    🔴 舊版把「正在與 AI 伴侶**黑川茜（Akane）**對話」與「將『千/欠/西』修正為
    『茜/小茜/Akane』」**寫死**，而 8766 麻衣 / 8767 雷姆 也會拿到同一段 prompt。
    2026-09-11/12 的生產語料證實它真的造成污染：雷姆實例的 `雷姆` 被改寫成
    `蕾姆`（rem:3238 / rem:4162）與 `茜`（rem:4227 / rem:5254）——**ASR 本來就聽對了**。

    現在角色名與同音提示一律從 `cfg["companion"]` 與本模組的角色規則表產生；
    `companion=None` 時不注入任何角色名（寧可少修，不可改錯角色）。
    """
    display_name = ""
    if isinstance(companion, dict):
        for key in ("display_name", "short_name"):
            v = companion.get(key)
            if isinstance(v, str) and v.strip():
                display_name = v.strip()
                break

    if display_name:
        who = f"正在與 AI 伴侶{display_name}進行即時語音對話"
    else:
        who = "正在與某位 AI 伴侶進行即時語音對話"

    names = _agent_names(companion)
    rules = _agent_rules(companion)
    hints = ["將「排成/拍成」修正為「排程」"]
    if names:
        # 只列當前角色的稱呼。**絕不列其他角色的名字**——那正是舊版造成污染的原因。
        hints.append(f"將屬於「{display_name or '目前角色'}」的同音稱呼還原為其正確寫法（例：{'／'.join(names)}）")
    if rules:
        for wrong, right in rules.get("pairs") or []:
            hints.append(f"將「{wrong}」還原為「{right}」")
    hint_text = "；".join(hints)

    return (
        f"你是一個極速語音識別修復器。使用者叫 Bryan，{who}。\n"
        "【任務】：\n"
        f"1. 修正語音轉寫常見的同音錯字（{hint_text}）。\n"
        "2. 去除口吃、重複贅字（如「呃...那個...就是說...」）。\n"
        "3. 補充正確標點符號，還原說話者的真實語意。\n"
        "4. 【雜音熔斷】：若輸入僅為無意義感嘆詞、嘆氣、咳嗽、背景雜音片段（如「啊」、「嗯」、「呼」），或為非中文/非英文的無意義幻覺片段，直接輸出 EMPTY。\n"
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
        # 【VC-REFINER-2】記住當前實例的角色身分（cfg["companion"]）。
        # 舊實作只讀 self.config["llm"] 就丟掉其餘，導致整個模組不知道自己在跟誰說話。
        # 刻意存「整份 companion dict」而非解析後的表：表由呼叫時即時推導，避免
        # 規則更新後舊實例仍持有過期快照。
        self.companion = self.config.get("companion") or None
        # 診斷用唯讀欄位；不得影響回傳值（VC-ASR-DIAG-1）。
        # None = 尚未呼叫過 refine_speech_text()；呼叫後值域僅 "llm" / "local" / "drop"。
        self.last_refine_path: Optional[str] = None
        if self.llm_call is None:
            llm_cfg = self.config.get("llm") or {}
            if llm_cfg.get("endpoint"):
                self.llm_call = build_llm_call(llm_cfg)

    @classmethod
    def from_config(cls, config: dict) -> "AsrRefiner":
        return cls(config=config)

    def refine_speech_text(self, raw_text: str, companion: Optional[dict] = None) -> Optional[str]:
        """回傳修復後的純淨文字；若為雜音或空白則回傳 None（觸發 DROP）。

        `companion`（**VC-REFINER-2 新增**）為**單次覆寫**：省略時用本實例的
        `self.companion`（由 `from_config` 從 `cfg["companion"]` 帶入）。
        刻意走參數而非改寫 `self.companion`——本方法在 asyncio 迴圈中可能被
        併發呼叫，改寫實例狀態會造成跨請求的角色洩漏。

        診斷欄位（VC-ASR-DIAG-1，唯讀；不得影響回傳值）：
          `self.last_refine_path` 記錄「本次回傳值由哪條路徑產出」，值域僅三種：
            - "llm"   — LLM 精煉成功且採用了它的輸出（含模型回覆經 strip 後非空）
            - "local" — 走規則式 local_refine() 產出（含 LLM 失敗／空回覆後落地）
            - "drop"  — 最終結果為 None（雜音/無內容，呼叫端會 DROP）
          進入點先**重設為本次的悲觀預設 "drop"**（本次若無 llm/local 產出即為 drop），
          因此任何路徑都不可能殘留上一次呼叫的值；`__init__` 的 None 則專指
          「尚未呼叫過」。**此重設是本欄位正確性的關鍵，不得移除。**
        """
        # 診斷用唯讀欄位；不得影響回傳值。
        # 每次呼叫先重設（悲觀預設 drop）——drop 路徑靠這行取得正確值，絕不殘留上一次的值。
        self.last_refine_path = "drop"
        effective_companion = self.companion if companion is None else companion
        if is_noise(raw_text):
            return None  # 純雜音：維持本次預設 "drop"
        if self.llm_call is not None:
            try:
                result = (self.llm_call(build_refine_prompt(raw_text, effective_companion)) or "").strip()
            except Exception:
                result = ""
            if result:
                if result.upper() == "EMPTY":  # 模型判定雜音 → 熔斷（維持 "drop"）
                    return None
                self.last_refine_path = "llm"
                return result
        # LLM 未注入／呼叫失敗／回覆空白 → 落到規則式路徑（如實標 local）
        refined = local_refine(raw_text, effective_companion)
        if refined is not None:
            self.last_refine_path = "local"
        return refined  # None ⇒ 維持本次預設 "drop"


# 模組級預設實例：離線確定性模式（極速），供無狀態呼叫與驗收測試使用
_DEFAULT_REFINER = AsrRefiner(llm_call=None)


def refine_speech_text(raw_text: str, companion: Optional[dict] = None) -> Optional[str]:
    """回傳修復後的純淨文字；若為雜音或空白則回傳 None（觸發 DROP）。

    `companion`（**VC-REFINER-2 新增**）以單次覆寫傳入，不改寫模組級單例的狀態。
    """
    return _DEFAULT_REFINER.refine_speech_text(raw_text, companion)