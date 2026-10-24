# 可回憶池資料面解剖報告（RECALL-POOL-ZERO-DIAGNOSIS）

**工單**：`logs/RECALL-POOL-ZERO-DIAGNOSIS-WORK-ORDER.md`（9,183 bytes；唯讀，未修改）
**性質**：**純唯讀診斷**。**本票 0 程式變更**（`src/**` 一行未動）⇒ **未跑全庫回歸**（無程式變更即無回歸對象）。
**量測時刻**：2026-10-24T16:05:53Z（12:05:53 EDT）
**量測程式**：`%TEMP%\recall_diag_final.py`（單一整合快照）＋ `%TEMP%\recall_diag_measure.py` / `recall_diag_addendum.py` / `recall_diag_anchor.py`（分段佐證）
**輸出檔**：`%TEMP%\recall_diag_final.json`（機讀全套數字）
**執行環境**：worktree `C:\Users\bbfcc\AppData\Local\Temp\recall_diag_wt`（detached HEAD `85b0ab7`，**唯讀沙盒**）；生產資料一律用主 repo 絕對路徑 `C:\Users\bbfcc\.local\bin\soul-os-harness\data`
**解譯器**：`.venv\Scripts\python.exe`（Python 3.11.15；主 repo `.venv`）

---

## ① 執行摘要

**池為什麼是 0：不是任何一道召回門檻太苛刻，而是生產圖譜裡「不存在任何一顆四層 × 可召回狀態 × 非隔離」的節點 —— 這不是調參問題，是產出端根本沒產出可召回狀態的結構性問題。**

全圖 **1054** 顆節點中，四層（`value`/`belief`/`trait`/`essence`）只有 **227** 顆（其餘 **827** 顆是 `pattern`，結構性不可召回）；這 227 顆裡 **224** 顆 `active`、**1** 顆 `weakening`、**2** 顆 `dormant`，而池只認 `dormant | superseded`。那 2 顆 `dormant` **恰好**是 SOUL-BUG-010 隔離處置產生的 2 顆 `value`（`quarantine` 事件把 `lifecycle_state` 同時寫成 `dormant`）⇒ 隔離閘門再砍一次，`dormant ∩ clean = 0`。同時全圖 `superseded` = **0**，而且**結構上不可能產生**：`record_contradiction()` 的唯一呼叫點在 `engine.supersede()` 內（`soul_elevation/engine.py:1178`），而 `supersede()` 以「矛盾證據數達門檻」為前提 ⇒ 累積器恆空 ⇒ 死鎖（專案內已載明：`src/inner_life/contradiction_probe.py:11-13`；該模組 Phase 1 只定義不落地）。⇒ **池 = 0 顆，且在現行接線下無法靠等待自然長大。**

**對今晚 22:00 夜槽的直接含意**：依錨點量測，會有 **9 顆** Legacy Value 跨過 `LIFECYCLE_T_WEAKEN_DAYS = 7` 而轉 `active → weakening`；但 `weakening` **不在** `RECALLABLE_LIFECYCLE_STATES = {dormant, superseded}` ⇒ **夜槽不會讓池長大，只會維持池 = 0**。而衰減路徑真正能產出非隔離 `dormant` 的時間點，離現在還有約 23 天（見 §⑥ 關鍵結構發現 3）。

---

## ② 三個假設的逐條證據與結論

### 母體定義（全報告一致，禁止跨母體相減）

| 代號 | 母體 | 定義 | 數量 |
|---|---|---|---|
| **A** | 全圖節點 | `data/elevation/elevation_nodes.jsonl` 全量（已套 lifecycle overlay） | **1054** 顆 |
| **B** | 四層節點 | A 中 `node_type ∈ {value, belief, trait, essence}` | **227** 顆 |
| **C** | 非隔離四層 | B 中不在隔離集者 | **225** 顆 |
| **D** | 可召回狀態節點 | A 中 `lifecycle_state ∈ {dormant, superseded}` | **18** 顆（dormant 18 / superseded 0） |
| **E** | SAGE 事實 | 47 個 `data/memory/*/graph.sqlite` 的 `facts` 表（**沙盒複製後**才查） | **2132** 筆 |
| **F** | 生命線頭 | 45 份 `data/soul/*/life_threads.jsonl` | **1064** 筆 |
| **G** | 對話 | `data/conversations/*.json` | 153 檔 / 886 筆（user 角色 391） |

---

### H-A「SAGE Fact 存入時的門檻過高？」→ **結論：不成立**

#### A-1 實際寫入 facts 的數量（母體 E）

