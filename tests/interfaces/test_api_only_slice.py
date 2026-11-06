"""EI-2B API-only isolated vertical slice (synthetic, offline).

Scope: in-process harness driver only. No HTTP server, no Gateway /
Telegram / Voice / Consciousness / SAGE / EventBus imports, no real LLM,
no production data. Persona excerpts are read HERE (test-side only) and
injected as fixtures; the adapter itself performs zero IO.
"""

import ast
import hashlib
import sqlite3
from pathlib import Path

import pytest

from src.interfaces.config import validate_registry
from src.interfaces.journal import Journal
from src.interfaces.runtime_port import FakeClock
from src.interfaces.schema import InterfaceError
from src.interfaces.slice_adapter import SliceDriver, SliceRequest, handle_text_request
from src.interfaces.slice_stub import SoulFixture, StubBackend, SyntheticSoulStore

OWNER = "owner-01"
TOKEN_FULL = "slice-token-full-001-key"
TOKEN_REM = "slice-token-rem-only-002-key"

REPO_ROOT = Path(__file__).resolve().parents[2]


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _persona_excerpt(name: str, limit: int = 600):
    p = REPO_ROOT / "personas" / name
    text = p.read_text(encoding="utf-8")
    excerpt = text[:limit]
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return excerpt, "personas/%s#sha256:%s" % (name, sha[:16]), sha


def make_slice_registry():
    routes = [
        {"soul_alias": "rem", "backend_alias": "backend-a", "agent_id": "agent-rem",
         "public_model_id": "model-rem-a", "backend_ref": "port-a", "context_window_tokens": 4096},
        {"soul_alias": "rem", "backend_alias": "backend-b", "agent_id": "agent-rem",
         "public_model_id": "model-rem-b", "backend_ref": "port-b", "context_window_tokens": 4096},
        {"soul_alias": "ram", "backend_alias": "backend-a", "agent_id": "agent-ram",
         "public_model_id": "model-ram-a", "backend_ref": "port-a", "context_window_tokens": 4096},
    ]
    clients = [
        {"client_id": "client-full", "principal_id": OWNER, "purpose": "interactive",
         "allowed_routes": ["rem/backend-a", "ram/backend-a"], "scope": "owner_private",
         "allow_persistent_memory": True, "credential_sha256": _digest(TOKEN_FULL)},
        {"client_id": "client-rem", "principal_id": OWNER, "purpose": "interactive",
         "allowed_routes": ["rem/backend-a", "rem/backend-b"], "scope": "owner_private",
         "allow_persistent_memory": True, "credential_sha256": _digest(TOKEN_REM)},
    ]
    return validate_registry({"owner_principal_id": OWNER, "backends": ["port-a", "port-b"],
                              "routes": routes, "clients": clients})


def make_store():
    rem_excerpt, rem_source, rem_sha = _persona_excerpt("agent_rem.md")
    ram_excerpt, ram_source, ram_sha = _persona_excerpt("agent_ram.md")
    store = SyntheticSoulStore()
    store.add(SoulFixture(agent_id="agent-rem", persona_excerpt=rem_excerpt,
                          persona_source=rem_source, persona_sha256=rem_sha,
                          facts=("rem-fact-alpha", "rem-fact-beta")))
    store.add(SoulFixture(agent_id="agent-ram", persona_excerpt=ram_excerpt,
                          persona_source=ram_source, persona_sha256=ram_sha,
                          facts=("ram-fact-gamma",)))
    return store, {"agent-rem": (rem_source, rem_sha), "agent-ram": (ram_source, ram_sha)}


def make_env():
    registry = make_slice_registry()
    store, prov = make_store()
    conn = sqlite3.connect(":memory:")
    journal = Journal(conn, epoch=1)
    clock = FakeClock(now_s=1_700_000_000.0, epoch=1)
    stub = StubBackend(authorized_backend_refs=("port-a", "port-b"))
    driver = SliceDriver()
    return registry, store, prov, journal, clock, stub, driver, conn


