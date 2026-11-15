"""SA-LIVE-ENTRY-FIX-1 T21 formal live-entry acceptance (sandbox only).

Decision-complete scope (SA-LIVE-ENTRY-FIX-1): product edits are the
single authorized line in ``src/soul_access/wiring.py`` (the formal route
``_sa_live_route`` gains ``body: dict``; untyped ``body`` was bound by
FastAPI as a query param so every JSON POST died with 422) plus the
default-OFF SA evidence passthrough in ``scripts/run_server.py``
(``_sa_evidence_sink_for_batch`` + ``evidence_sink=`` on the existing
factory; unset env => None => legacy behavior bit-identical). Frozen files
(bridge / api / release / schema / SA-0台帳/判準, formal params, gate
order, memory rules, decay) are untouched; no server is started, no
production port is bound, no real request leaves the process.

All test names carry the t21 marker: ``pytest -q tests/soul_access -k t21``
must collect > 0. Transport is memory-only: the formal endpoint is mounted
DIRECTLY on a real FastAPI app (``mount_live_route`` registers it; the
returned product function is held for the refusal check) and driven through
``httpx.ASGITransport`` in-process, never a port. NO self-added typed
wrapper is mounted anywhere (the T10 ``_entry(body: dict)`` shim masked this
very defect and must not be repeated). The only bottom-I/O seam replaced is
the lowest HTTP transport inside ``OpenAIBackend.complete`` (a counting
stand-in answering one fixed 200 reply with native non-empty usage).
Storage is sandbox-only: a self-created tmp release file, a tmp session
store, and caller-owned tmp evidence dirs (never production state, never
the pinned path). Sync tests only.

Coverage (ticket acceptance mapping):
  a. legal SA packet reaches the formal endpoint (200, no 422);
  b. refusal path performs zero outbound calls (403, posts == []);
  c. raw request / response / usage triple pairs completely (verify_pair);
  d. mutation: untyped body must turn RED (AST pin + pre-fix 422);
  e. mutation: removed sink must turn RED (admitted but zero evidence);
  f. run_server evidence switch defaults OFF and never creates directories.
"""

from __future__ import annotations

import asyncio
import ast
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
from src.llm import proxy as llm_proxy  # noqa: E402,F401 (patch target must import)
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

# -- module-level loop (import time; same queue-runner shape as the T10 file) ----
# Windows note: this MUST be an explicit SelectorEventLoop, not new_event_loop()
# (which returns a ProactorEventLoop here). The SA-0 conftest replaces
# socket.socket with a plain function per test; the proactor's self-pipe path
# does isinstance(conn, socket.socket) on every cross-thread wakeup, which then
# raises TypeError inside the loop ("Error on reading from the event loop self
# pipe") and the wakeup is lost. A sync FastAPI route (which is exactly what
# the formal endpoint is) runs in an anyio worker thread whose completion is
# delivered via that wakeup => the admitted ASGI call hangs forever. The
# selector loop's wakeup uses only its import-time-created pipe objects (no
# isinstance against the patched attribute), so it survives the block. Loop
# creation + self-pipe stay at import time (before any block), same as T10/T1.

_T21_LOOP = asyncio.SelectorEventLoop()


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


_ASGI_RUNNER = _Runner(_T21_LOOP, "t21-asgi")

# Real transport handles, saved before any per-test patch replaces the class
# attribute on the shared httpx module object.
_REAL_ASYNC_CLIENT = httpx.AsyncClient
_REAL_ASGI_TRANSPORT = httpx.ASGITransport

assert llm_proxy.OpenAIBackend is not None

# -- synthetic T21 fixtures (never production identity) -------------------------

OWNER = "t21-live-entry-owner"
SCOPE = "owner_private"
AGENT = "agent_rem"
MODEL = "t21-live-entry-model"
REPLY = "t21-live-entry-reply"
SENTINEL_SECRET = "t21-sandbox-secret-9c2e4a1f"
NON_ROUTABLE_BASE = "https://t21-live-entry.invalid/v1/chat/completions"
USAGE = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

_T21_POSTS: list = []


def _identity(n):
    req = "req-t21-%04d" % n
    return sa_schema.RequestIdentity(
        request_id=req,
        conversation_id="dm",
        branch_id="root",
        turn_id="turn-%s" % req,
        idempotency_key="key-t21-%04d" % n,
    )


def _snapshot_for(n):
    req = "req-t21-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(n),
        dedup=sa_schema.DedupKey(
            principal_id=OWNER,
            agent_id=AGENT,
            scope=SCOPE,
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t21-%04d" % n,
        ),
        backend_alias="primary",
        model=MODEL,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t21-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-t21-v1",
        projection_state="projection-t21-v1",
        recall_version="recall-t21-v1",
        recall_items=("recall-t21-item-1",),
        history_pool_version="pool-t21-v1",
        clean_history=("hist-t21-item-1",),
    )


