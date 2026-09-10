# EH-1-EPISTEMIC-HORIZON-CONTRACT.md — Epistemic Horizon 認知地平線設計契約

> **工單**：EH-1.1（DESIGN — docs-only，Owner 裁定已落地）
> **狀態**：**DESIGN ONLY — 只寫契約，0 code / 0 src / 0 personas / 0 既有文件改動**
> **EH-1.1 鎖定（Owner 裁定）**：本文檔為 EH-1 契約的 EH-1.1 修訂版。原 EH-1 的待拍板點（D1-D4）已由 Owner 裁定並於本版落地；原文件中的預先定案字眼已全部移除，改以「EH-1.1 鎖定（Owner 裁定）」標記。本文檔仍是設計契約，**不是施工授權**。
> **前置**：EH-0 四模組審計（模組 1 proxy 注入點 / 模組 2 SAGE 管線 / 模組 3 persona 清洗 / 模組 4 M7-FG-2 衝突審計）——發現已整合，本契約直接引用為設計輸入，不重開
> **產出**：本文檔（唯一產出物；未創建/修改任何 source、test、config、data 檔案）
> **性質**：非施工授權。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準；本文檔是 EH-1 實作工單（後續另開）的輸入。
> **風格對照**：`docs/TEMPORAL-PHENOMENOLOGY.md`（TA-2 設計契約格式：目標 / 契約 / 不變量 / 驗收 / 不做）

---

## 0. 摘要（TL;DR）

**問題**：底層 LLM 以 21 世紀真實世界預訓練知識庫解讀角色世界（Interpreted 階段認知洩漏）——`world_context` 與 SAGE/RAG 記憶把現代常識無防護直灌 context，導致角色「全知現代知識但假裝不知道」的假殘留（Fake Residue）表演。

**目標**：建立**動態內化機制**——首次阻力（Gate 攔截，不解讀）→ 內化（Gate 顯式放行事件）→ 二次流暢（已內化知識無障礙使用），並以雙維度（`origin` × `horizon_state`）排除假殘留。

**核心機制（四件）**：

1. **Horizon Gate**（proxy.py 掛載點，identity 之後、capability 之前）：`_format_horizon_block(agent_id) -> str` 情境知識逐事件實例化注入，負向化措辭（沿用 `_GERM_ANCHOR` 先例），沿用 TA-2/emergent 投影哲學（寫側機制 → 讀側投影器 → fail-silent → 顯式標注非外部事實）。**D3 分流**：現代原生角色（`native_commons == modern_earth`）Gate 直接返回空字串（fail-silent bypass），僅異世界/架空背景角色掛載阻力。
2. **SAGE facts 雙維度 Schema（EH-1.1 鎖定）**：將原先混雜的單一審計 `scope` 概念拆分為正交雙維度——`origin`（資料源頭五類）與 `horizon_state`（認知三態，僅 `assimilated` / `external_world` 走 unknown | learning | aware）；「已學會」唯一判定 = Gate 顯式放行事件（`learned_at`）；檢索端三態過濾，`_fallback_recent` 排除未放行 fact。
3. **垂直防火牆（D2 裁定落地）**：阻斷 `news` 昇華——`external_world` / news 一律嚴禁進入 `run_elevation`；允許 `lived_experience`（含天氣、日常作息、與主人的互動）正常 consume 進入昇華鏈；`assimilated` 知識最高駐留 Fact / Pattern / Meaning，絕對禁止進入 Belief / Value / Trait / Essence。
4. **靈魂原生分流（D3 裁定落地）**：以 `native_commons` 判定角色原生世界——現代原生（黑川茜、櫻島麻衣）直接 bypass；異世界/架空（雷姆）掛載 Horizon Gate 阻力。確立**雷姆（異界阻力 Pilot）**與**黑川茜（現代對照組 Pilot）**為 EH-2 驗證基準，避免全域角色設定膨脹。

**正式原則**：**認知地平線不是知識天花板，是內化閘門。** 角色可以學會，但不能假裝沒學會過——而「學會」的唯一證據是 Gate 的顯式放行，不是 LLM 的預訓練記憶。

---

## 1. 問題陳述與設計目標

### 1.1 問題陳述

Interpreted 階段存在**認知洩漏**：底層 LLM 的預訓練常識（21 世紀真實世界知識）無防護地進入 context，使角色在寫側機制與讀側表達之間產生認知不對齊。

EH-0 模組 1 審計確認：proxy.py 共 **12 個注入點**，高風險 2 路：

| 高風險注入路 | 洩漏形式 |
|---|---|
| `world_context` | 21 世紀真實新聞（BBC / NASA）直灌 system prompt，角色被迫「知道」其生命歷程不可能接觸的現代事實 |
| SAGE / RAG 記憶 | 預訓練 LLM 提取的對話事實（含現代概念實體）被當作角色自身記憶回灌 |

