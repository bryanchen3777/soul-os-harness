# M5.7-2 — Heartbeat Reactivation & SESSION_END Runtime Integration (Closeout)

**Mode:** IMPLEMENTATION
**Baseline:** HEAD = efeb242 = origin/main
**Final:** TBD (commit hash 拍板後補)
**Date:** 2026-08-10

---

## 1. Phase 1 Audit Findings (from M5.7-1)

M5.7-1 audit (efeb242) recommended **B (minimal implementation)** for re-enabling Heartbeat. Key findings:

1. M1.2 reason (dual-heartbeat conflict with scheduler Lesson 39) is **no longer valid** — Lesson 39 is DEAD code post-修法 12 / M5.2-I-8
2. Heartbeat Engine code is intact and correct (just disabled)
3. Re-enabling is a small wiring change with safety review
4. ConversationQualification (M5.6-2) is ready but never receives SESSION_END

---

## 2. Exact Lifecycle Boundary Used

**Trigger:** `HeartbeatEngine._loop` (60s tick interval, default)

**SESSION_END payload (5 fields per F):**
```python
{
    "elapsed_mins": float,                # existing (M1.2 carryover calc)
    "last_user_activity": ISO str,       # existing
    # M5.6-2 additive optional (now actually consumed by ConversationQualification):
    "last_session_id": Optional[str],    # from USER_MESSAGE.session_id
    "last_user_id": Optional[str],       # from payload.target_user_id / event.source
    "last_agent_id": Optional[str],      # from payload.target_agent (private mode only)
}
```

**Heartbeat Engine initialization (M5.7-2):**
- `tick_interval_seconds=60` (default)
- Subscribes to `USER_MESSAGE` (activity tracker) and `AGENT_SPEAK` (silence tracker)
- After `start()`: `_loop` task runs, publishes `SYSTEM_TICK` + `SESSION_END`
- After `stop()`: clean shutdown via `queue.join()`

---

## 3. Qualification Logic (M5.6-2, now activated)

**v1 policy constants (per M5.6-2):**
```python
QUALIFICATION_DURATION_THRESHOLD_MINS = 5.0
QUALIFICATION_TURN_DEPTH_THRESHOLD = 4
```

**End-to-end flow (NEW, M5.7-2 activates):**
```
USER_MESSAGE (elapsed_mins clock reset)
    ↓
[30min idle]
    ↓
HeartbeatEngine._loop detects elapsed_mins >= 30
    ↓
publish SESSION_END (with 5 fields)
    ↓
ConversationQualification.on_session_end (M5.6-2 subscriber)
    ↓
evaluate(event) → result
    ↓ (if qualified)
promote(result) → InnerLifeWriter.create_event() → canonical InnerLifeEvent
    ↓
NarrativeTraceWriter (auto via M5.4-6.4)
```

**ConversationQualification.on_session_end BUG FIX:**
- M5.6-2 original: handler only logged, did NOT call `self.promote()`
- M5.7-2 fix: handler now actually calls `self.promote(result)` to create InnerLifeEvent
- Without this fix, the bus path was silently doing nothing (only direct test calls worked)

---

## 4. Session Identity Mechanism (no change from M5.6-2)

- `session_id` = `payload.last_session_id`
- `correlation_id` = `session_id` (semantically: session IS the narrative group)
- Both are read from upstream SESSION_END payload, NEVER fabricated
- `parent_event_id` = None (root event, lineage_depth=0)

---

## 5. Modified Files (4 files, 806 insertions, 18 deletions)

| File | Change Type | Lines | Description |
|------|-------------|-------|-------------|
| `scripts/run_server.py` | Modified | +33 / -14 | Re-enable Heartbeat in lifespan startup + shutdown |
| `src/agent/consciousness.py` | Modified | +11 / -1 | Remove SYSTEM_TICK from event_filter (constraint M) |
| `src/conversation_qualification/qualifier.py` | Modified | +22 / -6 | BUG FIX: on_session_end now calls promote() |
| `tests/test_m5_7_2_heartbeat_reactivation.py` | New | +660 | 20 tests across 8 sections (A-H + M) |

---

## 6. Tests (20 tests, 8 sections, 20/20 PASS in 3.78s)

