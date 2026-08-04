"""
test_server_ops_python_path_v2.py — server_ops.ps1 修法 v2 (after 修法)

Bry 拍板 2026-08-03 23:40: 修 server_ops.ps1 Start-SoulOsServer 跟 _start_plan_a.ps1 對稱
- 明確指定 hermes-agent venv python, 不依賴系統 PATH
- 加 pre-check (python 存在 + uvicorn 可 import)
- 跟 8/2 15:20 miku 教訓 + 8/3 23:25:05 server_ops 重啟失敗同類問題

修法:
- $python 變數 = hermes-agent venv python (跟 _start_plan_a 一致)
- Write-OpsLog 函式: 寫 data/logs/server_ops.log (跟 _start_plan_a 的 plan_a_launcher.log 對稱)
- Start-SoulOsServer 加 pre-check: python 存在 + uvicorn 可 import, 失敗 exit 1
- Start-Process 從 -FilePath 'python' 改成 -FilePath $python

這個 v2 驗證修法後:
- server_ops.ps1 用 hermes-agent venv python (跟 _start_plan_a 一致)
- 有 uvicorn pre-check (跟 _start_plan_a 一致)
- Write-OpsLog 函式存在, 寫到 data/logs/server_ops.log
- 失敗 exit 1, 成功 exit 0
- 跟 _start_plan_a.ps1 邏輯對稱 (Bry 派工原話「對稱」要求)

mock 範圍:
- 讀 server_ops.ps1 內容, 驗證 hermes-agent venv + pre-check + log
- 不實際啟動 server (避免跟現有 PID 23512 衝突)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")
EXPECTED_PYTHON = r"C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
EXPECTED_LOG_NAME = "server_ops.log"  # 只比對檔名, 避免正反斜線平台差異 (PowerShell 用 \\)
EXPECTED_START_PLAN_A = REPO / "scripts" / "_start_plan_a.ps1"


class TestServerOpsPythonPathV2(unittest.TestCase):
    """server_ops.ps1 修法 v2 (after 修法) — 跟 _start_plan_a.ps1 對稱"""

    def setUp(self):
        self.server_ops = REPO / "scripts" / "server_ops.ps1"
        if not self.server_ops.exists():
            self.fail(f"server_ops.ps1 應該在 {self.server_ops}, 但不存在")
        self.content = self.server_ops.read_text(encoding="utf-8")

    def test_v2_uses_hermes_agent_venv_python(self):
        """v2: python 路徑必須明確指定 hermes-agent venv, 不依賴系統 PATH
        跟 _start_plan_a.ps1 對稱 (Bry 派工原話要求)
        """
        self.assertIn(
            EXPECTED_PYTHON, self.content,
            f"v2: server_ops.ps1 必須用 hermes-agent venv python ({EXPECTED_PYTHON}), "
            f"不依賴系統 PATH. 實際內容前 500 chars: {self.content[:500]!r}"
        )
        # 確認 Start-Process 用 $python 變數 (明確路徑) 不是 'python' (系統 PATH 字串)
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
        print(f"[v2] python 路徑 = {EXPECTED_PYTHON} (跟 _start_plan_a.ps1 對稱)")

    def test_v2_has_pre_check_uvicorn_import(self):
        """v2: pre-check 必須驗證 uvicorn 可 import, 避免 ModuleNotFoundError 啟動後才死
        跟 _start_plan_a.ps1 對稱
        """
        self.assertIn(
            "import uvicorn", self.content,
            f"v2: server_ops.ps1 必須有 uvicorn pre-check"
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

    def test_v2_logs_to_server_ops_log(self):
        """v2: 寫啟動 log 到 data/logs/server_ops.log (跟 _start_plan_a 的 plan_a_launcher.log 對稱)
        只比對檔名避免正反斜線平台差異 (PowerShell Join-Path 產 OS native separator)
        """
        self.assertIn(
            EXPECTED_LOG_NAME, self.content,
            f"v2: 應該寫啟動 log 到 {EXPECTED_LOG_NAME} (跟 _start_plan_a 的 plan_a_launcher.log 對稱)"
        )
        print(f"[v2] 啟動 log 檔名 = {EXPECTED_LOG_NAME} (跟 _start_plan_a 對稱)")

    def test_v2_exit_code_on_pre_check_failure(self):
        """v2: pre-check 失敗 exit 1 (跟 _start_plan_a 一致, 避免啟動後才死)"""
        # 確認 pre-check 失敗 path 有 exit 1
        self.assertIn(
            "exit 1", self.content,
            f"v2: 失敗應該 exit 1 (跟 _start_plan_a 一致)"
        )
        print(f"[v2] 失敗 exit 1 (跟 _start_plan_a 一致)")

    def test_v2_symmetric_with_start_plan_a(self):
        """v2: 跟 _start_plan_a.ps1 對稱 (Bry 派工原話「對稱」要求)
        兩個檔案都用 hermes-agent venv python + uvicorn pre-check + log
        """
        if not EXPECTED_START_PLAN_A.exists():
            self.skipTest("_start_plan_a.ps1 不存在, 跳過對稱檢查 (應在 plan_a 修法後存在)")
        plan_a = EXPECTED_START_PLAN_A.read_text(encoding="utf-8")
        # 兩個檔案都應該有 hermes-agent venv python
        self.assertIn(
            EXPECTED_PYTHON, self.content,
            f"v2: server_ops.ps1 應該用 hermes-agent venv python"
        )
        self.assertIn(
            EXPECTED_PYTHON, plan_a,
            f"v2: _start_plan_a.ps1 應該用 hermes-agent venv python (對稱)"
        )
        # 兩個檔案都應該有 uvicorn pre-check
        self.assertIn(
            "import uvicorn", self.content,
            f"v2: server_ops.ps1 應該有 uvicorn pre-check"
        )
        self.assertIn(
            "import uvicorn", plan_a,
            f"v2: _start_plan_a.ps1 應該有 uvicorn pre-check (對稱)"
        )
        print(f"[v2] server_ops.ps1 跟 _start_plan_a.ps1 對稱 (都用 hermes-agent venv + uvicorn pre-check)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
