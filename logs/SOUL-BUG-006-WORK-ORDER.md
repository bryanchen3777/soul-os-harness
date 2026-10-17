# SOUL-BUG-006：昇華抽象化與內容去重（工單）

**狀態**：🆕 OPEN｜**登記日**：2026-10-17｜**裁示**：獨立立票，排在 `SAGE-RANK-1` 之後
**嚴重度**：P1｜**登記者**：主大腦（`SAGE-RANK-1` Part A 靈魂圖譜實證體檢）

> ⚠️ **本檔目前只是備查記錄，尚未開工。**
> 本輪（`SAGE-RANK-1`）**不得動任何本 bug 相關的程式碼**。

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
壞掉的是**升格閘門**與**抽象步驟**，不是基礎持久化。

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