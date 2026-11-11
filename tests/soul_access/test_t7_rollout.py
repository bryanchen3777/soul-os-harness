"""SA-6 T7 rollout tests: staged batch release drill on the frozen release gate.

Decision-complete scope (SA-6): only this file (plus docs/SA-6-RUNBOOK.md and
verification/soul-access/sa6/) is new. No product file is touched by this
ticket; no T6 file is touched; the CLI台帳 and guards are untouched.

Rollout model under test (sandbox only, in-process, no server, no ports):

  A: rem x {primary, minimax-m3.1-flash, glm-5.3-flash, mimo-v2.6-flash} (4);
  B: {mai, ruka, akane, mahiru} x same four backends (new 16, union 20).

  The sandbox switches the rollout client's ``allowed_routes`` with the
  existing ``RegistryProvider.update()``: A admits 4 / B admits 20. Any other
  reachable SA credential must not cross batches. ``release`` stays the global
  gate (not a per-route switch); editing the registry file alone changes
  nothing until it is installed via ``update()`` (hot-read applies to the
  release file, not to the registry snapshot).

Release contract (frozen, same as T6 pins):

  open file ({"status": "open"}) -> new inbound admitted;
  closed / missing / unreadable / bad JSON / non-dict / wrong status ->
  LiveRefused(403, "forbidden") with zero generate calls;
  close -> reopen never resurrects an UNKNOWN isolation row (same key 409,
  fresh key flows, no restart).

Batch evidence per route carries batch / route / agent / request_id / status /
generate-delta. A-new (4) and B-new (16) are mutually exclusive, union 20.
Batch membership is read off the installed registry snapshot's
allowed_routes -- never off the tools/no-tools path (a no-tools admit and a
tools-loop run on the same A route both succeed; a B route in phase A is
refused before any path is chosen).

All test names carry the t7 marker: `pytest -q tests/soul_access -k t7`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created). bus / generate / tools are fakes;
the release file is always a self-created tmp file (never data/**).
Zero subprocess call sites in this file (R1/R5 rationale: the spawn probe
lives in the T0 shared oracle).

Evidence emission: pure by default (no writes outside tmp). When the caller
sets SA6_EMIT_EVIDENCE_JSONL to a caller-created file, each batch/handler
probe appends one JSON record (batch, route, agent, request_id, status,
generate_delta, phase, layer). The SA-6 worker sets it to
verification/soul-access/sa6/evidence.jsonl for the auditable drill run.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest

_REPOSHA = "8bc9600"
_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.interfaces.context_policy import admit as ei_admit  # noqa: E402
from src.interfaces.credentials import digest_of as cred_digest_of  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.interfaces.schema import InterfaceError as EIError  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import loop as sa_loop  # noqa: E402
from src.soul_access import pool as sa_pool  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import snapshot as sa_snapshot  # noqa: E402

OWNER = "t7-rollout-owner"
AGENT_REM = "agent_rem"
SCOPE = "owner_private"

BACKENDS = ("primary", "minimax-m3.1-flash", "glm-5.3-flash", "mimo-v2.6-flash")
A_SOULS = ("rem",)
B_NEW_SOULS = ("mai", "ruka", "akane", "mahiru")
ALL_SOULS = A_SOULS + B_NEW_SOULS

A_ROUTES = tuple("rem/" + b for b in BACKENDS)
B_NEW_ROUTES = tuple(s + "/" + b for s in B_NEW_SOULS for b in BACKENDS)
ALL_ROLLOUT_ROUTES = A_ROUTES + B_NEW_ROUTES
assert len(A_ROUTES) == 4
assert len(B_NEW_ROUTES) == 16
assert len(ALL_ROLLOUT_ROUTES) == 20
assert set(A_ROUTES).isdisjoint(set(B_NEW_ROUTES))

_ROLLOUT_SET_DIGEST = (
    "3c1c427bc3cd35eedad8c92fdcb1b44a7a168d46a12d2cb98f119fafc5868870"
)

ROLLOUT_TOKEN = "T7-ROLLOUT-SANDBOX-KEY"
OTHER_TOKEN = "T7-OTHER-SANDBOX-KEY"

_EMIT_PATH = os.environ.get("SA6_EMIT_EVIDENCE_JSONL")

_NO_TOOLS_PATH = ("head_load", "gen1", "admit")
_TOOLS_PATH = ("head_load", "gen1", "tool_loop", "tail_rejoin", "gen2", "admit")


def _record(record: dict) -> None:
    if not _EMIT_PATH:
        return
    with open(_EMIT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")


def _route_entry(soul: str, backend: str) -> dict:
    return {
        "soul_alias": soul,
        "backend_alias": backend,
        "agent_id": "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, backend),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _registry_raw(rollout_allowed, other_allowed=("rem/primary",)) -> dict:
    routes = [_route_entry(s, b) for s in ALL_SOULS for b in BACKENDS]
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": routes,
        "clients": [
            {
                "client_id": "client-t7-rollout",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(rollout_allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": cred_digest_of(ROLLOUT_TOKEN),
            },
            {
                "client_id": "client-t7-other",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(other_allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": cred_digest_of(OTHER_TOKEN),
            },
        ],
    }


def _raw_request(soul: str, backend: str) -> dict:
    return {
        "model": "do-not-deploy-%s-%s" % (soul, backend),
        "messages": [{"role": "user", "content": "t7 rollout probe"}],
    }


def _registry_probe(provider, token: str, soul: str, backend: str, request_id: str):
    """Pure registry verdict: admitted | refused-<status>/<code> | unknown-404."""
    try:
        ei_admit(
            provider.current(),
            token,
            soul,
            backend,
            _raw_request(soul, backend),
            {},
            request_id,
        )
    except EIError as exc:
        if exc.status == 404:
            return ("unknown-404", exc.code)
        return ("refused-%d" % exc.status, exc.code)
    return ("admitted", "admit")


def _identity(**over):
    kw = {
        "request_id": "req-t7-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t7-0001",
        "idempotency_key": "key-t7-0001",
    }
    kw.update(over)
    return sa_schema.RequestIdentity(**kw)


def _dedup(**over):
    kw = {
        "principal_id": OWNER,
        "agent_id": AGENT_REM,
        "scope": SCOPE,
        "conversation_id": "dm",
        "branch_id": "root",
        "idempotency_key": "key-t7-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _live(tag="t7"):
    return sa_snapshot.LiveState(
        personality_version="personality-%s-v1" % tag,
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-%s-v1" % tag,
        projection_state="projection-%s-v1" % tag,
        recall_version="recall-%s-v1" % tag,
        recall_items=("recall-%s-item-1" % tag,),
        history_pool_version="pool-%s-v1" % tag,
        clean_history=("hist-%s-item-1" % tag,),
    )


def _head(identity=None, dedup=None, **over):
    return sa_snapshot.take_head_snapshot(
        identity=identity if identity is not None else _identity(),
        dedup=dedup if dedup is not None else _dedup(),
        backend_alias="primary",
        model="do-not-deploy-model-t7",
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        live=_live("head"),
        **over,
    )


def _envelope_for(request_id: str, key: str, tag="t7"):
    head = _head(
        identity=_identity(request_id=request_id, idempotency_key=key),
        dedup=_dedup(idempotency_key=key),
    )
    return sa_api.build_envelope(
        snapshot=head,
        messages=(("user", "t7 rollout %s" % tag),),
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _live_loop():
    return sa_bridge.LiveLoop(bus=object(), llm=object(), middleware=object())


def _write_release(path: Path, payload) -> Path:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _refuse_code(exc) -> tuple:
    return (exc.status, exc.code)


class _CountingGenerate:
    def __init__(self, text="t7-live-reply"):
        self.text = text
        self.calls = []

    def __call__(self, snapshot, holder, live):
        self.calls.append((snapshot, holder, live))
        return self.text


# -- shared acceptance oracle (one oracle, three clauses) ----------------------
#
# Clause R (release): closed gate refuses 403/forbidden with zero generate;
#   open gate admits with generate delta 1 (same handler object, hot-read).
# Clause B (batch phase A): rollout credential admits the 4 A routes and is
#   refused 403 on the 16 B routes; the other credential cannot cross batches;
#   unknown routes stay 404.


def _oracle_clause_release_closed_refuses(handler, generate, envelope) -> None:
    before = len(generate.calls)
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(envelope)
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert len(generate.calls) - before == 0


def _oracle_clause_release_open_admits(handler, generate, envelope) -> None:
    before = len(generate.calls)
    assert handler(envelope) == generate.text
    assert len(generate.calls) - before == 1


def _oracle_clause_phase_a_matrix(provider) -> list:
    """Phase-A batch matrix. Returns the evidence records (also emitted)."""
    records = []
    for soul in ALL_SOULS:
        for backend in BACKENDS:
            route = soul + "/" + backend
            rid = "req-t7-a-%s-%s" % (soul, backend.replace(".", "_"))
            status, code = _registry_probe(provider, ROLLOUT_TOKEN, soul, backend, rid)
            batch = "A" if route in set(A_ROUTES) else "B"
            if batch == "A":
                assert status == "admitted", (route, status, code)
            else:
                assert (status, code) == ("refused-403", "forbidden"), (route, status, code)
            records.append(
                {
                    "batch": batch,
                    "route": route,
                    "agent": "agent_" + soul,
                    "request_id": rid,
                    "status": status,
                    "code": code,
                    "generate_delta": 0,
                    "phase": "A",
                    "layer": "registry",
                }
            )
    # Other reachable SA credential must not cross batches (B stays 403).
    for route in B_NEW_ROUTES:
        soul, backend = route.split("/")
        rid = "req-t7-a-other-%s" % soul
        status, code = _registry_probe(provider, OTHER_TOKEN, soul, backend, rid)
        assert (status, code) == ("refused-403", "forbidden"), (route, status, code)
    # Unknown routes are 404, never silently admitted.
    status, code = _registry_probe(provider, ROLLOUT_TOKEN, "yua", "primary", "req-t7-a-unknown")
    assert (status, code) == ("unknown-404", "route_not_found")
    for rec in records:
        _record(rec)
    return records


def _acceptance_oracle(release_path, provider, handler, generate, envelope) -> list:
    """The single SA-6 acceptance oracle: R-closed, R-open, B-phase-A."""
    _write_release(release_path, {"status": "closed"})
    _oracle_clause_release_closed_refuses(handler, generate, envelope)
    _write_release(release_path, {"status": "open"})
    _oracle_clause_release_open_admits(handler, generate, envelope)
    return _oracle_clause_phase_a_matrix(provider)


# -- T7-1: same handler open -> close -> open; fault matrix same verdicts -----


def test_t7_release_same_handler_hot_toggle_open_close_open(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    envelope = _envelope_for("req-t7-toggle-1", "key-t7-toggle-1")
    _oracle_clause_release_open_admits(handler, generate, envelope)
    _write_release(release_path, {"status": "closed"})
    _oracle_clause_release_closed_refuses(handler, generate, envelope)
    _write_release(release_path, {"status": "open"})
    _oracle_clause_release_open_admits(handler, generate, envelope)
    assert len(generate.calls) == 2  # refusals added zero.


@pytest.mark.parametrize(
    "payload",
    [
        "not-json{{{",
        "[1, 2, 3]",
        '{"status": "OPEN"}',
        '{"status": ""}',
        '{"other": 1}',
    ],
)
def test_t7_release_fault_content_refuses_403_zero_generate(tmp_path, payload):
    release_path = _write_release(tmp_path / "release.json", payload)
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    assert sa_bridge.is_live_inbound_open.__module__ == "src.soul_access.release"
    _oracle_clause_release_closed_refuses(
        handler, generate, _envelope_for("req-t7-fault-1", "key-t7-fault-1")
    )


def test_t7_release_missing_file_refuses_403_zero_generate(tmp_path):
    release_path = tmp_path / "release-missing.json"
    assert not release_path.exists()
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    _oracle_clause_release_closed_refuses(
        handler, generate, _envelope_for("req-t7-fault-2", "key-t7-fault-2")
    )


# -- T7-2: real PoolStore/loop UNKNOWN isolation survives gate flips ----------


def test_t7_unknown_isolation_survives_close_reopen_no_restart(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    pool = sa_pool.PoolStore()
    head = _head()
    gen_calls = []

    def _generate(snapshot, holder, phase_name):
        gen_calls.append(phase_name)
        return "t7-%s" % phase_name

    cancelled = sa_loop.ControlledFuture()
    assert cancelled.cancel() is True

    def _runner(handle_id, harness_context, gen1_text):
        return cancelled

    with pytest.raises(sa_loop.LoopRejected) as exc_info:
        sa_loop.run_tool_request(
            snapshot=head,
            provenance=sa_schema.Provenance(
                created_utc="2026-11-11T17:00:00-05:00",
                snapshot_digest=sa_schema.snapshot_digest(head),
                transitions=_TOOLS_PATH,
            ),
            holder=sa_schema.new_holder(
                "sa7-live:req-t7-0001", (sa_schema.snapshot_digest(head),)
            ),
            pool=pool,
            fingerprint="t7-fingerprint-quarantine",
            harness_context=("t7-harness-ctx",),
            handle_ids=("h1",),
            tool_runner=_runner,
            generate=_generate,
            make_tail_draft=lambda evidence: sa_loop.build_tail_draft("t7-draft", ()),
            tail_live=_live("tail"),
            created_utc="2026-11-11T17:00:00-05:00",
        )
    assert exc_info.value.status == 409
    assert [phase for phase in gen_calls] == ["gen1"]  # no gen2 leaked.
    # Gate flips must not resurrect the row, and need no restart (same pool).
    _write_release(release_path, {"status": "closed"})
    assert sa_bridge.is_live_inbound_open.__module__ == "src.soul_access.release"
    closed_claim = pool.claim(_dedup(), "t7-fingerprint-quarantine")
    assert closed_claim.disposition == sa_pool.DISPOSITION_ISOLATED
    assert closed_claim.http_status == 409
    _write_release(release_path, {"status": "open"})
    reopened_claim = pool.claim(_dedup(), "t7-fingerprint-quarantine")
    assert reopened_claim.disposition == sa_pool.DISPOSITION_ISOLATED
    assert reopened_claim.http_status == 409
    # A fresh key flows again once the gate reopens (no blanket block).
    fresh = pool.claim(_dedup(idempotency_key="key-t7-fresh"), "t7-fp-fresh")
    assert fresh.disposition == sa_pool.DISPOSITION_NEW


# -- T7-3: phase A -- 4 admitted with release round-trips, 16 refused ---------


def test_t7_batch_a_four_admitted_sixteen_refused(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    provider = RegistryProvider(_registry_raw(A_ROUTES))
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    admitted = []
    for route in A_ROUTES:
        soul, backend = route.split("/")
        rid = "req-t7-a-open-%s" % soul
        status, code = _registry_probe(provider, ROLLOUT_TOKEN, soul, backend, rid)
        assert (status, code) == ("admitted", "admit"), (route, status, code)
        before = len(generate.calls)
        text = handler(_envelope_for(rid, "key-" + rid))
        assert text == generate.text
        delta = len(generate.calls) - before
        assert delta == 1
        admitted.append(route)
        _record(
            {
                "batch": "A",
                "route": route,
                "agent": "agent_" + soul,
                "request_id": rid,
                "status": "admitted",
                "code": "admit",
                "generate_delta": delta,
                "phase": "A-open",
                "layer": "registry+release",
            }
        )
    assert tuple(admitted) == A_ROUTES
    assert len(admitted) == 4
    matrix = _oracle_clause_phase_a_matrix(provider)
    assert [r["route"] for r in matrix if r["batch"] == "A"] == list(A_ROUTES)
    assert len([r for r in matrix if r["batch"] == "B"]) == 16


# -- T7-4: phase B -- update adds 16, union 20, sets disjoint -----------------


def test_t7_batch_b_adds_sixteen_union_twenty(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    provider = RegistryProvider(_registry_raw(A_ROUTES))
    # Drill order: closed -> install A -> open -> A evidence -> close ->
    # install B -> open -> new-16 evidence (runbook order, sandbox).
    _write_release(release_path, {"status": "open"})
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    _write_release(release_path, {"status": "closed"})
    _oracle_clause_release_closed_refuses(
        handler, generate, _envelope_for("req-t7-b-closed", "key-t7-b-closed")
    )
    provider.update(_registry_raw(ALL_ROLLOUT_ROUTES))
    _write_release(release_path, {"status": "open"})
    newly = []
    for route in ALL_ROLLOUT_ROUTES:
        soul, backend = route.split("/")
        rid = "req-t7-b-open-%s-%s" % (soul, backend.replace(".", "_"))
        status, code = _registry_probe(provider, ROLLOUT_TOKEN, soul, backend, rid)
        assert (status, code) == ("admitted", "admit"), (route, status, code)
        before = len(generate.calls)
        assert handler(_envelope_for(rid, "key-" + rid)) == generate.text
        delta = len(generate.calls) - before
        assert delta == 1
        if route in set(B_NEW_ROUTES):
            newly.append(route)
        _record(
            {
                "batch": "A" if route in set(A_ROUTES) else "B",
                "route": route,
                "agent": "agent_" + soul,
                "request_id": rid,
                "status": "admitted",
                "code": "admit",
                "generate_delta": delta,
                "phase": "B-open",
                "layer": "registry+release",
            }
        )
    assert tuple(newly) == B_NEW_ROUTES
    assert len(newly) == 16
    assert set(A_ROUTES).isdisjoint(set(newly))
    assert set(A_ROUTES) | set(newly) == set(ALL_ROLLOUT_ROUTES)
    assert len(set(A_ROUTES) | set(newly)) == 20
    # Batch sets mutate only via installed snapshot, not via file edits alone.
    assert set(provider.current().clients[0].allowed_routes) == set(ALL_ROLLOUT_ROUTES)


# -- T7-5: batch is registry verdict, not tools/no-tools path ----------------


def test_t7_batch_membership_independent_of_tools_path(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    provider = RegistryProvider(_registry_raw(A_ROUTES))
    # Same A route travels both paths and succeeds on both (no-tools handler
    # and tools loop): the batch did not come from "has tools".
    soul, backend = "rem", "primary"
    status, _ = _registry_probe(provider, ROLLOUT_TOKEN, soul, backend, "req-t7-path-1")
    assert status == "admitted"
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    assert handler(_envelope_for("req-t7-path-1", "key-t7-path-1")) == generate.text

    pool = sa_pool.PoolStore()
    head = _head()
    gen2_calls = []

    def _gen(snapshot, holder, phase_name):
        gen2_calls.append(phase_name)
        return "t7-%s" % phase_name

    tools_result = sa_loop.run_tool_request(
        snapshot=head,
        provenance=sa_schema.Provenance(
            created_utc="2026-11-11T17:00:00-05:00",
            snapshot_digest=sa_schema.snapshot_digest(head),
            transitions=_TOOLS_PATH,
        ),
        holder=sa_schema.new_holder(
            "sa7-live:req-t7-path-2", (sa_schema.snapshot_digest(head),)
        ),
        pool=pool,
        fingerprint="t7-fingerprint-tools-path",
        harness_context=("t7-harness-ctx",),
        handle_ids=("h1",),
        tool_runner=lambda hid, ctx, gen1: ("natural_note", "t7-note"),
        generate=_gen,
        make_tail_draft=lambda evidence: sa_loop.build_tail_draft(
            "t7-clean-draft", (("natural_note", "t7-note"),)
        ),
        tail_live=_live("tail"),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    assert tools_result.text == "t7-gen2"
    assert (tools_result.loads, tools_result.generation_attempts) == (2, 2)
    # A B route in phase A is refused before any path is chosen (registry
    # layer precedes the no-tools/tools branch, on either path).
    status_b, code_b = _registry_probe(
        provider, ROLLOUT_TOKEN, "mai", "primary", "req-t7-path-3"
    )
    assert (status_b, code_b) == ("refused-403", "forbidden")


# -- T7-6: bidirectional mutations (relaxed / tightened / early-B) -----------


def test_t7_mutation_relaxed_always_open_turns_red_then_revert_green(
    tmp_path, monkeypatch
):
    """Relaxed mutant: gate forced open admits while closed (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    monkeypatch.setattr(sa_bridge, "is_live_inbound_open", lambda path: True)
    red = handler(_envelope_for("req-t7-m1", "key-t7-m1"))
    assert red == generate.text  # RED: closed gate ignored.
    if len(generate.calls) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed-open did not turn red")
    monkeypatch.undo()
    _oracle_clause_release_closed_refuses(
        handler, _CountingGenerate(), _envelope_for("req-t7-m1b", "key-t7-m1b")
    )


def test_t7_mutation_tightened_always_closed_turns_red_then_revert_green(
    tmp_path, monkeypatch
):
    """Tightened mutant: gate forced closed refuses while open (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    monkeypatch.setattr(sa_bridge, "is_live_inbound_open", lambda path: False)
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope_for("req-t7-m2", "key-t7-m2"))
    assert _refuse_code(exc_info.value) == (403, "forbidden")  # RED: open ignored.
    if len(generate.calls) != 0:
        pytest.fail("MUTATION DUD -- STOP, report: tightened-closed leaked generate")
    monkeypatch.undo()
    fresh = _CountingGenerate()
    handler2 = sa_bridge.build_live_handler(
        live=_live_loop(), generate=fresh, release_path=release_path
    )
    _oracle_clause_release_open_admits(
        handler2, fresh, _envelope_for("req-t7-m2b", "key-t7-m2b")
    )


def test_t7_mutation_early_b_release_turns_red_then_revert_green(tmp_path):
    """Early-B mutant: installing the B allowlist during phase A admits a B
    route the phase-A matrix forbids (RED). Reverting the install restores
    green -- the same oracle judges both."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    provider = RegistryProvider(_registry_raw(A_ROUTES))
    _oracle_clause_phase_a_matrix(provider)  # green on the honest install.
    provider.update(_registry_raw(ALL_ROLLOUT_ROUTES))  # mutant: early B.
    status, code = _registry_probe(
        provider, ROLLOUT_TOKEN, "mai", "primary", "req-t7-m3"
    )
    assert (status, code) == ("admitted", "admit")  # RED: B released early.
    with pytest.raises(AssertionError):
        _oracle_clause_phase_a_matrix(provider)  # the oracle bites.
    provider.update(_registry_raw(A_ROUTES))  # revert the install.
    _oracle_clause_phase_a_matrix(provider)  # green again.
    assert sa_bridge.is_live_inbound_open(release_path) is True


def test_t7_mutation_reverted_full_oracle_green(tmp_path):
    """Reverted wiring: the full acceptance oracle passes end to end."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    provider = RegistryProvider(_registry_raw(A_ROUTES))
    generate = _CountingGenerate()
    handler = sa_bridge.build_live_handler(
        live=_live_loop(), generate=generate, release_path=release_path
    )
    matrix = _acceptance_oracle(
        release_path, provider, handler, generate,
        _envelope_for("req-t7-oracle", "key-t7-oracle"),
    )
    assert len(matrix) == 20
    assert len([r for r in matrix if r["status"] == "admitted"]) == 4
    assert len([r for r in matrix if r["status"] == "refused-403"]) == 16


# -- T7-7: AST gates (no spawn/net/kill/prod writes; batches pinned) ----------


def _own_source() -> str:
    return Path(__file__).read_text(encoding="utf-8")


def test_t7_ast_no_spawn_net_kill_or_prod_paths():
    tree = ast.parse(_own_source())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.name or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    for banned in ("subprocess", "socket", "signal"):
        assert banned not in imported, ("SA-6 T7", banned)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("Popen", "run", "call", "kill", "create_connection"):
            if isinstance(node.value, ast.Name) and node.value.id in ("subprocess", "socket", "os"):
                raise AssertionError("SA-6 T7: banned call site via %r" % (node.value.id,))
    # Kill tokens are only policed inside call arguments (never bare words):
    # the docstring states the prohibition in prose, which must not trip.
    arg_strings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts = list(node.args) + [kw.value for kw in node.keywords]
            for part in parts:
                for sub in ast.walk(part):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        arg_strings.append(sub.value)
    for blob in arg_strings:
        assert "Stop-Process" not in blob and "taskkill" not in blob
        assert "data/" not in blob and "data\\" not in blob


def test_t7_ast_batches_pinned_and_handler_wiring_intact():
    assert A_ROUTES == (
        "rem/primary",
        "rem/minimax-m3.1-flash",
        "rem/glm-5.3-flash",
        "rem/mimo-v2.6-flash",
    )
    assert len(B_NEW_ROUTES) == 16
    assert B_NEW_ROUTES == tuple(
        s + "/" + b for s in B_NEW_SOULS for b in BACKENDS
    )
    assert len(set(ALL_ROLLOUT_ROUTES)) == 20
    assert set(A_ROUTES).isdisjoint(set(B_NEW_ROUTES))
    digest = hashlib.sha256(
        json.dumps(sorted(ALL_ROLLOUT_ROUTES), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    assert digest == _ROLLOUT_SET_DIGEST
    src = _own_source()
    assert "RegistryProvider.update" in src or "provider.update" in src
    assert "build_live_handler" in src and "LiveRefused" in src
    assert _REPOSHA in src
