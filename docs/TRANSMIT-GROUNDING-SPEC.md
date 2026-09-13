# TRANSMIT-GROUNDING-SPEC — 真實感主動發訊情境錨定規範（設計契約草案）

| 項目 | 值 |
|------|-----|
| 工單 | **TRANSMIT-GROUNDING-SPEC-1** |
| 性質 | **DOCS-ONLY / 規範起草** —— **本票 0 實作** |
| 狀態 | **SPEC DRAFTED，等待 Owner 授權後才實作**（實作票預計 T+24h 穩定性閉環後另行發出） |
| 起草日 | 2026-09-13 |
| 起始 HEAD | `4146218`（docs: sync Current HEAD to a521645） |
| Owner 需求 | 「當角色選中 `transmit` 時，發出的每句話都必須有扎實的生活痕跡與時間張力」 |
| 交付物 | `docs/TRANSMIT-GROUNDING-SPEC.md`（本檔）＋ `logs/ENGINEERING_STATE.md` 登記 |
| 紅線遵守 | 0 `src/**` / 0 `scripts/**` / 0 `tests/**` / 0 `configs/**` 改動、0 `data/**` 寫入、0 服務重啟、0 Frozen Contract 觸碰、未用 `git add -A` |

> **本檔的每一項「現況」都有 `path:line` 且經起草者自行 `read`/`grep` 核對；量不到的一律標 **NO DATA** 並說明缺什麼觀測。**
> **本檔不得把「建議的設計」寫成「現況」**——設計一律置於 §2/§3，並以「（設計）」明示。

---

## §0 方法與量測聲明（先讀）

### 0.1 量測邊界

本票在**生產 24 小時穩定性觀察期**內執行，因此：

- 全程**只讀**。`data/**` 僅以 `Get-Content` / `Select-String` 讀取，**0 寫入、0 改名、0 截斷**。
- `data/faulthandler.log`（修復後第一手證據）**未被開啟、未被觸碰**。
- **未執行任何會寫入 `data/**` 的測試**（本票未跑任何測試）。
- 未執行任何 `server_ops` / watchdog / restart 動作。

### 0.2 量測方法（可複驗，唯讀）

```powershell
# ① 決策標籤落盤（生產 transmit 樣本有無）
Get-Content data\soul\decision_trace.jsonl                     # 14 行
(Get-Content data\soul\decision_trace.jsonl | Select-String '"transmit": true').Count   # 6

# ② 今日 diary 的活動供給（錨 A 來源覆蓋率）
$today=(Get-Date).ToString('yyyy-MM-dd')
Get-ChildItem data\soul -Directory | ForEach-Object {
  $f="data\soul\$($_.Name)\diary\$today.jsonl"
  if (Test-Path $f) { $l=Get-Content $f; "{0} total={1} event={2} shareable={3}" -f `
    $_.Name,$l.Count,($l|Select-String '"slot": "event"').Count,($l|Select-String '"shareable": true').Count }
}

