# M5.13-1 — Lived Context Capability Preflight

**Mode:** STRICT READ-ONLY ARCHITECTURE AUDIT
**Baseline:** `48c3063` (M5.12-1)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT

---

## Executive Summary

| Layer | Reaches LLM Prompt? | Path | Status |
|-------|---------------------|------|--------|
| **Physical (World)** | ✅ Yes | WorldPerceptionMiddleware → world_context (text) | Mostly synthetic; real-world APIs NOT called |
| **Physical (Temporal)** | ✅ Yes | HeartbeatEngine → chrono-social block | M5.11-2 documented active |
| **Information (Memory)** | ✅ Yes | MemoryMiddleware → memory_context (text) | M5.10-2 wired |
| **Information (Diary/Dream)** | ✅ Yes | proxy._format_recent_inner_life → inner_life (text) | M2.0 wired; placeholder filtered |
| **Information (InnerLifeEvent trace)** | ❌ No | trace.jsonl — read by NarrativeTraceReader for gate, not LLM | Intentional (gate only) |
| **Social (Relationships)** | ❌ **No** | RelationshipsStore write active; read APIs exist; 0 LLM consumers | **P1 GAP** |
| **Personal (EmotionalCarryover)** | ✅ Indirectly | HeartbeatEngine → chrono-social block | M5.11-2 documented active |

**Soul Context Composition Classification: C — Partial Composition**

The LLM prompt has 7+ independent context blocks (system_prompt, memory_context, mood, inner_life, world_context, temporal, bry_block, history). They are appended sequentially with no unified provenance/identity metadata, and the **Social layer (relationships) is entirely missing**.

**Recommendation: B — M5.13-2 Design Audit (relationships-in-prompt)**

The architecture is mostly coherent. There is one P1 architectural gap (relationships not in LLM prompt) that warrants a design audit before any implementation.

---

## 1. Physical Context Trace

### Data sources

| Source | Type | Wired? | Reaches LLM? |
|--------|------|--------|--------------|
| `SyntheticWorldEventSource` | Synthetic events for testing | Optional (env var) | ✅ via WorldPerceptionMiddleware |
| `RealWorldEventSource` | Real-world APIs | **NOT called in production** | N/A |
| `WorldEvent` payload | event bus transport | ✅ | ✅ → world_context string |
| `WorldPerceptionMiddleware` | state + trace + score | ✅ default ON | ✅ → world_context string |
| `HeartbeatEngine` | temporal observation | ✅ | ✅ → chrono-social block |
| `PersonaConfig` | per-persona timing thresholds | ✅ | ✅ via build_temporal_context |

### Full Physical → LLM flow

```
Real-world APIs (NOT CALLED)
  ↓
WorldEventSource ABC (synthetic only in production tests)
  ↓
WorldPerceptionMiddleware.process_world_event_direct() [run_server.py L333]
  │ M3.1 Phase A — deterministic
  │ M3.1 Phase B — accept_threshold
  │ M3.1 validation
  │ M3.1 perception state
  │ M3.1 trace writer
  │
  └→ AGENT_INTENT_ENRICHED → AGENT_INTENT_PERCEIVED
       payload["world_context"] = format_world_context_block(state.world_context)
  │
  └→ LLMProxy._build_messages_group()
       L386: if world_context.strip(): system_parts.append(world_context + "\n")
```

### What is in physical context

- world_context (text): `format_world_context_block` renders:
  - current physical environment state (latest events)
  - event triggers (e.g., "calendar_event", "user_going_outside")
  - what changed recently in physical layer
- chrono-social block:
  - time_period (morning/afternoon/evening/night/deep_night/dawn)
  - silence_hours
  - attachment_heat (from EmotionalCarryover)
  - vulnerability_window
  - deviation_interpretation
  - preoccupation_flavor

### What is NOT in physical context (and why)

- World state detail (only surface-level world_context): WorldPerception state has more data than world_context exposes
- WorldPerception state itself (raw, not text): only rendered to text
- WorldEventSource raw payloads: only `novelty_id`, `type`, `summary` reach LLM
- Real-world APIs: explicitly NOT called (per M5.8-1 architecture)

### Synthetic vs Real-world

**Currently:** All production physical events come from `SyntheticWorldEventSource` (per M3.1 architecture). Real-world API integration is **out of scope** per M5.8-1, but the protocol is defined (`WorldEventSource` ABC) for future extension.

---

## 2. Information Context Trace

### Data sources

| Source | Type | Wired? | Reaches LLM? |
|--------|------|--------|--------------|
| `SAGELiteProvider` (memory.db) | v1 memory facts | ✅ | ✅ via MemoryMiddleware |
| `MemoryMiddleware` | context prefetch | ✅ | ✅ → memory_context string |
| `MemoryReader.retrieve_context` | semantic retrieval | ✅ | ✅ within MemoryMiddleware |
| `LLM Judge` | fact extraction | ✅ | ❌ does not reach LLM (judges IN, not OUT) |
| `Diary jsonl` | subjective reflection | ✅ | ✅ via _format_recent_inner_life |
| `Dream jsonl` | dream record | ✅ | ✅ via _format_recent_inner_life |
| `Event jsonl` | event record | ✅ | ✅ via _format_recent_inner_life |
| `InnerLifeEvent trace.jsonl` | canonical inner life | ✅ | ❌ reaches M5.8-4 gate only |
| `NarrativeTraceReader` | trace query | ✅ | ❌ reaches M5.8-4 gate only |

