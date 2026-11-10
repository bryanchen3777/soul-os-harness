"""Soul/preset routing tests (S5-W3-1). Pure offline: tmp files + monkeypatched env.

No server, no ports, no network, no real keys. Every key value in this file
is an uppercase hyphenated SENTINEL that matches no real credential shape.
``socket.create_connection`` is hard-blocked by the autouse fixture below
(``socket.socket.connect`` is deliberately NOT blocked: on Windows the
asyncio event loop builds its self-pipe on it -- SIM-TIME-001 precedent).

This file never imports ``src.llm.proxy``: the five-soul evidence is read
via AST only, and the routing module itself must not import proxy either
(pinned by ``test_routing_imports_are_pinned_ast``).
"""

import ast
import hashlib
import json
import os
import socket
import sys
from pathlib import Path

import pytest

from src.external_gateway import routing
from src.external_gateway.preset import Preset
from src.external_gateway.routing import SOUL_AGENT_MAP
from src.external_gateway.routing import SOUL_WHITELIST
from src.external_gateway.routing import resolve
from src.interfaces.config import validate_registry
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import RouteBinding

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "src" / "llm" / "proxy.py"
ROUTING_PATH = REPO_ROOT / "src" / "external_gateway" / "routing.py"

SOULS = ["rem", "mai", "ruka", "akane", "mahiru"]
PRESETS = ["primary", "secondary"]

REF_A = "S5_W3_ROUTING_REF_A"
REF_B = "S5_W3_ROUTING_REF_B"
SENTINEL_A = "S5-W3-ROUTING-SENTINEL-A"
SENTINEL_B = "S5-W3-ROUTING-SENTINEL-B"

OWNER = "s5-w3-routing-owner"

_FIXED_404 = "Unknown route."
_FIXED_500 = "Invalid gateway route configuration."


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("S5-W3-1: tests must not dial out (socket.create_connection blocked)")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


def _preset_entry(name, key_ref):
    return {
        "name": name,
        "provider": "deepseek",
        "model": "do-not-deploy-model",
        "params": {"temperature": 0.5, "max_tokens": 512},
        "key_ref": key_ref,
    }


def _write_preset_file(path, entries):
    path.write_text(json.dumps({"presets": entries}), encoding="utf-8")
    return path


def _route(soul, backend, agent=None):
    return {
        "soul_alias": soul,
        "backend_alias": backend,
        "agent_id": agent if agent is not None else "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, backend),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _make_registry(routes):
    allowed = ["%s/%s" % (r["soul_alias"], r["backend_alias"]) for r in routes]
    digest = hashlib.sha256(b"S5-W3-ROUTING-DUMMY-TOKEN").hexdigest()
    clients = [
        {
            "client_id": "client-s5-w3",
            "principal_id": OWNER,
            "purpose": "interactive",
            "allowed_routes": allowed,
            "scope": "owner_private",
            "allow_persistent_memory": True,
            "credential_sha256": digest,
        }
    ]
    return validate_registry(
        {"owner_principal_id": OWNER, "backends": ["port-a"], "routes": routes, "clients": clients}
    )


def _matrix_registry(extra_routes=()):
    routes = [_route(s, p) for s in SOULS for p in PRESETS] + list(extra_routes)
    return _make_registry(routes)


def _two_preset_file(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_A, SENTINEL_A)
    monkeypatch.setenv(REF_B, SENTINEL_B)
    return _write_preset_file(
        tmp_path / "presets.json",
        [_preset_entry("primary", REF_A), _preset_entry("secondary", REF_B)],
    )


def _module_literals(path, var):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == var for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(var + " not found in " + str(path))


# -- whitelist pin -----------------------------------------------------------

def test_whitelist_is_exactly_five():
    # Widening the whitelist (mutation direction 1) must turn this red.
    assert SOUL_WHITELIST == frozenset({"rem", "mai", "ruka", "akane", "mahiru"})


def test_soul_agent_map_is_pinned():
    assert dict(SOUL_AGENT_MAP) == {
        "rem": "agent_rem",
        "mai": "agent_mai",
        "ruka": "agent_ruka",
        "akane": "agent_akane",
        "mahiru": "agent_mahiru",
    }


def test_proxy_short_names_cover_five_souls_ast():
    """AST evidence: proxy short names give the same five agent_<slug> pairs.

    Sample-level evidence only (proxy table + registry route samples), NOT a
    deployment confirmation -- production wiring is owned by S6/S7.
    """
    before = set(sys.modules)
    short = _module_literals(PROXY_PATH, "AGENT_SHORT_NAMES")
    assert "src.llm.proxy" not in set(sys.modules) - before, (
        "must read proxy via AST, never import it"
    )
    for soul in SOULS:
        assert short.get("agent_" + soul) == soul, soul
    inverted = {"agent_" + soul: soul for soul in SOULS}
    assert {soul: SOUL_AGENT_MAP[soul] for soul in SOULS} == {
        slug: agent for agent, slug in inverted.items()
    }


# -- import guard: no network, no server, no proxy -----------------------------

def test_routing_imports_are_pinned_ast():
    tree = ast.parse(ROUTING_PATH.read_text(encoding="utf-8"))
    top, full = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full.add(alias.name)
                top.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            full.add(node.module or "")
            top.add((node.module or "").split(".")[0])
    assert top == {"__future__", "collections", "json", "src", "types"}, top
    banned = ("proxy", "socket", "urllib", "http", "httpx", "requests", "websocket",
              "aiohttp", "uvicorn", "fastapi", "ssl", "server")
    for mod in full:
        for word in banned:
            assert word not in mod, mod


# -- success matrix: five souls x two presets ---------------------------------

@pytest.mark.parametrize("soul", SOULS)
@pytest.mark.parametrize("preset", PRESETS)
def test_success_matrix_5x2(tmp_path, monkeypatch, soul, preset):
    # Dropping a legal soul from the whitelist (mutation direction 2) must
    # turn its two cases red.
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _matrix_registry()
    route, preset_obj = resolve(registry, preset_path, soul, preset)
    assert isinstance(route, RouteBinding)
    assert isinstance(preset_obj, Preset)
    assert (route.soul_alias, route.backend_alias) == (soul, preset)
    assert route.agent_id == "agent_" + soul
    assert preset_obj.name == preset
    assert preset_obj.provider == "deepseek"
    assert preset_obj.api_key == (SENTINEL_A if preset == "primary" else SENTINEL_B)


# -- 404: unknown soul / missing route / unknown preset ------------------------

@pytest.mark.parametrize("soul", ["ram", "yua", "REM", "rem ", "", None, 123])
def test_unknown_soul_404(tmp_path, monkeypatch, soul):
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _matrix_registry()
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path, soul, "primary")
    assert ei.value.status == 404
    assert ei.value.code == "route_not_found"
    assert ei.value.message == _FIXED_404


