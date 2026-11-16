"""OpenAI <-> internal chat-completions shim. Pure functions only.

No HTTP, no sockets, no files, no network, no LLM calls. This module only
translates shapes:

  OpenAI dict -> parse_chat_request -> InternalReq -> to_raw_request
      -> serve_external(..., executor) -> to_chat_response / to_error

Transparent passthrough (SHIM-OVERLAY-TRANS-1): the Harness / LLM upstream
is the authority on extra parameters. The shim only guards the routing
minimum -- ``model`` (non-empty string, public model id) and ``messages``
(non-empty array, at most 128 entries) plus the engine-local knobs it owns
(``temperature`` / ``max_tokens`` / ``stream`` / ``stop``). Every other
top-level key (``tools``, ``tool_choice``, ``functions``, ``function_call``,
``logit_bias``, ``store``, ``stream_options``, ``max_completion_tokens`` and
any future unknown key) is NEVER a 400: it rides verbatim in
``InternalReq.passthrough`` and is unpacked back into the payload by
``to_raw_request`` / the backend at send time, key names and values
byte-identical.

Message layer (SHIM-OVERLAY-TRANS-2): the message array is a structural
floor guard plus transparent passthrough. Each entry must be a mapping with
a non-empty string ``role`` and a present ``content`` key whose value is
``None``, ``str``, or ``list`` (parts arrays ride through). Roles are open:
system / developer / user / assistant / tool / function and any unknown
role are admitted. Every other message key (``tool_calls``,
``tool_call_id``, ``function_call``, ``id``, ``name``, ...) rides verbatim
in ``ChatMessage.extras`` and is restored by ``to_raw_request`` /
``_wire_messages`` without loss. A null or empty-string content is admitted
only with a tool payload (tool-only turn); bare empty content without tools
stays fail-closed 500. DoS guard is total-only: the canonical JSON of the
whole messages array is capped (same 256 KiB figure as the engine-side total
cap); there is deliberately no per-message cap so parts arrays pass.

S3 pseudo-streaming: ``stream: true`` is accepted. The upstream call
stays non-streaming (``to_raw_request`` always renders ``stream: False``);
the HTTP layer (``app.py``) synthesizes standard OpenAI SSE chunks from
the full reply text (role delta, full-content delta, stop chunk,
``data: [DONE]``) via ``to_chat_sse_chunks`` / ``iter_chat_sse``.
Upstream tool payloads (``tool_calls`` / ``function_call`` / ``usage``),
when present, are carried through the same builders verbatim -- present
means forwarded, absent means omitted, never synthesized, never forged.

``stop`` is accepted for OpenAI shape compatibility but is NOT forwarded:
``InternalReq.stop`` is carried (not rendered by ``to_raw_request``) --
the engine has no stop-sequence semantics.
"""

from __future__ import annotations

import copy
import json
import math
import re
import time
import uuid
from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field

from src.interfaces.schema import AdmissionDecision
from src.interfaces.schema import InterfaceError
from src.interfaces.schema import is_valid_public_model_id


_CODE = "invalid_request_error"

# Engine-local knobs parsed into typed fields. Everything else on the
# request body rides verbatim in ``InternalReq.passthrough`` (never a 400).
_PARSED_KEYS = frozenset({"model", "messages", "temperature", "max_tokens", "stop", "stream"})

_MSG_MAX_COUNT = 128

#: Total-only DoS guard for the message array: canonical JSON bytes of the
#: whole ``messages`` array. Mirrors the engine-side total-cap figure; there
#: is deliberately no per-message cap so parts arrays pass.
_MSG_MAX_JSON_BYTES = 262144

# Upstream failures are never echoed: every 5xx becomes this fixed copy.
_FIXED_UPSTREAM_MESSAGE = "Internal server error."

# Best-effort scrub so a leaked credential / upstream fragment never rides
# along in an error message. Case-insensitive on the label side.
_SCRUB_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9\-_]+"),
    re.compile(r"(?i)(api[_-]?key|secret|bearer)\s*[:=]\s*\S+"),
)


def _bad(param, message="Invalid request."):
    raise InterfaceError(400, _CODE, param, message)


