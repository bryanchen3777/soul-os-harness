# 工單：MVP-1 — Work Contract + Durable Work Store（Python 側，零 DSH coupling）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-0-WORK-ORDER.md`（baseline 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

在 Soul OS 側建立 Work Contract 的 domain schema、狀態機與 append-only WorkEvent durable store，作為 DSH Multi-Agent MVP 的 durable truth 地基。本工單**不碰 DSH**。

## 範圍（新建檔案，全部在 Soul OS repo 內）

- `src/work/__init__.py`
- `src/work/schema.py`
- `src/work/state_machine.py`
- `src/work/store.py`
- `tests/test_work_contract.py`

## 做法（決策已定，執行者照做）

1. `src/work/schema.py`：用 pydantic（對齊 `src/eventbus/schema.py` 模式）定義：
   - `WorkState` enum：proposed / approved / assigned / in_progress / awaiting_review / awaiting_approval / done / rejected / cancelled / blocked（**唯一 authoritative，不得新增 reviewing / failed / waiting**）。
   - `WorkObject`：schema_version="1.0"、work_id(uuid)、objective、state、owner(role)、assigned_agents(list[role])、artifacts / evidence / decisions / approvals / dependencies / provenance / resume_state。**不得引用任何 DSH type/id**。
   - `WorkEvent`：work_id、event_type（state_transition / artifact_produced / evidence_produced / decision_made / approval_granted / grant_issued）、payload、timestamp、provenance。
   - `HandoffResult`：work_id、role、result_type（**僅 artifact / evidence / decision，不得有 approval**）、artifact_refs / evidence_refs / decision、status（done / blocked / needs_input）、resume_hint。
   - `resume_state`：current_phase、pending_handoffs、last_artifact_refs、idempotency_keys。
   - `provenance`：role、capability（capability-neutral，非 DSH tool 名）、timestamp、input_refs、output_refs。
2. `src/work/state_machine.py`：實作 transition 驗證。**只有兩個 transition 需 Human approval**（proposed→approved、awaiting_approval→done），其餘 autonomous；blocked 是 non-terminal 可 resume 回 resume_state.current_phase。非法 transition 拋錯。
3. `src/work/store.py`：append-only WorkEvent JSONL log（複用 `src/memory/v1/store.py` 模式：只 append、不 update/delete、corrupt row 跳過留 log）。提供 `append(event)` 與 `fold(work_id) -> WorkObject`（current state = fold(events)）。資料目錄用 `src/paths.py` 的 `data_root() / "work"`。
4. `tests/test_work_contract.py`：schema 序列化 round-trip、state machine 合法/非法 transition、store append+fold、corrupt row 跳過、resume_state 最小重建。

## 驗收（完成的定義）

- `pytest tests/test_work_contract.py` 全過。
- WorkObject 序列化後不含任何 DSH type/id 字串。
- state machine 拒絕未列於 2A §4 的 state 與 transition。
- store 是 append-only（無 update/delete API）。

## 測試

- 新增 `tests/test_work_contract.py`（schema / state_machine / store 三組）。
- 跑既有回歸確認 0 影響（本工單純新增，不 import 既有 domain 模組）。

## 不做（Out of Scope）

- 不 import 任何 DSH type / 不建 DSH Adapter。
- 不實作 capability policy 執行、approval UI、worktree、git 操作。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/` 等 frozen contract。
- 不建立 orchestration engine（本工單只有 domain schema + store）。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 `src/eventbus/schema.py` 的 SoulEvent/EventType、`src/agency/` 的 4 stages/TriggerEnvelope/4 handlers、`src/memory/sage/` 寫入邏輯。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
