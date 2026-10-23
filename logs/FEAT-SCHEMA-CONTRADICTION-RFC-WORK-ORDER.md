# 工單：FEAT-SCHEMA-CONTRADICTION — RFC 專票：矛盾探針的來源定義 ＋ 沙盒驗證（**不動生產**）

> **狀態**：決策完備，待派工（**RFC 階段：只定義 ＋ 沙盒量測，不落地**）
> **日期**：2026-10-22 22:20 EDT
> **Owner 裁決（2026-10-22 22:1x，逐字）**：「**矛盾資料面准予另立 RFC 專票 FEAT-SCHEMA-CONTRADICTION**，先於沙盒中明確定義矛盾探針來源與欄位形態，**嚴禁在未經審定的情況下直接向生產 JSONL 追加欄位**。」
> **上游**：`logs/CAMPAIGN-B-D1-D3-TENSION-DRIVER-WORK-ORDER.md`（戰役 B Phase 1，已合入 `1119ea5`）、`logs/ENGINEERING_STATE.md` §0.1.45（因子②恆 0 之發現）
> **本文件 §0 是主大腦的實測勘查；每個數字皆由唯讀量測反推。請先讀完 §0 再動手。**

---

## 0. 主大腦實測勘查（2026-10-22 22:15 EDT，唯讀）

### 0.1 🔴 裁示字面 vs 裁示目的：欄位**早已存在**，本票**零 schema 變更**

Owner 裁示預設「擴充生產 Schema 涉及資料契約變更」。**實測推翻這個前提**：

| 量測項 | 實測結果 |
|---|---|
| `elevation_nodes.jsonl` 欄位 | **793/793 節點皆含 `contradiction_pressure`**（節點總數已由 712 增至 793，槽內仍在長） |
| 欄位形態 | `contradiction_pressure: tuple = ()`（`soul_elevation/models.py:130`），元素為 `ContradictionRecord`（`models.py:85-89`）——**只留「來源引用＋時間」**（`source_id`／`ts`／`event_identity`／`provenance_ref`），**不複製證據正文** |
| 引擎 API | `engine.record_contradiction()`（`engine.py:990`）、`_count_independent_contradictions()`（`:195`）、`_contradiction_spread_days()`（`:215`）**全部早已存在且語意完整** |
| 生產實況 | `contradiction_pressure` **793/793 皆為 `[]`**（空陣列） |

⇒ **不需要新增任何欄位**。這**更**符合裁示目的（「資料契約變更」既然不必發生，就不碰資料契約）；**零生產 JSONL 變更**是本票的硬約束。

### 0.2 真正的缺口：**引擎內部的結構性死鎖**

- `record_contradiction()` 的**唯一**呼叫點是 `engine.supersede()`（`engine.py:1176-1184`）。
- 而 `supersede()` 的觸發門檻是「獨立矛盾證據數 ≥ `LIFECYCLE_N_SUPERSEDE`（其他層 3）／essence ≥ 5」**且**「矛盾證據跨 ≥ `LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`（2）個不同模擬日」**且** essence 額外 valence 反轉門檻（`engine.py:1155-1159`、`:1187-1203`）。
- ⇒ **矛盾要靠 `supersede()` 產生，而 `supersede()` 要靠矛盾才可能觸發** ⇒ 累積器永遠是 `[]` ⇒ `supersede()` 永遠拋 `ValueError`（`engine.py:1194-1198`）。
- **這與 `reinforce()` 的病完全同構**：`reinforce()` 引擎早有、語意完整、生产零呼叫者（§0.1.38／`elevation_adapter.py:1102-1105`），已於戰役 B 前作（LIFECYCLE-REINFORCE-CHAIN，`f584371`／`1119ea5`）接上。
- ⇒ **本票要回答的問題不是「欄位長什麼樣」，而是「矛盾從哪裡來、谁把它寫進去」。**

### 0.3 Phase 1 的因子②為何恆 0（承接 §0.1.45）

`CAMPAIGN-B-P1` 的因子②「未決矛盾」鎖定在 `data/soul/**/life_threads.jsonl` 的 `contradiction` 欄位 ⇒ 實測 **944 個 JSONL 無一含此欄位** ⇒ 恆 0（worker 照實回報，未借 SAGE 矛盾偵測頂替）。
⇒ 本票定義的探針若成立，**因子②應改接本票定義的探針**（屬 Phase 2，**本票不動已合入的 `tension_scoring.py`**）。

---

## 1. 目標

**在沙盒中**定義「矛盾探針」的來源與欄位形態，並以**生產圖譜唯讀**量出「依此判準會產生多少組候選矛盾」，產出一份可供 Owner 審定的 RFC 與審定包。

