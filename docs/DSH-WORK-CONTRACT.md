# DSH Work Contract & Execution Boundary Design（Phase 2A）

**日期**：2026-08-23
**狀態**：Phase 2A — GO（Owner 授權 2026-08-23；前置設計，非 Soul OS milestone，不建立 ticket）
**範圍**：定義 Work / Artifact / Evidence / Decision / Approval / Resume 的 contract，以及 DSH Adapter 的 execution boundary。
**上游**：[`docs/DSH-SOUL-OS-MIGRATION-PLAN.md`](DSH-SOUL-OS-MIGRATION-PLAN.md)、Notion「DSH Multi-Agent Architecture 前置設計計畫」。

---

## 0. North Star

> **DSH orchestration ≠ Soul orchestration。**
> DSH 負責「現在怎麼把工作跑起來」；Soul OS 負責「為什麼做、要做什麼、記住什麼、如何形成連續性」。

Work Contract 是 Soul OS 與 DSH 之間唯一的 portability seam。DSH 只是其中一個 execution backend，未來可被 API / local agent / cloud agent 等 backend 替換。

---

## 1. Canonical Invariants（不可破）

1. **Work state governs lifecycle; capability policy governs authority.**
   - Work state machine 只管「這個 Work 的生命週期走到哪」。
   - Capability policy 只管「Agent 現在被允許做什麼」。
   - 兩者正交，不得互相污染。

2. **No agent may manufacture, infer, or substitute a Human Approval.**
   - Approval 只能由 Human 產生。
   - Agent 只能產生 Decision（自主選擇的 audit record），不能偽造、推斷或替代 Approval。

---

## 1.5 施工紀律（Owner 拍板）

Phase 2A 只把「Soul OS 如何描述 Work」這個 domain contract 定死，不是把整個 DSH multi-agent system 做完。

施工時若發現「這個 DSH API 可以這樣做」，**不得因此改變 Work Contract**。正確方向是：

```text
Soul OS Work Contract
        ↓
DSH Adapter
        ↓
DSH implementation
```

而不是：

```text
DSH API
   ↓
反推 Soul OS schema
```

---

## 2. 三層架構

```text
Soul OS (Identity / Memory / Inner Life / World / Agency)
        │
   Work Interface  ← 本文件定義的 contract
        │
   DSH Adapter     ← 我們自己的 seam（唯一 import DSH types 的地方）
        │
   DSH Runtime (subagents / workflow / goal / presets / session)
```

---

## 3. Work Object Schema

versioned、JSON-serializable、**不得引用任何 DSH type / id**。

```jsonc
{
  "schema_version": "1.0",
  "work_id": "uuid",
  "objective": "string",
  "state": "WorkState",  // enum；合法值與 transition 以 §4 為唯一 authoritative
  "owner": "role",              // chief | developer | ... | soul_identity（未來）
  "assigned_agents": ["role"],  // 角色，不是 DSH agent/session id
  "artifacts": [],
  "evidence": [],
  "decisions": [],
  "approvals": [],
  "dependencies": ["work_id"],
  "provenance": {},
  "resume_state": {}
}
```

### 3.1 四種 result 的 authority（不可混）

| 欄位 | 是什麼 | 誰產生 | 生命週期 |
|---|---|---|---|
| `artifacts[]` | 內容產出（code/doc/test 檔） | Specialist | 內容定址、可 hash、可回滾 |
| `evidence[]` | 驗證證明（test 過、review 過） | Tester/Auditor | 指向 artifact + 結論 |
| `decisions[]` | agent 的自主選擇 | 任何 agent | 只記錄、供 audit，不 gate |
| `approvals[]` | 人類的明確授權 | Human | gate 邊界，不可被 agent 偽造 |

關鍵語意：**Decision 是「我選了這個」，Approval 是「人類准了這個」**。authority 完全不同，只有 Approval 能 gate 高影響 action。

### 3.2 `resume_state`（persistence crux）

DSH 的 jobs / subagent / workflow 都是 in-process，重啟就沒。所以 `resume_state` 不能是「DSH session snapshot」，而要是**最小重建狀態**：

```jsonc
{
  "current_phase": "in_progress",
  "pending_handoffs": ["developer", "tester"],
  "last_artifact_refs": ["sha256:..."],
  "idempotency_keys": ["work-123:phase-2"]
}
```

DSH Adapter 重啟後用 `resume_state` 重新把 Work 掛回 DSH，不假設 in-process 狀態存活。

### 3.3 `provenance`

每個 artifact / evidence / decision / approval 都帶 provenance：

```jsonc
{
  "role": "developer",          // 誰
  "capability": "git.commit",    // 用什麼能力（capability-neutral，非 DSH tool 名）
  "timestamp": "iso8601",
  "input_refs": [],              // 消費了哪些 artifact/evidence
  "output_refs": []              // 產出了哪些 artifact/evidence
}
```

---

## 4. Layer 1 — Work State Machine

`WorkState` 是 enum，本節是合法值與 transition 的**唯一 authoritative source**。任何未列於此的 state（如 `reviewing` / `failed` / `waiting`）都是非法。

