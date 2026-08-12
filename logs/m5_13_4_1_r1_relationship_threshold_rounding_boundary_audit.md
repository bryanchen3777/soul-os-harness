# M5.13-4.1-R1 — Relationship Threshold Rounding Boundary Audit (READ-ONLY)

**Ticket:** M5.13-4.1-R1
**Mode:** READ-ONLY AUDIT / TEST-ONLY
**派工:** Bry 2026-08-11 22:20 EDT
**完成時間:** 2026-08-11 ~22:50 EDT
**HEAD (start/end):** c816142fadd1b05f1ca6d36c126a7be2e0b58fea (M5.13-4.1)
**origin/main:**        c816142fadd1b05f1ca6d36c126a7be2e0b58fea

---

## 1. Boundary Matrix (10 test values, per Bry 派工 spec)

| raw value | round(_, 6) | before fix | **with fix (M5.13-4.1)** | strict M5.13-2 spec | observed actual |
|-----------|-------------|------------|------------------------|---------------------|------------------|
| `0.3000001` | 0.3 | 認識 | 認識 | 認識 | 認識 |
| `0.3` | 0.3 | 認識 | 認識 | 認識 | 認識 |
| `0.29999999965208335` (decay noise) | 0.3 | "" (stranger) | **認識 (FIXED)** | "" (stranger) | 認識 |
| `0.2999999` | 0.3 | "" (stranger) | **認識 (FALSE PROMOTION)** | "" (stranger) | 認識 |
| `0.2999998` | 0.3 | "" (stranger) | **認識 (FALSE PROMOTION)** | "" (stranger) | 認識 |
| `0.2999995` | 0.299999 | "" (stranger) | "" (stranger) | "" (stranger) | "" (stranger) |
| `0.299999` | 0.299999 | "" (stranger) | "" (stranger) | "" (stranger) | "" (stranger) |
| `0.29999` | 0.29999 | "" (stranger) | "" (stranger) | "" (stranger) | "" (stranger) |
| `0.2999` | 0.2999 | "" (stranger) | "" (stranger) | "" (stranger) | "" (stranger) |
| `0.299` | 0.299 | "" (stranger) | "" (stranger) | "" (stranger) | "" (stranger) |

**3 values are falsely promoted by the current fix:**
- `0.2999999` → 認識 (should be "" per strict M5.13-2 contract)
- `0.2999998` → 認識 (should be "" per strict M5.13-2 contract)
- (Note: `0.2999995` rounds DOWN to 0.299999 due to Python's banker's
  rounding; only values strictly in `(0.2999995, 0.3)` round up)

**False-promotion range: `(0.2999995, 0.3)` = ~5×10⁻⁷ wide**

---

## 2. False-Promotion Range Analysis

### 2.1 Where the boundary actually sits
Python's `round()` uses banker's rounding (round-half-to-even). For
6-decimal-place rounding:
- `round(0.2999995, 6) = 0.299999` (rounds DOWN because 9 is odd)
- `round(0.29999951, 6) = 0.3` (rounds UP)
- `round(0.2999996, 6) = 0.3` (rounds UP)

So the actual false-promotion range is `(0.2999995, 0.3)` = 5×10⁻⁷.

(In IEEE 754, `0.2999995` is actually `0.29999949999...` so the
7th digit is 4, not 5. The threshold is just above 0.2999995.)

### 2.2 What "5×10⁻⁷" means in natural decay
The decay rate is `CONFIDENCE_DECAY_PER_DAY = 0.02`. Per second:
```
0.02 / 86400 = 2.315×10⁻⁷ per second
```

So 5×10⁻⁷ of confidence is approximately **2.16 seconds** of natural
decay at the 0.3 boundary.

**The current fix gives roughly 2 seconds of "grace period" for
newly-created relationships at the 0.3 boundary.** After 2 seconds,
the natural decay pushes the value below 0.2999995 and the fix no
longer promotes it.

---

## 3. Contract Evidence

### 3.1 M5.13-2 spec (intent document)
```
- confidence < 0.3  → 陌生人, 沒 behavioral signal, 不輸出
- 0.3 <= c < 0.5    → 認識
- 0.5 <= c < 0.7    → 熟悉
- 0.7 <= c < 0.9    → 親密
- c >= 0.9          → 深度信任
```

This is **STRICT**: `0.3 <= c` means `c is at least 0.3`, with no tolerance
for sub-decimal noise. The literal reading: `0.2999999` is NOT `0.3`,
so it should NOT be 認識.

### 3.2 M5.13-3 implementation (frozen in proxy.py:_format_relationship_block)
```python
if confidence >= _RELATIONSHIP_BAND_DEEP_TRUST:    # 0.9
    band = "深度信任"
elif confidence >= _RELATIONSHIP_BAND_CLOSE:      # 0.7
    band = "親密"
elif confidence >= _RELATIONSHIP_BAND_FAMILIAR:   # 0.5
    band = "熟悉"
elif confidence >= _RELATIONSHIP_BAND_MIN_THRESHOLD:  # 0.3  (M5.13-4.1: round(_, 6) >= 0.3)
    band = "認識"
```

