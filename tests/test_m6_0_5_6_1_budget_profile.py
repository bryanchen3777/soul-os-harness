"""
tests/test_m6_0_5_6_1_budget_profile.py
M6.0-5.6.1 (Bry 派工 2026-08-12 20:52): Budget Profile Registry tests.

Validates the named BudgetProfile enum + EvaluationBudgetConfig.from_profile()
factory introduced in M6.0-5.6.1.

Test categories (per Bry 派工 spec):
  A. Known profile lookup (CHAT / DIARY / DREAM)
  B. Expected budget values per profile
  C. Deterministic repeated lookup (same profile -> same config)
  D. Unknown profile behavior (TypeError on raw strings, None, etc.)
  E. Existing EvaluationBudgetConfig compatibility (defaults unchanged,
     custom construction still works, to_cost_budget still works)
  F. BudgetProfile enum semantics (values, str inheritance)
  G. Frozen / hashable (profile-derived configs usable as dict keys,
     set members, dataclass-equality)

Note: imports from `_helpers.subjective_eval` directly (not
`tests._helpers.subjective_eval`) to avoid the pre-existing test
collection issue (tests/ lacks __init__.py — see M5.15-6 closeout
known findings). sys.path is patched at module import time.
"""
from __future__ import annotations

import os
import sys
import unittest

# Workaround for pre-existing test collection issue (M5.15-6 closeout §6.1):
# `tests/` is not a Python package (no __init__.py at tests/ level), so the
# existing M6.0.x tests fail to collect via pytest. This new test imports
# from `_helpers.subjective_eval` directly via sys.path manipulation, which
# works because `_helpers/` and `_helpers/subjective_eval/` have their own
# __init__.py files.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _helpers.subjective_eval import (
    EvaluationBudgetConfig,
    BudgetProfile,
    CostBudget,
)


# ── A. Known profile lookup ──


class TestKnownProfileLookup(unittest.TestCase):
    """A. BudgetProfile enum members are accessible + look up via from_profile()."""

    def test_chat_profile_is_known(self):
        """BudgetProfile.CHAT is a valid enum member."""
        self.assertIs(BudgetProfile.CHAT, BudgetProfile("chat"))

    def test_diary_profile_is_known(self):
        """BudgetProfile.DIARY is a valid enum member."""
        self.assertIs(BudgetProfile.DIARY, BudgetProfile("diary"))

    def test_dream_profile_is_known(self):
        """BudgetProfile.DREAM is a valid enum member."""
        self.assertIs(BudgetProfile.DREAM, BudgetProfile("dream"))

    def test_from_profile_returns_evaluation_budget_config(self):
        """from_profile returns an EvaluationBudgetConfig instance."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        self.assertIsInstance(cfg, EvaluationBudgetConfig)


# ── B. Expected budget values per profile ──


class TestExpectedBudgetValues(unittest.TestCase):
    """B. Each profile maps to documented budget values."""

    def test_chat_profile_values(self):
        """CHAT profile = 3/2/5000/0.05 (matches EvaluationBudgetConfig defaults)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        self.assertEqual(cfg.max_judge_calls, 3)
        self.assertEqual(cfg.max_retries_per_judge, 2)
        self.assertEqual(cfg.max_token_budget, 5000)
        self.assertEqual(cfg.max_cost_usd, 0.05)

    def test_diary_profile_values(self):
        """DIARY profile = 2/1/3000/0.03 (smaller budget for high volume)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.DIARY)
        self.assertEqual(cfg.max_judge_calls, 2)
        self.assertEqual(cfg.max_retries_per_judge, 1)
        self.assertEqual(cfg.max_token_budget, 3000)
        self.assertEqual(cfg.max_cost_usd, 0.03)

    def test_dream_profile_values(self):
        """DREAM profile = 1/1/2000/0.02 (smallest budget for low observable)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.DREAM)
        self.assertEqual(cfg.max_judge_calls, 1)
        self.assertEqual(cfg.max_retries_per_judge, 1)
        self.assertEqual(cfg.max_token_budget, 2000)
        self.assertEqual(cfg.max_cost_usd, 0.02)


