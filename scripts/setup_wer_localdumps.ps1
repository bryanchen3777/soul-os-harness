# =============================================================================
# CRASH-DUMP-1: Windows Error Reporting LocalDumps (Full Dump) for python.exe / pythonw.exe
# =============================================================================
# 目的：為主服務 (python.exe) 與語音伴侶 (pythonw.exe) 啟用本機 Full Dump 捕捉，
#       供原生崩潰（python311.dll / c0000005）後續離線堆疊分析使用。
#
# 執行方式（必須提權）：
#   以「系統管理員」開啟 PowerShell，然後：
#     powershell -ExecutionPolicy Bypass -File scripts\setup_wer_localdumps.ps1
#
# 設計約束（鐵律）：
#   1. 冪等：可重複執行，結果一致，不報錯。
#   2. 只寫 LocalDumps 之下的兩個「映像檔專屬子鍵」，絕不碰 LocalDumps 根鍵的任何值
#      （根鍵若不存在，僅因建立子鍵而被動產生為容器，不寫入任何 DumpFolder/DumpType/DumpCount）。
#   3. 非管理員一律 exit 1，絕不 fallback 到 HKCU（未提權即未完成）。
#   4. 寫入前讀舊值、寫入後立即回讀逐項比對，不一致 exit 1。
#   5. 本腳本不含任何 dump 內容、token 或機密。
#
# DumpFolder 使用 ExpandString (REG_EXPAND_SZ)：本路徑不含 %VAR% 環境變數，故展開前後等值；
#   選用 ExpandString 是為了保留未來以環境變數改路徑的彈性（例如 %LOCALAPPDATA%）。
#   註：PowerShell 讀取 REG_EXPAND_SZ 時會做環境變數展開，本路徑無變數故回讀值精確可比對。
# =============================================================================

$ErrorActionPreference = 'Stop'

# --- 組態（決策已定，勿隨意更動）-------------------------------------------
$DumpFolder      = 'C:\Users\bbfcc\.local\bin\soul-os-harness\data\crash_dumps'
$LocalDumpsRoot  = 'HKLM:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'
$TargetImages    = @('python.exe', 'pythonw.exe')
$DumpTypeValue   = 2   # 2 = Full Dump（1 = MiniDump normal）
$DumpCountValue  = 5   # 每個映像檔最多保留 5 份 dump
$ExpectedNames   = @('DumpType', 'DumpCount', 'DumpFolder')

# --- 1. 管理員權限自檢（絕不 fallback 到 HKCU）-------------------------------
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal($currentIdentity)
$isAdmin = $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin) {
    Write-Host "[CRASH-DUMP-1] 錯誤：本腳本必須以系統管理員身分執行（需要寫入 HKLM）。"
    Write-Host "[CRASH-DUMP-1] 目前使用者：$($currentIdentity.Name)（非管理員）。"
    Write-Host "[CRASH-DUMP-1] 請以系統管理員身分開啟 PowerShell，再執行："
    Write-Host "                powershell -ExecutionPolicy Bypass -File scripts\setup_wer_localdumps.ps1"
    Write-Host "[CRASH-DUMP-1] 本腳本不會、也不允許 fallback 到 HKCU；未提權即為未完成（PENDING ELEVATION）。"
    exit 1
}

Write-Host "[CRASH-DUMP-1] 管理員權限自檢通過：$($currentIdentity.Name)"
Write-Host "[CRASH-DUMP-1] DumpFolder  = $DumpFolder"
Write-Host "[CRASH-DUMP-1] LocalDumps 根鍵 = $LocalDumpsRoot（僅讀取，不寫入任何值）"
Write-Host ""

# --- 2. DumpFolder 目錄確保存在（冪等）--------------------------------------
if (-not (Test-Path -LiteralPath $DumpFolder)) {
    New-Item -ItemType Directory -Path $DumpFolder -Force | Out-Null
    Write-Host "[CRASH-DUMP-1] DumpFolder 目錄不存在 -> 已建立：$DumpFolder"
} else {
    Write-Host "[CRASH-DUMP-1] DumpFolder 目錄已存在：$DumpFolder"
}
Write-Host ""

