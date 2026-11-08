"""EI-2A fake runtime port. Explicit evidence, no text parsing, no IO.

Contract (work order EI-2A-JOURNAL-PORT-1, decision-complete):

- ``submit()`` calls the injected port only after a successful ``begin()``:
  the entry must be executing with the matching epoch, otherwise the port
  is never touched (replay / uncertain / terminal / not-executing paths).
- The fake port returns explicit evidence (``PortResult``) or raises an
  explicit ``PortError``; nothing is parsed out of text.
- A backend error whose side effects cannot be proven absent marks the
  entry uncertain (same-epoch ``mark_uncertain``); a proven side-effect-free
  error (``ambiguous=False``) leaves the entry executing for an explicit
  caller-driven retry. An unknown exception is treated as ambiguous.
- A cancelled request never reuses its original execution opportunity.
- This ticket wires no backend/bus adapter: only the injected fake port.
- All data and clock come from the caller; import starts no runtime.
  ``FakeRuntime`` performs no network / subprocess / server IO.

Pure stdlib. Importing this module has no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from .journal import DESTINATIONS
from .journal import Journal
from .journal import OUTCOME_EXPIRED  # noqa: F401  (re-exported outcome label)
from .journal import OUTCOME_IN_PROGRESS  # noqa: F401
from .journal import OUTCOME_NEW  # noqa: F401
from .journal import OUTCOME_REPLAY  # noqa: F401
from .journal import OUTCOME_UNCERTAIN  # noqa: F401


#: submit() outcome labels.
SUBMIT_OK = "OK"
SUBMIT_REPLAY = "REPLAY"
SUBMIT_UNCERTAIN = "UNCERTAIN"
SUBMIT_CONFLICT = "CONFLICT"
SUBMIT_NOT_EXECUTING = "NOT_EXECUTING"
SUBMIT_TERMINAL = "TERMINAL"
SUBMIT_NO_CLAIM = "NO_CLAIM"
SUBMIT_RETRYABLE = "RETRYABLE"


@dataclass(frozen=True)
class Decision:
    """Opaque caller decision handed to the port. The port treats the
    payload as opaque bytes; nothing is parsed out of it."""

    operation: str
    payload: str = ""


@dataclass(frozen=True)
class PortResult:
    """Explicit backend evidence. ``completed`` is the explicit
    same-request completion evidence consumed by ``record_outcome``."""

    response_id: str
    response_body: str
    completed: bool = True
    destination: str = "backend"


class PortError(Exception):
    """Explicit backend failure.

    ``ambiguous=True`` (default): side effects cannot be proven absent, so
    the entry must go uncertain. ``ambiguous=False``: proven to have happened
    before any side effect, so the entry stays executing for an explicit
    caller-driven retry.
    """

    def __init__(self, message: str = "backend failed", *, ambiguous: bool = True):
        super().__init__(message)
        self.ambiguous = bool(ambiguous)


@dataclass(frozen=True)
class SubmitResult:
    outcome: str
    request_id: Optional[str]
    response_id: Optional[str]
    response_body: Optional[str]
    status: int
    code: str
    completed: bool = False


class FakeRuntime:
    """Deterministic in-memory fake port. Records every call; performs no IO.

    ``script`` is a non-empty list of ``PortResult`` / ``PortError`` items
    consumed in order; once exhausted, the last item repeats so a fixed
    script stays deterministic under retries.
    """

    def __init__(self, script: list) -> None:
        items = list(script)
        if not items:
            raise ValueError("script must be a non-empty list.")
        for item in items:
            if not isinstance(item, (PortResult, PortError)):
                raise TypeError("script items must be PortResult or PortError.")
        self._script = items
        self.calls: List[Decision] = []

    def execute(self, decision: Decision) -> PortResult:
        if not isinstance(decision, Decision):
            raise TypeError("decision must be a Decision.")
        self.calls.append(decision)
        item = self._script[min(len(self.calls) - 1, len(self._script) - 1)]
        if isinstance(item, PortError):
            raise PortError(str(item), ambiguous=item.ambiguous)
        return item

    @property
    def call_count(self) -> int:
        return len(self.calls)


def submit(
    journal: Journal,
    *,
    namespace: str,
    request_id: str,
    epoch: int,
    decision: Decision,
    port: FakeRuntime,
    now: float,
) -> SubmitResult:
    """Submit a begun claim to the injected port.

    The port is called only when the journal entry is executing with the
    matching epoch. Every other state returns without touching the port:
    generated/completed replays the fixed response, uncertain stays
    uncertain, terminal stays terminal.
    """
    if not isinstance(journal, Journal):
        raise TypeError("journal must be a Journal.")
    if not isinstance(decision, Decision):
        raise TypeError("decision must be a Decision.")
    if not hasattr(port, "execute"):
        raise TypeError("port must expose execute(decision).")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    stamp = float(now)

    entry = journal.load(namespace)
    if entry is None:
        return SubmitResult(
            SUBMIT_NO_CLAIM, request_id, None, None, 409, "journal_no_claim")
    if entry["request_id"] != request_id:
        return SubmitResult(
            SUBMIT_CONFLICT, entry["request_id"], None, None,
            409, "idempotency_conflict")
    state = entry["state"]
    if state in ("generated", "completed"):
        return SubmitResult(
            SUBMIT_REPLAY, entry["request_id"], entry["response_id"],
            entry["response_body"], 200, "idempotency_replay",
            completed=(state == "completed"))
    if state == "uncertain":
        return SubmitResult(
            SUBMIT_UNCERTAIN, entry["request_id"], None, None,
            409, "idempotency_uncertain")
    if state in ("failed", "cancelled"):
        return SubmitResult(
            SUBMIT_TERMINAL, entry["request_id"], None, None,
            409, "journal_terminal")
    if state != "executing" or entry["epoch"] != epoch:
        return SubmitResult(
            SUBMIT_NOT_EXECUTING, entry["request_id"], None, None,
            409, "journal_not_executing")

    # Executing with the matching epoch: the ONLY path that touches the port.
    try:
        result = port.execute(decision)
    except PortError as exc:
        if exc.ambiguous:
            journal.mark_uncertain(request_id, epoch)
            return SubmitResult(
                SUBMIT_UNCERTAIN, request_id, None, None,
                409, "idempotency_uncertain")
        return SubmitResult(
            SUBMIT_RETRYABLE, request_id, None, None,
            502, "backend_retryable")
    except Exception:
        # Unclassifiable failure: side effects cannot be proven absent.
        journal.mark_uncertain(request_id, epoch)
        return SubmitResult(
            SUBMIT_UNCERTAIN, request_id, None, None,
            409, "idempotency_uncertain")
    if not isinstance(result, PortResult):
        raise TypeError("port must return PortResult or raise PortError.")
    if result.destination not in DESTINATIONS:
        raise ValueError("unknown result destination: %r." % (result.destination,))

    fixed = journal.record_generated(
        request_id, epoch, result.response_id, result.response_body)
    if not fixed:  # pragma: no cover - lost a same-epoch race
        return SubmitResult(
            SUBMIT_NOT_EXECUTING, request_id, None, None,
            409, "journal_not_executing")
    journal.record_transport(
        request_id, result.destination, True,
        {"response_id": result.response_id, "operation": decision.operation},
        stamp,
    )
    completed = False
    if result.destination == "backend" and result.completed:
        journal.record_outcome(
            request_id, "backend", "ok", {"completed": True}, stamp)
        completed = True
    else:
        # Memory/bus evidence (or an incomplete backend result) is stored
        # as evidence only and can never impersonate a backend commit.
        journal.record_outcome(
            request_id, result.destination, "ok",
            {"completed": bool(result.completed)}, stamp)
    return SubmitResult(
        SUBMIT_OK, request_id, result.response_id, result.response_body,
        200, "submit_ok", completed=completed)


__all__ = [
    "Decision",
    "PortResult",
    "PortError",
    "FakeRuntime",
    "SubmitResult",
    "submit",
    "SUBMIT_OK",
    "SUBMIT_REPLAY",
    "SUBMIT_UNCERTAIN",
    "SUBMIT_CONFLICT",
    "SUBMIT_NOT_EXECUTING",
    "SUBMIT_TERMINAL",
    "SUBMIT_NO_CLAIM",
    "SUBMIT_RETRYABLE",
]
