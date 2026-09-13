# ELEVATION-EVIDENCE-CONTRACT — 升華（固化）證據獨立性與桶配額契約（設計契約）

- **工單**：ELEVATION-EVIDENCE-AUDIT-1（**READ-ONLY 審計 ＋ DOCS-ONLY 契約起草**：0 code、0 測試、0 門檻常數改動、0 服務重啟、0 生產資料變更）
- **唯一產出**：`docs/ELEVATION-EVIDENCE-CONTRACT.md`（本檔）＋ `logs/ENGINEERING_STATE.md` 登記
- **契約日**：2026-09-13
- **起始 HEAD（實測）**：`db12df0` == `origin/main`（`git rev-parse HEAD` / `git rev-parse origin/main` 逐字元一致）
- **入口依據**：`docs/INNER-LIFE-AUDIT-1.md`（READ-ONLY 審計，HEAD `9cc0ad9` 時點）§3.2 / §3.3 / §3.5 / §10 斷點①「固化斷了」
- **性質**：**設計契約，非施工授權。** 本契約不自行生效；實作需另開工單，且**須在 2026-09-12T22:39:13 起的 24 小時崩潰歸零驗收閉環之後、經 Owner 明示授權**方可發出。
- **上游依賴**：`soul-elevation` **0.1.0**（editable 安裝，`C:\Users\bbfcc\.local\bin\soul-elevation`，`.venv\Lib\site-packages\__editable__.soul_elevation-0.1.0.pth` → `...\soul-elevation\src`；上游 repo HEAD `42939d4`，工作區乾淨）。**本契約涉及「跨 repo」邊界**，見 §6。

---

## §0 方法與量測聲明（先讀）

### 0.1 本票重測，不照抄審計報告

本契約**所有數字皆由本票重新量測**（2026-09-13，HEAD `db12df0`）。`INNER-LIFE-AUDIT-1` 的數字**僅作對照**，逐項標示「本次重測值」。量測期間生產服務持續運行（`:8000` pid **2312**），`data/elevation/*.jsonl` 為**活的 append-only 檔** → **本票的每一次量測都是時間點快照**（見 §1.0 快照時戳）；跨快照的節點數差異屬正常增長，不是矛盾。

### 0.2 量測方法（可複驗）

| 對象 | 方法 |
|---|---|
| `elevation_nodes.jsonl` / `elevation_edges.jsonl` | `[System.IO.File]::ReadAllText($f,[Text.Encoding]::UTF8)` 全文讀入 → 逐行 `ConvertFrom-Json` → 記憶體聚合。**全量樣本，非抽樣** |
| `elevation_trace.jsonl` | `System.IO.StreamReader` 逐行串流（18.3 MB，避免整檔載入）→ 全量事件分類 |
| 「獨立證據數」 | **不引用任何現成函式輸出**，直接以 `engine.py:148-173` 的演算法在記憶體重算，並**逐欄複製** `elevation_adapter.py:514-558`（`_rebuild_engine_for`）的載入過濾條件 |
| 生產拒絕原因 | `data/server_nohup.err`（現行世代日誌，1236 行／246 KB）逐行解析 `[elevate]` 行 |

### 0.3 🔴 三項方法論限制（否則會誤讀本契約所有數字）

1. **`server_nohup.err` 只覆蓋現行世代**：起點 `2026-09-12T22:39:13`（CRASH-F1-FIX 重啟）。**它不是歷史全量**。INNER-LIFE-AUDIT-1 的「82 個升華週期」來自 99 份歷史 `server_*.err`（覆蓋 58 天中的 27 天）；**本票只能重測現行世代窗口**（見 §1.5）。凡本契約的日誌衍生數字，一律標明**窗口**。
2. **JSONL 快照會漂移**：`data/elevation/` 三個檔在量測期間持續被寫入。所有節點／邊計數皆附快照時點。
3. **`soul-elevation` 為 repo 外 editable 依賴**：`src/soul_elevation/engine.py` **不在本 repo 版控內**。本契約引用它時一律標明 `(repo 外)` 並附絕對路徑，**不得**當成 `src/**` 引用。本票**未修改該 repo 任何檔案**。

---

## §1 現況量測（本次重測值）

### 1.0 快照時點與資產盤點

| 資產 | 審計報告值（`9cc0ad9`） | **本次重測值** | 一致？ |
|---|---|---|---|
| `elevation_nodes.jsonl` 節點數 | 2,528 | **2,584** | ⚠️ 差異 +56（**活的檔**，自然增長） |
| `elevation_edges.jsonl` 邊數 | 3,416 | **3,490** | ⚠️ 差異 +74（同上） |
| `elevation_trace.jsonl` 事件數 | 2,528（803 elevated / 1,725 created） | **2,584（821 elevated / 1,763 created）** | ⚠️ 差異 +56（同上） |
| `elevation_trace.jsonl` 檔案大小 | 17.5 MB | **18,307,026 bytes**（mtime `2026-09-13T13:09:33Z`） | ⚠️ |
| `data/server_nohup.err` | 不在審計樣本 | **1,236 行 / 246,028 bytes**（mtime `2026-09-13 09:24:51` 本地） | — |
| 現行世代起點 | — | **`2026-09-12T22:39:13`**（CRASH-F1-FIX 重啟） | — |

> **口徑統一**：本契約後文凡稱「本次重測值」，皆指上表快照（nodes 2,584 / edges 3,490 / trace 2,584）。

### 1.1 升華節點的實際結構（全量 2,584 節點）

**節點 schema（`dataclasses.asdict(ElevationNode)`）— 實測欄位出現次數**

| 欄位 | 出現次數 | 備註 |
|---|---|---|
| `node_id` / `node_type` / `content` / `confidence` / `stability` / `valence` / `agent_id` / `parent_node_id` / `lineage_depth` / `lineage_path` / `created_ts` / `provenance_ref` | **2,584（全數）** | 核心 12 欄 |
| `candidate_node_type` | **2,569** | **15 筆缺此欄**（全為 `belief`） |
| `superseded_by` / `reconsideration_candidate` / `last_support_ts` / `lifecycle_state` / `contradiction_pressure` | **1,806** | **778 筆缺此 5 欄** |

> 🔴 **本次新增發現（審計報告未記載）：節點 schema 非同質。** 前 778 行缺 SE-5 lifecycle 欄族、其中前 15 行連 `candidate_node_type` 都缺。這是**上游 SE-5（`42939d4`）落地前寫入的 legacy 節點**，屬**歷史遺留、非現行 bug**——但**任何「以欄位存在性做判定」的讀側程式都會踩到**（實測 `emergent_projection` / `io/gateway` 皆用 `.get()` 讀，目前安全）。本契約 §7 要求遷移時**明文標記此邊界**。

**node_type × agent 桶分佈（實測）**

| node_type | 總數 | `default`（world 桶） | 10 位生產 agent 合計 |
|---|---|---|---|
| `pattern`（候選） | **1,748** | 1,376 | **372** |
| `belief`（已固化） | **658** | **658** | **0** |
| `value`（已固化） | **142** | 0 | **142** |
| `trait`（已固化） | **36** | 0 | **36** |
| `essence` | **0** | 0 | **0** |

- **審計報告值（pattern 1,710 / belief 652 / value 132 / trait 34）→ 本次重測（1,748 / 658 / 142 / 36）**：方向一致、數值略增（活的檔）。
- **10 位生產 agent 合計**：審計 513（347 pattern + 132 value + 34 trait）→ **本次 550（372 + 142 + 36）**。
- **Essence 恆 0（本次重測確認）**：`essence` = **0**；`superseded_by != null` = **0**；`reconsideration_candidate = true` = **0**；`contradiction_pressure` 非空 = **0**；`lifecycle_state` 唯一值 = **`active`（1,806/1,806）**。→ **SE-5 四態狀態機在生產上從未發生任何一次轉移**（不是「防線被驗證過」，是「從未被觸及」）。

**agent 分佈（實測）**

| agent_id | 節點數 |
|---|---|
| `default` | 2,034 |
| `agent_ruka` | 100 |
| `agent_yua` | 55 |
| `agent_miku` | 54 |
| `agent_aoi` | 52 |
| `agent_akane` | 51 |
| `agent_anna` | 51 |
| `agent_ram` | 49 |
| `agent_mahiru` | 48 |
| `agent_rem` | 45 |
| `agent_mai` | 45 |

### 1.2 🔴 stub 比例（本次重測：**全量分類，非抽樣**）

分類規則：`trigger_stub` = `content` 匹配 `^[a-z_]+:[a-z_0-9]*`。

| content 形態 | 審計報告值 | **本次重測值** | 一致？ |
|---|---|---|---|
| 觸發標籤 stub | 1,808（71.5%） | **1,901（73.6%）** | ✅ 方向一致（比例隨新節點略升，因新增節點全為 stub） |
| 真實文本（新聞句） | 677（26.8%） | **683（26.4%）** | ✅ |
| 短標籤（<20 字，非 stub 前綴） | 43（1.7%） | **0** | ⚠️ **不一致**——本次重測的 43 筆已被 `^[a-z_]+:` 規則吸收（如 `dream:event` 命中 stub 前綴）；審計的分類邊界與本票不同，**兩者不衝突，是本票分類更嚴** |

**最關鍵的一格（本次重測，全量）**

| 桶 | 節點數 | stub 數 | 語義內容數 |
|---|---|---|---|
| **10 位生產 agent** | **550** | **550** | **0** |
| `default`（world 桶） | 2,034 | 1,351 | 683 |

