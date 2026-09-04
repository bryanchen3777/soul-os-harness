# 🏛️ Soul OS 工單：TL-7 — 社交機會生命週期與自主意志驗證 Harness (TL-7) + 歷史舊測試對齊

**工單編號**：TICKET-TL-7-HARNESS (Dual-Brain Edition)  
**發起大腦**：Antigravity (當前輪值主大腦)  
**協同大腦**：DSH (當前輪值執行大腦 / DeepSeek v4 Flash)  
**發起日期**：2026-09-03  
**上游合約**：[`docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md`](../docs/SI-3-SELECTIVE-ATTENTION-CONTRACT.md)  
**基線 Commit**：`8821173` (SI-3 Phase 2 registered)

---

## 🤝 零、 雙大腦協同原則與背景共識

1. **治理鐵律恪守**：有 Harness 驗收才算真正的 Closed。TL-7 延續 TL-5（意志行為分布）與 TL-6（客廳環境穩定）的高標準，為 SI-3 畫上圓滿句號。
2. **DSH 審計警覺落實**：針對 `test_I7` 契約測試，嚴格恪守「先核對契約原文，再改測試斷言」。經雙大腦核實：
   - 參照 [`src/world/perception.py`](../src/world/perception.py#L90-L105) 與行 111（M5.4-3.1 正式合約）：`WorldEvent.to_payload()` 正式將 `priority` 作為 additive 欄位納入序列化並保持向下相容。
   - `test_m3_4_priority_semantic_boundary.py` 中的 `test_I7` 為 M3.4 時代的舊斷言，未同步 M5.4-3.1 契約升級，因此更新斷言完全合法合規。

---

## 🎯 一、 目標與範圍

### 目標
1. 實作 **TL-7（Social Opportunity & Volition Stability Harness）**：驗證話題產生、緊湊感知、SM-4 意志自發、300s TTL 自然蒸發與 0 殭屍回覆。
2. 完成歷史測試衛生清理：對齊 `test_I7` 契約與 `test_tl2_volition.py` stub 行為。

### 範圍
- **新增檔案**：
  - `harness/tl7.py`
  - `harness/run_tl7.py`
  - `tests/test_tl7_social_opportunity_harness.py`
- **修改檔案**：
  - `tests/test_m3_4_priority_semantic_boundary.py`（對齊 M5.4-3.1 契約）
  - `tests/test_tl2_volition.py`（對齊 SM-4 後 stub 行為）
- **工具執行**：
  - `scripts/update_notion_status.py`

---

## 📋 二、 具體實作步驟（決策已定，照章執行）

### 步驟 1：同步 Notion 團隊看板
執行：
```bash
.\.venv\Scripts\python.exe scripts/update_notion_status.py "[雙大腦協同] DSH 接單啟動 TL-7 社交情境 Harness 驗收與歷史測試對齊"
```
確認回傳 `SUCCESS`。

### 步驟 2：歷史舊測試契約對齊
1. **`tests/test_m3_4_priority_semantic_boundary.py`**:
   - `test_I7_worldevent_payload_contract_unchanged`：依據 M5.4-3.1 合約，`event.to_payload()` 允許且應包含 `priority: int`。將測試更新為：驗證 `payload["priority"] == 20` 且 `restored.priority == 20`（符合 M5.4-3.1 的 round-trip 保證）。
2. **`tests/test_tl2_volition.py`**:
   - 修正 `ContextRoutingLLM` stub 的模擬行為，確保其符合 SM-4.1~SM-4.6 六輪校準後的 prompt 判定階梯，使測試全部 PASS。

### 步驟 3：實作 `harness/tl7.py` 與 `harness/run_tl7.py`
參照 TL-6 架構，實作 `TL7Runner`：
1. **劇本階段（4 大核心階段）**：
   - **Phase A (話題湧現)**：Ruka 在客廳發佈 share「我烤了餅乾在桌上」（`SocialWorldEvent`）。
   - **Phase B (緊湊感知與機會生成)**：Akane 接收事件，`_render_social_context` 產出 `[客廳現況]`（包含反框架警語），其 `SocialOpportunityBuffer` 成功生成 1 筆 `SocialOpportunity`（TTL = 300s）。
   - **Phase C (意志選擇與無連鎖不變量)**：Akane 評估該機會生成 Motive，傳入 `build_decision_prompt`。驗證 Akane 走入 SM-4 四元單選（絕不繞過意志直接觸發 transmit，留白率維持真實常態）。
   - **Phase D (300s TTL 自然蒸發)**：時鐘模擬前進 301 秒，Akane 再次檢視客廳感知，`get_active_opportunities` 自動剔除過期條目，`_render_social_context` 自動恢復留白（回傳 `""`），驗證 0 殭屍回覆。
2. **三大驗收指標（Invariants）**：
   - **TTL Expiration Invariant**: 100% PASS（過期條目徹底蒸發，0 遺留）。
   - **No Cascading Volition Invariant**: 100% PASS（0 自動連鎖搶話）。
   - **D2 Determinism & 0 Mutation**: 3 次獨立 run 軌跡一致，生產 `data/` 0 diff。
3. **`harness/run_tl7.py`**:
   - CLI 入口，執行 3-run 系列並印出驗收表格。

### 步驟 4：編寫單元測試 `tests/test_tl7_social_opportunity_harness.py`
覆蓋 TL7Runner 單次執行、三階段驗證、3-run 確定性與生產資料 0 污染。

---

## 📊 三、 驗收標準與測試命令

請執行精確路徑測試（落實教訓，永遠指定明確路徑）：
```bash
.\.venv\Scripts\python.exe -m pytest tests/test_tl7_social_opportunity_harness.py tests/test_si3_phase2_integration.py tests/test_social_opportunity.py tests/test_tl6_social_harness.py tests/test_m3_4_priority_semantic_boundary.py tests/test_tl2_volition.py -v
```
驗收條件：
1. 上述測試清單 100% PASS（0 失敗）。
2. `harness/run_tl7.py` 跑通並顯示三大不變量全過。
3. 0 Frozen Contract 污染（不改動 Agency 4 stages、TriggerEnvelope、InnerLifeEvent）。
4. Notion 看板同步完成。

---

## 📝 四、 回報格式

完成後，請 DSH 回報：
1. 改動與新增檔案清單
2. 測試執行結果（測試筆數、耗時）
3. TL-7 驗收報告（三大不變量結果）
4. Notion 更新之 Block ID
5. 完成後將成果 commit & push 到 origin/main