| 分面 | 數量 |
|---|---|
| 總數 | **2132 筆**（47 個 agent DB 中 13 個非零） |
| 分 agent | `agent_yua` 856／`agent_ruka` 646／`agent_anna` 214／`agent_rem` 138／`agent_mai` 120／`agent_akane` 99／`agent_mahiru` 43／`agent_miku` 5／`agent_ram` 5／`agent_aoi` 3／`agent_mami` 1／`agent_minami` 1／`agent_shino` 1 |
| 分 `source` | `inference` 1971（92.45%）／`user` 161（7.55%） |
| 分 `origin` | `lived_experience` 2132（**100%**） |
| distinct `predicate` | **886** 種；長度分布：1 字 78 種、2 字 306 種、3 字 179 種、4 字 151 種 |
| top predicates | `是` 204／`去` 118／`有` 109／`來` 79／**`life_thread_dissolved` 51**／`不是` 39／`到` 33／`問` 30／`说` 27／`在` 26 |

#### A-2 寫入門檻的程式位置與條件

`src/memory/sage/writer.py`：

| 位置 | 條件 | 效果 |
|---|---|---|
| `writer.py:196` `extract_and_write()` | 呼叫端 | LLM／heuristic 抽取 → `add_facts_batch()` |
| `writer.py:286` `write_turn()` | user 內容 `source="user"`；assistant 內容 `source="inference"`（:335-344） | 一輪對話 = 2 次抽取 |
| `writer.py:349` `_write_single()` ① | `fact.validate()` schema gate（:358-362） | 驗證失敗 ⇒ `rejected`，不寫 |
| `writer.py:349` ② | predicate 正規化 `_PREDICATE_SYNONYMS`（:365） | 同義詞收斂 |
| `writer.py:349` ③ | entity 對齊 `_align_entity`（:370-371） | 主／客体正規化 |
| `writer.py:349` ④ | contradiction detection `_find_contradiction`（:374-383） | **不擋寫**，只調整 weight／confidence |
| `writer.py:349` ⑤ | **重複偵測與合併 `_find_similar`**（:386-394） | **重複 fact 只 `update_weight`，不新增列** ← 唯一會讓「有效 fact 不入庫」的非驗證機制 |
| `writer.py:349` ⑥ | `store.add_fact(fact)`（:397） | 寫入 |

⇒ **不存在任何數值門檻**（grep 全檔無 `min_confidence`／`min_weight` 擋寫路徑）。寫入門檻 = schema 驗證 ＋ 去重合併，兩者都不構成「門檻過高」。

#### A-3 與「理論上可進池的節點數」的差距歸因

| 項目 | 數量 |
|---|---|
| 理論上可進池的節點（四層） | **227** 顆（母體 B） |
| 實際進池 | **0** 顆 |
| facts → elevation 證據邊 | **41 / 2275 = 1.80%** |
| 41 條 sage_fact 邊的目標節點型別 | `pattern` 23／**`belief` 18**／`value` **0**／`trait` **0** |
| 41 條邊的 `source_id` 命中 live `fact_id` | **41 / 41（100%）** |
| 41 條邊對應事實的 `predicate` | **`life_thread_dissolved` 41 / 41（100%）** |
| 41 條邊對應事實的 `source` | **`inference` 41 / 41（100%）** |
| 其餘 886−1 種 predicate（含 78 種單字動詞） | **0** 條 elevation 邊 |

**差距歸因**：差距**不在寫入門檻**（2132 筆已寫入，量級遠超池需求），而在**寫入之後的兩段**：
1. **接線極薄**：2132 筆 facts 只貢獻 1.80% 的證據邊；圖譜其餘 2234 條（98.20%）來自 `inner_life_event`（diary 986+963、world 97+84、dream 35、support_propagation 69）。
2. **predicate 形態不可昇華**：886 種 predicate 中 585 種長度 ≤3 字，語意上無法支撐 belief/value/trait 的昇華；實際上只有 `life_thread_dissolved` 這一支曾成功變成邊。

⇒ **H-A 不成立。** 寫入端健康（2132 筆），瓶頸在「facts → elevation 證據邊」這一段。

---

### H-B「召回查詢的 SQL／向量過濾條件太苛刻？」→ **結論：不成立**

#### B-1 實際 WHERE 條件（逐條，SSOT＝`src/inner_life/elevation_recall.py` 的 `_is_recallable_node()`）

| # | 條件 | 排除顆數 | 占母體 A | 備註 |
|---|---|---|---|---|
| 1 | `node.agent_id == "default"`（world 節點） | **103** | 9.77% | 全部 world 節點 |
| 2 | `node.agent_id != <查詢 agent>` | 見 B-3 | — | per-query 閘門；對每個 agent 都排除幾乎全部他人節點 |
| 3 | `node.node_type not in PROJECTABLE_NODE_TYPES`（四層＝`belief`/`value`/`trait`/`essence`） | **827** | 78.46% | `pattern` 結構性不可召回 |
| 4 | 缺 `node_id` 或 `content` 非 `str` | **0** | 0.00% | 全圖無殘缺節點 |
| 5 | 隔離（quarantine） | **18** | 1.71% | 其中**四層內** 2 顆（`agent_akane` 1、`agent_aoi` 1） |
| 6 | `lifecycle_state not in {dormant, superseded}` | **1036** | 98.29% | `active` 783 ＋ `weakening` 253 |

