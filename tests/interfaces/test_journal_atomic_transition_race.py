"""Journal atomic-transition race tests (EI-2A-JOURNAL-ATOMIC-TRANSITION-RACE-FIX-1).

Proves stale writers can no longer overwrite converged Journal verdicts:
A2 (recovered uncertain must survive a late record_generated) and
B2 (converged generated must survive a late mark_uncertain).

Disposable TEMP SQLite only. Threads + events for deterministic
interleaving; no processes, no listeners, no network.
"""

import sqlite3
import threading

import pytest

from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError
from src.interfaces.journal import OwnershipError

NOW = 1700000000.0


def _open(path):
    return sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)


def _ns(i):
    return ("p-owner", "a-main", "s-priv", "c-%d" % i, "b-main", "k-%d" % i)


def _state(conn, rid):
    return conn.execute(
        "SELECT state, epoch, response_id, response_body FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()


class _HookConn:
    """Pass-through connection wrapper that parks a thread just before a
    chosen UPDATE statement is executed, for deterministic interleaving."""

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


def _try(fn):
    try:
        fn()
        return "OK"
    except JournalError:
        return "E"


def test_a2_stale_generated_cannot_overwrite_recovered_uncertain(tmp_path):
    db = tmp_path / "a2.db"
    raw_w = _open(db)
    jw_seed = Journal(raw_w, epoch=1)
    assert jw_seed.claim(_ns(11), "fpA2", "req-a2", NOW).kind == "NEW"
    jw_seed.begin("req-a2", 1)
    entered = threading.Event()
    release = threading.Event()
    jw = Journal(_HookConn(raw_w, "UPDATE requests SET response_id", entered, release), epoch=1)
    dbp = _open(db)
    jp = Journal(dbp, epoch=2)
    outcome = {}

    def worker():
        outcome["w"] = _try(lambda: jw.record_generated("req-a2", 1, "resp-stale", "STALE"))

    t = threading.Thread(target=worker)
    t.start()
    assert entered.wait(timeout=30)
    assert jp.recover() == ["req-a2"]
    release.set()
    t.join(timeout=30)
    assert outcome["w"] == "E"
    assert _state(dbp, "req-a2") == ("uncertain", 1, None, None)
    assert jp.claim(_ns(11), "fpA2", "req-a2", NOW).kind == "UNCERTAIN"
    raw_w.close()
    dbp.close()


def test_b2_late_uncertain_cannot_orphan_converged_generated(tmp_path):
    db = tmp_path / "b2.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(12), "fpB2", "req-b2", NOW).kind == "NEW"
    jw.begin("req-b2", 1)
    raw_p = _open(db)
    entered = threading.Event()
    release = threading.Event()
    jp = Journal(_HookConn(raw_p, "UPDATE requests SET state='uncertain'", entered, release), epoch=1)
    outcome = {}

    def parent():
        outcome["p"] = _try(lambda: jp.mark_uncertain("req-b2"))

    t = threading.Thread(target=parent)
    t.start()
    assert entered.wait(timeout=30)
    jw.record_generated("req-b2", 1, "resp-good", "GOOD")
    release.set()
    t.join(timeout=30)
    assert outcome["p"] == "E"
    assert _state(conn, "req-b2") == ("generated", 1, "resp-good", "GOOD")
    rr = Journal(_open(db), epoch=1).claim(_ns(12), "fpB2", "req-b2", NOW)
    assert rr.kind == "REPLAY" and (rr.response_id, rr.response_body) == ("resp-good", "GOOD")
    conn.close()
    raw_p.close()


def test_race_invariant_exactly_one_winner(tmp_path):
    db = tmp_path / "n.db"
    for i in range(300):
        rid = "req-n-%d" % i
        nsx = ("p", "a", "s", "c-n", "b", "k-%d" % i)
        cw = _open(db)
        cp = _open(db)
        jw = Journal(cw, epoch=1)
        jp = Journal(cp, epoch=1)
        jw.claim(nsx, "fpN", rid, NOW)
        jw.begin(rid, 1)
        res = {}
        tw = threading.Thread(target=lambda: res.setdefault(
            "w", _try(lambda: jw.record_generated(rid, 1, "resp", "B"))))
        tp = threading.Thread(target=lambda: res.setdefault("p", _try(lambda: jp.mark_uncertain(rid))))
        tw.start()
        tp.start()
        tw.join(timeout=30)
        tp.join(timeout=30)
        fin = _state(cp, rid)
        # Exactly one side wins; (OK,OK,*) clobber outcomes are impossible.
        assert (res["w"], res["p"], fin[0]) in (
            ("OK", "E", "generated"),
            ("E", "OK", "uncertain"),
        ), (res, fin)
        cw.close()
        cp.close()


def test_a1_sequential_stale_write_still_refused(tmp_path):
    db = tmp_path / "a1.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(21), "fpA1", "req-a1", NOW).kind == "NEW"
    jw.begin("req-a1", 1)
    dbp = _open(db)
    jp = Journal(dbp, epoch=2)
    assert jp.recover() == ["req-a1"]
    with pytest.raises(JournalError):
        jw.record_generated("req-a1", 1, "resp-stale", "STALE")
    assert _state(dbp, "req-a1") == ("uncertain", 1, None, None)
    conn.close()
    dbp.close()


def test_b1_sequential_late_uncertain_still_refused(tmp_path):
    db = tmp_path / "b1.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(22), "fpB1", "req-b1", NOW).kind == "NEW"
    jw.begin("req-b1", 1)
    jw.record_generated("req-b1", 1, "resp-good", "GOOD")
    dbp = _open(db)
    with pytest.raises(JournalError):
        Journal(dbp, epoch=1).mark_uncertain("req-b1")
    assert _state(conn, "req-b1") == ("generated", 1, "resp-good", "GOOD")
    conn.close()
    dbp.close()


def test_idempotent_generated_replay_same_content(tmp_path):
    db = tmp_path / "idem.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(31), "fpI", "req-i", NOW).kind == "NEW"
    jw.begin("req-i", 1)
    jw.record_generated("req-i", 1, "resp-1", "BODY-1")
    jw.record_generated("req-i", 1, "resp-1", "BODY-1")
    rr = jw.claim(_ns(31), "fpI", "req-i", NOW)
    assert rr.kind == "REPLAY" and (rr.response_id, rr.response_body) == ("resp-1", "BODY-1")
    conn.close()


def test_generated_mismatch_never_overwrites(tmp_path):
    db = tmp_path / "mm.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(32), "fpM", "req-m", NOW).kind == "NEW"
    jw.begin("req-m", 1)
    jw.record_generated("req-m", 1, "resp-1", "BODY-1")
    with pytest.raises(JournalError):
        jw.record_generated("req-m", 1, "resp-2", "BODY-2")
    assert _state(conn, "req-m") == ("generated", 1, "resp-1", "BODY-1")
    conn.close()


def test_stale_epoch_write_stays_ownership_error(tmp_path):
    db = tmp_path / "ep.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(33), "fpE", "req-e", NOW).kind == "NEW"
    jw.begin("req-e", 1)
    dbp = _open(db)
    jp = Journal(dbp, epoch=2)
    assert jp.claim(_ns(33), "fpE", "req-e", NOW).kind == "UNCERTAIN"
    with pytest.raises(OwnershipError):
        # Epoch fence preserved: wrong-epoch write is ownership, not state, error.
        Journal(_open(db), epoch=9).record_generated("req-e", 9, "resp-x", "X")
    with pytest.raises(JournalError):
        jw.record_generated("req-e", 1, "resp-stale", "STALE")
    assert _state(dbp, "req-e") == ("uncertain", 1, None, None)
    conn.close()
    dbp.close()


def test_concurrent_same_key_exactly_one_new(tmp_path):
    db = tmp_path / "cc.db"
    ns = _ns(41)
    n = 8
    barrier = threading.Barrier(n)
    kinds = [None] * n

    def worker(i):
        conn = _open(db)
        j = Journal(conn, epoch=7)
        barrier.wait(timeout=30)
        kinds[i] = j.claim(ns, "fp1", "req-1", 1000.0).kind
        conn.close()

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    [t.start() for t in ts]
    [t.join(timeout=60) for t in ts]
    assert sorted(kinds) == ["IN_PROGRESS"] * 7 + ["NEW"]


def test_autocommit_no_snapshot_isolation(tmp_path):
    db = tmp_path / "vis.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    jw.claim(_ns(51), "fpV", "req-v", NOW)
    jw.begin("req-v", 1)
    before = _state(conn, "req-v")[0]
    dbp = _open(db)
    Journal(dbp, epoch=2).mark_uncertain("req-v")
    after = _state(conn, "req-v")[0]
    assert (before, after) == ("executing", "uncertain")
    conn.close()
    dbp.close()


def test_busy_timeout_blocks_then_recovers_with_integrity(tmp_path):
    db = tmp_path / "busy.db"
    conn = _open(db)
    jw = Journal(conn, epoch=1)
    assert jw.claim(_ns(61), "fpT", "req-t", NOW).kind == "NEW"
    jw.begin("req-t", 1)
    c1 = _open(db)
    c2 = _open(db)
    hold = threading.Event()
    release = threading.Event()

    def holder():
        c1.execute("BEGIN IMMEDIATE")
        hold.set()
        assert release.wait(timeout=30)
        c1.execute("ROLLBACK")

    t = threading.Thread(target=holder)
    t.start()
    assert hold.wait(timeout=30)
    import time

    def releaser():
        time.sleep(1.0)
        release.set()

    rt = threading.Thread(target=releaser)
    rt.start()
    t0 = time.monotonic()
    c2.execute("BEGIN IMMEDIATE")
    blocked = time.monotonic() - t0
    c2.execute("ROLLBACK")
    rt.join(timeout=30)
    t.join(timeout=30)
    # Row survived the lock contention untouched; the waiter blocked (~1s)
    # instead of failing instantly with BUSY.
    assert _state(conn, "req-t") == ("executing", 1, None, None)
    assert blocked >= 0.9
    c1.close()
    c2.close()
    conn.close()
