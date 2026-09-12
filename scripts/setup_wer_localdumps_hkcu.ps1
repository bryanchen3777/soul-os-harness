# =============================================================================
# CRASH-DUMP-1b: Windows Error Reporting LocalDumps (Full Dump) — 使用者層 (HKCU)
# =============================================================================
# 目的：把使用者層 WER LocalDumps 的 DumpFolder 一律重導向到專案 data\crash_dumps、
#       DumpCount 收斂為 5，與提權腳本 scripts\setup_wer_localdumps.ps1 (HKLM) 構成
#       「雙軌一致性」：HKCU 與 HKLM 兩層設定等值 → WER 的讀取優先權 (HKCU vs HKLM)
#       不再產生任何行為分歧；主服務 (python.exe) 與語音伴侶 (pythonw.exe) 的
#       dump 落地點統一，且不再落到舊目錄 gov_1_temp\crash_dumps。
#
# 執行方式（免提權，直接以目前使用者身分執行即可）：
#   powershell -ExecutionPolicy Bypass -File scripts\setup_wer_localdumps_hkcu.ps1
#
# 設計約束（鐵律）：
#   1. 冪等：可重複執行，結果一致，不報錯。
#   2. 不需要管理員權限：HKCU 為當前使用者可寫，故本腳本刻意「不做」管理員自檢，
#      更不因非管理員而 exit。這是與 HKLM 腳本（必須提權、非管理員 exit 1）
#      的關鍵差異，兩者不可互相 fallback。
#   3. 只寫兩個「映像檔專屬子鍵」的三項數值 (DumpType / DumpCount / DumpFolder)，
#      絕不刪除同鍵下任何其他既有值（例如 OS 自帶值），亦不寫入 LocalDumps 根鍵的值。
#   4. 寫入前先印舊值（鍵不存在則明確標示「鍵不存在」），寫入後立即以
#      Get-ItemProperty 回讀逐項比對，不一致則 exit 1。
#   5. 本腳本不含任何 dump 內容、token 或機密。
#   6. 舊目錄 C:\Users\bbfcc\gov_1_temp\crash_dumps 與其既有 dump 檔一律不動、不刪。
#
# DumpFolder 使用 ExpandString (REG_EXPAND_SZ)：與 HKLM 腳本保持一致。
#   本路徑不含 %VAR% 環境變數，故展開前後等值；選用 ExpandString 是為了保留
#   未來以環境變數改路徑的彈性。
#   註：PowerShell 讀取 REG_EXPAND_SZ 時會做環境變數展開，本路徑無變數故回讀值精確可比對。
# =============================================================================

$ErrorActionPreference = 'Stop'

# --- 組態（決策已定，勿隨意更動）-------------------------------------------
$DumpFolder      = 'C:\Users\bbfcc\.local\bin\soul-os-harness\data\crash_dumps'
$LocalDumpsRoot  = 'HKCU:\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps'
$TargetImages    = @('python.exe', 'pythonw.exe')
$DumpTypeValue   = 2   # 2 = Full Dump（1 = MiniDump normal）
$DumpCountValue  = 5   # 每個映像檔最多保留 5 份 dump（由舊值 10 收斂，防磁碟耗盡）
$ExpectedNames   = @('DumpType', 'DumpCount', 'DumpFolder')

Write-Host "[CRASH-DUMP-1b-HKCU] 本腳本免提權（使用者層 HKCU，當前使用者可寫）。"
Write-Host "[CRASH-DUMP-1b-HKCU] DumpFolder  = $DumpFolder"
Write-Host "[CRASH-DUMP-1b-HKCU] LocalDumps  = $LocalDumpsRoot（僅對兩個映像檔子鍵寫值）"
Write-Host ""

# --- 1. DumpFolder 目錄確保存在（冪等）--------------------------------------
if (-not (Test-Path -LiteralPath $DumpFolder)) {
    New-Item -ItemType Directory -Path $DumpFolder -Force | Out-Null
    Write-Host "[CRASH-DUMP-1b-HKCU] DumpFolder 目錄不存在 -> 已建立：$DumpFolder"
} else {
    Write-Host "[CRASH-DUMP-1b-HKCU] DumpFolder 目錄已存在：$DumpFolder"
}
Write-Host ""

# --- 2. 輔助函式 -------------------------------------------------------------
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
    # 同鍵下的其他既有值（本腳本不得刪除）
    $other = @($old.PSObject.Properties |
        Where-Object { $_.Name -notlike 'PS*' -and $ExpectedNames -notcontains $_.Name } |
        ForEach-Object { $_.Name })
    if ($other.Count -eq 0) {
        Write-Host "  [舊值] 其他既有值：<無>"
    } else {
        Write-Host "  [舊值] 其他既有值：$($other -join ', ')"
    }
}

