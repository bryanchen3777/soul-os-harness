# 工單：SIM-ACCEL-THREAD-DISSOLVE — 虛擬時鐘快進線頭終態，證明「第 36 條接縫」增量機制 100% 暢通

> **狀態**：決策完備，待派工
> **日期**：2026-10-20 23:05 EDT
> **Owner 授權**：Bryan 2026-10-20 22:37 EDT —— 戰役二；「不睡覺、不守夜、不等明天」
> **前置**：`e56a1bb`（SIM-ACCEL-RECALL-30D 工單）／`9465bc0`（§0.1.25）／LIFE-THREAD-W3-FIX-1（`afc82b3`／`da5561a`，接縫帶正文上線）

---

## 0. 主大腦的前提勘查（2026-10-20 22:50 EDT，**已讀碼確認**）

### 0.1 接縫（seam）的抑制機制

`src/soul/life_thread_orchestrator.py`：
- `_ELEVATION_EMITTED: Dict[str, int] = {}`（第 164 行）——**行程內記憶體**登記表，鍵為 `thread_id`。**新行程＝空表**。
- `maybe_emit_elevation_events(agent_id, threads, slot, now, *, writer=None, submission_gate=None)`（第 350 行）：
  - 旗標 `LIFE_THREAD_ELEVATION_ENABLED` 缺席／OFF ⇒ 直接回 `False`（0 事件、0 寫入）。
  - `if thread_id in _ELEVATION_EMITTED: continue`（第 414 行）⇒ **at-most-once 只在行程內成立**。
  - 事件必須經 `InnerLifeWriter.create_event` 建立、經 `submission_gate.submit(...)` 進入。
- M2 階段（`:741-782`）：`dissolved_candidates = sum(1 for e in evals if e.should_mutate)`；存量終態 catch-up（`LIFE_THREAD_CATCHUP_ENABLED`）對 `status ∈ {completed, abandoned}` ∧ `dissolved_at is None` 的線頭交棒給 `dissolve_hook`。

### 0.2 讓線頭自然轉入 `completed` 的真實條件（`src/soul/life_thread_dissolution.py`）

- **存活軸**：`age = current_time − created_at`，門檻 `POLICY_MAX_ACTIVE_DURATION_DAYS = 14`。
- **無進展軸**：`idle = current_time − updated_at`，門檻 `POLICY_STALE_CHECK_THRESHOLD_DAYS = 7`。
- 🔴 **邊界嚴格大於（`>`）**：`age == 14 天` **不算**超限，**+1 秒才算**。⇒ 虛擬時鐘的正確取樣點是 `created_at + 14 天 + 1 秒`。
- 命中存活軸 ⇒ `reason = TIME_HORIZON_EXCEEDED`、`target_status = completed`、`should_mutate = True`。
- 另有 `checkpoints_exhausted is True` ⇒ `CHECKPOINTS_EXHAUSTED`（本票不用這條）。

### 0.3 溶解執行層完全可注入（`src/soul/life_thread_dissolution_exec.py`）

**純 stdlib、0 `src.*` import**；`llm_call`／`fact_writer`／`now`／`budget` **全部參數注入** ⇒
⇒ **本票可以完全 hermetic：注入假 LLM 與假 fact writer，0 真實推理、0 真實寫入。**
- 單次呼叫點固定：`llm_call(prompt, max_tokens=CONSOLIDATION_MAX_TOKENS)`；`budget=None` 時用行程級預算（省錢方向），**本票必須注入無限預算**。
- 回傳成功 ⇒ `status="consolidated"`、`fact=<正規化後的 fact>`、`llm_calls=1`。
- `confidence` **一律強制 1.0**（不由 LLM 產生）。

### 0.4 路徑隔離（`src/paths.py`）

`data_root()` 讀 `SOUL_OS_DATA_DIR` 環境變數，**行程期快取**。`src/soul/life_threads.py` 的 `life_threads_path()` 一律經 `data_root()` 組出。
⇒ **沙盒隔離做法：在專用子行程啟動前**設定 `SOUL_OS_DATA_DIR`（＝沙盒根），**不得**在同一行程中途改環境變數後再比較（2026-10-19 事故根因：拿兩個各自會漂移的值做比較）。

