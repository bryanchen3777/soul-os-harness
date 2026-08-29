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
# P0-2↔P1 解耦 (Bry 拍板 2026-07-31 20:25, β1 方案 2): 獨立狀態檔,
# 不依賴 watchdog.log 完整性 / regex 解析, 之後 P1 (faulthandler.log rotation)
# 動到 log 輪替策略時不會波及 hash-change 偵測
$lastObservedHashFile = Join-Path $stateDir '_last_observed_hash.txt'
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
# 設計 (β1 方案 2 升級, Bry 拍板 2026-07-31 20:25):
#   * 觀察期 hash 改存 _last_observed_hash.txt (獨立狀態檔), 不依賴 watchdog.log
#   * log regex parse 保留當備援 (雙保險, P0-2 原始設計)
#   * 解耦理由: P1 (faulthandler.log rotation) 動到 log 輪替策略時不會波及偵測
#   * 方案 1 (保留 log 最後一筆 tag) 被 Bry 7/31 20:25 拍板否決:
#     隱性約束靠人記得維護, 時間拉長後必然會被忘記一次

function Read-LastObservedHashFile {
    # 從 _last_observed_hash.txt 讀, 成功回傳 hash, 失敗回傳 $null
    # 失敗情境: 檔案不存在 (首次觀察期), 或檔案內容空白, 或讀取錯誤
    try {
        if (-not (Test-Path $lastObservedHashFile)) { return $null }
        $content = [System.IO.File]::ReadAllText($lastObservedHashFile, [System.Text.Encoding]::UTF8)
        $isBlank = ($content -eq '') -or ($content -eq $null)
        if ($isBlank) { return $null }
        $hash = $content.Trim()
        # 驗證格式: 7 字符 hex (git short hash) 或 40 字符 hex (full hash) 或 'unknown'
        if ($hash -match '^[a-f0-9]{7,40}$') { return $hash }
        return $null
    } catch { return $null }
}

function Write-LastObservedHashFile-Atomic {
    # 寫入 _last_observed_hash.txt, atomic write 防 race
    # 寫入失敗會 log 但不 throw (counter 寫入失敗也容許繼續)
    param([string]$hash)
    try {
        $tmpFile = "$lastObservedHashFile.tmp.$([System.Diagnostics.Process]::GetCurrentProcess().Id)"
        [System.IO.File]::WriteAllText($tmpFile, $hash, [System.Text.Encoding]::UTF8)
        Move-Item -Path $tmpFile -Destination $lastObservedHashFile -Force
    } catch {
        Log-Watch "  ERROR writing _last_observed_hash.txt: $_"
    }
}

