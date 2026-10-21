# SERVER-OPS-HANDLE-INHERITANCE — 實作紀錄

日期：2026-10-21 05:10 EDT　分支：main　基準 commit：`6962c2a`　（改動由主大腦驗收後提交）

## 0. 結論

`scripts/server_ops.ps1` 的啟動機制由「呼叫端直接以 .NET 導向拉起服務」改為 **「呼叫端 → 中繼 launcher → 服務」兩層**。呼叫端以**不帶** `-RedirectStandard*` 的 `Start-Process` 啟動中繼 launcher（PS 5.1 → **ShellExecute** 路徑 → `CreateProcess` **`bInheritHandles=FALSE`**）⇒ 中繼 launcher **不持有**呼叫端 stdout pipe；服務改由中繼 launcher 以 .NET 導向拉起 ⇒ 服務只繼承 launcher 的 handle（既有 nohup log 檔）。

**後果**：呼叫端在**服務仍活著**時就收到 stdout 的 EOF ⇒ **wrapper 確定返回** ⇒ 不會再被牆鐘上限連坐砍掉整棵行程樹（2026-09-16 事故形狀）。

**唯一驗收標準達成**：`test_callers_stdout_is_released_while_the_service_lives` **已由 xfail 轉為 PASS**（不是移除、不是 `strict=False` 放行、不是 xpass 騙過）。

## 1. 改動檔案

| 檔案 | 動作 |
|---|---|
| `scripts/server_ops.ps1` | **修改**（+64／−32）：新增中繼 launcher 模式（`SOULOS_LAUNCH_SERVICE_ONLY=1`）與一般模式的 launcher 啟動；**服務啟動原語逐字保留**在本檔（第一個 `Start-Process -FilePath` 仍是 `$python`，仍帶 `-RedirectStandardOutput $outLog`／`-RedirectStandardError $errLog`／`-PassThru`，pid 仍寫 `$proc.Id | Out-File -Encoding utf8 $pidFile`）。BOM／CRLF 保持（279 CRLF、0 LF-only、parse_errors=0） |
| `tests/infra/test_server_ops_launch_detach.py` | **修改**（+171／…）：xfail → 正常斷言；沙盒重播改兩層拓撲；handle 量測改比對 `GetFileType`；變異套件重新落點。**11 支舊守衛的斷言本體一行未改** |
| `tests/infra/test_server_ops_handle_inheritance.py` | **新增**：守衛 1~6 ＋ 5 條變異實驗 |

## 2. 拓撲與機制

```
呼叫端 (server_ops.ps1)                ← stdout = harness 的 pipe
  └─ Start-Process powershell.exe      ← **不帶** -RedirectStandard*
       （ShellExecute 路徑 → bInheritHandles=FALSE）
       中繼 launcher = server_ops.ps1 自身（環境變數 SOULOS_LAUNCH_SERVICE_ONLY=1）
        └─ Start-Process $python -RedirectStandardOutput $outLog -RedirectStandardError $errLog
             （.NET UseShellExecute=false → bInheritHandles=TRUE，但只繼承 launcher 的 handle）
             服務 = .venv python shim（pid 檔記的就是它）
              └─ 真直譯器（:8000 socket owner）
```

- **handle 不繼承**：launcher 以 ShellExecute 建立 ⇒ 不持有呼叫端 pipe；服務只繼承 launcher 的 handle ⇒ 呼叫端立即收到 EOF。
- **pid 交回**：launcher 以 `-PassThru` 取得服務 trampoline pid 寫入 `data\server.pid`；呼叫端 `WaitForExit(60000)` 後讀回記進 ops log。⇒ **pid 檔語意與改動前完全相同**（trampoline pid；socket owner 仍是其子行程）。
- **launcher 生命週期**：拉起服務後**立即結束**；服務不依賴父行程存活（沙盒實測 wrapper 退出後服務仍活）。**未掛進任何 job object，kill 邏輯零新增。**
- **launcher 命令列刻意不含 `run_server.py`**：`Get-ServerProcess` 以 `*run_server.py*` 比對命令列，含了會讓 `start` 的「已在執行」判斷誤判；服務腳本路徑由 launcher 依 `$root` 自行推導。

**為什麼選方案 A 而非 B**：B（`CreateProcessW`）已實測可行（EOF 0.59 s），但會踩到兩條既有契約 —— `tests/test_server_ops_python_path_v1.py` 以註解不感知正則抓本檔**第一個** `Start-Process -FilePath` 並斷言參數須為字面 `$python`（B 根本沒有 `Start-Process` ⇒ 該測試直接 error），且前一票守衛 2 要求第一個 `Start-Process` 必須帶三個旗標。A 把「直接拉起服務」搬進中繼層後這兩條**逐字成立**。

## 3. 測試（repo `.venv` Python 3.11.15；主大腦獨立複核）

