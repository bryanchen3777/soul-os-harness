"""SA-T16-ISOLATION-1 regression: T16's B2 load never touches real dotenv.

The gap this closes
-------------------
``tests/soul_access/test_t16_transport_observation.py`` exec's the formal B2
script (``verification/sa-open-fire-1/live-fire-2/live_fire_b2.py``), which
self-loads ``load_dotenv`` at import. Unstubbed, that exec imported the REAL
``dotenv`` into ``sys.modules`` and read a real ``.env`` into ``os.environ``
— process-wide pollution that turned T18's strict
``sys.modules.get("dotenv") is _SAVED_DOTENV`` assertion red whenever T16 ran
first (``1 failed`` in the T16->T18 order).

What this module checks (no subprocess, no network, no ports, no writes)
------------------------------------------------------------------------
* Success path: driving T16's own ``_stub_dotenv_for_b2`` + ``_load_b2``
  intercepts B2's ``load_dotenv()`` exactly once ``((), {})``; the real
  dotenv loader is never invoked (spied when present, still-absent when not);
  ``sys.modules`` and ``os.environ`` are byte-identical afterwards.
* Exception path: the same stub region raising mid-exec still restores
  ``sys.modules`` and ``os.environ`` exactly (yield-``finally`` teeth).
* Mechanism pin (AST): T16's ``_load_b2`` must route through
  ``_stub_dotenv_for_b2`` — deleting the ``with`` turns this red instead of
  quietly re-opening the leak.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
T16_PATH = (
    REPO_ROOT
    / "tests"
    / "soul_access"
    / "test_t16_transport_observation.py"
)
LIVE_B2_PATH = (
    REPO_ROOT
    / "verification"
    / "sa-open-fire-1"
    / "live-fire-2"
    / "live_fire_b2.py"
)


def _load_t16_subject():
    """Import the T16 test module by path WITHOUT pytest collection.

    A distinct module name keeps its ``_T16_DOTENV_CALLS`` list isolated
    from any pytest-collected copy. Importing it never exec's B2 (T16 only
    loads B2 lazily inside tests), touches no network, writes nothing.
    """
    assert T16_PATH.is_file(), f"T16 subject missing: {T16_PATH}"
    spec = importlib.util.spec_from_file_location(
        "t16_dotenv_isolation_subject", T16_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def _spy_real_dotenv():
    """Count REAL dotenv loader calls; restore the attribute afterwards.

    Yields a zero-arg ``real_reads()`` counter. When no real dotenv is
    imported, the counter trivially stays 0 and absence is asserted by
    the caller.
    """
    real = sys.modules.get("dotenv")
    if real is None or type(real).__name__ == "_NoopDotenv":
        yield lambda: 0
        return
    original = real.load_dotenv
    calls: list = []

    def _counting(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    real.load_dotenv = _counting
    try:
        yield lambda: len(calls)
    finally:
        real.load_dotenv = original


def _snapshot_env():
    return dict(os.environ)


def test_t16_b2_load_success_path_zero_real_dotenv_read_and_restores():
    """Success: stub intercepts once; real loader untouched; state restored."""
    mod = _load_t16_subject()
    dotenv_before = sys.modules.get("dotenv")
    real_was_present = dotenv_before is not None
    env_before = _snapshot_env()
    calls_before = len(mod._T16_DOTENV_CALLS)
    with _spy_real_dotenv() as real_reads:
        with mod._stub_dotenv_for_b2():
            assert sys.modules.get("dotenv") is mod._T16_STUB
            if not real_was_present:
                # The stub pre-empts the name: nothing imported the real one.
                assert "dotenv" in sys.modules  # the stub itself
            b2 = mod._load_b2.__wrapped__ if hasattr(mod._load_b2, "__wrapped__") else None
            assert b2 is None  # plain function, no wrapper hides the stub
            loaded = mod._load_b2()
            assert callable(loaded._land_transport_obs)
        # Region exited: sys.modules entry byte-identical to before.
        assert sys.modules.get("dotenv") is dotenv_before
        if not real_was_present:
            assert "dotenv" not in sys.modules
    # B2's import-time load_dotenv() hit the stub exactly once, no args.
    assert len(mod._T16_DOTENV_CALLS) == calls_before + 1
    assert mod._T16_DOTENV_CALLS[-1] == ((), {})
    # The real loader never ran, and the environment is untouched.
    assert real_reads() == 0
    assert _snapshot_env() == env_before


def test_t16_b2_stub_region_exception_path_still_restores():
    """Exception mid-region: the RuntimeError propagates AND state restores."""
    mod = _load_t16_subject()
    dotenv_before = sys.modules.get("dotenv")
    real_was_present = dotenv_before is not None
    env_before = _snapshot_env()
    boom_source = (
        "from dotenv import load_dotenv\n"
        "load_dotenv()\n"
        'raise RuntimeError("t16-synthetic-boom")\n'
    )
    with _spy_real_dotenv() as real_reads:
        try:
            with mod._stub_dotenv_for_b2():
                assert sys.modules.get("dotenv") is mod._T16_STUB
                exec(compile(boom_source, "<t16-boom-probe>", "exec"), {})
            raised = None
        except RuntimeError as exc:
            raised = exc
        assert raised is not None and "t16-synthetic-boom" in str(raised)
        assert sys.modules.get("dotenv") is dotenv_before
        if not real_was_present:
            assert "dotenv" not in sys.modules
    assert real_reads() == 0
    assert _snapshot_env() == env_before


def test_t16_load_b2_routes_through_the_dotenv_stub():
    """AST pin: ``_load_b2`` must exec B2 inside ``_stub_dotenv_for_b2``."""
    tree = ast.parse(T16_PATH.read_text(encoding="utf-8"), filename=str(T16_PATH))
    loader = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_load_b2":
            loader = node
            break
    assert loader is not None, "T16 no longer defines _load_b2"
    segment = ast.get_source_segment(
        T16_PATH.read_text(encoding="utf-8"), loader
    )
    assert segment is not None and "_stub_dotenv_for_b2" in segment, (
        "T16 _load_b2 bypasses the dotenv stub region — "
        "the T16->T18 pollution leak is re-opened"
    )
