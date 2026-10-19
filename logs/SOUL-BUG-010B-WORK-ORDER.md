# SOUL-BUG-010-B：生命週期自動老化 ＋ night slot 串接（工單 v1，決策完備）

**狀態**：✅ **實作完成**（守衛 14 支全綠 ＋ 變異 5/5 全紅；全庫回歸見回報）
**日期**：2026-10-19｜**嚴重度**：P1（架構缺口）｜**登記者**：主大腦
**Owner 裁決（2026-10-19）**：核准開工；核准「靈魂節點 ＋ Pattern 候選」雙層評估；
核准 aoi value 節點追加為第 17 顆隔離（實作時連其逐位元相同的 pattern twin 一併
隔離為第 18 顆，理由見下）。
**前序**：SOUL-BUG-010 v2（`704f851` ＋ `89397d5`）已完成「讀取面收斂 ＋ 防線二」，
本票接手其**刻意延後**的兩件事：`evaluate_lifecycle_all` 與 night slot 接線。

---

## 目標

讓生命週期**真的會走**：`ACTIVE → WEAKENING → DORMANT` 由時間與支持度驅動，
並把轉變寫進 SOUL-BUG-010 已建好的 append-only sidecar（讀取面已就緒，只差寫入端）。

一句話：**讀取面已認得生命週期，但至今沒有任何東西會產生生命週期。**

---

## 🔴 前置研究的三個決定性發現（全部實測，2026-10-19 15:4x 生產圖譜 398 節點 / 903 邊）

### 發現 1：錨點語意是錯的 —— 直接接線會誤傷 10 顆 value 節點

| 錨點算法 | 轉 `weakening` | 其中 value（**prompt 可見**） | 其中 pattern（不可見） |
|---|---|---|---|
| **現況接線**（引擎讀 `last_support_ts`） | **191** | **10** ❌ | 181 |
| **依教義自原始邊檔計算** | **181** | **0** ✅ | 181 |

**根因**：`last_support_ts` 在**全庫沒有任何寫入者**（grep 只命中 docstring／docs／
一個「temporal_phenomenology 不得賦值 `last_support_ts`」的測試）。
生產實測 **398/398 節點的 `last_support_ts == created_ts`** ⇒ 該欄位建檔後從未更新。
而 `_rebuild_engine_for` **不載入靈魂節點自己的邊**（`elevation_adapter.py:898`），
⇒ value 節點在引擎裡沒有任何 active edge，錨點只能落到 `last_support_ts` = 建立時間。

**決定性反證**（`value` 節點 `6a92c906b245`，agent_mahiru，81 條邊全為 `valid_until_ts=None`）：

| valid_from_ts 日期 | 邊數 |
|---|---|
| 2026-10-11 | 22 |
| 2026-10-15 | 19 |
| 2026-10-16 | 20 |
| 2026-10-17 | 20 |

⇒ 這顆 value 節點**跨 4 天持續被重新支持**，最新支持距今 2.3 天，**它從未失去支持**。
現況接線卻要把它判成 weakening。

**語意後果**：`weakening` 節點投影時會被加上不確定語氣前綴
（`emergent_projection.py:268-269` → `我隐约觉得「…」`）。
10 顆 value 節點 = **每隻 agent 2~3 顆 value 中的 1 顆**（約 40%），
會在接線當晚集體被套上「我隱約覺得」—— **而且理由是假的**。
這直接違反 `prior.py:62` 的信條：「**old ≠ outdated——年齡不是退場理由，失去支持才是**」。
現況接線把它變成了字面意義的「old = outdated」。

### 發現 2：`dormant` 目前不可達

- 節點年齡分佈：**max 15.74 天／median 6.74 天／min 0.03 天**；**≥7 天 191 顆、≥30 天 0 顆**。
- ⇒ 30 天門檻還要再等 **約 14 天**才可能觸及。
- ⇒ **防線二（`dormant` 排除）在本票上線後仍會處於靜止狀態**，
  唯一會生效的是本輪已寫入的 16 顆隔離節點。**不可把防線二的生效時間預設為本票落地日。**