**🔴 向量相似度門檻：不存在。** `src/inner_life/` 全目錄 grep `embedding|cosine|similarity|vector|score_threshold|min_score` 只命中 `trace_reader.py`（其 docstring 明寫 "No database, no embeddings, no vector search, no caching"）與 `__init__.py` 的說明文字 —— **召回路徑沒有任何向量檢索**。

**🔴 score 門檻：不存在。** 召回 = 純字串比對：contract path ＝整句子字串；token path ＝union tag 子字串（`_normalize_tags`）。回傳**全部**命中，無排序截斷、無 top-k、無分數門檻。

另有 probe 層 tag 閘門（`MIN_RECALL_TAG_LENGTH = 2` / `MAX_RECALL_TAGS = 12`）—— 影響 probe 端而非候選池；本次 200 個 probe 中 **4 個** tags 全無效（正規化後長度 < 2）被跳過，但仍計入分母 200。

#### B-2 排除原因分佈（池級，互斥；順序逐字對齊 `pool_summary`；母體 A＝1054）

| 條件 | 排除顆數 | 占母體 A |
|---|---|---|
| `excluded_pattern`（`node_type` 非四層） | **827** | **78.46%** |
| `excluded_state_active` | **224** | 21.25% |
| `blocked_by_quarantine` | **2** | 0.19% |
| `excluded_state_weakening` | **1** | 0.09% |
| **`recallable`（池）** | **0** | **0.00%** |
| 合計 | 1054 | 100.00% ✓ |

邊際分佈（各自獨立計數，同一節點可被多條件命中）：

| 條件 | 顆數 | 占母體 A |
|---|---|---|
| `excluded_pattern` | 827 | 78.46% |
| `excluded_state_active` | 783 | 74.29% |
| `excluded_state_weakening` | 253 | 24.00% |
| `quarantined` | 18 | 1.71% |
| 可召回狀態（dormant＋superseded） | 18 | 1.71% |
| `superseded` | **0** | **0.00%** |

**排除量最大的一條 ＝ `excluded_pattern`（827 顆 / 78.46%）**。但它對四層池不具決定性（`pattern` 是昇華前的中間層，結構性不可召回）。

**真正對池具決定性的一條 ＝ 狀態閘門**：全圖 `superseded` = **0**；`dormant` = **18**，而這 18 顆 **100% 落在隔離集內**（`dormant ∩ clean = 0`）⇒ 池 = 0。
交叉表（母體 A）：

| `node_type` | `lifecycle_state` | 隔離 | 顆數 |
|---|---|---|---|
| pattern | active | clean | 559 |
| pattern | weakening | clean | 252 |
| pattern | **dormant** | **Q** | **16** |
| value | active | clean | 211 |
| value | **dormant** | **Q** | **2** |
| belief | active | clean | 8 |
| belief | weakening | clean | 1 |
| trait | active | clean | 5 |
| — | superseded | — | **0** |

#### B-3 查詢路徑漏斗（逐 agent；母體＝每次查詢遍歷的全部 1054 顆）

以 `agent_yua` 為例：`step0_seen` 1054 → `excluded_world_default` 103 → `excluded_other_agent` 891 → `excluded_not_projectable` 48 → `excluded_state_gate` 12 → **`recallable_candidates` = 0**。
`agent_ruka` 888/50/13、`agent_akane` 887/52/**`excluded_quarantined` 1**/11、`agent_aoi` 891/47/**`excluded_quarantined` 1**/12、`agent_anna` 889/49/13。
**46 個 agent 的 `recallable_candidates` 全部為 0**（無一例外）。

#### B-4 反事實（決定性）：放寬任一條件都到不了門檻

| 情境 | 池大小 | 對 `GO_MIN_RECALLABLE_POOL = 5` |
|---|---|---|
| 依定義（全部守門） | **0** | 差 5 顆 |
| 拿掉**隔離**閘門 | **2** | 仍差 3 顆 ❌ |
| 拿掉**型別**閘門 | **0** | 差 5 顆 ❌ |
| 拿掉**狀態**閘門 | 225 | 過（但語意已毀：active 進池） |
| 全部拿掉 | 1054 | 過（無意義） |

⇒ **池 0 不是門檻苛刻造成的**：最寬鬆而仍維持「四層 × dormant|superseded」語意的情況下，池也只有 **2** 顆 < 門檻 5。

#### B-5 結論

**H-B 不成立。** 召回路徑的過濾條件**不苛刻**，而是**過濾了一個本身為空的集合**。真正的問題在產出端：沒有任何節點處於可召回狀態（`dormant` 全被隔離、`superseded` 結構性不可達）。

---

### H-C「歷史 Life Thread／對話從未被寫入召回候選表？」→ **結論：部分成立**

#### C-1 線頭總數 與「可能成為候選者」的數量

