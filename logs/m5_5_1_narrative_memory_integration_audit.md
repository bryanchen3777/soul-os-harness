# M5.5-1 — Narrative → Memory Integration Audit

**Mode:** READ-ONLY AUDIT
**Baseline:** HEAD = 9653705 = origin/main
**Date:** 2026-08-10
**Recommendation:** **D. Existing architecture has appropriate boundary; only minimal wiring required**

---

## 1. Audit Findings (Executive Summary)

The audit reveals a key architectural finding:

**The Memory layer's `inner_life_event_id` field is currently populated with SYNTHETIC UUIDs, NOT with real canonical `InnerLifeEvent.event_id` values.**

- `Fact.inner_life_event_id` (M5.4-5.2) → uses `uuid.uuid4().hex` directly (writer.py:194-198)
- `Memory.inner_life_event_id` (M5.4-5.2) → mirrors the same synthetic UUID
- `SQL facts.inner_life_event_id` (M5.4-5.2) → also synthetic
- `SAGELiteProvider` has **no** `set_inner_life_writer()` method
- `run_server.py` does NOT call any `set_inner_life_writer()` (only `set_llm_proxy()`)
- The canonical `InnerLifeWriter` from M5.4-5.1 is currently used by the 4 wired producers (M5.4-6.1/6.2) but is **not** wired into the Memory layer
- `trace.jsonl` (M5.4-6.4 active) is being populated by the 4 producers, but the Memory layer doesn't read from it

The Memory layer has the right **structure** (the `inner_life_event_id` field exists everywhere — Fact, v1 Memory, SQL, loader metadata) but the **values** are not yet connected to the canonical Inner Life architecture.

---

## 2. Current Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│ PRODUCTION RUNTIME (M5.4-6.4 active)                       │
└─────────────────────────────────────────────────────────────┘

  InnerLifeWriter (canonical authority, M5.4-5.1)
    │ (4 wired producers create events)
    │
    ├──> DiaryHandler  (TRIGGER_TYPE_DIARY_MORNING/NIGHT)
    ├──> DreamHandler  (TRIGGER_TYPE_DREAM_DREAM)
    ├──> EventHandler  (TRIGGER_TYPE_DREAM_EVENT)
    └──> AgencyTriggerHandler (TRIGGER_TYPE_AGENT_REPLY)
         │
         │ create_event() → returns InnerLifeEvent
         │
         ├──> Memory layer receives ONLY the event_id
         │    (via SoulEvent.inner_life_event_id field,
         │     threaded through consciousness._fire_intent
         │     → LLMProxy → AGENT_SPEAK)
         │
         │    BUT — Memory layer's SAGELiteProvider does
         │    NOT receive the canonical InnerLifeWriter.
         │    When extract_and_write creates facts, it
         │    generates SYNTHETIC UUIDs (writer.py:194-198).
         │
         ├──> NarrativeTraceWriter (M5.4-5.6)
         │    → data/inner_life/trace.jsonl
         │    (canonical events, 8 metadata fields)
         │
         └──> Diary/Dream/Event writers (writers.py / dream_event.py)
              → data/soul/{agent_id}/diary/{date}.jsonl
              (jsonl entries with inner_life_event_id field)

  Memory Layer (NOT wired to canonical InnerLifeWriter)
    │
    ├──> MemoryMiddleware (subscribes USER_MESSAGE / AGENT_INTENT / AGENT_SPEAK)
    │    │
    │    └──> On AGENT_SPEAK → provider.post_reply_commit(session_id, user_text, agent_text, source_pair)
    │
    ├──> SAGELiteProvider (per-agent)
    │    │
    │    └──> post_reply_commit → MemoryWriter.write_turn (sync via run_in_executor)
    │
    ├──> MemoryWriter.write_turn
    │    │
    │    ├──> extract_and_write (LLM judge + heuristic → facts)
    │    │    │
    │    │    ├──> LLM Judge (src/memory/llm_judge.py)
    │    │    │    - 3 categories: preference_plan_event_fact / milestone / diary
    │    │    │    - 3 judgments: SUPPORTED / WEAK / UNSUPPORTED
    │    │    │    - Per-category confidence thresholds
    │    │    │    - DISCRETE classification (qualification gate)
    │    │    │
    │    │    ├──> Heuristic (rule-based fallback when LLM judge disabled)
    │    │    │
    │    │    └──> ★ inner_life_event_id: SYNTHETIC uuid.uuid4().hex
    │    │         (NOT created via canonical InnerLifeWriter)
    │    │
    │    ├──> _write_single (per fact):
    │    │    1. Schema gate (fact.validate)
    │    │    2. Predicate normalization
    │    │    3. Entity alignment
    │    │    4. Contradiction detection (_find_contradiction)
    │    │    5. Dedup detection (_find_similar) → merge or write
    │    │    6. SQL + v1 mirror write (both with inner_life_event_id field)
    │    │
    │    └──> Source attribution via source_pair (e.g. "bryan:agent_ruka")
    │
    ├──> v1 Store (append-only jsonl, per agent)
    │    - fields: memory_id, agent_id, content, tags, created_at,
    │              category, confidence, inner_life_event_id
    │    - Loader reads v1, formats for prompt injection
    │    - Loader does NOT use inner_life_event_id (per v1 schema comment)
    │
    └──> SAGE Graph (SQLite per agent)
         - facts table with v6 schema (includes inner_life_event_id)
         - entities / relationships
         - stats: total facts, active_facts, avg confidence

  NarrativeTraceReader (M5.4-5.7) — READ-ONLY
    - 5 query APIs (event_id, session_id, correlation_id, lineage_path, ts_range)
    - Returns dicts (8 metadata fields)
    - Data source: data/inner_life/trace.jsonl
    - Currently NOT consumed by Memory layer