function Get-OtherValueNames {
    param([string]$KeyPath)

    if (-not (Test-Path -Path $KeyPath)) { return @() }
    $cur = Get-ItemProperty -Path $KeyPath
    return @($cur.PSObject.Properties |
        Where-Object { $_.Name -notlike 'PS*' -and $ExpectedNames -notcontains $_.Name } |
        ForEach-Object { $_.Name })
}

# --- 3. 主流程：逐一處理兩個映像檔子鍵 --------------------------------------
Write-Host "[CRASH-DUMP-1b-HKCU] LocalDumps 根鍵狀態（唯讀，不對根鍵寫入任何值）："
if (Test-Path -Path $LocalDumpsRoot) {
    Write-Host "  根鍵存在（本腳本不對根鍵寫入任何值）"
} else {
    Write-Host "  鍵不存在（將因建立子鍵而被動產生為容器，不寫入根鍵任何值）"
}
Write-Host ""

$allOk = $true

foreach ($image in $TargetImages) {
    $keyPath = Join-Path $LocalDumpsRoot $image
    Write-Host "[CRASH-DUMP-1b-HKCU] ===== $image ====="
    Write-Host "[CRASH-DUMP-1b-HKCU] 目標子鍵：$keyPath"

    # 3a. 寫入前：讀並印出舊值（含其他既有值清單）
    Show-OldValues -KeyPath $keyPath
    $otherBefore = @(Get-OtherValueNames -KeyPath $keyPath)

    # 3b. 建立子鍵（冪等）並寫入三項數值（Force = 覆寫既有值，可重複執行）
    if (-not (Test-Path -Path $keyPath)) {
        New-Item -Path $keyPath -Force | Out-Null
        Write-Host "  [寫入] 子鍵不存在 -> 已建立"
    } else {
        Write-Host "  [寫入] 子鍵已存在（冪等覆寫）"
    }

    New-ItemProperty -Path $keyPath -Name 'DumpType'   -PropertyType DWord        -Value $DumpTypeValue  -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'DumpCount'  -PropertyType DWord        -Value $DumpCountValue -Force | Out-Null
    New-ItemProperty -Path $keyPath -Name 'DumpFolder' -PropertyType ExpandString -Value $DumpFolder     -Force | Out-Null

    # 3c. 寫入後：立即回讀並逐項比對
    $readBack = Get-ItemProperty -Path $keyPath
    $expected = @{
        DumpType   = $DumpTypeValue
        DumpCount  = $DumpCountValue
        DumpFolder = $DumpFolder
    }

    foreach ($name in $ExpectedNames) {
        $actual     = $readBack.$name
        $wanted     = $expected[$name]
        $actualText = if ($null -eq $actual) { '<不存在>' } else { [string]$actual }
        if ($actualText -eq [string]$wanted) {
            Write-Host "  [回讀] OK   $name = $actualText"
        } else {
            Write-Host "  [回讀] FAIL $name = $actualText（預期 $wanted）"
            $allOk = $false
        }
    }

    # 3d. 確認其他既有值未被刪除
    $otherAfter = @(Get-OtherValueNames -KeyPath $keyPath)
    $lost = @($otherBefore | Where-Object { $otherAfter -notcontains $_ })
    if ($lost.Count -gt 0) {
        Write-Host "  [回讀] FAIL 既有值遺失：$($lost -join ', ')"
        $allOk = $false
    } else {
        if ($otherAfter.Count -eq 0) {
            Write-Host "  [回讀] OK   其他既有值：<無>（無值遺失）"
        } else {
            Write-Host "  [回讀] OK   其他既有值保留：$($otherAfter -join ', ')"
        }
    }

    if (-not $allOk) {
        Write-Host "[CRASH-DUMP-1b-HKCU] 錯誤：$image 回讀比對不一致，中止（exit 1）。"
        exit 1
    }
    Write-Host ""
}

# --- 4. 成功摘要 -------------------------------------------------------------
Write-Host "[CRASH-DUMP-1b-HKCU] 成功：以下子鍵已寫入並通過回讀驗證（LocalDumps 根鍵 0 值寫入）"
foreach ($image in $TargetImages) {
    Write-Host "  - $(Join-Path $LocalDumpsRoot $image)"
}
Write-Host "  共同設定：DumpType=$DumpTypeValue (Full Dump) / DumpCount=$DumpCountValue / DumpFolder=$DumpFolder"
Write-Host "  注意 1：新的 dump 設定僅對「之後啟動」的 python.exe / pythonw.exe 生效。"
Write-Host "  注意 2：舊目錄（gov_1_temp\crash_dumps）與其既有 dump 檔一律保留、未刪。"
Write-Host "  注意 3：HKLM 對應腳本為 scripts\setup_wer_localdumps.ps1（需提權，本腳本不涵蓋）。"
exit 0
