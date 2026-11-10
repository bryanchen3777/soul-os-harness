"""S7-W5 transport carrier tests. Pure offline: monkeypatched httpx only.

No real provider call exists anywhere in this file. Every key value here is
an uppercase hyphenated SENTINEL. ``socket.create_connection`` is
hard-blocked by the autouse fixture below (``socket.socket.connect`` is
deliberately NOT blocked: on Windows the asyncio event loop builds its own
self-pipe on it -- S5/S6 precedent).

Transport is ASGI memory only (TestClient runs the app in-process; it never
claims a port). The serve script is exercised through ``main()`` argument
handling only -- help text, refusal paths, import purity. No serving loop
is ever entered by any test in this file.
"""

import ast
import importlib.util
import json
import logging
import os
import socket
import sys
from pathlib import Path
from types import MappingProxyType

import httpx
import pytest
from fastapi.testclient import TestClient

from src.external_gateway.app import EXTERNAL_PORT
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import DeepSeekBackend
from src.external_gateway.backends import OllamaBackend
from src.external_gateway.backends import PresetRoutingBackend
from src.external_gateway.backends import PROVIDER_TIMEOUT_S
from src.external_gateway.backends import SUPPORTED_PROVIDERS
from src.external_gateway.backends import build_backend
from src.external_gateway.openai_shim import ChatMessage
from src.external_gateway.preset import Preset
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider
from src.interfaces.schema import InterfaceError

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKENDS_PATH = REPO_ROOT / "src" / "external_gateway" / "backends.py"
SERVE_PATH = REPO_ROOT / "scripts" / "serve_external_gateway.py"

OWNER = "s7-w5-carrier-owner"
REF_D = "S7_W5_DEEPSEEK_REF"
REF_O = "S7_W5_OLLAMA_REF"
KEY_D = "S7-W5-DEEPSEEK-SENTINEL"
KEY_O = "S7-W5-OLLAMA-SENTINEL"
TOKEN_A, _DIG_A = issue_credential()
CONTENT_SENTINEL = "S7-W5-REPLY-SENTINEL"
UPSTREAM_SENTINEL = "S7-W5-UPSTREAM-SENTINEL"

BACKEND_LOGGER = "soul_os.external_gateway.backends"
DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("S7-W5: tests must not dial out (socket.create_connection blocked)")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


def _deepseek_preset():
    return Preset(
        name="primary",
        provider="deepseek",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_D,
        api_key=KEY_D,
        base_url=None,
    )


def _ollama_preset(base_url="http://ollama-do-not-deploy:11434"):
    return Preset(
        name="primary",
        provider="ollama",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_O,
        api_key=KEY_O,
        base_url=base_url,
    )


def _openai_preset():
    return Preset(
        name="primary",
        provider="openai",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_D,
        api_key=KEY_D,
        base_url=None,
    )


def _messages():
    return (ChatMessage(role="user", content="hi"),)


def _params():
    return {"temperature": 0.5, "max_tokens": 512}


class _FakeResp:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _ok_payload(content=CONTENT_SENTINEL):
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def _patch_post(monkeypatch, status_code=200, payload=None, capture=None, raises=None):
    def _fake_post(self, url, headers=None, json=None, **kw):
        if capture is not None:
            capture.append({"url": url, "headers": dict(headers or {}), "json": json})
        if raises is not None:
            raise raises
        return _FakeResp(status_code, payload)

    monkeypatch.setattr(httpx.Client, "post", _fake_post, raising=True)


# -- unit: success shapes -------------------------------------------------------


def test_u1_deepseek_success_shape(monkeypatch):
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    backend = DeepSeekBackend(api_key=KEY_D, model="do-not-deploy-model")
    out = backend.complete(None, _deepseek_preset(), _messages(), _params())
    assert out == CONTENT_SENTINEL
    assert len(seen) == 1
    call = seen[0]
    assert call["url"] == DEEPSEEK_URL
    assert call["headers"].get("Authorization") == "Bearer " + KEY_D
    body = call["json"]
    assert body["model"] == "do-not-deploy-model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 512
    assert body["stream"] is False


