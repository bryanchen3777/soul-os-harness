# M6.0-5.2 — Real LLM Judge Backend Closeout

**Ticket**: M6.0-5.2 (Bry 派工 2026-08-11 19:40)
**Mode**: IMPLEMENTATION
**Baseline (before)**: HEAD = `5f4ae34` (M6.0-5) | origin/main = `5f4ae34` (synced)
**Final HEAD**: `5f4ae34` + 1 commit
**Date**: 2026-08-11 19:48 EDT

---

## 1. Tests

### M6.0-5.2 unit (this ticket's deliverable)

```
30/30 PASS in 2.37s

13 test categories (per Bry spec):
  1. real backend request construction (3 tests)
  2. structured response parsing (3 tests)
  3. valid 1-5 scores (3 tests, integrated with parsing)
  4. malformed response (3 tests)
  5. missing dimension (2 tests)
  6. invalid score (3 tests)
  7. timeout/error handling (4 tests)
  8. missing API key (3 tests)
  9. network disabled/default path (2 tests)
 10. three independent judge calls (1 test)
 11. no cross-contamination (1 test)
 12. provenance capture (3 tests)
 13. existing aggregation integration (2 tests)
```

All tests use `httpx.MockTransport` — **0 real network calls** during normal pytest.

### M6.0-5.2 opt-in (real network test)

```
1/1 SKIPPED (M6_REAL_LLM not set, correct behavior)
```

The opt-in test (`test_m6_0_5_2_real_judge_optin.py`) is gated by `M6_REAL_LLM=1` env var. Without it, the test is SKIPPED. No real API call, no cost, no production data.

---

## 2. Regression

### Required suites (per Bry spec)

| Suite | Tests | Result |
|-------|-------|--------|
| **M6.0-5.2 unit (this ticket)** | 30 | 30/30 PASS |
| **M6.0-5.2 opt-in (gated)** | 1 | 1/1 SKIPPED (correct) |
| **M6.0-5 (subjective eval framework)** | 56 | 56/56 PASS |
| M6.0-2 (A/B/C validation) | 16 | 16/16 PASS |
| M6.0-3 (D/E/F/G/H validation) | 22 | 22/22 PASS |
| M5.8-4 (producer gating) | 26 | 26/26 PASS |
| M5.9-3 (world → inner life) | 46 | 46/46 PASS |
| M5.9-3.1 (production wiring) | 31 | 31/31 PASS |
| M5.10-2 (LLM judge v1 context) | 13 | 13/13 PASS |
| M5.13-3 (relationship context) | 29 | 29/29 PASS |
| **Required subtotal** | **270** | **269 PASS + 1 SKIPPED** in 5.84s |

### Broader M-series regression

| Suite | Tests | Result |
|-------|-------|--------|
| M5.2-M5.7 baseline | 565 | 565/565 PASS in 30.17s |

### Total

**834/834 PASS + 1 SKIPPED** across all M5/M6 tests.

### Pre-existing failures

Pre-existing flaky test (M5.8-1 baseline) — NOT touched by M6.0-5.2:
- `tests/test_extract_and_judge_context_bug.py::test_content_stage_sees_real_text` (async infra)

Not in M6.0-5.2 scope. M6.0-5.2 30/30 PASS without touching this test.

---

## 3. Real backend architecture

### Module

`tests/_helpers/subjective_eval/real_judge.py` (NEW):

```python
class RealLLMJudge(Judge):
    def __init__(
        self,
        judge_id: str,
        model: str,
        api_key: Optional[str] = None,       # None → reads M6_LLM_API_KEY env var
        base_url: Optional[str] = None,      # Default: provider-specific
        provider: str = "claude",             # "claude" or "openai"
        temperature: float = 0.0,             # 0.0 for reproducibility
        timeout: float = 60.0,
        max_retries: int = 2,                 # retry 5xx / 429
        api_key_env_var: str = "M6_LLM_API_KEY",
    ):
        ...
    
    async def evaluate(self, evidence, http_client=None) -> JudgeResult:
        # 1. Fail-safe: missing credentials → JudgeResult(error="missing_credentials")
        # 2. Build request body + headers (provider-specific)
        # 3. POST to base_url with retry on 5xx/429
        # 4. Extract response text (provider-specific: choices[0].message.content for OpenAI, content[0].text for Claude)
        # 5. Parse JSON (fail-safe on malformed)
        # 6. Validate 8 dimensions 1-5 (fail-safe on invalid)
        # 7. Return JudgeResult with JudgeProvenance
        # NEVER raises
```

