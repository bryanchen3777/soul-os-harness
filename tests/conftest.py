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
"""
import pytest


def pytest_configure(config):
    """pytest filterwarnings：忽略 DeprecationWarning（execute_work mock 面標記）。"""
    config.addinivalue_line("filterwarnings", "ignore::DeprecationWarning")
