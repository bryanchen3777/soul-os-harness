"""Shared factories + registry boundary tests. No old runtime imports."""

import copy
import hashlib

import pytest

from src.interfaces.config import resolve_route
from src.interfaces.config import validate_registry
from src.interfaces.schema import InterfaceError


OWNER = "owner-01"
TOKEN_A = "token-alpha-001-valid-key"
TOKEN_B = "token-beta-002-valid-key"
TOKEN_U = "token-utility-003-key"


def digest_for(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def make_route_dict(soul="soul-a", backend="backend-a", agent="agent-01", model="model-a", ref="port-a", tokens=4096):
    return {"soul_alias": soul, "backend_alias": backend, "agent_id": agent, "public_model_id": model, "backend_ref": ref, "context_window_tokens": tokens}


def make_client_dict(client_id, token, purpose="interactive", allowed_routes=None, scope=None, allow_mem=None):
    if allowed_routes is None:
        allowed_routes = ["soul-a/backend-a"]
    if purpose == "interactive":
        if scope is None:
            scope = "owner_private"
        if allow_mem is None:
            allow_mem = True
    else:
        if scope is None:
            scope = "none"
        if allow_mem is None:
            allow_mem = False
    return {"client_id": client_id, "principal_id": OWNER, "purpose": purpose, "allowed_routes": list(allowed_routes), "scope": scope, "allow_persistent_memory": allow_mem, "credential_sha256": digest_for(token)}


def make_registry_raw(owner=OWNER, backends=None, routes=None, clients=None):
    if backends is None:
        backends = ["port-a"]
    if routes is None:
        routes = [make_route_dict()]
    if clients is None:
        clients = [make_client_dict("client-a", TOKEN_A)]
    return {"owner_principal_id": owner, "backends": list(backends), "routes": [dict(r) for r in routes], "clients": [dict(c) for c in clients]}


def make_registry(**kw):
    return validate_registry(make_registry_raw(**kw))


def make_dual_registry():
    return validate_registry(make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)]))


def test_valid_registry_shapes():
    reg = make_dual_registry()
    assert isinstance(reg.routes, tuple) and len(reg.routes) == 1
    assert isinstance(reg.clients, tuple) and len(reg.clients) == 2
    assert isinstance(reg.credentials, tuple) and len(reg.credentials) == 2
    assert isinstance(reg.backends, tuple) and reg.backends == ("port-a",)
    assert reg.routes[0].soul_alias == "soul-a"
    assert reg.clients[0].allowed_routes == ("soul-a/backend-a",)


def test_registry_immutable():
    reg = make_dual_registry()
    with pytest.raises(Exception):
        reg.owner_principal_id = "other"
    with pytest.raises(Exception):
        reg.routes[0].soul_alias = "x"
    assert isinstance(reg.routes, tuple)
    assert isinstance(reg.clients[0].allowed_routes, tuple)
    with pytest.raises(AttributeError):
        reg.backends.append("port-b")


def test_digest_normalized_lowercase():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A)])
    raw["clients"][0]["credential_sha256"] = digest_for(TOKEN_A).upper()
    reg = validate_registry(raw)
    assert reg.credentials[0].digest == digest_for(TOKEN_A)
    assert "digest" not in repr(reg.credentials[0]).lower() or "CredentialBinding" in repr(reg.credentials[0])


def test_reject_extra_top_field():
    raw = make_registry_raw()
    raw["extra"] = 1
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_missing_field():
    raw = make_registry_raw()
    del raw["backends"]
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_extra_route_field():
    raw = make_registry_raw()
    raw["routes"][0]["endpoint"] = "https://x"
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_extra_client_field():
    raw = make_registry_raw()
    raw["clients"][0]["raw_token"] = "x"
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_duplicate_route():
    raw = make_registry_raw(routes=[make_route_dict(), make_route_dict()])
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_duplicate_client_id():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-a", TOKEN_B)])
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_duplicate_digest_case_insensitive():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_A)])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw2 = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A)])
    raw2["clients"].append(make_client_dict("client-b", TOKEN_B))
    raw2["clients"][1]["credential_sha256"] = digest_for(TOKEN_A).upper()
    with pytest.raises(InterfaceError):
        validate_registry(raw2)


@pytest.mark.parametrize("bad", ["", "short", "z" * 63, "g" * 64, "a" * 65, 123, None, "X" * 64 + "!", "a/b" * 16])
def test_reject_bad_digest_format(bad):
    raw = make_registry_raw()
    raw["clients"][0]["credential_sha256"] = bad
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_unknown_backend_ref():
    raw = make_registry_raw()
    raw["routes"][0]["backend_ref"] = "port-missing"
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_bad_allowed_routes_format():
    for bad in ["no-slash", "a/b/c", "/b", "a/", "a b/c", "a/../c"]:
        raw = make_registry_raw()
        raw["clients"][0]["allowed_routes"] = [bad]
        with pytest.raises(InterfaceError):
            validate_registry(raw)


