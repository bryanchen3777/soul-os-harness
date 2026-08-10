# src/paths.py
# Soul OS — Canonical runtime data-root resolver
"""
P0.5 (Bry 派工 2026-08-09 19:48): Canonical data-root for runtime persistence.

Provides a single `data_root()` accessor that resolves the runtime data directory
based on the `SOUL_OS_DATA_DIR` environment variable.

Production default (env var unset):
    data_root() == Path("data").resolve()

Test subprocess (env var set to temp dir):
    data_root() == Path(SOUL_OS_DATA_DIR).resolve()

Every module that writes runtime state should use `data_root() / <sub-path>`
instead of the hardcoded `Path("data/...")` literal.

Frozen contract: this helper does NOT change production behavior when
SOUL_OS_DATA_DIR is unset. All existing production paths continue to work.
"""
from __future__ import annotations

import os
from pathlib import Path

_DATA_ROOT: Path | None = None


def data_root() -> Path:
    """Resolve the runtime data root.

    Reads SOUL_OS_DATA_DIR from environment. If unset, defaults to "data"
    (relative to cwd). Creates the directory on first call if it doesn't exist.

    Cached after first call (subprocess-lifetime singleton).

    Returns:
        Absolute Path to the data root.
    """
    global _DATA_ROOT
    if _DATA_ROOT is None:
        env = os.environ.get("SOUL_OS_DATA_DIR")
        if env:
            _DATA_ROOT = Path(env).resolve()
        else:
            _DATA_ROOT = Path("data").resolve()
        _DATA_ROOT.mkdir(parents=True, exist_ok=True)
    return _DATA_ROOT


def reset_data_root() -> None:
    """Reset the cached data root.

    Intended for test setup/teardown if a single process needs to re-resolve
    after changing SOUL_OS_DATA_DIR at runtime. Not normally needed in production.
    """
    global _DATA_ROOT
    _DATA_ROOT = None
