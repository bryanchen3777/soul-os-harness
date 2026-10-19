# C4-DESIGN — DORMANT / SUPERSEDED 回憶讀取設計契約（DOCS-ONLY）

- **工單**：C4-DESIGN（`INNER-LIFE-CONSTRUCTABILITY-AUDIT.md` C4 `IL-RECALL-1` 的設計前半；實作另票）
- **性質**：**只設計，0 code**。本票只新建本檔，不動 `src/`、`tests/`、`data/`，不寫測試。
- **前置**：SE-5 lifecycle 四態實作（`42939d4`，上游 `soul-elevation` repo）＋ SE-5 Step 1 讀側投影守門（`3ff2976`，`src/inner_life/emergent_projection.py`）＋ TG-1 排除條款（`docs/TG-1-GOAL-ENGINE-CONTRACT.md:143`）
- **日期**：2026-10-11

---

## 0. TL;DR

- **立場（範圍提案）**：DORMANT 回憶讀取**納入 v1**（只讀查詢，不恢復主動投影）；SUPERSEDED **只讀 lineage、不復活**。
- **介面**：新增只讀 `query_recall(agent_id, query, state_filter)`；回傳 `node + lineage_path + 狀態標`；**0 寫入、0 狀態遷移**。
- **不變量**：回憶結果永不混入 ACTIVE 投影（語氣隔離）；essence 豁免維持；單日噪聲拒絕沿用。
- **驗收**：合成資料測試清單（4 項），測試檔位見 §5（本票不寫測試）。
- **Frozen 觸碰**：§6 逐項列有／無——**全部無**。

---

## 1. 範圍提案

### 1.1 立場：DORMANT 回憶讀取納入 v1

**立場：納入。** v1 提供 DORMANT 節點的**顯式只讀回憶查詢**（on-demand recall），但**不恢復** DORMANT 對 `emergent_projection` 主動投影的參與（`NON_PROJECTED_LIFECYCLE_STATES` 維持不變）。

**理由**：

1. SE-5 四態齊備後，「我以前很在意」可查詢是 `ELEVATION-LIFECYCLE.md §2.2` 的明示設計要求（「狀態必須可查詢、可持久」）——讀側缺口不補，四態只有寫側。
2. 只讀查詢不觸碰狀態機：DORMANT → ACTIVE 的唯一通道仍是 REINFORCE（新支持證據），回憶查詢本身不產生證據、不遷移狀態，故不違反「證據不足什麼都不做」。
3. 與主動投影解耦：DORMANT 不再影響當前詮釋（`ELEVATION-LIFECYCLE.md §2.1` 語義）只約束**主動投影**；顯式回憶是被問才答，不污染當下人格，與 WEAKENING 語氣隔離先例同構。

### 1.2 TG-1 排除條款修訂申請

TG-1 `§3.2`（`docs/TG-1-GOAL-ENGINE-CONTRACT.md:143`）原文：「**明確排除**：**DORMANT 不入 v1**（TG-0 決策 2：證據不足不动，SE-5 默認鐵律；留 TG-2 評估）。」

**修訂申請語句（待 Owner 批）**：

> 申請將 TG-1 §3.2「DORMANT 不入 v1」修訂為「DORMANT 不入 v1 **主動投影**；DORMANT **只讀回憶查詢**納入 v1（本契約 §2–§4，0 寫入、0 狀態遷移）」。理由：回憶查詢不產生證據、不觸發 REINFORCE，不違反 TG-0 決策 2 的「證據不足不动」鐵律；SUPERSEDED 仍維持只讀 lineage、不復活。

### 1.3 SUPERSEDED：只讀 lineage，不復活

- SUPERSEDED 是**終態**（`ELEVATION-LIFECYCLE.md §2.1`）：回憶可讀其 `content + lineage_path`（回答「我為什麼改變了」），但**永不經回憶路徑復活**（無 SUPERSEDED → ACTIVE 邊；REINFORCE 對 superseded 拒絕，SE-5 實作語義）。
- **鐵律**：Forgetting = transition，不是 delete（`ELEVATION-LIFECYCLE.md §4.3`）——舊節點永久保留、凍結；回憶是讀凍結體，不是解凍。

