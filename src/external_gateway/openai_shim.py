"""OpenAI <-> internal chat-completions shim. Pure functions only.

No HTTP, no sockets, no files, no network, no LLM calls. This module only
translates shapes:

  OpenAI dict -> parse_chat_request -> InternalReq -> to_raw_request
      -> serve_external(..., executor) -> to_chat_response / to_error

S3 pseudo-streaming: ``stream: true`` is accepted. The upstream call
stays non-streaming (``to_raw_request`` always renders ``stream: False`` for
the engine allowlist); the HTTP layer (``app.py``) synthesizes standard
OpenAI SSE chunks from the full reply text (role delta, full-content delta,
stop chunk, ``data: [DONE]``) via ``to_chat_sse_chunks`` / ``iter_chat_sse``.

``stop`` is accepted for OpenAI shape compatibility but is NOT forwarded to
the engine: ``admit`` / ``parse_request`` have no stop-sequence semantics.
Accepting-and-dropping is documented here (not silent: see InternalReq.stop
and to_raw_request).
"""

from __future__ import annotations

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

# Top-level keys accepted on a chat-completions request body. Anything else --
# including the explicitly banned OpenAI extensions below -- is a 400.
# NOTE (SHIM-ADMIT-STREAM-OPTIONS-001): ``stream_options`` is admitted and
# no-op dropped (never forwarded upstream; no usage chunk is synthesized in
# SSE because upstream is zero-accounting). Never invent private claims in
# SSE chunks.
# NOTE (SHIM-ADMIT-STORE-001): ``store`` is admitted and no-op dropped
# (never forwarded upstream via ``to_raw_request``; no usage chunk is
# synthesized in SSE). Accepted values are None / True / False only.
# NOTE (SHIM-EMPTY-TOOL-ADMIT): ``tools`` / ``tool_choice`` stay in
# ``_BANNED_KEYS`` (not in ``_TOP_KEYS``) but are graded inside the ban
# check: empty ``tools`` ([]) and harmless ``tool_choice`` ("none", or
# "auto" with tools absent/empty) are admitted and no-op dropped (never
# stored on ``InternalReq``, never rendered by ``to_raw_request``).
# Any real tool use (non-empty ``tools``, "required"/dict ``tool_choice``,
# "auto" alongside non-empty ``tools``) stays 400.
# NOTE (SHIM-ADMIT-MAX-COMPLETION-TOKENS-001): 新版長度上限，映射為 max_tokens；
# 與 max_tokens 並存即 400 衝突；不轉上游原名、不合成 usage。
_TOP_KEYS = frozenset({"model", "messages", "temperature", "max_tokens", "max_completion_tokens", "stop", "stream", "stream_options", "store"})

# Accepted but rejected-with-400 extensions (subset of the "extra key" rule,
# listed explicitly so a reader sees the ban without diffing key sets).
_BANNED_KEYS = frozenset({"logit_bias", "function_call", "tools", "functions", "tool_choice"})

_MSG_KEYS = frozenset({"role", "content", "name"})
_ROLES = frozenset({"system", "user", "assistant"})

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
    role: str
    content: str
    name: str | None = None


