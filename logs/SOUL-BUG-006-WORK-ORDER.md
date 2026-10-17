# SOUL-BUG-006：昇華抽象化與內容去重（工單）

**狀態**：✅ 診斷完成（2026-10-17）／🟡 **程式碼與資料修改留待下一輪滿血執行**｜**裁示**：Owner 逐項拍板
**嚴重度**：P1｜**登記者**：主大腦

---

## 🔴 開工前必讀：兩個限制會決定整個修法（2026-10-17 查明）

### 限制 1｜`write_dream` 回傳的是 `Path`，不是敘事

`src/soul/dream_event.py:514-527`：`async def write_dream(...) -> Optional[Path]`

所以「從回傳值拿夢境敘事」**這條路不存在**。

### 限制 2｜`InnerLifeWriter` 是 Append-Only 單一權威，無 Patch API

`src/inner_life/writer.py` 的公開方法只有 `create_event` / `is_event_known` /
`get_event` / `get_events_by_session` / `get_events_by_correlation` /
`get_children` / `get_stats` / `get_known_event_count`。**沒有任何 update/patch 入口。**

`logs/m5_9_1_world_inner_life_boundary_audit.md:244` 明文：
「`create_event()` 是 **sole canonical identity authority**」。

### 限制 3｜生產端存在循環依賴

`scripts/run_server.py:1663-1674`（M5.4-5.4 凍結接線）：

```
create_event(extras={target_agent_id, all_agents_count})   ← 只有排程統計
  → _event_id
  → await write_dream(..., inner_life_event_id=_event_id) ← 敘事在這裡才生成
```

`write_dream` 需要 `event_id`，而 `event_id` 需要帶著夢境敘事 ⇒ **循環**。
這就是夢境事件 `extras` 永遠只有排程 metadata 的根因。

---

## ✅ Owner 拍板結論（2026-10-17，跨輪次延續，不得重開討論）

**架構決策：採「消費端反查」路徑，完全不動 `InnerLifeWriter` 與 `write_dream` 簽章。**

核心洞察：**`elevation_adapter` 消費事件時手上已經有 `event.event_id` 與 `actor_id`**，
而 `write_dream` 寫進 diary 的那筆夢境紀錄**本身就帶著剛傳進去的 `inner_life_event_id`**。
⇒ 消費端可**依 `event_id` 反查剛落盤的夢境正文**，完全不需要：
  - 回頭 Patch 已落盤的 InnerLifeEvent（不必擴張 Append-Only 邊界）
  - 二次 Submit 給 Gate（不必賭 gate 的同 event_id 去重行為）
  - 改 `write_dream` 的回傳型別（不必動簽章與既有測試契約）

### 三項工作同批完成

| # | 位置 | 工作 |
|---|---|---|
| 1 | `src/inner_life/elevation_adapter.py:167` | content 組裝：抽出乾淨人話敘事，拔除 `diary:night: narrative=` / `; slot=night` / `dream:dream:` 等 key=value 外殼 |
| 2 | `src/inner_life/elevation_adapter.py` | **Fail-closed 守衛**：無有效敘事、僅含 `all_agents_count` / `target_agent_id` 等排程統計的事件，拒絕建立昇華節點 |
| 3 | `scripts/run_server.py` 或 adapter 內 | 夢境敘事供料：於 `write_dream` 完成後，以 `event_id` 對應取得 narrative（**方案 A-i**，不改任何既有簽章） |
| 4 | `data/elevation/` | 備份至 `$env:TEMP` 後，一次性清除 `all_agents_count` 垃圾 trait(19) 與 pattern(60) 及對應邊（0 孤兒邊），並把既有 diary/world/value 節點的 content 原地脫殼（保留 `node_id` 與證據邊綁定） |

### 已授權但必須遵守的邊界

- `src/inner_life/elevation_adapter.py` **已由 Owner 明示解凍**（2026-10-17）。
- `src/memory/sage/writer.py` **仍在 frozen_dirs**，不得動。
- `M5.4-5.4`（`inner_life_event_id` 傳入 `write_dream` 的時序）與
  `tests/test_m5_4_6_1_executor_wiring.py` 釘死的初始 `extras` 形狀 **不得改動**。
- `test_no_frozen_contract_file_touched` 的 `allowed` 白名單需納入
  `elevation_adapter.py`（白名單只增一次，且**不得順手放寬**）。

### 為什麼這是「純加法」

`create_event` 的呼叫參數一字不改、執行順序一字不改、`write_dream` 簽章一字不改。
新增的是「事後取得敘事」的能力，不是「修改既有事件」的能力。
⇒ Append-Only 邊界完好，M5.4-5.4 契約完好。

