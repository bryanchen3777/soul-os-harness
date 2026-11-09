"""External entry + registry provider tests. Offline only: no server, no ports."""

import ast
import threading
from pathlib import Path

import pytest

from src.interfaces.config import validate_registry
from src.interfaces.credentials import issue_credential
from src.interfaces.credentials import rotate_registry
from src.interfaces.credentials import revoke_registry
from src.interfaces.external_auth import extract_bearer
from src.interfaces.external_auth import RegistryProvider
from src.interfaces.external_auth import serve_external
from src.interfaces.identity import authenticate
from src.interfaces.schema import InterfaceError

try:
    from tests.interfaces.test_config import TOKEN_A
    from tests.interfaces.test_config import TOKEN_B
    from tests.interfaces.test_config import digest_for
    from tests.interfaces.test_config import make_client_dict
    from tests.interfaces.test_config import make_dual_registry
    from tests.interfaces.test_config import make_registry
    from tests.interfaces.test_config import make_registry_raw
except ImportError:
    from test_config import TOKEN_A  # type: ignore
    from test_config import TOKEN_B  # type: ignore
    from test_config import digest_for  # type: ignore
    from test_config import make_client_dict  # type: ignore
    from test_config import make_dual_registry  # type: ignore
    from test_config import make_registry  # type: ignore
    from test_config import make_registry_raw  # type: ignore


def _body(text="hello world"):
    return {"model": "model-a", "messages": [{"role": "user", "content": text}]}


def _headers(token, extra=None):
    pairs = [("Authorization", "Bearer " + token)]
    for k, v in (extra or {}).items():
        pairs.append((k, v))
    return pairs


def _provider(**kw):
    return RegistryProvider(make_registry_raw(**kw))


# -- the oracle: one test kills both mutations -----------------------------
# M-bypass (always-admit) breaks the invalid half; M-reject (always-401)
# breaks the valid half. A green oracle means neither mutation survives.

def test_oracle_valid_passes_invalid_rejected():
    prov = RegistryProvider(make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)]))
    calls = []
    out = serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-oracle-ok", lambda d: calls.append(d) or "OK")
    assert out == "OK"
    assert len(calls) == 1 and calls[0].context.profile.client_id == "client-a"
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers("wrong-token-xyz"), "soul-a", "backend-a", _body(), "req-oracle-bad", lambda d: calls.append(d))
    assert ei.value.status == 401
    assert len(calls) == 1


# -- acceptance: new / rotated / revoked keys -------------------------------

def test_new_key_authenticates_end_to_end():
    secret, digest = issue_credential()
    raw = make_registry_raw()
    rotated = rotate_registry(raw, "client-a", digest)
    prov = RegistryProvider(rotated)
    calls = []
    serve_external(prov, _headers(secret), "soul-a", "backend-a", _body(), "req-new-ok", lambda d: calls.append(d))
    assert len(calls) == 1


def test_rotated_old_key_401_new_key_passes():
    secret, digest = issue_credential()
    prov = RegistryProvider(rotate_registry(make_registry_raw(), "client-a", digest))
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-rot-old", lambda d: None)
    assert ei.value.status == 401
    calls = []
    serve_external(prov, _headers(secret), "soul-a", "backend-a", _body(), "req-rot-new", lambda d: calls.append(d))
    assert len(calls) == 1


def test_revoked_key_rejected_for_new_requests():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    prov = RegistryProvider(raw)
    prov.update(revoke_registry(raw, "client-b"))
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_B), "soul-a", "backend-a", _body(), "req-rev", lambda d: None)
    assert ei.value.status == 401
    calls = []
    serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-kept", lambda d: calls.append(d))
    assert len(calls) == 1


# -- provider snapshot semantics ---------------------------------------------

def test_bad_registry_update_keeps_old_snapshot():
    prov = _provider()
    old = prov.current()
    bad = make_registry_raw()
    bad["clients"][0]["credential_sha256"] = "not-hex"
    with pytest.raises(InterfaceError):
        prov.update(bad)
    assert prov.current() is old
    calls = []
    serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-still-ok", lambda d: calls.append(d))
    assert len(calls) == 1


def test_update_validates_before_swap_duplicate_digest_rejected():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    prov = RegistryProvider(raw)
    old = prov.current()
    evil = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_A)])
    with pytest.raises(InterfaceError):
        prov.update(evil)
    assert prov.current() is old


