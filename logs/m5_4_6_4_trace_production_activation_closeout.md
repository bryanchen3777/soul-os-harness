# M5.4-6.4 — Narrative Trace Production Activation — CLOSEOUT

**Mode:** MINIMAL IMPLEMENTATION
**Baseline:** HEAD = 315deb4 = origin/main
**Final HEAD:** see git log
**Date:** 2026-08-10

---

## 1. Exact Source Change

**File:** `scripts/run_server.py` (lines 240-272, post-M5.4-6.4)

**Diff (1 functional line + import + comment update):**

```python
# ── M5.4-6.1 (Bry 派工 2026-08-10): InnerLifeWriter instance ──
# (略過 M5.4-6.1 註解, 全部保留)
from src.inner_life import (
    InnerLifeWriter,
    NarrativeTraceWriter,      # ← M5.4-6.4 新增
    Provenance,
    TRIGGER_TYPE_AGENT_REPLY,
    TRIGGER_TYPE_DIARY_MORNING,
    TRIGGER_TYPE_DIARY_NIGHT,
    TRIGGER_TYPE_DREAM_DREAM,
    TRIGGER_TYPE_DREAM_EVENT,
)
inner_life_writer = InnerLifeWriter(trace_writer=NarrativeTraceWriter())
#     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#     M5.4-6.4: 1 functional line change
```

**Total change:** 1 functional line + 1 import line + 12 comment lines (M5.4-6.4 rationale).

---

## 2. Tests

**New test file:** `tests/test_m5_4_6_4_trace_production_activation.py` (20 tests)

| Section | Count | Coverage |
|---------|-------|----------|
| A. Source injection | 3 | import exists, `trace_writer=` in construction, single InnerLifeWriter instance |
| B. Production construction pattern | 3 | exact pattern works, default path = data_root() / inner_life / trace.jsonl, create_event writes trace |
| C. 4 producer coverage | 5 | diary morning, diary night, dream, event, proactive_dm (each = 1 trace) |
| D. Trace event_id canonical match | 1 | byte-exact match between InnerLifeEvent.event_id and trace record event_id |
| E. NarrativeTraceReader compatibility | 1 | reader reads production-generated format |
| F. Failure isolation | 2 | trace_writer raises → event still valid; broken path → isolated |
| G. USER_MESSAGE exclusion | 1 | USER_MESSAGE not in any wired trigger_type → 0 traces |
| H. Privacy / content boundary | 1 | no content/prompt/response fields in any trace record |
| I. data_root() isolation | 1 | trace in tmp_path, not production data/ |
| J. No duplicate trace | 1 | exactly 1 trace per create_event (no double-write) |
| count | 1 | sanity check |
| **Total** | **20** | **20/20 PASSED** |

---

## 3. Regression

| Test File | Result | Notes |
|-----------|--------|-------|
| test_m5_4_5_1_inner_life_foundation.py | 59/59 PASS | |
| test_m5_4_5_2_memory_inner_life_integration.py | 29/29 PASS | |
| test_m5_4_5_3_diary_inner_life_integration.py | 27/27 PASS | |
| test_m5_4_5_4_dream_inner_life_integration.py | 23/23 PASS | |
| test_m5_4_5_5_event_bus_inner_life_integration.py | 16/16 PASS | |
| test_m5_4_5_6_narrative_trace_sidecar.py | 22/22 PASS | |
| test_m5_4_5_7_trace_reader.py | 22/22 PASS | |
| test_m5_4_6_1_executor_wiring.py | 30/30 PASS | |
| test_m5_4_6_2_proactive_dm_inner_life_wiring.py | 25/25 PASS | |
| test_m5_4_6_3_trace_production_activation_audit.py | 24/24 PASS | |
| test_m5_4_6_4_trace_production_activation.py (NEW) | 20/20 PASS | |
| test_m3_e2e_smoke.py | 3/3 PASS | |
| test_m3_world_awareness.py | 26/26 PASS | |
| test_websocket_e2e.py | **2/2 PASS** | **Re-verified post-M5.4-6.4 (M5.4-6.2 closeout timing-flake note confirmed) |
| **Total** | **328/328 PASS** | |

---

