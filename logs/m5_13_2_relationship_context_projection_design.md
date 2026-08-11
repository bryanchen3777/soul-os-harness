# M5.13-2 — Relationship Context Projection Design Audit

**Mode:** DESIGN AUDIT (READ-ONLY)
**Baseline:** `e940934` (M5.13-1)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT

---

## Executive Summary

**Question (from M5.13-1):** Should relationship information enter the LLM prompt? If so, how?

**Finding:** **YES, but minimal.** Only one field (`confidence`) currently has behavioral variance. The other subjective fields (`feeling`, `impression`) are always at default values in production. A bounded deterministic projection of `confidence` is the minimal integration.

**Final design:**
```
RelationshipsStore.get(BRYAN_ENTITY_ID)
      ↓
_format_relationship_block(agent_id) [new helper in proxy.py]
      ↓
SOCIAL_CONTEXT block (deterministic, bounded, agent-scoped)
      ↓
proxy.py _build_messages_group/_private composition
      ↓
LLM system prompt
```

**Recommended next ticket:** M5.13-3 (minimal implementation) — but **only if Bry approves**. Otherwise: **maintain current boundary (no relationship in prompt)** because the available behavioral signal is too thin to justify the implementation cost.

---

## 1. Field Behavioral Value Analysis

### Current relationship entry schema (from `relationships.py:91-99`)

```python
{
    "impression": str,                # default ""
    "feeling": str,                   # default "neutral"
    "confidence": float,              # default 0.3 (stranger)
    "interaction_count": int,         # default 0
    "last_interaction_at": str|None,  # default None (ISO 8601 UTC)
    "last_updated": str,              # default now (ISO 8601 UTC)
    "created_at": str,                # default now (ISO 8601 UTC)
}
```

### Actual production data (`logs/relationships_before_m0_4.json`)

| Field | Production values | Variance | Behavior-bearing? |
|-------|------------------|----------|-------------------|
| `confidence` | 0.04 – 0.83 (continuously updated) | **HIGH** | ✅ YES — trust signal |
| `feeling` | Always `"neutral"` | NONE | ❌ NO — no producer sets it |
| `impression` | Always `""` (except 1 example: `"姉様はいつも穏やか"`) | NONE | ❌ NO — no producer fills it |
| `interaction_count` | 0 – N (monotonic) | NONE | ⚠️ Metadata, not behavior |
| `last_interaction_at` | ISO timestamps (continuously updated) | HIGH | ⚠️ Can derive recency |
| `last_updated` | ISO timestamps (continuously updated) | HIGH | ⚠️ Metadata, not behavior |
| `created_at` | ISO timestamps (set once) | NONE | ⚠️ Persistence metadata |

### Producer code for each field

| Field | Producer | Trigger | Status |
|-------|----------|---------|--------|
| `confidence` | `RelationshipsStore.touch()` (delta-based) | USER_MESSAGE, AGENT_SPEAK, dream, event | ✅ ACTIVE |
| `feeling` | `RelationshipsStore.touch(feeling=...)` | caller-supplied | ❌ **0 callers** (always None) |
| `impression` | `RelationshipsStore.update_impression()` (LLM-generated) | LLM call (Stage 4.3) | ❌ **0 callers** (only stub signature) |
| `interaction_count` | `RelationshipsStore.touch()` | same as confidence | ✅ ACTIVE |
| `last_interaction_at` | `RelationshipsStore.touch()` | same as confidence | ✅ ACTIVE |
| `last_updated` | all writes | all writes | ✅ ACTIVE |
| `created_at` | set once at creation | initial entry | ✅ ACTIVE (static) |

### Behavioral value verdict (per Bry's question)

| Field | Verdict | Rationale |
|-------|---------|-----------|
| `confidence` | ✅ **HAS behavioral value** | Trust signal 0.0-1.0 with documented thresholds (0.3=陌生人, 0.5=已知身份, 0.7=姐妹, 0.9=深度信任) |
| `feeling` | ❌ **NO behavioral value** | Always `"neutral"` — no production code sets it |
| `impression` | ❌ **NO behavioral value** | Always `""` — no production code generates it (Stage 4.3 stub) |
| `interaction_count` | ⚠️ **Metadata only** | Monotonic, not behavior-bearing (a single long conversation vs many short ones both increase it) |
| `last_interaction_at` | ⚠️ **Derivable recency** | Same as `chrono-social` block's `silence_hours` — duplication risk |
| `last_updated` | ⚠️ **Persistence metadata** | Not behavior-bearing |
| `created_at` | ⚠️ **Persistence metadata** | Static, not behavior-bearing |

