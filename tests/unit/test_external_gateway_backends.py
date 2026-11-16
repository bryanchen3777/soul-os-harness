"""SA-GW-OLLAMA-ALIGN-1: Ollama Cloud alignment unit tests. Pure offline.

No server, no ports, no real keys, no real provider calls. Every key value
in this file is an uppercase hyphenated SENTINEL that matches no real
credential shape. ``socket.create_connection`` is hard-blocked by the
autouse fixture below. Outbound HTTP is replaced with a mock httpx client;
nothing dials out anywhere in this file.
"""

import json
import socket
from pathlib import Path

import pytest

from src.external_gateway import backends
from src.external_gateway.backends import OllamaBackend
from src.external_gateway.backends import SUPPORTED_PROVIDERS
from src.external_gateway.backends import build_backend
from src.external_gateway.preset import Preset
from src.interfaces.schema import InterfaceError

REPO_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_PRESETS = REPO_ROOT / "configs" / "sa" / "presets.json"

KEY_SENTINEL = "SA-GW-ALIGN-1-SENTINEL-KEY"
KEY_REF = "SA_GW_ALIGN_1_REF"

EXPECTED_URL = "https://ollama.com/v1/chat/completions"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def _blocked(*args, **kwargs):
        raise AssertionError("SA-GW-OLLAMA-ALIGN-1: tests must not dial out")

    monkeypatch.setattr(socket, "create_connection", _blocked, raising=True)


def _ollama_preset(base_url=EXPECTED_URL, provider="ollama"):
    return Preset(
        name="primary",
        provider=provider,
        model="do-not-deploy-model",
        params={"temperature": 0.5, "max_tokens": 8},
        key_ref=KEY_REF,
        api_key=KEY_SENTINEL,
        base_url=base_url,
    )


# -- (1) build_backend routes provider="ollama" to OllamaBackend -------------


def test_build_backend_ollama_provider_builds_ollama_backend():
    backend = build_backend(_ollama_preset(base_url="https://ollama.com"))
    assert isinstance(backend, OllamaBackend)


# -- (2) _url() tolerance: root / trailing slash / full path converge -------


@pytest.mark.parametrize(
    "base_url",
    [
        "https://ollama.com",
        "https://ollama.com/",
        "https://ollama.com/v1/chat/completions",
    ],
)
def test_ollama_url_converges_to_single_completions_url(base_url):
    assert OllamaBackend(base_url=base_url, model="m", api_key=KEY_SENTINEL)._url() == EXPECTED_URL


def test_ollama_url_v1_base_appends_only_chat_completions():
    backend = OllamaBackend(base_url="https://ollama.com/v1", model="m", api_key=KEY_SENTINEL)
    assert backend._url() == EXPECTED_URL


def test_ollama_url_full_path_is_not_doubled():
    backend = OllamaBackend(
        base_url="https://ollama.com/v1/chat/completions", model="m", api_key=KEY_SENTINEL
    )
    url = backend._url()
    assert url == EXPECTED_URL
    assert url.count("/v1/chat/completions") == 1


# -- (3) mock httpx client: Bearer header + JSON payload shape ---------------


class _FakeResp:
    status_code = 200

    @staticmethod
    def json():
        return {"choices": [{"message": {"content": "hello-from-mock"}}]}


class _FakeClient:
    """Stand-in for httpx.Client: records post() inputs, returns fixed reply."""

    last = None

    def __init__(self, *args, **kwargs):
        self.captured = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        self.captured = {"url": url, "headers": headers, "json": json}
        type(self).last = self.captured
        return _FakeResp()


def _route():
    return {
        "soul_alias": "rem",
        "backend_alias": "primary",
        "agent_id": "agent_rem",
        "public_model_id": "do-not-deploy-rem-primary",
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def test_ollama_complete_sends_bearer_and_payload(monkeypatch):
    monkeypatch.setattr(backends.httpx, "Client", _FakeClient, raising=True)
    backend = OllamaBackend(base_url="https://ollama.com", model="do-not-deploy-model", api_key=KEY_SENTINEL)
    preset = _ollama_preset(base_url="https://ollama.com")
    params = {"temperature": 0.5, "max_tokens": 8}
    messages = [{"role": "user", "content": "hi"}]
    text = backend.complete(_route(), preset, messages, params)
    assert text.text == "hello-from-mock"
    sent = _FakeClient.last
    assert sent["url"] == EXPECTED_URL
    assert sent["headers"] == {"Authorization": "Bearer " + KEY_SENTINEL}
    assert sent["json"] == {
        "model": "do-not-deploy-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.5,
        "max_tokens": 8,
        "stream": False,
    }
    assert KEY_SENTINEL not in sent["url"]


# -- mutation guard: non-whitelist provider stays fail-closed (500) ----------


def test_build_backend_unknown_provider_fails_closed_500():
    preset = _ollama_preset(provider="dummy")
    with pytest.raises(InterfaceError) as excinfo:
        build_backend(preset)
    assert excinfo.value.status == 500


def test_supported_providers_whitelist_unchanged():
    assert SUPPORTED_PROVIDERS == frozenset({"deepseek", "ollama"})


# -- production presets file alignment (read-only check) ---------------------


# -- SA-FIX-FAILCLOSED-EMPTY-CONTENT: empty/blank upstream reply is 500 ---


class _FakeEmptyResp:
    status_code = 200

    def __init__(self, content):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _empty_case_client(content):
    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def post(self, url, headers=None, json=None):
            return _FakeEmptyResp(content)

    return _Client


@pytest.mark.parametrize("content", ["", "   ", "\n\t "])
def test_ollama_empty_or_blank_content_fails_closed_500(monkeypatch, content):
    monkeypatch.setattr(backends.httpx, "Client", _empty_case_client(content), raising=True)
    backend = OllamaBackend(
        base_url="https://ollama.com", model="do-not-deploy-model", api_key=KEY_SENTINEL
    )
    with pytest.raises(InterfaceError) as excinfo:
        backend.complete(_route(), _ollama_preset(base_url="https://ollama.com"), [{"role": "user", "content": "hi"}], {"temperature": 0.5, "max_tokens": 8})
    assert excinfo.value.status == 500
    assert excinfo.value.code == "internal"


def test_ollama_nonempty_content_still_200(monkeypatch):
    monkeypatch.setattr(backends.httpx, "Client", _empty_case_client("  hi  "), raising=True)
    backend = OllamaBackend(
        base_url="https://ollama.com", model="do-not-deploy-model", api_key=KEY_SENTINEL
    )
    text = backend.complete(_route(), _ollama_preset(base_url="https://ollama.com"), [{"role": "user", "content": "hi"}], {"temperature": 0.5, "max_tokens": 8})
    assert text.text == "  hi  "


def test_production_presets_all_ollama_with_root_base_url():
    raw = json.loads(PRODUCTION_PRESETS.read_text(encoding="utf-8"))
    assert [p["name"] for p in raw["presets"]] == [
        "primary",
        "minimax-m3.1-flash",
        "glm-5.3-flash",
        "mimo-v2.6-flash",
    ]
    for entry in raw["presets"]:
        assert entry["provider"] == "ollama", entry["name"]
        assert entry["params"]["base_url"] == "https://ollama.com", entry["name"]
        assert entry["key_ref"] == "OLLAMA_API_KEY", entry["name"]