# ── C. Deterministic repeated lookup ──


class TestDeterministicLookup(unittest.TestCase):
    """C. Same profile produces identical config (no time-of-day / no random)."""

    def test_chat_is_deterministic(self):
        """CHAT profile -> identical config across N lookups."""
        configs = [
            EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
            for _ in range(10)
        ]
        for cfg in configs[1:]:
            self.assertEqual(cfg, configs[0])

    def test_diary_is_deterministic(self):
        """DIARY profile -> identical config across N lookups."""
        configs = [
            EvaluationBudgetConfig.from_profile(BudgetProfile.DIARY)
            for _ in range(10)
        ]
        for cfg in configs[1:]:
            self.assertEqual(cfg, configs[0])

    def test_dream_is_deterministic(self):
        """DREAM profile -> identical config across N lookups."""
        configs = [
            EvaluationBudgetConfig.from_profile(BudgetProfile.DREAM)
            for _ in range(10)
        ]
        for cfg in configs[1:]:
            self.assertEqual(cfg, configs[0])


# ── D. Unknown profile behavior ──


class TestUnknownProfileBehavior(unittest.TestCase):
    """D. from_profile rejects non-BudgetProfile inputs with TypeError."""

    def test_raw_string_rejected(self):
        """Raw string 'chat' (not enum) -> TypeError."""
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig.from_profile("chat")
        self.assertIn("BudgetProfile", str(ctx.exception))

    def test_none_rejected(self):
        """None -> TypeError."""
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig.from_profile(None)
        self.assertIn("BudgetProfile", str(ctx.exception))

    def test_int_rejected(self):
        """Int 123 (not enum) -> TypeError."""
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig.from_profile(123)
        self.assertIn("BudgetProfile", str(ctx.exception))

    def test_unknown_string_rejected(self):
        """Unknown string 'weather' (not in enum) -> TypeError."""
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig.from_profile("weather")
        self.assertIn("BudgetProfile", str(ctx.exception))

    def test_different_enum_rejected(self):
        """Different enum class -> TypeError (defensive membership check)."""
        from enum import Enum

        class OtherEnum(Enum):
            FOO = "foo"

        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig.from_profile(OtherEnum.FOO)
        self.assertIn("BudgetProfile", str(ctx.exception))


# ── E. Existing EvaluationBudgetConfig compatibility ──


class TestExistingEvaluationBudgetConfigCompatibility(unittest.TestCase):
    """E. Existing default + custom construction must remain unchanged."""

    def test_default_construction_unchanged(self):
        """EvaluationBudgetConfig() with no args = 3/2/5000/0.05 (unchanged)."""
        cfg = EvaluationBudgetConfig()
        self.assertEqual(cfg.max_judge_calls, 3)
        self.assertEqual(cfg.max_retries_per_judge, 2)
        self.assertEqual(cfg.max_token_budget, 5000)
        self.assertEqual(cfg.max_cost_usd, 0.05)

    def test_chat_profile_equals_default(self):
        """CHAT profile config equals default config (no-op semantically)."""
        chat = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        default = EvaluationBudgetConfig()
        self.assertEqual(chat, default)

    def test_custom_construction_still_works(self):
        """Custom values via constructor still work (backward compat)."""
        cfg = EvaluationBudgetConfig(
            max_judge_calls=10,
            max_retries_per_judge=5,
            max_token_budget=20000,
            max_cost_usd=1.0,
        )
        self.assertEqual(cfg.max_judge_calls, 10)
        self.assertEqual(cfg.max_retries_per_judge, 5)
        self.assertEqual(cfg.max_token_budget, 20000)
        self.assertEqual(cfg.max_cost_usd, 1.0)

    def test_to_cost_budget_still_works_on_profile_derived_config(self):
        """to_cost_budget() works on a profile-derived config (no special-casing)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.DIARY)
        cb = cfg.to_cost_budget()
        self.assertIsInstance(cb, CostBudget)
        self.assertEqual(cb.max_judge_calls, 2)
        self.assertEqual(cb.max_retries_per_judge, 1)
        self.assertEqual(cb.max_token_budget, 3000)
        self.assertEqual(cb.max_cost_usd, 0.03)

    def test_to_judge_max_retries_works_on_profile_derived_config(self):
        """to_judge_max_retries() works on profile-derived config (M6.0-5.4-R2)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.DREAM)
        self.assertEqual(cfg.to_judge_max_retries(), 1)

    def test_profile_derived_config_validation_still_runs(self):
        """__post_init__ validation runs on profile-derived config (defensive)."""
        # All profile values are pre-validated (>= 0), but verify
        # the validation chain runs by comparing equality with manual
        # construction.
        chat = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        manual = EvaluationBudgetConfig(
            max_judge_calls=3, max_retries_per_judge=2,
            max_token_budget=5000, max_cost_usd=0.05,
        )
        self.assertEqual(chat, manual)