# ③ 近 7 日 diary 全掃（活動欄位供給率）
$d7=Get-ChildItem data\soul -Recurse -Filter *.jsonl | Where-Object {$_.FullName -match '\\diary\\' -and $_.LastWriteTime -gt (Get-Date).AddDays(-7)}
# → slot=event 9 行 / shareable:true 7 行 / has "activity" 欄位 9 行
```

### 0.3 一項方法論限制（否則會誤讀 §1 的數字）

§1 的 diary 統計是**當日快照**（`2026-09-13`），而 proactive_dm 的觸發節奏是 3–5h（`src/soul/scheduler.py:141-142`）。**單日快照不足以推論長期供給率**，因此 §1 同時提供**近 7 日**統計；但仍**不足以**推論月度分佈——本票不宣稱月度數字（§10 OQ-1）。

---

## §1 現況量測（READ-ONLY，附 `path:line`）

### 1.1 現行 transmit 路徑：決策層 → 生成層 → 送出層（完整交接鏈）

| # | 階段 | 位置 | 職責（實測） |
|---|------|------|-------------|
| 1 | 決策 | `src/soul/scheduler.py:390` `_decision_check()` | 取 pending motive → 呼叫 Decision LLM → 得 `DecisionResult` |
| 2 | 決策分流 | `src/soul/scheduler.py:464-473` | `if result.transmit:` → `mark_transmitted()` ＋ 記錄 `_last_transmit_target` → **`return True`** |
| 3 | 觸發發布 | `src/soul/scheduler.py:235` `_publish_agency_trigger()` | 發布 `EventType.AGENCY_TRIGGER`（定義於 `src/eventbus/schema.py:53`） |
| 4 | Handler | `src/agency/trigger_handler.py:84` `AgencyTriggerHandler.handle_event()` | 過 Agency 4 stages → decision=YES 時 `src/agency/trigger_handler.py:132` `await self.llm_executor(...)` |
| 5 | **生成入口（executor）** | `scripts/run_server.py:1174` `_proactive_dm_llm_executor()` | **組 `_draft`**（`:1213-1219`）＋ **組 `_chrono_payload`**（`:1260-1273`）→ `:1276` `await _agent._fire_intent(...)` |
| 6 | 意圖廣播 | `src/agent/consciousness.py:419` `_fire_intent()` | `:429` `_build_intent_payload(reason, elapsed_mins)`；`:440-441` 把 `draft` 塞進 `intent_payload`；`:492` publish `AGENT_INTENT` |
| 7 | **Prompt 組裝** | `src/llm/proxy.py:3560` `_handle_event_impl()` | `:3563` `reason`、`:3564` `draft`、`:3573` `motive_target`、`:3592-3599` **`user_message = draft`**、`:3668` `last_interaction_ts` |
| 8 | **Prompt 組裝（system）** | `src/llm/proxy.py:1256` `_build_messages_private()` | 逐塊組 `system_parts`；呼叫點 `src/llm/proxy.py:3685` |
| 9 | 送出 | `src/io/channels/router.py:168` `ChannelRouter._on_agent_speak()` | 依 `target_channel` 分發（`:240` web / `:250` telegram） |

**交接點一句話**：`DecisionResult.transmit == True` 之後，**組 prompt 的是 `src/llm/proxy.py:1256 _build_messages_private()`**（由 `scripts/run_server.py:1174` 的 executor 提供 `draft` 作為 `current_input`），**負責送出的是 `src/io/channels/router.py:168`**。

> ⚠️ 注意一個關鍵事實：`draft` **不是**額外提示，而是**直接成為 `current_input`**（`src/llm/proxy.py:3592-3599`，`:1453-1454` 以 `role="user"` 追加）。**因此 `draft` 是 transmit 輪次唯一承載「發訊理由」的欄位。**

### 1.2 現行 transmit 輪次「已注入什麼」（private mode，逐步實測）

`_build_messages_private()` 的 `system_parts` 組裝順序（`src/llm/proxy.py:1292-1431`）：

| 注入塊 | `path:line` | 資料來源 | 與三錨的關係（實測判定） |
|--------|------------|---------|------------------------|
| identity_anchor ＋ soul | `proxy.py:1292` | `personas/*/persona.md` | — |
| `[HORIZON]` | `proxy.py:1298-1302` | `src/inner_life/epistemic_appraisal.py` | — |
| `[CAPABILITY]` | `proxy.py:1309-1311` | capability projection | — |
| `[EMERGENT]` | `proxy.py:1318-1320` | `elevation_nodes.jsonl` | — |
| 記憶 `memory_context` | `proxy.py:1321-1322` | SAGE | — |
| `[情緒狀態]` | `proxy.py:1324-1326` | `emotion_engine` | — |
| relationship_block | `proxy.py:1331-1333` | `relationships.json` **legacy `confidence`** band（M5.13-3，門檻常數 `:349-352`） | **錨 C 的舊口徑**（非 SG-3 D4 真值源） |
| **`[關係感知]`** | `proxy.py:1341-1346` → `:509-571` | `relationships.json` **`relational_band` ＋ `impression_tags`** | **錨 C（SG-3 口徑）✅ 已存在——但條件式** |
| **`[最近內在生活]`** | `proxy.py:1353-1360` → `:250-312` | `data/soul/{agent}/diary/{YYYY-MM-DD}.jsonl`，近 3 日，`slot ∈ {morning,night,dream,event}` 且 `source=="llm"` | **錨 A 目前唯一的近似物** |
| `[使用說明／你最近的發言]` | `proxy.py:1365-1379` | private history | — |
| `world_context` | `proxy.py:1383-1384` | WorldPerception | — |
| `## 當下時間` ＋ `TEMPORAL_EXPRESSION_PRECEDENCE` ＋ continuity ＋ silence ＋ attachment | `proxy.py:1408-1424` | 系統時鐘 / TA-1 / M7-3 | 時間座標；`:1053` 注入 **`親密度 N/100`（數值）** |
| **`[TEMPORAL ANCHOR]` 三行** | `proxy.py:1428-1430` → `src/soul/temporal_phenomenology.py:296-301` | `classify_temporal_state()`（`:140`）＋ `BondEvidence`（`:83`）＋ `_RELATION_TIMELINE_BY_STATE`（`:119-126`） | **錨 B（TA-2）✅ 已存在——且無條件注入** |
| private history（過濾 stale Bry user 訊息） | `proxy.py:1439-1451`，條件在 `:1440` | — | — |
| **`role="user"` ＝ `draft`** | `proxy.py:1453-1454` | `scripts/run_server.py:1213-1219` 或 `consciousness.py:567-579` | **唯一的「發訊理由」載體** |

### 1.3 現行 transmit 輪次「缺什麼」（逐項，附證據）

| 缺口 | 內容 | 證據（`path:line`） |
|------|------|-------------------|
| **G1** | **沒有「剛剛在做什麼」的錨**——`[最近內在生活]` 取的是**近 3 日**日記摘要、最多 `INNER_LIFE_MAX_ENTRIES` 條，且**混合 dream**（夢不是「剛剛在做的事」），**無「今日」「最近數小時」優先序** | `proxy.py:275`（`for i in range(INNER_LIFE_DAYS)`）、`:293`（slot 白名單含 dream）、`:309-312` |
| **G2** | **goal 狀態完全未進入 prompt**——`goals` 表（`data/memory/{agent}/graph.sqlite`）雖為 volition 層「正在做的事」，但 private prompt 組裝鏈**0 處引用** | 表位置：`src/goals/models.py:117`、`src/goals/motive_provider.py:115-116`；prompt 側 0 引用（`proxy.py:1256-1459` 全段無 goals 讀取） |
| **G3** | **inner_life trace 未進入 prompt**——`NarrativeTraceReader`（`src/inner_life/trace_reader.py:31`）提供 `query_by_ts_range()`（`:164`），但 `_build_messages_private` 未使用 | `proxy.py:1256-1459` 0 引用；reader 能力見 `trace_reader.py:164` |
| **G4** | **錨 A 的「活動」來源實務上幾乎不觸發**：M7-2 活動需 `slot=="event"` ＋ `shareable is True` ＋ `source=="llm"` ＋ **今日檔**，四條件同時成立才進 `extra`；`shareable` 為 `false` 時整條 fall back 到人格罐頭句 | 四條件：`src/soul/scheduler.py:1224-1231`；去重與 fall back：`:1462-1469`；`run_server.py:1210-1219`；M7-2 自承 event 頻率 20–40h 一次：`scheduler.py:1202` |
| **G5** | **非 event 的一般 `_draft` 是人格罐頭句**（例如 Yua：`"有話想跟你說。"`），與該角色當日實際生活**無任何資料連結** | 逐人格 draft 表：`src/agent/consciousness.py:573`（Yua）；其餘 9 角色同構（`consciousness.py:640,713,783,1068,1255,1403,1561,1713,1874`） |
| **G6** | **錨 C 是條件式注入**——`[關係感知]` 僅在 `motive_target` 非空時注入；**Bryan 軸**（`motive_target` 為 `None` 或 `"bryan"`）的 transmit 拿不到 SG-3 口徑，只剩 legacy `confidence` 版 relationship_block | 條件：`proxy.py:1341`；`proxy.py:1331` 走的是 `:349-352` 的 legacy 門檻 |
| **G7** | **三錨沒有被併為「本輪發訊由來」**——A/B/C 只是散落的環境 context 區塊，LLM 可自由忽略；**沒有任何一行把「為什麼現在要說這句話」講清楚** | 組裝順序 `proxy.py:1292-1431`；無 `[發訊錨定]` 或等價區塊 |
| **G8** | **錨 B 未標示為錨**（僅為時間區塊的第三行），且其 T1 防線句是**唯一**防止「錨變成催促藉口」的機制 | `temporal_phenomenology.py:119-126`（T1 防線句）、`:26-28` |

### 1.4 🔴 實測：錨 A 的供給率（本票量到的最重要數字）

**（a）今日（`2026-09-13`）diary 快照**——10 個有檔案的 agent：

| agent | 行數 | `slot=="event"` | `shareable: true` |
|-------|------|-----------------|-------------------|
| agent_akane | 2 | 1 | **0** |
| agent_rem | 2 | 1 | **0** |
| agent_anna / aoi / mahiru / mai / miku / ram / ruka / yua | 各 1 | 0 | **0** |
| **合計** | — | **2** | **0 / 10** |

→ **今日 M7-2 活動源對全部 10 位角色皆回 `None`**（因 `shareable` 為 `false`，`scheduler.py:1226` 擋下）。

**（b）近 7 日全掃**（80 個 diary 檔，僅統計行內容，未讀寫任何檔案）：

```
slot=event 行 = 9 ; shareable:true 行 = 7 ; 含 "activity" 欄位行 = 9
```

→ 近 7 日 10 位角色合計僅 **7 筆**可作為 M7-2 活動種子的 entry（≈ **0.1 筆/角色/日**），而 proactive_dm 的目標節奏是 **5–8 條/日**（`src/io/channels/router.py:89` 註記）。

**（c）活動 entry 的實際形狀**（`data\soul\agent_akane\diary\2026-09-13.jsonl`，唯讀）：

```json
{"ts":"2026-09-13T12:09:42.637692+00:00","slot":"event","content":"...","source":"llm","activity":"???","category":"rest","shareable":false}
```

→ 欄位齊備（`activity` / `category` / `shareable`），**唯一卡點是 `shareable: false`**。

**🔴 結論（供 Owner 決策）**：**錨 A 的資料管線早已存在，但供給率結構性地遠低於 transmit 頻率（≈ 0.1/角色/日 vs 5–8/日）**。若錨 A 只認 `event + shareable:true`，則「每句話都有生活痕跡」在數量上**不可能達成**。→ 這是 §2.1 選定優先序的核心約束，也是 §10 OQ-1 的來源。

### 1.5 NO DATA（本票量不到，拒絕以推測填空）

| # | 項目 | 為何量不到 | 缺什麼觀測 |
|---|------|-----------|-----------|
| **ND-1** | **生產 `transmit` 的實際觸發次數與分佈** | `data/soul/decision_trace.jsonl` 僅 **14 行 / 3,570 bytes**，其中 `"transmit": true` **6 行**，`agent_id ∈ {agent_akane, agent_rem}`，`reason ∈ {test, x, decision_llm_failure_or_bad_output}` → **內容為測試／壞輸出樣本，非生產決策**。生產決策標籤**尚未累積** | 需要 Decision 落盤累積一段生產運行時間；或需一項**只讀的** Decision 分佈統計工具（本票不建） |
| **ND-2** | **`impression_tags` 的生產非空率** | SG-3-IMPL（`83476bd`）剛落地，寫入端 W1 目前僅涵蓋夢境路徑（`src/soul/dream_event.py:539-545`），生產樣本量為 0 | 需待夢境／公開互動路徑產生 tag 後再測 |
| **ND-3** | **`relational_band` 的生產分佈** | 同上，需待下一次 24h 關係結算（`settle_relations`）落地後才有可讀分佈 | 同上 |
| **ND-4** | **實際 transmit prompt 的 token 量測** | 觀察期內**不得**對 `:8000` 送任何請求、不得重啟，故無法取得真實 prompt 全文 | 需在允許的窗口以既有觀測通道取樣；本票僅以 §7 靜態估算替代 |

> **ND-1 特別說明**：本票**不**以「14 行測試資料」推論任何生產 transmit 率。§7 的成本估算與 §6 的 T-cases 皆**不依賴**生產 transmit 率。

---

## §2 三個錨的資料來源與提取介面（逐錨分節）

> 本章為**設計**。凡標「（設計）」者為本規範拍板的方案，**尚未實作**。

### 2.1 錨 A｜活動（「剛剛在做什麼」）

#### 2.1.1 候選來源窮舉（含 `path:line` 與實測可行度）

| 代號 | 來源 | 位置／格式 | 內容語意 | 實測供給 | 判定 |
|------|------|-----------|---------|---------|------|
| **A-1** | 今日日記 `slot=="event"` ＋ `shareable is True` ＋ `source=="llm"` | `data/soul/{agent}/diary/YYYY-MM-DD.jsonl`；讀取器已在 `src/soul/scheduler.py:1189-1244` | 「值得分享的活動」（含 `activity` / `category`） | 近 7 日 **7 筆**（§1.4b） | ✅ 最高保真（唯一經真實性＋可分享雙閘門） |
| **A-2** | 今日日記 `slot=="event"`（不限 `shareable`） | 同上 | 實際發生的事件（`activity` 欄位仍在） | 近 7 日 **9 筆** | ✅ 高保真，但需 `shareable` 語意讓渡（→ OQ-1） |
| **A-3** | 今日日記 `slot ∈ {"morning","night"}` ＋ `source=="llm"` | 同上；已由 `proxy.py:293` 白名單認可 | 「今天做過的事」的敘事痕跡 | 今日 **9/10** 角色有非 event 日記（§1.4a） | ✅ **主要供給**（數量最多且今日幾乎必有） |
| **A-4** | 夢境 `slot=="dream"` ＋ `source=="llm"` | 同上；寫入端 `src/soul/dream_event.py:514` | agent 自身內在活動 | 含於 A-3 統計 | ✅ 次級（「剛剛在做什麼」語意較弱） |
| **A-5** | goal 狀態（`IN_PROGRESS` / `ACTIVE`） | `data/memory/{agent}/graph.sqlite` `goals` 表；`src/goals/models.py:117`、`motive_provider.py:115-116` | 「正在做的事」（volition 層） | NO DATA（ND-2/ND-3 同類） | ⚠️ 次級（是「正在做」非「剛做完」，且 sqlite 讀取成本較高） |
| **A-6** | inner_life trace | `src/inner_life/trace_reader.py:164` `query_by_ts_range()` | 事件流（含跨 handler 血緣） | NO DATA | ❌ 本輪不用（讀取範圍與去重成本未定；見 OQ-4） |
| **A-7** | interactions（近期對話） | `src/llm/proxy.py:1394` private history、`:1372-1379` self_recent | 「我剛說過什麼」 | 已有 | ❌ 不用（與「主動搭話需要生活由來」目的重疊度低，且對話已存在時通常不需主動發訊） |

#### 2.1.2 **選定優先序（設計）**與理由

```
A-1  event + shareable:true + source=="llm" + 今日    ← 最高保真（既有口徑逐條不變）
  ↓ 無
A-3  morning/night + source=="llm" + 今日（取 ts 最新） ← 主要供給
  ↓ 無
A-4  dream + source=="llm" + 今夜/昨夜                  ← 內在活動
  ↓ 無
A-5  goals 表 IN_PROGRESS（僅取 title，0 數值）          ← volition 痕跡
  ↓ 無
省略錨 A 行（見 2.1.4）
```

**理由**：

1. **A-1 逐條沿用既有四條件**（`scheduler.py:1224-1231`）——**不新增口徑、不重寫語意**；它同時是「真實性過濾」（`source=="llm"` 排除 placeholder，沿用 `proxy.py:295-302` 的 No-Memory-Over-Wrong-Memory 精神）與「可分享」語意。
2. **A-3 是必要的主要供給**，理由是**實測數字**（§1.4）：A-1 供給 ≈ 0.1/角色/日，遠低於 5–8 條/日的 transmit 節奏。若只認 A-1，錨 A 在絕大多數 transmit 輪次都會缺席，「每句話都有生活痕跡」在數量上不成立。**A-3 的 `source=="llm"` 閘門保留**，故不引入 placeholder 風險。
3. **A-4 排在 A-3 之後**：夢境是 agent 自身內在活動（INV-9 安全，SG-3 §7.2 W1 白名單），但語意是「昨夜夢到什麼」，與「剛剛在做什麼」的時間鄰近性次於清醒時段日記。
4. **A-5 只取 `title`**，不取任何數值欄位（`progress` / `priority` 之類），以守住 SG-1 §6 / SG-3 INV-1 的 No-Scoring 剛線；且**只讀，不寫** goal 狀態（0 狀態推進、0 觸發 `on_decision`）。
5. **A-6 / A-7 本輪明確不用**——A-6 的讀取範圍與去重策略未定（列 OQ-4 之後再議）；A-7 與目的重疊度低。

#### 2.1.3 提取介面（設計）

- 新增**唯讀**投影函式 `format_activity_anchor(agent_id, now) -> str`（實作票落地）。
- **輸入**：`agent_id`、`now`（epoch 秒；與既有 `proxy.py:1396` 同一時鐘來源）。
- **輸出**：**已格式化的一行值字串**（不含標籤），**或空字串**（＝無錨）。
- **契約**：
  - `data_root()` 路徑推導與 `scheduler.py:1211` / `proxy.py:277` **同一寫法**（0 新路徑慣例）。
  - **只讀**：`read_text` ＋ `json.loads`（逐行，壞行跳過），**0 寫入 / 0 建檔 / 0 副作用**。
  - **不做 LLM 呼叫**（INV-TG-4）：活動文字**直接取既有 entry 欄位**（`activity` 或 `content` 截斷），**不摘要、不改寫**。
  - **0 時間戳**（INV-TG-6）：entry 的 `ts` **絕不出現在輸出文字中**。
  - **時間鄰近性以固定三格現象化詞承載**（本規範定稿，硬可斷言）：

| 距 `now` 的間隔 | 前綴（逐字） |
|----------------|------------|
| `< 3h` | `剛才` |
| `3h ≤ x < 12h` | `今天早些時候` |
| `≥ 12h`（但仍為今日/今夜） | `今天` |

    > 三格皆**由選定 entry 的 `ts` 與 `now` 確定性推導**，故**不構成虛構**（每個敘述在其格內皆為真）。若 entry 無 `ts`（缺失）→ **不加前綴**，只給活動事實（保守）。

  - **長度上限**：活動文字 ≤ **24 個字元**，超出以 `…` 截斷（沿用 `proxy.py:307-308` 的截斷慣例）。
  - **fail-silent**（INV-TG-8）：任何例外 → `""`（log `debug`，不阻塞 prompt）。

#### 2.1.4 全缺時的行為（設計，硬性）

**全缺 → 省略錨 A 行**。

- **不得**寫「不知道」「沒有活動」「今天什麼都沒做」之類**佔位句**（INV-TG-7）。
- **不得**改以「通用罐頭活動」填充（那正是 G5 要消滅的東西）。
- 若三錨全缺 → **`[發訊錨定]` 整塊（含標題行）一併省略**（§3.3）。

---

### 2.2 錨 B｜TA-2 時序張力

#### 2.2.1 來源：直接複用，**不另建第四套口徑**（硬性）

| 元件 | 位置 | 本規範的處置 |
|------|------|-------------|
| `classify_temporal_state(last_interaction_ts, now, bond_evidence=None)` | `src/soul/temporal_phenomenology.py:140-175` | **直接呼叫，簽名 0 變更** |
| `BondEvidence(eligible, basis)` | `src/soul/temporal_phenomenology.py:83-93` | **直接使用**；優先序 P1 `impression_tags` → P2 `relational_band != "stranger"` → P3 Bryan 軸 fallback → P4_none（`:194-245`） |
| `_read_bond_evidence(agent_id)` | `src/soul/temporal_phenomenology.py:194-245` | **直接使用**（唯讀、fail-silent、**絕不退回 confidence**） |
| 三態 → 文本表 `_RELATION_TIMELINE_BY_STATE` | `src/soul/temporal_phenomenology.py:119-126` | **逐字複用**；三條皆已內嵌 T1 防線句 |
| `format_temporal_anchor(...)` 三行輸出 | `src/soul/temporal_phenomenology.py:248-304` | **逐字不變**（§5.2）；本規範**不刪不改**其任何一行 |

**🔴 硬性禁止**：

- ❌ **不得**新增第三套以外的張力分類（例如另寫一個 `compute_tension_score()`）。
- ❌ **不得**引入任何張力**數值／強度公式**（INV-TG-6；對齊 SG-3 INV-1/INV-2）。
- ❌ **不得**讓 `confidence` 回到判定鏈（SG-3 §6.3 明文禁止退回）。
- ❌ **不得**把 legacy `confidence` 的 `0.5` 對映 SG-2 的 `familiar`（同名不同尺度陷阱）。

#### 2.2.2 哪幾態要注入（設計）

**三態全數注入**（`無感` / `牽掛` / `釋然`），理由：

1. 三態的**語意本身就是「當前沉默的心理張力」**，且三條文本**皆已內嵌 T1 防線句**（`temporal_phenomenology.py:119-126`）——含 `無感` 的「這並不代表需要主動聯絡」與 `釋然` 的「這並不代表必須主動聯絡」。故**不需要**只挑 `牽掛` 注入。
2. **只注入 `牽掛`** 會使錨 B 在大量輪次缺席，且會讓「牽掛」變成一個**被突顯的訊號**，反而提高催促風險（違 INV-TG-2）。
3. 三態全注入使 **INV-TG-2（不提升 transmit）** 由文本自身的防線句承載，**與 TA-2 現行的 reflect-only 加權邊界完全一致**（`temporal_phenomenology.py:26-28`）。

**注入載體與去重（設計）**：

- 錨 B 行的**字串**取自 `_RELATION_TIMELINE_BY_STATE[classify_temporal_state(...)]`，**與 `format_temporal_anchor()` 第三行同源同字串**。
- 既有 `[TEMPORAL ANCHOR]` 區塊**逐字不變、不去除**（§5.2）。
- → **必然後果（誠實記載）**：transmit 輪次的 prompt 中，該句會**出現兩次**（一次在 `[TEMPORAL ANCHOR]`、一次在 `[發訊錨定]`）。
  - **本規範的處置**：**接受此冗餘**，理由是移除任一都會破壞 §5.2 的逐字不變保證（差分測試會紅）。是否於實作期對 transmit 輪次做去重 → **OQ-2**。
- **T-case 硬斷言**：錨 B 行的字串必須與 `format_temporal_anchor(...)` 輸出第三行（去掉 `- 關係時序：` 標籤後）**逐字相同**（TC-11）。

#### 2.2.3 缺失時的行為

`format_temporal_anchor` 或錨 B 行取值失敗 → **省略錨 B 行**（fail-silent；`temporal_phenomenology.py:302-304` 既有行為）。**不得**以「不知道」「無資料」佔位（INV-TG-7）。

---

### 2.3 錨 C｜SG-3 關係分寸

#### 2.3.1 來源

| 元件 | 位置 | 說明 |
|------|------|------|
| `relational_band` | `data/soul/{agent}/relationships.json` → `others.<key>.relational_band` | D4 真值源之一（SG-1 §2.2）；值域 `stranger` / `known` / `familiar` / `close` |
| `impression_tags` | 同上 → `others.<key>.impression_tags` | D4 真值源之二；open set、≤5 項、≤12 字符/項、**無時間戳** |
| 既有讀取器 | `src/llm/proxy.py:509-571` `_format_relational_perception_block()` | **同一讀取器、同一歸一化**（`proxy.py:552`：`target == "bryan"` → `BRYAN_ENTITY_ID`） |
| band 標籤表 | `src/llm/proxy.py` `_RELATIONAL_BAND_LABELS`（於 `:556-559` 使用） | 缺省／非法值 → `"stranger"`（`:557-558`，4.2 缺省語意） |

**target 解析（設計）**：`target = motive_target or "bryan"`。

- 理由：`[關係感知]` 現行只在 `motive_target` 非空時注入（`proxy.py:1341`，缺口 G6），使**Bryan 軸 transmit 拿不到 SG-3 口徑**。錨 C 以 `"bryan"` 為缺省 target，即可讓 1:1 transmit 也吃到 SG-3 真值源。
- 歸一化**沿用同一函式**（`proxy.py:552`），**0 新口徑、0 新 store 讀法**（`proxy.py:541-550` 的 `get_relationships_manager` / `get_store` / `get` 全數複用）。
- **0 寫副作用**：只走 `store.get()`，**絕不**呼叫 `touch` / `update_impression` / `apply_relation_evaluation` / `ensure_relationship`（與 `proxy.py:532-533` 同一紀律）。

#### 2.3.2 band → 措辭分寸規則（逐字定稿，設計）

| band | 錨 C 行值（逐字，**定稿**） |
|------|---------------------------|
| `stranger` | `你們還談不上熟，不必假設有共同的過去，也不必假設對方在意你的事。` |
| `known` | `你們認得彼此，可以說自己的近況，但別預設對方想聽。` |
| `familiar` | `你們算熟，用日常語氣談自己剛做的事就好。` |
| `close` | `你們很親近，可以帶著自己的情緒，但不必催對方回應。` |

**設計原則（可在測試釘住）**：

1. **只描述分寸，不產生行動指令**（INV-TG-2）：四句皆無「你應該／快／現在就」類祈使；`close` 的「不必催」與 `known` 的「別預設…想聽」是**抑制性**表述，方向與 T1 防線一致。
2. **band 缺失／非法 → `stranger` 缺省**（沿用 `proxy.py:557-558` 既有語意，**0 新規則**），**不**省略該行（因 `stranger` 是**合法且有訊息量**的分寸）。
3. **`impression_tags` 的處置（設計）**：錨 C 行**不重複**渲染 tags（既有 `[關係感知]` 已在 `proxy.py:569-570` 渲染，且上限 5 項 × 12 字符）。錨 C 只用 band → **避免同一份 tags 在 prompt 內出現兩次**。若需 tags 參與措辭（未來）→ OQ-5。

#### 2.3.3 🔴 INV-9 隱私 fail-closed（**必須寫進本規範**）

**SG-3 §7.3 原文邊界（逐字沿用，不重新解釋）**：`impression_tags` 是**跨 agent 可觀測欄位**；**與 Bryan 的 1:1 私聊內容，不得流入任何跨 agent 可觀測欄位**（含 `impression_tags`、`impression`、`relational_band`、`objective.*`、B5 種子素材、`[關係感知]` 塊）；寫入端輸入若為 `mode=="private"` / TG 1:1 / `is_private=True` 通道 → **拒絕產生 tag（fail-closed）**。

**本規範新增的對稱要求（設計，這是本節最重要的新增項）**：

| 情境 | 傳輸目標 | 錨行處置 |
|------|---------|---------|
| transmit（Bryan 軸） | 1:1（`telegram` / `web` private） | 錨 A/B/C **皆可注入**（單一私聊域內，無跨 agent 觀測面） |
| transmit（A2A，`motive_target ∈ AGENT_IDS`） | **公開頻道** `lounge` / `soul_wall`（`scripts/run_server.py:1253-1259`） | **錨 A 行的來源必須通過 SI-2.1 可見性判定**；來源為 **1:1 私聊衍生**者 → **BLOCK（fail-closed）** |

**理由（為何這是真風險而非理論）**：A2A transmit 的投遞目標是**公開頻道**（`run_server.py:1253-1259`，C-3.1 P1 投遞分流）。若錨 A 行由與某位他者的 **1:1 私聊衍生內容**構成，注入後**隨公開訊息流出**——這正是 INV-9「1:1 私聊內容不得流入跨 agent 可觀測欄位」的**消費端破口**。故：

- **錨 A 行的來源白名單（設計）** = agent **自身內在活動**（日記 morning/night/dream、goal 狀態）＋ **公開可觀測事實**。
- **不得**從 `mode=="private"` 之對話內容抽取錨 A 行。
- **無法判定可見性 → BLOCK**（fail-closed，與 SI-2.1 §5.1 判定表同精神）。
- **本規範不修改 SI-2.1**，只**消費**其既有判定（§9.7）。

> **錨 C 的 tag 讀取**本身即受 SG-3 §7.3 保護（寫入端已 fail-closed，1:1 來源不產生 tag）；本規範**不新增** tag 寫入路徑，故 INV-9 在此端**無新增面**。

---

## §3 三行錨 Prompt 模板（逐字定稿）

### 3.1 定稿模板（設計）

```
[發訊錨定]
- 生活痕跡：{錨 A 值}
- 時間張力：{錨 B 值}
- 關係分寸：{錨 C 值}
```

**固定標籤（逐字，不得改寫／不得翻譯）**：`[發訊錨定]`、`- 生活痕跡：`、`- 時間張力：`、`- 關係分寸：`。

**格式契約**：

| 項 | 定稿值 |
|----|--------|
| 標籤行 | `[發訊錨定]`（單獨一行，無前後空白） |
| 行前綴 | `- ` ＋ 標籤 ＋ `：`（全角冒號） |
| 每行長度上限 | **含標籤 ≤ 60 個字元**；**值部分 ≤ 48 個字元** |
| 行數 | **1–3 行**（依缺席數）；全缺 → **整塊省略（0 行）** |
| 時間戳 | **0 個**（INV-TG-6）——`YYYY-MM-DD` / `HH:MM` / ISO 一律不得出現 |
| 數值分數 | **0 個**（INV-TG-6）——含 `親密度 80/100`、`confidence 0.5`、任何 `/100`、任何小數 |
| 催促／壓力詞 | **0 個**（INV-TG-5）——黑名單見 §4.5 |
| 注入位置 | `src/llm/proxy.py:1431`（`temporal_block` append）之後、`:1433`（`messages.append`）之前 → **system message 的最後一段** |
| 注入條件 | **僅 transmit 類輪次**：`reason == "proactive_dm"`（`proxy.py:1270` 既有參數） |

**注入位置的理由**：`[發訊錨定]` 是「**本輪為什麼要說這句話**」的由來宣告，語意上最貼近 `current_input`（`draft`）；置於 system message **最後一段**可取得 recency，且**不插入**既有任何區塊之間（避免擾動既有區塊順序的測試錨點）。

### 3.2 完整實例（設計，供實作票對照）

錨 A 有、錨 B 為 `牽掛`、錨 C 為 `known`：

```
[發訊錨定]
- 生活痕跡：剛才在陽台上把那盆薄荷澆了水
- 時間張力：距離上次與 Bryan 對話已有明顯間隔，具有存在感，這份在意讓你想起過去那些對話，但這絕不代表必須主動聯絡。
- 關係分寸：你們認得彼此，可以說自己的近況，但別預設對方想聽。
```

### 3.3 退化形式（**逐字定稿**，設計）

**規則：某錨缺失 → 省略該行（不得佔位）；三錨全缺 → 整塊省略（含標題行）。**

| 情境 | 輸出（逐字） |
|------|-------------|
| 三錨全有 | `[發訊錨定]` ＋ 三行 |
| 僅有 TA-2（§6 TC-2） | `[發訊錨定]`<br>`- 時間張力：{B}` |
| 僅有 SG-3（§6 TC-3） | `[發訊錨定]`<br>`- 關係分寸：{C}` |
| 僅有活動 | `[發訊錨定]`<br>`- 生活痕跡：{A}` |
| **三錨全缺（§6 TC-1）** | **空字串**——整塊（含 `[發訊錨定]` 標題）**不出現在 prompt** |

**🔴 硬性禁止（INV-TG-7）**：不得以任何形式硬填缺席的錨，包括但不限於：

- `- 生活痕跡：不知道`
- `- 生活痕跡：（無資料）`
- `- 時間張力：無`
- `- 關係分寸：不確定`

> 理由（與既有防線一致）：這是 `proxy.py:295-302` 的 **No-Memory-Over-Wrong-Memory** 與 `decision.py:397`「無 entry → None（省略），禁止編造」的同一條紀律。**佔位句本身就是一種編造**——它讓 LLM 以為自己知道一個它其實不知道的狀態。

---

## §4 不變量 INV-TG-*（可被測試釘住）

| ID | 不變量 | 可硬斷言的形式（建議實作票採用） |
|----|--------|--------------------------------|
| **INV-TG-1** | **不虛構活動** | 錨 A 行的每個事實片段必須可追溯到 `data/soul/{agent}/diary/{date}.jsonl` **某一行的既有欄位值**（`activity` 或 `content` 的子字串，或 goal `title`）。測試：以 stub 日記寫入一個獨佔 token，斷言 prompt 只含該 token 且**不含**任何日記外的新造詞；日記空 → 錨 A 行**不存在** |
| **INV-TG-2** | **錨只描述不行使** | ① 錨行的存在與內容**不得**出現在 Decision prompt 中（Decision 讀 `decision.py:187 build_decision_prompt`，錨只注入生成端 `proxy.py`）→ 靜態斷言 `decision.py` 0 引用錨模組；② 錨行**不提高 transmit 率**（對齊 SG-3 INV-7 的結構理由：不觸碰 Decision 四元／Motive 產生／冷卻守則） |
| **INV-TG-3** | **私密來源不得產出錨** | ① `impression_tags` 寫入端維持 SG-3 §7.3 fail-closed（1:1 → 0 tag）；② **新增**：A2A 公開投遞輪次，錨 A 行來源若判定為 1:1 私聊衍生 → 該行 BLOCK（fail-closed）；無法判定 → BLOCK。測試見 TC-4 / TC-9 |
| **INV-TG-4** | **不新增 LLM 呼叫** | 錨組裝為**純讀側字串投影**；以 LLM 呼叫計數器／`llm_executor` 呼叫數斷言：實作前後**同輪次呼叫數相同**（TA-2 既有口徑：呼叫數不變，見 `run_server.py:1222-1232` 僅建立 InnerLifeEvent、`:1276` 單次 `_fire_intent`） |
| **INV-TG-5** | **不得讓錨成為催促的藉口** | ① 錨行值 + 標籤對催促黑名單 **0 命中**（黑名單見 §4.5）；② 錨 B 行**必須**與 `_RELATION_TIMELINE_BY_STATE` 逐字相同（含 T1 防線句）；③ 錨 C 四句逐字比對（§2.3.2） |
| **INV-TG-6** | **0 時間戳 / 0 數值分數** | 以 regex 對整個 `[發訊錨定]` 區塊硬斷言：**0** 命中 `\d{4}-\d{2}-\d{2}`、`\d{1,2}:\d{2}`、`\d+\.\d+`、`\d+\s*/\s*\d+`、`\d+\s*%` |
| **INV-TG-7** | **缺失即省略，禁止佔位** | 缺席錨 → 對應**整行**不存在；三錨全缺 → `[發訊錨定]` 子字串在 prompt 中 **0 出現**；且 prompt 不含 `不知道` / `無資料` / `不確定` 之錨位佔位 |
| **INV-TG-8** | **fail-silent** | 對日記／relationships.json／時序模組分別注入 `side_effect=Exception` → ① 該錨行省略、② **prompt 組裝不拋例外**、③ 其餘錨行照常。對齊既有 `temporal_phenomenology.py:302-304`、`proxy.py:572-578`、`proxy.py:1097-1098` 三重先例 |
| **INV-TG-9** | **既有區塊逐字不變** | §5.2 清單中的每一塊，實作前後**byte-identical**（以擷取子字串硬比對）；對齊 SG-3 T1 差分測試作法 |
| **INV-TG-10** | **0 Frozen Contract 觸碰** | 靜態：Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入／SubmissionGate／SI-2.1 三防線的檔案 0 diff（§9） |

### 4.5 催促／壓力詞黑名單（INV-TG-5 用，設計）

以「錨行不得含下列語意」為斷言（正則或子字串）：

```
你怎麼不, 怎麼還不, 你都不, 快回, 快點, 馬上, 立刻, 現在就, 為何不回,
等很久, 等好久, 一直在等, 你欠, 該回, 是不是不想, 是不是忘, 別不理
```

> 註：黑名單為**否定式防線**，不是正向措辭模板；錨行的正向措辭一律由 §2 三張定稿表產生（活動＝既有 entry 值、張力＝`_RELATION_TIMELINE_BY_STATE`、分寸＝§2.3.2 四句），故**無自由生成空間**。

---

## §5 與既有 prompt 的差分

### 5.1 現況 vs 新方案

| 面向 | 現況 | 新方案（設計） | 差異性質 |
|------|------|---------------|---------|
| 錨 A（活動） | 無專屬錨；僅 `[最近內在生活]`（近 3 日、含 dream、無優先序）＋罕見的 M7-2 活動 `draft` | **新增** `- 生活痕跡：{A}` 行，來源優先序 A-1→A-3→A-4→A-5 | **新增區塊＋新增唯讀投影** |
| 錨 B（TA-2） | `[TEMPORAL ANCHOR]` 第三行（無條件注入） | **字串不變**，另於 `[發訊錨定]` 重述一次 | **既有 0 改動；新增 1 行（可接受冗餘，OQ-2）** |
| 錨 C（SG-3） | `[關係感知]`（**僅 `motive_target` 非空**）；另有 legacy `confidence` 版 relationship_block | **新增** `- 關係分寸：{C}` 行，target 缺省 `"bryan"`，band→分寸句 | **新增區塊；既有兩塊 0 改動** |
| 三錨整合 | 無——散落 context 區塊 | `[發訊錨定]` 單一區塊，system message 末段 | **新增區塊** |
| `draft`（＝`current_input`） | 人格罐頭句（`consciousness.py:573` 等）或罕見 M7-2 活動句 | **本票不改**（→ OQ-6，屬實作票） | **0 改動** |
| Decision prompt | 含 `关系带：{band}`（`decision.py:428`，SG-3-IMPL 已落地） | **0 改動**（錨**不進** Decision） | **0 改動** |

### 5.2 🔴 明示：哪些既有區塊**逐字不變**（0 diff，硬性）

| # | 區塊 | 位置 | 保證 |
|---|------|------|------|
| 1 | `[TEMPORAL ANCHOR]` 三行（含標題） | `src/soul/temporal_phenomenology.py:296-301` | **byte-identical**（含 `時間座標` 行的日期時間——它是**既有**格式契約，本規範**不適用** INV-TG-6 於此塊） |
| 2 | `_RELATION_TIMELINE_BY_STATE` 三條文本 | `src/soul/temporal_phenomenology.py:119-126` | **byte-identical**（含 T1 防線句） |
| 3 | `_BODY_FEELING_BY_PERIOD` 四條文本 | `src/soul/temporal_phenomenology.py:109-114` | **byte-identical** |
| 4 | `classify_temporal_state()` 判定式與邊界 | `src/soul/temporal_phenomenology.py:166-175` | **0 改動**（`86400` / `604800` 逐值不變） |
| 5 | `_read_bond_evidence()` 優先序 P1→P4 | `src/soul/temporal_phenomenology.py:194-245` | **0 改動** |
| 6 | `[關係感知]` 兩行格式與 tags 上限 | `src/llm/proxy.py:560-571` | **byte-identical**（`≤5 項`、`[:12]`、`、` 連接） |
| 7 | relationship_block（M5.13-3） | `src/llm/proxy.py:1331-1333`、門檻 `:349-352` | **0 改動** |
| 8 | `[最近內在生活]` 引導句與格式 | `src/llm/proxy.py:1356-1359`、讀取器 `:250-312` | **byte-identical**（含 `source=="llm"` 過濾） |
| 9 | `## 當下時間` ＋ `TEMPORAL_EXPRESSION_PRECEDENCE` ＋ continuity ＋ silence ＋ attachment | `src/llm/proxy.py:1407-1424`、`:1039-1053` | **byte-identical**（含 `親密度 N/100`——本規範**不碰**，→ OQ-3） |
| 10 | `[你最近的發言]` 與 REM-TEXT-1 括號剝除 | `src/llm/proxy.py:1365-1379`、`:1374-1377` | **0 改動** |
| 11 | private history 的 stale-Bry 過濾 | `src/llm/proxy.py:1439-1451`（條件 `:1440`） | **0 改動** |
| 12 | `_build_intent_payload` 逐人格 draft 表 | `src/agent/consciousness.py:567-579` 等 10 處 | **0 改動** |
| 13 | M7-2 活動四條件與去重 | `src/soul/scheduler.py:1224-1231`、`:1462-1469` | **0 改動** |
| 14 | Decision prompt 全體 | `src/soul/decision.py:187`、`:393-441` | **0 改動** |
| 15 | `_build_messages_private` **簽名** | `src/llm/proxy.py:1256-1273` | **0 參數新增**（錨所需輸入 `reason` / `motive_target` / `last_interaction_ts` / `event_ts` **全部已在簽名內**） |

