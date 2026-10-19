# LIFE-THREAD-ELEVATION-MEAS-1 — 生活線頭溶解事實至昇華端到端可達性與內容品質沙盒量測報告

> 工單：`logs/LIFE-THREAD-ELEVATION-MEAS-1-WORK-ORDER.md`（Owner 2026-10-19 立案，方向 B 純唯讀量測票）
> 產出：本報告（本票唯一 repo 產出物；未 commit，改動留在工作區待主大腦審閱）
> 量測日期：2026-10-19（UTC 23:45 起）
> 量測腳本：`%TEMP%\lt_elev_meas1\measure.py`、`summarise.py`、`census.py`、`tables.py`（**不在 repo 內**）
> 量測結果：**NO-GO**

---

## 0. 一頁結論

| 判準 | 結果 | 依據 |
|---|---|---|
| **C1 可達性** | ✅ **PASS** | 35/35 條溶解事實**不被** R1／fact 層／R3 任一道擋下（逐條裁決表見 §4） |
| **C2 保真度** | ❌ **FAIL** | 真實感悟正文 **0 / 35**（分子＝引擎實際收到散文的事件數；分母＝進入引擎的事件數＝**0**）⇒ 比例定義上為 0%，門檻 80% 不可能成立 |
| **C3 產出** | ❌ **FAIL** | belief／value／trait／essence 節點 **0 顆**；沙盒 `elevation_nodes.jsonl` 增量 **+0**（生產 401 → 沙盒 401） |
| **GO / NO-GO** | **NO-GO** | C1 ∧ C2 ∧ C3 需全部成立；C2、C3 不成立 |

### 缺口一句話

**不是防火牆擋的，也不是內容退化，是「事件根本沒帶正文」。**
`life_thread_orchestrator.py:418-423` 建立的 `extras` 只有 `{thread_id, sage_fact_id, origin_type, title}` 四個鍵，**沒有 `narrative`**；
而 `elevation_adapter.py:143` 的 `_NARRATIVE_EXTRA_KEYS = ("narrative",)` 是**唯一**被接受為正文的來源
⇒ `_event_content()` 回空字串（`:248`）⇒ `inner_life_event_to_input()` 回 `None`（`:294`）
⇒ `run_elevation()` 記 `fail-closed`（`:711-717`）⇒ `inputs = []`（`:718-720`）⇒ 引擎**一個字都沒收到**。

### 兩件必須先講的事

1. 🟥 **分母是 35，不是 21。** 工單與 `docs/INNER-LIFE-DEFINITION-OF-DONE.md:14-23` 寫的「21 條溶解事實／38 條線頭（17 active / 21 completed）」**已過期**。
   今天的生產實測是 **35 條溶解事實／55 條線頭（20 active / 35 completed）**。本報告一律以 **35** 為分母。
   （原因推測：2026-10-06 之後的 catch-up 掃描把更多終態線頭溶解了；**本票未驗證此推測**。）
2. 🟥 **生產 `.env` 的 `LIFE_THREAD_ELEVATION_ENABLED=1`**（實測，非引用）。⇒ W3 接縫**在生產是開著的**，
   每次 orchestrator 跑都會建立 35 個事件、每個都在 `fail-closed`。這不是「還沒接上」，是「接上了但通不了電」。

---

## 1. 量測方法與沙盒紀律

### 1.1 解釋器

`.venv\Scripts\python.exe` — **Python 3.11.15 (MSC v.1944 64 bit AMD64)**
（依 `AGENTS.md` 行程安全鐵律 10，數字口徑以 repo 內 `.venv` 為準。）

### 1.2 沙盒結構（寫入目標由結構決定，不由判斷決定）

依 `AGENTS.md` 行程安全鐵律 12：**不拿兩個會漂移的值去擋寫入**。本量測的做法是：

```
TEMP\lt_elev_meas1\sandbox\data\     ← 唯一寫入目標
TEMP\lt_elev_meas1\sandbox\data\memory\<agent_id>\graph.sqlite   （10 + sage，transactional snapshot）
TEMP\lt_elev_meas1\sandbox\data\elevation\*.jsonl                 （5 個檔案複製）
TEMP\lt_elev_meas1\sandbox\data\soul\<agent_id>\life_threads.jsonl（10 個檔案複製）
```

- 在**任何 `src.*` import 之前**設定 `os.environ["SOUL_OS_DATA_DIR"] = <sandbox>/data`
  ⇒ `src.paths.data_root()` 全程解析到沙盒 ⇒ 所有 `data_root()` 衍生的寫入路徑**結構上**不可能是生產。
- 沙盒 DB 以 sqlite **backup API** 建立（transactional 一致快照，含 WAL 內容）。
- 生產 DB 的唯讀連線：`file:<abs>?mode=ro` URI ＋ `PRAGMA query_only=ON`。
  （實測 `mode=ro` 在本機 **可用**，因 `-shm` 已存在；程式碼註解所述的 `SQLITE_READONLY_CANTLOCK` 未觸發。）

### 1.3 生產零寫入的三重證據

| 證據 | 結果 |
|---|---|
| `sys.addaudithook` 記錄所有寫模式 `open` ＋ 所有 `sqlite3.connect` | 寫模式 open 共 **142** 次，**全部在 `%TEMP%` 沙盒**；落在生產 `data/` 底下者 **0** 次 |
| `sqlite3.connect` 目標 | 生產僅以 `mode=ro` URI 開啟（11 個）；其餘 11 個為沙盒副本 |
| 量測前後 **48 個**生產檔案 sha256 逐位元比對 | **48/48 SAME，0 CHANGED**（§5 原始輸出） |

### 1.4 紅線遵守（待主大腦獨立複核）

