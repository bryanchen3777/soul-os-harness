# M6.0-1 — Lived Context Validation Design Audit

**Mode:** READ-ONLY DESIGN / VALIDATION AUDIT
**Baseline:** `a2bd687` (M5.14-1)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT
**Audit scope:** Design M6.0 end-to-end lived-context validation framework

---

## 0. M6.0 Validation Philosophy

### The cardinal rule

> **M6 validates M5.x architecture. M6 does NOT add features.**

If a proposed validation scenario requires:
- A new context block in `proxy.py`
- A new schema field
- A new frozen contract
- A new LLM prompt template
- A new runtime path
- A new abstraction layer

…then it is **feature development**, not validation. Stop and report.

### M6's job

| In scope | Out of scope |
|---------|--------------|
| Verify M5.x runtime cycle is coherent | Add new runtime paths |
| Verify M5.x frozen contracts are preserved | Modify frozen contracts |
| Verify M5.x feedback loops are bounded | Add new feedback loops |
| Verify M5.x Soul Context composition is correct | Add new context blocks |
| Verify M5.x producer/consumer wiring | Add new producers/consumers |
| Verify M5.x data flows (memory.db, trace, relationships) | Add new persistence |
| Replay scenarios deterministically | LLM-based testing |
| Define PASS/PARTIAL/FAIL criteria | Semantic scoring |

### Verification principle

> **If a scenario's correctness cannot be verified without an LLM call, then that scenario is a STRUCTURAL test (deterministic), not an LLM test.**

A scenario is PASS / PARTIAL / FAIL by **deterministic observation** of state transitions and content. LLM output is treated as **opaque text** — we verify that LLM was *called with the right context*, not that the LLM *answered correctly*.

---

## 1. M6 Validation Model

### Model type: **State Transition + Content Verification**

