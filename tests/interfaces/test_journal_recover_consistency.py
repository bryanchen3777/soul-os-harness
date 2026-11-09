"""Journal recover() returned-IDs vs durable-rows consistency (J3-A~F).

Covers EI-2A-JOURNAL-J3-RECOVERY-RESULT-CONSISTENCY-FIX-1: recover() runs
SELECT-eligible + predicated-UPDATE inside one BEGIN IMMEDIATE transaction,
so the returned ID list and the rows actually turned uncertain can never
diverge under concurrency. Eligibility policy is unchanged
(state='executing' AND epoch<self.epoch).

J3-A Basic: old-epoch executing -> uncertain, exact ID returned.
J3-B Generated before recovery: a worker that converged to generated is
    neither reported nor downgraded (durable generated + response intact).
J3-C Recovery vs generation race: deterministic both-winners coverage.
    C1 parks recover between its SELECT and UPDATE (hook); the concurrent
    worker is fenced by the RESERVED lock (busy -> JournalError), recovery
    wins, returned == durable uncertain. C2 lets the worker converge first;
    recover then reports nothing and downgrades nothing. ("Worker completes
    inside the window" is structurally impossible once recover holds
    BEGIN IMMEDIATE -- that impossibility IS the fix; the old code allowed
    it and produced phantom IDs.)
J3-D Multiple: mixed states/epochs, only eligible IDs returned, rest kept.
J3-E Concurrent recovery: two connections race; exactly one winner per
    request (union == eligible set, disjoint, no error, no double report).
J3-F Failure/rollback: injected UPDATE / COMMIT / BEGIN failures raise
    (COMMIT failure is marked ambiguous), never false success, never
    phantom IDs; durable/exception/return stay consistent.

Disposable TEMP SQLite only. Threads + events for deterministic
interleaving; no processes, no listeners, no network, no sleeps, no
production paths/ports/services.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from src.interfaces.journal import Journal
from src.interfaces.journal import JournalError

NOW = 1700000000.0

#: Prefix matching ONLY recover()'s sweep UPDATE (claim()'s per-namespace
#: uncertain UPDATE carries ", updated_ts=?" right after 'uncertain').
_RECOVER_UPDATE_PREFIX = "UPDATE requests SET state='uncertain' WHERE"


def _open(path, timeout=30):
    return sqlite3.connect(
        str(path), timeout=timeout, isolation_level=None, check_same_thread=False
    )


def _ns(i):
    return ("p-owner", "a-main", "s-priv", "c-j%d" % i, "b-main", "k-j%d" % i)


def _seed_executing(db_path, i, epoch, rid=None, fp=None):
    """Claim + begin one row into executing; returns (rid, fp)."""
    rid = rid if rid is not None else "req-j3-%d" % i
    fp = fp if fp is not None else "fpJ3-%d" % i
    conn = _open(db_path)
    try:
        j = Journal(conn, epoch=epoch)
        assert j.claim(_ns(i), fp, rid, NOW).kind == "NEW"
        j.begin(rid, epoch)
    finally:
        conn.close()
    return rid, fp


def _state(db_path, rid):
    conn = _open(db_path)
    try:
        row = conn.execute(
            "SELECT state, epoch, response_id, response_body FROM requests"
            " WHERE request_id=?",
            (rid,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "row vanished: %r" % rid
    return row


def _recover_ids(db_path, epoch):
    conn = _open(db_path)
    try:
        return Journal(conn, epoch=epoch).recover()
    finally:
        conn.close()


class _HookConn:
    """Parks a thread just before recover()'s sweep UPDATE executes."""

    def __init__(self, real, entered, release):
        self._real = real
        self._entered = entered
        self._release = release

    def execute(self, sql, params=()):
        if sql.startswith(_RECOVER_UPDATE_PREFIX):
            self._entered.set()
            assert self._release.wait(timeout=30)
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FailUpdateConn:
    """Raises on recover()'s sweep UPDATE; everything else passes through."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=()):
        if sql.startswith(_RECOVER_UPDATE_PREFIX):
            raise sqlite3.OperationalError("injected recover UPDATE failure")
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FailCommitConn:
    """Raises on commit() once armed; init commit passes through unarmed."""

    def __init__(self, real):
        self._real = real
        self.armed = False

    def commit(self):
        if self.armed:
            raise sqlite3.OperationalError("injected recover COMMIT failure")
        return self._real.commit()

    def execute(self, sql, params=()):
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_j3_a_basic_old_executing_recovers_exact_id(tmp_path):
    db = tmp_path / "j3a.db"
    rid, _fp = _seed_executing(db, 0, epoch=1)
    got = _recover_ids(db, epoch=2)
    assert got == [rid]
    assert _state(db, rid)[:2] == ("uncertain", 1)
    # Idempotent: a second sweep finds nothing eligible.
    assert _recover_ids(db, epoch=2) == []


def test_j3_b_generated_before_recovery_not_reported_not_downgraded(tmp_path):
    db = tmp_path / "j3b.db"
    rid, _fp = _seed_executing(db, 1, epoch=1)
    worker = _open(db)
    try:
        Journal(worker, epoch=1).record_generated(rid, 1, "resp-b", "body-b")
    finally:
        worker.close()
    assert _recover_ids(db, epoch=2) == []
    state, epoch, resp_id, resp_body = _state(db, rid)
    assert (state, epoch, resp_id, resp_body) == ("generated", 1, "resp-b", "body-b")


def test_j3_c1_recovery_wins_fenced_worker(tmp_path):
    db = tmp_path / "j3c1.db"
    rid, _fp = _seed_executing(db, 2, epoch=1)
    entered = threading.Event()
    release = threading.Event()
    raw = _open(db)
    fenced = Journal(_HookConn(raw, entered, release), epoch=2)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", ("OK", fenced.recover())))
    t.start()
    try:
        assert entered.wait(timeout=30)
        # Recover holds BEGIN IMMEDIATE's RESERVED lock and is parked just
        # before its UPDATE: a concurrent worker cannot land generated in
        # the window -- its write hits SQLITE_BUSY, surfaced as JournalError.
        # (Short busy timeout: the point is "cannot succeed", not "waits".)
        short = _open(db, timeout=0.1)
        try:
            with pytest.raises(JournalError):
                Journal(short, epoch=1).record_generated(rid, 1, "resp-c", "body-c")
        finally:
            short.close()
        # Durable row is still executing: the fenced worker changed nothing.
        assert _state(db, rid)[0] == "executing"
    finally:
        release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert out["r"] == ("OK", [rid])
    state, _epoch, resp_id, resp_body = _state(db, rid)
    assert state == "uncertain"
    assert (resp_id, resp_body) == (None, None)
    # Late worker loses its CAS against the converged verdict, no clobber.
    late = _open(db)
    try:
        with pytest.raises(JournalError):
            Journal(late, epoch=1).record_generated(rid, 1, "resp-c", "body-c")
    finally:
        late.close()
    assert _state(db, rid)[0] == "uncertain"
    raw.close()


def test_j3_c2_generation_wins_recovery_reports_nothing(tmp_path):
    db = tmp_path / "j3c2.db"
    rid, _fp = _seed_executing(db, 3, epoch=1)
    worker = _open(db)
    try:
        Journal(worker, epoch=1).record_generated(rid, 1, "resp-c2", "body-c2")
    finally:
        worker.close()
    # Worker converged fully before recover's BEGIN: the SELECT snapshot no
    # longer sees it, so recover returns [] and the predicated UPDATE
    # touches nothing (no downgrade).
    assert _recover_ids(db, epoch=2) == []
    assert _state(db, rid) == ("generated", 1, "resp-c2", "body-c2")


def test_j3_d_multiple_mixed_states_and_epochs(tmp_path):
    db = tmp_path / "j3d.db"
    # accepted only (never executing): not eligible.
    conn = _open(db)
    try:
        j1 = Journal(conn, epoch=1)
        assert j1.claim(_ns(10), "fpD-acc", "req-d-acc", NOW).kind == "NEW"
    finally:
        conn.close()
    # Two eligible old-epoch executing rows + one even older epoch.
    _seed_executing(db, 11, epoch=1, rid="req-d-exe1")
    _seed_executing(db, 12, epoch=1, rid="req-d-exe2")
    _seed_executing(db, 13, epoch=0, rid="req-d-old")
    # generated / transport_sent / uncertain old rows: not eligible.
    _seed_executing(db, 14, epoch=1, rid="req-d-gen")
    _seed_executing(db, 15, epoch=1, rid="req-d-ts")
    _seed_executing(db, 16, epoch=1, rid="req-d-unc")
    w = _open(db)
    try:
        jw = Journal(w, epoch=1)
        jw.record_generated("req-d-gen", 1, "resp-d", "body-d")
        jw.record_generated("req-d-ts", 1, "resp-t", "body-t")
        jw.record_transport("req-d-ts", 1)
        jw.mark_uncertain("req-d-unc")
    finally:
        w.close()
    # Current-epoch executing: not eligible for a same-epoch sweep.
    _seed_executing(db, 17, epoch=2, rid="req-d-exe-cur")
    got = _recover_ids(db, epoch=2)
    assert sorted(got) == ["req-d-exe1", "req-d-exe2", "req-d-old"]
    assert _state(db, "req-d-exe1")[0] == "uncertain"
    assert _state(db, "req-d-exe2")[0] == "uncertain"
    assert _state(db, "req-d-old")[:2] == ("uncertain", 0)
    assert _state(db, "req-d-acc")[0] == "accepted"
    assert _state(db, "req-d-exe-cur")[:2] == ("executing", 2)
    assert _state(db, "req-d-gen") == ("generated", 1, "resp-d", "body-d")
    assert _state(db, "req-d-ts")[0] == "transport_sent"
    assert _state(db, "req-d-unc")[0] == "uncertain"
    # Second sweep is an idempotent no-op.
    assert _recover_ids(db, epoch=2) == []


def test_j3_e_concurrent_recovery_exactly_one_winner(tmp_path):
    db = tmp_path / "j3e.db"
    _seed_executing(db, 20, epoch=1, rid="req-e-1")
    _seed_executing(db, 21, epoch=1, rid="req-e-2")
    barrier = threading.Barrier(2, timeout=30)
    out = {}

    def _run(slot):
        try:
            conn = _open(db)
            try:
                barrier.wait(timeout=30)
                out[slot] = ("OK", Journal(conn, epoch=2).recover())
            finally:
                conn.close()
        except Exception as exc:  # pragma: no cover -- must not happen
            out[slot] = ("ERR", "%s: %s" % (type(exc).__name__, exc))

    ta = threading.Thread(target=_run, args=("a",))
    tb = threading.Thread(target=_run, args=("b",))
    ta.start()
    tb.start()
    ta.join(timeout=60)
    tb.join(timeout=60)
    assert not ta.is_alive() and not tb.is_alive()
    assert out["a"][0] == "OK" and out["b"][0] == "OK", out
    ids_a, ids_b = sorted(out["a"][1]), sorted(out["b"][1])
    # Union is the full eligible set, disjoint: no double report, no loss.
    assert sorted(ids_a + ids_b) == ["req-e-1", "req-e-2"]
    assert len(set(ids_a) & set(ids_b)) == 0
    # Winner takes all (atomic sweep under one lock); loser sees empty.
    assert min(len(ids_a), len(ids_b)) == 0
    assert _state(db, "req-e-1")[0] == "uncertain"
    assert _state(db, "req-e-2")[0] == "uncertain"


def test_j3_f1_update_failure_no_false_success_row_kept(tmp_path):
    db = tmp_path / "j3f1.db"
    rid, _fp = _seed_executing(db, 30, epoch=1)
    raw = _open(db)
    try:
        with pytest.raises(JournalError) as excinfo:
            Journal(_FailUpdateConn(raw), epoch=2).recover()
        assert str(excinfo.value) == "recover failed"
    finally:
        raw.close()
    # Rollback held: durable row untouched, and a later good sweep recovers it.
    assert _state(db, rid)[:2] == ("executing", 1)
    assert _recover_ids(db, epoch=2) == [rid]
    assert _state(db, rid)[0] == "uncertain"


def test_j3_f2_commit_failure_ambiguous_never_phantom(tmp_path):
    db = tmp_path / "j3f2.db"
    rid, _fp = _seed_executing(db, 31, epoch=1)
    raw = _open(db)
    try:
        failing = Journal(_FailCommitConn(raw), epoch=2)
        failing.conn.armed = True
        with pytest.raises(JournalError) as excinfo:
            failing.recover()
        assert str(excinfo.value) == "recover ambiguous: commit outcome unknown"
    finally:
        raw.close()
    # Nothing was returned (exception path); durable is whichever way the
    # commit actually went -- and the follow-up sweep agrees with it.
    state = _state(db, rid)[0]
    assert state in ("executing", "uncertain")
    follow = _recover_ids(db, epoch=2)
    assert follow == ([] if state == "uncertain" else [rid])
    assert _state(db, rid)[0] == "uncertain"


def test_j3_f3_begin_failure_no_false_success(tmp_path):
    db = tmp_path / "j3f3.db"
    rid, _fp = _seed_executing(db, 32, epoch=1)
    conn = _open(db)
    j = Journal(conn, epoch=2)
    conn.close()
    with pytest.raises(JournalError) as excinfo:
        j.recover()
    assert str(excinfo.value) == "recover failed"
    assert _state(db, rid)[:2] == ("executing", 1)
    assert _recover_ids(db, epoch=2) == [rid]