---

## 目標

讓昇華管線真的能把原始事件紀錄**昇華成人格層命題**（value / trait / belief），
而不是把日記與夢境簿記字串原樣搬進圖譜。

## 現況證據（2026-10-17 唯讀實測）

`data/elevation/elevation_nodes.jsonl` ＋ `elevation_edges.jsonl`：

| 指標 | 數值 |
|---|---|
| 節點總數 | 782（pattern 752／trait 19／value 10／belief 1） |
| 候選端產出 | value 349／trait 46／belief 357 |
| 修復上線後新增節點 | 176（pattern 174／trait 1／belief 1／**value 0**） |
| value/trait 建立時間 | 全部落在 10-06 ～ 10-15 02:05，幾乎全早於 10-14 21:15 的修復 |
| 語意重複 | 3 組前 60 字完全相同（×7 akane／×6 rem／×5 yua） |
| 證據邊數 | value 28～81 條／trait 僅 2～6 條 |

**已確認 SOUL-BUG-001 的修復是有效的**：節點與證據邊持續產出、0 重複三元組、0 孤兒邊。

### 🔴 2026-10-17 實測推翻「病灶 2：升格閘門幾乎不升格」

原假設是「349 個 value 候選只有 10 個升格」。**這是統計假象，已撤銷。**

- 753 顆 `pattern` 節點按 `(agent_id, candidate_node_type)` 分組後**只有 26 組**
  （value 10 組／trait 10 組／belief 6 組）。
- 這 20 組 value/trait **全部達標**（獨立證據 ≥ `min_evidence=2` 的組數 = 組數，10/10、10/10）。
- 圖譜中的 value 節點**正好 10 顆** ⇒ **升格閘門 100% 正常運作，那 10 顆就是全部結果**。
- 被擋下的 4 組只有 1 條獨立證據，是 fail-safe 正常工作。
- 扣除已消化證據後，多數組的「可用獨立證據」= 0，代表證據已被既有靈魂節點消化。

**`candidate_node_type` 並非沒被寫入**：783 顆節點全部帶此欄位、753 顆非空
（由外部套件 `soul_elevation` 寫入，不在 `src/` 內）。

⇒ **真正的病根只有兩個**：`content` 是 metadata 序列化外殼（病灶 1）、
夢境事件根本沒有敘事可供抽象（病灶 3）。
⇒ 而病灶 3 更嚴重：**30 顆靈魂節點中 19 顆 trait（63%）的 content 是
`dream:dream: all_agents_count=10; target_agent_id=...` 這種純排程統計**。

### 現存 value/trait 的實際內容

- trait：`dream:dream: all_agents_count=10; target_agent_id=agent_X`
- value：`diary:night: narrative=<日文日記原文>; slot=night`

兩者都是**未抽象化的原始簿記字串**，不構成人格層命題。

## 範圍（待主大腦於開工時收斂）

- 昇華候選的**升格閘門**（為何 349 個 value 候選只升 10 個）
- **抽象步驟**（把 narrative/dream 字串轉成 value/trait 命題）
- **跨日內容去重**（3 組完全重複的節點如何收斂）

## 不做（Out of Scope）

- 不動檢索側（那是 `SAGE-RANK-1`）
- 不動 SOUL-BUG-001 已結案的邊重綁／節點合併邏輯
- 不得為了讓節點數變好看而放寬升格條件

## 開工前必做

1. 搜 `src/inner_life/elevation_adapter.py` 及上下游的**既有註解**——
   本 repo 慣例是「診斷早被寫下來、但從未修掉」，先確認是否已有人指出答案。
2. 對 782 顆節點做一次 `node_type` × `target` 的**組成對帳**（函式層，非總數），
   確認升格失敗發生在哪一層。
3. 抽象步驟若需要 LLM，**先回報 Bry**（花錢事項，主大腦不得自行決定）。

## Frozen Contract 注意

`src/inner_life/elevation_adapter.py` 列在
`tests/test_observability_completeness_1.py::test_no_frozen_contract_file_touched`
的 `frozen_dirs` 內（SOUL-BUG-001 曾動過它並記錄了回退理由）。
開工前須先取得 Bry 對 frozen 邊界的明示授權。

## 相關

- 索引：`logs/SOUL-BUG-INDEX.md`
- 前置：`logs/SOUL-BUG-001-WORK-ORDER.md`（邊重綁，已結案）
- 本票由 `SAGE-RANK-1` 第一階段 Part A 診斷衍生