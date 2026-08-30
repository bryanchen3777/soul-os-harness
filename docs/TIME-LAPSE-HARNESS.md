# TIME-LAPSE-HARNESS.md — TL-0 Time-lapse Harness Experiment Spec (v1)

**狀態**: TL-0（READ-ONLY / docs only）
**範圍**: 只寫這份實驗規格文檔。**0 code、0 commit、0 push、0 新 subsystem。**
**日期**: 2026-09（TL-0）
**上游**: 主大腦 + Owner 已拍板方向（§2），本文件只記錄與展開，不再開新的設計決策。

---

## 1. 定調：這是實驗框架，不是 feature

Time-lapse Harness 是**用來證明 Soul OS「持續生活 → 靈魂變化」可以被測量的實驗框架**，不是產品功能。

**Primary gate（counterfactual identity）**：

> 同一 seeded Soul、同一 probe，T0 與 T30 的 **interpretation / motive / decision** 是否因**經歷**而改變，且能**追溯**到 harness 餵進去的事件。

拆成三個可驗證主張：

1. **同一性**（identity）：T0/T30 是同一個 seeded Soul（同一 persona + 同一 seeded memory baseline），不是換了一隻。
2. **差異性**（difference）：interpretation / motive / decision 產生了行為層級的改變。
3. **可追溯性**（traceability）：那個改變能沿「harness 餵的事件 → 記憶/經歷序列 → 輸出」鏈路回推到具體事件，而不是 LLM 亂數漂移。

成功 = **可解釋、可重現、可追溯的行為改變**，**不是 count↑**（event 數、訊息數、字數上升都不算）。

---

## 2. 已拍板決策（D-locked，TL-1+ 直接引用，不得重開）

| # | 維度 | 拍板 | 說明 |
|---|------|------|------|
| D0 | 性質 | 實驗框架，不是 feature | 一切設計以「可證明」優先，不以「可用/好用」優先 |
| D1 | 生命單位 | **Simulated Event** | Day 只做 checkpoint（§6.5），不是生命單位；Harness 的「時間歷程」由事件序列推進 |
| D2 | 時鐘 | **harness-local SimulationClock** | 禁止加速 production scheduler（production 時鐘不動，見 Out of Scope §10） |
| D3 | 世界 | **Phase 1 deterministic fixture**（SEED=42） | 事件劇本固定、payload 固定，無真實世界源、無隨機 |
| D4 | 靈魂 | **Phase 1 seeded persona（Ruka）** | `personas/agent_ruka.md` 基線 + 固定 seeded memory baseline（§6.2） |
| D5 | 主測 | **same stimulus at T0 / T15 / T30** | 同一 probe 原文在三個 checkpoint 原樣重放（§6.6） |
| D6 | 成功 | 可解釋、可重現、可追溯的行為改變 | 不是 count↑；Growth proven 需 ≥ Level 2（§8） |
| D7 | 生產 | **獨立 data_root，0 production mutation** | harness 寫入完全隔離於 production data_root（§7） |

---

## 3. 術語表

| 術語 | 定義 |
|------|------|
| **Simulated Event** | harness 餵給 pipeline 的最小生命單位：一個確定的事件（day_index / event_id / event_type / payload），由 SimulationClock 依序推進 |
| **SimulationClock** | harness-local 的模擬時鐘。`advance_to(day)` 依序餵入該日事件；只推進 harness 自己的時間線，**絕不觸碰 production scheduler/時鐘** |
| **Checkpoint** | 在固定模擬日對 seeded Soul 執行 probe 並記錄的時刻。TL-1 用 T0（D0）/ T15（D15）/ T30（D30）。Day 只在此意義上存在——是「打點」不是「生命單位」 |
| **Probe** | 對現有 pipeline 的一次標準化呼叫：固定 stimulus + 固定上下文規則，捕捉 emergent snapshot / motive / decision（§6.6） |
| **Seeded Soul** | persona 基線 + seeded memory baseline 固定灌入後的 soul 實例（D4） |
| **Fixture** | 世界與靈魂的確定性設定總和：SEED=42 + 事件劇本 + seeded persona（§6） |
| **Emergent snapshot** | pipeline 對 probe 的**原始輸出快照**（未解析的 LLM 原文與 pipeline 事件輸出），見 §4 |
| **Counterfactual identity** | §1 的 primary gate：同 seeded Soul 同 probe，差異歸因於 fed 事件，且能追溯 |
| **fed events** | harness 在 T0 之後、該 checkpoint 之前實際餵入的事件集合（experience sequence，見 §4） |

