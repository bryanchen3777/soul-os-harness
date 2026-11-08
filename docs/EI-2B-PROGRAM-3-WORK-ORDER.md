## 工單：EI-2B-PROGRAM-3 — scope 雙守衛協議與 lease 疑義封鎖收斂

### 目標
保留 PROGRAM-2 已驗真的功能，修復 scope 協議，完成身份式互斥、恢復隔離與入口／出站證據閉環。

### 範圍
- 修改 `src/interfaces/soul_lease.py`、`coordinator.py`。
- 接線限 `src/io/gateway.py`、`src/io/channels/router.py`、`src/voice/input_router.py`、`tts_service.py`、`src/agent/consciousness.py`、`src/llm/proxy.py`、`fish_tts_handler.py`、`src/soul/diary.py`、`dream_event.py`、`scheduler.py`；僅改邊界檢查、身份透傳及可信登記。
- 修改下述兩處守衛及 `tests/interfaces/`；證據寫入 `verification/ei2b-program3/`，更新本票文件與 canonical。不得覆蓋其他工作者差分。
- PROGRAM-2 功能收錄不等於 ACCEPTED；本票不以其先 ACCEPTED 為開工條件。

### 做法（決策已定，執行者照做）
1. **第二 cohort**：`tests/test_observability_completeness_1.py:955-963` 的 allowed 增列六檔：`src/voice/tts_service.py`、`src/soul/diary.py`、`src/voice/input_router.py`、`src/io/gateway.py`、`src/soul/dream_event.py`、`src/llm/fish_tts_handler.py`，另精確登記新模組 `src/interfaces/coordinator.py`、`src/interfaces/soul_lease.py`。`tests/infra/test_ei1_scope_registration_guard.py:16-28` 增固定 `EI2B_FILES` 與 cohort 摘要；`:121-123,191-199` 改驗兩 cohort 必須齊備、互斥，扣除兩者後仍等於原摘要。摘要沿用 `sha256(json.dumps(sorted(paths),ensure_ascii=True,separators=(',',':')).encode('utf-8'))`，從本票固定清單計算，禁止從受測 allowed 自我生成期望值；原摘要、原 baseline、frozen 摘要不變。

2. **推翻 TTL 接管**：`soul_lease.py:51-59,103-201` 改持久保留 FREE／HELD／QUARANTINED 行；generation 每次 FREE→HELD 加一，永不刪行重置。capability 固定為 `(alias,holder_token,request_id,generation)`，另綁 process nonce、PID＋建立時間及 journal epoch；token 不對外回傳或記日誌。

3. **四操作**：caller 提供專用 connection；啟用 WAL、`synchronous=FULL`、`busy_timeout=1000`，入口發現 `in_transaction` 即拒絕，不替 caller commit。每操作獨立 `BEGIN IMMEDIATE`，條件 UPDATE＋`rowcount==1`，commit 成功才回成功，例外 rollback。
   - acquire：只准 FREE→HELD；HELD 過期仍 busy。
   - renew：僅完整 capability 匹配 HELD 時刷新心跳；不改 generation。
   - release：僅匹配 capability 且已證明工作及出站副作用靜止，HELD→FREE。
   - recover：逐 holder 查證；活者不動，未知／過期／提交結果不明只准 HELD→QUARANTINED；確證死亡且副作用靜止才可 FREE。QUARANTINED 只能憑同等證據解除，不能靠時間。所有 uncertain 證據保留。

4. **可信死亡證據**：生命期適配器放 `soul_lease.py`，Windows 只查詢 process handle、建立時間與退出訊號，不取得終止權；PID、epoch 差異、心跳缺失均非死亡證據。權限不足／身份不符一律 UNKNOWN；未結出站 permit 一律視為未靜止。

5. **交易與恢復**：`coordinator.py:213-358,574-604` 保留 lease→claim→begin 分段提交，不包 journal 外層交易。停止啟動時全刪 lease及無條件 `recover_executing`；只對已證死 holder 用既有 `mark_uncertain(request_id,原epoch)`。claim-before-begin 留原 accepted 行及隔離證據；begin-before-publish 留 uncertain，不自動重播。commit 例外先重讀核對，不確定不得補償錯放。

6. **結束表**：coordinator 固定登記「未開始且確證無副作用→釋放；正常／確定失敗且全部任務及 permits 結束→釋放；取消但未 join／backend 不明／send 不明／崩潰→隔離」。不得以 finally、超時或 outcome 單獨釋放。

7. **Q1 清冊**：交付 `ENTRY-SINK-MATRIX.md`；逐一補掃入口、channel／voice adapters、bus producers／consumers、backend／儲存 caller、背景 producers、非聊天控制／啟動恢復六類，確認 G5 所有 caller。每列列 producer→admission→首副作用→sink、flag、身份及行號；列出控制入口而非假設不存在。新發現越界 writer 標 BLOCKED，禁止自行擴 scope。

