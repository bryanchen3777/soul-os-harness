"""EI-2B per-soul lease table (identity mutex, not TTL takeover).

Contract (work order EI-2B-PROGRAM-3, decision-complete; reverses the
PROGRAM-2 TTL-takeover half):

- Rows are persistent: FREE / HELD / QUARANTINED states, never deleted.
  ``generation`` increments on every FREE -> HELD transition and never
  resets. An expired heartbeat is NOT a takeover licence: HELD stays
  busy until proven-dead + side-effect-quiescent evidence arrives.
- Identity is a capability: ``(alias, holder_token, request_id,
  generation)``. The store additionally binds the process nonce, PID +
  process creation time, and journal epoch. The token is never returned
  to callers outside the capability object, never logged, and ``repr``
  is redacted.
- Caller-owned ``sqlite3.Connection``, caller-owned clock. Import
  performs no IO and reads no environment. Pure stdlib: no network,
  no subprocess spawn, no server, no env access. Liveness probing is
  read-only (never requests terminate rights).
"""

from __future__ import annotations

import ctypes
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("soul_os.soul_lease")

#: Row states. FREE rows persist (never deleted); QUARANTINED rows leave
#: only on proven-dead + quiesced evidence, never on time.
STATE_FREE = "FREE"
STATE_HELD = "HELD"
STATE_QUARANTINED = "QUARANTINED"

#: Liveness tri-state returned by the process adapter.
LIVENESS_ALIVE = "ALIVE"
LIVENESS_DEAD = "DEAD"
LIVENESS_UNKNOWN = "UNKNOWN"

#: PRAGMA profile for every lease connection (work order §3).
BUSY_TIMEOUT_MS = 1000

#: Default heartbeat TTL kept only as a stale-heartbeat *observation*
#: (reported, never acted on). Takeover on expiry is forbidden.
LEASE_DEFAULT_TTL_SECONDS = 1800.0


class SoulLeaseError(Exception):
    """Generic lease-store failure. Never carries storage internals."""


@dataclass(frozen=True)
class Capability:
    """Identity capability for one lease hold.

    ``holder_token`` is opaque owner proof. ``repr`` is redacted so a
    capability can never leak the token into logs.
    """

    soul_alias: str
    holder_token: str
    request_id: str
    generation: int

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            "Capability(soul_alias=%r, holder_token=<redacted>, "
            "request_id=%r, generation=%r)"
            % (self.soul_alias, self.request_id, self.generation)
        )


@dataclass(frozen=True)
class AcquireResult:
    """Outcome of one ``acquire`` attempt (no token material).

    ``holder_entry`` is a non-authoritative display hint for 409 busy
    bodies only; it never participates in capability matching.
    """

    won: bool
    holder_request_id: str
    holder_generation: int
    holder_state: str
    holder_entry: str = "unknown"


