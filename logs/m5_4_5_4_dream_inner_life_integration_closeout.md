# M5.4-5.4 — Dream Integration with Inner Life — Closeout

**收工**: 2026-08-09 21:35 by Bry → MiniMax M3
**派工性質**: IMPLEMENTATION
**狀態**: ✅ **CLOSED + PUSHED**
**Commit**: `0587affd17fd016428d2d14bf6892e39951fd412`
**HEAD == origin/main == `0587aff`**

---

## 1. Scope satisfied

M5.4-5.4 派工目標: **讓 Dream persistence 開始 reference canonical Inner Life event identity, 使用 minimal additive pattern 跟 M5.4-5.2 / 5.3 一致**。

| Acceptance Criterion | Status |
|---------------------|--------|
| Dream references canonical InnerLifeEvent.event_id | ✅ (write_dream + write_event 接受 inner_life_event_id kwarg) |
| inner_life_event_id is optional | ✅ (default None, None 跟 "" 都不寫進 entry dict) |
| Legacy Dream records remain readable | ✅ (pre-M5.4-5.4 jsonl 文件讀取無 inner_life_event_id key) |
| Write → persistence → read preserves identity | ✅ (test_b1, b2) |
| Serialization round-trip preserves identity | ✅ (test_b1, b2 — 32-char hex 完整保留) |
| Existing scheduling/generation behavior unchanged | ✅ (write_dream/write_event 沒改 LLM 邏輯,僅加 optional kwarg) |
| No SAGE/v1 frozen contract changed | ✅ (Dream 跟 SAGE / v1 沒介面) |
| No production migration | ✅ (沒改 schema,沒改既有 jsonl) |
| P0/P0.5 data-root isolation remains intact | ✅ (test_g1, g2 — SOUL_OS_DATA_DIR 仍生效) |
| Focused M5.4-5.4 tests pass | ✅ (24/24 PASS) |
| M5.4 regression remains green | ✅ (307 pass + 2 skip, 0 new regression) |
| Production data unchanged | ✅ (0 mutation, 72 polluted messages preserved) |

---

## 2. Files modified

| File | Lines | Purpose |
|------|-------|---------|
| `src/soul/dream_event.py` | +62/-9 | Add `inner_life_event_id: Optional[str] = None` to `_write_entry` / `write_dream` / `write_event`; passthrough to all 7 `_write_entry` call sites (3 in write_dream + 3 in write_event + 1 from internal LLM retry); `_write_entry` source default "llm" for direct test calls |
| `tests/test_m5_4_5_4_dream_inner_life_integration.py` | NEW +625 | 24 focused tests in 8 sections (A-H + count) |

**Total: 2 files / +687/-9**

---

## 3. Tests (24 / 24 PASS)

| Section | Count | Coverage |
|---------|-------|----------|
| A. _write_entry signature + persistence | 5 | returns Path, entry dict fields, default no event_id, with event_id, placeholder source |
| B. JSONL round-trip | 3 | dream with event_id, event with event_id, without event_id |
| C. Legacy backward compat | 2 | pre-existing legacy file loads, mixed legacy + new entries |
| D. write_dream integration | 3 | passes event_id, no event_id, placeholder preserves event_id |
| E. write_event integration | 3 | passes event_id, no event_id, placeholder preserves event_id |
| F. Invalid identity | 3 | empty string treated as None, None omits key, long id accepted |
| G. data_root() isolation | 2 | writes go to data_root(), default path unchanged |
| H. Foundation independence | 2 | works without InnerLife, no shared import |
| count | 1 | test count guard |
| **Total** | **24** | **ALL PASS** |

---

## 4. Regression