**Conclusion: Only `confidence` provides stable behavioral signal today.** `feeling` and `impression` require Stage 4.3 LLM generation (out of scope for M5.13-2).

---

## 2. Fields to Enter LLM (Minimum Set)

**Decision:** Expose ONLY `confidence` (single value per target entity) in a deterministic text block.

**Why not include other fields:**

- `feeling` always `"neutral"` — exposing a constant is noise
- `impression` always `""` — empty string is noise
- `interaction_count` — `chrono-social.silence_hours` already captures temporal signal
- `last_interaction_at` — `chrono-social.silence_hours` duplicates this
- `last_updated` / `created_at` — pure persistence metadata, no behavior meaning

**Why include `confidence`:**

- It has documented thresholds (Bry 派工 2026-07-18)
- It's continuously updated by `MemoryMiddleware`
- It varies meaningfully (0.04-0.83 in current data)
- It's a recognized behavioral signal: "do I trust this person enough to be open?"

---

## 3. Fields to NOT Enter LLM

| Field | Reason for exclusion |
|-------|---------------------|
| `feeling` | Currently always `"neutral"`; would expose empty constant; waiting on Stage 4.3 LLM generation |
| `impression` | Currently always `""`; raw short text without semantic anchor; waiting on Stage 4.3 |
| `interaction_count` | No behavioral signal beyond what silence_hours provides; pure metadata |
| `last_interaction_at` | Duplicated by chrono-social block; would be double-sourced |
| `last_updated` | Persistence metadata, no behavior meaning |
| `created_at` | Static, no behavior meaning |
| `confidence` (raw float 0.85) | Should be projected into discrete qualitative bands, not raw number (LLM may over-fit to precision) |

---

## 4. Context Representation

### Decision: discrete qualitative bands, not raw float

**Why bands, not float:**
- Raw `0.85` is arbitrary precision — LLM may over-interpret "0.85 vs 0.84"
- Discrete bands match the documented thresholds in `relationships.py:62-69` (CONFIDENCE_*)
- Bands are interpretable: "陌生" / "認識" / "熟悉" / "親密"
- Easier for LLM to reason about character stance

### Proposed projection function

**Location:** `src/llm/proxy.py` (new helper, additive)

**Signature:**
```python
def _format_relationship_block(agent_id: str) -> str:
    """
    M5.13-2 (Bry 派工 2026-08-11): 將 RelationshipsStore 投影成
    deterministic, bounded, agent-scoped SOCIAL_CONTEXT block。
    
    Per-agent 過濾: 只查 THIS agent 對 BRYAN_ENTITY_ID 的 relationship。
    其他 relationships (agent↔agent) 留給 Stage 4.2/4.3。
    
    閾值:
      - confidence >= 0.7 → "親密"
      - 0.5 <= confidence < 0.7 → "熟悉"
      - 0.3 <= confidence < 0.5 → "認識"
      - confidence < 0.3 → "陌生" (預設陌生 = skip injection)
    
    Skip 條件:
      - relationship 不存在 (store.get returns None)
      - confidence < 0.3 (陌生人, 沒 behavioral signal)
      - store read 失敗 (fail-silent, 不 crash prompt construction)
    """
```

**Deterministic output format:**
```
[你跟 Bry 的關係]
  熟悉度: 親密
```

**Multi-line (if needed for future):**
```
[你跟 Bry 的關係]
  熟悉度: 親密
  認識: 75 次互動
  上次互動: 1.5 小時前
```

**Bounded selection:**
- ONLY BRYAN_ENTITY_ID included (per-agent filtering)
- max 1 entry (single line)
- max ~50 chars total (similar to world_context scale)

**No semantic search / embedding:** all projection is pure deterministic.