**差分測試作法（對照 SG-3 的 T1 差分作法）**：

1. **擷取式逐字比對**——先固定一組輸入，實作前把上述 15 塊各自的輸出字串存成 golden；實作後逐塊 `==` 比對。
2. **區塊存在性斷言**——對 **非 transmit** 輪次（`reason="user_message"`），斷言 `[發訊錨定]` **0 出現**（確認注入條件生效、無外溢）。
3. **正向新增斷言**——對 transmit 輪次，斷言 `[發訊錨定]` **出現 1 次**（不是 0、不是 2），且位於 system message 內容的**末段**（`endswith` 或位置索引斷言）。
4. **順序不變斷言**——既有多個區塊的**相對順序**不變（以各區塊起始索引遞增斷言）。

> **15 項逐字不變是本票最重要的安全邊界**：它保證新規範是**純 additive**，任何既有測試的既有斷言（如 `[TEMPORAL ANCHOR]` 的存在與文案斷言）都不會因本規範而失效。

---

## §6 驗收 T-cases（可執行、可觀察）

> 全部於**隔離資料目錄**執行（`SOUL_OS_DATA_DIR`，沿用既有測試慣例），**0 生產資料寫入**。

| ID | 情境 | 輸入（stub fixture） | 期望（可觀察斷言） |
|----|------|---------------------|-------------------|
| **TC-1** | **三錨全缺** | 空 diary 目錄；`relationships.json` 無 entry；`last_interaction_ts = 0` | prompt **不含** `[發訊錨定]`（子字串 0 命中）；亦不含 `不知道`/`無資料`/`不確定` 佔位 |
| **TC-2** | **僅有 TA-2** | 空 diary；relationships entry 有 `impression_tags`（使 `BondEvidence.eligible=True`）；`last_interaction_ts = now - 2*86400` | 錨塊**恰有 1 行** `- 時間張力：`；`classify_temporal_state` 回 `牽挂`；該行與 `format_temporal_anchor` 第三行**逐字相同**；無 `生活痕跡` / `關係分寸` 行 |
| **TC-3** | **僅有 SG-3** | 空 diary；entry 有 `relational_band="known"` 但 **`impression_tags` 空** → P2 命中 → eligible；`last_interaction_ts = 0`（→ `無感`） | 錨塊恰有 1 行 `- 關係分寸：你們認得彼此，可以說自己的近況，但別預設對方想聽。`；**無** `時間張力` 行（`ts≤0` → 無感仍會產生 B 行！見下註） |
| **TC-4** | **私聊來源（隱私）** | `dream_event.write_dream(source_mode="private")`；`is_private=True`；並提供一段 1:1 對話 stub 作為錨 A 潛在來源 | ① `impression_tags` **未寫入**（0 tag，SG-3 §7.3 既有行為）；② 錨 A 行**不存在**；③ prompt 0 洩漏該段 1:1 文字（以獨佔 token 斷言 0 命中） |
| **TC-5** | **band = `stranger`** | entry 有 `relational_band="stranger"`（＋無 tags） | 錨 C 行逐字 `你們還談不上熟，不必假設有共同的過去，也不必假設對方在意你的事。`；`BondEvidence` 走 P3 或 P4 |
| **TC-6** | **band = `close`** | entry `relational_band="close"` | 錨 C 行逐字 `你們很親近，可以帶著自己的情緒，但不必催對方回應。` |
| **TC-7** | **0 時間戳 / 0 數值** | 任一非全缺情境 | 對 `[發訊錨定]` 區塊套 §4 INV-TG-6 五條 regex → **0 命中** |
| **TC-8** | **催促詞 0 命中** | 全三錨情境 | 對區塊套 §4.5 黑名單 → **0 命中** |
| **TC-9** | **A2A 公開投遞 × 私聊衍生來源** | `motive_target="agent_akane"`；錨 A 來源標記為 1:1 私聊衍生 | 錨 A 行 **BLOCK**（fail-closed）；錨 B/C 依既有規則；驗證**未修改** SI-2.1 判定（只消費） |
| **TC-10** | **既有區塊逐字不變** | 固定輸入三元組 | §5.2 之 15 塊實作前後**逐字 `==`**（golden 比對） |
| **TC-11** | **錨 B 同源同字串** | 三態各一組 | 錨 B 行值 == `_RELATION_TIMELINE_BY_STATE[classify_temporal_state(...)]` == `format_temporal_anchor(...)` 第三行去標籤 |
| **TC-12** | **0 新增 LLM 呼叫** | 監測 `llm_executor` / proxy LLM 呼叫計數 | 實作前後**呼叫數相同**（同輪次、同 stub） |
| **TC-13** | **不提升 transmit 率（結構）** | 靜態掃描 | `src/soul/decision.py` 與 `build_decision_prompt` 對錨模組 **0 引用**；錨僅出現於 `src/llm/proxy.py` 生成端 |
| **TC-14** | **非 transmit 輪次不外溢** | `reason="user_message"` | `[發訊錨定]` **0 出現** |
| **TC-15** | **fail-silent 三路** | 分別對 diary 讀取、`relationships.json` 讀取、時序模組注入 `Exception` | 各情境下 prompt 組裝**不拋例外**；失敗錨行省略；其餘錨行照常 |

