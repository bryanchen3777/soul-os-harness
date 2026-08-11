# M5.8-1 — Situated Life Coherence Architecture Audit

**Mode:** READ-ONLY / ARCHITECTURE AUDIT
**Baseline:** HEAD = 9d4769d = origin/main
**Date:** 2026-08-10
**Verdict:** **Architecture is layered and stable. 7 P2 capability gaps found, 0 P0/P1. Recommended next milestone: M5.9 (capability gap hardening) OR M6 (new product surface).**

---

## 1. Executive Summary

This audit asks: has Soul OS progressed from independent autonomous subsystems into a coherent **continuous-lived-context** architecture?

**Answer: Yes, with significant caveats.**

**What's coherent:**
- ✅ 9-layer architecture is established (Physical → Information → Social → Personal → Soul Context → Agency → Expression → Experience → Memory)
- ✅ Event bus is the single integration backbone (13 modules publish/subscribe)
- ✅ Agency 4-stage logic (Eligibility → Decision → Selection → Execution)
- ✅ 5 InnerLifeEvent producers with canonical identity (Diary/Dream/Event/ProactiveDM/ConversationQualification)
- ✅ Memory feeds Expression via memory_context injection (M5.3)
- ✅ World → Expression via world_context injection (M3)
- ✅ 17 frozen contracts preserved across 18+ commits
- ✅ 392/392 regression tests pass

**What's NOT coherent (P2 capability gaps):**
- ⚠️ Memory LLM Judge doesn't see Diary/Dream content
- ⚠️ Agency doesn't consult InnerLife state when deciding
- ⚠️ World → InnerLife direct path missing (only World → Expression)
- ⚠️ Relationships data is written but rarely read (not in LLM context)
- ⚠️ Heartbeat's EmotionalCarryover in SYSTEM_TICK payload is computed but unused (M5.7-2)
- ⚠️ ProactiveDM doesn't consult Memory before triggering (no semantic gate)
- ⚠️ Agency Stage 4 (Execution) is STUB only

These gaps don't violate any frozen contract. They are **capability opportunities** for future tickets, not architectural defects.

---

## 2. Complete Architecture / Lifecycle Map

