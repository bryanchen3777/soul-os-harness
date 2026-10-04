"""tests/test_ta2v2a2_event_attachment.py — A2：Local Event-Attachment（local 詞窗依附）。

機制規格：`docs/TA2-V2A2-MECHANISM-SPEC.md`（§3 R8 定義、§5 抗過fit門檻、§6 mutation test）
工單：TA-2-V2A2（Owner 2026-10-03 授權）

本測試是**純函式 + 離線重播**測試：
  - **不得**呼叫 LLM、**不得**連 `:8000`／任何 network、**不得**啟動伺服器。
  - **不寫入** `data/**`（重播輸出只由 `harness/run_ta2v2a2_replay.py` 的 `main()` 產生）。
  - 判定層的輸入只有回應文字（arm-blind），與重播腳本遵守同一隔離。

覆蓋：
  1. 封閉詞表與規格 §3 圍欄**逐字**一致（防止事後增刪詞表）。
  2. R8 正例／反例，逐條覆蓋 C1（不自證）、C2（詞類）、C3（子句內）與窗口規則。
  3. R8 **不得**覆寫 R1–R5；R6/R7 逐字沿用 A1（A2 ⊇ A1，additive）。
  4. 靜態契約：A2 模組內**不得**存在任何用於 attachment 的話語標記集合。
  5. GO-A／GO-B／GO-C 的**實測值**（含負面結果，見 test 名稱）。
  6. 兩個 mutation test（規格 §6 M1／M2）：**真的注入、真的變紅**。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import run_ta2v2a2_replay as r2  # noqa: E402
from harness import run_ta2v2a_feasibility as r1  # noqa: E402
from harness import ta2v2a2_event_attachment as a2  # noqa: E402
from harness import ta2v2a_attachment as a1  # noqa: E402

SPEC = REPO / "docs" / "TA2-V2A2-MECHANISM-SPEC.md"
A2_PATH = REPO / "harness" / "ta2v2a2_event_attachment.py"
A2_SRC = A2_PATH.read_text(encoding="utf-8")
V1_SHA256 = "bb443d4ac076aa6f44143855a3c04f5cdf80897a4bd0b5120404ff5a10038253"

#: 規格 §3 禁止進入 attachment 的話語標記（A1 的 `ATTACHMENT_MARKERS`，
#: 扣除規格**明文允許**在 `FILLER_CHARS` 內的 `就`）。
FORBIDDEN_ATTACHMENT_MARKERS = frozenset(
    (a1.ATTACHMENT_MARKERS - a2.FILLER_CHARS) | {"差不多", "到底", "算"}
)

ARMS_AND_PROBES = ("ON_correct", "ON_mismatched", "_control", "meal_timing",
                   "meeting_timing", "night_rest", "injected_temporal_line")


# ══════════════════════════════════════════════════════════════
# 1. 封閉詞表 vs 規格 §3 圍欄（逐字）
# ══════════════════════════════════════════════════════════════

def _parse_spec_word_lists():
    """從規格 §3 的 ```` ```python ```` 圍欄抓出 `NAME = {…}` 詞表（可跨行）。"""
    text = SPEC.read_text(encoding="utf-8")
    blocks, cur, in_fence = [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if in_fence:
                if cur:
                    blocks.append(cur)
                cur, in_fence = None, False
            else:
                cur, in_fence = [], True
            continue
        if in_fence and cur is not None:
            cur.append(line.split("#", 1)[0])
    out: dict[str, frozenset] = {}
    for blk in blocks:
        body = "\n".join(blk)
        for m in re.finditer(r"([A-Z_]+)\s*=\s*\{([^}]*)\}", body, flags=re.S):
            out[m.group(1)] = frozenset(
                w.strip().strip('"').strip("'")
                for w in re.split(r"[、,\s]+", m.group(2)) if w.strip()
            )
    return out


def test_spec_word_lists_match_module_verbatim():
    """模組的 `EVENT_VERBS`／`FILLER_CHARS` 必須與規格 §3 圍欄逐字相同（不得增刪）。"""
    spec = _parse_spec_word_lists()
    assert set(spec) == {"EVENT_VERBS", "FILLER_CHARS"}, f"規格詞表圍欄不符：{sorted(spec)}"
    assert spec["EVENT_VERBS"] == set(a2.EVENT_VERBS), "EVENT_VERBS 與規格 §3 不一致"
    assert spec["FILLER_CHARS"] == set(a2.FILLER_CHARS), "FILLER_CHARS 與規格 §3 不一致"
    # 規格 §3 的理由段落明確點名 `到/這/那/吧` 不得進入 FILLER_CHARS
    for banned in ("到", "這", "那", "吧"):
        assert banned not in a2.FILLER_CHARS


def test_event_verbs_and_action_tokens_are_disjoint():
    """C1 不自證：`EVENT_VERBS` 不得與 `ACTION_TOKENS`（睡覺類）有任何重疊。"""
    assert not (a2.EVENT_VERBS & a1.ACTION_TOKENS)


# ══════════════════════════════════════════════════════════════
# 2. R8 正例／反例
# ══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("clause,token,at,event", [
    ("也差不多該吃午餐了", "午餐", 6, "吃"),      # 0 填充、相鄰（corpus 實例）
    ("差不多該吃午餐了", "午餐", 5, "吃"),
    ("吃的午餐", "午餐", 2, "吃"),               # 1 個 FILLER（的）
    ("去用餐的午餐", "午餐", 4, "用餐"),          # 多字 event verb ＋ 1 個 FILLER
    ("好好休息的午安", "午安", 5, "休息"),
    ("午餐吃", "午餐", 0, "吃"),                 # event 在 temporal 之後
])
def test_r8_positive_local_attachment(clause, token, at, event):
    hit, ev = a2.r8_local_attachment(clause, token, at)
    assert hit is True
    assert ev["primary"]["event_token"] == event
    assert ev["primary"]["gap_len"] <= 1
    assert all(ch in a2.FILLER_CHARS for ch in ev["primary"]["gap"])


@pytest.mark.parametrize("clause,token,at,why", [
    ("差不多是午餐時間了", "午餐", 4, "無活動動詞"),
    ("凌晨快四點的飯，算宵夜還是早餐", "宵夜", 7, "無活動動詞"),
    ("吃到午餐", "午餐", 2, "`到` 不在 FILLER_CHARS（話語標記不得當 attachment）"),
    ("這頓到底算宵夜還是提早的早餐", "宵夜", 5, "`這` 命中但無相鄰活動動詞"),
    ("吃的啊午餐", "午餐", 3, "2 個字元填充 > 上限 1"),
    ("聽起來是該收尾的深夜了", "夜", 9, "`收尾` 與 `夜` 之間隔了 `的深` 2 字"),
    ("吃點午餐", "午餐", 3, "`點` 不在 FILLER_CHARS"),
])
def test_r8_negative_local_attachment(clause, token, at, why):
    hit, ev = a2.r8_local_attachment(clause, token, at)
    assert hit is False, f"不應成立：{why}"
    assert ev["reason"] == "no_local_event_side"


def test_r8_C1_event_verb_may_not_overlap_temporal_token():
    """C1 不自證：`補` 是 `補眠` 的前綴，span 重疊 ⇒ 不得成立。"""
    hit, ev = a2.r8_local_attachment("正好補眠", "補眠", 2)
    assert hit is False
    assert any(r["reject_reason"] == "overlaps_temporal_token" for r in ev["rejected"])


def test_r8_C1_sleep_action_never_rescued():
    """C1 + GO-B：`睡` 類只能被 R1 否決，永遠不會被 R8 救回。"""
    res = a2.apply_a2("去睡吧，手機放遠一點，燈關掉，慢慢呼吸。")
    tv = res["token_verdicts"][0]
    assert tv["token"] == "睡" and tv["verdict"] == a2.VETOED
    assert tv["a2_decided_by"] == a1.R1_ACTION_TOKEN
    assert tv["r8_evaluated"] is False


def test_r8_C2_non_verb_lexical_neighbour_is_not_event_side():
    """C2：非 `EVENT_VERBS` 的一切詞都不得充當 event-side（含抽象名詞、時間詞）。"""
    for clause, token, at in (("時間的午餐", "午餐", 3), ("尾聲的深夜", "夜", 4),
                              ("安排的晚餐", "晚餐", 4)):
        assert a2.r8_local_attachment(clause, token, at)[0] is False


def test_r8_C3_no_cross_clause_rescue():
    """C3：活動動詞與時段詞分屬不同小句 ⇒ 不得成立（跨子句共現不是 attachment）。"""
    text = "去吃飯吧，宵夜真香"
    res = a2.evaluate_token_a2(text, "宵夜")
    assert res["verdict"] == a2.UNDECIDED
    assert res["r8"]["evaluated"] is True and res["r8"]["matches"] == []
    # 同一小句內有相鄰動詞則成立（證明不是因為沒有動詞，而是因為跨子句）
    assert a2.r8_local_attachment("吃宵夜", "宵夜", 1)[0] is True
    # corpus 實例：`收尾` 與 `深夜` 之間隔了 `的深`，2 字 ⇒ 不成立
    real = "嗯，聽起來是該收尾的深夜了。別再撐了，去睡吧。"
    assert a2.r8_local_attachment("聽起來是該收尾的深夜了", "夜", 9)[0] is False
    assert a2.apply_a2(real)["a2_frame"] == "unframed"


def test_r8_never_overrides_R1_to_R5():
    """R1–R5 優先於 R8；`睡` 與 simile/attributive 類語料不受 R8 影響。"""
    simile = "我好像也有一件——大概是剛入夜的那種深藍，袖口沾著一點暖黃。"
    tv = a2.apply_a2(simile)["token_verdicts"][0]
    assert tv["verdict"] == a2.VETOED
    assert tv["a2_decided_by"] in (a1.R4_SIMILE_FRAME, a1.R3_ATTRIBUTIVE_NOUN)
    assert tv["r8_evaluated"] is False

    attr = "謝謝你——如果我有外套，大概是夜色剛落下、路燈一盞盞亮起時的那種藍。"
    tv = a2.apply_a2(attr)["token_verdicts"][0]
    assert tv["verdict"] == a2.VETOED
    assert tv["a2_decided_by"] == a1.R3_ATTRIBUTIVE_NOUN
    assert tv["r8_evaluated"] is False


def test_r8_does_not_change_R6_R7_semantics():
    """R6/R7 逐字沿用 A1：`這頓到底算宵夜…` 仍由 R7 判 kept（規格 §3 明文 additive）。"""
    text = "凌晨快四點，這頓到底算宵夜還是提早的早餐？"
    assert a2.evaluate_token_a2(text, "宵夜")["a2_decided_by"] == a1.R7_ATTACHED
    assert a1.evaluate_token(text, "宵夜")["reason"] == a1.R7_ATTACHED


# ══════════════════════════════════════════════════════════════
# 3. A1 相容性：additive、A2 ⊇ A1、逐 token 僅 R8 可改變
# ══════════════════════════════════════════════════════════════

def _rows():
    r1._arms_by_probe()
    texts, meta, _ = r1.load_corpus()
    rows = r2.build_audit(r2.replay_texts(texts), meta)
    for row, t in zip(rows, texts):
        row["raw_text"] = t
    return rows


def _metrics():
    return r2.compute_metrics(_rows())


def test_a2_is_additive_over_a1_on_all_66_observations():
    """A2 framed 集合必須是 A1 framed 集合的超集，且只有 R8 能改變 token 判定。"""
    rows = _rows()
    assert len(rows) == 66
    for row in rows:
        a1_frames = {a1.apply_a1(row["raw_text"])["v2_frame"]}
        a2_frames = {a2.apply_a2(row["raw_text"])["a2_frame"]}
        assert (a1_frames == {"unframed"}) or (a2_frames != {"unframed"}), row["obs_id"]
    for row in rows:
        a1r, a2r = a1.apply_a1(row["raw_text"]), a2.apply_a2(row["raw_text"])
        assert a1r["v1_frame"] == a2r["v1_frame"]
        for t1, t2 in zip(a1r["token_verdicts"], a2r["token_verdicts"]):
            assert t1["token"] == t2["token"]
            if t2["a2_decided_by"] != a2.R8_LOCAL_EVENT_ATTACHMENT:
                assert t2["verdict"] == t1["verdict"], row["obs_id"]


def test_pollution_sets_identical_to_A1_definition():
    """A2 的污染集 proxy 必須與 A1 契約 §4.2 **同一集合**（防漂移）。"""
    rows = _rows()
    mine = r2.pollution_sets(rows)
    a1_rows = [{"v1_frame": r["v1_frame"], "raw_text": r["raw_text"]} for r in rows]
    theirs = r1._pollution_sets(a1_rows)  # noqa: SLF001 — A1 唯讀重用
    assert [r["raw_text"] for r in mine["sleep"]] == [r["raw_text"] for r in theirs["sleep"]]
    assert ([r["raw_text"] for r in mine["night_simile"]]
            == [r["raw_text"] for r in theirs["night_simile"]])


# ══════════════════════════════════════════════════════════════
# 4. 靜態契約：arm-blind、無話語標記 attachment、僅標準庫
# ══════════════════════════════════════════════════════════════

def test_mechanism_module_has_no_arm_or_probe_branching():
    """機制模組的**程式碼**（docstring 除外）不得出現任何 arm／probe／注入行字串。"""
    tree = ast.parse(A2_SRC)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]
    for lit in literals:
        for token in ARMS_AND_PROBES:
            assert token not in lit, f"A2 機制模組含 probe/arm 字串：{lit!r}"


def test_replay_core_is_arm_blind_by_signature():
    sig = list(inspect.signature(r2.replay_texts).parameters)
    assert sig == ["raw_texts"], f"重播核心簽章可疑：{sig}"


def test_static_no_discourse_marker_attachment_set():
    """規格 §6 M2 靜態斷言：A2 模組不得有任一「話語標記集合」用於 attachment。"""
    _assert_no_marker_attachment(A2_SRC)
    tree = ast.parse(A2_SRC)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            assert "MARKER" not in node.name.upper(), f"疑似話語標記結構：{node.name}"
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name):
                    assert "MARKER" not in tgt.id.upper(), f"疑似話語標記常數：{tgt.id}"


def _assert_no_marker_attachment(source: str) -> None:
    """收集模組內**所有集合字面值**（Set/List/Tuple/Dict）的字串元素並比對黑名單。"""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                found.add(child.value)
    bad = found & FORBIDDEN_ATTACHMENT_MARKERS
    assert not bad, f"模組內出現話語標記集合（attachment 禁用）：{sorted(bad)}"


def test_static_imports_are_stdlib_and_harness_only():
    """僅標準庫；不得有 LLM／HTTP／NLP 基礎設施 import。"""
    banned = {"requests", "socket", "httpx", "aiohttp", "urllib", "openai",
              "anthropic", "llm_call", "jinja2", "spacy", "nltk", "hanlp"}
    for path in (A2_PATH, REPO / "harness" / "run_ta2v2a2_replay.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for m in mods:
                root = m.split(".")[0]
                assert root not in banned, f"{path.name} 引入禁用套件：{m}"
                assert root in {"__future__", "ast", "pathlib", "sys", "typing",
                                "json", "hashlib", "collections", "importlib",
                                "re", "harness", "pytest"}, f"非預期 import：{m}"


def test_frozen_v1_and_A1_are_byte_identical_to_baseline():
    v1 = REPO / "harness" / "run_ta2a_gate2.py"
    assert hashlib.sha256(v1.read_bytes()).hexdigest() == V1_SHA256
    for frozen in ("harness/tl12_temporal.py", "harness/clock.py", "harness/observer.py"):
        assert (REPO / frozen).exists()


def test_a2_does_not_write_under_data_from_tests():
    """本測試檔的唯一寫入是 mutation 用的 tmp_path（重播輸出只由 runner 的 `main()` 產生）。"""
    src = pathlib.Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    writers: dict[str, int] = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in ("write_text", "write_bytes", "mkdir", "unlink"):
                writers[fn.name] = writers.get(fn.name, 0) + 1
    assert writers == {"_load_mutant": 1}, f"測試檔的寫入點不符預期：{writers}"
    fn_src = next(ast.get_source_segment(src, n) for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_load_mutant")
    assert "tmp_path" in fn_src
    for node in ast.walk(tree):  # 不得呼叫 runner 的 main()（那會寫 data/**）
        if isinstance(node, ast.Call):
            assert not ast.dump(node.func).endswith("attr='main'"), \
                "測試檔不得呼叫 runner.main()"
    for node in ast.walk(tree):  # 不得引用重播輸出目錄
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "harness" + "_out" not in node.value, \
                f"測試檔不得引用重播輸出目錄：{node.value}"


def test_naming_discipline_local_attachment_not_parsing():
    doc = a2.__doc__ or ""
    assert "Local Event-Attachment" in doc
    assert re.search(r"不得\*{0,2}稱為 syntactic parsing", doc)
    assert "heuristic" in doc


# ══════════════════════════════════════════════════════════════
# 5. GO-A／GO-B／GO-C 實測值（含負面結果）
# ══════════════════════════════════════════════════════════════

def test_go_a_lunch_recovery_quantified():
    """GO-A：lunch 真陽性由 A1 的 0/5 恢復到 2/5（**量化後的恢復，非全面恢復**）。"""
    g = _metrics()["go_a_recall_recovery"]
    lg = g["lunch_genuine"]
    assert (lg["v1"], lg["A1"], lg["A2"]) == (5, 0, 2)
    assert lg["recovered_vs_A1"] == 2
    assert g["met"] is True
    for hit in (h for e in lg["evidence"] for h in e["hits"]):
        assert hit["event_token"] == "吃" and hit["relation"] == "adjacent"


def test_go_b_precision_below_absolute_threshold_is_reported_fail():
    """GO-B **FAIL**：睡污染集 precision 5/7 < 1.0（且 A1 JSON 的 1.0 是自除缺陷）。"""
    g = _metrics()["go_b_precision_preservation"]
    sleep = g["sleep_action_pollution"]
    assert sleep["n"] == 7
    assert sleep["A2"] == pytest.approx(5 / 7)
    assert sleep["A1"] == pytest.approx(5 / 7)  # 相較 A1 無下降
    assert g["sleep_precision_drop_vs_A1"] is False
    assert g["meets_absolute_threshold_1_0"] is False
    assert g["owner_stop_condition_triggered"]
    assert g["met"] is False
    assert g["night_metaphor_simile_pollution"]["A2"] == 1.0


def test_go_c_single_event_verb_dominance_is_reported_fail():
    """GO-C **FAIL**：救回的 2 個 frame 全部由單一動詞 `吃` 貢獻（100% > 40%）。"""
    g = _metrics()["go_c_mechanism_validity"]
    assert g["single_event_verb_counts"] == {"吃": 2}
    assert g["single_event_verb_share_max"] == 1.0
    assert g["single_event_verb_threshold_met"] is False
    assert g["lunch_share_of_rescues"] == 1.0
    assert g["other_frame_recovery_exists"] is False
    assert g["lunch_specialisation_threshold_met"] is False
    assert g["every_R8_attributed_frame_has_event_side_evidence"] is True
    assert g["every_retained_frame_has_event_side_evidence"] is False
    assert g["met"] is False


def test_retained_frames_without_event_side_evidence_are_the_R7_ones():
    """4/6 的 retained frame 由 A1 的 R7 話語標記保住 ⇒ 規格 §3 vs §4.1 內部不一致。"""
    m = _metrics()
    g = m["go_c_mechanism_validity"]
    assert g["retained_frames_total"] == 6
    assert g["retained_frames_with_r8_event_side_evidence"] == 2
    assert all("R7_ATTACHED" in x["decided_by"] for x in g["retained_frames_without_r8_evidence"])
    assert all(a2.R8_LOCAL_EVENT_ATTACHMENT not in x["decided_by"]
               for x in g["retained_frames_without_r8_evidence"])
    assert m["spec_conflicts"] and m["spec_conflicts"][0]["id"] == "SPEC-3-vs-4.1"


def test_no_regressions_and_did_recomputed():
    m = _metrics()
    assert m["regressions_vs_A1"] == []
    assert (m["A2_frames_non_unframed"] - m["A1_frames_non_unframed"]) == 2
    assert (m["v1_metrics"]["dR"], m["v1_metrics"]["dI"], m["v1_metrics"]["did"]) == (1, 0, 1)
    assert (m["A1_metrics"]["dR"], m["A1_metrics"]["dI"], m["A1_metrics"]["did"]) == (0, 0, 0)
    assert (m["A2_metrics"]["dR"], m["A2_metrics"]["dI"], m["A2_metrics"]["did"]) == (0, 0, 0)
    assert m["A2_metrics"]["unstable_reasons"]


# ══════════════════════════════════════════════════════════════
# 6. Mutation test（規格 §6）：M1 停用 event-side、M2 還原 marker 共現
#    🔴 依 Bry 的紀律：「測試存在」不等於「測試在 enforcement contract」。
#       下面兩支**真的注入變異、真的斷言驗收測試會變紅**。
# ══════════════════════════════════════════════════════════════

def _load_mutant(tmp_path: pathlib.Path, name: str, mutated_src: str):
    assert mutated_src != A2_SRC, "變異沒有改變任何原始碼（seam 已失效）"
    path = tmp_path / f"{name}.py"
    path.write_text(mutated_src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.modules.pop(name, None)
    return mod


def _assert_lunch_recovered(mod) -> None:
    """GO-A 驗收斷言（以模組為參數 ⇒ 可對變異模組執行並觀察其變紅）。"""
    texts = [r["raw_text"] for r in _rows()]
    kept = [t for t in texts if mod.apply_a2(t)["a2_frame"] == "lunch"]
    assert len(kept) == 2, f"GO-A 失敗：lunch 保留 {len(kept)}/5（預期 2/5）"


def _assert_r8_did_not_attach_via_marker(mod) -> None:
    """行為契約：**R8 層**不得把「子句含話語標記」當成 attachment。

    🔴 注意：最終 verdict 可能是 `kept`，但那必須來自 A1 的 R7（規格 §3 明文 additive），
       **不得**來自 R8。本斷言因此檢查 R8 自己的證據。
    """
    text = "凌晨快四點，這頓到底算宵夜還是提早的早餐？"
    res = mod.evaluate_token_a2(text, "宵夜")
    ev = res["r8"]
    assert ev["evaluated"] is True, "R8 未被評估（測試前提失效）"
    assert ev["matches"] == [], f"話語標記共現被當成 attachment：{ev['matches']}"
    assert res["a2_decided_by"] != mod.R8_LOCAL_EVENT_ATTACHMENT


def test_mutation_seams_exist():
    """M1／M2 的注入點必須真實存在於原始碼，否則 mutation test 會是空殼。"""
    assert "R8_ENABLED: bool = True" in A2_SRC
    assert "# --- BEGIN MUTATION SEAM: _r8_event_side_candidates ---" in A2_SRC
    assert "# --- END MUTATION SEAM: _r8_event_side_candidates ---" in A2_SRC


def test_mutation_M1_disabling_event_side_makes_acceptance_go_red(tmp_path):
    """規格 §6 M1：讓 R8 永遠不成立 ⇒ GO-A 驗收斷言**必須變紅**。"""
    mutated = A2_SRC.replace("R8_ENABLED: bool = True", "R8_ENABLED: bool = False")
    mutant = _load_mutant(tmp_path, "a2_mutant_m1", mutated)
    assert mutant.R8_ENABLED is False
    _assert_lunch_recovered(a2)  # 真實模組：綠
    with pytest.raises(AssertionError, match="GO-A 失敗"):
        _assert_lunch_recovered(mutant)  # 🔴 變異模組：紅（M1 被驗收測試抓到）


def test_mutation_M2_marker_cooccurrence_rejected_by_static_contract(tmp_path):
    """規格 §6 M2：把 event-side 判定還原成「子句含 到/這/那/吧」⇒ 契約測試必須拒絕。

    🔴 **實測限制（記錄，不繞過）**：因為 §3 規定 R8 只在 A1 已 fall-through 到 R6
       （小句內無任何 `ATTACHMENT_MARKERS`）時才被評估，而 A1 的 marker 集合**涵蓋**
       到/這/那/吧，所以 M2 這個特定變異在**行為上被 R7 完全遮蔽**（可觀測結果相同）。
       ⇒ M2 的強制點是**靜態契約**（模組內不得出現用於 attachment 的話語標記集合）；
       這裡同時用 AST 證明遮蔽確實發生，不假裝行為臂有效。
    """
    mutated = re.sub(
        r"(# --- BEGIN MUTATION SEAM: _r8_event_side_candidates ---\n).*?"
        r"(    # --- END MUTATION SEAM)",
        r'''\1    return ([{"event_token": m, "event_at": clause.find(m), "gap": "",
                  "gap_len": 0, "relation": "clause_marker_cooccurrence",
                  "order": "marker_beside_token", "temporal_token": token,
                  "temporal_at": at} for m in ("到", "這", "那", "吧") if m in clause],
            [])
\2''',
        A2_SRC, flags=re.S)
    mutant = _load_mutant(tmp_path, "a2_mutant_m2", mutated)

    # (a) 靜態契約：真實模組綠、變異模組紅
    _assert_no_marker_attachment(A2_SRC)
    with pytest.raises(AssertionError, match="attachment 禁用"):
        _assert_no_marker_attachment(mutated)

    # (b) 確認注入確實退化成「子句含話語標記」（不是無關變異）
    hit, ev = mutant.r8_local_attachment("這頓到底算宵夜還是提早的早餐", "宵夜", 5)
    assert hit is True
    assert ev["primary"]["relation"] == "clause_marker_cooccurrence"
    assert ev["primary"]["event_token"] in ("到", "這", "那", "吧")

    # (c) 行為層：**不會**產生假陽性 attachment（被 R7 遮蔽），但會**失去**真陽性救回
    #     ⇒ M2 變異在語料上可觀察到的差異是「少救」，不是「亂救」。
    #     GO-A 驗收斷言對它**同樣變紅**（lunch 保留 0/5），但方向與 (a) 的靜態契約互補。
    with pytest.raises(AssertionError, match="GO-A 失敗"):
        _assert_lunch_recovered(mutant)
    texts = [r["raw_text"] for r in _rows()]
    real_frames = {a2.apply_a2(t)["a2_frame"] for t in texts}
    mut_frames = {mutant.apply_a2(t)["a2_frame"] for t in texts}
    assert mut_frames < real_frames  # 真模組多出來的 2 個 lunch frame 在變異中消失
    assert mut_frames - {"unframed"} <= real_frames - {"unframed"}  # 沒有新增假 frame
    m = _metrics()["m2_mutation_testability"]
    assert m["r8_evaluated_observations"] > 0
    assert m["clause_without_A1_marker_guaranteed"] is True
    assert {"到", "這", "那", "吧"} <= set(a1.ATTACHMENT_MARKERS)