# --- 3. 舊值讀取輔助 ---------------------------------------------------------
function Show-OldValues {
    param([string]$KeyPath)

    if (-not (Test-Path -Path $KeyPath)) {
        Write-Host "  [舊值] 鍵不存在：$KeyPath"
        return
    }
    $old = Get-ItemProperty -Path $KeyPath
    foreach ($name in $ExpectedNames) {
        $value = $old.$name
        if ($null -eq $value) {
            Write-Host "  [舊值] $name = <不存在>"
        } else {
            Write-Host "  [舊值] $name = $value"
        }
    }
}

# --- 4. 主流程：逐一處理兩個映像檔子鍵 --------------------------------------
# 先讀並印出 LocalDumps 根鍵狀態（唯讀，僅供紀錄）
Write-Host "[CRASH-DUMP-1] LocalDumps 根鍵狀態（唯讀）："
if (Test-Path -Path $LocalDumpsRoot) {
    Write-Host "  根鍵存在（本腳本不對根鍵寫入任何值）"
} else {
    Write-Host "  鍵不存在（將因建立子鍵而被動產生，不寫入根鍵任何值）"
}
Write-Host ""

$allOk = $true

foreach ($image in $TargetImages) {
    $keyPath = Join-Path $LocalDumpsRoot $image
    Write-Host "[CRASH-DUMP-1] ===== $image ====="
    Write-Host "[CRASH-DUMP-1] 目標子鍵：$keyPath"

    # 4a. 寫入前：讀並印出舊值
    Show-OldValues -KeyPath $keyPath

    # 4b. 建立子鍵（冪等）並寫入三項數值（Force = 覆寫既有值，可重複執行）
    if (-not (Test-Path -Path $keyPath)) {
        New-Item -Path $keyPath -Force | Out-Null
        Write-Host "  [寫入] 已建立子鍵"
    } else {
        Write-Host "  [寫入] 子鍵已存在（冪等覆寫）"
    }

    New-ItemProperty -Path $keyPath -Name 'DumpType'   -PropertyType DWord        -Value $DumpTypeValue  -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'DumpCount'  -PropertyType DWord        -Value $DumpCountValue -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'DumpFolder' -PropertyType ExpandString -Value $DumpFolder     -Force | Out-Null

    # 4c. 寫入後：立即回讀並逐項比對
    $readBack = Get-ItemProperty -Path $keyPath
    $expected = @{
        DumpType   = $DumpTypeValue
        DumpCount  = $DumpCountValue
        DumpFolder = $DumpFolder
    }

    foreach ($name in $ExpectedNames) {
        $actual   = $readBack.$name
        $wanted   = $expected[$name]
        $actualText = if ($null -eq $actual) { '<不存在>' } else { [string]$actual }
        if ($actualText -eq [string]$wanted) {
            Write-Host "  [回讀] OK   $name = $actualText"
        } else {
            Write-Host "  [回讀] FAIL $name = $actualText（預期 $wanted）"
            $allOk = $false
        }
    }

    if (-not $allOk) {
        Write-Host "[CRASH-DUMP-1] 錯誤：$image 回讀比對不一致，中止（exit 1）。"
        exit 1
    }
    Write-Host ""
}

# --- 5. 成功摘要 -------------------------------------------------------------
Write-Host "[CRASH-DUMP-1] 成功：以下子鍵已寫入並通過回讀驗證（LocalDumps 根鍵 0 寫入）"
foreach ($image in $TargetImages) {
    Write-Host "  - $(Join-Path $LocalDumpsRoot $image)"
}
Write-Host "  共同設定：DumpType=$DumpTypeValue (Full Dump) / DumpCount=$DumpCountValue / DumpFolder=$DumpFolder"
Write-Host "  注意：新的 dump 設定僅對「之後啟動」的 python.exe / pythonw.exe 生效。"
exit 0
