"""
test_server_ops_python_path_v2.py — server_ops.ps1 修法 v2 (after 修法)

Bry 拍板 2026-08-03 23:40: 修 server_ops.ps1 Start-SoulOsServer 跟 _start_plan_a.ps1 對稱
- 明確指定 python, 不依賴系統 PATH
  (v2 原始修法是 pin hermes-agent venv)
- 加 pre-check (python 存在 + uvicorn 可 import)
- 跟 8/2 15:20 miku 教訓 + 8/3 23:25:05 server_ops 重啟失敗同類問題

re-fix (Bry 拍板 2026-08-15 23:33, 兩階段歷史的第二階段):
- 根因: hermes-agent venv 缺 networkx/telegram/pip → 8/15 22:00 night slot 時
  server 掛掉 (ModuleNotFoundError: No module named 'networkx' → Application startup failed)
- 修法: server_ops.ps1 與 _start_plan_a.ps1 一律改釘 soul-os 自己的 .venv
  (有 networkx/telegram/icalendar 全部依賴)
- 現況契約 (見 scripts/server_ops.ps1:28 `$python = '...\\soul-os-harness\\.venv\\Scripts\\python.exe'`):
  啟動用 soul-os .venv, 不是 hermes venv。測試名沿用歷史命名 (hermes_agent_venv),
  但斷言的是「現行契約 = soul-os .venv」。

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
# 現行契約 (Bry 拍板 2026-08-15 23:33 re-fix): soul-os 自己的 .venv。
# 對應 scripts/server_ops.ps1:28 的 $python 綁定。
EXPECTED_PYTHON = r"C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe"
# 已被 8/15 re-fix 取代的舊路徑 (hermes venv 缺 networkx 導致崩) — 保留作回歸護欄。
LEGACY_HERMES_PYTHON = r"C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
EXPECTED_LOG_NAME = "server_ops.log"  # 只比對檔名, 避免正反斜線平台差異 (PowerShell 用 \\)
EXPECTED_START_PLAN_A = REPO / "scripts" / "_start_plan_a.ps1"


def _extract_bound_python(source: str) -> str:
    """抽出 PowerShell 裡真正綁定的 $python = '...' 值 (排除註解行)。"""
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    m = re.search(r"\$python\s*=\s*'([^']+)'", code)
    if not m:
        raise AssertionError("找不到 $python = '<path>' 綁定")
    return m.group(1)


class TestServerOpsPythonPathV2(unittest.TestCase):
    """server_ops.ps1 修法 v2 (after 修法) — 跟 _start_plan_a.ps1 對稱"""

    def setUp(self):
        self.server_ops = REPO / "scripts" / "server_ops.ps1"
        if not self.server_ops.exists():
            self.fail(f"server_ops.ps1 應該在 {self.server_ops}, 但不存在")
        self.content = self.server_ops.read_text(encoding="utf-8")

    def test_v2_uses_hermes_agent_venv_python(self):
        """v2 (名沿用歷史): 現行契約 = 明確指定 soul-os .venv python, 不依賴系統 PATH

        證據 scripts/server_ops.ps1:
        - L28  $python = '...\\soul-os-harness\\.venv\\Scripts\\python.exe'
        - L67  if (-not (Test-Path $python))  → 存在性 pre-check
        - L72  & $python -c "import uvicorn ..." → uvicorn pre-check 真的消費 $python
        - L109 Start-Process -FilePath $python → 啟動真的消費 $python
        Bry 拍板 2026-08-15 23:33 re-fix: 從 hermes venv 改回 soul-os .venv
        (hermes venv 缺 networkx → 8/15 22:00 night slot server 崩)。
        """
        bound = _extract_bound_python(self.content)
        self.assertEqual(
            bound, EXPECTED_PYTHON,
            f"v2: server_ops.ps1 的 $python 必須綁 soul-os .venv ({EXPECTED_PYTHON}), "
            f"實際={bound!r}"
        )
        # 迴歸護欄: hermes venv 路徑不得再回來 (那正是 8/15 崩潰根因)
        self.assertNotIn(
            LEGACY_HERMES_PYTHON, self.content,
            f"v2: server_ops.ps1 不得回頭用 hermes venv ({LEGACY_HERMES_PYTHON}) — "
            f"8/15 re-fix 前正是這條路徑缺 networkx 導致 server 崩"
        )
        # 確認 $python 真的被消費: Test-Path + uvicorn pre-check + Start-Process
        self.assertRegex(
            self.content, r"Test-Path\s+\$python",
            "v2: 應該有 Test-Path $python 存在性 pre-check"
        )
        self.assertRegex(
            self.content, r"&\s*\$python\s+-c\s+\"import uvicorn",
            "v2: uvicorn pre-check 應該真的用 $python 執行 (不是寫死路徑)"
        )
        match_actual = re.search(r"Start-Process\s+-FilePath\s+(\S+)", self.content)
        self.assertIsNotNone(
            match_actual, "v2: 應該有 Start-Process -FilePath <python_path>"
        )
        actual_file_path = match_actual.group(1)
        self.assertEqual(
            actual_file_path, "$python",
            f"v2: Start-Process 必須用 $python 變數 (明確路徑), 實際 file_path={actual_file_path!r}"
        )
        self.assertNotEqual(
            actual_file_path, "'python'",
            f"v2: Start-Process 不該用系統 PATH 'python' (uvicorn ModuleNotFoundError 同類問題), "
            f"實際 file_path={actual_file_path!r}"
        )
        print(f"[v2] python 綁定 = {bound} (Test-Path/uvicorn pre-check/Start-Process 三處都消費 $python)")

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

        歷史: 2026-08-03 修法當下只 pin 了 hermes venv; Bry 拍板 2026-08-15 23:33
        re-fix 之後, 兩邊都改釘 soul-os .venv (hermes venv 缺 networkx 導致崩)。
        現況兩邊 $python 綁定必須逐字相符, 且都等於 soul-os .venv。
        證據: scripts/server_ops.ps1:28, scripts/_start_plan_a.ps1:29
        """
        if not EXPECTED_START_PLAN_A.exists():
            self.skipTest("_start_plan_a.ps1 不存在, 跳過對稱檢查 (應在 plan_a 修法後存在)")
        plan_a = EXPECTED_START_PLAN_A.read_text(encoding="utf-8")
        server_ops_bound = _extract_bound_python(self.content)
        plan_a_bound = _extract_bound_python(plan_a)
        self.assertEqual(
            server_ops_bound, plan_a_bound,
            f"v2: 兩個檔案的 $python 必須相同, server_ops={server_ops_bound!r} vs plan_a={plan_a_bound!r}"
        )
        self.assertEqual(
            server_ops_bound, EXPECTED_PYTHON,
            f"v2: 兩邊都必須是 soul-os .venv ({EXPECTED_PYTHON}), 實際={server_ops_bound!r}"
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
        print(f"[v2] server_ops.ps1 跟 _start_plan_a.ps1 對稱 ($python = {server_ops_bound}, 兩邊都有 uvicorn pre-check)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
