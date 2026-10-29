# 工單：TENSION-OPEN-IDEMPOTENT-WRITE-GUARD-1

## 目標
讓張力開立在**寫入端**自守冪等：同一 `tension_id` 在**未閉環／已開立**狀態下，**不得再產生第二筆 `tension_opened` 事件**。
（Owner 2026-10-29 裁定三：「維持靜態記錄，禁止生產重開同名 Tension」；
「若 Tension 處於未閉環或已開立狀態，不得重複生成相同 ID 之事件」。）

## 背景（主大腦已實測，勿重猜）
### 生產證據
`data/inner_life/epistemic_tensions.jsonl`（append-only 事件檔，5 筆事件、3 個 distinct tension_id）中，
**同一 `(agent_id, tension_id)` 有兩筆 `tension_opened` 事件**：
- `agent_rem` `relational_tension:6bfe56006414294a` ×2
  （ts 1792631245.15 = 10-21 21:07:25 EDT；ts 1792788307.74 = 10-23 16:45:07 EDT）
- `agent_yua` `relational_tension:4b0084b9a7d4ddf8` ×2
  （ts 1793027625.07 = 10-26 11:13:45 EDT；ts 1793183534.08 = 10-28 06:32:14 EDT）
兩筆的 `kind` 都是 `tension_opened`、`status` 都是 `open`、`source` 都是 `temporal_phenomenology`、
`subject` 都是 `bryan`、`bond.eligible=True`／`basis='P2_relational_band'`。
**期間沒有任何 `tension_resolved` 事件** ⇒ 第二筆不是「重開一條已消解的張力」，
而是**同一條仍開立的張力被再開一次**。

### 機制現況（已讀碼確認）
1. `src/inner_life/epistemic_action.py::run_background_action()`（約 1368-1385 行）：
   ```python
   tensions = list(load_open_tensions(agent_id, tension_store_dir))
   fresh = collect_tensions(...)
   known = {t.tension_id for t in tensions}
   for tension in fresh:
       if tension.tension_id in known:
           continue
       ...
       upsert_open_tension(tension, tension_store_dir)
   ```
   ⇒ **讀端去重**存在。`collect_tensions()` 通道 2（relational）的 `tension_id`
   ＝ `_tension_id(agent_id, TENSION_KIND_RELATIONAL, "bryan")` ＝ **固定雜湊**
   ⇒ 同一 agent 的 relational tension **永遠同一個 id**（這是設計上的冪等鍵）。
2. `upsert_open_tension()`（約 908-915 行）**無條件追加**一筆 `tension_opened` 事件：
   ```python
   event = _make_tension_event(TENSION_EVENT_OPENED, tension, "open")
   return _append_tension_events(store_dir, [event])
   ```
   ⇒ **寫入端完全不自守**；模組 docstring 明寫「append-only 下同 id 會有多筆事件，
   但 fold 的結果等價」—— 那是**讀取語意**的等價，**不是**「不會重複開立」的保證。
3. 🔴 **沙盒重現實驗（主大腦實測）**：在 tmp store 上模擬同一 agent 連續兩次
   `run_background_action` 的去重步驟（第二次的 `created_at` 改為 2000.0、
   `last_interaction_ts` 改為 1900）⇒ **去重生效**（`fresh_id in known` = True）、
   最終檔案只有 1 筆事件。
   ⇒ **矛盾**：讀端去重在沙盒有效，**生產上卻發生了重複開立**。
   ⇒ ⚠️ 主大腦**未能**在時限內定位這個矛盾的完整成因（已排除：服務重啟清空記憶體、
   legacy 遷移折算、檔案重建、`save_tension_records` 為寫入者、
   排程器直寫、sort order 亂序）。**執行者不必再追根因** ——
   Owner 裁示要的是**結果**（不得重複生成相同 ID 之事件），故本工單採
   **寫入端自守**（讓結果不再依賴讀端是否讀到）。
4. 🔴 **不得**因為「不知道根因」而放寬任何判準或放棄守門；寫入端自守是
   **更嚴**的方向（原本可能寫 2 筆，現在最多 1 筆）。