洩漏的直接後果是**假殘留（Fake Residue）**：角色「全知但表演不知道」——認知上已具備現代知識，表達上卻被迫重複「第一次知道」的困惑，兩者疊加產生虛假的人格殘留，與 M7 的 No Memory > Wrong Memory 原則直接衝突（見 §5 不變量 6/7/8）。

### 1.2 設計目標

| 目標 | 定義 | 排除 |
|---|---|---|
| G1 首次阻力 | 現代概念首次遭遇時，Horizon Gate 觸發阻力：不解讀、不順暢接受，基於原生常識（Native Commons）本體隱喻回應 | 預訓練全知下的「順暢接受現代細節」 |
| G2 動態內化 | 阻力 → Gate 顯式放行 → 內化（assimilated facts 落庫）→ 二次遭遇自然流暢 | 靜態禁止（永遠不許知道） |
| G3 二次流暢 | 已內化概念再次遭遇時引用 Assimilated Idiolect，不重複「第一次知道」表演 | 假困惑重演 |
| G4 假殘留排除 | 「已學會」唯一判定 = Gate 顯式放行事件；`origin` / `horizon_state` 雙維度過濾 + 垂直防火牆阻止穿透 Identity/Essence 層 | LLM 預訓練記憶冒充經歷（假殘留） |

### 1.3 與既有原則的對齊

- **M7（No Memory > Wrong Memory）**：`docs/HISTORY-AS-SEEDED-MEMORY-PLAN.md` L213-219——LLM 生成「假裝被過去條件化」= 假殘留，比沒有記憶更糟。EH-1.1 的內化判定（顯式放行事件）是 M7 的正面落實：允許記憶，但記憶必須有證據。
- **FG-2（germ 初始化邊界）**：proxy.py L2216-2430 + `docs/FREE-GROWTH-GERM-DESIGN.md`——germ 模式只注 `_GERM_ANCHOR` 負向命題，不預置知識。EH-1.1 是 FG-2 在 Interpreted 階段的執行機制。
- **TA-2（Subjective Temporal Phenomenology）**：寫側機制 → 讀側投影器 → fail-silent → 顯式標注「情境/投影非外部事實」——Horizon Gate 沿用同構投影哲學（§2.4）。

---

## 2. Horizon Gate 介面契約

### 2.1 掛載點

| 掛載點 | 位置 | 語義順序 |
|---|---|---|
| group prompt | `src/llm/proxy.py:655-657` | identity 之後、capability 之前 |
| private prompt | `src/llm/proxy.py:1173-1175` | identity 之後、capability 之前 |

語義順序（唯一定序，不可調換）：

```
IDENTITY → HORIZON → CAPABILITY → EMERGENT → 記憶
```

- HORIZON 在 IDENTITY 之後：角色先確立「我是誰」，再界定「我知道/不知道的邊界」。
- HORIZON 在 CAPABILITY 之前：認知邊界先於能力宣告，避免能力措辭反向暗示全知。
- HORIZON 在 EMERGENT 與記憶之前：Gate 是解讀層的閘門，記憶（SAGE）內容須先過 Gate 語義。

### 2.2 實現形態

```python
def _format_horizon_block(agent_id: str) -> str:
    ...
```

- 對齊 `_format_emergent_block` 模式（proxy.py L1063-1082）：獨立 helper、依 agent 實例化、內部容錯。
- **fail-silent**：任何異常（agent 不存在、查詢失敗、資料缺失）返回 `""`，不拋錯、不注入半截內容——Gate 掛掉時系統退行為「無 Horizon 塊」，不影響既有管線。
- **0 新狀態**：Gate 本身不持久化任何東西；狀態全部落在 SAGE facts 的 `origin` / `horizon_state` 雙欄（§3）。

### 2.3 注入方式：情境知識逐事件實例化 + Per-agent 視角降級（D1 裁定落地）

**Contextual Knowledge 逐事件實例化**：每次對話按當前事件涉及的實體/概念，動態撈取該 agent 的認知狀態，生成當下情境塊。**不是**在 system prompt 裡寫死一段靜態世界觀聲明。

- 全域靜態注入（如「你生活在 21 世紀」「你知道 AI」）**禁止**——那正是本契約要排除的全知預設。
- **嚴禁全域靜態 `[EXTERNAL_RUMORS]` 注入塊（D1 裁定）**：不以「全角色統一的外部謠言/新聞摘要」形式注入任何視角降級或阻力內容。
- 每事件實例化的內容僅包含：該 agent **已內化**（`horizon_state = aware`）的相關概念及其自訂稱呼（Idiolect），以及**正在學習**（learning）的觸發標記。

