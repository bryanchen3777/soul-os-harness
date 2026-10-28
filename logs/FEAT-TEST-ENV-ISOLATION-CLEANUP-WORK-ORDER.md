## 工單：FEAT-TEST-ENV-ISOLATION-CLEANUP（第三棒）

- **開立者**：主大腦（Engineering Brain）
- **核准**：Owner（Bryan）2026-10-27「第三棒：大收官・即刻開工」
- **上游**：第一棒 FEAT-LIFECYCLE-CLI-TYPE-NEUTRAL（§0.1.97）、第二棒 BUG-EMOTION-IMPORT-SIDE-EFFECT（§0.1.98／§0.1.99）——兩棒皆已閉環
- **工單狀態**：開立（decision-complete，執行者照做即可，不需再做任何設計決策）

---

### 目標

把「測試環境隔離」從零散慣例收斂成**可執行的守門**，達成兩件事：

1. **全庫回歸的 skip 集合零漂移**——同一份程式碼、任意時間重跑，被 skip 的**測試 ID 集合逐筆相同**。
2. **從制度上封死 import 期環境變數寫入**（AST 守衛，未來任何模組都不得再犯）。

---

### 🔺 前提更正（主大腦實測，2026-10-27；Bry 原述與現況不符）

Owner 原述：「盤點全庫 **50+ 個**在 import 期直接寫入 `SOUL_OS_DATA_DIR` 的測試檔案」。

**主大腦 AST 盤點結果：全庫只剩 1 個**——`tests/soul/test_decay_worker_settlement.py:61`
（`import os as _os` ＋ `_os.environ["SOUL_OS_DATA_DIR"] = str(_SETTLEMENT_ROOT)`）。

- 其餘模組早已改用 `tests/conftest.py` 的 autouse fixture ＋ `iso_env(tmp_path, monkeypatch)`。
- ⚠️ **盤點第一次差一拍的教訓（必須寫進守衛）**：第一版盤點用 AST 抓 `os.environ`，**抓不到別名** `_os.environ`
  ⇒ 結論是「0 個」。修正別名解析後才得到「1 個」。**⇒ 守衛 G-A 必須能解析別名**，否則同一個盲點會再來一次。

**⇒ 主線目標改寫**：真正可量測、可驗收的東西是「**Skip 數零漂移**」，不是「50+ 檔的數量」。
本票以「skip 集合被基準快照鎖定 ＋ 三條 AST 守衛」達成此目標。

---

### 範圍（Ownership）

- **只能改 `tests/**`**。
- **嚴禁改** `src/**`、`scripts/**`、`configs/**`、`personas/**`、`data/**`、`logs/**`（`logs/` 屬主大腦）。
- **不得**殺任何行程（禁 `Stop-Process`／`taskkill`／`pkill`／`kill`）、**不得**重啟服務、**不得**寫入／改名／刪除 `data/**`。
- **不得** `git add`／`git commit`／`git push`；**絕對禁止 `git add .`**（只能指定路徑）。
- **不得**啟動伺服器、**不得**綁 `:8000`／`:8765`／`:8766`／`:8767`、**不得**對其發真實請求。
- 測試解譯器**一律**用 repo 的 `.venv\Scripts\python.exe`（引用任何測試數字須註明解譯器）。

---

### 做法（決策已定，執行者照做）

#### D1. 解除全庫唯一的 import 期 env 寫入者

**檔案**：`tests/soul/test_decay_worker_settlement.py`

1. **刪除**模組層級 `_os.environ["SOUL_OS_DATA_DIR"] = str(_SETTLEMENT_ROOT)`（L61）。
2. **刪除／搬移**模組層級的憑證清除迴圈（L63-65：移除 `OPENAI_`／`ANTHROPIC_`／`DEEPSEEK_`／`GEMINI_`／`TELEGRAM_`／`TG_` 前綴變數）——它同樣是**行程級、無還原**的副作用。
3. 改為**模組層級 autouse fixture**（`@pytest.fixture(autouse=True)`）處理隔離與憑證清除：
   - 用**自建的 `pytest.MonkeyPatch()` 實例**（**不可**宣告 `monkeypatch` 參數——`tests/conftest.py` 已記載：宣告 `monkeypatch` 參數會把它提前 setup／延後 teardown，造成既有測試由綠轉紅）。
   - 在 `finally` 內 `undo()` 還原。
