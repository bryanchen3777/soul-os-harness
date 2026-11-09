## 工單：EI-2B-PROGRAM-1 — per-soul 全入口互斥施工（正式，可施工）

日期：2026-11-08；執行者待派；READY。Owner 指示：第四步未完成不停；本票是 EI-2B 的程式施工票。
設計輸入（已複核）：`C:/Users/bbfcc/.local/share/soul-os-validation/ei2b-b7-mutex-design-1-20261108/` 內 `WRITER-CENSUS.md`、`B7-MUTEX-DESIGN.md`（主腦已抽查行號引用，關鍵引用真實；G1 否定主張已升級為高置信：`Journal(` 僅見於 `src/interfaces/` 內部，`admit(` 僅見定義處，runtime 無調用方）。

### 目標（一句話）

把 per-soul coordinator（lease key＝soul alias）接進 HTTP／Web／TG／voice 四入口各自首次持久副作用之前，實現 409 busy＋fail-closed＋背景寫入歸屬，做到全入口互斥可驗收。

### 範圍（確切檔案／函式）

- 入口（admit＋claim 落點，按「解析成功後、第一次 publish／寫檔前」規則）：
  1. `src/io/gateway.py` — `/ws` 的 `websocket_endpoint`（USER_MESSAGE 分支 `:930-986`）：解析成功後、`bus.publish`（`:986`）前插入 `admit→claim→begin`。
  2. `src/io/channels/router.py` — `inbound`（`:875-966`）：解析成功後、`bus.publish`（`:966`）前同上。（施工 Step 0 先逐行讀 payload 語意再寫，見做法 0。）
  3. HTTP 入口、voice 入口 — Step 0 先定位檔位並引用行號，再按同規則落點（B1 已閉合為程序，不猜落點）。
- 協調：重用 EI-2A `Journal` 作 lease 存儲（B2 拍板案 A，見做法）；新 coordinator 模組 `src/interfaces/coordinator.py`，純函數＋caller-owned store，沿用 `journal.py:1-29` 合約風格。
- 派送 fail-closed：`src/io/gateway.py:1017-1074`（O1）、`src/io/channels/router.py:186-`（O2，含 fallback `:218-255`、send `:345`、outbox `:709-`）、TTS（O3）——缺登記即 uncertain＋日誌，刪 fallback。
- 背景歸屬：`src/llm/proxy.py:4952-4978` 歷史寫入、`src/memory/middleware.py:543-647` SAGE 提交、`src/soul/diary.py`／`dream_event.py`／`scheduler.py` 背景寫入——加 `non_request_write`／`triggered_by` 標記，不混入 outcome。lifecycle 評估歸背景、不經 lease（B3 已拍板）。
- **不動**：`src/agent/consciousness.py` 的 `_pending`／競標流程、`src/eventbus/` worker 投遞語意、`src/agent/speaker_token.py`、`src/eventbus/token_manager.py`（C3 保持不動，B4 已拍板）、Frozen（下節）。

### 做法（決策已定，執行者照做）

0. **先定位後接線（B1 閉合）**：開工先定位並在回報中引用行號——HTTP 入口檔、`voice` 入口 inbound 檔、`evaluate_lifecycle_all()` 定義、`router.py inbound :875-966` payload 語意。任一定位失敗→標 BLOCKED 回報，不猜落點、不寫對應接線。
1. feature flag 預設關＝舊行為逐字不變：所有新分支包在 flag 後；flag 關時新增程式碼零觸發（相容 fixture 見測試）。
2. 入口逐一接 `admit`（`src/interfaces/context_policy.py:62-102`）→ `idempotency_namespace`（`:105-108`，須內嵌 soul alias 作 lease key）→ `Journal.claim`（`journal.py:227-`）→ `begin`（`:273-`，epoch 綁定）→ 成功才 publish 第一個 bus 事件；`begin` 失敗→按 busy 表回 409。
3. lease 存儲（B2 拍板案 A）：runtime 啟動時 open 單一 SQLite（WAL），coordinator 持 caller-owned conn；沿用 `claim/begin` 單事務語意與 `recover_executing` 重啟路徑。不另建 lease 表。
4. 持有者身份沿 `correlation_id` 下傳（沿用 `proxy.py:4999` 語意，不改格式，只保證入口把 request_id 寫進第一個事件的 correlation 鏈）。
5. SAGE 背景提交完成記 `record_transport(memory,…)` 證據；**永不**以 memory／bus 證據觸發 `completed`（`journal.py:376-385` 已強制，施工只須「不繞過」）。
6. 背景寫入逐點加歸屬標記；`record_outcome(backend,…)` 僅由請求鏈的 backend 提交證據觸發。lifecycle 評估不經 lease，其寫入標非請求寫入（B3 拍板）。
7. fail-closed：缺登記→uncertain＋日誌；刪 O1/O2/O3 fallback。outbox 滯留訊息／孤兒 mp3：**保留＋標 uncertain＋日誌＋走對帳**，永不自動派送、永不靜默丟棄（B6 拍板）。
8. busy 語意（B5 拍板）：409 busy 含 holder 身份類別（入口種類＋lease epoch，不含 token）＋ `Retry-After: 1`；重試政策＝**同 key 重試**（等 IN_PROGRESS 轉 REPLAY 取固定回應）；僅 CONFLICT／EXPIRED／對帳後的 uncertain 才 mint 新 key。
9. C3（bus 單 holder＋queue）保持不動；coordinator 只做 per-soul admission（B4 拍板）。
10. InnerLifeWriter 缺口（B7 拍板接受殘留）：重啟後 `recover_executing` 把舊 epoch 標 uncertain，新事件走新 epoch，**不偽造補登**，斷裂記為 uncertain gap；writer 持久化不在本票範圍。
11. 死鎖禁令：grant 檢查只在入口 publish 之前；handler 內不新增任何等待-grant 模式；附靜態守衛測試（寫法執行者定，必須存在）。