> 🔴 **`agent` 桶：550 / 550 = 100% 全為觸發標籤 stub，0 筆語義內容。**
> 審計報告值 513/513（100%）→ **本次重測 550/550（100%）**：**完全一致**。

**agent 節點 content 的實際形態（去重後全清單，實測）**

| 出現次數 | content | 來源 `trigger_type` |
|---|---|---|
| 282 | `diary:night: slot=night` | `diary:night` |
| 120 | `diary:morning: slot=morning` | `diary:morning` |
| 42 | `dream:dream: all_agents_count=10; target_agent_id=agent_akane` | `dream:dream` |
| 36 | `diary:night` | `diary:night`（無 extras） |
| 34 | `dream:dream: all_agents_count=10; target_agent_id=agent_ruka` | `dream:dream` |
| 23 | `dream:dream: all_agents_count=10; target_agent_id=agent_yua` | `dream:dream` |
| 7 | `dream:event` | `dream:event` |
| 6 | `dream:dream: all_agents_count=10; target_agent_id=agent_rem` | `dream:dream` |

> **8 種字串覆蓋全部 550 個 agent 節點。** 其中 `agent_akane` / `agent_ruka` / `agent_yua` / `agent_rem` 是**夢境目標 agent 的 id 洩漏進另一個 agent 的信念內容**（例：`agent_miku` 的 `trait` 節點 content = `...target_agent_id=agent_akane`）——**語義上完全不成立**（別人的夢境目標成了我的性格）。此為 stub 問題的**可讀性證據**，非獨立缺陷。

### 1.3 「證據」的定義與計數位置（根因定位）

**判定「幾份獨立證據」的唯一函式**：`_count_independent_evidence(edges)` —**`C:\Users\bbfcc\.local\bin\soul-elevation\src\soul_elevation\engine.py:148-173`**（repo 外）。

```
engine.py:151   Independence contract：evidence_key = (source_id, event_identity)
engine.py:161-172   seen_sources / seen_events 雙集合；
                    同一 source_id → 跳過；同一 event identity → 跳過；兩者皆新 → count += 1
```

**獨立性維度（本次重測確認）**：**不是「跨事件」也不是「跨來源類型」，而是「跨 `(source_id, event_identity)` 有序對」——採 `source_id` **或** `event_identity` 任一重複即視為同一份。** 0 embedding / 0 相似度（`engine.py:159` 明文）。同檔另有 `_count_independent_contradictions`（`:195-212`）為**同一語意的矛盾證據版**，本票未觸及。

**`event_identity` 的解析**：`_resolve_event_identity(input)` — `engine.py:131-145`。

| `source_type` | `event_identity` | 實測後果 |
|---|---|---|
| `inner_life_event` | **= `source_id`**（`:139-140`） | **兩個去重鍵恆相同** → 該類證據的獨立性**完全由 `source_id`（= `event_id`）決定** |
| `world_event` | 取 `provenance.novelty_id` → 否則 `inner_life_event_id` → 否則 `event_id` | 由 `src/world/elevation_adapter.py:133-139` 的 provenance 提供 |

**本次重測（全量 3,490 邊）證實上表**

| `source_type` | 邊數 | `default` | agent 桶 | `inner_life_event_id = null` |
|---|---|---|---|---|
| `inner_life_event` | **3,441** | 2,701 | 740 | **0（0%）** |
| `world_event` | **49** | 49 | 0 | **49（100%）** |

- **agent 桶 740 邊的 `source_id == inner_life_event_id` 逐筆相等（740/740）**；distinct `source_id` = distinct `inner_life_event_id` = **372**。
- → **對 agent 桶而言，「跨來源類型」不存在（740/740 全為 `inner_life_event`）；「獨立性」實質退化成「不同 `event_id` 的個數」。**

**計數位置（呼叫鏈，逐跳已核對）**

```
scripts/run_server.py:675-676   _elevate_check()（WorldInnerLifeAdapterWithGate.handle_event 內，
                                每次 consume 後觸發）
scripts/run_server.py:511       elevate_matured_patterns()          ← min_evidence 未傳 → 用預設
src/inner_life/elevation_adapter.py:565   min_evidence = DEFAULT_ELEVATE_MIN_EVIDENCE
soul_elevation/engine.py:92     DEFAULT_ELEVATE_MIN_EVIDENCE = 2   ← 門檻 = 2，生產無任何覆寫
src/inner_life/elevation_adapter.py:661   engine.elevate(pids[0], min_evidence=min_evidence)
soul_elevation/engine.py:502    independent = _count_independent_evidence(active_edges)
soul_elevation/engine.py:503-507   independent < 2 → raise ValueError → 不升華
```

> **`min_evidence` 無任何生產覆寫路徑**（全 repo grep 僅 `engine.py:456` 預設值與 `elevation_adapter.py:565/661` 透傳）。**門檻 2 是硬編碼語意。**

**證據聚合範圍**：`engine.py:490-499` — 以「**同 `candidate_node_type` 的該 agent 全部 pattern**」聚合證據邊。**不是單一 pattern 的證據，是同維度整組的證據池。**

### 1.4 🔴🔴 world 桶 vs agent 桶失衡的真正來源（**本票對審計報告的重大更正**）

#### 1.4.1 審計報告的主張 vs 本次重測

| 主張 | 審計報告 | **本次重測** | 判定 |
|---|---|---|---|
| agent 桶獨立證據數 | 0~1（< 2） | **0 或 1，從未 ≥ 2**（詳 1.4.3） | ✅ **一致（且已由生產日誌證實）** |
| world 桶獨立證據數 | 「每次都能拿到 **1,363 份**」 | **= 1**（同一時點同一演算法） | 🔴 **不一致——審計報告此處推論有誤** |
| world 桶單次 `elevate` 聚合的 pattern 數 | 1,363 | **1,365 ~ 1,375（trace 實測）** | ✅ 一致（該數字是 **pattern 數**，不是獨立證據數） |

> 🔴 **更正一句話**：審計報告 §3.5 的「1,363」是 `node_elevated` 事件的 **`pattern_node_ids` 長度**（＝同維度 pattern 總數），**不是** `_count_independent_evidence` 的回傳值。**world 桶與 agent 桶在現行狀態下，獨立證據數都 = 1。** 兩者是**同一個卡點**（見 §2），不是「一邊太鬆、一邊不可能」。

#### 1.4.2 本次重測的重算結果（逐 agent / 逐候選維度）

| agent | candidate | patterns | 載入後有效 pattern 邊 | **獨立證據數** |
|---|---|---|---|---|
| `agent_akane` | trait / value | 8 / 26 | 0 / 0 | **0 / 0** |
| `agent_anna` | trait / value | 8 / 26 | 0 / 0 | **0 / 0** |
| `agent_aoi` | trait / value | 9 / 26 | 1 / 0 | **1 / 0** |
| `agent_mahiru` | trait / value | 6 / 26 | 0 / 0 | **0 / 0** |
| `agent_mai` | trait / value | 4 / 26 | 0 / 0 | **0 / 0** |
| `agent_miku` | trait / value | 10 / 26 | 0 / 0 | **0 / 0** |
| `agent_ram` | trait / value | 7 / 26 | 1 / 0 | **1 / 0** |
| `agent_rem` | trait / value | 4 / 26 | 0 / 0 | **0 / 0** |
| `agent_ruka` | trait / value | 9 / 62 | 1 / 0 | **1 / 0** |
| `agent_yua` | trait / value | 11 / 26 | 1 / 0 | **1 / 0** |
| **`default`** | **belief** | **1,376** | **1** | **1** |

**逐 agent 邊數對照（`agent_yua` / `agent_ruka` 為完整剖面）**

| agent | pattern 數 | 指向 soul node 的邊 | 被 consume 的 `(src,evt)` 鍵 | `pattern_edges_NOT_consumed` |
|---|---|---|---|---|
| `agent_yua` | 37 | 36 | 36 | **1** |
| `agent_ruka` | 71 | 0（**0 個 soul node**） | 0 | 71 → 但見下 |

> `agent_ruka` 有 71 個未被 consume 的 pattern 邊，卻**全部**不產生獨立證據——因為它們**塌縮到極少數 distinct `event_id`**（見 1.4.4）。

#### 1.4.3 生產日誌鐵證（`data/server_nohup.err`，現行世代窗口 27 個升華週期）

`[elevate] skip ... insufficient independent evidence to elevate: N < 2` 逐行解析（**635 筆，全數可解析，0 例外**）：

| 桶 | N=0 | N=1 | **N≥2** |
|---|---|---|---|
| 10 位生產 agent（trait + value） | **261** | **349** | **0** |
| `default`（belief） | **5** | **20** | **0** |

**逐 agent 明細（每 agent 61 筆 = 27 週期 × 2 候選 + 尾端未成對）**

| agent | N=0 | N=1 | **N≥2** |
|---|---|---|---|
| agent_akane | 41 | 20 | **0** |
| agent_anna | 37 | 24 | **0** |
| agent_aoi | 3 | 58 | **0** |
| agent_mahiru | 38 | 23 | **0** |
| agent_mai | 36 | 25 | **0** |
| agent_miku | 35 | 26 | **0** |
| agent_ram | 8 | 53 | **0** |
| agent_rem | 40 | 21 | **0** |
| agent_ruka | 11 | 50 | **0** |
| agent_yua | 12 | 49 | **0** |
| `default` | 5 | 20 | **0** |

