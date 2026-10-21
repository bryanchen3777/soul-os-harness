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

    # 啟動：服務不與呼叫端共用 Console（2026-10-21 SERVER-OPS-LAUNCH-DETACH）
    #
    # 拿掉 -NoNewWindow 的理由（實測，隔離沙盒 + 無害子行程）：
    #   Windows PowerShell 5.1 的 Start-Process 若帶 -NoNewWindow，服務會與呼叫端
    #   **共用同一個 Console**（服務端 GetConsoleProcessList 會數到呼叫端的行程）；
    #   拿掉之後服務拿到自己的 Console，呼叫端的 console handle 不再被服務掛住。
    #   （2026-10-21 02:10 DEPLOY-RESTART-21 的 restart 即是被 600s 上限砍掉 wrapper，
    #   服務僥倖存活）。
    #
    # 實測（隔離沙盒 + 無害子行程；守衛測試 tests/infra/test_server_ops_launch_detach.py）：
    #   拿掉 -NoNewWindow 之後，服務拿到自己的 Console（服務端 GetConsoleProcessList
    #   只剩自己、console hwnd 與呼叫端不同），輸出照樣由 -RedirectStandardOutput/
    #   -RedirectStandardError 寫進既有 nohup log（log 檔名與內容格式不變），
    #   服務則在 wrapper 退出後繼續存活（pid 檔語意不變）。
    #
    # ⚠ 尚未關閉的缺口（實測；需回報主大腦裁決，勿自行改啟動機制）：
    #   只要帶 -RedirectStandardOutput/-RedirectStandardError，PowerShell 5.1 就走
    #   .NET 的 UseShellExecute=false 路徑，而 .NET 的 CreateProcess 一律以
    #   bInheritHandles=TRUE 建立子行程 ⇒ **服務會一併繼承呼叫端的 stdout pipe**
    #   （實測：服務行程內 GetFileType(呼叫端 pipe handle) = VALID）。後果是呼叫端
    #   （例如 harness 的背景 shell）要等「服務退出」才收得到 stdout 的 EOF，指令看
    #   起來像「不返回」，最後被牆鐘上限連坐砍掉整棵行程樹（2026-09-16 事故形狀；
    #   2026-10-21 02:10 DEPLOY-RESTART-21 即是被 600s 上限殺掉 wrapper，服務僥倖
    #   存活）。拿掉 -NoNewWindow **不會**關掉這個缺口（實測：pipe 仍被服務持有）。
    #   真正要關掉它必須改變啟動機制（例如讓服務由一個以 bInheritHandles=FALSE 建立
    #   的中繼 launcher 拉起），那已超出本票授權的範圍，故先在此標註，並保留
    #   tests/infra/test_server_ops_launch_detach.py 內的已知缺口測試（xfail）。
    $proc = Start-Process -FilePath $python `
        -ArgumentList "$root\scripts\run_server.py" `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -PassThru `
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