def _body(model, text="hello slice"):
    return {"model": model, "messages": [{"role": "user", "content": text}]}


def _headers(conv="conv-s1", branch="branch-s1", turn="turn-s1", key="key-s1"):
    return {"X-Soul-Conversation-Id": conv, "X-Soul-Branch-Id": branch,
            "X-Soul-Turn-Id": turn, "Idempotency-Key": key}


def _req(token, soul, backend, raw, headers, rid):
    return SliceRequest(bearer_token=token, soul_alias=soul, backend_alias=backend,
                        raw_request=raw, headers=dict(headers), request_id=rid)


def _row_count(conn):
    return conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]


# ---- happy paths: identity + routing + provenance ----

def test_rem_harness_text_new_binds_identity_and_provenance():
    registry, store, prov, journal, clock, stub, driver, _ = make_env()
    req = _req(TOKEN_FULL, "rem", "backend-a", _body("model-rem-a", "hello rem"),
               _headers(), "req-rem-001")
    resp = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                               soul_store=store, stub_backend=stub, request=req)
    assert resp.kind == "NEW"
    assert resp.soul_alias == "rem" and resp.agent_id == "agent-rem"
    assert resp.backend_alias == "backend-a" and resp.backend_ref == "port-a"
    assert resp.public_model_id == "model-rem-a"
    # five IDs distinct and bound server-side
    assert resp.soul_alias != resp.client_id != resp.request_id
    assert resp.conversation_id == "conv-s1"
    assert resp.backend_ref == "port-a"
    assert resp.persona_sha256 == prov["agent-rem"][1]
    assert resp.persona_source == prov["agent-rem"][0]
    assert resp.memory_policy == "interactive_candidate"
    assert resp.memory_evidence == "syn-mem:agent-rem:2"
    assert len(stub.calls) == 1 and stub.calls[0]["agent_id"] == "agent-rem"
    assert stub.calls[0]["fact_count"] == 2


def test_ram_second_persona_isolated_facts():
    registry, store, prov, journal, clock, stub, driver, _ = make_env()
    req = _req(TOKEN_FULL, "ram", "backend-a", _body("model-ram-a", "hello ram"),
               _headers(conv="conv-ram-1", key="key-ram-1"), "req-ram-001")
    resp = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                               soul_store=store, stub_backend=stub, request=req)
    assert resp.kind == "NEW" and resp.agent_id == "agent-ram"
    assert resp.persona_sha256 == prov["agent-ram"][1]
    assert resp.persona_sha256 != prov["agent-rem"][1]
    assert resp.memory_evidence == "syn-mem:agent-ram:1"
    assert stub.calls[0]["agent_id"] == "agent-ram"


def test_model_switch_keeps_soul_identity():
    registry, store, prov, journal, clock, stub, driver, _ = make_env()
    r1 = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                             soul_store=store, stub_backend=stub,
                             request=_req(TOKEN_REM, "rem", "backend-a",
                                          _body("model-rem-a", "same soul a"), _headers(key="k-a"),
                                          "req-sw-001"))
    r2 = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                             soul_store=store, stub_backend=stub,
                             request=_req(TOKEN_REM, "rem", "backend-b",
                                          _body("model-rem-b", "same soul b"),
                                          _headers(key="k-b"), "req-sw-002"))
    assert r1.agent_id == r2.agent_id == "agent-rem"
    assert r1.public_model_id == "model-rem-a" and r2.public_model_id == "model-rem-b"
    assert r1.persona_sha256 == r2.persona_sha256


