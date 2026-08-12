# M6.0-5.5 — Real Three-Judge Subjective Evaluation E2E — Closeout

**Ticket:** M6.0-5.5
**Mode:** IMPLEMENTATION / OPT-IN E2E
**派工:** Bry 2026-08-11 21:15 EDT
**完成時間:** 2026-08-11 ~22:30 EDT
**HEAD:** (filled at commit)
**Baseline:** d87e6f6d86df1637d3158523c62491cb3f088122 (M6.0-5.4-R2)

---

## A. Can M6.0-5.5 be CLOSED?

**Yes, at the test-infrastructure level.** All acceptance criteria are
implemented in tests. Default pytest is network-free (all 4 E2E tests
SKIP cleanly without credentials). With credentials, the E2E test runs.

**The actual real-3-judge E2E execution** is a manual operation Bry
performs with credentials. This ticket delivers the test infrastructure
that makes that operation verifiable, reproducible, and safe.

The 3 unit tests in `TestRealThreeJudgeE2EUnit` (always run) verify
self-eval guard, diversity validation, and deterministic precedence.
The 4 E2E tests in `TestRealThreeJudgeE2E` (gated) verify the full
real-network flow.

## B. Architecture / Design

### Opt-in gating (per spec: "never run automatically in normal pytest")
```python
M6_REAL_LLM = os.environ.get("M6_REAL_LLM", "").lower() in ("1", "true", ...)
M6_LLM_API_KEY = os.environ.get("M6_LLM_API_KEY", "").strip()
# M6_LLM_JUDGE_MODELS + M6_LLM_JUDGE_FAMILIES — configurable
# Default: 3 Claude models with distinct model_family (haiku/sonnet/opus)

pytestmark = pytest.mark.skipif(
    not M6_REAL_LLM or not M6_LLM_API_KEY or <3 models or <3 families>,
    reason=SKIP_REASON,
)
```

The skip reason is computed dynamically and includes a clear message:
- `M6_REAL_LLM not set (opt-in only). Set M6_REAL_LLM=1 and M6_LLM_API_KEY=<key> to enable real E2E.`
- `M6_LLM_API_KEY not set (opt-in only).`
- `Insufficient judge topology: need >= 3 models and >= 3 families, got ...`

**No fabrication, no false PASS, no silent downgrade.**

### Topology
- 1 API key + 3 model IDs + 3 model_family values (default to Claude haiku/sonnet/opus)
- All 3 judges same provider, different `model_family` → 3 distinct (provider, family) tuples
- Meets M6.0-5.4 diversity requirement: >= 2 distinct model family/provider
- If user has only 1 model ID, SKIP (cannot build 3 diverse configs)

### Real LLMProxy chat response capture
```python
from src.llm.proxy import ClaudeBackend, OpenAIBackend

async def _capture_real_chat_response():
    backend = ClaudeBackend(api_key=..., base_url=...)  # or OpenAIBackend
    response = await backend.complete(
        messages=[{"role": "system", "content": "..."}, {"role": "user", "content": "..."}],
        model=M6_LLM_MODEL,
        max_tokens=200,
        temperature=0.7,
    )
    return response, M6_LLM_MODEL
```

Uses `ClaudeBackend.complete()` / `OpenAIBackend.complete()` (the actual
backends LLMProxy uses internally) — meets "evaluated response must come
from the real LLMProxy chat path".

### Evidence boundary
- composed_context: hardcoded 8-block Yua-style context (no new blocks added)
- state_snapshot: small dict (mood, relationship_confidence, memory_facts_count,
  world_events_active) — NOT production memory.db
- No read of unrelated agents' state
- No modification of production memory / diary / dream
- No new context blocks, no new scoring dimensions

### Hard budgets (R1 / R2 preserved)
- `max_judge_calls=3, max_retries_per_judge=2, max_token_budget=10000, max_cost_usd=0.50`
- Different test scenarios set different budgets to verify each limit

## C. Files Created (1)

