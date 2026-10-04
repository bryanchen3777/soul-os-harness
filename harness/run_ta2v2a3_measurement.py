"""harness/run_ta2v2a3_measurement.py — TA-2-V2A3-MEASUREMENT 的機械計算 runner。

工單：`docs/TA2-V2A3-MEASUREMENT-WORK-ORDER.md` §2.2／§6

**本 runner 不做判斷。** 判斷（Stage 1 `DEFENSIBLE`／`NOT_DEFENSIBLE`、Stage 3 五類）
全部來自 `harness/ta2v2a3_population.py`（arm-blind 標註資料）。本檔只做：

1. 由 corpus **唯讀**重算 v1-framed 母集合（frozen v1 唯讀 import）。
2. 由 A2 **機械**計算 `a2_survivors`（標註者看不到、也不參與此步）。
3. 以集合代數推導 `defensible` 與 `HEADROOM`。
4. 把 Stage 3 分類套到 `HEADROOM`，得 LOCAL / OUTSIDE_LOCAL 分區與五類計數。
5. **§6 一致性**：每個計數函式的分子／分母**定義直接取自其 docstring**並印出，
   另以**獨立重算路徑**（不同程式碼）比對同一組數值。

🔴 **Gate 判定不由本檔做出。** Gate 0 需要第二位盲式標註者的一致率，
兩份標註到齊後由主大腦裁決。本檔只輸出 Gate 的**輸入計數**，
`gate_status` 一律標為 `DEFERRED_TO_PRIMARY_BRAIN`，**不宣告 A 或 E**。

**0 LLM calls、0 network、0 replay、0 corpus mutation。**
corpus 以唯讀模式開啟；本檔只寫入自己的新產出 `data/harness_out/tl12/ta2v2a3_measurement.json`。
"""
from __future__ import annotations

import json
import pathlib
import re
import sys
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import ta2v2a3_population as pop  # noqa: E402
from harness.run_ta2a_gate2 import extract_temporal_frame  # frozen v1，唯讀  # noqa: E402
from harness.ta2v2a2_event_attachment import apply_a2  # A2，唯讀  # noqa: E402

CORPUS_PATH = REPO / "data" / "harness_out" / "tl12" / "ta2a_gate2_four_arm_real.json"
OUTPUT_PATH = REPO / "data" / "harness_out" / "tl12" / "ta2v2a3_measurement.json"

#: frozen v1 的 sha256（工單 §8 硬性約束；唯讀，不得改動）
FROZEN_V1_SHA256 = "bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253"
UNFRAMED = "unframed"


# ══════════════════════════════════════════════════════════════════════════
# corpus 唯讀載入
# ══════════════════════════════════════════════════════════════════════════


class Observation:
    """一筆 corpus observation 的唯讀視圖。"""

    __slots__ = ("obs_id", "probe", "arm", "run", "raw_text", "stored_frame",
                 "v1_frame", "a2_frame")

    def __init__(self, obs_id, probe, arm, run, raw_text, stored_frame):
        self.obs_id = obs_id
        self.probe = probe
        self.arm = arm
        self.run = run
        self.raw_text = raw_text
        self.stored_frame = stored_frame
        self.v1_frame = extract_temporal_frame(raw_text)      # 主路徑：frozen v1 重算
        self.a2_frame = apply_a2(raw_text)["a2_frame"]        # A2 機械計算

    def __repr__(self) -> str:  # pragma: no cover - 診斷用
        return f"<Observation {self.obs_id} v1={self.v1_frame} a2={self.a2_frame}>"