---

## 2. 介面

### 2.1 Query 形狀

```text
query_recall(agent_id: str, query: str, state_filter: set[str]) -> list[RecallHit]
```

| 參數 | 語義 | 約束 |
|---|---|---|
| `agent_id` | 只查該靈魂自己的節點（沿用 `project_emergent` 死規則 1；`agent_id == "default"` 的 world node 永不回憶） | 必填，非空字串，否則回 `[]` |
| `query` | 回憶檢索字串（子字串匹配 `content`，deterministic；不做語義排序、不做 LLM） | 必填；空字串回 `[]` |
| `state_filter` | 態過濾：`{"dormant"}`、`{"superseded"}` 或兩者；**不接受 `active`／`weakening`**（那是主動投影的轄區） | 缺省＝兩者；含非法態一律回 `[]`（fail-closed） |

只查 `node_type in {belief, value, trait, essence}`（沿用 `PROJECTABLE_NODE_TYPES`；pattern 不回憶）。

### 2.2 回傳形狀

```text
RecallHit = { node_id, node_type, content, lifecycle_state, lineage_path, superseded_by }
```

- `node`：`node_id + node_type + content`（原文照錄，不加語氣前綴——語氣隔離在**呈現層**做，見 §3.1）。
- `lineage_path`：節點的既有 `lineage_path` 欄位照抄（SUPERSEDED 的「以前 A → 現在 B」追溯鏈；只讀，不展開遞迴查詢）。
- `狀態標`：`lifecycle_state`（`"dormant"`／`"superseded"`）＋ SUPERSEDED 另附 `superseded_by`（取代者 id，無則 null）。
- 排序：`created_ts asc + node_id asc`（沿用投影 deterministic 排序）。

### 2.3 只讀保證

1. **0 寫入**：不寫 `elevation_nodes.jsonl`、`elevation_edges`、不寫任何 trace/sidecar（連 `elevation_projection_trace.jsonl` 也不寫——回憶不是投影）。
2. **0 狀態遷移**：不呼叫 `REINFORCE / SUPERSEDE / evaluate_lifecycle`；不碰 `lifecycle_state`、`last_support_ts`、`contradiction_pressure`、`reconsideration_candidate`。
3. **失敗靜默**：檔不存在／壞行／壞時間戳 → 跳過回 `[]`，絕不 raise（沿用 `load_elevation_nodes` fail-silent 語義）。

---

## 3. 不變量

### 3.1 回憶不得把 DORMANT 當 ACTIVE 投影（語氣隔離）

- 回憶結果**永不注入** `EMERGENT block`（主動投影路徑 `format_emergent_block` 0 改動）。
- 回憶結果呈現時必須帶過去式語氣標記（沿用 WEAKENING「我隱約覺得」先例，往過去再推一步）：
  - DORMANT → 前綴「我以前很在意……」（例：`我以前很在意「……」。`）
  - SUPERSEDED → 沿用既有 lineage 降維句式（`我以前覺得「A」，但後來發現「B」。`；`emergent_projection.py:260`）
- 目的：呼叫方不可能把回憶文本誤當當前信念。

### 3.2 essence 豁免維持

- essence 回憶查詢與其他層同介面，但 essence 的 lifecycle 語義不變：豁免 WEAKENING/DORMANT 自動衰減（`ELEVATION-LIFECYCLE.md §7.2`）——回憶讀到 essence DORMANT 節點**理論上應為空集**（essence 幾乎不進 DORMANT）；若讀到，照常回傳、不觸發任何復核（`reconsideration_candidate` 只由寫側打，回憶 0 寫入）。

### 3.3 單日噪聲拒絕沿用

- 回憶是讀側，不做 SUPERSEDE 判定，故不直接執行噪聲拒絕；但**回憶結果不得被呼叫方當成矛盾證據餵回狀態機**（契約級禁止：recall output ≠ contradiction input）。SUPERSEDE 的「矛盾獨立證據 ≥ N_supersede 且跨 ≥2 模擬日」（`ELEVATION-LIFECYCLE.md §7.1`＋`harness/tl4.py:520` `min_days_spread=2`）維持為寫側唯一門檻，回憶不開第二通道。

