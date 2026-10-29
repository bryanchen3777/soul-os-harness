# 工單：LEDGER-REFRESH-TYPE-NEUTRAL-1

## 目標
把 `scripts/ledger_refresh.py` 的「四條件結構性成員」量測從**型別綁死在 value** 解耦為**型別中立**，消除「missing 0」量測盲區 —— 使刷新器的量測口徑與系統代謝的實際形狀一致（belief／trait／essence 型同樣會被推導、會被要求入帳）。

## 背景（主大腦已實測確認，勿重猜）
1. **盲區本體**：`scripts/ledger_refresh.py::structural_members()` 第 2 個條件寫死
   `if n.get("node_type") != "value": continue` ⇒ 只看得到 value 型缺口。
2. **同源盲區在 CLI 側也有一份**：`scripts/eval_lifecycle_cli.py::expected_retirement_sets_by_cohort()`
   第 ~1333 行同樣寫死 `node.get("node_type") != "value"` ⇒ 推導集合也只看 value。
   ⇒ 兩支工具**同源同盲**（生成器與判準器都看不到 trait/belief），所以缺口永遠不會被自動發現。
3. **實測數據（2026-10-29 主大腦實測）**：全庫每個 cohort 的「approved 但推導不出」成員型別分佈：
   ```
   2026-10-20: value 9, trait 4
   2026-10-21: value 6 →（入帳 4 顆 belief/trait 後）belief 3, trait 1
   2026-10-22: value 6 ／ 2026-10-23: value 4 ／ 2026-10-24: value 5
   2026-10-25: value 4 ／ 2026-10-26: value 5 ／ 2026-10-27: value 5 ／ 2026-10-28: value 3
   合計：value 41、trait 5、belief 3
   ```
   ⇒ 非 value 型別的缺口確實存在，且被兩支工具一致地漏掉。
4. **後果（已實測）**：`2026-10-21` cohort 今晚實際退役了 4 顆靈魂型節點
   （3 belief ＋ 1 trait，Owner 2026-10-28 已核准入帳），但刷新器回報「missing 0」
   ⇒ **假綠**。同理 `2026-10-20` 的 4 顆 trait 缺口也從未被自動發現。
5. **CLI 側已具備型別中立能力**：`EXPECTED_RETIREMENT_COHORTS` 的條目支援 `"types"`
   欄位（`approved_node_types()` / `_recorded_node_type()`），2026-10-20 cohort 已有
   4 顆 trait 的先例。⇒ **本工單只須讓刷新器追平 CLI 既有能力，不新增任何台帳語意。**

## 範圍（只動這些檔案）
- `scripts/ledger_refresh.py`（主體）
- `scripts/eval_lifecycle_cli.py`（**僅限** `expected_retirement_sets_by_cohort()` 的型別條件；
  台帳資料本身**不得**改動，除非第 5 步的守衛要求）
- `tests/test_ledger_refresh*.py`（新增或既有測試檔；名稱依 repo 慣例）
- `tests/test_eval_lifecycle_cli.py`（**僅在**守衛要求時調整，且必須保留原斷言意圖）

## 做法（決策已定，執行者照做）

### 1. 刷新器：型別中立化 `structural_members()`
- 把 `node_type != "value"` 的硬寫，改為**以台帳記載型別為準**的比對：
  - 型別來源＝台帳 `EXPECTED_RETIREMENT_COHORTS` 的 `"types"` 欄位；
    未記載者沿用既有向後相容預設 `"value"`（與 `approved_node_types()` 完全同源）。
- 🔴 **不得**放寬任何其他條件（建立日、last_support_ts==created_ts、無 support_propagation 祖先邊
  三條**原封不動**）。
- 🔴 **不得**自行核准任何節點入帳：型別中立化只影響「量測得到哪些缺口」，
  **入帳仍必須由主大腦依實測核准**（CLI 端 `EXPECTED_RETIREMENT_COHORTS` 的寫入權
  仍只在主大腦手上）。
- 建議做法：直接 import / 複用 `scripts/eval_lifecycle_cli.py` 的 `approved_node_types()`
  與 `expected_retirement_sets_by_cohort()`，**不要**另寫一份推導（避免再次兩份口徑漂移）。
  若因 CLI 是 `__main__` 腳本而難以 import，用 `importlib.util.spec_from_file_location`
  載入（repo 既有做法見 `tests/` 內對 CLI 的載入慣例）。

