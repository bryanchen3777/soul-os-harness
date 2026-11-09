"""External credential lifecycle tests: issue / rotate / revoke. Offline only."""

import ast
import copy
import hashlib
from pathlib import Path

import pytest

from src.interfaces.config import validate_registry
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.credentials import revoke_registry
from src.interfaces.credentials import rotate_registry
from src.interfaces.identity import authenticate
from src.interfaces.schema import InterfaceError

try:
    from tests.interfaces.test_config import TOKEN_A
    from tests.interfaces.test_config import TOKEN_B
    from tests.interfaces.test_config import digest_for
    from tests.interfaces.test_config import make_client_dict
    from tests.interfaces.test_config import make_dual_registry
    from tests.interfaces.test_config import make_registry_raw
except ImportError:
    from test_config import TOKEN_A  # type: ignore
    from test_config import TOKEN_B  # type: ignore
    from test_config import digest_for  # type: ignore
    from test_config import make_client_dict  # type: ignore
    from test_config import make_dual_registry  # type: ignore
    from test_config import make_registry_raw  # type: ignore


# -- issue ---------------------------------------------------------------

def test_issue_returns_secret_and_matching_digest():
    secret, digest = issue_credential()
    assert isinstance(secret, str) and isinstance(digest, str)
    assert len(secret) > 0
    assert digest == hashlib.sha256(secret.encode("utf-8")).hexdigest()
    assert digest == digest_of(secret)


def test_issue_secrets_are_unique():
    s1, _ = issue_credential()
    s2, _ = issue_credential()
    assert s1 != s2


def test_issue_prints_nothing(capsys):
    issue_credential()
    out, err = capsys.readouterr()
    assert out == "" and err == ""


