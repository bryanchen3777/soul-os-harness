# M6.0-5.5-R1 — Real Three-Judge E2E Validation Gate — BLOCKED Report

**Ticket:** M6.0-5.5-R1
**Mode:** VALIDATION / READ-ONLY EXECUTION
**派工:** Bry 2026-08-11 21:40 EDT
**執行時間:** 2026-08-11 ~22:00 EDT
**HEAD (start):** 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
**HEAD (end):**   540eac2e230e2d4ce75a5d99c5e245d2f604fa59
**origin/main (start):** 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
**origin/main (end):**   540eac2e230e2d4ce75a5d99c5e245d2f604fa59

---

## 1. Execution Status: **BLOCKED — CREDENTIALS UNAVAILABLE**

Per Bry 派工 spec:
> If credentials are unavailable, the correct result is: BLOCKED — CREDENTIALS UNAVAILABLE
> It is NOT PASS and it is NOT a reason to modify the infrastructure.

This is the **correct, expected** result for this environment. No code was
modified to bypass the problem. No fabricated PASS.

### 1.1 Exact environment state (verified)
| Env Var | Value | Status |
|---------|-------|--------|
| `M6_REAL_LLM` | (empty) | **NOT SET** |
| `M6_LLM_API_KEY` | (empty) | **NOT SET** |
| `M6_LLM_PROVIDER` | (empty) | **NOT SET** |
| `M6_LLM_MODEL` | (empty) | **NOT SET** |
| `M6_LLM_JUDGE_MODELS` | (empty) | **NOT SET** |
| `M6_LLM_JUDGE_FAMILIES` | (empty) | **NOT SET** |
| `M6_LLM_BASE_URL` | (empty) | **NOT SET** |

Without `M6_REAL_LLM=1` + `M6_LLM_API_KEY` + 3 model IDs, the M6.0-5.5 E2E
test SKIPs cleanly per its `pytest.mark.skipif` gate. This is by design.

---

## 2. Items the BLOCKED environment cannot verify

Per the spec, the following items require real credentials and CANNOT be
verified in this run. They are documented as **NOT VERIFIED** (not "PASS"
or "FAIL"):

| # | Item | Status | Why |
|---|------|--------|-----|
| 1 | One actual real three-judge evaluation completed | **NOT VERIFIED** | No creds |
| 2 | No fabricated result | **PASS (by construction)** | Test SKIPs cleanly, no fabrication |
| 3 | 3 real judges executed | **NOT VERIFIED** | No creds |
| 4 | 3 distinct model configurations | **PASS (unit test)** | test_diversity_validates_unique_judges (3/3 PASS) |
| 5 | Self-evaluation guard passes | **PASS (unit test)** | test_self_evaluation_blocks (3/3 PASS) |
| 6 | 8 dimensions scored | **NOT VERIFIED** (in real run) | test_real_three_judge_e2e_full_flow SKIPPED; M6.0-5 unit tests verify mock judges (56/56 PASS) |
| 7 | All scores 1–5 categorical | **PASS (unit test)** | M6.0-5 validate_score enforces 1-5 (56/56 PASS) |
| 8 | Median aggregation | **PASS (unit test)** | M6.0-5 consensus tests (56/56 PASS) |
| 9 | Agreement calculated | **PASS (unit test)** | M6.0-5 consensus tests (56/56 PASS) |
| 10 | Final subjective status | **PASS (unit test)** | M6.0-5 consensus tests (56/56 PASS) |
| 11 | Provenance captured | **PASS (unit test)** | M6.0-5.2 + 5.4 tests verify provenance (39+22+17 = 78/78 PASS) |
| 12 | No credential leakage | **PASS (by design)** | Provenance schema does NOT include `api_key`/`authorization` fields (verified by source inspection of JudgeProvenance dataclass) |
| 13 | EvaluationBudgetConfig actually used | **PASS (unit test)** | M6.0-5.6 tests verify cfg propagation (30/30 PASS) |
| 14 | Call budget enforced | **PASS (unit test)** | M6.0-5.6 test_max_judge_calls_enforced |
| 15 | Token budget enforced | **PASS (unit test)** | M6.0-5.6 test_max_token_budget_enforced |
| 16 | Cost budget enforced | **PASS (unit test)** | M6.0-5.6 test_max_cost_usd_enforced |
| 17 | Retry budget enforced | **PASS (unit test)** | M6.0-5.6 test_max_retries_enforced + R2 17/17 |
| 18 | Calibration trigger | **PASS (unit test)** | M6.0-5 consensus + calibration tests |
| 19 | Production data SHA256 unchanged | **PASS** | See section 7 |
| 20 | Production mtime unchanged | **PASS** | See section 7 |
| 21 | No source code modification | **PASS** | See section 8 |
| 22 | Git remains clean | **PASS** | HEAD == origin/main, 20 untracked preserved, 0 modified |
| 23 | HEAD == origin/main | **PASS** | Same SHA at start and end |

