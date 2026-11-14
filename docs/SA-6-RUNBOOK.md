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

## 8. SA-THIN-ENTRY-1 薄端獨立入口（2026-11-14；閉門；成功僅判 DEPLOYED_CLOSED，不開門）

- 入口：`scripts/serve_sa_thin.py`（新建；本票唯一產品文件）。
  沿用生產認證／路由／設定載入器（`configs/sa/registry.json`＋`presets.json`，
  e351541 版；SHA-256 registry `89c30666…759128`、presets `c37de506…58cdc1`，
  啟動時重讀比對，不一致即拒啟 exit 2）。
- 綁定：只綁 `127.0.0.1:18765`（結構性釘死，無 `--host`／`--port` 旗標，
  常數 `THIN_HOST`／`THIN_PORT`，`THIN_PORT == EXTERNAL_PORT`）。
- 門禁：只作用本實例。行程內 `_THIN_OPEN = False` 常數，無開啟介面；
  soul-access 入站一律 403（凍結 bridge 文案 `soul-access inbound closed`），
  先於上游 POST 與記憶寫入之前拒絕。共享 `release/sa.json` 不讀不寫不翻轉；
  重啟（新行程重跑同一常數）後仍 closed。
- 拒啟（exit 2，`sa thin entry: startup refused.`；拒啟≠失敗，屬預期行為）：
  設定缺漏／JSON 破損／SHA 漂移／`do-not-deploy` 標記／registry schema 非法／
  `127.0.0.1:18765` 已被佔用。
- 啟停（沿用 S8 既有核准前台模式；不裝服務、不建系統服務、不開後台 wrapper）：
  - 啟動：`.venv\Scripts\python.exe -B scripts\serve_sa_thin.py`
    （成功印 `sa thin entry on 127.0.0.1:18765 (closed, loopback-only)` 後常駐）。
  - 停止：前台窗口 Ctrl-C（同 S8 閘道）。
  - 本票交付不啟動、不接流量；`release/sa.json` 保持 `{"status": "closed"}`。
- 閉門證據（沙盒，T12）：release=open 下仍 403；上游呼叫 0（forwarder 0 次、
  legacy carrier 零觸碰）；記憶寫入 0（拒絕點在 live handler 之前）；
  錯認證 401、非 rem/primary 路由 404/403；重建即重啟兩實例皆 403。
- 禁止：不開門、不接真流量；不動 :8000、主服務控制、系統服務、
  registry/presets、`data/**`、canonical；Frozen（§3 放行順序在內）一字不動。

## 9. SA-OPEN-FIRE-1 單發探針（2026-11-14；BLOCKED_PORT，零計費，雙門 closed）

- 探針：`scripts/sa_open_fire_probe.py`（新建；本票唯一產品外文件）。
  固定 `rem/primary`、單一 `request_id`（`req-sa-open-fire-1-0001`）、
  單一訊息（「你好。」）；原子單次准入（`O_EXCL` 已送標記先行，路徑
  `verification/sa-open-fire-1/`，重跑不重送）；禁併發、禁自動重試、
  禁補發；逾時視為已耗額；第 2 次額度不啟用。
- 預設閉門：一般啟動仍 closed；無 HTTP opener、無 `--host`／`--port`
  旗標（loopback `127.0.0.1:18765` 結構性寫死，與薄端常數斷言相等）。
- 存證：送前存可重播 request＋參數＋雜湊＋憑證引用（只記變數名
  `OLLAMA_API_KEY`）；回後須存原始 CallResult raw＋usage；缺 raw 即
  不合格停手（exit 3），不以文字答案或估算替代。
- 實彈前唯讀 preflight（2026-11-14T20:49:45Z）：`0.0.0.0:18765`
  被外來 PID `31704`（`python.exe` ← `9988 python.exe` ← `3388 svchost.exe`，
  Session 0，命令列皆空，2026-11-11 建立）佔用 ⇒ **BLOCKED_PORT**，
  拒啟並上報；未探測外來 listener、未反覆啟動、未殺行程、未換埠、
  未旁路直打 LLM。
