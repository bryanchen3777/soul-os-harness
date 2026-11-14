# SA-6 Staged Rollout Runbook（Soul 鏈第 7/7 票：分批放行準備）

- 狀態：**準備完成（preparation complete）；部署 BLOCKED（未核准，不得執行）。**
- 產品零改動：本票只新增 `tests/soul_access/test_t7_rollout.py`、
  本檔、`verification/soul-access/sa6/`。T6、CLI 台帳、守衛、Frozen Contract 一律未碰。
- 解譯器口徑：倉內 `.venv/Scripts/python.exe`（Python 3.11.15）。
- HEAD：`8bc9600`。

## 1. 採用裁決（票面 503 → 實際 403）

凍結產品行為（明文，SA-6 不得改）：

- `src/external_gateway/app.py:200`：`release gate closed ....... 403 forbidden (refuse new inbound)`。
- `src/soul_access/bridge.py:179`：`raise LiveRefused(403, "forbidden", "soul-access inbound closed")`。

採用決定（主大腦 Sol 裁定，轉述——逐字原文由主大腦持有）：**票面「熱關新入站 503」
字樣改為 HTTP 403/forbidden；不開產品票、不改 T6。**T7 以 403 為綠
（`test_t7_release_*`、`test_t7_mutation_*` 皆以 `(403, "forbidden")`＋零 generate
為期望）。未來任何人把期望改回 503，T7 即轉紅。

## 2. 分批定義（釘死，見 T7-7 摘要守衛）

- A 批：`rem × {primary, minimax-m3.1-flash, glm-5.3-flash, mimo-v2.6-flash}`（4 條）。
- B 批新增：`{mai, ruka, akane, mahiru} × 同上 4 後端`（16 條）。
- 併集恰 20 條；A∩B＝∅（`_ROLLOUT_SET_DIGEST = 3c1c427b…f5868870`，
  `test_t7_ast_batches_pinned_and_handler_wiring_intact` 逐字釘死）。

## 3. 放行順序（沙盒演練順序＝生產順序）

1. 關閉 release gate（`{"status": "closed"}`；hot-read，立即生效，無需重啟）。
2. 經 `RegistryProvider.update()` 安裝 A 名單 → 開 gate → 取 A 證據（4 放行＋16 拒絕）。
3. 關 gate → `update()` 安裝 A∪B 名單 → 開 gate → 取 B 新增 16 證據。
4. 任一步異常：關 gate 即全拒（403＋零 generate），回到第 1 步。

禁令：只改註冊表檔案**不等於**放行——必須經 `update()` 安裝進快照才生效
（T7-4 斷言：檔案編輯 alone 不改變已安裝快照）。

## 4. 回滾

- 立即：寫 `{"status": "closed"}`（同檔案 hot-read；UNKNOWN 隔離列不受開關影響，
  關→開不復活，見 T7-2）。
- 整批回滾：`update()` 重新安裝上一批名單（T7 early-B 變異證實 revert 即綠）。

## 5. 為何部署仍 BLOCKED（生產接線缺口）

倉內不存在一條「在線 handler＋真實 generate＋固定絕對路徑 release 檔」的組合佈線，
亦無核准的生產絕對路徑。沙盒用 tmp release 檔＋假 generate 驗的是**決策邏輯**，
不是生產接線。接線方案與生產路徑核准下達前，**任何真實放行操作一律禁止**；
本 runbook 只描述順序與回滾，不構成放行授權。

## 6. 驗收對應

- T7 全綠（17 項）：`pytest -q tests/soul_access -k t7`。
- 證據：`verification/soul-access/sa6/evidence.jsonl`（104 筆，見同目錄 REPORT）。
- CLI 沙盒對帳：同目錄 `cli-2026-11-12.json`（PENDING_COHORT，exit 3）。

## 7. SA-DEPLOY-PREFLIGHT-1 閉門預檢（2026-11-14；候選 `6fdd38f`；部署仍 BLOCKED）

- 候選確認：HEAD = `6fdd38f`（SA-BUDGET-GO-1）；追蹤檔零差異；他人未追蹤檔全保留；
  `stash@{0} pre-vc-audit local WIP` 未動。證據 `verification/sa-deploy-preflight-1/`。
- GO-1 引用（零新增模型請求）：15/15 ACCEPT，40088 output tokens，
  0 重試／0 UNKNOWN／0 超時；測試額度不轉上線額度。
- 唯讀核對：release 判定 `src/soul_access/release.py:30`；生產釘選
  `src/soul_access/wiring.py:74`（`release/sa.json` 不存在 ⇒ 恆 CLOSED）；
  :8000 掛載 `scripts/run_server.py:794-813`（dialog=None）；
  :18765 轉發 `scripts/serve_external_gateway.py:98-107`；
  倉內僅 do-not-deploy 範例，無生產路徑。
- Hard Gate（`.venv`，Python 3.11.15）：
  1. `pytest -q tests/soul_access tests/infra` ⇒ 555 passed（exit 0）。
  2. 變異收集 32 項（>0），隨上全綠（綠→紅→還原綠內建斷言，無 DUD）。
  3. `pytest -q tests` ⇒ 5 failed, 8974 passed, 32 skipped, 32 subtests passed
     （974.15s；五紅 = 既定 test_gap_*，nodeid＋原因逐項一致，零新增）。
  4. CLI `--simulated-time 2026-11-12T03:00:00Z` ⇒ PASS exit 0（no-op；
     sidecar 3397→3397 +0；類型 0/0/0/0；關鍵反例 0 轉變；
     prompt unexplained 0；與 SA-6 同槽差異係夜排程已實現預測轉變，見 results.md）。
- 核准包：`verification/sa-deploy-preflight-1/approval-package.md`（計畫文件，非授權）。
  APPROVAL-1 維持 FROZEN；INTERFACE-GAPs-1 前置、CANARY-WIRING-1 另票；
  批次定義（§2）與放行順序（§3）一字未改。
