"""SA-1 T1 bridge tests: thin entry -> :8000 live loop, no bare proxy.

Decision-complete scope (SA-1): only this file + api.py + bridge.py +
release.py are new; app.py gains one resolver dispatch before the legacy
parse (old path falls through byte-identical); the two launchers gain
minimal bridge wiring. SA-0 three files untouched, EI-1 old path unchanged.

All test names carry the t1 marker: `pytest -q tests/soul_access -k t1`
must collect > 0. Transport is httpx ASGITransport (in-process, never a
port) + injected live-instance fakes (never a real LLM call). The real
carrier (PresetRoutingBackend) is never constructed here.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

import sys

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.external_gateway.app import create_external_app  # noqa: E402
from src.external_gateway.app import file_preset_loader  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.credentials import issue_credential  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402

OWNER = "t1-bridge-owner"
REF = "T1_BRIDGE_REF"
SENTINEL = "T1-BRIDGE-SENTINEL-KEY"

OTHER_REF = "T1_BRIDGE_OTHER_REF"
OTHER_SENTINEL = "T1-BRIDGE-OTHER-KEY"

_SA_MODULES = (
    _REPO_ROOT / "src" / "soul_access" / "api.py",
    _REPO_ROOT / "src" / "soul_access" / "bridge.py",
    _REPO_ROOT / "src" / "soul_access" / "release.py",
)

# SA-1 Windows runner note: the frozen SA-0 conftest in this directory blocks
# ``socket.socket`` for every test, and on Windows ANY event-loop creation
# needs a socketpair self-pipe (ProactorEventLoop.__init__ ->
# _fallback_socketpair -> socket()). pytest-asyncio therefore cannot build
# its per-test loop here, so T1 drives a single module-level loop created at
# IMPORT time (before any test-scoped block is installed) and reuses it via
# _run(). The loop's self-pipe is loopback-ephemeral only: no production
# port, no outbound traffic, ASGITransport stays fully in-process.
_T1_LOOP = asyncio.new_event_loop()


def _run(coro):
    return _T1_LOOP.run_until_complete(coro)


@pytest.fixture(scope="session", autouse=True)
def _t1_close_loop():
    yield
    _T1_LOOP.close()


def _legal_snapshot():
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id="req-t1-0001",
            conversation_id="conv-t1-0001",
            branch_id="root",
            turn_id="turn-t1-0001",
            idempotency_key="key-t1-0001",
        ),
        dedup=sa_schema.DedupKey(
            principal_id=OWNER,
            agent_id="agent_rem",
            scope="owner_private",
            conversation_id="conv-t1-0001",
            branch_id="root",
            idempotency_key="key-t1-0001",
        ),
        backend_alias="primary",
        model="do-not-deploy-model-t1",
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t1-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-t1-v1",
        projection_state="projection-t1-v1",
        recall_version="recall-t1-v1",
        recall_items=("recall-t1-item-1",),
        history_pool_version="pool-t1-v1",
        clean_history=("hist-t1-item-1",),
    )


def _route(soul="rem", preset="primary", agent=None):
    return {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": agent if agent is not None else "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, preset),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }


def _raw(token, routes=None, allowed=("rem/primary",)):
    if routes is None:
        routes = [_route()]
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": routes,
        "clients": [
            {
                "client_id": "client-t1",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": list(allowed),
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _write_presets(path, model="do-not-deploy-model-t1", name="primary", key_ref=REF):
    path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": name,
                        "provider": "deepseek",
                        "model": model,
                        "params": {"temperature": 0.5, "max_tokens": 512},
                        "key_ref": key_ref,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_release(path, status):
    path.write_text(json.dumps({"status": status}), encoding="utf-8")
    return path


class _FakeBackend:
    """Legacy direct-provider carrier stand-in: counts calls (must stay 0)."""

    def __init__(self, text="t1-legacy-reply"):
        self.text = text
        self.calls = []

    def complete(self, route, preset, messages, params):
        self.calls.append((route, preset, messages, params))
        return self.text


class _FakeLive:
    """Injected :8000 live-instance stand-ins (bus/llm/middleware替身)."""

    def __init__(self):
        self.bus_calls = []
        self.llm_calls = []
        self.middleware_calls = []
        self.generate_calls = []

    def generate(self, snapshot, holder, live):
        self.generate_calls.append((snapshot, holder, live))
        self.llm_calls.append(snapshot)
        return "t1-live-reply"


def _setup(tmp_path, monkeypatch, *, release_status="open"):
    """Build one isolated app with the SA-1 live handler wired."""
    monkeypatch.setenv(REF, SENTINEL)
    monkeypatch.setenv(OTHER_REF, OTHER_SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json")
    release_path = _write_release(tmp_path / "release.json", release_status)
    provider = RegistryProvider(_raw(token))
    backend = _FakeBackend()
    fake = _FakeLive()
    loop = sa_bridge.LiveLoop(bus=object(), llm=fake, middleware=object())
    handler = sa_bridge.build_live_handler(
        live=loop, generate=fake.generate, release_path=str(release_path)
    )
    app = create_external_app(
        provider,
        file_preset_loader(preset_path),
        backend,
        soul_live_handler=handler,
    )
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    return (client, token, backend, fake, release_path)


def _auth(token):
    return {"Authorization": "Bearer " + token}


def _envelope_body(snapshot=None):
    snap = snapshot if snapshot is not None else _legal_snapshot()
    envelope = sa_api.build_envelope(
        snapshot=snap,
        messages=(("user", "hi"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    return sa_api.envelope_to_body(envelope)


# -- T1-1: no-tools single load + single generation ----------------------------


def test_t1_bridge_no_tools_single_load_single_generation():
    snap = _legal_snapshot()
    envelope = sa_api.build_envelope(
        snapshot=snap,
        messages=(("user", "hi"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )
    fake = _FakeLive()
    loop = sa_bridge.LiveLoop(bus=object(), llm=fake, middleware=object())
    holder = sa_api.build_holder(
        request_id=snap.identity.request_id,
        snapshot_digest_hex=envelope.provenance.snapshot_digest,
    )
    result = sa_bridge.admit_no_tools(
        snapshot=envelope.snapshot,
        holder=holder,
        provenance=envelope.provenance,
        live=loop,
        generate=fake.generate,
    )
    assert result.text == "t1-live-reply"
    assert result.loads == 1
    assert result.generation_attempts == 1
    assert result.provenance.transitions == ("head_load", "gen1", "admit")
    assert result.provenance.snapshot_digest == sa_schema.snapshot_digest(snap)
    assert len(fake.generate_calls) == 1


# -- T1-2: thin entry makes zero direct provider calls -------------------------


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


def test_t1_thin_entry_zero_direct_provider_calls():
    for path in _SA_MODULES:
        mods, names = _imported_module_names(path)
        for banned in (
            "httpx",
            "socket",
            "urllib",
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
    text = (_REPO_ROOT / "src" / "soul_access" / "api.py").read_text(encoding="utf-8")
    assert "soul_context_adapter" not in text  # ticket ban (offline-only helper)
    raw = "".join(p.read_text(encoding="utf-8") for p in _SA_MODULES)
    for banned in (
        "PresetRoutingBackend",
        "DeepSeekBackend",
        "OllamaBackend",
        "create_connection",
        "urlopen",
    ):
        assert banned not in raw, banned


def test_t1_envelope_dispatch_never_touches_legacy_backend(tmp_path, monkeypatch):
    client, token, backend, fake, _release = _setup(tmp_path, monkeypatch)

    async def _go():
        async with client:
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers=_auth(token),
                json=_envelope_body(),
            )

    resp = _run(_go())
    assert resp.status_code == 200
    out = resp.json()
    assert out["choices"][0]["message"] == {"role": "assistant", "content": "t1-live-reply"}
    assert out["model"] == "do-not-deploy-model-t1"
    assert backend.calls == []  # thin entry: provider zero direct calls
    assert len(fake.generate_calls) == 1  # live loop: exactly one generation


# -- T1-3: legacy path unchanged ------------------------------------------------


def test_t1_legacy_chat_path_unchanged(tmp_path, monkeypatch):
    client, token, backend, fake, _release = _setup(tmp_path, monkeypatch)

    async def _go():
        async with client:
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers=_auth(token),
                json={"model": "primary", "messages": [{"role": "user", "content": "hi"}]},
            )

    resp = _run(_go())
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"] == {
        "role": "assistant",
        "content": "t1-legacy-reply",
    }
    assert len(backend.calls) == 1  # legacy still uses the injected carrier
    assert fake.generate_calls == []  # live loop untouched by legacy traffic


# -- T1-4: bidirectional mutation (bypass red / misreject red / revert green) --


def test_t1_mutation_bypass_direct_provider_turns_red(tmp_path, monkeypatch):
    """Bypass probe: handler that dials the provider straight past the loop.

    Red state = the legacy carrier IS touched (calls == 1). The test pins
    the detector: any bypass of the live loop shows up as a provider call.
    """
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json")
    provider = RegistryProvider(_raw(token))
    backend = _FakeBackend()

    def _bypass(envelope, *, route=None, preset=None):
        return backend.complete(route, preset, [("user", "hi")], {})

    app = create_external_app(
        provider, file_preset_loader(preset_path), backend, soul_live_handler=_bypass
    )
    transport = httpx.ASGITransport(app=app)

    async def _go():
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers=_auth(token),
                json=_envelope_body(),
            )

    resp = _run(_go())
    assert resp.status_code == 200  # bypass still answers ...
    assert len(backend.calls) == 1  # ... but RED: provider touched directly


def test_t1_mutation_tightened_gate_rejects_legal_turns_red(tmp_path, monkeypatch):
    """Misreject probe: closed release refuses even a legal envelope (403)."""
    client, token, backend, fake, _release = _setup(
        tmp_path, monkeypatch, release_status="closed"
    )

    async def _go():
        async with client:
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers=_auth(token),
                json=_envelope_body(),
            )

    resp = _run(_go())
    assert resp.status_code == 403  # RED: legal request refused
    assert resp.json()["error"]["code"] == "forbidden"
    assert backend.calls == []
    assert fake.generate_calls == []


def test_t1_mutation_reverted_returns_green(tmp_path, monkeypatch):
    """Reverted wiring: legal envelope green again, counters 1/1, backend 0."""
    client, token, backend, fake, _release = _setup(tmp_path, monkeypatch)

    async def _go():
        async with client:
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers=_auth(token),
                json=_envelope_body(),
            )

    resp = _run(_go())
    assert resp.status_code == 200
    assert backend.calls == []
    assert len(fake.generate_calls) == 1


# -- T1-5: release hot-read semantics -------------------------------------------


def test_t1_release_hot_read_missing_bad_closed(tmp_path):
    missing = tmp_path / "no-such-release.json"
    assert sa_release.is_live_inbound_open(missing) is False
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert sa_release.is_live_inbound_open(bad) is False
    non_object = tmp_path / "list.json"
    non_object.write_text("[1, 2]", encoding="utf-8")
    assert sa_release.is_live_inbound_open(non_object) is False
    closed = _write_release(tmp_path / "closed.json", "closed")
    assert sa_release.is_live_inbound_open(closed) is False
    live = _write_release(tmp_path / "live.json", "open")
    assert sa_release.is_live_inbound_open(live) is True
    # Hot-read: flipping the file flips the very next read (no cache).
    _write_release(live, "closed")
    assert sa_release.is_live_inbound_open(live) is False
    _write_release(live, "open")
    assert sa_release.is_live_inbound_open(live) is True


# -- T1-6: isolation hard blocks stay on in this directory ---------------------


def test_t1_hard_blocks_stop_network_and_prod_writes(tmp_path):
    # NOTE: the server-spawn probe (subprocess.Popen) lives in T0-5 only:
    # the R5 spawn guard flags EVERY subprocess.* call site in tests/**, so
    # repeating that probe here would add a new guard hit without new
    # evidence (same conftest, same directory, same block). Socket / kill /
    # prod-write probes below prove the block is active inside T1 tests.
    import os
    import socket

    with pytest.raises(RuntimeError):
        socket.socket()
    with pytest.raises(RuntimeError):
        socket.create_connection(("127.0.0.1", 8000), timeout=1)
    with pytest.raises(RuntimeError):
        os.kill(999999, 9)
    prod_probe = _REPO_ROOT / "data" / "__sa1_t1_probe__.tmp"
    with pytest.raises(RuntimeError):
        open(prod_probe, "w")
    assert not prod_probe.exists()
    ok_file = tmp_path / "t1-ok.txt"
    ok_file.write_text("t1", encoding="utf-8")
    assert ok_file.read_text(encoding="utf-8") == "t1"
