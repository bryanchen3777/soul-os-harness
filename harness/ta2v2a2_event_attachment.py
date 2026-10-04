"""TA-2 v2-A2 — A2：Local Event-Attachment（**本地詞窗依附啟發式**）。

機制規格（主大腦預登記、逐字實作）：`docs/TA2-V2A2-MECHANISM-SPEC.md`
上游工單：TA-2-V2A2（Owner 2026-10-03 授權）。背景：`docs/TA2-GATE2-MEASUREMENT-CONTRACT-V2-DESIGN.md` §9。

🔴 **名稱紀律（規格 §0）**：本檔是 **local / lexical-window heuristic**。
   ❌ **不得**稱為 syntactic parsing / syntactic attachment / dependency parsing。
   ❌ **不得**宣稱由詞形匹配得到 semantic attribution。

🔴 **本檔只做一件事：新增 R8**（規格 §3）。A1 的 R1–R7 規則表與兩段式彙總
   **逐字不動**、全部 import 自 `harness/ta2v2a_attachment.py`（唯讀）。
   R8 的優先序是：

       R1 → R2 → (小句不可切) → R3 → R4 → R5 → **R8** → R6/R7

   即：**R8 優先於 R6/R7，但絕不覆寫 R1–R5**（規格 §3 硬約束）。
   由此 GO-B 守得住（睡／動作詞仍被 R1 否決），且 A1 的 `undecided` 語義保持可比。

🔴 **arm-blindness**：本檔所有判定函式的輸入**只有**回應文字 `raw_text`。
   本檔**不讀取** `arm`／`probe_id`／`injected_temporal_line`，也**不 import**
   frozen v1 的任何 arm 相關結構。

🔴 **0 次 LLM 呼叫、0 網路、僅標準庫**：不得 import `llm_call`／`requests`／`socket`，
   不得引入 dependency parser 或任何大型 NLP 基礎設施（Owner 工單禁止項）。
"""
from __future__ import annotations

import pathlib
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path and (REPO / "harness" / "ta2v2a_attachment.py").exists():
    sys.path.insert(0, str(REPO))

# 🔴 A1 唯讀 import。**不得**修改 `harness/ta2v2a_attachment.py`（A1 負面基線必須保留）。
from harness import ta2v2a_attachment as a1  # noqa: E402

#: frozen v1 唯讀 import（與 A1 相同路徑）。
from harness.run_ta2a_gate2 import extract_temporal_frame  # noqa: E402  (唯讀)

VETOED, KEPT, UNDECIDED = a1.VETOED, a1.KEPT, a1.UNDECIDED
ACTION_TOKENS = a1.ACTION_TOKENS  # C1 引用（唯讀別名）

# ══════════════════════════════════════════════════════════════
# 規格 §3 封閉詞表（**逐字**；與 docs/TA2-V2A2-MECHANISM-SPEC.md §3 圍欄
# 逐字一致，由 tests/test_ta2v2a2_event_attachment.py 驗證。**不得增刪。**）
# ══════════════════════════════════════════════════════════════

#: event-side：一般活動動詞類（**語意詞類**，非話語標記類）
EVENT_VERBS = frozenset(
    {"吃", "喝", "煮", "做", "用餐", "開會", "開", "聚", "聚會", "碰面", "見面", "上課",
     "上班", "下班", "收工", "上工", "休息", "午休", "出發", "動身", "抵達", "到達",
     "加班", "補", "熬", "收尾", "醒", "起床", "睡醒", "通勤", "忙", "忙完"}
)

#: 允許插入 event-side 與 temporal-side 之間的**唯一**填充字元（≤1 個）
FILLER_CHARS = frozenset({"的", "了", "是", "要", "該", "就", "在", "個"})

R8_LOCAL_EVENT_ATTACHMENT = "R8_LOCAL_EVENT_ATTACHMENT"

# 🔴 MUTATION SEAM M1（規格 §6 mutation test 1 的注入點）
#    規格 §6 M1 定義的注入 = 「讓 R8 永遠不成立（把 event-side 條件設為恆 False）」。
#    本常數是該注入的**唯一**著陸點；它只影響 `r8_local_attachment()`，不影響 R1–R7。
R8_ENABLED: bool = True


