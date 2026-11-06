"""Minimal in-process harness driver (EI-2B API-only isolated slice).

Pipeline (all injected, no IO of its own):

  admit (EI-1: auth -> route -> parse -> classify -> policy)
    -> in-slice per-(principal, agent) non-blocking lease (BUSY scope: THIS
       slice only; makes NO claim about shared-runtime full-ingress mutex)
    -> UNCERTAIN-fingerprint guard (fail-closed: a fingerprint that went
       UNCERTAIN is never blindly re-executed, even under a new key)
    -> journal claim/begin/generated/transport/outcome (EI-2A, untouched)
    -> stub backend generation over synthetic soul context

Auth model: soul identity is bound server-side from the registry route
(resolve_route + authorize_route). The client only names a (soul_alias,
backend_alias) pair; a pair outside its allowed_routes is rejected 403,
so a client can never forge another soul/scope.

No HTTP server, no Gateway/Telegram/Voice/Consciousness/SAGE/EventBus
imports, no real LLM, no production data. Pure stdlib only.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Dict, Optional

from .context_policy import admit as _admit
from .context_policy import idempotency_namespace
from .context_policy import request_fingerprint
from .runtime_port import FakeClock
from .runtime_port import submit
from .schema import InterfaceError


@dataclass(frozen=True)
class SliceRequest:
    bearer_token: str
    soul_alias: str
    backend_alias: str
    raw_request: dict
    headers: dict
    request_id: str


@dataclass(frozen=True)
class SliceResponse:
    kind: str
    request_id: str
    soul_alias: str
    agent_id: str
    client_id: str
    principal_id: str
    conversation_id: Optional[str]
    backend_alias: str
    public_model_id: str
    backend_ref: str
    response_id: Optional[str]
    response_body: Optional[str]
    memory_policy: str
    memory_evidence: Optional[str]
    persona_source: Optional[str]
    persona_sha256: Optional[str]


class SliceLeaseSet:
    """In-slice-only active-execution leases. NOT shared-runtime mutex."""

    def __init__(self) -> None:
        self._meta = threading.Lock()
        self._locks: Dict[tuple, threading.Lock] = {}

    def _for(self, key):
        with self._meta:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
            return lock

    def try_acquire(self, key) -> bool:
        return self._for(key).acquire(blocking=False)

    def release(self, key) -> None:
        try:
            self._for(key).release()
        except RuntimeError:
            pass

    @contextmanager
    def held(self, key):
        ok = self.try_acquire(key)
        if not ok:
            raise InterfaceError(409, "busy", None, "Slice lease is held.")
        try:
            yield
        finally:
            self.release(key)


class SliceDriver:
    """Owns in-slice coordination state (leases + uncertain fingerprints)."""

    def __init__(self) -> None:
        self.leases = SliceLeaseSet()
        self._uncertain_fingerprints = set()
        self._fp_meta = threading.Lock()

    def mark_uncertain_fp(self, fingerprint: str) -> None:
        with self._fp_meta:
            self._uncertain_fingerprints.add(fingerprint)

    def is_uncertain_fp(self, fingerprint: str) -> bool:
        with self._fp_meta:
            return fingerprint in self._uncertain_fingerprints


def _resp(kind, decision=None, request_id="", response_id=None,
          body=None, evidence=None, provenance=None):
    if decision is None:
        return SliceResponse(
            kind=kind, request_id=request_id, soul_alias="",
            agent_id="", client_id="", principal_id="",
            conversation_id=None, backend_alias="", public_model_id="",
            backend_ref="", response_id=response_id,
            response_body=body, memory_policy="",
            memory_evidence=evidence, persona_source=None,
            persona_sha256=None,
        )
    ctx = decision.context
    route = ctx.route
    return SliceResponse(
        kind=kind, request_id=request_id or ctx.request_id,
        soul_alias=route.soul_alias, agent_id=route.agent_id,
        client_id=ctx.profile.client_id,
        principal_id=ctx.profile.principal_id,
        conversation_id=ctx.conversation_id,
        backend_alias=route.backend_alias,
        public_model_id=route.public_model_id,
        backend_ref=route.backend_ref, response_id=response_id,
        response_body=body,
        memory_policy=decision.memory_policy.value
        if hasattr(decision.memory_policy, "value")
        else str(decision.memory_policy),
        memory_evidence=evidence,
        persona_source=provenance.persona_source if provenance else None,
        persona_sha256=provenance.persona_sha256 if provenance else None,
    )


def handle_text_request(*, driver, registry, journal, clock,
                        soul_store, stub_backend, request: SliceRequest):
    """Execute one harness-style text request. Returns SliceResponse.

    Raises InterfaceError for auth/route/model violations (fail-closed).
    BUSY / REPLAY / CONFLICT / IN_PROGRESS / UNCERTAIN / EXPIRED are
    returned as response kinds, never raised, never auto-retried.
    """
    if not isinstance(clock, FakeClock):
        raise InterfaceError(500, "invalid_clock", None, "Bad slice clock.")
    decision = _admit(
        registry, request.bearer_token, request.soul_alias,
        request.backend_alias, request.raw_request, request.headers,
        request.request_id,
    )
    ctx = decision.context
    namespace = idempotency_namespace(ctx)
    fingerprint = request_fingerprint(decision)
    lease_key = (ctx.profile.principal_id, ctx.route.agent_id)

    if driver.is_uncertain_fp(fingerprint):
        return _resp("UNCERTAIN", decision, ctx.request_id)

    if not driver.leases.try_acquire(lease_key):
        return _resp("BUSY", decision, ctx.request_id)
    try:
        if driver.is_uncertain_fp(fingerprint):
            return _resp("UNCERTAIN", decision, ctx.request_id)

        fixture, provenance = soul_store.load_context(ctx.route.agent_id)
        policy_name = (
            decision.memory_policy.value
            if hasattr(decision.memory_policy, "value")
            else str(decision.memory_policy)
        )

        state: dict = {}

        def _fake():
            gen = stub_backend.generate(
                agent_id=ctx.route.agent_id,
                backend_ref=ctx.route.backend_ref,
                public_model_id=ctx.route.public_model_id,
                user_text=decision.current_user_text,
                fixture=fixture, provenance=provenance,
                memory_policy=policy_name,
            )
            state["gen"] = gen
            return (gen.response_id, gen.body, gen.memory_evidence)

        out = submit(journal, namespace, fingerprint, ctx.request_id,
                     _fake, clock)
        if out.kind == "UNCERTAIN":
            driver.mark_uncertain_fp(fingerprint)
            return _resp("UNCERTAIN", decision, out.request_id)
        if out.kind != "NEW":
            return _resp(out.kind, decision, out.request_id,
                         out.response_id, out.response_body)
        gen = state.get("gen")
        return _resp("NEW", decision, out.request_id,
                     out.response_id, out.response_body,
                     gen.memory_evidence if gen else None,
                     gen.provenance if gen else None)
    finally:
        driver.leases.release(lease_key)
