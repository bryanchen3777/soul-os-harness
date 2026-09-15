"""TEST-INFRA-UI-PKILL guardrail — AST scan for pattern-based process kills in test code.

Context
-------
A test script used to contain, at module level::

    subprocess.run(['pkill', '-f', 'run_server'], capture_output=True)

That is a blind, name/command-line-pattern kill aimed at the production service, and it
fired on mere test *collection*. This guardrail makes that class of code impossible to
reintroduce anywhere under the test infrastructure.

Why this is AST-based (and not a text search)
---------------------------------------------
This is a *negative* assertion (expected hit count 0). A text-based "this string must
not appear" check quietly passes whenever the scanner itself breaks — wrong path, empty
file list, bad regex. To keep this from becoming a fake guardrail, the file also:

  * asserts how many files it actually scanned (a broken/empty scan fails loudly),
  * fails loudly on any file it cannot parse (never silently skips),
  * self-checks its classifier against known-bad AND known-good synthetic sources, so a
    classifier that has been neutered (e.g. a ban entry removed) turns this file red.

Rules (applied to every ``subprocess.*`` / ``os.system`` / ``os.popen`` / ``os.spawn*``
call in the scanned files, using that call's string-argument literals):

  R1. Any literal containing ``pkill`` / ``killall`` / ``Stop-Process`` is banned —
      these are name/pattern kills by construction.
  R2. ``taskkill`` is banned unless the same call also carries an explicit ``/PID``
      literal and carries neither ``/IM`` nor ``/FI``. PID-scoped tree cleanup of a
      process the test itself started is allowed (no name matching); name-pattern
      ``taskkill /IM`` / ``taskkill /FI "IMAGENAME..."`` is banned. Allowed PID-scoped
      sites are counted and reported so they can never grow silently.
  R3. A ``run_server`` literal (the production service module) is banned when the call
      would execute on import (module level outside a ``__main__`` guard) or from a
      ``test*`` function. It is allowed only inside a non-``test`` helper of a manual
      ``__main__`` driver that owns its own child handle.

Scan scope
----------
``tests/**/*.py`` (recursive) **plus** the repo-root ``test_*.py`` scripts. The root
level is included deliberately: the incident this guardrail exists for did not live
under ``tests/`` at all — ``pytest -q tests`` never even collected it — so a
``tests/``-only scanner would have been a fake guardrail for its own precedent.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = REPO_ROOT / "tests"

#: R1 primitives. Matched case-insensitively as substrings of a literal.
#: (``stop-process`` covers PowerShell ``Stop-Process``.)
BANNED_TOKENS: Tuple[str, ...] = ("pkill", "killall", "stop-process")

#: Process-spawning entry points whose string arguments are treated as commands.
SPAWN_ATTRS: Dict[str, frozenset] = {
    "subprocess": frozenset({
        "run", "call", "check_call", "check_output", "Popen",
        "getoutput", "getstatusoutput",
    }),
    "os": frozenset({
        "system", "popen",
        "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    }),
}

#: Minimum number of files the scanner must see. A scan that silently walks nothing is
#: exactly the "fake guardrail" failure mode; this floor makes it fail loudly instead.
MIN_SCANNED_FILES = 300


class Hit(NamedTuple):
    """A banned pattern-kill call site."""

    path: str
    lineno: int
    kind: str
    reason: str
    literals: Tuple[str, ...]


class Allowed(NamedTuple):
    """A deliberately permitted call site (documented, counted, never silent)."""

    path: str
    lineno: int
    kind: str
    reason: str


class ScanResult(NamedTuple):
    scanned_files: List[str]
    unparseable: List[Tuple[str, str]]
    hits: List[Hit]
    allowed: List[Allowed]


def _string_literals(call: ast.Call) -> List[str]:
    """Every string literal appearing in the call's arguments / keyword values."""
    out: List[str] = []
    for arg in list(call.args) + [kw.value for kw in call.keywords if kw.value is not None]:
        for node in ast.walk(arg):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return out


