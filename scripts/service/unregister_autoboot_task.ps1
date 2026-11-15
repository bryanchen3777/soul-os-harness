# unregister_autoboot_task.ps1 - SA-AUTOBOOT-TASK-1 one-click revoke + disable.
#
# Standard stop (no killing, ever):
#   1. Disable-ScheduledTask (no more auto-start),
#   2. write the TEMP stop-flag file so a running boot script exits cleanly,
#   3. optionally Stop-ScheduledTask (system reclaim of the running task,
#      NOT a bare kill) and optionally -Remove the registration.
#
# Usage:
#   .\scripts\service\unregister_autoboot_task.ps1           # disable + flag
#   .\scripts\service\unregister_autoboot_task.ps1 -Remove  # also unregister
#
# Safety: never Stop-Process / taskkill / kill; never touches data/**;
# never writes keys/tokens. Stop flag lives under $env:TEMP\sa-rem-boot\.

param(
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$TaskFolder = '\SoulOS\'
$TaskName = 'SA_Rem_Primary_Daemon'
$TaskFullPath = '\SoulOS\SA_Rem_Primary_Daemon'
$BootDir = Join-Path $env:TEMP 'sa-rem-boot'
$StopFile = Join-Path $BootDir '.stop_sa_rem'

# -- 1. stop-flag first: a running boot script sees it within 10s and exits ------
try {
    if (-not (Test-Path $BootDir)) { New-Item -ItemType Directory -Path $BootDir -Force | Out-Null }
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Set-Content -Path $StopFile -Value "stopped at $ts by unregister_autoboot_task" -Encoding UTF8
    Write-Host "[sa-autoboot] stop flag written: $StopFile"
} catch {
    Write-Host "[sa-autoboot] WARN stop-flag write failed: $_"
}

# -- 2. disable the schedule (no more auto-start) ----------------------------------
$existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    Write-Host "[sa-autoboot] task not present: $TaskFullPath (nothing to disable)."
    exit 0
}
Disable-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName | Out-Null
Write-Host "[sa-autoboot] disabled: $TaskFullPath"

# -- 3. system reclaim of a currently-running task instance (not a bare kill) ------
try {
    Stop-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
    Write-Host "[sa-autoboot] running instance reclaim requested (if any)."
} catch {
    Write-Host "[sa-autoboot] WARN reclaim request failed: $_"
}

if ($Remove) {
    Unregister-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -Confirm:$false
    Write-Host "[sa-autoboot] unregistered: $TaskFullPath"
}

exit 0