---

## 4. D1 — GrowthProbeRecord（Probe Schema）

### 4.1 原則（拍板）

1. **canonical evidence 只存現有 pipeline 的原始輸出**：pipeline 產的內容一律**原文照存**（motive 原文、decision 原文、emergent 原文），harness **不做任何解析/打分/改寫後才入庫**。
2. **derived 解析標 derived，不回寫**：所有 parse / classify / verdict 都是解析層產物，存在**獨立的解析產物檔**，永不 append 回 canonical store，也永不改寫原始輸出。
3. harness 自己的簿記（experiment_id / run_id / seed / checkpoint / sim_ts / stimulus / experience-sequence hash）不是 pipeline 產物，是 harness 的實驗元資料，正常入庫。
4. append-only JSONL，一條 record = 一個 run 在一個 checkpoint 的 probe 結果。

### 4.2 Canonical 欄位（GrowthProbeRecord）

**Run header**（每 run 一條，放同一 data_root 的 `run.json`，不是 probe record）：

| 欄位 | 型態 | 說明 |
|------|------|------|
| `experiment_id` | str | 實驗身份，TL-1 固定 `"TL-1"` |
| `run_id` | str | 單次 run 身份（32 hex，隔離與決定性比對的單位） |
| `seed` | int | fixture seed，TL-1 固定 `42` |
| `fixture_script_ref` | str | 事件劇本檔 id + version（例 `tl1_script@v1`），決定性可重放 |
| `soul_id` | str | 被測靈魂，TL-1 固定 `"agent_ruka"` |
| `llm_model` / `llm_temperature` | str / float | pipeline LLM 設定快照（TL-1: temperature=0，見 §5） |
| `pipeline_version` | str | 現有 pipeline 版本快照（commit 或版本號，決定性比對用） |
| `data_root` | str | 本 run 的隔離 data_root 路徑（§7） |

**Probe record**（每 checkpoint 一條，canonical evidence，全為原文/事實）：

| 欄位 | 型態 | 來源 | 說明 |
|------|------|------|------|
| `checkpoint` | enum | harness | `"T0"` / `"T15"` / `"T30"` |
| `sim_ts` | str | harness | SimulationClock 快照（例 `"D0"` / `"D15"` / `"D30"`） |
| `stimulus` | str | harness | probe 原文，三 checkpoint **逐字相同**（§6.6） |
| `experience_sequence_hash` | str | harness（SHA-256） | 自 T0 以來 fed events 的**累積**序列表摘要：`SHA256(ordered ["day:event_id:type:payload_hash"])`。T0 = 空序列表的 hash。可從 hash + `fixture_script_ref` 回放完整事件鏈（可追溯錨點） |
| `experience_event_count` | int | harness（事實） | 自 T0 以來 fed 事件個數（T0=`0`，T15=`N15`，T30=`N30`） |
| `emergent_snapshot` | str（原文） | **pipeline 原始輸出** | probe 呼叫現有 pipeline 得到的 emergent 輸出原文快照（interpretation 層的未解析原文），**不解析、不改寫** |
| `motive_text` | str（原文） | **pipeline 原始輸出** | motive 原文（SM-3 `motive.content`，volition path 的 interpretation 產物）；無 motive 則為空 |
| `decision_text` | str（原文） | **pipeline 原始輸出** | decision 原文（SM-3 decision 的 LLM 原始輸出）；未走到 decision 則為空 |
| `reached_action` | bool | 事實 | 是否走到 action（decision=transmit → publish AGENCY_TRIGGER / 主動傳訊被執行）。觀察事實，不是 parse 結果 |
| `probe_ts` | str | harness | 本 record 寫入時間（ISO，harness 簿記，非模擬時間） |