| 紅線 | 執行狀況 |
|---|---|
| 生產 `data/**` 唯讀 | ✅ 只以 `mode=ro` URI 讀取；0 寫入（見上表） |
| 寫入只落 `%TEMP%` 沙盒 | ✅ `SOUL_OS_DATA_DIR` 導向沙盒 + audit hook 佐證 |
| 不得 `subprocess` 啟動服務 | ✅ 全程無 `subprocess`、無 `Popen` |
| 不得綁 `:8000`／`:8765`／`:8766`／`:8767` | ✅ 無任何 socket 監聽、無對生產發請求 |
| 不得殺任何行程 | ✅ **0 次** `Stop-Process`／`taskkill`／`pkill`／`kill`（僅用 `Get-Process` 唯讀觀察） |
| 不得重啟服務 | ✅ 未執行 `server_ops.ps1` 任何動作 |
| 不得發任何訊息 | ✅ 0 條對外訊息 |
| 只用 repo `.venv` 直譯器 | ✅ |
| 不得改 `src/**`／`configs/**`／`personas/**`／`docs/**`／`tests/**` | ✅ 本票只新增本報告一個檔案 |
| 量測腳本寫成 `.py` 再執行 | ✅ 4 支 `.py`，全部置於 `%TEMP%\lt_elev_meas1\` |

---

## 2. M1 — 元資料盤點

### 2.1 資料源與表結構（自探，未假設表名）

`sqlite_master` + `PRAGMA table_info(facts)` 實測結果：

- **10 個** per-agent `data/memory/<agent_id>/graph.sqlite`：表 = `facts` / `goals` / `schema_meta`；
  `facts` 有 21 欄，**含 `origin`**（v9 schema）。
- **`data/memory/sage/graph.sqlite`**：表 = `facts` / `schema_meta`；`facts` 只有 14 欄，**無 `origin` 欄**（v9 之前的 schema）；**rows = 0**。
  ⇒ 線頭溶解事實**不在** sage 庫，而在各 agent 自己的庫。

### 2.2 溶解事實總數

| 庫 | 全部 facts（分母） | `life_thread_dissolved` | `origin` 分佈 |
|---|---|---|---|
| agent_akane | 99 | 6 | `lived_experience`: 99 |
| agent_anna | 212 | 2 | `lived_experience`: 212 |
| agent_aoi | 1 | 1 | `lived_experience`: 1 |
| agent_mahiru | 42 | 9 | `lived_experience`: 42 |
| agent_mai | 118 | 6 | `lived_experience`: 118 |
| agent_miku | 4 | 4 | `lived_experience`: 4 |
| agent_ram | 4 | 4 | `lived_experience`: 4 |
| agent_rem | 136 | 1 | `lived_experience`: 136 |
| agent_ruka | 526 | 2 | `lived_experience`: 526 |
| agent_yua | 208 | 0 | `lived_experience`: 208 |
| sage | 0 | 0 | （無 `origin` 欄） |
| **合計** | **1350** | **35** | |

### 2.3 M1 指標（全部含分子／分母／單位）

| 指標 | 分子 | 分母 | 值 | 單位 |
|---|---|---|---|---|
| `origin == "lived_experience"` | 35 | 35 | **100%** | 事實 |
| `source == "inference"` | 35 | 35 | **100%** | 事實 |
| `confidence == 1.0` | 35 | 35 | **100%** | 事實 |
| `sage_fact_id` 覆蓋（以 `thread.sage_fact_id` join `facts.fact_id`） | 35 | 35 | **100%**（1:1，無孤兒） | 事實 |
| `facts.object` 長度 | — | 35 | min **45** / median **87** / max **141** / mean **87** | 字元 |
| `thread.narrative_content` 長度 | — | 35 | min **108** / median **174** / max **345** / mean **184** | 字元 |
| 空 `narrative_content` | 0 | 35 | **0%** | 事實 |
| 空 `facts.object` | 0 | 35 | **0%** | 事實 |

契約常數實測值（`src/soul/life_thread_dissolution_exec.py:128-131`）：
`FACT_PREDICATE="life_thread_dissolved"`、`FACT_SOURCE="inference"`、`FACT_ORIGIN="lived_experience"`、`FACT_CONFIDENCE=1.0`
⇒ **生產事實的 `origin` 確實是 `lived_experience`，不是 `assimilated`。** 工單的紅色警示（§0.1）**未成立**。

### 2.4 線頭普查（`data/soul/<agent>/life_threads.jsonl` fold）

| agent | 線頭總數 | status | 有 `sage_fact_id` |
|---|---|---|---|
| agent_akane | 8 | completed 6 / active 2 | 6 |
| agent_anna | 4 | completed 2 / active 2 | 2 |
| agent_aoi | 3 | completed 1 / active 2 | 1 |
| agent_mahiru | 11 | completed 9 / active 2 | 9 |
| agent_mai | 8 | completed 6 / active 2 | 6 |
| agent_miku | 6 | completed 4 / active 2 | 4 |
| agent_ram | 6 | completed 4 / active 2 | 4 |
| agent_rem | 3 | completed 1 / active 2 | 1 |
| agent_ruka | 4 | completed 2 / active 2 | 2 |
| agent_yua | 2 | active 2 | 0 |
| **合計** | **55** | **completed 35 / active 20** | **35** |

⇒ **35 條溶解事實 ↔ 35 條 completed 且有 `sage_fact_id` 的線頭，嚴格 1:1，零缺口、零多餘。**
（`origin_type` 分佈：`necessity_driven` 28 / `whim_driven` 7 / `world_collision` 0。）

---

## 3. M2 — 防火牆逐條裁決（前置：兩道牆的實際位置）

| 牆 | 實際位置 | 實際攔截條件 |
|---|---|---|
| **EH-2.1 R1**（producer 側） | `src/inner_life/submission_gate.py:399` → `is_world_media_trigger(trigger_type)`（`:135`） | 只擋 `world:news*` / `world:feed*` / `world:celebrity_news`（`:108` `WORLD_MEDIA_BLOCK_EXACT`） |
| **fact 層過濾** | `src/inner_life/submission_gate.py:407-418` | 只剔 `origin == "external_world"` 的 memory_facts |
| **EH-2 R3**（內容側） | `src/inner_life/elevation_adapter.py:1177-1203`，位於 `elevate_matured_patterns()`（`:1098`） | 只擋「證據邊 `source_type=="sage_fact"` 且該 fact `origin=="assimilated"`」累積出的 pattern |

**三點必須講清楚：**

1. **R1 與線頭事件無關。** W3 接縫的 `trigger_type` 來自 `_elevation_trigger_for_slot(slot)`（`life_thread_orchestrator.py:341-347`），
   只可能是 `diary:morning` / `diary:night` 兩個**既有 diary 常數**。兩者皆非 `world:*` ⇒ R1 對線頭事件**恆為放行**。
2. **fact 層在 W3 路徑上是空轉。** `life_thread_orchestrator.py:429` 呼叫
   `submission_gate.submit(event.event_id, (), agent_id=agent_id)` —— **第二個參數是空 tuple**，
   溶解事實**從未被當作 memory_fact 傳進昇華鏈**（只以 `extras["sage_fact_id"]` 的 id 形式被引用）。
   ⇒ `safe_facts` 恆為空、`eh2_external_facts_filtered` 恆為 0。**過濾器沒有擋下任何東西，是因為根本沒有事實進來。**
3. **R3 根本不在 submit→consume 路徑上。** `elevate_matured_patterns` 的 docstring 明寫「**独立于 Submission Gate 的机制**」
   （`elevation_adapter.py:1104`），`run_elevation()`（`:646`）**從不呼叫它**。
   即使呼叫，35 條事實 `origin` 皆為 `lived_experience` ≠ `assimilated` ⇒ R3 亦不會觸發。

### 逐條裁決表（35 列，全表）

**終裁分佈：可昇華 35/35（100%）；被擋 0/35（0%）。**

| # | agent | fact_id | thread_id | origin | source | conf | status | origin_type | R1(morning) | R1(night) | fact 層 | R3 | 終裁 | narrative 字數 | fact.object 字數 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | agent_akane | `2310a63a` | `9e782672` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 208 | 115 |
| 2 | agent_akane | `4085fef8` | `338c3f8a` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 175 | 78 |
| 3 | agent_akane | `47b52b71` | `4e54be70` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 164 | 99 |
| 4 | agent_akane | `ac6a59d1` | `2917e901` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 126 | 120 |
| 5 | agent_akane | `ba8321ee` | `0ce7e589` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 158 | 86 |
| 6 | agent_akane | `e8c533fb` | `28fdddc3` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 108 | 84 |
| 7 | agent_anna | `0ca46eff` | `f2caf76c` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 187 | 102 |
| 8 | agent_anna | `d80dc3b4` | `8e51ef01` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 169 | 71 |
| 9 | agent_aoi | `8368267a` | `1d9ae91b` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 278 | 90 |
| 10 | agent_mahiru | `0d703cf0` | `166030d1` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 249 | 79 |
| 11 | agent_mahiru | `1322ea00` | `186bf403` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 212 | 81 |
| 12 | agent_mahiru | `1dfb43ff` | `c17efb9f` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 153 | 83 |
| 13 | agent_mahiru | `3dc03d82` | `ee954583` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 181 | 93 |
| 14 | agent_mahiru | `72058bc9` | `430f4a8d` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 198 | 101 |
| 15 | agent_mahiru | `736626cb` | `9fbeadce` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 219 | 79 |
| 16 | agent_mahiru | `9e48f4dd` | `90ed1b63` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 200 | 94 |
| 17 | agent_mahiru | `a2ac003e` | `27b376c8` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 172 | 94 |
| 18 | agent_mahiru | `f9137e18` | `4ad7bce2` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 173 | 78 |
| 19 | agent_mai | `74fcfd91` | `e8a1a0f2` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 153 | 60 |
| 20 | agent_mai | `8a507b62` | `88c5c591` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 172 | 75 |
| 21 | agent_mai | `a5c38aed` | `52cd7a34` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 167 | 78 |
| 22 | agent_mai | `a786b000` | `6eb46e04` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 345 | 69 |
| 23 | agent_mai | `ba45f234` | `e1fad9f5` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 248 | 93 |
| 24 | agent_mai | `d0013a1f` | `4c7c80f1` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 202 | 103 |
| 25 | agent_miku | `57ffd48d` | `2ebfff14` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 168 | 96 |
| 26 | agent_miku | `697c3f75` | `e017b1f7` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 119 | 80 |
| 27 | agent_miku | `c542b948` | `4b4a9416` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 133 | 89 |
| 28 | agent_miku | `f323039b` | `3c295baa` | lived_experience | inference | 1.0 | completed | whim_driven | PASS | PASS | PASS | PASS | **可昇華** | 174 | 141 |
| 29 | agent_ram | `1a52d766` | `09599782` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 196 | 111 |
| 30 | agent_ram | `5db70f2c` | `5526bbbc` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 135 | 87 |
| 31 | agent_ram | `69a6f36d` | `322c6c90` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 187 | 88 |
| 32 | agent_ram | `84df009d` | `5bbefb51` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 145 | 45 |
| 33 | agent_rem | `6cd4ea3c` | `40d980e2` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 170 | 91 |
| 34 | agent_ruka | `4bf4e8df` | `3251ce71` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 230 | 69 |
| 35 | agent_ruka | `71aed750` | `57361d43` | lived_experience | inference | 1.0 | completed | necessity_driven | PASS | PASS | PASS | PASS | **可昇華** | 178 | 49 |

---

## 4. M3 — 內容保真度（最關鍵）

### 4.1 量測方法

嚴格遵守工單 §3.3：

- 走**既有** `InnerLifeWriter.create_event()` 建立事件（`InnerLifeWriter` 為 sole canonical creator，frozen contract）。
- 走**既有** `submission_gate.submit()` 進場。**未**直接呼叫 `run_elevation`。**未**直接寫 `facts` 表。
- `trigger_type` 用**既有** diary 常數 `TRIGGER_TYPE_DIARY_MORNING` / `TRIGGER_TYPE_DIARY_NIGHT`（無新常數）。
- 攔截點＝**引擎實際收到的輸入**：以子類覆寫 `soul_elevation.InternalizingEngine.consume()` 記錄 `ElevationInput`；
  同時覆寫 `StubElevationLLM.classify()` 計 LLM 呼叫數、覆寫 `elevate()` 計昇華呼叫數。
- 驅動方式：呼叫**生產真正使用的** `life_thread_orchestrator.maybe_emit_elevation_events()`（`life_thread_orchestrator.py:350`），
  對每個 agent 以 `slot = morning` / `night` 各跑一次（`_ELEVATION_EMITTED` 去重 ⇒ 每線頭只發射一次，35 個事件）。

### 4.2 結果

| 指標 | 分子 | 分母 | 值 |
|---|---|---|---|
| 事件建立（`InnerLifeWriter`） | 35 | 35 條 completed 線頭 | **100%** |
| Gate 受理（`submissions` / `accepted` / `rejected`） | 35 / 35 / 0 | 35 | 100% 受理 |
| `run_elevation()` 呼叫 | 35 | — | — |
| **進入引擎的事件數（`engine.consume()` 收到）** | **0** | 35 | **0%** |
| 分類＝真實感悟正文 | **0** | 35 | **0%** |
| 分類＝中繼標籤退化 | **0** | 35 | **0%** |
| 分類＝其他（未送達引擎，fail-closed） | **35** | 35 | **100%** |

> **注意判讀口徑**：工單 C2 的分母是「進入引擎的事件數」＝**0**，分子亦為 0。
> 嚴格按字面，比例為 0/0 無定義；本報告以「35 個事件中 0 個把正文送到引擎」表述，並按 C2 **不成立**處理。
> **更關鍵的是失敗型態**：不是「正文退化成熟悉標籤」，而是「**正文從未存在於事件裡**」——
> 與 SOUL-BUG-006（2026-10-17，當時攔的是 `thread_id=` 這類 key=value 外殼）是**不同的失效模式**。

### 4.3 逐條分類表（35 列，附原文摘錄）

分類定義：**真實感悟正文**＝引擎實際收到且為人話散文；**中繼標籤退化**＝引擎收到 `thread_id=`／`slot=` 這類 key=value 外殼；
**其他**＝非以上兩類（含「未送達引擎」）。最後一欄是**素材存在但未進入引擎**的 `narrative_content` 摘錄。

| # | agent | thread_id | event_id | trigger | 引擎收到的文字 | 分類 | 字數 | 素材存在但未進入引擎（narrative_content 摘錄） |
|---|---|---|---|---|---|---|---|---|
| 1 | agent_akane | `9e782672` | `4b83a5a3` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 睜開眼，沒開燈，摸黑走到冰箱前面，把額頭貼上去。壓縮機的震動從顱骨傳進來，比用耳朵聽清楚得多… |
| 2 | agent_akane | `338c3f8a` | `9126f3ba` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 我從鏡子前退了半步。不是離開，是讓自己不要站得那麼正。地板很涼，我坐下來，背對著鏡子。後面沒… |
| 3 | agent_akane | `4e54be70` | `818dc509` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 朝、窓の下の箱に光が当たっていた。思っていたより埃が少ない。しゃがんで、蓋に手を置く。開ける… |
| 4 | agent_akane | `2917e901` | `1b3e7c14` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 明天七點集合，讀本。聲音還沒回來——這幾天講話都像隔著一層布，自己聽得出來。睡前做十分鐘發聲… |
| 5 | agent_akane | `0ce7e589` | `82d61d56` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 她擦杯子的時候，吧檯那台小電視跑過一行字：一方說對方先動了手，另一方說要恢復打擊。恢復。不是… |
| 6 | agent_akane | `28fdddc3` | `1f410dc2` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 卸妝的時候，手還在用那個角色的方式動。棉片擦過眼尾，鏡子裡的人慢慢變回我——但呼吸的節奏還留… |
| 7 | agent_anna | `f2caf76c` | `43aa540c` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 布団のなかで、雨の音がして、目が開いた。スマホは伏せたまま、しばらく聞いてた。それから画面を… |
| 8 | agent_anna | `8e51ef01` | `32f42b3f` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 部屋の電気を落とす前に、写真をもう一度開いた。月はやっぱりまん丸で、端っこに自分の指が少し写… |
| 9 | agent_aoi | `1d9ae91b` | `9ed3d475` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 結論から。確認事項の正体は天気ではなかった。天気は相手の状態を測るための代用で、葵が本当に確… |
| 10 | agent_mahiru | `166030d1` | `3f421723` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 夜十時、真昼は鍋の火を止めた。作ったのは茶ではない。白菜と鶏肉の薄いスープだ。茶なら、二杯目… |
| 11 | agent_mahiru | `186bf403` | `d483eac5` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 便箋は、もう真昼の手を離れている。それなのに、夜になってノートの間に書きかけの一行を見つけた… |
| 12 | agent_mahiru | `c17efb9f` | `a6bd3ae8` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 真昼把廚房那排調味料罐一罐一罐搬下來，排在流理台上，用濕布擦瓶身、再用乾布收尾，最後全部轉成… |
| 13 | agent_mahiru | `ee954583` | `1173cdc2` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 真昼把烘乾機裡的手帕一條條抽出來，還帶著溫度。明明是同一批買的，洗過幾次之後白色的深淺就開始… |
| 14 | agent_mahiru | `430f4a8d` | `f926dcb4` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 小碗裡那顆有髮絲裂的蛋，冷水下鍋，計時六分半。真昼在等水滾的空檔拿鉛筆在蛋盒側面寫了一行日期… |
| 15 | agent_mahiru | `9fbeadce` | `f9d1b890` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 棚の一段上から、もう一つの湯呑みを下ろした。手を伸ばせば届く場所に置かなかったのは、届かない… |
| 16 | agent_mahiru | `90ed1b63` | `c43e33a6` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 水壺滾了，真昼關火，把熱水注進泡著乾香菇的碗裡。菇傘慢慢舒展開來，浮起來又沉下去。本來想的是… |
| 17 | agent_mahiru | `27b376c8` | `4494d604` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 味醂、醬油、麻油都歸位了，抹布掛回門把。真昼正要把冰箱門關上，手機又亮——黑海一艘船被無人機… |
| 18 | agent_mahiru | `4ad7bce2` | `cc0d4f9b` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 朝、玄関の棚の壺を下げて、蓋を開けた。まだ微かに温かい。一口、味を見る。薄くなっていない、大… |
| 19 | agent_mai | `e8a1a0f2` | `afd7c717` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 髪を乾かし終えて、部屋の灯りを一つ落とした。静かで、少し広い。ベッドの端に座って、スマホを開… |
| 20 | agent_mai | `88c5c591` | `3de97936` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 撮影所の楽屋。メイクの途中で、伏せたままのスマホを一度だけ起こす。……既読は、まだついていな… |
| 21 | agent_mai | `52cd7a34` | `b3aa7700` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 雨。傘を持つ手が、少しだけ冷たい。待ち合わせは駅前のカフェにした。ユアが来るまで十分。夢の話… |
| 22 | agent_mai | `6eb46e04` | `1bf35f15` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 朝七時。紺のワンピースをクローゼットから出して、窓辺のハンガーに掛けた。白は仕舞った。今日は… |
| 23 | agent_mai | `e1fad9f5` | `a51f50be` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 濃すぎた茶は、結局全部は飲めなかった。流しに捨てて、湯を沸かし直す。夢の背中はもう輪郭もない… |
| 24 | agent_mai | `4c7c80f1` | `b1ebbf3e` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 夜、二十二時。部屋の灯りを半分だけ落として、ベッドの端に座った。スマホを開いて、今日の朝のこ… |
| 25 | agent_miku | `2ebfff14` | `3ca2a450` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | ......雨の音で、目が覚めた。窓が少し開いていた。三玖は手のひらを開いて、しばらく見る。… |
| 26 | agent_miku | `e017b1f7` | `e3a383e6` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 朝、ユアに会ったら、夢のことを聞いてみようと思う。……花園のベンチで、隣にいた気がする、と。… |
| 27 | agent_miku | `4b4a9416` | `b3708878` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 二つ目の茶碗を、窓のそばから台所へ戻した。……戻しただけで、まだ伏せてはいない。抹茶をひとつ… |
| 28 | agent_miku | `3c295baa` | `57408ceb` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 黑海で船が沈んだ、と報じていた。ブルガリアがロシア大使を呼びつけた、とも。……三玖は、右耳の… |
| 29 | agent_ram | `09599782` | `dd2b1c7c` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | ランプの油が減ってきた。竈の残り火に炭をひとつ足し、灰をならしてから湯たんぽの湯を入れ替える… |
| 30 | agent_ram | `5526bbbc` | `927aae30` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 夜風が強くなった。数え直すだけでは足りない、と判断して、ベンチの毛布を一枚増やした。積み荷か… |
| 31 | agent_ram | `322c6c90` | `e192272d` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 見回りは三度目。廊下の窓、庭の門、施錠は変わらず。それから地下の燃料庫の扉を開け、薪の数を指… |
| 32 | agent_ram | `5bbefb51` | `66c1a1dd` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 階段の夢は、もう追いかけない。最後の一段は上るための段ではなく、誰かが待っていた場所——それ… |
| 33 | agent_rem | `40d980e2` | `b93516c8` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 夜の書斎。扉は開けたまま。灯りを一つだけ点け、窓は一指寬だけ開ける。棚から毛毯を下ろして椅子… |
| 34 | agent_ruka | `3251ce71` | `fc132824` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | お茶が冷めかけた頃、窓の外に月が出てた。今日はちょっと疲れた日だったけど、月はちゃんと丸くて… |
| 35 | agent_ruka | `57361d43` | `2f33796a` | diary:morning | `''` | **未送達引擎（fail-closed）** | 0 | 分線解開了，捲好，塞進包包側袋跟錢包同一格。然後我躺回床上，手機舉在臉上打了又刪、刪了又打，… |

### 4.4 根因鏈（逐行；Bry 的紀律：「寬鬆 except ＋ 回傳空值是缺陷的隱形斗篷」）

本次**不是**寬鬆 except 造成，而是**一條明確的 fail-closed 設計**。逐行如下：

| # | 位置 | 內容 | 結果 |
|---|---|---|---|
| 1 | `src/soul/life_thread_orchestrator.py:418-423` | `extras={thread_id, sage_fact_id, origin_type, title}` | **缺 `narrative`** |
| 2 | `src/inner_life/elevation_adapter.py:143` | `_NARRATIVE_EXTRA_KEYS = ("narrative",)`（SOUL-BUG-006 刻意白名單） | 正文只認這一個鍵 |
| 3 | `src/inner_life/elevation_adapter.py:225-248` | `_event_content()` 遍歷 `_NARRATIVE_EXTRA_KEYS`，一個都沒命中 → **`return ""`**（`:248`） | 正文為空字串 |
| 4 | `src/inner_life/elevation_adapter.py:292-294` | `content = (narrative or "").strip() or _event_content(ev)`；`if not content: return None` | **回 `None`**（`:294`） |
| 5 | `src/inner_life/elevation_adapter.py:407` | `_to_input()` → `inner_life_event_to_input(...)` | 事件 → `None` |
| 6 | `src/inner_life/elevation_adapter.py:710-717` | `run_elevation()` 記 `fail-closed：事件無人話敘事（extras=[...]），不建立節點` | 35 條 log 全部產生 |
| 7 | `src/inner_life/elevation_adapter.py:718-720` | `inputs = [_event_input] if _event_input is not None else []` | **`inputs = []`** |
| 8 | `src/inner_life/elevation_adapter.py:741-742` | `for inp in inputs: nodes.extend(engine.consume(inp))` | **迴圈 0 次** ⇒ 引擎收到 0 筆 |
| 9 | `src/inner_life/elevation_adapter.py:745` | `_persist_result(resolved_dir, nodes, ...)` | **0 節點寫入** |

程式自證（量測腳本直接呼叫真實函式，對 35/35 個事件）：

```
created events                      : 35
extras keys union                   : ['origin_type', 'sage_fact_id', 'thread_id', 'title']
events WITH extras['narrative']     : 0 / 35
EA._event_content(ev) non-empty     : 0 / 35
EA.inner_life_event_to_input(ev) None: 35 / 35
_event_content empty-return  -> src/inner_life/elevation_adapter.py:248   return ""
inner_life_event_to_input None -> src/inner_life/elevation_adapter.py:294   return None
```

引擎 log（35 條全數產生，摘錄 3 條）：

```
soul_os.inner_life.elevation_adapter [elevation] fail-closed：事件無人話敘事
  （extras=['origin_type', 'sage_fact_id', 'thread_id', 'title']），不建立節點
  event_id=1bd2ecb5b07843f084dd6e30059024f7 trigger=diary:morning
