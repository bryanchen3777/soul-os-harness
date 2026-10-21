# 工單：SIM-ACCEL-STREAK-7D — 7 天連續可見性狀態機的模擬時鐘驗證（容量動態 ＋ streak 計數器）

> **狀態**：決策完備，待派工
> **日期**：2026-10-20 23:15 EDT
> **Owner 授權**：Bryan 2026-10-20 22:37 EDT —— 戰役三；「不睡覺、不守夜、不等明天 08:20」
> **前置**：`e56a1bb`（SIM-ACCEL-RECALL-30D 工單）／SIM-TIME-001（`tests/soul/test_life_thread_sim_time.py`）／M2-WRITEBACK-SIM-1／M2-CATCHUP-1

---

## 0. 主大腦的前提勘查（2026-10-20 22:50 EDT，**已讀碼確認 ＋ 已實測**）

### 0.1 🔴 關鍵發現：時間流逝**不會**讓 active 線頭完成

`src/soul/life_thread_dissolution.py`：
- 步驟 4（諮詢訊號）：age（`current_time − created_at` > 14 天，`POLICY_MAX_ACTIVE_DURATION_DAYS`）或 idle（`current_time − updated_at` > 7 天，`POLICY_STALE_CHECK_THRESHOLD_DAYS`）命中時，**在 `allow_soft_archive=False`（生產值）下只產生 advisory**：`target_status=ACTIVE`、`should_mutate=False`、`extra_metadata["advisory"]=True`。
- 步驟 3：active 線頭要 `should_mutate=True` **只有**呼叫端顯式注入 `checkpoints_exhausted=True` 一條路；M1 fold 的 13 個欄位**沒有**檢驗點清單，模組註文明文禁止猜測。
- ⇒ **active 線頭的終態由「誰決定關閉它」決定，不是由時間決定。**

**生產實測佐證**（2026-10-20 22:50，`lt.fold` 唯讀）：10 隻 agent **全部滿載**（active=2／cap=2），active 總數 20；其中 6 條 active 線頭的 age 已達 **33.03 天**（遠超 14 天存活軸）**卻仍是 active**。

### 0.2 現況與門檻三基準

| 項目 | 值 |
|---|---|
| active 線頭總數 | 20（10 隻 × 2 ＝ **全部滿載**） |
| 已完成線頭 | 35 |
| 門檻三 判準 2（窗口內每日推進的 thread_id／標題） | **成立**（2026-10-20 17:05 實測：每日都是不同 thread_id、不同標題） |
| 門檻三 判準 1（連續 7 天） | **不成立**；最高 mahiru **5 天**（§0.1.22） |

### 0.3 🔴 本票要回答的核心問題（不是「再等 7 天」）

**門檻三判準 1「連續 7 天，每天都有主動推進」在「cap=2 ＋ 每天需要一個**新** thread_id」的限制下，究竟是：**

- **(A) 純粹的時序問題**（再等 2 天就會自然轉綠）？
- **(B) 容量動態問題**（每隻 agent 只有 2 個槽位；每天要新 thread_id 就必須關閉一條舊的；關閉是由 LLM 決定的 ⇒ 是**機率性**的，不是**時間性**的）？

⇒ 若答案是 (B)，則「處置合約＝維持自然觀察」維持的是一個**沒有保證到達路徑**的狀態；需要 Bry 裁示是否調整政策（**本票只量測，不改政策**）。

### 0.4 SIM-TIME-001 平面**已存在**（不需另建）

`tests/soul/test_life_thread_sim_time.py`（1000+ 行）：**假時鐘 ＋ 記憶副本 ＋ 零 LLM 的可重放 harness**。
- 金樣快照：`tests/fixtures/life_thread_snapshot_2026091{9,20}_0800.json`（`provenance.ticket = "SIM-TIME-001"`、`plane = "simulation (fake clock + memory fork + zero LLM)"`）。
- `ScriptedLLM`（預錄 LLM 回覆，契約 `{"actions":[{"op": ...}]}`）。
- socket 連外攔阻（`socket.create_connection` 被 monkeypatch 成 raise）。
- 平面不變量：**連續 slot ＝ 直接換 `now` 再呼叫 ⇒ 0 `time.sleep`／0 真實等待**；唯一允許的 `await asyncio.sleep(0)` 是 event-loop 讓渡。
- ⚠️ `tests/soul/` **無 `__init__.py`** ⇒ **不得跨檔 import 另一個測試模組**（模組名解析依賴 pytest rootdir 插入順序，且會與 SIM-TIME-001 的 autouse 平面不變量糾纏——它的 `_no_live_llm` 把旗標釘在關）。既有做法是**複製最小必要 primitive**（見 `tests/soul/test_life_thread_sim_m2_writeback.py` 的「跨檔 import 的取捨」註記）。**本票沿用同一做法。**

