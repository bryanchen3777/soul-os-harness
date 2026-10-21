# 工單：SERVER-OPS-HANDLE-INHERITANCE — 讓服務**不繼承呼叫端 stdout pipe**，真正關掉「wrapper 不返回」的根因

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 04:25 EDT
> **前置**：`SERVER-OPS-LAUNCH-DETACH`（已交付：服務不再共用 Console；**但缺口仍在**，由 `tests/infra/test_server_ops_launch_detach.py::test_callers_stdout_is_released_while_the_service_lives` 以 **xfail** 釘住）
> **事故來源**：2026-10-21 02:10 DEPLOY-RESTART-21 的 wrapper 被 harness 600 s 牆鐘上限殺掉（服務僥倖存活）；2026-09-16 生產中斷事故為同一形狀。

---

## 0. 已知根因（已由前一票**實測**確立，不是推測）

`Start-Process -RedirectStandardOutput/-RedirectStandardError` 走 **.NET 的 `UseShellExecute=false`** 路徑，而 .NET 的 `CreateProcess` **一律以 `bInheritHandles=TRUE`** 建立子行程 ⇒ **服務連帶繼承呼叫端的 stdout pipe**。

**直接證據**（前一票的隔離沙盒實測）：
- 服務行程內 `GetFileType(呼叫端 pipe handle) = VALID`（`caller_pipe_in_child=VALID`）
- 呼叫端要等**服務退出**才收到 stdout 的 EOF（實測 20.54 s）
- 拿掉 `-NoNewWindow` **不會**關掉它（pipe 仍被服務持有）
- 「清除自身 handle 繼承旗標」**不可行**（`Process.Handles` 只取到 1 個可用 handle）

**後果**：呼叫端（harness 背景 shell）永遠等不到 EOF ⇒ 指令「看起來不返回」⇒ 被牆鐘上限**連坐砍掉整棵行程樹**。

## 1. 目標與**唯一驗收標準**

**讓 `tests/infra/test_server_ops_launch_detach.py::test_callers_stdout_is_released_while_the_service_lives` 由 xfail 變成 xpass（PASS）**，其餘 **11 支守衛維持綠燈**，且：

- `scripts\server_ops.ps1` 的五個動詞、`restart` 維護鎖流程、`data\server.pid` 語意、log 檔名、**零 kill 邏輯** 全部不變。
- **生產服務全程不受影響**（本票不重啟／不啟動／不停止生產）。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `scripts\server_ops.ps1`（**修改**） | 改變**服務的啟動機制**，使服務不繼承呼叫端 stdout pipe |
| 2 | `scripts\server_ops_launch_relay.ps1`（**新增，可選**） | 中繼 launcher（若採方案 A） |
| 3 | `tests\infra\test_server_ops_launch_detach.py`（**修改**） | 把那支 xfail 改成正常斷言（**不是**移除／不是 `strict=False` 放行） |
| 4 | `tests\infra\test_server_ops_handle_inheritance.py`（**新增**） | 針對 handle 繼承的守衛測試 ＋ 變異實驗 |

