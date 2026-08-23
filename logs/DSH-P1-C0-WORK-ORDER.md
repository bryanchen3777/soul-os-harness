# 工單：DSH P1-C0 — Domain Core Capability Enforcement

**日期**：2026-08-23
**性質**：Phase 1 implementation（可寫 code）——P1-C 的正式 prerequisite
**上游**：`docs/DSH-P1-C-ROUTING.md`（D4/D9，READY FOR IMPLEMENTATION）、`docs/DSH-WORK-CONTRACT.md`（2A §5.1 role matrix、§3.1 decision 語義）、Owner 拍板（2026-08-23：artifact.create 歸 Researcher）、baseline `65be40b`

---

## 目標

落實 2A §5.1 frozen role→capability matrix 的 **Domain Core enforcement**：`kernel.record_handoff` 在記錄產出前驗證 `handoff.role` 具備 `result_type` 對應的 capability。這是 P1-C1（真 DSH routing）的前置——沒有這個 enforcement，DSH 產出的 artifact/evidence 的 role 授權就無從驗證。

**核心原則**：不為了讓既有測試通過而把 Developer 的 artifact.create 補回 matrix。讓 implementation reality 暴露的 divergence 被修正，不反向修改 frozen contract。

---

## 範圍（只允許）

- `src/work/kernel.py`：`record_handoff` 增加 role↔capability enforcement
- `src/work/roles.py`：僅當需要新增 helper（`has_capability` 已存在，見下）時改；否則不改
- 測試遷移：`tests/test_work_adapter.py`、`tests/test_work_p1_preflight.py`、`tests/test_work_p1a_execution_shape.py` 中所有 `Developer + artifact.create` 的 happy path
- 新增測試：enforcement 的 deny path + decision 不 gate

## 做法（決策已定，執行者照做）

### 1. capability 映射（kernel.py，已存在 `_HANDOFF_CAPABILITY`）

現有 `_HANDOFF_CAPABILITY`（kernel.py）：
```python
_HANDOFF_CAPABILITY: dict[ResultType, str] = {
    ResultType.ARTIFACT: "artifact.create",
    ResultType.EVIDENCE: "evidence.create",
    ResultType.DECISION: "decision",
}
```

**新增 enforcement 語義**（不新增 dict，用既有 `_HANDOFF_CAPABILITY` + 一個 decision 特判）：

```python
# decision 不 gate（2A §3.1：decisions 是任何 agent 的自主選擇，只記錄供 audit，
# 不 gate）。artifact / evidence 才要求 role 具備對應 capability（2A §5.1）。
_NON_GATED_RESULT_TYPES = frozenset({ResultType.DECISION})
```

### 2. enforcement 檢查（`kernel.record_handoff` 產出分支開頭）

在 `record_handoff` 中，`status == DONE` 的產出分支（現有 `event_type = _HANDOFF_EVENT_TYPE[handoff.result_type]` 之前）插入：

```python
# role↔capability enforcement（2A §5.1，P1-C0）：
# 記錄產出前驗證 handoff.role 具備 result_type 對應 capability。
# decision 不 gate（2A §3.1）；blocked/needs_input 不在此（M1 已分流）。
if handoff.result_type not in _NON_GATED_RESULT_TYPES:
    required_capability = _HANDOFF_CAPABILITY[handoff.result_type]
    if not has_capability(handoff.role, required_capability):
        raise CapabilityNotAuthorizedError(
            f"role={handoff.role!r} lacks capability {required_capability!r} "
            f"for result_type={handoff.result_type.value!r}"
        )
```

### 3. import + exception

- kernel.py import `has_capability`：把 `from .roles import Role` 改為 `from .roles import Role, has_capability`。
- 新增 `CapabilityNotAuthorizedError`：**放 `src/work/roles.py`**（與 capability 語義同源），繼承 `PermissionError`（與 `NotDurableWriterError` 同類授權錯誤）：
  ```python
  class CapabilityNotAuthorizedError(PermissionError):
      """role 不具備 result_type 對應的 capability（2A §5.1）。"""
  ```
- kernel.py import 它：`from .roles import Role, has_capability, CapabilityNotAuthorizedError`。

