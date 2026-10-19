"""
tests/test_soul_decay_flaky_audit.py
SOUL-DECAY-FLAKY-AUDIT（2026-10-19）：decay flaky 測試的守衛。

背景
────────────────────────────────────────────────────────────────────────
`tests/test_m5_13_5_untouched_decay.py` 在全庫回歸中偶發失敗。根因**已實證**
是**牆鐘漂移競態**（不是跨日邊界、不是浮點精度、不是產品邏輯錯誤）：

  * 實作端 `src/soul/relationships.py:236` 的 `_decay_locked()` **自己**取
    `now = datetime.now(timezone.utc)`；
  * 測試端**另外**取一次 `datetime.now(timezone.utc)` 當參考時刻；
  * 兩者之間的漂移 Δ 直接灌進衰減量
    （`:269` `days = (now - anchor_dt).total_seconds() / 86400.0`
      → `:272` `decay = days * CONFIDENCE_DECAY_PER_DAY`）；
  * `assertAlmostEqual(..., places=10)` ⇒ 容差 0.5e-10（confidence）
    ⇒ **Δ 預算 = 0.5e-10 / 0.02 天 = 0.216 ms**。

⇒ 任何 > 0.216 ms 的延遲（GC pause／OS 排程搶占／防毒掃描／磁碟 IO）
  都足以翻紅。

修法：**注入固定時鐘**（`patch("src.soul.relationships.datetime", FrozenDatetime)`），
**不放寬任何斷言容差**。

本檔是守衛，釘住四件事
────────────────────────────────────────────────────────────────────────
  1. 競態量化：把「places=10 ⇒ 0.216 ms 預算」算成程式碼可驗的數字，
     並斷言它 **< 1 ms**（證明未凍結時這支測試本質上脆弱），
     同時斷言凍結後 Δ **恆為 0**。
  2. 注入點有效性：斷言 patch 真的生效（不是空轉），
     且 `fromisoformat()` 仍保持原生語意。
  3. 衰減語意：斷言「untouched 從 created_at 起算」與
     「touched 從 last_interaction_at 起算」**兩條語意仍成立且互相可區分**
     （確保重寫沒有把測試改成同義反覆的假綠）。
  4. 污染：凍結結束後 `src.soul.relationships.datetime` 必須還原為原生類別。
  5. 容差政策（AST）：重寫後的測試檔裡**沒有任何** `assertAlmostEqual`
     的 `places` 被放寬。

本檔**刻意依賴** `test_m5_13_5_untouched_decay` 模組的 `frozen_clock()` /
`reference_instant()` —— 這樣一旦有人把那份測試的凍結時鐘注入拿掉
（＝退回牆鐘），本檔的守衛會**轉紅**，而不是默默變成盲腸。
"""
from __future__ import annotations

import ast
import os
import pathlib
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# 讓 `import test_m5_13_5_untouched_decay` 在單獨跑本檔時也能解析。
_TESTS_DIR = pathlib.Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))

import test_m5_13_5_untouched_decay as decay_mod  # noqa: E402

import src.soul.relationships as rel  # noqa: E402
from src.soul.relationships import (  # noqa: E402
    BRYAN_ENTITY_ID,
    CONFIDENCE_DECAY_PER_DAY,
    UNTOUCHED_DECAY_GRACE_DAYS,
)

#: 重寫後測試檔使用的嚴格 places（不得放寬）。
STRICT_PLACES = 10

#: `assertAlmostEqual` 的 confidence 容差 → 可忍受的 Δ（天）。
def _delta_budget_ms(places: int, decay_per_day: float = CONFIDENCE_DECAY_PER_DAY) -> float:
    """把 `assertAlmostEqual(places=…)` 的容差換算成「時間漂移預算」（毫秒）。

    unittest 的 `assertAlmostEqual(a, b, places=p)` 等價於
    `round(a - b, p) == 0` ⇒ 容差 = 0.5 * 10**-p（confidence 單位）。
    confidence 每漂移一天會變動 `decay_per_day`（0.02），
    所以可忍受的時間漂移 = 容差 / decay_per_day（天）→ 換成毫秒。
    """
    tolerance = 0.5 * (10 ** -places)
    days = tolerance / decay_per_day
    return days * 86400.0 * 1000.0


# ── 1. 競態量化守衛 ──


