"""TEST-INFRA-ROOT-SCRIPTS-1 (D3) — AST guard: no production spawn / blind kill / pinned address.

Context
-------
Ten ``test_*.py`` scripts used to live at the repo root. They were never collected
by ``pytest -q tests``, but a **bare** ``pytest`` at the repo root does collect
them (default ``python_files = test_*.py``), and for several of them collection
alone executes the module body:

  * ``test_chat.py`` / ``test_fix.py`` / ``test_minimax.py`` —
    ``subprocess.Popen([sys.executable, 'scripts/run_server.py'])``: a **second
    production service on the service port**.
  * ``test_telegram.py`` and ``test_stage41_ws_ping{,2,3}.py`` — a bare
    ``asyncio.run(main())`` at module level: import-time side effects against the
    live service (real Telegram pollers / real WebSocket traffic).
  * an earlier revision of ``test_ui.py`` carried a module-level
    ``subprocess.run(['pkill', '-f', 'run_server'])`` — a blind, command-line
    pattern kill that matches the **production** process.

This file is the guardrail that makes the whole class impossible to reintroduce.
Its companion (``tests/test_infra_no_pattern_process_kill.py``, from
``TEST-INFRA-UI-PKILL``) covers pattern kills; this one adds the categorical
production-spawn ban, the pinned-service-address ban and the "repo root must stay
free of ``test_*.py``" rule.

Why AST and not a text search
-----------------------------
These are *negative* assertions (expected hit count 0). A text search quietly
passes whenever the scanner itself breaks — wrong path, empty file list, bad
regex. To keep this from being a fake guardrail, the module also:

  * asserts how many files it actually scanned (an empty/broken scan fails loudly),
  * fails loudly on any file it cannot parse (never silently skips),
  * self-checks its classifier against known-bad AND known-good synthetic sources,
    so a neutered classifier (a rule deleted, a token list emptied) turns red,
  * locks both allowlists by an explicit expected size, so a real offender cannot
    be silenced by quietly appending one entry.

Scan scope
----------
Repo-root ``*.py`` **plus** ``tests/**/*.py``. The root level is included on
purpose: ``pytest -q tests`` never collected the incident files, so a
``tests/``-only scanner would be blind to its own precedent.
"""
from __future__ import annotations

import ast
import functools
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"

