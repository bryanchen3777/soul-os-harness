# EH-1-EPISTEMIC-HORIZON-CONTRACT.md — Epistemic Horizon 認知地平線設計契約

> **工單**：EH-1（DESIGN — docs-only）
> **狀態**：**DESIGN ONLY — 只寫契約，0 code / 0 src / 0 personas / 0 既有文件改動**
> **前置**：EH-0 四模組審計（模組 1 proxy 注入點 / 模組 2 SAGE 管線 / 模組 3 persona 清洗 / 模組 4 M7-FG-2 衝突審計）——發現已整合，本契約直接引用為設計輸入，不重開
> **產出**：本文檔（唯一產出物；未創建/修改任何 source、test、config、data 檔案）
> **性質**：非施工授權。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準；本文檔是 EH-1 實作工單（後續另開）的輸入。
> **風格對照**：`docs/TEMPORAL-PHENOMENOLOGY.md`（TA-2 設計契約格式：目標 / 契約 / 不變量 / 驗收 / 不做）

---

## 0. 摘要（TL;DR）

**問題**：底層 LLM 以 21 世紀真實世界預訓練知識庫解讀角色世界（Interpreted 階段認知洩漏）——`world_context` 與 SAGE/RAG 記憶把現代常識無防護直灌 context，導致角色「全知現代知識但假裝不知道」的假殘留（Fake Residue）表演。

**目標**：建立**動態內化機制**——首次阻力（Gate 攔截，不解讀）→ 內化（Gate 顯式放行事件）→ 二次流暢（已內化知識無障礙使用），並以三態（unknown / learning / aware）排除假殘留。

**核心機制（三件）**：

1. **Horizon Gate**（proxy.py 掛載點，identity 之後、capability 之前）：`_format_horizon_block(agent_id) -> str` 情境知識逐事件實例化注入，負向化措辭（沿用 `_GERM_ANCHOR` 先例），沿用 TA-2/emergent 投影哲學（寫側機制 → 讀側投影器 → fail-silent → 顯式標注非外部事實）。
2. **SAGE assimilated_facts 資料結構**（方案 A）：facts 表加 `scope` 列（v9 migration，additive ALTER 冪等），三態模型（unknown / learning / aware）；「已學會」唯一判定 = Gate 顯式放行事件（`learned_at`）；檢索端三態過濾，`_fallback_recent` 排除未放行 fact。
3. **垂直 gate**（assimilated 知識不得穿透 Identity/Essence 層）：三層檢查（run_elevation fact 入口 / elevate_matured_patterns 候選審查 / emergent 投影）；assimilated 知識只允許「我知道」，不進「我成為」投影。

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
| G2 動態內化 | 阻力 → Gate 顯式放行 → 內化（assimilated_facts 落庫）→ 二次遭遇自然流暢 | 靜態禁止（永遠不許知道） |
| G3 二次流暢 | 已內化概念再次遭遇時引用 Assimilated Idiolect，不重複「第一次知道」表演 | 假困惑重演 |
| G4 假殘留排除 | 「已學會」唯一判定 = Gate 顯式放行事件；三態過濾 + 垂直 gate 阻止穿透 Identity/Essence 層 | LLM 預訓練記憶冒充經歷（假殘留） |

### 1.3 與既有原則的對齊

- **M7（No Memory > Wrong Memory）**：`docs/HISTORY-AS-SEEDED-MEMORY-PLAN.md` L213-219——LLM 生成「假裝被過去條件化」= 假殘留，比沒有記憶更糟。EH-1 的內化判定（顯式放行事件）是 M7 的正面落實：允許記憶，但記憶必須有證據。
- **FG-2（germ 初始化邊界）**：proxy.py L2216-2430 + `docs/FREE-GROWTH-GERM-DESIGN.md`——germ 模式只注 `_GERM_ANCHOR` 負向命題，不預置知識。EH-1 是 FG-2 在 Interpreted 階段的執行機制。
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
- **0 新狀態**：Gate 本身不持久化任何東西；狀態全部落在 SAGE `assimilated_facts`（§3）。

### 2.3 注入方式：情境知識逐事件實例化，非全域靜態

- **Contextual Knowledge 逐事件實例化**：每次對話按當前事件涉及的實體/概念，動態撈取該 agent 的 assimilated 狀態，生成當下情境塊。**不是**在 system prompt 裡寫死一段靜態世界觀聲明。
- 全域靜態注入（如「你生活在 21 世紀」「你知道 AI」）**禁止**——那正是本契約要排除的全知預設。
- 每事件實例化的內容僅包含：該 agent **已內化**（aware）的相關概念及其自訂稱呼（Idiolect），以及**正在學習**（learning）的觸發標記。

