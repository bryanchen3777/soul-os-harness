"""SA-RESPONSES-NATIVE-1 native /responses acceptance (sandbox only).

Decision-complete scope (Sol approved): product change is confined to
``src/soul_access/responses_backend.py`` (new native forwarder),
``scripts/serve_sa_thin.py`` (route mount + dual pin), and
``configs/sa/*`` (preset + 5 routes + 5 allowlist entries). Frozen
``src/external_gateway/**`` is untouched. No server is started, no
production port is bound, no real request leaves the process.

Transport is memory-only: the thin app is driven through
``httpx.ASGITransport`` (in-process, never a port). Upstream is a fake
httpx layer (``monkeypatch`` on the backend module attribute), so the
real ``forward_responses_request`` code path runs end to end without
any network. The only key values here are synthetic sandbox tokens;
production digests are never read. Storage is sandbox-only: tmp
registry / presets / release copies. Sync tests only.

Coverage:
  a. 5 souls x native preset透传 200, upstream JSON returned verbatim,
     outgoing URL / headers / payload asserted verbatim (no translation);
  b. missing / wrong credential refuses 401 with zero upstream calls;
  c. valid token but unlisted route refuses 403 with zero upstream calls;
  d. upstream 400 -> 400, 429 -> 429 (fixed copies, upstream text dropped);
  e. upstream 500 / timeout / non-JSON-200 -> 500, zero key leakage;
  f. non-openai provider (same preset name) -> 404;
  g. load_opencode_go_key: missing env -> 500, present -> value, fixed copy.
"""

from __future__ import annotations

import asyncio
import json
import os
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
        "serve_sa_thin_resp", _REPO_ROOT / "scripts" / "serve_sa_thin.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


thin_mod = _load_thin_module()

# -- module-level loop (import time; same idiom as the T12 file) -------------

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


_OUTWARD_RUNNER = _Runner(_RESP_LOOP, "resp-native-outward")


def _run(coro):
    return _OUTWARD_RUNNER.run(coro)


# -- synthetic fixtures (never production identity) ---------------------------

OWNER = "resp-native-owner"
SCOPE = "owner_private"
TOKEN = "resp-native-sandbox-token"
WRONG_TOKEN = "resp-native-wrong-token"
LIMITED_TOKEN = "resp-native-limited-token"
TOKEN_DIGEST = digest_of(TOKEN)
WRONG_DIGEST = digest_of(WRONG_TOKEN)
LIMITED_DIGEST = digest_of(LIMITED_TOKEN)
PRESET = "muse-spark-1.3-contributor"
MODEL = "muse-spark-1.3-contributor"
BASE_URL = "https://resp-native.invalid/zen/go/v1"
KEY_REF = "OPENCODE_GO_API_KEY"
FAKE_KEY = "resp-native-sandbox-fake-key"
SOULS = ("rem", "mai", "ruka", "akane", "mahiru")
SENTINEL = "resp-native-sentinel-key-value"

NATIVE_UPSTREAM = {
    "id": "resp-native-0001",
    "object": "response",
    "created_at": 1763000000,
    "model": MODEL,
    "output": [
        {
            "type": "message",
            "id": "msg-1",
            "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": "hi-from-native-mock", "annotations": []}],
        }
    ],
    "usage": {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8},
}


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
        "backends": ["resp-native-backend"],
        "routes": [
            {
                "soul_alias": soul,
                "backend_alias": PRESET,
                "agent_id": "agent_" + soul,
                "public_model_id": "sa-resp-%s" % soul,
                "backend_ref": "resp-native-backend",
                "context_window_tokens": 200000,
            }
            for soul in SOULS
        ],
        "clients": [
            {
                "client_id": "client-resp-native",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (soul, PRESET) for soul in SOULS],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _limited_registry_raw():
    raw = _registry_raw(token_digest=LIMITED_DIGEST)
    raw["clients"][0]["allowed_routes"] = ["rem/" + PRESET]
    return raw


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


# -- fake httpx layer (records inputs, replays canned upstream) --------------


class _FakeTimeout:
    def __init__(self, connect=None, read=None, write=None, pool=None):
        self.connect = connect
        self.read = read
        self.write = write
        self.pool = pool


class _FakeTimeoutException(Exception):
    pass


class _FakeResp:
    def __init__(self, status_code, payload=None, *, json_raises=False):
        self.status_code = status_code
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("resp-native fake non-JSON body")
        return self._payload


class _FakeClient:
    """Stand-in for httpx.Client: records post() inputs, replays one reply."""

    behaviour = {"status": 200, "payload": NATIVE_UPSTREAM}
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        if _FakeClient.behaviour.get("raise_timeout"):
            raise _FakeTimeoutException("resp-native fake timeout")
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(
            _FakeClient.behaviour["status"],
            _FakeClient.behaviour.get("payload"),
            json_raises=_FakeClient.behaviour.get("json_raises", False),
        )


class _FakeHttpx:
    Timeout = _FakeTimeout
    TimeoutException = _FakeTimeoutException
    Client = _FakeClient


@pytest.fixture()
def _fake_upstream(monkeypatch):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    monkeypatch.setattr(resp_mod, "httpx", _FakeHttpx, raising=True)
    _FakeClient.calls = []
    _FakeClient.behaviour = {"status": 200, "payload": NATIVE_UPSTREAM}
    return _FakeClient


def _build_app(registry_path, presets_path, release_path):
    return thin_mod.build_thin_app(
        str(registry_path), str(presets_path), release_path=str(release_path)
    )


def _post_responses(app, soul, token, body):
    async def _go():
        headers = {}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        return await client.post(
            "/%s/%s/v1/responses" % (soul, PRESET),
            headers=headers,
            json=body,
        )

    return _run(_go())


def _native_body(n=1):
    return {"model": MODEL, "input": "resp-native probe %d" % n, "stream": False}


# -- a. five souls native passthrough -----------------------------------------

import pytest as _pytest  # noqa: E402  (kept beside unittest import idiom)


@_pytest.mark.parametrize("soul", SOULS)
def test_resp_native_passthrough_verbatim_all_souls(
    _fake_upstream, tmp_path, soul
):
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    sent = _native_body()
    response = _post_responses(app, soul, TOKEN, sent)
    assert response.status_code == 200
    # Upstream JSON returned verbatim (native structure, no translation).
    assert response.json() == NATIVE_UPSTREAM
    # Exactly one upstream call, carrying the body verbatim.
    assert len(_FakeClient.calls) == 1
    outgoing = _FakeClient.calls[0]
    assert outgoing["url"] == BASE_URL + "/responses"
    assert outgoing["headers"]["Authorization"] == "Bearer " + FAKE_KEY
    assert outgoing["headers"]["Content-Type"] == "application/json"
    assert outgoing["json"] == sent
    # Zero key leakage on the wire outside the header and in the reply.
    assert FAKE_KEY not in outgoing["url"]
    assert FAKE_KEY not in response.text


# -- b. credential failures ----------------------------------------------------

def test_resp_native_missing_credential_refused_401_zero_upstream(
    _fake_upstream, tmp_path
):
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "rem", None, _native_body())
    assert response.status_code == 401
    assert _FakeClient.calls == []


