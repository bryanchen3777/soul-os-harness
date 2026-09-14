# PROC-DEATH-FORENSICS-1 — 生產服務之死鑑識登記

> **本文由協調者依 auditor 唯讀鑑識回報整理。**
> 性質：**FORENSICS / REGISTRATION（純文件）** —— 0 程式碼、0 服務重啟、0 `data/**` 寫入。
> 範圍：只新增本文；`logs/ENGINEERING_STATE.md` 僅追加登記（既有內容一字未刪）。
> 所有數字皆由協調者獨立複驗（複驗方式見 §5）。

---

## 1. 結論：生產服務之死 ＝ DSH harness 重啟的連坐（非 Soul OS 崩潰）

**一句話**：2026-09-14 生產 `:8000` 服務（pid 24344）**不是崩潰、不是 watchdog 殺的**，而是它所在的
**DSH harness 行程樹被 harness 自身重啟連坐終止**（Windows Job Object `KILL_ON_JOB_CLOSE` 語意）。

### 1.1 機制（原始碼級）

- DSH 以 Windows **Job Object** 托管子行程，並設 **`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 8192`**：
  - 常數定義 `@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-win32-process/lib/index.js:5`；
  - `:477-481` `createJobObjectW` ＋ `information.writeUInt32LE(8192, 16)`（`SetInformationJobObject`，class 9）；
  - `:570-576` `AssignProcessToJobObject`（指派失敗即 `terminateProcess` ＋ 關閉 job handle）。
  - 呼叫端 `dsh-subprocess-local/lib/index.js:998,1017`。
- **Job handle 關閉（owner 行程結束）⇒ 內核終止全部成員**。
- `Start-Process` **不會** breakaway：既未傳 `CREATE_BREAKAWAY_FROM_JOB`，該 Job 亦未設 `BREAKAWAY_OK`
  ⇒「detach」只脫離 console，**Job 成員身分不變**（父死子必死）。

### 1.2 觸發源（實測）

- `~/.dsh/profiles/web/.dsh-market/log.ndjson`：
  - `2026-09-14T13:45:13.436Z` `update dshmarket -> dshmarket@1.46.1 exit=0`
  - `2026-09-14T13:45:55.104Z` `restart scheduled pid=8224 helper=13604`
  - ⇒ **DSH Market 外掛更新觸發 harness 自我重啟**。
- `~/.dsh/elevated-dsh-restart.log`：`09:49:34` `no dsh host on 127.0.0.1:3080 to kill`（舊 host 已不存在）；
  新 node pid **21648**，CreationDate **09:49:37**。

### 1.3 死亡時間窗 ＝ (09:45:53.553, 09:46:22]，僅 29 秒

- 死者 stderr 最後一位元組 **09:45:53.553**（例行 calendar poll，正常節奏）。
- **決定性證據**：輪替保存的 `data/heartbeat_trace.20260914_094845.log` 內**唯一** block 為
  `=== 2026-09-14 09:45:22 (overwrite, every 60s) ===`
  ⇒ 60s 心跳的 **09:46:22 那一塊不存在**（服務已在該時點前死亡）。

### 1.4 死者身分

- **死者 ＝ pid 24344**，**2026-09-14 07:40:42** 啟動，由 **DSH subagent 執行 `scripts/server_ops.ps1 restart`**
  拉起（該票即 **PRODUCTION-RESTART-1**）⇒ **它是 harness 的子孫行程，因此是 Job 成員**。

### 1.5 無崩潰產物（負面證據；查詢本身成功，非查詢失敗）

| 檢查項 | 結果 |
|---|---|
| Application log 09:35–10:00 | 僅 2 筆 `VBScriptDeprecationAlert`（Id 4096）；**0 筆** Application Error／Windows Error Reporting／.NET Runtime／Application Hang |
| System log 同時段 | **完全無事件** |
| WER `ReportArchive` 最新 `AppCrash_python.exe_*` | **09-12 20:29:36**；09-14 全天 **0 筆** |
| WER LocalDumps → `data/crash_dumps/` | 最新 **09-12 20:33:03**；09-14 **無新 `.dmp`** |
| `data/faulthandler.log` | **6,373,318 B / mtime 2026-09-12 22:38:16 未變**（僅讀 size／mtime） |
| log 內 Traceback／shutdown 序列 | **0 Traceback、0 shutdown 序列**（6 筆 "CRITICAL" 全是 Persona 模板字串誤命中） |