@dataclass(frozen=True)
class Liveness:
    """One verified liveness verdict (tri-state, evidence-bound)."""

    state: str  # ALIVE / DEAD / UNKNOWN
    detail: str = ""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS soul_leases (
    soul_alias        TEXT PRIMARY KEY,
    state             TEXT NOT NULL,
    holder_token      TEXT NOT NULL DEFAULT '',
    holder_request_id TEXT NOT NULL DEFAULT '',
    holder_entry      TEXT NOT NULL DEFAULT '',
    generation        INTEGER NOT NULL DEFAULT 0,
    process_nonce     TEXT NOT NULL DEFAULT '',
    pid               INTEGER NOT NULL DEFAULT 0,
    process_create_time REAL NOT NULL DEFAULT 0,
    journal_epoch     INTEGER NOT NULL DEFAULT 0,
    heartbeat         REAL NOT NULL DEFAULT 0,
    updated_at        REAL NOT NULL DEFAULT 0
);
"""


def configure_connection(conn: sqlite3.Connection) -> None:
    """Apply the §3 PRAGMA profile to a caller-owned connection.

    Refuses to run inside a caller transaction (never hides commit
    responsibility). Idempotent.
    """
    if not isinstance(conn, sqlite3.Connection):
        raise TypeError("configure_connection requires a caller-opened sqlite3.Connection.")
    if conn.in_transaction:
        raise SoulLeaseError("lease connection busy: caller transaction in progress.")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA busy_timeout=%d" % BUSY_TIMEOUT_MS)
    except sqlite3.Error:
        raise SoulLeaseError("soul lease storage unavailable.")


def query_process_liveness(pid: int, process_create_time: float = 0.0) -> Liveness:
    """Read-only liveness probe (Windows: handle + create time + exit code).

    - Opens the process with query-only rights (never terminate rights).
    - Compares creation time when the holder recorded one: a PID reuse
      with a different creation time is NOT the holder -> UNKNOWN (never
      DEAD on PID alone).
    - PID / epoch difference / missing heartbeat are never death
      evidence -> at most UNKNOWN.
    - Permission denial / identity mismatch -> UNKNOWN, never DEAD.
    - Non-Windows platforms: cannot verify -> UNKNOWN (never DEAD).

    Pure read: no kill, no handle inheritance, handle always closed.
    """
    if os.name != "nt":
        return Liveness(LIVENESS_UNKNOWN, "non-windows probe unavailable")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return Liveness(LIVENESS_UNKNOWN, "invalid pid")
    try:
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    except Exception as exc:  # pragma: no cover - platform defensive
        return Liveness(LIVENESS_UNKNOWN, "kernel32 unavailable: %s" % exc)
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    SYNCHRONIZE = 0x00100000
    STILL_ACTIVE = 259
    handle = None
    try:
        handle = _kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | SYNCHRONIZE, False, pid
        )
        if not handle:
            # Cannot open: access denied or no such process. Both map to
            # UNKNOWN — absence of a handle is not death evidence.
            return Liveness(LIVENESS_UNKNOWN, "open-process failed (denied or gone)")
        creation = ctypes.c_ulonglong(0)
        _exit = ctypes.c_ulonglong(0)
        exit_code = ctypes.c_ulong()
        GetProcessTimes = getattr(_kernel32, "GetProcessTimes", None)
        GetExitCodeProcess = getattr(_kernel32, "GetExitCodeProcess", None)
        if GetProcessTimes is not None:
            c = ctypes.c_ulonglong(0)
            e = ctypes.c_ulonglong(0)
            k = ctypes.c_ulonglong(0)
            u = ctypes.c_ulonglong(0)
            try:
                ok = GetProcessTimes(
                    handle,
                    ctypes.byref(c),
                    ctypes.byref(e),
                    ctypes.byref(k),
                    ctypes.byref(u),
                )
                if ok:
                    creation = c
            except Exception:
                pass
        if process_create_time and int(creation.value) != 0:
            # FILETIME is 100ns ticks; stored create time is epoch
            # seconds. Compare with 2s tolerance; mismatch = PID reuse.
            recorded = int((float(process_create_time) + 11644473600.0) * 10000000.0)
            if abs(int(creation.value) - recorded) > 20000000:
                return Liveness(LIVENESS_UNKNOWN, "pid reused by another process")
        if GetExitCodeProcess is not None:
            try:
                if GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    if int(exit_code.value) != STILL_ACTIVE:
                        return Liveness(LIVENESS_DEAD, "exit code %d" % int(exit_code.value))
                    return Liveness(LIVENESS_ALIVE, "still active, identity matched")
            except Exception:
                pass
        return Liveness(LIVENESS_UNKNOWN, "exit signal unreadable")
    finally:
        try:
            if handle:
                _kernel32.CloseHandle(handle)
        except Exception:
            pass


def own_process_create_time() -> float:
    """Best-effort own-process creation time (epoch seconds, 0.0 unknown).

    Windows: read-only GetProcessTimes on the current process pseudo
    handle (no new handle, nothing to close). Other platforms: 0.0
    (recorded as-is; a zero recorded time disables the reuse check and
    the probe degrades to exit-signal-only, i.e. UNKNOWN unless the
    exit code proves death).
    """
    if os.name != "nt":
        return 0.0
    try:
        _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        GetCurrentProcess = getattr(_kernel32, "GetCurrentProcess", None)
        GetProcessTimes = getattr(_kernel32, "GetProcessTimes", None)
        if GetCurrentProcess is None or GetProcessTimes is None:
            return 0.0
        handle = GetCurrentProcess()
        c = ctypes.c_ulonglong(0)
        e = ctypes.c_ulonglong(0)
        k = ctypes.c_ulonglong(0)
        u = ctypes.c_ulonglong(0)
        if not GetProcessTimes(
            handle, ctypes.byref(c), ctypes.byref(e), ctypes.byref(k), ctypes.byref(u)
        ):
            return 0.0
        if int(c.value) == 0:
            return 0.0
        return float(int(c.value) / 10000000.0 - 11644473600.0)
    except Exception:
        return 0.0


class SoulLeaseStore:
    """Per-soul identity mutex over a caller-owned connection.

    Every mutating operation runs its own ``BEGIN IMMEDIATE`` with a
    conditional ``UPDATE`` + ``rowcount == 1`` check; the commit is the
    success signal. Any caller transaction in progress is refused, never
    joined or committed on the caller's behalf.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError("SoulLeaseStore requires a caller-opened sqlite3.Connection.")
        self._conn = conn
        try:
            configure_connection(conn)
            with self._conn:
                self._conn.executescript(_SCHEMA)
        except SoulLeaseError:
            raise
        except sqlite3.Error:
            raise SoulLeaseError("soul lease storage unavailable.")

    # -- validation (mirrors journal.py style: caller clock, strict types) --

    @staticmethod
    def _check_text(name: str, value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(name + " must be a non-empty string.")
        return value.strip()

    @staticmethod
    def _check_now(now) -> float:
        if isinstance(now, bool) or not isinstance(now, (int, float)):
            raise ValueError("now must be a caller-provided epoch number.")
        return float(now)

    @staticmethod
    def _check_epoch(epoch) -> int:
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch < 0:
            raise ValueError("epoch must be a non-negative int.")
        return epoch

    @staticmethod
    def _check_generation(generation) -> int:
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
            raise ValueError("generation must be a non-negative int.")
        return generation

    @staticmethod
    def _check_pid(pid) -> int:
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 0:
            raise ValueError("pid must be a non-negative int.")
        return pid

    def _begin_immediate(self) -> None:
        try:
            in_tx = self._conn.in_transaction
        except sqlite3.Error:
            raise SoulLeaseError("soul lease storage unavailable.")
        if in_tx:
            raise SoulLeaseError("lease connection busy: caller transaction in progress.")
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            raise SoulLeaseError("soul lease storage unavailable.")

    # -- public API ---------------------------------------------------------

    def acquire(
        self,
        soul_alias: str,
        holder_token: str,
        request_id: str,
        process_nonce: str,
        pid: int,
        process_create_time,
        journal_epoch: int,
        now,
        holder_entry: str = "unknown",
    ) -> AcquireResult:
        """Take the soul lease: only FREE -> HELD, generation + 1.

        HELD (even with a stale heartbeat) and QUARANTINED rows stay
        busy — expiry is observed, never acted on. First-ever holder
        inserts at generation 1. Returns the live holder identity class
        (request id + generation + state) without token material.
        """
        soul = self._check_text("soul_alias", soul_alias)
        token = self._check_text("holder_token", holder_token)
        req = self._check_text("request_id", request_id)
        nonce = self._check_text("process_nonce", process_nonce)
        pid = self._check_pid(pid)
        create_time = self._check_now(process_create_time)
        journal_epoch = self._check_epoch(journal_epoch)
        stamp = self._check_now(now)
        entry = holder_entry if isinstance(holder_entry, str) and holder_entry.strip() else "unknown"
        self._begin_immediate()
        try:
            row = self._conn.execute(
                "SELECT state, holder_request_id, generation FROM soul_leases WHERE soul_alias=?",
                (soul,),
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO soul_leases "
                    "(soul_alias, state, holder_token, holder_request_id, holder_entry, "
                    " generation, process_nonce, pid, process_create_time, "
                    " journal_epoch, heartbeat, updated_at) "
                    "VALUES (?, 'HELD', ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                    (soul, token, req, entry, nonce, pid, create_time,
                     journal_epoch, stamp, stamp),
                )
                self._conn.execute("COMMIT")
                return AcquireResult(True, req, 1, STATE_HELD, entry)
            state, cur_req, generation = str(row[0]), str(row[1]), int(row[2])
            if state != STATE_FREE:
                self._conn.execute("ROLLBACK")
                hint = self._conn.execute(
                    "SELECT holder_entry FROM soul_leases WHERE soul_alias=?",
                    (soul,),
                ).fetchone()
                return AcquireResult(
                    False, cur_req, generation, state,
                    str(hint[0]) if hint and hint[0] else "unknown",
                )
            cur = self._conn.execute(
                "UPDATE soul_leases SET state='HELD', holder_token=?, "
                "holder_request_id=?, holder_entry=?, generation=generation+1, "
                "process_nonce=?, pid=?, process_create_time=?, "
                "journal_epoch=?, heartbeat=?, updated_at=? "
                "WHERE soul_alias=? AND state='FREE'",
                (token, req, entry, nonce, pid, create_time,
                 journal_epoch, stamp, stamp, soul),
            )
            if cur.rowcount != 1:
                self._conn.execute("ROLLBACK")
                live = self._conn.execute(
                    "SELECT holder_request_id, generation, state, holder_entry "
                    "FROM soul_leases WHERE soul_alias=?",
                    (soul,),
                ).fetchone()
                if live is None:  # pragma: no cover - defensive
                    raise SoulLeaseError("soul lease storage unavailable.")
                return AcquireResult(
                    False, str(live[0]), int(live[1]), str(live[2]),
                    str(live[3]) if live[3] else "unknown",
                )
            self._conn.execute("COMMIT")
            return AcquireResult(True, req, generation + 1, STATE_HELD, entry)
        except SoulLeaseError:
            raise
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise SoulLeaseError("soul lease storage unavailable.")

    def renew(self, capability: Capability, now) -> bool:
        """Refresh the heartbeat: full-capability match on a HELD row only.

        Generation is unchanged. Returns False (not error) on any
        mismatch — a renew that does not match proves nothing.
        """
        if not isinstance(capability, Capability):
            raise ValueError("capability must be a Capability.")
        self._check_text("soul_alias", capability.soul_alias)
        self._check_text("holder_token", capability.holder_token)
        self._check_text("request_id", capability.request_id)
        self._check_generation(capability.generation)
        stamp = self._check_now(now)
        self._begin_immediate()
        try:
            cur = self._conn.execute(
                "UPDATE soul_leases SET heartbeat=?, updated_at=? "
                "WHERE soul_alias=? AND state='HELD' AND holder_token=? "
                "AND holder_request_id=? AND generation=?",
                (stamp, stamp, capability.soul_alias, capability.holder_token,
                 capability.request_id, capability.generation),
            )
            ok = cur.rowcount == 1
            self._conn.execute("COMMIT" if ok else "ROLLBACK")
            return ok
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise SoulLeaseError("soul lease storage unavailable.")

    def release(self, capability: Capability, now, quiesced: bool) -> bool:
        """Release a hold: HELD -> FREE on capability match + quiescence.

        ``quiesced`` is coordinator-proven evidence that the holder's
        work and all outbound side effects are settled (unclosed permits
        count as not quiesced). Without it the release is refused. The
        row persists as FREE with its generation (next acquire + 1).
        """
        if not isinstance(capability, Capability):
            raise ValueError("capability must be a Capability.")
        self._check_text("soul_alias", capability.soul_alias)
        self._check_text("holder_token", capability.holder_token)
        self._check_text("request_id", capability.request_id)
        self._check_generation(capability.generation)
        stamp = self._check_now(now)
        if quiesced is not True:
            return False
        self._begin_immediate()
        try:
            cur = self._conn.execute(
                "UPDATE soul_leases SET state='FREE', updated_at=? "
                "WHERE soul_alias=? AND state='HELD' AND holder_token=? "
                "AND holder_request_id=? AND generation=?",
                (stamp, capability.soul_alias, capability.holder_token,
                 capability.request_id, capability.generation),
            )
            ok = cur.rowcount == 1
            self._conn.execute("COMMIT" if ok else "ROLLBACK")
            return ok
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise SoulLeaseError("soul lease storage unavailable.")

    def recover(
        self,
        soul_alias: str,
        liveness: Liveness,
        now,
        side_effects_quiesced: bool,
    ) -> str:
        """Per-holder recovery driven by verified evidence (work order §3).

        - ALIVE -> no change (a live holder is never moved).
        - UNKNOWN -> HELD -> QUARANTINED (quarantine, never free).
        - DEAD + quiesced -> HELD/QUARANTINED -> FREE.
        - DEAD + not quiesced -> HELD -> QUARANTINED.
        - FREE rows are untouched. QUARANTINED leaves only on
          DEAD + quiesced evidence, never on time.
        Returns the resulting state.
        """
        soul = self._check_text("soul_alias", soul_alias)
        if not isinstance(liveness, Liveness) or liveness.state not in (
            LIVENESS_ALIVE, LIVENESS_DEAD, LIVENESS_UNKNOWN,
        ):
            raise ValueError("liveness must be a Liveness tri-state.")
        stamp = self._check_now(now)
        self._begin_immediate()
        try:
            row = self._conn.execute(
                "SELECT state FROM soul_leases WHERE soul_alias=?", (soul,)
            ).fetchone()
            if row is None:
                self._conn.execute("ROLLBACK")
                return STATE_FREE
            state = str(row[0])
            if liveness.state == LIVENESS_ALIVE:
                self._conn.execute("ROLLBACK")
                return state
            if state == STATE_FREE:
                self._conn.execute("ROLLBACK")
                return STATE_FREE
            if liveness.state == LIVENESS_UNKNOWN:
                if state == STATE_HELD:
                    self._conn.execute(
                        "UPDATE soul_leases SET state='QUARANTINED', updated_at=? "
                        "WHERE soul_alias=? AND state='HELD'",
                        (stamp, soul),
                    )
                    self._conn.execute("COMMIT")
                    return STATE_QUARANTINED
                self._conn.execute("ROLLBACK")
                return state
            # DEAD, verified.
            if side_effects_quiesced is True:
                self._conn.execute(
                    "UPDATE soul_leases SET state='FREE', updated_at=? "
                    "WHERE soul_alias=? AND state IN ('HELD','QUARANTINED')",
                    (stamp, soul),
                )
                self._conn.execute("COMMIT")
                return STATE_FREE
            if state == STATE_HELD:
                self._conn.execute(
                    "UPDATE soul_leases SET state='QUARANTINED', updated_at=? "
                    "WHERE soul_alias=? AND state='HELD'",
                    (stamp, soul),
                )
                self._conn.execute("COMMIT")
                return STATE_QUARANTINED
            self._conn.execute("ROLLBACK")
            return state
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise SoulLeaseError("soul lease storage unavailable.")

    def quarantine_self(self, capability: Capability, now) -> bool:
        """Live-holder self-isolation: HELD -> QUARANTINED on match.

        Used when the holder itself discovers its fate is ambiguous
        (cancel without join, backend/send outcome unknown, crash
        recovery): the holder is alive but must not proceed. Never
        time-driven; QUARANTINED leaves only via :meth:`recover` with
        DEAD + quiesced evidence.
        """
        if not isinstance(capability, Capability):
            raise ValueError("capability must be a Capability.")
        self._check_text("soul_alias", capability.soul_alias)
        self._check_text("holder_token", capability.holder_token)
        self._check_text("request_id", capability.request_id)
        self._check_generation(capability.generation)
        stamp = self._check_now(now)
        self._begin_immediate()
        try:
            cur = self._conn.execute(
                "UPDATE soul_leases SET state='QUARANTINED', updated_at=? "
                "WHERE soul_alias=? AND state='HELD' AND holder_token=? "
                "AND holder_request_id=? AND generation=?",
                (stamp, capability.soul_alias, capability.holder_token,
                 capability.request_id, capability.generation),
            )
            ok = cur.rowcount == 1
            self._conn.execute("COMMIT" if ok else "ROLLBACK")
            return ok
        except sqlite3.Error:
            try:
                self._conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise SoulLeaseError("soul lease storage unavailable.")

    def all_rows(self) -> list:
        """List every lease row identity (pure read, no token material).

        Used by startup recovery to verify each holder one by one.
        Returns ``soul_alias`` strings only; callers re-read rows they
        need via :meth:`load`.
        """
        try:
            rows = self._conn.execute(
                "SELECT soul_alias FROM soul_leases ORDER BY soul_alias"
            ).fetchall()
        except sqlite3.Error:
            raise SoulLeaseError("soul lease storage unavailable.")
        return [str(r[0]) for r in rows]

    def load(self, soul_alias: str) -> Optional[dict]:
        """Read the lease row for one soul (pure read, no token leak).

        The token hash presence is reported as a boolean (``has_token``)
        so diagnostics can distinguish FREE rows without exposing
        token material.
        """
        soul = self._check_text("soul_alias", soul_alias)
        try:
            row = self._conn.execute(
                "SELECT soul_alias, state, holder_request_id, holder_entry, "
                "generation, process_nonce, pid, process_create_time, "
                "journal_epoch, heartbeat, updated_at, "
                "CASE WHEN holder_token != '' THEN 1 ELSE 0 END "
                "FROM soul_leases WHERE soul_alias=?",
                (soul,),
            ).fetchone()
        except sqlite3.Error:
            raise SoulLeaseError("soul lease storage unavailable.")
        if row is None:
            return None
        return {
            "soul_alias": row[0],
            "state": row[1],
            "holder_request_id": row[2],
            "holder_entry": row[3],
            "generation": row[4],
            "process_nonce": row[5],
            "pid": row[6],
            "process_create_time": row[7],
            "journal_epoch": row[8],
            "heartbeat": row[9],
            "updated_at": row[10],
            "has_token": bool(row[11]),
        }


# -- backward-compatible names (PROGRAM-2 tests reference these) --------------
#: Alias kept so ``from src.interfaces.soul_lease import LeaseResult``
#: keeps importing during the PROGRAM-3 migration; new code uses
#: :class:`AcquireResult` / :class:`Capability`.
LeaseResult = AcquireResult
