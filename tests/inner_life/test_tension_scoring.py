# tests/inner_life/test_tension_scoring.py
"""CAMPAIGN-B-P1 §4.1 T4/T5/T6 — 含時驅動評分（可稽核、字典序、三態窗一致）。

工單：logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md

覆蓋：
  - **T4** 三因子可稽核（每個計數都能指回資料面列；``source_rows`` 非空且與計數一致）。
  - **T5** 排序為**字典序**（無加權總分／無權重常數；AST 逐項釘死）。
  - **T6** 三態窗一致性（同一 elapsed 輸入下 ``decay_bucket`` 與
    ``classify_temporal_state`` 的三態一一對應；邊界 24h／7d 各測一次，含 ``<`` 與
    ``≥`` 兩側）。

沙盒：線頭檔寫進 ``SOUL_OS_DATA_DIR`` 指向的**隔離 tmp 資料根**
（``tests/conftest.py`` 的 autouse fixture）⇒ 生產 ``data/**`` 全程唯讀。

🔴 本檔不使用 subprocess／os.spawn*／os.system／os.popen（R5 護欄）。
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.inner_life import tension_scoring as ts
from src.soul.temporal_phenomenology import (
    BondEvidence,
    STATE_CALM,
    STATE_RESOLVED,
    STATE_TENSION,
    classify_temporal_state,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "src/inner_life/tension_scoring.py"

#: 沙盒假時鐘（epoch 秒）——固定值，0 真實等待。
FAKE_NOW = 1_700_000_000

#: 上游三態窗的邊界（秒）——**釘住** canonical 值（上游為 frozen contract）。
CALM_BOUNDARY_SEC = 24 * 3600
RESOLVED_BOUNDARY_SEC = 7 * 24 * 3600


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row(
    thread_id: str,
    *,
    status: str = "active",
    sage_fact_id=None,
    age_hours: int = 30,
    contradiction=None,
    title: str = "線頭",
) -> dict:
    row = {
        "thread_id": thread_id,
        "event_seq": 1,
        "event_type": "created",
        "origin_type": "necessity_driven",
        "status": status,
        "title": title,
        "narrative_content": "（沙盒測試資料）",
        "created_at": _iso(FAKE_NOW - age_hours * 3600),
        "updated_at": _iso(FAKE_NOW - age_hours * 3600),
        "check_after_ts": _iso(FAKE_NOW + 3600),
        "share_target": "none",
        "dissolved_at": None,
        "sage_fact_id": sage_fact_id,
    }
    if contradiction is not None:
        row["contradiction"] = contradiction
    return row


def _write_threads(agent_id: str, rows) -> Path:
    """把線頭列寫進**隔離**資料根（沙盒；不碰生產）。"""
    from src.paths import data_root

    target_dir = Path(data_root()) / "soul" / agent_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / "life_threads.jsonl"
    target.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return target


# ── T4：三因子可稽核 ─────────────────────────────────────────────────────
class TestT4FactorsAreAuditable:
    @pytest.fixture
    def sandbox_rows(self):
        return [
            _row("gap-1", age_hours=30),  # ⇒ 資訊缺口
            _row("gap-2", age_hours=48, contradiction="與舊推論衝突"),  # ⇒ 缺口＋矛盾
            _row("has-fact", sage_fact_id="fact-9", age_hours=30),  # ⇒ 有 fact ⇒ 非缺口
            _row("fresh", age_hours=1),  # ⇒ 太新 ⇒ 非缺口
            _row("closed", status="completed", age_hours=30),  # ⇒ 非 active ⇒ 非缺口
        ]

    def test_t4_counts_match_the_defined_filters(self, sandbox_rows):
        _write_threads("agent_akane", sandbox_rows)
        scores = ts.score_tensions("agent_akane", FAKE_NOW, soul_dir=None)
        # 🔴 Owner 裁定 A（2026-10-22）：D3 已自評分目標移除 ⇒ 只剩 D1 一條。
        assert len(scores) == 1, "裁定 A 後僅 D1 一條（D3 已自評分目標移除）"
        by_code = {s.drive_code: s for s in scores}
        assert by_code["D1"].info_gap_count == 2, "缺口＝active＋無 fact＋≥24h"
        # 未決矛盾仍作為**觀測因子**誠實回報（此處合成資料有 1 筆），
        # 但**不驅動**任何張力 ⇒ 不會產生 D3 張力。
        assert by_code["D1"].unresolved_count == 1, "矛盾＝有 contradiction 欄位且為真值"
        assert "D3" not in by_code, "D3 不得再成為張力驅動目標（Owner 裁定 A）"

    def test_t4_source_rows_point_back_to_the_data_plane(self, sandbox_rows):
        _write_threads("agent_akane", sandbox_rows)
        scores = ts.score_tensions("agent_akane", FAKE_NOW, soul_dir=None)
        for score in scores:
            assert score.source_rows, "source_rows 不得為空"
            gap_rows = [r for r in score.source_rows if r["factor"] == "info_gap"]
            contradiction_rows = [
                r for r in score.source_rows if r["factor"] == "unresolved"
            ]
            assert len(gap_rows) == score.info_gap_count
            assert len(contradiction_rows) == score.unresolved_count
            assert {r["thread_id"] for r in gap_rows} == {"gap-1", "gap-2"}
            assert {r["thread_id"] for r in contradiction_rows} == {"gap-2"}

    def test_t4_no_tension_when_all_factors_zero(self):
        _write_threads("agent_akane", [_row("fresh", age_hours=1)])
        assert ts.score_tensions("agent_akane", FAKE_NOW, soul_dir=None) == []

    def test_t4_missing_data_plane_is_empty_not_error(self):
        """資料面不存在 ⇒ 0 筆（fail-closed），不是例外、也不是猜出來的數字。"""
        assert ts.score_tensions("agent_nobody", FAKE_NOW, soul_dir=None) == []


# ── T5：排序為字典序 ＋ 禁用加權總分 ────────────────────────────────────
def _score(state: str, unresolved: int, info_gap: int, drive_code: str = "D1") -> ts.TensionScore:
    return ts.TensionScore(
        agent_id="agent_akane",
        drive_code=drive_code,
        temporal_state=state,
        decay_bucket="24h-7d",
        info_gap_count=info_gap,
        unresolved_count=unresolved,
        source_rows=[],
    )


class TestT5LexicographicOrdering:
    def test_t5_temporal_state_dominates_the_order(self):
        scores = [
            _score(STATE_CALM, 9, 9, "D1"),
            _score(STATE_TENSION, 0, 0, "D1"),
            _score(STATE_RESOLVED, 0, 0, "D1"),
        ]
        assert [s.temporal_state for s in ts.sort_scores(scores)] == [
            STATE_TENSION,
            STATE_RESOLVED,
            STATE_CALM,
        ]

    def test_t5_unresolved_then_info_gap_then_drive_code(self):
        # 排序函式對 drive_code 字串是**泛用**的；此處用 D2 作為第二個 code 字串
        # （D3 已自驅動目標移除，用它會誤導讀者以為 D3 仍是目標）。
        scores = [
            _score(STATE_TENSION, 1, 1, "D2"),
            _score(STATE_TENSION, 2, 0, "D1"),
            _score(STATE_TENSION, 2, 5, "D1"),
            _score(STATE_TENSION, 2, 5, "D2"),
        ]
        ordered = ts.sort_scores(scores)
        assert [(s.unresolved_count, s.info_gap_count, s.drive_code) for s in ordered] == [
            (2, 5, "D1"),
            (2, 5, "D2"),
            (2, 0, "D1"),
            (1, 1, "D2"),
        ]

    def test_t5_score_tensions_returns_sorted_scores(self):
        rows = [
            _row("gap-1", age_hours=30),
            _row("gap-2", age_hours=48, contradiction="衝突"),
        ]
        _write_threads("agent_akane", rows)
        scores = ts.score_tensions("agent_akane", FAKE_NOW, soul_dir=None)
        assert [s.drive_code for s in scores] == ["D1"]


class TestT5NoWeightedTotal:
    """**顯式**斷言程式中不存在加權總分（工單 §4.1 T5）。

    三道機械化守門（皆可在 AST 上驗，不靠字串比對）：

    1. **無乘法節點**（``ast.Mult``）⇒ 不可能有「計數 × 權重」的乘積式總分；
    2. **無浮點常數** ⇒ 不可能有 ``0.3``／``0.7`` 這類權重係數；
    3. **無 weight 命名的識別字** ⇒ 沒有自造的權重常數／權重參數。
    """

    @staticmethod
    def _tree() -> ast.AST:
        return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))

    def test_t5_no_multiplication_operator(self):
        tree = self._tree()
        mults = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult)
        ]
        assert not mults, f"評分模組不得有乘法（加權總分的典型形狀）：{mults}"

    def test_t5_no_float_constants(self):
        tree = self._tree()
        floats = [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, float)
        ]
        assert not floats, f"評分模組不得有浮點常數（權重係數的典型形狀）：{floats}"

    def test_t5_no_weight_named_identifier(self):
        tree = self._tree()
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
            elif isinstance(node, ast.Attribute):
                names.add(node.attr)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, ast.keyword) and node.arg:
                names.add(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
        offenders = sorted(n for n in names if "weight" in n.lower())
        assert not offenders, f"不得有權重命名的識別字：{offenders}"


# ── T6：三態窗一致性 ─────────────────────────────────────────────────────
class TestT6ThreeStateWindowConsistency:
    """``decay_bucket`` 與 ``classify_temporal_state`` 的三態**一一對應**。

    邊界 24h／7d 各測一次，且**兩側都測**（``<`` 與 ``≥``）。
    兩道檢查同時成立：

    1. **相對**（對上游）：實作的 bucket 必須與上游 ``classify_temporal_state``
       的三態一致 ⇒ 上游窗變了會跟著變（不漂移）；
    2. **絕對**（釘值）：canonical 邊界仍是 24h／7d。
    """

    @pytest.mark.parametrize(
        "elapsed,expected_bucket,expected_state",
        [
            (CALM_BOUNDARY_SEC - 1, ts.DECAY_BUCKET_CALM, STATE_CALM),
            (CALM_BOUNDARY_SEC, ts.DECAY_BUCKET_TENSION, STATE_TENSION),
            (RESOLVED_BOUNDARY_SEC - 1, ts.DECAY_BUCKET_TENSION, STATE_TENSION),
            (RESOLVED_BOUNDARY_SEC, ts.DECAY_BUCKET_RESOLVED, STATE_RESOLVED),
        ],
    )
    def test_t6_bucket_and_state_agree_on_both_sides_of_each_boundary(
        self, elapsed, expected_bucket, expected_state
    ):
        upstream_state = classify_temporal_state(
            FAKE_NOW - elapsed, FAKE_NOW, BondEvidence(True)
        )
        assert upstream_state == expected_state, "上游三態邊界被移動"
        assert ts.decay_bucket(elapsed) == expected_bucket

        _write_threads("agent_akane", [_row("gap-1", age_hours=30)])
        scores = ts.score_tensions(
            "agent_akane",
            FAKE_NOW,
            last_interaction_ts=FAKE_NOW - elapsed,
            bond_evidence=BondEvidence(True),
        )
        assert scores, "沙盒有缺口 ⇒ 應至少產出一條張力"
        assert scores[0].decay_bucket == expected_bucket
        assert scores[0].temporal_state == expected_state

    def test_t6_bucket_to_state_mapping_is_one_to_one(self):
        samples = [
            0,
            3600,
            CALM_BOUNDARY_SEC - 1,
            CALM_BOUNDARY_SEC,
            RESOLVED_BOUNDARY_SEC - 1,
            RESOLVED_BOUNDARY_SEC,
        ]
        pairs = {
            (
                ts.decay_bucket(e),
                classify_temporal_state(FAKE_NOW - e, FAKE_NOW, BondEvidence(True)),
            )
            for e in samples
        }
        assert len(pairs) == 3, f"三個 bucket 應對應三個 state，實得 {pairs}"
        buckets = {b for b, _ in pairs}
        states = {s for _, s in pairs}
        assert len(buckets) == 3 and len(states) == 3, f"非一一對應：{pairs}"
        assert buckets == {
            ts.DECAY_BUCKET_CALM,
            ts.DECAY_BUCKET_TENSION,
            ts.DECAY_BUCKET_RESOLVED,
        }
        assert states == {STATE_CALM, STATE_TENSION, STATE_RESOLVED}


# ── T11：D3 已自評分目標移除（Owner 裁定 A，2026-10-22）────────────────────
class TestT11D3RemovedFromScoringTargets:
    """🔴 守衛 T11：**D3 不得再作為張力驅動目標**（Owner 裁定 A「誠實收斂」）。

    裁定理由（實測四條「什麼算衝突」判準路徑）：兩條恆 0；唯一非 0 的一條
    **48/48 來自 `support_propagation` 邊**，且其內容是**跨日生活切片而非邏輯衝突**
    ⇒ 放寬判準會用**假矛盾餵飽引擎 supersede 門檻** ⇒ **誤殺活著的命題**
    ⇒ **寧可誠實移除 D3，也不做假矛盾**。

    本守衛是該裁定的**可執行化身**：任何人想把 D3 塞回評分目標，這裡立刻轉紅。
    """

    def test_t11_active_drive_targets_is_d1_only(self):
        assert ts.ACTIVE_DRIVE_TARGETS == ("D1",), (
            "張力驅動目標集合必須只有 D1（裁定 A）：%s" % (ts.ACTIVE_DRIVE_TARGETS,)
        )
        assert "D3" not in ts.ACTIVE_DRIVE_TARGETS

    def test_t11_no_d3_score_even_when_contradiction_data_exists(self):
        """合成資料**有**未決矛盾（足以讓舊版產出 D3）⇒ 仍不得有 D3 張力。"""
        rows = [
            _row("gap-1", age_hours=30),
            _row("gap-2", age_hours=48, contradiction="與舊推論衝突"),
        ]
        _write_threads("agent_akane", rows)
        scores = ts.score_tensions("agent_akane", FAKE_NOW, soul_dir=None)
        codes = [s.drive_code for s in scores]
        assert codes == ["D1"], "裁定 A 後只允許 D1 一條張力：%s" % (codes,)
        assert ts.DRIVE_BOUNDARY_REPAIR not in codes

    def test_t11_score_tensions_source_never_references_d3(self):
        """AST 層：``score_tensions`` 內**不得**引用 ``DRIVE_BOUNDARY_REPAIR``。"""
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "score_tensions":
                fn = node
                break
        assert fn is not None, "找不到 score_tensions"
        hits = {
            n.id
            for n in ast.walk(fn)
            if isinstance(n, ast.Name) and n.id == "DRIVE_BOUNDARY_REPAIR"
        }
        assert not hits, (
            "score_tensions 不得引用 DRIVE_BOUNDARY_REPAIR（D3 已自評分目標移除）"
        )

    def test_t11_closure_loop_drives_touched_excludes_d3(self):
        """閉環的 ``drives_touched`` 也不得列入 D3（觀測因子很大也不行）。"""
        from src.inner_life import tension_closure_loop as tcl

        score = ts.TensionScore(
            agent_id="agent_akane",
            drive_code="D1",
            temporal_state=STATE_TENSION,
            decay_bucket=ts.DECAY_BUCKET_TENSION,
            info_gap_count=2,
            unresolved_count=5,  # 觀測因子很大，但 D3 已非驅動目標
            source_rows=[],
        )
        touched = tcl._drives_touched(score)
        assert touched == ["D1"], "drives_touched 只允許 D1：%s" % (touched,)
        assert "D3" not in touched
