# 工單：SERVER-OPS-PLAN-A-LAUNCH — 讓 watchdog 的 Plan A 啟動路徑走同一條中繼機制，關掉最後一個同根因路徑

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 05:15 EDT
> **前置**：`SERVER-OPS-HANDLE-INHERITANCE`（已交付：主啟動路徑已改為「呼叫端 → 中繼 launcher → 服務」，`bInheritHandles=FALSE`，**唯一驗收標準達成**：xfail → PASS，12 passed / 0 xfailed，全庫 6788 passed / 0 failed）
> **殘餘**：`scripts\_start_plan_a.ps1`（watchdog 的 Plan A 自動拉起路徑）**仍用 `.NET` 導向 ＋ `-NoNewWindow`** ⇒ 同一根因仍在。

---

## 0. 為什麼這張票必須存在（已由前一票實測確立）

**同一根因**：`.NET` 的 `Start-Process -RedirectStandard*` 強制 `UseShellExecute=false`，而 .NET 的 `CreateProcess` **一律 `bInheritHandles=TRUE`** ⇒ 被拉起的行程會**連帶繼承呼叫端的 stdout pipe** ⇒ 呼叫端要等**服務退出**才收到 EOF ⇒ 指令「看起來不返回」⇒ 被牆鐘上限**連坐砍掉整棵行程樹**（2026-09-16 事故形狀；2026-10-21 02:10 DEPLOY-RESTART-21 的 wrapper 即是被 600 s 上限殺掉，服務僥倖存活）。

**前一票已關掉主路徑（`server_ops.ps1`）** —— 但 **watchdog 在服務意外死亡時的 Plan A 自動拉起路徑**（`scripts\_start_plan_a.ps1:88-93`）**仍是舊機制** ⇒ 那條路徑上的 600 s 牆鐘風險**仍然存在**，而且它觸發的時機恰恰是**服務已經出問題**的時候 ⇒ **最需要可靠的那條路徑反而最不可靠**。

## 1. 目標

讓 **Plan A 自動拉起路徑**不再繼承呼叫端 stdout pipe 與 Console，**且與主路徑共用同一個啟動原語**（避免兩份啟動邏輯各自漂移）。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `scripts\_start_plan_a.ps1`（**修改**） | Plan A 的啟動呼叫改走中繼路徑 |
| 2 | `tests\infra\test_plan_a_launch_inheritance.py`（**新增**） | 守衛測試 ＋ 變異實驗 |