def test_credentials_module_has_no_io_or_env_vectors():
    src = Path("src/interfaces/credentials.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    try:
        stdlib = set(__import__("sys").stdlib_module_names)
    except AttributeError:  # pragma: no cover
        stdlib = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name.split(".")[0] in stdlib, "bad import " + a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                assert node.module in ("schema", "config", "identity"), "bad relative " + str(node.module)
            else:
                assert (node.module or "").split(".")[0] in stdlib, "bad from-import " + str(node.module)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
            assert name not in ("open", "print", "input", "exec", "eval", "compile", "__import__"), "bad call " + name
            assert name not in ("getenv", "environ", "socket", "connect", "Popen", "run", "urlopen", "getLogger", "info", "debug", "warning"), "bad call " + name
        elif isinstance(node, ast.Name):
            assert node.id not in ("environ",), "bad name " + node.id
    idents = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    idents |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (idents & {"getenv", "environ", "socket", "subprocess", "getLogger"})


# -- rotate --------------------------------------------------------------

def test_rotate_new_key_authenticates():
    raw = make_registry_raw()
    secret, digest = issue_credential()
    rotated = rotate_registry(raw, "client-a", digest)
    reg = validate_registry(rotated)
    client = authenticate(secret, reg)
    assert client.profile.client_id == "client-a"


def test_rotate_old_key_rejected_401():
    raw = make_registry_raw()
    secret, digest = issue_credential()
    rotated = rotate_registry(raw, "client-a", digest)
    reg = validate_registry(rotated)
    with pytest.raises(InterfaceError) as ei:
        authenticate(TOKEN_A, reg)
    assert ei.value.status == 401
    assert TOKEN_A not in str(ei.value.message) and TOKEN_A not in repr(ei.value)


def test_rotate_keeps_one_digest_per_client():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    _, digest = issue_credential()
    rotated = rotate_registry(raw, "client-a", digest)
    reg = validate_registry(rotated)
    assert len(reg.credentials) == 2
    digests = [c.digest for c in reg.credentials]
    assert len(set(digests)) == 2


def test_rotate_unknown_client_raises_and_input_unchanged():
    raw = make_registry_raw()
    before = copy.deepcopy(raw)
    _, digest = issue_credential()
    with pytest.raises(InterfaceError):
        rotate_registry(raw, "client-ghost", digest)
    assert raw == before


@pytest.mark.parametrize("bad", ["", "short", "g" * 64, "a" * 63, "a" * 65, 123, None, "X" * 64 + "!"])
def test_rotate_bad_digest_raises_and_input_unchanged(bad):
    raw = make_registry_raw()
    before = copy.deepcopy(raw)
    with pytest.raises(InterfaceError):
        rotate_registry(raw, "client-a", bad)
    assert raw == before


def test_rotate_duplicate_digest_refused():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    before = copy.deepcopy(raw)
    with pytest.raises(InterfaceError):
        rotate_registry(raw, "client-b", digest_for(TOKEN_A))
    assert raw == before


def test_rotate_does_not_mutate_input():
    raw = make_registry_raw()
    before = copy.deepcopy(raw)
    _, digest = issue_credential()
    rotate_registry(raw, "client-a", digest)
    assert raw == before
    assert raw["clients"][0]["credential_sha256"] == digest_for(TOKEN_A)


# -- revoke --------------------------------------------------------------

def test_revoke_new_requests_rejected_old_client_kept():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    revoked = revoke_registry(raw, "client-b")
    reg = validate_registry(revoked)
    assert [c.client_id for c in reg.clients] == ["client-a"]
    with pytest.raises(InterfaceError) as ei:
        authenticate(TOKEN_B, reg)
    assert ei.value.status == 401
    assert authenticate(TOKEN_A, reg).profile.client_id == "client-a"


def test_revoke_removes_binding_not_tombstone():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    revoked = revoke_registry(raw, "client-b")
    assert all(c.get("client_id") != "client-b" for c in revoked["clients"])
    assert digest_for(TOKEN_B) not in [c.get("credential_sha256", "").lower() for c in revoked["clients"]]


def test_revoke_unknown_client_raises_and_input_unchanged():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    before = copy.deepcopy(raw)
    with pytest.raises(InterfaceError):
        revoke_registry(raw, "client-ghost")
    assert raw == before


def test_revoke_last_client_refused_no_empty_registry():
    raw = make_registry_raw()
    before = copy.deepcopy(raw)
    with pytest.raises(InterfaceError) as ei:
        revoke_registry(raw, "client-a")
    assert ei.value.status == 409
    assert raw == before
    reg = validate_registry(raw)
    assert authenticate(TOKEN_A, reg).profile.client_id == "client-a"


def test_revoke_does_not_mutate_input():
    raw = make_registry_raw(clients=[make_client_dict("client-a", TOKEN_A), make_client_dict("client-b", TOKEN_B)])
    before = copy.deepcopy(raw)
    revoke_registry(raw, "client-b")
    assert raw == before


# -- leak checks ----------------------------------------------------------

def test_lifecycle_errors_never_echo_secret():
    secret, digest = issue_credential()
    raw = make_registry_raw()
    with pytest.raises(InterfaceError) as ei:
        authenticate(secret + "-wrong", validate_registry(raw))
    assert secret not in str(ei.value.message) and secret not in repr(ei.value)
    assert secret not in str(ei.value.param)
    before = copy.deepcopy(raw)
    with pytest.raises(InterfaceError):
        rotate_registry(raw, "client-ghost", digest)
    assert raw == before
    blob = digest + secret
    assert blob not in repr(rotate_registry(raw, "client-a", digest))


def test_example_registry_loads_validates_and_is_marked_test_only():
    import json

    example = json.loads(Path("configs/external_registry.example.json").read_text(encoding="utf-8"))
    reg = validate_registry(example)
    assert len(reg.clients) == 1
    assert "do-not-deploy" in example["owner_principal_id"]
    assert "do-not-deploy" in example["clients"][0]["client_id"]
    assert "example-" in example["routes"][0]["soul_alias"]
    assert example["clients"][0]["credential_sha256"] == digest_for("EXAMPLE-DO-NOT-DEPLOY-NEVER-USE-0001")
    assert example["clients"][0]["principal_id"] == example["owner_principal_id"]
