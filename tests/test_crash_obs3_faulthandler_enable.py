"""
test_crash_obs3_faulthandler_enable.py
CRASH-OBS-3 驗收: 啟動時必須**無條件**安裝 faulthandler 致命傾印處理器。

缺陷 (CRASH-OBS-2 執行者實測, 直接採信):
  scripts/run_server.py 的 `faulthandler.enable(file=_FAULTHANDLER_FILE)` 只出現在
  `_faulthandler_open()` 內, 而 `_faulthandler_open()` 只被輪替路徑呼叫
  (`_faulthandler_rotate_if_needed` → `_faulthandler_rotate_one(..., reopen=...)`),
  且輪替只在檔案 ≥ `_FAULTHANDLER_MAX_BYTES` (32MB) 才發生。
  實測 data/faulthandler.log = 6.0MB < 32MB → 執行中進程從未 enable()
  → C 層崩潰永遠不留現場 (CRASH-F1: 45 天 245 次 python311.dll c0000005)。
  這是回歸: d49c75d (原始版) 在模組層級直接 enable(); d20d9f2 (CRASH-OBS-1) 弄丟。

驗收對應 (工單「測試（必寫）」1-8):
  1. 啟動路徑即安裝 (乾淨小檔情境) + 目標檔正確 + 安裝 log 落盤
  1b. 真實子行程: 啟動後 is_enabled()==True 且「已安裝」log 真的寫出 (唯一憑據)
  2. 不依賴輪替 (輪替判定 False / _faulthandler_open 不可用時, 仍已安裝)
  3. 冪等 (連續呼叫不拋例外、語意不變、不關正在使用的 handle)
  4. fail-safe (enable 拋例外 → 啟動流程不受影響, 只記 warning)
  5. 輪替後 enable() 目標是新 handle (非已改名的舊 handle)
  6. 週期全執行緒 dump 已移除 (CRASH-F1-FIX, 2026-09-12): 0 處
     `dump_traceback_later` / 週期檔常量與函式 / marker 機制。
     原斷言「週期行為與 CRASH-OBS-2 版逐字相同」已隨根因修復失效 → 改寫為
     「週期機制已不存在」的正向守門 (見 docs/CRASH-F1-SYMBOLS-1.md)。
  7. heartbeat dumper 仍在 / 仍 60s / 已改 dump_traceback(all_threads=False)
     (all_threads=True 是本票移除的崩潰路徑, 見 CRASH-F1-FIX)
  8. 0 生產資料變更 (data/faulthandler* + data/heartbeat_trace* 檔名快照一致)

隔離手法 (沿用 tests/test_crash_obs2_faulthandler_split.py):
  exec scripts/run_server.py 原始碼到獨立 namespace, 期間把 src.paths.data_root
  換成 tmp → 模組層級副作用 (開檔 / enable) 全落在 tmp, 絕不碰生產 data/。
  本檔不啟動 uvicorn / 不 bind port / 不重啟任何服務 / 不改 data/ 任何檔案。
"""
import ast
import faulthandler
import logging
import os
import subprocess
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER_PY = ROOT / "scripts" / "run_server.py"
PROD_DATA = (ROOT / "data").resolve()
# CRASH-OBS-2 (致命/週期分檔) = 本票的凍結比較基準
CRASH_OBS2_REV = "a20b4e8"
INSTALL_LOG_MARK = "faulthandler 致命傾印已安裝"


# ── helpers ───────────────────────────────────────────────

def _read_source() -> str:
    return RUN_SERVER_PY.read_text(encoding="utf-8")


