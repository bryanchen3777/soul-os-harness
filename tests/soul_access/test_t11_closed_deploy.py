"""SA-THAW-DEPLOY-1 T11 closed-door refusal paths (sandbox only).

Decision-complete scope (SA-THAW-DEPLOY-1, second closed-door segment): this
file is new; no tracked product file is touched. The gate stays closed
(``release/sa.json`` keeps ``{"status": "closed"}``); nothing is installed,
nothing is unfrozen, nothing is deployed. All names are synthetic T11
fixtures (``t11-formal-owner`` / ``ruka`` / ``SA_T11_SHARED``); the T10
test identity is referenced only as a foreign principal that must be
refused, never admitted. Transport is memory-only (in-process ASGI, never a
port); the only bottom-I/O seam replaced is the lowest HTTP transport
inside ``OpenAIBackend.complete`` (a counting stand-in with a fixed 200
reply). Storage is sandbox-only: self-created tmp preset / registry /
release files (never production state, never the pinned path). The real
``OLLAMA_API_KEY`` value is never read: only its presence bit is observed,
and the sandbox preset files never name it.

All test names carry the t11 marker: `pytest -q tests/soul_access -k t11`
must collect > 0. Sync tests only.

Coverage (one closed-door refusal per group, zero real inference):
  a. missing model mapping refuses install (short / extra alias set,
     non-openai provider, unknown alias at resolve, unknown preset name
     at load, snapshot-vs-preset model drift at the outward entry);
  b. missing formal identity refuses (no owner / no clients / no digest
     at install; wrong credential and foreign test principal at entry);
  c. missing key_ref refuses (absent field, unset env var,
     plaintext-looking ref);
  d. shared URL + shared key keeps four distinct per-alias models
     (production preset direction: one URL, one key, four models);
  e. status=closed forbids every inference call (thin forward refused,
     live handler refused, dialog seam untouched, posts frozen).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import os
import queue
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.external_gateway.app import create_external_app  # noqa: E402
from src.external_gateway.app import file_preset_loader  # noqa: E402
from src.interfaces.config import resolve_route  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.interfaces.schema import InterfaceError  # noqa: E402
from src.llm import proxy as llm_proxy  # noqa: E402,F401 (patch target must import)
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

# -- module-level loops (import time; same idiom as the T10 file) --------------

_T11_LOOP_A = asyncio.new_event_loop()
_T11_LOOP_C = asyncio.new_event_loop()


class _Runner:
    """Own-thread loop driver: queue handoff, never a cross-thread wakeup."""

    def __init__(self, loop, name):
        self.loop = loop
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self.thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        while True:
            coro, out_q = self.jobs.get()
            try:
                out_q.put((True, self.loop.run_until_complete(coro)))
            except BaseException as exc:
                out_q.put((False, exc))

    def run(self, coro):
        out_q = queue.Queue(maxsize=1)
        self.jobs.put((coro, out_q))
        ok, val = out_q.get()
        if ok:
            return val
        raise val


_OUTWARD_RUNNER = _Runner(_T11_LOOP_A, "t11-outward")
_INFER_RUNNER = _Runner(_T11_LOOP_C, "t11-infer")

# Real transport handles, saved before any per-test patch replaces the class
# attribute on the shared httpx module object.
_REAL_ASYNC_CLIENT = httpx.AsyncClient
_REAL_ASGI_TRANSPORT = httpx.ASGITransport

assert llm_proxy.OpenAIBackend is not None

# -- synthetic T11 fixtures (never the T10 identity) ---------------------------

OWNER = "t11-formal-owner"
FOREIGN_PRINCIPAL = "t10-formal-owner"
SOUL = "ruka"
AGENT = "agent_ruka"
SCOPE = "owner_private"
TOKEN = "t11-formal-sandbox-token"
WRONG_TOKEN = "t11-wrong-token"

ALIASES = (
    "primary",
    "minimax-m3.1-flash",
    "glm-5.3-flash",
    "mimo-v2.6-flash",
)
MODELS = {
    "primary": "sa-t11-primary",
    "minimax-m3.1-flash": "sa-t11-minimax",
    "glm-5.3-flash": "sa-t11-glm",
    "mimo-v2.6-flash": "sa-t11-mimo",
}
SHARED_URL = "https://sa-t11-shared.invalid/v1/chat/completions"
SHARED_REF = "SA_T11_SHARED"
SHARED_KEY = "t11-sandbox-shared-key"
LLM_ORIGINAL_KEY = "t11-llm-original-key"
LLM_ORIGINAL_URL = "https://sa-t11-llm-original.invalid/v1/chat/completions"

FORMAL_DIGEST = digest_of(TOKEN)

_REPLY = "t11-formal-reply"

_T11_POSTS: list = []


def _identity(req, key):
    return sa_schema.RequestIdentity(
        request_id=req,
        conversation_id="dm",
        branch_id="root",
        turn_id="turn-%s" % req,
        idempotency_key=key,
    )


def _dedup(agent, req, key, principal=OWNER):
    return sa_schema.DedupKey(
        principal_id=principal,
        agent_id=agent,
        scope=SCOPE,
        conversation_id="dm",
        branch_id="root",
        idempotency_key=key,
    )


def _snapshot_for(alias, model, n, principal=OWNER):
    req = "req-t11-%04d" % n
    key = "key-t11-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(req, key),
        dedup=_dedup(AGENT, req, key, principal),
        backend_alias=alias,
        model=model,
        params=(("temperature", "0.5"), ("max_tokens", "8"),),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t11-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-t11-v1",
        projection_state="projection-t11-v1",
        recall_version="recall-t11-v1",
        recall_items=("recall-t11-item-1",),
        history_pool_version="pool-t11-v1",
        clean_history=("hist-t11-item-1",),
    )


def _envelope(snapshot=None, messages=(("user", "t11 probe"),)):
    snap = (
        snapshot
        if snapshot is not None
        else _snapshot_for("primary", MODELS["primary"], 1)
    )
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


def _preset_entries(base_url=SHARED_URL, key_ref=SHARED_REF):
    return [
        {
            "name": alias,
            "provider": "openai",
            "model": MODELS[alias],
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": base_url,
            },
            "key_ref": key_ref,
        }
        for alias in ALIASES
    ]


def _write_presets(path: Path, entries) -> Path:
    path.write_text(json.dumps({"presets": entries}), encoding="utf-8")
    return path


def _registry_raw(token_digest=FORMAL_DIGEST):
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-t11"],
        "routes": [
            {
                "soul_alias": SOUL,
                "backend_alias": alias,
                "agent_id": AGENT,
                "public_model_id": "sa-t11-%s-%s" % (SOUL, alias),
                "backend_ref": "port-t11",
                "context_window_tokens": 4096,
            }
            for alias in ALIASES
        ],
        "clients": [
            {
                "client_id": "client-t11-formal",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (SOUL, alias) for alias in ALIASES],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _sha_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _formal_env(monkeypatch):
    monkeypatch.setenv(SHARED_REF, SHARED_KEY)


def _formal_loader(presets_path):
    return file_preset_loader(str(presets_path))


class _Fake200:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{"message": {"content": _REPLY}, "finish_reason": "stop"}],
            "usage": {},
        }


class _CountingAsyncClient:
    """Bottom-transport stand-in: one fixed 200 reply, every post logged."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        body = dict(json or {})
        try:
            auth = dict(headers or {}).get("Authorization", "")
        except Exception:
            auth = ""
        _T11_POSTS.append({"url": auth and url, "auth": auth, "model": body.get("model")})
        return _Fake200()


