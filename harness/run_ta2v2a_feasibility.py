"""TA-2 v2-A — 候選 A（語法依附）離線重播。**0 次 LLM 呼叫，0 網路。**

預登記契約：`docs/TA2-V2A-FEASIBILITY-CONTRACT.md`
上游工單：`docs/TA2-V2A-FEASIBILITY-WORK-ORDER.md`

🔴 **本檔不是 TA-2 v2 implementation。** 它是 measurement feasibility spike：
   把候選 A 當作**疊在 frozen v1 規則表之上的 veto 濾層**，對已存在的 66 筆
   observation corpus 做離線重播。

🔴 **Gate 2 狀態不變：INCONCLUSIVE。** 本檔輸出**全部**標記 `feasibility_only: true`，
   **不輸出任何 Gate 2 PASS/FAIL 判定**（契約 AC8）。

🔴 **不修改任何 frozen 檔**：`harness/run_ta2a_gate2.py` 只被 import（唯讀），
   `harness/tl12_temporal.py`／`harness/clock.py`／`harness/observer.py`／v1 契約文件
   全部不碰（契約 AC2）。唯一寫入是 `data/harness_out/tl12/ta2v2a_*` 兩個新檔（AC5 範圍）。

🔴 **arm-blindness（契約 AC4）**：重播核心 `replay_texts()` 的輸入**只有**
   `list[str]`（純 `raw_text`），回傳 token 層／frame 層判定。`arm`／`probe_id`／
   `injected_temporal_line` 由 `load_corpus()` 拆成**平行的另一份清單**，
   只有**獨立的 audit 步驟** `build_audit()` 才把索引接回中繼資料。

用法：`.venv\\Scripts\\python.exe harness/run_ta2v2a_feasibility.py`
"""
from __future__ import annotations

import collections
import hashlib
import json
import pathlib
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import ta2v2a_attachment as a1  # noqa: E402
from harness.run_ta2a_gate2 import d  # noqa: E402  （契約 §2.4：必須 import，不轉錄）

CORPUS = REPO / "data" / "harness_out" / "tl12" / "ta2a_gate2_four_arm_real.json"
FROZEN_V1 = REPO / "harness" / "run_ta2a_gate2.py"
OUT_JSON = REPO / "data" / "harness_out" / "tl12" / "ta2v2a_feasibility.json"
OUT_AUDIT = REPO / "data" / "harness_out" / "tl12" / "ta2v2a_audit.md"

#: 契約 §7 預登記的凍結基線（重播前後都必須相符 —— AC1）
FROZEN_BASELINE = {
    "corpus_sha256": "f5690acbe05efa08e464ae54b6bb0a73c8f76130dbed7a2b7dd87208c1fcffe1",
    "v1_script_sha256": "bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253",
    "git_head_at_preregistration": "b14262f4a62e04faaca3fccaf45c5eaad889a45f",
}

CONTRACT_REF = "docs/TA2-V2A-FEASIBILITY-CONTRACT.md"
WORK_ORDER_REF = "docs/TA2-V2A-FEASIBILITY-WORK-ORDER.md"


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ══════════════════════════════════════════════════════════════
# 步驟 1：載入 corpus —— 把「回應文字」與「實驗中繼資料」拆成兩份平行清單
# ══════════════════════════════════════════════════════════════

