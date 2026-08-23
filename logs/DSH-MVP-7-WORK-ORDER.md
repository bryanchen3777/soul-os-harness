# 工單：MVP-7 — End-to-End Vertical Slice（2A–2D 完整閉環）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-6-R1-WORK-ORDER.md`（MVP-6 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 end-to-end vertical slice，把 2A–2D 完整閉環跑起來：Human → Chief → Work → Specialist → Handoff → Evidence → Chief → Approval → privileged action → Evidence → Durable Store。這是 MVP 的最後一張，串起 MVP-1~6 的所有模組。

## 範圍（Soul OS repo 內）

- 新增 `src/work/e2e.py`（end-to-end vertical slice 的 orchestration 腳本）
- 新增 `tests/test_work_e2e.py`（end-to-end 測試）

## 做法（決策已定，執行者照做）

1. `src/work/e2e.py`：`run_vertical_slice()` 串起完整閉環（只走既有模組，不 import DSH、不建 subagent、不呼叫 LLM）：
   - `WorkflowOrchestrator.create_work(objective, owner)` → proposed
   - Human approval #1：`state_machine.validate_transition(proposed → approved)` + 記 state_transition
   - `WorkflowOrchestrator.assign(work_id, role)` → decision_made
   - Specialist handoff：`WorkflowOrchestrator.consume_handoff(HandoffResult(...))` → artifact/evidence/decision
   - `WorkflowOrchestrator.synthesize(work_id)` → fold current WorkObject
   - Human approval #2：`validate_transition(awaiting_approval → done)` + 記 state_transition
   - Privileged action：`AuthorityManager.grant(approval, context)` → `CapabilityGrant` → `is_authorized(action)` → `consume(grant_id)`
   - Evidence → durable store（WorkEvent log + AuthorityStore）
2. `tests/test_work_e2e.py`：完整閉環跑通；restart → resume 後 authorization 語意一致；single-writer 不破壞；零 DSH coupling。

## 驗收（完成的定義）

- `pytest tests/test_work_e2e.py` 全過 + 全回歸綠。
- 完整閉環跑通：create → approve → assign → handoff → synthesize → approve → privileged action → evidence → durable store。
- restart → resume 後 authorization 語意一致。
- 不 import 任何 DSH type。

## 測試

- 新增 `tests/test_work_e2e.py`。
- 跑既有回歸（persistence + authority + workflow + roles + contract + ports）確認 0 影響。

## 不做（Out of Scope）

- 不建 DSH subagent（TypeScript）、不實作 LLM、不建 approval UI、不實作 HumanAuthorityPort 真實認證。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract、不改 MVP-1/2/3/4/5/6 的檔案。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `store.py` / `ports.py` / `bridge.py` / `roles.py` / `kernel.py` / `workflow.py` / `authority.py` / `persistence.py`（MVP-1/2/3/4/5/6 已 commit）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 完整閉環的實作方式
- 新增了哪些測試
- 完整回歸結果
- 剩餘 architectural concerns