| 項目 | 數值 |
|---|---|
| 線頭總數（母體 F） | **1064 筆**（45 個 agent，全部 `bad_json = 0`） |
| 具 thread 形狀欄位的記錄 | **1064 / 1064（100%）** |
| 欄位出現率 100% 的鍵 | `thread_id`／`event_seq`／`event_type`／`origin_type`／`status`／`title`／`narrative_content`／`created_at`／`updated_at`／`check_after_ts`／`share_target`／`dissolved_at`／`sage_fact_id`（各 1064/1064） |
| **依召回的實際欄位/型別定義可成為候選者** | **0 / 1064（0.00%）** |

「召回候選者」的實際定義（取自 `_is_recallable_node()` 的欄位與型別要求）：需同時滿足
`node_id` 存在 ＋ `node_type ∈ {belief, value, trait, essence}` ＋ `content` 為 `str` ＋ `agent_id ≠ "default"`。
實測：`node_type` 欄位出現數 = **0**（`node_type_presence = {}`）；亦無 `content`／`node_id`／`lifecycle_state` 欄位（線頭用 `narrative_content`／`title`／`status`）。
⇒ **life thread 的 schema 與召回候選定義結構性不相容 ⇒ 永遠不可能直接成為召回候選。**

#### C-2 對話來源是否有任何路徑寫入候選表（以程式碼為準）

| 檢查 | 結果 |
|---|---|
| 對話總量（母體 G） | 153 個 JSON 檔 / 886 筆記錄（user 角色 391）/ 10 個 agent |
| 對話 → SAGE facts | **有**：`writer.py:286` `write_turn()` 以 `source="user"` 寫入 **161** 筆 facts（`inference` 另 1971 筆） |
| 這 161 筆 `source="user"` facts → elevation 邊 | **0 條** |
| elevation 圖譜 `trigger_type` 全清 | `diary:night` 986／`diary:morning` 963／`world:weather_temp_change` 97／`world:rain_started` 84／`support_propagation` 69／`dream:dream` 35／`memory_fact` 41 —— **無任何 conversation 類 trigger** |
| 對話 → candidate table 的**直接**路徑 | **不存在**（程式碼層面：無任何 writer 把 conversation 記錄寫成 elevation node） |

#### C-3 間接路徑：接了，且實際生效過（但極薄）

程式碼：`src/soul/life_thread_consolidation_wiring.py:313-314`
```
_engine.consume(_ea.sage_fact_to_input(_fact_obj, agent_id=agent_id))
_ea._persist_result(store_dir, _nodes, _engine.evidence_edges)
```
數據佐證（母體 E）：

| 轉換段 | 數量 | 轉換率 |
|---|---|---|
| 線頭（1064）→ `life_thread_dissolved` 事實 | **51** 筆 | 51/1064 = **4.79%** |
| 該 51 筆事實 → elevation 證據邊 | **41** 條 | 41/51 = **80.39%** |
| 41 條邊的 `source_id` 命中 live `fact_id` | **41 / 41** | 100% |

⇒ 線頭接線**是通的**，但要過兩道閘門：①線頭必須走到 `dissolved` 終態才會產生 `life_thread_dissolved` 事實；②該事實必須再通過 `engine.consume()` 的證據門才成為節點與邊。

#### C-4 結論

**H-C 部分成立**：
- **「根本沒接」** —— 對**直接路徑**成立：life thread（0/1064）與對話（0/886）本身**從未被寫入候選表**，schema 不相容，程式碼層面也無此 writer。
- **「接了但被門檻擋掉」** —— 對**間接路徑**成立：life thread → dissolved → SAGE fact → `consume()` → `persist()` **已接線且已生效 41 次**，但被「須 dissolved 終態（4.79%）」與「consume 證據門（80.39%）」擋掉，最終只貢獻 1.80% 的證據邊。
- **對話則是「根本沒接」**：161 筆 user facts 產生 **0** 條 elevation 邊。

---

## ③ 排除原因分佈表（彙整）

### 池級（互斥；母體 A＝1054 顆）

| 條件 | 排除顆數 | 占母體 A | 備註 |
|---|---|---|---|
| `excluded_pattern` | **827** | **78.46%** | 排除量最大的一條；但對四層池不具決定性 |
| `excluded_state_active` | **224** | 21.25% | 對池具決定性（池不收 active） |
| `blocked_by_quarantine` | **2** | 0.19% | `agent_akane` 1、`agent_aoi` 1（皆 `value`） |
| `excluded_state_weakening` | **1** | 0.09% | `belief` × 1 |
| **`recallable`（池）** | **0** | **0.00%** | — |
| 合計 | 1054 | 100.00% | ✓ |

### 邊際（獨立計數；母體 A＝1054 顆）

| 條件 | 顆數 | 占母體 A |
|---|---|---|
| `excluded_pattern` | 827 | 78.46% |
| `excluded_state_active` | 783 | 74.29% |
| `excluded_state_weakening` | 253 | 24.00% |
| `quarantined` | 18 | 1.71% |
| 可召回狀態（dormant 18 ＋ superseded 0） | 18 | 1.71% |
| `superseded` | **0** | **0.00%** |