def test_inflight_snapshot_survives_update():
    prov = _provider()
    old = prov.current()
    _, digest = issue_credential()
    prov.update(rotate_registry(make_registry_raw(), "client-a", digest))
    assert authenticate(TOKEN_A, old).profile.client_id == "client-a"
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-after-rot", lambda d: None)
    assert ei.value.status == 401


def test_provider_reads_no_env_and_watches_no_files():
    src = Path("src/interfaces/external_auth.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    try:
        stdlib = set(__import__("sys").stdlib_module_names)
    except AttributeError:  # pragma: no cover
        stdlib = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                top = a.name.split(".")[0]
                assert top in stdlib, "bad import " + a.name
                assert top not in ("os", "socket", "subprocess", "sqlite3", "logging"), "banned import " + top
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                assert node.module in ("schema", "config", "identity", "context_policy"), "bad relative " + str(node.module)
            else:
                top = (node.module or "").split(".")[0]
                assert top in stdlib, "bad from-import " + str(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in ("open", "print", "getenv", "environ", "socket", "connect", "Popen", "run", "urlopen"), "bad call " + name
    idents = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    idents |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (idents & {"getenv", "environ", "socket", "subprocess", "urlopen"})


def test_snapshot_swap_atomic_under_threads():
    base_a = make_registry_raw()
    base_b = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_B)])
    d_a, d_b = digest_for(TOKEN_A), digest_for(TOKEN_B)
    prov = RegistryProvider(base_a)
    observed = []
    errors = []
    guard = threading.Lock()

    def reader():
        try:
            for _ in range(100):
                with guard:
                    observed.append(prov.current().credentials[0].digest)
        except Exception as exc:  # pragma: no cover - must stay empty
            with guard:
                errors.append(exc)

    def flipper():
        try:
            for _ in range(20):
                prov.update(base_b)
                prov.update(base_a)
        except Exception as exc:  # pragma: no cover - must stay empty
            with guard:
                errors.append(exc)

    def poisoner():
        bad = make_registry_raw()
        bad["clients"][0]["credential_sha256"] = "not-hex"
        for _ in range(20):
            try:
                prov.update(bad)
            except InterfaceError:
                pass
            except Exception as exc:  # pragma: no cover - must stay empty
                with guard:
                    errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(4)]
    threads += [threading.Thread(target=flipper) for _ in range(2)]
    threads += [threading.Thread(target=poisoner)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
        assert not t.is_alive()
    assert errors == []
    assert observed and set(observed) <= {d_a, d_b}
    final = prov.current().credentials[0].digest
    assert final in (d_a, d_b)
    authenticate(TOKEN_A if final == d_a else TOKEN_B, prov.current())


# -- header boundary ---------------------------------------------------------

def test_extract_bearer_ok_and_headers_pass_through():
    token, headers = extract_bearer([("Authorization", "Bearer " + TOKEN_A), ("X-Soul-Turn-Id", "t-01")])
    assert token == TOKEN_A
    assert headers["x-soul-turn-id"] == "t-01"


def test_missing_authorization_401_executor_silent():
    prov = _provider()
    calls = []
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, [("X-Soul-Turn-Id", "t-01")], "soul-a", "backend-a", _body(), "req-noauth", lambda d: calls.append(d))
    assert ei.value.status == 401
    assert calls == []


def test_duplicate_authorization_401_executor_silent():
    prov = _provider()
    calls = []
    dup = [("Authorization", "Bearer " + TOKEN_A), ("authorization", "Bearer " + TOKEN_B)]
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, dup, "soul-a", "backend-a", _body(), "req-dup", lambda d: calls.append(d))
    assert ei.value.status == 401
    assert calls == []


@pytest.mark.parametrize("bad", ["Bearer", "Bearer ", "Basic xyz", "Token " + TOKEN_A, "bearer " + TOKEN_A, "Bearer a b", "Bearer\ta", "Bearer " + "x" * 4097])
def test_malformed_authorization_401_executor_silent(bad):
    prov = _provider()
    calls = []
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, [("Authorization", bad)], "soul-a", "backend-a", _body(), "req-mal", lambda d: calls.append(d))
    assert ei.value.status == 401
    assert calls == []
    assert TOKEN_A not in str(ei.value.message) and TOKEN_A not in repr(ei.value)


