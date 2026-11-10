"""S6-W4 outward HTTP assembly: path auth errors over the S5 route layer.

Pure application layer only. This module exposes exactly one factory,
``create_external_app(registry, load_preset_fn, chat_backend)``, which
returns an ASGI app built on the repo's existing web framework (FastAPI,
already used by ``src/io/gateway.py``). No new third-party dependency.

What this module does NOT do (S7 owns the carrier):
  - no port attach, no serve loop, no background task;
  - no real provider dial-out (``chat_backend`` is injected; tests inject
    a fake, production injects the S7 carrier);
  - no file reads of its own (preset files are read only inside the
    injected ``load_preset_fn``);
  - no environment reads of its own (keys are read only inside the
    injected ``load_preset_fn``).

Request order per call (fail-closed, first refusal wins):
  1. ``parse_external_path`` -- pure shape check, before everything
     (unknown shape / unknown soul returns 404 without touching auth,
     the registry, the environment, or any file);
  2. method gate (chat needs POST, models needs GET, else 404);
  3. ``extract_bearer`` -- missing / malformed header returns 401;
  4. ``authenticate`` -- unknown / revoked token returns 401;
  5. ``resolve_route`` -- missing route returns 404;
  6. agent map check -- a route whose agent is not ``agent_<soul>``
     returns 500 with a fixed safe message;
  7. ``load_preset_fn`` -- unknown preset name inside an otherwise
     well-formed config returns 404; a broken config or a missing /
     empty key returns 500 with a fixed safe message;
  8. ``authorize_route`` -- token without this soul/preset pair
     returns 403 (checked after the route is known, before the body,
     so a forbidden caller never triggers body parsing);
  9. models endpoint returns a static list; chat parses the body
     (``parse_chat_request``: stream:true and schema faults are 400),
     calls ``chat_backend.complete``, and renders the reply with
     ``to_chat_response``.

Every error goes through ``to_error`` (fixed copy for 5xx, credential
scrub for the rest). The only log lines emitted here carry
soul / preset / endpoint / status / request id -- never headers,
never the body, never a token, never a key.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Protocol

from fastapi import FastAPI
from fastapi import Request
from fastapi.responses import JSONResponse

from src.external_gateway.openai_shim import parse_chat_request
from src.external_gateway.openai_shim import to_chat_response
from src.external_gateway.openai_shim import to_error
from src.external_gateway.preset import Preset
from src.external_gateway.preset import load_preset
from src.external_gateway.routing import SOUL_AGENT_MAP
from src.external_gateway.routing import SOUL_WHITELIST
from src.interfaces.config import resolve_route
from src.interfaces.external_auth import extract_bearer
from src.interfaces.identity import authenticate
from src.interfaces.identity import authorize_route
from src.interfaces.schema import InterfaceError

#: Port number reserved for the outward edge. S7 owns the carrier that
#: will actually accept calls there; this ticket only assembles the app.
#: Defined as a plain int constant -- this module never opens it.
EXTERNAL_PORT = 18765

_LOGGER = logging.getLogger("soul_os.external_gateway.app")

_FIXED_404_MESSAGE = "Unknown route."
_FIXED_403_MESSAGE = "Route access is forbidden."
_FIXED_401_MESSAGE = "Invalid credentials."
_FIXED_500_MESSAGE = "Internal server error."
_FIXED_500_PRESET_MESSAGE = "Invalid preset configuration."
_FIXED_500_ROUTE_MESSAGE = "Invalid gateway route configuration."

_CHAT_ENDPOINT = "chat/completions"
_MODELS_ENDPOINT = "models"


def _not_found(param="route"):
    raise InterfaceError(404, "route_not_found", param, _FIXED_404_MESSAGE)


def parse_external_path(path: str) -> tuple[str, str, str]:
    """Split an outward path into (soul, preset, endpoint). Pure function.

    Accepts only ``/{soul}/{preset}/v1/chat/completions`` and
    ``/{soul}/{preset}/v1/models`` -- exact case, no trailing slash, no
    extra segments. ``soul`` must be one of the five whitelisted souls;
    ``preset`` is any non-empty single segment. Anything else raises
    ``InterfaceError(404, "route_not_found", ...)`` before any caller
    touches the registry, the environment, or a file.
    """
    parts = path.split("/") if isinstance(path, str) else []
    if len(parts) == 6 and parts[0] == "" and parts[3] == "v1":
        if parts[4] == "chat" and parts[5] == "completions":
            endpoint = _CHAT_ENDPOINT
        else:
            _not_found()
    elif len(parts) == 5 and parts[0] == "" and parts[3] == "v1":
        if parts[4] == "models":
            endpoint = _MODELS_ENDPOINT
        else:
            _not_found()
    else:
        _not_found()
    soul = parts[1]
    preset = parts[2]
    if soul not in SOUL_WHITELIST:
        _not_found()
    if not isinstance(preset, str) or preset == "":
        _not_found()
    return (soul, preset, endpoint)


class ChatBackend(Protocol):
    """Outward provider carrier (injected; S7 owns the real one)."""

    def complete(self, route, preset, messages, params) -> str:
        """Return the assistant reply text for one admitted chat call."""
        ...  # pragma: no cover - protocol shape only


class NotImplementedBackend:
    """Default carrier: no provider wired, every chat call fails closed."""

    def complete(self, route, preset, messages, params) -> str:
        raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


def file_preset_loader(preset_path) -> Callable[[str], Preset]:
    """Adapt a preset JSON file to the ``load_preset_fn`` slot.

    The returned loader takes a preset name and returns its ``Preset``.
    An unknown name inside an otherwise well-formed file raises 404
    ``route_not_found`` (same split the S5 layer keeps); a broken file
    or a missing / empty key lets the loader's own 500 propagate.
    """

    def _load(name: str) -> Preset:
        import json

        names = None
        try:
            with open(preset_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            entries = raw.get("presets") if isinstance(raw, dict) else None
            if isinstance(entries, list):
                seen = set()
                ok = True
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                        ok = False
                        break
                    seen.add(entry["name"])
                if ok:
                    names = seen
        except Exception:
            names = None
        if names is not None and name not in names:
            raise InterfaceError(404, "route_not_found", "route", _FIXED_404_MESSAGE)
        return load_preset(preset_path, name=name)

    return _load


def _missing_preset_loader(name: str) -> Preset:
    raise InterfaceError(500, "invalid_preset", "preset", _FIXED_500_PRESET_MESSAGE)


def _error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content=to_error(status, code, message))


def _from_interface_error(exc: InterfaceError) -> JSONResponse:
    return _error_response(exc.status, exc.code, exc.message)


def create_external_app(registry, load_preset_fn=None, chat_backend=None) -> FastAPI:
    """Assemble the outward OpenAI-shaped ASGI app. No carrier attached.

    ``registry`` is a ``RegistryProvider`` (or a ready ``Registry``
    snapshot); ``load_preset_fn`` maps a preset name to its ``Preset``
    (see ``file_preset_loader``); ``chat_backend`` implements
    ``ChatBackend``. Omitted slots fail closed: a missing loader makes
    every route 500, a missing carrier installs ``NotImplementedBackend``.
    """
    loader = load_preset_fn if callable(load_preset_fn) else _missing_preset_loader
    backend = chat_backend if chat_backend is not None else NotImplementedBackend()

    app = FastAPI(redirect_slashes=False)

    def _snapshot():
        current = getattr(registry, "current", None)
        if callable(current):
            return current()
        return registry

    async def _handle(request: Request) -> JSONResponse:
        req_id = uuid.uuid4().hex
        scope = request.scope
        raw_path = scope.get("path", "")
        try:
            soul, preset_name, endpoint = parse_external_path(raw_path)
        except InterfaceError as exc:
            _LOGGER.info("ext status=404 endpoint=unknown req=%s", req_id)
            return _from_interface_error(exc)
        method = scope.get("method", "")
        if (endpoint == _CHAT_ENDPOINT and method != "POST") or (
            endpoint == _MODELS_ENDPOINT and method != "GET"
        ):
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=404 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(404, "route_not_found", _FIXED_404_MESSAGE)
        raw_headers = []
        for key, value in scope.get("headers", []):
            raw_headers.append((key.decode("latin-1"), value.decode("latin-1")))
        try:
            token, _headers = extract_bearer(raw_headers)
        except InterfaceError as exc:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=%s req=%s",
                soul,
                preset_name,
                endpoint,
                exc.status,
                req_id,
            )
            return _from_interface_error(exc)
        try:
            snapshot = _snapshot()
            client = authenticate(token, snapshot)
        except InterfaceError as exc:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=401 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(401, "unauthorized", _FIXED_401_MESSAGE)
        except Exception:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=401 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(401, "unauthorized", _FIXED_401_MESSAGE)
        try:
            route = resolve_route(snapshot, soul, preset_name)
        except InterfaceError as exc:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=404 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _from_interface_error(exc)
        if route.agent_id != SOUL_AGENT_MAP[soul]:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "invalid_route", _FIXED_500_ROUTE_MESSAGE)
        try:
            preset_obj = loader(preset_name)
        except InterfaceError as exc:
            if exc.status == 404:
                _LOGGER.info(
                    "ext soul=%s preset=%s endpoint=%s status=404 req=%s",
                    soul,
                    preset_name,
                    endpoint,
                    req_id,
                )
                return _error_response(404, "route_not_found", _FIXED_404_MESSAGE)
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "invalid_preset", _FIXED_500_PRESET_MESSAGE)
        except Exception:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "invalid_preset", _FIXED_500_PRESET_MESSAGE)
        try:
            authorize_route(client, route)
        except InterfaceError:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=403 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(403, "forbidden", _FIXED_403_MESSAGE)
        if endpoint == _MODELS_ENDPOINT:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=200 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return JSONResponse(
                status_code=200,
                content={"object": "list", "data": [{"id": preset_name}]},
            )
        try:
            body = await request.json()
        except Exception:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=400 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(400, "invalid_request_error", "Invalid request.")
        try:
            internal = parse_chat_request(body)
        except InterfaceError as exc:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=400 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _from_interface_error(exc)
        params = {"temperature": internal.temperature, "max_tokens": internal.max_tokens}
        try:
            text = backend.complete(route, preset_obj, internal.messages, params)
        except InterfaceError as exc:
            if isinstance(exc.status, int) and 400 <= exc.status < 500:
                _LOGGER.info(
                    "ext soul=%s preset=%s endpoint=%s status=%s req=%s",
                    soul,
                    preset_name,
                    endpoint,
                    exc.status,
                    req_id,
                )
                return _from_interface_error(exc)
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "internal", _FIXED_500_MESSAGE)
        except Exception:
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "internal", _FIXED_500_MESSAGE)
        if not isinstance(text, str):
            _LOGGER.info(
                "ext soul=%s preset=%s endpoint=%s status=500 req=%s",
                soul,
                preset_name,
                endpoint,
                req_id,
            )
            return _error_response(500, "internal", _FIXED_500_MESSAGE)
        _LOGGER.info(
            "ext soul=%s preset=%s endpoint=%s status=200 req=%s",
            soul,
            preset_name,
            endpoint,
            req_id,
        )
        return JSONResponse(status_code=200, content=to_chat_response(text, internal.model))

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
    async def _entry(request: Request) -> JSONResponse:
        return await _handle(request)

    return app