class TestRaceBudgetQuantification(unittest.TestCase):
    """把根因釘成可回歸的數字。

    這支測試**同時**做三件事，任何一件被破壞都會轉紅：
      (a) 算出 places=10 的時間預算並斷言 < 1 ms（脆弱性證據）；
      (b) 斷言凍結時鐘下 Δ **恆為 0**（重寫確實生效）；
      (c) 斷言真實牆鐘的 Δ **非零且大於預算**（競態確實存在）。
    """

    def test_race_budget_quantified_and_frozen_delta_is_zero(self):
        budget_ms = _delta_budget_ms(STRICT_PLACES)

        # (a) 脆弱性證據：places=10 的時間預算必須小於 1 ms。
        self.assertLess(
            budget_ms, 1.0,
            f"places={STRICT_PLACES} 只容許 {budget_ms:.6f} ms 的時鐘漂移；"
            f"若 > 1 ms 則這支測試在未凍結時本質上脆弱（GC/OS 排程即可翻紅）",
        )
        # 順便把精確數字釘住（0.216 ms），防止常數被偷偷改掉。
        #   0.5e-10 / 0.02 = 2.5e-9 天 → 2.16e-4 秒 → 0.216 ms
        self.assertAlmostEqual(budget_ms, 0.216, places=9)

        # (b) 凍結時鐘下，實作端讀到的 now 與測試端參考時刻必須完全相等。
        with decay_mod.frozen_clock():
            impl_now = rel.datetime.now(timezone.utc)
            reference = decay_mod.reference_instant()
            delta_ms = abs((impl_now - reference).total_seconds()) * 1000.0

        self.assertEqual(
            delta_ms, 0.0,
            f"凍結時鐘下 Δ 必須恆為 0（實作端讀到 {impl_now!r}，"
            f"測試端參考 {reference!r}，Δ={delta_ms} ms）",
        )
        self.assertEqual(impl_now, reference)

        # (c) 真實牆鐘確實會漂移 —— 5 ms 的延遲就遠超 0.216 ms 預算。
        first = datetime.now(timezone.utc)
        time.sleep(0.005)
        second = datetime.now(timezone.utc)
        wall_delta_ms = (second - first).total_seconds() * 1000.0
        self.assertGreater(
            wall_delta_ms, 1.0,
            f"真實牆鐘在 5 ms sleep 後只漂移 {wall_delta_ms} ms，"
            f"時鐘解析度低到量不出競態（測量環境異常）",
        )
        self.assertGreater(
            wall_delta_ms, budget_ms,
            f"真實牆鐘漂移 {wall_delta_ms:.4f} ms 必須超過 places={STRICT_PLACES} "
            f"的預算 {budget_ms:.6f} ms —— 這正是舊版測試偶發翻紅的原因",
        )


# ── 2. 注入點有效性守衛 ──


class TestInjectionPointEffectiveness(unittest.TestCase):
    """斷言凍結時鐘真的注入了，而不是空轉。"""

    def test_module_datetime_is_replaced_by_frozen_class(self):
        native = datetime
        with decay_mod.frozen_clock():
            self.assertIs(
                rel.datetime, decay_mod.FrozenDatetime,
                "src.soul.relationships.datetime 必須被換成 FrozenDatetime 子類",
            )
            self.assertIsNot(rel.datetime, native)
            self.assertTrue(issubclass(rel.datetime, native))

    def test_frozen_now_returns_exactly_the_specified_instant(self):
        expected = decay_mod.FROZEN_NOW
        with decay_mod.frozen_clock():
            got_utc = rel.datetime.now(timezone.utc)
            got_naive = rel.datetime.now()
            self.assertEqual(got_utc, expected)
            self.assertEqual(got_utc.isoformat(), expected.isoformat())
            # 沒有 tz 參數時也必須是同一個時刻（naive 版本）。
            self.assertEqual(got_naive.replace(tzinfo=timezone.utc), expected)
            # 不同 tz 參數 → 同一個絕對時刻。
            self.assertEqual(rel.datetime.now(timezone.utc), expected)

    def test_fromisoformat_remains_native(self):
        """`fromisoformat()` 必須保持原生語意（實作端靠它解析 entry 的 ISO 字串）。"""
        aware_iso = decay_mod.FROZEN_NOW.isoformat()
        naive_iso = decay_mod.FROZEN_NOW.replace(tzinfo=None).isoformat()
        with decay_mod.frozen_clock():
            parsed_aware = rel.datetime.fromisoformat(aware_iso)
            parsed_naive = rel.datetime.fromisoformat(naive_iso)
        self.assertIsInstance(parsed_aware, datetime)
        self.assertIsInstance(parsed_naive, datetime)
        self.assertEqual(parsed_aware, decay_mod.FROZEN_NOW)
        self.assertEqual(parsed_aware.tzinfo, timezone.utc)
        self.assertIsNone(parsed_naive.tzinfo)
        self.assertEqual(parsed_naive.replace(tzinfo=timezone.utc), decay_mod.FROZEN_NOW)


# ── 3. 衰減語意守衛 ──