### 驗收（完成的定義）

- 四入口逐一有 claim 點（行號級），且相容 fixture 證明：舊入口請求在 flag 開／關下結果語意逐字一致（關＝舊行為；開＋無衝突＝同結果＋多 journal 行）。
- 雙入口並發 fixture（fake clock）：同 soul 兩入口同時請求→恰一 holder、一 409 busy（含 holder 類別＋`Retry-After: 1`）；異 soul 兩請求→互不阻塞。
- busy 不排隊不丟訊不斷言成功：fixture 斷言 queue 無增長、無 outcome 寫入、回 409；同 key 重試轉 REPLAY 取固定回應。
- 背景寫入 fixture：diary／carryover／SAGE 背景提交在 lease 被持有時仍完成，且行／記錄帶非請求標記，不出現在請求 outcome。
- fail-closed fixture：缺登記事件→uncertain＋日誌，且 gateway／router／TTS 無送達（mock adapter 斷言 0 send）；outbox 滯留走保留＋對帳，不派送。
- 死鎖守衛：靜態測試斷言入口 grant 檢查點在 publish 之前、handler 內無新增等待-grant 模式。
- 全庫回歸（`.venv`）＋AST 守衛通過；`git status` 僅含預期檔案。

### 測試

- 新增 fixture 程式（fake-clock＋fault-injection：journal 滿／DB 錯→`JournalError`→uncertain；重啟→`recover_executing` 舊 epoch→uncertain；同 key 異體→CONFLICT；終端超 24h→EXPIRED／410；busy 同 key 重試→REPLAY）。
- 既有測試：EI-1／EI-2A 相關 guards、token／bus 測試、`tests/` 全庫回歸。
- 測試一律用 `.venv` 解譯器、指名範圍；禁啟伺服器、禁綁生產埠、禁真實 API／LLM、禁殺行程；`data/**` 只讀（用自建 TEMP 作資料目錄）。

### 不做（Out of Scope）

- 不改 Frozen（下節）；不動 cohort 台帳；不改 C1/C2/C3 語意；不碰 lifecycle 評估函式本體（只標記不改）；InnerLifeWriter 持久化不在本票；不部署／不重啟生產；未 ACCEPTED 前不 commit／push／合入 main。
- 不宣稱 EI-2B 已解；驗收由主腦按證據判定。

### Frozen Contract 注意

- 不碰：Agency 4 stages、TriggerEnvelope、InnerLifeEvent、4 handlers、SAGE 寫入邏輯、world middleware、衰減門檻、cohort 台帳、Journal ownership／recovery 語意、persona／corpus。
- EI-1 admit 要求非空 request_id（`context_policy.py:64-73`）保持；`journal.py` 24h EXPIRED 永不自動重做（`:14-17`）保持。

### 回報格式

- 改動檔案清單（行號級）＋開工前後 `git status` 對照。
- 測試結果（跑了哪些、過幾筆、有無失敗；全庫失敗逐項說明是否本票行為）。
- Step 0 四項定位的行號引用；是否有踩 Frozen／意外行為；殘留風險（B7 gap）是否如實記錄。
