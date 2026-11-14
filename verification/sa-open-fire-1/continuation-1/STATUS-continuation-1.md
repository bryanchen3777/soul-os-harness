# SA-OPEN-FIRE-1 續行演練 — 驗收狀態：ISOLATED-PASS（待 Owner 入列）

- 工單：SA-OPEN-FIRE-1 續行【前段】（重 preflight＋隔離開門單發＋關門負向驗證）
- 基線：`84d05ea`（`git rev-parse HEAD` 實測一致）
- 結果：**ISOLATED-PASS** —— 隔離假上游演練三項全過；**真實新批保留給 Owner 入列之後，
  本票零真實外呼、零計費**（`real_billing=0`）。
- 合成聲明：本目錄 `sandbox-evidence/` 內 raw/usage 為假 transport 合成，
  內文明標「合成資料（隔離演練，非實彈）」，**不算實彈成果**。

## 預檢（全唯讀，未啟服務、未發請求）

| 項 | 結果 |
|---|---|
| HEAD 含 84d05ea | PASS（`84d05ea64d9c22dac1c1cfa74b0204ee644b026`） |
| `:18765` 空 | PASS（演練前後皆空；薄端從未啟動） |
| `SoulOS-ExternalGateway` | PASS（Disabled） |
| `:8000` | 唯讀：LISTENING（PID 22832），`data/server.pid`=19512（trampoline＋子行程形狀）；未發請求、未重啟 |
| configs 釘選 | PASS（registry／presets sha256 與 `serve_sa_thin.py` 內釘選值一致） |
| 雙門 | closed／closed（`release/sa.json`＝`{"status":"closed"}`，`is_thin_open()`＝False） |
| 證據輸出位置 | `verification/sa-open-fire-1/continuation-1/`（沿用目錄，原票檔未動） |

見 `preflight-continuation-1.json`。

## 隔離開門單發（正式 sender 真實程式路徑）

- 行經 `wiring.build_dialog_factory(evidence_sink=…)` 逐請求透傳
  ＋ `OpenAIBackend.complete(evidence_sink=…)` 取證模式（固定零重試）——兩層皆實走。
- 上游＝行程內注入假 transport（斷言 `.invalid` 否則拒絕；`api_key` 為隔離 sentinel，
  非可用生產憑證；不綁正式埠）。
- 原票定義：rem/primary、`你好。`、model `deepseek-v4.1-flash:1017`、
  `temperature=0.5`、`max_tokens=8`、transport 恰 1 次。
- 對帳：`dialog_request_id=req-sa-open-fire-1-0001`；
  `evidence_request_id=req-b54bd4d2a916479e`；
  `payload_sha256=7043a3f…81475c`；`raw_sha256=88603077…0b25dd`；
  `usage={prompt:3, completion:1, total:4}`；`pair_ok=true`；
  憑證以變數名記錄、明文未入檔。見 `fire-report.json`。

## 獨立程序讀回（另起全新直譯器，無共享記憶體物件）

- 9 項全 true：pair／ids／payload／raw／usage／synthetic 標記／secret 缺席／key_ref／
  與 fire-report 一致。見 `crosscheck-report.json`（overall PASS）。

## 雙門負向＋復閉（隔離環境，沙盒門複本；生產門唯讀）

|  case | 結果 |
|---|---|
| 僅第一門閉（實例門常閉，沙盒門=open） | 403 攔截，上游計數 0 |
| 僅第二門閉（沙盒門=closed，真實釋放檢查路徑） | 403 攔截，上游計數 0 |
| 雙門復閉再驗 | handler 403＋forwarder 403，上游計數 0；生產門讀回 closed，`thin_open=false` |

見 `negative-report.json`（overall PASS）。

## 測試（`.venv` Python 3.11.15）

- `tests/soul_access/test_t10_thaw_deploy.py`～`test_t14_evidence.py`：**89 passed**
- 守衛 `test_sa_harness_budget_guard.py`＋`test_ei1_scope_registration_guard.py`：
  **106 passed**（演練前後各跑一次，皆綠）
- 純操作續行，未重跑無關全庫；產品源碼零改動（見下）。

## 最終狀態

- **BLOCKED（待 Owner 入列）** ＋ **雙門 closed**（持久：生產門檔 closed＋`_THIN_OPEN=False` 常數；
  運行有效：`:18765` 空＋閉門 handler／forwarder 實測 403）。
- 提交範圍：僅 `verification/sa-open-fire-1/continuation-1/` 新增檔；未推送。