4. **原「先釘根再 import `src.*`」的順序要求已失效**：`src/agent/emotion.py` 的 import 期副作用已在 BUG-EMOTION-IMPORT-SIDE-EFFECT 拔除（`DB_PATH` 惰性、`emotion_engine` 延遲建立、連線惰性）⇒ 模組層級不再需要 env 寫入。
   **但仍須保留**該檔既有的「不得在 import 期定型 `src.paths` 資料根快取」的斷言語意（見 D1.5）。
5. `test_isolation_root_is_disposable_not_production`（L170-208）的**斷言強度一律不得放寬**，仍須同時斷言：
   (a) 解析後的資料根 ≠ repo `data/`；(b) 解析後的資料根在**系統 tmp** 底下；(c) pytest 的 `tmp_path` 在系統 tmp 底下。
   原針對「模組層級釘根生效」的正向自證，改為**針對 conftest autouse 隔離生效**的正向自證——**改斷言對象，不改斷言強度**。
6. 檔頭 docstring 必須同步更新（說明「import 期寫入已移除，改由 fixture 隔離」，並註明第二棒已讓 `src/agent/emotion.py` 的 import 期相依失效）。

#### D2. 漂移候選 5 檔：統一改 env opt-in（預設空字串 ⇒ 固定 skip 理由）

**統一規則**：skip 判據屬「牆鐘時間／生產資料內容／外部資產存在性」三類者，一律改成 **env opt-in**。
**容量型 skip 維持既有慣例**（`SOUL_OS_LIVE_SERVICE_BASE_URL` 等既有 env 不動、不改語意）。

| 檔案 | 現況（實測） | 決策 |
|---|---|---|
| `tests/clients/test_voice_brain_actions.py` | 模組層 `skipif(not ANIMATION_ASSETS_BUNDLED)`（L36-62，以 `.mp4` 資產存在性為判據） | 改由 env **`SOUL_OS_CLIENT_AVATAR_DIR`**（指向 `.mp4` 資產目錄）供應能力；未設定 ⇒ skip，理由固定為 `未設定 SOUL_OS_CLIENT_AVATAR_DIR ⇒ 無客戶端視覺資產可對線`。**移除以檔案存在性為判據的 skipif。** |
| `tests/test_m1_5_night_400_baseline.py` | `skipTest("重啟後 400 數 < 5 …")`（L103-104，生產日誌內容）；另有 L115-116「4xx log not found」 | 該測試方法以 env **`SOUL_OS_NIGHT_400_BASELINE`** 閘門；未設定 ⇒ skip，理由固定為 `未設定 SOUL_OS_NIGHT_400_BASELINE ⇒ 生產夜槽 400 canary 未授權`。同檔同組 skip 點一併納入同一閘門。 |
| `tests/test_m1_5_night_400_fix.py` | L193／L205／L207／L214 多點 skip（生產日誌內容＋git 歷史） | 該測試方法以 env **`SOUL_OS_NIGHT_400_FIX_CHECK`** 閘門（同上理由格式）。**不得新增 `subprocess` 使用點**；既有 `subprocess` 呼叫維持原樣，**不得擴張 R5 白名單**。 |
| `tests/test_plan_a_launcher_v1.py` | L110-111 `if self.start_plan_a.exists(): skipTest("…已存在…")`（檔案存在性判據） | 改由 env **`SOUL_OS_PLAN_A_LAUNCHER_PATH`**（要探測的啟動腳本路徑）；未設定 ⇒ skip（理由固定）。設定後**移除存在性判斷**，直接以該路徑執行並斷言——語意不變（退役腳本必須不存在，且呼叫必失敗）。 |
| `tests/world/test_phase_b_world_event_id.py` | 圖譜內容相依（`future_source` 不在 `VALID_SOURCES`、未於 state 節點被 evaluated） | 改由 env **`SOUL_OS_PHASE_B_WORLD_LIVE`** 閘門（未設定 ⇒ skip，理由固定）。⚠️ **coverage 損失在此明文記帳**：「自造資料恆跑」的改造另開追蹤票，本票不做。 |

