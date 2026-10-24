# FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE — 實作紀錄（離線原型 ＋ 量測）

> **狀態**：分支**乙**（規格候選仍 = 0）｜**零生產變更、零 schema 變更、零引擎接線**｜待 Owner 審定
> **工單**：`logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-WORK-ORDER.md`
> **母案**：`docs/FEAT-SCHEMA-CONTRADICTION-RFC.md`／`logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md`（Phase 1）
> **量測時點**：2026-10-24T18:38:20.583454+00:00（＝2026-10-24 14:38 EDT）
> **解譯器**：`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`（repo 內 `.venv`，Python 3.11.15）
> **施工位置**：`%TEMP%\sup_wt`（detached HEAD `c9ec5e8`）——**主 repo 一行未改**

---

## 0. 一句話結論

**正規路徑（解開 `superseded` 死鎖鏈路）可行，但缺口不在 Phase 2 的「接線」，而在更上游：**
**系統從未產出過「同一來源、不同版本」的命題**（生產圖譜上 `lineage_depth ≥ 2` = 0／1056 筆、
父為靈魂節點 = 0／1056 筆 ⇒ 兩顆靈魂節點**永不共根** ⇒ 探針恆 0 組候選）。
⇒ **戰役二（召回池長大）的前置不是「把累積器接上」，而是「先讓『同源不同版命題』這種事件在生產上發生」。**

---

## 1. 改動檔案清單（**恰 5 檔**；對照工單 §4.1）

| # | 檔案 | 動作 | 行數 | bytes |
|---|---|---|---|---|
| 1 | `src/inner_life/contradiction_accumulator_prototype.py` | **新增**（A-3 累積器饋入判定器；純函式、零 IO／零 LLM、零門檻常數） | 219 行（非空 179） | 10,525 |
| 2 | `tests/inner_life/test_contradiction_accumulator_prototype.py` | **新增**（守衛**恰 6 筆** S1～S6） | 289 行（非空 248） | 12,508 |
| 3 | `logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-REPORT.md` | **新增**（本檔） | 622 行 | 44,402 |
| 4 | `logs/ENGINEERING_STATE.md` | **追加**條目（canonical 不覆寫；§0.1.68） | `+150 −0` | 1,647,599 → 1,657,930 |
| 5 | `tests/test_observability_completeness_1.py` | **修改**（`allowed` 逐票新增 1 筆；沿用 `contradiction_probe.py` 的既有列舉風格） | `+11 −0` | 45,369 → 46,212 |

`git status --short`（實作完成時）＝ 2 個 ` M` ＋ 3 個 `??`（未追蹤新檔）＝ **恰 5 檔**。
**未** `git add`／`commit`／`push`（依工單 §2.4.7）。**主 repo 一行未改**（本票全程在 `%TEMP%\sup_wt`；
主 repo `git status --short` 只有主大腦自己的 7 份未追蹤工單 md，**無任何已追蹤檔被改動**）。

> ⚠️ 新增的 3 個檔案是 **untracked** ⇒ `git diff --name-only HEAD` 實得**只有 2 檔**
> ⇒ 見 §8 的**三份 diff** 套用說明（diff A ＝ 已追蹤檔的修改；diff B／C ＝ 兩個新增檔）。

---

## 2. A-1：唯讀重測 Q1～Q6（**最新生產資料**）

### 2.0 量測宇宙（**工單寫的 1054／2275 已過期**）

| 檔案 | rows | bytes | sha256（量測前 ＝ 量測後） |
|---|---|---|---|
| `elevation_nodes.jsonl` | **1056** | 808,689 | `ec89be989895694b4d93d3b57280619509622824382d4db36eb9520b28405ce3` |
| `elevation_edges.jsonl` | **2277** | 885,800 | `944e8952f6a615aadcd83734d8e027993212e78d92772ac14ff9ce311df4bf48` |
| `elevation_lifecycle_events.jsonl` | 271 | 99,493 | `b5ed3b852aaf453ec2213329234f00d2d1964b8ae6c2a5e7ba3e71a219f44f8b` |
| `elevation_trace.jsonl` | 10,415 | 41,170,594 | `7ca82fcf84e210d25c5c1bee38691b8af79f8b8287025bc1c2060f20728550ac` |

🔴 **四檔量測前後 sha256 逐位元相同**（唯讀自證，見 §9）。
🔴 **未**對生產 sqlite 開任何連線（含 `mode=ro`）；四檔皆以純 JSONL 唯讀讀取。

**母體差異（需標示）**：工單 §0／§2.1 寫「當前母體＝1054 節點／2275 邊」（引自召回池診斷 2026-10-24 12:05Z）；
本票量測時（18:38Z）實測為 **1056／2277**。拆解：pattern **827 → 829**（＋2）、
value 213／belief 9／trait 5 **不變** ⇒ 增量**全在 pattern**（生產服務持續運作）。
⇒ 本報告的一切數字以 **1056／2277** 為準（**不沿用工單寫死的數字**）。

### 2.1 Q1 — 規格候選矛盾組數（分子）／符合前提的 `(N, e)` 配對總數（分母）

| 分子／分母／單位 | 值 |
|---|---|
| 分子（規格候選矛盾組數） | **0** 組 |
| 分母（符合前提的 `(N, e)` 配對總數） | **364** 組 |
| 比值 | **0／364 ＝ 0.0000**（組／配對） |

**篩選鏈（逐級存活數）**：

| 級 | 判準 | 存活 |
|---|---|---|
| 0 | `E(N)`（建立之後到達且仍有效的支持邊） | **364／364** |
| 1 | ＋ 同 agent ＋ 同 `source_id` 有對應靈魂節點 | **69／364** |
| 2 | ＋ **同一 lineage 根** | **0／69** |
| 3 | ＋ `content` 逐位元不等 | **0／0**（已清零，無定義） |

⇒ **唯一把配對清零的判準是「同一 lineage 根」**（與 Phase 1 同一成因）。

### 2.2 Q2 — 分佈

- **規格候選（0 組）**：agent／node_type／`ts` 最早／最晚／跨度／不同模擬日數 ⇒ **全部無定義**（0 筆）。
- **【診斷】近門配對（69 組，僅差「同一 lineage 根」）**：

| 項 | 值 | 單位／分母 |
|---|---|---|
| agent 分佈 | ruka 8、anna 9、aoi 8、miku 8、ram 8、rem 9、akane 5、mahiru 6、mai 7、**minami 1** | 組／69 |
| `node_type`（`N` 側） | `value` **69／69**（belief 0、trait 0） | 組／69 |
| `ts` 最早 | `2026-10-21T12:00:15.450271+00:00` | UTC ISO |
| `ts` 最晚 | `2026-10-24T12:01:46.570512+00:00` | UTC ISO |
| `ts` 跨度 | 3 天 0:01:31.120241 ＝ **3.001 天** | 天 |
| 不同模擬日數 | **4**（10-21／10-22／10-23／10-24） | 日 |
| `e.trigger_type` | `support_propagation` **69／69** | 組／69 |

