"""opencode-go Responses native carrier (SA-RESPONSES-NATIVE-1).

Pure outbound forwarder for the OpenAI Responses API shape
(``POST {base_url}/responses``). It carries the caller-supplied request
body verbatim to the upstream and returns the upstream JSON verbatim:
no completions<->responses translation exists anywhere in this module
(structurally impossible -- there is no mapping code to call).

Fail-closed mapping (exact, never invented; mirrors the chat carriers):
  upstream 400 -> 400 invalid_request_error (fixed copy, upstream text dropped)
  upstream 429 -> 429 rate_limit_error (fixed copy)
  upstream 401 / 403 / 404 / 5xx / network fault / timeout / non-JSON reply
    -> 500 internal (never leaks our key state or provider text)
Log hygiene: every log line carries at most the provider/preset/status.
Key, request body, reply body, headers, and provider text never enter
logs or error messages.
"""

from __future__ import annotations

import logging
import os
import secrets

import httpx

from src.interfaces.schema import InterfaceError


#: Preset/backend alias the thin ``/responses`` routes are pinned to.
RESPONSES_PRESET_NAME = "muse-spark-1.3-contributor"

#: Model the thin ``/responses`` routes accept (must equal the preset name).
RESPONSES_MODEL = "muse-spark-1.3-contributor"

#: Environment variable holding the opencode-go key (a NAME only here).
OPENCODE_GO_KEY_REF = "OPENCODE_GO_API_KEY"

#: Upstream path appended to the preset base URL (verbatim, no rewrite).
RESPONSES_PATH = "/responses"

#: Single timeout in seconds for connect, read, write, and pool alike.
RESPONSE_TIMEOUT_S = 60.0

#: Souls served by the thin ``/responses`` routes (same five as chat).
RESPONSES_SOULS = ("rem", "mai", "ruka", "akane", "mahiru")

_FIXED_400_MESSAGE = "Invalid request."
_FIXED_429_MESSAGE = "Rate limit exceeded."
_FIXED_500_MESSAGE = "Internal server error."
_FIXED_500_KEY_MESSAGE = "Invalid preset configuration."

_LOGGER = logging.getLogger("soul_os.soul_access.responses_backend")


def _fail_closed() -> None:
    """Refuse with the fixed 500 copy. Never returns."""
    raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


def _raise_for_status(status) -> None:
    """Map an upstream HTTP status to its exact InterfaceError. Never returns."""
    if status == 400:
        raise InterfaceError(400, "invalid_request_error", None, _FIXED_400_MESSAGE)
    if status == 429:
        raise InterfaceError(429, "rate_limit_error", None, _FIXED_429_MESSAGE)
    raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


def load_opencode_go_key() -> str:
    """Read the opencode-go key from the environment (name only, never logged).

    Returns the key string. A missing or empty value is a preset-side
    fault and refuses 500 (the caller never sees whether the variable
    was unset or empty). The returned key travels in the Authorization
    header only; no message built here ever contains it.
    """
    key = os.environ.get(OPENCODE_GO_KEY_REF)
    if not isinstance(key, str) or not key:
        raise InterfaceError(500, "internal", None, _FIXED_500_KEY_MESSAGE)
    return key


def forward_responses_request(request_body, base_url, api_key, timeout_s=60.0,
                             session_id=None):
    """Forward one Responses request natively; return ``(status, data)``.

    ``request_body`` is sent verbatim as the upstream JSON payload and
    the upstream 200 JSON is returned verbatim as ``data`` -- no field
    is renamed, dropped, or synthesized in either direction. Any
    non-200 status, network fault, timeout, or non-JSON reply raises
    the mapped ``InterfaceError`` (see module docstring). ``api_key``
    and ``request_body`` never enter logs or error messages.
    Optional ``session_id`` is forwarded as the ``x-opencode-session``
    upstream header. When it is missing (None) or empty, a per-request
    ``thin-<16 hex>`` value (``secrets.token_hex(8)``) is generated and
    injected instead, so upstream always receives the header; a supplied
    value is forwarded verbatim and never overwritten. Its value never
    enters logs, only the ``session_header_present`` /
    ``session_header_injected`` booleans.
    """
    try:
        timeout = float(timeout_s)
    except (TypeError, ValueError):
        _fail_closed()
    if not isinstance(base_url, str) or not base_url:
        _fail_closed()
    if not isinstance(api_key, str) or not api_key:
        _fail_closed()
    url = base_url.rstrip("/") + RESPONSES_PATH
    headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
    session_header_present = isinstance(session_id, str) and bool(session_id)
    if session_header_present:
        headers["x-opencode-session"] = session_id
        session_header_injected = False
    else:
        headers["x-opencode-session"] = "thin-" + secrets.token_hex(8)
        session_header_injected = True
    try:
        timeout_obj = httpx.Timeout(
            connect=timeout, read=timeout, write=timeout, pool=timeout
        )
    except Exception:
        _fail_closed()
    try:
        with httpx.Client(timeout=timeout_obj) as client:
            resp = client.post(url, headers=headers, json=request_body)
    except InterfaceError:
        raise
    except httpx.TimeoutException:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected)
        _fail_closed()
    except Exception:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected)
        _fail_closed()
    if resp.status_code != 200:
        _LOGGER.info(
            "ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s",
            RESPONSES_PRESET_NAME,
            resp.status_code,
            session_header_present,
            session_header_injected,
        )
        _raise_for_status(resp.status_code)
    try:
        data = resp.json()
    except InterfaceError:
        raise
    except Exception:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected)
        _fail_closed()
    _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s", RESPONSES_PRESET_NAME, 200, session_header_present, session_header_injected)
    return (200, data)
