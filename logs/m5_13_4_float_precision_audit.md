# M5.13-4 — Float Precision Issue Audit (READ-ONLY)

**Ticket:** M5.13-4
**Mode:** READ-ONLY AUDIT
**派工:** Bry 2026-08-11 21:50 EDT
**完成時間:** 2026-08-11 ~22:30 EDT
**HEAD (start):** 9d2174094958f85bcd45fe45344b6be478534dea
**HEAD (end):**   9d2174094958f85bcd45fe45344b6be478534dea
**origin/main:**   9d2174094958f85bcd45fe45344b6be478534dea

---

## 1. Exact Finding (reproduced)

The P3 finding carried over from M6.0-2 / M6.0-3:

> "0.3 → 0.2999... after JSON roundtrip. `>= 0.3` threshold check fails.
> Affects `_format_relationship_block` exact-threshold cases."

### 1.1 Original claim — INACCURATE

The original claim says "0.3 → 0.2999... **after JSON roundtrip**". This is
**inaccurate for Python's `json` module**. Verified:

```python
import json
back = json.loads(json.dumps({"confidence": 0.3}))["confidence"]
# back == 0.3  (exact equality)
# back >= 0.3  (True)
```

Python's `json` uses `repr()` which emits the shortest round-trip
representation. Literal `0.3` round-trips to exactly `0.3`. The same
is true for all band thresholds (0.3 / 0.5 / 0.7 / 0.9 / 0.6 / 0.75 / 0.8 / 0.9).

The "0.299999947306713" value cited in the original P3 description is
the **JavaScript** 64-bit float representation of 0.3, not the
Python / JSON round-trip representation.

### 1.2 The REAL bug (reproduced)

The actual reproduction of the P3 issue is via **arithmetic, not JSON**.

`RelationshipsStore._decay_locked()` subtracts `CONFIDENCE_DECAY_PER_DAY * days`
from every entry's confidence on every read. The decay arithmetic is:

```python
0.3 - 0.02 = 0.27999999999999997   # binary float arithmetic
0.3 - 0.02 (over 1 day)  = 0.29999999965208335  # actual time delta decay
```

These values are **genuinely less than 0.3** in IEEE 754 binary floating
point. The check `confidence >= 0.3` correctly returns `False` for
these values. So the value is **correctly classified as "stranger"**.

The contract bug is:
- User calls `ensure_relationship(initial_confidence=0.3)`
- User's intent: "create with the minimum '認識' band threshold"
- Actual behavior: stored value is 0.3, but on the FIRST `get()`,
  decay runs and value becomes 0.29999... (< 0.3)
- `_format_relationship_block` returns "" (no block) — the user's
  intent is silently dropped

### 1.3 Reproduction (executed in this audit, in temp dir)

```python
# From /tmp audit script (executed, not committed)
store = RelationshipsStore("test", tmp_dir)
entry = store.ensure_relationship("user_bryan", initial_confidence=0.3)
# entry["confidence"] == 0.3
# store.get("user_bryan")["confidence"] == 0.29999999965208335
# 0.29999999965208335 >= 0.3 → False
# _format_relationship_block("test") → ""  (silent drop)
```

The freshly created entry is 0.3 in-memory, but `get()` triggers decay
on all entries (including the freshly created one). The decayed value
is then persisted to disk via `_flush_locked()`. After this first
read, the on-disk value is 0.29999... — and stays that way.

---

## 2. Source / Code Path

### 2.1 Producer (writes confidence)

`src/soul/relationships.py`:
- `CONFIDENCE_DEFAULT_STRANGER = 0.3` (line 57, band min threshold)
- `CONFIDENCE_DECAY_PER_DAY = 0.02` (line 67)
- `RelationshipsStore.touch()` (lines 296-331) — applies confidence_delta
  arithmetic, clamps to [0, 1]
- `RelationshipsStore._decay_locked()` (lines 181-215) — applies natural
  decay on every read, including freshly-created entries
