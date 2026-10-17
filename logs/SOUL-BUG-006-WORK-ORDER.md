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

## ⚠️ Step 1 實作現況（2026-10-17 18:36，**未提交**）

程式碼已寫入工作樹（`src/inner_life/elevation_adapter.py`，+81/-16），
但**未 commit、未 push、全庫回歸未跑**。HEAD 仍是 `7e8bc7a`。
**接手者請先讀這裡再動手，不要假設工作樹是乾淨的。**

### 已完成的兩件事

1. **實證核對真實敘事鍵名（前置強制步驟，已完成）**
   從 `elevation_nodes.jsonl` 的 content 反解出全部 extras 鍵：

   | trigger | 實際鍵名 | 有敘事？ |
   |---|---|---|
   | `diary` | `narrative`(290)、`slot`(290)、`origin_type`/`thread_id`/`title`/`sage_fact_id`(各 69) | ✅ 只有 `narrative` |
   | `world` | `world_novelty_id`/`world_source`/`world_type`(各 353) | ❌ 無 |
   | `dream` | `all_agents_count`(60)/`target_agent_id`(60) | ❌ 無 |

   ⇒ **`narrative` 是唯一帶人話正文的鍵**。
   樣本佐證：`今夜は少し冷える。窓の外を見ながら、ブライアンの声が無性に聞きたくなった。`

   🔴 **交叉驗證成功**：repo 內 `tests/inner_life/test_diary_elevation_narrative.py:68`
   `test_with_narrative_key_content_is_the_narrative` 早已把 `narrative` 當敘事鍵。
   ⇒ 這個白名單**不是憑想像**寫的，既有契約也證實了它。

2. **消費端改造（已寫入）**
   - `_NARRATIVE_EXTRA_KEYS = ("narrative",)`（刻意不收 `title`：是標題不是敘事）
   - `_event_content()`：取不到敘事就回 `""`（舊版回 `trigger_type: k=v; k=v`）
   - `inner_life_event_to_input()` → 回傳型別改 `Optional`，無敘事回 `None`
   - `_to_input()` → 回傳型別改 `Optional`，**只有 InnerLifeEvent 分支**可能回 None
   - `run_elevation`（`:595` 附近）：過濾 None + INFO 日誌記錄 fail-closed 原因

### 🔴 7 支既有測試轉紅 —— 必須分流處理，不可一刀切放寬

| 檔案 | 紅燈 | 性質 | 正確處置 |
|---|---|---|---|
| `tests/inner_life/test_diary_elevation_narrative.py::test_without_narrative_yields_label` | 1 | **舊契約，本票刻意要推翻**（`:64` 斷言 `got == "diary:night: slot=night"`） | 更新斷言為「回 `None`／不產節點」，並在 commit 記錄契約變更理由 |
| 同檔 `::test_with_narrative_key_content_is_the_narrative` | 1 | 斷言 `got.startswith(TRIGGER_TYPE_DIARY_NIGHT)` —— 舊契約要求帶 trigger 前綴 | 改為斷言「等於乾淨敘事正文、不含 `key=` 外殼」 |
| `tests/test_epistemic_horizon.py::TestVerticalFirewall`（3 支） | 3 | **疑似 fixture starvation**：fixture 事件沒帶 `narrative`，被 fail-closed 全擋 | **先查紅在哪**。若只是 fixture 缺料 → 在 fixture 補 `narrative="test narrative"`，**不得放寬 fail-closed** |
| `tests/test_elevation_evidence_scope_a1.py::test_e1_...` | 1 | 同上疑慮 | 同上 |
| `tests/test_soul_bug_watchdogs.py::test_elevation_pattern_evidence_accumulation_must_reach_two` | 1 | 同上疑慮 | 同上 |

🔴 **判別準則（必須先分清楚，否則就是在拆守衛）**：
- fixture 只是**忘了放敘事** → 補 `narrative` 欄位（正確）
- 測試**刻意在驗證「無敘事事件」** → 改寫成 fail-closed 專屬守衛（正確）
- ❌ **絕不可以**因為 fail-closed 讓下游拿不到節點，就把它放寬回去

### 尚待 Owner 拍示的行為變更

**world 與 dream 事件目前 extras 完全沒有敘事（實測）**，所以 fail-closed 會**整批**攔截它們。
world 本來就永不昇華（P0 修正），但 **world pattern 的供給會停止**。
這是刻意的行為變更，需 Owner 明確拍板。

---

**消費端要改的三處**（全在 `src/inner_life/elevation_adapter.py`）：

| 位置 | 現況 | 要做的事 |
|---|---|---|
| `_event_content()` **:128-139** | `parts = [f"{k}={v}" for k,v in sorted(p.extras.items())]` → `f"{trigger_type}: " + "; ".join(parts)`。**這就是 `diary:night: narrative=…; slot=night` 外殼的唯一來源。** | 改成先從 `p.extras` 取敘事欄位；取不到就回傳空字串 |
| `inner_life_event_to_input()` **:160-173** | docstring `:167` 明寫 `content = trigger_type + provenance.extras` | content 改用 `_event_content()` 的乾淨結果；**敘事為空時回傳 `None`** 作為 fail-closed 訊號 |
| `_to_input()` **:268-283** | 型別分發器，直接 `return inner_life_event_to_input(...)` | 加一道 `None` 檢查 → **跳過該事件，不送進 `engine.consume()`**（這就是「不寫入垃圾 pattern」的落點） |

⚠️ 改 `_to_input` 的回傳型別（新增 `Optional`）會影響所有呼叫端，
開工時必須先確認呼叫端只有 `run_elevation` 一處，並且該處有 None 檢查。

**生產端要改的位置**：`scripts/run_server.py:1663-1694`
（`create_event` → `submission_gate.submit` → `_elevate_check()` → `_writer.write_dream(...)`）。
`write_dream` 的回傳值在 `:1693` 接收，需確認它回傳的 `Path` 對應哪個 agent 的 diary 檔。

### 敘事欄位的候選鍵名（開工時需以真實資料核對，不可憑想像）

已知 diary 事件帶 `narrative` 與 `slot`；dream 事件**只有** `target_agent_id` 與
`all_agents_count`（無敘事）。⇒ fail-closed 的判定不能只認 `narrative` 一個鍵名，
必須列出一組「敘事欄位」白名單，並且**逐一回實資料確認**哪些鍵真的帶人話。

---

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