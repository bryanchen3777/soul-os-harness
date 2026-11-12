"""SA-LIVE-WIRING-1 T8 live-wiring tests: thin forwarder + :8000 mount + adapter.

Decision-complete scope (SA-LIVE-WIRING-1): this file + ``src/soul_access/
wiring.py`` are new; ``scripts/run_server.py`` (lifespan mount on the SAME
bus/llm/middleware) and ``scripts/serve_external_gateway.py`` (thin side is a
cross-process forwarder holding NO live instances) gain minimal additive
edits. Frozen files (bridge/api/release/schema/台帳/判準) untouched.

What "wired" means here (all four must hold; look-alikes do NOT count):

  1. the SAME bus/llm/middleware objects flow into generate (``is``);
  2. one admitted call = one dialog call + one trace record + pending drained;
  3. the reply lands in the SHARED session file (telegram/voice/sa-live meet
     on the same root + same ``(agent_id, user_bryan)`` key);
  4. the internal route is registered ONLY at the fixed endpoint and
     re-verifies seal / route / identity / release before admitting.

All test names carry the t8 marker: `pytest -q tests/soul_access -k t8`
must collect > 0. Sync only (the SA-0 conftest in this directory hard-blocks
socket creation; NO event loop is created). bus/llm/middleware are fakes;
dialog is a fake bottom-I/O seam (zero real LLM calls, zero cost); the
release file is always a self-created tmp file (never data/**, never the
pinned production path). Zero subprocess call sites in this file (R1/R5
rationale: the spawn probe lives in the T0 shared oracle). The production
:8000 literal lives ONLY in ``src/soul_access/wiring.py`` (outside the
tests/** scan scope); this file references it via ``wiring`` attributes and
``sa_api.FORWARD_ENDPOINT`` (suffix assertions, never a literal).
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from clients.voice_companion.session_store import SessionStore  # noqa: E402
from src.external_gateway.openai_shim import parse_chat_request  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.credentials import issue_credential  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.interfaces.identity import authenticate  # noqa: E402
from src.interfaces.schema import InterfaceError  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

OWNER = "t8-live-wiring-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"

_T8_VERSION = "salw/v1"


def _identity(**over):
    kw = {
        "request_id": "req-t8-0001",
        "conversation_id": "dm",
        "branch_id": "root",
        "turn_id": "turn-t8-0001",
        "idempotency_key": "key-t8-0001",
    }
    kw.update(over)
    return sa_schema.RequestIdentity(**kw)


def _dedup(**over):
    kw = {
        "principal_id": OWNER,
        "agent_id": AGENT,
        "scope": SCOPE,
        "conversation_id": "dm",
        "branch_id": "root",
        "idempotency_key": "key-t8-0001",
    }
    kw.update(over)
    return sa_schema.DedupKey(**kw)


def _snapshot(**over):
    kw = {
        "identity": _identity(),
        "dedup": _dedup(),
        "backend_alias": "primary",
        "model": "do-not-deploy-model-t8",
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-12T00:00:00Z",
        "personality_version": "personality-t8-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-11T17:00:00-05:00",
        "inner_life_state": "inner-t8-v1",
        "projection_state": "projection-t8-v1",
        "recall_version": "recall-t8-v1",
        "recall_items": ("recall-t8-item-1",),
        "history_pool_version": "pool-t8-v1",
        "clean_history": ("hist-t8-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


def _envelope(request_id="req-t8-0001", key="key-t8-0001"):
    snap = _snapshot(
        identity=_identity(request_id=request_id, idempotency_key=key),
        dedup=_dedup(idempotency_key=key),
    )
    return sa_api.build_envelope(
        snapshot=snap,
        messages=(("user", "t8 live wiring probe"),),
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _write_release(path: Path, payload) -> Path:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _CountingDialog:
    """Fake bottom-I/O seam: records calls, returns fixed text, zero LLM."""

    def __init__(self, text="t8-live-reply"):
        self.text = text
        self.calls = []
        self.seen_pending = []

    def __call__(self, messages, *, agent_id=None, request_id=None):
        self.calls.append(
            {
                "messages": tuple(messages),
                "agent_id": agent_id,
                "request_id": request_id,
            }
        )
        return self.text


class _StubApp:
    """Minimal :8000 stand-in: state loop slot + route recorder."""

    def __init__(self, live):
        self.state = SimpleNamespace(_soul_access_live=live)
        self.routes = []

    def add_api_route(self, path, handler, methods=None, name=None):
        self.routes.append((path, handler, tuple(methods or ()), name))


def _stack(tmp_path, *, release_status="open", dialog_text="t8-live-reply"):
    """One honest stack: store + fakes + generate + live + handler."""
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    dialog = _CountingDialog(text=dialog_text)
    generate = wiring.build_generate(
        bus=bus, llm=llm, middleware=middleware,
        session_store=store, dialog=dialog,
    )
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    release_path = _write_release(
        tmp_path / "release.json", {"status": release_status}
    )
    handler = sa_bridge.build_live_handler(
        live=live, generate=generate, release_path=str(release_path)
    )
    return SimpleNamespace(
        store=store, bus=bus, llm=llm, middleware=middleware,
        dialog=dialog, generate=generate, live=live,
        release_path=release_path, handler=handler,
    )


# -- T8-1: same instances (is) + one call = one dialog + trace + drained -----


def test_t8_same_instances_identity_and_trace(tmp_path):
    st = _stack(tmp_path)
    envelope = _envelope()
    text = st.handler(envelope)
    assert text == "t8-live-reply"
    refs = st.generate.sa_live_refs
    assert refs[0] is st.bus
    assert refs[1] is st.llm
    assert refs[2] is st.middleware
    assert len(st.dialog.calls) == 1
    call = st.dialog.calls[0]
    assert call["agent_id"] == AGENT
    assert call["request_id"] == "req-t8-0001"
    assert len(st.generate.trace) == 1
    record = st.generate.trace[0]
    assert record["request_id"] == "req-t8-0001"
    assert record["agent_id"] == AGENT
    assert st.generate.pending == {}


def test_t8_pending_visible_in_flight_then_drained(tmp_path):
    st = _stack(tmp_path)
    seen = {}

    def _probing_dialog(messages, *, agent_id=None, request_id=None):
        seen["during"] = dict(generate.pending)
        return "t8-probed"

    generate = wiring.build_generate(
        bus=st.bus, llm=st.llm, middleware=st.middleware,
        session_store=st.store, dialog=_probing_dialog,
    )
    handler = sa_bridge.build_live_handler(
        live=st.live, generate=generate,
        release_path=str(st.release_path),
    )
    assert handler(_envelope()) == "t8-probed"
    assert list(seen["during"]) == ["req-t8-0001"]
    assert generate.pending == {}
    assert len(generate.trace) == 1


def test_t8_missing_instance_refuses_at_build(tmp_path):
    store = SessionStore(data_dir=tmp_path / "sessions")
    dialog = _CountingDialog()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        wiring.build_generate(
            bus=None, llm=object(), middleware=object(),
            session_store=store, dialog=dialog,
        )
    assert exc_info.value.status == 500
    assert dialog.calls == []


def test_t8_swapped_live_identity_mismatch_zero_dialog(tmp_path):
    st = _stack(tmp_path)
    other = sa_bridge.LiveLoop(bus=object(), llm=object(), middleware=object())
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        st.generate(_snapshot(), None, other)
    assert exc_info.value.status == 500
    assert st.dialog.calls == []
    assert st.generate.trace == []
    assert st.generate.pending == {}


def test_t8_dialog_none_refuses_never_falls_back(tmp_path):
    st = _stack(tmp_path)
    generate = wiring.build_generate(
        bus=st.bus, llm=st.llm, middleware=st.middleware,
        session_store=st.store, dialog=None,
    )
    handler = sa_bridge.build_live_handler(
        live=st.live, generate=generate,
        release_path=str(st.release_path),
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        handler(_envelope())
    assert exc_info.value.status == 500
    assert generate.trace == []
    assert generate.pending == {}


# -- T8-2: shared session, one root / one key / three channels ----------------


def test_t8_shared_session_telegram_voice_sa_three_way(tmp_path):
    st = _stack(tmp_path)
    agent, user = wiring.session_key(AGENT)
    assert (agent, user) == (AGENT, "user_bryan")
    st.store.append_turn(agent, user, "user", "t8 telegram 先到", "telegram")
    st.store.append_turn(agent, user, "assistant", "t8 voice 接著聊", "voice")
    assert st.handler(_envelope()) == "t8-live-reply"
    path = wiring.session_path(st.store, AGENT)
    assert path == st.store.data_dir / (AGENT + "_user_bryan.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    channels = [t["channel"] for t in raw["turns"]]
    assert channels == ["telegram", "voice", "sa-live"]
    assert raw["turns"][-1]["content"] == "t8-live-reply"
    assert len(st.dialog.calls) == 1
    history_roles = [role for role, _ in st.dialog.calls[0]["messages"]]
    assert "user" in history_roles and "assistant" in history_roles


def test_t8_same_store_root_no_fork(tmp_path):
    st = _stack(tmp_path)
    twin = SessionStore(data_dir=tmp_path / "sessions")
    fork = SessionStore(data_dir=tmp_path / "other-sessions")
    assert wiring.same_store_root(st.store, twin) is True
    assert wiring.same_store_root(st.store, fork) is False


# -- T8-3: mount registers the fixed path, re-verifies, then admits ----------


def test_t8_mount_registers_fixed_internal_path_and_admits(tmp_path):
    st = _stack(tmp_path)
    app = _StubApp(st.live)
    route = wiring.mount_live_route(
        app, live=st.live, generate=st.generate,
        release_path=str(st.release_path),
    )
    assert len(app.routes) == 1
    path, handler, methods, name = app.routes[0]
    assert path == sa_api.FORWARD_ENDPOINT
    assert path == wiring.INTERNAL_LIVE_PATH
    assert tuple(methods) == ("POST",)
    assert route.sa_live_path == wiring.INTERNAL_LIVE_PATH
    body = sa_api.envelope_to_body(_envelope())
    assert handler(body) == "t8-live-reply"
    assert len(st.dialog.calls) == 1


def test_t8_mount_closed_release_refuses_403_zero_dialog(tmp_path):
    st = _stack(tmp_path, release_status="closed")
    app = _StubApp(st.live)
    route = wiring.mount_live_route(
        app, live=st.live, generate=st.generate,
        release_path=str(st.release_path),
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        route(sa_api.envelope_to_body(_envelope()))
    assert (exc_info.value.status, exc_info.value.code) == (403, "forbidden")
    assert st.dialog.calls == []
    assert st.generate.trace == []


def test_t8_mount_broken_seal_refuses_before_admit(tmp_path):
    st = _stack(tmp_path)
    app = _StubApp(st.live)
    route = wiring.mount_live_route(
        app, live=st.live, generate=st.generate,
        release_path=str(st.release_path),
    )
    body = sa_api.envelope_to_body(_envelope())
    # The digest seal covers the snapshot: tampering sealed snapshot content
    # (not the top-level transport messages) must break the seal.
    body[sa_api.ENVELOPE_KEY]["snapshot"]["model"] = "tampered-after-seal"
    with pytest.raises(sa_schema.SnapshotRejected):
        route(body)
    assert st.dialog.calls == []


def test_t8_mount_swapped_state_loop_identity_mismatch(tmp_path):
    st = _stack(tmp_path)
    app = _StubApp(st.live)
    route = wiring.mount_live_route(
        app, live=st.live, generate=st.generate,
        release_path=str(st.release_path),
    )
    app.state._soul_access_live = sa_bridge.LiveLoop(
        bus=object(), llm=object(), middleware=object()
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        route(sa_api.envelope_to_body(_envelope()))
    assert exc_info.value.status == 500
    assert st.dialog.calls == []


def test_t8_mount_production_shape_refuses_until_dialog_wired(tmp_path):
    """The exact production shape (dialog=None) mounts but refuses at call."""
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    app = _StubApp(live)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    route = wiring.mount_live_route(
        app,
        live=live,
        generate=wiring.build_generate(
            bus=bus, llm=llm, middleware=middleware,
            session_store=store, dialog=None,
        ),
        release_path=str(release_path),
    )
    assert app.routes[0][0] == wiring.INTERNAL_LIVE_PATH
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        route(sa_api.envelope_to_body(_envelope()))
    assert exc_info.value.status == 500


# -- T8-4: thin forwarder (cross-process, zero live instances) ----------------


def _thin(release_path, sender=None):
    return wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )


def test_t8_thin_closed_refuses_403_sender_untouched(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sent = []
    fwd = _thin(release_path, sender=lambda url, body: sent.append((url, body)) or "x")
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert (exc_info.value.status, exc_info.value.code) == (403, "forbidden")
    assert sent == []


def test_t8_thin_open_posts_fixed_inner_endpoint(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sent = []

    def _fake_sender(url, body):
        sent.append((url, body))
        return "t8-thin-reply"

    fwd = _thin(release_path, sender=_fake_sender)
    assert fwd(_envelope()) == "t8-thin-reply"
    assert len(sent) == 1
    url, body = sent[0]
    assert url == fwd.sa_thin_target
    assert url.endswith(sa_api.FORWARD_ENDPOINT)
    assert sa_api.ENVELOPE_KEY in body
    assert sa_api.envelope_from_body(body).snapshot.dedup.agent_id == AGENT


def test_t8_thin_sender_fault_and_mistyped_refuse_500(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})

    def _boom(url, body):
        raise RuntimeError("t8 simulated carrier fault")

    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        _thin(release_path, sender=_boom)(_envelope())
    assert exc_info.value.status == 500

    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        _thin(release_path, sender=lambda url, body: "")(_envelope())
    assert exc_info.value.status == 500


def test_t8_thin_release_fault_matrix_refuses_403(tmp_path):
    for payload in ("not-json{{{", "[1, 2, 3]", '{"status": "OPEN"}', '{"other": 1}'):
        release_path = _write_release(tmp_path / "release.json", payload)
        sent = []
        fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "x")
        with pytest.raises(sa_bridge.LiveRefused) as exc_info:
            fwd(_envelope())
        assert exc_info.value.status == 403
        assert sent == []
    missing = tmp_path / "release-missing.json"
    assert not missing.exists()
    with pytest.raises(sa_bridge.LiveRefused):
        _thin(missing, sender=lambda url, body: "x")(_envelope())


# -- T8-5: bidirectional mutations (relaxed / tightened / revert green) -------


def test_t8_mutation_relaxed_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Relaxed mutant: forced-open gate forwards while closed (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sent = []
    fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "t8-red")
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: True)
    assert fwd(_envelope()) == "t8-red"  # RED: closed gate ignored.
    if len(sent) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed-open did not forward")
    monkeypatch.undo()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert exc_info.value.status == 403


def test_t8_mutation_tightened_gate_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Tightened mutant: forced-closed gate refuses while open (RED)."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sent = []
    fwd = _thin(release_path, sender=lambda url, body: sent.append(body) or "t8-red")
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: False)
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope())
    assert exc_info.value.status == 403  # RED: open gate ignored.
    if sent:
        pytest.fail("MUTATION DUD -- STOP, report: tightened-closed leaked a send")
    monkeypatch.undo()
    assert fwd(_envelope()) == "t8-red"


def test_t8_mutation_reverted_full_stack_green(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    st = _stack(tmp_path)
    assert sa_release.is_live_inbound_open(str(release_path)) is True
    assert st.handler(_envelope()) == "t8-live-reply"
    assert len(st.dialog.calls) == 1
    assert len(st.generate.trace) == 1


# -- T8-6: look-alikes that must NOT count as wiring success ------------------


def test_t8_open_route_without_generate_is_not_wiring_success(tmp_path):
    """開埠 ≠ 接線成功：路由已註冊、gate 開著，但 generate 沒接 → 仍 500."""
    store = SessionStore(data_dir=tmp_path / "sessions")
    bus, llm, middleware = object(), object(), object()
    live = sa_bridge.LiveLoop(bus=bus, llm=llm, middleware=middleware)
    app = _StubApp(live)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    generate = wiring.build_generate(
        bus=bus, llm=llm, middleware=middleware,
        session_store=store, dialog=None,
    )
    route = wiring.mount_live_route(
        app, live=live, generate=generate, release_path=str(release_path)
    )
    assert len(app.routes) == 1  # the port/route LOOKS open ...
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        route(sa_api.envelope_to_body(_envelope()))
    assert exc_info.value.status == 500  # ... but nothing can generate.
    assert generate.trace == []


def test_t8_unauthorized_401_never_reaches_the_loop(tmp_path):
    """401 ≠ 接線成功：認證在 loop 之前擋下，generate 零呼叫."""
    st = _stack(tmp_path)
    token, _digest = issue_credential()
    provider = RegistryProvider(
        {
            "owner_principal_id": OWNER,
            "backends": ["port-a"],
            "routes": [
                {
                    "soul_alias": "rem",
                    "backend_alias": "primary",
                    "agent_id": AGENT,
                    "public_model_id": "do-not-deploy-t8",
                    "backend_ref": "port-a",
                    "context_window_tokens": 4096,
                }
            ],
            "clients": [
                {
                    "client_id": "client-t8",
                    "principal_id": OWNER,
                    "purpose": "interactive",
                    "allowed_routes": ["rem/primary"],
                    "scope": "owner_private",
                    "allow_persistent_memory": True,
                    "credential_sha256": digest_of(token),
                }
            ],
        }
    )
    with pytest.raises(InterfaceError) as exc_info:
        authenticate("t8-wrong-token", provider.current())
    assert exc_info.value.status == 401
    assert st.dialog.calls == []
    assert st.generate.trace == []


def test_t8_legacy_200_without_live_calls_is_not_wiring_success(tmp_path):
    """legacy 200 ≠ 接線成功：無標記 body 走舊路，envelope 從未誕生."""
    st = _stack(tmp_path)
    legacy_body = {
        "model": "primary",
        "messages": [{"role": "user", "content": "t8 legacy hi"}],
    }
    assert sa_api.try_parse_envelope(legacy_body) is None
    internal = parse_chat_request(legacy_body)
    assert [(m.role, m.content) for m in internal.messages] == [
        ("user", "t8 legacy hi")
    ]
    assert st.dialog.calls == []
    assert st.generate.trace == []


# -- T8-7: release pin + AST gates ---------------------------------------------


def test_t8_release_pinned_and_closed_by_default():
    # The pinned path is fixed by the ticket (inside the harness tree, never
    # in git: the release/ dir does not exist and this ticket creates no
    # file). What the gate guarantees: absent/missing file reads closed.
    assert wiring.WIRING_VERSION == _T8_VERSION
    assert wiring.RELEASE_FILE == Path(
        "C:/Users/bbfcc/.local/bin/soul-os-harness/release/sa.json"
    )
    assert wiring.RELEASE_FILE.name == "sa.json"
    assert wiring.RELEASE_FILE.parent.name == "release"
    # Not production state: the pinned file never lives under repo data/.
    assert (_REPO_ROOT / "data") not in wiring.RELEASE_FILE.resolve().parents
    missing = _REPO_ROOT / "__t8_no_such_release__.json"
    assert sa_release.is_live_inbound_open(missing) is False
    assert sa_release.is_live_inbound_open(str(missing)) is False
    if not wiring.RELEASE_FILE.exists():
        assert sa_release.is_live_inbound_open(wiring.RELEASE_FILE) is False


def _own_source() -> str:
    return Path(__file__).read_text(encoding="utf-8")


def test_t8_ast_no_spawn_net_kill_or_prod_paths():
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
        assert banned not in imported, ("SA-LIVE-WIRING-1 T8", banned)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
            "Popen", "run", "call", "kill", "create_connection",
        ):
            if isinstance(node.value, ast.Name) and node.value.id in (
                "subprocess", "socket", "os",
            ):
                raise AssertionError(
                    "SA-LIVE-WIRING-1 T8: banned call site via %r"
                    % (node.value.id,)
                )
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


def test_t8_ast_wiring_no_fresh_construction_no_net_import():
    src = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(src)
    top_imports = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.add((alias.name or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_imports.add(node.module.split(".")[0])
    for banned in ("httpx", "socket", "urllib", "subprocess", "os", "signal"):
        assert banned not in top_imports, ("wiring top-level import", banned)
    built = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            built.add(func.id)
        elif isinstance(func, ast.Attribute):
            built.add(func.attr)
    for banned in (
        "LiveLoop", "SoulEventBus", "LLMProxy", "MemoryMiddleware", "PoolStore",
    ):
        assert banned not in built, ("wiring must not construct", banned)
    assert "soul_context_adapter" not in src


def test_t8_ast_scripts_use_formal_wiring():
    serve = (_REPO_ROOT / "scripts" / "serve_external_gateway.py").read_text(
        encoding="utf-8"
    )
    assert "build_thin_forwarder" in serve
    assert "RELEASE_FILE" in serve
    assert "build_live_handler" not in serve
    assert "live=None" not in serve
    run = (_REPO_ROOT / "scripts" / "run_server.py").read_text(encoding="utf-8")
    assert "mount_live_route" in run
    assert "build_generate" in run
    assert "get_live_loop" in run
    assert "new_shared_session_store" in run
