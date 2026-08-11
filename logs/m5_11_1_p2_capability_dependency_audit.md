# M5.11-1 — Situated Life P2 Capability Dependency Audit

**Mode:** READ-ONLY / ARCHITECTURE AUDIT
**Baseline:** `68f10e7` (M5.10-3 closeout)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT
**Audit scope:** P2.2 / P2.4 / P2.5 / P2.6 / P2.7 (M5.8-1 remaining capabilities)

---

## Executive Summary

| # | Capability | Classification | Blocking? | Next Ticket |
|---|-----------|----------------|----------|-------------|
| P2.7 | Stage 4 Execution STUB | **D — Intentional boundary** | No (executor handles real path) | Close P2.7 |
| P2.2 | Agency does not reference Inner Life state | **C — Frozen contract conflict** | Yes (needs contract change) | M5.11-2 (if Bry approves) |
| P2.4 | Relationships written but weakly read | **D — Intentional boundary** | No | Document + close |
| P2.5 | Heartbeat EmotionalCarryover is idle | **B — Idle infrastructure** | No | M5.11-2 (if Bry approves) |
| P2.6 | ProactiveDM does not consult Memory | **C — Frozen contract conflict** | Yes (needs contract change) | M5.11-2 (if Bry approves) |

**Recommended next ticket:** M5.11-2 — P2.5 Formal Closure + P2.7 Closing Audit
**P0/P1:** M5.11-2
**P2/P3:** M5.11-2 scope extensions (P2.2 / P2.6), only if Bry approves frozen contract change

---

## 1. Five Runtime Traces

### P2.7 — Agency Stage 4 Execution STUB

**Trace:**

```
SoulScheduler._fire_proactive_dm (scheduler.py L247)
  → _publish_agency_trigger(agent_id, "proactive_dm", extra={})
    → _inner_life_gate_check(agent_id) [M5.8-4 gate]
      → SoulEvent(AGENCY_TRIGGER) published to bus
        → AgencyTriggerHandler.handle_event(event)
          → TriggerEnvelope.from_payload(payload)
          → run_agency(state, perception=None, trigger=envelope, now=utc)
            → Stage 1: check_eligibility(state, now) → EligibilityResult
            → Stage 2: make_decision(eligibility, None, state, now, trigger)
            → Stage 3: select_action(decision_type) → action_type
            → Stage 4: execute_action_stub(action_type) → ExecutionResult(executed=True, reason="STUB: would publish AGENT_SPEAK...")
          → if decision.should_act:
              → await self.llm_executor(envelope.agent_id, envelope)
                → _proactive_dm_llm_executor(agent_id, trigger) [run_server.py L556]
                  → _agent = lookup agent
                  → _agent._fire_intent(...)
                    → LLM call (consciousness.py)
                      → bus.publish(AGENT_SPEAK) [consciousness.py]
```

**Key finding:** Stage 4 (`execute_action_stub`) is a STUB that returns `executed=True, reason="STUB: would publish AGENT_SPEAK..."`. No `bus.publish(AGENT_SPEAK)` inside `stages.py`. The actual production side effect (LLM call + AGENT_SPEAK publish) lives **entirely outside** the frozen 4-stage contract, in `run_server.py:_proactive_dm_llm_executor`.

**Evidence:**
- `stages.py:197-201`: `execute_action_stub` returns hardcoded STUB reason
- `run_server.py:556-620`: `_proactive_dm_llm_executor` contains actual LLM + bus.publish logic
- `trigger_handler.py:114-121`: `handle_event` calls `self.llm_executor` after decision YES

**Conclusion:** P2.7 is **not blocking P2.2 or P2.6**. The real execution path bypasses Stage 4 entirely. This is intentional — Stage 4 is documented as "STUB only, no production side effect" (M5.1 contract).

---

### P2.2 — Agency Does Not Reference Inner Life State

**Trace:**

