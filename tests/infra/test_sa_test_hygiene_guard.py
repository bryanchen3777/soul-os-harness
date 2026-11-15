"""SA-TEST-HYGIENE-1 — T18/T19/T20 case-matrix artifacts must not dirty the repo.

The gap this closes
-------------------
``test_t18_zz_matrix_artifact`` (and its T19 twin) used to ``write_text`` the
per-run case matrix into the repo tree
(``verification/sa-transport-obs-2a|2b/case-matrix.json``). Every run — green
or red — therefore left ``M verification/**`` in the working tree, and a
passing suite could never be re-run cleanly. T20 already wrote its matrix to
``tmp_path`` and is the shape this guard pins for all three.

What this module checks (no subprocess, no network, no ports, no writes)
------------------------------------------------------------------------
* Rule A (static): each ``*_zz_matrix_artifact`` function takes ``tmp_path``
  and never references ``REPO_ROOT`` — the artifact cannot name a repo path.
* Rule B (static): every filesystem-write call in the three subject files
  lives inside a function that takes a ``tmp_path``/``tmp_factory`` sandbox
  parameter — so there is no second write path on either the green or the red
  road (reads such as ``.exists()``/``read_text``/``iterdir`` stay allowed).
* Rule C (hash): the two committed matrices' working-tree bytes still hash
  to the blob recorded in ``.git/index``. Pure-Python index parse (same
  on-disk format as ``test_repo_hygiene_tracked_tests``) plus a git blob
  hash — no ``git`` process is spawned, so the R5 spawn guard stays green.

Anti-vacuity: synthetic bad/good sources prove the rule still bites, and the
subject scan asserts a floor (the zz functions must actually be found).
"""
from __future__ import annotations

import ast
import hashlib
import struct
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GIT_INDEX_PATH = REPO_ROOT / ".git" / "index"

#: Subject files and their artifact test + frozen committed matrix.
SUBJECTS = (
    (
        "tests/soul_access/test_t18_exit7_coverage.py",
        "test_t18_zz_matrix_artifact",
        "verification/sa-transport-obs-2a/case-matrix.json",
    ),
    (
        "tests/soul_access/test_t19_failure_observation.py",
        "test_t19_zz_matrix_artifact",
        "verification/sa-transport-obs-2b/case-matrix.json",
    ),
    (
        "tests/soul_access/test_t20_live_fire_observation.py",
        "test_t20_zz_matrix_artifact",
        "verification/sa-open-fire-1/live-fire-4/offline/case-matrix.json",
    ),
)

#: Filesystem-write call shapes (attribute calls). Bare ``open(...)`` with a
#: write mode is handled separately.
WRITE_ATTRS = frozenset({"write_text", "write_bytes", "mkdir", "replace"})

#: Sandbox fixture names: a write is confined only inside these.
SANDBOX_PARAMS = frozenset({"tmp_path", "tmp_factory", "tmpdir"})

_INDEX_HEADER = struct.Struct(">4sLL")
_INDEX_ENTRY_FIXED = 62


def _write_calls_in(func: ast.FunctionDef):
    """Yield (lineno, label) for every filesystem-write call under ``func``."""
    found = []
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Attribute) and target.attr in WRITE_ATTRS:
            found.append((node.lineno, target.attr))
        elif isinstance(target, ast.Name) and target.id == "open":
            modes = [
                kw.value for kw in node.keywords if kw.arg == "mode"
            ]
            if len(node.args) >= 2:
                modes.append(node.args[1])
            is_write = False
            for mode in modes:
                if isinstance(mode, ast.Constant) and isinstance(mode.value, str):
                    if any(ch in mode.value for ch in ("w", "a", "x", "+")):
                        is_write = True
            if is_write:
                found.append((node.lineno, "open-write"))
    return found


def _rule_violations(source: str, path: str):
    """Return human-readable violations of Rules A+B for one subject file."""
    tree = compile(source, path, "exec", ast.PyCF_ONLY_AST)
    violations = []
    zz_names = [zz for subj, zz, _ in SUBJECTS if subj == path]
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = {arg.arg for arg in node.args.args}
        params |= {arg.arg for arg in node.args.kwonlyargs}
        src = ast.get_source_segment(source, node) or ""
        if node.name in zz_names:
            # Rule A: the artifact must be tmp-backed and repo-blind.
            if not (params & SANDBOX_PARAMS):
                violations.append(
                    "%s:%s: %s takes no tmp sandbox param" % (path, node.lineno, node.name)
                )
            if "REPO_ROOT" in src:
                violations.append(
                    "%s:%s: %s references REPO_ROOT (must be tmp_path only)"
                    % (path, node.lineno, node.name)
                )
        for lineno, label in _write_calls_in(node):
            # Rule B: every write call needs a sandbox param in scope.
            if not (params & SANDBOX_PARAMS):
                violations.append(
                    "%s:%s: write call %r outside any tmp sandbox param (in %s)"
                    % (path, lineno, label, node.name)
                )
    return violations


