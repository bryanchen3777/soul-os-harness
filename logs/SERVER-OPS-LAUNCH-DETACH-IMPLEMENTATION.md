# SERVER-OPS-LAUNCH-DETACH 實作紀錄（2026-10-21 04:20 EDT）

> 工單：`logs/SERVER-OPS-LAUNCH-DETACH-WORK-ORDER.md`（主大腦開立，AGENTS.md 行程安全鐵律第 11 條）
> 狀態：**程式碼已交付且驗證通過；但工單對事故機制的假設被實測推翻，缺口已用 xfail 釘住並移交下一張票。**

## 1. 一句話結論

`server_ops.ps1` 的啟動呼叫**已改為不與呼叫端共用 Console**（服務拿到自屬 Console，實測 `GetConsoleProcessList=2`、hwnd 與呼叫端不同），輸出仍導向既有 `data\server_nohup.log`／`.err`，pid 語意不變；**但真正讓 wrapper 不返回的成因不是 `-NoNewWindow`**，實測證明是 **`.NET` 的 `Start-Process -RedirectStandard*` 強制 `bInheritHandles=TRUE` ⇒ 服務繼承呼叫端 stdout pipe**。拿掉 `-NoNewWindow` **不會**關掉這個缺口。

## 2. 改動

| 檔案 | 動作 |
|---|---|
| `scripts/server_ops.ps1` | **+28／−1**：啟動呼叫移除 `-NoNewWindow`；保留 `-RedirectStandardOutput $outLog`／`-RedirectStandardError $errLog`／`-PassThru`／`-WorkingDirectory $root` 與 `$proc.Id → $pidFile`；加入實測依據與缺口註解。**BOM／CRLF 保持**（247 CRLF、0 LF-only、parse_errors=0、`Start-Process` 恰 1 處） |
| `tests/infra/test_server_ops_launch_detach.py` | **新增**（850 行）：隔離守衛測試 12 支（11 ＋ 1 xfail）＋ 5 條變異實驗 |

未動：`src/**`、`data/**`、`personas/**`、`configs/**`、`docs/**`、其他 `scripts/**`。**無任何 kill 邏輯新增**（`taskkill` 仍恰 2 處且都在 `Stop-SoulOsServer`）。**未使用 `subprocess.*`／`os.spawn*`**（行程護欄白名單維持 22、size 未動）⇒ 改用 `ctypes`＋`_winapi`。

## 3. 測試（解譯器＝repo 的 `.venv` Python 3.11.15）

- `pytest -q tests/infra/test_server_ops_launch_detach.py` → **11 passed, 1 xfailed**（7.02 s；主大腦獨立複核同結果）
- `pytest -q tests` → **6780 passed, 24 skipped, 1 xfailed, 27 subtests passed，0 failed**（794.62 s）

**11 支守衛**：無 `-NoNewWindow`／導向仍在／pid 語意與 `-PassThru` 未變／五動詞與 restart 維護鎖流程未變／無新增 kill 邏輯／啟動路徑不寫生產 data／wrapper 確定返回／服務在 wrapper 退出後仍存活／輸出導向到 log 檔／服務不共用呼叫端 Console／生產狀態未被測試改動。
**1 支 xfail**：`test_callers_stdout_is_released_while_the_service_lives`（**已知缺口**，修好後會 xpass）。

## 4. 變異實驗（5 條全紅；全部施加於腳本文字、在無害子行程上重播，零生產接觸）

| # | 變異 | 轉紅的守衛 | 紅燈實測 |
|---|---|---|---|
| M1 | 改回 `-NoNewWindow` ＋ 拿掉導向 | 靜態無 `-NoNewWindow`／靜態導向存在／守衛3／守衛5 | **4 failed** |
| M2 | 只拿掉導向（輸出回到呼叫端） | 靜態導向存在／守衛3／守衛5 | **3 failed**（另 1 支 **xpassed**：無導向即無 handle 繼承 ⇒ **反證缺口來自 `.NET` 導向**） |
| M3 | 服務隨 wrapper 退出而中止 | **守衛2** | **1 failed**（僅 `test_service_outlives_the_wrapper`） |
| M4 | 啟動路徑改寫 `data\server.pid` | **守衛4** | **7 failed**（含 `test_launch_path_never_writes_into_production_data`；**生產 pid 檔未被寫入**） |
| M5 | 啟動呼叫加 `-Wait`（wrapper 等服務） | **守衛1** | **2 failed**（含 `test_wrapper_returns_deterministically`） |