### 發現 3：成本遠比原工單想像的低

- 全量掃描（11 個 agent、398 節點、903 邊）耗時 **0.066 秒**。
- ⇒ 原 SOUL-BUG-010 工單「`_lifecycle_anchor` 為 O(N×E)，圖譜成長後是實質成本」的判斷
  **在現況規模下不成立**（10 倍成長約 7 秒／天，仍可忽略）。
  ⇒ 每日 22:00 單次接線在成本上完全不是障礙，**不必**為了省成本而改成增量或抽樣。

### 發現 4（釐清，不是缺陷）：181 顆 pattern 的 weakening 是**正確**的

- 181 顆 weakening pattern **全部仍有 live edge**（沒有任何一顆是「被消耗後無邊」）。
- ⇒ 它們是**真的 7 天以上沒有新支持** ⇒ weakening 語意正確。
- ⇒ pattern 不進 prompt（`PROJECTABLE_NODE_TYPES` 只含 belief/value/trait/essence），
  且 `weakening` 不影響 `min_evidence`（防線二只排除 `dormant`）。
  ⇒ **正確接線後，181 顆狀態變更對 prompt 的可見影響為零。**

---

## 架構裁定（決策已定，執行者照做）

### 裁定 1：錨點改由**原始邊檔**計算，**在注入引擎之前**修正 `last_support_ts`

- **不得**直接呼叫 `engine.evaluate_lifecycle()` 就完事 —— 那是發現 1 的錯誤接線。
- **正解**：評估前先掃 `elevation_edges.jsonl`，為每個節點算出「仍有效支持邊的最大
  `valid_from_ts`」，把它**注入**要交給引擎的 `ElevationNode.last_support_ts`，
  引擎自身的 `_lifecycle_anchor` 便會走優先序 2（`last_support_ts`）而得到**正確**錨點。
- **好處**：
  - 不動 `soul_elevation` 任何一行（repo 外可選依賴）；
  - 不需要節點檔的 update 路徑（維持 Append-Only 紅線）；
  - 生命週期狀態機仍**全權歸引擎**（`evaluate_lifecycle` 決定逐級轉移、essence 豁免、
    superseded 終態、trace 事件），repo 只負責**提供正確的錨點輸入**。
- **邊界**：注入的 `last_support_ts` **只存在於記憶體中的 `ElevationNode` 物件**，
  **不得**寫回 `elevation_nodes.jsonl`（那是 append-only 權威檔）。
  節點檔裡的 `last_support_ts` 欄位維持原狀（= created_ts），屬既有 legacy 資料。

### 裁定 2：轉變寫入 `elevation_lifecycle_events.jsonl`（SOUL-BUG-010 已建好）

- 該 sidecar 與其 overlay 讀取面**已於 `704f851` 上線並實測生效**（16 顆隔離已驗證）。
- 本票只需**新增寫入端**：每次評估把轉變以
  `{ts, node_id, event, lifecycle_state, lifecycle_state_before, lifecycle_state_after, anchor_ts, reason}`
  append 進去。
  🔴 **`lifecycle_state` 是 overlay 實際讀取的欄位**（`lifecycle_overlay._reduce_events`
  只認這個 key）—— 只寫 `before`／`after` 會讓整批事件被**靜默忽略**，
  狀態永遠不生效。實作時正是這個坑被守衛 G3 抓出來。
- **絕不**改寫 `elevation_nodes.jsonl`。
- 同一 `node_id` 多筆事件取檔案順序最後一筆（append-only ⇒ 檔案順序即時間序），
  這已是 `lifecycle_overlay._reduce_events` 的既有語意，本票不得改動。

### 裁定 3：接線點 —— night slot（22:00），每日一次

