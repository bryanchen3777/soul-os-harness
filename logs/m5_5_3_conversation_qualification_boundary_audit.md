# M5.5-3 — Conversation → InnerLifeEvent Qualification Boundary Audit

**Mode:** READ-ONLY AUDIT
**Baseline:** HEAD = f0b3e5c = origin/main
**Date:** 2026-08-10
**Recommendation:** **B. Minimal new qualification boundary required**

---

## 1. Executive Summary

This audit investigates the runtime architecture for promoting a meaningful conversation into a canonical InnerLifeEvent. The goal is NOT to implement such a promotion — the goal is to determine the minimal correct boundary where such a decision COULD be made without violating any frozen contract, polluting existing memory, or fabricating identity.

**Key findings:**

1. The current runtime has **rich session-level metadata** (session_id, correlation_id, SESSION_END, message history, timestamps, agent_id, source_pair) but **no conversation-level qualification layer**.
2. The Memory LLM Judge operates at **per-fact granularity** and is conceptually distinct from **per-conversation qualification**. Reusing it for InnerLife promotion would conflate two different decisions.
3. The 4 existing producers (Diary / Dream / Event / Proactive DM) all have **semantic/scheduled triggers** — they don't "decide" if a lived experience is meaningful; they are **the lived experience**. USER_MESSAGE qualification is fundamentally different: it's **recognition** that an experience was meaningful, not **triggering** one.
4. The natural qualification boundary is **session close** (SESSION_END, elapsed >= 30min, already published by Heartbeat engine).
5. Architecture **B (minimal new qualification boundary)** is the right fit. A new `ConversationQualification` layer is needed; existing components remain unchanged.

**Bry decision required:** YES — for the specific design of the new qualification layer (signals, threshold, LLM prompt, rollback strategy). The audit's recommendation B is clear, but the actual implementation design needs Bry's sign-off before any code is written.

---

## 2. Complete USER_MESSAGE Runtime Path

### 2.1 Ingestion (Channel → Bus)

```
Telegram / Web / WebSocket
    ↓
scripts/run_server.py (HTTP / WS endpoint)
    ↓
SoulEvent(
    event_type=EventType.USER_MESSAGE,
    source="user_bryan" | similar,
    priority=EventPriority.HIGH,
    payload={
        "text": str,                    # Bry's actual message
        "platform": "app"|"web"|"voice",
        "attachments": list[dict],
        "target_agent": "agent_yua"|etc, # for mode=private
        "mode": "private"|"group",
        "target_channel": "telegram"|...,
        "target_user_id": "bryan"|"1696287850"|...
    },
    session_id=f"session_{user_id}_{agent_id}",  # from LLMProxy._session_key (per user × agent)
    correlation_id=None,  # default, not set at ingestion
    inner_life_event_id=None  # M5.4-5.5 default — not set
)
```

### 2.2 Consciousness: routing + intent firing

```
MemoryMiddleware._on_user_message (middleware.py:226-237)
    ↓ (caches user_text for AGENT_SPEAK pairing)
    ↓ session_id → _pending_user_text[session_id] = text

consciousness._on_user_message (consciousness.py:171-218)
    ↓ (mode=private, target_agent=this.agent)
    ↓ chrono_payload = {"draft": content, "target_channel": ..., "target_user_id": ...}
    ↓
consciousness._fire_intent (consciousness.py:415-475)
    ↓
    # M5.4-6.2: inner_life_event_id extracted from chrono_payload
    # But for USER_MESSAGE path, chrono_payload doesn't have it
    # → _event_id = None
    ↓
SoulEvent(
    event_type=EventType.AGENT_INTENT,
    inner_life_event_id=None,  # ← KEY POINT: USER_MESSAGE path has no canonical eid
    correlation_id=event.event_id,  # USER_MESSAGE event_id becomes correlation root
    session_id=event.session_id,    # preserved
    payload={...draft, target_channel, target_user_id, reason="user_message"...}
)
```

**KEY AUDIT FINDING:** The USER_MESSAGE path does NOT call `InnerLifeWriter.create_event()`. Unlike M5.4-6.1/6.2/6.4 (Diary/Dream/Event/ProactiveDM), USER_MESSAGE has no canonical event creation. The 4 wired producers are all "scheduled/semantic triggers" — they ARE the lived experience. USER_MESSAGE is the OPPOSITE: the lived experience is the WHOLE CONVERSATION, and we need to RECOGNIZE that it was meaningful.

### 2.3 Memory prefetch + LLM response