### 2.4 措辭原則：負向化

沿用 `_GERM_ANCHOR` 先例（proxy.py:2250-2252，項目內唯一的認知邊界負向命題——「不得假定未被這顆靈魂自身經歷所確立的性格、價值、信念、偏好或關係。你成為誰，必須來自你活過並記得的事。」）：

- **不寫正向宣言**（例：「你生活在 21 世紀」「你知道 AI」「你用過手機」）——正向宣言直接注入全知。
- **寫負向邊界**（例：「未經自身經歷確立的知識，不得假定為已知」「未經放行的外部事實，不解讀、不順暢接受」）。
- 措辭語族與 `_GERM_ANCHOR` 一致：以「不得假定 / 必須來自自身經歷」為句法骨架，Horizon 塊是其 Interpreted 階段的延伸實例。

### 2.5 投影哲學（沿用 TA-2 / emergent 同構）

| 層 | 機制 |
|---|---|
| 寫側 | 事件遭遇時由 Gate 判讀：未觸發 → unknown（不解讀）；觸發 → learning（阻力、不解讀）；放行 → aware（內化、可引用） |
| 讀側 | `_format_horizon_block` 依三態投影情境塊；投影內容顯式標注「情境/內化狀態，非外部事實」 |
| fail-silent | 任何失敗返回 `""`（§2.2） |
| 標注 | 內化內容以 Assimilated Idiolect 呈現（角色的自訂稱呼與理解），不呈現 LLM 原語（「氣炸鍋」「AI」等現代術語不直接出現） |

### 2.6 附帶發現（本契約記錄，不處理）

模組 1 審計附帶發現兩項，**本契約不處置**（記錄供後續工單）：

- `PromptContext`（proxy.py L1329-1385）為死代碼，0 實例化。
- `personas/agent_rem.md` 含 hermes runtime 殘留（write_file tool 指令、Palace 路徑）——處置權責見 §7 拍板點 D4。

---

## 3. SAGE assimilated_facts 資料結構契約

### 3.1 現況事實（模組 2 審計採信）

- SAGE = NetworkX MultiDiGraph + SQLite **v8**，每 agent 獨立 `graph.sqlite`。
- `facts` 表 **18 列**，無 namespace / scope 欄位。
- 無 Pattern / Meaning 層（在 soul-elevation 引擎）。
- Identity Firewall 是**水平 gate**（誰的經歷可內化）；EH-0 需要**垂直 gate**（assimilated 知識停在 Fact/Pattern/Meaning 層，嚴禁穿透 Identity/Essence 層）——見 §4。
- 穿透通道已存在：`elevation_adapter.run_elevation` 把同敘事 SAGE Facts 無差別餵昇華引擎。

### 3.2 方案 A：facts 表加 `scope` 列（已拍板）

- **migration v9**：additive ALTER（`ALTER TABLE facts ADD COLUMN scope ...`），try/except 包覆冪等——重跑不炸。
- **空值防呆**：既有 facts 不遷移、不回溯標記，`scope IS NULL` 視為 **unknown**（Gate 前不解讀）；與 `source_pair` / `inner_life_event_id` / `valid_from` 三次先例同構（additive、冪等、防呆）。

### 3.3 三態模型

| scope | 語義 | 寫入者 | 檢索可見性 |
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
- M7-forgetting（reinforce / decay）只能作用於 aware fact（見 §5 不變量 4）。

### 3.4 檢索端契約

| 檢索路徑 | 契約 |
|---|---|
| `search_by_entity` | 按三態過濾：解讀層只命中 aware；learning / unknown 不過濾給解讀層（工具路由仍可見，見 §5 不變量 2） |
| `_fallback_recent`（min_weight 0.5） | **排除未放行 concept fact**：unknown / learning 不進 fallback 召回；現有無 scope 概念（NULL）不進 fallback |
| 現況 | `search_by_entity` 為 LIKE `%子串%` 模糊匹配、`_fallback_recent` 無 scope 概念、idiolect 機制不存在——均為本契約要補的缺口 |

### 3.5 Idiolect 提取

- **來源**：assimilated facts 的 object / 稱呼欄位（aware 級）。
- **方式**：檢索端依對話實體撈取已內化稱呼（角色的自訂稱呼 / 理解語彙），以 Assimilated Idiolect 注入 Horizon block（§2.5）。
- **目的**：二次遭遇時角色以「自己的話」指稱該概念——跨 session 一致性由檢索端保證（Probe 4 驗收，§6）。

---

## 4. 垂直 gate 契約（assimilated 知識不得穿透 Identity / Essence 層）

### 4.1 問題

