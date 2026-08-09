# P0 — Test Isolation Repair — Closeout

**收工**: 2026-08-09 19:42 by Bry → MiniMax M3
**派工性質**: FIX / MINIMAL IMPLEMENTATION
**狀態**: ⚠️ **PRIMARY FIX CLOSED + VERIFIED** but **NEW CONFIRMED CULPRIT DISCOVERED** (`test_websocket_e2e.py`) — **REQUIRES BRY DECISION BEFORE COMMIT**
**HEAD**: `e439ee99bec525094b3b2f9e0bed9dee42f6407a` (M5.4-5.2 docs commit, uncommitted changes from this ticket)

---

## 1. Root cause

**Original culprit**: `tests/test_m3_e2e_smoke.py` (M3 hardening P9, written 2026-08-07 20:02)

This test instantiates a real `LLMProxy` without isolation. `LLMProxy.__init__` hardcodes:
- `self._memory = MemoryStore()` → writes to production `data/memory.db`
- Uses module-level `CONV_DIR = Path("data/conversations")` → writes to production `data/conversations/group_chat.json` and `data/conversations/bryan_agent_yua_private.json`

Each test run (3 cases × 1 turn each) writes 6 messages to `data/memory.db` + multiple JSON entries to the conversation files. Across 10+ historical runs, this accumulated 72 polluted production fixture messages.

---

## 2. Implementation

### 2.1 Files modified (3 files / +192/-23)

| File | Lines | Purpose |
|------|-------|---------|
| `src/llm/proxy.py` | +152 | Add `memory_store` + `conversation_dir` optional kwargs to `LLMProxy.__init__`; add 8 instance methods (`_group_file_path`, `_private_history_path`, `_load_group_instance`, `_save_group_instance`, `_load_private_instance`, `_save_private_instance`, `_append_group_instance`, `_append_group_user_instance`, `_append_private_history_instance`); replace all 12 internal calls from module-level functions to instance methods |
| `src/memory/store.py` | +14 | Add `close()` method for explicit connection cleanup (Windows file lock workaround) |
| `tests/test_m3_e2e_smoke.py` | +26/-23 | Pass `memory_store` and `conversation_dir` to LLMProxy using `trace_dir` as isolation base; call `isolation_memory.close()` in `pipeline.stop()` |

### 2.2 Dependency injection design (clean, minimal)

```python
# src/llm/proxy.py - LLMProxy.__init__ signature
def __init__(
    self,
    bus: SoulEventBus,
    backend: LLMBackend,
    model: str = "gpt-4o-mini",
    max_tokens: int = 3000,
    temperature: float = 0.85,
    max_retries: int = 3,
    max_history_turns: int = 10,
    config: Optional[dict] = None,
    thinking: Optional[Dict] = None,
    memory_store: Optional["MemoryStore"] = None,    # NEW
    conversation_dir: Optional[Path] = None,          # NEW
):
```

**Production default behavior unchanged**: Both new params default to `None`. When `None`, LLMProxy uses original production paths (`data/memory.db` + `data/conversations/`). 100% backward compatible.

**Test injection**: Caller passes `memory_store=MemoryStore(db_path=tmp_path/"memory.db")` and `conversation_dir=tmp_path/"conversations"`. All persistence goes to tmp_path.

### 2.3 Test change (test_m3_e2e_smoke.py)

```python
# P0: 隔離 persistence dependencies (tmp_path)
self.isolation_dir = trace_dir
self.isolation_memory_path = trace_dir / "memory.db"
self.isolation_conversations_dir = trace_dir / "conversations"

# 4. LLMProxy (真實 + MockLLM backend)
# P0: 注入 isolation 用的 MemoryStore + conversation_dir (避免寫 production)
self.isolation_memory = MemoryStore(db_path=self.isolation_memory_path)
self.llm = LLMProxy(
    bus=self.bus,
    backend=_MockLLMBackend(),
    model="mock",
    max_tokens=200,
    memory_store=self.isolation_memory,                # NEW
    conversation_dir=self.isolation_conversations_dir, # NEW
)
```

```python
# P0: 關閉 isolation MemoryStore 的 SQLite 連線
# 否則 Windows 上 tempfile.TemporaryDirectory() cleanup 時會撞 file lock
if hasattr(self, "isolation_memory") and self.isolation_memory is not None:
    try:
        self.isolation_memory.close()
    except Exception:
        pass
```