```
MemoryMiddleware._on_agent_intent (middleware.py:254-310)
    ↓ (prefetch memories, build context)
    ↓
SoulEvent(AGENT_INTENT_ENRICHED, payload.memory_context=...)
    ↓
LLMProxy consumes AGENT_INTENT_ENRICHED
    ↓ (builds messages from history: data/conversations/{user}_{agent}_private.json)
    ↓ (calls LLM)
    ↓
SoulEvent(
    event_type=AGENT_SPEAK,
    inner_life_event_id=event.inner_life_event_id,  # None for USER_MESSAGE
    correlation_id=event.correlation_id or event.event_id,
    session_id=event.session_id,
    payload={"text": generated_text, "audio_text": ..., "emotion": ..., ...}
)
```

### 2.4 Memory write (per-fact qualification)

```
MemoryMiddleware._on_agent_speak (middleware.py:392-487)
    ↓ (5s throttling per agent)
    ↓ (pops user_text from _pending_user_text[session_id])
    ↓
provider.post_reply_commit(
    session_id, user_text, agent_text,
    source_pair=f"{target_user_id}:{agent_id}",
    inner_life_event_id=event.inner_life_event_id  # None for USER_MESSAGE
)
    ↓
SAGELiteProvider → MemoryWriter.write_turn → extract_and_write
    ↓
LLM Judge (3 categories × 3 discrete judgments)
    OR heuristic fallback
    ↓
For each qualified fact:
    inner_life_event_id = uuid.uuid4().hex  # synthetic, M5.4-5.2 backward compat
    ↓
_write_single:
    schema gate → predicate normalization → entity alignment
    → contradiction detection → dedup → SQL + v1 mirror
```

### 2.5 Session lifecycle

```
Heartbeat engine (heartbeat/engine.py:203)
    ↓ (every 60s, computes elapsed_mins from last user message)
    ↓ (elapsed_mins >= SESSION_END_THRESHOLD_MINS, default 30)
    ↓
SoulEvent(
    event_type=EventType.SESSION_END,
    source="heartbeat_engine",
    payload={"elapsed_mins": ...}
)
    ↓
consciousness._on_session_end (consciousness.py:386-)
    → computes EmotionalCarryover, persists to temporal store
    ↓
MemoryMiddleware does NOT subscribe to SESSION_END currently
```

**KEY ARCHITECTURAL FACT:** SESSION_END is the natural boundary for conversation-level decisions. Heartbeat engine already publishes it.

---

## 3. Current Qualification Mechanisms (Inventory)

| # | Layer | File | Mechanism | Decision granularity | Output |
|---|-------|------|-----------|----------------------|--------|
| 1 | Memory LLM Judge | `src/memory/llm_judge.py` | 3 categories × 3 discrete judgments (SUPPORTED/WEAK/UNSUPPORTED) | Per-fact triple | Filter list of triples |
| 2 | Memory heuristic fallback | `src/memory/sage/writer.py:_extract_facts` | Rule-based extraction | Per-fact triple | Filter list of triples |
| 3 | Entity alignment | `src/memory/sage/writer.py:_align_entity` | String similarity (0.75 threshold) | Per-fact subject/object | Canonical entity name |
| 4 | Predicate normalization | `src/memory/sage/writer.py:_PREDICATE_SYNONYMS` | Synonym map | Per-fact predicate | Normalized predicate |
| 5 | Contradiction detection | `src/memory/sage/writer.py:_find_contradiction` | Per-fact comparison | Per-fact vs existing | Conflict or not |
| 6 | Dedup detection | `src/memory/sage/writer.py:_find_similar` | Per-fact similarity | Per-fact vs existing | Merge or new |
| 7 | Weight threshold | `src/memory/sage/writer.py:ANCHOR_WEIGHT_THRESHOLD` | Numeric | Per-fact | Anchor or not |
| 8 | Source pair filter | `src/memory/sage/writer.py` | String match | Per-fact | Include or not |
| 9 | Memory 5s throttle | `src/memory/middleware.py:COMMIT_COOLDOWN_SECS` | Time-since-last per agent | Per-agent temporal | Skip or write |
| 10 | SpeakerTokenBus | `scripts/run_server.py:SpeakerTokenBus` | Numeric score | Per-AGENT_INTENT | Win or lose |
| 11 | Heartbeat SESSION_END | `src/heartbeat/engine.py:203` | elapsed_mins >= 30 | Per-session temporal | Publish SESSION_END |
| 12 | β2.1 event generator | `src/memory/middleware.py:_generate_event_description` | LLM call (pilot only) | Per-heartbeat, agent_akane only | Event text or None |
| 13 | Emotion engine | `src/temporal/core.py:emotion_engine` | Continuous state machine | Per-interaction | Mood/intimacy update |
| 14 | Memory fail-safe | `src/memory/v1/loader.py` | None-confidence filter | Per-Memory | Include or skip in prompt |

**All current qualification operates at PER-FACT or PER-AGENT level.** No mechanism currently exists for per-conversation qualification.