**方法學修正紀錄**：M3 初次「轉紅」其實是探針的 `NameError` 而非變異生效；改用 `WaitForSingleObject`（Windows 上已終止行程的 pid 仍可被 `OpenProcess` 開啟，故 pid 存在性不能當死亡判準）後**精確只轉紅守衛2**。M2 的 xpass 是**反證**，價值高於紅燈本身。

## 5. 生產未受影響的證據（全庫回歸前後）

- `data\server.pid`：`10040` → `10040`，sha256 `A882FF179A8CE513BF4518105E1ADBA2369DDA157484C684059EEC7AE1F1B061` → **完全相同**
- `:8000` socket owner：`19744` → `19744`（未變）
- `python.exe` 行程數：`4` → `4`（無孤兒；服務樹 10040→19744 存活自 02:10:31 未受影響）
- 生產 `data\server_nohup.*` 由上線中的服務自己寫入（mtime 前進），**測試全程未寫入**。守衛4 以「啟動路徑的寫入目標全部落在沙盒」作**確定性**證據（不用雜湊比對，因服務本身會 append ⇒ 那是天然的不穩定守衛）

## 6. ⚠ 工單前提的實測修正（已移交下一張票）

**工單假設**：「`-NoNewWindow` 與呼叫端共用 Console」是 600 秒逾時的成因。
**實測**：不是。真正成因是 **`.NET` 的 `Start-Process -RedirectStandard*` 強制 `UseShellExecute=false`，而 `.NET` 的 `CreateProcess` 一律以 `bInheritHandles=TRUE` 建立子行程 ⇒ 服務連帶繼承呼叫端的 stdout pipe**。
**直接證據**：服務行程內 `GetFileType(呼叫端 pipe handle) = VALID`（`caller_pipe_in_child=VALID`）；呼叫端要等**服務退出**才收到 stdout 的 EOF（實測 20.54 s）。
⇒ 後果：harness 的背景 shell 永遠等不到 EOF ⇒ 指令看起來「不返回」⇒ 被牆鐘上限連坐砍掉整棵行程樹（**2026-09-16 事故形狀**；2026-10-21 02:10 的 DEPLOY-RESTART-21 即是被 600 s 上限殺掉 wrapper，服務僥倖存活）。
⇒ **拿掉 `-NoNewWindow` 不會**關掉這個缺口（實測：pipe 仍被服務持有）。
⇒ 實測排除的方案：「清除自身 handle 繼承旗標」不可行（`Process.Handles` 只取到 1 個可用 handle）。

**建議方案（已由 worker 實測方向，需另開票實作）**：
1. **中繼 launcher**：以 `ShellExecute`（⇒ `bInheritHandles=FALSE`）建立一個薄 launcher，**由 launcher** 再做 `Start-Process -RedirectStandard*` 拉起服務 ⇒ 服務只繼承 launcher 的 handle、**不繼承呼叫端的 pipe**；服務 pid 經暫存檔交回，**pid 語意不變**。
2. **備案**：P/Invoke `CreateProcessW`（`bInheritHandles=FALSE` ＋ `STARTUPINFO` 指定檔案 handle）。

## 7. 範圍衝突的處理

工單 §4.6 要寫 `logs\SERVER-OPS-LAUNCH-DETACH-IMPLEMENTATION.md`，但同工單 §2 的寫入權只有 2 個檔案、§5 與交辦均明列「不得改 `logs/**`」。worker 按**較嚴**的指示**沒有**寫入 `logs/`；本檔由**主大腦**補建（主大腦對 `logs/**` 有寫入權）。內容與 worker 報告全數一致。

## 8. 尚未關閉的缺口（移交）

`test_callers_stdout_is_released_while_the_service_lives` 仍為 **xfail** ⇒ **下一次授權重啟時，wrapper 仍可能被牆鐘上限殺掉**（服務仍會僥倖存活，但那是運氣）。
**下一張票**：`SERVER-OPS-HANDLE-INHERITANCE` —— 驗收標準＝**讓那支 xfail 測試變 xpass**，其餘 11 支守衛維持綠燈，五動詞與 pid／log 語意不變，**且本票同樣不得重啟／啟動／停止生產服務**。