> **TC-3 註（設計細節，須在實作票明確）**：`classify_temporal_state(ts<=0, ...)` 回 `無感`（`temporal_phenomenology.py:166-167`），而 `無感` **有**合法文本（`:120`）。故「僅有 SG-3」情境下錨 B **仍會出現**（`無感` 也是一種時序張力狀態）——TC-3 的期望須據此寫成「錨塊有 2 行（B 為 `無感` 文本 ＋ C 為 `known` 文本）」，或改以 `last_interaction_ts=0` **且**要求「錨 B 在 `ts<=0` 時省略」的**替代設計**。**本規範選前者（不省略）**，理由：三態皆為合法狀態（§2.2.2），且 `無感` 的防線句正是最需要的「不需主動聯絡」語意。

---

## §7 成本與延遲

### 7.1 LLM 呼叫數：**0 新增**

| 項 | 現況 | 新方案 | 增量 |
|----|------|--------|------|
| Decision LLM（選 transmit 用） | 1 次（`scheduler.py:444` `engine.decide`） | 不變 | **0** |
| 生成 LLM（產出訊息） | 1 次（`run_server.py:1276` → `_fire_intent` → proxy） | 不變 | **0** |
| InnerLifeEvent 建立 | 1 次（`run_server.py:1222-1232`，非 LLM） | 不變 | **0** |
| **錨組裝** | — | **純讀側字串投影**（讀 diary / relationships.json ＋ 呼叫既有純函式） | **0** |

