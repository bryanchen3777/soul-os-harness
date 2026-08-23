# 工單：MVP-3 — Chief + Specialist Roles + single-writer enforcement

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-2-WORK-ORDER.md`（MVP-2 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 role model（Chief / Developer / Tester / Auditor）+ role → capability mapping（2A §5），並把 single-writer rule 從「定義」變成「強制」——store 寫入路徑掛 writer 檢查，只有 Work kernel 能寫 durable state。

## 範圍（Soul OS repo 內）

- 新增 `src/work/roles.py`
- 新增 `src/work/kernel.py`
- 新增 `tests/test_work_roles.py`

## 做法（決策已定，執行者照做）

1. `src/work/roles.py`：定義 role model（2A §5）：
   - `Role` enum：`chief` / `developer` / `tester` / `auditor`（+ `human` 供 approval 用）
   - `ROLE_CAPABILITIES` mapping（2A §5 矩陣）：
     - chief → orchestration / decision / work.assign
     - developer → workspace.read / isolated.write / test.execute / git.branch
     - tester → workspace.read / test.execute / evidence.create
     - auditor → workspace.read / review / evidence.create
     - human → approval / privileged actions
   - capability 名稱是 capability-neutral（非 DSH tool 名）
2. `src/work/kernel.py`：`WorkKernel`（唯一 writer）：
   - 包住 `WorkStore`，只有 kernel 能 `append`（single-writer enforcement）
   - 提供 `assign(work_id, role)` / `record_handoff(handoff)` 等 kernel 專屬操作（Chief/Specialist 透過 kernel，不直接寫 store）
   - 非 kernel 的 actor 呼叫寫入 → 拋 `NotDurableWriterError`
3. `tests/test_work_roles.py`：role → capability mapping 對齊 2A §5、single-writer 強制（非 kernel 不能寫）、kernel 寫入正常。

## 驗收（完成的定義）

- `pytest tests/test_work_roles.py` 全過。
- role → capability mapping 對齊 2A §5。
- single-writer 強制：非 kernel actor 寫入拋錯。
- 不 import 任何 DSH type。

## 測試

- 新增 `tests/test_work_roles.py`（roles / kernel 兩組）。
- 跑既有回歸（`test_work_contract.py` + `test_work_ports.py`）確認 0 影響。

## 不做（Out of Scope）

- 不建 DSH subagent（TypeScript）。
- 不實作 Chief 的完整 orchestration（decision/assign/consume 的 LLM 流程，屬 MVP-4）。
- 不建 approval model / capability policy 執行（屬 MVP-5）。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 2A–2D 四份 contract。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/work/schema.py` / `state_machine.py` / `ports.py` / `bridge.py`（MVP-1/2 已 commit）。
- `store.py` 若需加 writer 檢查，只做 additive（不破壞既有 append/fold 語意）。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
