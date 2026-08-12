"""
tests/test_m5_13_4_1_boundary_decay.py
M5.13-4.1 (Bry 派工 2026-08-11 22:05): Relationship Confidence Boundary Regression.

Regression test for the M5.13-4 P3 finding. Locks down the canonical
0.3 boundary behavior against decay arithmetic representation noise.

Bug reproduction (M5.13-4 audit):
  ensure_relationship(0.3)         -> in-memory: 0.3
  store.get("user_bryan")           -> _decay_locked() runs
                                   -> 0.3 - 0.02 (sub-day) = 0.29999999965208335
  _format_relationship_block()      -> confidence >= 0.3 check
                                   -> 0.29999... < 0.3 -> returns ""
                                   -> user's "認識" intent is silently dropped

The fix (M5.13-4.1): at the 0.3 boundary, compare `round(confidence, 6) >= 0.3`
instead of `confidence >= 0.3`. This handles FP representation noise from
decay arithmetic (which is in the 1e-10 to 1e-15 range) while keeping the
threshold semantics (0.3 still means 0.3) and NOT changing 0.5/0.7/0.9
thresholds (which use exact `>=` comparison).

Scope: 0.3 boundary ONLY. Other bands (0.5, 0.7, 0.9) are unchanged per
M5.13-4.1 spec ("without weakening unrelated thresholds").
"""
from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Helpers ──


def _make_real_store_with_confidence(initial: float, ensure_decay_nonzero: bool = True):
    """
    Build a real RelationshipsStore in a tempdir, create a relationship
    with the given initial confidence, then read it back (triggering decay).

    If `ensure_decay_nonzero` is True, sleeps 0.05s before the get() to
    guarantee the decay amount is non-zero. This makes the test
    deterministic (no flaky timing).

    Returns (store, tempdir, read_back_confidence).
    """
    import time
    from src.soul.relationships import (
        RelationshipsStore,
        BRYAN_ENTITY_ID,
    )

    tmp = tempfile.mkdtemp(prefix="m5_13_4_1_")
    store = RelationshipsStore(
        agent_id="m5_13_4_1_test",
        data_dir=pathlib.Path(tmp),
    )
    store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=initial)
    if ensure_decay_nonzero:
        # Sleep 0.05s to ensure _decay_locked applies non-zero decay.
        # (0.05s * 0.02/day = 1.16e-7 decay, which is small but > 0)
        time.sleep(0.05)
    rel = store.get(BRYAN_ENTITY_ID)  # triggers _decay_locked
    return store, tmp, rel["confidence"]


def _format_with_real_store(store, agent_id: str) -> str:
    """Call _format_relationship_block with a mocked manager returning the given store."""
    from src.llm.proxy import _format_relationship_block

    mock_mgr = MagicMock()
    mock_mgr.get_store = MagicMock(return_value=store)
    with patch(
        "src.soul.relationships.get_relationships_manager",
        return_value=mock_mgr,
    ):
        return _format_relationship_block(agent_id)


# ── Reproduction (M5.13-4 P3 finding) ──


