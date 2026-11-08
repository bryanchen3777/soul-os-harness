"""Identity tests: shared factory import, no old runtime."""

import pytest

from src.interfaces.identity import authenticate
from src.interfaces.identity import authorize_route
from src.interfaces.schema import AuthenticatedClient
from src.interfaces.schema import InterfaceError

try:
    from tests.interfaces.test_config import TOKEN_A
    from tests.interfaces.test_config import TOKEN_B
    from tests.interfaces.test_config import TOKEN_U
    from tests.interfaces.test_config import make_client_dict
    from tests.interfaces.test_config import make_dual_registry
    from tests.interfaces.test_config import make_registry
    from tests.interfaces.test_config import make_registry_raw
except ImportError:
    from test_config import TOKEN_A  # type: ignore
    from test_config import TOKEN_B  # type: ignore
    from test_config import TOKEN_U  # type: ignore
    from test_config import make_client_dict  # type: ignore
    from test_config import make_dual_registry  # type: ignore
    from test_config import make_registry  # type: ignore
    from test_config import make_registry_raw  # type: ignore

from src.interfaces.config import validate_registry


def test_authenticate_ok_returns_profile():
    reg = make_dual_registry()
    c = authenticate(TOKEN_A, reg)
    assert isinstance(c, AuthenticatedClient)
    assert c.profile.client_id == "client-a"
    assert c.profile.principal_id == "owner-01"
    assert "token-alpha" not in repr(c)


def test_authenticate_wrong_token_401_no_echo():
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        authenticate("wrong-secret-value", reg)
    assert ei.value.status == 401
    assert "wrong-secret-value" not in str(ei.value.message)
    assert "wrong-secret-value" not in repr(ei.value)


@pytest.mark.parametrize("bad", ["", "   ", "\t\n ", "x" * 4097])
def test_authenticate_blank_or_too_long_401(bad):
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        authenticate(bad, reg)
    assert ei.value.status == 401


@pytest.mark.parametrize("bad", [None, 123, b"bytes-token", ["x"]])
def test_authenticate_non_string_401(bad):
    reg = make_dual_registry()
    with pytest.raises(InterfaceError) as ei:
        authenticate(bad, reg)
    assert ei.value.status == 401


def test_authenticate_does_not_store_raw_token():
    reg = make_dual_registry()
    c = authenticate(TOKEN_B, reg)
    assert c.profile.client_id == "client-b"
    blob = repr(c) + repr(c.profile)
    assert TOKEN_B not in blob


def test_authorize_ok_and_forbidden():
    reg = make_dual_registry()
    ca = authenticate(TOKEN_A, reg)
    route = reg.routes[0]
    authorize_route(ca, route)
    raw = make_registry_raw(routes=[{"soul_alias": "soul-a", "backend_alias": "backend-a", "agent_id": "agent-01", "public_model_id": "model-a", "backend_ref": "port-a", "context_window_tokens": 100}, {"soul_alias": "soul-a", "backend_alias": "backend-b", "agent_id": "agent-02", "public_model_id": "model-b", "backend_ref": "port-a", "context_window_tokens": 100}], clients=[make_client_dict("client-a", TOKEN_A, allowed_routes=["soul-a/backend-a"])])
    reg2 = validate_registry(raw)
    ca2 = authenticate(TOKEN_A, reg2)
    other = [r for r in reg2.routes if r.backend_alias == "backend-b"][0]
    with pytest.raises(InterfaceError) as ei:
        authorize_route(ca2, other)
    assert ei.value.status == 403


def test_body_role_cannot_forge_identity():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-u", TOKEN_U, purpose="utility")])
    reg = validate_registry(raw)
    c = authenticate(TOKEN_U, reg)
    assert c.profile.purpose.value == "utility"
    assert c.profile.scope.value == "none"