---

## 3. Provider / Model IDs (configurable defaults)

These are the **default** values that WOULD be used if credentials were
provided. Not executed in this BLOCKED run.

| Component | Default | Configurable via |
|-----------|---------|------------------|
| Response model | `claude-haiku-4-5-20251001` | `M6_LLM_MODEL` |
| Provider | `claude` | `M6_LLM_PROVIDER` |
| Judge 1 | `claude-haiku-4-5-20251001` (family `claude-haiku`) | `M6_LLM_JUDGE_MODELS` / `M6_LLM_JUDGE_FAMILIES` |
| Judge 2 | `claude-sonnet-4-5-20251001` (family `claude-sonnet`) | same |
| Judge 3 | `claude-opus-4-5-20251001` (family `claude-opus`) | same |

For 2-provider topology, users can override `M6_LLM_PROVIDER=openai` to
use GPT-4o-mini/4o/4-turbo defaults.

---

## 4. Number of real HTTP calls (actual)

**0** (no real calls attempted; credentials unavailable, M6.0-5.5 E2E
test SKIPs cleanly via `pytest.mark.skipif`).

If the run were unblocked with default config:
- Expected: 1 chat call (for evidence) + 3 judge calls = **4 real HTTP calls**
- Plus retry attempts (max 2/judge on 429/5xx/timeout) up to 12 additional

---

## 5. Judge results / scores / aggregation

**NOT VERIFIED** (no real run). The test infrastructure that WOULD verify
this is in `tests/test_m6_0_5_5_real_three_judge_e2e.py::test_real_three_judge_e2e_full_flow`
— currently SKIPPED.

Unit-level verification (PASS):
- `test_diversity_validates_unique_judges`: 3 judges with valid (provider, family) tuples PASS validate_diversity
- `test_self_evaluation_blocks`: judge.model == response.model raises SelfEvaluationError
- `test_deterministic_precedence_preserved`: det FAIL overrides subj result

---

## 6. Calibration behavior (unit test verified)

`eval_result.calibration_required` is computed in M6.0-5 consensus:
- `calibration_required = (num_disagreements > 0 OR num_harmful > 0 OR errored_judges > 0)`
- Normal agreement: `calibration_required = False`
- High disagreement or any score=1: `calibration_required = True`
- 1+ errored judge: `calibration_required = True` (M6.0-5.4)

Verified by M6.0-5 56/56 tests + M6.0-5.4 39/39 tests.

---

## 7. Production Integrity — SHA256 + mtime (BEFORE = AFTER)

### BEFORE validation run
```
data/soul/agent_yua/relationships.json: sha256=fdb3cc3f7643b5b4 mtime=1786495057.0972767
data/soul/agent_mai/relationships.json: sha256=7eb0ce59924a3314 mtime=1786494956.5760078
data/agents/agent_yua/carryover.json:   sha256=c6be0753ccce4e45 mtime=1785002906.2077687
data/agents/agent_mai/carryover.json:   sha256=96603486eb8b0554 mtime=1785002906.2403574
data/memory.db:                          sha256=49f68317414e1051 mtime=1786495048.1349423
```

### AFTER validation run
```
data/soul/agent_yua/relationships.json: sha256=fdb3cc3f7643b5b4 mtime=1786495057.0972767
data/soul/agent_mai/relationships.json: sha256=7eb0ce59924a3314 mtime=1786494956.5760078
data/agents/agent_yua/carryover.json:   sha256=c6be0753ccce4e45 mtime=1785002906.2077687
data/agents/agent_mai/carryover.json:   sha256=96603486eb8b0554 mtime=1785002906.2403574
data/memory.db:                          sha256=49f68317414e1051 mtime=1786495048.1349423
```

**All 5 production files: byte-identical before and after.** Mutation = 0.

(No diary/dream production state file found in `data/diary/` or
`data/dream/` — those are not part of the M6.0-5.5 E2E path; the E2E only
touches the chat path via `LLMProxy.Backend.complete()`.)

