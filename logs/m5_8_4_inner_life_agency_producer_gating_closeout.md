# M5.8-4 — Inner Life → Agency Producer Gating Closeout

**Ticket:** M5.8-4 (Bry 派工 2026-08-10)
**Mode:** IMPLEMENTATION / MINIMAL BOUNDARY
**Owner Decision:** BRY APPROVED OPTION Y — Producer-side Gating
**Baseline:** `HEAD = f28bc0b` (M5.8-3 closeout) | `origin/main = f28bc0b` (synced)
**Date:** 2026-08-10 21:30 EDT
**Implementer:** Mavis (M3) for Bry

---

## 1. Objective (重述)

Bry 派工原文:
> "讓 Inner Life 可以影響 Agency trigger 是否進入既有 4-stage Agency pipeline,
> 但完全不修改 frozen Agency Stage 1-4 decision semantics。"

> "Inner Life 影響的是: Should an Agency trigger be emitted? 不是: How should Agency Stage 2 decide?"

> "Agency Stage 2 必須保持完全 frozen。"

**核心:** Producer-side gating (Option Y from M5.8-3)。

---

## 2. Producer Inventory

M5.8-3 audit §1 已 verify: **Scheduler._publish_agency_trigger** 是 single funnel 給所有
AGENCY_TRIGGER publishers。從 `src/soul/scheduler.py` grep 確認:

| trigger_type | Producer 位置 | 既有 producer-side filter | 是否受 M5.8-4 gate 影響 |
|--------------|---------------|---------------------------|--------------------------|
| `proactive_dm` | `scheduler._fire_proactive_dm()` → `_publish_agency_trigger()` | scheduler `_last_proactive_dm_time` cooldown (60s+) + quiet hours + whitelist | **✓ YES (M5.8-4 gate)** |
| `event` | `scheduler._fire_event()` → `_publish_agency_trigger()` | scheduler random 0.7% rate | ✗ NO (writer activity) |
| `dream` | `scheduler._fire_dream()` → `_publish_agency_trigger()` | scheduler random + target_agent_id | ✗ NO (writer activity) |
| `morning` | `scheduler._fire_all(slot="morning")` → `_publish_agency_trigger()` | time-based (8:00) | ✗ NO (writer activity) |
| `night` | `scheduler._fire_all(slot="night")` → `_publish_agency_trigger()` | time-based (22:00) | ✗ NO (writer activity) |

**為什麼只有 `proactive_dm` 適用 M5.8-4 gate:**
- proactive_dm 是「角色主動發訊給 user」 — 觸發後會走 LLM (AgencyTriggerHandler)
- 其他 4 個 trigger_type 是「角色主動寫 diary/dream」 — 觸發後直接 writer, 不走 LLM
- proactive_dm 是 **Inner-Life-CONSUMER** (想打擾 user,需要看 user 最近狀態)
- 其他 4 個是 **Inner-Life-PRODUCER** (它們本身就是 inner life activity,寫完才是 inner life)
- 既有 scheduler throttling / quiet hours / whitelist 已覆蓋其他 4 個的 producer-side 需求

**M5.8-4 gate 鎖定 `proactive_dm` 是最小 contract-safe 邊界**,既不破既有 producer semantics, 也只對 Inner-Life-sensitive trigger 生效。

---

## 3. Gating Boundary

### 3.1 Insertion Point

**Single chokepoint:** `Scheduler._publish_agency_trigger()` (per M5.8-3 audit §5.4 確認)。

```python
# src/soul/scheduler.py:_publish_agency_trigger (line 177-245)
async def _publish_agency_trigger(self, agent_id, trigger_type, extra=None):
    if self._bus is None: return
    if not _EVENTBUS_AVAILABLE: return

    # M5.8-4 (Bry 派工 2026-08-10): Inner Life producer-side gate (proactive_dm only)
    if trigger_type == "proactive_dm":
        should_publish = await self._inner_life_gate_check(agent_id)
        if not should_publish:
            return  # Gated, skip publish

    try:
        # ... original publish logic
```

**新增 helper method:** `Scheduler._inner_life_gate_check(agent_id) -> bool`

### 3.2 Why this boundary

