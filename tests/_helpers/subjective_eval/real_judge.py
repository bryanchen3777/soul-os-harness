"""
tests/_helpers/subjective_eval/real_judge.py
M6.0-5.2 (Bry 派工 2026-08-11 19:40): Real LLM Judge Backend.

This module implements RealLLMJudge that calls a real LLM API to score
Soul OS responses. It is OPT-IN ONLY — no production LLM call is made
without explicit instantiation + env var + API key.

Design constraints (per Bry 派工 spec):
  - Reuse existing M6.0-5 judge / consensus / calibration contracts (NO redesign)
  - 3 independent judges (no shared state, no cross-contamination)
  - 1-5 categorical Likert (strict, no continuous)
  - Malformed response → fail-safe (JudgeResult with error=...)
  - No API keys in logs / fixtures / git diff
  - Default pytest is network-free
  - Missing credentials → clear error, no silent crash
  - Timeout / API errors → fail-safe
  - Capture provenance: model, provider, base_url, temperature, timestamp,
    response_hash, raw_response (truncated)

Provider support:
  - openai: OpenAI-style API (api_key, base_url, model)
  - claude: Anthropic-style API (api_key, base_url, model)

Both use httpx directly (matching LLMProxy pattern, no extra dependencies).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

try:
    import httpx
except ImportError as e:
    raise ImportError(
        "RealLLMJudge requires httpx. Install with: pip install httpx"
    ) from e

from .rubric import EIGHT_DIMENSIONS, validate_score
from .evidence import EvaluationEvidence
from .judge import Judge, JudgeResult, JudgeProvenance


logger = logging.getLogger("tests.helpers.subjective_eval.real_judge")

JUDGE_PROMPT_VERSION = "v1-2026-08-11"

# 4000 char cap on raw_response for audit (avoid huge blobs)
RAW_RESPONSE_MAX_CHARS = 4000

# Build a robust prompt that requests strict JSON output
JUDGE_PROMPT_TEMPLATE = """You are evaluating a Soul OS agent's response for subjective quality.

=== SCENARIO ===
scenario_id: {scenario_id}
agent_id: {agent_id}
user_id: {user_id}
model: {model}
prompt_version: {prompt_version}
rubric_version: {rubric_version}
temperature: {temperature}

=== USER INPUT ===
{user_input}

=== COMPOSED CONTEXT (Soul System Prompt) ===
{composed_context}

=== LLM RESPONSE (under evaluation) ===
{llm_response}

=== STATE SNAPSHOT ===
{state_snapshot}

=== RUBRIC (8 dimensions, 1-5 categorical Likert) ===
For each of the 8 dimensions, score 1-5 (integer only, no decimals):

1. context_coherence: Does the response integrate the Soul Context blocks appropriately?
2. temporal_appropriateness: Does the response match the time period?
3. relationship_continuity: Does the response reflect the relationship level?
4. memory_continuity: Does the response use memory facts without fabrication?
5. emotional_continuity: Does the response match the mood?
6. world_context_relevance: Does the response handle world context correctly?
7. character_persona_consistency: Is the response in character for the agent?
8. lived_context_coherence: Is the response coherent and natural overall?

Scoring anchors:
  1 = Clearly wrong / harmful
  2 = Significant weakness
  3 = Acceptable
  4 = Strong
  5 = Excellent

=== OUTPUT FORMAT ===
Respond with a JSON object only. No prose, no explanation outside the JSON.
Do NOT use markdown code blocks. Just raw JSON.