def test_raw_headers_wrong_type_400():
    prov = _provider()
    for bad in ({"Authorization": "Bearer x"}, "Bearer x", b"Bearer x", [("Authorization",)], [("Authorization", "Bearer x", "extra")], [(123, "v")], [("Authorization", 123)]):
        with pytest.raises(InterfaceError) as ei:
            serve_external(prov, bad, "soul-a", "backend-a", _body(), "req-wt", lambda d: None)
        assert ei.value.status in (400, 401)


def test_duplicate_non_auth_header_400_executor_silent():
    prov = _provider()
    calls = []
    dup = _headers(TOKEN_A, {"X-Soul-Turn-Id": "a"}) + [("x-soul-turn-id", "b")]
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, dup, "soul-a", "backend-a", _body(), "req-dh", lambda d: calls.append(d))
    assert ei.value.status == 400
    assert calls == []


def test_query_token_banned_even_with_valid_header():
    prov = _provider()
    calls = []
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-q", lambda d: calls.append(d), query_token=TOKEN_A)
    assert ei.value.status == 401
    assert calls == []


def test_body_token_banned_even_with_valid_header():
    prov = _provider()
    calls = []
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-b", lambda d: calls.append(d), body_token=TOKEN_A)
    assert ei.value.status == 401
    assert calls == []


# -- route + executor separation ----------------------------------------------

def test_unauthorized_route_403_executor_silent():
    routes = [
        {"soul_alias": "soul-a", "backend_alias": "backend-a", "agent_id": "agent-01", "public_model_id": "model-a", "backend_ref": "port-a", "context_window_tokens": 100},
        {"soul_alias": "soul-a", "backend_alias": "backend-b", "agent_id": "agent-02", "public_model_id": "model-b", "backend_ref": "port-a", "context_window_tokens": 100},
    ]
    raw = make_registry_raw(routes=routes, clients=[make_client_dict("client-a", TOKEN_A, allowed_routes=["soul-a/backend-a"])])
    prov = RegistryProvider(raw)
    calls = []
    body = {"model": "model-b", "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-b", body, "req-403", lambda d: calls.append(d))
    assert ei.value.status == 403
    assert calls == []


def test_auth_failure_never_touches_executor():
    prov = _provider()

    def _boom(decision):
        raise AssertionError("executor must not run on auth failure")

    cases = [
        ([], "req-e1", None, None),
        ([("Authorization", "Bearer wrong")], "req-e2", None, None),
        ([("Authorization", "Bearer " + TOKEN_A), ("Authorization", "Bearer " + TOKEN_A)], "req-e3", None, None),
        (_headers(TOKEN_A), "req-e4", "smuggled", None),
        (_headers(TOKEN_A), "req-e5", None, "smuggled"),
    ]
    for headers, req_id, q, b in cases:
        with pytest.raises(InterfaceError):
            serve_external(prov, headers, "soul-a", "backend-a", _body(), req_id, _boom, query_token=q, body_token=b)


def test_executor_called_exactly_once_and_result_passthrough():
    prov = _provider()
    calls = []
    sentinel = object()

    def _exec(decision):
        calls.append(decision)
        return sentinel

    out = serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-once", _exec)
    assert out is sentinel
    assert len(calls) == 1


def test_non_callable_executor_is_programmer_error():
    prov = _provider()
    with pytest.raises(TypeError):
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-te", None)


def test_entry_errors_never_echo_credential():
    prov = _provider()
    nasty = "Bearer sk-live-SECRET-ABC-123-xyz"
    with pytest.raises(InterfaceError) as ei:
        serve_external(prov, [("Authorization", nasty)], "soul-a", "backend-a", _body(), "req-leak", lambda d: None)
    assert "sk-live-SECRET-ABC-123-xyz" not in str(ei.value.message)
    assert "sk-live-SECRET-ABC-123-xyz" not in repr(ei.value)
    assert "sk-live-SECRET-ABC-123-xyz" not in str(ei.value.param)
    with pytest.raises(InterfaceError) as ei2:
        serve_external(prov, _headers(TOKEN_A), "soul-a", "backend-a", _body(), "req-leak2", lambda d: None, query_token=nasty)
    assert "sk-live-SECRET-ABC-123-xyz" not in repr(ei2.value)
