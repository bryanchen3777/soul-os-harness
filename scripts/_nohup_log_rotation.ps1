# _nohup_log_rotation.ps1 - shared nohup log rotation helper (CRASH-OBS-1)
# 用途: 給 _start_plan_a.ps1 / server_ops.ps1 dot-source 的共用輪替函式。
#    . (Join-Path $PSScriptRoot '_nohup_log_rotation.ps1')
#
# 背景 (盲區 i): Start-Process -RedirectStandardOutput/-RedirectStandardError 是
# truncate 模式, 每次重啟都把既有 data/server_nohup.{log,err} 清空 →
# 崩潰實例的最後輸出被下一次重啟清掉 (實測 data/server_nohup.err 只剩當前實例)。
#
# 做法:
#   - 啟動前把 data/server_nohup.{err,log,out} (存在且非空) 改名保留為
#     data/logs/server_nohup.<yyyyMMdd_HHmmss>.<ext> (崩潰實例日誌永遠存活到被輪替)
#   - 各 ext 只保留最近 $Keep 份 (預設 5), 防止無限增長
#   - 名字/路徑只含日期時間, 不會洩漏密碼或敏感資訊
#   - 任何失敗只記 WARN (透過 $LogFn 或 Write-Host), 絕不阻擋啟動流程
#
# 測試: 見 CRASH-OBS-1 驗收 (dot-source 本檔後以暫存目錄離線執行)。
function Backup-Rotate-NohupLogs {
    param(
        [Parameter(Mandatory = $true)][string]$Root,
        [Parameter(Mandatory = $true)][string]$BackupDir,
        [int]$Keep = 5,
        [scriptblock]$LogFn = $null
    )
    function Write-NohupRotateLog {
        param([string]$msg)
        if ($null -ne $LogFn) { & $LogFn $msg } else { Write-Host $msg }
    }
    try {
        if (-not (Test-Path $BackupDir)) {
            New-Item -ItemType Directory -Path $BackupDir -Force -ErrorAction Stop | Out-Null
        }
        $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
        foreach ($ext in @('err', 'log', 'out')) {
            $src = Join-Path $Root "data\server_nohup.$ext"
            if ((Test-Path $src) -and ((Get-Item $src -ErrorAction SilentlyContinue).Length -gt 0)) {
                $dst = Join-Path $BackupDir "server_nohup.${ts}.${ext}"
                Move-Item -Path $src -Destination $dst -Force -ErrorAction Stop
                Write-NohupRotateLog "rotated $src -> $dst"
            }
        }
        # 保留策略: 各 ext 最多保留最近 $Keep 份 (較舊的刪除, 防無限增長)
        foreach ($ext in @('err', 'log', 'out')) {
            $files = @(
                Get-ChildItem -Path $BackupDir -Filter "server_nohup.*.${ext}" -File -ErrorAction SilentlyContinue |
                    Sort-Object Name -Descending
            )
            for ($i = $Keep; $i -lt $files.Count; $i++) {
                Remove-Item $files[$i].FullName -Force -ErrorAction SilentlyContinue
                Write-NohupRotateLog "pruned old backup $($files[$i].Name)"
            }
        }
    } catch {
        Write-NohupRotateLog "WARN nohup log rotation failed (continue, start anyway): $_"
    }
}