"""SA-T18-DOTENV-BOUNDARY-1: T18's B3 load isolation boundary pins.

Root cause it locks (GATE-2 full-suite ``1 failed``):
``tests/soul_access/test_t18_exit7_coverage.py`` snapshotted
``sys.modules.get("dotenv")`` ONCE at import time (stale ``None``) and
re-asserted identity at test time. Mid-session, the life-thread pipeline's
legitimate lazy import (``src/soul/life_threads.py:393``
``from configs.loader import load_config`` → ``configs/loader.py:30``
``from dotenv import load_dotenv``) populates the entry, so the fossil
snapshot comparison went red. The fix scopes snapshot/stub/load/restore
into ``_stub_dotenv_for_b3`` + ``_load_b3`` (same shape as T16's
``_stub_dotenv_for_b2``); this module proves the boundary from the outside.

What this module checks (no subprocess, no network, no ports, no writes)
------------------------------------------------------------------------
* Four quadrants — prior entry {absent, present} x load outcome
  {success, exception}: driving the SUBJECT's own ``_stub_dotenv_for_b3``
  + ``_load_b3`` restores ``sys.modules`` byte-identically (identity when
  present, key-absence when absent) and leaves ``os.environ`` untouched.
* Zero real reads: the real ``dotenv`` loader (spied when present,
  still-absent when not) is never invoked by any guarded load.
* ``main`` never called: the loaded B3 exposes ``main`` (present) but the
  load path never invokes it — stub-hit shape ``[((), {})]`` per load plus
  an AST pin (zero ``*.main(`` call sites in the subject file).
* Two bidirectional mutations, each MUST show red:
  A. stub removed → B3's import reaches the REAL loader (observed red);
  B. restore removed → the stub leaks and the subject's own no-leak
  invariant demonstrably fails (observed red).

Read-only wrt product and wrt the subject file: the subject is imported by
path under a distinct module name (never pytest-collected); artifacts stay
in per-test ``tmp_path`` (unused — nothing is written at all).
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
T18_PATH = (
    REPO_ROOT
    / "tests"
    / "soul_access"
    / "test_t18_exit7_coverage.py"
)
LIVE_B3_PATH = (
    REPO_ROOT
    / "verification"
    / "sa-open-fire-1"
    / "live-fire-3"
    / "live_fire_b3.py"
)


def _load_t18_subject():
    """Import the T18 test module by path WITHOUT pytest collection.

    A distinct module name keeps its ``_DOTENV_CALLS`` list isolated
    from any pytest-collected copy. Importing it exec's B3 once through
    the subject's own scoped stub (its load-time strict pins run here),
    touches no network, writes nothing, never calls ``main``.
    """
    assert T18_PATH.is_file(), f"T18 subject missing: {T18_PATH}"
    assert LIVE_B3_PATH.is_file(), f"B3 script missing: {LIVE_B3_PATH}"
    spec = importlib.util.spec_from_file_location(
        "t18_dotenv_boundary_subject", T18_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _spy_real_dotenv():
    """Count REAL dotenv loader calls WITHOUT ever reading a real .env.

    Yields a zero-arg ``real_reads()`` counter. The spy records the call
    and returns ``False`` — it never delegates, so no file is read and no
    value is injected however the caller behaves. When no real dotenv is
    imported, the counter trivially stays 0 and absence is asserted by
    the caller.
    """
    real = sys.modules.get("dotenv")
    if real is None or type(real).__name__ == "_NoopDotenv":
        yield lambda: 0
        return
    calls: list = []

    def _recording(*args, **kwargs):
        calls.append((args, kwargs))
        return False

    original = real.load_dotenv
    real.load_dotenv = _recording
    try:
        yield lambda: len(calls)
    finally:
        real.load_dotenv = original


def _snapshot_env():
    return dict(os.environ)


class _PresentSentinel(ModuleType):
    """Distinct pre-existing entry for the present-state quadrants."""

    def load_dotenv(self, *args, **kwargs):  # noqa: N802
        raise AssertionError("present-sentinel must never be invoked")


def _drive_success_quadrant(mod, prior):
    """One guarded load; returns (loaded, stub_delta, dotenv_before)."""
    dotenv_before = sys.modules.get("dotenv")
    env_before = _snapshot_env()
    calls_before = len(mod._DOTENV_CALLS)
    with _spy_real_dotenv() as real_reads:
        loaded = mod._load_b3()
        assert callable(loaded.main)  # present but NEVER invoked by load
        assert callable(loaded._run)  # original pump intact on fresh load
    # Exact restore: identity when present, key-absence when absent.
    if prior == "absent":
        assert "dotenv" not in sys.modules
    else:
        assert sys.modules.get("dotenv") is dotenv_before
    # No environment injection through the guarded load.
    assert _snapshot_env() == env_before
    # The B3 import-time load_dotenv() hit the stub exactly once, no args.
    assert len(mod._DOTENV_CALLS) == calls_before + 1
    assert mod._DOTENV_CALLS[-1] == ((), {})
    # The real loader never ran.
    assert real_reads() == 0
    return loaded


def test_absent_success_restores_absence_and_reads_nothing():
    """Quadrant 1/4: prior absent + load succeeds ⇒ key stays absent."""
    mod = _load_t18_subject()
    _ABSENT = object()
    saved = sys.modules.pop("dotenv", _ABSENT)
    try:
        assert "dotenv" not in sys.modules
        _drive_success_quadrant(mod, "absent")
    finally:
        sys.modules.pop("dotenv", None)
        if saved is not _ABSENT:
            sys.modules["dotenv"] = saved


def test_present_success_restores_identity_and_reads_nothing():
    """Quadrant 2/4: prior present + load succeeds ⇒ identity restored."""
    mod = _load_t18_subject()
    sentinel = _PresentSentinel("dotenv")
    saved = sys.modules.get("dotenv", None)
    had = "dotenv" in sys.modules
    sys.modules["dotenv"] = sentinel
    try:
        _drive_success_quadrant(mod, "present")
        assert sys.modules.get("dotenv") is sentinel
    finally:
        if had:
            sys.modules["dotenv"] = saved
        else:
            sys.modules.pop("dotenv", None)


def test_absent_exception_restores_absence():
    """Quadrant 3/4: prior absent + boom inside region ⇒ key stays absent."""
    mod = _load_t18_subject()
    _ABSENT = object()
    saved = sys.modules.pop("dotenv", _ABSENT)
    env_before = _snapshot_env()
    try:
        assert "dotenv" not in sys.modules
        with _spy_real_dotenv() as real_reads:
            try:
                with mod._stub_dotenv_for_b3():
                    assert sys.modules.get("dotenv") is mod._T18_STUB
                    loaded = mod._load_b3()
                    assert callable(loaded.main)
                    raise RuntimeError("t18-synthetic-boom")
            except RuntimeError as exc:
                assert "t18-synthetic-boom" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("boom did not propagate")
        assert "dotenv" not in sys.modules
        assert _snapshot_env() == env_before
        assert real_reads() == 0
    finally:
        sys.modules.pop("dotenv", None)
        if saved is not _ABSENT:
            sys.modules["dotenv"] = saved


def test_present_exception_restores_identity():
    """Quadrant 4/4: prior present + boom inside region ⇒ identity back."""
    mod = _load_t18_subject()
    sentinel = _PresentSentinel("dotenv")
    saved = sys.modules.get("dotenv", None)
    had = "dotenv" in sys.modules
    sys.modules["dotenv"] = sentinel
    env_before = _snapshot_env()
    try:
        with _spy_real_dotenv() as real_reads:
            try:
                with mod._stub_dotenv_for_b3():
                    assert sys.modules.get("dotenv") is mod._T18_STUB
                    raise RuntimeError("t18-synthetic-boom")
            except RuntimeError as exc:
                assert "t18-synthetic-boom" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("boom did not propagate")
        assert sys.modules.get("dotenv") is sentinel
        assert _snapshot_env() == env_before
        assert real_reads() == 0
    finally:
        if had:
            sys.modules["dotenv"] = saved
        else:
            sys.modules.pop("dotenv", None)


def test_b3_load_never_calls_main():
    """``main`` is present on every fresh load but never invoked by it."""
    mod = _load_t18_subject()
    with _spy_real_dotenv() as real_reads:
        loaded = mod._load_b3()
    assert callable(loaded.main)
    # One stub hit per load: the import-time load_dotenv() only. A main()
    # invocation during load would either add calls or live-fire (and fail
    # offline); the shape pin below plus the AST pin make that structural.
    assert mod._DOTENV_CALLS[-1] == ((), {})
    assert real_reads() == 0
    # AST pin: the subject file contains zero *.main( call sites —
    # adding one turns this red instead of silently live-firing.
    tree = ast.parse(T18_PATH.read_text(encoding="utf-8"), filename=str(T18_PATH))
    main_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "main"
    ]
    assert main_calls == [], "subject must never call *.main()"


def test_mutation_stub_removed_reaches_real_loader():
    """Mutation A (stub removed) MUST show red: B3 hits the REAL loader.

    B3's module-level ``load_dotenv()`` is exec'd with NO stub while the
    real loader is spied (recording only — zero file reads, zero env
    injection). The spy MUST observe exactly one no-arg call: without the
    stub, the import reaches the real loader, i.e. the strict pins would
    go red. Session state is restored afterwards.
    """
    saved = sys.modules.get("dotenv", None)
    had_original = "dotenv" in sys.modules
    try:
        if not had_original or type(saved).__name__ == "_NoopDotenv":
            # A leaked stub would mask the mutation; drop it first so the
            # exec below genuinely faces the real loader. (No in-repo
            # loader leaves its stub behind; this is belt-and-braces.)
            sys.modules.pop("dotenv", None)
            import dotenv as _real  # noqa: F401  (module import: zero .env reads)

            saved, had_original = sys.modules.get("dotenv"), True
            introduced = True
        else:
            introduced = False
        with _spy_real_dotenv() as real_reads:
            spec = importlib.util.spec_from_file_location(
                "live_fire_b3_t18_mut_nostub", LIVE_B3_PATH
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        assert real_reads() == 1, (
            "mutation A must reach the real loader exactly once — "
            "if this is 0, B3 no longer self-loads and the pins are stale"
        )
    finally:
        sys.modules.pop("dotenv", None)
        if had_original and not introduced:
            sys.modules["dotenv"] = saved
        # else: restore original absence — leave no fresh import behind.


def test_mutation_restore_removed_leaks_stub_and_trips_noleak_pin():
    """Mutation B (restore removed) MUST show red: stub leaks, pin trips.

    An inline copy of the stub region WITHOUT the ``finally`` restore is
    driven through a real load. Afterwards the stub MUST be observed
    leaking in ``sys.modules`` — and the subject's own no-leak invariant
    MUST fail on that state (``pytest.raises`` proves the red). Session
    state is restored afterwards.
    """
    import pytest

    mod = _load_t18_subject()
    dotenv_before = sys.modules.get("dotenv", None)
    had = "dotenv" in sys.modules

    @contextmanager
    def _stub_without_restore():
        sys.modules["dotenv"] = mod._T18_STUB
        yield  # NOTE: no try/finally restore — the mutation under test

    try:
        with _stub_without_restore():
            loaded = mod._load_b3()
            assert callable(loaded.main)
        # RED state observed: the stub leaked into the live session.
        assert sys.modules.get("dotenv") is mod._T18_STUB
        # ... and the subject's no-leak invariant demonstrably trips on it.
        with pytest.raises(AssertionError):
            assert all(
                m is not mod._T18_STUB for m in list(sys.modules.values())
            )
    finally:
        sys.modules.pop("dotenv", None)
        if had:
            sys.modules["dotenv"] = dotenv_before


def test_t18_load_b3_routes_through_the_dotenv_stub():
    """Mechanism pin: ``_load_b3`` must exec B3 inside the stub region."""
    tree = ast.parse(T18_PATH.read_text(encoding="utf-8"), filename=str(T18_PATH))
    loader = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_b3":
            loader = node
            break
    assert loader is not None, "T18 no longer defines _load_b3"
    segment = ast.get_source_segment(
        T18_PATH.read_text(encoding="utf-8"), loader
    )
    assert segment is not None and "_stub_dotenv_for_b3" in segment, (
        "T18 _load_b3 bypasses the dotenv stub region — "
        "the stale-snapshot leak is re-opened"
    )
