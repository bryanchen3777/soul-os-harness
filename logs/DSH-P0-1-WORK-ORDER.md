# 工單：DSH-P0-1 — Minimal Work Execution Adapter

**日期**：2026-08-23
**性質**：Phase 0 implementation（可寫 code）
**上游**：`docs/DSH-ADAPTER-BUILD-PLAN.md`（MA-4，IMPLEMENTATION AUTHORIZED）、baseline `26e1e49`

---

## 目標

建立 minimal Work Execution Adapter，證明完整 execution path 跑通：

```text
Python WorkKernel
      ↓
BridgeMessage（request）
      ↓
TypeScript soul-dsh-adapter
      ↓
DSH execution（mock，本工單證明 path 不實作真 subagent）
      ↓
HandoffResult
      ↓
WorkflowOrchestrator.consume_handoff()
      ↓
WorkEvent / durable log
```

## 範圍（只允許）

- Python↔TS Bridge（IPC transport + BridgeMessage serialization）
- TypeScript soul-dsh-adapter（minimal，mock DSH execution）
- src/work/ execution path（WorkKernel → BridgeMessage → Adapter → HandoffResult → consume_handoff → WorkEvent）

## 做法（決策已定，執行者照做）

1. **Python↔TS Bridge**：Python 端把 `BridgeMessage` 序列化成 JSON，spawn Node.js subprocess，寫 stdin，讀 stdout 的 `HandoffResult` JSON。Bridge 是獨立模組（**不放進 `src/work/` Domain Core**，避免污染零 DSH boundary）。
2. **TS soul-dsh-adapter**：minimal Node.js script，讀 stdin 的 BridgeMessage JSON → mock DSH execution（回傳一個 fake artifact/evidence 的 HandoffResult）→ 寫 stdout。**Adapter 無 durable write authority**（只 read/write stdin/stdout，不碰 durable store）。
3. **src/work/ execution path**：Python 端把 WorkKernel 的 execution request 轉成 BridgeMessage → 走 bridge → 拿回 HandoffResult → `WorkflowOrchestrator.consume_handoff()` → WorkEvent。
4. **測試**：bridge round-trip、E2E（create → execution → handoff → WorkEvent）、Adapter 無 durable write authority、DSH failure isolation（TS crash/timeout/malformed response 不污染 durable truth）、No-DSH Survival（拔掉 TS 後 Domain Core 仍可 fold/authorize）、dedup（duplicate handoff 不重複 append）。

## 驗收（10 acceptance gates）

1. Bridge contract PASS（BridgeMessage JSON round-trip，兩側一致）
2. Work execution E2E PASS（create → execution → handoff → WorkEvent）
3. Handoff → durable WorkEvent PASS
4. Adapter 無 durable write authority
5. restart / resume PASS
6. duplicate handoff dedup PASS
7. DSH failure isolation PASS
8. No-DSH Survival PASS
9. regression 全綠
10. Phase 0 scope containment PASS

## 不做（Out of Scope）

- 不改 `src/work/` 十一模組（Domain Core 零 DSH import 永久鎖死）
- Soul runtime / eventbus、heartbeat / scheduler migration、World / perception、Inner Life、Agency redesign、Relationship、Time / Context、Identity migration、Memory migration
- DSH session → Soul memory、Adapter durable writer、第二條 production route
- 不實作真 DSH subagent / workflow / goal（本工單用 mock execution 證明 path）

## Frozen Contract 注意

- 不得修改 `src/work/` 十一模組、2A–2D 四份 contract。
- canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

## 回報格式

- 改動檔案清單
- Python↔TS bridge 的實作方式
- TS soul-dsh-adapter 的實作方式
- 新增了哪些測試
- 完整回歸結果
- 確認 10 條 acceptance gate 全部通過
- 剩餘 architectural concerns
