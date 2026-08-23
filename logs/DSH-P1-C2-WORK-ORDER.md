# 工單：DSH P1-C2 — Integration / Boundary Gate

**日期**：2026-08-23
**性質**：Phase 1 implementation（可寫 code）——真 DSH E2E 閉環，P1 的最後一環
**上游**：P1-C1-R（commit `041dad6`，真 DSH single_shot 已通）、`docs/DSH-P1-ARTIFACT-BOUNDARY.md`（P1-B）、`docs/DSH-P1-C1-DECOMPOSITION.md`

---

## 0. 快車道鐵律（Owner 鎖定，不做完美架構）

- **目標不是「完成 P1」，是「讓 DSH 成為 Soul OS 的自我開發工廠」**。做完這輪就停止「為了完整而完整」的設計，剩餘 hardening 邊用邊做。
- **不做新 architecture decomposition**。這份工單就是決策，執行者照做。
- 三層 claim→verify 已就緒（P1-C1-R 的 identity + P1-C0 的 capability + P1-B D10 的 content），C2 只把缺失的「content transport」補上，讓 E2E 真的跑起來。

---

## 1. 核心決策（pro 拍板，執行者照做）

### D1：artifact content = final_message（快車道，避開工具迴圈）

- C1.9 實測發現：要求 agent 用工具算 sha256 的 task 會讓 headless agent 進入工具迴圈（240s 不結束、反覆寫 probe 檔）。
- **C2 第一期：artifact content 就是 agent 的 final_message 文字**（session log 裡最後一個 assistant text，Domain Core 已能讀到）。
- Domain Core 在 `execute_work_dsh` 裡：`final_message.encode()` → `write_artifact` → 得 ref。
- 這是**文字型 artifact**（報告/code/決策紀錄），不是檔案型（patch/test report）。檔案型 artifact 留到「開發環境收尾」輪（邊用邊做），不阻塞本輪。

### D2：write_artifact 接入 + ref 由 Domain Core 回填（claim→verify 正確語義）

- 現狀：P1-C1-R 的 content 層只「驗證」已落盤 ref，沒「寫入」。
- **關鍵語義（review 揪出的自指矛盾，決策已定）**：content = final_message 時，`artifact_refs` 是 final_message 裡的一個欄位，而 ref = hash(final_message) 包含該欄位本身——LLM 無法預知自己文字會被 hash 成什麼（sha256 fixpoint 概率≈0）。**所以 agent 不該、也無法聲稱 ref。**
- C2 的正確 flow：`execute_work_dsh` 當 `result_type == artifact` 且 `status == done` 時：
  1. `ref = artifact_store.write_artifact(evidence.final_message.encode("utf-8"), DURABLE_WRITER)`。
  2. **Domain Core 回填**：`claim.artifact_refs = [ref]`（覆寫 agent 的 claim）。
  3. **防偽語義保留（P1-B D4）**：若 agent 在 final_message 裡聲稱了 artifact_refs，且聲稱值 ≠ `[ref]` → fail-closed（亂 claim 可拒）。但 agent 被 task prompt 指示**不聲稱 ref**（ref 由 Domain Core 算），正常 case 聲稱欄位為空或省略 → Domain Core 回填。
- **task prompt 相應修改**：`bridge.py` 的 task 構造移除「list the sha256 refs」要求（現有 `_build_task_text` :456-458 要改），改為「產出內容本身即可，ref 由系統計算」。**同時改 claim_form 模板**（bridge.py artifact 分支的 `"artifact_refs": ["sha256:<hex>"]` 示範也要移除/改空，否則 agent 照模板填 ref → 聲稱 ≠ canonical → fail-closed → E2E 紅；evidence 分支保留指向被驗證對象的語義）。
- **聲稱 vs 回填的判斷順序（實作時寫清）**：`artifact_refs == []` 或省略 = 未聲稱 → 回填 `[ref]`；`artifact_refs` 非空且 ≠ `[ref]` → fail-closed（防偽）。D3 的「回填後空 → fail」在此順序下自然成立（回填後不可能空）。
- **staging→ingest 機制保留但不 wire**（content = final_message 是 Domain Core 持有的 bytes，不需 staging 中轉；staging 是檔案型 artifact 的機制，留開發環境收尾輪）。

### D3：回填後空 refs 下限（reviewer 記帳）

- `result_type == artifact` + `status == done` + **回填後** `artifact_refs == []` → fail-closed（空產出是錯的；正常 case Domain Core 已回填 [ref]，不會空）。
- 加在 `execute_work_dsh` 的 content 層：artifact 回填後必須有至少一個 ref。

### D2-遷移：P1-C1 既有 content 層測試的遷移路徑

- P1-C1 的 content 層測試寫法是「測試預先 write_artifact(content) 得 ref，再把 ref 塞進 claim」——C2 改語義後（ref 由 Domain Core 回填），這些測試的 claim 構造要改為「claim 不聲稱 ref，Domain Core 回填後 assert claim.artifact_refs == [ref]」。
- 執行者需逐個遷移受影響的 content 層測試（fake-log 模式），確保 291 tests 全綠（遷移後）。

### D4：evidence 的 claim→verify（P1-B D8 承接，語義定錨）

