"""
test_crash_obs2_faulthandler_split.py
CRASH-OBS-2 遺產驗收 (faulthandler 觀測管線) — **CRASH-F1-FIX 後收斂版**。

歷史脈絡 (不可抹除的紀錄):
  CRASH-OBS-2 曾把 faulthandler 拆成兩檔 —— 致命傾印 (faulthandler.log) 與
  週期 dump (faulthandler_periodic.log), 因為週期 marker 會與致命例外標頭交錯。
  CRASH-F1-FIX (2026-09-12) 證明**週期全執行緒 dump 本身就是崩潰根因**
  (python311.dll c0000005 @ 0xA0, 244/245 次全部落在 faulthandler 自己的
  走訪路徑; 見 docs/CRASH-F1-SYMBOLS-1.md), 因此:
    - 三處 `faulthandler.dump_traceback_later(...)` 全部移除 (它沒有
      all_threads 參數, **永遠走訪全部執行緒**);
    - marker 機制 (_faulthandler_marker_tick / _faulthandler_marker_loop) 移除;
    - 週期檔常量/函式 (_FAULTHANDLER_PERIODIC_PATH / _FAULTHANDLER_PERIODIC_FILE /
      _faulthandler_periodic_open / _faulthandler_periodic_handle) 移除;
    - 輪替回歸**只管致命檔** (通用形式 `_faulthandler_rotate_one` 保留)。

本檔處置 (工單「測試（必寫）」6): 原文中「守舊狀態」的週期斷言 (test_2 / test_3 /
test_4 以及 test_1 / test_5 / test_6 / test_6b / test_7 的週期部分) **不是刪掉,
而是改寫為「週期機制已不存在」的正向守門斷言**; 仍有效的輪替 / 隔離 / 0 生產資料 /
selector 不變 斷言原樣保留。

對應驗收:
  1. 單一目標檔不變量 (只有致命檔; 週期路徑常量 0 殘留)
  2. 週期 dump 已不存在: AST 0 處 dump_traceback_later
  3. marker 已不存在: 可執行程式碼 0 處 periodic marker / tick 函式不存在
  4. marker loop / fail-safe 已不存在 (輪替自身 fail-safe 仍有效)
  5. 輪替只管致命檔 + 輪替後 handle 重指新檔 (非已改名的舊檔)
  6. 0 生產資料變更: 全程隔離在 tmp, data/ 下 0 建立 / 0 修改; 週期檔永不產生
  7. 鐵律守恆: KEEP=3 / 32MB / SOUL_OS_EVENT_LOOP 相關設定 0 改動

隔離手法:
  exec scripts/run_server.py 原始碼到獨立 namespace, 並在 exec 期間把
  `src.paths.data_root` 換成 tmp 目錄 → run_server 的模組層級副作用
  (開 faulthandler.log) 落在 tmp, 絕不碰生產 data/。
  本檔不啟動 uvicorn / 不 bind port / 不重啟任何服務 / 不動 data/ 任何檔案。
"""
import ast
import faulthandler
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER_PY = ROOT / "scripts" / "run_server.py"
PROD_DATA = (ROOT / "data").resolve()

REMOVED_PERIODIC_NAMES = (
    "_FAULTHANDLER_PERIODIC_PATH",
    "_FAULTHANDLER_PERIODIC_FILE",
    "_FAULTHANDLER_MARKER_INTERVAL_SECS",
    "_faulthandler_periodic_open",
    "_faulthandler_periodic_handle",
    "_faulthandler_marker_tick",
    "_faulthandler_marker_loop",
)


# ── helpers ───────────────────────────────────────────────

def _source() -> str:
    return RUN_SERVER_PY.read_text(encoding="utf-8")


def _code_only() -> str:
    """可執行原始碼 (ast.unparse 丟棄註解) — 斷言不被「已移除」的說明註解誤判。"""
    return ast.unparse(ast.parse(_source().replace("\r\n", "\n")))