- 掛在 `scripts/run_server.py` 的 night slot 區塊，與既有 diary/dream 寫入同槽。
  （SOUL-BUG-012 已修正該槽的 `write_event` → `submit` 順序，勿破壞。）
- **不得**掛 30 秒 scheduler 分支（門檻是 7／30 天，30 秒重算無意義；成本雖低但語意無意義）。
- 必須包在既有 fire-and-forget 的失敗隔離風格內（`except Exception` → warning ＋ `exc_info=True`），
  **絕不讓生命週期評估中斷任何既有主路徑**。

### 裁定 4：評估範圍 = 全節點（belief/value/trait/essence/pattern），但 essence 由引擎豁免

- 引擎自身已豁免 essence（`engine.py:1342-1343`），repo 不得另作一套。
- pattern 必須納入：防線二需要 pattern 能走到 `dormant` 才有意義。
- 維護者注意：`docs/ELEVATION-LIFECYCLE.md` 把生命週期描述為「durable soul structure」的機制，
  而 pattern 在 `engine.py:105-107` 被定義為「consolidation 輸出（候選，非靈魂結構）」——
  **本票刻意採「兩者都評估」**，理由：防線二的可執行性優於文件措辭的純度。
  這個取捨已由 Owner 在本裁決中拍板。

---

## 做法（範圍明確，不得自行擴張）

### 步驟 1：`src/inner_life/elevation_adapter.py` 新增 `evaluate_lifecycle_all()`

- 簽章：`evaluate_lifecycle_all(*, store_dir=None, now_ts=None) -> list[dict]`
- 流程：
  1. 載入節點（**經 `load_elevation_nodes`**，即已套 overlay）與全部邊（`_load_edges`）。
  2. 掃邊檔算出每節點的「仍有效支持邊最大 `valid_from_ts`」⇒ 產生
     `anchor_by_node: dict[node_id, str]`。
  3. 依 `agent_id` 分組，逐組 `_rebuild_engine_for(...)` 建引擎，
     **在建立後、建引擎前**把 `anchor_by_node` 的值注入對應 `ElevationNode.last_support_ts`
     （引擎已建好的節點要用 `dataclasses.replace()` 換回 `engine._nodes`，
     因為 `ElevationNode` 是 frozen dataclass —— **不可用屬性賦值**，
     這正是 SOUL-BUG-010 挖出的 frozen 地雷）。
  4. 呼叫 `engine.evaluate_lifecycle(now_ts=now_ts)` 取回轉變清單。
  5. 把轉變轉成事件 dict，**append 進 `store_dir / LIFECYCLE_EVENTS_FILENAME`**
     （沿用既有 `_append_jsonl`，**不新造寫入層**）。
  6. 回傳事件清單。
- **失敗隔離**：整體包 `try/except Exception` → `logger.warning(..., exc_info=True)`，
  **回傳 `[]` 絕不 raise**（不得阻斷 consume 主路徑）。
- **不做去重**：同一 `node_id` 每天最多一筆轉變（引擎本身逐級、一次一級），
  重複寫入無害且符合 append-only 審計性。

### 步驟 2：night slot 接線

> ⚠️ **實作時修正（2026-10-19）**：v1 初稿寫的是「掛在 `scripts/run_server.py` 的 night slot」。
> 實作時改為**掛在 `src/soul/scheduler.py` 主迴圈的 night slot additive 分支**
> （`_fire_lifecycle_eval()`），理由有三：
>   1. `run_server.py` 的 night 路徑是 **DiaryHandler 逐 agent 觸發**（10 隻 agent 各跑一次），
>      在那裡接線會讓評估**重複執行 10 次**；scheduler 是**單一呼叫點**。
>   2. 本檔既有同類型分支（C-2.1 週期敘事 `_fire_periodic_narrative`、
>      LIFE-THREAD-M5 生活線頭 `_fire_life_thread_slot`、
>      WORLD-EVAL-TIMER-1 `_fire_world_timer_eval`）**全部**是這個形狀：
>      30s wake 內、slot 檢查窗、0 新定時器 0 新通道、at-most-once 冪等鍵、fail-closed。
>   3. 30s tick 在 slot 窗內會重入數十次 ⇒ 冪等鍵（`_last_lifecycle_eval_date`）
>      必須放在**同一個物件**上才收斂得住。
> 語意完全等價（night slot、每日一次、fire-and-forget、失敗隔離），且更貼合本檔既有慣例。

