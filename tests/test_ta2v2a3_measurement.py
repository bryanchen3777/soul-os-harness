"""tests/test_ta2v2a3_measurement.py — TA-2-V2A3-MEASUREMENT：工單 §6 一致性驗收 ＋ mutation。

工單：`docs/TA2-V2A3-MEASUREMENT-WORK-ORDER.md`（§6 為一等驗收項）

**純函式 + 離線唯讀測試**：**不得**呼叫 LLM、**不得**連 network、**不得**啟動伺服器、
**不得**寫入 `data/**`（`ta2v2a3_measurement.json` 只由 runner 的 `main()` 產生）。
本測試**不讀 A1／A2 的分類結果**，只呼叫兩支被測模組的公開函式。

覆蓋：
  1. 母體完整性：Stage 1 覆蓋全部 25 筆 v1-framed，且不多不少。
  2. Stage 3 覆蓋**全體** `DEFENSIBLE`（runner 之後機械扣除 a2_survivors）。
  3. 分類空間 CLOSED（五類，無第六類、無子類）。
  4. clause 定義與「拒絕的 clause 慣例」靜態檢查。
  5. 🔴 §6-1：每個計數函式 **docstring 宣告的分子／分母與實作回傳值逐項相等**
     （本測試用**自己的** parser 與 `eval` 獨立重算，不呼叫 runner 的 `declared_formulas`）。
  6. 🔴 §6-2：每個計數的分子／分母定義與數值都出現在 runner 的輸出 block。
  7. 🔴 §6-3：獨立重算路徑與主路徑在集合與計數上完全一致。
  8. 🔴 §6-4：四個 mutation —— 改壞分子／分母／母體／獨立路徑後，**驗收測試必須變紅**。
"""
from __future__ import annotations

import ast
import hashlib
import inspect
import pathlib
import re
import sys
import types

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import run_ta2v2a3_measurement as runner  # noqa: E402
from harness import ta2v2a3_population as pop  # noqa: E402

POP_PATH = REPO / "harness" / "ta2v2a3_population.py"
RUN_PATH = REPO / "harness" / "run_ta2v2a3_measurement.py"
V1_PATH = REPO / "harness" / "run_ta2a_gate2.py"
V1_SHA256 = "bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253"

#: 本測試**自己**的公式列解析器（**不得**呼叫 runner 的 `declared_formulas`，
#: 否則兩條路徑會共用同一個 bug）。
_OWN_FORMULA = re.compile(r"^\s*(numerator|denominator|value)\s*=\s*(\S.*?)\s*$")

#: 允許在 `eval` 表定式中出現的名稱（runner 與 population 模組的模組層常數）
_EVAL_NAMES = (
    "UNFRAMED", "DEFENSIBLE", "NOT_DEFENSIBLE", "LOCAL_CLASSES",
    "OUTSIDE_LOCAL_CLASSES", "CLASS_SPACE",
)


def _own_declared(fn) -> dict:
    """用本測試自有的 parser 抽出 `fn` docstring 的分子／分母／值表定式。"""
    out = {}
    for line in inspect.getdoc(fn).splitlines():
        m = _OWN_FORMULA.match(line)
        if m and m.group(1) not in out:
            out[m.group(1)] = m.group(2)
    return out


def _eval_ns(fn):
    """`eval` 表定式所需的命名空間：函式參數名 → 以真實 corpus 資料綁定。"""
    observations = runner.load_observations()
    ordered = sorted(observations, key=lambda o: o.obs_id)
    v1_framed = [o.obs_id for o in ordered if o.v1_frame != runner.UNFRAMED]
    survivors = [o.obs_id for o in ordered
                 if o.v1_frame != runner.UNFRAMED and o.a2_frame != runner.UNFRAMED]
    defensible = [i for i in v1_framed if pop.stage1_verdict(i) == pop.DEFENSIBLE]
    headroom = [i for i in defensible if i not in set(survivors)]
    return {
        "observations": ordered,
        "v1_framed_ids": v1_framed,
        "survivor_ids": survivors,
        "defensible_ids": defensible,
        "headroom_ids": headroom,
        "judgments": None,  # 由呼叫端覆寫
    }


