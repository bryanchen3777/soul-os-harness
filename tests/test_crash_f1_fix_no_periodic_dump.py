"""
test_crash_f1_fix_no_periodic_dump.py
CRASH-F1-FIX 驗收: **來源守門測試** —— 週期性「全執行緒」dump 已被移除, 且未來
任何人把它加回來, 本檔立刻紅。

根因 (指令級鐵證, 直接採信; 完整報告 docs/CRASH-F1-SYMBOLS-1.md):
  python311.dll + c0000005 + 存取位址 0xA0 的 244/245 次崩潰, 全部落在
  faulthandler 傾印器自己的走訪路徑上:
      PyCode_Addr2Line+0xE  ← mov rcx,[rcx+0xA0], RCX=0 (NULL 的 code object)
      dump_frame+0xB1 → dump_traceback+0x6D → _Py_DumpTracebackThreads+0x160
      → faulthandler_thread+0x50
  faulthandler **不停世界**: 在其他執行緒正在執行/釋放 frame 的同時走訪全部
  執行緒堆疊 → dump_frame 在 +0x18 讀到的非 NULL `f_code`, 到 +0x95 重讀已變 0。
  `faulthandler.dump_traceback_later(...)` **沒有 all_threads 參數, 永遠走訪
  全部執行緒** → 就是已證實的崩潰路徑, 因此三處呼叫全部移除 (模組層級
  repeat=True / marker tick timeout=1 / marker loop fail-safe timeout=60)。

驗收對應 (工單「測試（必寫）」1-5):
  1. 來源守門 (最重要):
     - AST: 0 處 `dump_traceback_later` 呼叫或屬性引用 (含 cancel_ 變體)
     - AST: 0 處 `dump_traceback(..., all_threads=True)`
     - AST/模組: `_faulthandler_marker_loop` / `_faulthandler_marker_tick` 不存在
     - AST: 不再 import threading (marker daemon thread 的唯一殘留)
  2. 致命通道仍完好: 啟動路徑仍呼叫 `faulthandler.enable(file=<致命檔>)`
     (CRASH-OBS-3 續綠, 不得回退)
  3. heartbeat dumper 仍在 / 仍 60s / 已改 all_threads=False / 路徑與快照語意不變
  4. 輪替只管致命檔 (faulthandler.log 仍輪替; 週期檔常量與函式 0 殘留)
  5. 0 生產資料: data/faulthandler* + data/heartbeat_trace* 檔名快照前後一致

隔離手法 (沿用 tests/test_crash_obs2_faulthandler_split.py):
  exec scripts/run_server.py 原始碼到獨立 namespace, 期間把 src.paths.data_root
  換成 tmp → 模組層級副作用 (開檔 / enable) 全落在 tmp, 絕不碰生產 data/。
  本檔不啟動 uvicorn / 不 bind port / 不重啟任何服務 / 不改 data/ 任何檔案。
"""
import ast
import faulthandler
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER_PY = ROOT / "scripts" / "run_server.py"
PROD_DATA = (ROOT / "data").resolve()

REMOVED_PERIODIC_FUNCS = ("_faulthandler_marker_tick", "_faulthandler_marker_loop")
REMOVED_PERIODIC_NAMES = (
    "_FAULTHANDLER_PERIODIC_PATH",
    "_FAULTHANDLER_PERIODIC_FILE",
    "_FAULTHANDLER_MARKER_INTERVAL_SECS",
    "_faulthandler_periodic_open",
    "_faulthandler_periodic_handle",
)


# ── helpers ───────────────────────────────────────────────

def _read_source() -> str:
    return RUN_SERVER_PY.read_text(encoding="utf-8")


def _tree(source: str) -> ast.Module:
    return ast.parse(source.replace("\r\n", "\n"))


