"""THIN-OVERLAY-REM-1: thin standard requests carry the Rem soul overlay.

``scripts/serve_sa_thin.py`` wires ``build_rem_soul_overlay`` into BOTH
``create_external_app`` calls (reject + business), so marker-free standard
requests reach upstream with the full ``personas/agent_rem.md`` persona as
the leading system message. The overlay is fail-open: any fault returns
None and the gateway falls back to the raw parsed input (existing
overlay-degraded path, never a 500).

Pure offline: TestClient in-process only, no server, no ports, no real
keys, no real provider calls. Upstream HTTP is monkeypatched at the httpx
layer (the ``test_overlay_trans.py`` idiom). Every key value here is an
uppercase hyphenated SENTINEL.
"""

from __future__ import annotations

import json
import logging
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

import scripts.serve_sa_thin as thin_mod
from src.external_gateway.openai_shim import ChatMessage
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider

OWNER = "thin-overlay-owner"
REF = "THIN_OVERLAY_REF"
SENTINEL = "THIN-OVERLAY-SENTINEL-KEY"
FAKE_REPLY = "thin-rem-fake-reply"

#: Marks "caller did not pass an overlay" (None is a real fail-open value).
_UNSET = object()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("THIN-OVERLAY: tests must not dial out (socket.create_connection blocked)")

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
                "client_id": "client-thin-overlay",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _write_presets(path, provider="deepseek", model="do-not-deploy-model-thin-overlay"):
    entry = {
        "name": "primary",
        "provider": provider,
        "model": model,
        "params": {"temperature": 0.5, "max_tokens": 512},
        "key_ref": REF,
    }
    path.write_text(json.dumps({"presets": [entry]}), encoding="utf-8")


def _sandbox_files(tmp_path):
    registry_path = tmp_path / "registry.json"
    presets_path = tmp_path / "presets.json"
    release_path = tmp_path / "release.json"
    return (registry_path, presets_path, release_path)


class _FakeResp:
    status_code = 200

    def json(self):
        return {"choices": [{"message": {"role": "assistant", "content": FAKE_REPLY}}]}


def _setup_thin_app(tmp_path, monkeypatch, seen, soul_overlay_fn=_UNSET):
    """Build the real thin app (both apps share the overlay) + TestClient.

    Upstream HTTP is captured into ``seen`` (no dial-out). ``soul_overlay_fn``
    defaults to the production default (omit); pass an explicit overlay
    (e.g. missing-file) to exercise fail-open through the full stack.
    """
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    registry_path.write_text(json.dumps(_raw(token)), encoding="utf-8")
    _write_presets(presets_path)
    release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")

    def _fake_post(self, url, headers=None, json=None, **kw):
        seen.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeResp()

    monkeypatch.setattr(httpx.Client, "post", _fake_post, raising=True)
    if soul_overlay_fn is _UNSET:
        app = thin_mod.build_thin_app(str(registry_path), str(presets_path),
                                      release_path=str(release_path))
    else:
        app = thin_mod.build_thin_app(str(registry_path), str(presets_path),
                                      release_path=str(release_path),
                                      soul_overlay_fn=soul_overlay_fn)
    return (TestClient(app), token)


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _persona_text():
    text = thin_mod._load_rem_persona_text()
    assert isinstance(text, str) and text
    return text


# -- 1: pure-user messages get the persona front-inserted --------------------


def test_pure_user_messages_front_insert_persona():
    overlay = thin_mod.build_rem_soul_overlay()
    params = {"temperature": 0.5, "max_tokens": 8}
    msgs = (ChatMessage(role="user", content="hi", extras={}),)
    out = overlay(msgs, params)
    assert out is not None
    overlaid, out_params = out
    assert out_params == params
    assert len(overlaid) == 2
    assert overlaid[0].role == "system"
    assert overlaid[0].extras == {}
    assert "羅茲瓦爾公館" in overlaid[0].content
    assert "雷姆" in overlaid[0].content
    assert overlaid[1] == msgs[0]


# -- 2: an existing leading system message is prefixed, not duplicated -------


def test_existing_system_message_prefixed_not_duplicated():
    overlay = thin_mod.build_rem_soul_overlay()
    persona = _persona_text()
    params = {"temperature": 0.5, "max_tokens": 8}
    msgs = (
        ChatMessage(role="system", content="old-sys", extras={}),
        ChatMessage(role="user", content="hi", extras={}),
    )
    out = overlay(msgs, params)
    assert out is not None
    overlaid, _ = out
    assert len(overlaid) == 2
    assert overlaid[0].role == "system"
    assert overlaid[0].content == persona + "\n\n" + "old-sys"
    assert overlaid[1] == msgs[1]


# -- 3: missing persona file fails open (None + raw messages + HTTP 200) ----


def test_missing_persona_fail_open_raw_messages_http_200(tmp_path, monkeypatch, caplog):
    overlay = thin_mod.build_rem_soul_overlay(
        persona_path=str(tmp_path / "no-such-persona.md")
    )
    params = {"temperature": 0.5, "max_tokens": 8}
    msgs = (ChatMessage(role="user", content="hi", extras={}),)
    with caplog.at_level(logging.ERROR):
        assert overlay(msgs, params) is None
    assert "overlay-persona-unreadable" in caplog.text

    seen = []
    client, token = _setup_thin_app(tmp_path, monkeypatch, seen,
                                    soul_overlay_fn=overlay)
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/rem/primary/v1/chat/completions",
            headers=_auth(token),
            json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == FAKE_REPLY
    assert len(seen) == 1
    assert seen[0]["json"]["messages"] == [{"role": "user", "content": "hi"}]
    assert "overlay-applied" not in caplog.text


# -- 4: standard request carries Rem overlay upstream + overlay-applied log --


def test_thin_standard_request_carries_rem_overlay(tmp_path, monkeypatch, caplog):
    seen = []
    client, token = _setup_thin_app(tmp_path, monkeypatch, seen)
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/rem/primary/v1/chat/completions",
            headers=_auth(token),
            json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == FAKE_REPLY
    assert len(seen) == 1
    upstream_messages = seen[0]["json"]["messages"]
    assert upstream_messages[0]["role"] == "system"
    assert "羅茲瓦爾公館" in upstream_messages[0]["content"]
    assert "雷姆" in upstream_messages[0]["content"]
    assert upstream_messages[1:] == [{"role": "user", "content": "hi"}]
    assert "overlay-applied" in caplog.text
    assert "overlay-degraded" not in caplog.text
