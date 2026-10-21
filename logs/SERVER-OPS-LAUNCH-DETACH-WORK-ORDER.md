# 工單：SERVER-OPS-LAUNCH-DETACH — 讓 `server_ops.ps1` 啟動的服務脫離 Console，消除「harness 超時連坐殺服務」的結構性風險

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 02:25 EDT
> **依據**：AGENTS.md 行程安全鐵律第 11 條 —— 「若因 `-NoNewWindow` 咬住 stdout 而導致 wrapper 不退出：**修 `server_ops.ps1` 的啟動方式**（改用脫離 Console 的啟動、或導向輸出），**不要靠手動砍 wrapper**」
> **觸發實測**：2026-10-21 02:10:25 DEPLOY-RESTART-21 以背景 task 執行 `.\scripts\server_ops.ps1 restart`；重啟本身**成功**（PID 9504→19744、trampoline 26336→10040、VC 三實例未受干擾、/health 200、ERROR 0），但 **wrapper 因 `-NoNewWindow` 共享 Console、stdout 不關閉而一直不退出**，最終由 harness 的 600 秒牆鐘上限殺掉（`Command timed out after 600 seconds`）。**本次服務僥倖存活**（已逐項複核：19744／10040 存活、VC 三實例不變、/health 200、python.exe 全清單僅 4 筆無孤兒）—— **但這是運氣，不是安全性**。2026-09-16 事故就是這個形狀（harness 逾時中止命令時，連坐殺掉生產服務）。

---

## 1. 目標

讓 `scripts\server_ops.ps1` 啟動的服務**不與呼叫端共享 Console**，且 wrapper 在服務啟動完成後**確定返回**（不因 stdout 未關閉而掛住）⇒ 以後任何 harness／CI 的牆鐘上限都不可能連坐殺到生產服務。

**同時維持**：服務的可觀測性（log 仍寫入 `data\server_nohup.err`）、`data\server.pid` 的語意、`status`／`stop`／`tail` 的行為、以及**重啟路徑的正確性**（維護鎖 → stop → start → finally 移除鎖）。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `scripts\server_ops.ps1`（**修改**） | `Start-SoulOsServer` 的啟動方式：脫離 Console ＋ 導向輸出 |
| 2 | `tests\infra\test_server_ops_launch_detach.py`（**新增**） | 隔離驗證守衛測試（**不得**重啟／不得碰生產服務） |

**不得動**：`src/**`、`data/**`、`personas/**`、`configs/**`、`logs/**`、`docs/**`、`scripts/` 內其他檔案（尤其 `scripts\eval_lifecycle_cli.py`／`scripts\recall_readiness_cli.py`／`scripts\sim_accel_*.py`）。

## 3. 做法（決策已定，執行者照做）

### 3.1 `Start-SoulOsServer` 的啟動方式

**現況**（`scripts\server_ops.ps1` 約 109 行）：`Start-Process -FilePath $python -NoNewWindow -PassThru` ⇒ 新行程與呼叫端**共享 Console**，且其 stdout/stderr 繫在呼叫端 ⇒ wrapper 不返回。

**改為**：把服務的 stdout/stderr **導向既有 log 檔**（`data\server_nohup.out`／`data\server_nohup.err`，路徑沿用既有常數，**不得改 log 檔名**），並**不與呼叫端共享 Console**。
- 目標語意：① wrapper 在服務啟動完成（pid 寫入 `data\server.pid`、埠 LISTENING）後**確定返回**；② 服務不因 wrapper 退出而中止；③ log 內容與行位元格式維持既有樣式。
- **不要**自行發明 Console 隱藏技巧（`Start-Process -WindowStyle Hidden` 在 `-NoNewWindow` 互斥、且可能造成 uvicorn 的 stdout 失效）。**以「導向輸出 ＋ 不共享 Console」為準**，若 PowerShell 5.1 對脫離 Console 的啟動有限制，**在檔內註明限制並讓 `start` 在該情形下明確報錯退出（不得留下半啟動狀態）**，而不是靜默退化成共享 Console。
- 維持 `Start-Process -PassThru` 取得 pid，寫入 `data\server.pid` 的語意**不變**（pid 檔 = trampoline／launcher pid；`:8000` 的 socket owner 是其子行程 —— 這是既有且已驗證的拓撲，**不得**改變它）。
- **不得**改動 `Stop-SoulOsServer` 的行為、**不得**加入任何 kill 邏輯（鐵律 1：任何 agent／任何理由都不得殺行程；`stop` 分支的既有行為不動）。
- `restart` 分支的**維護鎖流程逐字保留**（寫鎖 → stop → `Start-Sleep 2` → start → finally 移除鎖 ＋ 寫 ops log）。

### 3.2 隔離驗證守衛測試（`tests\infra\test_server_ops_launch_detach.py`）