#### D3. 守衛（新增 `tests/infra/test_env_isolation_guard.py`）

- **G-A（import 期 env 寫入）**：`tests/**` 不得出現**模組層級**（import 期）的環境變數賦值／清除。
  偵測範圍：`os.environ[...] = ...`（`Assign`／`AnnAssign`）、`os.environ.setdefault/pop/popitem/clear/update(...)`，
  **且必須解析別名**（`import os as _os` → `_os.environ`；`from os import environ` → `environ`）。
  ⇒ 別名盲點是「前提差一拍」的根因，守衛抓不到就等於沒有。
- **G-B（volatile 判準驅逐）**：`tests/**` 的 `pytest.mark.skipif` 條件不得含
  ①時間函式（`time.time`／`time.localtime`／`time.gmtime`／`time.monotonic`、`datetime.now`／`datetime.utcnow`／`datetime.today`）
  ②檔案存在性檢查（`os.path.exists`、`Path.exists`／`is_file`／`is_dir`）。
  ⇒ 把「牆鐘／存在性」從 skipif 驅逐，斷言它們只該出現在 env 閘門後面。
- **G-C（skip 基準機制存在性）**：`tests/infra/skip_baseline.json` 存在且格式正確（`node_ids` **已排序**、**無時間戳欄位**、`count` 與清單長度一致）；`tests/conftest.py` 確實掛上比對 hook（AST 檢查 hook 函式存在）。
- 守衛必須**自證不是盲腸**：每條守衛都要有專屬變異實驗（見「測試」節）。**ABORT 與 GREEN 同等嚴重**——變異沒轉紅＝盲腸守衛＝本票失敗。

#### D4. skip 基準快照機制

1. 新增 `tests/infra/skip_baseline_lib.py`（**非** `test_*.py` ⇒ 不被 pytest 收集）：純函式
   `load_baseline()`／`save_baseline()`／`diff_skip_ids(baseline_ids, observed_ids)`／`collect_skip_ids(reports)`。
2. `tests/conftest.py` 新增 hook（`pytest_terminal_summary` 或 `pytest_sessionfinish`）：
   - 收集本次被 skip 的**測試 ID**（`terminalreporter.stats["skipped"]` 的 `report.nodeid`）。
     **只比 ID，不比理由**——理由含時間戳／絕對路徑，會造成假漂移。
   - **只在全庫回歸模式強制**：`config.args` 為空（裸 `pytest -q`）**或** env `SOUL_OS_ENFORCE_SKIP_BASELINE=1`。
     其餘（指定路徑／子集）**只印出觀察結果，不判紅**（否則任何子集跑都會假紅）。
   - 與基準**集合**比對；不同 ⇒ 印出 added／removed 清單，並讓整場 **exit 非 0**。
   - env `SOUL_OS_WRITE_SKIP_BASELINE=1` ⇒ 改寫基準檔（**僅限刻意更新時**）。
   - 基準檔內容**只放排序後的 ID 清單與 count，不得放時間戳**（避免每日 churn）。
3. 基準檔路徑：`tests/infra/skip_baseline.json`。
4. 匯入方式：`tests/conftest.py` 與 `tests/infra/test_skip_baseline_guard.py` 各自以
   `sys.path.append(str(Path(__file__).parent / "infra"))` 後 `import skip_baseline_lib`（**不要**依賴套件化）。

#### D5. 不得破壞既有守衛

- 既有守衛的**斷言一律不得放寬**。若某既有守衛因本票變更而轉紅 ⇒ **改守衛的基準**（保持同一語意），並在回報中逐條列出改了哪個基準、為什麼。
- `tests/test_repo_hygiene_tracked_tests.py` 會因「新增 collectable 測試檔尚未被 `git` 追蹤」而紅 ⇒ **那是預期中的**（主大腦會指定路徑 `git add`）。**不得自行 `git add`**。

---

### 驗收（完成的定義）