def _call_kind(call: ast.Call) -> Optional[str]:
    """``"subprocess.run"`` / ``"os.system"`` … for recognised spawn entry points."""
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    module, attr = func.value.id, func.attr
    if attr in SPAWN_ATTRS.get(module, frozenset()):
        return f"{module}.{attr}"
    return None


def _is_main_guard(test: ast.expr) -> bool:
    """True for ``if __name__ == "__main__":`` (either operand order)."""
    if not isinstance(test, ast.Compare) or len(test.ops) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    operands = [test.left] + list(test.comparators)
    names = [o for o in operands if isinstance(o, ast.Name) and o.id == "__name__"]
    consts = [o for o in operands if isinstance(o, ast.Constant) and o.value == "__main__"]
    return bool(names) and bool(consts)


class _CallCollector(ast.NodeVisitor):
    """Collects spawn calls with their execution context."""

    def __init__(self) -> None:
        self.func_stack: List[str] = []
        self.main_guard_depth = 0
        self.calls: List[Tuple[ast.Call, str, bool, Optional[str]]] = []

    # -- context tracking -------------------------------------------------
    def _visit_function(self, node) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_FunctionDef(self, node) -> None:  # noqa: N802 - ast API
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node) -> None:  # noqa: N802 - ast API
        self._visit_function(node)

    def visit_If(self, node) -> None:  # noqa: N802 - ast API
        if _is_main_guard(node.test):
            self.main_guard_depth += 1
            for stmt in node.body:
                self.visit(stmt)
            self.main_guard_depth -= 1
            for stmt in node.orelse:
                self.visit(stmt)
        else:
            self.generic_visit(node)

    # -- call collection --------------------------------------------------
    def visit_Call(self, node) -> None:  # noqa: N802 - ast API
        kind = _call_kind(node)
        if kind is not None:
            executes_on_import = (not self.func_stack) and self.main_guard_depth == 0
            enclosing = self.func_stack[-1] if self.func_stack else None
            self.calls.append((node, kind, executes_on_import, enclosing))
        self.generic_visit(node)


