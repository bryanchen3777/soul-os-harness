## 工單：EI-2A-JOURNAL-PORT-1 — 離線 journal／port（fake runtime）

日期：2026-11-08；執行者待派；READY。Owner 指示：B7 未 closed 不停；本票是 EI-2 第四步的第一個可施工項。

### 目標（一句話）

用 fake runtime 驗證 durable 去重、狀態與故障恢復，不把舊靈魂鏈接入，為 EI-2B 保留確定性邊界證據。

### 範圍（明確列出要動的檔案／模組）

- 新增（只允許新增，不改既有檔）：
  1. `src/interfaces/journal.py`
  2. `src/interfaces/runtime_port.py`
  3. `tests/interfaces/test_journal.py`
  4. `tests/interfaces/test_runtime_port.py`
- 允許更新：接口 CONTRACT 的 runtime 部分、本票證據目錄、canonical／Notion 由主腦統一更新（worker 不寫 canonical／不同步 Notion）。
- 本票目錄（worker 擁有）：`C:/Users/bbfcc/.local/share/soul-os-validation/ei2a-journal-port-1-20261108/`，內含 `reports/`、`EI-2A-RESULTS.md`。
- 禁止改其他 runtime；禁止動既有 `src/`、`tests/`、`configs/`、`scripts/` 任一已存在檔案；禁止動 `data/**`、`.env*`、憑證、生產服務。

### 做法（決策已定，執行者照做）

- `journal` 使用 SQLite，constructor 必須接收呼叫端顯式開啟的 connection，沒有預設路徑／環境變數。
- schema 以 namespace 唯一鍵保存 fingerprint、request ID、state、epoch、response、各提交目的地證據與建立／到期時間。
- `claim(namespace,fingerprint,request_id,now)` 在單一交易內返回 `NEW／REPLAY／IN_PROGRESS／CONFLICT／UNCERTAIN／EXPIRED`；不得在檢查後另開非原子 insert。
- 相同 key 不同指紋永遠 `CONFLICT`。已終態且超過 24h 的相同 key 回 `EXPIRED`（對外 `410 idempotency_expired`，要求新事件用新 key），不自動重做舊操作；unresolved 不因到期而解除阻擋。
- `begin(request_id,epoch)` 僅 accepted CAS 到 executing；`record_generated` 僅允許該執行者保存固定 response ID/body。
- `record_transport` 不改提交狀態；`recover_executing` 只將舊 epoch 的 executing 標 uncertain。
- `record_outcome` 只接受預定目的地／狀態 enum；不把 unknown 升 completed，除非有明確同 request 的 completion evidence。
- fake port 明確回傳證據，不靠文字解析。`submit(decision,fake_runtime,clock)` 在 begin 後才呼叫注入 port；本票無 backend／bus adapter。
- backend 出錯前後難以證明無副作用時為 uncertain；取消的請求不重用原執行機會。
- 同 key 已 generated 即重放，不調 runtime，不補做記憶。不得暴露 SQLite 錯誤、raw token 或其他 namespace 的 response。
- 所有資料與 clock 由呼叫端提供，模組 import 不啟 runtime；FakeRuntime 不允許網路／subprocess／伺服器。

### 驗收（完成的定義）

- 合成並行雙 claim 恰一 NEW。
- 相同 key 不同 payload 回 CONFLICT。
- begin 雙執行者只一成功。
- 在每個狀態間注入崩潰，executing 重啟後 UNCERTAIN 且不再呼叫 runtime。
- generated 重放 response ID/body 不變。
- 兩 principal／branch／agent 隔離。
- 24h 不清 unresolved。
- 傳輸與記憶狀態互不冒充。
- 以上全部綠，才具備 EI-2A ACCEPTED 條件；由主腦按證據判定，本票不自行宣告 ACCEPTED，更不宣稱 EI-2 整步完成。

### 測試（要寫哪些／跑哪些現有測試）

- 唯一解譯器：`C:/Users/bbfcc/.local/bin/soul-os-harness/.venv/Scripts/python.exe`（Python 3.11）。
- 先跑改前既有聚焦測試：`python -B -m pytest -q tests/interfaces`，保存命令／計數／exit，作為新檔加入前的基線。
- 再實作並跑新聚焦測試：`python -B -m pytest -q tests/interfaces/test_journal.py tests/interfaces/test_runtime_port.py`，必須全綠。
- 全程自建 TEMP：SQLite 只用自建 temp 連線；每次 pytest 前 `SOUL_OS_DATA_DIR` 指新 TEMP；dotenv disabled；禁 NPX／live URLs／tokens；禁 server／listener／真實 API-LLM／MCP 子行程。
- AST／安全變異：跑既有 `tests/interfaces` 相關守衛；對 journal 狀態躍遷（claim／begin／generated／outcome／uncertain／expired）做逐項功能變異，每條必須 exit 1 且在功能斷言轉紅，不把收集／語法／harness 失敗當紅；每個變體單獨 temp 副本，保存 patch。
- 全庫回歸：跑一次 `python -B -m pytest -q tests`，保存完整 stdout／stderr／exit／失敗 node 清單；新測試零失敗、無新增行為失敗；既有基線失敗只列 node，不擴白名單、不 skip、不改程式求綠。
- 不跑生產 lifecycle CLI；不綁生產埠；不啟服務。

### 不做（Out of Scope）

- 真實 runtime adapter、HTTP、SAGE／歷史補寫、舊鏈 metadata、TTS、工具、部署、生產重啟。
- 不解除 EI-2B 阻擋；不宣稱 B2～B7 已解；不開啟生產可寫入口。
- 不 commit／push／合入 main；不清理他人檔案；不殺任何行程。

### Frozen Contract 注意

- 既有 src 檔全部不動；frozen schema／Agency／world／SAGE 邊界不變。
- 衰減門檻、cohort 台帳、金樣／corpus 一律不碰。
- 新 journal 狀態名沿用契約既有語意，不得自創持久語意或把 transport 計數語意改掉。

### 回報格式

- 新增檔案清單與 diff（新檔為全量內容）。
- 解譯器、命令與實際測試數：改前聚焦基線、新聚焦測試、AST／變異逐條、 全庫命令／計數／exit／失敗 node 清單。
- 逐崩潰窗口結果：每個狀態注入前／後狀態與是否重調 runtime。
- 是否碰 frozen／意外行為／未覆蓋清單。
- 單一 authoritative `EI-2A-RESULTS.md`；完成本票即停，主腦保持當輪判定。