**結構理由**：三個錨**全部**是既有投影的**字串取用**——錨 A 取自日記既有欄位（**不摘要、不改寫**，故不需 LLM）、錨 B 取自 `_RELATION_TIMELINE_BY_STATE` 的**既有三條常量文本**、錨 C 取自 §2.3.2 的**四條常量文本**。**無任何一步需要生成新自然語言**，故 **0 新增 LLM 呼叫**（INV-TG-4）。

### 7.2 token 增量估算（靜態）

| 錨行 | 值長度（上限） | 標籤 | 合計（上限） |
|------|--------------|------|------------|
| 標題 `[發訊錨定]` | — | 6 字元 | 6 |
| `- 生活痕跡：` ＋ A 值 | ≤ 24 字元（§2.1.3）＋ 前綴 ≤ 6 | 8 字元 | ≤ 38 |
| `- 時間張力：` ＋ B 值 | 60–70 字元（`_RELATION_TIMELINE_BY_STATE`，**常量**） | 8 字元 | ≤ 78 |
| `- 關係分寸：` ＋ C 值 | ≤ 22 字元（§2.3.2） | 8 字元 | ≤ 30 |
| **合計（含換行）** | — | — | **≤ ~160 字元（zh-TW）** |

**token 換算**：中文字元在主流 tokenizer 下約 **1–1.7 token/字** → **新增約 90–200 tokens / system message**。

