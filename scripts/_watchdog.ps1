# Soul OS Watchdog (P0-2 升級版)
# - 每 5 分鐘被 Task Scheduler 叫一次
# - 檢查 port 8000 是不是還 listen + run_server.py 還活著
# - 死了就呼叫 Plan A launcher 拉起來
# - P0-2 計數器 (Bry 拍板 2026-07-31): 持久化在 data/state/post_<hash>_counter.json
#   * git HEAD short hash 變了 → 自動 reset (新觀察期)
#   * N 達到 N_CAP (預設 10) → 停止 auto-restart, 改記 ERROR 等 Bry 介入
#   * trial = tick 累加 (觀察 n≥98 收斂目標)
# - 所有動作寫到 data\logs\watchdog.log
# - 觀察期標籤格式: post-XXXXXX (例: post-943486a N=2/10 trial=12/98)

$ErrorActionPreference = 'Stop'
$port = 8000
$harness = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$logFile = Join-Path $harness 'data\logs\watchdog.log'
$stateDir = Join-Path $harness 'data\state'
$N_CAP = 10
$TRIAL_TARGET = 98

# === Logging (放最前面,讓其他 function 可以呼叫) ===

function Log-Watch([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        Out-File -Append -FilePath $logFile -InputObject $line -Encoding utf8 -ErrorAction SilentlyContinue
    } catch {}
}

# === P0-2: git HEAD hash 查詢 ===

function Get-GitShortHash {
    try {
        $hash = & git -C $harness rev-parse --short HEAD 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        return $hash.Trim()
    } catch { return $null }
}

function Get-GitFullHash {
    try {
        $hash = & git -C $harness rev-parse HEAD 2>&1
        if ($LASTEXITCODE -ne 0) { return $null }
        return $hash.Trim()
    } catch { return $null }
}

# === P0-2: 計數器檔案 I/O (atomic write 防 race) ===

function Get-CounterFilePath {
    param([string]$shortHash)
    return (Join-Path $stateDir "post_${shortHash}_counter.json")
}

function Read-Counter {
    param([string]$file)
    try {
        if (-not (Test-Path $file)) { return $null }
        $content = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
        if ([string]::IsNullOrWhiteSpace($content)) { return $null }
        return ($content | ConvertFrom-Json)
    } catch { return $null }
}

function Write-Counter-Atomic {
    param([string]$file, $data)
    try {
        $json = $data | ConvertTo-Json -Depth 5
        $tmpFile = "$file.tmp.$([System.Diagnostics.Process]::GetCurrentProcess().Id)"
        [System.IO.File]::WriteAllText($tmpFile, $json, [System.Text.Encoding]::UTF8)
        Move-Item -Path $tmpFile -Destination $file -Force
    } catch {
        Log-Watch "  ERROR writing counter file: $_"
    }
}

# === P0-2: 計數器管理 (hash 變更自動 reset) ===
# 設計: per-hash counter file + 用 watchdog log 最後一筆 post-XXXXX 標籤作為「上次 hash」
# 為什麼不用 _current_window.txt: Bry 拍板的 log 標籤就是用來識別觀察期, log 自己就是 state, 不需多一個檔

function Get-LastObservedHash {
    # 從 watchdog.log 最後一筆 OK/WARN/ERROR log 解析 post-<hash> 標籤
    # 如果 log 為空 或 沒 post-XXXXX 標籤 → 回傳 $null (代表首次觀察期)
    try {
        if (-not (Test-Path $logFile)) { return $null }
        # 讀最後 20 行找最近一筆 post-XXXXX (避免 log 被 rotation 後 last line 不是 post-XXXXX)
        $tail = Get-Content $logFile -Tail 20 -Encoding UTF8 -ErrorAction SilentlyContinue
        for ($i = $tail.Count - 1; $i -ge 0; $i--) {
            if ($tail[$i] -match 'post-([a-f0-9]+)') {
                return $matches[1]
            }
        }
        return $null
    } catch { return $null }
}