8. **入口**：web `gateway.py:958-1014`、TG `router.py:965-982`、voice `input_router.py:204-233` 均須早於 adapter 持久副作用。群聊先凍結完整目標集合，alias 排序逐一 acquire，全勝才 claim／begin／publish；敗則僅補償確證未使用的 capability，禁止部分 fan-out。WS 回 busy frame、TG 回明確忙碌訊息、voice 回 busy 狀態；皆帶 retry_after=1，不回成功、不入處理 queue。

9. **重試與輸入保留**：TG 用 bot namespace＋update_id，WS 用 session namespace＋client message id，voice 用 session＋turn id，再含 alias 派生 key；缺原始穩定識別時維持 UUID，明列不能保證重試去重。WS 由 client 保留並明示未接受；TG／voice 在票內新建 SQLite rejected-input 表保存原輸入與重試識別，不觸碰對話歷史、不自動重送。

10. **correlation／backend**：在 consciousness、proxy 及三出站點核對 soul＋request＋epoch，並驗 capability generation；缺漏、舊 worker、錯配全部零寫入／零送達。backend outcome 只取 proxy 真正提交成功的既有證據落點；記錄確切行號，memory／bus／TTS 不得冒充完成。

11. **背景／O4／R9**：coordinator 以受信 caller 登記 background capability，禁止憑 payload 布林放行；O4 共用 O1/O2/O3 授權。每次出站先原子建立 permit，完成才結清；孤兒 outbox／mp3 留原件，uncertain 落票內 SQLite reconciliation 表。登記／permit／對帳使用逐鍵 SQL CAS，禁止共享 dict／JSON 整體覆寫造成 lost update；不改 SAGE 寫入邏輯。

12. **口徑**：C3 不變；異 soul 只承諾 admission 互不阻塞，不承諾 bus 並行。queue 分 admission 與既有 C3 量測；flag 關連同 startup-recover 零觸發。證據綁 HEAD、工作樹差分摘要與關鍵檔 SHA256。

### 驗收（完成的定義）
- 兩守衛同綠；少／多 cohort 成員、改原 scope／frozen 均轉紅。
- 活 holder 遇 recover 不釋放；死且靜止可回收；疑義隔離；異 key 同 soul 雙 holder 恰一勝；舊 capability 零寫入／送達；提交點故障不錯放。
- 全庫差分逐項歸因，不能將非本票紅冒稱全綠；本票新增失敗零容忍，主腦獨立複核後才 ACCEPTED。
- 不改生命週期／昇華／投影，CLI 沙盒對帳不適用；若差分觸及即停工回報。

### 測試
- 擴 `test_soul_lease.py`：四操作、generation、死亡三態、故障提交、雙 connection 競爭。
- 擴 `test_coordinator.py`：交易拒入、兩崩潰窗口、結束表、活 holder recovery。
- 擴 `test_entry_wiring.py`：三入口 busy、穩定 key、拒收保留、群聊全有全無、adapter 首副作用。
- 新 `test_ei2b_authorization.py`：三元錯配、舊 generation、背景偽造、orphan、R9 併發保全、flag 關啟動零觸發。
- 守衛雙向變異只在記憶體／沙盒，逐條紅綠對照；mock→指定測試→`.venv\Scripts\python.exe -m pytest -q tests`。非本票失敗以同 HEAD 完整 clone 加開工基線差分複核。
- 寫入只進自建 TEMP／票證目錄，`data/**` 唯讀；禁伺服器、綁埠、真實 API／LLM。**任何 agent、任何理由，都不得殺行程**；不得清理疑似殘留、不得停止曾啟動服務的背景 task。

### 不做（Out of Scope）
跨進程多副本實測本票不做，僅雙 connection＋可信適配器 fixture，不宣稱多副本部署已證明。journal 語意、`admit()`、C1–C3、lifecycle 本體不動；不部署／重啟；未 ACCEPTED 前不 commit／push／合入。

### Frozen Contract 注意
Agency 4 stages、TriggerEnvelope、InnerLifeEvent、4 handlers、SAGE 寫入、world middleware、衰減與 cohort 台帳全部不碰；scope 登記不等於解凍。`journal.py` 零修改。

### 回報格式
逐檔行號與前後差分；守衛／變異／測試命令、解譯器及數量；失敗歸因；清冊、結束表、故障證據、HEAD／SHA256；Frozen／意外行為及殘留。canonical 與 Notion 分列規劃、實作、驗收、部署狀態；同步失敗明報待補。