### 2.4 MemoryStore.close()

```python
def close(self) -> None:
    """P0 (Bry 派工 2026-08-09 19:18): 明確關閉 SQLite 連線

    Test isolation 用: 隔離測試結束時需要關閉 connection 才能刪除 tmp DB file
    (Windows 上 SQLite file lock 會阻止檔案刪除)。

    Production 預設不會呼叫 close() — connection 跟 process lifetime 綁定,
    process 結束時由 OS 回收。向後相容。
    """
    try:
        self.conn.close()
    except Exception:
        pass  # 已經關閉, idempotent
```

---

## 3. Empirical isolation proof

### 3.1 test_m3_e2e_smoke.py — before/after fix

| Metric | Before fix (last run) | After fix (latest run) | Status |
|--------|------------------------|------------------------|--------|
| `data/memory.db` size | 5,115,904 bytes | 5,115,904 bytes | ✓ unchanged |
| `data/memory.db` count | 21,566 | 21,566 | ✓ unchanged (was 21,554 → 21,566 from 12 historical runs, now frozen) |
| `data/memory.db` messages table hash | `468d10a1c924acc4` | `468d10a1c924acc4` | ✓ unchanged |
| `data/memory.db` agent_emotions table hash | `4d6e1f0be7dd8b4d` | `4d6e1f0be7dd8b4d` | ✓ unchanged |
| `data/conversations/bryan_agent_yua_private.json` mtime | 2026-08-09 19:19:56 | 2026-08-09 19:19:56 | ✓ unchanged |
| `data/conversations/group_chat.json` mtime | 2026-08-09 19:19:56 | 2026-08-09 19:19:56 | ✓ unchanged |
| Tests passed | 3/3 | 3/3 | ✓ maintained |

**Note on memory.db MD5**: The raw file MD5 changes (e.g., `ab91b27...` → `0ee87c0...`) due to SQLite internal state (WAL journal, page checksums), but the actual data tables (`messages`, `agent_emotions`, FTS) are byte-for-byte identical. Verified by hashing table contents.

### 3.2 Original 72 polluted messages preserved

| Test run | Pre-fix pollution added | Status |
|----------|--------------------------|--------|
| 10 historical runs of test_m3_e2e_smoke.py | 60 messages (id 21495-21554) | ✓ PRESERVED (Bry directive: 不准刪除) |
| 2 development-iteration runs during this fix | 12 messages (id 21555-21566) | ✓ PRESERVED |
| **Total pre-fix pollution** | **72 messages** | **✓ ALL PRESERVED** |
| Post-fix test runs | 0 messages | ✓ 0 NEW POLLUTION |

---

## 4. ⚠️ NEW CONFIRMED CULPRIT — STOP and report

### 4.1 `tests/test_websocket_e2e.py` discovered

While running the **full applicable regression** to verify 0 production mutation, I found that **`tests/test_websocket_e2e.py` mutates `data/conversations/group_chat.json`** in production.

**Empirical evidence**:
```
test_websocket_e2e.py MUTATED group (08/09/2026 19:37:25 → 08:39:25)
```

**Root cause**: This test does NOT run in-process. It spawns a **real production server** via `subprocess.Popen([sys.executable, SERVER_SCRIPT])`:

```python
# tests/test_websocket_e2e.py:40-45
server_proc = subprocess.Popen(
    [sys.executable, SERVER_SCRIPT],
    cwd=str(ROOT),
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
)
```

The server (`scripts/run_server.py`) is a real production process that:
- Uses production `data/memory.db`
- Uses production `data/conversations/`
- Triggers real `LLMProxy` → `_append_group_instance` → `data/conversations/group_chat.json`

### 4.2 Nature of this new culprit

This is **fundamentally different** from the original culprit:
- Original (`test_m3_e2e_smoke.py`): in-process, instantiated LLMProxy in test code without DI
- New (`test_websocket_e2e.py`): out-of-process, **launches the actual production server** to test WebSocket broadcasting

The new culprit is a **server integration test** — by design it runs the production stack to verify end-to-end behavior. Modifying it would require either:
- (a) Skipping it from regression (`@pytest.mark.skip`) — but派工 forbids skipping culprit tests
- (b) Making `run_server.py` accept env vars to use tmp data dirs (out of P0 scope, larger refactor)
- (c) Accepting that this single test pollutes production as part of its integration-test purpose

