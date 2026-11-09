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

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


# ═════════════════════════════════════════════════════════════
# 真 hook 變異證明（SKIP-BASELINE-DRIFT-FIX-1 §5）
# ═════════════════════════════════════════════════════════════
#
# 同一 oracle（`tests/conftest.py::pytest_terminal_summary` 真 hook）
# 正常綠／變異紅：
#   · 基準集合 ⇒ 綠（STABLE，exitstatus 不動）；
#   · 資料變異（＋1／−1 筆 skip）⇒ 紅（DRIFT，`session.exitstatus = 1`）；
#   · 程式碼變異（差集清空／停用失敗設定）⇒ 被本檔 oracle 殺死。
# 全部只在合成物件／沙盒操作；接觸入口前先硬攔網路與持久化。

_HOOK_MOD = None


def _load_conftest_hook():
    """載入執行期真正在用的 hook（`tests/conftest.py`，非複製品）。"""
    global _HOOK_MOD
    if _HOOK_MOD is not None:
        return _HOOK_MOD
    path = Path(__file__).resolve().parent.parent / "conftest.py"
    spec = importlib.util.spec_from_file_location(
        "tests_conftest_hook_under_test", path
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _HOOK_MOD = mod
    return mod


class _FakeSession:
    """`terminalreporter._session` 的最小替身（只需 exitstatus）。"""

    def __init__(self) -> None:
        self.exitstatus = 0


class _FakeTerminalReporter:
    """真 hook 的合成 reporter（合成 skip 報告＋行收集器＋假 session）."""

    def __init__(self, skipped_ids) -> None:
        self.stats = {"skipped": [_FakeReport(n) for n in skipped_ids]}
        self.lines: list = []
        self._session = _FakeSession()

    def write_line(self, msg: str) -> None:
        self.lines.append(msg)


def _hard_block_io_before_entry(monkeypatch) -> None:
    """接觸入口前硬攔：比對路徑不得碰網路與持久化入口."""
    import socket
    import subprocess

    def _blocked(*args, **kwargs):
        raise AssertionError(
            "blocked: skip-baseline compare path must not touch network/persistence"
        )

    monkeypatch.setattr(socket, "socket", _blocked)
    monkeypatch.setattr(socket, "create_connection", _blocked)
    monkeypatch.setattr(subprocess, "Popen", _blocked)
    monkeypatch.setattr(subprocess, "run", _blocked)
    # 比對路徑不得寫基準檔（寫模式另有專用流程，本票不走）。
    monkeypatch.setattr(skip_baseline_lib, "save_baseline", _blocked)


def _drive_real_hook(monkeypatch, skipped_ids) -> _FakeTerminalReporter:
    """用合成 skip 集合驅動真 hook（全庫模式，唯讀基準）。"""
    hook = _load_conftest_hook()
    _hard_block_io_before_entry(monkeypatch)
    # 全庫模式判定走 env 強制（不依賴 config 形狀）。
    monkeypatch.setenv("SOUL_OS_ENFORCE_SKIP_BASELINE", "1")
    monkeypatch.delenv("SOUL_OS_WRITE_SKIP_BASELINE", raising=False)
    reporter = _FakeTerminalReporter(skipped_ids)
    hook.pytest_terminal_summary(reporter, 0, object())
    return reporter


def test_hook_reports_stable_for_baseline_set(monkeypatch):
    """正常綠：觀察集合逐筆等於基準 ⇒ STABLE，exitstatus 不動。"""
    baseline = skip_baseline_lib.load_baseline()
    reporter = _drive_real_hook(monkeypatch, list(baseline))
    assert reporter._session.exitstatus == 0
    assert any("STABLE" in line for line in reporter.lines), reporter.lines
    assert any("added=0 removed=0" in line for line in reporter.lines)


def test_hook_turns_red_on_added_skip(monkeypatch):
    """資料變異＋1：多一筆 skip ⇒ 真 hook 轉紅並逐筆指名。"""
    baseline = skip_baseline_lib.load_baseline()
    probe = "tests/synthetic/mutant_probe.py::test_added_skip_probe"
    assert probe not in baseline
    reporter = _drive_real_hook(monkeypatch, list(baseline) + [probe])
    assert reporter._session.exitstatus == 1, reporter.lines
    assert any("DRIFT" in line for line in reporter.lines)
    assert any(probe in line for line in reporter.lines)


def test_hook_turns_red_on_removed_skip(monkeypatch):
    """資料變異−1：少一筆 skip ⇒ 真 hook 轉紅並逐筆指名。"""
    baseline = skip_baseline_lib.load_baseline()
    assert baseline, "基準檔不得為空"
    dropped = baseline[0]
    observed = [n for n in baseline if n != dropped]
    reporter = _drive_real_hook(monkeypatch, observed)
    assert reporter._session.exitstatus == 1, reporter.lines
    assert any("DRIFT" in line for line in reporter.lines)
    assert any(dropped in line for line in reporter.lines)


def test_mutant_cleared_diff_is_killed(monkeypatch):
    """程式碼變異（差集清空）被 oracle 殺死：同一斷言正常綠、變異紅。"""
    oracle = (
        skip_baseline_lib.diff_skip_ids(["a"], ["a", "b"])["added"] == ["b"]
    )
    assert oracle  # 正常綠
    monkeypatch.setattr(
        skip_baseline_lib,
        "diff_skip_ids",
        lambda base, obs: {"added": [], "removed": []},
    )
    with pytest.raises(AssertionError):  # 變異紅（被殺見證）
        assert (
            skip_baseline_lib.diff_skip_ids(["a"], ["a", "b"])["added"]
            == ["b"]
        )


def test_mutant_disabled_fail_setting_is_killed(monkeypatch):
    """程式碼變異（停用失敗設定）被 oracle 殺死：同一斷言正常綠、變異紅。"""
    hook = _load_conftest_hook()
    baseline = skip_baseline_lib.load_baseline()
    probe = "tests/synthetic/mutant_probe.py::test_added_skip_probe"
    # 正常綠：真 hook 遇漂移必 exitstatus=1（有牙）。
    reporter = _drive_real_hook(monkeypatch, list(baseline) + [probe])
    assert reporter._session.exitstatus == 1
    # 變異：把失敗設定停用 ⇒ 同一 oracle 斷言失敗（被殺見證）。
    monkeypatch.setattr(hook, "_fail_session", lambda tr: None)
    reporter2 = _drive_real_hook(monkeypatch, list(baseline) + [probe])
    with pytest.raises(AssertionError):
        assert reporter2._session.exitstatus == 1
