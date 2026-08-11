# M5.14-2 — WorldEvent ↔ ProactiveDM Identity Contract Reconciliation Audit

**Ticket**: M5.14-2 (Bry 派工 2026-08-11)
**Mode**: READ-ONLY DESIGN / CONTRACT AUDIT
**Baseline**: HEAD = `d34513e` (M6.0-3) | origin/main = `d34513e` (synced)
**Auditor**: Mavis (M3) for Bry
**Date**: 2026-08-11 18:37 EDT

---

## 0. Audit Charter

Bry 派工 2026-08-11 18:37 EDT:
> "Trace the complete identity path and establish the canonical
> identity semantics before any implementation decision."
> "Determine whether this is:
> A. a real production contract conflict,
> B. an intentional identity boundary,
> C. a derivable identity that already exists elsewhere,
> D. a test/fixture-only mismatch.
> Do NOT assume F1 is a bug."

M6.0-3 F1-P1 finding (carried from M6.0-3 closeout):
- M5.8-4: `gate_proactive_dm(agent_id: str)` requires non-empty agent_id
- M5.9-3: production WorldEvent InnerLifeEvent has `actor_id=None`
- M6.0-3 could only validate Scenario F by supplying `agent_id="agent_yua"` + `actor_id="agent_yua"` in fixture (which contradicts M5.9-3 production semantic)

---

## 1. Identity Trace

### 1.1 WorldEvent identity (per `src/world/perception.py`)

**WorldEvent dataclass fields** (L64-70):
```python
source: str                     # "weather" | "news" | "calendar" | "social" | "synthetic"
type: str                       # 細分類型 e.g. "rain_started", "calendar_event"
novelty_id: str                 # 同一事實識別 (去重 key)
ts: str                         # ISO 8601 UTC timestamp
summary: str                    # 一句話客觀描述
data: Dict[str, Any] = field(default_factory=dict)
priority: int = 0               # M3.1 Phase B 新增, 預設 0
```

**NO `actor_id` field.** WorldEvent is canonical pure data structure with no agent identity at all.

**Source metadata**:
- `source`: "weather" | "news" | "calendar" | "social" | "synthetic" (5 valid values per `VALID_SOURCES` L46)
- `data`: free-form dict that source can put anything in. **No canonical agent identity signal.**

**Synthetic test data example** (TEST_C_calendar_event_30min, `src/world/source/synthetic.py`):
```python
{
    "source": "calendar",
    "type": "calendar_event",
    "novelty_id": "calendar_meeting_20260807_1500",
    "summary": "30 分鐘後有重要會議",
    "data": {"event_name": "重要會議", "minutes_until": 30},
    # ↑ data has event_name + minutes_until, NO actor
}
```

**Verification** (M5.9-2 §121):
> "WorldEvent 沒有 `agent_id` 欄位 (M5.9-1 §5.2 confirmed), 沒辦法知道哪個 Soul 跟 world event 相關。"

### 1.2 Event Bus propagation (per `src/eventbus/schema.py`)

**`EventType.WORLD_EVENT` payload** (L230-243):
```python
# EventType.WORLD_EVENT payload:
{
    "source": str,             # "weather" | "news" | "calendar" | "social" | "synthetic"
    "type": str,               # "rain_started" | "celebrity_news" | "calendar_event" | ...
    "novelty_id": str,         # 同一事實的識別
    "ts": str,                 # ISO 8601 UTC
    "summary": str,            # 一句話描述
    "data": dict,              # 結構化 payload (隨 source/type 變動)
}
```

**NO `actor_id` field** at the bus level.

**SoulEvent top-level** (L77-):
- `source`: sender ID (e.g. "system", "user_bryan", "agent_ruka", "heartbeat", "soul_scheduler", "synthetic")
- `target`: recipient (broadcast or specific agent_id)

For WORLD_EVENT: `source="synthetic"` (per middleware L477), `target="broadcast"`. **The bus publishes to all subscribers, not to a specific agent.**

**`to_payload()` / `from_payload()`** (perception.py L85-128):
- `to_payload()` returns `{source, type, novelty_id, ts, summary, data, priority}` (M5.4-3.1 added priority)
- `from_payload()` reads back same 7 fields
- **No actor_id preservation, transformation, or extraction.** None exists.

