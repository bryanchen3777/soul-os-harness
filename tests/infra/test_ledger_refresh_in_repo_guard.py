"""FEAT-LEDGER-REFRESH-IN-REPO (D2) — the daily ledger refresher must be a repo asset.

What this pins
--------------
The nightly cohort-ledger refresher used to live *outside* the repository
(``soul-os-harness-backups/ledger_refresh.py``): untracked, unreviewable and
unguardable, with its whole version history expressed as a chain of
``.bak-<date>`` filenames.  It now lives at ``scripts/ledger_refresh.py`` and
this module is the tripwire that keeps it there — and keeps the five
2026-10-27 fixes from being silently reverted.

G1  the refresher exists **and** is tracked by git.  A file that is on disk
    but unknown to git vanishes in a clean clone — that is the entire gap this
    ticket closes.
G2  it contains **no absolute path literal**; ``REPO`` must be derived from
    ``__file__`` so the repo can be cloned anywhere.
G3  importing it has **zero side effects** — no file created, no process
    started (the repo convention for everything under ``scripts/``).
G4  the five 2026-10-27 fixes are still in place:
      (a) a rewritten cohort entry keeps its ``types`` / ``bounds`` / agent
          comments — human bookkeeping must not be erased by the daily job;
      (b) the ``not_before`` fallback is *creation date + 8 days*;
      (c) per-node lower bounds come from the engine's own decay thresholds;
      (d) a **new** cohort gets ``not_before = cohort_not_before(date)``
          (before the fix it stayed ``None``, which silently skipped the whole
          per-node-bound computation and produced a false red on the next slot);
      (e) gate 2 accepts exit 3 (``PENDING_COHORT``) — only exit 1 / exit 2
          are a red light.
G5  ``--dry-run`` writes **no** file at all.

Design constraints honoured here
--------------------------------
* **No process spawning.**  R5 (``tests/infra/test_no_production_spawn_guard.py``)
  bans every ``subprocess.*`` / ``os.system`` / ``os.popen`` call site in
  ``tests/**`` unless it appears in a size-locked allowlist, and this ticket is
  explicitly not allowed to widen that allowlist.  The refresher is therefore
  driven **in-process** with its launcher stubbed out.
* **No ``git`` invocation.**  "Tracked" is read from ``.git/index`` — the same
  approach, for the same reason, as
  ``tests/infra/test_repo_hygiene_tracked_tests.py``.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.util
import io
import json
import os
import re
import struct
import sys
import tempfile
import time
import tokenize
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = REPO_ROOT / "scripts" / "ledger_refresh.py"
GIT_INDEX_PATH = REPO_ROOT / ".git" / "index"

#: The refresher's only repo-side write targets.  Everything else it writes
#: lands in ``%TEMP%`` (the sandbox) or in git itself.
REFRESH_WRITE_TARGETS: Sequence[str] = (
    "scripts/eval_lifecycle_cli.py",
    "logs/ENGINEERING_STATE.md",
    "scripts/ledger_refresh.py",
)

#: A drive-letter absolute path (``C:\...`` or ``C:/...``).
DRIVE_PATH_RE = re.compile(r"[A-Za-z]:[\\/]")

#: 32-hex node ids used by the synthetic ledger fixture below.
_ID_A = "a" * 32
_ID_B = "b" * 32

#: A minimal but *realistic* ledger entry: a cohort that a human curated
#: (types / bounds / agent comments).  The refresher must preserve all of it
#: when it rewrites the entry's node set.
_SYNTHETIC_LEDGER = """EXPECTED_RETIREMENT_COHORTS = (
    {
        "date": "2026-10-27",
        "not_before": "2026-11-04T02:00:00+00:00",
        "types": {
            "@A@": "belief",
        },
        "bounds": {
            "@B@": "2026-11-01T02:00:00+00:00",
        },
        "nodes": frozenset({
            "@A@",  # agent_alpha
            "@B@",  # agent_beta
        }),
    },
)
""".replace("@A@", _ID_A).replace("@B@", _ID_B)


# ═════════════════════════════════════════════════════════════
# Helpers
# ═════════════════════════════════════════════════════════════


def _load_module(name: str = "ledger_refresh_under_test"):
    """Import the refresher from its repo path (never as ``__main__``)."""
    spec = importlib.util.spec_from_file_location(name, LEDGER_PATH)
    assert spec is not None and spec.loader is not None, (
        f"cannot import {LEDGER_PATH}"
    )
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True  # never drop a __pycache__ into scripts/
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _sha256_or_none(path: Path) -> Optional[str]:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _elevation_fingerprint() -> Dict[str, Optional[str]]:
    """sha256 of every file under ``data/elevation/`` (the production graph)."""
    elev = REPO_ROOT / "data" / "elevation"
    if not elev.is_dir():
        return {}
    out: Dict[str, Optional[str]] = {}
    for path in sorted(elev.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            out[path.relative_to(REPO_ROOT).as_posix()] = _sha256_or_none(path)
    return out


def _tree_snapshot(root: Path) -> Set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


def drive_path_constants(source: str) -> List[str]:
    """Every string constant in *source* that holds a drive-letter path.

    The scan runs over the **parsed** constants, not the raw text: a raw scan
    cannot tell a real path from an escape sequence such as ``"for e in evs:\\n"``.
    """
    tree = ast.parse(source)
    hits: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if DRIVE_PATH_RE.search(node.value):
                hits.append(node.value)
    return hits


def drive_path_comments(source: str) -> List[Tuple[int, str]]:
    """Every comment in *source* that holds a drive-letter path."""
    hits: List[Tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT and DRIVE_PATH_RE.search(token.string):
            hits.append((token.start[0], token.string))
    return hits


# ── git index reader (no process spawning) ─────────────────────
#
# "Tracked" is read from ``.git/index`` for the same reason
# ``tests/infra/test_repo_hygiene_tracked_tests.py`` reads it there: R5 bans
# process spawning in ``tests/**``, and the index *is* git's inventory of
# tracked files.  Index versions 2 and 3 are understood; anything else raises
# rather than silently reporting "nothing is tracked".

_INDEX_MAGIC = b"DIRC"
_INDEX_HEADER = struct.Struct(">4sLL")
_INDEX_ENTRY_FIXED = 62
_INDEX_FLAG_NAME_MASK = 0x0FFF
_INDEX_FLAG_EXTENDED = 0x4000
_INDEX_LONG_PATH_SENTINEL = 0x0FFF


def read_tracked_paths(index_path: Path = GIT_INDEX_PATH) -> Set[str]:
    """Repo-relative posix paths recorded in the git index (= ``git ls-files``)."""
    data = index_path.read_bytes()
    if len(data) < _INDEX_HEADER.size:
        raise AssertionError(f"{index_path} is too short to be a git index")
    magic, version, entry_count = _INDEX_HEADER.unpack_from(data, 0)
    if magic != _INDEX_MAGIC:
        raise AssertionError(
            f"{index_path} does not look like a git index (magic={magic!r})"
        )
    if version not in (2, 3):
        raise AssertionError(
            f"unsupported git index version {version!r}; this guard parses v2/v3"
        )

    tracked: Set[str] = set()
    offset = _INDEX_HEADER.size
    for _ in range(entry_count):
        if offset + _INDEX_ENTRY_FIXED > len(data):
            raise AssertionError(
                f"{index_path} ended early after {len(tracked)} of "
                f"{entry_count} entries"
            )
        flags = struct.unpack_from(">H", data, offset + 60)[0]
        name_len = flags & _INDEX_FLAG_NAME_MASK
        cursor = offset + _INDEX_ENTRY_FIXED
        if version >= 3 and flags & _INDEX_FLAG_EXTENDED:
            cursor += 2
        fixed_len = cursor - offset
        if name_len < _INDEX_LONG_PATH_SENTINEL:
            raw = data[cursor:cursor + name_len]
            total = fixed_len + name_len + 1
        else:
            end = data.index(b"\0", cursor)
            raw = data[cursor:end]
            total = (end - offset) + 1
        tracked.add(raw.decode("utf-8", errors="surrogateescape"))
        offset += (total + 7) // 8 * 8

    if len(tracked) != entry_count:
        raise AssertionError(
            f"index parse produced {len(tracked)} paths for {entry_count} entries"
        )
    return tracked


# ── AST helpers for main() ─────────────────────────────────────


def _ledger_source() -> str:
    return LEDGER_PATH.read_text(encoding="utf-8")


def _ledger_tree() -> ast.Module:
    return ast.parse(_ledger_source())


def _main_function(tree: ast.Module) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            return node
    raise AssertionError("main() not found in scripts/ledger_refresh.py")


def _new_cohort_nb_expression() -> str:
    """Source of the ``else`` branch that assigns ``nb`` for a *new* cohort.

    This is the branch that used to leave ``nb = None``, which silently
    skipped the whole per-node-bound computation.
    """
    tree = _ledger_tree()
    main_fn = _main_function(tree)
    for node in main_fn.body:
        if not isinstance(node, ast.For):
            continue
        if ast.unparse(node.target) != "date":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.If):
                continue
            if ast.unparse(stmt.test).strip() != "date in entries":
                continue
            # if / elif / else  ->  orelse[0] is the elif, its orelse is the else body
            if not stmt.orelse or not isinstance(stmt.orelse[0], ast.If):
                raise AssertionError(
                    "the 'date in entries' branch has no elif/else chain"
                )
            for inner in stmt.orelse[0].orelse:
                if isinstance(inner, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "nb" for t in inner.targets
                ):
                    return ast.unparse(inner.value)
    raise AssertionError(
        "could not locate the new-cohort not_before assignment inside main()"
    )


def _gate2_condition() -> ast.expr:
    """The gate-2 condition (the one that mentions ``rc2``)."""
    main_fn = _main_function(_ledger_tree())
    for stmt in main_fn.body:
        if isinstance(stmt, ast.If):
            names = {n.id for n in ast.walk(stmt.test) if isinstance(n, ast.Name)}
            if "rc2" in names:
                return stmt.test
    raise AssertionError("could not locate the gate-2 condition inside main()")


def _pending_ok_expression() -> str:
    """Source of the ``pending_ok`` assignment in ``main()``."""
    main_fn = _main_function(_ledger_tree())
    for stmt in ast.walk(main_fn):
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pending_ok" for t in stmt.targets
        ):
            return ast.unparse(stmt.value)
    raise AssertionError("could not locate the pending_ok assignment inside main()")


# ── stubbed launcher (R5: no process spawning in tests/**) ─────


def _fake_sh(cmd, cwd=None, env=None):
    """Stand-in for the refresher's process launcher.

    The refresher spawns exactly two things: the sandbox simulation script and
    the sandbox CLI.  Their outputs are stubbed with the minimal shapes the
    refresher parses.  The guard's subject is the refresher's *own* write
    surface, not the sandbox CLI's (the CLI has its own guard).
    """
    joined = " ".join(str(part) for part in cmd)
    if "ledger_refresh_sim.py" in joined:
        closed = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        return 0, json.dumps({closed: {_ID_A: "agent_alpha"}}) + "\n", ""
    if "eval_lifecycle_cli.py" in joined:
        return 0, "VERDICT: PASS\n", ""
    return 0, "ok\n", ""


# ═════════════════════════════════════════════════════════════
# G1 — the refresher is a repo asset
# ═════════════════════════════════════════════════════════════


def test_g1_ledger_refresh_exists_and_is_git_tracked():
    assert LEDGER_PATH.is_file(), (
        "scripts/ledger_refresh.py is missing — the daily ledger refresher "
        "must live inside the repo, not in an unversioned backup directory"
    )
    tracked = read_tracked_paths()
    # Anti-vacuity: a broken/empty index parse must not look like "tracked".
    assert "scripts/eval_lifecycle_cli.py" in tracked, (
        "git index parse is broken or empty — the tracking check below would "
        "be meaningless"
    )
    rel = LEDGER_PATH.relative_to(REPO_ROOT).as_posix()
    assert rel in tracked, (
        f"{rel} exists on disk but git does not track it — stage it "
        f"(git add -- {rel}). A repo asset that is not in version control is "
        f"exactly the gap this guard closes."
    )


# ═════════════════════════════════════════════════════════════
# G2 — no absolute path literal; REPO is derived from __file__
# ═════════════════════════════════════════════════════════════


def test_g2_no_absolute_path_literal_and_repo_is_derived():
    source = _ledger_source()
    const_hits = drive_path_constants(source)
    assert not const_hits, (
        f"absolute path literal(s) in scripts/ledger_refresh.py: {const_hits}"
    )
    comment_hits = drive_path_comments(source)
    assert not comment_hits, (
        f"absolute path literal(s) in comments of scripts/ledger_refresh.py: "
        f"{comment_hits}"
    )
    module = _load_module()
    assert module.REPO == str(REPO_ROOT), (
        f"REPO={module.REPO!r} does not point at this repo ({str(REPO_ROOT)!r})"
    )
    assert module.REPO == str(Path(module.__file__).resolve().parents[1]), (
        "REPO must be derived from __file__, not from a constant"
    )
    assert module.CLI == os.path.join(
        str(REPO_ROOT), "scripts", "eval_lifecycle_cli.py"
    )
    assert module.ELEV == os.path.join(str(REPO_ROOT), "data", "elevation")
    assert module.CANON == os.path.join(str(REPO_ROOT), "logs", "ENGINEERING_STATE.md")
    assert module.PY == os.path.join(str(REPO_ROOT), ".venv", "Scripts", "python.exe")


def test_g2_absolute_path_detector_is_not_vacuous():
    """The detector must fire on a real path and stay quiet on a derived one."""
    assert drive_path_constants("REPO = r'C:\\Users\\bbfcc\\repo'")
    assert not drive_path_constants(
        "REPO = str(Path(__file__).resolve().parents[1])"
    )
    assert drive_path_comments("# backup lives at C:/Users/bbfcc/backup")
    assert not drive_path_comments("# REPO is derived from __file__")


# ═════════════════════════════════════════════════════════════
# G3 — importing the refresher has no side effects
# ═════════════════════════════════════════════════════════════


def test_g3_import_creates_no_file_and_starts_no_process(tmp_path, monkeypatch):
    before = _tree_snapshot(tmp_path)
    spawn_mod = importlib.import_module("subprocess")

    def _boom(*args, **kwargs):
        raise AssertionError(
            "importing scripts/ledger_refresh.py must not start a process"
        )

    with monkeypatch.context() as mp:
        for name in ("run", "Popen", "call", "check_call", "check_output"):
            mp.setattr(spawn_mod, name, _boom)
        mp.setattr(os, "system", _boom)
        mp.setattr(os, "popen", _boom)
        mp.chdir(tmp_path)
        module = _load_module("ledger_refresh_g3_probe")

    after = _tree_snapshot(tmp_path)
    assert before == after, (
        f"importing the refresher created {sorted(after - before)} in the cwd"
    )
    # Identity only — "REPO is derived from __file__" is G2's business.
    assert Path(module.__file__).name == "ledger_refresh.py"


# ═════════════════════════════════════════════════════════════
# G4 — the five 2026-10-27 fixes are still in place
# ═════════════════════════════════════════════════════════════


def test_g4a_rewritten_entry_keeps_types_bounds_and_agent_comments():
    """Fix ①: human bookkeeping survives the daily rewrite."""
    module = _load_module()
    out = module.rewrite_entry(
        _SYNTHETIC_LEDGER,
        "2026-10-27",
        {_ID_A, _ID_B},
        {_ID_A: "agent_alpha"},          # only one agent known to the refresher
        not_before="2026-11-04T02:00:00+00:00",
    )
    assert '"types": {' in out, "the cohort's types block was dropped"
    assert f'"{_ID_A}": "belief"' in out, "the cohort's per-node type was dropped"
    assert '"bounds": {' in out, "the cohort's bounds block was dropped"
    assert (
        f'"{_ID_B}": "2026-11-01T02:00:00+00:00"' in out
    ), "the cohort's per-node lower bound was dropped"
    assert "# agent_alpha" in out, "the agent comment was dropped"
    assert "# agent_beta" in out, (
        "a manually entered agent comment must not be downgraded to '?'"
    )
    assert "# ?" not in out, "an agent comment was downgraded to '?'"


def test_g4b_not_before_fallback_is_creation_date_plus_8_days():
    """Fix ②: the fallback is *creation date + 8 days*, not a hard-wired 2026-01-01."""
    module = _load_module()
    assert module.cohort_not_before("2026-10-27") == "2026-11-04T02:00:00+00:00"
    assert module.cohort_not_before("2026-10-30") == "2026-11-07T02:00:00+00:00"
    assert module.cohort_not_before("2026-12-28") == "2027-01-05T02:00:00+00:00"


def test_g4c_per_node_bounds_come_from_engine_decay_thresholds():
    """Fix ③: per-node lower bounds are the engine's own decay thresholds."""
    module = _load_module()
    nodes = [
        {
            "node_id": _ID_A,
            "node_type": "value",
            "created_ts": "2026-10-27T00:00:00+00:00",
            "last_support_ts": "2026-10-27T00:00:00+00:00",
        },
        {
            "node_id": _ID_B,
            "node_type": "value",
            "created_ts": "2026-10-27T00:00:00+00:00",
            "last_support_ts": "2026-10-27T00:00:00+00:00",
        },
    ]
    edges = [
        {
            "node_id": _ID_A,
            "valid_from_ts": "2026-10-28T00:00:00+00:00",
            "valid_until_ts": None,
        },
    ]
    thresholds = module.decay_thresholds(nodes, edges)
    assert thresholds[_ID_A] == "2026-11-04T00:00:00+00:00", (
        "the threshold of a node with a live support edge must be "
        "valid_from_ts + 7 days"
    )
    assert thresholds[_ID_B] == "2026-11-03T00:00:00+00:00", (
        "the threshold of a node without a live support edge must be "
        "last_support_ts + 7 days"
    )