---

## 4. 與既有投影的邊界

| 維度 | 主動投影（`emergent_projection`，現行） | 回憶查詢（本契約，待實作） |
|---|---|---|
| 觸發 | inference-time 每次自動投影 | 顯式呼叫才查 |
| 態覆蓋 | ACTIVE（正常）＋ WEAKENING（語氣修飾） | DORMANT ＋ SUPERSEDED |
| 輸出位置 | `EMERGENT block` | 呼叫方自定（禁入 EMERGENT block） |
| 寫入 | 投影 trace sidecar（現行） | 0 寫入（§2.3） |
| 缺省態語義 | 缺 `lifecycle_state` 視為 active（additive 相容） | 同（沿用 `_normalize_lifecycle_state`） |

---

## 5. 驗收（合成資料測試清單；本票不寫測試）

測試檔位：`tests/test_elevation_recall.py`（NEW，待實作票開工；合成 JSONL 寫入 tmp `store_dir`，不碰生產 `data/elevation/`）。

| # | 驗收項 | 合成資料做法 | 通過判準 |
|---|---|---|---|
| T1 | DORMANT 可查 | tmp store 寫 1 ACTIVE ＋ 1 DORMANT（含 query 子字串）；`state_filter={"dormant"}` | 只回 DORMANT 命中，帶 `lifecycle_state="dormant"`＋`lineage_path` |
| T2 | ACTIVE 優先（邊界不漏） | 同上 store；`state_filter` 含 `"active"` | 回 `[]`（fail-closed；ACTIVE 是投影轄區，不經回憶） |
| T3 | SUPERSEDED 只讀 | A（superseded，`superseded_by=B`）＋ B（active，`parent_node_id=A`）；查 A 的 query | 回 A（帶 `superseded_by=B`＋`lineage_path`）；複查 store：A 仍 superseded、B 無新增（0 寫入、0 遷移） |
| T4 | essence 不動 | essence DORMANT 合成節點 1 筆；查詢＋全 store 快照前後比對 | 可查得；store 逐位元組一致（0 寫入）；`reconsideration_candidate` 未被觸碰 |

---

## 6. Frozen／契約觸碰清單

| 項 | 有／無 | 說明 |
|---|---|---|
| SAGE 寫入（`src/memory/sage/`） | **無** | 回憶不經 SAGE、不寫 fact |
| InnerLifeEvent（`src/inner_life/event.py`） | **無** | 不新增事件、不改欄位 |
| 4 handlers（`src/agency/` diary／dream／trigger／event_handler.py） | **無** | 讀側查詢，不經 Agency 四階段 |
| essence 門檻（上游 `soul-elevation` engine／prior＋`ESSENCE_REVISE_CONFIDENCE_DELTA`） | **無** | 門檻 0 改動；§3.2 只重申豁免 |
| `emergent_projection` 主動投影（含 `NON_PROJECTED_LIFECYCLE_STATES`） | **無** | 0 改動；回憶另開函式 |
| 上游 `soul-elevation` repo | **無** | repo 外依賴，不動 |

---

## 7. Out of Scope（本票／v1 皆不做）

- 實作 `query_recall`（另票；依賴 C1 範圍裁定批覆＋ B3 真實回憶對象）。
- DORMANT 經回憶復活（無此邊；復活唯一通道＝寫側 REINFORCE）。
- 語義／向量檢索（v1 只做子字串 deterministic 匹配；量大再開票）。
- 回憶結果自動餵入 prompt（呼叫方語義由實作票定；禁入 EMERGENT block 為鐵律）。

---

## 8. 附錄 A（2026-10-19 實作票 ELEVATION-RECALL-HARDENING-1）

> **§0–§7 逐字保留不改。** 本附錄是**附加**條款，不修改、不覆寫、不重述上面任何
> 一條契約語意；與 §0–§7 衝突時，以 §0–§7 為準。
> 實作票：`logs/ELEVATION-RECALL-HARDENING-1-WORK-ORDER.md`。

### 8.1 隔離標記（quarantine）的語意

