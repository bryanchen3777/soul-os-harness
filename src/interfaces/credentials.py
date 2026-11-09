"""Offline credential lifecycle: issue / rotate / revoke. Pure stdlib, no IO."""

import copy
import hashlib
import secrets
from collections.abc import Mapping

from .schema import InterfaceError


_TOKEN_BYTES = 32
_HEX = frozenset("0123456789abcdefABCDEF")


def _bad(param, message="Invalid registry configuration."):
    raise InterfaceError(500, "invalid_registry", param, message)


def issue_credential():
    """Mint one fresh credential.

    Returns (secret, sha256_hex_digest). The secret is shown exactly once
    to the caller; only the digest ever enters a registry mapping.
    No files, no prints, no logging: the secret never leaves this tuple.
    """
    secret = secrets.token_urlsafe(_TOKEN_BYTES)
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return (secret, digest)


def digest_of(secret):
    """SHA-256 hex digest of a plaintext secret. Test/rotation helper."""
    if not isinstance(secret, str):
        _bad("credential_sha256")
    try:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()
    except Exception:
        _bad("credential_sha256")


def _normalize_digest(value):
    if not isinstance(value, str):
        return None
    if len(value) != 64:
        return None
    for ch in value:
        if ch not in _HEX:
            return None
    return value.lower()


def _as_mutable_registry(raw):
    if not isinstance(raw, Mapping):
        _bad("registry")
    try:
        dup = copy.deepcopy(dict(raw))
    except Exception:
        _bad("registry")
    if not isinstance(dup.get("clients"), list):
        _bad("clients")
    return dup


def _client_index(raw, client_id):
    if not isinstance(client_id, str):
        _bad("client_id")
    clients = raw.get("clients")
    if not isinstance(clients, list):
        _bad("clients")
    for i, entry in enumerate(clients):
        if isinstance(entry, Mapping) and entry.get("client_id") == client_id:
            return i
    raise InterfaceError(404, "client_not_found", "client_id", "Unknown client.")


def rotate_registry(raw, client_id, new_digest):
    """Return a new registry mapping with client_id bound to new_digest.

    Rotation replaces the digest in place: one client keeps exactly one
    digest, no dual-key grace period, no alias client. The input mapping
    is never mutated; a bad digest, an unknown client, or a digest that
    collides with another client raises InterfaceError and returns nothing.
    Full schema validation still happens at install time (RegistryProvider
    runs validate_registry and only swaps on success).
    """
    norm = _normalize_digest(new_digest)
    if norm is None:
        _bad("credential_sha256")
    dup = _as_mutable_registry(raw)
    idx = _client_index(dup, client_id)
    for j, other in enumerate(dup["clients"]):
        if j != idx and isinstance(other, Mapping):
            od = other.get("credential_sha256")
            if isinstance(od, str) and od.lower() == norm:
                _bad("credential_sha256")
    entry = dup["clients"][idx]
    if not isinstance(entry, Mapping):
        _bad("clients")
    fresh = dict(entry)
    fresh["credential_sha256"] = norm
    dup["clients"][idx] = fresh
    return dup


def revoke_registry(raw, client_id):
    """Return a new registry mapping with client_id removed.

    Revocation deletes the client entry: no hidden backdoor, no tombstone
    that still authenticates. Refusing to manufacture an illegal empty
    registry: revoking the last remaining client raises InterfaceError
    (the caller decommissions the entry point instead of serving an
    empty client list). Unknown client raises InterfaceError. The input
    mapping is never mutated.
    """
    dup = _as_mutable_registry(raw)
    idx = _client_index(dup, client_id)
    if len(dup["clients"]) <= 1:
        raise InterfaceError(409, "last_client", "clients", "Refusing to revoke the last client.")
    dup["clients"] = [c for j, c in enumerate(dup["clients"]) if j != idx]
    return dup
