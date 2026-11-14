"""SA-WIRING-OFFLINE-1 T9 offline wiring tests: dialog factory + outer checks.

Decision-complete scope (SA-WIRING-OFFLINE-1): this file is new; product
edits are ``src/soul_access/wiring.py`` (per-request dialog factory +
generate factory seam + mount current-turn binding + quoted-reply decode),
``src/external_gateway/app.py`` (envelope-vs-admission consistency, 403
with zero forward) and ``scripts/run_server.py`` (launcher loop capture +
factory assembly); candidates are ``configs/sa/*.offline.json`` (never
installed as production). Frozen files (bridge/api/release/schema/SA-0
台帳/判準) untouched.

All test names carry the t9 marker: `pytest -q tests/soul_access -k t9`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created by any test here: the factory's
default submitter is either bypassed with an injected fake submit/future or
refused before touching a loop via a monkeypatched running-loop probe).
bus/llm/middleware are fakes; the release file is always a self-created tmp
file (never data/**, never the pinned production path). Zero subprocess
call sites, zero real LLM calls, zero cost.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clients.voice_companion.session_store import SessionStore  # noqa: E402
from src.external_gateway.app import _sa_live_dispatch  # noqa: E402
from src.external_gateway.app import create_external_app  # noqa: E402
from src.external_gateway.app import file_preset_loader  # noqa: E402
from src.interfaces.config import resolve_route  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.credentials import issue_credential  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.interfaces.identity import authenticate  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

OWNER = "t9-offline-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"
MODEL = "do-not-deploy-model-t9"


def _identity(**over):
    kw = {
        "request_id": "req-t9-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t9-0001",
        "idempotency_key": "key-t9-0001",
    }
    kw.update(over)
    return sa_schema.RequestIdentity(**kw)


def _dedup(**over):
    kw = {
        "principal_id": OWNER,
        "agent_id": AGENT,
        "scope": SCOPE,
        "conversation_id": "dm",
        "branch_id": "root",
        "idempotency_key": "key-t9-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _snapshot(**over):
    kw = {
        "identity": _identity(),
        "dedup": _dedup(),
        "backend_alias": "primary",
        "model": MODEL,
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-12T00:00:00Z",
        "personality_version": "personality-t9-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-11T17:00:00-05:00",
        "inner_life_state": "inner-t9-v1",
        "projection_state": "projection-t9-v1",
        "recall_version": "recall-t9-v1",
        "recall_items": ("recall-t9-item-1",),
        "history_pool_version": "pool-t9-v1",
        "clean_history": ("hist-t9-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


def _envelope(snapshot=None, messages=(("user", "t9 offline probe"),)):
    snap = snapshot if snapshot is not None else _snapshot()
    return sa_api.build_envelope(
        snapshot=snap,
        messages=messages,
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _write_release(path: Path, payload) -> Path:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _FakeFuture:
    """Controllable future stand-in: no loop, no thread, no network."""

    def __init__(self, value=None, fault=None):
        self.value = value
        self.fault = fault

    def result(self):
        if self.fault is not None:
            raise self.fault
        return self.value


class _FakeBackend:
    """Sync bottom backend stand-in: records calls, returns fixed text."""

    def __init__(self, text="t9-offline-reply"):
        self.text = text
        self.calls = []

    def complete(self, *, messages, model, max_tokens, temperature, max_retries):
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "max_retries": max_retries,
            }
        )
        if isinstance(self.text, Exception):
            raise self.text
        return self.text


def _ok_submit(loop, coro_or_value, *, _sentinel=None):
    return _FakeFuture(coro_or_value if not hasattr(coro_or_value, "close") else None)


def _value_submit(loop, coro_or_value):
    return _FakeFuture(coro_or_value)


class _StubApp:
    """Minimal :8000 stand-in: state loop slot + route recorder."""

    def __init__(self, live):
        self.state = SimpleNamespace(_soul_access_live=live)
        self.routes = []

    def add_api_route(self, path, handler, methods=None, name=None):
        self.routes.append((path, handler, tuple(methods or ()), name))


# -- T9-1: per-request factory on primary (one attempt, max_retries=0) -------


def test_t9_factory_primary_single_attempt_max_retries_zero():
    backend = _FakeBackend()
    llm = SimpleNamespace(backend=backend)
    sentinel = object()
    seen = {}

    def _submit(loop, coro):
        seen["loop"] = loop
        return _FakeFuture(coro)

    factory = wiring.build_dialog_factory(llm=llm, loop=sentinel, submit=_submit)
    snap = _snapshot()
    dialog = factory(snap)
    assert dialog.sa_dialog_backend_alias == "primary"
    assert dialog.sa_dialog_model == MODEL
    text = dialog((("user", "t9 probe"),), agent_id=AGENT, request_id="req-t9-0001")
    assert text == "t9-offline-reply"
    assert seen["loop"] is sentinel
    assert len(backend.calls) == 1
    call = backend.calls[0]
    assert call["model"] == MODEL
    assert call["max_retries"] == 0
    assert call["max_tokens"] == 8
    assert call["temperature"] == 0.5


def test_t9_factory_non_primary_request_local_no_fallback():
    primary_backend = _FakeBackend(text="t9-primary-must-stay-quiet")
    llm = SimpleNamespace(backend=primary_backend)
    made = []

    def _resolve(alias):
        assert alias == "minimax-m3.1-flash"
        backend = _FakeBackend(text="t9-alias-reply")
        made.append(backend)
        return backend

    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), resolve_backend=_resolve, submit=_value_submit
    )
    first = factory(_snapshot(backend_alias="minimax-m3.1-flash"))
    second = factory(_snapshot(backend_alias="minimax-m3.1-flash"))
    assert first((("user", "one"),), agent_id=AGENT, request_id="r1") == "t9-alias-reply"
    assert second((("user", "two"),), agent_id=AGENT, request_id="r2") == "t9-alias-reply"
    assert len(made) == 2
    assert made[0] is not made[1]
    assert len(made[0].calls) == 1
    assert len(made[1].calls) == 1
    assert primary_backend.calls == []


def test_t9_factory_missing_mapping_refuses_no_fallback_to_primary():
    primary_backend = _FakeBackend(text="t9-primary-must-stay-quiet")
    llm = SimpleNamespace(backend=primary_backend)
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), resolve_backend=None, submit=_value_submit
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        factory(_snapshot(backend_alias="glm-5.3-flash"))
    assert exc_info.value.status == 500
    assert primary_backend.calls == []

    def _resolve_none(alias):
        return None

    factory_none = wiring.build_dialog_factory(
        llm=llm, loop=object(), resolve_backend=_resolve_none, submit=_value_submit
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        factory_none(_snapshot(backend_alias="glm-5.3-flash"))
    assert exc_info.value.status == 500
    assert primary_backend.calls == []


def test_t9_factory_empty_and_fault_single_attempt_no_resend():
    llm = SimpleNamespace(backend=_FakeBackend(text=""))
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_value_submit
    )
    dialog = factory(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        dialog((("user", "hi"),), agent_id=AGENT, request_id="req-t9-0001")
    assert exc_info.value.status == 500
    assert len(llm.backend.calls) == 1

    failing = SimpleNamespace(backend=_FakeBackend(text=RuntimeError("t9 boom")))
    factory_fail = wiring.build_dialog_factory(
        llm=failing, loop=object(), submit=_value_submit
    )
    dialog_fail = factory_fail(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        dialog_fail((("user", "hi"),), agent_id=AGENT, request_id="req-t9-0001")
    assert exc_info.value.status == 500
    assert len(failing.backend.calls) == 1


def test_t9_factory_same_loop_misuse_refuses_without_deadlock(monkeypatch):
    backend = _FakeBackend()
    llm = SimpleNamespace(backend=backend)
    sentinel = object()
    factory = wiring.build_dialog_factory(llm=llm, loop=sentinel)
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: sentinel)
    dialog = factory(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        dialog((("user", "hi"),), agent_id=AGENT, request_id="req-t9-0001")
    assert exc_info.value.status == 500
    assert backend.calls == []


def test_t9_generate_factory_dialog_mutex_at_build(tmp_path):
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_generate(
            bus=bus,
            llm=llm,
            middleware=middleware,
            session_store=store,
            dialog=lambda messages, **kw: "x",
            dialog_factory=lambda snapshot: (lambda messages, **kw: "x"),
        )
    assert exc_info.value.status == 500


# -- T9-2: mount binds the current turn; warm sessions keep live input ------


def test_t9_mount_binds_current_turn_warm_session_keeps_live_input(tmp_path):
    store = SessionStore(data_dir=tmp_path / "sessions")
    store.append_turn(AGENT, "user_bryan", "user", "t9 warm history", "telegram")
    seen = {}

    def _dialog(messages, *, agent_id=None, request_id=None):
        seen["messages"] = tuple(messages)
        seen["agent_id"] = agent_id
        seen["request_id"] = request_id
        return "t9-live-reply"

    bus, llm, middleware = object(), object(), object()
    generate = wiring.build_generate(
        bus=bus,
        llm=llm,
        middleware=middleware,
        session_store=store,
        dialog_factory=lambda snapshot: _dialog,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    app = _StubApp(live)
    route = wiring.mount_live_route(
        app, live=live, generate=generate, release_path=str(release_path)
    )
    snap = _snapshot()
    envelope = _envelope(
        snapshot=snap,
        messages=(("user", "t9 warm history"), ("user", "t9 live turn"),),
    )
    assert route(sa_api.envelope_to_body(envelope)) == "t9-live-reply"
    contents = [content for _, content in seen["messages"]]
    assert "t9 live turn" in contents
    assert "t9 warm history" in contents
    raw = json.loads(wiring.session_path(store, AGENT).read_text(encoding="utf-8"))
    assert raw["turns"][-1]["channel"] == "sa-live"
    assert raw["turns"][-1]["content"] == "t9-live-reply"
    assert len(generate.trace) == 1
    assert generate.pending == {}


def test_t9_end_to_end_factory_primary_via_mount_single_attempt(tmp_path):
    backend = _FakeBackend(text="t9-e2e-reply")
    llm = SimpleNamespace(backend=backend)
    sentinel = object()
    factory = wiring.build_dialog_factory(
        llm=llm, loop=sentinel, submit=_value_submit
    )
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, middleware = object(), object()
    generate = wiring.build_generate(
        bus=bus,
        llm=llm,
        middleware=middleware,
        session_store=store,
        dialog_factory=factory,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    app = _StubApp(live)
    route = wiring.mount_live_route(
        app, live=live, generate=generate, release_path=str(release_path)
    )
    assert route(sa_api.envelope_to_body(_envelope())) == "t9-e2e-reply"
    assert len(backend.calls) == 1
    assert backend.calls[0]["max_retries"] == 0
    assert backend.calls[0]["model"] == MODEL
    assert len(generate.trace) == 1
    assert generate.pending == {}


# -- T9-3: outer consistency (403, zero forward; 403 never becomes 500) -----

_REF = "SA_T9_KEY"
REF_SENTINEL = "t9-offline-sentinel"


def _outer_setup(tmp_path):
    import os

    os.environ[_REF] = REF_SENTINEL
    token, _digest = issue_credential()
    preset_path = tmp_path / "presets.json"
    preset_path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": "primary",
                        "provider": "deepseek",
                        "model": MODEL,
                        "params": {"temperature": 0.5, "max_tokens": 8},
                        "key_ref": _REF,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    raw = {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": [
            {
                "soul_alias": "rem",
                "backend_alias": "primary",
                "agent_id": AGENT,
                "public_model_id": MODEL,
                "backend_ref": "port-a",
                "context_window_tokens": 4096,
            }
        ],
        "clients": [
            {
                "client_id": "client-t9",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["rem/primary"],
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }
    provider = RegistryProvider(raw)
    snapshot = provider.current()
    client = authenticate(token, snapshot)
    route = resolve_route(snapshot, "rem", "primary")
    preset_obj = file_preset_loader(str(preset_path))("primary")
    calls = []
    no_calls_backend = SimpleNamespace(
        complete=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("legacy backend must stay untouched")
        )
    )
    app = create_external_app(
        provider, file_preset_loader(str(preset_path)), no_calls_backend
    )
    assert app is not None

    def _handler(envelope, *, route=None, preset=None):
        calls.append(envelope)
        return "t9-live"

    return SimpleNamespace(
        client=client, route=route, preset_obj=preset_obj, calls=calls,
        handler=_handler,
    )


def _dispatch(setup, body):
    return _sa_live_dispatch(
        body,
        soul="rem",
        preset_name="primary",
        endpoint="chat/completions",
        req_id="req-t9-0001",
        route=setup.route,
        preset_obj=setup.preset_obj,
        soul_live_handler=setup.handler,
        client=setup.client,
    )


def test_t9_outer_matching_envelope_admits_200_single_forward(tmp_path):
    setup = _outer_setup(tmp_path)
    body = sa_api.envelope_to_body(_envelope())
    response = _dispatch(setup, body)
    assert response.status_code == 200
    assert len(setup.calls) == 1
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["model"] == MODEL
    assert payload["choices"][0]["message"]["content"] == "t9-live"


def test_t9_outer_drift_refuses_403_zero_forward(tmp_path):
    drifts = [
        ("principal", _dedup(principal_id="someone-else"), "primary", MODEL, None),
        ("scope", _dedup(scope="other_scope"), "primary", MODEL, None),
        ("agent", _dedup(agent_id="agent_mai"), "primary", MODEL, None),
        ("alias", _dedup(), "minimax-m3.1-flash", MODEL, None),
        ("model", _dedup(), "primary", "do-not-deploy-other", None),
        ("body-model", _dedup(), "primary", MODEL, "tampered-model"),
    ]
    for label, dedup, alias, model, body_model in drifts:
        setup = _outer_setup(tmp_path)
        snap = _snapshot(dedup=dedup, backend_alias=alias, model=model)
        body = sa_api.envelope_to_body(_envelope(snapshot=snap))
        if body_model is not None:
            body["model"] = body_model
        response = _dispatch(setup, body)
        if label == "body-model":
            assert response.status_code == 400, label
        else:
            assert response.status_code == 403, label
        assert setup.calls == [], label


# -- T9-4: thin decodes transport-quoted replies ----------------------------


def _thin(release_path, sender=None):
    return wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )


def test_t9_thin_quoted_reply_decoded_plain_untouched(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    assert _thin(release_path, sender=lambda url, body: '"t9-quoted"')(_envelope()) == "t9-quoted"
    assert _thin(release_path, sender=lambda url, body: "t9-plain")(_envelope()) == "t9-plain"


# -- T9-5: release matrix + open -> close -> open hot-read -------------------


def test_t9_release_matrix_closed_missing_bad_refuse_403(tmp_path):
    for payload in ("not-json{{{", "[1, 2, 3]", '{"status": "OPEN"}', '{"other": 1}'):
        release_path = _write_release(tmp_path / "release.json", payload)
        sent = []
        fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "x")
        with pytest.raises(sa_bridge.LiveRefused) as exc_info:
            fwd(_envelope())
        assert exc_info.value.status == 403
        assert sent == []
    missing = tmp_path / "release-missing.json"
    assert not missing.exists()
    with pytest.raises(sa_bridge.LiveRefused):
        _thin(missing, sender=lambda url, body: "x")(_envelope())


def test_t9_release_open_close_open_hot_read(tmp_path):
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    dialog_calls = []

    def _dialog(messages, *, agent_id=None, request_id=None):
        dialog_calls.append((agent_id, request_id))
        return "t9-hot"

    generate = wiring.build_generate(
        bus=bus, llm=llm, middleware=middleware,
        session_store=store, dialog=_dialog,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    assert handler(_envelope()) == "t9-hot"
    release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert exc_info.value.status == 403
    calls_after_close = len(dialog_calls)
    release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    assert handler(_envelope()) == "t9-hot"
    assert len(dialog_calls) == calls_after_close + 1


# -- T9-6: bidirectional mutations (relaxed / tightened / revert green) -----


def test_t9_mutation_relaxed_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Relaxed mutant: forced-open gate forwards while closed (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sent = []
    fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "t9-red")
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: True)
    assert fwd(_envelope()) == "t9-red"  # RED: closed gate ignored.
    if len(sent) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed-open did not forward")
    monkeypatch.undo()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert exc_info.value.status == 403


def test_t9_mutation_tightened_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Tightened mutant: forced-closed gate refuses while open (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sent = []
    fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "t9-red")
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: False)
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert exc_info.value.status == 403  # RED: open gate ignored.
    if sent:
        pytest.fail("MUTATION DUD -- STOP, report: tightened-closed leaked a send")
    monkeypatch.undo()
    assert fwd(_envelope()) == "t9-red"


def test_t9_mutation_reverted_full_stack_green(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    store = SessionStore(data_dir=tmp_path / "sessions")
    backend = _FakeBackend(text="t9-green")
    llm = SimpleNamespace(backend=backend)
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_value_submit
    )
    bus, middleware = object(), object()
    generate = wiring.build_generate(
        bus=bus, llm=llm, middleware=middleware,
        session_store=store, dialog_factory=factory,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    assert sa_release.is_live_inbound_open(str(release_path)) is True
    assert handler(_envelope()) == "t9-green"
    assert len(backend.calls) == 1
    assert backend.calls[0]["max_retries"] == 0
    assert len(generate.trace) == 1