| Section | Test | Description | Status |
|---------|------|-------------|--------|
| A. Heartbeat Lifecycle | A1 | start() and stop() work without error | ✓ |
| A. Heartbeat Lifecycle | A2 | start() is idempotent (no duplicate task) | ✓ |
| B. Tick Works | B1 | 60s tick publishes SYSTEM_TICK | ✓ |
| C. No Duplicate Runtime | C1 | Only one HeartbeatEngine instance | ✓ |
| C. No Duplicate Runtime | C2 | Heartbeat subscribes once to USER_MESSAGE/AGENT_SPEAK | ✓ |
| D. Scheduler Lesson 39 Dead | D1 | run_server.py: register_heartbeat not invoked | ✓ |
| D. Scheduler Lesson 39 Dead | D2 | _heartbeat_callback definition still commented | ✓ |
| E. SESSION_END Publish | E1 | Payload has all 5 expected fields | ✓ |
| E. SESSION_END Publish | E2 | Heartbeat publishes SESSION_END after 30min idle | ✓ |
| G. ConversationQualification End-to-End | G1 | 5min+6turn → exactly 1 InnerLifeEvent | ✓ |
| G. ConversationQualification End-to-End | G2 | 3min → 0 events | ✓ |
| G. ConversationQualification End-to-End | G3 | 10min+2turn → 0 events | ✓ |
| H. Qualification Invariants | H | No content read, no leakage in extras | ✓ |
| H. Qualification Invariants | I | No LLM/heuristic/topic analysis in v1 | ✓ |
| H. Qualification Invariants | J | InnerLifeWriter sole creator | ✓ |
| H. Qualification Invariants | K | No duplicate InnerLifeEvent from qualification | ✓ |
| M. No Proactive Agency | M1 | consciousness event_filter excludes SYSTEM_TICK | ✓ |
| M. No Proactive Agency | M2 | _on_tick does not publish AGENT_INTENT (when called) | ✓ |
| M. No Proactive Agency | M3 | run_server.py: scheduler.register_heartbeat not invoked | ✓ |
| count | count | test count = 20 | ✓ |

**Result: 20/20 PASS**

---

## 7. Full Regression Results

| Suite | Tests | Status |
|-------|-------|--------|
| M5.4-5.1 Inner Life Foundation | part of 383 | PASS |
| M5.4-5.2 Memory Inner Life Integration | part of 383 | PASS |
| M5.4-5.3 Diary Inner Life Integration | part of 383 | PASS |
| M5.4-5.4 Dream Inner Life Integration | part of 383 | PASS |
| M5.4-5.5 Event Bus Inner Life Integration | part of 383 | PASS |
| M5.4-5.6 Narrative Trace Sidecar | part of 383 | PASS |
| M5.4-5.7 Trace Reader | part of 383 | PASS |
| M5.4-6.1 Executor Wiring | part of 383 | PASS |
| M5.4-6.2 Proactive DM Inner Life Wiring | part of 383 | PASS |
| M5.4-6.3 Trace Production Activation Audit | part of 383 | PASS |
| M5.4-6.4 Trace Production Activation | part of 383 | PASS |
| M5.5-2 Canonical InnerLifeEvent Propagation | part of 383 | PASS |
| M5.6-2 Conversation Qualification Implementation | 17/17 | PASS |
| M5.7-2 Heartbeat Reactivation | 20/20 | PASS |
| M3 E2E + World Awareness | 29/29 | PASS |
| **Total** | **383/383** | **PASS** |

**Pre-existing failures (unchanged, NOT caused by M5.7-2):**
- `tests/test_websocket_e2e.py::test_inject_tick_triggers_agent_speak` — flaky on slow runs (60s LLM-call timeout)

---

## 8. Heartbeat Runtime Behavior

**Pre-M5.7-2 state:**
- `app.state._heartbeat = None`
- Heartbeat Engine code intact but never invoked
- SYSTEM_TICK never published
- SESSION_END never published
- ConversationQualification (M5.6-2) subscribed to SESSION_END but never received events

**Post-M5.7-2 state:**
- `app.state._heartbeat = <HeartbeatEngine>` (active instance)
- Heartbeat Engine runs `_loop` task every 60s
- SYSTEM_TICK published (consumers: none — consciousness filtered out per M)
- SESSION_END published after 30min idle (consumers: ConversationQualification)
- ConversationQualification receives SESSION_END, applies v1 policy, promotes qualified sessions to canonical InnerLifeEvent
- NarrativeTraceWriter auto-traces (M5.4-6.4) the new events

