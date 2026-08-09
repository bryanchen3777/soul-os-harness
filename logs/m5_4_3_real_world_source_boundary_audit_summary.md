# M5.4-3 — Real WorldEventSource Boundary Audit Summary

**派工**: 2026-08-09 (Sunday) by Bry  
**狀態**: ✅ **CLOSED — ACCEPTED (with 2 architecture findings worth surfacing)**  
**派工精神**: STRICT READ-ONLY / 0 source modification / 0 commit / 0 push

---

## 1. Test Results

| Section | Coverage | Count | Result |
|---------|----------|-------|--------|
| **A. Source Lifecycle Failure** | start/stop fail isolation, idempotency, dup register | 5 | 5/5 ✅ |
| **B. Source emit_event Failure** | no injector, failing injector, priority type | 4 | 4/4 ✅ |
| **C. Injector / Routing Failure** | dispatcher missing source/injector, fail propagate, detach | 5 | 5/5 ✅ |
| **D. Validation (malformed payload)** | missing field, bad source, bad ts, bad novelty_id, normalize | 6 | 6/6 ✅ |
| **E. Middleware State Machine** | invalid/valid event, empty/budget/reject | 5 | 5/5 ✅ |
| **F. Stale Data** | old ts but perceived now, expired perceived_at, max bounded | 3 | 3/3 ✅ |
| **G. Duplicate Event** | novelty_count 累積, novelty score 衰減, expiry 遞減 | 3 | 3/3 ✅ |
| **H. Priority / Low-Priority** | priority anchor points, clamping, bus-payload lost, direct call works | 6 | 6/6 ✅ |
| **I. Complete Chain E2E** | source→bus→perceived, empty state, top-N, rejected excluded | 4 | 4/4 ✅ |
| **J. M5.3 Frozen Contract** | scoring weights, additive priority, frozen events | 3 | 3/3 ✅ |
| **Z. Production Safety** | production data 0 mutation | 1 | 1/1 ✅ |
| **test count** | 自我 count 驗證 ≥30 | 1 | 1/1 ✅ |
| **TOTAL** | | **46** | **46/46 ✅** |

**執行**: `& .venv\Scripts\python.exe -m pytest -v tests/test_m5_4_3_real_world_source_boundary_audit.py`  
**結果**: `46 passed, 1 warning in 0.98s` (warning = pydantic config deprecation, unrelated)

---

## 2. M5.3 / M5.4-1 / M5.4-2 Regression Maintenance

| Test File | Count | Result |
|-----------|-------|--------|
| test_m5_3_s1_4_v1_closed_loop | 3 | 3/3 ✅ |
| test_m5_3_s2_b_normalization | 22 | 22/22 ✅ |
| test_m5_3_s2_c_real_world_validation | 1 | 1/1 ✅ |
| test_m5_3_s2_d_world_awareness | 74 | 74/74 ✅ |
| test_m5_3_s2_e_e2e_world_perception | 55 | 55/55 ✅ |
| test_m5_3_s2_retrieval_diagnostic | 5 | 3/3 + 2 deselected (cp950 encoding, pre-existing) |
| test_m5_4_1_inner_life_narrative_audit | 50 | 48/48 + 2 skipped (POSIX perms Windows) |
| test_m5_4_2_memory_v1_mirror_failure_audit | 40 | 40/40 ✅ |
| **TOTAL** | **250** | **246 passed + 2 skipped + 2 deselected (env issue)** |

**2 deselected 說明**: 同 M5.4-2 — PowerShell cp950 編碼無法 print 中文字元 (\u7f57=羅),pre-existing,非 M5.4-3 造成。

---

## 3. Production Data 0 Mutation Verification

| Resource | Value | Status |
|----------|-------|--------|
| `git rev-parse HEAD` | `02ab4864b3f3b1c08b1a2e6256f5f88553050357` | unchanged |
| Working tree modified | 0 files | clean |
| Untracked files | 18 (17 prior + `tests/test_m5_4_3_real_world_source_boundary_audit.py`) | 1 new |
| S0 backup MD5 | `66D920058007FF1252E4FD23C288F2E9` | unchanged |
| `data/memory.db` size | 5,115,904 bytes | unchanged |
| `data/shadow.log` size | 1,846,306 bytes | unchanged |

**Source code modified**: 0 files (M5.4-3 STRICT READ-ONLY maintained)  
**Tests committed**: 0 (test file left untracked per M5.3-S1-4 / S-2-A / M5.4-2 convention)

---

## 4. Key Findings (派 Bry 決策)

### 4.1 Architecture Observations (6 LOW severity, NO production fix needed)