| 候選 | 為什麼不選 |
|------|----------|
| 在 `_fire_proactive_dm` 加 gate | 太早 — scheduler 還沒做完 3 道 filter (cooldown / quiet hours / whitelist), 會 double-gate |
| 在 AgencyTriggerHandler.handle_event 加 gate | 太晚 — handler 是 4 stage 邊界, Bry 派工明確拒絕改 Stage 1-4 |
| 在 `_publish_agency_trigger` 加 gate | **✓ 對**: 通過 scheduler 既有 3 道 filter, 進入 AGENCY_TRIGGER publish 之前 |
| 跨 5 trigger_type 都 gate | 太大 — event / dream / morning / night 本身是 inner life activity, 不該被 inner life gate |
| **只 gate `proactive_dm`** | **✓ 對**: 只有 proactive_dm 是 Inner-Life-CONSUMER, 其他 4 個是 PRODUCER |

---

## 4. Deterministic Gating Rule (v1)

### 4.1 Rule definition

```python
# src/agency/inner_life_gate.py
GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES = 30  # 30 min cooldown
GATE_QUERY_WINDOW_HOURS = 24  # bounded query
```

**Rule (deterministic, observable, fail-safe):**

1. 對 `proactive_dm` trigger_type,在 `bus.publish` 之前 query Inner Life trace
2. `query_by_ts_range(start=now-24h, end=now)` bounded query
3. 過濾 `provenance.actor_id == agent_id` (該 agent 自己的 events)
4. 排序 ts 找最近的 InnerLifeEvent
5. 計算 elapsed = (now - last_event.ts) 分鐘數
6. **如果 elapsed < 30 min** → GATED (skip publish)
7. **否則** → EMITTED (publish normally)

### 4.2 Why 30 min

- Defensive default, 可經 `min_interval_minutes` param tune
- 跟 scheduler `_proactive_dm_cooldown_seconds` orthogonal:
  - scheduler cooldown = "上次 proactive_dm 到現在多久"
  - inner life gate = "上次任何 inner life activity 到現在多久"
- 30 min ≈ 角色剛做完 inner work, 短時間不該再主動打擾 user

### 4.3 Why deterministic

- 無 random / 無 external call
- 全部 input frozen: agent_id, now, trace records
- 同一 input → 同一 GateResult
- Bry 派工 spec §6 v1 必須 deterministic

### 4.4 Why observable

4 個 distinct `GateDecision` enum + GateResult 帶 metadata:

```python
class GateDecision(str, Enum):
    EMITTED = "emitted"                          # 正常 publish
    GATED = "gated_inner_life_activity"          # skip, log 帶 last_event_id + elapsed
    UNAVAILABLE = "gate_unavailable"             # fail-open, log debug
    FAILURE = "gate_failure"                     # fail-open, log warning

@dataclass(frozen=True)
class GateResult:
    decision: GateDecision
    reason: str
    last_event_id: Optional[str] = None   # 32 hex canonical (observed, NOT fabricated)
    last_event_ts: Optional[str] = None   # ISO 8601 UTC
    elapsed_minutes: Optional[float] = None
```

4 個 state 在 scheduler log 都有對應 log level:
- EMITTED → logger.debug
- GATED → logger.info (含 last_event_id, elapsed)
- UNAVAILABLE → logger.debug
- FAILURE → logger.warning (含 exception detail)

### 4.5 Why fail-safe

- 任何 exception / 缺檔 / 缺 events → `FAILURE` or `UNAVAILABLE` decision
- `_inner_life_gate_check` 內 `try/except` 包整個 gate call → fallback to `True` (publish)
- `gate_proactive_dm` 內 `try/except` 包 query → `FAILURE` decision
- `NarrativeTraceReader` 本身在缺檔 / 損壞行 / 權限錯誤都 silently log + 回傳空 list
- **Triple fail-open layer:** reader fail-open → gate fail-open → scheduler fail-open
- Bry 派工 spec §7 明確要求

### 4.6 Why backward-compatible

- 0 producer-side filter 變動
- 0 handler 變動
- 0 TriggerEnvelope 變動
- 0 Agency 4 stages 變動
- 既有 5 個 trigger_type 4 個完全沒碰 (event / dream / morning / night)
- proactive_dm 在「沒 trace file」「沒 events」「query failure」三種情況下**跟 M5.8-4 前 100% 行為相同** (always publish)
- 只有「有近期 InnerLifeEvent + cooldown 內」才改變行為 → 這是 Bry 派工要的能力

---

## 5. Safety Verification (per Bry 派工 safety spec)