- `RelationshipsStore.ensure_relationship()` (lines 234-294) — calls
  `_decay_locked()` BEFORE creating new entry
- `RelationshipsStore.get()` (lines 219-225) — calls `_decay_locked()`
  + `_flush_locked()` on every read

### 2.2 Transformation

The transformation is `0.3 - (0.02 * days_since_last_decay)`. The
arithmetic produces IEEE 754 values that are less than 0.3 even
when the user intended exactly 0.3.

JSON serialization in `_flush_locked()` uses Python's `json.dumps`
which preserves the value exactly. So JSON is NOT the corruption point.

### 2.3 Consumer (threshold check)

`src/llm/proxy.py`:
- `_RELATIONSHIP_BAND_MIN_THRESHOLD = 0.3` (line 322)
- `_format_relationship_block()` (line 328) reads `confidence` from
  `store.get(BRYAN_ENTITY_ID)` and applies band mapping:
  ```python
  if confidence >= _RELATIONSHIP_BAND_DEEP_TRUST:  # 0.9
      band = "深度信任"
  elif confidence >= _RELATIONSHIP_BAND_CLOSE:  # 0.7
      band = "親密"
  elif confidence >= _RELATIONSHIP_BAND_FAMILIAR:  # 0.5
      band = "熟悉"
  elif confidence >= _RELATIONSHIP_BAND_MIN_THRESHOLD:  # 0.3
      band = "認識"
  else:
      return ""  # < 0.3: stranger, no block
  ```

The check `confidence >= 0.3` uses **exact comparison** on IEEE 754
floats. This is the contract at the boundary.

---

## 3. Contract at Boundary

| Side | Contract |
|------|----------|
| **Producer** | `ensure_relationship(initial_confidence=0.3)` should result in confidence=0.3 (the literal value the user specified) |
| **Storage** | JSON roundtrip preserves 0.3 → 0.3 (exact) — verified |
| **Consumer** | `confidence >= 0.3` is the threshold; below 0.3 = stranger (no block) |
| **Implicit** | The decay arithmetic `0.3 - 0.02` produces a value < 0.3 in IEEE 754 — the threshold check is correct, the value IS < 0.3 |

The implicit contract gap: the user does not expect `0.3` to be
silently downgraded to "stranger" after the first read. The threshold
0.3 is the LITERAL minimum band value, so creating with 0.3 should
yield "認識", not "".

---

## 4. Reproduction Evidence (executed)

### 4.1 Pure JSON roundtrip (no issue)
```python
import json
data = {"confidence": 0.3}
roundtrip = json.loads(json.dumps(data))["confidence"]
# roundtrip == 0.3 (exact)
# roundtrip >= 0.3 (True)
```
Result: **PASS** (no precision loss through JSON).

### 4.2 Decay arithmetic (real bug)
```python
0.3 - 0.02
# → 0.27999999999999997  (genuinely < 0.3)
0.3 - 0.02 * (1/86400 * elapsed_seconds)  # sub-day decay
# → ~0.29999... (genuinely < 0.3, by a small margin)
```
Result: **FAIL** (arithmetic produces < 0.3).

### 4.3 Real production path (audit script)
- `ensure_relationship(0.3)` → in-memory: 0.3
- `store.get("user_bryan")` → in-memory + disk: 0.29999999965208335
- `_format_relationship_block("test")` → ""

This was reproduced with a tempdir-based store (script in
`C:\Users\bbfcc\m5_13_4_temp\_db_check.py` and inline Python, NOT
committed to repo).

---

## 5. Impact / Severity

