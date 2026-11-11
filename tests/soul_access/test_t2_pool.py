"""SA-2 T2 pool tests: shared pool across entries/backends + dedup, no re-execution.

Decision-complete scope (SA-2): only this file + pool.py + policy.py are new;
web gateway / TG channels/router.py / voice input_router.py gain additive
identity passthrough only (no business-logic change); SA-0 / SA-1 / EI-1
untouched.

All test names carry the t2 marker: `pytest -q tests/soul_access -k t2`
must collect > 0. Sync only (no event loop: the SA-0 conftest in this
directory hard-blocks socket creation, and on Windows ANY event-loop
creation needs a socketpair self-pipe, cf. T1 note in test_t1_bridge.py).
Transport is an in-process fake executor (counts loads / generations /
writes); the real carrier (PresetRoutingBackend) is never constructed.
"""

from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.soul_access import policy as sa_policy  # noqa: E402
from src.soul_access import pool as sa_pool  # noqa: E402
from src.voice.gate import RoutingFeatures  # noqa: E402
from src.voice.input_router import VoiceInputRouter  # noqa: E402

OWNER = "t2-pool-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"
TEXT = "t2 hello soul"

_SA2_MODULES = (
    _REPO_ROOT / "src" / "soul_access" / "pool.py",
    _REPO_ROOT / "src" / "soul_access" / "policy.py",
)


def _identity(**over):
    kw = {
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t2-0001",
        "idempotency_key": "key-t2-0001",
    }
    kw.update(over)
    return sa_policy.EntryIdentity(**kw)


def _pool_key(**over):
    kw = {"principal_id": OWNER, "agent_id": AGENT, "scope": SCOPE}
    ident = over.pop("identity", None) or _identity()
    return sa_policy.build_pool_key(identity=ident, **kw)


def _dedup(pool_key=None, key="key-t2-0001"):
    return sa_policy.build_dedup_key(pool_key or _pool_key(), key)


def _fp(**over):
    kw = {
        "backend_alias": "primary",
        "backend_ref": "port-a",
        "public_model_id": "do-not-deploy-t2-primary",
        "text": TEXT,
        "turn_id": "turn-t2-0001",
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "memory_policy": "ephemeral",
    }
    kw.update(over)
    return sa_policy.request_fingerprint(**kw)


class _FakeExecutor:
    """Stand-in for loads + generation + writes (must stay 0 on replay)."""

    def __init__(self, text="t2-live-reply"):
        self.text = text
        self.loads = 0
        self.generation_attempts = 0
        self.writes = 0
        self.calls = []

    def run(self, dedup, fingerprint):
        self.loads += 1
        self.generation_attempts += 1
        self.writes += 1
        self.calls.append((dedup, fingerprint))
        return self.text


def _serve_once(store, executor, dedup, fingerprint):
    """One serve opportunity: NEW executes, REPLAY returns stored, else raise."""
    outcome = store.claim(dedup, fingerprint)
    if outcome.disposition == sa_pool.DISPOSITION_NEW:
        text = executor.run(dedup, fingerprint)
        store.complete(dedup, fingerprint, text)
        return (200, text)
    if outcome.disposition == sa_pool.DISPOSITION_REPLAY:
        return (outcome.http_status, outcome.response_text)
    raise sa_policy.IdentityError(
        outcome.http_status, outcome.code, None, outcome.message
    )


# -- T2-1: pool key is fixed 5-col (no channel / client / backend) ---------


