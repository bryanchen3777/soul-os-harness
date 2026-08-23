# 工單：MVP-2 — DSH Adapter Boundary（Python 側，唯一 DSH coupling 點）

**日期**：2026-08-23
**性質**：MVP implementation（可寫 code）
**上游**：`logs/DSH-MVP-1-WORK-ORDER.md`（MVP-1 完成）、`docs/DSH-ARCHITECTURE-CONTRACT-GATE.md`（2A–2D Gate PASS）

---

## 目標

建立 DSH Adapter 的 boundary：定義 anti-lock-in ports（Soul OS kernel 使用的介面）與 bridge protocol（Python↔TypeScript 的 message contract），作為**唯一 import DSH types 的地方**。本工單只做 Python 側 boundary，**不建 TypeScript DSH plugin**。

## 範圍（新建檔案，Soul OS repo 內）

- `src/work/ports.py`
- `src/work/bridge.py`
- `tests/test_work_ports.py`

## 做法（決策已定，執行者照做）

1. `src/work/ports.py`：定義 anti-lock-in ports（`typing.Protocol` 或 ABC），**不 import 任何 DSH type**：
   - `SoulRuntimePort`：`receive(stimulus)` / `tick(now)` / `recover(checkpoint)` / `snapshot()`
   - `SoulWorldPort`：`observe(query)` / `act(intent, policy)`
   - `SoulExperienceStore`：`append(experience)` / `query(...)` / `checkpoint()`
   - `SoulPresentationPort`：`publish(projection)`
2. `src/work/bridge.py`：定義 bridge protocol（language-neutral message format）：
   - message envelope：`event_id`、`timestamp`、`actor`、`source`、`causation/reference`、`schema_version`
   - 三種 message type：`request` / `response` / `event`
   - single-writer rule：kernel 是唯一 writer（DSH 側只讀不寫 durable state）
3. `tests/test_work_ports.py`：ports 不 import DSH、bridge message round-trip、single-writer rule 明確。

## 驗收（完成的定義）

- `pytest tests/test_work_ports.py` 全過。
- ports 不 import 任何 DSH type。
- bridge protocol 是 language-neutral（JSON-serializable）。
- single-writer rule 明確（kernel 唯一 writer）。

## 測試

- 新增 `tests/test_work_ports.py`（ports / bridge 兩組）。
- 跑既有回歸確認 0 影響。

## 不做（Out of Scope）

- 不建 TypeScript DSH plugin。
- 不 import DSH type。
- 不實作 subagent / workflow / goal 的實際呼叫。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。
- 不改 `src/work/schema.py` / `state_machine.py` / `store.py`（MVP-1 已 commit）。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- 不得改 MVP-1 的 `src/work/schema.py` / `state_machine.py` / `store.py`。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- 測試結果（跑了哪些、過幾筆、有無失敗）
- 是否有踩到 Frozen Contract / 意外行為
