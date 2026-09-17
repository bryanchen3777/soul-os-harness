"""TEST-REPO-HYGIENE-1 (D3) — every collectable test file under ``tests/**`` must be git-tracked.

The gap this closes
-------------------
``pytest`` collects *files on disk*, not files in git. An untracked ``test_*.py``
sitting in ``tests/`` therefore runs in every local regression, and then vanishes in
a clean clone. The asymmetry is silent: the local collection set and the
clean-clone collection set diverge, and a green local run proves less than it looks.

TEST-REPO-HYGIENE-1 closed that gap for the five files that were open at the time
(four tracked, one archived to ``docs/legacy/``). This module is the tripwire that
keeps it closed: a *new* collectable-but-untracked file under ``tests/**`` turns this
module red and is named in the failure message.

There is deliberately **no allowlist** here — every test file is expected to be
tracked, without exception. An exception list would just re-open the same gap one
entry at a time.

Why "tracked" is read from ``.git/index`` instead of shelling out to git
------------------------------------------------------------------------
The obvious implementation is ``git ls-files``. It is not used here on purpose:
process spawning anywhere in the scanned tree is banned outright by the companion
guard ``tests/infra/test_no_production_spawn_guard.py`` (rule R5 — a size-locked
allowlist covering *every* process-spawning call site, so an unlisted one is red by
construction). Adding a spawn site here would mean widening that lock, which this
ticket is not authorised to do.

Git's index *is* the definition of "tracked", and its on-disk format is stable and
documented (``Documentation/gitformat-index.txt``; entries are a 62-byte fixed
record plus a NUL-terminated, 8-byte-padded path). Index versions 2 and 3 are parsed
below. An unknown version fails loudly rather than silently reporting "nothing is
tracked" — a guard that cannot read its input must not pass.

Anti-vacuity (this guard must not be able to pass by scanning nothing)
---------------------------------------------------------------------
* the index reader is cross-checked against paths that must be tracked;
* the disk walk has a floor, so a broken walk (which yields ~0 files) fails loudly;
* ``conftest.py`` is asserted *not* to be classified as collectable;
* the classifier is fed synthetic known-bad and known-good inputs, and the real
  index reader + real disk scan are exercised against an injected synthetic
  candidate — so neutering the rule turns this module red instead of quietly green.
"""
from __future__ import annotations

import fnmatch
import struct
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Sequence, Set

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"
GIT_INDEX_PATH = REPO_ROOT / ".git" / "index"

#: pytest's default ``python_files`` (pytest docs, "Conventions for Python test
#: discovery"). A file matching **either** pattern is collected by a bare ``pytest``.
#: Kept in sync with the companion guard by
#: ``test_pattern_list_matches_companion_guard`` below.
PYTEST_DEFAULT_PYTHON_FILES: Sequence[str] = ("test_*.py", "*_test.py")

#: Anti-vacuity floor for the disk walk — a broken walk yields ~0, never ~250. This
#: is a "the scan walked nothing" tripwire, NOT a lock on the file count: ordinary
#: additions/removals of test files stay well above it.
MIN_COLLECTABLE_TEST_FILES = 200

#: Synthetic candidate used by the end-to-end teeth check. It is never written to
#: disk — it exists only inside the in-memory candidate list.
INJECTED_PROBE_PATH = "tests/infra/test_zz_injected_probe.py"

#: Well-known paths that must be in the index; guards against a silently empty parse.
KNOWN_TRACKED_PATHS: Sequence[str] = (
    "pytest.ini",
    "tests/conftest.py",
    "tests/infra/test_no_production_spawn_guard.py",
)

_INDEX_MAGIC = b"DIRC"
_INDEX_HEADER = struct.Struct(">4sLL")
_INDEX_ENTRY_FIXED = 62
_INDEX_FLAG_NAME_MASK = 0x0FFF
_INDEX_FLAG_EXTENDED = 0x4000
_INDEX_LONG_PATH_SENTINEL = 0x0FFF


