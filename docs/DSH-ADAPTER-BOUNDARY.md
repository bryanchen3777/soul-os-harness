# DSH Adapter Boundary Design（DSH-MA-1）

**日期**：2026-08-23
**狀態**：ARCHITECTURE DESIGN — 前置設計，NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`logs/DSH-MA-0-WORK-ORDER.md`（MA-0 Architecture Audit）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 0. North Star

> **Soul OS owns the durable work truth. DSH owns ephemeral execution.**

Adapter 不是「把 Python API 包成 TypeScript API」。它是 **trust boundary + execution boundary + recovery boundary**。

```text
Domain Core（durable truth + authority，Python）
        │
   Adapter（唯一耦合層，trust + execution + recovery boundary）
        │
DSH（ephemeral execution，TypeScript）
```

---

## 1. 責任邊界（承襲 MA-0）

| 面向 | Domain Core（Soul OS） | Adapter（唯一耦合層） | DSH（ephemeral） |
|---|---|---|---|
| Work 生命週期 | `state_machine.py`（10 state、2 Human approval） | 把 Work 狀態投影成 execution request | 不認識 WorkState |
| durable store | `store.py` + `persistence.py`（append-only JSONL） | 讀 durable truth 重建 execution | session log 只是 audit sidecar |
| 寫入權 | kernel 唯一 writer（store-level 強制） | 只讀不寫 durable state | 只讀不寫 durable state |
| 委派執行 | 無（domain orchestration 不建 subagent） | 把 execution request 轉成 subagent/workflow 呼叫 | subagent/workflow 執行 |
| Human Approval | `authority.py`（Approval→Grant→Action） | 實作 `HumanAuthorityPort.authenticate` | 無（DSH approval 是 sandbox 軸） |
| resume | `resume_state` + `AuthorityManager.resume()` | 讀 resume_state 重掛 execution | 無 restart-safe orchestration |

---

## 2. Domain Core → DSH（outbound）

### 2.1 Work / role / capability / execution request 怎麼送進 DSH

Domain Core 不直接呼叫 DSH。它透過 **bridge message**（`src/work/bridge.py`）發出 capability-neutral 的 execution request，Adapter 把 request 轉成 DSH 呼叫。

```text
Domain Core（WorkKernel / WorkflowOrchestrator）
        │
   BridgeMessage（request，capability-neutral）
        │
   Adapter（唯一 import DSH types 的地方）
        │
   DSH subagent / workflow / goal
```

- `role`（chief/developer/tester/auditor）→ Adapter 映射到 subagent + agent-preset
- `capability`（capability-neutral，如 `git.commit`）→ Adapter 映射到 DSH tool
- `work.assign` → Adapter 映射到 subagent spawn/fork
- 多階段工作 → Adapter 映射到 workflow
- 自動續輪 → Adapter 映射到 goal（但 goal 只負責 execution state，見 §6）

### 2.2 Chief / Specialist 如何建立

Chief / Specialist 是 **DSH subagent**（role + agent-preset），由 Adapter 建立：

```text
Adapter
  ├── Chief subagent（role=chief，preset=chief）
  ├── Developer subagent（role=developer，preset=developer）
  ├── Tester subagent（role=tester，preset=tester）
  └── Auditor subagent（role=auditor，preset=auditor）
```

role 存 Soul 自己的 role（`Role` enum），不存 DSH agent/session id（2A §8.2）。Adapter 在執行時才把 role → 具體 DSH subagent 解析。

### 2.3 tool invocation 如何映射

capability-neutral 名稱 → DSH tool 的映射表由 Adapter 持有（可演化）：

```text
git.commit → bash/git tool
git.push   → bash/git tool
deploy     → （未來）deploy tool
...
```

映射表是 Adapter 的實作細節，不是 Domain Core 的 contract。Domain Core 只發 capability-neutral 的 request。

---

## 3. DSH → Domain Core（inbound）

### 3.1 execution result / evidence / failure

DSH 執行完後，Adapter 把結果轉回 **HandoffResult**（2A §6 結構化 result，非 chat transcript）：

```text
DSH subagent 完成
        │
   Adapter 轉成 HandoffResult（artifact / evidence / decision）
        │
   WorkflowOrchestrator.consume_handoff()
        │
   WorkEvent log（durable）
```

- artifact → `artifact_produced` event
- evidence → `evidence_produced` event
- decision → `decision_made` event
- **不得有 approval**（Human Approval 走獨立 path，2A §6）

### 3.2 session termination

DSH session 結束（正常/異常）時，Adapter 記錄 termination，但不把 DSH session 當 durable truth。DSH session 只是 execution 的 audit sidecar（2D §3）。

### 3.3 causation / reference

BridgeMessage 的 `causation`（Soul causal truth，event_id）與 `reference`（DSH sessionId，非 causal truth）必須維持區分（migration plan §3.2）。Adapter 不得讓 DSH sessionId 變成 Soul identity / causal truth。

---

## 4. Authorization seam

### 4.1 HumanAuthorityPort 由誰實作

`HumanAuthorityPort.authenticate(context)` 由 **Adapter / runtime integration** 實作（2C identity seam）。Domain Core 不自己認證 Human，只委派注入的 port。