### 2. CLI：推導層型別中立化
- `expected_retirement_sets_by_cohort()` 的條件①由 `node_type != "value"` 改為
  「節點型別 == 該 cohort 台帳記載的型別」（沿用既有 `_recorded_node_type()` helper）。
- 🔴 **自我否決不變量必須保持成立**：`derived ⊆ approved`（推導不得超出核准）。
  改完後逐 cohort 實測此不變量，extra 必須為 0。
- 🔴 **不得**改動 `is_expected_metabolism()` 的放行條件（那是台帳放行的核心，動它＝放寬判準）。

### 3. 「expected 集合」與「推導集合」的角色必須分開（既有設計，守住）
- `expected` 的資格來源＝**台帳核准清單**（`approved_retirement_ids()`），**不是**推導集合。
- 推導集合只作「規則的自我描述」（證明規則沒被悄悄放寬）。
- ⇒ 型別中立化後，`derived` 會變大（多了 trait/belief）⇒ 那是**正確方向**
  （讓缺口可見），但**不等於**這些節點已被核准。

### 4. 必須產出「型別分佈」而不是只報總數
- 刷新器每個 cohort 的缺口回報，必須**逐型別**列出（value / belief / trait / essence 的筆數），
  否則「missing 0」這種假綠會再回來。
- 回報格式沿用既有 `print` 風格（見 `structural members of %s = %d` 一帶）。

### 5. 守衛（雙向變異測試，每條變異必須轉紅）
必寫下列守衛（守衛必須有牙 ⇒ 逐條做變異實測，證明它會轉紅）：
- **G1 刷新器型別中立**：構造一個 `node_type="belief"`（或 trait）且滿足其餘三條件的節點
  ⇒ `structural_members()` **必須**把它算進缺口（改前會漏 ⇒ 轉紅）。
- **G2 CLI 推導型別中立**：同構造，`expected_retirement_sets_by_cohort()` 必須把它算進 derived。
- **G3 自我否決不變量**：對**每個** cohort 斷言 `derived ⊆ approved`（extra == 0）。
- **G4 未放寬防呆**：構造一個「型別對但其餘條件不成立」的節點（例如 last_support_ts != created_ts，
  或有 support_propagation 祖先邊）⇒ **不得**被算進缺口（證明沒有為了修盲區而放寬其他條件）。
- **G5 台帳不得被工具自行改寫**：跑一次刷新器（`--dry-run` 與非 dry-run 都要測）⇒
  `scripts/eval_lifecycle_cli.py` 的 `EXPECTED_RETIREMENT_COHORTS` **不得**被自動新增節點
  （入帳必須是人類／主大腦的動作）。

### 6. 既有守衛 `test_closed_cohort_is_fully_reconciled_with_the_graph` 的處置（**需 Owner 裁示，見下**）
- 實測（主大腦 2026-10-29）：該守衛**在本工單之前就已經是紅的**（`git stash` 實測：
  還原到 `c5f4c4e`、`963364d`、`17ffee2`、`963364d~1` 皆同樣失敗）。
- 根因：台帳對封閉 cohort **只增不減**（`ledger_refresh.py` 註解明寫
  "the approved set may legitimately contain members the structural set no longer shows
  (supported-then-aged) => **only add, never shrink**"），
  而該守衛斷言 `set(derived) == set(approved)`（**等號**）⇒
  只要有成員「被背書過才老化」（supported-then-aged），approved 會**永久大於** derived ⇒ 假紅。
- 實測證據：2026-10-25 cohort 有 4 顆 value（`64325480…`／`36a215fc…`／`c9e4661a…`／`c4373ede…`）
  現已**帶有 support_propagation 祖先邊**（條件④不成立）⇒ 被推導層排除，但仍在台帳內。
