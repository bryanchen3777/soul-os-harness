# DSH P1-C1 — Identity & Handoff Seam Decomposition

**日期**：2026-08-23
**狀態**：ARCHITECTURE PLAN ONLY — NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-P1-C-ROUTING.md`（P1-C decomposition，READY）、`docs/DSH-P1-ARTIFACT-BOUNDARY.md`（P1-B，D1–D8）、`logs/DSH-P1-C0-WORK-ORDER.md`（已 LAND `06a0986`）

---

## 0. North Star & Trust Model（先鎖定信任根）

> **Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.**

### 0.1 Trust Model（reviewer 揪出的核心缺口，P1-C1 首要決策）

**binding 的信任根是 adapter（Soul OS 自己的 transport 程式碼）。**

- adapter 是 Domain Core 與 DSH 之間唯一耦合層（MA-1），是 Soul OS **自己的、可信的**程式碼——它不是敵手。
- P1-C1 的 identity binding 防的是 **惡意 LLM 偽造 role**（LLM 在 prompt 輸出裡自稱 Researcher），**不是**防惡意 adapter（adapter 謊報 header 是另一個 threat model，不在 P1-C1 範圍）。
- 這個 trust model 必須明說，否則「cwd 是 process 層 OS 事實」會被誤讀成「Domain Core 獨立驗證了 process 事實」——實際上 Domain Core 驗證的是「adapter 宣稱的 process 事實」，除非 Domain Core 自行開檔。

### 0.2 決策 T1：Domain Core 自行開檔讀 session log，adapter 只回報絕對路徑

- **adapter 不轉述 header 值**。adapter 執行完只回報「本次 execution 的 session log 絕對路徑」。
- **Domain Core 自行開檔**讀 log 的 header line（JSONL 第一行）+ final assistant message，自行解析 `cwd` / `id` / `createdAt`。
- 這把驗證從「adapter 說 header 是 X」升級為「Domain Core 讀 DSH process 寫在磁碟上的 header 是 X」。
- **極限（明說）**：Domain Core 開檔讀 log，仍擋不住「adapter 偽造整個 log 檔案」——那是信任根極限（adapter 是可信程式碼，惡意 adapter 屬不同 threat model）。

---

## 1. C1-A：Identity / Role Binding（事實 + 決策）

### 1.1 C1-A audit 事實（file:line 已實讀驗證）

| 錨點 | headless seam | config-driven seam | LLM 能偽造？ |
|---|---|---|---|
| sessionId | 硬編碼 `randomUUID()`（index.js:71），**不能外部指定** | **能**（`agents:[{id,sessionId,cwd}]`，dsh-agent-loop:961-971） | 不能（process 層） |
| cwd | `meta.cwd = process.cwd()`（index.js:72），**adapter 可控** | **能**（config 字串，DSH 只驗絕對路徑，不與 process.cwd() cross-check） | 不能（session 建立時定死） |
| agent preset / persona | 純 prompt 層，非 identity | 同 | **能**（LLM 文字） |

- session log header = `{version, id, createdAt, cwd, parentSession, origin, delegationDepth, agentPreset}`（dsh-session-persistence-jsonl:36-48），append-only，`assertStoredIdentity` 驗證 header id/cwd ↔ 路徑（:1345-1356）。
- **關鍵**：bash/fs tool 預設 workdir = `session.header.cwd`（dsh-tool-bash:178-183、dsh-tool-fs:242）→ **role cwd 就是 agent 的實際 workspace**，與 P1-B D7 的 workspace 概念一致。

### 1.2 決策 A1：identity binding = role→cwd 映射（Domain Core 維護，adapter 只讀）

- Domain Core 維護 `role → cwd` 映射（純路徑資料，無 DSH 概念，無 DSH id）。
- adapter spawn 時 cwd = 該 role 的專屬目錄；DSH 把 cwd 寫進 session header（process 層事實）。
- Domain Core 收到 handoff 時，自行開檔讀 header.cwd，cross-check `header.cwd 對應的 role == handoff.role`。
- **防的是惡意 LLM**：LLM 自稱 Researcher 改不了 header.cwd；只有真的在 Researcher cwd 跑的 session 才驗得過。

### 1.3 決策 A2：sessionId 走 config-driven agents（操作優勢，非安全優勢）

- **鎖定 config-driven agents**（`--patch` overlay 或 `$DSH_HOME/cordis.patch.yml` 注入 `agents:[{id, sessionId, cwd}]`），sessionId + cwd 雙雙由 adapter 指定。
- **明確其優勢是操作性的，不是 anti-forgery**：
  1. 身分確定 → log 路徑預先可知（不需 run 後掃目錄找 log，避免同 cwd 多 session 的時序歧義）。
  2. `restore-or-create` → crash 後同 id 重掛（recovery）。
  3. `validateConfiguredAgents` 保證跨 role sessionId 唯一。
- **sessionId 期望值只活在 adapter**（不進 Domain Core 資料層）——見 Q7/§4 #2。
- **fallback**（若 config-driven 第一期不可行）：headless 事後讀回。但 A2 是鎖定方案，fallback 不寫進 boundary contract。

### 1.4 決策 A3：不變量（reviewer 補全，C1-A 規格完成項）

1. **role cwd 彼此 disjoint**（一目錄 → 一 role，否則 binding 塌陷）。
2. **role cwd ∉ data_root 樹**（繼承 P1-B D7，workspace 與 durable truth 分離）。
3. **DSH_HOME 與 role cwd disjoint，且 ∉ tool scope**（否則 agent 可篡改自己的 log——DSH_HOME 若落在 LLM 可寫的目錄，header 可被 agent 改）。

---

## 2. C1-B：Structured Handoff Channel Decision

### 2.1 判斷框架（Owner 鎖定）

> **哪一條通道讓 DSH execution 的結果可靠進入 Domain Core，同時不讓 adapter 變成 artifact authority？**

### 2.2 channel 比較（最終裁決）

| Channel | 判定 | 理由 |
|---|---|---|
| stdout JSON | **否決** | 無 header 身分錨定（stdout 只是文字）；無持久性（pipe 截斷/遺失）；無 path↔id↔cwd 完整性驗證 |
| **session log final assistant message** | **採用** | header 身分錨定 + 增量 fsync append（crash 仍在）+ `assertStoredIdentity` path 完整性 |
| dedicated fd/channel | **否決** | DSH headless seam 未提供 |
| wrapper protocol | **否決** | adapter 包 protocol = adapter 重新掌握權威 |
| handoff.json 檔案 | **否決** | 依賴 LLM 記住寫/寫對路徑/寫合法 JSON，多三層失敗面；authenticity 與 final message 相同（都是 LLM 產物），不存在「檔案比文字更真」 |

> **關鍵澄清（reviewer 揪出）**：內容偽造風險（LLM 謊報 result_type/refs）**不是靠通道選擇解的**，是靠 claim→verify（A1 role→cwd + P1-C0 result_type→capability + P1-B D10 ref 存在性+hash）解的。通道選擇解的只有**身分錨定 + 持久性 + path 完整性**。stdout JSON 和 session log final message 是**同一份 LLM 文字**，「否決 stdout」的正確理由不是「LLM 文字可偽造」（那對 final message 同樣成立），而是「stdout 沒有 header 身分 + 無持久性」。

### 2.3 決策 B1：content 走 session log 的 final assistant message

- **identity meta（cwd/id/createdAt）**：Domain Core 自行開檔讀 header line。
- **結果內容（result_type/refs/decision）**：Domain Core 自行讀 log 的 **final assistant message**（最後一個**非空文字** message，與 headless `summarize()` 的語義一致）。
- **artifact content**：走 staging（P1-B §3.1 選項 B），不進 handoff 文字。

### 2.4 決策 B2：不信任任何「LLM 自行宣稱」的欄位（claim→verify 三層）

- 三層正交驗證，缺一 fail-closed：
  1. **identity**（誰跑的）：header.cwd → role（A1，Domain Core 開檔）。
  2. **capability**（能不能產）：result_type → capability（P1-C0，Domain Core enforcement）。
  3. **content**（產了什麼）：claimed ref → 存在性 + hash（P1-B D10）。
- 通道（stdout vs log）不影響這三層——它只影響 identity 錨定與持久性。

---

## 3. C1-C：Routing 分解（前置 + 施工）

### 3.1 前置（P1-C1 實施前必須完成）

1. **role→cwd 映射的 Domain Core 落地**（A1）：Domain Core 維護 role→cwd 純路徑映射，adapter 只讀。
2. **session log 讀取器（Domain Core 側）**：Domain Core 自行開檔讀 header line + final assistant message（JSONL 解析，非 import DSH）。
3. **staging 治理落地**（P1-B §3.1）：具體路徑 + 清理 + single-writer 擴充到 artifact store。
4. **A3 不變量落地**：role cwd disjoint、∉ data_root、DSH_HOME ∉ tool scope。

### 3.2 施工（C1-C work order 範圍）

1. `WorkExecutionBridge.execute` 升級：spawn `dsh --profile headless "<task>"`（cwd 依 role 設定，config-driven agents 注入 sessionId+cwd）。
2. adapter 執行完只回報 session log 絕對路徑。
3. Domain Core 自行開檔讀 header + final message，重建 HandoffResult claim。
4. Domain Core 三層 cross-check：header.cwd→role（A1）+ result_type→capability（P1-C0）+ claimed ref 存在性（P1-B D10）。
5. staging → Domain Core ingest（P1-B D1–D8）。
6. fail-closed 三條（M1/M2/M3）不退化。

### 3.3 後置（P1-C2 Integration Gate 驗證）

- Researcher artifact.create → PASS；Developer artifact.create → DENY（P1-C0 已證）。
- **借殼攻擊（惡意 LLM 自稱 role）**：header.cwd 對不上 handoff.role → DENY（A1）。
- claimed ref spoof → DENY（P1-B D10）。
- crash / partial artifact recovery（P1-B D6 原子 rename）。

---

## 4. Boundary contract（可實作）

1. **identity binding = role→cwd 映射**（Domain Core 維護，adapter 只讀），防惡意 LLM 偽造 role；信任根 = adapter（Soul OS 自己的 transport 程式碼）。
2. **Domain Core 自行開檔讀 session log**（adapter 只回報絕對路徑，不轉述 header 值）；sessionId 期望值只活在 adapter，Domain Core 資料層不存 DSH id（2A §8.2 / MA-1 §9.2）。
3. **sessionId 走 config-driven agents**（sessionId+cwd 由 adapter 指定，操作優勢非安全優勢）；headless 事後讀回是 fallback，不寫進 contract。
4. **content channel = session log header + final assistant message**；否決 stdout JSON / dedicated fd / wrapper protocol / handoff.json。
5. **claim→verify 三層正交**：identity（header.cwd→role）+ capability（P1-C0）+ content（ref 存在性+hash D10）。
6. **artifact content 走 staging → Domain Core ingest**（P1-B D1–D8），adapter 不寫 artifact store。
7. **A3 不變量**：role cwd disjoint、∉ data_root、DSH_HOME ∉ tool scope。
8. **fail-closed 三條（M1/M2/M3）不退化**。
9. **Domain Core 零 DSH coupling 永久不變**；HandoffResult（2A §6）不改。
10. **No-DSH Survival**：role→cwd 是純路徑資料；session log 讀取屬 execution 側流程（Domain Core 開檔讀 header），Domain Core 的 fold/authorize/resume/persist 不 import DSH、不要求 session log 存在才走 durable path。

---

## 5. Gate（P1-C1 通過條件）

1. identity binding（role→cwd + Domain Core 開檔讀 header）成立：惡意 LLM 自稱 role 被擋掉。
2. 借殼攻擊（header.cwd 對不上 handoff.role）→ DENY。
3. artifact content 經 staging → Domain Core ingest，adapter 不寫 artifact store。
4. claimed ref 存在性 + hash 驗證（P1-B D10）。
5. fail-closed 三條（M1/M2/M3）不退化。
6. No-DSH Survival：拔 DSH 後 Domain Core 不 import DSH、不要求 session log 存在，fold/authorize/resume/persist 仍綠。

---

*本文件為 DSH P1-C1 Identity & Handoff Seam Decomposition，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