#: Process-spawning entry points whose string arguments are treated as commands.
SPAWN_ATTRS: Dict[str, frozenset] = {
    "subprocess": frozenset({
        "Popen", "run", "call", "check_call", "check_output",
        "getoutput", "getstatusoutput",
    }),
    "os": frozenset({
        "system", "popen",
        "spawn", "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    }),
}

#: R1 — name/command-pattern kill primitives. Matched case-insensitively.
KILL_TOKENS: Tuple[str, ...] = ("pkill", "killall", "stop-process", "stop-service")

#: R2 — the production service module. Any spawn call carrying this token is banned.
PRODUCTION_MODULE_TOKEN = "run_server"

#: R3 — the production service address. ``(?:\d)`` grouping keeps this module free
#: of the very literal it bans, so the guard is not an exception to itself.
_P = r"(?:8000)"
ADDRESS_RE = re.compile(
    r"(?:https?|wss?)://[^\s'\"<>()]*:" + _P + r"(?!\d)"
    r"|(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):" + _P + r"(?!\d)",
    re.IGNORECASE,
)

#: Minimum files the scanner must see; a scan that walks nothing fails loudly.
MIN_SCANNED_FILES = 300

#: How many allowlist entries exists today. Any growth is a deliberate act.
EXPECTED_ADDRESS_ALLOWLIST_SIZE = 28
EXPECTED_KILL_ALLOWLIST_SIZE = 1


class Hit(NamedTuple):
    path: str
    lineno: int
    kind: str
    reason: str
    detail: str


class ScanResult(NamedTuple):
    scanned_files: List[str]
    unparseable: List[Tuple[str, str]]
    spawn_hits: List[Hit]
    kill_hits: List[Hit]
    address_hits: List[Hit]
    allowed_kills: List[Hit]


# ───────────────────────────────────────────────────────────
# Allowlists — explicit, reasoned, size-locked
# ───────────────────────────────────────────────────────────
#
# These are *pre-existing* sites that this ticket is not authorised to change
# (scope: only add tests/infra/** and tests/integration/**). Every entry carries a
# reason, and both lists are size-locked below, so neither can grow silently.

ADDRESS_ALLOWLIST: Dict[Tuple[str, int], str] = {
    # -- pre-existing tracked tests that probe the already-running service on the
    #    production port (read-only; they were green/red at baseline exactly as
    #    they are now — this ticket neither creates nor fixes them).
    ("tests/test_identity.py", 10): "pre-existing live WS probe of the running service (read-only)",
    ("tests/test_identity.py", 11): "pre-existing live HTTP probe of the running service (read-only)",
    ("tests/test_private_chat.py", 31): "pre-existing live probe of the running service (read-only)",
    ("tests/test_private_chat.py", 32): "pre-existing live WS probe of the running service (read-only)",
    ("tests/test_plan_a_launcher_v1.py", 94): "pre-existing /health probe of the running service (read-only)",
    ("tests/test_server_ops_python_path_v1.py", 79): "pre-existing /health probe of the running service (read-only)",
    ("tests/clients/test_vc_perf_opt_1.py", 303): "OpenAI-compatible base URL handed to an adapter (never dialled)",
    # -- the address-pattern fixtures of the companion guard
    ("tests/test_infra_no_pattern_process_kill.py", 398):
        "synthetic classifier fixture (a sample source string, not a live address)",
    # -- the production-port red line in the isolated e2e harness: the port value is
    #    asserted against so that the harness REFUSES to dial it.
    ("tests/test_websocket_e2e.py", 68):
        "PRODUCTION_PORT red line: value is compared against to refuse dialling it",
    # -- untracked one-off `_verify_*` driver scripts left in tests/ (not collected
    #    by pytest: they do not match test_*.py). They predate this ticket and are
    #    out of its authorised scope; listed individually so none can be added
    #    silently, and so their removal shrinks this list deliberately.
    ("tests/_verify_akane_jp.py", 106): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_akane_jp_v2.py", 48): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_akane_pressure.py", 51): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_anna_jp_v1.py", 19): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_anna_jp_v1_rerun2.py", 15): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_anna_jp_v1_rerun3.py", 15): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_anna_jp_v1_rerun3_v3.py", 18): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_mai_jp_v1.py", 18): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_mai_jp_v1_rerun.py", 11): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_mai_jp_v1_rerun1.py", 11): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_mai_jp_v1_rerun3.py", 11): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_miku_jp_v1.py", 25): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_post_a_live.py", 50): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_ram_jp_v1.py", 19): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_ram_jp_v2_scene3.py", 14): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_ram_jp_v2_scene3d.py", 14): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_rem_jp_v1.py", 19): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_ruka_jp_v1.py", 19): "untracked one-off live verify driver (pre-existing)",
    ("tests/_verify_yua_jp_v1.py", 20): "untracked one-off live verify driver (pre-existing)",
}

#: PID-scoped kills: the repo's own convention (see
#: tests/test_infra_no_pattern_process_kill.py R2) permits tree cleanup of a pid the
#: test itself owns, and bans name/pattern matching. Kept here as a counted allowlist.
KILL_ALLOWLIST: Dict[Tuple[str, int], str] = {
    ("tests/test_ts3_official_mcp_server.py", 108):
        "PID-scoped `taskkill /PID <pid> /T /F` tree cleanup of an npx child we started",
}


# ───────────────────────────────────────────────────────────
# Scanner
# ───────────────────────────────────────────────────────────


