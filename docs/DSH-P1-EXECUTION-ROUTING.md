# DSH P1 — Execution Routing Decomposition

**日期**：2026-08-23
**狀態**：ARCHITECTURE PLAN ONLY — NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-ADAPTER-BUILD-PLAN.md`（MA-4，IMPLEMENTATION AUTHORIZED）、`docs/DSH-ADAPTER-BOUNDARY.md`（MA-1）、`docs/DSH-SOUL-OS-MIGRATION-DECOMPOSITION.md`（MA-3）、`logs/DSH-P1-PREFLIGHT-WORK-ORDER.md`（已 LAND，commit `34e91d4`）

---

## 0. Phase 編號澄清（先鎖定，避免治理混亂）

本專案有三套 phase 語彙，本文件用「**P1**」指第三套（Soul OS 工作執行路由），不與前兩套混淆：

| 套 | 語彙 | Phase 1 指什麼 |
|---|---|---|
| migration-plan（Phase 0-6） | Soul OS → DSH 全遷移路線 | Phase 1 = Read-only DSH Mirror（Soul runtime 呈現層，**非本文件**） |
| MA-3（Phase 0-4） | 接 execution 的順序 | Phase 0 = 只接 `src/work/`（Domain Core） |
| **本文件（P0/P1）** | `src/work/` 執行路由落地 | **P1 = 把 `src/work/` 的 execution request 路由到真 DSH primitive** |

**P1 的定位**：MA-3 Phase 0 的 **execution-routing 最後一塊**——P0-1 已用 mock adapter 證明「Python WorkKernel → BridgeMessage → Adapter → HandoffResult → consume_handoff → WorkEvent」的完整 path（commit `ece757b`），P1-Preflight 已把 status/result_type/bridge-error 三條語義收斂（commit `34e91d4`）。**P1 是把 mock DSH execution 換成真 DSH primitive（subagent / workflow / goal）**，同時證明「DSH orchestration ≠ Soul orchestration」在真實 primitive 下仍成立。

> **Phase 0 殘留（不屬 P1 scope）**：MA-3 §4 Phase 0 還含「HumanAuthorityPort 由 Adapter 實作」——MA-4-R1 只做了 Domain Core 側 `HmacHumanAuthorityPort`，adapter 側的 IPC transport（token 傳遞）明示延後，不在 P1 的 execution routing scope 內。故 P1 不是 MA-3 Phase 0 的「最後一塊」，而是「execution-routing 最後一塊」。

> **不做的事**：P1 不接 Soul runtime（eventbus / heartbeat / scheduler）、不接 World / Inner Life / Agency / Relationship / Time-Context。那些是 MA-3 的 Phase 1-4，屬後續。P1 只動 `src/work/` 執行路由。

---

## 1. North Star（承襲 MA-1 / MA-3）

> **Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.**

P1 的成敗標準不是「DSH subagent 跑起來了」，而是：

> **Adapter 把 Soul 已經決定的執行型態精確轉譯成 DSH primitive 呼叫，而 adapter 自己不成為 orchestration authority。**

---

## 2. 分解總覽

```text
P1-A  Execution Target Contract     ← 真正前置決策點（本文件核心）
P1-B  Artifact / Reference Boundary
P1-C  DSH Execution Routing
P1-D  Workflow / Goal Resume Semantics
P1-E  Production Adapter Boundary
```

**依賴順序**：P1-A 是唯一硬前置（target 沒鎖定，P1-C/D 無法定義）。P1-B 是 P1-C 的前置（routing 產出 artifact，ref 定址要先鎖）。P1-D 依賴 P1-A 的 continuous 語義。P1-E 是可並行/可延後的最終包裝。

---

## 3. P1-A — Execution Target Contract（核心決策）

### 3.1 問題

真 DSH 有三種 primitive：`subagent`（單一 specialist）、`workflow`（多階段編排）、`goal`（自動續輪）。誰決定「這個 execution 用哪一個」？

- 若 **adapter 自己決定** → adapter 吸收 orchestration authority（違反 North Star）。
- 若 **Domain Core 直接認識 DSH primitive 名** → 違反「Domain Core 零 DSH coupling」（MA-1 §10.2、2A §8.1）。

這是 P1-A 要解的核心張力。

### 3.2 決策 A1：Execution Target 是 capability-neutral 的 Domain Core contract

**Domain Core 認識「執行型態」（execution shape），不認識 DSH primitive 名。**

定義 capability-neutral 的 `ExecutionShape` enum（放 `src/work/schema.py`，Domain Core）：

| ExecutionShape | Soul 語義 | 對應 2A §7 | Adapter 映射 |
|---|---|---|---|
| `single_shot` | 單一 specialist 一次 handoff 完成 | role → subagent + preset | subagent |
| `multi_stage` | 多階段工作（有 dependencies / 需編排） | 多階段工作 → workflow | workflow |
| `continuous` | 自動續輪（goal 驅動多輪，無 human / chief 介入） | 自動續輪 → goal | goal |

- `single_shot` / `multi_stage` / `continuous` 是 Soul orchestration 語義，**不是 DSH primitive 名**，Domain Core 引入它們不違反零 DSH coupling。
- **orchestration authority 在 Soul**：Soul 從 Work Object 語義（dependencies / resume_state / 生命週期）推導 execution shape。
- **transport 在 Adapter**：Adapter 把 shape 映射到 DSH primitive（映射表是 adapter 實作細節，MA-1 §2.1 已 sanction role→subagent / 多階段→workflow / 續輪→goal 的 mapping）。

### 3.3 決策 A2：execution shape 由 Work Object 語義推導，不是額外聲明

不新增「使用者手填 target」欄位。execution shape 由 Domain Core 從 Work Object 推導：

- Work 有 `dependencies`（非空）→ `multi_stage`
- Work 需「無 human / chief 介入的多輪自動續輪」→ `continuous`
- 其餘（含「blocked 後由單一 specialist 一輪完成的 resume」）→ `single_shot`

**resume discriminator（關鍵，避免與 M1 衝突）**：`blocked` 後的 resume 若是由**單一 specialist 再 handoff 一輪完成**（blocked → resume 回 in_progress → specialist 再 consume_handoff），是 `single_shot`，**不是** `continuous`——否則 P1-C 第一期的 resume path 會被 fail-closed 切斷，M1「blocked 是 non-terminal、可恢復」的語義倒退。`continuous` 只保留給「需無 human / chief 介入、goal 驅動多輪自動續輪」的場景。

推導邏輯是 **Domain Core 的 orchestration 決策**（放 `src/work/`），不是 adapter 的判斷。這讓「誰決定 target」的答案乾淨：**Soul 決定，adapter 只 translate。**

### 3.4 決策 A3：BridgeMessage payload 承載 execution shape + 最小執行參數

`src/work_adapter/execution.py` 的 `build_execution_request` 在 payload 新增：

```jsonc
{
  "work_id": "...",
  "objective": "...",
  "role": "developer",
  "capability": "artifact.create",
  "execution_shape": "single_shot | multi_stage | continuous",  // ← 新增
  "resume_state": { ... }
}
```

- `execution_shape` 是 capability-neutral（沿用 `ExecutionShape` enum），不引入 DSH 名。
- `resume_state` 已有（最小重建狀態），承載重掛資訊。
- 其餘欄位（work_id / objective / role / capability）不變。

### 3.5 決策 A4：每種 shape 的 handoff semantics

| Shape | 正常 handoff | blocked / needs_input | crash / timeout |
|---|---|---|---|
| single_shot | artifact/evidence/decision（result_type 對齊 capability，M2 已鎖） | state_transition(current→blocked)（M1 已鎖） | BridgeExecutionError（M3 已鎖） |
| multi_stage | 最終 stage 產出 artifact/evidence；中間 stage 產出 intermediate ref | 同上 | 同上 + intermediate ref 保留在 resume_hint |
| continuous | 每輪產出 decision/evidence，最終輪產出 artifact | 同上，blocked 後 resume 回 target state | 同上 + resume_state 由 durable log 重建 |

> **P1-A 只鎖 contract 語義，不鎖實作**。第一期實作（P1-C）只接 `single_shot`（subagent）——MA-3 §4「第一期只接 subagent」；`multi_stage`（workflow）與 `continuous`（goal）的 resume semantics 在 P1-D 才落地，但 contract 語義在 P1-A 一次鎖定，避免 P1-D 反推改 contract。

### 3.6 決策 A5：adapter 吸收 authority 的防火牆

即使 shape 由 Soul 推導，adapter 仍可能偷偷吸收 authority。防火牆：

1. **Adapter 不得自行決定 shape**——shape 只在 Domain Core 推導，adapter 只讀 `payload.execution_shape` 做 primitive 選擇。
2. **Adapter 不得自行編排多 stage**——`multi_stage` 的 stage 依賴圖在 Domain Core（Work.dependencies），adapter 不生成編排邏輯。
3. **Adapter 不得自行決定 resume**——`continuous` 的續輪決策在 Soul durable scheduler（MA-1 §6），adapter 只 translate resume mutation。
4. **Adapter 仍是 single-writer 之外**——durable write 仍只經 `kernel.record_handoff`（M1/M2 後更嚴格）。

### 3.7 決策 A6：multi_stage 的 workflow script authorship（P1-D 前置決策）

DSH workflow primitive 的載體是 JS script，script 本身就編碼 stage 結構 / 順序 / 資料流 / 錯誤處理。若 script 由誰寫不鎖定，A5.2「adapter 不生成編排邏輯」只是 policy statement，不是 mechanism——這是 P1-A 必須補的洞。

**鎖定方向**（三選一，P1-D work order 前必須選定）：
- **選項 1（推薦）：Domain Core 產出 shape-level 宣告式 spec，adapter 只做機械渲染。** Work.dependencies 在 Domain Core 折疊成 stage 依賴的宣告式描述（stage 順序、每 stage 的 role / capability、intermediate ref 流），adapter 把這個 spec 機械渲染成 DSH workflow script——渲染是 transport，不產生編排判斷。
- 選項 2：adapter 渲染，並在 P1-D work order 定義「Work.dependencies → script」的渲染契約（stage prompt、intermediate ref 流、failure 處理），作為 decision-complete 內容。
- 選項 3：multi_stage 第一期不接（與 C2 一致），script authorship 決策延後到 P1-D work order 一併解決。

**本 decomposition 鎖定**：script authorship 是 **P1-D 前置決策**，不得留到實作時由執行者自行決定。P1-D work order 必須先選定方向並定義契約，再派 executor。

---

## 4. P1-B — Artifact / Reference Boundary

### 4.1 問題

P1-C 執行後會產出 artifact。artifact 的 ref 怎麼定址？P0-1 review 的 D1（refs content-address 驗證）被 defer 因「需 artifact store」。P1-B 要決定：

1. artifact store 是否與 P1-C 同批落地？
2. refs 在沒有 store 前是什麼契約形態？

> **與 P1-Preflight D1 的立場反轉（治理透明度）**：P1-Preflight 把 refs content-address 驗證 defer（D1 前提是「Phase 1 尚無 artifact store，refs 是 opaque pointer」）。本 decomposition 的 B1/B2 決定在 P1-C 落地 artifact store 並同步引入 content-address 驗證，屬**設計上層取代** D1 的「尚無 store」前提——work order 非 frozen，不構成違約，但此反轉必須明記，避免後續把 D1 誤讀為「永不驗證 refs」。

### 4.2 決策 B1：refs content-address 驗證與 artifact store 同批落地，不提前做假驗證

- 在 artifact store 落地前，refs 是 **opaque pointer**（現狀），Domain Core 不驗證 ref 內容定址。
- 一旦 artifact store 落地（P1-C 的 artifact 落地點），**同步**引入 ref 格式驗證（`sha256:<hex>`）+ 內容定址驗證。
- 不在 store 落地前單獨做 ref 格式驗證（那是「拿 opaque 字串比對自己」的假驗證）。

### 4.3 決策 B2：P1-C 第一期的 artifact 落地點

第一期（single_shot subagent）的 artifact 落地點最小化：

- artifact 內容存 **Soul data root**（`data_root()/work/artifacts/<sha256>`，由 Domain Core 定義），不是 DSH session。
- Adapter 執行完把 artifact 內容 + hash 交回 Domain Core，Domain Core 計算 `sha256` 得 ref，寫進 HandoffResult.artifact_refs。
- 這讓 refs content-address 在 P1-C 就有真 store 可驗證，D1 自然收斂。

> **注意**：artifact store 的詳細設計（shared/isolated workspace、branch/worktree）是 2B 的 scope（2A §9）。P1-B 只鎖「ref 定址與 store 同批落地」這個決策，不做 2B 的完整 workspace 設計。

### 4.4 決策 B3：artifact content 回傳機制（P1-C 前必須鎖定）

HandoffResult（2A §6 frozen）只有 `artifact_refs` 字串，無 content 欄位。artifact 內容怎麼回傳，三個直覺選項都有 boundary 違反：
- base64 走 bridge → 需擴充 HandoffResult（frozen，不可改）
- adapter 直接寫 Soul data root → adapter 變 durable writer（違反 C3 / 單一 writer 精神）
- Domain Core 讀 adapter 給的路徑 → 檔案系統耦合 + path trust

**鎖定方向**（P1-C work order 前必須選定，推薦選項 1）：
- **選項 1（推薦）：Domain Core 是 artifact store 唯一 writer。** adapter 把 artifact content + hash 交回 Domain Core（經 bridge 的 language-neutral 載體），Domain Core 驗證 hash 後寫 `data_root()/work/artifacts/<sha256>`。adapter 不寫檔案、不碰 durable store。
- 選項 2：明確授權 adapter 寫 artifact store，並擴展 single-writer 檢查到 artifact store（新增 writer check）。

無論選哪個，**adapter 都不得成為 artifact store 的隱性 writer**。此決策是 P1-C 前置，不是 P1-B 可延後。

---

## 5. P1-C — DSH Execution Routing

### 5.1 問題

Python WorkKernel → adapter → 真 DSH primitive 的 transport 怎麼落地？現在是 `spawn node` 跑 mock `.mjs`。

### 5.2 決策 C1：routing 是 adapter 的 transport，Domain Core 不變

- Domain Core（`src/work/`）**零改動**（P1-C 不碰 Domain Core）。
- `src/work_adapter/bridge.py` 的 `WorkExecutionBridge.execute` 從「spawn mock script」升級為「呼叫真 DSH execution」。
- `dsh_adapter/soul-dsh-adapter.mjs` 從 mock 升級為「讀 payload.execution_shape → 呼叫對應 DSH primitive」。

### 5.3 決策 C2：第一期只接 single_shot（subagent）

- P1-C 第一期只實作 `execution_shape == single_shot` → DSH subagent。
- `multi_stage` / `continuous` 在第一期回 `BridgeExecutionError("execution_shape not yet supported")`（fail-closed，不是 silent 降級）。

### 5.4 決策 C3：adapter 不得成為 durable truth owner

- DSH primitive 執行結果 → adapter 轉成 HandoffResult → `consume_handoff` → `kernel.record_handoff`（唯一 durable write 路徑）。
- adapter 不寫 WorkStore / AuthorityStore（M1/M2/M3 後仍成立，P0-1 已證）。

### 5.5 決策 C4：failure 語義沿用 P1-Preflight 三條

- crash / timeout / malformed result → `BridgeExecutionError`（M3）。
- result_type 不對齊 capability → `BridgeExecutionError`（M2）。
- blocked / needs_input → state_transition（M1）。

---

## 6. P1-D — Workflow / Goal Resume Semantics

### 6.1 問題

`multi_stage`（workflow）與 `continuous`（goal）的 resume 語義，尤其 crash-after-write dedup。

### 6.2 決策 D1：resume 只依賴 durable log（承襲 MA-1 §5）

- DSH workflow / goal 是 ephemeral，重啟即失。resume 由 Domain Core 從 WorkEvent log fold 出 resume_state（已有 `store.fold_events` 的 blocked_from / idempotency_keys 邏輯）。
- Adapter 不依賴 DSH child discoverability（MA-1 §5.3）。

### 6.3 決策 D2：blocked 重試 dedup 的獨立機制

- P1-Preflight 已證：重複 blocked handoff → `InvalidTransitionError`（blocked→blocked 非法，正確防護）。
- crash-after-write 場景：若需要「重複 blocked 靜默 skip」而非拋錯，需**獨立機制**（非改 state machine）。
- 決策：**P1-D 才設計這個機制**（例如 blocked handoff 也帶 idempotency key，dedup 查 durable log 命中則 skip 不拋錯）。P1-A/P1-C 不提前做。

### 6.4 決策 D3：DSH goal 只承載 execution state（承襲 MA-1 §6）

- `continuous` 的續輪決策在 Soul durable scheduler，DSH goal 只承載 execution state。
- Adapter 只 translate「續輪 request → goal resume mutation」。

---

## 7. P1-E — Production Adapter Boundary

### 7.1 問題

`dsh_adapter/` 現在在 repo 內（P0-1 mock）。production adapter 依 MA-4 §1.1 是獨立 package，不在 Soul OS repo。

### 7.2 決策 E1：遷移時機 = adapter 穩定 + 有真 DSH primitive 之後

- P1-C 第一期仍在 repo 內開發 adapter（接真 subagent）。
- 遷出 repo 的時機：P1-C/P1-D 的 adapter 穩定、有真 primitive 呼叫、schema mirror 完成之後。

### 7.3 決策 E2：TS/Python schema mirror 與遷出同步

- TS 側 mirror Python 的 schema 驗證（message_type / 必填欄位 / execution_shape / result_type），與遷出 repo 同步做。
- 遷出前，Python 側 pydantic 仍是 protocol 完整性 enforcement（TS 側 mock 可接受）。

---

## 8. 依賴順序與 Gate

```text
P1-A（Execution Target Contract）      ← 本文件已鎖，待 review
  ↓  READY 後