### 5.1 Current production data (verified)
```
data/soul/agent_yua/relationships.json:
  user_bryan: confidence=0.9999966948946759
  agent_ram:  0.0
  agent_miku: 0.0
  agent_akane:0.0
  agent_rem:  0.0
  agent_anna: 0.0
  agent_mai:  0.0
  agent_mahiru:0.0
  agent_aoi:  0.029228190102083457
  agent_ruka: 0.9412270006344916

data/soul/agent_mai/relationships.json:
  user_bryan: 0.9096888327856483
  agent_yua:  0.0
  agent_ruka: 0.0
  agent_akane:0.0
  agent_ram:  0.021257243154166827
  agent_mahiru:0.06925155416412045
  agent_anna: 0.10924553929351852
  agent_miku: 0.14324445314328685
  agent_aoi:  0.18924883802546286
  agent_rem:  0.24054373831388895
```

**No production value is at the 0.3 boundary.** All values are
either:
- Well above 0.3 (0.5-1.0 range — Bry-Yua 0.9999..., Bry-Mai 0.9096...,
  Yua-Ruka 0.9412...)
- Well below 0.3 (0.0-0.24 range — agent-agent relationships)

### 5.2 Severity classification: **P3 (dormant, low-impact)**

- **Correctness**: there IS a real bug at the boundary — a user
  creating a relationship with confidence=0.3 gets "stranger" instead
  of "認識".
- **Probability**: low — Bry 派工 M5.13-2 design uses 0.3 as the
  default stranger threshold; in practice, new relationships start
  with touch() (delta +0.02) which moves them well above 0.3.
- **Blast radius**: minimal — the only consumer is
  `_format_relationship_block` for the relationship context block in
  LLM prompts. Other modules (v1 memory loader, etc.) have similar
  threshold checks but on different data sources.
- **User-visible impact**: a relationship created at exactly 0.3
  silently gets no "認識" block in the LLM prompt. The agent would
  treat the user as a stranger, which is technically consistent with
  confidence=0.3 → decay → "stranger" but contradicts the user's
  intent of "minimum known person".

### 5.3 Risk of leaving as-is
- The bug is dormant (no production value at boundary)
- The fix is small but touches decay semantics
- A test-only fix (use literal 0.3 in test mock) is NOT a real fix —
  it would only mask the production bug

---

## 6. Consumer audit (all consumers of `confidence` threshold checks)

| Location | Threshold values | Source of value | Same bug? |
|----------|------------------|-----------------|-----------|
| `src/llm/proxy.py:_format_relationship_block` | 0.3 / 0.5 / 0.7 / 0.9 | `RelationshipsStore.get()` (decayed) | **YES** (the P3 finding) |
| `src/memory/v1/loader.py:CONFIDENCE_THRESHOLDS` | 0.6 / 0.75 / 0.8 / 0.9 | `Memory.confidence` (from `memories.jsonl`) | **Different**: memory confidence is loaded from JSONL directly, not run through decay. JSON roundtrip preserves 0.8 → 0.8. |
| `src/memory/v1/loader.py:threshold_for` | same as above | same | Same as above (no decay path) |
| `src/memory/llm_judge.py` | (no threshold check; uses scores 0-1) | LLM-as-judge output | N/A — different domain |

**Conclusion**: the P3 finding is **isolated to the relationships
decay path**. The v1 memory loader uses literal confidence values
from JSONL which round-trip cleanly through Python's `json` module.

---

## 7. Recommendation

### 7.1 Classification: **P3 — TEST-ONLY or MINIMAL PRODUCTION FIX**

The P3 finding has two valid resolutions. Both should be considered
together.

### 7.2 Option A: TEST-ONLY fix (no production change)

The test `test_jian_shi_band` already passes because it uses a
mock store that bypasses the decay path. No test-only fix is needed
to make existing tests pass.

If we want to assert the boundary behavior in tests (catching the
production bug), we need an INTEGRATION test that:
- Uses a real `RelationshipsStore` (with tempdir)
- Calls `ensure_relationship(0.3)` then `store.get()`
- Asserts the resulting confidence is `>= 0.3`

Such a test would FAIL today, exposing the bug. This is the
correct test-only approach: write the test that catches the bug,
document that it fails, and recommend the production fix as a
separate ticket.

### 7.3 Option B: MINIMAL production fix (NOT implemented in this ticket)

