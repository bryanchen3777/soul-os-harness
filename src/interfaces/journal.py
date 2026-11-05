"""Offline durable request journal (EI-2A). Pure stdlib only.

Injected sqlite3 connection only; never opens files, env, or network.
All durability decisions live in single BEGIN IMMEDIATE transactions.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional


EXPIRY_SECONDS = 86400

_VALID_STATES = ("accepted", "executing", "generated", "transport_sent", "failed_before_execution", "uncertain")
_VALID_KINDS = ("NEW", "REPLAY", "IN_PROGRESS", "CONFLICT", "UNCERTAIN", "EXPIRED")
_VALID_OUTCOME_STATUS = ("pending", "completed", "skipped", "unknown")
_MEMORY_DEST = "memory"
_DEFAULT_OUTCOMES_TEXT = '{"memory": "pending"}'
_SCHEMA_SQL = "CREATE TABLE IF NOT EXISTS requests (principal_id TEXT NOT NULL, agent_id TEXT NOT NULL, scope TEXT NOT NULL, conversation_id TEXT NOT NULL, branch_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, fingerprint TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE, state TEXT NOT NULL, epoch INTEGER NOT NULL, response_id TEXT, response_body TEXT, transport_count INTEGER NOT NULL DEFAULT 0, outcomes TEXT NOT NULL, created_ts REAL NOT NULL, expires_ts REAL NOT NULL, updated_ts REAL NOT NULL, UNIQUE (principal_id, agent_id, scope, conversation_id, branch_id, idempotency_key))"


class JournalError(Exception):
    pass


class OwnershipError(JournalError):
    pass


@dataclass(frozen=True)
class ClaimResult:
    kind: str
    request_id: str
    response_id: Optional[str] = None
    response_body: Optional[str] = None


def _norm_namespace(namespace) -> tuple:
    try:
        items = tuple(namespace)
    except Exception:
        raise JournalError("bad namespace")
    if len(items) != 6:
        raise JournalError("bad namespace")
    out = []
    for v in items:
        if v is None:
            out.append("")
        elif isinstance(v, str):
            out.append(v)
        else:
            raise JournalError("bad namespace")
    return (out[0], out[1], out[2], out[3], out[4], out[5])


def _parse_outcomes(text: str) -> dict:
    try:
        data = json.loads(text)
    except Exception:
        raise JournalError("bad outcomes")
    if not isinstance(data, dict):
        raise JournalError("bad outcomes")
    return data


def _dump_outcomes(data: dict) -> str:
    try:
        return json.dumps(data, sort_keys=True, separators=(",", ":"))
    except Exception:
        raise JournalError("bad outcomes")


class Journal:
    def __init__(self, conn, *, epoch) -> None:
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        self.conn = conn
        self.epoch = epoch
        try:
            self.conn.execute(_SCHEMA_SQL)
            self.conn.commit()
        except Exception as exc:
            try:
                self.conn.rollback()
            except Exception:
                pass
            raise JournalError("journal init failed")

    def claim(self, namespace, fingerprint, request_id, now) -> ClaimResult:
        ns = _norm_namespace(namespace)
        if not isinstance(fingerprint, str):
            raise JournalError("bad fingerprint")
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        try:
            ts = float(now)
        except Exception:
            raise JournalError("bad now")
        conn = self.conn
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT fingerprint, request_id, state, epoch, response_id, response_body, expires_ts"
                    " FROM requests WHERE principal_id=? AND agent_id=? AND scope=? AND"
                    " conversation_id=? AND branch_id=? AND idempotency_key=?",
                    ns,
                ).fetchone()
                if row is None:
                    created = ts
                    expires = ts + EXPIRY_SECONDS
                    conn.execute(
                        "INSERT INTO requests (principal_id, agent_id, scope, conversation_id,"
                        " branch_id, idempotency_key, fingerprint, request_id, state, epoch,"
                        " response_id, response_body, transport_count, outcomes,"
                        " created_ts, expires_ts, updated_ts)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            ns[0],
                            ns[1],
                            ns[2],
                            ns[3],
                            ns[4],
                            ns[5],
                            fingerprint,
                            request_id,
                            "accepted",
                            self.epoch,
                            None,
                            None,
                            0,
                            _DEFAULT_OUTCOMES_TEXT,
                            created,
                            expires,
                            created,
                        ),
                    )
                    conn.commit()
                    return ClaimResult("NEW", request_id)
                sfp = row[0]
                srid = row[1]
                state = row[2]
                sepoch = row[3]
                sresp_id = row[4]
                sresp_body = row[5]
                sexp = row[6]
                if sfp != fingerprint:
                    conn.commit()
                    return ClaimResult("CONFLICT", srid)
                if state == "accepted":
                    conn.commit()
                    return ClaimResult("IN_PROGRESS", srid)
                if state == "executing":
                    if sepoch == self.epoch:
                        conn.commit()
                        return ClaimResult("IN_PROGRESS", srid)
                    if sepoch < self.epoch:
                        conn.execute(
                            "UPDATE requests SET state='uncertain', updated_ts=?"
                            " WHERE principal_id=? AND agent_id=? AND scope=?"
                            " AND conversation_id=? AND branch_id=? AND idempotency_key=?"
                            " AND state='executing'",
                            (ts, ns[0], ns[1], ns[2], ns[3], ns[4], ns[5]),
                        )
                        conn.commit()
                        return ClaimResult("UNCERTAIN", srid)
                    conn.commit()
                    return ClaimResult("IN_PROGRESS", srid)
                if state == "uncertain":
                    conn.commit()
                    return ClaimResult("UNCERTAIN", srid)
                if state == "failed_before_execution":
                    conn.execute(
                        "UPDATE requests SET state='accepted', epoch=?, expires_ts=?, updated_ts=?"
                        " WHERE principal_id=? AND agent_id=? AND scope=?"
                        " AND conversation_id=? AND branch_id=? AND idempotency_key=?"
                        " AND state='failed_before_execution'",
                        (self.epoch, ts + EXPIRY_SECONDS, ts, ns[0], ns[1], ns[2], ns[3], ns[4], ns[5]),
                    )
                    conn.commit()
                    return ClaimResult("NEW", srid)
                if state == "generated" or state == "transport_sent":
                    try:
                        expired = float(ts) > float(sexp)
                    except Exception:
                        raise JournalError("bad expiry")
                    if expired:
                        conn.commit()
                        return ClaimResult("EXPIRED", srid)
                    conn.commit()
                    return ClaimResult("REPLAY", srid, sresp_id, sresp_body)
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("bad state")
            except JournalError:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("claim failed")
        except JournalError:
            raise
        except Exception as exc:
            raise JournalError("claim failed")

    def begin(self, request_id, epoch) -> None:
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        conn = self.conn
        try:
            cur = conn.execute(
                "UPDATE requests SET state='executing' WHERE request_id=? AND state='accepted' AND epoch=?",
                (request_id, epoch),
            )
            if cur.rowcount != 1:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise OwnershipError("begin ownership failed")
            conn.commit()
        except OwnershipError:
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("begin failed")

    def record_generated(self, request_id, epoch, response_id, response_body) -> None:
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        conn = self.conn
        try:
            row = conn.execute(
                "SELECT state, epoch, response_id, response_body FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("unknown request")
            state = row[0]
            sepoch = row[1]
            sresp_id = row[2]
            sresp_body = row[3]
            if sepoch != epoch:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise OwnershipError("generated ownership failed")
            if state == "executing":
                conn.execute(
                    "UPDATE requests SET response_id=?, response_body=?, state='generated'"
                    " WHERE request_id=?",
                    (response_id, response_body, request_id),
                )
                conn.commit()
                return
            if state == "generated" or state == "transport_sent":
                if sresp_id == response_id and sresp_body == response_body:
                    try:
                        conn.commit()
                    except Exception:
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                        raise JournalError("generated commit failed")
                    return
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("generated response mismatch")
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("bad state for generated")
        except (JournalError, OwnershipError):
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("record_generated failed")

    def record_transport(self, request_id, epoch) -> None:
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        conn = self.conn
        try:
            row = conn.execute(
                "SELECT state, epoch FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("unknown request")
            state = row[0]
            sepoch = row[1]
            if sepoch != epoch:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise OwnershipError("transport ownership failed")
            if state == "generated":
                conn.execute(
                    "UPDATE requests SET state='transport_sent', transport_count=transport_count+1"
                    " WHERE request_id=?",
                    (request_id,),
                )
                conn.commit()
                return
            if state == "transport_sent":
                conn.execute(
                    "UPDATE requests SET transport_count=transport_count+1 WHERE request_id=?",
                    (request_id,),
                )
                conn.commit()
                return
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("bad state for transport")
        except (JournalError, OwnershipError):
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("record_transport failed")

    def record_outcome(self, request_id, epoch, dest, status, evidence=None) -> None:
        if dest != _MEMORY_DEST:
            raise JournalError("bad dest")
        if status not in _VALID_OUTCOME_STATUS:
            raise JournalError("bad status")
        if status == "completed":
            if evidence is None:
                raise JournalError("completed requires evidence")
            if isinstance(evidence, str) and evidence == "":
                raise JournalError("completed requires evidence")
            if isinstance(evidence, (list, dict, set, tuple)) and len(evidence) == 0:
                raise JournalError("completed requires evidence")
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        conn = self.conn
        try:
            row = conn.execute(
                "SELECT epoch, outcomes FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("unknown request")
            sepoch = row[0]
            if sepoch != epoch:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise OwnershipError("outcome ownership failed")
            data = _parse_outcomes(row[1])
            current = data.get(_MEMORY_DEST, "pending")
            if current == "completed" and status != "completed":
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("completed cannot downgrade")
            data[_MEMORY_DEST] = status
            text = _dump_outcomes(data)
            conn.execute(
                "UPDATE requests SET outcomes=? WHERE request_id=?",
                (text, request_id),
            )
            conn.commit()
        except (JournalError, OwnershipError):
            raise
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("record_outcome failed")

    def mark_uncertain(self, request_id) -> None:
        """Fail-closed monotonic move: executing -> uncertain (any epoch).

        Already-uncertain is a no-op success. Any other state, or an
        unknown request, raises JournalError. No epoch check: uncertainty
        must never be blocked by a stale owner.
        """
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        conn = self.conn
        try:
            row = conn.execute(
                "SELECT state FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("unknown request")
            if row[0] == "uncertain":
                try:
                    conn.commit()
                except Exception:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise JournalError("mark_uncertain failed")
                return
            if row[0] != "executing":
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("bad state for uncertain")
            conn.execute(
                "UPDATE requests SET state='uncertain' WHERE request_id=?",
                (request_id,),
            )
            conn.commit()
        except JournalError:
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("mark_uncertain failed")

    def mark_failed_before_execution(self, request_id, epoch) -> None:
        """Owner-only move: accepted -> failed_before_execution.

        Gives the failed_before_execution state a real producer: a claim
        whose execution could never start. Re-claim of the same key and
        fingerprint later resets to accepted (see claim).
        """
        if not isinstance(request_id, str) or request_id == "":
            raise JournalError("bad request_id")
        if not isinstance(epoch, int) or isinstance(epoch, bool):
            raise JournalError("bad epoch")
        conn = self.conn
        try:
            row = conn.execute(
                "SELECT state, epoch FROM requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            if row is None:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("unknown request")
            if row[1] != epoch:
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise OwnershipError("failed_before_execution ownership failed")
            if row[0] != "accepted":
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise JournalError("bad state for failed_before_execution")
            conn.execute(
                "UPDATE requests SET state='failed_before_execution' WHERE request_id=?",
                (request_id,),
            )
            conn.commit()
        except (JournalError, OwnershipError):
            raise
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("mark_failed_before_execution failed")

    def recover(self):
        conn = self.conn
        try:
            rows = conn.execute(
                "SELECT request_id FROM requests WHERE state='executing' AND epoch<?",
                (self.epoch,),
            ).fetchall()
            ids = [r[0] for r in rows]
            conn.execute(
                "UPDATE requests SET state='uncertain' WHERE state='executing' AND epoch<?",
                (self.epoch,),
            )
            conn.commit()
            return ids
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise JournalError("recover failed")