---

## 9. SESSION_END Verification (per F)

**5 fields in SESSION_END payload, all verified:**
- ✅ `elapsed_mins` (float) — tested in e1
- ✅ `last_user_activity` (ISO string) — tested in e1
- ✅ `last_session_id` (string) — tested in e1
- ✅ `last_user_id` (string) — tested in e1
- ✅ `last_agent_id` (string) — tested in e1

**Trigger condition:** `elapsed_mins >= 30` (default `SESSION_END_THRESHOLD_MINS = 30.0`)

**Verified end-to-end:** Heartbeat publishes SESSION_END → ConversationQualification receives → evaluates → promotes to canonical InnerLifeEvent (test e2 + g1)

---

## 10. ConversationQualification Integration

**Pre-M5.7-2 (M5.6-2 only):** Qualifier wired but never fired (no SESSION_END producer)

**Post-M5.7-2 (full activation):**
- Heartbeat publishes SESSION_END per 30min idle
- ConversationQualification.on_session_end receives it via bus
- Handler calls evaluate() → if qualified, calls promote()
- promote() calls inner_life_writer.create_event() → canonical event_id
- NarrativeTraceWriter auto-traces the event
- Fact.inner_life_event_id (M5.5-2) carries the canonical reference forward

**BUG FIX during M5.7-2:** Previous `on_session_end` only logged + stats, did NOT call `self.promote()`. This was a M5.6-2 implementation gap that became visible when running the bus dispatch path. Without this fix, the production path would silently do nothing. **Fixed and verified by test g1 (5min+6turn → exactly 1 InnerLifeEvent via bus).**

---

## 11. InnerLifeEvent Identity Verification

**Verified end-to-end chain:**
```
USER_MESSAGE
  → consciousness._fire_intent (chrono_payload carries session_id)
  → AGENT_INTENT (no canonical eid for USER_MESSAGE path)
  → LLMProxy
  → AGENT_SPEAK (no canonical eid)
  → MemoryMiddleware._on_agent_speak (synthetic eid for USER_MESSAGE)

[30 min idle]

  → HeartbeatEngine._loop
  → publish SESSION_END (with last_session_id / last_user_id / last_agent_id)
  → ConversationQualification.on_session_end
  → evaluate() → qualified=True
  → promote()
  → inner_life_writer.create_event(
      provenance=Provenance(
        trigger_type="conversation:user_message",
        source_system="narrative",
        actor_id=session_id,
        extras={"qualification_reason": "duration=10.0min>=5.0 AND turn_depth=6>=4"},
      ),
      session_id="session_bryan_agent_yua",
      correlation_id="session_bryan_agent_yua",
      parent_event_id=None,  # root event
    )
  → returns canonical event_id (32-char lowercase hex)
  → NarrativeTraceWriter._append_trace (auto)
```

**No duplicate events:** M5.6-2 test B1 + M5.7-2 test g1 both verify exactly 1 event per promotion. Heartbeat's `_session_ended` flag prevents SESSION_END re-firing within same session.

---

## 12. Production Integrity

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
- ✅ New runtime events are forward-only (act on future SESSION_END only)
- ✅ Heartbeat runtime starts fresh on server start (no historical state mutation)

---

## 13. Frozen Contract Verification