---

## 4. Memory-worthy vs InnerLife-worthy (Critical Distinction)

| Dimension | Memory fact | InnerLifeEvent |
|-----------|-------------|----------------|
| **Definition** | Atomic subject-predicate-object triple (e.g., "Bry likes apples") | Canonical lived-experience identity with provenance/lineage/correlation |
| **Time granularity** | Single turn (user message + agent response) | Multi-turn conversation, or scheduled slot (morning diary), or proactive DM |
| **Decision point** | Per-fact, post-LLM (after response generated) | Per-event, **pre-LLM for semantic triggers** (diary/dream/proactive) or **post-conversation for recognition** (USER_MESSAGE) |
| **Decision question** | "Is this triple a real fact about Bry?" | "Is this conversation/experience meaningful enough to become part of the agent's lived experience?" |
| **Output** | Filter list of triples (UNSUPPORTED dropped) | Yes/no for promotion; if yes, InnerLifeEvent with canonical event_id |
| **Longevity** | Long (LLM judge keeps qualified) | Long (canonical event_id cross-references Memory/Diary/Dream) |
| **Reusability** | One fact = one Fact entry | One event = N facts (Memory) + 0-1 diary + 0-1 dream + 0-1 event log |
| **Relationship** | A fact may be derived FROM an InnerLifeEvent (post-M5.5-2, carries inner_life_event_id reference) | An event is the IDENTITY layer above facts |

### Why these are different decisions

A single InnerLifeEvent may produce 0-10 memory facts. A trivial exchange may produce 0 facts. An emotionally heavy exchange may produce 10 facts. The COUNTS don't determine whether something was a meaningful experience.

A 1-line "what's for dinner" exchange:
- Probably 0 memory facts (LLM judge UNSUPPORTED)
- Definitely NOT an InnerLifeEvent (transactional, no lived experience)

A 30-min conversation about a relationship decision:
- Probably 5-10 memory facts (LLM judge SUPPORTED)
- MIGHT be an InnerLifeEvent (continuity + emotional weight + personal decision)

**Reusing Memory LLM Judge for InnerLife promotion is WRONG:**
- Wrong granularity (atomic triples vs whole conversations)
- Wrong decision point (per-fact post-LLM vs per-conversation mid/post-stream)
- Wrong output (filter list vs yes/no)
- Wrong LLM prompt (triple-evaluating vs conversation-evaluating)

**A separate qualification layer is needed** to evaluate conversations holistically.

---

## 5. Candidate Qualification Locations

### A. USER_MESSAGE ingestion

**Pros:**
- Earliest possible signal
- Can short-circuit trivial messages before they consume LLM tokens

**Cons:**
- Single message is NOT a conversation
- Can't see response yet
- Can't measure conversation depth
- Would need to block ingest on judgment (latency)
- One message = no continuity, no development, no emotional weight

**Verdict:** ❌ **NO.** Signal too thin. One message is never a lived experience.

### B. Conversation/session aggregation

**Pros:**
- Has full conversation view
- Can measure depth, continuity, emotional development
- Natural fit for SESSION_END trigger

**Cons:**
- Needs buffering / replay mechanism
- Complex to integrate with streaming AGENT_SPEAK
- Doesn't have LLM-quality signal at decision time
- Would need new component (ConversationQualification)

**Verdict:** ⚠️ **POSSIBLE but heavy.** This is the right SIGNAL but needs a dedicated component.

### C. Post-LLM response (per turn)

**Pros:**
- Has full user+assistant turn
- Can measure meaning per turn
- Can be done incrementally

**Cons:**
- Per-turn is too granular (one turn ≠ lived experience)
- Would need rolling buffer across multiple turns to detect continuity
- Introduces complex state per session
- Doesn't have natural stop signal

**Verdict:** ⚠️ **POSSIBLE but needs aggregation.** Could work if rolling buffer tracks multi-turn signals, but B (session-level) is cleaner.

### D. Memory qualification layer

**Pros:**
- Reuses existing LLM Judge infrastructure
- Already integrated

**Cons:**
- **Wrong granularity** (per-fact vs per-conversation)
- **Wrong output** (filter triples vs yes/no)
- **Wrong LLM prompt** (triple evaluator ≠ conversation evaluator)
- **Conflates Memory-worthy with InnerLife-worthy** (different concepts, must be evaluated separately per ticket requirement)

**Verdict:** ❌ **NO.** Conceptually wrong. The ticket explicitly says: "Do NOT automatically equate 'memory-worthy fact' with 'InnerLifeEvent-worthy experience'."

### E. InnerLifeWriter

**Pros:**
- Canonical identity authority (existing)
- Already creates events