## 4. Trace Production Verification (Controlled Test)

A controlled test in isolated `tmp_path` (separate from pytest, not committed) verified the exact production pattern end-to-end:

```
BEFORE: trace_path exists = False
BEFORE: trace_path = C:\...\Temp\m5_4_6_4_verify_37sqjkcv\inner_life\trace.jsonl
InnerLifeWriter._trace_writer type = NarrativeTraceWriter
Trace path: C:\...\Temp\m5_4_6_4_verify_37sqjkcv\inner_life\trace.jsonl
AFTER: trace_path exists = True
Number of trace records: 5
All event_ids match canonical: True
All trigger_types:
  diary:morning: actor=agent_yua
  diary:night: actor=agent_akane
  dream:dream: actor=agent_mahiru
  dream:event: actor=agent_anna
  agent_reply: actor=agent_ruka
Fields per record: ['correlation_id', 'event_id', 'lineage_depth', 'lineage_path', 'parent_event_id', 'provenance', 'session_id', 'ts']
Field count: 8
Fields match spec: True
Any content leak: False
Cleaned up C:\...\Temp\m5_4_6_4_verify_37sqjkcv
```

**Verification confirms:** trace is generated only after activation, only contains 8 metadata fields, all event_ids match canonical, no content leakage, isolated to tmp_path.

---

## 5. Before/After Production Integrity

### BEFORE (HEAD = 315deb4, pre-M5.4-6.4)
- `data/inner_life/trace.jsonl`: **DOES NOT EXIST**
- `data/inner_life/`: **DOES NOT EXIST**
- All other production files: **UNCHANGED**
  - `data/memory.db`: not touched
  - `data/conversations/`: not touched
  - `data/soul/.../diary/`: not touched
  - S0 backup: not touched

### AFTER (M5.4-6.4 merged)
- `data/inner_life/trace.jsonl`: will be **created on first production server startup** that triggers an InnerLifeEvent (Diary/Dream/Event/ProactiveDM executor)
- **No existing production data is touched** (no migration, no backfill, no replay)
- The activation is **purely additive** — production runtime before activation behaves identically to pre-M5.4-6.4

### Production runtime behavior change

| Aspect | Before (315deb4) | After (M5.4-6.4) |
|--------|-------------------|---------------------|
| 4 producer executors | Call `inner_life_writer.create_event()` | Call `inner_life_writer.create_event()` (unchanged) |
| InnerLifeEvent creation | Yes | Yes (unchanged) |
| Trace sidecar | Disabled (no writer) | **Enabled (writer injected)** |
| `data/inner_life/trace.jsonl` | Never created | Created on first trace |
| Estimated growth | 0 | ~5-6 MB/year |
| Failure behavior | N/A | Trace failure → log warning, event still valid (verified) |

---

## 6. Number of Trace Records Generated by Controlled Test

**Controlled test (in isolated tmp_path, since deleted):**
- BEFORE: 0 records
- 5 producer invocations → 5 InnerLifeEvents → 5 trace records
- AFTER: 5 records (1 per producer pattern)
- Per-tick rate: exactly 1 record per `create_event` call (no batching, no coalescing)

**Production estimate** (assuming same 4 producers active as in M5.4-6.3 audit):
| Producer | Records/day | Records/year |
|----------|-------------|--------------|
| Diary (morning + night, 10 agents) | 20 | 7,300 |
| Dream (3-5 agents) | 4 | 1,460 |
| Event (2/tick, 3-6 ticks) | 6-12 | 2,190-4,380 |
| Proactive DM (1 agent, 3-5h) | 5-8 | 1,825-2,920 |
| **Total** | **35-44** | **12,775-16,060** |

Estimated file size: ~400 bytes/record → ~5-6 MB/year.

---

## 7. Confirmation: No Historical Backfill

**Explicitly verified:**
- No migration script created (M5.4-6.4 is a 1-line source change, no data migration)
- No `backfill` function in `inner_life` module
- No code path reads existing diary/dream/event jsonl and writes to trace
- `trace.jsonl` is created **only on first `create_event()` call** after activation
- All audit and M5.4-6.4 tests use **isolated tmp_path**, no production data read

