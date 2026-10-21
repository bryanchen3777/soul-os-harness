# _start_plan_a.ps1 - Soul OS Plan A Launcher
# 用途: watchdog 偵測 server 死掉後呼叫此腳本拉新 server
# Bry 拍板 2026-08-15 23:33 修法: 跟 server_ops.ps1 一樣明確指定
# soul-os .venv python (有 networkx/telegram/icalendar 全部依賴), 不依賴系統 PATH
#
# 2026-10-21 SERVER-OPS-PLAN-A-LAUNCH: 啟動路徑改走「單一啟動原語」
# ------------------------------------------------------------------
# 舊機制（本檔已廢除; 只留作事故脈絡, 勿復原）:
#   $proc = Start-Process -FilePath $python `
#       -ArgumentList "$root\scripts\run_server.py" `
#       -RedirectStandardOutput $outLog -RedirectStandardError $errLog `
#       -NoNewWindow -PassThru -WorkingDirectory $root
#
# 為什麼不能用舊機制（實測根因, 勿自行改動機制）:
#   -RedirectStandard* 強制 .NET 走 UseShellExecute=false, 而 .NET 的 CreateProcess
#   一律以 bInheritHandles=TRUE 建立子行程 ⇒ 被拉起的服務會**連帶繼承呼叫端的
#   stdout pipe** ⇒ 呼叫端要等「服務退出」才收到 stdout 的 EOF ⇒ 指令看起來像
#   「不返回」⇒ 被牆鐘上限**連坐砍掉整棵行程樹**
#   （2026-09-16 生產中斷事故形狀; 2026-10-21 02:10 DEPLOY-RESTART-21 的 wrapper
#     即是被 600s 上限殺掉, 服務僥倖存活 —— 那是運氣不是安全性）。
#   拿掉 -NoNewWindow **不會**關掉這個缺口（前一票 SERVER-OPS-HANDLE-INHERITANCE 實測）。
#
# 現在的機制（單一啟動原語）:
#   呼叫端 → 本檔 (Plan A) → scripts\server_ops.ps1 -Action start
#          → 中繼 launcher (SOULOS_LAUNCH_SERVICE_ONLY=1) → 服務 (scripts\run_server.py)
#   server_ops.ps1 的 start 動詞本來就走中繼 launcher: 它以「不帶 -RedirectStandard*」
#   的 Start-Process 啟動中繼 launcher ⇒ PowerShell 5.1 走 ShellExecute 路徑 ⇒
#   CreateProcess 以 bInheritHandles=FALSE 建立 ⇒ 中繼 launcher **不持有**呼叫端的
#   pipe; 中繼 launcher 再以 .NET 導向拉起真正的服務 ⇒ 服務只繼承中繼 launcher 的
#   handle（既有 nohup log）, **不繼承**呼叫端的 pipe ⇒ 呼叫端立即收到 EOF。
#   未來只需維護 server_ops.ps1 一處啟動邏輯（避免兩份啟動邏輯各自漂移）。
#
# 🔴 本檔呼叫 server_ops.ps1 時**不得**帶 -RedirectStandard* / -NoNewWindow:
#    帶了就會讓 server_ops.ps1 繼承本檔的 stdout pipe（等於把繼承問題搬家）;
#    也不得以「重導向」繞開 —— 唯一正解就是上面那條不繼承的路徑。
#
# 契約（不變）:
# - 啟動失敗立刻 exit 1, 讓 watchdog Plan A 失敗計數 (N 累積) 不會誤判成功
# - 不檢查既有 process (Plan A 已經從 watchdog 確認 process 死了)
#   → 服務已經健康的時候 server_ops.ps1 的 start 會自己跳過重複啟動, 本檔最後的
#     健康等待會判定成功/失敗, 不會產生第二個實例
# - pid 語意: data\server.pid 仍是 launcher pid（中繼 launcher 寫入）,
#   :8000 的 socket owner 仍是它的子行程
# - log 檔名不變: data\server_nohup.log / .err（服務輸出, 由 server_ops.ps1 導向）
#   / data\logs\plan_a_launcher.log（本檔的啟動紀錄）
# - 必須在服務**真的起來**之後才回報成功（不得把「已送出啟動」當成「已起來」）
#
# 失敗可逆: 刪除此檔案, watchdog 23:18 跟 23:23 那種「Plan A 啟動但立刻死」
# 行為就會恢復 (powershel l.exe 找不到檔案立刻退出), 跟 Bry 派工前現狀一致

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$pidFile = Join-Path $root 'data\server.pid'
$outLog = Join-Path $root 'data\server_nohup.log'
$errLog = Join-Path $root 'data\server_nohup.err'
$launcherLog = Join-Path $root 'data\logs\plan_a_launcher.log'
# 2026-10-21: Plan A 不再自己拉起服務, 改為委派給 server_ops.ps1 的 start 動詞
# （單一啟動原語: 未來只需維護 server_ops.ps1 一處; 檔名/格式不變）。
$serverOps = Join-Path $root 'scripts\server_ops.ps1'
# 生產服務的 TCP 埠（健康等待用; 跟 server_ops.ps1 一致）。
$servicePort = 8000

# 跟 server_ops.ps1 一致: 用 soul-os .venv python (有 networkx/telegram/icalendar)
# 不依賴系統 PATH, 避免 8/15 22:00 night slot 時 hermes venv 缺 networkx 導致 server 掛掉
$python = 'C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe'

