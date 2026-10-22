# 工單：LIFECYCLE-REINFORCE-CHAIN — 補上「靈魂型節點的跨日支持」鏈路（Owner 2026-10-22 裁示：立即開票）

- **開立依據**：Owner（Bryan）2026-10-22 17:2x EDT 裁示 —— 今晚那筆 belief 弱化定性為**機制缺口**、照實標紅、**不擋**；並**立即開票**補 REINFORCE 鏈。
- **上游工單**：`logs/LIFECYCLE-REINFORCE-GAP-WORK-ORDER.md`（根因定案與量測證據）
- **狀態**：本工單為**決策完整**工單（執行者可直接開工，不需再做設計決策）。**尚未實作。**
- **量測口徑**：一切數字以 repo 的 `.venv\Scripts\python.exe` 為準；`data/**` 唯讀；沙盒實驗只寫自建 temp 目錄。

---

## 1. 目標

讓**既有** value／belief／trait 節點在其命題**仍被新的生活重新確認**時，能累積到新的支持邊並刷新衰減錨點——也就是**把 `soul_elevation.engine.reinforce()` 這條引擎早就寫好、卻從沒有人呼叫的路徑接上生產**。

一句話：**「失去支持才是退場理由」目前無法表達；這張票讓它可以被表達。**

---

## 2. 範圍

