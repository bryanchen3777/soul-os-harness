# 工單：MVP-5-R2 — Registry Boundary + Exact-Action Authorization

**日期**：2026-08-23
**性質**：MVP implementation（corrective work，可寫 code）
**上游**：`logs/DSH-MVP-5-R1-WORK-ORDER.md`（R1 BLOCKED）、MVP-5-R1 FINAL REVIEW（2 條殘留 forgery）

---

## 0. 背景

MVP-5-R1 被 FINAL REVIEW 判 BLOCKED，2 條殘留 forgery：
- **Forgery A**：`_approvals`/`_grants` 是裸 dict，非-Human caller 持 manager 參考可塞入「內部一致」的偽造 approval+grant 對，`is_authorized()` 回傳 True。
- **Forgery B**：`is_authorized` 不查 `action.action` vs `approval.requested_action`，agent 拿合法 grant 可把 action 換成未批准目標仍放行。

## 1. Registry Boundary（核心 invariant）

**不要依賴 MappingProxyType 作為 security boundary**（它只能防 `mgr._grants[id] = forged`，但 `_grants` 仍是 manager 的可達 attribute）。**不要把「不可偽造 marker」當成主要安全機制**。**不要把目標寫成「Python 真 private」——做不到，也沒必要。**

真正的 invariant：

> **Caller never supplies or controls the canonical Approval / CapabilityGrant registry used for authorization.**

```text
AuthorityManager
 ├── canonical approval/grant state
 │      ↓
 │   manager-controlled storage
 ├── grant()
 │      ↓
 │   only manager creates CapabilityGrant
 └── is_authorized(action)
        ↓
   lookup canonical grant
        ↓
   validate: approval provenance / capability / role / work_id /
             requested_action / scope_constraints / expiry / revocation / consumption
```

**測試 invariant**（adversarial mutation / reflection-style probing，不是只測正常 API）：

> external caller obtains AuthorityManager → cannot inject forged approval/grant → cannot replace canonical registry entries → cannot make `is_authorized() == True`.

## 2. Exact-Action Authorization（requested_action 必須進入 decision）

`requested_action` 不是可選項。現在 `Approval(capability=git.push, requested_action={repo=A, branch=main})` 不能拿同一個 grant 去做 `git.push repo=B branch=production`。

```text
AgentAction
   ↓
CapabilityGrant
   ↓
Approval
   ↓
requested_action
   ↕
actual action
```

不匹配 = DENY。**這是 structural comparison，不是只比較 capability。**

## 3. Hard Gate（比 R1 再高一層）

- 非-Human caller 不得建立可被 `is_authorized()` 接受的 chain。
- **No caller-controlled mutable state may alter the canonical authorization decision.**
- **A valid grant authorizes only the exact bounded action declared by its governing Approval; capability equality alone is insufficient.**

## 4. 範圍

- 修改 `src/work/authority.py`（registry encapsulation + requested_action enforcement）。
- 修改 `tests/test_work_authority.py`（adversarial forgery 測試）。

## 5. 驗收（完成的定義）

- `pytest tests/test_work_authority.py` 全過 + 全回歸綠。
- 測試 invariant：external caller 無法 inject/replace/mutate registry 使 `is_authorized() == True`。
- `is_authorized` 做 structural comparison（requested_action / scope_constraints 進入 decision）。

## 6. R2 Final Review 必須重做的 attack list（13 條）

1. registry injection attack
2. registry replacement attack
3. forged Approval
4. forged Grant
5. forged AgentAction
6. legitimate grant + altered requested action
7. legitimate grant + altered work_id
8. legitimate grant + altered role
9. legitimate grant + altered capability
10. revoked grant replay
11. consumed single-action replay
12. expired production.write
13. cross-work grant reuse

**任何一條能讓未授權 action 得到 True，直接 BLOCK。**

## 7. 不做（Out of Scope）

- 不實作 HumanAuthorityPort 的真實認證（屬未來 DSH Adapter）。
- 不建 DSH subagent、不實作 LLM、不建 approval UI。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py`（MVP-1/2/3/4 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 封閉了哪些 forgery（registry injection / replacement + requested_action）
- 新增了哪些 adversarial 測試
- 完整回歸結果
- 確認 13 條 attack 全部 deny
- 剩餘 architectural concerns
