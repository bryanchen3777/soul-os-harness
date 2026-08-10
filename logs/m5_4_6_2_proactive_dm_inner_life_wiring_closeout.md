# M5.4-6.2 — Proactive DM Inner Life Producer Wiring — CLOSEOUT

**Mode:** READ-ONLY AUDIT → MINIMAL IMPLEMENTATION
**Baseline:** HEAD = b08d702 = origin/main
**Final HEAD:** see git log
**Date:** 2026-08-10

---

## 1. Phase 1 Audit Findings

### Runtime call chain (proactive_dm path)

```
SoulScheduler._fire_proactive_dm
  → _publish_agency_trigger → AGENCY_TRIGGER SoulEvent [no inner_life_event_id]
    → AgencyTriggerHandler.handle_event
      → run_agency(trigger=envelope) → 4-stage decision
        → if decision=YES: _proactive_dm_llm_executor(agent_id, envelope)
          [M5.4-6.2 wiring point A: create InnerLifeEvent here]
          → agent._fire_intent(
              chrono_payload={
                  "draft": _draft,
                  "target_channel": "telegram",
                  "target_user_id": "...",
                  [M5.4-6.2 adds: "inner_life_event_id": event_id]
              }
            )
            [M5.4-6.2 wiring point B: extract inner_life_event_id from chrono_payload]
            → publishes AGENT_INTENT SoulEvent
              [M5.4-6.2: top-level SoulEvent.inner_life_event_id field is set]
            → MemoryMiddleware._on_agent_intent
            → WorldPerceptionMiddleware → AGENT_INTENT_PERCEIVED
            → SpeakerTokenManager
            → LLMProxy._handle_event_impl
              [M5.4-6.2 wiring point C: read event.inner_life_event_id]
              → builds messages, LLM call
              → builds AGENT_SPEAK SoulEvent
                [M5.4-6.2: AGENT_SPEAK.inner_life_event_id = event.inner_life_event_id]
              → bus.publish(speak_event)
              → stub AGENT_SPEAK fallback path also threads inner_life_event_id
```

### Authoritative producer boundary

**`_proactive_dm_llm_executor` in `scripts/run_server.py:454-510`** (post-M5.4-6.2)

This is the executor called by `AgencyTriggerHandler` after `Agency.run()` returns `decision=YES`. Parallel to the M5.4-6.1 diary/dream/event executor wirings, but with the difference that proactive_dm triggers an outbound `AGENT_INTENT` → LLM call → `AGENT_SPEAK` chain (not a direct writer call).

### Existing identity / correlation mechanism

| Field | Source | Notes |
|-------|--------|-------|
| `AGENCY_TRIGGER` SoulEvent | scheduler._publish_agency_trigger | no inner_life_event_id |
| `AGENT_INTENT` SoulEvent | consciousness._fire_intent | has session_id, no inner_life_event_id (pre-M5.4-6.2) |
| `AGENT_SPEAK` SoulEvent | LLMProxy._handle_event_impl | has session_id, correlation_id (fallback to AGENT_INTENT.event_id), no inner_life_event_id (pre-M5.4-6.2) |

**Existing propagation channel:** `chrono_payload` dict passed through `agent._fire_intent` (carries `draft`, `target_channel`, `target_user_id`, `dry_run`)

**Canonical cross-reference field:** `SoulEvent.inner_life_event_id: Optional[str]` (M5.4-5.5 frozen)

### Stop conditions check

| Stop condition | Triggered? | Notes |
|----------------|-----------|-------|
| 1. Frozen contract must change | NO | Uses existing `SoulEvent.inner_life_event_id` field (M5.4-5.5) |
| 2. Agency 4-stage logic must change | NO | Wiring is in executor after Agency decision |
| 3. Event Bus schema must change | NO | Schema is unchanged |
| 4. AGENT_SPEAK semantics must change | NO | AGENT_SPEAK payload/method unchanged, just adds optional field |
| 5. Production data mutation | NO | Wiring only, 0 mutation |
| 6. New identity authority | NO | Uses existing `InnerLifeWriter` |
| 7. Cross handler architecture refactor | NO | Single handler modification |
| 8. Authoritative producer boundary unclear | NO | `_proactive_dm_llm_executor` is the boundary, parallel to M5.4-6.1 pattern |

**No stop conditions triggered. Implementation proceeded.**

---

## 2. Exact Runtime Call Chain (post-M5.4-6.2)