def scanned_python_files() -> List[Path]:
    """Repo-root ``*.py`` plus ``tests/**/*.py`` (recursive)."""
    root_py = sorted(p for p in REPO_ROOT.glob("*.py") if p.is_file())
    under_tests = sorted(p for p in TESTS_DIR.rglob("*.py") if p.is_file())
    return sorted(set(root_py) | set(under_tests))


def _docstring_nodes(tree: ast.AST) -> Set[int]:
    """``id()`` of every docstring constant, so prose is not read as code."""
    ids: Set[int] = set()
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if isinstance(node, holders):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _call_kind(call: ast.Call) -> Optional[str]:
    func = call.func
    if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
        return None
    module, attr = func.value.id, func.attr
    if attr in SPAWN_ATTRS.get(module, frozenset()):
        return f"{module}.{attr}"
    return None


def _string_literals(call: ast.Call) -> List[str]:
    out: List[str] = []
    for arg in list(call.args) + [kw.value for kw in call.keywords if kw.value is not None]:
        for node in ast.walk(arg):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append(node.value)
    return out


def scan_source(src: str, path: str = "<synthetic>") -> ScanResult:
    """Classify every spawn call and address literal in ``src``.

    Raises ``SyntaxError`` for unparseable source (callers must never skip it).
    """
    tree = ast.parse(src)
    is_test_tree = path.startswith("tests/") or path.startswith("tests\\")
    # Computed once per file: recomputing this inside the node loop made the scan
    # quadratic and unacceptably slow on the larger test files.
    doc_ids = _docstring_nodes(tree)

    spawn_hits: List[Hit] = []
    kill_hits: List[Hit] = []
    address_hits: List[Hit] = []
    allowed_kills: List[Hit] = []

    for node in ast.walk(tree):
        # -- R1/R2: spawn-call argument literals ------------------------------
        if isinstance(node, ast.Call):
            kind = _call_kind(node)
            if kind is not None:
                literals = _string_literals(node)
                low = [lit.lower() for lit in literals]
                stripped = [lit.strip().lower() for lit in literals]

                for token in KILL_TOKENS:
                    if any(token in lit for lit in low):
                        kill_hits.append(Hit(
                            path, node.lineno, kind,
                            f"name/pattern-based process kill ({token!r})",
                            f"literals={literals!r}",
                        ))

                if any("taskkill" in lit for lit in low):
                    name_pattern = any(s in ("/im", "/fi") for s in stripped)
                    has_pid = any(s == "/pid" for s in stripped)
                    if name_pattern or not has_pid:
                        kill_hits.append(Hit(
                            path, node.lineno, kind,
                            "taskkill without an explicit /PID (name/pattern kill)",
                            f"literals={literals!r}",
                        ))
                    else:
                        allowed_kills.append(Hit(
                            path, node.lineno, kind,
                            "PID-scoped taskkill (PID-scoped tree cleanup)",
                            f"literals={literals!r}",
                        ))

                if any(PRODUCTION_MODULE_TOKEN in lit for lit in low):
                    spawn_hits.append(Hit(
                        path, node.lineno, kind,
                        f"production service ({PRODUCTION_MODULE_TOKEN!r}) spawn — "
                        "categorically banned in the scanned tree",
                        f"literals={literals!r}",
                    ))

            # -- R3b: `port=8000` keyword ------------------------------------
            for kw in node.keywords:
                if (
                    kw.arg == "port"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == 8000
                ):
                    address_hits.append(Hit(
                        path, node.lineno, "kwarg",
                        "pinned production service port (port=8000)",
                        "port=8000 keyword",
                    ))

        # -- R3b: `SOMETHING_PORT = 8000` ------------------------------------
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if node.value.value == 8000 and not isinstance(node.value.value, bool):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "PORT" in target.id.upper():
                        address_hits.append(Hit(
                            path, node.lineno, "assign",
                            "pinned production service port (PORT-named = 8000)",
                            f"{target.id} = 8000",
                        ))

        # -- R3: pinned service-address string literals (tests/** only) -------
        if (
            is_test_tree
            and isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in doc_ids
        ):
            match = ADDRESS_RE.search(node.value)
            if match:
                address_hits.append(Hit(
                    path, node.lineno, "literal",
                    "pinned production service address",
                    f"matched={match.group(0)!r} in {node.value[:80]!r}",
                ))

    return ScanResult([path], [], spawn_hits, kill_hits, address_hits, allowed_kills)


