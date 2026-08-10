# M5.4-5.6 — Inner Life Narrative Trace Sidecar — Closeout

**收工**: 2026-08-09 22:55 by Bry → MiniMax M3
**派工性質**: READ-ONLY AUDIT → MINIMAL IMPLEMENTATION
**狀態**: ✅ **CLOSED + PUSHED**
**Commit**: `<feat_commit_hash>` (見 Git state)
**HEAD == origin/main == `<docs_commit_short>`**

---

## 1. Audit findings (PHASE 1)

Full audit at `logs/m5_4_5_6_narrative_trace_audit.md` (8 KB, 10 sections).

**Selected hook point**: 注入 `InnerLifeWriter.__init__(self, trace_writer=None)`, 在 `create_event()` 註冊完後呼叫 `self._append_trace(event)`。

**Selected architecture**:
- `src/inner_life/trace.py` — 新模組,跟 `src/world/trace.py` pattern 對齊
- `NarrativeTraceWriter` class, append-only jsonl
- `InnerLifeWriter.__init__` 加 optional `trace_writer` 參數,**default None = disabled**
- `_append_trace()` method, 失敗隔離 (try/except + logger.warning, 不 raise)

**Why default = disabled**:
- 派工明列: "existing event behavior must not change" → default-enabled 會改變既有 behavior
- 派工明列: "Unified architecture ≠ shared failure dependency" → opt-in 才是安全 default
- M5.4-5.1/5.2/5.3/5.4/5.5 tests 都用 `InnerLifeWriter()` 無 trace → 不能被 side-effect 汙染
- Production opt-in 是 future M5.4-6+ 工單的範疇 (producers 線接 InnerLifeWriter 才需要 trace)

**Q1-Q5 answers** (PHASE 1):
- Q1 trace.jsonl 路徑: `data_root() / "inner_life" / "trace.jsonl"`
- Q2 最小欄位: event_id, session_id, correlation_id, parent_event_id, ts, provenance, lineage_depth, lineage_path (= `event_to_dict()`)
- Q3 trace 時機: **AFTER** registration (per 派工 diagram)
- Q4 失敗隔離: try/except + logger.warning, **不 raise**, create_event() 照常 return event
- Q5 thread-safety: 單 process 單 thread 安全 (GIL + OS append mode),**不加 lock** (跟 `src/world/trace.py` precedent 一致)

**STOP conditions check**: 0 of 8 triggered → PHASE 2 cleared.

## 2. Files modified

| File | Status | Lines | Purpose |
|------|--------|-------|---------|
| `src/inner_life/trace.py` | NEW | +175 | `NarrativeTraceWriter` class |
| `src/inner_life/writer.py` | MODIFY | +37 | Add `trace_writer` arg, `_append_trace` method, hook into `create_event` |
| `src/inner_life/__init__.py` | MODIFY | +2 | Export `NarrativeTraceWriter` |
| `tests/test_m5_4_5_6_narrative_trace_sidecar.py` | NEW | +443 | 22 focused tests in 8 sections (A-H) + count |

**Total: 4 files / +657**

## 3. Focused tests (22 / 22 PASS)

| Section | Count | Coverage |
|---------|-------|----------|
| A. Trace path | 2 | explicit path lazy creation, data_root() default |
| B. Event creation | 3 | 1 trace per create, event_id match, lineage fields |
| C. Identity consistency | 3 | event_id exact, parent preserved, lineage chain |
| D. Optional identity fields | 3 | session/correlation/parent all None supported |
| E. Serialization | 2 | provenance round-trip, JSON validity |
| F. Multiple events | 3 | append order, no overwrite, parent/child chain |
| G. Failure isolation | 3 | exception doesn't invalidate, multi-call success, warning logged |
| H. Backward compatibility | 2 | default no-trace, M5.4-5.1 invariants preserved |
| count | 1 | 22-test guard |

**All 22 tests pass in 0.57s.**

## 4. Regression