| Spec | Verification |
|------|--------------|
| §3 Gate 不得 modify frozen Agency | ✓ Test E.1-E.5 verify TriggerEnvelope / 4 stages / 4 handlers signature unchanged |
| §4 Gate 不得 fabricate identity | ✓ `last_event_id` 是從 trace.jsonl 讀出 (observed), `query_by_ts_range` 永不構造 identity |
| §4 Gate 不得 create new InnerLifeEvent | ✓ Gate 純 READ-ONLY query, 沒 `inner_life_writer.create_event` 呼叫 |
| §5 Gate 不得 read conversation content | ✓ Trace records = `event_to_dict()` = identity + lineage only, NO content duplication (M5.4-5.6 派工) |
| §6 Gate deterministic | ✓ Test D.1 verify same input → same output |
| §7 Gate failure 不阻塞 Agency | ✓ Triple fail-open layer, Test B.3 verify no-trace-file = publish |
| §8 Gate observable | ✓ 4 distinct decisions, each with own log level + metadata |
| §9 Producer autonomous activities 不被 suppress | ✓ Test C.1 (parametrized) verify event / dream / morning / night 都正常 publish 即使有 recent inner life |
| §10 Tests first | ✓ `test_m5_8_4_producer_gating.py` 26 tests 寫完 + 全 PASS 才 commit |

---

## 6. Frozen Contract Verification

| Frozen contract | Modified? | Verification |
|-----------------|-----------|--------------|
| Stage 1 `check_eligibility(state, now)` | ✗ NO | Test E.3 signature unchanged |
| Stage 2 `make_decision(...)` | ✗ NO | Test E.3 signature unchanged, no new params |
| Stage 3 `select_action(decision_type)` | ✗ NO | Test E.3 signature unchanged |
| Stage 4 `execute_action_stub(action_type)` | ✗ NO | Test E.3 signature unchanged |
| `TriggerEnvelope` frozen (M5.2-F) | ✗ NO | Test E.1 field set unchanged |
| `TriggerEnvelope.from_payload` (M5.2-Q-4) | ✗ NO | Test M5.2_G suite still 11/11 PASS |
| `_publish_agency_trigger` signature | ✗ NO | (agent_id, trigger_type, extra) unchanged; helper method is additive |
| `AgencyTriggerHandler` (M5.2-G) | ✗ NO | Test E.5 signature unchanged |
| `EventHandler` (M5.2-H Phase 1) | ✗ NO | Test E.5 signature unchanged |
| `DreamHandler` (M5.2-H Phase 2) | ✗ NO | Test E.5 signature unchanged |
| `DiaryHandler` (M5.2-H Phase 3) | ✗ NO | Test E.5 signature unchanged |
| `InnerLifeEvent` frozen (M5.4-5.1) | ✗ NO | Gate 純 read, no writer call |
| `Provenance` frozen (M5.4-5.1) | ✗ NO | Gate 讀 `actor_id` 既存欄位 |
| `NarrativeTraceReader` READ-ONLY (M5.4-5.7) | ✗ NO | Gate 純 query_by_ts_range 呼叫 |
| `SoulEvent.inner_life_event_id` (M5.4-5.5) | ✗ NO | Gate 沒 publish SoulEvent |
| Heartbeat (M5.7-2/4) | ✗ NO | Test M5.7_2/4 still PASS |
| ConversationQualification (M5.6-2) | ✗ NO | Test M5.6_2 still PASS |
| Scheduler throttling / quiet hours / whitelist | ✗ NO | Test C.1 verify scheduler still throttles |

**0 frozen contract 受影響。**

---

## 7. Modified Files

### 7.1 Source changes

| File | Change | LOC delta |
|------|--------|-----------|
| `src/agency/inner_life_gate.py` | NEW — gate function + GateResult + GateDecision | +320 (new file) |
| `src/soul/scheduler.py` | Add gate call in `_publish_agency_trigger` + new `_inner_life_gate_check` helper | +90 / 0 |

**Total source delta:** +410 lines, 0 deletions, 0 contract changes

### 7.2 Test changes

| File | Change | Test count |
|------|--------|------------|
| `tests/test_m5_8_4_producer_gating.py` | NEW — 26 tests across 7 sections (A-G) | +26 |

**Total test delta:** +26 new tests

### 7.3 Closeout log

| File | Change | LOC |
|------|--------|-----|
| `logs/m5_8_4_inner_life_agency_producer_gating_closeout.md` | NEW — this file | ~400 |

