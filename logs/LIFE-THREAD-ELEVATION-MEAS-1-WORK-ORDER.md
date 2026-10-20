# 工單：LIFE-THREAD-ELEVATION-MEAS-1 — 生活線頭溶解事實至昇華端到端可達性與內容品質沙盒量測

> **狀態**：待派工（決策完備版）
> **日期**：2026-10-19
> **Owner 授權**：2026-10-19 方向 B 立案裁決「核准開立方向 B **純唯讀量測票**，嚴禁盲目直接接線」；
>   「以數據輸出作為方向 B 是否動工的**唯一判準**」。
> **性質**：**純唯讀量測（Pure Read-Only / Zero Production Mutation）**。
> **產出**：一份量測報告 `logs/LIFE-THREAD-ELEVATION-MEAS-1-REPORT.md`。**不改任何 `src/**`。**

---

## 0. 背景：本票為什麼存在，以及一個必須先釐清的既有結論

### 0.1 缺口

`docs/INNER-LIFE-DEFINITION-OF-DONE.md`（2026-10-19 版本）第 14–23 行的現況量測：

| 觀察 | 實測值 |
|---|---|
| 線頭總數（依契約 §2.4 正確 fold） | 38（17 active / 21 completed） |
| `necessity_driven` 占比 | 38/38 = 100% |
| 溶解事實（`life_thread_dissolved`） | **21 條**，21/21 皆有 `dissolved_at` 與 `sage_fact_id` |
| **溶解事實進入 `run_elevation`** | **0** |
| emergent projection / belief / value 落盤 | **無** |

診斷原文：「引擎已能生產生活，且溶解品質良好（實例：ruka「這六天讓我明白，訊息不只是文字，而是我願意為一個人保留溫度的證明」）。但**單一起源**使個性分量為零，**未接昇華路徑**使經歷無法沉澱成信念。綜合判讀：**目前程度為「能呼吸」，尚未「活著」。**」

### 0.2 既有工單已存在的部分（**不要重做**）

`docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md`（2026-10-03，主大腦出票）**已存在**，且其 §1「調查結論（已實測驗證，非推測）」已載明：

- 昇華鏈的**每一道閘門**對生活線頭**都已敞開**（producer 合法性 ✅／EH-2.1 R1 垂直防火牆 ✅ 0 阻斷／fact 層過濾 ✅／只 consume 不 elevate ✅）。
- **全域事實分佈實測**：`origin` 唯一值為 `lived_experience`（1201 筆），**0 筆 `external_world`**。
- 唯一缺口：`life_thread_orchestrator` 的 docstring 明定「0 InnerLifeEvent、0 bus、0 Agency 觸發鏈」⇒ 線頭**從未建立事件** ⇒ `run_elevation` 對線頭**從未被行使**。
- 結論原文：「**不需要解除任何圍堵、不需要改動防火牆、不需要改變任何事實的 `origin`**。」

### 0.3 🔴 Owner 裁決中的一個假設，與既有實測**衝突**（本票必須分清）

Owner 2026-10-19 的立案理由提到「**EH-2 R3 垂直防火牆**」可能靜默過濾線頭事實。**但既有實測與此相反**，且原因是兩道防火牆被混為一談：

| 防火牆 | 位置 | 阻斷對象 | 對線頭事實（`origin=lived_experience`）的裁決 |
|---|---|---|---|
| **EH-2.1 R1**（producer 側） | `src/inner_life/submission_gate.py:392-405` | `world:news*`／`world:feed*`／`world:celebrity_news` 觸發型 | ✅ **0 阻斷**（所有非 `world:*` trigger 放行） |
| **EH-2 R3**（內容側） | `src/inner_life/elevation_adapter.py` 防線二（`_EH2_SENTINEL` 中和路徑） | `origin="assimilated"` 事實衍生的 pattern 不得進入 belief/value 候選分組 | 依 `src/memory/sage/models.py:46-51`：`assimilated`／`external_world` 才走認知三態；線頭事實 `origin="lived_experience"` ⇒ **不在 R3 的阻斷範圍** |

