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

import sys
from pathlib import Path

import pytest

# 讓 fixture 能在任意測試檔（含未自行插入 sys.path 者）匯入 src.paths。
# 用 append 而非 insert(0)，避免改變既有模組解析優先序。
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

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
