# DSH P1-C — Real DSH single_shot Routing

**日期**：2026-08-23
**狀態**：ARCHITECTURE PLAN ONLY — NOT AUTHORIZED（待 Independent Architecture Review）
**上游**：`docs/DSH-P1-EXECUTION-ROUTING.md`（P1 decomposition）、`docs/DSH-P1-ARTIFACT-BOUNDARY.md`（P1-B，READY，commit `d4a57a2`）、`docs/DSH-ADAPTER-BOUNDARY.md`（MA-1）、`logs/DSH-P1-A-WORK-ORDER.md`（已 LAND `83aa389`）

---

## 0. North Star

> **Soul OS owns the durable work truth. DSH owns ephemeral execution. DSH orchestration ≠ Soul orchestration.**

P1-C 把 mock DSH execution 換成**真 DSH agent 執行**（single_shot only），同時證明 adapter 仍是 transport/invoke，不取得 orchestration / artifact authority。

---

## 1. Transport seam 調查結論（P1-C 的前置事實）

DSH 提供官方 **headless one-shot agent run** seam：

```text
dsh --profile headless "<task>"
```

- 跑一個 fresh persisted top-level Agent，提交 task，等 quiescence，最後 assistant 文字寫 stdout，exit 0（completed）/ 1（失敗）。
- one-shot：跑完即退，不需 DSH web 常駐。
- headless agent 疊在 dsh-base 上，dsh-base 已編排完整 subagent 機制——headless agent 可透過內建 `subagent` tool 委派真 in-process DSH subagent，結果併入 final message。
- session flush 落盤 `$DSH_HOME/sessions`（可作第二結果通道）。

**seam 事實（已實讀 `dsh-headless/lib/index.js` 驗證）**：headless runner 的 `agents.create({...})` 只裝 `installModelSelection`，**不裝任何 preset**。`dsh --profile headless` 跑的是 **generic Agent（無 role preset）**——preset mount 只在 web path（`dsh-host-apiproxy`）。這是一個**重要的正面發現**：role 語義不能靠 DSH preset 承載，role authority 一直、也必須在 Domain Core（capability policy），DSH preset 只是 web 呈現概念，不是 execution 的 authority seam。

**替代方案（本階段否決）**：
- `dsh-subagent` 家族：`ctx.subagents.start()` 是 in-process API（需持 Cordis ctx），無 CLI/HTTP 暴露，外部 process 無法直接呼叫。
- `dsh-api-gateway`：HTTP POST /api/session.prompt 存在，但需 `dsh web` 常駐 + loopback trust fence（無認證層）。
- 常駐 plugin（adapter 掛進 web profile，內包 HTTP/IPC endpoint 呼叫 ctx.subagents.start）：架構更重，屬 P1-E（production adapter boundary）。

**P1-C 第一期的 transport = `dsh --profile headless`（one-shot，非常駐）。**

---

## 2. 決策（P1-C 鎖定）

### 2.1 D1：transport seam = headless one-shot

- `WorkExecutionBridge.execute` 從「spawn mock script」升級為「spawn `dsh --profile headless "<task>"`」。
- 保留現有 spawn node → stdin/stdout 的 transport 方向（P0-1 已證的 path），但被 spawn 的從 mock script 換成 dsh headless launcher。
- 常駐 plugin 留到 P1-E。

### 2.2 D2：role 語義由 task prompt 承載，role authority 在 Domain Core（非 preset）

- **headless 無 preset**（seam 事實，見 §1）——role 語義不能靠 DSH preset 承載。
- Soul role（chief / researcher / developer / tester / auditor）+ capability 由 adapter 構造的 **task prompt** 注入（adapter 在 prompt 中聲明「你是 role=X，執行 capability=Y，目標=objective」）。
- **role authority（能不能做某 capability）由 Domain Core 的 capability policy 強制**（`roles.ROLE_CAPABILITIES` + `has_capability`），不在 DSH preset / prompt 層。
- prompt 是 adapter 的實作細節，Domain Core 只發 capability-neutral 的 role + capability + objective。

### 2.3 D3：single_shot = 一次 headless generic Agent run

- `execution_shape == single_shot` → 一次 `dsh --profile headless` run，Agent 是 generic（無 preset），role/capability 語義由 task prompt 承載。
- Agent 內部要不要用 subagent tool 是 **DSH 的 execution 細節（ephemeral）**，Soul 不編排——Soul 只發「誰（role）用什麼能力（capability）做什麼（objective）」，DSH 內部怎麼委派 subagent 是 DSH orchestration。
- `multi_stage` / `continuous` 在第一期 fail-closed（`BridgeExecutionError("execution_shape not yet supported")`），不 silent 降級（P1 decomposition C2）。

### 2.4 D4：artifact.create = Researcher（Owner 拍板的 execution constraint）