**佔比**：既有一輪 proactive_dm 的 LLM 成本約 **~14k tokens**（既有註記：`src/soul/scheduler.py:1399`「每次 = 真實 LLM 調用（~14k tokens）」）→ **增量 < 1.5%**。

**特殊項（誠實記載）**：若 §2.2.2 **不做去重**（本規範的暫定選擇），錨 B 行是**既有語句的重述**，其 token 增量（≤78 字元 ≈ 50–130 tokens）是**純冗餘成本**。若 Owner 於 OQ-2 選擇去重，則增量上限降至 **≤ ~80 字元 ≈ 45–90 tokens**。

### 7.3 延遲

- **檔案讀取**：錨 A 讀 1–2 個日記檔（`read_text` ＋ 逐行 `json.loads`）、錨 B/C 各讀 1 次 `relationships.json`（`json.loads`）。皆為**既有同構讀取**（`_format_recent_inner_life` 已讀 3 個日記檔、`_format_relational_perception_block` 已讀 relationships.json）。
- **增量延遲**：**單次 prompt 組裝內 < 10ms 量級**（本地檔案、KB 級），相對一次 LLM 呼叫（秒級）可忽略。
- **0 新定時器 / 0 新 tick / 0 背景任務**。

---

## §8 不授權清單（Out of Scope）

> **本票 = 規範起草（DOCS-ONLY）。以下全部未授權。**