**Cons:**
- **Semantics wrong.** "Writer" semantically = "create". InnerLifeWriter creates events; it doesn't decide whether to.
- Adding qualification logic to InnerLifeWriter would conflate "identity authority" with "promotion decision"
- Would require InnerLifeWriter to be aware of conversation history (cross-concern)

**Verdict:** ❌ **NO.** InnerLifeWriter should remain a pure identity authority. Qualification is a separate concern.

### F. Separate ConversationQualification layer

**Pros:**
- **Clean separation of concerns** (qualification ≠ identity creation)
- Reusable for other promotion decisions
- Explicit ownership
- Doesn't pollute existing components
- Can be wired at natural boundary (SESSION_END)
- Reuses existing session_id / correlation_id / message history

**Cons:**
- New infrastructure
- Needs its own signals (depth, duration, emotional markers, topic continuity)
- Needs to be wired between agent and InnerLifeWriter
- LLM prompt design is a new design problem

**Verdict:** ✅ **YES.** Best fit architecturally. New component, clean ownership, natural boundary.

---

## 6. Timing Decision

| Timing | Pros | Cons | Verdict |
|--------|------|------|---------|
| **Synchronous (per turn)** | Immediate; no buffering | Per turn ≠ lived experience; would create excessive events | ❌ |
| **Asynchronous (per turn, fire-and-forget)** | Doesn't block response | Same per-turn problem; no conversation view | ❌ |
| **At conversation/session close (SESSION_END)** | Has full conversation; natural boundary; already published by Heartbeat; 30min idle signal | Need to identify "which conversation just ended"; multi-agent sessions have one SESSION_END for all agents | ✅ |
| **At later consolidation (heartbeat / daily)** | Even more signal; can review multiple sessions | Late events; introduces async complexity; harder to test | ⚠️ Possible fallback |

**Recommendation:** Session close (SESSION_END) is the natural boundary. Heartbeat engine already publishes it. But:

- SESSION_END doesn't tell us WHICH conversations were meaningful vs trivial. That's exactly what qualification should answer.
- A 30-min session might be 30min of "hi" + "bye" (not meaningful) or 30min of deep conversation (meaningful). Qualification decides.

**Proposed architecture:**

```
USER_MESSAGE/AGENT_SPEAK stream (existing — no change)
    ↓
consciousness._on_user_message + _fire_intent → AGENT_INTENT (existing — no change)
    ↓
LLMProxy → AGENT_SPEAK (existing — no change)
    ↓
MemoryMiddleware → Memory facts (synthetic eid, M5.4-5.2) (existing — no change)
    ↓
SESSION_END (existing — Heartbeat, elapsed >= 30min)
    ↓
[NEW, NOT IN M5.5-3 SCOPE]
ConversationQualification (subscribes to SESSION_END)
    ↓ reviews session's turns (data/conversations/{user}_{agent}_private.json)
    ↓ applies qualification signals (depth, duration, emotional, topic, decision)
    ↓
    if "qualifies as meaningful lived experience":
        InnerLifeWriter.create_event(
            provenance=Provenance(
                trigger_type="conversation:user_message",  # NEW trigger_type
                actor_id=user_id,
                source_system="conversation",  # or "memory" or new "qualification"
                extras={"qualification_reason": ..., "turns": N, "duration_min": ...}
            ),
            session_id=...,
            correlation_id=USER_MESSAGE_correlation_root  # NOT fabricated
        )
    ↓
canonical InnerLifeEvent → trace.jsonl
    ↓
Memory subsequent facts reference the canonical event_id (M5.5-2 mechanism)
```

**Critical invariant:** InnerLifeWriter remains the ONLY creator of InnerLifeEvent. The qualification layer does NOT create events; it just decides whether to ASK the writer to create one.

---

## 7. Ownership Preservation

| Component | Role | Status |
|-----------|------|--------|
| **InnerLifeWriter** | Canonical identity authority (creates event_id, lineage, correlation) | **UNCHANGED** — remains sole creator |
| **Memory LLM Judge** | Decides if a fact triple is memory-worthy (per-fact) | **UNCHANGED** — operates on triples, not conversations |
| **Heartbeat engine** | Decides when session ends (elapsed >= 30min) | **UNCHANGED** — already publishes SESSION_END |
| **Memory** | Consumer of lived experience (M5.5-2 mechanism) | **UNCHANGED** — consumes canonical eid when present |
| **MemoryMiddleware** | Routing + 5s throttling | **UNCHANGED** |
| **[NEW] ConversationQualification** | Decides if session's conversation deserves promotion | **NEW** — proposed for M5.6 |

**No ownership conflict.** The proposed qualification layer:
- Does NOT create events (only InnerLifeWriter does)
- Does NOT replace Memory LLM Judge
- Does NOT touch InnerLifeWriter's canonical role
- Acts as a **gate**: "should this session become an InnerLifeEvent?"
- Calls InnerLifeWriter.create_event() with valid inputs (no fabricated identity)

