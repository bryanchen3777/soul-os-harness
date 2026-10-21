# server_ops.ps1 - Soul OS server deployment helper
# Usage (in PowerShell, no mavis session needed):
#   cd C:\Users\bbfcc\.local\bin\soul-os-harness
#   .\scripts\server_ops.ps1 start
#   .\scripts\server_ops.ps1 status
#   .\scripts\server_ops.ps1 stop
#   .\scripts\server_ops.ps1 tail
#   .\scripts\server_ops.ps1 restart
#
# Detach python from PowerShell session via Start-Process.
# Solves mavis `bash` background task 1hr timeout issue.

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('start','stop','status','tail','restart','help')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$pidFile = Join-Path $root 'data\server.pid'
$outLog = Join-Path $root 'data\server_nohup.log'
$errLog = Join-Path $root 'data\server_nohup.err'
# 跟 _start_plan_a.ps1 一致 (Bry 拍板 2026-08-15 23:33 修法):
# 改用 soul-os .venv python (有 networkx/telegram/icalendar 全部依賴)
# 根因: hermes venv 缺 networkx/telegram/pip → 8/15 22:00 night slot 時 server 掛了
# (ModuleNotFoundError: No module named 'networkx' → Application startup failed)
$python = 'C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe'
$opsLog = Join-Path $root 'data\logs\server_ops.log'

function Get-ServerProcess {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*run_server.py*' }
}

function Write-OpsLog([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        $opsLogDir = Split-Path -Path $opsLog -Parent
        if (-not (Test-Path $opsLogDir)) {
            New-Item -ItemType Directory -Path $opsLogDir -Force | Out-Null
        }
        Add-Content -Path $opsLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[server_ops] WARN log write failed: $_"
    }
}

# CRASH-OBS-1 (2026-09-10): 載入共用 nohup log 輪替 helper (與 _start_plan_a.ps1 一致)
# 失敗只記 WARN, 絕不阻擋啟動
try {
    . (Join-Path $PSScriptRoot '_nohup_log_rotation.ps1')
} catch {
    Write-OpsLog "WARN failed to load nohup rotation helper (continue): $_"
}