function Get-Or-Create-Counter {
    param([string]$shortHash, [string]$fullHash)
    $file = Get-CounterFilePath $shortHash

    # 偵測 rotation: watchdog log 最後一筆 post-XXXXX vs 當前 git HEAD
    $lastObservedHash = Get-LastObservedHash
    $isRotation = ($null -ne $lastObservedHash) -and ($lastObservedHash -ne $shortHash)

    # 載入當前 hash 的 counter (如果存在)
    $counter = Read-Counter $file

    if ($isRotation) {
        # 觀察期切換: 舊 counter 保留為歷史, 新 counter 從 0 開始
        $now = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
        $counter = @{
            git_short_hash = $shortHash
            git_full_hash = $fullHash
            window_start_ts = $now
            n_restarts = 0
            trial_count = 0
            n_cap = $N_CAP
            trial_target = $TRIAL_TARGET
            last_update_ts = $now
        }
        Write-Counter-Atomic $file $counter
        Log-Watch "  observation window rotated: post-$lastObservedHash -> post-$shortHash (counter reset, n_restarts=0, trial_count=0)"
    } elseif ($null -eq $counter) {
        # 首次觀察期 (沒 log 或 log 沒 post-XXXXX)
        $now = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
        $counter = @{
            git_short_hash = $shortHash
            git_full_hash = $fullHash
            window_start_ts = $now
            n_restarts = 0
            trial_count = 0
            n_cap = $N_CAP
            trial_target = $TRIAL_TARGET
            last_update_ts = $now
        }
        Write-Counter-Atomic $file $counter
        Log-Watch "  new observation window: post-$shortHash (counter initialized, n_restarts=0, trial_count=0)"
    }
    # else: 同觀察期, 沿用現有 counter (下面 main 會 trial++)

    return $counter
}

# === Main ===

# 1. 抓 git HEAD hash (P0-2 必要前置)
$shortHash = Get-GitShortHash
$fullHash = Get-GitFullHash
if ($null -eq $shortHash) {
    Log-Watch "ERROR  cannot read git HEAD hash - P0-2 counter disabled, cap prevents restart (manual Bry intervention required)"
    exit 1
}

# 2. 載入或建立計數器 (hash 變了會自動 reset + 記 rotation)
$counter = Get-Or-Create-Counter $shortHash $fullHash

# 3. trial++ (每次 tick 都加,即使是 OK 狀態也算觀察樣本)
$counter.trial_count += 1
$counter.last_update_ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"

# 4. 健康檢查
$listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*run_server.py*' }
$healthy = ($null -ne $listener) -and ($procs.Count -ge 2)

# 5. 健康: 寫 OK + 帶 observation window 標籤 + 存計數器 (只更新 trial)
$counterFile = Get-CounterFilePath $shortHash
if ($healthy) {
    Write-Counter-Atomic $counterFile $counter
    Log-Watch "OK  post-$shortHash N=$($counter.n_restarts)/$N_CAP trial=$($counter.trial_count)/$TRIAL_TARGET port=$port listener=$($listener.OwningProcess) procs=$($procs.Count)"
    exit 0
}

# 6. 不健康: N≤10 cap 檢查 (P0-2 Bry 拍板)
if ($counter.n_restarts -ge $N_CAP) {
    Write-Counter-Atomic $counterFile $counter
    Log-Watch "ERROR  post-$shortHash N=$($counter.n_restarts)/$N_CAP CAP REACHED - auto-restart BLOCKED, Bry intervention required (check if 10 restarts in one observation window is a real bug or acceptable)"
    exit 2
}

# 7. N++ + 觸發 restart (原有邏輯)
$counter.n_restarts += 1
$counter.last_update_ts = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
Write-Counter-Atomic $counterFile $counter
Log-Watch "WARN  post-$shortHash N=$($counter.n_restarts)/$N_CAP trial=$($counter.trial_count)/$TRIAL_TARGET port_listen=$($null -ne $listener) procs=$($procs.Count) -> restart"

# 8. 砍舊 process
foreach ($p in $procs) {
    try {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        Log-Watch "  killed PID $($p.ProcessId)"
    } catch {}
}
Start-Sleep -Seconds 4

# 9. 拉 Plan A launcher
try {
    $ps = Start-Process -FilePath 'powershell.exe' `
        -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$harness\scripts\_start_plan_a.ps1" `
        -WorkingDirectory $harness `
        -WindowStyle Hidden `
        -PassThru
    Log-Watch "  launched Plan A (PID $($ps.Id))"
} catch {
    Log-Watch "  ERROR launching Plan A: $_"
}
