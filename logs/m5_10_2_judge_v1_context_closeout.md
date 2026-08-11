# M5.10-2 — Memory LLM Judge v1 Memory Context Visibility — Closeout

**Ticket:** M5.10-2 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY FIRST → MINIMAL IMPLEMENTATION
**Baseline:** `HEAD = c5a628c` (post M5.10-1)
**Result:** IMPLEMENTED ✓ (additive, 0 frozen contract change)
**Date:** 2026-08-11 00:10 EDT
**Auditor:** Lin (Mavis/M3) for Bry

---

## 0. Audit Charter

Bry 派工原文:
> "ONLY investigate v1 Memory visibility first."
> "Determine whether canonical v1 memory context should be visible to the Memory LLM Judge, and if yes, implement the smallest additive integration without changing Judge semantics or frozen contracts."
> "If implementation would alter frozen Judge semantics or create a new architecture decision: STOP and report CONTRACT CONFLICT."

---

## 1. Runtime Trace

### 1.1 Production Judge call chain (before)

```
MemoryMiddleware._on_agent_speak
  → SAGELiteProvider.post_reply_commit(...)
    → self._writer.write_turn(...)
      → self._writer.extract_and_write(...)
        → self._writer._extract_facts(...)
          → self._writer._extract_facts_llm(...)
            → judge.extract_and_judge(text, context="", agent_id=...)  ← HARDCODED EMPTY
```

### 1.2 Production Judge call chain (after)

```
MemoryMiddleware._on_agent_speak
  → SAGELiteProvider.post_reply_commit(...)
    → self._writer.write_turn(...)
      → self._writer.extract_and_write(...)
        → self._writer._extract_facts(...)
          → self._writer._extract_facts_llm(...)
            → self._memory_reader.retrieve_context(text, top_k=3, mode="precise", ...)
              → ContextResult.summary  (v1 memory string)
            → judge.extract_and_judge(text, context=summary, agent_id=...)
```

### 1.3 Key finding: `context` parameter already exists

`LLMJudge.extract_and_judge(text, context: str, agent_id: str)` — the `context` parameter **already exists** in the method signature. The problem was only the call site in `MemoryWriter._extract_facts_llm` hardcoded `context=""`.

---

## 2. Canonical v1 MemoryReader

| Property | Value |
|---|---|
| **Canonical source** | `SAGELiteProvider._reader` |
| **Shares GraphStore with** | `SAGELiteProvider._writer` (via `MemoryWriter.store`) |
| **Read semantics** | keyword search + graph traversal → `ContextResult.summary` (string) |
| **Filtering** | `source_pair_filter=None` (context stage: no access control, purely semantic) |
| **LLM dependency** | 0 (graph-only, no LLM call) |
| **Side effects** | 0 (read-only) |
| **Frozen since** | Phase 2.0 (M3.1/M3.2 era) |

---

## 3. Frozen Contract Impact

| Contract | Change | Frozen? |
|---|---|---|
| `LLMJudge.extract_and_judge` signature | **0** (context param already existed) | ✓ unchanged |
| `ContextResult` schema | **0** (reader output format unchanged) | ✓ unchanged |
| `MemoryWriter` public API | **0** (additive private field `_memory_reader`) | ✓ unchanged |
| `SAGELiteProvider` wiring | **additive** (reorder init, pass reader) | ✓ unchanged |
| `Fact` schema | **0** | ✓ unchanged |
| Production data | **0** (reader is read-only) | ✓ unchanged |

**Stop conditions hit: NONE**

---

## 4. Implementation Changes

### 4.1 `src/memory/sage/writer.py`

**File:** `src/memory/sage/writer.py`

**Changes:**
- `from __future__ import annotations` moved to top (Python import order)
- `TYPE_CHECKING` guard for `MemoryReader` forward reference (no runtime import)
- `MemoryWriter.__init__`: added optional `memory_reader: Optional[MemoryReader] = None` parameter
  - Stored as `self._memory_reader = memory_reader`
  - Default `None` = backward compatibility (no reader → context="" degenerate)
- `MemoryWriter._extract_facts_llm`: before calling `judge.extract_and_judge(...)`:
  ```python
  memory_context = ""
  if self._memory_reader is not None:
      try:
          reader_result = self._memory_reader.retrieve_context(
              text,
              top_k=3,          # conservative: just relevant facts
              max_tokens=400,    # keep context bounded
              mode="precise",   # high precision, no chains
          )
          memory_context = reader_result.summary
      except Exception:
          pass  # reader failure does not block extraction
  # then: judge.extract_and_judge(text, context=memory_context, agent_id=...)
  ```

