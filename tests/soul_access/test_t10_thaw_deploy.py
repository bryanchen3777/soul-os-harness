"""SA-THAW-DEPLOY-1 T10 closed-door acceptance: formal assembly, sandbox only.

Decision-complete scope (SA-THAW-DEPLOY-1, first closed-door segment): this
file is new; the only tracked product edit is the additive formal assembly in
``src/soul_access/wiring.py`` (``FORMAL_BACKEND_ALIASES`` /
``build_preset_backends`` / ``build_formal_resolve_backend``); the production
mount keeps ``resolve_backend=None`` so the gate stays closed. Frozen files
(bridge / api / release / schema / SA-0台帳/判準) are untouched; no server is
started, no production port is bound, no real request leaves the process.

All test names carry the t10 marker: `pytest -q tests/soul_access -k t10`
must collect > 0. Transport is memory-only: the outward app and the inward
sandbox app are both driven through ``httpx.ASGITransport`` (in-process, never
a port) and the only bottom-I/O seam replaced is the lowest HTTP transport
inside ``OpenAIBackend.complete`` (a counting stand-in that answers one fixed
200 reply). ``OpenAIBackend.complete`` itself runs unmodified, so every
admitted call provably performs exactly one inference attempt with
``max_retries=0``. Storage is sandbox-only: a self-created tmp release file
and a tmp session store (never production state, never the pinned path).

Two queue-driven runners nest the two memory hops plus inference:
hop 1 (outward app) runs on loop A in the test thread while hop 2 (thin
sender -> inward app) is served by runner B on its own thread; every dialog
``submit`` is served by runner C on a third thread. Runner C is required
because the inward handler (hence the dialog) executes ON runner B's thread:
running the inference coroutine back on runner B and blocking would stall
the very loop it waits for (production avoids this the same way -- the
dialog never runs on the launcher loop, see ``_default_submit``). Each
runner owns exactly one loop created at import time (same idiom as the T1
memory transport): the owning thread is the only thread that ever drives
its loop (queue handoff plus run_until_complete, never a cross-thread
wakeup), so no loop is ever created while the per-test blocks hold.
Sync tests only.

Formal evidence under verification/sa-thaw-deploy-1/formal/ (registry.a.json
= batch A, 4 routes; registry.union.json = A union B, 20 routes; presets.json
= 4 frozen aliases) is read by these tests directly: route lists, credential
digests and file hashes are pinned here, so any drift in the evidence breaks
these tests instead of passing silently.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
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

from clients.voice_companion.session_store import SessionStore  # noqa: E402
from src.external_gateway.app import create_external_app  # noqa: E402
from src.external_gateway.app import file_preset_loader  # noqa: E402
from src.interfaces.config import resolve_route  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.llm import proxy as llm_proxy  # noqa: E402,F401 (patch target must import)
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

# -- module-level loops (import time; see module docstring) ------------------

_T10_LOOP_A = asyncio.new_event_loop()
_T10_LOOP_B = asyncio.new_event_loop()
_T10_LOOP_C = asyncio.new_event_loop()


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


_HOP2_RUNNER = _Runner(_T10_LOOP_B, "t10-hop2")
_INFER_RUNNER = _Runner(_T10_LOOP_C, "t10-infer")

# Real transport handles, saved before any per-test patch replaces the class
# attribute on the shared httpx module object.
_REAL_ASYNC_CLIENT = httpx.AsyncClient
_REAL_ASGI_TRANSPORT = httpx.ASGITransport

assert llm_proxy.OpenAIBackend is not None

# -- formal pins ---------------------------------------------------------------

OWNER = "t10-formal-owner"
SCOPE = "owner_private"
FORMAL_TOKEN = "t10-formal-sandbox-token"
OTHER_TOKEN = "t10-other-sandbox-token"
FORMAL_DIGEST = "02356aae1ece900fbacff220d2a345cddaa385f89e66df36aef2d2fa5807d724"
OTHER_DIGEST = "1d780cb02c7b5d7268f957c328764853d022a4521d3e364fd13db2f02003d8bb"

ALIASES = (
    "primary",
    "minimax-m3.1-flash",
    "glm-5.3-flash",
    "mimo-v2.6-flash",
)
A_SOULS = ("rem",)
B_SOULS = ("mai", "ruka", "akane", "mahiru")
ALL_SOULS = A_SOULS + B_SOULS
AGENT_OF = {soul: "agent_%s" % soul for soul in ALL_SOULS}
MODEL_OF = {}
_PRESET_MODEL_OF_ALIAS = {
    "primary": "sa-formal-primary",
    "minimax-m3.1-flash": "sa-formal-minimax",
    "glm-5.3-flash": "sa-formal-glm",
    "mimo-v2.6-flash": "sa-formal-mimo",
}
for _soul in ALL_SOULS:
    for _alias in ALIASES:
        MODEL_OF[(_soul, _alias)] = _PRESET_MODEL_OF_ALIAS[_alias]

KEYREFS = {
    "primary": "SA_T10_PRIMARY",
    "minimax-m3.1-flash": "SA_T10_MINIMAX_M31",
    "glm-5.3-flash": "SA_T10_GLM_53",
    "mimo-v2.6-flash": "SA_T10_MIMO_V26",
}
PRESET_KEYS = {
    "primary": "t10-preset-key-primary",
    "minimax-m3.1-flash": "t10-preset-key-minimax",
    "glm-5.3-flash": "t10-preset-key-glm",
    "mimo-v2.6-flash": "t10-preset-key-mimo",
}
LLM_ORIGINAL_KEY = "t10-llm-original-key"

FORMAL_DIR = _REPO_ROOT / "verification" / "sa-thaw-deploy-1" / "formal"
PIN_A_SHA = "8c8060059b59baef041999f20b6c0a764d0069d5a1105e796dbce86758ed1644"
PIN_UNION_SHA = "d5d76ef1d6c2d32ba579a10da1d59baa01725ddf263506cdad48ac7373f02dbc"
PIN_PRESETS_SHA = "e57fc68f74200b4e25a9d799576fabcf5b0a935567a6522bfd19416ff568ac51"

_REPLY = "t10-formal-reply"

_T10_POSTS: list = []


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


def _snapshot_for(soul, alias, model, n, principal=OWNER):
    req = "req-t10-%04d" % n
    key = "key-t10-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(req, key),
        dedup=_dedup(AGENT_OF[soul], req, key, principal),
        backend_alias=alias,
        model=model,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t10-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-t10-v1",
        projection_state="projection-t10-v1",
        recall_version="recall-t10-v1",
        recall_items=("recall-t10-item-1",),
        history_pool_version="pool-t10-v1",
        clean_history=("hist-t10-item-1",),
    )


def _envelope(snapshot=None, messages=(("user", "t10 probe"),)):
    snap = (
        snapshot
        if snapshot is not None
        else _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 1)
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


def _formal_env(monkeypatch):
    for alias, ref in KEYREFS.items():
        monkeypatch.setenv(ref, PRESET_KEYS[alias])


def _formal_loader():
    return file_preset_loader(str(FORMAL_DIR / "presets.json"))


def _formal_raw(name):
    return json.loads((FORMAL_DIR / name).read_text(encoding="utf-8"))


def _sha_of(name):
    return hashlib.sha256((FORMAL_DIR / name).read_bytes()).hexdigest()


class _StubApp:
    """Minimal mount stand-in: state loop slot + route recorder."""

    def __init__(self, live=None):
        self.state = SimpleNamespace(_soul_access_live=live)
        self.routes = []

    def add_api_route(self, path, handler, methods=None, name=None):
        self.routes.append((path, handler, tuple(methods or ()), name))


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
        _T10_POSTS.append({"url": url, "auth": auth, "model": body.get("model")})
        return _Fake200()


def _patch_bottom_transport(monkeypatch):
    _T10_POSTS.clear()
    monkeypatch.setattr("src.llm.proxy.httpx.AsyncClient", _CountingAsyncClient)


def _submit_on_c(loop, coro):
    # Queue handoff to the infer runner: the calling thread blocks on the
    # queue (never on a cross-thread loop wakeup); the runner thread drives
    # its own loop to completion, then we hand back an already-done future
    # so the dialog seam keeps its future-based shape.
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
    return _T10_LOOP_A.run_until_complete(coro)


def _build_formal_backends():
    loader = _formal_loader()
    presets = {alias: loader(alias) for alias in ALIASES}
    return wiring.build_preset_backends(presets)


def _build_stack(monkeypatch, tmp_path, release_status="open"):
    """Assemble the full closed-door stack on sandbox storage."""
    _formal_env(monkeypatch)
    _patch_bottom_transport(monkeypatch)
    release_path = _write_release(tmp_path / "release.json", {"status": release_status})
    backends = _build_formal_backends()
    resolve = wiring.build_formal_resolve_backend(backends)
    llm_holder = SimpleNamespace(
        backend=llm_proxy.OpenAIBackend(
            api_key=LLM_ORIGINAL_KEY,
            base_url="https://sa-formal-primary.invalid/v1/chat/completions",
        )
    )
    sentinel = object()
    factory = wiring.build_dialog_factory(
        llm=llm_holder, loop=sentinel, resolve_backend=resolve, submit=_submit_on_c
    )
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, middleware = object(), object()
    generate = wiring.build_generate(
        bus=bus,
        llm=llm_holder,
        middleware=middleware,
        session_store=store,
        dialog_factory=factory,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm_holder, middleware=middleware)
    return SimpleNamespace(
        backends=backends,
        resolve=resolve,
        llm_holder=llm_holder,
        loop=sentinel,
        factory=factory,
        store=store,
        bus=bus,
        middleware=middleware,
        generate=generate,
        live=live,
        release_path=release_path,
    )


def _build_harness(monkeypatch, tmp_path, raw, release_status="open"):
    """Two-hop memory harness: outward app -> thin -> inward app -> dialog."""
    stack = _build_stack(monkeypatch, tmp_path, release_status)
    provider = RegistryProvider(raw)
    stub = _StubApp(stack.live)
    route_fn = wiring.mount_live_route(
        stub,
        live=stack.live,
        generate=stack.generate,
        release_path=str(stack.release_path),
    )
    assert stub.routes and stub.routes[0][0] == wiring.INTERNAL_LIVE_PATH
    assert stub.routes[0][2] == ("POST",)
    inward = FastAPI()
    sa_bridge.attach_live_loop(
        inward, bus=stack.bus, llm=stack.llm_holder, middleware=stack.middleware
    )

    async def _entry(body: dict):
        return route_fn(body)

    inward.add_api_route(wiring.INTERNAL_LIVE_PATH, _entry, methods=["POST"])

    def _send(url, body):
        assert url.endswith(wiring.INTERNAL_LIVE_PATH)

        async def _go():
            client = _REAL_ASYNC_CLIENT(
                transport=_REAL_ASGI_TRANSPORT(app=inward),
                base_url="http://test",
            )
            return await client.post(wiring.INTERNAL_LIVE_PATH, json=body)

        response = _HOP2_RUNNER.run(_go())
        if response.status_code != 200:
            raise RuntimeError("t10 inward hop status=%s" % response.status_code)
        return response.text

    thin = wiring.build_thin_forwarder(
        release_path=str(stack.release_path), sender=_send
    )

    def _never_backend(route, preset, messages, params):
        raise AssertionError("legacy carrier must stay untouched")

    outward = create_external_app(
        provider,
        _formal_loader(),
        _never_backend,
        soul_live_handler=thin,
    )
    return SimpleNamespace(stack=stack, provider=provider, thin=thin, outward=outward)


def _outer_post(harness, soul, alias, token, snapshot):
    body = sa_api.envelope_to_body(_envelope(snapshot))

    async def _go():
        client = _REAL_ASYNC_CLIENT(
            transport=_REAL_ASGI_TRANSPORT(app=harness.outward),
            base_url="http://test",
        )
        return await client.post(
            "/%s/%s/v1/chat/completions" % (soul, alias),
            headers={"Authorization": "Bearer " + token},
            json=body,
        )

    response = _run_a(_go())
    try:
        payload = response.json()
    except Exception:
        payload = None
    return (response.status_code, payload)


# -- T10-0: release matrix (missing / closed / bad refuse; hot-read works) ----


def test_t10_release_closed_missing_bad_refuse_403_zero_send(tmp_path):
    _T10_POSTS.clear()
    for payload in ("not-json{{{", "[1, 2, 3]", '{"status": "OPEN"}', '{"other": 1}'):
        release_path = _write_release(tmp_path / "release.json", payload)
        sent = []
        fwd = wiring.build_thin_forwarder(
            release_path=str(release_path),
            sender=lambda url, body: sent.append(body) or "t10-open",
        )
        with pytest.raises(sa_bridge.LiveRefused) as exc_info:
            fwd(_envelope())
        assert exc_info.value.status == 403
        assert sent == []
    missing = tmp_path / "release-missing.json"
    assert not missing.exists()
    with pytest.raises(sa_bridge.LiveRefused):
        wiring.build_thin_forwarder(
            release_path=str(missing),
            sender=lambda url, body: "t10-open",
        )(_envelope())


def test_t10_release_open_close_open_hot_read(tmp_path):
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    dialog_calls = []

    def _dialog(messages, *, agent_id=None, request_id=None):
        dialog_calls.append((agent_id, request_id))
        return "t10-hot"

    generate = wiring.build_generate(
        bus=bus,
        llm=llm,
        middleware=middleware,
        session_store=store,
        dialog=_dialog,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    assert handler(_envelope()) == "t10-hot"
    release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert exc_info.value.status == 403
    calls_after_close = len(dialog_calls)
    release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    assert handler(_envelope()) == "t10-hot"
    assert len(dialog_calls) == calls_after_close + 1


# -- T10-1: formal dialog (four aliases resolve; primary keeps its instance) --


def test_t10_formal_alias_set_exact_and_provider_pinned(monkeypatch):
    _formal_env(monkeypatch)
    assert tuple(wiring.FORMAL_BACKEND_ALIASES) == ALIASES
    backends = _build_formal_backends()
    assert set(backends.keys()) == set(ALIASES)
    for alias in ALIASES:
        assert isinstance(backends[alias], llm_proxy.OpenAIBackend)
    assert _formal_loader()("primary").provider == "openai"
    assert _formal_loader()("primary").model == "sa-formal-primary"


def test_t10_formal_rejects_non_openai_provider_unknown_alias_extra_alias(monkeypatch):
    _formal_env(monkeypatch)
    loader = _formal_loader()
    good = {alias: loader(alias) for alias in ALIASES}

    class _Other:
        provider = "other-provider"
        api_key = "k"
        base_url = "https://example.invalid/v1"

    mutated = dict(good)
    mutated["glm-5.3-flash"] = _Other()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(mutated)
    assert exc_info.value.status == 500

    short = {alias: good[alias] for alias in ALIASES if alias != "primary"}
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(short)
    assert exc_info.value.status == 500

    extra = dict(good)
    extra["void-unknown"] = good["primary"]
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_preset_backends(extra)
    assert exc_info.value.status == 500

    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_formal_resolve_backend(short)
    assert exc_info.value.status == 500


def test_t10_primary_uses_original_instance_never_resolved(monkeypatch, tmp_path):
    stack = _build_stack(monkeypatch, tmp_path)
    calls = []
    original_resolve = stack.resolve

    def _spy(alias):
        calls.append(alias)
        return original_resolve(alias)

    factory = wiring.build_dialog_factory(
        llm=stack.llm_holder,
        loop=stack.loop,
        resolve_backend=_spy,
        submit=_submit_on_c,
    )
    dialog = factory(_snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 11))
    assert dialog.sa_dialog_backend_alias == "primary"
    text = dialog(
        (("user", "t10 primary probe"),),
        agent_id="agent_rem",
        request_id="req-t10-0011",
    )
    assert text == _REPLY
    assert calls == []
    primary_posts = [p for p in _T10_POSTS if p["auth"] == "Bearer " + LLM_ORIGINAL_KEY]
    assert len(primary_posts) == 1
    assert primary_posts[0]["model"] == MODEL_OF[("rem", "primary")]


def test_t10_non_primary_single_attempt_no_fallback_no_retry(monkeypatch, tmp_path):
    stack = _build_stack(monkeypatch, tmp_path)
    _T10_POSTS.clear()
    for n, alias in enumerate(
        ("minimax-m3.1-flash", "glm-5.3-flash", "mimo-v2.6-flash"), start=21
    ):
        model = _formal_loader()(alias).model
        dialog = stack.factory(_snapshot_for("rem", alias, model, n))
        assert dialog.sa_dialog_backend_alias == alias
        text = dialog(
            (("user", "t10 alias probe"),),
            agent_id="agent_rem",
            request_id="req-t10-%04d" % n,
        )
        assert text == _REPLY
    assert len(_T10_POSTS) == 3
    by_auth = {}
    for post in _T10_POSTS:
        by_auth.setdefault(post["auth"], []).append(post)
    assert set(by_auth.keys()) == {
        "Bearer " + PRESET_KEYS["minimax-m3.1-flash"],
        "Bearer " + PRESET_KEYS["glm-5.3-flash"],
        "Bearer " + PRESET_KEYS["mimo-v2.6-flash"],
    }
    llm_posts = [p for p in _T10_POSTS if p["auth"] == "Bearer " + LLM_ORIGINAL_KEY]
    assert llm_posts == []


def test_t10_unknown_alias_refuses_500_primary_stays_quiet(monkeypatch, tmp_path):
    stack = _build_stack(monkeypatch, tmp_path)
    _T10_POSTS.clear()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        stack.factory(_snapshot_for("rem", "void-unknown", "sa-formal-void", 31))
    assert exc_info.value.status == 500
    assert _T10_POSTS == []

    def _none_resolve(alias):
        return None

    factory_none = wiring.build_dialog_factory(
        llm=stack.llm_holder,
        loop=stack.loop,
        resolve_backend=_none_resolve,
        submit=_submit_on_c,
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        factory_none(_snapshot_for("rem", "glm-5.3-flash", "sa-formal-glm", 32))
    assert exc_info.value.status == 500
    assert _T10_POSTS == []


def test_t10_same_loop_misuse_refuses_without_attempt(monkeypatch, tmp_path):
    stack = _build_stack(monkeypatch, tmp_path)
    _T10_POSTS.clear()
    monkeypatch.setattr(asyncio, "get_running_loop", lambda: stack.loop)
    dialog = stack.factory(
        _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 41)
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        dialog((("user", "hi"),), agent_id="agent_rem", request_id="req-t10-0041")
    assert exc_info.value.status == 500
    assert _T10_POSTS == []


# -- T10-2: formal registry + presets + thin update operation list ------------


def _route_key(route):
    return (
        route["soul_alias"],
        route["backend_alias"],
        route["agent_id"],
        route["public_model_id"],
    )


def test_t10_formal_files_pinned_validated_digests_match_tokens():
    assert _sha_of("registry.a.json") == PIN_A_SHA
    assert _sha_of("registry.union.json") == PIN_UNION_SHA
    assert _sha_of("presets.json") == PIN_PRESETS_SHA
    assert digest_of(FORMAL_TOKEN) == FORMAL_DIGEST
    assert digest_of(OTHER_TOKEN) == OTHER_DIGEST
    raw_a = _formal_raw("registry.a.json")
    raw_u = _formal_raw("registry.union.json")
    assert [r["soul_alias"] for r in raw_a["routes"]] == ["rem"] * 4
    assert [_route_key(r)[:2] for r in raw_a["routes"]] == [
        ("rem", alias) for alias in ALIASES
    ]
    assert [_route_key(r)[:2] for r in raw_u["routes"]] == [
        (soul, alias) for soul in ALL_SOULS for alias in ALIASES
    ]
    assert len(raw_u["routes"]) == 20
    assert raw_a["clients"][0]["allowed_routes"] == [
        "rem/%s" % alias for alias in ALIASES
    ]
    assert raw_a["clients"][0]["credential_sha256"] == FORMAL_DIGEST
    assert raw_a["clients"][1]["allowed_routes"] == ["rem/primary"]
    assert raw_a["clients"][1]["credential_sha256"] == OTHER_DIGEST
    assert raw_u["clients"][0]["credential_sha256"] == FORMAL_DIGEST
    assert len(raw_u["clients"][0]["allowed_routes"]) == 20
    provider_a = RegistryProvider(raw_a)
    provider_u = RegistryProvider(raw_u)
    assert len(provider_a.current().routes) == 4
    assert len(provider_u.current().routes) == 20
    for soul in ALL_SOULS:
        for alias in ALIASES:
            route = resolve_route(provider_u.current(), soul, alias)
            assert route.agent_id == AGENT_OF[soul]
    for soul in B_SOULS:
        with pytest.raises(Exception):
            resolve_route(provider_a.current(), soul, "primary")


def test_t10_presets_schema_models_keyrefs(monkeypatch):
    _formal_env(monkeypatch)
    loader = _formal_loader()
    for alias in ALIASES:
        preset = loader(alias)
        assert preset.provider == "openai"
        assert preset.key_ref == KEYREFS[alias]
        assert preset.base_url == "https://sa-formal-%s.invalid/v1/chat/completions" % (
            {
                "primary": "primary",
                "minimax-m3.1-flash": "minimax",
                "glm-5.3-flash": "glm",
                "mimo-v2.6-flash": "mimo",
            }[alias]
        )
    assert loader("primary").model == "sa-formal-primary"


def _snapshot_sha(provider):
    current = provider.current()
    routes = sorted(
        (r.soul_alias, r.backend_alias, r.agent_id, r.public_model_id)
        for r in current.routes
    )
    return hashlib.sha256(json.dumps(routes).encode("utf-8")).hexdigest()


def test_t10_update_op_list_install_and_reject(monkeypatch, tmp_path):
    _formal_env(monkeypatch)
    raw_a = _formal_raw("registry.a.json")
    raw_u = _formal_raw("registry.union.json")
    provider = RegistryProvider(raw_a)
    assert _snapshot_sha(provider) != _snapshot_sha(RegistryProvider(raw_u))
    # Editing a file copy alone installs nothing: the serving snapshot is
    # unchanged until update() is called.
    edited = json.loads(json.dumps(raw_a))
    edited["routes"].append(
        {
            "soul_alias": "mai",
            "backend_alias": "primary",
            "agent_id": "agent_mai",
            "public_model_id": "sa-formal-mai-primary",
            "backend_ref": "port-a",
            "context_window_tokens": 4096,
        }
    )
    with pytest.raises(Exception):
        resolve_route(provider.current(), "mai", "primary")
    # The edited bytes are a valid installable candidate on their own ...
    edited_provider = RegistryProvider(edited)
    assert (
        resolve_route(edited_provider.current(), "mai", "primary").agent_id
        == "agent_mai"
    )
    # ... but the serving snapshot is unchanged until update() is called.
    # A malformed candidate never swaps the snapshot.
    bad = json.loads(json.dumps(raw_a))
    bad["clients"][0]["credential_sha256"] = "0" * 63 + "g"
    with pytest.raises(Exception):
        provider.update(bad)
    with pytest.raises(Exception):
        resolve_route(provider.current(), "mai", "primary")
    assert len(provider.current().routes) == 4
    # Installing the union is exactly one update() call on the same provider.
    provider.update(raw_u)
    assert len(provider.current().routes) == 20
    assert resolve_route(provider.current(), "mai", "primary").agent_id == "agent_mai"
    # Rolling back is the same operation in reverse.
    provider.update(raw_a)
    assert len(provider.current().routes) == 4
    with pytest.raises(Exception):
        resolve_route(provider.current(), "mai", "primary")


# -- T10-3: memory ASGI end to end (auth -> route -> thin -> live -> dialog) -


def test_t10_e2e_batch_a_four_admitted_single_attempt_each(monkeypatch, tmp_path):
    harness = _build_harness(monkeypatch, tmp_path, _formal_raw("registry.a.json"))
    _T10_POSTS.clear()
    for n, alias in enumerate(ALIASES, start=101):
        snapshot = _snapshot_for("rem", alias, MODEL_OF[("rem", alias)], n)
        status, payload = _outer_post(harness, "rem", alias, FORMAL_TOKEN, snapshot)
        assert status == 200, (alias, status, payload)
        assert payload["model"] == MODEL_OF[("rem", alias)]
        assert payload["choices"][0]["message"]["content"] == _REPLY
    assert len(_T10_POSTS) == 4
    auths = sorted(p["auth"] for p in _T10_POSTS)
    assert auths == sorted(
        ["Bearer " + LLM_ORIGINAL_KEY]
        + ["Bearer " + PRESET_KEYS[alias] for alias in ALIASES if alias != "primary"]
    )
    assert harness.stack.generate.trace and len(harness.stack.generate.trace) == 4
    assert harness.stack.generate.pending == {}


def test_t10_e2e_batch_b_sixteen_refused_zero_posts(monkeypatch, tmp_path):
    harness = _build_harness(monkeypatch, tmp_path, _formal_raw("registry.a.json"))
    _T10_POSTS.clear()
    denied = 0
    for m, soul in enumerate(B_SOULS):
        for k, alias in enumerate(ALIASES):
            snapshot = _snapshot_for(soul, alias, MODEL_OF[(soul, alias)], 200 + m * 4 + k)
            status, _payload = _outer_post(harness, soul, alias, FORMAL_TOKEN, snapshot)
            assert status == 404, (soul, alias, status)
            denied += 1
    assert denied == 16
    assert _T10_POSTS == []


def test_t10_e2e_cross_identity_route_refusals_zero_posts(monkeypatch, tmp_path):
    harness = _build_harness(monkeypatch, tmp_path, _formal_raw("registry.a.json"))
    _T10_POSTS.clear()
    # Wrong credential.
    snapshot = _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 301)
    status, _payload = _outer_post(harness, "rem", "primary", "wrong-token", snapshot)
    assert status == 401
    # Restricted client outside its allowed routes.
    snapshot = _snapshot_for("rem", "glm-5.3-flash", MODEL_OF[("rem", "glm-5.3-flash")], 302)
    status, _payload = _outer_post(harness, "rem", "glm-5.3-flash", OTHER_TOKEN, snapshot)
    assert status == 403
    # Envelope principal drifts from the authenticated client.
    snapshot = _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 303, principal="someone-else")
    status, _payload = _outer_post(harness, "rem", "primary", FORMAL_TOKEN, snapshot)
    assert status == 403
    # Envelope agent drifts from the admitted route.
    snapshot = _snapshot_for("mai", "primary", MODEL_OF[("rem", "primary")], 304)
    status, _payload = _outer_post(harness, "rem", "primary", FORMAL_TOKEN, snapshot)
    assert status == 403
    # Unknown soul never resolves.
    snapshot = _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 305)
    status, _payload = _outer_post(harness, "void", "primary", FORMAL_TOKEN, snapshot)
    assert status == 404
    assert _T10_POSTS == []


def test_t10_e2e_closed_gate_403_zero_downstream(monkeypatch, tmp_path):
    harness = _build_harness(
        monkeypatch, tmp_path, _formal_raw("registry.a.json"), release_status="closed"
    )
    _T10_POSTS.clear()
    snapshot = _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 401)
    status, _payload = _outer_post(harness, "rem", "primary", FORMAL_TOKEN, snapshot)
    assert status == 403
    assert _T10_POSTS == []


def test_t10_e2e_union_twenty_admitted_shared_session(monkeypatch, tmp_path):
    harness = _build_harness(monkeypatch, tmp_path, _formal_raw("registry.union.json"))
    _T10_POSTS.clear()
    n = 500
    for soul in ALL_SOULS:
        for alias in ALIASES:
            n += 1
            snapshot = _snapshot_for(soul, alias, MODEL_OF[(soul, alias)], n)
            status, payload = _outer_post(harness, soul, alias, FORMAL_TOKEN, snapshot)
            assert status == 200, (soul, alias, status, payload)
            assert payload["choices"][0]["message"]["content"] == _REPLY
    assert len(_T10_POSTS) == 20
    assert len(harness.stack.generate.trace) == 20
    assert harness.stack.generate.pending == {}
    raw = json.loads(
        wiring.session_path(harness.stack.store, "agent_rem").read_text(encoding="utf-8")
    )
    channels = [turn.get("channel") for turn in raw["turns"]]
    assert "sa-live" in channels


# -- T10-4: bidirectional mutations (relaxed / tightened / revert green) -------


def _closed_forwarder_refuses(fwd, sent) -> bool:
    """Shared closed-gate oracle: True iff the forwarder refuses 403 with zero sends."""
    try:
        fwd(_envelope())
    except sa_bridge.LiveRefused as exc:
        return exc.status == 403 and sent == []
    return False


def _open_forwarder_admits(fwd, sent) -> bool:
    """Shared open-gate oracle: True iff the forwarder admits with exactly one send."""
    try:
        fwd(_envelope())
    except sa_bridge.LiveRefused:
        return False
    return len(sent) == 1


def test_t10_mutation_relaxed_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Relaxed mutant: forced-open gate forwards while closed (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sent = []
    fwd = wiring.build_thin_forwarder(
        release_path=str(release_path),
        sender=lambda url, body: sent.append(body) or "t10-red",
    )
    assert _closed_forwarder_refuses(fwd, sent) is True  # green baseline, shared oracle
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: True)
    assert _closed_forwarder_refuses(fwd, sent) is False  # RED: same oracle turns red
    if len(sent) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed-open did not forward")
    sent.clear()  # drop the RED send so the revert check starts from empty
    monkeypatch.undo()
    assert _closed_forwarder_refuses(fwd, sent) is True  # revert green


def test_t10_mutation_tightened_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Tightened mutant: forced-closed gate refuses while open (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sent = []
    fwd = wiring.build_thin_forwarder(
        release_path=str(release_path),
        sender=lambda url, body: sent.append(body) or "t10-red",
    )
    assert _open_forwarder_admits(fwd, sent) is True  # green baseline, shared oracle
    sent.clear()  # drop the baseline send so the mutant phase starts from empty
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: False)
    assert _open_forwarder_admits(fwd, sent) is False  # RED: same oracle turns red
    if sent:
        pytest.fail("MUTATION DUD -- STOP, report: tightened-closed leaked a send")
    monkeypatch.undo()
    assert _open_forwarder_admits(fwd, sent) is True  # revert green


def test_t10_mutation_reverted_full_stack_green(monkeypatch, tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    _formal_env(monkeypatch)
    _patch_bottom_transport(monkeypatch)
    backends = _build_formal_backends()
    resolve = wiring.build_formal_resolve_backend(backends)
    llm_holder = SimpleNamespace(backend=backends["primary"])
    factory = wiring.build_dialog_factory(
        llm=llm_holder, loop=object(), resolve_backend=resolve, submit=_submit_on_c
    )
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, middleware = object(), object()
    generate = wiring.build_generate(
        bus=bus,
        llm=llm_holder,
        middleware=middleware,
        session_store=store,
        dialog_factory=factory,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm_holder, middleware=middleware)
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    assert sa_release.is_live_inbound_open(str(release_path)) is True
    snapshot = _snapshot_for("rem", "glm-5.3-flash", "sa-formal-glm", 601)
    assert handler(_envelope(snapshot)) == _REPLY
    assert len(_T10_POSTS) == 1
    assert len(generate.trace) == 1


# -- T10-5: source self-check (no banned literals in this file) ----------------


def test_t10_source_carries_no_banned_literals():
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


# -- T10-6: rollback drill (closed -> A -> closed -> union -> closed -> A) ------


def test_t10_rollback_drill_closed_a_closed_union_closed_restore_a(
    monkeypatch, tmp_path
):
    raw_a = _formal_raw("registry.a.json")
    raw_u = _formal_raw("registry.union.json")
    harness = _build_harness(monkeypatch, tmp_path, raw_a, release_status="closed")
    _T10_POSTS.clear()

    def _bellwether(soul, alias, token, n, model=None):
        snapshot = _snapshot_for(
            soul,
            alias,
            model if model is not None else MODEL_OF[(soul, alias)],
            n,
        )
        return _outer_post(harness, soul, alias, token, snapshot)

    def _route_names():
        return sorted(
            (r.soul_alias, r.backend_alias) for r in harness.provider.current().routes
        )

    # 1. closed + A installed: every bellwether refused, zero inference.
    assert _bellwether("rem", "primary", FORMAL_TOKEN, 701)[0] == 403
    assert _T10_POSTS == []

    # 2. frozen order (shut gate -> install -> open gate): A was installed at
    #    build time under a shut gate -- assert shut, snapshot pinned to A,
    #    then open and run bellwether.
    assert json.loads(harness.stack.release_path.read_text(encoding="utf-8"))["status"] == "closed"
    assert _route_names() == [("rem", alias) for alias in sorted(ALIASES)]
    harness.stack.release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    assert _bellwether("rem", "primary", FORMAL_TOKEN, 702)[0] == 200
    assert _bellwether("mai", "primary", FORMAL_TOKEN, 703)[0] == 404
    assert len(_T10_POSTS) == 1
    assert _route_names() == [("rem", alias) for alias in sorted(ALIASES)]

    # 3. closed (same A): admitted route now refused, zero new inference.
    harness.stack.release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    assert _bellwether("rem", "primary", FORMAL_TOKEN, 704)[0] == 403
    assert len(_T10_POSTS) == 1

    # 4. frozen order: assert shut before install, install union, snapshot
    #    pinned to union, then open and run bellwether.
    assert json.loads(harness.stack.release_path.read_text(encoding="utf-8"))["status"] == "closed"
    harness.provider.update(raw_u)
    assert _route_names() == [
        (soul, alias) for soul in sorted(ALL_SOULS) for alias in sorted(ALIASES)
    ]
    harness.stack.release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    assert _bellwether("mai", "primary", FORMAL_TOKEN, 705)[0] == 200
    assert _bellwether("mahiru", "mimo-v2.6-flash", FORMAL_TOKEN, 706)[0] == 200
    assert len(_T10_POSTS) == 3
    assert _route_names() == [
        (soul, alias) for soul in sorted(ALL_SOULS) for alias in sorted(ALIASES)
    ]

    # 5. closed + union: union route refused, zero new inference.
    harness.stack.release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    assert _bellwether("mai", "primary", FORMAL_TOKEN, 707)[0] == 403
    assert len(_T10_POSTS) == 3

    # 6. frozen order: assert shut before install, restore pinned A, snapshot
    #    pinned to A, then open and run bellwether.
    assert json.loads(harness.stack.release_path.read_text(encoding="utf-8"))["status"] == "closed"
    harness.provider.update(raw_a)
    assert _route_names() == [("rem", alias) for alias in sorted(ALIASES)]
    harness.stack.release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    assert _bellwether("rem", "glm-5.3-flash", FORMAL_TOKEN, 708)[0] == 200
    assert _bellwether("mai", "primary", FORMAL_TOKEN, 709)[0] == 404
    assert _bellwether("rem", "void-unknown", FORMAL_TOKEN, 710, model="sa-formal-void")[0] == 404
    assert len(_T10_POSTS) == 4
    assert _route_names() == [("rem", alias) for alias in sorted(ALIASES)]
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        harness.stack.factory(
            _snapshot_for("mai", "void-unknown", "sa-formal-void", 711)
        )
    assert exc_info.value.status == 500
    assert len(_T10_POSTS) == 4


def test_t10_wrong_order_install_while_open_leaks_detected(monkeypatch, tmp_path):
    """Reverse-order control (forbidden by runbook section 3): opening the gate
    before installing admits old-snapshot traffic, so the freeze oracle of zero
    admission before opening is violated. Detecting the leak is a pass; this
    sequence must never be used for real installs."""
    raw_a = _formal_raw("registry.a.json")
    raw_u = _formal_raw("registry.union.json")
    harness = _build_harness(monkeypatch, tmp_path, raw_a, release_status="closed")
    _T10_POSTS.clear()
    # Wrong order: open first, install second.
    harness.stack.release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    snapshot = _snapshot_for("rem", "primary", MODEL_OF[("rem", "primary")], 801)
    status, _payload = _outer_post(harness, "rem", "primary", FORMAL_TOKEN, snapshot)
    assert status == 200  # old-snapshot traffic admitted while open
    leaked_before_install = len(_T10_POSTS)
    assert leaked_before_install == 1
    # The admission happened before update() ran: installing now cannot undo
    # the leak, which is exactly what the frozen order forbids.
    harness.provider.update(raw_u)
    assert leaked_before_install > 0
    assert len(_T10_POSTS) == leaked_before_install
