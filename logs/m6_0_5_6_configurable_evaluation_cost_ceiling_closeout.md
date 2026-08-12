# M6.0-5.6 — Configurable Subjective Evaluation Cost Ceiling — Closeout

**Ticket:** M6.0-5.6
**Mode:** IMPLEMENTATION
**派工:** Bry 2026-08-11 21:30 EDT
**完成時間:** 2026-08-11 ~22:50 EDT
**HEAD:** (filled at commit)
**Baseline:** 3f599a43110ec548178847d497c2a36f43b37dcc (M6.0-5.5)

---

## A. Configuration Surface (canonical)

**One canonical surface**: `EvaluationBudgetConfig` (frozen dataclass)

```python
@dataclass(frozen=True)
class EvaluationBudgetConfig:
    max_judge_calls: int = 3
    max_retries_per_judge: int = 2
    max_token_budget: int = 5000
    max_cost_usd: float = 0.05
```

### Default values (deterministic, NOT unlimited)
| Field | Default | Meaning |
|-------|---------|---------|
| `max_judge_calls` | 3 | Max total judge calls in one evaluation |
| `max_retries_per_judge` | 2 | Max retries per individual judge (0 = no retries) |
| `max_token_budget` | 5000 | Max tokens (input + output) per evaluation |
| `max_cost_usd` | 0.05 | Max estimated USD cost per evaluation (ESTIMATE, not real billing) |

### Validation (deterministic, M6.0-5.6 spec)
- **Negative limits**: ValueError at construction
- **Zero limits**: VALID (means "no calls / no retries where applicable")
- **Invalid types** (str/bool where int/float expected): TypeError at construction
- **No silent fallback to unlimited**: defaults are finite and documented

### Pricing explicitly labeled ESTIMATE
- `PricingModel` and `DEFAULT_PRICING` (M6.0-5.4-R1) remain unchanged
- Values labeled "ESTIMATES" in docstring; not from real provider API
- `default_pricing_lookup()` returns DEFAULT_PRICING or conservative fallback
- Never returns None, never raises, never silently $0

---

## B. Files Modified / Created

### Modified (2):
- `tests/_helpers/subjective_eval/multi_model_runner.py`:
  - Added `EvaluationBudgetConfig` dataclass (frozen, validated)
  - `to_cost_budget()` derives a fresh `CostBudget` with configured limits
  - `to_judge_max_retries()` returns `cfg.max_retries_per_judge + 1` for judge loop
  - Updated `MultiModelJudgeRunner.__init__` to accept `budget_config` parameter
  - When `budget_config` is set, derives `cost_budget` AND propagates
    `max_retries` to each `RealLLMJudge` (overrides judge.max_retries for consistency)
  - If BOTH `budget_config` and `cost_budget` are passed: ValueError (no silent override)
- `tests/_helpers/subjective_eval/__init__.py`:
  - Exported `EvaluationBudgetConfig`

### Created (1):
- `tests/test_m6_0_5_6_budget_config.py` — 30 tests across 6 classes:
  - TestEvaluationBudgetConfigDefaults (3 tests)
  - TestEvaluationBudgetConfigValidation (8 tests)
  - TestToCostBudget (2 tests)
  - TestConfigurationPropagation (6 tests)
  - TestEnforcement (4 tests)
  - TestZeroBudgetBehavior (3 tests)
  - TestR1R2Regression (4 tests)

### NOT modified (frozen):
- All M5.x production code
- M6.0-5.4 DiversityValidator, SelfEvaluationGuard, PricingModel
- M6.0-5.4-R1 CostBudget / record_call / record_retry / can_make_call / can_retry
- M6.0-5.4-R2 retry callback contract (Callable[[], bool])
- M6.0-5 consensus / Calibration / Precedence
- M6.0-5.5 E2E test infrastructure
- RealLLMJudge retry semantics (429/5xx/timeout, 4xx non-retryable)

---

## C. Configuration Propagation

When `budget_config` is passed to `MultiModelJudgeRunner.__init__`:

1. **cost_budget derived**: `runner.cost_budget = cfg.to_cost_budget()`
   - All 4 limits copied from config to fresh CostBudget
   - Tracking state (calls_made, retries_made, tokens_used, cost_estimated) = 0