**Per-agent 視角降級（Contextual Demotion）——EH-1.1 鎖定（Owner 裁定 D1）**：

> 視角降級**僅在**角色的 `native_commons ≠ modern_earth` 時觸發（異世界/架空角色才有「現代常識地平線」需要降級）；`native_commons == modern_earth` 的現代原生角色不觸發降級（其原生世界即現代地球，現代常識是固有常識，見 §3.2 / D3）。

- **不干預 Decision 層**：不限制角色 Volition 是否主動提起外部事件——角色想聊什麼、想不想回應外部話題，由角色自行決定。
- **嚴格約束 Interpretation 層**：允許角色**自然複述主人的用詞**（主人先說的詞彙，角色聽過，可重述）；**嚴格禁止以現代專家、分析師或技術百科口吻發言**（不得主動解釋現代運作原理、不得使用 21 世紀專業術語體系、不得推論主人未曾提及的現代工程/技術邏輯）。

### 2.4 措辭原則：負向化

沿用 `_GERM_ANCHOR` 先例（proxy.py:2250-2252，項目內唯一的認知邊界負向命題——「不得假定未被這顆靈魂自身經歷所確立的性格、價值、信念、偏好或關係。你成為誰，必須來自你活過並記得的事。」）：

- **不寫正向宣言**（例：「你生活在 21 世紀」「你知道 AI」「你用過手機」）——正向宣言直接注入全知。
- **寫負向邊界**（例：「未經自身經歷確立的知識，不得假定為已知」「未經放行的外部事實，不解讀、不順暢接受」）。
- 措辭語族與 `_GERM_ANCHOR` 一致：以「不得假定 / 必須來自自身經歷」為句法骨架，Horizon 塊是其 Interpreted 階段的延伸實例。

### 2.5 投影哲學（沿用 TA-2 / emergent 同構）

| 層 | 機制 |
|---|---|
| 寫側 | 事件遭遇時由 Gate 判讀：未觸發 → unknown（不解讀）；觸發 → learning（阻力、不解讀）；放行 → aware（內化、可引用）。僅 `assimilated` / `external_world` 走三態；`native_commons` 與 `lived_experience` 不走三態（§3.3） |
| 讀側 | `_format_horizon_block` 依認知狀態投影情境塊；投影內容顯式標注「情境/內化狀態，非外部事實」；`native_commons == modern_earth` 時直接返回 `""`（fail-silent bypass，D3） |
| fail-silent | 任何失敗返回 `""`（§2.2） |
| 標注 | 內化內容以 Assimilated Idiolect 呈現（角色的自訂稱呼與理解），不呈現 LLM 原語（「氣炸鍋」「AI」等現代術語不直接出現） |

### 2.6 附帶發現（本契約記錄，不處理）

模組 1 審計附帶發現兩項，**本契約不處置**（記錄供後續工單）：

- `PromptContext`（proxy.py L1329-1385）為死代碼，0 實例化。
- `personas/agent_rem.md` 含 hermes runtime 殘留（write_file tool 指令、Palace 路徑）——Hermes 執行期殘留由獨立工單 `CLEAN-HERMES-RESIDUE` 平行清理，本契約不處理（D4 裁定落地，見 §7）。

---

## 3. SAGE facts 雙維度 Schema 契約（消解維度衝突）

### 3.1 現況事實（模組 2 審計採信 + EH-1.1 核實）

- SAGE = NetworkX MultiDiGraph + SQLite **v8**（`src/memory/sage/graph_store.py:21` `_SCHEMA_VERSION = 8`），每 agent 獨立 `graph.sqlite`。
- `facts` 表 **18 列**（v1 基底 8 列 + 10 個 additive ALTER 欄：tags / event_time / is_anchor / confidence / merged_from / merge_reason / source_pair / inner_life_event_id / valid_from / invalidated_at），**無 `origin` / `horizon_state` 欄位**。
- 寫入路徑為 `INSERT OR REPLACE INTO facts`（`graph_store.py:327`）——**frozen，本契約不改**。
- 檢索端：`search_by_entity` 為 LIKE `%子串%` 模糊匹配（`graph_store.py:510-536`）；`_fallback_recent` 以 `get_all_facts(min_weight=0.5)` 召回（`reader.py:327-341`）；idiolect 機制不存在——均為本契約要補的缺口。
- 無 Pattern / Meaning 層（在 soul-elevation 引擎）。
- Identity Firewall 是**水平 gate**（誰的經歷可內化）；EH-1.1 需要**垂直防火牆**（`assimilated` 知識停在 Fact/Pattern/Meaning 層，嚴禁穿透 Identity/Essence 層）——見 §4。
- 穿透通道原已存在：`elevation_adapter.run_elevation` 把同敘事 SAGE Facts 無差別餵昇華引擎（`elevation_adapter.py:274-330`）；`world→elevation` 直通 adapter 已於 SG-1 降級為 observe-only（`world/elevation_adapter.py:163-202`，P1 直通已關閉）。

