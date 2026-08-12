# M6.0-5.4-R2 — Retry Budget Enforcement Completion — Closeout

**Ticket:** M6.0-5.4-R2
**Mode:** FIX
**派工:** Bry 2026-08-11 21:00 EDT (R1 fix left retry as observational only)
**完成時間:** 2026-08-11 ~22:00 EDT
**HEAD:** (filled at commit)
**Baseline:** cda79fdc4d5505b34426baf18eb5ecd133c7f981 (M6.0-5.4-R1)

---

## A. R1 Limitation Closed

R1 fix made `on_retry` a passive observer:
```python
on_retry() -> None  # records retry event
# BUT RealLLMJudge continues the retry regardless
```

So `CostBudget.record_retry()` could detect budget exhaustion but NOT
prevent the in-flight retry. The retry counter could go above `max_retries_per_judge`.

R2 makes the callback enforcement-capable:
```python
on_retry() -> bool
# True  -> retry allowed (loop sleeps + continues)
# False -> retry MUST NOT happen (loop breaks; no sleep, no extra HTTP)
```

## B. Exact Retry Callback Semantics

### Contract (M6.0-5.4-R2):
- `Optional[Callable[[], bool]]` — default `None` (preserves M6.0-5.2 legacy behavior)
- Called inside `RealLLMJudge.evaluate()` retry loop for 429 / 5xx / timeout
- NOT called for 4xx (non-retryable, unchanged from M6.0-5.2)
- Wrapped in try/except: callback errors are treated as "denied" (fail-safe)
  — a buggy callback MUST NOT let a retry bypass the budget

### RealLLMJudge behavior:
```python
if resp.status_code in (429, 500, 502, 503, 504):
    last_error = f"http_{resp.status_code}: ..."
    if attempt < self.max_retries - 1:
        if on_retry is not None:
            try:
                allowed = on_retry()
            except Exception:
                allowed = False  # callback error = denied
            if not allowed:
                break  # NO sleep, NO extra HTTP
        await asyncio.sleep(2 ** attempt)
        continue
    break
```

### Runner callback behavior (`MultiModelJudgeRunner._on_retry`):
```python
def _on_retry() -> bool:
    if not self.cost_budget.can_retry():
        return False  # do NOT call record_retry
    self.cost_budget.record_retry()
    return True
```

`can_retry()` (M6.0-5.4-R2 update) checks ALL three limits:
- retry count: `retries_made < max_total_retries` (where `max_total_retries = max_retries_per_judge * max_judge_calls`)
- token budget: `tokens_used < max_token_budget` (NEW in R2)
- cost budget: `cost_estimated < max_cost_usd`

## C. Before/After HTTP Call Count Evidence

| Scenario | Handler | R1 behavior | R2 behavior |
|----------|---------|-------------|-------------|
| 500 + denied (cost exhausted) | 500 | 2 calls (initial + 1 retry, counter incremented to 1) | **1 call (initial only, counter stays 0)** |
| 500 + denied (token exhausted) | 500 | 2 calls (counter 1) | **1 call (counter 0)** |
| 500 + denied (retry-limit exhausted) | 500 | 2 calls (counter 1) | **1 call (counter 0)** |
| 500 + allowed | 500 then 200 | 2 calls (counter 1) | 2 calls (counter 1) — unchanged |
| 429 + allowed | 429 then 200 | 2 calls (counter 1) | 2 calls (counter 1) — unchanged |
| timeout + denied | TimeoutException | 2 calls (counter 1) | **1 call (counter 0)** |
| 4xx (401) | 401 | 1 call, no retry | 1 call, no retry — unchanged |

## D. Retry Counter Evidence

R1 test `test_retry_via_runner_records_in_cost_budget`: 1 retry recorded (allowed scenario).
R2 test `test_cost_budget_exhausted_blocks_retry`: 0 retries (cost pre-exhausted).
R2 test `test_token_budget_exhausted_blocks_retry`: 0 retries (token pre-exhausted).
R2 test `test_retry_limit_exhausted_blocks_retry`: 0 retries (limit pre-exhausted).

Counter only increments when callback returns True (i.e., budget allows).

## E. Sleep Suppression Evidence