---

## 8. Boundary Tests (Examples)

### Should NOT qualify (no InnerLifeEvent)

| Example | Why |
|---------|-----|
| "what should I eat for dinner?" | Single turn, transactional, no lived experience |
| "what's 2+2?" | Pure factual, no continuity |
| "can you set a timer for 5 minutes?" | One-off request, no narrative meaning |
| "the wifi isn't working" + "try restarting" | Short troubleshooting, no emotional weight |
| "what's the weather today?" | Pure factual lookup |
| "translate this to Japanese" | Mechanical task, no lived experience |
| "list 3 ideas for a side project" | Brainstorming, no commitment, no continuity |

**Pattern:** transactional / factual / single-turn / no emotional development / no personal stakes

### Should potentially qualify (might warrant InnerLifeEvent)

| Example | Why |
|---------|-----|
| 30-min conversation about relationship decision | Continuity + emotional weight + personal decision |
| Multi-turn discussion of career change | Continuity + important personal decision + future impact |
| Processing grief / loss over multiple turns | Emotional / psychological significance |
| Planning meaningful life event (wedding, moving) | Continuity + important personal event + stakes |
| Extended philosophical reflection on values | Continuity + narrative + meaning |
| Working through a creative block together | Continuity + personal investment + outcome |
| Reconciling after a misunderstanding | Emotional weight + personal stakes + closure |

**Pattern:** multi-turn / continuity / emotional weight / personal decision / narrative significance / closure

### Already qualified (existing InnerLifeEvents)

| Trigger | Source system | Producer | Why it qualifies automatically |
|---------|---------------|----------|-------------------------------|
| Morning diary slot | `diary` | DiaryHandler | Scheduled lived experience |
| Night diary slot | `diary` | DiaryHandler | Scheduled lived experience |
| Dream (scheduled) | `dream` | DreamHandler | Scheduled lived experience |
| Random event | `dream` | EventHandler | Semantic trigger (randomness) |
| Proactive DM | `narrative` | AgencyTriggerHandler | Agent-initiated lived experience |
| Agent reply (proactive_dm path only) | `narrative` | AgencyTriggerHandler | Threaded to proactive_dm canonical event |

**Key distinction:** The existing 6 producers are SCHEDULED or SEMANTIC — they have a known "lived experience" type. USER_MESSAGE qualification is fundamentally different because it's RECOGNITION — we have to RECOGNIZE that an experience was meaningful, not TRIGGER one.

---

## 9. Architecture Classification

**Recommendation: B. Minimal new qualification boundary required**

| Option | Verdict | Reason |
|--------|---------|--------|
| A. Existing architecture supports qualification | ❌ NO | Current architecture has per-fact qualification (Memory LLM Judge) but no per-conversation qualification. Per-fact ≠ per-conversation. |
| B. Minimal new qualification boundary required | ✅ **YES** | A new `ConversationQualification` layer is needed. Minimal because it only adds a decision component at SESSION_END — existing components unchanged. |
| C. Larger design decision required | ❌ NO | We don't need to redesign anything. The existing identity authority (InnerLifeWriter), session lifecycle (Heartbeat SESSION_END), and message history (data/conversations) provide all needed signals. |
| D. Frozen contract conflict | ❌ NO | No contracts are violated. The new layer is purely additive. |

**Bry decision required: YES** — for the specific design of the new qualification layer:
- What signals to evaluate (depth, duration, emotional markers, topic continuity, decision moments)?
- What LLM prompt to use (which model, which context, which output format)?
- What threshold to apply (when does a session "qualify")?
- How to handle Bry-only vs agent-only conversations (privacy considerations)?
- What to do on qualification failure (silent skip vs log)?
- How to integrate with M5.4-5.5 inner_life_event_id propagation (subsequent turns after promotion)?
- Rollback strategy if qualification produces too many / too few events?

---

## 10. Frozen Contract Verification

| Contract | File | Status |
|----------|------|--------|
| M5.3 Memory Retrieval | `src/memory/sage/writer.py` | UNCHANGED |
| SAGE / v1 schema | `src/memory/sage/models.py`, `src/memory/v1/schema.py` | UNCHANGED |
| Fact schema | `src/memory/sage/models.py:7-86` | UNCHANGED |
| InnerLifeEvent frozen model | `src/inner_life/event.py` | UNCHANGED |
| Provenance frozen model | `src/inner_life/event.py:68-115` | UNCHANGED (could add new `trigger_type` values additively in future) |
| SoulEvent schema | `src/eventbus/schema.py` | UNCHANGED (could add new event type additively in future, but not required) |
| Event Bus contract | `src/eventbus/*` | UNCHANGED |
| M5.4-6.x producer wiring | `src/agency/*`, `run_server.py` | UNCHANGED |
| NarrativeTraceWriter / Reader | `src/inner_life/trace.py`, `trace_reader.py` | UNCHANGED |
| Memory LLM Judge | `src/memory/llm_judge.py` | UNCHANGED |
| Heartbeat engine SESSION_END | `src/heartbeat/engine.py:203-` | UNCHANGED |
| `Fact.inner_life_event_id` semantics | M5.4-5.2 + M5.5-2 | UNCHANGED — still "reference to canonical InnerLifeEvent" |