```
Frozen AgencyState (state.py L14-37):
  Fields: last_action_at, last_decision_at, is_dormant, is_busy,
          action_cooldown_seconds, decision_cooldown_seconds
  NO: inner_life field, emotional_state field, memory field

Frozen Stage 1 check_eligibility(stages.py L60-82):
  Inputs: state: AgencyState, now: datetime
  References: state.is_dormant, state.is_busy, state.last_action_at, state.action_cooldown_seconds
  No: inner_life reading, NarrativeTraceReader, MemoryReader

Frozen Stage 2 make_decision(stages.py L88-166):
  Inputs: eligibility, perception, state, now, trigger
  References: eligibility.eligible, perception.accepted, perception.priority,
              state.last_decision_at, state.decision_cooldown_seconds, trigger.*
  No: inner_life reading, memory reading

Frozen Stage 3 select_action(stages.py L172-184):
  Input: decision_type (str)
  Output: action_type (str) — pure 1:1 mapping
  No: any external data source

Frozen Stage 4 execute_action_stub (stages.py L190-201):
  Input: action_type (str)
  Output: ExecutionResult(executed=True, reason="STUB: ...")
  No: bus.publish, LLM call, memory read
```

**What M5.8-4 did (partial P2.2 resolution):**
- `scheduler.py:247-314`: `_inner_life_gate_check` reads `NarrativeTraceReader` before publishing `proactive_dm`
- This is **producer-side gating** — gate happens BEFORE Agency receives trigger
- Does NOT give Stage 1-4 awareness of Inner Life state

**What P2.2 full resolution would require:**

| Option | Approach | Contract Impact | Feasibility |
|--------|----------|----------------|-------------|
| Option 1 | Add `inner_life_state` field to `AgencyState` dataclass | **FROZEN** — M5.1 contract | BLOCKED |
| Option 2 | New pure function `check_inner_life_eligibility(il_state, ...)` | New function, but Stage 1 contract frozen | BLOCKED |
| Option 3 | Producer-side gating (M5.8-4 pattern) for all trigger types | Additive, no frozen contract change | Possible but narrow scope |
| Option 4 | Pass InnerLifeState through `trigger.extra` dict | Dict field, but semantic intent unclear | Bry decision needed |

**Classification: C — Frozen contract conflict**

**Key evidence that Stage 1-4 semantics are frozen:**
- M5.8-3 (M5.8-2/3 audit) already concluded: All options for Inner Life → Agency require Stage 2 byte-level change → CONTRACT CONFLICT → stopped
- `AgencyState` is a frozen dataclass per M5.1 contract
- `check_eligibility` / `make_decision` / `select_action` are frozen pure functions per M5.1 contract

---

### P2.4 — Relationships Written But Weakly Read

**Trace — Write path (ACTIVE):**

```
MemoryMiddleware.handle_event(event)
  → USER_MESSAGE: _relationships_manager.on_user_message(target_agent_id, user_id)
    → RelationshipsStore.touch(BRYAN_ENTITY_ID, delta=+0.05)
      → decay → ensure_relationship → flush to relationships.json
  → AGENT_SPEAK: _relationships_manager.on_agent_speak(speaker, session_agents)
    → RelationshipsStore.touch(other_id, delta=+0.008) per pair
  → dream: _relationships_manager.on_dream(dreamer_id, target_id)
  → event: _relationships_manager.on_event(agent_id)
```

**Trace — Read path (ZERO production consumers):**

```
RelationshipsStore.get(other_id)          — defined, 0 production calls
RelationshipsStore.get_all()             — defined, 0 production calls
MultiAgentRelationshipsManager.get_store() — defined, 0 production calls
  Docstring: "給 debug / 將來 4.2 diary 用"
```

**Evidence of zero production consumers:**

```python
# relationships.py L444:
def get_store(self, agent_id: str) -> RelationshipsStore:
    """對外提供單 store 讀取 (給 debug / 將來 4.2 diary 用)。"""
    return self._get_store(agent_id)

# relationships.py L29-30 (file header):
# Stage 4.1 第一刀範圍 (最小可驗收):
#   - 還不做 LLM 抽 impression (那是 Stage 4.3 動態互動的範圍)
#   - 還不做 4.1 -> 4.2 diary 串接 (那是 Stage 4.2 開工時)
```