def _assert_declared_matches_impl(mod, fn, ns):
    """§6-1：docstring 宣告的每個表定式都必須等於函式的實際回傳值。

    `eval` 的全域取**被測模組自己的 globals**（因此 `pop` / `UNFRAMED` 等常數都可用），
    再由 `ns` 覆寫成真實資料；本測試**不**呼叫 runner 的 `declared_formulas`。
    """
    declared = _own_declared(fn)
    assert declared, f"{fn.__name__}: docstring 沒有可執行的分子／分母宣告"
    result = fn(*_arg_for(fn, ns))
    glb = dict(mod.__dict__)
    glb.update(ns)
    for key, expr in declared.items():
        assert key in result, f"{fn.__name__}: docstring 宣告 `{key}` 但回傳 dict 沒有此 key"
        recomputed = eval(expr, glb)  # noqa: S307 - 表定式來自 repo 內 docstring，非使用者輸入
        # 後續表定式（`value = numerator / denominator …`）可引用前面已宣告的欄位。
        glb[key] = result[key]
        if isinstance(result[key], float) or isinstance(recomputed, float):
            assert result[key] == pytest.approx(recomputed), (
                f"{fn.__name__}.{key}: docstring 宣告 `{expr}` 重算得 {recomputed!r}，"
                f"實作回傳 {result[key]!r}"
            )
        else:
            assert result[key] == recomputed, (
                f"{fn.__name__}.{key}: docstring 宣告 `{expr}` 重算得 {recomputed!r}，"
                f"實作回傳 {result[key]!r}"
            )


def _arg_for(fn, ns):
    """依形參名從 `ns` 取實測資料。"""
    params = list(inspect.signature(fn).parameters)
    return [ns[p] for p in params]


# ══════════════════════════════════════════════════════════════════════════
# 1–3 母體完整性 / 分類空間 CLOSED
# ══════════════════════════════════════════════════════════════════════════


def test_frozen_v1_untouched():
    """frozen v1 唯讀：sha256 必須維持工單 §8 的值。"""
    digest = hashlib.sha256(V1_PATH.read_bytes()).hexdigest()
    assert digest == V1_SHA256, f"frozen v1 sha256 漂移：{digest}"


def test_stage1_covers_exactly_the_v1_framed_population():
    """Stage 1 必須覆蓋**全部** 25 筆 v1-framed，一筆不多、一筆不少。"""
    observations = runner.load_observations()
    v1_framed = {o.obs_id for o in observations if o.v1_frame != runner.UNFRAMED}
    assert len(observations) == 66
    assert len(v1_framed) == 25
    assert pop.stage1_ids() == v1_framed, (
        "Stage 1 標註集合與 v1-framed 母體不符——"
        "**母體必須是判準的結果，不得事後增刪**"
    )


def test_stage1_v1_frame_field_matches_annotation():
    """Stage 1 記錄的 `v1_frame` 必須與 frozen v1 對同一 raw_text 的重算值一致。"""
    frames = {o.obs_id: o.v1_frame for o in runner.load_observations()}
    for j in pop.STAGE1:
        assert frames[j.obs_id] == j.v1_frame, j.obs_id


def test_stage1_evidence_quotes_appear_verbatim_in_raw_text():
    """每一筆的引用理由都必須逐字出現在該筆 `raw_text` 中（防止編造證據）。"""
    texts = {o.obs_id: o.raw_text for o in runner.load_observations()}
    for j in pop.STAGE1:
        assert j.evidence in texts[j.obs_id], f"Stage 1 引用不逐字：{j.obs_id}"
    for j in pop.STAGE3:
        assert j.evidence in texts[j.obs_id], f"Stage 3 引用不逐字：{j.obs_id}"


def test_not_defensible_only_for_closed_reasons():
    """`NOT_DEFENSIBLE` 只允許命中封閉清單；其餘一律 `DEFENSIBLE`（預設值）。"""
    for j in pop.STAGE1:
        if j.verdict == pop.NOT_DEFENSIBLE:
            assert j.reason_code in pop.NOT_DEFENSIBLE_REASONS, j.obs_id
        else:
            assert j.verdict == pop.DEFENSIBLE
            assert j.reason_code == "default_defensible"


def test_stage3_covers_all_defensible():
    """Stage 3 必須覆蓋**全體** `DEFENSIBLE`（含 A2 可能已救回的），由 runner 事後機械扣除。"""
    assert {j.obs_id for j in pop.STAGE3} == set(pop.defensible_ids())


def test_class_space_is_closed_five_classes():
    """分類空間 CLOSED：五類、無第六類、無子類。"""
    assert pop.CLASS_SPACE == ("L1", "L2", "L3", "N1", "N0")
    assert set(pop.LOCAL_CLASSES) | set(pop.OUTSIDE_LOCAL_CLASSES) == set(pop.CLASS_SPACE)
    assert not (set(pop.LOCAL_CLASSES) & set(pop.OUTSIDE_LOCAL_CLASSES))
    for j in pop.STAGE3:
        assert j.cls in pop.CLASS_SPACE, j.cls


# ══════════════════════════════════════════════════════════════════════════
# 4 clause 定義
# ══════════════════════════════════════════════════════════════════════════