---

## 5. Threshold / Bounded Selection Specification

### Confidence → Band mapping (deterministic)

| Confidence range | Band label | Include in prompt? |
|------------------|-----------|---------------------|
| `[0.0, 0.3)` | (陌生, no signal) | ❌ NO — too thin |
| `[0.3, 0.5)` | 認識 | ✅ YES — first meaningful band |
| `[0.5, 0.7)` | 熟悉 | ✅ YES |
| `[0.7, 0.9)` | 親密 | ✅ YES |
| `[0.9, 1.0]` | 深度信任 | ✅ YES |

**Rationale for 0.3 cutoff:**
- `CONFIDENCE_DEFAULT_STRANGER = 0.3` (from `relationships.py:57`) — anything below this is "uninitialized"
- Below 0.3 = "haven't established meaningful relationship" = no behavioral signal
- Above 0.3 = at least "known but no interaction" tier

### Per-agent filtering

**Default: only `BRYAN_ENTITY_ID` for this agent's perspective.**
- For agent_yua: only `store.get("user_bryan")` → formatted into social block
- Future: multi-target (other agents) requires separate design ticket

**Skip if:**
- relationship not yet created (returns None)
- confidence below 0.3 threshold
- store.read exception (fail-silent, log debug, return "")

**No new persistence, no new schema fields, no new read APIs.**

---

## 6. Privacy / Boundary

### What becomes conversational context

| Field | Conversational? | Privacy concern |
|-------|----------------|-----------------|
| `confidence` (as band) | ✅ YES | LOW — abstract trust level, not character-specific |
| `feeling` ("warming", "guarded", etc.) | ❌ NO | MEDIUM — subjective character stance; could bias LLM into fixed persona |
| `impression` (短日文片語) | ❌ NO | MEDIUM — too character-specific, could be misread by LLM as instruction |

### What stays in persistence layer

- `feeling` (raw string)
- `impression` (raw string)
- `interaction_count`
- `last_interaction_at`
- `last_updated`
- `created_at`
- raw `confidence` (only the band enters LLM)

### Why `feeling` and `impression` are excluded (privacy + behavior)

**`feeling`** is explicitly a subjective stance ("guarded", "warming") that was designed as a private character attribute. Exposing it to LLM:
- Creates a fixed persona prompt ("you feel warming toward Bry")
- Risk: LLM stops inferring character from context, starts echoing stored labels
- Same problem as explicit "[mood: happy]" tags in early-stage LLM agents

**`impression`** is a 20-char LLM-generated short phrase:
- Bry 派工 2026-07-18 17:00: "短日文片語 (預設 ≤20 字)"
- Designed for cross-agent sharing, not LLM prompt
- Exposing to LLM creates circular reference (LLM sees its own previous impression generation)

### Future privacy consideration

If Bry later wants `feeling` or `impression` in prompt:
- Requires Stage 4.3 LLM generation (currently no producer)
- Requires explicit "summarize, don't echo" instruction
- Requires per-target consent / Owner policy
- **M5.13-2 does NOT address this — it's a future design ticket**

---

## 7. Injection Point and Precedence

### Current proxy.py composition order (group + private)

```python
system_parts = [
    identity_anchor + soul,                  # 1. persona
    # if memory_context:                    # 2. memory
    # if mood_desc:                         # 3. mood (transient)
    # if inner_life:                        # 4. inner_life (recent past)
    # if world_context:                     # 5. world (perception)
    # if current_time: temporal + chrono    # 6. temporal (chrono-social)
    # if bry_block:                         # 7. bry recent msgs
]
+ conversation_history                       # 8. session history
+ current_intent                             # 9. user message
```

### Recommended injection point: **between mood and inner_life**

```python
system_parts = [
    identity_anchor + soul,                  # 1. persona
    # if memory_context:                    # 2. memory (past events)
    # if mood_desc:                         # 3. mood (current transient state)
    # ★ NEW: if relationship_block:         # 4. relationship (current stance)
    # if inner_life:                        # 5. inner_life (recent past)
    # if world_context:                     # 6. world
    # if current_time: temporal + chrono    # 7. temporal
    # if bry_block:                         # 8. bry recent
]
```