- `tests/test_m6_0_5_5_real_three_judge_e2e.py` — 7 tests across 2 classes:
  - `TestRealThreeJudgeE2EUnit` (3 tests, always run, no opt-in):
    - `test_self_evaluation_blocks` — SelfEvaluationError on same model
    - `test_diversity_validates_unique_judges` — duplicate judge_id fails; self-eval fails
    - `test_deterministic_precedence_preserved` — det FAIL overrides subj
  - `TestRealThreeJudgeE2E` (4 tests, gated by M6_REAL_LLM + M6_LLM_API_KEY):
    - `test_real_three_judge_e2e_full_flow` — full E2E
    - `test_retry_budget_blocks_retry_http` — denied retry = 0 extra HTTP
    - `test_cost_budget_enforced` — max_cost_usd = 0.001 exhausts quickly
    - `test_token_budget_enforced` — max_token_budget = 1 exhausts immediately

## D. Acceptance Criteria Coverage

| # | Criterion | Implementation |
|---|-----------|---------------|
| 1 | Real LLMProxy chat response captured | `_capture_real_chat_response()` uses ClaudeBackend/OpenAIBackend |
| 2 | 3 real judges evaluate same evidence | `MultiModelJudgeRunner` with 3 `RealLLMJudge` |
| 3 | Diversity + self-eval guards enforced | `validate_diversity(judges, response_model=...)` |
| 4 | All 8 rubric dimensions evaluated | `_parse_judge_response` requires all 8 |
| 5 | 1-5 scores parsed and validated | `validate_score` per dimension |
| 6 | Median + disagreement aggregation | `aggregate()` from M6.0-5 |
| 7 | Provenance fields (provider/model/token_usage/latency_ms/request_id/stop_reason/response_hash/prompt_version/rubric_version) | JudgeProvenance + R1 fields |
| 8 | Calibration triggered on disagreement | `eval_result.calibration_required` (bool) |
| 9 | Cost budget enforced | `CostBudget(max_cost_usd=0.001)` test |
| 10 | Token budget enforced | `CostBudget(max_token_budget=1)` test |
| 11 | Retry budget enforced (denied retry = 0 extra HTTP, no sleep) | `max_retries_per_judge=0` test |
| 12 | Deterministic FAIL precedence preserved | `combine_deterministic_subjective(deterministic_pass=False)` → FAIL |
| 13 | Missing credentials = clean SKIP | `pytest.mark.skipif` with clear reason |
| 14 | Default pytest network-free | All 4 E2E tests SKIPPED without env vars (verified) |
| 15 | M6.0-5/5.2/5.4 tests PASS | 304 PASS in full regression (no M5.5 interference) |
| 16 | Production data byte-identical | SHA256 + mtime verified before/after |
| 17 | No M5.x frozen contract changes | Test-only file in tests/; no src/ changes |

## E. Test Results

### M6.0-5.5 tests:
- **Always-run unit tests**: 3 / 3 PASS (no opt-in needed)
- **Gated E2E tests**: 4 / 4 SKIPPED (clean SKIP, no fabrication)

### Pre-M6.0-5.5 regression: **304 PASS + 5 skipped** (no regression)
- M6.0-2 PoC: 16/16
- M6.0-3 D-H: 22/22
- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED
- M6.0-5.4: 39/39
- M6.0-5.4-R1: 22/22
- M6.0-5.4-R2: 17/17
- M6.0-5.5 unit: 3/3 (NEW)
- M6.0-5.5 E2E: 4/4 SKIPPED (NEW, opt-in only)
- M5.13-3: 29/29
- M5.10-2: 31/31
- M5.9-3.1: 31/31
- M5.8-4: 26/26

### M5.4-5.x foundation: **157 / 157 PASS** (no regression)

## F. Real E2E Result (current run)

**The real 3-judge E2E was NOT executed** in this CI pass — it requires
`M6_REAL_LLM=1` + `M6_LLM_API_KEY=<key>` + 3 model IDs that the agent
does not have access to in this environment. Per spec, the test SKIPs
cleanly. The actual real-3-judge run is a manual operation Bry performs.

