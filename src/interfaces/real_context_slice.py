"""EI-2B offline vertical wiring: EI-1 auth + EI-2A journal + real Soul context.

One offline harness text-request flow. Genuine reuse only, no synthetic
Soul context, no re-implementation of persona / SAGE / prompt building:

  admit (EI-1: authenticate -> resolve_route -> authorize_route -> parse
         -> classify -> policy)
    -> ticket-scope fail-closed checks (single Owner principal,
       OWNER_PRIVATE, INTERACTIVE, single fixed user/soul scope)
    -> UNCERTAIN-fingerprint guard (fail-closed: an exact fingerprint
       that went UNCERTAIN is never re-executed; the fingerprint covers
       the idempotency key, so a fresh key is a new demand that runs once)
    -> journal claim/begin/generated/transport/outcome (EI-2A, untouched)
       with the stateful build + request-local mock generation running
       INSIDE the submit() fake callback, i.e. only on NEW
    -> genuine context: load_harness_persona -> get_scope_provider ->
       prefetch_scope_memory (per-scope source_pair_filter) ->
       build_harness_messages (genuine _build_messages_private over an
       isolated per-agent MemoryStore) -> complete_with_backend with the
       single registry-routed mock backend + route model

Auth model: agent identity, backend and model are bound server-side from
the registry route (resolve_route + authorize_route + route.agent_id vs
the adapter allowlist). The client only names a (soul_alias,
backend_alias) pair. This request type carries NO agent_id / user_id /
scope fields at all, so there is no client-supplied identity to trust.
The private-memory user_id is the single ticket-fixed OWNER_USER_ID and
is only used after proving principal_id == registry.owner_principal_id;
a principal_id is NEVER used directly as the memory user_id.

Journal semantics (EI-2A, unchanged):
  NEW -> exactly one generation. REPLAY -> stored ids, fake never runs
    (no context re-read, no regeneration). CONFLICT / IN_PROGRESS /
    EXPIRED -> returned as-is, never generated, never retried.
    UNCERTAIN -> fail-closed, exact fingerprint pinned so the identical
    retry will not re-execute. The fingerprint covers the idempotency key,
    so a fresh key is a new demand: it runs the fake exactly once, then
    pins UNCERTAIN again (one generation per demand, never auto-retried).
    Auth/scope failure raises
    BEFORE any journal call, so it writes no journal row.
  Ticket wording map: ticket "BUSY" == journal "IN_PROGRESS" (a live
  accepted/executing row for the same namespace). There is no in-slice
  lease in this module; concurrency beyond one one-shot process is
  SHARED_RUNTIME_BLOCKED.

Memory accounting honesty: the fake ALWAYS returns evidence None, so
submit() records the journal outcome as memory="pending", never
"completed". "Having obtained memory context" is therefore never
recorded as "memory persistence completed". This ticket performs NO
memory writeback: the slice never calls MemoryStore.append /
append_batch, never calls the SAGE writer, and never records a completed
memory outcome.

Async bridge: complete_with_backend is async while runtime_port.submit
takes a sync fake. In the supported isolated one-shot process there is
no running event loop, so the fake drives the single await with
asyncio.run() (same bridge the adapter acceptance tests use). If a
running loop IS detected, the fake refuses instead of nesting loops
(no nest_asyncio, no frozen-contract change) -> submit marks UNCERTAIN
fail-closed. A loop-nested caller that cannot be bridged is a procedural
blocker for that caller, reported as UNCERTAIN, not worked around here.

Isolation: ISOLATED_ONESHOT_ONLY. data_root is validated
(validate_request_data_root) BEFORE the journal claim, so an unsafe root
writes no journal row; build_harness_messages re-validates before the
first mkdir (defense in depth). SOUL_OS_DATA_DIR must be preset before
the process starts. No off-slice delivery
surface, no real provider / paid API / network, no production IO, no
global LLM backend mutation, no shared-runtime support.

Ticket: EI-2B-HARNESS-REAL-CONTEXT-OFFLINE-VERTICAL-WIRING-1.
Adds only this module + its acceptance test. No commit/push/merge.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .context_policy import admit as _admit
from .context_policy import idempotency_namespace
from .context_policy import request_fingerprint
from .runtime_port import FakeClock
from .runtime_port import submit
from .schema import InterfaceError
from .schema import MemoryPolicy
from .schema import Purpose
from .schema import Scope
from .soul_context_adapter import ISOLATED_ONESHOT_ONLY  # noqa: F401 -- contract re-export
from .soul_context_adapter import SHARED_RUNTIME_BLOCKED  # noqa: F401 -- contract re-export
from .soul_context_adapter import IsolationError
from .soul_context_adapter import build_harness_messages
from .soul_context_adapter import complete_with_backend
from .soul_context_adapter import get_scope_provider
from .soul_context_adapter import load_harness_persona
from .soul_context_adapter import prefetch_scope_memory
from .soul_context_adapter import resolve_agent_id
from .soul_context_adapter import validate_request_data_root

#: Single fixed private-memory user for this ticket. The ONLY value the
#: slice will ever pass as user_id to prefetch / prompt building, and
#: only after the Owner binding checks below have passed.
OWNER_USER_ID = "bryan"

#: Default session label for the isolated one-shot request.
DEFAULT_SESSION_ID = "rs1"


@dataclass(frozen=True)
class RealSliceRequest:
    """Client-supplied fields. Deliberately NO agent_id / user_id / scope.

    Identity comes only from the bearer token + the server-side registry
    route. There is no field for the client to assert who it is.
    """

    bearer_token: str
    soul_alias: str
    backend_alias: str
    raw_request: Mapping
    headers: Mapping
    request_id: str


@dataclass(frozen=True)
class RealSliceResponse:
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
    user_id: str
    session_id: str
    persona_chars: int = 0
    memory_context_chars: int = 0
    message_count: int = 0
    error_note: Optional[str] = None
    isolation: Optional[object] = None


class RealSliceDriver:
    """Owns in-slice coordination state: UNCERTAIN fingerprints only.

    No leases, no threads, no background work. One driver per isolated
    one-shot process; sharing a driver across processes/runtimes is
    SHARED_RUNTIME_BLOCKED (same rationale as the adapter singleton).
    """

    def __init__(self) -> None:
        self._fp_lock = threading.Lock()
        self._uncertain_fingerprints = set()

    def mark_uncertain_fp(self, fingerprint: str) -> None:
        with self._fp_lock:
            self._uncertain_fingerprints.add(fingerprint)

    def is_uncertain_fp(self, fingerprint: str) -> bool:
        with self._fp_lock:
            return fingerprint in self._uncertain_fingerprints


def default_memory_store_factory(resolved_root: Path, agent_id: str):
    """Isolated per-agent private MemoryStore under the request root.

    One sqlite file per agent_id, so private-history ownership follows
    the server-side agent identity, never a client claim. The file lives
    under the already-validated request root (temp data only).
    """
    from src.memory.store import MemoryStore

    mem_dir = resolved_root / "memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    return MemoryStore(db_path=mem_dir / f"{agent_id}.db")


def _refuse(code: str, param: Optional[str], message: str) -> InterfaceError:
    return InterfaceError(403, code, param, message)


def _resp(kind: str, decision=None, request_id: str = "",
          response_id: Optional[str] = None,
          body: Optional[str] = None, user_id: str = "",
          session_id: str = "", persona_chars: int = 0,
          memory_context_chars: int = 0, message_count: int = 0,
          error_note: Optional[str] = None,
          isolation: Optional[object] = None) -> RealSliceResponse:
    if decision is None:
        return RealSliceResponse(
            kind=kind, request_id=request_id, soul_alias="",
            agent_id="", client_id="", principal_id="",
            conversation_id=None, backend_alias="", public_model_id="",
            backend_ref="", response_id=response_id,
            response_body=body, memory_policy="",
            memory_evidence=None, user_id=user_id,
            session_id=session_id, persona_chars=persona_chars,
            memory_context_chars=memory_context_chars,
            message_count=message_count, error_note=error_note,
            isolation=isolation,
        )
    ctx = decision.context
    route = ctx.route
    policy = decision.memory_policy
    return RealSliceResponse(
        kind=kind, request_id=request_id or ctx.request_id,
        soul_alias=route.soul_alias, agent_id=route.agent_id,
        client_id=ctx.profile.client_id,
        principal_id=ctx.profile.principal_id,
        conversation_id=ctx.conversation_id,
        backend_alias=route.backend_alias,
        public_model_id=route.public_model_id,
        backend_ref=route.backend_ref, response_id=response_id,
        response_body=body,
        memory_policy=policy.value if hasattr(policy, "value")
        else str(policy),
        memory_evidence=None, user_id=user_id,
        session_id=session_id, persona_chars=persona_chars,
        memory_context_chars=memory_context_chars,
        message_count=message_count, error_note=error_note,
        isolation=isolation,
    )


def handle_text_request(
    *,
    driver: RealSliceDriver,
    registry,
    journal,
    clock: FakeClock,
    backends: Mapping,
    request: RealSliceRequest,
    data_root,
    owner_user_id: str = OWNER_USER_ID,
    session_id: str = DEFAULT_SESSION_ID,
    current_time: str = "",
    memory_store_factory: Optional[Callable[[Path, str], Any]] = None,
) -> RealSliceResponse:
    """Execute one offline real-context text request. Returns RealSliceResponse.

    Raises InterfaceError for auth/route/model/scope violations
    (fail-closed, no journal write) and IsolationError for unsafe data
    roots or unbridgeable async context (before-claim variants write no
    journal row; inside-fake variants become UNCERTAIN via submit()).
    Non-NEW journal outcomes are returned, never raised, never retried.
    """
    if not isinstance(driver, RealSliceDriver):
        raise InterfaceError(500, "invalid_driver", None, "Bad slice driver.")
    if not isinstance(clock, FakeClock):
        raise InterfaceError(500, "invalid_clock", None, "Bad slice clock.")
    if not isinstance(backends, Mapping) or len(backends) == 0:
        raise InterfaceError(500, "invalid_backends", None, "Bad backends.")
    if not isinstance(owner_user_id, str) or owner_user_id == "":
        raise InterfaceError(500, "invalid_user", None, "Bad owner user.")
    if not isinstance(session_id, str) or session_id == "":
        raise InterfaceError(500, "invalid_session", None, "Bad session.")

    # 1. EI-1 authentication + route authorization + classification.
    #    Any InterfaceError here propagates with NO journal write.
    decision = _admit(
        registry, request.bearer_token, request.soul_alias,
        request.backend_alias, request.raw_request, request.headers,
        request.request_id,
    )
    ctx = decision.context
    route = ctx.route
    profile = ctx.profile

    # 2. Ticket-scope fail-closed checks (all stateless, all pre-claim).
    #    Owner binding: the request is only honored when the
    #    authenticated principal IS the registry owner. The memory
    #    user_id below is the ticket-fixed OWNER_USER_ID, never the
    #    principal_id string itself.
    if profile.principal_id != registry.owner_principal_id:
        raise _refuse("owner_binding", "principal",
                      "Principal is not the registry Owner; refusing.")
    if profile.purpose != Purpose.INTERACTIVE:
        raise _refuse("purpose", "purpose",
                      "Only INTERACTIVE purpose is supported; refusing.")
    if profile.scope != Scope.OWNER_PRIVATE:
        raise _refuse("scope", "scope",
                      "Only OWNER_PRIVATE scope is supported; refusing.")
    if decision.memory_policy != MemoryPolicy.INTERACTIVE_CANDIDATE:
        raise _refuse("memory_policy", "headers",
                      "Only full interactive context (all persistence "
                      "headers present) is supported; utility/ephemeral "
                      "requests are refused fail-closed.")
    if owner_user_id != OWNER_USER_ID:
        raise _refuse("user_binding", "user",
                      "Only the single fixed ticket user is supported; "
                      "cross-user memory access is refused.")
    # Route forgery: the registry route's agent must be the adapter
    # allowlist agent for the requested soul_alias (unknown alias ->
    # IsolationError, also fail-closed).
    expected_agent = resolve_agent_id(request.soul_alias)
    if route.agent_id != expected_agent:
        raise _refuse("route_forgery", "route",
                      "Registry route agent does not match the soul "
                      "allowlist agent; refusing.")

    backend = backends.get(route.backend_ref)
    if backend is None:
        raise InterfaceError(500, "unknown_backend", "route",
                             "No injected backend for this route.")
    if not callable(getattr(backend, "complete", None)):
        raise InterfaceError(500, "invalid_backend", "route",
                             "Injected backend has no complete().")

    namespace = idempotency_namespace(ctx)
    fingerprint = request_fingerprint(decision)

    # 3. UNCERTAIN-fingerprint guard: an exact fingerprint that already
    #    went UNCERTAIN is never re-executed (no journal call). The
    #    fingerprint covers the idempotency key, so a fresh key is a new
    #    demand and runs the fake exactly once (then pins UNCERTAIN again).
    if driver.is_uncertain_fp(fingerprint):
        return _resp("UNCERTAIN", decision, ctx.request_id,
                     user_id=owner_user_id, session_id=session_id)

    # 4. Stateless data-root preflight BEFORE the claim (an unsafe root
    #    writes no journal row). The stateful build re-validates inside.
    resolved_root, _ = validate_request_data_root(data_root)

    factory = memory_store_factory or default_memory_store_factory
    state: Dict[str, Any] = {}

    def _fake():
        # Stateful context construction runs ONLY here, i.e. only on
        # NEW (submit calls fake strictly after a NEW claim + begin).
        # Any exception -> submit marks UNCERTAIN fail-closed.
        try:
            agent_id, soul = load_harness_persona(request.soul_alias)
            if agent_id != route.agent_id:
                raise IsolationError(
                    "persona agent mismatch mid-build; refusing.")
            provider = get_scope_provider(
                resolved_root, request.soul_alias,
                session_id=session_id)
            store = factory(resolved_root, agent_id)
            try:
                memory_context = prefetch_scope_memory(
                    provider, decision.current_user_text,
                    session_id=session_id, user_id=owner_user_id,
                    agent_id=agent_id)
                built = build_harness_messages(
                    soul_alias=request.soul_alias,
                    user_id=owner_user_id,
                    text=decision.current_user_text,
                    data_root=resolved_root,
                    memory_store=store,
                    memory_context=memory_context,
                    current_time=current_time)
                messages = built["messages"]
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    running = None
                else:
                    running = True
                if running is not None:
                    raise IsolationError(
                        "a running event loop is already active: the "
                        "sync journal port cannot safely nest "
                        "asyncio.run() inside it (SHARED_RUNTIME_BLOCKED, "
                        "no nested-loop workaround); fail-closed "
                        "UNCERTAIN.")
                body = asyncio.run(complete_with_backend(
                    messages, backend, route.public_model_id))
            finally:
                try:
                    store.close()
                except Exception:
                    pass
            state["persona_chars"] = len(soul)
            state["memory_context_chars"] = len(memory_context)
            state["message_count"] = len(messages)
            state["isolation"] = built["isolation"]
            # evidence is ALWAYS None: obtaining memory context is never
            # recorded as completed memory persistence (no writeback).
            return ("resp-" + ctx.request_id, body, None)
        except Exception as exc:  # noqa: BLE001 -- journal owns the mapping
            state["error_note"] = f"{type(exc).__name__}: {exc}"
            raise

    out = submit(journal, namespace, fingerprint, ctx.request_id,
                 _fake, clock)
    if out.kind == "UNCERTAIN":
        driver.mark_uncertain_fp(fingerprint)
        return _resp("UNCERTAIN", decision, out.request_id,
                     user_id=owner_user_id, session_id=session_id,
                     error_note=state.get("error_note"))
    if out.kind != "NEW":
        # REPLAY / CONFLICT / IN_PROGRESS(=ticket BUSY) / EXPIRED:
        # fake never ran (REPLAY proof: state stays empty).
        return _resp(out.kind, decision, out.request_id,
                     out.response_id, out.response_body,
                     user_id=owner_user_id, session_id=session_id)
    return _resp("NEW", decision, out.request_id,
                 out.response_id, out.response_body,
                 user_id=owner_user_id, session_id=session_id,
                 persona_chars=int(state.get("persona_chars", 0)),
                 memory_context_chars=int(
                     state.get("memory_context_chars", 0)),
                 message_count=int(state.get("message_count", 0)),
                 isolation=state.get("isolation"))
