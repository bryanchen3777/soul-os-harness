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
    try:
        yield
    finally:
        env_patch.undo()  # 還原 SOUL_OS_DATA_DIR 原值（多為「未設定」）
        # 清快取，避免 tmp 路徑殘留給下一個測試。
        reset_data_root()
