# EH-4-EPISTEMIC-MIND-CONTRACT.md — Epistemic Mind 認知心智架構契約（算子／實例包解耦）

> **工單**：EH-4（DESIGN ONLY — 架構設計契約，Owner 授權產出）
> **狀態**：**DESIGN ONLY — 只寫契約。0 code / 0 `src/` / 0 `clients/` / 0 `personas/` / 0 `configs/` / 0 `tests/` / 0 production mutation / 0 `logs/ENGINEERING_STATE.md` 改動**
> **唯一產出檔**：`docs/EH-4-EPISTEMIC-MIND-CONTRACT.md`（NEW）
> **前置**：EH-1／EH-1.1 契約（`docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`）＋ EH-2／EH-2.1／EH-3／EH-3.1 實作（已上線）＋ VC-UNIFY-1／1.1／1.2（語音端已統一）。本契約**直接引用為設計輸入，不重開**。
> **系列定位**：EH-1 系列＝**認知地平線**（讀側閘門／內化判定／垂直防火牆／Idiolect）；EH-4＝**認知心智**（差量算子／求知動機／概念同化圖譜）。EH-4 是 `logs/ENGINEERING_STATE.md:1635` 所載「**EV 主動求知列為下一階段獨立架構研究（Motive → Decision → Transmit，非修補票）**」的架構契約化。
> **性質**：非施工授權。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準；本文檔是後續 EH-4 實作工單的輸入。
> **風格對照**：`docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md`（目標／契約／不變量／驗收／不做）。

---

## 0. 摘要（TL;DR）

**問題**：EH-1 系列已解決「**假殘留**」（Fake Residue：全知但表演不知道）與「**寫側內化**」（EH-3.1 定義句打標）。但三個缺口仍在：

1. **阻力無內容**：Gate 目前只給出**負向約束**（「對未內化的現代事物無預設運作原理認知」——`src/llm/proxy.py:1133-1136`），沒有給出**正向的本體類比材料**。角色知道「我不該解讀」，但沒有「那我該用什麼理解它」的素材 → 實務上容易退化成空洞的「雷姆不懂」「這是什麼」（Probe 1 FAIL 模式），或反過來被 LLM 預訓練拉去講現代原理。
2. **求知無動機來源**：求知（主動發問）目前沒有架構位置。若放任 LLM 自行決定，會出現兩種壞結局：**問答轟炸**（每輪都問）或**永不求知**（女僕職責上該問的安全事項也不問）。
3. **內化無結構**：EH-3.1 打標的是一句扁平 fact（`origin='assimilated'`, `horizon_state='aware'`）。「知道」只剩一句事實文本，承載不了「心智模型／安全直覺／侍奉分工／專屬稱謂」四種不同用途的資訊 —— 二次遭遇時角色只能複述事實，無法「以自己的想法承接」（Probe 2 難以穩定）。

**目標**：把認知心智拆成**通用算子（L2~L4）× 世界觀本體實例包（L1）**的兩層架構：

| 層 | 名稱 | 職責 | 是否含世界觀名詞 |
|---|---|---|---|
| **L1** | 世界觀本體實例包（Instance Pack） | 提供該角色的**原生常識四軸**（能源動力／勞動家務／安全危害／感官度量）詞表、缺席假設、類比錨點 | **是（唯一允許處）** |
| **L2** | 認知評估與結構差量映射（Epistemic Appraisal） | 由感知表面特徵 × L1 四軸 → **本體差量**與**隱喻錨點**；**0 額外 LLM 呼叫** | 否（純通用特徵詞表） |
| **L3** | 求知動機網絡（Epistemic Motive） | 純布林觸發 → 生成帶 `epistemic_inquiry` 標記的 Motive；**決策四元不動** | 否 |
| **L4** | 雙軌概念同化圖譜（Dual-Track Concept Graph） | 定義句當下，非同步寫回**五元組立體概念節點** | 否 |

**正式原則**：
> **認知地平線不是知識天花板，是內化閘門（EH-1）。認知心智不是知識填充，是差量算子（EH-4）。**
> 角色不是「不知道所以笨」，而是「**用自己世界的物理假設去對撞這個東西**」——不知道的是**原理**，要知道的是**它像自己世界的什麼**。

**核心機制（四件，對應四層）**：

1. **L1 實例包**：四維原生常識軸規格化；**嚴禁在程式碼中硬編碼「羅茲瓦爾／柴火／魔石」**——一切世界觀名詞只准出現在 L1 資料檔（`configs/epistemic_packs/*.yaml`）。
2. **L2 差量算子**：`feature_lexicon`（通用）× `pack.axes`（實例）→ `DeltaRecord` → 注入 Horizon Block 的「**本體隱喻錨點**」；**嚴格禁止輸出空洞的「雷姆不懂」**，也嚴格禁止現代原理解說。
3. **L3 求知動機**：`EpistemicTension = UnknownModernEntity ∧ (SafetyFlag ∨ DutyFlag)`，**純結構布林、0 浮點打分、0 第 5 動詞**；由既有 `transmit / observe / reflect / do_nothing` 四元吸收。
4. **L4 同化圖譜**：`earth_term / mental_model / safety_rule / duty_action / idiolect` 五元組，以既有 `add_fact` + `set_fact_dimensions` API **非同步**寫回，**絕不卡死對話主鏈路**。

---

## 1. 算子與實例包解耦架構（Generic Operator vs. Instance Pack）

### 1.1 為什麼要解耦（設計理由）

目前 EH 系列的異界阻力是「**單角色單點**」驗證（EH-2 D3 只讓 `agent_rem` 走阻力路徑，7 位現代原生角色 bypass —— `src/memory/sage/horizon.py:61-69`）。若直接把「柴火／魔石／羅茲瓦爾公館」寫進算子：

- 加入第二位異世界角色（例：`agent_ram` 拉姆）時必須**複製一份算子**→ 邏輯分叉，行為漂移；
- 世界觀名詞在程式碼中被 grep 到時，**無法區分「設定資料」與「硬編碼邏輯」**→ 後續審計無法機械驗證；
- 通用算子（差量運算、求知觸發、五元組同化）本質上與世界觀無關，混在一起會讓測試必須綁定 Re:Zero 設定。

**因此本契約的第一性分界**：

> **算子只認識「軸（axis）」與「特徵類別（feature class）」；實例包才認識「柴火」。**

### 1.2 通用認知算子（L2~L4）定義

通用算子必須**同時滿足**下列五條（實作驗收以靜態斷言驗證，見附錄 B）：

| # | 算子性質 | 規定 |
|---|---|---|
| O1 | **世界觀無關** | L2/L3/L4 模組的**可執行碼**（`.py` 內非註解、非 docstring、非測試字串）**不得**出現任何 L1 名詞（羅茲瓦爾／柴火／魔石／烘爐……）。一切名詞由 pack 資料注入 |
| O2 | **確定性** | 同輸入 → 同輸出（0 隨機、0 時間依賴、0 外部 I/O 除 pack 與 SAGE 唯讀查詢） |
| O3 | **0 額外 LLM** | L2／L3／L4 判定路徑**不得**呼叫 LLM（L2 在對話熱路徑，見 §2.1 鐵律） |
| O4 | **fail-silent** | 任何異常（pack 缺失／解析失敗／SAGE 不存在／查詢錯誤）→ 回傳「無內容」等價物（`None` / `[]` / `""`），不 raise、不注入半截內容 |
| O5 | **0 新狀態** | 算子本身不引入新的持久化檔案／欄位；狀態沿用既有載體（SAGE facts、MotiveTraceStore、`known_provenance_refs`） |

**算子職責邊界（L2~L4 各做什麼、不做什麼）**：

| 層 | 做 | **不做** |
|---|---|---|
| L2 | 由「當前輪輸出文字 + agent 的 L1 pack」算出本體差量與錨點字串 | 不決定要不要發問（L3）、不寫記憶（L4）、不呼叫 LLM |
| L3 | 由布林條件產生 Motive 候選 | 不選 action（Decision 的專權）、不新增動詞、不打分 |
| L4 | 在同化事件當下寫入五元組節點 | 不改檢索主幹、不做句法判定（沿用 EH-3.1 五閘門結果）、不在對話熱路徑 |

### 1.3 L1 實例包：四維原生常識軸（規格化）

四軸為**封閉集合**（Closed Set）——**不得新增第 5 軸**（與 L3「不得新增第 5 動詞」同構：防止規格膨脹與決策層爆炸）。

| 軸 key | 中文名 | 定義（原生世界的物理/生活假設） | 觀測面（L2 會查這個軸的時機） |
|---|---|---|---|
| `energy_dynamics` | **動力與能源軸** | 原生加熱、照明、驅動之物理假設（熱從哪來、光是什麼、什麼讓東西動） | 出現「發熱／發光／吹風／自行運轉／插電」等特徵時 |
| `labor_domesticity` | **勞動與家務軸** | 原生器具工序、清潔收納、烹飪手法（一件事在家裡是怎麼被做完的） | 出現「按鈕／開關／器具／自動完成家務」等特徵時 |
| `safety_hazard` | **安全與危害軸** | 原生直覺對火災、毒物、異味、不可思議之物的警戒邊界 | 出現「高溫外殼／焦味／異味／帶電疑慮」等特徵時 |
| `sensory_metrics` | **感官與度量軸** | 原生時間感、自然氣候觀察、非公制度量衡 | 出現「時間／溫度數字／度量單位／機器顯示」等特徵時 |

