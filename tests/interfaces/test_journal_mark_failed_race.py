"""Journal mark_failed_before_execution race tests (EI-2A-JOURNAL-MARK-FAILED-RACE-FIX-1).

Proves the stale-marker lost update is closed: mark_failed_before_execution
now lands via a predicated compare-and-set
(UPDATE ... WHERE request_id=? AND state='accepted' AND epoch=?), so a marker
that read accepted but lost the race to begin() can no longer flip a live
executing row back to failed_before_execution.

  J2-A  stale marker refused: marker parked after its SELECT / before its
        UPDATE loses to begin(); marker raises JournalError (bad state, NOT
        ownership), the durable row stays executing, and a same-key re-claim
        returns IN_PROGRESS (never NEW).
  J2-B  reverse order: marker-first vs begin-first each yield exactly one
        winner; the loser raises and the durable terminal state matches.
  J2-C  only a genuinely landed failed_before_execution row may re-NEW via
        same-key/same-fingerprint claim (which resets to accepted); rows that
        never landed failed re-claim IN_PROGRESS.
  J2-D  wrong-epoch marker raises OwnershipError and leaves the row
        byte-identical (begin with the right epoch still works after).
  J2-E  an SQLite write failure inside the CAS UPDATE never reports success:
        exact JournalError, durable row still accepted.
  J2-F  300 begin-vs-marker races: never both succeed, no stale overwrite,
        terminal state always matches the winner, wins sum to 300.

Disposable TEMP SQLite only. Threads + events/barriers for deterministic
interleaving where determinism matters (J2-A gate); no processes, no
listeners, no network. Every verdict assertion re-reads the final durable
SQLite row directly -- never trusts a return value alone.

The J2-A gate MUST park after the marker's SELECT has completed and before
its UPDATE executes. Parking before the SELECT would let the marker observe
the opponent's committed state and fail at validation -- a false negative
that passes even with the old unpredicated UPDATE.
"""

import sqlite3
import threading

import pytest

from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError
from src.interfaces.journal import OwnershipError

NOW = 1700000000.0

_MARK_UPDATE_PREFIX = "UPDATE requests SET state='failed_before_execution'"


def _open(path):
    return sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)


def _ns(i):
    return ("p-owner", "a-main", "s-priv", "c-j2-%d" % i, "b-main", "k-j2-%d" % i)