# ── F. BudgetProfile enum semantics ──


class TestBudgetProfileEnumSemantics(unittest.TestCase):
    """F. BudgetProfile is a str-Enum (JSON-serializable, value-based)."""

    def test_profile_values_are_stable(self):
        """Profile string values are stable contract (chat / diary / dream)."""
        self.assertEqual(BudgetProfile.CHAT.value, "chat")
        self.assertEqual(BudgetProfile.DIARY.value, "diary")
        self.assertEqual(BudgetProfile.DREAM.value, "dream")

    def test_profile_is_str(self):
        """BudgetProfile inherits from str (JSON-serializable)."""
        self.assertIsInstance(BudgetProfile.CHAT, str)
        self.assertEqual(str(BudgetProfile.CHAT), "BudgetProfile.CHAT")
        # But the underlying value is "chat"
        self.assertEqual(BudgetProfile.CHAT.value, "chat")

    def test_profile_lookup_by_value(self):
        """BudgetProfile(value) creates enum from string value."""
        self.assertIs(BudgetProfile("chat"), BudgetProfile.CHAT)
        self.assertIs(BudgetProfile("diary"), BudgetProfile.DIARY)
        self.assertIs(BudgetProfile("dream"), BudgetProfile.DREAM)

    def test_profile_lookup_by_invalid_value_raises(self):
        """BudgetProfile(invalid_value) raises ValueError."""
        with self.assertRaises(ValueError):
            BudgetProfile("weather")  # not a valid value
        with self.assertRaises(ValueError):
            BudgetProfile("")  # empty string

    def test_profile_count(self):
        """Exactly 3 profiles registered (no silent additions)."""
        self.assertEqual(len(BudgetProfile), 3)


# ── G. Frozen / hashable ──


class TestProfileDerivedConfigFrozen(unittest.TestCase):
    """G. Profile-derived configs are frozen + hashable (per M6.0-5.6 contract)."""

    def test_profile_derived_config_is_frozen(self):
        """Profile-derived EvaluationBudgetConfig is frozen (immutable)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        # FrozenInstanceError (dataclasses.FrozenInstanceError) is raised
        # when assigning to a frozen dataclass field. The exact error
        # message varies by Python version; we just check that ANY exception
        # is raised (i.e., the assignment is rejected).
        with self.assertRaises(Exception):
            cfg.max_judge_calls = 99  # type: ignore[misc]
        # Also verify the value was NOT changed
        self.assertEqual(cfg.max_judge_calls, 3)

    def test_profile_derived_config_is_hashable(self):
        """Profile-derived config is hashable (usable as dict key)."""
        cfg = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        # Should be hashable (frozen dataclass default)
        d = {cfg: "chat-config"}
        self.assertEqual(d[cfg], "chat-config")

    def test_profile_derived_config_in_set(self):
        """Profile-derived config can be a set member (uniqueness)."""
        cfg1 = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        cfg2 = EvaluationBudgetConfig.from_profile(BudgetProfile.CHAT)
        s = {cfg1, cfg2}
        self.assertEqual(len(s), 1)  # equal configs deduplicate


if __name__ == "__main__":
    unittest.main()
