"""SA-LOOP-OFFLINE-1: manual pump NoEventLoopError hypothesis, offline control.

Ticket hypothesis: B4's live-fire sender drives the dialog coroutine with a
manual coroutine pump (``_run``: explicit ``coro.send`` / ``coro.throw``, no
event loop is ever created -- byte-copied here from
``verification/sa-open-fire-1/live-fire-4/live_fire_b4_real.py:137``), while
the B4 failure record
(``verification/sa-open-fire-1/live-fire-4/failure/req-sa-open-fire-1-0001.json``)
shows ``outer_exception == ["LiveRefused", "NoEventLoopError"]``. This file
tests whether the pump is the cause, with a zero-external-connection control.

Real imports kept (per ticket): the real ``OpenAIBackend``, the real
``httpx``/``httpcore`` modules, the real ``wiring.build_dialog_factory``.
The ONLY mock sits at the lowest transport layer that still proves the point:
``httpcore._async.connection.AsyncHTTPConnection.handle_async_request`` (the
origin-dispatching wrapper, one level below the pool) returns a canned
provider body with native usage. It is installed for ALL phases, which is
exactly what makes the experiment decisive:

  - old-pump phases die INSIDE the pool (``_close_connections`` ->
    ``AsyncShieldCancellation`` -> ``anyio.CancelScope`` ->
    ``get_async_backend()`` -> ``NoEventLoopError``) BEFORE ever reaching the
    mocked wrapper (pool wrapper calls stay 0);
  - the candidate phase runs the same coroutine on a real single-threaded
    loop (``asyncio.new_event_loop`` + ``run_until_complete``, same thread, no
    sockets) so the pool internals work and the mocked wrapper returns the
    canned body exactly once (zero retry).

Network hard blocks (execution-time, never "should not happen" predictions):
repo conftest already hard-blocks ``socket.socket`` / ``create_connection`` /
``create_server``; this file additionally blocks ``socket.getaddrinfo`` /
``gethostbyname`` / ``getnameinfo`` (any real TCP needs one of these or a
socket first) and counts every attempt. Fake credential value
(``FAKE-KEY-...``) travels only in the in-memory Authorization header; all
landed files stay under per-phase ``tmp_path`` dirs and are scanned for the
secret afterwards.

Acceptance sequence in one test: old path RED (``LiveRefused`` caused by
``NoEventLoopError``, full stack printed, B4-shaped observation + lone
pre-send request file) -> candidate GREEN (canned raw/usage pair, ``pair_ok``,
exactly 1 transport call) -> restored old path RED again. If the old path
ever stops raising ``NoEventLoopError``, the hypothesis does NOT reproduce:
that outcome is INCONCLUSIVE by ticket rule (no overreach to the four live
failures), and the asserts below fail loudly with the actual chain printed.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# Captured at module import (collection runs BEFORE the SA-0 conftest fixture
# installs its per-test ``socket.socket`` block): the only window where the
# genuine class is reachable. Used solely to build the candidate loop's
# internal self-pipe (loopback pair, never connected anywhere); the loop then
# runs with the block reinstalled, so any real socket use during the run
# still raises.
_REAL_SOCKET_CLASS = socket.socket

from anyio import NoEventLoopError  # noqa: E402 (real exception class)
import httpcore  # noqa: E402 (real module; one method patched below)
from httpcore._async.connection import (  # noqa: E402 (real class)
    AsyncHTTPConnection,
)

from src.llm.proxy import OpenAIBackend  # noqa: E402 (real backend)
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import failure_observation as fa_obs  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402 (real factory)

DIALOG_REQUEST_ID = "req-sa-loop-offline-1-0001"
AGENT = "agent_rem"
MODEL = "loop-probe-model-v1"
MESSAGE = "你好。"
FAKE_KEY_VALUE = "FAKE-KEY-SA-LOOP-OFFLINE-1-NEVER-SENT"
KEY_REF_NAME = "LOOP_FAKE_KEY_REF"

CANNED_TEXT = "候選迴圈離線回覆"
CANNED_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}


def _canned_body_bytes() -> bytes:
    return json.dumps(
        {
            "id": "loop-candidate-synthetic",
            "object": "chat.completion",
            "choices": [
                {
                    "message": {"role": "assistant", "content": CANNED_TEXT},
                    "finish_reason": "stop",
                }
            ],
            "usage": dict(CANNED_USAGE),
        },
        ensure_ascii=False,
    ).encode("utf-8")


# -- offline transport fixture (lowest-layer mock + network hard blocks) ------

_TRANSPORT = SimpleNamespace(calls=[], attempts=[])


def _blocked_external(*args, **kwargs):
    _TRANSPORT.attempts.append((args, sorted(kwargs.keys())))
    raise RuntimeError("SA-LOOP-OFFLINE-1: external entry hard-blocked")


async def _fake_wrapper_handle_async(self, request):
    """Canned origin-wrapper reply: no socket, no TLS, no bytes on any wire."""
    stream = request.stream
    if hasattr(stream, "__iter__"):
        raw = b"".join(stream)
    else:  # pragma: no cover - defensive; httpx always hands sync content here
        parts = [part async for part in stream]
        raw = b"".join(parts)
    body = json.loads(raw.decode("utf-8"))
    _TRANSPORT.calls.append(
        {
            "method": request.method.decode()
            if isinstance(request.method, bytes)
            else str(request.method),
            "scheme": bytes(request.url.scheme).decode("ascii"),
            "host": bytes(request.url.host).decode("ascii", "replace"),
            "header_names": sorted(
                k.decode("latin-1") if isinstance(k, bytes) else str(k)
                for k, _v in request.headers
            ),
            "body": body,
        }
    )
    content = _canned_body_bytes()
    return httpcore.Response(
        200,
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", str(len(content)).encode("ascii")),
        ],
        content=content,
        extensions={b"http_version": b"HTTP/1.1", b"reason_phrase": b"OK"},
    )


@pytest.fixture(autouse=True)
def _offline_transport(monkeypatch):
    """Static for the whole file: mock BELOW the pool, blocks ABOVE sockets."""
    _TRANSPORT.calls.clear()
    _TRANSPORT.attempts.clear()
    monkeypatch.setattr(
        AsyncHTTPConnection, "handle_async_request", _fake_wrapper_handle_async
    )
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_external)
    monkeypatch.setattr(socket, "gethostbyname", _blocked_external)
    monkeypatch.setattr(socket, "getnameinfo", _blocked_external)
    yield _TRANSPORT


# -- two submit paths, same input ---------------------------------------------

def _run(coro):
    """Manual coroutine pump, NO loop creation, NO threads.

    Byte-faithful copy of ``live_fire_b4_real.py:_run`` (the B4 old path).
    """
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
                send_value = _run(yielded)
            else:
                raise AssertionError("loop-offline-1: fake suspend on %r" % (yielded,))
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _old_submit(loop, coro):
    """B4 old path submit: pump the real backend coroutine with no loop."""
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


def _candidate_submit(loop, coro):
    """Candidate path submit: one real loop, same thread, no sockets.

    Single-threaded (the calling thread runs the loop itself) and socket-free
    (transport is answered by the in-process wrapper fake, so the loop never
    performs I/O). The loop exists only so pool internals (anyio-backed
    ``AsyncShieldCancellation`` / events) can resolve a backend.

    Windows loop construction needs its internal loopback self-pipe
    (``socket.socketpair`` -> the ``socket.socket`` class, hard-blocked by the
    SA-0 conftest). The genuine class (captured at import) is restored ONLY
    around ``new_event_loop()`` -- an unconnected loopback pair, never an
    external connection -- and the block is reinstalled before the loop runs,
    so any socket creation during the run still raises and is counted.
    """
    blocked = socket.socket
    socket.socket = _REAL_SOCKET_CLASS
    try:
        fresh = asyncio.new_event_loop()
    finally:
        socket.socket = blocked

    def _counting_block(*args, **kwargs):
        _TRANSPORT.attempts.append(("socket.socket", ()))
        return blocked(*args, **kwargs)

    # A class (not a closure): asyncio internals call
    # ``isinstance(conn, socket.socket)`` on the loop self-pipe, which needs a
    # type. ``blocked`` raises on any real call, so ``__new__`` never returns.
    CountingBlock = type(
        "_CountingBlock", (), {"__new__": lambda cls, *a, **k: _counting_block(*a, **k)}
    )
    socket.socket = CountingBlock
    try:
        value = fresh.run_until_complete(coro)
    finally:
        socket.socket = blocked
        try:
            fresh.close()
        except Exception:  # noqa: BLE001
            pass
    return _ImmediateFuture(value)


# -- shared input (identical for both paths) ----------------------------------


def _snapshot() -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-loop-1",
            idempotency_key="key-loop-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id="loop-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-loop-1",
        ),
        backend_alias="primary",
        model=MODEL,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-14T00:00:00Z",
        personality_version="personality-loop-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-14T15:45:00-05:00",
        inner_life_state="inner-loop-v1",
        projection_state="projection-loop-v1",
        recall_version="recall-loop-v1",
        recall_items=("recall-loop-item-1",),
        history_pool_version="pool-loop-v1",
        clean_history=("hist-loop-item-1",),
    )


def _make_factory(submit, sink):
    backend = OpenAIBackend(api_key=FAKE_KEY_VALUE, base_url="http://127.0.0.1:9/x")
    llm = SimpleNamespace(backend=backend)
    return wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=submit, evidence_sink=sink
    )


def _red_asserts(exc, sink, *, phase):
    """Old-path RED signature: B4 failure-file shape, full stack attached."""
    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    print("---- %s old-path full stack ----\n%s" % (phase, tb_text))
    assert isinstance(exc.__cause__, NoEventLoopError), (
        "%s: cause is %r, not NoEventLoopError\n%s" % (phase, exc.__cause__, tb_text)
    )
    assert fa_obs.outer_chain(exc) == ["LiveRefused", "NoEventLoopError"], (
        "%s: outer chain drift\n%s" % (phase, tb_text)
    )
    assert "NoEventLoopError" in tb_text
    assert "AsyncShieldCancellation" in tb_text
    rec = fa_obs.build_observation(exc, dialog_request_id=DIALOG_REQUEST_ID)
    assert rec["snapshot_status"] == "absent"
    assert rec["snapshot_reason"] == "snapshot_absent"
    assert rec["type"] == "unknown"
    assert rec["phase"] == "unknown"
    assert rec["attempts"] == []
    assert rec["transport_calls"] is None
    assert rec["outer_exception"] == ["LiveRefused", "NoEventLoopError"]
    names = sorted(p.name for p in sink.directory.iterdir())
    assert len(names) == 1 and names[0].endswith(".request.json"), names
    assert [p for p in sink.directory.iterdir() if p.suffixes == [".response", ".json"]] == []
    return tb_text


# -- acceptance sequence: RED -> GREEN -> RED ---------------------------------


def test_loop_manual_pump_no_loop_vs_single_thread_loop(tmp_path):
    # Phase 1: OLD path must be RED (reproduces NoEventLoopError).
    sink1 = sa_evidence.EvidenceSink(tmp_path / "phase1", key_ref=KEY_REF_NAME)
    dialog1 = _make_factory(_old_submit, sink1)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei1:
        dialog1((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _red_asserts(ei1.value, sink1, phase="phase1")
    assert _TRANSPORT.calls == []  # died in the pool, wrapper never reached

    # Phase 2: CANDIDATE path must be GREEN (same input, real loop + mock).
    sink2 = sa_evidence.EvidenceSink(tmp_path / "phase2", key_ref=KEY_REF_NAME)
    dialog2 = _make_factory(_candidate_submit, sink2)(_snapshot())
    text = dialog2((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert text == CANNED_TEXT
    assert len(_TRANSPORT.calls) == 1  # single attempt: fixed zero retry
    call = _TRANSPORT.calls[0]
    assert call["scheme"] == "http" and call["method"] == "POST"
    assert call["body"]["model"] == MODEL
    assert [m["content"] for m in call["body"]["messages"]] == [MESSAGE]
    assert FAKE_KEY_VALUE not in json.dumps(call["body"])
    names2 = sorted(p.name for p in sink2.directory.iterdir())
    assert len(names2) == 2, names2
    evidence_request_id = names2[0].split(".request.json")[0]
    report = sink2.verify_pair(evidence_request_id)
    assert report["pair_ok"], report
    request_doc = json.loads((sink2.directory / names2[0]).read_text(encoding="utf-8"))
    response_doc = json.loads((sink2.directory / names2[1]).read_text(encoding="utf-8"))
    assert request_doc["payload_sha256"] == sa_evidence.sha256_hex(
        sa_evidence.canonical_bytes(call["body"])
    )
    assert response_doc["raw_sha256"] == sa_evidence.sha256_hex(_canned_body_bytes())
    assert response_doc["usage"] == CANNED_USAGE
    print(
        "phase2 GREEN: transport_calls=1 pair_ok=True usage=%s raw_prefix=%s"
        % (
            json.dumps(response_doc["usage"], sort_keys=True),
            response_doc["raw_sha256"][:16],
        )
    )
    for path in sorted(tmp_path.rglob("*")):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            assert FAKE_KEY_VALUE not in content, path
    for name in names2:
        content = (sink2.directory / name).read_text(encoding="utf-8")
        assert "Bearer" not in content and "api_key" not in content, name
        assert KEY_REF_NAME in content, name

    # Phase 3: restore OLD path -> RED again (candidate changed nothing global).
    sink3 = sa_evidence.EvidenceSink(tmp_path / "phase3", key_ref=KEY_REF_NAME)
    dialog3 = _make_factory(_old_submit, sink3)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei3:
        dialog3((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _red_asserts(ei3.value, sink3, phase="phase3")
    assert len(_TRANSPORT.calls) == 1  # old phases never reach the transport

    # Whole run: zero external entries, zero real I/O.
    assert _TRANSPORT.attempts == []