| # | Observation | Evidence | Severity |
|---|------------|----------|----------|
| O1 | WorldPerceptionState uses `perceived_at` (not `event.ts`) for novelty_window — old event.ts won't expire the state entry, only when state receives it does | F1, F2 tests | LOW (by design per perception.py comment) |
| O2 | state.max_active_events uses FIFO eviction via deque(maxlen=N) — silently drops oldest when full | F3 test | LOW (by design) |
| O3 | novelty_index count auto-decrements on expiry to avoid double counting | G3 test | LOW (correct) |
| O4 | Duplicate event (same novelty_id) → novelty score decays (1.0 → 0.5 → 0.33) | G2 test | LOW (correct) |
| O5 | priority mapping: 0→0.0, 5→0.4, 10→0.8, ≥12.5→1.0 (linear, clamped) | H1-H4 tests | LOW (per派工 #9 anchor) |
| O6 | Registry register duplicate source_id → ValueError (fail-fast) | A5 test | LOW (by派工 spec) |

### 4.2 MEDIUM Severity Findings (2 worth surfacing)

#### Finding M1: **WorldEvent.priority 經 bus-payload 路徑會丟失**

**Symptom (H5 test 觀察)**:
- Source emit `WorldEvent(priority=20, ...)` 經 `process_world_event_direct` → 
- `to_payload()` **故意不包含 priority 欄位** (perception.py L88 comment: "既有 M3 bus payload format 100% 保留, 不含 priority 欄位") → 
- `validate_world_event(payload)` → `WorldEvent.from_payload()` 重建 WorldEvent → priority 預設 0
- → M3.2-A priority_boost 在 end-to-end bus-payload 路徑下**實際上沒生效**

**Verified by H6 test (對照組)**:
- 直接用 `compute_scores(ev, novelty_count=1, event_priority=20)` → priority_boost=1.0
- → 證明 priority scoring 邏輯本身**正確**,只是被 bus-payload serialization 切斷

**Why this matters (M5.4-3 派工對齊)**:
- 派工原文: "M3.1 Phase B: 既有 M3 bus payload format 100% 保留, 不含 priority 欄位"
- 派工原文: "M3.2-A: priority 進場, 既有 5 維度 scoring 邏輯 0 改, additive priority_boost 進 final_score"
- 兩條 contract 在實作上**互相矛盾**:
  - M3.1 Phase B 保證 bus-payload 向後相容 → 砍掉 priority
  - M3.2-A 引入 priority_boost → 假設 priority 在 scoring 階段可見
  - 結果: M3.2-A scoring 永遠拿到 priority=0,additive contribution = 0

**Severity**: **MEDIUM (architecture contract contradiction, by design but worth surfacing)**

**Workaround (現有 path)**:
- Middleware `_on_world_event` 拿到 WorldEvent → 直接讀 `world_event.priority` 傳 `compute_scores(event_priority=world_event.priority)`
- BUT: `world_event` 是 `from_payload` 重建的,priority 已經 = 0
- 所以 workaround 也沒用

**Real fix options (派 Bry 決策)**:
- **Option A**: 把 priority 加進 `to_payload()` (違反 M3.1 Phase B 向後相容 contract)
- **Option B**: WorldEvent 不走 bus-payload,改走 reference (architecture 大改,需重做序列化)
- **Option C**: 接受 M3.2-A priority_boost 是 dead code,從 scoring 移除 priority_boost 維度
- **Option D**: Source 端 emit 進 bus 前,encode priority 到 novelty_id (hack)

**M5.4-3 派工精神**: 發現 defect → STOP,只回報。**不修**。

---

#### Finding M2: **WorldPerceptionMiddleware 沒有 `inject()` method,不能直接 conform WorldEventInjector Protocol**

**Symptom (I1 test 觀察)**:
- `WorldEventInjector` Protocol 定義 `async def inject(self, event: WorldEvent) -> None`
- `WorldPerceptionMiddleware` 沒有 `inject` method
- → 嘗試 `dispatcher.attach_injector(mw)` 然後 `dispatcher.emit_and_inject(...)` 會 raise `AttributeError`
- → 真正的 source → middleware chain 必須走 `bus.publish(WORLD_EVENT)` → middleware._on_world_event() 路徑

**Why this matters**:
- M3.1 Phase A 派工承諾 "Phase A 主要 implementation: WorldPerceptionMiddleware (process_world_event_direct)"
- 但 `process_world_event_direct` 不是 `inject`,不能被 Dispatcher 用
- 結果: Dispatcher.emit_and_inject 雖然存在,但沒有對應的 source chain 可以走
- 真實的 source → bus → middleware 鏈是繞過 Dispatcher 的

**Severity**: **MEDIUM (Phase A 跟 Phase C 兩個 routings 沒有對齊,真實 chain 走 Phase B-style bus path)**

**Workaround (現有)**:
- 不用 Dispatcher,直接 `bus.publish(SoulEvent(event_type=WORLD_EVENT, payload=...))`
- Middleware 訂閱 WORLD_EVENT,自動處理

**Real fix options (派 Bry 決策)**:
- **Option A**: WorldPerceptionMiddleware 加上 `async def inject(self, event)` 委派給 `process_world_event_direct`
- **Option B**: Dispatcher 接受 event 直接 publish 進 bus,不走 injector
- **Option C**: 接受現狀,Source → Bus → Middleware 是唯一真實路徑,Dispatcher 只用於 synthetic / test

**M5.4-3 派工精神**: 發現 defect → STOP,只回報。**不修**。

### 4.3 NO HIGH-severity defects found

- 所有 46 tests 通過
- 沒有 crash,沒有 data corruption
- chain (source → bus → perception → final prompt) E2E 驗證可運作
- M5.3 frozen contract (scoring weights, additive priority, frozen event types) 完全不變

---

## 5. Boundary Contract Confirmed

完整 chain 經審計後的 contracts:

```
Source Adapter (WorldEventSource ABC)
        ↓ emit WorldEvent
to_payload() (不含 priority 欄位 — M3.1 Phase B 向後相容)
        ↓ 包成 SoulEvent(payload=...)
Event Bus (SoulEventBus)
        ↓ publish(WORLD_EVENT)
WorldPerceptionMiddleware._on_world_event
        ├── validate_world_event (薄 input validation)
        │     ├── 必填欄位 (source/type/novelty_id/ts/summary)
        │     ├── source whitelist (weather/news/calendar/social/synthetic)
        │     ├── ts ISO 8601 + UTC
        │     ├── novelty_id [a-z0-9_]{4,128}
        │     └── novelty_id normalize lowercase
        ├── state.add (WorldPerceptionState, ephemeral, bounded 200)
        └── trace.write (jsonl sidecar, write failure 不 raise)
                ↓
[當 AGENT_INTENT_ENRICHED 抵達]
        ├── compute_scores (5 維度 + priority_boost)
        │     ⚠️ priority 在 bus-payload 路徑下永遠 = 0 (M1 finding)
        ├── should_accept (threshold gate, default 0.35)
        ├── top-N (perception_budget, default 3)
        └── WorldContext.to_text() (rendering)
                ↓
AGENT_INTENT_PERCEIVED (SoulEvent)
        ↓
LLMProxy._build_messages_group (world_context 注入 final prompt)
        ↓
Final Prompt
```

**Key contracts (verified by tests)**:
- ✅ Source lifecycle failure isolation (A1-A5)
- ✅ Source emit failure → propagate, no silent swallow (B1-B4)
- ✅ Dispatcher routing fail-fast (C1-C5)
- ✅ Validation rejects malformed payloads (D1-D6)
- ✅ Middleware state machine: validate → state → trace → context (E1-E5)
- ✅ Stale data handled via perceived_at + max_active_events (F1-F3)
- ✅ Duplicate event via novelty_index + decay score (G1-G3)
- ⚠️ Priority scoring logic works (H6) but lost in bus-payload (H5, M1 finding)
- ✅ Complete chain E2E: source → bus → perceived → final prompt (I1-I4)
- ✅ M5.3 frozen contract preserved (J1-J3)
- ✅ Production data 0 mutation (Z1)

---

## 6. Recommendations (派 Bry 決策)

Per M5.4-3 派工精神 "發現 defect → STOP,只回報":

1. **NO production code change recommended** — 所有 46 tests 通過,真實 chain 可運作
2. **Document M1 + M2 in `AGENTS.md`** — 兩個 MEDIUM finding 是真實 architecture contract contradictions,值得在 `AGENTS.md` 留下備註給未來 maintainer
3. **Future工單 candidates** (not派, just noting):
   - **M5.4-3.1 priority_boost dead code fix**: 選 A/B/C/D 其中一個,讓 M3.2-A priority 真的影響 scoring
   - **M5.4-3.2 Dispatcher-Injector alignment**: 選 A/B/C 其中一個,讓 WorldEventInjector Protocol 跟 WorldPerceptionMiddleware 對齊
   - **M5.4-4 Inner Life Unification** (M5.4-0 sub-ticket P0)
   - **M5.4-5 SpeakerToken 整合** (M5.4-0 sub-ticket P1)
   - **M5.4-6 Agency 4-World Context** (M5.4-0 sub-ticket P1)

---

## 7. Audit 派工結論

**M5.4-3 Real WorldEventSource Boundary Audit: ACCEPTED**

- 46/46 tests PASS
- 6 LOW + 2 MEDIUM observations documented
- 0 HIGH severity defects
- 0 source code modification
- 0 production data mutation
- M5.3 + M5.4-1 + M5.4-2 regression maintained (246 pass + 2 skip + 2 env-deselect)

**Pipeline 狀態**:
- M5.3 CLOSED + PUSHED (`02ab486`)
- M5.4-0 Architecture Audit ✅ CLOSED
- M5.4-1 Inner Life Narrative Boundary Audit ✅ CLOSED  
- M5.4-2 Memory DB ↔ v1 Mirror Failure Boundary Audit ✅ CLOSED
- **M5.4-3 Real WorldEventSource Boundary Audit ✅ CLOSED — ACCEPTED (2 MEDIUM findings)**(this工單)
- M5.4-4+ waiting for Bry 派工

**下一張**: 等 Bry 派工 (建議走 M5.4-3.1 priority fix 或 M5.4-4 Inner Life Unification)

---

**MEMORY 記錄時間**: 2026-08-09 17:55 EDT  
**Test file**: `tests/test_m5_4_3_real_world_source_boundary_audit.py` (untracked, ~50KB)  
**Summary log**: `logs/m5_4_3_real_world_source_boundary_audit_summary.md` (this file)  
**Author**: Lin (Mavis / MiniMax Code)