def _row(conn, rid):
    return conn.execute(
        "SELECT state, epoch FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()


class _HookConn:
    """Pass-through connection wrapper that parks a thread just before a
    chosen UPDATE statement is executed, for deterministic interleaving.

    The park point is AFTER the caller's SELECT (already returned) and
    BEFORE the guarded UPDATE is issued to SQLite.
    """

    def __init__(self, real, sql_prefix, entered, release):
        self._real = real
        self._prefix = sql_prefix
        self._entered = entered
        self._release = release

    def execute(self, sql, params=()):
        if sql.startswith(self._prefix):
            self._entered.set()
            assert self._release.wait(timeout=30)
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FailUpdateConn:
    """Pass-through wrapper that makes the guarded UPDATE raise an SQLite
    write failure. Journal.__init__ (CREATE + commit) passes through."""

    def __init__(self, real, sql_prefix, exc):
        self._real = real
        self._prefix = sql_prefix
        self._exc = exc

    def execute(self, sql, params=()):
        if sql.startswith(self._prefix):
            raise self._exc
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _run(fn):
    try:
        fn()
        return ("OK", None)
    except JournalError as exc:
        return ("E", exc)


def test_j2a_stale_marker_refused_row_stays_executing(tmp_path):
    db = tmp_path / "j2a.db"
    raw_m = _open(db)
    seed = Journal(raw_m, epoch=1)
    ns = _ns(101)
    assert seed.claim(ns, "fpJ2A", "req-j2a", NOW).kind == "NEW"
    entered = threading.Event()
    release = threading.Event()
    jm = Journal(_HookConn(raw_m, _MARK_UPDATE_PREFIX, entered, release), epoch=1)
    cb = _open(db)
    jb = Journal(cb, epoch=1)
    outcome = {}

    def marker():
        outcome["m"] = _run(lambda: jm.mark_failed_before_execution("req-j2a", 1))

    t = threading.Thread(target=marker)
    t.start()
    # Marker has finished its SELECT (saw accepted) and is parked just
    # before its UPDATE. The opponent now wins accepted -> executing.
    assert entered.wait(timeout=30)
    jb.begin("req-j2a", 1)
    release.set()
    t.join(timeout=30)
    status, exc = outcome["m"]
    assert status == "E"
    assert type(exc) is JournalError
    assert str(exc) == "bad state for failed_before_execution"
    # Direct durable read: the live execution survived the stale marker.
    assert _row(cb, "req-j2a") == ("executing", 1)
    # Same key + fingerprint re-claim sees a live execution, never a NEW.
    rr = Journal(_open(db), epoch=1).claim(ns, "fpJ2A", "req-j2a", NOW)
    assert rr.kind == "IN_PROGRESS"
    raw_m.close()
    cb.close()


def test_j2b_reverse_order_exactly_one_winner(tmp_path):
    db = tmp_path / "j2b.db"
    # Direction 1: marker lands first; begin() must lose as ownership.
    c1 = _open(db)
    j1 = Journal(c1, epoch=1)
    assert j1.claim(_ns(111), "fpJ2B1", "req-j2b-1", NOW).kind == "NEW"
    j1.mark_failed_before_execution("req-j2b-1", 1)
    with pytest.raises(OwnershipError, match="^begin ownership failed$"):
        j1.begin("req-j2b-1", 1)
    assert _row(c1, "req-j2b-1") == ("failed_before_execution", 1)
    c1.close()
    # Direction 2: begin() lands first; marker must lose as bad state.
    c2 = _open(db)
    j2 = Journal(c2, epoch=1)
    assert j2.claim(_ns(112), "fpJ2B2", "req-j2b-2", NOW).kind == "NEW"
    j2.begin("req-j2b-2", 1)
    with pytest.raises(JournalError, match="^bad state for failed_before_execution$"):
        j2.mark_failed_before_execution("req-j2b-2", 1)
    assert _row(c2, "req-j2b-2") == ("executing", 1)
    c2.close()


def test_j2c_only_landed_failed_can_reclaim_new(tmp_path):
    db = tmp_path / "j2c.db"
    conn = _open(db)
    ns = _ns(121)
    j1 = Journal(conn, epoch=1)
    assert j1.claim(ns, "fpJ2C", "req-j2c", NOW).kind == "NEW"
    j1.mark_failed_before_execution("req-j2c", 1)
    assert _row(conn, "req-j2c") == ("failed_before_execution", 1)
    # Genuinely landed failed: same key + fingerprint re-claim resets to
    # accepted under the new epoch and reports NEW.
    j2 = Journal(_open(db), epoch=2)
    assert j2.claim(ns, "fpJ2C", "req-j2c", NOW).kind == "NEW"
    assert _row(conn, "req-j2c") == ("accepted", 2)
    # The reset row carries the new epoch fence: old owner refused, new
    # owner proceeds.
    with pytest.raises(OwnershipError):
        Journal(_open(db), epoch=1).begin("req-j2c", 1)
    Journal(_open(db), epoch=2).begin("req-j2c", 2)
    assert _row(conn, "req-j2c") == ("executing", 2)
    # Negative: a row that never landed failed re-claims IN_PROGRESS, not NEW.
    j3 = Journal(conn, epoch=1)
    assert j3.claim(_ns(122), "fpJ2Cn", "req-j2c-neg", NOW).kind == "NEW"
    assert j3.claim(_ns(122), "fpJ2Cn", "req-j2c-neg", NOW).kind == "IN_PROGRESS"
    assert _row(conn, "req-j2c-neg") == ("accepted", 1)
    conn.close()


def test_j2d_wrong_epoch_ownership_error_row_unchanged(tmp_path):
    db = tmp_path / "j2d.db"
    conn = _open(db)
    j = Journal(conn, epoch=1)
    assert j.claim(_ns(131), "fpJ2D", "req-j2d", NOW).kind == "NEW"
    with pytest.raises(OwnershipError, match="^failed_before_execution ownership failed$"):
        Journal(_open(db), epoch=9).mark_failed_before_execution("req-j2d", 9)
    # Direct durable read: the row is byte-identical, fencing intact.
    assert _row(conn, "req-j2d") == ("accepted", 1)
    # The rightful owner proceeds as if nothing happened.
    j.begin("req-j2d", 1)
    assert _row(conn, "req-j2d") == ("executing", 1)
    # Wrong epoch against an executing row is ownership too, row untouched.
    with pytest.raises(OwnershipError, match="^failed_before_execution ownership failed$"):
        Journal(_open(db), epoch=9).mark_failed_before_execution("req-j2d", 9)
    assert _row(conn, "req-j2d") == ("executing", 1)
    conn.close()


def test_j2e_sqlite_write_failure_never_reports_success(tmp_path):
    db = tmp_path / "j2e.db"
    raw = _open(db)
    seed = Journal(raw, epoch=1)
    assert seed.claim(_ns(141), "fpJ2E", "req-j2e", NOW).kind == "NEW"
    broken = Journal(
        _FailUpdateConn(raw, _MARK_UPDATE_PREFIX, sqlite3.OperationalError("disk I/O error")),
        epoch=1,
    )
    with pytest.raises(JournalError, match="^mark_failed_before_execution failed$"):
        broken.mark_failed_before_execution("req-j2e", 1)
    # Direct durable read on an independent connection: still accepted,
    # no phantom failed_before_execution, no fake success anywhere.
    assert _row(_open(db), "req-j2e") == ("accepted", 1)
    # Unknown request under a broken connection is still JournalError,
    # never success.
    with pytest.raises(JournalError):
        broken.mark_failed_before_execution("req-no-such", 1)
    raw.close()


def test_j2f_begin_vs_marker_300_races(tmp_path):
    db = tmp_path / "j2f.db"
    wins_begin = 0
    wins_marker = 0
    for i in range(300):
        rid = "req-j2f-%d" % i
        ns = ("p-owner", "a-main", "s-priv", "c-j2f", "b-main", "k-j2f-%d" % i)
        cw = _open(db)
        cm = _open(db)
        jb = Journal(cw, epoch=1)
        jm = Journal(cm, epoch=1)
        assert jb.claim(ns, "fpJF", rid, NOW).kind == "NEW"
        barrier = threading.Barrier(2)
        res = {}

        def do_begin():
            barrier.wait(timeout=30)
            res["b"] = _run(lambda: jb.begin(rid, 1))

        def do_mark():
            barrier.wait(timeout=30)
            res["m"] = _run(lambda: jm.mark_failed_before_execution(rid, 1))

        tb = threading.Thread(target=do_begin)
        tm = threading.Thread(target=do_mark)
        tb.start()
        tm.start()
        tb.join(timeout=60)
        tm.join(timeout=60)
        co = _open(db)
        fin = _row(co, rid)
        co.close()
        # Exactly one side wins; the terminal state always matches the
        # winner; a stale overwrite (loser clobbering) is impossible.
        assert (res["b"][0], res["m"][0], fin[0]) in (
            ("OK", "E", "executing"),
            ("E", "OK", "failed_before_execution"),
        ), (i, res, fin)
        assert fin[1] == 1
        if res["b"][0] == "OK":
            wins_begin += 1
            assert type(res["m"][1]) is JournalError
            assert str(res["m"][1]) == "bad state for failed_before_execution"
        else:
            wins_marker += 1
            assert type(res["b"][1]) is OwnershipError
        cw.close()
        cm.close()
    # Generation accounting: one winner per row, no row with two winners,
    # no row with zero winners.
    assert wins_begin + wins_marker == 300
    assert wins_begin > 0 and wins_marker > 0
