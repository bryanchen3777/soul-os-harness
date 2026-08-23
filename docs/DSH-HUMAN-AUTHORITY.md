# DSH Human Authority Design（Phase 2C）

**日期**：2026-08-23
**狀態**：DESIGN — 前置設計，NOT AUTHORIZED（待 Owner 拍板）
**上游**：[`docs/DSH-WORK-CONTRACT.md`](DSH-WORK-CONTRACT.md)（Phase 2A）、[`docs/DSH-WORKSPACE-DESIGN.md`](DSH-WORKSPACE-DESIGN.md)（Phase 2B）

---

## 0. 目的

Phase 2C 把 2A 的「Approval = Human authority」落成可執行的安全模型：Human Approval 到底授權什麼、範圍多大、可否撤銷、多久有效，以及 approval 與 capability grant 如何建立一對一的 provenance chain。

---

## 1. 核心原則

**Approval 授權的是 capability + bounded action scope，不是「允許工作」。**

```text
Work
 │
 ├── Agent autonomous work
 │
 └── Human Approval
        ↓
   Capability Grant
        ↓
   Specific Action
```

而不是：

```text
Human → 「我批准這個 Work」 → Agent 想做什麼都可以
```

後者會破壞 2A 的 authority boundary。

---

## 2. Approval Schema

```jsonc
{
  "approval_id": "uuid",
  "work_id": "uuid",
  "capability": "git.commit | git.push | deploy | external.publish | production.write | ...",
  "requested_action": {          // 具體 action / context，不是模糊的「批准 git.push」
    "repository": "soul-os-harness",
    "branch": "main"
  },
  "action_scope": "single_action | work_scoped",
  "work_scope": "work_id",
  "grantee_role": "developer | chief",
  "granted_by": "human_identity",
  "granted_at": "iso8601",
  "expires_at": "iso8601 | null",   // null 僅限低風險；高風險必須有 expiry
  "revoked_at": "iso8601 | null",
  "scope_constraints": {}            // 額外約束
}
```

Human 批准的是「這個 capability 對這個具體 action/context 的授權」，不是模糊的「批准 git.push」。

---

## 3. Granularity（三種 scope，非互斥）

| scope | 語意 | 適用 |
|---|---|---|
| `single_action` | 一次 approval = 一次 action，用完即失效 | deploy / external.publish / production mutation / 不可逆 / 高外部影響 |
| `work_scoped` | 對該 Work **預先宣告**的一組 capability/action set | git.commit / git.push（可回滾） |
| `time_windowed` | **temporal constraint**，與 scope 組合，不是另一種 authority | production.write（必須 time-bounded） |

關鍵：`work_scoped` 不是「批准 Work → 任何 capability 都可用」，而是「批准 Work 的預先宣告 capability set」。

```text
Work #123
  requested: [git.commit, git.push]
  Human approves: work_id=#123, capabilities=[git.commit, git.push]
  （不是：approve Work #123 → Developer 未來任何 capability 都可以用）
```

`time_windowed` 是 scope constraint，可與其他 scope 組合：

```text
scope = work_scoped, expires_at = 20:30
scope = single_action, expires_at = 20:35
```

---

## 4. Expiry 規則

高風險 capability 不允許 indefinite approval。

| capability | 建議 expiry |
|---|---|
| git.commit | work-scoped / until action set consumed |
| git.push | work-scoped / preferably one-shot |
| production.write | **time-windowed 必須有 expiry** |
| deploy | single-action + expiry |
| external.publish | single-action + expiry |

**`production.write` 的 `expires_at = null` = invalid approval**（production capability 一旦洩漏，就變成永久 authority）。

---

## 5. Revocation 規則

- Revocation **立即阻止新的 privileged action**。
- 已進入不可安全中斷階段的 atomic action 可以完成。
- 可安全中斷者應停止。

Canonical rule：

> **Revocation is effective immediately for authorization of new actions; interruption of an already-started action is governed by that action's atomicity and safe-cancellation semantics.**

```text
git push（atomic）：
  push started → revoked → push completes ✅

deploy（30-min rollout）：
  revoked → new privileged steps blocked
         → if safely interruptible → stop
         → if atomic / non-interruptible → complete current atomic unit
         → record outcome
```

---

## 6. Provenance Chain（一對一）

```text
Human Approval (approval_id)
   ↓
Capability Grant (grant_id → approval_id)
   ↓
Agent Action (action_id → grant_id)
   ↓
Evidence (evidence_id → action_id)
```

每個 privileged Agent Action 必須有**且只能有一個** governing approval/grant。任何一段斷掉：

```text
Agent Action → NO VALID GRANT → authorization failure
```

不是「找不到 approval，但可能之前有人同意過」——**不能猜**。這是 2A invariant #2 的自然延伸：No agent may manufacture, infer, or substitute a Human Approval.

---

## 7. 四層 authority model

```text
HUMAN → Approval → Capability Grant → Agent Action → Evidence
```

三個東西完全分開：

| 層 | 決定什麼 |
|---|---|
| Work State | 「現在是否到了可以要求 / 消費 approval 的 lifecycle」 |
| Capability Policy | 「這個 role 能不能執行這個 action」 |
| Human Approval | 「這個具體 action 是否被授權」 |

```text
Work State ≠ Capability Policy ≠ Human Approval
```

---

## 8. Non-negotiables

1. Approval 授權的是 capability + bounded action scope，不是「允許工作」。
2. `work_scoped` 只能涵蓋預先宣告的 capability/action set。
3. `production.write` 必須 time-bounded（`expires_at = null` 是 invalid approval）。
4. Revocation 立即阻止新 action；in-flight 依 atomicity / safe-cancellation 決定。
5. **Every privileged Agent Action MUST have exactly one valid governing Capability Grant traceable to one Human Approval.**
6. 斷鏈 = authorization failure，禁止推斷 approval。
7. **A Human Approval grants only the explicitly declared capability/action scope; the approved scope is immutable and may not be expanded by any agent or downstream execution layer.**

---

## 9. Out of Scope

- **Phase 2D** — Persistence（approval / grant 的持久化、restart 後恢復、orphaned grant 清理）。

---

*本文件為 Phase 2C 前置設計，供 Owner 拍板。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
