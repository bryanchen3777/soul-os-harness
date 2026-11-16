"""opencode-go Responses native carrier (SA-RESPONSES-NATIVE-1).

Pure outbound forwarder for the OpenAI Responses API shape
(``POST {base_url}/responses``). It carries the caller-supplied request
body verbatim to the upstream and returns the upstream JSON verbatim:
no completions<->responses translation exists anywhere in this module
(structurally impossible -- there is no mapping code to call).

Fail-closed mapping (exact, never invented; mirrors the chat carriers):
  upstream 400 -> 400, classified in memory only (never logged, never echoed):
    model feature   -> upstream_model_not_found (fixed copy)
    parameter feature -> upstream_invalid_parameter (fixed copy)
    anything else     -> upstream_bad_request (fixed copy)
  upstream 429 -> 429 rate_limit_error (fixed copy)
  upstream 401 / 403 / 404 / 5xx / network fault / timeout / non-JSON reply
    -> 500 internal (never leaks our key state or provider text)
Model normalization: before sending, a dict body whose ``model`` is a known
public alias (``sa-rem-`` / ``sa-mai-`` / ``sa-ruka-`` / ``sa-akane-`` /
``sa-mahiru-`` prefixes, or the thin preset name) is rewritten in place to
the native model. The native value is left untouched; unknown values pass
through verbatim (fail-closed, never guessed).
Log hygiene: every log line carries at most the provider/preset/status plus
the ``model_normalized`` boolean. Key, request body, reply body, headers,
and provider text never enter logs or error messages.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

#: Fixed 400 copies per upstream feature class (upstream text is dropped).
_FIXED_400_MODEL_MESSAGE = "Upstream model not found for this request."
_FIXED_400_PARAM_MESSAGE = "Upstream rejected a request parameter."

#: Public-model-id prefixes (per soul) that normalize to the native model.
_MODEL_ALIAS_PREFIXES = ("sa-rem-", "sa-mai-", "sa-ruka-", "sa-akane-", "sa-mahiru-")

#: Exact public alias that normalizes to the native model. Today the thin
#: preset name equals the native model, so the native check below wins and
#: this entry is a no-op; it exists so a future divergence still normalizes.
_MODEL_ALIAS_EXACT = frozenset({RESPONSES_PRESET_NAME})

#: Upstream body read cap for in-memory 400 classification (4 KiB).
_UPSTREAM_BODY_LIMIT = 4096

#: Upstream 400 feature patterns (case-insensitive, matched in memory only).
_MODEL_NOT_FOUND_PAT = re.compile(
    r"model isn't included|unknown model|model_not_found", re.IGNORECASE
)
_INVALID_PARAM_PAT = re.compile(
    r"invalid parameter|unsupported parameter|extra fields not permitted",
    re.IGNORECASE,
)

_LOGGER = logging.getLogger("soul_os.soul_access.responses_backend")


def _fail_closed() -> None:
    """Refuse with the fixed 500 copy. Never returns."""
    raise InterfaceError(500, "internal", None, _FIXED_500_MESSAGE)


def _normalize_model(request_body) -> bool:
    """Rewrite a known public model alias to the native model in place.

    Returns True when a rewrite happened, False otherwise. A non-dict
    body, a missing/non-string ``model``, the native value itself, and any
    unknown value all return False and leave the body untouched
    (fail-closed: unknown values pass through verbatim, never guessed).
    Only the boolean outcome may be logged; the values never are.
    """
    if not isinstance(request_body, dict):
        return False
    try:
        model = request_body.get("model")
    except Exception:
        return False
    if not isinstance(model, str):
        return False
    if model == RESPONSES_MODEL:
        return False
    if model in _MODEL_ALIAS_EXACT or model.startswith(_MODEL_ALIAS_PREFIXES):
        try:
            request_body["model"] = RESPONSES_MODEL
        except Exception:
            return False
        return True
    return False


def _upstream_body_text(resp) -> str:
    """Read at most 4 KiB of the upstream error body for classification.

    Never raises and never logs: the text is used only for in-memory
    feature matching. Any fault (missing attributes, decode errors,
    non-serializable payloads) yields ``""`` (generic 400 branch).
    """
    text = None
    try:
        text = resp.text
    except Exception:
        text = None
    if isinstance(text, bytes):
        try:
            text = text.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if not isinstance(text, str) or not text:
        try:
            content = resp.content
        except Exception:
            content = None
        if isinstance(content, bytes) and content:
            try:
                text = content.decode("utf-8", errors="replace")
            except Exception:
                return ""
        else:
            try:
                payload = resp.json()
            except Exception:
                return ""
            try:
                text = json.dumps(payload, ensure_ascii=False)
            except Exception:
                return ""
            if not isinstance(text, str):
                return ""
    return text[:_UPSTREAM_BODY_LIMIT]


def _raise_for_status(status, body_text=None) -> None:
    """Map an upstream HTTP status to its exact InterfaceError. Never returns.

    ``body_text`` is optional (existing callers pass status only) and is
    used solely for in-memory 400 feature classification; it never enters
    logs or error messages.
    """
    if status == 400:
        text = body_text if isinstance(body_text, str) else ""
        if _MODEL_NOT_FOUND_PAT.search(text):
            raise InterfaceError(
                400, "upstream_model_not_found", None, _FIXED_400_MODEL_MESSAGE
            )
        if _INVALID_PARAM_PAT.search(text):
            raise InterfaceError(
                400, "upstream_invalid_parameter", None, _FIXED_400_PARAM_MESSAGE
            )
        raise InterfaceError(400, "upstream_bad_request", None, _FIXED_400_MESSAGE)
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


def _is_stream_request(request_body) -> bool:
    """Report whether the caller asked for SSE streaming (never raises).

    ``True`` only when ``request_body`` is a dict with a truthy ``stream``
    value. Non-dict bodies (list/str/None/...) always read as ``False``,
    so the classic whole-JSON path keeps serving them verbatim.
    """
    try:
        return isinstance(request_body, dict) and bool(request_body.get("stream"))
    except Exception:
        return False


def _forward_stream_request(url, headers, request_body, timeout_obj,
                            session_header_present, session_header_injected,
                            model_normalized):
    """Forward one streaming Responses request; return ``(200, gen, True)``.

    ``gen`` is a bytes generator yielding ``resp.iter_bytes()`` chunks
    verbatim (no SSE frame parsing, no JSON decoding -- ``resp.json()`` is
    never called on this path). A non-200 first packet reads at most 4 KiB
    and raises the mapped ``InterfaceError`` via ``_raise_for_status``.
    Mid-stream timeout/transport faults end the generator silently (EOF,
    no injected string, no upstream text). The client is closed when the
    generator exhausts, errors, or is closed early; the closing log carries
    only provider/preset/status/stream/bytes_sent numerics.
    """
    try:
        client = httpx.Client(timeout=timeout_obj)
    except Exception:
        _fail_closed()
    try:
        stream_cm = client.stream("POST", url, headers=headers, json=request_body)
        resp = stream_cm.__enter__()
    except InterfaceError:
        raise
    except httpx.TimeoutException:
        _LOGGER.info(
            "ext provider=opencode-go preset=%s status=%s stream=%s",
            RESPONSES_PRESET_NAME, 500, True,
        )
        try:
            client.close()
        except Exception:
            pass
        _fail_closed()
    except Exception:
        _LOGGER.info(
            "ext provider=opencode-go preset=%s status=%s stream=%s",
            RESPONSES_PRESET_NAME, 500, True,
        )
        try:
            client.close()
        except Exception:
            pass
        _fail_closed()
    if resp.status_code != 200:
        try:
            try:
                resp.read()
            except Exception:
                pass
            body_text = _upstream_body_text(resp)
        except Exception:
            body_text = ""
        _LOGGER.info(
            "ext provider=opencode-go preset=%s status=%s stream=%s",
            RESPONSES_PRESET_NAME, resp.status_code, True,
        )
        try:
            stream_cm.__exit__(None, None, None)
        except Exception:
            pass
        try:
            client.close()
        except Exception:
            pass
        _raise_for_status(resp.status_code, body_text)

    def _byte_gen():
        bytes_sent = 0
        try:
            for chunk in resp.iter_bytes():
                if not isinstance(chunk, (bytes, bytearray)):
                    continue
                bytes_sent += len(chunk)
                yield bytes(chunk)
        except Exception:
            return
        finally:
            try:
                stream_cm.__exit__(None, None, None)
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
            _LOGGER.info(
                "ext provider=opencode-go preset=%s status=%s stream=%s bytes_sent=%s",
                RESPONSES_PRESET_NAME, 200, True, bytes_sent,
            )

    return (200, _byte_gen(), True)


def forward_responses_request(request_body, base_url, api_key, timeout_s=60.0,
                             session_id=None):
    """Forward one Responses request natively.

    ``stream:false`` (or missing ``stream``) returns ``(status, data)``: the
    upstream 200 JSON verbatim. ``stream:true`` returns
    ``(status, payload, True)`` where ``payload`` is a bytes generator
    yielding the upstream SSE byte stream verbatim (``resp.json()`` is never
    called on that path). Non-stream callers keep unpacking two values, so
    existing ``status, data = ...`` call sites are unaffected.

    ``request_body`` is sent verbatim as the upstream JSON payload, except
    that a known public model alias is first rewritten in place to the
    native model (see ``_normalize_model``); unknown ``model`` values pass
    through verbatim and non-dict bodies are never touched. The upstream
    200 JSON is returned verbatim as ``data`` -- no field is renamed,
    dropped, or synthesized in either direction. Any non-200 status,
    network fault, timeout, or non-JSON reply raises the mapped
    ``InterfaceError`` (see module docstring). ``api_key`` and
    ``request_body`` never enter logs or error messages; alias rewriting is
    logged only as the ``model_normalized`` boolean.
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
    model_normalized = _normalize_model(request_body)
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
    if _is_stream_request(request_body):
        return _forward_stream_request(
            url, headers, request_body, timeout_obj,
            session_header_present, session_header_injected,
            model_normalized,
        )
    try:
        with httpx.Client(timeout=timeout_obj) as client:
            resp = client.post(url, headers=headers, json=request_body)
    except InterfaceError:
        raise
    except httpx.TimeoutException:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s model_normalized=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected, model_normalized)
        _fail_closed()
    except Exception:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s model_normalized=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected, model_normalized)
        _fail_closed()
    if resp.status_code != 200:
        _LOGGER.info(
            "ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s model_normalized=%s",
            RESPONSES_PRESET_NAME,
            resp.status_code,
            session_header_present,
            session_header_injected,
            model_normalized,
        )
        _raise_for_status(resp.status_code, _upstream_body_text(resp))
    try:
        data = resp.json()
    except InterfaceError:
        raise
    except Exception:
        _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s model_normalized=%s", RESPONSES_PRESET_NAME, 500, session_header_present, session_header_injected, model_normalized)
        _fail_closed()
    _LOGGER.info("ext provider=opencode-go preset=%s status=%s session_header_present=%s session_header_injected=%s model_normalized=%s", RESPONSES_PRESET_NAME, 200, session_header_present, session_header_injected, model_normalized)
    return (200, data)