**Classification: D — Intentional boundary**

**Rationale:** Stage 4.1 scope explicitly deferred read APIs to Stage 4.2/4.3. Read APIs are infrastructure stubs for future diary consumption. No current production consumer. This is not a gap — it's an intentional design boundary.

---

### P2.5 — Heartbeat EmotionalCarryover Is Idle

**Trace — Creation:**

```
consciousness._on_session_end(event)
  → EmotionalCarryover(
      intimacy_afterglow=min(state.intimacy_level/100, 1.0),
      unresolved_worry=state.dependency*0.5 if elapsed>60 else 0,
      emocional_openness_residue=...,
      attachment_heat=state.dependency,
      source_event="session_end",
    )
  → carryover.save(agent_id)  → agents/{agent_id}/carryover.json
```

**Trace — Loading + Publishing:**

```
HeartbeatEngine.start()
  → for agent_id in _agent_ids:
      carryover = EmotionalCarryover.load(agent_id, data_dir).apply_decay(0)
      self._carryovers[agent_id] = carryover

HeartbeatEngine._loop() tick body:
  → carryover = self._carryovers.get(primary_agent, EmotionalCarryover())
  → chrono_ctx = build_temporal_context(..., carryover=carryover, ...)
  → tick = SoulEvent(SYSTEM_TICK, payload={
        "attachment_heat": round(chrono_ctx.carryover.attachment_heat, 2),
        "chrono_block": render_temporal_block(chrono_ctx),
        ...
      })
  → await bus.publish(tick)
```

**Trace — Who consumes SYSTEM_TICK payload?**

```
consciousness.register() [consciousness.py L147-158]:
  → bus.subscribe(handler=handle_event, event_filter={
        USER_MESSAGE,
        # SYSTEM_TICK REMOVED per M5.7-2
        # EventType.SYSTEM_TICK,  # M5.7-2: 拿掉, 避免 proactive Agency
        AGENT_SPEAK,
        SESSION_END,
      })
  → _on_tick method: DEAD CODE (never called since M5.7-2)

System Tick payload fields published but NOT consumed for Agency decisions:
  - attachment_heat
  - chrono_block
  - time_period, silence_hours, vulnerability_window, deviation_interpretation

No production consumer in Agency Stage 1-4 or trigger handler.
```

**Evidence:**

1. `consciousness.py:152`: `SYSTEM_TICK` removed from `event_filter` per M5.7-2
2. `consciousness.py:290`: `_on_tick` method exists but is never called (dead code)
3. `HeartbeatEngine` publishes `SYSTEM_TICK` with `carryover_loaded` field — no consumer
4. `_on_session_end` writes carryover to disk — Heartbeat loads it on restart

**What IS using carryover:**
- `HeartbeatEngine._carryovers[agent_id]` feeds into `build_temporal_context`
- `render_temporal_block(chrono_ctx)` renders `attachment_heat` into `chrono_block` string
- `chrono_block` is embedded in `SYSTEM_TICK.payload.chrono_block`
- This `chrono_block` string is consumed by the LLM prompt at generation time (consciousness → LLM → response)

**Key distinction:**
- Carryover is **consumed at LLM generation time** (in the prompt that builds the response)
- Carryover is **NOT consumed for Agency decision-making** (Stage 1-4 gating)
- "Idle" means idle for the **Agency decision path**, not idle for LLM generation

**Classification: B — Idle infrastructure for Agency decisions**

**Note:** This is NOT dead code. `EmotionalCarryover` is actively loaded, published, and rendered into LLM prompts. It is idle only for the **situated-life Agency decision path** (should proactive_dm fire based on carryover? currently: no).

---

### P2.6 — ProactiveDM Does Not Consult Memory Before Triggering

**Trace:**