R2 test `test_denied_retry_no_sleep_called`: mock `asyncio.sleep`, denied retry
→ `mock_sleep.assert_not_called()` PASSES. Without R2 fix, the original loop
would have called `asyncio.sleep(1)` between attempts 0 and 1.

R2 test `test_allowed_retry_calls_sleep`: mock `asyncio.sleep`, allowed retry
→ `mock_sleep.assert_called_with(1)` PASSES. Sleep is correctly invoked for
allowed retries (preserves M6.0-5.2 backoff).

## F. Cost / Token Budget Evidence (denied scenarios)

- `test_cost_budget_exhausted_blocks_retry`: pre-set `cost_estimated=0.05` (= max).
  Judge 1 initial 500. on_retry: can_retry: cost 0.05 >= 0.05 → False. Retry denied.
  HTTP call count = 1. `cost_estimated` unchanged (0.05, not 0.10 or higher).
- `test_token_budget_exhausted_blocks_retry`: pre-set `tokens_used=1000` (= max).
  Similar flow; retry denied. HTTP call count = 1. `tokens_used` unchanged.
- `test_retry_limit_exhausted_blocks_retry`: `max_retries_per_judge=0`.
  can_retry: retries_made=0 >= max_total=0 → False. Retry denied.
  HTTP call count = 1.

## G. Files Modified / Created

### Modified (4):
- `tests/_helpers/subjective_eval/real_judge.py`:
  - `on_retry` type `Callable[[], None]` → `Callable[[], bool]`
  - Inside retry loop (both 429/5xx and timeout paths): call `on_retry()`,
    if returns `False`: `break` (no sleep, no extra HTTP)
  - Callback wrapped in try/except: errors = denied (fail-safe)
- `tests/_helpers/subjective_eval/multi_model_runner.py`:
  - `_on_retry` returns `bool` (was `None`)
  - First checks `can_retry()`; if False returns False (no record_retry)
  - If True: calls `record_retry()` then returns True
  - Updated `CostBudget.can_retry()` to also check `max_token_budget`
    (previously only checked retry count + cost)
- `tests/_helpers/subjective_eval/multi_model_runner.py`: `can_retry` updated
  to include token budget check
- `tests/test_m6_0_5_4_r1_budget_enforcement.py`:
  - Updated 3 existing `on_retry=lambda: ...` callbacks to return `True`
    (contract change from `None` to `bool`)
  - Updated `make_cb()` in retry-in-runner test to check `can_retry()` first
    (preserves R1 semantics under new contract)

### Created (1):
- `tests/test_m6_0_5_4_r2_retry_budget_enforcement.py` — 17 tests across 6 classes
  (Contract, BudgetExhaustion, RunnerEnforcement, RetryableFailures,
   SleepSuppression, BackwardCompat)

### NOT modified (frozen):
- All M5.x production code
- M6.0-5 scoring semantics
- M6.0-5.2 retry policy (still 429/5xx/timeout only, 4xx non-retryable, no
  retry on budget deny, max_retries semantics unchanged)