- **來源標記，不是 lifecycle state。** `lifecycle_state` 的四態語義（active /
  weakening / dormant / superseded）**完全不受本附錄影響**；隔離是「這顆節點被
  SOUL-BUG-010 處置過」的**來源標記**，與節點當下的生命週期態是兩件事。
- **唯一載體＝生命週期 sidecar**（`elevation_lifecycle_events.jsonl`，append-only）；
  節點檔本身保持位元組不變。標記事件的 `event` 欄位值為 `"quarantine"`。
- **單點解讀者**：`src/inner_life/lifecycle_overlay.py::load_quarantined_node_ids()`。
  消費端**不得**自行解讀 sidecar（守門散落到第 N 個消費端正是 SOUL-BUG-009／010
  的病根）。判定以**最新事件**為準（last-wins，與 overlay 的狀態語意一致）：
  節點先被隔離、後又發生 transition ⇒ 不再算隔離。
- 🔴 **與工單 §3.1 的偏離（實作時發現，必須記錄）**：工單原設計把旗標以
  `merged["quarantined"] = True` 掛在**節點 dict** 上。實作證實**這會讓整條昇華
  路徑爆掉**——
  - `src/inner_life/elevation_adapter.py:935` 以 `ElevationNode(**n)` 建引擎；
  - `ElevationNode` 是外部套件 `soul_elevation` 的 frozen dataclass（欄位固定 18 個）；
  - 節點 dict 多一個 key ⇒ `TypeError: ElevationNode.__init__() got an unexpected
    keyword argument 'quarantined'`；
  - 連帶後果：SOUL-BUG-010 的 M4 守衛轉紅、M3 守衛因例外被吞而**假綠**。
  而 `src/inner_life/elevation_adapter.py` 不在本票的檔案所有權範圍內（工單 §2
  「其他檔案一律不得動」）。
  ⇒ **偏離方案**：隔離資訊改走「**節點 id 集合**」這條獨立通道
  （`load_quarantined_node_ids()`），**節點 dict 的形狀保持引擎可吃**
  （守衛 `test_node_dict_stays_engine_constructible` 釘住 `ElevationNode(**node)`
  必須可建構）。**語意等價**：兩條路徑以同一份 sidecar 事件為真相、都以最新事件
  為準，守門仍只寫在一處（共用管線 `_recallable_hits`）。
- 消費端仍可對**節點 dict 自帶**的 `quarantined` 旗標做防禦性擋下（真值即隔離）；
  兩種來源取**聯集**（寧可多擋不可漏擋）。

### 8.2 `query_recall_by_tags`（新增並存入口，**不取代** `query_recall`）

```text
query_recall_by_tags(
    agent_id: str,
    tags: Sequence[str],
    state_filter: Optional[Set[str]] = None,
    *,
    store_dir: Optional[Any] = None,
) -> list[RecallHit]
```

- **回傳形狀與排序與 `query_recall` 相同**（契約 §2.2 六鍵；`created_ts asc +
  node_id asc`）；失敗靜默（任何例外 → `[]`，絕不 raise）。
- **過濾條件與隔離守門與 `query_recall` 完全相同**——兩條路徑共用同一個過濾管線
  `_recallable_hits()`（守門不散落）。
- **比對語意**：把 query 與 content 各自**正規化**後，任一 tag 命中即算命中
  （**union**；intersection 在中文內容上會把命中率壓到近 0）。
  - 正規化：lower()、全形轉半形（NFKC）、去除所有空白、去除所有標點（Unicode
    類別 `P*`）。
  - tag 守門：非空 str、正規化後長度 ≥ 2、去重（保留傳入順序）、**上限 12 個**
    （取傳入順序前 12）。單一字串視為**單一 tag**。
  - 不做語義排序、不呼叫 LLM、deterministic。
- 契約 §2.1 的「子字串匹配」**原文保留、仍是 `query_recall` 的比對語意**；
  本附錄只**新增**一條並存入口，**不修改**契約主文任何一條。

### 8.3 硬不變式：隔離節點**永不**召回

- 被隔離的節點（§8.1）在**任何**檢索路徑下都不可被召回——`query_recall` 與
  `query_recall_by_tags` 皆然。