def _scrub(message: str) -> str:
    text = message if isinstance(message, str) else str(message)
    for pat in _SCRUB_PATTERNS:
        text = pat.sub("[redacted]", text)
    return text


@dataclass(frozen=True)
class ChatMessage:
    """One admitted message: role + content floor plus verbatim extras.

    ``role`` is any non-empty string (unknown roles admitted).
    ``content`` is ``None`` (tool-only turn), ``str``, or ``list`` (parts
    array). ``extras`` holds every other message key verbatim
    (``tool_calls`` / ``tool_call_id`` / ``function_call`` / ``id`` /
    ``name`` / ...), snapshot-copied at parse time.
    """

    role: str
    content: str | list | None
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InternalReq:
    """Normalized chat request. ``stop`` is carried (not forwarded).

    ``passthrough`` holds every top-level body key outside the parsed
    engine-local knobs, verbatim (key names and values untouched). The
    backend unpacks it into the upstream payload as-is.
    """

    model: str
    messages: tuple[ChatMessage, ...]
    temperature: float = 0.5
    max_tokens: int = 512
    stop: tuple[str, ...] | None = None
    stream: bool = False
    passthrough: dict = field(default_factory=dict)

    def to_raw_request(self) -> dict:
        """Render the Mapping shape ``admit`` / ``parse_request`` accepts.

        ``stop`` is intentionally dropped: the engine has no stop-sequence
        semantics. ``stream`` is always False here, rendered explicitly.
        ``passthrough`` keys are unpacked verbatim on top (they are disjoint
        from the parsed knobs by construction, so nothing is overwritten).
        Message extras ride under their original keys: each message renders
        as ``{"role", "content", **extras}``, byte-identical to admission.
        """
        msgs = []
        for m in self.messages:
            item: dict = {"role": m.role, "content": m.content}
            item.update(dict(m.extras))
            msgs.append(item)
        raw = {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        raw.update(dict(self.passthrough))
        return raw


def _check_model(value, param="model") -> str:
    if not isinstance(value, str) or not is_valid_public_model_id(value):
        _bad(param)
    return value


def _check_messages(value) -> tuple[ChatMessage, ...]:
    """Structural floor guard + transparent passthrough (TRANS-2).

    Admits: non-empty list, at most 128 entries, whole-array canonical JSON
    within the total byte cap; each entry a mapping with a non-empty string
    ``role`` (any role, known or unknown) and a present ``content`` key
    (``None`` / ``str`` / ``list``). Every other message key rides verbatim
    in ``ChatMessage.extras`` -- never a 400. Null / empty-string content is
    admitted only with a tool payload (``tool_calls`` / ``function_call``);
    bare empty content without tools stays fail-closed 500.
    """
    if not isinstance(value, list) or len(value) == 0 or len(value) > _MSG_MAX_COUNT:
        _bad("messages")
    try:
        canon = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            default=str,
        )
    except Exception:
        _bad("messages")
    if len(canon.encode("utf-8")) > _MSG_MAX_JSON_BYTES:
        _bad("messages")
    out = []
    for m in value:
        if not isinstance(m, Mapping):
            _bad("messages")
        for k in m.keys():
            if not isinstance(k, str):
                _bad("messages", "Invalid request.")
        if "role" not in m or "content" not in m:
            _bad("messages")
        role = m["role"]
        content = m["content"]
        if not isinstance(role, str) or role == "":
            _bad("role")
        if not (content is None or isinstance(content, (str, list))):
            _bad("content")
        try:
            extras = copy.deepcopy({k: v for k, v in m.items() if k not in ("role", "content")})
        except Exception:
            extras = {k: v for k, v in m.items() if k not in ("role", "content")}
        if content is None or content == "":
            if extras.get("tool_calls") is None and extras.get("function_call") is None:
                raise InterfaceError(500, "internal", None, "Internal server error.")
        out.append(ChatMessage(role=role, content=content, extras=extras))
    return tuple(out)