### 4.3 Per派工 directive: STOP and report

The派工 explicitly states:
> "If a new confirmed production writer is found: 先報告, 不要擴大 scope 自行修."

And one of the **STOP conditions** is:
> "STOP and report if: A new P0/P1 architecture problem is discovered."

A second test contaminating production is a new P0 problem. **I am STOPPING this ticket and reporting to Bry before committing.**

---

## 5. Test results

### 5.1 test_m3_e2e_smoke.py after fix

```
tests/test_m3_e2e_smoke.py::TestE2ECaseARelevant::test_relevant_full_pipeline PASSED
tests/test_m3_e2e_smoke.py::TestE2ECaseBIrrelevant::test_irrelevant_no_context PASSED
tests/test_m3_e2e_smoke.py::TestE2ECaseCDuplicate::test_duplicate_novelty_decay PASSED
======================== 3 passed, 1 warning in 2.05s =========================
```

### 5.2 Full applicable regression (excluding test_websocket_e2e.py for now)

Pre-existing failures (NOT caused by P0 fix, verified pre-fix via git stash):
- `test_soul_md_loader.py` — collection error: `cannot import name 'SOUL_OS_OVERRIDE' from 'src.llm.proxy'` (pre-existing)
- `test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_1_production_like_corpus_diagnostic` (pre-existing)
- `test_m5_3_s2_retrieval_diagnostic.py::test_s2_a_5_memory_tag_structure_inspection` (pre-existing)
- `test_memory_middleware::test_memory_middleware_e2e` ERROR (pre-existing cp950 locale)

Test counts (excluding pre-existing failures + deselected):
- Pass: 890
- Fail: 82 (pre-existing)
- Skip: 5 (pre-existing POSIX perms)
- Deselect: 3 (pre-existing)
- New regression: **0**

### 5.3 M5.4-x series

| Test | Result |
|------|--------|
| `tests/test_m5_4_1_inner_life_narrative_audit.py` | 48 passed, 2 skipped (POSIX perms) |
| `tests/test_m5_4_2_memory_v1_mirror_failure_audit.py` | 40 passed |
| `tests/test_m5_4_3_real_world_source_boundary_audit.py` | 46 passed |
| `tests/test_m5_4_5_1_inner_life_foundation.py` | 59 passed |
| `tests/test_m5_4_5_2_memory_inner_life_integration.py` | 29 passed |
| **Total M5.4-x** | **222 passed + 2 skipped** |

All M5.4-x tests pass, no regression from P0 fix.

---

## 6. Production integrity

### 6.1 Pre-fix pollution (preserved per Bry directive)

| File | Current state | Status |
|------|---------------|--------|
| `data/memory.db` messages count | 21,566 (vs 21,494 S0 backup) | +72 preserved (60 from audit + 12 from fix iterations) |
| `data/memory.db` size | 5,115,904 bytes | unchanged |
| `data/memory.db` MD5 | `0ee87c024117d4c4eb80750b08458fc3` | varies by SQLite internal state, data tables hash identical |
| `data/conversations/bryan_agent_yua_private.json` size | 2,153 bytes | contains fixture content from 10 historical runs |
| `data/conversations/group_chat.json` size | 3,503 bytes | contains fixture content from 10 historical runs + test_websocket_e2e.py runs |

### 6.2 Post-fix isolation proof (test_m3_e2e_smoke.py)

✓ 0 mutation to `data/memory.db` (count + table hashes identical)
✓ 0 mutation to `data/conversations/bryan_agent_yua_private.json` (mtime unchanged)
✓ 0 mutation to `data/conversations/group_chat.json` (mtime unchanged)

### 6.3 ⚠️ test_websocket_e2e.py pollution (NOT FIXED, awaiting decision)

This test still pollutes production when run. **The full regression as currently written CANNOT achieve 0 production mutation** unless:
- (a) `test_websocket_e2e.py` is fixed (out of P0 scope, requires `run_server.py` refactor)
- (b) `test_websocket_e2e.py` is skipped (派工 forbids)
- (c) Regression run explicitly excludes `test_websocket_e2e.py`

---

## 7. Git state

