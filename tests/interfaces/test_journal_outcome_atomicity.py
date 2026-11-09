"""Journal outcome atomicity tests (EI-2A-JOURNAL-MEMORY-OUTCOME-ATOMICITY-FIX-1).

Proves concurrent record_outcome writers can neither silently downgrade a
durable completed with a stale non-completed status nor clobber a newer
durable outcome from a stale snapshot:

O1 completed vs stale skipped  -> completed survives, stale writer refused
O2 completed vs stale pending  -> ditto
O3 completed vs stale unknown  -> ditto
O4 reverse scheduling          -> non-completed durably first, completed
                                  after: completed wins (legal upgrade)
O5 concurrent completed pair   -> both succeed on byte-identical durable
                                  JSON; nothing is dropped, no new priority
O6 epoch ownership              -> wrong epoch is OwnershipError, durable
                                  untouched
O7 no fake success              -> sqlite failure / rollback never reports
                                  persistence; durable bytes unchanged
O8 existing semantics           -> validation, downgrade refusal, idempotent
                                  same-state update (frozen contract)

Disposable TEMP SQLite only. Threads + events for deterministic
interleaving; no processes, no listeners, no network, no sleeps.
"""

import json
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
    return ("p-owner", "a-main", "s-priv", "c-o%d" % i, "b-main", "k-o%d" % i)


def _raw(conn, rid):
    row = conn.execute(
        "SELECT outcomes FROM requests WHERE request_id=?",
        (rid,),
    ).fetchone()
    assert row is not None
    return row[0]


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


class _FailUpdateConn:
    """Pass-through wrapper whose outcomes UPDATE always raises, proving a
    sqlite failure surfaces as JournalError with durable state untouched."""

    def __init__(self, real):
        self._real = real

    def execute(self, sql, params=()):
        if sql.startswith("UPDATE requests SET outcomes="):
            raise sqlite3.OperationalError("injected outcome write failure")
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _try_msg(fn):
    try:
        fn()
        return ("OK", "")
    except JournalError as exc:
        return ("E", str(exc))


def _seeded(db_path):
    raw = _open(db_path)
    seed = Journal(raw, epoch=1)
    assert seed.claim(_ns(0), "fpO", "req-o", NOW).kind == "NEW"
    return raw


def test_o1_completed_survives_stale_skipped(tmp_path):
    db = tmp_path / "o1.db"
    raw = _seeded(db)
    entered = threading.Event()
    release = threading.Event()
    stale = Journal(_HookConn(raw, "UPDATE requests SET outcomes=", entered, release), epoch=1)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "s", _try_msg(lambda: stale.record_outcome("req-o", 1, "memory", "skipped"))))
    t.start()
    assert entered.wait(timeout=30)
    fresh = Journal(_open(db), epoch=1)
    fresh.record_outcome("req-o", 1, "memory", "completed", "ev-main")
    release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert out["s"] == ("E", "completed cannot downgrade")
    assert _raw(_open(db), "req-o") == '{"memory":"completed"}'
    raw.close()


def test_o2_completed_survives_stale_pending(tmp_path):
    db = tmp_path / "o2.db"
    raw = _seeded(db)
    # Move the durable outcome off pending first: a stale pending-writer
    # must hold a strictly stale snapshot (pending == durable would take
    # the idempotent no-write path before any UPDATE executes).
    Journal(_open(db), epoch=1).record_outcome("req-o", 1, "memory", "skipped")
    assert _raw(_open(db), "req-o") == '{"memory":"skipped"}'
    entered = threading.Event()
    release = threading.Event()
    stale = Journal(_HookConn(raw, "UPDATE requests SET outcomes=", entered, release), epoch=1)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "s", _try_msg(lambda: stale.record_outcome("req-o", 1, "memory", "pending"))))
    t.start()
    assert entered.wait(timeout=30)
    fresh = Journal(_open(db), epoch=1)
    fresh.record_outcome("req-o", 1, "memory", "completed", "ev-main")
    release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert out["s"] == ("E", "completed cannot downgrade")
    assert _raw(_open(db), "req-o") == '{"memory":"completed"}'
    raw.close()


def test_o3_completed_survives_stale_unknown(tmp_path):
    db = tmp_path / "o3.db"
    raw = _seeded(db)
    entered = threading.Event()
    release = threading.Event()
    stale = Journal(_HookConn(raw, "UPDATE requests SET outcomes=", entered, release), epoch=1)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "s", _try_msg(lambda: stale.record_outcome("req-o", 1, "memory", "unknown"))))
    t.start()
    assert entered.wait(timeout=30)
    fresh = Journal(_open(db), epoch=1)
    fresh.record_outcome("req-o", 1, "memory", "completed", "ev-main")
    release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert out["s"] == ("E", "completed cannot downgrade")
    assert _raw(_open(db), "req-o") == '{"memory":"completed"}'
    raw.close()