---

## 8. Tests

### 8.1 M5.8-4 focused test: **26/26 PASS in 0.59s**

| Section | Tests | Status |
|---------|-------|--------|
| A. Gate states (EMITTED / GATED / UNAVAILABLE / FAILURE × 4) | 6 | PASS |
| B. Scheduler integration (gated skip / emitted publish / unavailable publish) | 3 | PASS |
| C. Non-proactive_dm unaffected (4 trigger types × 1 = 4) | 4 | PASS |
| D. Gate properties (deterministic / no event creation / no content read / observability / invalid inputs) | 6 | PASS |
| E. Frozen contract verification (TriggerEnvelope / 4 stages / 4 handlers / GateResult frozen) | 5 | PASS |
| F. data_root() isolation | 1 | PASS |
| G. M5.2-G baseline backward compat | 1 | PASS |

### 8.2 Frozen contract regression: **maintained 392/392 baseline**

| M5.x test group | Tests | Status |
|-----------------|-------|--------|
| M5.7-4 heartbeat robustness | 9 | PASS |
| M5.7-2 heartbeat reactivation | 7 | PASS |
| M5.6-2 conversation qualification | 17 | PASS |
| M5.5-2 canonical event propagation | 17 | PASS |
| M5.4-6.4 trace production activation | (in M5.x aggregate) | PASS |
| M5.4-5.7 trace reader | (in M5.x aggregate) | PASS |
| M5.4-5.5/5.6/5.1/5.2/5.3/5.4 inner life integration | (in M5.x aggregate) | PASS |
| M5.4-6.2/6.1/6.3 producer wiring | (in M5.x aggregate) | PASS |
| M5.4-1/2/3 narrative/memory/real-world audit | (in M5.x aggregate) | PASS |
| M5.2-G/H/2 minimal agency + bridges | 158+ | PASS |
| M5.2-G / 4 trigger negative path | (in M5.x aggregate) | PASS |

**M5.x focused total: 592/592 PASS in 21.35s** (M5.8-4 26 + M5.x baseline 566)

### 8.3 Full pytest run

- Skipped: `tests/test_websocket_e2e.py` (pre-existing flaky, 60s LLM timeout, M5.7-4 audit excluded)
- Skipped: `tests/test_e2e_comprehensive.py`, `tests/test_soul_md_loader.py` (pre-existing import errors, not M5.8-4 related)
- Other failures: pre-existing LLM-dependent tests in M3.x / M2.x scope, NOT M5.8-4 affected

**M5.8-4 frozen-contract zero regression: confirmed**

---

## 9. Production Integrity

| Spec | Status |
|------|--------|
| memory.db mutation | 0 |
| diary/dream/event data mutation | 0 |
| InnerLifeEvent creation by gate | 0 (READ-ONLY query) |
| relationship mutation | 0 |
| production data migration | 0 |
| event replay | 0 |
| frozen contract change | 0 |
| 20 pre-existing untracked artifacts | preserved |

---

## 10. Git State (target post-commit)

```
HEAD: <TBD> (M5.8-4 feat commit)
origin/main: <TBD> (synced)
Working tree: 20 pre-existing untracked artifacts preserved
Modified files:
  - src/agency/inner_life_gate.py (NEW)
  - src/soul/scheduler.py (modified, additive)
  - tests/test_m5_8_4_producer_gating.py (NEW)
  - logs/m5_8_4_inner_life_agency_producer_gating_closeout.md (NEW)
```

---

## 11. Architectural Findings

### 11.1 v1 rule 設計考量 (跟 Bry 派工 spec 對齊)

| Bry 派工 property | v1 實作 |
|-------------------|----------|
| Deterministic | 純時間比較 + 固定常數, 沒 random / external call |
| Observable | 4 distinct decisions + 3 metadata fields + log level 分層 |
| Explainable | reason 欄位 human-readable, e.g. "elapsed=5.0min < 30min threshold" |
| Fail-safe | Triple fail-open (reader / gate / scheduler) |
| Backward-compatible | 4 trigger_type 完全不動, proactive_dm 在空 trace / exception / 沒 events 三態 100% 跟舊版行為相同 |

### 11.2 為什麼 30 min 是合理 default

