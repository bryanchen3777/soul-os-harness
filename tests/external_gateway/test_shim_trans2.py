"""SHIM-OVERLAY-TRANS-2: message-layer transparent passthrough.

Pure offline: TestClient in-process only, no server, no ports, no real keys,
no real provider calls. Upstream HTTP is monkeypatched at the httpx layer.
Every key value here is an uppercase hyphenated SENTINEL.
"""

import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import BackendReply
from src.external_gateway.backends import PresetRoutingBackend
from src.external_gateway.backends import _wire_messages
from src.external_gateway.openai_shim import ChatMessage
from src.external_gateway.openai_shim import InternalReq
from src.external_gateway.openai_shim import parse_chat_request
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider
from src.interfaces.schema import InterfaceError

OWNER = "trans2-overlay-owner"
REF = "TRANS2_OVERLAY_REF"
SENTINEL = "TRANS2-OVERLAY-SENTINEL-KEY"

TOOL_CALLS = [
    {
        "id": "call-9",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city":"X"}'},
    }
]
UPSTREAM_USAGE = {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("TRANS-2: tests must not dial out (socket.create_connection blocked)")

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


def _raw(token):
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": [_route()],
        "clients": [
            {
                "client_id": "client-trans2",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["rem/primary"],
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _setup(tmp_path, monkeypatch, backend, provider="ollama"):
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = tmp_path / "presets.json"
    entry = {
        "name": "primary",
        "provider": provider,
        "model": "do-not-deploy-model-trans2",
        "params": {"temperature": 0.5, "max_tokens": 512},
        "key_ref": REF,
    }
    if provider == "ollama":
        entry["params"]["base_url"] = "http://localhost:11434"
    preset_path.write_text(json.dumps({"presets": [entry]}), encoding="utf-8")
    app = create_external_app(
        RegistryProvider(_raw(token)), file_preset_loader(preset_path), backend
    )
    return (TestClient(app), token)


def _auth(token):
    return {"Authorization": "Bearer " + token}


class _ReplyBackend:
    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def complete(self, route, preset, messages, params, passthrough=None):
        self.calls.append((route, preset, messages, params, passthrough))
        return self.reply


def _patch_upstream(monkeypatch, seen, payload):
    class _FakeResp:
        status_code = 200

        def json(self):
            return payload

    def _fake_post(self, url, headers=None, json=None, **kw):
        seen.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeResp()

    monkeypatch.setattr(httpx.Client, "post", _fake_post, raising=True)


# -- shim: open roles ----------------------------------------------------------

@pytest.mark.parametrize("role", ["tool", "developer", "function", "mystery-role"])
def test_trans2_open_roles_admitted(role):
    req = parse_chat_request(
        {"model": "model-a", "messages": [{"role": role, "content": "x"}]}
    )
    assert isinstance(req, InternalReq)
    assert req.to_raw_request()["messages"][0] == {"role": role, "content": "x"}


# -- shim: content shapes ------------------------------------------------------

def test_trans2_content_list_parts_admitted():
    parts = [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "SENTINEL"}}]
    req = parse_chat_request(
        {"model": "model-a", "messages": [{"role": "user", "content": parts}]}
    )
    assert req.messages[0].content == parts
    assert req.to_raw_request()["messages"][0]["content"] == parts


def test_trans2_content_null_with_tool_calls_admitted():
    msg = {"role": "assistant", "content": None, "tool_calls": list(TOOL_CALLS)}
    req = parse_chat_request({"model": "model-a", "messages": [msg]})
    assert req.messages[0].content is None
    assert req.to_raw_request()["messages"][0] == msg


def test_trans2_content_empty_string_with_function_call_admitted():
    msg = {"role": "assistant", "content": "", "function_call": {"name": "f", "arguments": "{}"}}
    req = parse_chat_request({"model": "model-a", "messages": [msg]})
    assert req.to_raw_request()["messages"][0] == msg


@pytest.mark.parametrize("content", [None, ""])
def test_trans2_bare_empty_content_without_tools_fail_closed_500(content):
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(
            {"model": "model-a", "messages": [{"role": "user", "content": content}]}
        )
    assert ei.value.status == 500
    assert ei.value.code == "internal"


def test_trans2_bare_empty_content_without_tools_http_500(tmp_path, monkeypatch):
    backend = _ReplyBackend("TRANS2-NEVER-REACHED")
    client, token = _setup(tmp_path, monkeypatch, backend)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": ""}]},
    )
    assert resp.status_code == 500
    assert backend.calls == []


# -- shim: extras preserved ----------------------------------------------------

def test_trans2_extras_roundtrip_byte_identical():
    msg = {
        "role": "tool",
        "content": "18C",
        "tool_call_id": "call-9",
        "id": "msg-foreign-1",
        "name": "weather",
    }
    req = parse_chat_request({"model": "model-a", "messages": [msg]})
    assert req.messages[0].extras == {
        "tool_call_id": "call-9",
        "id": "msg-foreign-1",
        "name": "weather",
    }
    assert req.to_raw_request()["messages"][0] == msg