def _check_temperature(value) -> float:
    if type(value) is bool or not isinstance(value, (int, float)):
        _bad("temperature")
    try:
        ok = math.isfinite(float(value))
    except Exception:
        _bad("temperature")
    if not ok or float(value) < 0 or float(value) > 2:
        _bad("temperature")
    return float(value)


def _check_max_tokens(value) -> int:
    if type(value) is not int or value < 1 or value > 2048:
        _bad("max_tokens")
    return value


def _check_stop(value) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and len(value) > 0 and all(isinstance(s, str) for s in value):
        return tuple(value)
    _bad("stop")


def parse_chat_request(d: Mapping) -> InternalReq:
    """Parse an OpenAI chat-completions body with transparent passthrough.

    Raises InterfaceError(400, "invalid_request_error", ...) only on: a
    non-mapping input, a missing/invalid ``model`` or ``messages``, bad
    ``temperature`` / ``max_tokens`` / ``stop`` types, or a non-bool
    ``stream``. ``stream: true`` is accepted (S3 pseudo-streaming: upstream
    stays non-streaming, the HTTP layer synthesizes SSE). Message entries
    admit any non-empty role and ``None`` / ``str`` / ``list`` content with
    all other keys riding verbatim in ``ChatMessage.extras``; bare empty
    content without a tool payload is the one fail-closed 500.

    Every other top-level key -- ``tools``, ``tool_choice``,
    ``functions``, ``function_call``, ``logit_bias``, ``store``,
    ``stream_options``, ``max_completion_tokens`` and any future unknown
    key -- is NEVER a 400: it is stored verbatim in
    ``InternalReq.passthrough`` (a snapshot copy) and re-emitted by
    ``to_raw_request`` / the backend unpacked into the upstream payload.
    Non-string top-level keys stay 400 (no repr leak).
    """
    if not isinstance(d, Mapping):
        _bad(None)
    for k in d.keys():
        if not isinstance(k, str):
            _bad(k, "Invalid request.")
    if "model" not in d or "messages" not in d:
        _bad("model" if "model" not in d else "messages")
    stream = False
    if "stream" in d:
        st = d["stream"]
        if type(st) is not bool:
            _bad("stream")
        stream = st
    model = _check_model(d["model"])
    messages = _check_messages(d["messages"])
    temperature = _check_temperature(d["temperature"]) if "temperature" in d else 0.5
    max_tokens = _check_max_tokens(d["max_tokens"]) if "max_tokens" in d else 512
    stop = _check_stop(d["stop"]) if "stop" in d else None
    extra = {k: d[k] for k in d.keys() if k not in _PARSED_KEYS}
    try:
        # Snapshot: later mutation of the caller's body must not move the
        # request already admitted. Exotic non-copyable values fall back
        # to a shallow copy (still verbatim, still never a 400).
        passthrough = copy.deepcopy(extra)
    except Exception:
        passthrough = dict(extra)
    return InternalReq(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        stream=stream,
        passthrough=passthrough,
    )


def _passthrough_message_extra(message_extra) -> dict:
    """Copy the forwardable upstream tool keys verbatim, nothing else.

    Only ``tool_calls`` / ``function_call`` ride through (present means
    forwarded, absent means omitted). Anything else in the mapping is
    dropped -- never synthesized, never forged.
    """
    extra = {}
    if isinstance(message_extra, Mapping):
        for k in ("tool_calls", "function_call"):
            if k in message_extra and message_extra[k] is not None:
                extra[k] = message_extra[k]
    return extra