| 指令 | 結果 |
|---|---|
| `pytest -q tests/infra/test_server_ops_launch_detach.py` | **12 passed, 0 xfailed** |
| `pytest -q tests/infra/test_server_ops_handle_inheritance.py` | **7 passed** |
| `pytest -q tests`（全庫） | **6788 passed, 24 skipped, 27 subtests passed, 0 failed**（780.71 s） |

**主大腦獨立複核**：兩檔合計 **19 passed, 0 xfailed**（9.98 s），`test_callers_stdout_is_released_while_the_service_lives` 在**兩個檔皆 PASSED**。

**⚠️ 一處量測修正**：舊斷言 `caller_pipe_in_child != "VALID"`（只看 handle 是否有效）**無法分辨**「仍持有呼叫端 pipe」與「handle slot 已被別的 handle 占用」（後者 `GetFileType` 回 0）。現改為直接比對 **FileType**（呼叫端 stdout 是 pipe ⇒ 真繼承到必為 `FILE_TYPE_PIPE(0x3)`），並同時保留 EOF 事件量測。證據：變異 M1 下該斷言**確實轉紅**。

## 4. 變異實驗（10 條全轉紅）

**新檔 `SOULOS_HANDLE_MUTATION`**：M1 launcher 改用 .NET 導向（繼承呼叫端 pipe）→ 守衛1+2 紅（2 failed）；M2 launcher 等服務退出 → 守衛2 紅（1 failed）；M3 launcher 消失、服務由呼叫端直啟 → 守衛1 紅（2 failed）；M4 pid 檔改記 socket owner → 守衛5 紅（1 failed）；M5 啟動路徑寫生產 `data\server.pid` → 守衛6 紅（7 failed，**重播被拒 ⇒ 生產 pid 檔未被寫入**，已用 sha256 復核）。

**前一票 `SOULOS_LAUNCH_MUTATION`**（變異落點因兩層拓撲而重新對位）：M1 → 5 failed（含 Console 守衛）；M2 → 4 failed；M3 → 2 failed；M4 → 8 failed（重播拒絕執行，**生產 pid 檔未被寫入**）；M5 → 3 failed（含 `test_wrapper_returns_deterministically`）。

🔴 紅燈皆來自**真實斷言失敗**；死亡判準一律用 `WaitForSingleObject`（非 pid 存在性）；M4／M5 的變異被**靜態寫入目標分析**攔下，**沒有任何路徑觸及生產**。

## 5. 生產未受影響的證據

| 項目 | 改動前 | 回歸後 |
|---|---|---|
| `data\server.pid` 內容 | `10040` | `10040` |
| `data\server.pid` sha256 | `A882FF179A8CE513BF4518105E1ADBA2369DDA157484C684059EEC7AE1F1B061` | **完全相同**（主大腦複核 `A882FF179A8CE513…`） |
| `data\server.pid` mtime | 2026-10-21 02:10:31 | **02:10:31**（未被寫入） |
| `:8000` socket owner | `19744` | `19744` |
| 服務行程 | 10040／19744，started 02:10:31 | 同上（**未重啟**） |
| `data\logs\server_nohup.*` 輪替備份 | 最新 `20261021_021031` | 同（**未觸發輪替**） |
| `data\server_nohup.log/.err` | 只含 uvicorn `/health` access log | 同（**無 probe 輸出 ⇒ 測試未寫生產 log**） |
| 沙盒殘留行程 | — | **none**（以 release 檔讓服務自行結束） |

本票全程**未啟動／重啟／停止任何生產行程**（`Stop-Process`／`taskkill` 一次都沒發出）。

## 6. Frozen Contract 檢查

五動詞 `ValidateSet` **逐字未變**；`restart` 維護鎖流程（寫鎖→stop→sleep 2→start→finally 移除鎖＋ops log）**逐字未變**；`taskkill` 仍恰 2 處且都在 `Stop-SoulOsServer` 內；`data\server.pid` 語義**未變**；log 檔名／格式**未變**；前一票 11 支守衛**維持綠燈**（`git diff` 顯示**只有 xfail 那一支的斷言本體被改**）；行程護欄白名單**維持 22**、**零** `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`（新檔一律 `ctypes`＋`_winapi`）；**未 commit／push**。

## 7. 🔴 尚待裁示／後續票

1. **`scripts\_start_plan_a.ps1`（watchdog 的 Plan A 啟動路徑）仍有同一根因**：`_start_plan_a.ps1:88-93` 用 `Start-Process … -RedirectStandard* -NoNewWindow` ⇒ 服務仍會繼承呼叫端 stdout pipe 且共用 Console。**不在本票授權範圍（§6 禁改其他 `scripts/**`），故只回報不動手** ⇒ 已開 `SERVER-OPS-PLAN-A-LAUNCH` 票。
2. **🔴 生產端實機驗證不在本票範圍**：真實 `restart` 後 wrapper 是否確定返回，須待**下一次 Owner 授權重啟**時確認。**生產端目前仍跑舊的啟動機制**（`:8000` 仍是 02:10:31 啟動的實例）。
