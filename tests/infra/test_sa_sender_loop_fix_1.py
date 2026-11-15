"""SA-SENDER-LOOP-FIX-1: B4 probe sender on a real event loop, offline proof.

Scope (ticket SA-SENDER-LOOP-FIX-1): ONLY
``verification/sa-open-fire-1/live-fire-4/live_fire_b4_real.py`` ``_run`` /
``_submit`` were changed (manual coroutine pump -> one ``asyncio.run`` per
call). This file proves, offline, that the shipped fix walks the real
program path once with zero retries while the old pump dies with the
B4-shaped ``LiveRefused`` caused by ``NoEventLoopError``.

What is RED vs GREEN here:
  - RED (old pump): ``LiveRefused`` whose cause is ``NoEventLoopError``,
    outer chain ``["LiveRefused", "NoEventLoopError"]``, B4-shaped failure
    observation, lone pre-send ``.request.json`` (pool wrapper never
    reached, transport calls stay 0).
  - GREEN (fixed ``_run`` / ``_submit`` imported from the worktree probe
    file -- read-only import, never executed as a script, never a real
    call): canned provider body + native usage, ``pair_ok``, exactly 1
    transport call, zero retry.

Acceptance sequence: RED -> GREEN -> RED -> GREEN (old, fixed, old,
fixed). Mutations M1..M4 run on isolated in-memory copies / behavioral
variants only -- the worktree file is never overwritten, never reverted.

Real imports kept: real ``OpenAIBackend``, real ``httpx`` / ``httpcore``
modules, real ``wiring.build_dialog_factory``. The ONLY mock sits at the
lowest transport layer: ``httpcore._async.connection.AsyncHTTPConnection.
handle_async_request`` returns a canned provider body. Execution-time
network hard blocks (never "should not happen" predictions):
``socket.getaddrinfo`` / ``gethostbyname`` / ``getnameinfo`` raise and are
counted; ``socket.socket`` is a counting subclass of the genuine class
(construction recorded, ``connect`` recorded + delegated) so the loop
self-pipe works while any real peer attempt would be observed. Fake
credential value travels only in the in-memory Authorization header; all
landed files stay under per-test ``tmp_path`` dirs and are scanned for
the secret afterwards.

Conclusion discipline: a GREEN here proves the PROBE sender fix only. It
says nothing about the product path, end-to-end behaviour, or a B4
diagnosis -- the mock stands in for the wire.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import socket
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

# Captured at import (collection runs before any fixture): the genuine
# class. The counting subclass below delegates to it, so loop self-pipes
# work while every Python-level construction / connect is observed.
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

_PROBE_PATH = (
    _REPO_ROOT
    / "verification"
    / "sa-open-fire-1"
    / "live-fire-4"
    / "live_fire_b4_real.py"
)

DIALOG_REQUEST_ID = "req-sa-sender-fix-1-0001"
AGENT = "agent_rem"
MODEL = "sender-fix-probe-model-v1"
MESSAGE = "你好。"
FAKE_KEY_VALUE = "FAKE-KEY-SA-SENDER-FIX-1-NEVER-SENT"
KEY_REF_NAME = "SA_SENDER_FIX_FAKE_KEY_REF"

CANNED_TEXT = "發送器修復離線迴覆"
CANNED_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}


def _canned_body_bytes() -> bytes:
    return json.dumps(
        {
            "id": "sender-fix-synthetic",
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


# -- fixed probe import (read-only; never runs the probe script) -------------

_FIXED = {"mod": None}


def _clean_import(before_env, before_mods):
    """Restore process env + module table (probe self-loads dotenv at import)."""
    os.environ.clear()
    os.environ.update(before_env)
    for key in [k for k in sys.modules if k not in before_mods]:
        del sys.modules[key]


def _load_fixed():
    """Import the worktree probe file read-only; return its module."""
    if _FIXED["mod"] is not None:
        return _FIXED["mod"]
    before_env = dict(os.environ)
    before_mods = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(
            "live_fire_b4_real_fixed", str(_PROBE_PATH)
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        # The probe self-loads dotenv at import; keep the test process env
        # AND module table exactly as they were (no credential-shaped values
        # linger; later dotenv-sensitive tests see a pristine sys.modules).
        _clean_import(before_env, before_mods)
    assert hasattr(mod, "_run") and hasattr(mod, "_submit"), (
        "fixed probe module shape drift: %s" % sorted(dir(mod))
    )
    _FIXED["mod"] = mod
    return mod


# -- old pump (byte-faithful copy of the pre-fix probe path, RED reference) --

OLD_PUMP_TEXT = '''def _run(coro):
    """Synchronous coroutine pump (no loop creation, no threads)."""
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
                raise AssertionError("live-fire-4: fake suspend on %r" % (yielded,))
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc
'''


def _old_run(coro):
    """Manual coroutine pump, NO loop creation, NO threads (pre-fix path)."""
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
                send_value = _old_run(yielded)
            else:
                raise AssertionError("sender-fix-1: fake suspend on %r" % (yielded,))
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _old_submit(loop, coro):
    """Pre-fix submit: pump the real backend coroutine with no loop."""
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_old_run(coro))
    return _ImmediateFuture(coro)


# -- offline transport fixture (lowest-layer mock + network hard blocks) -----

_TRANSPORT = SimpleNamespace(calls=[], attempts=[], connects=[], sockets=[])


def _blocked_external(*args, **kwargs):
    _TRANSPORT.attempts.append((args, sorted(kwargs.keys())))
    raise RuntimeError("SA-SENDER-FIX-1: external entry hard-blocked")


def _is_loopback(address) -> bool:
    """Proactor self-pipe connects stay on loopback; anything else is external."""
    try:
        host = address[0] if isinstance(address, tuple) else str(address)
    except Exception:  # noqa: BLE001
        return False
    if host == "127.0.0.1" or host == "::1" or host == "localhost":
        return True
    try:
        import ipaddress as _ip

        return _ip.ip_address(host).is_loopback
    except Exception:  # noqa: BLE001
        return False


class _CountingSocket(_REAL_SOCKET_CLASS):
    """Genuine socket that records construction + connect attempts."""

    def __new__(cls, *args, **kwargs):
        _TRANSPORT.sockets.append((args, sorted(kwargs.keys())))
        return super().__new__(cls, *args, **kwargs)

    def connect(self, address, *args, **kwargs):
        _TRANSPORT.connects.append(address)
        return super().connect(address, *args, **kwargs)


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
    _TRANSPORT.connects.clear()
    _TRANSPORT.sockets.clear()
    monkeypatch.setattr(
        AsyncHTTPConnection, "handle_async_request", _fake_wrapper_handle_async
    )
    monkeypatch.setattr(socket, "getaddrinfo", _blocked_external)
    monkeypatch.setattr(socket, "gethostbyname", _blocked_external)
    monkeypatch.setattr(socket, "getnameinfo", _blocked_external)
    monkeypatch.setattr(socket, "socket", _CountingSocket)
    yield _TRANSPORT


# -- shared input (identical for old and fixed paths) ------------------------


def _snapshot() -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-fix-1",
            idempotency_key="key-fix-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id="fix-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fix-1",
        ),
        backend_alias="primary",
        model=MODEL,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-14T00:00:00Z",
        personality_version="personality-fix-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-14T15:45:00-05:00",
        inner_life_state="inner-fix-v1",
        projection_state="projection-fix-v1",
        recall_version="recall-fix-v1",
        recall_items=("recall-fix-item-1",),
        history_pool_version="pool-fix-v1",
        clean_history=("hist-fix-item-1",),
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


def _green_asserts(text, sink, *, phase, expect_calls=1):
    """Fixed-path GREEN signature: one walk, zero retry, pair verified."""
    assert text == CANNED_TEXT, ("%s: reply drift %r" % (phase, text))
    assert len(_TRANSPORT.calls) == expect_calls, (
        "%s: transport calls %d, want %d" % (phase, len(_TRANSPORT.calls), expect_calls)
    )
    if expect_calls:
        call = _TRANSPORT.calls[-1]
        assert call["scheme"] == "http" and call["method"] == "POST"
        assert call["body"]["model"] == MODEL
        assert [m["content"] for m in call["body"]["messages"]] == [MESSAGE]
        assert FAKE_KEY_VALUE not in json.dumps(call["body"])
    names = sorted(p.name for p in sink.directory.iterdir())
    assert len(names) == 2, ("%s: evidence files %r" % (phase, names))
    evidence_request_id = names[0].split(".request.json")[0]
    report = sink.verify_pair(evidence_request_id)
    assert report["pair_ok"], ("%s: %r" % (phase, report))
    request_doc = json.loads((sink.directory / names[0]).read_text(encoding="utf-8"))
    response_doc = json.loads((sink.directory / names[1]).read_text(encoding="utf-8"))
    assert request_doc["payload_sha256"] == sa_evidence.sha256_hex(
        sa_evidence.canonical_bytes(call["body"])
    )
    assert response_doc["raw_sha256"] == sa_evidence.sha256_hex(_canned_body_bytes())
    assert response_doc["usage"] == CANNED_USAGE
    print(
        "%s GREEN: transport_calls=%d pair_ok=True usage=%s raw_prefix=%s"
        % (
            phase,
            expect_calls,
            json.dumps(response_doc["usage"], sort_keys=True),
            response_doc["raw_sha256"][:16],
        )
    )


def _no_external_asserts(sink, *, phase):
    """Zero external entries; secret never landed in files.

    ``connect`` records are allowed ONLY on loopback: on Windows the
    ProactorEventLoop builds its internal self-pipe with a loopback
    connect at creation (empirically ``127.0.0.1`` + ephemeral port, one
    per fresh loop). Socket CONSTRUCTIONS are recorded and printed for
    observability only (the loop self-pipe is built through the
    Python-level ``socket.socket`` attribute on this build; construction
    alone cannot reach any peer). Anything non-loopback, or any DNS
    lookup, fails this gate.
    """
    assert _TRANSPORT.attempts == [], ("%s: external attempts %r" % (phase, _TRANSPORT.attempts))
    bad = [c for c in _TRANSPORT.connects if not _is_loopback(c)]
    assert bad == [], ("%s: non-loopback connects %r" % (phase, bad))
    print("%s loopback self-pipe connects: %r" % (phase, list(_TRANSPORT.connects)))
    print("%s sockets constructed at Python level: %r" % (phase, _TRANSPORT.sockets))
    for path in sorted(sink.directory.rglob("*")):
        if path.is_file():
            content = path.read_text(encoding="utf-8", errors="replace")
            assert FAKE_KEY_VALUE not in content, path
    for name in sorted(p.name for p in sink.directory.iterdir()):
        content = (sink.directory / name).read_text(encoding="utf-8")
        assert "Bearer" not in content and "api_key" not in content, name
        assert KEY_REF_NAME in content, name


# -- acceptance sequence: RED -> GREEN -> RED -> GREEN ------------------------


def test_sa_sender_loop_fix_red_green_red_green(tmp_path):
    fixed = _load_fixed()

    # Phase 1: OLD path RED (pre-fix pump, NoEventLoopError).
    sink1 = sa_evidence.EvidenceSink(tmp_path / "phase1", key_ref=KEY_REF_NAME)
    dialog1 = _make_factory(_old_submit, sink1)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei1:
        dialog1((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _red_asserts(ei1.value, sink1, phase="phase1")
    assert _TRANSPORT.calls == []  # died in the pool, wrapper never reached

    # Phase 2: FIXED path GREEN (shipped _run/_submit, real loop + mock).
    sink2 = sa_evidence.EvidenceSink(tmp_path / "phase2", key_ref=KEY_REF_NAME)
    dialog2 = _make_factory(fixed._submit, sink2)(_snapshot())
    text = dialog2((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _green_asserts(text, sink2, phase="phase2")
    _no_external_asserts(sink2, phase="phase2")

    # Phase 3: restore OLD path -> RED again (fix changed nothing global).
    sink3 = sa_evidence.EvidenceSink(tmp_path / "phase3", key_ref=KEY_REF_NAME)
    dialog3 = _make_factory(_old_submit, sink3)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei3:
        dialog3((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _red_asserts(ei3.value, sink3, phase="phase3")
    assert len(_TRANSPORT.calls) == 1  # old phases never reach the transport

    # Phase 4: re-apply FIXED path -> GREEN (the retained worktree version).
    sink4 = sa_evidence.EvidenceSink(tmp_path / "phase4", key_ref=KEY_REF_NAME)
    dialog4 = _make_factory(fixed._submit, sink4)(_snapshot())
    text4 = dialog4((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _green_asserts(text4, sink4, phase="phase4", expect_calls=2)
    _no_external_asserts(sink4, phase="phase4")

    # Whole run: zero external entries, zero real I/O.
    assert _TRANSPORT.attempts == []
    assert all(_is_loopback(c) for c in _TRANSPORT.connects)


# -- isolated-copy helper for source-level mutations (never worktree) --------


def _mutated_namespace(old_run_replacement=None, leak_hook=None):
    """Exec the probe source in an isolated namespace with one splice."""
    src = _PROBE_PATH.read_text(encoding="utf-8")
    assert src.count("def _run(coro):") == 1, "probe _run anchor drift"
    assert src.count("return asyncio.run(coro)") == 1, "probe asyncio.run anchor drift"
    assert OLD_PUMP_TEXT.strip().splitlines()[0] == "def _run(coro):"
    if old_run_replacement is not None:
        start = src.index("def _run(coro):")
        end = src.index("return asyncio.run(coro)") + len("return asyncio.run(coro)")
        src = src[:start] + old_run_replacement.rstrip("\n") + "\n" + src[end:]
    if leak_hook is not None:
        src = src.replace("return asyncio.run(coro)", leak_hook)
    ns = {
        "__name__": "live_fire_b4_real_mutant",
        "__file__": str(_PROBE_PATH),
        "_LEAKED_LOOPS": [],
    }
    before_env = dict(os.environ)
    before_mods = set(sys.modules)
    try:
        exec(compile(src, str(_PROBE_PATH), "exec"), ns)  # noqa: S102 (isolated test copy)
    finally:
        _clean_import(before_env, before_mods)
    return ns


# -- M1: true loop back to hand pump must turn RED, then GREEN ----------------


def test_sa_sender_loop_fix_m1_hand_pump_turns_red(tmp_path):
    ns = _mutated_namespace(old_run_replacement=OLD_PUMP_TEXT)
    sink = sa_evidence.EvidenceSink(tmp_path / "m1", key_ref=KEY_REF_NAME)
    dialog = _make_factory(ns["_submit"], sink)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei:
        dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _red_asserts(ei.value, sink, phase="m1-mutant")
    assert _TRANSPORT.calls == []

    fixed = _load_fixed()
    sink_g = sa_evidence.EvidenceSink(tmp_path / "m1-green", key_ref=KEY_REF_NAME)
    dialog_g = _make_factory(fixed._submit, sink_g)(_snapshot())
    text = dialog_g((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _green_asserts(text, sink_g, phase="m1-fixed", expect_calls=1)
    _no_external_asserts(sink_g, phase="m1-fixed")


# -- M2: nested run inside a running loop must refuse, then GREEN -------------


def test_sa_sender_loop_fix_m2_nested_run_refuses(tmp_path):
    fixed = _load_fixed()
    sink = sa_evidence.EvidenceSink(tmp_path / "m2", key_ref=KEY_REF_NAME)
    dialog = _make_factory(fixed._submit, sink)(_snapshot())

    async def _nested_main():
        return dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)

    with pytest.raises(sa_bridge.LiveRefused) as ei:
        asyncio.run(_nested_main())
    assert isinstance(ei.value.__cause__, RuntimeError), (
        "m2-mutant: cause is %r, want nested-loop RuntimeError" % (ei.value.__cause__,)
    )
    assert "nesting" in str(ei.value.__cause__)
    assert not isinstance(ei.value.__cause__, NoEventLoopError)
    print("m2-mutant RED: nested run refused (%s)" % type(ei.value.__cause__).__name__)

    sink_g = sa_evidence.EvidenceSink(tmp_path / "m2-green", key_ref=KEY_REF_NAME)
    dialog_g = _make_factory(fixed._submit, sink_g)(_snapshot())
    text = dialog_g((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _green_asserts(text, sink_g, phase="m2-fixed", expect_calls=1)
    _no_external_asserts(sink_g, phase="m2-fixed")


# -- M3: cached loop reused across calls (after close) must fail, then GREEN --


def test_sa_sender_loop_fix_m3_cached_loop_fails(tmp_path):
    shared = {}

    def _cached_submit(loop, coro):
        if asyncio.iscoroutine(coro):
            holder = shared.get("loop")
            if holder is None:
                holder = asyncio.new_event_loop()
                shared["loop"] = holder
            try:
                return _ImmediateFuture(holder.run_until_complete(coro))
            except BaseException:
                # Mirror the shipped guard: never leave a failed coroutine
                # un-awaited (keeps the red signal a clean LiveRefused).
                try:
                    coro.close()
                except Exception:  # noqa: BLE001
                    pass
                raise
        return _ImmediateFuture(coro)

    # First use on the cached loop: functionally fine (one walk).
    sink1 = sa_evidence.EvidenceSink(tmp_path / "m3-first", key_ref=KEY_REF_NAME)
    dialog1 = _make_factory(_cached_submit, sink1)(_snapshot())
    text1 = dialog1((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert text1 == CANNED_TEXT
    shared["loop"].close()  # lifecycle end: cached loop (and its client state) dies.

    # Second use reuses the dead loop -> must refuse (no cross-call caching).
    sink2 = sa_evidence.EvidenceSink(tmp_path / "m3-second", key_ref=KEY_REF_NAME)
    dialog2 = _make_factory(_cached_submit, sink2)(_snapshot())
    with pytest.raises(sa_bridge.LiveRefused) as ei:
        dialog2((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert isinstance(ei.value.__cause__, RuntimeError), (
        "m3-mutant: cause is %r, want closed-loop RuntimeError" % (ei.value.__cause__,)
    )
    assert "closed" in str(ei.value.__cause__).lower()
    print("m3-mutant RED: cached-loop reuse refused (%s)" % (ei.value.__cause__,))

    fixed = _load_fixed()
    sink_g = sa_evidence.EvidenceSink(tmp_path / "m3-green", key_ref=KEY_REF_NAME)
    dialog_g = _make_factory(fixed._submit, sink_g)(_snapshot())
    text = dialog_g((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    _green_asserts(text, sink_g, phase="m3-fixed", expect_calls=2)
    _no_external_asserts(sink_g, phase="m3-fixed")


# -- M4: skipped close must leak the loop (RED), then GREEN --------------------


def test_sa_sender_loop_fix_m4_skipped_close_leaks(tmp_path):
    ns = _mutated_namespace(
        leak_hook=(
            "_leak = asyncio.new_event_loop()\n"
            "    _LEAKED_LOOPS.append(_leak)\n"
            "    return _leak.run_until_complete(coro)"
        )
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "m4", key_ref=KEY_REF_NAME)
    dialog = _make_factory(ns["_submit"], sink)(_snapshot())
    text = dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert text == CANNED_TEXT  # functionally fine ...
    assert len(ns["_LEAKED_LOOPS"]) == 1
    assert ns["_LEAKED_LOOPS"][0].is_closed() is False  # ... but the loop leaked: RED.
    try:
        print("m4-mutant RED: loop left open (close skipped)")
    finally:
        ns["_LEAKED_LOOPS"][0].close()

    fixed = _load_fixed()
    sink_g = sa_evidence.EvidenceSink(tmp_path / "m4-green", key_ref=KEY_REF_NAME)
    dialog_g = _make_factory(fixed._submit, sink_g)(_snapshot())
    text_g = dialog_g((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    n_calls = len(_TRANSPORT.calls)
    _green_asserts(text_g, sink_g, phase="m4-fixed", expect_calls=n_calls)
    _no_external_asserts(sink_g, phase="m4-fixed")