### Full Information → LLM flow

```
USER_MESSAGE / AGENT_SPEAK events
  │
  ├→ MemoryMiddleware (existing, M-series)
  │    ├─ USER_MESSAGE: stores user_text to (session_id, agent_id) cache
  │    └─ AGENT_SPEAK: post_reply_commit → memory.db (M-series GraphStore)
  │
  └→ AGENT_INTENT event
       ↓ MemoryMiddleware handles
       ├─ _on_agent_intent (L254)
       │    prefetch memory via MemoryReader.retrieve_context()
       │    event.payload["memory_context"] = context.summary
       │
       └→ re-publish as AGENT_INTENT_ENRICHED
            payload: { memory_context: "...", ... }

Memory.db (SAGE GraphStore, SQLite)
  ↓
MemoryReader.retrieve_context(query, top_k, max_hops, max_tokens, mode)
  ↓
MemoryMiddleware prefetch → memory_context string
  ↓
LLMProxy._build_messages_group() L357-358
     if memory_context.strip():
         system_parts.append(f"\n你記得以下這些事情:\n{memory_context.strip()}")
```

### Diary/Dream/Event flow

```
DiaryWriter.write_diary() (run by DiaryHandler executor)
  ↓
data/soul/{agent_id}/diary/{date}.jsonl (append)
  ↓
LLMProxy._format_recent_inner_life(agent_id) [L246-285]
  - reads 3 days of diary/dream/event jsonl
  - filters slot in (morning, night, dream, event)
  - filters source == "llm" (placeholder excluded)
  - truncates each entry to INNER_LIFE_MAX_CHARS_PER_ENTRY
  - returns up to INNER_LIFE_MAX_ENTRIES lines
  ↓
LLMProxy._build_messages_group() L369-376
     if inner_life:
         system_parts.append(f"\n[最近內在生活] ...\n{inner_life}\n")
```

### What is in information context

- **v1 memory facts**: meaningful conversation memory, source_pair labeled, "you remember" prompt
- **Inner Life (diary/dream/event content)**: subjective content rendered with date+slot prefix
- **Conversation history**: recent turns (user/assistant)
- **Bry's recent user messages** (bry_block): cross-session context for proactive

### What is NOT in information context

- **InnerLifeEvent trace.jsonl raw data**: trace exists for M5.8-4 gate; diary content reaches LLM but the canonical event with provenance doesn't
- **LLM Judge output**: judges facts from new text, doesn't pass anything back to LLM
- **Memory evolution state**: fact aging, decay — not in prompt
- **Memory top_k specifics**: only the summary, not retrieval details

### Duplication analysis

- **memory vs diary**: 
  - memory = objective facts ("Bry said X") — M5.10-3 ruled out
  - diary = subjective reflection ("I felt Y when Bry said X") — M2.0 included
  - M5.10-3: No duplication; intentional separation
- **memory vs inner_life_event trace**: 
  - memory.db = conversation facts
  - trace.jsonl = canonical identity of inner life activity
  - No duplication; different granularity and purpose

### Isolated contexts

- **LLM Judge state**: judges per-event, doesn't maintain cross-event state
- **Memory evolution**: handles decay/aging internally, doesn't surface to LLM

---

## 3. Social Context Trace

### Data sources

| Source | Type | Wired? | Reaches LLM? |
|--------|------|--------|--------------|
| `RelationshipsStore.write` (MemoryMiddleware) | write on USER_MESSAGE/AGENT_SPEAK/dream/event | ✅ | N/A (write) |
| `RelationshipsStore.get()` | read API | defined | ❌ 0 production consumers |
| `RelationshipsStore.get_all()` | read API | defined | ❌ 0 production consumers |
| `MultiAgentRelationshipsManager.get_store()` | read API | defined | ❌ 0 production consumers |
| `confidence` / `feeling` / `impression` | data | written | ❌ 0 LLM consumers |

### Social → LLM flow

```
USER_MESSAGE event
  ↓
MemoryMiddleware._on_user_message()
  ├─ _relationships_manager.on_user_message(target_agent_id, user_id)
  │    → store.touch(BRYAN_ENTITY_ID, delta=+0.05)
  │       → relationships.json (write)
  │       → confidence += 0.05
  │       → feeling (default "neutral")
  │
  └─ (read path: NONE — write only)

AGENT_SPEAK event
  ↓
MemoryMiddleware._on_agent_speak()
  └─ _relationships_manager.on_agent_speak(speaker, session_agents)
     → store.touch(other_id, delta=+0.02) per other
     → relationships.json (write)

LLMProxy._build_messages_group()
  L0-700: NO reference to RelationshipsStore, MultiAgentRelationshipsManager,
          or any relationship data
  ❌ Social context does NOT reach LLM prompt
```

### **This is the major P1 gap.**