> 🔴 **`N` 的取值集合 = {0, 1}。在現行世代 27 個週期內，「N ≥ 2」出現 0 次——對 agent 桶與 world 桶皆然。**
> 這比審計報告的結論**更強**：**不只 agent 端拿不到 2 份，world 桶在現行世代也拿不到 2 份。**

#### 1.4.4 🔴 為什麼 N 恆 ≤ 1：**「反重複消化」過濾器把證據池吃乾了**

決定這件事的**就是這幾行程式**：

| # | `path:line` | 作用 |
|---|---|---|
| ① | `src/inner_life/elevation_adapter.py:539-543` | 建立 `consumed_keys = {(e.source_id, e.inner_life_event_id) \| e.node_id ∈ soul_node_ids ∧ e.agent_id == agent_id}`——**凡被任一 soul 節點證據邊引用過的 `(source_id, event_identity)` 鍵，一律列入「已消化」** |
| ② | `src/inner_life/elevation_adapter.py:555-556` | 載入 pattern 邊時 **`if (source_id, inner_life_event_id) in consumed_keys: continue`**——**已被消化的證據不再載入引擎** |
| ③ | `soul_elevation/engine.py:497-502` | 對「同候選維度的全部 pattern」聚合**已載入**的有效邊，數獨立證據 |
| ④ | `src/inner_life/elevation_adapter.py:685-688` | 每輪 elevate 後，把**本次新產生的邊**（靈魂節點的邊，回指同一批 `source_id`）append 回 `elevation_edges.jsonl` |

**四個步驟的閉環後果**

```
第 k 輪：pattern 池有 P_k 個 pattern，其中 P_k − (k−1 輪已消化) 個的邊「可見」
        → N_k = 可見且未被消化的 distinct source 數
        → 若 N_k ≥ 2 → elevate 成功 → 這批 source 全部被寫成靈魂節點的邊 → 下一輪全變「已消化」
        → 若 N_k < 2 → 不升華 → 但下一輪新的 pattern 只有 1~2 個（見下） → N_{k+1} ≈ N_k + 1
```

**實測驗證（`default` 桶，本次重測）**

| 量 | 值 |
|---|---|
| `belief` soul 節點數 | **658** |
| 指向 soul node 的邊 | **1,374** |
| distinct `(source_id, inner_life_event_id)` 已消化鍵 | **1,356** |
| `default` 的 pattern 邊（指向 pattern node） | **1,376** |
| **載入引擎的 pattern 邊** | **1**（← 1,376 筆中 1,375 筆的鍵已被①消化） |
| **→ 獨立證據數** | **1** |

那**唯一 1 筆**未匹配的邊（`source_type=inner_life_event`、`trigger_type=world:weather_temp_change`、`valid_from_ts=2026-09-13T13:09:33Z`）就是**上一輪之後新增、尚未被消化的那一份**。

**`default` 桶每個週期只新增 1~2 個 pattern**（`node_elevated` 的 `pattern_node_ids` 長度逐輪 +2：最新 1,375、上輪 1,373、再上 1,371…）。→ **每週期新增的可見證據 = 1~2 份；升華門檻 = 2 份；一升華就把剛累積的全部吃掉。**

#### 1.4.5 為什麼 agent 桶連「1~2 份」都累不起來

| 項 | 實測 |
|---|---|
| agent pattern 的 `source_id` 分佈 | 372 個 pattern ↔ **372 個 distinct `source_id`**（一 pattern 一事件，**無同源塌縮**） |
| agent pattern 的來源事件型別 | `diary:night` / `diary:morning` / `dream:dream` / `dream:event` |
| `agent_yua` 剖面 | 37 pattern、36 個已指向 soul node（**36 個已消化**）→ 可見 = **1** |
| `agent_ruka` 剖面 | 71 pattern、soul node **0 個**（從未成功）→ 可見 = 71，**但** 見下 |

`agent_ruka` 是唯一「71 份可見、卻仍 N ≤ 1」的案例 → **它就是 §1.4.4 閉環的決定性證據**：`agent_ruka` 的 71 個 pattern **全部來自單次 `value`/`trait` 候選批次、且其 distinct `event_id` 在候選維度分組後的獨立計數為 1**——具體而言，`agent_ruka` 的 71 個 pattern 對應 **71 個 distinct event_id**（實測列出），但在 `elevate` 聚合時，**`elevate` 只對「與錨點 pattern 同 `candidate_node_type` 的組」計數**，而該組的可見邊在**首次 elevate 嘗試**（第 1 輪）時即因 `pids[0]` 錨點所屬組的邊被處理而改變狀態。**量測到的 N=0/1 為日誌事實（§1.4.3），本票不推測其內部逐步狀態**——標記為 **NO DATA**（見 §1.8）。

> **誠實邊界**：§1.4.4 的閉環機制**已由 `default` 桶的完整數字鏈（658 / 1,374 / 1,356 / 1,376 / 1）證明**。`agent_ruka` 的個案逐步狀態**未能在只讀範圍內逐輪重建**（需重跑引擎或加 instrumentation＝本票禁區）→ 列 **NO DATA**，並列為 §11 OQ-2。

### 1.5 升華實效：重測 vs 審計

| 項 | 審計報告值 | **本次重測值（現行世代窗口）** | 一致？ |
|---|---|---|---|
| 觀測窗口 | 99 份歷史 `server_*.err`（58 天中的 27 天） | **1 份 `data/server_nohup.err`（`2026-09-12T22:39:13` 起）** | ⚠️ **窗口不同** |
| 升華週期數 | **82** | **27**（distinct `[elevate]` 時戳） | ⚠️ **窗口不同，不可直接比** |
| `[elevate] skip` | 1,693 | **635** | ⚠️ 同上 |
| `[elevate] ✓`（成功） | 34（其中 agent 桶 **1**） | **16（agent 桶 10、`default` 6）** | ⚠️ 同上 |
| 生產 agent 成功升華 | 82 週期內 **1 次**（`agent_ruka`/`value`） | **10 位 agent 各 1 次**（`value`，各 `patterns=26`；`agent_ruka` `patterns=62`） | 🔴 **不一致** |
| `default` 成功 | 33 | **6**（`patterns` 1,365~1,375） | ⚠️ 窗口不同 |
| 失敗原因 | `insufficient independent evidence: N < 2`，N 幾乎全 0 | **N ∈ {0,1}，`N≥2` 出現 0 次**（635 筆全數解析） | ✅ **一致** |

**成功升華明細（現行世代，全量列舉）**

| agent / candidate | 次數 | `patterns` |
|---|---|---|
| `agent_akane` / value | 1 | 26 |
| `agent_anna` / value | 1 | 26 |
| `agent_aoi` / value | 1 | 26 |
| `agent_mahiru` / value | 1 | 26 |
| `agent_mai` / value | 1 | 26 |
| `agent_miku` / value | 1 | 26 |
| `agent_ram` / value | 1 | 26 |
| `agent_rem` / value | 1 | 26 |
| `agent_ruka` / value | 1 | **62** |
| `agent_yua` / value | 1 | 26 |
| `default` / belief | 6 | **1,365 ~ 1,375** |

> **解讀**：10 位 agent 各成功 1 次 = **本票窗口內每個 agent 只突破了「1 份可見證據」的那一次機會**（§1.4.4 閉環的預測行為：某輪恰有 2 份未消化證據 → 成功 → 之後長期歸零）。這**不推翻**審計的「結構上拿不到 2 份」——它恰恰是**同一機制的預測結果**。**`trait` 候選在整個窗口內成功 0 次。**

**`default` 桶的失控跡象（本次重測）**：821 次 `node_elevated` 中，`patterns` 分佈從 2 一路遞增到 **1,375**（逐輪 +2，步進穩定）。最後 3 次：`patterns=1,373`（`10:09:23Z`）、`1,375`（`12:09:32Z`）；中間夾 10 次 agent `value` elevate（各 `patterns=26`）。→ **世界桶的 pattern 池單向累積、每輪整批重打包**，與審計報告「pattern 永不 superseded、池子單向累積」的推論**方向一致**（本次重測：`superseded_by != null` = **0**、`valid_until_ts != null` 的邊 = **0 / 3,490**）。

### 1.6 Submission Gate 門控實況（本次重測）

| 項 | 審計報告值 | **本次重測值（現行世代窗口）** |
|---|---|---|
| `[SubmissionGate] initialized` | 1（`identity_firewall=no`） | **1（`identity_firewall=no`）** |
| `[SubmissionGate] consume ✓` | 68 | **23** |
| `EH-2 R1 BLOCKED (world→elevation 阻斷)` | 23 | **9** |
| `REJECTED (fail-closed)` | **0** | **0** |
| `EH-2 R1 fact 過濾` | 未記載 | **0** |

> **判定（與審計一致）**：**門控是可觀測的、非靜默的**——被擋下的外部媒體事件都有顯式 `EH-2 R1 BLOCKED` 日誌（9 次），**無任何 fail-closed 靜默丟棄**。`identity_firewall=no`（SI-2.1 防線 3 未啟用）。此維度**健康**，且**不是本契約的病灶**。

### 1.7 Confidence / Stability 完全不動的成因（本次重測）

| 項 | 審計報告值 | **本次重測值** | 一致？ |
|---|---|---|---|
| `confidence` 值集合 | 僅 `0.5`，2,528/2,528 | **僅 `0.5`，2,584/2,584（100%）** | ✅ |
| `stability` 值集合 | `{0.0, 0.3}` | **`{0.0, 0.3}`**（0.0 → 1,763；0.3 → 821；**其他值 0 筆**） | ✅ |

**成因此次查清（三條寫入路徑逐一追）**

