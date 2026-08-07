"""
test_log_backup_baseline_v1.py — Log backup baseline: 修法前 server_ops.ps1 沒備份邏輯

Bry 拍板 2026-08-07 18:19 (派工原文):
> 拍板方案 B(啟動前備份舊 log 為帶時間戳檔名,沿用舊 server_ops 備份模式)
> 請照慣例走 mock test 流程(before→code→after→commit-only),完成後回報

Bry 派工原話 2026-08-07 18:16:
> server_ops 重啟時的 log truncate 問題,建議把 Start-Process -RedirectStandardOutput
> 從覆蓋模式(>)改成附加模式(>>),避免每次重啟就把過去的 scheduler 歷史 log 全部清空,
> 影響未來的事後驗證能力

Bry 拍板理由 2026-08-07 18:19:
> 方案 A 的「真 append」會讓 log 無限增長,還得另外補 rotate 邏輯,這正好違反
> 你們一路堅持的「不為假設中的未來灑過濾網」原則——你要解決的不是「log 要永久累積」,
> 而是「重啟時不要把歷史證據抹掉」,方案 B 精準對準這個問題,且直接沿用已經驗證過、
> 沒人抱怨的舊備份模式,改動量最小(4-6 行),風險最低

Bry 拒絕的方案 (要保留):
- 方案 A: Start-Process 改 cmd /c >> (PowerShell 沒原生 -Append, 必須 cmd 包裝)
  - 缺點: log 無限增長, 違反「不為假設中的未來灑過濾網」
  - 缺點: 改變 process 模型, 影響 signal handling / watchdog PID 判定
  - 缺點: 改動中等, 違反「改動最小優先」

這個 v1 驗證現狀 (before 修法):
- server_ops.ps1 Start-SoulOsServer 函式內沒 Move-Item 備份邏輯
- 沒使用 server_${ts} / server_$ts 備份檔名 pattern
- 沒備份 outLog ($outLog = data/server_nohup.log) 跟 errLog ($errLog = data/server_nohup.err) 兩個檔
- 修法前: 每次重啟 Start-Process -RedirectStandardOutput truncate 兩個 log 檔, 24h scheduler log 全丟
  (跟 8/7 17:31 cron 修法 12 一天回顧報告 ⚠️ 對齊)

Mock 範圍:
- 讀 server_ops.ps1 原始碼 (跟 M1.7 / M2.0 修法測試 source 層驗證模式一致)
- regex 找 Start-SoulOsServer 函式內的備份邏輯 marker
- v1 期望: 修法前沒備份邏輯 (4/4 pass)
- v1 修法後: 應該 fail (證明修法前狀態被打破)

Bry 派工原文 (要保留給未來 session 看):
- 「Bry 拍板方案 B 派工原話: 啟動前備份舊 log 為帶時間戳檔名, 沿用舊 server_ops 備份模式」
- 「Bry 派工: 沿用既有修法拼湊拒絕大改」 — 跟舊 server_20260716_170323.err 備份模式一致
- 「Bry 派工: 不為假設中的未來灑過濾網」 — 不加 log rotate / size limit 邏輯
- 「Bry 派工: 改動最小優先」 — 只加 4-6 行 Move-Item
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
    """抽 PowerShell 函式原始碼, 用 regex 對應 function ... { ... } 區塊"""
    source = SERVER_OPS_PATH.read_text(encoding="utf-8")
    # PowerShell function syntax: function Name { ... }
    # 對應到 } 結束 (跟 M1.7 proxy.py function regex 模式一致)
    pattern = rf"function {fn_name}\b.*?^\}}"
    match = re.search(pattern, source, re.DOTALL | re.MULTILINE)
    if not match:
        raise AssertionError(f"找不到 PowerShell 函式 {fn_name}")
    return match.group(0)


class TestLogBackupBaseline(unittest.TestCase):
    """驗證現狀 (before 修法) — server_ops.ps1 Start-SoulOsServer 沒備份邏輯"""

    def setUp(self):
        self.full_source = SERVER_OPS_PATH.read_text(encoding="utf-8")
        self.start_fn_source = _get_function_source("Start-SoulOsServer")

    def test_a_no_move_item_backup_in_start(self):
        """v1: Start-SoulOsServer 內沒 Move-Item 備份邏輯

        Bry 拍板方案 B: 啟動前 Move-Item 備份舊 log
        修法前: 沒 Move-Item
        修法後: 有 Move-Item (v1 fail 證明修法生效)
        """
        self.assertNotIn(
            "Move-Item", self.start_fn_source,
            "v1 baseline 期望 Start-SoulOsServer 沒 Move-Item 備份邏輯 (修法前), "
            "但找到了 → 修法可能已套用或 v1 寫在修法後"
        )
        print("[v1 baseline] Start-SoulOsServer 沒 Move-Item 備份邏輯 (預期: 修法後才有)")

    def test_b_no_backup_marker_in_start(self):
        """v1: Start-SoulOsServer 內沒備份 marker 字串

        Bry 派工原話: 「備份舊 log 為帶時間戳檔名」
        v1 期望: 沒 server_$ts / server_${ts} / yyyyMMdd 備份檔名 pattern
        修法後: 應該有 (v1 fail)
        """
        for marker in [
            "server_$ts",
            "server_${ts}",
            "yyyyMMdd",
            "Backup",
            "備份",
        ]:
            self.assertNotIn(
                marker, self.start_fn_source,
                f"v1 baseline 期望 Start-SoulOsServer 沒備份 marker ({marker}), "
                f"但找到了 → 修法可能已套用或 v1 寫在修法後"
            )
        print("[v1 baseline] Start-SoulOsServer 沒備份 marker (server_$ts / yyyyMMdd / Backup)")

    def test_c_full_source_no_log_backup_logic(self):
        """v1: server_ops.ps1 整檔案內沒 log 備份邏輯

        修法前: 整檔案沒 Move-Item 對 outLog / errLog
        修法後: 整檔案有 Move-Item 對 $outLog + $errLog (Bry 派工 spirit 兩個 log 都要備份)
        """
        # 找 Move-Item 對 $outLog 或 $errLog 的呼叫
        for var in ["$outLog", "$errLog"]:
            # 整檔案範圍內找 "Move-Item" + 該變數
            pattern = rf"Move-Item[^\n]*{re.escape(var)}"
            match = re.search(pattern, self.full_source)
            self.assertIsNone(
                match,
                f"v1 baseline 期望 server_ops.ps1 沒 Move-Item 對 {var} 的備份邏輯 "
                f"(修法前 truncate 直接覆蓋), 但找到了: {match.group(0) if match else 'N/A'}"
            )
        print("[v1 baseline] server_ops.ps1 整檔案沒 Move-Item 對 $outLog / $errLog 備份邏輯")

    def test_d_legacy_backup_files_use_correct_pattern(self):
        """v1: 舊備份檔 server_2026XXXX_*.err 存在, 證明舊 server_ops 用這個 pattern

        這個 test 不驗證 server_ops.ps1 修法狀態, 而是驗證「既有備份 pattern 確實存在」
        Bry 派工 spirit「沿用既有修法拼湊拒絕大改」: 沿用舊 server_20260716_HHMMSS.err 模式
        修法後: 應該用相同 pattern (server_YYYYMMDD_HHMMSS.err)
        """
        # 找 data/logs 內的舊備份檔
        legacy_dir = Path("C:/Users/bbfcc/.local/bin/soul-os-harness/data/logs")
        if not legacy_dir.is_dir():
            self.skipTest("data/logs 不存在, 跳過 legacy 備份驗證")

        legacy_files = list(legacy_dir.glob("server_2026*.err"))
        self.assertGreater(
            len(legacy_files), 0,
            f"v1 baseline 期望 data/logs 內有舊 server_2026*.err 備份檔 (Bry 派工 spirit 沿用對象), "
            f"但找不到 — 舊 server_ops 模式不存在, Bry 派工 spirit 沿用對象缺失"
        )
        # 抽第一個檔案確認 pattern
        first = legacy_files[0]
        match = re.match(r"server_(\d{8})_(\d{6})\.err$", first.name)
        self.assertIsNotNone(
            match,
            f"v1 期望舊備份檔名格式 server_YYYYMMDD_HHMMSS.err (Bry 派工 spirit 沿用對象), "
            f"但 {first.name} 不符合"
        )
        print(f"[v1 baseline] data/logs 有 {len(legacy_files)} 個舊 server_YYYYMMDD_HHMMSS.err 備份檔 (Bry 派工 spirit 沿用對象確認)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
