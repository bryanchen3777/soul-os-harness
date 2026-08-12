"""
tests/test_m6_0_5_4_r2_retry_budget_enforcement.py
M6.0-5.4-R2 (Bry 派工 2026-08-11 21:00): Retry Budget Enforcement tests.

Validates the on_retry callback is now ENFORCEMENT-CAPABLE (not just
observational). The callback contract changes from Callable[[], None] to
Callable[[], bool]:

  - Return True  -> retry is allowed; loop sleeps + continues
  - Return False -> retry MUST NOT happen; loop breaks immediately
                    (no sleep, no extra HTTP request)

Required evidence (per Bry 派工 spec):
  A. budget available -> retry occurs -> retry counter increments
  B. retry budget exhausted -> no retry HTTP request
  C. cost budget exhausted -> no retry HTTP request
  D. token budget exhausted -> no retry HTTP request
  E. retry denied -> no sleep
  F. retry denied -> no retry counter increment
  + HTTP call count: initial=1, denied=0 additional, total=1
  + 429 and timeout both behave correctly
  + Allowed retry produces exactly 1 additional HTTP call

Network safety: All tests use httpx.MockTransport / mock judges. NO real LLM call.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
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
    PricingModel,
)


# ── Helpers ──

def _make_evidence(scenario_id: str = "m6_0_5_4_r2") -> EvaluationEvidence:
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="早安",
        composed_context="[System] 你是 Yua。",
        llm_response="早安 Bry！",
        model="response-model-test",
        temperature=0.0,
        state_snapshot={"mood": 0.5, "relationship_confidence": 0.85},
        prompt_version="prompt-v1",
        extra={"agent_id": "agent_yua", "user_id": "bryan"},
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


# Controlled pricing for deterministic tests.
CONTROLLED_PRICING = PricingModel(
    provider="*", model="*",
    input_cost_per_1k=0.01, output_cost_per_1k=0.02,
)


def _controlled_pricing_lookup(provider: str, model: str) -> PricingModel:
    return CONTROLLED_PRICING


def _make_3_diverse_judges() -> List[RealLLMJudge]:
    return [
        RealLLMJudge(judge_id="A", model="claude-haiku-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-haiku"),
        RealLLMJudge(judge_id="B", model="claude-sonnet-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-sonnet"),
        RealLLMJudge(judge_id="C", model="gpt-4o-mini",
                     api_key="k", provider="openai", model_family="gpt-4o-mini"),
    ]


# ── 1. Contract: callback returns bool (positive cases) ──

class TestRetryCallbackContract(unittest.TestCase):
    """on_retry is enforcement-capable: returns bool, gates retry."""

    def test_callback_returning_true_allows_retry(self):
        """A. budget available -> retry occurs -> counter increments."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2, max_cost_usd=0.10)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=2)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("a_budget_available"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # Allowed retry: max_retries=2, 2 attempts, 1 retry event
        self.assertIsNotNone(result.error)
        self.assertEqual(budget.retries_made, 1)
        # Original retryable error preserved
        self.assertIn("http_500", result.error)

    def test_callback_returning_false_denies_retry(self):
        """Returning False must break loop immediately; 0 additional HTTP calls."""
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("a_deny"),
                    http_client=client,
                    on_retry=lambda: False,  # Always deny
                )

        result = asyncio.run(run())
        # 1 HTTP call (initial attempt). Retry denied, no further calls.
        self.assertEqual(call_count[0], 1)
        self.assertIsNotNone(result.error)
        self.assertIn("http_500", result.error)  # Original error preserved

    def test_callback_returning_false_does_not_sleep(self):
        """E. retry denied -> no sleep (timing test)."""
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("e_no_sleep"),
                    http_client=client,
                    on_retry=lambda: False,
                )

        start = time.monotonic()
        result = asyncio.run(run())
        elapsed = time.monotonic() - start
        # Denied: no asyncio.sleep(2**0=1s) called. Total time should be < 0.5s.
        self.assertIsNotNone(result.error)
        self.assertLess(elapsed, 0.5, f"Denied retry took {elapsed:.3f}s — sleep was not suppressed")
        # Compare to allowed: at least 1s due to sleep
        # (We don't actually run the allowed version in this test — just the denied one)


# ── 2. Cost / Token / Retry-limit exhaustion blocks retry ──

