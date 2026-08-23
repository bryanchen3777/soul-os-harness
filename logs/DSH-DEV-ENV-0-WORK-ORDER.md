# 工單：DSH-DEV-ENV-0 — Multi-Agent Development Loop Operationalization

**日期**：2026-08-23
**性質**：operational hardening（可寫 code）——把已 LAND 的單一 role execution 變成可操作的開發環境
**上游**：P1-C2（commit `97e85bf`，真 DSH E2E 閉環已通）

---

## 0. 鐵律（Owner 鎖定）

- **不做新 architecture decomposition**。這是 operationalization，把 P1-C2 已 LAND 的能力串成可執行的 loop。
- **不碰 P1-D（multi_stage/continuous）**。本工單是 single_shot 的 operational 化，三個 role 各自接 task；「Researcher → Developer → Tester」的**自動**三階段協作是 multi_stage，屬 P1-D，不在本工單。
- **不重新設計 identity / capability / handoff contract**。只消費 `execute_work_dsh` + `RoleCwdRegistry` + `ArtifactStore` 的既有 API。
- **retry 不改變 task semantics**（Operational resilience 要求）。

## 0.5 前置：Contract Change（Owner 拍板，本工單第一步）

review 揪出 blocking：**developer 在現契約下無任何合法產出型態**（`DEVELOPER` capabilities 無 artifact.create / evidence.create，產 artifact 被 P1-C0 DENY）。Owner 拍板**開正式 contract change：給 developer artifact.create**——定性「修復 2A §5.1 / 2B §5 / 實務三處不一致」（2B §5 明說 developer 對 artifact store 是 write），非遷就實務。

**落地（工單前置，第一步做）**：
- `src/work/roles.py` 的 `ROLE_CAPABILITIES`：`Role.DEVELOPER` 加 `"artifact.create"`。
- **遷移 P1-C0 相關測試（精確 4 檔 5 測試，reviewer 已核實）**：
  - `tests/test_work_roles.py`：matrix 對齊測試 ×2（developer 的 capability 集合加入 artifact.create）。
  - `tests/test_work_p1c0_enforcement.py`：`test_developer_artifact_create_denied_no_durable_write` → 改為「developer 產 artifact → PASS」正控制。
  - `tests/test_work_p1c1_routing.py`：t2（developer artifact DENY）→ PASS。
  - `tests/test_work_p1c2_integration.py`：test #2（developer artifact DENY）→ PASS。
  - 借殼/越權 deny path 語義不變（無 capability 的 role 仍 DENY；developer + evidence.create 仍 DENY）。
- **contract 文件同步（歸屬：執行者同步更新）**：`docs/DSH-WORK-CONTRACT.md` §5.1 的 Developer 列加入 `artifact.create`（這是正式 contract change 的文件落點，與 roles.py 保持一致，避免 code↔doc 產生新的不一致）。`docs/DSH-WORKSPACE-DESIGN.md` §5 已是「Developer write artifact store」，無需改。
- 這是 frozen contract 的正式變更，已在 ENGINEERING_STATE 記錄 Owner 拍板定性。

---

## 1. 目標

讓 Owner 能下達一個 Soul OS development task，指定 role（Researcher / Developer / Tester），系統走完整閉環（identity → capability → content → durable WorkEvent），把可驗證結果送回。完成後正式進入 **dogfooding / self-development** 階段。

## 2. Scope（四個範圍，Owner 鎖定）

### S1 — Run entrypoint

新增 `scripts/dsh_dev_run.py`（或等價單一入口）：

```text
python scripts/dsh_dev_run.py <role> <task>
```

- role ∈ {researcher, developer, tester}。
- 內部串起：`WorkKernel` + `WorkflowOrchestrator` + `RoleCwdRegistry`（註冊三個 role 的 cwd）+ `WorkExecutionBridge` + `ArtifactStore`。
- 流程：create_work（Chief）→ assign(role) → `execute_work_dsh(...)` → 印出結果（ref / WorkEvent / fold 摘要）。
- 失敗（BridgeExecutionError / CapabilityNotAuthorizedError / ExecutionEvidenceError）→ 非零 exit + 明確錯誤訊息，**不寫 durable state**（既有 fail-closed）。

### S2 — 三個基本 agent role

- 三個 role 的 **cwd**（role→cwd 映射，A3 不變量：disjoint、∉ data_root）+ **task prompt template** + **output expectations** + **capability 映射**。
- **capability 映射（decision-complete，review 揪出的缺口）**：
  - `researcher` → `artifact.create`（產 artifact）
  - `developer` → `artifact.create`（產 artifact，**contract change 後合法**）
  - `tester` → `evidence.create`（產 evidence）
- 這些是**開發環境配置**，不是 Soul OS contract。放在一個 config 檔（如 `configs/dsh_dev_roles.yaml`）或 entrypoint 內建，由執行者選定（interface adaptation）。
- 不做第四個 role（Auditor/Chief 等），先三個。

