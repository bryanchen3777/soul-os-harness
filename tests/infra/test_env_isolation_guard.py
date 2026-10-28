"""tests/infra/test_env_isolation_guard.py — 環境隔離三道守衛。

三條守衛各自對應一個**可被推翻的命題**（不是「跑過就算」的裝飾）：

- **G-A（import 期 env 寫入）**：`tests/**` 不得出現**模組層級**（import 期）的
  環境變數賦值／清除。
  偵測範圍：`os.environ[...] = ...`（Assign／AnnAssign／AugAssign／del）、
  `os.environ.setdefault/pop/popitem/clear/update(...)`，
  **且必須解析別名**（`import os as _os` → `_os.environ`；
  `from os import environ` → `environ`；`from os import environ as _env` → `_env`）。
  ⇒ 別名盲點是「前提差一拍」的根因：守衛抓不到就等於沒有。

- **G-B（volatile 判準驅逐）**：`tests/**` 的 `pytest.mark.skipif` 條件不得含
  ①時間函式（`time.time`／`localtime`／`gmtime`／`monotonic`、
  `datetime.now`／`utcnow`／`today`）
  ②檔案存在性檢查（`os.path.exists`／`Path.exists`／`is_file`／`is_dir`）。
  ⇒ 把「牆鐘／存在性」從 skipif 驅逐；它們只該出現在 **env 閘門後面**。
  （條件若引用模組層級常數，也會**逐層解析**那個常數 —— 否則
  `X = Path(...).exists()` 這種「先算好再當條件」的形式會漏掉。）

- **G-C（skip 基準機制存在性）**：`tests/infra/skip_baseline.json` 存在且格式正確
  （`node_ids` 已排序、**無時間戳欄位**、`count` 與清單長度一致）；
  `tests/conftest.py` 確實掛上比對 hook（AST 檢查 hook 函式存在）。

🔴 每條守衛都有**專屬變異實驗**（工單 M1／M2／M3）：變異必須讓對應守衛**轉紅**。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

#: repo 根目錄（tests/infra/ → tests/ → repo 根）。
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TESTS_ROOT = _REPO_ROOT / "tests"

#: 共用純函式庫（skip 基準機制）。**不依賴套件化**。
_INFRA_DIR = Path(__file__).resolve().parent
if str(_INFRA_DIR) not in sys.path:
    sys.path.append(str(_INFRA_DIR))

import skip_baseline_lib  # noqa: E402


# ═════════════════════════════════════════════════════════════
# 共用掃描工具
# ═════════════════════════════════════════════════════════════


def _iter_test_files() -> List[Path]:
    """`tests/**` 下的所有 `.py`（排除 `__pycache__`）。"""
    return sorted(
        p
        for p in _TESTS_ROOT.rglob("*.py")
        if p.is_file() and "__pycache__" not in p.parts
    )


def _dotted(node: ast.AST) -> Optional[str]:
    """把 `a.b.c` 形式的屬性鏈攤成字串；遇到呼叫等非屬性節點回 None。"""
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _module_aliases(tree: ast.Module) -> Dict[str, str]:
    """local name → canonical 模組根（`import os as _os` → `_os` → `os`）。"""
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                root = node.module.split(".")[0]
                for alias in node.names:
                    aliases[alias.asname or alias.name] = root
    return aliases


def _environ_bound_names(tree: ast.Module) -> Set[str]:
    """`from os import environ [as X]` 綁出來的名稱集合。"""
    names: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "os" and node.level == 0:
                for alias in node.names:
                    if alias.name == "environ":
                        names.add(alias.asname or alias.name)
    return names


def _is_environ_ref(node: ast.AST, aliases: Dict[str, str], bound: Set[str]) -> bool:
    """node 是否指向 `os.environ`（**解析別名**）。"""
    if isinstance(node, ast.Name):
        return node.id in bound
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return isinstance(node.value, ast.Name) and aliases.get(node.value.id) == "os"
    return False


def _is_environ_subscript(node: ast.AST, aliases: Dict[str, str], bound: Set[str]) -> bool:
    """node 是否是 `os.environ[...]`（Subscript）。"""
    return (
        isinstance(node, ast.Subscript)
        and _is_environ_ref(node.value, aliases, bound)
    )


# ═════════════════════════════════════════════════════════════
# G-A：模組層級（import 期）環境變數寫入
# ═════════════════════════════════════════════════════════════

#: `os.environ.<方法>()` 形式的寫入／清除。
_ENV_WRITE_METHODS = {"setdefault", "pop", "popitem", "clear", "update"}


def _scan_env_writes_in_scope(
    body: List[ast.stmt],
    aliases: Dict[str, str],
    bound: Set[str],
    scope_label: str,
    hits: List[str],
) -> None:
    """遞迴掃描**同一個 scope** 內的 env 寫入（含 if/for/with/try 內部）。"""
    for node in body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if _is_environ_subscript(target, aliases, bound):
                    hits.append(f"{scope_label}:{node.lineno} os.environ[...] = ...")
        elif isinstance(node, ast.AnnAssign):
            if _is_environ_subscript(node.target, aliases, bound):
                hits.append(f"{scope_label}:{node.lineno} os.environ[...] : T = ...")
        elif isinstance(node, ast.AugAssign):
            if _is_environ_subscript(node.target, aliases, bound):
                hits.append(f"{scope_label}:{node.lineno} os.environ[...] += ...")
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                if _is_environ_subscript(target, aliases, bound):
                    hits.append(f"{scope_label}:{node.lineno} del os.environ[...]")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _ENV_WRITE_METHODS
                and _is_environ_ref(func.value, aliases, bound)
            ):
                hits.append(
                    f"{scope_label}:{node.lineno} os.environ.{func.attr}(...)"
                )
        # 同 scope 的複合敘述：內部仍是 import 期（模組層級 / 類別主體）執行。
        if isinstance(node, (ast.If, ast.While, ast.With, ast.Try, ast.For)):
            _scan_env_writes_in_scope(node.body, aliases, bound, scope_label, hits)
            for attr in ("orelse", "finalbody"):
                extra = getattr(node, attr, None) or []
                _scan_env_writes_in_scope(extra, aliases, bound, scope_label, hits)
            for handler in getattr(node, "handlers", []) or []:
                _scan_env_writes_in_scope(handler.body, aliases, bound, scope_label, hits)


def _scan_module_env_writes(path: Path) -> List[str]:
    """回傳該檔的**模組層級／類別主體** env 寫入位置清單。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    aliases = _module_aliases(tree)
    bound = _environ_bound_names(tree)
    hits: List[str] = []
    _scan_env_writes_in_scope(tree.body, aliases, bound, "module", hits)
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            _scan_env_writes_in_scope(
                node.body, aliases, bound, f"class:{node.name}", hits
            )
    return hits