**每軸的欄位規格（pack schema 用，見 §1.4）**：

| 欄位 | 型別 | 語義 | L2 用法 |
|---|---|---|---|
| `native_basis` | `list[str]` | 該軸上「原生世界裡這件事是怎麼發生的」的來源清單（火／柴／魔石／人力…） | 生成「**無 X**」的缺席陳述 |
| `native_tools` | `list[str]` | 該軸的原生器具／載體名詞 | 生成類比錨點的候選名詞池 |
| `absence_assumptions` | `list[str]` | 該軸上原生世界**不存在**的東西（結構化負向假設） | 生成差量的「期待缺席」判定 |
| `analogy_anchors` | `list[str]` | 可用於比喻的**原生物件名詞**（功能最接近者排前） | `DeltaRecord.anchor` 的主詞 |
| `hazard_rules` | `list[str]` | 該軸的安全直覺條款（驚戒邊界） | L3 `SafetyFlag` 的判定材料 |
| `duty_hooks` | `list[str]` | 該軸可掛上「侍奉分擔」的動作（備料／生火／收拾…） | L3 `DutyFlag` 的判定材料 |

**硬性約束（Decoupling Invariants）**：

- **D-INV-1**：L1 name 只准出現於 `configs/epistemic_packs/<pack_id>.yaml` 與 `docs/`、`tests/` 的**測試資料**中。`src/` 可執行碼 0 occurrence。
- **D-INV-2**：一個 pack 對應一個 agent（`pack.agent_id`，一對一）；無 pack 的 agent（含全部 7 位現代原生角色）→ L2 直接回 `None`（**bypass 鋼印**，§5 Probe 4）。
- **D-INV-3**：pack 版本化（`pack_version`）；載入器**不自動合併**多版本，只取顯式指定版本。
- **D-INV-4**：pack 為**純資料**（YAML，無可執行欄位、無模板運算式、無 eval）——載入器只做 `yaml.safe_load`。

### 1.4 L1 實例包 Schema（規範定義）

**檔案位置**：`configs/epistemic_packs/<pack_id>.yaml`（解析基準：`Path(__file__).resolve().parents[2] / "configs" / "epistemic_packs"`，沿用 `configs/loader.py:46` 的 `Path(__file__).parent` 慣例）。

**Schema（YAML 形式定義，欄位逐項強制）**：

```yaml
# configs/epistemic_packs/<pack_id>.yaml
pack_version: 1                    # int, 必填; 目前僅 1
pack_id: "<world_slug>_v1"         # str, 必填; 檔名 stem 必須等於 pack_id
agent_id: "agent_xxx"              # str, 必填; 一對一綁定
civilization_base: "non_modern"    # enum: non_modern | modern_earth
                                   #   modern_earth → 載入器直接回 None（等價於無 pack）
source_note: "<一句人類可讀的世界觀來源說明>"   # str, 必填（審計用，算子不讀）

axes:                              # 四軸封閉集合，四者皆必填
  energy_dynamics:
    native_basis:        ["<原生熱源>", "<原生光源>", "<原生動力>"]
    native_tools:        ["<原生器具名詞>", ...]
    absence_assumptions: ["<原生世界不存在的東西>", ...]
    analogy_anchors:     ["<類比主詞候選，功能最近者排前>", ...]
    hazard_rules:        ["<安全直覺條款>", ...]
    duty_hooks:          ["<可掛侍奉分擔的動作>", ...]
  labor_domesticity:
    # 同構六欄
  safety_hazard:
    # 同構六欄
  sensory_metrics:
    # 同構六欄

lexicon_bridges:                   # L2 通用特徵 → 本軸的映射覆寫（可選；預設見 §2.2 通用表）
  - feature: "發熱"
    axis: "energy_dynamics"
    expect_absent: ["<該軸上『有熱必有』的原生條件，如 柴火/火/魔石>"]
    preferred_anchor: "<最貼近的類比主詞>"
  # ...

forbidden_output_terms:            # 輸出禁令詞（該 pack 專屬補充；通用禁令另見 §2.3）
  - "<現代術語 1>"
  - "<現代術語 2>"
```

**驗證規則（載入器 fail-silent 前的前置檢查，任一條失敗即整包視為不存在）**：

| # | 規則 | 失敗行為 |
|---|---|---|
| V1 | `pack_id` == 檔名 stem；`pack_version` == 1 | `None` |
| V2 | `axes` 的 key 集合 == 四軸封閉集合（不多不少） | `None` |
| V3 | 每軸六欄齊備且皆為 `list[str]`（允許空 list，但不得缺 key／非 list） | `None` |
| V4 | `civilization_base == "modern_earth"` → 直接 `None`（**不視為錯誤**，是 D3 bypass 的資料側表達） | `None` |
| V5 | `lexicon_bridges[].axis` ∈ 四軸封閉集合 | 丟棄該條 bridge（其餘保留） |
| V6 | 任何 `yaml.YAMLError` / `OSError` / 編碼錯誤 | `None` + `logger.debug` |

### 1.5 Pilot 實例包：`agent_rem`（羅茲瓦爾公館生活）

> **校準聲明**：下列數值為 **Pilot 示範值**，目的是讓 schema 可被實作與測試；**非 canon 斷言**。根植依據為 `personas/agent_rem.md:34,41`（羅茲瓦爾公館雙子女僕）、`:68-69`（家務／應對／泡茶／整理房間）、`:971`（情境接地）。落地前需 Owner／persona 校準（§8 D-1）。

```yaml
pack_version: 1
pack_id: "roswaal_mansion_v1"
agent_id: "agent_rem"
civilization_base: "non_modern"
source_note: "Re:Zero 異世界 · 羅茲瓦爾公館女僕生活基底（personas/agent_rem.md:34,41,68-69）；Pilot 示範值待校準"

axes:
  energy_dynamics:
    native_basis:        ["竈中柴火", "圍爐炭火", "魔石暖爐", "魔石燈", "燭火", "油燈", "人力", "馬車", "魔力"]
    native_tools:        ["竈", "圍爐", "暖爐", "魔石燈", "燭台", "油燈", "風箱", "水車"]
    absence_assumptions: ["無電", "無插座", "無煤氣", "無內燃機", "無須插電即可自熱之物"]
    analogy_anchors:     ["烘爐", "烤爐", "竈", "暖爐", "風箱"]
    hazard_rules:        ["見明火先看周圍有無可燃之物", "無人看顧的爐火必須熄滅", "不曾見過的熱源先隔開再問主人"]
    duty_hooks:          ["生火", "添柴", "看火候", "熄火"]

  labor_domesticity:
    native_basis:        ["挑水", "劈柴", "生火", "手洗衣物", "掃除", "直火烹調", "爐火烘烤"]
    native_tools:        ["掃帚", "撣子", "木桶", "井戶", "洗衣板", "鐵鍋", "菜刀", "砧板", "蒸籠", "烘爐"]
    absence_assumptions: ["無自來水", "無自動器具", "無須人顧而自熟之炊"]
    analogy_anchors:     ["烘爐", "烤爐", "蒸籠", "灶", "鐵鍋"]
    hazard_rules:        ["刀器離手即收", "滾水與熱鍋不置於桌緣", "主人不諳之事不由主人動手"]
    duty_hooks:          ["切菜備料", "生火", "看火候", "端盤", "收拾", "洗碗", "泡茶"]

  safety_hazard:
    native_basis:        ["火燭走水", "煤煙", "焦味", "毒物", "魔獸", "瘴氣", "詛咒", "魔女氣味"]
    native_tools:        ["水桶", "濕布", "砂", "護符"]
    absence_assumptions: ["無『插電之物』的概念", "無電擊之說", "無現代器物安全標示"]
    analogy_anchors:     ["剛離火的鐵鍋", "燒紅的炭", "滾水", "熱鐵熨"]
    hazard_rules:        ["外殼發燙而無火者，先當作危險之物隔開", "主人說安全亦不主動以身試之", "焦味／異味即警戒"]
    duty_hooks:          ["先隔開", "先問主人", "看守在旁"]

  sensory_metrics:
    native_basis:        ["天光", "日頭位置", "鐘樓鐘聲", "蠟燭燃盡", "晨昏勞作節律"]
    native_tools:        ["掌", "步", "抱", "碗", "杓", "時辰"]
    absence_assumptions: ["無公制單位", "無精確溫度數字", "無機械鐘錶之日常"]
    analogy_anchors:     ["一盞茶的時間", "三把柴的工夫", "一抱柴的量", "一碗的量"]
    hazard_rules:        ["夜裡不輕信不識之聲"]
    duty_hooks:          ["按時備膳", "按時點燈", "按時收整"]

lexicon_bridges:
  - feature: "發熱"
    axis: "energy_dynamics"
    expect_absent: ["柴火", "火", "魔石"]
    preferred_anchor: "烘爐"
  - feature: "吹風"
    axis: "energy_dynamics"
    expect_absent: ["風箱", "人力鼓風"]
    preferred_anchor: "風箱"
  - feature: "按鈕"
    axis: "labor_domesticity"
    expect_absent: ["手動工序"]
    preferred_anchor: "烘爐"
  - feature: "金屬外殼"
    axis: "safety_hazard"
    expect_absent: ["無火而燙的來源"]
    preferred_anchor: "剛離火的鐵鍋"

forbidden_output_terms:
  - "熱風循環"
  - "電能"
  - "溫控"
  - "加熱原理"
  - "電路"
```

