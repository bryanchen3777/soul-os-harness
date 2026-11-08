## 工單：EI-2B-B7-MUTEX-DESIGN-1 — 全入口互斥來源導向設計（只讀設計，不動 runtime）

日期：2026-11-08；執行者待派；READY。Owner 指示：第四步未完成不停；本票是 EI-2B 的前置設計票。

### 目標（一句話）

以來源為導向，逐函式釘死 per-soul 跨入口協調的精確設計（lease 位置、busy／defer 語意、背景寫入歸屬、fail-closed 規則），輸出可直接施工的 EI-2B 程式工單草案。

### 範圍（只讀＋票證目錄，不碰 repo 任何檔案）

- 只讀查閱：`src/agent/consciousness.py`（`_on_user_message`／`_fire_intent`／`_pending`／`_on_session_end`）、`src/agent/speaker_token.py`（競標窗口／cooldown）、`src/llm/proxy.py`（`handle_event`／`_handle_event_impl`／`_in_flight`）、`src/eventbus/`（bus worker 投遞語意、token manager）、`src/io/gateway.py`（broadcast 點）、`src/soul/scheduler.py`（night slot／lifecycle 呼叫點）、`src/memory/middleware.py`、`src/conversation_qualification/`、SESSION_END／diary／dream／lifecycle 所有持久寫入點、`src/interfaces/` 現有六模組＋EI-2A journal／port。
- 產出只寫本票目錄：`C:/Users/bbfcc/.local/share/soul-os-validation/ei2b-b7-mutex-design-1-20261108/`，內含 `B7-MUTEX-DESIGN.md`、`EI-2B-PROGRAM-WORK-ORDER-DRAFT.md`、`WRITER-CENSUS.md`。
- 禁止修改 repo 任何檔案（含 docs）；禁止跑服務、綁埠、真實 API／LLM；禁止殺行程；`data/**` 只讀，連快照都不複製（讀原始檔行號即可）。

### 做法（決策已定，執行者照做）

1. **Writer census（逐項 file:line）**：列出所有持久寫入點，分四類——①請求鏈寫入（USER_MESSAGE 觸發的情緒／關係 touch、intent、生成、記憶提交、交付證據）；②背景寫入（自主 followup、SESSION_END、diary／dream／lifecycle、排程）；③協調原語（`_pending` per-agent 鎖、speaker token 競標＋cooldown、`_in_flight` 去重、emotion `busy_timeout`）；④舊派送（gateway broadcast、router、TTS、社交 producer）。每項標：首次持久副作用位置、是否持有 request ID、崩潰後可判斷性。
2. **Coordinator 放置（主大腦已拍板）**：per-soul coordinator，lease key＝soul alias；HTTP／Web／TG／voice 四入口的 admission（EI-1 admit＋journal claim）必須發生在**各自首次持久副作用之前**；持有 lease 者延續其事件鏈。不得只鎖新 HTTP 卻聲稱全靈魂序列化。
3. **死鎖禁令（主大腦已拍板）**：bus worker 內**不得等待**另一個被它阻塞的 queued event。grant 必須在 publish 之前取得；拿不到→明確 busy 回應＋重試指引，不得在 worker 內阻塞等待。
4. **busy／defer 語意（主大腦已拍板）**：lease 被持有時，新請求回明確 409 busy（含 holder 身份類別與重試指引），不排隊、不丟訊、不謊報成功。背景寫入獨立於 lease（不被丟棄、不標成功），但必須標記為非請求寫入，**不得混入**請求的 outcome evidence。
5. **fail-closed（主大腦已拍板）**：任何接口事件缺 context 登記→直接 uncertain＋日誌，**禁止** gateway／router／TTS／社交 fallback 派送。舊入口相容：feature flag 關＝舊行為逐字不變；開之前須有相容 fixture 證明舊入口結果語意不變。
6. **輸出 EI-2B 程式工單草案**：確切檔案／函式、schema、呼叫順序、提交與恢復步驟、fake-clock／fault-injection fixture、逐項硬閘門、不做清單。缺任一項就標 BLOCKED 留給主腦裁決，不讓程式執行者猜設計。

### 驗收（完成的定義）

- `WRITER-CENSUS.md`：每個持久寫入點有 file:line＋四分類＋request ID 關聯＋崩潰可判斷性；無「應該沒有其他寫入」式宣稱。
- `B7-MUTEX-DESIGN.md`：coordinator 放置圖、lease 生命週期、busy／defer 回應表、背景寫入歸屬表、fail-closed 規則、死鎖禁令的執行機制（grant-before-publish＋超時路徑）。
- `EI-2B-PROGRAM-WORK-ORDER-DRAFT.md`：decision-complete 格式齊全，可直接施工。
- 全程只讀：`git status --porcelain -- src tests configs scripts` 與開工前一致（主腦驗）。

### 測試

- 本票不跑 pytest、不寫測試檔。驗證方式＝主腦逐項抽查 file:line 引用是否真實存在。
- 若發現 census 遺漏寫入點，補上並標註，不自行擴範圍進程式實作。

### 不做（Out of Scope）

- 不寫任何程式、不改 repo、不跑全庫、不碰 frozen、不動 cohort 台帳、不 commit／push、不部署、不重啟生產。
- 不宣稱 EI-2B 已解；本票只是設計輸入。

### Frozen Contract 注意

- 只讀不碰：Agency 4 stages、TriggerEnvelope、InnerLifeEvent、4 handlers、SAGE 寫入、world middleware、衰減門檻、cohort 台帳。

### 回報格式

- 三份文件路徑＋開工前後 `git status` 對照。
- census 行數／寫入點數；設計中無法閉合的缺口清單（標 BLOCKED 原因）。
- 是否踩 Frozen／意外行為。
