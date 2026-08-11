# M6.0-5 — Subjective LLM Quality Evaluation Infrastructure Closeout

**Ticket**: M6.0-5 (Bry 派工 2026-08-11 19:28)
**Mode**: IMPLEMENTATION
**Baseline (before)**: HEAD = `3ed1092` (M6.0-4 audit) | origin/main = `3ed1092` (synced)
**Final HEAD**: `3ed1092` + 1 commit
**Date**: 2026-08-11 19:35 EDT

---

## 1. Tests

### M6.0-5 (this ticket's deliverable)

```
56/56 PASS in 0.23s

16 test categories (per Bry spec):
  1. Evidence serialization (5 tests)
  2. Rubric validation (10 tests)
  3. Judge result validation (3 tests)
  4. Three independent judges (4 tests)
  5. No judge cross-contamination (1 test)
  6. Median aggregation (3 tests)
  7. Agreement calculation (3 tests)
  8. High-disagreement calibration trigger (2 tests)
  9. Low-disagreement automatic result (3 tests)
 10. Calibration queue generation (5 tests)
 11. Deterministic precedence (6 tests)
 12. Production isolation (1 test)
 13. Malformed judge output handling (2 tests)
 14. Missing dimension handling (1 test)
 15. Invalid score handling (4 tests)
 16. Reproducibility (3 tests)
```

All mock/fake judges are deterministic, network-free, no real LLM calls.
No API key, no credentials, no network access required.

---

## 2. Regression

### Required suites (per Bry spec)

| Suite | Tests | Result |
|-------|-------|--------|
| **M6.0-5 (this ticket)** | 56 | 56/56 PASS |
| M6.0-2 (A/B/C validation) | 16 | 16/16 PASS |
| M6.0-3 (D/E/F/G/H validation) | 22 | 22/22 PASS |
| M5.8-4 (producer gating) | 26 | 26/26 PASS |
| M5.9-3 (world → inner life) | 46 | 46/46 PASS |
| M5.9-3.1 (production wiring) | 31 | 31/31 PASS |
| M5.10-2 (LLM judge v1 context) | 13 | 13/13 PASS |
| M5.13-3 (relationship context) | 29 | 29/29 PASS |
| **Required subtotal** | **239** | **239/239 PASS** in 3.98s |

### Broader M-series regression

| Suite | Tests | Result |
|-------|-------|--------|
| M5.2-M5.7 baseline | 565 | 565/565 PASS in 30.39s |

### Total

**804/804 PASS** across all M5/M6 tests (M6.0-5: 56 + M6.0-2: 16 + M6.0-3: 22 + M5.8-4: 26 + M5.9-3: 46 + M5.9-3.1: 31 + M5.10-2: 13 + M5.13-3: 29 + M5.2-M5.7: 565).

### Pre-existing failures

Pre-existing flaky test (M5.8-1 baseline) — NOT touched by M6.0-5:
- `tests/test_extract_and_judge_context_bug.py::test_content_stage_sees_real_text` (async infra)

Not in M6.0-5 scope. M6.0-5 56/56 PASS without touching this test.

---

## 3. Production Integrity

### Verification (SHA256 + mtime before/after M6.0-5 test run)

| File | sha256 (before) | sha256 (after) | Status |
|------|-----------------|-----------------|--------|
| `data/soul/agent_yua/relationships.json` | 84765BCCAECEC489... | 84765BCCAECEC489... | **IDENTICAL** |
| `data/agents/agent_yua/carryover.json` | C6BE0753CCCE4E45 | C6BE0753CCCE4E45 | unchanged |
| `data/agents/agent_ruka/carryover.json` | 62D7E475C72C3BBF | 62D7E475C72C3BBF | unchanged |
| `data/agents/agent_yua/emotional-state.json` | 6ABA2661F22B0D83 | 6ABA2661F22B0D83 | unchanged |

**0 production mutation from M6.0-5 test runs.**

(Note: external Soul OS server processes periodically touch production data; verified by capturing SHA256 before/after M6.0-5 test execution.)

