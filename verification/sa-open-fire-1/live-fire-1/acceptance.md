# SA-OPEN-FIRE-1 live-fire-1 — 入列驗收單（Mavis 受控 sender，唯讀核查）

- 批次ID：`SA-OPEN-FIRE-1 live-fire-1` ／ dialog_request_id `req-sa-open-fire-1-0001`
- 複核時間（UTC）：2026-11-14T23:27:42Z 前後（gate1＋gate2 同輪）
- 結論：**GO（1＋2 全綠）** —— 隨後實彈 1 次已耗（exit 7，見下），失敗即停未重跑。

## sender（正式程式路徑，版本＋sha256，工作樹乾淨）

| 檔案 | 版本 | sha256 | 行證 |
|---|---|---|---|
| `src/soul_access/wiring.py` | `WIRING_VERSION="salw/v1"`（wiring.py:83） | `2adf8ce8ddd8487ec1fe449082e428e7026ed5ca3def57588420dddbb6b32751` | `build_dialog_factory(..., evidence_sink=None)`（:235）；逐請求顯式透傳（:354-355）；`max_retries: 0`（:352）；未知別名 500 拒絕、永不 fallback primary（:296-311；formal resolver :459-481）；單次 dialog 無重試環（:344-383 單 pass） |
| `src/llm/proxy.py` | `OpenAIBackend.complete` | `fcc4c6684e0687323aae3bc17f17ad6e0ede5f976d9028a3a7ed165d098` | `evidence_sink=None` 選配（:2010）；取證模式固定零重試（:2081-2082）；迴圈 `range(max_retries+1)`＝`range(1)` 恰 1 POST（:2133）；送前 `record_request`（:2107-2122）；回後原文＋原生 usage `record_response`（:2218-2228）；缺欄即不合格不補發；4xx 直接 raise（:2199） |
| `src/soul_access/evidence.py` | `EVIDENCE_VERSION="sa-evidence-channel-1"`（:40） | `a7e9dd6714ed19914eeaa311487f96d21446b69ccb7f13ea07c848c271e82b58` | 僅記 key 變數名 `KEY_REF="OLLAMA_API_KEY"`（:44），值永不讀取存放；`data/**` 結構性拒絕（:116-117）；原子落檔（:150-167）；`verify_pair` 重算雜湊（:209-250） |
| `scripts/serve_sa_thin.py`（守衛） | 閉門常數 `_THIN_OPEN=False` 無 opener（:98-105） | `890dcc65a995930e4bdc403803805b5e5e64f46245a6529fe36ff77a15ee49c0d` | 綁定 `127.0.0.1:18765` 結構性（:75-80）；無 `--host/--port`（:224-239）；閉門 handler 先行 403（:124-127）；`preflight` fail-closed（:176-203） |

## 生效配置（逐項引用，非口頭）

- 綁定 `127.0.0.1:18765`：thin `:75-78`；探針 `:90-91`（測試斷言兩者相等）。
- 路由 `rem/primary`：registry `routes` 含 `{soul_alias: rem, backend_alias: primary, agent_id: agent_rem}`；preset `primary`（provider openai／model `deepseek-v4.1-flash:1017`／temperature 0.5／max_tokens 8／base_url `https://ollama.com/v1/chat/completions`／key_ref `OLLAMA_API_KEY`）。
- `max_retries=0`：wiring.py:352；proxy.py:2081-2082；迴圈 :2133 恰 1 次。
- 併發=1：單行程單 dialog 單 POST（同步泵、無執行緒、無背景推理）；wiring 模組匯入僅 stdlib＋api/bridge/release/schema（:47-60），無探測／重試／背景任務。
- 無自動探測／重試／fallback／背景推理：見上表行證；`file_preset_loader` 只讀檔＋env 取鍵（preset.py:202），`Preset.__repr__` 只顯 key_ref 名（preset.py:78-80）。

## 證據位置

- `verification/sa-open-fire-1/live-fire-1/`（新目錄；原票 `request.template.json` 未動，位元組拷貝 `request.replayable.json` sha 一致 `421CBDDFFF…73DBB1`）。
- 模板固定輸入 `你好。`／max_tokens 8 未改字；模板內 sha `418af81353a2457…1915eeb1`（64 hex）經探針 `build_request()` 重算逐字一致。

## 實彈結果（本單授權恰 1 次，已耗盡）

- attempt：1 次 transport 嘗試；`exit 7 TRANSPORT_FAIL（LiveRefused 包裝）`；marker `sent.json` 已建（O_EXCL，拒絕重送）；**不補打**。
- 取證：`evidence/req-cfa331374c62470a.request.json`（送前 payload＋sha 已落地；`payload_sha256=7043a3ffb0…81475c` 與隔離演練 drill-1 同值，證明正式路徑位元組一致）；**無 response 檔**（POST 未收到 HTTP 回應即失敗）；`result.json` 不存在（未達標不偽造）。
- 失敗定位（唯讀）：`data/logs/llm_4xx_response.log` mtime 仍 2026-08-24（未觸 4xx 處理）；DNS `ollama.com→34.36.133.15` 正常 ⇒ 連線層（connect/TLS/逾時）單次耗盡，無 HTTP 回應。`result.json` 缺席即停，未估算補證。
- 計費可核實程度：行程內 `transport_calls=1` 計數＋provider usage 缺席（無回應）⇒ 計費邊界＝**本票 1 次額度已耗，無從判定 provider 側是否計費**；按票面「逾時／送達不明視為已耗」處理。
- 雙門復閉：`release/sa.json` 已位元組還原（sha `a9a30027…2b0056`＝開門前一致，git 零差異）；`is_thin_open()=False`；`:18765` 空。
- 閉門負向驗證：`negative PASS`（door1 403/up 0；door2 403/up 0；生產門 closed；`negative-report.json`）。
- 最終狀態：**實彈 FAIL（transport，未計費可確認，Mavis 受控 sender 限定）**。
- Frozen Contract：未踩（`src/`/`configs/`/`scripts/` 產品檔、`data/**`、被追蹤檔全零差異；僅 `verification/sa-open-fire-1/live-fire-1/` 新增；`release/sa.json` 開→關已還原無殘差）。
- 意外行為：一次性 `door_open.py`  helper 留於證據目錄（開門動作可審計）；薄端未做持久啟動（kill 禁令下無可用的非殺停止手段，以 momentary `preflight→0`＋`:18765` 空證明可閉門啟動，屬結構性替代）。
- 密鑰：全程只查存在性（行程 env True／`.env` 名 True，`-Quiet`）；值永不回報、不落盤；證據檔僅 `key_ref` 名（已掃 `Bearer`／`api_key` 字樣缺席）。
