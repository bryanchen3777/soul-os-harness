"""Model-alias normalization + graded-400 mapping (sandbox only, mock upstream).

Decision-complete scope: product change is confined to
``src/soul_access/responses_backend.py`` (in-place alias rewrite before
send; in-memory 400 feature classification). ``scripts/serve_sa_thin.py``
needs no wiring (body already flows straight into the forwarder; the
responses endpoint already returns ``to_error(exc.status, exc.code,
exc.message)`` with no other fields). Frozen ``src/external_gateway/**``
is untouched. No server is started, no production port is bound, no real
request leaves the process (``httpx`` is monkeypatched on the backend
module attribute; the only key values here are synthetic sandbox tokens).

Coverage:
  a. known public aliases (sa-rem-/sa-mai-/sa-ruka-/sa-akane-/sa-mahiru-
     prefixes) rewrite in place to the native model before send;
  b. the native model itself is left untouched (normalized=False);
  c. unknown model values pass through verbatim (fail-closed, no guessing);
  d. non-dict / missing / non-string models never crash the forwarder;
  e. rewrite outcome is logged only as the ``model_normalized`` boolean
     (alias value, body, and key never enter logs);
  f. upstream 400 classifies in memory: model feature ->
     upstream_model_not_found; parameter feature ->
     upstream_invalid_parameter; anything else -> upstream_bad_request;
  g. leak guard: an upstream 400 carrying ``api_key=sk-...`` surfaces
     neither the secret nor the upstream text in the raised error or in
     the ``to_error`` envelope the endpoint would return.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.external_gateway.openai_shim import to_error  # noqa: E402
from src.interfaces.schema import InterfaceError  # noqa: E402
from src.soul_access import responses_backend as resp_mod  # noqa: E402

NATIVE = resp_mod.RESPONSES_MODEL
BASE_URL = "https://model-norm.invalid/zen/go/v1"
FAKE_KEY = "model-norm-sandbox-fake-key"
SECRET = "sk-fake-secret-value-12345"

NATIVE_UPSTREAM = {"id": "model-norm-0001", "object": "response", "model": NATIVE}


# -- fake httpx layer (records inputs, replays canned upstream) ---------------


class _FakeTimeout:
    def __init__(self, connect=None, read=None, write=None, pool=None):
        self.connect = connect
        self.read = read
        self.write = write
        self.pool = pool


class _FakeTimeoutException(Exception):
    pass


class _FakeResp:
    def __init__(self, status_code, payload=None, *, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.content = text.encode("utf-8") if isinstance(text, str) else b""

    def json(self):
        return self._payload


class _FakeClient:
    behaviour = {"status": 200, "payload": NATIVE_UPSTREAM, "text": ""}
    calls = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResp(
            _FakeClient.behaviour["status"],
            _FakeClient.behaviour.get("payload"),
            text=_FakeClient.behaviour.get("text", ""),
        )


class _FakeHttpx:
    Timeout = _FakeTimeout
    TimeoutException = _FakeTimeoutException
    Client = _FakeClient


@pytest.fixture()
def _fake_upstream(monkeypatch):
    monkeypatch.setattr(resp_mod, "httpx", _FakeHttpx, raising=True)
    _FakeClient.calls = []
    _FakeClient.behaviour = {"status": 200, "payload": NATIVE_UPSTREAM, "text": ""}
    return _FakeClient


# -- a. known aliases rewrite to native ---------------------------------------

_ALIAS_MODELS = [
    "sa-rem-primary",
    "sa-rem-muse-spark-1.3-contributor",
    "sa-mai-primary",
    "sa-mai-minimax-m3.1-flash",
    "sa-ruka-primary",
    "sa-akane-glm-5.3-flash",
    "sa-mahiru-mimo-v2.6-flash",
]


@pytest.mark.parametrize("alias", _ALIAS_MODELS)
def test_model_alias_rewrites_to_native_in_place(_fake_upstream, alias):
    body = {"model": alias, "input": "hi"}
    status, data = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert data == NATIVE_UPSTREAM
    # In-place rewrite: the caller's own dict now carries the native model.
    assert body["model"] == NATIVE
    # The wire payload carries the native model, never the alias.
    assert len(_FakeClient.calls) == 1
    assert _FakeClient.calls[0]["json"]["model"] == NATIVE
    assert _FakeClient.calls[0]["url"] == BASE_URL + "/responses"
    assert FAKE_KEY not in _FakeClient.calls[0]["url"]


# -- b. native value untouched -------------------------------------------------

def test_native_model_left_untouched(_fake_upstream):
    body = {"model": NATIVE, "input": "hi"}
    status, _ = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert body["model"] == NATIVE
    assert _FakeClient.calls[0]["json"]["model"] == NATIVE


# -- c. unknown values pass through verbatim (fail-closed) --------------------

_UNKNOWN_MODELS = ["gpt-4", "sa-remember", "sa-rem", "muse-spark", "", 123, None]


@pytest.mark.parametrize("weird", _UNKNOWN_MODELS)
def test_unknown_model_passthrough_verbatim(_fake_upstream, weird):
    body = {"model": weird, "input": "hi"}
    status, _ = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert body["model"] == weird
    assert _FakeClient.calls[0]["json"]["model"] == weird


def test_missing_model_key_passthrough(_fake_upstream):
    body = {"input": "hi"}
    status, _ = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert "model" not in body
    assert "model" not in _FakeClient.calls[0]["json"]


# -- d. non-dict bodies never crash --------------------------------------------

_NON_DICT_BODIES = [["model", "sa-rem-primary"], "sa-rem-primary", None, 42]


@pytest.mark.parametrize("weird_body", _NON_DICT_BODIES)
def test_non_dict_body_never_crashes(_fake_upstream, weird_body):
    status, data = resp_mod.forward_responses_request(
        weird_body, BASE_URL, FAKE_KEY
    )
    assert status == 200
    assert data == NATIVE_UPSTREAM
    assert _FakeClient.calls[0]["json"] == weird_body


# -- e. log hygiene: boolean only ----------------------------------------------

def test_rewrite_logged_as_boolean_only(_fake_upstream, caplog):
    body = {"model": "sa-rem-primary", "input": "hi"}
    with caplog.at_level(logging.INFO, logger="soul_os.soul_access.responses_backend"):
        status, _ = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    assert caplog.records, "expected the forwarder to log"
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "model_normalized=True" in rendered
    assert "sa-rem-primary" not in rendered
    assert "sa-rem-primary" not in str([r.args for r in caplog.records])
    assert FAKE_KEY not in rendered


def test_no_rewrite_logs_false_without_values(_fake_upstream, caplog):
    body = {"model": "gpt-4", "input": "hi"}
    with caplog.at_level(logging.INFO, logger="soul_os.soul_access.responses_backend"):
        status, _ = resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    assert status == 200
    rendered = "\n".join(r.getMessage() for r in caplog.records)
    assert "model_normalized=False" in rendered
    assert "gpt-4" not in rendered


# -- f. graded 400 classification (in-memory only) ------------------------------

_400_CASES = [
    ("Model isn't included in the request", "upstream_model_not_found"),
    ("Unknown model 'sa-rem-primary'", "upstream_unknown_fallback_marker"),
    ("model_not_found: no such model", "upstream_model_not_found"),
    ("Invalid parameter: temperature out of range", "upstream_invalid_parameter"),
    ("Unsupported parameter 'reasoning'", "upstream_invalid_parameter"),
    ("Extra fields not permitted in responses body", "upstream_invalid_parameter"),
    ("upstream-says-no", "upstream_bad_request"),
    ("", "upstream_bad_request"),
]


@pytest.mark.parametrize("upstream_text,expect_code", _400_CASES)
def test_graded_400_mapping(_fake_upstream, upstream_text, expect_code):
    if expect_code == "upstream_unknown_fallback_marker":
        expect_code = "upstream_model_not_found"
    _FakeClient.behaviour = {"status": 400, "payload": None, "text": upstream_text}
    body = {"model": NATIVE, "input": "hi"}
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod.forward_responses_request(body, BASE_URL, FAKE_KEY)
    exc = exc_info.value
    assert exc.status == 400
    assert exc.code == expect_code
    # Upstream text is dropped: the fixed copy carries none of it.
    if upstream_text:
        assert upstream_text not in (exc.message or "")
    assert FAKE_KEY not in (exc.message or "")


def test_raise_for_status_backward_compatible_without_body():
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod._raise_for_status(400)
    assert exc_info.value.status == 400
    assert exc_info.value.code == "upstream_bad_request"
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod._raise_for_status(400, None)
    assert exc_info.value.code == "upstream_bad_request"
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod._raise_for_status(429)
    assert (exc_info.value.status, exc_info.value.code) == (429, "rate_limit_error")
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod._raise_for_status(500)
    assert (exc_info.value.status, exc_info.value.code) == (500, "internal")


# -- g. leak guard: secret + upstream text never surface -----------------------

def test_graded_400_never_leaks_secret_or_upstream_text(_fake_upstream):
    upstream_text = (
        "model_not_found: check api_key=" + SECRET + " and retry"
    )
    _FakeClient.behaviour = {"status": 400, "payload": None, "text": upstream_text}
    with pytest.raises(InterfaceError) as exc_info:
        resp_mod.forward_responses_request(
            {"model": NATIVE, "input": "hi"}, BASE_URL, FAKE_KEY
        )
    exc = exc_info.value
    assert exc.status == 400
    assert exc.code == "upstream_model_not_found"
    # The shape the thin endpoint returns: only status/code/message.
    envelope = to_error(exc.status, exc.code, exc.message)
    blob = json.dumps(envelope, ensure_ascii=False)
    assert SECRET not in blob
    assert SECRET not in (exc.message or "")
    assert upstream_text not in blob
    assert "api_key" not in blob
    assert set(envelope["error"].keys()) == {"message", "type", "code"}
    assert envelope["error"]["type"] == "invalid_request_error"
    assert FAKE_KEY not in blob