---

## 4. Git State

```
HEAD = 3ed1092 (M6.0-4 audit) + 1 commit (this ticket)
Working tree: 20 pre-existing untracked artifacts preserved (M5.8-1 baseline)
Modified: 0 source files
Modified: 0 test files (in src/ tree; tests/ added new files)
Modified: 0 production files
New: 7 files (1 test + 6 module files)
```

---

## 5. Modified Files

| File | Type | Notes |
|------|------|-------|
| `tests/_helpers/subjective_eval/__init__.py` | new | Module API + EVALUATOR_VERSION |
| `tests/_helpers/subjective_eval/rubric.py` | new | 8 dimensions + 1-5 Likert anchors + validate_score |
| `tests/_helpers/subjective_eval/evidence.py` | new | EvaluationEvidence dataclass + to/from dict |
| `tests/_helpers/subjective_eval/judge.py` | new | Judge ABC + 4 mock judges + SequentialJudgeRunner |
| `tests/_helpers/subjective_eval/consensus.py` | new | aggregate() + calculate_agreement() + EvaluationResult |
| `tests/_helpers/subjective_eval/calibration.py` | new | CalibrationItem + CalibrationQueue (JSONL) |
| `tests/_helpers/subjective_eval/precedence.py` | new | combine_deterministic_subjective() |
| `tests/test_m6_0_5_subjective_eval.py` | new | 56 tests covering 16 Bry spec categories |

### What was NOT changed (per out-of-scope)

- 0 production source files modified
- 0 frozen contracts modified
- 0 production data files modified
- 0 production prompt changes
- 0 LLMProxy modifications
- 0 new context blocks
- 0 new runtime scoring dimensions
- 0 diary/dream/proactive DM subjective eval
- 0 TTS / voice / embedding / vector DB
- 0 UI / human-review dashboard
- 0 database infrastructure (JSONL only)
- 0 production LLM calls
- 0 API keys / credentials

---

## 6. Architecture Implemented

### Module structure

```
tests/_helpers/subjective_eval/
├── __init__.py          (public API + EVALUATOR_VERSION)
├── rubric.py            (8 dimensions, 1-5 Likert, validate_score)
├── evidence.py          (EvaluationEvidence, to/from dict)
├── judge.py             (Judge ABC, 4 mocks, SequentialJudgeRunner)
├── consensus.py         (aggregate, calculate_agreement, EvaluationResult)
├── calibration.py       (CalibrationItem, CalibrationQueue JSONL)
└── precedence.py        (combine_deterministic_subjective)
```

### Judge architecture

- `Judge` ABC with `evaluate(evidence) → JudgeResult`
- `FixedScoreJudge`: returns same score for all 8 dimensions
- `ScriptedJudge`: pre-scripted sequence of (per-dim) scores
- `HighAgreementJudge`: returns 4 or 5 for all dimensions (low disagreement baseline)
- `HighDisagreementJudge`: alternates 1/5 patterns (calibration trigger test)
- `SequentialJudgeRunner`: enforces exactly 3 judges, unique IDs, sequential execution with same evidence

### Consensus algorithm

```python
for each dimension:
    scores = [judge_A, judge_B, judge_C]  # 1-5 each
    median_scores[dim] = int(statistics.median(scores))
    max_diff = max(scores) - min(scores)

calibration_required = (
    any max_diff >= AGREEMENT_THRESHOLD (2)
    or any score == 1 (harmful)
)

overall_subjective_status:
    if below_threshold >= 3 OR any_harmful: FAIL
    elif below_threshold >= 1: PARTIAL
    else: PASS
```

### Deterministic precedence

```python
det PASS + subj PASS = PASS
det PASS + subj PARTIAL/FAIL = PARTIAL
det FAIL + subj ANY = FAIL  # DET_OVERRIDES_SUBJECTIVE = True
```

---

## 7. Judge Aggregation Behavior

### Median computation (per dimension)