P1-A implementation（schema ExecutionShape + execution.py payload）
  ↓
P1-B（Artifact/Reference Boundary）    ← ref 定址與 store 同批
  ↓
P1-C（DSH Execution Routing）第一期     ← single_shot subagent only
  ↓
P1-D（Workflow/Goal Resume）           ← multi_stage / continuous
  ↓
P1-E（Production Adapter Boundary）    ← 遷出 repo + schema mirror
```

**每個子階段都走同一治理鏈**：decision-complete work order → executor → independent adversarial review → gate → commit/push 才 landing。

> **P1-A implementation 的授權改動明記**：P1-A 要動 `src/work/schema.py`（新增 `ExecutionShape` enum）——這與 P0-1「src/work/ 零改動」、P1-Preflight「僅 kernel.py」的先例不同，是**刻意的 Domain Core 修改**（shape 屬 Domain Core contract）。P1-A work order 必須明列 schema.py 為授權改動檔，並要求「shape 推導函數單元測試」補 A2 的 multi_stage / continuous 分支在第一期無觸發路徑的驗證缺口（否則 dead logic 不可驗證）。

**P1 整體 Gate**：
1. `single_shot`（subagent）真執行 path 全綠，adapter 不成為 orchestration authority。
2. `multi_stage` / `continuous` 的 contract 語義與實作一致（P1-D 後）。
3. No-DSH Survival：拔掉 DSH 後 Domain Core 仍 fold / authorize / resume / persist。
4. Domain Core 零 DSH import 不變。
5. 全部 failure 語義 fail-closed（M1/M2/M3 三條不退化）。

---

## 9. Boundary contract（可實作）

1. **ExecutionShape 是 capability-neutral 的 Domain Core contract**，不是 DSH primitive 名。
2. **execution shape 由 Domain Core 從 Work Object 語義推導**，adapter 只 translate，不自行決定。
3. **adapter 不得自行編排 stage / 決定 resume**（orchestration authority 在 Soul）。
4. **refs content-address 與 artifact store 同批落地**，不提前做假驗證。
5. **routing 第一期只接 single_shot**，multi_stage / continuous 未實作時 fail-closed。
6. **resume 只依賴 durable log**，不依賴 DSH child discoverability。
7. **DSH goal 只承載 execution state**，續輪決策在 Soul durable scheduler。
8. **production adapter 遷出 repo 與 schema mirror 同步**，遷出前 Python pydantic 是 protocol enforcement。
9. **Domain Core 零 DSH coupling 永久不變**。
10. **No-DSH Survival**：拔掉 DSH 後 Domain Core 仍完整可運作。

---

*本文件為 DSH P1 Execution Routing Decomposition，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
