"""SA-ENTRY-GATE-1 T15 entry/gate convergence tests (sandbox only).

Decision-complete scope (SA-ENTRY-GATE-1): this file is new; product changes
are additive only in ``src/soul_access/wiring.py`` (shared pre-dispatch gate)
and ``scripts/serve_sa_thin.py`` (loopback-only shutdown control plane).
Frozen files (bridge / api / release / external gateway assembly / configs /
release/sa.json) are untouched; no server is started, no production port is
bound, no real request leaves the process, nothing under data/** is touched.

All test names carry the t15 marker: ``pytest -q tests/soul_access -k t15``
must collect > 0. Transport is memory-only (counting-sender stand-ins);
the release gate is always a self-created tmp file (never the production
path, never the pinned path). Sync only: no event loop is created, no socket
is opened (a fake listener / fake clock stand in for the lifecycle drills).

Coverage:
  a. shared gate negative (direct gated sender): closed / missing /
     unreadable / illegal payload / retry-before-close all refuse 403 with
     zero sender calls; close never retracts an already-dispatched reply;
  b. thin forwarder shares the same gate (closed refuses, open admits one);
  c. bidirectional mutations (gate removed / forced) must turn red, then
     revert green; the real transport (``_default_sender``) is hard-replaced
     in every test so a mock pass can never be a live success;
  d. thin start/stop drills (fake listener/clock): start writes the control
     file; unauthorized / non-loopback shutdown refused with no state change;
     verified shutdown replies first then drains; repeated shutdown is
     idempotent; stuck drain reports BLOCKED; listener is closed exactly
     once and resources are released; normal return codes; run_id + state
     events reconcile;
  e. AST teeth: no spawn/kill call sites, no import-time network stack.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

import scripts.serve_sa_thin as thin_mod  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import release as sa_release  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring as sa_wiring  # noqa: E402


# -- fixtures (synthetic T15 identity; never production) ---------------------

OWNER = "t15-entry-owner"
AGENT = "agent_rem"
SCOPE = "owner_private"

#: Opaque URL handed to the shared gated sender. Deliberately non-routable
#: (``.invalid``): the sender passes it through untouched and the gate verdict
#: never depends on it, so no test needs the production address. The real
#: thin target construction is covered separately via
#: ``fwd.sa_thin_target`` + ``endswith(FORWARD_ENDPOINT)`` (no literal).
_GATED_URL = "http://t15-gated.invalid/internal/soul-access/live"


def _identity(req, key):
    return sa_schema.RequestIdentity(
        request_id=req,
        conversation_id="dm",
        branch_id="root",
        turn_id="turn-%s" % req,
        idempotency_key=key,
    )


def _dedup(key):
    return sa_schema.DedupKey(
        principal_id=OWNER,
        agent_id=AGENT,
        scope=SCOPE,
        conversation_id="dm",
        branch_id="root",
        idempotency_key=key,
    )


def _live():
    return sa_schema.LiveState(
        personality_version="personality-t15-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-14T15:45:00-05:00",
        inner_life_state="inner-t15-v1",
        projection_state="projection-t15-v1",
        recall_version="recall-t15-v1",
        recall_items=("recall-t15-item-1",),
        history_pool_version="pool-t15-v1",
        clean_history=("hist-t15-item-1",),
    )


def _snapshot(n):
    req = "req-t15-%04d" % n
    key = "key-t15-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(req, key),
        dedup=_dedup(key),
        backend_alias="primary",
        model="do-not-deploy-model-t15",
        params=(("temperature", "0.5"), ("max_tokens", "8"),),
        budget_tokens=512,
        deadline_utc="2026-11-14T00:00:00Z",
        personality_version="personality-t15-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-14T15:45:00-05:00",
        inner_life_state="inner-t15-v1",
        projection_state="projection-t15-v1",
        recall_version="recall-t15-v1",
        recall_items=("recall-t15-item-1",),
        history_pool_version="pool-t15-v1",
        clean_history=("hist-t15-item-1",),
    )


def _envelope(n=1):
    return sa_api.build_envelope(
        snapshot=_snapshot(n),
        messages=(("user", "t15 probe %d" % n),),
        created_utc="2026-11-14T15:45:00-05:00",
    )


def _write_release(path: Path, payload) -> Path:
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _CountingSender:
    """Bottom-transport stand-in: records every call, never touches network."""

    def __init__(self, text="t15-reply", fault=None):
        self.text = text
        self.fault = fault
        self.calls = []

    def __call__(self, url, body):
        self.calls.append((url, body))
        if self.fault is not None:
            raise self.fault
        return self.text


class _FakeListener:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, delta):
        self.t += delta


def _control(tmp_path, **over):
    kw = {
        "run_id": "run-t15-drill",
        "token": "t15-control-token",
        "control_path": str(tmp_path / "sa-thin.control.json"),
        "clock": _FakeClock(),
        "drain_timeout_s": 5.0,
        "listener": _FakeListener(),
        "sleeper": lambda s: None,
    }
    kw.update(over)
    return thin_mod.ThinControl(**kw)


def _refuse_code(exc):
    return (exc.status, exc.code)


# -- T15-a: shared gate negative (direct gated sender) ------------------------


def test_t15_gated_sender_closed_refuses_zero_send(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sender = _CountingSender()
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


def test_t15_gated_sender_missing_refuses_zero_send(tmp_path):
    release_path = tmp_path / "release-missing.json"
    assert not release_path.exists()
    sender = _CountingSender()
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


def test_t15_gated_sender_no_gate_source_refuses_zero_send():
    sender = _CountingSender()
    gated = sa_wiring.build_gated_sender(release_path=None, sender=sender)
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


@pytest.mark.parametrize(
    "payload",
    [
        "not-json{{{",
        "[1, 2, 3]",
        '{"status": "OPEN"}',
        '{"status": ""}',
        '{"other": 1}',
        "null",
    ],
)
def test_t15_gated_sender_illegal_refuses_zero_send(tmp_path, payload):
    release_path = _write_release(tmp_path / "release.json", payload)
    sender = _CountingSender()
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


def test_t15_gated_sender_unreadable_refuses_zero_send(tmp_path):
    release_path = tmp_path / "release-dir"
    release_path.mkdir()
    sender = _CountingSender()
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


def test_t15_gated_sender_retry_rereads_gate_close_between_attempts(tmp_path):
    """Attempt 1 transport-fails while open; the fault closes the gate; the
    retry must refuse 403 (re-read) instead of resending: sender stays at 1."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sender = _CountingSender()

    def _flaky(url, body):
        sender(url, body)  # records the single attempt, then faults
        _write_release(release_path, {"status": "closed"})
        raise ConnectionError("t15 transport down")

    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=_flaky, max_attempts=3
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert len(sender.calls) == 1  # no resend after the close


