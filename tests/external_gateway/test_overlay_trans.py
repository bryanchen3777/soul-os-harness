"""SHIM-OVERLAY-TRANS-1 acceptance: transparent passthrough + soul overlay.

Pure offline: TestClient in-process only, no server, no ports, no real keys,
no real provider calls. Upstream HTTP is monkeypatched at the httpx layer.
Every key value here is an uppercase hyphenated SENTINEL.
"""

import json
import socket

import pytest
from fastapi.testclient import TestClient

from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import BackendReply
from src.external_gateway.backends import OllamaBackend
from src.external_gateway.backends import PresetRoutingBackend
from src.external_gateway.openai_shim import InternalReq
from src.external_gateway.openai_shim import parse_chat_request
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider

OWNER = "trans-overlay-owner"
REF = "TRANS_OVERLAY_REF"
SENTINEL = "TRANS-OVERLAY-SENTINEL-KEY"

TOOL_CALLS = [
    {
        "id": "call-1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"X"}'},
    }
]
UPSTREAM_USAGE = {"prompt_tokens": 3, "completion_tokens": 7, "total_tokens": 10}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("TRANS: tests must not dial out (socket.create_connection blocked)")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


def _route(soul="rem", preset="primary"):
    return {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": "agent_" + soul,
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
                "client_id": "client-trans",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _write_presets(path, provider="deepseek", model="do-not-deploy-model-trans"):
    entry = {
        "name": "primary",
        "provider": provider,
        "model": model,
        "params": {"temperature": 0.5, "max_tokens": 512},
        "key_ref": REF,
    }
    if provider == "ollama":
        entry["params"]["base_url"] = "http://localhost:11434"
    path.write_text(json.dumps({"presets": [entry]}), encoding="utf-8")


def _setup(tmp_path, monkeypatch, backend, provider="deepseek", **app_kw):
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = tmp_path / "presets.json"
    _write_presets(preset_path, provider=provider)
    app = create_external_app(
        RegistryProvider(_raw(token)), file_preset_loader(preset_path), backend, **app_kw
    )
    return (TestClient(app), token)


def _auth(token):
    return {"Authorization": "Bearer " + token}


class _TextBackend:
    def __init__(self, text="trans-fake-reply"):
        self.text = text
        self.calls = []

    def complete(self, route, preset, messages, params, passthrough=None):
        self.calls.append((route, preset, messages, params, passthrough))
        return self.text


class _ReplyBackend:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, route, preset, messages, params, passthrough=None):
        self.calls.append((route, preset, messages, params, passthrough))
        return self.reply


# -- shim: arbitrary keys ride verbatim ---------------------------------------


def test_shim_passthrough_arbitrary_keys():
    tools = [{"type": "function", "function": {"name": "f"}}]
    body = {
        "model": "model-a",
        "messages": [{"role": "user", "content": "hi"}],
        "tools": tools,
        "tool_choice": "auto",
        "functions": [{"name": "old"}],
        "function_call": {"name": "old"},
        "logit_bias": {"x": 1},
        "store": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 16,
        "custom_meta_field": {"nested": [1, 2, 3]},
    }
    req = parse_chat_request(body)
    assert isinstance(req, InternalReq)
    assert req.model == "model-a"
    assert req.temperature == 0.5
    assert req.max_tokens == 512
    assert req.passthrough == {
        "tools": tools,
        "tool_choice": "auto",
        "functions": [{"name": "old"}],
        "function_call": {"name": "old"},
        "logit_bias": {"x": 1},
        "store": True,
        "stream_options": {"include_usage": True},
        "max_completion_tokens": 16,
        "custom_meta_field": {"nested": [1, 2, 3]},
    }
    raw = req.to_raw_request()
    for k, v in req.passthrough.items():
        assert raw[k] == v
    assert raw["model"] == "model-a"
    assert raw["temperature"] == 0.5
    assert raw["max_tokens"] == 512
    assert raw["stream"] is False


def test_shim_passthrough_is_snapshot_not_alias():
    body = {"model": "model-a", "messages": [{"role": "user", "content": "hi"}], "tools": []}
    req = parse_chat_request(body)
    body["tools"].append({"type": "function"})
    assert req.passthrough == {"tools": []}


# -- soul overlay: any fault degrades to the plain model reply ----------------


def test_soul_os_overlay_failure_fallback(tmp_path, monkeypatch):
    def _boom(messages, params):
        raise RuntimeError("TRANS-OVERLAY-DOWN")

    backend = _TextBackend()
    client, token = _setup(tmp_path, monkeypatch, backend, soul_overlay_fn=_boom)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "trans-fake-reply"}


def test_soul_os_overlay_malformed_return_fallback(tmp_path, monkeypatch):
    backend = _TextBackend()
    client, token = _setup(
        tmp_path, monkeypatch, backend, soul_overlay_fn=lambda m, p: "not-a-pair"
    )
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "trans-fake-reply"


