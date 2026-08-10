# M5.4-5.7 — Inner Life Query Layer — Closeout

**收工**: 2026-08-10 00:05 EDT by Bry → MiniMax M3
**派工性質**: READ-ONLY AUDIT → MINIMAL IMPLEMENTATION
**狀態**: ✅ **CLOSED + PUSHED**
**Commit feat**: `2a8c7a7`
**Commit docs**: `f414b8e`
**HEAD == origin/main == `f414b8e`**

---

## 1. Audit findings (PHASE 1)

**Files inspected**: `src/inner_life/trace.py`, `src/inner_life/writer.py`, `src/inner_life/__init__.py`, `src/inner_life/serialization.py`, `src/inner_life/event.py`, M5.4-5.6 tests.

**Confirmed**:
1. trace.jsonl is append-only (`open(..., "a")`) — no overwrite possible
2. `event_to_dict()` / `event_from_dict()` remain canonical serialization
3. Reader does NOT become a source of truth (read-only, returns dicts)
4. `data_root()` isolation intact via same path convention
5. Malformed records handled by `read_all()` per-line try/except + log warning
6. Legacy trace records readable (raw dict reading)

**STOP conditions: 0 of 8 triggered → PHASE 2 cleared.**

---

## 2. API design

`NarrativeTraceReader` class with 5 read-only query methods:

| Method | Returns | Semantics |
|--------|---------|-----------|
| `query_by_event_id(event_id)` | `list[dict]` (0 or 1) | exact match |
| `query_by_session_id(session_id)` | `list[dict]` | all records with that session |
| `query_by_correlation_id(correlation_id)` | `list[dict]` | all records in that narrative group |
| `query_by_lineage_path_prefix(prefix)` | `list[dict]` | lineage_path starts with prefix OR event_id == prefix |
| `query_by_ts_range(start, end)` | `list[dict]` | ISO 8601 lexicographic range, inclusive |

Key design decisions:
- **Returns `dict`, not `InnerLifeEvent`** — no `event_from_dict()` needed, no schema validation on read
- **`lineage_path_prefix` matches via `startswith(prefix/)` + `lineage_path == prefix` + `event_id == prefix`** — three conditions needed to cover root/child/leaf cases
- **No caching** — per派工 requirement; each query reads the file
- **No database** — per派工 requirement
- **Malformed lines skipped** — per-line try/except + logger.warning, returns partial valid list
- **Missing file returns `[]`** — no raise

---

## 3. Files modified

| File | Status | Lines | Purpose |
|------|--------|-------|---------|
| `src/inner_life/trace_reader.py` | NEW | +210 | `NarrativeTraceReader` class |
| `src/inner_life/__init__.py` | MODIFY | +1 | Export `NarrativeTraceReader` |
| `tests/test_m5_4_5_7_trace_reader.py` | NEW | +477 | 22 focused tests in 12 sections (A-L) |

**Total: 3 files / +688**

---

## 4. Focused tests (22 / 22 PASS)

| Section | Count | Coverage |
|---------|-------|----------|
| A. Missing trace file | 2 | non-existent file, empty dir |
| B. event_id query | 2 | exact match, unknown id |
| C. session_id query | 2 | known session, unknown session |
| D. correlation_id query | 2 | known corr, unknown corr |
| E. Lineage prefix | 3 | root prefix, child prefix, leaf prefix |
| F. Timestamp range | 3 | both bounds, start-only, end-only |
| G. Deterministic ordering | 2 | append order, repeated query |
| H. Malformed records | 2 | bad JSON, truncated lines |
| I. Legacy compatibility | 1 | minimal legacy record |
| J. data_root isolation | 1 | default path via env var |
| K. Read-only guarantee | 1 | query does not mutate file |
| L. Count guard | 1 | 22-test total |

---

## 5. Regression

| Suite | Result |
|-------|--------|
| M5.4-5.7 (new) | **22 PASS** ✓ |
| M5.4-5.6 Narrative Trace | **22 PASS** ✓ |
| M5.4-5.5 Event Bus | **15 PASS** ✓ |
| M5.4-5.4 Dream | **24 PASS** ✓ |
| M5.4-5.3 Diary | **27 PASS** ✓ |
| M5.4-5.2 Memory | **29 PASS** ✓ |
| M5.4-5.1 Foundation | **59 PASS** ✓ |
| M3 E2E smoke | **3 PASS** ✓ |
| M3 world awareness | **26 PASS** ✓ |
| test_websocket_e2e | **1 PASS + 1 pre-existing failure** |
| **M5.4-5.1 through 5.7 total** | **227 PASS + 1 pre-existing** |

