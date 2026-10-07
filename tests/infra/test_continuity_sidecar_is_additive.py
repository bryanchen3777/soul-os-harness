"""Memory Continuity Observability v1 — AST 護欄（G1–G5）。

D4 的不可繞過機械化保證：**sidecar 只能觀測已經發生的決策**。
這些護欄不依賴「執行者自律」，而是直接比對原始碼結構：

* **G1** ``record_injection`` 不得賦值 / 變更 ``eligible`` 或 ``load_result``
* **G2** ``continuity.py`` 不得 import 任何 LLM client（白名單外全禁）
* **G3** ``format_for_prompt`` 與 HEAD 版本逐節點相同
* **G4** ``continuity.py`` 不得 import ``src.memory.sage``
* **G5** ``middleware.py`` 的 ``_append_loader_trace`` 函式體未被改動

🔴 本檔案**不含任何 ``subprocess`` 呼叫**（spawn guard R5 逐行列管
``tests/**`` 的每個行程衍生呼叫點）。取 HEAD 原始碼的 helper 放在掃描範圍外的
``harness/continuity_analyze.py::git_head_source``，它只讀版本控制物件，
絕不啟動服務。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from harness.continuity_analyze import git_head_source  # noqa: E402

CONTINUITY_PY = REPO_ROOT / "src" / "memory" / "continuity.py"
MIDDLEWARE_PY = REPO_ROOT / "src" / "memory" / "middleware.py"
LOADER_PY = REPO_ROOT / "src" / "memory" / "v1" / "loader.py"

#: continuity.py 允許 import 的頂層模組白名單（G2：白名單外全禁）。
ALLOWED_IMPORTS: Set[str] = {
    "__future__",
    "hashlib",
    "json",
    "logging",
    "os",
    "time",
    "unicodedata",
    "uuid",
    "datetime",
    "pathlib",
    "typing",
}

#: 明確點名的禁區（即使白名單被放寬也不得出現）。
FORBIDDEN_MODULES = {
    "httpx",
    "openai",
    "anthropic",
    "google",
    "google.generativeai",
    "requests",
    "aiohttp",
    "urllib3",
    "src.llm.proxy",
    "src.llm",
    "src.memory.sage",
    "src.memory.sage.graph_store",
}

#: G1：不得對這些目標做變更型操作（它們是「已發生的決策」的來源）。
READ_ONLY_TARGETS = {"eligible", "load_result"}
MUTATING_METHODS = {"append", "pop", "sort", "remove", "clear", "extend", "insert"}


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_function(src: str, name: str) -> ast.FunctionDef:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"找不到函式 {name}")


def _base_name(node: ast.AST):
    """回傳屬性存取鏈的最底層 Name（例如 rows.append → rows）。"""
    while isinstance(node, ast.Attribute):
        node = node.value
    if isinstance(node, ast.Name):
        return node.id
    return None


def test_g1_record_injection_never_mutates_eligible_or_load_result():
    """G1: record_injection 只能讀 eligible / load_result。"""
    fn = _find_function(_read(CONTINUITY_PY), "record_injection")

    for node in ast.walk(fn):
        # 賦值：eligible = ... / load_result = ... / eligible.x = ...
        if isinstance(node, ast.Assign):
            for target in node.targets:
                base = _base_name(target)
                assert base not in READ_ONLY_TARGETS, (
                    f"record_injection 不得賦值給 {base}（:?? D4 additive-only）"
                )
        if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            base = _base_name(node.target)
            assert base not in READ_ONLY_TARGETS, (
                f"record_injection 不得改寫 {base}"
            )
        if isinstance(node, ast.Delete):
            for target in node.targets:
                assert _base_name(target) not in READ_ONLY_TARGETS, (
                    "record_injection 不得刪除 eligible / load_result 的元素"
                )
        # 變更型呼叫：eligible.append(...) / load_result.sort(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in MUTATING_METHODS:
                base = _base_name(node.func.value)
                assert base not in READ_ONLY_TARGETS, (
                    f"record_injection 不得呼叫 {base}.{node.func.attr}()"
                )


def test_g2_continuity_has_no_llm_client_import():
    """G2: 零 LLM 呼叫（P4）—— 白名單外全禁。"""
    tree = ast.parse(_read(CONTINUITY_PY))
    imported: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相對 import
                continue
            imported.append(node.module or "")

    for name in imported:
        root = name.split(".")[0]
        assert root not in FORBIDDEN_MODULES, f"continuity.py 不得 import {name}"
        assert name in ALLOWED_IMPORTS or root in ALLOWED_IMPORTS, (
            f"continuity.py import {name} 不在白名單內（G2：白名單外全禁）"
        )


def test_g4_continuity_does_not_import_sage():
    """G4: SAGE 零耦合（D1 明確不碰）。"""
    src = _read(CONTINUITY_PY)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sage" not in alias.name, f"不得 import {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            assert "sage" not in (node.module or ""), f"不得 import {node.module}"
    assert "from src.memory.sage" not in src


def test_g3_format_for_prompt_is_ast_identical_to_head():
    """G3: format_for_prompt 函式碼 AST 與 HEAD 版本逐節點相同。"""
    head_src = git_head_source("src/memory/v1/loader.py")
    head_fn = _find_function(head_src, "format_for_prompt")
    work_fn = _find_function(_read(LOADER_PY), "format_for_prompt")
    assert ast.dump(head_fn) == ast.dump(work_fn), (
        "format_for_prompt 的 AST 與 HEAD 不一致 —— 這是 frozen contract"
    )


def test_g3b_loader_file_is_byte_identical_to_head():
    """G3 加強：整個 loader.py 逐 byte 未變（零改動）。"""
    head_src = git_head_source("src/memory/v1/loader.py")
    assert _read(LOADER_PY).replace("\r\n", "\n") == head_src.replace("\r\n", "\n"), (
        "src/memory/v1/loader.py 有任何改動 —— 本票要求零改動"
    )


def test_g5_append_loader_trace_is_unchanged():
    """G5: _append_loader_trace 函式體未被改動（AST + 逐行原文）。"""
    head_src = git_head_source("src/memory/middleware.py")
    head_fn = _find_function(head_src, "_append_loader_trace")
    work_fn = _find_function(_read(MIDDLEWARE_PY), "_append_loader_trace")
    assert ast.dump(head_fn) == ast.dump(work_fn), (
        "_append_loader_trace 的 AST 與 HEAD 不一致 —— 既有 trace 行為被改動"
    )


def test_g5b_middleware_diff_is_purely_additive():
    """middleware.py 的改動必須是純新增（沒有刪除 / 修改既有行）。"""
    head_src = git_head_source("src/memory/middleware.py")
    head_lines = [l.strip() for l in head_src.replace("\r\n", "\n").splitlines()
                  if l.strip()]
    work_lines = [l.strip() for l in _read(MIDDLEWARE_PY).replace("\r\n", "\n").splitlines()
                  if l.strip()]
    work_set = set(work_lines)
    missing = [line for line in head_lines if line not in work_set]
    assert not missing, (
        f"middleware.py 有 {len(missing)} 行既有內容消失（必須是純 additive）:\n"
        + "\n".join(missing[:10])
    )


def test_g_continuity_sidecar_writes_to_new_files_only():
    """Sidecar 不得寫進既有 loader_trace.jsonl（工單逐點驗收第 3 條）。"""
    from src.memory.continuity import INJECTION_FILENAME, TURN_FILENAME
    assert INJECTION_FILENAME == "continuity_injection.jsonl"
    assert TURN_FILENAME == "continuity_turn.jsonl"
    assert "loader_trace" not in INJECTION_FILENAME
    assert "loader_trace" not in TURN_FILENAME