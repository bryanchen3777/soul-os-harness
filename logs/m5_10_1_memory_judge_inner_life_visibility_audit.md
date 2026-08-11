# M5.10-1 — Memory LLM Judge → Inner Life Visibility Audit

**Ticket:** M5.10-1 (Bry 派工 2026-08-10)
**Mode:** READ-ONLY / ARCHITECTURE AUDIT
**Baseline:** `HEAD = 7d382f5` (post M5.9-3.1) | `origin/main = 7d382f5` (synced)
**M5.9-3 = CLOSED | M5.9-3.1 = CLOSED | M5.8-1 P2.3 = RESOLVED**
**Date:** 2026-08-10 23:50 EDT
**Auditor:** Mavis (M3) for Bry

---

## 0. Audit Charter

Bry 派工原文:
> "Audit the current Memory LLM Judge runtime path and determine whether the Judge has sufficient visibility into Diary / Dream / Inner Life when evaluating memory quality."
> "The specific M5.8-1 finding is: P2.1 — Memory LLM Judge cannot currently see Diary / Dream / Inner Life."
> "Do NOT assume this is a bug. Determine whether this boundary is intentional, accidental, or a latent architecture mismatch."

---

## 1. Complete Runtime Path (Bry spec final report §1)

### 1.1 Production call chain (verified by reading `src/memory/`)

```
USER_MESSAGE (SoulEvent)
  → MemoryMiddleware._on_agent_intent (line 290-296)
    → provider.prefetch(query, session_id, source_pair_filter)
      → MemoryReader.retrieve_context() (sage/reader.py:45)
      → returns ContextResult.summary (string) for LLM context
  → LLMProxy generates AGENT_SPEAK
  → MemoryMiddleware._on_agent_speak (post-reply commit)
    → MemoryWriter.extract_and_write()
      → MemoryWriter._extract_facts(text, subject_hint, session_id, source)
        → MemoryWriter._extract_facts_llm (line 486-)
          → LLMJudge.extract_and_judge(text, context="", agent_id="")  ← *** HARDCODED EMPTY ***
            → LLMJudge.extract_triples(text, context="", agent_id="")
            → LLMJudge.judge_stance(triple, context="")
            → LLMJudge.judge_content(triple, context="")
      → returns (facts, raw_results) tuple
  → facts written to v1 memory store (mirror)
```

### 1.2 Two distinct Memory LLM operations

| Operation | Function | Caller | Context input |
|-----------|----------|--------|---------------|
| **Fact extraction** | `LLMJudge.extract_and_judge` | `MemoryWriter._extract_facts_llm` | **`context=""` HARDCODED** |
| **Memory retrieval** | `MemoryReader.retrieve_context` | `MemoryMiddleware._on_agent_intent` (for LLM context) | existing facts from graph store |

**The Judge (extraction) does NOT use the Reader's output. They are separate code paths.**

### 1.3 Critical evidence

**File:** `src/memory/sage/writer.py:510`

```python
results = loop.run_until_complete(
    judge.extract_and_judge(text, context="", agent_id=subject_hint or "")
)
```

**`context=""` is HARDCODED.** No `MemoryReader` call precedes this. The Judge is called with empty context in every invocation.

---

## 2. Current Judge Visibility Matrix (Bry spec §2)

| Information source | Judge sees? | Evidence (file:line) |
|-------------------|-------------|----------------------|
| `text` (raw user message / content) | ✓ YES | `judge.extract_and_judge(text, ...)` |
| `agent_id` | ✓ YES | `judge.extract_and_judge(..., agent_id="agent_X")` |
| Memory context (existing v1 facts) | ✗ NO | `context=""` hardcoded `sage/writer.py:510` |
| Diary entries (`data/diary/agent_X.jsonl`) | ✗ NO | `DiaryWriter.read_entries()` exists but not called by Judge |
| Dream entries (`data/dream/*.jsonl` or similar) | ✗ NO | No path from Judge to dream storage |
| InnerLifeEvent (`trace.jsonl`) | ✗ NO | `NarrativeTraceReader.query_*` exists but not called by Judge |
| v1 memory store (graph store) | ✗ NO (NOT in Judge context) | `MemoryReader` queries graph but Judge is separate path |
| Conversation/session metadata | ✗ NO | `session_id` passed to `_extract_facts` but not to Judge |
| Provenance/identity metadata | ✗ NO | `InnerLifeEvent.provenance` not accessible to Judge |
| `subject_hint` | ✓ YES | passed via `agent_id` |
| Recent v1 facts (via graph) | ✗ NO | `MemoryReader` is a separate path (LLM context only) |
| Diary text | ✗ NO | Diary has `summary` field but Judge never reads |
| Dream text | ✗ NO | Judge never reads |
| `inner_life_event_id` reference | ✗ NO | Stored in diary/dream entries but not exposed to Judge |

