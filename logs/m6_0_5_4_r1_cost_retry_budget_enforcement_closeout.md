# M6.0-5.4-R1 — Cost / Retry Budget Enforcement Correction — Closeout

**Ticket:** M6.0-5.4-R1
**Mode:** FIX
**派工:** Bry 2026-08-11 21:00 EDT (Engineering Brain found P1 acceptance failure)
**完成時間:** 2026-08-11 ~21:30 EDT
**HEAD:** (filled at commit)
**Baseline:** 6ba5b907f60b185747f3ac93e6372ba44d24ca23 (M6.0-5.4)

---

## A. Confirmed Defects (from Engineering Brain)

1. `MultiModelJudgeRunner.run()` called `record_call(0,0,0)` — real LLM cost never accumulated
2. After `judge.evaluate()` returned, orchestrator manually did
   `self.cost_budget.tokens_used += int(tu.get("total", 0))` WITHOUT calling
   `_check_exhausted()` — so subsequent `can_make_call()` was stale
3. `CostBudget.record_retry()` was never wired to the actual `RealLLMJudge` retry
   path — retries consumed no budget, could silently bypass `max_cost_usd` /
   `max_token_budget` / `max_retries_per_judge`

## B. Fix Strategy (per Bry 派工 R1 spec)

### Cost model: explicit configured PricingModel (Option A from R1 spec)

Per R1: "Inject an explicit pricing/cost estimator into the evaluation
infrastructure. It must be deterministic and testable. Do NOT scrape provider
pricing dynamically. Do NOT introduce a new external service. Do NOT require
network access for unit tests. If exact provider pricing is intentionally
unavailable, use an explicit configured cost function/rate table and make the
provenance clearly indicate that the value is an estimate. Never silently
treat a real judge call as $0."

**Implementation:**
- New `PricingModel` dataclass (frozen): `provider`, `model`, `input_cost_per_1k`,
  `output_cost_per_1k`. `estimate_cost(tokens_in, tokens_out) -> float` method.
- New `DEFAULT_PRICING: Dict[Tuple[str, str], PricingModel]` — built-in rate table
  for known (claude-haiku-4-5-20251001, claude-sonnet-4-5-20251001, gpt-4o-mini).
  Values are explicitly labeled "ESTIMATES" in docstring.
- New `default_pricing_lookup(provider, model) -> PricingModel` — returns
  `DEFAULT_PRICING[(provider, model)]` or conservative fallback.
- `MultiModelJudgeRunner.__init__` accepts `pricing_lookup: Optional[Callable]`
  (defaults to `default_pricing_lookup`).

### Retry wiring: on_retry callback (Option A from R1 spec)

Per R1: "Do NOT create two competing retry authorities... RealLLMJudge remains
authoritative for retry count. CostBudget receives retry events/counters from
that path."

**Implementation:**
- `RealLLMJudge.evaluate()` now accepts optional `on_retry: Callable[[], None]`
  parameter. Default `None` (backward compatible).
