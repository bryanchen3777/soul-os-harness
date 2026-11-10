"""S6-W4 outward HTTP assembly tests. Pure offline: tmp files + monkeypatched env.

No server, no ports, no real keys. Every key value in this file is an
uppercase hyphenated SENTINEL that matches no real credential shape.
``socket.create_connection`` is hard-blocked by the autouse fixture below
(``socket.socket.connect`` is deliberately NOT blocked: on Windows the
asyncio event loop builds its self-pipe on it -- S5 precedent).

Transport is ASGI memory only (TestClient runs the app in-process; it
never opens a port). The backend is always injected (fake or the
fail-closed default); no real provider call exists anywhere in this file.
"""

import ast
import json
import logging
import os
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.external_gateway.app import ChatBackend
from src.external_gateway.app import create_external_app
from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import file_preset_loader
from src.external_gateway.app import NotImplementedBackend
from src.external_gateway.app import parse_external_path
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.credentials import revoke_registry
from src.interfaces.external_auth import RegistryProvider
from src.interfaces.schema import InterfaceError

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_PATH = REPO_ROOT / "src" / "external_gateway" / "app.py"

OWNER = "s6-w4-app-owner"
REF_A = "S6_W4_APP_REF_A"
REF_B = "S6_W4_APP_REF_B"
KEY_A = "S6-W4-APP-SENTINEL-A"
KEY_B = "S6-W4-APP-SENTINEL-B"
BEARER_SENTINEL = "S6-W4-SCRUB-SENTINEL-TOKEN"

TOKEN_A, _DIG_A = issue_credential()
TOKEN_B, _DIG_B = issue_credential()

APP_LOGGER = "soul_os.external_gateway.app"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("S6-W4: tests must not dial out (socket.create_connection blocked)")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


class _FakeBackend:
    """Injected carrier stand-in: records its inputs, returns fixed text."""

    def __init__(self, text="hello-from-fake"):
        self.text = text
        self.calls = []

    def complete(self, route, preset, messages, params):
        self.calls.append((route, preset, messages, params))
        return self.text


def _route(soul, preset, agent=None):
    return {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": agent if agent is not None else "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, preset),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _raw(routes, client_specs):
    clients = []
    for cid, token, allowed in client_specs:
        clients.append(
            {
                "client_id": cid,
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        )
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": routes,
        "clients": clients,
    }


def _std_raw():
    routes = [_route("rem", "primary"), _route("mai", "primary"), _route("rem", "ghost")]
    return _raw(routes, [("client-a", TOKEN_A, ["rem/primary"]), ("client-b", TOKEN_B, ["mai/primary"])])


def _preset_file(path, entries):
    path.write_text(json.dumps({"presets": entries}), encoding="utf-8")
    return path


def _std_preset_file(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_A, KEY_A)
    monkeypatch.setenv(REF_B, KEY_B)
    return _preset_file(
        tmp_path / "presets.json",
        [
            {
                "name": "primary",
                "provider": "deepseek",
                "model": "do-not-deploy-model",
                "params": {"temperature": 0.5, "max_tokens": 512},
                "key_ref": REF_A,
            },
            {
                "name": "secondary",
                "provider": "deepseek",
                "model": "do-not-deploy-model",
                "params": {"temperature": 0.5, "max_tokens": 512},
                "key_ref": REF_B,
            },
        ],
    )


def _std_app(tmp_path, monkeypatch, backend=None):
    preset_path = _std_preset_file(tmp_path, monkeypatch)
    provider = RegistryProvider(_std_raw())
    return create_external_app(provider, file_preset_loader(preset_path), backend or _FakeBackend())


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _chat_body(**over):
    body = {"model": "primary", "messages": [{"role": "user", "content": "hi"}]}
    body.update(over)
    return body


def _err(resp):
    assert resp.status_code in (400, 401, 403, 404, 500)
    return resp.json()["error"]


# -- path parsing (pure + through the app) -----------------------------------

PATHS = "path-parsing"


def test_p1_parse_chat_path_ok():
    assert parse_external_path("/rem/primary/v1/chat/completions") == ("rem", "primary", "chat/completions")


def test_p2_parse_models_path_ok():
    assert parse_external_path("/mai/secondary/v1/models") == ("mai", "secondary", "models")


def test_p3_trailing_slash_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    for bad in ("/rem/primary/v1/models/", "/rem/primary/v1/chat/completions/"):
        resp = client.post(bad, headers=_auth(TOKEN_A), json=_chat_body()) if bad.endswith("completions/") else client.get(bad, headers=_auth(TOKEN_A))
        assert resp.status_code == 404
        assert _err(resp)["code"] == "route_not_found"


def test_p4_extra_segment_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.get("/rem/primary/v1/models/extra", headers=_auth(TOKEN_A))
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"