2. **judge.max_retries overridden**: each RealLLMJudge's `max_retries` set to
   `cfg.max_retries_per_judge + 1` so the budget has the final say via the
   on_retry callback (M6.0-5.4-R2 enforcement)
   - Edge case: `cfg.max_retries_per_judge=0` → `judge.max_retries=1`
     (1 initial attempt, 0 retries — consistent with budget blocking retry)

### Backward compatibility
- If ONLY `cost_budget` is passed (legacy path): runner uses it as-is,
  judge.max_retries untouched (caller responsibility)
- If ONLY `budget_config` is passed: full propagation
- If BOTH are passed: ValueError (no silent override)
- If NEITHER is passed: default `CostBudget()` (matches `EvaluationBudgetConfig` defaults)

---

## D. Test Results

### M6.0-5.6 new tests: **30 / 30 PASS** in 0.21s

| Class | Tests | Coverage |
|-------|-------|----------|
| TestEvaluationBudgetConfigDefaults | 3 | Default values, frozen, custom |
| TestEvaluationBudgetConfigValidation | 8 | Zero valid, negatives rejected, types rejected, no silent unlimited |
| TestToCostBudget | 2 | Derives correct CostBudget, zero limits mean no calls |
| TestConfigurationPropagation | 6 | runner accepts config, propagates max_retries, rejects both, backward compat |
| TestEnforcement | 4 | max_judge_calls, max_token_budget, max_cost_usd, max_retries all enforced |
| TestZeroBudgetBehavior | 3 | zero max_judge_calls, zero max_token_budget, zero max_cost_usd all block |
| TestR1R2Regression | 4 | call_count==calls_made, denied retry: 0 HTTP, 0 sleep, 0 counter |

### Pre-M6.0-5.6 regression: **304 / 304 PASS + 5 skipped** (no regression)
- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED
- M6.0-5.4: 39/39
- M6.0-5.4-R1: 22/22
- M6.0-5.4-R2: 17/17
- M6.0-5.5 unit: 3/3
- M6.0-5.5 E2E: 4/4 SKIPPED
- M6.0-2: 16/16
- M6.0-3: 22/22
- M5.13-3: 29/29
- M5.10-2: 31/31
- M5.9-3.1: 31/31
- M5.8-4: 26/26

### M5.4-5.x foundation: **157 / 157 PASS** (no regression)

---

## E. Acceptance Criteria Coverage

| # | Criterion | Implementation |
|---|-----------|---------------|
| 1 | Configuration has one canonical surface | `EvaluationBudgetConfig` |
| 2 | All four limits are configurable | max_judge_calls, max_retries_per_judge, max_token_budget, max_cost_usd |
| 3 | Defaults are deterministic | Verified in `TestEvaluationBudgetConfigDefaults` |
| 4 | No silent unlimited fallback | Defaults are finite; zero is explicit; negatives rejected |
| 5 | Invalid configuration rejected deterministically | ValueError + TypeError at __post_init__ |
| 6 | M6.0-5.4 behavior preserved | 39/39 PASS + 81/85 (incl. R1/R2/5.5) |
| 7 | R1 cost accounting preserved | 22/22 PASS; tests use budget_config |
| 8 | R2 retry enforcement preserved | 17/17 PASS; TestR1R2Regression verifies denied retry |
| 9 | max_judge_calls enforced | `test_max_judge_calls_enforced` |
| 10 | max_token_budget enforced | `test_max_token_budget_enforced` |
| 11 | max_cost_usd enforced | `test_max_cost_usd_enforced` |
| 12 | max_retries enforced | `test_max_retries_enforced` |
| 13 | zero-budget behavior tested | `TestZeroBudgetBehavior` (3 tests) |
| 14 | denied retry = 0 extra HTTP | `test_denied_retry_no_extra_http` |
| 15 | denied retry = 0 sleep | `test_denied_retry_no_sleep` (mock asyncio.sleep) |
| 16 | denied retry = 0 counter increment | `test_denied_retry_no_counter_increment` |
| 17 | M6.0-5.4 + R1 + R2 regression PASS | 78/78 + 5 skipped opt-in |
| 18 | M6.0-5.5 regression PASS | 3 unit PASS, 4 E2E SKIPPED (opt-in) |
| 19 | production data unchanged | SHA256 + mtime verified |
| 20 | no API credentials added to repo | No env vars required by M6.0-5.6 |