**一句話**：矛盾欄位與引擎 API 早已存在、只是永遠餓著；本票把「誰來餵它」這件事定義清楚並量出可行性，**但不落地任何生產變更**。

---

## 2. 做法（決策已定，執行者照做，**不需再做任何設計決策**）

### 2.1 探針規格（**deterministic、零 LLM、零新欄位**）

**輸入（全部唯讀）**：
- `data/elevation/elevation_nodes.jsonl`（靈魂型節點：`value`／`belief`／`trait`；`essence` 不參與 Phase 1 探針）
- `data/elevation/elevation_edges.jsonl`（既有支持邊：`node_id`／`source_id`／`source_type`／`valid_from_ts`／`valid_until_ts`／`agent_id`）
- `data/elevation/elevation_lifecycle_events.jsonl`（sidecar，只讀；**不**作為矛盾來源，只作生命週期狀態對照）

**候選矛盾判準（逐條照做，順序固定）**：

對每個 agent 的每個靈魂節點 `N`（`node_type ∈ {value, belief, trait}`、`lifecycle_state ≠ "superseded"`）：

1. 取 `N` **建立之後**到達的有效支持邊集合 `E(N)`：`edge.node_id == N.node_id` 且 `edge.valid_from_ts > N.created_ts` 且 `edge.valid_until_ts is None`。
2. 對每條 `e ∈ E(N)`，以 `e.source_id` 解析其**來源事件**，找出該來源在**同一 agent、同一 lineage 根**下產生的**另一個**靈魂節點 `N'`（`N' ≠ N`；`N'.parent_node_id` 與 `N.parent_node_id` 同鏈或同根）。
3. 若 `N'.content != N.content`（**逐位元不等**）⇒ 記為一組**候選矛盾** `(N, N', e)`。
4. `N'.content == N.content` ⇒ **不是**矛盾（是同命題再確認；已由 REINFORCE 鏈處理）⇒ 不記。

**欄位形態（沿用既有，零新增）**：
候選矛盾若要真的寫進引擎，欄位即 `ContradictionRecord`：
`source_id`（＝`e.source_id`）／`ts`（＝`e.valid_from_ts`，UTC ISO）／`event_identity`（＝`e.inner_life_event_id`，冪等去重鍵）／`provenance_ref`（＝`e.inner_life_event_id`）。
⇒ **本票不新增、不修改任何欄位定義**；`soul-elevation` 套件**唯讀**（外部 repo，不改）。

**計數與跨日判定（沿用引擎既有實作，不得另立）**：
- 獨立矛盾數 ⇒ `soul_elevation.engine._count_independent_contradictions(records)`
- 跨模擬日數 ⇒ `soul_elevation.engine._contradiction_spread_days(records)`
- 門檻 ⇒ `LIFECYCLE_N_SUPERSEDE`／`LIFECYCLE_ESSENCE_N_SUPERSEDE`／`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`（**不得新增、不得調低、不得調高**）

### 2.2 新增 `src/inner_life/contradiction_probe.py`（**沙盒原型；純唯讀**）

```
find_candidate_contradictions(nodes, edges, *, agent_id=None) -> list[CandidateContradiction]
```
- **純函式**：輸入為已載入的節點／邊序列，**不自己讀檔**（讀檔由呼叫端做，測試易隔離）。
- `CandidateContradiction`（frozen dataclass）：`node_id`／`counterpart_node_id`／`source_id`／`ts`／`event_identity`／`provenance_ref`／`agent_id`／`node_type`。
- **不得**呼叫 `engine.record_contradiction()`（本票只定義與量測，不寫引擎狀態）。
- **不得**讀 `data/`（由呼叫端餵資料）⇒ 測試完全不需要生產資料。
- **不得**引入任何權重／門檻常數。

### 2.3 新增 `docs/FEAT-SCHEMA-CONTRADICTION-RFC.md`（RFC 本體）

必含六節：
1. **問題陳述**：§0.2 的死鎖（矛盾要靠 supersede 產生、supersede 要靠矛盾）。
2. **探針來源規格**：§2.1 全段（判準、欄位形態、計數、門檻，**逐字沿用既有**）。
3. **資料契約影響評估**：**零欄位新增**；既有 `contradiction_pressure` 欄位形態不變；既有 JSONL **不追加任何欄位**；附上 §0.1 的 793/793 實測證據。
4. **沙盒量測結果**：依 §4.1 的必答題，含分子／分母／單位。
5. **Phase 2 落地提案（**本票不實作**）**：在 `elevation_adapter` 的候選迭代處，對每組候選矛盾呼叫 `engine.record_contradiction(...)`（冪等鍵＝`(node_id, source_id, event_identity)`）⇒ 累積器在 `supersede()` 之前被填滿 ⇒ **死鎖解除**；並列出該提案需要的 Owner 授權（動 `src/inner_life/**`、接引擎寫入路徑、以及後續的部署重啟）。
6. **風險與不做**：見 §5。

