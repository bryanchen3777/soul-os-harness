# -*- coding: utf-8 -*-
"""test_live_service_tests_are_opt_in.py — LIVE-DSH-OPT-IN 護欄

為什麼有這支
============
2026-10-07（SOUL-REM-01）實測發現：`pytest -q tests` 全庫回歸會對**線上 DSH
服務**發真實請求。肇因是 `tests/test_work_p1c1_routing.py::TestRealDshSmoke` 與
`tests/test_work_p1c2_integration.py::TestRealDshClosedLoop` 的 skip 條件只看
「dsh CLI + credential 是不是可用」——而那在 Bry 的生產機上**永遠成立**。

這直接牴觸 AGENTS.md 鐵律 #5「測試一律不得對 production 服務發真實請求」。
真正危險的不是那一個 bug，是它讓任何 agent 都可以合理地說
「我只是跑了 pytest 全庫，我沒有特別呼叫 production」。

所以這支護欄把規則變成可擋的東西：

  任何名稱帶有 real-service 訊號的測試，所在模組**必須**有明確的 opt-in
  環境變數閘門，且閘門預設為關閉。

執行:
  .venv\\Scripts\\python.exe -m pytest tests\\infra\\test_live_service_tests_are_opt_in.py -q
"""
import ast
import re
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent

# 名稱帶有這些訊號的測試，視為「會打真實服務」
REAL_SERVICE_NAME = re.compile(
    r"(RealDsh|real_dsh|RealService|real_service|LiveDsh|live_dsh|RealProd|real_prod)"
)

# 明確 opt-in 閘門的樣態：必須是「等於特定字串」的比較，不能只是 getenv 存在性
OPT_IN_GATE = re.compile(
    r"environ\.get\(\s*[\"']([A-Z0-9_]+)[\"']\s*\)\s*==\s*[\"']1[\"']"
)


def _modules():
    """要掃描的測試模組，**排除本檔案**。

    本檔案自己的函式名帶有 real_service / real_dsh 字樣（因為它討論的主題就是
    這些），若不排除，護欄會把自己判為違規者。這是自我指涉，處理方式是把
    護欄自身排除，而不是放寬比對規則 —— 放寬會讓規則可以被繞過。
    """
    here = Path(__file__).resolve()
    return [p for p in sorted(TESTS_DIR.rglob("test_*.py")) if p.resolve() != here]


def _real_service_nodes(path):
    """回傳 (lineno, name) 列表：此模組中所有看起來會打真實服務的 class/def。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):  # pragma: no cover - 防護欄自身不該因讀檔失敗紅
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if REAL_SERVICE_NAME.search(node.name):
                out.append((node.lineno, node.name))
    return out


def test_no_real_service_test_lacks_opt_in_gate():
    """任何 real-service 測試，其模組都必須有 env opt-in 閘門。"""
    offenders = []
    for path in _modules():
        nodes = _real_service_nodes(path)
        if not nodes:
            continue
        src = path.read_text(encoding="utf-8")
        gates = OPT_IN_GATE.findall(src)
        if not gates:
            offenders.append(
                f"{path.relative_to(TESTS_DIR.parent)}: "
                f"{[n for _, n in nodes]} 沒有任何 opt-in 閘門"
            )
    assert not offenders, (
        "以下測試看起來會打真實服務，但沒有明確 opt-in 閘門：\n  "
        + "\n  ".join(offenders)
        + "\n\n修法：加一個形如 "
        "`os.environ.get('<NAME>') == '1'` 的閘門，並且預設為關閉。"
    )


def test_live_dsh_opt_in_defaults_off():
    """兩個已知會打生產 DSH 的檔案：閘門存在，且必須預設關閉。"""
    for rel in ("test_work_p1c1_routing.py", "test_work_p1c2_integration.py"):
        path = TESTS_DIR / rel
        assert path.exists(), f"{rel} 不存在"
        src = path.read_text(encoding="utf-8")
        assert "SOULOS_ALLOW_LIVE_DSH_TESTS" in src, (
            f"{rel}: 缺少 SOULOS_ALLOW_LIVE_DSH_TESTS 閘門"
        )
        gate = OPT_IN_GATE.search(src)
        assert gate, f"{rel}: 閘門不是明確的 == '1' 比較"
        assert gate.group(1) == "SOULOS_ALLOW_LIVE_DSH_TESTS", gate.group(1)


def test_live_dsh_gate_is_not_merely_environment_availability():
    """防止把閘門改回「環境可用就跑」。

    原本的 bug 就是 `not (_DSH_AVAILABLE and _DSH_CREDENTIALS)`：
    在生產機上這兩者恆真，等於沒有閘門。這裡釘死 skipif 的條件必須引用
    opt-in 變數，不能只引用環境偵測結果。
    """
    for rel in ("test_work_p1c1_routing.py", "test_work_p1c2_integration.py"):
        src = (TESTS_DIR / rel).read_text(encoding="utf-8")
        m = re.search(r"needs_real_dsh\s*=\s*pytest\.mark\.skipif\(\s*(.*?),\s*reason=", src, re.S)
        assert m, f"{rel}: 找不到 needs_real_dsh 的 skipif 條件"
        condition = m.group(1)
        assert "_LIVE_DSH_OPT_IN" in condition, (
            f"{rel}: needs_real_dsh 的條件必須引用 _LIVE_DSH_OPT_IN，實得 {condition!r}"
        )
        assert "_DSH_AVAILABLE" not in condition, (
            f"{rel}: 閘門不可只靠環境可用性（那在生產機恆真）"
        )


def test_live_service_tests_are_skipped_without_opt_in():
    """未設 opt-in 時，這些測試確實被 skip。

    刻意**不用** subprocess 再跑一次 pytest：那會在本檔案新增一個
    ``subprocess.run`` 呼叫點，而 spawn guard 的 `SUBPROCESS_ALLOWLIST` 是用
    「檔案 + 行號」綁死的（2026-10-07 實測已因行號漂移紅过一次）。護欄不該
    自己製造同類脆弱點。

    執行期行為由人手工動確認過：
        .venv\\Scripts\\python.exe -m pytest
            tests\\test_work_p1c1_routing.py tests\\test_work_p1c2_integration.py -q -rs
        → 44 passed, 2 skipped（兩支 live DSH 皆以 opt-in 訊息跳過）
    """
    # 靜態層：三支測試已確保閘門存在、預設關閉、且不是靠環境可用性。
    # 這裡只確認閘門名稱一致，避免兩檔各跳各的。
    names = set()
    for rel in ("test_work_p1c1_routing.py", "test_work_p1c2_integration.py"):
        src = (TESTS_DIR / rel).read_text(encoding="utf-8")
        gate = OPT_IN_GATE.search(src)
        assert gate
        names.add(gate.group(1))
    assert names == {"SOULOS_ALLOW_LIVE_DSH_TESTS"}, names