def _code_only(source: str) -> str:
    """去掉註解與排版後的**可執行原始碼** (ast.unparse 天然丟棄註解)。

    用途: 讓守門斷言不被「說明本機制已移除」的註解誤判, 同時保證
    可執行程式碼裡連字面字串都 0 殘留 (等同嚴格 grep, 但不會被註解騙過)。
    """
    return ast.unparse(_tree(source))


def _func_sources(source: str) -> dict:
    norm = source.replace("\r\n", "\n")
    return {
        node.name: ast.get_source_segment(norm, node)
        for node in ast.walk(ast.parse(norm))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _exec_run_server_isolated(data_root_dir: Path, mod_name: str):
    """exec run_server.py 到獨立 namespace; exec 期間 data_root() → data_root_dir。"""
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import src.paths as paths

    code = compile(_read_source(), str(RUN_SERVER_PY), "exec")
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
    """exec run_server.py, 期間攔截 faulthandler.enable → 回傳 (mod, calls)。"""
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


def _prod_data_names():
    """data/ 下 faulthandler* + heartbeat_trace* 檔名快照 (只比名字)。

    不比大小/mtime: 生產服務本身會 append 這些檔, 大小本來就會動。
    """
    if not PROD_DATA.exists():
        return None
    return sorted(
        p.name
        for p in PROD_DATA.iterdir()
        if p.is_file()
        and (p.name.startswith("faulthandler") or p.name.startswith("heartbeat_trace"))
    )


def _close_handles(mod) -> None:
    handle = getattr(mod, "_FAULTHANDLER_FILE", None)
    try:
        if handle is not None and not handle.closed:
            handle.close()
    except Exception:
        pass


# ── fixtures ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _restore_real_faulthandler():
    """測後把本行程的 faulthandler 目標還原 (exec 就會 enable 到 tmp handle)。"""
    yield
    try:
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    try:
        faulthandler.enable(file=sys.stderr)
    except Exception:
        pass


@pytest.fixture(scope="module")
def run_server(tmp_path_factory):
    sandbox = tmp_path_factory.mktemp("crash_f1_fix_data")
    mod = _exec_run_server_isolated(sandbox, "_run_server_crash_f1_fix_under_test")
    yield mod
    _close_handles(mod)


# ── 1. 來源守門 (最重要: 防止根因回歸) ────────────────────

def test_1_no_dump_traceback_later_anywhere():
    """AST 掃描: scripts/run_server.py 0 處 `dump_traceback_later` 呼叫/屬性引用。

    這是本票最核心的守門: `dump_traceback_later` **沒有 all_threads 參數, 永遠
    走訪全部執行緒** → 已證實的 c0000005 @ 0xA0 崩潰路徑。任何形式的引用
    (屬性存取、直接 import 的名字、cancel_ 變體) 都必須是 0。
    """
    source = _read_source()
    tree = _tree(source)

    hits = []
    for node in ast.walk(tree):
        # faulthandler.dump_traceback_later / faulthandler.cancel_dump_traceback_later
        if isinstance(node, ast.Attribute) and "dump_traceback_later" in node.attr:
            hits.append((node.lineno, f".{node.attr}"))
        # from faulthandler import dump_traceback_later
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if "dump_traceback_later" in alias.name:
                    hits.append((node.lineno, f"import {alias.name}"))
        # 直接以裸名引用 (含字串化的 getattr 目標)
        if isinstance(node, ast.Name) and "dump_traceback_later" in node.id:
            hits.append((node.lineno, node.id))
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "dump_traceback_later" in node.value:
                hits.append((node.lineno, f"str-const {node.value[:40]!r}"))

    assert hits == [], (
        "run_server.py 不得再出現 dump_traceback_later (週期全執行緒 dump = "
        f"CRASH-F1 崩潰根因); 實際命中: {hits}"
    )
    # 可執行程式碼層級的嚴格掃描 (註解不算; 連字面字串也不放過)
    assert "dump_traceback_later" not in _code_only(source), (
        "可執行程式碼中仍殘留 dump_traceback_later 字樣"
    )


def test_1b_no_all_threads_true_in_dump_traceback():
    """AST 掃描: 0 處 `faulthandler.dump_traceback(..., all_threads=True)`。

    全檔只允許一處 dump_traceback, 且必須是 heartbeat dumper 的
    all_threads=False (只傾印呼叫者自己那條執行緒, 不可能與其他執行緒競態)。
    """
    tree = _tree(_read_source())
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "dump_traceback":
            kwargs = {kw.arg: kw.value for kw in node.keywords}
            value = kwargs.get("all_threads")
            literal = value.value if isinstance(value, ast.Constant) else None
            calls.append((node.lineno, literal))

    assert calls, "heartbeat dumper 的 dump_traceback 不該消失 (liveness 觀測)"
    bad = [c for c in calls if c[1] is not False]
    assert bad == [], (
        "dump_traceback 只允許 all_threads=False (全執行緒 dump = CRASH-F1 崩潰根因); "
        f"違規: {bad}"
    )


def test_1c_marker_mechanism_fully_removed():
    """marker 機制整組不存在: 兩個函式 0 定義、不再 import threading。"""
    source = _read_source()
    tree = _tree(source)
    defined = {
        node.name for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    for name in REMOVED_PERIODIC_FUNCS:
        assert name not in defined, f"{name} 必須已移除 (它是週期全執行緒 dump 的推手)"

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "threading" not in imported, "marker daemon thread 的唯一殘留 (import threading) 必須移除"

    # 可執行程式碼層級 (註解不算) 的嚴格掃描: 兩個函式名 0 殘留
    code_only = _code_only(source)
    for name in REMOVED_PERIODIC_FUNCS:
        assert name not in code_only, f"可執行程式碼仍殘留 {name}"


def test_1d_removed_periodic_module_names_absent(run_server):
    """模組層級: 所有週期檔常量/函式 0 殘留; 只剩致命檔路徑。"""
    for name in REMOVED_PERIODIC_NAMES:
        assert not hasattr(run_server, name), f"{name} 必須已移除"
    assert run_server._FAULTHANDLER_PATH.name == "faulthandler.log"


# ── 2. 致命通道仍完好 (CRASH-OBS-3 續綠, 不得回退) ────────

def test_2_fatal_handler_still_installed_at_startup(tmp_path):
    """啟動路徑仍呼叫 faulthandler.enable(file=<致命檔>), 且 is_enabled()==True。"""
    mod, calls = _exec_with_enable_recorder(tmp_path, "_rs_f1_t2")
    try:
        assert len(calls) == 1, calls
        target = calls[0]["file"]
        assert target is mod._FAULTHANDLER_FILE
        assert Path(target.name) == mod._FAULTHANDLER_PATH == tmp_path / "faulthandler.log"
        assert target.closed is False
        assert "a" in target.mode, "append 模式 (不截斷既有崩潰現場)"
    finally:
        _close_handles(mod)

    # 真實 enable (非 mock) 也必須生效
    mod2 = _exec_run_server_isolated(tmp_path, "_rs_f1_t2b")
    try:
        assert faulthandler.is_enabled() is True
    finally:
        _close_handles(mod2)


def test_2b_install_fatal_handler_source_intact():
    """`_faulthandler_install_fatal_handler` 仍在、仍是模組層級無條件安裝。"""
    funcs = _func_sources(_read_source())
    assert "_faulthandler_install_fatal_handler" in funcs
    body = funcs["_faulthandler_install_fatal_handler"]
    assert "faulthandler.enable(file=handle)" in body
    assert "致命傾印已安裝" in body
    # 模組層級 (非縮排) 呼叫, 不依賴輪替
    assert "\n_faulthandler_install_fatal_handler()\n" in _read_source()


# ── 3. heartbeat dumper ───────────────────────────────────

def test_3_heartbeat_dumper_all_threads_false():
    """_heartbeat_dumper 仍在、仍 60s、已改 all_threads=False、路徑/快照語意不變。"""
    source = _read_source()
    funcs = _func_sources(source)
    assert "_heartbeat_dumper" in funcs, "heartbeat dumper 不可停用或移除"
    body = funcs["_heartbeat_dumper"]

    assert "faulthandler.dump_traceback(file=f, all_threads=False)" in body, body
    assert "all_threads=True" not in body

    # 60s 節奏 + 覆寫語意 + 路徑 + 快照 (一律不變)
    assert "await asyncio.sleep(30 if _first else 60)" in body
    assert 'data_root() / "heartbeat_trace.log"' in body
    assert "_snapshot_heartbeat_trace(_dumper_path)" in body
    assert "(overwrite, every 60s)" in body
    assert "asyncio.create_task(_heartbeat_dumper())" in source

    # 快照邏輯與 KEEP=10 不動
    assert "_HEARTBEAT_TRACE_KEEP = 10" in source
    snap = funcs["_snapshot_heartbeat_trace"]
    assert 'f"heartbeat_trace.{ts}.log"' in snap
    assert "_HEARTBEAT_TRACE_KEEP" in snap


def test_3b_heartbeat_dumper_runs_60s_cadence(run_server):
    """節奏常數仍是 30s 首輪 / 60s 之後 (原始碼級證據, 不啟動 event loop)。"""
    body = _func_sources(_read_source())["_heartbeat_dumper"]
    assert "30 if _first else 60" in body
    assert "every 60s" in body


# ── 4. 輪替只管致命檔 ─────────────────────────────────────

def test_4_rotation_only_covers_fatal_file(run_server, monkeypatch, tmp_path):
    """輪替對致命檔生效; 不再產生任何 faulthandler_periodic* 檔。"""
    sandbox = Path(run_server._FAULTHANDLER_PATH).parent
    monkeypatch.setattr(run_server, "_FAULTHANDLER_MAX_BYTES", 1)  # 1 byte → 必定超標

    before = run_server._FAULTHANDLER_FILE
    before.write("x")
    before.flush()

    assert run_server._faulthandler_rotate_if_needed() is True
    assert before.closed, "輪替必須關掉舊 handle"

    after = run_server._FAULTHANDLER_FILE
    assert after is not before and not after.closed
    assert Path(after.name) == run_server._FAULTHANDLER_PATH

    after.write("fatal-after\n")
    after.flush()
    assert "fatal-after" in run_server._FAULTHANDLER_PATH.read_text(encoding="utf-8")

    names = {p.name for p in sandbox.iterdir()}
    assert any(n.startswith("faulthandler.") and n.endswith(".log") for n in names), names
    assert not [n for n in names if "periodic" in n], names


def test_4b_rotation_no_rotation_on_clean_small_file(tmp_path):
    """乾淨小檔不輪替 (輪替條件未鬆動)。"""
    mod = _exec_run_server_isolated(tmp_path, "_rs_f1_t4b")
    try:
        assert mod._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024
        assert mod._FAULTHANDLER_KEEP == 3
        assert mod._faulthandler_rotate_if_needed() is False
        assert not list(tmp_path.glob("faulthandler.*.log"))
    finally:
        _close_handles(mod)


# ── 5. 0 生產資料變更 ─────────────────────────────────────

def test_5_no_production_data_touched(tmp_path):
    """data/faulthandler* + data/heartbeat_trace* 檔名快照前後一致。"""
    before = _prod_data_names()
    mod, calls = _exec_with_enable_recorder(tmp_path, "_rs_f1_t5")
    try:
        assert calls, "啟動即安裝"
        assert Path(mod._FAULTHANDLER_PATH).parent == tmp_path
        assert Path(mod._FAULTHANDLER_PATH) != PROD_DATA / "faulthandler.log"
        assert not (tmp_path / "faulthandler_periodic.log").exists()
        mod._faulthandler_rotate_if_needed()
    finally:
        _close_handles(mod)

    after = _prod_data_names()
    assert after == before, f"data/ 下的 faulthandler*/heartbeat_trace* 被動到: {before} -> {after}"
