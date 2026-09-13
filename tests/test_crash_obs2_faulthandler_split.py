"""
test_crash_obs2_faulthandler_split.py
CRASH-OBS-2 驗收: faulthandler 致命傾印 / 週期 dump 分檔 (觀測管線隔離)。

背景 (實測, 直接採信):
  scripts/run_server.py 的 marker daemon thread 每 60s 做一次週期 dump,
  原本與致命例外處理器**共用同一個檔** (data/faulthandler.log)。後果:
    - data/faulthandler.log:6073 = `  File Windows fatal exception: access violation`
      (致命例外標頭交錯插進週期 dump 的 frame 行中間)
    - 該檔 66035 行中 `Current thread 0x` 出現 0 次
      → 崩潰執行緒自己的 Python 堆疊從未被完整寫出

驗收對應 (工單「測試（必寫）」1-7):
  1. 分檔不變量: 兩路徑不同 + 兩個獨立 handle (各自 append, 都可 flush)
  2. 週期 dump 目標正確: tick 傳給 dump_traceback_later 的 file 是週期檔
  3. marker 格式逐字不變: `\\n===== periodic dump @ <ISO 帶 offset> =====\\n`
  4. fail-safe 分支 (tick 拋例外) 的 repeat=True 保險也指向週期檔
  5. 輪替同時涵蓋兩檔 + 輪替後 handle 重指到新檔 (非已改名的舊檔)
  6. 0 生產資料變更: 全程隔離在 tmp, data/ 下 0 建立 / 0 修改
  7. 鐵律守恆: 週期 dump 仍是每 60s; SOUL_OS_EVENT_LOOP 相關設定 0 改動

隔離手法:
  exec scripts/run_server.py 原始碼到獨立 namespace, 並在 exec 期間把
  `src.paths.data_root` 換成 tmp 目錄 → run_server 的模組層級副作用
  (開 faulthandler.log) 落在 tmp, 絕不碰生產 data/。
  本檔不啟動 uvicorn / 不 bind port / 不重啟任何服務 / 不動 data/ 任何檔案。
"""
import faulthandler
import re
import sys
import types
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RUN_SERVER_PY = ROOT / "scripts" / "run_server.py"
PROD_DATA = (ROOT / "data").resolve()


# ── helpers ───────────────────────────────────────────────

