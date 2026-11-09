"""Token auth + route authorization. No IO, no token echo."""

import hashlib
import hmac
from .schema import AuthenticatedClient
from .schema import InterfaceError


def _unauth():
    raise InterfaceError(401, "unauthorized", None, "Invalid credentials.")


def authenticate(bearer_token: str, registry) -> AuthenticatedClient:
    if not isinstance(bearer_token, str):
        _unauth()
    if len(bearer_token) > 4096:
        _unauth()
    if bearer_token.strip() == "":
        _unauth()
    try:
        digest = hashlib.sha256(bearer_token.encode("utf-8")).hexdigest()
    except Exception:
        _unauth()
    for cred in registry.credentials:
        if hmac.compare_digest(cred.digest, digest):
            return AuthenticatedClient(profile=cred.profile)
    _unauth()


def authorize_route(client: AuthenticatedClient, route) -> None:
    try:
        allowed = client.profile.allowed_routes
    except Exception:
        raise InterfaceError(403, "forbidden", "route", "Route access is forbidden.")
    want = route.soul_alias + "/" + route.backend_alias
    for e in allowed:
        if e == want:
            return
    raise InterfaceError(403, "forbidden", "route", "Route access is forbidden.")