### 0.5 🔴 對 Owner 預期的一處修正

Owner 寫「產出增量的**第 10 顆 Value 節點**」。**機制上不成立**：Value 節點不是由「事件數」決定，而是由 **pattern 群組累積達 `min_evidence=2`** 觸發（`src/inner_life/elevation_adapter.py::elevate_matured_patterns`）。2026-10-19 的 9 顆 value 是當晚 diary/dream 累積出 51 個 pattern 後才昇華的，與 35 筆接縫事件無一一對應。
⇒ **本票的驗收目標改為**：第 36 筆接縫事件**正確產生**（且只有它產生）、帶正文與 reflection、35 筆歷史**不被重發**。「是否再長出一顆 value」**不在本票範圍**（它取決於 pattern 群組，不是事件數）。

---

## 1. 目標

在 **hermetic 沙盒**（`SOUL_OS_DATA_DIR` 指向 `%TEMP%` 沙盒）內，用虛擬時鐘把一條在途線頭推進到 `completed` 自然終態，證明：
1. **第 36 筆接縫事件被正確產生**（`maybe_emit_elevation_events` 回 True、事件帶 narrative ＋ reflection）；
2. **35 筆歷史線頭不被重發**（`_ELEVATION_EMITTED` 的 at-most-once 在預先播種後仍然有效）；
3. **生產檔案位元級未動**（`data/soul/**` ＋ `data/memory/**` 的 sha256 前後相同）。

**一句話**：增量發射機制面對**未來**的新線頭時，**不需要等真實生活推進 30 天**就能證明它 100% 暢通。

---

## 2. 範圍（你擁有這些檔案的寫入權；其他檔案一律不得動）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `scripts/sim_accel_thread_cli.py`（**新增**） | 兩階段沙盒 CLI（父行程準備沙盒 → `os.execve` 換成子行程 → 子行程跑模擬） |
| 2 | `tests/test_sim_accel_thread.py`（**新增**） | 守衛測試 + 變異實驗 |
| 3 | `logs/SIM-ACCEL-THREAD-DISSOLVE-IMPLEMENTATION.md`（**新增**） | 實作紀錄（實測數字、變異證據） |

**不得動**：`src/soul/life_thread_orchestrator.py`、`src/soul/life_thread_dissolution.py`、`src/soul/life_thread_dissolution_exec.py`、`src/soul/life_threads.py`、`src/paths.py`、`src/inner_life/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`。

---

## 3. 做法（決策已定，執行者照做）

### 3.1 `scripts/sim_accel_thread_cli.py` 的兩階段結構

**為什麼要兩階段**：`data_root()` 是行程期快取，**必須在行程啟動前**決定沙盒根 ⇒ 不能在同一行程內「先讀生產、再改 env、再寫沙盒」。
**做法**：用 `os.execve`（**不是** `subprocess`）換行程映像。

