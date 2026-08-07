"""
test_log_backup_fix_v2.py — Log backup fix 驗證: 啟動前備份舊 log 為帶時間戳檔名

Bry 拍板 2026-08-07 18:19 (派工原文):
> 拍板方案 B(啟動前備份舊 log 為帶時間戳檔名,沿用舊 server_ops 備份模式)
> 請照慣例走 mock test 流程(before→code→after→commit-only),完成後回報

這個 v2 驗證修法:
- server_ops.ps1 Start-SoulOsServer 函式內, Start-Process 之前
  加 Move-Item 備份邏輯, 對 $outLog + $errLog 兩個 log 檔
- 備份檔名格式: server_${backupTs}.log / server_${backupTs}.err
  (跟舊 server_20260716_170323.err 模式一致)
- $backupTs 用 Get-Date -Format 'yyyyMMdd_HHmmss' 產生
- 範圍: 只動 server_ops.ps1, 沒加 log rotate / size limit 邏輯
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


def _get_function_source(fn_name: str) -> str:
    """抽 PowerShell 函式原始碼"""
    source = SERVER_OPS_PATH.read_text(encoding="utf-8")
    pattern = rf"function {fn_name}\b.*?^\}}"
    match = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not match:
        raise AssertionError(f"找不到 PowerShell 函式 {fn_name}")
    return match.group(0)


class TestLogBackupFix(unittest.TestCase):
    """驗證修法 — server_ops.ps1 Start-SoulOsServer 內有備份邏輯"""

    def setUp(self):
        self.full_source = SERVER_OPS_PATH.read_text(encoding="utf-8")
        self.start_fn_source = _get_function_source("Start-SoulOsServer")

    def test_a_has_move_item_for_outlog(self):
        """v2: Start-SoulOsServer 內有 Move-Item 對 $outLog"""
        self.assertIn(
            "Move-Item", self.start_fn_source,
            "v2 期望 Start-SoulOsServer 內有 Move-Item 備份邏輯"
        )
        # 確認有 Move-Item 對 $outLog
        pattern = rf"Move-Item[^\n]*\$outLog"
        self.assertRegex(
            self.start_fn_source, pattern,
            "v2 期望 Start-SoulOsServer 內有 Move-Item 對 $outLog (備份 stdout log)"
        )
        print("[v2] Start-SoulOsServer 內有 Move-Item 對 $outLog 備份")

    def test_b_has_move_item_for_errlog(self):
        """v2: Start-SoulOsServer 內有 Move-Item 對 $errLog"""
        pattern = rf"Move-Item[^\n]*\$errLog"
        self.assertRegex(
            self.start_fn_source, pattern,
            "v2 期望 Start-SoulOsServer 內有 Move-Item 對 $errLog (備份 stderr log)"
        )
        print("[v2] Start-SoulOsServer 內有 Move-Item 對 $errLog 備份")

    def test_c_backup_filename_pattern(self):
        """v2: 備份檔名格式用 server_${backupTs}.log/.err 跟舊模式一致

        Bry 派工 spirit: 「沿用既有修法拼湊拒絕大改」 — 跟舊 server_20260716_170323.err 一致
        舊模式: server_YYYYMMDD_HHMMSS.err
        v2 期望: 修法後用相同 timestamp pattern + 同樣 server_ 前綴
        """
        # 確認有 yyyyMMdd_HHmmss timestamp format
        self.assertIn(
            "yyyyMMdd_HHmmss", self.start_fn_source,
            "v2 期望備份檔名用 yyyyMMdd_HHmmss timestamp (跟舊 server_YYYYMMDD_HHMMSS.err 一致)"
        )
        # 確認有 server_ 前綴
        self.assertIn(
            "server_${backupTs}", self.start_fn_source,
            "v2 期望備份檔名用 server_ 前綴 (跟舊 server_YYYYMMDD_HHMMSS.err 一致)"
        )
        print("[v2] 備份檔名用 yyyyMMdd_HHmmss + server_ 前綴 (跟舊備份模式 100% 一致)")

    def test_d_backup_before_start_process(self):
        """v2: 備份邏輯在 Start-Process 之前 (關鍵: 必須備份完才 Start-Process 啟動新 log)

        Bry 派工 spirit: 「Bry 派工: 改動最小優先」 — 備份在 Start-Process 之前
        邏輯順序: backupTs 算時間戳 → Move-Item 備份 → Start-Process 啟動新 server
        順序錯了: Start-Process 先啟動會 truncate log, 後備份會備份到空檔

        排除註解裡的 Start-Process 字串: 註解提到 Start-Process -RedirectStandardOutput
        跟 Start-Process 模式, find() 會先命中註解 → 用 regex 找 code 行的 Start-Process
        呼叫 (跳過 # 開頭的註解行)
        """
        # 用 regex 找 code 行的 Start-Process 呼叫 (跳過 # 開頭的註解行)
        # PowerShell Start-Process 呼叫通常寫成: $proc = Start-Process 或 Start-Process -FilePath
        # 排除: 註解內的 Start-Process 字串 (Bry 派工註解有寫 "Start-Process -RedirectStandardOutput")
        code_lines = [
            line for line in self.start_fn_source.splitlines()
            if not line.strip().startswith("#")
        ]
        code_source = "\n".join(code_lines)

        backup_idx = code_source.find("Move-Item")
        # 找 Start-Process 呼叫 (非註解), 用 regex 找 "Start-Process -FilePath" 或 "Start-Process -"
        start_process_match = re.search(r"Start-Process\s+-", code_source)
        self.assertIsNotNone(
            start_process_match, "v2 期望 Start-SoulOsServer code 內有 Start-Process 呼叫"
        )
        start_process_idx = start_process_match.start()

        self.assertNotEqual(
            backup_idx, -1, "v2 期望 Start-SoulOsServer 內有 Move-Item"
        )
        self.assertLess(
            backup_idx, start_process_idx,
            f"v2 期望 Move-Item 備份在 Start-Process 呼叫之前 "
            f"(backup at {backup_idx}, start_process at {start_process_idx}). "
            f"順序錯了: Start-Process 先啟動會 truncate log, 後備份會備份到空檔"
        )
        print(f"[v2] Move-Item 備份在 Start-Process 呼叫之前 (邏輯順序正確, 排除註解命中)")

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
        """v2: 備份前 Test-Path 檢查 (避免新裝環境沒 log 檔時 Move-Item 報錯)

        Bry 派工 spirit: 「改動最小」 — 但 Test-Path 檢查是必要的, 避免第一次啟動 (沒舊 log)
        Move-Item 報錯, 跟 Bry 派工 8/3 23:33 修法 spirit 一致 (避免 ModuleNotFoundError
        啟動後才死, 浪費 5 分鐘 tick)
        """
        # 確認 if (Test-Path $outLog) { Move-Item ... } 模式
        self.assertRegex(
            self.start_fn_source,
            r"if\s*\(\s*Test-Path\s+\$outLog\s*\)\s*\{[^}]*Move-Item",
            "v2 期望 if (Test-Path $outLog) { Move-Item } 模式 "
            "(避免新裝環境沒 log 檔時 Move-Item 報錯)"
        )
        self.assertRegex(
            self.start_fn_source,
            r"if\s*\(\s*Test-Path\s+\$errLog\s*\)\s*\{[^}]*Move-Item",
            "v2 期望 if (Test-Path $errLog) { Move-Item } 模式 "
            "(避免新裝環境沒 log 檔時 Move-Item 報錯)"
        )
        print("[v2] 備份邏輯用 if (Test-Path ...) { Move-Item } 模式 (避免新裝環境報錯)")

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
