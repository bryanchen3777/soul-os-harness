# M5.13-3 — Minimal Relationship Context Integration Closeout

**Mode:** IMPLEMENTATION / MINIMAL ADDITIVE
**Baseline:** `7bf10f0` (M5.13-2)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT

---

## 1. Tests

### Focused tests (M5.13-3)
- `tests/test_m5_13_3_relationship_context.py`
- **29 tests + 19 subtests = 48 assertions**
- **Status: 29 passed (100%)**

### Coverage by section

| Test class | Tests | Coverage |
|-----------|-------|----------|
| `TestHelperFunctionExists` | 2 | A: Helper exists, signature |
| `TestAgentAndBryScoped` | 3 | B/C: per-agent, per-target, agent isolation |
| `TestBandThresholds` | 6 (5 subtests × boundary cases) | E: 0.3+ / 0.5+ / 0.7+ / 0.9+ boundaries |
| `TestFailSilentAndNoLeak` | 9 | F/G/H/I: no raw float, no feeling/impression, fail-silent |
| `TestInjectionIntoBuildMessages` | 5 | J/K/M: group/private wired, both produce same block |
| `TestFrozenContractsUnchanged` | 4 | L: schema unchanged, 0 mutation |

---

## 2. Regression

| Suite | Count | Status |
|-------|-------|--------|
| M5.13-3 focused (new) | 29 | ✅ PASS |
| M5.8-4 producer gating | 19 | ✅ PASS |
| M5.9-3 world → inner life | 27 | ✅ PASS |
| M5.9-3.1 production wiring | 46 | ✅ PASS |
| M5.10-2 judge v1 context | 13 | ✅ PASS |
| M5.2 minimal agency | 22 | ✅ PASS |
| M5.2-G proactive DM bridge | 11 | ✅ PASS |
| M5.4-6.2 proactive DM inner life | 36 | ✅ PASS |
| M5.7.2 heartbeat reactivation | 20 | ✅ PASS |
| M5.7.4 heartbeat robustness | 9 | ✅ PASS |
| **Subtotal** | **232** | **✅ PASS** |

### Pre-existing failures (NOT introduced by M5.13-3)

| Suite | Count | Reason |
|-------|-------|--------|
| `tests/test_extract_and_judge_context_bug.py::test_content_stage_sees_real_text` | 1 | async test without pytest-asyncio (pre-existing, M5.8-1 baseline) |

This pre-existing failure was confirmed on baseline (`7bf10f0` without M5.13-3 changes) — same error, same test. Not introduced by M5.13-3.

---

## 3. Production Integrity

- **Memory data:** 0 mutation (relationship read is read-only)
- **Diary data:** 0 mutation (not touched)
- **Dream data:** 0 mutation (not touched)
- **Event data:** 0 mutation (not touched)
- **Trace data:** 0 mutation (not touched)
- **relationships.json:** 0 mutation (read-only access; verified by `test_helper_does_not_modify_relationship_data`)
- **memory.db:** 0 mutation (not touched)
- **diary jsonl:** 0 mutation (not touched)
- **dream jsonl:** 0 mutation (not touched)

---

## 4. Git State