| 檔案 | 動作 |
|---|---|
| `src/inner_life/elevation_adapter.py` | **主要落點**：新增「支持傳遞（Support Propagation）」步驟 |
| `tests/inner_life/test_lifecycle_reinforce_propagation.py` | **新增**守衛測試檔 |
| `tests/test_observability_completeness_1.py` | `allowed` 清單 **+1 檔**（新增 collectable 測試檔必須先 `git add`，否則 `tests/infra/test_repo_hygiene_tracked_tests.py` 會紅） |
| `logs/ENGINEERING_STATE.md` | 完工後登錄 §0.1.39（**完工時**才寫；寫入需依 §0.1.38 已取得的 Owner 授權） |
| `soul-elevation` 套件（`C:\Users\bbfcc\.local\bin\soul-elevation\`） | **不動**（`engine.reinforce()` 語意已完整且正確；只需呼叫） |

---

## 3. 做法（決策已定，執行者照做）

### 3.0 第 0 步（先量測，再動手）—— **已於 2026-10-22 17:3x EDT 完成**

**因為**：修復是否值得、能救回多少節點，取決於「各靈魂節點的祖先 pattern 在其建立之後是否真的還有新邊」。

**量測（唯讀，`.venv`；基準：節點 709、邊 1560）**：

| 指標 | 實測 |
|---|---|
| 靈魂型節點總數 | **112**（value 103／belief 4／trait 5） |
| **祖先 pattern 在該節點建立之後有新支持邊者** | **15 / 112（13.4%）** |
| 祖先 pattern 數 = 0（無 parent 鏈） | **0**（全部節點都有可追溯的祖先鏈） |
| 祖先存在但無新邊 | 97 |
| 有祖先新邊者的型別分佈 | **value 15／belief 0／trait 0** |
| 錨點位移（跨日） | **9 顆**（另 6 顆為同日，不影響） |

**逐節點（15 筆全列於量測輸出）**：9 顆 2026-10-20 建檔的 value 各有 **3 條**祖先新邊（最後一條 `2026-10-22T12:00:xx+00:00`，即**今天晨槽**）；6 顆 2026-10-22 建檔的 value 各有 **1 條**。分佈在 9 隻生產 agent（ruka 2／akane 1／anna 2／aoi 2／mahiru 1／mai 1／miku 2／ram 2／rem 2）。

**⇒ 分岔判定：比例 **13.4% ≫ 0**，且逐節點有具體邊證據 ⇒ 依 §3.1～3.4 **實作**（不停止）。**

#### 🔴 已被這次量測定案的限制（執行者與 Owner 都必須知道）

1. **本機制只對 `value` 型有效，對 `belief`／`trait` 型目前無效**（belief 0/4、trait 0/5 沒有祖先新邊）。
   ⇒ **今晚那顆 belief `57392ccdb3dd411e846f352e19d22e76`（2026-10-15 建檔）即使本票實作完畢，仍會在今晚弱化** —— 這與 Owner 裁示甲「不擋今晚那筆」一致，但**不得**被當成「修好就沒事」。
   ⇒ **「belief／trait 要能保活」需要另一條機制**（語意召回方向），而該方向的召回通道目前 **VERDICT = NO-GO**（池 0 顆、命中率 0/200 = 0.0000 < 5%）⇒ **不得**在本票裡偷偷改用召回通道來補 belief；那是另一張票、另一個裁示。
2. **本機制會把弱化波「往後推」，不會消除它**：9 顆 value 的衰減錨點會從「出生時刻」移到「最後一次重新確認時刻」（本例跨到 2026-10-22）⇒ 弱化時點由 `created+7d` 延後到 `last_confirm+7d`。⇒ 這**正是**正確語意（重新被確認的命題不該退場），但**不構成「弱化包裝會歸零」的保證**。
3. **不得**把「13.4%」寫成任何門檻或期望值常數 —— 每日重算（內容重複率會變）。

#### 量測工具（可重跑）

`%TEMP%\night_obs\reinforce_effect_probe.py`（唯讀；只讀 `data/elevation/elevation_nodes.jsonl` ＋ `elevation_edges.jsonl`；沿 `parent_node_id` 鏈回溯 ≤4 層，取祖先 pattern 在靈魂節點 `created_ts` 之後的有效支持邊）。

### 3.1 匹配規則（**決策已定，不得由執行者另選**）

> **新經歷要能重新支持既有 value／belief／trait，其唯一證據來源是：該靈魂節點的祖先 pattern（`parent_node_id` 鏈）在靈魂節點建立之後，又累積到了新的支持邊。**
>
> 而該新邊的產生條件，是**既有的 content-hash 合併機制**（SOUL-BUG-001）：同一命題（normalize 後 `content_hash` 相同）被**另一個獨立事件**重複產生 ⇒ 節點合併 ＋ 邊重綁 ⇒ pattern 的支持邊 +1。

**為什麼不做語意相似比對（這條是硬決策）**：語意召回通道（方向 A／`elevation_recall.py`／`recall_readiness_cli.py`）目前 **VERDICT = NO-GO**（可回憶池 **0** 顆、命中率 **0/200 = 0.0000** < 5% 門檻）。⇒ **不得**把生產的靈魂保活掛在一個已判定 NO-GO 的通道上。

**為什麼不做 content_hash 完全相同比對找靈魂節點**：那會把「pattern 層的重新確認」誤當成「靈魂層的重新確認」——靈魂節點的 content 在昇華時已被改寫（`76add6b`／SOUL-BUG-009 修的就是 `pids[0]` 恆取最舊 pattern 導致 content 凍結），兩層的 content 不保證同源。⇒ 只能走 **`parent_node_id` 鏈**。

### 3.2 落點與執行順序

在 `src/inner_life/elevation_adapter.py::elevate_matured_patterns()` 的既有流程中，**於 `engine.consume()` 與 `engine.elevate()` 之後、`_persist_result()` 之前**，插入「支持傳遞」步驟：

1. 取得本次新增邊：`engine._edges[baseline_edges:]`（`baseline_edges` 為既有變數）。
2. 取得既有靈魂節點：`engine._nodes` 中該 agent 的 `node_type ∈ {value, belief, trait}`。
3. 對每條新增邊 `e`：
   - 若 `e.node_id` **不是** pattern 型 ⇒ 跳過。
   - 沿 `parent_node_id` 鏈（**最多 4 層**，超出即停）從 `e.node_id` 向上找所有祖先靈魂節點（`node_type ∈ {value, belief, trait}`）。
   - 對每個找到的祖先靈魂節點 `t`，**若 `e.valid_from_ts > t.created_ts`**（邊必須發生在靈魂節點建立之後）⇒ 呼叫：
     ```python
     engine.reinforce(
         t.node_id,
         source_id=e.source_id,
         ts=e.valid_from_ts,            # ← 用「重新確認事件」的時間，不是傳遞執行的當下
         source_type=e.source_type,
         inner_life_event_id=e.inner_life_event_id,
         trigger_type="support_propagation",
     )
     ```
4. **去重**：以 `(target.node_id, e.source_id, e.inner_life_event_id)` 為鍵，同一批次內不得重複傳遞同一事件。
5. **隔離**：`engine.reinforce()` 對 `SUPERSEDED`（終態）節點會拋 `ValueError` ⇒ 必須以 `try/except` 逐節點包住，失敗只 `logger.warning` 並**繼續處理其餘節點**；單一節點失敗**不得**中斷整批。
6. **持久化**：`reinforce()` 追加的邊落在 `engine._edges` 內 ⇒ 會被既有的 `_persist_result(...)`（寫 `engine.evidence_edges[baseline_edges:]`）帶到 `elevation_edges.jsonl`。
   ⚠️ **必須驗證它真的落盤**：`_dedup_edges` 的鍵是 `(node_id, source_id, inner_life_event_id)`；傳遞出的邊其 `source_id` 與 `inner_life_event_id` 來自**新事件**，與既有昇華複製邊的鍵**不重複** ⇒ 應可落盤。**但這是推測，必由守衛 T6 實測證明，不得靠推論。**

### 3.3 引擎語意照用，不新造狀態機

`engine.reinforce()` 的既有語意（`soul-elevation/src/soul_elevation/engine.py:1047-1101`）：
- `ACTIVE→ACTIVE`：原地提升 confidence/stability，**不換 node_id、不改 lineage、不產生新因果節點**
- `WEAKENING→ACTIVE`／`DORMANT→ACTIVE`：重新激活
- `SUPERSEDED`：終態，拋 `ValueError`（v1 不自動復活）
- 新支持邊追加、**`last_support_ts` 更新＝衰減錨點刷新**（docstring 原話：`old ≠ outdated`）

⇒ **本工單不新增、不修改任何狀態機語意**；只負責「什麼時候該呼叫它」。

### 3.4 可觀測性（這是本票的驗收核心之一）

每次傳遞必須寫出結構化 log（既有 logger `soul_os.inner_life.elevation_adapter`），逐筆含：
`target_node_id`／`target_type`／`target_state_before`／`target_state_after`／`propagated_edge_count`／`source_event_id`／`source_ts`／`ancestor_pattern_id`／`ancestor_chain_depth`。

**並必須在報告中區分兩種弱化**：
- **「從未被重新支持而弱化」**（真的失去支持）
- **「曾被重新支持、之後又弱化」**（⇒ 需要看它後來為什麼沒再被確認）

---

## 4. 驗收（完成的定義）

1. **機制生效（決定性）**：沙盒實驗中，一顆 value 節點在其祖先 pattern 被重新確認後，**必須**新增 ≥1 條支持邊，且 `last_support_ts` 刷新到**重新確認事件的時間戳**（不是傳遞執行的當下）。
2. **衰減錨點跟著走**：同一顆節點在傳遞後，7 天門檻的判定必須以**刷新後的 `last_support_ts`** 為基準 ⇒ 它**不應**在「出生 + 7 天」弱化。
3. **守衛 T1～T6 全綠**（見 §5），且**雙向變異實驗 V1～V5 每一條都轉紅**。
4. **全庫回歸綠**（`.venv\Scripts\python.exe -m pytest -q tests`）。
5. **沙盒 CLI 快跑對帳**：以正確時鐘（tz-aware）跑 `scripts/eval_lifecycle_cli.py`，確認 (a) `eval_error` 空、`undelivered = 0`、(b) 回報「今晚槽的 belief 轉變數」**修復前後的差值**。
   ⚠️ **這是量測項，不是門檻**：修復後該 belief 是否還會弱化，取決於 §3.0 的量測結果（它的祖先 pattern 在 10-15 之後有無新邊）。**不得**為了讓某個數字好看而調整門檻或製造數據。
6. **Frozen Contract 零改動**：`Agency 4 stages`／`TriggerEnvelope`／`InnerLifeEvent`／`4 handlers`／`SAGE 寫入邏輯` 一律不動。
7. **生產零寫入**：驗收全程只寫 temp 沙盒；`data/**` 零改動（以 sha256／bytes／行數前後對帳證明）。

---

## 5. 測試（守衛 + 雙向變異實驗）

新增 `tests/inner_life/test_lifecycle_reinforce_propagation.py`：

| # | 守衛 | 內容 | 撤掉什麼會讓它轉紅 |
|---|---|---|---|
| **T1** | 傳遞生效（決定性） | 構造 pattern（content X）被 2 個獨立事件產生 → 昇華成 value；第 3 個事件再產生同一 content X ⇒ **該 value 必須新增 ≥1 條支持邊** | 移除傳遞步驟 |
| **T2** | 錨點語意 | 傳遞後的 `last_support_ts` 必須 == **重新確認事件**的時間戳，**≠** 傳遞執行當下 | 把 `ts=e.valid_from_ts` 改成 `ts=None` |
| **T3** | 隔離 | 一個 `SUPERSEDED` 的祖先 ⇒ 該次傳遞被隔離（warning），**其餘節點仍完成傳遞** | 移除 try/except 隔離 |
| **T4** | 去重 | 同一事件重複傳遞（引擎重跑）⇒ 支持邊**不得重複**（`(node_id, source_id, inner_life_event_id)` 唯一） | 移除去重 |
| **T5** | 不越界 | 祖先 pattern **沒有**新邊的靈魂節點 ⇒ **不得**被動（不得憑空生成支持邊） | 把傳遞套用到所有靈魂節點 |
| **T6** | 真的落盤 | 傳遞出的邊**必須**出現在 `elevation_edges.jsonl`（`node_id` = 靈魂節點、`source_id` = 新事件、`valid_from_ts` = 重新確認時刻），**不得**被 `_dedup_edges` 吃掉 | 讓傳遞邊使用與既有昇華複製邊相同的 key |

**雙向變異實驗（每條必須轉紅，且變異後檔案以 sha256 驗證 byte-identical 還原）**：

| 變異 | 撤掉什麼 | 應轉紅 |
|---|---|---|
| **V1** | 「支持傳遞」步驟 | T1、T2 |
| **V2** | `ts=e.valid_from_ts` → `ts=None` | T2 |
| **V3** | `SUPERSEDED` 的 try/except 隔離 | T3 |
| **V4** | 去重 | T4 |
| **V5** | 「祖先有新邊才傳遞」的條件 | T5 |
| **V6** | 傳遞邊的 `inner_life_event_id`（令其與既有複製邊同 key） | T6 |

**護欄**：既有守衛「靈魂型節點跨日支持邊 = 0」是**量測事實**、**不得**被寫成「必須 = 0」的常數門檻；修復後它應變成 > 0。新守衛必須寫成**機制不變量**（祖先有新邊 ⇒ 該節點必有新邊），不得寫死筆數。

---

## 6. 不做（Out of Scope）

- **不動** `soul-elevation` 套件任何檔案（`engine.py`／`prior.py`／`types.py`）。
- **不動**生命週期門檻（`LIFECYCLE_T_WEAKEN_DAYS = 7`／`LIFECYCLE_GRACE_DAYS = 3`／`LIFECYCLE_T_DORMANT_DAYS = 30`）。
- **不動**弱化語意與投影（`emergent_projection.py` 的 `WEAKENING_TONE_PREFIX = "我隐约觉得"`）。
- **不動**召回／語意比對通道（方向 A、B）。
- **不動** Frozen Contract（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯）。
- **不動** `data/**`（唯讀）；**不寫**任何生產檔案。
- **不刪除／不抑制**任何既有的弱化事件：既有 sidecar 的 207 筆 `lifecycle_transition` 是歷史事實，不因本票而回溯改寫。
- **不催熟**：不得為了製造「重新確認」而調整排程間隔／喚醒週期／注入 synthetic 事件。
- **不做**重啟（本票不涉及生產行為變更上線；若後續需要重啟，另行請 Owner 授權）。

---

## 7. Frozen Contract 注意

- `engine.reinforce()` 屬於引擎既有轉換，**非** Frozen Contract；本票只**呼叫**它，**不改**它。
- 本票的改動落在 `src/inner_life/elevation_adapter.py`（受治理區域）⇒ 必須獨立工單、獨立審查、有守衛、有變異實驗 ⇒ **不可**與其他改動混在同一個 commit。
- 提交前必問：「任一中間狀態會不會紅？」——若 T1～T6 中任一依賴另一個尚未提交的改動，兩者必須同一個 commit。
- **worker 不得自行 `git add`／`commit`／`push`**；由主大腦統一提交。

---

## 8. 回報格式

1. **§3.0 量測結果已於開票時完成**（見 §3.0：15/112 ＝ 13.4%，其中 value 15／belief 0／trait 0）⇒ **本票按 §3.1～3.4 實作，不停止**。回報時請引用該結果並**重跑一次** `%TEMP%\night_obs\reinforce_effect_probe.py` 確認基準未漂移（節點／邊數會長）。
2. 改動檔案清單（逐檔說明改了什麼）。
3. 測試結果：T1～T6 逐項狀態；**V1～V6 變異實驗逐條的轉紅結果**（含「撤掉什麼 → 哪個守衛轉紅」）；還原後 sha256 驗證。
4. 全庫回歸結果（**須寫明解譯器是 repo 的 `.venv`**）。
5. 沙盒 CLI 快跑對帳輸出（含 `eval_error`／`undelivered`／`VERDICT` 與修復前後的轉變數差值）。
6. **是否踩到 Frozen Contract／是否有意外行為**（若無，明寫「無」）。
7. 🔴 **必須明確回報**：(a) 傳遞生效的節點數與型別分佈（預期以 value 為主）；(b) **belief／trait 型是否仍無祖先新邊**（若仍為 0，必須明寫「belief 保活需要另一條機制，本票未解決」）；(c) 弱化包裝數的**方向性變化**（預期下降或持平，**不得**以任何方式干預生產來達成）。

---

## 9. 實作結果（2026-10-22 19:0x EDT；Owner 裁示「立即開工」後當輪完成）

### 9.1 改動檔案

| 檔案 | 改動 |
|---|---|
| `src/inner_life/elevation_adapter.py` | 新增 `_propagate_support_to_soul_nodes()` ＋ `_PROPAGATION_TARGET_TYPES`（belief/value/trait，**不含 essence**）＋ `_PROPAGATION_MAX_DEPTH=4`；在 `elevate_matured_patterns()` 的候選維度迴圈**之後**、`_persist_result()`**之前**呼叫。資料源是**磁碟邊**（`_rebuild_engine_for` 不載入靈魂節點自己的邊，記憶體裡沒有冪等判準所需的完整視圖）。 |
| `tests/inner_life/test_lifecycle_reinforce_propagation.py` | **新增**：T1～T6 ＋ 1 條機制不變量，共 7 項。 |

### 9.2 守衛與雙向變異實驗

- 基線 **7 passed**。
- 變異實驗 **6/6 轉紅**，變異後檔案以 sha256 驗證 **byte-identical 還原**：

| 變異 | 撤掉什麼 | 轉紅 |
|---|---|---|
| V1 | 傳遞步驟 | T1/T2/T4/T5/T6 |
| V2 | `ts=e.get("valid_from_ts")` → `ts=None` | T2 |
| V3 | 逐節點 try/except 隔離 | T3 |
| V4 | 去重鍵比對 | T4 |
| V5 | 「建立之後」時間條件 | T5 |
| V6 | 傳遞邊與既有複製邊同鍵（被 `_dedup_edges` 吃掉） | T6 |

- 🔴 **V3 第一次是綠的 —— 變異實驗抓到我自己的測試缺陷**：T3 裡我手動給了 OK 節點一條 `src3` 邊，讓「有沒有拿到 src3」這個斷言**自我滿足**，跟傳遞有沒有跑完全無關。**修法是修斷言**（移除那條預置邊），不是放寬守衛、也不是改機制。這條記在這裡，因為它是「守衛必須自我驗證」的活教材。
- 🔴 **T4 補了雙觀測點**：磁碟上的邊去重（`_dedup_edges`）本來就擋得住重複落盤 ⇒「磁碟只有 1 條」證明不了「傳遞邏輯冪等」。所以 T4 另外釘住 **trace 事件數**（`node_revised / reinforced_only`）：第二次跑**不得**再做一次傳遞工作。V4 就是被這條打紅的。

### 9.3 效果量級（生產圖譜的沙盒副本，**生產 data/ 全程唯讀**）

| 指標 | 實測 |
|---|---|
| 實際發生的 `support_propagation` 邊 | **33 筆** |
| 涵蓋的靈魂節點 | **15 顆**（與 §3.0 預測的 15/112 **完全一致**） |
| 型別 | **value 33 筆／belief 0／trait 0** |
| 錨點被刷新 | 15 顆（未變 97 顆） |
| 弱化時點位移 | **9 顆由 2026-10-27 延後到 2026-10-29（+2 天）**；另 6 顆錨點也刷新但落在同一天 ⇒ 弱化槽不變 |

### 9.4 ⚠️ 誠實的限制（必須連同效果數字一起回報）

1. **本機制恢復的是「機制」，不是那批已經不再被產出的命題。** 2026-10-24 那批 10 顆 legacy value（含關鍵反例 `6a92c906b245`）**完全不受影響** —— 它們的祖先 pattern 自 2026-10-17 起沒有任何新邊 ⇒ 沒有可傳遞的證據。⇒ 那批弱化是**上游內容生成**問題（那些命題已經不再被重複產生），**不是**傳遞鏈能救的。
2. **belief／trait 型仍然沒有任何傳遞**（祖先無新邊）⇒ 今晚那顆 belief 照樣弱化。**「belief 保活」是另一張票。**
3. **機制會複合**：每輪 `elevate_matured_patterns()` 都會撈「尚未傳遞過的祖先新邊」⇒ 命題被重新確認的頻率越高，被保活的靈魂節點越多。今天的數字（15 顆 / 33 邊）只是**當期快照**，不得寫成門檻。
4. **錨點刷新靠的是「邊」不是「節點欄位」**：`elevation_nodes.jsonl` 是 append-only、`_persist_result` 不回寫既有節點欄位 ⇒ `reinforce()` 對記憶體節點的 `last_support_ts`／`confidence` 變更**不會**落盤；但引擎的衰減錨點優先序是「有效支持邊最大 valid_from_ts → last_support_ts → created_ts+grace」⇒ **新邊即足以刷新錨點**，不依賴欄位回寫。`reinforce()` 另發的 `node_state_changed` 是 **trace** 事件（寫 `elevation_trace.jsonl`），**不是** lifecycle sidecar ⇒ 本機制**不改變**生命週期 overlay 的讀側語意。
