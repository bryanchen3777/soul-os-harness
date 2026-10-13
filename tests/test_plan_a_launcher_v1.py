"""
test_plan_a_launcher_v1.py — Plan A launcher 修法 v1 baseline (before 修法)

Bry 拍板 2026-08-03 23:33: 修 Plan A launcher 啟動路徑問題

根因 (查證事實):
- watchdog _watchdog.ps1 L275-285 呼叫 scripts/_start_plan_a.ps1
- 但 _start_plan_a.ps1 從來沒存在過 (git log 完全沒這個檔案歷史)
- 23:18:07 watchdog Plan A 拉 powershell.exe 啟動 _start_plan_a.ps1
  → powershell.exe 找不到檔案, 立刻退出, PID 短暫存在然後死
- 23:23:08 重複同樣失敗
- 我 23:23 手動啟動 PID 23512 成功 (因為我指定 hermes-agent venv python)
- 8/2 15:20 miku 教訓同類: 啟動路徑不一致 (系統 PATH python 找不到 uvicorn)

Bry 派工原話 (跟 server_ops.ps1 同類修法):
- Plan A 改成跟 server_ops.ps1 一樣, 明確指定 hermes-agent venv python
- 不依賴系統 PATH

Bry 派工原話: 修完後不需要立刻觸發驗證, 不要主動 kill 現在活著的 server 去測
Plan A. 找一個安全的方式驗證 (例如寫個獨立小腳本模擬 Plan A 邏輯但不影響現有 server)

這個 v1 驗證現狀 (before 修法):
- _start_plan_a.ps1 檔案不存在
- 模擬 Plan A 失敗: 嘗試啟動不存在的 .ps1, 確認 powershell.exe 立刻退出
- 不影響現有 server (PID 23512)

mock 範圍:
- v1: 模擬 Plan A 啟動 _start_plan_a.ps1 (不存在), 確認 powershell.exe 找不到檔案立刻退出
- v1 透過 subprocess.run 啟動 powershell.exe -File <不存在的檔案>, 確認 exit code != 0
- 不觸碰現有 server (PID 23512)
"""
import subprocess
import sys
import unittest
from pathlib import Path

# 確保 src 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")


class TestPlanALauncherV1(unittest.TestCase):
    """Plan A launcher v1 baseline (before 修法)"""

    def setUp(self):
        self.start_plan_a = REPO / "scripts" / "_start_plan_a.ps1"

    def test_baseline_start_plan_a_file_does_not_exist(self):
        """2026-10-12 更新: v1 「檔案必須不存在」的前提已過期, 改驗現行生產契約。

        證據: scripts/_start_plan_a.ps1 現存 (Bry 拍板 2026-08-15 23:33 修法後建立,
        檔頭 L3 明載拍板日期)。它是 watchdog Plan A 的正式生產基礎建設, 由
        tests/test_plan_a_launcher_v2.py 驗其行為; watchdog _watchdog.ps1 亦呼叫它。

        原本這條 v1 斷言「檔案不存在」, 修法落地後必然失敗 —— 屬過期 baseline,
        不是生產缺陷。改為驗證該檔案「存在且宣告預期結構」, 保留對生產基礎建設的
        迴歸防護 (若檔案被刪或結構走樣仍會紅)。
        """
        self.assertTrue(
            self.start_plan_a.exists(),
            f"生產基礎建設 scripts/_start_plan_a.ps1 應該存在 "
            f"(Bry 拍板 2026-08-15 23:33 修法後建立), 但不見於 {self.start_plan_a} "
            f"— watchdog Plan A 會退回 23:18/23:23 那種「立刻死」行為"
        )
        source = self.start_plan_a.read_text(encoding="utf-8")

        # 1. 明確指定 soul-os .venv python (Bry 派工 spirit「不依賴系統 PATH」)
        self.assertIn(
            ".venv\\Scripts\\python.exe", source,
            "_start_plan_a.ps1 必須明確指定 soul-os .venv python, 不依賴系統 PATH"
        )
        # 2. 啟動 run_server.py, 不靠 PATH 猜
        self.assertIn("run_server.py", source,
                      "_start_plan_a.ps1 必須啟動 run_server.py")
        # 3. Start-Process detach (讓 python 不跟著 powershell session 走)
        self.assertIn("Start-Process", source,
                      "_start_plan_a.ps1 必須用 Start-Process detach python")
        # 4. 寫 PID 給 watchdog / server_ops 共用
        self.assertIn("data\\server.pid", source,
                      "_start_plan_a.ps1 必須寫 data\\server.pid 給 watchdog 共用")
        # 5. 啟動失敗要 exit 1, 讓 watchdog 失敗計數不會誤判成功
        self.assertIn("exit 1", source,
                      "_start_plan_a.ps1 啟動失敗必須 exit 1 (watchdog 依賴失敗計數)")
        # 6. 成功路徑 exit 0
        self.assertIn("exit 0", source,
                      "_start_plan_a.ps1 成功啟動必須 exit 0")

        print(
            f"[現行契約] scripts/_start_plan_a.ps1 存在 ({len(source)} chars) "
            f"且宣告預期結構 (venv python / run_server.py / Start-Process / "
            f"server.pid / exit 0|1)"
        )

    def test_baseline_powershell_fails_on_missing_file(self):
        """v1 baseline: 模擬 Plan A 啟動不存在的 _start_plan_a.ps1, powershell.exe 找不到檔案立刻退出
        不影響現有 server (用 sandbox 測試, 不 kill PID 23512)
        """
        if self.start_plan_a.exists():
            self.skipTest(f"v1 baseline: _start_plan_a.ps1 已存在 ({self.start_plan_a}), 跳過")
        # 模擬 Plan A 啟動指令
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(self.start_plan_a)],
            capture_output=True, text=True, timeout=10,
        )
        # powershell.exe 找不到 .ps1 立刻退出, exit code != 0
        self.assertNotEqual(
            proc.returncode, 0,
            f"v1 baseline: Plan A 應該因為檔案不存在而失敗, "
            f"但 exit code = {proc.returncode}, stderr = {proc.stderr[:200]!r}"
        )
        # 確認錯誤訊息含 "找不到" 或 "cannot find" (跟 Bry 查證的 powershell 行為一致)
        error_msg = (proc.stderr + proc.stdout).lower()
        self.assertTrue(
            "找不到" in error_msg or "cannot find" in error_msg or "not found" in error_msg,
            f"v1 baseline: 錯誤訊息應該含「找不到」/「cannot find」, "
            f"實際: {error_msg[:200]!r}"
        )
        print(f"[v1 baseline] Plan A 啟動不存在的 _start_plan_a.ps1 失敗 (exit={proc.returncode})")

    def test_baseline_existing_server_not_affected(self):
        """v1 baseline: 驗證現有 server (PID 23512) 還活著, Plan A 失敗不影響它
        跟 Bry 派工原話「不主動 kill 現在活著的 server」對齊
        """
        import httpx
        try:
            r = httpx.get("http://127.0.0.1:8000/health", timeout=5)
            self.assertEqual(
                r.status_code, 200,
                f"現有 server 應該還活著, /health = {r.status_code}"
            )
            print(f"[v1 baseline] 現有 server PID 23512 還活著, /health = 200 (Plan A 模擬失敗沒影響)")
        except Exception as e:
            self.fail(
                f"v1 baseline: 現有 server 應該還活, 但 /health 失敗: {e}. "
                f"如果 server 真的死了, 這個 mock test 跟 Bry 派工原話假設衝突"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