⇒ **致命訊號未觸發、無 C 層崩潰**。這是一條「安靜的死亡」，與 CRASH-F1 系列的
`c0000005` 原生崩潰**簽名完全不同**。

### 1.6 watchdog 不是死因

- watchdog `09:43:03` `OK listener=24344` → `09:48:04` `WARN port_listen=False procs=0 -> restart`。
- 且 `09:48:04 → 09:48:05` 之間**無 `killed PID` 行** ⇒ **24344 在 watchdog 動手之前就已經死了**。
- 亦即 watchdog 後來的 restart 是**善後**，不是死因。

---

## 2. ⚠️ 更正協調者的兩項前提（必須登記，不得省略）

### 2.1 更正一：pid 2312（活 33 小時那個）**也是 harness 啟動的**

- 09-12 22:39:13 由 **DSH 工單 session `a34ed9ef`** → `scripts/server_ops.ps1` 拉起；
  `data/logs/server_ops.log` 09-12 22:39:13 記 `Server started PID=20260`（`.venv` uv 啟動器）。
- 它活 **33 小時**，**只因為其生命期內 0 次 harness 重啟**，**不是**因為它在 harness 之外。
- ⇒ **原假設「2312 在 harness 殺傷半徑外」不成立**。存活時間長短**不能**推論為「不在 Job 內」。

### 2.2 更正二：真正有效的對照組

- **VC 三支**（`VC1_AkaneVoiceCompanion` / `VC1_MaiVoiceCompanion` / `VC1_RemVoiceCompanion`，
  logon trigger，父鏈 `svchost -k netsvcs -s Schedule` ⇒ **Task Scheduler 為根**）：
  09-14 09:46 全程存活（`:8765/8766/8767` = pid 9576/9592/9584）。
- **pid 6184**（本票時刻之 `:8000` listener）：父鏈
  `6184 ← 296(.venv shim) ← 15928 ← wscript.exe ← svchost(Schedule)`，
  **全程無 node.exe** ⇒ 與 DSH harness 行程樹無關。
- ⇒ **判別準則 = 祖先鏈是否含 `node.exe`（DSH harness）**，而非「是否 detach」。

---

## 3. 指標污染與認證基準變更（治理）

### 3.1 `scripts/_watchdog.ps1` 的計數不區分死因

- 健康判據 ＝「port 8000 有 listener」（`:265-270` 區）。
- **`:336` `$counter.n_restarts += 1` 對任何不健康狀態計數**，程式碼**完全不區分死因**
  （原生崩潰／被外部殺／人為）。
- ⇒ 本次 harness 連坐被記為 **`N=1/10`**
  （`data/state/post_9096364_counter.json`：`n_restarts:1`、`n_cap:10`、`trial_count`（實測 12，下界）、
  `trial_target:98`、`window_start_ts 2026-09-14T09:13:03`）。

### 3.2 observation window 旋轉與崩潰無關

- `:152-198`：`:158` 比對 `data/state/_last_observed_hash.txt` 與當前 HEAD。
- **每次 commit 換窗歸零** ⇒ 09-14 當日 **≥24 次旋轉**，**與崩潰無關**。

### 3.3 ⇒ 登錄為新的認證基準（**治理變更**）

> **崩潰率／「0 崩潰」認證一律以「`data/faulthandler.log` byte 成長」＋「`data/crash_dumps/` 之 WER dump」為準。**
> **watchdog 的 `N/10` 與「pid 未重啟」只能當 availability 指標，不得當崩潰率指標**
> —— 它會被**非 Soul OS 事件**（harness 重啟連坐等）污染。

### 3.4 CRASH-F1 本身**不受污染**，認證有效、不需要撤銷

- CRASH-F1 的 24h 窗口（**09-12 22:40 → 09-13 22:40**）內 **0 次 harness 重啟**：
  `elevated-dsh-restart.log` 與 market log 證實 **09-12 19:14 之後至 09-14 09:49 之間無 harness 重啟**。
