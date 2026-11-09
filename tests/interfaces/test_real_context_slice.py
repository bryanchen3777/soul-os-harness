"""EI-2B-HARNESS-REAL-CONTEXT-OFFLINE-VERTICAL-WIRING-1 acceptance (tests 01-21).

Vertical flow under test: EI-1 admit -> ticket-scope fail-closed checks
-> EI-2A journal claim (runtime_port.submit) -> genuine Rem/Ram persona +
SAGE prefetch + genuine _build_messages_private -> request-local mock
backend generation. No SyntheticSoulStore, no real LLM, no production IO.

RUN RULE (structural, non-negotiable): the OS environment MUST preset
SOUL_OS_DATA_DIR to a fresh isolated root BEFORE the pytest process starts,
so that proxy.py import-time captures bind to isolation, never production::

    $env:SOUL_OS_DATA_DIR = "<fresh-tmp-root>"
    .venv\\Scripts\\python.exe -m pytest tests/interfaces/test_real_context_slice.py -q -p no:cacheprovider

Ticket wording map used below: ticket "BUSY" == journal "IN_PROGRESS"
(a live accepted/executing row for the same namespace). There is no
in-slice lease; concurrency beyond one one-shot process is
SHARED_RUNTIME_BLOCKED.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import os
import sqlite3
import sys
import threading
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.interfaces.config import validate_registry
from src.interfaces.context_policy import admit
from src.interfaces.context_policy import idempotency_namespace
from src.interfaces.context_policy import request_fingerprint
from src.interfaces.journal import Journal
from src.interfaces.real_context_slice import DEFAULT_SESSION_ID
from src.interfaces.real_context_slice import ISOLATED_ONESHOT_ONLY
from src.interfaces.real_context_slice import OWNER_USER_ID
from src.interfaces.real_context_slice import SHARED_RUNTIME_BLOCKED
from src.interfaces.real_context_slice import RealSliceDriver
from src.interfaces.real_context_slice import RealSliceRequest
from src.interfaces.real_context_slice import default_memory_store_factory
from src.interfaces.real_context_slice import handle_text_request
from src.interfaces.runtime_port import FakeClock
from src.interfaces.schema import InterfaceError
from src.interfaces.soul_context_adapter import IsolationError
from src.interfaces.soul_context_adapter import load_harness_persona

REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER = "owner-01"
TOKEN_OWNER = "token-owner-real-slice-001"
TOKEN_UTIL = "token-utility-real-slice-002"
EPOCH = 7
NOW = 1000.0


@pytest.fixture(autouse=True)
def _clean_relationship_singleton():
    """Same singleton hygiene as the adapter suite (this file only).

    Other test modules may leave the process-global relationships
    singleton pinned; the genuine build path refuses a foreign-pinned
    singleton, so reset it around every test here. This disturbs no live
    runtime: the test process is an isolated one-shot process.
    """
    from src.soul import relationships as _rel

    with _rel._manager_lock:
        _rel._manager_singleton = None
    yield
    with _rel._manager_lock:
        _rel._manager_singleton = None


# ── helpers ──────────────────────────────────────────────────────────

def _isolated_writes_root(tmp_path: Path) -> Path:
    env = os.environ.get("SOUL_OS_DATA_DIR")
    assert env, "SOUL_OS_DATA_DIR must be preset before pytest starts"
    root = Path(env).resolve() / tmp_path.name / "slice_data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _route(soul, backend_alias, agent, model, ref, tokens=4096):
    return {"soul_alias": soul, "backend_alias": backend_alias,
            "agent_id": agent, "public_model_id": model,
            "backend_ref": ref, "context_window_tokens": tokens}


def _owner_client(client_id="client-owner", token=TOKEN_OWNER,
                  allowed=None):
    if allowed is None:
        allowed = ["rem/backend-a", "rem/backend-b", "ram/backend-a"]
    return {"client_id": client_id, "principal_id": OWNER,
            "purpose": "interactive", "allowed_routes": list(allowed),
            "scope": "owner_private", "allow_persistent_memory": True,
            "credential_sha256": _digest(token)}


def _base_routes():
    return [_route("rem", "backend-a", "agent_rem", "mock-rem-a", "port-a"),
            _route("rem", "backend-b", "agent_rem", "mock-rem-b", "port-b"),
            _route("ram", "backend-a", "agent_ram", "mock-ram-a", "port-a")]


def make_owner_registry():
    return validate_registry(
        {"owner_principal_id": OWNER, "backends": ["port-a", "port-b"],
         "routes": _base_routes(), "clients": [_owner_client()]})


def make_restricted_registry():
    return validate_registry(
        {"owner_principal_id": OWNER, "backends": ["port-a", "port-b"],
         "routes": _base_routes(),
         "clients": [_owner_client(allowed=["rem/backend-a",
                                            "rem/backend-b"])]})


def make_utility_registry():
    return validate_registry(
        {"owner_principal_id": OWNER, "backends": ["port-a"],
         "routes": [_route("rem", "backend-a", "agent_rem",
                           "mock-rem-a", "port-a")],
         "clients": [{"client_id": "client-u", "principal_id": OWNER,
                      "purpose": "utility",
                      "allowed_routes": ["rem/backend-a"],
                      "scope": "none", "allow_persistent_memory": False,
                      "credential_sha256": _digest(TOKEN_UTIL)}]})


def make_poisoned_registry():
    routes = [_route("rem", "backend-a", "agent_ram", "mock-rem-a",
                     "port-a"),
              _route("rem", "backend-b", "agent_rem", "mock-rem-b",
                     "port-b"),
              _route("ram", "backend-a", "agent_ram", "mock-ram-a",
                     "port-a")]
    return validate_registry(
        {"owner_principal_id": OWNER, "backends": ["port-a", "port-b"],
         "routes": routes, "clients": [_owner_client()]})


class MockBackend:
    """Request-local mock backend: async complete + full call record."""

    def __init__(self, tag: str, reply: str):
        self.tag = tag
        self.reply = reply
        self.calls = []

    async def complete(self, messages, model, **kwargs):
        self.calls.append({"messages": list(messages), "model": model,
                           "kwargs": dict(kwargs)})
        return self.reply

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def last_model(self):
        return self.calls[-1]["model"] if self.calls else None

    @property
    def last_messages(self):
        return self.calls[-1]["messages"] if self.calls else None

    def blob(self) -> str:
        if not self.calls:
            return ""
        return "\n".join(m.get("content", "")
                         for m in self.last_messages)


class FailingBackend(MockBackend):
    async def complete(self, messages, model, **kwargs):
        self.calls.append({"messages": list(messages), "model": model,
                           "kwargs": dict(kwargs)})
        raise RuntimeError("mock-boom-generation-failure")


def _journal_at(root: Path, name="requests.db", epoch=EPOCH, now=NOW):
    db = root / "journal" / name
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=30, isolation_level=None,
                           check_same_thread=False)
    journal = Journal(conn, epoch=epoch)
    return conn, journal, FakeClock(now, epoch)


def _slice_env(tmp_path, registry=None, backends=None,
               journal_name="requests.db"):
    root = _isolated_writes_root(tmp_path)
    reg = registry if registry is not None else make_owner_registry()
    conn, journal, clock = _journal_at(root, journal_name)
    driver = RealSliceDriver()
    ports = backends if backends is not None else {
        "port-a": MockBackend("A", "mock-reply-A"),
        "port-b": MockBackend("B", "mock-reply-B")}
    return SimpleNamespace(root=root, registry=reg, conn=conn,
                           journal=journal, clock=clock, driver=driver,
                           backends=ports)


def _req(token, soul, backend_alias, model, text="早安請問",
         conv="conv-01", branch="branch-01", turn="turn-01",
         key="key-01", rid="req-01"):
    return RealSliceRequest(
        bearer_token=token, soul_alias=soul,
        backend_alias=backend_alias,
        raw_request={"model": model,
                     "messages": [{"role": "user", "content": text}]},
        headers={"X-Soul-Conversation-Id": conv,
                 "X-Soul-Branch-Id": branch, "X-Soul-Turn-Id": turn,
                 "Idempotency-Key": key},
        request_id=rid)


def _rows(conn):
    return conn.execute(
        "SELECT request_id, state, response_id, response_body, outcomes"
        " FROM requests ORDER BY request_id").fetchall()


def _seed_fact(provider, subject, predicate, obj_text, source_pair,
               session_id="seed"):
    from src.memory.sage.models import Fact

    fact = Fact(subject=subject, predicate=predicate,
                **{"object": obj_text},
                source="user", session_id=session_id,
                source_pair=source_pair)
    fid = provider._writer.add_fact(fact)
    assert fid, "rule-based add_fact must accept the seeded fact"
    return fid


def _counting_factory(counter):
    def _fac(resolved_root, agent_id):
        store = default_memory_store_factory(resolved_root, agent_id)
        orig_append = store.append
        orig_batch = store.append_batch

        def _a(*a, **k):
            counter.append(("append", agent_id))
            return orig_append(*a, **k)

        def _b(*a, **k):
            counter.append(("append_batch", agent_id))
            return orig_batch(*a, **k)

        store.append = _a  # type: ignore[method-assign]
        store.append_batch = _b  # type: ignore[method-assign]
        return store
    return _fac


def _seeding_factory(seeds):
    """seeds: {agent_id: [(session_id, role, content), ...]}."""

    def _fac(resolved_root, agent_id):
        store = default_memory_store_factory(resolved_root, agent_id)
        for sess, role, content in seeds.get(agent_id, []):
            store.append(sess, role, content,
                         speaker=agent_id if role == "assistant" else "")
        return store
    return _fac


# ── contract surface ─────────────────────────────────────────────────

def test_00_contract_flags_and_no_client_identity_fields():
    assert ISOLATED_ONESHOT_ONLY is True
    assert isinstance(SHARED_RUNTIME_BLOCKED, str) and SHARED_RUNTIME_BLOCKED
    assert OWNER_USER_ID == "bryan"
    names = {f.name for f in fields(RealSliceRequest)}
    assert names == {"bearer_token", "soul_alias", "backend_alias",
                     "raw_request", "headers", "request_id"}, names
    for banned in ("agent_id", "user_id", "principal_id", "scope",
                   "memory", "model"):
        assert banned not in names, banned


# ── 1/2: authorized Owner -> Rem / Ram real context -> mock reply ────

def test_01_owner_rem_real_context_mock_reply(tmp_path):
    env = _slice_env(tmp_path)
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a"),
            data_root=env.root)
        assert out.kind == "NEW"
        assert out.agent_id == "agent_rem"
        assert out.response_body == "mock-reply-A"
        assert out.response_id == "resp-req-01"
        assert out.memory_evidence is None
        assert out.user_id == OWNER_USER_ID
        _agent, genuine = load_harness_persona("rem")
        assert out.persona_chars == len(genuine) > 0
        assert out.message_count > 1
        backend = env.backends["port-a"]
        assert backend.call_count == 1
        assert backend.last_model == "mock-rem-a"
        assert backend.last_messages[0]["role"] == "system"
        assert "雷姆" in backend.blob()
        rows = _rows(env.conn)
        assert len(rows) == 1
        rid, state, _resp_id, body, outcomes = rows[0]
        assert (rid, state, body) == ("req-01", "transport_sent",
                                     "mock-reply-A")
        import json as _json
        assert _json.loads(outcomes) == {"memory": "pending"}
    finally:
        env.conn.close()


def test_02_owner_ram_real_context_mock_reply(tmp_path):
    env = _slice_env(tmp_path, journal_name="ram.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "ram", "backend-a", "mock-ram-a",
                         text="午安", rid="req-ram-01"),
            data_root=env.root)
        assert out.kind == "NEW"
        assert out.agent_id == "agent_ram"
        assert out.response_body == "mock-reply-A"
        _agent, genuine = load_harness_persona("ram")
        assert out.persona_chars == len(genuine) > 0
        backend = env.backends["port-a"]
        assert backend.call_count == 1
        assert backend.last_model == "mock-ram-a"
        assert "拉姆" in backend.blob()
        assert "你是 Yua" not in backend.blob()
    finally:
        env.conn.close()


# ── 3: correct soul identity (server-side route binding) ─────────────

def test_03_correct_soul_identity_from_route(tmp_path):
    env = _slice_env(tmp_path, journal_name="id.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-id-01"),
            data_root=env.root)
        assert out.kind == "NEW"
        assert (out.soul_alias, out.agent_id) == ("rem", "agent_rem")
        assert out.client_id == "client-owner"
        assert out.principal_id == OWNER
        assert out.backend_alias == "backend-a"
        assert out.backend_ref == "port-a"
        assert out.public_model_id == "mock-rem-a"
        assert out.conversation_id == "conv-01"
        assert out.memory_policy == "interactive_candidate"
        assert out.session_id == DEFAULT_SESSION_ID
    finally:
        env.conn.close()


# ── 4: correct SAGE memory retrieval ─────────────────────────────────

def test_04_correct_sage_memory_retrieval(tmp_path):
    from src.interfaces.soul_context_adapter import get_scope_provider

    env = _slice_env(tmp_path, journal_name="sage.db")
    try:
        provider = get_scope_provider(env.root, "rem", session_id="s04")
        _seed_fact(provider, subject="Bry", predicate="喜歡",
                   obj_text="紅茶配藍莓司康",
                   source_pair="bryan:agent_rem")
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         text="Bry 喜歡什麼紅茶", rid="req-sage-01"),
            data_root=env.root, session_id="s04")
        assert out.kind == "NEW"
        assert out.memory_context_chars > 0
        assert "紅茶" in env.backends["port-a"].blob()
    finally:
        env.conn.close()


# ── 5: private history isolation ─────────────────────────────────────

def test_05_private_history_isolation(tmp_path):
    marker = "rem-private-history-marker-xyz"
    seeds = {"agent_rem": [(f"session_{OWNER_USER_ID}_agent_rem",
                            "assistant", marker)]}
    env = _slice_env(tmp_path, journal_name="hist.db")
    try:
        factory = _seeding_factory(seeds)
        out_rem = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-h-rem"),
            data_root=env.root, memory_store_factory=factory)
        assert out_rem.kind == "NEW"
        assert marker in env.backends["port-a"].blob()
        out_ram = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "ram", "backend-a", "mock-ram-a",
                         text="另一段對話", conv="conv-ram",
                         key="key-ram", rid="req-h-ram"),
            data_root=env.root, memory_store_factory=factory)
        assert out_ram.kind == "NEW"
        ram_blob = env.backends["port-a"].blob()
        assert marker not in ram_blob
    finally:
        env.conn.close()


# ── 6: backend A/B model selection, identity stable ──────────────────

def test_06_backend_ab_model_selection_identity_stable(tmp_path):
    env = _slice_env(tmp_path, journal_name="ab.db")
    try:
        out_a = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         key="key-a", rid="req-ab-a"),
            data_root=env.root)
        out_b = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-b", "mock-rem-b",
                         key="key-b", rid="req-ab-b"),
            data_root=env.root)
        assert (out_a.kind, out_b.kind) == ("NEW", "NEW")
        ba, bb = env.backends["port-a"], env.backends["port-b"]
        assert (ba.call_count, bb.call_count) == (1, 1)
        assert ba.last_model == "mock-rem-a"
        assert bb.last_model == "mock-rem-b"
        # Same soul: persona identity, memory scope, history ownership.
        assert out_a.agent_id == out_b.agent_id == "agent_rem"
        assert out_a.user_id == out_b.user_id == OWNER_USER_ID
        assert (ba.last_messages[0]["content"] ==
                bb.last_messages[0]["content"])
        mem_file = env.root / "memory" / "agent_rem.db"
        assert mem_file.is_file()
        assert len(list((env.root / "memory").glob("*.db"))) == 1
    finally:
        env.conn.close()


# ── 7: unauthorized refused, no journal write ────────────────────────

def test_07_unauthorized_refused_no_journal(tmp_path):
    env = _slice_env(tmp_path, journal_name="auth.db")
    try:
        with pytest.raises(InterfaceError) as ei:
            handle_text_request(
                driver=env.driver, registry=env.registry,
                journal=env.journal, clock=env.clock,
                backends=env.backends,
                request=_req("wrong-token-xyz", "rem", "backend-a",
                             "mock-rem-a", rid="req-noauth"),
                data_root=env.root)
        assert ei.value.status == 401
        assert _rows(env.conn) == []
        assert env.backends["port-a"].call_count == 0
    finally:
        env.conn.close()


# ── 8: cross-soul route forgery refused ──────────────────────────────

def test_08_cross_soul_route_forgery_refused(tmp_path):
    # (a) token restricted to rem/* asking for a ram route -> 403.
    env = _slice_env(tmp_path, registry=make_restricted_registry(),
                     journal_name="forge-a.db")
    try:
        with pytest.raises(InterfaceError) as ei:
            handle_text_request(
                driver=env.driver, registry=env.registry,
                journal=env.journal, clock=env.clock,
                backends=env.backends,
                request=_req(TOKEN_OWNER, "ram", "backend-a",
                             "mock-ram-a", rid="req-forge-a"),
                data_root=env.root)
        assert ei.value.status == 403
        assert _rows(env.conn) == []
    finally:
        env.conn.close()
    # (b) poisoned registry: rem route pointing at agent_ram -> 403.
    env2 = _slice_env(tmp_path, registry=make_poisoned_registry(),
                      journal_name="forge-b.db")
    try:
        with pytest.raises(InterfaceError) as ei2:
            handle_text_request(
                driver=env2.driver, registry=env2.registry,
                journal=env2.journal, clock=env2.clock,
                backends=env2.backends,
                request=_req(TOKEN_OWNER, "rem", "backend-a",
                             "mock-rem-a", rid="req-forge-b"),
                data_root=env2.root)
        assert ei2.value.status == 403
        assert ei2.value.code == "route_forgery"
        assert _rows(env2.conn) == []
        assert env2.backends["port-a"].call_count == 0
    finally:
        env2.conn.close()


# ── 9: cross-user memory refused ─────────────────────────────────────

def test_09_cross_user_memory_refused(tmp_path):
    env = _slice_env(tmp_path, journal_name="user.db")
    try:
        with pytest.raises(InterfaceError) as ei:
            handle_text_request(
                driver=env.driver, registry=env.registry,
                journal=env.journal, clock=env.clock,
                backends=env.backends,
                request=_req(TOKEN_OWNER, "rem", "backend-a",
                             "mock-rem-a", rid="req-user-x"),
                data_root=env.root, owner_user_id="mallory")
        assert ei.value.status == 403
        assert ei.value.code == "user_binding"
        assert _rows(env.conn) == []
        assert env.backends["port-a"].call_count == 0
    finally:
        env.conn.close()


# ── 10: utility / ephemeral memory policy refused ────────────────────

def test_10_utility_ephemeral_policy_refused(tmp_path):
    # Utility purpose -> UTILITY_NO_MEMORY -> refused.
    env = _slice_env(tmp_path, registry=make_utility_registry(),
                     journal_name="util.db")
    try:
        with pytest.raises(InterfaceError) as ei:
            handle_text_request(
                driver=env.driver, registry=env.registry,
                journal=env.journal, clock=env.clock,
                backends=env.backends,
                request=_req(TOKEN_UTIL, "rem", "backend-a",
                             "mock-rem-a", rid="req-util"),
                data_root=env.root)
        assert ei.value.status == 403
        assert _rows(env.conn) == []
    finally:
        env.conn.close()
    # Ephemeral (idempotency headers missing) -> refused.
    env2 = _slice_env(tmp_path, journal_name="eph.db")
    try:
        bare = RealSliceRequest(
            bearer_token=TOKEN_OWNER, soul_alias="rem",
            backend_alias="backend-a",
            raw_request={"model": "mock-rem-a",
                         "messages": [{"role": "user",
                                       "content": "hi"}]},
            headers={}, request_id="req-eph")
        with pytest.raises(InterfaceError) as ei2:
            handle_text_request(
                driver=env2.driver, registry=env2.registry,
                journal=env2.journal, clock=env2.clock,
                backends=env2.backends, request=bare,
                data_root=env2.root)
        assert ei2.value.status == 403
        assert _rows(env2.conn) == []
        assert env2.backends["port-a"].call_count == 0
    finally:
        env2.conn.close()


# ── 11/12: NEW once, REPLAY rebuilds nothing ─────────────────────────

def test_11_12_new_once_replay_no_rebuild(tmp_path):
    env = _slice_env(tmp_path, journal_name="idem.db")
    try:
        first = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-idem-1"),
            data_root=env.root)
        assert first.kind == "NEW"
        second = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-idem-2"),
            data_root=env.root)
        assert second.kind == "REPLAY"
        # Same stored identity and body; exactly one generation.
        assert second.request_id == first.request_id == "req-idem-1"
        assert second.response_id == first.response_id
        assert second.response_body == first.response_body
        assert env.backends["port-a"].call_count == 1
        # REPLAY performed no rebuild: no fresh evidence attached.
        assert second.persona_chars == 0
        assert second.isolation is None
        assert len(_rows(env.conn)) == 1
    finally:
        env.conn.close()


# ── 13: CONFLICT generates nothing ───────────────────────────────────

def test_13_conflict_no_generation(tmp_path):
    env = _slice_env(tmp_path, journal_name="conf.db")
    try:
        first = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         text="第一版文字", rid="req-conf-1"),
            data_root=env.root)
        assert first.kind == "NEW"
        clash = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         text="第二版文字不同指紋", rid="req-conf-2"),
            data_root=env.root)
        assert clash.kind == "CONFLICT"
        assert clash.response_body is None
        assert env.backends["port-a"].call_count == 1
    finally:
        env.conn.close()


# ── 14: BUSY (journal IN_PROGRESS) generates nothing ─────────────────

def test_14_busy_in_progress_no_generation(tmp_path):
    env = _slice_env(tmp_path, journal_name="busy.db")
    try:
        probe = _req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                     rid="req-busy-1")
        decision = admit(env.registry, probe.bearer_token,
                         probe.soul_alias, probe.backend_alias,
                         probe.raw_request, probe.headers,
                         probe.request_id)
        ns = idempotency_namespace(decision.context)
        fp = request_fingerprint(decision)
        claimed = env.journal.claim(ns, fp, "req-busy-1", NOW)
        assert claimed.kind == "NEW"
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends, request=probe,
            data_root=env.root)
        # Ticket BUSY == journal IN_PROGRESS (live accepted row).
        assert out.kind == "IN_PROGRESS"
        assert out.response_body is None
        assert env.backends["port-a"].call_count == 0
        assert env.backends["port-b"].call_count == 0
    finally:
        env.conn.close()


# ── 15/16: UNCERTAIN fail-closed + generation error surfaced ─────────

def test_15_16_uncertain_fail_closed_error_surfaced(tmp_path):
    ports = {"port-a": FailingBackend("A", "never"),
             "port-b": MockBackend("B", "mock-reply-B")}
    env = _slice_env(tmp_path, backends=ports,
                     journal_name="unc.db")
    try:
        first = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-unc-1"),
            data_root=env.root)
        assert first.kind == "UNCERTAIN"
        # The generation error is surfaced, not hidden as success.
        assert first.error_note is not None
        assert "mock-boom-generation-failure" in first.error_note
        assert first.response_body is None
        rows = _rows(env.conn)
        assert len(rows) == 1
        assert rows[0][1] == "uncertain"
        # Same-key retry: sticky UNCERTAIN, no second generation.
        retry = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-unc-2"),
            data_root=env.root)
        assert retry.kind == "UNCERTAIN"
        # Fresh-key retry with identical content: the fingerprint covers
        # the idempotency key, so a fresh key is a new journal demand --
        # it re-executes once, then fails UNCERTAIN again (journal-correct:
        # exactly one generation per demand, no auto-retry beyond that).
        fresh = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         key="key-fresh", rid="req-unc-3"),
            data_root=env.root)
        assert fresh.kind == "UNCERTAIN"
        assert ports["port-a"].call_count == 2
        assert len(_rows(env.conn)) == 2
    finally:
        env.conn.close()


# ── 17: no memory writeback ──────────────────────────────────────────

def test_17_no_memory_writeback(tmp_path):
    import json as _json

    counter = []
    env = _slice_env(tmp_path, journal_name="nowb.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-nowb"),
            data_root=env.root,
            memory_store_factory=_counting_factory(counter))
        assert out.kind == "NEW"
        # No append path was ever taken (obtaining context is not
        # persistence), and the journal agrees: memory stays pending.
        assert counter == []
        rows = _rows(env.conn)
        assert _json.loads(rows[0][4]) == {"memory": "pending"}
        # Structural proof: the slice never calls a persistence API.
        import src.interfaces.real_context_slice as _slice

        tree = ast.parse(Path(_slice.__file__).read_text(
            encoding="utf-8"))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    called.add(fn.attr)
        for banned in ("append", "append_batch", "add_fact",
                       "record_outcome", "record_generated",
                       "record_transport"):
            assert banned not in called, banned
    finally:
        env.conn.close()


# ── 18: no channel broadcast ─────────────────────────────────────────

def test_18_no_channel_broadcast(tmp_path):
    import src.interfaces.real_context_slice as _slice

    text = Path(_slice.__file__).read_text(encoding="utf-8").lower()
    for token in ("gateway", "telegram", "voice", "websocket", "tts",
                  "publish", "broadcast", "speak(", "channel",
                  "eventbus", "event_bus", "agency", "consciousness"):
        assert token not in text, f"slice must not reference {token}"
    # Runtime proof (order-independent): snapshot sys.modules, run a
    # genuine NEW request, require the import DELTA to contain no
    # messaging/channel modules.
    before = set(sys.modules)
    env = _slice_env(tmp_path, journal_name="chan.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-chan"),
            data_root=env.root)
        assert out.kind == "NEW"
    finally:
        env.conn.close()
    delta = {m.lower() for m in set(sys.modules) - before}
    hits = [m for m in delta
            if any(k in m for k in ("gateway", "telegram", "voice",
                                    "channel", "eventbus", "agency"))]
    assert hits == [], hits


# ── 19: no production IO ─────────────────────────────────────────────

def test_19_no_production_io(tmp_path):
    from src.paths import production_data_root

    prod = production_data_root().resolve()  # path only, no IO
    threads_before = len(threading.enumerate())
    env = _slice_env(tmp_path, journal_name="prod.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "ram", "backend-a", "mock-ram-a",
                         rid="req-prod"),
            data_root=env.root)
        assert out.kind == "NEW"
        rep = out.isolation
        assert rep is not None
        for label, p in (
                ("current_data_root", rep.current_data_root),
                ("launch_data_root", rep.launch_data_root),
                ("module_conv_dir", rep.module_conv_dir),
                ("module_inner_life_dir", rep.module_inner_life_dir),
                ("relationship_dir", rep.relationship_dir)):
            resolved = Path(p).resolve()
            assert resolved != prod, label
            assert prod not in resolved.parents, label
        assert Path(rep.production_data_root).resolve() == prod
        # Everything the request created stays under the temp root.
        for p in (env.root / "journal" / "prod.db",
                  env.root / "memory" / "agent_ram.db",
                  Path(rep.relationship_dir)):
            assert env.root.resolve() in p.resolve().parents, p
    finally:
        env.conn.close()
    assert len(threading.enumerate()) == threads_before, \
        "background thread spawned"


# ── 20: no global singleton pollution ────────────────────────────────

def test_20_no_global_singleton_pollution(tmp_path):
    from src.soul import relationships as _rel

    import src.interfaces.soul_context_adapter as _adapter

    with _rel._manager_lock:
        assert _rel._manager_singleton is None
    env = _slice_env(tmp_path, journal_name="pol.db")
    try:
        out = handle_text_request(
            driver=env.driver, registry=env.registry,
            journal=env.journal, clock=env.clock,
            backends=env.backends,
            request=_req(TOKEN_OWNER, "rem", "backend-a", "mock-rem-a",
                         rid="req-pol"),
            data_root=env.root)
        assert out.kind == "NEW"
    finally:
        env.conn.close()
    # Relationships singleton restored (save/restore in the build).
    with _rel._manager_lock:
        assert _rel._manager_singleton is None
    # SAGE provider cache keys all nest under isolated roots, never
    # production; scopes stay independent instances.
    from src.paths import production_data_root

    prod = production_data_root().resolve()
    for root_str, scope in _adapter._SCOPE_PROVIDERS.keys():
        rp = Path(root_str).resolve()
        assert rp != prod and prod not in rp.parents
        assert scope in ("rem", "ram")
    # No global LLM backend mutation (same shape as adapter test_13).
    import src.interfaces.real_context_slice as _slice

    src_text = Path(_slice.__file__).read_text(encoding="utf-8")
    assert "set_llm_proxy" not in src_text
    assert "queue_prefetch" not in src_text
    import src.memory.sage.writer as _writer

    assert _writer.__dict__.get("_soul_os_llm_proxy") is None


# ── 21: running-loop bridge refuses fail-closed ──────────────────────

def test_21_running_loop_bridge_fail_closed(tmp_path):
    env = _slice_env(tmp_path, journal_name="loop.db")
    try:
        async def _main():
            return handle_text_request(
                driver=env.driver, registry=env.registry,
                journal=env.journal, clock=env.clock,
                backends=env.backends,
                request=_req(TOKEN_OWNER, "rem", "backend-a",
                             "mock-rem-a", rid="req-loop"),
                data_root=env.root)

        out = asyncio.run(_main())
        # No nested loop workaround: fail-closed UNCERTAIN, backend
        # untouched, journal row honestly uncertain.
        assert out.kind == "UNCERTAIN"
        assert out.error_note is not None
        assert "event loop" in out.error_note
        assert env.backends["port-a"].call_count == 0
        rows = _rows(env.conn)
        assert len(rows) == 1 and rows[0][1] == "uncertain"
    finally:
        env.conn.close()