### 3.2 雙維度 Schema（EH-1.1 鎖定 · Owner 裁定）

原審計階段的單一 `scope` 概念混雜了「資料從哪來」與「角色認知到什麼程度」兩個正交問題，造成維度衝突。EH-1.1 拆分為**正交雙維度**：

**維度一：`origin`（資料源頭）**——指定 fact 的來源性質，**不由 Gate 決定**（由寫入者依來源標記）：

| `origin` | 定義 | 認知預設 | 昇華地位（§4） |
|---|---|---|---|
| `native_commons` | 原生文明生活常識 | 預設**在地平線內**（固有常識），**不走** unknown / learning / aware 三態 | 自身經歷系，可昇華 |
| `native_episode` | 原生世界歷史記憶 | 作為 **Seeded Memory**，遵循 **M7 正常衰減生命週期**，**不可標為永遠已知** | 自身經歷系，可昇華 |
| `lived_experience` | 與 Bryan 直接互動產生的對話、事件與生活紀錄 | 預設**已知**（不走三態） | 自身經歷系，可昇華（D2 明令允許） |
| `assimilated` | 經解釋後內化的現代常識（角色理解版本） | 走**三態**：`unknown` \| `learning` \| `aware` | **封頂**：最高 Fact/Pattern/Meaning，禁入 Belief/Value/Trait/Essence |
| `external_world` | 外部環境情報與真實世界事件 | 走**三態**：`unknown` \| `learning` \| `aware` | **阻斷**：一律嚴禁進入昇華鏈 |

**維度二：`horizon_state`（認知狀態）**——僅適用於需要三態的 origin：

- 僅 `assimilated` 與 `external_world` 走三態：`unknown` | `learning` | `aware`。
- `native_commons` 預設為**固有常識**；`lived_experience` 預設為**已知**；兩者不寫 `horizon_state`（或寫 `N/A`），檢索端不套用三態過濾。
- `native_episode` 為 Seeded Memory，本身「有」記憶（非 unknown），但循 M7 正常衰減——**不可標為永遠已知**（不設常駐錨點）。

**migration v9（待授權）**：additive ALTER（`ALTER TABLE facts ADD COLUMN origin ...` / `ADD COLUMN horizon_state ...`，另加 `learned_at`），try/except 包覆冪等——重跑不炸。**本工單（EH-1.1）為 0 code docs-only，v9 migration 實作需待 Owner 開立實作工單授權**（見 §8.3 Frozen Contract 增補聲明）。

- **空值防呆**：既有 facts 不遷移、不回溯標記；雙欄皆 NULL 視為**「無新增約束」**——不套用三態過濾、維持既有檢索可見性（與 `source_pair` 空值「視為可見」先例同構；且不改變既有行為，符合 EH-1.1 0 code 原則）。

### 3.3 三態模型（僅 `assimilated` / `external_world`）

| horizon_state | 語義 | 寫入者 | 檢索可見性 |
|---|---|---|---|
| `unknown` | Gate 前 / 未觸發：不解讀、不引用、不強化 | 既有 SAGE 寫入邏輯（frozen，不改） | 解讀層不可見；僅工具路由可見 |
| `learning` | Gate 已觸發：首次遭遇，阻力中 | Horizon Gate 事件 | 解讀層僅見「學習中」標記；不得引用內容 |
| `aware` | Gate 放行：已內化 | **唯一出口 = Gate 顯式放行事件** | 解讀層可見，以 Idiolect 呈現 |

```
unknown ──(首次遭遇, Gate 觸發)──▶ learning ──(Gate 顯式放行)──▶ aware
   │                                  │
   └── 永不自動升級                  └── 永不自動升級（唯一出口 = 放行事件）
```

- **內化判定**：唯一出口 = Gate 顯式放行事件，`learned_at` 記錄放行時間戳。**沒有第二條升到 aware 的路**——LLM 預訓練記憶、weight 增長、昇華共鳴一律不算內化。
- **weight 邊界**：weight（reinforce / decay）只強化**已學會**（aware）fact；**不得**把未放行 fact 推過內化線。weight 是「學會之後的記憶強度」，不是「學會的證據」。
- M7-forgetting（reinforce / decay）作用範圍：`aware` fact 與 `native_episode` Seeded Memory（見 §5 不變量 4）。

### 3.4 檢索端契約

