# -*- coding: utf-8 -*-
"""tests/test_ledger_refresh_slots.py — 夜槽時區守衛（LEDGER-SLOT-TZ-1）。

工單
----
`LEDGER-SLOT-TZ-1`：`scripts/ledger_refresh.py::next_slot_iso()` 的下一個夜槽
必須依「**紐約當地 22:00**」推導（`zoneinfo.ZoneInfo("America/New_York")`），
消除兩類缺陷：① DST（舊寫法硬寫 02:00Z ⇒ 冬令整季錯一小時）；
② 跳槽（UTC `date()+1 天` 再取 02:00 ⇒ 跳到再隔一天）。

本檔的守衛
----------
* S1 驗收錨點：`next_slot_iso(2026-11-10T01:42Z) == 2026-11-10T03:00Z`。
* S2 槽前／恰槽／槽後三種相對位置（恰槽必須進下一天：嚴格晚於 now）。
* S3 非 UTC aware 輸入（+08:00、紐約本地）與 naive 硬拒。
* S4 DST 邊界矩陣（11/01 與 03/14 前後）＋ 美國選舉日（非固定 offset 證據）。
* S5 `cohort_not_before`：日序仍是 D+8；構造是本地 (D+7) 22:00 轉 UTC；
  非法輸入硬拒；缺 tz 資料硬拒（不得退回固定 offset）。
* S6 mock gate：0/PASS 與 3/PENDING_COHORT 接受；1/FAIL 與 2/硬拒必須被拒。
* S7 雙向變異（M1～M6，每條必須轉紅）。

🔴 安全邊界
------------
* 只測**純函式層**（`next_slot_iso`／`cohort_not_before`／gate-2 條件 AST）；
  **永不呼叫 `main()`**（那會走 git commit 路徑）。
* **不啟動子行程**（R5）、不連網、不讀寫 `data/**`、不碰生產排程。
* 變異體一律寫在 %TEMP% 副本上載入；repo 檔全程唯讀（前後 SHA 對帳）。
* 變異的是純時區數學，無任何觸達生產的路徑（無 socket／無行程／無檔案寫入）。
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REFRESH_PATH = REPO_ROOT / "scripts" / "ledger_refresh.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None, "cannot load %s" % path
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never drop a __pycache__ into scripts/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


REFRESH = _load("ledger_refresh_slots", REFRESH_PATH)


def _U(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ═════════════════════════════════════════════════════════════════════
# S1 — 驗收錨點（今晚的實害案例）
# ═════════════════════════════════════════════════════════════════════


def test_s1_acceptance_next_slot_is_local_2200_in_utc():
    """now=2026-11-10T01:42Z（= 11-09 20:42 EST）⇒ 下一槽是 11-10T03:00Z。

    舊實作給出 2026-11-11T02:00Z（跳槽＋錯 offset）⇒ 2026-11-09 20:40 的
    每日刷新把 gate2 跑在錯槽上。本測試釘死正確答案。
    """
    got = REFRESH.next_slot_iso(_U("2026-11-10T01:42:00+00:00"))
    assert got == _U("2026-11-10T03:00:00+00:00"), (
        "下一個夜槽必須是紐約 11-09 22:00 EST 的 UTC 投影，得到 %s" % got.isoformat())
    assert got.tzinfo is not None and got.utcoffset() is not None


# ═════════════════════════════════════════════════════════════════════
# S2 — 槽前／恰槽／槽後
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("now_s,exp_s", [
    ("2026-11-09T10:00:00+00:00", "2026-11-10T03:00:00+00:00"),  # 槽前（當天清晨）
    ("2026-11-10T01:42:00+00:00", "2026-11-10T03:00:00+00:00"),  # 槽前（當晚）
    ("2026-11-10T02:59:59+00:00", "2026-11-10T03:00:00+00:00"),  # 槽前 1 秒
    ("2026-11-10T03:00:00+00:00", "2026-11-11T03:00:00+00:00"),  # 恰槽 ⇒ 下一天
    ("2026-11-10T03:00:01+00:00", "2026-11-11T03:00:00+00:00"),  # 槽後 ⇒ 下一天
    ("2026-11-10T12:00:00+00:00", "2026-11-11T03:00:00+00:00"),  # 槽後（白天）
])
def test_s2_relative_position(now_s, exp_s):
    assert REFRESH.next_slot_iso(_U(now_s)) == _U(exp_s), (
        "now=%s 的下一槽必須是 %s" % (now_s, exp_s))


# ═════════════════════════════════════════════════════════════════════
# S3 — 非 UTC 輸入；naive 硬拒
# ═════════════════════════════════════════════════════════════════════


def test_s3_non_utc_aware_inputs_agree():
    """同一時刻用不同時區表達 ⇒ 同一答案（不猜、不漂移）。"""
    from zoneinfo import ZoneInfo
    ny = ZoneInfo("America/New_York")
    exp = _U("2026-11-10T03:00:00+00:00")
    assert REFRESH.next_slot_iso(_U("2026-11-10T09:42:00+08:00")) == exp
    assert REFRESH.next_slot_iso(
        datetime(2026, 11, 9, 20, 42, tzinfo=ny)) == exp


def test_s3_naive_datetime_is_rejected():
    """naive datetime 必須被硬拒（不猜時區）。"""
    with pytest.raises(ValueError):
        REFRESH.next_slot_iso(datetime(2026, 11, 10, 1, 42))
    with pytest.raises(ValueError):
        REFRESH.next_slot_iso(datetime(2026, 11, 10, 1, 42, 0))


# ═════════════════════════════════════════════════════════════════════
# S4 — DST 邊界矩陣 ＋ 選舉日
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("now_s,exp_s,label", [
    ("2026-11-01T01:00:00+00:00", "2026-11-01T02:00:00+00:00", "10-31 仍是 EDT"),
    ("2026-11-01T02:30:00+00:00", "2026-11-02T03:00:00+00:00", "10-31 槽過後 ⇒ 11-01 槽（EST）"),
    ("2026-11-02T02:00:00+00:00", "2026-11-02T03:00:00+00:00", "11-01 已是 EST"),
    ("2027-03-14T02:00:00+00:00", "2027-03-14T03:00:00+00:00", "03-13 仍是 EST"),
    ("2027-03-15T01:00:00+00:00", "2027-03-15T02:00:00+00:00", "03-14 已是 EDT"),
    ("2026-11-03T12:00:00+00:00", "2026-11-04T03:00:00+00:00", "美國選舉日（EST，非固定 offset）"),
])
def test_s4_dst_boundary_matrix(now_s, exp_s, label):
    got = REFRESH.next_slot_iso(_U(now_s))
    assert got == _U(exp_s), "（%s）now=%s ⇒ 必須是 %s，得到 %s" % (
        label, now_s, exp_s, got.isoformat())


# ═════════════════════════════════════════════════════════════════════
# S5 — cohort_not_before（日序 D+8；構造是本地 (D+7) 22:00 轉 UTC）
# ═════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("date,exp", [
    ("2026-10-24", "2026-11-01T02:00:00+00:00"),  # D+7=11-01 前仍是 EDT
    ("2026-10-25", "2026-11-02T03:00:00+00:00"),  # D+7=11-01 已是 EST
    ("2026-10-26", "2026-11-03T03:00:00+00:00"),
    ("2026-10-27", "2026-11-04T03:00:00+00:00"),
    ("2026-10-30", "2026-11-07T03:00:00+00:00"),
    ("2026-10-31", "2026-11-08T03:00:00+00:00"),
    ("2026-11-01", "2026-11-09T03:00:00+00:00"),
    ("2026-11-05", "2026-11-13T03:00:00+00:00"),
    ("2026-11-09", "2026-11-17T03:00:00+00:00"),
    ("2026-12-28", "2027-01-05T03:00:00+00:00"),
    ("2027-03-06", "2027-03-14T03:00:00+00:00"),  # D+7=03-13 仍是 EST
    ("2027-03-07", "2027-03-15T02:00:00+00:00"),  # D+7=03-14 已是 EDT
])
def test_s5_cohort_not_before_values(date, exp):
    assert REFRESH.cohort_not_before(date) == exp


@pytest.mark.parametrize("date", [
    "2026-10-11", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21",
    "2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26",
    "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30", "2026-10-31",
    "2026-11-01", "2026-11-02", "2026-11-03", "2026-11-09",
])
def test_s5_cohort_not_before_day_order_is_d_plus_8(date):
    """UTC 日期標籤的日序恆為 D+8（與舊公式一致；差的只是那一小時投影）。"""
    d = datetime.strptime(date, "%Y-%m-%d").date()
    got = REFRESH.cohort_not_before(date)
    assert got[:10] == (d + timedelta(days=8)).isoformat(), (
        "cohort %s 的 not_before 日期標籤必須是 D+8，得到 %s" % (date, got))


def test_s5_cohort_not_before_rejects_garbage():
    with pytest.raises(ValueError):
        REFRESH.cohort_not_before("not-a-date")
    with pytest.raises(ValueError):
        REFRESH.cohort_not_before("2026-13-45")


def test_s5_missing_tzdata_is_a_hard_reject(monkeypatch):
    """缺 tz 資料 ⇒ 硬拒（RuntimeError），不得退回固定 offset。"""
    import zoneinfo

    def _boom(name):
        raise zoneinfo.ZoneInfoNotFoundError("no tzdata in this env")

    monkeypatch.setattr(REFRESH, "ZoneInfo", _boom)
    with pytest.raises(RuntimeError):
        REFRESH.next_slot_iso(_U("2026-11-10T01:42:00+00:00"))
    with pytest.raises(RuntimeError):
        REFRESH.cohort_not_before("2026-10-26")


# ═════════════════════════════════════════════════════════════════════
# S6 — mock gate（gate-2 三態，AST 直取判定式）
# ═════════════════════════════════════════════════════════════════════


def _gate2():
    src = REFRESH_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")
    cond = None
    for stmt in main_fn.body:
        if isinstance(stmt, ast.If):
            names = {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)}
            if "rc2" in names:
                cond = stmt.test
                break
    assert cond is not None, "gate-2 判定式（提到 rc2 的 if）找不到"
    pending_expr = None
    for stmt in ast.walk(main_fn):
        if isinstance(stmt, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "pending_ok"
                for t in stmt.targets):
            pending_expr = ast.unparse(stmt.value)
            break
    assert pending_expr is not None, "pending_ok 賦值找不到"
    return ast.unparse(cond), pending_expr


_COND_SRC, _PENDING_SRC = _gate2()


def _gate2_is_red(rc1, rc2, verdict2):
    ns = {"rc1": rc1, "rc2": rc2, "verdict2": verdict2}
    ns["pending_ok"] = eval(_PENDING_SRC, dict(ns), {})
    return bool(eval(_COND_SRC, dict(ns), {}))


@pytest.mark.parametrize("rc1,rc2,verdict,red,label", [
    (0, 0, ["VERDICT: PASS ..."], False, "0/PASS 接受"),
    (0, 3, ["VERDICT: PENDING_COHORT ..."], False, "3/PENDING_COHORT 接受"),
    (0, 1, ["VERDICT: FAIL ..."], True, "1/FAIL 拒絕"),
    (0, 2, [], True, "2/硬拒 拒絕"),
    (1, 0, ["VERDICT: PASS ..."], True, "gate1 紅仍是紅"),
])
def test_s6_gate2_matrix(rc1, rc2, verdict, red, label):
    assert _gate2_is_red(rc1, rc2, verdict) is red, "gate-2（%s）判定錯誤" % label


# ═════════════════════════════════════════════════════════════════════
# S7 — 雙向變異（M1～M6，每條必須轉紅；TEMP 副本；repo SHA 對帳）
# ═════════════════════════════════════════════════════════════════════


def _mutant_module(text: str, tag: str):
    with tempfile.TemporaryDirectory(prefix="slot_mut_") as td:
        p = Path(td) / ("ledger_refresh_%s.py" % tag)
        p.write_text(text, encoding="utf-8")
        return _load("ledger_refresh_mut_%s" % tag, p)


def _assert_killed(mod, killer, label: str):
    try:
        killer(mod)
    except AssertionError:
        return  # 變異轉紅（被殺死）⇒ 有牙
    raise AssertionError("變異存活（未轉紅）：%s —— 依工單立即停止並回報" % label)


def _kill_p0(mod):
    assert mod.next_slot_iso(_U("2026-11-10T01:42:00+00:00")) == _U(
        "2026-11-10T03:00:00+00:00")


def _kill_dst_summer(mod):
    assert mod.next_slot_iso(_U("2026-11-01T01:00:00+00:00")) == _U(
        "2026-11-01T02:00:00+00:00")


def _kill_atslot(mod):
    assert mod.next_slot_iso(_U("2026-11-10T03:00:00+00:00")) == _U(
        "2026-11-11T03:00:00+00:00")


def _kill_cohort_order(mod):
    assert mod.cohort_not_before("2026-10-26") == "2026-11-03T03:00:00+00:00"


def _mutate(old: str, new: str, label: str) -> str:
    text = REFRESH_PATH.read_text(encoding="utf-8")
    assert old in text, "變異錨點未命中（%s）：源碼已漂移，先停下回報" % label
    return text.replace(old, new)


def test_s7m1_fixed_02_turns_red():
    """M1 固定 02:00Z ⇒ 冬令 P0 轉紅（給出 02:00Z 而非 03:00Z）。"""
    sha_before = _sha256(REFRESH_PATH)
    text = _mutate("22, 0, tzinfo=ny", "2, 0, tzinfo=timezone.utc", "M1")
    assert text.count("22, 0, tzinfo=ny") == 0, "M1 必須換掉兩處本地 22:00"
    _assert_killed(_mutant_module(text, "m1"), _kill_p0, "M1 固定 02")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"


def test_s7m2_fixed_03_turns_red():
    """M2 固定 03:00Z ⇒ 夏令 10-31 槽轉紅（給出 03:00Z 而非 02:00Z）。"""
    sha_before = _sha256(REFRESH_PATH)
    text = _mutate("22, 0, tzinfo=ny", "3, 0, tzinfo=timezone.utc", "M2")
    assert text.count("22, 0, tzinfo=ny") == 0, "M2 必須換掉兩處本地 22:00"
    _assert_killed(_mutant_module(text, "m2"), _kill_dst_summer, "M2 固定 03")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"


def test_s7m3_utc_day_plus_one_turns_red():
    """M3 回到 UTC 日加一（舊跳槽 bug）⇒ P0 跳到再隔一天 ⇒ 轉紅。"""
    sha_before = _sha256(REFRESH_PATH)
    old = ("    local = now_utc.astimezone(ny)\n"
           "    cand = datetime(local.year, local.month, local.day, 22, 0, tzinfo=ny)\n"
           "    if cand <= now_utc:\n"
           "        nxt = local.date() + timedelta(days=1)\n"
           "        cand = datetime(nxt.year, nxt.month, nxt.day, 22, 0, tzinfo=ny)\n")
    new = ("    day = now_utc.date() + timedelta(days=1)\n"
           "    cand = datetime(day.year, day.month, day.day, 2, 0, tzinfo=timezone.utc)\n")
    text = _mutate(old, new, "M3")
    _assert_killed(_mutant_module(text, "m3"), _kill_p0, "M3 UTC 日加一")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"


def test_s7m4_le_to_lt_turns_red():
    """M4 `<=` 改 `<` ⇒ 恰槽不再進下一天（違反嚴格晚於）⇒ 轉紅。"""
    sha_before = _sha256(REFRESH_PATH)
    text = _mutate("if cand <= now_utc:", "if cand < now_utc:  # MUTANT", "M4")
    _assert_killed(_mutant_module(text, "m4"), _kill_atslot, "M4 <= 改 <")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"


def test_s7m5_fixed_offset_turns_red():
    """M5 固定 offset（EST）⇒ 夏令 10-31 槽轉紅（給出 03:00Z 而非 02:00Z）。"""
    sha_before = _sha256(REFRESH_PATH)
    text = _mutate('return ZoneInfo("America/New_York")',
                   "return timezone(timedelta(hours=-5))  # MUTANT", "M5")
    _assert_killed(_mutant_module(text, "m5"), _kill_dst_summer, "M5 固定 offset")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"


def test_s7m6_wrong_cohort_day_order_turns_red():
    """M6 cohort 日序 D+7→D+8（⇒ UTC D+9）⇒ 日序斷言轉紅。"""
    sha_before = _sha256(REFRESH_PATH)
    text = _mutate("target = d + timedelta(days=7)",
                   "target = d + timedelta(days=8)  # MUTANT", "M6")
    _assert_killed(_mutant_module(text, "m6"), _kill_cohort_order, "M6 錯的日序")
    assert _sha256(REFRESH_PATH) == sha_before, "變異不得寫入 repo"