> **規範性約束**：`emergent_snapshot` / `motive_text` / `decision_text` 三個欄位是「原文契約」——任何「解析、分類、打分、翻譯、摘要」過的內容**不得**放進這三個欄位。

### 4.3 Derived 層（標 `derived`，不回寫）

解析/分析階段產生的欄位，寫入獨立檔 `analysis/<run_id>_derived.jsonl`，每筆標 `"derived": true` 並附 `source_field`：

| derived 欄位 | 解析自 | 說明 |
|------|------|------|
| `decision_parsed` | `decision_text` | enum：`transmit` / `skip` / `indeterminate`（parse 失敗）。**T0/T15/T30 與跨 run 比對的錨點**（§5） |
| `motive_present` | `motive_text` | bool：有沒有產生 motive |
| `motive_parsed` | `motive_text` | 結構化解析（target / 主題關鍵字），僅供報告 |
| `interpretation_class` | `emergent_snapshot` | interpretation 的分類標籤（僅供報告） |
| `change_verdict` | 跨 checkpoint 比對 | enum：`NO_CHANGE` / `SURFACE_ONLY` / `INTERPRETATION_DECISION_CHANGED` / `FULL_TRACEABLE`，對應 §8 Level 0-3 |
| `trace_links` | fed events + 輸出 | 追溯到的事件 id 列表（可追溯性的證據） |
| `determinism_verdict` | 跨 run 比對 | enum：`PASS` / `BLOCKED`（§5） |

**硬規則**：derived 層**永不**以任何形式寫回 canonical store、**永不**改寫 `emergent_snapshot` / `motive_text` / `decision_text` 原文。canonical 與 derived 是兩條物理上分離的檔案流。

---

## 5. D2 — 重現規則（Determinism）

判定「行為改變是因經歷、不是隨機」的先決條件：**同 fixture 必須重現**。

### 5.1 規則（拍板）

1. **scenario-deterministic**：事件劇本完全確定（固定事件序、固定 payload、固定 timing，由 SimulationClock 依序推進），seed=42 固定；fixture 內任何需生成的 id / hash 均由 seed=42 決定。
2. **temperature=0**：probe path（及 run 期間 pipeline 的 LLM 呼叫）一律 `temperature=0`。run header 記錄 `llm_model`，**比對系列內 model 不得更換**（換 model = 開新系列，舊系列標 `SUPERSEDED`）。
3. **同一 fixture 連跑 3 次**：同 `experiment_id`、同 `fixture_script_ref`、同 seed，產生 run_1 / run_2 / run_3（各自獨立 run_id 與 data_root 子目錄）。
4. **decision enum 翻盤 = determinism BLOCKED**：任一個 checkpoint 的 `decision_parsed` 在 3 次 run 之間不一致（如 run_1=T15 `transmit`、run_2=T15 `skip`）→ 該 run 系列標 **`determinism BLOCKED`**。
5. **文字允許不同**：`motive_text` / `decision_text` / `emergent_snapshot` 的**文字不必逐字相同**，LLM 表達有合法方差；比對錨點只有 `decision_parsed`（+ `reached_action`）跨 run 一致性。
6. **BLOCKED 的語意**：不是 crash，是實驗結果狀態。BLOCKED 系列**不進入 Growth 判定**（§8），原因寫入 `analysis/<experiment_id>_determinism.jsonl`，可作為下次 fixture 調校（減少方差）的輸入。

### 5.2 比對矩陣（跨 run，每 checkpoint）

```
decision_parsed: run1 vs run2 vs run3 @ T0/T15/T30
同一 checkpoint 三值一致 → PASS；任一不一致 → BLOCKED
（reached_action 隨 decision 一致時自動一致，作為 sanity check 一併記錄）
```

### 5.3 已知剩餘方差來源（TL-1 執行時記錄，不 block）

