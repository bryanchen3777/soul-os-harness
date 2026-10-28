"""tests/infra/test_skip_baseline_guard.py — skip 基準機制的**機制守衛**。

守的是「比對機制本身會不會壞掉」：四個純函式
（`load_baseline`／`save_baseline`／`diff_skip_ids`／`collect_skip_ids`）
與基準檔的格式契約。

🔴 **M3 變異實驗**會破壞 `skip_baseline_lib.diff_skip_ids`（或改壞基準檔格式）
   ⇒ 本檔必須**轉紅**。所以這裡的斷言必須真的驗到行為，不能只是「函式存在」。

🔴 匯入方式：工單原文寫「`tests/conftest.py` 與 `tests/infra/test_skip_baseline_guard.py`
   各自以 `sys.path.append(str(Path(__file__).parent / "infra"))` 後 import」
   ⇒ **前提差一拍**：本檔本身就在 `tests/infra/` 底下，
   `Path(__file__).parent / "infra"` 會指向**不存在**的 `tests/infra/infra`。
   ⇒ 實際採用 `Path(__file__).parent`（＝ `tests/infra`，純函式庫真正所在處）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

#: 共用純函式庫（skip 基準機制）。**不依賴套件化**。
_INFRA_DIR = Path(__file__).resolve().parent
if str(_INFRA_DIR) not in sys.path:
    sys.path.append(str(_INFRA_DIR))

import skip_baseline_lib  # noqa: E402


class _FakeReport:
    """terminal reporter 報告物件的最小替身（只需 outcome + nodeid）。"""

    def __init__(self, nodeid: str, outcome: str = "skipped") -> None:
        self.nodeid = nodeid
        self.outcome = outcome


# ═════════════════════════════════════════════════════════════
# diff_skip_ids：比對行為（M3 的靶心）
# ═════════════════════════════════════════════════════════════


def test_diff_skip_ids_reports_added_and_removed():
    """新增／消失都要**逐筆**列出（不是只給數量）。"""
    diff = skip_baseline_lib.diff_skip_ids(
        ["a", "b", "c"], ["b", "c", "d"]
    )
    assert diff["added"] == ["d"], diff
    assert diff["removed"] == ["a"], diff


def test_diff_skip_ids_is_order_insensitive():
    """比的是**集合**，不是順序 ⇒ 收集順序改變不得算漂移。"""
    diff = skip_baseline_lib.diff_skip_ids(
        ["a", "b"], ["b", "a"]
    )
    assert diff["added"] == [], diff
    assert diff["removed"] == [], diff


def test_diff_skip_ids_on_identical_sets_is_empty():
    """完全相同 ⇒ 兩個清單都必須是空（守衛機制的「綠」路徑）。"""
    diff = skip_baseline_lib.diff_skip_ids(
        ["a", "b"], ["a", "b"]
    )
    assert diff == {"added": [], "removed": []}, diff


def test_diff_skip_ids_detects_a_single_added_id():
    """**只多一筆**也必須被抓到（這是 skip 集合漂移的最小單位）。"""
    diff = skip_baseline_lib.diff_skip_ids(["a"], ["a", "b"])
    assert diff["added"] == ["b"], diff
    assert diff["removed"] == [], diff


def test_diff_skip_ids_detects_a_single_removed_id():
    """**只少一筆**也必須被抓到（少 skip 也是漂移：覆蓋率被悄悄砍掉）。"""
    diff = skip_baseline_lib.diff_skip_ids(["a", "b"], ["a"])
    assert diff["removed"] == ["b"], diff
    assert diff["added"] == [], diff


# ═════════════════════════════════════════════════════════════
# collect_skip_ids：從報告取出被 skip 的 ID
# ═════════════════════════════════════════════════════════════


def test_collect_skip_ids_extracts_and_sorts():
    reports = [
        _FakeReport("tests/b.py::t2"),
        _FakeReport("tests/a.py::t1"),
    ]
    assert skip_baseline_lib.collect_skip_ids(reports) == [
        "tests/a.py::t1",
        "tests/b.py::t2",
    ]


def test_collect_skip_ids_deduplicates():
    reports = [_FakeReport("tests/a.py::t1"), _FakeReport("tests/a.py::t1")]
    assert skip_baseline_lib.collect_skip_ids(reports) == ["tests/a.py::t1"]


def test_collect_skip_ids_ignores_non_skipped_reports():
    reports = [
        _FakeReport("tests/a.py::t1", outcome="passed"),
        _FakeReport("tests/b.py::t2", outcome="failed"),
        _FakeReport("tests/c.py::t3"),
    ]
    assert skip_baseline_lib.collect_skip_ids(reports) == ["tests/c.py::t3"]


def test_collect_skip_ids_of_empty_reports_is_empty():
    assert skip_baseline_lib.collect_skip_ids([]) == []


# ═════════════════════════════════════════════════════════════
# load_baseline / save_baseline：格式契約
# ═════════════════════════════════════════════════════════════


def test_save_then_load_round_trip(tmp_path):
    target = tmp_path / "skip_baseline.json"
    written = skip_baseline_lib.save_baseline(["b", "a", "a"], target)
    assert written == target
    assert skip_baseline_lib.load_baseline(target) == ["a", "b"]


def test_saved_baseline_has_no_timestamp_field(tmp_path):
    """基準檔**只允許** count / node_ids ⇒ 時間戳欄位會造成每日 churn。"""
    target = tmp_path / "skip_baseline.json"
    skip_baseline_lib.save_baseline(["a"], target)
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert set(raw) == {"count", "node_ids"}, raw
    assert raw["count"] == len(raw["node_ids"])


def test_load_baseline_rejects_count_mismatch(tmp_path):
    target = tmp_path / "skip_baseline.json"
    target.write_text(
        json.dumps({"count": 5, "node_ids": ["a"]}), encoding="utf-8"
    )
    try:
        skip_baseline_lib.load_baseline(target)
    except skip_baseline_lib.BaselineError as exc:
        assert "count" in str(exc)
    else:  # pragma: no cover — 守衛失敗路徑
        raise AssertionError("count 與清單長度不一致時必須 raise BaselineError")


def test_load_baseline_rejects_unsorted_node_ids(tmp_path):
    target = tmp_path / "skip_baseline.json"
    target.write_text(
        json.dumps({"count": 2, "node_ids": ["b", "a"]}), encoding="utf-8"
    )
    try:
        skip_baseline_lib.load_baseline(target)
    except skip_baseline_lib.BaselineError as exc:
        assert "排序" in str(exc)
    else:  # pragma: no cover — 守衛失敗路徑
        raise AssertionError("node_ids 未排序時必須 raise BaselineError")


def test_load_baseline_rejects_timestamp_field(tmp_path):
    target = tmp_path / "skip_baseline.json"
    target.write_text(
        json.dumps({"count": 1, "node_ids": ["a"], "generated_at": "2026-10-27"}),
        encoding="utf-8",
    )
    try:
        skip_baseline_lib.load_baseline(target)
    except skip_baseline_lib.BaselineError as exc:
        assert "generated_at" in str(exc)
    else:  # pragma: no cover — 守衛失敗路徑
        raise AssertionError("基準檔含時間戳欄位時必須 raise BaselineError")


def test_load_baseline_rejects_missing_file(tmp_path):
    try:
        skip_baseline_lib.load_baseline(tmp_path / "nope.json")
    except skip_baseline_lib.BaselineError as exc:
        assert "不存在" in str(exc)
    else:  # pragma: no cover — 守衛失敗路徑
        raise AssertionError("基準檔不存在時必須 raise BaselineError")


# ═════════════════════════════════════════════════════════════
# 真正的基準檔（本 repo 的那一份）
# ═════════════════════════════════════════════════════════════


def test_repo_skip_baseline_is_loadable_and_well_formed():
    """repo 的基準檔必須能被 `load_baseline` 讀回（格式契約的唯一實例）。"""
    node_ids = skip_baseline_lib.load_baseline()
    assert node_ids, "基準檔不得為空"
    assert node_ids == sorted(node_ids)
    assert len(node_ids) == len(set(node_ids))


def test_repo_skip_baseline_has_no_timestamp_field():
    """repo 的基準檔**不得**含時間戳欄位（每日 churn = 0）。"""
    raw = json.loads(
        (_INFRA_DIR / "skip_baseline.json").read_text(encoding="utf-8")
    )
    assert set(raw) == {"count", "node_ids"}, raw
