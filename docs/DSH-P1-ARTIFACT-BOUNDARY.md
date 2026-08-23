# DSH P1-B — Artifact / Reference Boundary

**日期**：2026-08-23
**狀態**：ARCHITECTURE PLAN ONLY — NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-P1-EXECUTION-ROUTING.md`（P1 decomposition，READY FOR IMPLEMENTATION）、`docs/DSH-WORKSPACE-DESIGN.md`（2B，DESIGN）、`docs/DSH-WORK-CONTRACT.md`（2A，CONTRACT ACCEPTED）、`docs/DSH-SOUL-OS-MIGRATION-ARCHITECTURE.md`（MA-2）、`logs/DSH-P1-A-WORK-ORDER.md`（已 LAND，commit `83aa389`）

---

## 0. North Star

> **Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.**

P1-B 要解的不是「要不要 hash refs」，而是 artifact 的 **ownership boundary**：

> **artifact content 的 canonical writer 與 ref 的 canonical 宣告者，都是 Domain Core；adapter 只 transport content，不成為 artifact authority，不成為 durable truth owner。**

---

## 1. 核心張力（為什麼 P1-B 不能跳過）

### 1.1 現有 frozen contract 的表面張力

- **2A §3.1**：`artifacts[]` 是「內容產出」，Specialist 產生，**內容定址、可 hash、可回滾**。
- **2B §4**：Artifact Store 是 durable/result state（不是 working state），`artifact = { hash, type, path, producer_role, provenance }`，內容定址 sha256；`evidence = { artifact_hash, verdict, producer_role, provenance }`。
- **2B §5**：Developer 對 artifact store 是 **write**，Tester/Auditor write(evidence)。

表面看「Developer 可寫 artifact store」似乎授權了某個執行者直接寫。**但 2B §5 的「Developer」是 Soul role（capability `artifact.create`），不是 DSH adapter。** 2B §5 說的是「誰有權限要求寫 artifact」（role），P1-B 要鎖的是「寫 artifact store 的實際寫入動作由誰執行」（Domain Core，不是 adapter）。

### 1.2 危險的假收斂（必須避免）

```text
DSH subagent → artifact content
        ↓
adapter 自己 hash / 存 / 宣告 ref      ← 危險：adapter 變 artifact authority
        ↓
WorkEvent.refs                         ← 危險：adapter 變 durable truth owner
```

這看起來乾淨，但會讓 adapter 慢慢變成 artifact authority + durable truth owner，侵蝕 MA-1 §10.2（Domain Core 零 DSH coupling）與 MA-4 已鎖死的 boundary。

### 1.3 現狀（P0-1/P1-A 已存在的權宜）

現有 `kernel.record_handoff` 直接信任 adapter 提供的 `HandoffResult.artifact_refs` 字串（`kernel.py` payload `{"artifact": {"refs": [...]}}` + `provenance.output_refs`）。mock adapter（`soul-dsh-adapter.mjs`）自己算 `mock:sha256:<hex>` 宣告 ref。**這是 mock 階段的權宜，P1-B 必須把 ref 的 canonical 宣告權收回 Domain Core。**

---

## 2. 決策（Domain Core 是 artifact authority）

### 2.1 D1：refs 是 content-addressed identity，不是 opaque reference

- refs 必須是 `sha256:<hex>` 形式的內容定址，由 **Domain Core 從 artifact content 計算**。
- 不再允許 adapter 自己宣告任意 ref 字串（P0-1 mock 的權宜在此終止）。
- 與 2A §3.1「內容定址、可 hash」、2B §4「內容定址 sha256」一致。

### 2.2 D2：artifact content 的 canonical writer 是 Domain Core

- adapter 把 artifact **content**（bytes）+ metadata 經 bridge 交回 Domain Core，**不直接寫 artifact store 檔案系統**。
- Domain Core 計算 sha256 → ref → 驗證 content 完整性 → 寫入 artifact store。
- adapter 不成為 artifact store 的隱性 writer（呼應 P1 decomposition §4.4 B3 選項 1）。

### 2.3 D3：artifact store 是 durable store 的一部分，受 single-writer 保護

- artifact store 的寫入與 WorkEvent log 一樣，只有 kernel（`DURABLE_WRITER`）能寫，store-level enforcement。
- 這是把 single-writer 檢查**擴充到 artifact store**（新 writer check），不是另起一套權限。

### 2.4 D4：refs 不得由 adapter 自己宣稱（claim → verify）

- HandoffResult（2A §6 frozen）仍有 `artifact_refs` 欄位——**不改 frozen contract**。
- 但 adapter 回傳的 `artifact_refs` 重新定位為 **claim（宣稱）**，不是 canonical ref。
- Domain Core 收到 claim 後，從 content 重新計算 hash，**驗證 claim == 自己算的 ref**：
  - 一致 → 接受，以 Domain Core 計算的 ref 為 canonical，寫入 WorkEvent。
  - 不一致 → 拒絕（fail-closed，`BridgeExecutionError`），不寫 durable state。
- 這讓「ref 的 canonical 宣告者」實質上是 Domain Core：adapter 只能 claim，Domain Core verify 後才接受。

### 2.5 D5：artifact content 必須先進 durable store，才形成 WorkEvent

write-ahead 順序：

```text
adapter → content + claim
        ↓
