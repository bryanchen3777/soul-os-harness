# SOUL-BUG-010：生命週期讀取面收斂 ＋ 防線二（Tiered Consumption）（工單 v2）

**狀態**：📋 裁決完備，**待 Owner 就「推翻 datetime.max 哨兵」一點回覆後即可實作**
**日期**：2026-10-18 初版 → 2026-10-19 依 SOUL-AUDIT-013-RO 覆寫（v2）｜**嚴重度**：P1（架構缺口，非故障）
**登記者**：主大腦

> **v2 為何推翻 v1**：v1 宣稱「全庫共 4 條路徑讀 `elevation_nodes.jsonl`」。SOUL-AUDIT-013-RO
> 唯讀審查 + 主大腦二次獨立複核後證實此數字**錯誤且架構方向錯誤**。v1 打算「逐條讀取路徑各自
> 套 overlay」，那是 7 處重複、且必然漏一處的脆弱做法。v2 改為**單一收斂點**
> （`load_elevation_nodes` 內部套用），並修正兩個型別／端點事實錯誤。逐項差異見文末「v1 → v2 變更對照」。

---

## 目標

讓生命週期狀態在**每一條讀取路徑上真的生效**，並讓 `dormant` 節點**真的停止繁殖**——
同時保住「細水長流的跨週習慣仍可沉澱為靈魂」這條哲學底線。

一句話：**目前靈魂層只會生、不會死；且就算接上生命週期，讀取面也不認得它。**

---

## 一、端點盤點（主大腦逐行複核，非採信子代理報告）

全庫搜尋 `load_elevation_nodes` / `NODES_FILENAME` 後，**生產端**觸及節點檔的點共 8 處，
性質分三類：

### A 類：唯一資料源（1 處）— 收斂點

| # | 位置 | 事實 |
|---|---|---|
| 1 | `src/inner_life/emergent_projection.py:92` `load_elevation_nodes()` | 定義處。`:102` 開檔、`:117-118` 逐行 append、`:122` return。**這是全庫唯一的節點讀取入口定義**。 |

### B 類：消費端（5 處，全部經由 #1）

| # | 位置 | 消費什麼 | 是否已有生命週期過濾 | overlay 後的動作 |
|---|---|---|---|---|
| 2 | `elevation_adapter.py:1120` → `:924` `ElevationNode(**n)` | 送進引擎 | **無**（`lifecycle_state` 完全不出現在本檔） | 靠 #1 自動獲得狀態 |
| 3 | `emergent_projection.py:231` `project_emergent` | 投影進 prompt | **有**：`NON_PROJECTED_LIFECYCLE_STATES`（`:64-68` / `:248-251`） | 靠 #1，**零改動** |
| 4 | `elevation_recall.py:90` | 歷史回憶 | **有**：`RECALLABLE_LIFECYCLE_STATES`（`:87` / `:115-117`） | 靠 #1，**零改動** |
| 5 | `src/goals/seed_provider.py:530` | 目標種子 | **有**：`:534` `not in ("active","weakening")` | 靠 #1，**零改動** |
| 6 | `src/io/gateway.py:285` → `:289-308` | **對外 API** `/api/soul/essence/{agent_id}` | **無** — 全程未讀 `lifecycle_state` | 🔴 **唯一真實洩漏口，必須自行加過濾** |

### C 類：觸及檔名／檔案但**不需要** overlay（2 處）

| # | 位置 | 事實 | 裁決 |
|---|---|---|---|
| 7 | `elevation_adapter.py:462` `_load_existing_dedup_keys` 裸 `open()` | 只取 `(agent_id, node_type, _dedup_content_hash(content))` 三元組當去重鍵。**完全不消費 `lifecycle_state`**。 | **不需 overlay**（加了也不影響任何行為）。回報必須明說。 |
| 8 | `src/world/elevation_adapter.py:56` | 只 `import NODES_FILENAME` 常數且**全檔未使用**（死碼 import）。 | **不需 overlay**。回報必須明說。 |

**v1 的「4 條」錯在哪**：v1 把 #1 與 #2 混為一談、漏了 #5 #6、把 #7 誤當成節點載入點、
把 #8 誤當成讀取點。實際需要動手的是 **1 個收斂點 + 1 個消費端過濾**。

---

## 二、架構裁定（決策已定，執行者照做）

### 裁定 1：overlay 套在 `load_elevation_nodes` **內部**，不是逐條呼叫端套

- **理由**：#2~#6 全部經由 #1 取得節點。在 #1 內部套 overlay ⇒ **一次收斂、五個消費端同時生效**，
  結構上不可能漏。反之逐條套用則是 5 處重複程式碼，新增第 6 個消費端時必然再漏一次
  —— 這正是 SOUL-BUG-009 的同款病根（守門散落）。