def load_observations() -> List[Observation]:
    """唯讀載入 corpus 的 66 筆 observation（`results[probe][arm]["observations"]` 是 **dict**）。"""
    with open(CORPUS_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    out: List[Observation] = []
    for probe, probe_block in data["results"].items():
        for arm, arm_block in probe_block.items():
            if not isinstance(arm_block, dict) or "observations" not in arm_block:
                continue
            obs = arm_block["observations"]
            items = obs.items() if isinstance(obs, dict) else list(enumerate(obs))
            for run, entry in items:
                raw = entry.get("raw_text") if isinstance(entry, dict) else str(entry)
                stored = entry.get("temporal_frame") if isinstance(entry, dict) else None
                out.append(Observation(f"{probe}/{arm}/{run}", probe, arm, str(run),
                                       raw or "", stored))
    return out


# ══════════════════════════════════════════════════════════════════════════
# §6：把每個計數函式的「宣告」抽成可印出、可重算的表定式
# ══════════════════════════════════════════════════════════════════════════

_FORMULA_LINE = re.compile(r"^\s*(numerator|denominator|value)\s*=\s*(.+?)\s*$")


def declared_formulas(fn) -> Dict[str, str]:
    """從 `fn` 的 docstring 抽出 `numerator` / `denominator` / `value` 三行表定式。

    輸出的每一項都必須是 `fn` 回傳 dict 的一個 key，且數值相等（由驗收測試以獨立
    parser 與 `eval` 檢查）。**docstring 是宣告的唯一來源**，本檔不另抄一份公式。
    """
    doc = (fn.__doc__ or "").splitlines()
    out: Dict[str, str] = {}
    for line in doc:
        m = _FORMULA_LINE.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def metric_block(fn, *args) -> Dict[str, Any]:
    """執行 `fn`，並把它的**宣告表定式**與**實際數值**綁在同一個 block 輸出。"""
    formulas = declared_formulas(fn)
    result = fn(*args)
    block: Dict[str, Any] = {"function": fn.__name__}
    for key, expr in formulas.items():
        block[f"{key}_definition"] = expr
        block[key] = result[key]
    for key, value in result.items():
        block.setdefault(key, value)
    return block


# ══════════════════════════════════════════════════════════════════════════
# 計數函式（主路徑）—— docstring 的分子／分母定義必須與實作逐字一致
# ══════════════════════════════════════════════════════════════════════════


def count_population(observations: Sequence[Observation]) -> Dict[str, Any]:
    """母體：corpus 全部 observation 中，v1 frame 非 `unframed` 的比例。

    numerator   = len([o for o in observations if o.v1_frame != UNFRAMED])
    denominator = len(observations)
    value       = numerator / denominator if denominator else None

    `v1_frame` 由 frozen v1 `extract_temporal_frame(raw_text)` 重算（主路徑）。
    另回傳 `breakdown`：v1 frame 計數表（**僅計入 numerator 的那些筆**）。
    """
    numerator = len([o for o in observations if o.v1_frame != UNFRAMED])
    denominator = len(observations)
    framed = [o for o in observations if o.v1_frame != UNFRAMED]
    breakdown = dict(Counter(o.v1_frame for o in framed))
    assert sum(breakdown.values()) == numerator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "breakdown": breakdown,
    }


def count_defensible(v1_framed_ids: Sequence[str]) -> Dict[str, Any]:
    """母體：`v1_framed` 中 Stage 1 判為 `DEFENSIBLE` 的比例。

    numerator   = len([i for i in v1_framed_ids if pop.stage1_verdict(i) == pop.DEFENSIBLE])
    denominator = len(v1_framed_ids)
    value       = numerator / denominator if denominator else None

    另回傳 `breakdown`：`{verdict: count}`，保證 `sum(breakdown.values()) == denominator`。
    """
    numerator = len([i for i in v1_framed_ids if pop.stage1_verdict(i) == pop.DEFENSIBLE])
    denominator = len(v1_framed_ids)
    not_def = denominator - numerator
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "breakdown": {pop.DEFENSIBLE: numerator, pop.NOT_DEFENSIBLE: not_def},
    }


def count_a2_survivors(v1_framed_ids: Sequence[str],
                       observations: Sequence[Observation]) -> Dict[str, Any]:
    """母體：`v1_framed` 中 A2 仍判為非 `unframed`（= A2 已救回）的比例。

    numerator   = len([o for o in observations if o.v1_frame != UNFRAMED and o.a2_frame != UNFRAMED])
    denominator = len(v1_framed_ids)
    value       = numerator / denominator if denominator else None

    `a2_frame` 由 `harness.ta2v2a2_event_attachment.apply_a2` 機械計算（A1／A2 實作唯讀）。
    🔴 本函式**不得**被 Stage 1 標註者當作標註依據：survivor 身分由 runner 機械決定。
    """
    by_id = {o.obs_id: o for o in observations}
    numerator = len([i for i in v1_framed_ids if by_id[i].a2_frame != UNFRAMED])
    denominator = len(v1_framed_ids)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def count_headroom(defensible_ids: Sequence[str], survivor_ids: Sequence[str]) -> Dict[str, Any]:
    """母體：`defensible` 中未被 A2 救回的比例。`HEADROOM = defensible \\ a2_survivors`。

    numerator   = len([i for i in defensible_ids if i not in set(survivor_ids)])
    denominator = len(defensible_ids)
    value       = numerator / denominator if denominator else None

    這是**推導**（集合代數），不是挑選。
    """
    survivors = set(survivor_ids)
    numerator = len([i for i in defensible_ids if i not in survivors])
    denominator = len(defensible_ids)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
    }