def test_trans2_wire_keeps_tool_context():
    msgs = (
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content=None,
            extras={"tool_calls": list(TOOL_CALLS)},
        ),
        ChatMessage(
            role="tool",
            content="18C",
            extras={"tool_call_id": "call-9"},
        ),
    )
    wired = _wire_messages(msgs)
    assert wired == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "tool_calls": list(TOOL_CALLS)},
        {"role": "tool", "content": "18C", "tool_call_id": "call-9"},
    ]


def test_trans2_wire_plain_shape_unchanged():
    wired = _wire_messages((ChatMessage(role="user", content="hi"),))
    assert wired == [{"role": "user", "content": "hi"}]


# -- shim: total-only DoS guard --------------------------------------------------

def test_trans2_single_large_string_passes_no_per_message_cap():
    big = "A" * 40000  # past the old 32 KiB per-string figure, under the total cap
    req = parse_chat_request(
        {"model": "model-a", "messages": [{"role": "user", "content": big}]}
    )
    assert req.messages[0].content == big
    assert req.to_raw_request()["messages"][0]["content"] == big


def test_trans2_oversize_array_still_400():
    big = "B" * (300 * 1024)
    with pytest.raises(InterfaceError) as ei:
        parse_chat_request(
            {"model": "model-a", "messages": [{"role": "user", "content": big}]}
        )
    assert ei.value.status == 400


# -- verification matrix: all mock, end to end -----------------------------------

def test_trans2_first_round_tools_200(tmp_path, monkeypatch):
    seen = []
    _patch_upstream(
        monkeypatch,
        seen,
        {
            "choices": [{"message": {"role": "assistant", "content": "sunny", "tool_calls": TOOL_CALLS}}],
            "usage": dict(UPSTREAM_USAGE),
        },
    )
    client, token = _setup(tmp_path, monkeypatch, PresetRoutingBackend())
    tools = [{"type": "function", "function": {"name": "get_weather"}}]
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={
            "model": "primary",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": tools,
            "tool_choice": "auto",
        },
    )
    assert resp.status_code == 200
    out = resp.json()
    assert out["choices"][0]["message"]["tool_calls"] == TOOL_CALLS
    assert out["usage"] == UPSTREAM_USAGE
    assert seen[0]["json"]["tools"] == tools
    assert seen[0]["json"]["tool_choice"] == "auto"


def test_trans2_multiround_tool_call_id_byte_roundtrip(tmp_path, monkeypatch):
    seen = []
    _patch_upstream(
        monkeypatch,
        seen,
        {"choices": [{"message": {"role": "assistant", "content": "done"}}]},
    )
    client, token = _setup(tmp_path, monkeypatch, PresetRoutingBackend())
    history = [
        {"role": "user", "content": "weather?"},
        {"role": "assistant", "content": None, "tool_calls": TOOL_CALLS},
        {"role": "tool", "content": "18C", "tool_call_id": "call-9"},
    ]
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": history},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"] == {"role": "assistant", "content": "done"}
    upstream_messages = seen[0]["json"]["messages"]
    assert json.dumps(upstream_messages, sort_keys=True) == json.dumps(history, sort_keys=True)
    tool_msgs = [m for m in upstream_messages if m["role"] == "tool"]
    assert len(tool_msgs) == 1 and tool_msgs[0]["tool_call_id"] == "call-9"


def test_trans2_content_array_and_null_history_200(tmp_path, monkeypatch):
    seen = []
    _patch_upstream(
        monkeypatch,
        seen,
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
    )
    client, token = _setup(tmp_path, monkeypatch, PresetRoutingBackend())
    parts = [{"type": "text", "text": "look"}]
    history = [
        {"role": "user", "content": parts},
        {"role": "assistant", "content": None, "tool_calls": TOOL_CALLS},
        {"role": "tool", "content": parts, "tool_call_id": "call-9"},
    ]
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": history},
    )
    assert resp.status_code == 200
    assert seen[0]["json"]["messages"] == history


def test_trans2_minimal_greeting_with_foreign_key_not_400(tmp_path, monkeypatch):
    backend = _ReplyBackend(BackendReply(text="hi there"))
    client, token = _setup(tmp_path, monkeypatch, backend)
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": [{"role": "user", "content": "hi", "id": "msg-1"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"] == {"role": "assistant", "content": "hi there"}


def test_trans2_wire_forwards_extras_to_upstream_payload(tmp_path, monkeypatch):
    seen = []
    _patch_upstream(
        monkeypatch,
        seen,
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
    )
    client, token = _setup(tmp_path, monkeypatch, PresetRoutingBackend())
    history = [
        {"role": "user", "content": "hi", "id": "msg-1", "name": "u"},
        {"role": "assistant", "content": "", "function_call": {"name": "f", "arguments": "{}"}},
    ]
    resp = client.post(
        "/rem/primary/v1/chat/completions",
        headers=_auth(token),
        json={"model": "primary", "messages": history},
    )
    assert resp.status_code == 200
    assert seen[0]["json"]["messages"] == history
