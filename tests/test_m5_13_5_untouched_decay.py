"""
tests/test_m5_13_5_untouched_decay.py
M5.13-5 (Bry 派工 2026-08-12 20:42): Untouched-Entry Decay.

Adds a `created_at` fallback for entries that have `last_interaction_at is None`.
- Within grace period (default 1.0 day): no decay (M5.13-2 strict 0.3 contract)
- Beyond grace period: decays from `created_at` (anchor)

Tests cover:
  A. Untouched entry within grace → no decay (preserves 0.3 contract)
  B. Untouched entry beyond grace → decays from created_at
  C. Grace boundary (just over 1.0 day) → decays deterministically
  D. Touched entry uses last_interaction_at (not created_at) — even if created_at is old
  E. Legacy entry without created_at → no decay, no crash
  F. Bad created_at timestamp → no crash, skip entry
  G. Grace constant 1.0 day, in days, deterministic

────────────────────────────────────────────────────────────────────────
SOUL-DECAY-FLAKY-AUDIT（2026-10-19）：注入固定時鐘，取代牆鐘競態
────────────────────────────────────────────────────────────────────────
這支檔案原本是**牆鐘敏感**的脆弱測試（全庫回歸中偶發紅）。根因（已實證）：

  * 實作端 `src/soul/relationships.py:236` 的 `_decay_locked()` **自己**取
    `now = datetime.now(timezone.utc)`；
  * 測試端（原本的 `_set_created_at_days_ago`）**另外**取一次
    `datetime.now(timezone.utc)` 當參考時刻；
  * 兩次取時之間的牆鐘漂移 Δ 直接灌進衰減量：
    `days = (now - anchor_dt).total_seconds() / 86400.0`（`:269`）
    ⇒ `decay = (N + Δ) * CONFIDENCE_DECAY_PER_DAY`（`:272`, 0.02/day）；
  * 斷言用 `assertAlmostEqual(..., places=10)` ⇒ 容差 0.5e-10（confidence）
    ⇒ **Δ 預算只有 0.5e-10 / 0.02 天 = 0.216 ms**。

⇒ 任何超過 0.216 ms 的延遲（GC pause／OS 排程搶占／防毒掃描／磁碟 IO）
  都足以讓這支測試翻紅。這是**測試脆弱**，不是產品邏輯錯誤。

修法（工單決策）：**注入固定時鐘**，**不放寬任何斷言容差**。
`src/soul/relationships.py:42` 的模組層級 `datetime` **就是類別本身**，
因此測試端可以 `patch("src.soul.relationships.datetime", FrozenDatetime)`
把實作端與測試端拉進**同一個**時鐘 ⇒ Δ ≡ 0 ⇒ `places=10` 從「靠運氣」
變成**位元精確的確定性斷言**。

`places` 只可保持或收緊，永遠不可放寬（工單硬性要求）。
守衛見 `tests/test_soul_decay_flaky_audit.py`。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Frozen clock (SOUL-DECAY-FLAKY-AUDIT) ─────────────────────────────
#
# 單一事實來源：FROZEN_NOW 是本檔所有「參考時刻」的來源。測試端寫入的
# created_at / last_interaction_at，與實作端 `_decay_locked()` 讀到的 `now`，
# 必須來自同一個時鐘 —— Δ 才會恆為 0。

#: 凍結時刻。刻意挑一個「非整日、非整點」的固定值，任何日曆邊界都無關。
FROZEN_NOW = datetime(2026, 10, 19, 12, 0, 0, tzinfo=timezone.utc)


class FrozenDatetime(datetime):
    """`datetime` 的子類：只覆寫 `now()`，其餘（含 `fromisoformat`）保持原生。

    `datetime` 不能被 `datetime` 實例化，但**可以**被 subclass；
    `now()` 是 classmethod，覆寫後 `datetime.now(tz)` 會回傳固定時刻。

    `fromisoformat()` **刻意不覆寫** —— 實作端用它解析 entry 裡的 ISO
    字串（`src/soul/relationships.py:250` / `:263`），語意必須保持原生。
    """

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_NOW.replace(tzinfo=None)
        return FROZEN_NOW.astimezone(tz)


@contextmanager
def frozen_clock():
    """把 `src.soul.relationships.datetime` 換成 `FrozenDatetime`。

    用 context manager ⇒ 離開 `with` 一定還原，**不會洩漏**到其他測試
    （污染守衛見 `tests/test_soul_decay_flaky_audit.py`）。

    Yields: 凍結時刻（測試端所有參考時刻都由它推導）。
    """
    with patch("src.soul.relationships.datetime", FrozenDatetime):
        yield FROZEN_NOW


def reference_instant():
    """測試端的參考時刻 —— 與實作端讀到的是**同一個**時刻。

    守衛（`test_soul_decay_flaky_audit.py`）會驗證這件事：實作端讀到的
    `now` 必須與本值**完全相等**（Δ ≡ 0）。
    """
    return FROZEN_NOW


# ── Helpers ──


def _make_real_store(initial_confidence: float = 0.5, agent_id: str = "m5_13_5_test"):
    """
    Build a real RelationshipsStore in a tempdir, create one untouched
    relationship with the given initial confidence.

    Returns (store, tempdir, other_id).
    The entry has last_interaction_at = None and created_at = now.

    ⚠️ 必須在 `frozen_clock()` 生效期間呼叫：這樣 `ensure_relationship()`
    寫進的 `created_at` 才是**凍結時刻**（`_now_iso()` 讀的是被 patch 的
    模組層級 `datetime`），測試端與實作端才看見同一個「現在」。
    """
    from src.soul.relationships import (
        RelationshipsStore,
        BRYAN_ENTITY_ID,
    )

    tmp = tempfile.mkdtemp(prefix="m5_13_5_")
    store = RelationshipsStore(
        agent_id=agent_id,
        data_dir=pathlib.Path(tmp),
    )
    store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=initial_confidence)
    return store, tmp, BRYAN_ENTITY_ID


def _set_created_at_days_ago(store, other_id: str, days_ago: float, now: datetime) -> None:
    """
    Mutate the entry's created_at to be `days_ago` before the **given**
    reference instant `now`.

    `now` 必須由呼叫端從**同一個凍結時鐘**取得（見 `frozen_clock()`）。
    這是整個重寫的核心：參考時刻不再自己另外抓一次牆鐘，
    Δ（測試取時 ↔ 實作取時的漂移）恆為 0。

    Modifies the in-memory cache directly (deterministic for tests).
    """
    past = now - timedelta(days=days_ago)
    entry = store._cache["others"][other_id]
    entry["created_at"] = past.isoformat()


# ── A. Untouched entry within grace → no decay ──


class TestUntouchedWithinGraceNoDecay(unittest.TestCase):
    """
    M5.13-5: An untouched entry created within the grace period
    (UNTOUCHED_DECAY_GRACE_DAYS = 1.0 day) must NOT decay.

    This preserves M5.13-2 strict 0.3 contract: ensure_relationship(0.3)
    followed by get() must return 0.3 (not decayed).
    """

    def test_untouched_within_grace_no_decay_at_0_3(self):
        """0.3 fresh untouched entry within grace → 0.3 (strict contract)."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.3, "m5_13_5_a1")
            try:
                _set_created_at_days_ago(store, other_id, 0.5, now)  # 12h ago, within grace
                entry = store.get(other_id)
                self.assertEqual(
                    entry["confidence"], 0.3,
                    f"Within grace, 0.3 must stay at 0.3, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_within_grace_no_decay_at_0_5(self):
        """0.5 fresh untouched entry within grace → 0.5 (no decay)."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.5, "m5_13_5_a2")
            try:
                _set_created_at_days_ago(store, other_id, 0.1, now)  # 2.4h ago, within grace
                entry = store.get(other_id)
                self.assertEqual(
                    entry["confidence"], 0.5,
                    f"Within grace, 0.5 must stay at 0.5, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_at_grace_boundary_no_decay(self):
        """Just under 1.0 day grace → no decay (grace inclusive of < 1.0)."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.7, "m5_13_5_a3")
            try:
                # Just under 1.0 day — should still be within grace
                _set_created_at_days_ago(store, other_id, 0.999, now)
                entry = store.get(other_id)
                self.assertEqual(
                    entry["confidence"], 0.7,
                    f"Just under 1.0 day grace: 0.7 must stay at 0.7, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)


# ── B. Untouched entry beyond grace → decays from created_at ──


class TestUntouchedBeyondGraceDecays(unittest.TestCase):
    """
    M5.13-5: An untouched entry older than the grace period decays
    from its `created_at` timestamp.

    Decay rate = CONFIDENCE_DECAY_PER_DAY (0.02/day).
    decay = max(0, age_days - UNTOUCHED_DECAY_GRACE_DAYS) * 0.02

    Note: The implementation uses `created_at` as the anchor (not
    `created_at + grace`), so the actual decay is `age_days * 0.02`.
    This is because once we're past the grace period, the entry
    "catches up" by decaying from the original created_at (so
    the total decay over time matches what a touched entry would
    have experienced since the same anchor).

    Concretely: at age_days=5.0, decay = 5.0 * 0.02 = 0.10.

    ⚠️ SOUL-DECAY-FLAKY-AUDIT：這些斷言原本靠牆鐘，Δ 預算只有 0.216 ms。
    現在改用**注入的凍結時鐘** ⇒ Δ ≡ 0 ⇒ `places=10` 是位元精確的
    確定性斷言（容差沒有被放寬，反而是「不再靠運氣」）。
    """

    def test_untouched_beyond_grace_decay_at_0_5(self):
        """0.5 untouched entry 5 days old → 0.5 - 5*0.02 = 0.40 (decayed from created_at)."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.5, "m5_13_5_b1")
            try:
                _set_created_at_days_ago(store, other_id, 5.0, now)
                entry = store.get(other_id)
                expected = 0.5 - 5.0 * 0.02  # = 0.40
                self.assertAlmostEqual(
                    entry["confidence"], expected, places=10,
                    msg=f"5-day-old untouched 0.5 entry should decay to {expected}, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_beyond_grace_decay_at_0_9(self):
        """0.9 untouched entry 30 days old → 0.9 - 30*0.02 = 0.30 (clamped by min)."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.9, "m5_13_5_b2")
            try:
                _set_created_at_days_ago(store, other_id, 30.0, now)
                entry = store.get(other_id)
                # 30 * 0.02 = 0.60; 0.9 - 0.60 = 0.30; CONFIDENCE_MIN = 0.0
                expected = 0.9 - 30.0 * 0.02  # = 0.30
                self.assertAlmostEqual(
                    entry["confidence"], expected, places=10,
                    msg=f"30-day-old untouched 0.9 entry should decay to {expected}, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_just_over_grace_decays(self):
        """1.5 days old → 1.5 * 0.02 = 0.03 decay from 0.5 → 0.47."""
        with frozen_clock() as now:
            store, tmp, other_id = _make_real_store(0.5, "m5_13_5_b3")
            try:
                _set_created_at_days_ago(store, other_id, 1.5, now)
                entry = store.get(other_id)
                expected = 0.5 - 1.5 * 0.02  # = 0.47
                self.assertAlmostEqual(
                    entry["confidence"], expected, places=10,
                    msg=f"1.5-day-old untouched 0.5 entry should decay to {expected}, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)


# ── D. Touched entry uses last_interaction_at (not created_at) ──


class TestTouchedEntryUsesLastInteractionNotCreatedAt(unittest.TestCase):
    """
    M5.13-5: An entry that has been touched (last_interaction_at is set)
    must use last_interaction_at as the decay anchor, NOT created_at.
    This is the M5.13-4.2 behavior, which M5.13-5 does not change.
    """

    def test_touched_recently_with_old_created_at_no_decay(self):
        """
        Created 30 days ago but touched today: anchor = last_interaction_at (today),
        so no decay (days = 0).
        """
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_d1_")
        try:
            with frozen_clock() as now:
                store = RelationshipsStore(
                    agent_id="m5_13_5_d1",
                    data_dir=pathlib.Path(tmp),
                )
                store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
                # Manually set created_at to 30 days ago AND last_interaction_at to NOW
                entry = store._cache["others"][BRYAN_ENTITY_ID]
                entry["created_at"] = (now - timedelta(days=30)).isoformat()
                entry["last_interaction_at"] = now.isoformat()
                entry = store.get(BRYAN_ENTITY_ID)
                # last_interaction_at = now → days = 0 → no decay → still 0.5
                self.assertAlmostEqual(
                    entry["confidence"], 0.5, places=10,
                    msg=f"Touched entry should use last_interaction_at (no decay), got {entry['confidence']!r}",
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_touched_5_days_ago_decays_from_last_interaction(self):
        """
        Created 30 days ago, touched 5 days ago: decay from last_interaction_at
        (5 days * 0.02 = 0.10 from 0.5 → 0.40), NOT from created_at (would be 0.30).
        """
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_d2_")
        try:
            with frozen_clock() as now:
                store = RelationshipsStore(
                    agent_id="m5_13_5_d2",
                    data_dir=pathlib.Path(tmp),
                )
                store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
                entry = store._cache["others"][BRYAN_ENTITY_ID]
                entry["created_at"] = (now - timedelta(days=30)).isoformat()
                entry["last_interaction_at"] = (now - timedelta(days=5)).isoformat()
                entry = store.get(BRYAN_ENTITY_ID)
                # last_interaction_at = 5 days ago → 5 * 0.02 = 0.10 from 0.5 → 0.40
                # (NOT 30 * 0.02 = 0.60 from 0.5 → would clamp to 0.0)
                expected = 0.5 - 5.0 * 0.02
                self.assertAlmostEqual(
                    entry["confidence"], expected, places=10,
                    msg=f"Touched entry should decay from last_interaction_at (5 days, {expected}), got {entry['confidence']!r}",
                )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── E. Legacy entry without created_at → no decay, no crash ──


class TestLegacyEntryNoCreatedAtSkipped(unittest.TestCase):
    """
    M5.13-5: An entry that has neither `last_interaction_at` nor `created_at`
    (legacy from before M5.13-4.2) must be skipped (no decay, no crash).
    This is the deterministic behavior per work order §8.
    """

    def test_legacy_entry_no_decay_no_crash(self):
        """Entry without last_interaction_at and without created_at → unchanged, no exception."""
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_e1_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_5_e1",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
            # Remove both timestamps to simulate legacy entry
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            entry["last_interaction_at"] = None
            entry["created_at"] = None
            # get() should not raise; confidence should remain 0.5
            entry_after = store.get(BRYAN_ENTITY_ID)
            self.assertEqual(
                entry_after["confidence"], 0.5,
                f"Legacy entry (no timestamps) should not decay, got {entry_after['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_entry_last_interaction_none_created_at_none(self):
        """
        Variant: entry has both fields set to None explicitly. Same expected behavior:
        no decay, no crash, confidence unchanged.
        """
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_e2_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_5_e2",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.7)
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            # Forcefully set both to None (covers both string "None" and actual None)
            entry["last_interaction_at"] = None
            entry["created_at"] = None
            entry_after = store.get(BRYAN_ENTITY_ID)
            self.assertEqual(
                entry_after["confidence"], 0.7,
                f"Entry with both timestamps None should not decay, got {entry_after['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── F. Bad created_at timestamp → no crash, skip entry ──


class TestBadCreatedAtTimestampSkipped(unittest.TestCase):
    """
    M5.13-5: A malformed `created_at` (non-ISO string) must be skipped
    (no decay, no crash). This is deterministic per work order §8.
    """

    def test_bad_created_at_no_crash(self):
        """Entry with malformed created_at → no decay, no exception."""
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_f1_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_5_f1",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            # last_interaction_at is None, created_at is malformed
            entry["last_interaction_at"] = None
            entry["created_at"] = "not-a-valid-iso-timestamp"
            # get() should not raise; confidence should remain 0.5
            entry_after = store.get(BRYAN_ENTITY_ID)
            self.assertEqual(
                entry_after["confidence"], 0.5,
                f"Bad created_at should not decay, got {entry_after['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_naive_created_at_no_tzinfo_handled(self):
        """
        Naive timestamp (no tzinfo) should be treated as UTC.
        An untouched entry with naive created_at 5 days ago should decay.

        Note: we use datetime.now(timezone.utc) - timedelta(days=5) to compute
        the naive timestamp string, so the test doesn't have a TZ drift
        between the test's local time and _decay_locked's UTC reference.

        SOUL-DECAY-FLAKY-AUDIT（2026-10-19）：本支**刻意不動**。
        它用 `places=6` ⇒ 容差 0.5e-6 / 0.02 = 2.5e-5 天 = **2.16 秒**預算
        （比 places=10 的 0.216 ms 寬 10,000 倍），本來就安全。
        守衛 `test_soul_decay_flaky_audit.py` 會用程式碼算出並鎖住這個數字。
        """
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_5_f2_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_5_f2",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            entry["last_interaction_at"] = None
            # Naive datetime (no tzinfo), generated from UTC reference
            naive_past = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
            entry["created_at"] = naive_past
            entry_after = store.get(BRYAN_ENTITY_ID)
            # Should treat as UTC and decay by 5 * 0.02 = 0.10
            expected = 0.5 - 5.0 * 0.02
            self.assertAlmostEqual(
                entry_after["confidence"], expected, places=6,
                msg=f"Naive created_at should be treated as UTC, decay to {expected}, got {entry_after['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── G. Grace constant verification ──


class TestGraceConstant(unittest.TestCase):
    """
    M5.13-5: UNTOUCHED_DECAY_GRACE_DAYS = 1.0 day is the canonical grace
    period. This test verifies the constant and its semantic meaning.
    """

    def test_grace_constant_is_one_day(self):
        from src.soul.relationships import UNTOUCHED_DECAY_GRACE_DAYS
        self.assertEqual(
            UNTOUCHED_DECAY_GRACE_DAYS, 1.0,
            f"UNTOUCHED_DECAY_GRACE_DAYS must be 1.0 day (per M5.13-5 spec), got {UNTOUCHED_DECAY_GRACE_DAYS!r}",
        )

    def test_grace_preserves_m5_13_2_strict_0_3_contract(self):
        """
        The grace period exists specifically to preserve M5.13-2 strict 0.3
        contract. A 0.3 entry created within the grace must stay at 0.3.
        """
        with frozen_clock():
            store, tmp, other_id = _make_real_store(0.3, "m5_13_5_g1")
            try:
                # Set created_at to "now" (0 days ago) — fresh untouched entry
                # Should not decay within grace
                entry = store.get(other_id)
                self.assertEqual(
                    entry["confidence"], 0.3,
                    f"M5.13-2 strict 0.3 contract: ensure_relationship(0.3) → get() must return 0.3, got {entry['confidence']!r}",
                )
            finally:
                shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
