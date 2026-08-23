# DSH-DEV-ENV-0 — Soul OS Multi-Agent Development Loop 使用說明

**工單**：`logs/DSH-DEV-ENV-0-WORK-ORDER.md`（operationalization：把 P1-C2 已 LAND 的單一 role execution 變成可操作的開發環境）
**性質**：single_shot 開發環境（非 multi_stage / continuous——那是 P1-D，不在本文件範圍）
**狀態**：dogfooding 入口（Owner 下 task → 系統走 identity → capability → content → durable WorkEvent 閉環）

---

## 0. 一句話

> **我要讓 Soul OS 做一件事 → `python scripts/dsh_dev_run.py <role> <task>`**

三個 role 各自接單一 task，串接由 Owner 手動驅動（不做 Researcher → Developer → Tester 的自動三階段——那是 P1-D）。

---

## 1. 我要讓 Soul OS 做一件事情 → 下什麼指令？

```text
python scripts/dsh_dev_run.py <role> "<task>"
```

- role ∈ {`researcher`, `developer`, `tester`}
- task 是一句自然語言工作描述（可含多行，雙引號包住）

範例：

```text
python scripts/dsh_dev_run.py researcher "分析 Soul OS 的 test_soul_md_loader.py stale import 問題的根因與修復方案"
```

成功輸出（節錄）：

```text
[run] work_id=3f2a... role=researcher capability=artifact.create
[run] executing real DSH headless ...
=== result ===
work_id     : 3f2a...
role        : researcher
event_type  : artifact_produced
ARTIFACT REF: sha256:9f3c...
fold        : artifacts=1 evidence=0 decisions=1
(done; rerun = new work, not resume)
```

---

## 2. 誰會接？

**role 由指令指定**，Soul OS 從 role 推導 capability 並由 Domain Core 驗證身份：

| role | 進入指令 | capability（role→capability 映射，開發環境配置） | 產出型態 |
|---|---|---|---|
| Researcher | `run researcher "<task>"` | `artifact.create` | text artifact |
| Developer | `run developer "<task>"` | `artifact.create`（contract change 後合法） | text artifact |
| Tester | `run tester "<task>"` | `evidence.create` | evidence（驗證某 artifact ref） |

身份不是 LLM 自稱的：entrypoint 依 role 指定 cwd（`workspaces/<role>/`），DSH session 真的在該 cwd 跑，header.cwd → role 的 binding 由 Domain Core 驗證（借殼 = DENY，fail-closed）。

---

## 3. Researcher 做什麼？

研究 / 分析 / 產出 text artifact。典型 task：「分析 X 的根因與修復方案」、「調查 Y 的現況並提出建議」。

- prompt 模板：把 task 接在「Analyze the task below and produce a text artifact: root cause analysis + concrete proposal」後。
- 產出：`ARTIFACT PRODUCED` WorkEvent + `sha256:<hex>` content-addressed ref（artifact 內容 = 最終訊息文字，ref 由 Domain Core 計算回填）。

## 4. Developer 做什麼？

實作 / 設計 / 產出 text artifact。

> **Contract change（DSH-DEV-ENV-0 §0.5，Owner 拍板 2026-08-23）**：Developer 加入 `artifact.create`——修復 2A §5.1 / 2B §5 / 實務三處不一致（2B §5 明說 developer 對 artifact store 是 write）。**Developer 產 text artifact 現在合法**（P1-C0 enforcement 由 DENY 遷移為 PASS）。

- 本階段 Developer 產的是 **text artifact**（實作方案 / 設計 / 變更提案），不是檔案型 artifact（檔案型 staging→ingest production wiring 是 P1-E，未做）。
- 產出：`ARTIFACT PRODUCED` WorkEvent + `sha256:<hex>` ref。

## 5. Tester 做什麼？

驗證某個 artifact ref、產出 evidence。

- task 需帶上被驗證對象的 ref（`sha256:<hex>`，從 Researcher/Developer 的輸出拿）。
- prompt 模板要求 `evidence_refs` 列出**被驗證的 ref**（不是自己訊息的 ref）。
- Domain Core 逐一 `verify_artifact_ref`（存在性 + hash，P1-C2 D4）；ref 不存在 / hash 不符 → fail-closed。
- 產出：`EVIDENCE PRODUCED` WorkEvent；verdict（PASS/FAIL 結論）留在最終訊息文字，由 Human/Auditor 判定（已知限制：HandoffResult 無 verdict 欄位）。

## 6. 結果去哪裡？

durable truth 全部在 **Soul OS Domain Core**（Soul OS owns the durable work truth）：

- `data/work/work_events.jsonl`：append-only WorkEvent log（create → assign → artifact/evidence produced）
- `data/work/artifacts/<sha256 hex>`：content-addressed artifact 檔案
- fold 出的 current WorkObject（artifacts / evidence / decisions）由 `WorkflowOrchestrator.synthesize` 即時重建