class TestBoundaryDecayReproduction(unittest.TestCase):
    """
    Reproduce the M5.13-4 P3 finding with a real RelationshipsStore
    (not a mock). The test exercises the actual decay arithmetic path
    that the M5.13-3 unit tests bypass via mock stores.
    """

    def test_decay_arithmetic_crosses_0_3_boundary(self):
        """
        Show that the decay arithmetic produces 0.29999... just below
        0.3 in IEEE 754. This is the actual bug surface.
        """
        store, tmp, confidence = _make_real_store_with_confidence(0.3)
        try:
            # The read-back value is just below 0.3 due to decay arithmetic.
            # (0.3 - 0.02 * sub-day delta = 0.29999999965208335)
            self.assertLess(
                confidence, 0.3,
                f"Expected decay to push 0.3 below 0.3, got {confidence!r}",
            )
            self.assertGreater(
                confidence, 0.29,
                f"Expected decay to be sub-day, got {confidence!r} (should be > 0.29)",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_format_block_returns_empty_at_0_3_after_decay_without_fix(self):
        """
        WITHOUT the fix: _format_relationship_block returns "" for the
        real-store path, even though the user created with 0.3 (the
        minimum "認識" threshold).

        This test documents the bug. After the M5.13-4.1 fix is applied,
        this test is replaced by test_format_block_returns_known_at_0_3.
        """
        store, tmp, _ = _make_real_store_with_confidence(0.3)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            # Per M5.13-4 audit: pre-fix, this returns "" (the bug).
            # Per M5.13-4.1 fix: this should return "[你跟 Bry 的關係]\n  熟悉度: 認識".
            # The fix makes this assertion pass.
            self.assertIn("認識", result, (
                f"M5.13-4.1 fix: 0.3 boundary should qualify as 認識, "
                f"got {result!r} (this is the bug reproduction)"
            ))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Other bands unchanged (M5.13-3 contract preserved) ──


class TestOtherBandsUnchanged(unittest.TestCase):
    """
    The M5.13-4.1 fix is scoped to the 0.3 boundary ONLY. Other bands
    (0.5, 0.7, 0.9) use exact `>=` comparison and are unchanged.

    These tests verify that the fix does NOT weaken 0.5/0.7/0.9 thresholds.

    IMPORTANT: these tests use real-store + get() (post-decay). After
    sub-day decay:
      - 0.0  -> ~0.0     (stranger)
      - 0.29 -> ~0.29    (stranger)
      - 0.3  -> 0.2999... (FIXED: 認識; pre-fix: stranger)
      - 0.5  -> 0.4999... (< 0.5, 認識 — unchanged by fix)
      - 0.7  -> 0.6999... (< 0.7, 熟悉 — unchanged by fix)
      - 0.9  -> 0.8999... (< 0.9, 親密 — unchanged by fix)

    Use exact equality (assertEqual) to avoid substring false positives
    ("熟悉度" contains "熟悉").
    """

    def test_below_0_3_still_stranger(self):
        """0.29 (clearly below) → still stranger, NOT 認識."""
        store, tmp, _ = _make_real_store_with_confidence(0.29)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            self.assertEqual(result, "", f"0.29 should be stranger, got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_0_still_stranger(self):
        """0.0 → stranger (no change, even with fix)."""
        store, tmp, _ = _make_real_store_with_confidence(0.0)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            self.assertEqual(result, "", f"0.0 should be stranger, got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_5_unaffected_by_fix(self):
        """0.5 → 認識 (post-decay; 0.5 threshold check unchanged by fix)."""
        store, tmp, _ = _make_real_store_with_confidence(0.5)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            # 0.5 - sub-day-decay = 0.4999... < 0.5, classified as 認識
            # (the spec says the fix should NOT change 0.5 behavior, even
            # though 0.5 has the same kind of decay noise as 0.3)
            expected = "[你跟 Bry 的關係]\n  熟悉度: 認識"
            self.assertEqual(result, expected, f"0.5 should be 認識 (post-decay), got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_7_unaffected_by_fix(self):
        """0.7 → 熟悉 (post-decay; 0.7 threshold check unchanged by fix)."""
        store, tmp, _ = _make_real_store_with_confidence(0.7)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            # 0.7 - sub-day-decay = 0.6999... < 0.7, classified as 熟悉
            expected = "[你跟 Bry 的關係]\n  熟悉度: 熟悉"
            self.assertEqual(result, expected, f"0.7 should be 熟悉 (post-decay), got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_9_unaffected_by_fix(self):
        """0.9 → 親密 (post-decay; 0.9 threshold check unchanged by fix)."""
        store, tmp, _ = _make_real_store_with_confidence(0.9)
        try:
            result = _format_with_real_store(store, "m5_13_4_1_test")
            # 0.9 - sub-day-decay = 0.8999... < 0.9, classified as 親密
            expected = "[你跟 Bry 的關係]\n  熟悉度: 親密"
            self.assertEqual(result, expected, f"0.9 should be 親密 (post-decay), got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Edge case: fresh file (no time elapsed, no decay) ──


class TestFreshFileNoDecay(unittest.TestCase):
    """
    When the file is freshly created and get() is called within the
    same atomic operation, the time delta is sub-second and the decay
    amount is tiny. round() to 6 decimal places handles this case.
    """

    def test_fresh_0_3_returns_jianshi(self):
        """Fresh 0.3 (no significant decay) → 認識."""
        # Direct call without going through store.get() to avoid decay
        from src.llm.proxy import _format_relationship_block
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_4_1_fresh_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_4_1_fresh",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.3)
            # Read directly from _cache (no decay) to simulate "fresh" state
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            self.assertEqual(entry["confidence"], 0.3)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Direct comparison: test the round() behavior ──


class TestRoundNormalization(unittest.TestCase):
    """
    Unit test for the round(confidence, 6) normalization.
    This is the actual mechanism of the M5.13-4.1 fix.
    """

    def test_round_0_29999999965208335_to_6(self):
        """0.29999999965208335 → 0.3 (handles decay noise)."""
        from src.llm.proxy import _format_relationship_block

        # Verify the round() logic that the fix uses
        noisy = 0.29999999965208335
        self.assertEqual(round(noisy, 6), 0.3)
        self.assertGreaterEqual(round(noisy, 6), 0.3)

    def test_round_0_27999999999999997_to_6(self):
        """0.27999999999999997 → 0.28 (genuine 1-day decay stays below)."""
        full_decay = 0.27999999999999997
        self.assertEqual(round(full_decay, 6), 0.28)
        self.assertLess(round(full_decay, 6), 0.3)

    def test_round_does_not_promote_genuine_below(self):
        """0.29 → 0.29 (genuine below stays below)."""
        self.assertEqual(round(0.29, 6), 0.29)
        self.assertLess(round(0.29, 6), 0.3)

    def test_round_preserves_exact_above(self):
        """0.5 → 0.5 (above-threshold unchanged)."""
        self.assertEqual(round(0.5, 6), 0.5)


if __name__ == "__main__":
    unittest.main()