```
階段一（父行程，--phase parent）：
  1. 解析參數：--agent（預設 mahiru）、--target-thread（可選，預設自動挑）、--days-ahead、--out-dir、--phase
  2. 建立沙盒：%TEMP%\sim_accel\thread_<UTC時間戳>\
       ├─ soul/<agent_id>/life_threads.jsonl   （由生產複製）
       ├─ memory/<agent_id>/graph.sqlite(+-wal/-shm)（由生產複製，供事實寫入）
       └─ elevation/（若下游需要）
  3. 生產 sha256 before：data/soul/**/life_threads.jsonl、data/memory/**/graph.sqlite（逐檔）
  4. env = os.environ.copy(); env["SOUL_OS_DATA_DIR"] = str(sandbox); env["LIFE_THREAD_ELEVATION_ENABLED"] = "1"
  5. os.execve(sys.executable, [sys.executable, __file__, "--phase", "child", ...], env)
     ⚠️ execve 會**取代**行程映像 ⇒ 父行程的 after 驗證**做不了** ⇒ 見 3.3 的分工。

階段二（子行程，--phase child）：
  1. 自我檢查：assert Path(os.environ["SOUL_OS_DATA_DIR"]).resolve() == sandbox.resolve()
     ⇒ data_root() 從第一次呼叫起就是沙盒（**不允許中途漂移**）
  2. 載入沙盒線頭：from src.soul import life_threads as lt; threads = lt.fold(agent_id)
  3. **預先播種 _ELEVATION_EMITTED**（重現線上伺服器的行程內登記表）：
     from src.soul import life_thread_orchestrator as orch
     取出「已 completed 且有 sage_fact_id」的線頭 id（沙盒內即可取得，不需要讀日誌），
     逐個寫入 orch._ELEVATION_EMITTED[tid] = 1
     ⇒ 這些就是 2026-10-19 已發射過的 35 條
  4. 挑目標在途線頭：status == "active" 且 dissolved_at is None；
     優先挑 --target-thread 指定者；否則挑 created_at 最舊且 created_at + 14d + 1s 已過於 now 的那一條
     （挑不到 ⇒ 印明確錯誤並 exit 2，**不許靜默改用別的線頭**）
  5. **虛擬時鐘**：
     virtual_now = created_at + timedelta(days=14) + timedelta(seconds=1)
     （嚴格大於邊界；用 15 天也可以，但**必須是 14 天整 + 1 秒**才能證明邊界語意）
  6. **M2 評估**：呼叫 `src/soul/life_thread_dissolution.py` 的評估函式（自行讀碼對齊確切函式名與簽章），
     傳入 thread、current_time=virtual_now、max_active_duration_days=14、stale_check_threshold_days=7、allow_soft_archive=False
     ⇒ 斷言：should_mutate is True、target_status == "completed"、reason 為 TIME_HORIZON_EXCEEDED
  7. **溶解執行層（注入假 LLM ＋ 假 fact writer）**：
     from src.soul import life_thread_dissolution_exec as exec_mod
     llm_call = lambda prompt, max_tokens=...: 回傳一段合法的 JSON 字串（含 "dissolution" 欄位、非空敘述 ≤200 字）
       ⚠️ 假 LLM 必須**回傳與真實契約相容的形狀**（JSON 物件、欄位名以 `exec_mod` 的解析器為準；解析器容忍 ``` 圍籬等畸形）
     fact_writer = 可呼叫物件：回傳一個假的 sage_fact_id（例如 "sim_fact_<uuid4 hex>"）並把 fact 記進記憶體清單
     budget = 注入「不限額」的預算物件（其 try_consume(agent_id) 恆回 True）
     now = virtual_now
     ⇒ 斷言：回傳 status == "consolidated"、llm_calls == 1、fact 非空
  8. **回寫沙盒 M1**（用既有 API，不要自創寫法）：
     lt.append_transition(agent_id, thread_id, "completed", updated_at=virtual_now.isoformat())
     lt.append_dissolved(agent_id, thread_id, dissolved_at=virtual_now.isoformat(), sage_fact_id=<假 id>)
     ⇒ 這兩筆寫在沙盒（data_root() 已是沙盒）
  9. **接縫**（核心）：
     重新 fold 取得最新 threads
     created = orch.maybe_emit_elevation_events(agent_id, threads, slot="night", now=virtual_now)
     ⇒ 斷言：created is True
     ⇒ 斷言：本次**只新增 1 個事件**（用 writer/submission_gate 的注入點計數，或比對 sidecar 筆數差）
     ⇒ 斷言：35 條預先播種的線頭**都沒有**被重發
     ⇒ 斷言：新事件帶有該線頭的 narrative（正文）與 dissolution 的 reflection
        （W3-FIX-1 的硬要求：事件必須帶正文，否則昇華出來的是標籤退化品）
 10. 產出 JSON 結果檔到 --out-dir（含上述每一項斷言的實測值）。
