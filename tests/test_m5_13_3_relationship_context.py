"""
tests/test_m5_13_3_relationship_context.py
M5.13-3 (Bry 派工 2026-08-11): Minimal Relationship Context Integration

驗收 (per M5.13-2 派工 spec):
  A. _format_relationship_block helper exists, deterministic, bounded
  B. Agent-scoped (only THIS agent's relationship)
  C. Bry-scoped (only BRYAN_ENTITY_ID)
  D. Confidence-only (no feeling, no impression, no interaction_count, no timestamps)
  E. Band thresholds: <0.3 skip, 0.3+ 認識, 0.5+ 熟悉, 0.7+ 親密, 0.9+ 深度信任
  F. Missing relationship → empty string
  G. Malformed confidence → empty string
  H. Raw float never leaks into output
  I. Fail-silent (store exception → empty string, no crash)
  J. Both _build_messages_group and _build_messages_private wired
  K. Existing context blocks unchanged (only new block appended)
  L. Frozen contracts: 0 change
  M. group/private both produce the same relationship block for same agent

不測:
  - LLM actual output (no real LLM call)
  - Relationship write logic (M5.13-3 is read-only)
  - Other agents' relationships with each other (M5.13-3 is Bry-only)
  - Stage 4.3 impression generation (out of scope)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _make_mock_store(confidence_value=None, has_relationship=True,
                     confidence_type="float", relationship_id="user_bryan"):
    """
    Build a mock MultiAgentRelationshipsManager that returns
    a mock store with the given confidence value.
    """
    mock_manager = MagicMock()
    mock_store = MagicMock()
    if has_relationship and confidence_value is not None:
        if confidence_type == "float":
            entry = {
                "impression": "I love Bry",  # should NOT leak
                "feeling": "deeply attached",  # should NOT leak
                "confidence": confidence_value,
                "interaction_count": 999,  # should NOT leak
                "last_interaction_at": "2026-08-11T10:00:00Z",  # should NOT leak
                "last_updated": "2026-08-11T10:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
            }
        elif confidence_type == "missing":
            entry = {
                "impression": "",
                "feeling": "neutral",
                "interaction_count": 0,
                "last_interaction_at": None,
                "last_updated": "2026-08-11T10:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
            }
        elif confidence_type == "string":
            entry = {
                "impression": "",
                "feeling": "neutral",
                "confidence": "not a number",
                "interaction_count": 0,
                "last_interaction_at": None,
                "last_updated": "2026-08-11T10:00:00Z",
                "created_at": "2026-01-01T00:00:00Z",
            }
        else:
            entry = None
        mock_store.get.return_value = entry
    else:
        mock_store.get.return_value = None
    mock_manager.get_store.return_value = mock_store
    return mock_manager


# ── Section A: Helper function exists + deterministic + bounded ──


class TestHelperFunctionExists(unittest.TestCase):
    """A. _format_relationship_block exists, has correct signature"""

    def test_helper_function_exists(self):
        from src.llm.proxy import _format_relationship_block
        self.assertTrue(callable(_format_relationship_block))

    def test_helper_signature_accepts_agent_id(self):
        import inspect
        from src.llm.proxy import _format_relationship_block
        sig = inspect.signature(_format_relationship_block)
        params = list(sig.parameters.keys())
        self.assertEqual(params[0], "agent_id")
        self.assertEqual(sig.return_annotation, str)


# ── Section B: Agent-scoped / Bry-scoped ──


class TestAgentAndBryScoped(unittest.TestCase):
    """B/C. Per-agent + per-target filter (only THIS agent's relationship with Bry)"""

    def test_helper_uses_BRYAN_ENTITY_ID(self):
        """Helper should query BRYAN_ENTITY_ID regardless of agent_id"""
        from src.llm.proxy import _format_relationship_block
        from src.soul.relationships import BRYAN_ENTITY_ID
        mock_mgr = _make_mock_store(confidence_value=0.85)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            _format_relationship_block("agent_yua")

        # Verify the store.get was called with BRYAN_ENTITY_ID
        mock_mgr.get_store.assert_called_with("agent_yua")
        mock_store = mock_mgr.get_store.return_value
        mock_store.get.assert_called_with(BRYAN_ENTITY_ID)

    def test_agent_isolation_different_agents(self):
        """Different agent_ids use their own relationship state"""
        from src.llm.proxy import _format_relationship_block
        # Build manager that returns different values for different agent_ids
        mock_mgr = MagicMock()

        def make_store_for(agent_id):
            store = MagicMock()
            # agent_yua: 0.85 (親密); agent_ruka: 0.4 (認識)
            if agent_id == "agent_yua":
                store.get.return_value = {
                    "confidence": 0.85, "feeling": "neutral", "impression": "",
                    "interaction_count": 0, "last_interaction_at": None,
                    "last_updated": "2026-08-11", "created_at": "2026-01-01",
                }
            elif agent_id == "agent_ruka":
                store.get.return_value = {
                    "confidence": 0.4, "feeling": "neutral", "impression": "",
                    "interaction_count": 0, "last_interaction_at": None,
                    "last_updated": "2026-08-11", "created_at": "2026-01-01",
                }
            return store
        mock_mgr.get_store.side_effect = make_store_for

        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            yua_result = _format_relationship_block("agent_yua")
            ruka_result = _format_relationship_block("agent_ruka")

        self.assertIn("親密", yua_result)
        self.assertIn("認識", ruka_result)
        self.assertNotEqual(yua_result, ruka_result)

    def test_unrelated_agent_returns_empty(self):
        """Agent with no relationship with Bry returns empty"""
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(has_relationship=False)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_unknown")
        self.assertEqual(result, "")


# ── Section C: Band thresholds ──


class TestBandThresholds(unittest.TestCase):
    """E. Band mapping: <0.3 skip, 0.3+ 認識, 0.5+ 熟悉, 0.7+ 親密, 0.9+ 深度信任"""

    def _format(self, agent_id, confidence):
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_value=confidence)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            return _format_relationship_block(agent_id)

    def test_below_threshold_returns_empty(self):
        for c in [0.0, 0.1, 0.29]:
            with self.subTest(confidence=c):
                result = self._format(f"agent_below_{c}", c)
                self.assertEqual(result, "", f"confidence={c} should skip")

    def test_jian_shi_band(self):
        for c in [0.3, 0.4, 0.499]:
            with self.subTest(confidence=c):
                result = self._format(f"agent_jianshi_{c}", c)
                # Use exact equality to avoid substring false positives
                # ("熟悉度" contains "熟悉" as substring)
                self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 認識")

    def test_shuxi_band(self):
        for c in [0.5, 0.6, 0.699]:
            with self.subTest(confidence=c):
                result = self._format(f"agent_shuxi_{c}", c)
                self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 熟悉")

    def test_qinmi_band(self):
        for c in [0.7, 0.8, 0.899]:
            with self.subTest(confidence=c):
                result = self._format(f"agent_qinmi_{c}", c)
                self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 親密")

    def test_deep_trust_band(self):
        for c in [0.9, 0.95, 1.0]:
            with self.subTest(confidence=c):
                result = self._format(f"agent_deep_{c}", c)
                self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 深度信任")

    def test_deterministic_output_same_input(self):
        """Same confidence must produce same output (no randomness)"""
        result1 = self._format("agent_det_a", 0.75)
        result2 = self._format("agent_det_b", 0.75)
        self.assertEqual(result1, result2)
        self.assertEqual(result1, "[你跟 Bry 的關係]\n  熟悉度: 親密")