1. `tests/soul/test_decay_worker_settlement.py` 不再有模組層級 env 寫入（G-A 綠）；憑證清除改由 fixture 還原。
2. `tests/test_data_root_isolation.py::test_allow_real_data_root_marker_opts_out` **由 skip 轉為「執行且通過」**
   ⇒ 全庫 skip 數 27 → **26**、passed 7504 → **7505**（**以實測為準**，不得預設）。
3. D2 的 5 檔 skip 理由全部改為 env opt-in ⇒ skip 集合穩定。
4. **全庫回歸連續 ≥ 2 次**（禁跑窗後、01:00 EDT 之後，**由主大腦執行**）skip 的測試 ID 集合**逐筆相同**。
5. 三條新守衛各有**專屬變異實驗**且**精準轉紅**；變異 harness 全部在 `%TEMP%` 山影樹執行，**repo 檔案 sha256 前後相同**。
6. `git status` 只含 `tests/**`（主大腦另會有工作單／canonical 檔，不在本票範圍）。

---

### 測試

- **定向**（解譯器＝repo `.venv`）：
  ```
  .venv\Scripts\python.exe -m pytest -q \
    tests/soul/test_decay_worker_settlement.py tests/test_data_root_isolation.py \
    tests/clients/test_voice_brain_actions.py tests/test_m1_5_night_400_baseline.py \
    tests/test_m1_5_night_400_fix.py tests/test_plan_a_launcher_v1.py \
    tests/world/test_phase_b_world_event_id.py \
    tests/infra/test_env_isolation_guard.py tests/infra/test_skip_baseline_guard.py
  ```
- **變異實驗**（M1／M2／M3，各對應一條守衛）：全部在 `%TEMP%` 山影樹執行 ⇒ **repo 檔案全程不寫入**（前後 sha256 相同）。
  - **M1**：在 `tests/**` 某檔的**模組層級**加回 env 寫入（含**別名**形式）⇒ **G-A 轉紅**。
  - **M2**：把某 `pytest.mark.skipif` 條件改成 `Path(...).exists()` 或時間函式比較 ⇒ **G-B 轉紅**。
  - **M3**：破壞 `skip_baseline_lib.diff_skip_ids`（或改壞基準檔格式）⇒ **G-C／skip 基準守衛轉紅**。
- **禁止跑全庫回歸**：禁跑窗 22:00–01:00 EDT（22:00 夜槽 + 夜記生成期間）。全庫回歸由主大腦執行。
- **禁止**：啟動伺服器、綁生產埠、對生產發真實請求、殺行程。

---

### 不做（Out of Scope）

- `src/**`、`scripts/**`、`logs/**`、`configs/**`、`personas/**`、`data/**` 一律不碰。
- 不做「自造資料恆跑」改造（D2 第 5 列的 coverage 損失另開追蹤票）。
- 不改既有 live-probe opt-in env（`SOUL_OS_LIVE_SERVICE_BASE_URL`、`SOUL_OS_LIVE_PRIVATE_CHAT`、`M6_REAL_LLM`、`SOUL_TEST_REAL_LLM`、`SOULOS_ALLOW_LIVE_DSH_TESTS`）的語意。
- 不碰生命週期台帳／`scripts/eval_lifecycle_cli.py`／刷新器（另票）。
- 不重啟服務、不改排程、不動 `pytest.ini` 的 `testpaths`。

---

### Frozen Contract 注意

- `AGENTS.md` 行程安全鐵律全數適用（不得殺行程、禁跑窗、只用 `.venv`、R5 禁 `subprocess`／`os.spawn*`／`os.system`／`os.popen`、`.venv` 解譯器口徑）。
- `data/**` 只讀。
- 本票**不得**碰 Frozen Contract（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯）。

---

### 回報格式

1. **改動檔案清單**：逐檔寫「改了什麼、為什麼」。
2. **測試結果**：命令、passed／failed／skipped 數、exit code（**註明解譯器是 repo `.venv`**）。
3. **守衛與變異實驗**：M1／M2／M3 各自「變異內容 → 哪條守衛轉紅 → repo sha256 前後相同」的證據。
4. **skip 集合變化**：變更前 27 條 → 變更後 N 條，**逐條列 ID 與理由**。
5. **是否踩到 Frozen Contract／意外行為**：含 `tests/test_repo_hygiene_tracked_tests.py` 的預期紅燈。