function Get-LastObservedHash {
    # β1 方案 2: 優先讀 _last_observed_hash.txt, 失敗才 fallback log regex (備援)
    # 這層 fallback 保留 P0-2 原始邏輯, 確保 _last_observed_hash.txt 被外部刪除
    # 或損壞時, watchdog 仍能從 log 偵測 rotation
    $hash = Read-LastObservedHashFile
    if ($null -ne $hash) { return $hash }

    # fallback: 從 watchdog.log 最後一筆 OK/WARN/ERROR log 解析 post-XXXXX 標籤
    try {
        if (-not (Test-Path $logFile)) { return $null }
        $tail = Get-Content $logFile -Tail 20 -Encoding UTF8 -ErrorAction SilentlyContinue
        for ($i = $tail.Count - 1; $i -ge 0; $i--) {
            if ($tail[$i] -match 'post-([a-f0-9]+)') {
                $logHash = $matches[1]
                # fallback 成功: 順便把 log 解析的 hash 同步寫入 .txt
                # 讓 .txt 跟現有 state 接軌, 下次 tick 直接走 .txt 路徑
                Write-LastObservedHashFile-Atomic $logHash
                return $logHash
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
        # β1 方案 2: 同步寫 _last_observed_hash.txt, 跟 counter 一起保持原子性
        # 這樣下次 tick 直接走 .txt 路徑, 不再依賴 log regex
        Write-LastObservedHashFile-Atomic $shortHash
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
        # β1 方案 2: 首次觀察期也同步寫 _last_observed_hash.txt
        Write-LastObservedHashFile-Atomic $shortHash
        Log-Watch "  new observation window: post-$shortHash (counter initialized, n_restarts=0, trial_count=0)"
    }
    # else: 同觀察期, 沿用現有 counter (下面 main 會 trial++)

    return $counter
}

# === Main ===

# 0. 維護窗口檢查 (修 server_ops restart 競態, Bry 派工 2026-08-29):
#    server_ops.ps1 restart 期間寫 data\state\watchdog_maintenance.lock,
#    watchdog 看到就 SKIP 本次 tick (不讀/不寫 counter, 不 N++, 不拉 Plan A),
#    避免在重啟窗口誤判不健康而拉起第二實例 (Telegram token Conflict)。
#    lock 超過 10 分鐘視為 stale (restart 中途崩潰殘留), 忽略並刪除,
#    避免 watchdog 永久停擺。此分支不改 CAP/restart 核心邏輯。
$maintenanceLock = Join-Path $stateDir 'watchdog_maintenance.lock'
if (Test-Path $maintenanceLock) {
    $lockAgeMin = $null
    try {
        $lockAgeMin = [int](((Get-Date) - (Get-Item $maintenanceLock).LastWriteTime).TotalMinutes)
    } catch {}
    if ($null -eq $lockAgeMin -or $lockAgeMin -le 10) {
        Log-Watch "SKIP  maintenance window (server_ops restart in progress, lock age=${lockAgeMin}m) - watchdog tick skipped"
        exit 0
    } else {
        Log-Watch "WARN  maintenance lock stale (${lockAgeMin}m > 10m) - removing and continuing"
        Remove-Item $maintenanceLock -Force -ErrorAction SilentlyContinue
    }
}

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

# 4.5 Bry 拍板 2026-08-03 13:40: event loop self-check 偵測
# 動機: 2026-08-03 02:15 hang (d190c96 觀察期) 之前沒早期信號, 4 小時才被 port 偵測
# 抓出. self-check 寫 data/state/event_loop_alive.json, 這裡順便看最後修改時間.
# 超過 interval * 2.5 (預設 25 min) 沒更新 → WARN (給 Bry 看, 不自動重啟).
# 跟 port 偵測並行, 兩條都 OK 算健康; 任一條 WARN 給 Bry 看.
$selfCheckPath = Join-Path $stateDir 'event_loop_alive.json'
$selfCheckStale = $false
$selfCheckSecondsSinceUpdate = $null
if (Test-Path $selfCheckPath) {
    $lastAlive = (Get-Item $selfCheckPath).LastWriteTime
    $selfCheckSecondsSinceUpdate = [int]((Get-Date) - $lastAlive).TotalSeconds
    # 從檔案讀 interval (預設 600s, 跟 run_server SOULOS_SELF_CHECK_INTERVAL_SECS 一致)
    $selfCheckInterval = 600
    try {
        $sc = Get-Content $selfCheckPath -Raw | ConvertFrom-Json
        if ($sc.interval_seconds) { $selfCheckInterval = [int]$sc.interval_seconds }
    } catch {}
    $selfCheckWarnThreshold = $selfCheckInterval * 2.5
    if ($selfCheckSecondsSinceUpdate -gt $selfCheckWarnThreshold) {
        $selfCheckStale = $true
        Log-Watch "WARN  self-check 超過 ${selfCheckWarnThreshold}s 沒更新 (實際 ${selfCheckSecondsSinceUpdate}s, interval=${selfCheckInterval}s), 給 Bry 看但不重啟"
    }
}

# 5. 健康: 寫 OK + 帶 observation window 標籤 + 存計數器 (只更新 trial)
$counterFile = Get-CounterFilePath $shortHash
if ($healthy) {
    Write-Counter-Atomic $counterFile $counter
    if ($selfCheckStale) {
        Log-Watch "OK+SELFWARN  post-$shortHash N=$($counter.n_restarts)/$N_CAP trial=$($counter.trial_count)/$TRIAL_TARGET port=$port listener=$($listener.OwningProcess) procs=$($procs.Count) self_check=${selfCheckSecondsSinceUpdate}s"
    } else {
        Log-Watch "OK  post-$shortHash N=$($counter.n_restarts)/$N_CAP trial=$($counter.trial_count)/$TRIAL_TARGET port=$port listener=$($listener.OwningProcess) procs=$($procs.Count)"
    }
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