def _exec_run_server_isolated(data_root_dir: Path, mod_name: str):
    """exec run_server.py 到獨立 namespace; exec 期間 data_root() → data_root_dir。

    模組層級副作用 (開 faulthandler.log / 新增的啟動即 enable) 全落在 tmp。
    `__name__` 不是 "__main__" → 絕不執行 uvicorn.run。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import src.paths as paths

    source = _read_source()
    code = compile(source, str(RUN_SERVER_PY), "exec")
    mod = types.ModuleType(mod_name)
    mod.__file__ = str(RUN_SERVER_PY)

    real_data_root = paths.data_root
    paths.data_root = lambda: data_root_dir
    try:
        exec(code, mod.__dict__)
    finally:
        paths.data_root = real_data_root
    return mod


def _exec_with_enable_recorder(data_root_dir: Path, mod_name: str):
    """exec run_server.py, 期間攔截 faulthandler.enable → 回傳 (mod, calls)。

    攔截只是為了斷言「啟動時的安裝時機與目標檔」而不動生產狀態;
    真實安裝 (is_enabled + 真 log) 由 test_1b 的子行程實證。
    """
    calls = []
    real_enable = faulthandler.enable

    def recorder(file=None, all_threads=True):  # noqa: A002
        calls.append({"file": file, "all_threads": all_threads})

    faulthandler.enable = recorder
    try:
        mod = _exec_run_server_isolated(data_root_dir, mod_name)
    finally:
        faulthandler.enable = real_enable
    return mod, calls


def _close_handles(mod) -> None:
    for attr in ("_FAULTHANDLER_FILE", "_FAULTHANDLER_PERIODIC_FILE"):
        handle = getattr(mod, attr, None)
        try:
            if handle is not None and not handle.closed:
                handle.close()
        except Exception:
            pass


def _func_sources(source: str) -> dict:
    """函式名 → 原始碼片段 (含 nested; newline 正規化後比對)。"""
    norm = source.replace("\r\n", "\n")
    tree = ast.parse(norm)
    return {
        node.name: ast.get_source_segment(norm, node)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _git_show(rev: str, path_in_repo: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"{rev}:{path_in_repo}"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8",
    )
    if proc.returncode != 0:
        pytest.skip(f"git show {rev}:{path_in_repo} 不可用: {proc.stderr.strip()[:200]}")
    return proc.stdout


def _prod_data_names():
    """data/ 下 faulthandler* + heartbeat_trace* 檔名快照 (只比名字)。

    不比大小/mtime: 生產服務每 60s append 這些檔, 大小本來就會動。
    """
    if not PROD_DATA.exists():
        return None
    return sorted(
        p.name
        for p in PROD_DATA.iterdir()
        if p.is_file() and (p.name.startswith("faulthandler") or p.name.startswith("heartbeat_trace"))
    )


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _restore_real_faulthandler():
    """測後把本行程的 faulthandler 目標還原 (CRASH-OBS-3 起 exec 就會 enable)。

    tmp handle 測後失效 → 必須還原, 免得本行程之後崩潰時 dump 到已關的 handle。
    """
    yield
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.enable(file=sys.stderr)
    except Exception:
        pass


# ── 1. 啟動路徑即安裝 ─────────────────────────────────────

def test_1_startup_installs_fatal_handler_on_small_file(tmp_path, monkeypatch, caplog):
    """乾淨小檔 (<32MB, 不存在) 情境: 啟動路徑就必須 enable(), 目標 = faulthandler.log。

    這是本回歸的核心斷言: 安裝**不可**依賴輪替。
    """
    replace_calls = []
    real_replace = Path.replace

    def spy_replace(self, target):
        replace_calls.append((str(self), str(target)))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", spy_replace)

    with caplog.at_level(logging.INFO, logger="soul_os.server"):
        mod, calls = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t1")
    try:
        # 情境確認: 啟動前沒有這個檔, 啟動後 0 byte « 32MB → 輪替條件不成立
        assert mod._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024
        assert mod._FAULTHANDLER_PATH.stat().st_size < mod._FAULTHANDLER_MAX_BYTES

        # (1) 啟動路徑確實呼叫了 enable
        assert calls, "啟動路徑必須安裝致命傾印處理器 (faulthandler.enable)"
        assert len(calls) == 1, calls

        # (2) 目標檔正確
        target = calls[0]["file"]
        assert target is mod._FAULTHANDLER_FILE
        assert Path(target.name) == mod._FAULTHANDLER_PATH
        assert Path(target.name).name == "faulthandler.log"
        assert Path(target.name).parent == tmp_path, "目標必須在隔離根, 絕非生產 data/"
        assert target.closed is False
        assert "a" in target.mode, "append 模式 (不截斷既有崩潰現場)"

        # (3) 這次啟動完全沒有輪替 → 安裝與輪替無關 (Path.replace / 輪替檔 0 筆)
        assert replace_calls == [], f"不該發生輪替: {replace_calls}"
        assert not list(tmp_path.glob("faulthandler.*.log"))
        assert mod._faulthandler_rotate_if_needed() is False
        assert replace_calls == []

        # (4) 安裝成功的 log 是「重啟後真的有裝」的唯一憑據 → 必須發出且帶路徑
        assert INSTALL_LOG_MARK in caplog.text, caplog.text
        assert str(mod._FAULTHANDLER_PATH) in caplog.text, caplog.text
    finally:
        _close_handles(mod)


# ── 1b. 真實子行程: 啟動後真的有裝 ────────────────────────

_PROBE = r'''
import faulthandler, sys, types
from pathlib import Path

ROOT = Path(sys.argv[1])
DATA = Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
import src.paths as paths
paths.data_root = lambda: DATA

src = (ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
mod = types.ModuleType("_run_server_obs3_probe")
mod.__file__ = str(ROOT / "scripts" / "run_server.py")
exec(compile(src, mod.__file__, "exec"), mod.__dict__)
print("OBS3_PROBE is_enabled=%s path=%s" % (faulthandler.is_enabled(), mod._FAULTHANDLER_PATH), flush=True)
'''


def test_1b_real_process_reports_handler_installed(tmp_path):
    """真實子行程 (真 enable + 真 logging): is_enabled()==True 且安裝 log 落盤。

    這是不靠 mock 的證據: 重啟後唯一的憑據就是這行 log。
    """
    probe = tmp_path / "_obs3_probe.py"
    probe.write_text(_PROBE, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()

    # 子行程的 stdout/stderr 是 pipe → Python 會用 locale 編碼 (本機 cp950);
    # 強制 UTF-8, 否則中文 log 行解碼成亂碼, 斷言會假紅。
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    proc = subprocess.run(
        [sys.executable, str(probe), str(ROOT), str(data)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, env=env,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, combined[-3000:]
    assert "OBS3_PROBE is_enabled=True" in combined, combined[-3000:]
    assert INSTALL_LOG_MARK in combined, combined[-3000:]
    assert str(data / "faulthandler.log") in combined, combined[-3000:]
    # 子行程的致命檔落在隔離根, 生產 data/ 不被碰
    assert (data / "faulthandler.log").exists()


# ── 2. 不依賴輪替 ─────────────────────────────────────────

def test_2_install_not_dependent_on_rotation(tmp_path, monkeypatch):
    """輪替判定「不需輪替」時 enable 仍已被呼叫; 安裝路徑不經過 _faulthandler_open。"""
    mod, calls = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t2")
    try:
        assert calls, "啟動即安裝, 與輪替無關"
        assert mod._faulthandler_rotate_if_needed() is False, "乾淨小檔不該輪替"

        # 反向控制: 把整條輪替路徑弄成不可能發生, 安裝仍必須成功
        recorded = []
        monkeypatch.setattr(
            faulthandler, "enable",
            lambda file=None, all_threads=True: recorded.append(file),  # noqa: A002
        )
        monkeypatch.setattr(mod, "_faulthandler_rotate_one", lambda *a, **k: False)
        monkeypatch.setattr(mod, "_faulthandler_rotate_if_needed", lambda: False)

        def boom() -> None:
            raise AssertionError("啟動安裝路徑不得呼叫 _faulthandler_open (那是輪替專用)")

        monkeypatch.setattr(mod, "_faulthandler_open", boom)

        handle_before = mod._FAULTHANDLER_FILE
        assert mod._faulthandler_install_fatal_handler() is True
        assert recorded == [handle_before], recorded
        assert recorded[0] is mod._FAULTHANDLER_FILE
        assert mod._faulthandler_rotate_if_needed() is False
    finally:
        _close_handles(mod)


# ── 3. 冪等 ───────────────────────────────────────────────

def test_3_install_is_idempotent(tmp_path, monkeypatch):
    """連續呼叫啟動路徑: 不拋例外、handle 語意不變、不關正在使用的 handle。"""
    mod, startup_calls = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t3")
    try:
        assert len(startup_calls) == 1
        handle = mod._FAULTHANDLER_FILE
        handle.write("existing-crash-scene\n")
        handle.flush()

        # (a) 真實 enable 冪等 (recorder 已還原): 連續 3 次
        for _ in range(3):
            assert mod._faulthandler_install_fatal_handler() is True
        assert mod._FAULTHANDLER_FILE is handle, "有效 handle 不得被重開"
        assert handle.closed is False, "不得關閉正在使用的 handle"
        assert faulthandler.is_enabled() is True
        # append 語意: 既有內容不被截斷
        assert "existing-crash-scene" in mod._FAULTHANDLER_PATH.read_text(encoding="utf-8")

        # (b) 每次都重新指向同一 handle (語意不變)
        seen = []
        monkeypatch.setattr(
            faulthandler, "enable",
            lambda file=None, all_threads=True: seen.append(file),  # noqa: A002
        )
        assert mod._faulthandler_install_fatal_handler() is True
        assert mod._faulthandler_install_fatal_handler() is True
        assert seen == [handle, handle]
        assert mod._FAULTHANDLER_FILE is handle
    finally:
        _close_handles(mod)


# ── 4. fail-safe ──────────────────────────────────────────

def test_4_enable_failure_never_breaks_startup(tmp_path, monkeypatch, caplog):
    """enable() 拋例外 → 吞掉 + warning; 模組仍完整載入 (觀測層不影響主服務)。"""
    def boom(file=None, all_threads=True):  # noqa: A002
        raise OSError("simulated enable failure")

    monkeypatch.setattr(faulthandler, "enable", boom)

    with caplog.at_level(logging.WARNING, logger="soul_os.server"):
        mod = _exec_run_server_isolated(tmp_path, "_rs_obs3_t4")  # 不得外洩例外
        assert mod._faulthandler_install_fatal_handler() is False

    assert "致命傾印安裝失敗" in caplog.text, caplog.text
    # 模組其餘啟動常數完好 → 例外沒有打斷啟動
    assert mod._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024
    assert mod._FAULTHANDLER_KEEP == 3
    assert mod._FAULTHANDLER_PATH.name == "faulthandler.log"
    # CRASH-F1-FIX: 週期機制常量已移除 (原斷言 _FAULTHANDLER_MARKER_INTERVAL_SECS
    # == 60 / _FAULTHANDLER_PERIODIC_PATH.name == "faulthandler_periodic.log"
    # 守的是已廢除的週期分檔狀態 → 改寫為「已不存在」的正向守門)
    assert not hasattr(mod, "_FAULTHANDLER_MARKER_INTERVAL_SECS")
    assert not hasattr(mod, "_FAULTHANDLER_PERIODIC_PATH")
    _close_handles(mod)


# ── 5. 輪替後重指新 handle ────────────────────────────────

def test_5_after_rotation_enable_targets_new_handle(tmp_path, monkeypatch):
    """輪替 → 關舊檔 → rename → 重新 enable 到**新** handle (絕不留在已改名的舊檔)。"""
    mod, startup_calls = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t5")
    try:
        assert len(startup_calls) == 1
        seen = []
        monkeypatch.setattr(
            faulthandler, "enable",
            lambda file=None, all_threads=True: seen.append(file),  # noqa: A002
        )
        monkeypatch.setattr(mod, "_FAULTHANDLER_MAX_BYTES", 1)  # 1 byte → 必定超標

        old = mod._FAULTHANDLER_FILE
        old.write("x")
        old.flush()

        assert mod._faulthandler_rotate_if_needed() is True
        new = mod._FAULTHANDLER_FILE
        assert new is not old
        assert old.closed is True
        assert new.closed is False
        assert Path(new.name) == mod._FAULTHANDLER_PATH

        assert seen == [new], f"輪替後必須且只能 enable 到新 handle: {seen}"
        assert old not in seen, "絕不可 enable 到已關閉/已改名的舊 handle"
    finally:
        _close_handles(mod)


# ── 6. 週期全執行緒 dump 已移除 (CRASH-F1-FIX 守門) ────────

def test_6_periodic_dump_mechanism_absent(tmp_path, monkeypatch):
    """CRASH-F1-FIX: 週期全執行緒 dump 與其分檔已不存在 (本檔原斷言的「凍結」狀態
    正是已證實的崩潰根因, 因此改寫為「機制已不存在」的正向守門)。

    根因: `dump_traceback_later` **沒有 all_threads 參數, 永遠走訪全部執行緒**,
    faulthandler 不停世界 → 與其他執行緒釋放 frame 競態 → c0000005 @ 0xA0。
    """
    mod, _ = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t6")
    try:
        # (a) 週期檔常量 / handle 0 殘留
        for name in (
            "_FAULTHANDLER_PERIODIC_PATH",
            "_FAULTHANDLER_PERIODIC_FILE",
            "_FAULTHANDLER_MARKER_INTERVAL_SECS",
            "_faulthandler_periodic_open",
            "_faulthandler_periodic_handle",
            "_faulthandler_marker_tick",
            "_faulthandler_marker_loop",
        ):
            assert not hasattr(mod, name), f"{name} 必須已移除"
        assert mod._FAULTHANDLER_KEEP == 3
        assert mod._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024

        # (b) 任何 dump_traceback_later 註冊都不得發生 (含 import 副作用)
        dumped = []
        monkeypatch.setattr(
            mod.faulthandler, "dump_traceback_later",
            lambda *a, **k: dumped.append((a, k)),
        )
        mod._faulthandler_install_fatal_handler()
        mod._faulthandler_rotate_if_needed()
        assert dumped == [], f"週期全執行緒 dump 不得回歸: {dumped}"

        # (c) 週期檔永不建立; 致命檔不被 marker 污染
        assert not (tmp_path / "faulthandler_periodic.log").exists()
        assert not list(tmp_path.glob("faulthandler_periodic*"))
        content = mod._FAULTHANDLER_PATH.read_text(encoding="utf-8")
        assert "periodic dump" not in content
    finally:
        _close_handles(mod)


def test_6b_no_periodic_functions_source_scan():
    """AST 掃描 (取代原「與 CRASH-OBS-2 逐字相同」比對): 週期相關符號 0 定義。

    原 test_6b 逐字比對七個週期函式與 a20b4e8 相同 —— 被比對的函式本票已刪除,
    該斷言必然紅且守的是舊狀態, 因此移除並以本守門取代 (不得放寬為空洞斷言)。
    """
    norm = _read_source().replace("\r\n", "\n")
    tree = ast.parse(norm)
    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_faulthandler_marker_tick" not in defined
    assert "_faulthandler_marker_loop" not in defined
    assert "_faulthandler_periodic_open" not in defined
    assert "_faulthandler_periodic_handle" not in defined
    # 仍存在的致命路徑函式 (不可順手刪掉)
    for keep in (
        "_faulthandler_open",
        "_faulthandler_install_fatal_handler",
        "_faulthandler_rotate_one",
        "_faulthandler_rotate_if_needed",
    ):
        assert keep in defined, f"{keep} 不該消失"

    code_only = ast.unparse(tree)
    assert "dump_traceback_later" not in code_only
    assert "faulthandler_periodic" not in code_only


# ── 7. heartbeat dumper (CRASH-F1-FIX: all_threads=False) ─

def test_7_heartbeat_dumper_intact():
    """_heartbeat_dumper 仍在、仍 60s、且已改 dump_traceback(all_threads=False)。

    CRASH-F1-FIX: all_threads=True 會走訪全部執行緒 → 已證實的崩潰路徑;
    all_threads=False 只傾印呼叫者自己 (event loop), 不可能與其他執行緒競態。
    """
    src_all = _read_source()
    funcs = _func_sources(src_all)
    assert "_heartbeat_dumper" in funcs, "heartbeat dumper 不可停用或移除"

    body = funcs["_heartbeat_dumper"]
    assert "faulthandler.dump_traceback(file=f, all_threads=False)" in body
    assert "all_threads=True" not in body, "全執行緒 dump 是 CRASH-F1 崩潰根因"
    assert 'data_root() / "heartbeat_trace.log"' in body
    assert "_snapshot_heartbeat_trace(_dumper_path)" in body
    assert "await asyncio.sleep(30 if _first else 60)" in body
    assert "asyncio.create_task(_heartbeat_dumper())" in src_all
    assert "_HEARTBEAT_TRACE_KEEP = 10" in src_all


def test_7b_heartbeat_dumper_differs_only_by_all_threads_flag():
    """逐字比對 (改寫版): `_snapshot_heartbeat_trace` 與 CRASH-OBS-2 完全相同;
    `_heartbeat_dumper` **只准**差在 all_threads 這一個布林 (本票唯一改動)。

    原斷言要求 `_heartbeat_dumper` 與 CRASH-OBS-2 逐字相同 —— 本票必須改掉
    all_threads=True, 因此改寫為「差異恰為該一個 token」的等價強度斷言
    (不是放寬成空洞斷言: 任何其他改動仍會紅)。
    """
    old = _func_sources(_git_show(CRASH_OBS2_REV, "scripts/run_server.py"))
    cur = _func_sources(_read_source())

    # 快照邏輯 (盲區 ii 修正) 逐字不變
    snap = "_snapshot_heartbeat_trace"
    assert snap in cur and snap in old
    assert cur[snap] == old[snap], f"{snap} 與 CRASH-OBS-2 ({CRASH_OBS2_REV}) 不一致"

    # dumper 本體: 差異恰為 all_threads True→False
    dumper = "_heartbeat_dumper"
    assert dumper in cur and dumper in old
    assert old[dumper] != cur[dumper], "本票必須改掉 all_threads=True"
    assert old[dumper].replace("all_threads=True", "all_threads=False") == cur[dumper], (
        f"{dumper} 除 all_threads 外不得有任何其他改動"
    )


# ── 8. 0 生產資料變更 ─────────────────────────────────────

def test_8_no_production_data_touched(tmp_path, monkeypatch):
    """data/faulthandler* + data/heartbeat_trace* 檔名快照前後一致。"""
    before = _prod_data_names()
    mod, calls = _exec_with_enable_recorder(tmp_path, "_rs_obs3_t8")
    try:
        assert calls, "啟動即安裝"
        assert Path(mod._FAULTHANDLER_PATH).parent == tmp_path
        assert Path(mod._FAULTHANDLER_PATH) != PROD_DATA / "faulthandler.log"
        for attr in ("_FAULTHANDLER_FILE", "_FAULTHANDLER_PERIODIC_FILE"):
            if not hasattr(mod, attr):  # CRASH-F1-FIX: 週期 handle 已移除
                continue
            handle = getattr(mod, attr)
            if handle is not None:
                assert Path(handle.name).parent == tmp_path, attr

        # 跑完整一輪 (輪替) 仍不得碰生產資料
        # (CRASH-F1-FIX: 原 `mod._faulthandler_marker_tick()` 已隨週期機制移除 →
        #  改用輪替檢查, 隔離強度不變)
        monkeypatch.setattr(mod, "_FAULTHANDLER_MAX_BYTES", 1)
        mod._faulthandler_rotate_if_needed()
    finally:
        _close_handles(mod)

    after = _prod_data_names()
    assert after == before, f"data/ 下的 faulthandler*/heartbeat_trace* 被動到: {before} -> {after}"