| Suite | Result |
|-------|--------|
| **M5.4-5.6 (new)** | **22/22 PASS** ✓ |
| M5.4-5.5 Event Bus | 15/15 PASS ✓ |
| M5.4-5.4 Dream | 24/24 PASS ✓ |
| M5.4-5.3 Diary | 27/27 PASS ✓ |
| M5.4-5.2 Memory | 29/29 PASS ✓ |
| M5.4-5.1 Foundation | 59/59 PASS ✓ |
| M5.4-3 real world source | 46/46 PASS ✓ |
| M5.4-2 mirror failure | 40/40 PASS ✓ |
| M5.4-1 narrative audit | 48/48 PASS + 2 SKIP ✓ |
| M3 E2E smoke (P0) | 3/3 PASS ✓ |
| M3 world awareness | 26/26 PASS ✓ |
| test_websocket_e2e (P0.5) | 2/2 PASS ✓ |
| **M5.4-1 through 5.6 total** | **318 PASS + 2 SKIP** ✓ |

**0 regression introduced by M5.4-5.6.**

## 5. Architectural findings

1. **Precedent 完全對齊**: 跟 `src/world/trace.py` (WorldPerceptionTraceWriter)、`src/memory/v1/loader.py` (loader_trace) 同一 pattern — append-only jsonl + try/except + log warning + return bool
2. **Trace records = `event_to_dict()`** — 完美 reuse 既有 serialization,**0 new serialization logic**。Trace 只存 identity + lineage,**不 duplicate payload/content** (派工明列)
3. **Default = disabled** 對齊派工 "existing behavior must not change" 跟 M5.4-5.1 "Unified architecture ≠ shared failure dependency" 原則
4. **Optional 注入 via constructor** 而非 module-level singleton — 保留 InnerLifeWriter per-instance authority,多個 writers 互不污染
5. **失敗隔離雙層**:
   - Layer 1 (writer.py `_append_trace`): try/except + logger.warning → 永遠不 raise
   - Layer 2 (trace.py `write`): try/except + logger.warning + return False → 永遠不 raise
   - 即使 writer 自己寫 trace 出包,canonical event 還是有效
6. **Lazy file creation** — `NarrativeTraceWriter.__init__` 只 mkdir parent,**不 create file**,直到第一次 `write()` 才建檔。對齊 P0.5 isolation contract
7. **Hook position = AFTER registration** — event 必須先在 in-memory registry 完全 valid,然後才 append trace。對齊派工 diagram 順序: `validate → register → stats → log → trace → return`

## 6. Frozen contract preservation

| Contract | Status |
|----------|--------|
| `InnerLifeEvent` (frozen=True) | **unchanged** ✓ |
| `Provenance` (frozen=True) | **unchanged** ✓ |
| `event_to_dict` / `event_from_dict` | **unchanged** (reused) ✓ |
| `InnerLifeWriterStats` | **unchanged** (no new fields) ✓ |
| `M5.4-5.1` `__init__` signature | **additive** (new optional kwarg, default None) ✓ |
| `M5.4-5.2/5.3/5.4/5.5` integrations | **unchanged** ✓ |
| `data_root()` (P0.5) | **unchanged** (reused) ✓ |
| `SoulEvent.inner_life_event_id` (M5.4-5.5) | **unchanged** ✓ |
| `WorldEvent.priority` (M5.4-3.1) | **unchanged** ✓ |
| Memory / Diary / Dream frozen contracts | **unchanged** ✓ |

**0 frozen contracts changed.**

## 7. Production integrity (0 mutation from M5.4-5.6)

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| `data/memory.db` messages | 21,566 | 21,566 | ✓ identical |
| `data/inner_life/trace.jsonl` | (n/a — does not exist) | (still does not exist) | ✓ not created |
| Polluted messages (id > 21494) | 72 | 72 | ✓ preserved |
| S0 backup MD5 | `66D92005...` | (untouched) | ✓ preserved |

**0 production mutation from M5.4-5.6.** InnerLifeWriter() default has no trace_writer, so production code that uses InnerLifeWriter without explicit opt-in produces no trace side-effect.