### S3 — Operational resilience

- session log 落盤（DSH 已做，確認路徑可讀）。
- execution failure handling：timeout / non-zero exit / malformed → 既有 fail-closed，entrypoint 轉成可讀錯誤 + 非零 exit。
- 失敗類型區分（README Q7 的 runbook 基礎）：
  - `BridgeExecutionError`（infra：crash/timeout/malformed）→ 重跑或檢查 DSH 環境。
  - `CapabilityNotAuthorizedError`（契約：role 無 capability）→ 不重跑，升級給 pro/Owner。
  - `ExecutionEvidenceError`（claim 畸形 / header 不符）→ 檢查 task 是否超出 role 能力。
- stale/orphan staging cleanup：**只清 `*.tmp` 孤兒**（真垃圾）；`staged-but-uningested` 是 pending 不是 orphan，**不清**（P1-B §3.1 選項 B：crash 後由重跑/ingest 閉合）。當前 staging 未 wire，無害；語義標籤正確即可。
- 可重新執行 task：**明說「重跑 = 新 work」**（entrypoint 每次 create_work → 新 work_id，跨 run 的 handoff dedup 不觸發）。durable truth 的保護是：內容定址（同 content → 同 ref，write_artifact 檢查既有檔回傳）+ fail-closed-before-write。**不是**「同 work resume」——blocked 後重跑也是新 work，不是 resume（resume 是 P1-D 範疇）。

### S4 — Developer-facing README

新增 `docs/DSH-DEV-ENV-USAGE.md`，回答 Owner 鎖定的 8 問：

1. 我要讓 Soul OS 做一件事情 → 下什麼指令？（`python scripts/dsh_dev_run.py <role> <task>`）
2. 誰會接？（role 由指令指定）
3. Researcher 做什麼？（研究/產 artifact）
4. Developer 做什麼？（實作/產 artifact——contract change 後合法，2A §5.1 修復三處不一致）
5. Tester 做什麼？（驗證/產 evidence）
6. 結果去哪裡？（durable WorkEvent + artifact store ref）
7. 失敗怎麼處理？（fail-closed + 三類錯誤的區分處置 runbook：infra 重跑 / 契約升級 / claim 檢查 task）
8. 怎麼跑一個真實 task？（smoke task 範例）

## 3. Smoke task（驗證「系統能不能真的開始自己生產 Soul OS」）

Owner 拍板（review 後）：原「清理 stale test」是 repo 檔案修改 + 三 role 串接，超出 single_shot + 文字 artifact 的 loop 能力。**改為兩段手動串接的單 role task**：

- **第一段**：`run researcher "分析 Soul OS 的 test_soul_md_loader.py stale import 問題的根因與修復方案"` → researcher 產分析 artifact（純文字）。
- **第二段**：`run tester "驗證 researcher 剛才的分析 artifact ref"` → tester 產 evidence（驗證該 artifact ref 存在）。

兩段由 Owner 手動串接（第一段的 artifact ref 從輸出拿，餵給第二段）。這測到 researcher 產 artifact + tester 產 evidence 兩條路，不觸及 multi_stage（每段都是獨立 single_shot work）。smoke task 用真 DSH（環境可用時），驗證 loop 真的能下 task → 接 role → 產結果 → 回 Domain Core。

## 4. 不做（Out of Scope）

- **不做** multi_stage / continuous（P1-D）——本工單不自動串 Researcher→Developer→Tester，三個 role 各自接 task，串接由 Owner 手動驅動。
- **不做** production adapter 移出 repo（P1-E）。
- **不做** 檔案型 artifact 的 staging→ingest production wiring（仍是文字型 artifact = final_message）。
- **不做** new role（Auditor 等）。
- **不改** identity / handoff contract（除 §0.5 的 contract change 給 developer artifact.create 外）。

## 5. 驗收

1. `run <role> <task>` 對 researcher / developer / tester 三個 role 都能跑（真 DSH；developer 在 contract change 後合法）。
2. 三層 claim→verify 不退化（借殼 / 無 capability role 越權 / 亂 claim ref 仍 fail-closed；developer 產 artifact 現在 PASS）。
3. 失敗（crash/timeout/malformed/deny）→ 非零 exit + 明確訊息，不寫 durable。
4. 可重跑 task（= 新 work）不污染 durable truth（內容定址 + fail-closed 保護）。
5. 全 work regression 綠（302 tests 不退化 + contract change 遷移後）。
6. README 回答 8 問（含失敗 runbook），且兩段 smoke task 能照 README 跑通。

## 6. 回報格式

- 改動檔案清單
- S1 entrypoint 的串接方式（registry/bridge/artifact_store 怎麼組裝）
- S2 三個 role 的 config 內容（cwd + prompt template）
- S3 resilience 處理（失敗/清理/重跑）
- S4 README 位置 + 8 問覆蓋
- smoke task 結果（真 DSH 或 skip 風險）
- 完整回歸結果
- 剩餘 architectural concerns