function Start-SoulOsServer {
    if (Get-ServerProcess) {
        Write-OpsLog "[skip] Server already running"
        Get-ServerProcess | Select-Object ProcessId, StartTime | Format-Table
        return
    }
    # 啟動前 sanity check (跟 _start_plan_a.ps1 一致): python 存在 + uvicorn 可 import
    # 避免 8/3 23:25:05 ModuleNotFoundError 啟動後才死, 浪費 5 分鐘 tick
    if (-not (Test-Path $python)) {
        Write-OpsLog "ERROR python not found at $python - start FAILED"
        exit 1
    }
    try {
        $uvCheck = & $python -c "import uvicorn; print('uvicorn', uvicorn.__version__)" 2>&1
        if ($LASTEXITCODE -ne 0) {
            Write-OpsLog "ERROR uvicorn import failed: $uvCheck - start FAILED"
            exit 1
        }
        Write-OpsLog "pre-check OK: $uvCheck"
    } catch {
        Write-OpsLog "ERROR pre-check exception: $_ - start FAILED"
        exit 1
    }
    $env:PYTHONIOENCODING = 'utf-8'

    # Proactive DM 三件修復 #3 (Bry 拍板 2026-08-29): 啟動前等 port 8000 釋放 —
    # 避免舊實例 (含 uv-managed python 子進程) 未死透時新實例 bind 衝突
    # (Errno 10048) 或雙實例並存 (Telegram getUpdates Conflict)。
    $portWaitUntil = (Get-Date).AddSeconds(15)
    while ((Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $portWaitUntil)) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
        Write-OpsLog "ERROR port 8000 still listening after 15s wait - start ABORTED (old instance not dead)"
        exit 1
    }

    # Bry 拍板 2026-08-07 18:19: 啟動前備份舊 log 為帶時間戳檔名,沿用舊 server_ops 備份模式
    # (派工原話: 「沿用既有修法拼湊拒絕大改」)
    # 解決 8/7 17:31 cron 修法 12 一天回顧報告 ⚠️ (24h scheduler log 全丟, 因為 Start-Process
    # -RedirectStandardOutput 是 truncate 模式, 每次重啟都把歷史 log 清掉)
    # CRASH-OBS-1 (2026-09-10): 改用共用輪替 helper (與 _start_plan_a.ps1 同一份邏輯):
    #   - 命名統一為 server_nohup.<ts>.{err,log} (舊的 server_<ts> 備份檔不受影響)
    #   - 各 ext 保留最近 5 份, 防止無限增長
    if (Get-Command Backup-Rotate-NohupLogs -ErrorAction SilentlyContinue) {
        Backup-Rotate-NohupLogs -Root $root -BackupDir (Join-Path $root 'data\logs') -LogFn { param($m) Write-OpsLog $m }
    } else {
        Write-OpsLog "WARN nohup rotation helper not loaded - skip rotation (start anyway)"
    }

    # 啟動：服務不與呼叫端共用 Console，且**不繼承呼叫端的 stdout pipe**
    # （2026-10-21 SERVER-OPS-HANDLE-INHERITANCE）
    #
    # 為什麼要多一層「中繼 launcher」（實測根因，勿自行改動機制）：
    #   .NET 的 Start-Process -RedirectStandardOutput/-RedirectStandardError 走
    #   UseShellExecute=false，而 .NET 的 CreateProcess 一律以 bInheritHandles=TRUE
    #   建立子行程 ⇒ 服務會連帶繼承「呼叫端」的 stdout pipe（實測：服務行程內
    #   GetFileType(呼叫端 pipe handle) = FILE_TYPE_PIPE）。後果：呼叫端（例如
    #   harness 的背景 shell）要等「服務退出」才收到 stdout 的 EOF ⇒ 指令看起來像
    #   「不返回」⇒ 被牆鐘上限連坐砍掉整棵行程樹（2026-09-16 生產中斷事故形狀；
    #   2026-10-21 02:10 DEPLOY-RESTART-21 即是被 600s 上限殺掉 wrapper，服務僥倖
    #   存活）。拿掉 -NoNewWindow **不會**關掉這個缺口（前一票實測）。
    #
    # 本票做法（把「直接拉起服務」搬進中繼 launcher 這一層）：
    #   1. 呼叫端以「**不帶** -RedirectStandard*」的 Start-Process 啟動中繼 launcher
    #      → PowerShell 5.1 走 ShellExecute 路徑 → CreateProcess 以
    #      bInheritHandles=FALSE 建立 ⇒ 中繼 launcher **不持有**呼叫端的 pipe。
    #   2. 中繼 launcher 再以 .NET 導向拉起真正的服務 ⇒ 服務只繼承中繼 launcher 的
    #      handle（既有 nohup log），**不繼承**呼叫端的 pipe ⇒ 呼叫端立即收到 EOF。
    #   3. pid 語意不變：中繼 launcher 把服務行程（trampoline）的 pid 寫進 pid 檔
    #      ⇒ data\server.pid 仍是 launcher pid，:8000 的 socket owner 仍是其子行程。
    #
    # 中繼 launcher 就是**本支腳本自己**（以環境變數 SOULOS_LAUNCH_SERVICE_ONLY=1
    # 進入中繼模式），理由：服務啟動原語必須留在本檔（tests/test_server_ops_python_path_v1.py
    # 與前一票的守衛都要求：本檔第一個啟動呼叫的 -FilePath 必須是 $python，且須帶
    # -RedirectStandardOutput $outLog / -RedirectStandardError $errLog / -PassThru）。
    #
    # ⚠ 中繼 launcher 的命令列刻意**不含** "run_server.py"：Get-ServerProcess 以
    #   '*run_server.py*' 比對命令列，若中繼被誤認成服務，start 的「已在執行」判斷
    #   會誤判成服務還活著。服務腳本路徑由本檔的 $root 推導，不經命令列傳遞。
    if ($env:SOULOS_LAUNCH_SERVICE_ONLY -eq '1') {
        # ── 中繼 launcher 模式：直接拉起服務行程（只由中繼 launcher 進入）──
        $proc = Start-Process -FilePath $python `
            -ArgumentList "$root\scripts\run_server.py" `
            -RedirectStandardOutput $outLog `
            -RedirectStandardError $errLog `
            -PassThru `
            -WorkingDirectory $root
        $proc.Id | Out-File -Encoding utf8 $pidFile
        Write-OpsLog "[relay] service started PID=$($proc.Id) python=$python"
        exit 0
    }

    # ── 一般模式：啟動中繼 launcher（不帶 -RedirectStandard* ⇒ 不繼承呼叫端 pipe）──
    $env:SOULOS_LAUNCH_SERVICE_ONLY = '1'
    try {
        $relay = Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $root 'scripts\server_ops.ps1'), '-Action', 'start') `
            -PassThru `
            -WorkingDirectory $root
    } finally {
        $env:SOULOS_LAUNCH_SERVICE_ONLY = $null
    }
    if (-not $relay.WaitForExit(60000)) {
        Write-OpsLog "ERROR relay launcher did not exit within 60s - start FAILED"
        exit 1
    }
    if ($relay.ExitCode -ne 0) {
        Write-OpsLog "ERROR relay launcher exit code $($relay.ExitCode) - start FAILED"
        exit 1
    }
    $serverPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if (-not $serverPid) {
        Write-OpsLog "ERROR pid file was not written by the relay launcher - start FAILED"
        exit 1
    }
    Write-OpsLog "[ok] Server started PID=$serverPid python=$python"
    Write-OpsLog "     Logs: $outLog"
    Write-OpsLog "           $errLog"
    Start-Sleep -Seconds 3
    try {
        $r = Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5
        Write-OpsLog "     /health = $($r.StatusCode)"
    } catch {
        Write-OpsLog "     /health failed: $($_.Exception.Message)"
    }
}

function Stop-SoulOsServer {
    # Proactive DM 三件修復 #3 (Bry 拍板 2026-08-29): 修 restart 競態 —
    # 舊版只靠 WMI CommandLine 匹配殺進程, 但 server 是 .venv python (uv shim)
    # spawn uv-managed python 的父子結構, WMI 匹配不可靠 (watchdog procs=0 已證明,
    # d8c057d 修的就是這個), 殺不乾淨 → 新舊實例短暫並存 → Telegram getUpdates
    # Conflict (10 bot × 2 實例 = 20 個 polling 衝突, 8/29 21:49-21:50 實證)。
    # 修法: 1) 先殺 port 8000 listener 的 owning process 進程樹 (真 server)
    #       2) 再殺所有匹配 run_server.py 的進程樹 (含 uv shim 父進程)
    #       3) taskkill /T 確保 uv-managed python 子進程也死透
    $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listener) {
        $listenerPid = $listener.OwningProcess
        try {
            & taskkill /PID $listenerPid /T /F 2>&1 | Out-Null
            Write-Host "[stop] Killed port-8000 listener tree PID=$listenerPid"
        } catch {
            Write-Host "[stop] WARN taskkill listener PID=$listenerPid failed: $_"
        }
    }
    $procs = Get-ServerProcess
    if (-not $procs) {
        Write-Host '[skip] No server running'
        if (Test-Path $pidFile) { Remove-Item $pidFile }
        return
    }
    $procs | ForEach-Object {
        try {
            & taskkill /PID $_.ProcessId /T /F 2>&1 | Out-Null
            Write-Host "[stop] Killed tree PID=$($_.ProcessId)"
        } catch {
            Write-Host "[stop] WARN kill PID=$($_.ProcessId) failed: $_"
        }
    }
    if (Test-Path $pidFile) { Remove-Item $pidFile }
    Write-Host '[ok] Server stopped'
}

function Get-SoulOsStatus {
    $procs = Get-ServerProcess
    if ($procs) {
        $procs | Select-Object ProcessId, @{N='UpTime';E={(Get-Date) - $_.StartTime}}, StartTime | Format-Table
        try {
            $r = Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing -TimeoutSec 5
            Write-Host "[health] /health = $($r.StatusCode)"
        } catch {
            Write-Host "[health] /health failed: $($_.Exception.Message)"
        }
    } else {
        Write-Host '[status] No server running'
    }
}

function Show-ServerTail {
    if (Test-Path $errLog) {
        Write-Host "=== Last 30 lines of $errLog ==="
        Get-Content $errLog -Tail 30
    } else {
        Write-Host '[tail] err log does not exist'
    }
}

switch ($Action) {
    'start'   { Start-SoulOsServer }
    'stop'    { Stop-SoulOsServer }
    'status'  { Get-SoulOsStatus }
    'tail'    { Show-ServerTail }
    'restart' {
        # 維護窗口: 寫 lock 檔暫停 watchdog tick (修 restart 競態, Bry 派工 2026-08-29)
        # watchdog 看到 lock 就 SKIP, 避免在 stop→start 窗口誤判不健康而拉 Plan A
        # 第二實例 (Telegram token Conflict)。finally 保證 lock 一定被清掉,
        # 即使 Start-SoulOsServer 失敗 (exit/exception) 也會恢復 watchdog。
        $maintenanceLock = Join-Path $root 'data\state\watchdog_maintenance.lock'
        try {
            $lockDir = Split-Path -Path $maintenanceLock -Parent
            if (-not (Test-Path $lockDir)) {
                New-Item -ItemType Directory -Path $lockDir -Force | Out-Null
            }
            Set-Content -Path $maintenanceLock -Value (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') -Encoding UTF8
            Write-OpsLog "[maintenance] watchdog paused (lock written: $maintenanceLock)"
            Stop-SoulOsServer
            Start-Sleep -Seconds 2
            Start-SoulOsServer
        } finally {
            if (Test-Path $maintenanceLock) {
                Remove-Item $maintenanceLock -Force -ErrorAction SilentlyContinue
            }
            Write-OpsLog "[maintenance] watchdog resumed (lock removed)"
        }
    }
    'help' {
        Write-Host 'Usage: .\scripts\server_ops.ps1 {start|stop|status|tail|restart|help}'
    }
}
