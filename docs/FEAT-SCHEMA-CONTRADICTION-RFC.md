# FEAT-SCHEMA-CONTRADICTION RFC — 矛盾探針的來源定義

> **狀態**：RFC 階段（**只定義 ＋ 沙盒量測；不落地任何生產變更**）｜待 Owner 審定
> **日期**：2026-10-22 22:40 EDT（量測時點）／2026-10-23 02:40 UTC（量測戳）
> **工單**：`logs/FEAT-SCHEMA-CONTRADICTION-RFC-WORK-ORDER.md`
> **上游**：`logs/CAMPAIGN-B-P1-D3-TENSION-DRIVER-WORK-ORDER.md`（戰役 B Phase 1，已合入 `1119ea5`）、`logs/ENGINEERING_STATE.md` §0.1.45（因子②恆 0 之發現）、§0.1.46（RFC 開票）
> **本票範圍**：新增 `src/inner_life/contradiction_probe.py`（沙盒原型）、`tests/inner_life/test_contradiction_probe.py`（守衛 T1～T6）、本 RFC、實作紀錄。**零生產變更、零 schema 變更、零引擎接線。**

---

## 1. 問題陳述

### 1.1 真正的缺口：**無呼叫者 ＋ 無衝突判準**（主大腦 2026-10-22 23:0x 更正）

> ⚠️ **更正紀錄（留痕）**：本節初稿原以「**結構性死鎖**」描述——「矛盾要靠 `supersede()` 產生，
> 而 `supersede()` 要靠矛盾才可能觸發」。**該描述不精確**，經主大腦逐行複核
> `soul_elevation/engine.py:1174-1203` 後更正：`record_contradiction()` 的寫入（`:1176-1184`）
> 發生在門檻判定（`:1194-1203`）**之前** ⇒ 累積器**可以**累積（每次 `supersede()` 嘗試都會先
> 留下一筆矛盾紀錄，門檻不足才丟 `ValueError`）⇒ **不是自我死鎖**。
> 該錯誤描述曾寫入 canonical（`logs/ENGINEERING_STATE.md` §0.1.46），已於 §0.1.49 以**新增條目**
> 更正（canonical 不覆寫）；本 RFC 尚未入庫，故直接更正並留痕。

- `engine.record_contradiction()`（`soul_elevation/engine.py:990`）是矛盾證據進入
  `ElevationNode.contradiction_pressure` 累積器的**唯一**入口。
- 它的**唯一**呼叫點在 `engine.supersede()` 內（`engine.py:1176-1184`），且位於門檻判定**之前**。
- 而 `supersede()` 的**觸發門檻**是「矛盾獨立證據數 ≥ `LIFECYCLE_N_SUPERSEDE`（其他層 3）／
  essence ≥ `LIFECYCLE_ESSENCE_N_SUPERSEDE`（5）」**且**「跨 ≥
  `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`（2）個不同模擬日」（`engine.py:1194-1203`）。

⇒ **真正的缺口是雙重的**：

1. **`supersede()` 在生產上沒有呼叫者**（harness repo 零生產呼叫者，已獨立複核）⇒ 累積器從未被寫入過；
2. **沒有「什麼算衝突」的判準** ⇒ 即使有人呼叫，也無從判斷新證據是否與既有命題衝突
   （第 1.4 節實測：四條可結構化判準，兩條恆 0、一條全是假陽性、一條已被否決）。

### 1.2 生產實況（實測，2026-10-22 22:40 EDT 唯讀）

| 量測項 | 實測值 | 單位／分母 |
|---|---|---|
| `elevation_nodes.jsonl` 節點總數 | 793 | 筆（601,607 bytes） |
| 含 `contradiction_pressure` 欄位者 | **793** | 筆／793（100%） |
| `contradiction_pressure == []` 者 | **793** | 筆／793（100%） |
| `contradiction_pressure != []` 者 | **0** | 筆／793（0%） |
| 欄位 Python 型別 | `list` | 唯一型別（793/793） |
| essence 節點 | **0** | 筆／793（該型別在生產尚未出現） |

（`sha256(elevation_nodes.jsonl)` = `486375993c00f7bd3fc5dacaab93853ec09db9694616f9bc518201f91dd83fcb`；
量測前後同一 hash ⇒ 唯讀自證，見第 4 節。）

### 1.3 本 RFC 要回答的問題

**不是**「欄位長什麼樣」（欄位已存在且語意完整），而是：

> **「候選矛盾從哪裡來？依此判準，在生產圖譜上會產生多少組？」**

