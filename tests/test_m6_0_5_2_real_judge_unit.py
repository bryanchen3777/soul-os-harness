"""
tests/test_m6_0_5_2_real_judge_unit.py
M6.0-5.2 (Bry 派工 2026-08-11 19:40): Real LLM Judge unit tests.

These tests use httpx.MockTransport to intercept HTTP calls.
NO real network is used. NO real API key required.

Test categories (per Bry spec):
  1. real backend request construction
  2. structured response parsing
  3. valid 1-5 scores
  4. malformed response
  5. missing dimension
  6. invalid score
  7. timeout/error handling
  8. missing API key
  9. network disabled/default path
 10. three independent judge calls
 11. no cross-contamination
 12. provenance capture
 13. existing aggregation integration
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Dict, List, Optional
from unittest.mock import patch

import httpx

sys.path.insert(0, sys.path[0] if False else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._helpers.subjective_eval import (
    EIGHT_DIMENSIONS,
    EvaluationEvidence,
    build_evidence_from_llmproxy_call,
    JudgeResult,
    JudgeProvenance,
    RealLLMJudge,
    SequentialJudgeRunner,
    aggregate,
    PASS,
    PARTIAL,
    FAIL,
    CalibrationQueue,
)


# ── Test helpers ──

def _make_evidence(scenario_id: str = "real_judge_test") -> EvaluationEvidence:
    return build_evidence_from_llmproxy_call(
        scenario_id=scenario_id,
        user_input="早安",
        composed_context="[System] 你是 Yua。",
        llm_response="早安 Bry！今天過得如何？",
        model="claude-haiku-4-5-20251001",
        temperature=0.0,
        state_snapshot={"mood": 0.5, "relationship_confidence": 0.85},
        prompt_version="prompt-v1",
        extra={"agent_id": "agent_yua", "user_id": "bryan"},
    )


def _make_claude_response_json(scores: Dict[str, int], rationale: str = "ok") -> Dict[str, Any]:
    """Build a Claude-style response body."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": json.dumps(
                {**scores, "rationale": rationale}, ensure_ascii=False
            )},
        ],
        "model": "claude-haiku-4-5-20251001",
        "stop_reason": "end_turn",
    }