def test_cross_soul_no_crosstalk_via_stub_log():
    registry, store, prov, journal, clock, stub, driver, _ = make_env()
    ra = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                             soul_store=store, stub_backend=stub,
                             request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "ping rem"),
                                          _headers(conv="c-rem", key="k-rem"), "req-x-001"))
    rb = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                             soul_store=store, stub_backend=stub,
                             request=_req(TOKEN_FULL, "ram", "backend-a",
                                          _body("model-ram-a", "ping ram"),
                                          _headers(conv="c-ram", key="k-ram"), "req-x-002"))
    assert [c["agent_id"] for c in stub.calls] == ["agent-rem", "agent-ram"]
    assert prov["agent-rem"][1][:12] in ra.response_body
    assert prov["agent-ram"][1][:12] in rb.response_body
    assert prov["agent-rem"][1][:12] not in rb.response_body


# ---- negative: auth / route / model ----

def test_forgery_token_cannot_reach_ungranted_soul():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    with pytest.raises(InterfaceError) as ei:
        handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                            soul_store=store, stub_backend=stub,
                            request=_req(TOKEN_REM, "ram", "backend-a",
                                         _body("model-ram-a", "forge"), _headers(), "req-forge-001"))
    assert ei.value.status == 403 and ei.value.code == "forbidden"
    assert len(stub.calls) == 0


def test_unauthorized_model_mismatch_rejected():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    with pytest.raises(InterfaceError) as ei:
        handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                            soul_store=store, stub_backend=stub,
                            request=_req(TOKEN_FULL, "rem", "backend-a",
                                         _body("model-ram-a", "wrong model"), _headers(),
                                         "req-model-001"))
    assert ei.value.status == 400 and ei.value.code == "model_mismatch"


def test_unknown_route_404():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    with pytest.raises(InterfaceError) as ei:
        handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                            soul_store=store, stub_backend=stub,
                            request=_req(TOKEN_FULL, "rem", "backend-zz",
                                         _body("model-rem-a", "nope"), _headers(), "req-404-001"))
    assert ei.value.status == 404


def test_stream_true_rejected():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    raw = {"model": "model-rem-a", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    with pytest.raises(InterfaceError) as ei:
        handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                            soul_store=store, stub_backend=stub,
                            request=_req(TOKEN_FULL, "rem", "backend-a", raw, _headers(),
                                         "req-stream-001"))
    assert ei.value.status == 400


# ---- lifecycle: replay / conflict / busy / uncertain ----

def test_same_key_retry_replays_without_regeneration():
    registry, store, _, journal, clock, stub, driver, conn = make_env()
    kw = dict(driver=driver, registry=registry, journal=journal, clock=clock,
              soul_store=store, stub_backend=stub)
    r1 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "same text"), _headers(),
                                          "req-replay-001"), **kw)
    r2 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "same text"), _headers(),
                                          "req-replay-001"), **kw)
    assert r1.kind == "NEW" and r2.kind == "REPLAY"
    assert r2.response_id == r1.response_id and r2.response_body == r1.response_body
    assert len(stub.calls) == 1
    assert _row_count(conn) == 1


def test_same_key_different_body_conflicts():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    kw = dict(driver=driver, registry=registry, journal=journal, clock=clock,
              soul_store=store, stub_backend=stub)
    r1 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "text one"), _headers(),
                                          "req-conf-001"), **kw)
    r2 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "text TWO"), _headers(),
                                          "req-conf-001"), **kw)
    assert r1.kind == "NEW" and r2.kind == "CONFLICT"


def test_different_key_in_flight_returns_busy_and_touches_no_journal_row():
    registry, store, _, journal, clock, stub, driver, conn = make_env()
    before = _row_count(conn)
    lease_key = (OWNER, "agent-rem")
    assert driver.leases.try_acquire(lease_key) is True
    try:
        resp = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                                   soul_store=store, stub_backend=stub,
                                   request=_req(TOKEN_FULL, "rem", "backend-a",
                                                _body("model-rem-a", "busy probe"),
                                                _headers(key="key-busy-9"), "req-busy-001"))
        assert resp.kind == "BUSY"
        assert len(stub.calls) == 0
        assert _row_count(conn) == before
    finally:
        driver.leases.release(lease_key)


class _BoomBackend(StubBackend):
    def generate(self, **kw):
        raise RuntimeError("synthetic stub failure")