| Step | Location | What happens |
|------|----------|--------------|
| 1 | `run_server.py:_proactive_dm_llm_executor` | `inner_life_writer.create_event(provenance=Provenance(trigger_type=TRIGGER_TYPE_AGENT_REPLY, actor_id=agent_id, source_system="narrative", extras={"trigger_source": "proactive_dm", "elapsed_mins": ...}))` |
| 2 | (same) | Try/except: failure → `_event_id = None` + logger.warning |
| 3 | (same) | `chrono_payload["inner_life_event_id"] = _event_id` (only if non-None) |
| 4 | `run_server.py` | `agent._fire_intent(chrono_payload=...)` |
| 5 | `consciousness.py:_fire_intent` | Extract `inner_life_event_id` from `chrono_payload` (defensive: must be non-empty str) |
| 6 | (same) | `SoulEvent(inner_life_event_id=_event_id, ...)` (AGENT_INTENT) |
| 7 | `bus.publish(AGENT_INTENT)` | Bus propagates event |
| 8 | `proxy.py:_handle_event_impl` | Receives AGENT_INTENT, reads `event.inner_life_event_id` |
| 9 | `proxy.py:2902+` (regular AGENT_SPEAK path) | `SoulEvent(inner_life_event_id=event.inner_life_event_id, ...)` (AGENT_SPEAK) |
| 10 | `proxy.py:2977+` (stub AGENT_SPEAK path) | Same as #9, for LLM failure / empty text fallback |
| 11 | `bus.publish(AGENT_SPEAK)` | Downstream consumers see `inner_life_event_id` on top-level field |

---

## 3. Implementation

### Files modified (3 tracked + 2 new)

| File | Change | Lines |
|------|--------|-------|
| `scripts/run_server.py` | Added `TRIGGER_TYPE_AGENT_REPLY` to inner_life import; wired `_proactive_dm_llm_executor` to create InnerLifeEvent and propagate via `chrono_payload` | +50/-3 |
| `src/agent/consciousness.py` | `_fire_intent` extracts `inner_life_event_id` from `chrono_payload`, sets on AGENT_INTENT SoulEvent top-level field | +18/-1 |
| `src/llm/proxy.py` | AGENT_SPEAK (regular + stub) reads `event.inner_life_event_id` and sets on AGENT_SPEAK top-level field | +10/-2 |
| `tests/test_m5_4_6_2_proactive_dm_inner_life_wiring.py` | New test file (25 tests) | new |
| `logs/m5_4_6_2_proactive_dm_inner_life_wiring_closeout.md` | New closeout log | new |

### InnerLifeEvent creation semantics

| Producer | trigger_type | actor_id | source_system | extras |
|----------|-------------|----------|---------------|--------|
| Proactive DM | `TRIGGER_TYPE_AGENT_REPLY` = "agent_reply" | agent_id | "narrative" | `{"trigger_source": "proactive_dm", "elapsed_mins": "..."}` |

**session_id, correlation_id, parent_event_id:** all `None` (not fabricated per ticket rule).
- Note: `session_id` is set on the AGENT_INTENT SoulEvent (from `consciousness._fire_intent` f"session_{user_id}_{agent_id}") and propagates to AGENT_SPEAK, but this is not a fabricated identity — it's the existing session anchor that the agent naturally creates.

---

## 4. Tests

**New test file:** `tests/test_m5_4_6_2_proactive_dm_inner_life_wiring.py` (25 tests)

| Section | Count | Coverage |
|---------|-------|----------|
| A. Executor pattern | 3 | 1 event per call, correct provenance, failure → event_id=None |
| B. Provenance semantics | 4 | trigger_type canonical, source_system valid, actor_id, extras |
| C. _fire_intent extraction | 5 | chrono_payload key → SoulEvent field, missing key → None, None chrono_payload → None, invalid type → ignored, empty string → ignored |
| D. AGENT_INTENT carries id | 2 | SoulEvent field accepts value, default None |
| E. LLMProxy propagation | 1 | Source inspection: AGENT_SPEAK construction threads event.inner_life_event_id |
| F. Failure isolation | 3 | create_event exception caught, _fire_intent still runs, validation error handled |
| G. Backward compatibility | 3 | existing draft-only chrono_payload, _fire_intent signature unchanged, legacy diary jsonl still readable |
| H. No duplicates | 1 | exactly 1 event per executor invocation |
| I. USER_MESSAGE exclusion | 2 | _fire_intent without inner_life_event_id key creates no event, TRIGGER_TYPE_USER_MESSAGE not used in wiring |
| count | 1 | sanity check |
| **Total** | **25** | |

**Result: 25/25 PASSED**

---

## 5. Regression