# ── Section D: Fail-silent + no raw float leak ──


class TestFailSilentAndNoLeak(unittest.TestCase):
    """F/G/H/I. Malformed/missing data fail-safe, raw float never leaks"""

    def test_no_raw_float_in_output(self):
        """Raw confidence (0.85) must NOT appear in output, only band label"""
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_value=0.85)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_float")
        self.assertNotIn("0.85", result)
        self.assertNotIn(".85", result)
        self.assertIn("親密", result)
        # Output should be exactly the expected band block
        self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 親密")

    def test_missing_confidence_returns_empty(self):
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_type="missing")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_no_conf")
        self.assertEqual(result, "")

    def test_non_numeric_confidence_returns_empty(self):
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_type="string")
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_bad_conf")
        self.assertEqual(result, "")

    def test_out_of_range_confidence_clamped_high(self):
        """confidence > 1.0 is clamped to 1.0 → 深度信任"""
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_value=5.0)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_clamp_high")
        # Clamped to 1.0 → 深度信任
        self.assertIn("深度信任", result)
        # Raw 5.0 should NOT leak
        self.assertNotIn("5.0", result)

    def test_negative_confidence_clamped_low(self):
        """confidence < 0.0 is clamped to 0.0 → 陌生人, skip"""
        from src.llm.proxy import _format_relationship_block
        mock_mgr = _make_mock_store(confidence_value=-0.5)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_neg")
        self.assertEqual(result, "")

    def test_store_exception_returns_empty(self):
        """If get_relationships_manager raises, fail-silent and return empty"""
        from src.llm.proxy import _format_relationship_block
        with patch(
            "src.soul.relationships.get_relationships_manager",
            side_effect=RuntimeError("store failure"),
        ):
            result = _format_relationship_block("agent_store_fail")
        self.assertEqual(result, "")

    def test_store_get_raises_returns_empty(self):
        """If store.get raises, fail-silent and return empty"""
        from src.llm.proxy import _format_relationship_block
        mock_mgr = MagicMock()
        mock_store = MagicMock()
        mock_store.get.side_effect = RuntimeError("disk failure")
        mock_mgr.get_store.return_value = mock_store
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_disk_fail")
        self.assertEqual(result, "")

    def test_empty_agent_id_returns_empty(self):
        from src.llm.proxy import _format_relationship_block
        for bad_id in ["", None, 123, []]:
            with self.subTest(agent_id=bad_id):
                result = _format_relationship_block(bad_id)
                self.assertEqual(result, "")

    def test_no_feeling_or_impression_in_output(self):
        """Feeling and impression must NOT appear in projection output"""
        from src.llm.proxy import _format_relationship_block
        # Default _make_mock_store includes "I love Bry" impression and "deeply attached" feeling
        mock_mgr = _make_mock_store(confidence_value=0.85)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            result = _format_relationship_block("agent_privacy")
        # All these must NOT appear
        self.assertNotIn("I love Bry", result)
        self.assertNotIn("deeply", result)
        self.assertNotIn("attached", result)
        self.assertNotIn("999", result)  # interaction_count
        self.assertNotIn("2026-08-11", result)  # timestamp
        self.assertNotIn("interaction", result)
        self.assertNotIn("impression", result)
        self.assertNotIn("feeling", result)
        # Only the band label
        self.assertEqual(result, "[你跟 Bry 的關係]\n  熟悉度: 親密")