⇒ **本票的第一項責任是「用今天的程式碼重新裁決這兩道牆」**，並且**用真實的 21 條事實逐條裁決**（不是引用 16 天前的結論）。
⇒ 契約層面的硬性事實（`src/soul/life_thread_dissolution_exec.py:127-131` 常數）：
`FACT_PREDICATE = "life_thread_dissolved"`、`FACT_SOURCE = "inference"`、**`FACT_ORIGIN = "lived_experience"`**、`FACT_CONFIDENCE = 1.0`。
**若實測發現生產事實的 `origin` 不是 `lived_experience`，那是比本票量測更嚴重的缺陷，必須在報告中標紅並回報主大腦，不得自行修正。**

### 0.4 本票與 W3 接線票的分工

| | 本票（LIFE-THREAD-ELEVATION-MEAS-1） | W3（LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER） |
|---|---|---|
| 性質 | **量測**，不改 `src/**` | 接線（開旗標路徑 ＋ 測試） |
| 回答的問題 | 「線頭的經歷**能不能**昇華？昇華出來的**是信念還是垃圾**？」 | 「如何把接縫開起來？」 |
| 產出 | 報告 ＋ **GO / NO-GO 判準** | 可執行的接縫實作 |

⇒ **本票是 W3 的前置。** 本票的報告必須能直接回答：「W3 工單的六個步驟是否成立？若不成立，缺的是什麼？」

---

## 1. 目標

在 **`%TEMP%` 隔離沙盒**上，用**生產真實的 21 條 `life_thread_dissolved` 事實**，端到端量出四件事：

1. **生產已溶解事實元資料盤點**（origin／source／confidence／sage_fact_id 分佈）。
2. **EH-2 R1 與 EH-2 R3 兩道防火牆的逐條穿越裁決**。
3. **昇華模型實際收到的內容保真度**（真實感悟正文 vs 中繼標籤退化）。
4. **端到端模擬產出量測**（若接上接縫，能產出幾顆合法的 belief／value）。

**一句話**：把「線頭能不能沉澱成信念」從**信念**變成**數字**。

---

## 2. 範圍

### 2.1 你擁有的寫入權

| 檔案 | 動作 |
|---|---|
| `logs/LIFE-THREAD-ELEVATION-MEAS-1-REPORT.md`（**新增**） | 唯一 repo 產出物（量測報告） |
| `%TEMP%` 下的量測腳本 | 任意（**不得**進 repo） |

### 2.2 明確不得動

- ❌ **任何 `src/**`**（本票是量測，不是接線）。
- ❌ **任何 `data/**`**（生產狀態，**唯讀**；寫入只准落在 `%TEMP%` 沙盒）。
- ❌ `configs/**`、`personas/**`、`docs/**`（既有契約文件）。
- ❌ 外部 `soul-elevation` repo。
- ❌ 任何 `tests/**`（本票不寫測試——量測腳本自帶斷言即可）。
- ❌ **不得重啟服務**、**不得發任何訊息**、**不得殺任何行程**。

---

## 3. 做法（決策已定，執行者照做）

### 3.0 沙盒紀律（**硬性**，沿用 `scripts/eval_lifecycle_cli.py` 的做法）

- **生產 `data/**` 只讀**；所有寫入（fact 寫入、elevation 節點、sidecar、trace）**只准落在 `%TEMP%` 沙盒**。
- 沙盒建立方式：把 `data/memory/<agent_id>/graph.sqlite`（10 個）與 `data/elevation/`（節點＋邊＋sidecar）**複製**到 `%TEMP%` 沙盒；
  **所有寫入目標路徑都必須指向沙盒**。