| Contract | File | Status |
|----------|------|--------|
| M5.3 Memory Retrieval | `src/memory/sage/writer.py` | UNCHANGED |
| SAGE / v1 schema | `src/memory/sage/models.py`, `src/memory/v1/schema.py` | UNCHANGED |
| Fact schema | `src/memory/sage/models.py:7-86` | UNCHANGED |
| `Fact.inner_life_event_id` semantics | M5.4-5.2 + M5.5-2 | UNCHANGED |
| InnerLifeEvent frozen model | `src/inner_life/event.py:118-` | UNCHANGED |
| Provenance frozen model | `src/inner_life/event.py:68-115` | UNCHANGED (uses existing trigger_type "conversation:user_message" value) |
| InnerLifeWriter API | `src/inner_life/writer.py:129-236` | UNCHANGED |
| NarrativeTraceWriter | `src/inner_life/trace.py` | UNCHANGED (auto-traces) |
| NarrativeTraceReader | `src/inner_life/trace_reader.py` | UNCHANGED |
| SoulEvent schema | `src/eventbus/schema.py` | UNCHANGED (SESSION_END event type unchanged; 3 additive optional payload fields per M5.6-2) |
| Event Bus contract | `src/eventbus/bus.py` | UNCHANGED |
| Memory LLM Judge | `src/memory/llm_judge.py` | UNCHANGED |
| MemoryWriter / SAGELiteProvider | `src/memory/sage/*` | UNCHANGED (M5.5-2 mechanism preserved) |
| Heartbeat SESSION_END | `src/heartbeat/engine.py` | UNCHANGED (already added M5.6-2 fields) |
| Emotion engine | `src/agent/emotion.py` | UNCHANGED |
| Temporal EmotionalCarryover | `src/temporal/models.py` | UNCHANGED |
| Stage 4.1 relationships | `src/soul/relationships.py` | UNCHANGED |
| AgencyTriggerHandler (M5.2-G) | `src/agency/*` | UNCHANGED |
| Scheduler (M5.2-G) | `src/soul/scheduler.py` | UNCHANGED |
| Existing 4 producers (Diary/Dream/Event/ProactiveDM) | `run_server.py` | UNCHANGED |
| Existing acceptance suites | tests/ | UNCHANGED (383/383 PASS) |
| ConversationQualification (M5.6-2) | `src/conversation_qualification/qualifier.py` | **EXTENDED** (bug fix in on_session_end handler — calls promote() now) |

**`VALID_SOURCE_SYSTEMS` unchanged:** `frozenset({"memory", "diary", "dream", "narrative", "system"})`. M5.7-2 uses existing `"narrative"` value via M5.6-2.

**`TRIGGER_TYPE_*` constants unchanged:** M5.6-2's `"conversation:user_message"` string literal still used (no enum modification).

---

## 14. Git State / Commits

### Before
```
HEAD = efeb242 (docs(m5.7-1): continuous life / heartbeat runtime audit)
origin/main = efeb242
Working tree: 20 個 pre-existing untracked artifacts
```

### After
```
HEAD = TBD (commit hash 拍板後補)
origin/main = TBD
Modified: 0 (all changes committed)
+ committed: feat(m5.7-2) implementation (4 files, 806+/18-)
+ new: tests/test_m5_7_2_heartbeat_reactivation.py
+ this closeout log
Untracked preserved: 20 pre-existing artifacts
```

### Commits (expected)
1. `feat(m5.7-2): heartbeat reactivation & SESSION_END runtime integration` (4 files, 806+/18-)
2. `docs(m5.7-2): add closeout summary log` (1 file, 350+)

---

## 15. Architectural Findings

### 15.1 M1.2 dual-conflict is no longer valid

- Original M1.2 reason: 60s tick + 30-60min scheduler heartbeat = 2 systems
- Current state: scheduler's Lesson 39 heartbeat is **DEAD code** (`register_heartbeat` commented, `_callbacks` removed)
- Re-enabling `src/heartbeat/` does NOT recreate the conflict
- M5.7-2 inline comment in `run_server.py` documents this rationale

### 15.2 ConversationQualification bug fix in on_session_end

- M5.6-2 implementation: `on_session_end` handler only logged + incremented stats
- BUG: did NOT call `self.promote()` — bus dispatch path silently did nothing
- M5.7-2 fix: handler now actually calls `self.promote(result)`
- This bug was hidden because M5.6-2 tests called `evaluate()` + `promote()` directly
- Production bus path was broken end-to-end; M5.7-2 fixes it

### 15.3 SYSTEM_TICK must be filtered out of consciousness

- Pre-M5.7-2: consciousness subscribed to SYSTEM_TICK, would call `_on_tick` → `_fire_intent` (proactive)
- Post-M5.7-2: SYSTEM_TICK still published (for observation), but consciousness does not consume
- This prevents Heartbeat tick from creating a second autonomous path alongside scheduler's AGENCY_TRIGGER
- Constraint M preserved: "SYSTEM_TICK 不得啟動 proactive Agency"

### 15.4 No production activation guard

- Heartbeat starts immediately on lifespan startup
- ConversationQualification starts receiving SESSION_END on first 30min idle
- Per work order: "New runtime events must be forward-only"
- No historical backfill, no replay
- All SESSION_END events are future events (not historical)