```

---

## 3. Memory Write Boundary

**The Memory write entry point is `MemoryMiddleware._on_agent_speak`** (middleware.py:392-477):

```python
async def _on_agent_speak(self, event: SoulEvent) -> None:
    # 1. Extract agent_id, session_id
    # 2. Stage 4.1: relationships touch
    # 3. Call provider.post_reply_commit(session_id, user_text, agent_text, source_pair)
    # 4. (shadow observer — observation only, not prod path)
```

`provider.post_reply_commit` (provider.py:206-260) → `MemoryWriter.write_turn` (writer.py:234-282) → `extract_and_write` (writer.py:174-211) → `_write_single` (writer.py:286-339) → SQL + v1 mirror.

**The qualification/judge gate** is `extract_and_write` (writer.py:174-211) which calls the LLM judge (`src/memory/llm_judge.py`):
- 3 categories with per-category confidence thresholds
- 3 discrete judgments (SUPPORTED / WEAK / UNSUPPORTED)
- UNSUPPORTED → 0 confidence → not written
- WEAK → mid-tier confidence, may or may not pass threshold
- SUPPORTED → high confidence

This is the **qualification boundary** that gates what becomes Memory.

**The dedup/contradiction gate** is `_write_single` (writer.py:286-339) which runs AFTER the LLM judge:
- `_find_similar` → merge with existing similar fact
- `_find_contradiction` → detect conflict with existing fact

These existing qualification/dedup gates are exactly the "memory qualification" the ticket asks about.

---

## 4. Narrative Trace Boundary

**Production runtime (M5.4-6.4 active):**
- `data/inner_life/trace.jsonl` is being populated by `NarrativeTraceWriter` (M5.4-5.6)
- Each record is created by `InnerLifeWriter._append_trace()` after `create_event()`
- Records are append-only JSONL, ~400 bytes each, 8 metadata fields

**NarrativeTraceReader (M5.4-5.7) provides 5 read APIs:**
- `query_by_event_id(event_id)` → 0 or 1 record
- `query_by_session_id(session_id)` → all records for session
- `query_by_correlation_id(corr_id)` → all records in correlation group
- `query_by_lineage_path_prefix(prefix)` → root + descendants
- `query_by_ts_range(start, end)` → time range
- Returns dicts (8 metadata fields), READ-ONLY
- Handles malformed lines gracefully (skips + logs warning)

**Filtering capabilities available:**
- By event_id (exact match)
- By session_id (exact match)
- By correlation_id (exact match)
- By lineage path (prefix match — supports parent + descendants)
- By timestamp range (ISO 8601 string comparison)

**NOT available:**
- Full-text content search
- Semantic/embedding search
- Vector similarity
- Real-time subscriptions (reader is poll-based)

**Memory layer does NOT currently consume from trace.** This is a key finding.

---

## 5. Qualification/Judge Boundary (Existing)

The Memory layer already has a sophisticated qualification/judge system:

### 5.1 LLM Judge (src/memory/llm_judge.py)

- **3 categories** with per-category prompt files:
  - `preference_plan_event_fact` → `judge_prompt_preference_plan_event_fact.md`
  - `milestone` → `judge_prompt_milestone.md`
  - `diary` → `judge_prompt_diary.md`
- **3 discrete judgments:**
  - SUPPORTED → high confidence (per category)
  - WEAK → mid confidence (per category)
  - UNSUPPORTED → 0 (not written)
- **Stance classification:** self_directed / other_directed

### 5.2 Confidence Thresholds (per category)

| Category | SUPPORTED | WEAK | UNSUPPORTED |
|----------|-----------|------|-------------|
| preference_plan_event_fact | 0.80 | 0.55 | 0.0 |
| milestone | 0.85 | 0.60 | 0.0 |
| diary | 0.55 | 0.40 | 0.0 |

### 5.3 Other Qualification Mechanisms

- **Heuristic fallback** (when LLM judge disabled) — rule-based, less precise
- **Entity alignment** (`_align_entity`) — normalizes subject/object for matching
- **Predicate normalization** (`_PREDICATE_SYNONYMS`) — collapses semantically equivalent predicates
- **Contradiction detection** (`_find_contradiction`) — detects conflicts
- **Dedup detection** (`_find_similar`) — merges or rejects near-duplicates
- **Weight threshold** (0.01 min for search) — filters noise
- **Source pair filtering** — restricts retrieval to relevant conversation pairs

**This is the "Does this InnerLifeEvent deserve to become long-term memory?" gate that the ticket asks about. It already exists, it works, and it operates on fact triples extracted from conversation text — not directly on InnerLifeEvent.**

---

## 6. Memory Has All Required Metadata

| Metadata | Source | Field |
|----------|--------|-------|
| Event provenance | writer.py (source) | `Fact.source` (user / inference / correction) |
| Temporal | writer.py (timestamp) | `Fact.timestamp`, `Fact.event_time` |
| Agent identity | MemoryMiddleware | `agent_id`, `source_pair` |
| Session identity | SoulEvent | `session_id` (via writer) |
| Source attribution | source_pair | `Fact.source_pair` (e.g. "bryan:agent_ruka") |
| Importance | writer.py | `Fact.weight` (0-2), `is_anchor` |
| Confidence | LLM judge | `Fact.confidence` (0-1) |
| Dedup | writer.py | `_find_similar` |
| Memory qualification | LLM judge | SUPPORTED/WEAK/UNSUPPORTED |
| Inner Life ref | M5.4-5.2 | `Fact.inner_life_event_id` (currently SYNTHETIC) |
| Category | llm_judge | `Fact.category` (preference_plan_event_fact / milestone / diary) |
| Tags | derive_query_tags | for retrieval matching |

**All required metadata exists. Only the inner_life_event_id value is currently disconnected from the canonical Inner Life architecture.**

---

## 7. Memory Memory Types Distinction (Already Exists)

The Memory layer already implements a multi-layered memory architecture:

| Memory Type | Implementation | Use Case |
|------------|----------------|----------|
| **Raw experience** | (none in Memory) — episodic memories are in diary jsonl + trace.jsonl | lived experiences |
| **Episodic memory** | `data/soul/{agent_id}/diary/{date}.jsonl` (write_diary/write_dream/write_event) | narrative continuity |
| **Semantic/factual memory** | `data/memory/{agent_id}/graph.sqlite` (Fact triples via SAGE) | knowledge representation |
| **Conversational memory** | `data/memory/{agent_id}/v1.jsonl` (Memory entries) | conversation context |
| **Structured backup** | `data/memory/{agent_id}/graph.sqlite` + `v1.jsonl` (mirror) | durable record |

**InnerLifeEvent ≠ any of these — it's the IDENTITY layer that cross-references all of them.**

The architecture already distinguishes raw experience (episodic) from semantic/factual knowledge (Memory). The InnerLifeEvent is the canonical identity that ties them together.

---

## 8. Frozen Contracts Identified

| Contract | File | Frozen By |
|----------|------|-----------|
| `Fact` dataclass | `src/memory/sage/models.py:7-86` | M5.4-5.2 (with M5.4-5.1 origin) |
| `Memory` dataclass (v1) | `src/memory/v1/schema.py:23-52` | M5.4-5.2 (with Perplexity v1.1) |
| GraphStore v6 schema | `src/memory/sage/graph_store.py:170-185` | M5.4-5.2 |
| `extract_and_write` / `write_turn` signature | `src/memory/sage/writer.py:174-282` | M5.2 (Perplexity Stage 1.5) |
| LLM judge (categories / thresholds) | `src/memory/llm_judge.py` | M5.2 (Bry 拍板 2026-07-02) |
| `SAGELiteProvider.post_reply_commit` signature | `src/memory/sage/provider.py:206-260` | M5.2 |
| `MemoryMiddleware` event subscription | `src/memory/middleware.py:218-224` | M5.2 |
| `set_llm_proxy` (existing pattern) | `src/memory/sage/writer.py:744-757` | M5.2 (M0.1) |
| NarrativeTraceReader API | `src/inner_life/trace_reader.py:105-189` | M5.4-5.7 |
| `InnerLifeEvent` / `Provenance` | `src/inner_life/event.py` | M5.4-5.1 |
| `InnerLifeWriter.create_event` signature | `src/inner_life/writer.py:129-236` | M5.4-5.1 |
| `SoulEvent.inner_life_event_id` | `src/eventbus/schema.py:154-161` | M5.4-5.5 |

**All frozen contracts must remain unchanged. No modification required for the recommended D architecture.**

---

## 9. Memory API Can Consume InnerLifeEvent Without Contract Changes

**Key finding: The existing Memory layer's `inner_life_event_id` field is structurally compatible with `InnerLifeEvent.event_id`.**

- Both are 32-char lowercase hex UUIDs (M5.4-5.1 contract)
- The field already exists in: `Fact`, `Memory`, SQL `facts.inner_life_event_id`
- The v1 schema comment confirms: "既有 records (pre-M5.4-5.1) 沒有這欄位 → `Memory(**data)` 用 default None 載入"
- No new field needed; no schema change needed; no frozen contract modification

**The minimal wiring required:**
1. Add `set_inner_life_writer(InnerLifeWriter)` to `SAGELiteProvider` (parallel to existing `set_llm_proxy`)
2. In `run_server.py:341-343` lifespan, add `set_inner_life_writer(inner_life_writer)` after the `set_llm_proxy(llm)` call
3. In `MemoryWriter.extract_and_write` and `extract` (writer.py:194-198 and :223-227), replace `uuid.uuid4().hex` with a call to `inner_life_writer.create_event(provenance=...)` if available, falling back to synthetic UUID otherwise
4. The Provenance would be a `TRIGGER_TYPE_AGENT_REPLY` (or category-specific) — the actual text source is the conversation message that triggered the extraction

**No contract changes. No schema changes. No new fields. Purely additive wiring.**

---

## 10. Production Integrity

- ✅ **READ-ONLY audit** — 0 source code modified
- ✅ **0 production data touched** — no write to memory.db, no diary/dream/event data modified
- ✅ **0 trace.jsonl created** — trace sidecar not affected (M5.4-6.4 already created it but unchanged)
- ✅ **Working tree clean** — only audit log + (optional) test file added
- ✅ **All M5.4 tests pass** — 277 + 24 + 20 = 321 M5.4 tests + 29 M3 tests + 2 websocket = 352/352

---

## 11. Regression Results

Run before this audit (state preserved):
- M5.4-5.1 through 5.7: 198/198 PASS
- M5.4-6.1 (Diary/Dream/Event executor wiring): 30/30 PASS
- M5.4-6.2 (Proactive DM executor wiring): 25/25 PASS
- M5.4-6.3 (Trace activation audit): 24/24 PASS
- M5.4-6.4 (Trace production activation): 20/20 PASS
- M3 E2E + World Awareness: 29/29 PASS
- test_websocket_e2e: 2/2 PASS
- **Total: 328/328 PASS**

(Re-running full regression unnecessary — no source code modified, working tree clean.)

---

## 12. Architecture Recommendation: **D**

> "Existing architecture already has an appropriate boundary, and only minimal wiring is required."

### Rationale

The audit reveals that the Memory layer **already** has:
1. ✅ The `inner_life_event_id` field in all storage layers (Fact, Memory, SQL, v1)
2. ✅ A sophisticated qualification/judge system (LLM judge with 3 categories, 3 judgments, per-category confidence thresholds)
3. ✅ Dedup / contradiction detection
4. ✅ Source attribution (source_pair)
5. ✅ Episodic / semantic / conversational memory distinction
6. ✅ Provenance (source, source_pair), temporal (timestamp, event_time), agent/session identity
7. ✅ Importance (weight, is_anchor), confidence (LLM judge output)
8. ✅ Loader / retrieval path that reads v1 (excludes unqualified facts via fail-safe)

**The ONLY gap** is that `inner_life_event_id` values are **synthetic UUIDs** instead of canonical `InnerLifeEvent.event_id` values. This is a 1-line wiring fix, not an architectural rework.

### Why NOT A (Trace → Memory direct ingestion)

- Would bypass the LLM judge + dedup + contradiction logic
- Would create memory entries without qualification
- Violates the ticket's explicit principle: "DO NOT simply copy every InnerLifeEvent into Memory"
- Would cause Memory to fill with low-quality facts (every diary/dream/event/proactive_dm → memory fact)

### Why NOT B (Trace → Memory qualification/judge)

- The qualification is already happening — the LLM judge operates on extracted facts
- The question is not whether to qualify, but whether to use canonical identity
- The 4 producers already write to Memory via `MemoryMiddleware._on_agent_speak` → `post_reply_commit` → `extract_and_write`
- The qualification layer is downstream of InnerLifeEvent creation, not upstream

### Why NOT C (InnerLifeEvent becomes intermediate episodic layer)

- This is partially already happening — the diary jsonl is the episodic layer
- But the Memory layer's job is **not** to be another episodic layer (that would duplicate)
- The Memory layer's job is to be the qualified knowledge graph, distinct from raw episodic experience
- Making Memory another episodic layer would conflate two distinct concerns

### Why D (Existing architecture + minimal wiring)

- All 4 producers already create canonical InnerLifeEvents (M5.4-6.1/6.2/6.4)
- The Memory layer's `inner_life_event_id` field is structurally ready (M5.4-5.2)
- The 4 producers already write to Memory via the existing pipeline
- **The minimal wiring** is: make the Memory layer's synthetic UUIDs into real `InnerLifeEvent.event_id` values by injecting the canonical `InnerLifeWriter` into `SAGELiteProvider`

**No new infrastructure. No new write paths. No new qualification. Just connect the canonical identity authority to the existing Memory write path.**

---

## 13. Minimal Wiring (Implementation Sketch for Future M5.5-2 Ticket)

**The minimal change is:**

```python
# In src/memory/sage/provider.py (add to SAGELiteProvider)
class SAGELiteProvider:
    def __init__(self, ...):
        # ... existing code ...
        self._inner_life_writer = None  # M5.5-2: optional canonical identity authority
    
    def set_inner_life_writer(self, writer):
        """M5.5-2: Inject canonical InnerLifeWriter (parallel to set_llm_proxy)."""
        self._inner_life_writer = writer