- ⇒ **本工單不得自行放寬或刪除該守衛的等號斷言**（那會是「為了讓數字好看而動門檻」）。
  正確處置是**提請裁示**，由主大腦／Owner 決定把等號改成「`derived ⊆ approved`」
  （允許 approved ⊋ derived，因台帳刻意保留 supported-then-aged 成員）
  或其他方案。**執行者在本次工單中只做：寫一個新守衛把這個「approved ⊋ derived 的差額」
  明確量測出來（允許 approved ⊋ derived，但要求差額**可被解釋**），並在報告中說明**，
  不要修改既有等號斷言。**

## 驗收（完成的定義）
- [ ] `structural_members()` 對 belief／trait／essence 型也會回報缺口（型別中立）。
- [ ] CLI `expected_retirement_sets_by_cohort()` 型別中立，且**逐 cohort** `derived ⊆ approved`
      （extra == 0）—— 主大腦實測：本次改動前 2026-10-21 為 extra=0，改動後仍必須為 0。
- [ ] 刷新器每 cohort 的缺口回報**逐型別**列筆數。
- [ ] G1～G5 五條守衛全綠，且**每條都做過變異實測並確認會轉紅**（貼出變異前後輸出）。
- [ ] 既有守衛 `test_closed_cohort_is_fully_reconciled_with_the_graph` **未被修改**；
      其紅燈在報告中列為「工單前既有、待 Owner 裁示」。
- [ ] `.venv\Scripts\python.exe -m pytest -q tests/test_eval_lifecycle_cli.py tests/test_ledger_refresh*.py`
      全綠（既有紅燈除外，且須在報告中明確標示）。
- [ ] 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（解譯器必須是 repo 內的 `.venv`，
      **不可**用 PATH 上的全域 pytest）。

## 測試
- 新增守衛檔（依 repo 命名慣例）。
- 變異實驗必須**不得**觸達生產：所有 mutation 在**影子樹**（複製到 `%TEMP%`）上做，
  或在 pytest 內以 `pytest_runtest_call` hook 於 call 階段硬攔。
  🔴 **絕對不得**讓被解除防護的程式碼真的執行到生產 `:8000`。
- 🔴 **絕對不得**啟動伺服器、綁定 `:8000`／`:8765`／`:8766`／`:8767`、對其發真實請求。
- 🔴 **絕對不得**寫入／改名／刪除 `data\` 下任何檔案（測試用 tmp 目錄）。
- 🔴 **絕對不得**殺任何行程（不得 Stop-Process／taskkill／pkill／task_stop）。

## 不做（Out of Scope）
- **不得**自行把任何節點加進 `EXPECTED_RETIREMENT_COHORTS`（入帳是主大腦／Owner 的動作）。
- **不得**改動 `is_expected_metabolism()`、`classify_soul_retirements()` 的放行邏輯。
- **不得**改動衰減門檻（7 天／21 天／任何 decay 常數）。
- **不得**改動既有守衛 `test_closed_cohort_is_fully_reconciled_with_the_graph` 的等號斷言。
- **不得**碰 `scripts/manual_*.py`（其中一支被收集就會打真實 LLM API）。
- **不得**改動 `src/` 下的引擎邏輯（本工單只動量測／判定層）。

## Frozen Contract 注意
- `Agency 4 stages`／`TriggerEnvelope`／`InnerLifeEvent`／`4 handlers`／`SAGE 寫入邏輯`：
  **本工單完全不碰**。
- 生命週期的三態判定（PASS／PENDING_COHORT／FAIL）**不碰**。
- `EXPECTED_RETIREMENT_COHORTS` 的**資料內容**不碰（只可能因守衛需要而加註解）。

## 回報格式
1. 改動檔案清單（逐檔一句話說明改什麼）
2. 守衛結果：G1～G5 逐條「守衛全綠 output 摘要」＋「變異實測：改什麼 ⇒ 轉紅的具體 output」
3. 自我否決不變量逐 cohort 實測表（date / derived / approved / extra）
4. 全庫回歸結果（**必須聲明解譯器為 repo 內 `.venv`**、collected / passed / failed / skipped 數）
5. 是否踩到 Frozen Contract / 是否自報為通過但未經主大腦複驗
6. 🔴 **明確聲明**：既有守衛 `test_closed_cohort_is_fully_reconciled_with_the_graph` 的紅燈
   在本工單**之前**即存在（附 `git stash` 或等效實測證據），本工單**未修改**它。