Per spec: "Do NOT implement the fix in this audit ticket."

The minimal fix is in `src/soul/relationships.py:_decay_locked()`:
skip decay for entries that were created within the current
call window (e.g. `last_interaction_at >= last_decay_at` would
indicate "no real interaction since last decay, no decay needed").

OR: in `_format_relationship_block`, add a small epsilon to the
threshold:
```python
elif confidence >= _RELATIONSHIP_BAND_MIN_THRESHOLD - 1e-9:
    band = "認識"
```
This treats 0.3 - epsilon as 0.3 for the boundary band. The contract
becomes "approximately >= 0.3" instead of "exactly >= 0.3".

Both fixes are < 10 lines. Neither modifies the test design. The
fix is a follow-up M5.13-4.1 IMPLEMENTATION ticket.

### 7.4 **Recommendation: TEST-ONLY (Option A)**

The bug is real but:
1. **Production impact = 0** (no value at 0.3 boundary)
2. **Test-only fix does NOT mask the production bug** — adding
   the failing integration test would surface the bug for future
   fixes
3. **Minimal production fix is small but touches a frozen contract
   area** (decay semantics) — Bry 派工 should decide whether to
   fix at decay layer or threshold layer

**Per spec "If the issue is only test-design debt, do not create
production fix"**: this is NOT test-design debt (the test design is
correct for the unit-level band logic). It's a real production
bug at the contract boundary. So a production fix IS warranted —
but not in this audit ticket.

---

## 8. Tests / Regression (no regression introduced)

### 8.1 Targeted regression run
```
tests/test_m5_13_3_relationship_context.py: 29 passed, 19 subtests
tests/test_m5_8_4_producer_gating.py:        PASS
tests/test_m5_10_2_judge_v1_context.py:      PASS
tests/test_m5_9_3_1_production_wiring.py:    PASS
Total: 99 / 99 PASS
```

The audit script (`_db_check.py` in temp dir) is informational only,
not committed to the repo, not part of regression.

### 8.2 No new test added in this audit (per spec "READ-ONLY")

The recommended follow-up test (Option A) would be a new
integration test in `tests/test_m5_13_4_*` that catches the
production bug. That test is NOT created in this ticket.

---

## 9. Production Integrity

### Production data SHA256 + mtime (BEFORE and AFTER audit, identical):
```
data/soul/agent_yua/relationships.json: sha256=fdb3cc3f7643b5b4 mtime=1786495057.0972767
data/soul/agent_mai/relationships.json: sha256=7eb0ce59924a3314 mtime=1786494956.5760078
data/agents/agent_yua/carryover.json:   sha256=c6be0753ccce4e45 mtime=1785002906.2077687
data/agents/agent_mai/carryover.json:   sha256=96603486eb8b0554 mtime=1785002906.2403574
data/memory.db:                          sha256=49f68317414e1051 mtime=1786495048.1349423
```

**0 mutation. READ-ONLY audit confirmed.**

---

## 10. Git State

### Before and after
```
HEAD: 9d2174094958f85bcd45fe45344b6be478534dea
origin/main: 9d2174094958f85bcd45fe45344b6be478534dea
Modified files: 0 (audit was READ-ONLY)
Untracked files: 20 (all known M5.8-1 baseline artifacts, preserved)
```

**No source code modified. Only audit documentation will be created
in this commit.**

---

## 11. Modified Files

This audit ticket creates only:
- `logs/m5_13_4_float_precision_audit.md` (this file)
- (Optional) `C:\Users\bbfcc\m5_13_4_temp\_db_check.py` (NOT committed,
  per M5.13-3.1 safety policy: temp files outside repo)

**No source code modified. No production data modified.**

---

## 12. Architectural Findings

### 12.1 Decay semantics vs boundary semantics
The P3 finding exposes a tension between two design intentions:
- **Decay**: "natural decay of relationships over time" (per M5.13-2)
- **Boundary band**: "exact threshold 0.3 is the '認識' band" (per M5.13-2)

