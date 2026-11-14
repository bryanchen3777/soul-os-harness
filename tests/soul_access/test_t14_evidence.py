"""SA-EVIDENCE-CHANNEL-1 T14 original-evidence capability tests (sandbox only).

Decision-complete scope (SA-EVIDENCE-CHANNEL-1): product edits are
``src/llm/proxy.py`` (``OpenAIBackend.complete`` gains optional
``evidence_sink``) and ``src/soul_access/wiring.py`` (dialog factory gains
the same optional pass-through); new module ``src/soul_access/evidence.py``
is the caller-owned sandbox sink. This file is new. Launcher, probe script,
production settings, ``data/**``, ``release/sa.json``, ``registry/presets``,
ports (incl. the Frozen outward-port assertion), bridge/api/schema/release
criteria, T6 403, memory criteria, single-fire cap and SAGE are untouched.

All test names carry the t14 marker: ``pytest -q tests/soul_access -k t14``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected
via ``monkeypatch`` (the module-global fake never leaves the test; no real
request, no server, no port is ever bound). ``asyncio.run`` is only used to
drive the in-process coroutine against that fake -- still zero I/O.

Coverage (ticket acceptance mapping):
  a. success: request payload hash == actual sent-body hash; response raw ==
     bottom-transport verbatim; usage traceable to the same request_id;
  b. missing evidence (missing raw / missing usage) == unqualified, no resend;
  c. timeout is consumed, single attempt, no retry;
  d. landing failure == unqualified, no send (pre) / no resend (post);
  e. concurrency isolation (separate sinks AND one shared sink, per-request
     request_id isolation, payloads never cross);
  f. sink unconfigured == legacy behavior bit-identical (legacy backend
     signature still accepted; same text with/without sink; no files);
  g. dialog factory forwards the sink explicitly per request (real backend
     end-to-end through the factory seam; str-only return shape kept);
  h. sink refuses repo data/** structurally; key is a variable NAME only;
  i. bidirectional mutations: sink bypass / request_id mismatch / hash
     forgery mutants must turn RED, revert GREEN (mutations never touch
     production: execution-time fake transport only, non-routable base URL).
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

NON_ROUTABLE_BASE = "http://t14-invalid.invalid/v1/chat/completions"
SENTINEL_SECRET = "t14-sandbox-secret-7f3a9c2e"
MODEL = "do-not-deploy-model-t14"
AGENT = "agent_rem"


def _transport_body(text, usage):
    """Fake provider JSON document; raw text is the verbatim transport body."""
    return {
        "id": "chatcmpl-t14",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": dict(usage),
    }


def _raw_of(body) -> str:
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


class _FakeResp:
    """httpx.Response stand-in: verbatim text + parsed JSON, no I/O."""

    def __init__(self, raw_text, body):
        self._raw_text = raw_text
        self._body = body
        self.status_code = 200

    @property
    def text(self):
        return self._raw_text

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class _FakeClient:
    """httpx.AsyncClient stand-in: records the ACTUAL sent body, one shot."""

    def __init__(self, behavior, calls, lock):
        self._behavior = behavior
        self._calls = calls
        self._lock = lock

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        with self._lock:
            self._calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        return self._behavior(self._calls[-1])


def _install_fake(monkeypatch, behavior):
    """Route ALL httpx.AsyncClient construction to the fake (execution hard-block)."""
    calls = []
    lock = threading.Lock()

    def _factory(*args, **kwargs):
        return _FakeClient(behavior, calls, lock)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls


def _ok_behavior(text="t14-reply", usage=None):
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4} if usage is None else usage

    def _behavior(call):
        body = _transport_body(text, usage)
        return _FakeResp(_raw_of(body), body)

    return _behavior


def _run(coro):
    """Loop-free coroutine driver: run to completion without an event loop.

    The stock ``asyncio.run`` cannot be used here: on Windows even loop
    creation calls ``socket.socketpair``, which the SA-0 conftest hard-block
    refuses by design. Our fakes never suspend on real I/O (every awaited
    object is an immediately-completing coroutine), so the coroutine can be
    pumped with ``send``/``throw`` directly. Anything that would need a real
    loop (a pending Future, a sleep, real I/O) fails loudly instead of being
    silently emulated.
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
                raise AssertionError(
                    "t14: fake suspended on %r (a real loop would be needed)" % (yielded,)
                )
        except BaseException as exc:
            send_value = None
            throw_exc = exc


