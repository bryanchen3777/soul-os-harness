# 工單：DSH P1-Preflight — Hardening Decomposition

**日期**：2026-08-23
**性質**：Phase 1 前置（hardening，可寫 code）
**上游**：DSH-P0-1 Independent Review（commit `ece757b`，Phase 0 CLOSED）暴露的 5 項 hardening + 7 條 non-blocking discrepancy
**狀態**：ARCHITECTURE DECISION + WORK ORDER（本工單 = 分類決策 + MUST-FIX 實作）

---

## 1. 分類決策（pro 拍板）

### MUST-FIX-before-P1（Phase 1 execution correctness 直接前置）

| # | Concern | 現況 | 決策 |
|---|---|---|---|
| M1 | `HandoffStatus` → WorkState 語義 | `kernel.record_handoff` 完全忽略 `status`，`blocked`/`needs_input` 與 `done` 同等記錄產出 | `done` → 記錄產出（現狀）；`blocked`/`needs_input` → **不記錄產出**，改記錄 `state_transition(current → blocked)`，resume 語義依 2A §4 |
| M2 | `result_type` ↔ `capability` 對齊 | `execution.py` anchor 只驗 `work_id`/`role`，adapter 可回傳與 request capability 不符的 `result_type`（event 類型 + provenance capability 錯記） | anchor 增加驗證 `handoff.result_type == result_type_for_capability(request.capability)` |
| M3 | Bridge error contract 統一 | 非 UTF-8 stdout 冒 `UnicodeDecodeError` 而非 `BridgeExecutionError`（`bridge.py:106` decode 在 except 範圍外） | 捕 `UnicodeDecodeError` → `BridgeExecutionError` |

### CAN-DEFER（hardening backlog，非 Phase 1 前置，本工單不實作）

| # | Concern | 理由 |
|---|---|---|
| D1 | refs content-address 驗證 | 真正的「hash 對應內容」驗證需要 artifact store；2B 才詳細設計 artifact storage。Phase 1 尚無 artifact store，refs 是 opaque pointer（可接受）。**格式層**驗證（ref 符合 `sha256:<hex>`）可併入 Phase 1 工單（與 TS mirror schema 驗證一起），不獨立做。 |
| D2 | production adapter package boundary（移出 repo） | Phase 1 仍在 repo 內開發 adapter（接真 subagent），移出是 deploy 時機的事，非 correctness 前置。 |
| D3 | MA-4-R1 backlog：grant() reject `expires_at=None` | authority 硬化，與 Phase 1 execution routing 無直接前置關係。 |
| D4 | MA-4-R1 backlog：e2e 改用 `issue_hmac_context` | test fidelity，非 Phase 1 前置。 |
| D5 | MA-4-R1 backlog：durable nonce registry | authority replay 防護跨 restart，非 Phase 1 前置。 |

> **細分說明**：P0-1 review 的「refs / result_type / status 未 anchor 驗證」中，`result_type↔capability`（M2）與 `status→WorkState`（M1）是 Phase 1 直接前置；`refs content-address` 因需 artifact store 而 defer（D1），避免在沒有 store 前提前引入假驗證。

---

## 2. MUST-FIX 實作（decision-complete）

### 2.1 M1 — HandoffStatus → WorkState 語義映射

**檔案**：`src/work/kernel.py`（`record_handoff`）

**決策**（依 2A §6 status 欄位 + §4 blocked non-terminal）：
- `status == DONE`：現有行為不變（記錄 `artifact_produced` / `evidence_produced` / `decision_made`）。
- `status == BLOCKED` 或 `status == NEEDS_INPUT`：
  1. `fold(handoff.work_id)` 取 current state（`from_state`）。
  2. `validate_transition(from_state, BLOCKED)`（2A §4：任何 active state 可進 blocked；`state_machine` 已支持）。
  3. 記錄 `WorkEvent(event_type=STATE_TRANSITION, payload={"from": from_state.value, "to": "blocked", "status": handoff.status.value, "resume_hint": handoff.resume_hint}, provenance=Provenance(role=handoff.role, capability=_HANDOFF_CAPABILITY[handoff.result_type]))`。
  4. **不記錄產出**（不寫 `artifact_produced`/`evidence_produced`/`decision_made`）。
  5. `resume_state.current_phase` 由既有 `store.fold_events` 的 `blocked_from` 邏輯自動得出（= 進入 blocked 前的 state），不需改 store。
- 產出分支：`status == DONE` 才走現有 result_type → event_type 邏輯。

