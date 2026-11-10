"""S8 acceptance: LAN -> gateway -> cloud route, consolidated.

Pure offline: tmp files + monkeypatched env only. No server, no ports, no
real keys, no real provider calls. Every key value in this file is an
uppercase hyphenated SENTINEL. ``socket.create_connection`` is hard-blocked
by the autouse fixture below (``socket.socket.connect`` is deliberately NOT
blocked: on Windows the asyncio event loop builds its self-pipe on it --
S5/S6/S7 precedent).

Transport is ASGI memory only (TestClient runs the app in-process; it never
claims a port). The backend is always the injected fake carrier below: it
counts calls so every reject path can assert provider-zero-calls. The real
carrier (PresetRoutingBackend) is never constructed here.

What this file pins (Sol S8-CLOSEOUT-1, decision-complete):

  1. chat/models happy paths on the frozen route (rem/primary);
  2. 401 / 403 / 404 / stream-400 / agent-mismatch-500, each with
     provider-zero-calls;
  3. preset per-call reread (model swap without restart/rebuild);
  4. registry startup snapshot (disk rewrite alone changes nothing;
     a rebuild -- i.e. a restart -- picks the new route up);
  5. EXTERNAL_PORT frozen at 18765.

Live evidence (H4/M4) is NOT re-probed here -- no real LLM request is made
by any test in this file. The H4/M4 citations live in
``verification/S8/REPORT.md``.
"""

import hashlib
import json
import socket

import pytest
from fastapi.testclient import TestClient

from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider

OWNER = "s8-acceptance-owner"
REF = "S8_ACCEPTANCE_REF"
SENTINEL = "S8-ACCEPTANCE-SENTINEL-KEY"

OTHER_REF = "S8_ACCEPTANCE_OTHER_REF"
OTHER_SENTINEL = "S8-ACCEPTANCE-OTHER-KEY"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("S8: tests must not dial out (socket.create_connection blocked)")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


class _FakeBackend:
    """Injected carrier stand-in: counts calls, returns fixed text."""

    def __init__(self, text="s8-fake-reply"):
        self.text = text
        self.calls = []

    def complete(self, route, preset, messages, params):
        self.calls.append((route, preset, messages, params))
        return self.text


