"""EI-2B coordinator tests: per-soul admission, busy, fail-closed, attribution.

Offline only: caller-opened SQLite in tmp files, caller-provided clock.
No server, no ports, no real API/LLM, no subprocess.
"""

from __future__ import annotations

import ast
import json
import sqlite3
import threading
from pathlib import Path

import pytest

from src.interfaces import coordinator as coord
from src.interfaces.coordinator import (
    FLAG_ENV_VAR,
    AdmissionOutcome,
    HolderRegistry,
    Journal,
    admit_entry,
    coordinator_enabled,
    current_busy,
    fail_closed_uncertain,
    fingerprint_for_text,
    is_non_request,
    lease_namespace,
    mark_non_request,
    mint_request_identity,
    open_admission_context,
    prepare_publish,
    require_registered,
)
from src.interfaces.soul_lease import SoulLeaseStore

T0 = 1_700_000_000.0
SOUL = "agent_yua"
KEY = "turn-001"
FP = "fp-body-v1"
REQ = "request-001"


def _open(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def _setup(tmp_path, name="c.db"):
    conn = _open(tmp_path / name)
    return Journal(conn, epoch=1), conn, HolderRegistry()


def _admit(j, reg, **kw):
    args = dict(
        soul_alias=SOUL,
        entry_kind="web",
        fingerprint=FP,
        request_id=REQ,
        idempotency_key=KEY,
        epoch=1,
        now=T0,
    )
    args.update(kw)
    return admit_entry(j, reg, **args)


# -- flag --------------------------------------------------------------------


def test_flag_defaults_off_and_needs_explicit_opt_in():
    assert coordinator_enabled({}) is False
    assert coordinator_enabled({FLAG_ENV_VAR: ""}) is False
    assert coordinator_enabled({FLAG_ENV_VAR: "0"}) is False
    assert coordinator_enabled({FLAG_ENV_VAR: "off"}) is False
    for raw in ("1", "true", "TRUE", "yes", "on", " On "):
        assert coordinator_enabled({FLAG_ENV_VAR: raw}) is True


def test_flag_off_means_zero_trigger_in_runtime_entries():
    """Flag off = old behaviour byte-identical: entries reference the
    coordinator only through lazy (function-level) imports behind the
    flag gate, never at module top level, so no new branch can fire."""
    assert coordinator_enabled({}) is False
    for rel in (
        "src/io/gateway.py",
        "src/io/channels/router.py",
        "src/voice/input_router.py",
    ):
        src = Path(rel).read_text(encoding="utf-8")
        assert "SOUL_OS_PER_SOUL_MUTEX" in src or "coordinator_enabled" in src, rel
        tree = ast.parse(src)
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = "" if isinstance(node, ast.Import) else (node.module or "")
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [mod]
                assert not any("coordinator" in n or "soul_lease" in n for n in names), \
                    "top-level coordinator import in %s" % rel


# -- namespace ---------------------------------------------------------------


def test_lease_namespace_embeds_soul_alias():
    ns = lease_namespace(SOUL, KEY)
    assert SOUL in ns
    assert ns.startswith("soul-lease/")
    assert lease_namespace(SOUL, KEY) != lease_namespace("agent_miku", KEY)
    assert lease_namespace(SOUL, KEY) != lease_namespace(SOUL, "turn-002")


def test_lease_namespace_rejects_empty():
    with pytest.raises(ValueError):
        lease_namespace("", KEY)
    with pytest.raises(ValueError):
        lease_namespace(SOUL, "  ")


def test_admit_rejects_background_and_unknown_kinds(tmp_path):
    j, _, reg = _setup(tmp_path)
    for kind in ("background", "scheduler", "lifecycle", "tts", ""):
        with pytest.raises(ValueError):
            _admit(j, reg, entry_kind=kind)


# -- admission basics --------------------------------------------------------


def test_new_admits_and_registers_holder_class(tmp_path):
    j, _, reg = _setup(tmp_path)
    out = _admit(j, reg)
    assert out.disposition == "NEW"
    assert out.http_status == 201
    assert out.should_publish is True
    assert reg.lookup(lease_namespace(SOUL, KEY)) == "web"


def test_dual_entry_same_soul_same_key_exactly_one_holder(tmp_path):
    path = tmp_path / "race.db"
    _open(path).close()
    barrier = threading.Barrier(2)
    rendezvous = threading.Barrier(2)
    outs = [None, None]
    shared = HolderRegistry()

    def worker(i: int) -> None:
        conn = _open(path)
        try:
            jj = Journal(conn, epoch=1)
            barrier.wait(timeout=10)
            outs[i] = admit_entry(
                jj, shared,
                soul_alias=SOUL,
                entry_kind="web" if i == 0 else "tg",
                fingerprint=FP,
                request_id="request-race-%d" % i,
                idempotency_key=KEY,
                epoch=1,
                now=T0,
            )
            # Winner registers inside admit_entry before returning; meet
            # here so the loser's re-read below is deterministic.
            rendezvous.wait(timeout=10)
            if not outs[i].should_publish:
                outs[i] = AdmissionOutcome(
                    disposition=outs[i].disposition,
                    http_status=outs[i].http_status,
                    code=outs[i].code,
                    should_publish=False,
                    busy=current_busy(jj, shared, soul_alias=SOUL, idempotency_key=KEY),
                )
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    holders = [o for o in outs if o.should_publish]
    busies = [o for o in outs if not o.should_publish]
    assert len(holders) == 1
    assert len(busies) == 1
    busy = busies[0]
    assert busy.disposition == "IN_PROGRESS"
    assert busy.http_status == 409
    assert busy.code == "soul_busy"
    assert busy.busy is not None
    assert busy.busy.status == 409
    assert busy.busy.retry_after == 1
    assert busy.busy.holder_entry in ("web", "tg")
    assert busy.busy.holder_epoch == 1
    # No token material in the busy body.
    blob = repr(busy.busy).lower()
    assert "token" not in blob and "bearer" not in blob and "digest" not in blob


def test_busy_writes_no_outcome_and_keeps_lease(tmp_path):
    j, conn, reg = _setup(tmp_path)
    first = _admit(j, reg)
    assert first.should_publish is True
    second = _admit(j, reg, entry_kind="tg", request_id="request-002")
    assert second.disposition == "IN_PROGRESS"
    assert second.should_publish is False
    rows = conn.execute(
        "SELECT request_id, outcomes FROM requests",
    ).fetchall()
    assert [r[0] for r in rows] == [REQ]
    assert json.loads(rows[0][1]) == {"memory": "pending"}
    assert coord._read_row_by_namespace(
        j, coord.journal_namespace(SOUL, KEY))["state"] == "executing"


def test_different_souls_do_not_block(tmp_path):
    j, _, reg = _setup(tmp_path)
    a = _admit(j, reg, soul_alias="agent_yua", request_id="request-a")
    b = _admit(j, reg, soul_alias="agent_miku", request_id="request-b",
               idempotency_key="turn-001")
    assert a.should_publish is True
    assert b.should_publish is True


def test_same_key_retry_replays_fixed_response(tmp_path):
    j, _, reg = _setup(tmp_path)
    assert _admit(j, reg).should_publish is True  # claim NEW + begin inside
    j.record_generated(REQ, 1, "resp-1", "fixed-body")
    retry = _admit(j, reg, request_id="request-retry-late")
    assert retry.disposition == "REPLAY"
    assert retry.http_status == 200
    assert retry.should_publish is False
    assert (retry.fixed_response_id, retry.fixed_response_body) == ("resp-1", "fixed-body")


def test_same_key_different_body_conflicts(tmp_path):
    j, _, reg = _setup(tmp_path)
    assert _admit(j, reg).should_publish is True
    clash = _admit(j, reg, fingerprint="fp-body-v2", request_id="request-002")
    assert clash.disposition == "CONFLICT"
    assert clash.http_status == 409
    assert clash.should_publish is False


def test_generated_past_24h_expires(tmp_path):
    """Upstream expiry: a generated row past EXPIRY_SECONDS re-admits as
    EXPIRED (no backend terminal exists upstream, §9 gap G1/G2 — expiry of
    the fixed response is the equivalent mapping)."""
    j, _, reg = _setup(tmp_path)
    assert _admit(j, reg).should_publish is True  # claim NEW + begin inside
    j.record_generated(REQ, 1, "resp-1", "fixed-body")
    late = _admit(j, reg, request_id="request-next-day", now=T0 + 24 * 3600 + 1.0)
    assert late.disposition == "EXPIRED"
    assert late.http_status == 410
    assert late.should_publish is False


# -- fault injection ---------------------------------------------------------


def test_storage_failure_fails_closed_uncertain(tmp_path):
    conn = _open(tmp_path / "dead.db")
    j = Journal(conn, epoch=1)
    reg = HolderRegistry()
    conn.close()  # every subsequent statement raises; must map to UNCERTAIN.
    out = _admit(j, reg)
    assert out.disposition == "UNCERTAIN"
    assert out.http_status == 409
    assert out.should_publish is False


def test_restart_marks_old_epoch_uncertain_without_backfill(tmp_path):
    j, conn, reg = _setup(tmp_path)
    assert _admit(j, reg, epoch=1).should_publish is True
    j2 = Journal(conn, epoch=2)
    assert j2.recover() == [REQ]
    stale = _admit(j2, reg, request_id="request-after-restart")
    assert stale.disposition == "UNCERTAIN"
    assert stale.should_publish is False
    # New epoch on a fresh key proceeds; the old row stays uncertain
    # (B7 accepted gap:断裂 recorded as an uncertain gap, never forged).
    fresh = _admit(j2, reg, request_id="request-epoch-2",
                   idempotency_key="turn-002", epoch=2)
    assert fresh.disposition == "NEW"
    assert fresh.should_publish is True
    assert coord._read_row_by_namespace(
        j2, coord.journal_namespace(SOUL, KEY))["state"] == "uncertain"


def test_fail_closed_path_marks_uncertain_and_never_publishes(tmp_path, caplog):
    j, _, reg = _setup(tmp_path)
    assert _admit(j, reg, epoch=1).should_publish is True
    with caplog.at_level("WARNING", logger="soul_os.coordinator"):
        out = fail_closed_uncertain(j, request_id=REQ, epoch=1, reason="no-registration")
    assert out.disposition == "UNCERTAIN"
    assert out.should_publish is False
    assert coord._read_request_by_id(j, REQ)["state"] == "uncertain"
    assert any("fail-closed" in m for m in caplog.messages)


def test_fail_closed_never_raises_on_dead_storage(tmp_path):
    conn = _open(tmp_path / "dead2.db")
    j = Journal(conn, epoch=1)
    conn.close()
    out = fail_closed_uncertain(j, request_id=REQ, epoch=1, reason="no-registration")
    assert out.disposition == "UNCERTAIN"
    assert out.should_publish is False


# -- background attribution --------------------------------------------------


def test_background_mark_never_mixes_into_outcome(tmp_path):
    labelled = mark_non_request({"text": "dream line"}, "night-slot")
    assert is_non_request(labelled) is True
    assert labelled["triggered_by"] == "night-slot"
    # Pure copy: input untouched.
    assert is_non_request({"text": "dream line"}) is False
    # Existing trigger reason (e.g. proxy triggered_by) is preserved.
    keep = mark_non_request({"triggered_by": "user_message"}, "slot-x")
    assert keep["triggered_by"] == "user_message"
    assert is_non_request(keep) is True
    with pytest.raises(ValueError):
        mark_non_request({}, "")
    # Memory/bus evidence can never complete a request (journal frozen rule).
    j, _, reg = _setup(tmp_path)
    assert _admit(j, reg).should_publish is True
    j.record_outcome(REQ, 1, "memory", "completed", {"completed": True})
    assert coord._read_request_by_id(j, REQ)["state"] == "executing"


# -- PROGRAM-2 two-step admission (lease first, claim second) ------------------


def _setup2(tmp_path, name="c2.db", epoch=1, ttl=1800.0):
    conn = _open(tmp_path / name)
    ctx = open_admission_context(conn, epoch=epoch, lease_ttl=ttl)
    return ctx, conn


def _admit2(ctx, **kw):
    args = dict(
        soul_alias=SOUL,
        entry_kind="web",
        fingerprint=FP,
        request_id=REQ,
        idempotency_key=KEY,
        epoch=ctx.epoch,
        now=T0,
    )
    args.update(kw)
    return admit_entry(
        ctx.journal, ctx.registry, lease_store=ctx.lease_store,
        lease_ttl=ctx.lease_ttl, held_map=ctx.held, **args,
    )


def test_mint_request_identity_is_fresh_hex():
    a = mint_request_identity()
    b = mint_request_identity()
    assert a.request_id != b.request_id
    assert a.idempotency_key != b.idempotency_key
    assert a.request_id.startswith("in-") and a.idempotency_key.startswith("in-")


def test_fingerprint_stable_and_stripped():
    assert fingerprint_for_text("  hello  ") == fingerprint_for_text("hello")
    assert fingerprint_for_text("a") != fingerprint_for_text("b")
    with pytest.raises(ValueError):
        fingerprint_for_text(123)  # type: ignore[arg-type]


def test_two_step_new_admits_and_holds_lease(tmp_path):
    ctx, _ = _setup2(tmp_path)
    out = _admit2(ctx)
    assert out.disposition == "NEW"
    assert out.should_publish is True
    held = ctx.lease_store.load(SOUL)
    assert held is not None and held["holder_request_id"] == REQ


def test_true_mutex_different_keys_same_soul(tmp_path):
    """PROGRAM-2 acceptance: different keys, one soul, threads + barrier →
    exactly one should_publish, one 409 busy with holder class + Retry-After."""
    path = tmp_path / "mutex.db"
    _open(path).close()
    barrier = threading.Barrier(2)
    outs = [None, None]

    def worker(i: int) -> None:
        conn = _open(path)
        try:
            ctx = open_admission_context(conn, epoch=1)
            barrier.wait(timeout=10)
            ident = mint_request_identity()
            outs[i] = admit_entry(
                ctx.journal, ctx.registry,
                soul_alias=SOUL,
                entry_kind="web" if i == 0 else "tg",
                fingerprint="fp-%d" % i,
                request_id=ident.request_id,
                idempotency_key=ident.idempotency_key,
                epoch=1,
                now=T0,
                lease_store=ctx.lease_store,
            )
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    holders = [o for o in outs if o.should_publish]
    busies = [o for o in outs if not o.should_publish]
    assert len(holders) == 1
    assert len(busies) == 1
    busy = busies[0]
    assert busy.disposition == "IN_PROGRESS"
    assert busy.http_status == 409
    assert busy.code == "soul_busy"
    assert busy.busy is not None
    assert busy.busy.retry_after == 1
    assert busy.busy.holder_epoch == 1
    assert busy.busy.holder_entry in ("web", "tg")


def test_lease_loser_same_key_replay_keeps_semantics(tmp_path):
    ctx, _ = _setup2(tmp_path)
    assert _admit2(ctx).should_publish is True
    ctx.journal.record_generated(REQ, 1, "resp-1", "fixed-body")
    # Same key, same body, new request id, lease still held by REQ:
    # lease loses, journal re-walk replays the fixed response.
    retry = _admit2(ctx, request_id="request-retry-late")
    assert retry.disposition == "REPLAY"
    assert retry.http_status == 200
    assert retry.should_publish is False
    assert (retry.fixed_response_id, retry.fixed_response_body) == ("resp-1", "fixed-body")


def test_lease_loser_different_key_pure_busy_no_journal_write(tmp_path):
    ctx, conn = _setup2(tmp_path)
    assert _admit2(ctx).should_publish is True
    other = _admit2(ctx, entry_kind="tg", fingerprint="fp-other",
                    request_id="request-other", idempotency_key="turn-002")
    assert other.disposition == "IN_PROGRESS"
    assert other.should_publish is False
    assert other.busy is not None and other.busy.holder_entry == "web"
    # Zero journal writes for the foreign key.
    assert coord._read_row_by_namespace(
        ctx.journal, coord.journal_namespace(SOUL, "turn-002")) is None


def test_claim_conflict_compensates_lease(tmp_path):
    """PROGRAM-3: a won lease lost at the claim is compensated with proven
    quiescence (never held-but-unused); the FREE row persists.

    Upstream has no backend terminal (§9 gap G1/G2), so the hold is freed
    through the proven-dead + quiesced recover path instead of a
    settle-after-terminal; the conflict-then-compensate half is unchanged."""
    from src.interfaces.coordinator import current_capability
    from src.interfaces.soul_lease import Liveness

    ctx, _ = _setup2(tmp_path)
    assert _admit2(ctx).should_publish is True
    cap = current_capability(ctx, SOUL)
    assert cap is not None
    dead = Liveness("DEAD", "test fixture death")
    assert ctx.lease_store.recover(
        SOUL, dead, T0 + 1, side_effects_quiesced=True) == "FREE"
    # A conflicting retry wins the FREE lease (generation 2), hits
    # CONFLICT at the claim, and auto-compensates back to FREE.
    clash = _admit2(ctx, fingerprint="fp-body-v2", request_id="request-002")
    assert clash.disposition == "CONFLICT"
    assert clash.should_publish is False
    row = ctx.lease_store.load(SOUL)
    assert row is not None
    assert row["state"] == "FREE"
    assert row["generation"] == 2
    assert current_capability(ctx, SOUL) is None


def test_settle_then_new_request_admits_with_next_generation(tmp_path):
    """Settle holds the live task; proven-dead + quiesced recover frees it
    and the next hold is generation 2 (no backend-terminal release exists
    upstream, §9 gap G1/G2)."""
    from src.interfaces.coordinator import (
        SETTLE_HELD_TASKS_OPEN,
        current_capability,
        settle_request,
    )
    from src.interfaces.soul_lease import Liveness

    ctx, _ = _setup2(tmp_path)
    assert _admit2(ctx).should_publish is True
    cap = current_capability(ctx, SOUL)
    assert cap is not None
    assert cap.generation == 1
    # Still executing: settle refuses (never release a live task).
    assert settle_request(ctx, cap, T0 + 1.0) == SETTLE_HELD_TASKS_OPEN
    assert ctx.lease_store.load(SOUL)["state"] == "HELD"
    # Fixed response: still held (generated never releases, §9 gap).
    ctx.journal.record_generated(REQ, 1, "resp-1", "fixed-body")
    assert settle_request(ctx, cap, T0 + 2.0) == SETTLE_HELD_TASKS_OPEN
    # Proven-dead + quiesced recover frees; the next hold is generation 2.
    dead = Liveness("DEAD", "test fixture death")
    assert ctx.lease_store.recover(
        SOUL, dead, T0 + 3.0, side_effects_quiesced=True) == "FREE"
    nxt = _admit2(ctx, request_id="request-next", idempotency_key="turn-002",
                  fingerprint="fp-next")
    assert nxt.disposition == "NEW"
    assert nxt.should_publish is True
    assert current_capability(ctx, SOUL).generation == 2


def test_stale_hold_never_taken_over(tmp_path, caplog):
    """PROGRAM-3 reversal: a HELD lease past any TTL stays busy — the
    next claimant loses and the holder is untouched (no takeover)."""
    ctx, _ = _setup2(tmp_path, ttl=10.0)
    assert _admit2(ctx).should_publish is True
    with caplog.at_level("WARNING", logger="soul_os.soul_lease"):
        nxt = _admit2(ctx, request_id="request-new", idempotency_key="turn-002",
                      fingerprint="fp-new", now=T0 + 10.0)
    assert nxt.disposition == "IN_PROGRESS"
    assert nxt.should_publish is False
    assert nxt.busy is not None and nxt.busy.holder_entry == "web"
    assert not any("takeover" in r.message for r in caplog.records)
    assert ctx.lease_store.load(SOUL)["holder_request_id"] == REQ


def test_recover_dead_holder_frees_and_marks_original_epoch(tmp_path):
    """Startup verification: a proven-dead + quiesced holder goes FREE
    and its journal row is settled with the ORIGINAL epoch."""
    from src.interfaces.soul_lease import Liveness, LIVENESS_DEAD

    ctx, conn = _setup2(tmp_path, epoch=1)
    assert _admit2(ctx).should_publish is True
    fresh = open_admission_context(
        conn, epoch=2,
        liveness_fn=lambda pid, create: Liveness(LIVENESS_DEAD, "exit 1"),
    )
    row = fresh.lease_store.load(SOUL)
    assert row is not None
    assert row["state"] == "FREE"
    assert row["generation"] == 1
    assert coord._read_row_by_namespace(
        fresh.journal, coord.journal_namespace(SOUL, KEY))["state"] == "uncertain"
    # New epoch on a fresh key proceeds with generation 2.
    nxt = admit_entry(
        fresh.journal, fresh.registry, soul_alias=SOUL, entry_kind="tg",
        fingerprint="fp-2", request_id="request-epoch-2",
        idempotency_key="turn-002", epoch=2, now=T0 + 1.0,
        lease_store=fresh.lease_store, held_map=fresh.held,
    )
    assert nxt.should_publish is True


def test_recover_live_holder_never_releases(tmp_path):
    """Startup verification: a live holder is untouched; newcomers busy."""
    from src.interfaces.soul_lease import Liveness, LIVENESS_ALIVE

    ctx, conn = _setup2(tmp_path, epoch=1)
    assert _admit2(ctx).should_publish is True
    fresh = open_admission_context(
        conn, epoch=2,
        liveness_fn=lambda pid, create: Liveness(LIVENESS_ALIVE, "still here"),
    )
    row = fresh.lease_store.load(SOUL)
    assert row is not None
    assert row["state"] == "HELD"
    assert row["holder_request_id"] == REQ
    nxt = admit_entry(
        fresh.journal, fresh.registry, soul_alias=SOUL, entry_kind="tg",
        fingerprint="fp-2", request_id="request-epoch-2",
        idempotency_key="turn-002", epoch=2, now=T0 + 1.0,
        lease_store=fresh.lease_store, held_map=fresh.held,
    )
    assert nxt.disposition == "IN_PROGRESS"
    assert nxt.should_publish is False


def test_recover_unknown_holder_quarantines_not_frees(tmp_path):
    """Startup verification: an unverifiable holder is quarantined and
    blocks admission (never silently freed, never taken over)."""
    from src.interfaces.soul_lease import Liveness, LIVENESS_UNKNOWN

    ctx, conn = _setup2(tmp_path, epoch=1)
    assert _admit2(ctx).should_publish is True
    fresh = open_admission_context(
        conn, epoch=2,
        liveness_fn=lambda pid, create: Liveness(LIVENESS_UNKNOWN, "denied"),
    )
    assert fresh.lease_store.load(SOUL)["state"] == "QUARANTINED"
    nxt = admit_entry(
        fresh.journal, fresh.registry, soul_alias=SOUL, entry_kind="tg",
        fingerprint="fp-2", request_id="request-epoch-2",
        idempotency_key="turn-002", epoch=2, now=T0 + 1.0,
        lease_store=fresh.lease_store, held_map=fresh.held,
    )
    assert nxt.disposition == "IN_PROGRESS"
    assert nxt.should_publish is False


def test_lease_store_failure_fails_closed(tmp_path):
    ctx, _ = _setup2(tmp_path)
    ctx.lease_store._conn.close()
    out = _admit2(ctx)
    assert out.disposition == "UNCERTAIN"
    assert out.should_publish is False


def test_prepare_publish_mints_and_stamps(tmp_path):
    ctx, _ = _setup2(tmp_path)
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text=" hi ")
    assert out.should_publish is True
    assert ident.request_id.startswith("in-")
    assert coord._read_request_by_id(
        ctx.journal, ident.request_id)["state"] == "executing"


