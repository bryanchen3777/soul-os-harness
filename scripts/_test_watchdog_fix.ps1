# _test_watchdog_fix.ps1 — 驗證 watchdog 方案 A/B 修復邏輯 (2026-09-08)
# 方案 A: 啟動窗口保護 (距上次 Plan A 啟動 < 90s 不判定崩潰)
# 方案 B: Plan A 前殺進程樹 + 等 port 釋放
# 用 mock 函數驗證核心判斷邏輯, 不執行真實 watchdog 主流程。

$ErrorActionPreference = 'Stop'
$pass = 0
$fail = 0

function Assert-True([bool]$cond, [string]$name) {
    if ($cond) { $script:pass++; Write-Host "PASS  $name" }
    else { $script:fail++; Write-Host "FAIL  $name" }
}

# === 方案 A: 啟動窗口判斷邏輯 (與 _watchdog.ps1 5.5 節同構) ===
$STARTUP_WINDOW_SECONDS = 90

function Test-StartupWindow([datetime]$lastLaunchTs) {
    # 回傳 $true = 在啟動窗口內 (應 skip restart)
    if ($null -eq $lastLaunchTs) { return $false }
    $sinceLaunchSec = [int]((Get-Date) - $lastLaunchTs).TotalSeconds
    return ($sinceLaunchSec -lt $STARTUP_WINDOW_SECONDS)
}

# 情境 1: 距上次啟動 30s (< 90s) → 應在窗口內 (skip)
$t1 = (Get-Date).AddSeconds(-30)
Assert-True (Test-StartupWindow $t1) "A1: 30s since launch -> in startup window (skip restart)"

# 情境 2: 距上次啟動 120s (> 90s) → 不應在窗口內 (正常判定)
$t2 = (Get-Date).AddSeconds(-120)
Assert-True (-not (Test-StartupWindow $t2)) "A2: 120s since launch -> outside window (normal check)"

# 情境 3: 無 last_launch 檔案 (首次) → $lastLaunchTs 保持 $null → 不進窗口保護
# (模擬 _watchdog.ps1 的 if ($null -ne $lastLaunchTs) 外層判斷)
$lastLaunchTs3 = $null
$inWindow3 = $false
if ($null -ne $lastLaunchTs3) { $inWindow3 = Test-StartupWindow $lastLaunchTs3 }
Assert-True (-not $inWindow3) "A3: no last_launch file -> outside window (normal check)"

# === 方案 B: port 釋放輪詢邏輯 (與 _watchdog.ps1 8 節同構) ===
$PORT_RELEASE_WAIT_SECONDS = 30

function Test-PortReleaseLoop([bool]$portReleasedImmediately) {
    # 模擬 Get-NetTCPConnection: 立即釋放 vs 一直占用
    $portReleased = $false
    $waitSec = 0
    for ($i = 0; $i -lt $PORT_RELEASE_WAIT_SECONDS; $i++) {
        Start-Sleep -Milliseconds 1
        $waitSec = $i + 1
        $l = if ($portReleasedImmediately) { $null } else { @{ LocalPort = 8000 } }
        if ($null -eq $l) { $portReleased = $true; break }
    }
    return @{ released = $portReleased; waitSec = $waitSec }
}

# 情境 4: port 立即釋放 → 輪詢應快速成功 (waitSec 小)
$r4 = Test-PortReleaseLoop $true
Assert-True ($r4.released) "B1: port released immediately -> loop succeeds"
Assert-True ($r4.waitSec -le 2) "B2: port released quickly (waitSec=$($r4.waitSec))"

# 情境 5: port 一直占用 → 輪詢應等滿 30s 後 proceed (released=false)
$r5 = Test-PortReleaseLoop $false
Assert-True (-not $r5.released) "B3: port never released -> loop times out (proceed anyway)"
Assert-True ($r5.waitSec -eq $PORT_RELEASE_WAIT_SECONDS) "B4: waited full $PORT_RELEASE_WAIT_SECONDS s"

# === 方案 B: 殺進程樹寬匹配邏輯 (與 _watchdog.ps1 8 節同構) ===
function Test-ServerProcMatch([string]$cmdline) {
    # 模擬 Where-Object { $_.CommandLine -like '*run_server.py*' }
    return ($cmdline -like '*run_server.py*')
}

# 情境 6: uv-managed python 進程 (Name 不是 python.exe 但 CommandLine 含 run_server.py)
Assert-True (Test-ServerProcMatch 'C:\...\uv\python\cpython-3.11\python.exe -m scripts.run_server.py --port 8000') "B5: uv-managed python matched by CommandLine"
# 情境 7: 無關進程不匹配
Assert-True (-not (Test-ServerProcMatch 'C:\Windows\System32\notepad.exe')) "B6: unrelated process not matched"

Write-Host ""
Write-Host "===== RESULT: $pass passed, $fail failed ====="
if ($fail -gt 0) { exit 1 } else { exit 0 }