### 查詢級（母體＝每次查詢遍歷的 1054 顆；46 個 agent 全部 `recallable_candidates = 0`）

| 條件 | 排除顆數（`agent_yua` 實例） | 占母體 A |
|---|---|---|
| `excluded_world_default` | 103 | 9.77% |
| `excluded_other_agent` | 891 | 84.54% |
| `excluded_not_projectable` | 48 | 4.55% |
| `excluded_quarantined` | 0 | 0.00% |
| `excluded_state_gate` | 12 | 1.14% |
| **`recallable_candidates`** | **0** | **0.00%** |

---

## ④ 既有 CLI 三項輸出 ＋ 門檻差距 ＋ stdout/stderr 分流

工具：`scripts/recall_readiness_cli.py`（既有唯讀工具，`sandbox-only` 模式）。
執行方式：`--source-dir <主 repo>\data\elevation --conversations-dir <主 repo>\data\conversations --report %TEMP%\recall_readiness_report_diag.md`。
分流：PowerShell 5.1 會把 native stderr 併進 error stream ⇒ 改用 `Start-Process -RedirectStandardOutput/-RedirectStandardError` **實體分流**兩個檔。

| 項目 | CLI 輸出 | 門檻（`src/inner_life/recall_readiness.py`） | 差距 |
|---|---|---|---|
| `pool_summary` → 可回憶池 | **0** 顆 | `GO_MIN_RECALLABLE_POOL = 5`（:38） | **差 5 顆** |
| 池中 `superseded` | **0** 顆 | `GO_MIN_SUPERSEDED = 1`（:40） | **差 1 顆** |
| `hit_rate`（token-level，新入口） | **0 / 200 = 0.0000** | `GO_MIN_HIT_RATE = 0.05`（:50） | **差 5.00 個百分點** |
| `hit_rate`（contract path 對照組） | 0 / 200 = 0.0000 | （對照組） | — |
| 非隔離命中比例 | **1.0000** | `1.0 − GO_MAX_QUARANTINED_HIT_RATIO`（= 0.0）＝ 1.0 | 🔴 **無定義（0/0 退化值）** |

**判定：stdout 的 `VERDICT: NO-GO`**；exit code = **1** ＝ **設計行為（NO-GO ⇒ exit 1），不是崩潰**。判定以 VERDICT 段為準。

### 🔴 0/0 退化值警示（必須照工單 §2.4 第 3 點處理）

CLI 的 `non_quarantined_hit_ratio = 1.0000` 來自 `src/inner_life/recall_readiness.py:230`：
```python
ratio = (non_quarantined / match_pairs) if match_pairs else 1.0
```
本次 `match_pairs = 0`（總命中 0）⇒ 該 `1.0` 是**硬寫的 0/0 退化值**。CLI 表格因此把它標為「通過」。
**正確表述是「總命中 0，比率無定義」；不可解讀為「隔離 0% 命中已達成」。** 本報告對此項一律標「無定義」。

### stdout / stderr 分流實況

| 流 | 檔案 | 內容 |
|---|---|---|
| stdout | `%TEMP%\recall_cli_stdout_20261024a.txt` | 完整 CLI 報告（含 `VERDICT: NO-GO`），非 ASCII 字元被 CLI 自身轉成 `\uXXXX` 跳脫 |
| stderr | `%TEMP%\recall_cli_stderr_20261024a.txt` | **4 行，全是 jieba 首次載入的正常訊息**：`Building prefix dict from the default dictionary ...` / `Loading model from cache ...\jieba.cache` / `Loading model cost 0.490 seconds.` / `Prefix dict has been built successfully.` —— **不是錯誤** |

CLI 另輸出報告檔 `%TEMP%\recall_readiness_report_diag.md`，其生產位元組對帳兩檔皆 `True`（見 §⑥）。

⚠️ CLI 第 5 行印「來源＝repo 生產目錄 = False」—— 這是**外觀現象，非資料問題**：該旗標以「腳本自身路徑推導的 `REPO_ROOT`」比對，而本次腳本從 worktree 執行（`REPO_ROOT`＝worktree），但 `--source-dir` 指向的**確實是主 repo 生產目錄**（絕對路徑已列印於 stdout 第 4 行）。本報告的數字另以主 repo 絕對路徑獨立覆核一致。

---

## ⑤ 生產零寫入證據（位元組對帳）

### 對帳母體（**刻意不比對整棵 `data/` 樹**）

依工單 §3.5，只比對「會受本票影響」的路徑：
1. `data/memory/*/graph.sqlite` ＋ `-wal` ＋ `-shm`（SAGE 事實庫家族）
2. `data/soul/**`（生命線頭等，45 個 agent）
3. `data/elevation/elevation_nodes.jsonl`、`data/elevation/elevation_lifecycle_events.jsonl`（本票唯讀來源）

⇒ **合計 1244 個路徑**。**未**比對 `data/inner_life/trace.jsonl` 等線上服務會並行寫入的檔案（整樹比對必然 flaky）。

### 對帳結果