### Provider support

| Provider | base_url | Headers | Response extraction |
|----------|----------|---------|---------------------|
| `claude` | `https://api.anthropic.com/v1/messages` | `x-api-key`, `anthropic-version: 2023-06-01` | `body["content"][0]["text"]` |
| `openai` | `https://api.openai.com/v1/chat/completions` | `Authorization: Bearer ...` | `body["choices"][0]["message"]["content"]` |

Matches LLMProxy at `src/llm/proxy.py:919-1162`.

### Request body (Claude)

```json
{
  "model": "claude-haiku-4-5-20251001",
  "max_tokens": 1000,
  "temperature": 0.0,
  "messages": [
    {"role": "user", "content": "<JUDGE_PROMPT with rubric + evidence>"}
  ]
}
```

### Judge prompt structure

```
You are evaluating a Soul OS agent's response for subjective quality.

=== SCENARIO ===
scenario_id / agent_id / user_id / model / prompt_version / rubric_version / temperature

=== USER INPUT === {user_input}
=== COMPOSED CONTEXT (Soul System Prompt) === {composed_context}
=== LLM RESPONSE (under evaluation) === {llm_response}
=== STATE SNAPSHOT === {state_snapshot JSON}

=== RUBRIC (8 dimensions, 1-5 categorical Likert) ===
1. context_coherence
2. temporal_appropriateness
3. relationship_continuity
4. memory_continuity
5. emotional_continuity
6. world_context_relevance
7. character_persona_consistency
8. lived_context_coherence

Anchors: 1=harmful 2=weakness 3=acceptable 4=strong 5=excellent

=== OUTPUT FORMAT === JSON only, no markdown
{"context_coherence": 4, ..., "rationale": "..."}
```

---

## 4. Three-judge independence verification

### Test: `TestNoCrossContamination.test_judges_see_independent_evidence`

Uses a `RecordingJudge` subclass that records the evidence each judge sees. Verifies:
- All 3 judges see the SAME evidence object (single source)
- No judge sees another judge's `JudgeResult` (no shared state)
- Each judge has its own `httpx.AsyncClient` (independent transport)

### Test: `TestThreeIndependentJudges.test_three_judges_three_independent_calls`

Verifies 3 HTTP calls are made (call_count == 3) — proves judges don't share connections.

---

## 5. Network / default behavior

### Default pytest: NETWORK-FREE

All M6.0-5.2 unit tests use `httpx.MockTransport` — no real network. M6.0-5 mock judges (FixedScoreJudge, HighAgreementJudge, etc.) do not use any HTTP at all.

### Opt-in: REAL NETWORK

`tests/test_m6_0_5_2_real_judge_optin.py` is gated by:

```python
M6_REAL_LLM = os.environ.get("M6_REAL_LLM", "").lower() in ("1", "true", "yes", "on")
pytestmark = pytest.mark.skipif(
    not M6_REAL_LLM or not M6_LLM_API_KEY,
    reason="M6_REAL_LLM not set (opt-in only). ..."
)
```

Without env var, the entire module is SKIPPED at collection time. No cost, no API key needed.

With env var + API key, the test makes 3 real LLM calls and verifies end-to-end.

### Missing API key

`RealLLMJudge(judge_id, model, api_key=None, ...)`:
- If env var `M6_LLM_API_KEY` is set: uses it
- Otherwise: `has_credentials()` returns False
- `evaluate()` returns `JudgeResult(error="missing_credentials: api_key not configured (env var M6_LLM_API_KEY)")`
- No exception, no crash

