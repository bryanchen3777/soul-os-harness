# DSH Persistence / Recovery / Resume Design（Phase 2D）

**日期**：2026-08-23
**狀態**：DESIGN — 前置設計，NOT AUTHORIZED（待 Owner 拍板）
**上游**：[`docs/DSH-WORK-CONTRACT.md`](DSH-WORK-CONTRACT.md)（2A）、[`docs/DSH-WORKSPACE-DESIGN.md`](DSH-WORKSPACE-DESIGN.md)（2B）、[`docs/DSH-HUMAN-AUTHORITY.md`](DSH-HUMAN-AUTHORITY.md)（2C）

---

## 0. 目的

Phase 2D 定義 **durable truth 與 DSH ephemeral state 的邊界**，以及 DSH restart / crash 後的 recovery / resume。這是前置架構的最後一塊核心。

---

## 1. 核心原則

> **Soul OS owns the durable work truth. DSH owns ephemeral execution.**

```text
Work state / handoffs / artifacts / evidence / decisions / approvals / capability grants / execution progress
        ↓
   DURABLE STATE（Soul OS owns）
        ↓
   DSH restart / crash
        ↓
   resume_state
        ↓
   DSH Adapter
        ↓
   new execution
```

---

## 2. Durable vs Ephemeral 邊界

| Durable（Soul OS owns） | Ephemeral（DSH owns） |
|---|---|
| Work state（`WorkState`） | DSH session log |
| Work object（objective / owner / assigned_agents / dependencies） | DSH jobs（in-process） |
| artifacts / evidence（內容定址 ref） | subagent Activation（in-process） |
| decisions（audit） | workflow run state（in-process） |
| approvals（immutable） | in-flight tool calls |
| capability grants（immutable） | |
| resume_state（最小重建狀態） | |

**DSH session / jobs / subagent Activation / workflow run 都不是 durable store。** 它們是 ephemeral execution state，重啟即失。

---

## 3. Durable State 模型

Soul OS 的 durable Work store 是 append-only event log（`WorkEvent`）：

```text
WorkEvent = {
  work_id,
  event_type,     # state_transition | artifact_produced | evidence_produced
                  # | decision_made | approval_granted | grant_issued
  payload,
  timestamp,
  provenance
}
```

Current Work state = fold(events)。DSH session 不是 durable store，只是 execution 的 audit sidecar。

---

## 4. resume_state

`resume_state` 是從 durable truth 推導的**最小重建狀態**（不是 DSH session snapshot）：

```text
resume_state = {
  current_phase,        # 現在卡在哪個 phase
  pending_handoffs,     # 哪些 specialist 還沒回
  last_artifact_refs,   # 最後產出的 artifact 指針
  idempotency_keys      # 避免重啟後重複執行
}
```

---

## 5. Recovery Flow

```text
DSH restart / crash
   ↓
Soul OS durable store（unchanged）
   ↓
DSH Adapter reads resume_state
   ↓
re-mount Work onto fresh DSH subagents / workflows
   ↓
new execution
```

DSH Adapter 重啟後用 `resume_state` 重新掛回 DSH，**不假設 in-process 狀態存活**。

---

## 6. Non-negotiables

1. **Soul OS owns the durable work truth. DSH owns ephemeral execution.**
2. DSH session / jobs / subagent Activation / workflow run 都不是 durable store。
3. `resume_state` 是最小重建狀態，不是 DSH session snapshot。
4. 重啟後 DSH Adapter 從 `resume_state` 重掛，不假設 in-process 狀態存活。
5. approvals / capability grants 是 immutable durable records。

---

## 7. Out of Scope

- **DSH Multi-Agent MVP 實作**（2A + 2B + 2C + 2D 的 Architecture Contract Gate 通過後才開始）。

---

*本文件為 Phase 2D 前置設計，供 Owner 拍板。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