def _patch_bottom_transport(monkeypatch):
    _T11_POSTS.clear()
    monkeypatch.setattr("src.llm.proxy.httpx.AsyncClient", _CountingAsyncClient)


def _submit_on_c(loop, coro):
    try:
        value = _INFER_RUNNER.run(coro)
    except BaseException as exc:
        fut = concurrent.futures.Future()
        fut.set_exception(exc)
        return fut
    fut = concurrent.futures.Future()
    fut.set_result(value)
    return fut


def _run_a(coro):
    return _T11_LOOP_A.run_until_complete(coro)


def _build_backends(monkeypatch, presets_path):
    _formal_env(monkeypatch)
    loader = _formal_loader(presets_path)
    presets = {alias: loader(alias) for alias in ALIASES}
    return wiring.build_preset_backends(presets)


def _build_outward(monkeypatch, tmp_path, raw, handler):
    loader_path = _write_presets(tmp_path / "presets.json", _preset_entries())
    _formal_env(monkeypatch)
    provider = RegistryProvider(raw)

    def _never_backend(route, preset, messages, params):
        raise AssertionError("legacy carrier must stay untouched")

    return create_external_app(
        provider,
        _formal_loader(loader_path),
        _never_backend,
        soul_live_handler=handler,
    )


def _outer_post(app, soul, alias, token, snapshot):
    body = sa_api.envelope_to_body(_envelope(snapshot))

    async def _go():
        client = _REAL_ASYNC_CLIENT(
            transport=_REAL_ASGI_TRANSPORT(app=app),
            base_url="http://test",
        )
        return await client.post(
            "/%s/%s/v1/chat/completions" % (soul, alias),
            headers={"Authorization": "Bearer " + token},
            json=body,
        )

    response = _OUTWARD_RUNNER.run(_go())
    try:
        payload = response.json()
    except Exception:
        payload = None
    return (response.status_code, payload)


