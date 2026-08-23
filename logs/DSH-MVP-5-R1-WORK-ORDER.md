# 工單：MVP-5-R1 — Human Authority Boundary 修復（identity seam + forgery hardening）

**日期**：2026-08-23
**性質**：MVP implementation（corrective work，可寫 code）
**上游**：`logs/DSH-MVP-5-WORK-ORDER.md`（MVP-5 BLOCKED）、MVP-5 FINAL REVIEW（forgery discovered = YES）

---

## 0. 背景

MVP-5 被 FINAL REVIEW 判 BLOCKED：Human Authority 目前是 convention（self-attested 字串 + 可變物件），不是 enforcement。四條 forgery：
1. `Approval(granted_by="human")` + `grant(approval, "human")` 可被任意 agent 建立合法 chain
2. `is_authorized` 信任 caller 傳入的 grant，可手造 grant 擴張 scope
3. model 無 frozen，`_approvals`/`_grants` 可外部 mutate
4. single_action 無 consume()，消費靠 caller 手動設旗標

## 1. Identity Boundary 設計（本工單的核心 seam）

**AuthorityManager 不自己「認證 Human」**。它只接受一個外部提供、不可由 Agent 自己製造的 Human Authority Context，並透過 port 驗證。

```text
HUMAN
  │
  ▼
Trusted Authority Boundary（未來 DSH Adapter / runtime integration 實作）
  │
  │ authenticated human context
  ▼
AuthorityManager.grant(context, approval)
  │
  ▼
CapabilityGrant
```

**關鍵區分**：`human_identity` 可以是資料，但 `human_identity ≠ proof of human authority`。不要把 `granted_by="human"` 當 authentication。

### 1.1 `HumanAuthorityContext`（frozen）

```python
class HumanAuthorityContext(BaseModel):
    model_config = ConfigDict(frozen=True)
    identity: str            # human identity（資料，非 proof）
    authority_token: str     # authenticated handle（proof，由 trusted boundary 簽發）
    issued_at: datetime
    expires_at: datetime | None
```

### 1.2 `HumanAuthorityPort`（Protocol，seam）

```python
@runtime_checkable
class HumanAuthorityPort(Protocol):
    def authenticate(self, context: HumanAuthorityContext) -> bool: ...
```

由未來 Adapter / runtime integration 實作。**AuthorityManager 不產生 context、不實作認證**，只接受注入的 port 並委派驗證。

### 1.3 `AuthorityManager.grant(context, approval)`

- 簽名改為 `grant(approval, context)`：context 是 `HumanAuthorityContext`，不是 self-attested 字串。
- 驗證：`self._human_authority.authenticate(context)` 為 False → `NotHumanGrantorError`。
- 無注入 port（`_human_authority is None`）→ deny（deny-by-default）。
- 不寫死 `if actor == "human"`、不寫死 `if token == "..."`。

## 2. 10 條 Hard Invariants（不可破）

- **I1** Human authority cannot be self-attested.
- **I2** Approval must originate from a trusted Human Authority boundary.
- **I3** Caller-supplied Approval/Grant objects are never authoritative.
- **I4** CapabilityGrant is resolved by canonical grant_id.
- **I5** Grant ↔ Approval provenance must match exactly.
- **I6** Approval/Grant/Action authority state is immutable from caller perspective.
- **I7** production.write requires bounded expiry.
- **I8** single_action can be consumed exactly once.
- **I9** revoke immediately invalidates new authorization.
- **I10** broken provenance chain = DENY.

**任何一條能讓非-Human caller 最終得到 `is_authorized() == True` 的路徑，都 BLOCK。**

## 3. 四條 forgery 修復（同一張工單，不拆散）

1. **Human forgery**：`grant(approval, context)` 接受 `HumanAuthorityContext`，經 `HumanAuthorityPort.authenticate` 驗證；self-attested "human" 字串不再有效。
2. **Grant forgery**：`is_authorized(action)` 改為**只接受 action**，從 `_grants` 依 `action.grant_id` 取 canonical grant，逐欄比對 grant↔approval（capability / grantee_role / work_id / action_scope / expires_at）。caller 傳入的 grant 不再是 authoritative。
3. **Mutable objects**：`Approval` / `CapabilityGrant` / `AgentAction` / `HumanAuthorityContext` 全部 `frozen=True`。`revoke(approval_id)` / `consume(grant_id)` 是 controlled operation，產生新的 authoritative state，不允許 caller 直接 mutate（`grant.consumed = ...` / `approval.revoked_at = ...` 消失）。
4. **single_action consumption**：新增 `consume(grant_id)` 原子方法（atomic validation → mark consumed → 後續 authorization = DENY）。caller 不能手動設 `grant.consumed = True`。

## 4. 範圍

- 修改 `src/work/authority.py`（加 HumanAuthorityContext / HumanAuthorityPort / 改 grant / is_authorized / frozen / consume）。
- 修改 `tests/test_work_authority.py`（加 forgery 測試）。

## 5. 驗收（完成的定義）

- `pytest tests/test_work_authority.py` 全過 + 全回歸（authority + workflow + roles + contract + ports）綠。
- 10 條 Hard Invariants 全部 enforced。
- forgery 測試：非-Human caller 無法建立可被 `is_authorized()` 接受的 chain（含 self-attested "human"、caller-supplied grant、直接 mutate、手動設 consumed）。

## 6. 不做（Out of Scope）

- 不實作 HumanAuthorityPort 的真實認證（屬未來 DSH Adapter / runtime integration）。
- 不建 DSH subagent、不實作 LLM、不建 approval UI。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py`（MVP-1/2/3/4 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 封閉了哪些 forgery（逐條對應 1/2/3/4）
- 新增了哪些測試
- 完整回歸結果
- 確認 10 條 Hard Invariants 全部 enforced
- 剩餘 architectural concerns