本 RFC 定義探針、實作純函式原型、以**生產圖譜唯讀**量出可行性，並**誠實報出 Q1 = 0
及其成因**（第 4 節）。Phase 2 的引擎接線**只寫在第 5 節，本票不實作**。

### 1.4 四條判準路徑的實測收斂（主大腦 2026-10-22 23:0x 追加；非 worker 自報）

以生產圖譜**唯讀**實測四條可結構化的「什麼算衝突」判準：

| # | 判準路徑 | 實測候選數 | 結論 |
|---|---|---|---|
| ① | **同一 lineage 根**（本 RFC §2 規格／O-1） | **0**（篩選鏈 343 → 48 → **0**） | 恆 0。結構證據：靈魂型 136 顆，`lineage_depth` **136/136 全＝1**，父節點型別 **136/136 全＝pattern** ⇒ 相異 lineage 根 **136/136** ⇒ **兩顆互異靈魂節點永不共根** |
| ② | **同 agent ＋ 同來源**（O-2，放寬） | **48** | 🔴 **48/48＝1.0000 來自 `support_propagation` 邊**；抽查顯示配對是**兩段不相干的人生**（例：「お茶が冷めかけた頃、窓の外に月が出てた…」vs「朝、カーテンを開けたら空が高くて…」＝月亮與疲累的傍晚 vs 早晨想見 Bry）⇒ **假陽性**。且假矛盾會**餵飽 `supersede()` 門檻**（獨立數 9/15、跨日 15/15 達標）⇒ **會誤觸發 supersede ＝ 真正的資料破壞** |
| ③ | **同一命題血統**＝同 `parent_node_id` ／ 同 `lineage_path` ／ 同 `lineage_path` 首段（O-3） | **0**（三種定義全 0） | 恆 0。**136 顆靈魂節點各自擁有獨立的 pattern 根 —— 沒有任何一顆 pattern 昇華出兩顆靈魂節點**（同 parent 配對數＝0）⇒ 現有圖譜裡**不存在「同一命題的兩個表述」** |
| ④ | **valence 反轉**（引擎已有 `_valence_reversed()` 語意） | **0** | 恆 0。**valence 136/136 全為 `neutral`**（同 agent 靈魂節點配對總數 453，無任何一對反轉） |

⇒ **四條路全堵**：兩條恆 0、一條全是假陽性（且具資料破壞性）、一條（語意判定）已被召回通道
VERDICT＝NO-GO 否決（池 0 顆／命中率 0/200＝0.0000＜5%）。

⇒ **因此 D3 的問題不是「探針判準該選哪一個」，而是「現有架構裡不存在可信的衝突資料源」**——
這已超出工程判斷，屬 **Owner 層級的架構裁決**（見 §5.3）。**本 RFC 不代裁、不放寬任何判準。**


### 2.1 輸入（全部唯讀）

| 資料面 | 角色 | 說明 |
|---|---|---|
| `data/elevation/elevation_nodes.jsonl` | **矛盾主體** | 靈魂型節點：`value`／`belief`／`trait`；`essence` 不參與 Phase 1 探針 |
| `data/elevation/elevation_edges.jsonl` | **證據來源** | 既有支持邊：`node_id`／`source_id`／`source_type`／`valid_from_ts`／`valid_until_ts`／`agent_id`／`inner_life_event_id` |
| `data/elevation/elevation_lifecycle_events.jsonl` | **僅生命週期狀態對照** | **不**作為矛盾來源 |

### 2.2 候選矛盾判準（順序固定，逐條照做）

對每個 agent 的每個靈魂節點 `N`（`node_type ∈ {value, belief, trait}`、
`lifecycle_state ≠ "superseded"`）：

1. 取 `N` **建立之後**到達的有效支持邊集合 `E(N)`：`edge.node_id == N.node_id` 且
   `edge.valid_from_ts > N.created_ts` 且 `edge.valid_until_ts is None`。
2. 對每條 `e ∈ E(N)`，以 `e.source_id` 解析其**來源事件**，找出該來源在**同一 agent、
   同一 lineage 根**下產生的**另一個**靈魂節點 `N'`（`N' ≠ N`；`N'.parent_node_id` 與
   `N.parent_node_id` 同鏈或同根）。
3. 若 `N'.content != N.content`（**逐位元不等**）⇒ 記為一組**候選矛盾** `(N, N', e)`。
4. `N'.content == N.content` ⇒ **不是**矛盾（是同命題再確認；已由 REINFORCE 鏈處理）
   ⇒ 不記。

