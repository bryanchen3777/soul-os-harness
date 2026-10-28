## 工單：FEAT-G3-PRODUCTION-BACKEND-WIRING（G-3 生產 Backend 接線與排程掛載）

- **開立者**：主大腦（Engineering Brain）
- **來源**：Owner 2026-10-28「後續主線作戰視野」第 1 條
  （「為 `run_server.py:902` 接入真實受控語意判定，正式打通 superseded 節點在生產演化槽中的誕生鏈路」）
- **狀態**：開立（decision-complete；執行者照做即可，不需再做設計決策）

---

### 目標

把 G-3 已完成的**矛盾預濾器＋語意判定**（commit `bb50c9e`）接到**生產演化槽**上，讓 `superseded` 節點能在生產槽真實誕生（＝G-3 從「離線可驗證」變成「生產會發生」）。

---

### 量測現況（主大腦 2026-10-28 實測，數字與行號可複現）

| 項 | 實測 |
|---|---|
| G-3 產物 | `src/inner_life/contradiction_prefilter.py`（`select_conflict_candidate_pairs`）、`src/inner_life/contradiction_judge_prototype.py`（`ContradictionPipeline`）、`src/inner_life/elevation_adapter.py`（`_run_contradiction_hook`）、守衛 `tests/inner_life/test_contradiction_prefilter_guard.py`（475 行） |
|  elevate 入口 | `src/inner_life/elevation_adapter.py:1417` `elevate_matured_patterns(*, store_dir, llm, min_evidence, contradiction_backend=None, contradiction_calls=None)` |
|  現況缺口 | `contradiction_backend` **缺省 None ⇒ 矛盾判定 Hook 整段跳過**（模組 docstring 明寫「生產預設零行為變化、零 LLM 成本」「本模組**不得**自建真實 LLM 連線（真實 backend 的注入由後續票負責）」） |
|  生產呼叫端 | `scripts/run_server.py:674-685` 的 `_elevate_check()` ⇒ **只呼叫 `elevate_matured_patterns()`，未注入 backend** ⇒ ⇒ **生產槽從不跑矛盾判定** ⇒ superseded 節點永遠不會誕生 |
|  觸發點 | `scripts/run_server.py:902`（`_WorldInnerLifeAdapterWithGate.handle_event` 內，consume 後 fire-and-forget 呼叫 `self._elevate_check()`） |
|  既有失敗隔離 | `_elevate_check` 為 fire-and-forget，失敗隔離在函式內（exception → warning ＋ 不阻斷主路徑） |

---

### 做法（決策已定，執行者照做）

#### D1. 新增真實 backend 模組 `src/inner_life/contradiction_backend_llm.py`

1. 對外只暴露**一個可注入的 callable**：`Callable[[str], str]`（與 `elevate_matured_patterns` 的 `contradiction_backend` 契約一致）⇒ **回傳二元判定字串**（`CONTRADICT` / `COMPATIBLE`）。
2. **預算硬限制（Owner 既定「真實 LLM 預算」規格，逐條實作）**：
   - **≤1 次／agent／日**：用 `contradiction_calls` 跨實例計數器（形如 `{(agent_id, date): n}`）⇒ 超額 ⇒ **不呼叫**、回 `COMPATIBLE`（fail-closed）。
   - **單次 ≤300 tokens**：呼叫端必須帶 token 上限（沿用 repo 既有 LLM 呼叫慣例的參數形狀，**不得**自造新的 LLM 連線機制）。
   - **retries = 0**：任何失敗**不重試**。
   - **二元輸出**：`CONTRADICT` / `COMPATIBLE`；**無法解析** ⇒ 視為 `COMPATIBLE`。
   - **fail-closed**：例外／逾時／格式不符 ⇒ 一律 `COMPATIBLE`（＝不動作、不寫引擎、不擋主路徑）。
3. **不得在模組層建立真實 LLM 連線** ⇒ 一律**延遲 import**（沿用 `elevation_adapter.py` 的既有規範：模組層 import 會與 `contradiction_prefilter` 成環）。
4. 預算耗盡／跳過呼叫時**必須寫一行 log**（可觀測：哪個 agent、哪一天、已用幾次）。

#### D2. 生產接線（`scripts/run_server.py`）

1. 在 `_elevate_check()` 內呼叫 `elevate_matured_patterns(contradiction_backend=<真實 backend>, contradiction_calls=<共享計數器>)`。
2. `contradiction_calls` 計數器必須**跨 `_elevate_check` 多次呼叫共享**（行程級單例）⇒ 否則「≤1 次／agent／日」會被每次呼叫重置 ⇒ **失守**。
3. **排程掛載維持既有位置**：`scripts/run_server.py:902` 的 fire-and-forget 觸發點**不動**（不得改成阻塞式、不得加進夜槽主迴圈、不得改變觸發頻率）⇒ 失敗隔離語意維持（exception → warning ＋ 不阻斷 submit／主路徑）。
4. `soul-elevation` 未安裝時（`except ImportError` 分支）維持 no-op（`pass`）⇒ 不得因為接線而在缺少外部組件時炸掉啟動。