### 4. 測試遷移（~19 處 Developer + artifact.create → Researcher + artifact.create）

**鐵律：不得補回 Developer 的 artifact.create**。遷移方向是「改測試的 role」，不是「改 matrix」。

- `tests/test_work_adapter.py`：約 15 處 `Role.DEVELOPER.value, "artifact.create"` → 改為 `Role.RESEARCHER.value, "artifact.create"`；對應的 assert（`message.actor == Role.DEVELOPER.value` / `handoff.role == ...` / `event.provenance.role == ...`）同步改為 `Role.RESEARCHER`。
- `tests/test_work_p1_preflight.py`：約 4 處 `execute_work(orch, work_id, Role.DEVELOPER.value, "artifact.create", bridge)` → 改為 `Role.RESEARCHER.value`。**注意**：M1 blocked 測試（`role=Role.DEVELOPER.value` + `result_type=ARTIFACT` + `status=BLOCKED`）**不遷移**——blocked 無產出，不觸發 enforcement；但若 blocked 測試斷言 `provenance.capability == "artifact.create"`，這個 capability 標記仍是 `_HANDOFF_CAPABILITY` 推導，不變。
- `tests/test_work_p1a_execution_shape.py`：約 2 處 `Role.DEVELOPER.value, "artifact.create"` → `Role.RESEARCHER.value`。

**遷移後每個 assert 都要同步**：role 字串、`provenance.role`、`handoff.role`、`message.actor`。執行者需逐處核對，不能只改 execute_work 呼叫行。

### 5. 新增測試（enforcement 的 deny path + decision 不 gate）

在 `tests/test_work_adapter.py` 或新檔 `tests/test_work_p1c0_enforcement.py`（新檔較乾淨）新增：

1. `Developer + artifact.create` → `CapabilityNotAuthorizedError`（直接 `orch.consume_handoff(HandoffResult(role=DEVELOPER, result_type=ARTIFACT, ...))`，無需 bridge）
2. `Researcher + artifact.create` → 通過（正控制）
3. `Tester + evidence.create` → 通過；`Developer + evidence.create` → `CapabilityNotAuthorizedError`
4. `Chief + decision` → 通過（decision 不 gate）；`Developer + decision` → 通過（2A §3.1 任何 agent 可記錄 decision）
5. `Auditor + evidence.create` → 通過（Auditor 有 evidence.create）
6. deny 時不寫 durable（rows 不變，半寫入防護）

## 驗收

1. `Developer + artifact.create` 的 `consume_handoff` → `CapabilityNotAuthorizedError`，不寫 durable
2. `Researcher + artifact.create` → 通過
3. `decision` 不 gate（任何 role 可記錄）
4. `blocked/needs_input` 不 gate（M1 語義不變）
5. 全部回歸 + 新測試綠；`src/work/` 零 DSH import 不變

## 測試

- 全 regression：`test_work_*.py` 全跑
- 新增 enforcement deny path 測試

## 不做（Out of Scope）

- **不碰** `dsh --profile headless` transport（P1-C1）
- **不碰** staging / artifact store ingest（P1-C1）
- **不碰** 結構化輸出通道（P1-C1）
- **不改** 2A §5.1 frozen matrix（artifact.create 仍歸 Researcher，不補回 Developer）
- **不改** `docs/` frozen contract、不改 `state_machine.py`、`store.py`、`authority.py`、`persistence.py`
- 不接真 DSH subagent

## Frozen Contract 注意

- 2A §5.1 role matrix 是唯一 authoritative source，本工單是它的 **enforcement 補齊**，不是修改。
- 2A §3.1「decision 任何 agent 產生」→ decision 不 gate。
- 2A §8 七條 non-negotiable 不變。
- Domain Core 零 DSH import 永久不變。

## 回報格式

- 改動檔案清單
- enforcement 實作方式 + decision 不 gate 的語義依據
- 測試遷移清單（改了哪些檔、幾處、role 從什麼改成什麼）
- 新增測試清單 + 結果
- 完整回歸結果
- 確認 `git diff --stat` 只動列名檔案、`src/work/` 零 DSH import
- 剩餘 architectural concerns
