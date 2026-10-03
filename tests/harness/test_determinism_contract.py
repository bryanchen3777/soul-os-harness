"""P2 Determinism Contract — 四級判定與三層區分。

契約：docs/DETERMINISM-CONTRACT-P2.md
實作：`harness/observer.py::classify_experiment_determinism`（additive）

層級：
  A. Raw determinism        同條件 → 同 raw output
  B. Observation stability  raw 不同，但 observation evidence 一致
  C. Causal attribution     （本函式**不判定**，需實驗自行提供對照組）

四級：DETERMINISTIC / STABLE_OBSERVATION / NON_DETERMINISTIC / INCONCLUSIVE
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO = pathlib.Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness.observer import Observer  # noqa: E402


def _rec(cp, emergent, decision="not_transmit"):
    return {
        "checkpoint": cp,
        "sim_ts": "t",
        "stimulus": "s",
        "emergent_snapshot": emergent,
        "motive_text": emergent,
        "decision_text": '{"decision": "%s"}' % decision,
        "reached_action": False,
    }


def _runs(per_run_records):
    """per_run_records: List[List[record]] —— 每個元素是一個 run 的 records。"""
    return [
        {"run_id": f"R{i}", "records": recs}
        for i, recs in enumerate(per_run_records)
    ]


# ── 完全一致：DETERMINISTIC ──────────────────────────────────

def test_fully_deterministic():
    o = Observer()
    runs = _runs([
        [_rec("D0", "同一段解讀"), _rec("D15", "同一段解讀2")],
        [_rec("D0", "同一段解讀"), _rec("D15", "同一段解讀2")],
        [_rec("D0", "同一段解讀"), _rec("D15", "同一段解讀2")],
    ])
    r = o.classify_experiment_determinism(runs)
    assert r["verdict"] == "DETERMINISTIC"
    assert r["raw_layer"]["verdict"] == "DETERMINISTIC"
    assert r["observation_layer"]["verdict"] == "DETERMINISTIC"
    assert r["legacy_verdict"] == "PASS"
    assert r["causal_attribution"] == "NOT_ASSESSED"


# ── raw 不穩、observation 穩：STABLE_OBSERVATION（本次 P2 的核心）──

def test_stable_observation_when_raw_varies_but_decision_agrees():
    """這就是 TL-12 當時無法表達的情況：raw 3 種不同、decision 一致。"""
    o = Observer()
    runs = _runs([
        [_rec("D0", "解讀A")],
        [_rec("D0", "解讀B")],
        [_rec("D0", "解讀C")],
    ])
    r = o.classify_experiment_determinism(runs)
    assert r["raw_layer"]["verdict"] == "NON_DETERMINISTIC", "raw 應判不穩"
    assert r["observation_layer"]["verdict"] == "STABLE_OBSERVATION", (
        "raw 不穩但 decision_parsed 一致 ⇒ 應判 STABLE_OBSERVATION"
    )
    assert r["verdict"] == "STABLE_OBSERVATION"
    # 🔴 既有 verdict 仍是 PASS —— 因為 `derive_determinism` 比的是
    #    `decision_parsed`（observation 錨點），**不是** raw 原文。
    #    這正是 P2 發現的缺口：舊機制回 PASS，卻無法告訴我們
    #    「raw 其實不穩、只是 observation 恰好一致」。
    assert r["legacy_verdict"] == "PASS"


def test_causal_attribution_is_never_asserted():
    """🔴 不可從 raw/observation 穩定推論因果歸因。"""
    o = Observer()
    runs = _runs([[_rec("D0", "x")], [_rec("D0", "x")], [_rec("D0", "x")]])
    r = o.classify_experiment_determinism(runs)
    assert r["causal_attribution"] == "NOT_ASSESSED"
    assert "control" in r["causal_attribution_note"]


# ── raw 與 observation 皆不穩：INCONCLUSIVE ───────────────────

def test_inconclusive_when_observation_also_varies():
    o = Observer()
    runs = _runs([
        [_rec("D0", "解讀A", decision="not_transmit")],
        [_rec("D0", "解讀B", decision="transmit")],
        [_rec("D0", "解讀C", decision="not_transmit")],
    ])
    r = o.classify_experiment_determinism(runs)
    assert r["raw_layer"]["verdict"] == "NON_DETERMINISTIC"
    assert r["observation_layer"]["verdict"] == "NON_DETERMINISTIC"
    assert r["verdict"] == "INCONCLUSIVE", "兩層皆不穩 ⇒ 無法支撐結論"


# ── 既有行為逐字元相容（不破壞 TL-1／TL-5）────────────────────

def test_derive_determinism_unchanged():
    """既有 `derive_determinism` 必須逐字元維持 PASS／BLOCKED 兩值。

    🔴 注意其比對錨點是 `decision_parsed`（observation 層），**不是 raw 原文** ——
    故 raw 不同但 decision 相同時，既有 verdict 仍是 PASS。
    """
    o = Observer()
    # decision 不同 ⇒ BLOCKED
    diff_decision = _runs([
        [_rec("D0", "a", decision="not_transmit")],
        [_rec("D0", "b", decision="transmit")],
        [_rec("D0", "c", decision="not_transmit")],
    ])
    legacy = o.derive_determinism(diff_decision)
    assert legacy["determinism_verdict"] == "BLOCKED"
    assert set(legacy) == {"determinism_verdict", "matrix"}, (
        f"既有回傳 key 集合不得改變：{sorted(legacy)}"
    )

    # decision 相同（raw 可不同）⇒ PASS
    same_decision = _runs([[_rec("D0", "a")], [_rec("D0", "b")], [_rec("D0", "c")]])
    assert o.derive_determinism(same_decision)["determinism_verdict"] == "PASS"


def test_classify_reuses_legacy_anchor_not_new_dimension():
    """observation 層必須沿用既有 `decision_parsed` 錨點，不得另立比對維度。"""
    o = Observer()
    runs = _runs([[_rec("D0", "a")], [_rec("D0", "b")], [_rec("D0", "c")]])
    r = o.classify_experiment_determinism(runs)
    assert r["observation_layer"]["matrix"] == o.derive_determinism(runs)["matrix"]
    assert "decision_parsed" in r["observation_layer"]["metric"]


# ── 契約文件的硬性規則 ────────────────────────────────────────

def test_contract_doc_declares_seed_is_not_llm_seed():
    """`seed: 42` 是 fixture seed，契約必須明文登記這個誤導性。"""
    doc = (REPO / "docs" / "DETERMINISM-CONTRACT-P2.md").read_text(encoding="utf-8")
    assert "不是 LLM seed" in doc or "不是 LLM seed。" in doc
    assert "make_real_llm_call" in doc, "契約必須點名 seed 的實際流向"