def test_clause_definition_is_the_owner_ruling():
    """§3 clause 定義必須是 Owner 裁定的那一句（完整述語、非逗號切分、非句界）。"""
    assert "完整述語" in pop.CLAUSE_DEFINITION
    assert "不採逗號切分" in pop.CLAUSE_DEFINITION
    assert "不採句界讀法" in pop.CLAUSE_DEFINITION


def test_rejected_clause_conventions_are_not_used():
    """兩個標註模組都**不得**使用被拒絕的 clause 慣例（只看程式碼，不看註解／docstring）。"""
    for path in (POP_PATH, RUN_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "clause_spans":
                pytest.fail(f"{path.name} 定義了 A1 的逗號切分 `clause_spans`")
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                doc = ast.get_docstring(node, clean=False)
                if doc is not None:
                    first = node.body[0].value
                    docstrings.add(id(first))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings):
                for bad in ("，、；：", "。，、"):
                    assert bad not in node.value, (
                        f"{path.name}:{node.lineno} 程式碼中出現被拒絕的 clause 切分字元 `{bad}`"
                    )


# ══════════════════════════════════════════════════════════════════════════
# 5–7 §6 一致性驗收
# ══════════════════════════════════════════════════════════════════════════


def test_section6_docstring_matches_implementation_population():
    """§6-1：population 模組的三個計數函式，宣告與實作逐項一致。"""
    ns = _eval_ns(pop.count_stage1_verdicts)
    ns["judgments"] = pop.STAGE1
    _assert_declared_matches_impl(pop, pop.count_stage1_verdicts, ns)
    ns3 = dict(ns)
    ns3["judgments"] = pop.STAGE3
    for fn in (pop.count_stage3_classes, pop.count_partition):
        _assert_declared_matches_impl(pop, fn, ns3)


def test_section6_docstring_matches_implementation_runner():
    """§6-1：runner 的六個計數函式，宣告與實作逐項一致。"""
    ns = _eval_ns(runner.count_population)
    for fn in runner.COUNTING_FUNCTIONS:
        _assert_declared_matches_impl(runner, fn, ns)


def test_section6_every_denominator_is_printed_with_definition_and_value():
    """§6-2：每個計數的分子／分母定義與數值都必須出現在 runner 的輸出 block。"""
    report = runner.build_report()
    assert set(report["metrics"]) == {fn.__name__.replace("count_", "")
                                      for fn in runner.COUNTING_FUNCTIONS}
    for name, block in report["metrics"].items():
        assert "denominator_definition" in block, name
        assert "denominator" in block, name
        assert isinstance(block["denominator"], int), name
        if "numerator_definition" in block:
            assert "numerator" in block, name
    printed = runner.format_denominators(report)
    for name, block in report["metrics"].items():
        assert name in printed
        assert repr(block["denominator"]) in printed
        assert block["denominator_definition"] in printed


def test_section6_independent_recomputation_agrees():
    """§6-3：獨立重算路徑（不同程式碼）必須與主路徑在集合與計數上完全一致。"""
    report = runner.build_report()
    check = runner.cross_check(report)
    assert check["agrees"], f"主／獨立路徑不一致：{check['mismatches']}"
    indep = check["independent"]
    key_for = {"v1_framed": "v1_framed", "a2_survivors": "a2_survivors",
               "defensible": "defensible", "HEADROOM": "headroom"}
    for name, ids in report["sets"].items():
        assert ids == indep[key_for[name]], f"集合 `{name}` 主／獨立路徑不一致"


def test_independent_path_is_genuinely_separate_code():
    """§6-3：獨立路徑不得直接呼叫主路徑的計數函式（否則「獨立」是假的）。"""
    src = inspect.getsource(runner.independent_recompute)
    for fn in runner.COUNTING_FUNCTIONS:
        assert f"{fn.__name__}(" not in src, f"獨立路徑呼叫了主路徑的 {fn.__name__}"
    assert "extract_temporal_frame" not in src, (
        "獨立路徑必須改用 corpus 已儲存的 temporal_frame 欄位，而非重跑 frozen v1"
    )


def test_gate_inputs_are_pure_delegations():
    """`gate_inputs` 不得有自己的算術：必須逐欄等於 `count_headroom_partition`。"""
    report = runner.build_report()
    block = report["metrics"]["headroom_partition"]
    assert report["gate_inputs"]["n_headroom"] == block["denominator"]
    assert report["gate_inputs"]["outside_local"] == block["numerator"]
    assert report["gate_inputs"]["outside_local_ratio"] == pytest.approx(block["value"])
    assert report["gate_inputs"]["n_headroom"] == report["metrics"]["headroom"]["numerator"]


