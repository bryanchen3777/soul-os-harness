"""
tests/conftest.py
Soul OS — pytest 層級設定（DSH P1-C2 D5）。

`execute_work`（mock/scripted 面）已標 deprecated（P1-C2 D5：docstring +
`warnings.warn(DeprecationWarning)`），但 291 tests 大量沿用（保留供測試/
離線）。在此以 pytest 的 filterwarnings 機制全域忽略 DeprecationWarning——
工單 D5 明訂「用 filterwarnings 處理，不 assert 級強制」。注意：pytest 用
`catch_warnings(record=True)` 強制記錄所有 warning，顯示與否由 filterwarnings
ini 值決定，所以必須走 `config.addinivalue_line("filterwarnings", ...)`，
不能只靠全域 `warnings.filterwarnings`。

TEST-ISOLATION-FIX-1（2026-09-13）：新增全域 autouse 資料根隔離 fixture。
背景：`tests/test_c31_relational_expression.py` 的兩支測試透過
`scheduler._decision_check()` → `src/soul/scheduler.py:459`
`DecisionTraceStore().append(...)` 把測試資料寫進**生產**
`data/soul/decision_trace.jsonl`（實測 14 行中有 12 行為測試污染）。
本 fixture 讓「未顯式宣告隔離」的測試自動把 `SOUL_OS_DATA_DIR` 指向 tmp，
從制度面關掉這條污染路徑。
"""
from __future__ import annotations

import os
import random
import sys
import warnings
from pathlib import Path

import pytest

# 讓 fixture 能在任意測試檔（含未自行插入 sys.path 者）匯入 src.paths。
# 用 append 而非 insert(0)，避免改變既有模組解析優先序。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

#: `tests/infra/` 內的共用純函式庫（skip 基準機制）。**不依賴套件化**：
#: 直接把該目錄 append 到 sys.path 後 import（與 `tests/infra/` 下的守衛檔同一做法）。
_INFRA_DIR = Path(__file__).resolve().parent / "infra"
if str(_INFRA_DIR) not in sys.path:
    sys.path.append(str(_INFRA_DIR))

import skip_baseline_lib  # noqa: E402

#: Opt-out 標記：顯式宣告「本測試必須斷言生產預設路徑佈局（repo `data/`）」。
ALLOW_REAL_DATA_ROOT = "allow_real_data_root"


def pytest_configure(config):
    """pytest filterwarnings：忽略 DeprecationWarning（execute_work mock 面標記）。"""
    config.addinivalue_line("filterwarnings", "ignore::DeprecationWarning")
    config.addinivalue_line(
        "markers",
        f"{ALLOW_REAL_DATA_ROOT}: 退出 TEST-ISOLATION-FIX-1 的全域資料根隔離。"
        "只有『必須斷言生產預設路徑佈局（repo `data/`）』的測試才可標記——"
        "白名單必須顯式且最小；一般測試請勿使用。",
    )