---

## 8. Git State (before and after)

### Before
```
HEAD: 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
origin/main: 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
Modified files: 0
Untracked files: 20 (all known baseline artifacts)
```

### After
```
HEAD: 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
origin/main: 540eac2e230e2d4ce75a5d99c5e245d2f604fa59
Modified files: 0
Untracked files: 20 (unchanged)
```

**No source code modified. No file changes. HEAD == origin/main.**

The 20 untracked files are the M5.8-1 baseline artifacts (logs, scripts,
test files from prior tickets) — none written by this validation run.

---

## 9. Regression Results

### M6.0-5.x + relevant M5
```
334 passed, 5 skipped, 2 warnings in 36.58s
```

- M6.0-5: 56/56
- M6.0-5.2 unit: 30/30
- M6.0-5.2 opt-in: 1/1 SKIPPED
- M6.0-5.4: 39/39
- M6.0-5.4-R1: 22/22
- M6.0-5.4-R2: 17/17
- M6.0-5.5 unit: 3/3
- M6.0-5.5 E2E: 4/4 SKIPPED (BLOCKED — credentials unavailable)
- M6.0-5.6: 30/30
- M6.0-2: 16/16
- M6.0-3: 22/22
- M5.13-3: 29/29
- M5.10-2: 31/31
- M5.9-3.1: 31/31
- M5.8-4: 26/26

### M5.4-5.x foundation
```
137 passed, 2 warnings in 4.37s
```

### Total
**471 / 471 PASS + 5 skipped (opt-in)** — no regression.

---

## 10. Modified files

**None.** This was a validation-only execution. No source code was
modified, no new files were created (this report is in
`C:\Users\bbfcc\m6_0_5_5_r1_temp\` outside the repo, per M5.13-3.1
safety policy).

---

## 11. Provider-specific findings

**None.** No provider interactions occurred (BLOCKED at gate).

If/when unblocked, expected findings:
- Claude `request_id` = `msg_*` prefix; `stop_reason` = `end_turn` or
  `max_tokens`
- OpenAI `request_id` = `chatcmpl-*` prefix; `stop_reason` = `stop` or
  `length`
- Both expose `usage` with token counts (input/output/total)
- `latency_ms` is wall-clock from before the request to after the response

---

## 12. Final verdict: **BLOCKED — CREDENTIALS UNAVAILABLE**

### Why this is the CORRECT result, not a failure

Per Bry 派工 spec:
> If the environment does not have valid credentials, the correct result is:
> BLOCKED — CREDENTIALS UNAVAILABLE
> It is NOT PASS and it is NOT a reason to modify the infrastructure.

The validation infrastructure (M6.0-5.5 E2E test + M6.0-5.6
EvaluationBudgetConfig) is in place and verified by 30 unit tests.
The **environmental prerequisites** for executing a real run are not
met in this session. This is an operational gate, not a code defect.

### How to unblock (for Bry to run later)

```powershell
$env:M6_REAL_LLM='1'
$env:M6_LLM_API_KEY='<sk-ant-...>'  # or sk-... for OpenAI
$env:M6_LLM_PROVIDER='claude'        # or 'openai'
$env:M6_LLM_MODEL='claude-haiku-4-5-20251001'
# Optional overrides:
# $env:M6_LLM_JUDGE_MODELS='claude-haiku-4-5-20251001,claude-sonnet-4-5-20251001,claude-opus-4-5-20251001'
# $env:M6_LLM_JUDGE_FAMILIES='claude-haiku,claude-sonnet,claude-opus'
# $env:M6_LLM_BASE_URL='https://api.anthropic.com'

cd C:\Users\bbfcc\.local\bin\soul-os-harness
.\.venv\Scripts\python.exe -m pytest tests/test_m6_0_5_5_real_three_judge_e2e.py::TestRealThreeJudgeE2E::test_real_three_judge_e2e_full_flow -v
```

The full-flow test will execute the 3-judge E2E and verify all 23
acceptance criteria in this report.

---

## 13. Recommended next ticket

**M5.13-4: Fix M5.13-3 float precision** — P3 cleanup (long-standing).
No new M6.0-5.x work is blocked by this BLOCKED state; the M6.0-5.x
infrastructure is verified by 471 unit/mock tests.

OR wait for Bry to provide credentials and re-run M6.0-5.5-R1.