def _event_verb_order() -> Tuple[str, ...]:
    """`EVENT_VERBS` 的**確定性**迭代順序（最長詞優先 → 詞長 → 字串序）。

    🔴 這只是**平手時的取捨順序**（決定 audit 裡的 primary 記錄），
       **不影響 R8 是否成立**（成立條件是「存在任一符合條件的 v」）。
    """
    return tuple(sorted(EVENT_VERBS, key=lambda w: (-len(w), w)))


_EVENT_VERB_ORDER: Tuple[str, ...] = _event_verb_order()


# ══════════════════════════════════════════════════════════════
# 規格 §3：R8 — Local Event-Attachment
# ══════════════════════════════════════════════════════════════

def clause_span_at(text: str, index: int) -> Tuple[str, Tuple[int, int]]:
    """回傳包含 `index` 的**原始**（未 strip）小句字串與其 `[start, end)` span。

    🔴 **C3（子句內）由此函式強制**：分句沿用 A1 的 `CLAUSE_SEPARATORS`（唯讀），
       `v` 與 `t` 若落在不同小句，兩者根本不會出現在同一個字串裡，跨子句共現因此不可能。
    """
    t = text or ""
    for s, e in a1.clause_spans(t):
        if s <= index < e:
            return t[s:e], (s, e)
    return t, (0, len(t))


def _r8_event_side_candidates(clause: str, token: str, at: int) -> Tuple[List[Dict[str, Any]],
                                                                         List[Dict[str, Any]]]:
    """C1／C2／C3 的**唯一**實作點：回傳 `(成立, 落選)` 兩組 event-side 候選。

    規格 §3：
      - C1 不自證：`v` 不得與 `t` span 重疊；`v` 不得是 `ACTION_TOKENS`。
      - C2 詞類  ：`v` 必須屬 `EVENT_VERBS`（封閉）。
      - 窗口    ：兩者之間 0 個字元，或 ≤1 個字元且該字元屬 `FILLER_CHARS`。

    `at` 為 `token` 此次出現在 `clause` 內的起始索引。
    """
    # --- BEGIN MUTATION SEAM: _r8_event_side_candidates ---
    c = clause or ""
    t0, t1 = at, at + len(token or "")
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for v in _EVENT_VERB_ORDER:
        if v in ACTION_TOKENS:  # C1（防禦性：睡覺類不得自證）
            continue
        p = c.find(v)
        while p >= 0:
            p0, p1 = p, p + len(v)
            base = {"event_token": v, "event_at": p, "temporal_token": token,
                    "temporal_at": t0}
            if not (p1 <= t0 or p0 >= t1):  # C1：不得與 temporal token 同 span
                rejected.append({**base, "reject_reason": "overlaps_temporal_token"})
            else:
                gap = c[p1:t0] if p1 <= t0 else c[t1:p0]
                ok = len(gap) <= 1 and all(ch in FILLER_CHARS for ch in gap)
                rec = {**base, "gap": gap, "gap_len": len(gap),
                       "relation": ("adjacent" if p1 == t0 or t1 == p0 else
                                    "filler_separated"),
                       "order": ("event_before_temporal" if p1 <= t0
                                 else "event_after_temporal")}
                if ok:
                    accepted.append(rec)
                else:
                    rejected.append({**rec, "reject_reason": "window_gap_not_filler"})
            p = c.find(v, p + 1)
    accepted.sort(key=lambda d: (d["gap_len"], d["event_at"], -len(d["event_token"]),
                                 d["event_token"]))
    return accepted, rejected
    # --- END MUTATION SEAM: _r8_event_side_candidates ---


def r8_local_attachment(clause: str, token: str, at: int) -> Tuple[bool, Dict[str, Any]]:
    """R8 判定述語（純函式）。回傳 `(是否成立, 證據)`。

    證據含 primary 命中的 event-side token、temporal token、兩者相對位置、
    填充字元、關係型態（adjacent / filler_separated）與全部候選（含落選原因）。
    """
    if not R8_ENABLED:  # 🔴 MUTATION SEAM M1 著陸點
        return False, {"rule": R8_LOCAL_EVENT_ATTACHMENT, "evaluated": False,
                       "reason": "r8_disabled", "primary": None,
                       "matches": [], "rejected": []}
    matches, rejected = _r8_event_side_candidates(clause, token, at)
    ev: Dict[str, Any] = {
        "rule": R8_LOCAL_EVENT_ATTACHMENT,
        "evaluated": True,
        "clause": clause,
        "temporal_token": token,
        "temporal_at": at,
        "primary": matches[0] if matches else None,
        "matches": matches,
        "rejected": rejected,
        "reason": ("local_event_attachment" if matches else "no_local_event_side"),
    }
    return bool(matches), ev


