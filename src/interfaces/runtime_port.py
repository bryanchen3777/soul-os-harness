"""Injectable fake runtime port (EI-2A). Pure stdlib only.

No IO, no network, no subprocess, no real LLM/Memory/Agency imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from src.interfaces.backend_execution import Decision
from src.interfaces.backend_execution import PortResult
from src.interfaces.backend_execution import PortError as _PortError


class FakeCancelled(Exception):
    pass


@dataclass
class FakeClock:
    now_s: float
    epoch: int

    def now(self) -> float:
        return self.now_s

    def advance(self, delta: float) -> float:
        self.now_s = float(self.now_s) + float(delta)
        return self.now_s

    def set_epoch(self, epoch: int) -> None:
        self.epoch = epoch


@dataclass(frozen=True)
class SubmitResult:
    kind: str
    request_id: str
    response_id: Optional[str] = None
    response_body: Optional[str] = None


class FakeRuntime:
    def __init__(self) -> None:
        self.calls = []
        self._mode = "ok"
        self._response_id = "resp-1"
        self._body = "body-1"
        self._evidence = "ev-1"
        self._exc = RuntimeError("fake failure")
        self._side_effect = False

    def ok(self, response_id, body, memory_evidence):
        self._mode = "ok"
        self._response_id = response_id
        self._body = body
        self._evidence = memory_evidence
        return self

    def raise_at(self, side_effect: bool, exc):
        self._mode = "raise"
        self._side_effect = bool(side_effect)
        self._exc = exc
        return self

    def cancel(self):
        self._mode = "cancel"
        return self

    @property
    def side_effect(self) -> bool:
        return self._side_effect

    def __call__(self):
        self.calls.append(len(self.calls) + 1)
        if self._mode == "ok":
            return (self._response_id, self._body, self._evidence)
        if self._mode == "cancel":
            raise FakeCancelled("cancelled")
        raise self._exc


def _fail_closed_uncertain(journal, request_id) -> None:
    try:
        journal.mark_uncertain(request_id)
    except Exception:
        pass


def _is_clean_failure(fake, exc) -> bool:
    """True only for a PROVEN side-effect-free failure (retryable).

    Refusal priority (Q3): FakeCancelled, explicit ambiguity, or any
    side-effect evidence seals the retry first. Otherwise a permit needs
    either a formal ``PortError(ambiguous=False)`` or a controlled
    ``FakeRuntime`` whose ``side_effect is False``. A missing attribute is
    never treated as False: unknown callables stay fail-closed.
    """
    if isinstance(exc, FakeCancelled):
        return False
    if isinstance(exc, _PortError):
        if exc.ambiguous is not False:
            return False
        # Formal clean proof — but explicit side-effect evidence on the
        # fake still wins and seals the retry.
        if getattr(fake, "side_effect", None) is True:
            return False
        return True
    if getattr(fake, "side_effect", None) is True:
        return False
    if getattr(fake, "side_effect", None) is False:
        return True
    return False


def _settle_success(journal, rid, epoch, out) -> SubmitResult:
    try:
        response_id = out[0]
        body = out[1]
        evidence = out[2]
    except Exception:
        _fail_closed_uncertain(journal, rid)
        return SubmitResult("UNCERTAIN", rid)
    journal.record_generated(rid, epoch, response_id, body)
    journal.record_transport(rid, epoch)
    if evidence is None:
        journal.record_outcome(rid, epoch, "memory", "pending")
    elif isinstance(evidence, str) and evidence == "":
        journal.record_outcome(rid, epoch, "memory", "pending")
    else:
        journal.record_outcome(rid, epoch, "memory", "completed", evidence)
    return SubmitResult("NEW", rid, response_id, body)


def _fail_clean_or_seal(journal, rid, epoch, fake, exc) -> SubmitResult:
    """Fail-closed mark + single-use clean-retry permit (Q3).

    First failure or retry failure share this path: the row is moved to
    uncertain, and ONLY a proven-clean failure stores a permit on the same
    Journal instance (memory only, bound to stored request_id / epoch /
    failed attempt, never to outcomes).
    """
    try:
        attempt = journal._next_submit_attempt(rid)
    except Exception:
        attempt = 0
    clean = _is_clean_failure(fake, exc)
    try:
        journal.mark_uncertain(rid)
    except Exception:
        return SubmitResult("UNCERTAIN", rid)
    if clean:
        try:
            journal._grant_clean_retry(rid, epoch, attempt)
        except Exception:
            pass
    return SubmitResult("UNCERTAIN", rid)


def _submit_uncertain_retry(journal, stored_rid, fake, clock):
    """One permitted retry off an UNCERTAIN claim (Q3).

    The permit must live on this same Journal instance, the clock epoch
    must still match the journal epoch, and try_consume_clean_retry must
    CAS the row uncertain -> executing. Only then is the runtime called —
    never begin. Any miss answers UNCERTAIN without touching the runtime.
    """
    try:
        epoch_ok = (clock.epoch == journal.epoch)
    except Exception:
        return SubmitResult("UNCERTAIN", stored_rid)
    if not epoch_ok:
        return SubmitResult("UNCERTAIN", stored_rid)
    consume = getattr(journal, "try_consume_clean_retry", None)
    if consume is None:
        return SubmitResult("UNCERTAIN", stored_rid)
    try:
        allowed = consume(stored_rid)
    except Exception:
        return SubmitResult("UNCERTAIN", stored_rid)
    if allowed is not True:
        return SubmitResult("UNCERTAIN", stored_rid)
    # Consumed: the row is executing in our epoch. Call the runtime
    # directly (no begin — the row is past accepted) and settle.
    try:
        out = fake()
    except Exception as exc:
        return _fail_clean_or_seal(journal, stored_rid, journal.epoch, fake, exc)
    return _settle_success(journal, stored_rid, journal.epoch, out)


def submit(journal, namespace, fingerprint, request_id, fake, clock):
    try:
        res = journal.claim(namespace, fingerprint, request_id, clock.now())
    except Exception as exc:
        # Shared-connection contention (Q3 concurrency): two threads on one
        # Journal/connection race BEGIN IMMEDIATE and the loser surfaces as
        # JournalError("claim failed") — the row is in-flight elsewhere.
        # Fail closed as IN_PROGRESS without touching the runtime.
        # Input-validation JournalErrors ("bad ...") and ownership errors
        # still propagate. Name + message match avoids importing journal
        # (keeps the boundary import graph unchanged).
        if type(exc).__name__ == "JournalError" and str(exc) == "claim failed":
            return SubmitResult("IN_PROGRESS", request_id)
        raise
    if res.kind == "UNCERTAIN":
        return _submit_uncertain_retry(journal, res.request_id, fake, clock)
    if res.kind != "NEW":
        return SubmitResult(res.kind, res.request_id, res.response_id, res.response_body)
    # Immutable request identity: from here on only the stored id is used,
    # so a caller-supplied id can never split ownership of one journal row.
    rid = res.request_id
    try:
        journal.begin(rid, clock.epoch)
    except Exception:
        try:
            journal.mark_failed_before_execution(rid, clock.epoch)
        except Exception:
            pass
        raise
    try:
        out = fake()
    except Exception as exc:
        return _fail_clean_or_seal(journal, rid, clock.epoch, fake, exc)
    return _settle_success(journal, rid, clock.epoch, out)
