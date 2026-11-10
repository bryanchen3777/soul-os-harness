"""S7-W5 outward provider carrier: real ChatBackend over repo httpx.

Synchronous only (``httpx.Client``; no ``AsyncClient`` -- ``app.py`` calls
``backend.complete`` without ``await``). No port attach, no serve loop, no
file reads, no environment reads: the preset (model, key, base URL) arrives
ready-made from ``load_preset`` through S6.

Fail-closed mapping (exact, never invented):
  provider 400 -> 400 invalid_request_error (fixed copy, upstream text dropped)
  provider 429 -> 429 rate_limit_error (fixed copy)
  provider 401 / 403 (our key refused = our side config fault)
    -> 500 internal (never leak our key state to the caller)
  provider 404 (model name fault = preset config fault) -> 500 internal
  provider 5xx / network fault / timeout / non-str reply -> 500 internal

Log hygiene: every log line carries provider / preset name / status only.
Key, messages, reply text, headers, tokens, and provider text never enter
logs or error messages (``Preset`` repr already hides the key; this module
never undoes that).
"""

from __future__ import annotations

import httpx
import logging
from typing import Any

from src.external_gateway.app import ChatBackend
from src.external_gateway.preset import Preset
from src.interfaces.schema import InterfaceError


#: Single timeout in seconds for connect, read, write, and pool alike.
PROVIDER_TIMEOUT_S = 60.0

#: First batch: only these two providers are carried here. The other two
#: names ``preset.py`` accepts (openai / gemini) fail closed in
#: ``build_backend`` below.
SUPPORTED_PROVIDERS = frozenset({"deepseek", "ollama"})

_DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"

_FIXED_400_MESSAGE = "Invalid request."
_FIXED_429_MESSAGE = "Rate limit exceeded."
_FIXED_500_MESSAGE = "Internal server error."
_FIXED_PRESET_MESSAGE = "Invalid preset configuration."

_LOGGER = logging.getLogger("soul_os.external_gateway.backends")


def _timeout() -> httpx.Timeout:
    """One timeout value applied to connect, read, write, and pool."""
    return httpx.Timeout(
        connect=PROVIDER_TIMEOUT_S,
        read=PROVIDER_TIMEOUT_S,
        write=PROVIDER_TIMEOUT_S,
        pool=PROVIDER_TIMEOUT_S,
    )


def _wire_messages(messages: Any) -> list:
    """Render internal messages to plain role/content dicts."""
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append({"role": m.get("role"), "content": m.get("content")})
        else:
            out.append({"role": getattr(m, "role", None), "content": getattr(m, "content", None)})
    return out


def _raise_for_status(status: Any) -> None:
    """Map a provider HTTP status to its exact InterfaceError. Never returns."""
    if status == 400:
        raise InterfaceError(400, "invalid_request_error", "preset", _FIXED_400_MESSAGE)
    if status == 429:
        raise InterfaceError(429, "rate_limit_error", "ratelimit", _FIXED_429_MESSAGE)
    raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


def _fail_closed() -> None:
    """Refuse with the fixed 500 copy. Never returns."""
    raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


class DeepSeekBackend:
    """DeepSeek chat carrier (key travels in the header, never in logs)."""

    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    def complete(self, route: Any, preset: Any, messages: Any, params: Any) -> str:
        try:
            payload = {
                "model": self._model,
                "messages": _wire_messages(messages),
                "temperature": params["temperature"],
                "max_tokens": params["max_tokens"],
                "stream": False,
            }
        except (KeyError, TypeError):
            _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 500)
            _fail_closed()
        headers = {"Authorization": "Bearer " + self._api_key}
        try:
            with httpx.Client(timeout=_timeout()) as client:
                resp = client.post(_DEEPSEEK_URL, headers=headers, json=payload)
        except InterfaceError:
            raise
        except httpx.TimeoutException:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 500)
            _fail_closed()
        except Exception:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 500)
            _fail_closed()
        if resp.status_code != 200:
            _LOGGER.info(
                "ext provider=%s preset=%s status=%s", "deepseek", preset.name, resp.status_code
            )
            _raise_for_status(resp.status_code)
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 500)
            _fail_closed()
        if not isinstance(content, str):
            _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 500)
            _fail_closed()
        _LOGGER.info("ext provider=%s preset=%s status=%s", "deepseek", preset.name, 200)
        return content


class OllamaBackend:
    """Ollama chat carrier (OpenAI-compatible path; no auth header here)."""

    def __init__(self, base_url: str, model: str) -> None:
        self._base_url = base_url
        self._model = model

    def _url(self) -> str:
        base = self._base_url if isinstance(self._base_url, str) else ""
        return base.rstrip("/") + "/v1/chat/completions"

    def complete(self, route: Any, preset: Any, messages: Any, params: Any) -> str:
        try:
            payload = {
                "model": self._model,
                "messages": _wire_messages(messages),
                "temperature": params["temperature"],
                "max_tokens": params["max_tokens"],
                "stream": False,
            }
        except (KeyError, TypeError):
            _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 500)
            _fail_closed()
        try:
            with httpx.Client(timeout=_timeout()) as client:
                resp = client.post(self._url(), json=payload)
        except InterfaceError:
            raise
        except httpx.TimeoutException:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 500)
            _fail_closed()
        except Exception:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 500)
            _fail_closed()
        if resp.status_code != 200:
            _LOGGER.info(
                "ext provider=%s preset=%s status=%s", "ollama", preset.name, resp.status_code
            )
            _raise_for_status(resp.status_code)
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except Exception:
            _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 500)
            _fail_closed()
        if not isinstance(content, str):
            _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 500)
            _fail_closed()
        _LOGGER.info("ext provider=%s preset=%s status=%s", "ollama", preset.name, 200)
        return content


class PresetRoutingBackend:
    """Per-call dispatch: pick the carrier from the preset on every call.

    This is the single object handed to ``create_external_app``.
    """

    def complete(self, route: Any, preset: Preset, messages: Any, params: Any) -> str:
        return build_backend(preset).complete(route, preset, messages, params)


def build_backend(preset: Preset) -> ChatBackend:
    """Build the carrier for one resolved preset. Unknown provider: 500."""
    provider = getattr(preset, "provider", None)
    if provider == "deepseek":
        return DeepSeekBackend(api_key=preset.api_key, model=preset.model)
    if provider == "ollama":
        return OllamaBackend(base_url=preset.base_url, model=preset.model)
    raise InterfaceError(500, "invalid_preset", "preset", _FIXED_PRESET_MESSAGE)