M5.11-2 documented P2.4 as "intentional boundary" because **read APIs are defined but 0 production consumers**. But the consequence is: the LLM has zero awareness of:
- How confident the character is with the user (Bry's relationship with agent)
- How the character feels about the user
- The character's impression of the user
- The character's relationship with other characters in the same session

**This is a real LLM-context gap, distinct from the P2.4 read-API infrastructure gap.**

The P2.4 closure is correct: the read APIs are intentionally not called by anyone. But M5.11-2 didn't address whether the LLM should see this data. That's a different question.

### Why relationships might be intentionally excluded

- Privacy: relationships contain subjective impressions ("Bry is annoying sometimes")
- Persona risk: explicit relationship labels could bias LLM into fixed character
- Volatility: confidence changes every interaction; including it could be noisy
- Spec gap: no design spec for "what should LLM see about relationships?"

### Why relationships should be included

- LLM generates responses about Bry without knowing how the character feels about Bry
- LLM cannot maintain coherent character relationships across turns
- "Memory" includes conversation facts but not "how I feel about Bry"
- Emotional continuity is broken

---

## 4. Personal / Inner-Life Context Trace

### Data sources

| Source | Type | Wired? | Reaches LLM? |
|--------|------|--------|--------------|
| `DiaryWriter.write_diary()` | subjective reflection | ✅ | ✅ via _format_recent_inner_life |
| `DreamWriter.write_dream()` | dream record | ✅ | ✅ via _format_recent_inner_life |
| `EventWriter.write_event()` | event record | ✅ | ✅ via _format_recent_inner_life |
| `InnerLifeWriter.create_event()` | canonical InnerLifeEvent | ✅ | ❌ (diary content reaches LLM, not the event itself) |
| `NarrativeTraceWriter` | trace.jsonl | ✅ | ❌ (M5.8-4 gate only) |
| `EmotionalCarryover` | per-agent state | ✅ | ✅ via chrono-social block |
| `consciousness.state (intimacy/mood/dependency)` | per-agent state | ✅ | ✅ via mood description + _should_speak logic |

### Inner Life → LLM flow

```
consciousness._on_session_end()
  ├─ carryover = EmotionalCarryover(
  │     intimacy_afterglow, unresolved_worry,
  │     emocional_openness_residue, attachment_heat
  │  )
  ├─ carryover.save(agent_id) → carryover.json (persistence)
  │
  └─ HeartbeatEngine.start()
       ├─ for agent_id in _agent_ids:
       │    carryover = EmotionalCarryover.load(agent_id, data_dir).apply_decay(0)
       │    self._carryovers[agent_id] = carryover
       │
       └─ _loop():
            carryover = self._carryovers.get(primary_agent, ...)
            chrono_ctx = build_temporal_context(..., carryover=carryover, ...)
            tick = SoulEvent(SYSTEM_TICK, payload={
                "attachment_heat": chrono_ctx.carryover.attachment_heat,
                "chrono_block": render_temporal_block(chrono_ctx),
                ...
            })
            bus.publish(tick)

DiaryWriter.write_diary()  [run by DiaryHandler executor]
  ├─ LLM call (subjective reflection generation)
  ├─ write to data/soul/{agent_id}/diary/{date}.jsonl
  │    source="llm" (real) or "placeholder" (stub)
  │
  └─ (read path) LLMProxy._format_recent_inner_life()
       └─ filters source=="llm" (Bry 8/7 16:46 拍板)
       └─ returns up to INNER_LIFE_MAX_ENTRIES lines
       └─ LLMProxy._build_messages_group() L369-376
            if inner_life:
                system_parts.append(f"\n[最近內在生活] ...\n{inner_life}\n")
```

### What is in Personal/Inner-Life context

- Diary content (3 days, morning/night)
- Dream content (3 days)
- Event content (3 days)
- EmotionalCarryover (via chrono-social block: attachment_heat, unresolved_worry, etc.)
- mood (intimacy_level, dependency, mood state)
- chrono-social (time_period, silence_hours, vulnerability_window)

### What is NOT in Personal/Inner-Life context

- Raw InnerLifeEvent trace (canonical identity, provenance, trigger_type)
- NarrativeTrace event sequence
- consciousness.state raw fields (only rendered descriptions)
- Carryover history (only current + decay)

### Active runtime inputs vs persistence-only vs intentionally isolated

| Component | Active runtime | Persistence | Prompt | Agency | Isolated |
|-----------|---------------|-------------|--------|--------|----------|
| Diary (3d) | ✅ | ✅ | ✅ | ❌ | - |
| Dream (3d) | ✅ | ✅ | ✅ | ❌ | - |
| Event (3d) | ✅ | ✅ | ✅ | ❌ | - |
| InnerLifeEvent trace | ✅ (gate) | ✅ | ❌ | ❌ | prompt-isolated |
| EmotionalCarryover | ✅ (chrono) | ✅ (carryover.json) | ✅ (chrono block) | ❌ | - |
| NarrativeTraceReader | ✅ (M5.8-4 gate) | ✅ | ❌ | ❌ | prompt-isolated |

---

## 5. Soul Context Composition Finding

### Canonical composition point

**File:** `src/llm/proxy.py`
**Functions:** `_build_messages_group` (L316-450) and `_build_messages_private` (L600-700)
**Method:** Sequential `system_parts.append()` of 7+ independent context blocks

### Composition sequence

```python
system_parts = []
# 1. system_prompt (persona)
system_parts.append(identity_anchor + soul.strip())

# 2. memory_context (v1 memory facts)
if memory_context.strip():
    system_parts.append(f"\n你記得以下這些事情:\n{memory_context.strip()}")

# 3. mood (Phase 3 emotion engine)
mood_desc = emotion_engine.mood_description(mood)
if mood_desc:
    system_parts.append(f"\n[情緒狀態] {mood_desc}")

# 4. inner_life (diary/dream/event 3-day content)
inner_life = _format_recent_inner_life(agent_id)
if inner_life:
    system_parts.append(f"\n[最近內在生活] ...\n{inner_life}\n")

# 5. world_context (WorldPerception text)
if world_context.strip():
    system_parts.append(world_context.strip() + "\n")

# 6. temporal (current time + chrono-social + silence)
if current_time:
    system_parts.append(f"\n## 當下時間\n{current_time}\n{_format_temporal_context(event_ts)}")

# 7. bry_block (Bry's recent user messages)
if bry_block:
    system_parts.append(bry_block)

# 8. conversation_history (recent turns)
# (inserted as separate messages array, not system_parts)
```

### Classification: **C — Partial Composition**

The 7+ context blocks are independently constructed by separate subsystems (MemoryMiddleware, WorldPerceptionMiddleware, HeartbeatEngine, consciousness, proxy.py itself) and concatenated sequentially. There is **no unified Soul Context object** with provenance, freshness, or identity metadata.

**Strengths:**
- Each block is owned by a clear subsystem
- Failures are isolated (one missing block doesn't break others)
- Frozen contracts preserved (no new infrastructure)
- Bounded sizes (truncation, max entries, max tokens)

**Weaknesses:**
- No provenance: LLM doesn't know which block is from where
- No freshness: stale inner_life (3-day) mixed with current world_context
- No identity continuity: agent_id implicit, no per-block identity
- No unified structure: each block has different format
- **Social layer entirely missing**

---

## 6. Three End-to-End Scenarios

### Scenario A: World event occurs (e.g., "calendar_event: Bry has dentist appointment at 3pm")

```
1. SOURCE: WorldEventSource
   - SyntheticWorldEventSource.build_calendar_event() (production test)
   - OR RealWorldEventSource (not implemented in production)

2. WORLD PROCESSING:
   WorldPerceptionMiddleware.process_world_event_direct(world_event)
   ├─ validation (accept threshold)
   ├─ state update (last_event, novelty_id)
   ├─ trace write
   └─ no bus.publish (this is direct call)

3. INNER LIFE QUALIFICATION (M5.9-3.1):
   WorldInnerLifeAdapter subscribes to WORLD_EVENT on bus
   ├─ qualify_world_event(world_event) [type whitelist: calendar_event, user_going_outside]
   ├─ if YES: dedup check (in-memory dict, no persistent dedup)
   ├─ if not duplicate: inner_life_writer.create_event(...)
   │    - provenance.trigger_type = "world:calendar_event"
   │    - provenance.actor_id = None (per M5.9-3 identity spec)
   │    - provenance.source_system = "narrative"
   │    - writes to data/inner_life/{agent_id}/events/{event_id}.json
   │    - appends to trace.jsonl
   └─ NOT published to bus (inner_life is closed-world)

4. NEXT PROACTIVE TRIGGER:
   SoulScheduler._fire_proactive_dm()
   ├─ _inner_life_gate_check(agent_id) [M5.8-4]
   │    - reads trace.jsonl via NarrativeTraceReader
   │    - finds the world:calendar_event InnerLifeEvent
   │    - if elapsed < 30 min → GATED (suppress)
   │    - if elapsed >= 30 min → EMITTED
   │
   └─ [EMITTED]: _bus.publish(AGENCY_TRIGGER)
        → AgencyTriggerHandler → run_agency → decision=YES
        → _proactive_dm_llm_executor
          → inner_life_writer.create_event() [M5.4-6.2]
          → _agent._fire_intent() → LLM call
              └→ memory_reader available for context
              └→ world_context available (from WorldPerception)
              └→ world_context contains: "calendar_event: Bry has dentist appointment at 3pm"
              → LLM sees world context in prompt

5. LLM EXPRESSION:
   LLMProxy._build_messages_group()
   L385-386: system_parts.append(world_context + "\n")
   LLM sees: "There is a calendar event: Bry has dentist appointment at 3pm"

6. AGENT_SPEAK:
   bus.publish(AGENT_SPEAK)
   └─ MemoryMiddleware._on_agent_speak()
        └─ _relationships_manager.on_agent_speak()
             └─ store.touch(other_id, delta=+0.02)
             (relationship data written, NOT read)

7. RESULTING MEMORY:
   MemoryMiddleware._on_agent_speak() (post_reply_commit)
   └─ writer.post_reply_commit() → memory.db
   └─ LLM Judge evaluates: "Did agent mention dentist appointment?"
   └─ MemoryReader (M5.10-2) provides v1 context to Judge
   └─ fact stored in memory.db

8. NEXT CYCLE:
   next USER_MESSAGE / AGENT_INTENT
   └─ MemoryMiddleware prefetch
   └─ memory_context includes the new fact: "agent mentioned dentist appointment"
   └─ LLM sees the fact in next turn
```

**Information loss points:**
- WorldEvent raw payload details (only `summary` + `type` reach LLM)
- WorldPerception state internals (only text rendered)
- InnerLifeEvent canonical identity (provenance, event_id) — not in LLM prompt
- Trace timeline — not in LLM prompt

### Scenario B: User conversation contains a meaningful fact

```
1. SOURCE: User sends USER_MESSAGE
   - gateway ingestion → SoulEvent(USER_MESSAGE, payload={content, target_agent, ...})

2. MEMORY PROCESSING (M-series):
   MemoryMiddleware._on_user_message(event)
   ├─ caches user_text to (session_id, agent_id)
   ├─ _relationships_manager.on_user_message(target_agent, BRYAN_ENTITY_ID)
   │    └─ store.touch(BRYAN_ENTITY_ID, delta=+0.05)
   │       (relationship data: confidence +0.05, feeling="neutral")
   │
   └─ consciousness._on_user_message(event)
        └─ emotion_engine.update(mood_delta=+0.08, intimacy_delta=0.3)
        └─ _fire_intent(reason="user_message", chrono_payload={"draft": content})
             └─ SoulEvent(AGENT_INTENT, payload={draft, mode="private"})

3. MEMORY MIDDLEWARE ENRICHMENT:
   MemoryMiddleware._on_agent_intent(event)
   ├─ prefetch via MemoryReader.retrieve_context(query=user_text, top_k=5, ...)
   ├─ event.payload["memory_context"] = summary
   └─ re-publish as AGENT_INTENT_ENRICHED

4. WORLD PERCEPTION (M3):
   WorldPerceptionMiddleware (production default ON)
   ├─ state update
   ├─ trace write
   └─ re-publish as AGENT_INTENT_PERCEIVED (no world_context change unless new world event)

5. SPEAKER TOKEN + LLM CALL:
   LLMProxy._handle_event_impl(event=AGENT_INTENT_PERCEIVED)
   ├─ read payload: memory_context, world_context, mood, chrono_block
   ├─ _build_messages_private(agent_id, soul, current_input, memory_context, world_context, ...)
   │
   └─ system_parts assembled:
        ├─ system_prompt (persona)
        ├─ memory_context ("you remember these things: ...")
        ├─ mood
        ├─ inner_life (3-day diary/dream/event)
        ├─ world_context (if non-empty)
        ├─ current_time + chrono-social
        ├─ bry_block
        └─ conversation_history (private history)

6. LLM RESPONSE:
   LLM generates reply
   └─ LLMProxy translates → bus.publish(AGENT_SPEAK)
        └─ MemoryMiddleware._on_agent_speak()
             ├─ relationships.on_agent_speak()
             └─ writer.post_reply_commit()
                  └─ _extract_facts_llm(user_text, agent_text)
                       ├─ MemoryReader.retrieve_context() [M5.10-2]
                       │    returns v1 facts as judge context
                       ├─ LLM Judge extracts facts
                       └─ facts → memory.db via writer
                             └─ MemoryEvolution (decay over time)

7. NEXT TURN:
   next USER_MESSAGE
   └─ MemoryMiddleware caches
   └─ consciousness._on_user_message() → mood update → _fire_intent
   └─ MemoryMiddleware._on_agent_intent() → prefetch (now includes new fact)
   └─ LLM sees: "Bry said X (user message) + I remember: [new fact] (memory_context)"
```

**Information loss points:**
- relationship state (confidence, feeling) — written, never read for LLM
- LLM Judge internal reasoning — not passed to LLM
- MemoryEvolution decay decisions — not surfaced

### Scenario C: Inner Life event occurs (e.g., diary write)

```
1. SOURCE: SoulScheduler._fire_morning() / _fire_night() / _fire_dream() / _fire_event()
   - time-based: morning (08:00), night (22:00)
   - random: dream (5min after night), event (4-8h random)

2. AGENCY TRIGGER (M5.2-H):
   _publish_agency_trigger(agent_id, trigger_type, extra={})
   ├─ SoulEvent(AGENCY_TRIGGER, payload={trigger_type: "morning" | "night" | "event" | "dream"})
   └─ NOT gated by M5.8-4 (only proactive_dm gated per design)

3. HANDLER DISPATCH:
   bus.subscribe → DiaryHandler (morning/night), DreamHandler (dream), EventHandler (event)
   ├─ DiaryHandler.handle_event()
   │    ├─ run_agency (decision)
   │    └─ [YES] → _diary_writer_executor(agent_id, slot)
   │
   ├─ DreamHandler.handle_event()
   │    ├─ run_agency (decision)
   │    └─ [YES] → _dream_writer_executor(dreamer, target_agent_id, all_agents)
   │
   └─ EventHandler.handle_event()
        ├─ run_agency (decision)
        └─ [YES] → _event_writer_executor(agent_id)

4. INNERLIFE EVENT CREATION (M5.4-6.1):
   diary_callback_factory / dream_callback / event_callback
   ├─ inner_life_writer.create_event(
   │      trigger_type="conversation:user_message" (for diary from M5.6-2),
   │      source_system="narrative",
   │      actor_id=agent_id,
   │      session_id=session_id,
   │      correlation_id=session_id,
   │      summary=...,
   │      data=...,
   │  )
   │
   └─ writes:
        ├─ data/inner_life/{agent_id}/events/{event_id}.json
        ├─ append to data/inner_life/trace.jsonl
        └─ (if diary/dream/event) DiaryWriter.write_diary/dream/event()
              └─ LLM call for subjective reflection
              └─ write to data/soul/{agent_id}/diary/{date}.jsonl (source="llm")

5. NEXT PROACTIVE OR USER TURN:
   Time passes. Next proactive_dm or USER_MESSAGE.
   MemoryMiddleware._on_agent_intent() prefetch:
   - v1 memory (no diary here, separate data store)
   - LLM doesn't see InnerLifeEvent from this trigger

   LLMProxy._format_recent_inner_life(agent_id):
   - reads data/soul/{agent_id}/diary/{date}.jsonl
   - finds the new diary entry
   - filters source=="llm"
   - truncates, returns
   - LLM sees: "[2026-08-11 morning] [diary content]"

6. AGENCY LATER:
   SoulScheduler._fire_proactive_dm()
   ├─ _inner_life_gate_check(agent_id)
   │    - reads trace.jsonl
   │    - finds the diary InnerLifeEvent
   │    - 30min cooldown check
   │
   └─ if elapsed < 30min → GATED
      if elapsed >= 30min → EMITTED → LLM call

7. NEXT CYCLE:
   next USER_MESSAGE or proactive
   LLM sees: [最近內在生活] with the new diary entry
   LLM can naturally reference it (or not, per Bry 派工 spirit: "根據對話上下文自然運用即可")
```

**Information loss points:**
- InnerLifeEvent canonical identity (provenance, event_id) — not in LLM prompt
- Diary/dream/event reasoning trace — not preserved
- LLM's reasoning when generating reflection — discarded after write

---

## 7. Dependency Graph

```
                    ┌─────────────────────────────────────────────────┐
                    │  DATA SOURCES (persistence)                      │
                    │                                                  │
                    │  ┌────────────┐  ┌──────────────┐  ┌──────────┐  │
                    │  │ world/     │  │ memory/      │  │ soul/    │  │
                    │  │ state +    │  │ memory.db    │  │ relations│  │
                    │  │ trace      │  │ (SQLite)     │  │ json     │  │
                    │  └─────┬──────┘  └──────┬───────┘  └────┬─────┘  │
                    │        │                │               │        │
                    │  ┌─────┴────────────────┴───────────────┘        │
                    │  │                                                │
                    │  │  ┌─────────────┐  ┌────────────┐  ┌─────────┐ │
                    │  │  │ inner_life/ │  │ soul/      │  │ inner_  │ │
                    │  │  │ trace.jsonl │  │ diary/     │  │ life/   │ │
                    │  │  │ (canonical) │  │ dream/     │  │ events/ │ │
                    │  │  └─────┬──────┘  │ event/     │  │ *.json  │ │
                    │  │        │         │ jsonl      │  └────┬────┘ │
                    │  │        │         └─────┬──────┘       │      │
                    │  │        │               │              │      │
                    │  └────────┼───────────────┼──────────────┘      │
                    │           │               │                     │
                    └───────────┼───────────────┼─────────────────────┘
                                │               │
                                ▼               ▼
                    ┌─────────────────────────────────────┐
                    │  SUBSYSTEMS (process/route)          │
                    │                                       │
                    │  WorldPerceptionMiddleware            │
                    │  ├─ validation / state / trace        │
                    │  └─ produces world_context (text)     │
                    │                                       │
                    │  MemoryMiddleware                     │
                    │  ├─ user_text cache                   │
                    │  ├─ relationships (write-only)        │
                    │  └─ prefetch → memory_context (text)  │
                    │                                       │
                    │  HeartbeatEngine                      │
                    │  └─ chrono-social + attachment_heat   │
                    │                                       │
                    │  consciousness (per-agent)            │
                    │  ├─ emotion / mood                    │
                    │  └─ state / dependency / intimacy     │
                    │                                       │
                    │  WorldInnerLifeAdapter (M5.9-3.1)     │
                    │  └─ InnerLifeEvent creation           │
                    │                                       │
                    │  DiaryWriter / DreamWriter / Event    │
                    │  └─ subjective content (LLM-generated)│
                    │                                       │
                    │  InnerLifeWriter (M5.4-5.1)           │
                    │  └─ canonical inner life events       │
                    │                                       │
                    └──────────────────┬────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  SOUL CONTEXT COMPOSITION             │
                    │  File: src/llm/proxy.py               │
                    │  Functions: _build_messages_group/_private │
                    │                                       │
                    │  system_parts = [                     │
                    │    system_prompt (persona),           │
                    │    memory_context,                    │
                    │    mood_desc,                         │
                    │    inner_life,                        │
                    │    world_context,                     │
                    │    current_time + chrono-social,      │
                    │    bry_block,                         │
                    │  ]                                    │
                    │  + conversation_history (separate)    │
                    │                                       │
                    │  ⚠️  RELATIONSHIPS NOT INCLUDED        │
                    │  ⚠️  RAW InnerLifeEvent trace excluded │
                    └──────────────────┬────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  LLM CALL                             │
                    │  OpenAI / Claude / Gemini             │
                    │  → response                           │
                    └──────────────────┬────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  EXPRESSION                           │
                    │  bus.publish(AGENT_SPEAK)             │
                    │  → ChannelRouter → Telegram/Discord   │
                    └──────────────────┬────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  EXPERIENCE                           │
                    │  bus.publish(AGENT_SPEAK)             │
                    │  → MemoryMiddleware                   │
                    │     ├─ relationships write            │
                    │     └─ memory.db write (post_reply_commit) │
                    │  → LLM Judge (extract facts)          │
                    └──────────────────┬────────────────────┘
                                       │
                                       ▼
                    ┌──────────────────────────────────────┐
                    │  NEXT CYCLE                           │
                    │  memory.db → MemoryReader → next LLM  │
                    │  relationships (next write, not read) │
                    │  inner_life trace → M5.8-4 gate      │
                    │  diary/dream/event → _format_recent   │
                    └──────────────────────────────────────┘
```

**Legend:**
- ✅ = existing connection (active)
- ❌ = no path (intentional or missing)
- ⚠️ = concern (gap or limited path)

---

## 8. Quality > Quantity Assessment

| Dimension | Rating | Evidence |
|-----------|--------|----------|
| **Coherence** | **PARTIAL** | 7+ context blocks concatenated; no unified metadata; relationships missing |
| **Provenance** | **STRONG** | InnerLifeEvent has full provenance; Memory fact has source_pair; freeze contracts enforce this |
| **Temporal continuity** | **STRONG** | chrono-social, heartbeat, system tick, SESSION_END carryover |
| **Identity continuity** | **PARTIAL** | agent_id, session_id maintained; but no per-block identity metadata |
| **Duplication** | **MINIMAL** | memory vs diary intentional separation (M5.10-3); no observed redundant writes |
| **Stale context** | **SOME** | memory_context can be stale (recency not enforced); inner_life reads 3-day; world_context is per-tick |
| **Contradictory context** | **NONE observed** | No mechanism for contradictions; each block is from a single source |
| **Missing context** | **MAJOR** | **Social (relationships) entirely missing**; raw InnerLifeEvent trace; world state internals |
| **Deterministic behavior** | **STRONG** | qualification rules (M5.9-3 whitelist), M5.8-4 gate (30min), Memory dedup |

### Quality assessment summary

The architecture is **mostly coherent** with strong provenance and temporal continuity. The single major gap is **Social context (relationships)** — the LLM has zero awareness of how the character feels about the user or about other characters.

---

## 9. Gap Classification

| Gap | Classification | Rationale |
|-----|---------------|-----------|
| Relationships not in LLM prompt | **P1** | Architecture integrity — character cannot maintain coherent social stance without LLM knowing it |
| Raw InnerLifeEvent trace not in LLM prompt | **P3 / INTENTIONAL** | Trace is for M5.8-4 gate; not intended for LLM directly; diary content reaches LLM instead |
| World state internals not in LLM prompt | **P3** | Only surface text rendered; sufficient for current spec |
| World sources all synthetic in production | **P3 / DEFERRED** | Real-world APIs out of scope per M5.8-1; protocol defined for future |
| Memory evolution / decay not in LLM prompt | **P3** | Internal mechanism, not surfaced to LLM |
| LLM Judge reasoning not in LLM prompt | **P3** | Judge evaluates facts, doesn't pass reasoning back |
| No unified Soul Context object | **P3 / INTENTIONAL** | 7+ blocks; composition is by concatenation, not unification |
| No provenance metadata per block | **P3** | LLM doesn't know "this fact from memory.db vs this diary from soul/" |
| P2.2 Inner Life → Agency decision awareness | **PARTIALLY CLOSED (M5.8-4)** | M5.12-1 |
| P2.6 ProactiveDM → Memory awareness | **DEFERRED** | M5.12-1 |
| P2.4 Relationships read | **CLOSED (M5.11-2)** | Intentional Stage 4.2/4.3 |

---

## 10. Frozen Contract Verification

| Contract | File | Status | Evidence |
|---------|------|--------|---------|
| AgencyState | `agency/state.py` | **FROZEN** | M5.1 dataclass, no inner_life field |
| Stage 1-4 pure functions | `agency/stages.py` | **FROZEN** | M5.8-3 confirmed C, M5.11-2 documented |
| TriggerEnvelope | `agency/trigger.py` | **FROZEN** | M5.2-F frozen |
| Agency.run() | `agency/agency.py` | **FROZEN** | M5.2-G, no contract change |
| WorldEvent contract | `world/perception.py` | **UNCHANGED** | M5.9-3.1 verified |
| InnerLifeEvent schema | `inner_life/event.py` | **FROZEN** | M5.4-5.1 frozen |
| Provenance schema | `inner_life/identity.py` | **FROZEN** | M5.4-5.1 frozen |
| Event Bus contracts | `eventbus/schema.py` | **UNCHANGED** | M5.9-3.1 verified |
| SAGE / v1 memory schema | `memory/sage/`, `memory/v1/` | **UNCHANGED** | M5.10-2 verified |
| Existing acceptance suites | tests/test_*.py | **UNCHANGED** | 203/203 PASS |

**All frozen contracts remain intact. No contract changes in this audit's scope.**

---

## 11. Production Integrity

- READ-ONLY audit: No source modification, no production mutation
- Data integrity: No memory.db, diary, trace, or relationships modified
- Regression risk: None (0 source changes)
- Working tree: 20 pre-existing untracked artifacts preserved

---

## 12. Regression

No source modifications in this audit. Baseline established from M5.12-1:

| Suite | Count | Status |
|-------|-------|--------|
| M5.8-4 producer gating | 19 | ✅ PASS |
| M5.9-3 world → inner life | 27 | ✅ PASS |
| M5.9-3.1 production wiring | 46 | ✅ PASS |
| M5.10-2 judge v1 context | 13 | ✅ PASS |
| M5.11-2 formal closures | (no new tests) | ✅ PASS |
| M5.12-1 P2.6 audit | (no new tests) | ✅ PASS |
| M5.2-G proactive DM bridge | 11 | ✅ PASS |
| M5.4-6.2 proactive DM inner life wiring | 36 | ✅ PASS |
| M5.2 minimal agency | 22 | ✅ PASS |
| M5.7 heartbeat | 29 | ✅ PASS |
| **Total** | **203** | **✅ PASS** |

This audit is READ-ONLY and produces no code changes. No new test runs required.

---

## 13. Git State

- **Baseline:** `48c3063` (M5.12-1)
- **Expected post-commit:** new audit log commit
- **Working tree:** 20 pre-existing untracked artifacts preserved

---

## 14. Architectural Recommendation

### Recommendation: **B — M5.13-2 Design Audit**

The architecture is mostly sufficient. There is **one P1 architectural gap** (relationships not in LLM prompt) that warrants a design audit before any implementation.

### Why not A (minimal integration)?

- The relationships-in-prompt gap is not a "small additive" change. It requires:
  - Decision: what to expose (confidence only? feeling? impression? all?)
  - Decision: how to format (raw JSON? text? gated by threshold?)
  - Decision: privacy boundary (some relationship fields might be too private for LLM)
  - Decision: where to add to proxy.py (after mood? after inner_life? separate section?)
- This is design space, not implementation space.

### Why not C (close M5.13)?

- The relationships gap is a real P1 issue, not a documentation gap.
- M5.11-2 closed P2.4 (read API gap) but did NOT close the LLM-context gap.
- Closing M5.13 would leave this P1 unaddressed.

### Why not D (defer to larger future milestone)?

- The relationships gap is small enough to address in a focused design audit.
- A "larger future milestone" would conflate this with unrelated work.
- Bry's working style favors tight, focused tickets (per 修法 / S-1B precedent).

### What M5.13-2 should cover

1. Trace which relationship fields should reach LLM
2. Determine formatting and threshold
3. Define privacy boundary (which fields are too sensitive)
4. Define where to inject in proxy.py
5. Verify no frozen contract change
6. Acceptance criteria for minimal integration

### What M5.13-2 should NOT do

- Implement relationships-in-prompt without a separate ticket
- Modify frozen contracts
- Add LLM/vector/embedding infrastructure
- Refactor existing context composition

---

## 15. Unresolved Bry Decisions

| # | Decision | Status |
|---|----------|--------|
| 1 | P2.2 scope accept (M5.12-1) | Pending Bry's response to M5.12-1 final report |
| 2 | P2.6 future direction (M5.12-1) | Pending Bry's response to M5.12-1 final report |
| 3 | **NEW: M5.13-2 = relationships-in-prompt design audit?** | Pending this audit's recommendation |

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M5.13-1 |
| Mode | STRICT READ-ONLY ARCHITECTURE AUDIT |
| Baseline | `48c3063` |
| Frozen contracts | 0 change verified |
| Audit scope | 4 context layers (Physical/Information/Social/Personal) + Soul Context composition + 3 end-to-end scenarios |
| Files read | `src/llm/proxy.py`, `src/agent/consciousness.py`, `src/memory/middleware.py`, `src/memory/sage/reader.py`, `src/memory/sage/writer.py`, `src/memory/sage/provider.py`, `src/heartbeat/engine.py`, `src/temporal/core.py`, `src/temporal/models.py`, `src/soul/relationships.py`, `src/inner_life/trace_reader.py`, `src/inner_life/writer.py`, `src/world/perception.py`, `src/world/middleware.py`, `src/world/registry.py`, `scripts/run_server.py` |
| Regression | 203/203 PASS (prior baseline) |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