### 2.4 檔案清單（執行者擁有，**僅**下列 4 個）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `docs/FEAT-SCHEMA-CONTRADICTION-RFC.md` | 新增（RFC 本體） |
| 2 | `src/inner_life/contradiction_probe.py` | 新增（沙盒原型） |
| 3 | `tests/inner_life/test_contradiction_probe.py` | 新增（守衛 T1～T6） |
| 4 | `logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md` | 新增（實作紀錄 ＋ 沙盒量測數字） |

> **提醒**：新增 collectable 測試檔**必須先 `git add`**，否則 `tests/infra/test_repo_hygiene_tracked_tests.py` 會紅（由主大腦在合入時統一處理；執行者**不得** `git add`／`git commit`／`git push`）。
> `src/inner_life/contradiction_probe.py` 為新模組 ⇒ 需**逐票**更新 `tests/test_observability_completeness_1.py` 的 `allowed`（**不得**動 `frozen_dirs` 11 筆）——**該檔由主大腦在合入時統一更新**，本票不列入執行者檔案清單。

---

## 3. 沙盒量測（**必答題**；用生產圖譜唯讀跑原型，報告寫 §2.3 第 4 節）

| # | 必答題 | 交付格式 |
|---|---|---|
| Q1 | 依 §2.1 判準，生產圖譜上**會產生多少組候選矛盾**？ | 分子／分母（分母＝符合前提的 `(N, e)` 配對總數）／單位 |
| Q2 | 分佈：agent 分佈、node_type 分佈、`ts` 時間分佈（最早／最晚／跨度） | 逐項列 |
| Q3 | 若 Q1＝0 ⇒ 明確寫「0」並**說明成因**（是哪一條判準把所有配對濾掉） | **不得**為了讓數字非 0 而放寬判準 |
| Q4 | 依引擎既有計數器，這些候選若寫入累積器，會達到 `LIFECYCLE_N_SUPERSEDE`（3）／`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD`（2 天）門檻嗎？ | 逐節點列出「獨立矛盾數／跨日數」 |
| Q5 | 與既有 REINFORCE 傳遞邊（`trigger_type == "support_propagation"`，部署首跑 33 筆）是否重疊？ | 重疊組數／分母 |
| Q6 | `agent_akane` 單獨列出（Phase 1 的唯一標桿角色） | 同 Q1～Q4 格式 |

> **量測紀律**：筆數由 bytes／行數實測反推；每個數字寫分子／分母／單位；0/0 標「無定義」；過濾前先確認欄位型別與時區（`valid_from_ts`／`created_ts` 為 UTC ISO 字串，**字串比較可行但須同格式**，`created_ts` 帶微秒、`valid_from_ts` 帶微秒或無 ⇒ 必須先統一解析再比）。

---

## 4. 守衛與變異

### 4.1 守衛（`tests/inner_life/test_contradiction_probe.py`，T1～T6）

| # | 測試 | 斷言要點 |
|---|---|---|
| T1 | 判準正確 | 合成資料：同 agent、同 lineage 根、`N'.content != N.content` ⇒ 產出 1 組候選矛盾；`content` **逐位元相同** ⇒ **0 組** |
| T2 | 時間門檍 | 邊的 `valid_from_ts <= N.created_ts` ⇒ **不**算候選（建立之前的證據不算矛盾） |
| T3 | 欄位形態 | 候選的 `source_id`／`ts`／`event_identity`／`provenance_ref` 逐字等於來源邊的對應欄位（`ts` 取 `valid_from_ts`） |
| T4 | 純函式 | 同一輸入兩次呼叫結果**逐位元相等**；函式**不讀檔**（以「傳入不存在的路徑／不給路徑參數」證明其無 IO 依賴） |
| T5 | 零新常數 | AST 檢查：模組內**無**權重命名識別字、**無**浮點門檻常數、**無**自訂門檻整數（門檻一律**引用**引擎既有常數或由呼叫端傳入） |
| T6 | 不寫引擎狀態 | 呼叫原型前後，傳入的節點物件 `contradiction_pressure` **逐位元不變**（原型不得呼叫 `record_contradiction`） |

### 4.2 變異（**每條必須轉紅**；沒轉紅＝盲腸守衛，必須拔除）

