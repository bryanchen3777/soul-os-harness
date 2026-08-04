"""
test_server_ops_python_path_v1.py — server_ops.ps1 修法 v1 baseline (before 修法)

Bry 拍板 2026-08-03 23:40 修法: 跟 _start_plan_a.ps1 一樣修 server_ops.ps1 Start-SoulOsServer
- 明確指定 hermes-agent venv python, 不依賴系統 PATH
- 加 pre-check (python 存在 + uvicorn 可 import)
- 跟 8/2 15:20 miku 教訓 + 8/3 23:25:05 server_ops 重啟失敗同類問題

Bry 派工原話: 「Bry 派工原話假設 server_ops.ps1 已經修過, 實際還沒修」
- 修法前 server_ops.ps1 L37 用 'Start-Process -FilePath python' (系統 PATH)
- 修法後改用 $python 變數 (hermes-agent venv) + pre-check

這個 v1 驗證現狀 (before 修法):
- server_ops.ps1 L37 仍用系統 PATH 'python' (沒明確指定 hermes-agent venv)
- 沒 pre-check (直接 Start-Process)
- 跟 _start_plan_a.ps1 不一致 (修法前 _start_plan_a.ps1 不存在, server_ops.ps1 仍用 PATH)

mock 範圍:
- 讀 server_ops.ps1 內容, 確認 L37 用 'python' 不是 hermes-agent venv
- 確認沒 pre-check (沒 'import uvicorn' 字串)
- 不實際啟動 server (避免跟現有 PID 23512 衝突)
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

REPO = Path("C:\\Users\\bbfcc\\.local\\bin\\soul-os-harness")
EXPECTED_PYTHON = r"C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"


class TestServerOpsPythonPathV1(unittest.TestCase):
    """server_ops.ps1 修法 v1 baseline (before 修法)"""

    def setUp(self):
        self.server_ops = REPO / "scripts" / "server_ops.ps1"
        if not self.server_ops.exists():
            self.fail(f"server_ops.ps1 應該在 {self.server_ops}, 但不存在")
        self.content = self.server_ops.read_text(encoding="utf-8")

    def test_baseline_uses_system_path_python(self):
        """v1 baseline: server_ops.ps1 用系統 PATH 'python' (沒明確指定 hermes-agent venv)
        跟 Bry 派工原話查證事實一致: 之前 8/3 23:25:05 server_ops 重啟失敗是因為這個
        """
        # 確認 Start-Process -FilePath 'python' 存在 (系統 PATH, 不是 hermes-agent venv)
        match = re.search(r"Start-Process\s+-FilePath\s+'python'", self.content)
        self.assertIsNotNone(
            match,
            f"v1 baseline: server_ops.ps1 應該用 'Start-Process -FilePath python' (系統 PATH), "
            f"這是修法前的現狀"
        )
        # 確認沒用 hermes-agent venv
        self.assertNotIn(
            EXPECTED_PYTHON, self.content,
            f"v1 baseline: server_ops.ps1 不應該包含 hermes-agent venv python ({EXPECTED_PYTHON}) - "
            f"這是修法前的現狀, 修法後才會有"
        )
        print(f"[v1 baseline] server_ops.ps1 用系統 PATH 'python' (修法前的現狀)")

    def test_baseline_no_uvicorn_pre_check(self):
        """v1 baseline: server_ops.ps1 沒 uvicorn pre-check
        修法後才有 pre-check (跟 _start_plan_a 一致)
        """
        self.assertNotIn(
            "import uvicorn", self.content,
            f"v1 baseline: server_ops.ps1 不應該有 'import uvicorn' pre-check - "
            f"修法前的現狀沒 pre-check, 啟動後才會死於 ModuleNotFoundError"
        )
        print(f"[v1 baseline] server_ops.ps1 沒 uvicorn pre-check (跟 8/3 23:25:05 失敗模式一致)")

    def test_baseline_existing_server_not_affected(self):
        """v1 baseline: 確認現有 server PID 23512 還活, mock test 不影響它
        Bry 派工原話: 「mock test 驗證 (不主動 kill 現有 server 去測)」
        """
        import httpx
        try:
            r = httpx.get("http://127.0.0.1:8000/health", timeout=5)
            self.assertEqual(
                r.status_code, 200,
                f"現有 server 應該還活, /health = {r.status_code}"
            )
            print(f"[v1 baseline] 現有 server PID 23512 還活, /health = 200 (mock test 沒影響)")
        except Exception as e:
            self.fail(
                f"v1 baseline: 現有 server 應該還活, 但 /health 失敗: {e}"
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