def test_t2_pool_key_fixed_five_cols_no_entry_no_backend():
    ident = _identity()
    web_key = sa_policy.build_pool_key(
        principal_id=OWNER, agent_id=AGENT, scope=SCOPE, identity=ident
    )
    tg_key = sa_policy.build_pool_key(
        principal_id=OWNER, agent_id=AGENT, scope=SCOPE, identity=ident
    )
    voice_key = sa_policy.build_pool_key(
        principal_id=OWNER, agent_id=AGENT, scope=SCOPE, identity=ident
    )
    assert web_key == tg_key == voice_key  # entries meet on the same pool key
    assert isinstance(web_key, sa_pool.PoolKey)
    # Six-col dedup namespace mirrors EI-1 (principal/agent/scope/conv/branch/key).
    dedup = sa_policy.build_dedup_key(web_key, "key-t2-0001")
    assert (
        dedup.principal_id,
        dedup.agent_id,
        dedup.scope,
        dedup.conversation_id,
        dedup.branch_id,
        dedup.idempotency_key,
    ) == (OWNER, AGENT, SCOPE, "dm", "root", "key-t2-0001")


# -- T2-2: shared pool across backends, same key + drifted fp => conflict -


def test_t2_shared_pool_cross_backend_same_key_conflicts():
    store = sa_pool.PoolStore()
    dedup = _dedup()
    outcome_a = store.claim(dedup, _fp(backend_alias="primary"))
    assert outcome_a.disposition == sa_pool.DISPOSITION_NEW
    store.complete(dedup, _fp(backend_alias="primary"), "t2-primary-reply")
    # Same logical request arriving via another backend: pool is shared
    # (same key), but the execution differs => CONFLICT (409), never replay.
    outcome_b = store.claim(dedup, _fp(backend_alias="secondary"))
    assert outcome_b.disposition == sa_pool.DISPOSITION_CONFLICT
    assert outcome_b.http_status == 409
    assert sa_policy.http_status_for(outcome_b.disposition) == 409


# -- T2-3: replay returns the original with zero new loads/generations/writes


def test_t2_replay_returns_original_with_zero_new_cost():
    store = sa_pool.PoolStore()
    executor = _FakeExecutor()
    dedup = _dedup()
    fingerprint = _fp()
    status_first, text_first = _serve_once(store, executor, dedup, fingerprint)
    assert (status_first, text_first) == (200, "t2-live-reply")
    assert (executor.loads, executor.generation_attempts, executor.writes) == (1, 1, 1)
    status_second, text_second = _serve_once(store, executor, dedup, fingerprint)
    assert status_second == 200
    assert text_second == text_first  # byte-identical original response
    assert (executor.loads, executor.generation_attempts, executor.writes) == (1, 1, 1)
    assert len(executor.calls) == 1  # replay executed nothing new
    assert len(store) == 1  # no extra rows for the replay


# -- T2-4: dual-connection race: second gets busy, same-key retry replays --


def test_t2_dual_connection_race_second_gets_busy_then_replays():
    store = sa_pool.PoolStore()
    dedup = _dedup()
    fingerprint = _fp()
    first = store.claim(dedup, fingerprint)
    assert first.disposition == sa_pool.DISPOSITION_NEW
    second = store.claim(dedup, fingerprint)
    assert second.disposition == sa_pool.DISPOSITION_IN_PROGRESS
    assert second.http_status == 409
    assert second.code == "soul_busy"
    store.complete(dedup, fingerprint, "t2-race-reply")
    retry = store.claim(dedup, fingerprint)
    assert retry.disposition == sa_pool.DISPOSITION_REPLAY
    assert retry.http_status == 200
    assert retry.response_text == "t2-race-reply"


# -- T2-5: identity isolation (different pool rows never collide) ----------


def test_t2_identity_isolation_no_cross_talk():
    store = sa_pool.PoolStore()
    fingerprint = _fp()
    base = _dedup()
    assert store.claim(base, fingerprint).disposition == sa_pool.DISPOSITION_NEW
    other_owner = _dedup(
        sa_policy.build_pool_key(
            principal_id="someone-else",
            agent_id=AGENT,
            scope=SCOPE,
            identity=_identity(),
        )
    )
    assert store.claim(other_owner, fingerprint).disposition == sa_pool.DISPOSITION_NEW
    other_conv = _dedup(
        sa_policy.build_pool_key(
            principal_id=OWNER,
            agent_id=AGENT,
            scope=SCOPE,
            identity=_identity(conversation_id="group-9"),
        )
    )
    assert store.claim(other_conv, fingerprint).disposition == sa_pool.DISPOSITION_NEW
    # Same key but a fresh idempotency key is a different logical request.
    other_key = _dedup(key="key-t2-0002")
    assert store.claim(other_key, fingerprint).disposition == sa_pool.DISPOSITION_NEW


