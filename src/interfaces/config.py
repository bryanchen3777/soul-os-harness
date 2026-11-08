"""Validate registry mapping into immutable Registry. No IO."""

from collections.abc import Mapping
from .schema import ClientProfile
from .schema import CredentialBinding
from .schema import InterfaceError
from .schema import Purpose
from .schema import Registry
from .schema import RouteBinding
from .schema import Scope
from .schema import is_valid_id
from .schema import is_valid_public_model_id


_TOP_KEYS = ("owner_principal_id", "backends", "routes", "clients")
_ROUTE_KEYS = ("soul_alias", "backend_alias", "agent_id", "public_model_id", "backend_ref", "context_window_tokens")
_CLIENT_KEYS = ("client_id", "principal_id", "purpose", "allowed_routes", "scope", "allow_persistent_memory", "credential_sha256")
_HEX = "0123456789abcdefABCDEF"
_HEX_LOWER = "0123456789abcdef"


def _fail(param, message="Invalid registry configuration."):
    raise InterfaceError(500, "invalid_registry", param, message)


def _norm_digest(v):
    if not isinstance(v, str):
        return None
    if len(v) != 64:
        return None
    for ch in v:
        if ch not in _HEX:
            return None
    low = v.lower()
    for ch in low:
        if ch not in _HEX_LOWER:
            return None
    return low


def validate_registry(raw: Mapping) -> Registry:
    if not isinstance(raw, Mapping):
        _fail("registry")
    keys = list(raw.keys())
    if len(keys) != 4:
        _fail("registry")
    for k in _TOP_KEYS:
        if k not in raw:
            _fail(k)
    for k in keys:
        if k not in _TOP_KEYS:
            _fail("registry")
    owner = raw["owner_principal_id"]
    if not is_valid_id(owner):
        _fail("owner_principal_id")
    backends_raw = raw["backends"]
    if not isinstance(backends_raw, list):
        _fail("backends")
    if len(backends_raw) == 0:
        _fail("backends")
    seen_be = []
    for b in backends_raw:
        if not is_valid_id(b):
            _fail("backends")
        if b in seen_be:
            _fail("backends")
        seen_be.append(b)
    be_set = set(seen_be)
    routes_raw = raw["routes"]
    if not isinstance(routes_raw, list) or len(routes_raw) == 0:
        _fail("routes")
    routes = []
    route_keys = []
    for r in routes_raw:
        if not isinstance(r, Mapping):
            _fail("routes")
        rk = list(r.keys())
        if len(rk) != 6:
            _fail("routes")
        for k in _ROUTE_KEYS:
            if k not in r:
                _fail("routes")
        for k in rk:
            if k not in _ROUTE_KEYS:
                _fail("registry")
        sa = r["soul_alias"]
        ba = r["backend_alias"]
        ag = r["agent_id"]
        br = r["backend_ref"]
        pm = r["public_model_id"]
        cw = r["context_window_tokens"]
        if not is_valid_id(sa):
            _fail("soul_alias")
        if not is_valid_id(ba):
            _fail("backend_alias")
        if not is_valid_id(ag):
            _fail("agent_id")
        if not is_valid_id(br):
            _fail("backend_ref")
        if not is_valid_public_model_id(pm):
            _fail("public_model_id")
        if type(cw) is not int or cw <= 0:
            _fail("context_window_tokens")
        if br not in be_set:
            _fail("backend_ref")
        key = (sa, ba)
        if key in route_keys:
            _fail("routes")
        route_keys.append(key)
        routes.append(RouteBinding(soul_alias=sa, backend_alias=ba, agent_id=ag, public_model_id=pm, backend_ref=br, context_window_tokens=cw))
    rset = set(route_keys)
    clients_raw = raw["clients"]
    if not isinstance(clients_raw, list) or len(clients_raw) == 0:
        _fail("clients")
    clients = []
    creds = []
    seen_cid = []
    seen_dig = []
    for c in clients_raw:
        if not isinstance(c, Mapping):
            _fail("clients")
        ck = list(c.keys())
        if len(ck) != 7:
            _fail("clients")
        for k in _CLIENT_KEYS:
            if k not in c:
                _fail(k)
        for k in ck:
            if k not in _CLIENT_KEYS:
                _fail("registry")
        cid = c["client_id"]
        pid = c["principal_id"]
        if not is_valid_id(cid):
            _fail("client_id")
        if not is_valid_id(pid):
            _fail("principal_id")
        if pid != owner:
            _fail("principal_id")
        if cid in seen_cid:
            _fail("client_id")
        seen_cid.append(cid)
        pur = c["purpose"]
        sc = c["scope"]
        if pur not in ("interactive", "utility"):
            _fail("purpose")
        if sc not in ("owner_private", "none"):
            _fail("scope")
        purpose = Purpose(pur)
        scope = Scope(sc)
        apm = c["allow_persistent_memory"]
        if type(apm) is not bool:
            _fail("allow_persistent_memory")
        if purpose == Purpose.UTILITY:
            if scope != Scope.NONE or apm is not False:
                _fail("scope")
        else:
            if scope != Scope.OWNER_PRIVATE:
                _fail("scope")
        ar = c["allowed_routes"]
        if not isinstance(ar, list) or len(ar) == 0:
            _fail("allowed_routes")
        seen_ar = []
        for e in ar:
            if not isinstance(e, str):
                _fail("allowed_routes")
            parts = e.split("/")
            if len(parts) != 2 or not is_valid_id(parts[0]) or not is_valid_id(parts[1]):
                _fail("allowed_routes")
            if e in seen_ar:
                _fail("allowed_routes")
            seen_ar.append(e)
            if (parts[0], parts[1]) not in rset:
                _fail("allowed_routes")
        dig = _norm_digest(c["credential_sha256"])
        if dig is None:
            _fail("credential_sha256")
        if dig in seen_dig:
            _fail("credential_sha256")
        seen_dig.append(dig)
        prof = ClientProfile(client_id=cid, principal_id=pid, purpose=purpose, allowed_routes=tuple(seen_ar), scope=scope, allow_persistent_memory=apm)
        clients.append(prof)
        creds.append(CredentialBinding(digest=dig, profile=prof))
    return Registry(routes=tuple(routes), clients=tuple(clients), credentials=tuple(creds), owner_principal_id=owner, backends=tuple(seen_be))


def resolve_route(registry, soul_alias: str, backend_alias: str):
    for r in registry.routes:
        if r.soul_alias == soul_alias and r.backend_alias == backend_alias:
            return r
    raise InterfaceError(404, "route_not_found", "route", "Unknown route.")