def count_headroom_classes(headroom_ids: Sequence[str]) -> Dict[str, Any]:
    """母體：`HEADROOM` 的 Stage 3 五類計數（CLOSED 空間，缺項以 0 顯式列出）。

    denominator = len(headroom_ids)

    另回傳 `counts`：`{cls: count}`，保證 `sum(counts.values()) == denominator`。
    """
    denominator = len(headroom_ids)
    counts = {cls: 0 for cls in pop.CLASS_SPACE}
    for i in headroom_ids:
        cls = pop.stage3_class(i)
        if cls not in counts:
            raise ValueError(f"class outside CLOSED space: {cls!r}")
        counts[cls] += 1
    assert sum(counts.values()) == denominator
    return {"denominator": denominator, "counts": counts}


def count_headroom_partition(headroom_ids: Sequence[str]) -> Dict[str, Any]:
    """母體：`HEADROOM` 的 LOCAL / OUTSIDE_LOCAL 分區比例（Gate 2 的輸入計數）。

    numerator   = len([i for i in headroom_ids if pop.stage3_class(i) in pop.OUTSIDE_LOCAL_CLASSES])
    denominator = len(headroom_ids)
    value       = numerator / denominator if denominator else None

    另回傳 `breakdown`：`{"LOCAL": denominator - numerator, "OUTSIDE_LOCAL": numerator}`。
    🔴 本函式**只算計數**；門檻判讀（Gate 2）由主大腦在兩份標註到齊後負責。
    """
    numerator = len([i for i in headroom_ids
                     if pop.stage3_class(i) in pop.OUTSIDE_LOCAL_CLASSES])
    denominator = len(headroom_ids)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": numerator / denominator if denominator else None,
        "breakdown": {"LOCAL": denominator - numerator, "OUTSIDE_LOCAL": numerator},
    }


COUNTING_FUNCTIONS = (
    count_population,
    count_defensible,
    count_a2_survivors,
    count_headroom,
    count_headroom_classes,
    count_headroom_partition,
)


# ══════════════════════════════════════════════════════════════════════════
# 獨立重算路徑（與主路徑**不同程式碼**）
# ══════════════════════════════════════════════════════════════════════════
# 獨立性範圍（誠實聲明）：
#   * 母體：`v1_framed` 改由 corpus **已儲存的 `temporal_frame` 欄位**取得，
#     主路徑則重新呼叫 frozen `extract_temporal_frame(raw_text)`。兩者必須相等。
#   * 彙總：改用 `Counter` / `set` 運算 / `sum(bool)`，不使用主路徑的任何計數函式。
#   * `a2_frame` **不**重新實作 A2：`apply_a2` 是 A2 的唯一實作，重寫一份只會製造分歧。
#     獨立性因此限於「母體定義 + 彙總」——這正是工單 §6 的標的。
# ══════════════════════════════════════════════════════════════════════════