- 第二阻斷（獨立）：離線已證明現行 dialog 只回字串
  （`src/soul_access/wiring.py:358`），無 raw/usage 通道 ⇒ 即使埠可用，
  `--fire` 亦停於 EVIDENCE_GAP；擴改線上鏈路須另票裁決，本票不斷言。
- 終態（讀回值）：計費嘗試數 **0**（無 `sent.json`）；實例門 closed
 （本票無 opener）；共享門 closed（`release/sa.json` 讀回 closed，零寫入）。
- 測試：`tests/soul_access/test_t13_open_fire.py`（21 項，含雙向變異 6 項）。
- 證據：`verification/sa-open-fire-1/`（`check-report.json`／
  `preflight-blocked.json`／`request.template.json`／`STATUS.md`）。
- 禁止（本票）：不推播（本地提交，pathspec 僅本票檔案）；不動 §3。

## 10. SA-EVIDENCE-CHANNEL-1 取證通道（2026-11-14；Sol 起草，零真實呼叫）

- 目標：補「原始取證能力」（單發探針送前可重播 request、回後 CallResult raw＋usage），不開門、不部署、零真實呼叫。
- 改動（additive；預設零行為變化）：
  - `src/soul_access/evidence.py`（新增）：呼叫端專用沙盒 sink。送前存實際 provider payload＋sha256；回後存同次回應原文＋原生 usage，以 request_id 關聯；原子落檔（tmp＋rename）；只記 key 變數名（`OLLAMA_API_KEY`），不存憑證；`data/**` 結構性拒絕；無「最後一次結果」全域暫存。
  - `src/llm/proxy.py`（`OpenAIBackend.complete`）：新增選配 `evidence_sink=None`。未配置時舊路徑逐位元不變；配置時固定零重試，缺欄（缺 raw/usage）或落檔失敗即不合格（不補發）；raw 不進回覆正文／記憶／日誌。
  - `src/soul_access/wiring.py`（`build_dialog_factory`）：新增選配 `evidence_sink=None`，逐請求顯式透傳；未配置時完全不帶該參數（舊後端簽名相容，T9 全過）。
  - `tests/soul_access/test_t14_evidence.py`（新增，20 項）：成功往返（雜湊／原文／usage 同 request_id）、缺證不合格、逾時已耗單次、落檔失敗不發／不重發、併發隔離（分 sink＋共享 sink）、未配置舊行為一致、factory 透傳、data/** 拒絕、雙向變異三類（sink 旁路／request_id 錯配／雜湊偽造，轉紅再轉綠）。全 mock：假 `httpx.AsyncClient`＋無迴圈協程驅動（Windows 建 loop 需 socketpair，會觸 SA-0 封網，故不用 `asyncio.run`）；非路由基址 `.invalid`；零伺服器、零埠、零真實請求。
- 驗收（Hard Gate，`.venv` Python 3.11.15）：
  1. `pytest -q tests/soul_access/test_t14_evidence.py` ⇒ 20 passed；
  2. T10–T14 同跑 ⇒ 89 passed；`tests/soul_access tests/infra` ⇒ 666 passed（含 AST 守衛；T7 pin 未觸線）；
  3. CLI 沙盒對帳 `--simulated-time 2026-11-14T03:00:00Z` ⇒ VERDICT PASS（exit 0；sidecar ＋0；類型 0/0/0/0；關鍵節點穩定；prompt 零差異）；
  4. 全庫 `pytest -q tests` ⇒ 見 `verification/sa-evidence-channel-1/evidence-report.json`。
- 邊界聲明：本票只驗收「能力」（evidence channel 可用），**不宣稱線上缺口已解除**。實彈續行另有兩前置：埠釋放＋閉門接線驗證。
- 證據：`verification/sa-evidence-channel-1/`（`STATUS.md`／`evidence-report.json`／`sample/` 真實碼路徑樣本對）。
- 禁止（本票）：不碰 launcher／探針／生產設定／data；失敗保留閉門；不推播（本地提交，pathspec 僅本票檔案）。