🔴 **本測試絕對不得重啟、啟動或停止生產服務**（`:8000` 正在跑）。驗證對象是**啟動原語本身**，用一個無害的子行程（例如 `python -c "import time; ..."` 睡數秒後退出）驗證：
1. **新啟動路徑會讓 wrapper 返回**（不因 stdout 未關閉而掛住）⇒ 以「啟動後 wrapper 在 N 秒內返回」斷言。
2. **被啟動的子行程在 wrapper 退出後仍存活**（這是本次事故的根因防線）⇒ 以「wrapper 返回後子行程仍在」斷言。
3. **輸出被導向指定檔案**（log 內容可讀）⇒ 斷言檔案有內容。
4. **不影響生產**：斷言測試期間生產 `data\server.pid` 與 `:8000` 的 socket owner **未變**（以測試前後的 pid 值比對），且測試**不寫** `data\server_nohup.*`。

⚠️ **行程建立護欄**：`tests\infra\test_no_production_spawn_guard.py` 把 `tests/**` 內**每一個** `subprocess.*` 呼叫點釘成 `(檔案, 行號)` 白名單且**大小鎖死在 22** ⇒ **本測試不得使用 `subprocess.*`**。改用 `os.spawnv`／`os.posix_spawn` 等非 `subprocess.*` 的 API（或以 `Start-Process` 於 PowerShell 子行程內做同樣驗證）。**不得**擅自擴張該護欄的白名單。

⚠️ 若需要以 PowerShell 驗證 `Start-SoulOsServer` 的新路徑，**不得**呼叫 `server_ops.ps1 start`（那會啟動第二個生產實例）；只能**靜態解析**該腳本（AST／字串斷言）確認「不再使用 `-NoNewWindow`」且「輸出已導向」，外加用無害子行程做上述 1~4 項的動態驗證。

### 3.3 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 把啟動改回 `-NoNewWindow`（不導向輸出） | 守衛 1／3（wrapper 不返回 ＋ log 無內容） |
| 2 | 讓輸出仍繫在呼叫端（不重定向） | 守衛 3（log 無內容） |
| 3 | 讓被啟動的子行程隨 wrapper 退出而中止 | 守衛 2（wrapper 返回後子程序已死） |
| 4 | 讓新啟動路徑也去寫 `data\server.pid`（造成生產 pid 檔被覆寫） | 守衛 4（生產 pid 未變） |

🔴 **變異實驗安全**：全部針對**無害子行程**與**靜態解析**，**不得**觸及生產服務、不得呼叫 `server_ops.ps1 start`／`stop`／`restart`、不得對任何 PID 做終止操作。

## 4. 驗收（完成的定義）

1. `pytest -q tests/infra/test_server_ops_launch_detach.py` **全綠**。
2. **生產服務全程未受影響**（測試前後 `:8000` socket owner 與 `data\server.pid` **逐位元／逐值相同**；`/health` 仍 200；`data\server_nohup.err` 未被測試寫入）。
3. **靜態斷言**：`scripts\server_ops.ps1` 中**不再出現** `-NoNewWindow`（允許以註解說明歷史原因，但不得出現在實際啟動呼叫），且輸出導向存在。
4. **4 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
5. `pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
6. 實作紀錄寫 `logs\SERVER-OPS-LAUNCH-DETACH-IMPLEMENTATION.md`。

## 5. 不做（Out of Scope）

- **不得**在本票重啟／啟動／停止生產服務（`:8000` 正在跑；驗收本票不需要動它）。
- 不得改 `src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、`logs/**`、其他 `scripts/**` 檔案。
- 不得新增任何 kill 邏輯（`Stop-Process`／`taskkill`／`pkill`）。
- 不得改動 `data\server.pid` 的語意（pid 檔 = launcher pid；`:8000` socket owner = 其子行程）。
- 不得改動 log 檔名（`data\server_nohup.out`／`data\server_nohup.err`）。
- **不得 commit／push**（由主大腦驗收後執行）。
- 🔴 **本票的生產端驗證（真實 `restart` 後 wrapper 確定返回）須另開 Owner 授權** —— 在下一次授權重啟時才做實機確認；本票只交付「隔離可驗證」的程式碼。

## 6. Frozen Contract 注意

| 契約 | 限制 |
|---|---|
| `server_ops.ps1` 的 `start`／`stop`／`status`／`tail`／`restart` 五個動詞 | 參數與行為**逐字保留**（`ValidateSet` 不動） |
| `restart` 的維護鎖流程 | 逐字保留（寫鎖 → stop → sleep 2 → start → finally 移除鎖 ＋ ops log） |
| `data\server.pid` 語義 | launcher pid；`:8000` socket owner 為其子行程（既有拓撲，不改） |
| log 檔名 | `data\server_nohup.out`／`data\server_nohup.err` 不改 |
| 行程護欄 `tests\infra\test_no_production_spawn_guard.py` | 白名單**大小鎖死 22**，不得擴張；新測試不得用 `subprocess.*` |
| 「不得殺行程」鐵律 | 本票**不得**引入任何 kill 邏輯 |

## 7. 回報格式

1. 改動檔案清單。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：4 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
4. **生產未受影響的證據**：測試前後 `:8000` socket owner 與 `data\server.pid` 的比對、log 檔未被寫入的證據。
5. 新啟動路徑的行為描述（wrapper 如何返回、輸出導向到哪、服務是否脫離 Console），以及**尚待下一次授權重啟做實機確認**的明確聲明。
6. 是否有踩到 Frozen Contract／意外行為。
