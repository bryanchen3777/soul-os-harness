"""EI-2B per-soul admission coordinator (caller-owned stores, caller-owned clock).

Contract (work order EI-2B-PROGRAM-3, decision-complete; reverses the
PROGRAM-2 TTL-takeover half, keeps B4 / B5 / B6 / B7):

- Mutex truth lives in the ``soul_leases`` table
  (``src/interfaces/soul_lease.py``): persistent FREE / HELD /
  QUARANTINED rows, ``generation`` + 1 on every FREE -> HELD, identity
  by capability ``(alias, holder_token, request_id, generation)``.
  Expiry never takes over; death needs verified evidence.
- ``admit_entry`` runs in two steps: (1) acquire the soul lease by
  capability, (2) claim the idempotency row (``claim`` + CAS ``begin``).
  Only the winner of BOTH steps gets ``should_publish=True``. A lease
  won and then lost at the claim is released only with proven
  quiescence (never held what we will not use, never freed what may
  still be working).
- Claim points sit at each entry's first persistent side effect boundary:
  parse success, before the first ``bus.publish``. Grant checks happen only
  there (deadlock ban: no handler ever waits on a grant).
- Segmented commit (work order §5): lease -> claim -> begin run as
  three independent operations, never wrapped in one outer journal
  transaction. Claim-before-begin keeps the accepted row + isolation
  evidence; begin-before-publish goes uncertain, never auto-replays.
  Commit exceptions re-read before compensating; ambiguous states are
  never compensated into the wrong place.
- Settle table (work order §6, ``settle_request``): not-started +
  proven-no-side-effect -> release; executing / generated -> held;
  transport_sent (send fate unknown) / uncertain / crash -> isolate.
  No backend-terminal release exists (upstream has no completed / failed
  / cancelled states). Never release on finally, timeout, or outcome alone.
  Backend-terminal release exists ONLY on private memory evidence
  (EI-2B-BACKEND-SETTLE-1 ``backend_execution`` ledger: never persisted,
  never replayed across restarts — recovery conservatively isolates).
- Feature flag ``SOUL_OS_PER_SOUL_MUTEX`` defaults OFF. When off, entries
  must not call this module at all (old behaviour byte-identical); see
  ``coordinator_enabled()`` and the static compat guard in tests.
- 409 busy carries the holder identity CLASS (entry kind + lease epoch,
  never a token) plus ``Retry-After: 1``. Retry policy: retry the SAME key
  and wait for IN_PROGRESS -> REPLAY; mint a fresh key only on CONFLICT,
  EXPIRED, or reconciled uncertain.
- Storage errors are never leaked (mapped to UNCERTAIN, never to success).
- Background writes never go through the coordinator; ``mark_non_request``
  labels them so they cannot be mistaken for request outcomes. Lifecycle
  evaluation takes no lease (B3).
- Restart: ``recover()`` marks old-epoch executing journal rows
  uncertain (evidence kept, ids counted not logged); lease rows are
  verified holder-by-holder (``open_admission_context``): the live stay
  held, the unknown go QUARANTINED, and only the proven-dead + quiesced
  go FREE — each freed holder's journal row then goes single-parameter
  ``mark_uncertain`` (never blanket).

Pure stdlib. No network, no subprocess, no server, no env access at import.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple

from src.interfaces.journal import (
    Journal,
    JournalError,
    OwnershipError,
)
from src.interfaces.backend_execution import (
    BACKEND_CANCELLED,
    BACKEND_COMPLETED,
    BACKEND_FAILED,
    BACKEND_RETRYABLE,
    BACKEND_RUNNING,
    BACKEND_TERMINAL,
    BACKEND_UNKNOWN,
    BackendRecord,
    Decision,
    PortError,
    PortResult,
)
from src.interfaces.soul_lease import (
    LEASE_DEFAULT_TTL_SECONDS,
    LIVENESS_ALIVE,
    LIVENESS_DEAD,
    LIVENESS_UNKNOWN,
    STATE_FREE as LEASE_FREE,
    STATE_HELD as LEASE_HELD,
    STATE_QUARANTINED as LEASE_QUARANTINED,
    AcquireResult,
    Capability,
    LeaseResult,
    Liveness,
    SoulLeaseError,
    SoulLeaseStore,
    own_process_create_time,
    query_process_liveness,
)

logger = logging.getLogger("soul_os.coordinator")

#: Feature flag env var. Default OFF: any value other than an explicit
#: opt-in ("1"/"true"/"yes"/"on", case-insensitive) means disabled.
FLAG_ENV_VAR = "SOUL_OS_PER_SOUL_MUTEX"

#: Entry kinds allowed to hold a soul lease. Background work is
#: deliberately absent: it never leases (B3/B4).
ENTRY_KINDS = ("http", "web", "tg", "voice")

#: Retry-After seconds for every 409 busy body (B5).
RETRY_AFTER_SECONDS = 1

#: Journal namespace prefix. The soul alias is always embedded.
NAMESPACE_PREFIX = "soul-lease/"

#: Marker placed on background writes so they never mix into a request
#: outcome (B3/B4 attribution table).
NON_REQUEST_MARK = "non_request_write"

#: Request identity for internal entries (work order §4 key rule): every
#: publish opportunity mints a fresh id, retry is a new request.
REQUEST_ID_PREFIX = "in-"


@dataclass(frozen=True)
class RequestIdentity:
    """One publish opportunity's identity (fresh per attempt, never reused).

    ``request_id`` names the lease holder; ``idempotency_key`` scopes the
    journal row. Internal entries mint both fresh per opportunity; a retry
    is a new request (ordered by the lease, never concurrent).
    """

    request_id: str
    idempotency_key: str


def mint_request_identity() -> RequestIdentity:
    """Mint a fresh request identity (uuid4 hex, no I/O, no clock)."""
    return RequestIdentity(
        request_id="%s%s" % (REQUEST_ID_PREFIX, uuid.uuid4().hex),
        idempotency_key="%s%s" % (REQUEST_ID_PREFIX, uuid.uuid4().hex),
    )


def fingerprint_for_text(text: str) -> str:
    """Stable fingerprint for entry text (sha256 over stripped text)."""
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BusyBody:
    """409 busy payload. Holder identity CLASS only: entry kind + lease
    epoch. Never carries a bearer token or digest."""

    status: int = 409
    code: str = "soul_busy"
    holder_entry: str = "unknown"
    holder_epoch: int = 0
    retry_after: int = RETRY_AFTER_SECONDS


@dataclass(frozen=True)
class AdmissionOutcome:
    """Result of one per-soul admission attempt at an entry claim point."""

    disposition: str
    http_status: int
    code: str
    should_publish: bool
    busy: Optional[BusyBody] = None
    fixed_response_id: Optional[str] = None
    fixed_response_body: Optional[str] = None


def busy_frame(outcome: AdmissionOutcome) -> dict:
    """Render a 409 busy outcome as a wire frame (WS busy frame).

    Holder identity class only (entry kind + lease epoch); never a token,
    never a digest. Always carries ``retry_after`` = 1 (§8: entries reply
    busy with retry_after=1, never success, never queue). Pure, no I/O.
    """
    busy = outcome.busy if isinstance(outcome, AdmissionOutcome) else None
    entry = getattr(busy, "holder_entry", "unknown") or "unknown"
    try:
        epoch = int(getattr(busy, "holder_epoch", 0) or 0)
    except (TypeError, ValueError):
        epoch = 0
    return {
        "type": "soul_busy",
        "code": outcome.code if isinstance(outcome, AdmissionOutcome) else "soul_busy",
        "holder_entry": str(entry),
        "holder_epoch": epoch,
        "retry_after": RETRY_AFTER_SECONDS,
    }


def busy_text(outcome: AdmissionOutcome) -> str:
    """Human-readable busy line (TG busy message, §8).

    Same identity-class rule as :func:`busy_frame`: no tokens. Carries the
    retry_after=1 hint in prose so the user knows to retry the same key.
    """
    frame = busy_frame(outcome)
    return (
        "Soul busy (%s holder, epoch %d): please retry the same message "
        "in %d second(s); a new message starts a new request."
        % (frame["holder_entry"], frame["holder_epoch"], frame["retry_after"])
    )


def derive_retry_key(
    entry_kind: str,
    soul_alias: str,
    client_key: Optional[str] = None,
) -> str:
    """Derive the idempotency key for one publish opportunity (§9).

    - ``client_key`` present (TG ``bot-namespace + update_id``, WS
      ``session + client message id``, voice ``session + turn id``):
      stable ``"<kind>:<alias>:<client>"`` — same-key retries reach REPLAY.
    - ``client_key`` absent: fresh ``"uuid-<hex>"``. Deduplication across
      retries is then NOT guaranteed (stated here and in the matrix, never
      silently promised). Entries must keep the raw input and never
      auto-resend.
    """
    if entry_kind not in ENTRY_KINDS:
        raise ValueError("entry_kind must be one of %r." % (ENTRY_KINDS,))
    if not isinstance(soul_alias, str) or not soul_alias.strip():
        raise ValueError("soul_alias must be a non-empty string.")
    if isinstance(client_key, str) and client_key.strip():
        cleaned = []
        for c in client_key.strip():
            cleaned.append(c if (c.isalnum() or c in "-_.:/") else "-")
        safe = ""
        for c in cleaned[:160]:
            safe += c
        return "%s:%s:%s" % (entry_kind, soul_alias.strip(), safe or "unstable")
    return "uuid-%s" % uuid.uuid4().hex


@dataclass
class HolderRegistry:
    """Caller-owned holder-class labels, keyed by soul alias.

    The Journal owns the mutex truth; this map only owns the human-readable
    holder class (entry kind) for busy bodies, because the frozen Journal
    schema has no column for it. Keys are lease namespaces (which embed the
    soul alias). In-memory only: after a restart the class reads back as
    ``"unknown"`` with the true epoch from the Journal (part of the
    accepted B7 residual gap —the epoch is never forged).

    ``track_request`` additionally binds each lease-path ``request_id`` to
    its ``(soul_alias, generation)`` so dispatch-side O-gates can verify
    the generation (``require_current``): a stale-generation worker's id
    is unregistered even when its journal row still reads executing.
    Leaseless (legacy) admits are never tracked and keep the journal-only
    rule. The map lives and dies with the process: after a restart the
    registry is empty and ``recover()`` has already marked old
    executing rows uncertain, so nothing tracked can go stale.
    """

    _classes: dict = field(default_factory=dict)
    _requests: dict = field(default_factory=dict)

    def register(self, soul_alias: str, entry_kind: str) -> None:
        self._classes[soul_alias] = entry_kind

    def lookup(self, soul_alias: str) -> str:
        return self._classes.get(soul_alias, "unknown")

    def track_request(self, request_id: str, soul_alias: str, generation: int) -> None:
        """Bind a lease-path request to its (alias, generation)."""
        if isinstance(request_id, str) and request_id:
            self._requests[request_id] = (soul_alias, int(generation))

    def lookup_request(self, request_id: str):
        """Return ``(soul_alias, generation)`` or ``None`` (untracked)."""
        try:
            return self._requests.get(request_id)
        except AttributeError:
            return None


def coordinator_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """True only on explicit opt-in. Default (unset/anything else) is off."""
    source = env if env is not None else os.environ
    raw = source.get(FLAG_ENV_VAR, "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def lease_namespace(soul_alias: str, idempotency_key: str) -> str:
    """Per-soul lease namespace. Always embeds the soul alias.

    The idempotency key scopes the row per request opportunity so that a
    same-key retry can reach REPLAY while a different soul never collides.
    Residual limitation (reported, not silently solved): two genuinely
    different keys under one soul map to different namespaces, so the
    Journal alone does not serialise them. A per-soul singleton namespace
    would permanently CONFLICT every later request, and a second lease
    table is rejected by B2案A — cross-key serialisation needs a parent
    decision before any entry wiring claims full-soul mutex.
    """
    if not isinstance(soul_alias, str) or not soul_alias.strip():
        raise ValueError("soul_alias must be a non-empty string.")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key must be a non-empty string.")
    return "%s%s/%s" % (NAMESPACE_PREFIX, soul_alias.strip(), idempotency_key.strip())


#: Coordinator-local disposition labels. Same string values as the upstream
#: ``ClaimResult.kind`` register (EI-2B-JOURNAL-ADAPT-1): these are the
#: coordinator's admission dispositions, not journal states — no new journal
#: state is introduced here.
OUTCOME_NEW = "NEW"
OUTCOME_REPLAY = "REPLAY"
OUTCOME_IN_PROGRESS = "IN_PROGRESS"
OUTCOME_CONFLICT = "CONFLICT"
OUTCOME_UNCERTAIN = "UNCERTAIN"
OUTCOME_EXPIRED = "EXPIRED"

#: Coordinator-local copies of the upstream durable states this module may
#: observe. Upstream ``_VALID_STATES`` has no completed / failed /
#: cancelled — the settle table below must never reference such terminal
#: states (see §9 gap G1/G2).
STATE_ACCEPTED = "accepted"
STATE_EXECUTING = "executing"
STATE_GENERATED = "generated"
STATE_TRANSPORT_SENT = "transport_sent"
STATE_FAILED_BEFORE_EXECUTION = "failed_before_execution"
STATE_UNCERTAIN = "uncertain"


def journal_namespace(soul_alias: str, idempotency_key: str) -> tuple:
    """Six-column journal key for one soul's idempotency row.

    Fixed mapping (EI-2B-JOURNAL-ADAPT-1 §3): ``("ei2b-local", alias,
    "soul-lease", "", "", key)``. ``lease_namespace()`` above is kept for
    the in-memory holder-class registry only; the two namespaces are built
    independently — the old string is never parsed apart. Single-user entry
    namespace; no multi-tenant authorization is claimed.
    """
    if not isinstance(soul_alias, str) or not soul_alias.strip():
        raise ValueError("soul_alias must be a non-empty string.")
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("idempotency_key must be a non-empty string.")
    return (
        "ei2b-local",
        soul_alias.strip(),
        "soul-lease",
        "",
        "",
        idempotency_key.strip(),
    )


_REQUEST_ROW_SELECT = (
    "SELECT request_id, state, epoch, fingerprint, response_id,"
    " response_body, expires_ts FROM requests"
)


def _row_to_dict(row) -> dict:
    return {
        "request_id": row[0],
        "state": row[1],
        "epoch": row[2],
        "fingerprint": row[3],
        "response_id": row[4],
        "response_body": row[5],
        "expires_ts": row[6],
    }


def _read_request_by_id(journal: Journal, request_id: str) -> Optional[dict]:
    """Coordinator-owned read-only lookup of one journal row by request id.

    Parameterized SELECT over ``journal.conn``; never added as a Journal
    API (upstream stays byte-identical). Storage failure raises
    ``JournalError`` (callers map it fail-closed); unknown id returns None.
    """
    try:
        row = journal.conn.execute(
            _REQUEST_ROW_SELECT + " WHERE request_id=?",
            (request_id,),
        ).fetchone()
    except JournalError:
        raise
    except Exception as exc:
        raise JournalError("coordinator read failed: %s" % exc)
    if row is None:
        return None
    return _row_to_dict(row)


def _read_row_by_namespace(journal: Journal, journal_ns: tuple) -> Optional[dict]:
    """Coordinator-owned read-only lookup of one journal row by six-tuple.

    Same contract as :func:`_read_request_by_id`, keyed by the full
    ``(principal_id, agent_id, scope, conversation_id, branch_id,
    idempotency_key)`` tuple.
    """
    try:
        row = journal.conn.execute(
            _REQUEST_ROW_SELECT
            + " WHERE principal_id=? AND agent_id=? AND scope=?"
            + " AND conversation_id=? AND branch_id=? AND idempotency_key=?",
            tuple(journal_ns),
        ).fetchone()
    except JournalError:
        raise
    except Exception as exc:
        raise JournalError("coordinator read failed: %s" % exc)
    if row is None:
        return None
    return _row_to_dict(row)


def _holder_of(journal: Journal, journal_ns: tuple, registry: Optional[HolderRegistry], registry_key: str) -> BusyBody:
    epoch = 0
    try:
        row = _read_row_by_namespace(journal, journal_ns)
        if row is not None:
            epoch = int(row.get("epoch") or 0)
    except (JournalError, ValueError, TypeError, AttributeError):
        epoch = 0
    holder_entry = registry.lookup(registry_key) if registry is not None else "unknown"
    return BusyBody(holder_entry=holder_entry, holder_epoch=epoch)


def _busy_from_lease(
    lease: AcquireResult,
    lease_store: Optional[SoulLeaseStore] = None,
    soul_alias: str = "",
) -> BusyBody:
    """409 body from the live lease holder (class + epoch, never a token).

    Prefers the holder's recorded journal epoch + entry hint via a pure
    re-read; falls back to the acquire result when storage is gone.
    """
    entry: Any = lease.holder_entry
    epoch: Any = lease.holder_generation
    if lease_store is not None and soul_alias:
        try:
            row = lease_store.load(soul_alias)
        except SoulLeaseError:
            row = None
        if row is not None:
            entry = row.get("holder_entry") or entry
            epoch = row.get("journal_epoch", epoch)
    try:
        epoch_int = int(epoch)
    except (TypeError, ValueError):
        epoch_int = 0
    return BusyBody(
        holder_entry=str(entry) if entry else "unknown",
        holder_epoch=epoch_int,
    )


def _mint_holder_token() -> str:
    """Mint one opaque holder token (never logged, never returned)."""
    return secrets.token_hex(16)


def _admit_with_lease(
    journal: Journal,
    registry: Optional[HolderRegistry],
    lease_store: SoulLeaseStore,
    *,
    soul_alias: str,
    entry_kind: str,
    fingerprint: str,
    request_id: str,
    journal_ns: tuple,
    registry_key: str,
    epoch: int,
    now: float,
    lease_ttl: float,
    holder_token: Optional[str] = None,
    process_nonce: str = "",
    pid: int = -1,
    process_create_time: Optional[float] = None,
    held_map: Optional[dict] = None,
    backend_ledger: Optional[dict] = None,
) -> AdmissionOutcome:
    """Two-step admission: capability lease first, idempotency row second.

    Segmented commit (§5): the lease, the claim, and the begin are three
    independent operations —never one outer journal transaction. A lease
    won and then lost at the claim/begin is released only with proven
    quiescence (this path never published and opened no permits, so the
    proof is local and exact).

    On ``begin`` success the request is bound RUNNING in ``backend_ledger``
    (when given; SETTLE-1): only a bound request may call the backend
    port. ``None`` keeps the legacy journal-only path (no port rights).
    """
    token = holder_token if holder_token else _mint_holder_token()
    nonce = process_nonce if process_nonce else ""
    own_pid = os.getpid() if pid is None or pid < 0 else pid
    create_time = 0.0 if process_create_time is None else process_create_time
    try:
        lease = lease_store.acquire(
            soul_alias, token, request_id, nonce or _mint_holder_token(),
            own_pid, create_time, epoch, now,
            holder_entry=entry_kind,
        )
    except SoulLeaseError as exc:
        logger.warning("[Coordinator] lease storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
        return _uncertain(registry)
    capability = Capability(soul_alias, token, request_id, lease.holder_generation)
    if not lease.won:
        # Lost the lease: another request holds this soul. If our own
        # idempotency namespace already has a row (same-key retry), walk
        # the journal so REPLAY / CONFLICT / EXPIRED keep their meaning;
        # otherwise this is a pure busy with zero journal writes.
        try:
            existing = _read_row_by_namespace(journal, journal_ns)
        except JournalError as exc:
            logger.warning("[Coordinator] busy re-read failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
            return _uncertain(registry)
        if existing is not None:
            try:
                claimed = journal.claim(journal_ns, fingerprint, request_id, now)
            except JournalError as exc:
                logger.warning("[Coordinator] claim storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
                return _uncertain(registry)
            if claimed.kind == OUTCOME_REPLAY:
                return AdmissionOutcome(
                    disposition=OUTCOME_REPLAY,
                    http_status=200,
                    code="soul_replay",
                    should_publish=False,
                    fixed_response_id=claimed.response_id,
                    fixed_response_body=claimed.response_body,
                )
            if claimed.kind == OUTCOME_CONFLICT:
                return AdmissionOutcome(
                    disposition=OUTCOME_CONFLICT,
                    http_status=409,
                    code="soul_conflict",
                    should_publish=False,
                )
            if claimed.kind == OUTCOME_EXPIRED:
                return AdmissionOutcome(
                    disposition=OUTCOME_EXPIRED,
                    http_status=410,
                    code="soul_expired",
                    should_publish=False,
                )
            if claimed.kind == OUTCOME_UNCERTAIN:
                return AdmissionOutcome(
                    disposition=OUTCOME_UNCERTAIN,
                    http_status=409,
                    code="soul_uncertain",
                    should_publish=False,
                    busy=_busy_from_lease(lease, lease_store, soul_alias),
                )
        return AdmissionOutcome(
            disposition=OUTCOME_IN_PROGRESS,
            http_status=409,
            code="soul_busy",
            should_publish=False,
            busy=_busy_from_lease(lease, lease_store, soul_alias),
        )
    # Lease won: claim the idempotency row, then CAS begin.
    try:
        claimed = journal.claim(journal_ns, fingerprint, request_id, now)
    except JournalError as exc:
        logger.warning("[Coordinator] claim storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
        _compensate_lease(lease_store, capability, soul_alias, entry_kind, now, held_map)
        return _uncertain(registry)
    if claimed.kind != OUTCOME_NEW:
        # Same-key second attempt under a fresh lease: do not hold what
        # we will not use —release, then map the journal outcome. The
        # persistent request id is authoritative: when it differs from our
        # capability's id we publish nothing, release our just-won lease
        # (isolate), and report — never paper over it by swapping identity.
        if claimed.request_id != request_id:
            logger.warning(
                "[Coordinator] divergent claim soul=%s entry=%s: journal row held by %s",
                soul_alias, entry_kind, claimed.request_id,
            )
        _compensate_lease(lease_store, capability, soul_alias, entry_kind, now, held_map)
        if claimed.kind == OUTCOME_REPLAY:
            return AdmissionOutcome(
                disposition=OUTCOME_REPLAY,
                http_status=200,
                code="soul_replay",
                should_publish=False,
                fixed_response_id=claimed.response_id,
                fixed_response_body=claimed.response_body,
            )
        if claimed.kind == OUTCOME_CONFLICT:
            return AdmissionOutcome(
                disposition=OUTCOME_CONFLICT,
                http_status=409,
                code="soul_conflict",
                should_publish=False,
            )
        if claimed.kind == OUTCOME_EXPIRED:
            return AdmissionOutcome(
                disposition=OUTCOME_EXPIRED,
                http_status=410,
                code="soul_expired",
                should_publish=False,
            )
        if claimed.kind == OUTCOME_IN_PROGRESS:
            return AdmissionOutcome(
                disposition=OUTCOME_IN_PROGRESS,
                http_status=409,
                code="soul_busy",
                should_publish=False,
                busy=_holder_of(journal, journal_ns, registry, registry_key),
            )
        return AdmissionOutcome(
            disposition=OUTCOME_UNCERTAIN,
            http_status=409,
            code="soul_uncertain",
            should_publish=False,
            busy=_holder_of(journal, journal_ns, registry, registry_key),
        )
    try:
        journal.begin(request_id, epoch)
    except OwnershipError as exc:
        # Lost the CAS: someone holds the row. Release the lease, fail busy.
        logger.warning("[Coordinator] begin ownership lost soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
        _compensate_lease(lease_store, capability, soul_alias, entry_kind, now, held_map)
        return AdmissionOutcome(
            disposition=OUTCOME_IN_PROGRESS,
            http_status=409,
            code="soul_busy",
            should_publish=False,
            busy=_holder_of(journal, journal_ns, registry, registry_key),
        )
    except JournalError as exc:
        logger.warning("[Coordinator] begin storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
        # §5 commit-exception path: re-read before compensating. If the
        # row is executing under OUR request id the CAS may have landed
        # before the error —mark it uncertain (never compensate into the
        # wrong place). Single-parameter mark: non-executing raises and is
        # left alone, never assumed a no-op.
        seen = _recheck_begin(journal, request_id, epoch)
        if seen is not None and seen.get("request_id") == request_id:
            try:
                journal.mark_uncertain(request_id)
            except JournalError:
                pass
        _compensate_lease(lease_store, capability, soul_alias, entry_kind, now, held_map)
        return _uncertain(registry)
    # Begin success returns None — never truth-tested (no `if won:`).
    if registry is not None:
        registry.register(registry_key, entry_kind)
        registry.track_request(request_id, soul_alias, capability.generation)
    if held_map is not None:
        held_map[soul_alias] = capability
    if backend_ledger is not None:
        _bind_backend_running(
            backend_ledger, soul_alias=soul_alias, request_id=request_id,
            generation=capability.generation, epoch=epoch,
        )
    return AdmissionOutcome(
        disposition=OUTCOME_NEW,
        http_status=201,
        code="soul_admitted",
        should_publish=True,
    )


def _compensate_lease(
    lease_store: SoulLeaseStore,
    capability: Capability,
    soul_alias: str,
    entry_kind: str,
    now: float,
    held_map: Optional[dict] = None,
) -> None:
    """Release a just-won but unused lease (claim/begin lost).

    Quiescence proof is local and exact: this path never published and
    opened no permits, so ``quiesced=True`` is proven, not assumed.
    Failures are logged, never raised (admission already has its
    journal-mapped outcome).
    """
    try:
        if lease_store.release(capability, now, quiesced=True):
            if held_map is not None:
                held_map.pop(soul_alias, None)
    except (SoulLeaseError, ValueError) as exc:
        logger.warning(
            "[Coordinator] compensating release failed soul=%s entry=%s: %s",
            soul_alias, entry_kind, exc,
        )


def _recheck_begin(
    journal: Journal, request_id: str, epoch: int
) -> Optional[dict]:
    """Re-read a journal row after a commit exception (§5).

    Returns the row (or None) without writing: the caller decides, and
    an ambiguous state is never compensated into the wrong place. The
    ``epoch`` argument is kept for caller compatibility and is not used
    for fencing here. Storage failure reads as None (fail closed).
    """
    try:
        return _read_request_by_id(journal, request_id)
    except JournalError:
        return None


def _uncertain(registry: Optional[HolderRegistry], code: str = "soul_uncertain") -> AdmissionOutcome:
    holder = registry.lookup("") if registry is not None else "unknown"
    return AdmissionOutcome(
        disposition=OUTCOME_UNCERTAIN,
        http_status=409,
        code=code,
        should_publish=False,
        busy=BusyBody(holder_entry=holder),
    )


def admit_entry(
    journal: Journal,
    registry: Optional[HolderRegistry],
    *,
    soul_alias: str,
    entry_kind: str,
    fingerprint: str,
    request_id: str,
    idempotency_key: str,
    epoch: int,
    now: float,
    lease_store: Optional[SoulLeaseStore] = None,
    lease_ttl: float = LEASE_DEFAULT_TTL_SECONDS,
    holder_token: Optional[str] = None,
    process_nonce: str = "",
    pid: int = -1,
    process_create_time: Optional[float] = None,
    held_map: Optional[dict] = None,
    backend_ledger: Optional[dict] = None,
) -> AdmissionOutcome:
    """Per-soul admission at an entry claim point (publish boundary).

    Two steps when ``lease_store`` is given (EI-2B-PROGRAM-3): (1) acquire
    the soul lease by capability ``(alias, holder_token, request_id,
    generation)``, (2) ``journal.claim`` + CAS ``begin``. Only the winner
    of BOTH steps gets ``should_publish=True``. A lease won and then lost
    at the claim/begin is released with proven quiescence (this path never
    published and opened no permits —never hold what you will not use,
    never free what may still be working).

    Holder identity (``holder_token`` / ``process_nonce`` / ``pid`` /
    ``process_create_time``) defaults to a fresh token + current process
    when absent; ``prepare_publish`` passes the context-stable values.
    ``lease_ttl`` is kept as an accepted-but-unused observation knob:
    expiry never takes over (PROGRAM-3 reverses the TTL half).

    Without ``lease_store`` (legacy / PROGRAM-1 callers): the old
    single-step ``claim -> begin`` path, unchanged.

    ``backend_ledger`` (SETTLE-1): the winning admission binds RUNNING
    here so the backend port may be called; ``None`` keeps the legacy
    journal-only path with no port rights.

    ``JournalError``/``SoulLeaseError`` map to UNCERTAIN (fail-closed),
    never to success. Losers get 409 busy (holder class + epoch +
    ``Retry-After: 1``), never a queue, never a drop-with-success.
    """
    if entry_kind not in ENTRY_KINDS:
        raise ValueError("entry_kind must be one of %r." % (ENTRY_KINDS,))
    registry_key = lease_namespace(soul_alias, idempotency_key)
    journal_ns = journal_namespace(soul_alias, idempotency_key)
    if lease_store is not None:
        return _admit_with_lease(
            journal, registry, lease_store,
            soul_alias=soul_alias, entry_kind=entry_kind,
            fingerprint=fingerprint, request_id=request_id,
            journal_ns=journal_ns, registry_key=registry_key,
            epoch=epoch, now=now,
            lease_ttl=lease_ttl, holder_token=holder_token,
            process_nonce=process_nonce, pid=pid,
            process_create_time=process_create_time,
            held_map=held_map,
            backend_ledger=backend_ledger,
        )
    try:
        claimed = journal.claim(journal_ns, fingerprint, request_id, now)
    except JournalError as exc:
        logger.warning("[Coordinator] claim storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
        return _uncertain(registry)
    if claimed.kind == OUTCOME_NEW:
        try:
            journal.begin(request_id, epoch)
        except OwnershipError as exc:
            # Lost the CAS: someone else holds the row. Fail closed as busy.
            logger.warning("[Coordinator] begin ownership lost soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
            return AdmissionOutcome(
                disposition=OUTCOME_IN_PROGRESS,
                http_status=409,
                code="soul_busy",
                should_publish=False,
                busy=_holder_of(journal, journal_ns, registry, registry_key),
            )
        except JournalError as exc:
            logger.warning("[Coordinator] begin storage failure soul=%s entry=%s: %s", soul_alias, entry_kind, exc)
            try:
                journal.mark_uncertain(request_id)
            except JournalError:
                pass
            return _uncertain(registry)
        # Begin success returns None — never truth-tested (no `if won:`).
        if registry is not None:
            registry.register(registry_key, entry_kind)
        return AdmissionOutcome(
            disposition=OUTCOME_NEW,
            http_status=201,
            code="soul_admitted",
            should_publish=True,
        )
    if claimed.kind == OUTCOME_IN_PROGRESS:
        return AdmissionOutcome(
            disposition=OUTCOME_IN_PROGRESS,
            http_status=409,
            code="soul_busy",
            should_publish=False,
            busy=_holder_of(journal, journal_ns, registry, registry_key),
        )
    if claimed.kind == OUTCOME_REPLAY:
        return AdmissionOutcome(
            disposition=OUTCOME_REPLAY,
            http_status=200,
            code="soul_replay",
            should_publish=False,
            fixed_response_id=claimed.response_id,
            fixed_response_body=claimed.response_body,
        )
    if claimed.kind == OUTCOME_CONFLICT:
        return AdmissionOutcome(
            disposition=OUTCOME_CONFLICT,
            http_status=409,
            code="soul_conflict",
            should_publish=False,
        )
    if claimed.kind == OUTCOME_EXPIRED:
        return AdmissionOutcome(
            disposition=OUTCOME_EXPIRED,
            http_status=410,
            code="soul_expired",
            should_publish=False,
        )
    # UNCERTAIN and any future disposition: never publish, never queue.
    return AdmissionOutcome(
        disposition=OUTCOME_UNCERTAIN,
        http_status=409,
        code="soul_uncertain",
        should_publish=False,
        busy=_holder_of(journal, journal_ns, registry, registry_key),
    )


def current_busy(
    journal: Journal,
    registry: Optional[HolderRegistry],
    *,
    soul_alias: str,
    idempotency_key: str,
) -> BusyBody:
    """Re-read the busy body for a lease (holder class may have registered
    after an earlier admission attempt saw ``"unknown"``). Pure read."""
    return _holder_of(
        journal,
        journal_namespace(soul_alias, idempotency_key),
        registry,
        lease_namespace(soul_alias, idempotency_key),
    )


def fail_closed_uncertain(
    journal: Journal,
    *,
    request_id: str,
    epoch: int,
    reason: str,
) -> AdmissionOutcome:
    """Missing-registration path: mark uncertain + log, never dispatch.

    Callers use this when an event has no valid admission (no claim point
    covered it): the event must not be broadcast/sent/synthesised. Best
    effort single-parameter ``mark_uncertain`` (non-executing raises and is
    left alone, never assumed a no-op); storage failure is logged, never
    raised. The ``epoch`` argument is kept for caller compatibility and is
    not passed to the journal.
    """
    try:
        journal.mark_uncertain(request_id)
    except JournalError as exc:
        logger.warning("[Coordinator] fail-closed mark_uncertain failed: %s", exc)
    logger.warning("[Coordinator] fail-closed uncertain request=%s epoch=%s reason=%s", request_id, epoch, reason)
    return AdmissionOutcome(
        disposition=OUTCOME_UNCERTAIN,
        http_status=409,
        code="soul_uncertain",
        should_publish=False,
        busy=BusyBody(),
    )


def mark_non_request(payload: Mapping[str, Any], triggered_by: str) -> dict:
    """Label a background write so it never mixes into a request outcome.

    Pure: returns a copy, never mutates the input. ``triggered_by`` names
    the slot/trigger (mirrors the existing ``triggered_by=reason`` usage);
    the fixed ``non_request_write`` marker carries no request identity.
    """
    if not isinstance(triggered_by, str) or not triggered_by.strip():
        raise ValueError("triggered_by must be a non-empty string.")
    labelled = dict(payload)
    labelled[NON_REQUEST_MARK] = True
    labelled.setdefault("triggered_by", triggered_by.strip())
    return labelled


def is_non_request(payload: Mapping[str, Any]) -> bool:
    """True when a record carries the background-write marker."""
    try:
        return payload.get(NON_REQUEST_MARK) is True
    except AttributeError:
        return False


@dataclass
class AdmissionContext:
    """Caller-owned admission bundle shared by one process's entries.

    One WAL connection backs the frozen ``Journal`` (idempotency
    ledger), the ``SoulLeaseStore`` (mutex), and the coordinator-owned
    permit/reconciliation tables; ``epoch`` is the process epoch (bumped
    on restart); ``now_fn`` is the caller clock.

    ``process_nonce`` / ``pid`` / ``process_create_time`` bind every
    capability this context mints to this process instance (work order
    §2): recovery probes them read-only to tell a live holder from a
    dead one. ``liveness_fn`` defaults to the OS read-only prober and
    exists so tests can inject a fixture (never real processes).
    """

    journal: Journal
    lease_store: SoulLeaseStore
    registry: HolderRegistry
    epoch: int
    lease_ttl: float = LEASE_DEFAULT_TTL_SECONDS
    now_fn: Any = None
    process_nonce: str = ""
    pid: int = -1
    process_create_time: float = 0.0
    liveness_fn: Any = None
    #: Live holds minted by this context: soul_alias -> Capability.
    #: The token never leaves this map (never logged, never returned in
    #: outcomes); entries settle through :func:`settle_request` /
    #: :func:`settle_held`, which look the capability up here.
    held: dict = field(default_factory=dict)
    #: Private backend-execution ledger (SETTLE-1): request_id ->
    #: :class:`BackendRecord`. Memory only: bound at ``begin`` success
    #: (RUNNING), advanced only by real port returns, never persisted.
    #: A restart drops it; recovery must isolate, never re-release.
    _backend_ledger: dict = field(default_factory=dict, repr=False, compare=False)
    #: Private release tombstones (SETTLE-1): request_id -> terminal state.
    #: A released request settles idempotent without touching the lease
    #: store again and never replays the port.
    _backend_tombstones: dict = field(default_factory=dict, repr=False, compare=False)

    def now(self) -> float:
        fn = self.now_fn if self.now_fn is not None else time.time
        return float(fn())


def _verify_holder(
    ctx: AdmissionContext,
    soul_alias: str,
    now: float,
) -> str:
    """Verify one lease holder at startup; return the resulting state.

    - Probe liveness read-only (pid + creation time; never PID/epoch/
      heartbeat alone, never terminate rights).
    - Quiescence = zero unsettled permits for the holder's request
      (unclosed permits count as not quiesced, §4).
    - ALIVE -> untouched. UNKNOWN -> quarantine. DEAD + quiesced ->
      free + single-parameter ``mark_uncertain`` of the holder's journal
      row. DEAD + working -> quarantine.
    """
    try:
        row = ctx.lease_store.load(soul_alias)
    except SoulLeaseError as exc:
        logger.warning("[Coordinator] startup verify read failed soul=%s: %s", soul_alias, exc)
        return LEASE_HELD
    if row is None or row.get("state") == LEASE_FREE:
        return LEASE_FREE
    probe = ctx.liveness_fn if ctx.liveness_fn is not None else query_process_liveness
    try:
        liveness = probe(int(row.get("pid") or 0), float(row.get("process_create_time") or 0.0))
    except Exception:
        liveness = Liveness(LIVENESS_UNKNOWN, "probe crashed")
    if not isinstance(liveness, Liveness):
        liveness = Liveness(LIVENESS_UNKNOWN, "probe contract violated")
    holder_request = str(row.get("holder_request_id") or "")
    quiesced = count_open_permits(ctx, holder_request) == 0 if holder_request else True
    try:
        result = ctx.lease_store.recover(
            soul_alias, liveness, now,
            side_effects_quiesced=quiesced,
        )
    except SoulLeaseError as exc:
        logger.warning("[Coordinator] startup verify failed soul=%s: %s", soul_alias, exc)
        return str(row.get("state"))
    if result == LEASE_FREE and liveness.state == LIVENESS_DEAD and holder_request:
        # Proven-dead + quiesced only: fail the holder's journal row
        # closed. Single-parameter mark raises unless the row is still
        # executing, so generated / transport_sent rows are untouched
        # (never compensated into the wrong place).
        try:
            ctx.journal.mark_uncertain(holder_request)
        except (JournalError, ValueError, TypeError):
            pass
    return result


def open_admission_context(
    conn,
    *,
    epoch: int,
    registry: Optional[HolderRegistry] = None,
    lease_ttl: float = LEASE_DEFAULT_TTL_SECONDS,
    now_fn: Any = None,
    process_nonce: str = "",
    pid: int = -1,
    process_create_time: Optional[float] = None,
    liveness_fn: Any = None,
) -> AdmissionContext:
    """Build caller-owned admission stores over one connection + recover.

    ``sqlite3`` is imported lazily here (never at module import). Restart
    recovery runs at open in two parts: (1) the upstream journal rule —
    ``recover()`` moves old-epoch executing rows to uncertain and returns
    their request ids (a commit ambiguity raises and fails the open
    closed — never a false success, never phantom ids); (2) per-holder
    lease verification (work order §5) —live holders stay held, unknown
    go QUARANTINED, and only proven-dead + quiesced holders go FREE (each
    then settled via single-parameter ``mark_uncertain``). The
    PROGRAM-2 blanket ``drop every lease row`` is gone. A journal scan
    never stands in for holder death: the lease is still verified
    holder-by-holder.
    """
    import sqlite3 as _sqlite3

    if not isinstance(conn, _sqlite3.Connection):
        raise TypeError("open_admission_context requires a caller-opened sqlite3.Connection.")
    if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
        raise ValueError("epoch must be a non-negative int.")
    journal = Journal(conn, epoch=epoch)
    lease_store = SoulLeaseStore(conn)
    _ensure_permit_schema(conn)
    _ensure_rejected_schema(conn)
    try:
        recovered = journal.recover()
    except JournalError:
        raise
    if recovered:
        logger.warning(
            "[Coordinator] startup recover marked %d executing row(s) uncertain",
            len(recovered),
        )
    ctx = AdmissionContext(
        journal=journal,
        lease_store=lease_store,
        registry=registry if registry is not None else HolderRegistry(),
        epoch=epoch,
        lease_ttl=float(lease_ttl),
        now_fn=now_fn,
        process_nonce=process_nonce or uuid.uuid4().hex,
        pid=os.getpid() if pid is None or pid < 0 else pid,
        process_create_time=(
            own_process_create_time()
            if process_create_time is None else float(process_create_time)
        ),
        liveness_fn=liveness_fn,
    )
    try:
        now = ctx.now()
    except Exception:
        now = time.time()
    for soul in lease_store.all_rows():
        _verify_holder(ctx, soul, now)
    return ctx


def prepare_publish(
    ctx: AdmissionContext,
    *,
    soul_alias: str,
    entry_kind: str,
    text: str,
    idempotency_key: Optional[str] = None,
) -> Tuple[RequestIdentity, AdmissionOutcome]:
    """Mint a fresh identity and run two-step admission for one publish.

    Key rule (§4): every publish opportunity mints a fresh ``request_id``;
    retry is a new request. ``idempotency_key`` scopes the journal row:
    ``None`` (default, preserves every existing caller) mints a fresh key
    per opportunity; a stable §9 retry key (see :func:`derive_retry_key`)
    routes a same-key retry to the SAME journal row so the frozen journal
    answers IN_PROGRESS / REPLAY / CONFLICT / EXPIRED / UNCERTAIN instead
    of double-publishing. A stable key never bypasses the lease: a
    same-key retry that loses the lease still fails busy (never queues).

    Returns ``(identity, outcome)``; callers publish only when
    ``outcome.should_publish`` is True and must stamp
    ``payload["soul_request_id"]`` with ``identity.request_id`` so the
    dispatch-side O-gates can require registration.
    """
    identity = mint_request_identity()
    outcome = admit_entry(
        ctx.journal,
        ctx.registry,
        soul_alias=soul_alias,
        entry_kind=entry_kind,
        fingerprint=fingerprint_for_text(text),
        request_id=identity.request_id,
        idempotency_key=(
            identity.idempotency_key
            if idempotency_key is None else idempotency_key
        ),
        epoch=ctx.epoch,
        now=ctx.now(),
        lease_store=ctx.lease_store,
        lease_ttl=ctx.lease_ttl,
        process_nonce=ctx.process_nonce,
        pid=ctx.pid,
        process_create_time=ctx.process_create_time,
        held_map=ctx.held,
        backend_ledger=ctx._backend_ledger,
    )
    return identity, outcome


def current_capability(
    ctx: AdmissionContext, soul_alias: str
) -> Optional[Capability]:
    """Return this context's live capability for a soul, if it holds one.

    Pure in-memory lookup; the token never leaves the context. ``None``
    means this process currently holds nothing for the soul (it may be
    FREE, or held by someone else, or quarantined).
    """
    cap = ctx.held.get(soul_alias)
    return cap if isinstance(cap, Capability) else None


# -- group admission, all-or-nothing (§8) ------------------------------------
#
# Production entries publish one event per soul per opportunity, so a group
# request is a frozen set of single-soul publishes: freeze the full target
# set, walk it alias-sorted, acquire every lease first, and only then
# claim/begin each row. Any loss compensates every grant already taken
# (lease-only grants release proven-quiescent; journal-touched grants go
# uncertain with evidence kept + lease released) and NOTHING publishes:
# partial fan-out is structurally impossible.

@dataclass
class GroupAdmissionResult:
    """Outcome of one all-or-nothing group admission.

    ``won`` True: ``grants`` maps every alias to its
    ``(RequestIdentity, Capability)`` and every journal row is executing
    under this context (all capabilities also sit in ``ctx.held``).
    ``won`` False: nothing publishes; ``outcome`` is the losing alias's
    mapped outcome (busy / replay / conflict / expired / uncertain).
    """

    won: bool
    grants: dict = field(default_factory=dict)
    outcome: Optional[AdmissionOutcome] = None


def _compensate_group_grant(
    ctx: AdmissionContext,
    soul_alias: str,
    identity: RequestIdentity,
    capability: Capability,
    journal_touched: bool,
    now: float,
) -> None:
    """Undo one group grant (best effort, never raises).

    Quiescence proof is local and exact: the group path never publishes
    and opens no permits, so ``quiesced=True`` is proven, not assumed.
    Journal-touched grants are marked uncertain (single-parameter; a
    non-executing row raises and is left alone) because their row already
    reads executing-ours.
    """
    if journal_touched:
        try:
            ctx.journal.mark_uncertain(identity.request_id)
        except JournalError:
            pass
    try:
        if ctx.lease_store.release(capability, now, quiesced=True):
            if ctx.held.get(soul_alias) == capability:
                ctx.held.pop(soul_alias, None)
    except (SoulLeaseError, ValueError):
        pass


def admit_group(
    ctx: AdmissionContext,
    *,
    soul_aliases,
    entry_kind: str,
    texts: Optional[Mapping[str, str]] = None,
    idempotency_keys: Optional[Mapping[str, str]] = None,
    now: Optional[float] = None,
) -> GroupAdmissionResult:
    """Admit a frozen group of souls, all-or-nothing (§8).

    ``soul_aliases`` is frozen (deduplicated) and walked alias-sorted so
    concurrent groups serialize in the same order (deadlock ban: no lock
    ordering inversion). Phase 1 takes every lease; phase 2 claims + begins
    every journal row. Any loss compensates all grants and returns
    ``won=False`` with the losing outcome. Exactly like the single path,
    storage failures map to UNCERTAIN (fail closed), never to success.
    """
    if entry_kind not in ENTRY_KINDS:
        raise ValueError("entry_kind must be one of %r." % (ENTRY_KINDS,))
    try:
        targets = sorted({a.strip() for a in soul_aliases if isinstance(a, str) and a.strip()})
    except TypeError:
        raise ValueError("soul_aliases must be an iterable of strings.")
    if not targets:
        raise ValueError("soul_aliases must name at least one soul.")
    stamp = ctx.now() if now is None else float(now)
    texts = texts if isinstance(texts, Mapping) else {}
    idem = idempotency_keys if isinstance(idempotency_keys, Mapping) else {}

    # Phase 1: every lease, alias-sorted. No journal writes yet.
    grants: list = []  # (alias, identity, capability)
    lease_store = ctx.lease_store
    for alias in targets:
        identity = mint_request_identity()
        token = _mint_holder_token()
        try:
            lease = lease_store.acquire(
                alias, token, identity.request_id,
                ctx.process_nonce or _mint_holder_token(),
                ctx.pid, ctx.process_create_time, ctx.epoch, stamp,
                holder_entry=entry_kind,
            )
        except SoulLeaseError as exc:
            logger.warning("[Coordinator] group lease failure soul=%s entry=%s: %s", alias, entry_kind, exc)
            for a2, i2, c2 in grants:
                _compensate_group_grant(ctx, a2, i2, c2, False, stamp)
            return GroupAdmissionResult(False, {}, _uncertain(ctx.registry))
        capability = Capability(alias, token, identity.request_id, lease.holder_generation)
        if not lease.won:
            for a2, i2, c2 in grants:
                _compensate_group_grant(ctx, a2, i2, c2, False, stamp)
            return GroupAdmissionResult(
                False, {},
                AdmissionOutcome(
                    disposition=OUTCOME_IN_PROGRESS,
                    http_status=409,
                    code="soul_busy",
                    should_publish=False,
                    busy=_busy_from_lease(lease, ctx.lease_store, alias),
                ),
            )
        grants.append((alias, identity, capability))

    # Phase 2: every claim + begin, alias-sorted. Any loss unwinds all.
    won_aliases: list = []
    for alias, identity, capability in grants:
        key = idem.get(alias)
        registry_key = lease_namespace(alias, identity.idempotency_key)
        journal_ns = journal_namespace(
            alias, identity.idempotency_key if key is None else str(key)
        )
        try:
            text = texts.get(alias, "")
            claimed = ctx.journal.claim(journal_ns, fingerprint_for_text(text), identity.request_id, stamp)
        except JournalError as exc:
            logger.warning("[Coordinator] group claim failure soul=%s entry=%s: %s", alias, entry_kind, exc)
            for a2, i2, c2 in grants:
                _compensate_group_grant(ctx, a2, i2, c2, a2 in won_aliases, stamp)
            return GroupAdmissionResult(False, {}, _uncertain(ctx.registry))
        if claimed.kind != OUTCOME_NEW:
            if claimed.request_id != identity.request_id:
                logger.warning(
                    "[Coordinator] divergent group claim soul=%s entry=%s: journal row held by %s",
                    alias, entry_kind, claimed.request_id,
                )
            for a2, i2, c2 in grants:
                _compensate_group_grant(ctx, a2, i2, c2, a2 in won_aliases, stamp)
            return GroupAdmissionResult(
                False, {}, _map_claim_outcome(ctx, claimed, journal_ns, registry_key))
        try:
            ctx.journal.begin(identity.request_id, ctx.epoch)
        except OwnershipError as exc:
            # Lost the CAS: someone holds the row. Unwind all, fail busy.
            logger.warning("[Coordinator] group begin ownership lost soul=%s entry=%s: %s", alias, entry_kind, exc)
            for a2, i2, c2 in grants:
                _compensate_group_grant(ctx, a2, i2, c2, a2 in won_aliases, stamp)
            return GroupAdmissionResult(
                False, {},
                AdmissionOutcome(
                    disposition=OUTCOME_IN_PROGRESS,
                    http_status=409,
                    code="soul_busy",
                    should_publish=False,
                    busy=_holder_of(ctx.journal, journal_ns, ctx.registry, registry_key),
                ),
            )
        except JournalError as exc:
            logger.warning("[Coordinator] group begin failure soul=%s entry=%s: %s", alias, entry_kind, exc)
            seen = _recheck_begin(ctx.journal, identity.request_id, ctx.epoch)
            touched = seen is not None and seen.get("request_id") == identity.request_id
            if touched:
                try:
                    ctx.journal.mark_uncertain(identity.request_id)
                except JournalError:
                    pass
            for a2, i2, c2 in grants:
                _compensate_group_grant(
                    ctx, a2, i2, c2, (a2 in won_aliases) or (a2 == alias and touched), stamp)
            return GroupAdmissionResult(False, {}, _uncertain(ctx.registry))
        # Begin success returns None — never truth-tested (no `if begun:`).
        won_aliases.append(alias)

    for alias, identity, capability in grants:
        if ctx.registry is not None:
            ctx.registry.register(lease_namespace(alias, identity.idempotency_key), entry_kind)
            ctx.registry.track_request(identity.request_id, alias, capability.generation)
        ctx.held[alias] = capability
        # SETTLE-1: every group winner binds RUNNING like the single path.
        _bind_backend_running(
            ctx._backend_ledger, soul_alias=alias,
            request_id=identity.request_id,
            generation=capability.generation, epoch=ctx.epoch,
        )
    return GroupAdmissionResult(
        True, {a: (i, c) for a, i, c in grants}, None
    )


def _map_claim_outcome(ctx: AdmissionContext, claimed, journal_ns: tuple, registry_key: str) -> AdmissionOutcome:
    """Map a non-NEW journal claim to its admission outcome (group path)."""
    if claimed.kind == OUTCOME_REPLAY:
        return AdmissionOutcome(
            disposition=OUTCOME_REPLAY,
            http_status=200,
            code="soul_replay",
            should_publish=False,
            fixed_response_id=claimed.response_id,
            fixed_response_body=claimed.response_body,
        )
    if claimed.kind == OUTCOME_CONFLICT:
        return AdmissionOutcome(
            disposition=OUTCOME_CONFLICT,
            http_status=409,
            code="soul_conflict",
            should_publish=False,
        )
    if claimed.kind == OUTCOME_EXPIRED:
        return AdmissionOutcome(
            disposition=OUTCOME_EXPIRED,
            http_status=410,
            code="soul_expired",
            should_publish=False,
        )
    if claimed.kind == OUTCOME_IN_PROGRESS:
        return AdmissionOutcome(
            disposition=OUTCOME_IN_PROGRESS,
            http_status=409,
            code="soul_busy",
            should_publish=False,
            busy=_holder_of(ctx.journal, journal_ns, ctx.registry, registry_key),
        )
    return _uncertain(ctx.registry)


# -- rejected inputs (§9) ------------------------------------------------------
#
# A denied entry keeps the raw input + retry key in this table (same
# admission database, per-key SQL rows — never a shared dict / whole-JSON
# overwrite). It never touches conversation history and never auto-resends:
# retention is evidence for the caller to retry the SAME key, nothing more.

_REJECTED_SCHEMA = """
CREATE TABLE IF NOT EXISTS rejected_inputs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          REAL NOT NULL,
    entry_kind  TEXT NOT NULL,
    soul_alias  TEXT NOT NULL,
    retry_key   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rejected_inputs_by_key
    ON rejected_inputs (retry_key);