| 項目 | 結果 |
|---|---|
| 對帳路徑數 | **1244** |
| `sha256` / `size` / `mtime_ns` 變動的檔案 | **0 個**（`changed = []`） |
| 結論 | **production unchanged = True** |

CLI 自身的兩檔對帳（`%TEMP%\recall_readiness_report_diag.md`）：

| 檔案 | before | after | 相同 |
|---|---|---|---|
| `elevation_nodes.jsonl` | `a1cd6a2b04faf9fb` | `a1cd6a2b04faf9fb` | **True** |
| `elevation_lifecycle_events.jsonl` | `b5ed3b852aaf453e` | `b5ed3b852aaf453e` | **True** |

### 其他邊界（逐項自查）

| 邊界 | 狀態 |
|---|---|
| 寫／改名／刪除 `data/**` | **0** |
| 寫 `src/**` | **0** |
| 對生產 sqlite 開連線（含 `mode=ro`） | **0** —— 一律先複製 `graph.sqlite` ＋ `-wal` ＋ `-shm` 到 `tempfile.mkdtemp()` 沙盒再查 |
| 清表／重置登錄表／觸發合成事件 | **0** |
| 真實 LLM 呼叫／連網 | **0** |
| 跑 `scripts/manual_*.py` | **0** |
| 重啟服務／殺行程 | **0** |
| `git add`／commit／push | **0** |
| 程式變更 | **0 檔** ⇒ **未跑全庫回歸**（無程式變更即無回歸對象） |

---

## ⑥ 關鍵結構發現（三個假設之外，但決定修復方向）

### 發現 1：`superseded` **結構性不可達** ⇒ `GO_MIN_SUPERSEDED = 1` 永遠達不到

- `record_contradiction()` 的**唯一**呼叫點在 `engine.supersede()` 內：`soul_elevation/src/soul_elevation/engine.py:1178`。
- `supersede()` 又以「矛盾證據數達門檻」為觸發前提 ⇒ 累積器恆空 ⇒ 死鎖。
- 專案內已載明此死鎖：`src/inner_life/contradiction_probe.py:11-13`（"**結構性死鎖**（累積器恆空 ⇒ `supersede()` 恆拋 `ValueError`）"）；該模組 Phase 1 **只定義、只量測、不落地**（:23-24），Phase 2 寫入路徑尚不存在。
- 實測佐證（母體 A）：1054 顆中 `contradiction_pressure` 非空 = **0**、`superseded_by` 非空 = **0**；sidecar 271 筆事件 `event` 分佈 = `lifecycle_transition` 253 ＋ `quarantine` 18，**無任何 superseded 事件**。
- 門檻原義（`recall_readiness.py:40`）："缺它就不會有『以前覺得 A，後來發現 B』" ⇒ **戰役二（Belief 長效保活）恰恰依賴這條結構性缺口。**

### 發現 2：唯一的 `dormant` 產生者是隔離處置 ⇒ 池的 dormant 分支天然是空腿

- sidecar 271 筆事件的 `state_before` 全為 `active`；`state_after` = `weakening` 253 ＋ **`dormant` 18**。
- 那 18 筆 `dormant` 全部來自 `quarantine` 事件，`reason` = `"SOUL-BUG-010 historical truncation victim (SOUL-BUG-011 left-truncation signature; head cut mid-token)"`，`ts` 全部 = `2026-10-19T19:30:00+00:00`（單次處置）。
- ⇒ `quarantine` 事件把 `lifecycle_state` 同時寫成 `dormant` ⇒ 這條路徑產生的 `dormant` **必然**被隔離閘門擋住（`dormant ∩ clean = 0`）。

### 發現 3：衰減路徑**可以**產 `dormant`，但離現在還有約 23 天

- 狀態機（`soul_elevation/engine.py:1326-1360`）：`ACTIVE --(days_since_anchor ≥ 7)--> WEAKENING --(≥ 30)--> DORMANT`，**一次評估最多轉移一級**；`SUPERSEDED` 為終態。
- 錨點（anchor）＝仍有效支持邊的 `valid_from_ts` 最大值 → 否則 `last_support_ts` → 否則 `created_ts + 3 天 grace`（`prior.py:60-65`；`LIFECYCLE_T_WEAKEN_DAYS = 7`／`LIFECYCLE_T_DORMANT_DAYS = 30`／`LIFECYCLE_GRACE_DAYS = 3`）。
- 實測（母體 B＝227 顆四層節點，全數 `anchor_src = active_edge`；2275 條邊全為 still-valid）：

| `days_since_anchor` | 顆數 |
|---|---|
| < 7 天 | **215** |
| 7–14 天 | **10** |
| 14–30 天 | **0** |
| ≥ 30 天 | **0** |

⇒ **目前不存在任何一顆離 `dormant` 不到 23 天的非隔離四層節點**；最早的非隔離 `dormant` 落在 **2026-11-16/17（UTC）**（anchor 2026-10-17T12:00–12:28Z ＋ 30 天）。