def test_soul_os_overlay_none_means_no_change(tmp_path, monkeypatch):
    seen = []

    def _noop(messages, params):
        seen.append((messages, params))
        return None

    backend = _TextBackend()
    client, token = _setup(tmp_path, monkeypatch, backend, soul_overlay_fn=_noop)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert len(seen) == 1
    route, preset, messages, params, passthrough = backend.calls[0]
    assert [m.content for m in messages] == ["hi"]
    assert params == {"temperature": 0.5, "max_tokens": 512}


# -- memory settle: background failure never affects the main reply -----------


def test_background_memory_failure_resilient(tmp_path, monkeypatch):
    def _settle_boom(soul, preset_name, messages, text):
        raise RuntimeError("TRANS-SETTLE-DOWN")

    backend = _TextBackend()
    client, token = _setup(tmp_path, monkeypatch, backend, memory_settle_fn=_settle_boom)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"] == {
        "role": "assistant",
        "content": "trans-fake-reply",
    }


def test_background_memory_success_receives_context(tmp_path, monkeypatch):
    seen = []

    def _settle(soul, preset_name, messages, text):
        seen.append((soul, preset_name, [m.content for m in messages], text))

    backend = _TextBackend()
    client, token = _setup(tmp_path, monkeypatch, backend, memory_settle_fn=_settle)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert resp.status_code == 200
    assert seen == [("rem", "primary", ["hi"], "trans-fake-reply")]


# -- tool reply: byte-identical through app + SSE ------------------------------


def test_tool_calls_byte_identical_non_stream(tmp_path, monkeypatch):
    reply = BackendReply(text="", tool_calls=TOOL_CALLS, usage=dict(UPSTREAM_USAGE))
    backend = _ReplyBackend(reply)
    client, token = _setup(tmp_path, monkeypatch, backend)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": TOOL_CALLS,
            "tool_choice": "auto",
        },
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["choices"][0]["message"]["tool_calls"] == TOOL_CALLS
    assert out["choices"][0]["message"]["content"] == ""
    assert out["usage"] == UPSTREAM_USAGE
    _route, _preset, _messages, _params, passthrough = backend.calls[0]
    assert passthrough["tools"] == TOOL_CALLS
    assert passthrough["tool_choice"] == "auto"


def test_tool_calls_byte_identical_stream(tmp_path, monkeypatch):
    reply = BackendReply(text="", tool_calls=TOOL_CALLS, usage=dict(UPSTREAM_USAGE))
    backend = _ReplyBackend(reply)
    client, token = _setup(tmp_path, monkeypatch, backend)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": TOOL_CALLS,
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    frames = [ln for ln in resp.text.split("\n") if ln.startswith("data: ")]
    assert frames[-1] == "data: [DONE]"
    payloads = [json.loads(f[len("data: "):]) for f in frames[:-1]]
    deltas = [p["choices"][0]["delta"] for p in payloads if p["choices"]]
    content_deltas = [d for d in deltas if "content" in d]
    assert len(content_deltas) == 1
    assert content_deltas[0]["tool_calls"] == TOOL_CALLS
    usage_chunks = [p for p in payloads if p.get("usage")]
    assert len(usage_chunks) == 1
    assert usage_chunks[0]["usage"] == UPSTREAM_USAGE


# -- mock loopback: shim -> real routing backend -> mock upstream --------------


def test_mock_loopback_tools_and_unknown_key_end_to_end(tmp_path, monkeypatch):
    import httpx

    from src.external_gateway import backends as _backends_mod

    seen = []

    class _FakeResp:
        status_code = 200

        def json(self):
            return {
                "choices": [{"message": {"role": "assistant", "content": None, "tool_calls": TOOL_CALLS}}],
                "usage": dict(UPSTREAM_USAGE),
            }

    def _fake_post(self, url, headers=None, json=None, **kw):
        seen.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeResp()

    monkeypatch.setattr(httpx.Client, "post", _fake_post, raising=True)
    assert _backends_mod.OllamaBackend is OllamaBackend
    client, token = _setup(
        tmp_path, monkeypatch, PresetRoutingBackend(), provider="ollama"
    )
    tools = [{"type": "function", "function": {"name": "get_weather"}}]
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": tools,
            "tool_choice": "auto",
            "custom_meta_field": "meta-1",
        },
    )
    assert resp.status_code == 200
    assert len(seen) == 1
    upstream = seen[0]["json"]
    assert upstream["tools"] == tools
    assert upstream["tool_choice"] == "auto"
    assert upstream["custom_meta_field"] == "meta-1"
    out = resp.json()
    assert out["choices"][0]["message"]["tool_calls"] == TOOL_CALLS
    assert out["choices"][0]["message"]["content"] == ""
    assert out["usage"] == UPSTREAM_USAGE