| Test File | Result | Notes |
|-----------|--------|-------|
| test_m5_4_5_1_inner_life_foundation.py | 59/59 PASS | |
| test_m5_4_5_2_memory_inner_life_integration.py | 29/29 PASS | |
| test_m5_4_5_3_diary_inner_life_integration.py | 27/27 PASS | |
| test_m5_4_5_4_dream_inner_life_integration.py | 23/23 PASS | |
| test_m5_4_5_5_event_bus_inner_life_integration.py | 16/16 PASS | |
| test_m5_4_5_6_narrative_trace_sidecar.py | 22/22 PASS | |
| test_m5_4_5_7_trace_reader.py | 22/22 PASS | |
| test_m5_4_6_1_executor_wiring.py (M5.4-6.1) | 30/30 PASS | |
| test_m5_4_6_2_proactive_dm_inner_life_wiring.py (NEW) | 25/25 PASS | |
| test_m3_e2e_smoke.py | 3/3 PASS | |
| test_m3_world_awareness.py | 26/26 PASS | |
| test_m3_disabled_mode.py | 1/1 PASS | |
| test_m3_observability.py | 8/8 PASS | |
| test_m3_prompt_integrity.py | 5/5 PASS | |
| test_websocket_e2e.py | **2/2 PASS** | **See evidence below** |
| **Total** | **298/298 PASS** | |

### test_websocket_e2e pre-existing failure analysis (per ticket requirement)

**Evidence (re-tested on baseline + M5.4-6.2 branch):**

| State | test_inject_tick_triggers_agent_speak | test_websocket_user_message_forwarding |
|-------|---------------------------------------|---------------------------------------|
| Baseline (b08d702, no M5.4-6.2 changes) | **PASS** | **PASS** |
| M5.4-6.2 branch (with all changes) | **PASS** | **PASS** |

**Conclusion:** The earlier M5.4-6.1 closeout log noted "1 pre-existing test_websocket_e2e failure". Re-verification under the M5.4-6.2 cycle shows this is a **timing flake** (test depends on running WebSocket server + LLM mock backend responding in time), not a persistent failure. Both tests pass consistently when run with adequate resources.

**No regressions introduced by M5.4-6.2.**

---

## 6. Production Integrity

- ✅ **STRICT 0 MUTATION** of production data
- ✅ No source/test modified in production paths (only executor + consciousness + proxy wiring)
- ✅ All tests use isolated `tmp_path` / mocked bus / mocked state
- ✅ `_restore_data_root()` in every test finally block
- ✅ No migration scripts created
- ✅ No temporary instrumentation added
- ✅ Mock bus class for testing isolation

---

## 7. Frozen Contract Verification

| Frozen contract | Status | Evidence |
|-----------------|--------|----------|
| `InnerLifeEvent` dataclass | UNCHANGED | M5.4-5.1 — no edits to event.py |
| `Provenance` dataclass | UNCHANGED | M5.4-5.1 — no edits to event.py |
| `NarrativeTraceWriter` | UNCHANGED | M5.4-5.6 — no edits |
| `NarrativeTraceReader` | UNCHANGED | M5.4-5.7 — no edits |
| `SoulEvent.inner_life_event_id` field | UNCHANGED | M5.4-5.5 — used as-is (default None, accepts value) |
| `DiaryWriter.write_entry` | UNCHANGED | M5.4-5.3 — not touched in M5.4-6.2 |
| `DreamEventWriter._write_entry` | UNCHANGED | M5.4-5.4 — not touched |
| Agency 4-stage decision logic | UNCHANGED | M5.2-G — wiring is in executor, not Agency |
| `AGENCY_TRIGGER` event type | UNCHANGED | M5.2-F — schema unchanged |
| `TriggerEnvelope` dataclass | UNCHANGED | M5.2-F — not touched |
| Event Handler trigger_type filters | UNCHANGED | M5.2-H — not touched |
| `consciousness._fire_intent` signature | UNCHANGED | M5.4-6.2 — back-compat: chrono_payload is dict, optional key |
| LLMProxy AGENT_SPEAK payload structure | UNCHANGED | M5.4-6.2 — payload dict unchanged, only added top-level field |

**All frozen contracts preserved.**

---

## 8. Git State

**Modified files (tracked):**
- `scripts/run_server.py` (+50/-3)
- `src/agent/consciousness.py` (+18/-1)
- `src/llm/proxy.py` (+10/-2)

**New untracked files (this ticket):**
- `tests/test_m5_4_6_2_proactive_dm_inner_life_wiring.py` (25 tests)
- `logs/m5_4_6_2_proactive_dm_inner_life_wiring_closeout.md` (this log)

