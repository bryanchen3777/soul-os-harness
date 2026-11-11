"""SA-2 cross-entry shared pool + dedup (no duplicate execution).

Contract (decision-complete, SA-2 only; SA-0 schema + SA-1 wiring untouched):

  - Pool key (fixed, 5 cols): ``(principal, agent, scope, conversation,
    branch)``. It carries NO channel / client / backend: every entry
    (web gateway DM, TG inbound, voice) shares the same pool row for the
    same five columns. Legacy DMs unify on ``conversation="dm"``,
    ``branch="root"`` (see ``policy.dm_identity``).
  - Dedup key (6 cols): pool key + ``idempotency_key``. This mirrors the
    EI-1 six-column idempotency namespace
    (``src/interfaces/context_policy.py:105``) without importing it.
  - Fingerprint (execution identity): backend alias / backend ref /
    public model, request text, turn, params, memory policy. It
    EXCLUDES ``request_id`` (retries mint fresh ids; same logical request
    must still replay). Same key + different fingerprint => CONFLICT.
  - Decisions: NEW (first claim, caller executes) / IN_PROGRESS (same
    key+fingerprint while executing => 409 busy, retry the SAME key) /
    REPLAY (same key+fingerprint completed => 200 with the ORIGINAL
    response, zero new loads / generations / writes) / CONFLICT (same key,
    different fingerprint => 409) / ISOLATED (quarantined row => 409).
  - Locking: this pool is NOT a lock. Per-soul serialization stays with
    the coordinator per-soul lease; ``claim`` is a synchronous
    check-and-set for single-process dedup only. The pool key is never
    used as a lock granularity.

Purity: stdlib + SA-0 ``schema`` types only. No IO, no network, no clock,
no global mutable state (each ``PoolStore`` instance owns its dict).
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from src.soul_access.schema import DedupKey

__all__ = [
    "PoolKey",
    "ClaimOutcome",
    "PoolRejected",
    "PoolStore",
    "DISPOSITION_NEW",
    "DISPOSITION_IN_PROGRESS",
    "DISPOSITION_REPLAY",
    "DISPOSITION_CONFLICT",
    "DISPOSITION_ISOLATED",
]

#: Claim dispositions (fixed set, never extended by callers).
DISPOSITION_NEW = "NEW"
DISPOSITION_IN_PROGRESS = "IN_PROGRESS"
DISPOSITION_REPLAY = "REPLAY"
DISPOSITION_CONFLICT = "CONFLICT"
DISPOSITION_ISOLATED = "ISOLATED"

#: Row states inside one PoolStore (never persisted, never replayed).
_STATE_EXECUTING = "executing"
_STATE_DONE = "done"
_STATE_ISOLATED = "isolated"


class PoolRejected(Exception):
    """Fail-closed refusal: bad identity (400) or dedup conflict (409)."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PoolKey:
    """Fixed 5-col pool key (no channel / client / backend, ever)."""

    principal_id: str
    agent_id: str
    scope: str
    conversation_id: str
    branch_id: str


@dataclass(frozen=True)
class ClaimOutcome:
    """One claim decision (HTTP-mapped; NEW means "caller executes")."""

    disposition: str
    http_status: int
    code: str
    response_text: str = ""
    message: str = ""


@dataclass
class _Row:
    fingerprint: str
    state: str
    response_text: str = ""


def _require_id(name: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise PoolRejected(400, "invalid_request", name, "Invalid request.")
    return value


def _coerce_dedup(dedup: DedupKey) -> tuple:
    if not isinstance(dedup, DedupKey):
        raise PoolRejected(400, "invalid_request", None, "Invalid request.")
    return (
        _require_id("principal_id", dedup.principal_id),
        _require_id("agent_id", dedup.agent_id),
        _require_id("scope", dedup.scope),
        _require_id("conversation_id", dedup.conversation_id),
        _require_id("branch_id", dedup.branch_id),
        _require_id("idempotency_key", dedup.idempotency_key),
    )


@dataclass
class PoolStore:
    """In-memory shared pool (one instance = one shared pool).

    Cross-entry by construction: callers from any channel / client /
    backend build the same 6-col dedup key for the same logical request
    and meet on the same row.
    """

    _rows: dict = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self._rows)

    def claim(self, dedup: DedupKey, fingerprint: str) -> ClaimOutcome:
        """Claim one execution opportunity (synchronous check-and-set).

        First claim for a key => NEW (caller executes, then ``complete``).
        Same key+fingerprint while executing => IN_PROGRESS (409, retry the
        SAME key). Same key+fingerprint when done => REPLAY (200, original
        response, zero new work). Same key + different fingerprint =>
        CONFLICT (409). Quarantined row => ISOLATED (409).
        """
        key = _coerce_dedup(dedup)
        if not isinstance(fingerprint, str) or not fingerprint:
            raise PoolRejected(400, "invalid_request", "fingerprint", "Invalid request.")
        row = self._rows.get(key)
        if row is None:
            self._rows[key] = _Row(fingerprint=fingerprint, state=_STATE_EXECUTING)
            return ClaimOutcome(
                disposition=DISPOSITION_NEW,
                http_status=200,
                code="new",
                message="pool claim: first execution opportunity",
            )
        if row.state == _STATE_ISOLATED:
            return ClaimOutcome(
                disposition=DISPOSITION_ISOLATED,
                http_status=409,
                code="isolated",
                message="pool claim: row isolated",
            )
        if row.fingerprint != fingerprint:
            return ClaimOutcome(
                disposition=DISPOSITION_CONFLICT,
                http_status=409,
                code="conflict",
                message="pool claim: same key, different fingerprint",
            )
        if row.state == _STATE_EXECUTING:
            return ClaimOutcome(
                disposition=DISPOSITION_IN_PROGRESS,
                http_status=409,
                code="soul_busy",
                message="pool claim: same request already executing",
            )
        return ClaimOutcome(
            disposition=DISPOSITION_REPLAY,
            http_status=200,
            code="replay",
            response_text=row.response_text,
            message="pool claim: replay original response",
        )

    def complete(self, dedup: DedupKey, fingerprint: str, response_text: str) -> ClaimOutcome:
        """Mark the executing row done and store its response for replay."""
        key = _coerce_dedup(dedup)
        if not isinstance(fingerprint, str) or not fingerprint:
            raise PoolRejected(400, "invalid_request", "fingerprint", "Invalid request.")
        if not isinstance(response_text, str):
            raise PoolRejected(400, "invalid_request", "response_text", "Invalid request.")
        row = self._rows.get(key)
        if row is None or row.state != _STATE_EXECUTING:
            raise PoolRejected(409, "conflict", "Invalid request.")
        if row.fingerprint != fingerprint:
            raise PoolRejected(409, "conflict", "Invalid request.")
        row.state = _STATE_DONE
        row.response_text = response_text
        return ClaimOutcome(
            disposition=DISPOSITION_REPLAY,
            http_status=200,
            code="replay",
            response_text=response_text,
            message="pool complete: response stored",
        )

    def quarantine(self, dedup: DedupKey) -> None:
        """Isolate a row: later claims on it return ISOLATED (409)."""
        key = _coerce_dedup(dedup)
        row = self._rows.get(key)
        if row is None:
            self._rows[key] = _Row(fingerprint="", state=_STATE_ISOLATED)
        else:
            row.state = _STATE_ISOLATED