### 2.3 Q3 — Q1 ＝ 0 的成因 ＋ 結構性證據

**成因（逐條）**：

| 判準 | 結果 | 是否成因 |
|---|---|---|
| 步驟 1 `E(N)`（時間門檻） | 364／364 通過 | ❌ 不是 |
| 步驟 2「同 agent ＋ 同 `source_id` 找另一靈魂節點」 | 69／364 有對應節點 | ❌ 不是 |
| 步驟 2「**同一 lineage 根**」 | **69／69 在此被濾掉** | ✅ **唯一成因** |
| 步驟 3 `content` 逐位元不等 | 近門 69／69 的 content **全部相異** | ❌ 不是 |

**結構性證據（最新資料）**：

| 項 | 值 | 分母 |
|---|---|---|
| 靈魂節點總數（value／belief／trait） | **227** | 227 |
| `lineage_depth` 分佈 | **`{1: 227}`**（**無 depth ≥ 2**） | 227 |
| 父節點型別分佈 | **`{pattern: 227}`**（父**皆**為 pattern） | 227 |
| 父為靈魂節點者 | **0** | 227 |
| 相異 lineage 根數 | **227** | 227（**1:1，無共用根**） |

⇒ 兩顆**互不相同**的靈魂節點在現行資料形狀下**永不共根**（除非其中一顆是另一顆的 revise 子節點；**現行 0 筆**）
⇒ 該判準恆不成立 ⇒ 候選恆 0 ⇒ **死鎖不會因規格判準而解除**。

### 2.4 Q4 — 以引擎既有計數器逐節點算門檻（**零新增、零調低**）

門檻（**引用**引擎既有常數，未新增／未調整）：
`LIFECYCLE_N_SUPERSEDE=3`／`LIFECYCLE_ESSENCE_N_SUPERSEDE=5`／`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD=2`
（`soul_elevation/prior.py:68-73`；🔴 **引擎在 repo 外，未改**）。

- **規格候選（0 組）**：所有節點獨立矛盾數＝**0**、跨模擬日數＝**0**
  ⇒ **0／227 節點達門檻**（獨立數門檻 0／227；跨日門檻 0／227；兩者皆達 0／227）。

- **【診斷】僅放寬「同一 lineage 根」一條**（**非本票設計**；僅供 Owner 判讀可行性）：
  受影響 **21** 顆節點；達獨立數門檻 **15／21**；達跨日門檻 **15／21**；兩者皆達 **15／21**。
  （逐節點明細見 `logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-REPORT.md` 附錄／量測原始輸出。）

  ⚠️ 此表**僅供 Owner 判讀可行性**，**非本票設計主張**：近門 69 組 **100%** 來自 REINFORCE
  `support_propagation` ⇒ 那是「**命題再確認**」而非反例 ⇒ **放寬會語意誤判**
  （Owner 已明確拒絕「為了讓數字過關而修改判準」）⇒ **本票未放寬任何判準**。

### 2.5 Q5 — 與既有 REINFORCE `support_propagation` 邊的重疊

| 項 | 值 | 單位／分母 |
|---|---|---|
| 生產 `support_propagation` 邊 | **69** | 筆／2277 |
| 規格候選覆蓋之邊 | **0** | 邊（候選集合為空） |
| **規格口徑重疊組數** | **0／0 ＝ 無定義** | 組 |
| 近門配對用到 `support_propagation` 邊 | **69／69 ＝ 1.0000** | 組／69 |

### 2.6 Q6 — `agent_akane` 單獨列出

| 項 | 值 | 單位／分母 |
|---|---|---|
| 靈魂節點數 | **12** | 筆 |
| `E(N)` 配對數（分母） | **44** | 組 |
| **規格候選（分子）** | **0** | 組／44 ＝ **0.0000** |
| 近門配對 | **5** | 組／44 ＝ 0.1136 |
| 對應節點 | `7906aef95d55`（`value`）vs `a6aad545683c`／`a0e77f23ae95`／`1408be73efa34`／`9f74b9763bc9`／`e2809538cfc6` | — |
| `ts` | 2026-10-21T12:00:25.792506／10-22T02:00:36.251867／10-22T12:00:13.084868／10-23T02:01:03.724317／10-24T12:00:06.482474 | UTC ISO |
| `e.trigger_type` | `support_propagation` **5／5** | 組／5 |
| **【診斷】** | 獨立矛盾數 **5／3**、跨模擬日數 **4／2** | 達門檻＝是／是 |

---

## 3. 與 Phase 1 基線（793 節點／1791 邊，2026-10-23T02:40Z）逐項對照

> 對照基準：Phase 1 的實測記錄（`logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md` §4）。
> **Q1～Q6 的量測邏輯本票沿用 Phase 1 的同一支腳本**（`%TEMP%\featchema_measure.py` 的 Q1～Q6 段**逐字複製**）
> ⇒ 數字**同口徑**、可直接相減。