- **Owner 拍板（2026-08-23）**：以 2A §5.1 frozen contract 為 canonical authority，`artifact.create` 歸 Researcher。2A 是 frozen/ACCEPTED，2B 是 design，kernel/e2e 是 implementation reality——三者衝突時，implementation 不得反向修改 frozen contract。
- P1-C **不得**透過 role substitution / capability spoofing / adapter-side authorization bypass 解決 artifact.create 衝突。
- P1-C 若出現「Developer 必須直接產 artifact」的證據，那是**新的正式 contract change decision**，另開 governance decision，不藏在 implementation 裡。
- execution role == Soul role：DSH generic Agent 只是 execution mechanism，不重新定義 Soul capability ownership。

**enforcement 缺口（reviewer 實讀確認，P1-C 必須補）**：

- 現有 `kernel.record_handoff` / `consume_handoff` / `execute_work` **零 role↔capability 檢查**（`has_capability` 只在 authority.py 的 Human path 用）。
- 現有測試群 ~19 call sites 全以 Developer + artifact.create 為 happy path——這是 implementation reality 與 frozen contract 的衝突，不是 P1-C 可忽略的既存偏差。
- **enforcement 位置（P1-C 鎖定）**：role↔capability 檢查必須在 **Domain Core 的 handoff 消費路徑**（`kernel.record_handoff` 或 `workflow.consume_handoff`），驗證 `handoff.role` 具備 `result_type` 對應的 capability（artifact → artifact.create、evidence → evidence.create、decision → decision）。這是「execution role == Soul role」的強制落點。
- **這需要授權改 Domain Core**（與 P1 decomposition C1「P1-C 不碰 Domain Core」的表述衝突——見下 D9）。這是 P1-C 前必須解的前置決策，不能 defer。

### 2.5 D5：結構化輸出契約（文字 → HandoffResult）——P1-C 最尖銳的張力

headless 的 stdout 是**自然語言文字**，但 Domain Core 要的是結構化 HandoffResult（2A §6）。解法：

- **prompt-level 結構化契約**：headless task prompt 要求 Agent 以 HandoffResult JSON 格式輸出（adapter 構造 prompt 時注入 JSON schema + 欄位說明）。
- **adapter 解析 stdout JSON**：`json.loads` → `HandoffResult.model_validate`（沿用 P0-1 bridge 的解析路徑）。
- **fail-closed**：非 JSON / JSON 但缺欄位 / 欄位型別錯 → `BridgeExecutionError`（M3 已鎖），不寫 durable state。
- **可靠性風險明記**：LLM 不一定遵守 JSON 格式。P1-C 第一期接受這個風險（fail-closed 即安全預設），若實測成功率低，P1-C-R1 再引入 retry / 二次結構化呼叫（屬後續 hardening，非第一期）。

### 2.6 D6：artifact content 回傳 = staging（消費 P1-B §3.1 選項 B）

- headless Agent 在它的 workspace（cwd，與 data_root disjoint）產出 artifact content。
- adapter 把 content 從 Agent workspace 移到 staging 區（canonical store 之外），Domain Core 驗證 hash 後 ingest 進 artifact store。
- 消費 P1-B D1–D8 全部：refs content-addressed、Domain Core canonical writer、claim→verify、write-ahead + atomic rename、evidence 同一邊界。
- staging 治理（P1-B §3.1 已鎖）：位置 disjoint、role capability 檢查、ingest 原子、tool scope 不含 data_root、清理。

### 2.7 D7：fail-closed 語義沿用 P1-Preflight 三條

- crash / timeout / malformed result → `BridgeExecutionError`（M3）。
- result_type 不對齊 capability → `BridgeExecutionError`（M2）。
- blocked / needs_input → state_transition（M1）。
- headless exit 1 / 空 stdout / 非 JSON → `BridgeExecutionError`。

### 2.8 D8：No-DSH Survival 不變

- 拔掉 DSH 後 Domain Core 仍 fold / authorize / resume / persist（消費 P1-B D8）。
- headless Agent 的產出是 ephemeral execution，artifact content 經 staging → Domain Core ingest 才成 durable。

### 2.9 D9：P1-C 需授權改 Domain Core 補 role↔capability enforcement

- P1 decomposition C1 表述「P1-C 不碰 Domain Core」，但 D4 的 enforcement（role↔capability 檢查）必須在 Domain Core 的 handoff 消費路徑。
- **修正 C1**：P1-C 的「routing transport」不碰 Domain Core；但 P1-C 需一個**獨立的前置小改動**，在 Domain Core 補 role↔capability enforcement（`kernel.record_handoff` 或 `workflow.consume_handoff` 驗證 role 具備 result_type 對應 capability）。
- 這個改動是 2A §5.1 frozen contract 的 **enforcement 補齊**（不是修改 contract，是補上 contract 已宣告但未 enforcement 的檢查），與 P1-Preflight 的 M1/M2 同質。
- P1-C work order 必須把「Domain Core role↔capability enforcement」列為**授權改動檔**（kernel.py / workflow.py），並明列現有 ~19 個 Developer+artifact.create 測試的遷移方式（改為 Researcher 或明確違反時 fail）。

### 2.10 D10：claimed ref → file 存在性驗證（結果可信度）