The M5.13-3 implementation uses `>=` (strict greater-or-equal). The
M5.13-4.1 fix changes the 0.3 case to `round(confidence, 6) >= 0.3`.

**The fix RELAXES the strict `>=` comparison to allow values within
5×10⁻⁷ of 0.3 to count as 認識.** This is a contract deviation
for values in the false-promotion range.

### 3.3 Existing tests don't cover the false-promotion range
- `test_below_threshold_returns_empty`: uses `[0.0, 0.1, 0.29]` (all
  clearly below 0.3, none in false-promotion range)
- `test_jian_shi_band`: uses `[0.3, 0.4, 0.499]` (all clearly at or above 0.3)
- `test_m5_13_4_1_boundary_decay.test_below_0_3_still_stranger`: uses
  0.29 (clearly below)

**No existing test exercises the false-promotion range.** The M5.13-4.1
fix introduces behavior in this range that is not covered by tests.

---

## 4. Real Producer Analysis

### 4.1 Decay arithmetic (Natural)
The decay formula in `RelationshipsStore._decay_locked`:
```python
decay = days * CONFIDENCE_DECAY_PER_DAY
entry["confidence"] = max(0.0, entry["confidence"] - decay)
```

For a relationship created with 0.3:
- 0.5 seconds after creation: confidence = 0.29999988377... **IN false-promotion range** ✓
- 3 seconds after creation: confidence = 0.29999930454... BELOW false-promotion range
- ~2.16 seconds: the exact boundary

**The decay arithmetic NATURALLY produces values in the false-promotion
range for the first ~2 seconds after creation.** This is the SAME
arithmetic that the fix was designed to handle. The fix's grace
period (~2s) is essentially "decay is paused for 2 seconds" from a
user perspective.

### 4.2 Touch arithmetic (`RelationshipsStore.touch`)
```python
entry["confidence"] = max(0.0, min(1.0, entry["confidence"] + confidence_delta))
```

`confidence_delta` is one of: `±0.02, ±0.05, ±0.10, ±0.15, ±0.20`.
Adding/subtracting these from existing values produces results
that are NOT in the false-promotion range:
- `0.31 - 0.01 = 0.3` (exact, not in [0.2999995, 0.3))
- `0.32 - 0.02 = 0.3` (exact)

Touch arithmetic does NOT naturally land in the false-promotion range.

### 4.3 ensure_relationship with deliberate caller value
```python
entry = others[other_id] = _new_relationship_entry(
    confidence=initial_confidence,  # caller-specified
    ...
)
```

A caller could pass `initial_confidence=0.2999999` to deliberately
create a value in the false-promotion range. This is **unusual**:
- Why would a caller pass 0.2999999 instead of 0.3 or 0.29?
- If they want 0.3, they pass 0.3 (which the fix correctly preserves)
- If they want < 0.3, they pass 0.29 (which the fix correctly rejects)
- 0.2999999 is a "weird" value suggesting FP noise

**No legitimate producer generates values in the false-promotion
range except natural decay in the first ~2 seconds.**

---

## 5. False-Promotion Range Summary

| Aspect | Value |
|--------|-------|
| Range | `(0.2999995, 0.3)` |
| Width | ~5×10⁻⁷ (5e-7) |
| Natural source | Decay arithmetic in first ~2 seconds |
| Deliberate source | `ensure_relationship(0.2999999)` (unusual) |
| Touch arithmetic | NOT a source |
| JSON roundtrip | NOT a source (Python preserves exact) |
| Real impact on production | None (no value in production is in this range) |

---

## 6. Tests (read-only, no production code modified)

### 6.1 Boundary matrix script
`C:\Users\bbfcc\m5_13_4_1_r1_temp\_boundary_matrix.py` — verifies the
exact rounding transition for all 10 spec values + 6 boundary tests.