{{
  "context_coherence": <int 1-5>,
  "temporal_appropriateness": <int 1-5>,
  "relationship_continuity": <int 1-5>,
  "memory_continuity": <int 1-5>,
  "emotional_continuity": <int 1-5>,
  "world_context_relevance": <int 1-5>,
  "character_persona_consistency": <int 1-5>,
  "lived_context_coherence": <int 1-5>,
  "rationale": "short one-sentence explanation (max 200 chars)"
}}
"""


def _build_judge_prompt(
    evidence: EvaluationEvidence,
    temperature: float,
) -> str:
    """Build the judge prompt from an evidence packet."""
    extra = evidence.extra or {}
    return JUDGE_PROMPT_TEMPLATE.format(
        scenario_id=evidence.scenario_id,
        agent_id=extra.get("agent_id", "unknown"),
        user_id=extra.get("user_id", "unknown"),
        model=evidence.model,
        prompt_version=evidence.prompt_version,
        rubric_version=evidence.rubric_version,
        temperature=temperature,
        user_input=evidence.user_input,
        composed_context=evidence.composed_context,
        llm_response=evidence.llm_response,
        state_snapshot=json.dumps(evidence.state_snapshot, ensure_ascii=False, indent=2),
    )


def _parse_judge_response(
    raw_response: str,
) -> Dict[str, Union[int, str]]:
    """
    Parse the LLM response as JSON and extract per-dimension scores.

    Returns dict with keys = 8 dimension names + "rationale".
    Raises ValueError on malformed response.
    """
    if not isinstance(raw_response, str) or not raw_response.strip():
        raise ValueError("Empty or non-string LLM response")

    # Try to find JSON object in the response (some LLMs add prose around it)
    text = raw_response.strip()
    # Strip markdown code block fences if present
    if text.startswith("```"):
        # Find first { and last }
        first = text.find("{")
        last = text.rfind("}")
        if first < 0 or last < 0 or last <= first:
            raise ValueError("Response has code fence but no JSON object")
        text = text[first:last + 1]
    else:
        # Try to extract JSON object directly
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            text = text[first:last + 1]

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response is not valid JSON: {e}")

    if not isinstance(data, dict):
        raise ValueError(f"Response JSON is not a dict, got {type(data).__name__}")

    # Validate all 8 dimensions present
    for dim in EIGHT_DIMENSIONS:
        if dim not in data:
            raise ValueError(f"Missing dimension in response: {dim!r}")
        # Will validate int + range via validate_score
        score = data[dim]
        if not isinstance(score, int) or isinstance(score, bool):
            raise ValueError(
                f"Dimension {dim!r} score must be int, got {type(score).__name__}: {score!r}"
            )
        validate_score(score)

    # Rationale optional
    rationale = data.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = str(rationale)

    return {
        **{dim: int(data[dim]) for dim in EIGHT_DIMENSIONS},
        "rationale": rationale[:200],  # cap at 200 chars
    }


def _hash_response(raw: str) -> str:
    """SHA256 hash of raw response for audit provenance."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _truncate_response(raw: str) -> str:
    """Truncate raw response for audit provenance (avoid huge blobs)."""
    if len(raw) <= RAW_RESPONSE_MAX_CHARS:
        return raw
    return raw[:RAW_RESPONSE_MAX_CHARS] + f"\n\n[... truncated at {RAW_RESPONSE_MAX_CHARS} chars, total {len(raw)} ...]"