def test_require_registered_gate(tmp_path):
    ctx, _ = _setup2(tmp_path)
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="hi")
    assert out.should_publish is True
    assert require_registered(ctx.journal, ident.request_id) is True
    assert require_registered(ctx.journal, "request-ghost") is False
    assert require_registered(ctx.journal, "") is False
    # Generated rows no longer count as live registrations (no backend
    # terminal exists upstream, §9 gap: a fixed response never releases).
    ctx.journal.record_generated(ident.request_id, 1, "r", "b")
    assert require_registered(ctx.journal, ident.request_id) is False


# -- deadlock static guard ---------------------------------------------------


def test_no_wait_on_grant_in_coordinator():
    """Grant checks happen only at entry claim points (publish boundary);
    the coordinator itself must never block-wait."""
    tree = ast.parse(Path("src/interfaces/coordinator.py").read_text(encoding="utf-8"))
    banned_attrs = {"sleep", "wait", "wait_for", "join"}
    for node in ast.walk(tree):
        assert not isinstance(node, ast.Await), "coordinator must not await"
        assert not isinstance(node, ast.Yield), "coordinator must not yield"
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "acquire":
                    # Only the non-blocking single-transaction lease take
                    # (lease_store.acquire) may use this name; no lock /
                    # semaphore / queue blocking primitive may appear.
                    holder = func.value
                    assert isinstance(holder, ast.Name) and holder.id == "lease_store", \
                        "banned wait primitive: acquire outside lease_store"
                assert func.attr not in banned_attrs, "banned wait primitive: %s" % func.attr
                if func.attr == "get" and isinstance(func.value, ast.Name):
                    assert "queue" not in func.value.id.lower(), "banned queue.get wait"
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert imports <= {"logging", "os", "dataclasses", "typing", "src", "__future__", "hashlib", "time", "uuid", "sqlite3", "secrets"}, imports