def scan_files(paths: List[Path]) -> ScanResult:
    scanned: List[str] = []
    unparseable: List[Tuple[str, str]] = []
    spawn_hits: List[Hit] = []
    kill_hits: List[Hit] = []
    address_hits: List[Hit] = []
    allowed_kills: List[Hit] = []

    for path in paths:
        rel = path.relative_to(REPO_ROOT).as_posix()
        scanned.append(rel)
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
            result = scan_source(src, rel)
        except SyntaxError as exc:
            unparseable.append((rel, f"{exc.__class__.__name__}: {exc}"))
            continue
        spawn_hits.extend(result.spawn_hits)
        kill_hits.extend(result.kill_hits)
        address_hits.extend(result.address_hits)
        allowed_kills.extend(result.allowed_kills)

    return ScanResult(scanned, unparseable, spawn_hits, kill_hits, address_hits, allowed_kills)


def _fmt(hits: List[Hit]) -> str:
    return "\n".join(f"  {h.path}:{h.lineno} [{h.kind}] {h.reason} | {h.detail}" for h in hits)


@functools.lru_cache(maxsize=1)
def scan_tree() -> ScanResult:
    """One scan per process, shared by every rule test (parsing 300+ files is not free).

    Per-process caching is what makes the mutations in D4 observable: each mutated
    run is a fresh pytest process, so it re-scans from disk.
    """
    return scan_files(scanned_python_files())


# ───────────────────────────────────────────────────────────
# The guardrail itself
# ───────────────────────────────────────────────────────────


def test_no_production_service_spawn_in_scanned_tree(capsys):
    """R1: zero spawn calls carrying the production module name."""
    result = scan_tree()

    assert not result.unparseable, (
        "掃描器無法解析以下檔案（不得靜默跳過）:\n"
        + "\n".join(f"  {p}: {e}" for p, e in result.unparseable)
    )
    assert len(result.scanned_files) >= MIN_SCANNED_FILES, (
        f"掃描器只掃到 {len(result.scanned_files)} 個檔 (< {MIN_SCANNED_FILES})；"
        "掃描範圍可能已損壞（負例型護欄不得靜默放行）"
    )

    with capsys.disabled():
        print(
            f"\n[ROOT-SCRIPTS-GUARD] scanned={len(result.scanned_files)} "
            f"spawn_hits={len(result.spawn_hits)} kill_hits={len(result.kill_hits)} "
            f"address_hits={len(result.address_hits)} allowed_kills={len(result.allowed_kills)}"
        )

    assert result.spawn_hits == [], (
        f"偵測到 {len(result.spawn_hits)} 處生產服務 spawn"
        f"（subprocess/os 呼叫引數字串含 {PRODUCTION_MODULE_TOKEN!r}）:\n"
        + _fmt(result.spawn_hits)
    )


def test_no_blind_process_kill_in_scanned_tree():
    """R2: zero name/pattern kills; PID-scoped kills must be the allowlisted ones."""
    result = scan_tree()

    assert result.kill_hits == [], (
        f"偵測到 {len(result.kill_hits)} 處盲殺/名稱模式殺進程呼叫:\n" + _fmt(result.kill_hits)
    )

    found_allowed = {(h.path, h.lineno) for h in result.allowed_kills}
    unexpected = found_allowed - set(KILL_ALLOWLIST)
    assert not unexpected, (
        "出現未經允許的 PID-scoped kill 站點（allowlist 不得任意增長）:\n"
        + "\n".join(f"  {p}:{ln}" for p, ln in sorted(unexpected))
    )
    assert len(KILL_ALLOWLIST) == EXPECTED_KILL_ALLOWLIST_SIZE, (
        f"kill allowlist 大小 {len(KILL_ALLOWLIST)} != 鎖定的 "
        f"{EXPECTED_KILL_ALLOWLIST_SIZE}（放寬白名單必須是刻意行為）"
    )