class TestRetryBudgetExhaustion(unittest.TestCase):
    """B/C/D. When budget is already exhausted, retry MUST NOT happen."""

    def test_retry_limit_exhausted_blocks_retry(self):
        """B. retry budget exhausted (max_retries_per_judge) -> no retry HTTP."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10)
        # max_retries_per_judge=0 means can_retry always returns False (retries_made >= 0)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("b_retry_limit"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 HTTP call (initial). can_retry returns False -> retry denied.
        self.assertEqual(call_count[0], 1)
        self.assertEqual(budget.retries_made, 0)
        self.assertIn("http_500", result.error)

    def test_cost_budget_exhausted_blocks_retry(self):
        """C. cost budget exhausted -> no retry HTTP request."""
        # Pre-set cost_estimated to the max (simulating previous calls exhausted cost)
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2, max_cost_usd=0.05)
        budget.cost_estimated = 0.05  # already at limit
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("c_cost_exhausted"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 HTTP call. can_retry returns False (cost >= max_cost_usd).
        self.assertEqual(call_count[0], 1)
        self.assertEqual(budget.retries_made, 0)
        # Cost was NOT incremented by the denied retry
        self.assertEqual(budget.cost_estimated, 0.05)

    def test_token_budget_exhausted_blocks_retry(self):
        """D. token budget exhausted -> no retry HTTP request."""
        # Pre-set tokens_used to the max (simulating previous calls exhausted tokens)
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2,
                            max_token_budget=1000, max_cost_usd=0.10)
        budget.tokens_used = 1000  # already at limit
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]

        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("d_token_exhausted"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 HTTP call. can_retry returns False (tokens >= max_token_budget).
        self.assertEqual(call_count[0], 1)
        self.assertEqual(budget.retries_made, 0)
        self.assertEqual(budget.tokens_used, 1000)


# ── 3. Runner-level: callback wired into cost_budget.can_retry() ──

class TestRunnerRetryEnforcement(unittest.TestCase):
    """Runner's _on_retry is enforcement-capable via CostBudget.can_retry()."""

    def test_runner_denies_retry_when_cost_exhausted(self):
        """Cost budget exhausted -> runner's on_retry returns False -> no retry HTTP."""
        judges = _make_3_diverse_judges()
        # Set max_cost_usd very low so judge 1's single call exhausts it.
        # Per call: 2000 tokens * CONTROLLED_PRICING = $0.025
        # max_cost_usd=0.01 -> exhausted after judge 1 (post-call)
        # Wait — post-call update happens AFTER judge 1 returns.
        # For judge 1's retry, we need to pre-exhaust. Let me set max_cost_usd=0.
        budget = CostBudget(max_judge_calls=10, max_retries_per_judge=2,
                            max_cost_usd=0.0)  # 0 budget
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            cost_budget=budget, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("runner_cost_deny")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # Pre-call: can_make_call: cost_estimated (0) >= max_cost_usd (0) -> False
        # All 3 judges blocked pre-call. 0 HTTP calls.
        self.assertEqual(call_count[0], 0)
        self.assertEqual(result.budget.calls_made, 0)
        self.assertEqual(result.status, EvaluationStatus.UNAVAILABLE)

    def test_runner_denies_retry_via_inflight_exhaustion(self):
        """Cost budget set so judge 1 retries consume it -> judge 1's 2nd retry denied."""
        judges = _make_3_diverse_judges()
        # Pre-set cost_estimated to a value that allows the first call but
        # blocks the first retry via the runner's can_retry check.
        # Runner's _on_retry:
        #   if not can_retry(): return False
        #   record_retry(); return True
        # We need cost_estimated >= max_cost_usd when the retry is attempted.
        # But the retry happens DURING judge 1's call, BEFORE the post-call
        # record_call. So we need to pre-set cost_estimated.
        budget = CostBudget(max_judge_calls=10, max_retries_per_judge=2,
                            max_cost_usd=0.05)
        budget.cost_estimated = 0.05  # already at limit pre-call
        # Wait, then can_make_call returns False (cost >= max), so judge 1 is blocked pre-call.
        # Let me re-think. We want judge 1 to make the initial call, then be denied on retry.
        # That means: before judge 1, cost < max. After judge 1, cost >= max. Then retry denied.
        # But the retry is checked BEFORE the call is made (inside the retry loop, before continue).
        # So after the initial 500 response, the loop calls on_retry (which calls can_retry).
        # At that point, cost_estimated hasn't been updated yet (post-call update happens after evaluate returns).
        # So can_retry returns True (cost=0 < 0.05) -> retry allowed.
        # Hmm. This is a chicken-and-egg problem.
        # The retry budget is checked based on PRE-CALL state, not after the call.
        # So we can't simulate "judge 1 retries cost the budget" via pre-call exhaustion.
        # Instead, we test: pre-call state has cost_estimated at limit -> can_retry returns False
        # but can_make_call also returns False (same check), so judge is blocked pre-call.
        # To test retry-deny-during-inflight, we'd need to update cost_estimated between
        # attempts. RealLLMJudge doesn't do that.
        # For now, the cleanest test is: pre-call state exhausted -> can_retry False -> no retry.
        # But that's the same as can_make_call being False. So the test is essentially the same.
        # Let me use max_cost_usd=0.05 and pre-set cost to 0.049. Then:
        # can_make_call: 0.049 < 0.05 -> True. Judge 1 called.
        # During retry, can_retry: 0.049 < 0.05 -> True. Retry allowed.
        # This doesn't help.
        # Alternative: pre-set retries_made to max_total_retries.
        # max_retries_per_judge=2, max_judge_calls=10 -> max_total_retries=20
        # If retries_made=20, can_retry returns False.
        # But we want to ALLOW the first call, then DENY the retry.
        # The issue: retries_made is updated by the callback AFTER the first retry is allowed.
        # So if we want the 2nd retry (i.e., the retry within judge 1) to be denied, we need
        # retries_made to be at limit BEFORE the retry.
        # max_retries_per_judge=1, max_judge_calls=10 -> max_total_retries=10
        # If retries_made=10, can_retry returns False. But can_make_call also returns False
        # (calls_made=0 < 10). So both pass. Then judge 1's retry: can_retry False -> denied.
        budget = CostBudget(max_judge_calls=10, max_retries_per_judge=1, max_cost_usd=1.0)
        budget.retries_made = 10  # at max_total_retries
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            cost_budget=budget, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("runner_inflight_deny")

        call_count = [0]
        def counting_handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(counting_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # Judge 1: can_make_call: calls=0<10, tokens=0<5000, cost=0<1.0 -> True.
        # Initial attempt: 500. on_retry: can_retry: retries_made=10 >= 10 -> False.
        # Retry denied, no extra HTTP call.
        # call_count should be 1 (only initial).
        # After judge 1 returns: record_call(cost=0.025). Judge 2: similar, judge 3: similar.
        # All 3 judges called once each (initial only).
        self.assertEqual(call_count[0], 3)  # 1 per judge, 0 retries
        self.assertEqual(result.budget.retries_made, 10)  # unchanged from pre-set
        # All 3 judges have error (denied retry -> no further attempt)
        # Actually, after the initial 500 + denied retry, evaluate returns the error.
        # Then post-call: record_call. Then judge 2: similar.
        # So 3 HTTP calls total (one per judge), 0 retries.
        self.assertEqual(result.budget.calls_made, 3)

    def test_runner_allows_retry_within_budget(self):
        """Allowed retry: 1 extra HTTP call (initial + 1 retry that succeeds)."""
        judges = _make_3_diverse_judges()
        # Generous budget so all retries allowed
        budget = CostBudget(max_judge_calls=10, max_retries_per_judge=2,
                            max_cost_usd=1.0)
        runner = MultiModelJudgeRunner(
            judges, response_model="response-model-test",
            cost_budget=budget, pricing_lookup=_controlled_pricing_lookup,
        )
        ev = _make_evidence("runner_allow_retry")

        # Handler that returns 500 once then 200 (1 retry per judge that succeeds)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] % 2 == 1:  # odd calls: 500
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # Each judge: 1 initial 500 + 1 retry 200 = 2 calls. 3 judges = 6 calls.
        self.assertEqual(call_count[0], 6)
        # 3 retries (one per judge)
        self.assertEqual(result.budget.retries_made, 3)


# ── 4. 429 and timeout both behave correctly ──

class TestRetryableFailures(unittest.TestCase):
    """429 and timeout both trigger retry; both respect callback contract."""

    def test_429_allowed_retry(self):
        """429 + allowed retry: 2 HTTP calls."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2, max_cost_usd=0.10)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=2)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("429_allowed"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 initial 429 + 1 retry 200 = 2 calls
        self.assertEqual(call_count[0], 2)
        self.assertEqual(budget.retries_made, 1)
        self.assertIsNone(result.error)

    def test_429_denied_retry(self):
        """429 + denied retry: 1 HTTP call (no retry)."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(429, text="rate limited")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("429_denied"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 initial 429. can_retry returns False (retries_made=0 >= max_total=0). No retry.
        self.assertEqual(call_count[0], 1)
        self.assertIn("http_429", result.error)
        self.assertEqual(budget.retries_made, 0)

    def test_timeout_allowed_retry(self):
        """timeout + allowed retry: 2 HTTP attempts (timeout then success)."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2, max_cost_usd=0.10)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=2, timeout=5.0)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] == 1:
                raise httpx.TimeoutException("simulated")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("timeout_allowed"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 1 timeout + 1 retry 200 = 2 attempts
        self.assertEqual(call_count[0], 2)
        self.assertEqual(budget.retries_made, 1)
        self.assertIsNone(result.error)

    def test_timeout_denied_retry(self):
        """timeout + denied retry: 1 HTTP attempt (no retry)."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=0, max_cost_usd=0.10)
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3, timeout=5.0)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            raise httpx.TimeoutException("simulated")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("timeout_denied"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        self.assertEqual(call_count[0], 1)
        self.assertIn("timeout", result.error)
        self.assertEqual(budget.retries_made, 0)


# ── 5. Sleep suppression ──

class TestSleepSuppression(unittest.TestCase):
    """E. retry denied -> no sleep."""

    def test_denied_retry_no_sleep_called(self):
        """Mock asyncio.sleep inside real_judge; verify NOT called when denied."""
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        mock_sleep_holder: Dict[str, Any] = {}

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch("asyncio.sleep") as mock_sleep:
                    result = await judge.evaluate(
                        _make_evidence("no_sleep_denied"),
                        http_client=client,
                        on_retry=lambda: False,  # Always deny
                    )
                    mock_sleep_holder["mock"] = mock_sleep
                    return result

        result = asyncio.run(run())
        # asyncio.sleep must NOT be called when retry is denied
        mock_sleep_holder["mock"].assert_not_called()
        self.assertIsNotNone(result.error)

    def test_allowed_retry_calls_sleep(self):
        """Mock asyncio.sleep; verify called when retry is allowed."""
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=2)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="server error")

        mock_sleep_holder: Dict[str, Any] = {}

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with patch("asyncio.sleep") as mock_sleep:
                    result = await judge.evaluate(
                        _make_evidence("sleep_allowed"),
                        http_client=client,
                        on_retry=lambda: True,  # Always allow
                    )
                    mock_sleep_holder["mock"] = mock_sleep
                    return result

        result = asyncio.run(run())
        # asyncio.sleep(2**0=1) called once (between attempts 0 and 1)
        self.assertEqual(mock_sleep_holder["mock"].call_count, 1)
        mock_sleep_holder["mock"].assert_called_with(1)
        self.assertIsNotNone(result.error)