### 發現 4：今晚 22:00 夜槽會把 9 顆 Legacy Value 轉成 `weakening`，但那**不會**讓池長大

依錨點量測，下列 **9 顆**非隔離 `value` 的 `days_since_anchor` = 7.15–7.17 天（剛好跨過 `T_WEAKEN = 7`）⇒ 下一輪 night slot（`src/soul/scheduler.py:1935` 呼叫 `evaluate_lifecycle_all()`）會轉 `active → weakening`：

| agent | `node_id` | anchor | days |
|---|---|---|---|
| `agent_anna` | `d0df567934274a9697feff6d1b0d9af5` | 2026-10-17T12:28:12Z | 7.15 |
| `agent_aoi` | `24bad790f8954dc2ae4711c4c1c6ffac` | 2026-10-17T12:00:28Z | 7.17 |
| `agent_mahiru` | `6a92c906b245458cb0c4c64ea214f2eb` | 2026-10-17T12:28:12Z | 7.15 |
| `agent_mai` | `a8b48b0210e74363b506f5750d61d125` | 2026-10-17T12:28:12Z | 7.15 |
| `agent_miku` | `56391bd5ce154d9f8ee09ef40280ec71` | 2026-10-17T12:28:12Z | 7.15 |
| `agent_ram` | `7fcf5557b1994fd1977973f7dcdf5a38` | 2026-10-17T12:28:12Z | 7.15 |
| `agent_rem` | `8dd335036f21453b85c72d17eaf5b303` | 2026-10-17T12:00:13Z | 7.17 |
| `agent_ruka` | `9f9b13498c334268aaf2e95429bd2228` | 2026-10-17T12:00:20Z | 7.17 |
| `agent_yua` | `60a2d5e453914ac3bbbbc02cb549b076` | 2026-10-17T12:00:10Z | 7.17 |

⇒ 但 `weakening` **不在** `RECALLABLE_LIFECYCLE_STATES = {dormant, superseded}` ⇒ **夜槽後池仍為 0**。

---

## ⑦ 最小修復點建議（**只建議，不實作；實作另開票**）

### 建議 1（首選）：補上 `superseded` 的生產路徑 ＝ `contradiction_probe` 的 Phase 2

- **為什麼是最小**：這是**唯一**能把 `superseded` 從 0 變成 ≥1 的路徑 —— `GO_MIN_SUPERSEDED = 1` 在現行接線下**結構性不可達**（發現 1）。其餘所有修法都只是把 `dormant` 這條單腿路線做厚，而 `dormant` 的最快自然時點是 2026-11-16/17（發現 3）。
- **實作時要注意**：`LIFECYCLE_N_SUPERSEDE = 3`、`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD = 2`、`LIFECYCLE_ESSENCE_N_SUPERSEDE = 5`、`LIFECYCLE_ESSENCE_CONFIDENCE_DELTA = 0.3`（`soul_elevation/prior.py:68-73`）是**契約不是參數**，**不得調低**；矛盾必須跨 ≥2 個模擬日（防單日噪声）；essence 層另有多條件同時滿足；Phase 2 必須維持 `contradiction_probe` 既有的「零 IO／零 LLM／純函式／零新欄位」邊界。

### 建議 2（最快見效，但屬契約變更）：把池的可召回狀態集合重新裁示（`RECALLABLE_LIFECYCLE_STATES`）

- **為什麼是最小**：只需改**一處常數集合**（`src/inner_life/elevation_recall.py`），**不碰任何寫入路徑**、不動資料面、不需等 23 天；且能立刻讓今晚那 9 顆 `weakening` value 進池（池 = 9 ≥ 5 ⇒ 直接過 `GO_MIN_RECALLABLE_POOL`）。
- **實作時要注意**：這是**frozen contract 級的語意變更**（池的定義＝「老化後仍可被憶起」；放寬後 `active→weakening` 的節點立刻可召回，等於把「老化」與「退場」拆開）⇒ **必須主大腦＋Owner 拍板**；門檻差距須**重算**（命中率仍可能 < 5%，見未解問題 1）；`weakening` 節點內容品質需先抽驗（211 顆 `active` value 內容來自 diary 昇華）；並須確認 `pool_summary` 與 `_is_recallable_node` 共用同一組狀態（避免兩處不一致）。

### 建議 3（把 `dormant` 的兩種語意解耦，或明文宣告）

- **為什麼是最小**：現行 `quarantine` 事件把 `lifecycle_state` 同時寫成 `dormant`（發現 2），使「dormant」同時代表「老化退場」與「隔離退場」⇒ 池的 dormant 分支現實上只有隔離一條腿，而那條腿必被隔離閘門擋住。解耦只需動 quarantine 事件的狀態寫入語意（單一事件欄位層級）。
- **實作時要注意**：**不建議**為了讓池長大而調低 `LIFECYCLE_T_DORMANT_DAYS = 30` —— 那會把「失去支持」與「年齡」混淆，違反 `prior.py:62` 的設計語意（"old ≠ outdated——年齡不是退場理由，失去支持才是"）。若走此路，最早的非隔離 `dormant` 落在 **2026-11-16/17（UTC）**，且仍須先驗命中率是否達 5%。