def _envelope(n=1):
    return sa_api.build_envelope(
        snapshot=_snapshot_for(n),
        messages=(("user", "t21 probe"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _write_release(path: Path, status: str) -> Path:
    path.write_text(json.dumps({"status": status}), encoding="utf-8")
    return path


# -- fake bottom transport (the ONLY replaced seam) ------------------------------

def _transport_body():
    return {
        "id": "chatcmpl-t21",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": REPLY},
                "finish_reason": "stop",
            }
        ],
        "usage": dict(USAGE),
    }


def _raw_of(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


class _Fake200:
    """httpx.Response stand-in: verbatim raw text + parsed JSON, no I/O."""

    status_code = 200

    def __init__(self, raw_text, body):
        self._raw_text = raw_text
        self._body = body

    @property
    def text(self):
        return self._raw_text

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class _CountingAsyncClient:
    """Bottom-transport stand-in: one fixed 200 reply, every post logged."""

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        body = dict(json or {})
        _T21_POSTS.append({"url": url, "model": body.get("model"), "json": body})
        wire = _transport_body()
        return _Fake200(_raw_of(wire), wire)


def _patch_bottom_transport(monkeypatch):
    _T21_POSTS.clear()
    monkeypatch.setattr("src.llm.proxy.httpx.AsyncClient", _CountingAsyncClient)


# -- loop-free inference submit (T14 idiom; no loop is ever created here) ---------

def _pump(coro):
    send_value = None
    throw_exc = None
    started = False
    while True:
        try:
            if throw_exc is not None:
                exc, throw_exc = throw_exc, None
                yielded = coro.throw(exc)
            elif not started:
                started = True
                yielded = coro.send(None)
            else:
                yielded = coro.send(send_value)
        except StopIteration as done:
            return done.value
        try:
            if asyncio.iscoroutine(yielded):
                send_value = _pump(yielded)
            else:
                raise AssertionError(
                    "t21: fake suspended on %r (a real loop would be needed)" % (yielded,)
                )
        except BaseException as exc:
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _immediate_submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_pump(coro))
    return _ImmediateFuture(coro)


# -- formal stack: the REAL endpoint mounted on a REAL FastAPI app ----------------
# No wrapper, no re-typing: mount_live_route registers the product function
# itself; the returned object IS that product function.

def _build_stack(monkeypatch, tmp_path, release_status="open", evidence=True):
    """Assemble the formal live stack on sandbox storage."""
    _patch_bottom_transport(monkeypatch)
    release_path = _write_release(tmp_path / "release.json", release_status)
    llm_holder = SimpleNamespace(
        backend=llm_proxy.OpenAIBackend(
            api_key=SENTINEL_SECRET,
            base_url=NON_ROUTABLE_BASE,
        )
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "ev") if evidence else None
    factory = wiring.build_dialog_factory(
        llm=llm_holder, loop=object(), resolve_backend=None,
        submit=_immediate_submit, evidence_sink=sink,
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
    app = FastAPI()
    live = sa_bridge.attach_live_loop(
        app, bus=bus, llm=llm_holder, middleware=middleware
    )
    route_fn = wiring.mount_live_route(
        app,
        live=live,
        generate=generate,
        release_path=str(release_path),
    )
    assert route_fn.sa_live_path == wiring.INTERNAL_LIVE_PATH
    paths = [getattr(route, "path", None) for route in app.routes]
    assert wiring.INTERNAL_LIVE_PATH in paths
    return SimpleNamespace(
        app=app, route_fn=route_fn, sink=sink, release_path=release_path,
    )


def _post(app, body):
    async def _go():
        client = _REAL_ASYNC_CLIENT(
            transport=_REAL_ASGI_TRANSPORT(app=app),
            base_url="http://test",
        )
        return await client.post(wiring.INTERNAL_LIVE_PATH, json=body)

    return _ASGI_RUNNER.run(_go())


def _sink_files(sink):
    return sorted(
        p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
    )


# -- T21-a: legal packet reaches the formal endpoint (no 422) ---------------------

def test_t21_live_entry_formal_endpoint_reachable_no_422(tmp_path, monkeypatch):
    stack = _build_stack(monkeypatch, tmp_path, release_status="open")
    body = sa_api.envelope_to_body(_envelope())

    response = _post(stack.app, body)

    assert response.status_code == 200, "formal endpoint must not 422 a legal packet"
    assert response.json() == REPLY
    assert len(_T21_POSTS) == 1  # exactly one inference attempt, max_retries=0


# -- T21-b: refusal path performs zero outbound calls ------------------------------

def test_t21_live_entry_refusal_zero_outbound(tmp_path, monkeypatch):
    stack = _build_stack(monkeypatch, tmp_path, release_status="closed")
    body = sa_api.envelope_to_body(_envelope())

    with pytest.raises(sa_bridge.LiveRefused) as caught:
        stack.route_fn(body)

    assert caught.value.status == 403
    assert _T21_POSTS == []  # closed gate: nothing was ever sent


# -- T21-c: raw request / response / usage triple pairs completely ------------------

def test_t21_live_entry_evidence_triple_pair_complete(tmp_path, monkeypatch):
    stack = _build_stack(monkeypatch, tmp_path, release_status="open")
    body = sa_api.envelope_to_body(_envelope())

    response = _post(stack.app, body)

    assert response.status_code == 200
    assert response.json() == REPLY
    assert len(_T21_POSTS) == 1
    sent_body = _T21_POSTS[0]["json"]

    names = _sink_files(stack.sink)
    assert len(names) == 2
    request_id = names[0].split(".request.json")[0]
    assert names == ["%s.request.json" % request_id, "%s.response.json" % request_id]

    request = json.loads((stack.sink.directory / names[0]).read_text(encoding="utf-8"))
    stored_response = json.loads(
        (stack.sink.directory / names[1]).read_text(encoding="utf-8")
    )

    # Pre-send payload == the ACTUAL sent body (replayable), hash matches.
    assert request["payload"] == sent_body
    assert request["payload_sha256"] == sa_evidence.sha256_hex(
        sa_evidence.canonical_bytes(sent_body)
    )
    # Post-return raw == bottom-transport verbatim, usage native, same id.
    assert stored_response["raw_text"] == _raw_of(_transport_body())
    assert stored_response["usage"] == USAGE
    assert stored_response["request_id"] == request_id == request["request_id"]
    report = stack.sink.verify_pair(request_id)
    assert report["pair_ok"] is True

    # No credentials stored: variable NAME present, secret value absent.
    for name in names:
        content = (stack.sink.directory / name).read_text(encoding="utf-8")
        assert sa_evidence.KEY_REF in content
        assert SENTINEL_SECRET not in content


# -- T21-d: mutation pin -- untyped body must turn RED --------------------------------
# The defect was an untyped ``body`` param (FastAPI bound it as query => 422
# on every JSON POST). This AST pin fails the moment the annotation is
# removed; the reachable test above independently 422s on reverted code.

def test_t21_mutation_body_untyped_turns_red_ast():
    src = Path(wiring.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    checked = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_sa_live_route":
            params = list(node.args.posonlyargs) + list(node.args.args)
            assert params, "MUTATION DUD -- STOP, report: route takes no params"
            annotation = params[0].annotation
            assert annotation is not None, (
                "RED: _sa_live_route body lost its type (JSON POST would 422)"
            )
            assert ast.unparse(annotation) == "dict", (
                "RED: _sa_live_route body is not typed dict (JSON POST would 422)"
            )
            checked = True
    if not checked:
        pytest.fail("MUTATION DUD -- STOP, report: _sa_live_route not found")


# -- T21-e: mutation -- removed sink must turn RED --------------------------------------
# MUTANT: the factory is built without the sink (== the run_server
# default-OFF path). The call is still admitted and answered, but no raw /
# request / response / usage triple exists, so the ticket's evidence gate
# ("no original evidence == fail") trips: RED.

def test_t21_mutation_sink_removed_no_evidence_red(tmp_path, monkeypatch):
    stack = _build_stack(
        monkeypatch, tmp_path, release_status="open", evidence=None
    )
    assert stack.sink is None
    body = sa_api.envelope_to_body(_envelope())

    response = _post(stack.app, body)

    assert response.status_code == 200  # reachable, reply intact ...
    assert response.json() == REPLY
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir(parents=True, exist_ok=True)
    leftovers = [p.name for p in ev_dir.iterdir() if not p.name.startswith(".tmp-")]
    if leftovers:
        pytest.fail(
            "MUTATION DUD -- STOP, report: sink-less run still landed evidence"
        )
    # RED confirmed: zero original-evidence files, no pair can verify.
    assert leftovers == []


# -- T21-f: run_server evidence switch defaults OFF, never creates dirs ---------------
# run_server.py is NOT imported here (module import touches production
# state); these are source-level structural pins over the exact switch.

def test_t21_run_server_evidence_default_off_wiring():
    text = (_REPO_ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
    tree = ast.parse(text)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert "_sa_evidence_sink_for_batch" in fns
    segment = ast.get_source_segment(text, fns["_sa_evidence_sink_for_batch"])
    assert segment is not None
    assert "SOULOS_SA_EVIDENCE_DIR" in segment
    assert 'os.getenv("SOULOS_SA_EVIDENCE_DIR", "")' in segment  # default OFF
    assert "is_dir" in segment  # caller-created dir only ...
    assert "mkdir" not in segment  # ... never created here
    assert segment.count("return None") >= 2  # OFF path + any-fault path
    assert text.count("EvidenceSink(") == 1  # single construction site
    assert "evidence_sink=_sa_evidence" in text  # passthrough, existing factory
