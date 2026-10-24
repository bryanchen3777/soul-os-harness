# 工單：FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE（解鎖 superseded 死鎖鏈路 —— 離線原型）

> 開立：2026-10-24 14:50 EDT（主大腦；依 Owner 2026-10-24 14:2x 裁決：戰役二**否決**放寬狀態集合、採「路徑修復與自然解鎖」；正式開打維持在今晚 22:00 夜槽觀測之後）
> 依賴：`logs/RECALL-POOL-ZERO-DIAGNOSIS-REPORT.md`（召回池診斷，已合入 `a70fa5a`）＋ `logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md`（Phase 1 實測記錄）
> 母案：`docs/FEAT-SCHEMA-CONTRADICTION-RFC.md`／`logs/FEAT-SCHEMA-CONTRADICTION-RFC-WORK-ORDER.md`（Phase 1）＋ `logs/SIM-ACCEL-RECALL-30D-WORK-ORDER.md`（沙盒示範手法）

---

## 0. 出發點（已量現況，不猜）

| 事實 | 值 | 來源 |
|---|---|---|
| `superseded` 節點數 | **0**，且**結構性不可達**：`record_contradiction()` 的唯一呼叫點在 `engine.supersede()` 內（門檻判定之前），而 `supersede()` 以「矛盾證據達門檻」為觸發前提 | 召回池診斷；`docs/FEAT-SCHEMA-CONTRADICTION-RFC.md` 第 5 節 |
| 引擎門檻（**契約不是參數**） | `LIFECYCLE_N_SUPERSEDE=3`／`LIFECYCLE_ESSENCE_N_SUPERSEDE=5`／`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD=2`／`LIFECYCLE_ESSENCE_CONFIDENCE_DELTA=0.3` | `soul_elevation/prior.py:68-73`（主大腦 2026-10-24 14:35 實讀） |
| 引擎位置（**在 repo 外**） | `C:\Users\bbfcc\.local\bin\soul-elevation\src\soul_elevation\engine.py`／`prior.py`（editable install） | 主大腦實讀 |
| Phase 1 的探針**已存在** | `src/inner_life/contradiction_probe.py`：`find_candidate_contradictions(nodes, edges, *, agent_id=None)`（純函式、零 IO／零 LLM、不寫引擎狀態、零新欄位） | 主大腦實讀全文 |
| ⚠️ **Phase 1 的實測結論（關鍵）** | 規格候選矛盾 **0** 組／343 配對；**唯一把配對清零的判準是「同一 lineage 根」**；結構性證據：靈魂節點 136 筆的 `lineage_depth` **全為 1**（無 depth ≥ 2）、父節點型別**全為 pattern**、父為靈魂節點者 **0** 筆、相異 lineage 根 **136/136** ⇒ **兩顆互不相同的靈魂節點在現行資料形狀下永不共根** ⇒ Q4：**0／136 節點達門檻**、「**死鎖不會因規格判準而解除**」 | `logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md` Q1／Q3／Q4 |
| ⚠️ **近門 48 組的語意** | **100%** 來自 `trigger_type == "support_propagation"`（REINFORCE 傳遞邊）⇒ 那是「**命題再確認**」而非反例 ⇒ **放寬 lineage 判準會語意誤判** | 同上 Q5；RFC §6.1 R2 |
| **Phase 1 的母體（已過期）** | 節點 **793**（pattern 657／value 125／belief 6／trait 5／essence 0）／邊 **1791**（`support_propagation` 48） | 同上 §量測宇宙 |
| **當前母體（最新）** | 節點 **1054**（pattern 827／value 213／belief 9／trait 5）／邊 **2275** | 召回池診斷 2026-10-24 12:05Z |
| ⇒ **為什麼必須重測** | Phase 1 的結論建立在 **793／1791** 上；**資料已長到 1054／2275**（＋33%／＋27%）⇒ **前提可能已變** ⇒ 不得沿用舊結論 | 主大腦裁定 |

---

## 1. 目標

**回答一個問題：正規路徑（解開 `superseded` 死鎖鏈路）是否可行、在哪個上游。**

**不是**盲目的「把 Phase 2 接上去」——因為 Phase 1 的實測已證：**即使完美接線，生產圖譜上目前也沒有任何候選矛盾可餵進累積器**（規格候選 0 組）。⇒ 本票先**用最新生產資料重測前提**，再依結果走兩條分支之一，**都必須有量測與明確結論**。

**為什麼現在做**：召回池診斷已證明池 0 是**產出端結構性缺口**（`dormant ∩ clean = 0`；`superseded` 恆 0 且不可達）；Owner 已**拒絕**放寬狀態集合、要求走正規路徑。⇒ 本票是戰役二（Belief 長效保活）的前置解盲。

