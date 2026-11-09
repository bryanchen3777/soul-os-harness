"""Boundary import guard: short-context hard IO block + AST stdlib/package-only check."""

import ast
import builtins
import importlib
import io
import os
import pathlib
import socket
import sqlite3
import subprocess
import sys


_PKG_FILES = ("src/interfaces/__init__.py", "src/interfaces/schema.py", "src/interfaces/config.py", "src/interfaces/classification.py", "src/interfaces/identity.py", "src/interfaces/context_policy.py", "src/interfaces/journal.py", "src/interfaces/runtime_port.py")
_PKG_ALLOW = ("src/interfaces/__init__.py", "src/interfaces/schema.py", "src/interfaces/config.py", "src/interfaces/identity.py", "src/interfaces/classification.py", "src/interfaces/context_policy.py", "src/interfaces/journal.py", "src/interfaces/runtime_port.py", "src/__init__.py")
_REL_ALLOW = ("schema", "config", "identity", "classification", "context_policy", "journal", "runtime_port")


def _is_read_mode(mode) -> bool:
    if mode is None:
        return True
    if not isinstance(mode, str):
        return False
    return "w" not in mode and "a" not in mode and "x" not in mode and "+" not in mode


def _allow_read_path(p) -> bool:
    try:
        s = str(p)
    except Exception:
        return False
    s = s.replace("\\", "/")
    if "__pycache__" in s and s.endswith(".pyc") and "src/interfaces" in s:
        return True
    return s in _PKG_ALLOW or s.endswith("/src/interfaces/__init__.py") or s.endswith("/src/__init__.py")


def test_import_has_no_runtime_io_and_ast_clean(monkeypatch):
    import collections.abc  # noqa: F401
    import dataclasses  # noqa: F401
    import enum  # noqa: F401
    import hashlib  # noqa: F401
    import hmac  # noqa: F401
    import json  # noqa: F401
    import math  # noqa: F401
    import typing  # noqa: F401

    saved = {k: v for k, v in sys.modules.items() if k == "src.interfaces" or k.startswith("src.interfaces.")}
    for k in list(saved.keys()):
        del sys.modules[k]

    real_open = builtins.open
    real_io_open = io.open
    real_path_open = pathlib.Path.open

    def guarded_open(file, mode="r", *a, **k):
        if "mode" in k:
            mode = k.get("mode", mode)
        if not _is_read_mode(mode):
            raise AssertionError("blocked file write")
        if not _allow_read_path(file):
            raise AssertionError("blocked file IO")
        return real_open(file, mode, *a, **k)

    def guarded_io_open(file, mode="r", *a, **k):
        if "mode" in k:
            mode = k.get("mode", mode)
        if not _is_read_mode(mode):
            raise AssertionError("blocked io write")
        if not _allow_read_path(file):
            raise AssertionError("blocked io IO")
        return real_io_open(file, mode, *a, **k)

    def guarded_path_open(self, mode="r", *a, **k):
        if "mode" in k:
            mode = k.get("mode", mode)
        if not _is_read_mode(mode):
            raise AssertionError("blocked Path write")
        if not _allow_read_path(str(self)):
            raise AssertionError("blocked Path IO")
        return real_path_open(self, mode, *a, **k)

    def blocked_socket(*a, **k):
        raise AssertionError("blocked socket")

    def blocked_sqlite(*a, **k):
        raise AssertionError("blocked sqlite")

    def blocked_proc(*a, **k):
        raise AssertionError("blocked subprocess")

    def blocked_getenv(key, *a, **k):
        raise AssertionError("blocked getenv")

    class BlockedEnviron:
        def __getitem__(self, k):
            raise AssertionError("blocked environ read")

        def get(self, k, *a, **k2):
            raise AssertionError("blocked environ read")

        def __contains__(self, k):
            raise AssertionError("blocked environ read")

    try:
        with monkeypatch.context() as m:
            m.setattr(builtins, "open", guarded_open)
            m.setattr(io, "open", guarded_io_open)
            m.setattr(pathlib.Path, "open", guarded_path_open)
            m.setattr(socket, "socket", blocked_socket)
            if getattr(socket, "create_connection", None) is not None:
                m.setattr(socket, "create_connection", blocked_socket)
            m.setattr(sqlite3, "connect", blocked_sqlite)
            m.setattr(subprocess, "Popen", blocked_proc)
            m.setattr(subprocess, "run", blocked_proc)
            m.setattr(subprocess, "call", blocked_proc)
            m.setattr(os, "getenv", blocked_getenv)
            m.setattr(os, "environ", BlockedEnviron())
            importlib.import_module("src.interfaces.schema")
            importlib.import_module("src.interfaces.config")
            importlib.import_module("src.interfaces.identity")
            importlib.import_module("src.interfaces.classification")
            importlib.import_module("src.interfaces.context_policy")
    finally:
        for k in [k for k in sys.modules if k == "src.interfaces" or k.startswith("src.interfaces.")]:
            del sys.modules[k]
        sys.modules.update(saved)

    try:
        stdlib = sys.stdlib_module_names
    except AttributeError:
        stdlib = set()
    for rel in _PKG_FILES:
        with real_open(rel, "r", encoding="utf-8") as fh:
            tree = ast.parse(fh.read(), filename=rel)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    full = a.name
                    top = full.split(".")[0]
                    if top in stdlib or top == "__future__":
                        continue
                    assert full == "src.interfaces" or full.startswith("src.interfaces."), "bad import " + full
            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    assert node.level == 1, "bad relative level in " + rel
                    assert node.module in _REL_ALLOW, "bad relative import " + str(node.module) + " in " + rel
                    continue
                mod = node.module or ""
                top = mod.split(".")[0]
                if top in stdlib or top == "__future__":
                    continue
                assert mod == "src.interfaces" or mod.startswith("src.interfaces."), "bad from-import " + mod
        for stmt in tree.body:
            if isinstance(stmt, (ast.Expr, ast.Assign, ast.AnnAssign)):
                val = getattr(stmt, "value", None)
                if val is None:
                    continue
                for sub in ast.walk(val):
                    if isinstance(sub, ast.Call):
                        func = sub.func
                        name = ""
                        if isinstance(func, ast.Name):
                            name = func.id
                        elif isinstance(func, ast.Attribute):
                            name = func.attr
                        assert name in ("dataclass", "field"), "module-level call " + name + " in " + rel