- headless Agent 可能「聲稱」產出 artifact ref，但實際 content 沒寫到 workspace（LLM 說謊 / 誤報）。
- P1-B D4 的 claim→verify 必須落在**存在性層**：Domain Core 驗證 claimed ref 對應的 content 真的存在（staging 區或 workspace），且 hash 相符。
- 僅驗證「ref 字串格式對」是不夠的——那是格式驗證，不是 content-integrity 驗證。P1-C 鎖定：claimed ref → file 存在性 + hash 驗證，缺一則 fail-closed。

---

## 3. 待 P1-C work order 選定的細項（P1-C 只鎖大方向）

以下不是 P1-C decomposition 的 blocking 決策，是 P1-C work order 必須 decision-complete 的實作細項：

1. **headless task prompt 構造**：adapter 怎麼把 BridgeMessage payload（work_id/objective/role/capability/execution_shape/resume_state）組裝成 headless task + 結構化輸出要求。誰持有 prompt template（adapter 側）。
2. **role/capability 語義注入**：headless 無 preset，role/capability 語義如何在 task prompt 中承載（adapter 側）。
3. **staging 路徑 + 清理機制**（P1-B §3.1 已鎖方向，P1-C 定具體路徑）。**清理規格必須含 staged-but-uningested 檔**（不只是 *.tmp 孤兒）——crash 在 staging 寫入後、ingest 前，staged 檔是孤兒，需啟動掃描清理或標記。
4. **artifact store writer check 落地**：新 store class 或 WorkStore 擴充，store-level `is_durable_writer`。
5. **content 回傳的 command 通道**（P1-B §3 item 5 已分析）：staging 落點 + adapter 如何告知 Domain Core content 就緒（envelope/message-type 選擇）。
6. **結構化輸出的更穩健通道（D5 補充）**：除 stdout JSON 外，考慮 **workspace handoff.json 檔案通道**——headless Agent 把 HandoffResult 寫到 workspace 的 handoff.json，adapter 讀檔案（比解析 stdout 自然語言更可靠）。此為 work order 的 decision-complete 細項，P1-C decomposition 只鎖「fail-closed 是安全預設」。
7. **prompt guardrail（D3 補充）**：task prompt 不得暗示/允許 Agent 自行決定 stage 計畫（那會讓 adapter 經 prompt 偷渡 orchestration authority）。prompt 必須限於「單一 capability 的執行」，不注入 stage 分解/順序/委派策略。
8. **冷啟動成本 trade-off（D1 補充）**：每次 headless 呼叫冷啟動完整 DSH core。P1-C 第一期接受此成本，但必須在 work order 明記為 trade-off（非 P1-E 才發現），並記錄 metering 觀察點（token/cost/latency）。

---

## 4. Boundary contract（可實作）

1. **transport = `dsh --profile headless` one-shot**，非 DSH web 常駐、非常駐 plugin（P1-E 才升級）。
2. **role/capability 語義由 task prompt 承載**（headless 無 preset），role authority 在 Domain Core capability policy，Domain Core 只發 capability-neutral role + capability。
3. **single_shot = 一次 headless generic Agent run**；Agent 內部 subagent 委派是 DSH execution 細節，Soul 不編排。
4. **artifact.create 歸 Researcher（2A §5.1 frozen，Owner 拍板）**；P1-C 不得 role substitution / capability spoofing / adapter-side bypass；role↔capability enforcement 在 Domain Core（D4/D9）。
5. **結構化輸出 = prompt-level JSON contract（或 handoff.json 檔案通道）+ adapter 解析 + fail-closed**（非 JSON → BridgeExecutionError）。
6. **artifact content 走 staging → Domain Core ingest**（消費 P1-B D1–D8），adapter 不寫 artifact store；claimed ref → file 存在性 + hash 驗證（D10）。
7. **fail-closed 三條（M1/M2/M3）不退化**。
8. **Domain Core 零 DSH coupling 永久不變**；HandoffResult（2A §6）不改。
9. **No-DSH Survival**：拔掉 DSH 後 Domain Core 仍完整可運作。
10. **DSH session log / subagent run 是 ephemeral audit sidecar**（MA-2 Q9），不是 durable truth。

---

## 5. Gate（P1-C 通過條件）

1. single_shot 真 headless generic Agent run 全綠，adapter 不取得 orchestration / artifact authority。
2. artifact.create 走 Researcher（2A §5.1），無 role substitution / spoofing / bypass；role↔capability enforcement 在 Domain Core（D4/D9）。
3. 結構化輸出 fail-closed（非 JSON → BridgeExecutionError，不寫 durable）；claimed ref → file 存在性 + hash 驗證（D10）。
4. artifact content 經 staging → Domain Core ingest（消費 P1-B D1–D8），adapter 不寫 artifact store。
5. multi_stage / continuous fail-closed，不 silent 降級；role↔capability enforcement 對 multi_stage/continuous 的 shape 注入有單元測試覆蓋。
6. No-DSH Survival：拔掉 DSH 後 Domain Core 仍 fold / authorize / resume / persist。
7. Domain Core 零 DSH import 不變。

---

*本文件為 DSH P1-C Real DSH single_shot Routing，供 Independent Architecture Review。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。*
