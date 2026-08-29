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

    # Bry 拍板 2026-08-07 18:19: 啟動前備份舊 log 為帶時間戳檔名,沿用舊 server_ops 備份模式
    # (派工原話: 「沿用既有修法拼湊拒絕大改」)
    # 解決 8/7 17:31 cron 修法 12 一天回顧報告 ⚠️ (24h scheduler log 全丟, 因為 Start-Process
    # -RedirectStandardOutput 是 truncate 模式, 每次重啟都把歷史 log 清掉)
    # 範圍: 只動 server_ops.ps1, 不加 log rotate / size limit 邏輯
    # (Bry 派工 spirit: 「不為假設中的未來灑過濾網」)
    $backupDir = Join-Path $root 'data\logs'
    $backupTs = Get-Date -Format 'yyyyMMdd_HHmmss'
    if (Test-Path $outLog) {
        Move-Item -Path $outLog -Destination (Join-Path $backupDir "server_${backupTs}.log")
    }
    if (Test-Path $errLog) {
        Move-Item -Path $errLog -Destination (Join-Path $backupDir "server_${backupTs}.err")
    }

    $proc = Start-Process -FilePath $python `
        -ArgumentList "$root\scripts\run_server.py" `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -NoNewWindow -PassThru `
        -WorkingDirectory $root
    $proc.Id | Out-File -Encoding utf8 $pidFile
    Write-OpsLog "[ok] Server started PID=$($proc.Id) python=$python"
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
    $procs = Get-ServerProcess
    if (-not $procs) {
        Write-Host '[skip] No server running'
        if (Test-Path $pidFile) { Remove-Item $pidFile }
        return
    }
    $procs | ForEach-Object {
        Write-Host "[stop] Killing PID=$($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force
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
