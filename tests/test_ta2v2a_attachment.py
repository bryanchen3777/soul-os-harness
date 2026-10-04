"""tests/test_ta2v2a_attachment.py — TA-2 v2-A 候選 A（語法依附）A1 veto 濾層。

預登記契約：`docs/TA2-V2A-FEASIBILITY-CONTRACT.md`（§2 規則表、§3 兩段式判定）。
工單：`docs/TA2-V2A-FEASIBILITY-WORK-ORDER.md`。

本測試是**純函式**測試：
  - **不得**呼叫 LLM、**不得**連 `:8000`、**不得**開 network。
  - 不寫入 `data/soul/**`、不寫入 `data/elevation/**`（不碰任何 data_root）。
  - 輸入只有回應文字（arm-blind），與重播腳本遵守同一隔離。

覆蓋（契約 AC6）：每條 rule_id 至少一例**正例**與一例**反例**；
`R2` 依契約 §3.5 採**規則述語單元測試**（v1 順序下不可達，不構造不可能的 corpus 案例）。
另含 AC5：模組詞表與契約 §2.1／§3.4 圍欄**逐字一致**。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from harness import ta2v2a_attachment as a1  # noqa: E402
from harness.run_ta2a_gate2 import _EXCLUDE_FOR, extract_temporal_frame  # noqa: E402

CONTRACT = REPO / "docs" / "TA2-V2A-FEASIBILITY-CONTRACT.md"


# ══════════════════════════════════════════════════════════════
# AC5 — 詞表完整性：模組 vs 契約圍欄，逐字一致
# ══════════════════════════════════════════════════════════════

def _parse_contract_word_lists():
    """從契約抓出所有 `NAME = {a, b, c}` 圍欄區塊 → [dict]（每個詞表圍欄一份）。"""
    text = CONTRACT.read_text(encoding="utf-8")
    blocks, cur, in_fence = [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if in_fence:
                in_fence = False
                if cur:
                    blocks.append(cur)
                    cur = None
            else:
                in_fence = True
                cur = {}
            continue
        if not in_fence or cur is None:
            continue
        stripped = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Z_]+)\s*=\s*\{(.*)\}$", stripped)
        if not m:
            continue
        cur[m.group(1)] = frozenset(
            x.strip() for x in re.split(r"[、,]", m.group(2)) if x.strip()
        )
    return [b for b in blocks if b]


def test_ac5_contract_word_lists_match_module():
    """契約每一個詞表圍欄都必須與模組常數逐字一致，且兩個圍欄彼此相同。"""
    blocks = _parse_contract_word_lists()
    assert len(blocks) == 2, f"契約應有 2 個詞表圍欄（§2.1 / §3.4），實得 {len(blocks)}"
    module = {
        "ACTION_TOKENS": a1.ACTION_TOKENS,
        "COMPOUND_TIME_NOUNS": a1.COMPOUND_TIME_NOUNS,
        "COLOR_APPEARANCE_NOUNS": a1.COLOR_APPEARANCE_NOUNS,
        "SIMILE_MARKERS": a1.SIMILE_MARKERS,
        "NEGATION_MARKERS": a1.NEGATION_MARKERS,
        "ATTACHMENT_MARKERS": a1.ATTACHMENT_MARKERS,
    }
    for i, blk in enumerate(blocks):
        assert set(blk) == set(module), f"圍欄 {i} 的詞表名稱集合不符：{sorted(blk)}"
        for name, expected in module.items():
            assert blk[name] == expected, f"圍欄 {i} 的 {name} 與模組不一致"
    assert blocks[0] == blocks[1], "契約 §2.1 與 §3.4 的詞表必須逐字相同"


# ══════════════════════════════════════════════════════════════
# R1 — ACTION_TOKEN
# ══════════════════════════════════════════════════════════════

def test_r1_positive_well_known_pollution_sleep():
    """R1 正例（工單案例表）：「去睡吧，別再撐了。」⇒ `vetoed`。"""
    out = a1.apply_a1("去睡吧，別再撐了。")
    assert out["v1_frame"] == "night"
    assert out["T"] == ["睡"]
    assert out["token_verdicts"][0]["verdict"] == "vetoed"
    assert a1.R1_ACTION_TOKEN in out["token_verdicts"][0]["rules"]
    assert out["a1_verdict"] == "vetoed"
    assert out["v2_frame"] == "unframed"


def test_r1_negative_compound_time_noun_is_not_action_token():
    """R1 反例（工單案例表）：「午睡一下比較好」**不得** `vetoed`。

    `午睡` 是 `COMPOUND_TIME_NOUNS`（v1 siesta 幀的合法觸發詞），`R1` 不得作用於它。
    框架層結果為 `undecided`（該句無依附標記 → R6），**不是** `vetoed`。
    """
    assert a1.r1_action_token("午睡") is False
    assert "午睡" in a1.COMPOUND_TIME_NOUNS
    assert "午睡" not in a1.ACTION_TOKENS
    assert a1.r1_action_token("補眠") is False

    out = a1.apply_a1("午睡一下比較好")
    assert out["v1_frame"] == "siesta"          # v1 仍判 siesta
    assert out["T"] == ["午睡"]
    assert a1.R1_ACTION_TOKEN not in out["token_verdicts"][0]["rules"]
    assert out["a1_verdict"] != "vetoed"        # 🔴 不是 vetoed
    assert out["a1_verdict"] == "undecided"
    assert a1.R6_NO_ATTACHMENT in out["token_verdicts"][0]["rules"]


# ══════════════════════════════════════════════════════════════
# R2 — EXCLUDE_TOKEN（防禦性規則，v1 順序下不可達；契約 §3.5）
# ══════════════════════════════════════════════════════════════

def test_r2_positive_predicate_vetoes_when_exclude_hit():
    """R2 正例：排除詞命中 ⇒ 判 `vetoed` 並記 `R2`（純述語單元測試）。"""
    assert a1.r2_exclude_token(True) is True
    v = a1.judge_token_at("夜深了補眠", "夜", 0, exclude_hit=True)
    assert v["verdict"] == "vetoed"
    assert a1.R2_EXCLUDE_TOKEN in v["rules"]


def test_r2_negative_predicate_and_unreachability_under_v1():
    """R2 反例：排除詞未命中 ⇒ 不記 `R2`；且 v1 指派 frame 時結構上不可能命中。"""
    assert a1.r2_exclude_token(False) is False
    v = a1.judge_token_at("夜深了", "夜", 0, exclude_hit=False)
    assert a1.R2_EXCLUDE_TOKEN not in v["rules"]
    # v1 先比 siesta（含 補眠）與 late_night_meal（含 宵夜），
    # 故一旦 v1 指派 night/early_breakfast，其排除詞必不在原文。
    for frame, bad in _EXCLUDE_FOR.items():
        sample = {"night": "夜深了該睡", "early_breakfast": "吃早餐"}[frame]
        assert bad[0] not in sample
        assert extract_temporal_frame(sample) == frame
        assert a1.exclude_hit_for(frame, sample) is False


# ══════════════════════════════════════════════════════════════
# R3 — ATTRIBUTIVE_NOUN
# ══════════════════════════════════════════════════════════════

def test_r3_positive_night_color_compound():
    """R3 正例（工單案例表）：「大概是夜色剛落下…那種藍」⇒ `vetoed`。"""
    out = a1.apply_a1("大概是夜色剛落下…那種藍")
    assert out["v1_frame"] == "night"
    assert out["T"] == ["夜"]
    rules = out["token_verdicts"][0]["rules"]
    assert out["token_verdicts"][0]["verdict"] == "vetoed"
    assert a1.R3_ATTRIBUTIVE_NOUN in rules          # R3 為主記錄
    assert a1.R4_SIMILE_FRAME in rules               # §2.2.2：R4 也要寫入 ledger
    assert out["a1_verdict"] == "vetoed"
    assert out["v2_frame"] == "unframed"


def test_r3_positive_suffix_modifier_window():
    """R3 情形 B（契約 §2.3.6）：「夜的顏色」——窗口內只准 `剛`／`的`，且 ≤3 字。"""
    assert a1.r3_attributive_noun("夜的顏色很適合", "夜") is True
    assert a1.r3_attributive_noun("夜剛的顏色很適合", "夜") is True
    # 🔴 窗口內出現 `剛`／`的` 以外的字 ⇒ 不成立（契約 §2.3.6 情形 B 逐字定義）。
    #    這是規則表設計如此，**不因為這樣太嚴就放寬**。
    assert a1.r3_attributive_noun("夜剛落的顏色", "夜") is False
    #    窗口 > 3 字亦不成立（「的」×4 落在 token 與名詞之間，間隔 = 4）。
    assert a1.r3_attributive_noun("夜的了的的顏色", "夜") is False


def test_r3_negative_zhongwu_is_not_attributive():
    """R3 反例（工單案例表）：「那就中午吧」**不得**因 R3 而 veto。

    依規則實算：`中午` 不修飾任何外觀類名詞；且 v1 無 `中午` 觸發詞 ⇒ v1 已是 `unframed`，
    A1 判定為 `undecided`（不進入規則表）。工單要求「`kept` 或 `undecided`，依實算」——
    實算結果是 `undecided`。**不得為了讓它變 `kept` 而改規則。**
    """
    assert a1.r3_attributive_noun("那就中午吧", "中午") is False
    out = a1.apply_a1("那就中午吧")
    assert out["v1_frame"] == "unframed"
    assert out["a1_verdict"] == "undecided"
    assert out["T"] == []
    assert out["v2_frame"] == "unframed"


# ══════════════════════════════════════════════════════════════
# R4 — SIMILE_FRAME
# ══════════════════════════════════════════════════════════════

def test_r4_positive_clause_contains_simile_marker():
    """R4 正例：命中所在小句含 `好像`。"""
    assert a1.r4_simile_frame("好像夜就要來了") is True
    v = a1.judge_token_at("夜好像要來了", "夜", 0)
    assert v["verdict"] == "vetoed"
    assert a1.R4_SIMILE_FRAME in v["rules"]


def test_r4_negative_plain_event_clause():
    """R4 反例：無比喻標記的普通事件小句。"""
    assert a1.r4_simile_frame("那就夜吧") is False


# ══════════════════════════════════════════════════════════════
# R5 — NEGATION_FRAME
# ══════════════════════════════════════════════════════════════

def test_r5_positive_clause_contains_negation_marker():
    """R5 正例（工單案例表的否定小句「不是晚上」）。"""
    assert a1.r5_negation_frame("不是晚上") is True
    v = a1.judge_token_at("夜不是我說的那樣", "夜", 0)
    assert v["verdict"] == "vetoed"
    assert a1.R5_NEGATION_FRAME in v["rules"]


def test_r5_negative_plain_event_clause():
    """R5 反例：無否定標記的小句。"""
    assert a1.r5_negation_frame("夜深了") is False


# ══════════════════════════════════════════════════════════════
# R6 / R7 — 有無依附標記
# ══════════════════════════════════════════════════════════════

def test_r6_positive_no_attachment_marker_in_clause():
    """R6 正例：命中詞孤立，該小句內無任何依附標記 ⇒ `undecided`。"""
    assert a1.r6_no_attachment("夜深了") is True
    v = a1.judge_token_at("夜深了", "夜", 0)
    assert v["verdict"] == "undecided"
    assert v["rules"] == [a1.R6_NO_ATTACHMENT]


def test_r6_negative_clause_has_attachment_marker():
    """R6 反例：小句內有 `吧` ⇒ 走 R7 而非 R6。"""
    assert a1.r6_no_attachment("那就夜吧") is False


def test_r7_positive_attachment_marker_found():
    """R7 正例：小句內找到依附標記 ⇒ `kept`。"""
    assert a1.r7_attached("那就夜吧") is True
    v = a1.judge_token_at("那就夜吧", "夜", 2)
    assert v["verdict"] == "kept"
    assert v["rules"] == [a1.R7_ATTACHED]


def test_r7_negative_no_marker_and_no_veto_rule():
    """R7 反例：小句內找不到依附標記 ⇒ 不是 `kept`。"""
    assert a1.r7_attached("夜深了") is False


# ══════════════════════════════════════════════════════════════
# §3.2 兩段式彙總（**撤回重發的核心：不得回退成 frame 層直接否決**）
# ══════════════════════════════════════════════════════════════

def test_summary_1_mixed_vetoed_and_undecided_is_not_vetoed():
    """彙總 1（工單案例表）：「夜深了，該睡了」

    token 層：`夜` ≠ `vetoed`（R6，小句「夜深了」無 marker）、`睡` = `vetoed`（R1）。
    frame 層：**不得**直接判 `vetoed`（否則誤殺合法的 `夜`）。
    預登記真值表（契約 §3.3）預期為 `undecided` → 丟失 frame。
    """
    out = a1.apply_a1("夜深了，該睡了")
    assert out["v1_frame"] == "night"
    assert out["T"] == ["夜", "睡"]
    by_token = {t["token"]: t for t in out["token_verdicts"]}
    assert by_token["睡"]["verdict"] == "vetoed"
    assert a1.R1_ACTION_TOKEN in by_token["睡"]["rules"]
    assert by_token["夜"]["verdict"] != "vetoed"
    assert a1.R6_NO_ATTACHMENT in by_token["夜"]["rules"]

    # 🔴 核心斷言：不是全部 vetoed ⇒ 不得判 vetoed
    assert out["a1_verdict"] == "undecided"
    assert out["a1_verdict"] != "vetoed"
    assert out["v2_frame"] == "unframed"


def test_summary_2_bu眠_not_action_token():
    """彙總 2（工單案例表）：「大概補個眠吧」——`補眠` 不受 R1；`吧` 在依附標記內。

    🔴 誠實記錄：此句的 v1 frame 實為 `unframed`（`補眠` 需為連續子串，「補個眠」不是），
    故 A1 判定為 `undecided`、不進入規則表。**這是 v1 詞表的限制，不是 A1 的結果，
    依工單指示「依實算，不得硬寫期望值」——如實寫出，不為讓它變好看而改規則。**
    """
    assert a1.r1_action_token("補眠") is False
    assert a1.r7_attached("大概補個眠吧") is True

    out = a1.apply_a1("大概補個眠吧")
    assert out["v1_frame"] == "unframed"
    assert out["a1_verdict"] == "undecided"
    assert out["v2_frame"] == "unframed"

    # 若 v1 真的把 `補眠` 判進 siesta 幀，A1 也不得因 R1 殺掉它。
    s = a1.judge_token_at("大概補個眠吧", "補眠", 0)
    assert s["verdict"] != "vetoed" or a1.R1_ACTION_TOKEN not in s["rules"]


def test_summary_kept_wins_over_vetoed():
    """彙總真值表第 2 列：任一 `kept` ⇒ 保留 v1 frame（`kept` 勝過同筆的 `vetoed`）。"""
    tokens = [{"verdict": "vetoed"}, {"verdict": "kept"}]
    assert a1.summarize(tokens) == "kept"
    out = a1.apply_a1("那就夜吧，去睡吧")
    assert out["a1_verdict"] == "kept"
    assert out["v2_frame"] == "night"


def test_summary_all_vetoed_is_vetoed():
    """彙總真值表第 1 列：全部 `vetoed` ⇒ `vetoed` → `unframed`。"""
    assert a1.summarize([{"verdict": "vetoed"}, {"verdict": "vetoed"}]) == "vetoed"
    out = a1.apply_a1("去睡吧，就寢了")
    assert out["a1_verdict"] == "vetoed"
    assert out["v2_frame"] == "unframed"


def test_summary_empty_T_is_undecided_without_rules():
    """`T` 為空（v1 已是 `unframed`）⇒ 直接 `undecided`，**不進入規則表**。"""
    out = a1.apply_a1("我知道了")
    assert out["v1_frame"] == "unframed"
    assert out["T"] == []
    assert out["a1_verdict"] == "undecided"
    assert out["v2_frame"] == "unframed"
    assert out["rules"] == []


# ══════════════════════════════════════════════════════════════
# 模組自我約束（AC3 / AC4 / 凍結）
# ══════════════════════════════════════════════════════════════

def test_module_has_no_llm_or_network_imports():
    """AC3：規則模組**不得** import LLM / 網路（以 AST 檢查 import 陳述式，非全文字串）。

    🔴 用 AST 而非字串搜尋：模組 docstring 合法的提到「llm_call」以說明禁令本身，
    那不是 import。
    """
    src = (REPO / "harness" / "ta2v2a_attachment.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    banned = {"llm_call", "requests", "urllib", "socket", "httpx", "aiohttp",
              "subprocess", "harness.runner", "configs"}
    assert not (imported & banned), f"規則模組不得 import：{sorted(imported & banned)}"
    # 唯一允許的跨檔 import 是 frozen v1 規則表與兩個純函式。
    assert "harness.run_ta2a_gate2" in {n.module for n in ast.walk(tree)
                                        if isinstance(n, ast.ImportFrom) and n.module}


def test_t_order_follows_v1_rule_table_declaration_order():
    """§2.3.9：`T` 的順序 = v1 規則表宣告順序（`夜` 先於 `睡` 先於 `就寢`）。"""
    assert a1.frame_triggers("night") == ("夜", "睡", "就寢")
    assert a1.trigger_tokens("night", "就寢吧，該睡囉，夜深了") == ["夜", "睡", "就寢"]


def test_clause_splitting_is_the_only_segmentation_means():
    """分句手段封閉：只認 `。！？；：，、`，`…` 不切。"""
    assert a1.CLAUSE_SEPARATORS == "。！？；：，、"
    assert a1.split_clauses("去睡吧，別再撐了。") == ["去睡吧", "別再撐了", ""]
    assert a1.split_clauses("夜色剛落下…那種藍") == ["夜色剛落下…那種藍"]
