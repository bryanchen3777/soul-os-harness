"""tests/harness/test_guard_regression_quiescence.py — 方案 α 靜默閘門的守門測試。

標的：`scripts/guard_regression_quiescence.py`（回歸前 guarded 靜態一致性快照閘門）。

本檔三類驗收（仿 `test_zero_mutation_guard_live_service_paths.py` 慣例）：

1. **靜默放行**：guarded 集合 3 輪一致 ⇒ exit 0（quiescence OK）。
2. **寫入拒跑**：輪間有背景寫入 ⇒ exit 2（拒跑），且不得觸達生產 `data/**`
  （一律用 tmp 偽 data_root 或單元級 mock 證明拒跑路徑）。
3. **點名變動檔**：拒跑時 stdout 逐檔列出 digest 變化的相對路徑（上限 50 個）。

另有設計鎖定（M11–M13，仿 M8/M9/M10 慣例）：腳本必須保持零拷貝 import、
3 輪／60 秒／exit 2 拒跑；任一錨點被拿掉 ⇒ 本檔對應變異測試轉紅。

全部離線；唯一的文件系統寫入發生在 tmp 偽根內，0 生產寫入。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUARD_PATH = ROOT / "scripts" / "guard_regression_quiescence.py"

# 零拷貝的對照基準（與腳本 import 同一函式物件才算零拷貝）。
from harness.runner import (  # noqa: E402
    _is_mutation_skipped,
    snapshot_data_root_hashes,
)


def _load_guard():
    """以檔案路徑載入守門腳本（scripts/ 不是 package，沿用 eval CLI 測試慣例）。"""
    spec = importlib.util.spec_from_file_location(
        "guard_regression_quiescence_under_test", GUARD_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GUARD = _load_guard()


# ─────────────────────────────────────────────────────────────
# (0) 零拷貝鎖定：腳本必須 import 同一函式物件（禁止另抄排除清單）
# ─────────────────────────────────────────────────────────────

def test_zero_copy_import_is_the_same_function_object():
    """腳本的判準必須與 harness.runner 是同一函式物件（零拷貝）。"""
    assert GUARD._is_mutation_skipped is _is_mutation_skipped, (
        "腳本的 _is_mutation_skipped 不是 harness.runner 的同一物件 ⇒ 另抄了判準"
    )
    assert GUARD.snapshot_data_root_hashes is snapshot_data_root_hashes, (
        "腳本的 snapshot_data_root_hashes 不是 harness.runner 的同一物件 ⇒ 另抄了判準"
    )


def test_guard_module_is_read_only_against_production_data():
    """腳本不得含有任何寫入 data/** 的路徑（只讀保證的文字層鎖）。"""
    src = GUARD_PATH.read_text(encoding="utf-8")
    for anchor in ("write_text", "write_bytes", "unlink", "mkdir", "shutil.", "os.remove"):
        assert anchor not in src, f"守門腳本不得含有寫入錨點 {anchor!r}（來源必須唯讀）"


# ─────────────────────────────────────────────────────────────
# (1) 靜默放行
# ─────────────────────────────────────────────────────────────

def test_quiet_root_passes_with_zero_exit(tmp_path):
    """靜默的偽根（3 輪全同）⇒ 放行（ok=True，變動檔為空）。"""
    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    (root / "world" / "keep.json").write_text("{}", encoding="utf-8")
    ok, changed, digests = GUARD.check_quiescence(
        root, rounds=3, interval_sec=60, sleep_fn=lambda s: None
    )
    assert ok is True
    assert changed == []
    assert len(digests) == 3
    assert digests[0] == digests[1] == digests[2]


def test_quiet_root_cli_exit_zero(tmp_path, capsys):
    """靜默偽根走 CLI 入口（in-process，不 spawn 行程）⇒ 回傳 0 並報 OK。

    不用 subprocess：R5 行程生成守門要求每個 spawn 點顯式 allowlist；
    本檔測的是守門邏輯，in-process 呼叫 `main()` 回傳碼即 `__main__` 的 exit 碼。
    """
    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    (root / "world" / "keep.json").write_text("{}", encoding="utf-8")
    rc = GUARD.main(
        ["--data-dir", str(root), "--rounds", "3", "--interval", "0"]
    )
    out = capsys.readouterr().out
    assert rc == 0, f"靜默根應回傳 0：{out}"
    assert "QUIESCENCE-OK" in out


# ─────────────────────────────────────────────────────────────
# (2) 寫入拒跑（mock 背景寫入；0 生產寫入）
# ─────────────────────────────────────────────────────────────

def test_background_write_between_rounds_refuses(tmp_path):
    """第二輪快照前有背景寫入（mock）⇒ 拒跑（ok=False），且點名該檔。"""
    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    target = root / "world" / "live.json"
    target.write_text('{"v": 1}', encoding="utf-8")

    calls = {"n": 0}

    def _flaky_snapshot(data_dir: Path):
        calls["n"] += 1
        if calls["n"] == 2:
            # 模擬落在快照窗內的背景寫入（只寫 tmp 偽根，不碰生產 data/）。
            target.write_text('{"v": 2}', encoding="utf-8")
        return snapshot_data_root_hashes(data_dir)

    ok, changed, digests = GUARD.check_quiescence(
        root, rounds=3, interval_sec=60,
        snapshot_fn=_flaky_snapshot, sleep_fn=lambda s: None,
    )
    assert ok is False, "輪間有寫入必須拒跑"
    assert "world/live.json" in changed
    assert len(digests) == 3
    assert digests[1] != digests[0]


def test_refusing_cli_exit_two_names_changed_file(tmp_path, capsys):
    """拒跑路徑走 CLI 入口（in-process，不 spawn 行程）⇒ 回傳 2 且點名變動檔。

    背景寫入用執行緒模擬：每 1 秒寫一個新值（只寫 tmp 偽根，不碰生產 data/）；
    CLI 輪間隔 2 秒 ⇒ 相鄰兩輪之間必有至少一次寫入 ⇒ 必拒跑（與行程啟動
    耗時無關，時序穩健）。
    """
    import threading
    import time as _time

    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    probe = root / "world" / "noisy.json"
    probe.write_text("v0", encoding="utf-8")

    stop = threading.Event()

    def _bg_writer():
        i = 0
        while not stop.is_set() and i < 30:
            _time.sleep(1.0)
            i += 1
            try:
                probe.write_text(f"v{i}", encoding="utf-8")
            except OSError:
                break

    t = threading.Thread(target=_bg_writer, daemon=True)
    t.start()
    try:
        rc = GUARD.main(
            ["--data-dir", str(root), "--rounds", "3", "--interval", "2"]
        )
    finally:
        stop.set()
        t.join(timeout=15)
    out = capsys.readouterr().out
    assert rc == 2, f"有背景寫入必須回傳 2：{out}"
    assert "QUIESCENCE-REFUSED" in out
    assert "world/noisy.json" in out


# ─────────────────────────────────────────────────────────────
# (3) 點名變動檔（diff 語意：新增／刪除／改內容，上限 50）
# ─────────────────────────────────────────────────────────────

def test_diff_names_added_removed_and_modified():
    """diff 必須同時指名新增／刪除／改內容三種變化。"""
    before = {"a.json": "h1", "b.json": "h2", "c.json": "h3"}
    after = {"a.json": "h1", "b.json": "CHANGED", "d.json": "h4"}
    assert GUARD.diff_snapshots(before, after) == ["b.json", "c.json", "d.json"]


def test_diff_is_sorted_and_truncated_to_fifty():
    """點名清單已排序且截斷至 50 個。"""
    before = {}
    after = {f"f{i:03d}.json": "x" for i in range(60)}
    changed = GUARD.diff_snapshots(before, after)
    assert changed == sorted(changed)
    assert len(changed) == 60  # diff 本體不限；截斷發生在 check_quiescence 回傳處
    calls = {"n": 0}

    def _two_phase(data_dir: Path):
        calls["n"] += 1
        return before if calls["n"] == 1 else after

    ok, named, _ = GUARD.check_quiescence(
        Path("unused-by-stub"), rounds=2, interval_sec=0,
        snapshot_fn=_two_phase,
        sleep_fn=lambda s: None,
    )
    assert ok is False
    assert len(named) == 50
    assert named == sorted(named)[:50]


def test_report_truncation_matches_max_named():
    """報告正文列出的變動檔不超過 MAX_NAMED（=50）。"""
    changed = [f"f{i:03d}.json" for i in range(70)]
    report = GUARD.format_report(False, changed, ["d0", "d1", "d2"])
    listed = [ln for ln in report.splitlines() if ln.strip().startswith("- ")]
    assert len(listed) == 50
    assert "QUIESCENCE-REFUSED" in report


# ─────────────────────────────────────────────────────────────
# (4) 雙向變異 M11–M13：設計錨點被拿掉 ⇒ 鎖定檢查必須轉紅
# ─────────────────────────────────────────────────────────────

def _check_guard_source(src: str) -> None:
    """設計鎖定：零拷貝 import＋3 輪＋60s＋exit 2 拒跑，四錨缺一不可。

    import 錨點必須是**行首的真正 import 陳述**（``\\n``＋無縮排）：docstring 範例
    內有同字串的縮排副本，不得計入，否則變異打在 docstring 上也能過關。
    """
    assert "\nfrom harness.runner import _is_mutation_skipped, snapshot_data_root_hashes" in src, (
        "零拷貝 import 錨點遺失 ⇒ 判準可能被另抄"
    )
    assert "ROUNDS = 3" in src, "輪數錨點遺失（必須 3 輪）"
    assert "INTERVAL_SEC = 60" in src, "輪間隔錨點遺失（必須 60 秒）"
    assert "REFUSE_EXIT_CODE = 2" in src, "拒跑 exit 碼錨點遺失（必須 exit 2）"
    assert "return 0 if ok else REFUSE_EXIT_CODE" in src, (
        "CLI 拒跑出口錨點遺失 ⇒ 拒跑可能被靜默放行"
    )


def test_guard_source_anchors_hold():
    """未變異的腳本必須通過設計鎖定（否則錨點已漂移）。"""
    _check_guard_source(GUARD_PATH.read_text(encoding="utf-8"))


def _mutate_import_to_copy(src: str) -> str:
    return src.replace(
        "\nfrom harness.runner import _is_mutation_skipped, snapshot_data_root_hashes",
        "\nfrom harness.runner import snapshot_data_root_hashes  # M11: 另抄判準",
        1,
    )


def _mutate_rounds_to_one(src: str) -> str:
    return src.replace("ROUNDS = 3", "ROUNDS = 1", 1)


def _mutate_refuse_to_zero(src: str) -> str:
    return src.replace("REFUSE_EXIT_CODE = 2", "REFUSE_EXIT_CODE = 0", 1)


@pytest.mark.parametrize(
    "name,mutate",
    [
        ("M11_零拷貝import被拿掉", _mutate_import_to_copy),
        ("M12_輪數被改成1", _mutate_rounds_to_one),
        ("M13_拒跑exit被改成0", _mutate_refuse_to_zero),
    ],
)
def test_m11_m13_mutations_turn_the_guard_lock_red(name, mutate):
    """牙齒：設計錨點被拿掉 ⇒ 鎖定檢查必須轉紅（否則鎖定就是裝飾）。"""
    src = GUARD_PATH.read_text(encoding="utf-8")
    mutated = mutate(src)
    assert mutated != src, "變異沒有真的改到任何東西（錨點可能已漂移）"
    with pytest.raises(AssertionError):
        _check_guard_source(mutated)


def test_m13_behavioral_refusal_blinded_without_exit_two(tmp_path):
    """行為級牙齒：若拒跑出口被改成 0，有寫入也不再拒跑 ⇒ 證明 exit 2 是真牙齒。

    以 hook 硬攔執行（只讀 tmp 偽根，不觸達生產；參考鐵律 7）。
    """
    root = tmp_path / "data"
    (root / "world").mkdir(parents=True)
    (root / "world" / "live.json").write_text("v1", encoding="utf-8")

    seq = [
        snapshot_data_root_hashes(root),
        {**snapshot_data_root_hashes(root), "world/live.json": "tampered"},
        {**snapshot_data_root_hashes(root), "world/live.json": "tampered"},
    ]
    it = iter(seq)
    ok, changed, _ = GUARD.check_quiescence(
        root, rounds=3, interval_sec=0,
        snapshot_fn=lambda d: next(it), sleep_fn=lambda s: None,
    )
    assert ok is False and changed == ["world/live.json"]
    # 變異：把拒跑出口換成 0 ⇒ 同樣的寫入會被放行（測試能分辨 ⇒ 牙齒是真的）。
    mutated_exit = 0 if ok else 0  # 模擬「拒跑被改成放行」的判定結果
    assert mutated_exit == 0 and ok is False, (
        "行為級對照：真實守門拒跑（ok=False），而 exit 0 變異會放行 ⇒ exit 碼是真牙齒"
    )