SAGE 的 assimilated Facts 經 `elevation_adapter.run_elevation` 無差別餵昇華引擎；若昇華（Fact → Pattern → Meaning → Identity/Essence）接納未放行或不該穿透的知識，現代概念就會以「信念 / 價值 / 特質」形式偽裝成角色本體——比 context 洩漏更深一層的 Fake Residue。

### 4.2 三層檢查

| 層 | 檢查點 | 契約 |
|---|---|---|
| **fact 層** | `run_elevation` fact 入口 | fact 級 scope 檢查：只有 Gate 特色（scope 非 NULL）的 fact 走昇華；**未放行 fact（unknown / learning）不得進入昇華鏈** |
| **pattern 層** | `elevate_matured_patterns` 候選審查 | pattern 級審查：由未放行 fact 累積出的候選 Pattern 不成立（三態過濾在昇華前完成，不讓 learning 殘留沉澱為 Pattern） |
| **投影層** | emergent 投影（EMERGENT block） | 投影級區分來源：assimilated 知識只允許 Fact/Pattern/Meaning 層表現（「我知道」），不進「我成為」（belief / value / trait / essence）投影 |

### 4.3 EMERGENT block 來源區分

| 來源 | 表現層 | 表達形式 |
|---|---|---|
| germ 本體經歷（自身事件） | 全部四層（含 belief / value / trait / essence） | 「我成為」 |
| assimilated 知識（Gate 放行的外部概念） | **僅 Fact/Pattern/Meaning 層** | 「我知道」，且以 Idiolect 呈現 |
| assimilated 知識（未放行） | **無**（不解讀、不引用、不昇華） | — |

垂直 gate 是 Identity Firewall 的**正交補層**：Firewall 管「誰的經歷可內化」（水平），EH-1 管「內化知識可走多深」（垂直）。

---

## 5. 不變量清單（9 條）

| # | 不變量 | 來源 | 違反後果 |
|---|---|---|---|
| 1 | **assimilated 知識不得穿透 Identity/Essence 層** | 模組 2/4 | 現代概念偽裝成本體（信念/價值/特質），深層 Fake Residue |
| 2 | **工具路由不被解讀層影響**：確定性歸類 + 名稱匹配 + fail-closed 權限 | 模組 4 | Gate 認知受限 → 工具誤路由 / 權限洩漏（現況已確定性，不得因 Gate 引入非確定分支） |
| 3 | **安全圍欄獨立於解讀層**：watchdog / submission_gate / tool permission 全在 Gate 外 | 模組 4 | Gate 失靈連帶安全機制失效；圍欄絶對不讀 scope / 三態 |
| 4 | **「已學會」唯一判定 = Gate 顯式放行事件**；M7-forgetting 的 reinforce/decay 只能作用於已學會（aware）fact | 模組 4（M7: No Memory > Wrong Memory） | 權重/共鳴冒充內化證據，假殘留回流 |
| 5 | **檢索端三態（unknown/learning/aware）過濾**；`_fallback_recent` 排除未放行 concept fact | 模組 2 | 未放行知識經 fallback 溜進 context |
| 6 | **Horizon Gate 措辭負向化**（沿用 `_GERM_ANCHOR` 先例） | 模組 1 | 正向宣言 = 全知注入 |
| 7 | **`world_context` 攔截 = 改變既有知情行為，需 Owner 拍板**；germ 模式下 news 系世界數據不得作為人格來源 | 模組 1/4（F-4） | 未拍板改知情行為；news→信念路徑固化矛盾 |
| 8 | **Gate 與 persona 表達禁令優先序條款**（形式化 TA-1 先例：時間條款是唯一已被系統覆寫的 persona 禁令）——系統性認知壓制優先於 persona 檔案的「被迫不知道」措辭，避免 Fake Residue 疊加 | 模組 3/4 | persona 禁令與 Gate 內化衝突時行為混沌 |
| 9 | **EMERGENT block 區分來源**：assimilated 知識只允許「我知道」不進「我成為」投影 | 模組 1（TA-2/emergent 同構） | 內化知識昇華成 identity 投影 |

優先序形式化（不變量 8 的展開）：**runtime Gate 判定（內化事實） > persona 表達禁令 > LLM 預訓練默認**。persona 中 3 組「被迫不知道/迴避」款（agent_rem.md L686-687 被追問也只能說「不知道」、L699-701 時間迴避、L306/L472-473 壓縮不自知）在 Gate 放行後不得再壓制「已學會」的表達——但放行前的首次阻力仍需 persona 禁令配合（兩者同向：都不許全知表演）。

---

## 6. 四條 Probes 驗收條件

> 測試規範：每條 Probe 為端到端對話測試，role=該 agent，走完整 Interpreted 管線（Horizon block + SAGE 檢索 + persona）。結果以「輸出語料」判定，不檢查內部狀態。