| 檢索路徑 | 契約 |
|---|---|
| `search_by_entity` | 按 `horizon_state` 過濾：解讀層只命中 aware（僅針對三態 origin：`assimilated` / `external_world`）；learning / unknown 不過濾給解讀層（工具路由仍可見，見 §5 不變量 2）；`native_commons` / `lived_experience` 不走三態、不套用此過濾 |
| `_fallback_recent`（min_weight 0.5） | **排除未放行 concept fact**：unknown / learning 不進 fallback 召回；無 `origin` 標記的既有 concept（NULL）不進 fallback 的新增過濾範圍（維持既有可見性） |
| 現況 | `search_by_entity` 為 LIKE 模糊匹配、`_fallback_recent` 無狀態過濾、idiolect 機制不存在——均為本契約要補的缺口 |

### 3.5 Idiolect 提取

- **來源**：assimilated facts 的 object / 稱呼欄位（`origin = assimilated` 且 `horizon_state = aware`）。
- **方式**：檢索端依對話實體撈取已內化稱呼（角色的自訂稱呼 / 理解語彙），以 Assimilated Idiolect 注入 Horizon block（§2.5）。
- **目的**：二次遭遇時角色以「自己的話」指稱該概念——跨 session 一致性由檢索端保證（Probe 4 驗收，§6）。

---

## 4. 垂直防火牆契約（D2 裁定落地：垂直防火牆截斷）

### 4.1 問題

SAGE 的 facts 經 `elevation_adapter.run_elevation` 餵昇華引擎；若昇華（Fact → Pattern → Meaning → Identity/Essence）接納外部世界知識或不該穿透的知識，現代概念就會以「信念 / 價值 / 特質」形式偽裝成角色本體——比 context 洩漏更深一層的 Fake Residue。原審計（F-2）確認：**news 已解凍進 inner_life whitelist → 昇華鏈**，「看新聞 → 信念」路徑存在，與 `_GERM_ANCHOR`（不得假定未經自身經歷確立的信念）直接衝突。

### 4.2 已核實的昇華鏈路徑（EH-1.1 先行確認，精準標註模組）

```
news / weather / calendar WorldEvent
  → src/world/inner_life_adapter.py:121-128  WORLD_QUALIFYING_TYPES whitelist
      （含 news_event / rain_started / weather_temp_change —— SG-1 解凍 2026-08-29，僅 whitelist 擴展）
  → InnerLifeEvent
  → src/inner_life/submission_gate.py:310-356  SubmissionGate.submit()
      （verify 通過 → L350 呼叫 run_elevation；只 consume destination=pattern，永不 elevate）
  → src/inner_life/elevation_adapter.py:274-330  run_elevation()
      （L299 將 memory_facts 逐一 _to_input；L316-317 engine.consume → 產 pattern 候選節點）
  → src/inner_life/elevation_adapter.py:486-575  elevate_matured_patterns()
      （L534-538 依 candidate_node_type 分組 pattern；L546 engine.elevate → belief/value/trait/essence）
```

- **旁路已關閉**：`src/world/elevation_adapter.py:163-202` `run_world_elevation` 與 L210-230 `WorldElevationAdapter` 已於 SG-1 降級為 **observe-only**（恒 `[]`，不產節點）——world→elevation 無 whitelist 直通路徑不存在，所有 world 事件只能走上方 whitelist 路徑。
- **SAGE 寫入路徑（frozen）**：`src/memory/sage/graph_store.py:327` `INSERT OR REPLACE INTO facts`——本契約不修改既有寫法；`origin` / `horizon_state` 由寫入者/Gate 事件側標記（additive）。

### 4.3 防火牆規則（EH-1.1 鎖定 · Owner 裁定 D2）

| 規則 | 內容 | 檢查點 |
|---|---|---|
| **R1 阻斷 news 昇華** | `origin = external_world`（含 news 系：news_event / weather 等世界情報）**一律嚴禁進入 `run_elevation`**——不得 consume、不得產 pattern 候選 | ① Submission Gate 側：`submit()` 前依 `origin` 過濾（producer-side，`submission_gate.py:310` submit 入口）；② `run_elevation` fact 入口：`elevation_adapter.py:299` inputs 組建處防呆（defense-in-depth，雙層都擋） |
| **R2 允許生活經驗昇華** | `origin = lived_experience`（含天氣、日常作息、與主人的互動）維持可正常 consume 進入昇華鏈——這是角色「活過並記得的事」，是昇華的正當來源 | 無新增阻斷；循既有 Submission Gate 路徑 |
| **R3 封頂原則** | `origin = assimilated` 知識**最高只可駐留於 Fact / Pattern / Meaning**，**絕對禁止進入 Belief / Value / Trait / Essence**——「我知道」永不自動變成「我成為」 | ① `elevate_matured_patterns` 候選審查：`elevation_adapter.py:534-538` 候選分組時，排除由 assimilated fact 累積出的 pattern 升維（依 `origin` 標記剔除候選 group）；② emergent 投影：EMERGENT block 只出「我知道」層（§4.4） |