def to_chat_response(text: str, model: str, *, message_extra=None, usage=None) -> dict:
    """Build an OpenAI-shaped ``chat.completion`` dict for one assistant reply.

    Upstream tool payloads ride through verbatim: ``message_extra`` may
    carry ``tool_calls`` / ``function_call`` (merged into the message) and
    ``usage`` (a Mapping) replaces the default zero-usage. Absent means
    omitted -- nothing is synthesized or forged.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(model, str):
        raise TypeError("model must be str")
    message: dict = {"role": "assistant", "content": text}
    message.update(_passthrough_message_extra(message_extra))
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "stop",
            }
        ],
        # 未接計費，不偽造 token 數：上游無 usage 時誠實填 0；上游有則原樣透傳。
        "usage": dict(usage)
        if isinstance(usage, Mapping)
        else {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def to_chat_sse_chunks(text: str, model: str, *, message_extra=None, usage=None) -> tuple:
    """Build the three OpenAI-shaped ``chat.completion.chunk`` payloads.

    Order: role delta, full-content delta, stop chunk. All three share one
    ``id`` (``chatcmpl-`` + uuid4 hex, same rule as ``to_chat_response``),
    one ``created`` (``int(time.time())``), and the caller's ``model``.
    The full reply rides in a single content delta: upstream is
    non-streaming, so there is nothing to incrementally tokenize.
    ``message_extra`` (``tool_calls`` / ``function_call``) rides verbatim
    inside the content delta when present. When ``usage`` (a Mapping) is
    given, a fourth usage chunk is appended (``choices: []``); otherwise
    the return stays a 3-tuple and no usage chunk is synthesized.
    """
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(model, str):
        raise TypeError("model must be str")
    cid = "chatcmpl-" + uuid.uuid4().hex
    created = int(time.time())
    base = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model}
    role_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
    }
    content_delta: dict = {"content": text}
    content_delta.update(_passthrough_message_extra(message_extra))
    content_chunk = {
        **base,
        "choices": [{"index": 0, "delta": content_delta, "finish_reason": None}],
    }
    stop_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    if isinstance(usage, Mapping):
        usage_chunk = {**base, "choices": [], "usage": dict(usage)}
        return (role_chunk, content_chunk, stop_chunk, usage_chunk)
    return (role_chunk, content_chunk, stop_chunk)


def iter_chat_sse(text: str, model: str, *, message_extra=None, usage=None):
    """Yield SSE ``data:`` frames (bytes) for one full reply plus ``[DONE]``.

    Pure bytes formatting only: no HTTP, no sockets, no files. The caller
    (``app.py``) wraps this iterator in a ``StreamingResponse`` with
    ``media_type="text/event-stream"``. Upstream tool payloads and usage
    ride through exactly as ``to_chat_sse_chunks`` renders them.
    """
    for payload in to_chat_sse_chunks(text, model, message_extra=message_extra, usage=usage):
        yield ("data: " + json.dumps(payload, ensure_ascii=False) + "\n\n").encode("utf-8")
    yield b"data: [DONE]\n\n"


_TYPE_BY_STATUS = {
    400: "invalid_request_error",
    401: "invalid_client",
    403: "permission_denied",
    429: "rate_limit_error",
}


def _type_for_status(status: int) -> str:
    if isinstance(status, int) and status >= 500:
        return "server_error"
    return _TYPE_BY_STATUS.get(status, "invalid_request_error")


def to_error(status: int, code: str, message: str) -> dict:
    """Build an OpenAI-shaped error envelope.

    ``type`` is derived from ``status`` (401->invalid_client,
    403->permission_denied, 400->invalid_request_error,
    429->rate_limit_error, 5xx->server_error). 5xx messages are always
    replaced with fixed copy so upstream text never leaks; all other
    messages pass through the credential scrubber.
    """
    if not isinstance(status, int):
        raise TypeError("status must be int")
    if not isinstance(code, str):
        raise TypeError("code must be str")
    if isinstance(status, int) and status >= 500:
        safe = _FIXED_UPSTREAM_MESSAGE
    else:
        safe = _scrub(message)
    return {"error": {"message": safe, "type": _type_for_status(status), "code": code}}


def make_executor(model: str, generate: Callable[[AdmissionDecision], str]):
    """Build a ``serve_external``-compatible executor.

    The returned callable takes exactly one positional argument -- the
    admitted ``AdmissionDecision`` -- and returns the final OpenAI-shaped
    response dict, matching the ``executor`` callback signature of
    ``serve_external(provider, raw_headers, soul_alias, backend_alias,
    raw_request, request_id, executor, ...)``.
    """
    if not isinstance(model, str):
        raise TypeError("model must be str")
    if not callable(generate):
        raise TypeError("generate must be callable")

    def executor(decision: AdmissionDecision) -> dict:
        return to_chat_response(generate(decision), model)

    return executor