| A | B | C | median | max_diff | calibration |
|---|---|---|--------|----------|-------------|
| 4 | 4 | 4 | 4 | 0 | No |
| 4 | 4 | 5 | 4 | 1 | No |
| 3 | 4 | 5 | 4 | 2 | Yes (max_diff=2 ≥ threshold) |
| 1 | 3 | 5 | 3 | 4 | Yes (max_diff=4 + harmful) |
| 1 | 1 | 1 | 1 | 0 | Yes (harmful score=1) |
| 4 | 4 | 2 | 4 | 2 | Yes (max_diff=2) |
| 3 | 3 | 3 | 3 | 0 | No (median=3 = threshold, all ≥3) |

### Overall status aggregation

- 8 median scores all ≥ 3 → PASS
- 1-2 medians < 3 → PARTIAL
- 3+ medians < 3 OR any score=1 → FAIL

---

## 8. Calibration Behavior

### Trigger conditions (`calibration_required = True`)

1. Any dimension with `max_diff >= 2` (judges disagree by 2+ Likert steps)
2. Any score == 1 (harmful content detected)
3. Periodic sampling (out of scope for v1)

### Queue states

```
PENDING → REVIEWED → ACCEPTED (looks ok)
                   → OVERRIDDEN (Bry disagrees with consensus)
```

### Storage

JSONL file at user-provided path. Append-only for `add()`, full-replace for `update()`. No database infrastructure.

### Bry review properties

- **Asynchronous**: review doesn't block evaluation
- **Optional**: not required for every evaluation
- **Non-blocking**: pending items don't gate evaluation completion
- **Manual**: only Bry controls state transitions

---

## 9. Deterministic Precedence Behavior

### Hard rule

`DET_OVERRIDES_SUBJECTIVE = True`

A subjective evaluator MUST NEVER override a deterministic contract failure.

### Test cases (all PASS)

| Deterministic | Subjective | Final |
|---------------|------------|-------|
| PASS | PASS | PASS |
| PASS | PARTIAL | PARTIAL |
| PASS | FAIL | PARTIAL |
| FAIL | PASS | **FAIL** (det overrides) |
| FAIL | PARTIAL | FAIL |
| FAIL | FAIL | FAIL |

This rule is enforced by `combine_deterministic_subjective()` function and tested in 6 dedicated test cases.

---

## 10. Findings

### F1: 8 existing dimensions fully derived from M5.x observable

**Severity**: VALIDATION (positive)

All 8 dimensions (context_coherence, temporal_appropriateness, relationship_continuity, memory_continuity, emotional_continuity, world_context_relevance, character_persona_consistency, lived_context_coherence) map to existing M5.x observable state. No new runtime dimensions required.

### F2: Mock judges are sufficient for current scope

**Severity**: INFORMATIONAL

Per Bry spec: "Prefer deterministic fake judges. Do NOT make the test suite dependent on network access."

4 mock judges (FixedScoreJudge, ScriptedJudge, HighAgreementJudge, HighDisagreementJudge) cover all test scenarios without requiring real LLM. Real LLM judge implementation deferred to a separate ticket (M6.0-5.1 or later).

### F3: Diary/dream/proactive DM subjective eval out of scope

**Severity**: INFORMATIONAL (out-of-scope)

Per Bry 派工 spec, M6.0-5 only covers LLMProxy chat. Diary/dream/proactive DM subjective eval requires:
1. Recording backend for diary/dream LLM (raw httpx, no mock layer — F1 in M6.0-4 audit)
2. Diary/dream-specific evidence schema
3. Diary/dream-specific dimension rubric (or same 8 dimensions applied to diary content)

Future ticket: M6.0-5.1 — Diary/Dream subjective eval infrastructure.

### F4: Production data integrity verified

**Severity**: VALIDATION (positive)

SHA256 + mtime before/after M6.0-5 test run = IDENTICAL. 0 production mutation.

---

## 11. Unresolved Decisions

Per Bry 派工 spec, all major architecture decisions are FROZEN for this ticket:

| Decision | Status |
|----------|--------|
| Evaluator: Hybrid (3 LLM judges + Bry human calibration) | ✓ FROZEN |
| 3 independent LLM judges | ✓ FROZEN |
| 1-5 categorical Likert | ✓ FROZEN |
| Median + agreement aggregation | ✓ FROZEN |
| Det > Subj precedence | ✓ FROZEN |
| Mock judges for tests (no real LLM by default) | ✓ FROZEN |
| Bry is the human evaluator (asynchronous, optional) | ✓ FROZEN |
| LLMProxy chat ONLY (no diary/dream/proactive) | ✓ FROZEN |

**0 unresolved decisions in M6.0-5 scope.**

### Open questions for future M6.x tickets (out of M6.0-5 scope)

1. **M6.0-5.1**: Diary/Dream subjective eval infrastructure (F3 above)
2. **M6.0-5.2**: Real LLM judge backend with API key management
3. **M6.0-5.3**: Cross-character subjective comparison (Yua vs Ruka voice)
4. **M6.0-5.4**: Bry calibration UI/dashboard (currently text-only JSONL)

---

## 12. Stop Conditions Check

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Production runtime modification required | **No** | All subjective eval code in tests/_helpers/ |
| 2 | Frozen contract must change | **No** | 0 source files modified |
| 3 | New runtime context block necessary | **No** | 8 dimensions derived from existing state |
| 4 | Evaluation requires production data mutation | **No** | 0 mutation verified |
| 5 | 3-judge architecture cannot be implemented | **No** | SequentialJudgeRunner works |
| 6 | Scope requires diary/dream/proactive DM | **No** | Strictly LLMProxy chat only |
| 7 | Significant architecture choice not covered | **No** | All decisions in spec |

**0 of 7 stop conditions hit. M6.0-5 proceeds normally.**

---

## 13. Confirmation

### Temporary files outside repo

✓ All temp files in `C:\Users\bbfcc\m6_0_5_temp\` (NOT in repo)
- `_commit_msg.txt` (will be moved to logs/ via commit)
- No other scratch files

### 20 baseline untracked artifacts preserved

✓ Per `git status --short | Select-String "^\?\?"` count = 20
- Same list as M5.8-1 baseline
- No new untracked files

### Production isolation

✓ Read-only access via Path.cwd() / "data/" in test_evaluation_runs_dont_mutate_production
✓ No writes to production memory.db / relationships.json / diary / dream / trace

### Mock judges (no real LLM)

✓ FixedScoreJudge / ScriptedJudge / HighAgreementJudge / HighDisagreementJudge all in-process
✓ No httpx / OpenAI / Anthropic calls
✓ No API keys, no credentials

### Frozen contracts unchanged

✓ 0 source files modified
✓ M5.8-4, M5.9-3, M5.9-3.1, M5.10-2, M5.13-3, M6.0-2, M6.0-3 all PASS
✓ M5.2-M5.7 baseline all PASS

---

## 14. Recommended Next Ticket

**M6.0-5.1 — Diary/Dream subjective eval infrastructure** (out of M6.0-5 scope, F3 above)

Mode: IMPLEMENTATION
Scope:
- Recording backend for `_call_minimax_for_diary` and `_call_minimax_for_dream_event` (currently raw httpx)
- Diary/dream evidence schema (8 dimensions applied to diary content)
- Diary/dream subjective eval test (mock diary LLM backend, like MockLLMBackend but for diary/dream)

Or:

**M6.0-5.2 — Real LLM judge backend** (out of M6.0-5 scope, F2 above)

Mode: IMPLEMENTATION
Scope:
- Real LLM judge backend (Claude or OpenAI) implementing `Judge` ABC
- API key management (env var, NOT committed)
- Cost estimation
- Calibration with Bry on real judge outputs

---

**M6.0-5 status: CLOSED, TEST-ONLY, 0 production mutation, 0 frozen contract change, 56/56 subjective tests PASS, 239/239 required regression PASS, 565/565 broader M-series PASS, all 16 test categories covered.**