- **風險控制**：`load_elevation_nodes` 的回傳**必須維持 `list[dict]`**（既有測試
  `test_emergent_projection.py:117` 斷言 `== []`、多處 `n["node_id"]` 下標存取），
  且**不得改動** `_normalize_lifecycle_state`（缺省 → `active`）與
  `NON_PROJECTED_LIFECYCLE_STATES`（Frozen Contract）。

### 裁定 2：`io/gateway.py` 補上生命週期過濾

- 只加 `dormant` / `superseded` 的排除，沿用 `emergent_projection.NON_PROJECTED_LIFECYCLE_STATES`
  **同一個常數物件**（不得另寫一份字面值，避免三處狀態集分裂——SOUL-BUG-011 的前車之鑑）。
- 過濾點放在 `:291` 的 agent 過濾之後、`:295` 的 node_type 過濾之前。

### 裁定 3：🔴 **推翻** v1／前序裁決的 `datetime.max` 哨兵

- 前序裁決理由是「`_EH2_SENTINEL` 是字串寫入 `Optional[datetime]` 型別違規」。**該前提經查證為錯誤**：
  - `soul-elevation/src/soul_elevation/models.py:176` 明文：`valid_until_ts: Optional[str]`（**str**，非 datetime）；
    `:175` `valid_from_ts: str` 同理。
  - 全庫消費端（`engine.py:446 / 802 / 832 / 1308`）一律 `is None` 恆等比較，無任何 datetime 算術。
- **結論**：現行字串哨兵 `_eh2_r3_blocked` **本來就型別正確、行為等價**。改用
  `datetime.max.replace(tzinfo=timezone.utc)` 反而會**製造**一筆真正的型別違規。
- **裁決：不改哨兵。** 若日後 `soul_elevation` 把該欄改成 datetime 型別，再另開票處理。

### 裁定 4：5 顆受害節點以 **sidecar 事件**隔離，**絕不重寫節點檔**

- `elevation_nodes.jsonl` 是 append-only 單一權威（Owner 紅線）。節點降級只能寫進
  新建的 `elevation_lifecycle_events.jsonl`，由 overlay 在讀取時疊加 ⇒ **節點檔位元組不變**。
- 🔴 **本次實作階段不得寫生產 `data/elevation/`**（AGENTS.md 第 8 條：`data/**` 為生產狀態，
  不得為了「清理」而寫入）。實際寫入屬**部署階段**，需 Owner 當次明示授權，且必須與
  重啟同一批次（舊程式碼無 overlay，單獨寫 sidecar 不會改變任何行為）。

---

## 三、做法（範圍明確，不得自行擴張）

### 步驟 1：新增純函式模組 `src/inner_life/lifecycle_overlay.py`

- `LIFECYCLE_EVENTS_FILENAME = "elevation_lifecycle_events.jsonl"`
- `load_lifecycle_events(store_dir) -> list[dict]`
  - 缺檔 → `[]`；`OSError` → `[]`；單行壞 JSON → 跳過該行。
  - **絕不 raise**（沿用本專案 fail-silent 慣例）。
- `TERMINAL_OR_DORMANT_LIFECYCLE_STATES: frozenset = frozenset({"dormant", "superseded"})`
- `apply_lifecycle_overlay(nodes, events) -> list[dict]`
  - **純函式**：回傳**新的 dict 淺拷貝**列表，**不得**原地 `setattr`／原地改動呼叫端傳入的 dict
    （`load_elevation_nodes` 的回傳物件有其他消費端共享，現場改會造成跨呼叫污染）。
  - 以 `node_id` 為鍵；**同一 `node_id` 多筆事件取檔案順序最後一筆**（append-only ⇒ 檔案順序即時間序）。
  - 事件覆蓋欄位僅兩個：`lifecycle_state`、`superseded_by`。
  - 事件若帶未知 `lifecycle_state` 值 → **忽略該筆**（不得寫進節點 dict，
    否則會污染 `_normalize_lifecycle_state` 的 additive 語意）。
  - `nodes` 非 dict 元素 → 原樣保留、不套用。
- **純標準函式庫**：`json` / `pathlib` / `typing` only。
  **不得 import `soul_elevation`**（`emergent_projection.py:61-63` 明文要求 read-side 獨立於該可選依賴），
  亦不得 import `src.inner_life` 下任何模組（避免循環）。

### 步驟 2：`load_elevation_nodes` 內部套用 overlay

