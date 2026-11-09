"""Parse + classify chat completions subset. Strict allowlist, no LLM."""

import json
import math
from collections.abc import Mapping
from .schema import ContentSegment
from .schema import InterfaceError
from .schema import Message
from .schema import RequestInput
from .schema import Source
from .schema import contains_surrogate
from .schema import is_valid_public_model_id


_TOP = ("model", "messages", "stream", "temperature", "max_tokens", "n")
_MSG_KEYS = ("role", "content", "name")
_ROLES = ("system", "developer", "user", "assistant")


def _bad(status, code, param, message):
    raise InterfaceError(status, code, param, message)


def _scan_surrogate(obj) -> bool:
    if isinstance(obj, str):
        return contains_surrogate(obj)
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            if isinstance(k, str) and contains_surrogate(k):
                return True
            if _scan_surrogate(v):
                return True
        return False
    if isinstance(obj, list):
        for v in obj:
            if _scan_surrogate(v):
                return True
        return False
    return False


def parse_request(raw: Mapping, route) -> RequestInput:
    if not isinstance(raw, Mapping):
        _bad(400, "invalid_request", None, "Invalid request.")
    for k in raw.keys():
        if k not in _TOP:
            _bad(400, "unsupported_parameter", "request", "Unsupported parameter.")
    if "model" not in raw or "messages" not in raw:
        _bad(400, "invalid_request", "model" if "model" not in raw else "messages", "Invalid request.")
    model = raw["model"]
    if not isinstance(model, str) or not is_valid_public_model_id(model):
        _bad(400, "invalid_request", "model", "Invalid request.")
    if model != route.public_model_id:
        _bad(400, "model_mismatch", "model", "Route and model mismatch.")
    msgs = raw["messages"]
    if not isinstance(msgs, list):
        _bad(400, "invalid_request", "messages", "Invalid request.")
    if len(msgs) == 0:
        _bad(400, "invalid_request", "messages", "Invalid request.")
    if len(msgs) > 128:
        _bad(413, "request_too_large", "messages", "Request is too large.")
    try:
        canon = json.dumps(raw, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except Exception:
        _bad(400, "invalid_request", None, "Invalid request.")
    if len(canon) > 262144:
        _bad(413, "request_too_large", None, "Request is too large.")
    if _scan_surrogate(raw):
        _bad(400, "invalid_request", None, "Invalid request.")
    out = []
    total = 0
    for m in msgs:
        if not isinstance(m, Mapping):
            _bad(400, "invalid_request", "messages", "Invalid request.")
        for k in m.keys():
            if k not in _MSG_KEYS:
                _bad(400, "unsupported_parameter", "messages", "Unsupported parameter.")
        if "role" not in m or "content" not in m:
            _bad(400, "invalid_request", "messages", "Invalid request.")
        role = m["role"]
        content = m["content"]
        name = m.get("name", None)
        if not isinstance(role, str) or role not in _ROLES:
            _bad(400, "unsupported_role", "role", "Unsupported role.")
        if not isinstance(content, str):
            _bad(400, "invalid_request", "content", "Invalid request.")
        if contains_surrogate(content):
            _bad(400, "invalid_request", "content", "Invalid request.")
        if name is not None:
            if not isinstance(name, str):
                _bad(400, "invalid_request", "name", "Invalid request.")
            if len(name) > 64 or contains_surrogate(name):
                _bad(400, "invalid_request", "name", "Invalid request.")
        try:
            nb = len(content.encode("utf-8"))
        except Exception:
            _bad(400, "invalid_request", "content", "Invalid request.")
        if nb > 32768:
            _bad(413, "request_too_large", "content", "Request is too large.")
        total += nb
        if total > 131072:
            _bad(413, "request_too_large", "messages", "Request is too large.")
        out.append(Message(role=role, content=content, name=name))
    if out[-1].role != "user":
        _bad(400, "missing_current_user", None, "Current user message is missing.")
    if out[-1].content.strip() == "":
        _bad(400, "invalid_request", "messages", "Invalid request.")
    if "stream" in raw:
        st = raw["stream"]
        if type(st) is not bool:
            _bad(400, "invalid_request", "stream", "Invalid request.")
        if st is True:
            _bad(400, "unsupported_feature", "stream", "This feature is not supported by this interface version.")
        stream = False
    else:
        stream = False
    if "temperature" in raw:
        t = raw["temperature"]
        if type(t) is bool or not isinstance(t, (int, float)):
            _bad(400, "invalid_request", "temperature", "Invalid request.")
        try:
            ok = math.isfinite(float(t))
        except Exception:
            _bad(400, "invalid_request", "temperature", "Invalid request.")
        if not ok or float(t) < 0 or float(t) > 2:
            _bad(400, "invalid_request", "temperature", "Invalid request.")
        temp = float(t)
    else:
        temp = 0.5
    if "max_tokens" in raw:
        mt = raw["max_tokens"]
        if type(mt) is not int or mt < 1 or mt > 2048:
            _bad(400, "invalid_request", "max_tokens", "Invalid request.")
        max_tok = mt
    else:
        max_tok = 512
    if "n" in raw:
        n = raw["n"]
        if type(n) is not int or n != 1:
            _bad(400, "invalid_request", "n", "Invalid request.")
    else:
        n = 1
    return RequestInput(model=model, messages=tuple(out), stream=stream, temperature=temp, max_tokens=max_tok, n=n)


def classify_messages(request: RequestInput):
    msgs = request.messages
    if len(msgs) == 0:
        _bad(400, "missing_current_user", None, "Current user message is missing.")
    if msgs[-1].role != "user":
        _bad(400, "missing_current_user", None, "Current user message is missing.")
    segs = []
    last = len(msgs) - 1
    for i, m in enumerate(msgs):
        if i == last:
            src = Source.CURRENT_USER
        elif m.role in ("system", "developer"):
            src = Source.EXTERNAL_INSTRUCTION
        else:
            src = Source.EXTERNAL_HISTORY
        segs.append(ContentSegment(role=m.role, content=m.content, name=m.name, source=src, memory_candidate=False))
    return tuple(segs)