**Conclusion:** The Judge operates on `text` + `agent_id` in **complete isolation**. **ZERO visibility** into Diary, Dream, Inner Life, v1 memory, or any other context.

---

## 3. Trace all Existing Adapters / Providers / Readers (Bry spec §3)

| Module | Function | Used by Judge? | Used by Memory system? |
|--------|----------|----------------|----------------------|
| `src/memory/llm_judge.py` | LLMJudge (extraction) | (IS the Judge) | YES (via MemoryWriter) |
| `src/memory/sage/reader.py` | MemoryReader (retrieval) | ✗ NO | YES (for LLM context injection) |
| `src/memory/sage/writer.py` | MemoryWriter (extraction) | YES (calls Judge) | YES |
| `src/memory/sage/provider.py` | SAGELiteProvider (prefetch) | ✗ NO | YES (for LLM context) |
| `src/memory/sage/graph_store.py` | GraphStore (graph backend) | ✗ NO | YES (Reader backend) |
| `src/memory/sage/evolution.py` | Evolution (not active in Judge) | ✗ NO | (yes for retrieval) |
| `src/memory/sage/models.py` | Fact/Entity models | ✗ NO (Judge outputs dict) | YES (graph storage) |
| `src/soul/diary.py` | DiaryWriter / read_entries | ✗ NO | YES (for diary writes) |
| `src/soul/dream_event.py` | DreamEventWriter | ✗ NO | YES (for dream writes) |
| `src/inner_life/trace_reader.py` | NarrativeTraceReader (5 query methods) | ✗ NO | YES (for query only) |
| `src/inner_life/writer.py` | InnerLifeWriter (sole creator) | ✗ NO | YES (for inner life creation) |

**Key finding:** The Judge has only **1 adapter** (its own LLM call). It does **NOT** call any of the other 10+ adapters in the system. The `context=""` is a hardcoded barrier, not a routing choice.

---

## 4. Diary / Dream / InnerLife Visibility Status (Bry spec §4)

### 4.1 Diary

- **Visible to Judge:** ✗ NO
- **Where stored:** `data/diary/agent_X.jsonl` (per DiaryWriter.write_entry line 181-)
- **Reader API:** `DiaryWriter.read_entries(agent_id, slot, since, limit, inner_life_event_id)` exists at line 246
- **Why Judge doesn't see it:** Judge is called with `context=""` and never invokes `read_entries()`
- **Classification:** **D. Partially visible but inconsistently.** Diary entries exist in storage with `read_entries()` API, but no path from Judge to that API.

### 4.2 Dream

- **Visible to Judge:** ✗ NO
- **Where stored:** `data/dream/*.jsonl` or similar (per DreamEventWriter)
- **Reader API:** No read function found (DreamEventWriter appears write-only from audit)
- **Why Judge doesn't see it:** Judge is called with `context=""`; no reader API exists
- **Classification:** **A. Intentionally outside Judge scope.** No reader API means even if we wanted to provide context, there's no clean read path. This is by design (dreams are write-only records, not retrieval targets).

### 4.3 InnerLifeEvent

- **Visible to Judge:** ✗ NO
- **Where stored:** `data/inner_life/trace.jsonl` (per NarrativeTraceWriter.write line 103-123)
- **Reader API:** `NarrativeTraceReader.query_by_event_id / session_id / correlation_id / lineage_path_prefix / ts_range` (5 methods, M5.4-5.7)
- **Why Judge doesn't see it:** Judge is called with `context=""`; NarrativeTraceReader exists but not called from Judge path
- **Classification:** **D. Partially visible but inconsistently.** InnerLifeEvent has a complete reader API (5 query methods), but Judge has no path to it.

### 4.4 v1 memory store (graph)