"""


def _ensure_rejected_schema(conn) -> None:
    try:
        with conn:
            conn.executescript(_REJECTED_SCHEMA)
    except sqlite3.Error:
        raise SoulLeaseError("rejected-input storage unavailable.")


def record_rejected_input(
    ctx: AdmissionContext,
    *,
    entry_kind: str,
    soul_alias: str,
    retry_key: str,
    reason: str,
    text: str,
    now: float,
) -> int:
    """Retain one denied input with its retry key (§9).

    Best-effort callers wrap this in try/except: retention must never turn
    a deny into a crash. Returns the row id. Raises ValueError on bad
    arguments, SoulLeaseError when storage is unavailable.
    """
    if entry_kind not in ENTRY_KINDS:
        raise ValueError("entry_kind must be one of %r." % (ENTRY_KINDS,))
    if not isinstance(soul_alias, str) or not soul_alias.strip():
        raise ValueError("soul_alias must be a non-empty string.")
    if not isinstance(retry_key, str) or not retry_key.strip():
        raise ValueError("retry_key must be a non-empty string.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string.")
    if not isinstance(text, str):
        raise ValueError("text must be a string.")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    conn = _permit_conn(ctx)
    try:
        with conn:
            cur = conn.execute(
                "INSERT INTO rejected_inputs "
                "(at, entry_kind, soul_alias, retry_key, reason, text) "
                "VALUES (?,?,?,?,?,?)",
                (float(now), entry_kind, soul_alias.strip(),
                 retry_key.strip(), reason.strip(), text),
            )
            return int(cur.lastrowid or 0)
    except sqlite3.Error:
        raise SoulLeaseError("rejected-input storage unavailable.")


def count_rejected_inputs(ctx: AdmissionContext, retry_key: str) -> int:
    """Count retained inputs for one retry key (pure read)."""
    if not isinstance(retry_key, str) or not retry_key:
        return 0
    try:
        row = _permit_conn(ctx).execute(
            "SELECT COUNT(*) FROM rejected_inputs WHERE retry_key=?",
            (retry_key,),
        ).fetchone()
    except sqlite3.Error:
        raise SoulLeaseError("rejected-input storage unavailable.")
    return int(row[0]) if row else 0


# -- terminal settle hook + generation check (§6 valve, §10) -------------------

def settle_after_outcome(
    ctx: AdmissionContext, request_id: str, now: float
) -> str:
    """Settle hook for outcome writers (the §6 release valve).

    Finds this context's live capability by ``request_id`` (token never
    leaves :attr:`AdmissionContext.held`) and runs :func:`settle_request`,
    which re-reads the journal row and releases only on terminal state +
    zero open permits, or isolates otherwise. Returns the ``SETTLE_*``
    verdict, or ``SETTLE_REFUSED_UNKNOWN`` when this context holds nothing
    for the request (never touches another holder's lease).
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    for cap in list(ctx.held.values()):
        if isinstance(cap, Capability) and cap.request_id == request_id.strip():
            return settle_request(ctx, cap, now)
    return SETTLE_REFUSED_UNKNOWN


def require_current(ctx: AdmissionContext, request_id: str) -> bool:
    """Dispatch-side O-gate: registered AND generation-current (§10).

    ``require_registered`` (journal row executing) plus the capability
    generation check: a stale-generation worker's id is unregistered even
    when its journal row still reads executing, so a wrong-generation
    send is zero-write / zero-deliver. ``False`` on missing ids, unknown
    rows, non-executing rows, storage failure — and on any mismatch of
    ``(alias, generation, holder request)`` against the live lease row.

    Leaseless (legacy) admits are never tracked and keep the journal-only
    rule (documented residual: legacy paths predate generations).

    SETTLE-1: a sealed backend record (terminal or UNKNOWN) is never
    current — dispatch past cancellation / completion / isolation is
    zero-write / zero-deliver even when the journal row still reads
    executing.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        return False
    try:
        ledger = ctx._backend_ledger
    except AttributeError:
        ledger = None
    if isinstance(ledger, dict):
        rec = ledger.get(request_id.strip())
        if rec is not None and getattr(rec, "sealed", False):
            return False
    try:
        row = _read_request_by_id(ctx.journal, request_id.strip())
    except JournalError:
        return False
    if row is None:
        return False
    try:
        if row.get("state") != STATE_EXECUTING:
            return False
    except AttributeError:
        return False
    tracked = ctx.registry.lookup_request(request_id.strip()) if ctx.registry is not None else None
    if tracked is None:
        return True
    alias, generation = tracked
    try:
        lease_row = ctx.lease_store.load(alias)
    except SoulLeaseError:
        return False
    if lease_row is None:
        return False
    try:
        return (
            lease_row.get("state") == LEASE_HELD
            and int(lease_row.get("generation") or 0) == int(generation)
            and str(lease_row.get("holder_request_id") or "") == request_id.strip()
        )
    except (TypeError, ValueError):
        return False


# -- trusted background callers (§11) ------------------------------------------
#
# Background work never takes a lease and never reads a payload boolean
# for authorization: the caller names itself explicitly and the name must
# sit on this fixed roll. Diary / dream writers keep their
# ``non_request_write`` marker (they must never import this module —
# enforced by a static guard); scheduler-class callers use this check at
# the deployment wiring point.

TRUSTED_BACKGROUND_CALLERS = ("scheduler", "diary", "dream")


def check_background_caller(caller_name: str) -> str:
    """Authorize a background-write caller by explicit name (§11).

    The name is caller-declared code identity, never a payload field: a
    forged ``non_request_write`` / ``triggered_by`` boolean in some payload
    cannot authorize anything because this function never reads payloads.
    Unknown or empty names raise ``ValueError`` (forgery closed). Returns
    the verified name.
    """
    if not isinstance(caller_name, str) or not caller_name.strip():
        raise ValueError("background caller name must be a non-empty string.")
    name = caller_name.strip()
    if name not in TRUSTED_BACKGROUND_CALLERS:
        raise ValueError("untrusted background caller: %r." % (name,))
    return name


# -- outbound permits + reconciliation (§11) ----------------------------------
#
# Every outbound send opens a permit first (atomic INSERT) and settles it
# when the send completes. Permits are keyed SQL rows with CAS
# transitions —never a shared dict / whole-JSON overwrite (lost-update
# ban). An unclosed permit counts as "not quiesced" for release and
# recovery decisions. Orphan permits (holder dead or row uncertain) keep
# their evidence and are recorded in the reconciliation table; originals
# (outbox payloads / mp3 files) are never deleted by this module.

_PERMIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbound_permits (
    permit_id   TEXT PRIMARY KEY,
    request_id  TEXT NOT NULL,
    destination TEXT NOT NULL,
    state       TEXT NOT NULL,
    opened_at   REAL NOT NULL,
    settled_at  REAL
);
CREATE INDEX IF NOT EXISTS outbound_permits_by_request
    ON outbound_permits (request_id);
CREATE TABLE IF NOT EXISTS reconciliation (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    permit_id   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    at          REAL NOT NULL
);
"""

PERMIT_OPEN = "open"
PERMIT_CLOSED = "closed"


def _ensure_permit_schema(conn) -> None:
    try:
        with conn:
            conn.executescript(_PERMIT_SCHEMA)
    except sqlite3.Error:
        raise SoulLeaseError("permit storage unavailable.")


def _permit_conn(ctx: AdmissionContext):
    return ctx.journal.conn


def open_permit(
    ctx: AdmissionContext,
    *,
    request_id: str,
    destination: str,
    now: float,
) -> str:
    """Atomically open one outbound permit for a request (send barrier).

    Must be called BEFORE the send. Returns the permit id; the caller
    must :func:`close_permit` it exactly once when the send settles.

    SETTLE-1: a sealed backend record (terminal or UNKNOWN) refuses new
    permits — fail closed with ``ValueError``, never an orphan permit.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError("destination must be a non-empty string.")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    try:
        ledger = ctx._backend_ledger
    except AttributeError:
        ledger = None
    if isinstance(ledger, dict):
        rec = ledger.get(request_id.strip())
        if rec is not None and getattr(rec, "sealed", False):
            raise ValueError("request sealed: refusing new permit.")
    permit_id = "permit-%s" % uuid.uuid4().hex
    conn = _permit_conn(ctx)
    try:
        with conn:
            conn.execute(
                "INSERT INTO outbound_permits "
                "(permit_id, request_id, destination, state, opened_at) "
                "VALUES (?,?,?,?,?)",
                (permit_id, request_id.strip(), destination.strip(),
                 PERMIT_OPEN, float(now)),
            )
    except sqlite3.Error:
        raise SoulLeaseError("permit storage unavailable.")
    return permit_id


def close_permit(
    ctx: AdmissionContext, *, permit_id: str, now: float
) -> bool:
    """CAS one permit open -> closed. Exactly-once settle; True iff closed."""
    if not isinstance(permit_id, str) or not permit_id.strip():
        raise ValueError("permit_id must be a non-empty string.")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    conn = _permit_conn(ctx)
    try:
        with conn:
            cur = conn.execute(
                "UPDATE outbound_permits SET state=?, settled_at=? "
                "WHERE permit_id=? AND state=?",
                (PERMIT_CLOSED, float(now), permit_id.strip(), PERMIT_OPEN),
            )
            return cur.rowcount == 1
    except sqlite3.Error:
        raise SoulLeaseError("permit storage unavailable.")


def count_open_permits(ctx: AdmissionContext, request_id: str) -> int:
    """Count unsettled permits for one request (pure read, 0 = quiesced)."""
    if not isinstance(request_id, str) or not request_id:
        return 0
    conn = _permit_conn(ctx)
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM outbound_permits "
            "WHERE request_id=? AND state=?",
            (request_id, PERMIT_OPEN),
        ).fetchone()
    except sqlite3.Error:
        raise SoulLeaseError("permit storage unavailable.")
    return int(row[0]) if row else 0


def record_reconciliation(
    ctx: AdmissionContext, *, request_id: str, permit_id: str,
    reason: str, now: float,
) -> None:
    """File one orphan-permit reconciliation record (evidence preserved)."""
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string.")
    if not isinstance(permit_id, str) or not permit_id.strip():
        raise ValueError("permit_id must be a non-empty string.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must be a non-empty string.")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    conn = _permit_conn(ctx)
    try:
        with conn:
            conn.execute(
                "INSERT INTO reconciliation (request_id, permit_id, reason, at) "
                "VALUES (?,?,?,?)",
                (request_id.strip(), permit_id.strip(), reason.strip(), float(now)),
            )
    except sqlite3.Error:
        raise SoulLeaseError("permit storage unavailable.")


# -- backend execution evidence (EI-2B-BACKEND-SETTLE-1) --------------------
#
# Upstream journal states stop at generated / transport_sent (§9 gaps G1/G2:
# no completed / failed / cancelled). Backend-terminal evidence therefore
# lives ONLY in the context-private memory ledger (request_id ->
# BackendRecord), bound at begin success (RUNNING) and advanced solely by
# real port returns. It is never persisted and never replayed: a restart
# drops it, and recovery must conservatively isolate, never re-release.
# Callers can never submit a "completed" boolean; only a real PortResult
# returned by the port creates completion evidence. FAILED is reachable only
# by abandoning a proven-clean RETRYABLE; general exceptions always map to
# UNKNOWN (never retryable, never FAILED).
#
# Port protocol (duck-typed, G5): ``execute(decision)`` -> PortResult or
# raise; ``cancel()`` best-effort halt; ``confirm_exited()`` truthy = exited
# clean (CANCELLED), falsy / missing / raising = fate unproven (UNKNOWN).
# The join point is deliberately NOT named ``join``: the static deadlock
# guard bans that attribute name in this module. A port must not expose
# ``destination`` / ``memory`` / ``completed`` attributes (caller-supplied
# routing or verdict evidence is refused outright).


def _bind_backend_running(
    ledger, *, soul_alias: str, request_id: str,
    generation: int, epoch: int,
):
    """Bind one admitted request RUNNING in the memory ledger (begin success).

    ``None`` ledger keeps the legacy journal-only path (no port rights).
    A sealed record is never rebound (fail closed, never resurrect).
    """
    if ledger is None:
        return None
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string.")
    existing = ledger.get(request_id)
    if existing is not None and getattr(existing, "sealed", False):
        raise ValueError("backend record sealed: refusing rebind.")
    rec = BackendRecord(
        soul_alias=soul_alias, request_id=request_id,
        generation=int(generation), epoch=int(epoch),
        attempt=0, state=BACKEND_RUNNING, sealed=False,
    )
    ledger[request_id] = rec
    return rec


def _require_live_backend_record(
    ctx: AdmissionContext, capability: Capability,
) -> BackendRecord:
    """Return the ledger record for a live capability, else refuse.

    Refusal (``ValueError``, fail closed, never a state): non-capability,
    capability not live in this context (stale or foreign holder), missing
    ledger record (journal-only legacy path or post-restart empty ledger:
    memory evidence is never re-created), or binding mismatch
    (alias / generation / request / epoch). Callers must re-admit; nothing
    here may mint evidence from the journal row alone.
    """
    if not isinstance(capability, Capability):
        raise ValueError("capability must be a Capability.")
    try:
        live = ctx.held.get(capability.soul_alias)
    except AttributeError:
        live = None
    if live != capability:
        raise ValueError("capability not live in this context (stale or foreign).")
    ledger = ctx._backend_ledger
    rec = ledger.get(capability.request_id) if isinstance(ledger, dict) else None
    if rec is None:
        raise ValueError("no backend evidence bound (journal-only or post-restart).")
    try:
        bound = (
            rec.soul_alias == capability.soul_alias
            and int(rec.generation) == int(capability.generation)
            and rec.request_id == capability.request_id
            and int(rec.epoch) == int(ctx.epoch)
        )
    except (TypeError, ValueError, AttributeError):
        bound = False
    if not bound:
        raise ValueError("backend evidence binding mismatch (stale attempt/epoch).")
    return rec


def _require_backend_port(port):
    """Validate the duck-typed backend port (G5 consumer side).

    Requires a callable ``execute``; refuses ports exposing ``destination``,
    ``memory``, or ``completed`` attributes (routing / verdict evidence must
    come from the real port return, never from caller-supplied fields).
    """
    if port is None or not callable(getattr(port, "execute", None)):
        raise ValueError("port must expose a callable execute(decision).")
    for forbidden in ("destination", "memory", "completed"):
        if getattr(port, forbidden, None) is not None:
            raise ValueError("port must not carry %r evidence." % (forbidden,))
    return port


def _apply_port_return(ctx: AdmissionContext, rec: BackendRecord, result) -> str:
    """Map one real port return onto the ledger record. Returns the state.

    ``PortResult(completed=True)`` fixes the response durably FIRST
    (``record_generated``); only a successful fix yields COMPLETED. A failed
    fix, ``completed=False``, or a non-``PortResult`` return proves nothing
    about side-effect fate: UNKNOWN (sealed, never retryable, never FAILED).
    """
    if isinstance(result, PortResult) and result.completed is True:
        try:
            ctx.journal.record_generated(
                rec.request_id, rec.epoch, result.response_id, result.response_body)
        except (JournalError, OwnershipError, ValueError, TypeError):
            rec.state = BACKEND_UNKNOWN
            rec.sealed = True
            return BACKEND_UNKNOWN
        rec.response_id = result.response_id
        rec.response_body = result.response_body
        rec.state = BACKEND_COMPLETED
        rec.sealed = True
        return BACKEND_COMPLETED
    rec.state = BACKEND_UNKNOWN
    rec.sealed = True
    return BACKEND_UNKNOWN


def _apply_port_error(rec: BackendRecord, exc: BaseException) -> str:
    """Map one port exception onto the ledger record. Returns the state.

    Only an explicit ``PortError(ambiguous=False)`` — the port's own proof
    of a side-effect-free failure — yields RETRYABLE (lease kept). An
    ambiguous ``PortError`` or ANY general exception proves nothing:
    UNKNOWN (sealed). General failures never enter RETRYABLE and there is
    no path from them to FAILED.
    """
    if isinstance(exc, PortError) and exc.ambiguous is False:
        rec.state = BACKEND_RETRYABLE
        return BACKEND_RETRYABLE
    rec.state = BACKEND_UNKNOWN
    rec.sealed = True
    return BACKEND_UNKNOWN


def submit_backend(
    ctx: AdmissionContext, capability: Capability,
    decision: Decision, port,
) -> str:
    """First backend call for one admitted request (G2/G5 consumer side).

    Requires a live capability, a bound RUNNING record, and a first
    submission (attempt 0, nothing stored yet). Stores the decision/port,
    pre-increments the attempt fence, then calls ``port.execute(decision)``
    exactly once and maps the real return. RETRYABLE records must use
    :func:`retry_backend` (re-authorization per attempt); sealed records
    refuse. Returns the new evidence state (``BACKEND_*``).
    """
    if not isinstance(decision, Decision):
        raise ValueError("decision must be a Decision.")
    _require_backend_port(port)
    rec = _require_live_backend_record(ctx, capability)
    if rec.sealed:
        raise ValueError("backend record sealed: refusing submit.")
    if rec.state != BACKEND_RUNNING:
        raise ValueError("submit allowed only from RUNNING (use retry_backend from RETRYABLE).")
    if rec.attempt != 0 or rec.decision is not None or rec.port is not None:
        raise ValueError("backend already submitted (use retry_backend).")
    rec.decision = decision
    rec.port = port
    rec.attempt += 1
    try:
        result = port.execute(decision)
    except Exception as exc:
        return _apply_port_error(rec, exc)
    return _apply_port_return(ctx, rec, result)


def retry_backend(
    ctx: AdmissionContext, capability: Capability, *,
    attempt: Optional[int] = None, abandon: bool = False,
) -> str:
    """Retry or abandon after a proven side-effect-free failure (G4).

    Replays the STORED decision/port — the caller supplies no new input and
    no verdict boolean. Normal path requires RETRYABLE (never sealed,
    never terminal): optionally fences on ``attempt`` (stale attempt
    refuses with zero resend), pre-increments, transitions to RUNNING
    BEFORE the call so a concurrent retry sees a non-retryable state and
    refuses without resending, then maps the real return. ``abandon=True``
    requires RETRYABLE and is the ONLY path to FAILED (the caller gives up
    a proven-clean retry). Retrying a sealed terminal record is an
    idempotent no-op returning its state (never resends). Returns the new
    evidence state (``BACKEND_*``).
    """
    if not isinstance(abandon, bool):
        raise ValueError("abandon must be a bool, not a truthy value.")
    rec = _require_live_backend_record(ctx, capability)
    if abandon:
        if rec.sealed or rec.state != BACKEND_RETRYABLE:
            raise ValueError("abandon allowed only from unsealed RETRYABLE.")
        rec.state = BACKEND_FAILED
        rec.sealed = True
        return BACKEND_FAILED
    if rec.sealed:
        return rec.state
    if rec.state != BACKEND_RETRYABLE:
        raise ValueError("retry allowed only from RETRYABLE.")
    if attempt is not None:
        if isinstance(attempt, bool):
            raise ValueError("attempt must be an int, not a bool.")
        try:
            fenced = int(attempt)
        except (TypeError, ValueError):
            raise ValueError("attempt must be an int.")
        if fenced != int(rec.attempt):
            raise ValueError("stale attempt (fencing): refusing resend.")
    if rec.decision is None or rec.port is None:
        raise ValueError("no stored backend call to replay.")
    rec.attempt += 1
    rec.state = BACKEND_RUNNING
    try:
        result = rec.port.execute(rec.decision)
    except Exception as exc:
        return _apply_port_error(rec, exc)
    return _apply_port_return(ctx, rec, result)


def cancel_backend(ctx: AdmissionContext, capability: Capability) -> str:
    """Cancel one request's backend call (G1 consumer side).

    Seals the record FIRST (no submit / retry / permit / dispatch past this
    point), then best-effort ``port.cancel()``, then the join point
    ``port.confirm_exited()`` (truthy = exited clean -> CANCELLED). A
    missing/unjoined ``confirm_exited``, a falsy return, or ANY exception
    from the port yields UNKNOWN (fate unproven, fail-closed isolation). An
    already-sealed record returns its state without touching the port.
    Cancelling before any submit proves zero side effects structurally
    (the port was never invoked) -> CANCELLED. Returns the evidence state
    (``BACKEND_*``).
    """
    rec = _require_live_backend_record(ctx, capability)
    if rec.sealed:
        return rec.state
    rec.sealed = True
    port = rec.port
    if port is None:
        rec.state = BACKEND_CANCELLED
        return BACKEND_CANCELLED
    try:
        cancel_fn = getattr(port, "cancel", None)
        if callable(cancel_fn):
            cancel_fn()
        confirm_fn = getattr(port, "confirm_exited", None)
        if not callable(confirm_fn):
            rec.state = BACKEND_UNKNOWN
            return BACKEND_UNKNOWN
        exited_clean = confirm_fn()
    except Exception:
        rec.state = BACKEND_UNKNOWN
        return BACKEND_UNKNOWN
    if exited_clean:
        rec.state = BACKEND_CANCELLED
        return BACKEND_CANCELLED
    rec.state = BACKEND_UNKNOWN
    return BACKEND_UNKNOWN


def _settle_with_backend_evidence(
    ctx: AdmissionContext, capability: Capability,
    rec: BackendRecord, permits_open: int, now: float, release_fn,
) -> str:
    """Settle one ledger-bound request per the SETTLE-1 end table.

    - terminal (COMPLETED / FAILED / CANCELLED) + sealed + zero permits ->
      release (tombstone kept on success for idempotent re-settle);
      terminal + open permits -> held (permits).
    - RUNNING / RETRYABLE -> held (tasks open), whatever the journal says.
    - UNKNOWN, unsealed-terminal contradiction, binding contradiction, or
      any future label -> isolate (quarantine on capability match).
    Never releases on memory outcomes, transport state, or finally/timeout.
    """
    try:
        bound = (
            rec.soul_alias == capability.soul_alias
            and int(rec.generation) == int(capability.generation)
            and rec.request_id == capability.request_id
            and int(rec.epoch) == int(ctx.epoch)
        )
    except (TypeError, ValueError, AttributeError):
        bound = False
    if not bound:
        try:
            ctx.lease_store.quarantine_self(capability, now)
        except (SoulLeaseError, ValueError):
            pass
        return SETTLE_ISOLATED
    state = rec.state
    if state in BACKEND_TERMINAL and rec.sealed:
        if permits_open == 0:
            verdict = release_fn()
            if verdict == SETTLE_RELEASED:
                ctx._backend_tombstones[capability.request_id] = state
            return verdict
        return SETTLE_HELD_PERMITS_OPEN
    if state in (BACKEND_RUNNING, BACKEND_RETRYABLE):
        return SETTLE_HELD_TASKS_OPEN
    try:
        ctx.lease_store.quarantine_self(capability, now)
    except (SoulLeaseError, ValueError):
        pass
    return SETTLE_ISOLATED


# -- settle table (§6, EI-2B-JOURNAL-ADAPT-1) ------------------------------------
#
# Fixed register — the ONLY ways a held lease ends:
# - "released": not-started + proven no side effect (accepted /
#   failed_before_execution row, zero permits). Upstream has NO
#   completed / failed / cancelled journal states (§9 gap G1/G2), so no
#   backend-terminal release exists here; memory outcomes and
#   transport_sent must never impersonate one.
# - "held_tasks_open": executing / generated — still working or response
#   fixed but uncommitted, never released.
# - "held_permits_open": not-started row with unclosed permits (defensive;
#   unreachable via the public admit path, which always begins).
# - "isolated": transport_sent (send fate unknown), uncertain, or any
#   ambiguous mix — quarantine on capability match, never free.
# Never released by finally, timeout, or outcome alone.
#
# SETTLE-1 ledger branch (takes effect ONLY when the request has memory
# backend evidence in ``ctx._backend_ledger``; journal-only requests keep
# the table above byte-identical):
# - tombstoned request id -> released (idempotent re-settle: no lease
#   touch, no port replay, ever again).
# - COMPLETED / FAILED / CANCELLED + sealed + zero permits -> release
#   (tombstone kept on success); terminal + open permits -> held_permits.
# - RUNNING / RETRYABLE -> held_tasks_open, whatever the journal says.
# - UNKNOWN, unsealed-terminal contradiction, binding contradiction, or
#   any future label -> isolated (quarantine on capability match).
# FAILED means a proven-clean abandonment only; general exceptions land
# UNKNOWN and can never release.

SETTLE_RELEASED = "released"
SETTLE_ISOLATED = "isolated"
SETTLE_HELD_TASKS_OPEN = "held_tasks_open"
SETTLE_HELD_PERMITS_OPEN = "held_permits_open"
SETTLE_REFUSED_UNKNOWN = "refused_unknown"


def settle_request(
    ctx: AdmissionContext,
    capability: Capability,
    now: float,
) -> str:
    """End one holder's lease per the fixed settle table.

    The caller proves task completion by driving the journal row forward
    first; this function re-reads (never trusts the caller's word) and
    either releases with proven quiescence or isolates. Returns one of
    the ``SETTLE_*`` verdicts. There is no backend-terminal release:
    upstream journal states stop at generated / transport_sent, so those
    (and uncertain) isolate — memory ``completed`` must never be read as
    a backend terminal state (§9 gap G1/G2, BLOCKED, not waived).

    SETTLE-1: when this context holds memory backend evidence for the
    request (``ctx._backend_ledger``), the ledger branch decides per the
    end table — including terminal release on COMPLETED / FAILED /
    CANCELLED (sealed, zero permits) with a memory tombstone for
    idempotent re-settle. Journal-only requests (no ledger record: legacy
    path or post-restart empty ledger) keep the journal-only rule exactly:
    executing / generated never release, so a restart can never re-release
    a backend whose memory evidence is gone.
    """
    if not isinstance(capability, Capability):
        raise ValueError("capability must be a Capability.")
    if isinstance(now, bool) or not isinstance(now, (int, float)):
        raise ValueError("now must be a caller-provided epoch number.")
    stamp = float(now)
    # SETTLE-1 fast path: a released terminal settles idempotent from the
    # memory tombstone — no lease touch, no port replay, ever again.
    try:
        tombstoned = (
            isinstance(ctx._backend_tombstones, dict)
            and capability.request_id in ctx._backend_tombstones
        )
    except AttributeError:
        tombstoned = False
    if tombstoned:
        return SETTLE_RELEASED
    try:
        row = _read_request_by_id(ctx.journal, capability.request_id)
    except JournalError:
        return SETTLE_REFUSED_UNKNOWN
    if row is None:
        return SETTLE_REFUSED_UNKNOWN
    state = row.get("state")
    permits_open = count_open_permits(ctx, capability.request_id)

    def _release() -> str:
        try:
            if ctx.lease_store.release(capability, stamp, quiesced=True):
                if ctx.held.get(capability.soul_alias) == capability:
                    ctx.held.pop(capability.soul_alias, None)
                return SETTLE_RELEASED
        except SoulLeaseError:
            return SETTLE_REFUSED_UNKNOWN
        return SETTLE_REFUSED_UNKNOWN

    try:
        ledger = ctx._backend_ledger
    except AttributeError:
        ledger = None
    backend_rec = (
        ledger.get(capability.request_id)
        if isinstance(ledger, dict) else None
    )
    if backend_rec is not None:
        return _settle_with_backend_evidence(
            ctx, capability, backend_rec, permits_open, stamp, _release)

    if state == STATE_ACCEPTED or state == STATE_FAILED_BEFORE_EXECUTION:
        # Not started: release only with proven zero side effect.
        if permits_open == 0:
            return _release()
        return SETTLE_HELD_PERMITS_OPEN
    if state == STATE_EXECUTING or state == STATE_GENERATED:
        # Still working / response fixed but uncommitted: never release.
        return SETTLE_HELD_TASKS_OPEN
    # TRANSPORT_SENT (send fate unknown), UNCERTAIN, or any future state:
    # isolate — quarantine on capability match, never free.
    try:
        ctx.lease_store.quarantine_self(capability, stamp)
    except (SoulLeaseError, ValueError):
        pass
    return SETTLE_ISOLATED


def settle_held(
    ctx: AdmissionContext, soul_alias: str, now: float
) -> str:
    """Settle this context's live hold for a soul (if any).

    Looks the capability up in :attr:`AdmissionContext.held` so entry
    and dispatch code never handles tokens directly. Returns the
    :func:`settle_request` verdict, or ``SETTLE_REFUSED_UNKNOWN`` when
    this context holds nothing for the soul.
    """
    cap = current_capability(ctx, soul_alias)
    if cap is None:
        return SETTLE_REFUSED_UNKNOWN
    return settle_request(ctx, cap, now)


def require_registered(
    journal: Journal, request_id: str, *,
    ledger: Optional[dict] = None,
) -> bool:
    """Dispatch-side O-gate: True only for a live executing request row.

    Payloads without ``soul_request_id`` are background (pass-through,
    never blocked here); payloads naming an unknown / non-executing row
    are unregistered and must go fail-closed uncertain with zero send.
    Storage failure reads as unregistered (fail closed, never dispatch).

    SETTLE-1: pass the context's ``_backend_ledger`` as ``ledger`` and a
    sealed record (terminal or UNKNOWN) reads unregistered even when its
    journal row still says executing. ``None`` (default) keeps the legacy
    journal-only rule byte-identical.
    """
    if not isinstance(request_id, str) or not request_id.strip():
        return False
    if isinstance(ledger, dict):
        rec = ledger.get(request_id.strip())
        if rec is not None and getattr(rec, "sealed", False):
            return False
    try:
        row = _read_request_by_id(journal, request_id.strip())
    except JournalError:
        return False
    if row is None:
        return False
    try:
        return row.get("state") == STATE_EXECUTING
    except AttributeError:
        return False