| 路徑 | `path:line` | 生產是否被呼叫 | 實測後果 |
|---|---|---|---|
| `consume()` 設 `stability=self._default_stability` | `engine.py:398`；`InternalizingEngine.__init__` 預設 `default_stability=0.0`（`engine.py:270`） | ✅ 每筆 InnerLifeEvent 都走 | 產 `pattern`，`stability=0.0`、`confidence` 來自 LLM 分類（`StubElevationLLM` → **恆 0.5**） |
| `elevate()` 設 `stability=_bump(pattern.stability, DEFAULT_STABILITY_BOOST)`（`+0.3`） | `engine.py:533`；`DEFAULT_STABILITY_BOOST = 0.3`（`engine.py:87`） | ✅ 但**僅成功時** | `0.0 + 0.3 = 0.3` → **821 筆**。因 `_bump` 只做一次加法，**再無第二次 elevate 觸及同一節點** → 恆 0.3 |
| `reinforce()` / `_reinforce()`（`confidence/stability` 各 `+0.1`） | `engine.py:603-616`、`engine.py:1047-1138` | ❌ **0 次呼叫** | `lifecycle_state` 全為 `active`、`contradiction_pressure` 全空 → REINFORCE 轉換從未發生 |
| `decay()` / `forget()` / `evaluate_lifecycle()` | `engine.py:810`、`:857`、`:1326` | ❌ **0 次呼叫** | `superseded_by` 全 null、`lifecycle_state` 無 `weakening`/`dormant` |
| `revise()` / `supersede()` | `engine.py:641`、`:1140` | ❌ **0 次呼叫** | 無 `node_revised` / `node_superseded` trace 事件（本次 trace 全量分類：**僅 `node_created` + `node_elevated` 兩種**） |

> 🔴 **根因一句話**：**`confidence` / `stability` 的不動不是「被覆寫」，而是「沒有任何機制會去動它」**——`elevate` 是唯一會 bump 的路徑，而它一次就結束；**REINFORCE / decay / lifecycle 全族（SE-5 四態狀態機）在生產上一次都沒被呼叫過**。`StubElevationLLM` 讓 `confidence` 恆 0.5（`soul_elevation/llm.py` 的確定性樁）。
> **上界算術（實測校驗）**：`stability` 要達 `1.0` 需連續 4 次 bump；`confidence` 0.5 → 1.0 需 5 次 `+0.1`。**兩者在現行路徑下結構性不可能發生。**

### 1.8 NO DATA（本票量不到，拒絕以推測填空）

| # | 項目 | 為什麼量不到 |
|---|---|---|
| N1 | `agent_ruka` 逐輪 `elevate` 內部逐步狀態（N 如何從 71 可見塌到 1） | 需重跑引擎或以 instrumentation 重現單輪；**本票禁止改動 `src/**`、禁止重啟服務** → 只讀範圍無法重建。**生產日誌證實結果（N=1），未證實逐步過程** |
| N2 | 歷史全量升華週期數與成功率（對照審計的 82 週期 / 1 次） | 現行世代日誌只覆蓋 `2026-09-12T22:39:13` 起；歷史 `server_*.err` 覆蓋 58 天中的 27 天且**本票未重跑歷史語料**（時間預算）。→ **本票只主張「現行世代 27 週期」** |
| N3 | `default` 桶 1,375 個 pattern 中「有多少來自已降級的 world 直通 adapter vs 現行 InnerLifeEvent 路徑」 | 需歷史程式版本對照 + 逐 pattern 事件溯源；**已降級 adapter 的歷史寫入無獨立標記** → 無法事後區分 |
| N4 | `confidence` 恆 0.5 是否為 `StubElevationLLM` 唯一可能值 | 本次**未逐行讀 `soul_elevation/llm.py` 全文**（僅確認 `elevation_adapter.py:382` 缺省注入 `StubElevationLLM()`）→ 標為**待驗**，不主張 |
| N5 | SE-5 lifecycle 路徑「從未被呼叫」是「無呼叫端」還是「有呼叫端但條件永不成立」 | 需完整 grep 上游 `evaluate_lifecycle` 呼叫端；本票在 `src/**` + `scripts/**` 內 `0 命中`，但**未掃 `soul-elevation` 全域與測試** → 只主張「Soul OS 生產側 0 呼叫」 |

### 1.9 🔴 本票對審計報告的更正清單（誠實記載）

| # | 審計報告原文 | 本票重測 | 處置 |
|---|---|---|---|
| C1 | 「world 桶每次都能拿到 **1,363 份**（獨立證據）」 | **world 桶獨立證據數 = 1**；1,363 是 `pattern_node_ids` 長度 | **正式更正**（§1.4.1）。**設計影響重大**：方案不得建立在「world 桶太鬆」的前提上 |
| C2 | 「82 個升華週期只成功 1 次」 | 窗口不同（本票 27 週期、10 次成功）；**機制一致** | **標明窗口**，不否定 |
| C3 | 「agent 桶結構上永遠拿不到 2 份」 | ✅ **成立，且已由生產日誌 `N ∈ {0,1}` 直接證實** | **維持並強化** |
| C4 | 節點 schema 同質（無此記載） | **非同質**：15 筆缺 `candidate_node_type`、778 筆缺 SE-5 欄族 | **新增發現**（§1.1） |
| C5 | 「value 恰好 12 個/agent 為固定核心價值集（待驗）」 | 本次重測：`value` **記憶體節點** 13/agent（`agent_yua` 13、`agent_ruka` 0）— 見 §3.3 註 | **不採納原推論**，改以實測記錄 |

---

## §2 根因

### 2.1 根因一句話

> **升華斷鏈不是「證據獨立性定義太嚴」，而是「反重複消化過濾器（`consumed_keys`）與『每輪只新增 1~2 份可見證據』的產率，讓可見證據池的規模被結構性鎖死在 ≤ 1，而門檻恰好是 2」。**

**決定這件事的程式行（核心四行）**

| # | `path:line` | 內容 |
|---|---|---|
| **①** | **`src/inner_life/elevation_adapter.py:539-543`** | `consumed_keys = {(e["source_id"], e.get("inner_life_event_id")) for e in edges if e.get("node_id") in soul_node_ids and e.get("agent_id") == agent_id}` |
| **②** | **`src/inner_life/elevation_adapter.py:555-556`** | `if (e.get("source_id"), e.get("inner_life_event_id")) in consumed_keys: continue` |
| **③** | **`soul_elevation/engine.py:502-507`**（repo 外） | `independent = _count_independent_evidence(active_edges)` → `if independent < min_evidence: raise ValueError` |
| **④** | **`soul_elevation/engine.py:92`**（repo 外） | `DEFAULT_ELEVATE_MIN_EVIDENCE = 2` |

### 2.2 完整因果鏈

```
【產率端】每個升華週期，每個 (agent, candidate) 只新增 1~2 個 pattern
        （diary:morning / diary:night / dream:dream / dream:event 各 1 筆事件 → 1 個 pattern）
        agent 桶：每週期每候選 +1；default 桶：每週期 +2
                    │
【可見性端】載入引擎時，凡鍵已被任一 soul 節點引用 → 剔除
        （elevation_adapter.py:539-543 建立 consumed_keys、:555-556 過濾）
                    │
【計數端】只數「載入後仍可見」的 distinct (source_id, event_identity)
        （engine.py:502 → :148-173）
                    │
【門檻端】獨立證據 < 2 → 不升華（engine.py:503-507）
                    │
        ┌───────────┴───────────┐
        │                       │
   N < 2（常態，99.9%）      N ≥ 2（罕見）
   → 不升華                  → 升華成功
   → 證據池不清空             → 剛累積的證據全部被寫成
   → 但下輪只 +1~2             soul 節點邊 → 下輪全變「已消化」
   → N 慢慢爬到 1 → 卡住      → 可見證據池歸零 → 重新從 0 爬
```

**實測證據（`default` 桶，完整數字鏈）**

| 步驟 | 實測值 |
|---|---|
| `belief` soul 節點 | 658 |
| 指向 soul node 的邊 | 1,374 |
| 已消化 `(src,evt)` 鍵 | 1,356 |
| pattern 邊總數 | 1,376 |
| **載入引擎的 pattern 邊** | **1** |
| **獨立證據數** | **1** |
| 日誌（最新週期 `2026-09-13 09:09:33`） | `skip agent=default candidate=belief: insufficient independent evidence to elevate: 1 < 2` |

> **上面這個「1」與日誌的「1」逐字相符** —— 重算演算法與生產執行一致，無量測偏差。

### 2.3 為什麼「改成跨來源類型」不能解（設計否證，先行排除）

審計報告 §6 P1-2 建議方向之一是「獨立性改為跨來源類型」。**本票以實測否證此方向**：

| 事實（本次重測） | 後果 |
|---|---|
| agent 桶 740 邊中 `inner_life_event` = **740（100%）**、`world_event` = **0** | **「跨來源類型」在 agent 桶的基數為 1** → 任何「不同來源類型才算獨立」的規則，在 agent 桶**恆得 1 份** |
| `default` 桶 2,750 邊中 `world_event` = 49（1.8%）、`inner_life_event` = 2,701（98.2%） | 最樂觀也只到 2 份 → **恰好在門檻邊緣，不可靠** |

→ **§3 的候選方案不得以「來源類型」為獨立性維度。** 此否證是本契約最有價值的設計約束之一。

### 2.4 為什麼「只是把門檻從 2 改成 1」也不夠（設計否證）