## 範圍（只動這些檔案）
- `src/inner_life/epistemic_action.py`（主體：`upsert_open_tension` 與其守門 helper）
- `tests/inner_life/test_epistemic_action.py` 或 `tests/inner_life/test_atomic_append.py`
  （新增守衛；依 repo 既有慣例選檔）
- **不得**動 `src/llm/proxy.py`、`src/inner_life/epistemic_tension_scheduler.py`
  （那兩處只是**呼叫端**，機制修在寫入端）

## 做法（決策已定，執行者照做）

### 1. 寫入端自守（核心）
在 `upsert_open_tension()` 追加事件**之前**，先判斷「該 `tension_id` 是否已經處於
**未閉合／已開立**狀態」：
- 判斷依據＝**同一份承載檔**（`store_dir` 解析出的路徑）的 fold 結果
  （`_read_tension_events(store_dir)` → `_fold_tension_events(...)`），
  取該 `tension_id` 的**最後一筆**事件：
  - 若其 `status` ∈ {`open`, `partially_resolved`} ⇒ **已開立** ⇒ **不得再追加** `opened` 事件
    ⇒ **跳過寫入並回傳 `True`**（**冪等成功**，不是錯誤）。
  - 若其 `status` == `resolved` ⇒ 已消解 ⇒ 允許重新開立（維持既有語意）。
  - 若查無該 id ⇒ 首次開立 ⇒ 正常追加。
- 🔴 **不得**改變 `TENSION_EVENT_KINDS`／`TENSION_STATUSES` 這兩個封閉集合。
- 🔴 **不得**改變 fold 語意（最後一筆勝出）。
- 🔴 **不得**改變 `mark_tension_resolved()`／`record_thought_enqueued()` 的行為。

### 2. 冪等性必須對**同一次呼叫內的批次**也成立
`_append_tension_events()` 可一次送多筆事件（`save_tension_records` 路徑）。
⇒ 守門邏輯要能處理「同一批內含多筆同 id 事件」⇒ 批次內也要去重
（**先 fold 既有 ＋ 批次內累積**）。

### 3. 必須保留 fail-silent 與既有回傳契約
- `upsert_open_tension()` 仍回 `bool`（True ＝ 冪等成功或寫入成功；False ＝ 寫入失敗）。
- 讀取端（`load_open_tensions`／`load_tension_records`／`_fold_tension_events`）**語意不變**。

### 4. 不得改變既有「append-only 不刪除既有事件」的性質
⇒ 已存在的 5 筆歷史事件（含那 4 筆重複開立）**保留不動**
（Owner 裁定三＝「維持靜態記錄」）。**不得**去重現有檔案、**不得**改寫 `data/`。

## 驗收（完成的定義）
- [ ] `upsert_open_tension()` 在該 id 已 open/partially_resolved 時**不再追加**事件
      （可觀測：追加前後檔案筆數不變；回傳 True）。
- [ ] 該 id 已 resolved 時**仍可**重新開立（維持既有語意）。
- [ ] 首次開立**仍會**追加（既有行為不回歸）。
- [ ] 批次內同 id 多筆**也只寫一筆**。
- [ ] **守衛（每條都做變異實測，證明會轉紅）**：
  - **G1 寫入端冪等**：對同一 id 連續呼叫兩次 `upsert_open_tension` ⇒ 檔內 `tension_opened`
    事件**只有 1 筆**（改前是 2 筆 ⇒ 轉紅）。
  - **G2 resolved 後可重開**：先 `mark_tension_resolved` 再 `upsert_open_tension` ⇒ 可再寫一筆
    `opened`（證明沒有把「已開立」判成「永不開立」）。
  - **G3 批次內去重**：同一批送兩筆同 id ⇒ 只寫一筆。
  - **G4 去重是寫入端而非讀端**：構造「讀取端看不到（模擬讀取失敗／空檔）」但
    **寫入端已自守**的情境 ⇒ 仍不得寫入第二筆
    （可用 monkeypatch `load_open_tensions` 回傳空清單來證明：
    **寫入端自守不依賴讀端**）。🔴 這條是本工單的**核心價值守衛**。
  - **G5 既有語意不回歸**：`load_open_tensions`／`load_tension_records`／`_fold_tension_events`
    的既有守衛**全部仍綠**（不得因本次改動而放寬讀取語意）。