def test_no_a_or_e_decision_in_runner():
    """本票**不得**宣告 A 或 E：Gate 判定一律 DEFERRED。"""
    report = runner.build_report()
    assert report["gate_status"]["gate_0_raw_agreement"] == \
        "NOT_COMPUTABLE_SINGLE_ANNOTATOR"
    assert report["gate_status"]["gate_1_sample_guard"] == "DEFERRED_TO_PRIMARY_BRAIN"
    assert report["gate_status"]["gate_2_a_vs_e"] == "DEFERRED_TO_PRIMARY_BRAIN"


# ══════════════════════════════════════════════════════════════════════════
# 8 §6-4 mutation：改壞分子／分母／母體／獨立路徑後，驗收測試**必須變紅**
# ══════════════════════════════════════════════════════════════════════════


def _mutant(path: pathlib.Path, old: str, new: str, name: str) -> types.ModuleType:
    """把模組原始碼注入一處破壞並在獨立命名空間重載（**不寫回 repo 檔案**）。"""
    src = path.read_text(encoding="utf-8")
    assert old in src, f"mutation 目標字串不存在於 {path.name}：{old!r}"
    src = src.replace(old, new, 1)
    mod = types.ModuleType(name)
    mod.__file__ = str(path)
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


def test_m1_numerator_break_turns_acceptance_red():
    """M1：把 `count_headroom_partition` 的分子改成「全部筆數」⇒ 驗收必須變紅。

    這正是 A1 `_precision()` 恆等式缺陷的同型：docstring 仍寫著正確公式，實作卻被改壞。
    """
    mutant = _mutant(
        RUN_PATH,
        "    numerator = len([i for i in headroom_ids\n"
        "                     if pop.stage3_class(i) in pop.OUTSIDE_LOCAL_CLASSES])",
        "    numerator = len(headroom_ids)",
        "mutant_m1",
    )
    ns = _eval_ns(mutant.count_headroom_partition)
    with pytest.raises(AssertionError, match="docstring 宣告"):
        _assert_declared_matches_impl(mutant, mutant.count_headroom_partition, ns)


def test_m2_denominator_break_turns_acceptance_red():
    """M2：把 `count_headroom` 的分母改成 `len(defensible) + 1` ⇒ 驗收必須變紅。"""
    mutant = _mutant(
        RUN_PATH,
        "    numerator = len([i for i in defensible_ids if i not in survivors])\n"
        "    denominator = len(defensible_ids)",
        "    numerator = len([i for i in defensible_ids if i not in survivors])\n"
        "    denominator = len(defensible_ids) + 1",
        "mutant_m2",
    )
    ns = _eval_ns(mutant.count_headroom)
    with pytest.raises(AssertionError, match="docstring 宣告"):
        _assert_declared_matches_impl(mutant, mutant.count_headroom, ns)


def test_m3_population_definition_break_turns_cross_check_red():
    """M3：主路徑的 v1-framed 母體條件漏掉整個 frame ⇒ 獨立路徑比對必須變紅。"""
    mutant = _mutant(
        RUN_PATH,
        "    v1_framed = [o.obs_id for o in ordered if o.v1_frame != UNFRAMED]",
        "    v1_framed = [o.obs_id for o in ordered\n"
        "                 if o.v1_frame != UNFRAMED and o.v1_frame != \"lunch\"]",
        "mutant_m3",
    )
    report = mutant.build_report()
    check = mutant.cross_check(report)
    assert not check["agrees"], "母體被改壞後獨立路徑竟仍然同意——比對無效"
    assert any(m.startswith("set:") for m in check["mismatches"])


def test_m4_independent_path_aliasing_turns_cross_check_red():
    """M4：讓獨立路徑直接複用主路徑的分子（掩蓋 M3 的破壞）⇒ 驗收必須變紅。

    這是「獨立重算路徑」最容易被無聲稀釋掉的退化形式，必須有測試守著。
    """
    # 前置：HEADROOM 必須至少含一個 LOCAL 筆，否則這處 mutation 在數值上是 no-op。
    baseline = runner.build_report()
    local_ids = [i for i in baseline["sets"]["HEADROOM"]
                 if pop.stage3_class(i) in pop.LOCAL_CLASSES]
    assert local_ids, "HEADROOM 沒有 LOCAL 筆，M4 將不會變紅——請先確認分類表"

    mutant = _mutant(
        RUN_PATH,
        "    outside = {i for i in headroom\n"
        "               if pop.stage3_class(i) not in pop.LOCAL_CLASSES}",
        "    outside = headroom",
        "mutant_m4",
    )
    report = mutant.build_report()
    check = mutant.cross_check(report)
    assert not check["agrees"], "獨立路徑被改為複用主路徑後竟仍然同意"
    assert any("headroom_partition" in m for m in check["mismatches"])