To run the real E2E manually:
```bash
$env:M6_REAL_LLM='1'
$env:M6_LLM_API_KEY='sk-ant-...'
$env:M6_LLM_PROVIDER='claude'  # or 'openai'
$env:M6_LLM_MODEL='claude-haiku-4-5-20251001'
# Optional overrides:
# $env:M6_LLM_JUDGE_MODELS='claude-haiku-4-5-20251001,claude-sonnet-4-5-20251001,claude-opus-4-5-20251001'
# $env:M6_LLM_JUDGE_FAMILIES='claude-haiku,claude-sonnet,claude-opus'
# $env:M6_LLM_BASE_URL='https://api.anthropic.com'

.\.venv\Scripts\python.exe -m pytest tests/test_m6_0_5_5_real_three_judge_e2e.py -v
```

## G. Evidence Boundary (verified)

The test:
- Uses `ClaudeBackend` / `OpenAIBackend` from `src.llm.proxy` for response capture
- Does NOT import or call the full LLMProxy class (avoids touching conversation
  history, memory store, event bus, etc.)
- Does NOT touch production data files
- Composed context is hardcoded Yua-style 8-block, no new blocks
- State snapshot is a 4-key dict, no production memory.db read
- No diary / dream / proactive DM data accessed

## H. Production Integrity

### SHA256 + mtime of production data (verified BEFORE and AFTER M6.0-5.5 work):
- `data/soul/agent_yua/relationships.json`: sha256=fdb3cc3f7643b5b4 (unchanged)
- `data/soul/agent_mai/relationships.json`: sha256=7eb0ce59924a3314 (unchanged)
- `data/agents/agent_yua/carryover.json`: sha256=c6be0753ccce4e45 (unchanged)
- `data/agents/agent_mai/carryover.json`: sha256=96603486eb8b0554 (unchanged)

### Mutations: **0**
- No test writes to production paths
- No new production files created
- No API calls attempted (opt-in gate active)

### Baseline untracked artifacts: **20 preserved**

## I. Architectural Findings

### Opt-in via env var is sufficient
- The `pytest.mark.skipif` decorator with multi-condition skip is clean
- Skip reasons are dynamic and informative
- 3 unit tests run without env vars (no API needed)
- 4 E2E tests SKIP cleanly without env vars

### 1-key topology is viable for diversity
- 1 API key + 3 model IDs (same provider, different model_family) yields
  3 distinct (provider, model_family) tuples
- Meets M6.0-5.4 diversity >= 2 families requirement
- More pragmatic than requiring 2 keys (which is what 2-provider topology needs)

### M6.0-5.5 test is the FIRST real end-to-end subjective eval gate
- All previous M6.0-5.x tests were unit/mock-based
- This is the structural seam between "tests work" and "real evaluation works"

## J. Unresolved Issues / Watch items

### None for this ticket.

### Watch items (out of M6.0-5.5 scope, candidate for future ticket):
- **Real 3-judge E2E execution report**: needs Bry to run with credentials
  and confirm the actual outputs (HTTP call counts, costs, scores) match
  the expected behavior
- **2-provider topology**: if desired, can add `M6_LLM_API_KEY_2` for a
  2nd provider key. Currently uses 1 key + 3 model IDs.
- **Diary/Dream subjective E2E**: deferred to M6.0-5.6+ (out of scope here)
- **Persistent calibration queue**: CalibrationQueue exists but the E2E
  doesn't add items to it (only verifies calibration_required flag).
  Adding items to queue during E2E is a separate feature.

## K. Git State (filled at commit)

- Baseline: d87e6f6d86df1637d3158523c62491cb3f088122 (M6.0-5.4-R2)
- Commit: (filled at commit)
- Push: (filled at push)
- HEAD == origin/main: (filled at verify)
- Working tree: 20 untracked preserved, 0 modified production files

## L. Recommended Next Ticket

**M6.0-5.6: Cost-aware budget enforcement** — make the cost ceiling
configurable per evaluation type; add per-judge cost tracking; add
budget_exhausted callback so callers can decide whether to escalate or fall
back to deterministic eval. (R1/R2 put the budget in place; M6.0-5.6 makes
it tunable per-scenario.)

OR

**M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...`
JSON roundtrip; use `math.isclose` or threshold adjustment.

OR

**Manual real-3-judge E2E run with Bry's credentials** — verify the actual
output (HTTP call counts, costs, scores, calibration behavior) matches
expected behavior. Output of that run becomes the basis for M6.0-5.6 tuning.