| # | 項 | Phase 1（793／1791） | 本票（1056／2277） | 變化 | 為什麼 |
|---|---|---|---|---|---|
| 1 | 節點總數 | 793 | 1056 | ＋263（＋33.2%） | 生產服務持續產出 |
| 2 | 邊總數 | 1791 | 2277 | ＋486（＋27.1%） | 同上 |
| 3 | 靈魂節點（value＋belief＋trait） | 136 | 227 | ＋91（＋66.9%） | pattern 657→829，昇華量同步長大 |
| 4 | 節點型別分佈 | pattern 657／value 125／belief 6／trait 5 | pattern 829／value 213／belief 9／trait 5 | value ＋88、pattern ＋172 | value 成長最快（＋70%）⇒ 命題數在漲 |
| 5 | **`E(N)` 配對（Q1 分母）** | 343 | **364** | ＋21（＋6.1%） | 邊的成長遠快於「建立後到達的有效支持邊」 |
| 6 | **規格候選（Q1 分子）** | **0** | **0** | **不變** | 🔴 前提**未**改變 |
| 7 | 篩選鏈第 1 級（同 agent＋同 source） | 48／343 | 69／364 | ＋21（＋43.8%） | REINFORCE 傳遞邊同步長大 |
| 8 | **篩選鏈第 2 級（＋同一 lineage 根）** | **0／48** | **0／69** | **不變** | 🔴 仍**唯一**清零點 |
| 9 | `lineage_depth` 分佈（靈魂） | `{1: 136}` | **`{1: 227}`** | **不變（仍全 1）** | 🔴 **無任何 revise／supersede 鏈** |
| 10 | 父節點型別（靈魂） | `{pattern: 136}` | **`{pattern: 227}`** | **不變（仍全 pattern）** | 同上 |
| 11 | 父為靈魂節點者 | 0 | **0** | 不變 | 同上 |
| 12 | 相異 lineage 根／總數 | 136／136 | **227／227** | 不變（仍 1:1） | 同上 |
| 13 | `support_propagation` 邊 | 48 | 69 | ＋21（＋43.8%） | 與第 7 級同步（近門配對＝REINFORCE 邊） |
| 14 | 近門配對用 `support_propagation` | 48／48 ＝ 1.0 | **69／69 ＝ 1.0** | **不變（仍 100%）** | 🔴 語意風險**未**下降 |
| 15 | 診斷：達獨立數門檻 | 9／15 | **15／21** | ＋6 節點 | 支援邊累積變多 |
| 16 | 診斷：達跨日門檻 | 15／15 | **15／21** | 比例 100% → 71.4% | 新節點多數還沒跨滿 2 個模擬日 |
| 17 | 診斷：兩個門檻皆達 | （Phase 1 未單列） | **15／21** | — | 見 §4.2 的 A-3 量化 |
| 18 | `agent_akane` 靈魂節點 | 10 | 12 | ＋2 | — |
| 19 | `agent_akane` `E(N)` 配對 | 43 | 44 | ＋1 | — |
| 20 | `agent_akane` 規格候選 | 0 | **0** | 不變 | 同第 6 項 |
| 21 | `agent_akane` 近門配對 | 4 | 5 | ＋1 | — |
| 22 | 近門配對 `ts` 跨度 | 1.584 天 | **3.001 天** | ＋1.417 天 | 量測窗口多一天 |
| 23 | 近門配對不同模擬日數 | 3 | **4** | ＋1 日 | 同上 |
| 24 | 近門配對涉及的 agent | 9 個 | **10 個**（新增 `minami`） | ＋1 | 新 agent 上線 |
| 25 | `lineage_depth ≥ 2` 節點（全體） | （未量） | **0／1056** | — | 🔴 關鍵新量測（§4.1） |
| 26 | `parent → 靈魂節點`（全體） | （未量） | **0／1056** | — | 🔴 同上 |
| 27 | `superseded_by` 非 null | （未量） | **0／1056** | — | 🔴 同上 |
| 28 | `contradiction_pressure` 非空 | （未量） | **0／1056** | — | 累積器**恆空**（死鎖的直接證據） |

### 3.1 對照結論（**Phase 1 的前提是否已變？**）

**沒有變。** 資料長了 ＋33%（節點）／＋27%（邊），靈魂節點長了 ＋67%，
但**「同源不同版命題」的存在性指標一格都沒動**：
`lineage_depth` 仍全 1、父仍全 pattern、父為靈魂仍 0、相異根仍 1:1、規格候選仍 0。
⇒ **Phase 1 的結論在最新資料上完整重現，且結論的「不變性」本身已被量測證明**。

---

## 4. A-2：前提判定 ＝ **分支乙**（規格候選仍 = 0）

### 4.1 分支乙交付 ①：重現 Phase 1 的結構性證據（最新資料）

| 結構性指標 | Phase 1 | 本票 | 結論 |
|---|---|---|---|
| `lineage_depth` 是否仍恆 1 | 是（136/136） | **是（227/227）** | ✅ 重現 |
| 父是否仍皆 pattern | 是（136/136） | **是（227/227）** | ✅ 重現 |
| 相異 lineage 根是否仍 ＝ 總數 | 是（136/136） | **是（227/227）** | ✅ 重現 |
| 「同一命題的不同版本」鏈是否存在 | 否 | **否** | ✅ 重現 |

**上游缺口的第一手證據（全體 1056 節點）**：

| 量 | 值 | 分母 |
|---|---|---|
| `lineage_depth ≥ 2` 的節點 | **0** | 1056 |
| `parent_node_id` 指向**靈魂節點**的節點 | **0** | 1056 |
| `superseded_by` 非 null 的節點 | **0** | 1056 |
| `lifecycle_state == "superseded"` 的節點 | **0** | 1056 |
| `reconsideration_candidate == true` 的節點 | **0** | 1056 |
| `contradiction_pressure` 非空的節點 | **0** | 1056 |
| revise 類**真** lineage 事件（`node_revised` 且 `parent_node_id` 非 null） | **0** | 69 筆 `node_revised` trace 事件 |

🔴 **一個必須寫進報告的命名陷阱**：`elevation_trace.jsonl` 裡有 **69 筆 `node_revised`**，
但**全部 69 筆**都是 `reason = "reinforced_only"`、`parent_node_id = null`、`lineage_depth = 1`
⇒ 全部來自 **`engine.reinforce()`**（`soul_elevation/engine.py:1122`），
**不是** `engine.revise()`（`engine.py:777`，該處會寫 `parent_node_id = old.node_id`）。
**只 grep `node_revised` 會誤判「revise 鏈已存在」** —— 這是本票抓到的一個真實陷阱。

**生命週期 sidecar**（`elevation_lifecycle_events.jsonl`，271 筆）：`quarantine` 18 筆
（active→dormant）、`lifecycle_transition` 253 筆（active→weakening）、
**轉移到 `superseded` 的事件 0／271 筆**。
（`elevation_nodes.jsonl` 的 `lifecycle_state` 欄位全為 `active` 1056／1056 ——
生命週期狀態的權威面是 sidecar，節點檔的欄位不回寫。）

### 4.2 分支乙交付 ②：用 A-3 判定器量化「**差多少**」

| 候選來源 | 受評節點 | 達獨立數門檻 | 達跨日門檻 | 兩個皆達 |
|---|---|---|---|---|
| **規格候選**（探針輸出，本票判準） | 227 | **0／227** | **0／227** | **0／227** |
| 【診斷】近門組（**機制示範**，非規格候選） | 227 | 15／227 | 15／227 | 15／227 |

**差距分佈（規格候選）**：
獨立矛盾數 gap ＝ **3**（**227／227** 顆節點都是 3）；跨模擬日數 gap ＝ **2**（**227／227**）。
⇒ **不是「差一點」，是每顆靈魂節點都差「3 個獨立矛盾 ＋ 2 個不同模擬日」的滿額**。

**差距分佈（近門診斷組）**：獨立數 gap `{−2: 9, 0: 6, 2: 6, 3: 206}`；
跨日 gap `{−2: 9, −1: 6, 1: 6, 2: 206}`。

### 4.3 分支乙交付 ③：**誰能產出 revise 鏈**，為什麼在生產上沒發生