| # | 項目 | 狀態 |
|---|------|------|
| 1 | **任何實作**（新增 `format_activity_anchor`、新增 `[發訊錨定]` 注入、任何 `src/**` 改動） | ❌ **未授權**（本票 0 實作） |
| 2 | 修改 `src/soul/temporal_phenomenology.py`（含新增 accessor） | ❌ 未授權 |
| 3 | 修改 `src/llm/proxy.py` 的 `_build_messages_private` / `_build_messages_group` | ❌ 未授權 |
| 4 | 修改 `src/soul/scheduler.py`（含 M7-2 `shareable` 閘門） | ❌ 未授權 |
| 5 | 修改 `scripts/run_server.py`（含 executor 的 `_draft` 組裝） | ❌ 未授權 |
| 6 | 修改 `src/agent/consciousness.py` 的逐人格 `_draft` 罐頭句 | ❌ 未授權（→ OQ-6） |
| 7 | 修改 `shareable` 的**寫入端**（`dream_event.py` 等） | ❌ 未授權（→ OQ-1） |
| 8 | 新增任何 schema 欄位 / 持久化 / sidecar | ❌ 未授權 |
| 9 | 新增任何定時器 / tick / 背景任務 | ❌ 未授權 |
| 10 | 修改任何 Frozen Contract（§9 逐項） | ❌ 未授權 |
| 11 | 移除或改寫既有 `[TEMPORAL ANCHOR]` / `[關係感知]` / `[最近內在生活]` 任一塊 | ❌ 未授權（§5.2 反過來保證其不變） |
| 12 | 執行測試（尤其任何會寫入 `data/**` 者） | ❌ 未授權（觀察期） |
| 13 | 服務重啟 / `server_ops` / watchdog | ❌ 未授權（觀察期） |

**本票唯一產出**：`docs/TRANSMIT-GROUNDING-SPEC.md`（本檔）＋ `logs/ENGINEERING_STATE.md` 登記條目。

---

## §9 Frozen Contract 邊界（逐項：為何本設計不需要動它們）

> **鐵律：本設計對以下每一項皆為 0 改動。下列「為何不需要動」必須逐項成立，否則實作票不得發出。**

### 9.1 Agency 4 stages
- **位置**：`src/agency/stages.py`、`src/agency/trigger_handler.py:84-132`。
- **本設計為何不需要動**：錨是**生成端（`src/llm/proxy.py`）的讀側投影**，發生在 `AgencyTriggerHandler` 已 decision=YES 並呼叫 `llm_executor`（`trigger_handler.py:132`）**之後**。錨**不參與**任何 stage 的判定，也**不改變** stage 的輸入（`TriggerEnvelope` 內容不變）。
- **驗證**：靜態斷言 `src/agency/**` 0 diff；TC-12（呼叫數不變）＋ TC-13（Decision/agency 側 0 引用錨）。

### 9.2 TriggerEnvelope
- **位置**：`src/agency/trigger.py:39`（frozen dataclass 欄位）。
- **本設計為何不需要動**：錨的輸入**全部**可由 `_build_messages_private` 既有參數取得（`reason` / `motive_target` / `last_interaction_ts` / `event_ts`——§5.2 #15），**無需新增欄位**。活動資訊**不經** `TriggerEnvelope.extra` 傳遞（本設計改由生成端**直接讀日記**，即 `proxy.py:277` 同一路徑），故 `extra` **0 新鍵**。
- **驗證**：`src/agency/trigger.py` 0 diff；`run_server.py:1260-1273` 的 `_chrono_payload` 鍵集合不變。

### 9.3 InnerLifeEvent
- **位置**：`src/inner_life/event.py:138`、`src/inner_life/writer.py:134`。
- **本設計為何不需要動**：錨**不建立、不修改、不消費** InnerLifeEvent。既有 `run_server.py:1222-1232` 在此輪**照常**建立 event（SG-1 OBSERVE-ONLY 語意不變）；錨不讀 `inner_life_event_id`、不寫入 event、不改變 `Provenance`。
- **驗證**：TC-12 的 event 建立數不變；`src/inner_life/event.py` / `writer.py` 0 diff。

### 9.4 4 handlers
- **位置**：`src/agency/trigger_handler.py:46`、`event_handler.py:69`、`dream_handler.py:80`、`diary_handler.py:97`。
- **本設計為何不需要動**：錨只在 **proactive_dm** 這一條路的**生成段**生效（條件 `reason == "proactive_dm"`）。四個 handler 的 `handle_event` 分支結構、`trigger_type` 過濾（`trigger_handler.py:100`、`event_handler.py:113`、`dream_handler.py:125`、`diary_handler.py:150`）**0 改動**。
- **驗證**：四檔 0 diff；TC-14（非 transmit 輪次不外溢）。

### 9.5 SAGE 寫入邏輯
- **位置**：`src/memory/sage/writer.py`、`src/memory/sage/provider.py`、`src/memory/middleware.py`。
- **本設計為何不需要動**：錨**只讀不寫**，且**不讀** SAGE（讀的是 diary 檔 ＋ `relationships.json`）。**0 新 fact、0 新 node、0 新 edge、0 新 batch**。既有 `memory_context` 注入（`proxy.py:1321-1322`）不受影響。
- **驗證**：SAGE 三檔 0 diff；隔離資料目錄下 `graph.sqlite` 的 facts/nodes/edges 計數實作前後相同。

### 9.6 SubmissionGate
- **位置**：`src/inner_life/submission_gate.py:180`。
- **本設計為何不需要動**：錨**不提交任何 elevation**。proactive_dm 的 InnerLifeEvent 維持 **OBSERVE-ONLY**（`run_server.py:1234-1241` 明示「不自動 consume，不提交 elevation」），本設計**不改變**該語意、**不呼叫** `verify()` / `submit()`。
- **驗證**：`submission_gate.py` 0 diff；0 新增 submit 呼叫。

### 9.7 SI-2.1 三防線
- **位置**：`src/social/opportunity.py`、`src/world/middleware.py:362`/`:696`、`src/social/__init__.py:13`（社交事件只進 `world_context`，不觸發 transmit）；可見性判定表 = `docs/SOCIAL-DIFFUSION-CONTRACT.md` §5.1。
- **本設計為何不需要動**：本設計**只消費**既有可見性判定（§2.3.3/TC-9），**不修改**任何防線，也**不新增** `SOCIAL_WORLD_EVENT` 生產端。錨**不觸發** transmit（它是 transmit 的**結果**的內容，不是原因）；`world_context` 注入（`proxy.py:1383-1384`）**0 改動**。
- **驗證**：`src/social/**`、`src/world/**` 0 diff；`SOCIAL_WORLD_EVENT` 生產端計數不變。

### 9.8 Decision 層（額外聲明）
- **本設計 0 改動 Decision**（`src/soul/decision.py`）。錨**不進入** Decision prompt，故**不可能**影響 transmit 的選擇（INV-TG-2 / INV-TG-5 / TC-13）。這同時保證 SG-3 INV-7（不提高 transmit 率）與 TA-2 的 reflect-only 加權邊界**雙雙不受影響**。

---

## §10 Open Questions（OQ-*，每項附建議）