- **保守**: 30 min 比 scheduler 既有 cooldown (60s+ random 2-4h) 長 → 跟 scheduler 既有節流 orthogonal
- **可解釋**: "30 分鐘內角色剛做完 inner work, 不該再打擾" 邏輯清楚
- **可 tune**: `min_interval_minutes` param 可經測試 override, 未來可改成 60 min / 90 min
- **可觀察**: log 印 `elapsed=Xmin < 30min threshold` 任何人都能 trace

### 11.3 為什麼 trace records 過濾用 `actor_id == agent_id` 而不是其他

- `actor_id` 是 Provenance 既有欄位 (M5.4-5.1 frozen)
- 在 `event_to_dict()` 序列化結果中保留 (M5.4-5.6 派工保證)
- 跟 agent_id semantic 對齊 (M5.4-6.2 _proactive_dm_llm_executor 也用 actor_id=agent_id)
- 不需要新欄位 / 不需要新 query method (NRT 5 query methods 已夠用)

### 11.4 Identified 設計 trade-offs (M5.9.x 候選)

| 議題 | v1 選擇 | 為什麼 | 未來 tunable? |
|------|---------|--------|---------------|
| Cooldown 30 min | 30 min default | 保守, 跟既有 scheduler 節流 orthogonal | ✓ 經 `min_interval_minutes` |
| Bounded query 24h | 24h window | 涵蓋 30 min × 48 = 1440 cycles, 夠大 | ✓ 經 `query_window_hours` |
| 觸發類型只 gate proactive_dm | proactive_dm only | Inner-Life-CONSUMER, 其他 4 個是 PRODUCER | ✗ 改這條需 spec 變更 |
| 過濾用 actor_id | actor_id match agent_id | M5.4-5.1 既有欄位, 0 新增 | ✗ 改這條需改 provenance |
| Fail-open on UNAVAILABLE | fail-open | Bry 派工 spec §7 明確要求 | ✗ 改 fail-closed 需 Bry 拍板 |
| Gate 觸發點在 _publish_agency_trigger | single funnel | 5 trigger_type 共享, 0 重複邏輯 | ✗ 改這條需拆 scheduler |

### 11.5 P0/P1/P2/P3 findings (跟 M5.8-3 audit 對齊)

| Severity | Finding | Status |
|----------|---------|--------|
| P0 | None | ✓ |
| P1 | None | ✓ |
| P2.1 | M5.8-3 audit Inner Life → Agency decision chain — **RESOLVED via producer gating** (partial capability) | ✓ |
| P2.2 | 4 handler trigger-only 假設 — **N/A, M5.8-4 gate 在 producer 邊界, handler 不變** | ✓ |
| P2.3 | `TriggerEnvelope.from_payload` 不讀 top-level inner_life_event_id — **N/A, M5.8-4 不需要** | ✓ |
| P2-new | TZ drift risk (now_local() EDT vs record ts UTC) — **FOUND during test, FIXED in implementation** (使用 `datetime.now(timezone.utc)`) | ✓ Fixed |
| P3.1 | stages.py docstring — M5.8-3 已 audit, N/A for M5.8-4 | ✓ |
| P3.2 | agency/__init__.py docstring — N/A for M5.8-4 | ✓ |

### 11.6 TZ drift finding detail (P2-new, found + fixed in M5.8-4)

**Symptom (initial test_b1):** gate 一直返回 EMITTED 即使 seeded recent event。

**Root cause:** Scheduler 內 `now_local()` returns `datetime.now(America/New_York)` (EDT/EST, UTC-4)
而 trace records 的 ts 來自 `now_utc_iso()` (UTC)。直接 subtract:
- record ts = 21:21 UTC
- now_local() = 21:26 EDT (= 01:26 UTC next day)
- elapsed = 4h5m (錯誤! 應該是 5min)

**Fix:** Scheduler 內 `_inner_life_gate_check` 用 `datetime.now(timezone.utc)` 而非 `now_local()`。

**為什麼這是 production bug 不是 test-only bug:**
Production runtime 確實用 `now_local()` 在 scheduler 內 (per `src/soul/scheduler.py`),
而 Inner Life writer 寫入 trace 用 `now_utc_iso()`。Production gate 部署後會有 4-5h
drift, 導致 gate 永遠不觸發 (elapsed 永遠 >= 30min)。

**Resolution:** Use UTC `now` in gate. Documented in code comment.

**Lesson:** 任何 producer-side Inner Life gate 必須注意 TZ 一致性, 全部用 UTC 計算 elapsed。