# ══════════════════════════════════════════════════════════════
# 兩段式（token 層 → frame 層）；R1–R7 逐字沿用 A1，只在 R6/R7 之前插入 R8
# ══════════════════════════════════════════════════════════════

def judge_token_at_a2(text: str, token: str, at: int,
                      exclude_hit: bool = False) -> Dict[str, Any]:
    """對 token 在 `at` 的單一出現做 A1＋R8 判定。

    R1–R5／R7 的判定**完全委派**給 `a1.judge_token_at`（唯讀 import，不重寫），
    因此「R8 不得覆寫 R1–R5」與「R6/R7 逐字不動」是**結構性**成立的，不靠人工紀律。

    插入點：僅當 A1 已 fall-through 到 R6（`undecided` + `R6_NO_ATTACHMENT`）時，
    R8 才有機會把該 token 判為 `kept`。R7 命中時本來就是 `kept`，R8 不再介入
    （R8 的證據仍記錄為 `evaluated: false`，以免暗示它參與了該判定）。
    """
    base = a1.judge_token_at(text, token, at, exclude_hit=exclude_hit)
    clause, _span = clause_span_at(text, at)
    cat = at - _span[0]

    r8_hit = False
    r8_ev: Dict[str, Any] = {
        "rule": R8_LOCAL_EVENT_ATTACHMENT, "evaluated": False,
        "reason": "not_reached", "primary": None, "matches": [], "rejected": [],
    }
    verdict = base["verdict"]
    rules = list(base["rules"])
    reason = base["reason"]

    if base["reason"] == a1.R6_NO_ATTACHMENT:
        r8_hit, r8_ev = r8_local_attachment(clause, token, cat)
        if r8_hit:
            verdict, reason = KEPT, R8_LOCAL_EVENT_ATTACHMENT
            rules = list(base["rules"]) + [R8_LOCAL_EVENT_ATTACHMENT]
    elif base["reason"] in (a1.R7_ATTACHED, a1.R1_ACTION_TOKEN, a1.R2_EXCLUDE_TOKEN,
                            a1.R3_ATTRIBUTIVE_NOUN, a1.R4_SIMILE_FRAME,
                            a1.R5_NEGATION_FRAME):
        r8_ev = {**r8_ev, "reason": f"not_reached_{base['reason']}"}
    else:  # unsplittable_clause / absent
        r8_ev = {**r8_ev, "reason": f"not_reached_{base['reason']}"}

    return {"verdict": verdict, "rules": rules, "clause": clause,
            "reason": reason, "a1_verdict": base["verdict"],
            "a1_rules": list(base["rules"]), "a1_reason": base["reason"],
            "a2_decided_by": (R8_LOCAL_EVENT_ATTACHMENT if reason ==
                              R8_LOCAL_EVENT_ATTACHMENT else base["reason"]),
            "r8": r8_ev}


def evaluate_token_a2(text: str, token: str, exclude_hit: bool = False) -> Dict[str, Any]:
    """對 token 的**所有**出現逐一分句判定，取最嚴格結果（嚴格度序沿用 A1）。"""
    t = text or ""
    best: Optional[Dict[str, Any]] = None
    for at in a1._all_occurrences(t, token):  # noqa: SLF001 — 唯讀重用 A1 的 occurrence 掃描
        cand = judge_token_at_a2(t, token, at, exclude_hit=exclude_hit)
        cand["at"] = at
        if best is None or a1._STRICTNESS[cand["verdict"]] < a1._STRICTNESS[best["verdict"]]:  # noqa: SLF001
            best = cand
    if best is None:
        return {"verdict": UNDECIDED, "rules": [], "clause": "", "reason": "absent",
                "at": -1, "a1_verdict": UNDECIDED, "a1_rules": [], "a1_reason": "absent",
                "a2_decided_by": "absent",
                "r8": {"rule": R8_LOCAL_EVENT_ATTACHMENT, "evaluated": False,
                       "reason": "absent", "primary": None, "matches": [],
                       "rejected": []}}
    return best