**Dispatcher path** (perception.py + dispatcher.py):
- `SyntheticWorldEventSource.emit_event()` builds WorldEvent with `source=source_id` (e.g. "synthetic")
- `WorldEventDispatcher.emit_and_inject(source_id, type, summary, novelty_id, data, priority)` — **no agent_id parameter at all**
- Dispatcher propagates WorldEvent to bus via injector (L175-)

**Verification** (L181):
```python
async def emit_and_inject(
    self,
    source_id: str,
    type: str,
    summary: str,
    novelty_id: str,
    data: Optional[Dict[str, Any]] = None,
    priority: int = 0,
) -> WorldEvent:
    # NO agent_id parameter
```

### 1.3 World → Inner Life (per `src/world/inner_life_adapter.py`)

**`WorldInnerLifeAdapter._create_inner_life_event`** (L363-389):
```python
def _create_inner_life_event(self, world_event: WorldEvent) -> InnerLifeEvent:
    """
    Per M5.9-2 spec §6:
      - trigger_type = "world:<type>"
      - actor_id = None
      - source_system = "narrative"
      - extras = {world_source, world_type, world_novelty_id}
      - session_id / correlation_id / parent_event_id = None
    """
    return self._writer.create_event(
        provenance=Provenance(
            trigger_type=f"world:{world_event.type}",
            actor_id=None,                              # ← explicit
            source_system="narrative",
            trace_ref=None,
            extras={
                "world_source": str(world_event.source),
                "world_type": str(world_event.type),
                "world_novelty_id": str(world_event.novelty_id),
            },
        ),
        session_id=None,
        correlation_id=None,
        parent_event_id=None,
    )
```

**`actor_id = None` is M5.9-2 spec §6 EXPLICIT DECISION**, not lazy default.

**Identity preservation**:
- Original WorldEvent identity preserved in `provenance.extras.{world_source, world_type, world_novelty_id}`
- No agent identity ever attached to world events
- This is the canonical "system-level observation" identity, distinct from agent-specific identities

**InnerLifeEvent identity for world events** (final, per `event_to_dict`):
```json
{
  "event_id": "32hex",
  "session_id": null,
  "correlation_id": null,
  "parent_event_id": null,
  "ts": "ISO 8601",
  "provenance": {
    "trigger_type": "world:calendar_event" | "world:user_going_outside",
    "actor_id": null,                              ← KEY
    "source_system": "narrative",
    "trace_ref": null,
    "extras": {
      "world_source": "calendar" | "social" | ...,
      "world_type": "calendar_event" | ...,
      "world_novelty_id": "..."
    }
  },
  "lineage_depth": 0,
  "lineage_path": "..."
}
```

### 1.4 World → Agency / proactive DM path

**The path** (per `src/soul/scheduler.py` L177-330):

```
SoulScheduler._publish_agency_trigger(agent_id, trigger_type="proactive_dm")
    ↓
if trigger_type == "proactive_dm":
    gate_result = gate_proactive_dm(
        agent_id=agent_id,                  ← scheduler's authoritative agent_id
        now=datetime.now(timezone.utc),
        trace_reader=NarrativeTraceReader(),  ← reads trace.jsonl
    )
    if gate_result.decision == GateDecision.GATED:
        return  # skip publish
    ↓
bus.publish(AGENCY_TRIGGER, payload={trigger_type: "proactive_dm", agent_id: agent_id, ...})
    ↓
AgencyTriggerHandler → _proactive_dm_llm_executor(agent_id, trigger)
    ↓
inner_life_writer.create_event(provenance=Provenance(
    trigger_type=TRIGGER_TYPE_AGENT_REPLY,
    actor_id=agent_id,                       ← This is the SAME agent_id from scheduler
    source_system="narrative",
    ...
))
```

**`agent_id` at the gate** comes from:
- `scheduler._all_agents` (production: `["agent_yua", "agent_ruka", ...]`)
- Passed through `_publish_agency_trigger(agent_id, trigger_type)` parameter
- This is the scheduler's authoritative identity of "which agent is about to act"