```text
proposed
   │  Human approval #1
   ▼
approved
   │
   ▼
assigned
   │
   ▼
in_progress
   │
   ▼
awaiting_review
   │
   ▼
awaiting_approval
   │  Human approval #2
   ▼
done
```

終態：`rejected` / `cancelled` / `done`。

`blocked` 是 **non-terminal**（可恢復）：任何 active state 都可能進入 `blocked`，解除阻塞後 resume 回 `resume_state.current_phase` 指定的 target state（通常是 `in_progress` 或 `awaiting_review`）。

**只有兩個 transition 需要 Human approval**：
- `proposed → approved`（開工前）
- `awaiting_approval → done`（commit/push/deploy 前）

其餘（assigned、in_progress、awaiting_review、blocked → resume）都是 autonomous。

---

## 5. Layer 2 — Capability Authorization

### 5.1 Role → Capability 矩陣

| Role | Capabilities |
|---|---|
| Researcher | workspace.read, research, artifact.create |
| Developer | workspace.read, isolated.write, test.execute, git.branch, artifact.create |
| Tester | workspace.read, test.execute, evidence.create |
| Auditor | workspace.read, review, evidence.create |
| Chief | orchestration, decision, work.assign |
| Human | approval, privileged actions |

> **Contract change（2026-08-23，DSH-DEV-ENV-0 §0.5，Owner 拍板）**：Developer 列
> 加入 `artifact.create`（修復 2A §5.1 / 2B §5 / 實務三處不一致——2B §5 明說
> developer 對 artifact store 是 write）。Developer 產 text artifact 自此合法；
> 與 `src/work/roles.py` 的 `ROLE_CAPABILITIES` 同步（code↔doc 不許產生新不一致）。

### 5.2 高風險 capability（需 approval / policy gate）

```text
production.write
git.commit
git.push
deploy
external.publish
```

### 5.3 isolated worktree vs production workspace

```text
isolated worktree
    └── autonomous write ✅

production workspace
    └── write ❌
         └── explicit approval → temporary capability
```

這是**安全邊界**，不是流程規則。它讓「Developer 修改 production source」不必塞進 Work state machine，而是由 capability policy 在 action 層 gate。

---

## 6. Handoff Protocol

Specialist 回傳結構化 result，**不是 chat transcript**。

```jsonc
{
  "work_id": "uuid",
  "role": "developer",
  "result_type": "artifact | evidence | decision",
  "artifact_refs": [],   // 內容定址 ref
  "evidence_refs": [],
  "decision": {},
  "status": "done | blocked | needs_input",
  "resume_hint": {}
}
```

`result_type` 只能是 `artifact` / `evidence` / `decision`。**不得有 `approval`**——Human Approval 透過獨立的 Human authority path 寫入 `approvals[]`，不是 Specialist 的 result。

Chief 不需要知道 Specialist 的內部實作，只需要：assign Work → Specialist produce Artifact/Evidence → update Work → Chief consume result。

---

## 7. DSH Adapter Mapping Boundary

DSH Adapter 是唯一 import DSH types 的地方。mapping：

| Work Contract | DSH |
|---|---|
| role | subagent + agent-preset |
| work.assign | subagent spawn / fork |
| 多階段工作 | workflow |
| 自動續輪 | goal |
| session trace | dsh-session |
| capability | DSH tool（bash / fs / git...） |
| resume_state | 重掛 subagent / workflow（不假設 in-process 存活） |

---

## 8. Non-negotiables

1. Work Contract 不得引用 DSH type / id。
2. `owner` / `assigned_agents` 存 role，不存 DSH id。
3. Approval 只能由 Human 產生。
4. 四種 result authority 不得混。
5. `resume_state` 是最小重建狀態，不是 DSH session snapshot。
6. capability 名稱是 capability-neutral，不是 DSH tool 名。
7. Handoff Protocol MUST NOT permit an agent-generated approval result。

---

## 9. Out of Scope（後續 Phase）

- **Phase 2B** — Workspace / Git / Worktree 詳細設計（shared workspace、isolated workspace、branch/worktree 策略、artifact storage）。
- **Phase 2C** — Human Approval 詳細流程（approval 的呈現、撤銷、時效、audit）。
- **Phase 2D** — Persistence（restart / crash recovery / resume / orphaned jobs / goal continuation）。

---

## 10. Phase 2A Contract Gate

Phase 2A implementation/documentation 完成後，**不要直接進 2B**。先做一次 Contract Gate，確認：

1. schema 是否真的不含 DSH-specific type/id
2. state transition 是否沒有偷偷混入 capability authorization
3. approval 是否只能由 Human authority 產生
4. handoff 是否可以脫離 chat transcript 重建
5. resume_state 是否足以重新掛回 execution backend
6. DSH coupling 是否只存在 Adapter
7. 2B / 2C / 2D 是否沒有被提前實作

Gate 通過後，才正式進 **Phase 2B — Workspace / Git / Worktree Design**。

---

*本文件為 Phase 2A 前置設計，供 Owner 拍板。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
