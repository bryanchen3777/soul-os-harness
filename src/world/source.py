"""
src/world/source.py — Soul OS M3.1 Phase A compatibility shim

Bry 拍板 2026-08-08 02:21 A' cleanup:

這個檔案是最薄 compatibility shim, 內容 re-export SyntheticWorldEventSource
跟 SYNTHETIC_TEST_EVENTS 從 src.world.source.synthetic。

【重要 Python 行為說明】
  當 src/world/source.py (module) 跟 src/world/source/ (subpackage with __init__.py)
  同時存在時, Python 的 import system 會把 `src.world.source` 解析成
  **package** (regular package 優先於同名 module)。

  所以實際 import path:
    from src.world.source import SyntheticWorldEventSource
      → 走 src/world/source/__init__.py 內 re-export
      → 從 src.world.source.synthetic 拿 class

  這個 .py module 永遠不會被加載, 但 shim 內容保留作:
    1. Path reference: 給 reader 明確知道 src.world.source 應該是
       SyntheticWorldEventSource 的 entry point
    2. Documentation: 解釋 Python 對同名 module + package 的優先行為
    3. Future 伏筆: 如果未來決定移除 source/ subpackage 重新回到
       M3 原始的扁平 source.py module 結構, 這個 shim 內容就是
       屆時 source.py 的 template

  三條 import path 全部指向同一個 class (M3.1 Phase A A' cleanup contract):
    - from src.world import SyntheticWorldEventSource
        (透過 src/world/__init__.py re-export)
    - from src.world.source import SyntheticWorldEventSource
        (透過 src/world/source/__init__.py re-export, package wins)
    - from src.world.source.synthetic import SyntheticWorldEventSource
        (直接從 subpackage module 拿)
"""
from __future__ import annotations

# 這個 shim 永遠不會被執行 (Python 解析優先 package),
# 但 shim 內容保留為 path reference + future 伏筆。
from .source.synthetic import SyntheticWorldEventSource, SYNTHETIC_TEST_EVENTS  # noqa: F401

__all__ = [
    "SyntheticWorldEventSource",
    "SYNTHETIC_TEST_EVENTS",
]