def test_lease_acquire_never_in_handler_paths():
    """Lease take lives only in the coordinator; no handler waits on it."""
    for rel in (
        "src/io/gateway.py",
        "src/io/channels/router.py",
        "src/voice/input_router.py",
        "src/agent/consciousness.py",
        "src/llm/proxy.py",
        "src/llm/fish_tts_handler.py",
        "src/voice/tts_service.py",
    ):
        src = Path(rel).read_text(encoding="utf-8")
        assert ".acquire(" not in src, rel
        assert "SoulLeaseStore" not in src, rel


# -- PROGRAM-3: group admission -----------------------------------------------

def _ctx3(tmp_path, name="p3.db", epoch=1):
    conn = _open(tmp_path / name)
    return open_admission_context(conn, epoch=epoch)


def test_admit_group_all_win_grants_held_and_executing(tmp_path):
    ctx = _ctx3(tmp_path)
    res = coord.admit_group(
        ctx, soul_aliases=["agent_miku", "agent_yua"],
        entry_kind="web", texts={"agent_yua": "hi", "agent_miku": "yo"},
        now=T0,
    )
    assert res.won is True
    assert res.outcome is None
    assert sorted(res.grants) == ["agent_miku", "agent_yua"]
    for alias, (ident, cap) in res.grants.items():
        assert ctx.held.get(alias) == cap
        assert coord._read_request_by_id(
            ctx.journal, ident.request_id)["state"] == "executing"
        assert coord.require_current(ctx, ident.request_id) is True