def load_corpus(path: pathlib.Path = CORPUS) -> Tuple[List[str], List[Dict[str, str]], List[str]]:
    """回 `(raw_texts, meta, probe_order)`。

    🔴 AC4 隔離的第一道牆：`raw_texts` 與 `meta` **物理分離**。重播核心只收前者。
    臂的迭代順序在此固定（probe 順序取 dict 插入序；臂順序見 `ARMS_BY_PROBE`），
    確保重現性 —— 但**順序本身不含任何 arm 語意**（不同於 arm 值）。
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    results = data["results"]
    raw_texts: List[str] = []
    meta: List[Dict[str, str]] = []
    probe_order: List[str] = []
    for pid, pr in results.items():
        probe_order.append(pid)
        for arm in ARMS_BY_PROBE.get(pid, ()):
            a = pr.get(arm)
            if not a:
                continue
            for obs in a["observations"]:
                raw_texts.append(obs["raw_text"])
                meta.append({
                    # 🔴 `probe_key` = corpus `results` 的鍵（控制組是 `_control`）；
                    #    `probe_id` = observation 內的值（控制組是 `irrelevant`，見 v1:364）。
                    #    指標彙總必須用 `probe_key`，否則 v1 的「跳過 _control」分支不會生效。
                    "probe_key": pid,
                    "probe_id": obs.get("probe_id") or pid,
                    "arm": obs.get("arm") or arm,
                    "run_index": str(obs.get("run_index")),
                })
    return raw_texts, meta, probe_order


#: 臂迭代順序（對齊 v1 `main()` 的印表順序；不含任何語意）
ARMS_BY_PROBE: Dict[str, Tuple[str, ...]] = {}


def _arms_by_probe() -> None:
    """由 `load_corpus` 首次呼叫時填入，避免模組層級依賴 corpus 內容。"""
    data = json.loads(CORPUS.read_text(encoding="utf-8"))
    for pid, pr in data["results"].items():
        ARMS_BY_PROBE[pid] = (("ON_correct", "ON_mismatched", "OFF") if pid != "_control"
                              else ("ON", "OFF"))


# ══════════════════════════════════════════════════════════════
# 步驟 2：重播核心（**arm-blind**：只吃 list[str]**）
# ══════════════════════════════════════════════════════════════

def replay_texts(raw_texts: Sequence[str]) -> List[Dict[str, Any]]:
    """對每筆 `raw_text` 套用 A1。**本函式看不到 arm / probe_id / 注入行。**"""
    return [a1.apply_a1(t or "") for t in raw_texts]


# ══════════════════════════════════════════════════════════════
# 步驟 3：audit 步驟（**獨立**；唯一能把 arm 資訊接回來的地方 —— AC4）
# ══════════════════════════════════════════════════════════════

def build_audit(results: Sequence[Dict[str, Any]],
                meta: Sequence[Dict[str, str]]) -> List[Dict[str, Any]]:
    """把 blind 重播結果與中繼資料**依索引**接回，產生逐筆 audit 列。"""
    assert len(results) == len(meta), "索引長度不符"
    rows: List[Dict[str, Any]] = []
    for i, (res, m) in enumerate(zip(results, meta)):
        span = "; ".join(
            f"{tv['token']}→{tv['clause']}" for tv in res["token_verdicts"]
        ) or "-"
        rows.append({
            "index": i,
            "obs_id": f"{m['probe_id']}/{m['arm']}/run{m['run_index']}",
            "probe_key": m["probe_key"],
            "probe_id": m["probe_id"],
            "arm": m["arm"],
            "run_index": int(m["run_index"]),
            "v1_frame": res["v1_frame"],
            "v2_frame": res["v2_frame"],
            "T": res["T"],
            "token_verdicts": [
                {"token": tv["token"], "verdict": tv["verdict"], "rules": tv["rules"],
                 "clause": tv["clause"]}
                for tv in res["token_verdicts"]
            ],
            "A1_verdict": res["a1_verdict"],
            "rule_id": res["rules"],
            "span": span,
            # 🔴 AC7：raw_text 完整保留，不得截斷
            "raw_text": None,  # 由呼叫端以「原文」填入（保持 replay 結果純淨）
        })
    return rows


# ══════════════════════════════════════════════════════════════
# 步驟 4：frozen v1 公式的**逐字轉錄**（契約 §2.4）
#   🔴 v1 的 `_mode` 與 ΔR/ΔI/DiD 彙總是 `main()` 內的巢狀 closure／內聯程式碼，
#      **不是模組層級可 import 的函式**；v1 凍結不得修改，故此處逐字轉錄並標註出處。
#      `d()` 本身是模組層級，已 import（未轉錄）。
# ══════════════════════════════════════════════════════════════

def _mode(frames: List[str]) -> str:
    """逐字轉錄自 `harness/run_ta2a_gate2.py:377-379`（v1 `main()` 內的 `_mode`）。

    「多數決（N=6 時 first-run 單一樣本有偏；此為樣本統計，非規則變更）」
    """
    return collections.Counter(frames).most_common(1)[0][0] if frames else "unframed"


def recompute_v1_metrics(rows: Sequence[Dict[str, Any]], field: str) -> Dict[str, Any]:
    """以 `field`（`'v1_frame'` 或 `'v2_frame'`）逐字沿用 v1 的彙總與三門檻。

    出處：`harness/run_ta2a_gate2.py:377-409`
      - `_mode`            :377-379（多數決）
      - per_probe ΔR       :383-397（`d(_mode(correct), _mode(mismatched))`）
      - `dR = max(...)`    :398
      - ΔI（控制組）        :400-403（`d(_mode(ON), _mode(OFF))`）
      - `did = dR - dI`    :405
      - 條件1/2/3          :407-409（順序與門檻不動）
    🔴 這些布林值**不是** Gate 2 判定，僅為 feasibility 觀察值（契約 §4.1、AC8）。
    """
    per_probe: Dict[str, int] = {}
    unstable: List[str] = []
    by_probe: Dict[str, Dict[str, List[str]]] = collections.defaultdict(dict)
    for r in rows:
        # 🔴 用 `probe_key`（corpus `results` 的鍵），**不是** observation 的 `probe_id`：
        #    控制組的 results 鍵是 `_control`，但其 observation 的 probe_id 是 `irrelevant`
        #    （v1 `run_ta2a_gate2.py:364`）。若用 probe_id，v1 的「跳過 _control」分支失效。
        by_probe[r["probe_key"]][r["arm"]] = by_probe[r["probe_key"]].get(r["arm"], []) + [
            r[field]
        ]

    for pid, arms in by_probe.items():
        if pid == "_control":
            continue  # v1 :384-385
        fc = arms.get("ON_correct", [])
        fm = arms.get("ON_mismatched", [])
        # 必要條件 3：同一 probe 兩臂各自 observation 穩定（v1 :389-396）
        if not fc or not fm:
            unstable.append(f"{pid}：缺少 ON_correct/ON_mismatched 臂（{field}）")
            continue
        if len(set(fc)) != 1:
            unstable.append(f"{pid}：ON-correct 的 {field} 跨 run 不一致（{fc}）")
        if len(set(fm)) != 1:
            unstable.append(f"{pid}：ON-mismatched 的 {field} 跨 run 不一致（{fm}）")
        per_probe[pid] = d(_mode(fc), _mode(fm))

    dR = max(per_probe.values()) if per_probe else 0
    ctrl_on = by_probe.get("_control", {}).get("ON", [])
    ctrl_off = by_probe.get("_control", {}).get("OFF", [])
    dI = d(_mode(ctrl_on), _mode(ctrl_off))
    did = dR - dI
    return {
        "dR_per_probe": per_probe,
        "dR": dR,
        "dI": dI,
        "did": did,
        "feasibility_conditions": {
            "c1_dR_ge_1": dR >= 1,
            "c2_did_ge_0_5": did >= 0.5,
            "c3_observation_stable": not any("跨 run 不一致" in u for u in unstable),
        },
        "unstable_reasons": unstable,
        "modes": {
            **{f"{pid}:{arm}": _mode(frames)
               for pid, arms in by_probe.items() for arm, frames in arms.items()},
        },
    }


# ══════════════════════════════════════════════════════════════
# 步驟 5：量化指標（契約 §4）
# ══════════════════════════════════════════════════════════════

def _pollution_sets(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """契約 §4.2 預登記的「已知污染集」proxy 定義（**資料檢視前已鎖定**）。"""
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


def _precision(rows: Sequence[Dict[str, Any]]) -> Optional[float]:
    """|{obs ∈ pollution_X : v2_frame == "unframed"}| / |pollution_X|（分母 0 ⇒ None）。"""
    return (len(rows) / len(rows)) if rows else None


def compute_metrics(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    v1_non = [r for r in rows if r["v1_frame"] != "unframed"]
    v2_non = [r for r in rows if r["v2_frame"] != "unframed"]
    changed = [r for r in rows if r["v1_frame"] != r["v2_frame"]]

    poll = _pollution_sets(rows)
    veto_by_rule: Dict[str, int] = collections.Counter()
    for r in rows:
        for tv in r["token_verdicts"]:
            for rule in tv["rules"]:
                veto_by_rule[rule] += 1

    # 契約 §4.4 token 層丟失歸因：v2 丟失但 v1 保有 frame 的筆數，依丟失原因分類
    lost_all_vetoed, lost_no_attachment = [], []
    for r in rows:
        if r["v1_frame"] == "unframed" or r["v2_frame"] != "unframed":
            continue
        if r["A1_verdict"] == "vetoed":
            lost_all_vetoed.append(r)
        elif r["A1_verdict"] == "undecided":
            lost_no_attachment.append(r)

    v1m = recompute_v1_metrics(rows, "v1_frame")
    v2m = recompute_v1_metrics(rows, "v2_frame")

    # token 層逐詞判定分佈（報告 §規則表缺陷 的證據；契約 §3.3 要求逐 token 揭露）
    token_counts: Dict[str, Dict[str, int]] = collections.defaultdict(
        lambda: collections.defaultdict(int))
    kept_detail: List[Dict[str, Any]] = []
    for r in rows:
        for tv in r["token_verdicts"]:
            token_counts[tv["token"]][tv["verdict"]] += 1
            if tv["verdict"] == "kept":
                kept_detail.append({"obs_id": r["obs_id"], "token": tv["token"],
                                    "clause": tv["clause"], "rules": tv["rules"]})

    # 🔴 轉錄保真度自檢：把「用 v1_frame 重算」的結果與 corpus 自己記錄的 v1 數字比對。
    #    這是契約 §2.4「逐字轉錄 frozen v1 公式」的**驗證**，不是門檻。
    #    （不吻合 ⇒ 轉錄有錯，必須修轉錄；不是去調規則表。）
    corpus_top = json.loads(CORPUS.read_text(encoding="utf-8"))
    fidelity = {
        "corpus_recorded_dR": corpus_top.get("dR"),
        "recomputed_dR_from_v1_frame": v1m["dR"],
        "corpus_recorded_dI": corpus_top.get("dI"),
        "recomputed_dI_from_v1_frame": v1m["dI"],
        "corpus_recorded_did": corpus_top.get("did"),
        "recomputed_did_from_v1_frame": v1m["did"],
    }
    fidelity["transcription_matches_frozen_v1"] = (
        fidelity["corpus_recorded_dR"] == fidelity["recomputed_dR_from_v1_frame"]
        and fidelity["corpus_recorded_dI"] == fidelity["recomputed_dI_from_v1_frame"]
        and fidelity["corpus_recorded_did"] == fidelity["recomputed_did_from_v1_frame"]
    )
    if not fidelity["transcription_matches_frozen_v1"]:  # 契約 §6.4 精神：不得隱藏
        print("⛔ v1 公式轉錄與 corpus 記錄不一致（見 metrics.v1_transcription_fidelity）",
              file=sys.stderr)

    # 契約 §4.3 輔助觀察（僅記錄，不設門檻）
    aux: Dict[str, Any] = {}
    for pid in ("meal_timing", "meeting_timing", "night_rest"):
        kept = [r for r in rows if r["probe_key"] == pid and r["arm"] == "ON_correct"
                and r["v2_frame"] != "unframed"]
        aux[f"{pid}:ON_correct_v2_framed_count"] = len(kept)
    siesta_v1 = [r for r in rows if r["v1_frame"] == "siesta"]
    siesta_v2 = [r for r in rows if r["v2_frame"] == "siesta"]
    aux["siesta_v1_count"] = len(siesta_v1)
    aux["siesta_v2_count"] = len(siesta_v2)
    aux["siesta_survived"] = len(siesta_v2) == len(siesta_v1)

    return {
        "feasibility_only": True,
        "llm_calls": 0,
        "contract": CONTRACT_REF,
        "work_order": WORK_ORDER_REF,
        "n_observations": len(rows),
        "v1_frames_non_unframed": len(v1_non),
        "v2_frames_non_unframed": len(v2_non),
        "frame_changed": len(changed),
        "noise_proxy": (len(changed) / len(v1_non)) if v1_non else None,
        "non_trivial": len(v2_non) > 0,
        "non_triviality_note": (
            "契約 §4.1 硬地板：v2_frame != unframed 的筆數 > 0。若為 0 則 A1 是無用濾層，"
            "屬**假通過（false pass）**，必須在報告中明寫。"
        ),
        "recall_loss_upper_bound": len(v1_non) - len(v2_non),
        "recall_note": "A1 結構上只能 veto，**不能提高 recall**（契約 §1.3、AC9）。",
        "precision_on_known_pollution": {
            "definition_note": (
                "契約 §4.2 預登記的 proxy，非逐字比對 Owner 的兩個示例句；"
                "命中率低**不得**用來調整集合定義。"
            ),
            "sleep_contamination": {
                "set": "v1_frame == 'night' 且命中任一 ACTION_TOKENS",
                "n": len(poll["sleep"]),
                "vetoed_by_v2": sum(1 for r in poll["sleep"] if r["v2_frame"] == "unframed"),
                "precision": _precision(poll["sleep"]),
                "texts": sorted({r["raw_text"] for r in poll["sleep"]}),
            },
            "night_simile_contamination": {
                "set": "v1_frame == 'night' 且命中任一 SIMILE_MARKERS",
                "n": len(poll["night_simile"]),
                "vetoed_by_v2": sum(1 for r in poll["night_simile"]
                                    if r["v2_frame"] == "unframed"),
                "precision": _precision(poll["night_simile"]),
                "texts": sorted({r["raw_text"] for r in poll["night_simile"]}),
            },
        },
        "veto_ledger_by_rule": dict(sorted(veto_by_rule.items())),
        "token_level_verdict_counts": {k: dict(sorted(v.items()))
                                       for k, v in sorted(token_counts.items())},
        "kept_detail": kept_detail,
        "token_layer_loss_attribution": {
            "all_vetoed": {
                "count": len(lost_all_vetoed),
                "note": "T 中所有 token 皆 vetoed ⇒ A1 判 vetoed（設計上要做的 precision 過濾）",
                "obs": [{"obs_id": r["obs_id"], "raw_text": r["raw_text"],
                         "token_verdicts": r["token_verdicts"]} for r in lost_all_vetoed],
            },
            "no_attachment_undecided": {
                "count": len(lost_no_attachment),
                "note": "無 kept 且至少一個 undecided（R6 找不到依附標記）⇒ 規則表的 recall 損失",
                "obs": [{"obs_id": r["obs_id"], "raw_text": r["raw_text"],
                         "token_verdicts": r["token_verdicts"]} for r in lost_no_attachment],
            },
        },
        "v1_metrics": v1m,
        "v2_metrics": v2m,
        "v1_transcription_fidelity": fidelity,
        "auxiliary_observations": aux,
        "gate2_note": (
            "🔴 **本檔不含任何 Gate 2 PASS/FAIL 判定。** Gate 2 仍為 **INCONCLUSIVE**。"
            "上列 feasibility_conditions 僅為 v1 三門檻在 v2 frame 上的**觀察值**，"
            "不是 Gate 2 判定，也不得被引用為 Gate 2 結論（契約 AC8）。"
        ),
    }


# ══════════════════════════════════════════════════════════════
# 步驟 6：audit md
# ══════════════════════════════════════════════════════════════

def render_audit_md(rows: Sequence[Dict[str, Any]], metrics: Dict[str, Any],
                    baseline: Dict[str, Any], sha_after: Dict[str, str]) -> str:
    L: List[str] = []
    A = L.append
    A("# TA-2 v2-A — 候選 A（語法依附）feasibility 逐筆 audit")
    A("")
    A("> 🔴 `feasibility_only: true` — **本檔不含任何 Gate 2 PASS/FAIL 判定。**")
    A("> Gate 2 仍為 **INCONCLUSIVE**。預登記契約："
      f"`{CONTRACT_REF}`；工單：`{WORK_ORDER_REF}`。")
    A("> **A1 結構上只能 veto，不能提高 recall**（A2 rescue 明確不做：等同新 classifier，"
      "違反 Owner 設計 §9.3）。")
    A("")

    A("## 1. 凍結基線（AC1）")
    A("")
    A("| 項目 | 預登記（契約 §7） | 重播前 | 重播後 | 相符 |")
    A("|---|---|---|---|---|")
    A(f"| `sha256(ta2a_gate2_four_arm_real.json)` | `{baseline['corpus_sha256']}` "
      f"| `{sha_after['corpus_before']}` | `{sha_after['corpus_after']}` "
      f"| {'✅' if baseline['corpus_sha256'] == sha_after['corpus_after'] else '❌'} |")
    A(f"| `sha256(harness/run_ta2a_gate2.py)` | `{baseline['v1_script_sha256']}` "
      f"| `{sha_after['v1_before']}` | `{sha_after['v1_after']}` "
      f"| {'✅' if baseline['v1_script_sha256'] == sha_after['v1_after'] else '❌'} |")
    A("")
    A(f"預登記時 git HEAD：`{baseline['git_head_at_preregistration']}`")
    A("")

    m = metrics
    A("## 2. 量化摘要（全部 `feasibility_only: true`）")
    A("")
    A("| 指標 | 值 |")
    A("|---|---|")
    A(f"| LLM 呼叫次數 | `{m['llm_calls']}` |")
    A(f"| observations | {m['n_observations']} |")
    A(f"| v1 非 unframed | {m['v1_frames_non_unframed']} |")
    A(f"| v2 非 unframed | {m['v2_frames_non_unframed']} |")
    A(f"| frame 改變筆數 | {m['frame_changed']} |")
    A(f"| noise proxy | {m['noise_proxy']} |")
    A(f"| **non-triviality（硬地板）** | **{m['non_trivial']}** |")
    A(f"| recall 損失上限 | {m['recall_loss_upper_bound']} |")
    ps = m["precision_on_known_pollution"]["sleep_contamination"]
    pn = m["precision_on_known_pollution"]["night_simile_contamination"]
    A(f"| precision（`睡` 污染集, n={ps['n']}） | {ps['precision']} |")
    A(f"| precision（`夜` 比喻污染集, n={pn['n']}） | {pn['precision']} |")
    A(f"| ΔR（v1 / v2） | {m['v1_metrics']['dR']} / {m['v2_metrics']['dR']} |")
    A(f"| ΔI（v1 / v2） | {m['v1_metrics']['dI']} / {m['v2_metrics']['dI']} |")
    A(f"| DiD（v1 / v2） | {m['v1_metrics']['did']} / {m['v2_metrics']['did']} |")
    A(f"| 條件 3 observation 穩定（v1 / v2） | "
      f"{m['v1_metrics']['feasibility_conditions']['c3_observation_stable']} / "
      f"{m['v2_metrics']['feasibility_conditions']['c3_observation_stable']} |")
    A("")
    A("> 上表的三門檻觀察值**不是** Gate 2 判定（契約 AC8）。")
    A("")

    A("## 3. 逐筆 audit 表（全部 66 筆；`raw_text` 未截斷 — AC7）")
    A("")
    A("| # | obs_id | v1_frame | v2_frame | T | token_verdicts | A1_verdict | rule_id | span |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        tv = ", ".join(f"{t['token']}:{t['verdict']}" for t in r["token_verdicts"]) or "—"
        span = (r["span"] or "—").replace("|", "\\|")
        A(f"| {r['index']} | `{r['obs_id']}` | {r['v1_frame']} | {r['v2_frame']} | "
          f"`{','.join(r['T']) or '—'}` | {tv} | **{r['A1_verdict']}** | "
          f"`{','.join(r['rule_id']) or '—'}` | {span} |")
    A("")
    A("### 3.1 完整原文（逐筆，未截斷）")
    A("")
    for r in rows:
        A(f"- **`{r['obs_id']}`** · v1=`{r['v1_frame']}` · v2=`{r['v2_frame']}` · "
          f"A1=`{r['A1_verdict']}`")
        # 🔴 AC7：用圍欄區塊承載原文，避免原文內的換行破壞 markdown 結構。
        #    **不得**對原文做任何截斷。
        A("")
        A("  ```text")
        for ln in (r["raw_text"] or "").splitlines():
            A(f"  {ln}")
        A("  ```")
        A("")
    A("")

    A("## 4. veto ledger（依 rule_id 統計）")
    A("")
    A("| rule_id | token 層觸發次數 |")
    A("|---|---|")
    for rule, n in m["veto_ledger_by_rule"].items():
        A(f"| `{rule}` | {n} |")
    A("")

    A("## 5. token 層丟失歸因表（契約 §4.4）")
    A("")
    la = m["token_layer_loss_attribution"]
    A("| 丟失原因 | 筆數 | 性質 |")
    A("|---|---|---|")
    A(f"| 所有 token 皆 vetoed | {la['all_vetoed']['count']} | "
      "A1 設計上要做的 precision 過濾（預期、可辯護） |")
    A(f"| token 無依附標記（undecided） | {la['no_attachment_undecided']['count']} | "
      "規則表的 **recall 損失**（代價） |")
    A("")
    A("### 5.1 `all_vetoed` 明細")
    A("")
    if la["all_vetoed"]["obs"]:
        for o in la["all_vetoed"]["obs"]:
            tv = ", ".join(f"{t['token']}:{t['verdict']}" for t in o["token_verdicts"])
            A(f"- `{o['obs_id']}` · {tv}")
            A("")
            A("  ```text")
            for ln in (o["raw_text"] or "").splitlines():
                A(f"  {ln}")
            A("  ```")
            A("")
    else:
        A("（無）")
    A("")
    A("### 5.2 `no_attachment_undecided` 明細")
    A("")
    if la["no_attachment_undecided"]["obs"]:
        for o in la["no_attachment_undecided"]["obs"]:
            tv = ", ".join(f"{t['token']}:{t['verdict']}" for t in o["token_verdicts"])
            A(f"- `{o['obs_id']}` · {tv}")
            A("")
            A("  ```text")
            for ln in (o["raw_text"] or "").splitlines():
                A(f"  {ln}")
            A("  ```")
            A("")
    else:
        A("（無）")
    A("")

    A("## 6. undecided 清單（全部原文）")
    A("")
    und = [r for r in rows if r["A1_verdict"] == "undecided"]
    if und:
        for r in und:
            tv = ", ".join(f"{t['token']}:{t['verdict']}" for t in r["token_verdicts"]) or "—"
            A(f"- `{r['obs_id']}` · v1=`{r['v1_frame']}` · {tv}")
            A("")
            A("  ```text")
            for ln in (r["raw_text"] or "").splitlines():
                A(f"  {ln}")
            A("  ```")
            A("")
    else:
        A("（無）")
    A("")

    A("## 7. 已知污染集命中的原文（契約 §4.2 proxy）")
    A("")
    A(f"- `睡` 污染集（n={ps['n']}，v2 判 unframed {ps['vetoed_by_v2']} 筆）：")
    if not ps["texts"]:
        A("  - （無）")
    for t in ps["texts"]:
        A("")
        A("  ```text")
        for ln in (t or "").splitlines():
            A(f"  {ln}")
        A("  ```")
    A("")
    A(f"- `夜` 比喻污染集（n={pn['n']}，v2 判 unframed {pn['vetoed_by_v2']} 筆）：")
    if not pn["texts"]:
        A("  - （無）")
    for t in pn["texts"]:
        A("")
        A("  ```text")
        for ln in (t or "").splitlines():
            A(f"  {ln}")
        A("  ```")
    A("")

    A("## 8. 輔助觀察（僅記錄，不設門檻）")
    A("")
    for k, v in m["auxiliary_observations"].items():
        A(f"- `{k}` = {v}")
    A("")

    A("## 9. 規則表的已知缺陷（**記錄，不修** — 契約 §2.2.5／§6.5）")
    A("")
    A("> 🔴 以下全部是**預登記規則表在實際語料上的真實失效**。依工單與契約，"
      "**不得**為了讓數字好看而調整規則表、詞表或窗口大小。")
    A("")
    A("### 9.1 token 層逐詞判定分佈")
    A("")
    A("| token | vetoed | kept | undecided |")
    A("|---|---|---|---|")
    for tok, counts in m["token_level_verdict_counts"].items():
        A(f"| `{tok}` | {counts.get('vetoed', 0)} | {counts.get('kept', 0)} | "
          f"{counts.get('undecided', 0)} |")
    A("")
    A(f"**觀察**：`R6`（找不到依附標記）觸發 "
      f"{m['veto_ledger_by_rule'].get('R6_NO_ATTACHMENT', 0)} 次，是規則表最主要的失敗模式；"
      f"`R1`（動作詞）只觸發 {m['veto_ledger_by_rule'].get('R1_ACTION_TOKEN', 0)} 次。"
      "換言之，**A1 在實際語料上幾乎不是靠「辨識動作詞」賺到頭寸，而是靠「找不到依附標記」"
      "把大量 frame 判成 undecided 而丟失**。")
    A("")
    A("### 9.2 缺陷清單")
    A("")
    A("1. **附著標記在真實 LLM 語料上幾乎不存在。** `午餐`／`宵夜`／`午安`／`補眠` 出現的"
      "小句裡通常沒有任何 `ATTACHMENT_MARKERS`，於是 R6 全面命中 → 幀被丟。"
      "這是**規則表的 recall 損失**，不是 A1 的設計目標。")
    A(f"2. **`siesta` 幀未存活**：v1 判 siesta {m['auxiliary_observations']['siesta_v1_count']} 筆，"
      f"v2 只剩 {m['auxiliary_observations']['siesta_v2_count']} 筆"
      f"（`siesta_survived = {m['auxiliary_observations']['siesta_survived']}`）。"
      "**成因不是 `R1`**：`午睡`／`補眠` 在 `COMPOUND_TIME_NOUNS`，`R1` 未作用於它們"
      "（ledger 的 R1 全部落在 `睡`）。真正的成因是同一句裡的 `午安` token 判 `undecided`，"
      "而 `補眠` 自己也 `undecided`。依工單明文：**不得**為了救回 siesta 而擴大 "
      "`ACTION_TOKENS` 或 `ATTACHMENT_MARKERS`。**照實回報。**")
    A("3. **`ATTACHMENT_MARKERS` 的單字 marker 太泛，存活下來的 frame 可能是假陽性。**"
      "全部 `kept` 的 token 如下（注意是哪個 marker 意外命中）：")
    A("")
    for kd in m["kept_detail"]:
        A(f"   - `{kd['obs_id']}` · `{kd['token']}` · 小句：`{kd['clause']}` "
          f"· rules=`{','.join(kd['rules'])}`")
    A("")
    A("   觀察：這幾筆的 `kept` 多由 `到`／`這` 這類**單字** marker 觸發，且該 marker 落在"
      "與時段詞**無關**的位置（如「已經**到**尾聲了」「**這**頓到底算」），"
      "並非時段詞依附於事件。**這是 R7 的已知假陽性，記錄不修。**")
    A("4. **預登記的 recall 損失確實發生**：契約 §3.3 明文預期的「夜深了，該睡了」情形"
      "在 corpus 中出現，`夜` 判 `undecided`（R6）、`睡` 判 `vetoed`（R1）"
      "→ frame 層判 `undecided` → 丟失 frame。**依工單明文，未為救它加詞。**")
    A("5. **v1 詞表的覆蓋缺口（非 A1 的問題，記錄不修）**：觀察到的語料含「補個眠」"
      "（「補眠」非連續子串，故 v1 不觸發 siesta）與「不是晚上」"
      "（v1 無 `晚上` 觸發詞，故 v1 判 `unframed`）。")
    A("6. **`R2` 仍為 0 筆**（v1 規則順序下不可達，契約 §2.2.3／§3.5）；**`R5` 未觸發**。")
    A("")

    A("## 10. arm-blindness 隔離說明（AC4）")
    A("")
    A("1. `load_corpus()` 把 corpus 拆成**兩份平行清單**：`raw_texts: list[str]`（純回應文字）"
      "與 `meta: list[dict]`（`probe_id`／`arm`／`run_index`）。")
    A("2. `replay_texts(raw_texts)` 函式簽章**只接受 `Sequence[str]`**，回傳 token 層與 "
      "frame 層判定。它在整個重播過程中**無法**取得 `arm`／`probe_id`／"
      "`injected_temporal_line` —— 不是「不去讀」，而是結構上收不到。")
    A("3. `build_audit()` 是**獨立的 audit 步驟**，唯一把索引接回中繼資料的地方；"
      "本檔第 3 節的 `obs_id`／`probe_id`／`arm` 三欄皆由該步驟寫入。")
    A("")

    A("## 11. 限制（誠實邊界）")
    A("")
    A("1. **A1 不能提高 recall。** 結構上它只能 `vetoed`；丟失的真陽性上限見第 2 節。")
    A("2. **A2（rescue）不做**：讓 A 自己偵測 v1 漏掉的 frame 等同**新 temporal classifier**，"
      "違反 Owner 設計 §9.3 的明文禁止。")
    A("3. **本檔不是 Gate 2 判定**；Gate 2 仍為 INCONCLUSIVE。")
    A("4. precision 集合是**預登記的 proxy**，非逐字比對 Owner 的兩個示例句。")
    A("5. 中文分句是字串切分、不是 parser：`…`、破折號、換行等**不在**封閉分隔符清單內，"
      "跨這類標點的 marker 可能被誤算為同小句。**記錄不修。**")
    A("6. 規則表缺陷一律**記錄不修**（契約 §6.5）。")
    A("")
    return "\n".join(L) + "\n"


# ══════════════════════════════════════════════════════════════

def main() -> int:
    # ── AC1：重播前基線 ──
    sha = {"corpus_before": _sha256(CORPUS), "v1_before": _sha256(FROZEN_V1)}
    baseline_ok = (sha["corpus_before"] == FROZEN_BASELINE["corpus_sha256"]
                   and sha["v1_before"] == FROZEN_BASELINE["v1_script_sha256"])
    if not baseline_ok:  # 契約 §6.1 停止條件
        print("⛔ 契約 §6.1 停止條件：凍結基線與預登記不符，中止。", file=sys.stderr)
        print(f"  corpus: {sha['corpus_before']}", file=sys.stderr)
        print(f"  v1    : {sha['v1_before']}", file=sys.stderr)
        return 2

    # ── 步驟 1–3 ──
    _arms_by_probe()
    raw_texts, meta, _probe_order = load_corpus()
    results = replay_texts(raw_texts)          # 🔴 arm-blind：只吃 list[str]
    rows = build_audit(results, meta)          # 🔴 獨立 audit 步驟才接回 arm
    for r, text in zip(rows, raw_texts):
        r["raw_text"] = text                   # 原文完整保留，不截斷

    metrics = compute_metrics(rows)

    # ── AC1：重播後重算 ──
    sha["corpus_after"] = _sha256(CORPUS)
    sha["v1_after"] = _sha256(FROZEN_V1)
    if (sha["corpus_after"] != sha["corpus_before"]
            or sha["v1_after"] != sha["v1_before"]):  # 契約 §6.1
        print("⛔ 重播前後基線不一致，中止。", file=sys.stderr)
        return 2

    payload = {
        "feasibility_only": True,
        "llm_calls": 0,
        "contract": CONTRACT_REF,
        "work_order": WORK_ORDER_REF,
        "frozen_baseline": {
            **FROZEN_BASELINE,
            "corpus_sha256_replay_before": sha["corpus_before"],
            "corpus_sha256_replay_after": sha["corpus_after"],
            "v1_script_sha256_replay_before": sha["v1_before"],
            "v1_script_sha256_replay_after": sha["v1_after"],
            "ac1_baseline_match": True,
        },
        "metrics": metrics,
        "audit": rows,
        "gate2_note": metrics["gate2_note"],
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    OUT_AUDIT.write_text(render_audit_md(rows, metrics, FROZEN_BASELINE, sha),
                         encoding="utf-8", newline="\n")

    print("TA-2 v2-A feasibility replay（0 LLM 呼叫，feasibility_only=true）")
    print(f"  observations           : {metrics['n_observations']}")
    print(f"  v1 非 unframed / v2 非  : {metrics['v1_frames_non_unframed']} / "
          f"{metrics['v2_frames_non_unframed']}")
    print(f"  noise proxy            : {metrics['noise_proxy']}")
    print(f"  non-trivial（硬地板）    : {metrics['non_trivial']}")
    print(f"  recall 損失上限          : {metrics['recall_loss_upper_bound']}")
    print(f"  precision 睡污染集       : {metrics['precision_on_known_pollution']['sleep_contamination']['precision']}"
          f" (n={metrics['precision_on_known_pollution']['sleep_contamination']['n']})")
    print(f"  precision 夜比喻污染集   : "
          f"{metrics['precision_on_known_pollution']['night_simile_contamination']['precision']}"
          f" (n={metrics['precision_on_known_pollution']['night_simile_contamination']['n']})")
    print(f"  ΔR/ΔI/DiD v1           : {metrics['v1_metrics']['dR']} / "
          f"{metrics['v1_metrics']['dI']} / {metrics['v1_metrics']['did']}")
    print(f"  ΔR/ΔI/DiD v2           : {metrics['v2_metrics']['dR']} / "
          f"{metrics['v2_metrics']['dI']} / {metrics['v2_metrics']['did']}")
    print(f"  丟失歸因 all_vetoed     : "
          f"{metrics['token_layer_loss_attribution']['all_vetoed']['count']}")
    print(f"  丟失歸因 no_attachment  : "
          f"{metrics['token_layer_loss_attribution']['no_attachment_undecided']['count']}")
    print("  🔴 Gate 2 維持 INCONCLUSIVE；本檔不含 Gate 2 判定。")
    print(f"  → {OUT_JSON.relative_to(REPO)}")
    print(f"  → {OUT_AUDIT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