def _classify(
    kind: str,
    literals: List[str],
    *,
    executes_on_import: bool,
    enclosing_func: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(ban_reason, allow_reason)``; at most one of them is non-None."""
    low = [lit.lower() for lit in literals]
    stripped = [lit.strip().lower() for lit in literals]

    # R1 — pattern/name kills.
    for token in BANNED_TOKENS:
        if any(token in lit for lit in low):
            return f"name/pattern-based process kill ({token!r}) in {kind}", None

    # R2 — taskkill: only PID-scoped is tolerable.
    if any("taskkill" in lit for lit in low):
        name_pattern = any(s in ("/im", "/fi") for s in stripped)
        has_pid = any(s == "/pid" for s in stripped)
        if name_pattern or not has_pid:
            return (
                f"taskkill without an explicit /PID (name/pattern kill) in {kind}: {literals!r}",
                None,
            )
        return None, "taskkill /PID <pid> — PID-scoped tree cleanup (no name matching)"

    # R3 — the production service must never be spawned by collected/module-level test code.
    if any("run_server" in lit for lit in low):
        if executes_on_import:
            return (
                f"production service ('run_server') launched at module level in {kind} "
                f"(executes on import/collection): {literals!r}",
                None,
            )
        if enclosing_func and enclosing_func.startswith("test"):
            return (
                f"production service ('run_server') launched from test function "
                f"{enclosing_func}() via {kind}: {literals!r}",
                None,
            )
        return None, (
            f"manual __main__ driver launching the service by path via {kind} "
            f"(helper {enclosing_func}(), owns its own handle)"
        )

    return None, None


def scan_source(src: str, path: str = "<synthetic>") -> Tuple[List[Hit], List[Allowed]]:
    """Classify every spawn call in ``src``. Raises ``SyntaxError`` if unparseable."""
    tree = ast.parse(src)
    collector = _CallCollector()
    collector.visit(tree)

    hits: List[Hit] = []
    allowed: List[Allowed] = []
    for call, kind, executes_on_import, enclosing in collector.calls:
        literals = _string_literals(call)
        ban, allow = _classify(
            kind,
            literals,
            executes_on_import=executes_on_import,
            enclosing_func=enclosing,
        )
        rel = path
        if ban is not None:
            hits.append(Hit(rel, call.lineno, kind, ban, tuple(literals)))
        elif allow is not None:
            allowed.append(Allowed(rel, call.lineno, kind, allow))
    return hits, allowed


def _scan_files(paths: List[Path]) -> ScanResult:
    scanned: List[str] = []
    unparseable: List[Tuple[str, str]] = []
    hits: List[Hit] = []
    allowed: List[Allowed] = []

    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        scanned.append(rel)
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            file_hits, file_allowed = scan_source(src, rel)
        except SyntaxError as exc:  # a scanner that skips files silently is a fake guardrail
            unparseable.append((rel, f"{exc.__class__.__name__}: {exc}"))
            continue
        hits.extend(file_hits)
        allowed.extend(file_allowed)

    return ScanResult(scanned, unparseable, hits, allowed)


def scanned_test_files() -> List[Path]:
    """``tests/**/*.py`` (recursive) plus the repo-root ``test_*.py`` scripts."""
    under_tests = sorted(p for p in TESTS_DIR.rglob("*.py") if p.is_file())
    at_root = sorted(p for p in REPO_ROOT.glob("test_*.py") if p.is_file())
    return under_tests + at_root


def _fmt_hits(hits: List[Hit]) -> str:
    return "\n".join(
        f"  {h.path}:{h.lineno}  [{h.kind}]  {h.reason}\n      literals={list(h.literals)!r}"
        for h in hits
    )


# ───────────────────────────────────────────────────────────
# The guardrail itself
# ───────────────────────────────────────────────────────────


def test_no_pattern_process_kill_in_test_infrastructure(capsys):
    """R1/R2/R3: zero banned pattern-kill call sites across the scanned test tree."""
    result = _scan_files(scanned_test_files())

    assert not result.unparseable, (
        "掃描器無法解析以下測試檔（不得靜默跳過）:\n"
        + "\n".join(f"  {p}: {err}" for p, err in result.unparseable)
    )
    assert len(result.scanned_files) >= MIN_SCANNED_FILES, (
        f"掃描器只掃到 {len(result.scanned_files)} 個檔 (< {MIN_SCANNED_FILES})，"
        "掃描範圍可能已損壞（負例型護欄不得靜默放行）"
    )

    with capsys.disabled():
        # Visible inventory: proves the scanner ran and shows every tolerated site.
        print(
            f"\n[TEST-INFRA-UI-PKILL] scanned_files={len(result.scanned_files)} "
            f"banned_hits={len(result.hits)} allowed_pid_or_driver_sites={len(result.allowed)}"
        )
        for a in result.allowed:
            print(f"  allowed: {a.path}:{a.lineno} [{a.kind}] {a.reason}")

    assert result.hits == [], (
        f"偵測到 {len(result.hits)} 個盲殺/名稱模式殺進程呼叫"
        f"（pkill/killall/Stop-Process/名稱模式 taskkill/模組層級 run_server）：\n"
        + _fmt_hits(result.hits)
    )


def test_scanner_inventory_covers_root_level_test_scripts():
    """The scan scope must include the root scripts — that is where the precedent lived."""
    files = {p.relative_to(REPO_ROOT).as_posix() for p in scanned_test_files()}
    assert "tests/test_infra_no_pattern_process_kill.py" in files
    assert "test_ui.py" in files, "根目錄 test_*.py 必須在掃描範圍內（本次事故的實際位置）"
    assert len(files) >= MIN_SCANNED_FILES, f"掃描檔數 {len(files)} < {MIN_SCANNED_FILES}"


# ───────────────────────────────────────────────────────────
# Scanner self-check: a neutered classifier must not pass quietly
# ───────────────────────────────────────────────────────────

#: (snippet, label) — every snippet MUST produce at least one hit.
_BAD_SNIPPETS: Tuple[Tuple[str, str], ...] = (
    (
        "import subprocess\n"
        "subprocess.run(['pkill', '-f', 'run_server'], capture_output=True)\n",
        "the exact incident pattern (module level)",
    ),
    (
        "import subprocess\n"
        "def go():\n"
        "    subprocess.run(['pkill', '-f', 'run_server'])\n",
        "the incident pattern wrapped in a helper",
    ),
    ("import subprocess\nsubprocess.run(['killall', 'python'])\n", "killall"),
    (
        "import subprocess\nsubprocess.run(['taskkill', '/IM', 'node.exe', '/F'])\n",
        "taskkill by image name",
    ),
    (
        "import subprocess\n"
        "subprocess.run(['taskkill', '/F', '/FI', 'IMAGENAME eq node.exe'])\n",
        "taskkill by image-name filter",
    ),
    (
        "import subprocess\n"
        "subprocess.run(['taskkill', str(pid), '/T', '/F'])\n",
        "taskkill with no /PID switch is not PID-scoped -> banned",
    ),
    (
        "import subprocess\n"
        "subprocess.run(['powershell', '-Command', 'Stop-Process', '-Name', 'python'])\n",
        "PowerShell Stop-Process by name",
    ),
    ("import os\nos.system('pkill -f run_server')\n", "os.system with a shell command"),
    ("import os\nos.popen('killall python')\n", "os.popen with a shell command"),
    (
        "import subprocess\nsubprocess.Popen(['scripts/run_server.py'])\n",
        "production service spawned at module level",
    ),
    (
        "import subprocess\n"
        "def test_spawns_production():\n"
        "    subprocess.Popen(['scripts/run_server.py'])\n",
        "production service spawned from a test function",
    ),
)

#: (snippet, label) — every snippet MUST produce zero hits.
_GOOD_SNIPPETS: Tuple[Tuple[str, str], ...] = (
    (
        "import subprocess\n"
        "def _kill_tree(pid):\n"
        "    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'])\n",
        "PID-scoped tree kill of a pid we hold",
    ),
    ("proc.kill()\n", "own handle kill()"),
    ("proc.terminate()\nproc.wait(timeout=10)\n", "own handle terminate()+wait()"),
    (
        "import subprocess\n"
        "def start():\n"
        "    return subprocess.Popen(['scripts/run_server.py'])\n"
        "if __name__ == '__main__':\n"
        "    start().kill()\n",
        "manual __main__ driver, own handle, no pattern matching",
    ),
    ("import urllib.request\nurllib.request.urlopen('http://localhost:8000/')\n", "plain read-only probe"),
)


@pytest.mark.parametrize("src,label", _BAD_SNIPPETS, ids=[lbl for _, lbl in _BAD_SNIPPETS])
def test_scanner_flags_known_bad_sources(src, label):
    """The classifier must flag every known-bad shape (guards against silent neutering)."""
    hits, _ = scan_source(src)
    assert hits, f"掃描器漏掉了已知的危險樣本（{label}）——護欄已失效"


@pytest.mark.parametrize("src,label", _GOOD_SNIPPETS, ids=[lbl for _, lbl in _GOOD_SNIPPETS])
def test_scanner_allows_own_handle_lifecycle_sources(src, label):
    """The classifier must not red on legitimate own-handle process management."""
    hits, _ = scan_source(src)
    assert not hits, f"掃描器誤判合法樣本（{label}）: {[h.reason for h in hits]}"


def test_classifier_ban_list_is_intact():
    """Explicit guard for the ban list itself: neutering it must be a visible failure."""
    assert set(BANNED_TOKENS) == {"pkill", "killall", "stop-process"}
    assert "run" in SPAWN_ATTRS["subprocess"] and "Popen" in SPAWN_ATTRS["subprocess"]
    assert "system" in SPAWN_ATTRS["os"] and "popen" in SPAWN_ATTRS["os"]