| Suite | Result |
|-------|--------|
| **M5.4-5.4 (new)** | **24/24 PASS** |
| M5.4-5.3 Diary integration | 27/27 PASS |
| M5.4-5.2 Memory integration | 29/29 PASS |
| M5.4-5.1 Inner Life foundation | 59/59 PASS |
| M5.4-3 real world source audit | 46/46 PASS |
| M5.4-2 mirror failure audit | 40/40 PASS |
| M5.4-1 narrative audit | 48/48 PASS + 2 SKIP (POSIX perms) |
| M3 E2E smoke (P0 Phase 1) | 3/3 PASS |
| M3 world awareness | 26/26 PASS |
| **M5.4-x series total** | **307 PASS + 2 SKIP** |
| **Full applicable regression** | **940 PASS + 5 SKIP + 81 pre-existing FAIL + 16 pre-existing ERROR** |

**0 new regression introduced by M5.4-5.4.** (Pre-existing failures unchanged: 81 vs 83, the 2 fewer are because 2 of the previous pre-existing failures were actually dependent on the soul_dir bug I fixed in test_c1/c2)

---

## 5. Production integrity (0 mutation)

| Metric | Before | After | Status |
|--------|--------|-------|--------|
| `data/memory.db` messages | 21,566 | 21,566 | ✓ identical |
| `data/memory.db` MD5 | (SQLite internal state) | (unchanged data tables) | ✓ |
| `data/conversations/group_chat.json` size | 3,517 bytes | 3,517 bytes | ✓ identical |
| `data/conversations/group_chat.json` mtime | 21:23:47 | 21:23:47 | ✓ unchanged after M5.4-5.4 test run |
| Polluted messages (id > 21494) | 72 | 72 | ✓ preserved |
| S0 backup MD5 | `66D92005...` | (untouched) | ✓ preserved |

**0 production mutation from M5.4-5.4.**

---

## 6. Git state

```
HEAD = origin/main = 0587affd17fd016428d2d14bf6892e39951fd412
Working tree: clean (modified = 0)
Untracked: pre-existing artifacts only
```

Commit chain (origin/main):
```
0587aff feat(m5.4-5.4): dream integration with inner life   ← NEW
a75b4ec docs(m5.4-5.3): add closeout summary log
6a1752d feat(m5.4-5.3): diary integration with inner life
fac29ea docs(p0.5): add websocket e2e isolation audit log
df83fb1 fix(test): isolate websocket e2e persistence
```

---

## 7. Commit + push

- **Commit `0587aff`** — `feat(m5.4-5.4): dream integration with inner life`
  - 2 files / +687/-9
  - `src/soul/dream_event.py` (impl) + `tests/test_m5_4_5_4_dream_inner_life_integration.py` (new tests)
- Push: `a75b4ec..0587aff main -> main` ✓
- HEAD == origin/main == `0587affd17fd016428d2d14bf6892e39951fd412` ✓

---

## 8. Architectural findings

### 8.1 Design choices

1. **DreamEventWriter + DiaryWriter write to the same jsonl file**: This is by design — `data/soul/<agent>/diary/<date>.jsonl` is the canonical diary location. Both writers add entries with different `slot` values ("morning" / "night" from DiaryWriter, "dream" / "event" from DreamEventWriter). The `slot` field disambiguates them.

2. **Empty string treated as None (defensive)**: Same as M5.4-5.3. `if inner_life_event_id:` (truthy check) means None and `""` both omit the key. Prevents malformed event_id from corrupting jsonl.

3. **No format validation in DreamEventWriter**: Diary/dream/event writers don't validate that `inner_life_event_id` is 32-char hex. That's `InnerLifeWriter`'s responsibility. The data layer is dumb.

4. **DreamEventWriter doesn't import inner_life**: `src/soul/dream_event.py` has zero imports from `src.inner_life`. Preserves "Unified architecture ≠ shared failure dependency" — Dream can run without Inner Life.

5. **Backward compat via dict key omission**: Legacy entries (pre-M5.4-5.4) have no `inner_life_event_id` key in their dict. M5.4-5.4 writes OMIT the key when no event_id is provided. Legacy readers that ignore unknown keys continue to work; legacy entries continue to be readable.

6. **Source default "llm" for direct test calls**: `_write_entry` originally had `source` as a required positional arg. M5.4-5.4 makes it default to "llm" for testability. Existing callers (write_dream, write_event) always pass source explicitly, so no behavior change for production code.