def test_uncertain_never_reruns_even_under_new_request_id():
    registry, store, _, journal, clock, _, driver, _ = make_env()
    boom = _BoomBackend(authorized_backend_refs=("port-a", "port-b"))
    kw = dict(driver=driver, registry=registry, journal=journal, clock=clock,
              soul_store=store, stub_backend=boom)
    headers = _headers(key="key-unc-1")
    u1 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "fragile"), headers,
                                          "req-unc-001"), **kw)
    assert u1.kind == "UNCERTAIN"
    # Same fingerprint under a NEW request_id must NOT auto re-execute.
    ok_stub = StubBackend(authorized_backend_refs=("port-a", "port-b"))
    kw2 = dict(driver=driver, registry=registry, journal=journal, clock=clock,
               soul_store=store, stub_backend=ok_stub)
    u2 = handle_text_request(request=_req(TOKEN_FULL, "rem", "backend-a",
                                          _body("model-rem-a", "fragile"), headers,
                                          "req-unc-002-NEWKEY"), **kw2)
    assert u2.kind == "UNCERTAIN"
    assert len(ok_stub.calls) == 0


def test_journal_lifecycle_intact_after_new():
    registry, store, _, journal, clock, stub, driver, conn = make_env()
    resp = handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                               soul_store=store, stub_backend=stub,
                               request=_req(TOKEN_FULL, "rem", "backend-a",
                                            _body("model-rem-a", "lifecycle"), _headers(),
                                            "req-life-001"))
    assert resp.kind == "NEW"
    row = conn.execute("SELECT state, response_id, transport_count, outcomes FROM requests "
                       "WHERE request_id=?", ("req-life-001",)).fetchone()
    assert row[0] == "transport_sent" and row[1] == resp.response_id and row[2] == 1
    assert '"memory":"completed"' in row[3]


# ---- boundary: no channels, stdlib-only, honest runtime gap ----

def test_no_channel_dispatch_paths_in_slice_sources():
    banned = {"telegram", "websocket", "tts", "gateway", "consciousness",
              "sage", "eventbus", "event_bus", "llmproxy", "openai", "anthropic"}
    for name in ("src/interfaces/slice_adapter.py", "src/interfaces/slice_stub.py"):
        tree = ast.parse((REPO_ROOT / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert a.name.split(".")[0].lower() not in banned, (name, a.name)
            elif isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0].lower() not in banned, (name, node.module)
            elif isinstance(node, ast.Attribute):
                assert node.attr.lower() not in banned, (name, node.attr)
            elif isinstance(node, ast.Name):
                assert node.id.lower() not in banned, (name, node.id)
    tree_a = ast.parse((REPO_ROOT / "src/interfaces/slice_adapter.py").read_text(encoding="utf-8"))
    tree_s = ast.parse((REPO_ROOT / "src/interfaces/slice_stub.py").read_text(encoding="utf-8"))
    imported = set()
    for tree in (tree_a, tree_s):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add((node.module or "").split(".")[0])
    imported -= {"interfaces", ""}
    allowed = {"__future__", "threading", "contextlib", "dataclasses", "typing",
               "hashlib", "json", "pathlib", "sqlite3", "ast",
               "context_policy", "runtime_port", "schema"}
    assert imported <= allowed | {"src"}, imported


def test_missing_soul_fixture_reports_blocked_boundary():
    registry, store, _, journal, clock, stub, driver, _ = make_env()
    store._fixtures.pop("agent-ram")
    with pytest.raises(InterfaceError) as ei:
        handle_text_request(driver=driver, registry=registry, journal=journal, clock=clock,
                            soul_store=store, stub_backend=stub,
                            request=_req(TOKEN_FULL, "ram", "backend-a",
                                         _body("model-ram-a", "no fixture"), _headers(),
                                         "req-blocked-001"))
    assert ei.value.status == 500 and ei.value.code == "soul_context_blocked"