**時間比對**：`created_ts` 帶微秒、`valid_from_ts` 帶微秒或無 ⇒ **先統一解析成 aware
UTC `datetime` 再比**；壞值／缺值 ⇒ 該配對不成立（不猜、不放寬）。

### 2.3 欄位形態（沿用既有，零新增）

候選矛盾若要真的寫進引擎，欄位即既有 `soul_elevation.models.ContradictionRecord`
（`models.py:85-89`），四欄**逐字取自來源邊**：

| `ContradictionRecord` 欄位 | 取值 | 來源 |
|---|---|---|
| `source_id` | `e.source_id` | 來源邊 |
| `ts` | `e.valid_from_ts`（UTC ISO） | 來源邊（**不是** `created_ts`） |
| `event_identity` | `e.inner_life_event_id` | 來源邊（冪等去重鍵） |
| `provenance_ref` | `e.inner_life_event_id` | 來源邊 |

⇒ **本票不新增、不修改任何欄位定義**；`soul_elevation` 套件**唯讀**（外部 repo，不改）。

### 2.4 計數與跨日判定（沿用引擎既有實作，不得另立）

- 獨立矛盾數 ⇒ `soul_elevation.engine._count_independent_contradictions(records)`
- 跨模擬日數 ⇒ `soul_elevation.engine._contradiction_spread_days(records)`
- 門檻 ⇒ `LIFECYCLE_N_SUPERSEDE`（3）／`LIFECYCLE_ESSENCE_N_SUPERSEDE`（5）／
  `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`（2）——**不得新增、不得調低、不得調高**。

### 2.5 兩處判準歧義的收斂（**明確記錄，非設計變更**）

工單 §2.1 的步驟 2 有兩處可作不同讀法。本票的**收斂取捨**如下（原型與守衛皆照此）：

| 歧義點 | 本票取捨 | 理由 |
|---|---|---|
| 「同一 lineage 根」 | 取**節點層級** lineage root（沿 `parent_node_id` 鏈上溯到無父者；鏈斷時退回 `lineage_path` 首段） | 主句「同一 lineage 根」為主判準；此取捨**包含**括號內「同鏈或同根」（兩節點同根 ⇒ 其父同根或一方為另一方祖先） |
| 「另一個靈魂節點」 | 與 `N` 套用**同一組**資格條件（型別 ∈ Phase 1 型別、`lifecycle_state ≠ "superseded"`） | 探針**對稱**、可稽核；避免以已 superseded 節點作對照 |

⚠️ 這兩條取捨**直接決定 Q1 的數字**（見 §4.3 Q3 與第 6 節風險 R1）。**若 Owner 對
「同一 lineage 根」採不同讀法，探針須改寫並重跑量測**——本票**不得**為了讓數字非 0
而放寬判準。

### 2.6 原型 API（沙盒；純唯讀）

```
src/inner_life/contradiction_probe.py
  find_candidate_contradictions(nodes, edges, *, agent_id=None) -> list[CandidateContradiction]
  CandidateContradiction（frozen dataclass）:
      node_id / counterpart_node_id / source_id / ts / event_identity /
      provenance_ref / agent_id / node_type
```

- **純函式**：輸入＝已載入的節點／邊序列（dict 列或引擎物件皆可）；**自己不讀檔**
  （讀檔由呼叫端做）⇒ 測試完全不需要生產資料。
- **不得**呼叫 `engine.record_contradiction()`；**不得** import 引擎；**不得**讀 `data/`。
- **零權重／零門檻常數**：模組內**無任何數值常數**（AST 守衛 T5 釘死）。

---

## 3. 資料契約影響評估

### 3.1 結論（逐字聲明）

> **零欄位新增、既有生產 JSONL 不追加任何欄位。**

### 3.2 逐項對照

| 面向 | 影響 | 證據 |
|---|---|---|
| 新增欄位 | **0 個** | 既有 `contradiction_pressure`（`models.py:130`）欄位族不動；本票不動 `soul_elevation` |
| 修改既有欄位 | **0 個** | `ContradictionRecord` 四欄（`models.py:85-89`）只**引用**、不改寫 |
| 生產 JSONL 追加欄位 | **0 筆** | `elevation_nodes.jsonl`／`elevation_edges.jsonl`／`elevation_lifecycle_events.jsonl` 三檔**零寫入**（量測前後 sha256 相同，見 §4.5） |
| `data/soul/**/life_threads.jsonl` 的 `contradiction` 欄位 | **不新增** | 因子②（戰役 B Phase 1）維持原狀，本票不動已合入的 `tension_scoring.py` |
| 引擎 API | **0 改動** | `record_contradiction()`／`_count_independent_contradictions()`／`_contradiction_spread_days()`／`LIFECYCLE_*` 只引用 |
| 新增程式碼 | 1 個沙盒原型 + 1 個測試檔 + 2 份文件 | `src/inner_life/contradiction_probe.py`／`tests/inner_life/test_contradiction_probe.py`／本 RFC／實作紀錄 |