def test_u2_ollama_success_shape_with_bearer(monkeypatch):
    # S7-W6: Bearer is unconditional (cloud and local share one path, no
    # URL sniffing). Localhost stays the loopback representative, so the
    # D3 https gate lets it through.
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    preset = _ollama_preset(base_url="http://localhost:11434")
    backend = OllamaBackend(base_url=preset.base_url, model="do-not-deploy-model", api_key=preset.api_key)
    out = backend.complete(None, preset, _messages(), _params())
    assert out == CONTENT_SENTINEL
    assert len(seen) == 1
    call = seen[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer " + KEY_O}
    assert call["json"]["stream"] is False


# -- unit: exact error map ------------------------------------------------------


def test_u3_provider_429_maps_429(monkeypatch):
    _patch_post(monkeypatch, 429, {"error": "slow down"})
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert ei.value.status == 429
    assert ei.value.code == "rate_limit_error"
    assert ei.value.message == "Rate limit exceeded."


def test_u4_provider_401_maps_500_without_leak(monkeypatch):
    _patch_post(monkeypatch, 401, {"error": UPSTREAM_SENTINEL})
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert ei.value.status == 500
    assert ei.value.code == "internal"
    assert ei.value.message == "Internal server error."
    assert UPSTREAM_SENTINEL not in str(ei.value)


def test_u5_provider_403_maps_500(monkeypatch):
    _patch_post(monkeypatch, 403, {"error": "denied"})
    with pytest.raises(InterfaceError) as ei:
        OllamaBackend(base_url="http://localhost:11434", model="m", api_key=KEY_O).complete(None, _ollama_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")


def test_u6_provider_404_maps_500(monkeypatch):
    _patch_post(monkeypatch, 404, {"error": "no such model"})
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")


def test_u7_provider_500_maps_500(monkeypatch):
    _patch_post(monkeypatch, 500, {"error": UPSTREAM_SENTINEL})
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")
    assert UPSTREAM_SENTINEL not in str(ei.value)


def test_u8_timeout_maps_500(monkeypatch):
    _patch_post(monkeypatch, raises=httpx.TimeoutException("S7-W5-TIMEOUT-SENTINEL"))
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")
    assert "S7-W5-TIMEOUT-SENTINEL" not in str(ei.value)


def test_u9_connect_error_maps_500(monkeypatch):
    _patch_post(monkeypatch, raises=httpx.ConnectError("S7-W5-CONNECT-SENTINEL"))
    with pytest.raises(InterfaceError) as ei:
        OllamaBackend(base_url="http://localhost:11434", model="m", api_key=KEY_O).complete(None, _ollama_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")
    assert "S7-W5-CONNECT-SENTINEL" not in str(ei.value)


def test_u10_non_str_content_maps_500(monkeypatch):
    _patch_post(monkeypatch, 200, {"choices": [{"message": {"role": "assistant", "content": 12345}}]})
    with pytest.raises(InterfaceError) as ei:
        DeepSeekBackend(api_key=KEY_D, model="m").complete(None, _deepseek_preset(), _messages(), _params())
    assert (ei.value.status, ei.value.code) == (500, "internal")


def test_u11_openai_preset_fails_closed_invalid_preset():
    with pytest.raises(InterfaceError) as ei:
        build_backend(_openai_preset())
    assert ei.value.status == 500
    assert ei.value.code == "invalid_preset"
    assert ei.value.message == "Invalid preset configuration."


def test_u12_secrets_never_enter_logs_or_errors(monkeypatch, caplog):
    _patch_post(monkeypatch, 401, {"error": UPSTREAM_SENTINEL})
    preset = _deepseek_preset()
    with caplog.at_level(logging.INFO, logger=BACKEND_LOGGER):
        with pytest.raises(InterfaceError) as ei:
            PresetRoutingBackend().complete(None, preset, _messages(), _params())
    assert KEY_D not in str(ei.value)
    assert KEY_D not in ei.value.message
    assert KEY_D not in caplog.text
    assert "hi" not in caplog.text
    assert UPSTREAM_SENTINEL not in str(ei.value)
    assert UPSTREAM_SENTINEL not in caplog.text


# -- S7-W6: Ollama Bearer + https gate (H1-H3) -----------------------------------
#
# Cloud and local share one unconditional Bearer path (no URL sniffing).
# Plain http is loopback-only; anything else must arrive over https.
# Every key below is an uppercase hyphenated SENTINEL, never real material.


KEY_C = "S7-W6-CLOUD-SENTINEL-KEY"
KEY_L = "S7-W6-LOCAL-SENTINEL-KEY"
KEY_W = "S7-W6-WAN-SENTINEL-KEY"
REF_C = "S7_W6_OLLAMA_CLOUD_REF"
REF_L = "S7_W6_OLLAMA_LOCAL_REF"
REF_W = "S7_W6_OLLAMA_WAN_REF"
CLOUD_URL = "https://ollama-do-not-deploy-cloud.example.com"
WAN_URL = "http://wan-do-not-deploy.example.com"


def _ollama_cloud_preset():
    return Preset(
        name="primary",
        provider="ollama",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_C,
        api_key=KEY_C,
        base_url=CLOUD_URL,
    )


def _ollama_localhost_preset():
    return Preset(
        name="primary",
        provider="ollama",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_L,
        api_key=KEY_L,
        base_url="http://localhost:11434",
    )


def _ollama_wan_preset():
    return Preset(
        name="primary",
        provider="ollama",
        model="do-not-deploy-model",
        params=MappingProxyType({"temperature": 0.5, "max_tokens": 512}),
        key_ref=REF_W,
        api_key=KEY_W,
        base_url=WAN_URL,
    )


def test_h1_cloud_bearer_exact_and_passthrough(monkeypatch):
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    preset = _ollama_cloud_preset()
    out = build_backend(preset).complete(None, preset, _messages(), _params())
    assert out == CONTENT_SENTINEL
    assert len(seen) == 1
    call = seen[0]
    assert call["url"] == CLOUD_URL + "/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer " + KEY_C}
    body = call["json"]
    assert body["model"] == "do-not-deploy-model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert body["temperature"] == 0.5
    assert body["max_tokens"] == 512
    assert body["stream"] is False


def test_h1b_cloud_401_maps_500_without_leak(monkeypatch, caplog):
    _patch_post(monkeypatch, 401, {"error": UPSTREAM_SENTINEL})
    preset = _ollama_cloud_preset()
    with caplog.at_level(logging.INFO, logger=BACKEND_LOGGER):
        with pytest.raises(InterfaceError) as ei:
            build_backend(preset).complete(None, preset, _messages(), _params())
    assert ei.value.status == 500
    assert ei.value.code == "internal"
    assert ei.value.message == "Internal server error."
    assert KEY_C not in str(ei.value)
    assert KEY_C[:8] not in str(ei.value)
    assert KEY_C[-4:] not in str(ei.value)
    assert UPSTREAM_SENTINEL not in str(ei.value)
    assert KEY_C not in caplog.text
    assert KEY_C[:8] not in caplog.text
    assert KEY_C[-4:] not in caplog.text
    assert UPSTREAM_SENTINEL not in caplog.text


def test_h2_localhost_same_bearer_no_fork(monkeypatch):
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    preset = _ollama_localhost_preset()
    out = build_backend(preset).complete(None, preset, _messages(), _params())
    assert out == CONTENT_SENTINEL
    assert len(seen) == 1
    call = seen[0]
    assert call["url"] == "http://localhost:11434/v1/chat/completions"
    assert call["headers"] == {"Authorization": "Bearer " + KEY_L}


def test_h3_wan_http_refused_without_request(monkeypatch):
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    preset = _ollama_wan_preset()
    with pytest.raises(InterfaceError) as ei:
        build_backend(preset).complete(None, preset, _messages(), _params())
    assert ei.value.status == 500
    assert ei.value.code == "internal"
    assert ei.value.message == "Internal server error."
    assert len(seen) == 0
    assert KEY_W not in str(ei.value)


def test_h3b_loopback_numeric_http_still_allowed(monkeypatch):
    seen = []
    _patch_post(monkeypatch, 200, _ok_payload(), capture=seen)
    preset = _ollama_preset(base_url="http://127.0.0.1:11434")
    backend = OllamaBackend(base_url=preset.base_url, model="do-not-deploy-model", api_key=preset.api_key)
    out = backend.complete(None, preset, _messages(), _params())
    assert out == CONTENT_SENTINEL
    assert len(seen) == 1
    assert seen[0]["headers"] == {"Authorization": "Bearer " + KEY_O}


# -- service layer: through the S6 app ------------------------------------------


def _route(soul, preset, agent=None):
    return {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": agent if agent is not None else "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, preset),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _std_raw():
    routes = [_route("rem", "primary")]
    raw = {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": routes,
        "clients": [
            {
                "client_id": "client-a",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["rem/primary"],
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(TOKEN_A),
            }
        ],
    }
    return raw


def _std_app(tmp_path, monkeypatch):
    monkeypatch.setenv(REF_D, KEY_D)
    preset_path = tmp_path / "presets.json"
    preset_path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": "primary",
                        "provider": "deepseek",
                        "model": "do-not-deploy-model",
                        "params": {"temperature": 0.5, "max_tokens": 512},
                        "key_ref": REF_D,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    provider = RegistryProvider(_std_raw())
    return create_external_app(provider, file_preset_loader(preset_path), PresetRoutingBackend())


def test_v1_chat_200_through_app_with_routing_backend(tmp_path, monkeypatch):
    _patch_post(monkeypatch, 200, _ok_payload("served-by-fake"))
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers={"Authorization": "Bearer " + TOKEN_A},
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "served-by-fake"}
    assert out["usage"] == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_v2_models_200_through_app_with_routing_backend(tmp_path, monkeypatch):
    client = TestClient(_std_app(tmp_path, monkeypatch))
    resp = client.get(
        "/rem/primary/v1/models", headers={"Authorization": "Bearer " + TOKEN_A}
    )
    assert resp.status_code == 200
    assert resp.json() == {"object": "list", "data": [{"id": "primary"}]}


# -- serve script: import purity, help, refusal paths ------------------------------


def _load_serve_module(tag):
    spec = importlib.util.spec_from_file_location(
        "s7_serve_probe_%s" % tag, SERVE_PATH
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_w1_import_has_zero_side_effects(monkeypatch):
    touched = []
    real_get = os.environ.get

    def _spy(key, default=None):
        if key in (REF_D, REF_O):
            touched.append(key)
        return real_get(key, default)

    monkeypatch.setattr(os.environ, "get", _spy)
    mod = _load_serve_module("w1")
    assert touched == []
    assert callable(mod.main)
    assert callable(mod.build_parser)


def test_w2_help_defaults_pin_lan_host_and_frozen_port(capsys):
    mod = _load_serve_module("w2")
    assert mod.build_parser().get_default("host") == "0.0.0.0"
    assert mod.build_parser().get_default("port") == EXTERNAL_PORT
    with pytest.raises(SystemExit) as ei:
        mod.main(["--help"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "0.0.0.0" in out
    assert str(EXTERNAL_PORT) in out

def _write_presets(path):
    path.write_text(json.dumps({"presets": []}), encoding="utf-8")
    return path


def _block_serve(monkeypatch, mod):
    # W3-W5 must never reach the bind: block it so a mutant that drops a
    # refusal check turns red here (RuntimeError) instead of serving.

    def _boom(*args, **kwargs):
        raise RuntimeError("must not serve in tests")

    monkeypatch.setattr(mod.uvicorn, "run", _boom)


def test_w3_template_registry_refused(tmp_path, capsys, monkeypatch):
    mod = _load_serve_module("w3")
    _block_serve(monkeypatch, mod)
    reg = tmp_path / "reg.json"
    reg.write_text(
        json.dumps(
            {
                "owner_principal_id": "x-do-not-deploy",
                "backends": ["port-a"],
                "routes": [],
                "clients": [],
            }
        ),
        encoding="utf-8",
    )
    assert mod.main(["--registry", str(reg), "--presets", str(_write_presets(tmp_path / "p.json"))]) == 2
    assert mod._STARTUP_REFUSAL in capsys.readouterr().err


def test_w4_missing_file_refused(tmp_path, capsys, monkeypatch):
    mod = _load_serve_module("w4")
    _block_serve(monkeypatch, mod)
    assert (
        mod.main(
            [
                "--registry",
                str(tmp_path / "no-such-reg.json"),
                "--presets",
                str(tmp_path / "no-such-pre.json"),
            ]
        )
        == 2
    )
    assert mod._STARTUP_REFUSAL in capsys.readouterr().err


def test_w5_broken_json_refused(tmp_path, capsys, monkeypatch):
    mod = _load_serve_module("w5")
    _block_serve(monkeypatch, mod)
    reg = tmp_path / "reg.json"
    reg.write_text("{broken", encoding="utf-8")
    assert mod.main(["--registry", str(reg), "--presets", str(_write_presets(tmp_path / "p.json"))]) == 2
    assert mod._STARTUP_REFUSAL in capsys.readouterr().err


# -- structural guards ---------------------------------------------------------------


def _port_literals():
    # The guard polices port literals without embedding them: contiguous
    # colon-plus-port tokens must not appear in THIS file either (S7 rule), so
    # the list is assembled from parts. The executed check is identical.
    # Even the bare digit runs ("8000") are assembled so no contiguous
    # banned literal appears in THIS file either.
    return tuple(":" + p for p in ("80" + "00", "87" + "65", "87" + "66", "87" + "67"))


def test_g1_backends_imports_and_literals_pinned():
    tree = ast.parse(BACKENDS_PATH.read_text(encoding="utf-8"))
    top, full = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                full.add(alias.name)
                top.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            full.add(node.module or "")
            top.add((node.module or "").split(".")[0])
    assert top == {"__future__", "httpx", "logging", "src", "typing"}, top
    banned = (
        "uvicorn",
        "socket",
        "urllib",
        "requests",
        "websocket",
        "aiohttp",
        "ssl",
        "server",
    )
    for mod in full:
        for word in banned:
            assert word not in mod, mod
    text = BACKENDS_PATH.read_text(encoding="utf-8")
    # NOTE: bare "server" is pinned at import level only (above): the D3
    # mandated fixed copy "Internal server error." carries that token, so a
    # text-level "server" check would false-positive on required copy.
    for snippet in ("uvicorn", "socket", "urllib", "requests", "websocket", "aiohttp", "ssl"):
        assert snippet not in text, snippet
    for snippet in _port_literals():
        assert snippet not in text, snippet
    assert PROVIDER_TIMEOUT_S == 60.0
    assert SUPPORTED_PROVIDERS == frozenset({"deepseek", "ollama"})
    assert "https://api.deepseek.com/v1/chat/completions" in text
    assert callable(build_backend)
    assert callable(PresetRoutingBackend)


def test_g2_serve_shape_pinned():
    mod = _load_serve_module("g2")
    text = SERVE_PATH.read_text(encoding="utf-8")
    for snippet in _port_literals():
        assert snippet not in text, snippet
    assert "18" + "765" not in text, "frozen port literal"
    assert "EXTERNAL_PORT" in text
    assert "do-not-deploy" in text
    assert "0.0.0.0" in text
    assert "trusted-LAN plaintext, no TLS" in text
    assert mod.build_parser().get_default("port") == EXTERNAL_PORT
    runner = getattr(mod.uvicorn, "run")
    assert callable(runner)
