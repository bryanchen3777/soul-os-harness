# 工單：MVP-6 — Recovery / Resume（durable state 持久化 + 重掛）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-5-R2-WORK-ORDER.md`（MVP-5 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 recovery / resume：把 authority boundary 的 approval/grant registry 從 in-memory 提升為 durable state，restart 後能從 durable truth 恢復。這是 2D「Soul OS owns the durable work truth」的最後一塊。

## 範圍（Soul OS repo 內）

- 新增 `src/work/persistence.py`（或擴 `authority.py`，只做 additive）
- 新增 `tests/test_work_persistence.py`

## 做法（決策已定，執行者照做）

1. **持久化 approval/grant registry**：把 `AuthorityManager` 的 `__approvals`/`__grants` 從 in-memory 提升為 append-only durable log（複用 `WorkStore` 的 append-only JSONL 模式，或新增 `AuthorityStore`）。approval/grant 的建立、撤銷、消費都記成 durable event。
2. **Resume**：`AuthorityManager.resume()` 從 durable log fold 出 canonical registry（approval/grant 的 current state = fold(events)）。
3. **Recovery flow**：restart → load durable log → fold registry → 後續 authorization 用恢復後的 canonical state。
4. **文件化測試**（承接 MVP-5-R2 Final Review 的非 blocking 建議）：補一條測試，把 mangled-name 雙注入（`mgr._AuthorityManager__grants`/`__approvals` 注入「完全一致」偽造對）回傳 True 顯式記錄為 known/accepted limitation。

## 驗收（完成的定義）

- `pytest tests/test_work_persistence.py` 全過 + 全回歸綠。
- approval/grant registry 是 durable（restart 後可 resume）。
- resume 後 authorization 用恢復的 canonical state。
- mangled-name 殘留路徑有文件化測試（known/accepted limitation）。

## 測試

- 新增 `tests/test_work_persistence.py`（persist / resume / recovery + 文件化 mangled-name 測試）。
- 跑既有回歸（authority + workflow + roles + contract + ports）確認 0 影響。

## 不做（Out of Scope）

- 不建 DSH subagent、不實作 LLM、不建 approval UI。
- 不實作 HumanAuthorityPort 的真實認證（屬未來 DSH Adapter）。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4/5 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py` / `authority.py`（MVP-1/2/3/4/5 已 commit；authority.py 若需 additive 只做 additive）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 持久化 / resume / recovery 的實作方式
- 新增了哪些測試（含文件化 mangled-name 測試）
- 完整回歸結果
- 剩餘 architectural concerns