def _read_index_blobs(index_path: Path = GIT_INDEX_PATH):
    """Return {repo-relative posix path: blob sha1 hex} from the git index.

    Pure-Python read of ``.git/index`` (versions 2/3); no process is spawned.
    """
    data = index_path.read_bytes()
    if len(data) < _INDEX_HEADER.size:
        raise AssertionError(f"{index_path} is too short to be a git index")
    magic, version, entry_count = _INDEX_HEADER.unpack_from(data, 0)
    if magic != b"DIRC":
        raise AssertionError(f"{index_path} does not look like a git index")
    if version not in (2, 3):
        raise AssertionError(
            f"unsupported git index version {version!r}; extend this parser "
            "rather than letting the guard report a clean tree"
        )
    blobs = {}
    offset = _INDEX_HEADER.size
    for _ in range(entry_count):
        if offset + _INDEX_ENTRY_FIXED > len(data):
            raise AssertionError(f"{index_path} ended early after {len(blobs)} entries")
        sha = data[offset + 40:offset + 60].hex()
        flags = struct.unpack_from(">H", data, offset + 60)[0]
        name_len = flags & 0x0FFF
        cursor = offset + _INDEX_ENTRY_FIXED
        if version >= 3 and flags & 0x4000:
            cursor += 2
        fixed_len = cursor - offset
        if name_len < 0x0FFF:
            raw = data[cursor:cursor + name_len]
            total = fixed_len + name_len + 1
        else:
            end = data.index(b"\0", cursor)
            raw = data[cursor:end]
            total = (end - offset) + 1
        blobs[raw.decode("utf-8", errors="surrogateescape")] = sha
        offset += (total + 7) // 8 * 8
    if len(blobs) != entry_count:
        raise AssertionError("index parse produced %d paths for %d entries" % (len(blobs), entry_count))
    return blobs


def _git_blob_hash(content: bytes) -> str:
    """The blob id git records for ``content`` (``git hash-object`` semantics).

    This repo has ``core.autocrlf=true`` and no ``.gitattributes`` text rules,
    so checkout materialises LF blobs as CRLF working bytes while ``git
    status``/``git hash-object`` compare through the clean filter
    (CRLF -> LF on the way in). A raw-bytes hash would therefore cry dirty on
    a tree git itself calls clean. Mirror the filter: normalise CRLF -> LF
    for text bytes (no NUL in the probe window, same heuristic family git
    uses); hash bytes with NULs raw.
    """
    probe = content[:8000]
    if b"\0" not in probe:
        content = content.replace(b"\r\n", b"\n")
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


# ───────────────────────────────────────────────────────────
# Teeth: synthetic inputs prove the rule still bites
# ───────────────────────────────────────────────────────────


def test_rule_flags_repo_anchored_artifact_write():
    bad = (
        "def test_t18_zz_matrix_artifact():\n"
        "    out_dir = _REPO_ROOT / 'verification' / 'x'\n"
        "    out_dir.mkdir(parents=True, exist_ok=True)\n"
        "    (out_dir / 'case-matrix.json').write_text('{}', encoding='utf-8')\n"
    )
    violations = _rule_violations(bad, "tests/soul_access/test_t18_exit7_coverage.py")
    assert any("tmp" in v for v in violations), f"bad source not flagged: {violations!r}"
    assert any("REPO_ROOT" in v for v in violations), f"repo anchor not flagged: {violations!r}"


def test_rule_stays_silent_on_tmp_backed_artifact():
    good = (
        "def test_t18_zz_matrix_artifact(tmp_path):\n"
        "    out_dir = tmp_path / 't18-case-matrix'\n"
        "    out_dir.mkdir(parents=True, exist_ok=True)\n"
        "    (out_dir / 'case-matrix.json').write_text('{}', encoding='utf-8')\n"
    )
    assert _rule_violations(good, "tests/soul_access/test_t18_exit7_coverage.py") == []


def test_rule_flags_write_outside_sandbox_param():
    bad = (
        "def test_t18_something():\n"
        "    Path('verification/x.json').write_text('{}', encoding='utf-8')\n"
    )
    violations = _rule_violations(bad, "tests/soul_access/test_t18_exit7_coverage.py")
    assert violations, "write outside a tmp sandbox param must be flagged"


# ───────────────────────────────────────────────────────────
# The rules themselves
# ───────────────────────────────────────────────────────────


def test_matrix_artifacts_are_tmp_backed_and_repo_blind():
    """Rules A+B: no repo-tree write path in T18/T19/T20 (green or red road)."""
    all_violations = []
    for rel, _zz, _matrix in SUBJECTS:
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        all_violations.extend(_rule_violations(source, rel))
    assert all_violations == [], (
        "test artifact writes outside the tmp sandbox (repo tree would be "
        "dirtied on green AND red runs):\n" + "\n".join("  - " + v for v in all_violations)
    )


def test_subject_zz_functions_are_found():
    """Anti-vacuity: the scan really sees the three artifact functions."""
    found = 0
    for rel, zz, _matrix in SUBJECTS:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        names = {
            node.name for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert zz in names, f"{rel} no longer defines {zz} (guard is scanning air)"
        found += 1
    assert found == len(SUBJECTS) >= 3


def test_committed_matrices_match_head_blobs():
    """Rule C: tracked matrices unchanged — working bytes hash to index blobs.

    The T20 formal offline matrix is expected to be absent from the index
    (it was never committed); only the T18/T19 frozen references are pinned.
    """
    blobs = _read_index_blobs()
    assert len(blobs) > 100, "index parse yielded almost nothing; refusing a vacuous pass"
    for _rel, _zz, matrix in SUBJECTS:
        if matrix not in blobs:
            continue  # never-committed formal artifact: nothing to pin
        working = (REPO_ROOT / matrix).read_bytes()
        assert _git_blob_hash(working) == blobs[matrix], (
            f"{matrix} differs from the committed blob: the suite (or a hand "
            "edit) dirtied a frozen reference. Restore with "
            f"`git checkout -- {matrix}` and re-run."
        )
