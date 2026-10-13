"""
test_log_backup_fix_v2.py — Log backup fix 驗證: 啟動前備份舊 log 為帶時間戳檔名

Bry 拍板 2026-08-07 18:19 (派工原文):
> 拍板方案 B(啟動前備份舊 log 為帶時間戳檔名,沿用舊 server_ops 備份模式)
> 請照慣例走 mock test 流程(before→code→after→commit-only),完成後回報

這個 v2 驗證修法:
- CRASH-OBS-1 (Bry 2026-09-10) 後, 輪替邏輯已從 server_ops.ps1 抽到共用 helper
  scripts/_nohup_log_rotation.ps1 的 Backup-Rotate-NohupLogs
- server_ops.ps1 的 Start-SoulOsServer 在 Start-Process 之前呼叫該 helper
- helper 內用 Get-Date -Format 'yyyyMMdd_HHmmss' + Move-Item 把
  data/server_nohup.<ext> 改名保留為 data/logs/server_nohup.<ts>.<ext>
- 範圍: 只動 server_ops.ps1 / _nohup_log_rotation.ps1, 沒加 log rotate / size limit 邏輯
- 沒 import 新 module, 沒改既有 Start-Process / Stop-SoulOsServer 邏輯

Mock 範圍:
- 讀 server_ops.ps1 原始碼, regex 找 Start-SoulOsServer 函式內備份邏輯
- 跟 M1.7 / M2.0 修法測試 source 層驗證模式一致
- v2 期望: 修法後 9/9 pass (備份邏輯 + 派工精神守住)

Bry 派工原文 (要保留給未來 session 看):
- 「Bry 拍板方案 B 派工原話: 啟動前備份舊 log 為帶時間戳檔名, 沿用舊 server_ops 備份模式」
- 「Bry 派工理由: 方案 B 精準對準『重啟時不要把歷史證據抹掉』這個問題, 且直接沿用已經驗證過、
  沒人抱怨的舊備份模式, 改動量最小(4-6 行), 風險最低」
- 「Bry 派工: 沿用既有修法拼湊拒絕大改」 — 跟舊 server_20260716_170323.err 備份模式一致
- 「Bry 派工: 不為假設中的未來灑過濾網」 — 不加 log rotate / size limit 邏輯
- 「Bry 派工: 改動最小優先」 — 只加 4 行邏輯 (backupDir, backupTs, if outLog { Move-Item }, if errLog { Move-Item })

Bry 拒絕的方案 (要保留):
- 方案 A: Start-Process 改 cmd /c >> (PowerShell 沒原生 -Append, 必須 cmd 包裝)
  - 缺點: log 無限增長, 違反「不為假設中的未來灑過濾網」
  - 缺點: 改變 process 模型, 影響 signal handling / watchdog PID 判定
  - 缺點: 改動中等, 違反「改動最小優先」
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


SERVER_OPS_PATH = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/server_ops.ps1"
)

# CRASH-OBS-1 (2026-09-10): 輪替邏輯從 server_ops.ps1 抽到共用 helper
ROTATION_PATH = Path(
    "C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/_nohup_log_rotation.ps1"
)


def _get_function_source(fn_name: str) -> str:
    """抽 PowerShell 函式原始碼"""
    source = SERVER_OPS_PATH.read_text(encoding="utf-8")
    pattern = rf"function {fn_name}\b.*?^\}}"
    match = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not match:
        raise AssertionError(f"找不到 PowerShell 函式 {fn_name}")
    return match.group(0)


def _get_rotation_source() -> str:
    """CRASH-OBS-1: 讀共用輪替 helper (server_ops.ps1 dot-source 這一支)"""
    if not ROTATION_PATH.exists():
        raise AssertionError(f"找不到共用輪替 helper {ROTATION_PATH}")
    return ROTATION_PATH.read_text(encoding="utf-8")


def _rotation_exts(rotation_source: str) -> list:
    """抽出輪替涵蓋的 nohup log 副檔名清單 (helper L33 foreach ($ext in @(...)))"""
    m = re.search(r"foreach\s*\(\s*\$ext\s+in\s+@\(([^)]*)\)\s*\)", rotation_source)
    if not m:
        raise AssertionError("找不到輪替 helper 的 $ext 清單")
    return re.findall(r"'([^']+)'", m.group(1))


class TestLogBackupFix(unittest.TestCase):
    """驗證修法 — server_ops.ps1 Start-SoulOsServer 內有備份邏輯"""

    def setUp(self):
        self.full_source = SERVER_OPS_PATH.read_text(encoding="utf-8")
        self.start_fn_source = _get_function_source("Start-SoulOsServer")
        # CRASH-OBS-1: 輪替 (Move-Item / timestamp) 已移到共用 helper
        self.rotation_source = _get_rotation_source()

    def test_a_has_move_item_for_outlog(self):
        """v2 (CRASH-OBS-1 後): stdout log ($outLog = data\\server_nohup.log) 由共用輪替 helper 用 Move-Item 保留

        現行呼叫鏈 (Bry 拍板 CRASH-OBS-1 2026-09-10):
        - server_ops.ps1:22       $outLog = Join-Path $root 'data\\server_nohup.log'
        - server_ops.ps1:111      Start-SoulOsServer 內 -RedirectStandardOutput $outLog (truncate 模式)
        - server_ops.ps1:103-104  Start-SoulOsServer 內呼叫 Backup-Rotate-NohupLogs -Root $root ...
        - _nohup_log_rotation.ps1:33-37  helper 內 Move-Item data/server_nohup.log -> data/logs/server_nohup.<ts>.log

        為什麼斷言「呼叫鏈」而不只斷言「helper 裡有 Move-Item」:
        Start-Process -RedirectStandardOutput 是 truncate, 每次重啟清掉歷史 nohup log
        (8/7 17:31 cron 修法 12 一回顧報告 ⚠️ 的根因)。若有人只留 helper 的 Move-Item
        卻拿掉 server_ops 的輪替呼叫, 崩潰證據仍會被下次重啟清空 — 所以這裡要求
        server_ops 真的呼叫 helper。
        """
        # (1) server_ops 把 stdout 綁到 data\server_nohup.log
        self.assertRegex(
            self.full_source, r"\$outLog\s*=\s*Join-Path\s+\$root\s+'data\\server_nohup\.log'",
            "v2 期望 server_ops.ps1 的 $outLog 綁到 data\\server_nohup.log"
        )
        # (2) Start-SoulOsServer 真的把 stdout 重導到 $outLog
        self.assertRegex(
            self.start_fn_source, r"-RedirectStandardOutput\s+\$outLog",
            "v2 期望 Start-SoulOsServer 用 -RedirectStandardOutput $outLog (會被 truncate 的目標)"
        )
        # (3) Start-SoulOsServer 真的呼叫共用輪替 helper (不只是 helper 內有 Move-Item)
        self.assertRegex(
            self.start_fn_source,
            r"Backup-Rotate-NohupLogs\s+-Root\s+\$root",
            "v2 期望 Start-SoulOsServer 呼叫 Backup-Rotate-NohupLogs (CRASH-OBS-1 輪替呼叫鏈)"
        )
        # (4) helper 涵蓋 'log' 這個 ext (= stdout) 且真的用 Move-Item 改名保留
        exts = _rotation_exts(self.rotation_source)
        self.assertIn("log", exts, f"v2 期望輪替涵蓋 'log' (stdout) ext, 實際={exts}")
        self.assertRegex(
            self.rotation_source,
            r"Move-Item\s+-Path\s+\$src\s+-Destination\s+\$dst",
            "v2 期望輪替 helper 用 Move-Item 改名保留舊 log"
        )
        print("[v2] stdout log ($outLog=server_nohup.log) 由 helper Move-Item 輪替保留 (CRASH-OBS-1)")

    def test_b_has_move_item_for_errlog(self):
        """v2 (CRASH-OBS-1 後): stderr log ($errLog = data\\server_nohup.err) 由共用輪替 helper 用 Move-Item 保留

        證據: server_ops.ps1:23 ($errLog 綁 server_nohup.err), server_ops.ps1:112
        (-RedirectStandardError $errLog), server_ops.ps1:103-104 (輪替呼叫),
        _nohup_log_rotation.ps1:33-37 (helper 輪替 'err' ext)。
        """
        # (1) server_ops 把 stderr 綁到 data\server_nohup.err
        self.assertRegex(
            self.full_source, r"\$errLog\s*=\s*Join-Path\s+\$root\s+'data\\server_nohup\.err'",
            "v2 期望 server_ops.ps1 的 $errLog 綁到 data\\server_nohup.err"
        )
        # (2) Start-SoulOsServer 真的把 stderr 重導到 $errLog
        self.assertRegex(
            self.start_fn_source, r"-RedirectStandardError\s+\$errLog",
            "v2 期望 Start-SoulOsServer 用 -RedirectStandardError $errLog (會被 truncate 的目標)"
        )
        # (3) Start-SoulOsServer 真的呼叫共用輪替 helper
        self.assertRegex(
            self.start_fn_source,
            r"Backup-Rotate-NohupLogs\s+-Root\s+\$root",
            "v2 期望 Start-SoulOsServer 呼叫 Backup-Rotate-NohupLogs (CRASH-OBS-1 輪替呼叫鏈)"
        )
        # (4) helper 涵蓋 'err' 這個 ext (= stderr) 且用 Move-Item 改名保留
        exts = _rotation_exts(self.rotation_source)
        self.assertIn("err", exts, f"v2 期望輪替涵蓋 'err' (stderr) ext, 實際={exts}")
        self.assertRegex(
            self.rotation_source,
            r"Move-Item\s+-Path\s+\$src\s+-Destination\s+\$dst",
            "v2 期望輪替 helper 用 Move-Item 改名保留舊 log"
        )
        print("[v2] stderr log ($errLog=server_nohup.err) 由 helper Move-Item 輪替保留 (CRASH-OBS-1)")

    def test_c_backup_filename_pattern(self):
        """v2 (CRASH-OBS-1 後): 備份檔名統一為 server_nohup.<yyyyMMdd_HHmmss>.<ext>

        證據 scripts/_nohup_log_rotation.ps1:
        - L32  $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
        - L36  $dst = Join-Path $BackupDir "server_nohup.${ts}.${ext}"

        歷史: 修法當下 (2026-08-07) 備份檔名是 server_${backupTs}.log/.err (直接寫在
        server_ops.ps1 內)。CRASH-OBS-1 (2026-09-10) 抽出共用 helper 後命名統一為
        server_nohup.<ts>.<ext>; 舊的 server_<ts> 備份檔不受影響 (仍留在 data/logs/)。
        現行 helper 已無 server_${backupTs} 命名 — 這裡斷言新命名 + 舊命名不再出現。
        """
        # timestamp format 保留 (跟舊 server_YYYYMMDD_HHMMSS 一致的時間粒度)
        self.assertRegex(
            self.rotation_source, r"Get-Date\s+-Format\s+'yyyyMMdd_HHmmss'",
            "v2 期望輪替用 yyyyMMdd_HHmmss timestamp (跟舊 server_YYYYMMDD_HHMMSS 一致)"
        )
        # CRASH-OBS-1 統一的 server_nohup.<ts>.<ext> 命名
        self.assertRegex(
            self.rotation_source, r"server_nohup\.\$\{ts\}\.\$\{ext\}",
            "v2 期望備份檔名統一為 server_nohup.${ts}.${ext} (CRASH-OBS-1 命名統一)"
        )
        # 舊的 server_${backupTs} 命名已隨抽出移除, 現行 helper 不該再用
        self.assertNotIn(
            "server_${backupTs}", self.rotation_source,
            "v2 期望舊的 server_${backupTs} 命名已隨 CRASH-OBS-1 抽出移除"
        )
        print("[v2] 備份檔名 server_nohup.<yyyyMMdd_HHmmss>.<ext> (CRASH-OBS-1 統一命名)")

    def test_d_backup_before_start_process(self):
        """v2 (CRASH-OBS-1 後): 輪替呼叫必須在 Start-Process 之前

        證據 scripts/server_ops.ps1:
        - L104  Backup-Rotate-NohupLogs -Root $root -BackupDir ...   (輪替舊 log)
        - L109  $proc = Start-Process -FilePath $python ...          (啟動新 server)

        順序錯了: Start-Process 的 -RedirectStandard* 是 truncate, 先啟動會清掉
        data/server_nohup.{log,err}, 後輪替會輪到空檔 → 崩潰證據仍會遺失。

        這裡刻意比「輪替 helper 的實際呼叫 (Backup-Rotate-NohupLogs -Root)」而非
        Get-Command 探測行, 確保定位到的是真的執行點而不是存在性檢查。
        """
        # 排除註解行, 只看 code
        code_lines = [
            line for line in self.start_fn_source.splitlines()
            if not line.strip().startswith("#")
        ]
        code_source = "\n".join(code_lines)

        rotate_call = re.search(r"Backup-Rotate-NohupLogs\s+-Root\s+\$root", code_source)
        self.assertIsNotNone(
            rotate_call, "v2 期望 Start-SoulOsServer code 內有 Backup-Rotate-NohupLogs 輪替呼叫"
        )
        rotate_idx = rotate_call.start()

        start_process_match = re.search(r"Start-Process\s+-", code_source)
        self.assertIsNotNone(
            start_process_match, "v2 期望 Start-SoulOsServer code 內有 Start-Process 呼叫"
        )
        start_process_idx = start_process_match.start()

        self.assertLess(
            rotate_idx, start_process_idx,
            f"v2 期望輪替在 Start-Process 之前 (rotate at {rotate_idx}, start_process at {start_process_idx}). "
            f"順序錯了: Start-Process 先 truncate log, 後輪替會輪到空檔"
        )
        print(f"[v2] 輪替在 Start-Process 之前 (rotate={rotate_idx} < start={start_process_idx})")

    def test_e_no_log_rotate_logic(self):
        """v2 派工精神: 「不為假設中的未來灑過濾網」 — 不加 log rotate / size limit 邏輯

        Bry 拍板方案 B: 不需要 rotate 邏輯, 跟舊 server_ops 風格一致
        v2 驗證: Start-SoulOsServer 內沒 log rotate / size limit 邏輯 (排除註解內字串)
        """
        # 排除註解行, 只看 code
        code_lines = [
            line for line in self.start_fn_source.splitlines()
            if not line.strip().startswith("#")
        ]
        code_source = "\n".join(code_lines)
        for forbidden in [
            "log rotate",
            "logrotate",
            "max_size",
            "size_limit",
            "maxSize",
            "Rotate-Item",
            "log-rotate",
        ]:
            self.assertNotIn(
                forbidden, code_source,
                f"v2 派工精神: Start-SoulOsServer code 不應加 {forbidden} 邏輯 "
                f"(Bry 派工: 「不為假設中的未來灑過濾網」)"
            )
        # "rotate" 單字可能出現在變數名 (e.g. $rotation), 用 regex 找 code 行
        # 含 "rotate" 關鍵字的 PowerShell cmdlet (Get-Rotate, Set-Rotate, etc)
        rotate_cmdlet_pattern = r"\b(Get|Set|New|Invoke|Start|Stop)-?Rotate[A-Z]\w*"
        self.assertNotRegex(
            code_source, rotate_cmdlet_pattern,
            f"v2 派工精神: Start-SoulOsServer code 不應加 PowerShell rotate cmdlet "
            f"(Bry 派工: 「不為假設中的未來灑過濾網」)"
        )
        print("[v2] Start-SoulOsServer code 沒加 log rotate / size limit 邏輯 (Bry 派工 spirit 守住, 排除註解)")

    def test_f_test_path_check_before_move(self):
        """v2 (CRASH-OBS-1 後): 輪替 helper 內 Move-Item 前有 Test-Path 存在性守衛

        證據 scripts/_nohup_log_rotation.ps1:
        - L35  if ((Test-Path $src) -and ((Get-Item $src -ErrorAction SilentlyContinue).Length -gt 0)) {
        - L37      Move-Item -Path $src -Destination $dst -Force -ErrorAction Stop

        避免新裝環境 / 第一次啟動沒有舊 log 檔時 Move-Item 報錯。helper 整段包在
        try/catch (L28/L52), 但 Test-Path 守衛讓「檔案不存在」走正常路徑而非例外。
        這裡同時要求 server_ops 真的呼叫這個 helper (使守衛在實際啟動路徑上生效),
        而不是只有 helper 檔案裡擺一段沒人呼叫的防禦。
        """
        # Test-Path 守衛包住 Move-Item
        self.assertRegex(
            self.rotation_source,
            r"if\s*\(\s*\(Test-Path\s+\$src\)[^\n]*\)[\s\S]*?Move-Item",
            "v2 期望輪替 helper 內 if (Test-Path $src ...) { Move-Item } 守衛存在 "
            "(避免沒有舊 log 檔時 Move-Item 報錯)"
        )
        # 且 server_ops 真的呼叫這個 helper (守衛在實際路徑上)
        self.assertRegex(
            self.start_fn_source,
            r"Backup-Rotate-NohupLogs\s+-Root\s+\$root",
            "v2 期望 Start-SoulOsServer 呼叫輪替 helper (使 Test-Path 守衛生效)"
        )
        print("[v2] 輪替 helper 內 Move-Item 有 Test-Path 守衛 (且 server_ops 有呼叫)")

    def test_g_backup_dir_uses_logs_subdir(self):
        """v2: 備份目錄用 data/logs 跟舊備份檔位置一致

        舊 server_20260716_170323.err 在 data/logs/ 內
        v2 期望: 修法後備份到 data/logs/server_${backupTs}.log/.err
        """
        self.assertRegex(
            self.start_fn_source,
            r"Join-Path\s+\$root\s+'data\\logs'",
            "v2 期望備份目錄用 Join-Path $root 'data\\logs' (跟舊 server_20260716_*.err 位置一致)"
        )
        print("[v2] 備份目錄用 Join-Path $root 'data\\logs' (跟舊備份位置一致)")

    def test_h_no_other_functions_modified(self):
        """v2 派工精神: 「範圍限定」 — 只動 Start-SoulOsServer, 其他函式沒被改

        v2 驗證: Stop-SoulOsServer / Get-SoulOsStatus / Show-ServerTail 沒 Move-Item
        """
        for fn_name in ["Stop-SoulOsServer", "Get-SoulOsStatus", "Show-ServerTail"]:
            try:
                fn_source = _get_function_source(fn_name)
            except AssertionError:
                continue  # 找不到跳過
            self.assertNotIn(
                "Move-Item", fn_source,
                f"v2 派工精神: {fn_name} 不應有 Move-Item 邏輯 (Bry 派工 spirit 範圍限定, "
                f"只動 Start-SoulOsServer)"
            )
        print("[v2] 其他函式 (Stop/Get-Status/Show-Tail) 沒被改 (Bry 派工 spirit 範圍限定守住)")

    def test_i_ps_syntax_valid(self):
        """v2 E2E: server_ops.ps1 PowerShell 語法有效 (能用 PowerShell parser 解析)

        用 PowerShell 5.1 內建的 [System.Management.Automation.PSParser]::Tokenize
        驗證語法, 避免 Bry 之後 restart 報 syntax error
        """
        import subprocess
        # 用 PowerShell parse 驗證 (不執行, 只 parse)
        # 從 stdin 餵 script 內容
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$tokens = $null; $errors = $null; "
             "[System.Management.Automation.PSParser]::Tokenize("
             "(Get-Content 'C:/Users/bbfcc/.local/bin/soul-os-harness/scripts/server_ops.ps1' -Raw), "
             "[ref]$tokens, [ref]$errors) | Out-Null; "
             "if ($errors) { Write-Output 'PARSE_ERROR'; $errors | ForEach-Object { Write-Output $_.Message } } "
             "else { Write-Output 'PARSE_OK' }"
            ],
            capture_output=True, text=True, timeout=15
        )
        output = result.stdout.strip() + result.stderr.strip()
        self.assertIn(
            "PARSE_OK", output,
            f"v2 E2E 期望 server_ops.ps1 PowerShell 語法有效, 實際:\n{output}"
        )
        print(f"[v2 E2E] server_ops.ps1 PowerShell 語法有效 (Parser 通過)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