| 機制 | 位置 | 產生的 lineage 形狀 | 生產呼叫點 |
|---|---|---|---|
| `engine.revise()` | `soul_elevation/engine.py:641` | `parent_node_id = old`、**`lineage_depth = old + 1`**、新 `lineage_path` ⇒ **父子皆為靈魂節點** | **0 個**（全 repo 連 `scripts/` 都無呼叫） |
| `engine.supersede()` | `soul_elevation/engine.py:1140` | 同上，且舊節點 `lifecycle_state = "superseded"` | **0 個生產呼叫點**（唯一呼叫者是 `scripts/sim_accel_recall_cli.py` 這支**手動沙盒 CLI**，**不是**生產路徑；且 `supersede()` 本身**循環阻塞**） |

**為什麼沒發生（三個層面的答案）**：

1. **路徑不存在（不是被條件擋掉）**：
   `src/inner_life/elevation_adapter.py` 是 Soul OS 側唯一接觸 `soul_elevation` 的地方，
   它只呼叫 `consume()`／`elevate()`／`reinforce()`——
   **全 repo `git grep revise(` 的命中只有文件敘述**（`docs/ELEVATION-EVIDENCE-CONTRACT.md`），
   **`src/` 與 `scripts/` 皆無任何呼叫點**。
   ⇒ `revise()` 在生產上是**沒有線的引擎 API**。
   （`record_contradiction()`／`supersede()` 唯一的呼叫者是 `scripts/sim_accel_recall_cli.py`
   —— **手動沙盒 CLI**，非生產排程；`SIM-ACCEL-RECALL-30D` 票用它示範過「記矛盾 → 取代」的完整路徑。）
2. **事件來源沒有**：生產目前的事件來源是「記憶／日記／夢／世界事件 → pattern → 昇華 → 靈魂節點」。
   這條鏈**只會新增節點**（`elevate()` 產 `parent = pattern`、`lineage_depth = 1`），
   **從不改寫既有命題** ⇒ 「同源不同版命題」這種資料形狀**在生產上不可能出現**。
3. **就算接上 `revise()` 也不會直接通**（**本票實測**）：
   - `revise()` **不**把舊節點的原 `source_ids` 帶到新節點（只掛「觸發修訂的新來源」）；
     而 `supersede()` **有**把舊邊的 `source_id` 複製到新節點（`engine.py:1244-1258`）
     —— 但 `supersede()` 會把舊節點標成 `superseded`，而**探針排除 `superseded` 節點**
     ⇒ **以現行探針判準，`supersede()` 產生的鏈結構上無法回饋給探針**。
   - `revise()` 產生的新邊 `valid_from_ts == created_ts` ⇒ **不滿足**探針的
     「`valid_from_ts > created_ts`」⇒ revise **當下**不產生任何 `E(N)`。
   ⇒ 走 `revise()` 這條路是**唯一**可能，但需要**額外**的資料形狀（§4.4）。
4. **LLM 現況（僅供裁示參考）**：生產 adapter 注入的是 `StubElevationLLM()`（確定性樁），
   `revise()` 的重估會走 `self._llm.classify()` ⇒ 即便接線，「重估」也不會是真實 LLM 推論。

### 4.4 分支乙交付 ④：**「讓召回池合法長大」的最小上游缺口描述**（**只描述、不實作**）

本段用**真實引擎的 `revise()` ＋ 真實探針**在**沙盒**（合成資料，**機制示範**）
逐形狀量測，證明「什麼資料形狀才會讓探針真的吐出候選」：

| 形狀 | 內容 | 探針候選 |
|---|---|---|
| **A**（對照組 ＝ 現行生產形狀） | 兩顆靈魂節點各有自己的 pattern 根、不同 source | **0** 組 |
| **B** | **真實 `engine.revise()`** 產出的 revise 鏈（父＝靈魂節點、`lineage_depth = 2`） | **0** 組 |
| **C** | revise 鏈 ＋ **revise 之後**落在新版本 N' 的新支持邊，且該邊的 `source_id` **與舊節點共用** | **1** 組 |
| **D** | revise 鏈 ＋ revise 之後落在 N' 的新支持邊，但 `source_id` **不**與舊節點共用 | **0** 組 |
| **E** | revise 鏈 ＋ **3 條**共用 source、**分屬 3 個不同模擬日**的新支持邊 | **3** 組 |

**形狀 E 的判定器輸出（用 A-3 判定器算）**：
**獨立矛盾數 3／3、跨模擬日數 3／2 ⇒ 達門檻＝是**。

**形狀 C 的實際候選（原始輸出）**：
`N = 32914b2094d8430b82102b9e6cf91d41`（改寫後的新版本）vs
`N' = soulB`（改寫前的舊版本）、`source_id = src-shared`、`ts = 2026-10-25T18:46:52.623493+00:00`
⇒ 探針把候選掛在**新版本**上、對手是**舊版本**（兩者同源同根、內容相異）。

🔴 **因此「正規路徑可行」已被證明**：**不必放寬任何判準、不必調低任何門檻**，
只要生產上真的出現形狀 E 的資料，累積器就會滿、`supersede()` 就會產生 `superseded` 節點。

⇒ **最小上游缺口（一句話）**：

> **需要一個「同源不同版命題」的事件來源**：把既有靈魂命題 **N** 改寫成新版本 **N'**
> （`parent_node_id = N`、`lineage_depth ≥ 2`），**並且**在改寫**之後**讓**同一個上游來源**
> （同一 `memory_id`／`fact_id`／`event_id`）以一條**新的、`valid_from_ts` 晚於 `N'.created_ts`** 的
> 支持邊再次落到 **N'** 上；且為了跨過 `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD = 2`，
> 這樣的「同源不同版」必須在 **≥ 2 個不同模擬日**上各發生一次
> （⇒ 每顆節點需要 **≥ 3 個相異 `source_id`**、分屬 **≥ 2 個模擬日**）。

**上游缺口的最小工程形狀（三件，缺一不可）**：

| # | 缺口 | 現況 | 誰該做 |
|---|---|---|---|
| G-1 | **產生 revise 鏈的呼叫點**（把既有命題改寫成新版本，父＝靈魂節點） | `engine.revise()` **零生產呼叫點** | 一個「偵測到命題被改述」的判定層呼叫 `engine.revise()` |
| G-2 | **舊 `source_id` 的承接**（讓同一來源同時掛在舊版與新版上） | `revise()` **不**承接舊 `source_ids` | revise 呼叫端把 N 的原 `source_ids` 一併傳入 `revise(source_ids=...)`，或在寫入端複製舊邊 |
| G-3 | **改寫後的跨日再支持事件**（`valid_from_ts > N'.created_ts` 且跨 ≥2 個模擬日） | 生產無此事件 | revise 之後的支援傳遞／再確認機制，須能把**同一 source** 在**後續模擬日**再次落到 N' |