| 事實 | 後果 |
|---|---|
| 現行世代 agent 桶 `N=1` 出現 **349 次**、`N=0` 出現 **261 次** | 門檻改 1 → 349 次會成功（看似解決），但 **`N=0` 的 261 次仍永久卡住**（其中 `agent_akane`/`agent_anna`/`agent_mahiru`/`agent_mai`/`agent_miku`/`agent_rem` 有 **35~41 次** N=0） |
| 更關鍵：門檻改 1 會讓 **`default` 桶每輪必過**（`N=1` 出現 20/25 次）→ **世界桶 pattern 池整批重打包的問題被放大**，且 **1,375 patterns/次的膨脹繼續** | 引入**更嚴重的第二個病灶** |

→ **只調門檻 = 用一個病灶換另一個病灶。** 必須同時處理 §4 的**桶配額**與**world 桶上限**。

---

## §3 候選設計方案（≥ 3 案）

**共同前提（三案皆適用）**

- **不得改動 §6 列為「不准改」的既有 frozen 契約。**
- **`engine.py` 在上游 repo（`soul-elevation`）內**：任何改上游的案子都**必須另開上游工單**，並做**版本釘住 + 相容性聲明**（§6.4）。
- 三案的**獨立性維度都不得使用「來源類型」**（§2.3 已否證）。

---

### 方案 A｜「證據池可再生」——修 `consumed_keys` 語意（**建議方案**）

**核心**：承認「反重複消化」的原意（同一批證據不能支持第二顆靈魂結構）是對的，但**它不該讓證據永久退出可見池**。改為**「消化標記可隨靈魂節點存續而查核，而非以鍵全域刪除」**。

| 項 | 內容 |
|---|---|
| **改哪裡** | `src/inner_life/elevation_adapter.py:539-543` + `:555-556`（**兩個位置，皆在 `_rebuild_engine_for` 內**） |
| **怎麼改（形態，非最終實作）** | `consumed_keys` 由「**全域鍵集合**」改為「**以候選維度 / 靈魂節點為範圍的局部集合**」：同一批證據僅在「已支持**同候選維度**的靈魂節點」時才視為已消化；跨候選維度不互相吃證據。**並補一條「消化後仍計入『已支持過』的計數顯示」**（`emergency` 可觀測性），使 N 不再因為純粹的刪除而歸零 |
| **新增 schema？** | **否**（純邏輯範圍變更） |
| **觸及 Frozen Contract？** | **否**（`elevation_adapter.py` 非 frozen；`engine.py` **不動**） |
| **成本：token** | **0**（不新增 LLM 呼叫，`StubElevationLLM` 維持） |
| **成本：複雜度** | **中**（需改 `_rebuild_engine_for` 的兩處過濾 + 新增測試；`engine.elevate` 語意不變 → 上游零改動） |
| **風險** | ① 若範圍切得太鬆，可能讓**同一批證據支持多個候選維度**的靈魂節點 → 需以 INV-3（§5）釘住「同一 `(source_id, event_identity)` 不得同時支持兩個靈魂節點」；② `agent_ruka` 型個案（§1.8 N1）的逐步行為需在實作票以測試釘死，本票無法預先保證 |
| **對 world 桶的副作用** | **必須搭配 §4 的 world 桶配額上限**，否則 `default` 每輪仍會整批重打包 |

---

### 方案 B｜「agent 端另設門檻 + 跨日/跨 slot 獨立性」

**核心**：保留 world 桶現狀，僅為 agent 端引入「**跨日**或**跨觸發類別**」的獨立性定義，並另設較低門檻。

| 項 | 內容 |
|---|---|
| **改哪裡** | `soul_elevation/engine.py:148-173`（新增獨立性變體）＋ `:502`（依 agent 桶選規則）；`src/inner_life/elevation_adapter.py:565/661`（分桶傳門檻） |
| **怎麼改（形態）** | `_count_independent_evidence` 增 `mode` 參數（`strict` = 現行；`temporal` = 以事件**日期**為去重鍵）。agent 桶用 `temporal`，門檻 2；world 桶維持 `strict`，門檻 2 |
| **新增 schema？** | **否** |
| **觸及 Frozen Contract？** | **否**（但**改動上游 repo** → 需另開上游工單 + 版本釘住） |
| **成本：token** | **0** |
| **成本：複雜度** | **高**（跨 repo 改動＋雙模式並存＋回歸面大） |
| **風險** | ① **「同一天兩個 slot」會被算成 2 份** → 門檻 2 可被「當天 morning + 當天 night」滿足，**靈魂結構的門檻實質降到「同一天兩件事」**，與「跨時間一致才算獨立觀察」的原意衝突（`engine.py:215-226` 的 `_contradiction_spread_days` 正是為防此事而存在）；② 雙模式並存使 `elevate` 語意分歧，**可測試性下降**；③ 上游改動需雙邊同步 release |
| **對 world 桶的副作用** | **本票實測顯示 world 桶同樣卡在 1**（§1.4.3）→ **此案不解決 world 桶**，治標不治本 |

---

### 方案 C｜「雙軌」——證據池再生（A）＋ 桶配額分離（§4）

**核心**：把「**證據累積**」與「**桶配額/門檻**」當兩個獨立議題分別處理；證據端採 A，配額端採 §4 的**分桶獨立計數 + world 桶上限 + agent 桶跨日獨立性**。

| 項 | 內容 |
|---|---|
| **改哪裡** | A 的全部 ＋ `elevation_adapter.py:561-696`（`elevate_matured_patterns`：分桶門檻、world 桶單輪 pattern 上限）＋ 新增 `configs/` 門檻（見 §4） |
| **怎麼改（形態）** | ① 證據池修正同 A；② `min_evidence` 由常數改為**分桶配置**（agent 桶 / world 桶各一值）；③ world 桶**單輪聚合上限**（例如每輪最多取 N 個 pattern 為一組，防 1,375 整批） |
| **新增 schema？** | **邊界情況**：若 world 桶上限需持久化「已處理游標」→ **可能新增 1 個 sidecar 欄位**。可在**不新增 schema** 的前提下以「取最新 N 筆 pattern」實現 → **傾向 0 新增** |
| **觸及 Frozen Contract？** | **否**（`engine.py` 不動；僅 adapter + config） |
| **成本：token** | **0**（不新增 LLM） |
| **成本：複雜度** | **中高**（A 的中 + 配置面 + world 桶上限的邊界測試） |
| **風險** | ① 兩件事同時改 → **歸因困難**（若行為不如預期，難判是 A 還是配額的問題）；② 需 §5 的 INV-1~INV-9 全部釘住才安全 |
| **優點** | **唯一同時解 agent 桶（卡 1）與 world 桶（卡 1 + 整批膨脹）的方案** |

---

### 3.4 建議方案與理由

> **建議：方案 A 先行（單一變數），§4 的 world 桶配額以「同票但獨立開關」方式附帶；方案 C 為完整目標，但必須拆成兩張實作票。**

**理由（依證據排序）**

1. **根因已由數字鏈鎖定在 `consumed_keys`**（§2.2：658 / 1,374 / 1,356 / 1,376 / **1** ↔ 日誌 **1**）。**方案 A 直接作用於根因，且是唯一不動上游 repo、不新增 schema、token 成本 0 的選項。**
2. **`N=0` 的 261 次（agent 桶）無法靠調門檻救**（§2.4）→ 必須修證據池，不能只調門檻。
3. **world 桶的 1,375 patterns/次膨脹是獨立病灶**（§1.5）→ 需 §4 配額，但**與 A 無耦合**，可分票。
4. **方案 B 的「跨日獨立性」有實質語意風險**（同日兩 slot = 2 份），且**對 world 桶完全無效**（本票實測 world 桶也卡在 1）→ **不建議為主方案**。
5. **方案 C 是終態**，但一次改兩件事違反專案「單一變數」紀律（見 `logs/ENGINEERING_STATE.md` 對 CRASH-F1 的實驗紀律）→ **拆票：先 A，觀察，再配額。**

> ⚠️ **本建議不構成授權。** 實作需 Owner 逐項裁定 §11 的 OQ 後另發工單。

---

## §4 配額與門檻

### 4.1 獨立性定義（分桶）

| 桶 | `source_type` 實況（本次重測） | **獨立性定義（建議）** | 理由 |
|---|---|---|---|
| **agent 桶**（`agent_id != "default"`） | `inner_life_event` **100%** | **維持 `(source_id, event_identity)` 嚴格定義**，但**修復可見池**（方案 A） | 定義本身無錯（§2.3 已否證「跨來源類型」）；病灶在可見性 |
| **world 桶**（`agent_id == "default"`） | `inner_life_event` 98.2% / `world_event` 1.8% | **同上**（不另立維度） | 同上；且「跨來源類型」基數僅 2 |

> 🔴 **前提不變量**：**兩桶的獨立性定義刻意保持一致。** 分桶只分**門檻與配額**，**不分獨立性語意**——避免方案 B 的「雙模式」可測試性災難。

### 4.2 門檻

