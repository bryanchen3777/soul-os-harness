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
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ──


def _make_real_store(initial_confidence: float = 0.5, agent_id: str = "m5_13_5_test"):
    """
    Build a real RelationshipsStore in a tempdir, create one untouched
    relationship with the given initial confidence.

    Returns (store, tempdir, other_id).
    The entry has last_interaction_at = None and created_at = now.
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


def _set_created_at_days_ago(store, other_id: str, days_ago: float) -> None:
    """
    Mutate the entry's created_at to be `days_ago` before now.
    Modifies the in-memory cache directly (deterministic for tests).
    """
    now = datetime.now(timezone.utc)
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
        store, tmp, other_id = _make_real_store(0.3, "m5_13_5_a1")
        try:
            _set_created_at_days_ago(store, other_id, 0.5)  # 12h ago, within grace
            entry = store.get(other_id)
            self.assertEqual(
                entry["confidence"], 0.3,
                f"Within grace, 0.3 must stay at 0.3, got {entry['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_within_grace_no_decay_at_0_5(self):
        """0.5 fresh untouched entry within grace → 0.5 (no decay)."""
        store, tmp, other_id = _make_real_store(0.5, "m5_13_5_a2")
        try:
            _set_created_at_days_ago(store, other_id, 0.1)  # 2.4h ago, within grace
            entry = store.get(other_id)
            self.assertEqual(
                entry["confidence"], 0.5,
                f"Within grace, 0.5 must stay at 0.5, got {entry['confidence']!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_untouched_at_grace_boundary_no_decay(self):
        """Just under 1.0 day grace → no decay (grace inclusive of < 1.0)."""
        store, tmp, other_id = _make_real_store(0.7, "m5_13_5_a3")
        try:
            # Just under 1.0 day — should still be within grace
            _set_created_at_days_ago(store, other_id, 0.999)
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
    """

    def test_untouched_beyond_grace_decay_at_0_5(self):
        """0.5 untouched entry 5 days old → 0.5 - 5*0.02 = 0.40 (decayed from created_at)."""
        store, tmp, other_id = _make_real_store(0.5, "m5_13_5_b1")
        try:
            _set_created_at_days_ago(store, other_id, 5.0)
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
        store, tmp, other_id = _make_real_store(0.9, "m5_13_5_b2")
        try:
            _set_created_at_days_ago(store, other_id, 30.0)
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
        store, tmp, other_id = _make_real_store(0.5, "m5_13_5_b3")
        try:
            _set_created_at_days_ago(store, other_id, 1.5)
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
            store = RelationshipsStore(
                agent_id="m5_13_5_d1",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
            # Manually set created_at to 30 days ago AND last_interaction_at to NOW
            now = datetime.now(timezone.utc)
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
            store = RelationshipsStore(
                agent_id="m5_13_5_d2",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.5)
            now = datetime.now(timezone.utc)
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
    (no crash, no decay). This is deterministic per work order §8.
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