---

## 1. 目標

在 **SIM-TIME-001 風格的假時鐘平面**上，以 **7 個日曆日 × 2 個 slot = 14 個 slot**（0 真實等待）驅動 10 隻 agent 的線頭引擎，量測：

1. **容量動態**：每隻 agent 在 14 個 slot 中能建立幾條新線頭、關閉幾條、是否出現**容量死鎖**（連續 N 個 slot 無法建立新線頭）。
2. **標題多樣性**：14 個 slot 建立的新線頭標題**互不相同**的比率。
3. **streak 計數器**：用**門檻三的正確算法**（`updated_at` 日期集合 → 對每個日期作錨點往前連續計數 → 取最大值）在第 7 天是否**精確轉綠**。
4. **反事實對照**：把「每天新建一條線頭」換成「每天只推進既有線頭」，量測 streak 計數器**不會**轉綠（證明判準 2 的「不同 thread_id」要求確實是關鍵，不是多餘條件）。

**不達標也回報** —— 本票的價值在於把「不確定」變成「確定」。

---

## 2. 範圍（你擁有這些檔案的寫入權；其他檔案一律不得動）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `tests/soul/test_sim_accel_streak_7d.py`（**新增**） | 7 天 × 14 slot 的假時鐘模擬 ＋ 守衛測試 ＋ 變異實驗 |
| 2 | `logs/SIM-ACCEL-STREAK-7D-IMPLEMENTATION.md`（**新增**） | 實作紀錄（量測數字、變事證據、結論） |

**不得動**：`src/**`（全部）、`tests/soul/test_life_thread_sim_time.py`、`tests/soul/test_life_thread_sim_m2_writeback.py`、`tests/soul/test_life_thread_sim_m2_catchup.py`、`data/**`、`docs/**`、`personas/**`、`configs/**`。

> ⚠️ **本票是純測試票**（0 `src/**` 改動）—— 因為要回答的是「現有狀態機在 7 天高頻推進下會怎樣」，改動被測對象就失去意義了。

---

## 3. 做法（決策已定，執行者照做）

### 3.1 平面 primitive（複製最小必要集合，**不得跨檔 import 測試模組**）

從 `tests/soul/test_life_thread_sim_time.py` 複製／沿用：
- **假時鐘**：`now` 由測試直接注入（local tz `America/New_York`；slot 08:00／22:00）。連續 slot ＝ 直接換 `now` 再呼叫 ⇒ **0 `time.sleep`／0 真實等待**。
- **記憶副本**：把 `data/soul/<agent>/life_threads.jsonl` 的事件流**時間截斷**到起點（`updated_at <= cutoff`、保留檔案順序、投影到 `fields_kept`）⇒ 得到起點狀態；所有後續寫入落在 **tmp_path**（pytest 的 `tmp_path`），**不碰生產**。
  ⚠️ 金樣快照的 `provenance.derivation` 即此語意（見 `tests/fixtures/life_thread_snapshot_20260920_0800.json`），可對照。
- **ScriptedLLM**：回傳 M4 契約形狀 `{"actions":[{"op": ...}]}`。**確切 op 與欄位請讀 `src/soul/life_thread_origins.py`（M4 `run_origin_round`）與 `src/soul/life_threads.py::apply_actions` 自行對齊，不得猜。**
- **socket 攔阻**：`socket.create_connection` monkeypatch 成 raise（斷言「矩陣內不得連外」）。
- **零真實 LLM**：腳本化 LLM 必須 **100% 覆蓋**；任何未預期的 LLM 呼叫都要 raise。