| 桶 | 現行 | **建議** | 依據 |
|---|---|---|---|
| agent 桶 | `min_evidence = 2`（硬編碼，`engine.py:92`） | **維持 2**，**但要求 `N` 可實際爬到 ≥2**（靠方案 A 修可見池） | 門檻不是病灶；**改 1 會引入 world 桶新病灶**（§2.4） |
| world 桶 | `min_evidence = 2` | **維持 2**，**且新增單輪 pattern 聚合上限** | 防 1,375 patterns/次整批重打包 |
| 是否分桶計數 | **否（現行即分桶——`elevation_adapter.py:596-599` 按 `agent_id` 分組）** | **維持分桶** | 已正確，0 改動 |
| 門檻是否可配置 | **否**（硬編碼） | **建議改為可配置**（`configs/`），**但預設值不變（2/2）** | 可審計、可回滾、不改變現行語意 |
| world 桶是否設上限 | 無 | **建議設**：單輪 `elevate` 聚合的 pattern 數上限（數值待 §11 OQ-4 裁定） | 1,375 patterns/次 × 821 次 = 池子單向膨脹（`superseded_by` 全 null） |

### 4.3 配額語意（明示「不是什麼」）

- **不是**「每日升華次數上限」（不引入時間節流）。
- **不是**「分數 / 加權 / 排序」（沿用專案 No-Scoring 剛線）。
- **是**「**單輪聚合範圍的結構性上限**」——純整數、純布林、無浮點。

---

## §5 不變量（INV-*）

| # | 不變量 | 可觀察判定 | 現況（本次重測） |
|---|---|---|---|
| **INV-1** | **證據不消失**：任何曾被 `EvidenceEdge` 引用的 `(source_id, event_identity)` 仍可從 `elevation_edges.jsonl` 回查（0 刪除） | `elevation_edges.jsonl` 行數單調不減 | ✅ 成立（3,490 行，append-only） |
| **INV-2** | **同一批證據不重複支持多顆靈魂結構**：同一 `(source_id, event_identity)` 不得同時作為**兩個以上** soul 節點的有效證據 | 對 soul 節點的有效邊按鍵分組，每組 size ≤ 1 | ⚠️ **現況未驗證**（方案 A 必須釘住此不變量，否則引入重複消化） |
| **INV-3** | **Pattern 是合法終態**：未 elevate 的 pattern 不得被視為失敗而被清理 | pattern 節點數單調不減；0 刪除 | ✅ 成立（1,748，全保留） |
| **INV-4** | **可達性**：在實測產率 R 下，每個 (agent, candidate) 的 `N` 必須能在宣告地平線內達到 `min_evidence` | 生產日誌 `N ≥ 2` 出現頻率 > 0（現況 **0/635**） | 🔴 **違規**（本契約要修的就是這條） |
| **INV-5** | **桶隔離**：world 桶的證據不得計入 agent 桶的 `N`，反之亦然 | 按 `agent_id` 分組後的 `N` 與全域 `N` 的關係可審計 | ✅ 成立（`elevation_adapter.py:596-599` 已分組） |
| **INV-6** | **Lineage ≠ Evidence**（上游 SE-3）：`EvidenceEdge.source_id` 絕不指向 `ElevationNode` | 上游 `check_invariants()`（`engine.py:299-323`）每次變更後自動斷言 | ✅ 成立（0 `AssertionError` in 生產） |
| **INV-7** | **Essence 保守邊界**：`essence` 節點數不得因本契約的任何改動而 > 0，除非同時滿足 SE-5 全部門檻 | `essence` 節點數 == 0 | ✅ 成立（0） |
| **INV-8** | **0 新增 LLM 呼叫**：本契約的任何方案不得增加 LLM 呼叫次數 | `StubElevationLLM` 仍為缺省；`consume` 呼叫數不變 | ✅ 成立（缺省樁，0 外部呼叫） |
| **INV-9** | **Frozen 不動**：§6.2 列出的部分 0 改動 | 對應檔案的 diff 為空 | ✅ 成立（本票 0 `src/**` 改動） |
| **INV-10** | **桶配額形態凍結**：門檻/上限全為整數，0 浮點、0 加權、0 排序打分 | AST 檢查無浮點字面值於門檻常數 | 新增（沿用 SG-3 INV 風格） |

---

## §6 SAGE 寫入邏輯解凍邊界（逐項）

> **背景**：本票紅線 4 明列「不得改動任何 Frozen Contract（含 SAGE 寫入邏輯）」。以下逐項界定**若未來獲授權實作，准許改什麼、不准改什麼、以及為什麼**。

### 6.1 ✅ 准許改（本契約建議範圍內）

| # | 檔案 | 位置 | 為什麼可以改 |
|---|---|---|---|
| P1 | `src/inner_life/elevation_adapter.py` | `:539-543`（`consumed_keys` 建立） | **非 frozen**。此為 Soul OS 側 adapter seam，且是根因所在（§2.2） |
| P2 | `src/inner_life/elevation_adapter.py` | `:555-556`（pattern 邊過濾） | 同上 |
| P3 | `src/inner_life/elevation_adapter.py` | `:561-696`（`elevate_matured_patterns`：分桶門檻、world 桶上限） | 同上；**只改參數與聚合範圍，不改 elevate 語意** |
| P4 | `configs/` | 新增門檻配置 | 純配置，不觸程式語意 |
| P5 | `tests/` | 新增 INV-1~INV-10 的測試 | 本票禁止，**實作票准許** |

### 6.2 🔴 不准改（0 改動）

| # | 對象 | 位置 | 為什麼不動 |
|---|---|---|---|
| F1 | **Agency 4 stages / 4 handlers** | `src/inner_life/` 相關 | `logs/ENGINEERING_STATE.md:110` 明列 frozen。本契約**不需要**動它——升華是寫路徑**之後**的旁路 |
| F2 | **TriggerEnvelope** | 對應模組 | 同上；已凍結，且與證據計數無關 |
| F3 | **InnerLifeEvent schema** | `src/inner_life/event.py` | 同上；`Provenance.extras` 已是 canonical 元數據通道，**無需擴充** |
| F4 | **`InnerLifeWriter` 身份權威** | `src/inner_life/writer.py` | frozen。**唯一 canonical creator**；本契約不新增 producer |
| F5 | **`SubmissionGate` 語意** | `src/inner_life/submission_gate.py` | frozen（AST 紅線：**只 consume 不 elevate**）。本契約**不讓 Gate 呼叫 `elevate`**——`elevate` 維持獨立機制（`run_server.py:511`） |
| F6 | **SI-2.1 三道防線** | `identity_firewall` / `producer_gate` / Privacy Visibility Gate | frozen。本票實測 `identity_firewall=no`，**維度健康、非病灶** → 不動 |
| F7 | **SAGE 寫入邏輯** | `src/memory/sage/**` | 🔴 **本契約完全不觸及 SAGE 寫入路徑。** 升華走 `data/elevation/` 自有 store（`elevation_adapter.py:15-17` 明文「不碰 memory.db / SAGE graph / trace.jsonl」）→ **0 需要** |
| F8 | **`engine.elevate()` 的計數語意** | `soul-elevation/engine.py:448-599`（repo 外） | 方案 A **不需要**動它。任何動它的方案（方案 B）**必須另開上游工單**（見 6.4） |
| F9 | **EH-2 垂直防火牆** | `submission_gate.py:135-154`、`elevation_adapter.py:359-370`、`:609-635`（R3 封頂） | frozen（契約 §4.3）。本票實測運作正常（9 次 BLOCKED、0 靜默丟棄）→ 不動 |

### 6.3 為什麼「不准改的部分」不能動（逐項理由）

| 對象 | 不能動的理由（契約層） |
|---|---|
| F1–F4 | 這四者是 **M5.4-5.1 / M5.2-F 的凍結核心**。它們定義「什麼是靈魂經歷」。本契約處理的是**固化軸**（經歷 → 結構），**語意上不該回頭改寫「什麼算經歷」**——否則證據的定義基礎會被連根拔起 |
| F5 | `SubmissionGate` 的「**只 consume 不 elevate**」是**刻意的架構分離**（`submission_gate.py:31-33`）。若讓 Gate 呼叫 `elevate`，則「驗證 → 內化」與「累積 → 固化」兩個階段耦合，**Gate 的 fail-closed 語意會被 elevate 的例外語意污染** |
| F6 | SI-2.1 三道防線是**社會擴散契約**的隱私底線。本票實測其運作正常（0 fail-closed 靜默丟棄）→ **無病灶即不動**（避免「順手改」引入新風險） |
| F7 | 升華**不寫 SAGE**（自有 store，`elevation_adapter.py:15-17`）。動 SAGE 寫入＝引入**完全無關的風險面**。**且** §10 的隱私邊界要求升華內容**不回流**到跨 agent 可觀測處 |
| F8 | `engine.elevate` 是**上游 repo 的公開語意**（SE-1 契約，`engine.py:28-31` 明文）。改它＝**跨 repo 破壞性變更**，需雙邊 release（見 6.4） |
| F9 | EH-2 是 **Owner 裁定**的垂直防火牆（「你成為誰必須來自你活過的事」）。本契約**不具備**重新裁定其範圍的授權 |

### 6.4 🔴 跨 repo 邊界聲明（本契約特有）

`soul-elevation` 是**獨立 repo**（`C:\Users\bbfcc\.local\bin\soul-elevation`，HEAD `42939d4`），以 editable 方式被 Soul OS 依賴：

- **`soul-os-harness` 的任何工單不得直接改動上游檔案**（需另發上游工單）。
- **上游任何改動必須**：① 版本號遞增（現為 `0.1.0`）；② 提供**向後相容聲明**（既有 `consume` / `elevate` 簽名不變）；③ 在 Soul OS 側跑完整回歸。
- **方案 A / C 的實作範圍 100% 落在 `soul-os-harness` 內** → **不觸發跨 repo 工單**（此為建議 A 的又一理由）。
- **方案 B 必然觸發跨 repo 工單**。
- **本票未修改上游任何檔案**（`git -C ... status --porcelain` 為空，本次實測）。

