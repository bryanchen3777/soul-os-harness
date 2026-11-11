"""SA-0 T0 isolation conftest: independent hard blocks (no network, no prod writes).

Scope: applies ONLY to tests/soul_access/ (this directory). Pure stdlib.
Blocks, per test (autouse, monkeypatch-scoped, auto-undone after each test):
  1. Network: socket.socket / create_connection / create_server raise.
  2. Server spawn: subprocess.Popen with a server launcher argv raises.
  3. Production writes: any write/open-append/remove under repo data/** raises.
  4. Process kill: os.kill raises (no agent may kill processes, ever).

Legit tmp writes (tmp_path / pytest cache) keep working: the guard only
refuses paths resolving under <repo>/data/.
"""
from __future__ import annotations

import builtins
import os
import pathlib
import socket
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_DATA = (_REPO_ROOT / "data").resolve()

_REAL_OPEN = builtins.open


def _is_under_prod_data(path_like) -> bool:
    try:
        p = Path(path_like).expanduser()
        if not p.is_absolute():
            p = (_REPO_ROOT / p)
        # resolve(strict=False): never touches disk, only normalises.
        rp = p.resolve(strict=False)
        try:
            rp.relative_to(_PROD_DATA)
            return True
        except ValueError:
            return False
    except Exception:
        return False


def _blocked_net(*args, **kwargs):
    raise RuntimeError("SA-0 T0: network is hard-blocked in tests/soul_access")


def _blocked_kill(*args, **kwargs):
    raise RuntimeError("SA-0 T0: os.kill is hard-blocked (never kill processes)")


def _guarded_open(file, mode="r", *args, **kwargs):
    if isinstance(mode, str) and any(ch in mode for ch in ("w", "a", "+")):
        if _is_under_prod_data(file):
            raise RuntimeError("SA-0 T0: writes under data/** are hard-blocked")
    return _REAL_OPEN(file, mode, *args, **kwargs)


def _guarded_path_open(self, mode="r", *args, **kwargs):
    if isinstance(mode, str) and any(ch in mode for ch in ("w", "a", "x", "+")):
        if _is_under_prod_data(self):
            raise RuntimeError("SA-0 T0: writes under data/** are hard-blocked")
    return self.__class__.__open_original__(self, mode, *args, **kwargs)


def _guarded_write_text(self, *args, **kwargs):
    if _is_under_prod_data(self):
        raise RuntimeError("SA-0 T0: writes under data/** are hard-blocked")
    return self.__class__.__write_text_original__(self, *args, **kwargs)


def _guarded_write_bytes(self, *args, **kwargs):
    if _is_under_prod_data(self):
        raise RuntimeError("SA-0 T0: writes under data/** are hard-blocked")
    return self.__class__.__write_bytes_original__(self, *args, **kwargs)


def _guarded_unlink(self, *args, **kwargs):
    if _is_under_prod_data(self):
        raise RuntimeError("SA-0 T0: removes under data/** are hard-blocked")
    return self.__class__.__unlink_original__(self, *args, **kwargs)


_REAL_POPEN = subprocess.Popen
_SERVER_MARKERS = ("run_server", "serve_external_gateway", "uvicorn", ":8000", ":8765", ":8766", ":8767")


def _guarded_popen(args, *pargs, **kwargs):
    try:
        cmd = " ".join(str(c) for c in (args if isinstance(args, (list, tuple)) else [args]))
    except Exception:
        cmd = ""
    if any(m in cmd for m in _SERVER_MARKERS):
        raise RuntimeError("SA-0 T0: spawning a server is hard-blocked")
    return _REAL_POPEN(args, *pargs, **kwargs)


@pytest.fixture(autouse=True)
def _sa0_t0_hard_blocks(monkeypatch):
    """Install SA-0 T0 hard blocks for every test in this directory."""
    # 1. Network.
    monkeypatch.setattr(socket, "socket", _blocked_net)
    monkeypatch.setattr(socket, "create_connection", _blocked_net)
    monkeypatch.setattr(socket, "create_server", _blocked_net)
    # 2. Server spawn.
    monkeypatch.setattr(subprocess, "Popen", _guarded_popen)
    # 3. Production writes: builtins.open + pathlib writers.
    monkeypatch.setattr(builtins, "open", _guarded_open)
    if not hasattr(pathlib.Path, "__open_original__"):
        pathlib.Path.__open_original__ = pathlib.Path.open
        pathlib.Path.__write_text_original__ = pathlib.Path.write_text
        pathlib.Path.__write_bytes_original__ = pathlib.Path.write_bytes
        pathlib.Path.__unlink_original__ = pathlib.Path.unlink
    monkeypatch.setattr(pathlib.Path, "open", _guarded_path_open)
    monkeypatch.setattr(pathlib.Path, "write_text", _guarded_write_text)
    monkeypatch.setattr(pathlib.Path, "write_bytes", _guarded_write_bytes)
    monkeypatch.setattr(pathlib.Path, "unlink", _guarded_unlink)
    # 4. Process kill.
    monkeypatch.setattr(os, "kill", _blocked_kill)
    yield