# ═════════════════════════════════════════════════════════════
# G-B：skipif 條件不得含牆鐘／存在性判準
# ═════════════════════════════════════════════════════════════

#: 時間函式名（最後一段）。
_TIME_CALL_NAMES = {
    "time",
    "localtime",
    "gmtime",
    "monotonic",
    "monotonic_ns",
    "time_ns",
    "now",
    "utcnow",
    "today",
}

#: 檔案存在性方法名（最後一段）。
_EXISTENCE_METHODS = {"exists", "is_file", "is_dir"}


def _module_constant_bindings(tree: ast.Module) -> Dict[str, ast.expr]:
    """模組層級的常數綁定：name → 值運算式（只收 Assign／AnnAssign）。"""
    bindings: Dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                bindings[node.target.id] = node.value
    return bindings


def _collect_volatile_criteria(
    node: Optional[ast.AST],
    aliases: Dict[str, str],
    bindings: Dict[str, ast.expr],
    seen: Set[str],
) -> List[str]:
    """回傳運算式（含逐層解析的模組層級常數）裡的 volatile 判準清單。"""
    out: List[str] = []
    if node is None:
        return out
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            dotted = _dotted(sub.func)
            if dotted:
                parts = dotted.split(".")
                base = aliases.get(parts[0], parts[0])
                last = parts[-1]
                if base in {"time", "datetime"} and last in _TIME_CALL_NAMES:
                    out.append(f"time:{dotted}")
                elif base == "os" and last in {"exists", "isfile", "isdir"} and "path" in parts:
                    out.append(f"existence:{dotted}")
        elif isinstance(sub, ast.Attribute):
            if sub.attr in _EXISTENCE_METHODS:
                out.append(f"existence:{sub.attr}")
        elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
            bound_value = bindings.get(sub.id)
            if bound_value is not None and sub.id not in seen:
                seen.add(sub.id)
                out.extend(
                    _collect_volatile_criteria(bound_value, aliases, bindings, seen)
                )
    return out