🔴 **三件都只是「描述」**：本票**未實作**其中任何一件，**未**接線，**未**改引擎。

### 4.5 🔺 若接線，池會變成 N？

**分支乙不適用工單 §4.6 的「若接線池會變成 N」格式**（那是分支甲的交付）。
但本票給出**等價的沙盒量測**：

> **在沙盒（合成資料、機制示範）**，若上游真的產出形狀 E 的資料並接上累積器，
> **單顆節點**即可滿足「獨立矛盾數 3／3 ＋ 跨模擬日數 3／2」⇒ 可產生 **1** 顆 `superseded` 節點
> ⇒ **召回池可從 0 變成 ≥1**。
> ⚠️ **這是沙盒量測，不是生產**：生產目前 0 組候選、0 顆達門檻（§2.4），池仍 ＝ **0**。

---

## 5. A-3：累積器饋入判定器（`src/inner_life/contradiction_accumulator_prototype.py`）

### 5.1 公開 API

```python
assess_accumulator(
    node_id: str,
    candidates: Sequence[Any] = (),        # 探針輸出（CandidateContradiction 或等價 dict）
    existing_pressure: Sequence[Any] = (),  # 該節點既有 contradiction_pressure
) -> AccumulatorAssessment
```

輸出欄位：`record_count`／`independent_contradictions`／`spread_days`／
`required_independent`／`required_spread_days`／`meets_independent_threshold`／
`meets_spread_threshold`／`meets_supersede_thresholds`／`independent_gap`／`spread_gap`。

### 5.2 硬性要求**逐條自證**

| 工單要求 | 自證 | 證據 |
|---|---|---|
| 🔴 **零門檻常數**（一律 `getattr(prior, ...)`） | 模組內**無任何數值常數**（**比工單列舉的 3／2／5／0.3 更強**：連計數器的 0／1 都不用，改用 `list` 累加 ＋ `len()`）。兩個門檻以 `int(getattr(prior, "LIFECYCLE_N_SUPERSEDE"))`／`int(getattr(prior, "LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD"))` 取得 | 守衛 **S1**（AST：數值常數集合必須為空 ＋ `getattr` 第二實參必須是 `LIFECYCLE_*` 常數字串）；變異 **M1** 轉紅 |
| 🔴 **零新欄位** | 只輸出**本模組自有**的 dataclass；不新增／不修改任何引擎欄位 | 守衛 S4（與引擎既有 `ContradictionRecord` 對帳） |
| 🔴 **零 IO／零 LLM** | AST：無 `open(`／`requests`／`httpx`／`urllib`／`socket`／`aiohttp`／`subprocess` 之 call 與 import | 守衛 **S2**；變異 **M2** 轉紅 |
| 🔴 **不寫引擎狀態** | AST：無 `record_contradiction`／`supersede` 識別字（Name／Attribute）、無 `soul_elevation.engine` 之 import | 守衛 **S3**；變異 **M3** 轉紅 |
| 🔴 **不 import 引擎的寫入路徑** | 只 `from soul_elevation import prior` 取常數。**誠實揭露**：`soul_elevation/__init__.py` 會 re-export engine（套件封裝的必然結果），但本模組**零呼叫**其任何寫入 API —— S3 的 AST 守衛釘死 | 守衛 **S3** |
| 🔴 **確定性** | 同一輸入兩次呼叫 `repr()` 逐位元相同；無時間戳、無 set 走訪順序洩漏到輸出 | 守衛 **S6**；變異 **M6** 轉紅 |
| 🔴 **計數語意與引擎一致** | 兩個計數器是 `engine._count_independent_contradictions()`／`engine._contradiction_spread_days()` 的**逐字鏡像**（含「命中 event identity 去重時**不**把 source_id 記入已見集合」的順序細節） | 守衛 **S4**（以引擎計數器直接對帳，且另斷言字面期望值 5／5，防止「兩邊一起錯」） |

---

## 6. 守衛 6 筆（**恰 6 筆**）＋ 變異 M1～M6

### 6.1 守衛（S1～S6）

| # | 內容 | 實作要點 |
|---|---|---|
| **S1** | 零門檻常數 | AST：模組內**零數值常數**；兩個門檻必須以 `getattr(prior, "<NAME>")` 取得 |
| **S2** | 零 IO／零 LLM | AST：禁 `open`／`requests`／`httpx`／`urllib`／`urlopen`／`socket`／`aiohttp`／`subprocess`（call 名與 import 名） |
| **S3** | 不寫引擎狀態 | AST：禁 `record_contradiction`／`supersede` 識別字（Name／Attribute）、禁 import `…engine` |
| **S4** | 正確性 | 以引擎既有計數器對帳「獨立矛盾數／跨日數」；覆蓋三種去重分支（同 source、同 event、兩者皆無） |
| **S5** | 門檻守門生效 | 獨立矛盾 < 3 ⇒ 未達；跨日 < 2 ⇒ 未達（**且**另有「兩者皆達 ⇒ 是」的正面案例，證明不是「永遠未達」） |
| **S6** | 確定性 | 同一輸入兩次呼叫 `repr()` 逐位元相同 |

### 6.2 變異實驗（**每條必須轉紅；ABORT 與意外全綠同等嚴重**）