- [ ] `.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_epistemic_action.py tests/inner_life/test_atomic_append.py`
      全綠。
- [ ] 相鄰守衛：`.venv\Scripts\python.exe -m pytest -q tests/inner_life/` 全綠。
- [ ] 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（**必須**用 repo 內 `.venv`；
      **不可**用 PATH 上的全域 pytest）。

## 測試
- 所有測試**只寫 tmp 目錄**（`tmp_path`），**絕不碰生產 `data\`**。
- 變異實驗必須**不得**觸達生產：凡「移除某道閘門」型變異，必須讓被解除防護的程式碼
  **不可能真的執行到生產**（用 pytest `pytest_runtest_call` hook 在 call 階段直接 `raise`，
  或把目標路徑指向不可路由位址）。🔴 **不得**只靠「它應該會失敗」的預測。
- 🔴 **絕對不得**啟動伺服器、綁定 `:8000`／`:8765`／`:8766`／`:8767`、對其發真實請求。
- 🔴 **絕對不得**殺任何行程（不得 Stop-Process／taskkill／pkill／task_stop）。
- 🔴 **不得**跑 `scripts/manual_*.py`。

## 不做（Out of Scope）
- **不得**改寫或去重 `data/inner_life/epistemic_tensions.jsonl` 的既有 5 筆事件
  （Owner 裁定三＝「維持靜態記錄」）。
- **不得**修改 `tension_id` 的生成方式（`_tension_id` 的雜湊輸入）—— 那是既有的冪等鍵。
- **不得**改動 `src/llm/proxy.py` 的節流閘（`_claim_background_action_slot`）。
- **不得**改動 `collect_tensions()` 的產生條件（哪些時刻算「有張力」）。
- **不得**放寬任何既有守衛（若某條既有守衛因本次改動轉紅，**回報**，
  不得自行改它；主大腦另行裁示）。
- **不得**動衰減門檻、AGENCY 4 stages、TriggerEnvelope、InnerLifeEvent、4 handlers、
  SAGE 寫入邏輯等 Frozen Contract。

## Frozen Contract 注意
- `src/inner_life/epistemic_action.py` 的**事件欄位封閉形狀**（`TENSION_EVENT_FIELDS`）、
  **事件型別封閉集合**（`TENSION_EVENT_KINDS`）、**狀態封閉集合**（`TENSION_STATUSES`）
  ：**不得**擴充或改動。
- **不得**改變 append-only 承載的原子追加原語（`src.fsio.atomic_append.append_bytes`）
  與其併寫守衛（`tests/inner_life/test_atomic_append.py` 守衛 A／B 必須仍綠）。

## 回報格式
1. 改動檔案清單（逐檔一句話說明改什麼）
2. G1～G5 逐條：**守衛全綠 output 摘要** ＋ **變異實測的轉紅 output**（改什麼 ⇒ 具體轉紅證據）
3. 相鄰守衛與全庫回歸結果（**必須聲明解譯器為 repo 內 `.venv`**、
   collected / passed / failed / skipped）
4. 是否踩到 Frozen Contract（逐項確認）
5. 🔴 **明確聲明**：本工單**未**觸碰 `data/` 下任何檔案、**未**改寫既有 5 筆歷史事件、
   **未**重啟服務、**未**殺任何行程、**未** commit/push。
6. 🔴 **誠實聲明**：主大腦未能定位「讀端去重在沙盒有效但生產仍重複」的完整成因
   （已排除：服務重啟、legacy 遷移、檔案重建、`save_tension_records` 為寫入者、
   排程器直寫、事件順序亂序）。若你在實作過程中**發現了根因**，請在回報中明確指出
   （那是比本工單更高的價值）；若沒發現，也請明說「未找到根因，已依裁定改為寫入端自守」。
