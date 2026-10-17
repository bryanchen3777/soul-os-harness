<#
  vc_ops.ps1 — Voice Companion 三實例的啟停／狀態管理（VC-REFINER-2 附帶）

  為什麼需要這個腳本
  ------------------
  :8000 主服務一直有 scripts/server_ops.ps1 負責 start/stop/status/restart，
  但三個 VC 語音實例（8765 黒川茜 / 8766 櫻島麻衣 / 8767 雷姆）**從來沒有任何
  官方重啟路徑** —— 歷史上都是人手動開 pythonw.exe。缺了這條路，任何要讓 VC 改動
  生效的重啟都只能靠「殺行程」，那正是 AGENTS.md 第 1 條明令禁止的動作。
  本腳本把重啟變成**可稽核、可預先檢視、不需臨時殺行程**的固定動作。

  三個實例的啟動命令（2026-10-16 實測自 production 行程命令列，逐字沿用）：
    8765  黒川茜  pythonw -u -m clients.voice_companion.web_server --https
    8766  櫻島麻衣  pythonw -u -m clients.voice_companion.web_server --config clients/voice_companion/profiles/mai.json --https
    8767  雷姆      pythonw -u -m clients.voice_companion.web_server --config clients/voice_companion/profiles/rem.json --https

  ⚠️ 小茜**沒有** profiles/akane.json —— clients/voice_companion/config.json 就是
     她的預設 config，所以啟動時**不帶** --config。這是結構不對稱，不是 bug。

  用法：
    .\scripts\vc_ops.ps1 status      # 只讀：列出三埠的 listener / PID / 命令列
    .\scripts\vc_ops.ps1 restart     # 停掉三個實例並以相同命令重新拉起
    .\scripts\vc_ops.ps1 start       # 只啟動「沒在跑」的實例
    .\scripts\vc_ops.ps1 stop        # 只停
    .\scripts\vc_ops.ps1 help

  ⚠️ 停止的作法與 scripts/server_ops.ps1 一致：先找出**該埠的 socket owner**，
     再 taskkill /T /F 整棵樹（/T 確保 uv-managed 的 python 子進程也收乾淨）。
     絕不依 PID 大小或名稱猜測要殺誰。
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('start','stop','status','restart','help')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
$opsLog = Join-Path $root 'data\logs\vc_ops.log'

# 實例定義：Port / 代號 / 額外參數
$INSTANCES = @(
    [pscustomobject]@{ Port = 8765; Tag = 'akane'; Extra = @() }
    [pscustomobject]@{ Port = 8766; Tag = 'mai';  Extra = @('--config', 'clients/voice_companion/profiles/mai.json') }
    [pscustomobject]@{ Port = 8767; Tag = 'rem';  Extra = @('--config', 'clients/voice_companion/profiles/rem.json') }
)

function Write-OpsLog([string]$msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    try {
        $dir = Split-Path -Path $opsLog -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $opsLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[warn] ops log unavailable: $($_.Exception.Message)"
    }
}

function Get-Listener([int]$port) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-VcProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*clients.voice_companion.web_server*' }
}

function Show-Status {
    Write-OpsLog '[status] === VC 三實例狀態 ==='
    $procs = Get-VcProcesses
    foreach ($inst in $INSTANCES) {
        $l = Get-Listener $inst.Port
        if ($l) {
            $pid_ = $l.OwningProcess
            $proc = $procs | Where-Object { $_.ProcessId -eq $pid_ } | Select-Object -First 1
            $cmd = if ($proc) { $proc.CommandLine } else { '<process not matched>' }
            $launcher = if ($proc) { $proc.ParentProcessId } else { '?' }
            Write-OpsLog ("[status] {0} :{1} LISTEN pid={2} launcher={3}" -f $inst.Tag, $inst.Port, $pid_, $launcher)
            Write-OpsLog ("[status]      cmd={0}" -f $cmd)
        } else {
            Write-OpsLog ("[status] {0} :{1} NOT LISTENING" -f $inst.Tag, $inst.Port)
        }
    }
}

function Start-Instance($inst) {
    if (Get-Listener $inst.Port) {
        Write-OpsLog ("[start] {0} :{1} 已在監聽，略過" -f $inst.Tag, $inst.Port)
        return
    }
    $argList = @('-u', '-m', 'clients.voice_companion.web_server') + $inst.Extra + @('--https')
    $p = Start-Process -FilePath $pythonw -ArgumentList $argList -WorkingDirectory $root -PassThru
    Write-OpsLog ("[start] {0} :{1} launched launcher_pid={2} args={3}" -f $inst.Tag, $inst.Port, $p.Id, ($argList -join ' '))
}

function Stop-Instance($inst) {
    $l = Get-Listener $inst.Port
    if (-not $l) {
        Write-OpsLog ("[stop] {0} :{1} 本來就沒在跑" -f $inst.Tag, $inst.Port)
        return
    }
    $pid_ = $l.OwningProcess
    try {
        & taskkill /PID $pid_ /T /F 2>&1 | Out-Null
        Write-OpsLog ("[stop] {0} :{1} killed listener tree PID={2}" -f $inst.Tag, $inst.Port, $pid_)
    } catch {
        Write-OpsLog ("[stop] {0} :{1} WARN taskkill PID={2} 失敗: {3}" -f $inst.Tag, $inst.Port, $pid_, $_)
    }
    # 等埠釋放
    $until = (Get-Date).AddSeconds(15)
    while ((Get-Listener $inst.Port) -and ((Get-Date) -lt $until)) { Start-Sleep -Milliseconds 300 }
    if (Get-Listener $inst.Port) {
        Write-OpsLog ("[stop] {0} :{1} 埠未釋放" -f $inst.Tag, $inst.Port)
    }
}

switch ($Action) {
    'status' { Show-Status }
    'start'  { foreach ($i in $INSTANCES) { Start-Instance $i } }
    'stop'   { foreach ($i in $INSTANCES) { Stop-Instance $i } }
    'restart' {
        Write-OpsLog '[restart] 依序停止三個實例…'
        foreach ($i in $INSTANCES) { Stop-Instance $i }
        Write-OpsLog '[restart] 依序重新啟動…'
        foreach ($i in $INSTANCES) { Start-Instance $i }
        Start-Sleep -Seconds 6
        Show-Status
    }
    'help' {
        Write-Host @'
用法：.\scripts\vc_ops.ps1 <action>
  status    只讀列出 8765/8766/8767 的 listener、PID 與完整命令列
  start     啟動「目前沒在跑」的實例
  stop      停止三個實例（以埠的 socket owner 為準，taskkill /T /F 整棵樹）
  restart   stop + start，並於結束時列出狀態
'@
    }
}