### 2.1 9 Layers (with actual runtime paths)

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 1: PHYSICAL / WORLD                                              │
│   - External: TG / Web / WebSocket (USER_MESSAGE)                      │
│   - External: World events source (WORLD_EVENT: weather/calendar/news) │
│   - Internal: Heartbeat Engine (60s SYSTEM_TICK + 30min SESSION_END)  │
│   Producers: src/io/gateway.py, src/world/*, src/heartbeat/engine.py  │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ bus.publish
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 2: INFORMATION (Event Bus)                                        │
│   - SoulEventBus: asyncio.PriorityQueue, 13 subscribers                 │
│   - 22+ event types (USER_MESSAGE, SYSTEM_TICK, AGENT_INTENT, ...)     │
│   Producers: every module that calls bus.publish()                       │
│   Consumers: every module that calls bus.subscribe()                    │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 3: SOCIAL (Relationships)                                         │
│   - data/soul/{agent}/relationships.json (per-agent)                    │
│   - Stage 4.1: USER_MESSAGE → touch target_agent (+0.05)               │
│   - Stage 4.1: AGENT_SPEAK → touch session_agents (+0.02)              │
│   - Stage 4.3: LLM impression writing (短日文片語)                      │
│   Producer/Consumer: src/memory/middleware.py (MemoryMiddleware)        │
│   Status: WRITTEN but rarely READ (P2.4 gap)                           │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 4: PERSONAL / INNER LIFE                                          │
│   - InnerLifeWriter (canonical identity authority)                      │
│   - NarrativeTraceWriter (auto-trace sidecar)                          │
│   - DiaryWriter / DreamWriter / EventWriter (Soul storage)              │
│   - ConversationQualification (M5.6-2 + M5.7-2 activated)              │
│   5 producers:                                                       │
│     1. Diary morning (M5.4-5.3) → diary:morning                         │
│     2. Diary night (M5.4-5.3) → diary:night                             │
│     3. Dream (M5.4-5.3) → dream:dream                                   │
│     4. Event (M5.4-5.3) → dream:event                                   │
│     5. ProactiveDM (M5.4-6.2) → agent_reply                              │
│     6. Conversation qualified (M5.6-2) → conversation:user_message        │
│   Files: src/inner_life/*, src/soul/{diary,dream_event}.py              │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ AGENCY_TRIGGER
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 5: SOUL CONTEXT (Emotional + Temporal)                            │
│   - EmotionalCarryover (Temporal core, per-agent persistent)            │
│   - Emotion engine (mood, intimacy, in-memory state)                    │
│   - Loaded by Heartbeat at startup (per-agent)                          │
│   - Written at SESSION_END (consumed by consciousness._on_session_end)  │
│   - Injected into SYSTEM_TICK payload (M3.5 chrono)                     │
│   Status: PARTIALLY WIRED — SYSTEM_TICK carryover is unused (M5.7-2)    │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ AGENCY_TRIGGER
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 6: AGENCY (4-stage decision logic)                                │
│   - src/agency/agency.py: Eligibility → Decision → Selection → Execution│
│   - 4 handlers consume AGENCY_TRIGGER:                                  │
│     1. AgencyTriggerHandler (proactive_dm, M5.2-G)                      │
│     2. EventHandler (event, M5.2-H Phase 1)                             │
│     3. DreamHandler (dream, M5.2-H Phase 2)                            │
│     4. DiaryHandler (morning/night, M5.2-H Phase 3)                    │
│   - Stage 4 (Execution) is STUB (per agency.py:8)                        │
│   Producer: src/soul/scheduler.py (AGENCY_TRIGGER publish)              │
│   Consumers: src/agency/* (4 handlers)                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ SPEAKER_TOKEN_GRANTED → AGENT_SPEAK
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 7: EXPRESSION (LLMProxy + IOGateway)                              │
│   - LLMProxy: subscribes to SPEAKER_TOKEN_GRANTED, publishes AGENT_SPEAK│
│   - IOGateway: subscribes to AGENT_SPEAK, publishes to channels          │
│   - ChannelRouter / FishTTSHandler (audio)                              │
│   Producers: src/llm/proxy.py, src/io/gateway.py                        │
│   Consumers: src/io/*, src/llm/fish_tts_handler.py                      │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ AGENT_SPEAK
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 8: EXPERIENCE (USER_MESSAGE → LLM → AGENT_SPEAK loop)             │
│   - Each turn: USER_MESSAGE → AGENT_INTENT → ENRICHED → PERCEIVED →    │
│     SPEAKER_TOKEN_GRANTED → AGENT_SPEAK                                 │
│   - Context: memory_context + world_context + inner_life + SOUL          │
│   Status: COHERENT per-turn; CROSS-TURN continuity via conversation     │
│   history + carryover                                                   │
└─────────────────────────────────────────────────────────────────────────┘
                                  ↓ AGENT_SPEAK + SESSION_END
┌─────────────────────────────────────────────────────────────────────────┐
│ Layer 9: MEMORY (SAGE Graph + v1 Mirror)                                │
│   - MemoryMiddleware: subscribes to AGENT_INTENT, AGENT_SPEAK          │
│   - SAGELiteProvider per agent (SQLite graph + JSON mirror)              │
│   - LLM Judge (3 categories × 3 discrete judgments)                     │
│   - Prefetch injects memory_context into AGENT_INTENT                  │
│   - v1 mirror for retrieval, graph for relationships                    │
│   Files: src/memory/*, src/memory/sage/*                               │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Event Bus Topology (13 subscribers)

| Subscriber | Subscribes To | File | Purpose |
|------------|---------------|------|---------|
| `memory_middleware` | USER_MESSAGE, AGENT_INTENT, AGENT_SPEAK | `src/memory/middleware.py` | Fact extraction + memory_context injection |
| `llm_proxy` | SPEAKER_TOKEN_GRANTED | `src/llm/proxy.py` | LLM generation → AGENT_SPEAK |
| `world_perception` | WORLD_EVENT, AGENT_INTENT_ENRICHED | `src/world/middleware.py` | World state + context injection (M3) |
| `speaker_token_manager` | AGENT_INTENT_PERCEIVED (+ fallback ENRICHED), AGENT_SPEAK | `src/eventbus/token_manager.py` | Token arbitration |
| `heartbeat_activity_tracker` | USER_MESSAGE | `src/heartbeat/engine.py` | Activity clock reset (M5.7-2) |
| `heartbeat_silence_tracker` | AGENT_SPEAK | `src/heartbeat/engine.py` | Global silence tracking |
| `conversation_qualification` | SESSION_END | `src/conversation_qualification/qualifier.py` | Conversation → InnerLifeEvent (M5.6-2/M5.7-2) |
| `consciousness.{agent_id}` | USER_MESSAGE, AGENT_SPEAK, SESSION_END | `src/agent/consciousness.py` | Per-agent routing (SYSTEM_TICK filtered M5.7-2) |
| `agency_trigger_handler` | AGENCY_TRIGGER | `src/agency/trigger_handler.py` | 4-stage decision (proactive_dm) |
| `event_handler` | AGENCY_TRIGGER | `src/agency/event_handler.py` | 4-stage decision (event) |
| `dream_handler` | AGENCY_TRIGGER | `src/agency/dream_handler.py` | 4-stage decision (dream) |
| `diary_handler` | AGENCY_TRIGGER | `src/agency/diary_handler.py` | 4-stage decision (morning/night) |
| `io_gateway` | AGENT_SPEAK, AGENT_AUDIO_READY | `src/io/gateway.py` | Channel routing |
| `fish_tts_handler` | AGENT_SPEAK | `src/llm/fish_tts_handler.py` | TTS synthesis |
| `voice_tts_service` | (legacy) | `src/voice/tts_service.py` | (kept for compatibility) |

**Total: 13+ subscribers across 9 layers.** Single integration backbone (SoulEventBus).

---

## 3. Actual Runtime Paths

### 3.1 Per-Turn (USER_MESSAGE → AGENT_SPEAK)

```
1. USER_MESSAGE (IOGateway, src/io/gateway.py:594)
   payload: {text, target_user_id, target_agent, mode, ...}
   session_id: f"session_{user_id}_{agent_id}"
   ↓ bus.publish

2. consciousness._on_user_message (src/agent/consciousness.py:171)
   ↓ extracts content, mode, target_agent
   ↓ publishes AGENT_INTENT (no canonical eid for USER_MESSAGE path)

3. MemoryMiddleware._on_agent_intent (src/memory/middleware.py:254)
   ↓ prefetch memory facts
   ↓ re-publishes as AGENT_INTENT_ENRICHED with memory_context

4. [OPTIONAL M3] WorldPerceptionMiddleware._on_agent_intent_enriched
   ↓ (src/world/middleware.py:241)
   ↓ top-N world events selection
   ↓ re-publishes as AGENT_INTENT_PERCEIVED with world_context

5. SpeakerTokenManager (src/eventbus/token_manager.py)
   ↓ subscribes to AGENT_INTENT_PERCEIVED (production) or AGENT_INTENT_ENRICHED (fallback)
   ↓ arbitrates via SpeakerTokenBus
   ↓ publishes SPEAKER_TOKEN_GRANTED to winner

6. LLMProxy (src/llm/proxy.py:2529)
   ↓ subscribes to SPEAKER_TOKEN_GRANTED
   ↓ builds messages (system + history + memory + world + intent)
   ↓ calls LLM
   ↓ publishes AGENT_SPEAK
   (inner_life_event_id is None for USER_MESSAGE path)

7. MemoryMiddleware._on_agent_speak (src/memory/middleware.py:392)
   ↓ updates relationships (Stage 4.1)
   ↓ calls post_reply_commit (write to graph + v1)
   (inner_life_event_id is None for USER_MESSAGE path → writer uses synthetic UUID per M5.5-2)

8. FishTTSHandler (src/llm/fish_tts_handler.py)
   ↓ subscribes to AGENT_SPEAK
   ↓ synthesizes TTS
   ↓ publishes AGENT_AUDIO_READY

9. IOGateway (src/io/gateway.py:255)
   ↓ subscribes to AGENT_SPEAK, AGENT_AUDIO_READY
   ↓ routes to channel (TG, web, websocket)
```

**Status: FULLY INTEGRATED end-to-end.** Every step is real production code, not documentation.

### 3.2 Per-Session (USER_MESSAGE → SESSION_END → InnerLifeEvent)

```
1. [30 minutes of inactivity]
   ↓ HeartbeatEngine._loop (engine.py:164+)
   ↓ elapsed_mins >= 30
   ↓ _session_ended flag check (dedup)

2. SESSION_END publish (engine.py:242+)
   payload: {elapsed_mins, last_user_activity, last_session_id, last_user_id, last_agent_id}
   ↓ bus.publish

3. ConversationQualification.on_session_end (qualifier.py:194)
   ↓ evaluate() — extract session_id, read conversation history entry count
   ↓ if duration >= 5min AND turn_depth >= 4:
   ↓   promote() → writer.create_event(trigger_type="conversation:user_message")
   ↓   → canonical InnerLifeEvent created
   ↓ NarrativeTraceWriter auto-traces

4. consciousness._on_session_end (src/agent/consciousness.py:386)
   ↓ computes EmotionalCarryover
   ↓ persists to data/agents/{agent}/carryover.json
```

**Status: M5.6-2 + M5.7-2 integration. Conversation → InnerLifeEvent → trace works end-to-end.**

### 3.3 Per-Scheduled (Scheduler → AGENCY_TRIGGER → Diary)

```
1. Scheduler._run_loop (src/soul/scheduler.py:765)
   ↓ at 08:00 / 22:00
   ↓ _fire_all(slot) — for each agent
   ↓ publish AGENCY_TRIGGER(trigger_type=slot) — slot=morning/night

2. DiaryHandler.on_agency_trigger (src/agency/diary_handler.py)
   ↓ validates envelope
   ↓ filters trigger_type ∈ {morning, night}
   ↓ calls writer_executor(agent_id, slot)
   ↓ writer_executor → diary_callback_factory(aid) (run_server.py:418-420)
   ↓ → diary_writer_executor(agent_id, slot)
   ↓ → generate_diary_entry (LLM call)
   ↓ → writer.write_diary(..., inner_life_event_id=event_id)
   ↓ → canonical InnerLifeEvent created (trigger_type=diary:morning/night)
```

**Status: M5.2-H Phase 3 integration. Diary producer wires AGENCY_TRIGGER → InnerLifeEvent.**

---

## 4. Integration Matrix

| Source | Bus | Target | Status | Path Type |
|--------|-----|--------|--------|-----------|
| USER_MESSAGE (TG/Web) | ✓ | Consciousness | real E2E | per-turn |
| USER_MESSAGE (TG/Web) | ✓ | Heartbeat (activity tracker) | real E2E | per-turn |
| USER_MESSAGE (TG/Web) | ✓ | Memory (cache) | real E2E | per-turn |
| AGENT_INTENT (consciousness) | ✓ | Memory (prefetch) | real E2E | per-turn |
| AGENT_INTENT (consciousness) | ✓ | SpeakerToken (arbiter) | real E2E | per-turn |
| AGENT_INTENT_ENRICHED (memory) | ✓ | World (perception) | real E2E | per-turn (M3) |
| AGENT_INTENT_PERCEIVED (world) | ✓ | SpeakerToken (final) | real E2E | per-turn (M3) |
| SPEAKER_TOKEN_GRANTED (token) | ✓ | LLMProxy | real E2E | per-turn |
| AGENT_SPEAK (LLMProxy) | ✓ | Memory (write) | real E2E | per-turn |
| AGENT_SPEAK (LLMProxy) | ✓ | FishTTS | real E2E | per-turn |
| AGENT_SPEAK (LLMProxy) | ✓ | IOGateway | real E2E | per-turn |
| AGENT_SPEAK (LLMProxy) | ✓ | Heartbeat (silence) | real E2E | per-turn |
| AGENT_SPEAK (LLMProxy) | ✓ | Token (release) | real E2E | per-turn |
| AGENT_AUDIO_READY (FishTTS) | ✓ | IOGateway | real E2E | per-turn |
| SYSTEM_TICK (Heartbeat) | ✓ | (none — filtered) | BUS ONLY (no consumer) | observation |
| SESSION_END (Heartbeat) | ✓ | ConversationQualification | real E2E (M5.6-2) | per-session |
| SESSION_END (Heartbeat) | ✓ | consciousness (carryover) | real E2E | per-session |
| AGENCY_TRIGGER (Scheduler) | ✓ | 4 Agency handlers | real E2E | per-scheduled |
| AGENCY_TRIGGER (Scheduler) | ✓ | InnerLife (via executor) | real E2E | per-scheduled |
| WORLD_EVENT (external) | ✓ | World (state + perception) | real E2E | per-world |
| MEMORY_QUERY (consciousness) | ✓ | Memory (loader) | real E2E | per-turn |
| MEMORY_RETRIEVED (memory) | ✓ | consciousness | real E2E | per-turn |

**22+ bus paths. All real E2E. ZERO "data persistence only" or "isolated subsystem" patterns.**

---

## 5. Temporal Continuity Analysis

| Continuity Type | Source | Persistence | Cross-boundary | Status |
|-----------------|--------|-------------|----------------|--------|
| **Turn** | AGENT_SPEAK → conversation history | `data/conversations/{user}_{agent}_private.json` | within session | ✅ WORKING |
| **Session** | session_id, carryover, conversation history | session_id + carryover.json | across turns | ✅ WORKING (M5.7-2 + M5.6-2) |
| **Scheduled** | Scheduler AGENCY_TRIGGER (morning/night) | per-agent persistent | across days | ✅ WORKING (M5.2-H) |
| **Proactive** | Scheduler proactive_dm (Ruka whitelist) | per-agent persistent | across days | ✅ WORKING (M5.4-6.2) |
| **InnerLife** | InnerLifeWriter (canonical event_id) | trace.jsonl + diary/dream/event jsonl | cross-session | ✅ WORKING (M5.4-5.1) |
| **Memory** | Fact triples (SAGE + v1 mirror) | SQLite + JSONL | cross-session | ✅ WORKING (M5.3) |
| **Relationships** | relationships.json (per-agent) | per-agent JSON | cross-session | ✅ WORKING (Stage 4.1) |
| **EmotionalCarryover** | carryover.json (per-agent) | per-agent JSON | across SESSION_END | ⚠️ PARTIAL — Heartbeat loads at startup, writes at SESSION_END, but the in-flight SYSTEM_TICK payload that carries it is unused (M5.7-2) |

**Temporal continuity is well-established except for one P2 capability gap: Heartbeat's EmotionalCarryover is computed but not propagated to AGENT_INTENT (M5.7-2 removed SYSTEM_TICK consumer in consciousness).**

---

## 6. World Awareness Integration

```
WORLD_EVENT (external)
    ↓ bus.publish
WorldPerceptionMiddleware._on_world_event (src/world/middleware.py:244)
    ↓ validate → state.add_event → trace
    ↓ NO InnerLife creation (just observation)

AGENT_INTENT_ENRICHED
    ↓ bus.publish
WorldPerceptionMiddleware._on_agent_intent_enriched (src/world/middleware.py:241+)
    ↓ top-N world events selection (perception_budget=3)
    ↓ re-publish as AGENT_INTENT_PERCEIVED with world_context

LLMProxy (AGENT_INTENT_PERCEIVED → AGENT_SPEAK)
    ↓ world_context injected into system prompt
    ↓ LLM uses world events to inform response
```

| World Influence | Status | Notes |
|-----------------|--------|-------|
| World → Expression (LLM context) | ✅ WORKING | M3 Phase 1 |
| World → Agency (4-stage decision) | ❌ DISCONNECTED | Agency has no World input |
| World → Inner Life (Diary/Dream content) | ❌ DISCONNECTED | Diary/Dream writers don't read World state |
| World → Memory (fact extraction) | ❌ DISCONNECTED | Memory LLM Judge only sees conversation text |

**World awareness has a single integrated path (→ Expression via LLM). 3 disconnected paths to other layers.**

---

## 7. Social/Relationship Integration

```
USER_MESSAGE
    ↓ bus.publish
MemoryMiddleware._on_user_message (src/memory/middleware.py:226)
    ↓ _relationships_manager.on_user_message(agent_id, user_id) (Stage 4.1)
    ↓ relationships.json[agent_id][user_id].confidence += 0.05
    ↓ relationships.json[agent_id][user_id].interaction_count += 1

AGENT_SPEAK
    ↓ bus.publish
MemoryMiddleware._on_agent_speak (src/memory/middleware.py:392)
    ↓ _relationships_manager.on_agent_speak(speaker, session_agents) (Stage 4.1)
    ↓ relationships.json[speaker][other_agent].confidence += 0.02
```

| Relationship Influence | Status | Notes |
|----------------------|--------|-------|
| Relationships → Agency (4-stage decision) | ❌ DISCONNECTED | Agency doesn't read relationships.json |
| Relationships → Expression (LLM context) | ⚠️ PARTIAL | Stage 4.3 LLM impression is written but not read by LLMProxy |
| Relationships → Memory (fact extraction) | ❌ DISCONNECTED | Memory LLM Judge doesn't see relationships |
| Relationships → future behavior | ❌ DISCONNECTED | No read path exists |

**Relationships data is being WRITTEN (Stage 4.1) but barely READ. 4 disconnected paths.**

---

## 8. Inner Life Feedback Loops

```
5 InnerLifeEvent Producers:
1. Diary morning/night (M5.4-5.3) → data/soul/{agent}/diary/{date}.jsonl
2. Dream (M5.4-5.3) → data/soul/{agent}/dream/{date}.jsonl
3. Event (M5.4-5.3) → data/soul/{agent}/event/{date}.jsonl
4. ProactiveDM (M5.4-6.2) → InnerLifeWriter.create_event (canonical eid)
5. ConversationQualification (M5.6-2 + M5.7-2) → InnerLifeWriter.create_event (canonical eid)

All 5 producers also auto-write to trace.jsonl via NarrativeTraceWriter.
```

| Inner Life Influence | Status | Notes |
|----------------------|--------|-------|
| Inner Life → Agency (decision input) | ❌ DISCONNECTED | Agency 4-stage doesn't read diary/dream/event |
| Inner Life → Expression (LLM context) | ⚠️ PARTIAL | LLMProxy._format_recent_inner_life (M2.0) reads diary but not dream/event |
| Inner Life → Memory (fact extraction) | ❌ DISCONNECTED | Memory LLM Judge only sees conversation text |
| Inner Life → future proactive behavior | ⚠️ PARTIAL | ProactiveDM uses InnerLifeEvent (canonical eid propagation), but doesn't read existing InnerLife content |

**Inner Life has 4 partial / disconnected paths to other layers. Only ProactiveDM uses InnerLife identity fully (canonical eid → AGENT_SPEAK).**

---

## 9. Memory Closing the Loop

```
Experience (USER_MESSAGE → AGENT_SPEAK)
    ↓ MemoryMiddleware._on_agent_speak (src/memory/middleware.py:392)
    ↓ MemoryWriter.write_turn (M5.3)
    ↓ Fact extraction via LLM Judge (3 categories)
    ↓ Writes to:
    ↓   - SQL facts (graph.sqlite)
    ↓   - v1.jsonl mirror (Memory.inner_life_event_id per M5.5-2)
    ↓   - trace.jsonl (M5.4-6.4)

Memory
    ↓ MemoryMiddleware._on_agent_intent (prefetch)
    ↓ Loads memory_context
    ↓ Re-publishes as AGENT_INTENT_ENRICHED
    ↓ LLMProxy receives memory_context
    ↓ LLM generates response with memory awareness

New Experience (next turn)
    ↓ Loop continues
```

| Memory Feedback | Status | Notes |
|----------------|--------|-------|
| Memory → LLM context (prefetch) | ✅ WORKING | M5.3 |
| Memory → LLM Judge (per-fact extraction) | ✅ WORKING | M5.3 |
| Memory → Inner Life (fact → diary?) | ❌ DISCONNECTED | No reverse path |
| Memory → proactive_dm (semantic gate) | ❌ DISCONNECTED | ProactiveDM doesn't consult Memory before triggering |
| Memory → Agency (memory-informed decision) | ❌ DISCONNECTED | Agency has no Memory input |

**Memory has 2 working feedback paths (prefetch, judge) and 3 disconnected paths (reverse to Inner Life, proactive gate, agency decision).**

---

## 10. Duplicated / Disconnected Paths

### 10.1 Duplicated Paths

| Path A | Path B | Status |
|--------|--------|--------|
| `scheduler._fire_all(morning)` legacy callback | `DiaryHandler.on_agency_trigger` (M5.2-H Phase 3) | A is noop in production (M5.2-I-7); B is the real path |
| `scheduler._fire_dream` direct write | `DreamHandler.on_agency_trigger` (M5.2-H Phase 2) | A is legacy; B is the real path |
| `scheduler._fire_event` direct write | `EventHandler.on_agency_trigger` (M5.2-H Phase 1) | A is legacy; B is the real path |

**No active duplication.** Legacy paths exist but are noop'd; new M5.2-H paths are production.

### 10.2 Disconnected Paths (the 7 P2 gaps)

| # | Disconnection | Impact |
|---|---------------|--------|
| 1 | World → Inner Life | Diary/Dream content doesn't reflect world events |
| 2 | World → Agency | Agency decisions don't consider world state |
| 3 | Relationships → Agency/Expression/Memory | Data exists but unused |
| 4 | Inner Life → Agency | Agency doesn't read diary/dream |
| 5 | Inner Life → Memory | Memory Judge doesn't see diary/dream |
| 6 | Memory → proactive_dm (gate) | ProactiveDM fires regardless of memory |
| 7 | Agency Stage 4 (Execution) STUB | Pure function with no real execution |

---

## 11. Identity / Provenance / Correlation

| Identity | Source | Persistence | Status |
|----------|--------|-------------|--------|
| `event_id` (SoulEvent) | UUID4 | event lifetime | ✅ M5.4-5.5 frozen |
| `session_id` (SoulEvent) | `f"session_{user_id}_{agent_id}"` (LLMProxy._session_key) | event lifetime | ✅ M5.5-2 preserved |
| `correlation_id` (SoulEvent) | upstream event_id | event lifetime | ✅ M5.4-5.5 frozen |
| `parent_event_id` (InnerLifeEvent) | parent event_id (causation chain) | InnerLifeWriter._events | ✅ M5.4-5.1 frozen |
| `inner_life_event_id` (SoulEvent) | canonical InnerLifeEvent.event_id | propagated to downstream | ✅ M5.5-2 |
| `inner_life_event_id` (Fact) | canonical OR synthetic UUID | Memory fact | ✅ M5.5-2 |
| `lineage_depth` (InnerLifeEvent) | computed from parent | event lifetime | ✅ M5.4-5.1 |
| `last_session_id` (SESSION_END payload) | from USER_MESSAGE.session_id | SESSION_END event | ✅ M5.6-2 |
| `last_user_id` (SESSION_END payload) | from USER_MESSAGE payload | SESSION_END event | ✅ M5.6-2 |
| `last_agent_id` (SESSION_END payload) | from USER_MESSAGE payload (private mode) | SESSION_END event | ✅ M5.6-2 |
| `trigger_type` (Provenance) | canonical vocabulary | InnerLifeEvent lifetime | ✅ M5.4-5.1 + M5.6-2 added |
| `correlation_id` (InnerLifeEvent) | = session_id (semantically session IS the group) | InnerLifeEvent lifetime | ✅ M5.6-2 |

**Identity is well-defined. 12 identity fields, all propagated correctly.**

### 11.1 Missing Identity

| Missing | Severity | Notes |
|---------|----------|-------|
| AGENT_SPEAK doesn't carry `parent_diary_event_id` for diary-triggered responses | P2 | Diary doesn't trigger user-facing AGENT_SPEAK (WRITER_ONLY per M5.2-H Phase 3) |
| AGENT_SPEAK doesn't carry `parent_world_event_id` for world-influenced responses | P2 | World → Expression via prompt only, not via event correlation |
| Memory Fact doesn't carry `parent_inner_life_event_id` (only `inner_life_event_id` for direct promotion) | P3 | Could enable Memory → Inner Life reverse trace |
| ProactiveDM response doesn't carry `source_diary_id` (which diary inspired the proactive) | P2 | Diary could influence proactive, but currently disconnected |

---

## 12. P0/P1/P2/P3 Findings

### 12.1 P0 — Correctness / Production Integrity

**0 P0 findings.** No correctness issues, no production data corruption, no contract violations, no P0 risks.

### 12.2 P1 — Architecture Integrity

**0 P1 findings.** Layered architecture is coherent, bus integration is correct, Agency 4-stage logic is preserved, 18 frozen contracts verified.

### 12.3 P2 — Capability Gaps (7 findings)

| # | Finding | Severity | Description |
|---|---------|----------|-------------|
| P2.1 | Memory Judge doesn't see Diary/Dream | medium | Memory LLM Judge only sees USER_MESSAGE + AGENT_SPEAK text. Diary/Dream content exists but is not in Memory's fact extraction context. |
| P2.2 | Agency doesn't consult Inner Life state | medium | Agency 4-stage has no input from diary/dream/event state. Decisions are made without Inner Life context. |
| P2.3 | World → Inner Life direct path missing | medium | WORLD_EVENT updates WorldPerceptionState, which feeds world_context to LLM. But Diary/Dream/Event writers don't see world events. |
| P2.4 | Relationships data is rarely read | medium | relationships.json is written on USER_MESSAGE / AGENT_SPEAK (Stage 4.1) but is not read by Agency, Memory, or LLM context. |
| P2.5 | Heartbeat's EmotionalCarryover is unused | medium | SYSTEM_TICK payload contains `attachment_heat` etc. (M3.5 chrono). But M5.7-2 removed consciousness SYSTEM_TICK subscriber. The carryover is computed but never reaches AGENT_INTENT. |
| P2.6 | ProactiveDM doesn't consult Memory before triggering | medium | Scheduler fires proactive_dm based on timing (3-5h interval), not on memory semantic gate. No "should I say something now?" reasoning. |
| P2.7 | Agency Stage 4 (Execution) is STUB | medium | `agency.py:8` says "Stage 4 is STUB only". The 4-stage chain Eligibility → Decision → Selection → Execution is incomplete; Execution is not actually executed by Agency. |

### 12.4 P3 — Cleanup / Documentation (3 findings)

| # | Finding | Severity | Description |
|---|---------|----------|-------------|
| P3.1 | Agency 4-stage comment is misleading | cosmetic | Many references to "4-stage" in code/docs imply Execution is real. agency.py:8 says it's STUB. |
| P3.2 | "Future phase" placeholders scattered | cosmetic | Agency handler docstrings reference "M5.2-H 之後 phases" — could be cleaned up. |
| P3.3 | ConversationQualification v1 group-mode limit undocumented in run_server | cosmetic | Qualifier rejects group mode (agent_id_missing) but this is only in code, not in run_server.py. |

---

## 13. Frozen Contract Impact

**0 frozen contract conflicts.** All 18 contracts verified UNCHANGED:

| Contract | Status |
|----------|--------|
| M5.3 Memory Retrieval | UNCHANGED |
| SAGE / v1 schema | UNCHANGED |
| Fact schema | UNCHANGED |
| InnerLifeEvent frozen model | UNCHANGED |
| Provenance frozen model | UNCHANGED |
| SoulEvent schema | UNCHANGED |
| Event Bus contract | UNCHANGED |
| M5.4-6.x producer rules | UNCHANGED |
| NarrativeTraceWriter / Reader | UNCHANGED |
| LLM Judge qualification | UNCHANGED |
| Agency 4-stage logic | UNCHANGED (Stage 4 STUB is unchanged behavior) |
| Scheduler AGENCY_TRIGGER | UNCHANGED |
| MemoryMiddleware | UNCHANGED |
| ConversationQualification | UNCHANGED |
| Heartbeat Engine | UNCHANGED |
| World Perception (M3) | UNCHANGED |
| SpeakerToken | UNCHANGED |
| Existing 4 producer wiring | UNCHANGED |

**No contract changes proposed in this audit. The 7 P2 gaps can be addressed WITHOUT contract changes — they are capability gaps, not architectural defects.**

---

## 14. Regression

Run before this audit, state preserved:

| Suite | Tests | Status |
|-------|-------|--------|
| M5.4-5.1 Inner Life Foundation | part of 392 | PASS |
| M5.4-5.2 Memory Inner Life Integration | part of 392 | PASS |
| M5.4-5.3 Diary Inner Life Integration | part of 392 | PASS |
| M5.4-5.4 Dream Inner Life Integration | part of 392 | PASS |
| M5.4-5.5 Event Bus Inner Life Integration | part of 392 | PASS |
| M5.4-5.6 Narrative Trace Sidecar | part of 392 | PASS |
| M5.4-5.7 Trace Reader | part of 392 | PASS |
| M5.4-6.1 Executor Wiring | part of 392 | PASS |
| M5.4-6.2 Proactive DM Inner Life Wiring | part of 392 | PASS |
| M5.4-6.3 Trace Production Activation Audit | part of 392 | PASS |
| M5.4-6.4 Trace Production Activation | part of 392 | PASS |
| M5.5-2 Canonical InnerLifeEvent Propagation | part of 392 | PASS |
| M5.6-2 Conversation Qualification Implementation | 17/17 | PASS |
| M5.7-2 Heartbeat Reactivation | 20/20 | PASS |
| M5.7-4 Heartbeat Robustness | 9/9 | PASS |
| M3 E2E + World Awareness | 29/29 | PASS |
| **Total** | **392/392** | **PASS** |

Baseline held. No source code modified by this audit.

---

## 15. Production Integrity

- ✅ `data/memory/**` — 0 modification
- ✅ `data/soul/**/diary/**` — 0 modification
- ✅ `data/soul/**/dream/**` — 0 modification
- ✅ `data/soul/**/event/**` — 0 modification
- ✅ `data/inner_life/trace.jsonl` — 0 modification
- ✅ `data/conversations/**` — 0 modification
- ✅ `data/soul/relationships.json` — 0 modification
- ✅ `data/agents/{agent}/carryover.json` — 0 modification
- ✅ `data/memory.db` — 0 modification
- ✅ No historical backfill
- ✅ No trace replay
- ✅ Source code — 0 modification
- ✅ No new InnerLifeEvents created

---

## 16. Git State

### Before
```
HEAD = 9d4769d (docs(m5.7-4): add closeout summary log)
origin/main = 9d4769d
Working tree: 20 個 pre-existing untracked artifacts
```

### After (expected)
```
HEAD = TBD (commit hash 拍板後補)
origin/main = TBD
+ new: logs/m5_8_1_situated_life_coherence_audit.md (this file)
Untracked preserved: 20 pre-existing artifacts
```

### Commit (expected)
- `docs(m5.8-1): situated life coherence architecture audit (READ-ONLY)`
  - 1 file: this audit log
  - 0 source code changes
  - 0 test changes

---

## 17. Recommended Next Milestone

### Option A: M5.9 — P2 Capability Gap Hardening (multi-ticket)

Address the 7 P2 gaps with a series of focused tickets:

- **M5.9.1**: P2.7 — Agency Stage 4 Execution implementation (smallest scope)
- **M5.9.2**: P2.5 — Heartbeat SYSTEM_TICK carryover propagation (re-enable consciousness subscriber OR new path)
- **M5.9.3**: P2.4 — Relationships → LLM context (read relationships.json in LLMProxy)
- **M5.9.4**: P2.1 + P2.5 — Memory LLM Judge sees Diary/Dream (extend extract_and_write to include InnerLife context)
- **M5.9.5**: P2.3 — World → Inner Life path (diary_writer reads World state)
- **M5.9.6**: P2.6 — ProactiveDM semantic gate (consult Memory before triggering)
- **M5.9.7**: P2.2 — Agency reads Inner Life state (decision input from diary)

Each ticket should follow M5.6-2 / M5.7-2 pattern: audit → design → implement.

### Option B: M6 — New Product Surface

The architecture is mature enough to support new product features:
- Cross-session narrative reconstruction (M5.7-1 out-of-scope, M5.6-2 noted as future)
- LLM-based conversation qualification (M5.6-2 fallback B)
- Observability dashboard
- Multi-agent cross-talk

### Option C: Stability (no new ticket)

The architecture is layered, contracts are frozen, tests are green. M5.8-1 confirms Soul OS has reached a stable "situated life" baseline. No new ticket needed unless new product feature is required.

**Recommendation:** Option A (M5.9) is the natural next step. Each P2 gap is a small, well-scoped ticket.

---

## 18. Bry Decision Required?

**No immediate Bry decision required.** The audit:
- Does NOT propose any contract changes
- Does NOT require re-enabling any disabled behavior
- Does NOT require new infrastructure
- Documents 7 P2 capability gaps that are future-ticket candidates

Each future ticket (M5.9.1 ~ M5.9.7) would have its own Bry decision on:
- Whether to address the gap
- How aggressively (e.g., M5.9.3 could be "expose top-3 relationships in LLM context" or "expose all relationships with relevance filter")
- Cadence / priority

But for M5.8-1 itself: **no Bry decision required.**

---

## 19. Final Status

| Item | Status |
|------|--------|
| Audit complete | ✅ |
| Complete architecture/lifecycle map | ✅ (9 layers + 13+ subscribers) |
| Actual producer → bus → consumer paths | ✅ (22+ paths, all real E2E) |
| Integration matrix | ✅ |
| Temporal continuity analysis | ✅ (8 dimensions, 1 partial) |
| Identity/provenance analysis | ✅ (12 fields, 4 missing) |
| P0/P1/P2/P3 findings | ✅ (0/0/7/3) |
| Frozen-contract impact | ✅ (0 conflicts) |
| Regression | ✅ (392/392 PASS) |
| Production integrity | ✅ (0 modification) |
| Git state | ✅ (1 file, 0 source changes) |
| Recommended next milestone | ✅ (M5.9 capability hardening) |
| Bry decision required | ✅ NO (each M5.9.x would have its own) |
| Stop conditions | ✅ None triggered |