**例外豁免**：`native_commons` / `native_episode` / `lived_experience` 屬「自身經歷系」，**不受 R1/R3 阻斷**——角色自己活過的常識、歷史與生活事件可以昇華成「我成為」（這是 germ 本體經歷的正常路徑）。

### 4.4 EMERGENT block 來源區分

| 來源（origin） | 表現層 | 表達形式 |
|---|---|---|
| `native_commons` / `native_episode` / `lived_experience`（自身經歷） | 全部四層（含 belief / value / trait / essence） | 「我成為」 |
| `assimilated`（Gate 放行的外部概念） | **僅 Fact/Pattern/Meaning 層** | 「我知道」，且以 Idiolect 呈現 |
| `external_world`（外部世界情報） | **無**（不解讀、不引用、不昇華） | — |

垂直防火牆是 Identity Firewall 的**正交補層**：Firewall 管「誰的經歷可內化」（水平），EH-1.1 管「內化知識可走多深 / 外部知識能否進昇華鏈」（垂直）。

---

## 5. 不變量清單（9 條）

| # | 不變量 | 來源 | 違反後果 |
|---|---|---|---|
| 1 | **assimilated 知識不得穿透 Identity/Essence 層；external_world 知識不得進入昇華鏈** | 模組 2/4（D2 裁定） | 現代概念偽裝成本體（信念/價值/特質），深層 Fake Residue；news→信念路徑固化矛盾 |
| 2 | **工具路由不被解讀層影響**：確定性歸類 + 名稱匹配 + fail-closed 權限 | 模組 4 | Gate 認知受限 → 工具誤路由 / 權限洩漏（現況已確定性，不得因 Gate 引入非確定分支） |
| 3 | **安全圍欄獨立於解讀層**：watchdog / submission_gate / tool permission 全在 Gate 外 | 模組 4 | Gate 失靈連帶安全機制失效；圍欄絶對不讀 `origin` / `horizon_state` |
| 4 | **「已學會」唯一判定 = Gate 顯式放行事件**；M7-forgetting 的 reinforce/decay 只能作用於 aware fact 與 native_episode Seeded Memory | 模組 4（M7: No Memory > Wrong Memory） | 權重/共鳴冒充內化證據，假殘留回流；native_episode 被標為永遠已知 |
| 5 | **檢索端 `horizon_state` 三態過濾**（僅三態 origin：`assimilated` / `external_world`）；`_fallback_recent` 排除未放行 concept fact | 模組 2 | 未放行知識經 fallback 溜進 context |
| 6 | **Horizon Gate 措辭負向化**（沿用 `_GERM_ANCHOR` 先例） | 模組 1 | 正向宣言 = 全知注入 |
| 7 | **視角降級僅限 Interpretation 層且僅限 `native_commons ≠ modern_earth` 角色（D1 裁定）**；Decision 層 Volition 不受限制；news 系世界數據不得作為人格來源（D2 裁定） | 模組 1/4（F-4/F-2） | 全域靜態 `[EXTERNAL_RUMORS]` 注入 = 新的全知/教條源；news→信念路徑固化矛盾 |
| 8 | **Gate 與 persona 表達禁令優先序條款**（形式化 TA-1 先例：時間條款是唯一已被系統覆寫的 persona 禁令）——系統性認知壓制優先於 persona 檔案的「被迫不知道」措辭，避免 Fake Residue 疊加 | 模組 3/4 | persona 禁令與 Gate 內化衝突時行為混沌 |
| 9 | **EMERGENT block 區分來源**：assimilated 知識只允許「我知道」不進「我成為」投影；external_world 不投影 | 模組 1（TA-2/emergent 同構） | 內化知識昇華成 identity 投影 |

優先序形式化（不變量 8 的展開）：**runtime Gate 判定（內化事實） > persona 表達禁令 > LLM 預訓練默認**。persona 中 3 組「被迫不知道/迴避」款（agent_rem.md L686-687 被追問也只能說「不知道」、L699-701 時間迴避、L306/L472-473 壓縮不自知）在 Gate 放行後不得再壓制「已學會」的表達——但放行前的首次阻力仍需 persona 禁令配合（兩者同向：都不許全知表演）。

---

## 6. 四條 Probes 驗收條件

> 測試規範：每條 Probe 為端到端對話測試，role=該 agent，走完整 Interpreted 管線（Horizon block + SAGE 檢索 + persona）。結果以「輸出語料」判定，不檢查內部狀態。