def test_o4_reverse_scheduling_noncompleted_first_then_completed(tmp_path):
    db = tmp_path / "o4.db"
    raw = _seeded(db)
    entered = threading.Event()
    release = threading.Event()
    late = Journal(_HookConn(raw, "UPDATE requests SET outcomes=", entered, release), epoch=1)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "b", _try_msg(lambda: late.record_outcome("req-o", 1, "memory", "completed", "ev-b"))))
    t.start()
    assert entered.wait(timeout=30)
    # A concurrent writer durably lands a non-completed outcome first; the
    # parked completed writer must still upgrade it (legal forward move).
    first = Journal(_open(db), epoch=1)
    first.record_outcome("req-o", 1, "memory", "skipped")
    assert _raw(_open(db), "req-o") == '{"memory":"skipped"}'
    release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    assert out["b"] == ("OK", "")
    assert _raw(_open(db), "req-o") == '{"memory":"completed"}'
    raw.close()


def test_o5_concurrent_completed_pair_both_succeed_identical_bytes(tmp_path):
    db = tmp_path / "o5.db"
    raw = _seeded(db)
    entered = threading.Event()
    release = threading.Event()
    writer_a = Journal(_HookConn(raw, "UPDATE requests SET outcomes=", entered, release), epoch=1)
    out = {}
    t = threading.Thread(
        target=lambda: out.setdefault(
            "a", _try_msg(lambda: writer_a.record_outcome("req-o", 1, "memory", "completed", "ev-A"))))
    t.start()
    assert entered.wait(timeout=30)
    writer_b = Journal(_open(db), epoch=1)
    writer_b.record_outcome("req-o", 1, "memory", "completed", "ev-B")
    release.set()
    t.join(timeout=30)
    assert not t.is_alive()
    # Same-state update contract: both writers carry valid evidence and
    # agree on byte-identical durable JSON, so both succeed and the
    # persisted outcome is exactly one completed — nothing dropped, no
    # priority invented.
    assert out["a"] == ("OK", "")
    assert _raw(_open(db), "req-o") == '{"memory":"completed"}'
    raw.close()


def test_o6_epoch_ownership(tmp_path):
    db = tmp_path / "o6.db"
    conn = _open(db)
    j = Journal(conn, epoch=1)
    assert j.claim(_ns(6), "fpO6", "req-o6", NOW).kind == "NEW"
    j.record_outcome("req-o6", 1, "memory", "completed", "ev-1")
    before = _raw(conn, "req-o6")
    with pytest.raises(OwnershipError):
        Journal(_open(db), epoch=9).record_outcome("req-o6", 9, "memory", "skipped")
    # Ownership fence fires before any state decision: durable bytes untouched.
    assert _raw(conn, "req-o6") == before == '{"memory":"completed"}'
    conn.close()


def test_o7_sqlite_failure_and_rollback_never_report_success(tmp_path):
    db = tmp_path / "o7.db"
    raw = _open(db)
    seed = Journal(raw, epoch=1)
    assert seed.claim(_ns(7), "fpO7", "req-o7", NOW).kind == "NEW"
    failing = Journal(_FailUpdateConn(raw), epoch=1)
    with pytest.raises(JournalError) as excinfo:
        failing.record_outcome("req-o7", 1, "memory", "skipped")
    assert str(excinfo.value) == "record_outcome failed"
    assert _raw(raw, "req-o7") == '{"memory": "pending"}'
    raw.close()
    # A closed connection is a hard failure, never a silent success.
    dead = _open(db)
    jd = Journal(dead, epoch=1)
    dead.close()
    with pytest.raises(JournalError):
        jd.record_outcome("req-o7", 1, "memory", "skipped")
    assert _raw(_open(db), "req-o7") == '{"memory": "pending"}'


def test_o8_existing_outcome_semantics_preserved(tmp_path):
    db = tmp_path / "o8.db"
    conn = _open(db)
    j = Journal(conn, epoch=1)
    assert j.claim(_ns(8), "fpO8", "req-o8", NOW).kind == "NEW"
    with pytest.raises(JournalError):
        j.record_outcome("req-o8", 1, "transport", "completed", "ev")
    with pytest.raises(JournalError):
        j.record_outcome("req-o8", 1, "memory", "bogus")
    with pytest.raises(JournalError):
        j.record_outcome("req-o8", 1, "memory", "completed", None)
    with pytest.raises(JournalError):
        j.record_outcome("req-o8", 1, "memory", "completed", "")
    with pytest.raises(JournalError):
        j.record_outcome("req-o8", 1, "memory", "completed", [])
    with pytest.raises(JournalError):
        j.record_outcome("req-ox-missing", 1, "memory", "pending")
    with pytest.raises(OwnershipError):
        Journal(_open(db), epoch=9).record_outcome("req-o8", 9, "memory", "pending")
    # Legal forward path unchanged: pending -> unknown -> completed.
    j.record_outcome("req-o8", 1, "memory", "unknown")
    assert json.loads(_raw(conn, "req-o8")) == {"memory": "unknown"}
    j.record_outcome("req-o8", 1, "memory", "completed", "ev-1")
    assert json.loads(_raw(conn, "req-o8")) == {"memory": "completed"}
    # Downgrades refused with the frozen error.
    for stale in ("pending", "skipped", "unknown"):
        with pytest.raises(JournalError) as excinfo:
            j.record_outcome("req-o8", 1, "memory", stale)
        assert str(excinfo.value) == "completed cannot downgrade"
    # Same-state completed update is idempotent success (existing contract).
    j.record_outcome("req-o8", 1, "memory", "completed", "ev-2")
    assert _raw(conn, "req-o8") == '{"memory":"completed"}'
    conn.close()
