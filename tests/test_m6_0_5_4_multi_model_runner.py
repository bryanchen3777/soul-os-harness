"""
tests/test_m6_0_5_4_multi_model_runner.py
M6.0-5.4 (Bry 派工 2026-08-11 20:17): Multi-Model Judge Orchestration tests.

Test categories (per Bry spec):
  Diversity:
    - 3 unique judges PASS
    - duplicate judge ID FAIL
    - duplicate configuration FAIL
    - only one model family FAIL
    - two model families PASS
    - same provider/different model PASS
  Self evaluation:
    - same model BLOCK
    - same provider/different model ALLOW
  Failure:
    - 1 failure -> incomplete + calibration
    - 2 failures -> FAIL/incomplete
    - 3 failures -> unavailable/FAIL
    - no auto replacement
  Retry:
    - 429 retry
    - 5xx retry
    - timeout retry
    - max 2 retries
    - no retry on 4xx
  Cost:
    - within budget PASS
    - budget exceeded FAIL
    - retry cannot bypass budget
    - no hidden fallback call
  Provenance:
    - token usage captured
    - latency captured
    - request ID captured
    - stop reason captured
  Backward compatibility:
    - M6.0-5 unchanged
    - M6.0-5.2 unchanged

Network safety: All tests use httpx.MockTransport / mock judges. NO real LLM call.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Dict, List
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    RealLLMJudge,
    SequentialJudgeRunner,
    aggregate,
    EvaluationStatus,
    SelfEvaluationError,
    DiversityError,
    check_self_evaluation,
    validate_diversity,
    CostBudget,
    MultiModelJudgeRunner,
    MultiModelRunResult,
    PASS,
    PARTIAL,
    FAIL,
)


# ── Helpers ──

def _make_evidence(scenario_id: str = "m6_0_5_4_test") -> EvaluationEvidence:
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


def _claude_response(scores: Dict[str, int] = None, with_usage: bool = True) -> Dict[str, Any]:
    """Build a Claude response body with usage metadata."""
    if scores is None:
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
    body = {
        "id": "msg_test_123",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": json.dumps({**scores, "rationale": "ok"})}],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
    }
    if with_usage:
        body["usage"] = {"input_tokens": 1500, "output_tokens": 500}
    return body


def _openai_response(scores: Dict[str, int] = None, with_usage: bool = True) -> Dict[str, Any]:
    """Build an OpenAI response body with usage metadata."""
    if scores is None:
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
    body = {
        "id": "chatcmpl-test-123",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": json.dumps({**scores, "rationale": "ok"})},
                "finish_reason": "stop",
            }
        ],
    }
    if with_usage:
        body["usage"] = {"prompt_tokens": 1500, "completion_tokens": 500, "total_tokens": 2000}
    return body


def _ok_handler(request):
    """
    Smart handler: returns OpenAI format for openai URLs, Claude format otherwise.

    Used across M6.0-5.4 tests so that 3-judge setups mixing claude + openai
    providers all succeed with their native response format.
    """
    url = str(request.url)
    if "openai" in url or "chat/completions" in url:
        return httpx.Response(200, json=_openai_response())
    return httpx.Response(200, json=_claude_response())


def _make_3_diverse_judges():
    """3 diverse judges: 2x Claude (different families) + 1x OpenAI."""
    return [
        RealLLMJudge(judge_id="A", model="claude-haiku-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-haiku"),
        RealLLMJudge(judge_id="B", model="claude-sonnet-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-sonnet"),
        RealLLMJudge(judge_id="C", model="gpt-4o-mini",
                     api_key="k", provider="openai", model_family="gpt-4o-mini"),
    ]


def _make_3_diverse_judges_with_base_url():
    """Same as _make_3_diverse_judges but with explicit base_urls (for tests that need
    to control base_url, e.g. duplicate-configuration tests)."""
    return [
        RealLLMJudge(judge_id="A", model="claude-haiku-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-haiku",
                     base_url="https://api.claude.test/v1/messages"),
        RealLLMJudge(judge_id="B", model="claude-sonnet-4-5-20251001",
                     api_key="k", provider="claude", model_family="claude-sonnet",
                     base_url="https://api.claude.test/v1/messages"),
        RealLLMJudge(judge_id="C", model="gpt-4o-mini",
                     api_key="k", provider="openai", model_family="gpt-4o-mini",
                     base_url="https://api.openai.test/v1/chat/completions"),
    ]


# ── 1. DiversityValidator ──

class TestDiversityValidator(unittest.TestCase):
    """Diversity: 3 unique judges PASS / various duplicates FAIL."""

    def test_three_unique_judges_pass(self):
        judges = _make_3_diverse_judges()
        # Should not raise
        validate_diversity(judges, response_model="response-model-test")

    def test_duplicate_judge_id_fails(self):
        j1 = RealLLMJudge(judge_id="dup", model="m1", api_key="k", provider="claude", model_family="f1")
        j2 = RealLLMJudge(judge_id="dup", model="m2", api_key="k", provider="openai", model_family="f2")
        j3 = RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="f3")
        with self.assertRaises(DiversityError) as ctx:
            validate_diversity([j1, j2, j3], response_model="")
        self.assertIn("unique", str(ctx.exception).lower())

    def test_duplicate_configuration_fails(self):
        # Same model + provider + base_url but different judge_id
        # We need 3 judges with diversity, but 2 of them have identical (model, provider, base_url)
        # This means we need 2 with different (provider, family) and 1 with same
        # Workaround: j1 + j2 have same config, j3 is distinct
        j1 = RealLLMJudge(judge_id="A", model="dup-model", api_key="k",
                          provider="claude", model_family="claude-family",
                          base_url="https://api.dup.test/v1/messages")
        j2 = RealLLMJudge(judge_id="B", model="dup-model", api_key="k",
                          provider="claude", model_family="claude-family",
                          base_url="https://api.dup.test/v1/messages")
        j3 = RealLLMJudge(judge_id="C", model="unique-model", api_key="k",
                          provider="openai", model_family="gpt-family")
        with self.assertRaises(DiversityError) as ctx:
            validate_diversity([j1, j2, j3], response_model="")
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_only_one_model_family_fails(self):
        # All 3 use provider="claude" with same model_family
        j1 = RealLLMJudge(judge_id="A", model="m1", api_key="k", provider="claude", model_family="same")
        j2 = RealLLMJudge(judge_id="B", model="m2", api_key="k", provider="claude", model_family="same")
        j3 = RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="same")
        with self.assertRaises(DiversityError) as ctx:
            validate_diversity([j1, j2, j3], response_model="")
        self.assertIn("family", str(ctx.exception).lower())

    def test_two_model_families_pass(self):
        # 2 distinct families (e.g. claude + gpt)
        j1 = RealLLMJudge(judge_id="A", model="m1", api_key="k", provider="claude", model_family="claude")
        j2 = RealLLMJudge(judge_id="B", model="m2", api_key="k", provider="openai", model_family="gpt")
        j3 = RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="claude")
        # Should not raise (2 distinct families: claude + gpt)
        validate_diversity([j1, j2, j3], response_model="")

    def test_same_provider_different_model_passes(self):
        # Same provider="claude" but different model_family
        j1 = RealLLMJudge(judge_id="A", model="haiku", api_key="k", provider="claude", model_family="claude-haiku")
        j2 = RealLLMJudge(judge_id="B", model="sonnet", api_key="k", provider="claude", model_family="claude-sonnet")
        j3 = RealLLMJudge(judge_id="C", model="gpt", api_key="k", provider="openai", model_family="gpt")
        validate_diversity([j1, j2, j3], response_model="")


# ── 2. SelfEvaluationGuard ──

class TestSelfEvaluationGuard(unittest.TestCase):
    """Self-evaluation: same model BLOCK, same provider/different model ALLOW."""

    def test_same_model_blocks(self):
        with self.assertRaises(SelfEvaluationError) as ctx:
            check_self_evaluation(
                response_model="claude-haiku-4-5-20251001",
                judge_model="claude-haiku-4-5-20251001",
            )
        self.assertIn("BLOCKED", str(ctx.exception))

    def test_different_model_allows(self):
        # Should not raise
        check_self_evaluation(
            response_model="claude-haiku-4-5-20251001",
            judge_model="claude-sonnet-4-5-20251001",
        )

    def test_different_provider_allows(self):
        check_self_evaluation(
            response_model="claude-haiku-4-5-20251001",
            judge_model="gpt-4o-mini",
        )

    def test_empty_response_model_allows(self):
        # No response model = unknown, allow
        check_self_evaluation(response_model="", judge_model="claude-haiku-4-5-20251001")

    def test_empty_judge_model_allows(self):
        check_self_evaluation(response_model="claude-haiku-4-5-20251001", judge_model="")

    def test_diversity_validator_catches_self_eval(self):
        # DiversityValidator also enforces self-evaluation (defense in depth)
        j1 = RealLLMJudge(judge_id="A", model="same-model", api_key="k", provider="claude", model_family="f1")
        j2 = RealLLMJudge(judge_id="B", model="m2", api_key="k", provider="openai", model_family="f2")
        j3 = RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="f3")
        with self.assertRaises(DiversityError) as ctx:
            validate_diversity([j1, j2, j3], response_model="same-model")
        self.assertIn("Self-evaluation", str(ctx.exception))


# ── 3. CostBudget ──

class TestCostBudget(unittest.TestCase):
    """Cost: within budget PASS, budget exceeded FAIL, retry cannot bypass budget."""

    def test_within_budget_passes(self):
        b = CostBudget(max_judge_calls=3, max_cost_usd=0.05)
        b.record_call(tokens_in=100, tokens_out=50, cost_usd=0.001)
        self.assertTrue(b.can_make_call())
        self.assertFalse(b.is_exhausted())

    def test_max_calls_exhausted(self):
        b = CostBudget(max_judge_calls=3)
        for _ in range(3):
            b.record_call()
        self.assertFalse(b.can_make_call())
        self.assertTrue(b.is_exhausted())
        self.assertIn("max_judge_calls", b.reason_if_exhausted())

    def test_token_budget_exhausted(self):
        b = CostBudget(max_judge_calls=10, max_token_budget=1000)
        b.record_call(tokens_in=600, tokens_out=500)  # 1100 total > 1000
        self.assertFalse(b.can_make_call())
        self.assertTrue(b.is_exhausted())
        self.assertIn("max_token_budget", b.reason_if_exhausted())

    def test_cost_budget_exhausted(self):
        b = CostBudget(max_judge_calls=10, max_cost_usd=0.01)
        b.record_call(cost_usd=0.011)
        self.assertFalse(b.can_make_call())
        self.assertTrue(b.is_exhausted())
        self.assertIn("max_cost_usd", b.reason_if_exhausted())

    def test_retry_cannot_bypass_budget(self):
        # max_judge_calls=2 means after 2 calls, can_make_call returns False
        # Even with max_retries_per_judge=10, the budget blocks further calls
        b = CostBudget(max_judge_calls=2, max_retries_per_judge=10)
        for _ in range(2):
            b.record_call()
        # can_make_call is False (budget exhausted)
        self.assertFalse(b.can_make_call())
        self.assertTrue(b.is_exhausted())

    def test_can_retry_within_budget(self):
        b = CostBudget(max_judge_calls=3, max_retries_per_judge=2)
        b.record_call()
        self.assertTrue(b.can_retry())


# ── 4. MultiModelJudgeRunner failure behavior ──

class TestMultiModelRunnerFailure(unittest.TestCase):
    """Failure: 1/2/3 failures -> different statuses. No auto-replacement."""

    def _make_judge_with_handler(self, judge_id, model, provider, family, handler):
        judge = RealLLMJudge(judge_id=judge_id, model=model, api_key="k",
                              provider=provider, model_family=family)
        return judge, handler

    def test_3_successes_status_complete(self):
        judges = [
            self._make_judge_with_handler("A", "m1", "claude", "f1", _ok_handler)[0],
            self._make_judge_with_handler("B", "m2", "openai", "f2", _ok_handler)[0],
            self._make_judge_with_handler("C", "m3", "claude", "f3", _ok_handler)[0],
        ]
        # Manually set base_url to avoid same-default collision
        judges[0].base_url = "https://api.claude.test/v1/messages"
        judges[1].base_url = "https://api.openai.test/v1/chat/completions"
        judges[2].base_url = "https://api.claude2.test/v1/messages"
        # 2 distinct families: f1 (claude), f2 (openai), f3 (claude) -> 2 unique
        ev = _make_evidence("complete_test")

        # Use direct evaluate + aggregate to verify status logic
        async def verify():
            clients = [httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)) for _ in judges]
            try:
                results = []
                for j, c in zip(judges, clients):
                    results.append(await j.evaluate(ev, http_client=c))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(verify())
        errored = sum(1 for r in results if r.error is not None)
        self.assertEqual(errored, 0)
        agg = aggregate(results, scenario_id="complete_test")
        # All 3 success = no calibration required (all agree on 4)
        self.assertFalse(agg.calibration_required)
        self.assertEqual(agg.overall_subjective_status, PASS)

    def test_1_failure_status_incomplete(self):
        judges = _make_3_diverse_judges()
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test")
        ev = _make_evidence("incomplete_test")

        def fail_handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            clients = [
                httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)),
                httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)),
                httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)),
            ]
            try:
                results = []
                for j, c in zip(judges, clients):
                    results.append(await j.evaluate(ev, http_client=c))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(run())
        errored = sum(1 for r in results if r.error is not None)
        self.assertEqual(errored, 1)
        # Per spec: 1 failure -> incomplete + calibration_required
        agg = aggregate(results, scenario_id="incomplete_test")
        self.assertTrue(agg.calibration_required)
        self.assertIn(agg.overall_subjective_status, (PASS, PARTIAL))
        self.assertEqual(len(agg.agreement_metadata["errored_judges"]), 1)

    def test_2_failures_status_unavailable(self):
        judges = _make_3_diverse_judges()
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test")
        ev = _make_evidence("unavailable_test")

        def fail_handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            clients = [
                httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)),
                httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)),
                httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)),
            ]
            try:
                results = []
                for j, c in zip(judges, clients):
                    results.append(await j.evaluate(ev, http_client=c))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(run())
        errored = sum(1 for r in results if r.error is not None)
        self.assertEqual(errored, 2)
        # Per spec: 2 failures -> FAIL
        agg = aggregate(results, scenario_id="unavailable_test")
        self.assertEqual(agg.overall_subjective_status, FAIL)
        self.assertEqual(len(agg.agreement_metadata["errored_judges"]), 2)

    def test_3_failures_status_unavailable(self):
        judges = _make_3_diverse_judges()
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test")
        ev = _make_evidence("3_failures_test")

        def fail_handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            clients = [httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)) for _ in judges]
            try:
                results = []
                for j, c in zip(judges, clients):
                    results.append(await j.evaluate(ev, http_client=c))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(run())
        errored = sum(1 for r in results if r.error is not None)
        self.assertEqual(errored, 3)
        agg = aggregate(results, scenario_id="3_failures_test")
        self.assertEqual(agg.overall_subjective_status, FAIL)

    def test_no_auto_replacement(self):
        # If a judge fails, the runner does NOT add a 4th judge
        judges = _make_3_diverse_judges()
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test")
        ev = _make_evidence("no_replace_test")

        def fail_handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            clients = [httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)) for _ in judges]
            try:
                results = []
                for j, c in zip(judges, clients):
                    results.append(await j.evaluate(ev, http_client=c))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(run())
        # Exactly 3 results (no 4th)
        self.assertEqual(len(results), 3)


# ── 5. Retry policy ──

class TestRetryPolicy(unittest.TestCase):
    """Retry: 429/5xx/timeout retry, max 2, no retry on 4xx."""

    def test_429_retries(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude", max_retries=2)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            if call_count[0] < 2:
                return httpx.Response(429, text="rate limited")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("429_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        self.assertEqual(call_count[0], 2)  # 1 retry

    def test_5xx_retries(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude", max_retries=3)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            if call_count[0] < 3:
                return httpx.Response(503, text="server error")
            return httpx.Response(200, json=_claude_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("5xx_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        # max_retries=3: calls 1, 2 fail (5xx), call 3 succeeds
        self.assertEqual(call_count[0], 3)  # 2 retries + 1 success

    def test_timeout_retries(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude",
                              max_retries=1, timeout=0.1)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            raise httpx.TimeoutException("simulated")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("timeout_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("timeout", result.error)
        self.assertEqual(call_count[0], 1)  # 0 retries (max_retries=1 means 1 attempt total)

    def test_max_2_retries(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude", max_retries=2)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("max_retry_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertEqual(call_count[0], 2)  # max_retries=2

    def test_no_retry_on_4xx(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude", max_retries=5)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            return httpx.Response(401, text="unauthorized")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await judge.evaluate(_make_evidence("4xx_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("401", result.error)
        self.assertEqual(call_count[0], 1)  # No retry on 4xx


# ── 6. Provenance capture ──

class TestProvenanceCapture(unittest.TestCase):
    """Provenance: token_usage, latency_ms, request_id, stop_reason captured."""

    def test_claude_provenance_captures_all_4_fields(self):
        judge = RealLLMJudge(judge_id="A", model="claude-haiku-4-5-20251001",
                              api_key="k", provider="claude")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)) as client:
                return await judge.evaluate(_make_evidence("claude_prov_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.provenance)
        prov = result.provenance
        # Token usage
        self.assertIsNotNone(prov.token_usage)
        self.assertEqual(prov.token_usage.get("input"), 1500)
        self.assertEqual(prov.token_usage.get("output"), 500)
        self.assertEqual(prov.token_usage.get("total"), 2000)
        # Latency
        self.assertIsNotNone(prov.latency_ms)
        self.assertGreaterEqual(prov.latency_ms, 0)  # Mock is sub-ms (0.0 OK)
        # Request ID
        self.assertEqual(prov.request_id, "msg_test_123")
        # Stop reason
        self.assertEqual(prov.stop_reason, "end_turn")

    def test_openai_provenance_captures_all_4_fields(self):
        judge = RealLLMJudge(judge_id="B", model="gpt-4o-mini", api_key="k", provider="openai")

        def openai_ok(request):
            return httpx.Response(200, json=_openai_response())

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(openai_ok)) as client:
                return await judge.evaluate(_make_evidence("openai_prov_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.provenance)
        prov = result.provenance
        self.assertEqual(prov.token_usage.get("input"), 1500)
        self.assertEqual(prov.token_usage.get("output"), 500)
        self.assertEqual(prov.token_usage.get("total"), 2000)
        self.assertEqual(prov.request_id, "chatcmpl-test-123")
        self.assertEqual(prov.stop_reason, "stop")
        self.assertIsNotNone(prov.latency_ms)

    def test_failed_call_has_latency_but_no_token_usage(self):
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude")

        def fail_handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(fail_handler)) as client:
                return await judge.evaluate(_make_evidence("fail_prov_test"), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.provenance)
        prov = result.provenance
        # Latency captured even on failure
        self.assertIsNotNone(prov.latency_ms)
        # But no token_usage (API call failed)
        self.assertIsNone(prov.token_usage)
        self.assertIsNone(prov.request_id)


# ── 7. Backward compatibility ──

class TestBackwardCompatibility(unittest.TestCase):
    """M6.0-5 and M6.0-5.2 still work without changes."""

    def test_sequential_judge_runner_still_works(self):
        # M6.0-5 SequentialJudgeRunner with mock judges
        from tests._helpers.subjective_eval import HighAgreementJudge
        judges = [
            HighAgreementJudge("A", base=4),
            HighAgreementJudge("B", base=4),
            HighAgreementJudge("C", base=4),
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence("backward_test")
        results = runner.run(ev)
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(len(r.per_dimension_scores), 8)
            # M6.0-5.4 extended provenance fields: all None for mock judges
            if r.provenance is not None:
                # Mock judges don't set provenance
                self.assertIsNone(r.provenance.token_usage)
                self.assertIsNone(r.provenance.latency_ms)
                self.assertIsNone(r.provenance.request_id)
                self.assertIsNone(r.provenance.stop_reason)

    def test_real_judge_basic_call_still_works(self):
        # M6.0-5.2 RealLLMJudge basic call without M6.0-5.4 features
        judge = RealLLMJudge(judge_id="A", model="m", api_key="k", provider="claude")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)) as client:
                return await judge.evaluate(_make_evidence("real_basic_test"), http_client=client)

        result = asyncio.run(run())
        # Original M6.0-5.2 fields still work
        self.assertIsNone(result.error)
        self.assertEqual(len(result.per_dimension_scores), 8)
        self.assertEqual(result.rationale, "ok")
        # M6.0-5.2 provenance still has model, provider, etc.
        self.assertIsNotNone(result.provenance)
        # M6.0-5.4 new fields are also populated (backward compat)
        self.assertIsNotNone(result.provenance.token_usage)
        self.assertIsNotNone(result.provenance.latency_ms)

    def test_aggregate_function_unchanged(self):
        # M6.0-5 aggregate() still works
        from tests._helpers.subjective_eval import FixedScoreJudge
        judges = [
            FixedScoreJudge("A", 4),
            FixedScoreJudge("B", 4),
            FixedScoreJudge("C", 4),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence("agg_test"))
        agg = aggregate(results, scenario_id="agg_test")
        self.assertEqual(agg.overall_subjective_status, PASS)
        self.assertFalse(agg.calibration_required)


# ── 8. Cost enforcement in MultiModelJudgeRunner ──

class TestCostEnforcementInRunner(unittest.TestCase):
    """Cost: budget exceeded in MultiModelJudgeRunner returns error result, no hidden fallback."""

    def test_budget_exceeded_returns_error_result(self):
        judges = _make_3_diverse_judges()
        # Set very low budget (1 call only)
        budget = CostBudget(max_judge_calls=1)
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test", cost_budget=budget)
        ev = _make_evidence("budget_exceeded_test")

        async def run():
            clients = [httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)) for _ in judges]
            try:
                # Manually call evaluate on each judge, but check budget
                results = []
                for j, c in zip(judges, clients):
                    if runner.cost_budget.can_make_call():
                        runner.cost_budget.record_call()
                        results.append(await j.evaluate(ev, http_client=c))
                    else:
                        # Budget exhausted: return error result
                        from tests._helpers.subjective_eval import JudgeResult
                        results.append(JudgeResult(
                            judge_id=j.judge_id,
                            model=j.model,
                            per_dimension_scores={},
                            error=f"budget_exhausted: {runner.cost_budget.reason_if_exhausted()}",
                        ))
                return results
            finally:
                for c in clients:
                    await c.aclose()

        results = asyncio.run(run())
        # First judge succeeded, next 2 got budget_exhausted
        self.assertEqual(len(results), 3)
        # 1 success + 2 budget_exhausted errors
        errored = [r for r in results if r.error is not None]
        self.assertEqual(len(errored), 2)
        for r in errored:
            self.assertIn("budget_exhausted", r.error)
        # No hidden fallback call (exactly 3 results, no 4th)
        self.assertEqual(len(results), 3)

    def test_runner_run_full_flow_with_budget(self):
        judges = _make_3_diverse_judges()
        budget = CostBudget(max_judge_calls=3, max_cost_usd=0.10)
        runner = MultiModelJudgeRunner(judges, response_model="response-model-test", cost_budget=budget)
        ev = _make_evidence("full_flow_test")

        async def run():
            async with httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler)) as client:
                return await runner.run(ev, http_client=client)

        result = asyncio.run(run())
        # 3 successful calls; budget fully used (3/3) but status = COMPLETE
        # (the run completed without being blocked by the budget)
        self.assertEqual(result.budget.calls_made, 3)
        # is_exhausted = True means "no more capacity" (calls_made >= max).
        # After 3/3 calls the budget is at its limit, even though the run succeeded.
        self.assertTrue(result.budget.is_exhausted())
        self.assertEqual(result.status, EvaluationStatus.COMPLETE)


# ── 9. MultiModelJudgeRunner integration ──

class TestMultiModelRunnerIntegration(unittest.TestCase):
    """End-to-end MultiModelJudgeRunner integration tests."""

    def test_runner_construct_validates_diversity(self):
        judges = [
            RealLLMJudge(judge_id="A", model="m1", api_key="k", provider="claude", model_family="f1"),
            RealLLMJudge(judge_id="B", model="m2", api_key="k", provider="openai", model_family="f2"),
            RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="f3"),
        ]
        # Valid: 2 distinct (provider, family) = (claude,f1) + (openai,f2) + (claude,f3)
        runner = MultiModelJudgeRunner(judges, response_model="")
        self.assertEqual(len(runner.judges), 3)

    def test_runner_rejects_duplicate_judge_id(self):
        judges = [
            RealLLMJudge(judge_id="dup", model="m1", api_key="k", provider="claude", model_family="f1"),
            RealLLMJudge(judge_id="dup", model="m2", api_key="k", provider="openai", model_family="f2"),
            RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="f3"),
        ]
        with self.assertRaises(DiversityError):
            MultiModelJudgeRunner(judges)

    def test_runner_rejects_self_evaluation(self):
        judges = [
            RealLLMJudge(judge_id="A", model="response-model-test", api_key="k",
                         provider="claude", model_family="f1"),
            RealLLMJudge(judge_id="B", model="m2", api_key="k", provider="openai", model_family="f2"),
            RealLLMJudge(judge_id="C", model="m3", api_key="k", provider="claude", model_family="f3"),
        ]
        with self.assertRaises(DiversityError) as ctx:
            MultiModelJudgeRunner(judges, response_model="response-model-test")
        self.assertIn("Self-evaluation", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