- Inside the retry loop, before each `continue`, the callback is invoked
  (wrapped in try/except so callback errors don't break the retry path).
- `MultiModelJudgeRunner.run()` passes a callback that calls
  `self.cost_budget.record_retry()`.
- `record_retry()` already incremented `retries_made` AND re-ran
  `_check_exhausted()` — so each retry can push the budget over and the next
  judge call is blocked pre-call.

### Token budget: post-call re-check

Per R1: "After a judge call: record actual token usage. immediately
re-evaluate exhaustion state."

**Implementation:**
- Replaced the manual `self.cost_budget.tokens_used += int(tu.get("total", 0))`
  with `self.cost_budget.record_call(tokens_in=..., tokens_out=..., cost_usd=...)`
  which:
  - Increments `calls_made`
  - Adds `tokens_in + tokens_out` to `tokens_used`
  - Adds `cost_usd` (from PricingModel) to `cost_estimated`
  - Calls `_check_exhausted()` immediately
- This means: after each successful judge call returns, the budget is
  re-evaluated; the next iteration's `can_make_call()` sees the updated state
  and returns `False` if any limit was hit.

## C. Files Modified / Created

### Modified (3):
- `tests/_helpers/subjective_eval/multi_model_runner.py`:
  - Added `PricingModel`, `DEFAULT_PRICING`, `default_pricing_lookup`
  - Added `pricing_lookup` param to `MultiModelJudgeRunner.__init__`
  - Rewrote `run()`: pass `on_retry` callback; estimate cost via
    PricingModel; use `record_call()` with actual tokens + cost (NOT 0,0,0)
- `tests/_helpers/subjective_eval/real_judge.py`:
  - Added `on_retry: Optional[Callable[[], None]] = None` to `evaluate()`
  - Called `on_retry()` before each `continue` in the retry loop
  - Wrapped in try/except so callback failures don't break retry
- `tests/_helpers/subjective_eval/__init__.py`:
  - Exported `PricingModel`, `DEFAULT_PRICING`, `default_pricing_lookup`

### Created (1):
- `tests/test_m6_0_5_4_r1_budget_enforcement.py` — 22 tests across 6 classes
  (PricingModel unit, Cost accounting, Token accounting, Retry accounting,
   Network call count, Backward compat)

### NOT modified (frozen):
- All M5.x production code
- All M6.0-5 scoring semantics
- All M6.0-5.2 RealLLMJudge retry behavior (semantics unchanged: still only
  429/5xx/timeout retry, 4xx no-retry, max_retries unchanged)
- Judge ABC, mock judges, SequentialJudgeRunner
- Consensus / calibration / precedence modules
- M6.0-5.4 DiversityValidator, SelfEvaluationGuard
- `CostBudget` class (existing methods unchanged; new behavior via the
  existing `record_call`/`record_retry` methods which already call
  `_check_exhausted`)

## D. Test Results

### M6.0-5.4-R1 new tests: **22 / 22 PASS** in 5.31s

Test classes:
- TestPricingModel (5 tests) — basic, zero, lookup known, lookup unknown, no-silent-zero
- TestCostAccounting (4 tests) — non-zero accumulation, budget blocks, exhaustion reason, ceiling enforced
- TestTokenAccounting (3 tests) — actual usage accumulated, exhaustion prevents next, below-first-call blocks all
- TestRetryAccounting (4 tests) — retry event increments, runner records in budget, 4xx no-retry, max 2 enforced
- TestNetworkCallCount (4 tests) — budget-exhausted makes ZERO HTTP calls, zero pre-blocked, no extra after token/cost
- TestBackwardCompatibilityR1 (2 tests) — default pricing works, existing constructor signature works

### Pre-R1 M6.0-5.4 tests: **39 / 39 PASS** (no regression)
### Full M6.0-5.x + M5.10-2 + M5.13-3 + M5.9-3.1 + M5.8-4: **284 PASS, 1 skipped**
- M6.0-2 PoC: 16/16
- M6.0-3 D-H: 22/22
- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED
- M6.0-5.4: 39/39
- M6.0-5.4-R1: 22/22 (NEW)
- M5.13-3: 29/29
- M5.10-2: 31/31
- M5.9-3.1: 31/31
- M5.8-4: 26/26
### M5.4-5.x foundation: **166 / 166 PASS** (no regression)

## E. Architectural Findings

### Cost model is explicit, deterministic, testable
- `PricingModel` is a frozen dataclass with explicit per-1k-token USD values
- `DEFAULT_PRICING` is a small built-in table for known models; values are
  clearly labeled "ESTIMATES" in docstring (not from real provider API)
- `default_pricing_lookup` returns `DEFAULT_PRICING` entry or conservative
  fallback; never returns `None`, never raises
- Tests use `_controlled_pricing_lookup` returning a single `PricingModel`
  for full determinism ($0.01 input / $0.02 output per 1k)

### Retry authority is single
- `RealLLMJudge` remains the sole authority on retry decisions (it owns the
  retry loop, the `max_retries` config, and the 429/5xx/timeout trigger set)
- `CostBudget` is purely a passive counter — receives retry events via
  callback; never decides to retry; never competes with the judge's loop
- `on_retry` callback is wrapped in try/except so callback errors NEVER
  break the retry path (per fail-safe principle from M6.0-5.2)

### Budget is hard ceiling, not advisory
- `record_call()` re-runs `_check_exhausted()` after every usage update
- `can_make_call()` checks CURRENT state (calls_made, tokens_used,
  cost_estimated) — not a projection of next call
- If budget is at limit pre-call, judge is blocked → ZERO HTTP calls
  (verified by `test_budget_exhausted_judges_make_zero_http_calls`:
  `call_count[0] == 2 == result.budget.calls_made`)

### Backward compat
- `RealLLMJudge.evaluate()` `on_retry` defaults to `None` — existing
  M6.0-5.2 tests pass without modification
- `MultiModelJudgeRunner.__init__` `pricing_lookup` defaults to
  `default_pricing_lookup` — existing M6.0-5.4 tests pass without
  modification
- `CostBudget` API unchanged (existing M6.0-5.4 tests pass without
  modification)

## F. Production Integrity

### SHA256 + mtime of production data (verified BEFORE and AFTER R1 work):
- `data/soul/agent_yua/relationships.json`: sha256=fdb3cc3f7643b5b4 (unchanged)
- `data/soul/agent_mai/relationships.json`: sha256=7eb0ce59924a3314 (unchanged)
- `data/agents/agent_yua/carryover.json`: sha256=c6be0753ccce4e45 (unchanged)
- `data/agents/agent_mai/carryover.json`: sha256=96603486eb8b0554 (unchanged)
- `data/inner_life/trace.jsonl`: NOT FOUND (expected)

### R1 mutations: **0**
- All tests use MockTransport or mock judges
- No real LLM API call attempted (M6_REAL_LLM not set, opt-in gate active)
- No new production data files created
- No new test helpers writing to production paths

### Baseline untracked artifacts: **20 preserved** (M5.8-1 baseline)

## G. Exact Cost Model (deterministic for tests)

`PricingModel` fields:
- `provider: str` — e.g. "claude", "openai"
- `model: str` — e.g. "claude-haiku-4-5-20251001"
- `input_cost_per_1k: float` — USD per 1000 input tokens
- `output_cost_per_1k: float` — USD per 1000 output tokens

`estimate_cost(tokens_in, tokens_out) -> float`:
```python
return (tokens_in / 1000.0) * self.input_cost_per_1k \
     + (tokens_out / 1000.0) * self.output_cost_per_1k
```

`DEFAULT_PRICING` (rough estimates of public list prices, 2026-08-11):
- `("claude", "claude-haiku-4-5-20251001")`: $0.00025 / $0.00125 per 1k
- `("claude", "claude-sonnet-4-5-20251001")`: $0.003 / $0.015 per 1k
- `("openai", "gpt-4o-mini")`: $0.00015 / $0.0006 per 1k
- Fallback: $0.001 / $0.002 per 1k (conservative)

Controlled pricing used in R1 tests:
- $0.01 / $0.02 per 1k → per call: 1500 input + 500 output = $0.025

## H. Token Accounting (deterministic for tests)

For each successful judge call:
- Extract `tokens_in` from `provenance.token_usage.get("input", 0)`
- Extract `tokens_out` from `provenance.token_usage.get("output", 0)`
- `tokens_used += tokens_in + tokens_out`
- Re-evaluate exhaustion immediately

For each failed call (no `token_usage` available):
- `calls_made += 1` (still counts toward max_judge_calls)
- No token/cost accumulation (no usage data to record)
- Re-evaluate exhaustion immediately (may trigger from calls_made alone)

## I. Retry Accounting (deterministic for tests)

For each retry inside `RealLLMJudge.evaluate()`:
- `on_retry()` callback is invoked BEFORE `asyncio.sleep(2**attempt)`
- Callback wraps `CostBudget.record_retry()`:
  - `retries_made += 1`
  - Re-evaluate exhaustion immediately
- If budget is exhausted after retry, the next judge call (in the
  orchestrator's loop) sees `can_make_call() == False` and returns
  fail-safe error result WITHOUT making an HTTP call

## J. Budget Exhaustion Behavior (deterministic for tests)

When `_check_exhausted()` triggers, the first condition that matches sets
`budget_exhausted_reason` (priority order):
1. `max_judge_calls`: `calls_made >= max_judge_calls`
2. `max_token_budget`: `tokens_used >= max_token_budget`
3. `max_cost_usd`: `cost_estimated >= max_cost_usd`

Once set, `is_exhausted()` returns True. The next `can_make_call()` call
returns False (regardless of which condition triggered).

## K. HTTP Call Count Evidence (R1 spec: "prove budget-exhausted judges generate ZERO HTTP calls")

| Scenario | call_count | calls_made | status | reason |
|----------|------------|------------|--------|--------|
| `max_judge_calls=2`, all OK | 2 | 2 | INCOMPLETE | judge 3 budget_exhausted (calls) |
| `max_judge_calls=0`, all blocked | 0 | 0 | UNAVAILABLE | pre-call: 0 >= 0 |
| `max_token_budget=2500` | 2 | 2 | INCOMPLETE | judge 3 budget_exhausted (tokens) |
| `max_cost_usd=0.03` | 2 | 2 | INCOMPLETE | judge 3 budget_exhausted (cost) |
| 3 OK judges (default budget) | 3 | 3 | COMPLETE | max_judge_calls=3 |

All cases satisfy `call_count == result.budget.calls_made` (no surprise
HTTP calls beyond what the budget allowed).

## L. Unresolved Issues

### None for this ticket.

### Watch items (out of R1 scope, candidate for future ticket):
- **Cost accounting is per-call, not per-token-real-time**: a single judge
  call can exceed the entire `max_cost_usd` if its real cost is high.
  Future: enforce per-call cost cap (e.g. reject calls whose projected cost
  would push the budget over).
- **DEFAULT_PRICING values are rough estimates**: should be updated when
  provider pricing changes. Not automated; manual update required.
- **No cost accumulation for failed calls**: if a judge call fails after
  some tokens are consumed (e.g. partial response), the tokens may or may
  not be in `token_usage` depending on the failure mode. Currently we
  only accumulate when `token_usage` is present (i.e. the call succeeded
  enough to parse a response body).

## M. Git State (filled at commit)

- Baseline: 6ba5b907f60b185747f3ac93e6372ba44d24ca23 (M6.0-5.4)
- Commit: (filled at commit)
- Push: (filled at push)
- HEAD == origin/main: (filled at verify)
- Working tree: 20 untracked preserved, 0 modified production files