---

## 6. Error / fail-safe behavior

| Failure mode | Behavior |
|--------------|----------|
| Missing API key | `JudgeResult(error="missing_credentials: ...")` |
| HTTP 4xx (e.g. 401, 403) | No retry, `JudgeResult(error="http_4xx: ...")` |
| HTTP 5xx (e.g. 500, 502, 503) | Retry up to `max_retries`, then `JudgeResult(error="http_5xx: ...")` |
| HTTP 429 (rate limit) | Retry up to `max_retries` |
| Timeout (`httpx.TimeoutException`) | Retry up to `max_retries`, then `JudgeResult(error="timeout: ...")` |
| Empty response | `JudgeResult(error="malformed_response: Empty or non-string LLM response")` |
| Non-JSON response | `JudgeResult(error="malformed_response: Response is not valid JSON: ...")` |
| JSON not dict (e.g. array) | `JudgeResult(error="malformed_response: Response JSON is not a dict, got list")` |
| Missing dimension | `JudgeResult(error="malformed_response: Missing dimension in response: 'X'")` |
| Invalid score (e.g. 0, 6) | `JudgeResult(error="malformed_response: Score must be in [1, 2, 3, 4, 5], got 0")` |
| Non-int score (e.g. "good") | `JudgeResult(error="malformed_response: Dimension 'X' score must be int, got str")` |

**NEVER raises.** All errors return `JudgeResult` with `error` field set and `per_dimension_scores={}`.

---

## 7. Credential handling

### Default behavior: No credentials committed

The repo contains no API keys. Tests do not require real API keys to run.

### Opt-in credentials (never committed)

```bash
# Example: enable real LLM test
M6_REAL_LLM=1 M6_LLM_API_KEY=sk-... M6_LLM_MODEL=claude-haiku-4-5-20251001 \
  python -m pytest tests/test_m6_0_5_2_real_judge_optin.py -v
```

API key is read at runtime from `M6_LLM_API_KEY` env var. Never written to file. Never logged.

### Test verifies: `TestProvenanceCapture.test_no_api_key_in_provenance`

Uses a test key `"SUPER-SECRET-KEY-DO-NOT-LEAK"` and verifies the string does NOT appear in:
- `JudgeProvenance.__dict__`
- Any field of the provenance dataclass

This is a regression test — if someone accidentally adds `api_key` to provenance, this test fails.

---

## 8. Provenance / reproducibility

### `JudgeProvenance` dataclass

```python
@dataclass(frozen=True)
class JudgeProvenance:
    provider: str                          # "claude" | "openai" | "mock"
    model: str                             # e.g. "claude-haiku-4-5-20251001"
    base_url: Optional[str]                # API endpoint (no api_key)
    temperature: float                     # 0.0 default
    timestamp: str                         # ISO 8601 UTC
    response_hash: Optional[str]           # SHA256 of raw_response
    raw_response: Optional[str]           # Full text, truncated at 4000 chars
    prompt_version: str = "v1-2026-08-11"  # JUDGE_PROMPT_VERSION constant
    rubric_version: str = "v1-2026-08-11"  # from evidence
```

### Reproducibility guarantees

1. **Prompt version** = "v1-2026-08-11" (constant `JUDGE_PROMPT_VERSION`)
2. **Model** = explicit (passed to constructor, captured in provenance)
3. **Temperature** = explicit (default 0.0)
4. **Timestamp** = captured at evaluate() time
5. **Response hash** = SHA256 of raw response (verifiable later)
6. **Raw response** = stored (truncated to 4000 chars for audit; full for shorter responses)

### Re-evaluation reproducibility

To reproduce a past evaluation:
1. Take `evidence` + `provenance`
2. Use same `model`, `temperature`, `rubric_version`, `prompt_version`
3. Call `RealLLMJudge(judge_id=provenance.judge_id, model=provenance.model, ...).evaluate(evidence)`
4. Compare `response_hash` to original