def test_missing_route_404(tmp_path, monkeypatch):
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _matrix_registry()
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path, "rem", "no-such-preset")
    assert (ei.value.status, ei.value.code) == (404, "route_not_found")


def test_unknown_preset_in_valid_config_404(tmp_path, monkeypatch):
    # The route exists in the registry, but the well-formed preset file has
    # no such entry: still 404, not 500.
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _matrix_registry(extra_routes=[_route("rem", "ghost")])
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path, "rem", "ghost")
    assert (ei.value.status, ei.value.code) == (404, "route_not_found")
    assert ei.value.message == _FIXED_404


def test_illegal_routes_read_no_env(tmp_path, monkeypatch):
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _matrix_registry(extra_routes=[_route("rem", "ghost")])
    calls = []
    real_get = os.environ.get

    def _spy(key, default=None):
        calls.append(key)
        return real_get(key, default)

    monkeypatch.setattr(os.environ, "get", _spy)
    for soul, preset in [("ram", "primary"), ("rem", "no-such-preset"), ("rem", "ghost")]:
        with pytest.raises(InterfaceError) as ei:
            resolve(registry, preset_path, soul, preset)
        assert ei.value.status == 404
    assert calls == [], calls


# -- 500: wrong mapping / bad config / missing key -----------------------------

def test_mapping_mismatch_500(tmp_path, monkeypatch):
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    registry = _make_registry([_route("rem", "primary", agent="agent_mai")])
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path, "rem", "primary")
    assert ei.value.status == 500
    assert ei.value.code == "invalid_route"
    assert ei.value.message == _FIXED_500
    text = str(ei.value) + repr(ei.value)
    assert SENTINEL_A not in text and SENTINEL_B not in text


def test_bad_preset_file_500(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_A, SENTINEL_A)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    registry = _make_registry([_route("rem", "primary")])
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path=bad, soul="rem", preset="primary")
    assert ei.value.status == 500
    assert ei.value.code == "invalid_preset"
    assert SENTINEL_A not in str(ei.value)


def test_missing_key_500(tmp_path, monkeypatch):
    preset_path = _two_preset_file(tmp_path, monkeypatch)
    monkeypatch.delenv(REF_A, raising=False)
    monkeypatch.delenv(REF_B, raising=False)
    registry = _matrix_registry()
    with pytest.raises(InterfaceError) as ei:
        resolve(registry, preset_path, "rem", "primary")
    assert ei.value.status == 500
    assert ei.value.code == "invalid_preset"
    assert SENTINEL_A not in str(ei.value) and SENTINEL_B not in str(ei.value)
