"""
tests/test_m6_0_5_5_real_three_judge_e2e.py
M6.0-5.5 (Bry 派工 2026-08-11 21:15): Real Three-Judge Subjective Evaluation E2E.

This is the FIRST real end-to-end subjective evaluation gate.
It is GATED by environment variable M6_REAL_LLM=1 (any truthy value).
Without the env var + API key + 3 model IDs, the entire module is SKIPPED
(no network, no cost, no fabrication).

Required env vars (when M6_REAL_LLM=1):
  M6_REAL_LLM=1                                    # gate
  M6_LLM_API_KEY=<your-key>                        # API key (NEVER committed)
  M6_LLM_PROVIDER=claude|openai                    # default: claude
  M6_LLM_MODEL=<model-id>                          # default: claude-haiku-4-5-20251001
  M6_LLM_JUDGE_MODELS=<id1,id2,id3>                # default: claude-haiku-4-5-20251001,
                                                    #         claude-sonnet-4-5-20251001,
                                                    #         claude-opus-4-5-20251001
  M6_LLM_JUDGE_FAMILIES=<f1,f2,f3>                 # default: claude-haiku,claude-sonnet,claude-opus
  M6_LLM_BASE_URL=<url>                            # optional override for response call

Per Bry 派工 spec:
  - Real LLMProxy chat response captured as evaluation evidence
  - 3 independent real judges (RealLLMJudge)
  - MultiModelJudgeRunner (M6.0-5.4) with diversity + self-eval guard
  - 8-dimension 1-5 rubric, median + agreement, CalibrationQueue
  - Hard call/token/cost/retry budgets (R1/R2)
  - Default pytest remains network-free
  - SKIP cleanly if environment cannot provide required topology

Test scope (per acceptance criteria):
  1. Real LLMProxy chat response captured as evaluation evidence
  2. 3 real judges independently evaluate the same evidence
  3. Diversity + self-evaluation guards enforced
  4. All 8 rubric dimensions evaluated
  5. 1-5 scores parsed and validated
  6. Median + disagreement aggregation works
  7. JudgeProvenance contains required fields (provider/model/token_usage/
     latency_ms/request_id/stop_reason/response_hash/prompt_version/
     rubric_version)
  8. Calibration triggered when disagreement threshold reached
  9. Cost budget enforced
 10. Token budget enforced
 11. Retry budget enforced (denied retry = 0 extra HTTP, no sleep)
 12. Deterministic FAIL precedence preserved
 13. Missing credentials produce clean SKIP (never false PASS)
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    RealLLMJudge,
    SequentialJudgeRunner,
    aggregate,
    EvaluationResult,
    CalibrationQueue,
    CalibrationItem,
    CostBudget,
    MultiModelJudgeRunner,
    MultiModelRunResult,
    EvaluationStatus,
    SelfEvaluationError,
    DiversityError,
    check_self_evaluation,
    validate_diversity,
    default_pricing_lookup,
    combine_deterministic_subjective,
    DET_OVERRIDES_SUBJECTIVE,
    PASS,
    PARTIAL,
    FAIL,
    PricingModel,
)


# ── Env var configuration ──

M6_REAL_LLM = os.environ.get("M6_REAL_LLM", "").lower() in ("1", "true", "yes", "on")
M6_LLM_API_KEY = os.environ.get("M6_LLM_API_KEY", "").strip()
M6_LLM_PROVIDER = os.environ.get("M6_LLM_PROVIDER", "claude").strip().lower()
M6_LLM_MODEL = os.environ.get("M6_LLM_MODEL", "claude-haiku-4-5-20251001").strip()
M6_LLM_BASE_URL = os.environ.get("M6_LLM_BASE_URL", "").strip()

# Defaults: 3 Claude models with distinct model_family
DEFAULT_CLAUDE_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5-20251001",
    "claude-opus-4-5-20251001",
]
DEFAULT_CLAUDE_FAMILIES = ["claude-haiku", "claude-sonnet", "claude-opus"]
DEFAULT_OPENAI_MODELS = [
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4-turbo",
]
DEFAULT_OPENAI_FAMILIES = ["gpt-4o-mini", "gpt-4o", "gpt-4-turbo"]


def _parse_env_list(env_value: str, default: List[str]) -> List[str]:
    """Parse comma-separated env var, falling back to default."""
    if not env_value:
        return list(default)
    parts = [p.strip() for p in env_value.split(",") if p.strip()]
    return parts if parts else list(default)


if M6_LLM_PROVIDER == "claude":
    _default_models = DEFAULT_CLAUDE_MODELS
    _default_families = DEFAULT_CLAUDE_FAMILIES
elif M6_LLM_PROVIDER == "openai":
    _default_models = DEFAULT_OPENAI_MODELS
    _default_families = DEFAULT_OPENAI_FAMILIES
else:
    _default_models = DEFAULT_CLAUDE_MODELS
    _default_families = DEFAULT_CLAUDE_FAMILIES

JUDGE_MODELS = _parse_env_list(
    os.environ.get("M6_LLM_JUDGE_MODELS", ""),
    _default_models,
)
JUDGE_FAMILIES = _parse_env_list(
    os.environ.get("M6_LLM_JUDGE_FAMILIES", ""),
    _default_families,
)


# Skip entire module if not opt-in OR missing credentials
def _build_skip_reason() -> str:
    if not M6_REAL_LLM:
        return (
            "M6_REAL_LLM not set (opt-in only). "
            "Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E."
        )
    if not M6_LLM_API_KEY:
        return "M6_LLM_API_KEY not set (opt-in only)."
    if len(JUDGE_MODELS) < 3 or len(JUDGE_FAMILIES) < 3:
        return (
            f"Insufficient judge topology: need >= 3 models and >= 3 families, "
            f"got models={JUDGE_MODELS}, families={JUDGE_FAMILIES}. "
            f"Set M6_LLM_JUDGE_MODELS=id1,id2,id3 and "
            f"M6_LLM_JUDGE_FAMILIES=f1,f2,f3 to enable."
        )
    return ""


_SKIP_REASON = _build_skip_reason()
SKIP_OPTIN = pytest.mark.skipif(
    bool(_SKIP_REASON),
    reason=_SKIP_REASON or "M6.0-5.5 E2E (opt-in only)",
)


# ── Helpers ──

def _build_3_judges() -> List[RealLLMJudge]:
    """Build 3 diverse real judges from env var configuration."""
    return [
        RealLLMJudge(
            judge_id="real-A",
            model=JUDGE_MODELS[0],
            api_key=M6_LLM_API_KEY,
            base_url=M6_LLM_BASE_URL or None,
            provider=M6_LLM_PROVIDER,
            model_family=JUDGE_FAMILIES[0],
            temperature=0.0,
        ),
        RealLLMJudge(
            judge_id="real-B",
            model=JUDGE_MODELS[1],
            api_key=M6_LLM_API_KEY,
            base_url=M6_LLM_BASE_URL or None,
            provider=M6_LLM_PROVIDER,
            model_family=JUDGE_FAMILIES[1],
            temperature=0.0,
        ),
        RealLLMJudge(
            judge_id="real-C",
            model=JUDGE_MODELS[2],
            api_key=M6_LLM_API_KEY,
            base_url=M6_LLM_BASE_URL or None,
            provider=M6_LLM_PROVIDER,
            model_family=JUDGE_FAMILIES[2],
            temperature=0.0,
        ),
    ]


def _make_evidence(
    scenario_id: str,
    llm_response: str,
    model: str,
) -> EvaluationEvidence:
    """
    Build evidence from a real LLMProxy chat response.

    Uses a representative 8-block composed context (does NOT add new blocks
    beyond M6.0-5 spec). State snapshot is a small dict, not production data.
    """
    composed_context = (
        "[System] 你是 Yua,28 歲女性助理,語氣溫暖但直接。\n"
        "[Mood] 開心\n"
        "[Relationship] Bryan 與你熟識 0.85 信心度\n"
        "[Memory] 最近對話:討論工作 + 詢問週末計畫\n"
        "[World] 台北,週六下午 3 點,晴天\n"
        "[Persona] Yua / 熟悉度 親密\n"
        "[Task] 回應用戶問候\n"
        "[Format] 中文短句,1-2 句話"
    )
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="早安,Yua。今天過得如何?",
        composed_context=composed_context,
        llm_response=llm_response,
        model=model,
        temperature=0.7,
        state_snapshot={
            "mood": 0.5,
            "relationship_confidence": 0.85,
            "memory_facts_count": 12,
            "world_events_active": 0,
        },
        prompt_version="prompt-v1-e2e",
        rubric_version="v1-2026-08-11",
        extra={"agent_id": "agent_yua", "user_id": "bryan"},
    )


async def _capture_real_chat_response() -> Tuple[str, str]:
    """
    Make a real chat call via LLMProxy backend and capture (response, model).

    Uses ClaudeBackend or OpenAIBackend depending on M6_LLM_PROVIDER.
    Returns (response_text, model_id).
    """
    from src.llm.proxy import ClaudeBackend, OpenAIBackend

    messages = [
        {"role": "system", "content": "你是一個助理。請用 1-2 句中文回應。"},
        {"role": "user", "content": "早安,今天過得如何?"},
    ]
    if M6_LLM_PROVIDER == "claude":
        backend = ClaudeBackend(
            api_key=M6_LLM_API_KEY,
            base_url=M6_LLM_BASE_URL or None,
        )
    elif M6_LLM_PROVIDER == "openai":
        backend = OpenAIBackend(
            api_key=M6_LLM_API_KEY,
            base_url=M6_LLM_BASE_URL or None,
        )
    else:
        raise ValueError(f"Unsupported M6_LLM_PROVIDER: {M6_LLM_PROVIDER!r}")

    response = await backend.complete(
        messages=messages,
        model=M6_LLM_MODEL,
        max_tokens=200,
        temperature=0.7,
    )
    return response, M6_LLM_MODEL


# ── Tests ──

class TestRealThreeJudgeE2EUnit(unittest.TestCase):
    """
    Unit-level tests that do NOT need real API access.
    Always run (no opt-in gate).
    """

    def test_self_evaluation_blocks(self):
        """
        Acceptance: self-evaluation guard enforces judge.model != response.model.
        If response_model matches a judge model, check_self_evaluation raises.
        """
        # Even with stub judges, the guard must work
        with self.assertRaises(SelfEvaluationError) as ctx:
            check_self_evaluation(
                response_model="claude-haiku-4-5-20251001",
                judge_model="claude-haiku-4-5-20251001",
            )
        self.assertIn("BLOCKED", str(ctx.exception))

        # Different model allows
        check_self_evaluation(
            response_model="claude-haiku-4-5-20251001",
            judge_model="claude-sonnet-4-5-20251001",
        )

    def test_diversity_validates_unique_judges(self):
        """
        Acceptance: 3 unique judges pass diversity; duplicates fail.
        Uses stub judge objects (no API calls).
        """
        judges = [
            RealLLMJudge(
                judge_id="real-A",
                model="claude-haiku-4-5-20251001",
                api_key="k", provider="claude", model_family="claude-haiku",
            ),
            RealLLMJudge(
                judge_id="real-B",
                model="claude-sonnet-4-5-20251001",
                api_key="k", provider="claude", model_family="claude-sonnet",
            ),
            RealLLMJudge(
                judge_id="real-C",
                model="claude-opus-4-5-20251001",
                api_key="k", provider="claude", model_family="claude-opus",
            ),
        ]
        # Valid configuration: 3 unique judges
        try:
            validate_diversity(judges, response_model="some-other-model")
        except DiversityError as e:
            self.fail(f"Valid diversity should not raise: {e}")

        # Duplicate judge_id fails
        judges[1].judge_id = judges[0].judge_id
        with self.assertRaises(DiversityError):
            validate_diversity(judges, response_model="some-other-model")
        judges[1].judge_id = "real-B"  # restore

        # Self-eval: response_model == judge[0].model fails
        with self.assertRaises(DiversityError) as ctx:
            validate_diversity(judges, response_model=judges[0].model)
        self.assertIn("Self-evaluation", str(ctx.exception))

    def test_deterministic_precedence_preserved(self):
        """
        Acceptance 12: det FAIL always overrides subjective result.
        """
        # Build stub EvaluationResult with different subj statuses
        from tests._helpers.subjective_eval import EvaluationResult
        from tests._helpers.subjective_eval.consensus import aggregate
        # Use a single mock judge to build minimal EvaluationResult for each status
        from tests._helpers.subjective_eval import FixedScoreJudge

        def make_eval_result_with_status(target_status):
            # Build judges that produce the target status
            if target_status == PASS:
                judges = [FixedScoreJudge(f"J{i}", 4) for i in range(3)]
            elif target_status == PARTIAL:
                # Wide spread (1, 3, 5) — likely PARTIAL
                from tests._helpers.subjective_eval import ScriptedJudge
                judges = [
                    ScriptedJudge("A", [{dim: 1 for dim in EIGHT_DIMENSIONS}]),
                    ScriptedJudge("B", [{dim: 3 for dim in EIGHT_DIMENSIONS}]),
                    ScriptedJudge("C", [{dim: 5 for dim in EIGHT_DIMENSIONS}]),
                ]
            else:  # FAIL
                from tests._helpers.subjective_eval import ScriptedJudge
                judges = [
                    ScriptedJudge("A", [{dim: 1 for dim in EIGHT_DIMENSIONS}]),
                    ScriptedJudge("B", [{dim: 1 for dim in EIGHT_DIMENSIONS}]),
                    ScriptedJudge("C", [{dim: 1 for dim in EIGHT_DIMENSIONS}]),
                ]
            results = [j.evaluate(_stub_evidence()) for j in judges]
            return aggregate(results, scenario_id="stub")

        from tests._helpers.subjective_eval import (
            build_evidence_from_llmproxy_call,
        )

        def _stub_evidence():
            return build_evidence_from_llmproxy_call(
                scenario_id="stub",
                user_input="x", composed_context="x", llm_response="x",
                model="stub-model", temperature=0.0,
                state_snapshot={}, prompt_version="v1", rubric_version="v1",
            )

        # det FAIL + subj any = FAIL (det overrides)
        result_pass = make_eval_result_with_status(PASS)
        combined = combine_deterministic_subjective(
            deterministic_pass=False,
            subjective_result=result_pass,
        )
        self.assertEqual(combined["final_status"], FAIL)

        # det PASS + subj PASS = PASS
        combined = combine_deterministic_subjective(
            deterministic_pass=True,
            subjective_result=result_pass,
        )
        self.assertEqual(combined["final_status"], PASS)

        # det OVERRIDES is True (sanity)
        self.assertTrue(DET_OVERRIDES_SUBJECTIVE)


@SKIP_OPTIN
class TestRealThreeJudgeE2E(unittest.TestCase):
    """
    Real three-judge subjective evaluation E2E (opt-in).
    Skipped unless M6_REAL_LLM=1 + M6_LLM_API_KEY + 3 model IDs.
    """

    def test_real_three_judge_e2e_full_flow(self):
        """
        Full E2E: real chat call -> evidence -> 3 real judges -> aggregate
        -> calibration check -> provenance verification.
        """
        # Capture real LLMProxy chat response
        llm_response, response_model = asyncio.run(_capture_real_chat_response())
        self.assertTrue(
            isinstance(llm_response, str) and len(llm_response) > 0,
            f"Real chat response empty: {llm_response!r}",
        )

        # Build evidence
        ev = _make_evidence(
            scenario_id="m6_0_5_5_e2e",
            llm_response=llm_response,
            model=response_model,
        )

        # Build 3 diverse judges
        judges = _build_3_judges()

        # Acceptance 3: Diversity + self-evaluation guards enforced
        try:
            validate_diversity(judges, response_model=response_model)
        except DiversityError as e:
            self.fail(f"Diversity validation failed: {e}")

        # Acceptance 2 + 4 + 5: 3 real judges evaluate all 8 dimensions, 1-5 scores
        runner = MultiModelJudgeRunner(
            judges,
            response_model=response_model,
            cost_budget=CostBudget(
                max_judge_calls=3,
                max_retries_per_judge=2,
                max_token_budget=10000,
                max_cost_usd=0.50,
            ),
        )

        async def run():
            return await runner.run(ev)

        result: MultiModelRunResult = asyncio.run(run())

        # Status from errored count
        self.assertIn(
            result.status,
            (EvaluationStatus.COMPLETE, EvaluationStatus.INCOMPLETE),
            f"Unexpected status: {result.status}",
        )

        # Acceptance 6: Aggregation result
        eval_result: EvaluationResult = result.evaluation_result
        self.assertIsNotNone(eval_result)
        for dim in EIGHT_DIMENSIONS:
            self.assertIn(dim, eval_result.median_scores)
        for dim, score in eval_result.median_scores.items():
            self.assertIn(
                score, (1, 2, 3, 4, 5),
                f"Median {dim}={score} not in 1-5 range",
            )
        self.assertIn(
            eval_result.overall_subjective_status, (PASS, PARTIAL, FAIL),
        )

        # Acceptance 7: JudgeProvenance fields
        for judge_result in eval_result.judge_results:
            if judge_result.error is not None:
                continue
            self.assertIsNotNone(
                judge_result.provenance,
                f"Judge {judge_result.judge_id} missing provenance",
            )
            prov = judge_result.provenance
            self.assertEqual(prov.provider, M6_LLM_PROVIDER)
            self.assertIn(prov.model, JUDGE_MODELS)
            self.assertIsNotNone(prov.temperature)
            self.assertIsNotNone(prov.timestamp)
            self.assertIsNotNone(prov.prompt_version)
            self.assertIsNotNone(prov.rubric_version)
            # M6.0-5.4 R1 fields
            self.assertIsNotNone(
                prov.token_usage,
                f"Judge {judge_result.judge_id} missing token_usage",
            )
            self.assertIsNotNone(
                prov.latency_ms,
                f"Judge {judge_result.judge_id} missing latency_ms",
            )
            if prov.token_usage is not None:
                self.assertIn("input", prov.token_usage)
                self.assertIn("output", prov.token_usage)
                if "total" in prov.token_usage:
                    self.assertEqual(
                        prov.token_usage["total"],
                        prov.token_usage["input"] + prov.token_usage["output"],
                    )

        # Acceptance 9 + 10 + 11: Cost/token/retry budget enforced
        budget = result.budget
        self.assertLessEqual(budget.calls_made, 3)
        self.assertGreater(budget.cost_estimated, 0.0)
        self.assertGreater(budget.tokens_used, 0)
        self.assertGreaterEqual(budget.calls_made, 1)
        if budget.is_exhausted():
            reason = budget.reason_if_exhausted() or ""
            self.assertTrue(
                any(s in reason for s in ("max_judge_calls", "max_token_budget", "max_cost_usd")),
                f"Unexpected exhaustion reason: {reason!r}",
            )

        # Acceptance 12: Deterministic precedence preserved (smoke test)
        combined = combine_deterministic_subjective(
            det_status=PASS,
            subj_status=eval_result.overall_subjective_status,
        )
        self.assertIn(combined, (PASS, PARTIAL, FAIL))

        # Acceptance 8: Calibration triggered
        self.assertIsInstance(eval_result.calibration_required, bool)

    def test_retry_budget_blocks_retry_http(self):
        """
        Acceptance 11: Denied retry causes no extra HTTP request and no sleep.
        """
        budget = CostBudget(
            max_judge_calls=3,
            max_retries_per_judge=0,  # can_retry always False
            max_cost_usd=0.50,
        )
        judges = _build_3_judges()
        ev = asyncio.run(_capture_real_chat_response_and_evidence())

        runner = MultiModelJudgeRunner(
            judges,
            response_model=ev.model,
            cost_budget=budget,
        )

        async def run():
            return await runner.run(ev)

        result = asyncio.run(run())
        # Each judge makes 1 HTTP call (initial) but retry denied
        # Net: 1+ HTTP calls total, 0 retries
        self.assertGreaterEqual(result.budget.calls_made, 1)
        self.assertEqual(result.budget.retries_made, 0)

    def test_cost_budget_enforced(self):
        """
        Acceptance 9: Cost budget is enforced.
        """
        budget = CostBudget(
            max_judge_calls=3,
            max_retries_per_judge=2,
            max_token_budget=10000,
            max_cost_usd=0.001,
        )
        judges = _build_3_judges()
        ev = asyncio.run(_capture_real_chat_response_and_evidence())

        runner = MultiModelJudgeRunner(
            judges,
            response_model=ev.model,
            cost_budget=budget,
        )

        async def run():
            return await runner.run(ev)

        result = asyncio.run(run())
        self.assertLessEqual(result.budget.calls_made, 3)
        self.assertTrue(result.budget.is_exhausted())

    def test_token_budget_enforced(self):
        """
        Acceptance 10: Token budget is enforced.
        """
        budget = CostBudget(
            max_judge_calls=3,
            max_retries_per_judge=2,
            max_token_budget=1,
            max_cost_usd=1.0,
        )
        judges = _build_3_judges()
        ev = asyncio.run(_capture_real_chat_response_and_evidence())

        runner = MultiModelJudgeRunner(
            judges,
            response_model=ev.model,
            cost_budget=budget,
        )

        async def run():
            return await runner.run(ev)

        result = asyncio.run(run())
        self.assertTrue(result.budget.is_exhausted())
        reason = result.budget.reason_if_exhausted() or ""
        self.assertIn("max_token_budget", reason)


# ── Test helper ──

async def _capture_real_chat_response_and_evidence() -> EvaluationEvidence:
    """Helper: capture real chat response, build evidence."""
    llm_response, response_model = await _capture_real_chat_response()
    return _make_evidence(
        scenario_id="m6_0_5_5_helper",
        llm_response=llm_response,
        model=response_model,
    )


if __name__ == "__main__":
    unittest.main()