# ── Section E: Integration with _build_messages_* ──


def _make_mock_memory():
    """Mock memory object that satisfies _build_messages_* expectations"""
    m = MagicMock()
    m.get_group_history.return_value = []  # empty group history
    m.get_recent_with_meta.return_value = []  # empty private history
    return m


class TestInjectionIntoBuildMessages(unittest.TestCase):
    """J/K/M. Both _build_messages_group and _build_messages_private wired correctly"""

    def test_group_path_includes_relationship_block(self):
        from src.llm.proxy import _build_messages_group
        mock_mgr = _make_mock_store(confidence_value=0.85)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            messages = _build_messages_group(
                agent_id="agent_yua",
                soul="你是 Yua。",
                current_input="hi",
                memory_context="",
                memory=_make_mock_memory(),
            )
        sys_content = messages[0]["content"]
        self.assertIn("你跟 Bry 的關係", sys_content)
        self.assertIn("親密", sys_content)

    def test_private_path_includes_relationship_block(self):
        from src.llm.proxy import _build_messages_private
        mock_mgr = _make_mock_store(confidence_value=0.85)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            messages = _build_messages_private(
                agent_id="agent_yua",
                soul="你是 Yua。",
                current_input="hi",
                memory_context="",
                memory=_make_mock_memory(),
            )
        sys_content = messages[0]["content"]
        self.assertIn("你跟 Bry 的關係", sys_content)
        self.assertIn("親密", sys_content)

    def test_group_private_same_relationship_block(self):
        """Group and private should produce SAME relationship block for same agent"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        mock_mgr = _make_mock_store(confidence_value=0.4)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            group_msgs = _build_messages_group(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )
            private_msgs = _build_messages_private(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )

        import re
        rel_re = re.compile(r"\[你跟 Bry 的關係\][^\n]*\n[^\n]*熟悉度: \S+")
        group_match = rel_re.search(group_msgs[0]["content"])
        private_match = rel_re.search(private_msgs[0]["content"])

        self.assertIsNotNone(group_match, "group should have relationship block")
        self.assertIsNotNone(private_match, "private should have relationship block")
        self.assertEqual(group_match.group(), private_match.group())

    def test_no_relationship_block_when_below_threshold(self):
        """When confidence < 0.3, NO relationship block in either path"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        mock_mgr = _make_mock_store(confidence_value=0.1)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            group_msgs = _build_messages_group(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )
            private_msgs = _build_messages_private(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )

        self.assertNotIn("你跟 Bry 的關係", group_msgs[0]["content"])
        self.assertNotIn("你跟 Bry 的關係", private_msgs[0]["content"])

    def test_no_relationship_block_when_no_relationship(self):
        """When store returns None, no relationship block injected"""
        from src.llm.proxy import _build_messages_group, _build_messages_private
        mock_mgr = _make_mock_store(has_relationship=False)
        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            group_msgs = _build_messages_group(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )
            private_msgs = _build_messages_private(
                agent_id="agent_yua", soul="你是 Yua。",
                current_input="hi", memory_context="", memory=_make_mock_memory(),
            )

        self.assertNotIn("你跟 Bry 的關係", group_msgs[0]["content"])
        self.assertNotIn("你跟 Bry 的關係", private_msgs[0]["content"])