**No frozen contract conflicts discovered.** The new layer is purely additive:
- Could add `trigger_type` values like `"conversation:user_message"` (additive to existing TRIGGER_TYPE_*)
- Could add new `source_system` value like `"conversation"` (additive to VALID_SOURCE_SYSTEMS, if needed)
- New Provenance.extras keys for qualification reasoning (extras is dict, no schema needed)

---

## 11. Production Integrity

- ✅ `data/memory/**` — 0 modification
- ✅ `data/soul/**/diary/**` — 0 modification
- ✅ `data/soul/**/dream/**` — 0 modification
- ✅ `data/soul/**/event/**` — 0 modification
- ✅ `data/inner_life/trace.jsonl` — 0 modification
- ✅ `data/conversations/**` — 0 modification
- ✅ No production data migration
- ✅ No historical backfill
- ✅ No new InnerLifeEvents created
- ✅ No trace records written
- ✅ Source code: 0 modification (this is a READ-ONLY audit)
- ✅ No new tests created (audit is documentation only)

---

## 12. Regression Results

Run before this audit, state preserved:

| Suite | Tests | Status |
|-------|-------|--------|
| M5.4-5.1 Inner Life Foundation | part of 348 | PASS |
| M5.4-5.2 Memory Inner Life Integration | part of 348 | PASS |
| M5.4-5.3 Diary Inner Life Integration | part of 348 | PASS |
| M5.4-5.4 Dream Inner Life Integration | part of 348 | PASS |
| M5.4-5.5 Event Bus Inner Life Integration | part of 348 | PASS |
| M5.4-5.6 Narrative Trace Sidecar | part of 348 | PASS |
| M5.4-5.7 Trace Reader | part of 348 | PASS |
| M5.4-6.1 Executor Wiring | part of 348 | PASS |
| M5.4-6.2 Proactive DM Inner Life Wiring | part of 348 | PASS |
| M5.4-6.3 Trace Production Activation Audit | part of 348 | PASS |
| M5.4-6.4 Trace Production Activation | part of 348 | PASS |
| M5.5-2 Canonical InnerLifeEvent Propagation | part of 348 | PASS |
| M3 E2E + World Awareness | 29/29 | PASS |
| WebSocket E2E | 2/2 | PASS |
| **Total** | **348/348** | **PASS** |

(Re-running full regression unnecessary — no source code modified, working tree clean.)

Pre-existing failures unchanged (NOT caused by this audit, confirmed on baseline f0b3e5c):
- `tests/test_memory_middleware.py::test_memory_middleware_e2e` — fixture `tmp_dir` not found
- `tests/test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_1_*` and `test_s2_a_5_*` — Windows cp950 console encoding

---

## 13. Architectural Findings

### 13.1 Existing architecture is well-positioned but incomplete

The Soul OS has:
- ✅ Canonical identity authority (InnerLifeWriter)
- ✅ Session lifecycle signals (SESSION_END from Heartbeat)
- ✅ Message history persistence (data/conversations/{user}_{agent}_private.json)
- ✅ Per-fact qualification (Memory LLM Judge)
- ✅ Existing canonical event threading (M5.5-2 for proactive_dm)

But lacks:
- ❌ Per-conversation qualification (no mechanism for "is this conversation meaningful?")
- ❌ Session-end consolidation hook (no subscriber for SESSION_END that aggregates turns)
- ❌ Recognition-as-promotion pattern (existing producers are triggers, not recognition)

### 13.2 The 6 wired InnerLifeEvent producers are all "triggers", not "recognition"

Diary/Dream/Event/ProactiveDM/AgentReply-via-ProactiveDM are all **scheduled or semantic triggers**. They say: "a lived experience just happened at this time, with this type". They don't need to "decide" if it's meaningful — that's built into the trigger type.

USER_MESSAGE qualification is fundamentally different: it's **"did the conversation that just happened constitute a meaningful lived experience?"** This is a RECOGNITION problem, not a TRIGGERING problem. A new component is needed.

### 13.3 Per-fact ≠ per-conversation: do not conflate