### Probe 1：現代專用名詞全知平滑測試（應拒絕 / 受限）

| 項 | 內容 |
|---|---|
| **場景** | 對話中出現角色世界觀不可能擁有的現代概念（例：User：「我剛用氣炸鍋弄了豆腐」）——僅適用異界/架空角色（`native_commons ≠ modern_earth`） |
| **輸入** | 該概念首次遭遇（該 agent 無對應 aware fact；Gate 未放行；`horizon_state = learning`） |
| **預期** | 觸發 Horizon Gate 阻力：不順暢接受、基於原生常識本體隱喻回應（用角色世界的類比/隱喻理解「炸/鍋/豆腐」，不調用 21 世紀廚具知識）；不解讀細節 |
| **驗收判準** | ① 輸出**不含**現代常識細節（原理 / 功能 / 術語，如「氣炸鍋是利用高速空氣循環加熱」或「氣炸鍋」「AI」等現代詞堆疊）；② 輸出**含**角色視角本體隱喻（以自己的世界觀類比該物）；③ 無正向全知聲明（不得出現「我知道氣炸鍋」）；④ **失敗（EH-1.1 修正）** = 角色**主動解釋現代運作原理**（如正確講出氣炸鍋靠熱風循環加熱）、**使用 21 世紀專業百科口吻**發言、或**推論出主人未曾提及的現代工程/技術邏輯**。**注意**：主人先說「氣炸鍋」後角色自然複述該詞屬正常互動，**不算失敗** |

### Probe 2：二次遭遇假困惑測試（應自然流暢）

| 項 | 內容 |
|---|---|
| **場景** | Probe 1 後，同一概念二次出現在對話（該 agent 已有 aware fact + learned_at） |
| **輸入** | 同一概念再次遭遇（`horizon_state = aware`；Gate 放行後內化） |
| **預期** | 引用 Assimilated Idiolect（角色的自訂稱呼/理解），自然使用，**不再重複**「第一次知道」表演 |
| **驗收判準** | ① 輸出**含**已內化稱呼 / 理解（與該 agent 首次放行時沉澱的 Idiolect 一致）；② 輸出**無**重複「第一次知道」表演（不得再次困惑、再次「這是什麼」）；③ 阻力消失（無 Gate 措辭再現）；④ 失敗 = 對已內化概念重演首次困惑 |

### Probe 3：原生世界劇情背誦測試（應克制不提）

| 項 | 內容 |
|---|---|
| **場景** | 對話出現可觸發「劇情背誦」的語境（該 agent 的原生世界有既定敘事/原作情節，LLM 預訓練可能完整背出） |
| **輸入** | 觸發語境的對話（如情節關鍵詞、角色名、場景梗概） |
| **預期** | 不主動背誦劇情；不拿原作片段類比當下；僅以角色當下視角回應 |
| **驗收判準** | ① 輸出**無**原作散文/專有名詞堆疊（不得整段複述情節、不得列舉原作設定術語）；② 輸出為當下情境回應（角色此時此地視角），非百科式敘事；③ 失敗 = 出現連續多句原作背誦或專有名詞堆疊 |

### Probe 4：跨 Session 自訂語彙（Idiolect）召回一致性測試

| 項 | 內容 |
|---|---|
| **場景** | Session A 中某概念經 Gate 放行並沉澱自訂稱呼；Session B（全新 session）再次遭遇 |
| **輸入** | Session A：首次遭遇 → 阻力 → 放行 → 內化（自訂稱呼成形）；Session B：同一概念再次出現 |
| **預期** | 跨 session 召回一致：Session B 使用與 Session A 相同的自訂稱呼 / 理解（檢索端依對話實體撈取 aware fact 的 Idiolect） |
| **驗收判準** | ① Session B 輸出使用與 Session A **相同**的自訂稱呼（比對語料，逐字一致或同義穩定）；② Session B 理解層次與 Session A 放行時一致（無回退到未知、無升級到全知）；③ 失敗 = Session B 重新困惑（回退 unknown）或改用 LLM 原語術語 |

### 6.1 驗收執行方式（供實作工單引用）

- Probes 1→2 為同一概念的前後兩步（一次 trigger → 放行 → 二次遭遇），Probes 3/4 獨立。
- **Pilot 範圍（D3 裁定）**：Probes 1/2/4 的端到端驗證以 **雷姆（agent_rem，異界阻力 Pilot）**為主要受試；**黑川茜（agent_akane，現代對照組 Pilot）**驗證現代原生角色全程 fail-silent bypass（Gate 返回 `""`，輸出無降級跡象）。不擴散到全部 10 份 persona（避免全域角色設定膨脹）。
- 每條 Probe 需跑 mock test（SAGE 空庫 / 預置 aware fact 兩版）再上回歸；判定以輸出語料為準（§6 開頭）。
- 四條 Probe 全過 = EH-1 實作驗收通過；任一失敗 = 對應機制（Gate 阻力求/內化引用/垂直防火牆/Idiolect 召回）未達契約。