### 3.3 §0.1 的 793/793 實測證據（**本票獨立複核**）

| 量測項 | 實測值 | 分母／單位 |
|---|---|---|
| `elevation_nodes.jsonl` 位元組 | 601,607 | bytes（793 行 JSONL，0 壞行） |
| sha256 | `486375993c00f7bd3fc5dacaab93853ec09db9694616f9bc518201f91dd83fcb` | — |
| 含 `contradiction_pressure` 欄位者 | **793** | 筆／793（100%） |
| `contradiction_pressure == []` | **793** | 筆／793（100%） |
| `contradiction_pressure != []` | **0** | 筆／793（0%） |
| 欄位型別 | `list` | 唯一型別（793/793） |

⇒ 裁示字面（「擴充生產 Schema 涉及資料契約變更」）的前提**不成立**：欄位早已存在，
本票**零 schema 變更** ⇒ **更**符合裁示目的（「資料契約變更既然不必發生，就不碰資料契約」）。

---

## 4. 沙盒量測結果

> **量測時點**：2026-10-23T02:40:18.794673+00:00（＝2026-10-22 22:40:18 EDT）。
> **方式**：以本票原型 `src/inner_life/contradiction_probe.py` 對**生產圖譜唯讀**執行
> （`scripts/…` 皆未執行；未觸發任何 synthetic 事件；未發真實請求）。
> **輸入**：`elevation_nodes.jsonl` 793 筆／601,607 bytes／
> sha256 `486375993c00f7bd…`；`elevation_edges.jsonl` 1791 筆／696,344 bytes／
> sha256 `ccb6dc492dbb1f14…`；`elevation_lifecycle_events.jsonl` 252 筆／92,544 bytes
> （僅狀態對照，未參與判定）。
> **語意基準**：`0/0` 一律標「**無定義**」。

### 4.0 量測宇宙（先確認基準）

| 項 | 值 | 分母／單位 |
|---|---|---|
| 節點總數 | 793 | 筆 |
| `pattern` 節點 | 657 | 筆／793 |
| 靈魂型節點（value 125＋belief 6＋trait 5） | **136** | 筆／793 |
| lifecycle 狀態 | `active` 793／793 | 筆（`superseded` 0 筆） |
| 邊總數 | 1791 | 筆 |
| `valid_until_ts is None` | 1791／1791 | 筆（無失效邊） |
| `trigger_type == "support_propagation"` | **48** | 筆／1791 |

### 4.1 Q1 — 依 §2.2 判準，生產圖譜上會產生多少組候選矛盾？

| 項 | 值 |
|---|---|
| 分子（候選矛盾組數） | **0** |
| 分母（符合前提的 `(N, e)` 配對總數） | **343** |
| 比值 | **0／343 ＝ 0.0000**（組／配對） |

**篩選鏈（逐關）**：

| 關卡 | 存活 | 說明 |
|---|---|---|
| 步驟 1：`E(N)`（`valid_from_ts > created_ts` 且仍有效） | **343／343** | 時間門檻**沒有**濾掉全部 |
| 步驟 2a：同 agent ＋ 同 `source_id` 有另一顆靈魂節點 | **48／343** | 來源解析**沒有**濾掉全部 |
| 步驟 2b：**同一 lineage 根** | **0／48** | 🔴 **唯一把配對清零的判準** |
| 步驟 3：`content` 逐位元不等 | — | 近門 48 組的 content **全部相異** ⇒ content 判準**不是**成因 |

### 4.2 Q2 — 分佈

**規格候選（0 組）**：agent 分佈＝**無定義**（0 筆）；node_type 分佈＝**無定義**（0 筆）；
`ts` 最早／最晚／跨度＝**無定義**（0 筆）。

**【診斷】僅差「同一 lineage 根」一條件的近門配對（48 組，非本票判準，僅供 Owner 判讀）**：