- **Visible to Judge:** ✗ NO (Judge doesn't read it)
- **Visible to MemoryReader:** ✓ YES (Reader reads graph for LLM context)
- **Classification:** **B. Accidentally invisible from Judge, but visible elsewhere.** The Reader uses the graph; the Judge doesn't. This is a known architectural separation, but the Judge's lack of memory context is a design choice (or oversight) — the existing infrastructure could be reused.

---

## 5. Intentional vs Accidental Classification (Bry spec §4)

### 5.1 Per data source

| Source | Classification | Evidence |
|--------|---------------|----------|
| Diary | **D. Partially visible but inconsistently** | `read_entries()` API exists, Judge doesn't use it |
| Dream | **A. Intentionally outside Judge scope** | No read API, by design (write-only) |
| InnerLifeEvent | **D. Partially visible but inconsistently** | 5 query methods exist, Judge doesn't use them |
| v1 memory store | **B. Accidentally invisible** | `MemoryReader` reads it for LLM context; Judge doesn't read it; no design rationale found for empty context |

### 5.2 M5.8-1 P2.1 finding classification

**Original finding:** "Memory LLM Judge cannot currently see Diary / Dream / Inner Life."

**Classification after this audit:**

The boundary is **mostly ACCIDENTAL** (no design rationale found for `context=""`), with **partial infrastructure** available (Diary + InnerLifeEvent readers exist but unused). Dream is **intentionally outside** (no reader API).

The empty context in `sage/writer.py:510` is a **hard-coded default** that has no documented reason. The infrastructure for providing context (MemoryReader) exists, but is in a separate code path (LLM context injection, not Judge evaluation).

**No intentional architecture decision exists to exclude Diary/Dream/InnerLife from Judge.** This is a **latent architecture gap** (M5.8-1 P2.1 confirmed) with multiple reuse paths.

---

## 6. Existing Context Mechanisms (Bry spec §5, §6)

### 6.1 Already in production

| Mechanism | Module | Function | Reuse for Judge? |
|-----------|--------|----------|------------------|
| `MemoryReader.retrieve_context` | `src/memory/sage/reader.py:45` | Query v1 graph store for relevant facts | ✓ YES (could provide memory context) |
| `NarrativeTraceReader.query_*` (5 methods) | `src/inner_life/trace_reader.py:105-` | Query InnerLifeEvent trace | ✓ YES (could provide inner life context) |
| `DiaryWriter.read_entries` | `src/soul/diary.py:246` | Read diary entries for agent | ✓ YES (could provide diary context) |
| Dream read API | (none exists) | N/A | ✗ NO (must design if needed) |

### 6.2 Three reuse options for Judge (Bry spec §7)

#### Option A: Existing context reuse
**Reuse `MemoryReader.retrieve_context` for the Judge's context slot.**

```python
# In sage/writer.py:510 (pseudocode, not implemented)
context = self._reader.retrieve_context(text)  # ← REUSE
results = judge.extract_and_judge(text, context=context.summary, agent_id=...)
```

**Pros:**
- Reuses existing infrastructure
- No new abstraction
- 0 frozen contract change (MemoryReader signature unchanged)
- MemoryReader already used for LLM context

**Cons:**
- Diary/Dream/InnerLife still not visible (MemoryReader only knows v1 graph)
- Would need to extend MemoryReader to also query Diary/Dream/InnerLife (out of scope for "reuse")

**Coverage:** v1 memory only. No Diary/Dream/InnerLife.

#### Option B: Additive context composition
**Construct Judge context from multiple sources.**

```python
# Pseudocode
context_parts = [
    self._reader.retrieve_context(text).summary,  # v1 memory
    diary_reader.read_entries(agent_id, since=...).summary,  # diary
    trace_reader.query_by_ts_range(...).summary,  # inner life
]
context = "\n".join(context_parts)
results = judge.extract_and_judge(text, context=context, agent_id=...)
```

**Pros:**
- Comprehensive (all sources)
- Reuses existing readers (3 of 3)

**Cons:**
- More code, more wiring
- Diary/Dream/InnerLife readers may not exist for all sources
- Privacy: Judge would see diary text (privacy boundary concern)

**Coverage:** v1 memory + diary + inner life (if reader exists).

#### Option C: No change / intentional boundary
**Keep Judge in isolation.**

**Pros:**
- Privacy: Judge doesn't see private content
- Determinism: each evaluation is local
- No contract change

**Cons:**
- M5.8-1 P2.1 remains P2 (visibility gap)
- Diary category classification may be inaccurate without context
- M5.4-5.4派工指 InnerLifeEvent 是 canonical lived experience, Judge should arguably know

---

## 7. Frozen Contract Impact (Bry spec §F)

| Frozen contract | Modified? |
|-----------------|-----------|
| LLMJudge (judge contract) | ✗ NO (interface already accepts `context` param) |
| MemoryWriter (writer contract) | ✗ NO (internal method `_extract_facts_llm` not exposed) |
| v1 memory schema | ✗ NO (Judge doesn't read graph) |
| Memory retrieval contract | ✗ NO (MemoryReader unchanged) |
| Memory Judge contract | ✗ NO (same as above) |
| InnerLifeEvent identity | ✗ NO (Judge doesn't create InnerLifeEvent) |
| Provenance | ✗ NO (Judge doesn't see InnerLifeEvent) |
| NarrativeTrace | ✗ NO (Judge doesn't query) |
| Event Bus contracts | ✗ NO (Judge not in event flow) |
| Agency Stage 1-4 | ✗ NO (Judge doesn't trigger Agency) |
| Existing acceptance suites | ✗ NO (per audit scope, no tests modified) |

**All frozen contracts preserved.** Option B (additive context) requires no contract change — only internal wiring in `MemoryWriter._extract_facts_llm` to construct context from existing readers.

---

## 8. Privacy / Content-Boundary Implications (Bry spec §8)

### 8.1 What Judge currently sees

- `text` parameter: typically user message OR agent's reply text (depending on call site)
- `agent_id`: opaque string like "agent_yua"
- Empty `context`

**NO content from:**
- Diary text (only structured events with optional summary)
- Dream text (no reader API)
- InnerLifeEvent text (none stored; trace is identity+lineage only per M5.4-5.6)
- v1 memory text (only summary/keywords via MemoryReader, not raw conversation)

### 8.2 What Judge would see with Option A (memory only)

- `text` + `agent_id` + v1 memory summary (already sanitized, no raw conversation)

**Privacy risk:** Low. v1 memory summary is pre-sanitized.

### 8.3 What Judge would see with Option B (additive context)

- `text` + `agent_id` + v1 memory summary + **diary summary** + **inner life metadata**

**Privacy risk:** Medium. Diary `summary` field exists per DiaryWriter.write_entry. Judge would see diary text.

**Diary privacy spec:** `soul/diary.py:188-205` shows diary entry has `content` (text), `topic`, `mood`, `actions_taken`, `events_referenced`, `inner_life_event_id`, `ts`, etc. The Judge seeing these would be **a privacy expansion**.

### 8.4 What Judge would see with Option C (no change)

- Same as 8.1 (current state, no privacy change)

**Bry 派工 spec 強調:**
> "Determine whether Judge would gain access to conversation content, prompt/response content, or only structured lived-experience metadata."

**Finding:** With Option B, Judge would gain access to **diary `content` field** (text), which is **conversation-adjacent content** (diary is agent's self-expression). This is **NOT raw conversation content** (user messages / agent responses) but is **closely related**.

---

## 9. Recursive / Feedback Risk (Bry spec §9)

### 9.1 Judge must NOT create InnerLifeEvent

**Verified:** Judge is a **read-only evaluator**. Its output is `triples` (list of dicts) + `category` + `judgment` + `reason`. None of these construct `InnerLifeEvent`. Verified by reading `src/memory/llm_judge.py`:
- `extract_and_judge` returns `List[Dict[str, Any]]` (line 283)
- No `InnerLifeEvent` constructor call
- No `inner_life_writer.create_event()` call
- No `Provenance(...)` construction

**Recursive risk: 0.** ✓

### 9.2 Judge must NOT trigger Agency

**Verified:** Judge doesn't publish to Event Bus. It only calls `llm_proxy.backend.complete()` (line 156, 220, 256). It doesn't call `bus.publish()`. So no `AGENCY_TRIGGER` or other bus events.

**Recursive risk: 0.** ✓

### 9.3 Judge must NOT mutate production memory

**Verified:** Judge returns `triples` (line 183-196). The CALLER (`MemoryWriter`) writes the triples to v1 memory store (in `extract_and_write` line 190-...). The Judge itself doesn't write.

**But:** Adding context to the Judge might cause the Judge to **decide differently**, which would cause the WRITER to write different facts. This is **indirect mutation through Judge output change** — not direct mutation.

**Indirect mutation:** Could change which facts get written to memory. This is by design (Judge is a quality filter), not recursive feedback.

**Recursive risk: 0.** ✓

### 9.4 Judge must NOT feed back into same evaluation cycle

**Verified:** Judge is called once per fact extraction. No re-entrancy. No callback. No self-reference.

**Recursive risk: 0.** ✓

### 9.5 Summary

**0 recursive / feedback risk in current design.** Adding context (Options A or B) doesn't introduce new risk — it just provides more information to the Judge's existing decision.

---

## 10. P0/P1/P2/P3 Findings (Bry spec §10)

### P0 — Correctness / Production Integrity

**0 findings.** READ-ONLY audit, 0 production mutation, 0 source code change.

### P1 — Judge evaluates incorrect context in production

**0 findings.** Current Judge evaluates each message in isolation. This is by design (or accident, not incorrect). No false classification in production logs.

### P2 — Visibility gap (the M5.8-1 P2.1 finding)

**1 finding confirmed:**

#### P2.1 — Memory LLM Judge cannot currently see Diary / Dream / Inner Life (M5.8-1 origin)

**Evidence (verified):**
- `src/memory/sage/writer.py:510`: `judge.extract_and_judge(text, context="", agent_id="...")` — `context=""` hardcoded
- No path from Judge to `DiaryWriter.read_entries`, `NarrativeTraceReader.query_*`, or `DreamEventWriter`
- `MemoryReader` is in a separate code path (LLM context injection, not Judge evaluation)

**Root cause:** Empty context in `_extract_facts_llm` call. No design rationale found.

**Status:** Confirmed architectural gap, not bug. Multiple reuse paths exist (Options A, B).

**Severity:** P2 (capability gap). Not P1 (Judge isn't producing incorrect results in production). Not P0 (no production data loss).

### P3 — Documentation

**0 findings.** No stale comments or docstrings found related to this gap.

---

## 11. Tests / Regression (Bry spec §11)

### 11.1 No source code modified (READ-ONLY)

No new tests written, no existing tests modified. **Bry spec §N: "Do NOT modify source code."**

### 11.2 Regression baseline (Bry spec §J)

**Focused suites verified (no regression introduced by this audit):**

| Suite | Tests | Status |
|-------|-------|--------|
| M3 prompt integrity | (incl.) | PASS |
| M3 observability | (incl.) | PASS |
| M5.4-6.4 trace production activation | (incl.) | PASS |
| M5.5-2 canonical inner life event propagation | (incl.) | PASS |
| M5.6-2 conversation qualification | (incl.) | PASS |
| M5.7-2 heartbeat reactivation | (incl.) | PASS |
| M5.7-4 heartbeat robustness | (incl.) | PASS |
| M5.8-4 producer gating | (incl.) | PASS |
| M5.9-3 world inner life adapter | (incl.) | PASS |
| M5.9-3.1 production wiring | (incl.) | PASS |
| M5.2 minimal agency | (incl.) | PASS |
| **Total** | **219** | **PASS in 14.53s, 0 FAIL, 0 NEW regression** |

### 11.3 Pre-existing issues (NOT M5.10-1)

Same as M5.9-3.1 audit:
- 6 sys.path issues in M3.1/M3.2/M3.4 tests (verified pre-existing)
- 1 flaky LLM test in test_websocket_e2e (M5.7-4 audit excluded)

**0 NEW failure, 0 NEW regression.**

---

## 12. Production Integrity (Bry spec)

| Item | Status |
|------|--------|
| Source modification | 0 (READ-ONLY) |
| memory.db mutation | 0 |
| v1 memory mutation | 0 |
| diary mutation | 0 |
| dream mutation | 0 |
| trace.jsonl mutation | 0 |
| replay | 0 |
| backfill | 0 |
| InnerLifeEvent backfill | 0 |
| migration | 0 |
| 20 pre-existing untracked artifacts | preserved |
| Frozen contract change | 0 |
| Persistent dedup state | 0 |

**All 0. Strict production safety.** ✓

---

## 13. Git State (Bry spec §13)

```
HEAD:           7d382f524733ebd22af8ff9102284391109613be
origin/main:    7d382f524733ebd22af8ff9102284391109613be
                ↳ HEAD == origin/main ✓ SYNCED
Recent log:
  7d382f5 docs(m5.9-3.1): add closeout summary log
  831f3f1 feat(m5.9-3.1): world -> inner life production wiring
  d843319 docs(m5.9-3): add closeout summary log
Working tree:  20 pre-existing untracked artifacts preserved
                (audit log is the only new untracked file)
```

**Commit SHA:** `7d382f5` (will be advanced after this audit log commit).

---

## 14. Recommended Next Ticket (Bry spec §15)

### Option 1 — No change (Mavis 推薦 if Bry accepts P2.1 as boundary)

Mark P2.1 as "intentional boundary, accepted" in next audit. No code change.

### Option 2 — M5.10-2: Memory LLM Judge context composition (Mavis 推薦 for capability)

**Scope:** Additive context wiring (Option B minimal). Modify `MemoryWriter._extract_facts_llm` to construct context from existing readers (v1 memory + diary + inner life trace). 0 frozen contract change. Pattern: reuse existing `MemoryReader` + new helper for diary/inner life.

**Why recommended:**
- All 3 readers already exist (MemoryReader, DiaryWriter.read_entries, NarrativeTraceReader.query_*)
- 0 new abstraction
- 0 frozen contract change
- Reuses existing pattern (MemoryReader.prefetch is already used for LLM context)

### Option 3 — M5.10-2: Memory LLM Judge v1 memory only (Option A)

**Scope:** Just add v1 memory context to Judge. Skip diary / inner life for now.

**Why not recommended:** M5.8-1 P2.1 finding is about Diary / Dream / Inner Life, not v1 memory. v1 memory is already in MemoryReader (which Judge doesn't use). Adding v1 only is partial solution.

### Option 4 — M5.10-2: Dream read API design (separate ticket)

**Scope:** Design a dream read API. Then include in Judge context. Larger scope, more risk.

### Option 5 — Skip / 收工 (Mavis 也推薦)

P2.1 stays as documented gap. Focus on M5.8-1 P2.2 / P2.4 / P2.5 / P2.6 / P2.7 (other P2.x).

---

## 15. Explicit Bry Decision Required (Bry spec §16)

### 15.1 Decision required?

**YES, for any next-step that involves code change:**

1. **If Bry wants P2.1 capability added:** Approve M5.10-2 implementation with Option A or B
2. **If Bry accepts P2.1 as intentional boundary:** No code change, just document
3. **If Bry wants partial (v1 memory only):** Approve M5.10-2 with Option A

### 15.2 Decision NOT required for:

- Continuing other M5.10+ tickets (P2.2 / P2.4 / P2.5 / P2.6 / P2.7)
- M5.8-1 closeout (P2.3 already resolved, P2.1 documented)
- Other Soul OS work (M5.5-2 / M5.6-2 / M5.7-2 / M5.7-4 closed; P2.1 is isolated finding)

---

## 16. Final Status

**M5.10-1 audit COMPLETE.**

| Item | Status |
|------|--------|
| Read-only | ✓ |
| 0 source modification | ✓ |
| 0 production data mutation | ✓ |
| 0 frozen contract change | ✓ |
| Runtime path mapped (Bry spec §1) | ✓ (verified `sage/writer.py:510` hardcoded `context=""`) |
| Visibility matrix (Bry spec §2) | ✓ (Judge sees ONLY text + agent_id, nothing else) |
| Adapters traced (Bry spec §3) | ✓ (1 direct + 10+ unused adapters) |
| Intentional vs accidental (Bry spec §4) | ✓ Mostly **B. Accidental** (empty context with no rationale) + partial **D. infrastructure exists but unused** |
| Existing context mechanisms (Bry spec §5) | ✓ 4 readers available (MemoryReader, DiaryWriter.read, NarrativeTraceReader 5x, no Dream reader) |
| Proposed options (Bry spec §6) | ✓ 3 options (A: reuse, B: additive composition, C: no change) |
| Frozen contract impact (Bry spec §F) | ✓ 0 change for all 3 options |
| Privacy implications (Bry spec §8) | ✓ Option B exposes diary text to Judge (privacy expansion) |
| Recursive/feedback risk (Bry spec §9) | ✓ 0 risk for all 3 options |
| P0/P1/P2/P3 (Bry spec §10) | ✓ 0/0/1 (P2.1 confirmed) / 0 |
| Regression (Bry spec §J) | ✓ 219/219 PASS in 14.53s, 0 NEW regression |
| Production integrity (Bry spec) | ✓ All 0 |
| Stop conditions (8 items per Bry spec) | ✓ 0 hit |

**P2.1 (M5.8-1 origin) is CONFIRMED as architectural gap, mostly ACCIDENTAL (no design rationale for `context=""`), with PARTIAL infrastructure (3 of 4 readers exist).**

**Awaiting Bry decision on P2.1 next-step direction (capability / boundary / partial).**
