"""EI-2A offline idempotency journal. Caller-owned SQLite, caller-owned clock.

Contract (work order EI-2A-JOURNAL-PORT-1, decision-complete):

- The constructor takes a caller-opened ``sqlite3.Connection``. There is no
  default path, no environment lookup, and import performs no IO.
- One row per idempotency ``namespace`` (unique key) holding the payload
  ``fingerprint``, the owning ``request_id``, the durable ``state``, the
  claiming ``epoch``, the fixed response, per-destination submit evidence,
  and create/expiry timestamps.
- ``claim()`` runs check-and-insert inside a single transaction and returns
  one of NEW / REPLAY / IN_PROGRESS / CONFLICT / UNCERTAIN / EXPIRED.
- Same key + different fingerprint is always CONFLICT (even past expiry).
- A terminal row older than 24h returns EXPIRED (callers map it to
  ``410 idempotency_expired`` and must mint a fresh key for a new event);
  the old operation is never redone automatically, and unresolved rows are
  never unblocked by expiry.
- ``begin()`` CASes accepted -> executing for exactly one executor.
- ``record_generated()`` only lets the holding executor fix response id/body.
- ``record_transport()`` stores evidence; it never changes commit state.
- ``recover_executing()`` only marks old-epoch executing rows uncertain.
- ``record_outcome()`` only accepts the predetermined destination/state
  enums; ``unknown`` never promotes to completed without explicit
  same-request completion evidence, and memory/bus evidence can never
  impersonate a backend commit.
- Storage errors are never leaked (generic ``JournalError``).

Pure stdlib. No network, no subprocess, no server, no env access.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Mapping, Optional


#: Idempotency window: terminal rows older than this report EXPIRED.
EXPIRY_SECONDS = 24 * 3600

#: Durable entry states. ``generated`` is durable but NOT terminal:
#: its fixed response must still replay after the window.
STATE_ACCEPTED = "accepted"
STATE_EXECUTING = "executing"
STATE_GENERATED = "generated"
STATE_UNCERTAIN = "uncertain"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"

STATES = frozenset(
    (
        STATE_ACCEPTED,
        STATE_EXECUTING,
        STATE_GENERATED,
        STATE_UNCERTAIN,
        STATE_COMPLETED,
        STATE_FAILED,
        STATE_CANCELLED,
    )
)

#: States that count as terminal for the 24h EXPIRED rule.
TERMINAL_STATES = frozenset((STATE_COMPLETED, STATE_FAILED, STATE_CANCELLED))

#: Predetermined submit destinations (transport vs memory are separate).
DESTINATIONS = ("backend", "bus", "memory")

#: Predetermined per-destination outcome states.
OUTCOME_STATES = ("ok", "failed", "unknown")

#: claim()/submit() outcome labels.
OUTCOME_NEW = "NEW"
OUTCOME_REPLAY = "REPLAY"
OUTCOME_IN_PROGRESS = "IN_PROGRESS"
OUTCOME_CONFLICT = "CONFLICT"
OUTCOME_UNCERTAIN = "UNCERTAIN"
OUTCOME_EXPIRED = "EXPIRED"


class JournalError(Exception):
    """Generic journal failure. Never carries storage internals."""


@dataclass(frozen=True)
class ClaimResult:
    outcome: str
    request_id: Optional[str]
    response_id: Optional[str]
    response_body: Optional[str]
    status: int
    code: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_entries (
    namespace   TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    request_id  TEXT NOT NULL UNIQUE,
    state       TEXT NOT NULL,
    epoch       INTEGER NOT NULL,
    response_id TEXT,
    response_body TEXT,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_transports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    destination TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    evidence    TEXT NOT NULL,
    at          REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id  TEXT NOT NULL,
    destination TEXT NOT NULL,
    state       TEXT NOT NULL,
    evidence    TEXT NOT NULL,
    at          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS journal_transports_by_request
    ON journal_transports (request_id);
CREATE INDEX IF NOT EXISTS journal_outcomes_by_request
    ON journal_outcomes (request_id);
"""

_ENTRY_COLUMNS = (
    "namespace, fingerprint, request_id, state, epoch, "
    "response_id, response_body, created_at, expires_at"
)


def _entry_dict(row) -> dict:
    return {
        "namespace": row[0],
        "fingerprint": row[1],
        "request_id": row[2],
        "state": row[3],
        "epoch": row[4],
        "response_id": row[5],
        "response_body": row[6],
        "created_at": row[7],
        "expires_at": row[8],
    }