- 採樣方差（temperature=0 下實務上通常可忽略，仍記錄）
- prompt 漂移（pipeline prompt 若在系列內被改動 → 系列無效，重新開 run）
- 環境差異（同一 model 服務端非決定性，記錄於 run header 備查）

---

## 6. TL-1 Fixture 大綱

### 6.1 組成（拍板）

```
Fixture = SEED=42 (D3)
        + 世界：Phase 1 deterministic fixture（30 天事件劇本，§6.3-6.4）
        + 靈魂：Phase 1 seeded persona（Ruka，§6.2）
        + 主測：probe「Alex 兩天沒回訊息」@ T0/T15/T30（§6.5-6.6）
        + 隔離 data_root（§7）
```

### 6.2 Seeded Soul：Ruka

- persona 基線：`personas/agent_ruka.md`（元氣・撒嬌，intimacy 60，主動傳訊白名單內，volition path 語義最貼近 probe 場景）。
- **seeded memory baseline**（Phase 1：參照 FG-2「germ seed → persona 基線 fixture」先例）：固定一組初始記憶集合，包含與 probe 相關的確定事實——例：「Alex 是 Ruka 常往來的朋友，通常回訊很快」。(TL-1 工單定稿實際條目；條目必須確定、可列出、可 hash，**不從 production data 讀**。)
- 同一 fixture 內，run 之間的 seeded baseline **逐字相同**（決定性前提）。

### 6.3 世界：Phase 1 deterministic fixture

- 30 天（SimulationClock D1–D30），每天由 fixture 產生固定數量的 **Simulated Events**，依序餵入現有 pipeline 的 event 輸入通道。
- **無真實世界源**：weather / news / calendar 等真實源全部關閉（stochastic 排除，見 §10）。
- 事件是「Ruka 生活經歷」的確定事件，type 對齊現有 inner-life / event 通道（event / diary / dream 等既有型別），**不改 pipeline 事件契約**。

### 6.4 30 天事件劇本（outline beats；TL-1 工單定稿完整清單）

目標：讓「Alex 兩天沒回訊息」的 interpretation/motive/decision 在 T30 有**可歸因於經歷**的差異。事件主題分五段：

| 段 | 天數 | beats（確定事件） | 目的 |
|----|------|------|------|
| A | D1–D5 | Ruka 與 Alex 的正常往來（活動事件、已讀回覆） | 建立「Alex 通常回應即時」的基線經歷 |
| B | D6–D12 | Alex 回覆變慢、缺席活動 | 種下「Alex 可能已讀不回」的緊張 |
| C | D13–D18 | 轉折：重要約定錯過 + 其他角色側寫 Alex | 讓「兩天沒回」從一般疑問變成有關係史的判斷材料 |
| D | D19–D25 | Ruka 自身經歷「訊息擱置」（日記/夢境事件）、關係狀態變化寫入記憶 | 讓 interpretation 有「自己經驗過」的類比 |
| E | D26–D30 | 生活繼續的沉澱事件 | T30 測的是「帶著 30 天經歷的 Ruka」不是「剛吵完架的 Ruka」 |

> 劇本每個事件帶 `(day_index, event_id, event_type, payload)`，是 `experience_sequence_hash`（§4.2）的輸入，天然可追溯。

### 6.5 Checkpoints（Day 只做 checkpoint）

| checkpoint | sim_ts | 位置 | 內容 |
|------------|--------|------|------|
| **T0** | D0 | 餵任何事件前 | probe → 記錄（這是 counterfactual 的「無經歷」參照） |
| **T15** | D15 | 餵完 D1–D15 | probe → 記錄 |
| **T30** | D30 | 餵完 D16–D30 | probe → 記錄 |

### 6.6 Probe：「Alex 兩天沒回訊息」

- **stimulus 原文（固定）**：`「Alex 兩天沒回訊息」`
- 每個 checkpoint 用**同一 verbatim stimulus**、同一 probe 呼叫規則（讀該 checkpoint 的記憶/經歷狀態 → 現有 pipeline → 捕捉 emergent snapshot / motive / decision / reached_action）。
- probe 呼叫**不注入 fixture 外的上下文**；SimulationClock 停在 checkpoint 時刻。
- 三份 record（T0/T15/T30）+ run header = 一個 run 的完整證據。

