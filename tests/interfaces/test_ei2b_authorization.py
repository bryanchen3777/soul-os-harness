"""EI-2B-PROGRAM-3 authorization tests: triple check, stale generations,
background forgery, orphans, R9 permit CAS, flag-off startup silence.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.interfaces import coordinator as coord
from src.interfaces.coordinator import (
    coordinator_enabled,
    open_admission_context,
    prepare_publish,
)
from src.interfaces.soul_lease import Liveness

T0 = 1_700_000_000.0
SOUL = "agent_yua"
OTHER = "agent_miku"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _ctx(tmp_path, name="auth.db", epoch=1):
    conn = _open(tmp_path / name)
    return open_admission_context(conn, epoch=epoch)


# -- triple mismatch (soul + request + generation): zero write / zero send ---


def test_triple_unknown_request_is_not_current(tmp_path):
    ctx = _ctx(tmp_path)
    assert coord.require_current(ctx, "req-ghost") is False
    assert coord.require_current(ctx, "") is False
    assert coord.require_current(ctx, "   ") is False


def test_triple_alias_mismatch_is_not_current(tmp_path):
    ctx = _ctx(tmp_path, "triple.db")
    ident_a, out_a = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="a-work")
    assert out_a.should_publish is True
    assert coord.require_current(ctx, ident_a.request_id) is True
    # Forged rebinding of the same request id under another soul: the live
    # lease row for that soul names a different holder request, so the gate
    # stays closed (zero send for the mismatched triple).
    ctx.registry.track_request(ident_a.request_id, OTHER, 1)
    assert coord.require_current(ctx, ident_a.request_id) is False
    # The other soul holds nothing live either.
    assert coord.current_capability(ctx, OTHER) is None


def test_triple_cross_soul_ids_never_validate_each_other(tmp_path):
    ctx = _ctx(tmp_path, "xsoul.db")
    ident_a, out_a = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="a")
    ident_b, out_b = prepare_publish(ctx, soul_alias=OTHER, entry_kind="web", text="b")
    assert out_a.should_publish is True
    assert out_b.should_publish is True
    assert coord.require_current(ctx, ident_a.request_id) is True
    assert coord.require_current(ctx, ident_b.request_id) is True
    # Each id validates only against its own (alias, generation, request).
    ctx.registry.track_request(ident_a.request_id, OTHER, 1)
    assert coord.require_current(ctx, ident_a.request_id) is False
    assert coord.require_current(ctx, ident_b.request_id) is True


# -- stale generation: the old worker keeps an executing row but is blocked --


def test_stale_generation_old_capability_releases_nothing(tmp_path):
    ctx = _ctx(tmp_path, "stale.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="w1")
    assert out.should_publish is True
    old_cap = ctx.held[SOUL]
    old_generation = old_cap.generation
    # Recycle under the worker via the recover path (death + quiescence),
    # then re-admit as the new worker at the next generation.
    dead = Liveness("DEAD", "test fixture death")
    assert ctx.lease_store.recover(SOUL, dead, T0 + 5, side_effects_quiesced=True) == "FREE"
    ident2, out2 = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="w2")
    assert out2.should_publish is True
    assert ctx.held[SOUL].generation == old_generation + 1
    # Stale worker: journal row still executing, but the triple no longer
    # matches the live lease — blocked (zero write / zero send).
    assert ctx.journal.load_by_request(ident.request_id)["state"] == "executing"
    assert coord.require_current(ctx, ident.request_id) is False
    assert coord.require_current(ctx, ident2.request_id) is True
    # The stale capability object itself releases nothing: the conditional
    # UPDATE matches no row (token/request/generation all rotated).
    assert ctx.lease_store.release(old_cap, T0 + 6, quiesced=True) is False
    assert ctx.lease_store.load(SOUL)["holder_request_id"] == ident2.request_id
    # ... and the settle valve never releases on the stale capability's
    # behalf either (old row still executing -> held, never released).
    assert coord.settle_request(ctx, old_cap, T0 + 7) != coord.SETTLE_RELEASED
    assert ctx.held.get(SOUL) is not None


# -- background forgery: the roll is a fixed code-identity list --------------


def test_background_caller_roll_accepts_only_the_trusted_three():
    for trusted in ("scheduler", "diary", "dream"):
        assert coord.check_background_caller(trusted) == trusted
        assert coord.TRUSTED_BACKGROUND_CALLERS.count(trusted) == 1


def test_background_caller_rejects_payload_shaped_forgeries():
    # NOTE: "scheduler " (trailing space) is NOT listed: the checker
    # canonicalizes with strip() to the trusted name itself by design.
    for forged in (
        "", "  ", "web", "tg", "voice", "true", "1", "admin", "root",
        "non_request_write", "triggered_by", "background",
        " Scheduler", "DIARY", "Dream", "scheduler;drop", "diary\x00",
    ):
        with pytest.raises(ValueError):
            coord.check_background_caller(forged)


def test_background_caller_never_reads_payloads():
    # Authorization input is the caller-declared name only: a payload that
    # claims to be background authorizes nothing by itself.
    assert coord.is_non_request({"triggered_by": "user_message"}) is False
    with pytest.raises(ValueError):
        coord.check_background_caller("non_request_write")
    with pytest.raises(ValueError):
        coord.check_background_caller("true")


# -- orphan permits: evidence kept, release blocked, reconciliation filed ----


def test_orphan_permit_blocks_release_and_files_reconciliation(tmp_path):
    ctx = _ctx(tmp_path, "orphan.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="tg", text="send me")
    assert out.should_publish is True
    # Drive the journal row terminal through the backend evidence point.
    assert ctx.journal.record_generated(ident.request_id, 1, "r-ok", "body-ok") is True
    assert ctx.journal.record_outcome(
        ident.request_id, "backend", "ok", {"completed": True}, T0 + 1) is True
    # One outbound permit opens and is never closed: the send fate is
    # unknown, so the holder is NOT quiesced.
    permit = coord.open_permit(ctx, request_id=ident.request_id, destination="tg", now=T0 + 2)
    assert coord.count_open_permits(ctx, ident.request_id) == 1
    dead = Liveness("DEAD", "holder gone with an open permit")
    assert ctx.lease_store.recover(
        SOUL, dead, T0 + 3, side_effects_quiesced=False) == "QUARANTINED"
    # Orphan evidence is filed; the original permit row is never deleted.
    coord.record_reconciliation(
        ctx, request_id=ident.request_id, permit_id=permit,
        reason="holder-dead-permit-open", now=T0 + 4,
    )
    rows = ctx.journal._conn.execute(
        "SELECT request_id, permit_id, reason FROM reconciliation").fetchall()
    assert (ident.request_id, permit, "holder-dead-permit-open") in rows
    assert ctx.journal._conn.execute(
        "SELECT COUNT(*) FROM outbound_permits WHERE permit_id=? AND state='open'",
        (permit,)).fetchone()[0] == 1
    # Terminal row but open permit: the valve holds (never released).
    assert coord.settle_after_outcome(ctx, ident.request_id, T0 + 5) == coord.SETTLE_HELD_PERMITS_OPEN
    assert ctx.lease_store.load(SOUL)["state"] == "QUARANTINED"


# -- R9: per-key SQL CAS, no shared-dict / whole-JSON overwrite --------------


def test_permit_close_is_exactly_once_cas(tmp_path):
    ctx = _ctx(tmp_path, "r9.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="r9")
    assert out.should_publish is True
    first = coord.open_permit(ctx, request_id=ident.request_id, destination="web", now=T0)
    second = coord.open_permit(ctx, request_id=ident.request_id, destination="web", now=T0 + 1)
    assert first != second
    assert coord.count_open_permits(ctx, ident.request_id) == 2
    assert coord.close_permit(ctx, permit_id=first, now=T0 + 2) is True
    # Second close of the same key settles nothing (CAS rowcount 0).
    assert coord.close_permit(ctx, permit_id=first, now=T0 + 3) is False
    assert coord.count_open_permits(ctx, ident.request_id) == 1
    assert coord.close_permit(ctx, permit_id=second, now=T0 + 4) is True
    assert coord.count_open_permits(ctx, ident.request_id) == 0


def test_permit_rows_are_visible_across_connections_with_cas(tmp_path):
    name = "r9x.db"
    ctx = _ctx(tmp_path, name)
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="xconn")
    assert out.should_publish is True
    permit = coord.open_permit(ctx, request_id=ident.request_id, destination="tg", now=T0)
    peer = _open(tmp_path / name)
    try:
        seen = peer.execute(
            "SELECT COUNT(*) FROM outbound_permits WHERE permit_id=? AND state='open'",
            (permit,)).fetchone()[0]
        assert seen == 1
        # A competing closer wins exactly once through the same CAS
        # predicate; the loser (this context) then settles nothing.
        won = peer.execute(
            "UPDATE outbound_permits SET state='closed', settled_at=? "
            "WHERE permit_id=? AND state='open'",
            (T0 + 1, permit)).rowcount
        assert won == 1
        peer.commit()
    finally:
        peer.close()
    assert coord.close_permit(ctx, permit_id=permit, now=T0 + 2) is False
    assert coord.count_open_permits(ctx, ident.request_id) == 0


# -- flag off + startup recovery: zero trigger -------------------------------


def test_flag_defaults_off():
    assert coordinator_enabled({}) is False


def test_startup_recovery_leaves_settled_state_untouched(tmp_path):
    name = "startup.db"
    ctx = _ctx(tmp_path, name, epoch=1)
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="done")
    assert out.should_publish is True
    assert ctx.journal.record_generated(ident.request_id, 1, "r", "b") is True
    assert ctx.journal.record_outcome(
        ident.request_id, "backend", "ok", {"completed": True}, T0 + 1) is True
    assert coord.settle_after_outcome(ctx, ident.request_id, T0 + 2) == coord.SETTLE_RELEASED
    generation = ctx.lease_store.load(SOUL)["generation"]
    ctx.journal._conn.close()
    # Restart at a new epoch over the same file: settled rows and the FREE
    # lease row (with its generation) are untouched — zero trigger.
    ctx2 = open_admission_context(_open(tmp_path / name), epoch=2)
    try:
        assert ctx2.journal.load_by_request(ident.request_id)["state"] == "completed"
        row = ctx2.lease_store.load(SOUL)
        assert row["state"] == "FREE"
        assert int(row["generation"]) == int(generation)
        assert ctx2.lease_store.all_rows() == [SOUL]
    finally:
        ctx2.journal._conn.close()


def test_startup_recovery_on_empty_store_is_noop(tmp_path):
    ctx = _ctx(tmp_path, "empty.db", epoch=3)
    try:
        assert ctx.lease_store.all_rows() == []
        assert ctx.journal.load_by_request("req-absent") is None
        assert coord.count_open_permits(ctx, "req-absent") == 0
    finally:
        ctx.journal._conn.close()
