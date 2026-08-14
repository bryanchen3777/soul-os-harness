# M6.1-8 — Agency Re-enable Investigation (READ-ONLY)

**Bry 拍板**: 2026-08-14 18:25 EDT
**Audit 時間**: 2026-08-14 18:25–22:38 EDT
**Mode**: READ-ONLY / ARCHITECTURE INVESTIGATION
**性質**: 不重新啟用 Agency / 不修改 source / 不修改 config / 不修改 production data
**Previous ticket**: M6.1-7 Production Lived Context Evidence Reassessment (bdf76ad)

---

## Executive Conclusion

**ROOT CAUSE IDENTIFIED**: SoulScheduler 的 `agents=0` 是 **回歸 (regression)**, 不是刻意關閉。

M5.2 migration (commit `481ea41`, 2026-08-08 21:11 EDT, "refactor(m5.2): migrate scheduler triggers to agency event bridge") 在 `run_server.py` 移除了 `scheduler.register(aid, cb)` callsite (M5.2-I Phase 7), 但 M5.2-I Phase 8 在 `scheduler.py` 內把 `_fire_all` 的 iteration source 從 `_callbacks` 改到 `_all_agents`, **沒有**新增任何 `scheduler.register(aid)` 補回去。產線 `SoulScheduler._all_agents` 從那次重啟後永遠是 `[]` (default empty list)。

5 條排程觸發路徑全部在 `if not self._all_agents: return` (scheduler.py:560/620/921) 或 `_get_proactive_agents() returns []` 早退, 從不發 AGENCY_TRIGGER, 從不呼叫 4 個 handler。

**6+ 天沒寫 diary / dream / event / proactive_dm (8/8 21:13 → 8/14 22:38)**。

這個 bug **被 M5.2-I Phase I-9 sweep (logs/m5_2_i_i9_callback_dependency_sweep.md, 2026-08-08 17:50 EDT) 明確紀錄**:
> "`register(agent_id, callback)` (diary) | **完全沒** (I-7 移除 `scheduler.register` 改用 `diary_callbacks_real` dict lookup)"
> "API COMPAT (介面保留, **production 完全沒註冊**)"

I-9 標記 "API COMPAT" PASS, 沒追 "production 完全沒註冊" 對實際行為的影響, 然後 commit 481ea41 21:11 上 main。**這是已知 regression, 已知被 ship, 沒人修**。

**Agency 不是「被關閉」, 是「被卡死」**。4 個 handler (AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler) 都正確 wire 在 bus 上, 都正確過濾 trigger_type, 都在等 AGENCY_TRIGGER 進來, **只是上游從來沒 publish 過**。

**修法**: 在 `run_server.py` line 776 之後加 3 行:

```python
# M6.1-8.1 (Bry 拍板): 補回 M5.2-I Phase 7 漏掉的 agent 註冊
for aid in agent_ids:
    scheduler.register(aid)
```

**範圍**: 1 檔案, +3/-0, 0 frozen contract, 0 production data, 0 M3/M5.4-5.x 變更。**但 Bry 必須先拍板** (M6.1-8 是 READ-ONLY, work order 明令禁止直接重新啟用)。

---

## 1. Baseline Verification

| 項目 | 狀態 |
|------|------|
| HEAD | `bdf76ad0787d7254efa5c11ab95005acfa98ec84` (M6.1-7 closeout) |
| origin/main | `bdf76ad0787d7254efa5c11ab95005acfa98ec84` |
| HEAD == origin/main | ✅ Synced |
| Working tree | 0 modified, 20 untracked artifacts preserved |
| Server | PIDs 8568 + 20800, /health=200, 8.5h uptime (started 8/14 09:48:08) |
| Production data | byte-for-byte unchanged (READ-ONLY audit) |
| Calendar source | active, polling 300s |
| Weather source | active, polling 1800s |
| News source | active, polling 1800s |

---

## 2. Configuration Source

### 2.1 Scheduler internal state

**File**: `src/soul/scheduler.py`