- 在 `emergent_projection.py:122` `return nodes` 之前，改為套用 overlay 後回傳。
- **不得改動**該函式的 docstring 既有語意之外的行為；簽章不變（仍 `store_dir` 單一參數）。
- 零 sidecar 時 overlay 必須是**逐位元 no-op**（回傳等值 dict 列表）。

### 步驟 3：`io/gateway.py` 補生命週期過濾

- 於 `:291` agent 過濾之後插入狀態過濾，使用 `NON_PROJECTED_LIFECYCLE_STATES`。
- **不得**改動回傳 dict 的鍵集合（既有 API 契約）。

### 步驟 4：`elevation_adapter.py` R3 邊中和加 `try/finally`

- 保護範圍必須**前推至包含** `:1234` 的 `_select_anchor_representative(...)` 呼叫
  —— 該呼叫在 `try` 之外，可拋 `KeyError`（`[engine._nodes[pid] for pid in pids]`）。
- **成功路徑也必須還原**（現行 `:1267-1268` 直接 append，從不還原，
  使 `:1210` 註解「不永久丟失」自陳為假）。
- 還原語意：`for e in neutralized: e.valid_until_ts = None`。
- 允許保留既有的 `_anchor is None` 早退分支與兩層 `except`（它們只是 `try` 內的
  `continue`／log，不構成重複還原）；**但必須確認還原只發生一次**。

### 步驟 5：`elevation_adapter.py` 防線二——昇華層排除終態／休眠 pattern

- 在 `elevate_matured_patterns` 的候選分組處（`:1193-1199` `candidates` 建構迴圈），
  排除 `node.lifecycle_state ∈ TERMINAL_OR_DORMANT_LIFECYCLE_STATES` 的 pattern。
- **只排除，不改權重**：`active` 與 `weakening` 的計票方式逐位元不變。
- 必須留註解說明語意：**「一個月完全無人問津的陳年廢棄 pattern 不得被挖出來湊數升格」**，
  而**不是**「年紀大就沒資格」——跨週習慣在 `weakening` 區間內仍有完整投票權。
- 本步**依賴**步驟 2（`load_elevation_nodes` 已帶入 `lifecycle_state`）；若 sidecar 為空則
  全數 `active`，行為與現況逐位元相同。

---

## 四、驗收（完成的定義）

1. **`elevation_nodes.jsonl` 位元組不變**：實作前後 sha256 與檔案大小逐位元相同。
   基線：`elevation_nodes.jsonl` = 261,447 bytes ／ sha256 `C8794F96…21B6`（2026-10-19 00:07 快照）。
2. **防線一有效**：`dormant` 節點不得出現在 `project_emergent()` 輸出。
3. **防線二有效**：同一個 `dormant` pattern **無法**單獨把某維度推過 `min_evidence`；
   同一個 `weakening` pattern（8 天區間）**仍可**正常升格。
4. **對外 API 不洩漏**：`/api/soul/essence/{agent_id}` 不得回傳 `dormant`／`superseded` 節點。
5. **R3 不洩漏**：`engine.elevate` 成功（回傳 soul node）後，被中和的邊 `valid_until_ts`
   必須**全部回到 `None`**，且同 agent 的**後續候選維度**計票不受影響。
6. **零迴歸**：全庫 `.venv\Scripts\python.exe -m pytest -q tests` 與實作前同數，
   解釋器口徑一致（`.venv`）。

---

## 五、測試