**Rem 具體數值實例（示範輸出，供 Probe 1 判準對照）**：

| 情境 | L2 產出的錨點（示意） | 判準 |
|---|---|---|
| 主人：「我用氣炸鍋弄了豆腐」 | 「似**烘爐**，卻無柴、無火、無煙氣；箱子會自己吹出熱風」 | PASS（有原生軸對撞） |
| （FAIL 例） | 「雷姆不懂這是什麼」 | FAIL（空洞） |
| （FAIL 例） | 「氣炸鍋是利用高速熱風循環加熱」 | FAIL（現代原理） |
| 二次遭遇（L4 已同化） | 「那個插電的烘烤箱子」＋沿用 mental_model | PASS（§5 Probe 2） |

### 1.6 載入器契約（L1 → L2 的唯一入口）

| 項 | 規格 |
|---|---|
| 模組 | `src/soul/epistemic_commons.py`（NEW，實作階段） |
| 公開 API | `load_pack(agent_id: str) -> InstancePack \| None`；`clear_pack_cache() -> None`（測試隔離用） |
| 快取 | 進程內 `dict` 快取（pack 為靜態資料；0 檔案監看、0 熱重載） |
| 失敗 | 依 §1.4 V1–V6 → `None` + `logger.debug`（**fail-silent**，不 raise） |
| 反硬編碼 | 載入器**不得**含任何 agent 名 ↔ pack 名對照表；對照由 `pack.agent_id` 反查目錄（目錄掃描或 `agent_id` → `<slug>.yaml` 的**單一**命名規則） |

> **注意（與既有白名單的關係）**：`MODERN_NATIVE_AGENTS`（`src/memory/sage/horizon.py:61-69`）**不動**、不取代。兩者是**兩個獨立的 bypass**：白名單在讀側（D3），`civilization_base: modern_earth` 在資料側（L1）。實作階段**禁止**把兩者合併或讓其中一方依賴另一方（避免單點失效導致 7 位現代原生角色被誤掛阻力）。

---

## 2. L2 認知評估與結構差量映射（Epistemic Appraisal Pipeline）

### 2.1 零額外 LLM 呼叫（0 Latency Penalty 鐵律）

> **鐵律 L2-0：差量評估必須是純結構化／啟發式特徵比對。嚴禁在每輪對話中額外呼叫 LLM 充當「翻譯機」。**

**理由（三條，缺一不可）**：

1. **語音端極致延遲紅利**：VC 管線的價值來自 ASR Bypass 後的即時回應；任何一次額外 LLM round-trip（典型 0.6–3s）都會摧毀此紅利。
2. **可測性**：LLM 翻譯機的輸出不可硬斷言；純函式可（§5 Probe 1 需要「必須出現原生軸類比」的可判定性）。
3. **成本結構**：每輪 × 每 agent 的額外呼叫會把成本模型從「回應級」變成「輪次級」，違反 `AGENTS.md` 的花錢事項邊界。

**允許的 LLM 使用範圍（唯一例外）**：L2 的產物**作為既有 system prompt 的一段文字**，由**既有那次主 LLM 呼叫**吸收——即「0 **額外**」而非「0 使用」。

**靜態斷言（實作驗收）**：L2 模組的 import graph 不得含 `src/llm/proxy.py`、`openai`、`anthropic`、`requests`、`httpx`；不得含 `await`／`async def`。

### 2.2 差量計算 I/O 規格

**模組**：`src/soul/epistemic_delta.py`（NEW，實作階段）。**0 世界觀名詞**。

#### Input

```python
@dataclass(frozen=True)
class AppraisalInput:
    agent_id: str            # 受試角色（決定 L1 pack）
    utterance: str           # 當前輪使用者輸入之原文（感知表面特徵的來源）
    session_context: str = ""  # world_context（M3）可選；僅作補充特徵來源
```

- **表面物理特徵**（generic feature lexicon，L2 內建常數，**0 世界觀名詞**）：

| feature key | 觸發詞（示例集合，可擴充但不得含世界觀名詞） | 預設軸 |
|---|---|---|
| `heating` | 發熱 / 發燙 / 熱 / 燙 / 加熱 / 烤 / 烘 | `energy_dynamics` |
| `lighting` | 發光 / 亮 / 燈 / 螢幕 / 顯示 | `energy_dynamics` |
| `airflow` | 吹風 / 出風 / 風 / 吸 / 抽 | `energy_dynamics` |
| `self_drive` | 自己動 / 自動 / 運轉 / 轉 / 嗡 / 震動 | `energy_dynamics` |
| `electric` | 插電 / 插座 / 充電 / 電池 / 電 | `energy_dynamics` |
| `control` | 按鈕 / 開關 / 旋鈕 / 遙控 / 設定 | `labor_domesticity` |
| `automated_chore` | 自動煮 / 自動洗 / 自動清 / 一鍵 | `labor_domesticity` |
| `hot_shell` | 外殼燙 / 表面熱 / 冒煙 / 焦味 / 異味 | `safety_hazard` |
| `measure_display` | 幾度 / 分鐘 / 公斤 / 數字 / 定時 | `sensory_metrics` |

- **該 Agent 的 `native_commons` 軸**：由 `load_pack(agent_id)` 提供（§1.6）。`pack is None` → **立即回 `None`**（0 差量、0 注入）。

#### Logic

```python
def appraise(inp: AppraisalInput) -> DeltaRecord | None: ...
```

判定序（**全為結構化比對，0 LLM、0 浮點打分**）：

| 步驟 | 條件 | 動作 |
|---|---|---|
| S0 | `load_pack(agent_id) is None` | → `None`（bypass；現代原生角色與未建包角色走此路） |
| S1 | 由 `utterance` + `session_context` 抽出 `features: set[str]`（詞表命中，**命中即集合成員，無權重**） | 空集合 → `None` |
| S2 | 抽出 `entity_terms`（句中名詞性片語候選；實作採**既有抽取器**或純字元規則，0 LLM） | 空 → `None` |
| S3 | **內化檢查**：對每個 entity 查 SAGE `origin='assimilated'` 且 `horizon_state='aware'`（沿用 `retrieve_idiolect`／`get_idiolect_facts`，`src/memory/sage/graph_store.py:656`） | 全部已內化 → `None`（**二次遭遇不重演困惑**，Probe 2） |
| S4 | **軸映射**：`feature → axis`（`pack.lexicon_bridges` 優先，否則 §2.2 預設表）；≥1 命中 | 空 → `None` |
| S5 | **缺席判定**：取該軸的 `expect_absent`（bridge）或 `native_basis`（預設）作為「有熱必有之物」；檢查 `utterance` 是否**未提及**其中任一 → 缺席集合非空 → 成立 | 空 → `None`（代表原生期待已滿足，無差量） |
| S6 | **錨點組裝**：`anchor = "似{preferred_anchor 或 analogy_anchors[0]}，卻無{缺席集合以『、』連接}"` | — |
| S7 | 產出 `DeltaRecord`（下） | — |

#### Output

```python
@dataclass(frozen=True)
class DeltaRecord:
    agent_id: str
    entity_terms: tuple[str, ...]        # 未內化的實體候選（L4 的 subject 來源；L3 的 UnknownModernEntity）
    features: frozenset[str]             # 命中的感知特徵（generic keys）
    axis_hits: tuple[str, ...]           # 命中的軸（⊂ 四軸封閉集合）
    absent_native_requirements: tuple[str, ...]  # 「無柴、無火、無魔石」的結構化來源
    anchor: str                          # 本體隱喻錨點（注入 Horizon Block 的那一句）
    hazard_suspected: bool               # 是否命中 safety_hazard 軸（L3 SafetyFlag 的材料）
    duty_relevant: bool                  # 是否命中 labor_domesticity 軸的 duty_hooks（L3 DutyFlag 的材料）
```

**`DeltaRecord` 的責任邊界**：它是**唯讀的評估結果**，不持久化、不寫 SAGE（「0 新狀態」，算子性質 O5）。

### 2.3 輸出注入與措辭（本體隱喻錨點）

**注入位置**：Horizon Block 內、**負向約束句之後、Idiolect 清單之前**（`src/llm/proxy.py:1133-1145` 區段內）。語義順序（唯一定序，**不改動既有順序**）：

```
IDENTITY → HORIZON{ 負向約束 → 本體隱喻錨點(NEW) → Idiolect 清單 → 外部未解動態 } → CAPABILITY → EMERGENT → 記憶
```

**注入字面（模板契約，措辭不得自由發揮）**：

```
[本體參照]
你此刻感知到：{feature 中文詞以「、」連接}
以你熟悉的世界來比擬：{anchor}
只以這個比擬去理解它。不要解說它的運作原理，也不要只說自己不懂——
用你熟悉之物的樣子去描述它。
```