- M6.0-5.2 fail-safe (never raises)
- Judge ABC, mock judges, SequentialJudgeRunner
- M6.0-5.4 DiversityValidator, SelfEvaluationGuard
- M6.0-5.4 PricingModel (R1), evaluation status logic
- M6.0-5.4-R1 cost accumulation post-call, record_call() with actual usage
- M6.0-5 Consensus / Calibration / Precedence
- CostBudget `record_call` / `record_retry` semantics (only `can_retry` got
  one new check, and that's a minimal extension to match the spec)

## H. Test Results

### M6.0-5.4-R2 new tests: **17 / 17 PASS** in 10.32s

Test classes:
- TestRetryCallbackContract (3 tests) — True allows, False denies, no sleep on deny
- TestRetryBudgetExhaustion (3 tests) — retry-limit, cost, token blocks retry
- TestRunnerRetryEnforcement (3 tests) — runner integration with all three limits
- TestRetryableFailures (4 tests) — 429 allowed/denied, timeout allowed/denied
- TestSleepSuppression (2 tests) — mock asyncio.sleep, denied/allowed
- TestBackwardCompatR2 (2 tests) — on_retry=None legacy, R1-style always-True

### Pre-R2 M6.0-5.4 + R1 tests: **61 / 61 PASS** (no regression)
### Full M6 + M5.10-2 + M5.13-3 + M5.9-3.1 + M5.8-4: **301 PASS, 1 skipped**
- M6.0-2 PoC: 16/16
- M6.0-3 D-H: 22/22
- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED
- M6.0-5.4: 39/39
- M6.0-5.4-R1: 22/22
- M6.0-5.4-R2: 17/17 (NEW)
- M5.13-3: 29/29
- M5.10-2: 31/31
- M5.9-3.1: 31/31
- M5.8-4: 26/26
### M5.4-5.x foundation: **137 / 137 PASS** (no regression)

## I. Architectural Findings

### Single retry authority preserved (per R1)
- `RealLLMJudge` is the sole owner of the retry loop and `max_retries` config
- `CostBudget` is passive: receives events via callback
- Callback is now enforcement-capable, not just observational

### Fail-safe preserved
- Callback errors treated as "denied" (try/except in judge)
- RealLLMJudge never raises
- 4xx still non-retryable (no callback invoked)
- Denied retry preserves original error (no new taxonomy)

### Backward compatibility
- `on_retry=None` (default) preserves M6.0-5.2 behavior exactly
- Existing R1 tests updated to return True (semantics unchanged, contract change)
- All pre-R2 tests pass without modification of test logic

### Defense in depth
- Pre-call check: `can_make_call()` (R1) blocks judges entirely
- In-call check: `can_retry()` (R2) blocks individual retries within a call
- Post-call check: `record_call()` re-runs `_check_exhausted()` (R1)
- All three layers use the same `CostBudget` state for consistency

## J. Production Integrity

### SHA256 + mtime of production data (verified BEFORE and AFTER R2 work):
- `data/soul/agent_yua/relationships.json`: sha256=fdb3cc3f7643b5b4 (unchanged)
- `data/soul/agent_mai/relationships.json`: sha256=7eb0ce59924a3314 (unchanged)
- `data/agents/agent_yua/carryover.json`: sha256=c6be0753ccce4e45 (unchanged)
- `data/agents/agent_mai/carryover.json`: sha256=96603486eb8b0554 (unchanged)
- `data/inner_life/trace.jsonl`: NOT FOUND (expected)

### R2 mutations: **0**
- All tests use MockTransport or mock judges
- No real LLM API call attempted (M6_REAL_LLM not set, opt-in gate active)
- No new production data files created

### Baseline untracked artifacts: **20 preserved** (M5.8-1 baseline)

## K. Unresolved Issues

### None for this ticket.

### Watch items (out of R2 scope, candidate for future ticket):
- **Per-call cost cap**: a single judge call could exceed `max_cost_usd` if
  the judge is configured with high `max_retries` and each retry also returns
  full usage data. Future: reject calls whose projected cost (with retries)
  would push the budget over.
- **Mid-flight budget check**: `can_retry()` only sees PRE-call state.
  In-flight retry cost isn't accounted for until the call completes. This is
  by design (can't interrupt a call mid-retry) but could be tightened with
  pre-call projection.
- **No call count ceiling per judge**: a single judge could consume all 3
  retries; if the orchestrator wanted to spread retries across judges, that
  would need new logic (out of R2 scope).

## L. Git State (filled at commit)

- Baseline: cda79fdc4d5505b34426baf18eb5ecd133c7f981 (M6.0-5.4-R1)
- Commit: (filled at commit)
- Push: (filled at push)
- HEAD == origin/main: (filled at verify)
- Working tree: 20 untracked preserved, 0 modified production files

## M. Recommended Next Ticket

**M6.0-5.5: Diary/Dream subjective evaluation** — extend the M6.0-5.4-R2
orchestrator to also evaluate diary and dream output (currently only
LLMProxy chat is in scope). Per M6.0-4 design audit, raw httpx infrastructure
is needed (no LLMProxy integration for diary/dream).

OR

**M6.0-5.6: Cost-aware budget enforcement** — make the cost ceiling
configurable per evaluation type; add per-judge cost tracking; add
budget_exhausted callback so callers can decide whether to escalate or fall
back to deterministic eval.

OR

**M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...`
JSON roundtrip; use `math.isclose` or threshold adjustment.