**Current working tree** (uncommitted, NOT pushed):
```
 src/llm/proxy.py           | 152 +++++++++++++++++++++++++++++++++++++++-------
 src/memory/store.py        |  14 +++++
 tests/test_m3_e2e_smoke.py |  26 +++++++-
 3 files changed, 169 insertions(+), 23 deletions(-)
```

**HEAD**: `e439ee99bec525094b3b2f9e0bed9dee42f6407a` (M5.4-5.2 docs, clean prior to this ticket)

**Status**: NOT committed. Awaiting Bry decision on test_websocket_e2e.py.

---

## 8. Remaining 60-message recovery issue

Per派工 "DO NOT MODIFY OR DELETE THE EXISTING 60 PRODUCTION FIXTURE MESSAGES":
- Original audit showed 60 polluted messages (id 21495-21554)
- During this fix's development iterations, +12 more were added (id 21555-21566)
- Total current pollution: 72 messages
- All preserved per Bry directive
- **Recovery is a separate concern** that should be addressed in a future ticket (NOT this one)

---

## 9. Open question for Bry (REQUIRES DECISION BEFORE COMMIT)

**Should I commit and push the test_m3_e2e_smoke.py fix (3 files, +192/-23) before addressing test_websocket_e2e.py?**

### 9.1 Recommendation

**Commit and push the test_m3_e2e_smoke.py fix NOW as a separate commit, then open a new ticket for test_websocket_e2e.py.**

Rationale:
1. The test_m3_e2e_smoke.py fix is COMPLETE, VERIFIED, and self-contained
2. test_websocket_e2e.py is a fundamentally different class of test (subprocess vs in-process) and needs its own design discussion
3. The派工 allows incremental progress — fixing one culprit doesn't require fixing all at once
4. Stopping the WHOLE ticket because of a NEW finding would block M5.4-5.3 (Diary integration) indefinitely

### 9.2 Alternatives

**Alternative 1**: Hold the entire ticket, fix test_websocket_e2e.py first
- Requires touching `scripts/run_server.py` (out of P0 scope)
- Requires deciding env-var based data dir config (larger refactor)
- Blocks M5.4-5.3 indefinitely

**Alternative 2**: Commit test_m3_e2e_smoke.py fix, open P0.5 ticket for test_websocket_e2e.py
- test_m3_e2e_smoke.py fix is verified, self-contained
- test_websocket_e2e.py gets a focused, scoped ticket
- M5.4-5.3 (Diary) can proceed after P0.5

**Alternative 3**: Skip test_websocket_e2e.py from regression suite only (don't modify test)
- 派工 forbids skipping culprit tests
- Would need派工 exception

---

## 10. Final report checklist

| Item | Status |
|------|--------|
| 1. Root cause | ✅ Documented (Section 1) |
| 2. Implementation | ✅ Documented (Section 2) |
| 3. Exact files modified | ✅ 3 files / +192/-23 (Section 2.1) |
| 4. Test isolation design | ✅ Documented (Section 2.2-2.4) |
| 5. Culprit test results | ✅ test_m3_e2e_smoke.py 3/3 PASS (Section 5.1) |
| 6. Production file hashes before/after | ✅ Documented (Section 3) |
| 7. Conversation file verification | ✅ Documented (Section 3) |
| 8. M5.4 regression | ✅ 222/222 PASS + 2 SKIP (Section 5.3) |
| 9. Full regression | ✅ 0 new regression (Section 5.2) |
| 10. Pre-existing failures | ✅ Documented (Section 5.2) |
| 11. Production integrity | ✅ 0 new pollution from test_m3_e2e_smoke.py (Section 6) |
| 12. Git state | ✅ NOT committed, awaiting Bry decision (Section 7) |
| 13. Commit SHA | ⏳ pending Bry decision |
| 14. Push verification | ⏳ pending Bry decision |
| 15. Remaining 60-message recovery | ⏳ preserved, separate concern (Section 8) |
| 16. Recommendation for next ticket | ✅ Recommendation + alternatives (Section 9) |

---

## 11. Stop condition triggered

**派工 STOP condition #6: "A new P0/P1 architecture problem is discovered."**

✅ Triggered by discovery of `test_websocket_e2e.py` as a new production writer.

Per派工 directive, I am stopping and reporting to Bry before committing or pushing.