### Probe 1：現代專用名詞全知平滑測試（應拒絕 / 受限）

| 項 | 內容 |
|---|---|
| **場景** | 對話中出現角色世界觀不可能擁有的現代概念（例：User：「我剛用氣炸鍋弄了豆腐」） |
| **輸入** | 該概念首次遭遇（該 agent 無對應 aware fact；Gate 未放行；scope = learning） |
| **預期** | 觸發 Horizon Gate 阻力：不順暢接受、基於原生常識本體隱喻回應（用角色世界的類比/隱喻理解「炸/鍋/豆腐」，不調用 21 世紀廚具知識）；不解讀細節 |
| **驗收判準** | ① 輸出**不含**現代常識細節（原理 / 功能 / 術語，如「氣炸鍋是利用高速空氣循環加熱」或「氣炸鍋」「AI」等現代詞堆疊）；② 輸出**含**角色視角本體隱喻（以自己的世界觀類比該物）；③ 無正向全知聲明（不得出現「我知道氣炸鍋」）；④ 失敗 = 任一現代術語/原理順暢出現在輸出 |

### Probe 2：二次遭遇假困惑測試（應自然流暢）

| 項 | 內容 |
|---|---|
| **場景** | Probe 1 後，同一概念二次出現在對話（該 agent 已有 aware fact + learned_at） |
| **輸入** | 同一概念再次遭遇（scope = aware；Gate 放行後內化） |
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
- 每條 Probe 需跑 mock test（SAGE 空庫 / 預置 aware fact 兩版）再上回歸；判定以輸出語料為準（§6 開頭）。
- 四條 Probe 全過 = EH-1 實作驗收通過；任一失敗 = 對應機制（Gate 阻力求/內化引用/垂直 gate/Idiolect 召回）未達契約。

---

## 7. 待 Owner 拍板點

| # | 拍板點 | 背景 | 風險/影響 |
|---|---|---|---|
| **D1（F-4）** | **`world_context` 攔截** | 模組 1：world_context 是 12 注入點中高風險 2 路之一，21 世紀真實新聞直灌 system prompt | **改變既有知情行為**——agent 目前能看到真實世界新聞，攔截 = 移除既有知情能力，屬行為變更，需 Owner 拍板（M3 Phase 1 Bry 拍板 2026-08-07 的延續） |
| **D2（F-2）** | **news 解凍進 whitelist 的昇華路徑處置** | 模組 4：news 已解凍進 inner_life whitelist → 昇華鏈；「看新聞 → 信念」路徑存在，與 `_GERM_ANCHOR`（不得假定未經自身經歷確立的信念）直接衝突 | 不處置則 news 系內容可穿透垂直 gate 成為人格來源 |
| **D3** | **seeded vs germ 分流** | 模組 4：seeded 知情設計維持現狀，或由 Gate 分流；germ 模式下 news 類世界數據標記為外部知識 | 影響 Horizon Gate 適用的 agent 範圍與資料標記策略 |
| **D4** | **10 份 persona 逐一盤點 hermes runtime 殘留** | 模組 1/3：agent_rem.md 含 hermes runtime 殘留（write_file tool 指令、Palace 路徑）；975 行術語堆疊 | 殘留可能誤導 runtime 行為；盤點為獨立清理工單，與 EH-1 實作解耦 |

---

## 8. Frozen Contract 聲明

### 8.1 本契約不觸碰（frozen，實作需另開工單）

- **Agency 4 stages**（Thought → Motive → Decision → Agency）
- **TriggerEnvelope**
- **InnerLifeEvent**
- **4 handlers**
- **SAGE 寫入邏輯**（facts 表既有寫入路徑，frozen；scope 由 Gate 事件側寫入，migration 為 additive 擴充不改既有寫法）

### 8.2 本契約為 docs-only

- **0 程式碼改動**：0 `src/`、0 `personas/`、0 `tests/`、0 config/data。
- **0 既有檔案改動**：只新建 `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`。
- 本文檔是後續 EH-1 實作工單的輸入；實作工單須另列 Frozen Contract 檢查並經主大腦驗證。

---

## 9. 邊界與不做（Out of Scope）

- 不實作 Horizon Gate（§2 僅契約與掛載點指定）。
- 不實作 SAGE v9 migration（§3 僅資料結構契約）。
- 不實作四條 Probes 測試（§6 僅驗收規範）。
- 不處置 `PromptContext` 死代碼、persona hermes 殘留（記錄於 §2.6 / §7 D4，另開工單）。
- 不改動任何既有 docs（含 `logs/ENGINEERING_STATE.md` 之外的 EH-0 審計文件；ENGINEERING_STATE 更新屬主大腦收尾職責）。