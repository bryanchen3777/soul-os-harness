"""
tests/test_m5_13_4_1_boundary_decay.py
M5.13-4.1 (Bry 派工 2026-08-11 22:05): Relationship Confidence Boundary Regression.
M5.13-4.2 (Bry 派工 2026-08-11 22:40): Reverted M5.13-4.1 consumer fix;
producer-side per-entry decay anchor.

Regression test for the M5.13-4 P3 finding. Locks down the canonical
0.3 boundary behavior against decay arithmetic representation noise.

Bug reproduction (M5.13-4 audit):
  ensure_relationship(0.3)
  -> store.get()
  -> _decay_locked() applies 0.02/day decay
  -> 0.29999999965208335 (genuinely < 0.3 in IEEE 754)
  -> _format_relationship_block checks `>= 0.3` -> False
  -> returns "" (stranger)
  -> user's "認識" intent is silently dropped

Original fix (M5.13-4.1, REVERTED):
  `round(confidence, 6) >= 0.3` in _format_relationship_block
  PROBLEM: introduced 5e-7 false-promotion range [(0.2999995, 0.3) → 認識]
  - violates strict M5.13-2 contract: `confidence < 0.3 → 陌生人`

Replacement (M5.13-4.2):
  Producer-side fix in RelationshipsStore._decay_locked:
  Use per-entry decay anchor (last_interaction_at or created_at).
  For freshly-created entries, anchor = created_at = now, so first
  decay produces 0 (no FP noise). Subsequent reads apply decay based
  on the per-entry anchor (matches spec "decay if no interaction").
  No false-promotion range. Strict contract preserved.

Scope: 0.3 boundary AND all other bands (0.5/0.7/0.9) all return to
their pre-decay classifications because the per-entry anchor is
"now" for freshly-created entries.
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


def _make_real_store_with_confidence(initial: float):
    """
    Build a real RelationshipsStore in a tempdir, create a relationship
    with the given initial confidence, then read it back.

    M5.13-4.2: freshly-created entries are NOT subject to decay on the
    first read (per-entry anchor = created_at = now). So the
    read-back confidence should be exactly `initial` (no decay).

    Returns (store, tempdir, read_back_confidence).
    """
    from src.soul.relationships import (
        RelationshipsStore,
        BRYAN_ENTITY_ID,
    )

    tmp = tempfile.mkdtemp(prefix="m5_13_4_2_")
    store = RelationshipsStore(
        agent_id="m5_13_4_2_test",
        data_dir=pathlib.Path(tmp),
    )
    store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=initial)
    rel = store.get(BRYAN_ENTITY_ID)
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


# ── 0.3 boundary: bug fixed, strict semantics preserved ──


class TestBoundaryDecayReproduction(unittest.TestCase):
    """
    M5.13-4.2: 0.3 boundary regression test.

    The fix moves decay handling to the producer (per-entry anchor).
    For freshly-created entries, the read-back confidence is exactly
    the initial value (no decay applied). This fixes the bug WITHOUT
    introducing a false-promotion range.
    """

    def test_0_3_freshly_created_stays_at_0_3(self):
        """Producer fix: ensure_relationship(0.3) + get() → confidence stays at 0.3."""
        store, tmp, confidence = _make_real_store_with_confidence(0.3)
        try:
            self.assertEqual(
                confidence, 0.3,
                f"Producer fix: 0.3 boundary should stay at 0.3, got {confidence!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_format_block_returns_jianshi_at_0_3(self):
        """Consumer behavior: 0.3 → 認識 (strict M5.13-2 contract preserved)."""
        store, tmp, _ = _make_real_store_with_confidence(0.3)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            expected = "[你跟 Bry 的關係]\n  熟悉度: 認識"
            self.assertEqual(
                result, expected,
                f"0.3 should be 認識 (strict contract), got {result!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Other bands: NO false-promotion, NO false-demotion ──


class TestOtherBandsUnchanged(unittest.TestCase):
    """
    M5.13-4.2: per-entry anchor means freshly-created entries are NOT
    decayed. So 0.5, 0.7, 0.9 produce their direct band (no decay-induced
    demotion). Other bands (0.5/0.7/0.9) are unchanged from the M5.13-3
    contract: strict `>=` comparison.
    """

    def test_below_0_3_still_stranger(self):
        """0.29 (genuine below) → stranger (no promotion)."""
        store, tmp, _ = _make_real_store_with_confidence(0.29)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            self.assertEqual(result, "", f"0.29 should be stranger, got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_0_still_stranger(self):
        """0.0 → stranger."""
        store, tmp, _ = _make_real_store_with_confidence(0.0)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            self.assertEqual(result, "", f"0.0 should be stranger, got {result!r}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_5_returns_shuxi(self):
        """0.5 → 熟悉 (per-entry anchor preserves 0.5; strict >= 0.5)."""
        store, tmp, _ = _make_real_store_with_confidence(0.5)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            expected = "[你跟 Bry 的關係]\n  熟悉度: 熟悉"
            self.assertEqual(
                result, expected, f"0.5 should be 熟悉 (per-entry anchor), got {result!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_7_returns_qinmi(self):
        """0.7 → 親密."""
        store, tmp, _ = _make_real_store_with_confidence(0.7)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            expected = "[你跟 Bry 的關係]\n  熟悉度: 親密"
            self.assertEqual(
                result, expected, f"0.7 should be 親密 (per-entry anchor), got {result!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_9_returns_deep_trust(self):
        """0.9 → 深度信任."""
        store, tmp, _ = _make_real_store_with_confidence(0.9)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            expected = "[你跟 Bry 的關係]\n  熟悉度: 深度信任"
            self.assertEqual(
                result, expected, f"0.9 should be 深度信任 (per-entry anchor), got {result!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_0_3000001_returns_jianshi(self):
        """0.3000001 → 認識 (just above boundary, still 認識)."""
        store, tmp, _ = _make_real_store_with_confidence(0.3000001)
        try:
            result = _format_with_real_store(store, "m5_13_4_2_test")
            expected = "[你跟 Bry 的關係]\n  熟悉度: 認識"
            self.assertEqual(
                result, expected, f"0.3000001 should be 認識, got {result!r}",
            )
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Edge case: fresh file (no time elapsed, no decay) ──


class TestFreshFileNoDecay(unittest.TestCase):
    """Direct cache read returns the initial value (no decay)."""

    def test_fresh_0_3_cache_read(self):
        """ensure_relationship(0.3) → _cache["others"][...] → confidence = 0.3."""
        from src.soul.relationships import RelationshipsStore, BRYAN_ENTITY_ID

        tmp = tempfile.mkdtemp(prefix="m5_13_4_2_fresh_")
        try:
            store = RelationshipsStore(
                agent_id="m5_13_4_2_fresh",
                data_dir=pathlib.Path(tmp),
            )
            store.ensure_relationship(BRYAN_ENTITY_ID, initial_confidence=0.3)
            entry = store._cache["others"][BRYAN_ENTITY_ID]
            self.assertEqual(entry["confidence"], 0.3)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ── Direct comparison: Python round() behavior (still valid for reference) ──


class TestRoundNormalization(unittest.TestCase):
    """
    Reference tests for Python's round() behavior.
    Note: M5.13-4.2 NO LONGER uses round() in _format_relationship_block.
    These tests are kept as documentation of the previous mechanism.
    """

    def test_round_0_29999999965208335_to_6(self):
        """0.29999999965208335 → 0.3 (handles decay noise)."""
        noisy = 0.29999999965208335
        self.assertEqual(round(noisy, 6), 0.3)

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