def _scan_skipif_conditions(path: Path) -> List[str]:
    """回傳該檔所有 `pytest.mark.skipif` 條件裡的 volatile 判準。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return []
    aliases = _module_aliases(tree)
    bindings = _module_constant_bindings(tree)
    hits: List[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        dotted = _dotted(node.func)
        if not dotted:
            continue
        parts = dotted.split(".")
        if parts[-2:] != ["mark", "skipif"]:
            continue
        if not node.args:
            continue
        criteria = _collect_volatile_criteria(
            node.args[0], aliases, bindings, set()
        )
        for criterion in sorted(set(criteria)):
            hits.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{node.lineno} {criterion}")
    return hits


# ═════════════════════════════════════════════════════════════
# G-A / G-B / G-C
# ═════════════════════════════════════════════════════════════


def test_g_a_no_module_level_env_writes_in_tests():
    """G-A：`tests/**` 不得有模組層級（import 期）的環境變數寫入／清除。

    歷史事故：`tests/soul/test_decay_worker_settlement.py` 在 import 期寫
    `os.environ["SOUL_OS_DATA_DIR"]` 且永不還原 ⇒ 收集順序一翻，
    `tests/test_data_root_isolation.py` 的白名單自證測試就會
    一邊被 skip、一邊轉紅（實測 `19 passed, 1 skipped` ↔ `1 failed, 19 passed`）。
    """
    offenders: List[str] = []
    for path in _iter_test_files():
        for hit in _scan_module_env_writes(path):
            offenders.append(f"{path.relative_to(_REPO_ROOT).as_posix()}:{hit}")
    assert not offenders, (
        "tests/** 出現模組層級（import 期）環境變數寫入／清除 —— "
        "那是行程級、無還原的副作用，會讓 skip 集合與測試紅綠隨收集順序漂移:\n"
        + "\n".join(sorted(offenders))
    )


def test_g_b_skipif_conditions_have_no_volatile_criteria():
    """G-B：`pytest.mark.skipif` 條件不得含牆鐘時間／檔案存在性判準。

    那兩類判準會讓 skip 集合隨機器時間與資產樹漂移；它們只該出現在
    **env 閘門後面**（env opt-in ⇒ 離線環境恆定 skip，理由固定）。
    """
    offenders: List[str] = []
    for path in _iter_test_files():
        offenders.extend(_scan_skipif_conditions(path))
    assert not offenders, (
        "pytest.mark.skipif 條件含 volatile 判準（時間函式／檔案存在性）—— "
        "skip 集合會漂移；請改用 env opt-in 閘門:\n" + "\n".join(sorted(offenders))
    )


def test_g_c_skip_baseline_file_exists_and_is_well_formed():
    """G-C：`tests/infra/skip_baseline.json` 存在且格式正確。"""
    path = _INFRA_DIR / "skip_baseline.json"
    assert path.is_file(), f"skip 基準檔不存在: {path}"
    node_ids = skip_baseline_lib.load_baseline(path)
    assert node_ids == sorted(node_ids), "基準檔的 node_ids 必須已排序"
    assert len(node_ids) == len(set(node_ids)), "基準檔的 node_ids 不得重複"
    raw = path.read_text(encoding="utf-8")
    assert "timestamp" not in raw.lower(), "基準檔不得含時間戳欄位（會造成每日 churn）"


def test_g_c_conftest_hooks_the_skip_baseline_comparison():
    """G-C：`tests/conftest.py` 確實掛上 skip 基準比對 hook（AST 檢查）。"""
    conftest = _TESTS_ROOT / "conftest.py"
    assert conftest.is_file(), f"tests/conftest.py 不存在: {conftest}"
    tree = ast.parse(conftest.read_text(encoding="utf-8"), filename=str(conftest))
    hook_names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert (
        "pytest_terminal_summary" in hook_names or "pytest_sessionfinish" in hook_names
    ), "tests/conftest.py 沒有掛 skip 基準比對 hook（pytest_terminal_summary / pytest_sessionfinish）"