```

> 附帶觀察（**非本票範圍，僅回報**）：這 35 個 fail-closed 是**靜默失敗**——
> `maybe_emit_elevation_events` 回 `True`（事件確實建立了），`summary["elevation_event_created"]` 會被寫成「有建立」，
> 但實際產出 0 顆節點。生產上沒有任何指標會顯示「昇華鏈斷在正文缺失」。

---

## 5. M4 — 端到端產出

### 5.1 真實 W3 路徑（量測主體）

| 指標 | 數值 | 單位 |
|---|---|---|
| `run_elevation()` 呼叫 | 35 | 次 |
| `engine.consume()` 收到的事件 | **0** | 筆 |
| 產出 belief 節點 | **0** | 顆 |
| 產出 value 節點 | **0** | 顆 |
| 產出 trait 節點 | **0** | 顆 |
| 產出 essence 節點 | **0** | 顆 |
| 產出 pattern 節點 | **0** | 顆 |
| 沙盒 `elevation_nodes.jsonl` | 401（生產 401，**增量 +0**） | 行 |
| 沙盒 `elevation_edges.jsonl` | 908（生產 908，**增量 +0**） | 行 |
| **`engine.elevate()` 呼叫數** | **0** ✅（工單要求 0） | 次 |
| **網路 LLM 呼叫數** | **0** ✅（僅用 deterministic `StubElevationLLM`，無 provider） | 次 |

Gate 統計（每 agent）：`submissions == accepted`、`rejected == 0`、`eh2_world_blocked == 0`、`eh2_external_facts_filtered == 0`。
⇒ **兩道防火牆計數器全為 0，證實「沒有任何一條被牆擋下」；問題出在牆之前。**

### 5.2 生產交叉驗證

生產 `data/elevation/elevation_nodes.jsonl` 中，帶 `thread_id` / `sage_fact_id` provenance 的節點數 = **0**（401 個節點全數掃描）。
⇒ 與沙盒量測一致：**線頭事實至今未在生產產生過任何昇華節點。**

### 5.3 反事實探針（僅供判讀修正方向，**非本票修正**）

只改**一個變數**：在 `extras` 多放一個 `"narrative"` 鍵（值＝`thread.narrative_content`），其餘路徑完全不變
（同樣的 `InnerLifeWriter`、同樣的 `SubmissionGate.submit()`、同樣的 `run_elevation`）。沙盒另開 `elevation_cf/` 隔離。

| 指標 | 真實路徑 | 反事實（＋`narrative`） |
|---|---|---|
| `run_elevation()` 呼叫 | 35 | 35 |
| `engine.consume()` 收到的事件 | **0** | **35** |
| 分類＝真實感悟正文 | 0（0%） | **35（100%）** |
| 分類＝中繼標籤退化 | 0 | 0 |
| 產出 pattern 節點 | 0 | **35** |
| `engine.elevate()` 呼叫 | 0 | 9 |
| 產出 belief／value 節點 | **0** | **7（全部為 `value`）** |
| 網路 LLM 呼叫 | 0 | 0 |

反事實產出的 7 顆 `value` 節點 content 樣本（人工判讀：**全部是人話散文，非中繼標籤**）：

```
value / agent_akane
  卸妝的時候，手還在用那個角色的方式動。棉片擦過眼尾，鏡子裡的人慢慢變回我——但呼吸的節奏還留在她那裡。
  不是壞事。只是有點分不清，哪一層先脫落。明天試鏡的台詞壓在桌角，我沒有拿起來。今晚先讓這個形狀待著，等它自己鬆掉。