| 維度 | 值 |
|---|---|
| agent | ruka 6、anna 6、aoi 6、miku 6、ram 6、rem 6、akane 4、mahiru 4、mai 4（組／48） |
| node_type（`N` 側） | `value` 48／48（`belief` 0、`trait` 0） |
| `ts` 最早 | `2026-10-21T12:00:15.450271+00:00` |
| `ts` 最晚 | `2026-10-23T02:01:37.427165+00:00` |
| `ts` 跨度 | 1 天 14:01:21.976894 ＝ **1.584 天** |
| 不同模擬日 | **3**（2026-10-21／10-22／10-23） |
| `e.trigger_type` | `support_propagation` 48／48 |

### 4.3 Q3 — Q1 ＝ 0 的成因（**是哪一條判準濾掉全部配對**）

**成因＝§2.2 步驟 2 的「同一 lineage 根」判準**（48／48 組在此被濾掉）。步驟 1（時間門檻）
與步驟 3（content 逐位元不等）**都不是**成因。

**結構性證據（為什麼這條判準在現行資料形狀下恆不成立）**：

| 量測項 | 值 | 分母／單位 |
|---|---|---|
| 靈魂節點總數 | 136 | 筆 |
| `lineage_depth` 分佈 | `{1: 136}` | 筆／136（**無 depth ≥ 2 的靈魂節點**） |
| 父節點型別分佈 | `{pattern: 136}` | 筆／136（**父皆為 pattern**） |
| 父為靈魂節點者 | **0** | 筆／136 |
| 相異 lineage 根數 | **136** | 個／136（每顆靈魂節點各有自己的 pattern 根） |

⇒ 靈魂節點一律是**各自** pattern 的子節點 ⇒ 兩顆**互不相同**的靈魂節點在現行資料形狀下
**永不共根**（除非其中一顆是另一顆的 revise 子節點；現行 **0** 筆）⇒ 該判準恆為
「不成立」⇒ 候選恆 0。

⚠️ **本票未為讓數字非 0 而放寬任何判準、調低任何門檻。** 上表的最後兩列即為 Owner 裁決
「同一 lineage 根該不該讀成別的意思」的**決策輸入**（第 5 節前置條件、第 6 節風險 R1）。

### 4.4 Q4 — 以引擎既有計數器，這些候選若寫入累積器，會達門檻嗎？

**門檻（引用引擎既有常數，零新增）**：`LIFECYCLE_N_SUPERSEDE=3`、
`LIFECYCLE_ESSENCE_N_SUPERSEDE=5`、`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD=2`。

**規格候選（0 組）**：**所有節點的獨立矛盾數 ＝ 0、跨模擬日數 ＝ 0** ⇒ 全部未達門檻
（0／136 節點達門檻）⇒ **死鎖不會因本票的規格判準而解除**。

**【診斷】若僅放寬「同一 lineage 根」一條（非本票設計；僅供 Owner 判讀可行性）**——
以引擎既有計數器逐節點計算（受影響節點 15 顆）：

| 節點（node_id 前 12） | agent | 記錄數 | 獨立矛盾數／門檻 | 跨模擬日數／門檻 | 達門檻 |
|---|---|---|---|---|---|
| `7906aef95d55` | akane | 4 | **4／3** | **3／2** | 是／是 |
| `33194d9da2fb` | mahiru | 4 | **4／3** | **3／2** | 是／是 |
| `536049d72ce4` | ram | 4 | **4／3** | **3／2** | 是／是 |
| `6d6de3b3f240` | mai | 4 | **4／3** | **3／2** | 是／是 |
| `9a1daf40ee9f` | rem | 4 | **4／3** | **3／2** | 是／是 |
| `b031a9fb32f6` | anna | 4 | **4／3** | **3／2** | 是／是 |
| `b82bd21bcf1c` | miku | 4 | **4／3** | **3／2** | 是／是 |
| `cbd28b1afed3` | ruka | 4 | **4／3** | **3／2** | 是／是 |
| `171be5175389` | aoi | 4 | **4／3** | **3／2** | 是／是 |
| `2c6d0cabb484` | ruka | 2 | 2／3 | 2／2 | 否／是 |
| `37cd3ff1ab2d` | ram | 2 | 2／3 | 2／2 | 否／是 |
| `4c943311aedc` | anna | 2 | 2／3 | 2／2 | 否／是 |
| `68435bfe2da4` | rem | 2 | 2／3 | 2／2 | 否／是 |
| `7a4596621757` | aoi | 2 | 2／3 | 2／2 | 否／是 |
| `e928bbed121f` | miku | 2 | 2／3 | 2／2 | 否／是 |

⇒ 達獨立數門檻者 **9／15** 節點；達跨日門檻者 **15／15** 節點。
⚠️ **此表是診斷，不是本票的設計主張**：放寬 lineage 判準會讓 **REINFORCE 傳遞邊**
（見 Q5）被當成矛盾來源，存在語意誤判風險（見第 6 節 R2）。