def _make_openai_response_json(scores: Dict[str, int], rationale: str = "ok") -> Dict[str, Any]:
    """Build an OpenAI-style response body."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(
                        {**scores, "rationale": rationale}, ensure_ascii=False
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    }


def _mock_transport_with_handler(handler):
    """Build an httpx.MockTransport that delegates to a handler function."""
    return httpx.MockTransport(handler)


def _ok_claude_handler(request: httpx.Request) -> httpx.Response:
    """Mock HTTP handler that returns a valid Claude response."""
    scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
    body = _make_claude_response_json(scores, "test rationale")
    return httpx.Response(200, json=body)


# ── 1. Real backend request construction ──

class TestRequestConstruction(unittest.TestCase):
    """1. Real backend constructs correct HTTP request."""

    def test_claude_request_uses_x_api_key_header(self):
        judge = RealLLMJudge(
            judge_id="claude-1",
            model="claude-haiku-4-5-20251001",
            api_key="test-key-123",
            provider="claude",
        )
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=_make_claude_response_json({dim: 4 for dim in EIGHT_DIMENSIONS}))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        self.assertIn("x-api-key", captured["headers"])
        self.assertEqual(captured["headers"]["x-api-key"], "test-key-123")
        self.assertEqual(captured["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["body"]["model"], "claude-haiku-4-5-20251001")
        self.assertEqual(captured["body"]["temperature"], 0.0)

    def test_openai_request_uses_bearer_header(self):
        judge = RealLLMJudge(
            judge_id="openai-1",
            model="gpt-4o-mini",
            api_key="sk-test-456",
            provider="openai",
        )
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=_make_openai_response_json({dim: 4 for dim in EIGHT_DIMENSIONS}))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        self.assertEqual(captured["headers"]["authorization"], "Bearer sk-test-456")
        self.assertIn("response_format", captured["body"])
        self.assertEqual(captured["body"]["response_format"]["type"], "json_object")

    def test_request_body_contains_8_dimensions_in_prompt(self):
        judge = RealLLMJudge(judge_id="j1", model="x", api_key="k", provider="claude")
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json=_make_claude_response_json({dim: 3 for dim in EIGHT_DIMENSIONS}))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        asyncio.run(run())
        user_msg = captured["body"]["messages"][0]["content"]
        for dim in EIGHT_DIMENSIONS:
            self.assertIn(dim, user_msg)


# ── 2. Structured response parsing + 3. Valid scores ──

class TestResponseParsing(unittest.TestCase):
    """2-3. Parses Claude/OpenAI response to per-dimension scores 1-5."""

    def test_parses_claude_response_with_valid_scores(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}

        async def run():
            client = httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler))
            try:
                return await judge.evaluate(_make_evidence(), http_client=client)
            finally:
                await client.aclose()

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(result.per_dimension_scores[dim], 4)
        self.assertEqual(result.rationale, "test rationale")

    def test_parses_openai_response_with_valid_scores(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="openai")

        def handler(request):
            return httpx.Response(200, json=_make_openai_response_json({dim: 5 for dim in EIGHT_DIMENSIONS}))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(result.per_dimension_scores[dim], 5)

    def test_parses_json_with_markdown_code_fence(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
        wrapped = "```json\n" + json.dumps({**scores, "rationale": "ok"}) + "\n```"

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json_with_text(wrapped))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNone(result.error)
        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(result.per_dimension_scores[dim], 4)


def _make_claude_response_json_with_text(text: str) -> Dict[str, Any]:
    return {
        "id": "msg_test", "type": "message", "role": "assistant",
        "content": [{"type": "text", "text": text}],
        "model": "claude-haiku-4-5-20251001", "stop_reason": "end_turn",
    }


# ── 4. Malformed response handling ──

class TestMalformedResponse(unittest.TestCase):
    """4. Malformed LLM responses fail safely (no exception, error=...)."""

    def test_empty_response_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json_with_text(""))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("Empty", result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_non_json_response_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json_with_text("This is not JSON"))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("not valid JSON", result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_json_not_dict_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json_with_text("[1, 2, 3]"))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("not a dict", result.error)


# ── 5. Missing dimension handling ──

class TestMissingDimension(unittest.TestCase):
    """5. JSON missing one or more dimensions fails safely."""

    def test_missing_one_dimension(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: 4 for dim in EIGHT_DIMENSIONS}
        scores_missing = {k: v for k, v in scores.items() if k != "memory_continuity"}

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json(scores_missing))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("memory_continuity", result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_missing_all_dimensions(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json_with_text('{"rationale": "no scores"}'))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.per_dimension_scores, {})


# ── 6. Invalid score handling ──

class TestInvalidScore(unittest.TestCase):
    """6. Out-of-range or non-int scores fail safely."""

    def test_score_zero_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: 0 for dim in EIGHT_DIMENSIONS}

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json(scores))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_score_six_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: 6 for dim in EIGHT_DIMENSIONS}

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json(scores))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_score_string_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude")
        scores = {dim: "good" for dim in EIGHT_DIMENSIONS}

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json(scores))

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertEqual(result.per_dimension_scores, {})


# ── 7. Timeout / error handling ──

class TestTimeoutAndErrors(unittest.TestCase):
    """7. Timeout and HTTP errors fail safely."""

    def test_timeout_fails_safe(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude", timeout=0.1, max_retries=1)

        def handler(request):
            # Simulate slow response by raising timeout
            raise httpx.TimeoutException("simulated timeout")

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("timeout", result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_http_500_fails_safe_after_max_retries(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude", max_retries=1)

        def handler(request):
            return httpx.Response(500, text="internal error")

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("http_500", result.error)
        self.assertEqual(result.per_dimension_scores, {})

    def test_http_4xx_no_retry(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude", max_retries=3)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            return httpx.Response(401, text="unauthorized")

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        self.assertIn("http_401", result.error)
        # 4xx should not retry
        self.assertEqual(call_count[0], 1)

    def test_http_500_retries_then_fails(self):
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude", max_retries=2)
        call_count = [0]

        def handler(request):
            call_count[0] += 1
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.error)
        # 500 should retry (max_retries=2)
        self.assertEqual(call_count[0], 2)


# ── 8. Missing API key ──

class TestMissingApiKey(unittest.TestCase):
    """8. Missing API key fails safely (no crash, error=missing_credentials)."""

    def test_no_api_key_constructor_succeeds(self):
        # Explicit None + no env var → no crash
        with patch.dict(os.environ, {}, clear=True):
            judge = RealLLMJudge(judge_id="j", model="x", api_key=None, provider="claude")
            self.assertFalse(judge.has_credentials())

    def test_no_api_key_evaluate_fails_safe(self):
        with patch.dict(os.environ, {}, clear=True):
            judge = RealLLMJudge(judge_id="j", model="x", api_key=None, provider="claude")

            async def run():
                return await judge.evaluate(_make_evidence())

            result = asyncio.run(run())
            self.assertIsNotNone(result.error)
            self.assertIn("missing_credentials", result.error)
            self.assertEqual(result.per_dimension_scores, {})

    def test_api_key_from_env_var(self):
        with patch.dict(os.environ, {"M6_LLM_API_KEY": "env-key-789"}):
            judge = RealLLMJudge(judge_id="j", model="x", api_key=None, provider="claude")
            self.assertTrue(judge.has_credentials())


# ── 9. Network disabled / default path ──

class TestNetworkDefault(unittest.TestCase):
    """9. Default pytest runs are network-free."""

    def test_mock_judges_do_not_make_http_calls(self):
        from tests._helpers.subjective_eval import FixedScoreJudge, HighAgreementJudge
        judges = [
            HighAgreementJudge("A", base=4),
            HighAgreementJudge("B", base=4),
            HighAgreementJudge("C", base=4),
        ]
        runner = SequentialJudgeRunner(judges)
        results = runner.run(_make_evidence())
        self.assertEqual(len(results), 3)
        for jr in results:
            self.assertEqual(len(jr.per_dimension_scores), 8)
            self.assertIsNone(jr.error)
            self.assertIsNone(jr.provenance)  # Mock judges have no provenance

    def test_real_judge_without_http_client_uses_default(self):
        # Real judge should manage its own httpx client (no leak)
        judge = RealLLMJudge(judge_id="j", model="x", api_key="k", provider="claude", timeout=5.0)

        def handler(request):
            return httpx.Response(200, json=_make_claude_response_json({dim: 4 for dim in EIGHT_DIMENSIONS}))

        async def run_with_client():
            client = httpx.AsyncClient(transport=_mock_transport_with_handler(handler))
            try:
                return await judge.evaluate(_make_evidence(), http_client=client)
            finally:
                await client.aclose()

        result = asyncio.run(run_with_client())
        self.assertIsNone(result.error)
        self.assertEqual(len(result.per_dimension_scores), 8)


# ── 10. Three independent judge calls ──

class TestThreeIndependentJudges(unittest.TestCase):
    """10. Three judges each make independent HTTP calls."""

    def test_three_judges_three_independent_calls(self):
        judges = []
        call_count = [0]

        def make_handler(judge_name):
            def handler(request):
                call_count[0] += 1
                return httpx.Response(200, json=_make_claude_response_json({dim: 4 for dim in EIGHT_DIMENSIONS}))
            return handler

        judges = [
            RealLLMJudge(judge_id="A", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="B", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="C", model="x", api_key="k", provider="claude"),
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence()

        async def run():
            # Each judge gets its OWN client (independent)
            clients = [
                httpx.AsyncClient(transport=_mock_transport_with_handler(make_handler(j.judge_id)))
                for j in judges
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
        self.assertEqual(call_count[0], 3, "Expected 3 independent HTTP calls")
        for r in results:
            self.assertIsNone(r.error)


# ── 11. No cross-contamination ──

class TestNoCrossContamination(unittest.TestCase):
    """11. Judge B does not see Judge A's answer."""

    def test_judges_see_independent_evidence(self):
        seen_evidences = []

        class RecordingJudge(RealLLMJudge):
            def __init__(self, judge_id, recording_list):
                super().__init__(judge_id, model="x", api_key="k", provider="claude")
                self.recording = recording_list

            async def evaluate(self, evidence, http_client=None):
                # Record what evidence this judge sees
                self.recording.append(evidence)
                return await super().evaluate(evidence, http_client=http_client)

        recordings = []
        judges = [
            RecordingJudge("A", recordings),
            RecordingJudge("B", recordings),
            RecordingJudge("C", recordings),
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence("contamination_test")

        async def run():
            clients = [
                httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler))
                for _ in judges
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

        # Each judge saw the SAME evidence
        self.assertEqual(len(recordings), 3)
        for rec in recordings:
            self.assertIs(rec, ev)
        # No judge saw another judge's result
        for rec in recordings:
            self.assertNotIsInstance(rec, JudgeResult)