---

## 2. 做法（決策已定，執行者照做）

### 2.1 A-1：唯讀重測 Q1～Q6（**以最新生產資料**）

母體：`data/elevation/elevation_nodes.jsonl`（1054）＋ `data/elevation/elevation_edges.jsonl`（2275）。
🔴 **不得對生產 sqlite 開任何連線（含 `mode=ro`）**；`data/elevation/*.jsonl` 是純 JSONL ⇒ 可直接唯讀讀取；**須**對照 Phase 1 基線（793／1791）逐項標明變化。

| 項 | 必答內容 |
|---|---|
| **Q1** | 規格候選矛盾組數（分子）／符合前提的 `(N, e)` 配對總數（分母）；篩選鏈逐級存活數（`E(N)` → 同 agent＋同 `source_id` 有對應靈魂節點 → 加「同一 lineage 根」→ 加 `content` 逐位元不等） |
| **Q2** | 規格候選的 agent／node_type 分佈、`ts` 最早／最晚／跨度、**不同模擬日數**；近門配對的同樣分佈 |
| **Q3** | Q1 ＝ 0 的成因（**是哪一條判準**）＋ 結構性證據：`lineage_depth` 分佈、**父節點型別分佈**、**父為靈魂節點者數**、**相異 lineage 根數／總數** |
| **Q4** | 以**引擎既有計數器**（`soul_elevation.engine._count_independent_contradictions`／`_contradiction_spread_days`，**零新增、零調低**）逐節點算「獨立矛盾數／跨日數」是否達 `3`／`2` |
| **Q5** | 與既有 REINFORCE `support_propagation` 邊的重疊（近門配對的 `trigger_type` 分佈） |
| **Q6** | `agent_akane` 單獨列出（Phase 1 的標桿角色） |

### 2.2 A-2：前提判定（**二分支，必走其一**）

#### 分支甲：規格候選 **> 0**
1. 列出哪些節點達門檻（Q4 的逐節點表）。
2. 產出 **Phase 2 接線的前置條件清單**（**設計，不實作**）：誰在什麼時機呼叫 `record_contradiction()`、`source_id` 與 `ts` 怎麼給、如何滿足「跨 ≥2 個不同模擬日」、如何保證「同一 `source_id` 只算 1 個獨立證據」。
3. 做**離線原型**（沙盒內）：把候選餵進累積器 → 達門檻 → `supersede()` 產出 superseded 節點 ＋ lifecycle 事件；**且必須證明門檻真的在生效**：
   - 矛盾 **< 3 個** ⇒ **必須失敗**（`supersede()` 不產生 superseded）。
   - 矛盾**只跨 1 個模擬日** ⇒ **必須失敗**。
   - 生產三檔（`elevation_nodes.jsonl`／`elevation_edges.jsonl`／`elevation_lifecycle_events.jsonl`）sha256 前後**逐位元相同**。
   ⇒ 手法可參考 `logs/SIM-ACCEL-RECALL-30D-WORK-ORDER.md`（該票已示範沙盒記錄矛盾→取代的完整路徑與欄位形狀）。

#### 分支乙：規格候選 **仍 = 0**
1. **重現 Phase 1 的結構性證據**（用最新資料）：`lineage_depth` 是否仍恆 1？父是否仍皆 pattern？相異 lineage 根是否仍 ＝總數？
2. **鎖定真正的上游缺口**：量測**「同一命題的不同版本」鏈是否存在** ⇒ `lineage_depth ≥ 2` 的節點數／`parent_node_id` 指向**靈魂節點**者數／任何 revise 類事件的實際筆數。
3. 讀**誰能產出 revise 鏈**：引擎與寫入端（`src/inner_life/elevation_adapter.py` 等）中 `parent_node_id`／`lineage_depth` 的**產生路徑** ⇒ 說明為什麼在生產上它沒發生（是路徑不存在？被條件擋掉？還是事件來源沒有？）
4. ⇒ 產出**「讓召回池合法長大」的最小上游缺口描述**：需要**什麼事件來源／什麼資料形狀**，才會出現合法的「同源不同版命題」⇒ **只描述，不實作**。

### 2.3 A-3：新增「累積器饋入判定器」（**兩個分支都需要 ⇒ 範圍確定**）

