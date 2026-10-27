"""
test_server_ops_python_path_v1.py — server_ops.ps1 修法 v1 baseline (2026-10-12 校準為現況)

Bry 拍板 2026-08-03 23:40 修法: 跟 _start_plan_a.ps1 一樣修 server_ops.ps1 Start-SoulOsServer
- 明確指定 venv python, 不依賴系統 PATH
- 加 pre-check (python 存在 + uvicorn 可 import)
- 跟 8/2 15:20 miku 教訓 + 8/3 23:25:05 server_ops 重啟失敗同類問題

後續 2026-08-15 23:33 二次修: $python 由 hermes venv 改成 soul-os .venv
根因: hermes venv 缺 networkx/telegram → 8/15 22:00 night slot server 掛了

Bry 派工原話: 「 Bry 派工原話假設 server_ops.ps1 已經修過, 實際還沒修」
- 修法前 server_ops.ps1 L37 用 'Start-Process -FilePath python' (系統 PATH)
- 修法後改用 $python 變數 (venv) + pre-check

2026-10-12 校準: 本檔原為「修法前」baseline, 修法早已落地, 兩條斷言皆已過期。
不刪測試, 只把期望值校準成現況契約 (production 正確, 測試過期):
- test_baseline_uses_system_path_python: 明確 venv python + Start-Process 用 $python 變數
- test_baseline_no_uvicorn_pre_check: 有 uvicorn pre-check 且位置在 Start-Process 之前
mock 範圍:
- 讀 server_ops.ps1 內容, 不實際啟動 server (避免跟生產服務衝突)

🔺 2026-10-27（FEAT-LIFECYCLE-CLI-TYPE-NEUTRAL B 段）—— /health 探針改為 opt-in:
- 目標由環境變數 `SOUL_OS_LIVE_SERVICE_BASE_URL` 提供（**預設空字串**）
- 未設定時 `self.skipTest(...)` ⇒ 預設離線、不對任何服務發請求（不是失敗）
"""
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")
EXPECTED_PYTHON = str(REPO / ".venv" / "Scripts" / "python.exe")

#: 線上服務目標。**預設空字串** ⇒ 環境未設定時沒有目標、沒有能力。
#: 🔺 2026-10-27（FEAT-LIFECYCLE-CLI-TYPE-NEUTRAL B 段）：本檔原本硬寫生產服務
#: 位址且**沒有任何 opt-in 閘門** ⇒ 每次全庫回歸都直連生產服務。現改為由環境變數
#: 明確授權（預設空字串），未設定時以 `self.skipTest(...)` 跳過（不是失敗）。
LIVE_BASE = os.environ.get("SOUL_OS_LIVE_SERVICE_BASE_URL", "")


class TestServerOpsPythonPathV1(unittest.TestCase):
    """server_ops.ps1 修法 v1 baseline (before 修法)"""

    def setUp(self):
        self.server_ops = REPO / "scripts" / "server_ops.ps1"
        if not self.server_ops.exists():
            self.fail(f"server_ops.ps1 應該在 {self.server_ops}, 但不存在")
        self.content = self.server_ops.read_text(encoding="utf-8")

    def test_baseline_uses_system_path_python(self):
        """校準 (2026-10-12): server_ops.ps1 明確指定 venv python, 不依賴系統 PATH

        證據: server_ops.ps1 L28 $python = repo .venv python (Bry 2026-08-15 23:33 二次修:
        hermes venv 缺 networkx → 8/15 22:00 night slot server 掛掉), L109 Start-Process 用 $python。
        原 v1 期望 (修法前用系統 PATH 'python') 已被 Bry 2026-08-03 23:40 修法推翻。
        """
        self.assertIn(
            EXPECTED_PYTHON, self.content,
            f"校準: 應該明確指定 venv python ({EXPECTED_PYTHON}, server_ops.ps1 L28)"
        )
        # 真的用在 Start-Process 上 (server_ops.ps1 L109), 不是宣告完就擱著
        match = re.search(r"Start-Process\s+-FilePath\s+(\S+)", self.content)
        self.assertIsNotNone(match, "應該有 Start-Process -FilePath 呼叫")
        actual = match.group(1)
        self.assertEqual(actual, "$python", f"L109 應該用 $python 變數, 實際 = {actual!r}")
        self.assertNotEqual(actual, "'python'", "不該用系統 PATH 'python' (8/3 23:25:05 同類問題)")
        print(f"[v1 校準] server_ops.ps1 = venv python ({EXPECTED_PYTHON}), 非系統 PATH")

    def test_baseline_no_uvicorn_pre_check(self):
        """校準 (2026-10-12): server_ops.ps1 有 uvicorn pre-check, 且在 Start-Process 之前"""
        # 證據: server_ops.ps1 L67-81 (Test-Path $python + import uvicorn, 失敗 exit 1),
        # 早於 L109 Start-Process — Bry 2026-08-03 23:40 修法, 避免 8/3 23:25:05 啟動後才死。
        self.assertIn("import uvicorn", self.content, "校準: 應該有 uvicorn pre-check")
        match = re.search(r"Start-Process\s+-FilePath", self.content)
        self.assertIsNotNone(match, "應該有 Start-Process -FilePath 呼叫")
        self.assertLess(self.content.find("import uvicorn"), match.start(),
                        "pre-check (L67-81) 應該在 Start-Process (L109) 之前, 否則檢查沒意義")
        print("[v1 校準] uvicorn pre-check 在 Start-Process 之前 (server_ops.ps1 L67-81)")

    def test_baseline_existing_server_not_affected(self):
        """v1 baseline: 確認「**被明確授權的**目標」/health 可達, mock test 不影響它
        Bry 派工原話: 「mock test 驗證 (不主動 kill 現有 server 去測)」

        🔺 2026-10-27（FEAT-LIFECYCLE-CLI-TYPE-NEUTRAL B 段）：本檔原本硬寫生產服務
        位址、**沒有任何 opt-in 閘門** ⇒ 每次全庫回歸都直連生產服務。現改為：
          · 目標由 `SOUL_OS_LIVE_SERVICE_BASE_URL` 提供（**預設空字串**）
            ⇒ 環境未設定時沒有目標、沒有能力；
          · 沒有授權目標時 `self.skipTest(...)` ⇒ 預設離線、無從探測（**不是失敗**：
            沒有目標就沒有東西可探）。
        """
        if not LIVE_BASE:
            self.skipTest(
                "未設定 SOUL_OS_LIVE_SERVICE_BASE_URL ⇒ 沒有授權目標，無從探測"
                "（預設離線；這不是失敗）"
            )
        import httpx
        try:
            r = httpx.get(f"{LIVE_BASE}/health", timeout=5)
            self.assertEqual(
                r.status_code, 200,
                f"授權目標的 /health 應該可達, /health = {r.status_code}"
            )
            print(f"[v1 baseline] 授權目標 {LIVE_BASE} 的 /health = 200 "
                  f"(mock test 沒影響)")
        except Exception as e:
            self.fail(
                f"v1 baseline: 授權目標 {LIVE_BASE} 的 /health 失敗: {e}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