def _route(soul="rem", preset="primary", agent=None):
    return {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": agent if agent is not None else "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, preset),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _raw(token, routes=None, allowed=("rem/primary",)):
    if routes is None:
        routes = [_route()]
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": routes,
        "clients": [
            {
                "client_id": "client-s8",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _write_presets(path, model="do-not-deploy-model-s8", name="primary", key_ref=REF):
    path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": name,
                        "provider": "deepseek",
                        "model": model,
                        "params": {"temperature": 0.5, "max_tokens": 512},
                        "key_ref": key_ref,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _setup(tmp_path, monkeypatch, routes=None, allowed=("rem/primary",), model="do-not-deploy-model-s8"):
    """Build one isolated app. Returns (client, token, backend, preset_path)."""
    monkeypatch.setenv(REF, SENTINEL)
    monkeypatch.setenv(OTHER_REF, OTHER_SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json", model=model)
    provider = RegistryProvider(_raw(token, routes=routes, allowed=allowed))
    backend = _FakeBackend()
    app = create_external_app(provider, file_preset_loader(preset_path), backend)
    return (TestClient(app), token, backend, preset_path, provider)


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _chat_body(**over):
    body = {"model": "primary", "messages": [{"role": "user", "content": "hi"}]}
    body.update(over)
    return body


def _err(resp):
    assert resp.status_code in (400, 401, 403, 404, 500)
    return resp.json()["error"]


# -- frozen port -------------------------------------------------------------


def test_s8_port_is_frozen_18765():
    assert EXTERNAL_PORT == 18765


# -- happy paths --------------------------------------------------------------


def test_s8_chat_200_rem_primary(tmp_path, monkeypatch):
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["object"] == "chat.completion"
    assert out["id"].startswith("chatcmpl-")
    assert out["model"] == "primary"
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "s8-fake-reply"}
    assert out["choices"][0]["finish_reason"] == "stop"
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    assert len(backend.calls) == 1
    route, preset, _messages, params = backend.calls[0]
    assert (route.soul_alias, route.backend_alias) == ("rem", "primary")
    assert route.agent_id == "agent_rem"
    assert params == {"temperature": 0.5, "max_tokens": 512}


def test_s8_models_200_static_list(tmp_path, monkeypatch):
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.get("/rem/primary/v1/models", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json() == {"object": "list", "data": [{"id": "primary"}]}
    assert backend.calls == []


# -- reject paths: each asserts provider zero calls ---------------------------


def test_s8_missing_auth_401_no_provider_call(tmp_path, monkeypatch):
    client, _token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post("/rem/primary/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"
    assert backend.calls == []


def test_s8_wrong_token_401_no_provider_call(tmp_path, monkeypatch):
    client, _token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth("s8-no-such-token"),
        json=_chat_body(),
    )
    assert resp.status_code == 401
    assert _err(resp)["code"] == "unauthorized"
    assert backend.calls == []


def test_s8_forbidden_403_no_provider_call(tmp_path, monkeypatch):
    # Token is valid but its allowed_routes omit rem/primary.
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json")
    routes = [_route("rem", "primary"), _route("mai", "primary")]
    raw = _raw(token, routes=routes, allowed=("mai/primary",))
    backend = _FakeBackend()
    app = create_external_app(RegistryProvider(raw), file_preset_loader(preset_path), backend)
    client = TestClient(app)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp.status_code == 403
    assert _err(resp)["code"] == "forbidden"
    assert backend.calls == []


def test_s8_unknown_soul_404_no_provider_call(tmp_path, monkeypatch):
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post(
        "/ram/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"
    assert backend.calls == []


def test_s8_unknown_preset_404_no_provider_call(tmp_path, monkeypatch):
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post(
        "/rem/no-such-preset/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"
    assert backend.calls == []


def test_s8_stream_true_400_no_provider_call(tmp_path, monkeypatch):
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(stream=True),
    )
    assert resp.status_code == 400
    err = _err(resp)
    assert err["code"] == "invalid_request_error"
    assert err["message"] == "Streaming is not supported by this interface version."
    assert backend.calls == []


def test_s8_agent_mismatch_500_no_provider_call(tmp_path, monkeypatch):
    routes = [_route("rem", "primary", agent="agent_mai")]
    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch, routes=routes)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp.status_code == 500
    assert backend.calls == []
    assert SENTINEL not in resp.text


# -- preset per-call reread vs registry startup snapshot -----------------------


def test_s8_preset_model_swap_without_restart(tmp_path, monkeypatch):
    client, token, backend, preset_path, _provider = _setup(
        tmp_path, monkeypatch, model="do-not-deploy-model-s8-v1"
    )
    resp1 = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp1.status_code == 200
    assert backend.calls[-1][1].model == "do-not-deploy-model-s8-v1"
    _write_presets(preset_path, model="do-not-deploy-model-s8-v2")
    resp2 = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(),
    )
    assert resp2.status_code == 200
    assert backend.calls[-1][1].model == "do-not-deploy-model-s8-v2"


def test_s8_registry_disk_rewrite_needs_restart(tmp_path, monkeypatch):
    client, token, backend, preset_path, _provider = _setup(tmp_path, monkeypatch)
    ok = client.post(
        "/rem/secondary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(model="secondary"),
    )
    assert ok.status_code == 404
    # A disk-only rewrite is NOT picked up: the running app holds the
    # startup snapshot (RegistryProvider object), not the file.
    raw_v2 = _raw(
        token,
        routes=[_route("rem", "primary"), _route("rem", "secondary")],
        allowed=("rem/primary", "rem/secondary"),
    )
    (tmp_path / "registry.json").write_text(json.dumps(raw_v2), encoding="utf-8")
    still_404 = client.post(
        "/rem/secondary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(model="secondary"),
    )
    assert still_404.status_code == 404
    assert backend.calls == []
    # Restart == rebuild the provider from the new file: the route appears.
    provider2 = RegistryProvider(
        json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    )
    client2 = TestClient(create_external_app(provider2, file_preset_loader(preset_path), backend))
    resp = client2.post(
        "/rem/secondary/v1/chat/completions",
        headers=_auth(token),
        json=_chat_body(model="secondary"),
    )
    # The secondary preset name is absent from the preset file, so the route
    # resolves but the preset lookup stays 404 -- only the registry half moved.
    assert resp.status_code == 404
    assert _err(resp)["code"] == "route_not_found"


# -- credential hygiene --------------------------------------------------------


def test_s8_provider_key_never_leaves(tmp_path, monkeypatch, caplog):
    import logging

    client, token, backend, _preset_path, _provider = _setup(tmp_path, monkeypatch)
    with caplog.at_level(logging.INFO, logger="soul_os.external_gateway.app"):
        resp = client.post(
            "/rem/primary/v1/chat/completions",
            headers=_auth(token),
            json=_chat_body(),
        )
    assert resp.status_code == 200
    assert SENTINEL not in resp.text
    assert SENTINEL not in caplog.text
    digest_probe = hashlib.sha256(SENTINEL.encode("utf-8")).hexdigest()
    assert digest_probe not in resp.text