def apply_a2(text: str) -> Dict[str, Any]:
    """對單一 `raw_text` 套用 A2（**arm-blind**：輸入只有回應文字）。

    frame 層彙總**逐字沿用** `a1.summarize`（唯讀 import）。
    """
    t = text or ""
    v1_frame = extract_temporal_frame(t)  # frozen v1，唯讀
    if v1_frame == "unframed":
        return {"v1_frame": v1_frame, "T": [], "token_verdicts": [],
                "a2_verdict": UNDECIDED, "a2_frame": "unframed", "rules": [],
                "reason": "v1_unframed"}

    tokens = a1.trigger_tokens(v1_frame, t)
    if not tokens:
        return {"v1_frame": v1_frame, "T": [], "token_verdicts": [],
                "a2_verdict": UNDECIDED, "a2_frame": "unframed", "rules": [],
                "reason": "empty_T"}

    exclude_hit = a1.exclude_hit_for(v1_frame, t)
    token_verdicts: List[Dict[str, Any]] = []
    for tok in tokens:
        v = evaluate_token_a2(t, tok, exclude_hit=exclude_hit)
        prim = v["r8"].get("primary") or {}
        token_verdicts.append({
            "token": tok,
            "verdict": v["verdict"],
            "rules": v["rules"],
            "clause": v["clause"][:60],
            "a1_verdict": v["a1_verdict"],
            "a1_reason": v["a1_reason"],
            "a2_decided_by": v["a2_decided_by"],
            "r8_evaluated": v["r8"].get("evaluated", False),
            "r8_reason": v["r8"].get("reason"),
            "r8_event_token": prim.get("event_token"),
            "r8_temporal_token": prim.get("temporal_token"),
            "r8_event_at": prim.get("event_at"),
            "r8_temporal_at": prim.get("temporal_at"),
            "r8_gap": prim.get("gap"),
            "r8_relation": prim.get("relation"),
            "r8_order": prim.get("order"),
            "r8_match_count": len(v["r8"].get("matches") or []),
            "r8_all_matches": list(v["r8"].get("matches") or []),
        })

    a2 = a1.summarize(token_verdicts)  # 逐字沿用 A1 的 frame 層真值表
    return {
        "v1_frame": v1_frame,
        "T": tokens,
        "token_verdicts": token_verdicts,
        "a2_verdict": a2,
        "a2_frame": v1_frame if a2 == KEPT else "unframed",
        "rules": sorted({r for tv in token_verdicts for r in tv["rules"]}),
        "r8_rescued": any(tv["a2_decided_by"] == R8_LOCAL_EVENT_ATTACHMENT
                          for tv in token_verdicts),
        "reason": "frame_summary",
    }


def r8_evidence(text: str) -> List[Dict[str, Any]]:
    """回傳該筆回應中**所有由 R8 成立**的命中（GO-C 證據本體）。

    每筆含 temporal token、event-side token、兩者在小句內的相對位置、填充字元、
    關係型態、命中小句原文，以及該 token 的**全部**候選（不只 primary）。
    """
    out: List[Dict[str, Any]] = []
    for tv in apply_a2(text)["token_verdicts"]:
        if tv["a2_decided_by"] != R8_LOCAL_EVENT_ATTACHMENT:
            continue
        base = {k: tv[k] for k in
                ("token", "clause", "r8_event_token", "r8_temporal_token",
                 "r8_event_at", "r8_temporal_at", "r8_gap", "r8_relation",
                 "r8_order")}
        for m in (tv.get("r8_all_matches") or []):
            out.append({**base, "all_matches": len(tv["r8_all_matches"]),
                        "match": m})
    return out


__all__ = [
    "EVENT_VERBS", "FILLER_CHARS", "R8_ENABLED", "R8_LOCAL_EVENT_ATTACHMENT",
    "ACTION_TOKENS", "VETOED", "KEPT", "UNDECIDED",
    "clause_span_at", "r8_local_attachment", "judge_token_at_a2",
    "evaluate_token_a2", "apply_a2", "r8_evidence",
]