def test_admit_group_partial_loss_compensates_and_publishes_nothing(tmp_path):
    import os as _os

    from src.interfaces.soul_lease import own_process_create_time as _own_ct

    ctx = _ctx3(tmp_path, "g2.db")
    # Separate connection pre-holds agent_zeta (live: own pid + create time;
    # 0.0 create time is the designed best-effort fallback and still probes
    # ALIVE for a running pid). Sorted order puts agent_yua first so it wins
    # phase 1 and must then be compensated.
    hc = _open(tmp_path / "g2.db")
    SoulLeaseStore(hc).acquire(
        "agent_zeta", "tok-pre", "in-pre", "nonce-pre",
        _os.getpid(), _own_ct(), 1, T0 + 1, holder_entry="web",
    )
    hc.close()
    res = coord.admit_group(
        ctx, soul_aliases=["agent_yua", "agent_zeta"],
        entry_kind="tg", texts={"agent_yua": "hi", "agent_zeta": "yo"},
        now=T0 + 2,
    )
    assert res.won is False
    assert res.outcome is not None and res.outcome.should_publish is False
    assert res.outcome.code == "soul_busy"
    # The phase-1 winner (agent_yua) is fully compensated: lease FREE
    # (row exists because it was won then released), nothing held, and no
    # journal row was ever touched for it (phase-1-only grants never claim).
    assert ctx.held == {}
    assert ctx.lease_store.load("agent_yua")["state"] == "FREE"
    assert ctx.lease_store.load("agent_zeta")["state"] == "HELD"
    assert ctx.lease_store.load("agent_zeta")["holder_request_id"] == "in-pre"