@pytest.fixture(autouse=True)
def _isolate_soul_os_data_root(request, tmp_path_factory):
    """TEST-ISOLATION-FIX-1：每個測試獨立資料根（無菌室）。

    設計鐵律：
      1. **Fail-safe**：本 fixture 只「指向」新建的 tmp 目錄（`mktemp`），
         **絕不刪除／截斷／改名任何既有路徑下的檔案**；對生產 `data/**`
         零寫入、零刪除。
      2. **Opt-out**：`@pytest.mark.allow_real_data_root` 可退出本 fixture
         （顯式且最小的白名單）。
      3. **不衝突**：autouse 先設，測試自身再 `monkeypatch.setenv` 或直接改
         `os.environ` 皆可覆寫。
      4. `reset_data_root()` 不可省略：`src.paths.data_root()` 是
         process-lifetime 快取；只 `setenv` 而不清快取，後續測試仍會解析到
         生產路徑（隔離會失效）。
      5. **不佔用 `monkeypatch` fixture**（實測教訓）：本 fixture 若宣告
         `monkeypatch` 參數，會把 `monkeypatch` 的 setup 提前到其他
         function-scoped fixture（如 `tests/memory/test_sage_flush_guard.py`
         的 `make_store`）之前，連帶把它的 teardown 延後 → monkeypatch 的
         patch 在 fixture teardown 時尚未還原，造成既有測試由綠轉紅
         （實測 `test_flush_failure_isolated` teardown TypeError ＋
         `test_flush_all_live_does_not_instantiate` 連鎖 n_live=2）。
         故改用**自建** `pytest.MonkeyPatch()` 實例（同一套 pytest 還原
         機制），維持既有 fixture 的先後順序不變。
    """
    from src.paths import reset_data_root

    # 顯式白名單：不動資料根（測試自行斷言生產預設佈局）。
    if request.node.get_closest_marker(ALLOW_REAL_DATA_ROOT) is not None:
        yield
        return

    isolated_root = tmp_path_factory.mktemp("soul_data_root")
    # 這是唯一的動作：把資料根「指向」新目錄——不複製、不刪除、不移動。
    env_patch = pytest.MonkeyPatch()
    env_patch.setenv("SOUL_OS_DATA_DIR", str(isolated_root))
    reset_data_root()
    # Keep an explicit empty value so load_dotenv(..., override=False) cannot
    # rehydrate a production feature flag into the pytest process.
    env_patch.setenv("LIFE_THREAD_BOOTSTRAP_ENABLED", "")
    env_patch.setenv("LIFE_THREAD_CONSOLIDATION_ENABLED", "")
    env_patch.setenv("LIFE_THREAD_CATCHUP_ENABLED", "")
    # LIFE-THREAD-W1 (2026-10-03)：同一類地雷的第五次踩點預防。
    # `life_thread_orchestrator.whim_enabled()` 採「非空且非 0／非 false 即 ON」
    # 的解析規則，**缺席即 OFF 不成立** —— 必須顯式釘成空字串，
    # 否則 2026-10-03 部署寫入 `.env` 的 `LIFE_THREAD_WHIM_ENABLED=1`
    # 會被 `load_dotenv(..., override=False)` 補回 pytest 行程，
    # 讓 7 筆早於本旗標的既有 life-thread 測試轉紅。
    env_patch.setenv("LIFE_THREAD_WHIM_ENABLED", "")
    # LIFE-THREAD-W3 (2026-10-06)：同一類地雷的第六次踩點預防。
    # `life_thread_orchestrator.elevation_enabled()` 採 `lt_wiring.TRUTHY_VALUES`
    # 白名單解析且**缺席即 OFF**（見 orchestrator docstring）—— 釘空字串的理由
    # 是擋 `load_dotenv(..., override=False)` 回注：若只靠「沒設」來當 OFF，
    # 生產 `.env` 的 `LIFE_THREAD_ELEVATION_ENABLED=1` 會被補回 pytest 行程，
    # 讓「預設關」類斷言轉紅。
    env_patch.setenv("LIFE_THREAD_ELEVATION_ENABLED", "")
    # INTIMACY-GROWTH-2 (C6)：同一類地雷的第四次踩點預防。    # `src/agent/emotion.py` 的 `decay_enabled()` **缺席即 OFF**，而
    # `load_dotenv(..., override=False)` 會在「變數不存在」時把生產
    # `.env` 的值補回 pytest 行程 ⇒ 若只靠「沒設」來當 OFF，測試會在
    # 開發機（有 .env）與 CI（無 .env）給出不同結果。
    # 顯式釘成 `""`（空字串非真值 ⇒ OFF），讓 OFF 是**被寫下來的斷言**。
    # 同理釘住 VC 通知客戶端與主服務端點的兩支旗標與 token。
    env_patch.setenv("INTIMACY_DECAY_ENABLED", "")
    env_patch.setenv("VC_INBOUND_TOUCH_ENABLED", "")
    env_patch.setenv("INTERNAL_VC_TOUCH_ENABLED", "")
    env_patch.setenv("INTERNAL_VC_TOUCH_TOKEN", "")
    try:
        yield
    finally:
        env_patch.undo()  # Restore SOUL_OS_DATA_DIR and host feature flags.
        # 清快取，避免 tmp 路徑殘留給下一個測試。
        reset_data_root()