| Field | Value |
|-------|-------|
| Commit 1 (source + tests) | `32e5172 feat(m5.13-3): minimal relationship context integration` |
| Working tree (post-commit) | clean (modified files: 0; untracked: 21 known artifacts + closeout log) |
| Pre-existing untracked | 21 (20 prior + 1 new `tests/test_m5_13_3_relationship_context.py`... wait, that's the test file we just committed) |

**Untracked artifacts after commit 1:**
- 20 pre-existing untracked artifacts preserved

---

## 5. Modified Files

| File | Change | Lines |
|------|--------|-------|
| `src/llm/proxy.py` | + `_format_relationship_block` helper + 2 injection points | +126 |
| `tests/test_m5_13_3_relationship_context.py` | New file, 29 tests | +545 |

**Total:** 1 source file + 1 test file = 2 files modified/added.

---

## 6. Commit / Push

| Action | Status |
|--------|--------|
| Commit 1 (source + tests) | ✅ `32e5172` |
| Push 1 | ✅ origin/main |
| Commit 2 (closeout log) | Pending (this file) |
| Push 2 | Pending |

---

## 7. Exact Relationship Projection Behavior

### Helper function

**File:** `src/llm/proxy.py:_format_relationship_block`

**Signature:** `def _format_relationship_block(agent_id: str) -> str`

**Behavior matrix:**

| `agent_id` | Relationship exists? | `confidence` | Output |
|-----------|---------------------|--------------|--------|
| non-empty str | yes | `>= 0.9` | `[你跟 Bry 的關係]\n  熟悉度: 深度信任` |
| non-empty str | yes | `[0.7, 0.9)` | `[你跟 Bry 的關係]\n  熟悉度: 親密` |
| non-empty str | yes | `[0.5, 0.7)` | `[你跟 Bry 的關係]\n  熟悉度: 熟悉` |
| non-empty str | yes | `[0.3, 0.5)` | `[你跟 Bry 的關係]\n  熟悉度: 認識` |
| non-empty str | yes | `< 0.3` | `""` (skip) |
| non-empty str | yes | `> 1.0` | clamped to 1.0 → 深度信任 |
| non-empty str | yes | `< 0.0` | clamped to 0.0 → skip |
| non-empty str | yes | non-numeric | `""` (skip) |
| non-empty str | yes | missing key | `""` (skip) |
| non-empty str | no | n/a | `""` (skip) |
| empty / None / non-str | n/a | n/a | `""` (skip) |
| any | exception in store | n/a | `""` (fail-silent, log debug) |

### Injection behavior

In `_build_messages_group` (L487-492) and `_build_messages_private` (L762-767):

```python
# After mood_desc, before inner_life
relationship_block = _format_relationship_block(agent_id)
if relationship_block:
    system_parts.append(f"\n{relationship_block}")
```

When the helper returns `""` (skip cases), NO block is appended → existing context block order preserved.

---

## 8. Architectural Findings

### What M5.13-3 verified

1. **Helper is fully deterministic** — same confidence always produces same output (no LLM, no randomness, no time-based variation).
2. **Per-agent isolation works** — agent_yua's relationship is independent of agent_ruka's.
3. **Bry target isolation works** — only BRYAN_ENTITY_ID is queried; other relationships ignored.
4. **Fail-silent is robust** — store exceptions, malformed data, missing data all return "".
5. **No raw float leakage** — 0.85 never appears in output; only "親密" appears.
6. **No subjective field leakage** — feeling, impression, interaction_count, timestamps never appear.
7. **Schema unchanged** — RelationshipsStore API + entry schema are byte-identical.
8. **Read-only verified** — calling helper does not call .touch / .update_impression / .ensure_relationship.
9. **Both paths produce same block** — group and private have identical relationship block content.
10. **Below threshold gracefully skips** — 0.1 confidence produces no block, no error.

### What M5.13-3 does NOT change

- Agency Stage 1-4: 0 change
- TriggerEnvelope: 0 change
- AgencyState: 0 change
- RelationshipsStore schema: 0 change
- RelationshipsStore public API: 0 change
- Event Bus contracts: 0 change
- MemoryReader / MemoryWriter: 0 change
- InnerLifeEvent: 0 change
- WorldEvent: 0 change
- LLM Judge: 0 change
- Heartbeat: 0 change

---

## 9. Unresolved Issues

### From M5.13-1 / M5.13-2 / M5.12-1 (pending Bry decisions)

| # | Decision | Status |
|---|----------|--------|
| 1 | P2.2 scope accept (M5.12-1) | Pending Bry |
| 2 | P2.6 future direction (M5.12-1) | Pending Bry |
| 3 | M5.13-3 implementation direction (M5.13-2) | **RESOLVED: A implemented** |

### From M5.13-3 (no new unresolved issues)

M5.13-3 implementation is complete. No new architectural questions or contract conflicts identified.

### Pre-existing test infrastructure (not in M5.13-3 scope)

| # | Issue | Status |
|---|-------|--------|
| 1 | `test_extract_and_judge_context_bug.py` async without pytest-asyncio | Pre-existing, out of M5.13-3 scope |

---

## 10. Acceptance Criteria Verification

| Criterion | Status | Evidence |
|-----------|--------|----------|
| Relationship block deterministic | ✅ | `TestBandThresholds::test_deterministic_output_same_input` |
| Agent-scoped | ✅ | `TestAgentAndBryScoped::test_helper_uses_BRYAN_ENTITY_ID`, `test_agent_isolation_different_agents` |
| Bry-scoped | ✅ | `TestAgentAndBryScoped::test_helper_uses_BRYAN_ENTITY_ID` (verifies BRYAN_ENTITY_ID is the only target) |
| Confidence-only | ✅ | `TestFailSilentAndNoLeak::test_no_feeling_or_impression_in_output` |
| Threshold behavior verified | ✅ | `TestBandThresholds` (5 subtests × boundary cases) |
| Malformed/missing data fail-safe | ✅ | `TestFailSilentAndNoLeak` (9 tests) |
| Group/private canonical paths covered | ✅ | `TestInjectionIntoBuildMessages` (5 tests) |
| Existing context ordering preserved | ✅ | Injection is additive (`system_parts.append`) before inner_life; no reordering |
| Frozen contracts unchanged | ✅ | `TestFrozenContractsUnchanged` (4 tests) |
| No Agency Stage 1-4 changes | ✅ | src/llm/proxy.py only; agency/ untouched |
| No Relationships schema changes | ✅ | `TestFrozenContractsUnchanged::test_relationships_schema_untouched` |
| No LLM/semantic/vector infrastructure | ✅ | Helper is pure deterministic; no new dependencies |
| Production data unchanged | ✅ | `TestFrozenContractsUnchanged::test_helper_does_not_modify_relationship_data` |
| Focused tests PASS | ✅ | 29/29 + 19 subtests |
| Relevant regression PASS | ✅ | 232/232 (all M-series focused) |

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M5.13-3 |
| Mode | IMPLEMENTATION / MINIMAL ADDITIVE |
| Baseline | `7bf10f0` |
| Frozen contracts | 0 change |
| Source files modified | 1 (`src/llm/proxy.py`) |
| New test files | 1 (`tests/test_m5_13_3_relationship_context.py`) |
| Tests added | 29 (with 19 subtests = 48 assertions) |
| Regression | 232/232 PASS |
| Pre-existing failures | 1 (out of scope, M5.8-1 baseline) |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