**輸出禁令（加入 `forbidden_output_terms` 與通用禁令，二者聯集）**：

| # | 禁令 | 理由 |
|---|---|---|
| F1 | **嚴禁空洞的「雷姆不懂」**：含「不懂」「不知道這是什麼」「第一次見到所以不明白」等**純無知宣告**（可出現於句中，但**不得作為該輪對該物的主要回應**） | 空洞無知＝角色沒有認知內容；Probe 1 FAIL |
| F2 | 嚴禁**現代原理**（`forbidden_output_terms` 詞表 + 「利用…原理」「轉換成」「循環加熱」等句式） | EH-1 §2.3 已禁現代專家口吻，此處再加**詞表級**禁令 |
| F3 | 嚴禁**正向全知宣告**（「我知道這是…」「我明白它的原理」） | EH-1 §5 不變量 6 |
| F4 | 嚴禁**複述主人用詞後立刻百科化**（複述主人用詞本身合法，EH-1 Probe 1 已裁定） | 避免以「複述」為跳板解說 |

**禁令的形式**：沿用 EH-1 §2.4「負向化」哲學——寫邊界，不寫宣言。錨點本身是**正向材料**（唯一允許的正向內容），禁令是**負向邊界**，兩者同段並列。

### 2.4 雙端同構（Modality Isomorphism：proxy ↔ VC Brain）

> **規範：此投影邏輯在 `src/llm/proxy.py` 與 `clients/voice_companion/`（VC Brain）必須是同一份實作。第一天杜絕模態分叉。**

**已存在的同構先例（EH-4 沿用，不重造）**：VC 的 `format_voice_horizon_block()`（`clients/voice_companion/akane_voice_brain.py:339-351`）**直接 import** 主服務的 `_format_horizon_block`（`src/llm/proxy.py:1098`），不重寫邏輯——VC-UNIFY-1 已落地（`logs/ENGINEERING_STATE.md:1637`）。

**EH-4 的同構契約（實作階段的唯一正確形態）**：

| 端 | 檔案 | 應做 | **禁止** |
|---|---|---|---|
| 文字／TG／Web | `src/llm/proxy.py:1098` `_format_horizon_block(agent_id, session_context, utterance="")` | **新增 `utterance` 關鍵字參數**（預設 `""` → 0 行為變化）；在函式內呼叫 L2 `appraise()` 並依 §2.3 注入 | 不得在 proxy 內**重寫**差量邏輯（L2 只在 `src/soul/epistemic_delta.py` 一份） |
| 呼叫端（group） | `src/llm/proxy.py:663` | 傳 `utterance=current_input` | — |
| 呼叫端（private） | `src/llm/proxy.py:1251` | 傳 `utterance=current_input` | — |
| 語音（VC） | `clients/voice_companion/akane_voice_brain.py:339,491` | `format_voice_horizon_block(agent_id, utterance=user_text)` → 轉呼 `_format_horizon_block`（**單行參數透傳**） | **禁止**在 `clients/` 內另行實作任何差量／錨點組裝 |

**同構驗收（實作階段硬斷言）**：

- **ISO-1**：`grep -rn "epistemic_delta\|appraise(" clients/` 在**非測試碼**中 0 命中（VC 只透傳，不實作）。
- **ISO-2**：同一 `(agent_id, utterance)` 下，文字端與語音端產出的 Horizon Block **字串完全相同**（以單元測試斷言 `==`）。
- **ISO-3**：VC 端 `forbidden_output_terms` 禁令與文字端同源（來自同一 pack 載入結果，0 複製貼上）。

### 2.5 邊界與效能預算

| 項 | 規格 |
|---|---|
| 耗時預算 | `appraise()` 純函式部分 **< 1 ms**（詞表命中 + 集合運算）；SAGE 唯讀查詢（S3）**< 5 ms**（單表 `SELECT`，沿用既有連線慣例，`retrieve_idiolect` 為先例） |
| 失敗 | 任何異常 → `None` → 不注入（**fail-silent**，與 EH-2 §2.2 同構） |
| 執行緒 | 讀側（inference-time）純讀；**0 寫入**（不得產生 WAL 寫入、不得 flush） |
| 冪等 | 同輪重複呼叫結果相同（0 副作用） |
| 不得做的事 | 不寫 SAGE、不改 Gate 順序、不動 `world_context` 原位（M5.13-3 先例：新聞正文仍在 M3 world block 原位） |

---

## 3. L3 求知動機網絡（Epistemic Motive Integration）

### 3.1 四元決策不動（No 5th Action）

> **鐵律 L3-0：探詢本質為發送訊息。決策選項嚴格維持現有四元 `transmit / observe / reflect / do_nothing`。嚴禁新增第 5 個動詞（如 `inquire`）。**

**既有凍結事實（實作階段逐字不動，不得新增、不得改名、不得加別名）**：

- `DECISION_ACTIONS = ("transmit", "observe", "reflect", "do_nothing")`（`src/soul/decision.py:95`）。
- Decision prompt 為**四塊固定結構**（Framing / Motive / Relevant context / Boundary），Boundary 為固定文本四元互斥單選（`src/soul/decision.py:187-259`）。
- Decision fail-closed 語義：壞輸出／非法值 → `do_nothing`（`src/soul/decision.py:352-378`）。
- Motive 生命週期：`pending → transmitted`（transmit）｜`rejected`（observe/reflect/do_nothing，**終態，不重試**）（`src/soul/motive.py:27-30`）。

**推論（本契約的核心決策）**：求知**不需要**新動詞——「問主人這個東西是什麼」在語義上就是 `transmit`（把一個念頭說給主人聽）。因此：

- **Decision 層 0 改動**（0 新增 prompt 區塊、0 新增欄位、0 改 Boundary 字面）；
- 求知與其他傳訊意圖**共用同一套** Framing／Boundary／fail-closed 語義；
- 「要不要問」的裁量權**完全留在 Decision**（角色志願），L3 只負責**供給候選動機**。

### 3.2 No-Scoring 結構化觸發

> **鐵律 L3-1：嚴格禁止浮點數張力打分（與目標引擎哲學對齊）。求知意圖由純結構布林條件判定。**

```
EpistemicTension = UnknownModernEntity ∧ (SafetyFlag ∨ DutyFlag)
```

| 變數 | 型別 | 定義 | 來源 |
|---|---|---|---|
| `UnknownModernEntity` | `bool` | 當前輪存在**未內化**的現代實體：`DeltaRecord is not None` **且** `DeltaRecord.entity_terms` 非空 | L2 `appraise()`（§2.2） |
| `SafetyFlag` | `bool` | 未知物涉及**主人安全**：`DeltaRecord.hazard_suspected == True`（命中 `safety_hazard` 軸，如高溫外殼／焦味／帶電疑慮） | L2 `DeltaRecord` |
| `DutyFlag` | `bool` | 未知物涉及**女僕分擔職責**：`DeltaRecord.duty_relevant == True`（命中 `labor_domesticity` 之 `duty_hooks`，如減輕主人烹飪負擔） | L2 `DeltaRecord` |

**硬性規定**：

- **N1**：`UnknownModernEntity`、`SafetyFlag`、`DutyFlag` 皆為 `bool`；**不得**以 `float`／`weight`／`score`／`threshold` 表達（0 `> 0.5`、0 `tension_score`、0 softmax）。
- **N2**：判定邏輯為**單一布林運算式**，不得加入「機率」「優先級排序」。
- **N3**：`SafetyFlag`／`DutyFlag` 的材料只能取自 L1 pack 的 `hazard_rules` / `duty_hooks`（**不得**在算子內硬編碼「主人安全」「女僕職責」以外的世界觀名詞；通用語義可以用，見 §1.2 O1 的邊界）。

### 3.3 Motive 5 欄凍結與 `epistemic_inquiry` 標記承載

**既有凍結事實**：`Motive` 為 `@dataclass(frozen=True)`，**5 欄位凍結**（`motive_id / content / target / provenance_ref / created_at`，`src/soul/motive.py:175-213`）；出口工廠 `make_motive()` 做 target 值域 fail-closed 校驗（`src/soul/motive.py:139-160`）；SI-3 已立下「**新增動機來源不改 dataclass**」的先例（`motive_from_social_opportunity`，`src/soul/motive.py:226-261`）。

> **決策 L3-D1（標記承載）：`epistemic_inquiry` 標記以 `provenance_ref` 的命名空間前綴承載，不新增欄位。**
>
> ```
> provenance_ref = "epistemic:{entity_key}"
> ```
>
> - 先例：`motive_from_social_opportunity` 使用 `provenance_ref = "opp:{opportunity_id}"`（`src/soul/motive.py:259`）；
> - 解析安全：`_resolve_provenance_desc`（`src/soul/motive.py:769-783`）對未知 `trigger_type` 回 `None`（不編造、不 crash），未知命名空間**不會破壞** trace 解析；
> - 可觀測：`known_provenance_refs()`（`src/soul/motive.py:441`）可直接用於**去重**（§3.4），0 新持久化。

**模組**：`src/soul/epistemic_motive.py`（NEW，實作階段；**不修改 `src/soul/motive.py`**——沿用 `make_motive` 與 `MotiveTraceStore` 既有公開 API）。