The Memory LLM Judge's per-fact SUPPORTED/WEAK/UNSUPPORTED decision is for a specific triple. A conversation may have 0 SUPPORTED triples (trivial Q&A) or 10 SUPPORTED triples (rich conversation). Neither count determines whether the conversation was a meaningful lived experience.

**A separate qualification layer is needed** that evaluates the conversation as a whole, not the triples within it.

### 13.4 Session close is the natural boundary

The Heartbeat engine already publishes SESSION_END when elapsed_mins >= 30. This is:
- ✅ A natural temporal boundary
- ✅ A single point of decision (not per-turn)
- ✅ Aligned with existing session lifecycle
- ✅ Reuses existing session_id / correlation_id

Subscribing a new ConversationQualification layer to SESSION_END is the right integration.

### 13.5 InnerLifeWriter remains sole identity authority

The new qualification layer:
- Decides yes/no (does this session qualify?)
- If yes, calls `InnerLifeWriter.create_event()` with valid provenance/session/correlation
- If no, does nothing (no event created)

**No second identity system.** No fabrication. The qualification layer is a gate, not an authority.

### 13.6 Privacy considerations (out of scope for this audit)

A real implementation should consider:
- Bry-only vs Bry+agent vs multi-agent conversations
- Whether to qualify conversations where agent gave factual advice only
- Whether to qualify conversations where Bry was emotionally distressed (may want to always qualify)

These are design decisions Bry should make. Out of scope for M5.5-3.

---

## 14. Stop Conditions Final Check

| Stop Condition | Triggered? | Notes |
|----------------|-----------|-------|
| 1. Frozen contract conflict discovered | NO | All contracts preserved |
| 2. Existing architecture cannot support qualification without major redesign | NO | B fits, no redesign needed |
| 3. Qualification requires fabricating session/correlation/lineage identity | NO | SESSION_END + existing session_id provide real signals |
| 4. Production data would need migration | NO | New layer is additive, no migration |
| 5. Memory and Inner Life ownership cannot remain separate | NO | Clean separation (per-fact vs per-conversation) |
| 6. Multiple materially different architecture choices require Owner decision | **YES** | Bry decision needed for qualification signals/prompt/threshold design |

**Stop condition triggered: #6** — Bry decision required for the new qualification layer's specific design (signals, prompt, threshold, rollback).

---

## 15. Acceptance Criteria Status

| Criterion | Status | Notes |
|-----------|--------|-------|
| Complete USER_MESSAGE runtime path documented | ✅ | Section 2 (5 stages: ingestion → consciousness → memory → LLM → session end) |
| Conversation/session boundary documented | ✅ | Section 2.5 (SESSION_END, Heartbeat) |
| Existing qualification mechanisms documented | ✅ | Section 3 (14 mechanisms inventoried) |
| Difference between Memory-worthy and InnerLife-worthy explicitly established | ✅ | Section 4 (7 dimensions compared) |
| Candidate qualification boundaries evaluated | ✅ | Section 5 (A-F evaluated, F chosen) |
| Ownership of InnerLifeEvent creation preserved | ✅ | Section 7 (InnerLifeWriter remains sole creator) |
| No speculative infrastructure introduced | ✅ | Section 11 (0 source code modification) |
| No source behavior modified | ✅ | Section 11 + 12 |
| No USER_MESSAGE InnerLifeEvents created | ✅ | Section 11 (no events created) |
| No production data modified | ✅ | Section 11 |
| Frozen contracts unchanged | ✅ | Section 10 (12 contracts verified) |
| Recommendation classified A/B/C/D | ✅ **B** | Section 9 |
| Future implementation acceptance contract proposed | ✅ | Section 16 |

**All acceptance criteria met. ✅**

---

## 16. Proposed Future Implementation Acceptance Contract

**M5.6 (if Bry approves) — ConversationQualification Layer (MINIMAL IMPLEMENTATION)**

### Scope

1. New component: `src/conversation_qualification/qualifier.py` (or similar location)
2. Subscribes to `EventType.SESSION_END`
3. Reads session's conversation history from `data/conversations/{user}_{agent}_private.json`
4. Applies qualification signals (TBD by Bry):
   - Signal 1: Conversation depth (turn count >= N?)
   - Signal 2: Conversation duration (>= M minutes?)
   - Signal 3: Topic continuity / progression
   - Signal 4: Emotional markers (LLM or rule-based)
   - Signal 5: Decision moments
   - Signal 6: Closure / resolution
5. Returns yes/no decision
6. If yes: calls `InnerLifeWriter.create_event()` with `Provenance(trigger_type="conversation:user_message", ...)` and session_id, correlation_id from session
7. If no: returns without action

### Acceptance criteria (for M5.6 implementation)