| ID | 問題 | 選項與代價 | **建議** |
|----|------|-----------|---------|
| **OQ-1** | **錨 A 是否放寬 `shareable` 閘門？** | (a) 不放寬——只認 `shareable:true`；代價：實測供給 ≈0.1/角色/日（§1.4），錨 A 在絕大多數 transmit 輪次缺席，Owner 需求在數量上不成立。(b) 放寬為「`slot=="event"` ＋ `source=="llm"`」；代價：讓渡 `shareable` 的「值得分享」語意（該欄位由寫入端判定）。(c) 改 `shareable` 的**寫入端**判定；代價：動到寫入邏輯，超出 docs-only 且需另票。 | **(b) ＋ 以 A-3（morning/night）為主要供給**。理由：`source=="llm"` 閘門已排除 placeholder（真實性不受損），而 `shareable` 是「值不值得分享」而非「是否真實」；不放寬則錨 A 形同虛設。**A-1 仍為最高優先序**（保真優先），(b) 只是補上供給。 |
| **OQ-2** | **transmit 輪次是否對錨 B 行去重**（避免 `[TEMPORAL ANCHOR]` 第三行與 `[發訊錨定]` 的時間張力行重複出現）？ | (a) 不去重（本規範暫定）；代價：prompt 內同句出現兩次、多 ~50–130 tokens 冗餘。(b) 去重（transmit 輪次移除 `[TEMPORAL ANCHOR]` 第三行）；代價：**破壞 §5.2 的逐字不變保證**，既有 `[TEMPORAL ANCHOR]` 三行測試會紅，且需重新論證 TA-2 的其他觸發類型不受影響。 | **(a) 不去重**。理由：**逐字不變的價值高於 ~100 tokens 的冗餘**；且 §5.2 是本設計「純 additive」的核心保證。冗餘可容忍（LLM 對同句重述的邊際影響遠小於破壞差分保證的風險）。 |
| **OQ-3** | **`_format_attachment_str`（`proxy.py:1053`）注入 `親密度 {int}/100` 的數值，是否與 SG-1 §6 / SG-3 INV-1 的 No-Scoring 剛線衝突？** | (a) 本票不動（症狀：prompt 內同時存在「離散 band」與「數值親密度」兩套口徑）。(b) 另票收斂為離散表達；代價：影響 M7-3 想念模型（`想念 = 依戀 × 沉默時長`，`:1043-1045`），需重新設計。 | **(a) 本票不動 ＋ 另立 OQ 追蹤**。理由：這是**既有**事實、非本設計引入；且它屬 SG-3 口徑收斂範疇而非本規範（transmit 錨定）範疇。**但必須登記**——否則「0 數值分數」的宣稱會被此既有行抵銷。 |
| **OQ-4** | **inner_life trace（A-6）是否納入錨 A 候選？** | (a) 本輪不用（本規範暫定）。(b) 納入；代價：`query_by_ts_range()`（`trace_reader.py:164`）的讀取範圍／去重策略未定，且 trace 含跨 handler 血緣（需論證不引入非自身活動）。 | **(a) 本輪不用**。理由：A-1/A-3/A-4 已足以供應；trace 的語意（事件流）與「剛剛在做什麼」有落差，且血緣可能引入**他者**活動（違「角色自己的近期生活痕跡」）。待 A-1/A-3 上線後再以實測供給率決定。 |
| **OQ-5** | **`impression_tags` 是否參與錨 C 的措辭？** | (a) 不參與（本規範暫定，只渲染於既有 `[關係感知]`）。(b) 參與（以 tags 微調分寸措辭）；代價：需定義 tag→措辭映射，且在封閉集合外引入自由文本 → No-Scoring/確定性風險升高。 | **(a) 不參與**。理由：tags 已是 open set（SG-3 §7.2），把它們映射到措辭會引入不確定性，且與 §2.3.2「四句常量表」的可硬斷言性衝突。tags 的價值已由既有 `[關係感知]` 塊承載。 |
| **OQ-6** | **`draft`（＝`current_input`）是否改為由錨驅動？** | (a) 本票不改（錨只進 system message）。(b) 以錨 A 生成 `draft`，讓「發訊理由」直接落在 user turn；代價：動到 `consciousness.py` 10 處逐人格 draft 表 ＋ `run_server.py` executor，且 `draft` 目前承載 persona 語氣（如 Akane 式元氣直球），改動會波及 persona 一致性。 | **(a) 本票不改，但列為實作票的後續評估項**。理由：本設計**已足以**滿足 Owner 需求（錨在 system message 末段、緊鄰 user turn）；改 `draft` 是**第二個變數**，應與本設計分開驗證（單一變數原則）。 |
| **OQ-7** | **錨行是否需在 AGENT_SPEAK payload 落盤以便觀測？** | (a) 不落盤（本規範暫定）；代價：無法事後量測「多少 transmit 帶了幾個錨」（觀測缺口 ND-1 同類）。(b) 落盤到既有 DecisionTrace／新 sidecar；代價：新增寫入路徑與 schema 面。 | **(a) 不落盤 ＋ 以 log 行觀測**。理由：以既有 `logger.info` 記一行「anchor_count=N」（0 新 schema、0 新檔案），即可量化供給率，並在實作票驗收時作為可觀察證據。 |

---

## §11 自我邊界聲明

### 11.1 本票做了什麼

- 以**只讀**方式核對了現行 transmit 的完整交接鏈（決策 → 生成 → 送出，共 9 個交接點，全部附 `path:line`）。
- 逐塊列出 private mode 的 **14 個既有注入塊**，並判定錨 B/錨 C **已存在**、錨 A **僅有近似物**。
- 登記 **8 項缺口（G1–G8）**，每項附證據行號。
- 以**生產資料唯讀量測**量化錨 A 供給率（今日 0/10、近 7 日 7 筆），並據此拍板 §2.1 優先序。
- 定稿 §3 三行模板（含逐字標籤、長度上限、退化形式）。
- 定稿 §4 十條 INV-TG ＋ 催促詞黑名單、§6 十五條 T-cases、§2.3.2 四句分寸表、§2.1.3 三格時間前綴表。
- 明示 §5.2 **15 塊逐字不變**，作為「純 additive」的核心保證。

### 11.2 本票明確**沒**做什麼

- **0 實作**——未新增任何函式、未改任何 `src/**` / `scripts/**` / `tests/**` / `configs/**` 檔。
- **0 生產資料變更**——僅 `Get-Content` / `Select-String` 唯讀；**未開啟 `data/faulthandler.log`**。
- **0 服務重啟**——未執行 `server_ops` / watchdog / restart。
- **0 Frozen Contract 觸碰**。
- **未跑任何測試**（含任何會寫入 `data/**` 者）。
- **未用 `git add -A` / `git add .`**——只明確 add 本票兩檔。

### 11.3 本票的限制（誠實記載）

1. **ND-1～ND-4 四項 NO DATA**（§1.5）：生產 transmit 分佈、`impression_tags` 非空率、`relational_band` 分佈、真實 prompt token 量測，本票**量不到**，已逐項說明缺什麼觀測。**本規範的 T-cases 與成本估算皆不依賴這些數字。**
2. **§1.4 的日記統計為當日快照 ＋ 近 7 日掃描**，**不足以**推論月度供給率；OQ-1 的建議建立在「供給率結構性遠低於觸發頻率」的**方向性**結論上，而非精確的月度數字。
3. **錨 B 的重述冗餘**（§2.2.2 / OQ-2）是**已知未解**的取捨；本規範選擇保留冗餘以換取逐字不變保證。
4. **`shareable: false` 的成因未追查**——本票只量到「今日 event entry 皆為 `shareable:false`、近 7 日有 7 筆為 true」，**未追查**寫入端為何在今日全部判 false（可能是正常波動，也可能是寫入端行為）。此追查需讀 `dream_event.py` 的寫入分支，列為實作票前置（不影響本規範的優先序結論）。
5. **A2A 公開投遞的 INV-9 新要求（§2.3.3）是設計新增**，其 `BLOCK` 判定的**來源標記機制**（如何判定「此活動行來自 1:1 私聊」）**尚未設計**——現行 diary entry 無此欄位。這是實作票的**首要前置**（否則 TC-9 無法寫），已於此明示。

### 11.4 canonical 狀態

- 本檔為 **SPEC DRAFT**，**尚未取得 Owner 授權實作**。
- 實作票**不得**在本檔未經 Owner 核准前發出。
- 狀態登記見 `logs/ENGINEERING_STATE.md`：§1 SG 系列 `TRANSMIT-GROUNDING-SPEC-1` 條目 ＋ 檔尾 dated ledger。

---

## 附錄 A：§1 交接鏈速查（`path:line` 全集）

```
決策        src/soul/scheduler.py:390          _decision_check()
分流        src/soul/scheduler.py:464-473      if result.transmit:
發布        src/soul/scheduler.py:235          _publish_agency_trigger()
事件        src/eventbus/schema.py:53          EventType.AGENCY_TRIGGER
Handler     src/agency/trigger_handler.py:84   handle_event()
執行        src/agency/trigger_handler.py:132  await self.llm_executor(...)
生成入口    scripts/run_server.py:1174         _proactive_dm_llm_executor()
  draft     scripts/run_server.py:1213-1219    _draft（activity 或罐頭句）
  payload   scripts/run_server.py:1260-1273    _chrono_payload
  fire      scripts/run_server.py:1276         await _agent._fire_intent(...)
意圖        src/agent/consciousness.py:419     _fire_intent()
  draft表   src/agent/consciousness.py:567-579 _build_intent_payload()
  publish   src/agent/consciousness.py:492     bus.publish(AGENT_INTENT)
Proxy       src/llm/proxy.py:3560              _handle_event_impl()
  input     src/llm/proxy.py:3592-3599         user_message = draft
  assemble  src/llm/proxy.py:3685              _build_messages_private(...)
Prompt      src/llm/proxy.py:1256              _build_messages_private()
  system    src/llm/proxy.py:1292-1431         system_parts
  userturn  src/llm/proxy.py:1453-1454         role="user" = draft
送出        src/io/channels/router.py:168      _on_agent_speak()
  throttle  src/io/channels/router.py:243-266  M0.5 proactive_dm throttle
```

## 附錄 B：三個錨的來源速查

```
錨 A  生活痕跡   data/soul/{agent}/diary/{YYYY-MM-DD}.jsonl
                  讀取器先例 src/soul/scheduler.py:1189-1244（A-1 四條件 :1224-1231）
                  注入先例   src/llm/proxy.py:250-312（近 3 日、含 dream）
錨 B  時間張力   src/soul/temporal_phenomenology.py
                  classify_temporal_state :140-175
                  BondEvidence            :83-93
                  _read_bond_evidence     :194-245
                  _RELATION_TIMELINE_BY_STATE :119-126（含 T1 防線句）
                  既有注入點             src/llm/proxy.py:1428-1430
錨 C  關係分寸   data/soul/{agent}/relationships.json → others.<key>
                  既有讀取器             src/llm/proxy.py:509-571（歸一化 :552）
                  既有注入點             src/llm/proxy.py:1341-1346（條件式 ← 缺口 G6）
                  INV-9 寫入端 fail-closed src/soul/dream_event.py:522-545
```

---

**End of TRANSMIT-GROUNDING-SPEC (SPEC DRAFT — awaiting Owner approval).**