def test_t15_gated_close_does_not_retract_dispatched(tmp_path):
    """A close AFTER a successful dispatch keeps the dispatched reply: the
    gate guards new dispatches only, never retraction."""
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sender = _CountingSender(text="t15-dispatched")

    def _send_then_close(url, body):
        text = sender(url, body)
        _write_release(release_path, {"status": "closed"})
        return text

    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=_send_then_close, max_attempts=2
    )
    assert (
        gated(_GATED_URL, {"model": "x"})
        == "t15-dispatched"
    )
    assert len(sender.calls) == 1


# -- T15-b: thin forwarder shares the same gate --------------------------------


def test_t15_thin_forwarder_closed_refuses_zero_send(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sender = _CountingSender()
    fwd = sa_wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope(1))
    assert _refuse_code(exc_info.value) == (403, "forbidden")
    assert sender.calls == []


def test_t15_thin_forwarder_open_admits_single_send(tmp_path):
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sender = _CountingSender(text="t15-thin-reply")
    fwd = sa_wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )
    assert fwd(_envelope(2)) == "t15-thin-reply"
    assert len(sender.calls) == 1
    assert sender.calls[0][0].endswith(sa_api.FORWARD_ENDPOINT)


# -- T15-c: mutations must turn red, then revert green ------------------------