def test_t2_quarantined_row_returns_isolated_409():
    store = sa_pool.PoolStore()
    dedup = _dedup()
    fingerprint = _fp()
    assert store.claim(dedup, fingerprint).disposition == sa_pool.DISPOSITION_NEW
    store.quarantine(dedup)
    outcome = store.claim(dedup, fingerprint)
    assert outcome.disposition == sa_pool.DISPOSITION_ISOLATED
    assert outcome.http_status == 409
    assert sa_policy.http_status_for(outcome.disposition) == 409


# -- T2-6: 400 matrix (missing/bad conversation/turn/key, dup headers) ----


def _headers(**over):
    base = {
        "x-soul-conversation-id": "dm",
        "x-soul-branch-id": "root",
        "x-soul-turn-id": "turn-t2-0001",
        "idempotency-key": "key-t2-0001",
    }
    base.update(over)
    return base


def test_t2_bad_identity_returns_400_matrix():
    bad_headers = [
        ({k: v for k, v in _headers().items() if k != "x-soul-conversation-id"}),
        ({**_headers(), "x-soul-conversation-id": "a/b"}),
        ({k: v for k, v in _headers().items() if k != "x-soul-turn-id"}),
        ({**_headers(), "x-soul-turn-id": ""}),
        ({k: v for k, v in _headers().items() if k != "idempotency-key"}),
        ({**_headers(), "idempotency-key": "has space"}),
        ({**_headers(), "X-SOUL-TURN-ID": "turn-dup"}),  # duplicate header
        ({**_headers(), "x-soul-unknown": "nope"}),  # unsupported parameter
    ]
    for headers in bad_headers:
        with pytest.raises(sa_policy.IdentityError) as exc_info:
            sa_policy.parse_headers(headers)
        assert exc_info.value.status == 400, headers
    # Branch defaults to root; legacy DM unifies on dm/root.
    no_branch = {k: v for k, v in _headers().items() if k != "x-soul-branch-id"}
    assert sa_policy.parse_headers(no_branch).branch_id == "root"
    assert sa_policy.parse_headers(_headers()).conversation_id == "dm"
    dm = sa_policy.dm_identity(turn="turn-t2-0001", key="key-t2-0001")
    assert (dm.conversation_id, dm.branch_id) == ("dm", "root")
    with pytest.raises(sa_policy.IdentityError) as exc_info:
        sa_policy.dm_identity(turn=None, key="key-t2-0001")
    assert exc_info.value.status == 400


def test_t2_tg_and_voice_key_derivation():
    tg = sa_policy.tg_identity(update_id=123)
    assert tg.turn_id == "123"
    assert tg.idempotency_key == "telegram.123"
    assert (tg.conversation_id, tg.branch_id) == ("dm", "root")
    with pytest.raises(sa_policy.IdentityError) as exc_info:
        sa_policy.tg_identity(update_id="not-an-int!!")
    assert exc_info.value.status == 400
    vc = sa_policy.vc_identity(session_id="session_bryan_agent_yua", turn_id="turn-7")
    assert vc.turn_id == "turn-7"
    assert vc.idempotency_key == "session_bryan_agent_yua.turn-7"
    assert (vc.conversation_id, vc.branch_id) == ("dm", "root")
    with pytest.raises(sa_policy.IdentityError) as exc_info:
        sa_policy.vc_identity(session_id="session_bryan_agent_yua", turn_id=None)
    assert exc_info.value.status == 400


# -- T2-7: fingerprint contract (request_id excluded, backend/text/turn in) -