**dedup 決策**：`blocked`/`needs_input` 的 handoff **不做 idempotency dedup**（它們不寫產出 event，只寫 state_transition）。注意：**重複 blocked handoff 不是冪等的**——`blocked → blocked` 由 state machine 判為非法（`can_transition` 對 `from == BLOCKED` 特判只允許 resume 回 ACTIVE_STATES），重複 blocked handoff 會拋 `InvalidTransitionError`。這是**正確防護**（避免 `blocked_from` 被覆寫成 `blocked` 污染 `resume_state.current_phase`）。Phase 1 若需 blocked 重試 dedup（如 crash-after-write）再議。

**驗收**：
- `status=done` → 產出 event（不變，回歸全綠）
- `status=blocked` → `state_transition(in_progress → blocked)`，無產出 event
- `status=needs_input` → 同上，payload 帶 `status=needs_input` + `resume_hint`
- 非法 transition（如 terminal state 再進 blocked）→ `InvalidTransitionError`
- fold 後 `work.state == blocked`、`resume_state.current_phase == in_progress`（blocked_from）

### 2.2 M2 — result_type ↔ capability anchor 驗證

**檔案**：
- `src/work/kernel.py`（或 `src/work/schema.py`）：新增 `result_type_for_capability(capability) -> ResultType` canonical 映射
- `src/work_adapter/execution.py`：anchor 增加驗證

**決策**：
- 映射（與 mock adapter 既有行為一致，提升為 Domain Core canonical）：
  - capability 含 `"evidence"` → `ResultType.EVIDENCE`
  - capability 含 `"decision"` → `ResultType.DECISION`
  - 其餘 → `ResultType.ARTIFACT`
- `execution.py` anchor（在 work_id/role 驗證之後）：
  ```python
  expected = result_type_for_capability(capability)
  if handoff.result_type != expected:
      raise BridgeExecutionError(
          f"result_type mismatch: handoff={handoff.result_type.value!r} "
          f"does not match request capability={capability!r} (expected {expected.value!r})"
      )
  ```
- 映射函數放 **Domain Core**（`kernel.py` 或 `schema.py`），adapter 只 import（單一真相在 Domain Core，TS 側 mock mirror 同一語義，但 Python anchor 是 enforcement）。

**驗收**：
- capability `"artifact.create"` → adapter 回 `decision` → `BridgeExecutionError`（fail closed，不寫 durable）
- capability `"evidence.create"` → adapter 回 `artifact` → `BridgeExecutionError`
- 正常對齊路徑（artifact/evidence/decision）全通過（回歸）

### 2.3 M3 — Bridge error contract 統一

**檔案**：`src/work_adapter/bridge.py`

**決策**：`proc.communicate(...)` 的 decode 失敗（非 UTF-8 stdout）會冒 `UnicodeDecodeError`，不在現有 `except subprocess.TimeoutExpired` 範圍。補一個 `except UnicodeDecodeError` → `BridgeExecutionError`（fail closed）。

**驗收**：非 UTF-8 stdout → `BridgeExecutionError`（不是 `UnicodeDecodeError`），且不寫 durable state。

---

## 3. 測試

在 `tests/test_work_adapter.py`（或新增 `tests/test_work_p1_preflight.py`）新增：
1. M1：`status=blocked` handoff → state_transition + 無產出 event + fold blocked + resume_state.current_phase
2. M1：`status=needs_input` handoff → 同上 + payload 帶 needs_input
3. M1：terminal state 再進 blocked → `InvalidTransitionError`
4. M2：result_type mismatch（artifact request → decision 回傳）→ `BridgeExecutionError`
5. M2：evidence request → artifact 回傳 → `BridgeExecutionError`
6. M3：非 UTF-8 stdout → `BridgeExecutionError`

跑全 regression（含既有 230 tests + e2e），確認 `src/work/` 零 DSH import 不變、`git diff` 只動列名檔案。

---

## 4. 不做（Out of Scope）

- D1–D5（CAN-DEFER 五項，本工單不實作）
- 不接真 DSH subagent / workflow / goal（Phase 1 才做）
- 不改 `docs/` 四份 frozen contract（2A–2D）
- 不改 `src/work/store.py` / `state_machine.py` / `authority.py` / `persistence.py`（除非 M1 需要，但決策上不需）
- 不接 Soul runtime / eventbus / World / Inner Life / Agency

## 5. Frozen Contract 注意

- M1 是「補齊 2A §6 已定義 `status` 欄位 + §4 blocked non-terminal 的 enforcement」，不是違反 contract。
- 不新增 WorkState（blocked 已存在）、不新增 ResultType、不新增 HandoffStatus 值。
- 2A §6「result_type 不得有 approval」不變；2A §8 七條 non-negotiable 不變。
- Domain Core 零 DSH import 永久不變。

## 6. 回報格式

- 改動檔案清單
- M1/M2/M3 各自實作方式 + 測試結果
- 完整回歸結果（230 + 新增）
- 確認 `git diff` 只動列名檔案、`src/work/` 零 DSH import 不變
- 剩餘 architectural concerns
