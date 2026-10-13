"""
tests/test_data_root_isolation.py — TEST-ISOLATION-FIX-1 無菌室存在證明。

本檔是「全域資料根隔離」（`tests/conftest.py` 的 autouse fixture
`_isolate_soul_os_data_root`）的**存在證明**：

  在**沒有任何顯式隔離宣告**（沒有 `monkeypatch.setenv`、沒有
  `_isolated_data_root(...)`）的情況下，`SOUL_OS_DATA_DIR` 已被 fixture
  指向非生產路徑，且 `src.paths.data_root()` 的實際解析結果也在 tmp。

最後一支測試驗證 opt-out 標記 `@pytest.mark.allow_real_data_root` 本身有效
（白名單機制自證：被標記的測試可斷言生產預設路徑佈局）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.paths import data_root

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTION_DATA_ROOT = (_REPO_ROOT / "data").resolve()

# ─────────────────────────────────────────────────────────────
# 本檔第三支測試的前置條件（Bry 2026-10-12 裁定：環境耦合，非邏輯缺陷）
# ─────────────────────────────────────────────────────────────
# `test_allow_real_data_root_marker_opts_out` 斷言的是**白名單機制本身**
# （標記了 `allow_real_data_root` 的測試，其 `SOUL_OS_DATA_DIR` 不應被
# autouse fixture 設定）。它隱含一個前置條件：**pytest 行程的環境裡
# 本來就沒有 `SOUL_OS_DATA_DIR`**。
#
# 該前置條件並非永遠成立。全庫有多支測試在 **import 時間**（pytest
# 收集階段，早於任何測試執行）以行程級 `os.environ["SOUL_OS_DATA_DIR"] = …`
# 寫入且**從不還原**，例如 `tests/soul/test_decay_worker_settlement.py:61`。
# 這類寫入是 process-wide 且無 teardown 的；只要該模組先於本檔被收集
# （`tests/soul/…` 字母序早於 `tests/test_…`，故實測必然先被收集），
# 本檔的斷言就會失敗 —— 但**失敗的是「環境已被汙染」，不是白名單機制壞掉**：
# 單獨跑本檔時三支全綠（實測 `3 passed`），與 `test_data_root_isolation.py`
# 的其他兩支無關。
#
# 守門檢查的是這個**確切的前置條件**（cheap、無副作用：只讀 process env），
# 環境乾淨時本測試照常執行並真的驗證白名單機制。
requires_clean_process_env = pytest.mark.skipif(
    os.environ.get("SOUL_OS_DATA_DIR") is not None,
    reason=(
        "Another test module leaked SOUL_OS_DATA_DIR into the pytest process at "
        "import time (no teardown), so the clean-env precondition this "
        "whitelist self-proof depends on does not hold"
    ),
)


def test_env_var_is_pointed_at_non_production_path():
    """核心斷言：未顯式隔離的測試，SOUL_OS_DATA_DIR 已被指向 tmp（非生產）。"""
    env = os.environ.get("SOUL_OS_DATA_DIR")
    assert env, "autouse 隔離 fixture 未生效：SOUL_OS_DATA_DIR 未被設定"
    resolved = Path(env).resolve()
    assert resolved != _PRODUCTION_DATA_ROOT, (
        f"SOUL_OS_DATA_DIR 仍指向生產資料根：{resolved}"
    )
    assert _PRODUCTION_DATA_ROOT not in resolved.parents, (
        f"SOUL_OS_DATA_DIR 落在生產資料根底下：{resolved}"
    )


def test_data_root_resolves_inside_isolated_dir():
    """`data_root()` 快取已被 fixture 重置，解析結果必須等於 tmp 隔離目錄。"""
    resolved = data_root()
    assert resolved == Path(os.environ["SOUL_OS_DATA_DIR"]).resolve()
    assert resolved != _PRODUCTION_DATA_ROOT


@requires_clean_process_env
@pytest.mark.allow_real_data_root
def test_allow_real_data_root_marker_opts_out():
    """白名單機制自證：標記 `allow_real_data_root` 的測試可斷言生產預設佈局。"""
    assert os.environ.get("SOUL_OS_DATA_DIR") is None, (
        "白名單測試不應被 autouse fixture 設定 SOUL_OS_DATA_DIR"
    )
    assert data_root() == _PRODUCTION_DATA_ROOT