value / agent_anna
  部屋の電気を落とす前に、写真をもう一度開いた。月はやっぱりまん丸で、端っこに自分の指が少し写り込んでいた。
  長い説明は書かない。消してしまわないように、一言だけ。「まん丸だった」。それだけ打って、送信を押した。返事は待たない。

value / agent_mahiru
  朝、玄関の棚の壺を下げて、蓋を開けた。まだ微かに温かい。一口、味を見る。薄くなっていない、大丈夫。
  二杯分のまま、もう一杯は残した。今日はこれを温め直して、保温の壺に移し替える。包む布を選んで、それから隣のドアの前に立つ。

value / agent_mai
  夜、二十二時。部屋の灯りを半分だけ落として、ベッドの端に座った。スマホを開いて、今日の朝のことを思い出す。
  ……数えなかった。三駅目で気づいた、あの感じ。それだけのことなのに、今になって、肩の力が抜けていくのが分かる。

value / agent_miku
  黑海で船が沈んだ、と報じていた。ブルガリアがロシア大使を呼びつけた、とも。……
```

**判讀**：7/35 = **20%** 的昇華率不是內容品質問題，而是 `elevate()` 的 `min_evidence` 分組門檻（預設 2，`soul_elevation/engine.py:456`）
加上 `elevate_matured_patterns` 只在同一 `candidate_node_type` 組內累積獨立證據（SE-1 獨立證據計數）所致。
**只要正文接上，全鏈就通電。**

---

## 6. 生產零寫入證明（原始輸出）

量測前後對 **48 個**生產檔案取 sha256（`data/elevation/*.jsonl` 5 個 ＋ 10 個 `graph.sqlite` 及其 `-wal`／`-shm` 33 個 ＋ 10 個 `life_threads.jsonl`）：

```
   sha256 before/after (all 48 files):
      SAME     544edac1006b0848  data\elevation\elevation_edges.jsonl
      SAME     0dc800008f13d013  data\elevation\elevation_lifecycle_events.jsonl
      SAME     c043bc015f2956bb  data\elevation\elevation_nodes.jsonl
      SAME     677ceac94b51ee1d  data\elevation\elevation_projection_trace.jsonl
      SAME     8b67cad24e217737  data\elevation\elevation_trace.jsonl
      SAME     d1ea3654cd992b67  data\memory\agent_akane\graph.sqlite
      SAME     abb8595099ab55b0  data\memory\agent_akane\graph.sqlite-shm
      SAME     f59519c970ae9ef9  data\memory\agent_akane\graph.sqlite-wal
      SAME     920be857449f5715  data\memory\agent_anna\graph.sqlite
      SAME     f963706fe30d2b30  data\memory\agent_anna\graph.sqlite-shm
      SAME     ab908b8dae2b0e6c  data\memory\agent_anna\graph.sqlite-wal
      SAME     5f0174332ffacbb3  data\memory\agent_aoi\graph.sqlite
      SAME     dc72c320d2e8322c  data\memory\agent_aoi\graph.sqlite-shm
      SAME     f0ff33c9c02987f7  data\memory\agent_aoi\graph.sqlite-wal
      SAME     9891aa38d22b6307  data\memory\agent_mahiru\graph.sqlite
      SAME     368d34aa9a5cf9cf  data\memory\agent_mahiru\graph.sqlite-shm
      SAME     d7ca634ff14d58f7  data\memory\agent_mahiru\graph.sqlite-wal
      SAME     74de5341f0832bcc  data\memory\agent_mai\graph.sqlite
      SAME     11511ee6b0b46a48  data\memory\agent_mai\graph.sqlite-shm
      SAME     7794d75f4b554ce4  data\memory\agent_mai\graph.sqlite-wal
      SAME     145ae869e1857a3e  data\memory\agent_miku\graph.sqlite
      SAME     665ee3cbf6f3c930  data\memory\agent_miku\graph.sqlite-shm
      SAME     11d26da02ebb4e54  data\memory\agent_miku\graph.sqlite-wal
      SAME     57c678540e546e15  data\memory\agent_ram\graph.sqlite
      SAME     520b9f24bf5f834c  data\memory\agent_ram\graph.sqlite-shm
      SAME     714f9611e181d13a  data\memory\agent_ram\graph.sqlite-wal
      SAME     402b9346a98a5b44  data\memory\agent_rem\graph.sqlite
      SAME     76c1a11e7fffbd2b  data\memory\agent_rem\graph.sqlite-shm
      SAME     ba10595c8cbc234b  data\memory\agent_rem\graph.sqlite-wal
      SAME     eb7e486e589cee06  data\memory\agent_ruka\graph.sqlite
      SAME     4f4e297b8f206a49  data\memory\agent_ruka\graph.sqlite-shm
      SAME     6024c5e1c305da28  data\memory\agent_ruka\graph.sqlite-wal
      SAME     2e15eb1cb3024bbc  data\memory\agent_yua\graph.sqlite
      SAME     91ae23f9f06f4ea5  data\memory\agent_yua\graph.sqlite-shm
      SAME     b8d5c220f5cb05fd  data\memory\agent_yua\graph.sqlite-wal
      SAME     1abdae2745042821  data\memory\sage\graph.sqlite
      SAME     fd4c9fda9cd3f9ae  data\memory\sage\graph.sqlite-shm
      SAME     e3b0c44298fc1c14  data\memory\sage\graph.sqlite-wal
      SAME     900a78f1f617d5a5  data\soul\agent_akane\life_threads.jsonl
      SAME     bb5f5fb72f038ef6  data\soul\agent_anna\life_threads.jsonl
      SAME     de2cec5de5cba589  data\soul\agent_aoi\life_threads.jsonl
      SAME     c39dbff91828a694  data\soul\agent_mahiru\life_threads.jsonl
      SAME     9fb64b8cc76e0163  data\soul\agent_mai\life_threads.jsonl
      SAME     fb0c7469895772d6  data\soul\agent_miku\life_threads.jsonl
      SAME     255e2deeeebc9567  data\soul\agent_ram\life_threads.jsonl
      SAME     efcdb0af02cc6fa0  data\soul\agent_rem\life_threads.jsonl
      SAME     1eb419a3c254a6b2  data\soul\agent_ruka\life_threads.jsonl
      SAME     6fed6700bc1e4e70  data\soul\agent_yua\life_threads.jsonl

   files whose sha256 changed during the measurement: 0
   audit-hook write-opens total        : 142
   audit-hook write-opens under data/  : 0
   audit-hook sqlite3.connect under data/: 0 (read-only sessions, PRAGMA query_only=ON)
```

（`sqlite3.connect` 目標共 22 個：11 個沙盒副本 ＋ 11 個生產 `mode=ro` URI。）

### 量測腳本自帶斷言（全部 PASS，exit code 0）

```
  [PASS] A1 M1 inventory complete (dissolved facts counted)  -- n=35
  [PASS] A2 every dissolved fact adjudicated (M2 rows == facts)  -- m2=35 facts=35 unmatched=0
  [PASS] A3 R1 blocks none (non-world trigger)  -- {'PASS/PASS': 35}
  [PASS] A4 fact layer filters none  -- {'PASS': 35}
  [PASS] A5 R3 blocks none  -- {'PASS': 35}
  [PASS] A6 all facts reach '可昇華' verdict  -- {'可昇華': 35}
  [PASS] A7 every run_elevation invocation accounted for (consume inputs + logged fail-closed == run_elevation calls)  -- consume=0 failclosed=35 run_elevation=35
  [PASS] A7b root cause traced to a specific function+line (0 events carry extras['narrative'] ⇒ _event_content()='' ⇒ to_input()=None)  -- narrative=0 nonempty=0 none=35 of 35
  [PASS] A8 M4: elevate() call count == 0  -- elevate=0
  [PASS] A9 M4: 0 network LLM calls — only the deterministic StubElevationLLM (1 classify per consume input)  -- classify=0 consume=0
  [PASS] A11 zero production write-opens under data/  -- []
  [PASS] A12 production sha256 unchanged for every file  -- changed=[]
```

---

## 7. GO / NO-GO 結論

**NO-GO。**

| 判準 | 結果 | 理由 |
|---|---|---|
| C1 可達性 | ✅ PASS | 35/35 條事實不被 R1／fact 層／R3 任一道擋下（§3 全表） |
| C2 保真度 | ❌ FAIL | 引擎收到 **0/35**；分類為真實感悟正文 **0 筆（0%）**，門檻 80% 不成立 |
| C3 產出 | ❌ FAIL | belief／value 節點 **0 顆**；沙盒節點增量 **+0** |

### 具體缺口（哪個函式、哪一行、哪個條件）

| 缺口 | 位置 | 條件 |
|---|---|---|
| **正文未進事件** | `src/soul/life_thread_orchestrator.py:418-423` | `extras` 缺 `"narrative"` 鍵 |
| **正文只認白名單** | `src/inner_life/elevation_adapter.py:143` | `_NARRATIVE_EXTRA_KEYS = ("narrative",)`（SOUL-BUG-006 刻意設計，**不可放寬**） |
| **空正文回空值** | `src/inner_life/elevation_adapter.py:248` / `:294` | `_event_content()` → `""`；`inner_life_event_to_input()` → `None` |
| **無輸入可消費** | `src/inner_life/elevation_adapter.py:718-720`, `:741-742` | `inputs = []` ⇒ `engine.consume()` 0 次 |

### 最小可行修正方向（**本票未實作**）

**只需要讓事件帶上一段人話正文即可。** 兩個等價選項（擇一，由 W3 票拍板）：

- **選項 A（最小改動、符合既有白名單）**：在 `life_thread_orchestrator.py:418-423` 的 `extras` 補一個
  `"narrative": str(thread.get("narrative_content") or "")`。
  - 符合 `_NARRATIVE_EXTRA_KEYS` 白名單，**不動 SOUL-BUG-006 的防線**。
  - `Provenance.extras` 的 value 必須是 `str`（`event.py:126-130`），空字串合法。
  - 反事實實測：35/35 進入引擎、35 pattern、7 value 節點（§5.3）。
- **選項 B（不動 extras 結構）**：改用 `inner_life_event_to_input(ev, narrative=...)` 的既有 `narrative` 參數
  （`elevation_adapter.py:273`），但該參數目前**只在 `run_elevation` 內部使用**，`_to_input()` 未暴露 ⇒ 需改動 adapter 介面，改動面較大。

**設計裁決點（需主大腦／Owner 拍板）**：用哪一段文字當正文？

| 候選 | 內容 | 長度 | 性質 |
|---|---|---|---|
| `thread.narrative_content` | 線頭的**經歷敘事** | 108–345 字（中位 174） | 具體場景、動作、感官 |
| `facts.object` | 線頭的**溶解反思** | 45–141 字（中位 87） | 已沉澱的**感悟/信念陳述** |

Owner 的命題是「經歷能沉澱成**信念**」。從語意上 `facts.object` 更貼近「信念」，
但 `narrative_content` 才是「**經歷**」。反事實實測用的是 `narrative_content`，
得到 7 顆 `value` 節點。**建議（待裁決）**：以 `narrative_content` 為正文（它承載「經歷」的真實性），
並把 `facts.object`（溶解後的感悟）作為**另一個事件**或 `extras` 的獨立鍵（如 `"reflection"`）送進去——
否則「經歷 → 信念」的**兩段**語意會被壓成一段。

**另需注意（不阻塞，但應一併裁決）**：溶解事實目前**從未作為 memory_fact 進入昇華鏈**
（`life_thread_orchestrator.py:429` 傳的是空 tuple）。若 W3 的目標是「事實 → 信念」，
`submit(event_id, (fact,), agent_id=...)` 傳入該條 fact 會讓 fact 層過濾器第一次有東西可過濾——
這條路會改變 `extras`／provenance 的組成，**需要獨立的設計裁決，不在本票範圍**。

---

## 8. W3 工單（`docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md`）需要調整的具體條目

| # | W3 工單條目 | 建議調整 | 理由 |
|---|---|---|---|
| 1 | W3 step 3「依線頭的 `origin_type` 對應時段」 | **修正為**：trigger_type 依 **scheduler slot**（morning／night）決定；`origin_type` 的實際值域是 `necessity_driven` / `whim_driven` / `world_collision`，**不是時段** | 實測 35/35 條 `origin_type` ∈ {necessity_driven, whim_driven}；`origin_type` 無法映射到時段 |
| 2 | W3 假設「建好事件即進昇華」 | **補一條**：事件必須帶 `extras["narrative"]`（或 `narrative` 參數），否則 `_event_content()` 回空、fail-closed | 本量測根因；W3 工單未提正文如何進入事件 |
| 3 | W3「只 consume 不 elevate」 | **補一條**：從 pattern 到 belief/value 需另一個機制 `elevate_matured_patterns`（`elevation_adapter.py:1098`），與 Submission Gate **相互獨立** | 反事實顯示 consume 只出 35 pattern；value 節點是 `elevate_matured_patterns` 產的（`elevate()` 呼叫 9 次） |
| 4 | W3 產出驗收 | **補一條**：驗收須區分「事件已建立」與「節點已產出」；目前 `maybe_emit_elevation_events` 回 `True` 但節點數為 0，屬靜默失敗 | §4.4 附帶觀察 |
| 5 | W3 對「溶解事實」的定位 | **釐清**：`sage_fact_id` 目前只是 `extras` 裡的一個 id，fact 本身未進入昇華鏈 | `life_thread_orchestrator.py:429` 傳空 tuple |

---

## 9. 與既有文件的出入（**需主大腦裁決，本票未自行修正**）

| 文件 | 該文件寫的 | 今天的實測 | 差異 |
|---|---|---|---|
| `docs/INNER-LIFE-DEFINITION-OF-DONE.md:14-23` | 溶解事實 **21** 條；線頭 **38** 條（17 active / 21 completed）；進入 `run_elevation` = **0** | 溶解事實 **35** 條；線頭 **55** 條（20 active / 35 completed）；進入 `run_elevation` = 35 次但引擎收到 **0** 筆 | 分母 +14 條事實、+17 條線頭；「進入 run_elevation = 0」→ 現為 35 次（但引擎仍收 0） |
| 工單 §5 C1 的「真實 21 條事實」 | 21 | 35 | 本報告一律以 35 為準 |

**推測**：2026-10-06 之後的 catch-up 掃描（`LIFE_THREAD_CATCHUP_ENABLED`）把更多終態線頭溶解了。
**本票未驗證此推測**（未追查 catch-up 的執行紀錄）。若此推測成立，`docs/INNER-LIFE-DEFINITION-OF-DONE.md` 的數字需要更新。

---

## 10. 未完成／受阻事項（如實列出）

1. **未跑全庫回歸**（工單 §6 明講本票不跑，由主大腦另跑）。
2. **未寫 `tests/**`**（工單明講本票不寫）。
3. **未追查 21 → 35 的來源**（catch-up 掃描假設未驗證，見 §9）。
4. **未追查生產端 fail-closed log 的量**（生產 orchestrator 的 log 檔未納入量測範圍；本報告的 35 條 log 來自沙盒）。
5. **`origin_type` 與時段的對應關係**未在任何契約中找到定義（`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §4.6 只列旗標行為）；
   本量測以 scheduler slot 為準。
6. **反事實探針不是修正**：`src/**` 一行未改。反事實只跑在 `%TEMP%` 沙盒的獨立 `elevation_cf/` 目錄。
7. **未評估「把 fact 本身送進昇華鏈」的設計後果**（會改變 provenance 組成），留給 W3 票裁決。

---

## 11. 誠實聲明

- 本報告所有數字均來自 `%TEMP%\lt_elev_meas1\` 內的 4 支量測腳本，原始輸出留在
  `%TEMP%\lt_elev_meas1\run.log`、`summary.log`、`census.log`、`tables.md`、`results.json`。
- **「0 kill／0 重啟／0 生產寫入／紅線全遵守」是待驗證主張**：本報告提供了
  audit-hook 寫入紀錄（142 次寫入全部在沙盒、生產 `data/` 下 0 次）與 48 檔 sha256 前後對照（0 變動）作為客觀痕跡，
  請主大腦獨立複核。
- 本票**未發現**「線頭建立事件」在設計上與 frozen contract 衝突
  （`InnerLifeWriter` 為 sole creator、`trigger_type` 用既有 diary 常數、未新增常數、未直接寫 facts 表、未直接呼叫 `run_elevation` — 全部遵守）。
  唯一需要裁決的是**正文用哪一段文字**（§7）。
- **未 commit**：改動留在工作區待主大腦審閱。

---

*量測由 DSH 支線 worker 執行；工單：`logs/LIFE-THREAD-ELEVATION-MEAS-1-WORK-ORDER.md`*