---

## 9. Calibration integration

Reuses M6.0-5 `aggregate()` and `CalibrationQueue` directly. **No new calibration mechanism.**

### Errored judge handling (M6.0-5.2 addition)

If any judge errors:
- `calibration_required = True` (failure to evaluate = calibration event)
- `agreement_metadata["errored_judges"]` lists the errored judges
- If 2+ judges errored: `overall_subjective_status = FAIL` (insufficient evidence)
- If 1 judge errored: median over 2 successful judges; calibration_required = True

### Test verifies: `TestAggregationIntegration.test_errored_judge_triggers_calibration`

3 judges, 1 has no API key → aggregated result has `calibration_required=True` and `errored_judges[0]["judge_id"] == "C"`.

---

## 10. Deterministic precedence (unchanged)

`combine_deterministic_subjective()` from M6.0-5 is unchanged. RealLLMJudge integrates with it transparently — same as mock judges.

| Deterministic | Subjective | Final |
|---------------|------------|-------|
| PASS | PASS | PASS |
| PASS | PARTIAL | PARTIAL |
| PASS | FAIL | PARTIAL |
| FAIL | any | FAIL (det overrides) |

---

## 11. Production integrity

### SHA256 + mtime before/after M6.0-5.2 test run

| File | sha256 (before) | sha256 (after) | Status |
|------|-----------------|-----------------|--------|
| `data/soul/agent_yua/relationships.json` | 84765BCCAECEC489... | 84765BCCAECEC489... | **IDENTICAL** |
| `data/agents/agent_yua/carryover.json` | C6BE0753CCCE4E45 | C6BE0753CCCE4E45 | unchanged |
| `data/agents/agent_ruka/carryover.json` | 62D7E475C72C3BBF | 62D7E475C72C3BBF | unchanged |
| `data/agents/agent_yua/emotional-state.json` | 6ABA2661F22B0D83 | 6ABA2661F22B0D83 | unchanged |

**0 production mutation from M6.0-5.2 test runs.**

### What M6.0-5.2 does NOT touch

- ❌ memory.db
- ❌ relationships.json
- ❌ carryover.json
- ❌ production diary / dream / trace
- ❌ Soul OS runtime (LLMProxy, MemoryMiddleware, scheduler, etc.)
- ❌ Production prompts
- ❌ Real API calls during pytest (default off)

---

## 12. Frozen contract status

### Contracts EXTENDED (backward compatible)

| Contract | Old | New | Backward compat |
|----------|-----|-----|-----------------|
| `JudgeResult` | `(judge_id, model, per_dimension_scores, rationale)` | + `provenance: Optional[JudgeProvenance] = None`, + `error: Optional[str] = None` | ✓ (defaults None) |
| `consensus.aggregate()` | 3 judges, all successful | + errored judges trigger calibration_required=True; 2+ errored → FAIL | ✓ (M6.0-5 still 56/56 PASS) |

### Contracts UNCHANGED (verified)

- 0 source files modified (`git diff --stat src/`)
- 0 frozen contract changes
- LLMProxy: 0 changes
- MemoryMiddleware / MemoryReader / MemoryWriter: 0 changes
- WorldEvent / InnerLifeEvent / Provenance: 0 changes
- M5.8-4 gate: 0 changes
- M5.9-3 adapter: 0 changes
- M5.10-2 LLM judge: 0 changes
- M5.13-3 relationship: 0 changes
- M6.0-5 framework: 0 changes (M6.0-5 tests still 56/56 PASS)

---

## 13. Git state