def test_g4d_new_cohort_gets_not_before_from_cohort_not_before():
    """Fix ④: a *new* cohort must get a real ``not_before`` (was ``None``)."""
    module = _load_module()
    expression = _new_cohort_nb_expression()
    value = eval(
        expression,
        {"cohort_not_before": module.cohort_not_before, "date": "2026-11-05"},
        {},
    )
    assert value == "2026-11-13T02:00:00+00:00", (
        f"the new-cohort branch evaluated to {value!r}; it must use "
        f"cohort_not_before(date) (= creation date + 8 days @02:00Z)"
    )


def test_g4e_gate2_accepts_exit_3_pending_cohort():
    """Fix ⑤: exit 3 (PENDING_COHORT) is *not* a red light; exit 1 still is."""
    condition = ast.unparse(_gate2_condition())
    pending_expression = _pending_ok_expression()

    pending = {"rc1": 0, "rc2": 3, "verdict2": ["VERDICT: PENDING_COHORT ..."]}
    pending["pending_ok"] = eval(pending_expression, dict(pending), {})
    assert eval(condition, dict(pending), {}) is False, (
        "gate 2 must not treat exit 3 (PENDING_COHORT) as a red light"
    )

    failing = {"rc1": 0, "rc2": 1, "verdict2": ["VERDICT: FAIL ..."]}
    failing["pending_ok"] = eval(pending_expression, dict(failing), {})
    assert eval(condition, dict(failing), {}) is True, (
        "exit 1 (FAIL) must stay a red light"
    )


