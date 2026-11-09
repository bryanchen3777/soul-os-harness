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

from src.paths import _resolve_root, data_root

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PRODUCTION_DATA_ROOT = (_REPO_ROOT / "data").resolve()

# ─────────────────────────────────────────────────────────────
# 本檔第三支測試的前置條件（SKIP-BASELINE-DRIFT-FIX-1 重寫，
# 取代舊 `requires_clean_process_env` 污染式 skip）
# ─────────────────────────────────────────────────────────────
# 舊機制（已刪除）：模組層級 `skipif` 在 import 期求值
# `os.environ.get("SOUL_OS_DATA_DIR") is not None`；
# 只要行程環境已有資料根（例如啟動前預設的隔離根）就**整支跳過** ⇒
# skip 集合隨啟動環境翻動。
# 新機制（啟動快照自證）：`tests/conftest.py::pytest_configure` 在收集前把
# `SOUL_OS_DATA_DIR` 原值存入 `config.stash`；本測試以 opt-out 身份
# （autouse 隔離 fixture 不動它）斷言「測試當下環境等於啟動快照」。
# 歷史洩漏源（`tests/soul/test_decay_worker_settlement.py` 的模組層級
# env 寫入）已改為 fixture（該檔 `:142`），故快照比對恆成立、**不再 skip**。
# 路徑斷言一律用 `_resolve_root()`（純路徑、不 mkdir）：`data_root()` 會
# `mkdir(exist_ok=True)`，為證明預設根而建立生產目錄是多餘的 I/O。


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


@pytest.mark.allow_real_data_root
def test_allow_real_data_root_marker_opts_out(startup_data_dir_snapshot):
    """白名單機制自證：標記 `allow_real_data_root` 的測試可斷言生產預設佈局。

    啟動快照自證（SKIP-BASELINE-DRIFT-FIX-1）：opt-out 身份下 autouse
    隔離 fixture 不動環境 ⇒ 測試當下必須等於收集前快照（恆執行、不 skip）。
    路徑斷言用 `_resolve_root()` 純路徑解析（不 mkdir、不碰生產目錄）。
    """
    assert os.environ.get("SOUL_OS_DATA_DIR") == startup_data_dir_snapshot, (
        "白名單測試的環境被測試期程式碼改動過："
        f"當下={os.environ.get('SOUL_OS_DATA_DIR')!r} "
        f"啟動快照={startup_data_dir_snapshot!r}"
    )
    if startup_data_dir_snapshot is None:
        # 預設根：env 未設 ⇒ 解析結果必須是生產預設佈局（repo `data/`）。
        assert _resolve_root() == _PRODUCTION_DATA_ROOT
    else:
        # 顯式根：啟動前已預設隔離根 ⇒ 解析結果必須等於該顯式根。
        assert _resolve_root() == Path(startup_data_dir_snapshot).resolve()