# SOUL-TECH-DEBT-001 (2026-10-12): 補齊 EventBus 測試基礎。
#
# 背景: `tests/test_event_bus.py` 等 6 個檔案宣告 `async def test_x(bus: SoulEventBus)`
# 但全庫從未定義 `bus` fixture，導致 15 個 ERROR 級聯失敗
# (`fixture 'bus' not found`)。這與 async 模式是**兩個獨立阻塞**：
# 實測在 `asyncio_mode=auto` 下這 9 個 ERROR 依然存在。
#
# 生命週期設計（為何這樣寫）:
#   1. fixture 負責 `start()` —— 因為多數測試（test_event_bus.py 的 7 個煙霧測試）
#      只 `publish()` 從不自行啟動。
#   2. `SoulEventBus.start()` 本身 idempotent（bus.py:217 有 `if self._running` 守衛），
#      所以會自行 `await bus.start()` 的測試
#      （test_event_generation_v1/v2、test_memory_middleware）不會重複啟動。
#   3. `stop()` 對已停止的 bus 安全（`_running=False` + `if task and not done()` 守衛），
#      故 fixture 無條件 teardown 亦安全，測試自行 stop 後不會二次爆錯。
#   4. 本 fixture **不寫入 `data/**`**，與 TEST-ISOLATION-FIX-1 的資料根隔離共存。
@pytest.fixture
async def bus():
    """共用 SoulEventBus 實例（已啟動，測試結束自動優雅停止）。"""
    from src.eventbus import SoulEventBus

    b = SoulEventBus()
    await b.start()
    try:
        yield b
    finally:
        await b.stop()


# SOUL-TECH-DEBT-001 (2026-10-12)：以下兩個 fixture 補齊「獨立腳本式測試」的參數缺口。
#
# 背景: `test_event_generation_v1.py` / `test_memory_middleware.py` /
# `test_self_check_v1/v2.py` / `test_timezone_fix_v1/v2.py` 這批檔案的檔頭都寫著
# 「執行方式：python test_xxx.py」，它們是靠檔尾 `if __name__ == "__main__":`
# 區塊直接呼叫測試函式並自行餵參數：
#     tmp_dir      = Path(tempfile.mkdtemp(prefix="soul_os_phase2_"))
#     project_root = Path(__file__).resolve().parent.parent
# pytest 跑時沒有 `__main__`，該參數就變成「找不到的 fixture」→ ERROR。
# 這裡提供**等價語意**，不是為了讓測試變綠而發明新語意：
#   - `tmp_dir`      ≡ 該測試在 `__main__` 裡用的 tempfile.mkdtemp()，改由 pytest
#                     的 `tmp_path` 提供（每測試獨立、結束自動清理，不碰 data/**）。
#   - `project_root` ≡ 該測試在 `__main__` 裡的 Path(__file__).parent.parent，即 repo 根。
@pytest.fixture
def tmp_dir(tmp_path):
    """獨立腳本式測試的暫存資料根（等同該檔 `__main__` 的 tempfile.mkdtemp()）。"""
    return tmp_path


@pytest.fixture
def project_root() -> Path:
    """repo 根目錄（等同該檔 `__main__` 的 Path(__file__).resolve().parent.parent）。"""
    return Path(__file__).resolve().parent.parent