### 3.2 兩條劇本（**都要跑**）

| 劇本 | 每個 slot 的腳本化 LLM 動作 | 目的 |
|---|---|---|
| **A：每天新建一條線頭** | 在有容量空位時回「建立新線頭」動作；**同時**回「關閉最舊的 active 線頭」動作（模擬 LLM 自主收束） | 主劇本：驗證 streak 計數器能否在第 7 天轉綠、標題是否互異、是否容量死鎖 |
| **B：每天只推進既有線頭** | 只回「推進／更新既有線頭」動作，**不新建、不關閉** | 反事實：證明「不同 thread_id」是判準 2 的關鍵 |

**7 天 × 2 slot = 14 個 slot**；slot 時間依 local tz `America/New_York` 的 08:00／22:00，日期連續遞增。

### 3.3 每隻 agent 的 4 項量測（**每個數字寫明分子／分母／單位**）

1. **容量動態**：14 個 slot 中「新建立線頭數」「關閉線頭數」「建立失敗（容量飽和）slot 數」；並定義**容量死鎖**＝連續 ≥ 3 個 slot 無法建立新線頭（門檻由主大腦定，**寫在報告裡**）。
2. **標題多樣性**：新建線頭的標題集合大小 ÷ 新建線頭總數；並列出重複的標題（若有）。
3. **streak 計數器**：用**門檻三的正確算法**（`updated_at` 日期集合 → 對每個日期作錨點往前連續計數 → 取最大值）逐日計算，報告第 1~7 天的值；**斷言第 7 天是否達 7**。
   ⚠️ **不得**使用「最後一次更新距今幾天」——該口徑已在 §0.1.22 被證偽並作廢。
4. **反事實對照**：劇本 B 的 streak 計數器逐日值。

### 3.4 額外的容量動態模型（純計算，不改任何程式）

用起點狀態建立一個**簡單的解析模型**，回答：「在 cap=2、每天新建 1 條、每天關閉 1 條的假設下，容量何時第一次出現空位不足？」與「若 LLM 的關閉決策有 p 機率發生，7 天內至少關閉 6 次的機率是多少（每 agent 每天 1 次嘗試，7 天 ⇒ 1−(1−p)^7）」。
⇒ 這讓 Bry 能**用數字**判斷「門檻三的不穩定性來源是容量、還是 LLM 的關閉傾向」，而不是靠感覺。

### 3.5 守衛測試 + 變異實驗

**守衛測試**
1. 14 個 slot 跑完，**0 `time.sleep`／0 真實等待**（斷言總耗時 < 30 秒）。
2. **0 真實 LLM 呼叫**（socket 攔阻生效 ＋ 腳本化 LLM 100% 覆蓋）。
3. 生產 `data/soul/**/life_threads.jsonl` 的 sha256 **前後逐位元相同**（所有寫入都在 tmp_path）。
4. 劇本 A 的 streak 計數器在第 7 天的值**與手算結果一致**（寫死一個小的手算案例做對照）。
5. streak 計數器**不是**「距今天數」：餵入一個「最後一次更新距今 30 天但只有 1 天有推進」的資料，必須得到 1 而非 30。
6. 劇本 B 的 streak **不**轉綠（證明判準 2 的「不同 thread_id」確實必要）。

**變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）**

| # | 變異 | 必須轉紅的測試 |
|---|---|---|
| 1 | 把 streak 計數器改成「距今天數」 | 守衛測試 5 |
| 2 | 讓容量檢查失效（cap 視為無限） | 容量動態量測（死鎖數必須改變） |
| 3 | 讓腳本化 LLM 漏接某些 slot（產生未預期呼叫） | 守衛測試 2（0 真實 LLM 呼叫） |
| 4 | 把 memory fork 寫回生產（去掉 tmp_path 隔離） | 守衛測試 3（生產 sha256 相同） |
| 5 | 讓標題多樣性統計漏掉重複計數 | 守衛測試 4（計數器與手算一致） |

