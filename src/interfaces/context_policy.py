"""Admit one request: auth -> route -> body -> headers -> policy. No state, no IO."""

import hashlib
import json
from collections.abc import Mapping
from .config import resolve_route
from .identity import authenticate
from .identity import authorize_route
from .classification import classify_messages
from .classification import parse_request
from .schema import AdmissionDecision
from .schema import ContentSegment
from .schema import GenerationLimits
from .schema import InterfaceError
from .schema import MemoryPolicy
from .schema import Purpose
from .schema import RequestContext
from .schema import Scope
from .schema import contains_surrogate
from .schema import is_valid_id


_C = "x-soul-conversation-id"
_B = "x-soul-branch-id"
_T = "x-soul-turn-id"
_K = "idempotency-key"


def _bad(status, code, param, message):
    raise InterfaceError(status, code, param, message)


def _norm_headers(headers: Mapping):
    if not isinstance(headers, Mapping):
        _bad(400, "invalid_request", None, "Invalid request.")
    seen = {}
    for k, v in headers.items():
        if not isinstance(k, str) or not isinstance(v, str):
            _bad(400, "invalid_request", None, "Invalid request.")
        low = k.lower()
        if low in seen:
            _bad(400, "invalid_request", "headers", "Invalid request.")
        seen[low] = v
    for low in seen.keys():
        if low.startswith("x-soul-") and low not in (_C, _B, _T):
            _bad(400, "unsupported_parameter", "headers", "Unsupported parameter.")
    conv = seen.get(_C, None)
    branch = seen.get(_B, "root")
    turn = seen.get(_T, None)
    key = seen.get(_K, None)
    if conv is not None and not is_valid_id(conv):
        _bad(400, "invalid_request", _C, "Invalid request.")
    if not is_valid_id(branch):
        _bad(400, "invalid_request", _B, "Invalid request.")
    if turn is not None and not is_valid_id(turn):
        _bad(400, "invalid_request", _T, "Invalid request.")
    if key is not None and not is_valid_id(key):
        _bad(400, "invalid_request", _K, "Invalid request.")
    return conv, branch, turn, key


def admit(registry, bearer_token: str, soul_alias: str, backend_alias: str, raw_request: Mapping, headers: Mapping, request_id: str) -> AdmissionDecision:
    client = authenticate(bearer_token, registry)
    if not isinstance(request_id, str) or len(request_id) < 1 or len(request_id) > 128:
        _bad(400, "invalid_request", "request_id", "Invalid request.")
    if request_id.strip() == "":
        _bad(400, "invalid_request", "request_id", "Invalid request.")
    if contains_surrogate(request_id):
        _bad(400, "invalid_request", "request_id", "Invalid request.")
    for ch in request_id:
        o = ord(ch)
        if o < 32 or o == 127:
            _bad(400, "invalid_request", "request_id", "Invalid request.")
    route = resolve_route(registry, soul_alias, backend_alias)
    authorize_route(client, route)
    req = parse_request(raw_request, route)
    conv, branch, turn, key = _norm_headers(headers)
    base = classify_messages(req)
    purpose = client.profile.purpose
    scope = client.profile.scope
    if purpose == Purpose.UTILITY:
        policy = MemoryPolicy.UTILITY_NO_MEMORY
        segs = base
    else:
        if scope != Scope.OWNER_PRIVATE:
            policy = MemoryPolicy.EPHEMERAL
        elif conv is None or turn is None or key is None:
            policy = MemoryPolicy.EPHEMERAL
        elif client.profile.allow_persistent_memory is not True:
            policy = MemoryPolicy.EPHEMERAL
        else:
            policy = MemoryPolicy.INTERACTIVE_CANDIDATE
        if policy == MemoryPolicy.INTERACTIVE_CANDIDATE:
            tmp = list(base)
            last = tmp[-1]
            tmp[-1] = ContentSegment(role=last.role, content=last.content, name=last.name, source=last.source, memory_candidate=True)
            segs = tuple(tmp)
        else:
            segs = base
    ctx = RequestContext(request_id=request_id, profile=client.profile, route=route, conversation_id=conv, branch_id=branch, turn_id=turn, idempotency_key=key)
    lim = GenerationLimits(temperature=req.temperature, max_tokens=req.max_tokens, context_window_tokens=route.context_window_tokens, provider_attempts=1, tool_rounds=0)
    return AdmissionDecision(context=ctx, segments=segs, current_user_text=segs[-1].content, memory_policy=policy, generation_limits=lim)


def idempotency_namespace(context) -> tuple:
    sc = context.profile.scope
    scv = sc.value if isinstance(sc, Scope) else sc
    return (context.profile.principal_id, context.route.agent_id, scv, context.conversation_id, context.branch_id, context.idempotency_key)


def _ev(v):
    if isinstance(v, Scope) or isinstance(v, Purpose) or isinstance(v, MemoryPolicy):
        return v.value
    try:
        from .schema import Source as _S
        if isinstance(v, _S):
            return v.value
    except Exception:
        pass
    return v


def request_fingerprint(decision: AdmissionDecision) -> str:
    ctx = decision.context
    route = ctx.route
    prof = ctx.profile
    lim = decision.generation_limits
    segs = []
    for s in decision.segments:
        segs.append({"content": s.content, "memory_candidate": s.memory_candidate, "name": s.name, "role": s.role, "source": _ev(s.source)})
    payload = {
        "agent_id": route.agent_id,
        "backend_alias": route.backend_alias,
        "backend_ref": route.backend_ref,
        "branch_id": ctx.branch_id,
        "conversation_id": ctx.conversation_id,
        "generation": {"context_window_tokens": lim.context_window_tokens, "max_tokens": lim.max_tokens, "provider_attempts": lim.provider_attempts, "temperature": lim.temperature, "tool_rounds": lim.tool_rounds},
        "idempotency_key": ctx.idempotency_key,
        "memory_policy": _ev(decision.memory_policy),
        "principal_id": prof.principal_id,
        "public_model_id": route.public_model_id,
        "purpose": _ev(prof.purpose),
        "scope": _ev(prof.scope),
        "segments": segs,
        "soul_alias": route.soul_alias,
        "turn_id": ctx.turn_id,
    }
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()
