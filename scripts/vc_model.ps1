<#
  vc_model.ps1 — VC 語音模型切換／退回（TTS + ASR）：帶拼錯防呆 ＋ 實例對帳

  為什麼需要這個腳本
  ------------------
  2026-10-24：VC 的模型從 TTS `s2.1-pro` / ASR `transcribe-1` 換成 `drama-3` / `transcribe-1-pro`。
  兩個實測確認的陷阱，使「手改三份 JSON」不夠安全：

  ① **模型是用 HTTP header `model` 選的，不是 JSON body 欄位**；header 缺或**拼錯不會報錯**，
     會安靜 fallback（實測：header 寫 `drama_3` → 靜默退回 s2.1-pro，RMS／成聲比與
     s2.1-pro 完全一致）。⇒ 「設定改了」與「真的換了模型」是兩件事，必須能對帳。

  ② **模型名就是功能開關**：drama-3 專屬能力（語言標籤注入、表演標記白名單、prompt 附加段）
     全部以模型名為條件（`supports_language_tags()` / `supports_drama3_markup()`）。
     ⇒ **退回只需改模型名**，其餘程式與設定不必動，也不必重做守門。

  白名單與編輯邏輯的**單一事實來源**＝`clients/voice_companion/model_registry.py`
  （本腳本只做編排：對帳／寫入／重啟／稽核日誌；實際編輯交給該模組，避免兩份實作漂移）。

  用法：
    .\scripts\vc_model.ps1 status
        對帳：① 三份設定的 model / asr_model ② 三實例「實際生效」值（讀啟動日誌）
        ③ 兩者是否一致（不一致＝實例還在跑舊模型，必須重啟才生效）

    .\scripts\vc_model.ps1 set -TtsModel drama-3 -AsrModel transcribe-1-pro
        寫入三份設定（只動 fish_audio 區塊內的 model / asr_model）
    .\scripts\vc_model.ps1 set -TtsModel s2.1-pro
    .\scripts\vc_model.ps1 rollback
        退回 TTS=s2.1-pro（ASR 不動，除非另外給 -AsrModel）
    .\scripts\vc_model.ps1 rollback -Restart
        退回並重啟（**重啟屬生產動作，需 Owner 授權**；重啟走 scripts\vc_ops.ps1 的既有路徑）
    .\scripts\vc_model.ps1 help

  防呆（逐條都對應上面的一個真實陷阱）：
    * 模型名必須在官方白名單內 ⇒ 拼錯在**寫入前**就被擋下，不會變成靜默 fallback。
    * 寫入後重新讀檔驗證：JSON 仍可解析 ＋ 值確實改到 ＋ llm.model 未被誤傷。
    * 絕不碰 `llm` 區塊的 `model`（那是 LLM 模型，與 TTS 無關）。
    * 預設**不**重啟：改了設定但沒重啟，實例仍在跑舊模型 ⇒ 腳本會明講並給出重啟指令，
      不做「看起來生效」的無聲 no-op。
#>

param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('status','set','rollback','help')]
    [string]$Action,
    [string]$TtsModel,
    [string]$AsrModel,
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$root = 'C:\Users\bbfcc\.local\bin\soul-os-harness'
$modelLog = Join-Path $root 'data\logs\vc_model.log'
$python = Join-Path $root '.venv\Scripts\python.exe'

# ── 三個實例：代號 / 埠 / 設定檔 / 啟動日誌 ──
$INSTANCES = @(
    [pscustomobject]@{
        Tag = 'akane'; Port = 8765
        Config = 'clients\voice_companion\config.json'
        Log    = 'clients\voice_companion\logs\vc_agent_akane.log'
    }
    [pscustomobject]@{
        Tag = 'mai'; Port = 8766
        Config = 'clients\voice_companion\profiles\mai.json'
        Log    = 'clients\voice_companion\logs\vc_agent_mai.log'
    }
    [pscustomobject]@{
        Tag = 'rem'; Port = 8767
        Config = 'clients\voice_companion\profiles\rem.json'
        Log    = 'clients\voice_companion\logs\vc_agent_rem.log'
    }
)

function Write-ModelLog([string]$msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line
    try {
        $dir = Split-Path -Path $modelLog -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $modelLog -Value $line -Encoding UTF8
    } catch {
        Write-Host "[warn] model log unavailable: $($_.Exception.Message)"
    }
}