🔴 **變異實驗的執行安全（AGENTS.md 鐵律 7）**：凡「移除某道閘門」型變異，必須讓被解除防護的程式碼**不可能真的執行**（用 pytest `pytest_runtest_call` hook 在 call 階段直接 `raise`）。**不得**讓任何變異觸達生產或真實 LLM。

---

## 4. 驗收（完成的定義）

1. `.venv\Scripts\python.exe -m pytest -q tests/soul/test_sim_accel_streak_7d.py` **全綠**，且報告含：
   - 劇本 A／B 各自的 14 個 slot 逐 slot 明細（建立／關閉／失敗、計數器值、當日新 thread_id 與標題）；
   - 4 項量測的分子／分母／單位；
   - §3.4 的容量動態模型與機率表；
   - **明確結論**：門檻三判準 1 的不穩定性來源是 (A) 時序 還是 (B) 容量／機率。
2. 生產 `data/soul/**` sha256 前後**逐位元相同**。
3. 全庫回歸 `.venv\Scripts\python.exe -m pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
4. **5 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
5. 實作紀錄寫入 `logs/SIM-ACCEL-STREAK-7D-IMPLEMENTATION.md`（**結論要寫清楚，不要寫「看起來」**）。

## 5. 測試

- 新測試：`tests/soul/test_sim_accel_streak_7d.py`。
- 必跑現有測試：`tests/soul/test_life_thread_sim_time.py`、`tests/soul/test_life_thread_sim_m2_writeback.py`、`tests/soul/test_life_thread_sim_m2_catchup.py`、`tests/infra/`（AST 守衛）。
- 全庫回歸：`.venv\Scripts\python.exe -m pytest -q tests`（**必須指名範圍**）。
- 不得啟動伺服器、不得綁 `:8000`／`:8765`／`:8766`／`:8767`、不得對生產發真實請求。

## 6. 不做（Out of Scope）

- **不得改任何 `src/**`**（本票是純測試票）。
- 不得改既有 SIM-TIME-001／M2-WRITEBACK-SIM-1／M2-CATCHUP-1 的測試檔。
- 不得寫入／改名／刪除 `data/**`；不得改 `docs/**`、`personas/**`、`configs/**`。
- 不得重啟服務、不得啟動／停止任何行程、不得對任何 PID 做 `Stop-Process`／`taskkill`／`pkill`。
- **不得觸發任何真實 LLM 推理**。
- 不得觸發 synthetic diary／dream；不得跑 `scripts/manual_*.py`。
- **不得**為了好看而調大 cap、改門檻、或「催熟」線頭 —— 本票只量測現狀。
- **不得 commit／push**（由主大腦驗證後執行）。

## 7. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `src/soul/life_thread_orchestrator.py` 的 M2 advisory 語意 | age/idle 命中**不得**變更狀態（`should_mutate=False`、`advisory=True`）——**不得**為了讓本票有結論而改它 |
| `checkpoints_exhausted` 語意 | 呼叫端顯式注入；**不得**由測試從資料推斷 |
| `POLICY_MAX_ACTIVE_DURATION_DAYS=14`／`POLICY_STALE_CHECK_THRESHOLD_DAYS=7`／`POLICY_ALLOW_SOFT_ARCHIVE=False` | **不得**調整 |
| `life_thread_dissolution` 的邊界（`>` 嚴格大於） | **不得**改動 |
| 門檻三 streak 計數器算法 | 正確算法（日期集合 → 錨點連續計數 → 取最大值）；**「距今天數」已作廢**，不得復辟 |
| SIM-TIME-001 的平面不變量 | 0 `time.sleep`、0 真實 LLM、socket 攔阻、金樣快照語意 |

## 8. 回報格式

1. 改動檔案清單。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：5 條逐條的「變異內容 → 轉紅的測試名 → 紅燈實測輸出」。
4. 14 個 slot 逐 slot 明細（兩劇本）＋ 4 項量測（分子／分母／單位）＋ 容量動態模型與機率表。
5. **明確結論**：門檻三判準 1 屬於 (A) 時序問題 還是 (B) 容量／機率問題；若是 (B)，給出「要達成 7 天連續所需的最小條件」與其當前是否成立。
6. 是否有踩到 Frozen Contract／意外行為；生產 sha256 前後對帳。