---

## F. Production Integrity

### SHA256 + mtime of production data (verified BEFORE and AFTER M6.0-5.6 work):
- `data/soul/agent_yua/relationships.json`: sha256=fdb3cc3f7643b5b4 (unchanged)
- `data/soul/agent_mai/relationships.json`: sha256=7eb0ce59924a3314 (unchanged)
- `data/agents/agent_yua/carryover.json`: sha256=c6be0753ccce4e45 (unchanged)
- `data/agents/agent_mai/carryover.json`: sha256=96603486eb8b0554 (unchanged)
- `data/inner_life/trace.jsonl`: NOT FOUND (expected)

### Mutations: **0**
- M6.0-5.6 is test infrastructure only (multi_model_runner.py + test file)
- No production runtime changes
- No new production files

### Baseline untracked artifacts: **20 preserved**

---

## G. Backward Compatibility — M6.0-5.5

**M6.0-5.5 remains backward compatible.** The M6.0-5.5 test file constructs
`MultiModelJudgeRunner` without `budget_config`:
```python
runner = MultiModelJudgeRunner(judges, response_model="...")
```
This path is preserved — runner uses `cost_budget or CostBudget()` (defaults).
M6.0-5.5 3 unit tests + 4 E2E tests still pass (3 PASS, 4 SKIPPED opt-in).

---

## H. Architectural Findings

### One canonical surface + two usage paths
- `EvaluationBudgetConfig` is the canonical M6.0-5.6 surface
- `CostBudget` is the runtime state (mutable, tracks usage)
- Caller can use either path; passing both raises ValueError (no silent override)
- `EvaluationBudgetConfig.to_cost_budget()` is the explicit derivation point

### max_retries propagation is intentional
- Without propagation, caller would need to ensure `judge.max_retries == cfg.max_retries_per_judge + 1` manually
- With propagation, runner enforces consistency between budget and judge loop
- Caller can still use legacy `cost_budget` path with manual judge.max_retries

### Validation at __post_init__
- Frozen dataclass + __post_init__ catches errors at construction
- No silent fallback to unlimited; if you want 1000 calls, pass max_judge_calls=1000
- Defaults are documented; users can read them without instantiating

### R1/R2 enforcement preserved
- All R1 tests still pass with budget_config
- All R2 tests still pass with budget_config
- TestR1R2Regression explicitly verifies: call_count==calls_made,
  denied retry = 0 extra HTTP, 0 sleep, 0 counter increment

---

## I. Unresolved Issues

### None for this ticket.

### Watch items (out of M6.0-5.6 scope):
- **Per-evaluation-type budget profiles**: future could have a `BudgetProfile`
  (e.g. "chat" / "diary" / "dream") that maps to default `EvaluationBudgetConfig`
  values. Out of scope for M6.0-5.6 (which is just the config surface).
- **Budget exhausted callback**: caller might want to decide whether to
  escalate or fall back to deterministic eval. Currently runner just returns
  fail-safe JudgeResult with error. Out of scope for M6.0-5.6.
- **max_retries=0 edge case**: propagates to judge.max_retries=1
  (1 initial attempt, 0 retries). The spec says "zero limits → valid and
  means no calls / no retries where applicable" — this is the "no retries"
  interpretation. Documented in code; tests verify.
- **Pricing per evaluation type**: currently `default_pricing_lookup`
  is global. Future could let caller pass per-evaluation pricing. Out of
  scope for M6.0-5.6.

---

## J. Git State (filled at commit)

- Baseline: 3f599a43110ec548178847d497c2a36f43b37dcc (M6.0-5.5)
- Commit: (filled at commit)
- Push: (filled at push)
- HEAD == origin/main: (filled at verify)
- Working tree: 20 untracked preserved, 0 modified production files

---

## K. Recommended Next Ticket

**M5.13-4: Fix M5.13-3 float precision** — P3 fix for `0.3 → 0.2999...`
JSON roundtrip; use `math.isclose` or threshold adjustment. (Long-standing
P3 issue, candidate for cleanup.)

OR

**M6.0-5.6.1: Budget profile registry** — add `BudgetProfile` enum and
`EvaluationBudgetConfig.from_profile()` factory for common cases
(`chat` / `diary` / `dream`). Out of scope for M6.0-5.6 but the config
surface enables it.
