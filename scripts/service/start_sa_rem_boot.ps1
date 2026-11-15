# start_sa_rem_boot.ps1 - SA-AUTOBOOT-TASK-1 boot + open-door guide (thin rem/primary).
#
# Owner instruction (2026-11-15): thin rem/primary (127.0.0.1:18765) boots /
# logs on unattended, passes health, then opens the door to standby.
#
# What this script does (fixed order, fail-closed):
#   1. Launch the thin entry (loopback-only, closed by construction).
#   2. Health-probe poll (1s interval, max 30 attempts = 30s timeout).
#   3. Read THIS run's control token file, send close -> install -> ready -> open
#      (one shot each, any non-200 stops, fail-closed, no infinite retry).
#   4. Guard loop every 10s: liveness + stop-flag check.
#
# Safety (frozen / iron rules):
#   - Loopback ONLY (127.0.0.1:18765). No --host/--port, no 0.0.0.0.
#   - NEVER Stop-Process / taskkill / kill. Standard stop = disable the
#     scheduled task + write the TEMP stop-flag file (+ Stop-ScheduledTask
#     system reclaim when needed). This script never terminates processes.
#   - NEVER writes under data/** (iron rule 8). Stop flag + logs live under
#     $env:TEMP\sa-rem-boot\ (see deviation A in the ticket).
#   - NEVER embeds any key/token. The control token is read at runtime from
#     the existing control file the thin entry wrote; OLLAMA_API_KEY (if
#     needed by the backend) is resolved by the service process environment,
#     never stored here / in the scheduler / in any config file.
#   - NEVER sends chat/completions. The only HTTP probe is the zero-upstream
#     static models list (or a bare TCP LISTENING check when no bearer is
#     configured). No billing path is touched.
#
# Probe kind (declared for the handover report):
#   LISTENING+MODELS-STATIC. The thin entry exposes NO /healthz, so the
#   script first proves TCP LISTENING on 127.0.0.1:18765, then issues one
#   Bearer-less GET /rem/primary/v1/models per attempt. ANY HTTP reply
#   (200/401/403) proves liveness with zero upstream cost (the models route
#   returns a static list before any provider call); chat/completions is
#   never requested.

$ErrorActionPreference = 'Stop'

# -- single source of truth (unit-tested constants; keep greppable) --------------
$ThinHost = '127.0.0.1'
$ThinPort = 18765
$ProbeIntervalSec = 1
$ProbeMaxAttempts = 30
$ProbeTimeoutSec = 30
$GuardIntervalSec = 10
$ControlEndpoint = '/internal/sa-thin/control'
$ModelsProbePath = '/rem/primary/v1/models'
$ControlSequence = @('close', 'install', 'ready', 'open')
$RepoRoot = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$PythonExe = 'C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe'
$ThinEntry = 'scripts\serve_sa_thin.py'

# Deviation A (iron rule 8 outranks the ticket text): stop flag + logs live
# under system TEMP, never under data/**.
$BootDir = Join-Path $env:TEMP 'sa-rem-boot'
$StopFile = Join-Path $BootDir '.stop_sa_rem'
$BootLog = Join-Path $BootDir 'boot.log'
$BootErrorLog = Join-Path $BootDir 'boot_error.log'

function Write-BootLog([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        $dir = Split-Path -Path $BootLog -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $BootLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[sa-rem-boot] WARN log write failed: $_"
    }
}

function Write-BootError([string]$msg) {
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $msg"
    Write-Host $line
    try {
        $dir = Split-Path -Path $BootErrorLog -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $BootErrorLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[sa-rem-boot] WARN error-log write failed: $_"
    }
}

function Test-PortListening {
    param([string]$Host_, [int]$Port)
    $client = $null
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $iar = $client.BeginConnect($Host_, $Port, $null, $null)
        if (-not $iar.AsyncWaitHandle.WaitOne(1000)) { return $false }
        $client.EndConnect($iar)
        return $true
    } catch {
        return $false
    } finally {
        if ($client -ne $null) { $client.Close() }
    }
}

function Test-ModelsProbe {
    # Zero-upstream static-list probe. Any HTTP status (200/401/403) proves
    # the listener is alive; a connection failure means not-ready yet.
    # Never requests chat/completions (billing path).
    try {
        $url = "http://${ThinHost}:${ThinPort}${ModelsProbePath}"
        $resp = Invoke-WebRequest -Uri $url -Method Get -TimeoutSec 3 -UseBasicParsing -ErrorAction Stop
        return $true
    } catch {
        $webEx = $_.Exception
        if ($webEx -ne $null -and $webEx.Response -ne $null) {
            return $true  # got an HTTP reply (e.g. 401/403): listener alive
        }
        return $false
    }
}