def independent_recompute() -> Dict[str, Any]:
    """以不同程式碼重算四個集合與三個計數，回傳可直接與主路徑比對的 dict。"""
    with open(CORPUS_PATH, encoding="utf-8") as fh:
        raw_json = json.load(fh)

    # --- 母體：改用已儲存的 temporal_frame 欄位 ---
    stored: Dict[str, str] = {}
    a2_survivors: Set[str] = set()
    for probe in sorted(raw_json["results"]):
        probe_block = raw_json["results"][probe]
        for arm in sorted(probe_block):
            arm_block = probe_block[arm]
            if not isinstance(arm_block, dict) or "observations" not in arm_block:
                continue
            obs = arm_block["observations"]
            items = obs.items() if isinstance(obs, dict) else list(enumerate(obs))
            for run, entry in items:
                obs_id = "/".join((probe, arm, str(run)))
                stored[obs_id] = entry["temporal_frame"]
                a2_survivors.add((obs_id, apply_a2(entry["raw_text"])["a2_frame"]))

    unframed_marker = UNFRAMED
    v1_framed = {oid for oid, frame in stored.items() if frame != unframed_marker}
    survivors = {oid for oid, frame in a2_survivors if frame != unframed_marker}
    all_ids = set(stored)

    defensible = {i for i in v1_framed if pop.stage1_verdict(i) == pop.DEFENSIBLE}
    headroom = defensible - survivors
    outside = {i for i in headroom
               if pop.stage3_class(i) not in pop.LOCAL_CLASSES}

    classes = Counter(pop.stage3_class(i) for i in headroom)
    return {
        "corpus_total": len(all_ids),
        "v1_framed": sorted(v1_framed),
        "a2_survivors": sorted(survivors & v1_framed),
        "defensible": sorted(defensible),
        "headroom": sorted(headroom),
        "metrics": {
            "population": {"numerator": len(v1_framed), "denominator": len(all_ids)},
            "defensible": {"numerator": len(defensible), "denominator": len(v1_framed)},
            "a2_survivors": {"numerator": len(survivors & v1_framed),
                             "denominator": len(v1_framed)},
            "headroom": {"numerator": len(headroom), "denominator": len(defensible)},
            "headroom_classes": {"denominator": len(headroom),
                                 "counts": {c: classes.get(c, 0)
                                            for c in pop.CLASS_SPACE}},
            "headroom_partition": {"numerator": len(outside),
                                   "denominator": len(headroom)},
        },
    }


# ══════════════════════════════════════════════════════════════════════════
# 主路徑
# ══════════════════════════════════════════════════════════════════════════


def build_report() -> Dict[str, Any]:
    """主路徑：四個集合 + 每個計數的「宣告定義 + 實際數值」。"""
    observations = load_observations()
    ordered = sorted(observations, key=lambda o: o.obs_id)

    v1_framed = [o.obs_id for o in ordered if o.v1_frame != UNFRAMED]
    survivors = [o.obs_id for o in ordered
                 if o.v1_frame != UNFRAMED and o.a2_frame != UNFRAMED]
    defensible = [i for i in v1_framed if pop.stage1_verdict(i) == pop.DEFENSIBLE]
    headroom = [i for i in defensible if i not in set(survivors)]

    report = {
        "ticket": "TA-2-V2A3-MEASUREMENT",
        "mode": "READ-ONLY / OFFLINE MEASUREMENT CONTRACT CORRECTION",
        "frozen_v1_sha256": FROZEN_V1_SHA256,
        "corpus_path": str(CORPUS_PATH.relative_to(REPO)).replace("\\", "/"),
        "corpus_sha256": _sha256(CORPUS_PATH),
        "clause_definition": pop.CLAUSE_DEFINITION,
        "class_space": list(pop.CLASS_SPACE),
        "class_definitions": dict(pop.CLASS_DEFINITIONS),
        "annotation_coverage": {
            "stage1_labeled": len(pop.STAGE1),
            "stage3_labeled": len(pop.STAGE3),
            "stage3_covers_all_defensible": (
                {j.obs_id for j in pop.STAGE3} == set(pop.defensible_ids())
            ),
        },
        "gate_status": {
            "decided_by": "PRIMARY_BRAIN (主大腦)，在兩份盲式標註到齊後",
            "gate_0_raw_agreement": "NOT_COMPUTABLE_SINGLE_ANNOTATOR",
            "gate_1_sample_guard": "DEFERRED_TO_PRIMARY_BRAIN",
            "gate_2_a_vs_e": "DEFERRED_TO_PRIMARY_BRAIN",
            "note": "本檔只輸出 Gate 的輸入計數；**不宣告 A 或 E**。"
                    "Gate 2 全程維持 INCONCLUSIVE。",
        },
        "sets": {
            "v1_framed": v1_framed,
            "a2_survivors": sorted(survivors),
            "defensible": sorted(defensible),
            "HEADROOM": sorted(headroom),
        },
        "metrics": {
            "population": metric_block(count_population, ordered),
            "defensible": metric_block(count_defensible, v1_framed),
            "a2_survivors": metric_block(count_a2_survivors, v1_framed, ordered),
            "headroom": metric_block(count_headroom, defensible, survivors),
            "headroom_classes": metric_block(count_headroom_classes, headroom),
            "headroom_partition": metric_block(count_headroom_partition, headroom),
        },
        "gate_inputs": {
            "n_headroom": _gate_input(headroom, "denominator"),
            "outside_local": _gate_input(headroom, "numerator"),
            "outside_local_ratio": _gate_input(headroom, "value"),
        },
    }
    return report


