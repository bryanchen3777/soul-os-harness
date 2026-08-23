# 工單：DSH P1-C1 — Real DSH single_shot Routing

**日期**：2026-08-23
**性質**：Phase 1 implementation（可寫 code）——真 DSH execution 施工
**上游**：`docs/DSH-P1-C1-DECOMPOSITION.md`（READY FOR IMPLEMENTATION）、`docs/DSH-P1-ARTIFACT-BOUNDARY.md`（P1-B）、`docs/DSH-P1-C-ROUTING.md`（P1-C）、baseline `9f01c5e`

---

## 0. 鐵律（Owner 鎖定，執行者不得違反）

- **不做 identity architecture 設計創新**。decomposition `9f01c5e` 已把 trust model 收斂，本工單只做 interface adaptation。
- **adapter 不得自行決定 role authority**；sessionId 是 operational correlation，不升格成 Soul identity authority。
- **不接受 adapter 轉述的 cwd/sessionId/role 作為證據**——Domain Core 自行開檔讀 session log。
- **claim→verify 三層正交**（identity / capability / content），任何一層失敗 fail-closed。

---

## 0.1 事實更正（review 實讀 DSH 源碼後，interface adaptation，非設計創新）

review 實讀確認一個 decomposition A2 未驗證的 DSH 機制事實：

> `dsh-headless/lib/index.js:70-72` 的 headless runner **硬編碼** `sessionId: SessionId(randomUUID())` + `meta.cwd = process.cwd()`，**完全不消費 config-driven agents config**（`agents:[{id,sessionId,cwd}]` 是 AgentLoop 常駐 declarative agent 機制，headless runner 不驅動它們）。

**後果**：
- `--patch overlay` 注入 agents config **對 headless 無效**——sessionId 不可由 adapter 預先指定。
- decomposition A2 的「config-driven 預先指定 sessionId」在 headless transport 下**不成立**。

**修正（decomposition A2 的 fallback 升為主力）**：
- **sessionId 不可控**，但 **cwd 可控**（spawn 時 `cwd=` 參數）。
- binding 靠 cwd：spawn cwd 依 role 設定 → DSH 把 `process.cwd()` 寫進 header → Domain Core 開檔讀 header.cwd 驗證 == 該 role 的 cwd。
- log 路徑 `<projectKey(cwd)>/<encode(id)>/`：`projectKey(cwd)` 由 cwd 決定（可控），`encode(id)` 是 randomUUID（不可控）→ **run 後掃目錄找唯一新增的 session 目錄**。

---

## 1. 施工順序（C1.1 → C1.9，依序完成，不跳步）

### C1.1 — Identity Binding Contract（Domain Core）

**新增 `src/work/execution_evidence.py`**（Domain Core，零 DSH import）：
- `ExecutionEvidence`（pydantic model）：`cwd: str`、`session_id: str`、`created_at: str`、`final_message: str`。
- `RoleCwdRegistry`：Domain Core 維護 `role → cwd` canonical mapping（adapter 只讀）。`register(role, cwd)` / `cwd_for(role)` / `role_for(cwd)`。
- `verify_role_binding(role, evidence) -> bool`：`role_for(evidence.cwd) == role`。

### C1.2 — role→cwd configuration

- `RoleCwdRegistry` 預設空；由 execution path 註冊 role→cwd 映射。
- 純路徑資料，不存 DSH id（2A §8.2 / MA-1 §9.2）。

### C1.3 — Session-log reader（Domain Core 開檔讀）

**加在 `src/work/execution_evidence.py`**：
- `read_execution_evidence(log_path) -> ExecutionEvidence`：
  1. 開檔（明文 JSONL，見 C1.4 的 compression:none），讀第一行 header（含 `cwd`/`id`/`createdAt`/`version`）。
  2. **驗證 header.version**（未知 version → fail-closed，格式 drift 防線）。
  3. 遍歷事件找最後一個非空 assistant message（`type == "assistant/message"` 且 text block 非空）。
  4. 解析失敗 / 缺 header / 缺 cwd → `ExecutionEvidenceError`（fail-closed）。
- **格式契約明說**：這讀的是 DSH 的 on-disk session log 格式（header 欄位 + event type），**import 層零 DSH coupling，但格式知識進 Domain Core 是「已知的格式契約」，不是 language-neutral**。header.version 驗證是格式 drift 的防線。

### C1.4 — DSH headless launcher（adapter 側）