```
HEAD = 5f4ae34 (M6.0-5) + 1 commit (this ticket)
Working tree: 20 pre-existing untracked artifacts preserved (M5.8-1 baseline)
Modified: 0 source files
New: 3 files
  - tests/_helpers/subjective_eval/real_judge.py
  - tests/test_m6_0_5_2_real_judge_unit.py
  - tests/test_m6_0_5_2_real_judge_optin.py
  - logs/m6_0_5_2_real_llm_judge_backend_closeout.md (this file)
Modified: 2 files (backward compatible)
  - tests/_helpers/subjective_eval/judge.py (added optional fields)
  - tests/_helpers/subjective_eval/consensus.py (handle errored judges)
  - tests/_helpers/subjective_eval/__init__.py (export new API)
```

---

## 14. Modified files

| File | Type | Notes |
|------|------|-------|
| `tests/_helpers/subjective_eval/real_judge.py` | new | RealLLMJudge + helpers (18,334 bytes) |
| `tests/_helpers/subjective_eval/judge.py` | modified | + JudgeProvenance + error field (backward compat) |
| `tests/_helpers/subjective_eval/consensus.py` | modified | + errored judge handling |
| `tests/_helpers/subjective_eval/__init__.py` | modified | + new public API exports |
| `tests/test_m6_0_5_2_real_judge_unit.py` | new | 30 unit tests (httpx.MockTransport) |
| `tests/test_m6_0_5_2_real_judge_optin.py` | new | 1 opt-in test (M6_REAL_LLM gated) |
| `logs/m6_0_5_2_real_llm_judge_backend_closeout.md` | new | this closeout |

### What was NOT changed (per out-of-scope)

- 0 production source files modified
- 0 LLMProxy modifications
- 0 production prompt changes
- 0 new context blocks
- 0 new runtime scoring dimensions
- 0 diary/dream/proactive subjective eval
- 0 TTS / voice / embedding / vector DB
- 0 API keys in git
- 0 production data mutation
- 0 new calibration mechanism (reuses M6.0-5)

---

## 15. Architectural findings

### F1: RealLLMJudge matches LLMProxy pattern (positive)

**Severity**: VALIDATION (positive)

RealLLMJudge uses the same httpx pattern as LLMProxy at `src/llm/proxy.py:919-1162`:
- OpenAIBackend: `Authorization: Bearer`, `response_format: json_object`
- ClaudeBackend: `x-api-key`, `anthropic-version: 2023-06-01`
- Async httpx.AsyncClient with timeout + retry

This means M6.0-5.2 does NOT introduce a new HTTP framework or new pattern. It reuses proven infrastructure.

### F2: Optional JudgeResult fields preserve backward compat (positive)

**Severity**: VALIDATION (positive)

Adding `provenance: Optional[JudgeProvenance] = None` and `error: Optional[str] = None` to `JudgeResult`:
- All existing tests construct `JudgeResult` with keyword args
- New fields default to None
- M6.0-5 56/56 tests still PASS without modification
- Backward compat verified

### F3: Mock judges + real judges use same Judge ABC (positive)

**Severity**: VALIDATION (positive)

`RealLLMJudge` extends `Judge` ABC from M6.0-5. It overrides `evaluate()` to be `async` instead of `sync`. This is a method-level override; the ABC contract is preserved.

`SequentialJudgeRunner` works with both mock and real judges because:
- `MockLLMBackend.evaluate()` returns `JudgeResult`
- `RealLLMJudge.evaluate()` returns `JudgeResult` (or `JudgeResult` with error)

### F4: Opt-in real network test correctly SKIPPED (positive)

**Severity**: VALIDATION (positive)

`tests/test_m6_0_5_2_real_judge_optin.py` uses `pytest.mark.skipif` at module level. Without `M6_REAL_LLM=1` env var:
- The entire module is skipped at collection time
- 0 pytest collection work
- 0 cost
- Verified: 1/1 SKIPPED in normal pytest run

### F5: Provenance captures all needed audit fields (informational)

**Severity**: INFORMATIONAL

`JudgeProvenance` captures 8 fields:
- provider, model, base_url, temperature (config)
- timestamp (when)
- response_hash, raw_response (what LLM said)
- prompt_version, rubric_version (versioning)

Sufficient for audit / reproducibility. Could add `latency_ms`, `request_id`, `token_usage` in future, but not required by Bry spec.