def test_reject_allowed_routes_unknown_route():
    raw = make_registry_raw()
    raw["clients"][0]["allowed_routes"] = ["soul-a/backend-missing"]
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_allowed_routes_duplicate_within_client():
    raw = make_registry_raw()
    raw["clients"][0]["allowed_routes"] = ["soul-a/backend-a", "soul-a/backend-a"]
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_backends_duplicate_or_empty():
    raw = make_registry_raw(backends=["port-a", "port-a"])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw2 = make_registry_raw(backends=[])
    with pytest.raises(InterfaceError):
        validate_registry(raw2)


def test_reject_empty_routes_clients():
    with pytest.raises(InterfaceError):
        validate_registry(make_registry_raw(routes=[]))
    with pytest.raises(InterfaceError):
        validate_registry(make_registry_raw(clients=[]))


def test_reject_principal_mismatch():
    raw = make_registry_raw()
    raw["clients"][0]["principal_id"] = "owner-02"
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_utility_scope_or_mem():
    raw = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility", scope="owner_private")])
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    raw2 = make_registry_raw(clients=[make_client_dict("client-u", TOKEN_U, purpose="utility", allow_mem=True)])
    with pytest.raises(InterfaceError):
        validate_registry(raw2)


def test_reject_interactive_scope_none():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A, purpose="interactive", scope="none")])
    with pytest.raises(InterfaceError):
        validate_registry(raw)


@pytest.mark.parametrize("bad", [".", "..", "", "a/b", "a\nb", "a b", "x" * 129, "caf\u00e9", "a\\b"])
def test_reject_bad_ids(bad):
    raw = make_registry_raw(owner=bad if isinstance(bad, str) else OWNER)
    if bad != OWNER:
        with pytest.raises(InterfaceError):
            validate_registry(raw)
    raw2 = make_registry_raw()
    raw2["routes"][0]["soul_alias"] = bad
    with pytest.raises(InterfaceError):
        validate_registry(raw2)
    raw3 = make_registry_raw()
    raw3["clients"][0]["client_id"] = bad
    with pytest.raises(InterfaceError):
        validate_registry(raw3)


def test_reject_public_model_path_or_empty():
    for bad in ["", "   ", "\t", "a/b", "a\\b", ".", "..", "x" * 129, "a\ud800b", "ab\x00cd", "ab\x1fcd"]:
        raw = make_registry_raw()
        raw["routes"][0]["public_model_id"] = bad
        with pytest.raises(InterfaceError):
            validate_registry(raw)


@pytest.mark.parametrize("bad", [True, False, 0, -1, "4096", 4.5, None])
def test_reject_bad_context_window(bad):
    raw = make_registry_raw()
    raw["routes"][0]["context_window_tokens"] = bad
    with pytest.raises(InterfaceError):
        validate_registry(raw)


def test_reject_bool_allow_mem_and_nonmapping():
    raw = make_registry_raw()
    raw["clients"][0]["allow_persistent_memory"] = 1
    with pytest.raises(InterfaceError):
        validate_registry(raw)
    with pytest.raises(InterfaceError):
        validate_registry("not-a-mapping")
    with pytest.raises(InterfaceError):
        validate_registry(make_registry_raw(backends="port-a"))


def test_unknown_registry_field_no_secret_echo():
    secret = "sk-live-SECRET-ABC-123-xyz"
    raw = make_registry_raw()
    raw[secret] = 1
    with pytest.raises(InterfaceError) as ei:
        validate_registry(raw)
    assert secret not in str(ei.value.message)
    assert ei.value.param != secret
    assert secret not in repr(ei.value)
    assert secret not in str(ei.value.param)
    raw2 = make_registry_raw()
    raw2["routes"][0][secret] = "v"
    with pytest.raises(InterfaceError) as ei2:
        validate_registry(raw2)
    assert secret not in str(ei2.value.message)
    assert ei2.value.param != secret
    assert secret not in repr(ei2.value)


def test_resolve_route_ok_and_404_no_leak():
    reg = make_dual_registry()
    r = resolve_route(reg, "soul-a", "backend-a")
    assert r.agent_id == "agent-01"
    with pytest.raises(InterfaceError) as ei:
        resolve_route(reg, "soul-a", "missing")
    assert ei.value.status == 404
    assert "agent-01" not in str(ei.value) and "missing" not in str(ei.value)