def test_p5_soul_case_variant_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post("/REM/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"


def test_p6_version_and_endpoint_case_variant_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    for bad in ("/rem/primary/V1/models", "/rem/primary/v1/Chat/Completions", "/rem/primary/v1/chat/COMPLETIONS"):
        if bad.endswith("models"):
            resp = client.get(bad, headers=_auth(TOKEN_A))
        else:
            resp = client.post(bad, headers=_auth(TOKEN_A), json=_chat_body())
        assert resp.status_code == 404, bad
        assert _err(resp)["code"] == "route_not_found"


def test_p7_wrong_method_and_root_are_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.get("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A))
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"
    resp = client.post("/rem/primary/v1/models", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"
    resp = client.get("/", headers=_auth(TOKEN_A))
    assert resp.status_code == 404


# -- auth --------------------------------------------------------------------

def test_a1_missing_auth_is_401(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post("/rem/primary/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 401
    err = _err(resp)
    assert err == {"message": "Invalid credentials.", "type": "invalid_client", "code": "unauthorized"}


def test_a2_bad_scheme_is_401(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers={"Authorization": "Token " + TOKEN_A},
        json=_chat_body(),
    )
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"


def test_a3_empty_bearer_is_401(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers={"Authorization": "Bearer "},
        json=_chat_body(),
    )
    assert resp.status_code == 401
    assert _err(resp)["message"] == "Invalid credentials."


def test_a4_unknown_token_beats_bad_body_401(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth("no-such-token"),
        json={"model": "primary"},
    )
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"


def test_a5_revoked_token_is_401(tmp_path, monkeypatch):
    revoked_raw = revoke_registry(_std_raw(), "client-b")
    provider = RegistryProvider(revoked_raw)
    preset_path = _std_preset_file(tmp_path, monkeypatch)
    app = create_external_app(provider, file_preset_loader(preset_path), _FakeBackend())
    client = TestClient(app)
    resp = client.post("/mai/primary/v1/chat/completions", headers=_auth(TOKEN_B), json=_chat_body())
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"


def test_a6_valid_token_reaches_backend_200(tmp_path, monkeypatch):
    backend = _FakeBackend("ok-text")
    client = TestClient(_std_app(tmp_path, monkeypatch, backend=backend))
    resp = client.post("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 200
    assert len(backend.calls) == 1


# -- error mapping ------------------------------------------------------------

def test_e1_unknown_soul_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post("/ram/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 404
    assert _err(resp) == {"message": "Unknown route.", "type": "invalid_request_error", "code": "route_not_found"}


def test_e2_missing_route_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post("/rem/no-such-preset/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"


def test_e3_unknown_preset_in_valid_config_is_404(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post("/rem/ghost/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 404
    err = _err(resp)
    assert err["code"] == "route_not_found"
    assert err["message"] == "Unknown route."


def test_e4_stream_true_is_400(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body(stream=True)
    )
    assert resp.status_code == 400
    err = _err(resp)
    assert err["code"] == "invalid_request_error"
    assert err["message"] == "Streaming is not supported by this interface version."


def test_e5_schema_fault_is_400(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    bad = _chat_body(messages=[{"role": "developer", "content": "x"}])
    resp = client.post("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=bad)
    assert resp.status_code == 400
    assert _err(resp)["code"] == "invalid_request_error"


def test_e6_mapping_mismatch_is_500_without_detail(tmp_path, monkeypatch):
    routes = [_route("rem", "primary", agent="agent_mai")]
    raw = _raw(routes, [("client-a", TOKEN_A, ["rem/primary"])])
    provider = RegistryProvider(raw)
    preset_path = _std_preset_file(tmp_path, monkeypatch)
    app = create_external_app(provider, file_preset_loader(preset_path), _FakeBackend())
    client = TestClient(app)
    resp = client.post("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 500
    err = _err(resp)
    assert err["message"] == "Internal server error."
    assert err["type"] == "server_error"
    assert KEY_A not in resp.text


def test_e7_forbidden_route_beats_bad_body_403(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions", headers=_auth(TOKEN_B), json={"model": "primary"}
    )
    assert resp.status_code == 403
    assert _err(resp) == {
        "message": "Route access is forbidden.",
        "type": "permission_denied",
        "code": "forbidden",
    }


def test_e8_bad_config_and_missing_key_are_500(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_A, KEY_A)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    provider = RegistryProvider(_std_raw())
    app = create_external_app(provider, file_preset_loader(bad), _FakeBackend())
    resp = TestClient(app).post(
        "/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body()
    )
    assert resp.status_code == 500
    assert _err(resp)["message"] == "Internal server error."
    good = _std_preset_file(tmp_path, monkeypatch)
    monkeypatch.delenv(REF_A, raising=False)
    monkeypatch.delenv(REF_B, raising=False)
    app2 = create_external_app(provider, file_preset_loader(good), _FakeBackend())
    resp2 = TestClient(app2).post(
        "/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body()
    )
    assert resp2.status_code == 500
    assert _err(resp2)["message"] == "Internal server error."
    assert KEY_A not in resp2.text


# -- models endpoint ----------------------------------------------------------

def test_m1_models_returns_static_list(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.get("/mai/primary/v1/models", headers=_auth(TOKEN_B))
    assert resp.status_code == 200
    assert resp.json() == {"object": "list", "data": [{"id": "primary"}]}


def test_m2_models_without_token_is_401(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.get("/mai/primary/v1/models")
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"


# -- secret scrub --------------------------------------------------------------

def test_s1_bad_bearer_sentinel_never_echoed(tmp_path, monkeypatch, caplog):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        resp = client.post(
            "/rem/primary/v1/chat/completions",
            headers=_auth(BEARER_SENTINEL),
            json=_chat_body(),
        )
    assert resp.status_code == 401
    assert BEARER_SENTINEL not in resp.text
    assert BEARER_SENTINEL not in caplog.text


def test_s2_provider_key_never_leaves(tmp_path, monkeypatch, caplog):
    backend = _FakeBackend("ok-text")
    client = TestClient(_std_app(tmp_path, monkeypatch, backend=backend))
    with caplog.at_level(logging.INFO, logger=APP_LOGGER):
        resp = client.post("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 200
    assert KEY_A not in resp.text
    assert KEY_A not in caplog.text


# -- backend injection ----------------------------------------------------------

def test_b1_fake_backend_text_shapes_reply(tmp_path, monkeypatch):
    backend = _FakeBackend("says hi")
    client = TestClient(_std_app(tmp_path, monkeypatch, backend=backend))
    resp = client.post("/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body())
    assert resp.status_code == 200
    out = resp.json()
    assert out["object"] == "chat.completion"
    assert out["model"] == "primary"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "says hi"}
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    route, preset, messages, params = backend.calls[0]
    assert (route.soul_alias, route.backend_alias) == ("rem", "primary")
    assert preset.name == "primary"
    assert params == {"temperature": 0.5, "max_tokens": 512}
    assert [m.content for m in messages] == ["hi"]


def test_b2_default_backend_fails_closed_500(tmp_path, monkeypatch):
    preset_path = _std_preset_file(tmp_path, monkeypatch)
    provider = RegistryProvider(_std_raw())
    app = create_external_app(provider, file_preset_loader(preset_path))
    assert isinstance(app.routes, list)
    resp = TestClient(app).post(
        "/rem/primary/v1/chat/completions", headers=_auth(TOKEN_A), json=_chat_body()
    )
    assert resp.status_code == 500
    assert _err(resp)["message"] == "Internal server error."


# -- illegal routes read nothing -------------------------------------------------

def test_z1_illegal_route_reads_no_env_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_A, KEY_A)
    real_get = os.environ.get

    def _spy(key, default=None):
        # The framework itself reads its own env (PYTHONASYNCIODEBUG and
        # friends) on every call -- that noise is not ours. What must never
        # happen on an illegal route is OUR code touching preset key refs.
        if key in (REF_A, REF_B):
            raise AssertionError("illegal route must not touch preset env: " + key)
        return real_get(key, default)

    monkeypatch.setattr(os.environ, "get", _spy)
    loader_calls = []
    base = file_preset_loader(tmp_path / "does-not-exist.json")

    def _counting(name):
        loader_calls.append(name)
        return base(name)

    provider = RegistryProvider(_std_raw())
    app = create_external_app(provider, _counting, _FakeBackend())
    client = TestClient(app)
    for bad in (
        "/ram/primary/v1/chat/completions",
        "/rem/primary/v1/models/",
        "/rem/primary/v1/chat/completions/extra",
    ):
        resp = client.post(bad, json=_chat_body())
        assert resp.status_code == 404, bad
        assert _err(resp)["code"] == "route_not_found"
    assert loader_calls == []


# -- structural guard: the app file stays offline ---------------------------------

def test_g1_app_imports_and_literals_are_pinned():
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))
    top, full = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full.add(alias.name)
                top.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            full.add(node.module or "")
            top.add((node.module or "").split(".")[0])
    assert top == {"__future__", "collections", "fastapi", "json", "logging", "src", "typing", "uuid"}, top
    banned = (
        "proxy",
        "socket",
        "urllib",
        "httpx",
        "requests",
        "websocket",
        "aiohttp",
        "uvicorn",
        "http",
        "ssl",
        "server",
    )
    for mod in full:
        for word in banned:
            assert word not in mod, mod
    text = APP_PATH.read_text(encoding="utf-8")
    for snippet in ("uvicorn.run", "socket", "requests", "urllib", ":8000", ":8765", ":8766", ":8767"):
        assert snippet not in text, snippet
    assert EXTERNAL_PORT == 18765
    assert callable(create_external_app)
    assert callable(parse_external_path)
    assert callable(file_preset_loader)
    assert issubclass(NotImplementedBackend, object)
    assert isinstance(ChatBackend, type)
    with pytest.raises(InterfaceError):
        NotImplementedBackend().complete(None, None, (), {})