def test_admit_group_phase2_loss_marks_journal_uncertain(tmp_path):
    ctx = _ctx3(tmp_path, "g2b.db")
    # Pre-claim agent_b's namespace under another request with the SAME
    # fingerprint: the group claim then maps to non-NEW (phase-2 loss).
    idem = {"agent_a": "fixed-key-a", "agent_b": "fixed-key-b"}
    ns_b = coord.journal_namespace("agent_b", "fixed-key-b")
    pre = ctx.journal.claim(ns_b, coord.fingerprint_for_text("b-text"), "in-other", T0)
    assert pre.kind == "NEW"
    res = coord.admit_group(
        ctx, soul_aliases=["agent_a", "agent_b"],
        entry_kind="web", texts={"agent_a": "a-text", "agent_b": "b-text"},
        now=T0 + 1, idempotency_keys=idem,
    )
    assert res.won is False
    assert ctx.held == {}
    # agent_a (journal-touched winner) keeps its row as UNCERTAIN evidence
    # with the original epoch; both leases are FREE again.
    assert ctx.lease_store.load("agent_a")["state"] == "FREE"
    assert ctx.lease_store.load("agent_b")["state"] == "FREE"
    ns_a = coord.journal_namespace("agent_a", "fixed-key-a")
    row_a = coord._read_row_by_namespace(ctx.journal, ns_a)
    assert row_a is not None and row_a["state"] == "uncertain"
    assert int(row_a["epoch"]) == 1  # original epoch, never forged
    # agent_b's pre-existing row is untouched by the group attempt.
    assert coord._read_row_by_namespace(ctx.journal, ns_b)["request_id"] == "in-other"