@dataclass(frozen=True)
class InternalReq:
    """Normalized chat request. ``stop`` is carried (not forwarded)."""

    model: str
    messages: tuple[ChatMessage, ...]
    temperature: float = 0.5
    max_tokens: int = 512
    stop: tuple[str, ...] | None = None
    stream: bool = False

    def to_raw_request(self) -> dict:
        """Render the Mapping shape ``admit`` / ``parse_request`` accepts.

        ``stop`` is intentionally dropped: the engine has no stop-sequence
        semantics. ``stream`` is always False here (the engine allowlist
        admits only non-streaming; ``True`` is served as synthesized SSE at
        the HTTP layer), rendered explicitly for the engine's allowlist.
        ``stream_options`` is admitted-and-dropped upstream of here and never
        rendered.
        """
        msgs = []
        for m in self.messages:
            item: dict = {"role": m.role, "content": m.content}
            if m.name is not None:
                item["name"] = m.name
            msgs.append(item)
        return {
            "model": self.model,
            "messages": msgs,
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


def _check_model(value, param="model") -> str:
    if not isinstance(value, str) or not is_valid_public_model_id(value):
        _bad(param)
    return value


def _check_messages(value) -> tuple[ChatMessage, ...]:
    if not isinstance(value, list) or len(value) == 0 or len(value) > 128:
        _bad("messages")
    out = []
    for m in value:
        if not isinstance(m, Mapping):
            _bad("messages")
        for k in m.keys():
            if k not in _MSG_KEYS:
                if isinstance(k, str):
                    _bad("messages", f"Unsupported message field: {k}")
                else:
                    _bad("messages", "Invalid request.")
        if "role" not in m or "content" not in m:
            _bad("messages")
        role = m["role"]
        content = m["content"]
        name = m.get("name", None)
        if not isinstance(role, str) or role not in _ROLES:
            _bad("role")
        if not isinstance(content, str):
            _bad("content")
        if name is not None and (not isinstance(name, str) or len(name) > 64):
            _bad("name")
        out.append(ChatMessage(role=role, content=content, name=name))
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


def _check_max_completion_tokens(value) -> int | None:
    if value is None:
        return None
    if type(value) is bool:
        _bad("max_completion_tokens")
    if type(value) is not int or value < 1 or value > 2048:
        _bad("max_completion_tokens")
    return value


def _check_stop(value) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and len(value) > 0 and all(isinstance(s, str) for s in value):
        return tuple(value)
    _bad("stop")


def _check_store(value) -> None:
    if value is None or type(value) is bool:
        return None
    _bad("store")


def _check_stream_options(value) -> None:
    """Admit ``stream_options`` and no-op drop it.

    ``None`` or any Mapping is accepted and discarded (not stored, not
    forwarded; ``to_raw_request`` never renders it and no usage chunk is
    synthesized). Anything else is a 400. Content is not validated.
    """
    if value is None or isinstance(value, Mapping):
        return None
    _bad("stream_options")


def parse_chat_request(d: Mapping) -> InternalReq:
    """Parse + strictly validate an OpenAI chat-completions body.

    Raises InterfaceError(400, "invalid_request_error", ...) on: non-mapping
    input, any key outside the allowlist (banned extensions included),
    bad role, bad types, or non-bool ``stream``. ``stream: true`` is
    accepted (S3 pseudo-streaming: upstream stays non-streaming, the HTTP
    layer synthesizes SSE); ``stream`` defaults to False when absent.
    ``stream_options`` (Mapping or None) is admitted and no-op dropped: its
    content is not validated, it is not stored on ``InternalReq``, it never
    appears in ``to_raw_request``, and no usage chunk is appended to SSE
    (upstream is zero-accounting).
    """
    if not isinstance(d, Mapping):
        _bad(None)
    # Order-independent: ``tool_choice == "auto"`` is harmless only when
    # ``tools`` is absent or empty, so grade ``tools`` emptiness BEFORE the
    # per-key ban loop (a single pass in insertion order could otherwise
    # admit ``{"tool_choice": "auto", "tools": [...]}`` by seeing "auto"
    # first).
    _tools_empty = ("tools" not in d) or (isinstance(d["tools"], list) and len(d["tools"]) == 0)
    for k in d.keys():
        if k == "tools":
            v = d[k]
            if not (isinstance(v, list) and len(v) == 0):
                _bad(k, "Unsupported parameter.")
            continue
        if k == "tool_choice":
            v = d[k]
            if v == "none":
                continue
            if v == "auto" and _tools_empty:
                continue
            _bad(k, "Unsupported parameter.")
            continue
        if k in _BANNED_KEYS:
            _bad(k, "Unsupported parameter.")
        if k not in _TOP_KEYS:
            if isinstance(k, str):
                _bad(k, f"Unsupported parameter: {k}")
            else:
                _bad(k, "Invalid request.")
    if "model" not in d or "messages" not in d:
        _bad("model" if "model" not in d else "messages")
    if "stream_options" in d:
        _check_stream_options(d["stream_options"])
    if "store" in d:
        _check_store(d["store"])
    stream = False
    if "stream" in d:
        st = d["stream"]
        if type(st) is not bool:
            _bad("stream")
        stream = st
    model = _check_model(d["model"])
    messages = _check_messages(d["messages"])
    temperature = _check_temperature(d["temperature"]) if "temperature" in d else 0.5
    if "max_tokens" in d and "max_completion_tokens" in d:
        _bad("max_completion_tokens")
    elif "max_completion_tokens" in d:
        v = _check_max_completion_tokens(d["max_completion_tokens"])
        max_tokens = 512 if v is None else v
    else:
        max_tokens = _check_max_tokens(d["max_tokens"]) if "max_tokens" in d else 512
    stop = _check_stop(d["stop"]) if "stop" in d else None
    return InternalReq(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        stream=stream,
    )


def to_chat_response(text: str, model: str) -> dict:
    """Build an OpenAI-shaped ``chat.completion`` dict for one assistant reply."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    if not isinstance(model, str):
        raise TypeError("model must be str")
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        # 未接計費，不偽造 token 數：usage 誠實填 0。
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


def to_chat_sse_chunks(text: str, model: str) -> tuple[dict, dict, dict]:
    """Build the three OpenAI-shaped ``chat.completion.chunk`` payloads.

    Order: role delta, full-content delta, stop chunk. All three share one
    ``id`` (``chatcmpl-`` + uuid4 hex, same rule as ``to_chat_response``),
    one ``created`` (``int(time.time())``), and the caller's ``model``.
    The full reply rides in a single content delta: upstream is
    non-streaming, so there is nothing to incrementally tokenize.
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
    content_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
    }
    stop_chunk = {
        **base,
        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
    }
    return (role_chunk, content_chunk, stop_chunk)


def iter_chat_sse(text: str, model: str):
    """Yield SSE ``data:`` frames (bytes) for one full reply plus ``[DONE]``.

    Pure bytes formatting only: no HTTP, no sockets, no files. The caller
    (``app.py``) wraps this iterator in a ``StreamingResponse`` with
    ``media_type="text/event-stream"``.
    """
    for payload in to_chat_sse_chunks(text, model):
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
