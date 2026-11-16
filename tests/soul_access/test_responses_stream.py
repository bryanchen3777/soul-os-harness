"""SA-RESPONSES-SSE-STREAM-1 streaming acceptance (sandbox only, mock upstream).

Decision-complete scope (Sol approved): product change is confined to
``src/soul_access/responses_backend.py`` (bool(stream) branch: SSE byte
passthrough via ``httpx.Client.stream``) and ``scripts/serve_sa_thin.py``
(``StreamingResponse`` wiring only). Frozen ``src/external_gateway/**`` is
untouched. No server is started, no production port is bound, no real
request leaves the process (``httpx`` is monkeypatched on the backend
module attribute; the only key values here are synthetic sandbox tokens).

Coverage:
  a. stream:true success passes bytes through verbatim and never calls
     ``resp.json()`` (direct forwarder + thin endpoint SSE headers/body);
  b. upstream 400 / 429 on the first packet are intercepted as the existing
     fixed ``InterfaceError`` copies (direct + endpoint);
  c. an ``httpx.ReadTimeout`` on the 2nd chunk ends the generator silently
     (EOF, no raise, no injected frame);
  d. stream:false (or missing ``stream``) keeps the original
     ``client.post`` + ``.json()`` whole-JSON path untouched (2-tuple);
  e. leak guard: logs and errors never carry body / key / upstream text
     (``caplog`` asserted).
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sys
import threading
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.schema import InterfaceError  # noqa: E402
from src.soul_access import responses_backend as resp_mod  # noqa: E402


def _load_thin_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "serve_sa_thin_stream", _REPO_ROOT / "scripts" / "serve_sa_thin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


thin_mod = _load_thin_module()

_RESP_LOOP = asyncio.new_event_loop()


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


_OUTWARD_RUNNER = _Runner(_RESP_LOOP, "resp-stream-outward")


def _run(coro):
    return _OUTWARD_RUNNER.run(coro)


# -- synthetic fixtures (never production identity) ----------------------------

OWNER = "resp-stream-owner"
SCOPE = "owner_private"
TOKEN = "resp-stream-sandbox-token"
TOKEN_DIGEST = digest_of(TOKEN)
PRESET = "muse-spark-1.3-contributor"
MODEL = "muse-spark-1.3-contributor"
BASE_URL = "https://resp-stream.invalid/zen/go/v1"
KEY_REF = "OPENCODE_GO_API_KEY"
FAKE_KEY = "resp-stream-sandbox-fake-key"
SECRET = "sk-stream-secret-abc123"
UPSTREAM_NO = "upstream-says-no-stream-marker"
SOULS = ("rem", "mai", "ruka", "akane", "mahiru")

CHUNK_A = b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
CHUNK_B = b"data: [DONE]\n\n"

NATIVE_UPSTREAM = {"id": "resp-stream-0001", "object": "response", "model": MODEL}


def _preset_entries(provider="openai"):
    return [
        {
            "name": PRESET,
            "provider": provider,
            "model": MODEL,
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": BASE_URL,
            },
            "key_ref": KEY_REF,
        }
    ]


def _registry_raw(token_digest=TOKEN_DIGEST):
    return {
        "owner_principal_id": OWNER,
        "backends": ["resp-stream-backend"],
        "routes": [
            {
                "soul_alias": soul,
                "backend_alias": PRESET,
                "agent_id": "agent_" + soul,
                "public_model_id": "sa-resp-%s" % soul,
                "backend_ref": "resp-stream-backend",
                "context_window_tokens": 200000,
            }
            for soul in SOULS
        ],
        "clients": [
            {
                "client_id": "client-resp-stream",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (soul, PRESET) for soul in SOULS],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _sandbox_files(tmp_path, *, registry=None, provider="openai"):
    registry_path = tmp_path / "registry.json"
    presets_path = tmp_path / "presets.json"
    release_path = tmp_path / "release.json"
    registry_path.write_text(
        json.dumps(registry if registry is not None else _registry_raw()),
        encoding="utf-8",
    )
    presets_path.write_text(
        json.dumps({"presets": _preset_entries(provider)}), encoding="utf-8"
    )
    release_path.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    return (registry_path, presets_path, release_path)


# -- fake httpx layer (records inputs, replays canned upstream) ---------------

class _FakeTimeout:
    def __init__(self, connect=None, read=None, write=None, pool=None):
        self.connect = connect
        self.read = read
        self.write = write
        self.pool = pool


class _FakeTimeoutException(Exception):
    pass


class _FakeReadTimeout(_FakeTimeoutException):
    pass


class _FakeJsonResp:
    """Non-stream whole-JSON reply (records nothing; ``json()`` is legal)."""

    def __init__(self, status_code, payload=None, *, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else b""

    def json(self):
        return self._payload


class _FakeStreamResp:
    """Streaming first-packet reply. ``json()`` must never be called."""

    def __init__(self, status_code, *, chunks=None, text="", fail_at=None,
                 fail_exc=None):
        self.status_code = status_code
        self._chunks = list(chunks or [])
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else b""
        self.json_called = False
        self.read_called = False
        self._fail_at = fail_at
        self._fail_exc = fail_exc

    def read(self):
        self.read_called = True
        return self.content

    def iter_bytes(self):
        for idx, chunk in enumerate(self._chunks):
            if self._fail_at is not None and idx == self._fail_at:
                raise self._fail_exc or _FakeReadTimeout("resp-stream fake timeout")
            yield chunk

    def json(self):
        self.json_called = True
        raise AssertionError("stream path must never call .json()")


class _FakeStreamCM:
    def __init__(self, resp):
        self.resp = resp
        self.closed = False

    def __enter__(self):
        return self.resp

    def __exit__(self, *exc):
        self.closed = True
        return False


class _FakeClient:
    """Stand-in for httpx.Client: records post()/stream() inputs."""

    behaviour = {}
    calls = []
    stream_calls = []
    last_cm = None
    last_resp = None
    closed_count = 0

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self.closed = True
        type(self).closed_count += 1

    def post(self, url, headers=None, json=None):
        if _FakeClient.behaviour.get("raise_post_timeout"):
            raise _FakeTimeoutException("resp-stream fake post timeout")
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        return _FakeJsonResp(
            _FakeClient.behaviour.get("post_status", 200),
            _FakeClient.behaviour.get("post_payload", NATIVE_UPSTREAM),
        )

    def stream(self, method, url, headers=None, json=None):
        type(self).stream_calls.append(
            {"method": method, "url": url, "headers": headers, "json": json}
        )
        behaviour = _FakeClient.behaviour
        if behaviour.get("raise_stream_timeout"):
            raise _FakeTimeoutException("resp-stream fake stream timeout")
        if behaviour.get("raise_stream"):
            raise RuntimeError("resp-stream fake stream setup fault")
        resp = _FakeStreamResp(
            behaviour.get("stream_status", 200),
            chunks=behaviour.get("stream_chunks", [CHUNK_A, CHUNK_B]),
            text=behaviour.get("stream_text", ""),
            fail_at=behaviour.get("fail_at"),
            fail_exc=behaviour.get("fail_exc"),
        )
        cm = _FakeStreamCM(resp)
        type(self).last_cm = cm
        type(self).last_resp = resp
        return cm


class _FakeHttpx:
    Timeout = _FakeTimeout
    TimeoutException = _FakeTimeoutException
    ReadTimeout = _FakeReadTimeout
    Client = _FakeClient


@pytest.fixture()
def _fake_upstream(monkeypatch):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    monkeypatch.setattr(resp_mod, "httpx", _FakeHttpx, raising=True)
    _FakeClient.calls = []
    _FakeClient.stream_calls = []
    _FakeClient.last_cm = None
    _FakeClient.last_resp = None
    _FakeClient.closed_count = 0
    _FakeClient.behaviour = {
        "post_status": 200,
        "post_payload": NATIVE_UPSTREAM,
        "stream_status": 200,
        "stream_chunks": [CHUNK_A, CHUNK_B],
        "stream_text": "",
    }
    return _FakeClient


def _build_app(registry_path, presets_path, release_path):
    return thin_mod.build_thin_app(
        str(registry_path), str(presets_path), release_path=str(release_path)
    )


def _post_responses(app, soul, token, body):
    async def _go():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        return await client.post(
            "/%s/%s/v1/responses" % (soul, PRESET),
            headers={"Authorization": "Bearer " + token} if token else {},
            json=body,
        )

    return _run(_go())


def _stream_body(n=1):
    return {"model": MODEL, "input": "resp-stream probe %d" % n, "stream": True}


# -- a. stream:true success: bytes verbatim, .json() never called --------------

def test_stream_success_passthrough_bytes_and_no_json(_fake_upstream):
    body = _stream_body()
    result = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert len(result) == 3
    status, gen, is_stream = result
    assert status == 200
    assert is_stream is True
    assert b"".join(gen) == CHUNK_A + CHUNK_B
    # The stream path never decodes JSON.
    assert _FakeClient.last_resp.json_called is False
    # One upstream call, carrying the body verbatim (stream flag included).
    assert len(_FakeClient.stream_calls) == 1
    assert _FakeClient.calls == []
    outgoing = _FakeClient.stream_calls[0]
    assert outgoing["method"] == "POST"
    assert outgoing["url"] == BASE_URL + "/responses"
    assert outgoing["headers"]["Authorization"] == "Bearer " + FAKE_KEY
    assert outgoing["json"] == body
    assert outgoing["json"]["stream"] is True
    assert FAKE_KEY not in outgoing["url"]


def test_stream_endpoint_returns_sse_verbatim(_fake_upstream, tmp_path):
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "rem", TOKEN, _stream_body())
    assert response.status_code == 200
    content_type = response.headers.get("content-type", "")
    assert "text/event-stream" in content_type
    assert response.headers.get("cache-control") == "no-cache"
    assert response.headers.get("connection") == "keep-alive"
    assert response.headers.get("x-accel-buffering") == "no"
    assert response.content == CHUNK_A + CHUNK_B
    assert _FakeClient.last_resp.json_called is False


# -- b. first-packet 400 / 429 intercepted as the fixed InterfaceError ---------

@pytest.mark.parametrize(
    "upstream_status,expect_status,expect_code",
    [(400, 400, "upstream_bad_request"), (429, 429, "rate_limit_error")],
)
def test_stream_first_packet_error_mapping(
    _fake_upstream, upstream_status, expect_status, expect_code
):
    _FakeClient.behaviour.update(
        {"stream_status": upstream_status, "stream_text": UPSTREAM_NO}
    )
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod.forward_responses_request(_stream_body(), BASE_URL, FAKE_KEY)
    exc = exc_info.value
    assert exc.status == expect_status
    assert exc.code == expect_code
    assert UPSTREAM_NO not in (exc.message or "")
    assert FAKE_KEY not in (exc.message or "")


@pytest.mark.parametrize(
    "upstream_status,expect_status",
    [(400, 400), (429, 429)],
)
def test_stream_endpoint_error_mapping_fixed_copy(
    _fake_upstream, tmp_path, upstream_status, expect_status
):
    _FakeClient.behaviour.update(
        {"stream_status": upstream_status, "stream_text": UPSTREAM_NO}
    )
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "ruka", TOKEN, _stream_body())
    assert response.status_code == expect_status
    assert UPSTREAM_NO not in response.text
    assert FAKE_KEY not in response.text


# -- c. mid-stream ReadTimeout on chunk 2: silent EOF, no raise ----------------

def test_stream_mid_timeout_exits_silently(_fake_upstream):
    _FakeClient.behaviour.update(
        {
            "stream_status": 200,
            "stream_chunks": [CHUNK_A, CHUNK_B],
            "fail_at": 1,
            "fail_exc": _FakeReadTimeout("resp-stream fake read timeout"),
        }
    )
    _, gen, is_stream = resp_mod.forward_responses_request(
        _stream_body(), BASE_URL, FAKE_KEY
    )
    assert is_stream is True
    collected = []
    for chunk in gen:  # must not raise
        collected.append(chunk)
    # First chunk delivered, failure swallowed as EOF (no injected frame).
    assert collected == [CHUNK_A]


# -- d. stream:false keeps the original whole-JSON path ------------------------

def test_non_stream_path_unchanged(_fake_upstream):
    body = {"model": MODEL, "input": "resp-stream plain", "stream": False}
    result = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert len(result) == 2
    status, data = result
    assert status == 200
    assert data == NATIVE_UPSTREAM
    assert len(_FakeClient.calls) == 1
    assert _FakeClient.stream_calls == []


def test_missing_stream_key_stays_non_stream(_fake_upstream):
    body = {"model": MODEL, "input": "resp-stream no flag"}
    status, data = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert data == NATIVE_UPSTREAM
    assert _FakeClient.stream_calls == []


# -- e. leak guard: logs and errors carry no body / key / upstream text --------

def test_stream_leak_guard_logs_and_errors(_fake_upstream, tmp_path, caplog):
    evil_text = "boom " + UPSTREAM_NO + " key=" + SECRET
    _FakeClient.behaviour.update({"stream_status": 400, "stream_text": evil_text})
    with caplog.at_level(logging.INFO):
        with pytest.raises(InterfaceError) as exc_info:
            resp_mod.forward_responses_request(_stream_body(), BASE_URL, FAKE_KEY)
    exc = exc_info.value
    assert SECRET not in (exc.message or "")
    assert UPSTREAM_NO not in (exc.message or "")
    assert FAKE_KEY not in (exc.message or "")
    for record in caplog.records:
        rendered = record.getMessage()
        assert SECRET not in rendered
        assert UPSTREAM_NO not in rendered
        assert FAKE_KEY not in rendered
        assert SECRET not in str(record.args)
        assert FAKE_KEY not in str(record.args)

    caplog.clear()
    _FakeClient.behaviour.update(
        {"stream_status": 200, "stream_chunks": [CHUNK_A, CHUNK_B], "stream_text": ""}
    )
    with caplog.at_level(logging.INFO):
        _, gen, _ = resp_mod.forward_responses_request(
            _stream_body(), BASE_URL, FAKE_KEY
        )
        assert b"".join(gen) == CHUNK_A + CHUNK_B
    for record in caplog.records:
        rendered = record.getMessage()
        assert FAKE_KEY not in rendered
        assert SECRET not in rendered
        assert SECRET not in str(record.args)
        assert FAKE_KEY not in str(record.args)
