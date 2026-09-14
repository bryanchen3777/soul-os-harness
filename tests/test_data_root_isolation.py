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
def test_allow_real_data_root_marker_opts_out():
    """白名單機制自證：標記 `allow_real_data_root` 的測試可斷言生產預設佈局。"""
    assert os.environ.get("SOUL_OS_DATA_DIR") is None, (
        "白名單測試不應被 autouse fixture 設定 SOUL_OS_DATA_DIR"
    )
    assert data_root() == _PRODUCTION_DATA_ROOT
