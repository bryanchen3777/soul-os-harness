"""EI-2B backend execution evidence vocabulary (pure types, no IO).

Decision-complete work order EI-2B-BACKEND-SETTLE-1: separates backend
completion evidence from journal durable state. This module holds ONLY the
in-memory evidence vocabulary:

- ``Decision``: the single input a backend port may act on
  (``operation`` + opaque ``payload``). It carries no destination and no
  memory evidence: a port is authorized by receiving the object, never by a
  caller-supplied ``"completed"`` boolean.
- ``PortResult``: what a port returns (fixed ``response_id`` /
  ``response_body`` + ``completed`` flag). Only a real port return creates
  completion evidence; callers cannot submit one directly.
- ``PortError``: an explicit backend failure with an ``ambiguous`` flag.
  ``ambiguous=False`` is a proven side-effect-free failure (retryable);
  ``ambiguous=True`` means the side-effect fate is unknown (isolate).
  Any other exception is general failure: never retryable, never FAILED.
- ``BackendRecord``: one request's memory-only evidence, bound to
  (capability, epoch, request, attempt). Never persisted: a restart drops
  it and recovery must conservatively isolate, never re-release.
- ``BACKEND_*`` state labels: the memory evidence register
  (running / retryable / completed / failed / cancelled / unknown).

Pure stdlib (``dataclasses`` + ``typing`` only). No IO, no network, no
subprocess, no clock, no environment access at import or at runtime. The
three lifecycle APIs (``submit_backend`` / ``retry_backend`` /
``cancel_backend``) and the settle table live in ``coordinator.py``, which
owns the journal, the lease store, and the private memory ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

#: Memory evidence register. These labels live ONLY in the coordinator's
#: private memory ledger; they are never written to the journal (upstream
#: ``_VALID_STATES`` has no completed / failed / cancelled states, §9 gap
#: G1/G2) and never to any other table.
BACKEND_RUNNING = "running"
BACKEND_RETRYABLE = "retryable"
BACKEND_COMPLETED = "completed"
BACKEND_FAILED = "failed"
BACKEND_CANCELLED = "cancelled"
BACKEND_UNKNOWN = "unknown"

BACKEND_TERMINAL = (BACKEND_COMPLETED, BACKEND_FAILED, BACKEND_CANCELLED)


@dataclass(frozen=True)
class Decision:
    """The single input a backend port may act on.

    ``operation`` names the backend operation; ``payload`` is opaque
    operation input. Deliberately destination-free: the port decides where
    (if anywhere) bytes go, the caller never names a destination. There is
    no ``completed`` field: completion evidence comes only from a real
    ``PortResult`` returned by the port, never from caller input.
    """

    operation: str
    payload: Any = None

    def __post_init__(self) -> None:
        if not isinstance(self.operation, str) or not self.operation.strip():
            raise ValueError("operation must be a non-empty string.")


@dataclass(frozen=True)
class PortResult:
    """What a backend port returns on one ``execute`` call.

    ``completed=True`` means the backend finished AND fixed its response;
    only then (plus a successful durable ``record_generated`` of the fixed
    response) does the coordinator log COMPLETED evidence. ``completed=False``
    states nothing about side-effect fate, so it maps to UNKNOWN, never to
    retryable and never to FAILED.
    """

    response_id: str
    response_body: str
    completed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.response_id, str) or not self.response_id:
            raise ValueError("response_id must be a non-empty string.")
        if not isinstance(self.response_body, str):
            raise ValueError("response_body must be a string.")
        if not isinstance(self.completed, bool):
            raise ValueError("completed must be a bool, not a truthy value.")


@dataclass(frozen=True)
class PortError(Exception):
    """An explicit backend failure with a side-effect-fate flag.

    - ``ambiguous=False``: the port proves the call had NO side effect
      (clean failure) -> RETRYABLE, the lease is kept.
    - ``ambiguous=True``: the side-effect fate is unknown -> UNKNOWN,
      fail-closed isolation. The caller abandoning a retry is the ONLY
      path to FAILED, and only from RETRYABLE (already proven clean).

    Any non-``PortError`` exception from a port is general failure: it
    proves nothing, so it maps to UNKNOWN — never to RETRYABLE (no retry)
    and never to FAILED.
    """

    ambiguous: bool

    def __post_init__(self) -> None:
        if not isinstance(self.ambiguous, bool):
            raise ValueError("ambiguous must be a bool, not a truthy value.")


@dataclass
class BackendRecord:
    """One request's memory-only backend evidence.

    Bound at admission (``begin`` success -> RUNNING) to the winning
    capability's ``(soul_alias, generation)``, the context ``epoch``, and
    the journal ``request_id``; ``attempt`` fences retries (admission binds
    0, each port call pre-increments, so a duplicate/parallel call with a
    stale attempt is refused with zero resend). ``decision`` / ``port`` are
    stored at submit so ``retry`` replays the SAME call — the caller never
    supplies a new result. Terminal records auto-seal (``sealed=True``):
    no new submit, retry, permit, or dispatch may touch a sealed request.
    """

    soul_alias: str
    request_id: str
    generation: int
    epoch: int
    attempt: int = 0
    state: str = BACKEND_RUNNING
    sealed: bool = False
    response_id: Optional[str] = None
    response_body: Optional[str] = None
    decision: Any = None
    port: Any = None

    def is_terminal(self) -> bool:
        """True once the evidence reached a terminal label."""
        return self.state in BACKEND_TERMINAL