```
SoulScheduler._fire_proactive_dm()  [scheduler.py]
  → _inner_life_gate_check(agent_id) [M5.8-4 gate — reads NarrativeTraceReader]
    → gate_proactive_dm(agent_id, now_utc, NarrativeTraceReader())
      → NarrativeTraceReader: reads trace.jsonl (Inner Life event trace)
      → Check: any InnerLifeEvent in last 30 min?
        → YES: GATED, skip publish
        → NO: EMITTED, continue
  → _publish_agency_trigger(agent_id, "proactive_dm", ...)
    → SoulEvent(AGENCY_TRIGGER) published
      → AgencyTriggerHandler.handle_event → run_agency → decision → llm_executor
        → _proactive_dm_llm_executor(agent_id, trigger)
          → _agent._fire_intent(...) → LLM → bus.publish(AGENT_SPEAK)
```

**What M5.8-4 provides (partial P2.6 resolution):**
- `NarrativeTraceReader` gate checks Inner Life trace before publishing
- This is **not `memory.db` consultation** — it's Inner Life event trace
- `NarrativeTraceReader` reads from `data/inner_life/{agent_id}/trace.jsonl`
- `MemoryReader` reads from `data/memory/memory.db` (different data source)

**What full P2.6 resolution would require:**
- Pass `MemoryReader` to scheduler
- Add new gate function: consult memory.db for recent significant facts
- e.g., "Did this agent have a significant interaction in the last N hours per memory.db?"

**Classification: C — Frozen contract conflict for memory.db consultation**

**Note:** M5.8-4 provides partial resolution (Inner Life awareness, not memory.db). The gap is specifically `memory.db` consultation.

---

## 2. Dependency Graph

```
┌─────────────────────────────────────────────────────────────┐
│  P2.7 — Stage 4 STUB (INTENTIONAL)                         │
│  Real path: _proactive_dm_llm_executor (outside Stage 4)     │
│  BLOCKS: NOTHING — executor handles real execution          │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  P2.4 — Relationships weakly read (INTENTIONAL)             │
│  Write: active via MemoryMiddleware                         │
│  Read: 0 production consumers ("debug / 將來 4.2 diary 用") │
│  BLOCKS: NOTHING                                            │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  P2.5 — EmotionalCarryover idle for Agency decisions       │
│  Created: _on_session_end → carryover.save()                │
│  Published: SYSTEM_TICK.payload.attachment_heat             │
│  Consumed: LLM prompt generation (YES)                      │
│  Consumed: Agency Stage 1-4 gating (NO)                     │
│  BLOCKS: NOTHING — only affects "proactive based on carryover" │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  P2.2 — Agency not aware of Inner Life state               │
│  Frozen: AgencyState (no inner_life field)                  │
│  Frozen: Stage 1-4 pure functions                           │
│  M5.8-4 partial: producer-side gating for proactive_dm     │
│  BLOCKS: Meaningful Stage 1-4 inner life awareness          │
│  PATH FORWARD: Producer-side gating (M5.8-4 pattern) —     │
│                possible without frozen contract change       │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│  P2.6 — ProactiveDM does not consult memory.db              │
│  M5.8-4 partial: NarrativeTraceReader gate (trace.jsonl)   │
│  Gap: memory.db (factual conversation memory)                │
│  BLOCKS: Memory-aware proactive trigger gating               │
│  PATH FORWARD: Requires new gate function + MemoryReader     │
│                access to scheduler — Stage 2 territory       │
└─────────────────────────────────────────────────────────────┘
```

**Dependency ordering:**
1. P2.7 → nothing blocked (intentional, executor handles)
2. P2.4 → nothing blocked (intentional, Stage 4.2/4.3 future)
3. P2.5 → nothing blocked (idle for Agency, active for LLM prompts)
4. P2.2 → P2.6 both need Bry decision on frozen contract scope
5. P2.2 more tractable: producer-side gating (M5.8-4 pattern) possible
6. P2.6 harder: memory.db access from scheduler requires new infrastructure

---

## 3. Classification A/B/C/D