### 4.5 Q5 — 與既有 REINFORCE 傳遞邊是否重疊？

| 項 | 值 | 單位／分母 |
|---|---|---|
| 生產 `trigger_type == "support_propagation"` 邊 | **48** | 筆／1791 |
| 規格候選覆蓋之邊 | **0** | 邊（候選集合為空） |
| **規格口徑重疊組數** | **0／0 ＝ 無定義** | 組 |
| 近門配對用到 `support_propagation` 邊 | **48／48 ＝ 1.0000** | 組／48 |

⇒ 規格判準下無從判斷（候選為 0）；但**近門 48 組 100% 來自 REINFORCE 傳遞邊**
⇒ 若放寬 lineage 判準，探針的第一個信號源**全部**是 REINFORCE（**確認**）而非反例。

### 4.6 Q6 — `agent_akane` 單獨列出（Phase 1 唯一標桿角色）

| 項 | 值 | 單位／分母 |
|---|---|---|
| 靈魂節點數 | 10 | 筆 |
| `E(N)` 配對數（分母） | **43** | 組 |
| **規格候選矛盾組數（分子）** | **0** | 組／43 ＝ **0.0000** |
| 近門配對（僅差「同一 lineage 根」） | **4** | 組／43 ＝ 0.0930 |
| 對應節點 | `7906aef95d55`（`value`）vs `a6aad545683c`／`a0e77f23ae95`／`1408be73efa34`／`9f74b9763bc9` | — |
| `ts` | 2026-10-21T12:00:25.792506／10-22T02:00:36.251867／10-22T12:00:13.084868／10-23T02:01:37.724317 | UTC ISO |
| `e.trigger_type` | `support_propagation` 4／4 | 組／4 |
| **【診斷】** 獨立矛盾數／跨模擬日數 | **4／3**、**3／2** | 達門檻＝是／是 |

⇒ 依規格判準，`agent_akane` 與全體同樣為 **0** 組候選；成因與全體相同（§4.3）。

### 4.7 守衛與變異結果（§4.1／§4.2 工單驗收）

| 守衛 | 內容 | 結果 |
|---|---|---|
| T1 | 判準正確（同根＋content 相異＝1 組／content 逐位元相同＝0 組／異根＝0／異 agent＝0／superseded＝0／essence＝0／pattern≠對應節點） | 綠 |
| T2 | 時間門檻（早於／等於建立時刻／失效邊／微秒與無微秒／Z 與 +08:00 偏移／壞值） | 綠 |
| T3 | 欄位形態（`source_id`／`ts`／`event_identity`／`provenance_ref` 逐字等於來源邊；`ts` 取 `valid_from_ts`；frozen） | 綠 |
| T4 | 純函式（兩次呼叫逐位元相等／輸入順序不變多重集／公開 API 不接受路徑／AST 無檔案 IO） | 綠 |
| T5 | 零新常數（AST：**零數值常數**／無權重命名／無自訂門檻常數／不重定義 `LIFECYCLE_*`） | 綠 |
| T6 | 不寫引擎狀態（傳入節點 `contradiction_pressure` 逐位元不變／模組不引用 `record_contradiction`／不 import 引擎） | 綠 |

**變異 M1～M4：每條都真的轉紅，且位元還原（sha256 相同）**：

| 變異 | 預期轉紅 | 實得轉紅 |
|---|---|---|
| M1 放寬 `content` 相等判準 | T1 | `test_t1_byte_identical_content_is_not_a_contradiction`（1 failed／29 passed） |
| M2 移除 `valid_from_ts > created_ts` | T2 | T2 全數轉紅（＋T1/T3/T6 連帶，見實作紀錄） |
| M3 `ts` 取 `created_ts` | T3 | `test_t3_candidate_fields_are_verbatim_from_the_source_edge`＋`test_t3_ts_is_valid_from_ts_not_created_ts` |
| M4 回傳前呼叫 `engine.record_contradiction()` | T6 | `test_t6_module_never_references_record_contradiction`＋`test_t6_probe_does_not_import_the_engine` |

---

## 5. Phase 2 落地提案（**本票不實作**）

### 5.1 接線提案（若 Owner 審定通過，另開工單）

在 `src/inner_life/elevation_adapter.py` 的候選迭代處，對**每組候選矛盾**呼叫：

```
engine.record_contradiction(
    node_id,
    source_id=cand.source_id,
    ts=cand.ts,
    event_identity=cand.event_identity,
    provenance_ref=cand.provenance_ref,
)
```

