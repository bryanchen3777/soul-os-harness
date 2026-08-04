# _start_plan_a.ps1 - Soul OS Plan A Launcher
# 用途: watchdog 偵測 server 死掉後呼叫此腳本拉新 server
# Bry 拍板 2026-08-03 23:33 修法: 跟 server_ops.ps1 一樣明確指定
# hermes-agent venv python, 不依賴系統 PATH (避免 uvicorn ModuleNotFoundError)
#
# 設計:
# - Detach python from PowerShell session via Start-Process
# - python 必須是 hermes-agent venv (有 uvicorn / fastapi / pydantic 等依賴)
# - 啟動失敗立刻 exit 1, 讓 watchdog Plan A 失敗計數 (N 累積) 不會誤判成功
# - 不檢查既有 process (Plan A 已經從 watchdog 確認 process 死了)
# - 寫 PID 到 data\server.pid 給 watchdog / server_ops 共用
# - 寫啟動 log 到 data\logs\plan_a_launcher.log 給 Bry 事後核對
#
# 範圍: 只負責啟動 run_server.py, 不負責其他邏輯 (跟 server_ops.ps1 的
# Start-SoulOsServer 一致, 但省略 process check 因為 Plan A 已經確認 server 死)
#
# 失敗可逆: 刪除此檔案, watchdog 23:18 跟 23:23 那種「Plan A 啟動但立刻死」
# 行為就會恢復 (powershel l.exe 找不到檔案立刻退出), 跟 Bry 派工前現狀一致

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$pidFile = Join-Path $root 'data\server.pid'
$outLog = Join-Path $root 'data\server_nohup.log'
$errLog = Join-Path $root 'data\server_nohup.err'
$launcherLog = Join-Path $root 'data\logs\plan_a_launcher.log'

# 跟 server_ops.ps1 L30-54 一致: 用 hermes-agent venv python (有 uvicorn)
# 不依賴系統 PATH, 避免 8/2 15:20 miku 教訓 + 8/3 23:25:05 Plan A 失敗同類問題
$python = 'C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe'

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

# === 啟動 server ===
try {
    $env:PYTHONIOENCODING = 'utf-8'
    $proc = Start-Process -FilePath $python `
        -ArgumentList "$root\scripts\run_server.py" `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog `
        -NoNewWindow -PassThru `
        -WorkingDirectory $root
    $proc.Id | Out-File -Encoding utf8 $pidFile
    Log-PlanA "OK started server PID=$($proc.Id) python=$python"
    exit 0
} catch {
    Log-PlanA "ERROR Start-Process failed: $_ - Plan A FAILED"
    exit 1
}