| # | 變異 | 預期轉紅 |
|---|---|---|
| M1 | 放寬 `content` 相等判準（相同內容也記矛盾） | T1 |
| M2 | 移除 `valid_from_ts > created_ts` 條件 | T2 |
| M3 | 讓 `ts` 取 `created_ts` 而非 `valid_from_ts` | T3 |
| M4 | 讓原型在回傳前呼叫 `engine.record_contradiction()` | T6 |

> 變異 harness 放 `%TEMP%`（**不得**放 `tests/**`——R5 護欄禁 `subprocess.*`）；每次變異後以 sha256 驗證位元還原（patch 用 Python 寫 bytes，避免 PowerShell 5.1 BOM）。

---

## 5. 驗收（完成的定義）

1. T1～T6 全綠；全庫回歸 **0 failed**（`.venv\Scripts\python.exe -m pytest -q tests`，回報須註明解譯器與 collected 數）。
2. **M1～M4 全紅**且位元還原（sha256 證明）。
3. §3 的 **Q1～Q6 全部有答案**（含 Q3 的「0 也要寫成因」路徑）。
4. `docs/FEAT-SCHEMA-CONTRADICTION-RFC.md` 六節齊備，且**第 3 節明確寫「零欄位新增、既有 JSONL 不追加任何欄位」**並附 §0.1 的 793/793 證據。
5. `git diff --name-only` 恰好等於 §2.4 的 4 個檔案。
6. **`data/**` 位元恆等**：實作前後對 `data/` 全樹算 sha256 清單並比對 ⇒ 0 差異（本票**唯讀**生產；沙盒量測亦只讀）。
7. Frozen Contract 零觸碰（§6 逐項自證）。

---

## 6. 不做（Out of Scope）

- **不新增、不修改任何生產欄位**（`contradiction_pressure` 沿用；`life_threads` 的 `contradiction` 欄位**不新增**）。
- **不寫入 `data/**`**（唯讀；沙盒量測只讀生產）。
- **不呼叫 `engine.record_contradiction()`**、**不接任何引擎寫入路徑**（Phase 2 提案，本票只寫在 RFC 裡）。
- **不改** `src/inner_life/tension_scoring.py`、`src/inner_life/tension_closure_loop.py`、`src/inner_life/drive_profile.py`（戰役 B Phase 1 已合入，本票**凍結**它們）。
- **不改** `soul-elevation` 套件（外部 repo，**唯讀**；引用其常數與函式即可）。
- **不改** `src/inner_life/elevation_adapter.py`／`src/llm/proxy.py`／`src/soul/**`／`src/memory/sage/**`／`personas/**`／`configs/**`。
- **不部署、不重啟、不動行程、不觸發 synthetic 事件**。
- **不得 `git add`／`git commit`／`git push`**。
- 🔴 **嚴禁**為了讓 Q1 ≠ 0 而放寬任何判準或調低任何門檻。

---

## 7. Frozen Contract 注意

- Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯（`src/agency/*`、`src/memory/sage/writer.py` 等）**不得改動**。
- `tests/test_observability_completeness_1.py` 的 `frozen_dirs` **11 筆全保留**（`src/agency/stages.py`、`trigger_handler.py`、`event_handler.py`、`dream_handler.py`、`diary_handler.py`、`src/inner_life/submission_gate.py`、`src/social/identity_firewall.py`、`src/social/producer_gate.py`、`src/world/middleware.py`、`src/memory/sage/writer.py`、`src/soul/decision.py`）。
- `soul_elevation` 的 `ContradictionRecord` 欄位族、`LIFECYCLE_*` 門檻、`_count_independent_contradictions()`／`_contradiction_spread_days()` **只引用、不改寫**。
- 戰役 B Phase 1 的三個新模組 **本票凍結**。

---

## 8. 回報格式

1. 改動檔案清單（對照 §2.4）
2. 測試結果（指令、collected／passed／failed／耗時、**註明解譯器為 repo 的 `.venv`**）
3. 變異 M1～M4 的**紅燈訊息原文** ＋ 位元還原 sha256 證明
4. **Q1～Q6 的完整答案**（分子／分母／單位；Q3 若為 0 附成因）
5. RFC 六節的完成確認（第 3 節「零欄位新增」聲明逐字貼出）
6. Frozen Contract 自證（逐項）
7. `data/**` 位元恆等證明
8. 任何與本工單前提不符的實測（回報，不自行改設計）

---

## 9. 待 Owner 審定（**本票交付後才進入下一步**）

本票**只**交付「定義 ＋ 沙盒量測 ＋ 審定包」。**是否進入 Phase 2**（在 `elevation_adapter` 接 `engine.record_contradiction()` 以解開死鎖、並讓因子②改接本探針）**須 Owner 審定後另開工單**——本票**不得**夾帶任何生產接線。