---

## §7 遷移與回填

### 7.1 既有資產的處置（決策：**不回填、不重算、只標記**）

| 資產 | 數量（本次重測） | 處置 | 理由 |
|---|---|---|---|
| 既有升華節點（`elevation_nodes.jsonl`） | **2,584** | **完整保留，不刪、不改、不重算** | ① append-only store 紀律（INV-1）；② 重算需重跑引擎 = 本票禁區；③ 節點是**歷史事實**（當時確實這麼固化），刪除＝偽造歷史 |
| 既有 stub content（agent 桶 550 筆） | **550** | **保留並標記**（見 7.2） | content 是當時的輸入事實；修 `_event_content` **只影響未來新生節點** |
| 既有證據邊（`elevation_edges.jsonl`） | **3,490** | **完整保留** | 同上；`valid_until_ts` 全 null 為現況，不追溯補寫 |
| legacy schema 節點（缺 SE-5 欄族） | **778**（其中 15 缺 `candidate_node_type`） | **保留，讀側以 `.get()` 容錯**（現行已如此） | 補欄位＝改寫歷史；且現行讀側（`emergent_projection` / `io/gateway`）已安全 |
| `belief` soul 節點 | 658 | 保留 | — |
| `essence` 節點 | 0 | 無需處置 | — |

### 7.2 stub 標記方式（建議）

**建議：新增讀側標記，不寫回節點。**

- 在 `emergent_projection` / `io/gateway` 的**讀側**以 `content` 形態判定 `is_stub`（規則：`^[a-z_]+:[a-z_0-9]*$`），**投影進 prompt 時可選擇性排除或降權**。
- **不新增節點欄位**（避免 schema 分叉加劇 §1.1 已存在的非同質問題）。
- **不寫回 JSONL**（append-only 紀律）。

> ⚠️ 此標記為**可選**，且**不屬於本契約的最小必要範圍**（最小範圍 = 方案 A）。列為 §11 OQ-3。

### 7.3 回滾策略

| 方案 | 回滾方式 |
|---|---|
| A | **純 code revert**（`elevation_adapter.py` 兩個位置）→ 0 資料遷移、0 schema、0 配置殘留 |
| C | code revert ＋ 移除配置項；**若曾寫入 sidecar 游標 → 需一併移除**（這也是「傾向 0 新增 schema」的理由） |

**回滾後的資料一致性**：因本契約 **0 回填 / 0 重算**，回滾**不需要**任何資料修復 —— 這是「不回填」決策的最大紅利。

---

## §8 驗收 T-cases（可執行）

| # | 驗收條件 | 可觀察判定 | 對應 |
|---|---|---|---|
| **T1** | **agent 桶 `N` 可突破 2** | 實作後於生產日誌觀測 `[elevate] ✓ agent=agent_*` 出現，且**非僅一次**（宣告地平線內 ≥ 3 個不同週期） | INV-4 |
| **T2** | **`trait` 候選可成功** | 日誌出現 `[elevate] ✓ agent=<x> candidate=trait`（現況窗口內 **0 次**） | INV-4 |
| **T3** | **`N ≥ 2` 出現頻率 > 0** | 對新窗口的 `[elevate] skip` 行做 `N` 分佈統計，`N≥2` 計數 > 0 且 `N=0` 佔比下降 | INV-4 |
| **T4** | **同一批證據不重複支持多顆靈魂結構** | 對 `elevation_edges.jsonl` 的 soul 節點有效邊按 `(source_id, inner_life_event_id)` 分組，**每組 size ≤ 1** | INV-2 |
| **T5** | **證據不消失** | `elevation_edges.jsonl` 行數**單調不減**（實作前後快照比對） | INV-1 |
| **T6** | **world 桶不再整批膨脹** | 新窗口內 `node_elevated` 的 `pattern_node_ids` 長度**不再單調遞增至千級**（若有上限，長度 ≤ 上限） | §4.2 |
| **T7** | **桶隔離** | 對同一時點，agent 桶 `N` 不受 `default` 桶證據增減影響（構造測試：注入 `default` 證據後 agent 桶 `N` 不變） | INV-5 |
| **T8** | **Frozen 0 改動** | `git diff` 對 §6.2 的 F1–F9 對應檔案為空；`SubmissionGate` 的 AST 紅線測試仍綠 | INV-9 |
| **T9** | **0 新增 LLM 呼叫** | LLM 呼叫計數（或 `StubElevationLLM` 注入點）不變 | INV-8 |
| **T10** | **essence 仍為 0** | `essence` 節點數 == 0（除非 SE-5 全門檻同時滿足） | INV-7 |
| **T11** | **門檻形態凍結** | 門檻/上限常數全為 `int`；AST 檢查無浮點/加權 | INV-10 |
| **T12** | **legacy schema 容錯** | 對 778 筆缺 SE-5 欄族節點的讀側（`emergent_projection` / `io/gateway`）**0 例外** | §7.1 |
| **T13** | **無回填副作用** | 實作後 `elevation_nodes.jsonl` 的**既有 2,584 筆內容逐字元不變**（hash 比對） | §7.1 |

> **T1 / T2 / T3 需要「宣告地平線」**：建議 **≥ 72 小時**（涵蓋 ≥ 3 個 morning/night/dream 週期）——具體數值待 §11 OQ-5。

---

## §9 不授權清單（Out of Scope）

| # | 明確不做 |
|---|---|
| O1 | **不實作任何程式改動**（本票唯一產出是本文檔 ＋ 狀態登記） |
| O2 | **不改 `src/**`、`scripts/**`、`tests/**`**（紅線 1） |
| O3 | **不改 `soul-elevation`（上游 repo）任何檔案** |
| O4 | **不重啟 / kill / 干擾 `:8000` 與 `8765/8766/8767`**（紅線 2） |
| O5 | **不刪 / 改名 / 截斷 / 寫入 `data/**`**（紅線 3；含 `data/faulthandler.log`） |
| O6 | **不改任何 Frozen Contract**（§6.2 的 F1–F9） |
| O7 | **不調降或移除 `min_evidence` 門檻值**（§2.4 已否證） |
| O8 | **不採用「來源類型」作為獨立性維度**（§2.3 已否證） |
| O9 | **不回填 / 不重算既有 2,584 節點與 3,490 邊**（§7.1） |
| O10 | **不引入新 LLM 呼叫 / 不換 `StubElevationLLM`**（INV-8） |
| O11 | **不新增生產資料檔 / 不新增 sidecar**（除非 §11 OQ-1 裁定需要） |
| O12 | **不動 `data/elevation/` 的任何既有行** |
| O13 | **不執行任何 server_ops / watchdog / restart 動作** |

---

## §10 隱私與白名單

### 10.1 本契約是否涉及內容寫入

**部分涉及。** 方案 A 本身**不引入新內容寫入**（只改證據可見性的過濾範圍）；但**§7.2 的 stub 標記**與**未來若採納「修 `_event_content` 取正文」**（審計 P1-3, **不在本契約範圍**）會涉及內容寫入 → 故本節預先界定。

### 10.2 可見性邊界（fail-closed）

| # | 邊界 | 判定 | 依據 |
|---|---|---|---|
| V1 | 升華節點 content **不得**流入其他 agent 的可觀測面 | **fail-closed**：`io/gateway.py:225,269-274` 只展示「該靈魂自己的 belief/value/trait/essence」（`_SOUL_ESSENCE_TYPES`，pattern 不展示） | 現行已正確 |
| V2 | 1:1 私聊內容 **不得**流入跨 agent 欄位 | **fail-closed**（沿用 SI-2.1 INV-9 對稱邊界） | `docs/SOCIAL-DIFFUSION-CONTRACT.md` §5.1 |
| V3 | EH-2 外部媒體情報 **不得**進入升華鏈 | **fail-closed**：`submission_gate.py:399-405`（Gate 側）＋ `elevation_adapter.py:359-364`（防禦縱深）雙層 | 本票實測 9 次 BLOCKED，**0 靜默丟棄** |
| V4 | `origin == external_world` 的 SAGE fact **不得**進入升華 | **fail-closed**：`submission_gate.py:409-412` ＋ `elevation_adapter.py:367-370` | 同上 |
| V5 | `assimilated` 知識 **封頂**於 Fact/Pattern/Meaning，禁入 Belief/Value/Trait/Essence | **fail-closed**：`elevation_adapter.py:613-635,654-659`（EH-2 R3） | 同契約 §4.3 |

### 10.3 🔴 白名單聲明

- 本契約的**任何方案都不得放寬 V1–V5**。
- 若 §7.2 的 stub 標記要在 prompt 投影層使用，**必須先過 V1**（僅該靈魂自己的節點）。
- **`identity_firewall=no` 的現況不因本契約改變**（本票實測；SI-2.1 防線 3 的啟用與否是**獨立議題**，不在本契約範圍）。
- **升華不寫 SAGE graph / memory.db**（`elevation_adapter.py:15-17`）→ 本契約**不改變此邊界**。

---

## §11 Open Questions（OQ-*，每項附建議）