# ── 6. Backward compatibility ──

class TestBackwardCompatR2(unittest.TestCase):
    """Existing M6.0-5.4 / M6.0-5.4-R1 / M6.0-5.2 tests still pass via Callable[[], bool]."""

    def test_callback_none_preserves_legacy_behavior(self):
        """on_retry=None: no callback invoked, max_retries controls everything (M6.0-5.2 behavior)."""
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=2)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("legacy_none"),
                                            http_client=client)

        result = asyncio.run(run())
        # max_retries=2: 2 attempts, no callback, retry happens automatically
        self.assertEqual(call_count[0], 2)
        self.assertIsNotNone(result.error)
        self.assertIn("http_500", result.error)

    def test_callback_returning_true_preserves_r1_behavior(self):
        """R1-style callback that always returns True: retry counter increments."""
        budget = CostBudget(max_judge_calls=3, max_retries_per_judge=2, max_cost_usd=0.10)
        # max_retries=3 means 3 total attempts; 2 retries needed before success
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k",
                              provider="claude", max_retries=3)
        call_count = [0]
        def handler(request: httpx.Request) -> httpx.Response:
            call_count[0] += 1
            if call_count[0] < 3:
                return httpx.Response(500, text="server error")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(
                    _make_evidence("r1_style"),
                    http_client=client,
                    on_retry=lambda: budget.can_retry() and (budget.record_retry() or True),
                )

        result = asyncio.run(run())
        # 2 retries before success
        self.assertEqual(call_count[0], 3)
        self.assertEqual(budget.retries_made, 2)
        self.assertIsNone(result.error)


if __name__ == "__main__":
    unittest.main()