**Rationale for parameters:**
- `top_k=3`: only the 3 most relevant facts (avoid context bloat)
- `mode="precise"`: no chain expansion (lower noise)
- `max_tokens=400`: hard cap on context length
- `source_pair_filter=None`: context stage is purely semantic, no access control
- `query=text`: use current message as query (finds relevant prior facts)

### 4.2 `src/memory/sage/provider.py`

**File:** `src/memory/sage/provider.py`

**Changes:**
- `_init_components` reordered: `MemoryReader` now created **before** `MemoryWriter`
- `MemoryWriter` construction: added `memory_reader=self._reader`
- No new infrastructure (same GraphStore, same canonical bus)

**Rationale:** writer needs reader instance, so reader must be built first.

---

## 5. Scope Boundary (Bry spec §4 / M5.10-1 finding)

| Source | Included? | Rationale |
|---|---|---|
| v1 memory (graph) | **✓ YES** | Bry conservative path selection |
| Diary | **✗ NO** | Out of scope per M5.10-2 |
| Dream | **✗ NO** | Out of scope per M5.10-2 |
| NarrativeTrace | **✗ NO** | Out of scope per M5.10-2 |
| semantic/vector search | **✗ NO** | Out of scope |
| embeddings | **✗ NO** | Out of scope |
| new LLM judge | **✗ NO** | Not needed |

---

## 6. Privacy / Content Analysis

| Concern | Assessment |
|---|---|
| Judge sees Diary text | **N/A** (Diary not included in this scope) |
| Judge sees Dream text | **N/A** (Dream not included) |
| Judge sees v1 memory facts | **LOW RISK** — v1 facts are already "published" to the memory system; `MemoryReader.retrieve_context` is the canonical read path; Judge seeing graph facts is consistent with existing visibility |
| Judge writes to memory | **0** — Judge is read-only; `MemoryReader` is read-only |
| Access control bypass | **0** — `source_pair_filter=None` means no extra filtering; existing source_pair labeling on facts remains (only used in the separate `MemoryMiddleware` context injection path) |

---

## 7. Recursive / Feedback Risk

| Risk | Assessment |
|---|---|
| Judge creates InnerLifeEvent | **0** — Judge is read-only evaluator, no bus.publish |
| Judge triggers Agency | **0** — No trigger pathway |
| Context feeds back into same evaluation cycle | **0** — `retrieve_context` reads from graph, extraction writes to graph; next call sees newly written facts (natural eventual consistency) |
| Reader mutates state | **0** — `MemoryReader.retrieve_context` is read-only graph traversal |

---

## 8. Production Data Integrity

| Check | Status |
|---|---|
| `memory.db` written during tests | **0** (focused tests use mocks/temp dirs) |
| v1 production memory mutated | **0** (reader is read-only) |
| graph.sqlite changed | **0** (focused tests use mocks) |
| Diary/dream/trace modified | **0** (not touched) |
| Replay / backfill | **0** |

---

## 9. Acceptance Criteria

| Criterion | Status |
|---|---|
| A. Canonical v1 MemoryReader identified | ✓ `SAGELiteProvider._reader` |
| B. Complete Judge runtime path traced | ✓ call chain documented |
| C. Existing v1 memory schema preserved | ✓ `Fact` / `ContextResult` unchanged |
| D. No Diary context added | ✓ (scope boundary) |
| E. No Dream context added | ✓ (scope boundary) |
| F. No NarrativeTrace context added | ✓ (scope boundary) |
| G. No LLM/semantic/vector infrastructure added | ✓ |
| H. Existing Judge decision logic preserved | ✓ (context only additive string) |
| I. Frozen contracts unchanged | ✓ |
| J. Context addition is additive and deterministic | ✓ |
| K. No production data mutation | ✓ |
| L. No new persistence | ✓ |
| M. No recursive feedback path | ✓ |
| N. Existing Judge tests pass | ✓ `test_extract_and_judge_context_bug.py` PASS |
| O. Regression passes | ✓ M3.2 / M3 / M3.1 baseline (pre-existing issues unchanged) |

---

## 10. Tests

### 10.1 Focused tests: `tests/test_m5_10_2_judge_v1_context.py`

**13 tests / 13 PASS in 0.246s**