def matches_pytest_default_pattern(filename: str) -> bool:
    """True when *filename* (a basename) matches pytest's default collection set."""
    return any(
        fnmatch.fnmatchcase(filename, pattern)
        for pattern in PYTEST_DEFAULT_PYTHON_FILES
    )


def read_tracked_paths(index_path: Path = GIT_INDEX_PATH) -> Set[str]:
    """Return the repo-relative posix paths recorded in the git index.

    The index is the set of files git considers tracked (staged or committed), so
    this is the same inventory ``git ls-files`` would print — without spawning a
    process. Supports index versions 2 and 3; any other version raises.
    """
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
            f"unsupported git index version {version!r}; this guard parses v2/v3. "
            "Re-read gitformat-index.txt and extend read_tracked_paths() rather "
            "than letting the guard report an empty tracked set."
        )

    tracked: Set[str] = set()
    offset = _INDEX_HEADER.size
    for _ in range(entry_count):
        if offset + _INDEX_ENTRY_FIXED > len(data):
            raise AssertionError(
                f"{index_path} ended early after {len(tracked)} of {entry_count} entries"
            )
        flags = struct.unpack_from(">H", data, offset + 60)[0]
        name_len = flags & _INDEX_FLAG_NAME_MASK
        cursor = offset + _INDEX_ENTRY_FIXED
        if version >= 3 and flags & _INDEX_FLAG_EXTENDED:
            cursor += 2  # v3 optional extended flags
        fixed_len = cursor - offset
        if name_len < _INDEX_LONG_PATH_SENTINEL:
            raw = data[cursor:cursor + name_len]
            total = fixed_len + name_len + 1
        else:  # 0xFFF means "name longer than the mask; read to the NUL"
            end = data.index(b"\0", cursor)
            raw = data[cursor:end]
            total = (end - offset) + 1
        tracked.add(raw.decode("utf-8", errors="surrogateescape"))
        offset += (total + 7) // 8 * 8  # entries are 8-byte padded

    if len(tracked) != entry_count:
        raise AssertionError(
            f"index parse produced {len(tracked)} paths for {entry_count} entries"
        )
    return tracked