def test_no_pinned_service_address_in_tests():
    """R3: no pinned service address anywhere under ``tests/**``."""
    result = scan_tree()

    found = {(h.path, h.lineno) for h in result.address_hits}
    unexpected = found - set(ADDRESS_ALLOWLIST)
    assert not unexpected, (
        "偵測到未經允許的硬寫生產服務位址（tests/** 不得出現 :8000 服務位址）:\n"
        + _fmt([h for h in result.address_hits if (h.path, h.lineno) in unexpected])
    )

    # The allowlist is the only tolerated surface, and it is size-locked: adding a
    # new entry to silence a real offender is a visible, deliberate edit.
    assert len(ADDRESS_ALLOWLIST) == EXPECTED_ADDRESS_ALLOWLIST_SIZE, (
        f"address allowlist 大小 {len(ADDRESS_ALLOWLIST)} != 鎖定的 "
        f"{EXPECTED_ADDRESS_ALLOWLIST_SIZE}（白名單不得任意增長）"
    )


def test_repo_root_has_no_test_scripts():
    """R4: the repo root must stay free of ``test_*.py`` — the landmines' address.

    A bare ``pytest`` at the repo root collects ``test_*.py`` from the rootdir, and
    several of the removed scripts executed on import. If one ever comes back, this
    fails immediately.
    """
    at_root = sorted(p.name for p in REPO_ROOT.glob("test_*.py") if p.is_file())
    assert at_root == [], (
        f"repo 根目錄不得存在 test_*.py（裸跑 pytest 會收集並可能在 import 時執行）: {at_root}"
    )


def test_scanner_inventory_covers_root_and_tests():
    """The scan scope must include both the root and the tests tree."""
    files = {p.relative_to(REPO_ROOT).as_posix() for p in scanned_python_files()}
    assert "tests/infra/test_no_production_spawn_guard.py" in files
    assert "tests/conftest.py" in files
    assert len(files) >= MIN_SCANNED_FILES, f"掃描檔數 {len(files)} < {MIN_SCANNED_FILES}"


# ───────────────────────────────────────────────────────────
# Teeth: a neutered classifier must not pass quietly
# ───────────────────────────────────────────────────────────

#: (snippet, label) — each MUST produce at least one hit.
_BAD_SNIPPETS: Tuple[Tuple[str, str], ...] = (
    (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, 'scripts/run_server.py'])\n",
        "production service spawned at module level",
    ),
    (
        "import subprocess\n"
        "def start():\n"
        "    return subprocess.Popen(['scripts/run_server.py'])\n",
        "production service spawned from a non-test helper",
    ),
    (
        "import subprocess\n"
        "def test_bad():\n"
        "    subprocess.run(['python', 'scripts/run_server.py'])\n",
        "production service spawned from a test function",
    ),
    (
        "import os\nos.system('python scripts/run_server.py &')\n",
        "production service spawned through os.system",
    ),
    ("import subprocess\nsubprocess.run(['pkill', '-f', 'python'])\n", "pkill"),
    ("import subprocess\nsubprocess.run(['killall', 'python'])\n", "killall"),
    (
        "import subprocess\nsubprocess.run(['taskkill', '/IM', 'python.exe', '/F'])\n",
        "taskkill by image name",
    ),
    (
        "import subprocess\nsubprocess.run(['taskkill', '/F', '/FI', 'IMAGENAME eq python.exe'])\n",
        "taskkill by image-name filter",
    ),
    (
        "import subprocess\n"
        "subprocess.run(['powershell', '-Command', 'Stop-Process', '-Name', 'python'])\n",
        "PowerShell Stop-Process by name",
    ),
    (
        "import subprocess\nsubprocess.run(['powershell', '-Command', 'Stop-Service', 'SoulOS'])\n",
        "PowerShell Stop-Service by name",
    ),
)

