# 工單：MVP-4 — Workflow / Handoff（Chief 的 decision/assign/consume 閉環）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-3-R1-WORK-ORDER.md`（MVP-3 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 workflow orchestration（Chief 的 decision / assign / consume 閉環）+ handoff flow，把 2A–2D contract 跑起來。這是「domain orchestration」（Chief 的決策邏輯），不是「execution orchestration」（DSH subagent，屬後續 phase）。

## 範圍（Soul OS repo 內）

- 新增 `src/work/workflow.py`
- 新增 `tests/test_work_workflow.py`

## 做法（決策已定，執行者照做）

1. `src/work/workflow.py`：`WorkflowOrchestrator`（Chief 的 decision/assign/consume 閉環），**只透過 `WorkKernel` 操作 durable state，不直接碰 store**：
   - `create_work(objective, owner)` → 建立 Work（proposed，記 state_transition）
   - `assign(work_id, role)` → 委派給 kernel.assign（記 decision_made）
   - `consume_handoff(handoff)` → 委派給 kernel.record_handoff（記 artifact/evidence/decision）
   - `synthesize(work_id)` → fold 出 current WorkObject，供 Chief 判斷下一步
   - 全程不 import DSH、不呼叫 LLM、不建 subagent
2. `tests/test_work_workflow.py`：create → assign → handoff → consume → synthesize 閉環跑通；single-writer 不破壞（orchestrator 只走 kernel）；fold 結果正確。

## 驗收（完成的定義）

- `pytest tests/test_work_workflow.py` 全過。
- 閉環跑通：create → assign → handoff → consume → synthesize。
- orchestrator 只走 kernel，不直接碰 store（single-writer 不破壞）。
- 不 import 任何 DSH type。

## 測試

- 新增 `tests/test_work_workflow.py`。
- 跑既有回歸（`test_work_roles.py` + `test_work_contract.py` + `test_work_ports.py`）確認 0 影響。

## 不做（Out of Scope）

- 不建 DSH subagent（TypeScript）。
- 不實作 LLM 呼叫（Chief 的實際決策由 LLM 產生，屬後續 phase）。
- 不建 approval model / capability policy 執行（屬 MVP-5）。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py`（MVP-1/2/3 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