- **量測前後必附生產檔案 sha256 對照**（`data/elevation/*.jsonl` ＋ 10 個 `graph.sqlite`），證明生產零寫入。
- **不得**使用 `subprocess` 啟動任何服務；**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`；**不得**對生產發真實請求。
- 量測腳本一律寫成 `.py` 檔再執行（**不可**用 `python -c "<多行>"`）；執行前設
  `$env:PYTHONIOENCODING='utf-8'` 與 `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8`（cp950 會 `UnicodeEncodeError`）。
- 用 `.venv\Scripts\python.exe`（唯一合法解譯器）。

### 3.1 M1 — 生產已溶解事實元資料盤點

- **資料源**：per-agent 事實庫 `data/memory/<agent_id>/graph.sqlite`（10 個 agent）＋ `data/memory/sage/graph.sqlite`。
  ⚠️ **表結構請先自探**（`sqlite3` 讀 `sqlite_master` / `PRAGMA table_info`），不要假設表名；**唯讀**開啟（`file:…?mode=ro` URI）。
- 篩出 `predicate == "life_thread_dissolved"` 的**全部**事實。
- 報告下列分佈（**分子／分母／單位**一律寫清楚）：
  | 欄位 | 內容 |
  |---|---|
  | 總數 | 溶解事實筆數（分母 = 全域事實總數） |
  | `origin` | 取值分佈（**必須**與 `FACT_ORIGIN="lived_experience"` 對照） |
  | `source` | 取值分佈（對照 `FACT_SOURCE="inference"`） |
  | `confidence` | 取值分佈（對照 `FACT_CONFIDENCE=1.0`） |
  | `subject`（agent） | 每隻 agent 各幾筆 |
  | `sage_fact_id` 覆蓋率 | 有／無 `sage_fact_id` 的比例 |
  | `object` 長度 | 分布（min／中位／max，字元數） |
- **逐條列出**（在報告的附錄）每一條的 `node_id`／`agent_id`／`origin`／`sage_fact_id`／`object` 前 120 字。

### 3.2 M2 — 兩道防火牆的逐條穿越裁決

**在沙盒上**對每一條真實事實做以下裁決（**每條都要有裁決結果，不准抽樣**）：

| 閘門 | 判定方式 |
|---|---|
| **EH-2.1 R1**（producer 側） | 用 `src/inner_life/submission_gate.py` 的實際判定邏輯，逐條算出「放行／阻斷」＋理由。線頭事件的 trigger_type 依 W3 工單步驟 3 = 既有 `TRIGGER_TYPE_DIARY_MORNING`／`TRIGGER_TYPE_DIARY_NIGHT`。 |
| **fact 層過濾** | `submission_gate.py:407-418`：逐條算出「被過濾／放行」＋理由。 |
| **EH-2 R3**（內容側） | 在 `src/inner_life/elevation_adapter.py` 的分組／計票邏輯上，逐條算出該事實衍生的 pattern 是否會進入 belief/value 候選分組。若被排除，必須指出**是哪一行、依哪個欄位**排除的。 |
| **最終裁決** | 每條事實一列：`可昇華 / 被 R1 擋 / 被 fact 層擋 / 被 R3 擋 / 缺事件` ＋ 一句理由。 |

⇒ **報告必須明確回答**：兩道牆在**今天**的程式碼上，對**真實的 21 條**線頭事實，各自的實際裁決是什麼。

### 3.3 M3 — 昇華模型實際收到的內容保真度

**在沙盒上**把真實事實餾成 `InnerLifeEvent`（**依 W3 工單步驟 2/3/4 的語意**：`completed` 狀態且有 `sage_fact_id` 才建事件；trigger_type 用既有 diary 常數；**走 `InnerLifeWriter` ＋ `submission_gate.submit()`，嚴禁直接呼叫 `run_elevation`、嚴禁直接寫 facts 表**），然後：

- 攔截**引擎實際收到的文字**（`engine.consume()` 的輸入、`elevation_adapter` 送進 `soul_elevation` 的 content）。
- 逐條分類：
  - ✅ **真實感悟正文**（例：ruka「這六天讓我明白，訊息不只是文字，而是我願意為一個人保留溫度的證明」）
  - ⚠️ **中繼標籤退化**（`thread_id=…`／`slot=night`／`origin_type=…` 這類欄位值被當成正文）
  - ❓ **其他**（空字串、超長、被截斷）
- 報告**分類比例**（分子／分母／單位）＋ **每條的原文摘錄**（前 150 字）。
- **這是本票最關鍵的一項**：它直接回答「會不會重演『標籤被昇華成信念』的退化缺陷」。

### 3.4 M4 — 端到端模擬產出量測

**在沙盒上**把 M3 建好的事件真的送進昇華鏈（`submission_gate.submit()` → `run_elevation` → `engine.consume()`），然後量：

| 量 | 內容 |
|---|---|
| 進入 `run_elevation` 的事件數 | 分子／分母（分母 = 21 條真實事實） |
| 產出的 belief／value 節點數 | 分佈（by node_type、by agent） |
| 產出節點的 content 樣本 | 每顆前 150 字（人工可判讀：是真信念還是垃圾） |
| 沙盒 sidecar／節點檔增量 | 寫了什麼（**只在沙盒**） |
| `elevate()` 呼叫次數 | 必須 **0**（契約：只 consume 不 elevate） |
| LLM 呼叫次數 | 必須 **0**（本票不做任何 LLM 呼叫；昇華是確定性演算法） |

**若產出為 0 顆**：必須追到**具體是哪一個函式回傳了空結果**，並引用其行號與條件（Bry 的紀律：「寬鬆 except ＋ 回傳空值是缺陷的隱形斗篷」⇒ 空結果必須追到根，不許只報「0」）。

---

## 4. 驗收（完成的定義）

1. **四項量測全部完成**，每項都有分子／分母／單位的數字與可複核的摘錄。
2. **兩道防火牆的逐條裁決表**完整（21 條，一條不缺）。
3. **內容保真度分類比例**有明確數字，且每條有原文摘錄。
4. **端到端產出**有明確數字；若為 0，追到根因行號。
5. **生產零寫入證明**：量測前後 `data/elevation/*.jsonl` ＋ 10 個 `graph.sqlite` 的 sha256 逐位元相同（附原始輸出）。
6. **報告** `logs/LIFE-THREAD-ELEVATION-MEAS-1-REPORT.md` 存在，且含 §5 的 **GO / NO-GO 判準**結論。
7. `git status --porcelain` 只有該報告檔（無其他變動）。

---

## 5. GO / NO-GO 判準（本票的最終交付）

報告末尾**必須**給出以下二選一結論與理由：

| 條件 | GO 條件 |
|---|---|
| **C1 可達性** | M2 顯示真實 **35 條**事實**不被 R1／fact 層／R3 任一道擋下**（逐條裁決表佐證） |
| **C2 保真度** | M3 的真實感悟正文比例 **≥ 80%**（分子 = 分類為「真實感悟正文」的事件數，分母 = 進入引擎的事件數） |
| **C3 產出** | M4 產出 belief／value 節點 **≥ 1 顆**，且其 content 經人工判讀**不是中繼標籤** |

- **C1 ∧ C2 ∧ C3 全部成立 ⇒ GO**（W3 接線票可開工；報告須指出 W3 是否需要調整）。
- **任一不成立 ⇒ NO-GO**，且報告**必須**指出**具體缺口**（哪一個函式、哪一行、哪個條件）與**最小可行修正方向**（但**不得**在本票實作任何修正）。

---

## 6. 測試

本票**不寫 `tests/**`**。量測腳本必須**自帶斷言**（每個斷言對應 §4 的一條驗收），任何斷言失敗 ⇒ 腳本非零退出。
腳本與其斷言輸出留在 `%TEMP%`，報告中引用關鍵輸出摘錄。

**不跑全庫回歸**（主大腦另外跑）。

---

## 7. 回報格式

1. **四項量測的完整數字**（含分子／分母／單位）與關鍵摘錄。
2. **兩道防火牆逐條裁決表**（21 列）。
3. **內容保真度分類表**（21 列，附原文摘錄）。
4. **端到端產出**（節點數、類型分佈、content 樣本、LLM 呼叫數、`elevate()` 呼叫數）。
5. **生產零寫入證明**（量測前後 sha256 對照原始輸出）。
6. **GO / NO-GO 結論 ＋ 理由**（若 NO-GO，附最小可行修正方向）。
7. **W3 工單是否需要調整的具體條目**（若有）。
8. **未完成或受阻事項**（如實列出）。
9. **誠實要求**：若發現「線頭建立事件」在設計上與某項契約衝突，**停下來回報**，不要自行繞過。
   執行者宣稱的「0 kill／0 重啟／0 生產寫入／紅線全遵守」一律是**待驗證主張**，主大腦會獨立複核。

---

## 附錄 A — 關鍵程式碼座標（實作者必知，省下考古時間）

| 位置 | 內容 |
|---|---|
| `src/soul/life_thread_dissolution_exec.py:127-131` | `FACT_PREDICATE="life_thread_dissolved"`、`FACT_SOURCE="inference"`、`FACT_ORIGIN="lived_experience"`、`FACT_CONFIDENCE=1.0` |
| `src/memory/sage/models.py:46-51` | `origin` 值域：`lived_experience`（DB 未填時的 v9 DEFAULT）／`assimilated`／`external_world`／`native_episode`；`assimilated`／`external_world` 才走認知三態 |
| `src/memory/sage/writer.py:164` | `MemoryWriter.add_fact(fact) -> str`（回 fact_id，失敗回 `""`） |
| `src/inner_life/submission_gate.py:82-131` | producer 合法性（`VALID_PRODUCER_TRIGGER_TYPES`） |
| `src/inner_life/submission_gate.py:392-405` | **EH-2.1 R1** 垂直防火牆（只擋 `world:news*`／`world:feed*`／`world:celebrity_news`） |
| `src/inner_life/submission_gate.py:407-418` | fact 層過濾 |
| `src/inner_life/submission_gate.py:420-431` | 只 consume 不 elevate（`elevate()` 永不呼叫） |
| `src/inner_life/submission_gate.py:207-208` | `InnerLifeWriter` 是 sole canonical InnerLifeEvent creator（M5.4-5.1 frozen） |
| `src/inner_life/elevation_adapter.py` | `run_elevation`、防線二（R3 中和，`_EH2_SENTINEL` 路徑） |
| `src/inner_life/emergent_projection.py` | 最終投影（`format_emergent_block`）——用來驗產出是否真的會被投影 |
| `src/soul/life_thread_orchestrator.py` | **缺口所在**：docstring 明定「0 InnerLifeEvent、0 bus、0 Agency 觸發鏈」 |
| `docs/LIFE-THREAD-W3-ELEVATION-SEAM-WORK-ORDER.md` | 方向 B 的接線工單（**只讀參照**） |
| `docs/INNER-LIFE-DEFINITION-OF-DONE.md:14-23` | 現況量測基線（溶解事實 21 條、進入 `run_elevation` = 0） |
| `docs/LIFE-THREAD-ENGINE-CONTRACT.md:288-297` | §3.4 SAGE 寫入規格（Fact 組裝逐欄） |
| `data/memory/<agent_id>/graph.sqlite`（10 個） | per-agent 事實庫（**表結構自探**，唯讀開啟） |
| `data/memory/sage/graph.sqlite` | 全域事實庫 |
| `data/elevation/` | 昇華圖譜（節點／邊／sidecar）——沙盒複製來源 |

**既有量測腳本可重用**：`%TEMP%\sbg010b_probe.py`／`sbg010b_accept.py`（2026-10-19，產生了 `docs/INNER-LIFE-DEFINITION-OF-DONE.md` 的數字）——**先讀它們**再決定是否重用，能省下大量考古時間。