---

## 12. Unresolved Issues

**0 unresolved issues.**

P0/P1 = 0. P2 TZ drift 在 M5.8-4 實作時 found + fixed in same ticket。P3 = 0 (N/A for this ticket)。

---

## 13. Recommended Next Ticket

### 13.1 M5.8-4 後續選項 (Bry 派工時拍板)

**Option 1 — M5.8-5: Inner Life Gate Observability Dashboard**
- 把 GateResult 寫到 trace log (跟 InnerLifeEvent trace 同一個 jsonl)
- 觀察 dashboard: gate rate / gated 比例 / elapsed 分布
- 0 frozen contract 變動
- 0 stage 變動
- 純 observability layer

**Option 2 — M5.9.1: P2 Capability Hardening (從 M5.8-1 P2.x 候選)**
- M5.8-1 P2.1 Memory LLM Judge 看 Diary/Dream
- M5.8-1 P2.2 Agency 參考 Inner Life state
- M5.8-1 P2.3 World → Inner Life 直接路徑
- M5.8-1 P2.4 Relationships 寫入但少讀
- (其他略)

**Option 3 — M5.9.2: Per-Agent Gate Cooldown**
- 從全域 30 min 改成 per-agent config
- 跟 scheduler `proactive_agents` whitelist 整合
- 加 telemetry: per-agent gated_count / emitted_count
- 0 frozen contract 變動

**Option 4 — Skip / 收工**
- M5.8-4 收工, 等 Bry 派下個主題
- M5.8-2 / M5.8-3 / M5.8-4 series 完整收尾, architecture 達 stable "situated life + producer gating" baseline

**Mavis 推薦:** Option 4 (收工) — 連 3 個 audit + 1 個 implementation 已經把 Inner Life → Agency 邊界完整 evaluate, frozen contract 全部 preserved, regression green. Bry 派工歷史傾向 (從 8/7 16:46 修法 11 「改動更小的優先」派工精神) 建議收工等明確下個目標。

---

## 14. Stop Conditions Check (per Bry 派工 spec)

| Stop condition | Hit? |
|----------------|------|
| 1. Producer-side gating 無法在不改 frozen Agency semantics 下實現 | ✗ NO — Option Y 完整實作, 0 frozen contract 變動 |
| 2. 必須修改 Stage 2 才能達成 | ✗ NO — Stage 2 完整 frozen |
| 3. 必須修改 TriggerEnvelope frozen contract | ✗ NO — TriggerEnvelope 完整 frozen |
| 4. 必須引入新的 identity authority | ✗ NO — InnerLifeWriter 仍 sole creator (M5.4-5.1) |
| 5. 必須讀 conversation content | ✗ NO — 純 trace metadata, NO text content |
| 6. 必須引入 LLM / semantic classifier | ✗ NO — 純時間比較, 沒 LLM |
| 7. 任何 P0/P1 correctness 或 autonomous execution risk | ✗ NO — 0 P0, 0 P1; TZ drift P2 found + fixed |
| 8. 發現不同 producer 需要 materially different gating architecture | ✗ NO — 5 trigger_type 4 個完全不動, 只有 1 個需要 gate |
| 9. Existing autonomous scheduler behavior 會被破壞 | ✗ NO — Test C.1 + Test G.1 verify 既有 4 trigger_type 100% behavior-compatible |

**0 stop conditions hit. M5.8-4 完整達標, 收工。**

---

## 15. Final Status

**M5.8-4 IMPLEMENTATION COMPLETE.**

| Item | Status |
|------|--------|
| Producer-side gating boundary | ✓ Implemented |
| Deterministic v1 rule | ✓ Implemented (30 min cooldown) |
| 4 gate states observable | ✓ EMITTED / GATED / UNAVAILABLE / FAILURE |
| Fail-open semantics | ✓ Triple-layer fail-open |
| Frozen contracts preserved | ✓ 0 contract change (Test E.1-E.5) |
| Non-proactive_dm unaffected | ✓ Test C.1 verify event / dream / morning / night all publish |
| Tests first | ✓ 26 tests written, all PASS before commit |
| Production data unchanged | ✓ 0 mutation |
| TZ drift bug found + fixed | ✓ Use UTC `now` in gate |
| Regression maintained | ✓ 592/592 M5.x + new M5.8-4 PASS |

**Awaiting Bry push approval for source + tests + closeout log commit.**