**改 `src/work_adapter/bridge.py`**：
- `WorkExecutionBridge.execute` 升級為 spawn `dsh --profile headless "<task>"`，`cwd=` 依 role 設定（binding 錨點）。
- **--patch overlay 注入**（不用全域 `$DSH_HOME/cordis.patch.yml`，避免污染其他 profile）：
  1. `session-persistence-jsonl.compression: none`（否則預設 zstd，檔名 `session.jsonl.zstd`，reader 讀不到明文——review 實讀 `DEFAULT_COMPRESSION="zstd"`）。
  2. **tool scope 排除**（A3.3 落地）：bash/fs tool 的 workdir/scope 排除 `DSH_HOME` 與 `data_root`，防 agent 篡改自己的 session log header。執行者需實讀 `dsh-tool-bash`/`dsh-tool-fs` 的 config 格式，做 interface adaptation。
- **log 路徑定位（事後讀回，sessionId 不可控）**：
  1. spawn 前記錄 `$DSH_HOME/sessions/<projectKey(cwd)>/` 下既有的 session 目錄清單。
  2. run 後 diff 找出唯一新增的 session 目錄（`<encode(id)>/`），取其 `session.jsonl` 絕對路徑。
  3. 若新增目錄不唯一（同 cwd 並行執行）→ fail-closed（時序歧義不可接受）。
  4. adapter 只回報絕對路徑，不轉述 header 值。

### C1.5 — Identity verification（Domain Core cross-check）

**改 `src/work_adapter/execution.py` 的 `execute_work`**：
- bridge.execute 回報 log 路徑 → Domain Core `read_execution_evidence(log_path)` → `verify_role_binding(role, evidence)`。
- `verify_role_binding` 失敗 → fail-closed（不寫 durable）。
- 三層驗證順序：identity（cwd→role）→ capability（P1-C0 已有）→ content（P1-B D10，C1.7 落地）。

### C1.6 — Final-message extraction（role 來源澄清）

- `ExecutionEvidence.final_message` = 最後一個非空 assistant text。
- **HandoffResult.role 的 canonical 值 = `role_for(evidence.cwd)`（binding 決定，process 事實）**，不是 final_message 裡 LLM 宣稱的 role。
- final_message 解析出的 role/result_type/refs 是 **claim**，供 cross-check：claim.role 必須 == binding role，否則 fail-closed（B2 identity 權威 = header.cwd→role）。
- 重建 HandoffResult claim 失敗 / 非結構化 → fail-closed（P1-C D5）。

### C1.7 — Staging → Domain Core ingest + artifact store

**新增 `src/work/artifact_store.py`**（Domain Core，零 DSH import）：
- content-addressed 寫入：`write_artifact(content_bytes) -> ref`（`sha256:<hex>`），write temp + atomic rename（P1-B D6）。
- `verify_artifact_ref(ref) -> bool`：claimed ref → 檔案存在性 + hash 相符（P1-B D10）。
- single-writer 擴充：artifact store 的 writer check（`is_durable_writer`，只有 kernel 能寫）。
- staging 路徑：`data_root()/work/staging/`（canonical store 之外），ingest 後清理。

**DSH execution 不直接取得 Domain Core artifact authority**（staging 不是 canonical store）。

### C1.8 — Negative / bypass tests

- 12 情境測試矩陣（見 §4）。**T1-T8 是 fake-log 單元測試**（構造 fake session log 測 Domain Core 的 verify/reader），**C1.9 是唯一真 DSH integration**。

### C1.9 — Real DSH smoke test

- 一個真正的 `dsh --profile headless` execution 被 Soul OS 接收，經三層驗證後進 Domain Core。
- 真 DSH 環境不可用時 skip（`needs_node` 模式），**但 skip 時必須記錄「config-driven/zstd/tool-scope 可行性未經真 DSH 驗證」的風險註記**。

---

## 2. 範圍（授權改動檔）

**Domain Core（授權改動，零 DSH import 永久不變）**：
- 新增 `src/work/execution_evidence.py`（ExecutionEvidence + RoleCwdRegistry + read_execution_evidence + verify_role_binding）
- 新增 `src/work/artifact_store.py`（write_artifact + verify_artifact_ref + single-writer）

**work_adapter**：
- `src/work_adapter/bridge.py`（spawn dsh headless + --patch overlay + 事後讀回 log 路徑）
- `src/work_adapter/execution.py`（identity verification 接入）