#: (snippet, label) — each MUST produce zero hits.
_GOOD_SNIPPETS: Tuple[Tuple[str, str], ...] = (
    (
        "import subprocess\n"
        "def _kill_tree(pid):\n"
        "    subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'])\n",
        "PID-scoped taskkill of a pid we own",
    ),
    ("proc.kill()\n", "own handle kill()"),
    ("proc.terminate()\nproc.wait(timeout=10)\n", "own handle terminate()+wait()"),
    ("import pathlib\np = pathlib.Path('scripts') / 'run_server.py'\nsrc = p.read_text()\n",
     "reading the production module source (no spawn)"),
)


def _bad_address_snippet() -> str:
    """A synthetic source carrying a pinned address, assembled so that *this* file
    contains no such literal (the guard must not be an exception to itself)."""
    return "WS = '" + "ws://127.0.0.1" + ":" + "8000" + "/ws'\n"


def _bad_port_snippet() -> str:
    return "PORT_" + "PINNED = " + "8000" + "\n"


@pytest.mark.parametrize("src,label", _BAD_SNIPPETS, ids=[lbl for _, lbl in _BAD_SNIPPETS])
def test_scanner_flags_known_bad_sources(src, label):
    """The classifier must flag every known-bad shape (guards against neutering)."""
    result = scan_source(src, "tests/synthetic_bad.py")
    assert result.spawn_hits or result.kill_hits, (
        f"掃描器漏掉了已知的危險樣本（{label}）——護欄已失效"
    )


def test_scanner_flags_pinned_address_and_port():
    """The address/port rules must catch their own known-bad shapes."""
    addr = scan_source(_bad_address_snippet(), "tests/synthetic_addr.py")
    assert addr.address_hits, "掃描器漏掉了硬寫服務位址樣本——護欄已失效"

    port = scan_source(_bad_port_snippet(), "tests/synthetic_port.py")
    assert port.address_hits, "掃描器漏掉了硬寫 PORT=8000 樣本——護欄已失效"


@pytest.mark.parametrize("src,label", _GOOD_SNIPPETS, ids=[lbl for _, lbl in _GOOD_SNIPPETS])
def test_scanner_allows_legitimate_sources(src, label):
    """The classifier must not red on legitimate own-handle / read-only code."""
    result = scan_source(src, "tests/synthetic_good.py")
    assert not (result.spawn_hits or result.kill_hits or result.address_hits), (
        f"掃描器誤判合法樣本（{label}）: "
        f"{[h.reason for h in result.spawn_hits + result.kill_hits + result.address_hits]}"
    )


def test_scanner_reads_docstrings_as_prose_not_code():
    """Prose in a docstring must not be read as a pinned address (no false reds)."""
    src = '"""Docstring mentioning a legacy literal 127.0.0.1' + ":" + "8000" + ' only."""\nX = 1\n'
    result = scan_source(src, "tests/synthetic_doc.py")
    assert not result.address_hits, "docstring 內的敘述文字不應被當成硬寫位址"


def test_classifier_rule_tables_are_intact():
    """Explicit guard for the rule tables: emptying them must be a visible failure."""
    assert set(KILL_TOKENS) == {"pkill", "killall", "stop-process", "stop-service"}
    assert PRODUCTION_MODULE_TOKEN == "run_server"
    assert "Popen" in SPAWN_ATTRS["subprocess"] and "run" in SPAWN_ATTRS["subprocess"]
    assert "system" in SPAWN_ATTRS["os"] and "popen" in SPAWN_ATTRS["os"]
    assert ADDRESS_RE.search("http://localhost" + ":" + "8000/health") is not None