class RealLLMJudge(Judge):
    """
    Real LLM judge that calls a model API to score an evidence packet.

    Supports two providers:
      - "openai": OpenAI-style API (Authorization: Bearer header, json body with messages)
      - "claude": Anthropic-style API (x-api-key header, anthropic-version header, json body with messages)

    Constructor:
      judge_id: stable identifier (e.g. "judge-A")
      model: model identifier (e.g. "claude-haiku-4-5-20251001")
      api_key: API key (or None for env-var fallback)
      base_url: API endpoint URL
      provider: "openai" or "claude"
      temperature: generation temperature (default 0.0 for reproducibility)
      timeout: HTTP timeout in seconds (default 60.0)
      max_retries: max retry count for transient errors (default 2)
      api_key_env_var: if api_key is None, read from this env var (default "M6_LLM_API_KEY")

    Real LLM execution is explicit opt-in: must instantiate this class
    with a real API key. Mock judges (FixedScoreJudge etc.) are the
    default and do not require this class.

    IMPORTANT: This judge uses async API. Call via `await judge.evaluate(evidence)`.
    Mock judges' evaluate() is sync; RealLLMJudge's evaluate() is async.
    """
    def __init__(
        self,
        judge_id: str,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        provider: str = "claude",
        temperature: float = 0.0,
        timeout: float = 60.0,
        max_retries: int = 2,
        api_key_env_var: str = "M6_LLM_API_KEY",
        model_family: Optional[str] = None,
    ):
        super().__init__(judge_id, model)
        if provider not in ("openai", "claude"):
            raise ValueError(f"provider must be 'openai' or 'claude', got {provider!r}")
        self.provider = provider
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        # M6.0-5.4: model_family for diversity validation.
        # Defaults to provider (so 1 Claude + 1 OpenAI = 2 distinct families).
        self.model_family = model_family or provider

        # Resolve API key: explicit > env var
        import os
        if api_key is None:
            api_key = os.environ.get(api_key_env_var)
        if not api_key:
            # Fail-safe: do not raise (so mock tests can co-exist)
            # The judge will return error="missing_credentials" on evaluate()
            self._api_key: Optional[str] = None
        else:
            self._api_key = api_key

        # Default base URLs (matching LLMProxy)
        if provider == "openai":
            self.base_url = base_url or "https://api.openai.com/v1/chat/completions"
        else:  # claude
            self.base_url = base_url or "https://api.anthropic.com/v1/messages"

    def has_credentials(self) -> bool:
        """Return True if this judge has an API key configured."""
        return self._api_key is not None

    def _build_request_body(self, evidence: EvaluationEvidence) -> Dict[str, Any]:
        """Build the HTTP request body for the LLM API."""
        user_prompt = _build_judge_prompt(evidence, self.temperature)
        if self.provider == "openai":
            return {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": "You are a strict JSON output evaluator."},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": self.temperature,
                "max_tokens": 1000,
                "response_format": {"type": "json_object"},
            }
        else:  # claude
            return {
                "model": self.model,
                "max_tokens": 1000,
                "temperature": self.temperature,
                "messages": [
                    {"role": "user", "content": user_prompt},
                ],
            }

    def _build_headers(self) -> Dict[str, str]:
        """Build the HTTP headers for the LLM API."""
        if not self._api_key:
            return {}
        if self.provider == "openai":
            return {
                "Authorization": f"Bearer {self._api_key}",
                "content-type": "application/json",
            }
        else:  # claude
            return {
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }

    def _extract_response_text(self, body: Dict[str, Any]) -> str:
        """Extract text content from API response body."""
        if self.provider == "openai":
            # OpenAI: body["choices"][0]["message"]["content"]
            choices = body.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError("OpenAI response missing 'choices'")
            content = choices[0].get("message", {}).get("content", "")
            if not isinstance(content, str):
                raise ValueError(f"OpenAI content is not str, got {type(content).__name__}")
            return content
        else:  # claude
            # Claude: body["content"][0]["text"]
            content_list = body.get("content")
            if not content_list or not isinstance(content_list, list):
                raise ValueError("Claude response missing 'content'")
            text = content_list[0].get("text", "")
            if not isinstance(text, str):
                raise ValueError(f"Claude text is not str, got {type(text).__name__}")
            return text

    def _extract_metadata(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """M6.0-5.4: Extract token_usage, request_id, stop_reason from API response."""
        meta: Dict[str, Any] = {}
        if self.provider == "openai":
            # OpenAI: body["usage"] = {prompt_tokens, completion_tokens, total_tokens}
            usage = body.get("usage", {})
            if isinstance(usage, dict):
                token_usage: Dict[str, int] = {}
                if "prompt_tokens" in usage:
                    token_usage["input"] = int(usage["prompt_tokens"])
                if "completion_tokens" in usage:
                    token_usage["output"] = int(usage["completion_tokens"])
                if "total_tokens" in usage:
                    token_usage["total"] = int(usage["total_tokens"])
                if token_usage:
                    meta["token_usage"] = token_usage
            # body["id"] = "chatcmpl-..."
            rid = body.get("id")
            if isinstance(rid, str):
                meta["request_id"] = rid
            # body["choices"][0]["finish_reason"] = "stop" | "length" | "tool_calls"
            choices = body.get("choices")
            if isinstance(choices, list) and choices:
                sr = choices[0].get("finish_reason")
                if isinstance(sr, str):
                    meta["stop_reason"] = sr
        else:  # claude
            # Claude: body["usage"] = {input_tokens, output_tokens}
            usage = body.get("usage", {})
            if isinstance(usage, dict):
                token_usage = {}
                if "input_tokens" in usage:
                    token_usage["input"] = int(usage["input_tokens"])
                if "output_tokens" in usage:
                    token_usage["output"] = int(usage["output_tokens"])
                if token_usage:
                    total = token_usage.get("input", 0) + token_usage.get("output", 0)
                    token_usage["total"] = total
                    meta["token_usage"] = token_usage
            # body["id"] = "msg_..."
            rid = body.get("id")
            if isinstance(rid, str):
                meta["request_id"] = rid
            # body["stop_reason"] = "end_turn" | "max_tokens" | "stop_sequence"
            sr = body.get("stop_reason")
            if isinstance(sr, str):
                meta["stop_reason"] = sr
        return meta

    async def evaluate(
        self,
        evidence: EvaluationEvidence,
        http_client: Optional["httpx.AsyncClient"] = None,
        on_retry: Optional[Callable[[], bool]] = None,
    ) -> JudgeResult:
        """
        Evaluate evidence by calling real LLM API.
        Returns JudgeResult with scores on success, or error=... on failure.
        Never raises (fail-safe).

        on_retry (M6.0-5.4-R1 / R2): optional enforcement-capable callback
                  invoked BEFORE each retry attempt.

        Contract (M6.0-5.4-R2):
            - Return True  -> retry is allowed; loop sleeps + continues
            - Return False -> retry MUST NOT happen; loop breaks immediately
              (no sleep, no additional HTTP request). The original retryable
              error is preserved as the JudgeResult.error.

        The callback is called inside the retry loop for 429 / 5xx / timeout
        failures. For 4xx (non-retryable) the callback is NOT called.

        Callback errors are treated as "retry denied" (fail-safe: a buggy
        callback must not let a retry bypass the budget). The original error
        is preserved.

        Per Bry 派工 M6.0-5.4-R1: "Option A — RealLLMJudge remains authoritative
        for retry count. CostBudget receives retry events/counters from that
        path."
        Per Bry 派工 M6.0-5.4-R2: "Make retry callback enforcement-capable."
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        import time as _time

        # Fail-safe 1: missing credentials
        if not self._api_key:
            return JudgeResult(
                judge_id=self.judge_id,
                model=self.model,
                per_dimension_scores={},
                error=f"missing_credentials: api_key not configured (env var M6_LLM_API_KEY)",
                provenance=JudgeProvenance(
                    provider=self.provider,
                    model=self.model,
                    base_url=self.base_url,
                    temperature=self.temperature,
                    timestamp=timestamp,
                    response_hash=None,
                    raw_response=None,
                    prompt_version=JUDGE_PROMPT_VERSION,
                    rubric_version=evidence.rubric_version,
                ),
            )

        request_body = self._build_request_body(evidence)
        headers = self._build_headers()
        last_error: Optional[str] = None
        raw_response_text: Optional[str] = None
        body: Dict[str, Any] = {}
        latency_ms: Optional[float] = None
        start_time = _time.monotonic()

        # Use provided http_client or create a new one
        owns_client = http_client is None
        if owns_client:
            http_client = httpx.AsyncClient(timeout=self.timeout)

        try:
            for attempt in range(self.max_retries):
                try:
                    resp = await http_client.post(
                        self.base_url,
                        headers=headers,
                        json=request_body,
                    )
                    # Check status
                    if resp.status_code in (429, 500, 502, 503, 504):
                        # Retryable
                        last_error = f"http_{resp.status_code}: {resp.text[:200]}"
                        if attempt < self.max_retries - 1:
                            # M6.0-5.4-R2: ask on_retry callback if retry allowed
                            if on_retry is not None:
                                try:
                                    allowed = on_retry()
                                except Exception:
                                    # Callback errors must not break retry path —
                                    # treat as denied (fail-safe: buggy callback
                                    # must NOT let retry bypass budget)
                                    allowed = False
                                if not allowed:
                                    # Retry denied: no sleep, no extra HTTP call
                                    break
                            import asyncio
                            await asyncio.sleep(2 ** attempt)
                            continue
                        break
                    if resp.status_code != 200:
                        # Non-retryable error
                        last_error = f"http_{resp.status_code}: {resp.text[:200]}"
                        break
                    # Success
                    body = resp.json()
                    raw_response_text = self._extract_response_text(body)
                    break
                except httpx.TimeoutException as e:
                    last_error = f"timeout: {type(e).__name__}"
                    if attempt < self.max_retries - 1:
                        # M6.0-5.4-R2: ask on_retry callback if retry allowed
                        if on_retry is not None:
                            try:
                                allowed = on_retry()
                            except Exception:
                                # Callback errors: treat as denied
                                allowed = False
                            if not allowed:
                                # Retry denied: no sleep, no extra HTTP call
                                break
                        import asyncio
                        await asyncio.sleep(2 ** attempt)
                        continue
                    break
                except httpx.HTTPError as e:
                    last_error = f"http_error: {type(e).__name__}: {e}"
                    break
                except Exception as e:
                    last_error = f"unexpected: {type(e).__name__}: {e}"
                    break
            # Record latency after loop (success or fail)
            latency_ms = (_time.monotonic() - start_time) * 1000.0
        finally:
            if owns_client:
                await http_client.aclose()

        # Fail-safe 2: HTTP error
        if raw_response_text is None:
            return JudgeResult(
                judge_id=self.judge_id,
                model=self.model,
                per_dimension_scores={},
                error=last_error or "unknown_error",
                provenance=JudgeProvenance(
                    provider=self.provider,
                    model=self.model,
                    base_url=self.base_url,
                    temperature=self.temperature,
                    timestamp=timestamp,
                    response_hash=None,
                    raw_response=None,
                    prompt_version=JUDGE_PROMPT_VERSION,
                    rubric_version=evidence.rubric_version,
                    latency_ms=latency_ms,
                ),
            )

        # Extract metadata (M6.0-5.4)
        meta = self._extract_metadata(body)

        # Success: parse response
        try:
            parsed = _parse_judge_response(raw_response_text)
        except ValueError as e:
            return JudgeResult(
                judge_id=self.judge_id,
                model=self.model,
                per_dimension_scores={},
                error=f"malformed_response: {e}",
                provenance=JudgeProvenance(
                    provider=self.provider,
                    model=self.model,
                    base_url=self.base_url,
                    temperature=self.temperature,
                    timestamp=timestamp,
                    response_hash=_hash_response(raw_response_text),
                    raw_response=_truncate_response(raw_response_text),
                    prompt_version=JUDGE_PROMPT_VERSION,
                    rubric_version=evidence.rubric_version,
                    latency_ms=latency_ms,
                    request_id=meta.get("request_id"),
                    stop_reason=meta.get("stop_reason"),
                    token_usage=meta.get("token_usage"),
                ),
            )

        # Build per_dimension_scores (exclude rationale)
        per_dimension_scores = {dim: parsed[dim] for dim in EIGHT_DIMENSIONS}
        rationale = parsed.get("rationale", "")

        return JudgeResult(
            judge_id=self.judge_id,
            model=self.model,
            per_dimension_scores=per_dimension_scores,
            rationale=rationale,
            provenance=JudgeProvenance(
                provider=self.provider,
                model=self.model,
                base_url=self.base_url,
                temperature=self.temperature,
                timestamp=timestamp,
                response_hash=_hash_response(raw_response_text),
                raw_response=_truncate_response(raw_response_text),
                prompt_version=JUDGE_PROMPT_VERSION,
                rubric_version=evidence.rubric_version,
                latency_ms=latency_ms,
                request_id=meta.get("request_id"),
                stop_reason=meta.get("stop_reason"),
                token_usage=meta.get("token_usage"),
            ),
        )