### 6.2 Existing regression (verified to still pass)
- M5.13-3: **29/29 PASS** (no change in behavior for the M5.13-3 test cases)
- M5.13-4.1: **12/12 PASS** (the fix's tests still pass)

---

## 7. Production Integrity

### SHA256 + mtime BEFORE and AFTER (identical):
```
data/soul/agent_yua/relationships.json: sha256=fdb3cc3f7643b5b4 mtime=1786495057.0972767
data/soul/agent_mai/relationships.json: sha256=7eb0ce59924a3314 mtime=1786494956.5760078
data/agents/agent_yua/carryover.json:   sha256=c6be0753ccce4e45 mtime=1785002906.2077687
data/agents/agent_mai/carryover.json:   sha256=96603486eb8b0554 mtime=1785002906.2403574
data/memory.db:                          sha256=49f68317414e1051 mtime=1786495048.1349423
```

**0 mutation. Audit is read-only.**

---

## 8. Git State

- HEAD (start): c816142fadd1b05f1ca6d36c126a7be2e0b58fea (M5.13-4.1)
- HEAD (end):   c816142fadd1b05f1ca6d36c126a7be2e0b58fea (unchanged)
- Modified files: 0 (audit is read-only)
- Untracked files: 20 baseline artifacts preserved

---

## 9. Modified Files

**None.** This audit creates only:
- `C:\Users\bbfcc\m5_13_4_1_r1_temp\_boundary_matrix.py` (NOT committed, per safety policy)
- `C:\Users\bbfcc\m5_13_4_1_r1_temp\m5_13_4_1_r1_audit.md` (this file, will be committed)
- `logs/m5_13_4_1_r1_relationship_threshold_rounding_boundary_audit.md` (will be committed)

**No production code modified. No production data modified.**

---

## 10. Recommendation: **C. BRY DECISION REQUIRED**

### 10.1 Why NOT A (accept current fix)
- The M5.13-2 spec is **STRICT** (`0.3 <= c` means exact)
- The M5.13-3 implementation is **STRICT** (`>=` is exact)
- The M5.13-4.1 fix **relaxes** the strict comparison to allow
  values in `(0.2999995, 0.3)` to count as 認識
- This is a **real contract deviation**, not just noise filtering
- A deliberate caller could pass `initial_confidence=0.2999999` and
  get 認識 instead of stranger
- The spec didn't explicitly authorize this promotion
- Existing tests don't cover the false-promotion range

### 10.2 Why NOT B (replace current fix)
- The M5.13-4.1 spec said:
  > "Do NOT automatically replace round(..., 6) with:
  > - math.isclose()
  > - epsilon constant
  > - Decimal
  > - global float normalization
  > unless the audit proves that is the correct contract."
- This audit does NOT prove the contract is "strict"; it shows the
  contract is ambiguous (strict spec vs. decay-noise intent)
- All alternatives (math.isclose, Decimal, epsilon) have similar
  false-promotion ranges (just smaller) or don't fix the bug
- A narrower fix (e.g., `round(confidence, 8) >= 0.3`) would have
  false-promotion range 5×10⁻⁹ but wouldn't fix the sub-second decay bug
- A "no fix" alternative re-introduces the original P3 bug
- So B is not clearly better than the current fix

### 10.3 Why C (Bry decision required)
The M5.13-2 spec and M5.13-3 implementation both use **strict** threshold
semantics. The M5.13-4.1 fix deviates from this strictness by
introducing a 5×10⁻⁷ tolerance. The deviation is:
- **Small in magnitude** (5e-7, ~2 seconds of natural decay)
- **Real in principle** (a deliberate caller could exploit it)
- **Not authorized by spec** (spec said "fix the boundary" not
  "introduce tolerance")
- **Not covered by existing tests** (no test exercises the false-promotion range)

Bry needs to decide:
- **A**: "The 5e-7 tolerance is contractually acceptable" (boundary intent > strict spec)
- **B**: "Tighten the fix to not have false-promotion" (would require
  a different approach, possibly relaxing the "do not alter _decay_locked"
  constraint)
- **C**: "Document the false-promotion range and leave as-is"
  (status quo, current fix stays)

The current implementation is the most minimal correct fix for the
sub-day decay bug. Whether its 5e-7 tolerance is acceptable is a
design decision that depends on Bry's intent interpretation.

---

## 11. Whether M5.13-4.1 can finally CLOSE

**Conditional close**: M5.13-4.1 fixed the immediate P3 bug (sub-day
decay at 0.3 boundary). Whether the fix is fully acceptable depends
on Bry's interpretation of the false-promotion range.

**If Bry says A (accept)**: M5.13-4.1 can be considered closed.
Recommendation: add a test that documents the false-promotion range
(e.g., `test_0_2999999_promoted_to_jianshi`) so the behavior is
explicitly tested.

**If Bry says C (decide later)**: M5.13-4.1 stays as the current fix
with a known 5e-7 false-promotion range. The bug is fixed; the
side effect is documented and auditable.

**If Bry says B (replace)**: M5.13-4.1 is replaced with a narrower
fix. This requires a new ticket since the current fix is already
committed.

---

## 12. Stop Conditions (none hit)

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | Production code modified | **No** | Audit is READ-ONLY |
| 2 | Current fix changed | **No** | Audit is READ-ONLY |
| 3 | Production data modified | **No** | 0 mutation verified |
| 4 | Frozen contract expanded | **No** | Contract surface unchanged |
| 5 | New P1/P0 discovered | **No** | P3 boundary concern only |

---

## 13. Next Steps (awaiting Bry decision)

If Bry picks:
- **A** (accept): M5.13-4.1 closed. Add a regression test for the
  false-promotion range to make the behavior explicit.
- **B** (replace): Open M5.13-4.2 ticket for a narrower fix.
- **C** (defer): Leave M5.13-4.1 as-is, document the 5e-7 range in
  the test file, defer the decision.

Awaiting Bry direction.