| Line | Code | Note |
|------|------|------|
| 161 | `self._all_agents: List[str] = []` | Default empty list |
| 355-366 | `def register(self, agent_id: str) -> None: if agent_id not in self._all_agents: self._all_agents.append(agent_id)` | **唯一** populate 方式 |
| 473 | `f"agents={len(self._all_agents)}"` | start() log line — emits "agents=0" |
| 560 | `if not self._all_agents: return` | `_fire_dream` early return |
| 620 | `if not self._all_agents: return` | `_fire_event` early return |
| 696-697 | `if self._proactive_agents_whitelist is None: return list(self._all_agents)` | `_get_proactive_agents` returns empty if `_all_agents` empty |
| 921 | `if not self._all_agents: return` | `_fire_all` early return (morning + night) |

### 2.2 run_server.py scheduler wiring

**File**: `scripts/run_server.py`

| Line | Code | Note |
|------|------|------|
| 760-763 | `scheduler = get_scheduler(bus=bus, proactive_agents=["agent_ruka"])` | Scheduler created with empty `_all_agents` |
| 773-776 | `diary_callbacks_real: Dict[str, Any] = {}; for aid in agent_ids: cb_real = await diary_callback_factory(aid); diary_callbacks_real[aid] = cb_real` | `diary_callbacks_real` dict populated, **but `_all_agents` not populated** |
| 1133 | `await scheduler.start()` | `agents={len(self._all_agents)}` = `agents=0` in start log |

### 2.3 Grep evidence

```
$ grep -n "scheduler\.register" scripts/run_server.py
784: # scheduler.register_dream_event() API 仍保留為 compat surface (見 scheduler.py)。
891: # scheduler.register_heartbeat(_heartbeat_callback)
892: # M5.2-O-3 (Bry 拍板 2026-08-08): 移除 scheduler.register_proactive_dm(_proactive_dm_callback)
```

**0 actual callsite** in `run_server.py`. Only 3 commented-out references. **CONFIRMED**: `_all_agents` is permanently empty.

### 2.4 Environment / Config

| Source | Value | Note |
|--------|-------|------|
| `DISABLE_PROACTIVE` in `.env` | (not set) | Default = "false" → `await scheduler.start()` IS called |
| `DISABLE_PROACTIVE` in `.env.example` | (not documented) | No env switch for proactive disable documented |
| `proactive_agents` in `get_scheduler()` | `["agent_ruka"]` | whitelist for proactive_dm + event (per 修法 11) |
| `proactive_dm_min/max_interval_minutes` | 180/300 (3-5h) | Per 修法 12 (Bry 8/6 17:12) |
| `morning_time` / `night_time` | 08:00 / 22:00 | Per Bry 拍板 2026-07-18 18:24+ |
| `dream_minutes_after_night` | 5 | 22:05 dream |
| `event_min/max_interval_minutes` | 240/480 (4-8h) | Per Mavis 7/21 16:35 拍板 |

**No env switch is causing `agents=0`**. The scheduler is STARTED, but `_all_agents` is empty.

---

## 3. Determine Intent — REGRESSION, NOT INTENTIONAL

### 3.1 The original `register()` callsite

**Pre-M5.2 (before commit 481ea41, 2026-08-08)**:
```python
# run_server.py (parent of 481ea41)
for aid in agent_ids:
    cb_real = await diary_callback_factory(aid)
    diary_callbacks_real[aid] = cb_real
    scheduler.register(aid, cb)  # ← EXISTED before M5.2
```

**Post-M5.2 (commit 481ea41, 2026-08-08 21:11)**:
```python
# run_server.py (481ea41)
for aid in agent_ids:
    cb_real = await diary_callback_factory(aid)
    diary_callbacks_real[aid] = cb_real
# scheduler.register() REMOVED — _all_agents permanently empty
```

**Git evidence**:
```
$ git show 481ea41 -- scripts/run_server.py | grep register
- scheduler.register(aid, cb)  # ← removed
+ # I-6 Scheduler 已 AGENCY_TRIGGER-only (4 條 fire_* path 不再 invoke callback).
```

### 3.2 M5.2-I Phase I-9 sweep (2026-08-08 17:50 EDT, BEFORE commit)

**File**: `logs/m5_2_i_i9_callback_dependency_sweep.md`

