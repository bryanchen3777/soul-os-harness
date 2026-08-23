# 工單：MVP-3-R1 — Single-Writer Enforcement Hardening

**日期**：2026-08-23
**性質**：MVP implementation（corrective work，可寫 code）
**上游**：`logs/DSH-MVP-3-WORK-ORDER.md`（MVP-3 BLOCKED）、MVP-3 FINAL REVIEW（single-writer 是 convention 非 enforcement）

---

## 目標

把 single-writer 從 WorkKernel API-level convention 提升為 **WorkStore durable-write enforcement**。

## Blocker（MVP-3 FINAL REVIEW 發現）

三條 bypass：
1. `WorkStore.append()` 可直接寫檔（無 writer 檢查）
2. `WorkStore` 可直接 import / instantiate
3. `kernel._store.append()` 可直接繞過 kernel

## 核心 invariant（不可破）

```
EVERY durable append
   ↓
MUST pass writer authorization
   ↓
unauthorized actor = hard failure
```

即使有人故意 `from src.work.store import WorkStore; store.append(...)` 也不能突破。**「WorkStore 不 export」不是 security boundary；append() 的 writer authorization 才是。**

## 做法（決策已定，執行者照做）

1. **`WorkStore.append(event, actor)`**：
   - `actor` 必須**明確提供**（無 default，缺參數即失敗）
   - 在 durable write boundary enforce：`is_durable_writer(actor)` 為 False → 拋 `NotDurableWriterError`
   - 不得允許 default / implicit actor 繞過
2. **`WorkKernel.append(event, actor=DURABLE_WRITER)`**：
   - 保持唯一合法 durable writer facade
   - 把 actor 明確傳給 `store.append(event, actor)`
   - 不得因 internal access 破壞 authorization
3. **`NotDurableWriterError` 位置**：移到 `store.py`（或 `bridge.py`）避免 circular import（kernel.py 目前 import store.py，store.py 不能反向 import kernel.py）。kernel.py 從新位置 import 並 re-export 保持相容。
4. **`WorkStore` exposure**：移除不必要的 public export / re-export（`__init__.py`），但**不把這當主要 security boundary**——真正 boundary 是 append() enforcement。
5. **Bypass tests**（`tests/test_work_roles.py` 或新檔）：
   - A. `WorkKernel` 正常寫入 → PASS
   - B. `WorkStore().append(event, "developer")` → `NotDurableWriterError`
   - C. `kernel._store.append(event, "developer")` → `NotDurableWriterError`
   - D. `from src.work.store import WorkStore; WorkStore().append(event, "developer")` → `NotDurableWriterError`（即使能 import 也不能 bypass）

## 驗收（完成的定義）

- `pytest tests/test_work_roles.py tests/test_work_contract.py tests/test_work_ports.py` 全過（38+ tests + 新增 bypass tests）。
- writer enforcement 是 **STORE-LEVEL**（`WorkStore.append` 檢查），不是 KERNEL-ONLY。
- 三條 bypass 全部封死（B/C/D 測試證明）。

## 不做（Out of Scope）

- 不改 2A–2D 四份 contract。
- 不改 WorkState semantics、不改 Role → Capability matrix。
- 不加入 Approval / Grant、不加入 DSH、不實作 MVP-4。
- 不碰 `src/agency/`、`src/memory/`、`src/eventbus/`、`src/inner_life/`。

## Frozen Contract 注意

- 不得修改 2A–2D 四份 contract（`docs/DSH-*.md`）。
- `store.py` 的改動只做 additive（加 actor 參數 + writer 檢查），不破壞既有 append/fold 語意。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單（含 store.py 的 additive 改動）
- 封閉了哪些 bypass（逐條對應 B/C/D）
- 新增了哪些測試
- 完整回歸結果
- 確認 writer enforcement 現在是 STORE-LEVEL 而非 KERNEL-ONLY
- 剩餘 architectural concerns
