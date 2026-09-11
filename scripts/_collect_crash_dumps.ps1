# _collect_crash_dumps.ps1 - WER crash dump 收集器 (CRASH-OBS-1, additive)
# 用途: 在 Windows WER 系統清理之前, 把 AppCrash_python.exe_* 報告
#       (.mdmp + Report.wer) 複製到 data/crash_dumps/<時戳>_<原目錄名>/,
#       保留最近 5 份 (盲區: 目前沒有任何機制收集 WER dump)。
#
# 設計 (工單決策已定):
#   - 由 _watchdog.ps1 每 5 分鐘以獨立 process 呼叫; 本腳本任何失敗都吞掉,
#     exit 0 — 0 崩潰: 收集失敗絕不影響 watchdog / 主服務
#   - 權限不足 / 目錄不存在 / 檔案被鎖 → 記 INFO/WARNING, 靜默跳過
#   - ProgramData 讀不到時降級到 %LOCALAPPDATA% 的 WER ReportArchive
#   - 不需要管理員權限; 只複製不刪除 WER 原檔
#   - lock 檔防 concurrent (watchdog 每 5 分鐘 + 手動同時跑)
$ErrorActionPreference = 'Continue'
$root = if ($env:CRASH_DUMP_COLLECTOR_ROOT) { $env:CRASH_DUMP_COLLECTOR_ROOT } else { 'C:\Users\bbfcc\.local\bin\soul-os-harness' }
$logFile = Join-Path $root 'data\logs\crash_dump_collector.log'
$destBase = Join-Path $root 'data\crash_dumps'
$stateFile = Join-Path $destBase '.last_collected.txt'
$lockFile = Join-Path $destBase '.collector.lock'
$keep = 5
$lockMaxAgeMin = 10

function Log-Collector([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    try {
        $dir = Split-Path -Path $logFile -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $logFile -Value $line -Encoding UTF8
    } catch {}
}

# === lock (防 concurrent; stale lock 直接覆寫) ===
if (Test-Path $lockFile) {
    try {
        $lockAgeMin = [int](((Get-Date) - (Get-Item $lockFile).LastWriteTime).TotalMinutes)
        if ($lockAgeMin -lt $lockMaxAgeMin) { exit 0 }
    } catch {}
}
try {
    if (-not (Test-Path $destBase)) { New-Item -ItemType Directory -Path $destBase -Force -ErrorAction SilentlyContinue | Out-Null }
    Set-Content -Path $lockFile -Value (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') -Encoding UTF8 -ErrorAction Stop
} catch {
    Log-Collector "WARN cannot write lock file: $_ (continue anyway)"
}

# === 上次收集時間 (無狀態檔 = 首次收集, 全量收集一次) ===
$lastCollected = $null
try {
    if (Test-Path $stateFile) {
        $lastCollected = ([System.IO.File]::ReadAllText($stateFile)).Trim()
    }
} catch {}

# === WER ReportArchive 掃描 (ProgramData 優先, LOCALAPPDATA 降級) ===
$werBases = if ($env:CRASH_DUMP_WER_BASES) {
    @($env:CRASH_DUMP_WER_BASES -split ';' | Where-Object { $_ })
} else {
    @(
        'C:\ProgramData\Microsoft\Windows\WER\ReportArchive',
        (Join-Path $env:LOCALAPPDATA 'Microsoft\Windows\WER\ReportArchive')
    )
}
$searched = $false
foreach ($base in $werBases) {
    if (-not (Test-Path $base)) { continue }
    $searched = $true
    $dirs = $null
    try {
        $dirs = @(Get-ChildItem -Path $base -Directory -Filter 'AppCrash_python.exe_*' -ErrorAction SilentlyContinue)
    } catch {
        Log-Collector "WARN cannot list WER ReportArchive ($base) - degrade gracefully: $_"
        continue
    }
    if ($null -eq $dirs -or $dirs.Count -eq 0) { continue }
    foreach ($dir in $dirs) {
        try {
            # 該報告內 .mdmp / Report.wer 的最新 mtime (≈崩潰時刻) —
            # 比上次收集更新才收集 (首次收集 = 全部)
            $newest = Get-ChildItem -Path $dir.FullName -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like '*.dmp' -or $_.Name -eq 'Report.wer' } |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($null -eq $newest) { continue }
            if ($null -ne $lastCollected) {
                $newestTs = $newest.LastWriteTime.ToString('yyyy-MM-ddTHH:mm:ss')
                if ([string]::CompareOrdinal($newestTs, $lastCollected) -le 0) { continue }
            }
            $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
            $dest = Join-Path $destBase "${ts}_$($dir.Name)"
            New-Item -ItemType Directory -Path $dest -Force -ErrorAction SilentlyContinue | Out-Null
            $copied = 0
            Get-ChildItem -Path $dir.FullName -File -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like '*.dmp' -or $_.Name -eq 'Report.wer' } |
                ForEach-Object {
                    try {
                        Copy-Item -Path $_.FullName -Destination $dest -Force -ErrorAction Stop
                        $copied++
                    } catch {
                        Log-Collector "WARN copy failed for $($_.Name) (locked?): $_ (skip)"
                    }
                }
            Log-Collector "INFO collected $($dir.Name) -> $dest ($copied files)"
        } catch {
            Log-Collector "WARN collect failed for $($dir.Name): $_ (skip)"
        }
    }
}

# === 更新狀態 + 保留最近 5 份 ===
try {
    Set-Content -Path $stateFile -Value (Get-Date -Format 'yyyy-MM-ddTHH:mm:ss') -Encoding UTF8 -ErrorAction SilentlyContinue
} catch {}
try {
    $collected = @(
        Get-ChildItem -Path $destBase -Directory -Filter '*_AppCrash_python.exe_*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
    )
    for ($i = $keep; $i -lt $collected.Count; $i++) {
        Remove-Item $collected[$i].FullName -Recurse -Force -ErrorAction SilentlyContinue
        Log-Collector "pruned old collection $($collected[$i].Name)"
    }
} catch {}

try { Remove-Item $lockFile -Force -ErrorAction SilentlyContinue } catch {}
if (-not $searched) {
    Log-Collector "INFO no WER ReportArchive found (both bases absent) - nothing to collect"
}
exit 0