def _exec_run_server_isolated(data_root_dir: Path, mod_name: str):
    """exec run_server.py 到獨立 namespace; exec 期間 data_root() → data_root_dir。

    回傳 module object (含 _FAULTHANDLER_* 常數與 marker/輪替函式)。
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import src.paths as paths

    source = RUN_SERVER_PY.read_text(encoding="utf-8")
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


def _recorder(calls):
    """攔截 faulthandler.dump_traceback_later, 記錄傳入的 file 目標。"""

    def fake(timeout=None, repeat=False, file=None, **kwargs):  # noqa: A002
        calls.append({"timeout": timeout, "repeat": repeat, "file": file})

    return fake


def _prod_faulthandler_names():
    """data/ 下所有 faulthandler* 檔名快照 (只比名字, 不比大小/mtime:
    生產服務每 60s 會 append 這兩個檔, 大小會動, 不是本工單造成的)。"""
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
    """載入 run_server.py (隔離資料根); 測後清掉 faulthandler timer / handle 殘影。"""
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
    for attr in ("_FAULTHANDLER_FILE", "_FAULTHANDLER_PERIODIC_FILE"):
        handle = getattr(mod, attr, None)
        try:
            if handle is not None and not handle.closed:
                handle.close()
        except Exception:
            pass


# ── 1. 分檔不變量 ─────────────────────────────────────────

def test_1_two_paths_two_independent_handles(run_server, sandbox):
    """兩路徑不同 + 兩個獨立 handle, 各自 append 開啟且可 flush。"""
    assert run_server._FAULTHANDLER_PATH != run_server._FAULTHANDLER_PERIODIC_PATH
    assert run_server._FAULTHANDLER_PATH.name == "faulthandler.log"
    assert run_server._FAULTHANDLER_PERIODIC_PATH.name == "faulthandler_periodic.log"

    assert run_server._faulthandler_periodic_open() is True
    fatal = run_server._FAULTHANDLER_FILE
    periodic = run_server._FAULTHANDLER_PERIODIC_FILE
    assert fatal is not None and periodic is not None
    assert fatal is not periodic, "致命檔與週期檔必須是不同 file object / handle"
    assert "a" in fatal.mode and "a" in periodic.mode, "兩檔都必須以 append 模式開啟"
    assert Path(fatal.name) == run_server._FAULTHANDLER_PATH
    assert Path(periodic.name) == run_server._FAULTHANDLER_PERIODIC_PATH
    fatal.flush()
    periodic.flush()
    assert not fatal.closed and not periodic.closed
    # 兩 handle 都落在隔離根 → 絕非生產 data/
    assert Path(fatal.name).parent == sandbox
    assert Path(periodic.name).parent == sandbox


# ── 2. 週期 dump 目標正確 ─────────────────────────────────

def test_2_marker_tick_targets_periodic_file(run_server, monkeypatch):
    """tick 傳入 dump_traceback_later 的 file 必須是週期檔 (不是致命檔)。"""
    run_server._faulthandler_periodic_handle()
    calls = []
    monkeypatch.setattr(run_server.faulthandler, "dump_traceback_later", _recorder(calls))

    run_server._faulthandler_marker_tick()

    assert len(calls) == 1, calls
    assert calls[0]["file"] is run_server._FAULTHANDLER_PERIODIC_FILE
    assert calls[0]["file"] is not run_server._FAULTHANDLER_FILE
    assert calls[0]["timeout"] == 1
    assert calls[0]["repeat"] is False


# ── 3. marker 格式不變 ────────────────────────────────────

def test_3_marker_format_verbatim_and_only_in_periodic_file(run_server, monkeypatch):
    """標記行逐字符合 `\\n===== periodic dump @ <ISO> =====\\n` 且只寫進週期檔。"""
    run_server._faulthandler_periodic_handle()
    monkeypatch.setattr(run_server.faulthandler, "dump_traceback_later", _recorder([]))

    fatal_before = run_server._FAULTHANDLER_PATH.read_text(encoding="utf-8")
    run_server._faulthandler_marker_tick()

    # tick 內有 flush → 用另一個 handle 讀得到, 證明真的落地
    content = run_server._FAULTHANDLER_PERIODIC_PATH.read_text(encoding="utf-8")
    lines = [ln for ln in content.split("\n") if "periodic dump" in ln]
    assert lines, f"週期檔必須有 marker 行, 實際內容: {content[-300:]!r}"

    prefix, suffix = "===== periodic dump @ ", " ====="
    line = lines[-1]
    assert line.startswith(prefix) and line.endswith(suffix), line
    iso = line[len(prefix): -len(suffix)]
    assert "=====" in line
    dt = datetime.fromisoformat(iso)  # ISO 可解析
    assert dt.tzinfo is not None, f"ISO 必須帶 offset: {iso!r}"
    assert f"\n===== periodic dump @ {iso} =====\n" in content

    # 致命檔不得出現週期 marker (分檔有效)
    fatal_after = run_server._FAULTHANDLER_PATH.read_text(encoding="utf-8")
    assert fatal_after == fatal_before
    assert "periodic dump" not in fatal_after


# ── 4. fail-safe 分支指向週期檔 ───────────────────────────

def test_4_failsafe_fallback_targets_periodic_file(run_server, monkeypatch):
    """tick 拋例外 → marker loop 的 repeat=True 保險也必須指向週期檔。"""
    run_server._faulthandler_periodic_handle()
    calls = []
    monkeypatch.setattr(run_server.faulthandler, "dump_traceback_later", _recorder(calls))

    def boom():
        raise RuntimeError("boom (simulated marker tick failure)")

    monkeypatch.setattr(run_server, "_faulthandler_marker_tick", boom)

    class _StopLoop(Exception):
        pass

    slept = {"n": 0}

    def fake_sleep(_secs):
        slept["n"] += 1
        if slept["n"] >= 2:
            # 讓 marker loop 跑完一輪 (tick 失敗 → fallback) 後結束, 不真的等 60s
            raise _StopLoop()

    monkeypatch.setattr(run_server, "time", types.SimpleNamespace(sleep=fake_sleep))

    with pytest.raises(_StopLoop):
        run_server._faulthandler_marker_loop()

    assert len(calls) == 1, calls
    assert calls[0]["file"] is run_server._FAULTHANDLER_PERIODIC_FILE
    assert calls[0]["file"] is not run_server._FAULTHANDLER_FILE
    assert calls[0]["timeout"] == 60
    assert calls[0]["repeat"] is True


# ── 5. 輪替涵蓋兩檔 + handle 重開 ─────────────────────────

def test_5_rotation_covers_both_files_and_reopens_handles(run_server, monkeypatch, sandbox):
    """輪替對兩檔都生效; 輪替後 handle 重新指向新檔 (不是已改名的舊檔)。"""
    monkeypatch.setattr(run_server, "_FAULTHANDLER_MAX_BYTES", 1)  # 1 byte → 必定超標
    assert run_server._faulthandler_periodic_open() is True

    fatal_before = run_server._FAULTHANDLER_FILE
    periodic_before = run_server._FAULTHANDLER_PERIODIC_FILE
    fatal_before.write("x")
    fatal_before.flush()
    periodic_before.write("x")
    periodic_before.flush()

    assert run_server._faulthandler_rotate_if_needed() is True

    # 舊 handle 已關 (且已被改名為 <stem>.<ts>.log)
    assert fatal_before.closed and periodic_before.closed

    fatal_after = run_server._FAULTHANDLER_FILE
    periodic_after = run_server._FAULTHANDLER_PERIODIC_FILE
    assert fatal_after is not fatal_before
    assert periodic_after is not periodic_before
    assert Path(fatal_after.name) == run_server._FAULTHANDLER_PATH
    assert Path(periodic_after.name) == run_server._FAULTHANDLER_PERIODIC_PATH
    assert not fatal_after.closed and not periodic_after.closed

    # 新 handle 真的寫進「原路徑的新檔」, 不是指向已改名的舊檔
    fatal_after.write("fatal-after\n")
    fatal_after.flush()
    periodic_after.write("periodic-after\n")
    periodic_after.flush()
    assert "fatal-after" in run_server._FAULTHANDLER_PATH.read_text(encoding="utf-8")
    assert "periodic-after" in run_server._FAULTHANDLER_PERIODIC_PATH.read_text(encoding="utf-8")

    names = {p.name for p in sandbox.iterdir()}
    assert any(re.fullmatch(r"faulthandler\.\d{8}_\d{6}\.log", n) for n in names), names
    assert any(
        re.fullmatch(r"faulthandler_periodic\.\d{8}_\d{6}\.log", n) for n in names
    ), names

    # 兩組 glob 命名空間互不重疊 (輪替不會互相誤刪)
    # 注意: `faulthandler.*.log` 只匹配 <stem>.<ts>.log, 不含 faulthandler.log 本身
    fatal_glob = {p.name for p in sandbox.glob("faulthandler.*.log")}
    periodic_glob = {p.name for p in sandbox.glob("faulthandler_periodic.*.log")}
    assert any(re.fullmatch(r"faulthandler\.\d{8}_\d{6}\.log", n) for n in fatal_glob), fatal_glob
    assert any(
        re.fullmatch(r"faulthandler_periodic\.\d{8}_\d{6}\.log", n) for n in periodic_glob
    ), periodic_glob
    assert not (fatal_glob & periodic_glob), fatal_glob & periodic_glob


# ── 6. 不動生產資料 ───────────────────────────────────────

def test_6_no_production_data_touched(run_server, monkeypatch, sandbox):
    """隔離驗證: 全程 0 建立 / 0 修改 data/ 下的檔案。"""
    # (a) run_server 的兩個路徑都在隔離根
    assert run_server._FAULTHANDLER_PATH.parent == sandbox
    assert run_server._FAULTHANDLER_PERIODIC_PATH.parent == sandbox
    assert run_server._FAULTHANDLER_PATH.parent != PROD_DATA
    assert run_server._FAULTHANDLER_PERIODIC_PATH.parent != PROD_DATA
    # (b) 沒有任何 handle 開在 data/
    for attr in ("_FAULTHANDLER_FILE", "_FAULTHANDLER_PERIODIC_FILE"):
        handle = getattr(run_server, attr)
        if handle is not None:
            assert Path(handle.name).parent == sandbox, attr

    # (c) 跑完整一輪 (marker tick + 輪替檢查) 後, data/ 的 faulthandler* 檔名不變
    before = _prod_faulthandler_names()
    monkeypatch.setattr(run_server.faulthandler, "dump_traceback_later", _recorder([]))
    run_server._faulthandler_marker_tick()
    run_server._faulthandler_rotate_if_needed()
    after = _prod_faulthandler_names()
    assert after == before, f"data/ 下的 faulthandler* 被動到: {before} -> {after}"


def test_6b_import_does_not_create_periodic_file(tmp_path):
    """lazy 開啟: exec run_server 原始碼本身不得建立週期檔 (0 生產資料變更的關鍵)。

    致命檔在 import 就會被 append 開啟 (既有行為, 不變); 週期檔必須等到
    第一次真正要寫週期 dump 才建檔 → 既有測試 import run_server 不會在
    生產 data/ 下留下 faulthandler_periodic.log。
    """
    mod = _exec_run_server_isolated(tmp_path, "_run_server_crash_obs2_lazy_probe")
    try:
        assert mod._FAULTHANDLER_PERIODIC_FILE is None
        assert not (tmp_path / "faulthandler_periodic.log").exists()
        # 致命檔路徑仍是 import 即開 (目標語意不變)
        assert mod._FAULTHANDLER_FILE is not None
        assert Path(mod._FAULTHANDLER_FILE.name) == mod._FAULTHANDLER_PATH
    finally:
        for attr in ("_FAULTHANDLER_FILE", "_FAULTHANDLER_PERIODIC_FILE"):
            handle = getattr(mod, attr, None)
            try:
                if handle is not None and not handle.closed:
                    handle.close()
            except Exception:
                pass


# ── 7. 鐵律守恆 (頻率 / selector 0 改動) ──────────────────

def test_7_cadence_and_event_loop_settings_untouched(run_server, monkeypatch):
    """週期 dump 仍是每 60s; SOUL_OS_EVENT_LOOP 相關設定 0 改動。"""
    assert run_server._FAULTHANDLER_MARKER_INTERVAL_SECS == 60
    assert run_server._FAULTHANDLER_KEEP == 3
    assert run_server._FAULTHANDLER_MAX_BYTES == 32 * 1024 * 1024

    monkeypatch.delenv("SOUL_OS_EVENT_LOOP", raising=False)
    assert run_server._DEFAULT_EVENT_LOOP == "proactor"
    assert run_server._resolve_event_loop({"server": {"event_loop": "selector"}}) == "selector"
    assert run_server._resolve_event_loop({"server": {"event_loop": "proactor"}}) == "proactor"