```

### 3.2 沙盒化紀律（逐條照做）

- **唯一寫入目標＝沙盒**（`SOUL_OS_DATA_DIR` 指向 `%TEMP%` 下的沙盒）。**不提供任何就地寫入旗標**（AGENTS.md 鐵律 12）。
- 沙盒必須在 `tempfile.gettempdir()` 之下，否則 `print("[refuse] ...")` 並 `exit 3`。
- **不 import** `subprocess`／`requests`／`httpx`／`socket`／`uvicorn`／`fastapi`（`os.execve` 是允許的，它不是程序建立 API）。
- **不啟動伺服器、不綁任何埠、不發任何網路請求**。
- **0 真實 LLM 呼叫**（`llm_call` 一律注入假身）—— 這是本票的紅線：一旦注入失效就會真的花錢並打到外部 API。
- 生產檔 sha256 對帳（見 3.3）。
- 報告寫 `%TEMP%`；不寫任何 repo 檔案（除 §2 列出的 3 個檔案）。

### 3.3 生產位元級對帳（誰做）

- **父行程**在 `os.execve` 前算完生產 sha256 **before**，把結果寫進沙盒的 `production_sha_before.json`。
- **子行程**在模擬結束後算生產 sha256 **after**，寫 `production_sha256_after.json`，並**逐檔比對**：任一不同 ⇒ **印明確 FAIL 並 exit 4**（不是警告）。
- 比對範圍：`data/soul/*/life_threads.jsonl` 全檔、`data/memory/*/graph.sqlite` 全檔、`data/elevation/*.jsonl` 全檔。
- ⚠️ **不得**在子行程裡用 `data_root()` 去取生產路徑（那已是沙盒）⇒ 生產路徑必須用**絕對字面路徑**（`REPO_ROOT / "data" / ...`）取得，且**只讀**。

### 3.4 CLI 介面

```
--phase        parent | child（預設 parent）
--agent        agent id（不含 "agent_" 前綴，預設 mahiru）
--target-thread  可選：指定 thread_id（32  hex）；預設自動挑
--out-dir      沙盒根（預設 %TEMP%\sim_accel\thread_<UTC時間戳>）
--json         以 JSON 輸出
```
退出碼：全部斷言通過 ⇒ 0；斷言失敗 ⇒ 1；沙盒不在 temp 之下 ⇒ 3；生產 sha256 不符 ⇒ 4；找不到合適目標線頭 ⇒ 2。

### 3.5 守衛測試 + 變異實驗（`tests/test_sim_accel_thread.py`）

**守衛測試**
1. `virtual_now = created_at + 14 天`（**整數天，不加 1 秒**）⇒ **必須 `should_mutate is False`**（證明邊界是嚴格 `>`，不是 `>=`）。
2. `virtual_now = created_at + 14 天 + 1 秒` ⇒ `should_mutate is True`、reason = `TIME_HORIZON_EXCEEDED`。
3. 預先播種 35 條後跑接縫 ⇒ **只新增 1 個事件**，且事件對應的是**新推進的那條線頭**。
4. 不預先播種（模擬新行程）⇒ 接縫會發射**全部** 36 筆（證明 at-most-once 確實是行程內機制，且預先播種確實有效）。
5. 溶解執行層注入假 LLM ⇒ `llm_calls == 1`、無任何真實網路呼叫（測試中 monkeypatch 真實 LLM 入口為「一被呼叫就 raise」）。
6. 沙盒執行後，生產 `data/soul/**` ＋ `data/memory/**` 的 sha256 **逐位元相同**。

**變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）**

| # | 變異 | 必須轉紅的測試 |
|---|---|---|
| 1 | 把 M2 的邊界由 `>` 改成 `>=` | 守衛測試 1（14 天整必須不轉換） |
| 2 | 移除 `_ELEVATION_EMITTED` 的 at-most-once 跳過 | 守衛測試 3（只能新增 1 個事件） |
| 3 | 讓假 LLM 的回傳形狀不符合解析契約 | 守衛測試 5（`status` 必須是 `consolidated`） |
| 4 | 讓溶解執行層在 `llm_call is None` 時不跳過而直接呼叫 | 守衛測試 5（0 真實呼叫） |
| 5 | 把沙盒的 `SOUL_OS_DATA_DIR` 設定改成父行程之後才設定（模擬漂移） | 守衛測試 6（生產 sha256 必須相同） |

🔴 **變異實驗的執行安全（AGENTS.md 鐵律 7）**：凡「移除某道閘門」型變異，必須讓被解除防護的程式碼**不可能真的執行**（用 pytest `pytest_runtest_call` hook 在 call 階段直接 `raise`）。**不得**讓任何變異觸達生產。**不得**讓假 LLM 失效後真的打到外部 API（注入點必須是「缺依賴就跳過」的路徑）。

---

## 4. 驗收（完成的定義）

1. `.venv\Scripts\python.exe scripts\sim_accel_thread_cli.py --agent mahiru` 輸出**全部斷言通過**，且：
   - M2 判定：`should_mutate=True`、`target=completed`、`reason=TIME_HORIZON_EXCEEDED`、虛擬時點 = `created_at + 14d + 1s`；
   - 接縫：**只新增 1 個事件**（第 36 筆），35 筆歷史**零重發**；
   - 新事件**帶正文（narrative）與 reflection**；
   - `llm_calls == 1` 且**無真實推理**；
   - **生產三類檔案（soul／memory／elevation）sha256 前後逐位元相同**。
2. `pytest` 全庫回歸 **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
3. **5 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
4. 實作紀錄寫入 `logs/SIM-ACCEL-THREAD-DISSOLVE-IMPLEMENTATION.md`。

## 5. 測試

- 新測試：`tests/test_sim_accel_thread.py`（如上）。
- 必跑現有測試：`tests/soul/test_life_thread_dissolution_exec.py`、`tests/soul/` 下的線頭相關測試、`tests/infra/`（AST 守衛）。
- 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（**必須指名範圍**）。
- 不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。

## 6. 不做（Out of Scope）

- 不得改 `src/soul/life_thread_orchestrator.py`／`life_thread_dissolution.py`／`life_thread_dissolution_exec.py`／`life_threads.py`／`src/paths.py`。
- 不得改 `src/inner_life/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`。
- 不得寫入／改名／刪除 `data/**` 下任何檔案；**沙盒資料不得流回生產**。
- 不得重啟服務、不得啟動／停止任何行程、不得對任何 PID 做 `Stop-Process`／`taskkill`／`pkill`。
- **不得觸發任何真實 LLM 推理**（假 LLM 必須 100% 覆蓋）。
- 不得觸發 synthetic diary／dream。
- **不得**在本票追求「再長出一顆 value 節點」（機制上由 pattern 群組決定，見 §0.5；已排除於本票範圍）。
- 不得跑 `scripts/manual_*.py`。
- **不得 commit／push**（由主大腦驗證後執行）。

## 7. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `src/soul/life_thread_dissolution.py` 的邊界語意 | `>` 嚴格大於（14 天整不算、+1 秒才算）**不得**改動 |
| `_ELEVATION_EMITTED` 的 at-most-once 語意 | **不得**改動（行程內記憶體、鍵為 `thread_id`） |
| `maybe_emit_elevation_events` 的旗標預設 | `LIFE_THREAD_ELEVATION_ENABLED` 預設 OFF；沙盒測試才設 ON，**不得**改預設 |
| `life_thread_dissolution_exec` 的「依賴缺席就跳過」語意 | **不得**改動（`llm_call is None` ⇒ `skipped_no_llm`、0 呼叫） |
| `confidence` 強制 1.0 | **不得**改動 |
| M1 落盤 | append-only；路徑必須經 `data_root()`（**不得硬編碼 `data/`**） |

## 8. 回報格式

1. 改動檔案清單（新增／修改，逐檔一行）。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：5 條逐條的「變異內容 → 轉紅的測試名 → 紅燈實測輸出」。
4. 沙盒實測：M2 判定值、虛擬時點與其與 14 天邊界的關係、接縫事件數（新增／重發各幾筆）、事件正文與 reflection 的實測片段（截斷 ≤200 字）、`llm_calls`、生產三類檔案 sha256 前後對帳。
5. 是否有踩到 Frozen Contract／意外行為。
