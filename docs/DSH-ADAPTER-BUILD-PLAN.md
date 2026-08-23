# DSH Adapter Build Plan（DSH-MA-4）

**日期**：2026-08-23
**狀態**：ARCHITECTURE DESIGN — 前置設計，NOT AUTHORIZED（待 Independent Implementation Architecture Review）
**上游**：`docs/DSH-SOUL-OS-MIGRATION-DECOMPOSITION.md`（MA-3）、`docs/DSH-ADAPTER-BOUNDARY.md`（MA-1）

---

## 0. North Star

> **Migration = 建立新的 execution boundary，不是搬遷 Soul ownership。**

MA-4 開始碰 implementation，但**不直接寫 Adapter**。先做 Implementation Build Plan，把施工問題釘死，通過 Independent Review 才授權施工。

```text
MA-4 Implementation / Adapter Build Plan
        ↓
Independent Implementation Architecture Review
        ↓
IMPLEMENTATION AUTHORIZED
        ↓
Phase 0 Adapter implementation
        ↓
Phase 0 Contract / E2E / No-DSH Gate
```

---

## 1. Adapter package / directory boundary

### 1.1 TypeScript 端放哪裡

Adapter 是 **TypeScript DSH plugin**（獨立 package，如 `soul-dsh-adapter`），**不在 Soul OS repo 內**（MA-0 已定位）。

```text
Soul OS repo（Python）
  ├── src/work/（Domain Core，零 DSH import）
  └── ...

soul-dsh-adapter（TypeScript DSH plugin，獨立 package）
  ├── src/index.ts（host 端：註冊 tools / routes / systemPrompt）
  ├── src/bridge.ts（Python ↔ TypeScript IPC bridge）
  └── ...
```

### 1.2 Python ↔ TypeScript 如何通訊

透過 **IPC / subprocess bridge**（`src/work/bridge.py` 的 BridgeMessage 是 language-neutral JSON contract）。

```text
Python Domain Core
        │
   BridgeMessage（JSON，language-neutral）
        │
   IPC / subprocess
        │
TypeScript Adapter
        │
   DSH subagent / workflow / goal
```

### 1.3 哪些 interface 是跨語言 contract

| Interface | 語言 | 說明 |
|---|---|---|
| `BridgeMessage` | JSON | message envelope（event_id / causation / reference / payload） |
| `HandoffResult` | JSON | 結構化 result（artifact / evidence / decision） |
| `HumanAuthorityContext` | JSON | authenticated context（identity + authority_token） |
| `resume_state` | JSON | 最小重建狀態 |

**這些是跨語言 contract，兩邊都要 mirror，但 Domain Core 是 authoritative source。**

---

## 2. Phase 0 最小 execution path

```text
WorkKernel（Python）
   ↓
BridgeMessage（request，capability-neutral）
   ↓
Adapter（TypeScript，唯一 import DSH types）
   ↓
DSH subagent（execution）
   ↓
HandoffResult（結構化 result）
   ↓
WorkflowOrchestrator.consume_handoff（Python）
   ↓
WorkEvent（durable log）
```

Phase 0 只接 `src/work/`（Domain Core），不接 Soul runtime（eventbus / heartbeat / scheduler）。

---

## 3. HumanAuthorityPort

### 3.1 authenticated context 從哪裡來

`HumanAuthorityContext` 由 **trusted Human Authority boundary**（DSH Adapter 的 approval UI / runtime integration）簽發，不是 agent 自己製造。

```text
Human → DSH Adapter approval UI → HumanAuthorityContext（authenticated）→ AuthorityManager.grant()
```

### 3.2 deny-by-default

無注入 `HumanAuthorityPort` → `NotHumanGrantorError`（deny-by-default）。在 Adapter 落地前，任何 privileged action 都無法執行（正確的安全預設）。

### 3.3 Approval → Grant → Action provenance 如何跨 Adapter

provenance chain（Approval → Grant → Action → Evidence）在 **Domain Core（authority.py）** 強制，Adapter 只傳遞 authenticated context，不持有 authority 真相。

```text
HumanAuthorityContext（Adapter 簽發）
   ↓
AuthorityManager.grant(approval, context)（Domain Core 驗證）
   ↓
CapabilityGrant（Domain Core 強制一對一 provenance）
   ↓
AgentAction（Domain Core 強制 grant → action）
```

---

## 4. Resume

### 4.1 WorkEvent / AuthorityEvent fold

Domain Core 從 durable log（WorkEvent + AuthorityEvent）fold 出 canonical state。

### 4.2 resume_state reconstruction

`resume_state` 從 durable log 推導（不是 DSH session snapshot）。

### 4.3 fresh DSH execution

Adapter 讀 resume_state → re-mount fresh DSH subagents / workflows（不假設 in-process 存活）。

### 4.4 idempotency / duplicate handoff

`resume_state.idempotency_keys` 防止重啟後重複執行 / 重複 handoff。

---

## 5. Single-writer

### 5.1 Adapter 永遠不能成為第二 writer

`WorkStore.append` / `AuthorityStore.append` 在 store 層檢查 `is_durable_writer(actor)`，Adapter 的 actor（`dsh_adapter`）永遠不是 durable writer。

### 5.2 legacy route + DSH route 並存

feature flag 切換，**每階段只允許一條 active production route**（single-writer rule 的延伸）。切換後舊 route 保留做 parallel observation，不立即移除。

---

## 6. No-DSH Survival

Phase 0 做完就要能證明：拔掉 DSH 後，Soul Core 還是完整可運作（receive / tick / recover / snapshot / persist / perceive / form experience / memory retrieval / agency）。

```text
Phase 0 完成後
  ↓
拔掉 DSH（關閉 Adapter plugin）
  ↓
Soul Core 仍能 fold durable log / authorize / resume
```

---

## 7. Implementation gate

**MA-4 本身通過 Independent Review 前，不授權施工。**

```text
MA-4 Build Plan
  ↓
Independent Implementation Architecture Review
  ↓
IMPLEMENTATION AUTHORIZED
  ↓
Phase 0 Adapter implementation
  ↓
Phase 0 Contract / E2E / No-DSH Gate
```

---

## 8. Boundary contract（可實作）

1. **Adapter 是 TypeScript DSH plugin（獨立 package）**，不在 Soul OS repo 內。
2. **Python ↔ TypeScript 透過 IPC / subprocess bridge**，BridgeMessage 是跨語言 contract。
3. **Phase 0 只接 src/work/**，不接 Soul runtime。
4. **HumanAuthorityContext 由 trusted boundary 簽發**，deny-by-default。
5. **provenance chain 在 Domain Core 強制**，Adapter 只傳遞 context。
6. **resume 只依賴 durable log**，idempotency_keys 防重複。
7. **Adapter 永遠不是 durable writer**（store-level 強制）。
8. **每階段只一條 active production route**（feature flag）。
9. **No-DSH Survival**：Phase 0 完成後拔掉 DSH，Soul Core 仍可運作。
10. **MA-4 通過前不授權施工**。

---

*本文件為 DSH-MA-4 Adapter Build Plan，供 Independent Implementation Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