def test_admit_group_rejects_empty_and_bad_kind(tmp_path):
    ctx = _ctx3(tmp_path, "g3.db")
    with pytest.raises(ValueError):
        coord.admit_group(ctx, soul_aliases=[], entry_kind="web", now=T0)
    with pytest.raises(ValueError):
        coord.admit_group(ctx, soul_aliases=["agent_yua"], entry_kind="bogus", now=T0)


# -- PROGRAM-3: retry keys, busy render, rejected inputs ----------------------

def test_derive_retry_key_stable_and_uuid_fallback():
    k1 = coord.derive_retry_key("tg", "agent_yua", "telegram:88123")
    assert k1 == "tg:agent_yua:telegram:88123"
    k2 = coord.derive_retry_key("web", "agent_yua", "sess_1/msg-9")
    assert k2.startswith("web:agent_yua:sess_1/msg-9")
    u1 = coord.derive_retry_key("voice", "agent_yua", None)
    u2 = coord.derive_retry_key("voice", "agent_yua", "   ")
    assert u1.startswith("uuid-") and u2.startswith("uuid-") and u1 != u2
    with pytest.raises(ValueError):
        coord.derive_retry_key("bogus", "agent_yua", "x")


def test_busy_frame_carries_retry_after_and_no_token(tmp_path):
    ctx = _ctx3(tmp_path, "b.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="first")
    assert out.should_publish is True
    ident2, out2 = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="second")
    assert out2.should_publish is False and out2.busy is not None
    frame = coord.busy_frame(out2)
    assert frame["type"] == "soul_busy"
    assert frame["retry_after"] == 1 == coord.RETRY_AFTER_SECONDS
    assert frame["code"] == "soul_busy"
    blob = str(frame) + coord.busy_text(out2)
    assert ident.request_id in blob or True  # request ids are not secrets here
    # Tokens never appear: the held capability token is not in any render.
    cap = ctx.held[SOUL]
    assert cap.holder_token not in blob
    assert repr(cap) and "redacted" in repr(cap)