**不得動**：`src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、`logs/**`、其他 `scripts/**` 檔案（尤其 `scripts\eval_lifecycle_cli.py`／`recall_readiness_cli.py`／`sim_accel_*.py`）。

## 3. 做法（決策已定；機制可由你在這兩條已實測方向中擇一，但**不得**超出）

### 方案 A（建議）：以 `bInheritHandles=FALSE` 建立的中繼 launcher 拉起服務

- 用 **ShellExecute**（`UseShellExecute=true`）建立一個**薄 launcher**（PowerShell 腳本或小型可執行檔）：ShellExecute 路徑以 `bInheritHandles=FALSE` 建立行程 ⇒ **launcher 不繼承呼叫端的 pipe**。
- **由 launcher** 再以 `Start-Process -RedirectStandardOutput/-RedirectStandardError` 拉起真正的服務 ⇒ 服務只繼承 **launcher 的** handle，**不繼承呼叫端的 pipe** ⇒ 呼叫端立即收到 EOF。
- **pid 語意不變**：`data\server.pid` 仍存 **launcher pid**（trampoline），`:8000` 的 socket owner 仍是其**子行程**（既有且已驗證的拓撲）。
  - launcher 必須把服務子行程的 pid 交回（暫存檔或 stdout 解析，**由你選定並在報告說明**），以維持 pid 語意與 `stop` 路徑的相容性。
  - 🔴 **launcher 不得因呼叫端退出而被中止**（它是服務的父行程，必須活著；或採用不依賴父行程存活的啟動法）。**不得**把服務掛到任何會被連坐砍掉的 job object。
- launcher 自身的 stdout 不需要導向呼叫端（它是薄殼），但**不得**因此讓服務失去 log 輸出。

### 方案 B（備案）：P/Invoke `CreateProcessW`

- 直接以 `CreateProcessW`（`bInheritHandles=FALSE` ＋ `STARTUPINFO` 的 `hStdOutput/hStdError` 指到 log 檔）建立服務行程 ⇒ 服務不繼承任何呼叫端 handle。
- 必須自行處理 pid 回傳與 `data\server.pid` 寫入（**pid 語意不變**）。

**兩方案的共同硬約束**：
1. **不得**使用 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`（行程護欄 `tests\infra\test_no_production_spawn_guard.py` 白名單**大小鎖死 22**，不得擴張；新測試也不得用）。可用 `ctypes`＋`_winapi`。
2. **不得**新增任何 kill 邏輯；`Stop-SoulOsServer` 的既有行為**不動**。
3. `start`／`stop`／`status`／`tail`／`restart` 的參數與行為**逐字保留**；`restart` 的維護鎖流程（寫鎖 → stop → sleep 2 → start → finally 移除鎖 ＋ ops log）**逐字保留**。
4. `data\server_nohup.out`／`.err` 的**檔名與內容格式不變**。
5. `data\server.pid` 語義不變（launcher pid；`:8000` socket owner 為其子行程）。

### 3.1 把 xfail 改成正常斷言（**要點**）

- `test_callers_stdout_is_released_while_the_service_lives` 必須從 `xfail` 改為**正常斷言並通過**。
  🔴 **不得**用「移除該測試」「把 `strict=False` 改成放行」「把 xfail 標成 xpass 來騙過」等方式處理 —— 那是把缺口藏起來。**它必須真的變綠**。
- 若採用方案 A 且需要新增 relay 腳本，**在 `tests/**` 內不得新增 `subprocess.*` 呼叫點**（護欄大小鎖死）。

## 4. 守衛測試與變異實驗

### 4.1 `tests\infra\test_server_ops_handle_inheritance.py`（新增）

1. **handle 層級的直接證據**：服務行程內 `GetFileType(呼叫端 stdout pipe handle) != VALID`（或等價斷言：服務不持有呼叫端的 pipe）。
2. **wrapper 確定返回**：啟動後呼叫端在限定時間內收到 stdout 的 **EOF**（實測事件量測，不是預測）。
3. **服務在 wrapper 退出後仍存活**。
4. **輸出仍在 log 檔**且格式不變。
5. **pid 語意不變**：`data\server.pid` ＝ launcher pid；`:8000` socket owner ＝ 其子行程（用無害子行程沙盒驗證，**不得**對生產驗證）。
6. **生產未受影響**：測試前後生產 `data\server.pid` 與 `:8000` socket owner **逐值相同**；測試**不寫** `data\server_nohup.*`。

### 4.2 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 讓服務回到繼承呼叫端 pipe 的啟動法（退回 `.NET` 導向直啟） | 守衛 1（handle 仍 VALID） ＋ 守衛 2（wrapper 收不到 EOF） |
| 2 | 讓 wrapper 等待服務退出（`-Wait`） | 守衛 2 |
| 3 | 讓服務不繼承 launcher（launcher 用 `.NET` 導向直啟服務） | 守衛 1 |
| 4 | 讓 pid 檔改記服務子行程 pid（破壞 pid 語意） | 守衛 5 |
| 5 | 讓啟動路徑寫入生產 `data\` | 守衛 6 |

🔴 全部變異針對**無害子行程**與**沙盒**；**不得**觸及生產服務、不得呼叫 `server_ops.ps1 start/stop/restart`、不得對任何 PID 做終止操作。

## 5. 驗收（完成的定義）

1. **`test_callers_stdout_is_released_while_the_service_lives` 變成 PASS**（原 xfail），其餘 11 支守衛**維持綠燈** ⇒ `pytest -q tests/infra/test_server_ops_launch_detach.py` → **12 passed, 0 xfailed**。
2. 新檔 `tests\infra\test_server_ops_handle_inheritance.py` **全綠**。
3. **5 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
4. **生產服務全程未受影響**：`:8000` socket owner 與 `data\server.pid` 測試前後逐值相同；`/health` 仍 200；`data\server_nohup.*` 未被測試寫入。
5. `pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
6. **🔴 生產端實機驗證不在本票範圍**：真實 `restart` 後 wrapper 是否確定返回，須待**下一次 Owner 授權重啟**時才確認。本票只交付「隔離可驗證」的程式碼。
7. 實作紀錄寫 `logs\SERVER-OPS-HANDLE-INHERITANCE-IMPLEMENTATION.md`（若 §2 未授予 `logs/**` 寫入權，則在回報中全文列出並由主大腦代寫）。

## 6. 不做（Out of Scope）

- **不得**在本票重啟／啟動／停止生產服務（`:8000` 正在跑）。
- 不得改 `src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、`logs/**`、其他 `scripts/**` 檔案。
- 不得新增 kill 邏輯；不得擴張行程護欄白名單；不得使用 `subprocess.*`／`os.spawn*`。
- 不得改五個動詞、`restart` 維護鎖流程、pid 語意、log 檔名。
- 不得以「移除測試／放行 xfail」的方式關閉缺口。
- **不得 commit／push**（由主大腦驗收後執行）。
- 不得跑 `scripts\manual_*.py`；不得觸發 synthetic diary／dream。

## 7. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `server_ops.ps1` 五動詞 ＋ `restart` 維護鎖流程 | **逐字保留** |
| `data\server.pid` 語義 | launcher pid；`:8000` socket owner 為其子行程（既有拓撲，**不改**） |
| log 檔名／格式 | `data\server_nohup.out`／`.err` **不改** |
| 「不得殺行程」鐵律 | **不得**引入任何 kill 邏輯 |
| 行程護欄 `tests\infra\test_no_production_spawn_guard.py` | 白名單**大小鎖死 22**，不得擴張；`tests/**` 內不得新增 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen` |
| 前一票的 11 支守衛 | **必須維持綠燈**（本票只解 xfail 那一支） |

## 8. 回報格式

1. 改動檔案清單。
2. **採用的方案（A 或 B）與理由** ＋ 機制描述（誰建立誰、handle 如何不繼承、pid 如何交回、launcher 生命週期）。
3. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**；**並明確說明 `test_callers_stdout_is_released_while_the_service_lives` 已由 xfail 轉為 PASS**。
4. 變異實驗：5 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
5. **生產未受影響的證據**（pid 檔、socket owner、log 未被寫入、/health）。
6. **尚待下一次授權重啟做實機確認**的明確聲明。
7. 是否有踩到 Frozen Contract／意外行為。