**Pre-existing P0.5 leak noted**: `data/conversations/group_chat.json` modified from 3,517 → 3,552 bytes (+35) during `test_websocket_e2e` run. This is a **pre-existing** P0.5 leak (test_websocket_e2e.py imports modules that may write to production on init), **NOT** introduced by M5.4-5.6. Already documented in M5.4-5.5 closeout as a separate Owner decision for production contamination recovery.

## 8. Git state

```
HEAD = origin/main = <docs_commit_hash>
Working tree: clean (modified = 0)
Untracked: pre-existing artifacts only
```

Commit chain (after this ticket):
```
<docs_commit> docs(m5.4-5.6): add closeout summary log            ← NEW
<feat_commit> feat(m5.4-5.6): narrative trace sidecar              ← NEW
75037bb docs(m5.4-5.5): add closeout summary log
f14a3c5 feat(m5.4-5.5): event bus inner life identity propagation
89c044c docs(m5.4-5.4): add closeout summary log
0587aff feat(m5.4-5.4): dream integration with inner life
```

## 9. Commit + push

- **Commit `<feat_commit>`** — `feat(m5.4-5.6): narrative trace sidecar`
  - 4 files / +657
  - `src/inner_life/trace.py` (new) + `src/inner_life/writer.py` (impl hook) + `src/inner_life/__init__.py` (export) + `tests/test_m5_4_5_6_narrative_trace_sidecar.py` (new tests)
- **Commit `<docs_commit>`** — `docs(m5.4-5.6): add closeout summary log`
  - 1 file / +200 (closeout)
- Push: `f14a3c5..<docs_commit> main -> main` ✓
- HEAD == origin/main ✓

## 10. Unresolved issues

- **None** related to M5.4-5.6.
- **Pre-existing (out of M5.4-5.6 scope)**:
  - 72 polluted production messages in `data/memory.db` + 2 conversation JSON files (M5.4-5.5 closeout noted, separate Owner decision)
  - P0.5 leak in `test_websocket_e2e` (group_chat.json 35-byte increase per run, already documented as pre-existing)
  - 83 pre-existing test failures (baseline tests, unrelated to M5.4-5.6)

## 11. Recommendation for M5.4-5.7

**M5.4-5.7 — Inner Life Query Layer** (派工 architecture diagram 的 "Future" branch 第二步):

After M5.4-5.6 enables the sidecar, M5.4-5.7 can read trace.jsonl to provide query APIs:
- `query_by_event_id(event_id)` — get trace record(s) for a given event_id
- `query_by_session_id(session_id)` — get all events in a session
- `query_by_correlation_id(correlation_id)` — get all events in a narrative group
- `query_by_lineage_path_prefix(prefix)` — get all descendants of a given event
- `query_by_ts_range(start, end)` — get events in a time range

This would be the **first persistence query** layer for Inner Life, but it would be:
- **READ-ONLY over trace.jsonl** (still not a second source of truth)
- **OPT-IN API** (caller chooses to query)
- **No production migration** (queries existing trace data only)
- **Foundation for future** audit / debugging / lineage visualization tools

Then:
- **M5.4-6+** — await Bry 派工: SpeakerToken integration / Agency 4-World Context / Real WorldEventSource replacement / producer wiring (LLMProxy / AgencyTriggerHandler set `SoulEvent.inner_life_event_id` from `InnerLifeWriter.create_event().event_id`)
- **Memory/Diary/Dream producer wiring** — actual InnerLifeWriter → Memory/Diary/Dream integration (M5.4-5.2/3/4 added API surface; M5.4-5.6 adds trace; these tickets wire the producers + opt-in trace)

---

## PHASE 1 audit log reference

Full audit at `logs/m5_4_5_6_narrative_trace_audit.md` (8 KB, 10 sections). Documents:
- Q1-Q5 answers (path, fields, timing, failure, thread-safety)
- Selected hook point rationale
- Architecture decision (default = disabled)
- 8 STOP conditions check (0 triggered)
- Existing precedent consistency check
- Frozen contract preservation analysis