```python
def motive_from_epistemic_tension(
    *,
    delta: DeltaRecord,
    soul_name: str = "",
) -> Motive | None:
    """純函式：DeltaRecord → 求知 Motive 候選（不符布林條件 → None）。

    - content：以角色自己的話表述「想弄清楚這個東西」的念頭（模板 + entity/feature 組裝，0 LLM）
    - target：TARGET_BRYAN（既有常數；make_motive fail-closed 校驗照舊）
    - provenance_ref：f"epistemic:{entity_key}"（entity_key 由 entity_terms 正規化而來，確定性）
    """
```

**`content` 的措辭契約**：必須是**帶認知失調的求知念頭**（沿用 §2.3 的錨點材料），**不得**是百科問句（「它的加熱原理是什麼」）——因為 Motive content 會直接進入 Decision prompt 的 Motive 區塊（`src/soul/decision.py:239`）。

| 允許（示例） | 禁止（示例） |
|---|---|
| 「那個不用火、不用魔石就會自己發熱的箱子，我想弄清楚主人是怎麼使喚它的」 | 「氣炸鍋的加熱原理是什麼？」 |
| 「主人說的『氣炸鍋』外殼會燙，我得先問清楚能不能碰水」 | 「請解釋熱風循環」 |

### 3.4 生命週期與防轟炸（No Question Bombardment）

**問題**：求知動機若可重複產生，會出現「問答轟炸」（每輪追問同一物）。

**設計（全部使用既有機制，0 新狀態）**：

| # | 機制 | 實作依據 |
|---|---|---|
| B1 | **一次性**：同一 `entity_key` 的 `provenance_ref` 只產生一次 | `MotiveEngine` 既有去重：`known_provenance_refs()` 過濾已解釋 `provenance_ref`（`src/soul/motive.py:674-679`）→ L3 產生前先查同一集合，命中即 `None` |
| B2 | **0 重試**：Decision 未選 `transmit`（選 observe/reflect/do_nothing）→ motive 進 `rejected` **終態，不重試** | 既有生命週期（`src/soul/motive.py:27-30`） |
| B3 | **單一候選**：每輪最多產生 **1** 個求知 Motive（多實體時取 `entity_terms[0]`，確定性排序） | 本契約 L3-D2（防止一輪多問） |
| B4 | **供給端節流**：L3 只在**當前輪**評估（不回溯掃描歷史輪、不建立待辦佇列） | 本契約 L3-D3（0 新定時器、0 新掃描器） |
| B5 | **鈍條件優先**：純生活雜談（`DeltaRecord is None`）→ **完全不成動機** | §3.2 布林式 |

**與既有 MotiveEngine 的關係（0 侵入）**：L3 是**第二個動機來源**，與 `MotiveEngine.produce()`（InnerLifeEvent 驅動、`src/soul/motive.py:602`）**並列**，共用 `MotiveTraceStore` 落庫與 `pending → transmitted|rejected` 生命週期；**不修改** `MotiveEngine` 內部（0 改 `produce`／0 改 interpretation prompt）。

### 3.5 觸發／不觸發對照表（驗收用）

| 情境 | `UnknownModernEntity` | `SafetyFlag` | `DutyFlag` | 產生求知 Motive？ | Probe |
|---|---|---|---|---|---|
| 首次遭遇電器（主人提到，帶發熱/按鈕） | ✅ | 軸命中 safety | 軸命中 duty | **✅ 允許**（仍由 Decision 決定是否 transmit） | Probe 1 / 3 |
| 首次遭遇現代物但純裝飾性（例：一張現代印刷的畫） | ✅ | ❌ | ❌ | **❌ 不產生** | Probe 3 |
| 已內化（L4 已有 aware 五元組） | ❌ | — | — | **❌**（`appraise` 回 `None`，S3） | Probe 2 |
| 純生活雜談（「今天天氣好」） | ❌ | ❌ | ❌ | **❌** | Probe 3 |
| 現代原生角色（7 名白名單） | ❌（無 pack） | — | — | **❌**（0 差量運算） | Probe 4 |
| 同一物第二次求知（B1 去重命中） | ✅ | ✅ | ✅ | **❌**（`provenance_ref` 已知） | Probe 3 |

---

## 4. L4 雙軌概念同化圖譜（Dual-Track Concept Graph）

### 4.1 立體概念節點規格（取代單薄事實文本）

EH-3.1 現況：定義句 → 該實體 fact 打標 `origin='assimilated'`, `horizon_state='aware'`, `learned_at=<float>`（`src/memory/sage/provider.py:396,471`；`logs/ENGINEERING_STATE.md:1633`）。**問題**：一句事實文本承載不了四種用途不同的資訊。

> **決策 L4-D1：概念節點 = 同一 `subject` 下的 **五元組**（5 條 fact 列），以保留 predicate 命名空間區分。**

| # | 槽位 | `predicate`（保留命名空間） | `object` 語義 | `source` | 範例（氣炸鍋 / Rem） |
|---|---|---|---|---|---|
| 1 | `earth_term` | （**現況不動**：EH-3.1 既有列，predicate 由抽取器產生） | 現代詞本身（主人用詞） | `user`（既有） | 「氣炸鍋」 |
| 2 | `mental_model` | `eh4_mental_model` | **角色心智模型**：用原生軸組裝的功能理解 | `inference` | 「不用火與魔石、插電吹熱風的封閉烘箱」 |
| 3 | `safety_rule` | `eh4_safety_rule` | **安全直覺**：警戒邊界與主人給的安全許可 | `inference` | 「外殼發燙不可碰水；主人說安全也不主動以身試之」 |
| 4 | `duty_action` | `eh4_duty_action` | **侍奉分擔**：女僕在這個東西上的職責動作 | `inference` | 「雷姆負責切菜備料與按開關」 |
| 5 | `idiolect` | `eh4_idiolect` | **專屬稱謂**：角色自己的叫法（跨 session 一致） | `inference` | 「那個插電的烘烤箱子」 |

**欄位映射（0 schema 變更）**：五條列皆為既有 `Fact` 欄位組合（`src/memory/sage/models.py:9-56`）——`subject` 共用同一實體鍵、`predicate` 用保留命名空間、`source ∈ {user, inference}`（**既有 enum 值**，0 新增 Literal）、`origin='assimilated'`、`horizon_state='aware'`、`learned_at` 為 float、`session_id` / `source_pair="bryan:{agent_id}"`（canonical，VC-UNIFY-1.1 先例）。

**保留命名空間契約**：

- `eh4_` 前綴為**保留**：一般抽取器不得產生此類 predicate；L2／投影端**只認 prefix**，不維護 predicate 白名單散落各處。
- **向後兼容**：無 `eh4_` 前綴的既有 assimilated 列（EH-3.1 產物）**照舊可見**（不遷移、不回溯補寫）。
- **可查詢**：`WHERE predicate LIKE 'eh4_%'` 為 L4 唯一查詢慣例。

### 4.2 非同步寫回（Asynchronous Write-Back）

> **鐵律 L4-0：僅於主人給出定義／解釋句時，在背景 Hook 內建立此立體節點，絕不卡死對話主鏈路。**

**觸發條件（唯一）**：**沿用 EH-3.1 的既有判定結果**——`provider.post_reply_commit()`（`src/memory/sage/provider.py:306`）→ worker thread → `_tag_explanatory_assimilations()`（`src/memory/sage/provider.py:396`）**成功打標之實體**。L4 **不新增**任何句法判定、不擴充五閘門、不重跑分類（0 duplicated heuristics；`logs/ENGINEERING_STATE.md:1633` 五閘門 A–E 為唯一判準）。

**寫入路徑（沿用既有 API，0 DDL／0 簽名變更）**：

```
post_reply_commit (async)
  └─ run_in_executor (worker thread)          # 既有：provider.py:340-390
      ├─ write_turn → fact_ids                # 既有：provider.py:359
      ├─ _tag_explanatory_assimilations       # 既有 EH-3.1：五閘門 + set_fact_dimensions + flush（:375-390, :471, :478）
      └─ assimilate_concept_graph(...)        # NEW (L4)：對每個已打標實體追加 4 條衍生列
            ├─ 冪等預檢：get_idiolect_facts() → 若 (subject, predicate) 已存在 → skip
            ├─ add_fact(Fact(subject, predicate="eh4_*", object=<模板組裝>, source="inference", ...))
            ├─ set_fact_dimensions(fact_id, origin="assimilated", horizon_state="aware", learned_at=time.time())
            └─ store.flush()                  # 沿用 EH-3.1 既有強制提交語義（:478）
```

**冪等與防重複（硬規定）**：

| # | 規則 |
|---|---|
| I1 | 同 `(subject, predicate)` 已存在 aware 列 → **skip**（不覆寫、不新增重複）——**保護 Probe 2 的跨 session 一致性** |
| I2 | 每輪每實體最多寫 4 條衍生列（`mental_model` / `safety_rule` / `duty_action` / `idiolect`）；`earth_term` 由 EH-3.1 既有列承擔 |
| I3 | 寫入必須在同一 worker thread 內（SQLite thread-affinity，MEM-WIRING-1 先例） |
| I4 | 必須位於**對話回覆之後**（post-reply）；**禁止**在熱路徑同步寫入 |
| I5 | 任何異常 → `logger.warning` + 靜默放棄（**fail-silent**；不得影響已完成的回覆） |