| Capability | Classification | Rationale |
|-----------|----------------|-----------|
| P2.7 Stage 4 STUB | **D — Intentional boundary** | Stage 4 is documented as STUB; real execution is in executor layer outside frozen contract |
| P2.2 Inner Life → Agency | **C — Frozen contract conflict** | M5.8-3 already proved all options hit frozen contract; producer-side gating (M5.8-4 pattern) is partial workaround |
| P2.4 Relationships read | **D — Intentional boundary** | Stage 4.1 scope explicitly deferred; docstring says "debug / 將來 4.2 diary 用"; 0 production consumers |
| P2.5 EmotionalCarryover idle | **B — Idle infrastructure** | Actively used in LLM prompts; idle only for Agency decision path; NOT dead code |
| P2.6 Memory → ProactiveDM | **C — Frozen contract conflict** | M5.8-4 provides Inner Life gate (trace.jsonl); memory.db gap requires new gate function + MemoryReader wiring |

---

## 4. Priority Ranking

| Priority | Capability | Rationale |
|----------|-----------|-----------|
| **P0 — Correctness** | P2.7 closure | Formally document that P2.7 is resolved (executor handles real path); stop treating as "missing capability" |
| **P1 — Architecture integrity** | P2.5 formal closure | Document that carryover IS used (LLM prompts), NOT dead; establish that "idle for Agency decisions" is intentional |
| **P2 — Capability value** | P2.2 (producer-side gating extension) | M5.8-4 pattern extensible to other trigger types; additive, no frozen contract change |
| **P3 — Cleanup** | P2.4 documentation | Add explicit docstring to Stage 4.1 that read APIs are intentionally deferred |
| **P2 — Capability value** | P2.6 memory gate | Hardest; requires MemoryReader access to scheduler + new gate function; Stage 2 territory |

---

## 5. Frozen Contract Impact

| Capability | Frozen Objects Touched | Impact |
|-----------|----------------------|--------|
| P2.7 | None | No change — executor is outside frozen contract |
| P2.2 | `AgencyState`, `check_eligibility`, `make_decision`, `stages.py` | **BLOCKED** — all frozen per M5.1/M5.8-3 |
| P2.4 | None | No change — write path active, read APIs are stubs |
| P2.5 | None | No change — carryover infrastructure is additive |
| P2.6 | `SoulScheduler` | **BLOCKED for memory.db** — new gate function would need MemoryReader injected into scheduler |

---

## 6. Production Integrity

- **READ-ONLY audit:** No source modification, no production mutation
- **Data integrity:** No memory.db, diary, trace, or relationships modified
- **Regression risk:** None (0 source changes in this audit)
- **Working tree:** 20 pre-existing untracked artifacts preserved

---

## 7. Regression

No source modifications in this audit. Baseline established from prior test runs:

| Suite | Scope | Status |
|-------|-------|--------|
| M5.8-4.1 focused | 533 tests, producer gating | PASS |
| M5.9-3 focused | 46 tests, world → inner life | PASS |
| M5.9-3.1 regression | 508 tests, wiring | PASS |
| M5.10-2 focused | 13 tests, judge v1 context | PASS |
| M5.10-3 regression | diary audit | PASS |
| M5.8-1 P2.x baseline | Existing M-series tests | PASS |

**This audit does not require new test runs** — it is READ-ONLY and produces no code changes.

---

## 8. P0/P1/P2/P3 Summary

```
P0 — Correctness / Contract clarity:
  M5.11-2: P2.7 Formal Closure — document that Stage 4 STUB is intentional,
           real path is in _proactive_dm_llm_executor (outside frozen contract)

P1 — Architecture integrity:
  M5.11-2 (same ticket): P2.5 Formal Closure — document that EmotionalCarryover
           IS actively used (LLM prompt generation), idle only for Agency gating

P2 — Capability extension (producer-side):
  M5.11-3 (NEW): P2.2 Producer Gating Extension — extend M5.8-4 pattern to
           additional trigger types (morning/night/dream/event), additive,
           no frozen contract change

P3 — Documentation:
  M5.11-2 (same ticket): P2.4 Explicit Documentation — add docstring noting
           read APIs are intentionally deferred to Stage 4.2/4.3

BLOCKED (C — frozen contract conflict, Bry decision required for Stage 2 change):
  P2.2 (full): Inner Life state awareness inside Stage 1-4 frozen functions
  P2.6: memory.db consultation from scheduler gate (requires MemoryReader injection)
```