| Test | Criterion |
|---|---|
| `test_writer_constructs_without_memory_reader` | A: backward compat (no reader) |
| `test_writer_constructs_with_memory_reader` | A: accepts reader param |
| `test_signature_additive_no_required_param` | A: no required param change |
| `test_retrieve_context_called_with_correct_params` | D+E+H: correct params (precise/3/400/None) |
| `test_memory_context_passed_to_judge` | E: summary → judge context |
| `test_empty_summary_works` | E: empty summary → empty context |
| `test_no_reader_does_not_call_retrieve` | C: no reader → no call, context="" |
| `test_retrieve_context_exception_does_not_propagate` | F: exception safe |
| `test_provider_wires_reader_to_writer` | B: provider wires reader to writer |
| `test_writer_references_provider_reader` | B: writer._memory_reader == provider._reader |
| `test_provider_wires_reader_to_writer` | I: reader before writer order |
| `test_llmjudge_signature_unchanged` | J: context param already existed |
| `test_memorywriter_public_api_unchanged` | J: public API intact |

### 10.2 Regression

| Suite | Result |
|---|---|
| `test_extract_and_judge_context_bug.py` | PASS (regression guard for context bug) |
| `verify_m3_2_live.py` M3.2 unit | 13/14 (pre-existing, unchanged) |
| `verify_m3_2_live.py` M3 e2e | 3/3 ✓ |
| `verify_m3_2_live.py` full | 183/189 (pre-existing, unchanged) |
| `test_memory_middleware.py` | FAIL pre-existing (baseline before M5.10-2) |

---

## 11. Git State

```
Baseline:   c5a628c (M5.10-1)
Final:      21258fe (feat commit)
Diff:       src/memory/sage/writer.py (+35/-6)
            src/memory/sage/provider.py (+12/-2)
            tests/test_m5_10_2_judge_v1_context.py (+352)
Frozen contracts: 0 change
Regression: M3.2/M3/M3.1 baseline maintained
Push:       PASS (c5a628c..21258fe main -> main)
```

---

## 12. Architectural Findings

### F1 (Informational): `context` parameter was always present

`LLMJudge.extract_and_judge(text, context: str, agent_id: str)` always accepted a `context` parameter. The "gap" was purely the call site hardcoding `context=""`. No design decision was violated; the boundary was accidentally enforced by the empty string.

**Implication:** The `context` field in the Judge prompt was always intended to carry external context. The v1 memory addition simply activates the intended channel.

### F2 (Informational): Provider wiring completeness

The `SAGELiteProvider` already owned both `_writer` and `_reader`. The only missing piece was wiring them together. No new infrastructure was needed.

---

## 13. Unresolved Decisions

None. M5.10-2 scope was: v1 memory only. That scope was fully addressed.

---

## 14. Recommended Next Ticket

**M5.10-3 — Diary Visibility (per M5.10-1 P2.1 Option 2 scope expansion)**

From M5.10-1 findings:
- Diary: `DiaryWriter.read_entries()` exists but not called by Judge
- Option 2 (full Mavis recommendation) proposed: additive context composition using existing 3 readers (MemoryReader + Diary + Trace)

**If Bry selects M5.10-3:**
- Add `DiaryWriter` to `MemoryWriter.__init__` (optional, default None)
- In `_extract_facts_llm`: call both `self._memory_reader` AND `self._diary_reader`
- Format: `"[Memory]: {memory_summary}\n[Diary]: {diary_summary}"`
- Privacy consideration: Diary `summary` field (not raw `content`)
- Stop condition: requires changes to `DiaryWriter` read API

**Alternative: Accept P2.1 as v1-only boundary**
- M5.10-1 P2.1 is now partially resolved (v1 memory visible)
- Diary/Dream/Trace remain intentionally invisible per conservative path
- M5.8-1 remaining P2.x: P2.2 (Agency consults Inner Life), P2.4 (Relationships read), P2.5 (Heartbeat carryover), P2.6 (ProactiveDM consults Memory)

---

## 15. Bry Decision Required

**Decision: Next ticket direction**

| Option | Description |
|---|---|
| **A** (Mavis 推薦) | M5.10-3 Diary Visibility — expand to Diary (Option 2 from M5.10-1) |
| **B** | Accept current P2.1 resolution (v1 only) and move to M5.8-1 P2.2/P2.4/P2.5/P2.6 |
| **C** | Full Option 2 (MemoryReader + Diary + NarrativeTrace) in one ticket |
| **D** | 收工 — M5.10-2 closes P2.1 |

---

## 16. Summary

M5.10-2 is **CLOSED**.

The Memory LLM Judge now has v1 memory context visibility through the existing `MemoryReader` → `ContextResult.summary` channel. The change is minimal (1 new optional parameter in `MemoryWriter.__init__`, 1 wire in `SAGELiteProvider`), fully additive, with 0 frozen contract changes. The Judge's `context` parameter (which always existed) now carries real v1 memory content instead of an empty string.

Scope was strictly limited to v1 memory. Diary, Dream, NarrativeTrace remain out of scope per Bry's conservative path selection.
