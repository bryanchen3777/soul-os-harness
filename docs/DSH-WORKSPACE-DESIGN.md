# DSH Workspace / Git / Worktree Design（Phase 2B）

**日期**：2026-08-23
**狀態**：DESIGN — 前置設計，NOT AUTHORIZED（待 Owner 拍板）
**上游**：[`docs/DSH-WORK-CONTRACT.md`](DSH-WORK-CONTRACT.md)（Phase 2A，CONTRACT ACCEPTED）

---

## 0. 目的

Phase 2B 定義 DSH multi-agent 環境的 workspace / git / worktree 拓撲，讓 Developer / Tester / Auditor 能安全並行，且 production code 的修改受 2A 的 capability authorization 保護。

---

## 1. 核心原則（承襲 2A §5.3）

```text
isolated worktree
    └── autonomous write ✅

production workspace
    └── write ❌
         └── explicit approval → temporary capability
```

這是**安全邊界**，不是流程規則。

---

## 2. Workspace 拓撲

三種 workspace：

| Workspace | 用途 | 寫入權限 |
|---|---|---|
| production workspace | canonical repo（main branch） | read-only for agents；write 需 approval |
| isolated worktree | per-Work 的 git worktree | Specialist 自主 write |
| shared artifact store | 內容定址的 artifact / evidence | 所有 agent 可讀；producer 可寫 |

---

## 3. Git 策略（核心決策：一個 Work → 一個 isolated worktree）

**一個 Work → 一個 isolated worktree，所有 Specialist 共享該 worktree，capability 決定 read/write。** 不是每個 Specialist 一個 worktree——那會迫使 Developer 為了交接而先 commit，引入不必要的 fetch / sync / uncommitted-changes 處理。

- 每個 Work 一個 branch：`work/<work_id>`。
- 該 branch 上開**一個** git worktree，Specialist 共享。
- 合併回 production 走 PR / merge，需 approval（`git.commit` / `git.push` 是高風險 capability）。

```text
main (production, read-only)
   │
   └── work/<work_id> (one isolated worktree, shared)
          ├── Developer  → write
          ├── Tester     → read + execute
          └── Auditor    → read + inspect
```

---

## 4. Artifact 模型

**Workspace = working state；Artifact Store = durable/result state。兩者不要混。**

Artifact Store 是 **evidence / result exchange layer**，不是 workspace 的替代品。

```text
Work Workspace (working state)
├── source changes
├── generated files
├── tests
└── build output
       │
       ▼
Artifact Store (durable/result state)
├── patch
├── test report
├── audit report
└── evidence
```

**關鍵：Artifact / Evidence 是交接 contract；Git commit 不是交接 contract。** Developer 不需要為了交接而先 commit——Tester / Auditor 直接測 / audit 同一個 working state。

- 內容定址（sha256）。
- evidence 指向 artifact hash。

```text
artifact = { hash, type, path, producer_role, provenance }
evidence = { artifact_hash, verdict, producer_role, provenance }
```

artifact 的 `type` 不限定為 patch，可以是：source snapshot / patch / test output / build output / generated file / report。Tester 的 evidence 直接描述「tested artifact sha256:xxxx against worktree state, verdict=PASS」，不把 Work Contract 偷偷變成 Git patch workflow。

---

## 5. 權限矩陣（承襲 2A §5）

| Role | production | isolated worktree | artifact store |
|---|---|---|---|
| Developer | read | write ✅ | write |
| Tester | read | read | write (evidence) |
| Auditor | read | read | write (evidence) |
| Chief | read | read | read |
| Human | approval | — | — |

**Non-negotiable：Workspace sharing MUST NOT imply capability sharing.**

共享的是 state，不是 authority。Developer 可以 write，不代表 Tester / Auditor 自動取得 write。所有 filesystem / tool actions 仍受 invoking role 的 capability policy 約束，即使 agent 在同一個 Work worktree 內操作。

---

## 6. Flow

```text
Chief assign Work
   ↓
Developer (shared worktree, write) → artifact (patch)
   ↓
Tester (shared worktree, read+execute) → evidence (test result)
   ↓
Auditor (shared worktree, read+inspect) → evidence (review)
   ↓
Chief synthesis → awaiting_approval
   ↓
Human approval → merge to production (git.commit / git.push)
```

---

## 7. Out of Scope

- **Phase 2C** — Human Approval 詳細流程（approval UI、撤銷、時效、audit）。
- **Phase 2D** — Persistence（restart / crash recovery / resume）。

---

*本文件為 Phase 2B 前置設計，供 Owner 拍板。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
