"""SA-2 admission identity policy (400 / 409 mapping, keys, fingerprint).

Decision-complete scope (SA-2): identity extraction + normalization for the
shared pool, the fixed 5-col pool key, the 6-col dedup key, and the
execution fingerprint. No business logic, no IO, no network, no clock.

Rules (fixed):

  - Strict header path (external callers): ``conversation`` / ``turn`` /
    ``idempotency-key`` are REQUIRED (missing or malformed => 400);
    ``branch`` defaults to ``"root"`` when absent; duplicate headers
    (case-insensitive) => 400; unknown ``x-soul-*`` headers => 400
    ``unsupported_parameter``. ID charset mirrors EI-1 ``is_valid_id``
    (``[A-Za-z0-9._-]{1,128}``, no ``.``/``..``) without importing EI-1.
  - Legacy DM entries (web WS DM, TG private chat, voice): unify on
    ``conversation="dm"``, ``branch="root"`` via ``dm_identity``. TG
    derives turn/key from bot namespace + ``update_id``; voice from
    session + ``turn_id``.
  - Pool key: ``(principal, agent, scope, conversation, branch)`` — fixed,
    no channel / client / backend.
  - Dedup key: pool key + ``idempotency_key`` (EI-1 six-col namespace).
  - Fingerprint: backend alias / backend ref / public model, request text,
    turn, params, memory policy. ``request_id`` is EXCLUDED (retries mint
    fresh ids; same logical request must still replay).
  - HTTP mapping: bad identity => 400; same-key different-fingerprint /
    busy (in-progress) / isolated => 409; completed same-key => 200
    replay of the original response.

Purity: stdlib + SA-0 ``schema`` + sibling ``pool`` only. No EI-1 imports,
no provider / backend imports, no network, no clock.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from src.soul_access import pool as _pool
from src.soul_access.schema import DedupKey

__all__ = [
    "IdentityError",
    "EntryIdentity",
    "DM_CONVERSATION",
    "DEFAULT_BRANCH",
    "HEADER_CONVERSATION",
    "HEADER_BRANCH",
    "HEADER_TURN",
    "HEADER_KEY",
    "parse_headers",
    "dm_identity",
    "tg_identity",
    "vc_identity",
    "build_pool_key",
    "build_dedup_key",
    "request_fingerprint",
    "http_status_for",
]

#: Legacy-DM unified conversation (entries without a conversation concept).
DM_CONVERSATION = "dm"

#: Default branch when the caller supplies none.
DEFAULT_BRANCH = "root"

#: Strict header names (same wire names as the EI-1 admission headers).
HEADER_CONVERSATION = "x-soul-conversation-id"
HEADER_BRANCH = "x-soul-branch-id"
HEADER_TURN = "x-soul-turn-id"
HEADER_KEY = "idempotency-key"

_ID_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-"
)


class IdentityError(Exception):
    """Fail-closed identity refusal (carries its HTTP mapping)."""

    def __init__(self, status: int, code: str, param, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.param = param
        self.message = message


@dataclass(frozen=True)
class EntryIdentity:
    """Normalized per-request identity (conversation/branch/turn/key)."""

    conversation_id: str
    branch_id: str
    turn_id: str
    idempotency_key: str


def _bad(status: int, code: str, param, message: str = "Invalid request."):
    raise IdentityError(status, code, param, message)


def _is_valid_id(value: object) -> bool:
    """ID charset rule (parity with EI-1 ``is_valid_id``, reimplemented).

    Local copy on purpose: soul_access stays stdlib + SA-0-only so the
    EI-1 old path is provably untouched (no new import edge).
    """
    if not isinstance(value, str):
        return False
    n = len(value)
    if n < 1 or n > 128:
        return False
    if value == "." or value == "..":
        return False
    for ch in value:
        if ch not in _ID_CHARS:
            return False
    return True


def _need_id(name: str, value: object) -> str:
    if not _is_valid_id(value):
        _bad(400, "invalid_request", name)
    return value


def parse_headers(headers: Mapping) -> EntryIdentity:
    """Parse the strict identity headers (fail-closed, 400 on any fault)."""
    if not isinstance(headers, Mapping):
        _bad(400, "invalid_request", None)
    seen: dict = {}
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            _bad(400, "invalid_request", None)
        low = key.lower()
        if low in seen:
            _bad(400, "invalid_request", "headers")
        seen[low] = value
    for low in seen:
        if low.startswith("x-soul-") and low not in (
            HEADER_CONVERSATION,
            HEADER_BRANCH,
            HEADER_TURN,
        ):
            _bad(400, "unsupported_parameter", "headers")
    conv = seen.get(HEADER_CONVERSATION)
    branch = seen.get(HEADER_BRANCH, DEFAULT_BRANCH)
    turn = seen.get(HEADER_TURN)
    key = seen.get(HEADER_KEY)
    if conv is None:
        _bad(400, "invalid_request", HEADER_CONVERSATION)
    if turn is None:
        _bad(400, "invalid_request", HEADER_TURN)
    if key is None:
        _bad(400, "invalid_request", HEADER_KEY)
    return EntryIdentity(
        conversation_id=_need_id(HEADER_CONVERSATION, conv),
        branch_id=_need_id(HEADER_BRANCH, branch),
        turn_id=_need_id(HEADER_TURN, turn),
        idempotency_key=_need_id(HEADER_KEY, key),
    )


def dm_identity(*, turn: str, key: str,
                conversation: str = DM_CONVERSATION,
                branch: str = DEFAULT_BRANCH) -> EntryIdentity:
    """Legacy-DM identity (unified conversation ``dm`` / branch ``root``)."""
    if turn is None or key is None:
        _bad(400, "invalid_request", None)
    return EntryIdentity(
        conversation_id=_need_id(HEADER_CONVERSATION, conversation),
        branch_id=_need_id(HEADER_BRANCH, branch),
        turn_id=_need_id(HEADER_TURN, turn),
        idempotency_key=_need_id(HEADER_KEY, key),
    )


def tg_identity(*, update_id, channel: str = "telegram") -> EntryIdentity:
    """TG identity: turn/key from bot namespace + ``update_id``."""
    if not isinstance(channel, str) or not channel:
        _bad(400, "invalid_request", "channel")
    if isinstance(update_id, bool):
        _bad(400, "invalid_request", HEADER_TURN)
    try:
        update_int = int(update_id)
    except (TypeError, ValueError):
        _bad(400, "invalid_request", HEADER_TURN)
    if isinstance(update_id, str) and str(update_int) != update_id.strip():
        _bad(400, "invalid_request", HEADER_TURN)
    turn = str(update_int)
    # NOTE: "." separator (not ":"): the derived key must itself pass the
    # EI-1-parity ID charset ([A-Za-z0-9._-], no ":"), else admission 400s.
    return dm_identity(turn=turn, key="%s.%d" % (channel, update_int))


def vc_identity(*, session_id: str, turn_id: str) -> EntryIdentity:
    """Voice identity: turn/key from session + ``turn_id``."""
    if not _is_valid_id(session_id):
        _bad(400, "invalid_request", "session_id")
    if not _is_valid_id(turn_id):
        _bad(400, "invalid_request", HEADER_TURN)
    # NOTE: "." separator (not ":"): see tg_identity above.
    return dm_identity(turn=turn_id, key="%s.%s" % (session_id, turn_id))


def build_pool_key(*, principal_id: str, agent_id: str, scope: str,
                   identity: EntryIdentity) -> _pool.PoolKey:
    """Fixed 5-col pool key (no channel / client / backend, ever)."""
    if not isinstance(identity, EntryIdentity):
        _bad(400, "invalid_request", None)
    for name, value in (
        ("principal_id", principal_id),
        ("agent_id", agent_id),
        ("scope", scope),
    ):
        if not isinstance(value, str) or not value:
            _bad(400, "invalid_request", name)
    _need_id(HEADER_CONVERSATION, identity.conversation_id)
    _need_id(HEADER_BRANCH, identity.branch_id)
    return _pool.PoolKey(
        principal_id=principal_id,
        agent_id=agent_id,
        scope=scope,
        conversation_id=identity.conversation_id,
        branch_id=identity.branch_id,
    )


def build_dedup_key(pool_key: _pool.PoolKey, idempotency_key: str) -> DedupKey:
    """6-col dedup key: pool key + ``idempotency_key`` (EI-1 namespace)."""
    if not isinstance(pool_key, _pool.PoolKey):
        _bad(400, "invalid_request", None)
    return DedupKey(
        principal_id=pool_key.principal_id,
        agent_id=pool_key.agent_id,
        scope=pool_key.scope,
        conversation_id=pool_key.conversation_id,
        branch_id=pool_key.branch_id,
        idempotency_key=_need_id(HEADER_KEY, idempotency_key),
    )


def _norm_params(params) -> list:
    if isinstance(params, Mapping):
        items = sorted(params.items(), key=lambda kv: str(kv[0]))
        return [[str(k), str(v)] for k, v in items]
    try:
        pairs = list(params)
    except TypeError:
        _bad(400, "invalid_request", "params")
    norm = []
    for pair in pairs:
        try:
            k, v = pair
        except (TypeError, ValueError):
            _bad(400, "invalid_request", "params")
        norm.append([str(k), str(v)])
    norm.sort(key=lambda kv: (kv[0], kv[1]))
    return norm


def request_fingerprint(*, backend_alias: str, backend_ref: str,
                        public_model_id: str, text: str, turn_id: str,
                        params, memory_policy: str) -> str:
    """Execution fingerprint (sha256; ``request_id`` deliberately excluded).

    Any backend / text / turn / params / policy drift changes the digest,
    so the same dedup key with a different execution conflicts (409)
    instead of falsely replaying.
    """
    for name, value in (
        ("backend_alias", backend_alias),
        ("backend_ref", backend_ref),
        ("public_model_id", public_model_id),
        ("text", text),
        ("turn_id", turn_id),
        ("memory_policy", memory_policy),
    ):
        if not isinstance(value, str) or not value:
            _bad(400, "invalid_request", name)
    payload = {
        "backend_alias": backend_alias,
        "backend_ref": backend_ref,
        "public_model_id": public_model_id,
        "text": text,
        "turn_id": turn_id,
        "params": _norm_params(params),
        "memory_policy": memory_policy,
    }
    data = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def http_status_for(disposition: str) -> int:
    """HTTP mapping: REPLAY/NEW => 200; IN_PROGRESS/CONFLICT/ISOLATED => 409."""
    if disposition in (_pool.DISPOSITION_NEW, _pool.DISPOSITION_REPLAY):
        return 200
    if disposition in (
        _pool.DISPOSITION_IN_PROGRESS,
        _pool.DISPOSITION_CONFLICT,
        _pool.DISPOSITION_ISOLATED,
    ):
        return 409
    _bad(500, "internal", None, "Unknown disposition.")