**不得動**：`src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、`logs/**`、`scripts\server_ops.ps1`（**主路徑已定案並合入，不得再改**）、其他 `scripts/**` 檔案。

## 3. 做法（決策已定）

### 3.1 首選：**讓 Plan A 直接呼叫 `server_ops.ps1 start`**

`server_ops.ps1` 的 `start` **已經**走中繼 launcher 機制（`SOULOS_LAUNCH_SERVICE_ONLY=1`）⇒ Plan A 只要**呼叫既有入口**即可同時獲得：① 不繼承呼叫端 pipe、② 服務不共用 Console、③ **單一啟動原語**（未來只需維護一處）。

**硬約束（必須逐條滿足並有守衛測試）**：
1. **不得遞迴**：Plan A → `server_ops.ps1 start` → 中繼 launcher → 服務，**不得**再回到 Plan A。必須以**靜態解析**證明呼叫圖無環（例：Plan A 呼叫 `start` 動詞，而 `start` 分支**不**呼叫 `_start_plan_a.ps1`）。
2. **不得改變 watchdog 的重試語意**：Plan A 的重試次數／退避／健康等待邏輯**逐字保留**。
3. **不得改變 pid 語意與 log 檔名**：`data\server.pid` 仍為 launcher pid；`:8000` socket owner 仍為其子行程；`data\server_nohup.log`／`.err` 不改名不改格式。
4. **零 kill 邏輯新增**；`Stop-Process`／`taskkill` 不得出現在 Plan A 的啟動路徑上（既有 `stop` 語意不動）。
5. **Plan A 必須在服務真正起來之後才回報成功**（不得把「已送出啟動」當成「已起來」）；若採方案 3.1，須沿用 `server_ops.ps1` 既有的一致性等待邏輯或自備等價等待（**以 `start` 的既有行為為準，不得放寬**）。
6. Plan A 呼叫 `server_ops.ps1` 時，**不得**把 watchdog 自己的 stdout pipe 傳下去（否則等於把繼承問題搬家）—— 這正是要靠中繼機制解決的部分；**不得**以「重導向」繞開。

### 3.2 備選：Plan A 自行建立同構的中繼 launcher

若 3.1 因遞迴或語意衝突不可行，則 Plan A **自己**以 ShellExecute（`bInheritHandles=FALSE`）建立中繼 launcher，再由 launcher 以 `.NET` 導向拉起服務。**此時必須把中繼原語抽成可共用的函式**（放在 `server_ops.ps1` 或新檔），**不得複製兩份實作**。
🔴 **不得**為此修改 `server_ops.ps1`（§2 已禁改）⇒ 若選此路，請**先回報**要求擴大寫入權，由主大腦裁示。

### 3.3 守衛測試（`tests\infra\test_plan_a_launch_inheritance.py`）

1. **無遞迴**：靜態解析（AST／字串）證明 Plan A → `server_ops.ps1 start` 的呼叫圖**無環**，且 `start` 分支不呼叫 `_start_plan_a.ps1`。
2. **不繼承呼叫端 pipe**：以無害子行程重播 Plan A 的啟動區塊，斷言被拉起的行程**不持有**呼叫端 stdout pipe（`GetFileType` ≠ `FILE_TYPE_PIPE`）。
3. **wrapper 確定返回**：呼叫端在限定時間內收到 stdout 的 **EOF**（事件量測，非預測）。
4. **服務在 wrapper 退出後仍存活**。
5. **輸出仍在 log 檔**、格式不變。
6. **pid 語意不變**：pid 檔＝launcher pid、`:8000` socket owner＝其子行程（以沙盒同構驗證）。
7. **watchdog 重試語意未變**（重試次數／退避／健康等待的靜態斷言）。
8. **生產未受影響**：測試前後生產 `data\server.pid` 與 `:8000` socket owner **逐值相同**；測試**不寫** `data\server_nohup.*`。

### 3.4 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 讓 Plan A 改回 `.NET` 導向直啟服務（回到舊機制） | 守衛 2（pipe 被持有） ＋ 守衛 3（收不到 EOF） |
| 2 | 讓 Plan A 呼叫 `server_ops.ps1` 的路徑形成環（`start` 反向呼叫 Plan A） | 守衛 1（無遞迴） |
| 3 | 讓 Plan A 在服務起來前就回報成功 | 守衛 3／4 |
| 4 | 讓 pid 檔改記服務子行程 pid | 守衛 6 |
| 5 | 讓啟動路徑寫入生產 `data\` | 守衛 8 |

🔴 全部針對**無害子行程**與**沙盒**；**不得**觸及生產服務、**不得**呼叫 `server_ops.ps1 start/stop/restart`（那會動到生產或啟動第二個實例）、**不得**對任何 PID 做終止操作。

## 4. 驗收（完成的定義）

1. `pytest -q tests\infra\test_plan_a_launch_inheritance.py` **全綠**（8 支）。
2. **5 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
3. **生產服務全程未受影響**：`:8000` socket owner 與 `data\server.pid` 測試前後逐值相同（**且 sha256 相同**）；`/health` 仍 200；`data\server_nohup.*` 未被測試寫入；無殘留沙盒行程。
4. `pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
5. 實作紀錄寫 `logs\SERVER-OPS-PLAN-A-LAUNCH-IMPLEMENTATION.md`（若未授予 `logs/**` 寫入權，則在回報中全文列出，由主大腦代寫）。
6. **🔴 生產端實機驗證不在本票範圍**：Plan A 真實觸發時的行為，須待**下一次服務異常或 Owner 授權重啟**時才確認。本票只交付「隔離可驗證」的程式碼。

## 5. 不做（Out of Scope）

- **不得**在本票重啟／啟動／停止生產服務，也**不得**主動觸發 Plan A（那會啟動第二個實例）。
- 不得改 `src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、`logs/**`、`scripts\server_ops.ps1`、其他 `scripts/**` 檔案。
- 不得新增 kill 邏輯；不得擴張行程護欄白名單；`tests/**` 內不得使用 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`（用 `ctypes` ＋ `_winapi`）。
- 不得改 watchdog 的重試語意、pid 語意、log 檔名。
- 不得複製兩份中繼 launcher 實作。
- **不得 commit／push**（由主大腦驗收後執行）。
- 不得跑 `scripts\manual_*.py`；不得觸發 synthetic diary／dream。

## 6. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `server_ops.ps1`（主路徑） | **不得修改**（已定案合入） |
| watchdog 重試語意 | 次數／退避／健康等待**逐字保留** |
| 呼叫圖 | Plan A → `server_ops.ps1 start` **不得成環** |
| `data\server.pid` 語義 | launcher pid；`:8000` socket owner 為其子行程 |
| log 檔名／格式 | `data\server_nohup.log`／`.err` **不改** |
| 「不得殺行程」鐵律 | 不得新增任何 kill 邏輯 |
| 行程護欄 | 白名單**大小鎖死 22**；`tests/**` 內零 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen` |

## 7. 回報格式

1. 改動檔案清單。
2. **採用的方案（3.1 或 3.2）與理由** ＋ 機制描述（誰建立誰、handle 如何不繼承、pid 如何交回、是否可能遞迴及其證明）。
3. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
4. 變異實驗：5 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
5. **生產未受影響的證據**（pid 檔內容與 sha256、socket owner、log 未被寫入、/health）。
6. **尚待下一次授權重啟／服務異常時做實機確認**的明確聲明。
7. 是否有踩到 Frozen Contract／意外行為。