---

## 9. Bry Decision Required?

**YES — for two items:**

### Decision 1: P2.7 Formal Closure
- **Question:** Is it acceptable to close P2.7 as "resolved via executor layer"?
- **Evidence:** Stage 4 STUB is intentional per M5.1 contract. Real AGENT_SPEAK path is in `_proactive_dm_llm_executor` (run_server.py L556-620), which is outside the frozen 4-stage contract.
- **Risk of NOT closing:** P2.7 continues to appear as "unresolved missing capability" in audits, creating confusion.
- **Recommended:** Close P2.7. Executor layer handles real execution. Stage 4 remains STUB as designed.

### Decision 2: P2.2 / P2.6 Frozen Contract Scope
- **Question:** Should Bry approve Stage 2 modification for P2.2 (Stage 1-4 awareness) and/or P2.6 (memory.db gate)?
- **Evidence:** M5.8-3 already established that all Stage 1-4 changes require byte-level frozen contract modification.
- **Alternative (already done):** M5.8-4 producer-side gating provides partial P2.2/P2.6 resolution without frozen contract change.
- **Recommended:** Decline frozen contract change for Stage 1-4. Extend M5.8-4 producer-side gating for remaining trigger types (P2.2 partial). Accept memory.db gap as a known limitation.

---

## 10. Recommended Next Ticket

### M5.11-2 — Situated Life P2 Formal Closures + Documentation

**Scope (minimal, additive only):**

1. **P2.7 Formal Closure:** Document in `stages.py:execute_action_stub` docstring that real execution is in executor layer (run_server.py L556-620). Add comment citing this audit.

2. **P2.5 Formal Closure:** Document in `HeartbeatEngine._loop` and `consciousness._on_session_end` that EmotionalCarryover IS actively used in LLM prompt generation. "Idle for Agency decisions" is intentional separation (M5.7-2 heartbeat constraint). NOT dead code.

3. **P2.4 Explicit Documentation:** Confirm in `relationships.py:get_store()` docstring that read APIs are intentionally deferred to Stage 4.2/4.3. Add "DO NOT IMPLEMENT" annotation if Bry approves.

**STOP conditions for M5.11-2:**
- If any comment/docstring change requires frozen contract modification → STOP
- If any Stage 1-4 semantic change needed → STOP (Bry decision gate)
- If any production data mutation → STOP

**This ticket is purely documentation/truthfulness correction — code hygiene, not refactor (per Bry 8/9 13:01 S-1B precedent).**

---

## Acceptance Criteria Verification

| # | Criterion | Status |
|---|-----------|--------|
| 1 | All five P2 paths traced | ✅ |
| 2 | Current runtime consumers verified | ✅ |
| 3 | Frozen boundaries identified | ✅ |
| 4 | Dependency graph documented | ✅ |
| 5 | Duplicate/overlapping work identified | ✅ |
| 6 | Each P2 classified A/B/C/D | ✅ |
| 7 | Priority/order established | ✅ |
| 8 | Exactly ONE next ticket recommended | ✅ |
| 9 | No source modification | ✅ |
| 10 | No production mutation | ✅ |
| 11 | Audit log committed and pushed | Pending |

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M5.11-1 |
| Mode | READ-ONLY / ARCHITECTURE AUDIT |
| Baseline | `68f10e7` |
| Frozen contracts | `AgencyState`, Stage 1-4 pure functions, TriggerEnvelope, Event Bus schema |
| Audit scope | P2.2 / P2.4 / P2.5 / P2.6 / P2.7 |
| Files read | `src/agency/state.py`, `src/agency/stages.py`, `src/agency/agency.py`, `src/agency/trigger.py`, `src/agency/trigger_handler.py`, `src/soul/scheduler.py`, `src/soul/relationships.py`, `src/heartbeat/engine.py`, `src/agent/consciousness.py`, `src/conversation_qualification/qualifier.py`, `src/temporal/models.py`, `src/temporal/core.py`, `src/temporal/render.py` |
| Regression | READ-ONLY audit, no new test runs required |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