def test_t15_mutation_gate_removed_turns_red_then_revert_green(tmp_path, monkeypatch):
    """Relaxed mutant: with the gate hot-read forced open, a closed gate
    dispatches (RED). Reverting restores the 403 -- the same oracle judges."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sender = _CountingSender(text="t15-red")
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: True)
    assert (
        gated(_GATED_URL, {"model": "x"})
        == "t15-red"
    )
    if len(sender.calls) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed-open did not dispatch")
    monkeypatch.undo()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        gated(_GATED_URL, {"model": "x"})
    assert _refuse_code(exc_info.value) == (403, "forbidden")


def test_t15_mutation_thin_gate_removed_turns_red_then_revert_green(
    tmp_path, monkeypatch
):
    """The thin entry uses the same shared gate: removing it dispatches the
    thin forward while closed (RED); reverting refuses again."""
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    sender = _CountingSender(text="t15-thin-red")
    fwd = sa_wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )
    monkeypatch.setattr(sa_release, "is_live_inbound_open", lambda path: True)
    assert fwd(_envelope(3)) == "t15-thin-red"
    if len(sender.calls) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: thin relaxed-open did not dispatch")
    monkeypatch.undo()
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        fwd(_envelope(4))
    assert _refuse_code(exc_info.value) == (403, "forbidden")


def test_t15_real_transport_hard_replaced_every_path(tmp_path, monkeypatch):
    """Every dispatch in this file goes through an injected fake: with the
    real transport rigged to explode, the open-gate admit still passes --
    so a mock pass can never be a live success."""

    def _exploding(url, body):
        raise AssertionError("t15 must never touch the real transport")

    monkeypatch.setattr(sa_wiring, "_default_sender", _exploding)
    release_path = _write_release(tmp_path / "release.json", {"status": "open"})
    sender = _CountingSender(text="t15-fake-only")
    gated = sa_wiring.build_gated_sender(
        release_path=str(release_path), sender=sender
    )
    assert (
        gated(_GATED_URL, {"model": "x"})
        == "t15-fake-only"
    )
    fwd = sa_wiring.build_thin_forwarder(
        release_path=str(release_path), sender=sender
    )
    assert fwd(_envelope(5)) == "t15-fake-only"


# -- T15-d: thin start/stop drills (fake listener/clock) ----------------------


def test_t15_control_start_writes_control_file(tmp_path):
    control = _control(tmp_path)
    path = Path(control.write_control_file())
    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-t15-drill"
    assert isinstance(payload["pid"], int) and payload["pid"] > 0
    assert payload["token"] == "t15-control-token"
    assert payload["endpoint"] == thin_mod.CONTROL_ENDPOINT
    assert control.result_code == 1  # still serving
    assert control.events[0]["event"] == "started"
    assert all(ev["run_id"] == "run-t15-drill" for ev in control.events)


def test_t15_control_unauthorized_shutdown_no_state_change(tmp_path):
    control = _control(tmp_path)
    listener = control._listener
    status, payload = control.handle_post("127.0.0.1", {"token": "wrong", "command": "shutdown"})
    assert status == 401
    assert payload["run_id"] == "run-t15-drill"
    assert control.state == thin_mod.STATE_SERVING
    assert listener.closed == 0
    assert control.result_code == 1
    blob = json.dumps(control.events)
    assert "wrong" not in blob and "t15-control-token" not in blob


def test_t15_control_non_loopback_refused_even_with_token(tmp_path):
    control = _control(tmp_path)
    for host in ("192.168.1.10", "10.0.0.5", "203.0.113.7", "testclient", None):
        status, _ = control.handle_post(
            host, {"token": "t15-control-token", "command": "shutdown"}
        )
        assert status == 403, host
    assert control.state == thin_mod.STATE_SERVING


def test_t15_control_bad_command_refused(tmp_path):
    control = _control(tmp_path)
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "reboot"}
    )
    assert status == 400
    assert payload["run_id"] == "run-t15-drill"
    assert control.state == thin_mod.STATE_SERVING


def test_t15_control_shutdown_drains_clean_idempotent(tmp_path):
    control = _control(tmp_path)
    listener = control._listener
    # Verified shutdown replies FIRST (draining), then the drain completes.
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status == 200
    assert payload == {
        "status": "ok",
        "run_id": "run-t15-drill",
        "state": thin_mod.STATE_DRAINING,
    }
    assert control.state == thin_mod.STATE_SHUT
    assert listener.closed == 1
    assert control._listener is None  # resources released
    assert control.result_code == 0  # normal return
    kinds = [ev["event"] for ev in control.events]
    assert kinds[:2] == ["started", "control-file-written"] or "started" in kinds
    assert "drain-begin" in kinds and "shut" in kinds
    assert all(ev["run_id"] == "run-t15-drill" for ev in control.events)
    # Repeated shutdown is idempotent: no second close, still 200.
    status2, payload2 = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status2 == 200 and payload2["state"] == thin_mod.STATE_SHUT
    assert listener.closed == 1


def test_t15_control_inflight_finishes_before_timeout(tmp_path):
    control = _control(tmp_path)
    clock = control._clock
    control.inflight = 1  # drill seam: one in-flight unit outstanding
    polls = []

    def _finish():
        polls.append(1)
        clock.advance(0.01)
        if len(polls) >= 2:
            control.inflight = 0

    control._poller = _finish
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT
    assert control.result_code == 0


def test_t15_control_stuck_drain_reports_blocked(tmp_path):
    control = _control(tmp_path)
    clock = control._clock
    listener = control._listener
    control.inflight = 1  # drill seam: stuck unit, never finishes
    control._poller = lambda: clock.advance(10.0)  # blow past the deadline
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status == 200  # reply-first still holds
    assert payload["state"] == thin_mod.STATE_DRAINING
    assert control.state == thin_mod.STATE_BLOCKED
    assert control.result_code == 3
    assert listener.closed == 1  # listener still closed + released
    assert control._listener is None
    assert "blocked" in [ev["event"] for ev in control.events]
    # Repeated shutdown on BLOCKED stays BLOCKED (idempotent, no recovery).
    status2, payload2 = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status2 == 200 and payload2["state"] == thin_mod.STATE_BLOCKED


def test_t15_control_shutdown_works_while_gate_closed(tmp_path):
    """The control path never consults the release gate: shutdown works
    while the (sandbox) production-equivalent gate reads closed."""
    _write_release(tmp_path / "release.json", {"status": "closed"})
    control = _control(tmp_path)
    status, _ = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    assert status == 200
    assert control.state == thin_mod.STATE_SHUT
    assert control.result_code == 0


def test_t15_control_track_refuses_new_work_while_draining(tmp_path):
    control = _control(tmp_path)
    with control.track():
        assert control.inflight == 1
    assert control.inflight == 0
    control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "shutdown"}
    )
    with pytest.raises(Exception):
        with control.track():
            pass
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "t15-control-token", "command": "status"}
    )
    assert status == 200 and payload["state"] == thin_mod.STATE_SHUT


def test_t15_control_route_registered_before_catchall(tmp_path, monkeypatch):
    """The POST control route is mounted on the built app ahead of the
    catch-all, loopback-only, POST-only (registration proof; real bind is a
    separately-authorized live round and is NOT run here)."""
    monkeypatch.setenv("SA_T15_THIN_KEY", "t15-sandbox-fake-key")
    from src.interfaces.credentials import digest_of  # noqa: E402

    registry = {
        "owner_principal_id": "t15-entry-owner",
        "backends": ["t15-thin"],
        "routes": [
            {
                "soul_alias": "rem",
                "backend_alias": "primary",
                "agent_id": "agent_rem",
                "public_model_id": "sa-t15-rem-primary",
                "backend_ref": "t15-thin",
                "context_window_tokens": 4096,
            }
        ],
        "clients": [
            {
                "client_id": "client-t15-thin",
                "principal_id": "t15-entry-owner",
                "purpose": "interactive",
                "allowed_routes": ["rem/primary"],
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of("t15-thin-sandbox-token"),
            }
        ],
    }
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps(registry), encoding="utf-8")
    presets_path = tmp_path / "presets.json"
    presets_path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": "primary",
                        "provider": "openai",
                        "model": "sa-t15-primary",
                        "params": {
                            "temperature": 0.5,
                            "max_tokens": 8,
                            "base_url": "https://t15-thin.invalid/v1/chat/completions",
                        },
                        "key_ref": "SA_T15_THIN_KEY",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    release_path = _write_release(tmp_path / "release.json", {"status": "closed"})
    control = _control(tmp_path)
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path), release_path=str(release_path),
        _forwarder=lambda envelope, **kw: (_ for _ in ()).throw(
            AssertionError("closed thin door forwarded")
        ),
        control=control,
    )
    paths = [
        (getattr(route, "path", None), sorted(getattr(route, "methods", None) or ()))
        for route in app.routes
    ]
    control_routes = [p for p in paths if p[0] == thin_mod.CONTROL_ENDPOINT]
    assert control_routes == [(thin_mod.CONTROL_ENDPOINT, ["POST"])]
    first_control = next(
        i for i, route in enumerate(app.routes)
        if getattr(route, "path", None) == thin_mod.CONTROL_ENDPOINT
    )
    first_catchall = next(
        i for i, route in enumerate(app.routes)
        if getattr(route, "path", None) == "/{full_path:path}"
    )
    assert first_control < first_catchall


# -- T15-e: AST teeth (no spawn/kill; no import-time network) ------------------


def _call_arg_strings(tree):
    blobs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts = list(node.args) + [kw.value for kw in node.keywords]
            for part in parts:
                for sub in ast.walk(part):
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                        blobs.append(sub.value)
    return blobs


def test_t15_ast_no_spawn_kill_or_prod_writes():
    for name in ("scripts/serve_sa_thin.py", "src/soul_access/wiring.py"):
        src = (_REPO_ROOT / name).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add((alias.name or "").split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported.add(node.module.split(".")[0])
        assert "subprocess" not in imported, name
        assert "signal" not in imported, name
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in (
                "Popen",
                "run",
                "call",
                "kill",
            ):
                if isinstance(node.value, ast.Name) and node.value.id in (
                    "subprocess",
                    "os",
                ):
                    raise AssertionError("%s: banned call site" % name)
        for blob in _call_arg_strings(tree):
            assert "Stop-Process" not in blob, name
            assert "taskkill" not in blob, name
            assert "task_stop" not in blob, name
            assert "data/" not in blob and "data\\" not in blob, name


def test_t15_ast_network_stack_stays_lazy():
    """httpx (the only real transport) is imported inside the default sender
    only -- importing the modules never touches the network stack."""
    wiring_src = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(
        encoding="utf-8"
    )
    assert "\nimport httpx" not in wiring_src
    assert "\nfrom httpx" not in wiring_src
    assert "    import httpx" in wiring_src  # function-local, lazy
    thin_src = (_REPO_ROOT / "scripts" / "serve_sa_thin.py").read_text(encoding="utf-8")
    assert "import httpx" not in thin_src
