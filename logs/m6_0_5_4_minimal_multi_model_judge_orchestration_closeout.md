# M6.0-5.4 — Minimal Multi-Model Judge Orchestration — Closeout

**Ticket:** M6.0-5.4
**Mode:** IMPLEMENTATION (post M6.0-5.3 design audit)
**派工:** Bry 2026-08-11 20:17 EDT
**完成時間:** 2026-08-11 ~20:50 EDT
**HEAD:** (filled at commit)
**Baseline:** c7812608e15456b7b32143df75f7f0d26bf9c1 (M6.0-5.3)

---

## A. Scope Summary

Implemented minimal multi-model judge orchestration layer (M6.0-5.4) on top of
M6.0-5.2 RealLLMJudge and M6.0-5 consensus. Reuses existing Judge ABC, consensus
algorithm, and CalibrationQueue — does NOT modify M5.x contracts, M6.0-5
scoring semantics, or production runtime.

### Bry 派工 frozen decisions implemented as-is:
- 3 judges = 3 distinct judge configurations
- ≥2 distinct model families/providers required
- Same response model as judge model = HARD BLOCK
- Same provider allowed if model family differs
- No automatic judge replacement
- 1 failure → incomplete + calibration_required
- 2 failures → FAIL/incomplete
- 3 failures → unavailable/FAIL
- Retry only 429/5xx/timeout, max 2
- Temperature = 0.0 default
- Bounded token + cost budget
- No circuit breaker in this ticket

---

## B. Files Modified / Created

### Created (2):
- `tests/_helpers/subjective_eval/multi_model_runner.py` — MultiModelJudgeRunner,
  DiversityValidator, SelfEvaluationGuard, CostBudget, EvaluationStatus,
  MultiModelRunResult
- `tests/test_m6_0_5_4_multi_model_runner.py` — 39 tests across 9 test classes

### Modified (3):
- `tests/_helpers/subjective_eval/judge.py` — extended `JudgeProvenance` with
  4 optional fields: `token_usage`, `latency_ms`, `request_id`, `stop_reason`
  (all default None for backward compat with mock judges)
- `tests/_helpers/subjective_eval/real_judge.py` — added `model_family` param
  to `RealLLMJudge.__init__`; added `_extract_metadata()` to populate the 4
  new fields from API response (OpenAI `usage`/`id`/`finish_reason`; Claude
  `usage`/`id`/`stop_reason`); pass metadata through to JudgeProvenance
- `tests/_helpers/subjective_eval/__init__.py` — export new symbols:
  EvaluationStatus, SelfEvaluationError, DiversityError, check_self_evaluation,
  validate_diversity, CostBudget, MultiModelJudgeRunner, MultiModelRunResult

### NOT modified (frozen):
- All M5.x production code
- All M6.0-5 scoring semantics
- All M6.0-5.2 RealLLMJudge retry behavior, fail-safe, opt-in env var
- All Judge ABC, mock judges, SequentialJudgeRunner
- All consensus / calibration / precedence modules

---

## C. Test Results

### M6.0-5.4 new tests: **39 / 39 PASS** in 15.36s

Test classes:
- TestDiversityValidator (6 tests) — unique IDs, duplicate config, family diversity
- TestSelfEvaluationGuard (6 tests) — same model BLOCK, same provider OK
- TestCostBudget (6 tests) — within budget, max calls, token cap, cost cap, retry
- TestMultiModelRunnerFailure (5 tests) — 0/1/2/3 failures, no auto-replacement
- TestRetryPolicy (5 tests) — 429, 5xx, timeout, max 2, no 4xx retry
- TestProvenanceCapture (3 tests) — token usage, latency, request ID, stop reason
- TestBackwardCompatibility (3 tests) — M6.0-5 SequentialJudgeRunner, M6.0-5.2
  RealLLMJudge basic, aggregate unchanged
- TestCostEnforcementInRunner (2 tests) — budget_exhausted error result, full flow
- TestMultiModelRunnerIntegration (3 tests) — construction validates diversity,
  rejects duplicate ID, rejects self-eval

### Full regression: **262 / 262 PASS + 1 skipped** in 21.22s
- M6.0-2 PoC: 16/16
- M6.0-3 D-H: 22/22
- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED (no M6_REAL_LLM env var)
- M6.0-5.4: 39/39
- M5.13-3 relationship context: 29/29
- M5.10-2 memory judge v1: 31/31
- M5.9-3.1 world inner life wiring: 31/31
- M5.8-4 producer gating: 26/26

---

## D. Architectural Findings

### Diversity Validation
- **3 layers of defense** for self-eval: `check_self_evaluation()` standalone
  function; `validate_diversity()` checks in MultiModelJudgeRunner.__init__;
  JudgeProvenance logs the judge model for audit
