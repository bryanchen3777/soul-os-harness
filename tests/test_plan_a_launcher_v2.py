"""
test_plan_a_launcher_v2.py — Plan A launcher 修法 v2 (after 修法)

Bry 拍板 2026-08-03 23:33: 修 Plan A launcher 啟動路徑問題

修法 (v2, 2026-08-03 23:40 定案):
- 建立 scripts/_start_plan_a.ps1 (原本不存在, git log 完全沒歷史)
- 跟 server_ops.ps1 一樣明確指定 python, 不依賴系統 PATH
  (v2 原始修法是 pin hermes-agent venv + uvicorn pre-check)

re-fix (Bry 拍板 2026-08-15 23:33, 兩階段歷史的第二階段):
- 根因: hermes-agent venv 缺 networkx/telegram/pip → 8/15 22:00 night slot 時
  server 掛掉 (ModuleNotFoundError: No module named 'networkx' → Application startup failed)
- 修法: _start_plan_a.ps1 與 server_ops.ps1 一律改釘 soul-os 自己的 .venv
  (有 networkx/telegram/icalendar 全部依賴)
- 現況契約 (見 scripts/_start_plan_a.ps1:29 `$python = '...\\soul-os-harness\\.venv\\Scripts\\python.exe'`):
  啟動用 soul-os .venv, 不是 hermes venv。測試名沿用歷史命名 (hermes_agent_venv),
  但斷言的是「現行契約 = soul-os .venv」。
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
# 現行契約 (Bry 拍板 2026-08-15 23:33 re-fix): soul-os 自己的 .venv。
# 對應 scripts/_start_plan_a.ps1:29 與 scripts/server_ops.ps1:28 的 $python 綁定。
EXPECTED_PYTHON = r"C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe"
# 已被 8/15 re-fix 取代的舊路徑 (hermes venv 缺 networkx 導致崩) — 保留作回歸護欄。
LEGACY_HERMES_PYTHON = r"C:\Users\bbfcc\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe"
EXPECTED_SERVER_OPS = REPO / "scripts" / "server_ops.ps1"


def _extract_bound_python(source: str) -> str:
    """抽出 PowerShell 裡真正綁定的 $python = '...' 值 (排除註解行)。"""
    code = "\n".join(
        line for line in source.splitlines() if not line.strip().startswith("#")
    )
    m = re.search(r"\$python\s*=\s*'([^']+)'", code)
    if not m:
        raise AssertionError("找不到 $python = '<path>' 綁定")
    return m.group(1)


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
        """v2 (名沿用歷史): 現行契約 = 明確指定 soul-os .venv python, 不依賴系統 PATH

        證據 scripts/_start_plan_a.ps1:
        - L29  $python = '...\\soul-os-harness\\.venv\\Scripts\\python.exe'
        - L58  if (-not (Test-Path $python))  → 存在性 pre-check
        - L65  & $python -c "import uvicorn ..." → uvicorn pre-check 真的消費 $python
        - L88  Start-Process -FilePath $python → 啟動真的消費 $python
        Bry 拍板 2026-08-15 23:33 re-fix: 從 hermes venv 改回 soul-os .venv
        (hermes venv 缺 networkx → 8/15 22:00 night slot server 崩)。
        """
        bound = _extract_bound_python(self.content)
        self.assertEqual(
            bound, EXPECTED_PYTHON,
            f"v2: _start_plan_a.ps1 的 $python 必須綁 soul-os .venv ({EXPECTED_PYTHON}), "
            f"實際={bound!r}"
        )
        # 迴歸護欄: hermes venv 路徑不得再回來 (那正是 8/15 崩潰根因)
        self.assertNotIn(
            LEGACY_HERMES_PYTHON, self.content,
            f"v2: _start_plan_a.ps1 不得回頭用 hermes venv ({LEGACY_HERMES_PYTHON}) — "
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
        """v2: 跟 server_ops.ps1 啟動意圖一致

        歷史: 修法當下 (2026-08-03) server_ops.ps1 還停在系統 PATH python, 只有
        _start_plan_a 單方面 pin hermes venv — 那時本測試只能單邊斷言。

        現況 (Bry 拍板 2026-08-15 23:33 re-fix): 兩個檔案都已改成 soul-os .venv
        (hermes venv 缺 networkx 導致 8/15 22:00 night slot 崩潰), 所以「對稱」
        終於成立。改為真正比較兩個檔案的 $python 綁定值必須相等, 且都等於
        soul-os .venv。若有人把任一邊改回 hermes venv / 系統 PATH, 這裡立刻紅。

        證據: scripts/_start_plan_a.ps1:29, scripts/server_ops.ps1:28
        """
        server_ops = EXPECTED_SERVER_OPS.read_text(encoding="utf-8")
        plan_a_bound = _extract_bound_python(self.content)
        server_ops_bound = _extract_bound_python(server_ops)

        self.assertEqual(
            plan_a_bound, server_ops_bound,
            f"v2: _start_plan_a.ps1 與 server_ops.ps1 的 $python 必須相同, "
            f"plan_a={plan_a_bound!r} vs server_ops={server_ops_bound!r}"
        )
        self.assertEqual(
            plan_a_bound, EXPECTED_PYTHON,
            f"v2: 兩邊都必須是 soul-os .venv ({EXPECTED_PYTHON}), 實際={plan_a_bound!r}"
        )
        self.assertNotIn(
            LEGACY_HERMES_PYTHON, self.content,
            f"v2: _start_plan_a.ps1 不得回頭用 hermes venv — 8/15 re-fix 前正是這條路徑缺 networkx 崩潰"
        )
        print(f"[v2] _start_plan_a.ps1 與 server_ops.ps1 的 $python 對稱 = {plan_a_bound}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