def _gate_input(headroom_ids: Sequence[str], key: str):
    """Gate 輸入計數的**唯一**來源：`count_headroom_partition` 的回傳值。

    本函式**不做任何算術**（只是轉取），因此沒有自己的分子／分母表定式；
    其正確性由 `test_gate_inputs_are_pure_delegations` 守住——
    轉取不得偏離 `count_headroom_partition` 的任何欄位。
    """
    return count_headroom_partition(headroom_ids)[key]


def _sha256(path: pathlib.Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()

def cross_check(report: Dict[str, Any]) -> Dict[str, Any]:
    """把主路徑的集合與計數與獨立路徑逐項比對。"""
    indep = independent_recompute()
    mismatches: List[str] = []

    indep_sets = {
        "v1_framed": indep["v1_framed"],
        "a2_survivors": indep["a2_survivors"],
        "defensible": indep["defensible"],
        "HEADROOM": indep["headroom"],
    }
    for name, expected in indep_sets.items():
        if report["sets"][name] != expected:
            mismatches.append(f"set:{name}")

    for name, block in report["metrics"].items():
        ref = indep["metrics"][name]
        for key in ("numerator", "denominator"):
            if key in ref and block.get(key) != ref[key]:
                mismatches.append(f"metric:{name}.{key}")
        if "counts" in ref and block.get("counts") != ref["counts"]:
            mismatches.append(f"metric:{name}.counts")

    return {"independent": indep, "mismatches": mismatches, "agrees": not mismatches}


def format_denominators(report: Dict[str, Any]) -> str:
    """把每個計數的分子／分母**定義與數值**逐項印出（工單 §6）。"""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("§6 DENOMINATOR MANIFEST（定義直接取自各計數函式 docstring）")
    lines.append("=" * 78)
    for name, block in report["metrics"].items():
        lines.append(f"[{name}]  {block['function']}()")
        for key in ("numerator", "denominator", "value"):
            if f"{key}_definition" in block:
                lines.append(f"    {key:<12} = {block[key]!r}")
                lines.append(f"    {'':<12}   where: {block[f'{key}_definition']}")
        if "counts" in block:
            counts = block["counts"]
            lines.append(f"    counts       = {counts!r}")
            lines.append(f"    {'':<12}   where: {block['denominator_definition']} → "
                         f"sum(counts.values()) == {block['denominator']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """跑主路徑 + 獨立路徑，印出 §6 manifest，寫出 JSON 產出。"""
    report = build_report()
    check = cross_check(report)

    print("TA-2-V2A3-MEASUREMENT — 集合機械計算（0 LLM calls / 0 network）")
    print(f"frozen v1 sha256 : {report['frozen_v1_sha256']}")
    print(f"corpus sha256    : {report['corpus_sha256']}")
    for name in ("v1_framed", "a2_survivors", "defensible", "HEADROOM"):
        print(f"|{name:<13}| = {len(report['sets'][name])}")
    print()
    print(format_denominators(report))
    print(f"independent recompute agrees : {check['agrees']}")
    if check["mismatches"]:
        print(f"MISMATCHES                   : {check['mismatches']}")
    print()
    print("Gate 輸入計數（**門檻判讀由主大腦負責，本檔不判 A / E**）：")
    for key, value in report["gate_inputs"].items():
        print(f"    {key:<22} = {value!r}")
    print()

    report["cross_check"] = {
        "agrees": check["agrees"],
        "mismatches": check["mismatches"],
        "independent_metrics": check["independent"]["metrics"],
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")
    print(f"written: {OUTPUT_PATH.relative_to(REPO)}")
    return 0 if check["agrees"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