# ── 呼叫 model_registry（白名單與編輯邏輯的單一事實來源）──
function Invoke-Registry([string[]]$argsList) {
    $full = @('-m', 'clients.voice_companion.model_registry') + $argsList
    Push-Location $root
    try {
        $out = & $python @full 2>&1
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    if ($code -ne 0) {
        throw ("model_registry 失敗（exit {0}）：{1}" -f $code, ($out -join "`n"))
    }
    return ($out -join "`n")
}

function Get-Allowed {
    $json = Invoke-Registry @('allowed')
    return ($json | ConvertFrom-Json)
}

function Get-ConfiguredModels([string]$relPath) {
    $json = Invoke-Registry @('get', '--file', $relPath)
    return ($json | ConvertFrom-Json)
}

function Set-ConfiguredModels([string]$relPath, [string]$tts, [string]$asr) {
    $a = @('set', '--file', $relPath)
    if ($tts) { $a += @('--tts', $tts) }
    if ($asr) { $a += @('--asr', $asr) }
    $json = Invoke-Registry $a
    return ($json | ConvertFrom-Json)
}

function Get-RunningModel([string]$logRelPath) {
    $full = Join-Path $root $logRelPath
    if (-not (Test-Path $full)) { return $null }
    # ⚠️ 不可用 -Tail 視窗：雷姆的日誌有三萬多行，startup 行早已被推出視窗外
    #    （⇒ 會誤報「讀不到啟動日誌」，看起來像實例沒跑）。改讀完整檔取最後一筆。
    $line = Get-Content $full -ErrorAction SilentlyContinue |
        Where-Object { $_ -like '*startup agent_id=*' } | Select-Object -Last 1
    if (-not $line) { return $null }
    $t = [regex]::Match($line, '(?:^|\s)model=(\S+)')
    $a = [regex]::Match($line, '(?:^|\s)asr_model=(\S+)')
    return [pscustomobject]@{
        Tts = $(if ($t.Success) { $t.Groups[1].Value } else { '' })
        Asr = $(if ($a.Success) { $a.Groups[1].Value } else { '' })
        Stamp = $line.Substring(0, [Math]::Min(19, $line.Length))
    }
}

function Show-Status {
    Write-ModelLog '[status] === VC 模型對帳（設定 vs 實例實際生效）==='
    $allowed = Get-Allowed
    Write-Host ("允許的模型：TTS {0} ／ ASR {1}" -f ($allowed.tts -join ' '), ($allowed.asr -join ' '))
    Write-Host ""
    foreach ($inst in $INSTANCES) {
        $cfg = Get-ConfiguredModels $inst.Config
        $run = Get-RunningModel $inst.Log
        Write-Host ("[{0}] :{1}" -f $inst.Tag, $inst.Port)
        Write-Host ("   設定      TTS={0}  ASR={1}" -f $cfg.model, $cfg.asr_model)
        if ($run) {
            Write-Host ("   實例生效  TTS={0}  ASR={1}   (啟動日誌 {2})" -f $run.Tts, $run.Asr, $run.Stamp)
            if ($run.Tts -eq $cfg.model -and $run.Asr -eq $cfg.asr_model) {
                Write-Host "   ⇒ 一致（設定與實例相同）"
            } else {
                Write-Host "   ⇒ ⚠️ 不一致：實例仍在跑舊模型，必須重啟才會套用設定"
            }
        } else {
            Write-Host "   實例生效  （讀不到啟動日誌）"
        }
    }
    Write-Host ""
    Write-Host "提醒：Fish Audio 的模型是 HTTP header 選的，**拼錯不會報錯、會安靜 fallback**。"
    Write-Host "      退回請用：.\scripts\vc_model.ps1 rollback [-Restart]"
}

function Invoke-Set([string]$tts, [string]$asr, [bool]$doRestart) {
    $allowed = Get-Allowed
    if ($tts -and ($allowed.tts -notcontains $tts)) {
        throw ("TTS 模型名不在官方白名單內：'{0}'（允許：{1}）" -f $tts, ($allowed.tts -join ' / '))
    }
    if ($asr -and ($allowed.asr -notcontains $asr)) {
        throw ("ASR 模型名不在官方白名單內：'{0}'（允許：{1}）" -f $asr, ($allowed.asr -join ' / '))
    }

    foreach ($inst in $INSTANCES) {
        $r = Set-ConfiguredModels $inst.Config $tts $asr
        Write-ModelLog ("[set] {0}: model {1} -> {2}  asr_model {3} -> {4}" -f `
            $inst.Config, $r.old_model, $r.new_model, $r.old_asr_model, $r.new_asr_model)
    }

    if ($doRestart) {
        Write-ModelLog '[set] 重啟三實例（官方路徑 scripts\vc_ops.ps1 restart）…'
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\vc_ops.ps1') restart
        Write-ModelLog '[set] 重啟完成（下面顯示生效後的對帳）'
        Show-Status
    } else {
        Write-ModelLog '[set] 設定已寫入（**實例尚未重啟 ⇒ 仍在跑舊模型**）'
        Write-Host ""
        Write-Host "設定已寫入，但**實例仍在跑舊模型**。套用新模型請執行："
        Write-Host "    .\scripts\vc_ops.ps1 restart"
        Write-Host "（或直接用 -Restart 一併完成；重啟屬生產動作，需 Owner 授權）"
    }
}

switch ($Action) {
    'status'   { Show-Status }
    'set'      {
        if (-not $TtsModel -and -not $AsrModel) { throw 'set 需要 -TtsModel 或 -AsrModel 至少一個' }
        Invoke-Set $TtsModel $AsrModel $Restart.IsPresent
    }
    'rollback' {
        Write-ModelLog '[rollback] 退回 TTS=s2.1-pro（ASR 不動，除非另外給 -AsrModel）'
        Invoke-Set 's2.1-pro' $AsrModel $Restart.IsPresent
    }
    'help'     { Get-Help $PSCommandPath -Detailed }
}
