"""OpenAI <-> internal chat-completions shim. Pure functions only.

No HTTP, no sockets, no files, no network, no LLM calls. This module only
translates shapes:

  OpenAI dict -> parse_chat_request -> InternalReq -> to_raw_request
      -> serve_external(..., executor) -> to_chat_response / to_error

S2 resolution (frozen for S3): the first batch is non-streaming, so
``stream: true`` is always rejected with 400. SSE stays a later increment.

``stop`` is accepted for OpenAI shape compatibility but is NOT forwarded to
the engine: ``admit`` / ``parse_request`` have no stop-sequence semantics.
Accepting-and-dropping is documented here (not silent: see InternalReq.stop
and to_raw_request).
"""

from __future__ import annotations

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
_TOP_KEYS = frozenset({"model", "messages", "temperature", "max_tokens", "stop", "stream"})

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
        semantics. ``stream`` is always False here (True is rejected at
        parse time), rendered explicitly for the engine's allowlist.
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
                _bad("messages", "Unsupported parameter.")
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


def _check_stop(value) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and len(value) > 0 and all(isinstance(s, str) for s in value):
        return tuple(value)
    _bad("stop")


def parse_chat_request(d: Mapping) -> InternalReq:
    """Parse + strictly validate an OpenAI chat-completions body.

    Raises InterfaceError(400, "invalid_request_error", ...) on: non-mapping
    input, any key outside the allowlist (banned extensions included),
    bad role, bad types, or ``stream: true`` (S2: first batch is non-stream).
    """
    if not isinstance(d, Mapping):
        _bad(None)
    for k in d.keys():
        if k in _BANNED_KEYS:
            _bad(k, "Unsupported parameter.")
        if k not in _TOP_KEYS:
            _bad(k, "Unsupported parameter." if isinstance(k, str) else "Invalid request.")
    if "model" not in d or "messages" not in d:
        _bad("model" if "model" not in d else "messages")
    if "stream" in d:
        st = d["stream"]
        if type(st) is not bool:
            _bad("stream")
        if st is True:
            _bad("stream", "Streaming is not supported by this interface version.")
    model = _check_model(d["model"])
    messages = _check_messages(d["messages"])
    temperature = _check_temperature(d["temperature"]) if "temperature" in d else 0.5
    max_tokens = _check_max_tokens(d["max_tokens"]) if "max_tokens" in d else 512
    stop = _check_stop(d["stop"]) if "stop" in d else None
    return InternalReq(
        model=model,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
        stop=stop,
        stream=False,
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