`src/inner_life/contradiction_accumulator_prototype.py`（**新增**，純函式）：
- 輸入＝某節點的候選矛盾清單（`contradiction_probe.find_candidate_contradictions()` 的輸出）＋該節點的既有 `contradiction_pressure`。
- 輸出＝「若這些寫入累積器，會達門檻嗎」＝**獨立矛盾數**／**跨模擬日數**／**是否達 `LIFECYCLE_N_SUPERSEDE`**／**是否達 `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`**。
- 🔴 **零門檻常數**（一律引用引擎既有 `prior.py` 的值）；🔴 **零新欄位**；🔴 **零 IO／零 LLM**；🔴 **不呼叫 `engine.record_contradiction()`**（不寫引擎狀態）；🔴 **不 import 引擎的寫入路徑**。
- 🔺 分支甲用它驗證可行性；分支乙用它量化「差多少」。

### 2.4 邊界（硬性）
1. 🔴 **不得調低／調高／新增** `LIFECYCLE_N_SUPERSEDE`／`LIFECYCLE_ESSENCE_N_SUPERSEDE`／`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`／`LIFECYCLE_ESSENCE_CONFIDENCE_DELTA`（**契約不是參數**；且引擎在 repo 外 ⇒ 也改不到）。
2. 🔴 **不得放寬「同一 lineage 根」判準**（Phase 1 已記錄：近門 48 組 **100%** 來自 REINFORCE「命題再確認」⇒ 放寬會**語意誤判**；且 Owner 已明確拒絕「為了讓數字過關而修改判準」）。⇒ 若你認為該判準本身需要重裁 ⇒ **只回報建議，不動手**。
3. 🔴 **不得偽造矛盾邊**（禁止偽造邊過閘門）⇒ 原型中的合成矛盾必須**明確標示為「機制示範」**，且**不得**用於任何過閘門宣稱。
4. 🔴 **不得改引擎**（`C:\Users\bbfcc\.local\bin\soul-elevation\src\soul_elevation\` **在 repo 外**）⇒ 只讀、只呼叫其 API。
5. 🔴 **不得接生產**：不改 `src/inner_life/elevation_recall.py` 的 `RECALLABLE_LIFECYCLE_STATES`、不改排程器、不改 `src/inner_life/contradiction_probe.py` 的既有判準、不改 `src/inner_life/tension_closure_loop.py`。
6. 🔴 **0 真實 LLM、0 生產寫入、0 網路**；不得對生產 sqlite 開連線（含 `mode=ro`）；不得清表、不得觸發合成事件、不得重啟服務、不得殺行程。
7. 🔴 不得 `git add`／`git commit`／`git push`；不得 `git add .`。

---

## 3. 守衛（新增檔 `tests/inner_life/test_contradiction_accumulator_prototype.py`）

守衛須涵蓋（**恰 6 筆**，S1～S6）：

| # | 內容 |
|---|---|
| S1 | 判定器**零門檻常數**：模組內**不得**出現 `3`／`2`／`5`／`0.3` 這類字面門檻值（AST 斷言：門檻一律 `getattr(prior, ...)` 取得） |
| S2 | 判定器**零 IO／零 LLM**：模組內不得有 `open(`／`requests`／`httpx`／`urllib`（AST 斷言） |
| S3 | 判定器**不寫引擎狀態**：不得呼叫 `engine.record_contradiction()`／`engine.supersede()`（AST 斷言） |
| S4 | 判定器**正確性**：用引擎既有計數器算出的「獨立矛盾數／跨日數」與判定器輸出一致（同一 `source_id` 只算 1 個；跨日以 `ts` 的日期部分計） |
| S5 | **門檻守門生效**：獨立矛盾 **< 3** ⇒ 判定「未達」；跨日 **< 2** ⇒ 判定「未達」（**不得**放寬） |
| S6 | **確定性**：同一輸入兩次呼叫輸出**逐位元相同** |

### 變異實驗（**每條必須轉紅**；ABORT 與意外全綠同等嚴重）

| # | 變異 | 應轉紅 |
|---|---|---|
| M1 | 判定器內**硬編碼門檻**（不再引用 `prior`） | S1 |
| M2 | 判定器內加入檔案讀取 | S2 |
| M3 | 判定器直接呼叫 `engine.record_contradiction()` | S3 |
| M4 | 獨立矛盾計數**把同一 `source_id` 重複計數** | S4 |
| M5 | 跨日判定**永遠回 2**（放寬） | S5 |
| M6 | 判定器輸出**加入時間戳**（破壞確定性） | S6 |

⚠️ 變異落點在**單一檔案**（新模組）⇒ harness 可用**改副本**模式；baseline 必須是**整個新守衛檔全綠**（6 筆）；收尾必印被測檔 sha256 與初始值相同。

---

## 4. 驗收（完成的定義）
1. `git diff --name-only HEAD` **恰好 5 檔**：
   `src/inner_life/contradiction_accumulator_prototype.py`（新增）、`tests/inner_life/test_contradiction_accumulator_prototype.py`（新增）、`logs/FEAT-SUPERSEDED-ACCUMULATOR-PIPELINE-REPORT.md`（新增）、`logs/ENGINEERING_STATE.md`（追加條目）、`tests/test_observability_completeness_1.py`（**`allowed` 逐票更新**：新增 `src/inner_life/contradiction_accumulator_prototype.py`，沿用 `src/inner_life/contradiction_probe.py` 的既有列舉風格）。多一檔少一檔都**停止並回報**。
2. 新守衛 **6 筆全綠**；變異 **M1～M6 全數轉紅**。
3. A-1 的 Q1～Q6 **逐項有數字**（分子／分母／單位）＋ **與 Phase 1 基線（793／1791）逐項對照**。
4. A-2 的**分支判定明確**（甲／乙）＋ 對應交付（分支甲：前置條件清單 ＋ 離線原型 ＋ 門檻守門證明；分支乙：結構性證據重現 ＋ 上游缺口描述）。
5. A-3 判定器符合 §2.3 的全部硬性要求。
6. 🔴 **誠實聲明（必寫）**：本票**不**讓生產召回池長大（旗標／接線不動生產；池仍 = 0）；它回答的是「**正規路徑是否可行、在哪個上游**」⇒ **戰役二是否開打仍等今晚夜槽觀測與 Owner 裁示**。若分支甲成立 ⇒ 須明寫「若接線，池會變成 N（≥1）」**並註明那是沙盒量測、不是生產**。

## 5. 測試
- 定向：`pytest -q tests/inner_life/test_contradiction_accumulator_prototype.py` ⇒ 預期 **6 passed / 0 failed**。
- 既有守衛不得被削弱：`pytest -q tests/inner_life/test_tension_closure_loop.py tests/inner_life/test_p5_working_state_context.py tests/inner_life/test_p5_data_plane_guard.py tests/inner_life/test_campaign_b_e2e_closed_loop.py tests/inner_life/test_contradiction_probe.py` ⇒ **以你實際量到的為準**（若 `test_contradiction_probe.py` 不存在，請在報告中寫明並以實際存在的檔案清單為準）。
- 回歸：`.venv\Scripts\python.exe -m pytest -q tests` ⇒ 期望＝**當下 HEAD 的基線 ＋ 6 筆**（🔴 **基線必須在合入當下重算**，不得沿用工單寫死的數字）。
- 🔴 **回歸的失敗數必須以 pytest 的 returncode 為主閘門**；失敗數只從 pytest 的**耗時摘要行**取，**找不到 "failed" token 一律當 0**（pytest 在零失敗時輸出裡根本沒有 "failed" 字樣）。
- 🔴 測試**不得**啟動伺服器、**不得**綁 `:8000`／`:8765-8767`、**不得**對生產發真實請求；`tests/**` 禁用 `subprocess`／`os.spawn*`／`os.system`／`os.popen`。
- 🔴 變異 harness 只能改**副本**；repo 檔全程不寫入。

## 6. 不做（Out of Scope）
- **不實作** Phase 2 的引擎接線（只出前置條件清單 ＋ 離線原型）。
- **不**改引擎（repo 外）、**不**改 `elevation_recall.py` 的狀態集合、**不**改排程器、**不**改 `contradiction_probe.py` 的既有判準。
- **不**寫 `data/**`、不重啟、不部署、不打真實 LLM、不連網。
- 不 commit／push／git add（由主大腦合入時處理）。
- 不做 A/B 對照實驗；不製造 synthetic 事件到生產。

## 7. Frozen Contract 注意
11 檔永久紅線：`src/agency/` 5 檔、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`。本票不觸碰任何 frozen 檔。
`src/inner_life/contradiction_probe.py` 與 `src/inner_life/elevation_recall.py` **皆非** frozen，但本票**不得改動**它們（見 §2.4.5）。

## 8. 回報格式
- 5 檔範圍 ＋ numstat。
- **Q1～Q6 逐項**（分子／分母／單位）＋ **與 Phase 1 基線的對照表**（哪些變了、為什麼）。
- **分支判定**（甲／乙）＋ 對應交付（前置條件清單／原型證明／上游缺口描述）。
- 新守衛 6 筆 ＋ 變異 M1～M6 逐條。
- 判定器的硬性要求逐條自證（零門檻常數／零 IO／零 LLM／不寫引擎／確定性）。
- **誠實聲明**（本票不讓生產池長大）。
- 未做與明文排除。
- 若遇工單內部矛盾或缺資訊：**標示並附理由回報**，不要自行改選解法。