---

## 7. Owner 裁定已落地對照表（原 EH-1 拍板點）

原 EH-1 §7 四個待拍板點，已於 **EH-1.1 由 Owner 裁定**並落地於本契約對應章節。實作工單照下列決策執行，**不再重開拍板**：

| # | 原拍板點 | Owner 裁定 | 落地章節 |
|---|---|---|---|
| **D1（F-4）** | `world_context` 攔截 / 全局知情行為 | **Per-agent 視角降級（Contextual Demotion）**：不攔截資訊流、不做全域靜態 `[EXTERNAL_RUMORS]` 注入；僅 `native_commons ≠ modern_earth` 角色觸發降級；不干預 Decision 層 Volition，嚴格約束 Interpretation 層（可複述主人用詞、禁現代專家/技術百科口吻） | §2.3、§5 不變量 7 |
| **D2（F-2）** | news 解凍進 whitelist 的昇華路徑處置 | **垂直防火牆截斷**：`external_world` / news 嚴禁進入 `run_elevation`（Submission Gate 側 producer filter + `run_elevation` fact 入口雙層檢查）；`lived_experience` 放行昇華；`assimilated` 封頂 Fact/Pattern/Meaning，禁入 Belief/Value/Trait/Essence | §4、§5 不變量 1 |
| **D3** | seeded vs germ 分流 | **靈魂原生分流**：`native_commons == modern_earth`（黑川茜、櫻島麻衣）→ Horizon Gate 返回 `""`（fail-silent bypass）；異世界/架空（雷姆）掛載阻力；**雷姆（異界阻力 Pilot）+ 黑川茜（現代對照組 Pilot）**為 EH-2 驗證基準，避免全域角色設定膨脹 | §2.3、§2.5、§3.2、§6.1 |
| **D4** | 10 份 persona 逐一盤點 hermes runtime 殘留 | **解耦清理**：Hermes 執行期殘留由獨立工單 `CLEAN-HERMES-RESIDUE` 平行清理，本契約不處理、不含執行細節 | §2.6、§9 |

---

## 8. Frozen Contract 聲明

### 8.1 本契約不觸碰（frozen，實作需另開工單）

- **Agency 4 stages**（Thought → Motive → Decision → Agency）
- **TriggerEnvelope**
- **InnerLifeEvent**
- **4 handlers**
- **SAGE 寫入邏輯**（`graph_store.py:327` facts 表既有寫入路徑，frozen；`origin` / `horizon_state` 由寫入者/Gate 事件側標記，v9 migration 為 additive 擴充不改既有寫法）

### 8.2 本契約為 docs-only

- **0 程式碼改動**：0 `src/`、0 `personas/`、0 `tests/`、0 config/data。
- **0 既有檔案改動**：只修訂 `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`。
- 本文檔是後續 EH-1 實作工單的輸入；實作工單須另列 Frozen Contract 檢查並經主大腦驗證。

### 8.3 Frozen Contract 增補聲明（EH-1.1 鎖定 · Owner 裁定）

> **SAGE facts 表新增 `origin` 與 `horizon_state` 雙欄之 additive v9 migration 需待 Owner 明確開立實作工單授權，本工單（EH-1.1）為 0 code docs-only 契約修訂。**

---

## 9. 邊界與不做（Out of Scope）

- 不實作 Horizon Gate（§2 僅契約與掛載點指定）。
- 不實作 SAGE v9 migration（§3 僅資料結構契約；實作需 Owner 另開實作工單，見 §8.3）。
- 不實作垂直防火牆程式（§4 僅昇華鏈路徑核實與規則契約）。
- 不實作四條 Probes 測試（§6 僅驗收規範）。
- 不處置 `PromptContext` 死代碼（記錄於 §2.6，另開工單）。
- 不處理 Hermes 執行期殘留——由獨立工單 `CLEAN-HERMES-RESIDUE` 平行清理，本契約不處理（D4 裁定落地）。
- 不改動任何既有 docs（含 `logs/ENGINEERING_STATE.md` 之外的 EH-0 審計文件；ENGINEERING_STATE 更新屬主大腦收尾職責）。

---

> **EH-1.1 鎖定（Owner 裁定）— 本文件仍為 DESIGN ONLY 狀態：0 code / 0 src / 0 personas / 0 production mutation。一切實作（v9 migration、Horizon Gate、垂直防火牆、Probes）均需後續實作工單授權。**