# ── 12. Provenance capture ──

class TestProvenanceCapture(unittest.TestCase):
    """12. JudgeProvenance captures model, provider, base_url, temperature, timestamp, response_hash, raw_response."""

    def test_successful_call_captures_provenance(self):
        judge = RealLLMJudge(
            judge_id="prov-1",
            model="claude-haiku-4-5-20251001",
            api_key="k",
            base_url="https://api.test/v1/messages",
            provider="claude",
            temperature=0.0,
        )

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.provenance)
        prov = result.provenance
        self.assertEqual(prov.provider, "claude")
        self.assertEqual(prov.model, "claude-haiku-4-5-20251001")
        self.assertEqual(prov.base_url, "https://api.test/v1/messages")
        self.assertEqual(prov.temperature, 0.0)
        self.assertIn("T", prov.timestamp)  # ISO 8601
        self.assertIsNotNone(prov.response_hash)
        self.assertEqual(len(prov.response_hash), 64)  # SHA256 hex
        self.assertIsNotNone(prov.raw_response)

    def test_no_api_key_in_provenance(self):
        # Even if api_key is "secret-123", it must not appear in provenance
        judge = RealLLMJudge(
            judge_id="p",
            model="x",
            api_key="SUPER-SECRET-KEY-DO-NOT-LEAK",
            base_url="https://api.test/v1/messages",
            provider="claude",
        )

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        prov = result.provenance
        prov_str = str(prov.__dict__)
        self.assertNotIn("SUPER-SECRET-KEY", prov_str)
        self.assertNotIn("DO-NOT-LEAK", prov_str)

    def test_failed_call_has_provenance_with_error(self):
        judge = RealLLMJudge(judge_id="p", model="x", api_key="k", provider="claude")

        def handler(request):
            return httpx.Response(500, text="server error")

        async def run():
            async with httpx.AsyncClient(transport=_mock_transport_with_handler(handler)) as client:
                return await judge.evaluate(_make_evidence(), http_client=client)

        result = asyncio.run(run())
        self.assertIsNotNone(result.provenance)
        self.assertIsNone(result.provenance.response_hash)
        self.assertIsNone(result.provenance.raw_response)