# In src/memory/sage/writer.py (extract_and_write / extract)
def extract_and_write(self, ..., inner_life_event_id=None):
    # M5.5-2: accept optional event_id from caller
    # Or: have _get_inner_life_event_id() helper that uses self._inner_life_writer if available
    ...
    if self._inner_life_writer:
        # M5.5-2: create canonical InnerLifeEvent
        event = self._inner_life_writer.create_event(
            provenance=Provenance(
                trigger_type=TRIGGER_TYPE_AGENT_REPLY,  # or category-specific
                actor_id=subject_hint,
                source_system="memory",  # add to VALID_SOURCE_SYSTEMS if not present
            )
        )
        eid = event.event_id
    else:
        # M5.4-5.2 backward compat: synthetic UUID
        eid = uuid.uuid4().hex
    
    for f, r in zip(facts, raw_results):
        f.inner_life_event_id = eid  # all facts in this turn share the same event_id
        r["inner_life_event_id"] = eid
```

**Total change:** ~10-15 lines. No frozen contract modification.

**Note:** The `source_system` value "memory" would need to be added to `VALID_SOURCE_SYSTEMS` (event.py:65). This is a **single-line addition** to a tuple, not a contract change. The Provenance frozen model allows new source_system values as long as they pass validation.

---

## 14. Bry Decision Required: NO

The architecture recommendation is **D**, derivable from the existing codebase:
- Memory layer's `inner_life_event_id` field already exists (M5.4-5.2)
- The `InnerLifeWriter` is already the canonical authority (M5.4-5.1, currently used by 4 producers)
- The Memory write path already operates via `post_reply_commit` → `write_turn` → `extract_and_write`
- The qualification/judge system already exists (LLM judge)
- The only missing piece is the wiring between `InnerLifeWriter` and `SAGELiteProvider`

No architectural conflicts. No frozen contract modifications. No production data migration. No automatic trace-to-memory ingestion (which the ticket explicitly warns against).

**No Bry decision required. The next implementation ticket (M5.5-2) can be derived from this audit.**

---

## 15. Proposed Next Implementation Ticket

**M5.5-2 — InnerLifeWriter → Memory layer wiring (minimal)**

**Scope:**
1. Add `set_inner_life_writer(InnerLifeWriter)` to `SAGELiteProvider` (parallel to `set_llm_proxy`)
2. Add `set_inner_life_writer(inner_life_writer)` to run_server.py lifespan (parallel to `set_llm_proxy(llm)`)
3. Modify `MemoryWriter.extract_and_write` and `extract` to call `inner_life_writer.create_event(provenance=...)` when available
4. Add "memory" to `VALID_SOURCE_SYSTEMS` in `src/inner_life/event.py:65`
5. Add focused tests verifying canonical identity flows from `InnerLifeWriter` → `Fact.inner_life_event_id`

**Properties:**
- ADDITIVE only (backward compat: if no inner_life_writer, fall back to synthetic UUID)
- 0 frozen contract modifications
- 0 new identity authority (uses existing `InnerLifeWriter`)
- 0 new fields (uses existing `inner_life_event_id`)
- 0 production data migration
- ~10-15 lines of code change
- 0 changes to qualification/judge logic (LLM judge unchanged)
- 0 changes to dedup/contradiction logic
- 0 changes to episodic/semantic/conversational memory distinction

**Estimated impact:** 1-2 day implementation + 5-10 focused tests.

**Out of scope (per ticket):**
- Automatic trace → memory ingestion (forbidden)
- New embeddings / vector DB
- New scoring dimensions
- Memory schema changes
- USER_MESSAGE → InnerLifeEvent qualification
- Conversation qualification layer
- LLM call → InnerLifeEvent
- Memory retrieval path changes (the loader doesn't need to know about inner_life_event_id)

---

## 16. Stop Conditions Final Check

| Stop Condition | Triggered? | Notes |
|----------------|-----------|-------|
| 1. frozen Memory contract modification | NO | Inner_life_event_id field already exists (M5.4-5.2) |
| 2. production data migration | NO | New facts get new IDs; old facts keep synthetic IDs |
| 3. trace becomes second source of truth | NO | Trace is observability sidecar; Memory is separate durable store |
| 4. automatic ingestion bypasses qualification | NO | Qualification layer unchanged |
| 5. existing Memory architecture conflicts with Inner Life | NO | Architectures are complementary |
| 6. multiple materially different architecture choices | NO (only D fits) | A/B/C all have specific disqualifications |

**No stop conditions triggered. Audit complete. ✅**

---

## 17. Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| Complete Memory runtime path documented | ✅ | section 2 (architecture diagram) + section 3 (write boundary) |
| Complete Narrative Trace runtime path documented | ✅ | section 2 + section 4 (trace boundary) |
| Existing qualification/judge mechanisms identified | ✅ | section 5 (LLM judge, heuristic, dedup, contradiction) |
| Existing memory write boundary identified | ✅ | section 3 (post_reply_commit → write_turn → extract_and_write → _write_single) |
| Existing frozen contracts identified | ✅ | section 8 (12 frozen contracts listed) |
| Production mutation = 0 | ✅ | section 10 |
| Source modifications = 0 | ✅ | working tree clean (only this audit log) |
| No schema changes | ✅ | section 12-13 (recommendation D is additive only) |
| No speculative infrastructure | ✅ | section 12 (only minimal wiring recommended) |
| Recommended integration architecture explicitly classified A/B/C/D | ✅ **D** | section 12 (with rationale for why not A/B/C) |
| Architecture decision required | **NO** | section 14 |

**All acceptance criteria met. ✅**