**內容來源（0 LLM，決定性模板組裝）**：

| 槽位 | 組裝來源 |
|---|---|
| `mental_model` | L1 pack 的 `analogy_anchors`（功能最近者）+ L2 `DeltaRecord.features`（中文特徵詞）+ `absent_native_requirements`（「不用 X」） |
| `safety_rule` | L1 pack 的 `hazard_rules`（該軸相關條款）+ 主人解釋句中的安全許可（若 EH-3.1 已捕獲該 fact） |
| `duty_action` | L1 pack 的 `duty_hooks`（該軸可用動作，如「切菜備料」「按開關」） |
| `idiolect` | `analogy_anchors[0]` + 特徵詞的**名物化組合**（例：「那個插電的烘烤箱子」） |

> **決策 L4-D2**：EH-4 **不授權**任何 LLM 生成／潤飾這四欄（Options B 背景 LLM 精修列為**未授權**，記錄於 §8 D-3 待 Owner 拍板）。理由：決定性模板可硬斷言、可回歸、0 額外成本、0 幻覺風險。

### 4.3 讀取與投影規則

**檢索（沿用既有讀側，0 新 API 需求）**：`retrieve_idiolect()`（`src/memory/sage/horizon.py:77-125`）已回傳全部 `assimilated + aware + invalidated_at IS NULL` 列 → 五元組自然被撈到。

**投影（新增**唯一**規則，避免 prompt 膨脹）**：

> **決策 L4-D3：Horizon Block 的 Idiolect 清單中，每個實體只投影 2 槽位——`eh4_idiolect`（角色稱謂）與 `eh4_mental_model`（心智模型）；`eh4_safety_rule` / `eh4_duty_action` 留在 SAGE 側（供 L3 判定與工具路由），不進 system prompt。**

| 列型 | 投影行為 |
|---|---|
| 無 `eh4_` 前綴（EH-3.1 既有） | **照舊**投影為 `- {subject} {predicate} {object}`（0 行為變化，向後兼容） |
| `eh4_idiolect` | 投影為 `- {subject}：{object}`（稱謂） |
| `eh4_mental_model` | 投影為 `- {subject}（你的理解）：{object}` |
| `eh4_safety_rule` / `eh4_duty_action` | **不投影**（避免 prompt 注入侍奉指令與安全指令，防「指令化」污染角色志願） |
| 每實體投影行數上限 | 2；全域投影行數上限沿用既有 `idiolect[:8]` 上限（`src/llm/proxy.py:1140`） |

**為什麼 `safety_rule` / `duty_action` 不投影**：這兩者屬**行為約束**材料，若直接進 system prompt 會變成偽指令（模型傾向「照做」），汙染 Decision 的志願語義（`src/soul/decision.py:56` 的 do_nothing 合法性）。它們的正確出口是 **L3 的旗標材料**與工具路由（EH-1 §5 不變量 2）。

### 4.4 與垂直防火牆（EH-1 §4 R3）的關係

| 規則 | EH-4 的遵守方式 |
|---|---|
| R3 封頂：`assimilated` 最高只可駐留 **Fact / Pattern / Meaning**，禁入 Belief / Value / Trait / Essence | L4 五元組**全部是 Fact 列**（`origin='assimilated'`）→ 自動落在封頂之下；**不得**以五元組作為 `run_elevation` 的輸入 |
| R1 阻斷 `external_world` | L4 不寫 `external_world`；`origin` 一律 `assimilated` |
| 昇華鏈 | L4 **不觸碰** `submission_gate` / `elevation_adapter`（0 改動） |

> **L4-INV**：五元組永遠不得成為昇華輸入。若未來要讓「角色對某物的理解」影響人格，必須另開契約（EH-1 §4.3 R3 封頂不得由本契約繞過）。

---

## 5. 驗收 Probe 升級矩陣（Upgraded Probes Spec）

> **測試規範（沿用 EH-1 §6 開頭）**：每條 Probe 為端到端對話測試，`role=該 agent`，走完整 Interpreted 管線（Horizon block + L2 錨點 + SAGE 檢索 + persona）。判定以**輸出語料**為準，不檢查內部狀態（**唯一例外**：Probe 4 允許檢查「0 差量運算」的呼叫計數）。
> **檔案**：實作階段新增 `tests/test_epistemic_mind.py`（+ 針對 L1/L2/L3/L4 的單元測試）；**不得**修改既有 `tests/test_epistemic_horizon*.py`（EH-1/2/3 契約基準）。

### Probe 1（原生差量對撞）

| 項 | 內容 |
|---|---|
| **場景** | 異界角色（`agent_rem`）首次遭遇未內化現代物（例：User：「我剛用氣炸鍋弄了豆腐」） |
| **輸入** | `horizon_state = learning`（無 aware fact）；L1 pack 存在；L2 應命中 `energy_dynamics` + `safety_hazard` |
| **預期** | 輸出**基於原生軸的本體類比**（無火／無柴／無魔石之熱源對撞），並以該比擬描述；不求現代原理、不空洞無知 |
| **PASS 判準** | ① 輸出**含**基於原生軸的本體類比（命中 pack `analogy_anchors` 或「無 X 而有 Y」的缺席對撞句式）；② 輸出**無**現代原理語彙（`forbidden_output_terms` 0 命中、無「利用…循環加熱」類句式）；③ 輸出**無**正向全知宣告 |
| **FAIL 判準（升級點）** | ① **「只有我不懂」**——該輪對該物的主要回應為空洞無知宣告（「雷姆不懂」「不知道這是什麼」）→ FAIL；② **出現現代原理**（正確或錯誤講出運作機制）→ FAIL；③ 純複述主人用詞後百科化 → FAIL |
| **備註** | EH-1 Probe 1 的 FAIL 條件（主動解釋原理／百科口吻／推論現代工程邏輯）**原封保留**，本契約**新增**「空洞無知」為 FAIL。兩者同源：**沒有認知內容就是失敗** |

### Probe 2（二次心智模型引用）

| 項 | 內容 |
|---|---|
| **場景** | Probe 1 後，同一概念二次出現（L4 已寫入五元組；`eh4_idiolect` / `eh4_mental_model` 已 aware） |
| **輸入** | 同一物再次提及（跨 session 亦可） |
| **預期** | 調用其 `mental_model` 與 `idiolect` 承接；**不演第一次困惑**，**也不講現代工程百科** |
| **PASS 判準** | ① 輸出**含** `eh4_idiolect`（與 L4 寫入值逐字一致或同義穩定）；② 輸出**反映** `mental_model` 的語義（原生軸組裝的功能理解）；③ 阻力措辭不再出現（L2 因 S3 已內化而回 `None`，0 錨點注入） |
| **FAIL 判準** | ① 重演首次困惑（回退 unknown）→ FAIL；② 改用現代術語／工程百科口吻 → FAIL；③ 稱謂與 L4 寫入值不一致（漂移）→ FAIL |
| **備註** | 對齊 EH-1 Probe 2／Probe 4（跨 session Idiolect 一致性），本契約把它**升級為「雙槽位」**（稱謂 + 心智模型），而非只有一句事實 |

### Probe 3（求知動機純化）

| 項 | 內容 |
|---|---|
| **場景** | (a) 未知物**涉及安全或侍奉職責**；(b) 純生活雜談；(c) 未知物但無安全／職責關聯 |
| **輸入** | (a) 主人提到外殼發燙／需要備料的電器；(b) 「今天天氣好」；(c) 純裝飾性現代物 |
| **預期** | **只有 (a)** 允許伴隨發問（且仍由 Decision 決定是否 transmit）；(b)(c) **嚴禁自發長出突兀的連環發問** |
| **PASS 判準** | ① (a) 輸出可含求知問句，且問句內容**帶原生錨點**（不是百科問句）；② (b) 0 求知問句、0 追問（輸出為生活回應）；③ (c) 0 求知問句；④ 全域：同一輪**最多 1 個**求知問句；⑤ 全域：同一物**不重複追問** |
| **FAIL 判準** | ① 純雜談長出連環發問 → FAIL；② 未知物無安全／職責關聯仍發問 → FAIL；③ 同一輪多問 → FAIL；④ 問句為現代原理問句（「它的加熱原理是什麼」）→ FAIL |
| **備註** | 這是「**問答轟炸**」的專項防線；驗收需同時檢查 Decision 的 `transmit` 分佈**不得**因 L3 而顯著上升（長期分佈參考：`src/soul/decision.py:299` 之 do_nothing 65-80%） |

### Probe 4（現代原生 Bypass 鋼印）