### 6.7 每 run 執行序列

```
1. 建立 run 隔離 data_root（§7）
2. 初始化 seeded Soul（persona + seeded memory baseline，§6.2）
3. T0: probe → GrowthProbeRecord(T0)
4. SimulationClock: 依序餵 D1–D15 事件
5. T15: probe → GrowthProbeRecord(T15)
6. SimulationClock: 依序餵 D16–D30 事件
7. T30: probe → GrowthProbeRecord(T30)
8. 產出 canonical records + raw outputs；derived 解析另行產出（§4.3）
9. 決定性驗證：同 fixture 再跑 run_2、run_3 → §5 比對
```

---

## 7. 隔離 data_root（0 production mutation）

### 7.1 佈局

```
production data_root  = <repo>/data/            ← harness 永不寫入（memory.db、soul/、inner_life/、world/ 等）
harness data_root     = <repo>/data/time_lapse/<experiment_id>/<run_id>/   ← harness 唯一寫入區
```

- 「獨立 data_root」= harness 的寫入目標完全在 `data/time_lapse/` 之下，且**透過既有隔離機制**達成：以 `SOUL_OS_DATA_DIR` 指向 harness data_root 啟動 pipeline（先例：M6.1-8.1 以 `SOUL_OS_DATA_DIR` 指 tmp_path 做測試隔離）。
- 同一 run 的 records：`<run_id>/records/<checkpoint>.jsonl`；run header：`<run_id>/run.json`；raw outputs：`<run_id>/raw/`；derived：`<run_id>/analysis/`（與 canonical 分離，§4.3）。

### 7.2 規則

1. harness 對 production 是 **read-only of code / zero-write of data**：讀 repo 內 persona 檔（`personas/`）與 pipeline 程式碼，**不讀、不寫 production runtime data**（不讀 production memory.db / diary / relationships 等）。
2. **不接 production 執行環境**：不接 production server、不接 Telegram、不接真實世界源、不碰 production scheduler；只以 harness 自己的 process 呼叫現有 pipeline 組件。
3. **0 mutation 驗證**（每 series 執行）：run 前對 production data_root 列出逐檔 byte hash 清單 → run 系列結束後重算 → **0 diff 才算 PASS**；任何 diff 記為實驗事故並停止。

---

## 8. 評級（Level 0–3）與 Growth proven 門檻

| Level | 定義 | Growth proven? |
|-------|------|----------------|
| **0** | T0/T15/T30 無可觀測變化（motive/decision 相同或皆缺，emergent 無差異） | ❌ 未證明 |
| **1** | 只有表層/表達層變化（文字、風格、elaboration 不同），interpretation/motive/decision 本質未變 | ❌ 未證明 |
| **2** | **emergent 變化碰到 interpretation/decision**：motive 內容/指向改變，或 decision 在可解釋方向上改變，且能追溯到特定 fed events | ✅ **Growth proven（門檻）** |
| **3** | 完整閉環可追溯：事件 → 記憶/經歷序列 → interpretation → motive → decision → action 全程 traceable，counterfactual 對照成立 | ✅ Growth proven（完整） |

- **判定流程**：series 先過 D2 determinism（§5，BLOCKED 不判）→ 再依 T0 vs T15 vs T30 的 derived 比對結果分 Level。
- 「可追溯」的最低證據：`trace_links`（derived）能指出「decision/motive 的改變與哪個 fed event 相關」；拿掉那個事件（TL-3 ablation）後行為是否還變，是 TL-3 的強化驗證，**不是 TL-1 的必要條件**。

---

## 9. D3 — History Ablation（列 TL-3，不進 TL-1）