冪等鍵＝`(node_id, source_id, event_identity)`（既有 `record_contradiction()` 內建去重）
⇒ 累積器在 `supersede()` **之前**被填滿 ⇒ **死鎖解除**。

### 5.2 🔴 前置條件（本票量測直接指出，**必須先裁決**）

量測結果（§4.3）顯示：依**現判準**「同一 lineage 根」，候選在生產恆 **0** ⇒
**即使完成 §5.1 的接線，死鎖也不會解除**（累積器仍恆空）。

⇒ Phase 2 啟動前，Owner 必須先裁決其中一條（**本票不代為裁決**）：

| 選項 | 說明 | 量測後果 |
|---|---|---|
| **O-1** | 維持「同一 lineage 根」不變 | 候選恆 0／136 節點達門檻；**死鎖不解**；須另尋矛盾來源 |
| **O-2** | 放寬為「同 agent ＋ 同來源」（放棄 lineage 約束） | 48 組候選；9／15 節點達獨立數門檻、15／15 達跨日門檻；**但 100% 來自 REINFORCE 傳遞邊** ⇒ 語意誤判風險（R2） |
| **O-3** | 改判準語意（例：同一**命題血統**＝同 pattern 祖先／同 `lineage_path` 首段） | 需重跑本票量測（原型可支援，惟須改 §2.2 步驟 2 的收斂並重測守衛） |

### 5.4 🔴 裁決記錄（Owner 2026-10-22 23:1x）：**採納選項 A（誠實收斂）** ⇒ **Phase 2 不進行**

Owner 裁決（逐字要點）：

> **堅決拒絕**為了讓模組「跑得起來」而人為放寬衝突判準（O-2）。實測顯示那 48 組候選邊
> 100% 屬於支持傳遞邊，內容描繪的是**跨日的生活切片而非邏輯衝突**；若強行認定為矛盾，
> 將以**假矛盾餵飽 supersede 門檻**，直接導致系統**誤殺活著的命題**。
> **不引進外部高成本判定**（否決選項 B），**亦不破壞單子靈魂語意**（否決選項 C）。

⇒ 本 RFC 的**工程後果**（已由主大腦落實，見 §7）：

1. **D3 自 CAMPAIGN-B 的張力驅動評分目標中移除**；Phase 1 聚焦於**具備真實資料面的 D1**
   （資訊缺口 ＋ 時間態衰減）。
2. **§5.1 的 Phase 2 接線提案不實作**（不動 `elevation_adapter.py`、不接
   `engine.record_contradiction()`、不部署）。
3. **O-1／O-2／O-3 全數結案**（O-2 被明確否決；O-1、O-3 隨 D3 移除而不再需要裁決）。
4. **Belief／Trait 的長效保活回歸既定「方向 A」**（等待真實回憶召回池隨時間自然成熟），
   **不走任何捷徑**。
5. 本 RFC 的**沙盒原型與守衛保留在 repo**（`src/inner_life/contradiction_probe.py` ＋
   `tests/inner_life/test_contradiction_probe.py`），作為**未來若重新評估衝突判準**時的
   可重跑基準與**架構誠實性的證據**（本票實測的四條判準路徑結論已寫入
   `logs/ENGINEERING_STATE.md` §0.1.49）。

### 5.5 Phase 2 需要的 Owner 授權（**已因裁定 A 而不適用**）

> 本節保留原文以留痕。裁定 A 後**不需要**下列任何一項授權；若未來要重新評估衝突判準，
> 必須**重新取得** Owner 授權並另開工單（不得沿用本節）。

1. 動 `src/inner_life/**`（`elevation_adapter.py` 接引擎寫入路徑）。
2. 接引擎寫入路徑（`engine.record_contradiction()` 從生產流程被呼叫）。
3. 部署與重啟（官方 `.\scripts\server_ops.ps1 restart`，需 Owner 明示授權）。
4. **（若採 O-2／O-3）** 對「什麼算矛盾」的語意裁決 —— 屬資料契約層級的決定。
   ⇒ **裁定 A 已明確否決 O-2。**

---

## 6. 風險與不做

### 6.1 風險