- 守門只寫在共用管線 `_recallable_hits()` 的第 5 步（單點收斂）。
- 任何命中隔離節點的情況＝硬不變式破裂＝**第二階段接線一律 no-go**。

### 8.4 實測依據（量測口徑寫清楚）

量測工具：`scripts/recall_readiness_cli.py`（唯讀來源＋自建 temp 沙盒；生產
`data/elevation/*.jsonl` 前後 sha256 不變）。詞元來源 SSOT：
`src/memory/v1/loader.py::derive_query_tags`（不重寫一份）。
**探針＝一筆真實 user 訊息**（`data/conversations/*.json` 中 `role == "user"`），
**單位＝probe**；命中率＝「至少命中 1 顆同 agent 可回憶節點的 probe 數 ÷ probe
總數」。

**生產圖譜現況（2026-10-19 實測）**：401 顆節點；態分布 active 216 / weakening 167 /
dormant 18；類型分布 pattern 370 / value 30 / belief 1；隔離 18 顆。

| 量 | 值（分子／分母，單位） |
|---|---|
| 可回憶池（非隔離 × 四層 × dormant\|superseded） | **0 顆**（硬化前為 2 顆，且那 2 顆正是被隔離的日文污染節點 ⇒ 守門擋下 `blocked_by_quarantine = 2`） |
| 詞元級 union 命中率（200 probes） | **0 / 200 = 0.00%** |
| 契約路徑（整句子字串）命中率（200 probes） | **0 / 200 = 0.00%** |

**「12/59 = 20.34%」的分母拆解（59 probes，忽略 agent 守門）**：

| 分母 | 命中率 | unique 節點 |
|---|---|---|
| A. 通道可回憶池（含 agent 守門） | 0/59 = 0.00% | 0 |
| B. 全部 dormant\|superseded（含 pattern、含隔離） | 7/59 = 11.86% | 3 |
| C. 全部 401 顆（含 active/weakening） | 11/59 = 18.64% | 8 |

⇒ 工單引用的 20.34% 量的是**忽略 agent 守門、且含非可回憶節點**的分母，
**不可直接搬到回憶通道**（通道受 §2.1 的 agent 守門與四層限制）。

**受控污染重現（沙盒副本，生產零寫入；注入中文 dormant 節點後的命中率）**：

| 注入顆數 | 可回憶池 | 命中率（分子/分母，單位：probe） |
|---|---|---|
| 1 | 1 | **2/200 = 1.00%**（1 顆 unique 節點、2 組命中配對） |
| 5 | 5 | 5/200 = 2.50%（3 顆 unique） |
| 20 | 20 | 8/200 = 4.00%（9 顆 unique） |
| 80 | 80 | 11/200 = 5.50%（33 顆 unique） |

⇒ **「池中有 1 顆中文 dormant 節點」的預期命中率＝1.00%**；
且命中率在這個量級**飽和**（80 顆也只有 5.50%）⇒ 原訂 15% 門檻**在池 = 5～80 顆的
量級不可達**（＝死門檻，第二階段永遠 GO 不了）。

**門檻已由主大腦於 2026-10-19 重新校準為 5%**（依據：把生產現存 **31 顆真實
value/belief 節點**（＝防線二 30 天門檻生效後真正會變成可回憶池的內容）在沙盒設為
dormant，以 **390 筆真實 user 訊息**實測命中率 = **31/390 = 7.95%**，命中 7 顆 unique
節點；池 5 時實測 2.50% 仍低於門檻 ⇒ 池小時仍會被擋下、池大時有餘裕）。

### 8.5 本附錄**不**做的事

- 不改 §0–§7 任何一條；不改 `PROJECTABLE_NODE_TYPES` /
  `NON_PROJECTED_LIFECYCLE_STATES`；不製造新 lifecycle state。
- 不讓 `pattern` 類節點回憶（契約 §2.1 明文）。
- 不做語意／向量檢索、不呼叫 LLM、不做 FTS5。
- 不做 prompt 注入（第二階段另開票；本附錄只給判準，不動 prompt 鏈路）。