**測試**：
- 新增 `tests/test_work_p1c1_routing.py`（12 情境矩陣）
- `tests/test_work_adapter.py` 的 scope containment 授權集更新（新增 execution_evidence.py + artifact_store.py）

## 3. A3 invariants（可測試條件）

1. **role cwd disjoint**：`RoleCwdRegistry.register` 拒絕兩 role 同 cwd（`RoleCwdConflictError`）。
2. **role cwd ∉ data_root**：`register` 拒絕 cwd 落在 `data_root()` 樹內（`RoleCwdConflictError`）。
3. **DSH_HOME ∉ tool scope**：C1.4 的 --patch overlay 注入 tool scope 排除（DSH 側落地）；Domain Core 側以「零 DSH_HOME 概念 + 只接受 adapter 絕對路徑」測試（grep 零 `DSH_HOME` 於 src/work/）。

## 4. 測試矩陣（12 情境，Owner 鎖定）

| # | 情境 | 預期 | 層級 |
|---|---|---|---|
| T1 | Researcher → Researcher cwd → artifact | PASS | fake-log 單元 |
| T2 | Developer → Developer cwd → artifact | DENY（P1-C0 capability） | fake-log 單元 |
| T3 | Researcher task 在 Developer cwd | DENY（identity binding） | fake-log 單元 |
| T4 | LLM 自稱 Researcher，但 cwd=Developer | DENY（identity binding） | fake-log 單元 |
| T5 | valid identity + invalid artifact ref | DENY（content D10） | fake-log 單元 |
| T6 | valid identity + valid capability + valid ref | PASS | fake-log 單元 |
| T7 | blocked / needs_input | 不觸發 artifact capability gate（M1） | fake-log 單元 |
| T8 | decision | 不受 artifact capability gate 影響（2A §3.1） | fake-log 單元 |
| T9 | cwd 落入 data_root | invariant failure | 單元（registry） |
| T10 | role cwd overlap | invariant failure | 單元（registry） |
| T11 | adapter 提供錯誤 log path | fail-closed | fake-log 單元 |
| T12 | session log header 與 expected binding 不符 | fail-closed | fake-log 單元 |
| — | 真 DSH execution 三層驗證 | PASS | C1.9 smoke（可 skip） |

## 5. 驗收

1. 12 情境測試矩陣全綠（T1-T8/T11/T12 是 fake-log 單元測試）。
2. `src/work/` 零 DSH import（grep），零 `DSH_HOME` 字串（A3.3 Domain Core 側）。
3. artifact store single-writer + write temp + atomic rename + claimed ref 驗證（P1-B D3/D6/D10）落地。
4. `git diff --stat` 只動列名檔案。
5. 全 regression（含 P1-C0 的 256 tests）綠。
6. C1.9 真 DSH smoke：環境可用時 PASS，不可用時 skip 並記錄風險註記。

## 6. 不做（Out of Scope）

- **不做 P1-C2 Integration Gate**。
- **不做** multi_stage / continuous routing（P1-D）。
- **不做** production adapter 移出 repo（P1-E）。
- **不改** `docs/` frozen contract（2A–2D）、`docs/DSH-P1-C1-DECOMPOSITION.md`。
- **不改** `state_machine.py`、`store.py`、`authority.py`、`persistence.py`、`roles.py`、`schema.py`（execution_evidence 和 artifact_store 是獨立新模組）。

## 7. Frozen Contract 注意

- 2A §8.2（不存 DSH id）、MA-1 §9.2（sessionId 只作 reference）→ RoleCwdRegistry 只存 role→cwd 路徑，不存 sessionId。
- Domain Core 零 DSH import 永久不變；session log 格式是「已知的格式契約」（header.version 驗證）。
- No-DSH Survival：fold/authorize/resume/persist 不 import DSH、不要求 session log 存在。

## 8. 回報格式

- 改動檔案清單（精確）
- C1.1-C1.9 各步驟實作方式（簡述，含 --patch overlay 的實際 config、tool scope 排除的 interface adaptation、事後讀回 log 的目錄 diff 邏輯）
- 12 情境測試結果（逐情境 PASS/DENY/skip）
- 完整回歸結果
- 確認 `git diff --stat` 只動列名檔案、`src/work/` 零 DSH import + 零 DSH_HOME 字串
- 剩餘 architectural concerns（含 C1.9 skip 風險註記）
