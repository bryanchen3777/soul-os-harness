"""
tests/test_m6_0_5_6_budget_config.py
M6.0-5.6 (Bry 派工 2026-08-11 21:30): Configurable Evaluation Cost Ceiling tests.

Validates the canonical EvaluationBudgetConfig surface + propagation + R1/R2
preservation + zero/invalid limit handling.

Test categories (per Bry 派工 spec):
  Validation:
    - default values are deterministic
    - custom values
    - zero limits are valid (means "no calls / no retries where applicable")
    - negative limits are rejected (ValueError)
    - invalid types are rejected (TypeError)
    - no silent fallback to unlimited
  Propagation:
    - budget_config.to_cost_budget() derives correct CostBudget
    - MultiModelJudgeRunner accepts budget_config
    - Both budget_config and cost_budget → ValueError (no silent override)
    - budget_config propagates max_retries to each RealLLMJudge
  Enforcement:
    - max_judge_calls enforced
    - max_token_budget enforced
    - max_cost_usd enforced
    - max_retries enforced
  R1/R2 regression:
    - call_count == budget.calls_made
    - denied retry: 0 extra HTTP, 0 sleep, 0 counter increment
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Callable, Dict, List
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    RealLLMJudge,
    EvaluationStatus,
    CostBudget,
    MultiModelJudgeRunner,
    MultiModelRunResult,
    EvaluationBudgetConfig,
    PricingModel,
    default_pricing_lookup,
)


# ── Helpers ──

def _make_evidence(scenario_id: str = "m6_0_5_6") -> EvaluationEvidence:
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="x",
        composed_context="x",
        llm_response="x",
        model="response-model-test",
        temperature=0.0,
        state_snapshot={},
        prompt_version="v1",
        extra={},
    )


def _claude_response(scores: Dict[str, int] = None) -> Dict[str, Any]:
    if scores is None:
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
    return {
        "id": "msg_test_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": json.dumps({**scores, "rationale": "ok"})}],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1500, "output_tokens": 500},
    }


def _smart_handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if "openai" in url or "chat/completions" in url:
        from tests.test_m6_0_5_4_r1_budget_enforcement import _openai_response
        return httpx.Response(200, json=_openai_response())
    return httpx.Response(200, json=_claude_response())


def _make_3_diverse_judges() -> List[RealLLMJudge]:
    return [
        RealLLMJudge(judge_id="A", model="claude-haiku-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-haiku"),
        RealLLMJudge(judge_id="B", model="claude-sonnet-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-sonnet"),
        RealLLMJudge(judge_id="C", model="gpt-4o-mini",
                     api_key="k", provider="openai", model_family="gpt-4o-mini"),
    ]


CONTROLLED_PRICING = PricingModel(
    provider="*", model="*",
    input_cost_per_1k=0.01, output_cost_per_1k=0.02,
)


def _controlled_pricing_lookup(provider: str, model: str) -> PricingModel:
    return CONTROLLED_PRICING


# ── 1. Defaults + validation ──

class TestEvaluationBudgetConfigDefaults(unittest.TestCase):
    """Default values are deterministic; not unlimited."""

    def test_default_values_match_documented(self):
        cfg = EvaluationBudgetConfig()
        self.assertEqual(cfg.max_judge_calls, 3)
        self.assertEqual(cfg.max_retries_per_judge, 2)
        self.assertEqual(cfg.max_token_budget, 5000)
        self.assertEqual(cfg.max_cost_usd, 0.05)

    def test_frozen_immutable(self):
        cfg = EvaluationBudgetConfig()
        with self.assertRaises(Exception):  # FrozenInstanceError or AttributeError
            cfg.max_judge_calls = 5  # type: ignore

    def test_custom_values(self):
        cfg = EvaluationBudgetConfig(
            max_judge_calls=5,
            max_retries_per_judge=3,
            max_token_budget=10000,
            max_cost_usd=0.20,
        )
        self.assertEqual(cfg.max_judge_calls, 5)
        self.assertEqual(cfg.max_retries_per_judge, 3)
        self.assertEqual(cfg.max_token_budget, 10000)
        self.assertEqual(cfg.max_cost_usd, 0.20)


class TestEvaluationBudgetConfigValidation(unittest.TestCase):
    """Validation: negative rejected, zero valid, invalid types rejected."""

    def test_zero_limits_are_valid(self):
        """Zero is valid and means 'no calls / no retries where applicable'."""
        cfg = EvaluationBudgetConfig(
            max_judge_calls=0,
            max_retries_per_judge=0,
            max_token_budget=0,
            max_cost_usd=0.0,
        )
        self.assertEqual(cfg.max_judge_calls, 0)
        self.assertEqual(cfg.max_retries_per_judge, 0)
        self.assertEqual(cfg.max_token_budget, 0)
        self.assertEqual(cfg.max_cost_usd, 0.0)

    def test_negative_max_judge_calls_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            EvaluationBudgetConfig(max_judge_calls=-1)
        self.assertIn("max_judge_calls must be >= 0", str(ctx.exception))

    def test_negative_max_retries_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            EvaluationBudgetConfig(max_retries_per_judge=-1)
        self.assertIn("max_retries_per_judge must be >= 0", str(ctx.exception))

    def test_negative_max_token_budget_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            EvaluationBudgetConfig(max_token_budget=-1)
        self.assertIn("max_token_budget must be >= 0", str(ctx.exception))

    def test_negative_max_cost_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            EvaluationBudgetConfig(max_cost_usd=-0.01)
        self.assertIn("max_cost_usd must be >= 0", str(ctx.exception))

    def test_invalid_type_max_judge_calls_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig(max_judge_calls="3")  # type: ignore
        self.assertIn("max_judge_calls must be int", str(ctx.exception))

    def test_invalid_type_max_cost_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            EvaluationBudgetConfig(max_cost_usd="0.05")  # type: ignore
        self.assertIn("max_cost_usd must be numeric", str(ctx.exception))

    def test_no_silent_unlimited_fallback(self):
        """Defaults are NOT unlimited (would silently allow any usage)."""
        cfg = EvaluationBudgetConfig()
        # Document the limits — they are finite, not unlimited
        self.assertLess(cfg.max_judge_calls, 1000000)
        self.assertLess(cfg.max_token_budget, 1000000)
        self.assertLess(cfg.max_cost_usd, 1000000.0)


# ── 2. to_cost_budget derivation ──

class TestToCostBudget(unittest.TestCase):
    """to_cost_budget() derives a CostBudget with the configured limits."""

    def test_to_cost_budget_preserves_limits(self):
        cfg = EvaluationBudgetConfig(
            max_judge_calls=5,
            max_retries_per_judge=3,
            max_token_budget=10000,
            max_cost_usd=0.20,
        )
        budget = cfg.to_cost_budget()
        self.assertEqual(budget.max_judge_calls, 5)
        self.assertEqual(budget.max_retries_per_judge, 3)
        self.assertEqual(budget.max_token_budget, 10000)
        self.assertEqual(budget.max_cost_usd, 0.20)
        # Fresh state
        self.assertEqual(budget.calls_made, 0)
        self.assertEqual(budget.retries_made, 0)
        self.assertEqual(budget.tokens_used, 0)
        self.assertEqual(budget.cost_estimated, 0.0)

    def test_to_cost_budget_zero_limits_means_no_calls(self):
        cfg = EvaluationBudgetConfig(
            max_judge_calls=0,
            max_retries_per_judge=0,
            max_token_budget=0,
            max_cost_usd=0.0,
        )
        budget = cfg.to_cost_budget()
        # can_make_call returns False (0 >= 0)
        self.assertFalse(budget.can_make_call())
        # can_retry returns False (0 >= 0)
        self.assertFalse(budget.can_retry())


# ── 3. Configuration propagation into MultiModelJudgeRunner ──

class TestConfigurationPropagation(unittest.TestCase):
    """budget_config propagates to cost_budget and judges."""

    def test_runner_accepts_budget_config(self):
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_judge_calls=2, max_token_budget=1000)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg,
        )
        # cost_budget derived from config
        self.assertEqual(runner.cost_budget.max_judge_calls, 2)
        self.assertEqual(runner.cost_budget.max_token_budget, 1000)
        # budget_config stored
        self.assertIs(runner.budget_config, cfg)

    def test_runner_propagates_max_retries_to_judges(self):
        """budget_config.max_retries_per_judge propagates to each judge."""
        judges = _make_3_diverse_judges()
        # Default judge.max_retries = 2
        cfg = EvaluationBudgetConfig(max_retries_per_judge=4)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg,
        )
        # Each judge.max_retries = cfg.max_retries_per_judge + 1
        for j in runner.judges:
            self.assertEqual(j.max_retries, 5)

    def test_runner_propagates_zero_max_retries(self):
        """cfg.max_retries_per_judge=0 → judge.max_retries=1 (1 initial, 0 retries)."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_retries_per_judge=0)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg,
        )
        for j in runner.judges:
            self.assertEqual(j.max_retries, 1)

    def test_runner_rejects_both_budget_config_and_cost_budget(self):
        """No silent override when both are passed."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig()
        cost_budget = CostBudget()
        with self.assertRaises(ValueError) as ctx:
            MultiModelJudgeRunner(
                judges, response_model="response-model-test",
                budget_config=cfg, cost_budget=cost_budget,
            )
        self.assertIn("cannot pass both", str(ctx.exception))

    def test_runner_backward_compatible_no_config(self):
        """If neither budget_config nor cost_budget is passed, defaults apply."""
        judges = _make_3_diverse_judges()
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
        )
        # Default CostBudget (matches EvaluationBudgetConfig defaults)
        self.assertEqual(runner.cost_budget.max_judge_calls, 3)
        self.assertEqual(runner.cost_budget.max_retries_per_judge, 2)
        self.assertEqual(runner.cost_budget.max_token_budget, 5000)
        self.assertEqual(runner.cost_budget.max_cost_usd, 0.05)
        # budget_config is None
        self.assertIsNone(runner.budget_config)

    def test_runner_legacy_cost_budget_still_works(self):
        """Pre-M6.0-5.6 path: pass cost_budget directly."""
        judges = _make_3_diverse_judges()
        budget = CostBudget(
            max_judge_calls=2,
            max_retries_per_judge=1,
            max_token_budget=500,
            max_cost_usd=0.01,
        )
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            cost_budget=budget,
        )
        self.assertIs(runner.cost_budget, budget)
        # budget_config is None (legacy path)
        self.assertIsNone(runner.budget_config)


# ── 4. Enforcement via MultiModelJudgeRunner ──

class TestEnforcement(unittest.TestCase):
    """Each of the 4 limits is actually enforced (not just stored)."""

    def test_max_judge_calls_enforced(self):
        """max_judge_calls=1: only 1 judge call is made, others blocked."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_judge_calls=1, max_cost_usd=0.10)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("max_calls_test")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_smart_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        self.assertEqual(result.budget.calls_made, 1)
        # status: 2 of 3 errored (budget_exhausted) -> UNAVAILABLE
        # (per M6.0-5.4: budget_exhausted counts as errored)
        self.assertEqual(result.status, EvaluationStatus.UNAVAILABLE)

    def test_max_token_budget_enforced(self):
        """max_token_budget=2500: only 2 judges called (3rd blocked pre-call)."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(
            max_judge_calls=10, max_token_budget=2500, max_cost_usd=0.10,
        )
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("max_tokens_test")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_smart_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # Per call: 2000 tokens; max_token_budget=2500:
        # After call 1: 2000 < 2500, can_make_call True -> call
        # After call 2: 4000 >= 2500 -> exhausted. Call 3 blocked.
        self.assertEqual(result.budget.calls_made, 2)
        self.assertEqual(result.budget.tokens_used, 4000)
        self.assertIn("max_token_budget", result.budget.reason_if_exhausted() or "")

    def test_max_cost_usd_enforced(self):
        """max_cost_usd=0.03: only 2 judges called (3rd blocked pre-call)."""
        judges = _make_3_diverse_judges()
        # Per call: $0.025; max=0.03 -> 1 call (0.025 < 0.03), 2nd call (0.025 < 0.03),
        # after 2nd call cost=0.05 >= 0.03 -> exhausted, 3rd blocked.
        cfg = EvaluationBudgetConfig(
            max_judge_calls=10, max_cost_usd=0.03,
        )
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("max_cost_test")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_smart_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        self.assertEqual(result.budget.calls_made, 2)
        self.assertIn("max_cost_usd", result.budget.reason_if_exhausted() or "")

    def test_max_retries_enforced(self):
        """max_retries_per_judge=0: each judge makes 1 attempt, no retries."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(
            max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10,
        )
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("max_retries_test")

        # Handler that always returns 500 (would normally trigger retry)
        call_count = [0]
        def always_500(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(always_500)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # 3 judges x 1 attempt = 3 HTTP calls (no retries)
        self.assertEqual(call_count[0], 3)
        self.assertEqual(result.budget.calls_made, 3)
        self.assertEqual(result.budget.retries_made, 0)


# ── 5. Zero-budget behavior ──

class TestZeroBudgetBehavior(unittest.TestCase):
    """Zero limits mean no calls (valid, deterministic)."""

    def test_zero_max_judge_calls_blocks_all(self):
        """max_judge_calls=0 → 0 HTTP calls, all judges budget_exhausted."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_judge_calls=0)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("zero_calls")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # Pre-call: 0 >= 0 -> False, all 3 blocked. 0 HTTP calls.
        self.assertEqual(call_count[0], 0)
        self.assertEqual(result.budget.calls_made, 0)
        self.assertEqual(result.status, EvaluationStatus.UNAVAILABLE)

    def test_zero_max_token_budget_blocks_all(self):
        """max_token_budget=0 → 0 HTTP calls."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_token_budget=0)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("zero_tokens")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        self.assertEqual(call_count[0], 0)
        self.assertEqual(result.budget.calls_made, 0)
        self.assertEqual(result.status, EvaluationStatus.UNAVAILABLE)

    def test_zero_max_cost_blocks_all(self):
        """max_cost_usd=0 → 0 HTTP calls."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_cost_usd=0.0)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("zero_cost")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        self.assertEqual(call_count[0], 0)
        self.assertEqual(result.budget.calls_made, 0)
        self.assertEqual(result.status, EvaluationStatus.UNAVAILABLE)


# ── 6. R1/R2 regression: call count + denied retry ──

class TestR1R2Regression(unittest.TestCase):
    """R1: real cost accumulation. R2: denied retry = 0 extra HTTP, 0 sleep, 0 counter."""

    def test_call_count_equals_budget_calls_made(self):
        """R1 regression: call_count == budget.calls_made (no surprise calls)."""
        judges = _make_3_diverse_judges()
        cfg = EvaluationBudgetConfig(max_judge_calls=2, max_cost_usd=0.10)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("call_count_regression")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # R1: HTTP call count exactly matches budget.calls_made
        self.assertEqual(call_count[0], result.budget.calls_made)
        # 2 successful calls, 1 blocked -> 2 HTTP calls total
        self.assertEqual(call_count[0], 2)
        self.assertEqual(result.budget.calls_made, 2)

    def test_denied_retry_no_extra_http(self):
        """R2 regression: denied retry → 0 extra HTTP calls."""
        cfg = EvaluationBudgetConfig(
            max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10,
        )
        judge = RealLLMJudge(
            judge_id="A", model="m", api_key="k",
            provider="claude", max_retries=cfg.max_retries_per_judge + 1,
        )
        # Override judge.max_retries via runner construction
        runner = MultiModelJudgeRunner(
            [judge,
             RealLLMJudge(judge_id="B", model="m2", api_key="k",
                          provider="openai", model_family="f2"),
             RealLLMJudge(judge_id="C", model="m3", api_key="k",
                          provider="claude", model_family="f3")],
            response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("denied_regression")

        call_count = [0]
        def counting_500(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_500)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # 3 judges, each 1 attempt (no retry), 0 retries
        self.assertEqual(call_count[0], 3)
        self.assertEqual(result.budget.calls_made, 3)
        self.assertEqual(result.budget.retries_made, 0)

    def test_denied_retry_no_sleep(self):
        """R2 regression: denied retry → asyncio.sleep NOT called."""
        cfg = EvaluationBudgetConfig(
            max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10,
        )
        judge = RealLLMJudge(
            judge_id="A", model="m", api_key="k", provider="claude",
            max_retries=cfg.max_retries_per_judge + 1,
        )
        runner = MultiModelJudgeRunner(
            [judge,
             RealLLMJudge(judge_id="B", model="m2", api_key="k",
                          provider="openai", model_family="f2"),
             RealLLMJudge(judge_id="C", model="m3", api_key="k",
                          provider="claude", model_family="f3")],
            response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("no_sleep_regression")

        mock_sleep_holder: Dict[str, Any] = {}

        def counting_500(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_500)) as client:
                with patch("asyncio.sleep") as mock_sleep:
                    result = await runner.run(ev, http_client=client)
                    mock_sleep_holder["mock"] = mock_sleep
                    return result

        result = asyncio.run(run())
        # can_retry returns False (max_retries_per_judge=0) → on_retry returns False
        # → judge breaks out of retry loop without sleep
        mock_sleep_holder["mock"].assert_not_called()
        self.assertEqual(result.budget.retries_made, 0)

    def test_denied_retry_no_counter_increment(self):
        """R2 regression: denied retry → retries_made NOT incremented."""
        cfg = EvaluationBudgetConfig(
            max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10,
        )
        judge = RealLLMJudge(
            judge_id="A", model="m", api_key="k", provider="claude",
            max_retries=cfg.max_retries_per_judge + 1,
        )
        runner = MultiModelJudgeRunner(
            [judge,
             RealLLMJudge(judge_id="B", model="m2", api_key="k",
                          provider="openai", model_family="f2"),
             RealLLMJudge(judge_id="C", model="m3", api_key="k",
                          provider="claude", model_family="f3")],
            response_model="response-model-test",
            budget_config=cfg, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("no_counter_regression")

        def counting_500(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_500)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # R2: retries_made NOT incremented when retry is denied
        self.assertEqual(result.budget.retries_made, 0)


if __name__ == "__main__":
    unittest.main()