- 現狀：P1-C1-R 只做 artifact 的 content 驗證，evidence 未驗證。
- **evidence_refs 語義定錨（review 揪出雙語義，決策已定）**：evidence_refs **指向被驗證的對象**（Tester 聲稱「我驗證了 artifact X」），**不是** evidence 自己的文字快照。P1-B D8 / 2B §4：evidence = { artifact_hash, verdict, ... }，artifact_hash 指向被驗證的 artifact。
- C2 的 evidence flow：
  1. Tester 的 final_message 就是 evidence 內容（含「驗證了哪個 artifact」的結論）。
  2. Domain Core 從 claim 解析 Tester 聲稱驗證的 artifact ref（`evidence_refs`），逐一 `verify_artifact_ref`（存在性 + hash）——**驗證被驗證對象真的存在**。
  3. **verdict 不可 machine-check（接受）**：HandoffResult（2A §6 frozen）無 verdict 欄位，Tester 的 PASS/FAIL 結論留在 final_message 文字裡，Domain Core 不做 verdict 判定（那是 Human/Auditor 的職責，非 content 層）。這是已知限制，非 C2 要解。
- **不做**「evidence 自己的文字也 write_artifact 存成 artifact」——那會混淆 evidence_refs（被驗證對象）和 artifact_refs（產出內容）的語義。

### D5：路由切換（reviewer 記帳）

- `execute_work_dsh` 是**唯一 production 路由**（真 DSH）。
- `execute_work`（mock/scripted 面）標註 deprecated（docstring + 註解）+ 加 `warnings.warn(DeprecationWarning)`（code 層可見性，291 tests 用 filterwarnings 處理；不宜 assert 級強制），保留只供測試/離線。P1-C2 的 E2E 一律走 `execute_work_dsh`。

### D6：headless approval policy = fail-fast deny（reviewer 記帳）

- C1.9 發現：overlay `permission: disabled` 後 session 落 workspace-write，但 base approval policy 仍 "ask"，agent escalation 可能 hang。
- C2 在 --patch overlay 追加 approval policy 設定為 **fail-fast deny**。**DSH 枚舉值 = `never`**（review 已實讀 dsh-user-approval types：`ApprovalPolicy = 'ask' | 'never'`，`never` = 確定性 rejected，正是 headless/CI 姿態）。執行者實讀 `dsh-user-approval` / `dsh-permission-presets` config 確認 plugin id + overlay 鍵名，做 interface adaptation。
- 驗收：overlay 含 approval policy `never`，真 DSH E2E 無 escalation hang。

---

## 2. 範圍（授權改動檔）

- `src/work_adapter/execution.py`：D2（write_artifact 接入 + ref 回填）+ D3（回填後空下限）+ D4（evidence verify）+ D5（execute_work DeprecationWarning）
- `src/work_adapter/bridge.py`：D6（approval policy overlay）
- 測試：`tests/test_work_p1c2_integration.py`（新，E2E + deny path）

## 3. E2E 測試（真 DSH，C2 的核心驗收）

用 `execute_work_dsh` 跑真 headless，驗證完整閉環：

1. **Researcher artifact E2E**：Researcher role → headless → 產文字 artifact → final_message 進 artifact store（Domain Core 算 ref 回填）→ ref 進 WorkEvent → fold 出 artifact。
2. **Developer artifact DENY**：Developer role 產 artifact → P1-C0 capability gate 拒絕（`CapabilityNotAuthorizedError`）。
3. **借殼 DENY**：role=Researcher 但 cwd=Developer → identity binding 拒絕。
4. **回填後空 refs DENY**：artifact + done + Domain Core 回填後仍空 → D3 拒絕。
5. **agent 聲稱錯誤 ref DENY**：agent 在 final_message 聲稱 ref 且 ≠ Domain Core 算的 canonical ref → D2 防偽語義拒絕（正常 case agent 不聲稱）。
6. **Tester evidence E2E**：Tester 產 evidence，聲稱驗證的 artifact ref 存在性驗證（D4）。

真 DSH 環境不可用時，用 fake session log 測（同 P1-C1 的 fake-log 模式），但標明「真 DSH 未驗證」風險。

## 4. 驗收

1. E2E 閉環：Researcher 產 artifact 經三層驗證進 Domain Core（真 DSH 環境可用時）。
2. 三層 deny path 全測（identity / capability / content）。
3. D2-D6 全部落地（D2 含回填語義 + D2-遷移）。
4. 全 regression 綠（含 P1-C1-R 的 291 tests，遷移後）。
5. `src/work/` 零 DSH import + 零 DSH_HOME 字串不變。
6. D6 approval policy：overlay 含 `approval policy = never`（DSH 枚舉值，fail-fast deny），真 DSH E2E 無 hang。

## 5. 不做（Out of Scope）

- **不做** multi_stage / continuous（P1-D）。
- **不做** production adapter 移出 repo（P1-E）。
- **不做** 檔案型 artifact（patch/test report）的 staging→ingest production wiring——那是「開發環境收尾」輪，邊用邊做。
- **不做** 開發環境的 operational hardening（啟動方式/agent config/logs/失敗處理/操作文件）——那是下一輪。
- **不做** evidence verdict 的 machine-check（HandoffResult frozen 無 verdict 欄位，已知限制）。
- **不改** docs/ frozen contract、`src/work/` 既有模組（execution_evidence/artifact_store 已在 P1-C1-R LAND）。

## 6. 回報格式

- 改動檔案清單
- D1-D6 各決策實作方式（含 approval policy 的實際 config、write_artifact 接入點）
- E2E 測試結果（真 DSH 或 fake-log，標明哪種）
- 完整回歸結果
- 確認 git diff --stat 只動列名檔案、src/work/ 零 DSH import + 零 DSH_HOME
- 剩餘 architectural concerns