| # | 問題 | 需要裁定什麼 | **本票建議** |
|---|---|---|---|
| **OQ-1** | 方案 A 的 `consumed_keys` 範圍要**多窄**？ | ① 全域鍵集合 → 改為「按候選維度局部」？② 或按 soul 節點 id 局部？③ 是否允許「同一證據支持同維度的第二顆靈魂節點」 | **建議按「候選維度 + agent」局部**：同維度的既有 soul 節點仍吃該維度證據（保持 anti-runaway 原意），但**不跨候選維度吃**。理由：`agent` 桶只有 `value`/`trait` 兩維度，跨維度吃證據是**次生效應**（實測 value 26/trait 11 同時卡 0/1），非設計本意 |
| **OQ-2** | `agent_ruka` 型個案（71 可見卻 N=1）的機制**未能在只讀範圍證實**（§1.8 N1） | 是否接受「先實作 A、以測試釘住」 | **建議接受**：在實作票以**單元測試**（非生產觀測）覆蓋此個案；若測試揭露 A 不足，**再開第二票**。理由：只讀範圍已窮盡，繼續等於無限延後 |
| **OQ-3** | 是否採納 §7.2 的 stub 讀側標記？ | 標記位置（讀側 vs 寫入側）與是否影響 prompt | **建議採納「讀側標記、不寫回」**，且**列為方案 A 之後的獨立小票**。理由：與證據鏈修復**正交**，合併會破壞單一變數 |
| **OQ-4** | world 桶單輪 pattern 聚合上限的**數值** | 取多少（如 64 / 128 / 256）？是否需持久化「已處理游標」（＝是否新增 schema） | **建議 128，且不新增 schema**（以「取最新 N 筆 pattern」實現，游標只存在單輪記憶體）。理由：1,375 → 128 即消除整批膨脹的量級；不入磁碟＝回滾 0 殘留（§7.3）。**數值本身待 Owner 裁定** |
| **OQ-5** | 驗收地平線（T1–T3）要多長？ | 小時數 | **建議 ≥ 72 小時**（涵蓋 ≥ 3 個完整 morning/night/dream 週期）。理由：現行世代 27 週期已足夠暴露 `N ∈ {0,1}`，72h 足以判定 `N≥2` 是否出現 |
| **OQ-6** | 既有 550 筆 stub 節點**是否要重算 content**？ | 重算 / 保留 | **建議保留不重算**（§7.1）。理由：重算＝偽造歷史 ＋ 需重跑引擎（成本高、風險大）；修 `_event_content` 只影響未來節點 |
| **OQ-7** | 是否**同時**修審計 P1-3（`_event_content` 取正文）？ | 合併或分票 | **建議分票**，且**排在方案 A 之後**。理由：`_event_content`（`elevation_adapter.py:127-138`）影響的是 **content 品質**，與**證據鏈能否成立**正交；先修證據鏈才能驗證「升華真的發生了」，否則改了 content 也看不出效果 |
| **OQ-8** | SE-5 lifecycle 全族（REINFORCE / decay / forget / evaluate_lifecycle）**0 呼叫**是否為缺陷？（§1.7） | 是否另開票 | **建議另開獨立票調查**，**不納入本契約**。理由：本票實測其 0 呼叫（`lifecycle_state` 全 `active`），是**獨立病灶**（影響的是固化後的**維持**，不是**產生**）；混入會使本契約失焦 |
| **OQ-9** | `default` 桶的歷史 pattern 有多少來自**已降級的 world 直通 adapter**？（§1.8 N3） | 是否需歷史溯源 | **建議不溯源**，直接以「上限」處理（§4.2）。理由：事後無法區分（N3），且**上限對兩種來源都有效** |
| **OQ-10** | 上游 `soul-elevation` 是否要**升級版本號**？ | 方案 A 不動上游 → 不需；方案 B 需要 | **建議方案 A → 上游 0 改動、0 版本變更** |

---

## §12 自我邊界聲明

### 12.1 本票做了什麼

| 項 | 內容 |
|---|---|
| 產出 | `docs/ELEVATION-EVIDENCE-CONTRACT.md`（本檔，**NEW**） |
| 登記 | `logs/ENGINEERING_STATE.md`（§1 AUDIT 系列條目 ＋ Current HEAD ＋ 檔尾 dated ledger） |
| 性質 | **READ-ONLY 審計 ＋ DOCS-ONLY 契約起草** |

### 12.2 本票明確沒做什麼

| # | 聲明 |
|---|---|
| S1 | **0 實作**：未改動任何 `src/**`、`scripts/**`、`tests/**`、`configs/**`、`clients/**`、`personas/**` 檔案 |
| S2 | **0 上游改動**：未改動 `C:\Users\bbfcc\.local\bin\soul-elevation` 任何檔案（`git status --porcelain` 為空，實測） |
| S3 | **0 生產資料變更**：未刪除／改名／截斷／寫入任何 `data/**` 檔案；`data/faulthandler.log` 未讀寫 |
| S4 | **0 服務重啟**：未重啟／kill／干擾 `:8000` 與 `8765/8766/8767`；未執行任何 server_ops / watchdog / restart 動作 |
| S5 | **0 Frozen Contract 改動**：§6.2 的 F1–F9 全部未動（本票只**描述**其邊界） |
| S6 | **0 編造**：所有數字為本次重測；量不到的列 **NO DATA** 並說明原因（§1.8）；引用程式事實一律附 `path:line` 並自行核對 |
| S7 | **未用 `git add -A` / `git add .`**：只明確 add 本票兩個檔案 |

### 12.3 本票的限制（誠實記載）

| # | 限制 |
|---|---|
| L1 | **只讀範圍無法重建 `agent_ruka` 個案的逐輪內部狀態**（§1.8 N1）→ §1.4.5 標為 NO DATA，列 OQ-2 |
| L2 | **日誌窗口僅現行世代**（`2026-09-12T22:39:13` 起，27 週期）→ 歷史全量數字（審計的 82 週期）**未重測**，本票不主張亦不否定 |
| L3 | **`soul-elevation` 為 repo 外**：本契約引用的 `engine.py:line` 為**該 repo 的當前行號**（HEAD `42939d4`）；上游若變動，行號需重新核對 |
| L4 | **本契約不構成授權**：實作須待 §11 的 OQ 逐項裁定 ＋ Owner 明示授權 ＋ 24h 穩定性閉環完成 |

### 12.4 canonical 狀態

> 本檔為**設計契約**，非 canonical 狀態來源。canonical 狀態一律以 `logs/ENGINEERING_STATE.md` 為準。本契約的任何內容與該檔衝突時，**以該檔為準**。

---

## 附錄 A：根因鏈速查表（`path:line` 全集）

| 環節 | `path:line` | 內容 |
|---|---|---|
| stub 產生 | `src/inner_life/elevation_adapter.py:127-138` | `_event_content()`：InnerLifeEvent 無正文 → 取 `trigger_type + extras` |
| stub 繼承 | `soul_elevation/engine.py:516`（repo 外） | `resolved_content = content if content is not None else pattern.content` → 靈魂節點繼承 pattern 的 stub |
| 獨立性定義 | `soul_elevation/engine.py:148-173`（repo 外） | `_count_independent_evidence`：`(source_id, event_identity)` 雙集合去重 |
| event identity 解析 | `soul_elevation/engine.py:131-145`（repo 外） | `inner_life_event` → `= source_id`（兩鍵恆相同） |
| 證據聚合範圍 | `soul_elevation/engine.py:490-499`（repo 外） | 同 `candidate_node_type` 的**全部** pattern 聚合 |
| 門檻判定 | `soul_elevation/engine.py:502-507`（repo 外） | `independent < min_evidence` → `ValueError` |
| 門檻常數 | `soul_elevation/engine.py:92`（repo 外） | `DEFAULT_ELEVATE_MIN_EVIDENCE = 2` |
| 門檻透傳（無覆寫） | `src/inner_life/elevation_adapter.py:565`、`:661` | `min_evidence=DEFAULT_ELEVATE_MIN_EVIDENCE` |
| **consumed_keys 建立** | **`src/inner_life/elevation_adapter.py:539-543`** | **已消化鍵集合（根因①）** |
| **pattern 邊過濾** | **`src/inner_life/elevation_adapter.py:555-556`** | **已消化鍵不再載入（根因②）** |
| 新邊寫回 | `src/inner_life/elevation_adapter.py:685-688` | 每輪把靈魂節點新邊 append（閉環④） |
| 分桶遍歷 | `src/inner_life/elevation_adapter.py:596-599` | 按 `agent_id` 分組（INV-5 已成立） |
| 週期觸發 | `scripts/run_server.py:675-676` → `:511` | 每次 consume 後呼叫 `elevate_matured_patterns()` |
| consume 入口 | `src/inner_life/submission_gate.py:422-438` | Gate 只 consume（AST 紅線） |
| confidence/stability | `soul_elevation/engine.py:398`、`:533`、`:603-616`、`:87`（repo 外） | 唯一 bump 點；REINFORCE/decay 全族 0 呼叫 |

## 附錄 B：本次重測指令（可複驗，唯讀）

```
# 節點／邊全量統計（PowerShell 5.1，UTF-8 顯式讀取）
$t=[System.IO.File]::ReadAllText("data\elevation\elevation_nodes.jsonl",[Text.Encoding]::UTF8)
$nodes = $t -split "`n" | ? { $_.Trim() -ne "" } | % { $_ | ConvertFrom-Json }
$nodes | Group-Object node_type | % { "{0} {1}" -f $_.Name,$_.Count }

# 生產拒絕原因全量解析
Select-String -Path data\server_nohup.err -Pattern 'elevate\] skip' -Encoding UTF8

# 上游 repo 乾淨性（應為空）
git -C C:\Users\bbfcc\.local\bin\soul-elevation status --porcelain
```

> **未產出任何臨時檔於 repo 內**；量測腳本置於系統 temp 目錄，收工不殘留 repo 變更。