entrypoint 印出的 `ARTIFACT REF` / `EVIDENCE REF` 就是跨 run 串接的 handle（第二段 smoke 用）。

**session log**（執行證據）：`$DSH_HOME/sessions-headless/<projectKey(cwd)>/<encode(session_id)>/session.jsonl`（DSH 落盤，Domain Core 自行開檔讀 header + final message；路徑不可讀 → fail-closed）。

## 7. 失敗怎麼處理？

**fail-closed**：任何執行失敗都不寫 **execution result**（無 artifact/evidence event；既有 create/assign 簿記除外）。entrypoint 依錯誤類別給非零 exit code + 明確訊息：

| exit | 類別 | 錯誤 | 處置（runbook） |
|---|---|---|---|
| 2 | usage | 缺參數 / 未知 role / 空 task | 修正指令重跑 |
| 3 | **infra** | `BridgeExecutionError`（DSH crash / timeout / malformed / 缺 dsh / log 定位失敗 / 未命中 claim 關鍵字） | **重跑**，或檢查 DSH 環境（`dsh` CLI、credential、`$DSH_HOME/sessions-headless` 可讀性、長路徑） |
| 4 | **contract** | `CapabilityNotAuthorizedError`（role 無 capability） | **不重跑**——契約問題，升級給 pro/Owner（正常 config 不該發生；發生代表配置/契約已漂移） |
| 5 | **claim/evidence** | claim 畸形 / header 不符 / 驗證對象不存在或 hash 不符（`ExecutionEvidenceError`，與訊息命中 claim 關鍵字的 `BridgeExecutionError`） | **檢查 task 是否超出 role 能力**（例如 Tester 的 task 沒帶對 ref、agent 亂 claim）後重跑 |
| 1 | unknown | 未分類異常 | 看 traceback，回報 |

> claim 類的分類是訊息關鍵字啟發式（entrypoint 內 `_CLAIM_ERROR_MARKERS`）；`ExecutionEvidenceError` 在 execution path 會被包進 `BridgeExecutionError`（C1.6 `except ... from`），所以 runbook 以訊息內容為準。

**重跑語義**：重跑 = **新 work**（每次 `create_work` → 新 work_id），**不是 resume**（resume 是 P1-D 範疇）。durable truth 不受污染的原因：
- 內容定址：同 content → 同 ref，`write_artifact` 檢查既有檔直接回傳（不產生第二份）
- fail-closed-before-write：驗證不過不寫

**staging/orphan 清理**：entrypoint 啟動時只清 `*.tmp` 孤兒（atomic rename crash 的真垃圾）；`staged-but-uningested` 是 **pending 不是 orphan，不清**（P1-B §3.1 選項 B：crash 後由重跑/ingest 閉合）。

## 8. 怎麼跑一個真實 task？（smoke task）

兩段手動串接的單 role task（測 researcher 產 artifact + tester 產 evidence 兩條路，不觸及 multi_stage）：

**第一段 — Researcher 產分析 artifact：**

```text
python scripts/dsh_dev_run.py researcher "分析 Soul OS 的 tests/test_soul_md_loader.py stale import 問題的根因與修復方案"
```

從輸出抄下 `ARTIFACT REF: sha256:<hex>`。

**第二段 — Tester 驗證該 ref：**

```text
python scripts/dsh_dev_run.py tester "驗證 researcher 剛才的分析 artifact ref sha256:<hex> 存在且內容未被篡改"
```

預期：
- 第一段 exit 0，`ARTIFACT REF` 一行，fold 摘要 `artifacts=1`
- 第二段 exit 0，`EVIDENCE REF` 指向同一 ref，fold 摘要 `evidence=1`

**環境需求**：`dsh` CLI 在 PATH（npm shim 亦可）+ LLM credential（`$DSH_HOME/.credentials.yaml` 或 `DEEPSEEK_API_KEY`）。缺任一 → entrypoint 在 bridge 層 fail-closed（exit 3，infra 類）。

**隔離選項**（不想寫進 repo 預設 `data/` 時）：`SOUL_OS_DATA_DIR=<temp dir>` 指到 temp；session root 可用 `SOUL_OS_DSH_SESSION_ROOT` 覆寫；逾時可用 `SOUL_OS_DSH_TIMEOUT`（秒）覆寫（預設 300）。

---

## 附錄：entrypoint 內部組裝（S1 對照）

```text
WorkflowOrchestrator(kernel=WorkKernel(data_dir=data_root()/"work"))
    + RoleCwdRegistry（註冊 researcher / developer / tester 三 cwd）
    + WorkExecutionBridge（dsh --profile headless + --patch overlay）
    + ArtifactStore(data_dir=data_root()/"work")
    → execute_work_dsh(orchestrator, work_id, role, capability, bridge, registry, store)
    → (message, claim, event, evidence)
```

create_work(Chief) → assign(role) → execute_work_dsh → 印結果（ref / WorkEvent / fold 摘要）。
