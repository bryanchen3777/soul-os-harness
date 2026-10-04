"""TA-2 v2-A — 候選 A（語法依附）A1 veto 濾層。

預登記契約：`docs/TA2-V2A-FEASIBILITY-CONTRACT.md`（§2 規則表、§3 兩段式判定）。
本檔是該契約的**逐字實作**。規則表、詞表、窗口大小**不得**因結果而修改。

🔴 **定位**：這是疊在 **frozen v1 規則表之上的 veto 濾層**，不是新分類器。
   - A1（本檔）= precision 濾層，只**否決** v1 的候選命中，**不自行偵測時段**。
   - A2（rescue/recall）= **明確不做**（等同新 classifier，違反 Owner §9.3）。
   ⇒ **A1 結構上只可能降低 recall，不可能提高 recall。**

🔴 **arm-blindness（契約 AC4）**：本檔所有判定函式的輸入**只有**回應文字
   `raw_text`。本檔**不讀取** `arm`／`probe_id`／`injected_temporal_line`，
   也**不 import** `harness.run_ta2a_gate2` 的 arm 相關結構（只取規則表與
   兩個純函式）。

🔴 **0 次 LLM 呼叫、0 網路**：本檔不得 import `llm_call`／`requests`／`socket`。
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

# 🔴 frozen v1 唯讀 import（契約 §2.4）。**不得**修改被 import 的檔案。
from harness.run_ta2a_gate2 import (  # noqa: E402
    _EXCLUDE_FOR,
    _FRAME_RULES,
    d,  # noqa: F401  (契約 §2.4：距離函式必須 import，不轉錄)
    extract_temporal_frame,
)

# ══════════════════════════════════════════════════════════════
# §2.1 封閉詞表（**逐字**；與 docs/TA2-V2A-FEASIBILITY-CONTRACT.md
#     §2.1 / §3.4 逐字一致，由 tests/test_ta2v2a_attachment.py 驗證）
# ══════════════════════════════════════════════════════════════

#: 純動作／狀態詞：作為動詞或身體狀態，**不是** time-of-day
ACTION_TOKENS = frozenset({"睡", "睡覺", "入睡", "就寢"})

#: 複合時段名詞：本身即含時段意義，**不**列入 ACTION_TOKENS
#: （否則會誤殺整個 siesta 幀——主大腦自查修正）
COMPOUND_TIME_NOUNS = frozenset(
    {"午睡", "補眠", "宵夜", "早餐", "午餐", "晚餐", "晚飯", "中飯", "午安"}
)

COLOR_APPEARANCE_NOUNS = frozenset({"藍", "色", "顏色", "色澤", "天", "光", "夜色"})
SIMILE_MARKERS = frozenset({"好像", "像是", "彷彿", "好似", "大概", "像是那種", "那種"})
NEGATION_MARKERS = frozenset({"不是", "並非", "並不", "不在"})
ATTACHMENT_MARKERS = frozenset(
    {"就", "吧", "改", "換", "挪", "推", "順延", "延到", "到", "這", "那", "現在",
     "待會", "剛才", "這次"}
)

#: §2.3.1 允許的**唯一**分句手段（封閉清單，沒有第二套切分）
CLAUSE_SEPARATORS = "。！？；：，、"

#: §2.3.6 允許插入 R3 窗口的字元（僅 `剛`／`的`）
_R3_FILLER_CHARS = frozenset("剛的")

#: §2.3.3 多 occurrence 縮減的嚴格度優先序（數字越小越嚴格）
_STRICTNESS: Dict[str, int] = {"vetoed": 0, "kept": 1, "undecided": 2}

VETOED, KEPT, UNDECIDED = "vetoed", "kept", "undecided"

#: rule_id 常數（契約 §2 表；順序即優先序）
R1_ACTION_TOKEN = "R1_ACTION_TOKEN"
R2_EXCLUDE_TOKEN = "R2_EXCLUDE_TOKEN"
R3_ATTRIBUTIVE_NOUN = "R3_ATTRIBUTIVE_NOUN"
R4_SIMILE_FRAME = "R4_SIMILE_FRAME"
R5_NEGATION_FRAME = "R5_NEGATION_FRAME"
R6_NO_ATTACHMENT = "R6_NO_ATTACHMENT"
R7_ATTACHED = "R7_ATTACHED"

ALL_RULE_IDS: Tuple[str, ...] = (
    R1_ACTION_TOKEN, R2_EXCLUDE_TOKEN, R3_ATTRIBUTIVE_NOUN, R4_SIMILE_FRAME,
    R5_NEGATION_FRAME, R6_NO_ATTACHMENT, R7_ATTACHED,
)


# ══════════════════════════════════════════════════════════════
# §2.3.1／§2.3.2 分句
# ══════════════════════════════════════════════════════════════

def has_clause_separator(text: str) -> bool:
    """是否含任一分隔符。

    🔴 **修訂記錄 R-1（見契約 §2.5）**：本函式**早期版本**回傳 False 時會強制判
    `undecided`。該行為與工單自帶的案例表衝突（工單 R3 正例「大概是夜色剛落下…那種藍」
    **無任何**分句標點，卻要求 `vetoed`）。故「無標點」**不再**觸發 `undecided`：
    全文即為單一小句，規則照常評估。`undecided` 僅保留給**真正無法切分**的退化情形
    （命中 token 所在小句為空／純空白），見 `judge_token_at`。
    """
    return any(sep in (text or "") for sep in CLAUSE_SEPARATORS)


def clause_spans(text: str) -> List[Tuple[int, int]]:
    """回傳每個小句在原文中的 `[start, end)` span（分隔符不屬於任何小句）。"""
    t = text or ""
    spans: List[Tuple[int, int]] = []
    start = 0
    for i, ch in enumerate(t):
        if ch in CLAUSE_SEPARATORS:
            spans.append((start, i))
            start = i + 1
    spans.append((start, len(t)))
    return spans


def split_clauses(text: str) -> List[str]:
    """§2.3.1：最大非分隔字元連續段，去除前後空白。"""
    t = text or ""
    return [(t[s:e]).strip() for s, e in clause_spans(t)]


def _clause_at(text: str, index: int) -> Tuple[str, Tuple[int, int]]:
    """回傳包含 `index` 的小句字串與其 span。"""
    t = text or ""
    for s, e in clause_spans(t):
        if s <= index < e:
            return t[s:e].strip(), (s, e)
    return t.strip(), (0, len(t))


def _all_occurrences(text: str, token: str) -> List[int]:
    out: List[int] = []
    i = (text or "").find(token)
    while i >= 0:
        out.append(i)
        i = (text or "").find(token, i + 1)
    return out


# ══════════════════════════════════════════════════════════════
# §2 規則表的單條判定述語（供 ledger 與測試使用）
# ══════════════════════════════════════════════════════════════

def r1_action_token(token: str) -> bool:
    """R1：命中 token 屬 ACTION_TOKENS。**不需要小句**（§2.3.4）。"""
    return token in ACTION_TOKENS


def r2_exclude_token(exclude_hit: bool) -> bool:
    """R2：v1 排除詞命中。防禦性規則——v1 順序下不可達（契約 §2.2.3／§3.5）。"""
    return bool(exclude_hit)


def r3_attributive_noun(clause: str, token: str, at: Optional[int] = None) -> bool:
    """R3：命中 token 在小句內**直接修飾外觀類名詞**（窗口 ≤3，允許 `剛`／`的`）。

    §2.3.6 兩種情形：
      A（重疊／複合詞）：`n0 <= t0` 且 token span 與名詞 span 重疊 → 涵蓋「夜色」。
      B（後接修飾）：`n0 > t1`，間隔字元全屬 {剛, 的}，且 `n0 - t1 <= 3`
        → 涵蓋「夜的顏色」「夜的色澤」。
    """
    c = clause or ""
    t0 = c.find(token) if at is None else at
    if t0 < 0 or not token:
        return False
    t1 = t0 + len(token)
    for noun in COLOR_APPEARANCE_NOUNS:
        n0 = c.find(noun)
        while n0 >= 0:
            n1 = n0 + len(noun)
            if n0 <= t0 < n1:  # 情形 A
                return True
            if n0 > t1 and (n0 - t1) <= 3:  # 情形 B
                gap = c[t1:n0]
                if gap and all(ch in _R3_FILLER_CHARS for ch in gap):
                    return True
            n0 = c.find(noun, n0 + 1)
    return False


def r4_simile_frame(clause: str) -> bool:
    """R4：命中所在小句含 SIMILE_MARKERS（子串包含）。"""
    return any(m in (clause or "") for m in SIMILE_MARKERS)


def r5_negation_frame(clause: str) -> bool:
    """R5：命中所在小句含 NEGATION_MARKERS（子串包含）。"""
    return any(m in (clause or "") for m in NEGATION_MARKERS)


def r6_no_attachment(clause: str) -> bool:
    """R6：小句內找不到任何依附標記。"""
    return not any(m in (clause or "") for m in ATTACHMENT_MARKERS)


def r7_attached(clause: str) -> bool:
    """R7：小句內找到依附標記。"""
    return any(m in (clause or "") for m in ATTACHMENT_MARKERS)


# ══════════════════════════════════════════════════════════════
# §2.3.8 token 層判定
# ══════════════════════════════════════════════════════════════

def judge_token_at(
    text: str,
    token: str,
    at: int,
    exclude_hit: bool = False,
) -> Dict[str, Any]:
    """對 token 在 `at` 的**單一**出現做規則表判定（§2.3.8 優先序）。

    回傳 `{"verdict", "rules", "clause", "reason"}`。
    `rules` 為 ledger 記錄（可多值，依 §2.2.2／§2.2.3 全部寫入）。
    """
    clause, _span = _clause_at(text, at)
    rules: List[str] = []

    # §2.2.1：R1 最高優先，命中即 vetoed，不再看其他規則。
    if r1_action_token(token):
        return {"verdict": VETOED, "rules": [R1_ACTION_TOKEN] + rules,
                "clause": clause, "reason": R1_ACTION_TOKEN}

    # 🔴 修訂記錄 R-2（契約 §2.5）：R2 命中時**動作即為 `vetoed`**（逐字對齊工單 §3 表），
    #    優先序僅次於 R1。契約 §2.3.5 早期版本說 R2「不改變主結果」——已依工單表修正。
    #    （R2 在 v1 規則順序下不可達，故此路徑在 corpus 上不可觀測。）
    if r2_exclude_token(exclude_hit):
        return {"verdict": VETOED, "rules": [R2_EXCLUDE_TOKEN],
                "clause": clause, "reason": R2_EXCLUDE_TOKEN}

    # §2.2.4：**真正**無法切分（命中所在小句為空／純空白）⇒ undecided。
    # 🔴 見修訂記錄 R-1：全文無標點**不**算無法切分（全文即單一小句），
    #    否則工單自帶的 R3 正例「大概是夜色剛落下…那種藍」會被誤判為 undecided。
    if not clause:
        return {"verdict": UNDECIDED, "rules": rules, "clause": clause,
                "reason": "unsplittable_clause"}

    # `at` 是原文的絕對位置，與小句內的相對位置不同；R3 一律以小句內首次出現為準。
    hit3 = r3_attributive_noun(clause, token)
    hit4 = r4_simile_frame(clause)
    hit5 = r5_negation_frame(clause)

    if hit3:
        rules.append(R3_ATTRIBUTIVE_NOUN)  # §2.2.2：R3 為主記錄
        if hit4:
            rules.append(R4_SIMILE_FRAME)
        if hit5:
            rules.append(R5_NEGATION_FRAME)
        return {"verdict": VETOED, "rules": rules, "clause": clause,
                "reason": R3_ATTRIBUTIVE_NOUN}
    if hit4:
        rules.append(R4_SIMILE_FRAME)
        if hit5:
            rules.append(R5_NEGATION_FRAME)
        return {"verdict": VETOED, "rules": rules, "clause": clause,
                "reason": R4_SIMILE_FRAME}
    if hit5:
        rules.append(R5_NEGATION_FRAME)
        return {"verdict": VETOED, "rules": rules, "clause": clause,
                "reason": R5_NEGATION_FRAME}
    if r7_attached(clause):
        rules.append(R7_ATTACHED)
        return {"verdict": KEPT, "rules": rules, "clause": clause,
                "reason": R7_ATTACHED}
    rules.append(R6_NO_ATTACHMENT)
    return {"verdict": UNDECIDED, "rules": rules, "clause": clause,
            "reason": R6_NO_ATTACHMENT}


def evaluate_token(text: str, token: str, exclude_hit: bool = False) -> Dict[str, Any]:
    """§2.3.3：對 token 的**所有**出現逐一分句判定，取最嚴格結果。

    嚴格度優先序（契約 §2.3.3）：`vetoed` > `kept` > `undecided`。
    `span`（小句摘要）記錄**產生該結果的那一次**出現。
    """
    t = text or ""
    best: Optional[Dict[str, Any]] = None
    for at in _all_occurrences(t, token):
        cand = judge_token_at(t, token, at, exclude_hit=exclude_hit)
        cand["at"] = at
        if best is None or _STRICTNESS[cand["verdict"]] < _STRICTNESS[best["verdict"]]:
            best = cand
    if best is None:
        return {"verdict": UNDECIDED, "rules": [], "clause": "", "reason": "absent", "at": -1}
    return best


# ══════════════════════════════════════════════════════════════
# §2.3.9 T 的構造
# ══════════════════════════════════════════════════════════════

def frame_triggers(frame: str) -> Tuple[str, ...]:
    """v1 `_FRAME_RULES` 中 frame `F` 的觸發詞 tuple（唯讀）。"""
    for name, keys, _exclusive in _FRAME_RULES:
        if name == frame:
            return tuple(keys)
    return ()


def trigger_tokens(frame: str, text: str) -> List[str]:
    """T = v1 frame `F` 的所有實際命中 token，順序 = v1 規則表宣告順序（§2.3.9）。"""
    t = text or ""
    return [k for k in frame_triggers(frame) if k in t]


def exclude_hit_for(frame: str, text: str) -> bool:
    """v1 排除詞是否命中（依構造在 v1 已指派 F 時**永不**為真——§2.2.3／§3.5）。"""
    return any(bad in (text or "") for bad in _EXCLUDE_FOR.get(frame, ()))


# ══════════════════════════════════════════════════════════════
# §3.2 兩段式：token 層 → frame 層彙總
# ══════════════════════════════════════════════════════════════

def summarize(tokens: Sequence[Dict[str, Any]]) -> str:
    """§3.2 frame 層彙總真值表（**不得改寫**）。

    - 全部 `vetoed`           ⇒ `vetoed`
    - 任一 `kept`             ⇒ `kept`
    - 其餘（至少一個 undecided）⇒ `undecided`
    """
    if not tokens:
        return UNDECIDED
    verdicts = [t["verdict"] for t in tokens]
    if all(v == VETOED for v in verdicts):
        return VETOED
    if any(v == KEPT for v in verdicts):
        return KEPT
    return UNDECIDED


def apply_a1(text: str) -> Dict[str, Any]:
    """對單一 `raw_text` 套用 A1（**arm-blind**：輸入只有回應文字）。

    回傳：
      `v1_frame`, `T`, `token_verdicts`（逐 token）, `a1_verdict`, `v2_frame`,
      `rules`（frame 層彙總 rule_id 集合）, `clause`（觸發彙總的小句摘要）
    """
    t = text or ""
    v1_frame = extract_temporal_frame(t)  # frozen v1，唯讀

    if v1_frame == "unframed":
        return {"v1_frame": v1_frame, "T": [], "token_verdicts": [],
                "a1_verdict": UNDECIDED, "v2_frame": "unframed", "rules": [],
                "clause": "", "reason": "v1_unframed"}

    tokens = trigger_tokens(v1_frame, t)
    if not tokens:
        return {"v1_frame": v1_frame, "T": [], "token_verdicts": [],
                "a1_verdict": UNDECIDED, "v2_frame": "unframed", "rules": [],
                "clause": "", "reason": "empty_T"}

    exclude_hit = exclude_hit_for(v1_frame, t)
    token_verdicts = []
    for tok in tokens:
        v = evaluate_token(t, tok, exclude_hit=exclude_hit)
        token_verdicts.append({
            "token": tok,
            "verdict": v["verdict"],
            "rules": v["rules"],
            "clause": v["clause"][:40],
        })

    a1 = summarize(token_verdicts)
    v2_frame = v1_frame if a1 == KEPT else "unframed"
    return {
        "v1_frame": v1_frame,
        "T": tokens,
        "token_verdicts": token_verdicts,
        "a1_verdict": a1,
        "v2_frame": v2_frame,
        "rules": sorted({r for tv in token_verdicts for r in tv["rules"]}),
        "clause": token_verdicts[0]["clause"],
        "reason": "frame_summary",
    }