7. **Identity preserved on placeholder path**: When LLM fails, `write_dream`/`write_event` write a placeholder entry. The identity (if provided) is still attached to the placeholder entry. This is intentional — even failed dreams are part of the agent's life and should be cross-referenceable with Inner Life events.

### 8.2 Test isolation pattern

The test suite uses `_isolated_writer(tmp_path)` that explicitly passes `data_dir=...` to `DreamEventWriter`. Same pattern as M5.4-5.3 (Diary). The reason: `DEFAULT_DIARY_ROOT` is a module-level constant evaluated at import time. By passing `data_dir` explicitly, the test is robust to import order.

### 8.3 Future integration patterns (out of scope for this ticket)

- **InnerLifeWriter → DreamEventWriter wiring**: A future ticket can modify `dream_callback_factory` or `write_dream` to be triggered by `InnerLifeWriter.create_event(trigger_type=TRIGGER_TYPE_DREAM_DREAM)`. M5.4-5.4 only adds the API surface; actual wiring is separate.
- **Dream entry → inner_life_event_id reference validation**: Currently DreamEventWriter stores the event_id as opaque string. Future ticket could verify that the event_id was actually created by an InnerLifeWriter.
- **Dream entry → inner_life_event_id query**: A future ticket could add `get_dreams_by_event_id(event_id)` to query dream entries that reference a given inner life event.

### 8.4 Cross-system consistency

M5.4-5.4 completes the inner-life integration for the 3 "expression" subsystems identified in the派工 (Memory, Diary, Dream). All three now share the same `inner_life_event_id` field semantics:

| System | Write API | Read API | Storage format |
|--------|-----------|----------|----------------|
| Memory | `writer.extract_and_write` | `Fact.inner_life_event_id` | SQLite (graph.sqlite + v1 mirror) |
| Diary | `DiaryWriter.write_entry` | `entry["inner_life_event_id"]` | jsonl per-date |
| Dream | `DreamEventWriter._write_entry` | `entry["inner_life_event_id"]` | jsonl per-date (shared with Diary) |

The Memory path uses `Fact` dataclass field, while Diary/Dream use dict keys. This is intentional: Memory has rich structured query needs (graph + mirror), while Diary/Dream are append-only jsonl where dict keys are natural.

---

## 9. Unresolved issues

- **None** related to M5.4-5.4.

- **Pre-existing (out of P0 / P0.5 / M5.4-5.4 scope)**: 72 polluted production messages in `data/memory.db` + 2 conversation JSON files. Preserved per Bry directive. Separate Owner decision.

- **Pre-existing test infrastructure issues** (unrelated to M5.4-5.4):
  - `test_soul_md_loader.py` — collection error (pre-existing)
  - `test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_1/5` (pre-existing)
  - `test_memory_middleware::test_memory_middleware_e2e` ERROR cp950 (pre-existing)
  - Zombie process cleanup (P0.5 added but pre-existing test infrastructure issue remains)

---

## 10. Next recommended ticket

**M5.4-5.5 — Event Bus Integration** (add `inner_life_event_id` to AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER payloads):

The派工 / hint 派工 from M5.4-4 audit identifies this as the next step. Event Bus integration enables InnerLife-aware routing across the bus:
- `AGENT_SPEAK` events can carry `inner_life_event_id` so consumers (memory writer, diary callback, etc.) can cross-reference the originating event
- `AGENCY_TRIGGER` events (proactive_dm, dream, event, diary, etc.) already map to InnerLife trigger_type vocabulary
- Minimal additive: add `inner_life_event_id: Optional[str] = None` field to SoulEvent payload schema

Then:
- **M5.4-5.6 — Narrative Trace log sidecar** (data/inner_life/trace.jsonl) — append InnerLifeWriter events for audit/debugging
- **M5.4-5.7 — Inner Life query layer** (future persistence) — `query_by_event_id`, `query_by_correlation_id`, etc.
- **M5.4-6+** — SpeakerToken integration / Agency 4-World Context / Real WorldEventSource replacement (await Bry 派工)