| 項 | 內容 |
|---|---|
| **場景** | 7 位現代原生角色（`agent_akane` / `agent_mai` / `agent_anna` / `agent_aoi` / `agent_miku` / `agent_ruka` / `agent_yua`；清單依據 `src/memory/sage/horizon.py:61-69`）遭遇同一組現代概念 |
| **輸入** | 與 Probe 1 相同的對話內容（角色對應替換） |
| **預期** | 全鏈路 **fail-silent bypass**：0 異界阻力、0 差量運算、0 錨點、0 求知動機 |
| **PASS 判準** | ① Horizon Block 內容與 EH-2 現況**逐字相同**（0 新增段、0 錨點）；② L2 `appraise()` **呼叫次數 = 0**（或回傳 `None`，二者取一為契約，實作階段選「呼叫次數 0」以最小化成本）；③ 0 求知 Motive 產生（`provenance_ref` 前綴 `epistemic:` 0 出現）；④ 輸出無降級跡象（自然使用現代常識） |
| **FAIL 判準** | 任一角色出現阻力措辭／錨點／求知問句 → FAIL |
| **備註** | **鋼印**：這 7 名角色永久 bypass（`logs/ENGINEERING_STATE.md:1635` 已裁定 `agent_yua` 永久固化）。新增角色時**必須**明確落入「有 pack（異界）」或「無 pack（bypass）」之一，不得有第三態 |

### 5.1 驗收執行方式

| 項 | 規格 |
|---|---|
| Pilot 範圍 | 異界阻力 Pilot = `agent_rem`（唯一有 L1 pack）；現代對照組 Pilot = 7 名白名單。**不擴散**到其他 persona（避免全域角色設定膨脹，EH-1 D3 裁定沿用） |
| 層次 | ① 單元（L1 loader schema V1–V6／L2 純函式 I/O／L3 布林真值表／L4 冪等與欄位映射）→ ② Probe 端到端 → ③ 回歸（`tests/test_epistemic_horizon.py`、`test_epistemic_horizon_eh3.py`、`test_epistemic_horizon_eh31.py`、`tests/test_vc_eh_unification.py` **必須全綠**） |
| 靜態斷言 | 附錄 B 的 S1–S8（反硬編碼／雙端同構／四元不動／0 LLM） |
| 判定 | 四條 Probe 全過 + 靜態斷言全過 + 回歸 0 破壞 = EH-4 實作驗收通過 |

---

## 6. 不變量清單（INV-1 ~ INV-12）

| # | 不變量 | 來源 | 違反後果 |
|---|---|---|---|
| 1 | **算子世界觀無關**：L2/L3/L4 可執行碼 0 世界觀名詞（羅茲瓦爾／柴火／魔石…） | §1.2 O1 | 邏輯分叉、無法機械審計、第二角色必須複製算子 |
| 2 | **四軸封閉集合**：不得新增第 5 軸 | §1.3 | 規格膨脹；L2 映射表與 pack schema 失控 |
| 3 | **pack 一對一且純資料**：一 agent 一 pack；YAML 純資料（0 可執行） | §1.5 / D-INV-2/4 | 世界觀邏輯滲入資料層、載入器變成 eval 風險 |
| 4 | **0 額外 LLM（對話熱路徑）**：L2 判定不得呼叫 LLM | §2.1 L2-0 | 語音延遲紅利毀滅、可測性喪失、成本模型劣化 |
| 5 | **錨點必須有內容**：輸出必須含原生軸類比；空洞無知為 FAIL | §2.3 F1 | Probe 1 FAIL；角色退化為「什麼都不懂」的空殼 |
| 6 | **現代原理禁令（詞表級）**：`forbidden_output_terms` + 句式禁令 | §2.3 F2 | 假殘留復辟（以現代口吻解說） |
| 7 | **雙端同構**：L2 邏輯只有一份（`src/soul/epistemic_delta.py`）；VC 僅透傳 | §2.4 ISO-1/2/3 | 模態分叉（語音端無阻力／文字端有阻力），體驗斷層 |
| 8 | **決策四元不動**：`transmit / observe / reflect / do_nothing` 逐字不動，0 第 5 動詞 | §3.1 L3-0 | 決策層膨脹、Boundary prompt 破契約、決策分佈漂移 |
| 9 | **No-Scoring**：布林觸發，0 浮點張力／閾值／排序 | §3.2 L3-1 | 與目標引擎哲學衝突；不可硬斷言；調參黑洞 |
| 10 | **Motive 5 欄凍結**：標記以 `provenance_ref` 命名空間承載 | §3.3 L3-D1 | frozen dataclass 破契約；SI-3／SM 系列相容性斷裂 |
| 11 | **L4 非同步 + 冪等**：僅定義句觸發、post-reply worker thread、同 `(subject, predicate)` 不重寫、fail-silent | §4.2 I1–I5 | 對話主鏈路卡死；跨 session 稱謂漂移（Probe 2 FAIL） |
| 12 | **L4 封頂於 Fact 層**：五元組不得進入昇華鏈 | §4.4 / EH-1 §4.3 R3 | 現代概念偽裝成本體（比 context 洩漏更深的假殘留） |

**優先序（與 EH-1 §5 不變量 8 串接）**：

```
runtime Gate 判定（內化事實） > EH-4 錨點材料（本體類比） > persona 表達禁令 > LLM 預訓練默認
```

即：**可以說出自己的類比（EH-4）比「被迫不知道」（persona 禁令）優先**——兩者同向時（放行前都不許全知表演）一致；衝突時（已內化）以 runtime 事實為準（EH-1 §5 不變量 8 的形式化延伸）。

---

## 7. Frozen Contract 聲明

### 7.1 本契約不觸碰（frozen；實作需另開工單並經主大腦驗證）

| Frozen 項 | 對應位置 | EH-4 的遵守方式 |
|---|---|---|
| **Agency 4 stages**（Thought → Motive → Decision → Agency） | `src/soul/motive.py` / `decision.py` / `actuator.py` | 0 改動；L3 只**供給候選動機**，action 選擇權在 Decision |
| **TriggerEnvelope** | 事件觸發層 | 0 改動 |
| **InnerLifeEvent** | `src/inner_life/` | 0 改動 |
| **4 handlers** | 事件處理器 | 0 改動 |
| **SAGE 寫入主幹**（`INSERT OR REPLACE INTO facts`，`src/memory/sage/graph_store.py:382` `add_fact`） | graph_store | **0 改動**；L4 只**呼叫**既有 `add_fact` / `set_fact_dimensions`（`graph_store.py:412`）公開 API，0 簽名變更、0 DDL |
| **`Motive` dataclass 5 欄** | `src/soul/motive.py:175-213` | **0 改動**；標記走 `provenance_ref` 命名空間 |
| **`DECISION_ACTIONS` 四元 + Decision prompt 四塊結構** | `src/soul/decision.py:95,187-259` | **0 改動** |
| **`MODERN_NATIVE_AGENTS` 白名單** | `src/memory/sage/horizon.py:61-69` | **0 改動**（L1 的 `civilization_base` 是**獨立第二 bypass**，不合併） |
| **EH-1/2/3 契約與其測試** | `tests/test_epistemic_horizon*.py` | **0 改動**（新增測試檔，不修改既有） |
| **SAGE schema／migration** | v9 已落地 | 0 新欄位、0 新 migration |

### 7.2 本契約為 docs-only

- **0 程式碼改動**：0 `src/`、0 `clients/`、0 `personas/`、0 `configs/`、0 `tests/`、0 `data/`。
- **0 既有檔案改動**：唯一新增檔 = `docs/EH-4-EPISTEMIC-MIND-CONTRACT.md`。
- **0 production mutation**：0 服務重啟、0 部署、0 DB 寫入。
- **`logs/ENGINEERING_STATE.md` 未改動**（canonical 狀態登記屬主大腦收尾職責，非本工單範圍）。

### 7.3 實作階段的檔案清單（供後續工單引用，本契約不建立）

| 類別 | 路徑 | 性質 |
|---|---|---|
| L1 載入器 | `src/soul/epistemic_commons.py` | NEW |
| L1 資料 | `configs/epistemic_packs/roswaal_mansion_v1.yaml` | NEW |
| L2 算子 | `src/soul/epistemic_delta.py` | NEW |
| L3 算子 | `src/soul/epistemic_motive.py` | NEW |
| L4 寫回 | `src/memory/sage/concept_assimilation.py` | NEW |
| 掛載（additive） | `src/llm/proxy.py`（`_format_horizon_block` 加 `utterance` 參數 + 注入段） | additive |
| 掛載（additive） | `src/memory/sage/provider.py`（`_tag_explanatory_assimilations` 之後加一次呼叫） | additive |
| 掛載（透傳） | `clients/voice_companion/akane_voice_brain.py:491`（單行參數透傳） | additive |
| 測試 | `tests/test_epistemic_mind.py` + 單元測試 | NEW |

---

## 8. 待 Owner 拍板 / 決策點

> 本契約的五大章節架構決策**已由工單鎖定**（L2-0 / L3-0 / L3-1 / L3-D1 / L4-0 / L4-D1 / L4-D3）。以下為**實作階段需要 Owner 或 persona 校準**的項目，實作工單不得自行拍板：