# === Logging ===
function Log-PlanA([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        $launcherLogDir = Split-Path -Path $launcherLog -Parent
        if (-not (Test-Path $launcherLogDir)) {
            New-Item -ItemType Directory -Path $launcherLogDir -Force | Out-Null
        }
        Add-Content -Path $launcherLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[plan_a_launcher] WARN log write failed: $_"
    }
}

# CRASH-OBS-1 (2026-09-10): 載入共用 nohup log 輪替 helper
# 盲區 i: Start-Process -RedirectStandard* 是 truncate 模式, 每次重啟會清掉
# 崩潰實例的最後輸出 → 啟動前先把舊 data/server_nohup.{log,err} 改名保留。
# 載入失敗只記 WARN, 絕不阻擋啟動 (觀測層 0 影響)。
try {
    . (Join-Path $PSScriptRoot '_nohup_log_rotation.ps1')
} catch {
    Log-PlanA "WARN failed to load nohup rotation helper (continue): $_"
}

# === 啟動前 sanity check ===
if (-not (Test-Path $python)) {
    Log-PlanA "ERROR python not found at $python - Plan A FAILED"
    exit 1
}

# 確認 uvicorn 可 import (跟 server_ops.ps1 觸發的 ModuleNotFoundError 同類檢查)
try {
    $uvCheck = & $python -c "import uvicorn; print('uvicorn', uvicorn.__version__)" 2>&1
    if ($LASTEXITCODE -ne 0) {
        Log-PlanA "ERROR uvicorn import failed: $uvCheck - Plan A FAILED"
        exit 1
    }
    Log-PlanA "pre-check OK: $uvCheck"
} catch {
    Log-PlanA "ERROR pre-check exception: $_ - Plan A FAILED"
    exit 1
}

# === 啟動前: 輪替保留舊 nohup log (CRASH-OBS-1) ===
# 崩潰實例的日誌必須永遠存活到被輪替, 不得在啟動時被清空。
# 本函式由共用 helper 提供 (scripts\_nohup_log_rotation.ps1), 失敗不阻擋啟動。
if (Get-Command Backup-Rotate-NohupLogs -ErrorAction SilentlyContinue) {
    Backup-Rotate-NohupLogs -Root $root -BackupDir (Join-Path $root 'data\logs') -LogFn { param($m) Log-PlanA $m }
} else {
    Log-PlanA "WARN nohup rotation helper not loaded - skip rotation (start anyway)"
}

# === 啟動 server: 委派給 server_ops.ps1 start（單一啟動原語）===
if (-not (Test-Path $serverOps)) {
    Log-PlanA "ERROR server_ops.ps1 not found at $serverOps - Plan A FAILED"
    exit 1
}
$ops = $null
$launchFailed = $false
try {
    $env:PYTHONIOENCODING = 'utf-8'
    # 🔴 不得帶 -RedirectStandard* / -NoNewWindow:
    #    帶了就會走 .NET 的 UseShellExecute=false 路徑 (CreateProcess 一律
    #    bInheritHandles=TRUE) ⇒ server_ops.ps1 會繼承本檔的 stdout pipe
    #    ⇒ 呼叫端要等服務退出才收到 EOF ⇒ 被牆鐘上限連坐砍掉整棵行程樹。
    #    不帶 ⇒ PowerShell 5.1 走 ShellExecute 路徑 ⇒ CreateProcess 以
    #    bInheritHandles=FALSE 建立 ⇒ server_ops.ps1 不持有呼叫端的 pipe。
    $ops = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $serverOps, '-Action', 'start') `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -PassThru
    if (-not $ops.WaitForExit(120000)) {
        Log-PlanA "ERROR server_ops.ps1 start did not exit within 120s - Plan A FAILED"
        $launchFailed = $true
    }
} catch {
    Log-PlanA "ERROR launching server_ops.ps1 start failed: $_ - Plan A FAILED"
    $launchFailed = $true
}
if ($launchFailed) {
    exit 1
}
if ($ops.ExitCode -ne 0) {
    Log-PlanA "ERROR server_ops.ps1 start exit code $($ops.ExitCode) - Plan A FAILED"
    exit 1
}

# === 驗證: pid 檔 + 服務真的起來了才算成功 ===
# 不得把「已送出啟動」當成「已起來」: server_ops.ps1 的 start 已經做過
# 「中繼 launcher 有寫 pid 檔」的一致性等待, 這裡再確認服務真的健康。
$serverPid = $null
try {
    $serverPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
} catch {
    $serverPid = $null
}
if (-not $serverPid) {
    Log-PlanA "ERROR pid file $pidFile was not written - Plan A FAILED"
    exit 1
}
$healthy = $false
$healthDeadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $healthDeadline) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:$servicePort/health" -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        # 服務還沒起來 —— 繼續等（不得在此回報成功）
    }
    Start-Sleep -Seconds 2
}
if (-not $healthy) {
    Log-PlanA "ERROR service not healthy within 60s (pid=$serverPid) - Plan A FAILED"
    exit 1
}
Log-PlanA "OK server healthy PID=$serverPid (via server_ops.ps1 start; python=$python; logs: $outLog / $errLog)"
exit 0