def test_resp_native_wrong_credential_refused_401_zero_upstream(
    _fake_upstream, tmp_path
):
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "rem", WRONG_TOKEN, _native_body())
    assert response.status_code == 401
    assert _FakeClient.calls == []


# -- c. authorization failure --------------------------------------------------

def test_resp_native_unlisted_route_refused_403_zero_upstream(
    _fake_upstream, tmp_path, monkeypatch
):
    monkeypatch.setenv(KEY_REF, SENTINEL)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, registry=_limited_registry_raw()
    )
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "mai", LIMITED_TOKEN, _native_body())
    assert response.status_code == 403
    assert _FakeClient.calls == []
    assert SENTINEL not in response.text


# -- d. upstream error mapping -------------------------------------------------

_UPSTREAM_CASES = [
    (400, 400, "invalid_request_error"),
    (429, 429, "rate_limit_error"),
]


@_pytest.mark.parametrize(
    "upstream_status,expect_status,expect_code", _UPSTREAM_CASES
)
def test_resp_native_upstream_error_mapping(
    _fake_upstream, tmp_path, upstream_status, expect_status, expect_code
):
    _FakeClient.behaviour = {
        "status": upstream_status,
        "payload": {"error": {"message": "upstream-says-no", "type": "upstream"}},
    }
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "ruka", TOKEN, _native_body())
    assert response.status_code == expect_status
    # Fixed copy: upstream text is dropped, never relayed.
    assert "upstream-says-no" not in response.text
    assert response.json()["error"]["type"] == expect_code
    assert FAKE_KEY not in response.text


# -- e. fail-closed: 500 / timeout / non-JSON ----------------------------------

def test_resp_native_upstream_500_maps_500(_fake_upstream, tmp_path):
    _FakeClient.behaviour = {"status": 500, "payload": {"oops": True}}
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "akane", TOKEN, _native_body())
    assert response.status_code == 500
    assert FAKE_KEY not in response.text


def test_resp_native_upstream_timeout_maps_500(_fake_upstream, tmp_path):
    _FakeClient.behaviour = {"raise_timeout": True}
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "mahiru", TOKEN, _native_body())
    assert response.status_code == 500
    assert FAKE_KEY not in response.text


def test_resp_native_upstream_non_json_200_maps_500(_fake_upstream, tmp_path):
    _FakeClient.behaviour = {"status": 200, "payload": None, "json_raises": True}
    registry_path, presets_path, release_path = _sandbox_files(tmp_path)
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "rem", TOKEN, _native_body(n=7))
    assert response.status_code == 500
    assert FAKE_KEY not in response.text


# -- f. non-openai provider refuses 404 ----------------------------------------

def test_resp_native_non_openai_preset_refuses_404_zero_upstream(
    _fake_upstream, tmp_path
):
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, provider="ollama"
    )
    app = _build_app(registry_path, presets_path, release_path)
    response = _post_responses(app, "rem", TOKEN, _native_body())
    assert response.status_code == 404
    assert _FakeClient.calls == []


# -- g. key loader unit ---------------------------------------------------------

def test_resp_native_missing_key_refuses_500_fixed_copy(monkeypatch):
    monkeypatch.delenv(KEY_REF, raising=False)
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod.load_opencode_go_key()
    assert exc_info.value.status == 500
    assert exc_info.value.message == "Invalid preset configuration."


def test_resp_native_present_key_returns_value(monkeypatch):
    monkeypatch.setenv(KEY_REF, SENTINEL)
    assert resp_mod.load_opencode_go_key() == SENTINEL


def test_resp_native_missing_key_never_echoes_env(monkeypatch):
    monkeypatch.delenv(KEY_REF, raising=False)
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod.load_opencode_go_key()
    assert SENTINEL not in str(exc_info.value)
    assert SENTINEL not in (exc_info.value.message or "")