# ═════════════════════════════════════════════════════════════
# G5 — --dry-run writes no file
# ═════════════════════════════════════════════════════════════


@pytest.mark.allow_real_data_root
def test_g5_dry_run_writes_no_file(tmp_path, monkeypatch):
    """``--dry-run`` must leave every file exactly as it found it."""
    module = _load_module("ledger_refresh_g5_probe")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))  # sandbox -> pytest tmp
    monkeypatch.setattr(module, "sh", _fake_sh)
    monkeypatch.setattr(sys, "argv", ["ledger_refresh.py", "--dry-run"])

    before_targets = {
        rel: _sha256_or_none(REPO_ROOT / rel) for rel in REFRESH_WRITE_TARGETS
    }
    before_elev = _elevation_fingerprint()
    # Pre-flight: is something else writing into data/elevation right now?
    time.sleep(0.2)
    preflight_elev = _elevation_fingerprint()

    assert module.main() == 0

    after_targets = {
        rel: _sha256_or_none(REPO_ROOT / rel) for rel in REFRESH_WRITE_TARGETS
    }
    after_elev = _elevation_fingerprint()

    changed = {
        rel: (before_targets[rel], after_targets[rel])
        for rel in REFRESH_WRITE_TARGETS
        if before_targets[rel] != after_targets[rel]
    }
    assert not changed, f"--dry-run modified {sorted(changed)}"
    assert set(before_elev) == set(after_elev), (
        "--dry-run added or removed files under data/elevation/"
    )
    if before_elev == preflight_elev:
        # The production graph was quiescent during the pre-flight window, so a
        # content change during the dry-run can only be the refresher's doing.
        assert before_elev == after_elev, "--dry-run modified data/elevation/"
    else:
        warnings.warn(
            "data/elevation/ was being written by another writer during the "
            "pre-flight window, so its content is not attributable to the "
            "refresher; only the file inventory and the refresher's own write "
            "targets were asserted."
        )