def _exec_run_server_isolated(data_root_dir: Path, mod_name: str):
    """exec run_server.py 到獨立 namespace; exec 期間 data_root() → data_root_dir。

    回傳 module object (含 _FAULTHANDLER_* 常數與輪替函式)。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import src.paths as paths

    code = compile(_source(), str(RUN_SERVER_PY), "exec")
    mod = types.ModuleType(mod_name)
    mod.__file__ = str(RUN_SERVER_PY)

    real_data_root = paths.data_root
    paths.data_root = lambda: data_root_dir
    try:
        exec(code, mod.__dict__)
    finally:
        paths.data_root = real_data_root
    return mod


def _prod_faulthandler_names():
    """data/ 下所有 faulthandler* 檔名快照 (只比名字, 不比大小/mtime:
    生產服務本身會 append 這個檔, 大小會動, 不是本工單造成的)。"""
    if not PROD_DATA.exists():
        return None
    return sorted(
        p.name
        for p in PROD_DATA.iterdir()
        if p.is_file() and p.name.startswith("faulthandler")
    )


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> Path:
    """模組層級隔離資料根 (取代生產 data/)。"""
    return tmp_path_factory.mktemp("crash_obs2_data")


@pytest.fixture(scope="module")
def run_server(sandbox):
    """載入 run_server.py (隔離資料根); 測後清掉 faulthandler handle 殘影。"""
    mod = _exec_run_server_isolated(sandbox, "_run_server_crash_obs2_under_test")
    yield mod

    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.enable(file=sys.stderr)
    except Exception:
        pass
    handle = getattr(mod, "_FAULTHANDLER_FILE", None)
    try:
        if handle is not None and not handle.closed:
            handle.close()
    except Exception:
        pass


# ── 1. 單一目標檔不變量 (原: 兩路徑兩 handle) ─────────────

def test_1_single_fatal_target_only(run_server, sandbox):
    """只剩一個目標檔 (致命檔); 週期檔路徑常量與 handle 0 殘留。"""
    assert run_server._FAULTHANDLER_PATH.name == "faulthandler.log"
    for name in REMOVED_PERIODIC_NAMES:
        assert not hasattr(run_server, name), f"{name} 必須已移除 (週期機制不復存在)"

    fatal = run_server._FAULTHANDLER_FILE
    assert fatal is not None
    assert "a" in fatal.mode, "致命檔必須以 append 模式開啟 (不截斷既有崩潰現場)"
    assert Path(fatal.name) == run_server._FAULTHANDLER_PATH
    fatal.flush()
    assert not fatal.closed
    # handle 落在隔離根 → 絕非生產 data/
    assert Path(fatal.name).parent == sandbox
    assert (sandbox / "faulthandler.log").exists()


# ── 2. 週期 dump 已不存在 (原: 週期 dump 目標正確) ─────────

def test_2_periodic_dump_mechanism_gone(run_server):
    """AST: 0 處 `dump_traceback_later` 呼叫/屬性引用 (根因回歸守門)。

    原斷言「tick 傳給 dump_traceback_later 的 file 是週期檔」已不成立 ——
    該呼叫**整組移除**, 因為它沒有 all_threads 參數, 永遠走訪全部執行緒。
    """
    hits = []
    for node in ast.walk(ast.parse(_source().replace("\r\n", "\n"))):
        if isinstance(node, ast.Attribute) and "dump_traceback_later" in node.attr:
            hits.append((node.lineno, f".{node.attr}"))
        if isinstance(node, ast.ImportFrom):
            hits += [
                (node.lineno, a.name) for a in node.names
                if "dump_traceback_later" in a.name
            ]
        if isinstance(node, ast.Name) and "dump_traceback_later" in node.id:
            hits.append((node.lineno, node.id))
    assert hits == [], f"週期全執行緒 dump 已移除, 不得回歸: {hits}"
    assert "dump_traceback_later" not in _code_only()

    # 呼叫後也不該有任何全執行緒 dump 殘留
    calls = [
        node for node in ast.walk(ast.parse(_source().replace("\r\n", "\n")))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "dump_traceback"
    ]
    for call in calls:
        for kw in call.keywords:
            if kw.arg == "all_threads":
                assert isinstance(kw.value, ast.Constant) and kw.value.value is False, (
                    f"L{call.lineno}: dump_traceback 只能是 all_threads=False"
                )


# ── 3. marker 已不存在 (原: marker 格式不變) ──────────────

def test_3_marker_gone(run_server):
    """原斷言 marker 行逐字格式 → 改寫為「marker 機制已不存在」的正向守門。"""
    code_only = _code_only()
    assert "periodic dump" not in code_only, "marker 標記行寫入邏輯必須已移除"
    assert "_faulthandler_marker_tick" not in code_only
    assert "_faulthandler_marker_loop" not in code_only

    defined = {
        node.name for node in ast.walk(ast.parse(_source().replace("\r\n", "\n")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_faulthandler_marker_tick" not in defined
    assert "_faulthandler_marker_loop" not in defined

    # 週期檔絕不被建立 (原本 marker 會 lazy 建檔)
    assert not run_server._FAULTHANDLER_PATH.with_name("faulthandler_periodic.log").exists()
    assert not list(Path(run_server._FAULTHANDLER_PATH).parent.glob("faulthandler_periodic*"))


# ── 4. marker loop / fail-safe 已不存在 ───────────────────

def test_4_marker_loop_failsafe_gone(run_server):
    """原斷言「tick 拋例外 → repeat=True 保險指向週期檔」→ 整個 loop 已不存在。

    保留仍有效的一條: 輪替自身的 fail-safe (handle 已關 → 重開重指) 不變。
    """
    for name in ("_faulthandler_marker_loop", "_faulthandler_marker_tick"):
        assert not hasattr(run_server, name), f"{name} 必須已移除"
    assert "repeat=True" not in _code_only(), "任何 repeat=True 的週期註冊都必須消失"

    # 輪替 fail-safe 仍有效: 檔案不存在 → 不輪替、不拋例外
    assert run_server._faulthandler_rotate_one(
        run_server._FAULTHANDLER_PATH.with_name("no-such-file.log"),
        None,
        run_server._faulthandler_open,
        "no-such-file",
    ) is False


# ── 5. 輪替只管致命檔 + handle 重開 ───────────────────────

def test_5_rotation_fatal_only_and_reopens_handle(run_server, monkeypatch, sandbox):
    """輪替對致命檔生效; 輪替後 handle 重新指向新檔 (不是已改名的舊檔)。"""
    monkeypatch.setattr(run_server, "_FAULTHANDLER_MAX_BYTES", 1)  # 1 byte → 必定超標

    fatal_before = run_server._FAULTHANDLER_FILE
    fatal_before.write("x")
    fatal_before.flush()

    assert run_server._faulthandler_rotate_if_needed() is True

    # 舊 handle 已關 (且已被改名為 faulthandler.<ts>.log)
    assert fatal_before.closed

    fatal_after = run_server._FAULTHANDLER_FILE
    assert fatal_after is not fatal_before
    assert Path(fatal_after.name) == run_server._FAULTHANDLER_PATH
    assert not fatal_after.closed

    # 新 handle 真的寫進「原路徑的新檔」, 不是指向已改名的舊檔
    fatal_after.write("fatal-after\n")
    fatal_after.flush()
    assert "fatal-after" in run_server._FAULTHANDLER_PATH.read_text(encoding="utf-8")

    names = {p.name for p in sandbox.iterdir()}
    assert any(re.fullmatch(r"faulthandler\.\d{8}_\d{6}\.log", n) for n in names), names
    # 週期檔 (含輪替後的 faulthandler_periodic.<ts>.log) 一律不得存在
    assert not [n for n in names if "periodic" in n], names

    # 輪替 glob 只涵蓋 faulthandler.<ts>.log, 不含 faulthandler.log 本身
    fatal_glob = {p.name for p in sandbox.glob("faulthandler.*.log")}
    assert any(re.fullmatch(r"faulthandler\.\d{8}_\d{6}\.log", n) for n in fatal_glob), fatal_glob
    assert "faulthandler.log" not in fatal_glob


# ── 6. 不動生產資料 ───────────────────────────────────────

def test_6_no_production_data_touched(run_server, monkeypatch, sandbox):
    """隔離驗證: 全程 0 建立 / 0 修改 data/ 下的檔案。"""
    # (a) 致命檔路徑在隔離根
    assert run_server._FAULTHANDLER_PATH.parent == sandbox
    assert run_server._FAULTHANDLER_PATH.parent != PROD_DATA
    # (b) 沒有任何 handle 開在 data/
    handle = run_server._FAULTHANDLER_FILE
    if handle is not None:
        assert Path(handle.name).parent == sandbox

    # (c) 跑完整一輪 (輪替檢查) 後, data/ 的 faulthandler* 檔名不變
    before = _prod_faulthandler_names()
    run_server._faulthandler_rotate_if_needed()
    after = _prod_faulthandler_names()
    assert after == before, f"data/ 下的 faulthandler* 被動到: {before} -> {after}"


def test_6b_import_never_creates_periodic_file(tmp_path):
    """瘦身守門: import run_server **永不**建立 faulthandler_periodic.log。

    原斷言「週期檔是 lazy 開啟 (import 時 _FAULTHANDLER_PERIODIC_FILE is None)」
    → 改寫為更強的正向斷言: 該檔在任何路徑下都不會被建立 (機制已移除)。
    """
    mod = _exec_run_server_isolated(tmp_path, "_run_server_crash_obs2_lazy_probe")
    try:
        assert not hasattr(mod, "_FAULTHANDLER_PERIODIC_FILE")
        assert not (tmp_path / "faulthandler_periodic.log").exists()
        assert not list(tmp_path.glob("faulthandler_periodic*"))
        # 致命檔路徑仍是 import 即開 (目標語意不變)
        assert mod._FAULTHANDLER_FILE is not None
        assert Path(mod._FAULTHANDLER_FILE.name) == mod._FAULTHANDLER_PATH
    finally:
        handle = getattr(mod, "_FAULTHANDLER_FILE", None)
        try:
            if handle is not None and not handle.closed:
                handle.close()
        except Exception:
            pass


# ── 7. 鐵律守恆 (上限 / selector 0 改動) ──────────────────

def test_7_caps_and_event_loop_settings_untouched(run_server, monkeypatch):
    """輪替上限/KEEP 不變; 週期節奏常量已不存在; SOUL_OS_EVENT_LOOP 相關設定 0 改動。"""
    assert run_server._FAULTHANDLER_KEEP == 3
    assert run_server._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024
    # 原斷言 `_FAULTHANDLER_MARKER_INTERVAL_SECS == 60` → 改寫為「該常量已不存在」
    assert not hasattr(run_server, "_FAULTHANDLER_MARKER_INTERVAL_SECS")

    monkeypatch.delenv("SOUL_OS_EVENT_LOOP", raising=False)
    assert run_server._DEFAULT_EVENT_LOOP == "proactor"
    assert run_server._resolve_event_loop({"server": {"event_loop": "selector"}}) == "selector"
    assert run_server._resolve_event_loop({"server": {"event_loop": "proactor"}}) == "proactor"