def _complete(backend, **over):
    kw = {
        "messages": [{"role": "user", "content": "t14 probe"}],
        "model": MODEL,
        "max_tokens": 16,
        "temperature": 0.5,
        "max_retries": 0,
    }
    kw.update(over)
    return _run(backend.complete(**kw))


def _snapshot(**over):
    kw = {
        "identity": sa_schema.RequestIdentity(
            request_id="req-t14-0001",
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-t14-0001",
            idempotency_key="key-t14-0001",
        ),
        "dedup": sa_schema.DedupKey(
            principal_id="t14-offline-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t14-0001",
        ),
        "backend_alias": "primary",
        "model": MODEL,
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-12T00:00:00Z",
        "personality_version": "personality-t14-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-11T17:00:00-05:00",
        "inner_life_state": "inner-t14-v1",
        "projection_state": "projection-t14-v1",
        "recall_version": "recall-t14-v1",
        "recall_items": ("recall-t14-item-1",),
        "history_pool_version": "pool-t14-v1",
        "clean_history": ("hist-t14-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


def _run_submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _sink_files(sink):
    return sorted(p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-"))


# -- T14-a: success round-trip -----------------------------------------------

def test_t14_evidence_success_roundtrip_hash_raw_usage(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    calls = _install_fake(monkeypatch, _ok_behavior(text="t14-answer"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    text = _complete(backend, evidence_sink=sink)

    assert text == "t14-answer"
    assert len(calls) == 1  # single-fire: exactly one transport attempt
    sent_body = calls[0]["json"]

    names = _sink_files(sink)
    assert len(names) == 2
    request_id = names[0].split(".request.json")[0]
    assert names == ["%s.request.json" % request_id, "%s.response.json" % request_id]

    request = json.loads((sink.directory / names[0]).read_text(encoding="utf-8"))
    response = json.loads((sink.directory / names[1]).read_text(encoding="utf-8"))

    # Pre-send payload == the ACTUAL sent body (replayable), hash matches.
    assert request["payload"] == sent_body
    assert request["payload_sha256"] == sa_evidence.sha256_hex(
        sa_evidence.canonical_bytes(sent_body)
    )
    # Post-return raw == bottom-transport verbatim, usage native, same id.
    assert response["raw_text"] == _raw_of(_transport_body("t14-answer", {
        "prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4,
    }))
    assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    assert response["request_id"] == request_id == request["request_id"]
    assert response["raw_sha256"] == sa_evidence.sha256_hex(
        response["raw_text"].encode("utf-8")
    )
    report = sink.verify_pair(request_id)
    assert report["pair_ok"] is True

    # Return shape unchanged: str only; no raw/usage smuggled into the reply.
    assert isinstance(text, str) and "usage" not in text and "chatcmpl" not in text

    # No credentials stored: variable NAME present, secret value absent.
    for name in names:
        content = (sink.directory / name).read_text(encoding="utf-8")
        assert sa_evidence.KEY_REF in content
        assert SENTINEL_SECRET not in content
    assert "getenv" not in Path(sa_evidence.__file__).read_text(encoding="utf-8")
    assert "environ" not in Path(sa_evidence.__file__).read_text(encoding="utf-8")


# -- T14-b: missing evidence == unqualified ------------------------------------

@pytest.mark.parametrize("variant", ["missing-raw", "missing-usage"])
def test_t14_evidence_missing_field_unqualified_no_resend(tmp_path, monkeypatch, variant):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        if variant == "missing-raw":
            return _FakeResp("", {"id": "x", "choices": [], "usage": {"prompt_tokens": 1}})
        body = {"id": "x", "choices": [{"message": {"content": "t14"}}]}
        return _FakeResp(_raw_of(body), body)

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    with pytest.raises(sa_evidence.EvidenceGap):
        _complete(backend, evidence_sink=sink)

    assert len(calls) == 1  # consumed, never resent
    names = _sink_files(sink)
    assert len(names) == 1 and names[0].endswith(".request.json")
    with pytest.raises(sa_evidence.EvidenceStoreError):
        sink.verify_pair(names[0].split(".request.json")[0])


# -- T14-c: timeout consumed, single attempt ------------------------------------

def test_t14_evidence_timeout_consumed_single_attempt(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.TimeoutException("t14 simulated timeout")

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    with pytest.raises(RuntimeError, match="exhausted 1 attempt"):
        _complete(backend, evidence_sink=sink)

    assert len(calls) == 1  # zero-retry: one attempt, ever
    names = _sink_files(sink)
    assert len(names) == 1 and names[0].endswith(".request.json")


# -- T14-d: landing failure == unqualified ---------------------------------------

def test_t14_evidence_pre_send_landing_failure_no_send(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    calls = _install_fake(monkeypatch, _ok_behavior())
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    monkeypatch.setattr(
        sink, "record_request",
        lambda **kw: (_ for _ in ()).throw(sa_evidence.EvidenceStoreError("t14 mutant disk full")),
    )

    with pytest.raises(sa_evidence.EvidenceStoreError):
        _complete(backend, evidence_sink=sink)

    assert calls == []  # pre-send landing failed: nothing was ever sent


def test_t14_evidence_post_return_landing_failure_no_resend(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    calls = _install_fake(monkeypatch, _ok_behavior())
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    monkeypatch.setattr(
        sink, "record_response",
        lambda **kw: (_ for _ in ()).throw(sa_evidence.EvidenceStoreError("t14 mutant disk full")),
    )

    with pytest.raises(sa_evidence.EvidenceStoreError):
        _complete(backend, evidence_sink=sink)

    assert len(calls) == 1  # post-return landing failed: received once, never resent
    assert _sink_files(sink) != []  # the pre-send record stays; the pair stays broken


# -- T14-e: concurrency isolation -----------------------------------------------

def _dispatch_behavior(call):
    """Shared-fake behavior: echo each thread's own prompt back (no cross-talk)."""
    user_text = call["json"]["messages"][-1]["content"]  # "t14-prompt-%d"
    index = int(user_text.rsplit("-", 1)[-1])
    text = "t14-thread-%d" % index
    usage = {"prompt_tokens": index + 1, "completion_tokens": 1, "total_tokens": index + 2}
    body = _transport_body(text, usage)
    return _FakeResp(_raw_of(body), body)


def _thread_complete(index, out, errors):
    try:
        backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
        result = _run(backend.complete(
            messages=[{"role": "user", "content": "t14-prompt-%d" % index}],
            model=MODEL, max_tokens=16, temperature=0.5,
            max_retries=0, evidence_sink=out[index]["sink"],
        ))
        out[index]["result"] = result
    except Exception as exc:  # noqa: BLE001 -- collected, then re-raised in main thread
        errors[index] = exc


def test_t14_evidence_concurrent_separate_sinks_isolated(tmp_path, monkeypatch):
    calls = _install_fake(monkeypatch, _dispatch_behavior)
    sinks = [sa_evidence.EvidenceSink(tmp_path / ("ev-%d" % i)) for i in range(4)]
    out = [{"sink": sink} for sink in sinks]
    errors = {}
    threads = [
        threading.Thread(target=_thread_complete, args=(i, out, errors)) for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == {}
    assert len(calls) == 4

    seen_ids = set()
    for i in range(4):
        assert out[i]["result"] == "t14-thread-%d" % i
        names = _sink_files(sinks[i])
        assert len(names) == 2
        request_id = names[0].split(".request.json")[0]
        assert request_id not in seen_ids
        seen_ids.add(request_id)
        stored = json.loads((sinks[i].directory / names[0]).read_text(encoding="utf-8"))
        assert stored["payload"]["messages"] == [{"role": "user", "content": "t14-prompt-%d" % i}]
        assert sinks[i].verify_pair(request_id)["pair_ok"] is True
    assert len(seen_ids) == 4


def test_t14_evidence_concurrent_shared_sink_request_ids_isolated(tmp_path, monkeypatch):
    calls = _install_fake(monkeypatch, _dispatch_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "shared")
    out = [{"sink": sink} for _ in range(4)]
    errors = {}
    threads = [
        threading.Thread(target=_thread_complete, args=(i, out, errors)) for i in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == {}
    assert len(calls) == 4

    for i in range(4):
        assert out[i]["result"] == "t14-thread-%d" % i
    names = _sink_files(sink)
    assert len(names) == 8
    ids = sorted(n.split(".request.json")[0] for n in names if n.endswith(".request.json"))
    assert len(set(ids)) == 4
    for request_id in ids:
        assert sink.verify_pair(request_id)["pair_ok"] is True


# -- T14-f: sink unconfigured == legacy behavior ----------------------------------

class _LegacyBackend:
    """T9-style fixed-signature backend: no evidence_sink parameter, ever."""

    def __init__(self, text="t14-legacy-reply"):
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
        return self.text


def test_t14_no_sink_legacy_backend_signature_still_accepted():
    llm = SimpleNamespace(backend=_LegacyBackend())
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_run_submit)
    dialog = factory(_snapshot())
    assert dialog((("user", "t14 legacy"),), agent_id=AGENT, request_id="req-t14-legacy") == "t14-legacy-reply"
    assert "evidence_sink" not in llm.backend.calls[0]
    assert llm.backend.calls[0]["max_retries"] == 0


def test_t14_no_sink_text_identical_no_files(tmp_path, monkeypatch):
    with_sink_calls = _install_fake(monkeypatch, _ok_behavior(text="t14-same"))
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    with_sink = _complete(backend, evidence_sink=sink)

    before = {path.name for path in tmp_path.iterdir()}
    without_sink = _complete(backend)
    after = {path.name for path in tmp_path.iterdir()}

    assert with_sink == without_sink == "t14-same"
    assert before == after  # unconfigured sink: zero evidence files anywhere new
    assert len(with_sink_calls) == 2


# -- T14-g: factory forwards the sink per request ---------------------------------

def test_t14_dialog_factory_forwards_sink_per_request(tmp_path, monkeypatch):
    _install_fake(monkeypatch, _ok_behavior(text="t14-dialog-reply"))
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
    )

    first = factory(_snapshot())
    assert first((("user", "one"),), agent_id=AGENT, request_id="req-t14-a") == "t14-dialog-reply"
    second = factory(_snapshot())
    assert second((("user", "two"),), agent_id=AGENT, request_id="req-t14-b") == "t14-dialog-reply"

    names = _sink_files(sink)
    assert len(names) == 4
    ids = sorted(n.split(".request.json")[0] for n in names if n.endswith(".request.json"))
    assert len(set(ids)) == 2  # one request_id per dialog call, never shared
    for request_id in ids:
        report = sink.verify_pair(request_id)
        assert report["pair_ok"] is True
        stored = json.loads(
            (sink.directory / ("%s.request.json" % request_id)).read_text(encoding="utf-8")
        )
        assert stored["payload"]["model"] == MODEL


def test_t14_dialog_factory_no_sink_stays_str_only(tmp_path, monkeypatch):
    _install_fake(monkeypatch, _ok_behavior(text="t14-plain"))
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_run_submit)
    dialog = factory(_snapshot())
    assert dialog((("user", "hi"),), agent_id=AGENT, request_id="req-t14-plain") == "t14-plain"


# -- T14-h: structural guards ------------------------------------------------------

def test_t14_sink_refuses_prod_data_tree():
    prod_data = _REPO_ROOT / "data"
    with pytest.raises(sa_evidence.EvidenceStoreError):
        sa_evidence.EvidenceSink(prod_data / "t14-must-refuse")
    with pytest.raises(sa_evidence.EvidenceStoreError):
        sa_evidence.EvidenceSink(prod_data)


def test_t14_sink_rejects_bad_request_ids(tmp_path):
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    for bad in ("", "../escape", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            sink.record_request(
                request_id=bad, payload={"m": "x"},
                payload_sha256="0" * 64,
            )


# -- T14-i: bidirectional mutations (RED then GREEN) ---------------------------------
# Mutations never touch production: execution-time in-process fakes only, the
# backend base URL is non-routable (.invalid), zero real requests exist.

def test_t14_mutation_sink_bypassed_leaves_no_evidence_red(tmp_path, monkeypatch):
    """Relaxed mutant: sink writes bypassed -> success without evidence (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-mutant-bypass"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    monkeypatch.setattr(sa_evidence.EvidenceSink, "record_request", lambda self, **kw: None)  # MUTANT
    monkeypatch.setattr(sa_evidence.EvidenceSink, "record_response", lambda self, **kw: None)  # MUTANT

    text = _complete(backend, evidence_sink=sink)
    if _sink_files(sink) != [] or text != "t14-mutant-bypass":
        pytest.fail("MUTATION DUD -- STOP, report: bypassed sink still landed evidence")
    # RED confirmed: the reply came back but nothing was captured.


def test_t14_mutation_sink_bypassed_reverted_green(tmp_path, monkeypatch):
    """Revert: real sink captures the pair (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-bypass-green"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    assert _complete(backend, evidence_sink=sink) == "t14-bypass-green"
    names = _sink_files(sink)
    assert len(names) == 2
    assert sink.verify_pair(names[0].split(".request.json")[0])["pair_ok"] is True


def test_t14_mutation_request_id_mismatched_breaks_pairing_red(tmp_path, monkeypatch):
    """Corrupt mutant: response filed under a different id -> pair broken (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-mutant-mismatch"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    real_record = sa_evidence.EvidenceSink.record_response

    def _mutant_record(self, **kw):  # MUTANT: file the response under a forged id
        kw = dict(kw)
        kw["request_id"] = kw["request_id"] + "-forged"
        return real_record(self, **kw)

    monkeypatch.setattr(sa_evidence.EvidenceSink, "record_response", _mutant_record)  # MUTANT

    assert _complete(backend, evidence_sink=sink) == "t14-mutant-mismatch"
    names = _sink_files(sink)
    request_ids = sorted(n.split(".request.json")[0] for n in names if n.endswith(".request.json"))
    assert len(request_ids) == 1
    try:
        sink.verify_pair(request_ids[0])
    except sa_evidence.EvidenceStoreError:
        pass  # RED confirmed: the pair no longer correlates.
    else:
        pytest.fail("MUTATION DUD -- STOP, report: mismatched ids still verified")


def test_t14_mutation_request_id_mismatched_reverted_green(tmp_path, monkeypatch):
    """Revert: matching ids verify (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-mismatch-green"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    assert _complete(backend, evidence_sink=sink) == "t14-mismatch-green"
    names = _sink_files(sink)
    request_id = names[0].split(".request.json")[0]
    assert sink.verify_pair(request_id)["pair_ok"] is True


def test_t14_mutation_hash_forged_detected_red(tmp_path, monkeypatch):
    """Corrupt mutant: stored payload hash forged -> recompute trips (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-mutant-forge"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    real_record = sa_evidence.EvidenceSink.record_request

    def _mutant_record(self, **kw):  # MUTANT: store a forged digest
        kw = dict(kw)
        kw["payload_sha256"] = "0" * 64
        return real_record(self, **kw)

    monkeypatch.setattr(sa_evidence.EvidenceSink, "record_request", _mutant_record)  # MUTANT

    assert _complete(backend, evidence_sink=sink) == "t14-mutant-forge"
    names = _sink_files(sink)
    request_id = names[0].split(".request.json")[0]
    report = sink.verify_pair(request_id)
    if report["pair_ok"]:
        pytest.fail("MUTATION DUD -- STOP, report: forged hash still verified")
    assert report["payload_sha256_ok"] is False  # RED confirmed: forgery detected.


def test_t14_mutation_hash_forged_reverted_green(tmp_path, monkeypatch):
    """Revert: honest digest verifies (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    _install_fake(monkeypatch, _ok_behavior(text="t14-forge-green"))
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    assert _complete(backend, evidence_sink=sink) == "t14-forge-green"
    names = _sink_files(sink)
    request_id = names[0].split(".request.json")[0]
    report = sink.verify_pair(request_id)
    assert report["payload_sha256_ok"] is True
    assert report["pair_ok"] is True