- 新測試檔 `tests/inner_life/test_soul_bug_010_lifecycle_overlay.py`
- **不得**啟動伺服器、**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`、**不得**對生產發真實請求、
  **不得**殺任何行程。
- `store_dir` 一律指向 `tmp_path`；**不得**寫入 `data/**`。
- **變異實驗至少覆蓋 4 條，每一條都必須轉紅**：
  1. 拿掉 overlay 套用（`load_elevation_nodes` 不再疊加）
  2. 拿掉 gateway 過濾
  3. 拿掉防線二的 dormant 排除
  4. 把 `weakening` 誤一併排除（必須轉紅——證明守衛分得清兩種終止語意）
  5. 拿掉 R3 的 `finally` 還原（成功路徑洩漏）
- **反空轉設計**：每條「不得發生 X」都配「該發生的位置確實發生了」的對照組。
- **雙向證明**：真實碼 RED 不足以為證，必須再驗修復版 GREEN（否則分不出「守衛認得出缺陷」
  與「測試工具本身壞掉」）。

---

## 六、不做（Out of Scope）

- ❌ **不改** `soul_elevation` 任何一行（repo 外 editable 依賴）。
- ❌ **不接** `evaluate_lifecycle_all` 與 night slot 接線（v1 步驟 3／5）。
  **理由**：那是「自動老化」而非「讀取面收斂」，且需部署重啟才能生效，另開 SOUL-BUG-010-B。
  ⚠️ **誠實揭露**：本票落地後，**只有被隔離的節點是終態，其餘節點在 B 票上線前仍全部 `active`**。
  這不是假防線（防線一／防線二對被標記的節點是真實行為變更），但**自動老化確實尚未存在**。
- ❌ **不接** `record_contradiction`／`supersede`（需 NLI 矛盾偵測，另一張票）。
- ❌ **不整檔覆寫或重排** `elevation_nodes.jsonl`。
- ❌ **不改** `emergent_projection` 的過濾邏輯與 `_normalize_lifecycle_state`。
- ❌ **本次不寫生產 `data/elevation/`**（部署階段另行授權）。
- ❌ 不碰 `personas/`、`configs/`、`data/`。

---

## 七、Frozen Contract 注意

- `elevation_nodes.jsonl` **append-only 單一權威**，不得引入 update／rewrite 路徑。
  （已驗證 `_persist_result` 只 append `fresh_records`，既有節點列永不被重寫 ⇒
  overlay 帶入的 `lifecycle_state` 不會被寫回節點檔。）
- `NON_PROJECTED_LIFECYCLE_STATES`、`_normalize_lifecycle_state` 缺省 → `active` 不得改動；
  既有測試 `test_04_superseded_not_projected`／`test_05_missing_lifecycle_state_treated_as_active`
  ／`test_T3_superseded_readonly` 必須維持綠。
- SOUL-BUG-009 的錨點政策（`_select_anchor_representative`）與 SOUL-BUG-001 的
  `VALUE-DEDUP-A-1`／`INV-2` 不得更動。
- `elevation_adapter.py` 外層 `except` 的 `exc_info=True`（SOUL-BUG-009 修的靜默回傳 [] 缺陷）不得移除。
- `elevation_recall.py:162-164` 的 `_store_path()` 是**測試專用**路徑計算（只算路徑不開檔），
  **不是**讀取端點——v1 誤列，回報須明說。

---

## 八、回報格式

- 改動檔案清單（逐檔一行，標明新增／修改）。
- 測試結果：跑了哪些、幾筆過幾筆、有無失敗；**變異實驗 N/N 全紅的逐條對照**。
- `elevation_nodes.jsonl` 實作前後的 **sha256 與檔案大小對照**。
- **8 個端點逐條回報**：哪些靠收斂點自動生效、哪些改了、明說 #7 #8 為何不需要改。
- 是否踩到 Frozen Contract、有無意外行為。
- **誠實揭露**：哪些是實測、哪些是推估、哪些無法確定。

---

## 相關

- 索引：`logs/SOUL-BUG-INDEX.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`
- 演算法原始碼：`soul-elevation/src/soul_elevation/models.py:176`、`engine.py:444-446`、
  `engine.py:1295-1377`、`prior.py:62-76`（**repo 外**）

---

## 附錄：v1 → v2 變更對照

| # | v1 的說法 | v2 的裁決 | 推翻依據 |
|---|---|---|---|
| 1 | 全庫共 **4** 條路徑讀節點檔 | 實際 **8** 處觸及，其中 **5** 個消費端 + **1** 個資料源 + **2** 個不需改 | 全庫 `grep load_elevation_nodes\|NODES_FILENAME`，主大腦逐行複核 |
| 2 | 逐條路徑各自套 overlay | **單一收斂點**（`load_elevation_nodes` 內部） | 散落守門＝SOUL-BUG-009 同款病根；結構上防漏 |
| 3 | `elevation_recall.py:162-164` 是讀取端點 | 是**測試專用** `_store_path()`，只算路徑不開檔 | 讀取該處原始碼確認 |
| 4 | `elevation_adapter.py:462` 是節點載入點 | 是 `_load_existing_dedup_keys`，只取三元組去重鍵，**不消費 lifecycle** | 讀取 `:445-490` 確認 |
| 5 | `src/world/elevation_adapter.py:56` 需確認是否要改 | 只 import 常數未使用（死碼），**不需改** | 全檔 grep 確認無其他用法 |
| 6 | `_EH2_SENTINEL` 寫入 `Optional[datetime]` 屬型別違規 → 改 `datetime.max` | **前提錯誤，不改** | `models.py:176` 明文 `Optional[str]`；全庫消費端皆 `is None` |
| 7 | 漏列 `io/gateway.py:285` 為唯一對外洩漏口 | **提升為必須動手的一項** | `:289-308` 全程未讀 `lifecycle_state` |