Section 2.2 explicitly documents the regression:
> | API | scheduler.py 內部 | run_server.py | tests | 影響 |
> | `register(agent_id, callback)` (diary) | L267-279 (寫入 `_callbacks[morning/night]` + `_all_agents.append`) | **完全沒** (I-7 移除 `scheduler.register` 改用 `diary_callbacks_real` dict lookup) | `test_m5_2_h3_diary_bridge.py:93` (隔離 helper) | API COMPAT (介面保留, **production 完全沒註冊**) |

**The I-9 sweep marked the regression as "API COMPAT" PASS** without testing the actual scheduler → handler pipeline in production. The regression was visible in the sweep but not fixed.

Section 2.3:
> | L820-832 `_fire_all` (I-8 後) | 用 `_all_agents` 迭代, **但**完全不再觸發 `_callbacks`** | PRODUCTION (M5.2-I8) |

I-8 changed iteration source to `_all_agents`, but the I-7 + I-8 dual change broke the contract:
- I-7: removed `register(aid, cb)` callsite
- I-8: changed iteration source to `_all_agents`
- **Gap**: no replacement `register(aid)` to populate `_all_agents`

### 3.3 M5.2 series tests did NOT catch this

| Test file | Tests | Coverage | Why it didn't catch |
|-----------|-------|----------|---------------------|
| `test_m5_2_g_proactive_dm_bridge.py` | 11/11 | `AgencyTriggerHandler` with explicit `SoulEvent` | Test injects events directly, bypasses scheduler |
| `test_m5_2_h_event_bridge.py` | 11/11 | `EventHandler` with explicit `SoulEvent` | Same |
| `test_m5_2_h2_dream_bridge.py` | 14/14 | `DreamHandler` with explicit `SoulEvent` | Same |
| `test_m5_2_h3_diary_bridge.py` | 13/13 | `DiaryHandler` with explicit `SoulEvent` | Same |
| `test_m5_2_minimal_agency.py` | 22/22 | Agency 4 stages with explicit trigger | Same |

**All 71 tests** test handler ingress with explicit events, never the **scheduler → AGENCY_TRIGGER → handler** end-to-end path. The `if not self._all_agents: return` early return is in scheduler.py, never reached by the bridge tests.

### 3.4 Verdict: UNINTENTIONAL REGRESSION

- Not an Owner decision to disable
- Not a test-only configuration
- Not a temporary config
- Not an undocumented architecture decision
- **REGRESSION**: missed step during I-7 → I-8 refactor, documented as "API COMPAT" but never tested against the actual production behavior chain

---

## 4. Historical Evidence (Timeline)

| 時間 | 事件 | Source |
|------|------|--------|
| 8/5 21:08 | Bry 被連環訊息轟炸, 拍板 DISABLE_PROACTIVE 緊急開關 | run_server.py:1115-1118 |
| 8/6 17:12 | Bry 拍板 修法 12: heartbeat 暫停, proactive_dm 3-5h | scheduler.py:108-114 |
| 8/6 16:xx | 修法 11: proactive_agents whitelist 收斂到 ["agent_ruka"] | run_server.py:752-754 |
| **8/8 17:50** | M5.2-I I-9 sweep 完成, 紀錄 "production 完全沒註冊" | `logs/m5_2_i_i9_callback_dependency_sweep.md` §2.2 |
| **8/8 20:18** | Plan A launcher 重啟 server (PID 20292), 這是 M5.2 之前的最後一次乾淨重啟 | `data/logs/plan_a_launcher.log` |
| **8/8 21:11** | Commit `481ea41`: "refactor(m5.2): migrate scheduler triggers to agency event bridge" | git log |
| 8/8 21:13 | Plan A launcher 重啟 server (PID 20292 → 20292 from log) | `data/logs/plan_a_launcher.log` |
| **8/8 08:00** | Last mass diary generation: 10 agents, all 10 files in `data/soul/*/diary/2026-08-08.jsonl` | filesystem |
| 8/9 09:11 | Last `relationships.json` write (yua) | filesystem |
| 8/10 01:05 | Last yua diary (anomaly, manual or 1-test-only) | filesystem |
| 8/12 22:49 | Last `inner_life/trace.jsonl` write | filesystem |
| 8/13 09:11 | Last aoi/mai/miku/anna/mahiru/ram/rem/akane carryover.json writes (8/13 09:11:12 同分鐘 burst) | filesystem |
| 8/14 09:48 | Plan A launcher started current PIDs 8568 + 20800 | `data/logs/plan_a_launcher.log` |
| 8/14 18:25 | M6.1-7 audit: 0 diary/dream/event emission since 8/8 08:00 (6.5 days) | M6.1-7 closeout (bdf76ad) |
| **8/14 22:38** | M6.1-8 audit: confirmed `_all_agents` is empty, no fix proposed | This document |