**This `agent_id` is NOT derived from any world event.** It's the scheduler's per-agent scope.

**`_publish_agency_trigger` 4 call sites** (per M5.8-4.1 audit):
| Line | Trigger type | agent_id source |
|------|--------------|-----------------|
| L582 | dream | `dreamer` (passed to `_fire_dream`) |
| L653 | event | `agent_id` (from `agents` iteration) |
| L831 | proactive_dm | `agent_id` (from scheduler's per-agent loop) |
| L930 | morning/night | `agent_id` (from `self._all_agents` iteration) |

ALL 4 sites pass a real agent_id from the scheduler's authoritative per-agent state. **None come from world events.**

### 1.5 InnerLifeEvent actor_id patterns (full production picture)

Per `scripts/run_server.py` and existing executors:

| Producer | trigger_type | actor_id | source_system |
|----------|--------------|----------|---------------|
| `_proactive_dm_llm_executor` (L584-589) | `agent_reply` | `agent_id` | "narrative" |
| `_event_writer_executor` (L667-672) | `dream:event` | `agent_id` | "dream" |
| `_dream_writer_executor` (L728-733) | `dream:dream` | `dreamer` | "dream" |
| `_diary_writer_executor` (L812-817) | `diary:morning` / `diary:night` | `agent_id` | "diary" |
| `ConversationQualification` (L289) | `conversation:user_message` | `session_id` | "narrative" |
| `WorldInnerLifeAdapter` (M5.9-3) | `world:<type>` | **`None`** | "narrative" |

**6 producers, 6 distinct actor_id patterns**:
- 4 use `agent_id` (Soul-level: dream, event, diary, proactive_dm)
- 1 uses `session_id` (chat session encoded user_id)
- 1 uses **`None`** (world events, M5.9-2 spec §6 explicit decision)

**This is documented evidence that the existing production architecture ALREADY treats world events as agent-agnostic.**

---

## 2. M5.8-4 gate semantic

### 2.1 Gate filter rule (per `src/agency/inner_life_gate.py` L261):

```python
# Filter by agent_id via provenance.actor_id
agent_records = []
for r in all_records:
    if not isinstance(r, dict):
        continue
    provenance = r.get("provenance")
    if not isinstance(provenance, dict):
        continue
    if provenance.get("actor_id") == agent_id:    # ← KEY FILTER
        agent_records.append(r)
```

**The filter is `provenance.actor_id == agent_id` (strict string equality).**

### 2.2 Design intent (M5.8-4 closeout L100, L173):

> L100: "過濾 `provenance.actor_id == agent_id` (該 agent 自己的 events)" — explicitly "the agent's own events"
> L173: "Gate 不得 fabricate identity" — `last_event_id` is observed, not constructed
> L200: "Provenance frozen (M5.4-5.1) | ✗ NO | Gate 讀 `actor_id` 既存欄位" — Gate READS existing actor_id

### 2.3 Why this rule (M5.8-4 closeout §3 + §4):

> "proactive_dm 是 **Inner-Life-CONSUMER** (想打擾 user, 需要看 user 最近狀態)"
> "其他 4 個是 **Inner-Life-PRODUCER** (它們本身就是 inner life activity, 寫完才是 inner life)"

**Gate semantic: "if THIS agent has done personal inner work recently, don't proactively DM them"**

Personal inner work means: agent wrote diary, agent had dream, agent replied to user, agent noticed event. These all have `actor_id = <the agent>`.

**World events are NOT personal inner work.** They are external observations ("30-min meeting", "going outside"). They don't represent the agent doing anything. Setting `actor_id=None` for world events correctly says: "this inner life event was not caused by any specific agent's action."

### 2.4 Production semantic correctness check:

| InnerLifeEvent trigger_type | actor_id | Filter by `actor_id == "agent_yua"`? | Gate fires? |
|------------------------------|----------|---------------------------------------|-------------|
| `diary:morning` (by yua) | "agent_yua" | YES | GATES yua's proactive_dm |
| `dream:dream` (by yua) | "agent_yua" | YES | GATES yua's proactive_dm |
| `dream:event` (by yua) | "agent_yua" | YES | GATES yua's proactive_dm |
| `agent_reply` (yua replying) | "agent_yua" | YES | GATES yua's proactive_dm |
| `conversation:user_message` | "session_bryan_agent_yua" | NO (different key) | does NOT gate |
| `world:calendar_event` | **`None`** | **NO** | **does NOT gate (intentional)** |

**This is the correct production semantic.** World events are external facts; they should NOT gate a specific agent's proactive DM. The agent didn't do personal inner work just because a calendar event was noticed.

If world events DID gate, then:
- 30-min meeting notice would suppress proactive_dm for ALL agents (cross-cutting)
- "going outside" would suppress proactive_dm for ALL agents
- This would create a "global quiet period" that doesn't match the design intent of "agent-specific personal work"

---

## 3. Contract Matrix

| WorldEvent source/type | WorldEvent fields | Bus payload | WorldInnerLifeAdapter | InnerLifeEvent actor_id | Gate filter match for `agent_id="agent_yua"` | Production semantic |
|------------------------|-------------------|-------------|------------------------|-------------------------|----------------------------------------------|---------------------|
| calendar / `calendar_event` | source, type, novelty_id, ts, summary, data={event_name, minutes_until} | same 7 fields, source="synthetic", target="broadcast" | qualifies YES, creates event with `actor_id=None` | `null` | NO (None != "agent_yua") | external fact, not personal inner work → does NOT gate (intentional) |
| social / `user_going_outside` | source, type, novelty_id, ts, summary, data={actor: "..."} | same 7 fields | qualifies YES, creates event with `actor_id=None` | `null` | NO (None != "agent_yua") | external fact, not personal inner work → does NOT gate (intentional) |
| weather / `rain_started` | source, type, novelty_id, ts, summary, data={precipitation_mm, intensity} | same 7 fields | qualifies NO (type not in WORLD_QUALIFYING_TYPES) | N/A (no InnerLifeEvent created) | N/A | never becomes InnerLifeEvent |
| news / `celebrity_news` | source, type, novelty_id, ts, summary, data={celebrity, topic} | same 7 fields | qualifies NO (type not in WORLD_QUALIFYING_TYPES) | N/A | N/A | never becomes InnerLifeEvent |

**M5.9-3 `WORLD_QUALIFYING_TYPES = {"calendar_event", "user_going_outside"}` (per M5.9-2 spec §A.2)**

| InnerLifeEvent trigger_type | actor_id | Source of actor_id | Gate filter for `agent_id == "agent_yua"` | Production semantic |
|------------------------------|----------|---------------------|-------------------------------------------|---------------------|
| `diary:morning` / `diary:night` | "agent_yua" | scheduler's per-agent loop (L812-817) | YES | personal inner work → GATES |
| `dream:dream` | "agent_yua" (dreamer) | scheduler's _fire_dream (L731) | YES | personal inner work → GATES |
| `dream:event` | "agent_yua" | scheduler's _fire_event (L670) | YES | personal inner work → GATES |
| `agent_reply` (proactive_dm) | "agent_yua" | scheduler's _fire_proactive_dm (L587) | YES | personal inner work → GATES |
| `conversation:user_message` | "session_bryan_agent_yua" | ConversationQualification | NO (different key) | chat-session scoped, not agent-level |
| `world:calendar_event` / `world:user_going_outside` | `null` | M5.9-2 spec §6 explicit | NO | external observation, not personal → does NOT gate (intentional) |

---

## 4. Existing Canonical Identity Sources (M5.14-2 spec §6)

| Source | agent_id availability | Used for what |
|--------|----------------------|----------------|
| `WorldEvent` (dataclass) | **NO agent_id field at all** | per-type events only |
| `WorldEvent.data` (free-form dict) | inconsistent (4/5 scenarios have no actor key) | per-type payload only |
| `WorldEventDispatcher` (call-site) | **NO agent_id parameter** | dispatcher is agent-agnostic |
| `WorldPerceptionMiddleware._on_world_event` | reads `event.payload` only | validation + state, no agent context |
| `WorldInnerLifeAdapter._create_inner_life_event` | inherits from world_event (None) | M5.9-2 spec §6 explicit None |
| `InnerLifeEvent.provenance.actor_id` | 5 of 6 producers set; 1 sets None | M5.8-4 gate filter target |
| `scheduler._all_agents` (canonical) | list[str], "agent_yua" etc. | scheduler's per-agent identity |
| `scheduler._fire_*` (per-trigger) | per-agent from scheduler | `_publish_agency_trigger(agent_id, ...)` |
| `_proactive_dm_llm_executor(agent_id, trigger)` | receives from scheduler | canonical per-agent identity |

**Conclusion: WorldEvent has no agent identity at any layer. The only canonical agent identity source is the scheduler (L582/L653/L831/L930), and it always passes a real agent_id from `self._all_agents` (or equivalent).**

---

## 5. Severity Classification

### 5.1 F1-P1 assessment

| Question | Answer | Evidence |
|----------|--------|----------|
| Is F1-P1 a real production contract conflict? | **NO** | M5.8-4 gate filter is `actor_id == agent_id` (per agent's own events). M5.9-3 world events have `actor_id=None` (M5.9-2 spec §6 explicit, "system-level observations"). The two contracts are **mutually consistent**: M5.8-4 wants personal inner work; M5.9-3 says world events are not personal inner work. |
| Is the F1-P1 an intentional identity boundary? | **YES** | Both M5.8-4 (closeout L100 "該 agent 自己的 events") and M5.9-2 (§6.1 "World events are external observations, not soul-action") explicitly designed this. World events are correctly excluded from per-agent proactive_dm gating. |
| Is there a derivable identity that already exists elsewhere? | **NO** | WorldEvent has no agent_id field (M5.9-2 §121 confirmed). The 4 candidate options A/B/C were all rejected in M5.9-2 §6.1 (data.actor: inconsistent; call-site context: not shared; static: fabrication). No canonical mapping exists. |
| Is F1-P1 a test/fixture-only mismatch? | **YES (for M6.0-3 F1-F3)** | M6.0-3 F1-F3 tests used `agent_id=None` to "match all events" — this violates M5.8-4 contract. M6.0-3 fixed by setting `actor_id="agent_yua"` in fixture, which **also violates M5.9-3 production semantic** (where world events have `actor_id=None`). The validation contract between F1-F3 and the M5.x contracts is fundamentally misaligned. |

### 5.2 Severity

- **Production semantic**: INTENTIONAL, CORRECT, NO BUG
- **M5.8-4 + M5.9-3 contracts**: mutually consistent, no conflict
- **M6.0-3 F1-F3 validation**: TEST DESIGN issue, not contract issue
  - The test was trying to validate the gate's "30 min cooldown" semantic
  - But it used world events which are by design not eligible for the gate filter
  - The correct validation of the gate should use agent-specific events (diary:morning, dream:dream, etc.) where `actor_id == agent_id` matches the gate's filter naturally

**Severity: P3 — TEST DESIGN ISSUE, not P1 contract conflict.**

(F1-P1 was misclassified in M6.0-3 closeout. The P1 label is incorrect. The actual issue is a P3 test design problem in M6.0-3.)

---

## 6. Recommended Resolution

### 6.1 For M6.0-3 F1-F3 (M6.0-3 closeout reclassification)

**Option A (Recommended)**: Update M6.0-3 F1-F3 tests to use agent-specific events instead of world events.

```python
# Instead of:
#   fixture: actor_id="agent_yua"  (artificial, violates M5.9-3)
#   gate(agent_id="agent_yua")    (works but mismatched)

# Use:
#   fixture: InnerLifeEvent with trigger_type="diary:morning", actor_id="agent_yua"
#   gate(agent_id="agent_yua")    (naturally matches, no fixture modification)
```

This validates the same gate semantic (30 min cooldown for THIS agent's personal inner work) using the canonical agent-specific event type. The test then aligns with both M5.8-4 and M5.9-3 production contracts.

**Option B (Reject)**: Add new schema field to InnerLifeEvent to record "this world event affects all agents" (M5.14-2 ticket F1-P1 §11.2 option C in M6.0-3 closeout).
- **REJECTED**: requires InnerLifeEvent schema change. Both M5.4-5.1 (InnerLifeEvent) and M5.9-3 (WorldInnerLifeAdapter) contracts are frozen. Bry 派工 2026-08-11 18:37 explicitly: "If the only valid solution requires changing either frozen M5.8-4 or M5.9-3 semantics: STOP. Report: CONTRACT CONFLICT — BRY DECISION REQUIRED."

**Option C (Reject)**: Add a "match all" semantic to M5.8-4 gate (pass `agent_id="*"` or `None`).
- **REJECTED**: requires M5.8-4 contract change. Frozen.

**Option D (Reject)**: Modify M5.9-3 to set `actor_id` to a real agent_id.
- **REJECTED**: requires M5.9-3 contract change AND violates M5.9-2 spec §7 "Do NOT fabricate actor identity." Frozen.

### 6.2 No M5.x contract changes needed

The M5.x architecture is INTENTIONALLY designed this way:
- M5.8-4: agent-specific gate (correct, frozen)
- M5.9-3: world events are agent-agnostic (correct, frozen)
- The two contracts are mutually consistent, not in conflict

**M5.14-2 verdict: NO PRODUCTION CONTRACT CONFLICT. F1-P1 is TEST DESIGN issue.**

---

## 7. Frozen Contracts Involved

| Contract | Source | Status | 0 change verified |
|----------|--------|--------|--------------------|
| WorldEvent dataclass | M3 / `src/world/perception.py:50` | FROZEN | ✓ no modification in M5.14-2 |
| WorldEvent fields (7 fields, no actor_id) | M3 | FROZEN | ✓ |
| SoulEvent.WORLD_EVENT payload | M3 / `src/eventbus/schema.py:230` | FROZEN | ✓ |
| Provenance dataclass | M5.4-5.1 / `src/inner_life/event.py:69` | FROZEN | ✓ no field change |
| Provenance.actor_id: Optional[str] = None | M5.4-5.1 | FROZEN | ✓ "None for system" docstring |
| WorldInnerLifeAdapter (M5.9-3) | M5.9-3 / `src/world/inner_life_adapter.py` | FROZEN | ✓ no modification |
| gate_proactive_dm() signature | M5.8-4 / `src/agency/inner_life_gate.py:153` | FROZEN | ✓ |
| gate filter `actor_id == agent_id` | M5.8-4 closeout L100 | FROZEN | ✓ |
| scheduler._publish_agency_trigger | M5.2-G / `src/soul/scheduler.py:177` | FROZEN | ✓ |
| AgencyTriggerHandler | M5.2-G | FROZEN | ✓ |
| _proactive_dm_llm_executor | M5.2-G / M5.4-6.2 / `scripts/run_server.py:556` | FROZEN | ✓ |
| Stage 1-4 / TriggerEnvelope | M5.1 / M5.2-F | FROZEN | ✓ |

**0 frozen contract changes in M5.14-2 (audit is read-only).**

---

## 8. Production Integrity

### 8.1 Files tracked before / after M5.14-2

| File | sha256 prefix (before) | sha256 prefix (after) | mtime |
|------|------------------------|------------------------|-------|
| `data/soul/agent_yua/relationships.json` | B3BA273F18A60389 | B3BA273F18A60389 | unchanged |
| `data/agents/agent_yua/carryover.json` | C6BE0753CCCE4E45 | C6BE0753CCCE4E45 | unchanged |
| `data/agents/agent_ruka/carryover.json` | 62D7E475C72C3BBF | 62D7E475C72C3BBF | unchanged |
| `data/inner_life/trace.jsonl` | (not present) | (not present) | n/a |
| `data/memory/memory.db` | (not present) | (not present) | n/a |

**0 production data mutation. Audit is strictly READ-ONLY.**

### 8.2 Working tree

- 20 pre-existing untracked artifacts preserved (M5.8-1 baseline)
- 0 modified files
- 0 new tracked files (audit log is untracked, will be committed per M5.14-2 closeout convention)

---

## 9. Tests / Regression

### 9.1 Focused sanity suite (read-only verification)

To prove the audit did not alter behavior, ran focused M5.x tests that exercise the identity paths:

| Test file | Tests | Result |
|-----------|-------|--------|
| `test_m5_8_4_producer_gating.py` | 26 | PASS |
| `test_m5_9_3_world_inner_life_adapter.py` | 46 | PASS |
| `test_m5_9_3_1_production_wiring.py` | 31 | PASS |
| **Focused subtotal** | **103** | **103/103 PASS** |

**0 behavior change. Audit is non-mutating.**

### 9.2 Pre-existing failures (not M5.14-2 related)

Pre-existing flaky test (M5.8-1 baseline) — NOT touched by M5.14-2:
- `tests/test_extract_and_judge_context_bug.py::test_content_stage_sees_real_text` (async infra)

Not in M5.14-2 scope.

---

## 10. Git State

```
HEAD = d34513e (M6.0-3) — synced with origin/main
Working tree: 20 pre-existing untracked artifacts preserved
Modified: 0
New (untracked): logs/m5_14_2_world_proactive_identity_audit.md (this file, will be committed)
```

---

## 11. Findings Summary

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| F1 | M5.8-4 vs M5.9-3 actor_id mismatch | **RECLASSIFIED: P3 (test design)**, was P1 in M6.0-3 | INTENTIONAL production design |
| F2 | WorldEvent has no agent_id at any layer | INFORMATIONAL | By design (M5.9-2 spec §121) |
| F3 | Gate filter `actor_id == agent_id` excludes world events | INFORMATIONAL | By design (M5.8-4 closeout L100) |
| F4 | M6.0-3 F1-F3 used `actor_id="agent_yua"` in fixture for world events | P3 test design | Mismatch with M5.9-3 production semantic |

---

## 12. Stop Conditions Check

| # | Stop condition | Hit? | Resolution |
|---|----------------|------|------------|
| 1 | frozen contract conflict discovered | **No** | M5.8-4 + M5.9-3 are mutually consistent (intentional design) |
| 2 | production identity ambiguity | **No** | All 6 producer patterns documented; world events are agent-agnostic by design |
| 3 | multiple materially different architecture solutions | **No** | Recommended Option A is single, minimal, no contract change |
| 4 | any temptation to add fallback identity | **No** | All 4 candidate identity-mapping options already rejected in M5.9-2 §6.1 |
| 5 | any production mutation | **No** | 0 mutation verified by SHA256 + mtime |

**0 stop conditions hit. Audit proceeds normally.**

---

## 13. Final Classification

**A — No production contract conflict, F1 is test design issue.**

M5.8-4 and M5.9-3 are intentionally designed to be mutually consistent:
- M5.8-4 gate: filters for THIS agent's personal inner work (actor_id == agent_id)
- M5.9-3: world events are agent-agnostic system-level observations (actor_id = None)
- Result: world events correctly do NOT gate any specific agent's proactive_dm

M6.0-3 F1-F3 tests were trying to validate the gate's 30-min cooldown semantic using world events. The correct validation should use agent-specific events (diary:morning, dream:dream, etc.) where the filter naturally matches.

---

## 14. Recommended Next Ticket

**M5.14-3 — M6.0-3 F1-F3 test reclassification (P3 doc-only fix)**

Mode: READ-ONLY or minimal IMPLEMENTATION
Scope:
- Update M6.0-3 F1-F3 tests to use agent-specific events instead of world events
- Update fixture F (scenario_F/trace.jsonl) to use `trigger_type="diary:morning"` and `actor_id="agent_yua"`
- Update F1-F3 test code: `agent_id="agent_yua"` (already correct)
- Document in M6.0-3 closeout that F1-P1 is RECLASSIFIED to P3 (test design, not production)
- 0 production code change
- 0 frozen contract change

Expected outcome: M6.0-3 F1-F3 tests validate the gate's 30-min cooldown semantic using the canonical agent-specific event type, matching both M5.8-4 and M5.9-3 production contracts.

---

**M5.14-2 status: CLOSED, READ-ONLY, 0 production mutation, 0 frozen contract change.**

**Final classification: A (no production conflict). F1-P1 reclassified to P3 (test design).**