#### D3. 觀測

每筆矛盾判定寫**一行** log（`agent_id`／`pattern node_id` 前 12 碼／`verdict`／是否記錄 superseded／本次是否真的呼叫了 LLM）⇒ 與 `elevation_adapter.py:1403` 既有 log 風格一致（`[elevate] ↻ contradiction …`）。

---

### 測試（新增守衛 `tests/inner_life/test_contradiction_backend_llm_guard.py`）

必須覆蓋（每條都要有**專屬變異實驗**，變異**必須轉紅**，否則守衛無牙）：

| # | 不變量 | 變異方向 |
|---|---|---|
| G1 | 同一 `(agent, date)` **第二次**呼叫**不觸發** LLM（預算 ≤1 次／日） | 把預算上限放大 ⇒ 必紅 |
| G2 | 單次呼叫的 token 上限 ≤300 | 把上限調大 ⇒ 必紅 |
| G3 | `retries = 0`（失敗不重試） | 改成重試 1 次 ⇒ 必紅 |
| G4 | **fail-closed**：例外／逾時／無法解析 ⇒ 回 `COMPATIBLE` 且**不寫引擎** | 改成 fail-open（回 `CONTRADICT`）⇒ 必紅 |
| G5 | 回傳值**只可能**是 `CONTRADICT` / `COMPATIBLE` 二元 | 讓它回第三種值 ⇒ 必紅 |
| G6 | 模組層**無** LLM 連線建立（延遲 import） | 改成模組層建立 ⇒ 必紅 |

- 守衛**不得**呼叫真實 LLM（用 mock backend 注入）⇒ **零 LLM 成本**。
- 既有 `tests/inner_life/test_contradiction_prefilter_guard.py` 必須**維持全綠**。
- `tests/test_observability_completeness_1.py` 的 frozen-contract／allowed 清單**需新增**本模組（既有守衛逐票更新慣例）⇒ 不得放寬既有條目。

---

### 驗收（完成的定義）

1. `elevate_matured_patterns` 的 `contradiction_backend` 在**生產路徑已被注入**（`scripts/run_server.py` 有實測可證的接線點）。
2. 新守衛 G1～G6 全綠，且**六條變異各自精準轉紅**（變異 harness 放 `%TEMP%`，repo 檔案全程零寫入、前後 sha256 相同）。
3. 既有 G-3 守衛 + 生命週期守衛 + 全庫回歸全綠（`0 failed`；skip 集合須與 `tests/infra/skip_baseline.json` **逐筆相同** ⇒ 這是第三棒留下的基準機制）。
4. **零真實 LLM 成本**（本票只做接線與守護，不在驗收階段真的打 LLM）。

---

### 🔴 重啟授權（硬性）—— 已由 Owner 2026-10-28 裁定

**裁定原文**：「核准工單規格，依約落實『每 Agent 每日至多 1 次、二元輸出、Fail-Closed、
跨呼叫共享計數器、延遲 Import』等防禦合約。……**在代碼與測試守衛全數通過、全庫回歸驗收合入主幹之前，
嚴禁發起即時重啟。** 生產進程（PID 3448）繼續保持連續運行；重啟動作待驗收落盤後，
統一排定於下一個受控維護窗口執行。」

⇒ **本票的最終決策**：
1. 規格照工單執行（≤1 次／agent／日、單次 ≤300 tokens、retries=0、二元、fail-closed、
   **跨呼叫共享計數器**、延遲 import）—— 這五條是**防禦合約**，不得為便利而放寬。
2. **本票全程不得發起任何重啟**（不得 `server_ops.ps1 restart`、不得 kill 任何 PID）。
   ⇒ 生產服務（PID 3448）**保持連續運行**。
3. **重啟時機**：待「程式 ＋ 守衛 ＋ 全庫回歸」全數合入主幹後，由 **Owner 統一排定於下一個受控維護窗口**執行
   （本票的執行者**無權**排定或執行重啟）。

---

### 不做（Out of Scope）

- 改 `soul-elevation`（**外部凍結組件**）的任何門檻／常數／邏輯。
- 改 `scripts/eval_lifecycle_cli.py` 的台帳判準（型別中立化／逐節點下界／三態判定已於 §0.1.97／§0.1.100 閉環）。
- 偽造任何 pattern 邊／手動消紅／為了讓 superseded 出現而造資料。
- 改 `_elevate_check` 的觸發位置或頻率。
- 提前執行 20:40 的台帳刷新或任何會 commit/push 的動作。

---

### 回報格式

1. 改動檔案清單（逐檔：改了什麼、為什麼）。
2. 新守衛 G1～G6 的「變異內容 → 哪條轉紅 → repo sha256 前後相同」證據。
3. 定向測試結果（命令、passed/failed/skipped、exit code、**註明解譯器是 repo `.venv`**）。
4. 預算行為的實測證據（同一 agent 同日第二次呼叫**沒有**真的打 LLM；用 mock 計數證明）。
5. 是否需要重啟（**並明確說明尚未重啟、等 Owner 授權**）。