class TestDecaySemantics(unittest.TestCase):
    """凍結後的測試**仍然有牙**：兩條錨點語意必須成立且互相可區分。"""

    def _untouched_confidence(self, days_ago: float, initial: float = 0.5) -> float:
        with decay_mod.frozen_clock() as now:
            store, tmp, other_id = decay_mod._make_real_store(initial, "audit_untouched")
            try:
                decay_mod._set_created_at_days_ago(store, other_id, days_ago, now)
                return store.get(other_id)["confidence"]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def _touched_confidence(self, created_days_ago: float, touched_days_ago: float,
                            initial: float = 0.5) -> float:
        with decay_mod.frozen_clock() as now:
            tmp = tempfile.mkdtemp(prefix="audit_touched_")
            try:
                store = rel.RelationshipsStore(
                    agent_id="audit_touched",
                    data_dir=pathlib.Path(tmp),
                )
                store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=initial)
                entry = store._cache["others"][BRYAN_ENTITY_ID]
                entry["created_at"] = (now - timedelta(days=created_days_ago)).isoformat()
                entry["last_interaction_at"] = (now - timedelta(days=touched_days_ago)).isoformat()
                return store.get(BRYAN_ENTITY_ID)["confidence"]
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_beyond_grace_decays_from_created_at(self):
        """untouched 過去 grace ⇒ 從 `created_at` 起算衰減。"""
        got = self._untouched_confidence(5.0)
        expected = 0.5 - 5.0 * CONFIDENCE_DECAY_PER_DAY
        self.assertEqual(
            got, expected,
            f"untouched 5 天前建立的 entry 應衰減到 {expected!r}，實得 {got!r}",
        )
        # 語意可區分性：若實作改成「created_at + grace」錨點，會得到別的值。
        grace_shifted = 0.5 - (5.0 - UNTOUCHED_DECAY_GRACE_DAYS) * CONFIDENCE_DECAY_PER_DAY
        self.assertNotEqual(got, grace_shifted)

    def test_touched_decays_from_last_interaction_not_created_at(self):
        """touched ⇒ 從 `last_interaction_at` 起算（不是 created_at）。"""
        got = self._touched_confidence(created_days_ago=30.0, touched_days_ago=5.0)
        expected = 0.5 - 5.0 * CONFIDENCE_DECAY_PER_DAY
        self.assertEqual(
            got, expected,
            f"touched 5 天前的 entry 應衰減到 {expected!r}，實得 {got!r}",
        )
        # 若誤用 created_at 錨點 ⇒ 30 天 → 0.5 - 0.6 → clamp 到 0.0。
        created_at_anchored = max(0.0, 0.5 - 30.0 * CONFIDENCE_DECAY_PER_DAY)
        self.assertNotEqual(
            got, created_at_anchored,
            "touched entry 的衰減量必須與 created_at 錨點可區分，否則語意守衛是盲腸",
        )

    def test_decay_rate_is_exact_under_frozen_clock(self):
        """衰減率本身也要被守衛：0.02/day 不可被偷偷乘上任何係數。"""
        got = self._untouched_confidence(5.0)
        self.assertEqual(got, 0.5 - 5.0 * 0.02)
        self.assertNotEqual(got, 0.5 - 5.0 * 0.02 * 1.01)


# ── 4. 污染守衛 ──


class TestNoPollution(unittest.TestCase):
    """凍結結束後，模組層級 `datetime` 必須還原為原生類別。"""

    def test_datetime_restored_after_frozen_clock(self):
        native = datetime
        self.assertIs(rel.datetime, native, "進入前就已經不是原生類別（前一輪測試洩漏了？）")

        with decay_mod.frozen_clock():
            self.assertIsNot(rel.datetime, native)

        self.assertIs(
            rel.datetime, native,
            "凍結時鐘結束後 src.soul.relationships.datetime 必須還原為原生 datetime",
        )
        self.assertIsNot(rel.datetime.now(timezone.utc), decay_mod.FROZEN_NOW)

    def test_repeated_use_leaves_no_residue(self):
        """連續多次凍結 / 還原不得殘留。"""
        native = datetime
        for _ in range(3):
            with decay_mod.frozen_clock():
                self.assertIs(rel.datetime, decay_mod.FrozenDatetime)
            self.assertIs(rel.datetime, native)


# ── 5. 容差政策守衛（AST） ──


class TestTolerancePolicy(unittest.TestCase):
    """重寫**不得**靠放寬容差來「修好」flaky。

    直接用 AST 讀重寫後測試檔，確保沒有任何 `assertAlmostEqual` 的
    `places` 被放寬（> 10），且嚴格斷言（places == 10）仍在。
    """

    @staticmethod
    def _places_values() -> list:
        source = pathlib.Path(decay_mod.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        values = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "assertAlmostEqual"):
                continue
            places = 7  # unittest 預設
            for kw in node.keywords:
                if kw.arg == "places":
                    places = ast.literal_eval(kw.value)
            values.append(places)
        return values

    def test_no_assertion_tolerance_was_loosened(self):
        values = self._places_values()
        self.assertGreaterEqual(len(values), 6, f"找不到 assertAlmostEqual 斷言：{values!r}")
        worst = max(values)
        self.assertLessEqual(
            worst, STRICT_PLACES,
            f"有人把 assertAlmostEqual 的 places 放寬到 {worst}（上限 {STRICT_PLACES}）—— "
            f"flaky 必須用注入固定時鐘修，不得放寬容差",
        )

    def test_strict_places_assertions_are_still_present(self):
        values = self._places_values()
        self.assertGreaterEqual(
            values.count(STRICT_PLACES), 5,
            f"places={STRICT_PLACES} 的嚴格斷言只剩 {values.count(STRICT_PLACES)} 個（原本 5 個）：{values!r}",
        )


if __name__ == "__main__":
    unittest.main()