# ═════════════════════════════════════════════════════════════
# Skip 集合基準機制（FEAT-TEST-ENV-ISOLATION-CLEANUP D4）
# ═════════════════════════════════════════════════════════════
#
# 命題：同一份程式碼、任意時間重跑 ⇒ **被 skip 的測試 ID 集合逐筆相同**。
# 機制：本場實際被 skip 的測試 ID 集合 vs `tests/infra/skip_baseline.json`。
#   · **只比 ID，不比理由**（理由含時間戳／絕對路徑 ⇒ 假漂移）。
#   · 只在**全庫回歸模式**強制（子集跑只印觀察結果，不判紅）。
#   · `SOUL_OS_WRITE_SKIP_BASELINE=1` ⇒ 改寫基準檔（僅限刻意更新時）。
#   · 基準檔**不含時間戳** ⇒ 每日 churn = 0。
#
# 🔴 **前提差一拍（實測）**：工單原文寫「`config.args` 為空（裸 `pytest -q`）」
#    ⇒ 但 pytest 9 的 `config.args` 在裸跑時**不為空**（= `pytest.ini` 的
#    `testpaths` 展開結果 `["tests"]`）⇒ 不能用「args 為空」判斷全庫模式。
#    改用**公開**的 `config.args_source`（見 `_is_full_repo_run` 的說明）。


def _is_full_repo_run(config) -> bool:
    """本場是否「全庫回歸模式」（決定 skip 基準是否強制判紅）。

    判準（依序）：
      1. env `SOUL_OS_ENFORCE_SKIP_BASELINE=1` ⇒ 是（強制全庫比對）。
      2. `config.args_source == TESTPATHS` ⇒ 是（呼叫端沒指定路徑，用的是
         `pytest.ini` 的 `testpaths`）。
      3. `config.args_source == ARGS` 且 args 恰好等於 testpaths 展開 ⇒ 是
         （`pytest -q tests` 與裸跑等價）。
      4. 其餘（指定子集路徑）⇒ 否。

    另外：帶 `-k` / `-m` / `--deselect` / `--lf` / `--ff` / `--stepwise`
    的執行**不算**全庫回歸（收集集合被裁過 ⇒ skip 集合本來就會不同）。
    """
    if os.environ.get("SOUL_OS_ENFORCE_SKIP_BASELINE") == "1":
        return True
    option = getattr(config, "option", None)
    for name in (
        "keyword",
        "markexpr",
        "deselect",
        "lf",
        "ff",
        "last_failed",
        "stepwise",
        "stepwise_skip",
    ):
        if getattr(option, name, None):
            return False
    source = getattr(config, "args_source", None)
    source_name = getattr(source, "name", "") or ""
    if source_name == "TESTPATHS":
        return True
    if source_name == "ARGS":
        try:
            testpaths = [str(p) for p in (config.getini("testpaths") or [])]
        except Exception:  # noqa: BLE001 — 讀不到就當「不是全庫」
            return False
        args = [str(a) for a in (getattr(config, "args", None) or [])]
        return bool(testpaths) and sorted(args) == sorted(testpaths)
    # 舊版 pytest 沒有 args_source ⇒ 退回「args 為空」。
    return not (getattr(config, "args", None) or [])