function Send-ControlCommand {
    param([string]$Command, [string]$Token, [string]$RunId)
    # Single shot, zero retry: exactly one POST per door command.
    $url = "http://${ThinHost}:${ThinPort}${ControlEndpoint}"
    $body = @{ token = $Token; command = $Command; run_id = $RunId } | ConvertTo-Json -Compress
    try {
        $resp = Invoke-WebRequest -Uri $url -Method Post -Body $body `
            -ContentType 'application/json' -TimeoutSec 5 -UseBasicParsing -ErrorAction Stop
        return [int]$resp.StatusCode
    } catch {
        $webEx = $_.Exception
        if ($webEx -ne $null -and $webEx.Response -ne $null) {
            return [int]$webEx.Response.StatusCode
        }
        return -1  # lost contact
    }
}

# -- 0. stop-flag short-circuit (disabled stays disabled) --------------------------
if (Test-Path $StopFile) {
    Write-BootLog "stop flag present at $StopFile; staying stopped (fail-closed)."
    exit 0
}

# -- 1. launch the thin entry -------------------------------------------------------
Write-BootLog "launching thin entry on ${ThinHost}:${ThinPort} (closed, loopback-only)."
try {
    Start-Process -FilePath $PythonExe -ArgumentList @('-B', $ThinEntry) `
        -WorkingDirectory $RepoRoot -WindowStyle Hidden
} catch {
    Write-BootError "launch failed: $_"
    exit 2
}

# -- 2. health-probe poll: 1s x 30 = 30s timeout, then fail-closed -------------------
$ready = $false
for ($i = 1; $i -le $ProbeMaxAttempts; $i++) {
    if ((Test-Path $StopFile)) {
        Write-BootLog "stop flag appeared during probe; staying stopped."
        exit 0
    }
    $listening = Test-PortListening -Host_ $ThinHost -Port $ThinPort
    if ($listening) {
        if (Test-ModelsProbe) {
            Write-BootLog "probe attempt $i/${ProbeMaxAttempts}: LISTENING + models-static OK."
            $ready = $true
            break
        }
        Write-BootLog "probe attempt $i/${ProbeMaxAttempts}: listening, models not yet answering."
    } else {
        Write-BootLog "probe attempt $i/${ProbeMaxAttempts}: not listening yet."
    }
    Start-Sleep -Seconds $ProbeIntervalSec
}
if (-not $ready) {
    Write-BootError "probe timeout after ${ProbeTimeoutSec}s (${ProbeMaxAttempts}x${ProbeIntervalSec}s); fail-closed, no retry."
    exit 2
}

# -- 3. open-door guide: close -> install -> ready -> open, one shot each -----------
$controlFile = Join-Path $env:TEMP 'sa-thin-18765.control.json'
try {
    $filed = Get-Content -Path $controlFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $token = $filed.token
    $runId = $filed.run_id
    $endpoint = $filed.endpoint
} catch {
    Write-BootError "unreadable control file; fail-closed (no door command sent)."
    exit 2
}
if ([string]::IsNullOrEmpty($token) -or [string]::IsNullOrEmpty($runId) -or ($endpoint -ne $ControlEndpoint)) {
    Write-BootError "control file refused (run_id/token/endpoint); fail-closed."
    exit 2
}
foreach ($cmd in $ControlSequence) {
    $status = Send-ControlCommand -Command $cmd -Token $token -RunId $runId
    if ($status -ne 200) {
        Write-BootError "door command '$cmd' refused (status $status); fail-closed, stopping sequence."
        exit 2
    }
    Write-BootLog "door command '$cmd' ok (200)."
}
Write-BootLog "door open; entering guard loop."

# -- 4. guard loop: every 10s, liveness + stop-flag ----------------------------------
while ($true) {
    Start-Sleep -Seconds $GuardIntervalSec
    if (Test-Path $StopFile) {
        Write-BootLog "stop flag detected; staying stopped (scheduler must be disabled separately)."
        exit 0
    }
    if (-not (Test-PortListening -Host_ $ThinHost -Port $ThinPort)) {
        Write-BootError "listener lost; fail-closed (no relaunch, no kill)."
        exit 2
    }
}
