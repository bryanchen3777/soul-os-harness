# M5.4-3.1 — World Event Bus Contract Alignment Summary

**派工**: 2026-08-09 17:43 by Bry  
**性質**: MINIMAL IMPLEMENTATION / CONTRACT REPAIR  
**狀態**: ✅ **CLOSED + PUSHED** (`daf0f78`)

---

## 1. Contract Repairs (2 個 MEDIUM findings from M5.4-3 audit)

### M1: Priority preservation through bus-payload

**Before (M5.4-3 finding)**:
- `WorldEvent.to_payload()` 不包含 priority(M3.1 Phase B 向後相容 contract)
- `validate_world_event` 重建 WorldEvent 時 priority 預設 0
- → M3.2-A `priority_boost` 在 end-to-end bus-payload 路徑下**dead code**

**After (M5.4-3.1 fix)**:
- `to_payload()` 新增 `"priority": self.priority` 欄位
- `from_payload()` 從 payload 讀 priority,`payload.get("priority", 0)` 向後相容
- `validate_world_event()` 也 pass priority 給 `WorldEvent` constructor(真實 reconstruction path)
- **M3.2-A `priority_boost` 在 E2E 真正生效**
- Frozen M3 contract 100% 保留(`{source, type, novelty_id, ts, summary, data}` 都還在,只是 additive 加 priority)
- 防禦性: 非 int / bool 視為 0(避免 TypeError 從 `__post_init__` crash middleware)

### M2: Injector boundary alignment

**Before (M5.4-3 finding)**:
- `WorldEventInjector` Protocol 定義 `async def inject(event)`
- `WorldPerceptionMiddleware` 沒實作 `inject` method
- → Dispatcher 不能用 middleware 當 injector(I1 test 觀察到 AttributeError)
- → 真實 source → middleware chain 必須繞過 Dispatcher 走 bus

**After (M5.4-3.1 fix)**:
- `WorldPerceptionMiddleware.inject(event)` 新增 method
- 委派給 `process_world_event_direct` (single processing path)
- Conforms `WorldEventInjector` Protocol
- **exactly-once**:每次 `inject` 只跑一次 `_on_world_event`,沒有 duplicate 處理
- 不直接走 `bus.publish` 是因為 middleware 自己就是 bus subscriber(避免自己訂閱自己 race)

---

## 2. Frozen Contract Preservation

| Frozen Item | Status | Evidence |
|-------------|--------|----------|
| M5.3 259/259 + 50/50 + N-3 | ✅ 維持 | 297 pass + 2 skip + 2 deselect |
| SCORE_WEIGHTS 5 維度 sum=1.00 | ✅ 不變 | J1 test 維持 |
| PRIORITY_BOOST_WEIGHT=0.05 (additive) | ✅ 不變 | J2 test 維持 |
| compute_scores formula | ✅ 不變 | priority 0 → boost 0,priority 5 → boost 0.4 (H2 test 維持) |
| validate_world_event M3 必填欄位 | ✅ 不變 | D1-D6 tests 全部維持 |
| Frozen event types (AGENT_INTENT_*, WORLD_EVENT) | ✅ 不變 | J3 test 維持 |
| Memory Loader / MemoryMiddleware | ✅ 完全沒動 | 0 commit 在 src/memory/ |
| Diary / Dream | ✅ 完全沒動 | 0 commit 在 src/soul/ 除 scheduler |
| Agency / Scheduler | ✅ 完全沒動 | 0 commit 在 src/agency/ + src/soul/scheduler.py |
| retrieval / World Awareness tuning | ✅ 不變 | 0 commit 在 S-2-B 相關 code |
| production data (memory.db, v1, diary) | ✅ 0 mutation | 詳見 §3 |

---

## 3. Production Data 0 Mutation Verification

| Resource | Before (M5.3) | After (M5.4-3.1) | Status |
|----------|----------------|-------------------|--------|
| `git rev-parse HEAD` | `02ab486` | `daf0f78` (PUSHED) | new commit |
| Working tree modified | 0 files | 0 files | clean |
| `data/memory.db` size | 5,115,904 | 5,115,904 | unchanged |
| `data/memory.db.backup-20260809` MD5 | `66D920058007FF1252E4FD23C288F2E9` | `66D920058007FF1252E4FD23C288F2E9` | unchanged |
| v1 store (`data/memory/`) | 44 files / 11,494,060 bytes | 44 files / 11,494,060 bytes | unchanged |
| `data/memory/agent_rem/memories.jsonl` LastWriteTime | 2026/8/8 20:21 | 2026/8/8 20:21 | unchanged |
| `data/soul/agent_yua/diary/2026-08-08.jsonl` | 197 bytes | 197 bytes | unchanged |
| Total diary files | 197 | 197 | unchanged |

---

## 4. Source Code Changes (Minimal)

3 files / +52 lines / -3 lines:

```
 src/world/middleware.py | 19 +++++++++++++++++++    (新增 inject method)
 src/world/perception.py | 24 +++++++++++++++++++++---  (to_payload + from_payload 加 priority)
 src/world/validation.py | 12 +++++++++++              (validate_world_event 傳 priority)
 3 files changed, 52 insertions(+), 3 deletions(-)
```

無修改檔案:
- `src/memory/` (Memory Loader / Mirror / Diary / Dream) — 0 commits
- `src/agency/` (4-stage Agency) — 0 commits
- `src/eventbus/` (除已存在的 bus) — 0 commits
- `src/soul/scheduler.py` — 0 commits
- `src/temporal/` — 0 commits
- `src/llm/proxy.py` — 0 commits