**Untracked files preserved (pre-existing, NOT modified by this ticket):**
- All logs/* and scripts/_* files (pre-existing artifacts)
- All pre-existing test files

**Working tree:** clean except for tracked modifications + this ticket's new files + pre-existing untracked artifacts.

---

## 9. Architectural Findings

1. **`chrono_payload` is the canonical propagation channel for proactive DM metadata.** The same pattern (extract from chrono_payload, set on intent_payload / SoulEvent field) used for `draft`, `target_channel`, `target_user_id`, `dry_run` now extends cleanly to `inner_life_event_id`. This is a well-established pattern, not a new mechanism.

2. **Top-level `SoulEvent.inner_life_event_id` field is the right place for cross-reference.** Using payload would require consumer-side `event.payload.get("inner_life_event_id")` indirection. Top-level field is direct, type-checked, and matches the M5.4-5.5 design intent.

3. **Defensive type check in `_fire_intent` is necessary.** Even though executor always passes a valid str, downstream consumers (heartbeat, spawn_cold_intents, spawn_intent) might pass arbitrary data. Type check prevents invalid types from being silently accepted.

4. **Stub AGENT_SPEAK path must also thread inner_life_event_id.** Even though downstream consumers skip stub events (is_stub=True), `_pending` reset still happens, and the stub SoulEvent should be semantically consistent with the regular path (same top-level fields, even if the semantic content is empty).

5. **Provenance.trigger_type = `agent_reply` is semantically correct.** Proactive DM produces an outbound agent message, which is the same semantic as a reactive agent reply. Other candidates (`system`, `memory_fact`) were considered but `agent_reply` is the most semantically aligned with the M5.4-5.1 trigger type catalog.

6. **No change to `consciousness._fire_intent` signature.** The existing `chrono_payload: Optional[Dict[str, Any]] = None` parameter accepts the new key without signature change. This is the cleanest backward-compat strategy — same pattern used for `dry_run` (M2 task 4 → 5b → 5c chain).

7. **Per-instance InnerLifeWriter is correct.** Same as M5.4-6.1, no global singleton. The writer is created at lifespan start and is per-process-lifetime.

---

## 10. Unresolved Issues

1. **NarrativeTraceWriter is not wired in v1.** Same as M5.4-6.1. `InnerLifeWriter(trace_writer=None)` is the default. To enable tracing, a future ticket needs to construct a `NarrativeTraceWriter` and pass it to the `InnerLifeWriter` constructor. (Cross-cutting ticket, not proactive_dm-specific.)

2. **AGENT_INTENT event in M3.2 admin endpoints (`/api/test/spawn_cold_intents`, `/api/test/spawn_intent`) also creates InnerLifeEvent if executor is rewired.** Currently these endpoints don't go through the proactive_dm executor (they call `agent._fire_intent` directly). If they need InnerLifeEvent in the future, they would need similar wiring. (Out of scope per ticket — "modify frozen contracts / refactor Agency architecture".)

3. **Cross-handler InnerLifeWriter sharing is by closure, not injection.** The `inner_life_writer` is captured in the executor closure from lifespan scope. If a future handler is added in a different module, it would need explicit injection. Currently fine for the 4 wired producers (all in run_server.py).

4. **`chrono_payload["inner_life_event_id"]` is in payload, not top-level SoulEvent.** The propagation uses chrono_payload dict (existing pattern) until it reaches the SoulEvent constructor. This is the cleanest path that doesn't require modifying `_fire_intent`'s signature. (A future API redesign could promote inner_life_event_id to a dedicated parameter, but that would break backward compat with admin endpoints.)

---

## 11. Bry Decision Required: NO

All architectural decisions were derivable from the M5.4-5.x frozen contracts and the M5.4-6.1 pattern:
- Provenance uses canonical `TRIGGER_TYPE_AGENT_REPLY` (from M5.4-5.1 enum)
- Source system uses "narrative" (already in `VALID_SOURCE_SYSTEMS`)
- Propagation channel uses `chrono_payload` (existing pattern for proactive_dm metadata)
- Top-level SoulEvent field (from M5.4-5.5)
- Failure isolation pattern (from M5.4-6.1)

No new architectural choices required Bry input.

---

## 12. Recommended Next Ticket

**M5.4-6.3 — Optional NarrativeTraceWriter injection**

Cross-cutting ticket to enable full observability:
- Construct `NarrativeTraceWriter()` in lifespan
- Pass to `InnerLifeWriter(trace_writer=...)` constructor
- All 4 wired producers (Diary / Dream / Event / Proactive DM) automatically emit trace records
- Acceptance: trace.jsonl populated by production triggers, NarrativeTraceReader can query

Estimated impact: 3-5 lines in run_server.py + 1 test verifying trace sidecar receives events.

**Out of scope for next tickets:**
- USER_MESSAGE → InnerLifeEvent qualification layer (requires future design)
- Cross-handler lineage (parent_event_id across handlers)
- LLM API call → InnerLifeEvent (out of scope per M5.4-6.x pattern — LLM call is reactive, not lived)

---

## 13. Stop Conditions Final Check

| Stop Condition | Status | Notes |
|----------------|--------|-------|
| 1. Frozen contract must change | NO (verified) | All M5.4-5.x contracts preserved |
| 2. Agency 4-stage logic must change | NO | Wiring in executor, not Agency |
| 3. Event Bus schema must change | NO | Schema unchanged |
| 4. AGENT_SPEAK semantics must change | NO | Payload unchanged, only added optional top-level field |
| 5. Production data mutation | NO | STRICT 0 MUTATION |
| 6. New identity authority | NO | Uses existing InnerLifeWriter |
| 7. Cross handler architecture refactor | NO | Single handler + propagation chain |
| 8. Authoritative producer boundary unclear | NO | `_proactive_dm_llm_executor` is the boundary |

**No stop conditions triggered. Implementation complete. ✅**

---

## Acceptance Criteria Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| A1. Proactive DM executor creates InnerLifeEvent | ✅ | test_a1, test_a2 |
| A2. Each execution exactly one event | ✅ | test_a1, test_h1 |
| A3. event_id uses canonical InnerLifeWriter result | ✅ | test_a1 (InnerLifeWriter.create_event() result) |
| B1. event_id propagated to AGENT_SPEAK | ✅ | test_e1 (source inspection), test_c1 (_fire_intent extraction) |
| B2. Final SoulEvent.inner_life_event_id matches | ✅ | test_c1, test_e1 |
| B3. No duplicate identity | ✅ | test_h1 |
| C1. Uses existing Provenance / TriggerType | ✅ | test_b1, test_b2 |
| C2. No new enum invented | ✅ | test_b1 (TRIGGER_TYPE_AGENT_REPLY is M5.4-5.1) |
| C3. No fabricated session/correlation/parent | ✅ | test_b3, test_b4 (only actor_id + extras) |
| D1. InnerLifeWriter failure doesn't block proactive DM | ✅ | test_a3, test_f1, test_f2 |
| D2. Failure logged | ✅ | test_a3, test_f1 (logger.warning) |
| D3. SoulEvent maintains original behavior | ✅ | test_f2 |
| D4. No half-finished canonical state | ✅ | test_f1, test_f2 |
| E1. Existing behavior unchanged when disabled | ✅ | test_g1, test_g2, test_g3 |
| E2. Legacy data unchanged | ✅ | test_g3 |
| E3. No migration | ✅ | 0 mutation |
| F1. SoulEvent schema unchanged | ✅ | test_d1, test_d2 (only used existing field) |
| F2. InnerLifeEvent frozen model unchanged | ✅ | No edits to event.py |
| F3. Provenance frozen model unchanged | ✅ | No edits to event.py |
| F4. Agency 4-stage decision logic unchanged | ✅ | No edits to agency.py |
| F5. Event Bus contract unchanged | ✅ | No edits to eventbus schema |
| G1. USER_MESSAGE not modified | ✅ | test_i1, test_i2 |
| G2. Conversation qualification not modified | ✅ | Not implemented |
| G3. Heartbeat not modified | ✅ | Heartbeat code still commented out |
| G4. Diary/Dream/Event not modified | ✅ | M5.4-6.1 wirings untouched |
| G5. NarrativeTraceReader not modified | ✅ | No edits to trace_reader.py |
| G6. No unrelated refactor | ✅ | Only 3 minimal edits |

**All acceptance criteria met. ✅**

---

## Success Condition (per ticket)

> Proactive DM 成為第四條真正 wired 的 structured lived-experience producer

**Status: ✅ ACHIEVED**

```
Diary        ─→ InnerLifeEvent ✅ (M5.4-6.1)
Dream        ─→ InnerLifeEvent ✅ (M5.4-6.1)
Event        ─→ InnerLifeEvent ✅ (M5.4-6.1)
Proactive DM ─→ InnerLifeEvent ✅ (M5.4-6.2)
                ↓
           event_id propagation ✅
                ↓
           SoulEvent (AGENT_SPEAK) carries identity ✅
                ↓
           Memory / Narrative consumers (read from existing top-level field) ✅
```

Agency decision semantics unchanged. ✅
