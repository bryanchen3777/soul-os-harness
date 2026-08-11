# M5.11-2 — P2.7 / P2.5 Formal Closures + P2.4 Documentation

**Mode:** IMPLEMENTATION / DOCUMENTATION-ONLY
**Baseline:** `6fbb899` (M5.11-1 audit)
**Author:** Mavis / Lin
**Date:** 2026-08-11 EDT

---

## Objective

Formally document three intentional architecture boundaries established by M5.11-1 audit:
- P2.7: Stage 4 STUB is an intentional frozen decision boundary
- P2.5: EmotionalCarryover is active infrastructure, not dead code
- P2.4: Relationship read access is intentionally deferred to future Stage 4.2/4.3

---

## Changes Made

### P2.7 — Stage 4 Architecture Boundary

**File:** `src/agency/stages.py`

**Module header docstring** (lines 11-20):
```
M5.11-2 (Bry 派工 2026-08-11): Stage 4 Architecture Boundary
─────────────────────────────────────────────────────────────────
Stage 4 是 M5.1 frozen contract 的設計決策:
  - Stage 4 是 deterministic decision boundary (要不要 act?)
  - Stage 4 不是 execution boundary (怎麼 act / bus.publish)
  - 真正執行: run_server.py 注入的 llm_executor (_proactive_dm_llm_executor 等)
    → _fire_intent → LLM → bus.publish(AGENT_SPEAK)
  - Stage 4 不得擴展去複製 executor 行為 (不在 frozen scope 內)
  - executor 層是 runtime wiring, 不是 frozen contract 的一部分
─────────────────────────────────────────────────────────────────
```

**`execute_action_stub` function docstring** (lines 208-212):
```
M5.11-2 (Bry 派工 2026-08-11):
Stage 4 是 frozen decision boundary, 不是 execution boundary。
真正 AGENT_SPEAK 發生在 executor layer (run_server.py):
  _proactive_dm_llm_executor → _agent._fire_intent() → bus.publish(AGENT_SPEAK)。
execute_action_stub 的 STUB reason 是 frozen 輸出, 不應被視為缺失。
```

---

### P2.5 — EmotionalCarryover Active Infrastructure

**File:** `src/heartbeat/engine.py`

**HeartbeatEngine._loop carryover comment block** (lines 211-217):
```
# M5.11-2 (Bry 派工 2026-08-11): EmotionalCarryover 不是死代碼。
# carryover 通過 build_temporal_context → render_temporal_block → chrono_block
# 流入 LLM prompt 生成階段 (consciousness._fire_intent → LLM → 回應)。
# EmotionalCarryover 不是 Agency decision-gating 的輸入:
#   - Agency Stage 1-4 不讀 carryover (M5.7-2 heartbeat constraint)
#   - carryover 用於 LLM 回應的時空情緒 flavor, 不是 decision 門控
#   - 這個分離是 intentional boundary (M5.7-2 拍板), 不是缺失
```

---

### P2.4 — Relationship Read API Intentional Boundary

**File:** `src/soul/relationships.py`

**`MultiAgentRelationshipsManager.get_store` docstring** (lines 447-451):
```
M5.11-2 (Bry 派工 2026-08-11): Relationship read access 是 intentional boundary。
Stage 4.1 只做 write (touch/ensure/update_impression), read API 是預留介面,
為 Stage 4.2 (diary 串接) / Stage 4.3 (LLM impression) 預留。
目前 0 個 production consumer 調用 read API — 這是設計決策, 不是缺失。
Stage 4.2/4.3 範圍確定前不應主動實現 relationship read 邏輯。
```

---

## Acceptance Criteria Verification

| Criterion | Status |
|-----------|--------|
| A. P2.7: frozen Stage 4 boundary distinguished from executor | ✅ |
| B. P2.5: EmotionalCarryover active consumer identified | ✅ |
| C. P2.4: Relationship read limitation documented | ✅ |
| D. Zero behavior change | ✅ |
| E. Frozen contracts unchanged | ✅ |
| No new imports | ✅ |
| No new runtime dependencies | ✅ |
| No new persistence | ✅ |
| No Event Bus changes | ✅ |

---

## Regression

| Suite | Count | Status |
|-------|-------|--------|
| M5.10-2 judge v1 context | 13 | ✅ PASS |
| M5.2 minimal agency | 22 | ✅ PASS |
| M5.7.2 heartbeat reactivation | 20 | ✅ PASS |
| M5.7.4 heartbeat robustness | 9 | ✅ PASS |
| M5.8-4 producer gating | 19 | ✅ PASS |
| M5.9-3 world → inner life adapter | 27 | ✅ PASS |
| M5.9-3.1 production wiring | 46 | ✅ PASS |
| M5.2-G proactive DM bridge | 11 | ✅ PASS |
| M5.4-6.2 proactive DM inner life wiring | 36 | ✅ PASS |
| **Total** | **203** | **✅ PASS** |

---

## Production Integrity

- No memory.db modification
- No v1 memory modification
- No diary/dream/event data modification
- No relationship production data modification
- No replay/backfill
- 0 behavior change (documentation only)

---

## Git State

- **Modified:** `src/agency/stages.py`, `src/heartbeat/engine.py`, `src/soul/relationships.py`
- **Baseline:** `6fbb899`
- **Working tree:** 20 pre-existing untracked artifacts preserved
- **Frozen contracts:** 0 change

---

## Architectural Findings

1. **P2.7 is closed.** Stage 4 is an intentional frozen decision boundary. Real execution is in the runtime executor layer (run_server.py). Future audits will not rediscover this as a missing capability.

2. **P2.5 is clarified.** EmotionalCarryover is active — it flows through `build_temporal_context → render_temporal_block → chrono_block → LLM prompt`. Its non-consumption by Agency decision gating is a deliberate architectural separation per M5.7-2.

3. **P2.4 is closed.** Relationship read APIs are explicitly documented as intentional Stage 4.2/4.3 deferred scope. Zero production consumers.

4. **All three P2 capabilities are now formally documented as intentional boundaries.** Future audits have canonical source-of-truth comments in the codebase itself.

---

## Unresolved Issues

None. M5.11-1 found two Bry decisions still required for P2.2/P2.6 frozen contract scope (Stage 2 change approval). This ticket completes P2.7/P2.5/P2.4 formal closure only — those two decisions remain pending Bry's response to M5.11-1 Final Report.

---

## Audit Metadata

| Field | Value |
|-------|-------|
| Ticket | M5.11-2 |
| Mode | IMPLEMENTATION / DOCUMENTATION-ONLY |
| Baseline | `6fbb899` |
| Frozen contracts | 0 change |
| Regression | 203/203 PASS |
| Modified files | 3 |
| Author | Mavis / Lin |
| Date | 2026-08-11 EDT |
