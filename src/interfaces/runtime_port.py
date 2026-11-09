"""Injectable fake runtime port (EI-2A). Pure stdlib only.

No IO, no network, no subprocess, no real LLM/Memory/Agency imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


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


def submit(journal, namespace, fingerprint, request_id, fake, clock):
    res = journal.claim(namespace, fingerprint, request_id, clock.now())
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
    except Exception:
        _fail_closed_uncertain(journal, rid)
        return SubmitResult("UNCERTAIN", rid)
    try:
        response_id = out[0]
        body = out[1]
        evidence = out[2]
    except Exception:
        _fail_closed_uncertain(journal, rid)
        return SubmitResult("UNCERTAIN", rid)
    journal.record_generated(rid, clock.epoch, response_id, body)
    journal.record_transport(rid, clock.epoch)
    if evidence is None:
        journal.record_outcome(rid, clock.epoch, "memory", "pending")
    elif isinstance(evidence, str) and evidence == "":
        journal.record_outcome(rid, clock.epoch, "memory", "pending")
    else:
        journal.record_outcome(rid, clock.epoch, "memory", "completed", evidence)
    return SubmitResult("NEW", rid, response_id, body)