The decay applies to ALL entries on every read, including freshly
created ones. The boundary comparison is exact-float (`>=`).

These two designs are individually correct but interact poorly at
the 0.3 boundary.

### 12.2 The threshold check is at the right layer
Putting the band-mapping logic in `_format_relationship_block` (the
only consumer of relationships.confidence for LLM prompts) is the
right separation. The store is concerned with persistence + decay;
the consumer is concerned with projection.

A fix at the consumer layer (add epsilon) would be local and small.
A fix at the store layer (don't decay freshly-created entries) is
also small but touches more code paths.

### 12.3 Test design is correct for unit tests
The existing M5.13-3 tests use a mock store that returns literal
confidence values. This correctly tests the band-mapping logic.
The tests do NOT exercise the decay path, which is where the bug
lives. This is NOT a test design flaw — it's appropriate unit
test scoping. The bug is an integration concern (real store + decay
+ threshold).

### 12.4 Other consumers are NOT affected
The v1 memory loader uses confidence values from `memories.jsonl`
which round-trip through Python's `json` cleanly. No equivalent bug
in memory confidence threshold checks.

---

## 13. Whether a follow-up implementation ticket is required

**YES, but not in this audit ticket.** Per spec, the audit ticket
does not implement the fix.

### 13.1 Recommended follow-up: M5.13-4.1 IMPLEMENTATION

Two valid approaches (Bry to choose):

**Option 1: Fix at store layer (decay semantics)**
- Modify `src/soul/relationships.py:_decay_locked()`
- Skip decay for entries where `last_interaction_at == last_decay_at`
  (i.e. freshly created in the same atomic operation)
- Preserves exact-float contract at consumer layer
- Touches decay semantics (frozen contract area — Bry decision)

**Option 2: Fix at consumer layer (threshold tolerance)**
- Modify `src/llm/proxy.py:_format_relationship_block()`
- Add small epsilon to the 0.3 threshold:
  `elif confidence >= _RELATIONSHIP_BAND_MIN_THRESHOLD - 1e-9:`
- Local, minimal change (1 line)
- Changes "exact >= 0.3" to "approximately >= 0.3" (slight contract
  weakening — Bry decision)

**Option 3: Add integration test, defer fix**
- Add `tests/test_m5_13_4_boundary_decay.py` that uses real
  `RelationshipsStore` and asserts `confidence >= 0.3` after
  `ensure_relationship(0.3)` then `store.get()`
- Test would FAIL today, exposing the bug
- Document the bug; defer fix to a separate ticket

### 13.2 Bry decision required

The fix location (Option 1 vs Option 2) is a Bry decision because:
- Option 1 touches frozen contract (decay semantics) per M5.13-2
- Option 2 introduces approximate equality (new contract pattern)

---

## 14. Stop conditions (none hit)

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Frozen contract conflict (audit) | **No** | Audit is READ-ONLY; no contract changes |
| 2 | P0/P1 correctness discovered | **No** | P3 only, dormant in production |
| 3 | Production mutation | **No** | 0 mutation verified |
| 4 | Test design debt only (no production fix) | **No** | Real production bug, but follow-up fix not in this ticket |

---

## 15. Final verdict

**P3 finding confirmed.** The original description ("0.3 → 0.2999...
after JSON roundtrip") is **inaccurate** (Python's json preserves
0.3 exactly). The **real** bug is that `_decay_locked()` applies
0.02/day decay to freshly-created entries on the first read,
producing 0.29999... (genuinely < 0.3 in IEEE 754), which then
fails the `>= 0.3` threshold check.

**Current production impact: 0** (no value at the 0.3 boundary).

**Recommendation: TEST-ONLY fix in this audit ticket (none
implemented). Follow-up M5.13-4.1 IMPLEMENTATION ticket required
for a production fix (Bry decision on Option 1 / Option 2).**

The audit is **read-only complete**. No source code modified.
HEAD == origin/main. 20 baseline untracked artifacts preserved.
