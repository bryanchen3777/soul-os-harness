"""TA-2 v2-A2 — Local Event-Attachment 離線重播。**0 次 LLM 呼叫，0 網路。**

機制規格：`docs/TA2-V2A2-MECHANISM-SPEC.md`（R8 唯讀新增，A1 R1–R7 逐字不動）
A1 契約：`docs/TA2-V2A-FEASIBILITY-CONTRACT.md`（A1 負面基線，**保留不覆蓋**）

🔴 **本檔不是 Gate 2 判定。** 所有輸出標記 `feasibility_only: true`，
   Gate 2 全程維持 **INCONCLUSIVE**（Owner 工單 / 規格 §8）。

🔴 **不修改任何 frozen 檔**：`harness/run_ta2a_gate2.py` 只被 import（唯讀），
   `harness/ta2v2a_attachment.py`（A1）亦唯讀。唯一寫入是
   `data/harness_out/tl12/ta2v2a2_*` 兩個新檔（A1 的 `ta2v2a_*` 一律不碰）。

🔴 **arm-blindness**：`replay_texts()` 的輸入**只有** `list[str]`；
   `arm`／`probe_id` 只由**獨立的** `build_audit()` 依索引接回。

用法：`.venv\\Scripts\\python.exe harness/run_ta2v2a2_replay.py`
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import ta2v2a2_event_attachment as a2  # noqa: E402
from harness import ta2v2a_attachment as a1  # noqa: E402
# 🔴 A1 重播腳本唯讀 import：**重用**其 corpus 載入與 frozen v1 指標公式，
#    避免二次實作造成的靜默漂移（A1 的 `load_corpus` 必須先由 `_arms_by_probe()` 填序）。
from harness.run_ta2v2a_feasibility import (  # noqa: E402
    _arms_by_probe,  # noqa: SLF001
    load_corpus,
    recompute_v1_metrics,
)

CORPUS = REPO / "data" / "harness_out" / "tl12" / "ta2a_gate2_four_arm_real.json"
FROZEN_V1 = REPO / "harness" / "run_ta2a_gate2.py"
FROZEN_A1 = REPO / "harness" / "ta2v2a_attachment.py"
A1_OUT_JSON = REPO / "data" / "harness_out" / "tl12" / "ta2v2a_feasibility.json"
A1_OUT_MD = REPO / "data" / "harness_out" / "tl12" / "ta2v2a_audit.md"
OUT_JSON = REPO / "data" / "harness_out" / "tl12" / "ta2v2a2_event_attachment.json"
OUT_AUDIT = REPO / "data" / "harness_out" / "tl12" / "ta2v2a2_audit.md"

#: 與 A1 契約 §7 相同的凍結基線（重播前後都必須相符）
FROZEN_BASELINE = {
    "corpus_sha256": "f5690acbe05efa08e464ae54b6bb0a73c8f76130dbed7a2b7dd87208c1fcffe1",
    "v1_script_sha256": "bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253",
    "a1_module_sha256_at_a2_start": None,  # 執行時填入（自證 A1 未被改動）
    "a1_output_json_sha256_at_a2_start": None,
    "a1_output_md_sha256_at_a2_start": None,
}

SPEC_REF = "docs/TA2-V2A2-MECHANISM-SPEC.md"
A1_CONTRACT_REF = "docs/TA2-V2A-FEASIBILITY-CONTRACT.md"
DESIGN_REF = "docs/TA2-GATE2-MEASUREMENT-CONTRACT-V2-DESIGN.md#9"

#: 規格 §5 預登記的抗過度擬合硬門檻（**事前**固定，見到結果後不得調整）
MAX_SINGLE_EVENT_VERB_SHARE = 0.40
MAX_LUNCH_SHARE_WITHOUT_OTHER_RECOVERY = 0.60


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ══════════════════════════════════════════════════════════════
# 步驟 1–2：arm-blind 重播核心
# ══════════════════════════════════════════════════════════════

def replay_texts(raw_texts: Sequence[str]) -> List[Dict[str, Any]]:
    """對每筆 `raw_text` 套用 A1 與 A2。**本函式看不到 arm / probe_id / 注入行。**"""
    return [{"a1": a1.apply_a1(t or ""), "a2": a2.apply_a2(t or "")} for t in raw_texts]


def build_audit(results: Sequence[Dict[str, Any]],
                meta: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    """把 blind 重播結果與中繼資料**依索引**接回（唯一接回 arm 的地方）。"""
    assert len(results) == len(meta), "索引長度不符"
    rows: List[Dict[str, Any]] = []
    for i, (res, m) in enumerate(zip(results, meta)):
        a1r, a2r = res["a1"], res["a2"]
        r8 = [{"token": tv["token"], "event_token": tv["r8_event_token"],
               "event_at": tv["r8_event_at"], "temporal_token": tv["r8_temporal_token"],
               "temporal_at": tv["r8_temporal_at"], "gap": tv["r8_gap"],
               "relation": tv["r8_relation"], "order": tv["r8_order"],
               "clause": tv["clause"], "n_matches": tv["r8_match_count"]}
              for tv in a2r["token_verdicts"]
              if tv["a2_decided_by"] == a2.R8_LOCAL_EVENT_ATTACHMENT]
        rows.append({
            "index": i,
            "obs_id": f"{m['probe_id']}/{m['arm']}/run{m['run_index']}",
            "probe_key": m["probe_key"],
            "probe_id": m["probe_id"],
            "arm": m["arm"],
            "run_index": int(m["run_index"]),
            "v1_frame": a2r["v1_frame"],
            "a1_frame": a1r["v2_frame"],
            "a2_frame": a2r["a2_frame"],
            "T": a2r["T"],
            "a1_verdict": a1r["a1_verdict"],
            "a2_verdict": a2r["a2_verdict"],
            "a2_frame_changed_vs_v1": a2r["v1_frame"] != a2r["a2_frame"],
            "a2_frame_changed_vs_a1": a1r["v2_frame"] != a2r["a2_frame"],
            "r8_rescued": bool(r8),
            "r8_hits": r8,
            "token_verdicts": [
                {"token": tv["token"], "verdict": tv["verdict"], "rules": tv["rules"],
                 "clause": tv["clause"], "a1_verdict": tv["a1_verdict"],
                 "a1_reason": tv["a1_reason"], "a2_decided_by": tv["a2_decided_by"],
                 "r8_evaluated": tv["r8_evaluated"], "r8_reason": tv["r8_reason"],
                 "r8_event_token": tv["r8_event_token"],
                 "r8_temporal_token": tv["r8_temporal_token"],
                 "r8_event_at": tv["r8_event_at"],
                 "r8_temporal_at": tv["r8_temporal_at"],
                 "r8_gap": tv["r8_gap"], "r8_relation": tv["r8_relation"],
                 "r8_order": tv["r8_order"], "r8_match_count": tv["r8_match_count"]}
                for tv in a2r["token_verdicts"]
            ],
            "rule_id": a2r["rules"],
            "raw_text": None,  # 由呼叫端填入完整原文（不截斷）
        })
    return rows


# ══════════════════════════════════════════════════════════════
# 步驟 3：量化指標
# ══════════════════════════════════════════════════════════════

def pollution_sets(rows: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """**與 A1 契約 §4.2 逐字同義**的污染集 proxy（資料檢視前即已鎖定，不得調整）。"""
    sleep_set, simile_set = [], []
    for r in rows:
        text = r["raw_text"] or ""
        if r["v1_frame"] != "night":
            continue
        if any(t in text for t in a1.ACTION_TOKENS):
            sleep_set.append(r)
        if any(m in text for m in a1.SIMILE_MARKERS):
            simile_set.append(r)
    return {"sleep": sleep_set, "night_simile": simile_set}


def _precision(rows: Sequence[Dict[str, Any]], field: str) -> Optional[float]:
    """`|{obs ∈ pollution_X : field == "unframed"}| / |pollution_X|`（分母 0 ⇒ None）。

    🔴 **A1 報告缺陷（記錄，不修）**：A1 的 `_precision()` 寫成 `len(rows)/len(rows)`，
       恆為 1.0。本檔給出**真實**比率，並同時以同一公式重算 A1 的數字供比較。
    """
    return (sum(1 for r in rows if r[field] == "unframed") / len(rows)) if rows else None


def compute_metrics(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    v1_non = [r for r in rows if r["v1_frame"] != "unframed"]
    a1_non = [r for r in rows if r["a1_frame"] != "unframed"]
    a2_non = [r for r in rows if r["a2_frame"] != "unframed"]
    poll = pollution_sets(rows)

    ledger = collections.Counter()
    for r in rows:
        for tv in r["token_verdicts"]:
            for rule in tv["rules"]:
                ledger[rule] += 1

    rescued = [r for r in rows if r["r8_rescued"]]
    lost_vs_v1 = [r for r in v1_non if r["a2_frame"] == "unframed"]
    lost_all_vetoed = [r for r in lost_vs_v1 if r["a2_verdict"] == "vetoed"]
    lost_undecided = [r for r in lost_vs_v1 if r["a2_verdict"] == "undecided"]
    regressed_vs_a1 = [r for r in rows if r["a1_frame"] != "unframed"
                       and r["a2_frame"] == "unframed"]
    retained = a2_non
    retained_with_r8 = [r for r in retained if r["r8_rescued"]]
    retained_without_r8 = [r for r in retained if not r["r8_rescued"]]

    verb_counts = collections.Counter()
    for r in rescued:
        for h in r["r8_hits"]:
            verb_counts[h["event_token"]] += 1
    lunch_rescued = [r for r in rescued if r["a1_frame"] != "lunch"]
    non_lunch_rescued = [r for r in rescued if r["a1_frame"] == "lunch"]
    top_share = ((max(verb_counts.values()) / sum(verb_counts.values()))
                 if verb_counts else 0.0)
    lunch_share = (len(lunch_rescued) / len(rescued)) if rescued else 0.0

    # GO-A：lunch（meal_timing × ON_correct）真陽性 frame 的三版對照
    lunch = [r for r in rows if r["probe_key"] == "meal_timing"
             and r["arm"] == "ON_correct"]
    lunch_v1 = [r for r in lunch if r["v1_frame"] == "lunch"]
    lunch_a1 = [r for r in lunch if r["a1_frame"] == "lunch"]
    lunch_a2 = [r for r in lunch if r["a2_frame"] == "lunch"]
    aux = {}
    for pid in ("meal_timing", "meeting_timing", "night_rest"):
        sel = [r for r in rows if r["probe_key"] == pid]
        aux[f"{pid}:v1_framed"] = sum(1 for r in sel if r["v1_frame"] != "unframed")
        aux[f"{pid}:A1_framed"] = sum(1 for r in sel if r["a1_frame"] != "unframed")
        aux[f"{pid}:A2_framed"] = sum(1 for r in sel if r["a2_frame"] != "unframed")
    for fr in ("siesta", "late_night_meal", "night", "lunch"):
        aux[f"frame_{fr}:v1"] = sum(1 for r in rows if r["v1_frame"] == fr)
        aux[f"frame_{fr}:A1"] = sum(1 for r in rows if r["a1_frame"] == fr)
        aux[f"frame_{fr}:A2"] = sum(1 for r in rows if r["a2_frame"] == fr)

    m_v1 = recompute_v1_metrics(rows, "v1_frame")
    m_a1 = recompute_v1_metrics(rows, "a1_frame")
    m_a2 = recompute_v1_metrics(rows, "a2_frame")

    p_sleep = {"n": len(poll["sleep"]),
               "A1": _precision(poll["sleep"], "a1_frame"),
               "A2": _precision(poll["sleep"], "a2_frame"),
               "texts": sorted({r["raw_text"] for r in poll["sleep"]}),
               "retained_by_A2": sorted(r["obs_id"] for r in poll["sleep"]
                                        if r["a2_frame"] != "unframed")}
    p_simile = {"n": len(poll["night_simile"]),
                "A1": _precision(poll["night_simile"], "a1_frame"),
                "A2": _precision(poll["night_simile"], "a2_frame"),
                "texts": sorted({r["raw_text"] for r in poll["night_simile"]}),
                "retained_by_A2": sorted(r["obs_id"] for r in poll["night_simile"]
                                         if r["a2_frame"] != "unframed")}

    go_a = {
        "lunch_genuine": {
            "v1": len(lunch_v1), "A1": len(lunch_a1), "A2": len(lunch_a2),
            "recovered_vs_A1": len(lunch_a2) - len(lunch_a1),
            "recovered_obs": [r["obs_id"] for r in lunch_a2],
            "evidence": [{"obs_id": r["obs_id"], "hits": r["r8_hits"]} for r in lunch_a2],
        },
        "other_genuine_recovered": [
            {"obs_id": r["obs_id"], "A1_frame": r["a1_frame"], "A2_frame": r["a2_frame"],
             "hits": r["r8_hits"]} for r in non_lunch_rescued],
        "lunch_special_casing": None,
        "lunch_special_casing_note": (
            "無。R8 對**每個** temporal token 一視同仁（封閉詞表 ＋ 窗口規則，"
            "模組內不含 probe／arm／個別觀測字串；由 tests 靜態斷言）。"
            "lunch 之所以恢復，是因為 `吃` 與 `午餐` 在真實語料中**相鄰**。"),
        "met": bool(lunch_a2) and len(lunch_a2) > len(lunch_a1),
        "definition": ("GO-A：lunch 真陽性 frame 必須明顯恢復（> A1），且不得只針對 lunch；"
                       "其他幀的恢復一併報告。**不設保底門檻**——負面結果允許。"),
    }
    go_b = {
        "sleep_action_pollution": p_sleep,
        "night_metaphor_simile_pollution": p_simile,
        "sleep_precision_drop_vs_A1": (None if p_sleep["A1"] is None
                                        or p_sleep["A2"] is None
                                        else p_sleep["A2"] < p_sleep["A1"]),
        "simile_precision_drop_vs_A1": (None if p_simile["A1"] is None
                                         or p_simile["A2"] is None
                                         else p_simile["A2"] < p_simile["A1"]),
        "meets_absolute_threshold_1_0": (p_sleep["A2"] is not None
                                        and p_simile["A2"] is not None
                                        and p_sleep["A2"] >= 1.0
                                        and p_simile["A2"] >= 1.0),
        "owner_stop_condition_triggered": (
            "precision < 1.0 ⇒ STOP 並回報" if (
                p_sleep["A2"] is None or p_simile["A2"] is None
                or p_sleep["A2"] < 1.0 or p_simile["A2"] < 1.0) else None),
        "met": (p_sleep["A2"] is not None and p_simile["A2"] is not None
                and p_sleep["A2"] >= 1.0 and p_simile["A2"] >= 1.0
                and not (p_sleep["A2"] < (p_sleep["A1"] or 0))
                and not (p_simile["A2"] < (p_simile["A1"] or 0))),
        "definition": ("GO-B（Owner 工單逐字）：兩個污染 proxy 的 precision 皆須 **≥ 1.0**，"
                       "且相較 A1 **不得下降**。A1 JSON 內的 `precision` 欄位是自除恆等式"
                       "（缺陷），故本檔以正確公式同時重算 A1 與 A2。"),
        "reading_note": ("兩種讀法並陳：(a) 相對 A1 **無下降**（A1 與 A2 皆 5/7）；"
                         "(b) 絕對門檻 **< 1.0**（5/7）⇒ GO-B 不成立，且觸發 Owner "
                         "停止條件。**採 (b) 為準**（工單把 ≥1.0 列為 acceptance）。"),
    }
    go_c = {
        "every_R8_attributed_frame_has_event_side_evidence": all(
            bool(r["r8_hits"]) for r in retained_with_r8),
        "every_retained_frame_has_event_side_evidence": len(retained_without_r8) == 0,
        "retained_frames_total": len(retained),
        "retained_frames_with_r8_event_side_evidence": len(retained_with_r8),
        "retained_frames_without_r8_evidence": [
            {"obs_id": r["obs_id"], "A2_frame": r["a2_frame"],
             "decided_by": sorted({tv["a2_decided_by"] for tv in r["token_verdicts"]})}
            for r in retained_without_r8],
        "single_event_verb_share_max": top_share,
        "single_event_verb_share_threshold": MAX_SINGLE_EVENT_VERB_SHARE,
        "single_event_verb_counts": dict(sorted(verb_counts.items())),
        "single_event_verb_threshold_met": top_share <= MAX_SINGLE_EVENT_VERB_SHARE,
        "lunch_share_of_rescues": lunch_share,
        "lunch_share_threshold": MAX_LUNCH_SHARE_WITHOUT_OTHER_RECOVERY,
        "other_frame_recovery_exists": bool(non_lunch_rescued),
        "lunch_specialisation_threshold_met": (
            bool(non_lunch_rescued) or lunch_share <= MAX_LUNCH_SHARE_WITHOUT_OTHER_RECOVERY),
        "cross_clause_rescues": 0,  # C3 由 clause 切分結構性保證：v 與 t 必同小句
        "definition": ("GO-C：每個被 R8 救回的 frame 都要能指出 event-side + temporal-side "
                       "的 local relation；任一單一 event verb 佔救回 frame > 40% ⇒ FAIL；"
                       "lunch 佔 > 60% 且其他幀無恢復 ⇒ FAIL。"),
    }
    go_c["met"] = bool(
        go_c["every_R8_attributed_frame_has_event_side_evidence"]
        and go_c["every_retained_frame_has_event_side_evidence"]
        and go_c["single_event_verb_threshold_met"]
        and go_c["lunch_specialisation_threshold_met"])

    # 規格 §3 ↔ §4.1 內部不一致的**實測**證據（記錄，**不修**）
    deep_night = [r for r in rows if r["v1_frame"] == "night"
                  and "尾聲" in (r["raw_text"] or "")]
    if deep_night and any(r["a2_frame"] != "unframed" for r in deep_night):
        spec_conflicts = [{
            "id": "SPEC-3-vs-4.1",
            "spec_clause": ("§3（R6/R7 逐字不動）vs §4.1"
                            "（「深夜已經到尾聲了」應退回 undecided）"),
            "observed": ("該類 frame 在 A2 仍為 kept，機制理由是 A1 的 R7 話語標記 `到`，"
                         "而非 R8 的 event-side 關係。"),
            "evidence_obs": [r["obs_id"] for r in deep_night],
            "resolution_owner": ("需 Owner／主大腦裁定：R6/R7 的 marker 落底是否保留。"
                                 "執行者**未**自行改動機制。"),
        }]
    else:
        spec_conflicts = []

    # 規格 §6 M2 的**可執行性**限制（記錄，不修）：A1 的 `ATTACHMENT_MARKERS` 涵蓋
    # 到/這/那/吧，而 R8 只在 A1 已 fall-through 到 R6（小句內無 marker）時才被評估，
    # 因此「子句含話語標記」型的 R8 變異在行為上被 R7 完全遮蔽 ⇒ M2 的行為臂不可觀察。
    r8_evaluated_rows = [r for r in rows if any(tv["r8_evaluated"]
                                                for tv in r["token_verdicts"])]
    m2_shadowing = {
        "r8_evaluated_observations": len(r8_evaluated_rows),
        "clause_without_A1_marker_guaranteed": all(
            not any(mk in tv["clause"] for mk in a1.ATTACHMENT_MARKERS)
            for r in r8_evaluated_rows for tv in r["token_verdicts"]
            if tv["r8_evaluated"]),
        "M2_enforcement": "static AST contract only（行為臂被 R7 遮蔽，見 architectural_findings）",
    }

    return {
        "feasibility_only": True,
        "llm_calls": 0,
        "network_calls": 0,
        "spec": SPEC_REF,
        "a1_contract": A1_CONTRACT_REF,
        "design": DESIGN_REF,
        "n_observations": len(rows),
        "v1_frames_non_unframed": len(v1_non),
        "A1_frames_non_unframed": len(a1_non),
        "A2_frames_non_unframed": len(a2_non),
        "A2_minus_A1_frames": len(a2_non) - len(a1_non),
        "noise_proxy_A1": ((sum(1 for r in rows if r["v1_frame"] != r["a1_frame"])
                            / len(v1_non)) if v1_non else None),
        "noise_proxy_A2": ((sum(1 for r in rows if r["v1_frame"] != r["a2_frame"])
                            / len(v1_non)) if v1_non else None),
        "recall_loss_vs_v1_A1": len(v1_non) - len(a1_non),
        "recall_loss_vs_v1_A2": len(v1_non) - len(a2_non),
        "recall_loss_breakdown_A2": {
            "all_vetoed": {"count": len(lost_all_vetoed),
                           "obs": [r["obs_id"] for r in lost_all_vetoed]},
            "undecided_no_local_event_side": {
                "count": len(lost_undecided),
                "obs": [r["obs_id"] for r in lost_undecided]},
            "R8_rescued": {
                "count": len(rescued),
                "obs": [{"obs_id": r["obs_id"], "A1_frame": r["a1_frame"],
                         "A2_frame": r["a2_frame"], "hits": r["r8_hits"]}
                        for r in rescued]},
        },
        "regressions_vs_A1": [r["obs_id"] for r in regressed_vs_a1],
        "additive_check_note": ("R8 為 additive（A1 R6/R7 逐字保留），因此 A2 framed 集合"
                                "**必須**是 A1 framed 集合的子集擴張；`regressions_vs_A1` "
                                "應為空。"),
        "precision_on_known_pollution": {
            "definition_note": ("與 A1 契約 §4.2 同義的 proxy；**分母固定**，命中率低"
                                "**不得**用來調整集合定義。"),
            "A1_precision_defect_note": (
                "🔴 A1 `harness/run_ta2v2a_feasibility.py:_precision()` 為 "
                "`len(rows)/len(rows)`（自除），恆回傳 1.0；A1 JSON 中 "
                "`precision_on_known_pollution.*.precision` 因此**不是真實比率**。"
                "本檔以正確公式同時重算 A1 與 A2，**未修改 A1 檔案**。"),
        },
        "veto_ledger_by_rule_A2": dict(sorted(ledger.items())),
        "auxiliary_observations": aux,
        "v1_metrics": m_v1,
        "A1_metrics": m_a1,
        "A2_metrics": m_a2,
        "go_a_recall_recovery": go_a,
        "go_b_precision_preservation": go_b,
        "go_c_mechanism_validity": go_c,
        "spec_conflicts": spec_conflicts,
        "m2_mutation_testability": m2_shadowing,
        "gate2_note": ("🔴 **本檔不含任何 Gate 2 PASS/FAIL 判定。** Gate 2 全程維持 "
                       "**INCONCLUSIVE**（Owner 工單 / 規格 §8）。"),
    }


# ══════════════════════════════════════════════════════════════
# 步驟 4：audit md
# ══════════════════════════════════════════════════════════════

def render_audit_md(rows: Sequence[Dict[str, Any]], m: Dict[str, Any],
                    sha: Dict[str, str]) -> str:
    L: List[str] = []
    A = L.append
    A("# TA-2 v2-A2 — Local Event-Attachment 逐筆 audit")
    A("")
    A("> 🔴 `feasibility_only: true` — **本檔不含任何 Gate 2 PASS/FAIL 判定。**"
      " Gate 2 維持 **INCONCLUSIVE**。")
    A(f"> 機制規格：`{SPEC_REF}`（R8 唯讀新增；A1 R1–R7 逐字不動）。")
    A("> 正式名稱：**local attachment heuristic**（❌ 非 syntactic parsing）。")
    A("")
    A("## 1. 凍結基線（AC1 / AC4）")
    A("")
    A("| 項目 | 重播前 | 重播後 | 相符 |")
    A("|---|---|---|---|")
    A(f"| `sha256(ta2a_gate2_four_arm_real.json)` | `{sha['corpus_before']}` | "
      f"`{sha['corpus_after']}` | {'✅' if sha['corpus_before'] == sha['corpus_after'] else '❌'} |")
    A(f"| `sha256(harness/run_ta2a_gate2.py)`（frozen v1） | `{sha['v1_before']}` | "
      f"`{sha['v1_after']}` | {'✅' if sha['v1_before'] == sha['v1_after'] else '❌'} |")
    A(f"| `sha256(harness/ta2v2a_attachment.py)`（A1） | `{sha['a1mod_before']}` | "
      f"`{sha['a1mod_after']}` | {'✅' if sha['a1mod_before'] == sha['a1mod_after'] else '❌'} |")
    A(f"| `sha256(ta2v2a_feasibility.json)`（A1 輸出） | `{sha['a1json_before']}` | "
      f"`{sha['a1json_after']}` | {'✅' if sha['a1json_before'] == sha['a1json_after'] else '❌'} |")
    A(f"| `sha256(ta2v2a_audit.md)`（A1 輸出） | `{sha['a1md_before']}` | "
      f"`{sha['a1md_after']}` | {'✅' if sha['a1md_before'] == sha['a1md_after'] else '❌'} |")
    A("")
    A("## 2. 量化摘要")
    A("")
    A("| 指標 | v1 | A1 | A2 |")
    A("|---|---|---|---|")
    A(f"| 非 unframed frame 數 | {m['v1_frames_non_unframed']} | "
      f"{m['A1_frames_non_unframed']} | {m['A2_frames_non_unframed']} |")
    A(f"| 相對 v1 的 recall 損失 | 0 | {m['recall_loss_vs_v1_A1']} | "
      f"{m['recall_loss_vs_v1_A2']} |")
    A(f"| noise proxy | 0 | {m['noise_proxy_A1']} | {m['noise_proxy_A2']} |")
    A(f"| ΔR | {m['v1_metrics']['dR']} | {m['A1_metrics']['dR']} | {m['A2_metrics']['dR']} |")
    A(f"| ΔI | {m['v1_metrics']['dI']} | {m['A1_metrics']['dI']} | {m['A2_metrics']['dI']} |")
    A(f"| DiD | {m['v1_metrics']['did']} | {m['A1_metrics']['did']} | {m['A2_metrics']['did']} |")
    ps, pn = (m["go_b_precision_preservation"]["sleep_action_pollution"],
              m["go_b_precision_preservation"]["night_metaphor_simile_pollution"])
    A(f"| precision（睡污染集 n={ps['n']}） | — | {ps['A1']} | {ps['A2']} |")
    A(f"| precision（夜比喻污染集 n={pn['n']}） | — | {pn['A1']} | {pn['A2']} |")
    A(f"| R8 救回 frame 數 | — | 0 | {m['recall_loss_breakdown_A2']['R8_rescued']['count']} |")
    A(f"| 相較 A1 的退步（應為空） | — | — | {m['regressions_vs_A1'] or '[]'} |")
    A("")
    A("> A1 的 JSON 內 `precision` 欄位是自除恆等式（缺陷，記錄不修）；上表 A1 欄"
      "為本檔以正確公式重算。"
      "")
    A("## 3. GO-A / GO-B / GO-C 判定")
    A("")
    for key, title in (("go_a_recall_recovery", "GO-A Recall recovery"),
                       ("go_b_precision_preservation", "GO-B Precision preservation"),
                       ("go_c_mechanism_validity", "GO-C Mechanism validity")):
        g = m[key]
        A(f"### {title} → **{'PASS' if g['met'] else 'FAIL'}**")
        A("")
        A(f"- 定義：{g['definition']}")
        for k, val in g.items():
            if k in ("definition", "met"):
                continue
            A(f"- `{k}` = {json.dumps(val, ensure_ascii=False)}")
        A("")
    A("## 4. R8 救回的每一個 frame（GO-C 證據本體）")
    A("")
    A("| obs_id | A1_frame | A2_frame | temporal token `t` | event-side `v` | "
      "v@t / t@t | gap | relation | 小句 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for o in m["recall_loss_breakdown_A2"]["R8_rescued"]["obs"]:
        for h in o["hits"]:
            A(f"| `{o['obs_id']}` | {o['A1_frame']} | {o['A2_frame']} | "
              f"`{h['temporal_token']}` | `{h['event_token']}` | "
              f"{h['event_at']} / {h['temporal_at']} | `{h['gap']}` | "
              f"{h['relation']} | {h['clause']} |")
    A("")
    A("## 5. 逐筆 audit（全部 66 筆；`raw_text` 未截斷）")
    A("")
    A("| # | obs_id | v1_frame | A1_frame | A2_frame | T | A1_verdict | A2_verdict | "
      "A2_decided_by | R8 v@t |")
    A("|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        tv = ", ".join(f"{t['token']}:{t['verdict']}" for t in r["token_verdicts"]) or "—"
        db = ", ".join(sorted({t["a2_decided_by"] for t in r["token_verdicts"]})) or "—"
        vv = ", ".join(sorted({str(t["r8_event_token"]) for t in r["token_verdicts"]
                               if t["r8_event_token"]})) or "—"
        A(f"| {r['index']} | `{r['obs_id']}` | {r['v1_frame']} | {r['a1_frame']} | "
          f"{r['a2_frame']} | `{','.join(r['T']) or '—'}` | {r['a1_verdict']} | "
          f"{r['a2_verdict']} | {db} | {vv} |")
    A("")
    A("### 5.1 完整原文（逐筆，未截斷）")
    A("")
    for r in rows:
        tv = ", ".join(f"{t['token']}→{t['verdict']}({t['a2_decided_by']})"
                       for t in r["token_verdicts"]) or "—"
        A(f"- **`{r['obs_id']}`** · v1=`{r['v1_frame']}` · A1=`{r['a1_frame']}` · "
          f"A2=`{r['a2_frame']}` · {tv}")
        A("")
        A("  ```text")
        for ln in (r["raw_text"] or "").splitlines():
            A(f"  {ln}")
        A("  ```")
        A("")
    A("## 6. recall 損失歸因（A2）")
    A("")
    lb = m["recall_loss_breakdown_A2"]
    A("| 類別 | 筆數 | obs_id |")
    A("|---|---|---|")
    A(f"| 全部 token vetoed（precision 過濾） | {lb['all_vetoed']['count']} | "
      f"`{'、'.join(lb['all_vetoed']['obs'])}` |")
    A(f"| undecided（無 local event-side） | "
      f"{lb['undecided_no_local_event_side']['count']} | "
      f"`{'、'.join(lb['undecided_no_local_event_side']['obs'])}` |")
    A(f"| **R8 救回** | {lb['R8_rescued']['count']} | "
      f"`{'、'.join(o['obs_id'] for o in lb['R8_rescued']['obs'])}` |")
    A("")
    A("## 7. 已知污染集（proxy，與 A1 契約 §4.2 同義）")
    A("")
    for title, p in (("睡污染集", ps), ("夜比喻污染集", pn)):
        A(f"### {title}（n={p['n']}；A1 precision={p['A1']}；A2 precision={p['A2']}）")
        A("")
        A(f"- A2 仍保留 frame 的 obs：{p['retained_by_A2'] or '（無）'}")
        for t in p["texts"]:
            A("")
            A("  ```text")
            for ln in (t or "").splitlines():
                A(f"  {ln}")
            A("  ```")
        A("")
    A("## 8. 輔助觀察（每個 probe / 每個 frame 的三版計數）")
    A("")
    for k, val in m["auxiliary_observations"].items():
        A(f"- `{k}` = {val}")
    A("")
    A("## 9. 限制（誠實邊界）")
    A("")
    A("1. **A2 不是 Gate 2 判定**；Gate 2 全程 INCONCLUSIVE。")
    A("2. **本機制是 local lexical-window heuristic，不是 parser**；"
      "它**不能**由詞形匹配宣稱 semantic attribution。")
    A("3. R8 只在 A1 已 fall-through 到 R6 時有機會介入；R1–R5／R7 逐字沿用 A1。")
    A("4. 分句是字串切分（沿用 A1 的 `CLAUSE_SEPARATORS` 封閉清單），"
      "`…`／破折號／換行不在其中。**記錄不修。**")
    A("5. 規格 §4.1 預期的「深夜已經到尾聲了」退回 undecided **未發生**，"
      "原因是 §3 規定 R6/R7 逐字保留（`到` 仍命中 R7）。此為規格 §3 與 §4.1 "
      "之間的**內部不一致**，見 JSON 的 `spec_conflicts`。**執行者未修改機制。**")
    A("")
    return "\n".join(L) + "\n"


# ══════════════════════════════════════════════════════════════

def main() -> int:
    sha = {
        "corpus_before": _sha256(CORPUS), "v1_before": _sha256(FROZEN_V1),
        "a1mod_before": _sha256(FROZEN_A1),
        "a1json_before": _sha256(A1_OUT_JSON), "a1md_before": _sha256(A1_OUT_MD),
    }
    baseline_ok = (sha["corpus_before"] == FROZEN_BASELINE["corpus_sha256"]
                   and sha["v1_before"] == FROZEN_BASELINE["v1_script_sha256"])
    if not baseline_ok:  # 停止條件：凍結基線不符
        print("⛔ 停止條件：凍結基線與預登記不符，中止。", file=sys.stderr)
        return 2

    _arms_by_probe()
    raw_texts, meta, _order = load_corpus()
    results = replay_texts(raw_texts)          # 🔴 arm-blind
    rows = build_audit(results, meta)          # 🔴 獨立 audit 步驟
    for r, text in zip(rows, raw_texts):
        r["raw_text"] = text

    metrics = compute_metrics(rows)

    sha["corpus_after"] = _sha256(CORPUS)
    sha["v1_after"] = _sha256(FROZEN_V1)
    sha["a1mod_after"] = _sha256(FROZEN_A1)
    sha["a1json_after"] = _sha256(A1_OUT_JSON)
    sha["a1md_after"] = _sha256(A1_OUT_MD)
    unchanged = all(sha[k.replace("_before", "_after")] == sha[k]
                    for k in list(sha) if k.endswith("_before"))
    if not unchanged:  # 停止條件：重播前後有任何凍結檔變動
        print("⛔ 停止條件：重播前後凍結檔不一致，中止。", file=sys.stderr)
        return 2

    payload = {
        "feasibility_only": True,
        "llm_calls": 0,
        "network_calls": 0,
        "spec": SPEC_REF,
        "mechanism": {
            "name": "A2 — Local Event-Attachment",
            "not": "NOT syntactic parsing / NOT dependency parsing",
            "new_rule": "R8_LOCAL_EVENT_ATTACHMENT",
            "definition": ("對每個 A1 已判定的 temporal-side token t，"
                           "若同一子句內存在 event-side token v ∈ EVENT_VERBS，"
                           "且 v 與 t 之間 0 個字元或 ≤1 個 FILLER_CHARS 字元，"
                           "且 v 與 t span 不重疊、v ∉ ACTION_TOKENS ⇒ 該 token 判 kept。"),
            "priority": "R1→R2→(unsplittable)→R3→R4→R5→R8→R6/R7（R8 additive，不覆寫 R1–R5）",
            "event_verbs": sorted(a2.EVENT_VERBS),
            "filler_chars": sorted(a2.FILLER_CHARS),
        },
        "frozen_baseline": {**FROZEN_BASELINE, **sha, "all_unchanged": True},
        "metrics": metrics,
        "audit": rows,
        "gate2_note": metrics["gate2_note"],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    OUT_AUDIT.write_text(render_audit_md(rows, metrics, sha),
                         encoding="utf-8", newline="\n")

    g = (metrics["go_a_recall_recovery"], metrics["go_b_precision_preservation"],
         metrics["go_c_mechanism_validity"])
    print("TA-2 v2-A2 local event-attachment replay（0 LLM、0 網路、feasibility_only=true）")
    print(f"  observations                 : {metrics['n_observations']}")
    print(f"  v1 / A1 / A2 非 unframed      : {metrics['v1_frames_non_unframed']} / "
          f"{metrics['A1_frames_non_unframed']} / {metrics['A2_frames_non_unframed']}")
    print(f"  R8 救回 frame                : "
          f"{metrics['recall_loss_breakdown_A2']['R8_rescued']['count']}")
    print(f"  相較 A1 的退步（應為空）      : {metrics['regressions_vs_A1'] or '[]'}")
    print(f"  ΔR/ΔI/DiD  A1                : {metrics['A1_metrics']['dR']}/"
          f"{metrics['A1_metrics']['dI']}/{metrics['A1_metrics']['did']}")
    print(f"  ΔR/ΔI/DiD  A2                : {metrics['A2_metrics']['dR']}/"
          f"{metrics['A2_metrics']['dI']}/{metrics['A2_metrics']['did']}")
    print(f"  GO-A / GO-B / GO-C           : "
          f"{'PASS' if g[0]['met'] else 'FAIL'} / "
          f"{'PASS' if g[1]['met'] else 'FAIL'} / "
          f"{'PASS' if g[2]['met'] else 'FAIL'}")
    print("  🔴 Gate 2 維持 INCONCLUSIVE；本檔不含 Gate 2 判定。")
    print(f"  → {OUT_JSON.relative_to(REPO)}")
    print(f"  → {OUT_AUDIT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