| # | 風險 | 影響 | 緩解（本票已做） |
|---|---|---|---|
| R1 | 「同一 lineage 根」判準在現行資料形狀下**恆不成立**（136 顆靈魂節點各有自己的 pattern 根） | 候選恆 0 ⇒ Phase 2 接線也解不了死鎖 | 已量測並量化（§4.3）＋ 把裁決題寫成 §5.2 的 O-1／O-2／O-3；**本票不自行放寬** |
| R2 | 近門 48 組 **100% 來自 `support_propagation`**（REINFORCE＝命題**再確認**，非反例） | 若採 O-2，探針會把「再確認」誤判為「矛盾」⇒ 假陽性 | §4.5 明示重疊 48／48；§4.4 標明診斷表非設計主張；O-2 需 Owner 另裁語意 |
| R3 | 以 `source_id` 作「來源事件」鍵較粗（`event_identity` 更細） | 可能把不同事件誤視為同一來源 | 本票**照工單逐字**用 `source_id`；細化屬 O-3 範圍 |
| R4 | `ts` 取邊的 `valid_from_ts` ⇒ 跨模擬日數取決於**模擬時鐘**，非真實日曆 | 跨日門檻判定可能與人類直覺不符 | 沿用引擎既有 `_contradiction_spread_days()`，**零新語意** |
| R5 | 生產 essence 節點 **0 筆** | essence 專屬門檻（5／valence 反轉）無資料可驗 | 已於 §1.2 明示；本票不造資料 |
| R6 | 探針需在記憶體裝載整張圖 | 793 節點／1791 邊 ⇒ 量級極小（< 1 MB） | 已實測；原型保持純函式、不自行讀檔 |

### 6.2 不做（Out of Scope，本票逐項自證）

| 不做項 | 自證 |
|---|---|
| 不新增／不修改任何生產欄位 | `data/**` 位元恆等（§4.5 讀取側 sha256 ＋ 實作紀錄的全樹 manifest 0 差異） |
| 不寫入 `data/**` | 全程唯讀；量測前後兩檔 sha256 相同 |
| 不呼叫 `engine.record_contradiction()`／不接引擎寫入路徑 | 原型**零** `soul_elevation` import（T6 AST 守衛釘死；M4 變異證明守衛會轉紅） |
| 不改 `src/inner_life/tension_scoring.py`／`tension_closure_loop.py`／`drive_profile.py` | 本票檔案清單不含這三者（戰役 B Phase 1 凍結） |
| 不改 `soul_elevation` 套件 | 唯讀引用其常數與函式；外部 repo 零寫入 |
| 不改 `src/inner_life/elevation_adapter.py`／`src/llm/proxy.py`／`src/soul/**`／`src/memory/sage/**`／`personas/**`／`configs/**` | 同上（檔案清單） |
| 不部署、不重啟、不動行程、不觸發 synthetic 事件 | 本票未啟動任何服務、未發真實請求、未動行程 |
| 不 `git add`／`commit`／`push` | 本票僅工作區檔案；合入由主大腦統一處理 |
| 🔴 不為讓 Q1 ≠ 0 而放寬判準或調低門檻 | Q1 如實報 0 ＋ 成因（§4.3） |

### 6.3 Frozen Contract 零觸碰自證

| Frozen 項 | 自證 |
|---|---|
| Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯 | 本票改動檔案清單不含 `src/agency/*`／`src/soul/**`／`src/memory/sage/**` |
| `tests/test_observability_completeness_1.py` 的 `frozen_dirs` 11 筆 | 本票**未動**該檔；11 筆全保留 |
| `soul_elevation` 的 `ContradictionRecord` 欄位族／`LIFECYCLE_*` 門檻／兩個計數器 | 只引用、不改寫（外部 repo 唯讀） |
| 戰役 B Phase 1 三模組 | 凍結未動 |

---

## 附錄 A — 術語與檔案對照

| 名稱 | 位置 |
|---|---|
| `record_contradiction()` | `soul-elevation/src/soul_elevation/engine.py:990` |
| `record_contradiction()` 唯一呼叫點 | 同上 `:1176-1184`（在 `supersede()` 內） |
| `supersede()` 門檻拋錯 | 同上 `:1194-1203` |
| `_count_independent_contradictions()` | 同上 `:195` |
| `_contradiction_spread_days()` | 同上 `:215` |
| `ContradictionRecord` | `soul-elevation/src/soul_elevation/models.py:85-89` |
| `ElevationNode.contradiction_pressure` | 同上 `:130` |
| `LIFECYCLE_N_SUPERSEDE`／`_ESSENCE_N_SUPERSEDE`／`_MIN_DAYS_SPREAD` | `soul-elevation/src/soul_elevation/prior.py:68,72,69` |
| 本票原型 | `src/inner_life/contradiction_probe.py` |
| 本票守衛 | `tests/inner_life/test_contradiction_probe.py` |