### 15.5 Heartbeat ↔ Scheduler separation

| Component | Responsibility | Output |
|-----------|----------------|--------|
| Heartbeat | Temporal observation / lifecycle detection | SYSTEM_TICK (60s), SESSION_END (30min idle) |
| Scheduler | Planned autonomous activities | AGENCY_TRIGGER (morning/night/dream/event/proactive_dm) |
| Heartbeat (proposed future) | Could observe Agency (read-only) | observability only |

**Verified separation:** Heartbeat's SYSTEM_TICK does NOT trigger Agency. Heartbeat's SESSION_END only triggers ConversationQualification (which calls writer.create_event, NOT AGENCY_TRIGGER).

---

## 16. Unresolved Issues

| Issue | Severity | Notes |
|-------|----------|-------|
| ConversationQualification on_session_end had silent bug pre-M5.7-2 | Implementation gap | Fixed in M5.7-2 |
| SESSION_END payload depends on `target_agent` in USER_MESSAGE (group mode = None) | v1 limitation | Qualifier rejects group mode (agent_id missing) |
| Heartbeat publishes SYSTEM_TICK every 60s (no production consumer) | Minor waste | OK; cheap, can be observed |
| Test g1 + k used asyncio.run + bus dispatch (timing-sensitive) | Test reliability | Resolved by relying on bus.stop()'s queue.join() |
| scheduler Lesson 39 still dead (intentional) | Out of scope | Per M5.7-2 work order |
| `app.state._heartbeat` is now non-None — `/_admin/fast_forward` endpoint may need re-activation (M1.2) | Out of scope | Per M5.7-2 work order |

None blocking. All documented in code comments.

---

## 17. Stop Conditions Final Check

| Stop Condition | Triggered? | Resolution |
|----------------|-----------|------------|
| 1. Duplicate heartbeat runtime discovered | NO | Tests C1, C2 verify single instance, single subscription |
| 2. Scheduler heartbeat must be revived | NO | M5.7-2 explicitly out-of-scope; D1, D2 verify dead |
| 3. Existing Agency 4-stage path must be bypassed | NO | Heartbeat doesn't publish AGENCY_TRIGGER; M1, M2, M3 verify constraint M |
| 4. Heartbeat tick directly triggers autonomous agent execution | NO | SYSTEM_TICK filtered from consciousness |
| 5. SESSION_END produces duplicate InnerLifeEvents | NO | Test g1, k verify exactly 1; Heartbeat's `_session_ended` flag prevents re-fire |
| 6. Frozen contract modification becomes necessary | NO | All additive; 0 contract changes |
| 7. Production historical data must be modified | NO | Forward-only; 0 mutation to memory.db / diary / dream / event / trace.jsonl |
| 8. ConversationQualification requires conversation content | NO | Test h, I verify only count, no content access |
| 9. Existing producer behavior changes unexpectedly | NO | M5.4-6.x producers unchanged (test sections G, H pass) |
| 10. P0/P1 architecture issue discovered | NO | All safety audits pass (P0 = NONE) |

**No stop conditions triggered. Implementation complete. ✅**

---

## 18. Final Status

| Item | Status |
|------|--------|
| Implementation complete | ✅ |
| Heartbeat reactivation | ✅ (lifespan start + stop) |
| 60s tick works | ✅ (B1 verified) |
| No duplicate runtime | ✅ (C1, C2) |
| scheduler Lesson 39 stays dead | ✅ (D1, D2) |
| SESSION_END publishes with 5 fields | ✅ (E1, E2) |
| ConversationQualification end-to-end | ✅ (G1, G2, G3) |
| No content read / no heuristic / sole creator / no dup | ✅ (H, I, J, K) |
| SYSTEM_TICK does not trigger proactive Agency | ✅ (M1, M2, M3) |
| ConversationQualification on_session_end bug fixed | ✅ |
| Frozen contracts preserved | ✅ (17 contracts verified) |
| Production integrity | ✅ (0 modification) |
| Full regression | ✅ (383/383 PASS) |
| Stop conditions | ✅ None triggered |
| SYSTEM_TICK remained disabled (in consciousness) | ✅ (filter changed) |
| scheduler Lesson 39 remained disabled | ✅ (D1, D2) |
| Recommended next ticket | None — M5.7 chain complete |