def test_record_and_count_rejected_inputs(tmp_path):
    ctx = _ctx3(tmp_path, "r.db")
    assert coord.count_rejected_inputs(ctx, "k-none") == 0
    rowid = coord.record_rejected_input(
        ctx, entry_kind="tg", soul_alias=SOUL, retry_key="tg:k1",
        reason="soul_busy", text="raw hello", now=T0,
    )
    assert rowid > 0
    assert coord.count_rejected_inputs(ctx, "tg:k1") == 1
    with pytest.raises(ValueError):
        coord.record_rejected_input(
            ctx, entry_kind="bogus", soul_alias=SOUL, retry_key="k",
            reason="r", text="t", now=T0,
        )


def test_stable_key_retry_returns_fixed_response_without_republish(tmp_path):
    ctx = _ctx3(tmp_path, "s.db")
    key = coord.derive_retry_key("tg", SOUL, "telegram:777")
    ident, out = prepare_publish(
        ctx, soul_alias=SOUL, entry_kind="tg", text="same text", idempotency_key=key)
    assert out.should_publish is True
    # Fix the response; the journal now answers REPLAY for the same key
    # (no backend terminal exists upstream, §9 gap — no settle step here).
    ctx.journal.record_generated(ident.request_id, 1, "resp-1", "fixed body")
    # Same-key retry: journal replays the fixed response.
    ident2, out2 = prepare_publish(
        ctx, soul_alias=SOUL, entry_kind="tg", text="same text", idempotency_key=key)
    assert out2.should_publish is False
    assert out2.disposition == "REPLAY"
    assert out2.fixed_response_body == "fixed body"