**Key observation**: The **last 6+ days** show NO new diary files, NO new dream files, NO new event files, NO proactive_dm (except via Bry's TG conversation → handler, not via scheduler).

The only AGENCY activity is:
- Bry → TG → conversation → handler → agent reply (manual conversation)
- 10 agents registered in `app.state._agents` for `app.state._scheduler` to iterate
- 4 handlers wired to bus, all correctly filtering trigger_type
- **But scheduler never publishes AGENCY_TRIGGER**

---

## 5. Agency Path Audit

### 5.1 6 Scheduler trigger paths

| Path | Method | Status | Reason |
|------|--------|--------|--------|
| **morning** (08:00 daily) | `_fire_all(slot="morning")` | ❌ **BROKEN** | `if not self._all_agents: return` (scheduler.py:921) |
| **night** (22:00 daily) | `_fire_all(slot="night")` | ❌ **BROKEN** | `if not self._all_agents: return` (scheduler.py:921) |
| **dream** (22:05 daily) | `_fire_dream(today)` | ❌ **BROKEN** | `if not self._all_agents: return` (scheduler.py:560) |
| **event** (4-8h random) | `_fire_event()` | ❌ **BROKEN** | `if not self._all_agents: return` (scheduler.py:620) |
| **proactive_dm** (3-5h random) | `_fire_proactive_dm()` | ❌ **BROKEN** | `_get_proactive_agents()` returns `list(self._all_agents)` if whitelist None, or `whitelist ∩ _all_agents` (empty) if whitelist set |
| **heartbeat** (30-60m random) | `_fire_heartbeat()` | ⚪ **DISABLED** | Per 修法 12 (Bry 8/6 17:12). `register_heartbeat` commented out in run_server.py:890-891. **INTENTIONAL**, separate from agents=0 |

**5 of 6 paths broken due to `_all_agents` empty**. Heartbeat is disabled by Owner decision (修法 12), independent regression.

### 5.2 4 Handler ingress (downstream, all correctly wired, all starving)

| Handler | File | trigger_type filter | Wired at | Status |
|---------|------|---------------------|----------|--------|
| AgencyTriggerHandler | `src/agency/trigger_handler.py` | `"proactive_dm"` (L83) | `run_server.py:898-908` | ✅ Wired, **starved** |
| EventHandler | `src/agency/event_handler.py` | `"event"` (L108) | `run_server.py:952-960` | ✅ Wired, **starved** |
| DreamHandler | `src/agency/dream_handler.py` | `"dream"` (L120) | `run_server.py:1021-1029` | ✅ Wired, **starved** |
| DiaryHandler | `src/agency/diary_handler.py` | `{"morning", "night"}` (L139) | `run_server.py:1100-1108` | ✅ Wired, **starved** |

All 4 handlers:
- Subscribe to `event_filter={EventType.AGENCY_TRIGGER}` (run_server.py:907/958/1027/1106)
- Correctly filter by `trigger_type` in their `handle_event()` method
- Have their `writer_executor` / `llm_executor` properly wired
- Are **operational but starved** — no AGENCY_TRIGGER is ever published because `_all_agents` is empty

### 5.3 Handler side effects (if/when AGENCY_TRIGGER flows)

| Handler | Side effects | Source |
|---------|--------------|--------|
| AgencyTriggerHandler | `_proactive_dm_llm_executor` → `_agent._fire_intent()` → LLM call → AGENT_INTENT → LLMProxy → AGENT_SPEAK → TG DM | run_server.py:824-888 |
| EventHandler | `_event_writer_executor` → `inner_life_writer.create_event()` → `writer.write_event()` → `data/events/{date}.jsonl` | run_server.py:917-950 |
| DreamHandler | `_dream_writer_executor` → `inner_life_writer.create_event()` → `writer.write_dream()` → `data/soul/{aid}/diary/{date}.jsonl` + `relationships.json` `on_dream` touch + `_extract_impression` update | run_server.py:969-1019 |
| DiaryHandler | `_diary_writer_executor` → `inner_life_writer.create_event()` → `diary_callbacks_real[aid](aid, slot, inner_life_event_id)` → `generate_diary_entry()` → LLM call → `data/soul/{aid}/diary/{date}.jsonl` + relationships.json update | run_server.py:1041-1098 |

**All 4 handlers are production-ready, frozen, and have 71/71 tests passing**. The bug is purely in the scheduler upstream.

---

## 6. Production Safety Analysis (Re-enable Impact)

If we add `for aid in agent_ids: scheduler.register(aid)` to `run_server.py`:

### 6.1 Expected side effects (first 24h)

| Effect | Volume | Risk |
|--------|--------|------|
| Diary writes | 10 agents × 2 slots = 20 LLM calls/day (morning + night) | LLM rate limit (`LLM_CONCURRENCY_LIMIT`); memory.db write; diary jsonl append |
| Dream writes | 3-5 agents × 1 slot/night = 3-5/day at 22:05 | InnerLifeEvent; dream jsonl; relationship side effect |
| Event writes | 1 event per 4-8h = 2-6/day (random) | InnerLifeEvent; events jsonl |
| Proactive_dm (ruka only) | 1 per 3-5h = 5-8/day (per 修法 12) | LLM call; InnerLifeEvent; TG DM |
| Heartbeat | 0 (suspended per 修法 12) | None |
| **Total per day** | ~20 diary + 3-5 dream + 2-6 event + 5-8 proactive_dm | All gated by `LLM_CONCURRENCY_LIMIT` |

### 6.2 First-time burst at 08:00

- 10 agents × morning diary = 10 simultaneous LLM calls
- `LLM_CONCURRENCY_LIMIT` (run_server.py:793 import) mitigates via semaphore
- 10 InnerLifeEvents created in 1 minute (`inner_life/trace.jsonl` +10 lines)
- 10 diary jsonl files appended
- 10 relationships.json `_extract_impression` calls (memory side effect)

### 6.3 InnerLife gate

- Per M5.8-4, `proactive_dm` is gated by `gate_proactive_dm()` checking last InnerLifeEvent
- Fail-open: if trace unavailable, allow publish
- For ruka: first proactive_dm after re-enable: gated by last 30+ min InnerLifeEvent
- Other 4 trigger types: NOT gated (per M5.8-4 §scope)

### 6.4 FROZEN contracts (NOT modified by fix)

- M3 WorldEvent schema — frozen
- M3.1 WorldEvent.priority — frozen
- M3.1 WorldEventSource ABC — frozen
- M3.1 WorldEventInjector Protocol — frozen
- M3.1 Event Bus — frozen
- M5.4-5.1 InnerLifeEvent 9 fields — frozen
- M5.4-5.1 Provenance — frozen
- M5.4-5.1 lineage_depth/lineage_path — frozen
- M5.4-5.5 SoulEvent.inner_life_event_id — frozen
- M5.9-2 WORLD_QUALIFYING_TYPES — frozen
- M5.9-3 WorldInnerLifeAdapter filtering+dedup — frozen
- AGENCY_TRIGGER schema (M5.2-F) — frozen
- VALID_SOURCES — frozen

**Fix is purely server startup wiring**, 0 frozen contract touched.

---

## 7. Minimal Safe Re-enable Proposal (NOT EXECUTED)

### 7.1 The 3-line fix

**File**: `scripts/run_server.py`
**Location**: After line 776 (after `diary_callbacks_real[aid] = cb_real`), before line 1133 (`await scheduler.start()`)

```python
# M6.1-8.1 (Bry 拍板): 補回 M5.2-I Phase 7 漏掉的 agent 註冊
# 動機: M5.2 migration (commit 481ea41, 2026-08-08 21:11) 移除了
#       scheduler.register(aid, cb) callsite 但 M5.2-I Phase 8 改用
#       _all_agents 當 iteration source, 沒補回 register(aid).
#       結果: _all_agents 永久 empty, 5 條 trigger path 全部 silent skip.
# 範圍: 純 server startup wiring, 0 frozen contract, 0 production data
# 驗證: 修完後 scheduler.start() 會 log "agents=10" (取代 "agents=0")
for aid in agent_ids:
    scheduler.register(aid)
```

**Diff**:
```diff
         diary_callbacks_real: Dict[str, Any] = {}  # agent_id -> real callback (供 DiaryHandler.executor 用)
         for aid in agent_ids:
             cb_real = await diary_callback_factory(aid)
             diary_callbacks_real[aid] = cb_real

+        # M6.1-8.1 (Bry 拍板): 補回 M5.2-I Phase 7 漏掉的 agent 註冊
+        for aid in agent_ids:
+            scheduler.register(aid)
+
         # 4.2+缺口 1: dream + event 觸發時機 (Bry 2026-07-20 19:03 拍板)
```

**Size**: +4/-0 (3 executable lines + 1 comment)
**Files**: 1
**Contracts**: 0 changed
**Production data**: 0 changed

### 7.2 4 safety options for Bry to choose

| Option | Description | Pros | Cons | Recommended |
|--------|-------------|------|------|-------------|
| **A** | Direct re-enable, 24h monitor | Simplest, fastest recovery | First 08:00 burst: 10 simultaneous LLM calls | For Bry who wants 1-day backfill |
| **B** | Gradual: register 1 agent at a time, 1h apart | Limits burst to 1 LLM call/agent | Takes 10h to fully populate; weird intermediate state | For Bry who wants zero burst risk |
| **C** | Shadow mode: register all, gate scheduler output to log-only | Test full pipeline without writes | Adds new code path; needs rollback plan | For Bry who wants pre-flight |
| **D** | Isolated test first: spin up test instance, run 24h, then production | Cleanest validation | Most expensive; needs test infra | For Bry who wants 100% confidence |

**Bry 拍板 required** per M6.1-8 work order: "本 ticket 不允許直接重新啟用 Agency" / GOV-2 §2.8 Owner decision boundary.

### 7.3 Validation criteria (any option)

After re-enable + 24h RUN-AND-COLLECT:
- ✅ `scheduler.start()` log shows `agents=10` (not `agents=0`)
- ✅ 10 morning diary writes (08:00 local)
- ✅ 10 night diary writes (22:00 local)
- ✅ 3-5 dream writes (22:05 local)
- ✅ 2-6 event writes (4-8h random)
- ✅ 1-3 proactive_dm (3-5h, ruka only)
- ✅ 0 heartbeat (suspended)
- ✅ InnerLifeEvent count increases (trace.jsonl +30-50 lines/day)
- ✅ relationships.json updates
- ✅ No frozen contract violations
- ✅ /health=200, server stable

---

## 8. Whether Bry Decision is Required

**YES** — Bry decision is REQUIRED.

### 8.1 M6.1-8 work order says

> "本 ticket 不允許直接重新啟用 Agency"
> "目標是回答：為什麼 Agency handlers 現在是 disabled？以及：在什麼條件下可以安全重新啟用？"
> "如果 investigation 證明可以重新啟用：只提出最小可驗證方案。例如：test-only → shadow → isolated agent → controlled production。但不要實作。"

### 8.2 GOV-2 §2.8

> "Owner decision boundary: Bryan sole authority for frozen contract changes"
> "Agency re-enable = production activation = Owner decision"

### 8.3 First-time re-enable risk

- 10-agent diary burst at next 08:00
- 6+ days of zero Agency activity (memory/decay state may be stale)
- InnerLifeEvent burst (10+ in 1 minute)
- LLM load: 10 + 3-5 + 1-3 + 5-8 = ~25 calls/day vs current 0

Bry must choose A/B/C/D from §7.2 before any implementation.

---

## 9. Git State

| Item | Value |
|------|-------|
| HEAD | `bdf76ad` (M6.1-7 closeout) |
| origin/main | `bdf76ad` (synced) |
| Working tree | 0 modified, 20 untracked preserved |
| This audit produces | `logs/m6_1_8_agency_reenable_investigation.md` (closeout commit only) |
| Production data | byte-for-byte unchanged |
| Frozen contracts | 0 changed |

---

## 10. Production Integrity

| Item | Status |
|------|--------|
| Production data | 0 modified |
| Production config | 0 modified |
| Frozen contracts | 0 changed |
| Server | Running, /health=200, PIDs 8568 + 20800 |
| Calendar/Weather/News | All still polling |
| TG conversation | Still working (independent of scheduler) |
| InnerLife writes | Still 0 (last write 8/12 22:49) |
| Diary writes | Still 0 (last write 8/10 01:05 anomaly) |
| Memory mutations | Only from TG conversation, not from scheduler |

---

## 11. Recommended Next Ticket

### M6.1-8.1 — Minimal Agency Re-enable (IMPLEMENTATION)

**Mode**: IMPLEMENTATION
**Scope**: +3 lines in `scripts/run_server.py`
**Pre-conditions**:
- Bry 拍板 A / B / C / D from §7.2
- Bry 確認 dispatcher timing (08:00 / 22:00 / 22:05 / etc.)
- Bry 確認 10-agent burst acceptable (or use B/C/D)
- Bry 確認 InnerLifeEvent burst rate acceptable

**Acceptance**:
- 24h RUN-AND-COLLECT after deploy
- `scheduler.start()` log shows `agents=10`
- Diary / Dream / Event / Proactive_dm all functional
- 0 frozen contract changes
- 0 production data corruption
- /health=200 stable
- InnerLifeEvent count +30-50/day

**Stop conditions**:
- Any frozen contract conflict
- Any unexpected production data corruption
- LLM rate limit overflow (10 simultaneous calls)
- Memory middleware errors
- InnerLifeEvent creation failures

### M6.1-9 — Lived Context Formation Audit (DEFERRED)

**Pre-conditions**:
- M6.1-8.1 closed
- Agency re-enabled
- 24h+ RUN-AND-COLLECT after M6.1-8.1
- Multi-signal world_context observable (Calendar + Weather + News + InnerLife + Diary)

**Mode**: READ-ONLY
**Objective**: Determine if multi-signal Lived Context is forming (per M6.1-0/M6.1-1/M6.1-7 reassessment).

---

## 12. M6.1 Milestone Status Update

| Ticket | Status | Note |
|--------|--------|------|
| M6.1-0 | CLOSED | Lived Context Awareness Architecture Audit |
| M6.1-1 | CLOSED | Lived Context Taxonomy & Minimal Architecture |
| M6.1-2 | CLOSED | Canonical Boundary & Documentation (9e050f6) |
| M6.1-3 | CLOSED | Evidence & Calendar Run-and-Collect Audit |
| M6.1-3.1 | CLOSED | Open-Meteo Weather Source (ac50256) |
| M6.1-3.2 | CLOSED | Live Weather Activation & E2E |
| M6.1-3.3 | CLOSED | Organic Weather Context Evaluation |
| M6.1-4 | CLOSED | Personal Lived Context Capability Audit (DEFER signal) |
| M6.1-5 | CLOSED | Information Lived Context Capability Audit (need News) |
| M6.1-5.1 | CLOSED | RSS News Source (9f8ece8) |
| M6.1-5.2 | CLOSED | Live News Activation & E2E (with accept-gate caveat) |
| M6.1-5.3 | CLOSED | News Lookback & Context Density Audit (KEEP 2h) |
| M6.1-6.0 | CLOSED | Personal Architecture Decision (DEFER) |
| M6.1-6.0-C | CLOSED | Personal Audit Closeout (49adf46) |
| M6.1-7 | CLOSED | Production Lived Context Evidence Reassessment (bdf76ad) |
| **M6.1-8** | **CLOSED** | **Agency Re-enable Investigation (this audit)** |
| M6.1-8.1 | PENDING | Minimal Agency Re-enable (Bry decision required) |
| M6.1-9 | PENDING | Lived Context Formation Audit (deferred, after M6.1-8.1) |

**M6.1 series status**:
- Signal half (Physical + Information + Social Temporal): ✅ COMPLETE
- Life half (Personal + Agency + Expression): ❌ **BLOCKED at Agency layer**
- Bry decision required to unblock Life half

---

## 13. Why Now?

M6.1-7 (just closed) confirmed:
- Lived Context is NOT YET FORMED
- World → Perception: ✅ operational
- Perception → Lived Context: ⚠️ single-source only (Weather)
- Lived Context → Soul Interpretation: ⚠️ influencing (1081/3440 LLM responses)
- Soul Interpretation → Agency: ❌ **BROKEN** (Scheduler `agents=0` since 8/8)
- Agency → Expression: ❌ INACTIVE

The M6.1-7 finding triggered M6.1-8 (this audit) to find out WHY Agency is broken. M6.1-8 found the **root cause** (M5.2 regression) and proposes **minimal fix** (3 lines).

Without M6.1-8.1, M6.1 series cannot reach "Lived Context actually informs Agency and Expression" — the entire M6.1 thesis is blocked at the Agency layer.

---

## 14. Lessons Learned (M5.2-I Phase I-9 Verdict Failure)

This regression is a textbook case of "test passing ≠ behavior correct":

1. **I-9 sweep marked PASS** because all 71 handler tests pass with explicit events
2. **I-9 sweep did NOT test scheduler → handler end-to-end** (would have caught `_all_agents` empty)
3. **API COMPAT verdict** treated "interface preserved" as "behavior preserved" — false equivalence
4. **Production 完全沒註冊 was documented** but not connected to "= all 5 trigger paths broken"
5. **Regression shipped to production 21:11 same day**, no smoke test before deploy

**Bry 拍板 needed**: 8/14 22:38 EDT, status = "ROOT CAUSE IDENTIFIED, FIX PROPOSED, AWAITING OWNER DECISION".

---

## 15. Acceptance Criteria

| Criterion | Status |
|-----------|--------|
| `agents=0` root cause identified | ✅ M5.2-I Phase 7 callsite removal, I-8 iteration source change, no replacement |
| Configuration source identified | ✅ `scripts/run_server.py:760-763, 773-776, 1133` |
| Historical intent checked | ✅ M5.2-I I-9 sweep (8/8 17:50) documents "production 完全沒註冊" |
| Agency execution graph documented | ✅ §5 |
| Disabled components identified | ✅ 5 of 6 trigger paths broken (heartbeat separately disabled by 修法 12) |
| Production side effects identified | ✅ §6 |
| Safety implications assessed | ✅ §6 + §7.3 |
| Minimal re-enable strategy proposed | ✅ §7 (4 options, Bry decides) |
| No source code changes | ✅ This is READ-ONLY audit |
| No configuration changes | ✅ |
| No production mutation | ✅ |
| Frozen contracts unchanged | ✅ |

**All 12 acceptance criteria MET. Ticket CLOSED.**

---

## 16. Modified Files

This is a READ-ONLY audit. **0 files modified in source/test/production**. Only deliverable is this report, committed in closeout (M6.1-8-C).

- **Created**: `logs/m6_1_8_agency_reenable_investigation.md` (this file)
- **Committed in closeout (M6.1-8-C)**: this file

---

## 17. Whether a New Ticket is Justified

**YES — M6.1-8.1 is justified**.

Per GOV-2 §2.8, M6.1-8.1 (Minimal Agency Re-enable) requires:
- Bry 拍板 on Option A / B / C / D from §7.2
- Bry 拍板 on dispatcher timing
- Bry 拍板 on burst acceptability

**Quality > Quantity** rule per work order: M6.1-8.1 has P0 (regression) + P1 (M6.1 series blocked) findings. Justified.

---

## 18. Final Verdict

**ROOT CAUSE**: M5.2 migration regression, `_all_agents` permanently empty, all 5 scheduler trigger paths silently skip.

**FIX**: 3 lines in `run_server.py`, 0 contract change, 0 production data change.

**DECISION REQUIRED**: Bry 拍板 on A/B/C/D from §7.2.

**RECOMMENDED NEXT**: M6.1-8.1 (Implementation, scope = 3 lines), then M6.1-9 (Lived Context Formation Audit).

**M6.1 series**: 50% complete (Signal half ✅, Life half ❌ blocked at Agency). M6.1-8.1 is the key to unblocking Life half.

---

**END OF M6.1-8 AUDIT REPORT**