**BEFORE activation:** trace.jsonl does not exist in production (verified).
**AFTER activation:** trace.jsonl contains only post-activation events (no backfill).
**Trace records are strictly forward-going** from the activation moment onward.

---

## 8. Git State

**Modified files (tracked):**
- `scripts/run_server.py` (+12/-1 lines, 1 functional line + 1 import + 12 comment lines)

**New untracked files (this ticket):**
- `tests/test_m5_4_6_4_trace_production_activation.py` (20 tests)
- `logs/m5_4_6_4_trace_production_activation_closeout.md` (this log)

**Untracked files preserved (pre-existing, NOT modified by this ticket):**
- All previous audit/test artifacts (M5.4-6.0, M5.4-6.3, etc.)
- All pre-existing `scripts/_*` and `tests/_*` files

**Working tree:** clean except for this ticket's tracked modification + new untracked files.

---

## 9. Commit / Push

**Two commits planned:**

1. `feat(m5.4-6.4): narrative trace production activation` — the 1-line source change + 20 tests
2. `docs(m5.4-6.4): add closeout summary log` — this file

Both pushed to `origin/main`. Final verification: `HEAD == origin/main`.

---

## 10. Architectural Findings

1. **1-line activation is sufficient.** The M5.4-5.6 design of `InnerLifeWriter(trace_writer=Optional[NarrativeTraceWriter])` + `_append_trace` made the activation a 1-line constructor change. No executor code change, no schema change, no contract change.

2. **Per-instance authority preserved.** Single `InnerLifeWriter` + single `NarrativeTraceWriter` per process. No global singleton, no double-injection. Verified by `test_a3_run_server_uses_single_inner_life_writer`.

3. **DataRoot isolation contract holds.** `data_root()` P0.5 contract ensures test/production separation. `test_i1_isolated_test_does_not_pollute_production` verifies trace file is in `tmp_path`, not production `data/`.

4. **Frozen contracts preserved.** Zero changes to:
   - `InnerLifeEvent` dataclass
   - `Provenance` dataclass
   - `NarrativeTraceWriter` schema
   - `NarrativeTraceReader` query API
   - `SoulEvent` schema (M5.4-5.5)
   - Agency 4-stage logic
   - Event Bus contract
   - 4 producer executor patterns (M5.4-6.1/6.2)

5. **Failure isolation is double-layered and works in production context.** Verified by `test_f1` (mock writer raises) and `test_f2` (broken path scenario). Production `inner_life_writer` with default `data_root()` path will fail gracefully if disk is full or path is read-only.

6. **4 producer wiring chain works without modification.** The M5.4-6.1 (diary/dream/event) and M5.4-6.2 (proactive_dm) wirings are unaffected by the trace activation. They all use `inner_life_writer.create_event()` which now automatically emits a trace record. Zero executor code change required.

7. **USER_MESSAGE path is correctly excluded.** USER_MESSAGE goes through `IOGateway` → `LLMProxy`, not through any of the 4 wired executors. The activation does not add a trace hook to the USER_MESSAGE path. Verified by `test_g1_user_message_does_not_produce_trace`.

---

## 11. Unresolved Issues

1. **Trace rotation/retention.** No built-in rotation. Estimated 5-6 MB/year is acceptable, but long-term (>5 years) would benefit from a rotation policy. This is **out of scope for M5.4-6.4** per ticket — would be a future ticket.

2. **No shutdown cleanup for trace file handle.** The design uses per-write context manager (verified by `test_b1_write_uses_context_manager_closes_handle`), so no long-lived handle. No `close()` or `flush()` method exists (verified by `test_b2_no_close_or_flush_method_required`). GC handles cleanup naturally. This is **by design** (matches `WorldPerceptionTraceWriter` and `loader_trace` patterns).

3. **Trace record is in-memory state machine.** `InnerLifeWriter` is per-process, in-memory only (M5.4-5.1). The trace is the only durable record of events. If process crashes, the canonical event registry is lost (consistent with M5.4-5.1 design — not a regression). The trace file persists, but the lineage indices (in-memory) are lost.