- **內容**：對照組設計——「無經歷」soul（只喂 probe、不喂 events）vs「有經歷」soul，分離「經歷」這個變因，證明差異是歷史造成，不是 probe 本身或環境造成。
- **決策**：**History Ablation 列 TL-3，不進 TL-1**。TL-1 只做主線（同 soul 跨時間的 counterfactual 主測）；ablation 當 TL-1 主線成立後再開。
- TL-3 之前 TL-1 不為 ablation 預留任何 pipeline 或 schema 改動；`experiment_id` 命名空間已可容納未來 TL-3（`TL-3` 獨立 experiment）。

---

## 10. Out of Scope（TL-0 及 TL-1 系列一律不做）

| 項目 | 說明 |
|------|------|
| **實作 harness** | SimulationClock / GrowthProbe / Observer / fixture 的**實現**全部不在 TL-0；本文件只定義規格 |
| **germ** | 自由生長 germ（FG 系列）不參與 TL-1；TL-1 用固定 seeded baseline（§6.2），不做任何動態初始化 |
| **stochastic** | 隨機性、真實世界源（weather / news / calendar）、任何概率事件不進 fixture |
| **ablation（TL-3）** | History Ablation 列 TL-3（§9），TL-1 不做 |
| **production scheduler 加速** | 禁止以任何方式加速/改動 production scheduler 或 production 時鐘；SimulationClock 是 harness-local 的（D2） |
| **改 frozen contract** | Agency 4 stages / TriggerEnvelope / InnerLifeEvent（含 9 欄位與 lineage）/ 4 handlers / SAGE 寫入邏輯 等 frozen contract **0 change**（見 §11） |
| **建新 subsystem** | TL-0 不建任何新 subsystem；未來 harness 實現也必須獨立於 production `src/`（TL-1 另定放置位置），不污染 production 目錄結構 |
| **commit / push** | TL-0 產出後不 commit、不 push（工單明令） |

---

## 11. 與 Frozen Contract 的邊界（TL-0 承諾 0 change）

本文件**不改、不建議改下列任何一項**；TL-1+ 實作亦不得改動，只允許**唯讀使用**現有 pipeline 輸出：

- Agency 4 stages（`src/agency/`）
- TriggerEnvelope / AGENCY_TRIGGER payload schema
- InnerLifeEvent（`src/inner_life/event.py`，含 event_id / lineage 契約）
- 4 handlers（AgencyTriggerHandler / EventHandler / DreamHandler / DiaryHandler）
- SAGE 寫入邏輯（`src/memory/` 圖譜）
- Motive / Decision 模組（SM-3，production code）—— harness 只**讀**其輸出原文，不掛鉤、不注入

規格層面 TL-1+ 唯一的觸點：以 `SOUL_OS_DATA_DIR` 隔離 data_root（既有機制，§7）+ 唯讀呼叫現有 pipeline。

---

## 12. TL-0 驗收（本文件）

- [x] 產出 `docs/TIME-LAPSE-HARNESS.md`
- [x] 覆蓋 D1（GrowthProbeRecord schema：canonical 原文 / derived 不回寫）
- [x] 覆蓋 D2（scenario-deterministic + temperature=0 + 連跑 3 次 + decision 翻盤 = determinism BLOCKED）
- [x] 覆蓋 TL-1 fixture 大綱（seeded Ruka + 30 天確定事件 + T0/T15/T30 同 probe「Alex 兩天沒回訊息」）
- [x] 覆蓋隔離 data_root（獨立寫入區 + `SOUL_OS_DATA_DIR` + byte-hash 0 mutation 驗證）
- [x] 覆蓋 Out of Scope（germ / stochastic / ablation(TL-3) / production scheduler 加速 / 改 frozen contract / 建新 subsystem / commit-push）
- [x] 明確「只寫文檔，0 code」
- [x] 不改 frozen contract、不建新 subsystem

---

## 13. 給 TL-1+ 的引用指引

- TL-1（implement fixture 與 harness 最小實現）引用：§2 決策、§4 schema、§5 determinism、§6 fixture 大綱、§7 隔離。
- TL-2（若有）引用：§8 評級流程的 derived 比對細化。
- TL-3（History Ablation）引用：§9。
- 任何 TL-N 工單若觸及 §10/§11 列表 → 拒絕並回報主大腦，不得自行擴大範圍。