- 且 pid **2312 全程未變**、`faulthandler.log` **未成長**、**0 WER dump**。
- ⇒ **CRASH-F1 的認證有效，不需要撤銷。**

---

## 4. 新增生產鐵律（**鐵律**）

> **生產服務 `:8000` 只能由 Task Scheduler 為根的行程樹啟動或重啟** ——
> 即 `SoulOS_Watchdog`（`wscript.exe scripts\_watchdog_hidden.vbs` → `_watchdog.ps1` → `_start_plan_a.ps1`）這條路。
> **禁止在任何 DSH session／subagent 內用 PowerShell 直接 spawn 生產 python**
> （含 `scripts/server_ops.ps1 restart`）。

- **理由**：`scripts/server_ops.ps1:109-114` 與 `scripts/_start_plan_a.ps1:88-93` 用的是**同一句
  `Start-Process`**；差別**不在 detach 手法**，而在**祖先是否在 DSH Job 內**。
- **修正後的重啟程序（取代 PRODUCTION-RESTART-1 所記載之方式）**：
  1. 停掉舊服務（kill 不會產生 Job 成員，安全）；
  2. **交由排程路徑拉起** —— 等 `SoulOS_Watchdog` 下一 tick（≤5 分鐘）自動拉起，
     或以 `schtasks /run /tn SoulOS_Watchdog` 觸發；
  3. **絕不在 harness 行程樹內啟動新進程**。
- **附帶操作事實**：DSH **Market 外掛更新會自動排定 harness 重啟**（本次即 `dshmarket@1.46.1`）
  ⇒ 生產需常駐時，**避免此時更新外掛**，或確保生產不在 harness 子孫樹內。

---

## 5. 附錄 A：本票複驗狀態與範圍

- **本文數字複驗**（協調者獨立複驗；唯讀）：
  - Job Object 常數與呼叫點：三處 `read` 原文確認（`:5`、`:481`、`:570`）。
  - 觸發源：market log 兩行逐字命中；`elevated-dsh-restart.log` 對應時戳命中。
  - 心跳證據：`data/heartbeat_trace.20260914_094845.log` 之 block 數 **實測 = 1**（僅 09:45:22）。
  - 負面證據：`data/faulthandler.log` size **6,373,318 B**、mtime **2026-09-12 22:38:16** 未變；
    `data/crash_dumps/` 最新為 **2026-09-12 20:33:03**。
  - 計數檔：`data/state/post_9096364_counter.json`（`n_restarts:1`）與
    `data/state/_last_observed_hash.txt`（mtime `2026-09-14T09:13:03`）讀出。
- **本票範圍（嚴守）**：0 程式碼（`src/**`、`tests/**`、`configs/**`、`scripts/**` 一律不動，
  **特別是 `scripts/_watchdog.ps1` 絕對不准改**）；0 `data/**` 寫入；
  **未開啟 `data/faulthandler.log`**（僅讀 size／mtime）；0 log 清理／輪替；
  0 服務重啟／0 kill／不執行 server_ops、watchdog、Plan A。

---

## 6. 附錄 B：已知地雷（**本次刻意不修**）

- `scripts/_watchdog.ps1:344-350`：以 `CommandLine -like '*<生產啟動檔名>*'` 抓進程並
  `Stop-Process -Force`（`:350`），**且不限 `Name`**；`:344` 的註解明示這是
  **2026-08-29 Bry 派工的刻意設計**（為涵蓋 `.venv` shim／wrapper）。
- **風險**：任何命令行含該字串的進程（**包含 DSH subagent 正在執行 grep／tail／編輯該檔的 pwsh**）
  在「服務不健康且該進程存活」時會被 `/Force` 殺。
- **緩解準則（登記為操作紀律）**：**服務不健康期間，禁止以 shell 命令列引用該檔名字串**
  （改用 `read`／`grep` 工具，其不經 shell 命令列）。
- **明確登記：是否收窄該匹配留待獨立設計票**（需先評估 `.venv`／wrapper 覆蓋率），
  **本票不動任何程式碼**。