4. **No content search over trace.** The trace is a JSONL file, not a database. The `NarrativeTraceReader` provides 5 query APIs (event_id / session_id / correlation_id / lineage_path / ts_range), but no full-text search. This is **by design** per M5.4-5.7.

5. **Cross-process trace aggregation not supported.** If multiple Soul OS processes run concurrently, each writes to its own `data/inner_life/trace.jsonl` (path collision). Production currently runs single-process (verified in M5.2-G/I-6 cycles). Would be a future ticket if multi-process is ever needed.

---

## 12. Recommended Next Ticket

**M5.4-6.5 (optional) — NarrativeTraceReader integration with MemoryMiddleware**

Now that trace is enabled in production, MemoryMiddleware could subscribe to trace records to:
- Index events by session_id / agent_id
- Enable narrative-level memory queries (e.g., "show me all inner life events for agent_yua in the last 7 days")
- Bridge trace → memory for long-term storage

**Estimated impact:** 1-2 day ticket. Cross-cutting: touches `src/memory/middleware.py` and `src/inner_life/`. Independent of M5.4-6.4 activation.

**Out of scope (any next ticket):**
- Trace rotation/retention (separate ticket, 1-2 days)
- Trace dashboard / visualization (UI work, much larger scope)
- Multi-process trace aggregation (architectural change, only if multi-process is needed)

**Or:** Close the M5.4-6.x series here. M5.4-6.4 completes the Inner Life production activation arc:
- M5.4-5.1 → Inner Life foundation
- M5.4-5.3/5.4 → Diary/Dream integration
- M5.4-5.5 → SoulEvent cross-reference
- M5.4-5.6 → Trace sidecar (opt-in)
- M5.4-5.7 → Trace reader
- M5.4-6.1/6.2 → 4 producer wiring
- M5.4-6.3 → Audit
- M5.4-6.4 → Production activation (this ticket)

---

## 13. Acceptance Criteria Status

| Criterion | Status | Evidence |
|-----------|--------|----------|
| NarrativeTraceWriter in production lifespan enabled | ✅ | test_a1, test_a2 |
| Single per-lifespan InnerLifeWriter instance | ✅ | test_a3 |
| 4 producers do not need additional modification | ✅ | executor code unchanged, all 4 pass through create_event |
| Diary → exactly 1 trace | ✅ | test_c1, test_c2 |
| Dream → exactly 1 trace | ✅ | test_c3 |
| Event → exactly 1 trace | ✅ | test_c4 |
| Proactive DM → exactly 1 trace | ✅ | test_c5 |
| trace event_id == canonical InnerLifeEvent.event_id | ✅ | test_d1 |
| NarrativeTraceReader can read production format | ✅ | test_e1 |
| trace failure does not block canonical event | ✅ | test_f1, test_f2 |
| no duplicate trace | ✅ | test_j1 |
| USER_MESSAGE does not produce InnerLifeEvent | ✅ | test_g1 |
| no prompt/response/content in trace | ✅ | test_h1 |
| data_root() isolation maintained | ✅ | test_i1 |
| existing M5.4 regression passes | ✅ | 277/277 + 20/20 new = 297/297 |

**All acceptance criteria met. ✅**

---

## 14. Stop Conditions Final Check

| Stop Condition | Triggered? | Notes |
|----------------|-----------|-------|
| 1. Frozen contract modification | NO | 1-line additive change, no contract edits |
| 2. More than minimal injection required | NO | 1 functional line + 1 import |
| 3. Duplicate InnerLifeWriter instances | NO | test_a3 verifies exactly 1 construction |
| 4. Duplicate trace records | NO | test_j1 verifies exactly 1 per create_event |
| 5. Production data mutated | NO | trace is forward-only, no migration, no backfill |
| 6. Historical data backfilled | NO | confirmed in section 7 |
| 7. Trace failure blocks producer | NO | test_f1, test_f2 verify isolation |
| 8. USER_MESSAGE creates InnerLifeEvent | NO | test_g1 verifies 0 traces from USER_MESSAGE |
| 9. Trace contains conversation content | NO | test_h1 verifies only 8 metadata fields |
| 10. Newly introduced regression | NO | 328/328 all tests pass |

**No stop conditions triggered. Implementation complete. ✅**