Domain Core 計算 hash → ref
        ↓
Domain Core 驗證 claim == ref（不符 → 拒絕）
        ↓
Domain Core 寫 artifact store（content 落盤）
        ↓
Domain Core 構造/接受 HandoffResult → record_handoff → WorkEvent
```

- artifact store 寫入在 WorkEvent append **之前**。
- WorkEvent.refs 只引用已落盤的 content-addressed ref，不引用未落盤的 claim。

### 2.6 D6：crash recovery 與 dedup 閉合（內容定址 + 原子寫入）

**原子性前提（critical）**：內容定址冪等只在「寫入是原子的」下成立。檔案系統一般寫入非原子，crash 於 partial write 會產生 partial 檔案、重算得 partial hash、寫到錯誤路徑、與預期 ref 不符。因此 artifact store 寫入必須是 **write temp + atomic rename**：

1. 寫入 `<sha256>.tmp`（temp 檔）。
2. 完整寫入後 `os.replace` 原子 rename 到 `artifacts/<sha256>`。
3. crash 於 temp 寫入階段 → temp 檔成孤兒，但 canonical 路徑 `artifacts/<sha256>` 從未出現 → 重跑重寫，不受影響。
4. rename 是原子的 → canonical 路徑要嘛完整存在、要嘛不存在，無 partial 狀態。

| 情境 | 閉合方式 |
|---|---|
| crash 於 temp 寫入（未 rename） | canonical 路徑不存在，重跑重寫；孤兒 temp 由清理機制處理 |
| crash-after-artifact-write / crash-before-WorkEvent | content 已原子落盤但 WorkEvent 未寫。重跑時重算 hash 得**同一 ref**，寫入冪等（同 content 同路徑）；WorkEvent dedup 由既有 idempotency key 閉合 |
| duplicate artifact（同 content） | 同 content → 同 hash → 同 ref，不產生第二份 |
| adapter claim 錯 hash | D4 驗證失敗，fail-closed，不落盤 |

**關鍵**：內容定址（sha256）+ 原子 rename 讓 artifact 寫入冪等且無 partial 狀態，crash recovery 靠「重算 hash + 檢查 canonical 路徑是否存在」判斷，不需額外「寫了沒」狀態。

### 2.7 D7：P1-C 的真 subagent output 不產生新的 durable truth ownership

- 真 subagent 產出的 content 必須走**同一條 path**（adapter transport → Domain Core 驗證 + 寫入），不得有第二條「adapter 直接寫 store」的路。
- DSH session log / tool result 仍是 ephemeral audit sidecar（MA-2 Q9），不是 artifact store。
- 這守住 MA-2「Soul-owned vs DSH execution artifact 分離」：artifact content 是 Soul-owned（durable），DSH 執行痕跡是 ephemeral。

**Enforcement（不是 policy statement）**：真 subagent 持 bash/fs/git tool，若其 workspace 與 data_root 不 disjoint，subagent 本身就是隱性第二 writer（比 adapter 更直接）。P1-C/E 必須落地：
1. subagent 的 workspace（isolated worktree）與 `data_root()` **disjoint**（不同目錄樹）。
2. subagent tool scope（bash/fs）**不含 `data_root()`**，只能在 workspace 內操作。
3. P1-E（sandbox 層）正式落地；P1-C 第一期至少以 workspace 路徑隔離保證。

### 2.8 D8：evidence 走同一 claim → verify 邊界

- kernel.record_handoff 對 EVIDENCE 同樣照單全收 `handoff.evidence_refs`（現狀無驗證，`kernel.py` evidence 分支直接信任 refs 字串）。
- evidence（2B §4：`{ artifact_hash, verdict, producer_role, provenance }`）的 `artifact_hash` 必須：
  1. 指向已落盤的 canonical ref（Domain Core verify 該 ref 存在於 artifact store）。
  2. claim → verify：Tester 回傳的 `evidence.artifact_hash` 是 claim，Domain Core 驗證其指向的 ref 已落盤且 hash 相符。
- evidence 本身也是 content（小結構化資料），走同一條「adapter transport content → Domain Core 計算/驗證 → 寫入 → WorkEvent」path。
- 與 D1–D5 對稱：artifact 的 canonical writer 是 Domain Core，evidence 的 canonical writer 也是 Domain Core。

> 若 P1-C 第一期只做 artifact（single_shot developer），evidence 的 claim→verify 可列為 P1-C requirement 而非第一期實作；但 decomposition 必須鎖定「evidence 與 artifact 同一邊界」，不得在 P1-C 時遺漏。

---

## 3. 待 P1-C work order 選定的細項（P1-B 只鎖大方向）

以下不是 P1-B 的 blocking 決策，是 P1-C work order 必須 decision-complete 的實作細項。P1-B 鎖「canonical writer = Domain Core」這個不變量，細項在 P1-C 選定。

1. **content transport mechanism**：content 怎麼從 adapter 傳回 Domain Core？
   - 選項 A（base64 內嵌 bridge response）：**已否決**——bridge response 就是 HandoffResult（2A §6 frozen，無 content 欄位），內嵌需擴充 frozen contract（P1 decomposition §4.4 B3 已判不可改）。
   - 選項 C（DSH session 側 reference，Domain Core 另路讀取）：**已否決**——path trust（P1 decomposition §4.4 B3 已判違反 boundary）。
   - 選項 B（staging）：**實質唯一選項**。adapter 把 content 寫到 staging 區（canonical store 之外），Domain Core 驗證 hash 後**移入**（ingest）canonical artifact store，ingest 後清理。
   - **P1-B 鎖定 staging 治理（P1-C 必須遵守）**：
     - staging 位置在 canonical artifact store 之外，與 `data_root()/work/artifacts/` disjoint。
     - staging 寫入受 role capability 檢查（不是無治理的檔案系統寫入面）。
     - ingest 是原子的（write temp + atomic rename，D6）。
     - subagent tool scope 不含 `data_root()`（D7）。
     - ingest 後清理 staging；孤兒 staging 由清理機制處理。
2. **artifact store 路徑**：`data_root()/work/artifacts/<sha256>`（P1 decomposition §4.3 B2 已提出）。
3. **single-writer 檢查擴充**：artifact store 的 writer check 具體怎麼落地（新 store class 或 WorkStore 擴充）。
4. **evidence 的 artifact_hash 指向**：見 D8（claim → verify + 存在性驗證），Tester/Auditor 的 evidence 指向已落盤 ref。
5. **content 回傳通道的 contract 觸碰分析**（P1-C 必須明列授權）：
   - `HandoffResult`（2A §6）：**frozen 不可改**（不可加 content 欄位）。
   - `BridgeMessage` envelope（migration plan §3.2 crossing-event contract）：required 欄位不可改；payload 是 generic dict，**可擴充**（P1-A 已示範）。
   - 現行 bridge inbound 是 `HandoffResult`（stdout 一行 JSON），**無 content 回傳位置**。content 回傳要嘛改 response 封套（新 wrapper，非 frozen 但屬 bridge protocol 變更）、要嘛新增 inbound BridgeMessageType、要嘛 staging（§3.1 選項 B）。
   - **口徑澄清**：content 的「落點機制」（staging，§3.1 選項 B）與「command 通道選項」（response 封套 / BridgeMessageType，承載 content 的 reference 或 metadata）是**正交的兩個問題**。staging 解決「content bytes 放哪」，command 通道解決「adapter 如何告知 Domain Core content 已就緒」。P1-C work order 必須分別明列這兩軸的授權，不可混為一談。

---

## 4. 上游 frozen contract 之間的已知不一致（P1-C 前必須定錨）

P1-B 不修改 frozen contract，但必須**明記** reviewer 發現的三處不一致，供 P1-C work order 定錨，避免執行者踩雷：

1. **2A §5.1 vs 2B §5 vs 實務的 `artifact.create` 歸屬**：
   - 2A §5.1 矩陣：`artifact.create` 只給 Researcher，Developer 沒有。
   - 2B §5 權限矩陣：Developer 對 artifact store 是 write（且矩陣缺 Researcher 列）。
   - 現有 kernel/e2e 實務：Developer + `artifact.create`。
   - **P1-B 定位**：這是 2A 與 2B 之間的上游衝突，P1-B 不擅自裁定。P1-C work order 若讓 Developer 產 artifact，必須先由 Owner/主大腦拍板「artifact.create 應屬哪個 role」，或明確用哪一份 contract 為準。P1-B 的 D2（canonical writer 是 Domain Core）與 role 歸屬無關——role 決定「誰有權要求寫」，kernel 執行「實際寫入」，兩者正交。

2. **2B 自身狀態**：2B header 是「DESIGN — NOT AUTHORIZED（待 Owner 拍板）」。P1-B 引用 2B §4/§5 作為 artifact store 的設計參考，但**不把 2B 當 frozen anchor**——2B 的 artifact 模型是方向性參考，P1-B 的 D1–D8 才是 P1-C 的執行約束。

3. **2B §2「shared artifact store | producer 可寫」**：與 §5 同一 re-interpretation 問題（「可寫」指 role capability，非 adapter 檔案系統寫入），P1-B 的 D2/D3 已鎖定實際寫入由 Domain Core 執行。

---

## 5. Boundary contract（可實作）

1. **refs 是 content-addressed identity**（`sha256:<hex>`），由 Domain Core 計算，不是 adapter 宣告的 opaque reference。
2. **artifact content 的 canonical writer 是 Domain Core**，adapter 只 transport content，不寫 artifact store 檔案系統。
3. **artifact store 受 single-writer 保護**（store-level writer check），只有 kernel 能寫。
4. **adapter 的 artifact_refs 是 claim，Domain Core verify**——hash 不符 → fail-closed 拒絕。
5. **artifact content 先進 durable store，才形成 WorkEvent**（write-ahead，WorkEvent.refs 只引用已落盤 ref）。
6. **crash recovery 靠內容定址 + 原子 rename**（write temp + `os.replace`），不依賴額外「寫了沒」狀態。
7. **真 subagent output 走同一條 path**，無第二條「adapter 直接寫 store」的路。
8. **Domain Core 零 DSH coupling 永久不變**；HandoffResult（2A §6）不改。
9. **No-DSH Survival**：拔掉 DSH 後 Domain Core 仍能計算/驗證/寫入 artifact 並 fold。
10. **2B §5 的 role 權限不變**：Developer/Tester/Auditor 的 artifact.write 能力仍是 Soul role 的 capability，P1-B 只鎖「實際寫入動作由 Domain Core 執行」。

---

## 6. Gate（P1-B 通過條件）

1. refs 是 content-addressed（`sha256:<hex>`），Domain Core 計算，adapter 不能宣告任意 ref。
2. artifact content 只由 Domain Core 寫入 store，adapter 無寫入路徑。
3. adapter claim 錯 hash → fail-closed，不落盤。
4. artifact 落盤在 WorkEvent 之前，crash recovery 靠內容定址 + 原子 rename 冪等（無 partial-write）。
5. No-DSH Survival：拔掉 DSH 後 Domain Core 仍能 hash/verify/store/fold。
6. Domain Core 零 DSH import 不變，HandoffResult 不改。

---

*本文件為 DSH P1-B Artifact / Reference Boundary，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