def _fail_session(terminalreporter) -> None:
    """讓整場 exit code 變成非 0（pytest 9 的 exit status 在 sessionfinish 尾端定案）。"""
    session = getattr(terminalreporter, "_session", None)
    if session is not None:
        session.exitstatus = 1


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Skip 集合基準比對（只在全庫回歸模式強制；子集跑只印不判紅）。"""
    observed = skip_baseline_lib.collect_skip_ids(
        terminalreporter.stats.get("skipped", [])
    )
    enforce = _is_full_repo_run(config)
    write_mode = os.environ.get("SOUL_OS_WRITE_SKIP_BASELINE") == "1"

    # 🔴 2026-10-28 主大腦：終端摘要一律用 **ASCII 機器格式**。
    #   理由：本 repo 有多個「用 `subprocess.run(..., text=True)` 收 pytest 輸出」的
    #   meta-test（test_m3_world_awareness.py／test_epistemic_mind_eh42.py 等）。
    #   它們未指定 `encoding` ⇒ 本機 locale＝cp950 ⇒ **child 一旦輸出中文就會
    #   UnicodeDecodeError** ⇒ `proc.stdout is None` ⇒ 消費端假綠／假紅。
    #   （該坑本 repo 已在 tests/soul/test_life_thread_origins_m4.py:883-1007 記載。）
    #   ⇒ 摘要行保持 ASCII ⇒ 任何消費端都不會被它打爆；中文說明只放在程式碼註解。
    if write_mode:
        if not enforce:
            terminalreporter.write_line(
                "[SKIP-BASELINE] write-mode=1 but run is NOT full-repo mode"
                " => baseline NOT written (a subset run must not overwrite it)"
            )
            return
        try:
            path = skip_baseline_lib.save_baseline(observed)
        except OSError as exc:  # pragma: no cover — 磁碟錯誤
            terminalreporter.write_line(f"[SKIP-BASELINE] baseline write FAILED: {exc}")
            _fail_session(terminalreporter)
            return
        terminalreporter.write_line(
            f"[SKIP-BASELINE] baseline rewritten: {path} ids={len(observed)}"
        )
        return

    try:
        baseline = skip_baseline_lib.load_baseline()
    except skip_baseline_lib.BaselineError as exc:
        terminalreporter.write_line(f"[SKIP-BASELINE] baseline unavailable: {exc}")
        if enforce:
            _fail_session(terminalreporter)
        return

    diff = skip_baseline_lib.diff_skip_ids(baseline, observed)
    added, removed = diff["added"], diff["removed"]
    if not added and not removed:
        terminalreporter.write_line(
            f"[SKIP-BASELINE] skip-set STABLE skipped={len(observed)} "
            f"baseline={len(baseline)} added=0 removed=0"
        )
        return

    terminalreporter.write_line("")
    if enforce:
        terminalreporter.write_line("[SKIP-BASELINE] skip-set DRIFT:")
        for node_id in added:
            terminalreporter.write_line(f"  + {node_id}")
        for node_id in removed:
            terminalreporter.write_line(f"  - {node_id}")
        terminalreporter.write_line(
            "[SKIP-BASELINE] full-repo mode => FAIL (exit 1)."
            " If this change is intended, rewrite the baseline on purpose with"
            " SOUL_OS_WRITE_SKIP_BASELINE=1"
        )
        _fail_session(terminalreporter)
    else:
        # 子集跑的 skip 集合本來就只覆蓋基準的一部分 ⇒ 逐筆列表會是雜訊。
        # 只印觀察結果（計數），不判紅。
        terminalreporter.write_line(
            f"[SKIP-BASELINE] observe-only (not full-repo mode) "
            f"skipped={len(observed)} baseline={len(baseline)} "
            f"added={len(added)} removed={len(removed)}"
        )


# ═════════════════════════════════════════════════════════════
# 收集順序擾動機制（Owner 2026-10-27 裁示）
# ═════════════════════════════════════════════════════════════
#
# env `SOUL_OS_TEST_ORDER`：
#   · 未設定／空字串 ⇒ **完全不動**收集順序（既有行為零改變）。
#   · `reverse` ⇒ 反轉收集順序。
#   · `shuffle` ⇒ 以 `SOUL_OS_TEST_ORDER_SEED`（預設 0）為種子的**確定性**洗牌
#     （同一 seed ⇒ 逐位元相同的順序 ⇒ 可做 A/B 對照）。
# 只調換 item 順序：不改任何測試的語意、fixture、標記。


def pytest_collection_modifyitems(session, config, items):
    """收集順序擾動（env 驅動；**未設定時零影響**）。"""
    order = os.environ.get("SOUL_OS_TEST_ORDER", "").strip()
    if not order:
        return
    if order == "reverse":
        items.reverse()
        return
    if order == "shuffle":
        raw_seed = os.environ.get("SOUL_OS_TEST_ORDER_SEED", "0").strip() or "0"
        try:
            seed = int(raw_seed)
        except ValueError:
            warnings.warn(
                f"SOUL_OS_TEST_ORDER_SEED={raw_seed!r} 不是整數 ⇒ 退回 seed=0",
                UserWarning,
                stacklevel=2,
            )
            seed = 0
        random.Random(seed).shuffle(items)
        return
    warnings.warn(
        f"SOUL_OS_TEST_ORDER={order!r} 未知取值 ⇒ 收集順序不變"
        "（可用：reverse / shuffle）",
        UserWarning,
        stacklevel=2,
    )