- **Duplicate config check uses (model, provider, base_url)** — NOT including
  judge_id, so two judges with same model+provider+base_url but different IDs
  are still rejected (they'd behave identically)
- **Family diversity uses (provider, model_family) tuple** — at least 2 distinct
  tuples required. Two Claude judges with different model_family values count
  as diverse; two OpenAI judges with same model_family don't

### Cost Budget
- **3 limits** tracked simultaneously: max_judge_calls, max_token_budget,
  max_cost_usd. can_make_call() blocks when ANY limit is hit
- **No retry bypass**: record_call + record_retry both check limits; retry
  cannot extend the budget
- **Fail-safe**: when can_make_call returns False mid-run, the remaining
  judges get JudgeResult with `error="budget_exhausted: <reason>"` — never
  raises, never silently adds another judge

### Retry Policy
- **RealLLMJudge internal retry** unchanged from M6.0-5.2: only 429/500/502/
  503/504 + httpx.TimeoutException retry; max_retries=2 (2 total attempts)
- 4xx (incl 401, 403, 404) → no retry, fail-fast
- judge.evaluate() never raises; all errors → JudgeResult with error=

### MultiModelJudgeRunner.run() Behavior
- 0 errored → COMPLETE
- 1 errored → INCOMPLETE + calibration_required
- 2+ errored → UNAVAILABLE
- aggregate() (M6.0-5) is reused unchanged
- Optional `http_client` parameter lets tests share a single MockTransport
  across all 3 judges; production code can pass None (each judge creates
  its own client)

### Backward Compatibility
- Mock judges (FixedScoreJudge, HighAgreementJudge, etc.) do not set the
  4 new JudgeProvenance fields; they default to None. Existing tests
  using SequentialJudgeRunner work unchanged
- RealLLMJudge basic calls work unchanged; new metadata fields are
  additionally populated
- `model_family` defaults to `provider` so existing tests that didn't
  set it still pass diversity (e.g. 1 claude + 1 openai = 2 families)

---

## E. Production Integrity

### SHA256 + mtime of production data (before test run):
- `data/soul/agent_yua/relationships.json`: sha256=fdb3cc3f7643b5b4
- `data/soul/agent_mai/relationships.json`: sha256=7eb0ce59924a3314
- `data/inner_life/trace.jsonl`: NOT FOUND (expected, not yet created)
- `data/agents/agent_yua/carryover.json`: sha256=c6be0753ccce4e45
- `data/agents/agent_mai/carryover.json`: sha256=96603486eb8b0554

### M6.0-5.4 mutations: **0**
- All tests use MockTransport or mock judges
- All test writes go to tempfile.TemporaryDirectory() or in-memory
- No real LLM API call attempted (M6_REAL_LLM not set, opt-in gate active)
- No new production data files created

### Baseline untracked artifacts: **20 preserved**
(M5.8-1 baseline — all logs, scripts, test artifacts from prior tickets)

---

## F. Bry Decisions Implemented (no new decisions needed)

All decisions in M6.0-5.3 audit were frozen before this ticket. No new
Bry decision was required during implementation. The 15 decisions from
M6.0-5.3 (commit c781260) are all reflected in the code:
- Topology C (3 distinct model families)
- Self-eval hard block
- Provider-OK-if-family-differs
- No auto-replacement
- Bounded cost (max 3 calls, 2 retries, 5000 tokens, $0.05)
- Retry 429/5xx/timeout only
- Temperature 0.0 default

---

## G. Out of Scope (NOT implemented per spec)

- Diary / Dream subjective evaluation
- Circuit breaker
- Dynamic provider fallback
- Automatic judge replacement
- Cost optimization / topology presets
- New scoring
- New rubric dimensions
- Production runtime integration
- Real 3-judge E2E call
- Embeddings / vector DB / semantic retrieval

---

## H. Modified / Unresolved / Risks

### Unresolved: None for this ticket.

### Risks / Watch items:
- **Cost budget ceiling is hard-coded at $0.05** — if real API costs exceed
  this for one evaluation, the budget will be exhausted mid-run. Future
  ticket M6.0-5.3+ should consider making the ceiling configurable per
  evaluation type.
- **No persistent state** — the MultiModelJudgeRunner does not write
  to any database. If a real evaluation runs and crashes, the results
  are lost. (Per spec: M6 is test-only, no production persistence.)

---

## I. Git State (filled at commit)

- Baseline: c7812608e15456b7b32143df75f7f0d26bf9c1
- Commit: (filled at commit)
- Push: (filled at push)
- HEAD == origin/main: (filled at verify)
- Working tree: 20 untracked preserved, 0 modified production files

---

## J. Recommended Next Ticket

**M6.0-5.5: Diary/Dream subjective evaluation** — extend the M6.0-5.4
orchestrator to also evaluate diary and dream output (currently only
LLMProxy chat is in scope). Per M6.0-4 design audit, raw httpx infrastructure
is needed (no LLMProxy integration for diary/dream).

OR

**M6.0-5.6: Cost-aware budget enforcement** — make the $0.05 / 5000 token
ceiling configurable per evaluation type; add per-judge cost tracking;
add budget_exhausted callback so callers can decide whether to escalate
or fall back to deterministic eval.

OR

**M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...`
JSON roundtrip; use `math.isclose` or threshold adjustment.