# ── Section F: Frozen contracts unchanged ──


class TestFrozenContractsUnchanged(unittest.TestCase):
    """L. Verify frozen contracts are 0 change"""

    def test_relationships_store_untouched(self):
        """RelationshipsStore API surface unchanged"""
        from src.soul.relationships import RelationshipsStore
        expected = ["get", "get_all", "ensure_relationship", "update_impression", "touch"]
        for m in expected:
            self.assertTrue(
                hasattr(RelationshipsStore, m),
                f"RelationshipsStore.{m} missing — frozen contract broken",
            )

    def test_relationships_schema_untouched(self):
        """Relationship entry schema unchanged"""
        from src.soul.relationships import _new_relationship_entry
        entry = _new_relationship_entry(
            other_id="user_bryan", impression="", feeling="neutral",
            confidence=0.5, interaction_count=0, last_interaction_at=None,
        )
        for key in ["impression", "feeling", "confidence",
                    "interaction_count", "last_interaction_at", "last_updated"]:
            self.assertIn(key, entry, f"Schema field '{key}' missing")

    def test_band_constants_match_spec(self):
        """Verify the band threshold constants match M5.13-2 design"""
        from src.llm import proxy
        self.assertEqual(proxy._RELATIONSHIP_BAND_MIN_THRESHOLD, 0.3)
        self.assertEqual(proxy._RELATIONSHIP_BAND_FAMILIAR, 0.5)
        self.assertEqual(proxy._RELATIONSHIP_BAND_CLOSE, 0.7)
        self.assertEqual(proxy._RELATIONSHIP_BAND_DEEP_TRUST, 0.9)

    def test_helper_does_not_modify_relationship_data(self):
        """Calling _format_relationship_block must NOT modify relationship entry"""
        from src.llm.proxy import _format_relationship_block

        # Use a mock that tracks if .put / .touch was called
        mock_mgr = MagicMock()
        mock_store = MagicMock()
        original_entry = {
            "impression": "I love Bry",
            "feeling": "deeply attached",
            "confidence": 0.85,
            "interaction_count": 999,
            "last_interaction_at": "2026-08-11T10:00:00Z",
            "last_updated": "2026-08-11T10:00:00Z",
            "created_at": "2026-01-01T00:00:00Z",
        }
        mock_store.get.return_value = original_entry
        mock_mgr.get_store.return_value = mock_store

        with patch(
            "src.soul.relationships.get_relationships_manager",
            return_value=mock_mgr,
        ):
            _format_relationship_block("agent_immut")

        # Verify the entry dict was NOT mutated
        self.assertEqual(original_entry["confidence"], 0.85)
        self.assertEqual(original_entry["impression"], "I love Bry")
        self.assertEqual(original_entry["feeling"], "deeply attached")
        # Verify no write methods were called on the store
        mock_store.touch.assert_not_called()
        mock_store.update_impression.assert_not_called()
        mock_store.ensure_relationship.assert_not_called()


if __name__ == "__main__":
    unittest.main()