- 新增 `SoulScheduler._fire_lifecycle_eval(now)`：
  - 只在 `_slot_for_time(now) == "night"` 觸發；
  - at-most-once 冪等鍵 `self._last_lifecycle_eval_date`（每日恰好一次）；
  - 呼叫 `evaluate_lifecycle_all(now_ts=<UTC ISO>)`；
  - **fail-closed**：整段 `try/except Exception` → `logger.warning`，不阻斷主迴圈。
- 在 `_run_loop()` 中以 `await self._fire_lifecycle_eval(now)` 並列呼叫
  （分支編號 5.8，與 5.6／5.7 同級）。
- 不得改動該槽既有的 `write_event` → `submit` 順序（SOUL-BUG-012 修復）。

### 步驟 3：守衛（`tests/inner_life/test_soul_bug_010b_lifecycle_eval.py`）

**不得**啟動伺服器、**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`、**不得**對生產發真實請求、
**不得**殺任何行程；`store_dir` 一律 `tmp_path`；**不得**寫 `data/**`。

必須覆蓋的維度（每條都要有對照組）：

| # | 守衛 | 對照組 |
|---|---|---|
| G1 | 錨點取自原始邊檔：value 節點有 active edge 時，錨點 = 該 edge 的 `valid_from_ts`，**不是** `created_ts` | 同一節點移除邊後錨點才落到 `created_ts + grace` |
| G2 | `last_support_ts` 注入**不寫回**節點檔（append-only 紅線） | 評估前後 `elevation_nodes.jsonl` 位元組相同 |
| G3 | 轉變事件 append 進 sidecar，且**多筆取最後一筆** | 連續兩次評估同一節點，overlay 讀到第二次的狀態 |
| G4 | 引擎豁免 essence：essence 永不轉 weakening | value 節點在同條件下**會**轉 |
| G5 | 7 天門檻：`weakening` 不影響 `min_evidence`（防線二只排除 dormant） | 同一維度下 dormant pattern 被排除、weakening pattern 仍能升格 |
| G6 | 失敗隔離：sidecar 寫入失敗時評估不 raise | 正常路徑回傳事件清單 |
| G7 | 逐級轉移：一次評估最多轉一級 | 單次評估後狀態不跳級 |

### 步驟 4：變異實驗（雙向證明）

至少 5 組，每組必須轉紅：

1. 把錨點改回讀 `last_support_ts`（= 現況錯誤接線）⇒ **G1 轉紅**。
2. 拿掉 `last_support_ts` 注入（引擎用節點檔原值）⇒ **G1 轉紅**。
3. 把注入改成直接屬性賦值（frozen dataclass）⇒ **拋例外**（這是 SOUL-BUG-010 的同款地雷，
   必須證明新程式碼不再踩）。
4. 拿掉 sidecar 寫入 ⇒ **G3 轉紅**。
5. 把 `weakening` 也排除出計票 ⇒ **G5 轉紅**。

---

## 驗收（完成的定義）

1. **節點檔位元組不變**：`elevation_nodes.jsonl` 評估前後 sha256 與大小逐位元相同。
2. **接線當晚的實際效果可預期且已驗算**：以**生產圖譜的 temp 副本**跑真實引擎
   （2026-10-19 實測，398 節點 / 904 邊）：

   | 項目 | 實測值 |
   |---|---|
   | 第一晚轉變總數 | **167**（全部 `active → weakening`） |
   | 其中 **pattern** | **167** |
   | 其中 **value/belief/trait/essence（prompt 可見）** | **0** ✅ |
   | `dormant` 轉變 | **0**（30 天門檻尚未觸及） |
   | 誤傷檢查：value `6a92c906b245`（81 條有效邊、持續被重新支持） | **無轉變** ✅ |
   | 耗時 | **0.070 s** |

   **數字對帳（可重現）**：依原始邊檔算，≥7 天的節點共 **181** 顆（全為 pattern）；
   其中 **14** 顆已被隔離為 `dormant` ⇒ 引擎對 `dormant` 不發轉移（既不匹配
   `active→weakening` 也不匹配 `weakening→dormant`）⇒ 181 − 14 = **167**。
   per-agent 分佈逐項吻合：akane 6／anna 17／aoi 17／mahiru 18／mai 18／miku 18／
   ram 18／rem 18／ruka 19／yua 18。

   ⇒ **prompt 可見影響為零**。這個數字必須由守衛 G1 重現機制，
   絕對數值則是當日資料的點測量（圖譜每日成長 ⇒ 會漂移）。
   ⚠️ 早期版本此處寫「181」，那是**隔離前**且**錨點未修正**時的量；
   現已更正為 **167**（隔離 14 顆後）。
3. **錯接線的破壞力被量化並釘住**：若日後有人退回到「直接呼叫引擎」
   （即發現 1 的錯誤接線），守衛必須轉紅 —— 因為那會誤傷 10 顆 value 節點。
4. **零迴歸**：全庫 `.venv\Scripts\python.exe -m pytest -q tests` 與實作前同數。

---

## 不做（Out of Scope）

- ❌ **不改** `soul_elevation` 任何一行。
- ❌ **不接** `record_contradiction`／`supersede`（需 NLI 矛盾偵測，另一張票）。
- ❌ **不整檔覆寫或重排** `elevation_nodes.jsonl`。
- ❌ **不維護**節點檔裡的 `last_support_ts` 欄位（維持 legacy 原狀；
  正確錨點在評估時於記憶體中算出）。
- ❌ **不設**任何入口限流門檻。
- ❌ **不碰** `personas/`、`configs/`、`data/`（`data/` 只讀；
  部署當日的 sidecar 寫入屬部署階段，需 Owner 當次明示授權）。

---

## Frozen Contract 注意

- `elevation_nodes.jsonl` append-only 單一權威。
- 讀取端過濾契約（`NON_PROJECTED_LIFECYCLE_STATES`、`_normalize_lifecycle_state`
  缺省→`active`）不得改動。
- `lifecycle_overlay` 的 sidecar 事件語意（最後一筆勝出、非法事件忽略）不得改動。
- `elevation_adapter.py` 外層 `except` 的 `exc_info=True` 不得移除。
- SOUL-BUG-009 錨點政策與 SOUL-BUG-001 的 `VALUE-DEDUP-A-1`／`INV-2` 不得更動。
- SOUL-BUG-012 的 `write_event` → `submit` 時序不得破壞。

---

## 回報格式

- 改動檔案清單（逐檔一行）。
- 守衛結果 + **變異實驗 N/N 全紅的逐條對照**。
- `elevation_nodes.jsonl` 評估前後 sha256 與大小對照。
- 接線當晚的**實際**轉變統計（節點數、by type、by agent），以及 prompt 可見影響的量化。
- 誠實揭露：哪些是實測、哪些是推估、哪些無法確定。

---

## 相關

- 索引：`logs/SOUL-BUG-INDEX.md`
- Canonical 狀態：`logs/ENGINEERING_STATE.md`（2026-10-19 條目 ④）
- 前序工單：`logs/SOUL-BUG-010-WORK-ORDER.md`（v2）
- 演算法原始碼（**repo 外**）：`soul-elevation/src/soul_elevation/engine.py:1295-1377`、
  `prior.py:62-76`