Each M6 scenario is a deterministic test that:
1. Sets up a known initial state (seeded, isolated)
2. Executes the runtime cycle (mocked LLM or real LLM with mock backend)
3. Observes state transitions at each boundary
4. Verifies content at each observable checkpoint
5. Does NOT verify LLM output quality (that's subjective)

### What M6 verifies

| Dimension | Verification approach |
|-----------|----------------------|
| **Structural correctness** | Code path is reached, contracts satisfied, schemas match |
| **Contextual coherence** | All 8 Soul Context blocks present, in correct order, with correct data |
| **Behavioral continuity** | State transitions match spec (e.g., relationships.json +0.05 after USER_MESSAGE) |
| **Subjective quality** | OUT OF SCOPE — LLM-dependent, cannot be deterministic |

### What M6 does NOT verify

- LLM "quality" of generated text
- Diary "creativity" or "emotional depth"
- "Character voice" consistency
- Whether the LLM "understood" the context

These are inherently non-deterministic and subjective. M6 stops at **"was the LLM called with the right context in the right order?"**

---

## 2. Scenario Matrix (8 scenarios)

| # | Scenario | Verifies | Determinism |
|---|----------|----------|-------------|
| **A** | Ordinary user conversation | USER_MESSAGE → AGENT_INTENT → AGENT_SPEAK cycle | High |
| **B** | Relationship continuity | confidence band updates across cycles | High |
| **C** | Memory continuity | fact extracted → retrievable in next cycle | High |
| **D** | Temporal continuity | chrono-social + carryover across sessions | High |
| **E** | World event → inner life | calendar_event → diary/dream/event | High |
| **F** | World event → proactive gate | M5.8-4 30min cooldown | High |
| **G** | Inner-life persistence → later context | 3-day diary window, source="llm" filter | High |
| **H** | Multi-cycle lived-context continuity | 3 visits, consistent identity/memory/relationships | High |

Each scenario is detailed below.

---

### Scenario A: Ordinary User Conversation

**Purpose:** Verify the canonical USER_MESSAGE → LLM → AGENT_SPEAK cycle works end-to-end.

**Source state:**
- `relationships.json`: agent_yua ↔ Bry, confidence=0.5
- `memory.db`: empty
- `data/soul/agent_yua/diary/`: empty
- `data/agents/agent_yua/carryover.json`: empty (default)
- Soul Context blocks: system_prompt only

**Trigger:** USER_MESSAGE = "你今天好嗎？"

**Cycle:**
1. Gateway ingestion → SoulEvent(USER_MESSAGE)
2. MemoryMiddleware._on_user_message → relationships.touch(BRYAN, +0.05) → 0.55
3. consciousness._on_user_message → emotion_engine.update + _fire_intent(AGENT_INTENT)
4. MemoryMiddleware._on_agent_intent → MemoryReader.retrieve_context → memory_context=""
5. WorldPerceptionMiddleware._on_agent_intent_enriched → world_context=""
6. SpeakerTokenManager (group) or direct (private) → LLM call
7. LLMProxy._build_messages_* → LLM call (mocked)
8. SoulEvent(AGENT_SPEAK) → MemoryMiddleware._on_agent_speak
   - relationships.on_agent_speak → confidence+0.008 → 0.558
   - post_reply_commit → LLM Judge → memory.db (empty in test, no facts)

**Next-cycle observable state:**
- `relationships.json`: agent_yua ↔ Bry, confidence ≈ 0.558 (after touch)
- `memory.db`: empty (no facts to extract)
- `data/agents/agent_yua/emotional-state.json`: updated (mood/intimacy)

**Checkpoints (deterministic):**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| A1 | Relationships.touch called with BRYAN_ENTITY_ID | `store.get(BRYAN).confidence` == 0.55 (after +0.05) |
| A2 | Memory context empty (no prior memory) | `event.payload["memory_context"] == ""` |
| A3 | LLM called with 8-block system prompt | `messages[0]["content"]` contains all 8 markers in order |
| A4 | AGENT_SPEAK published | `bus` receives AGENT_SPEAK event |
| A5 | Relationships on_agent_speak called | `store.get(BRYAN).confidence` ≈ 0.558 (after +0.008) |
| A6 | LLM Judge called | `provider.post_reply_commit` invoked |

**PASS criteria:** All 6 checkpoints hit, in order, with expected values.
**PARTIAL criteria:** 4-5 of 6 checkpoints hit.
**FAIL criteria:** < 4 checkpoints hit, OR AGENT_SPEAK not published, OR frozen contract violated.

---

### Scenario B: Relationship Continuity

**Purpose:** Verify M5.13-3 relationship block reaches LLM and updates across multiple cycles.

**Source state:**
- `relationships.json`: agent_yua ↔ Bry, confidence=0.85 (established from prior cycles)
- `memory.db`: irrelevant for this scenario
- `data/soul/agent_yua/diary/`: irrelevant

**Trigger:** USER_MESSAGE = "早安" (a brief positive trigger)

**Cycle (this iteration only):**
1. MemoryMiddleware._on_user_message → relationships.touch(BRYAN, +0.05) → 0.90
2. LLM call: should see relationship_block in system prompt

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| B1 | relationships.confidence = 0.85 before touch | `store.get(BRYAN).confidence == 0.85` |
| B2 | LLM prompt contains relationship_block | `messages[0]["content"]` contains "深度信任" band |
| B3 | After touch, confidence = 0.90 | `store.get(BRYAN).confidence == 0.90` |
| B4 | Next cycle: LLM sees updated band | Recompute, verify still 深度信任 |
| B5 | No raw float leak | "0.85" / "0.90" not in prompt |

**PASS criteria:** All 5 checkpoints hit.
**PARTIAL criteria:** 4/5.
**FAIL criteria:** < 4/5 OR raw float leaked OR wrong band label.

---

### Scenario C: Memory Continuity (FACT extraction → retrieval)

**Purpose:** Verify v1 memory context flows correctly: USER_MESSAGE contains a fact → LLM Judge extracts → memory.db stores → next LLM call retrieves.

**Source state:**
- `memory.db`: empty
- `relationships.json`: agent_yua ↔ Bry, confidence=0.5

**Trigger (cycle 1):** USER_MESSAGE = "我昨天去看了電影 Inception" (a fact)

**Cycle 1:**
1. LLM call (mocked response: "Inception 很好看呢！")
2. MemoryMiddleware._on_agent_speak → provider.post_reply_commit
3. MemoryWriter._extract_facts_llm:
   - MemoryReader.retrieve_context(text, top_k=3, ...) → v1 memory context (empty initially)
   - LLM Judge: extracts fact "Bry watched Inception yesterday"
   - Writes to memory.db

**Trigger (cycle 2):** USER_MESSAGE = "我昨天看的那部電影怎麼樣？" (a query about the fact)

**Cycle 2:**
1. MemoryMiddleware._on_agent_intent → MemoryReader.retrieve_context(query)
2. Retrieves the fact from cycle 1
3. memory_context = summary including the fact
4. LLM call: should see the fact in system prompt

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| C1 | Cycle 1 LLM Judge called | `MemoryWriter._extract_facts_llm` invoked |
| C2 | Memory Judge received v1 context (even if empty) | `_extract_facts_llm` calls `_memory_reader.retrieve_context` |
| C3 | Cycle 1 fact stored in memory.db | `memory.db` query returns the fact |
| C4 | Cycle 2 retrieve returns the fact | `MemoryReader.retrieve_context` returns summary including the fact |
| C5 | Cycle 2 LLM prompt contains the fact | `messages[0]["content"]` contains the fact text |
| C6 | No false facts injected | Memory content only from cycle 1, not synthesized |

**PASS criteria:** All 6 checkpoints hit.
**LLM-dependent:** C3 and C5 require the mocked LLM Judge to return expected fact — use deterministic mock.

**Isolation:** Use fresh memory.db (tempdir per scenario).

---

### Scenario D: Temporal Continuity (chrono-social + carryover)

**Purpose:** Verify carryover from SESSION_END flows into next session's chrono-social block.

**Source state:**
- `data/agents/agent_yua/carryover.json`: empty
- `data/state/event_loop_alive.json`: irrelevant

**Trigger (cycle 1):** USER_MESSAGE → ... → SESSION_END (elapsed >= 30min)

**Cycle 1:**
1. SESSION_END event published
2. HeartbeatEngine._loop catches SESSION_END (or consciousness._on_session_end)
3. consciousness._on_session_end:
   - Build EmotionalCarryover from current state
   - carryover.save(agent_id) → data/agents/agent_yua/carryover.json

**Trigger (cycle 2):** server restart (or new session)

**Cycle 2:**
1. HeartbeatEngine.start() loads carryover from disk
2. _carryovers[agent_id] = carryover.apply_decay(0)
3. Next tick: chrono_ctx = build_temporal_context(..., carryover=carryover)
4. chrono_block includes carryover.attachment_heat etc.

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| D1 | SESSION_END triggers carryover save | `carryover.json` file exists and non-empty after cycle 1 |
| D2 | carryover.attachment_heat computed | `carryover.json` contains "attachment_heat" field |
| D3 | HeartbeatEngine loads carryover on startup | `engine._carryovers[agent_id]` is populated |
| D4 | chrono-social block includes carryover | Rendered block contains "attachment_heat" line |
| D5 | apply_decay is deterministic (elapsed_hours=0) | Two loads give same values |

**PASS criteria:** All 5 checkpoints hit.
**Determinism:** Decay is time-based, but `apply_decay(0)` is fully deterministic.

---

### Scenario E: World Event → Inner Life (calendar_event → diary)

**Purpose:** Verify world event creates InnerLifeEvent and diary entry.

**Source state:**
- `data/inner_life/trace.jsonl`: empty
- `data/soul/agent_yua/diary/`: empty
- `relationships.json`: agent_yua ↔ Bry, confidence=0.5

**Trigger (cycle 1):** world event — calendar_event (synthetic via `WorldPerceptionMiddleware.process_world_event_direct`)

**Cycle 1:**
1. WorldPerceptionMiddleware._on_world_event → state + trace
2. WorldInnerLifeAdapter (parallel subscription):
   - qualify_world_event(calendar_event) → YES (in whitelist)
   - dedup check (in-memory)
   - inner_life_writer.create_event(provenance.trigger_type="world:calendar_event")
3. trace.jsonl appended
4. data/inner_life/agent_yua/events/{event_id}.json written

**Trigger (cycle 2):** scheduler fires next morning diary at 08:00

**Cycle 2:**
1. AGENCY_TRIGGER (trigger_type="morning") → DiaryHandler
2. _diary_writer_executor → DiaryWriter.write_diary
3. Generates subjective reflection (mocked LLM)
4. Appends to data/soul/agent_yua/diary/{date}.jsonl

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| E1 | WorldPerceptionMiddleware processes WORLD_EVENT | `state.world_context` updated |
| E2 | WorldInnerLifeAdapter qualifies calendar_event | qualification result = YES |
| E3 | InnerLifeEvent created in trace.jsonl | New line in trace.jsonl with trigger_type="world:calendar_event" |
| E4 | Event file written | data/inner_life/agent_yua/events/{event_id}.json exists |
| E5 | diary.jsonl has the new entry | data/soul/agent_yua/diary/{date}.jsonl has new line |
| E6 | source="llm" not "placeholder" | `entry.get("source") == "llm"` |

**PASS criteria:** All 6 checkpoints hit.
**Type whitelist:** Only `calendar_event` and `user_going_outside` qualify; other types should fail (E2 = NO).

---

### Scenario F: World Event → Proactive Gate (M5.8-4 30min cooldown)

**Purpose:** Verify M5.8-4 producer gate suppresses proactive_dm when recent inner life activity exists.

**Source state:**
- `data/inner_life/trace.jsonl`: contains InnerLifeEvent with ts=now (recent)
- `relationships.json`: agent_yua ↔ Bry, confidence=0.5
- proactive whitelist: ["agent_yua"]

**Trigger:** scheduler._fire_proactive_dm fires (random)

**Cycle:**
1. SoulScheduler._fire_proactive_dm → _inner_life_gate_check(agent_id)
2. NarrativeTraceReader.query_by_ts_range(24h window)
3. Filter provenance.actor_id == agent_id
4. Compute elapsed = now - last_event.ts
5. If elapsed < 30min → GATED, skip publish
6. If elapsed >= 30min → EMITTED, continue

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| F1 | Gate query reads trace.jsonl | NarrativeTraceReader invoked |
| F2 | Filter by actor_id works | Only this agent's events considered |
| F3 | elapsed < 30min → GATED | No AGENCY_TRIGGER published, observable log shows GATED |
| F4 | elapsed >= 30min → EMITTED | AGENCY_TRIGGER published |
| F5 | Fail-open on query exception | RuntimeError → still emit |
| F6 | Fail-open on no trace file | UNAVAILABLE → still emit |

**PASS criteria:** All 6 checkpoints hit.
**Determinism:** Test uses `datetime.now(timezone.utc)` mock to control elapsed time.

---

### Scenario G: Inner-Life Persistence → Later Context (3-day window, source filter)

**Purpose:** Verify `_format_recent_inner_life` correctly filters diary/dream/event by date and source.

**Source state:**
- `data/soul/agent_yua/diary/`:
  - `2026-08-09.jsonl` with 2 entries (source="llm", source="placeholder")
  - `2026-08-10.jsonl` with 1 entry (source="llm")
  - `2026-08-13.jsonl` (today) with 1 entry (source="llm")
  - `2026-08-05.jsonl` (5 days ago, outside 3-day window)

**Trigger:** USER_MESSAGE → LLM call

**Cycle:**
1. LLMProxy._build_messages_* → _format_recent_inner_life(agent_yua)
2. Read 3 most recent days
3. Filter source="llm" only
4. Truncate each entry to INNER_LIFE_MAX_CHARS_PER_ENTRY
5. Cap at INNER_LIFE_MAX_ENTRIES total

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| G1 | 3-day window respected | Oldest entry (2026-08-05) NOT in output |
| G2 | source="llm" filter | "placeholder" entry NOT in output |
| G3 | Entries ordered by date | Most recent first (or file order) |
| G4 | Entry count capped | At most INNER_LIFE_MAX_ENTRIES lines |
| G5 | Truncation applied | Long entry truncated with "..." |
| G6 | "placeholder" string absent | (defensive, ensure no leak) |

**PASS criteria:** All 6 checkpoints hit.
**Determinism:** File system reads are deterministic given fixed fixtures.

---

### Scenario H: Multi-Cycle Lived-Context Continuity

**Purpose:** Verify 3 consecutive USER_MESSAGE cycles produce consistent state across persona, memory, relationships, inner life, world awareness.

**Source state (cycle 0):** Initial state (empty memory, default relationships, no diary)

**Triggers (3 cycles):**
- Cycle 1: USER_MESSAGE = "早安"
- Cycle 2: USER_MESSAGE = "今天好累"
- Cycle 3: USER_MESSAGE = "我去看電影了"

**Cycle 1 → 2 → 3 expectations:**
- Cycle 1: relationships.confidence += 0.05
- Cycle 2: relationships.confidence += 0.05; mood changes; emotion_engine update
- Cycle 3: relationships.confidence += 0.05; fact "watched a movie" extracted to memory.db

**Checkpoints:**

| # | Checkpoint | Verification |
|---|-----------|--------------|
| H1 | After 3 cycles, relationships.confidence = 0.65 | (3 * +0.05 + 3 * +0.008 from agent_speak ≈ 0.674) |
| H2 | Mood/intimacy evolve monotonically | emotion_engine state shows 3 updates |
| H3 | memory.db has at least 1 fact | from cycle 3 |
| H4 | Each cycle's LLM call sees accumulated context | memory_context grows over cycles |
| H5 | Persona (system_prompt) is constant | Same persona across 3 cycles |
| H6 | No state regression (memory loss, etc.) | All 3 cycles can be replayed in any order and produce same end state |
| H7 | Counter files in data/state/ are updated | (test isolation: verify no production files touched) |

**PASS criteria:** All 7 checkpoints hit.
**LLM-dependent:** H3 depends on what facts the mocked LLM Judge returns. Use deterministic mock.

---

## 3. Deterministic Assertions

Each checkpoint is verified by a **deterministic assertion** of state. Assertions are categorized:

| Type | Examples | Determinism |
|------|----------|-------------|
| **D-File** | File exists, file content matches expected text | 100% deterministic |
| **D-DB** | SQLite query returns expected row count / value | 100% deterministic |
| **D-State** | In-memory state equals expected value (e.g., relationships.confidence == 0.65) | 100% deterministic |
| **D-Event** | Specific SoulEvent was published to bus | 100% deterministic |
| **D-Order** | Operations happened in expected order (e.g., 3 user_messages in a row) | 100% deterministic |

All 8 scenarios use only D-* assertions. No LLM-dependent assertions in the M6 framework.

---

## 4. LLM-Dependent Assertions

**M6.0-1 design rule:** LLM-dependent assertions are **OUT OF SCOPE** for M6.

| Reason | Detail |
|--------|--------|
| Non-determinism | Same input → different output across runs |
| Cost | Each LLM call is $$ and slow |
| External dependency | Network, API rate limits, model availability |
| Subjective quality | "Good" character voice is opinion, not assertion |

### What M6 does instead

For each scenario that involves an LLM call, M6 uses a **deterministic mock backend** that returns fixed text. The mock is just a stand-in for the OpenAI/Claude API call. M6 verifies:
- The mock was *called* (right context flowed in)
- The mock's response was *published* correctly
- The downstream consumers *processed* the response correctly

M6 does NOT verify:
- The mock's response was the "right" answer
- The character voice was "good"
- The diary was "creative"

### Where LLM quality would be tested

A separate framework (M6.5 or M7, future) would handle subjective quality via:
- LLM-as-judge (with its own non-determinism)
- Human evaluation
- Golden-output regression (manually curated)

These are **separate tickets** and not part of M6.0.

---

## 5. Replay Model

### Replay philosophy

> A scenario MUST be reproducible from a known seed. Production data MUST NOT be required to reproduce.

### Replay mechanism

Each scenario has:
- **Seed**: a deterministic input (e.g., a fixed `datetime.now(timezone.utc)`, a fixed LLM mock backend, a fixed data fixture)
- **Initial state**: a fixture file in `tests/fixtures/m6_0/scenario_X/` (e.g., `relationships.json`, `memory.db`)
- **Driver**: a test function that runs the scenario and verifies checkpoints

### What "replay" means

1. Load fixture into isolated `data_root` (tempdir)
2. Inject the trigger (e.g., `MemoryMiddleware._on_user_message` with mock SoulEvent)
3. Wait for the cycle to complete (no real LLM call — use mock backend)
4. Verify checkpoints
5. Cleanup: tempdir is auto-deleted

### What "replay" does NOT mean

- Replaying real production events from production data
- Replaying the same test multiple times and expecting different outputs (M6 is deterministic — same input → same output)
- Replaying across server restarts (each scenario is one process)

### Why "replay without mutating production" is the goal

If a scenario requires reading production data, then:
- The scenario is fragile (depends on production state)
- The scenario cannot be reproduced (production state is mutable)
- The scenario can leak data from one run to another

M6 avoids this entirely by using **tempdir fixtures**. Production data is read by the *server*, not by the test.

---

## 6. Production Isolation Model

### Isolation principles

| Principle | Mechanism |
|----------|-----------|
| **No production data read** | All fixtures loaded from `tests/fixtures/m6_0/` |
| **No production data write** | All writes go to `tempfile.TemporaryDirectory()` per scenario |
| **No shared state between scenarios** | Each scenario creates fresh tempdir |
| **No side effects on real data_root** | `data_root` patch points to tempdir (P0.5 pattern) |
| **No live server interaction** | LLMProxy is invoked with mock backend, not real OpenAI |

### Isolation pattern (per scenario)

```python
# Standard M6 fixture pattern
import tempfile
from pathlib import Path
from unittest.mock import patch

def run_scenario_x():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # 1. Load fixture to tempdir
        (tmp / "data").mkdir()
        copy_fixture("scenario_x/relationships.json", tmp / "data/soul/agent_yua/relationships.json")
        # 2. Patch data_root
        with patch("src.paths.data_root", return_value=tmp / "data"):
            # 3. Run scenario with mock LLM
            from src.llm.proxy import LLMProxy
            mock_backend = MockBackend(fixed_response="...")
            proxy = LLMProxy(backend=mock_backend)
            # ... trigger scenario ...
            # 4. Verify checkpoints
            assert relationships.confidence == expected
            assert memory.db query returns expected
            # 5. Tempdir auto-cleaned on exit
```

### What goes into fixtures

- `tests/fixtures/m6_0/scenario_A/relationships.json` — initial relationships
- `tests/fixtures/m6_0/scenario_A/memory.db` — initial memory (or empty)
- `tests/fixtures/m6_0/scenario_A/diary/2026-08-09.jsonl` — initial diary
- `tests/fixtures/m6_0/scenario_A/expected_state.json` — expected end state

Fixtures are **deterministic test inputs**, not production data.

### What does NOT go into fixtures

- Production user messages
- Production agent state
- Production LLM responses
- Any data that contains PII

---

## 7. Failure Taxonomy

### Result categories

| Result | Definition | Required response |
|--------|------------|-------------------|
| **PASS** | All checkpoints hit, all assertions true | Move on to next scenario |
| **PARTIAL** | 50-99% checkpoints hit, but core flow not broken | Report findings, do not fail test |
| **FAIL** | < 50% checkpoints hit, OR core flow broken, OR frozen contract violated | Fail test, generate ticket for fix |

### PARTIAL scenarios: when to accept

| Scenario | PASS threshold | PARTIAL acceptable? |
|----------|----------------|---------------------|
| A Ordinary user | 6/6 checkpoints | No — all checkpoints must hit |
| B Relationship | 5/5 | No |
| C Memory | 6/6 | No |
| D Temporal | 5/5 | No |
| E World → inner life | 6/6 | No (or 5/6 if type whitelist miss) |
| F World → gate | 6/6 | No |
| G Inner life persistence | 6/6 | No (boundary test) |
| H Multi-cycle | 7/7 | No (regression) |

**M6 design choice:** PARTIAL is documented but rarely used. Either the cycle works or it doesn't. PARTIAL is reserved for documenting **known design limitations** (e.g., E2 = NO when world_type is not in whitelist).

### FAIL → ticket generation

When a scenario FAILS, M6 generates a **structured failure report**:
- Scenario ID
- Checkpoint that failed
- Expected vs actual state
- Likely cause (frozen contract? data corruption? test bug?)
- Suggested follow-up ticket (e.g., "M5.X-Y: investigate checkpoint N")

---

## 8. Acceptance Gates

### M6 framework acceptance

| Gate | Criterion |
|------|-----------|
| **AG-1** | All 8 scenarios designed with checkpoints |
| **AG-2** | Each scenario has at least 5 deterministic checkpoints |
| **AG-3** | All scenarios reproducible from fixtures (no production data required) |
| **AG-4** | Replay model defined (seed + fixture + driver) |
| **AG-5** | Production isolation pattern documented |
| **AG-6** | PASS/PARTIAL/FAIL taxonomy defined |
| **AG-7** | No LLM-dependent assertions in M6 |
| **AG-8** | No new frozen contracts introduced |
| **AG-9** | No new context blocks added |
| **AG-10** | M6 does not become a feature-development mechanism (verified by design) |

### M6.0-2 implementation gates (future)

| Gate | Criterion |
|------|-----------|
| **IG-1** | Test file `tests/test_m6_0_scenarios.py` exists |
| **IG-2** | Each scenario has a test function |
| **IG-3** | All scenarios use `tempfile.TemporaryDirectory()` for isolation |
| **IG-4** | All scenarios use `MockBackend` for LLM (no real calls) |
| **IG-5** | Fixtures under `tests/fixtures/m6_0/` are deterministic |
| **IG-6** | Test runs in < 60s total (per scenario < 10s) |
| **IG-7** | All 8 scenarios PASS in fresh repo state |
| **IG-8** | All 8 scenarios PASS after re-running (deterministic) |

---

## 9. M6.0-2 Implementation Scope (Recommended)

### Minimum viable M6.0-2 ticket

**Scope:** Implement 3 of 8 scenarios as proof-of-concept. Defer 5 to M6.0-3.

**Selected scenarios for M6.0-2:**
- **A (Ordinary user)** — simplest, covers baseline cycle
- **B (Relationship continuity)** — verifies M5.13-3 (most recent feature)
- **C (Memory continuity)** — verifies M5.10-2 (core infrastructure)

**Deferred to M6.0-3:**
- D (Temporal) — needs carryover fixture setup
- E (World → inner life) — needs synthetic world event setup
- F (World → gate) — needs gate timing control
- G (Inner life persistence) — needs diary fixture setup
- H (Multi-cycle) — builds on A, B, C

**M6.0-2 deliverables:**
- `tests/test_m6_0_scenarios.py` with 3 scenario test functions
- `tests/fixtures/m6_0/scenario_A/`, `B/`, `C/` (initial state fixtures)
- `tests/_helpers/mock_llm_backend.py` (mock LLM backend for testing)
- `tests/_helpers/state_assertions.py` (reusable assertion helpers)
- `logs/m6_0_2_closeout.md`

**Acceptance:**
- All 3 scenarios PASS in fresh test run
- All 3 scenarios PASS in re-run (determinism)
- No production data touched
- No frozen contract changes
- Test runs in < 30s total

---

## 10. Unresolved Bry Decisions

| # | Decision | Source | Status |
|---|----------|--------|--------|
| 1 | M6.0-2 scope: 3 scenarios (A/B/C) or 8 scenarios? | This audit | Pending |
| 2 | M6 framework ownership: dedicated M6 module or part of existing test_m5_*? | This audit | Pending |
| 3 | Mock LLM backend: in-repo fixture or external library? | This audit | Pending |
| 4 | Subjective LLM quality testing: separate M6.5 ticket or never? | This audit | Pending |
| 5 | Real-world source integration (B1) | M5.14-1 | Pending |
| 6 | P2.6 Memory gate (B4) | M5.12-1 / M5.14-1 | Pending |

---

## 11. Required Classification

### **A — Validation framework ready for implementation**

Justification:
- ✅ 8 scenarios defined with clear checkpoints (5-7 each)
- ✅ All scenarios use deterministic assertions only
- ✅ LLM-dependent assertions explicitly excluded
- ✅ Replay model: seed + fixture + driver (3 components)
- ✅ Production isolation: tempdir + P0.5 pattern
- ✅ PASS/PARTIAL/FAIL taxonomy defined
- ✅ No frozen contract changes required
- ✅ No new context blocks added
- ✅ M6 cannot become a feature-development mechanism (validated by design rule)
- ✅ M6.0-2 implementation scope defined (3 scenarios first)

### Minor design notes (B-class, not blocking)

1. **B-D1**: Subjective LLM quality testing is deferred (out of M6.0 scope). A future M6.5+ ticket would handle this.
2. **B-D2**: Mock LLM backend needs to be a stable, reusable component. Recommend `tests/_helpers/mock_llm_backend.py` as a shared utility.
3. **B-D3**: 5 deferred scenarios (D-H) require more complex fixture setup. Recommend M6.0-3 as a follow-up.

### No significant gaps (C-class) or frozen contract conflicts (D-class)

---

## 12. Architectural Recommendation

### **CLOSE M6.0-1 design phase. PROCEED to M6.0-2 implementation.**

**M6.0-2 scope (recommended):**
- Implement Scenarios A, B, C as proof-of-concept
- Use tempdir isolation + MockBackend LLM
- No frozen contract changes
- No new context blocks
- Reusable fixtures and helpers

**M6.0-3 scope (future):**
- Implement Scenarios D, E, F, G, H
- Build on M6.0-2 helpers and patterns

**M6.5+ scope (deferred):**
- Subjective LLM quality testing
- Real-world integration tests
- Multi-agent multi-user scenarios

**Bry's decision required:**
- ✅ Approve M6.0-2 with 3-scenario scope (recommended)?
- Or expand to 8-scenario scope in M6.0-2?
- Or defer M6 entirely?

---

## 13. Verification: M6 Does NOT Become Feature Development

### Self-check matrix

| Check | Result |
|-------|--------|
| Does M6 add a new context block? | ❌ No |
| Does M6 add a new frozen contract? | ❌ No |
| Does M6 add a new runtime path? | ❌ No |
| Does M6 add a new LLM prompt template? | ❌ No |
| Does M6 modify any existing M5.x behavior? | ❌ No (only validates) |
| Does M6 require embeddings/vector DB/semantic? | ❌ No |
| Does M6 require new scoring? | ❌ No |
| Does M6 introduce new abstractions? | ❌ No (test framework, not runtime) |

**Conclusion:** M6 is a **validation framework**, not a feature. The only "new code" is test infrastructure (test files, fixtures, mock backends), which is conventional test engineering, not product code.

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M6.0-1 |
| Mode | READ-ONLY DESIGN / VALIDATION AUDIT |
| Baseline | `a2bd687` |
| Files read | M5.14-1 audit log (cached), M5.13-3 + M5.13-3.1 closeout logs (cached) |
| Files created | 1 (this audit log) at `C:\Users\bbfcc\m6_0_1_temp\` |
| Classification | **A — Validation framework ready for implementation** |
| Recommended next ticket | M6.0-2 (implement 3 scenarios) |
| Production data | 0 mutation |
| Source modifications | 0 |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
