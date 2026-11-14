# SA 部署核准包（SA-DEPLOY-PREFLIGHT-1，計畫文件；非授權）
狀態：準備完成（preparation complete）；部署 BLOCKED（未核准，不得執行）。
APPROVAL-1（放行授權）維持 FROZEN。本包只列計畫，不執行。

## 1. 候選
- commit：`6fdd38f7f8e7f12a5a9b9a67421ec879c62a9b13`
  （SA-BUDGET-GO-1: 5 Go channels T1 verified 15/15 ACCEPT, Sol approved spend）
- 工作樹：追蹤檔零差異（`git diff HEAD --stat` 空）；未追蹤他人檔案全保留、未混入；
  `stash@{0} pre-vc-audit local WIP` 未動。
- 證據：`verification/sa-deploy-preflight-1/candidate-and-sha256.txt`

## 2. 批次（凍結，照 SA-6 runbook，不改）
- A 批：`rem x {primary, minimax-m3.1-flash, glm-5.3-flash, mimo-v2.6-flash}`（4 條）。
- B 批新增：`{mai, ruka, akane, mahiru} x 同上 4 後端`（16 條）。
- 併集恰 20 條；摘要 `_ROLLOUT_SET_DIGEST` 見 runbook §2（T7 釘死）。
- 放行順序與回滾：runbook §3-§4（關 gate -> update() 安裝 -> 開 gate 取證；異常關 gate 即全拒）。

## 3. 成本授權（引用，不新增）
- SA-BUDGET-GO-1：15/15 ACCEPT，40088 output tokens，0 重試／0 UNKNOWN／0 超時。
- 本票零新增模型請求；測試額度不轉上線額度。
- 五通道日後放行推理成本：未授權。任何真實放行流量須 Owner 另票核准花費。
- 證據：`verification/sa-deploy-preflight-1/go1-evidence.md`

## 4. 預定操作（未執行，待核准後另票）
1. Owner 核准生產 release 檔路徑與內容（目前 `release/sa.json` 不存在，gate 讀 closed）。
2. Owner 核准生產 registry／presets（倉內僅 do-not-deploy 範例，啟動即拒收）。
3. Owner 核准生產 dialog 接線（目前 `dialog=None`，呼叫時 500）。
4. INTERFACE-GAPs-1（tests/interfaces 五紅）先行解除，另票。
5. CANARY-WIRING-1 保留另票，不在本包範圍。
6. 日後重啟須 Owner 明示授權，另票走 `scripts/server_ops.ps1 restart`。

## 5. 回滾責任
- 操作人（核准後指定）：任一步異常 => 寫 `{"status": "closed"}`（hot-read 即時全拒），
  回到 runbook §3 第 1 步；整批回滾 => `update()` 重裝上一批名單。
- 回滾驗證：T7 early-B 變異已證實 revert 即綠；回滾後重跑 T7 全綠＋CLI 對帳方可結案。

## 6. 本票驗收（Hard Gate 四門；證據見 verification/sa-deploy-preflight-1/results.md）
- [x] AST 守衛：tests/soul_access + tests/infra 全綠（555 passed, exit 0）
- [x] 雙向變異：32 收集，綠→紅→還原綠（內建斷言）
- [x] 全庫回歸：8974 passed + 既定五紅（nodeid＋原因逐項一致，零新增）
- [x] CLI 沙盒對帳：--simulated-time 2026-11-12T03:00:00Z，PASS exit 0（no-op）
- 四門全綠：核准包可交付，部署仍 BLOCKED；不等夜槽 Canary。