# -- T11-a: missing model mapping refuses (no guessing, no same-default) -------

def test_t11_frozen_aliases_unchanged_and_release_path_closed_shape():
    assert tuple(wiring.FORMAL_BACKEND_ALIASES) == ALIASES
    assert wiring.RELEASE_FILE.is_absolute()
    assert wiring.RELEASE_FILE.name == "sa.json"


def test_t11_missing_alias_mapping_refuses_install_no_guess(monkeypatch, tmp_path):
    presets_path = _write_presets(tmp_path / "presets.json", _preset_entries())
    _formal_env(monkeypatch)
    loader = _formal_loader(presets_path)
    good = {alias: loader(alias) for alias in ALIASES}

    short = {alias: good[alias] for alias in ALIASES if alias != "primary"}
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(short)
    assert exc_info.value.status == 500

    extra = dict(good)
    extra["void-unknown"] = good["primary"]
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(extra)
    assert exc_info.value.status == 500

    class _FakeProvider:
        provider = "void-fake"
        api_key = "t11-sandbox-key"
        base_url = "https://sa-t11-void.invalid/v1/chat/completions"

    mutated = dict(good)
    mutated["glm-5.3-flash"] = _FakeProvider()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(mutated)
    assert exc_info.value.status == 500

    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_formal_resolve_backend(short)
    assert exc_info.value.status == 500