**Harness**：`%TEMP%\p5_sup_scripts\p5_sup_mutation_harness.py`（**改副本**模式；
repo／worktree 檔**全程不寫入**）。每條變異在
`%TEMP%\p5_sup_scripts\mutsandbox\<id>\` 內建獨立沙盒（模組**副本** ＋ 探針副本 ＋ 守衛副本），
以子行程跑 pytest。**判定主閘門＝pytest returncode**；失敗數只從耗時摘要行取；
**出現 "error"（收集期錯誤）＝ ABORT**（守衛根本沒跑）⇒ 該條變異視為失敗。

**Baseline（未變異的沙盒副本）**：**6 passed / 0 failed / 0 errors，rc = 0** ✅

| 變異 | 內容 | 期望轉紅 | **實際轉紅的測試名** | rc | 摘要 |
|---|---|---|---|---|---|
| **M1** | 硬編碼門檻（`required_independent = 3`） | S1 | `test_s1_module_has_zero_threshold_literals_and_reads_thresholds_from_prior` | 1 | `1 failed, 5 passed` |
| **M2** | 加入檔案讀取（`open(__file__).read()`） | S2 | `test_s2_module_has_zero_file_io_and_zero_network` | 1 | `1 failed, 5 passed` |
| **M3** | 引用引擎寫入路徑（`import soul_elevation.engine as _engine` ＋ `_engine.InternalizingEngine.record_contradiction`） | S3 | `test_s3_module_never_touches_engine_write_paths` | 1 | `1 failed, 5 passed` |
| **M4** | 移除 `source_id` 去重（同一 source 重複計數） | S4 | `test_s4_counts_are_identical_to_engine_counters` | 1 | `1 failed, 5 passed` |
| **M5** | 跨日門檻判定永遠視為達標（`meets_spread = True`） | S5 | `test_s5_thresholds_are_enforced_and_never_relaxed` | 1 | `1 failed, 5 passed` |
| **M6** | 輸出加入時間戳（`assessed_at` 欄位） | S6 | `test_s6_output_is_byte_identical_across_repeated_calls` | 1 | `1 failed, 5 passed` |
| **M5-variant**（補充） | 跨日**計數**永遠回門檻值（`return int(getattr(prior, "LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD"))`） | S4＋S5 | `test_s4_counts_are_identical_to_engine_counters`、`test_s5_thresholds_are_enforced_and_never_relaxed` | 1 | `2 failed, 4 passed` |

🔴 **每條變異都「恰好」打紅預期的那一個守衛**（M5-variant 例外，因 S4 是**同一數量**的對帳守衛，
必然連帶轉紅 —— 這是雙重把關，非弱變異）。

🔴 **M6 的一個真實陷阱（已處理，實測）**：
本機 Windows 的 `datetime.now()` 時鐘粒度約 **15.6 ms** —— **實測 2000 次連續呼叫轉移 0 次**
（`time.time_ns()` 同樣 0/1999；`time.perf_counter_ns()` 只有 1087/1999）。
⇒ 若 M6 用 `datetime.now()` 取值，**兩次呼叫極可能取到同一值 ⇒ S6 不會轉紅 ⇒ 意外全綠**。
本票因此讓 M6 的時間戳**同時掛一個單調序號**（`itertools.count()`，實測 1999/1999 唯一）
⇒ **保證**轉紅、不依賴時鐘運氣。

**收尾位元自證**：被測檔 `src/inner_life/contradiction_accumulator_prototype.py`
初始 sha256 ＝ `5960c8c6c25ad1d5c3d001889998d99ffb9fe373ba71b05554252203f2491ba2`（10,525 bytes），
**收尾 sha256 相同** ✅
harness 最終判定：`ALL_MUTATIONS_RED_AND_RESTORED=True`（rc = 0）。

---

## 7. 測試結果

### 7.1 定向（**新守衛 6 筆**）

```
$ .venv\Scripts\python.exe -m pytest -q tests/inner_life/test_contradiction_accumulator_prototype.py
......                                                                   [100%]
6 passed in 0.33s
```
pytest **returncode ＝ 0**；passed ＝ **6**；failed ＝ **0**（摘要行無 "failed" token ⇒ 依量測紀律當 0）；
errors ＝ 0。沙盒＝全**合成資料**（不讀 `data/**`、不依賴生產資料）。

### 7.2 既有守衛（工單 §5 指定 5 檔，**全部存在**）

| 檔案 | 存在 |
|---|---|
| `tests/inner_life/test_tension_closure_loop.py` | ✅ |
| `tests/inner_life/test_p5_working_state_context.py` | ✅ |
| `tests/inner_life/test_p5_data_plane_guard.py` | ✅ |
| `tests/inner_life/test_campaign_b_e2e_closed_loop.py` | ✅ |
| `tests/inner_life/test_contradiction_probe.py` | ✅ |

**實測（5 檔合併執行）**：**2 failed, 133 passed**（rc = 1）。

那 2 筆是 **worktree 環境性失敗、與本票無關**（且**同樣出現在乾淨 HEAD 的基線回歸**裡）：

1. `tests/inner_life/test_tension_closure_loop.py::TestT10RejectsProductionStoreDir::test_t10_store_dir_outside_temp_is_rejected`
   —— worktree 位於 `%TEMP%` 之下 ⇒ `REPO_ROOT/not_a_sandbox` 被誤判為 temp 之下。
2. `tests/inner_life/test_tension_closure_loop.py::TestP2T12SandboxIsolation::test_p2_t12_production_files_are_byte_identical_after_the_loop`
   —— worktree 內無生產 `data/` ⇒ 對帳基準為空 ⇒ 守衛**自陳「假紅」**
   （`AssertionError: 生產對帳基準不應為空（否則本測試是假紅／假綠）`）。

⇒ **既有守衛未被削弱**：兩筆失敗與基線回歸的失敗集合**逐筆相同**（§7.4）。

### 7.3 全庫回歸（**基線在當下 HEAD 重算**；前後差分口徑）

見 §7.4。

### 7.4 回歸結果（**基線在當下 HEAD 重算**；前後差分口徑）

解譯器 ＝ **repo 的 `.venv`**（`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`，Python 3.11.15），
cwd ＝ `%TEMP%\sup_wt`。兩輪皆為 `pytest -q tests`（**全庫**）。

| 樹 | failed | passed | skipped | errors | 耗時 | pytest returncode |
|---|---|---|---|---|---|---|
| **改動前**（乾淨 worktree，HEAD `c9ec5e8`） | 33 | **7,333** | 44 | 10 | 707.16s（0:11:47） | 1 |
| **改動後**（本票最終樹） | 33 | **7,339** | 44 | 10 | **679.32s（0:11:19）** | 1 |

⇒ **passed ＋6 ＝ 本票新增的 6 筆守衛**（工單 §5 的「基線 ＋ 6 筆」✅）。
⇒ **failed／errors 的數量與集合逐筆相同**（**新增 0、消失 0**）——以失敗名逐筆比對確認：

```
NEW failures   = []
GONE failures  = []
NEW errors     = []
GONE errors    = []
```

⇒ **既有守衛未被削弱**。

#### 🔺 回歸口徑說明（**33 failed ＋ 10 errors 全部是 worktree 環境性失敗，與本票無關**）

這 33 筆失敗 ＋ 10 筆錯誤在**乾淨 HEAD 的基線回歸裡逐筆相同**（上表已比對），
且全部落在以下**環境性**成因（worktree 位於 `%TEMP%` 之下、worktree 內無生產 `data/`、
`.git` 在 worktree 是**檔**不是目錄）：

| 檔案 | 筆數 | 成因 |
|---|---|---|
| `tests/test_ms2_multimodal_perception.py` | 8 | 需 MCP server（worktree 無其啟動路徑） |
| `tests/infra/test_repo_hygiene_tracked_tests.py` | 3 | **`.git` 是檔不是目錄**（worktree 固有）；🔴 本票新增的 2 個 untracked 檔**未**增加任何新失敗 |
| `tests/test_sim_accel_thread.py` | 3 | worktree 內無生產 `data/` ⇒ 對帳基準為空 |
| `tests/test_ta2v2a3_measurement.py` | 2 | 同上 |
| `tests/test_m5_3_s2_retrieval_diagnostic.py` | 2 | 同上 |
| `tests/inner_life/test_tension_closure_loop.py` | 2 | worktree 位於 `%TEMP%` 之下 ⇒ `REPO_ROOT/not_a_sandbox` 被誤判；worktree 無生產 `data/` |
| 其餘 13 檔 | 13 | 各自環境性成因（同型態，詳見基線輸出） |
| `tests/soul/test_sim_accel_streak_7d_sunset.py` | 10 errors | worktree 內無生產 `data/` ⇒ fixture 收集期錯誤 |

> **回歸數字一律以 repo 內 `.venv` 的直譯器為準**（PATH 上的全域 pytest 是另一個世界），
> 且**失敗數以 pytest 的 returncode 為主閘門**、失敗數只從耗時摘要行取
> （零失敗時輸出裡根本沒有 "failed" 字樣 ⇒ 一律當 0）。

---

## 8. 交付物清單（含**兩份 diff** 的套用說明）

| # | 交付物 | 路徑 |
|---|---|---|
| 1 | 5 檔未提交變更 | `%TEMP%\sup_wt`（見 §1） |
| 2 | **diff A**（已追蹤檔的修改） | `%TEMP%\p5_sup_scripts\p5_sup_patch.diff` |
| 2 | **diff B**（新增的守衛檔；untracked，`git diff` 不含） | `%TEMP%\p5_sup_scripts\p5_sup_new_guard.diff` |
| 2 | **diff C**（新增的模組檔；untracked，`git diff` 不含） | `%TEMP%\p5_sup_scripts\p5_sup_new_module.diff` |
| 3 | 變異 harness 本體 | `%TEMP%\p5_sup_scripts\p5_sup_mutation_harness.py` |
| 3 | 變異報告（完整輸出） | `%TEMP%\p5_sup_scripts\mutation_report.txt` |
| 3 | 量測腳本 | `%TEMP%\p5_sup_scripts\p5_sup_measure.py`／`p5_sup_branch_b.py` |
| 4 | 本報告 | `%TEMP%\sup_wt\logs\FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-REPORT.md` |

### 8.1 三份 diff 如何合併套用（**已實測可套用**）

```powershell
# 1) diff A：已追蹤檔的修改（logs/ENGINEERING_STATE.md ＋ tests/test_observability_completeness_1.py）
git apply --whitespace=nowarn "C:\Users\bbfcc\AppData\Local\Temp\p5_sup_scripts\p5_sup_patch.diff"

# 2) diff B：新增的守衛檔（untracked；git diff 不含它）
git apply --whitespace=nowarn "C:\Users\bbfcc\AppData\Local\Temp\p5_sup_scripts\p5_sup_new_guard.diff"

# 3) diff C：新增的模組檔（untracked；git diff 不含它）
git apply --whitespace=nowarn "C:\Users\bbfcc\AppData\Local\Temp\p5_sup_scripts\p5_sup_new_module.diff"
```

✅ **三份 diff 皆以 `git apply --check --reverse` 對 worktree 實測通過（rc=0）**
⇒ 內容與工作樹**逐位元一致** ⇒ 在乾淨基底上可乾淨套用。

| diff | 產生方式 | 內容 | 標頭 |
|---|---|---|---|
| `p5_sup_patch.diff` | `git -C %TEMP%\sup_wt diff --output=…` | **2 檔已追蹤檔**的修改 | `a/… b/…`（正常） |
| `p5_sup_new_guard.diff` | `git -C %TEMP%\sup_wt diff --no-index --output=… NUL tests/inner_life/test_contradiction_accumulator_prototype.py` | **新增守衛檔**（289 行） | `new file mode 100644`＋`--- /dev/null`（**git 已把 `NUL` 正規化為 `/dev/null`** ⇒ 可直接 `git apply`） |
| `p5_sup_new_module.diff` | 同上（`NUL src/inner_life/contradiction_accumulator_prototype.py`） | **新增模組檔**（219 行） | 同上 |

⚠️ 三份 diff 皆**無 BOM**（由 git 自己的 writer 輸出，**未**經 PowerShell `Out-File -Encoding utf8`）。
⚠️ 套用時**必須**在 **repo 根目錄**執行 `git apply`；若 `git apply` 因環境差異失敗，
**等價做法**是直接取出該檔（diff 的 `+` 行即完整檔案內容）。

---

## 9. 唯讀自證（`data/**` 逐位元）

| 檔案 | 量測前 sha256 | 量測後 sha256 | 結論 |
|---|---|---|---|
| `elevation_nodes.jsonl` | `ec89be989895694b…` | `ec89be989895694b…` | ✅ **UNCHANGED（位元相同）** |
| `elevation_edges.jsonl` | `944e8952f6a615aa…` | `944e8952f6a615aa…` | ✅ **UNCHANGED** |
| `elevation_lifecycle_events.jsonl` | `b5ed3b852aaf453e…` | `b5ed3b852aaf453e…` | ✅ **UNCHANGED** |
| `elevation_trace.jsonl` | `7ca82fcf84e210d2…` | `7ca82fcf84e210d2…` | ✅ **UNCHANGED** |

🔴 本票**未**對生產 sqlite 開任何連線（含 `mode=ro`）；四檔皆純 JSONL 唯讀。
🔴 量測前後 sha256 由量測腳本**內建自比**（不是事後回補）。

---

## 10. 🔴 誠實聲明（工單 §4.6 必寫）

1. **本票不讓生產召回池長大。** 旗標／接線不動生產；生產 `superseded` 節點仍 ＝ **0**，
   召回池仍 ＝ **0**。本票**沒有**、也**不宣稱**任何生產行為改變。
2. 本票回答的是：**「正規路徑（解開 `superseded` 死鎖鏈路）是否可行、在哪個上游」**。
   答案：**可行**（形狀 E 已證明不必放寬判準即可達門檻），
   **但上游不是 Phase 2 的接線，而是 §4.4 的 G-1／G-2／G-3 三件上游工程**。
3. **戰役二（Belief 長效保活）是否開打，仍等今晚 22:00 夜槽觀測與 Owner 裁示。**
   本票**不**構成開打授權。
4. §4.5 的「若接線池會變成 ≥1」是**沙盒量測（合成資料、機制示範）**，**不是生產**。
5. **近門 48→69 組全部來自 REINFORCE `support_propagation`**（命題再確認，非反例）⇒
   本票**未**放寬 lineage 判準，**未**調低任何門檻，**未**偽造任何矛盾邊。
6. **合成資料僅用於 Part 2 的機制示範**，**不得**用於任何過閘門宣稱。

---

## 11. 未做與明文排除

- **未實作** Phase 2 的引擎接線（只出上游缺口描述 ＋ 離線原型 ＋ 判定器）。
- **未實作** G-1／G-2／G-3 任何一件（上游事件來源）——**只描述**。
- **未改**引擎（`C:\Users\bbfcc\.local\bin\soul-elevation\` 在 repo 外）：零寫入、零改動。
- **未調低／調高／新增** `LIFECYCLE_N_SUPERSEDE`／`LIFECYCLE_ESSENCE_N_SUPERSEDE`／
  `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`／`LIFECYCLE_ESSENCE_CONFIDENCE_DELTA`（**契約不是參數**）。
- **未放寬**「同一 lineage 根」判準；**未**改 `src/inner_life/contradiction_probe.py` 的既有判準
  （**一行未改**）；**未**改 `src/inner_life/elevation_recall.py` 的 `RECALLABLE_LIFECYCLE_STATES`；
  **未**改 `src/inner_life/tension_closure_loop.py`；**未**改排程器。
- **未偽造矛盾邊過閘門**；合成資料僅 Part 2 機制示範，且已明確標示。
- **未寫** `data/**`（worktree 內 `data/` 僅 gitignored 的本機產物）；**未**對生產 sqlite 開連線。
- 0 真實 LLM、0 外部 API、0 網路；**未**重啟、**未**部署、**未**殺任何行程。
- **未** `git add`／`commit`／`push`（**絕對無** `git add .`）。
- **未**觸碰 11 個 frozen contract 檔。

---

## 12. 🔺 工單內部矛盾／缺資訊之標示（**照做並回報，未自行改選解法**）

| # | 工單前提 | 實測 | 處置 |
|---|---|---|---|
| **P1** | §0／§2.1「當前母體＝**1054** 節點／**2275** 邊」 | 量測時（18:38Z）＝ **1056／2277**（pattern 827→829，其餘型別不變） | 以**實測值**為準（§2.0）；本票**不**沿用工單寫死的數字 |
| **P2** | §0「Phase 1 的母體已過期 ⇒ 前提可能已變」 | 前提**未**變：`lineage_depth`／父型別／相異根／規格候選 **4 項全不變**（§3） | 判定＝**分支乙**；結論以最新資料重現 |
| **P3** | §2.3「🔴 **零門檻常數**（一律引用引擎既有 `prior.py` 的值）」 | 工單列舉 3／2／5／0.3；本票採**更強**形式：模組內**零數值常數**（含計數器的 0／1） | 已於 §5.2 與守衛 S1 明示；**未**放寬任何契約值 |
| **P4** | §3「S4 用引擎既有計數器算出的結果與判定器輸出**一致**」 | 若只做比對，「兩邊一起算錯」會通過 | S4 **另**斷言字面期望值（5／5），把「一起錯」的可能性堵住 |
| **P5** | §3「M5 跨日判定永遠回 2」 | 若在**計數**層實作，會連帶打紅 S4（同一數量的對帳守衛）⇒ 變異不隔離 | 主 M5 改在**判定閘門**層實作（隔離到 S5）；**另**加 M5-variant 保留計數層版本（明列 S4＋S5），兩者皆轉紅 |
| **P6** | §3「M6 輸出加入時間戳（破壞確定性）」 | 本機 `datetime.now()` 時鐘粒度 ≈15.6 ms（2000 次連續呼叫**轉移 0 次**）⇒ 純時間戳會「意外全綠」 | M6 的時間戳另掛單調序號（`itertools.count()`，1999/1999 唯一）⇒ **保證**轉紅；已在 §6.2 明示 |
| **P7** | §3「M3 判定器直接呼叫 `engine.record_contradiction()`」 | `record_contradiction` 是 `InternalizingEngine` 的**類別方法**、非模組層級函數 ⇒ 以 `engine.record_contradiction` 存取會在**執行期**拋 `AttributeError` ⇒ 連帶打紅 S4/S5/S6 ⇒ 屬**弱變異** | M3 改以類別屬性引用（`_engine.InternalizingEngine.record_contradiction`）⇒ **只有 S3 轉紅**；已在 §6.2 明示 |
| **P8** | §4.1「`git diff --name-only HEAD` **恰好 5 檔**」 | **實得 2 檔** —— 本票的 3 個新檔（含 report 本身）皆為 **untracked** ⇒ `git diff` **不含**它們 | 以 `git status --short`（**恰 5 檔**：2 個 ` M` ＋ 3 個 `??`）＋ `git diff --numstat` 併看確認（§1）；並在 §8 產出**三份 diff** 以補 `git diff` 的缺口 |

---

## 13. 附錄 — 本票產出的量測／變異工具（**均在 `%TEMP%`，非 repo**）

| 檔案 | 用途 |
|---|---|
| `%TEMP%\p5_sup_scripts\p5_sup_measure.py` | A-1 Q1～Q6 ＋ 分支乙 U1～U3（唯讀；內建量測前後 sha256 自比；Q1～Q6 段**逐字沿用** Phase 1 腳本） |
| `%TEMP%\p5_sup_scripts\p5_sup_branch_b.py` | A-3 量化「差多少」＋ Part 2 上游形狀機制示範（真實引擎 `revise()` ＋ 真實探針） |
| `%TEMP%\p5_sup_scripts\p5_sup_mutation_harness.py` | M1～M6 變異（改副本 ＋ 位元還原自證） |
| `%TEMP%\p5_sup_scripts\mutsandbox\<id>\` | 每條變異的獨立沙盒 |
| `%TEMP%\p5_sup_scripts\mutation_report.txt` | 變異完整輸出 |
| `%TEMP%\p5_sup_scripts\measure_output.txt`／`branch_b_output.txt` | 量測原始輸出 |
| `%TEMP%\p5_sup_scripts\baseline_regression.txt`／`final_regression.txt` | 回歸前後輸出 |
| `%TEMP%\p5_sup_scripts\patch_bytes.py`／`to_crlf.py` | 位元層級檔案工具（保 CRLF、不加 BOM） |
| `%TEMP%\p5_sup_scripts\crosscheck_report.py` | **報告數字對帳**：逐條驗證本報告的每個頭條數字都真的出現在量測／變異原始輸出裡（**56 條全綠**） |

> ⚠️ **一個量測紀律陷阱（實測踩到）**：PowerShell 5.1 的 `Tee-Object -FilePath` 寫出的檔案是
> **UTF-16LE with BOM**（開頭 `FF FE`），**不是** UTF-8 ⇒ 以 Python `encoding="utf-8"` 讀會整份解碼失敗。
> ⇒ 對帳腳本一律以 `encoding="utf-16"` 讀這些檔（`Get-Content` 則會自動依 BOM 判斷，故 PowerShell 側不受影響）。