---

## 5. Test Results

### M5.4-3 Boundary Audit (updated 5 tests + 5 new = 51 total)

| Test | Change | Status |
|------|--------|--------|
| H5: priority preserved through bus-payload | **UPDATED** (was: known finding → now: high wins) | ✅ PASSED |
| H6: priority preserved when direct compute_scores | unchanged | ✅ PASSED |
| H7: M1 to_payload includes priority | **NEW** | ✅ PASSED |
| H8: M1 from_payload backward compat (old/new/bad/bool) | **NEW** | ✅ PASSED |
| H9: M1 validate_world_event passes priority | **NEW** | ✅ PASSED |
| H10: M2 middleware.inject conforms Protocol | **NEW** | ✅ PASSED |
| H11: M2 inject idempotency vs process_world_event_direct | **NEW** | ✅ PASSED |
| Other 44 tests | unchanged | ✅ PASSED |

**M5.4-3 total**: **51/51 PASSED** (was 46, +5 new H7-H11)

### Full Regression (M5.3 + M5.4-1 + M5.4-2 + M5.4-3)

| Test File | Count | Result |
|-----------|-------|--------|
| test_m5_3_s1_4_v1_closed_loop | 3 | 3/3 ✅ |
| test_m5_3_s2_b_normalization | 22 | 22/22 ✅ |
| test_m5_3_s2_c_real_world_validation | 1 | 1/1 ✅ |
| test_m5_3_s2_d_world_awareness | 74 | 74/74 ✅ |
| test_m5_3_s2_e_e2e_world_perception | 55 | 55/55 ✅ |
| test_m5_3_s2_retrieval_diagnostic | 5 | 3/3 + 2 deselected (cp950 env, pre-existing) |
| test_m5_4_1_inner_life_narrative_audit | 50 | 48/48 + 2 skipped (POSIX perms Windows) |
| test_m5_4_2_memory_v1_mirror_failure_audit | 40 | 40/40 ✅ |
| test_m5_4_3_real_world_source_boundary_audit | 51 | 51/51 ✅ |
| **TOTAL** | **301** | **297 passed + 2 skipped + 2 deselected (env issue)** |

**0 regression** — 所有 frozen M5.3 測試 100% 維持。

---

## 6. STOP Conditions Check (派工明確指定)

| STOP Condition | Status |
|----------------|--------|
| 修 M1 必須破壞 frozen M3 payload contract | ✅ NOT TRIGGERED — additive 加欄位,frozen 6 欄位 100% 保留 |
| 需要 migration | ✅ NOT TRIGGERED — `payload.get("priority", 0)` 自動 fallback |
| 修改 frozen tests | ✅ NOT TRIGGERED — 既有 M5.3 tests 0 修改全 pass |
| 冒出第三個非必要 defect | ✅ NOT TRIGGERED — fix scope 嚴格限定 M1+M2,沒有意外發現 |

派工 4 條 STOP 條件 0 觸發,正常 close。

---

## 7. Commit & Push

- **Commit**: `daf0f78 feat(m5.4-3.1): world event bus contract alignment`
- **6 files changed, 1823 insertions(+), 3 deletions(-)**
- **Files committed**:
  - `src/world/middleware.py` (modified)
  - `src/world/perception.py` (modified)
  - `src/world/validation.py` (modified)
  - `tests/test_m5_4_3_real_world_source_boundary_audit.py` (new, includes H7-H11 contract repair tests)
  - `logs/m5_4_2_memory_v1_mirror_failure_audit_summary.md` (new)
  - `logs/m5_4_3_real_world_source_boundary_audit_summary.md` (new)
- **Push**: `02ab486..daf0f78 main -> main` ✅
- **HEAD == origin/main == daf0f78**

---

## 8. Pipeline 狀態 (post M5.4-3.1)

- M5.3 CLOSED + PUSHED (`02ab486`)
- M5.4-0 Architecture Audit ✅
- M5.4-1 Inner Life Narrative Boundary Audit ✅
- M5.4-2 Memory DB ↔ v1 Mirror Failure Boundary Audit ✅
- M5.4-3 Real WorldEventSource Boundary Audit ✅
- **M5.4-3.1 World Event Bus Contract Alignment ✅ CLOSED + PUSHED (`daf0f78`)**(this工單)
- M5.4-4+ waiting for Bry 派工

**Information World 邊界 audit + contract repair cycle 完整 closed**。

---

## 9. 觀察 & 下一步建議

派工結束後的真實狀態:
1. **M3.2-A priority_boost 從 dead code 變成 alive** — high priority events 在 E2E 真的會贏
2. **Dispatcher-Middleware chain 對齊** — `dispatcher.attach_injector(middleware)` 現在可以 work
3. **M5.4-0 推薦的 P0 工單仍有 2 個待派**:
   - **M5.4-4 Inner Life Unification** (Memory/Diary/Dream 共享 schema)
   - **M5.4-5 SpeakerToken 整合** (雙 impl 合併,SpeakerTokenBus + SpeakerTokenManager)
4. **M5.4-3 audit 還有 6 LOW-severity observations** — no fix needed, 持續 monitoring

---

**MEMORY 記錄時間**: 2026-08-09 17:55 EDT  
**Commit**: `daf0f78`  
**Pushed**: ✅ to `origin/main`  
**Author**: Lin (Mavis / MiniMax Code)