**0 regressions introduced by M5.4-5.7.**

Pre-existing failure note: `test_websocket_e2e::test_inject_tick_triggers_agent_speak` — pre-existing P0.5 leak (group_chat.json +35 bytes per run, documented in M5.4-5.5 closeout).

---

## 6. Malformed-record behavior

Per-line JSON parsing in `_read_all()`:
- `json.JSONDecodeError` → skipped + `logger.warning` → continues to next line
- `OSError` → `logger.warning` → returns partial list
- No exception propagates
- Valid records before/after malformed line always returned

---

## 7. Deterministic ordering behavior

All query methods return records in **append order** (the order events were written to trace.jsonl). This is deterministic because:
- JSONL is append-only
- `_read_all()` reads file sequentially
- No sorting, no filtering that would reorder

Repeated calls to the same query return identical results.

---

## 8. Production integrity

| Metric | Status |
|--------|--------|
| `data/memory.db` | unchanged ✓ |
| `data/inner_life/trace.jsonl` | unchanged (reader only) ✓ |
| 72 polluted messages | preserved ✓ |
| Frozen contracts | unchanged ✓ |

Reader is **read-only** — cannot modify production data.

---

## 9. Git state

```
HEAD = origin/main = f414b8e
Working tree: clean (modified = 0)
Untracked: pre-existing artifacts only
```

Commit chain (after this ticket):
```
f414b8e docs(m5.4-5.7): add closeout summary log        ← HEAD
2a8c7a7 feat(m5.4-5.7): inner life query layer          ← feat
bb37b18 docs(m5.4-5.6): add closeout summary log
```

---

## 10. Commit + push

- **Commit `2a8c7a7`** — `feat(m5.4-5.7): inner life query layer`
  - 3 files: `src/inner_life/trace_reader.py` (new) + `src/inner_life/__init__.py` (export) + `tests/test_m5_4_5_7_trace_reader.py` (new)
- **Commit `f414b8e`** — `docs(m5.4-5.7): add closeout summary log`
  - 1 file: `logs/m5_4_5_7_closeout.md`
- Push: `origin/main` ✓ (`1a1c9a2..f414b8e`)
- HEAD == origin/main ✓ (`f414b8e`)

---

## 11. Architectural findings

1. **Minimal read-only layer**: `NarrativeTraceReader` is the simplest possible query layer — `_read_all()` + Python list comprehensions. No new abstractions needed.
2. **No caching by design**: Per派工 requirement. If performance becomes an issue, a future ticket can add caching with evidence.
3. **Malformed record resilience**: Per-line try/except is the right granularity — one bad line never corrupts valid records before or after.
4. **Lineage prefix = three conditions**: Root events have `lineage_path == event_id`. Child events have `lineage_path = root_id/child_id`. Grandchildren have `lineage_path = root_id/child_id/grandchild_id`. The query needs to handle all three cases.
5. **Determinism via append order**: No sorting needed — the JSONL append order IS the deterministic order.
6. **ISO 8601 lexicographic comparison works**: All trace timestamps are ISO 8601 UTC strings. String comparison is equivalent to time comparison because the format is `YYYY-MM-DDTHH:MM:SSZ` which sorts lexicographically.

---

## 12. Unresolved issues

- **None** related to M5.4-5.7.
- **Pre-existing (out of M5.4-5.7 scope)**:
  - 72 polluted production messages in `data/memory.db` (M5.4-5.5 closeout noted)
  - P0.5 leak in `test_websocket_e2e` (group_chat.json 35-byte increase per run)
  - 83 pre-existing test failures (baseline unrelated to M5.4-5.7)

---

## 13. Recommended next ticket

**M5.4-6 — SpeakerToken + Agency 4-World Context** (per M5.4-5.6 closeout):

After M5.4-5.7 completes the Inner Life query layer, the natural next step is to wire up the producer side:
- **SpeakerToken integration**: Agent reply → `InnerLifeWriter.create_event()` → get `event_id` → store in `SoulEvent.inner_life_event_id` (M5.4-5.5 surface already exists)
- **Agency 4-World Context**: Real `WorldEventSource` replacement using actual perception data
- **LLMProxy / AgencyTriggerHandler**: Set `SoulEvent.inner_life_event_id` from `InnerLifeWriter.create_event().event_id`

These are **future Bry 派工 decisions** — do not self-start.
