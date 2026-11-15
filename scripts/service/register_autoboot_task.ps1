# register_autoboot_task.ps1 - SA-AUTOBOOT-TASK-1 scheduler registration + audit.
#
# Carrier: Windows Task Scheduler, path \SoulOS\SA_Rem_Primary_Daemon,
# trigger AtLogOn (current user, no elevation to SYSTEM), non-interactive
# background (-WindowStyle Hidden).
# Description: "Soul OS SA Rem Primary Service - Authorized by Owner (2026-11-15 standing standby order)".
#
# Usage:
#   .\scripts\service\register_autoboot_task.ps1          # register / refresh
#   .\scripts\service\register_autoboot_task.ps1 -Audit   # read-only audit
#
# Safety: never writes keys/tokens anywhere; never touches data/**;
# never kills processes; never binds production ports.

param(
    [switch]$Audit
)

$ErrorActionPreference = 'Stop'

$TaskFolder = '\SoulOS\'
$TaskName = 'SA_Rem_Primary_Daemon'
$TaskFullPath = '\SoulOS\SA_Rem_Primary_Daemon'
$TaskDescription = 'Soul OS SA Rem Primary Service - Authorized by Owner (2026-11-15 standing standby order)'
$BootScript = 'C:\Users\bbfcc\.local\bin\soul-os-harness\scripts\service\start_sa_rem_boot.ps1'
$PSExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$BootArgs = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $BootScript + '"'

function Get-TaskAudit {
    $t = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($null -eq $t) {
        Write-Host "[sa-autoboot] task missing: $TaskFullPath"
        return 1
    }
    $info = Get-ScheduledTaskInfo -TaskPath $TaskFolder -TaskName $TaskName -ErrorAction SilentlyContinue
    Write-Host "[sa-autoboot] task: $($t.TaskPath)$($t.TaskName)"
    Write-Host "[sa-autoboot] state: $($t.State)"
    if ($null -ne $info) {
        Write-Host "[sa-autoboot] lastRun: $($info.LastRunTime) lastResult: $($info.LastTaskResult)"
    }
    foreach ($a in $t.Actions) {
        Write-Host "[sa-autoboot] action: $($a.Execute) $($a.Arguments)"
    }
    foreach ($tr in $t.Triggers) {
        Write-Host "[sa-autoboot] trigger: $($tr.CimClass.CimClassName) enabled=$($tr.Enabled)"
    }
    if ($t.State -eq 'Ready') { return 0 }
    Write-Host "[sa-autoboot] NOT Ready (state=$($t.State))"
    return 2
}

if ($Audit) {
    exit (Get-TaskAudit)
}

# -- register / refresh (idempotent) ------------------------------------------------
$action = New-ScheduledTaskAction -Execute $PSExe -Argument $BootArgs
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Enabled = $true
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

$existing = Get-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Unregister-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName -Confirm:$false
    Write-Host "[sa-autoboot] removed stale registration, re-registering."
}

Register-ScheduledTask -TaskPath $TaskFolder -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description $TaskDescription | Out-Null
Write-Host "[sa-autoboot] registered: $TaskFullPath (AtLogOn, user=$env:USERNAME, Hidden, no SYSTEM elevation)."

exit (Get-TaskAudit)