---

## ⑧ 仍未解的問題（本票未能判定）

| # | 問題 | 為何本票未能判定 |
|---|---|---|
| 1 | **池非空 ⇒ 命中率是否 ≥ 5%？** | 池 = 0 時命中必然 = 0，**無法反推**。且召回為純字串比對（子字串），命中率對內容相似度極敏感。CLI 的 200 個 probe 中有 4 個「tags 全無效」被跳過但仍計入分母 200。⇒ **即使池修好，GO 仍可能只過 2/3 項。** |
| 2 | 工單 §0 所述「10 顆 Legacy Value」與本次量得 **9 顆**的差異歸屬 | 工單未給 node_id；本次依錨點量得 9 顆**非隔離** value 會轉 weakening，推測第 10 顆是 `agent_akane` 已被隔離（dormant）的 value。**本票未能判定**確切清單。 |
| 3 | worktree 與主 repo 的 HEAD 落差 | worktree = `85b0ab7`，主 repo = `7facd88`（多一個 commit：P5-②）。已逐檔比對 `git diff 85b0ab7..7facd88 -- src/inner_life/ src/sage/ scripts/recall_readiness_cli.py` → 僅 `tension_closure_loop.py` / `tension_thinking.py` 變動，**召回與 SAGE 路徑逐位元相同** ⇒ 量測結論對生產有效；但主 repo 的 `ENGINEERING_STATE.md` 已有 §0.1.64（worktree 僅到 §0.1.63）⇒ 追加條目編號採 **§0.1.66** 以免撞號。 |
| 4 | CLI 快照與最終整合快照相差 1 顆節點 | CLI 執行時 1053 顆（~16:03Z），最終整合量測 1054 顆（16:05:53Z）⇒ 線上服務在量測期間新增 1 顆節點。兩次快照的**池數字同為 0**，結論不受影響；但「1054」是 16:05:53Z 的快照值。 |
| 5 | `quarantine` 事件為何同時寫 `lifecycle_state = dormant` | 本票只**記錄現象**（18/18 筆如此）。判定其為刻意語意耦合抑或當初副作用，涉 frozen contract，**本票不動**。 |
| 6 | `dormant` 是否**應該**等於「隔離退場」 | 屬池定義的語意裁示，不是量測能回答的問題。已列入 §⑦ 建議 3 交由主大腦＋Owner 拍板。 |

---

## ⑨ 對戰役二（Belief 長效保活）的直接建議

1. **戰役二的前提尚未成立。** 戰役二的核心是「以前覺得 A，後來發現 B」⇒ 需要池內有 `superseded` 節點。而 `superseded` 現況 = **0 顆**且**結構性不可達**（發現 1）⇒ **現在開戰役二，等於建在未通的地基上**（與工單 §0 的判斷一致，但本票給出了更強的證據：不是「門檻沒調好」，是「**生產路徑不存在**」）。
2. **今晚 22:00 夜槽不會改善池。** 9 顆 value 轉 `weakening`，而 `weakening` 不在池內（發現 4）。⇒ 開戰役二前，池的預期值仍是 **0**。
3. **建議順序**：先開票做 §⑦ 建議 1（`superseded` 寫入端）⇒ 再依 §⑦ 建議 2 的裁示結果決定池的狀態集合 ⇒ 池 ≥ 5 且 superseded ≥ 1 且命中率 ≥ 5% 三項全綠後，再啟動戰役二。
4. **不要**用「手動消紅」「偽造邊」「放寬門檻讓它出數字」來過 GO 閘門 —— 依 Owner 2026-10-23 裁定（告警疲勞比假紅更壞），GO 判準必須維持可執行的真實條件。

---

## ⑩ 未做（明文排除）

- **未改任何程式**：`src/**` 一行未動 ⇒ **未跑全庫回歸**（本票 0 程式變更）。
- **未寫／未改名／未刪除 `data/**`**；**未對生產 sqlite 開任何連線**（含 `mode=ro`）—— 一律沙盒複製後再查。
- **未清任何表、未重置任何登錄表、未觸發任何合成事件**（synthetic diary／dream／事件）。
- **未打真實 LLM 呼叫、未連網、未跑 `scripts/manual_*.py`、未重啟服務、未 kill 行程**。
- **未 commit／push／git add**；未動主 repo（主 repo 的 `data/` 與 `logs/` 本票全程唯讀）。
- **未修改工單** `logs/RECALL-POOL-ZERO-DIAGNOSIS-WORK-ORDER.md`。
- **未實作任何修復**（§⑦ 三條建議均為建議；實作另開票）。

---

*本報告由 RECALL-POOL-ZERO-DIAGNOSIS 工單執行者產出；所有數字可由 `%TEMP%\recall_diag_final.py` → `%TEMP%\recall_diag_final.json` 重現。*