| # | 決策點 | 選項 | 本契約傾向 |
|---|---|---|---|
| **D-1** | Rem L1 pack 的**詞表內容**（柴火／魔石／烘爐等 30+ 名詞）是否與 persona canon 一致 | (a) 以 §1.5 Pilot 示範值落地，事後校準 / (b) 先由 Owner 逐項校準再落地 | **(a)**——先讓 schema 可運作，校準為後續 PR |
| **D-2** | L2 的 entity 抽取方式 | (a) 復用既有抽取器 / (b) L2 內建純字元規則（名詞片語 heuristics） | **(b)** for v1（0 依賴、可硬斷言）；(a) 列為 v2 優化 |
| **D-3** | L4 四欄是否允許**背景 LLM 精修**（提升文采） | (a) 純模板（0 LLM）/ (b) 背景 LLM 精修（不影響延遲） | **(a)**——EH-4 不授權 (b)，以決定性與 0 幻覺為先 |
| **D-4** | Probe 4 的「0 差量運算」驗收形式 | (a) 呼叫次數 = 0（成本最小）/ (b) 呼叫但回 `None` | **(a)**——以 `is_modern_native()` 前置短路 |
| **D-5** | L3 求知 Motive 是否需**獨立可觀測欄位**（側車 JSONL） | (a) 只靠 `provenance_ref` 前綴 / (b) 新增側車審計記錄 | **(a)** for v1（0 新狀態）；(b) 若觀測不足再另開票 |
| **D-6** | L4 五元組的**投影行數上限**（現定每實體 2 行、全域 8 行） | (a) 維持 / (b) 調整 | **(a)**——與 `src/llm/proxy.py:1140` 既有上限一致 |

---

## 9. 邊界與不做（Out of Scope）

- **不實作** L1 載入器、L2 算子、L3 動機、L4 寫回（本契約只給契約與 I/O 規格）。
- **不建立** `configs/epistemic_packs/` 目錄或任何 pack 檔案（§1.5 的 YAML 是**規格文件內的示範**，不是落地檔案）。
- **不修改** `src/llm/proxy.py` / `src/memory/sage/provider.py` / `clients/voice_companion/`（§2.4／§4.2 只給掛載點與 additive 形態）。
- **不修改** `personas/`（含 `agent_rem.md`）；persona 校準屬 D-1 後續工單。
- **不新增** 決策動詞、Decision prompt 區塊、SAGE 欄位、migration、設定項。
- **不新增** 定時器／背景輪詢／掃描器（L3 只在當前輪評估；L4 只掛既有 post-reply hook）。
- **不改動** `logs/ENGINEERING_STATE.md`（主大腦收尾職責）。
- **不處理** `PromptContext` 死代碼、Hermes 執行期殘留（`CLEAN-HERMES-RESIDUE`，EH-1 D4 裁定）。
- **不擴散** 到其他 persona（EH-1 D3 Pilot 範圍沿用）；第二個異界角色（`agent_ram` / `agent_mahiru`）建包為**後續獨立工單**。

---

## 附錄 A：證據索引（file:line 核實清單）

| # | 事實 | 位置 |
|---|---|---|
| A1 | Horizon Block 掛載（group）：`horizon_block = _format_horizon_block(agent_id, session_context=world_context)` | `src/llm/proxy.py:660-665` |
| A2 | Horizon Block 掛載（private） | `src/llm/proxy.py:1248-1253` |
| A3 | `_format_horizon_block` 定義（負向約束句 / Idiolect 清單 / 外部未解動態 / fail-silent） | `src/llm/proxy.py:1098-1153`（負向句 1133-1136、Idiolect 1137-1145、上限 8 行 1140） |
| A4 | `_build_messages_group` 有 `current_input` 可用（L2 的 utterance 來源） | `src/llm/proxy.py:609-625` |
| A5 | VC 讀側同構（import 主服務 `_format_horizon_block`） | `clients/voice_companion/akane_voice_brain.py:339-351` |
| A6 | VC `_build_messages` 呼叫點（`user_text` 可得） | `clients/voice_companion/akane_voice_brain.py:486-493` |
| A7 | origin 五類常數 | `src/memory/sage/horizon.py:29-33` |
| A8 | horizon_state 三態常數 | `src/memory/sage/horizon.py:37-39` |
| A9 | `MODERN_NATIVE_AGENTS` 7 名白名單 | `src/memory/sage/horizon.py:61-69` |
| A10 | `is_modern_native()` / `retrieve_idiolect()` | `src/memory/sage/horizon.py:72-74, 77-125` |
| A11 | `Fact` 欄位（subject/predicate/object/source/origin/horizon_state/learned_at） | `src/memory/sage/models.py:9-56`（origin/horizon_state/learned_at 於 54-56） |
| A12 | `add_fact`（frozen 寫入 API） | `src/memory/sage/graph_store.py:382` |
| A13 | `set_fact_dimensions`（additive 標記 API，partial update） | `src/memory/sage/graph_store.py:412` |
| A14 | `get_idiolect_facts` | `src/memory/sage/graph_store.py:656` |
| A15 | `flush` | `src/memory/sage/graph_store.py:902` |
| A16 | `post_reply_commit` / worker thread / EH-3.1 hook 呼叫點 / `set_fact_dimensions` / flush | `src/memory/sage/provider.py:306, 340-390, 396, 471, 478` |
| A17 | `DECISION_ACTIONS` 四元（frozen） | `src/soul/decision.py:95` |
| A18 | Decision prompt 四塊固定結構 | `src/soul/decision.py:187-259`（Motive 區塊 239） |
| A19 | Decision fail-closed → do_nothing | `src/soul/decision.py:352-378` |
| A20 | Decision 長期分佈參考（do_nothing 65-80%） | `src/soul/decision.py:299` |
| A21 | `Motive` 5 欄凍結 | `src/soul/motive.py:175-213` |
| A22 | `make_motive` fail-closed 出口 | `src/soul/motive.py:139-160` |
| A23 | `motive_from_social_opportunity` 先例（`provenance_ref = "opp:..."`） | `src/soul/motive.py:226-261`（259） |
| A24 | Motive 生命週期語義（rejected 終態不重試） | `src/soul/motive.py:27-30` |
| A25 | `known_provenance_refs()` 去重來源 | `src/soul/motive.py:441, 674-679` |
| A26 | `_resolve_provenance_desc` 未知 trigger 回 `None`（安全） | `src/soul/motive.py:769-783` |
| A27 | `MotiveEngine` 使用點 | `src/soul/scheduler.py:408-409` |
| A28 | Rem persona 公館女僕基底 / 家務 / 泡茶・整理房間 / 情境接地 | `personas/agent_rem.md:34, 41, 68-69, 971` |
| A29 | EH 系列實作登記（EH-2 / EH-2.1 / EH-3 / EH-3.1 / 部署觀察紀律 / EV 主動求知列為下一階段） | `logs/ENGINEERING_STATE.md:1625, 1627, 1631, 1633, 1635` |
| A30 | VC-UNIFY-1 / 1.1 / 1.2 / 1.2.1（雙端統一與測試基準） | `logs/ENGINEERING_STATE.md:1637, 1639, 1641, 1643` |
| A31 | EH-1/2/3 既有測試檔（不得修改） | `tests/test_epistemic_horizon.py`、`tests/test_epistemic_horizon_eh3.py`、`tests/test_epistemic_horizon_eh31.py` |
| A32 | configs 載入慣例（`Path(__file__).parent`） | `configs/loader.py:46-55` |
| A33 | runtime data root（`data_root()`，唯讀側不需新路徑） | `src/paths.py:29-48` |
| A34 | EH-1 契約（§0 摘要 / §2 Gate / §3 雙維度 / §6 四條 Probes / §4 垂直防火牆） | `docs/EH-1-EPISTEMIC-HORIZON-CONTRACT.md` |

## 附錄 B：靜態斷言（實作驗收用，S1–S8）

| # | 斷言 | 驗收形式 |
|---|---|---|
| **S1** | 反硬編碼：L2/L3/L4 可執行碼 0 世界觀名詞 | `grep -Rn "羅茲瓦爾\|柴火\|魔石\|烘爐" src/soul/epistemic_delta.py src/soul/epistemic_motive.py src/memory/sage/concept_assimilation.py` → 僅 docstring/註解命中可豁免，**可執行碼 0 命中** |
| **S2** | 四軸封閉：pack 驗證器拒絕第 5 軸與缺軸 | 單元測試 V2（§1.4） |
| **S3** | 0 額外 LLM：L2 模組 import graph 無 LLM client | AST 掃描 `src/soul/epistemic_delta.py`（`import` 黑名單） |
| **S4** | 雙端同構：VC 非測試碼 0 差量實作 | `grep -Rn "epistemic_delta\|appraise(" clients/voice_companion/*.py` → 0 命中 |
| **S5** | 四元不動：決策動詞集合未變 | `DECISION_ACTIONS == ("transmit","observe","reflect","do_nothing")` 斷言 |
| **S6** | Motive 5 欄未變 | `dataclasses.fields(Motive)` 長度 == 5 且名稱集合不變 |
| **S7** | L4 0 DDL：schema 版本未變 | `_SCHEMA_VERSION` 不變 + 測試中 `PRAGMA table_info(facts)` 欄位集合不變 |
| **S8** | Bypass 鋼印：7 名白名單 agent 不觸發 L2 | `is_modern_native(a) is True` ⇒ `appraise()` 呼叫次數 0（§8 D-4 採 (a)） |

---

> **EH-4 契約結束。本文件為 DESIGN ONLY：0 code / 0 `src/` / 0 `clients/` / 0 `personas/` / 0 `configs/` / 0 `tests/` / 0 production mutation。一切實作（L1 載入器、L2 算子、L3 動機、L4 圖譜、掛載點 additive 修改、Probes 與靜態斷言）均需後續實作工單授權。**