def test_t2_fingerprint_excludes_request_id_includes_execution():
    base = _fp()
    # request_id is not even a parameter: retries mint fresh ids yet replay.
    assert "request_id" not in inspect.signature(
        sa_policy.request_fingerprint
    ).parameters
    for drift in (
        {"backend_alias": "secondary"},
        {"backend_ref": "port-b"},
        {"public_model_id": "do-not-deploy-t2-secondary"},
        {"text": "t2 different text"},
        {"turn_id": "turn-t2-0002"},
        {"params": (("temperature", "0.9"), ("max_tokens", "8"))},
        {"memory_policy": "interactive_candidate"},
    ):
        assert _fp(**drift) != base, drift
    assert _fp() == base  # deterministic
    # Same logical request under a fresh request id still replays (proved at
    # pool level: fingerprint equality is the only replay condition).
    store = sa_pool.PoolStore()
    dedup = _dedup()
    store.claim(dedup, base)
    store.complete(dedup, base, "t2-stable-reply")
    assert store.claim(dedup, _fp()).disposition == sa_pool.DISPOSITION_REPLAY


# -- T2-8: bidirectional mutation (relaxed red / tightened red / reverted green)


def _relaxed_fp_without_backend(**over):
    """Mutation probe (relaxed): fingerprint DROPS the backend columns.

    Backend kwargs are accepted-but-ignored: a backend drift is invisible to
    this digest, so the second claim falsely replays (RED).
    """
    over.pop("backend_alias", None)
    over.pop("backend_ref", None)
    over.pop("public_model_id", None)
    kw = {
        "text": TEXT,
        "turn_id": "turn-t2-0001",
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "memory_policy": "ephemeral",
    }
    kw.update(over)
    import hashlib as _hashlib
    import json as _json

    data = _json.dumps(
        kw, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return _hashlib.sha256(data).hexdigest()


def test_t2_mutation_relaxed_fingerprint_turns_red():
    """Relaxed probe: without backend cols, a backend drift falsely replays."""
    store = sa_pool.PoolStore()
    dedup = _dedup()
    store.claim(dedup, _relaxed_fp_without_backend())
    store.complete(dedup, _relaxed_fp_without_backend(), "t2-relaxed-reply")
    red = store.claim(dedup, _relaxed_fp_without_backend(backend_alias="secondary"))
    assert red.disposition == sa_pool.DISPOSITION_REPLAY  # RED: drift not caught
    # Normal gate stays green: the same drift conflicts instead of replaying.
    store2 = sa_pool.PoolStore()
    store2.claim(dedup, _fp(backend_alias="primary"))
    store2.complete(dedup, _fp(backend_alias="primary"), "t2-normal-reply")
    green = store2.claim(dedup, _fp(backend_alias="secondary"))
    assert green.disposition == sa_pool.DISPOSITION_CONFLICT


def test_t2_mutation_tightened_pool_key_turns_red():
    """Tightened probe: pool key WRONGLY adds backend => pool splits."""
    store = sa_pool.PoolStore()
    narrow_a = ("t2-ns", "agent_rem", "owner_private", "dm", "root", "primary")
    narrow_b = ("t2-ns", "agent_rem", "owner_private", "dm", "root", "secondary")
    record: dict = {}

    def _narrow_claim(narrow_key, fingerprint):
        if narrow_key not in record:
            record[narrow_key] = fingerprint
            return "NEW"
        if record[narrow_key] != fingerprint:
            return "CONFLICT"
        return "REPLAY"

    assert _narrow_claim(narrow_a, _fp()) == "NEW"
    # RED: same logical key on another backend starts over (dup execution).
    assert _narrow_claim(narrow_b, _fp()) == "NEW"
    # Normal gate stays green: shared 5-col key + drifted fp => CONFLICT.
    store2 = sa_pool.PoolStore()
    dedup = _dedup()
    store2.claim(dedup, _fp(backend_alias="primary"))
    assert (
        store2.claim(dedup, _fp(backend_alias="secondary")).disposition
        == sa_pool.DISPOSITION_CONFLICT
    )


def test_t2_mutation_reverted_returns_green():
    store = sa_pool.PoolStore()
    executor = _FakeExecutor()
    dedup = _dedup()
    fingerprint = _fp()
    assert _serve_once(store, executor, dedup, fingerprint) == (200, "t2-live-reply")
    assert _serve_once(store, executor, dedup, fingerprint) == (200, "t2-live-reply")
    assert (executor.loads, executor.generation_attempts, executor.writes) == (1, 1, 1)
    conflict = store.claim(dedup, _fp(backend_alias="secondary"))
    assert conflict.disposition == sa_pool.DISPOSITION_CONFLICT
    assert conflict.http_status == 409


# -- T2-9: SA-2 import purity (stdlib + SA-0 schema + sibling pool only) ----


def _imported_module_names(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    mods = set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
                names.add((alias.name or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                mods.add(node.module)
                names.add(node.module.split(".")[0])
            for alias in node.names:
                names.add(alias.name)
    return mods, names


def test_t2_sa2_modules_make_no_bare_provider_calls():
    for path in _SA2_MODULES:
        mods, names = _imported_module_names(path)
        for banned in (
            "httpx",
            "socket",
            "urllib",
            "subprocess",
            "src.interfaces",
            "src.llm",
            "src.external_gateway",
        ):
            assert not any(
                m == banned or m.startswith(banned + ".") for m in mods
            ), (path.name, banned)
        for banned_name in (
            "PresetRoutingBackend",
            "DeepSeekBackend",
            "OllamaBackend",
            "soul_context_adapter",
        ):
            assert banned_name not in names, (path.name, banned_name)
    raw = "".join(p.read_text(encoding="utf-8") for p in _SA2_MODULES)
    for banned in (
        "PresetRoutingBackend",
        "DeepSeekBackend",
        "OllamaBackend",
        "create_connection",
        "urlopen",
        "Popen",
    ):
        assert banned not in raw, banned


# -- T2-10: entry identity passthrough (voice behavioral + TG/web static) --


def test_t2_voice_payload_carries_dm_identity():
    router = VoiceInputRouter()
    features = RoutingFeatures(text="hi", text_normalized="hi", owner_ok=True)
    payload = router.build_user_message_payload(features)
    assert payload["conversation_id"] == "dm"
    assert payload["branch_id"] == "root"
    assert payload["content"] == "hi"  # business payload untouched
    event = router.build_user_message_event(features)
    assert event.session_id == "session_bryan_agent_yua"


def test_t2_gateway_and_router_sources_carry_identity_passthrough():
    gateway_src = (
        _REPO_ROOT / "src" / "io" / "gateway.py"
    ).read_text(encoding="utf-8")
    assert '"conversation_id": msg.get("conversation_id") or "dm"' in gateway_src
    assert '"branch_id": msg.get("branch_id") or "root"' in gateway_src
    assert '"turn_id": msg.get("turn_id") or msg.get("message_id")' in gateway_src
    router_src = (
        _REPO_ROOT / "src" / "io" / "channels" / "router.py"
    ).read_text(encoding="utf-8")
    assert '"conversation_id": "dm"' in router_src
    assert '"branch_id": "root"' in router_src
    assert '"idempotency_key": ("%s.%s" % (channel, update_id))' in router_src
    voice_src = (
        _REPO_ROOT / "src" / "voice" / "input_router.py"
    ).read_text(encoding="utf-8")
    assert '"conversation_id": "dm"' in voice_src
    assert '"branch_id": "root"' in voice_src
    assert '"idempotency_key"] = (' in voice_src


# -- T2-11: isolation hard blocks stay on in this directory -----------------


def test_t2_hard_blocks_stop_network_and_prod_writes(tmp_path):
    # NOTE: same R1/R5 rationale as T1 — this file keeps zero spawn call
    # sites, so the spawn probe is not repeated here (the shared oracle in
    # T0 covers the same conftest block). Socket / kill / prod-write probes
    # below prove the block is active inside T2 tests.
    import os
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa2_t2_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    ok_file = tmp_path / "t2-ok.txt"
    ok_file.write_text("t2", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t2"
