# 工單：MVP-5 — Authority Boundary（Capability Policy + Approval Model）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-4-WORK-ORDER.md`（MVP-4 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 authority boundary：capability policy（role → capability → authorized，2A §5）+ approval model（Approval / CapabilityGrant / provenance chain / revocation，2C）。這是把 2C 的「Approval = Human authority」落成 executable enforcement。

## 範圍（Soul OS repo 內）

- 新增 `src/work/authority.py`
- 新增 `tests/test_work_authority.py`

## 做法（決策已定，執行者照做）

1. `src/work/authority.py`：
   - `CapabilityPolicy`：`authorize(role, capability) -> bool`（用 `roles.ROLE_CAPABILITIES`，2A §5）；高風險 capability（production.write / git.commit / git.push / deploy / external.publish）需 approval gate。
   - `Approval` schema（2C §2）：approval_id / work_id / capability / requested_action / action_scope（single_action | work_scoped）/ grantee_role / granted_by / granted_at / expires_at / revoked_at / scope_constraints。
   - `CapabilityGrant`：grant_id → approval_id（provenance chain 一對一，2C §6）。
   - `grant(approval, actor)`：只有 Human（granted_by=human）能發 grant；agent 不能製造/推測/替代 Approval（2A invariant #2）。
   - `revoke(approval_id)`：revocation 立即阻止新 action；in-flight 依 atomicity（2C §5）。
   - `is_authorized(action, grant)`：privileged action 必須有且只有一個 valid governing grant（2C §8 #5）；斷鏈 = authorization failure。
2. `tests/test_work_authority.py`：capability policy 對齊 2A §5、approval 只能由 Human 產生、grant 一對一 provenance、revocation 立即阻止新 action、斷鏈 = deny、production.write 必須 time-bounded（expires_at=null = invalid）。

## 驗收（完成的定義）

- `pytest tests/test_work_authority.py` 全過。
- capability policy 對齊 2A §5。
- approval 只能由 Human 產生（agent 不能製造/推測/替代）。
- grant 一對一 provenance（approval → grant → action → evidence）。
- 斷鏈 = authorization failure（禁止推斷 approval）。
- 不 import 任何 DSH type。

## 測試

- 新增 `tests/test_work_authority.py`。
- 跑既有回歸（`test_work_workflow.py` + `test_work_roles.py` + `test_work_contract.py` + `test_work_ports.py`）確認 0 影響。

## 不做（Out of Scope）

- 不建 DSH subagent、不實作 LLM 呼叫。
- 不建 approval UI（呈現/撤銷/時效的 UI，屬後續 phase）。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py`（MVP-1/2/3/4 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
