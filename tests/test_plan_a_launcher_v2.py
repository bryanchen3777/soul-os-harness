"""
test_plan_a_launcher_v2.py — Plan A launcher 修法 v2 (after 修法)

Bry 拍板 2026-08-03 23:33: 修 Plan A launcher 啟動路徑問題

修法:
- 建立 scripts/_start_plan_a.ps1 (原本不存在, git log 完全沒歷史)
- 跟 server_ops.ps1 一樣明確指定 hermes-agent venv python, 不依賴系統 PATH
- 加 pre-check: python 存在 + uvicorn 可 import (避免 ModuleNotFoundError)
- 寫啟動 log 到 data/logs/plan_a_launcher.log
- exit code 處理: 失敗 exit 1, 成功 exit 0 (讓 watchdog N 計數準確)

Bry 派工原話: 修完後不需要立刻觸發驗證, 不要主動 kill 現在活著的 server.
找安全方式驗證. 這個 v2 寫獨立小腳本驗證 Plan A 邏輯 (pre-check + 啟動指令),
不 kill 現有 server (PID 23512), 不實際啟動新 process.

這個 v2 驗證修法後:
- _start_plan_a.ps1 存在
- python 路徑 = hermes-agent venv (跟 server_ops.ps1 一致)
- pre-check 邏輯正確: uvicorn import OK
- 啟動指令結構: Start-Process 帶 hermes-agent venv python + run_server.py
- log 寫到正確位置
- 失敗 exit 1 (讓 watchdog N 計數準確)

mock 範圍:
- 讀 _start_plan_a.ps1 內容, 驗證 python 路徑 / 啟動指令 / pre-check 邏輯
- 不實際執行 _start_plan_a.ps1 (避免跟現有 server 衝突)
- 透過模擬 powershell 語法 parse 確認
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")
EXPECTED_PYTHON = r"C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
EXPECTED_SERVER_OPS = REPO / "scripts" / "server_ops.ps1"


class TestPlanALauncherV2(unittest.TestCase):
    """Plan A launcher v2 (after 修法) — 修法後邏輯驗證"""

    def setUp(self):
        self.start_plan_a = REPO / "scripts" / "_start_plan_a.ps1"
        if not self.start_plan_a.exists():
            self.fail(
                f"v2 after 修法: _start_plan_a.ps1 應該已建立於 {self.start_plan_a}, "
                f"但檔案不存在. Bry 派工單要求建立, 沒建立 = 修法失敗"
            )
        self.content = self.start_plan_a.read_text(encoding="utf-8")

    def test_v2_uses_hermes_agent_venv_python(self):
        """v2: python 路徑必須明確指定 hermes-agent venv, 不依賴系統 PATH
        跟 server_ops.ps1 Start-SoulOsServer L30-54 同類修法
        """
        self.assertIn(
            EXPECTED_PYTHON, self.content,
            f"v2: _start_plan_a.ps1 必須用 hermes-agent venv python ({EXPECTED_PYTHON}), "
            f"不依賴系統 PATH. 實際內容前 1000 chars: {self.content[:1000]!r}"
        )
        # 確認 Start-Process 用 $python 變數 (明確路徑) 不是 'python' (系統 PATH 字串)
        # 用 regex 找真正的 Start-Process 呼叫, 不是註解裡的字
        match_actual = re.search(r"Start-Process\s+-FilePath\s+(\S+)", self.content)
        self.assertIsNotNone(
            match_actual,
            f"v2: 應該有 Start-Process -FilePath <python_path>"
        )
        actual_file_path = match_actual.group(1)
        self.assertNotEqual(
            actual_file_path, "'python'",
            f"v2: Start-Process 不該用系統 PATH 'python' (uvicorn ModuleNotFoundError 同類問題), "
            f"實際 file_path={actual_file_path!r}"
        )
        print(f"[v2] python 路徑 = {EXPECTED_PYTHON} (跟 server_ops.ps1 意圖一致)")

    def test_v2_has_pre_check_uvicorn_import(self):
        """v2: pre-check 必須驗證 uvicorn 可 import, 避免 ModuleNotFoundError 啟動後才死"""
        self.assertIn(
            "import uvicorn", self.content,
            f"v2: _start_plan_a.ps1 必須有 uvicorn pre-check, 避免啟動後才死"
        )
        # 用 regex 找真正的 Start-Process 呼叫 (Start-Process -FilePath ...), 避免匹配到註解
        match = re.search(r"Start-Process\s+-FilePath", self.content)
        self.assertIsNotNone(
            match, "v2: 應該有 Start-Process -FilePath 呼叫"
        )
        start_pos = match.start()
        uvicorn_pos = self.content.find("import uvicorn")
        self.assertNotEqual(uvicorn_pos, -1, "v2: 應該有 uvicorn pre-check")
        self.assertLess(
            uvicorn_pos, start_pos,
            f"v2: uvicorn pre-check 應該在 Start-Process 之前, "
            f"uvicorn pos={uvicorn_pos}, Start-Process pos={start_pos}"
        )
        print(f"[v2] uvicorn pre-check 在 Start-Process 之前 (uvicorn pos={uvicorn_pos} < {start_pos})")

    def test_v2_start_process_correct_args(self):
        """v2: Start-Process 帶正確參數 (hermes-agent venv python + run_server.py + 工作目錄)"""
        match = re.search(
            r"Start-Process\s+-FilePath\s+\$python",
            self.content,
        )
        self.assertIsNotNone(
            match,
            f"v2: Start-Process 應該用 $python 變數 (明確路徑), 不該用 'python' (系統 PATH)"
        )
        # 確認帶 run_server.py
        self.assertIn(
            "run_server.py", self.content,
            f"v2: 應該啟動 run_server.py"
        )
        # 確認帶 WorkingDirectory
        self.assertIn(
            "WorkingDirectory", self.content,
            f"v2: 應該帶 WorkingDirectory 確保工作目錄正確"
        )
        print(f"[v2] Start-Process 帶 $python + run_server.py + WorkingDirectory (跟 server_ops.ps1 意圖一致)")

    def test_v2_logs_to_plan_a_launcher_log(self):
        """v2: 寫啟動 log 到 data/logs/plan_a_launcher.log (跟 watchdog.log 區隔, 方便 Bry 事後核對)"""
        self.assertIn(
            "plan_a_launcher.log", self.content,
            f"v2: 應該寫啟動 log 到 data/logs/plan_a_launcher.log"
        )
        print(f"[v2] 啟動 log 路徑 = data/logs/plan_a_launcher.log")

    def test_v2_exit_code_on_failure(self):
        """v2: 失敗 exit 1, 成功 exit 0 (讓 watchdog N 計數準確)"""
        self.assertIn(
            "exit 1", self.content,
            f"v2: 失敗應該 exit 1 (讓 watchdog N 計數準確, 不會誤判成功)"
        )
        self.assertIn(
            "exit 0", self.content,
            f"v2: 成功應該 exit 0 (跟 watchdog 0 = healthy 對齊)"
        )
        print(f"[v2] 失敗 exit 1 + 成功 exit 0 (讓 watchdog N 計數準確)")

    def test_v2_consistent_with_server_ops_intent(self):
        """v2: 跟 server_ops.ps1 啟動意圖一致 (都用 hermes-agent venv python)
        Bry 派工原話假設: server_ops.ps1 已用 hermes-agent venv
        查證事實: server_ops.ps1 還是用系統 PATH python (L37: 'python' 不是 hermes-agent venv)
        Bry 派工原話可能誤判 server_ops 現狀, 但 _start_plan_a 仍應用 hermes-agent venv
        跟 server_ops.ps1 意圖一致 (Bry 派工原話拍板方向)

        這個測試不 assertIn server_ops 內容, 只確認 _start_plan_a 用 hermes-agent venv
        (跟 server_ops 意圖一致, 等待 Bry 確認是否要順便修 server_ops)
        """
        self.assertIn(
            EXPECTED_PYTHON, self.content,
            f"v2: _start_plan_a.ps1 應該用 hermes-agent venv python"
        )
        print(f"[v2] _start_plan_a.ps1 用 hermes-agent venv python (跟 server_ops.ps1 啟動意圖一致, "
              f"雖然 server_ops.ps1 本身還沒改, 等 Bry 確認是否要順便修)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