def collectable_test_files(
    tests_dir: Path = TESTS_DIR, repo_root: Path = REPO_ROOT
) -> List[str]:
    """Repo-relative paths of every ``tests/**`` file pytest would collect."""
    found: List[str] = []
    for path in sorted(tests_dir.rglob("*.py")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if matches_pytest_default_pattern(path.name):
            found.append(path.relative_to(repo_root).as_posix())
    return found


def untracked_collectable_tests(
    tracked: Iterable[str], candidates: Iterable[str]
) -> List[str]:
    """The rule: candidates that pytest would collect but git does not track.

    Pure function — no filesystem, no index, no git — so it can be exercised
    against synthetic inventories in the self-checks below. It re-applies the
    collectable-name filter itself (rather than trusting the caller) so a caller
    that passes an unfiltered list still cannot get a non-collectable path
    reported.
    """
    tracked_set = set(tracked)
    return sorted(
        path
        for path in candidates
        if matches_pytest_default_pattern(PurePosixPath(path).name)
        and path not in tracked_set
    )


# ───────────────────────────────────────────────────────────
# Anti-vacuity: the reader, the walk and the rule all really work
# ───────────────────────────────────────────────────────────


def test_git_index_reader_sees_known_tracked_paths():
    """The index parse must find the repo, not silently return an empty set."""
    tracked = read_tracked_paths()
    assert len(tracked) > 100, f"index parse yielded only {len(tracked)} paths"
    missing = [p for p in KNOWN_TRACKED_PATHS if p not in tracked]
    assert not missing, f"index parse is incomplete; missing known-tracked: {missing}"


def test_pytest_pattern_classifier_follows_pytest_conventions():
    """Both default patterns match; non-collectable names do not."""
    assert matches_pytest_default_pattern("test_alpha.py")
    assert matches_pytest_default_pattern("alpha_test.py")
    for not_collected in (
        "conftest.py",
        "verify_miku_2_22.py",
        "manual_disable_thinking.py",
        "alpha.py",
        "test_alpha.txt",
    ):
        assert not matches_pytest_default_pattern(not_collected), (
            f"{not_collected!r} must NOT be classified as collectable"
        )


def test_pattern_list_matches_companion_guard():
    """Both guards must agree on what "collectable" means."""
    from tests.infra.test_no_production_spawn_guard import (
        PYTEST_DEFAULT_PYTHON_FILES as companion_patterns,
    )

    assert tuple(companion_patterns) == tuple(PYTEST_DEFAULT_PYTHON_FILES), (
        "the two guards disagree on pytest's default python_files: "
        f"{companion_patterns!r} != {tuple(PYTEST_DEFAULT_PYTHON_FILES)!r}"
    )


def test_collectable_walk_is_not_vacuous():
    """A broken walk yields ~0 files; that must fail, not pass."""
    files = collectable_test_files()
    assert len(files) >= MIN_COLLECTABLE_TEST_FILES, (
        f"the walk found only {len(files)} collectable test files "
        f"(< {MIN_COLLECTABLE_TEST_FILES}); the scanner is broken, not the repo"
    )
    assert "tests/conftest.py" not in files, (
        "conftest.py is a pytest plugin, not a collected test module"
    )


# ───────────────────────────────────────────────────────────
# Teeth: synthetic inputs prove the rule still bites
# ───────────────────────────────────────────────────────────


def test_rule_flags_a_synthetic_untracked_collectable_file():
    """Known-bad: an untracked collectable path must be reported."""
    tracked = ["tests/test_alpha.py", "tests/infra/test_beta.py"]
    candidates = [
        "tests/test_alpha.py",
        "tests/infra/test_beta.py",
        "tests/test_untracked_probe.py",
    ]
    assert untracked_collectable_tests(tracked, candidates) == [
        "tests/test_untracked_probe.py"
    ]


def test_rule_stays_silent_on_a_fully_tracked_inventory():
    """Known-good: a complete inventory must yield no offenders (no false alarm)."""
    tracked = ["tests/test_alpha.py", "tests/infra/test_beta.py"]
    assert untracked_collectable_tests(tracked, tracked) == []
    # an untracked *non*-collectable path is not this guard's business
    assert untracked_collectable_tests(
        tracked, tracked + ["tests/verify_miku_2_22.py", "tests/conftest.py"]
    ) == []


def test_real_pipeline_flags_an_injected_synthetic_candidate():
    """End-to-end teeth: real index reader + real walk + real rule.

    No probe file is created inside the repo — the synthetic candidate exists only
    in the in-memory list, so this cannot leave the tree dirty. The comparison is
    against the un-injected run, so the assertion holds whether or not this very
    file is already staged.
    """
    tracked = read_tracked_paths()
    candidates = collectable_test_files()
    before = set(untracked_collectable_tests(tracked, candidates))
    after = set(
        untracked_collectable_tests(tracked, candidates + [INJECTED_PROBE_PATH])
    )
    assert after - before == {INJECTED_PROBE_PATH}
    assert INJECTED_PROBE_PATH in after


# ───────────────────────────────────────────────────────────
# The rule itself
# ───────────────────────────────────────────────────────────


def test_every_collectable_test_file_under_tests_is_git_tracked():
    """``tests/**`` collectable ``.py`` files must all be tracked. No allowlist."""
    offenders = untracked_collectable_tests(
        read_tracked_paths(), collectable_test_files()
    )
    assert offenders == [], (
        "untracked test file(s) that pytest WILL collect locally but that a clean "
        "clone will not have — the local collection set has diverged from the "
        "committed one.\n"
        "Fix by tracking the file (git add <path>), or by archiving it under "
        "docs/legacy/<name>.txt if it is a one-off audit rather than a regression:\n"
        + "\n".join(f"  - {path}" for path in offenders)
    )