[ ] ConversationQualification subscribes to SESSION_END correctly
[ ] Subscribes per (user, agent) — each session evaluated independently
[ ] Reads conversation history without modification (READ-ONLY on data/conversations/)
[ ] Returns yes/no in <1s (no LLM blocking) or <5s (with LLM)
[ ] If yes: calls InnerLifeWriter.create_event() with:
  - valid Provenance (trigger_type, actor_id, source_system)
  - valid session_id (from session metadata)
  - valid correlation_id (correlation root from first USER_MESSAGE)
  - NO parent_event_id (root event, lineage_depth=0)
[ ] If no: returns None, no event created
[ ] Per Bry signals, applies them deterministically
[ ] Logs all decisions (qualified yes/no + reason) for observability
[ ] Failure isolation: if qualification fails (LLM timeout etc.), no event created, log warning
[ ] No production data modified
[ ] Memory remains unchanged (still uses synthetic eid for non-qualified sessions)

### Test scenarios

| Test | Input | Expected output |
|------|-------|-----------------|
| `test_trivial_qa_no_qualify` | "what's 2+2?" + "4" | No event created |
| `test_dinner_recommendation_no_qualify` | "what should I eat for dinner?" + "..." | No event created |
| `test_short_troubleshooting_no_qualify` | "wifi broken" + "restart router" + "ok thanks" | No event created |
| `test_extended_emotional_conversation_qualify` | 15-turn discussion about relationship decision | Event created with emotional reasoning |
| `test_decision_conversation_qualify` | "Should I take the job?" multi-turn with pros/cons | Event created with decision reasoning |
| `test_short_response_to_long_message_no_qualify` | User writes 500 words, agent says 1 line | No event created (response doesn't engage) |
| `test_empty_session_no_qualify` | SESSION_END with no conversation history | No event created |
| `test_no_inner_life_writer_fail_isolated` | InnerLifeWriter.create_event raises | No crash, log warning |
| `test_memory_unaffected_by_qualification` | Session doesn't qualify → Memory still has synthetic eid (M5.4-5.2) | Memory unchanged |
| `test_correlation_root_extracted_from_user_message` | First USER_MESSAGE event_id becomes correlation_id | Matches expected format |

### Out of scope (for M5.6)

- Multi-session consolidation (only SESSION_END per session)
- Cross-session narrative reconstruction
- LLM call → InnerLifeEvent (not USER_MESSAGE triggered)
- Trace → Memory direct ingestion
- Memory scoring changes
- New embeddings / vector DB
- New persistence layer
- New schema (only additive Provenance extras / trigger_type)
- Heartbeat restoration
- Unrelated refactor

### Frozen contracts preserved

All M5.3 / M5.4-5.1 / M5.4-5.5 / M5.4-5.6 / M5.4-5.7 / M5.4-6.x / M5.5-2 / M3 contracts UNCHANGED.

---

## 17. Recommended Next Ticket

### Option 1: M5.6 — ConversationQualification Layer (BRY DECISION GATE FIRST)

**Mode:** BRY DECISION GATE → DESIGN (read-only) → Bry APPROVAL → IMPLEMENTATION

**Why:** The implementation design needs Bry's approval on signals, LLM prompt, threshold before any code. A separate Bry decision gate prevents "let me just implement something and Bry can review later" anti-pattern.

**Sub-tickets:**
- M5.6.0: Bry decision packet (this audit + 2-3 design alternatives, Bry picks one)
- M5.6.1: Design (READ-ONLY, no source modification, defines signals/prompt/rollback)
- M5.6.2: Implementation (after Bry approval)

### Option 2: Defer (no ticket)

If Bry decides conversation qualification is premature:
- M5.5-3 audit captures findings
- Future code can reference this audit
- M5.5 chain complete (M5.5-1 audit → M5.5-2 implementation → M5.5-3 audit)

### Option 3: Re-audit

If Bry disagrees with the recommendation B (e.g., wants option F merged into M5.5-2, or wants A re-examined):
- M5.5-3a: Re-audit with different scope

**Recommendation:** Awaiting Bry direction. No ticket auto-opened.

---

## 18. Final Status

| Item | Status |
|------|--------|
| Audit complete | ✅ |
| Recommendation classified | ✅ **B** |
| Frozen contracts verified | ✅ (all UNCHANGED) |
| Production integrity verified | ✅ (0 modification) |
| Regression verified | ✅ (348/348 PASS) |
| Architectural findings documented | ✅ (Section 13, 6 findings) |
| Bry decision required | ✅ YES (for M5.6 design specifics) |
| Recommended next ticket | Section 17 (3 options) |
| No speculative infrastructure | ✅ |
| No USER_MESSAGE InnerLifeEvents created | ✅ (read-only audit) |
| All acceptance criteria met | ✅ (Section 15) |
