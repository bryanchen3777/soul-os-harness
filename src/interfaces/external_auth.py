"""Caller-owned registry snapshot + pure external entry. No env, no files, no network."""

import threading
from collections.abc import Mapping
from collections.abc import Sequence

from .config import validate_registry
from .context_policy import admit
from .schema import InterfaceError


def _unauth():
    raise InterfaceError(401, "unauthorized", None, "Invalid credentials.")


def _bad_headers():
    raise InterfaceError(400, "invalid_request", "headers", "Invalid request.")


class RegistryProvider:
    """Caller-owned, explicit registry snapshot.

    The provider never reads the environment and never watches files: the
    caller passes each candidate mapping explicitly. update() fully
    validates the candidate with validate_registry first and only swaps
    the snapshot on success; a bad candidate raises InterfaceError and
    the old snapshot keeps serving. Snapshots are immutable Registry
    objects, so a request admitted against an older snapshot (in-flight)
    is unaffected by a later swap -- new requests read the new snapshot.
    """

    def __init__(self, initial_raw):
        self._lock = threading.Lock()
        self._snapshot = validate_registry(initial_raw)

    def current(self):
        with self._lock:
            return self._snapshot

    def update(self, new_raw):
        candidate = validate_registry(new_raw)
        with self._lock:
            self._snapshot = candidate
            return candidate


def extract_bearer(raw_headers):
    """Split a raw header sequence into (bearer_token, headers_mapping).

    raw_headers is a sequence of (name, value) string pairs, preserving
    duplicates so the boundary can judge them. Rules: exactly one
    Authorization entry (zero or duplicates -> 401); its value must be a
    single "Bearer <token>" with no embedded whitespace (anything else
    -> 401); duplicate non-Authorization names -> 400 like admit. No
    error path ever echoes the credential.
    """
    if isinstance(raw_headers, Mapping) or isinstance(raw_headers, (str, bytes)):
        _bad_headers()
    try:
        pairs = list(raw_headers)
    except Exception:
        _bad_headers()
    auth_values = []
    headers = {}
    dup_non_auth = False
    for item in pairs:
        if not isinstance(item, (tuple, list)) or len(item) != 2:
            _bad_headers()
        name, value = item[0], item[1]
        if not isinstance(name, str) or not isinstance(value, str):
            _bad_headers()
        low = name.lower()
        if low == "authorization":
            auth_values.append(value)
        elif low in headers:
            dup_non_auth = True
        else:
            headers[low] = value
    if len(auth_values) != 1:
        _unauth()
    value = auth_values[0]
    if not value.startswith("Bearer "):
        _unauth()
    token = value[7:]
    if token == "":
        _unauth()
    for ch in token:
        if ch == " " or ch == "\t" or ch == "\n" or ch == "\r":
            _unauth()
    if dup_non_auth:
        _bad_headers()
    return (token, headers)


def serve_external(provider, raw_headers, soul_alias, backend_alias, raw_request, request_id, executor, query_token=None, body_token=None):
    """Pure external entry: auth -> route -> body -> headers, then execute once.

    query_token / body_token exist only to prove the ban: any key smuggled
    outside the Authorization header is refused with 401 even when the
    header itself is valid. There is no query/body credential path that
    works. Identity and routing reuse admit, so unknown routes stay 403
    and malformed bodies stay 400/413. The executor runs exactly once and
    only after admit succeeds; every auth failure raises before the
    executor is touched, and no error carries the raw credential.
    """
    if not callable(executor):
        raise TypeError("executor must be callable")
    if query_token is not None or body_token is not None:
        _unauth()
    token, headers = extract_bearer(raw_headers)
    registry = provider.current()
    decision = admit(registry, token, soul_alias, backend_alias, raw_request, headers, request_id)
    return executor(decision)