```text
Human → Trusted Authority Boundary（Adapter 實作）→ HumanAuthorityContext → AuthorityManager.grant()
```

在 Adapter 落地前，deny-by-default 意味著任何 privileged action 都無法執行（正確的安全預設）。

### 4.2 DSH approval/sandbox 為什麼不能取代 Soul capability authority

DSH 的 approval/sandbox 是 **執行權限**（sandbox 軸：file effects、delegated policy 把 child approval 釘 `'never'`）。Soul 的 Human Approval 是 **authority gate**（capability 軸：Approval→Grant→Action provenance chain）。

兩者不得混用。DSH 的 `'never'` 不得被誤讀為 Soul 的 deny-by-default Human authority。

---

## 5. Resume / Recovery

### 5.1 crash/restart 後誰是 source of truth

**Domain Core 的 durable log（WorkEvent + AuthorityEvent）是唯一 source of truth。** DSH session / subagent / workflow / goal 全是 ephemeral，重啟即失。

### 5.2 如何從 WorkEvent / authority log 重建 DSH execution

```text
restart
  ↓
Domain Core 讀 durable log（WorkEvent + AuthorityEvent）
  ↓
fold 出 canonical state（WorkObject + approval/grant registry）
  ↓
Adapter 讀 resume_state
  ↓
re-mount fresh DSH subagents / workflows
  ↓
new execution
```

### 5.3 不依賴 DSH session 的 child discoverability

DSH continuable child 的 cold resume 依賴 session persistence，且 one-shot child 完成後不可發現。**Adapter 的 resume 不能依賴 DSH 的 child 可發現性，只能依賴 Soul 自己的 durable store。**

---

## 6. Scheduling

### 6.1 DSH Goal 只負責 execution state

DSH goal 是「state, not scheduling」（MA-0 發現）。它不決定「下一輪是否應該發生」。

### 6.2 Soul durable scheduler 決定下一輪

Soul OS 的 durable scheduler（`src/soul/scheduler.py`）主導續輪。DSH goal 只承載 execution state，續輪由 Soul 的 scheduler 決定。

```text
Soul durable scheduler（決定下一輪）
        │
   Adapter（把續輪 request 轉成 DSH goal 的 resume mutation）
        │
   DSH goal（execution state）
```

---

## 7. Metering

### 7.1 token / cost / execution budget 放哪一層

DSH workflow 無 token-budget vocabulary（只 cap concurrency/items/children）。**token/cost/execution budget 由 Soul OS 側（或 Adapter）自建 metering 層。**

### 7.2 不讓 DSH workflow 能力限制變成 Soul policy

DSH workflow 的能力限制（foreground only、no journaling、no nested workflow）是 **DSH 的實作限制**，不是 Soul 的 policy。Soul 的 policy（capability authorization、approval gate）由 Domain Core 定義，不得被 DSH 的能力限制反向滲透。

---

## 8. No-DSH Survival

拔掉 DSH 後，Domain Core 仍能：
- 讀取 durable state（WorkEvent + AuthorityEvent log）
- 恢復 canonical state（fold）
- 驗證 authorization（is_authorized 只讀 canonical registry）

DSH 消失時，execution 消失，但 durable truth + authority 仍在。這是 No-DSH Survival Test 的落點。

---

## 9. Object / API graph

### 9.1 哪些 Domain objects 可以進 Adapter

- `BridgeMessage`（language-neutral message）
- `HandoffResult`（結構化 result）
- `WorkObject` / `WorkEvent`（durable truth 的投影）
- `HumanAuthorityContext`（authenticated context）
- `resume_state`（最小重建狀態）

### 9.2 哪些 DSH objects 絕對不能滲透回 Domain Core

- DSH session id（只能作 `reference`，不能作 `causation` / identity）
- DSH subagent / workflow / goal 的內部 object
- DSH tool 的具體名稱（Domain Core 只用 capability-neutral 名稱）
- DSH approval / sandbox policy（不能取代 Soul capability authority）

**Domain Core 的 `src/work/` 全模組零 DSH import，這是永久鎖死的 boundary。**

---

## 10. Boundary contract（可實作）

1. **Adapter 是唯一 import DSH types 的地方**（2A §7）。
2. **Domain Core 零 DSH coupling**（`src/work/` 全模組零 DSH import）。
3. **single-writer store-level**：WorkStore / AuthorityStore 在 store 層檢查 writer。
4. **HumanAuthorityPort seam**：Adapter 實作 authenticate，Domain Core 只委派。
5. **resume 只依賴 durable log**：不依賴 DSH session / child discoverability。
6. **DSH goal 只負責 execution state**：Soul durable scheduler 決定續輪。
7. **metering 由 Soul OS 側自建**：不讓 DSH workflow 能力限制變成 Soul policy。
8. **causation / reference 區分**：DSH sessionId 只作 reference，不作 causal truth。
9. **No-DSH Survival**：拔掉 DSH 後 Domain Core 仍能讀取/恢復/驗證 durable state。
10. **DSH approval/sandbox ≠ Soul capability authority**：兩軸分開映射。

---

*本文件為 DSH-MA-1 Adapter Boundary Design，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
