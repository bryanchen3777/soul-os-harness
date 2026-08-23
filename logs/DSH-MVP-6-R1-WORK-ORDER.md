# 工單：MVP-6-R1 — AuthorityStore Single-Writer Enforcement + Recovery Robustness

**日期**：2026-08-23
**性質**：MVP implementation（corrective work，可寫 code）
**上游**：`logs/DSH-MVP-6-WORK-ORDER.md`（MVP-6 BLOCKED）、MVP-6 FINAL REVIEW（AuthorityStore 缺 single-writer enforcement）

---

## 0. 背景

MVP-6 被 FINAL REVIEW 判 BLOCKED：`AuthorityStore.append(event)` 無 actor 參數、無 `is_durable_writer` 檢查，任何人可 `AuthorityStore().append(AuthorityEvent(APPROVAL_GRANTED, ...))` 直接偽造 durable authority log，繞過 `AuthorityManager.grant()` 的 `HumanAuthorityPort.authenticate`。這是 MVP-3 single-writer 漏洞的同款 bug。

## P0 — 必修：AuthorityStore single-writer enforcement

把 `AuthorityStore.append(event)` 改成 `AuthorityStore.append(event, actor)`：

- `actor` 必填、無 default
- store-level 執行 `is_durable_writer(actor)`
- unauthorized actor → `NotDurableWriterError`
- `AuthorityManager.grant/revoke/consume` 必須以 canonical durable writer 身份呼叫（`DURABLE_WRITER`）
- **不能只在 AuthorityManager 做檢查**——直接拿 AuthorityStore 寫 durable log 必須被拒絕

重新鎖死：

> **Every durable authority append MUST pass writer authorization.**

避免重演 MVP-3：kernel/manager-level check ≠ durable store boundary。

## P1 — Recovery robustness（修 D2）

`_fold_authority_events` 的 `event.payload["approval"]` / `["grant"]` 改成防禦式解析（`.get()`）：

- malformed / corrupt row 不讓 `resume()` 整個 crash
- skip / reject malformed event
- 不得產生半套 authority state

## P2 — 保留 D3

`store=None` 的 in-memory mode 不用改。這仍符合 2D 的 opt-in durable persistence 語意，不要 scope creep。

## R1 驗收 Gate（Final Review 必須重做）

1. direct `AuthorityStore.append()` bypass → DENY
2. forged APPROVAL_GRANTED event → DENY
3. forged GRANT_REVOKED event → DENY
4. forged GRANT_CONSUMED event → DENY
5. AuthorityManager 正常 grant/revoke/consume → PASS
6. durable write failure → memory 不 mutation
7. restart → resume 從 durable truth 恢復
8. malformed event → 不 crash / 不污染 canonical state
9. authorization pre/post recovery 一致
10. zero DSH coupling / frozen boundary preserved

## Hard blocker

**只要 AuthorityStore 還能被非-durable-writer 直接 append，就 BLOCK。**

「mangled-name 是 accepted limitation」不代表 durable registry injection 可以接受——前者是 process 內 Python reflection limitation；後者是可持久化、可跨 restart 存活的 authority forgery，兩者不是同一個 threat class。

## 範圍

- 修改 `src/work/persistence.py`（AuthorityStore.append 加 actor + writer 檢查）
- 修改 `src/work/authority.py`（AuthorityManager 以 DURABLE_WRITER 身份呼叫 + `_fold_authority_events` 防禦式解析）
- 修改 `tests/test_work_persistence.py`（加 bypass / forged event / malformed event 測試）

## 不做（Out of Scope）

- 不改 `store=None` 的 in-memory mode（P2）。
- 不建 DSH subagent、不實作 LLM、不建 approval UI、不實作 HumanAuthorityPort 真實認證。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4/5 的檔案（authority.py 只做 additive）。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py`（MVP-1/2/3/4 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- P0 / P1 的實作方式
- 新增了哪些測試（bypass / forged event / malformed event）
- 完整回歸結果
- 確認 10 條 R1 驗收 Gate 全部通過
- 剩餘 architectural concerns
