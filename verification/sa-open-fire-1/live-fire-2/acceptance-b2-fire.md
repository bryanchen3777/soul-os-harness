# SA-OPEN-FIRE-1 live-fire-2（B2 改訂）— 實彈報告：TRANSPORT_FAIL，失敗即停

- 批次ID：`SA-OPEN-FIRE-1 live-fire-2` ／ dialog_request_id `req-sa-open-fire-1-0001`
  （新批由目錄＋`sent.json` 區分；live-fire-1 封存不動，其 marker 未重置、未讀寫）
- 改訂依據：臥龍選項二 —— 撤回「開門→薄端 HTTP」，沿用 live-fire-1 同款直送 sender 路徑。
  Owner 已授權單次新批，恰 1 次真實推理；失敗即停，不重跑。
- 實彈時間（UTC）：2026-11-15T00:12Z 前後（此輪 hard gate＋實彈＋負向同輪）
- 最終狀態：**實彈 FAIL（transport，未計費可確認，B2 直送限定）** —— 中止，不記整批 PASS；
  不宣稱薄端通過／正式路徑恢復／部署驗收。

## 1. 硬閘（發送前全綠，全唯讀；未為預檢打真 API）

| 項 | 結果 |
|---|---|
| HEAD（收斂票內含） | PASS：`ed9e253e610795c1321007de4fc4dae7217289b2`（即 ed9e253 本票；其父為 9c73599，工單所載 9c73599 即此鏈） |
| 直送程式版本（Gate1 結論逐行引用，非口頭自報） | PASS：`wiring.WIRING_VERSION="salw/v1"`（wiring.py:84）；`build_dialog_factory(..., evidence_sink=None)`（:236）；逐請求顯式透傳（:346／:355-356）；`max_retries: 0`（:353）；`proxy.py:2010 evidence_sink=None` 選配；取證模式固定零重試（:2081-2082）；迴圈 `range(max_retries+1)`＝`range(1)` 恰 1 POST（:2133）；送前 `record_request`（:2118-2122）；回後原文＋原生 usage `record_response`（:2218-2228）；路由 rem/primary（registry 實測含 `{rem, primary, agent_rem}`）；preset primary＝provider openai／model `deepseek-v4.1-flash:1017`／`key_ref=OLLAMA_API_KEY`／`{temperature:0.5, max_tokens:8, base_url:https://ollama.com/v1/chat/completions}` |
| 模板同一位元組 | PASS：`request.template.json` 內載 sha `418af813…1915eeb1` 經 `sa_open_fire_probe.build_request()` 重算逐字一致；檔 sha `421cbddf…73dbb1` 與 live-fire-1 驗收單一致；`request_id=req-sa-open-fire-1-0001` |
| `:18765` 空（發送前＋發送後） | PASS（bind 探針可綁＝空；薄端從未啟動） |
| 舊排程 | PASS（`SoulOS-ExternalGateway` Disabled；`SoulOS_Watchdog` Ready 常態） |
| `:8000` 只讀 | PASS（LISTENING PID 22832，`data/server.pid`=19512 trampoline 形狀；本輪未發任何請求、未重啟） |
| registry／presets pin 一致 | PASS（實測 registry `89c30666…759128`、presets `c37de506…58cdc1`，與 `serve_sa_thin.py:92-93` 內釘選值逐字一致） |
| 雙門 closed（發送前讀回，未翻門） | PASS（`release/sa.json`＝`{"status":"closed"}`；`is_thin_open()` False） |
| T13 | PASS（`pytest -q tests/soul_access -k t13`：21 passed，`.venv` Python 3.11.15） |
| mock 對帳（continuation-1 離線演練引用） | `fire-report.json`：`payload_sha256=7043a3f…81475c`、`pair_ok=true`、`transport_calls=1`、`real_billing=0`（合成資料，非實彈成果）—— 證明正式 sender 取證通道可用 |

## 2. 實彈（恰 1 次，直送路徑；B2 新批）

- 送前保存可重播 request：`request.replayable.json`＝模板位元組拷貝（送前落檔）。
- 密鑰：行程 `load_dotenv()` 自載；全程只查存在性（boolean True），值永不回報、不落盤。
- 翻門：**零** —— 直送不查生產門，`release/sa.json` 全程只讀，薄端不啟動。
- attempt：1 次 transport 嘗試；`exit 7 TRANSPORT_FAIL（LiveRefused 包裝）`；
  marker `sent.json` 已建（O_EXCL，拒絕重送）；**不補打**。
- 取證：`evidence/req-3de97b41344a42fd.request.json`（送前 payload＋sha 已落地；
  `payload_sha256=7043a3ffb0…81475c` 與 live-fire-1 及隔離演練 drill-1 **同值**，
  證明正式路徑位元組一致；Python utf-8 讀回 `content='你好。'` 正確，
  控制台亂碼僅 PowerShell 顯示編碼假象）；**無 response 檔**（POST 未收到 HTTP 回應即失敗）；
  `result.json` 不存在（未達標不偽造）。
- 失敗定位（唯讀）：`data/logs/llm_4xx_response.log` mtime 仍 2026-08-25（未觸 4xx 處理；
  DNS 正常見 live-fire-1 驗收）⇒ 連線層（connect/TLS/逾時）單次耗盡，無 HTTP 回應。
  與 live-fire-1（exit 7）同形。
- 計費可核實程度：行程內 `transport_calls=1` 計數＋provider usage 缺席（無回應）⇒
  計費邊界＝**本票 1 次額度已耗，無從判定 provider 側是否計費**；
  按票面「逾時／送達不明視為已耗」處理。
- 雙門複閉：`release/sa.json` 讀回 `{"status":"closed"}`（零寫入）；
  `is_thin_open()` False；`:18765` 空。
- 閉門負向驗證：`negative PASS`（door1 403/up 0；door2 403/up 0；生產門 closed；
  `negative-report.json`，ticket=`SA-OPEN-FIRE-1-live-fire-2`）。
- 驗收效力（臥龍限定）：中止 ⇒ **只記「B2 直送限定實彈 FAIL（transport）」**；
  不得記整批 PASS；薄端／正式路徑／部署一概不宣稱。
- Frozen 差異：無（`src/`／`configs/`／`scripts/`／`data/**`／被追蹤檔全零差異；
  僅本目錄新增 `live_fire_b2.py`＋`negative_check_b2.py`＋`request.replayable.json`＋
  `sent.json`＋`evidence/`＋`negative-report.json`＋本檔；`release/sa.json` 未碰）。
