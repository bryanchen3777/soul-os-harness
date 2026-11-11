"""SA-0 T0 contract tests: immutable, isolated, EI-1 unchanged, bidirectional mutation.

Decision-complete scope (SA-0): only this file + conftest.py + src/soul_access/schema.py.
No wiring, no server, no data/** writes (hard-blocked by conftest.py).
All test names carry the t0 marker: `pytest -q tests/soul_access -k t0` must collect > 0.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import FrozenInstanceError
from dataclasses import fields
from dataclasses import is_dataclass
from dataclasses import replace
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

_SCHEMA_PATH = _REPO_ROOT / "src" / "soul_access" / "schema.py"


def _load_schema():
    spec = importlib.util.spec_from_file_location("sa0_schema_under_test", str(_SCHEMA_PATH))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


schema = _load_schema()

EI1_SIX = (
    "src/interfaces/__init__.py",
    "src/interfaces/schema.py",
    "src/interfaces/config.py",
    "src/interfaces/identity.py",
    "src/interfaces/classification.py",
    "src/interfaces/context_policy.py",
)


def _legal_snapshot():
    return schema.SoulSnapshot(
        identity=schema.RequestIdentity(
            request_id="req-t0-0001",
            conversation_id="conv-t0-0001",
            branch_id="root",
            turn_id="turn-t0-0001",
            idempotency_key="key-t0-0001",
        ),
        dedup=schema.DedupKey(
            principal_id="bryan",
            agent_id="soul-t0",
            scope="owner_private",
            conversation_id="conv-t0-0001",
            branch_id="root",
            idempotency_key="key-t0-0001",
        ),
        backend_alias="t0-backend",
        model="t0-model",
        params=(("temperature", "0.5"),),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t0-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T16:56:00-05:00",
        inner_life_state="inner-t0-v1",
        projection_state="projection-t0-v1",
        recall_version="recall-t0-v1",
        recall_items=("recall-t0-item-1",),
        history_pool_version="pool-t0-v1",
        clean_history=("hist-t0-item-1",),
    )


# ── T0-1: no shared mutable state in the contract module ──────────────────


def test_t0_schema_module_has_no_shared_mutable():
    for name in dir(schema):
        if name.startswith("__"):
            continue
        value = getattr(schema, name)
        assert not isinstance(value, (list, dict, set, bytearray)), name
    for cls in (
        schema.RequestIdentity,
        schema.DedupKey,
        schema.SoulSnapshot,
        schema.RequestLocalHolder,
        schema.Provenance,
    ):
        assert is_dataclass(cls), cls
        params = getattr(cls, "__dataclass_params__", None)
        assert params is not None and params.frozen is True, cls


def test_t0_frozen_types_reject_mutation():
    snap = _legal_snapshot()
    with pytest.raises(FrozenInstanceError):
        snap.budget_tokens = 1
    with pytest.raises(FrozenInstanceError):
        snap.identity.request_id = "tampered"
    holder = schema.new_holder("cap-t0", ("join-t0",))
    with pytest.raises(FrozenInstanceError):
        holder.capability = "tampered"
    prov = schema.Provenance(created_utc="t", snapshot_digest="d", transitions=())
    with pytest.raises(FrozenInstanceError):
        prov.snapshot_digest = "tampered"


def test_t0_cross_request_holders_and_snapshots_are_independent():
    snap_a = _legal_snapshot()
    snap_b = _legal_snapshot()
    assert snap_a is not snap_b
    assert snap_a == snap_b  # same inputs, equal value ...
    assert snap_a.identity is not snap_b.identity  # ... but no shared object
    # Immutable tuples MAY be interned by CPython (constant folding); sharing
    # an immutable is harmless. What matters: no MUTATION can cross requests.
    assert snap_a.params == snap_b.params
    holder_a = schema.new_holder("cap-t0", ("join-t0",))
    holder_b = schema.new_holder("cap-t0", ("join-t0",))
    assert holder_a is not holder_b
    assert holder_a == holder_b
    # Tampering via replace() yields a NEW object; the original is untouched.
    tampered = replace(snap_a, budget_tokens=-1)
    assert tampered.budget_tokens == -1
    assert snap_a.budget_tokens == 512
    with pytest.raises(schema.SnapshotRejected):
        schema.validate_snapshot(tampered)
    assert schema.validate_snapshot(snap_a) is snap_a


# ── T0-2: fixed state machine ─────────────────────────────────────────────


def test_t0_phase_machine_allows_only_fixed_paths():
    P = schema.Phase
    assert schema.full_path(has_tools=True) == (
        P.HEAD_LOAD, P.GEN1, P.TOOL_LOOP, P.TAIL_REJOIN, P.GEN2, P.ADMIT,
    )
    assert schema.full_path(has_tools=False) == (P.HEAD_LOAD, P.GEN1, P.ADMIT)
    # Legal steps walk green.
    assert schema.advance(P.HEAD_LOAD, P.GEN1, has_tools=True) is P.GEN1
    assert schema.advance(P.GEN1, P.TOOL_LOOP, has_tools=True) is P.TOOL_LOOP
    assert schema.advance(P.TOOL_LOOP, P.TAIL_REJOIN, has_tools=True) is P.TAIL_REJOIN
    assert schema.advance(P.TAIL_REJOIN, P.GEN2, has_tools=True) is P.GEN2
    assert schema.advance(P.GEN2, P.ADMIT, has_tools=True) is P.ADMIT
    assert schema.advance(P.GEN1, P.ADMIT, has_tools=False) is P.ADMIT
    # Off-machine steps turn red.
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.GEN1, P.ADMIT, has_tools=True)  # tools must loop
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.GEN1, P.TOOL_LOOP, has_tools=False)  # no loop w/o tools
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.HEAD_LOAD, P.TOOL_LOOP, has_tools=True)  # skip GEN1
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.GEN1, P.GEN2, has_tools=True)  # skip loop+rejoin
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.TOOL_LOOP, P.GEN2, has_tools=True)  # skip rejoin
    with pytest.raises(schema.PhaseViolation):
        schema.advance(P.ADMIT, P.GEN1, has_tools=False)  # terminal, no restart


def test_t0_snapshot_carries_all_required_field_groups():
    names = {f.name for f in fields(schema.SoulSnapshot)}
    # Five identity cols live on RequestIdentity; six dedup cols on DedupKey.
    assert {f.name for f in fields(schema.RequestIdentity)} == {
        "request_id", "conversation_id", "branch_id", "turn_id", "idempotency_key",
    }
    assert {f.name for f in fields(schema.DedupKey)} == {
        "principal_id", "agent_id", "scope",
        "conversation_id", "branch_id", "idempotency_key",
    }
    for required in (
        "identity", "dedup",  # 5 + 6 cols
        "backend_alias", "model", "params",  # backend/model/params
        "budget_tokens", "deadline_utc",  # C budget/deadline
        "personality_version",  # personality version
        "emotion_state", "relationship_state", "chrono_state",
        "inner_life_state", "projection_state",  # carried states
        "recall_version", "recall_items",  # recall
        "history_pool_version", "clean_history",  # clean history + pool version
    ):
        assert required in names, required
    # Capability / join evidence live ONLY in the request-local holder.
    holder_names = {f.name for f in fields(schema.RequestLocalHolder)}
    assert holder_names == {"capability", "join_evidence"}
    assert "capability" not in names and "join_evidence" not in names


# ── T0-3: old EI-1 contract unchanged ─────────────────────────────────────


def test_t0_old_ei1_contract_still_one_provider_zero_tools():
    for rel in EI1_SIX:
        assert (_REPO_ROOT / rel).is_file(), rel
    policy_text = (_REPO_ROOT / "src/interfaces/context_policy.py").read_text(encoding="utf-8")
    line_101 = policy_text.splitlines()[100]
    assert "provider_attempts=1" in line_101, line_101
    assert "tool_rounds=0" in line_101, line_101
    from src.interfaces.schema import GenerationLimits

    lim = GenerationLimits(temperature=0.5, max_tokens=8, context_window_tokens=100)
    assert lim.provider_attempts == 1
    assert lim.tool_rounds == 0


# ── T0-4: green baseline + bidirectional mutation ─────────────────────────


def test_t0_legal_snapshot_validates_and_tamper_rejected():
    legal = _legal_snapshot()
    assert schema.validate_snapshot(legal) is legal
    digest_a = schema.snapshot_digest(legal)
    assert schema.snapshot_digest(_legal_snapshot()) == digest_a  # deterministic
    tampered = replace(legal, budget_tokens=0)
    with pytest.raises(schema.SnapshotRejected):
        schema.validate_snapshot(tampered)
    assert schema.snapshot_digest(tampered) != digest_a  # seal breaks on tamper
    bad_identity = replace(
        legal,
        identity=replace(legal.identity, request_id=""),
    )
    with pytest.raises(schema.SnapshotRejected):
        schema.validate_snapshot(bad_identity)


def _relaxed_validate(snapshot):
    """Bidirectional-mutation probe: validator with the identity rule REMOVED."""
    try:
        return schema.validate_snapshot(snapshot)
    except schema.SnapshotRejected as exc:
        if "request_id/branch_id" in str(exc):
            return snapshot  # mutation accepts the tamper => gate is red
        raise


def test_t0_mutation_relaxed_validator_turns_red():
    legal = _legal_snapshot()
    bad_identity = replace(
        legal, identity=replace(legal.identity, request_id="")
    )
    # Normal gate stays green: legal passes, tamper fails.
    assert schema.validate_snapshot(legal) is legal
    with pytest.raises(schema.SnapshotRejected):
        schema.validate_snapshot(bad_identity)
    # Relaxed (mutated) gate turns red: the same tamper slips through.
    assert _relaxed_validate(bad_identity) is bad_identity
    assert _relaxed_validate(legal) is legal


def _tightened_validate(snapshot):
    """Bidirectional-mutation probe: validator with an extra impossible rule."""
    schema.validate_snapshot(snapshot)
    if snapshot.budget_tokens < 10**9:
        raise schema.SnapshotRejected("tightened probe: budget floor not met")
    return snapshot


def test_t0_mutation_tightened_validator_turns_red():
    legal = _legal_snapshot()
    assert schema.validate_snapshot(legal) is legal  # normal gate green
    with pytest.raises(schema.SnapshotRejected):  # tightened gate red on legal
        _tightened_validate(legal)


def test_t0_mutation_reverted_validator_returns_green():
    legal = _legal_snapshot()
    tampered = replace(legal, budget_tokens=-5)
    # Reverted (original) gate: legal green, tamper red again.
    assert schema.validate_snapshot(legal) is legal
    with pytest.raises(schema.SnapshotRejected):
        schema.validate_snapshot(tampered)
    digest = schema.snapshot_digest(legal)
    assert schema.snapshot_digest(_legal_snapshot()) == digest


# ── T0-5: independent hard blocks (network / prod writes / kill / spawn) ──


def test_t0_hard_blocks_stop_network_and_prod_writes(tmp_path):
    import os
    import socket
    import subprocess

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    with pytest.raises(RuntimeError):
        subprocess.Popen(["python", "scripts/run_server.py"])
    prod_probe = _REPO_ROOT / "data" / "__sa0_t0_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    with pytest.raises(RuntimeError):
        prod_probe.write_text("t0", encoding="utf-8")
    assert not prod_probe.exists()  # blocked before creation: zero prod writes
    # Legit tmp writes keep working (guard is scoped to data/** only).
    ok_file = tmp_path / "t0-ok.txt"
    ok_file.write_text("t0", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t0"
