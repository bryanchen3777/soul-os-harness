# soul-dsh-adapter（DSH-P0-1，Phase 0 mock）

DSH migration Phase 0 的 minimal **Work Execution Adapter**（mock DSH execution）。

> 核心原則：**Soul OS owns the durable work truth. DSH owns ephemeral execution.**

## 內容

| 檔案 | 說明 |
|---|---|
| `soul-dsh-adapter.mjs` | Node.js mock adapter：讀 stdin 的 BridgeMessage JSON → mock DSH execution → 寫 stdout 的 HandoffResult JSON |

## 通訊協定（與 `src/work_adapter/bridge.py` 兩側 mirror）

- `stdin`：一行 `BridgeMessage` JSON（`message_type=request`，payload 帶
  `work_id / objective / role / capability / resume_state`）
- `stdout`：一行 `HandoffResult` JSON（`work_id / role / result_type /
  artifact_refs / evidence_refs / decision / status / resume_hint`）
- 一 request 一 response，EOF 即結束。serialization 是 language-neutral JSON
  （`src/work/bridge.py` 的 BridgeMessage / `src/work/schema.py` 的 HandoffResult
  是 authoritative source，兩側 mirror 同一 contract）。

## 無 durable write authority

Adapter 只 read/write stdin/stdout，**不 import fs、不碰任何 durable store**。
durable write 一律回 Domain Core（`WorkflowOrchestrator.consume_handoff`）執行。

## mock execution

依 payload.capability 決定 result_type（含 "evidence" → evidence、含
"decision" → decision、其餘 → artifact），fake ref = `mock:sha256:<hash>`：
同 request → 同 ref，讓 Domain Core 的 idempotency dedup 在 bridge 全路徑命中。

## 手動測試

```bash
echo '{"message_type":"request","actor":"developer","source":"soul_kernel","payload":{"work_id":"w1","objective":"build X","role":"developer","capability":"artifact.create"}}' | node soul-dsh-adapter.mjs
```

## 注意（architectural concern）

依 `docs/DSH-ADAPTER-BUILD-PLAN.md`（MA-4）§1.1，production adapter 是
**獨立 TypeScript DSH plugin package**（不在 Soul OS repo 內）。本目錄的
`.mjs` 只是 Phase 0 的 minimal mock（證明 bridge + execution path），
後續 phase 會以獨立 package 形式重寫為真 DSH 執行。