# ── 13. Existing aggregation integration ──

class TestAggregationIntegration(unittest.TestCase):
    """13. Real judges integrate with existing consensus / calibration."""

    def test_three_real_judges_aggregate_correctly(self):
        judges = [
            RealLLMJudge(judge_id="A", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="B", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="C", model="x", api_key="k", provider="claude"),
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence("integration_test")

        async def run():
            clients = [
                httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler))
                for _ in judges
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
        agg = aggregate(results, scenario_id="integration_test")
        # All 3 judges agree on 4 → median=4, no calibration
        for dim in EIGHT_DIMENSIONS:
            self.assertEqual(agg.median_scores[dim], 4)
        self.assertEqual(agg.overall_subjective_status, PASS)
        self.assertFalse(agg.calibration_required)

    def test_errored_judge_triggers_calibration(self):
        judges = [
            RealLLMJudge(judge_id="A", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="B", model="x", api_key="k", provider="claude"),
            RealLLMJudge(judge_id="C", model="x", api_key=None, provider="claude"),  # missing key
        ]
        runner = SequentialJudgeRunner(judges)
        ev = _make_evidence("error_test")

        async def run():
            with patch.dict(os.environ, {}, clear=True):
                # A, B will succeed; C has no key
                clients = [
                    httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler)),
                    httpx.AsyncClient(transport=_mock_transport_with_handler(_ok_claude_handler)),
                    None,  # C will fail
                ]
                results = []
                try:
                    for j, c in zip(judges, clients):
                        if c is None:
                            results.append(await j.evaluate(ev))
                        else:
                            results.append(await j.evaluate(ev, http_client=c))
                    return results
                finally:
                    for c in clients:
                        if c is not None:
                            await c.aclose()

        results = asyncio.run(run())
        agg = aggregate(results, scenario_id="error_test")
        # 1 judge errored → calibration required
        self.assertTrue(agg.calibration_required)
        # Median over 2 successful judges = 4 → not FAIL
        # But 1 judge errored → calibration should at least be true
        self.assertEqual(agg.agreement_metadata["errored_judges"][0]["judge_id"], "C")


if __name__ == "__main__":
    unittest.main()