class Journal:
    """Durable idempotency journal over a caller-owned connection."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError("Journal requires a caller-opened sqlite3.Connection.")
        self._conn = conn
        try:
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    # -- internal helpers -------------------------------------------------

    @staticmethod
    def _check_text(name: str, value: Any) -> str:
        if not isinstance(value, str) or value == "":
            raise ValueError(name + " must be a non-empty string.")
        return value

    @staticmethod
    def _check_now(now: Any) -> float:
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be a caller-provided epoch number.")
        return float(now)

    @staticmethod
    def _check_epoch(epoch: Any) -> int:
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError("epoch must be a non-negative int.")
        return epoch

    @staticmethod
    def _dump_evidence(evidence: Any) -> str:
        if not isinstance(evidence, Mapping):
            raise ValueError("evidence must be a mapping.")
        try:
            return json.dumps(dict(evidence), sort_keys=True, ensure_ascii=True)
        except (TypeError, ValueError):
            raise ValueError("evidence must be JSON-serializable.")

    @staticmethod
    def _classify(entry: dict, fingerprint: str, now: float) -> ClaimResult:
        if fingerprint != entry["fingerprint"]:
            # Same key, different payload: always CONFLICT, even past expiry.
            return ClaimResult(
                OUTCOME_CONFLICT, entry["request_id"], None, None,
                409, "idempotency_conflict",
            )
        state = entry["state"]
        if state in TERMINAL_STATES and now >= entry["expires_at"]:
            # Terminal and past the 24h window: demand a fresh key.
            # The old operation is NOT redone; unresolved rows never land here.
            return ClaimResult(
                OUTCOME_EXPIRED, entry["request_id"], entry["response_id"],
                entry["response_body"], 410, "idempotency_expired",
            )
        if state in (STATE_ACCEPTED, STATE_EXECUTING):
            return ClaimResult(
                OUTCOME_IN_PROGRESS, entry["request_id"], None, None,
                409, "idempotency_in_progress",
            )
        if state == STATE_UNCERTAIN:
            return ClaimResult(
                OUTCOME_UNCERTAIN, entry["request_id"], None, None,
                409, "idempotency_uncertain",
            )
        # generated, completed, failed, cancelled (within window): replay
        # the fixed response without touching the runtime or memory.
        return ClaimResult(
            OUTCOME_REPLAY, entry["request_id"], entry["response_id"],
            entry["response_body"], 200, "idempotency_replay",
        )

    # -- public API ---------------------------------------------------------

    def claim(
        self, namespace: str, fingerprint: str, request_id: str, now: float
    ) -> ClaimResult:
        """Claim an idempotency namespace in a single transaction."""
        self._check_text("namespace", namespace)
        self._check_text("fingerprint", fingerprint)
        self._check_text("request_id", request_id)
        stamp = self._check_now(now)
        expires_at = stamp + EXPIRY_SECONDS
        try:
            with self._conn:
                row = self._conn.execute(
                    "SELECT " + _ENTRY_COLUMNS + " FROM journal_entries WHERE namespace=?",
                    (namespace,),
                ).fetchone()
                if row is None:
                    try:
                        self._conn.execute(
                            "INSERT INTO journal_entries (" + _ENTRY_COLUMNS + ") "
                            "VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                namespace, fingerprint, request_id,
                                STATE_ACCEPTED, 0, None, None,
                                stamp, expires_at,
                            ),
                        )
                    except sqlite3.IntegrityError:
                        # Lost an insert race: another claimant won. Re-read
                        # inside the same transaction and classify as existing.
                        row = self._conn.execute(
                            "SELECT " + _ENTRY_COLUMNS + " FROM journal_entries WHERE namespace=?",
                            (namespace,),
                        ).fetchone()
                        if row is None:  # pragma: no cover - defensive
                            raise JournalError("journal storage unavailable.")
                        return self._classify(_entry_dict(row), fingerprint, stamp)
                    return ClaimResult(
                        OUTCOME_NEW, request_id, None, None,
                        201, "idempotency_new",
                    )
                return self._classify(_entry_dict(row), fingerprint, stamp)
        except JournalError:
            raise
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def begin(self, request_id: str, epoch: int) -> bool:
        """CAS accepted -> executing. Exactly one executor wins."""
        self._check_text("request_id", request_id)
        epoch = self._check_epoch(epoch)
        try:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE journal_entries SET state=?, epoch=? "
                    "WHERE request_id=? AND state=?",
                    (STATE_EXECUTING, epoch, request_id, STATE_ACCEPTED),
                )
                return cur.rowcount == 1
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def record_generated(
        self, request_id: str, epoch: int, response_id: str, response_body: str
    ) -> bool:
        """Fix the response. Only the holding executor (epoch) may do so."""
        self._check_text("request_id", request_id)
        epoch = self._check_epoch(epoch)
        self._check_text("response_id", response_id)
        if not isinstance(response_body, str):
            raise ValueError("response_body must be a string.")
        try:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE journal_entries SET state=?, response_id=?, "
                    "response_body=? "
                    "WHERE request_id=? AND epoch=? AND state=?",
                    (
                        STATE_GENERATED, response_id, response_body,
                        request_id, epoch, STATE_EXECUTING,
                    ),
                )
                return cur.rowcount == 1
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def record_transport(
        self,
        request_id: str,
        destination: str,
        ok: bool,
        evidence: Mapping[str, Any],
        now: float,
    ) -> bool:
        """Store per-destination transport evidence. Never changes commit state."""
        self._check_text("request_id", request_id)
        if destination not in DESTINATIONS:
            raise ValueError("unknown destination: %r." % (destination,))
        stamp = self._check_now(now)
        blob = self._dump_evidence(evidence)
        try:
            with self._conn:
                known = self._conn.execute(
                    "SELECT 1 FROM journal_entries WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if known is None:
                    return False
                self._conn.execute(
                    "INSERT INTO journal_transports "
                    "(request_id, destination, ok, evidence, at) "
                    "VALUES (?,?,?,?,?)",
                    (request_id, destination, 1 if ok else 0, blob, stamp),
                )
                return True
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def record_outcome(
        self,
        request_id: str,
        destination: str,
        state: str,
        evidence: Mapping[str, Any],
        now: float,
    ) -> bool:
        """Store a per-destination outcome; promote only on explicit backend
        completion evidence. ``unknown`` never promotes; memory/bus evidence
        can never impersonate a backend commit."""
        self._check_text("request_id", request_id)
        if destination not in DESTINATIONS:
            raise ValueError("unknown destination: %r." % (destination,))
        if state not in OUTCOME_STATES:
            raise ValueError("unknown outcome state: %r." % (state,))
        stamp = self._check_now(now)
        blob = self._dump_evidence(evidence)
        try:
            with self._conn:
                known = self._conn.execute(
                    "SELECT state FROM journal_entries WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if known is None:
                    return False
                self._conn.execute(
                    "INSERT INTO journal_outcomes "
                    "(request_id, destination, state, evidence, at) "
                    "VALUES (?,?,?,?,?)",
                    (request_id, destination, state, blob, stamp),
                )
                if destination == "backend" and isinstance(evidence, Mapping):
                    if state == "ok" and evidence.get("completed") is True:
                        self._conn.execute(
                            "UPDATE journal_entries SET state=? "
                            "WHERE request_id=? AND state IN (?,?,?)",
                            (
                                STATE_COMPLETED, request_id,
                                STATE_EXECUTING, STATE_GENERATED, STATE_UNCERTAIN,
                            ),
                        )
                    elif state == "failed" and evidence.get("definitive") is True:
                        self._conn.execute(
                            "UPDATE journal_entries SET state=? "
                            "WHERE request_id=? AND state IN (?,?,?)",
                            (
                                STATE_FAILED, request_id,
                                STATE_EXECUTING, STATE_GENERATED, STATE_UNCERTAIN,
                            ),
                        )
                return True
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def recover_executing(self, current_epoch: int) -> int:
        """Mark only old-epoch executing rows uncertain (restart recovery)."""
        current_epoch = self._check_epoch(current_epoch)
        try:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE journal_entries SET state=? "
                    "WHERE state=? AND epoch<?",
                    (STATE_UNCERTAIN, STATE_EXECUTING, current_epoch),
                )
                return cur.rowcount
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def mark_uncertain(self, request_id: str, epoch: int) -> bool:
        """Mark a same-epoch executing row uncertain (ambiguous backend error)."""
        self._check_text("request_id", request_id)
        epoch = self._check_epoch(epoch)
        try:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE journal_entries SET state=? "
                    "WHERE request_id=? AND epoch=? AND state=?",
                    (STATE_UNCERTAIN, request_id, epoch, STATE_EXECUTING),
                )
                return cur.rowcount == 1
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def cancel(self, request_id: str) -> bool:
        """Cancel an unresolved claim. The original execution opportunity is
        never reused: cancelled rows can never begin again."""
        self._check_text("request_id", request_id)
        try:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE journal_entries SET state=? "
                    "WHERE request_id=? AND state IN (?,?,?)",
                    (
                        STATE_CANCELLED, request_id,
                        STATE_ACCEPTED, STATE_EXECUTING, STATE_UNCERTAIN,
                    ),
                )
                return cur.rowcount == 1
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")

    def load(self, namespace: str) -> Optional[dict]:
        """Read one entry by exact namespace. Never spans namespaces."""
        self._check_text("namespace", namespace)
        try:
            row = self._conn.execute(
                "SELECT " + _ENTRY_COLUMNS + " FROM journal_entries WHERE namespace=?",
                (namespace,),
            ).fetchone()
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")
        if row is None:
            return None
        return _entry_dict(row)

    def load_by_request(self, request_id: str) -> Optional[dict]:
        """Read one entry by exact request id."""
        self._check_text("request_id", request_id)
        try:
            row = self._conn.execute(
                "SELECT " + _ENTRY_COLUMNS + " FROM journal_entries WHERE request_id=?",
                (request_id,),
            ).fetchone()
        except sqlite3.Error:
            raise JournalError("journal storage unavailable.")
        if row is None:
            return None
        return _entry_dict(row)
