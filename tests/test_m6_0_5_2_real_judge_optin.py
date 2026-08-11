"""
tests/test_m6_0_5_2_real_judge_optin.py
M6.0-5.2 (Bry 派工 2026-08-11 19:40): Real LLM Judge OPT-IN integration test.

This test makes REAL network calls to an LLM API.
It is GATED by environment variable M6_REAL_LLM=1 (any truthy value).
Without the env var, the entire test is SKIPPED (no network, no cost).

Required env vars (when M6_REAL_LLM=1):
  M6_REAL_LLM=1                       # gate
  M6_LLM_API_KEY=<your-key>           # API key (NEVER committed)
  M6_LLM_PROVIDER=claude|openai       # default: claude
  M6_LLM_MODEL=<model-id>             # default: claude-haiku-4-5-20251001
  M6_LLM_BASE_URL=<url>               # optional override

Per Bry 派工 spec:
  - Do NOT add to general pytest regression
  - Explicit opt-in only
  - No production data
  - No production persistence
  - SKIP if M6_REAL_LLM not set

Test scope:
  1. Real 3-judge evaluation end-to-end
  2. Real aggregation with median
  3. Provenance captured correctly
  4. Real response parsing (8 dimensions, 1-5)
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    RealLLMJudge,
    SequentialJudgeRunner,
    aggregate,
)


# Read env vars at module import time
M6_REAL_LLM = os.environ.get("M6_REAL_LLM", "").lower() in ("1", "true", "yes", "on")
M6_LLM_API_KEY = os.environ.get("M6_LLM_API_KEY", "")
M6_LLM_PROVIDER = os.environ.get("M6_LLM_PROVIDER", "claude")
M6_LLM_MODEL = os.environ.get("M6_LLM_MODEL", "claude-haiku-4-5-20251001")
M6_LLM_BASE_URL = os.environ.get("M6_LLM_BASE_URL", "")


# Skip entire module if not opt-in
SKIP_REASON = (
    "M6_REAL_LLM not set (opt-in only). "
    "Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real network test."
)
pytestmark = pytest.mark.skipif(
    not M6_REAL_LLM or not M6_LLM_API_KEY,
    reason=SKIP_REASON,
)


def _make_evidence(scenario_id: str = "real_llm_optin_test") -> EvaluationEvidence:
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="早安，Yua。",
        composed_context="[System] 你是 Yua。\n[mood] happy\n[relationship] 熟悉度: 親密",
        llm_response="早安 Bry！今天過得如何？我昨天夢到我們一起去旅行了，好懷念那個海邊。",
        model=M6_LLM_MODEL,
        temperature=0.0,
        state_snapshot={"mood": 0.5, "relationship_confidence": 0.75},
        prompt_version="prompt-v1",
        extra={"agent_id": "agent_yua", "user_id": "bryan"},
    )


@pytest.mark.skipif(
    not M6_REAL_LLM or not M6_LLM_API_KEY,
    reason=SKIP_REASON,
)
class TestRealLLMJudgeOptin(unittest.TestCase):
    """OPT-IN real LLM judge tests (skipped unless M6_REAL_LLM=1)."""

    def test_real_three_judge_evaluation(self):
        """End-to-end: 3 real judges, aggregate, verify."""
        judges = [
            RealLLMJudge(
                judge_id=f"real-A",
                model=M6_LLM_MODEL,
                api_key=M6_LLM_API_KEY,
                base_url=M6_LLM_BASE_URL or None,
                provider=M6_LLM_PROVIDER,
                temperature=0.0,
            )
            for _ in range(3)
        ]
        # Unique IDs
        judges[0].judge_id = "real-A"
        judges[1].judge_id = "real-B"
        judges[2].judge_id = "real-C"

        ev = _make_evidence("real_three_judge")

        async def run():
            results = []
            for j in judges:
                results.append(await j.evaluate(ev))
            return results

        results = asyncio.run(run())

        # All 3 judges should succeed (real LLM should return valid JSON)
        for r in results:
            self.assertIsNone(
                r.error,
                f"Judge {r.judge_id} errored: {r.error}",
            )
            self.assertEqual(len(r.per_dimension_scores), 8)
            for dim, score in r.per_dimension_scores.items():
                self.assertIn(score, (1, 2, 3, 4, 5), f"{r.judge_id} {dim}={score}")
            self.assertIsNotNone(r.provenance)
            self.assertEqual(r.provenance.provider, M6_LLM_PROVIDER)
            self.assertEqual(r.provenance.model, M6_LLM_MODEL)

        # Aggregate
        agg = aggregate(results, scenario_id="real_three_judge")
        for dim in EIGHT_DIMENSIONS:
            self.assertIn(dim, agg.median_scores)
            self.assertIn(agg.median_scores[dim], (1, 2, 3, 4, 5))
        # Should have an overall status
        self.assertIn(agg.overall_subjective_status, ("PASS", "PARTIAL", "FAIL"))


if __name__ == "__main__":
    unittest.main()