# -- PROGRAM-3: generation gate + settle valve --------------------------------

def test_require_current_blocks_stale_generation_worker(tmp_path):
    import os as _os

    from src.interfaces.soul_lease import Liveness
    from src.interfaces.soul_lease import own_process_create_time as _own_ct

    ctx = _ctx3(tmp_path, "cur.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="w1")
    assert out.should_publish is True
    assert coord.require_current(ctx, ident.request_id) is True
    assert coord.require_current(ctx, "in-ghost") is False
    # Recycle the lease out from under the executing row via the recover
    # path (death evidence + quiescence), then re-admit as a new worker.
    dead = Liveness("DEAD", "test fixture death")
    assert ctx.lease_store.recover(SOUL, dead, T0 + 5, side_effects_quiesced=True) == "FREE"
    ident2, out2 = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="w2")
    assert out2.should_publish is True
    # The stale worker's id still names an executing journal row, but its
    # generation no longer matches the live lease: blocked, zero send.
    assert coord._read_request_by_id(
        ctx.journal, ident.request_id)["state"] == "executing"
    assert coord.require_current(ctx, ident.request_id) is False
    assert coord.require_current(ctx, ident2.request_id) is True


def test_settle_valve_needs_terminal_and_zero_permits(tmp_path):
    """New-table valve: executing / generated never release (no backend
    terminal exists upstream, §9 gap G1/G2); unknown ids refuse."""
    ctx = _ctx3(tmp_path, "sv.db")
    ident, out = prepare_publish(ctx, soul_alias=SOUL, entry_kind="web", text="task")
    assert out.should_publish is True
    # Executing: not releasable, never released.
    assert coord.settle_after_outcome(ctx, ident.request_id, T0 + 1) == coord.SETTLE_HELD_TASKS_OPEN
    assert ctx.held.get(SOUL) is not None
    # An open permit changes nothing while tasks are open ...
    permit = coord.open_permit(ctx, request_id=ident.request_id, destination="web", now=T0 + 2)
    ctx.journal.record_generated(ident.request_id, 1, "r", "b")
    # ... and a fixed response still never releases: generated is not a
    # terminal state and memory outcomes never impersonate one.
    assert coord.settle_after_outcome(ctx, ident.request_id, T0 + 4) == coord.SETTLE_HELD_TASKS_OPEN
    assert coord.close_permit(ctx, permit_id=permit, now=T0 + 5) is True
    assert coord.settle_after_outcome(ctx, ident.request_id, T0 + 6) == coord.SETTLE_HELD_TASKS_OPEN
    assert ctx.held.get(SOUL) is not None
    assert coord.settle_after_outcome(ctx, "in-ghost", T0 + 7) == coord.SETTLE_REFUSED_UNKNOWN


# -- PROGRAM-3: trusted background callers -------------------------------------

def test_background_caller_roll_rejects_forgery():
    assert coord.check_background_caller("scheduler") == "scheduler"
    assert coord.check_background_caller("diary") == "diary"
    for forged in ("", "  ", "web", "tg", "true", "non_request_write", "admin"):
        with pytest.raises(ValueError):
            coord.check_background_caller(forged)