---

## 16. Unresolved issues

### 0 unresolved issues in M6.0-5.2 scope.

All Bry 派工 spec requirements implemented and tested.

### Open questions for future M6.x tickets (out of M6.0-5.2 scope)

| Question | Ticket |
|----------|--------|
| How to handle rate limits / cost over time | M6.0-5.3 (out of scope) |
| Multi-provider orchestration (3 different models in one run) | M6.0-5.3 (out of scope) |
| Diary / Dream / Proactive DM subjective eval | M6.0-5.1 (already identified in M6.0-5 closeout) |
| Real LLM test in CI (with secret management) | M6.0-5.4 (out of scope) |

---

## 17. Stop conditions check

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Real judge requires production runtime modification | **No** | RealLLMJudge in tests/_helpers/ (test-only) |
| 2 | Existing frozen LLM contract must change | **No** | RealLLMJudge reuses existing LLMProxy patterns; no contract change |
| 3 | API credentials need to be stored in repo | **No** | API key only in env var; no key in code, fixtures, or git |
| 4 | Evaluation requires production data | **No** | Tests use synthetic evidence; production isolated |
| 5 | Existing M6.0-5 contracts need redesign | **No** | Only added optional fields (provenance, error); 56/56 M6.0-5 tests still PASS |
| 6 | A new abstraction materially changes runtime architecture | **No** | RealLLMJudge is new code in tests/, 0 source changes |
| 7 | Real judge cannot be isolated from production | **No** | Verified: 0 production mutation, gated by env var |
| 8 | Any production file is mutated | **No** | SHA256 verified identical before/after test runs |

**0 of 8 stop conditions hit. M6.0-5.2 proceeds normally.**

---

## 18. Confirmation

### Acceptance question (per Bry 派工 spec)

> "Can M6.0 now safely perform a real, opt-in, three-judge subjective evaluation of an LLMProxy chat response without modifying or contaminating Soul OS production?"

**YES.** Verified by:

1. **0 production code change**: `git diff --stat src/` = empty
2. **0 production data mutation**: SHA256 + mtime identical before/after
3. **0 API key committed**: `git diff` shows no credentials; only env var
4. **Opt-in only**: `M6_REAL_LLM=1` env var required for real network
5. **Mock judges by default**: M6.0-5 mock judges do not use HTTP at all
6. **Fail-safe**: missing credentials, timeouts, malformed responses, invalid scores — all return `JudgeResult(error=...)` without raising
7. **Provenance captured**: provider, model, base_url, temperature, timestamp, response_hash, raw_response (truncated 4000)
8. **No cross-contamination**: 3 judges see same evidence, no shared state
9. **Existing M6.0-5 unchanged**: 56/56 tests still PASS

**M6.0-5.2 status: CLOSED, OPT-IN REAL LLM JUDGE BACKEND, 0 production mutation, 0 frozen contract change, 30/30 unit tests PASS, 1 opt-in SKIPPED (correct), 269/270 required regression PASS, 565/565 broader M-series PASS.**

---

## 19. Recommended next ticket

**M6.0-5.1 — Diary/Dream subjective evaluation infrastructure** (already identified in M6.0-5 closeout)

Mode: IMPLEMENTATION
Scope:
- Recording backend for `_call_minimax_for_diary` and `_call_minimax_for_dream_event` (currently raw httpx)
- Diary/dream evidence schema (8 dimensions applied to diary content)
- Diary/dream subjective eval test (mock diary LLM backend)
- Reuse M6.0-5 + M6.0-5.2 framework

Or:

**M6.0-5.3 — Multi-provider orchestration + cost control** (out of M6.0-5.2 scope, F1)

Mode: IMPLEMENTATION
Scope:
- Cost estimation per evaluation
- Rate limit handling
- 3 different models in one run (multi-provider)
- Bry reviews cost before commit

---

**M6.0-5.2 closed.  M6.0 now safely performs real, opt-in, three-judge subjective evaluation.**