### Rationale for this position

| Block | Time horizon | Semantic role |
|-------|--------------|---------------|
| memory_context | past (event-time) | "you remember..." |
| mood | current transient | "you feel right now..." |
| **relationship** | **current persistent** | **"you regard this person as..."** |
| inner_life | recent past | "you've been thinking about..." |
| world_context | current | "around you..." |
| temporal | current | "it's currently..." |
| bry_block | recent past | "Bry recently said..." |

`relationship` belongs at the same level as `mood` (current state) and `inner_life` (recent state). It's a **persistent stance** that:
- Is more stable than mood (decay: 0.02/day vs emotional engine's transient)
- Is more general than inner_life (a stance applies to all interactions, not specific events)
- Belongs BEFORE inner_life (general stance) and AFTER mood (transient state)

### Per-block precedence

When LLM sees these blocks, the order matters because LLMs attend to earlier blocks more:
- Persona establishes WHO
- Memory establishes WHAT HAPPENED
- Mood establishes CURRENT FEELING
- **Relationship establishes REGARD FOR BRY** (general stance)
- Inner_life establishes WHAT YOU'VE BEEN THINKING
- World establishes WHAT'S AROUND YOU
- Temporal establishes WHEN

The relationship block is correctly placed at the "current persistent state" level.

### Group vs Private parity

Both `_build_messages_group` and `_build_messages_private` should inject the relationship block at the same position. This ensures group-mode and private-mode have consistent stance signaling.

---

## 8. Identity / Provenance

### Per-agent scoping (CRITICAL)

The relationship block is **THIS agent's perspective**:
- `agent_yua`'s relationship with Bry: 0.85 (親密)
- `agent_ruka`'s relationship with Bry: 0.45 (認識)
- `agent_akane`'s relationship with Bry: 0.78 (親密)

Each agent's prompt must include ONLY their own relationship data. Cross-contamination would break character consistency.

**Implementation:** `_format_relationship_block(agent_id)` queries `RelationshipsStore.get(agent_id) → store.get(BRYAN_ENTITY_ID)`. The `agent_id` is already passed to `_build_messages_group/_private`.

### Target identity (clear, explicit)

The block should explicitly name the target:
- ✅ "你跟 Bry 的熟悉度: 親密"
- ❌ "你的熟悉度: 親密" (ambiguous target)

The word "Bry" is the user's name (per current persona-driven naming convention; could be generalized to "{user_id}" in multi-user mode).

### Avoiding cross-agent contamination

- Each agent's prompt only includes their own relationship state
- No aggregated "all agents' relationships with Bry" block
- No cross-agent relationship projection (A's view of B) in M5.13-2

---

## 9. Frozen Contract Verification

| Contract | Status | Notes |
|---------|--------|-------|
| `AgencyState` | ✅ FROZEN | No change |
| Stage 1-4 pure functions | ✅ FROZEN | No change |
| `TriggerEnvelope` | ✅ FROZEN | No change |
| `RelationshipsStore` schema | ✅ FROZEN | No change (read-only access) |
| `RelationshipsStore.get()` API | ✅ FROZEN | Already defined, no signature change |
| `Event Bus` contracts | ✅ UNCHANGED | No event changes |
| `MemoryMiddleware` | ✅ UNCHANGED | M5.13-2 does not modify middleware |
| `InnerLifeEvent` schema | ✅ FROZEN | No change |
| `WorldEvent` schema | ✅ FROZEN | No change |
| Proxy.py block order (private/group) | ✅ UNCHANGED | Additive — new block appended, no existing block moved |

**All frozen contracts remain intact.** M5.13-2 is purely additive.

---

## 10. Final Minimal Integration Contract

### What M5.13-3 (next implementation ticket) would add

**Single new function in `src/llm/proxy.py`:**

```python
# ── M5.13-2/3 (Bry 派工 2026-08-11): SOCIAL_CONTEXT projection ──

def _format_relationship_block(agent_id: str) -> str:
    """
    M5.13-2 (Bry 派工 2026-08-11): 將 RelationshipsStore 投影成
    deterministic, bounded, agent-scoped SOCIAL_CONTEXT block。
    
    Per-agent 過濾: 只查 THIS agent 對 BRYAN_ENTITY_ID 的 relationship。
    
    閾值: confidence >= 0.3 才輸出 (陌生人以下不輸出)
    
    Output format:
        [你跟 Bry 的關係]
          熟悉度: {band_label}
    
    Returns "" if:
      - relationship 不存在
      - confidence < 0.3
      - store read 失敗 (fail-silent)
    """
    try:
        from src.soul.relationships import (
            get_relationships_manager,
            BRYAN_ENTITY_ID,
        )
        manager = get_relationships_manager()
        store = manager.get_store(agent_id)
        if store is None:
            return ""
        rel = store.get(BRYAN_ENTITY_ID)
        if not rel:
            return ""
        confidence = rel.get("confidence", 0.0)
        if confidence < 0.3:
            return ""
        # Band mapping (deterministic)
        if confidence >= 0.9:
            band = "深度信任"
        elif confidence >= 0.7:
            band = "親密"
        elif confidence >= 0.5:
            band = "熟悉"
        else:  # [0.3, 0.5)
            band = "認識"
        return f"[你跟 Bry 的關係]\n  熟悉度: {band}"
    except Exception as e:
        logger.debug(
            f"[M5.13-2 SOCIAL_CONTEXT] projection failed (fail-silent): "
            f"agent={agent_id} err={type(e).__name__}: {e}"
        )
        return ""
```

**Single new injection point in `_build_messages_group` (L362 area) and `_build_messages_private` (L630 area):**

```python
# After mood_desc injection, before inner_life injection
# M5.13-2: SOCIAL_CONTEXT block (relationship → Bry)
relationship_block = _format_relationship_block(agent_id)
if relationship_block:
    system_parts.append(f"\n{relationship_block}")
```

### Properties of the integration

| Property | Value |
|---------|-------|
| Files modified | 1 (`src/llm/proxy.py`) |
| New functions | 1 (`_format_relationship_block`) |
| New dependencies | 0 |
| New schemas | 0 |
| New persistence | 0 |
| New event types | 0 |
| Frozen contract changes | 0 |
| Lines added (approx) | ~40 |
| Behavioral signal | confidence only (single axis) |

### What M5.13-3 does NOT do

- ❌ Does not modify RelationshipsStore schema
- ❌ Does not expose `feeling` or `impression` (no production producer)
- ❌ Does not expose other agents (only BRYAN_ENTITY_ID)
- ❌ Does not modify MemoryMiddleware
- ❌ Does not change proxy.py block ORDER (additive at end of system_parts)
- ❌ Does not introduce semantic search / embedding
- ❌ Does not introduce LLM judge for social context

---

## 11. Alternative: Maintain Intentional Boundary

If Bry decides the behavioral signal is too thin, the alternative is to **close the P1 gap as INTENTIONAL** for now:

**Rationale:**
- Only `confidence` has behavioral signal (single axis, 0.0-1.0)
- One-dimensional signal may not justify the implementation cost
- Stage 4.3 LLM generation of `feeling`/`impression` is required for richer signal
- Current inner_life (diary/dream/event) already captures subjective character state
- MemoryMiddleware already touches relationship on every interaction
- Without LLM-generated impression, character can only signal "trust level" not "stance"

**Implications of maintaining boundary:**
- LLM continues to have zero relationship awareness
- Character consistency depends entirely on diary/memory/inner_life
- P1 gap documented but not closed

**Comparison:**

| Aspect | Implement M5.13-3 | Maintain boundary |
|--------|-------------------|-------------------|
| Behavioral signal | Single-axis (trust) | None |
| Implementation cost | ~40 lines, 1 file | 0 |
| Risk of LLM persona lock-in | LOW (band, not raw float) | N/A |
| Risk of bias from subjective labels | None (no feeling/impression) | N/A |
| Privacy surface | Minimal (abstract band) | None |
| Reversibility | Easy (remove one append) | N/A |
| Alignment with frozen contracts | ✅ Additive | ✅ No change |

**Bry's call:** The current signal (confidence-only) is meaningful but thin. Whether to implement depends on Bry's preference for the trade-off.

---

## 12. STOP Conditions Check

| Condition | Hit? |
|-----------|------|
| 1. Frozen contract must change | ❌ No |
| 2. Production data mutates | ❌ No (read-only on store) |
| 3. Recursive autonomous feedback | ❌ No (projection is one-way) |
| 4. P0/P1 issue revealed | ❌ No (audit only) |
| 5. Multiple architecture directions have materially different long-term consequences | ⚠️ YES — Bry decision required on (A) implement M5.13-3 or (B) maintain boundary |
| 6. Cannot produce coherent recommendation without Owner decision | ⚠️ YES — confidence-only signal is too thin for definitive "yes implement" |

**Conclusion:** Bry decision required for the final direction.

---

## 13. Architectural Recommendation

### Recommendation: **B (Bry decision gate) — present both options, let Bry choose**

**Option A: M5.13-3 Minimal Implementation**
- Build `_format_relationship_block` as specified
- Inject after mood_desc in both group and private paths
- ~40 lines, 1 file
- Single behavioral signal: trust band (陌生人/認識/熟悉/親密/深度信任)
- Reversible: remove the append to revert
- Add focused test (M5.13-3) for the projection

**Option B: Maintain Current Boundary (close P1 as INTENTIONAL for now)**
- Document that relationship LLM context is gated on Stage 4.3 LLM generation
- Wait for `feeling`/`impression` to have real producer code
- Re-evaluate when Stage 4.3 ships
- Current inner_life (diary/dream/event) provides character continuity

### Why both options are presented

Per Bry spec: "如果 audit 最終發現 relationship information 不足以提供穩定 behavioral signal，也可以得出『目前維持 intentional boundary』；不要為了填 P1 gap 而硬做。"

The available behavioral signal (`confidence` only) is:
- ✅ Real (continuously updated)
- ✅ Documented thresholds
- ⚠️ Single-dimensional
- ⚠️ No subjective stance available (`feeling`/`impression` always at default)

Whether this is "stable behavioral signal" depends on Bry's judgment. Both options are defensible.

### What M5.13-2 does NOT recommend

- ❌ Implementing `feeling`/`impression` projection (no producer code exists)
- ❌ Multi-target projection (other agents) (out of scope)
- ❌ Modifying frozen contracts
- ❌ Adding semantic search
- ❌ Adding LLM judge

---

## 14. Acceptance Criteria Verification

| # | Criterion | Status |
|---|-----------|--------|
| 1 | Field behavioral value analyzed | ✅ Section 1 |
| 2 | Fields to enter LLM identified (minimum set) | ✅ Section 2 |
| 3 | Fields excluded (privacy/persistence) | ✅ Section 3 |
| 4 | Context representation specified (deterministic text) | ✅ Section 4 |
| 5 | Threshold / bounded selection (0.3 cutoff) | ✅ Section 5 |
| 6 | Privacy / boundary defined | ✅ Section 6 |
| 7 | Injection point and precedence specified | ✅ Section 7 |
| 8 | Identity / provenance handled (per-agent, explicit target) | ✅ Section 8 |
| 9 | Frozen contract preserved | ✅ Section 9 |
| 10 | Minimal integration contract drafted | ✅ Section 10 |
| 11 | Alternative (maintain boundary) presented | ✅ Section 11 |
| 12 | Bry decision requested | ✅ Section 13 |

---

## 15. Bry Decision Required

**Question:** Which option for M5.13-3?

**Option A:** Implement minimal `_format_relationship_block` (40 lines, 1 file, confidence-only signal)
**Option B:** Maintain current boundary (close P1 as INTENTIONAL, wait for Stage 4.3)
**Option C:** Defer until Stage 4.3 ships (then re-evaluate with full feeling/impression signal)

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M5.13-2 |
| Mode | DESIGN AUDIT (READ-ONLY) |
| Baseline | `e940934` |
| Frozen contracts | 0 change |
| Audit scope | relationship field analysis, context projection, injection design |
| Files read | `src/soul/relationships.py`, `src/llm/proxy.py` (L246-700), `src/memory/middleware.py`, `logs/relationships_before_m0_4.json` (sample data) |
| Regression | 0 source changes (design only) |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