def test_t11_unknown_alias_never_resolves_zero_posts(monkeypatch, tmp_path):
    presets_path = _write_presets(tmp_path / "presets.json", _preset_entries())
    _formal_env(monkeypatch)
    _patch_bottom_transport(monkeypatch)
    backends = _build_backends_no_env(presets_path)
    resolve = wiring.build_formal_resolve_backend(backends)
    llm_holder = SimpleNamespace(
        backend=llm_proxy.OpenAIBackend(
            api_key=LLM_ORIGINAL_KEY,
            base_url=LLM_ORIGINAL_URL,
        )
    )
    factory = wiring.build_dialog_factory(
        llm=llm_holder,
        loop=object(),
        resolve_backend=resolve,
        submit=_submit_on_c,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        factory(_snapshot_for("void-unknown", "sa-t11-void", 11))
    assert exc_info.value.status == 500
    assert _T11_POSTS == []

    with pytest.raises(InterfaceError):
        _formal_loader(presets_path)("void-unknown")


def _build_backends_no_env(presets_path):
    loader = _formal_loader(presets_path)
    presets = {alias: loader(alias) for alias in ALIASES}
    return wiring.build_preset_backends(presets)


def test_t11_model_drift_refuses_before_forward(monkeypatch, tmp_path):
    forwarded = []

    def _stub(envelope, route=None, preset=None):
        forwarded.append(envelope)
        return "t11-stub-reply"

    app = _build_outward(monkeypatch, tmp_path, _registry_raw(), _stub)
    snapshot = _snapshot_for("primary", MODELS["primary"], 21)
    status, payload = _outer_post(app, SOUL, "primary", TOKEN, snapshot)
    assert status == 200
    assert payload["model"] == MODELS["primary"]
    assert len(forwarded) == 1

    drifted = _snapshot_for("primary", "sa-t11-drifted-model", 22)
    status, _payload = _outer_post(app, SOUL, "primary", TOKEN, drifted)
    assert status == 403
    assert len(forwarded) == 1


# -- T11-b: missing formal identity refuses (never the T10 identity) ------------

def test_t11_missing_owner_refuses_install_keeps_snapshot():
    raw = _registry_raw()
    provider = RegistryProvider(raw)
    assert len(provider.current().routes) == 4

    no_owner = json.loads(json.dumps(raw))
    del no_owner["owner_principal_id"]
    with pytest.raises(InterfaceError):
        RegistryProvider(no_owner)
    with pytest.raises(InterfaceError):
        provider.update(no_owner)
    assert len(provider.current().routes) == 4
    assert provider.current().owner_principal_id == OWNER


def test_t11_missing_clients_or_digest_refuses_install_keeps_snapshot():
    raw = _registry_raw()
    provider = RegistryProvider(raw)

    no_clients = json.loads(json.dumps(raw))
    no_clients["clients"] = []
    with pytest.raises(InterfaceError):
        RegistryProvider(no_clients)

    no_digest = json.loads(json.dumps(raw))
    del no_digest["clients"][0]["credential_sha256"]
    with pytest.raises(InterfaceError):
        RegistryProvider(no_digest)
    with pytest.raises(InterfaceError):
        provider.update(no_digest)

    bad_digest = json.loads(json.dumps(raw))
    bad_digest["clients"][0]["credential_sha256"] = "0" * 63 + "g"
    with pytest.raises(InterfaceError):
        provider.update(bad_digest)

    assert len(provider.current().routes) == 4
    assert provider.current().owner_principal_id == OWNER


def test_t11_foreign_identity_never_admitted(monkeypatch, tmp_path):
    forwarded = []

    def _stub(envelope, route=None, preset=None):
        forwarded.append(envelope)
        return "t11-stub-reply"

    app = _build_outward(monkeypatch, tmp_path, _registry_raw(), _stub)

    snapshot = _snapshot_for("primary", MODELS["primary"], 31)
    status, _payload = _outer_post(app, SOUL, "primary", WRONG_TOKEN, snapshot)
    assert status == 401
    assert forwarded == []

    foreign = _snapshot_for("primary", MODELS["primary"], 32, principal=FOREIGN_PRINCIPAL)
    status, _payload = _outer_post(app, SOUL, "primary", TOKEN, foreign)
    assert status == 403
    assert forwarded == []

    assert resolve_route(RegistryProvider(_registry_raw()).current(), SOUL, "primary").agent_id == AGENT


# -- T11-c: missing key_ref refuses --------------------------------------------

def test_t11_missing_key_ref_field_refuses(monkeypatch, tmp_path):
    entries = _preset_entries()
    broken = [dict(e) for e in entries]
    del broken[1]["key_ref"]
    path = _write_presets(tmp_path / "presets-no-keyref.json", broken)
    _formal_env(monkeypatch)
    with pytest.raises(InterfaceError):
        _formal_loader(path)("minimax-m3.1-flash")


def test_t11_unset_key_env_refuses_no_partial(monkeypatch, tmp_path):
    path = _write_presets(tmp_path / "presets.json", _preset_entries())
    monkeypatch.delenv(SHARED_REF, raising=False)
    assert os.environ.get(SHARED_REF) is None
    with pytest.raises(InterfaceError):
        _formal_loader(path)("primary")


def test_t11_plaintext_looking_key_ref_refuses(monkeypatch, tmp_path):
    _formal_env(monkeypatch)
    lower = _preset_entries(key_ref="sa_t11_shared")
    path = _write_presets(tmp_path / "presets-lower.json", lower)
    with pytest.raises(InterfaceError):
        _formal_loader(path)("primary")

    long_ref = "SA_T11_" + "X" * 21
    assert len(long_ref) > 24
    long_entries = _preset_entries(key_ref=long_ref)
    long_path = _write_presets(tmp_path / "presets-long.json", long_entries)
    with pytest.raises(InterfaceError):
        _formal_loader(long_path)("primary")


# -- T11-d: shared URL + shared key keeps four distinct models ------------------

def test_t11_shared_url_key_keeps_distinct_models(monkeypatch, tmp_path):
    assert "OLLAMA_API_KEY" not in "\n".join(
        json.dumps(e) for e in _preset_entries()
    )
    ollama_present = "OLLAMA_API_KEY" in os.environ
    assert isinstance(ollama_present, bool)

    path = _write_presets(tmp_path / "presets.json", _preset_entries())
    _formal_env(monkeypatch)
    _patch_bottom_transport(monkeypatch)
    loader = _formal_loader(path)
    presets = {alias: loader(alias) for alias in ALIASES}

    assert {p.base_url for p in presets.values()} == {SHARED_URL}
    assert {p.key_ref for p in presets.values()} == {SHARED_REF}
    assert len({p.model for p in presets.values()}) == 4
    for alias in ALIASES:
        assert presets[alias].model == MODELS[alias]
        assert presets[alias].provider == "openai"

    backends = wiring.build_preset_backends(presets)
    resolve = wiring.build_formal_resolve_backend(backends)
    llm_holder = SimpleNamespace(
        backend=llm_proxy.OpenAIBackend(
            api_key=LLM_ORIGINAL_KEY,
            base_url=LLM_ORIGINAL_URL,
        )
    )
    factory = wiring.build_dialog_factory(
        llm=llm_holder,
        loop=object(),
        resolve_backend=resolve,
        submit=_submit_on_c,
    )
    _T11_POSTS.clear()
    for n, alias in enumerate(ALIASES, start=41):
        dialog = factory(_snapshot_for(alias, MODELS[alias], n))
        assert dialog.sa_dialog_backend_alias == alias
        text = dialog(
            (("user", "t11 shared probe"),),
            agent_id=AGENT,
            request_id="req-t11-%04d" % n,
        )
        assert text == _REPLY
    assert len(_T11_POSTS) == 4
    posted_models = [p["model"] for p in _T11_POSTS]
    assert posted_models == [MODELS[alias] for alias in ALIASES]
    assert len(set(posted_models)) == 4
    non_primary_auths = {
        p["auth"] for p in _T11_POSTS if p["auth"] != "Bearer " + LLM_ORIGINAL_KEY
    }
    assert non_primary_auths == {"Bearer " + SHARED_KEY}


# -- T11-e: status=closed forbids every inference call --------------------------

def test_t11_closed_gate_zero_inference(monkeypatch, tmp_path):
    from clients.voice_companion.session_store import SessionStore  # noqa: E402

    _formal_env(monkeypatch)
    _patch_bottom_transport(monkeypatch)
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})

    sent = []
    fwd = wiring.build_thin_forwarder(
        release_path=str(release_path),
        sender=lambda url, body: sent.append(body) or "t11-red",
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert exc_info.value.status == 403
    assert sent == []

    dialog_calls = []

    def _explode_dialog(messages, *, agent_id=None, request_id=None):
        dialog_calls.append((agent_id, request_id))
        raise AssertionError("inference attempted while closed")

    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    generate = wiring.build_generate(
        bus=bus,
        llm=llm,
        middleware=middleware,
        session_store=store,
        dialog=_explode_dialog,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert exc_info.value.status == 403
    assert dialog_calls == []
    assert _T11_POSTS == []

    release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    dialog_ok = []

    def _ok_dialog(messages, *, agent_id=None, request_id=None):
        dialog_ok.append((agent_id, request_id))
        return "t11-half-open-reply"

    generate_open = wiring.build_generate(
        bus=bus,
        llm=llm,
        middleware=middleware,
        session_store=store,
        dialog=_ok_dialog,
    )
    handler_open = sa_bridge.build_live_handler(
        live=live, generate=generate_open, release_path=str(release_path)
    )
    assert handler_open(_envelope()) == "t11-half-open-reply"
    assert len(dialog_ok) == 1

    release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    with pytest.raises(sa_bridge.LiveRefused):
        handler_open(_envelope())
    assert len(dialog_ok) == 1


# -- T11-f: source self-check (no banned literals in this file) -----------------

def test_t11_source_carries_no_banned_literals():
    src = Path(__file__).read_text(encoding="utf-8")
    host_a = "local" + "host"
    port_b = "800" + "0"
    banned = [
        "http" + "://" + host_a + ":" + port_b,
        host_a + ":" + port_b,
        "127" + ".0.0.1:" + port_b,
        "run_" + "server",
        "sub" + "process",
        "so" + "cket",
        "sig" + "nal",
        "da" + "ta/",
        "pk" + "ill",
    ]
    offenders = [token for token in banned if token in src]
    assert offenders == []
