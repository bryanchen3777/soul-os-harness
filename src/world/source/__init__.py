"""
src/world/source/ — M3.1 Phase A WorldEventSource abstraction subpackage。

Bry 拍板 2026-08-08 02:21 A' cleanup:
  source/ 是 M3.1 Phase A 的 source abstraction subpackage, 但只放
  SyntheticWorldEventSource 一個 implementation。
  WorldEventSource ABC 改放在 src/world/base.py (sibling, 唯一 canonical)。

  目前內容:
    - synthetic.py:  SyntheticWorldEventSource (M3 既有邏輯,
                     M3.1 Phase A 改 conform WorldEventSource)
    - calendar_ical.py: IcalCalendarSource (M5.15-6 IMPLEMENTATION)
                     Real-world WorldEventSource for iCal/ICS public feeds.

  既有 M3 行為 100% 不變:
    - SyntheticWorldEventSource 的 build_*() factory methods 完全保留
    - SYNTHETIC_TEST_EVENTS spec 完全保留
    - 既有 M3 tests 透過 `from src.world import SyntheticWorldEventSource` 仍可拿到
    - 既有 path `from src.world.source import SyntheticWorldEventSource` 也可
      拿到 (透過本 __init__.py re-export, 因為 src.world.source 在這個
      subpackage 存在下被 Python 解析成 package 而非 module)

  NOTE:
    src/world/source.py (sibling module) 是一個 compatibility shim,
    內容是 re-export 邏輯。當 source/ subpackage 存在時, Python 對
    `src.world.source` 解析優先 package, 所以 source.py module
    永遠不會被加載; 但 compatibility shim 內容保留作為 path
    reference + documentation, 給未來 path 重組時 (例如刪除
    subpackage 把 source.py 重新啟用) 留伏筆。

M5.15-6 (Bry 派工 2026-08-12 19:29) — Real-World Calendar Source Integration:
  Added IcalCalendarSource (src/world/source/calendar_ical.py).
  - 0 frozen contract change (M3 WorldEvent / M3.1 ABC / M3.1 Bus / M5.4-5.1 InnerLifeEvent / M5.9-2 / M5.15-3 / M5.15-5 all preserved)
  - Public iCal URL (no OAuth, no credentials, no token store)
  - Env-gated via SOULOS_CALENDAR_ICAL_URL
  - Polling-driven (300s default), 24h lookahead default
  - Parent-only for RRULE (no recurrence engine)
  - CANCELLED skipped
  - Library: icalendar (PyPI, MIT, mature)
"""
from .synthetic import SyntheticWorldEventSource, SYNTHETIC_TEST_EVENTS
from .calendar_ical import IcalCalendarSource

__all__ = [
    "SyntheticWorldEventSource",
    "SYNTHETIC_TEST_EVENTS",
    "IcalCalendarSource",  # M5.15-6
]
