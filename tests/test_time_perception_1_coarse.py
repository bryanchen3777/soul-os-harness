"""TIME-PERCEPTION-1 — Temporal Representation（Owner 2026-10-03 裁定 A）。

裁定逐字：
  「精確時間保留，但降格」——第一行 lived time（粗顆粒）、第二行 grounding anchor。
  硬限制：語意生成主要依賴 coarse time；精確時間不得成為主要敘事錨點。

本檔驗收的三條硬規則：
  1. **粗顆粒**：輸出不得含 `HH:MM`、秒、`EDT`/`EST`。
  2. **仍由 deterministic clock 推導**：coarse phrase 與星期必須一致
     （2026-10-03 是**週六**，若硬編「週五」即 regression —— Bry 2026-10-03 親自抓到）。
  3. **不得硬湊生活意義**：沒有宣告作息時，**不得**產生距離錨。

全部為純函數 + 合成 datetime：無生產依賴、無等待。
"""
from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

import pytest

REPO = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness")
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from src.timezone_utils import (  # noqa: E402
    _WEEKDAY_CN,
    _weekday_cn,
    coarse_now_line,
    coarse_time_phrase,
    day_position,
    now_local,
)

TZ = datetime.timezone(datetime.timedelta(hours=-4))  # EDT，僅測試用


def dt(y=2026, m=10, d=3, hh=14, mm=22):
    return datetime.datetime(y, m, d, hh, mm, tzinfo=TZ)


# ── 1. 粗顆粒：不給精確數字 ─────────────────────────────────────────

MACHINE = re.compile(r"\d{1,2}:\d{2}|\bEDT\b|\bEST\b|\bUTC\b|[秒分]鐘")


@pytest.mark.parametrize("hh,mm", [
    (0, 0), (0, 30), (1, 45), (3, 5), (3, 50), (5, 0), (6, 30), (7, 3),
    (9, 5), (10, 44), (11, 0), (12, 30), (14, 22), (15, 50), (17, 15),
    (18, 59), (19, 40), (20, 40), (21, 10), (22, 30), (23, 10), (23, 50),
])
def test_never_leaks_machine_time(hh, mm):
    out = coarse_time_phrase(dt(hh=hh, mm=mm))
    assert not MACHINE.search(out), f"{hh:02d}:{mm:02d} 洩漏了機器時間：{out!r}"


def test_neither_seconds_nor_minutes_units():
    for hh in range(24):
        for mm in (0, 7, 15, 22, 30, 45, 59):
            out = coarse_time_phrase(dt(hh=hh, mm=mm))
            assert "鐘" not in out, f"出現『鐘』：{out!r}"
            assert "分" not in out, f"出現『分』：{out!r}"


# ── 2. 星期必須與 deterministic clock 一致（Bry 親自抓到的 regression）──

def test_20261003_is_saturday_not_friday():
    """2026-10-03 是週六。若實作硬編『週五』即為 regression。"""
    d = dt(y=2026, m=10, d=3, hh=14, mm=22)
    assert d.weekday() == 5, "datetime 本身就不對"
    # `_WEEKDAY_CN` 對應 strftime("%w")（週日=0），weekday() 是週一=0 —— 兩者不可混用
    assert _WEEKDAY_CN[int(d.strftime("%w"))] == "週六"
    assert _weekday_cn(d) == "週六"
    line = coarse_now_line(d)
    assert "週六" in line, f"缺少週六：{line!r}"
    assert "週五" not in line, f"出現週五（硬編錯誤）：{line!r}"


def test_weekday_helper_does_not_use_monday_based_index():
    """釘死索引口徑：`_WEEKDAY_CN[dt.weekday()]` 會整組錯一格。"""
    d = dt(m=1, d=5, hh=14, mm=22)  # 2026-01-05 是**週一**
    assert d.weekday() == 0
    assert _WEEKDAY_CN[d.weekday()] == "週日", "本斷言刻意記錄錯用的結果"
    assert _weekday_cn(d) == "週一", "正確實作必須給週一"


@pytest.mark.parametrize("day,expect", [
    (5, "週一"), (6, "週二"), (7, "週三"), (8, "週四"), (9, "週五"),
    (10, "週六"), (11, "週日"), (12, "週一"),
])
def test_weekday_tracks_clock(day, expect):
    d = dt(m=1, d=day, hh=14, mm=22)  # 2026-01
    assert _weekday_cn(d) == expect
    assert expect in coarse_now_line(d)


# ── 3. 一天位置（自我定位）與時段不得矛盾 ──────────────────────────

POSITION_HOUR = {
    "今天才剛開始": (6, 9),
    "今天已經過了一段": (11, 13),
    "今天已經過了一大段": (14, 16),
    "今天快走完了": (17, 19),
    "今天快收尾了": (20, 22),
}


@pytest.mark.parametrize("pos,span", sorted(POSITION_HOUR.items()))
def test_day_position_covers_range(pos, span):
    lo, hi = span
    for h in range(lo, hi + 1):
        assert day_position(dt(hh=h, mm=0)) == pos, f"{h:02d}:00 應為 {pos}"


def test_deep_night_position():
    assert day_position(dt(hh=23, mm=50)).startswith("今天快走完了")
    assert day_position(dt(hh=2, mm=0)).startswith("今天快走完了")


def test_coarse_line_has_both_parts():
    line = coarse_now_line(dt(hh=14, mm=22))
    assert "週六" in line
    assert "下午" in line
    assert "今天已經過了一大段" in line


# ── 4. 複用既有口徑（0 第四套）─────────────────────────────────────

def test_reuses_existing_period_label_not_a_fourth_standard():
    from src.timezone_utils import _period_label
    for h in range(24):
        assert _period_label(h) in coarse_time_phrase(dt(hh=h, mm=25)), (
            f"{h:02d} 的時段詞與既有 _period_label 不一致"
        )


def test_no_new_timezone_constant_introduced():
    src = (REPO / "src" / "timezone_utils.py").read_text(encoding="utf-8")
    assert src.count("ZoneInfo(") >= 1, "既有 LOCAL_TZ 不得被移除"
    # 本票不得新增第二個時區來源
    assert "America/New_York" in src


# ── 5. 不得硬湊生活意義（Bry 新增的 acceptance criterion）──────────

def test_no_fabricated_meaning_without_declared_schedule():
    """沒有宣告作息時，coarse 行**不得**含『離晚飯』之類的距離錨。"""
    for h in range(24):
        line = coarse_now_line(dt(hh=h, mm=30))
        for forbidden in ("離晚飯", "離 Bry", "快吃飯", "該吃飯"):
            assert forbidden not in line, f"{h:02d} 硬湊了意義：{line!r}"


def test_coarse_phrase_has_no_owner_reference():
    """粗顆粒時間描述**只描述時間本身**，不含任何人物／事件。"""
    for h in range(24):
        for forbidden in ("Bry", "主人", "吃飯", "睡", "夢"):
            assert forbidden not in coarse_time_phrase(dt(hh=h, mm=30))


# ── 6. now_local 未被破壞（2026-10-03 誤刪事故的回歸釘死）────────

def test_now_local_still_works():
    n = now_local()
    assert n.tzinfo is not None
    assert isinstance(n, datetime.datetime)


def test_grounding_line_still_available_for_ticket_2():
    """裁定 A：精確行**保留**（降格），本票不動它 —— 這裡釘死現況以便日後比對。"""
    from src.llm.proxy import _format_temporal_context
    out = _format_temporal_context(dt(hh=14, mm=22))
    assert "EDT" in out, "精確 grounding 行必須仍在（修法 8 不撤銷）"
    assert "14:22" in out
