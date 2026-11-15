"""SA-DOOR-MECH-1 door-mechanism acceptance (sandbox only).

Decision-complete scope (SA-DOOR-MECH-1, mastermind Sol approved): prove the
formal dual-door mechanism and ONLY the mechanism (actual opening is
SA-DOOR-OPEN-1). Product change is additive-only in
``scripts/serve_sa_thin.py`` (``ThinControl.close_door`` /
``install_registry`` / ``ready`` / ``open_door`` + ``panic-close`` +
``--control`` branch); ``src/**``, frozen contracts, configs and the
production release file are untouched. No server is started, no production
port is bound, no real request leaves the process, nothing under ``data/**``
is touched.

All test names carry the door marker. Transport is memory-only: the thin app
is driven through ``httpx.ASGITransport`` (in-process, never a port); the
sandbox release/config files live under per-test ``tmp_path``. The only key
material is synthetic sandbox tokens. Outbound bottom-I/O is a counting
sender stand-in aimed at a non-routable ``.invalid`` base. The ``--control``
client never touches the real network: success paths use an injected fake
``HTTPConnection`` factory, and the real factory is execution-time blocked
by the SA-0 conftest (any real socket raises). Sync tests only.

Coverage (ticket acceptance mapping):
  a. default closed + restart never restores open (fresh controller stays
     closed even while the sandbox shared gate reads open);
  b. wrong token / wrong or missing run_id refuse 401 with zero state
     change; non-loopback refuses 403;
  c. order cannot be skipped (open-before-install, open-without-ready,
     ready-without-install, install-while-open all 409);
  d. full install -> ready -> open admits exactly one success; order proof
     (shared-opened event precedes instance-opened);
  e. close after open refuses new inbound (403, zero new upstream calls),
     release reads closed back, close is idempotent;
  f. panic-close is a direct close_door (dual re-close, idempotent);
  g. close is never blocked by drift (drifted configs still re-close);
     drift refuses install/open and re-closes (ticket stopped);
  h. install drives the service's actual RegistryProvider.update() with the
     same verified bytes (live snapshot changes; bad candidate keeps the
     old snapshot);
  i. concurrency: admissions and close share one lock (no exception, ends
     closed, new inbound refused);
  j. ``--control`` runs before bind/serve; fixed loopback endpoint, zero
     retry; success exits 0; lost contact / bad file / refusal exit 2 and
     never claim a re-close;
  k. bidirectional mutations (door check removed / hash guard removed) turn
     red, then revert green; DUD stops the run;
  l. AST teeth: no spawn/kill/network imports; panic-close calls
     close_door; ``--control`` branch precedes preflight in main.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import queue
import sys
import threading
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:  # noqa: E402
    sys.path.append(str(_REPO_ROOT))

import scripts.serve_sa_thin as thin_mod  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring as sa_wiring  # noqa: E402


# -- synthetic door fixtures (never production identity) ----------------------

OWNER = "door-mech-owner"
SOUL = "rem"
AGENT = "agent_rem"
SCOPE = "owner_private"
TOKEN = "door-mech-sandbox-token"
WRONG_TOKEN = "door-mech-wrong-token"
TOKEN_DIGEST = digest_of(TOKEN)
PRIMARY = "primary"
STANDBY = "standby"
ALIASES = (PRIMARY, STANDBY)
MODELS = {PRIMARY: "door-primary", STANDBY: "door-standby"}
KEY_REF = "SA_DOOR_MECH_KEY"
FAKE_KEY = "door-mech-sandbox-fake-key"
NON_ROUTABLE_TARGET = "http://door-mech.invalid"
THIN_SUCCESS_TEXT = "door-mech reply"
RUN_ID = "run-door-mech-1"
DOOR_TOKEN = "door-mech-control-token"


def _identity(req, key):
    return sa_schema.RequestIdentity(
        request_id=req,
        conversation_id="dm",
        branch_id="root",
        turn_id="turn-%s" % req,
        idempotency_key=key,
    )


def _dedup(agent, req, key, principal=OWNER):
    return sa_schema.DedupKey(
        principal_id=principal,
        agent_id=agent,
        scope=SCOPE,
        conversation_id="dm",
        branch_id="root",
        idempotency_key=key,
    )


def _snapshot_for(alias, model, n, principal=OWNER):
    req = "req-door-%04d" % n
    key = "key-door-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(req, key),
        dedup=_dedup(AGENT, req, key, principal),
        backend_alias=alias,
        model=model,
        params=(("temperature", "0.5"), ("max_tokens", "8"),),
        budget_tokens=512,
        deadline_utc="2026-11-15T00:00:00Z",
        personality_version="personality-door-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-15T15:45:00-05:00",
        inner_life_state="inner-door-v1",
        projection_state="projection-door-v1",
        recall_version="recall-door-v1",
        recall_items=("recall-door-item-1",),
        history_pool_version="pool-door-v1",
        clean_history=("hist-door-item-1",),
    )


def _registry_raw(token_digest=TOKEN_DIGEST):
    return {
        "owner_principal_id": OWNER,
        "backends": ["door-mech"],
        "routes": [
            {
                "soul_alias": SOUL,
                "backend_alias": alias,
                "agent_id": AGENT,
                "public_model_id": "door-%s-%s" % (SOUL, alias),
                "backend_ref": "door-mech",
                "context_window_tokens": 4096,
            }
            for alias in ALIASES
        ],
        "clients": [
            {
                "client_id": "client-door-mech",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (SOUL, PRIMARY)],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _preset_entries():
    return [
        {
            "name": alias,
            "provider": "openai",
            "model": MODELS[alias],
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": "https://door-mech.invalid/v1/chat/completions",
            },
            "key_ref": KEY_REF,
        }
        for alias in ALIASES
    ]


def _door_files(tmp_path: Path, *, release_status="closed"):
    """Sandbox registry / presets / release copies (never production)."""
    registry_path = tmp_path / "registry.json"
    presets_path = tmp_path / "presets.json"
    release_path = tmp_path / "release.json"
    registry_path.write_text(json.dumps(_registry_raw()), encoding="utf-8")
    presets_path.write_text(
        json.dumps({"presets": _preset_entries()}), encoding="utf-8"
    )
    release_path.write_text(json.dumps({"status": release_status}), encoding="utf-8")
    return (registry_path, presets_path, release_path)


def _pin_to_files(monkeypatch, registry_path: Path, presets_path: Path):
    """Point the dual-hash pins at the sandbox copies (T12 precedent).

    The production pins stay asserted by T12; here the sandbox copies stand
    in so the verify path runs for real (drift tests flip one byte and must
    refuse).
    """
    monkeypatch.setattr(
        thin_mod,
        "REGISTRY_SHA256",
        hashlib.sha256(registry_path.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        thin_mod,
        "PRESETS_SHA256",
        hashlib.sha256(presets_path.read_bytes()).hexdigest(),
    )


def _door_control(
    tmp_path: Path,
    monkeypatch,
    gate_path: Path,
    registry_path: Path,
    presets_path: Path,
    *,
    run_id=RUN_ID,
    token=DOOR_TOKEN,
    pin=True,
):
    if pin:
        _pin_to_files(monkeypatch, registry_path, presets_path)
    from src.interfaces.external_auth import RegistryProvider  # noqa: E402

    provider = RegistryProvider(json.loads(registry_path.read_text(encoding="utf-8")))
    control = thin_mod.ThinControl(
        run_id=run_id,
        token=token,
        control_path=str(tmp_path / "control.json"),
        release_path=str(gate_path),
        registry_path=str(registry_path),
        presets_path=str(presets_path),
    )
    control.attach(provider=provider)
    return control


def _open_via_api(control):
    status, _ = control.install_registry()
    assert status == 200
    status, _ = control.ready()
    assert status == 200
    status, payload = control.open_door()
    assert status == 200
    return payload


class _CountingSender:
    """Bottom-transport stand-in: records every call, answers fixed text."""

    def __init__(self, text=THIN_SUCCESS_TEXT):
        self.text = text
        self.calls = []

    def __call__(self, url, body):
        self.calls.append({"url": url, "body": body})
        return self.text


def _build_app(monkeypatch, registry_path, presets_path, gate_path, control, sender):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    forwarder = sa_wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base=NON_ROUTABLE_TARGET,
        sender=sender,
    )
    return thin_mod.build_thin_app(
        str(registry_path),
        str(presets_path),
        release_path=str(gate_path),
        _forwarder=forwarder,
        control=control,
    )


# -- module-level loop driver for ASGI loopback (T12/T17 idiom: in-process) ---

_DOOR_LOOP = asyncio.new_event_loop()


class _Runner:
    """Own-thread loop driver: queue handoff, never a cross-thread wakeup."""

    def __init__(self, loop, name):
        self.loop = loop
        self.jobs = queue.Queue()
        self.thread = threading.Thread(target=self._serve, name=name, daemon=True)
        self.thread.start()

    def _serve(self):
        asyncio.set_event_loop(self.loop)
        while True:
            coro, out_q = self.jobs.get()
            try:
                out_q.put((True, self.loop.run_until_complete(coro)))
            except BaseException as exc:  # noqa: BLE001
                out_q.put((False, exc))

    def run(self, coro):
        out_q = queue.Queue(maxsize=1)
        self.jobs.put((coro, out_q))
        ok, val = out_q.get()
        if ok:
            return val
        raise val


_DOOR_RUNNER = _Runner(_DOOR_LOOP, "door-mech-outward")


def _post(app, soul, alias, token, body):
    async def _go():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        try:
            return await client.post(
                "/%s/%s/v1/chat/completions" % (soul, alias),
                headers={"Authorization": "Bearer " + token},
                json=body,
            )
        finally:
            await client.aclose()

    return _DOOR_RUNNER.run(_go())


def _post_envelope(app, n=1):
    snapshot = _snapshot_for(PRIMARY, MODELS[PRIMARY], n)
    return _post(app, SOUL, PRIMARY, TOKEN, sa_api.envelope_to_body(
        sa_api.build_envelope(snapshot=snapshot, messages=(("user", "door probe"),),
                              created_utc="2026-11-15T00:00:00Z")))


# -- fake HTTPConnection factory for the --control client ---------------------

class _FakeHTTPResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    def read(self):
        return self._body.encode("utf-8")


class _FakeHTTPConnection:
    """Zero-retry loopback stand-in: records the single request, never dials."""

    def __init__(self, host, port, timeout=None, *, status=200, body="{}", fault=None,
                 log=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._status = status
        self._body = body
        self._fault = fault
        self.requests = []
        if log is not None:
            log.append((host, port))

    def request(self, method, path, body=None, headers=None):
        self.requests.append((method, path, body, dict(headers or {})))
        if self._fault is not None:
            raise self._fault

    def getresponse(self):
        return _FakeHTTPResponse(self._status, self._body)


def _fake_factory(log, *, status=200, body="{}", fault=None):
    def _make(host, port, timeout=None):
        conn = _FakeHTTPConnection(
            host, port, timeout=timeout, status=status, body=body, fault=fault,
            log=log,
        )
        _make.last = conn
        return conn

    _make.last = None
    return _make


def _write_control_file(tmp_path: Path, control):
    control.write_control_file()
    return str(tmp_path / "control.json")


# -- a/b: default closed, restart never restores open, auth -------------------

def test_door_default_closed_restart_stays_closed(tmp_path, monkeypatch):
    """Fresh controllers start closed; a fresh one stays closed even while the
    shared sandbox gate reads open (restart never restores open)."""
    registry_path, presets_path, gate_path = _door_files(tmp_path, release_status="open")
    sender = _CountingSender()
    first = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    app = _build_app(monkeypatch, registry_path, presets_path, gate_path, first, sender)
    assert _post_envelope(app).status_code == 403
    assert sender.calls == []
    assert first.is_instance_open() is False
    # Restart = a brand-new controller object: still closed, same open gate.
    second = _door_control(
        tmp_path, monkeypatch, gate_path, registry_path, presets_path,
        run_id="run-door-mech-2",
    )
    assert second.is_instance_open() is False
    app2 = _build_app(monkeypatch, registry_path, presets_path, gate_path, second, sender)
    assert _post_envelope(app2, n=2).status_code == 403
    assert sender.calls == []


def test_door_wrong_token_no_state_change(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    status, payload = control.handle_post(
        "127.0.0.1", {"token": WRONG_TOKEN, "command": "close", "run_id": RUN_ID}
    )
    assert status == 401
    assert payload["run_id"] == RUN_ID
    assert control.is_instance_open() is False
    assert control.is_shared_open() is False
    blob = json.dumps(control.events)
    assert WRONG_TOKEN not in blob and DOOR_TOKEN not in blob


def test_door_wrong_or_missing_run_id_no_state_change(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    for bad_body in (
        {"token": DOOR_TOKEN, "command": "close", "run_id": "run-nope"},
        {"token": DOOR_TOKEN, "command": "close"},
        {"token": DOOR_TOKEN, "command": "install", "run_id": "run-nope"},
    ):
        status, payload = control.handle_post("127.0.0.1", bad_body)
        assert status == 401
        assert payload["run_id"] == RUN_ID
    assert control.is_instance_open() is False
    assert control.is_shared_open() is False


def test_door_non_loopback_refused_even_with_token(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    for host in ("192.168.1.10", "10.0.0.5", None):
        status, _ = control.handle_post(
            host, {"token": DOOR_TOKEN, "command": "close", "run_id": RUN_ID}
        )
        assert status == 403, host
    assert control.is_instance_open() is False


# -- c: order cannot be skipped ------------------------------------------------

def test_door_sequence_cannot_skip(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    status, payload = control.open_door()
    assert status == 409 and payload["reason"] == "not-installed-or-ready"
    status, payload = control.ready()
    assert status == 409 and payload["reason"] == "not-installed"
    status, payload = control.install_registry()
    assert status == 200
    status, payload = control.open_door()
    assert status == 409 and payload["reason"] == "not-installed-or-ready"
    assert control.is_instance_open() is False
    assert control.is_shared_open() is False


def test_door_install_requires_both_closed(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    _open_via_api(control)
    assert control.is_shared_open() is True
    status, payload = control.install_registry()
    assert status == 409 and payload["reason"] == "doors-not-both-closed"
    # The successful open above still stands (the refused reinstall changed nothing).
    assert control.is_instance_open() is True
    assert control.is_shared_open() is True


# -- d: full sequence admits once, shared-first order --------------------------

def test_door_full_sequence_opens_once_shared_first(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    sender = _CountingSender()
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    app = _build_app(monkeypatch, registry_path, presets_path, gate_path, control, sender)
    assert _post_envelope(app).status_code == 403
    assert sender.calls == []
    status, payload = control.install_registry()
    assert status == 200
    status, payload = control.ready()
    assert status == 200 and payload["ready"] is True
    status, payload = control.open_door()
    assert status == 200
    assert payload["instance_open"] is True and payload["shared_open"] is True
    kinds = [ev["event"] for ev in control.events]
    assert kinds.index("shared-opened") < kinds.index("instance-opened")
    response = _post_envelope(app, n=2)
    assert response.status_code == 200
    assert len(sender.calls) == 1
    assert response.json()["choices"][0]["message"]["content"] == THIN_SUCCESS_TEXT
    assert all(ev["run_id"] == RUN_ID for ev in control.events)


# -- e/f: close + panic-close re-close, idempotent ------------------------------

def test_door_close_after_open_refuses_new_inbound_zero_upstream(
    tmp_path, monkeypatch
):
    """Close after open: legal new inbound is 403, upstream delta is zero."""
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    sender = _CountingSender()
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    app = _build_app(monkeypatch, registry_path, presets_path, gate_path, control, sender)
    _open_via_api(control)
    assert _post_envelope(app).status_code == 200
    assert len(sender.calls) == 1
    kinds_before = len(control.events)
    status, payload = control.close_door()
    assert status == 200
    assert payload["instance_open"] is False and payload["shared_open"] is False
    kinds = [ev["event"] for ev in control.events[kinds_before:]]
    assert kinds.index("instance-closed") < kinds.index("shared-closed")
    assert json.loads(gate_path.read_text(encoding="utf-8")) == {"status": "closed"}
    assert control._ready is False  # ready qualification revoked
    assert _post_envelope(app, n=2).status_code == 403
    assert len(sender.calls) == 1  # zero new upstream
    # Idempotent: a second close is still 200 and changes nothing.
    status2, _ = control.close_door()
    assert status2 == 200
    assert _post_envelope(app, n=3).status_code == 403
    assert len(sender.calls) == 1


def test_door_panic_close_is_direct_close_idempotent(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    sender = _CountingSender()
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    app = _build_app(monkeypatch, registry_path, presets_path, gate_path, control, sender)
    _open_via_api(control)
    assert _post_envelope(app).status_code == 200
    status, payload = control.handle_post(
        "127.0.0.1",
        {"token": DOOR_TOKEN, "command": "panic-close", "run_id": RUN_ID},
    )
    assert status == 200
    assert payload["instance_open"] is False and payload["shared_open"] is False
    assert _post_envelope(app, n=2).status_code == 403
    assert len(sender.calls) == 1
    status2, _ = control.handle_post(
        "127.0.0.1",
        {"token": DOOR_TOKEN, "command": "panic-close", "run_id": RUN_ID},
    )
    assert status2 == 200
    assert len(sender.calls) == 1


# -- g: drift never blocks close; drift refuses install/open and re-closes -----

def test_door_close_not_blocked_by_drift(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(
        tmp_path, release_status="open"
    )
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    raw = bytearray(registry_path.read_bytes())
    raw[-2] ^= 0x01
    registry_path.write_bytes(bytes(raw))
    # Configs drifted, shared gate open: close must still re-close (200).
    status, payload = control.close_door()
    assert status == 200
    assert payload["instance_open"] is False and payload["shared_open"] is False
    assert json.loads(gate_path.read_text(encoding="utf-8")) == {"status": "closed"}


def test_door_drift_refuses_install_and_open_and_recloses(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    status, _ = control.install_registry()
    assert status == 200
    status, _ = control.ready()
    assert status == 200
    raw = presets_path.read_text(encoding="utf-8")
    assert '"door-primary"' in raw
    presets_path.write_text(raw.replace('"door-primary"', '"door-primarx"'), encoding="utf-8")
    status, payload = control.open_door()
    assert status == 409 and payload["reason"] == "presets-drift"
    assert control.is_instance_open() is False
    assert control.is_shared_open() is False  # drift re-closed
    assert control._ready is False
    # Fresh drift also refuses install (ticket stopped, nothing half-installed).
    # NOTE: no re-pinning -- the pins still name the pre-drift bytes.
    control2 = _door_control(
        tmp_path, monkeypatch, gate_path, registry_path, presets_path,
        run_id="run-door-drift-2", pin=False,
    )
    status2, payload2 = control2.install_registry()
    assert status2 == 409 and payload2["reason"] == "presets-drift"


# -- h: install drives the live provider snapshot ------------------------------

def test_door_install_uses_live_provider_snapshot(tmp_path, monkeypatch):
    from src.interfaces.external_auth import RegistryProvider  # noqa: E402

    registry_path, presets_path, gate_path = _door_files(tmp_path)
    _pin_to_files(monkeypatch, registry_path, presets_path)
    provider = RegistryProvider(json.loads(registry_path.read_text(encoding="utf-8")))
    control = thin_mod.ThinControl(
        run_id=RUN_ID,
        token=DOOR_TOKEN,
        control_path=str(tmp_path / "control.json"),
        release_path=str(gate_path),
        registry_path=str(registry_path),
        presets_path=str(presets_path),
    )
    control.attach(provider=provider)
    status, _ = control.install_registry()
    assert status == 200
    live = provider.current()
    assert {(r.soul_alias, r.backend_alias) for r in live.routes} == {
        ("rem", "primary"), ("rem", "standby"),
    }
    # A schema-bad candidate keeps the old snapshot serving (provider's own
    # snapshot semantics: update validates first, swaps only on success).
    bad = {
        "owner_principal_id": OWNER,
        "backends": [],
        "routes": [],
        "clients": [],
    }
    registry_path.write_text(json.dumps(bad), encoding="utf-8")
    _pin_to_files(monkeypatch, registry_path, presets_path)
    status2, payload2 = control.install_registry()
    assert status2 == 409 and payload2["reason"] == "snapshot-rejected"
    assert provider.current() is live


# -- i: concurrency shares one lock --------------------------------------------

def test_door_concurrent_admissions_and_close_share_one_lock(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    sender = _CountingSender()
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    app = _build_app(monkeypatch, registry_path, presets_path, gate_path, control, sender)
    _open_via_api(control)
    errors = []

    def _admit_many():
        try:
            for n in range(10, 30):
                _post_envelope(app, n=n)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    workers = [threading.Thread(target=_admit_many) for _ in range(4)]
    for w in workers:
        w.start()
    status, _ = control.close_door()
    assert status == 200
    for w in workers:
        w.join()
    assert errors == []
    # Ended closed: new inbound refuses, shared gate reads closed.
    assert control.is_instance_open() is False
    assert control.is_shared_open() is False
    assert _post_envelope(app, n=99).status_code == 403


# -- j: --control client (fixed endpoint, zero retry, exit codes) ---------------

def test_door_control_success_exit_0_single_fixed_request(tmp_path, monkeypatch):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    control_file = _write_control_file(tmp_path, control)
    log = []
    factory = _fake_factory(
        log, status=200, body=json.dumps({"status": "ok", "run_id": RUN_ID}),
    )
    monkeypatch.setattr(thin_mod.http.client, "HTTPConnection", factory)
    code = thin_mod.main(["--control", "close", "--control-file", control_file])
    assert code == 0
    assert log == [(thin_mod.THIN_HOST, thin_mod.THIN_PORT)]
    conn = factory.last
    assert len(conn.requests) == 1  # zero retry: exactly one attempt
    method, path, body, _headers = conn.requests[0]
    assert method == "POST" and path == thin_mod.CONTROL_ENDPOINT
    sent = json.loads(body)
    assert sent["command"] == "close" and sent["run_id"] == RUN_ID
    assert sent["token"] == DOOR_TOKEN


def test_door_control_lost_contact_exit_2_no_closed_claim(
    tmp_path, monkeypatch, capsys
):
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    control_file = _write_control_file(tmp_path, control)
    log = []
    factory = _fake_factory(log, fault=OSError("door write failed"))
    monkeypatch.setattr(thin_mod.http.client, "HTTPConnection", factory)
    code = thin_mod.main(["--control", "close", "--control-file", control_file])
    assert code == 2
    assert len(factory.last.requests) == 1  # the single attempt failed, no retry
    out, err = capsys.readouterr()
    assert "ok." not in out  # never claims a re-close on failure


def test_door_control_bad_file_and_refusal_exit_2(tmp_path, monkeypatch):
    log = []
    factory = _fake_factory(log, status=401, body=json.dumps({"status": "unauthorized"}))
    monkeypatch.setattr(thin_mod.http.client, "HTTPConnection", factory)
    assert thin_mod.main(
        ["--control", "open", "--control-file", str(tmp_path / "missing.json")]
    ) == 2
    assert log == []  # unreadable file: no contact attempted
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    control_file = _write_control_file(tmp_path, control)
    assert thin_mod.main(
        ["--control", "open", "--control-file", control_file]
    ) == 2
    assert log == [(thin_mod.THIN_HOST, thin_mod.THIN_PORT)]


def test_door_control_branch_runs_before_preflight(tmp_path, monkeypatch):
    """The --control分流 runs before preflight/bind/serve (main returns
    without ever reaching the bind probe)."""
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    control_file = _write_control_file(tmp_path, control)
    log = []
    factory = _fake_factory(log, status=200, body=json.dumps({"status": "ok"}))
    monkeypatch.setattr(thin_mod.http.client, "HTTPConnection", factory)

    def _exploding(*args, **kwargs):
        raise AssertionError("door --control must not reach preflight/bind")

    monkeypatch.setattr(thin_mod, "preflight", _exploding)
    code = thin_mod.main(["--control", "ready", "--control-file", control_file])
    assert code == 0
    assert log == [(thin_mod.THIN_HOST, thin_mod.THIN_PORT)]


# -- k: bidirectional mutations (removed guard must turn red) -------------------

def test_door_mutation_door_removed_red_then_revert_green(tmp_path, monkeypatch):
    """Relaxed mutant: the instance-door check deleted -> closed doors admit
    (RED). Reverting restores the 403 -- the same oracle judges."""
    registry_path, presets_path, gate_path = _door_files(
        tmp_path, release_status="open"
    )
    sender = _CountingSender()
    control = _door_control(tmp_path, monkeypatch, gate_path, registry_path, presets_path)
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    monkeypatch.setattr(
        thin_mod,
        "build_thin_closed_handler",
        lambda **kw: kw["_forwarder"],
    )
    app = thin_mod.build_thin_app(
        str(registry_path),
        str(presets_path),
        release_path=str(gate_path),
        _forwarder=sa_wiring.build_thin_forwarder(
            release_path=str(gate_path),
            target_base=NON_ROUTABLE_TARGET,
            sender=sender,
        ),
        control=control,
    )
    assert control.is_instance_open() is False  # doors still closed ...
    response = _post_envelope(app)
    if response.status_code != 200 or len(sender.calls) != 1:
        pytest.fail("MUTATION DUD -- STOP, report: removed door did not admit")
    monkeypatch.undo()
    sender2 = _CountingSender()
    control2 = _door_control(
        tmp_path, monkeypatch, gate_path, registry_path, presets_path,
        run_id="run-door-revert-1",
    )
    app2 = _build_app(
        monkeypatch, registry_path, presets_path, gate_path, control2, sender2
    )
    assert _post_envelope(app2, n=4).status_code == 403
    assert sender2.calls == []


def test_door_mutation_hash_guard_removed_red_then_revert_green(
    tmp_path, monkeypatch
):
    """Relaxed mutant: the hash guard rigged to always match -> drifted
    configs install (RED). Reverting refuses the drift (GREEN)."""
    registry_path, presets_path, gate_path = _door_files(tmp_path)
    raw = registry_path.read_text(encoding="utf-8")
    assert '"door-mech-owner"' in raw
    registry_path.write_text(raw.replace('"door-mech-owner"', '"door-mech-owneq"'), encoding="utf-8")
    _pin_to_files(monkeypatch, registry_path, presets_path)
    control = thin_mod.ThinControl(
        run_id=RUN_ID,
        token=DOOR_TOKEN,
        control_path=str(tmp_path / "control.json"),
        release_path=str(gate_path),
        registry_path=str(registry_path),
        presets_path=str(presets_path),
    )
    from src.interfaces.external_auth import RegistryProvider  # noqa: E402

    provider = RegistryProvider(_registry_raw())
    control.attach(provider=provider)
    calls = []

    def _fake_sha256(data):
        calls.append(bytes(data))

        class _H:
            def hexdigest(self_inner):
                if len(calls) == 1:
                    return thin_mod.REGISTRY_SHA256
                return thin_mod.PRESETS_SHA256

        return _H()

    monkeypatch.setattr(thin_mod.hashlib, "sha256", _fake_sha256)
    status, _ = control.install_registry()
    if status != 200:
        pytest.fail("MUTATION DUD -- STOP, report: removed hash guard did not admit drift")
    monkeypatch.undo()
    control2 = thin_mod.ThinControl(
        run_id="run-door-revert-2",
        token=DOOR_TOKEN,
        control_path=str(tmp_path / "control2.json"),
        release_path=str(gate_path),
        registry_path=str(registry_path),
        presets_path=str(presets_path),
    )
    control2.attach(provider=provider)
    status2, payload2 = control2.install_registry()
    assert status2 == 409 and payload2["reason"] == "registry-drift"


# -- l: AST teeth ---------------------------------------------------------------

def _thin_source():
    return (_REPO_ROOT / "scripts" / "serve_sa_thin.py").read_text(encoding="utf-8")


def test_door_ast_no_spawn_kill_or_import_time_network():
    src = _thin_source()
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add((alias.name or "").split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "subprocess" not in imported
    assert "signal" not in imported
    assert "httpx" not in imported
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in (
            "Popen", "run", "call", "kill",
        ):
            if isinstance(node.value, ast.Name) and node.value.id in ("subprocess", "os"):
                raise AssertionError("banned call site")
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "Stop-Process" not in node.value
            assert "taskkill" not in node.value
            assert "task_stop" not in node.value


def test_door_ast_panic_close_calls_close_door_and_control_first():
    """panic-close is a direct close_door() call; the --control分流 precedes
    preflight/bind in main(); the client sends exactly one request."""
    src = _thin_source()
    tree = ast.parse(src)
    found_panic = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "handle_post":
            text = ast.get_source_segment(src, node) or ""
            if "panic-close" in text and "close_door" in text:
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Call)
                        and isinstance(sub.func, ast.Attribute)
                        and sub.func.attr == "close_door"
                    ):
                        found_panic = True
    assert found_panic, "panic-close must call close_door() directly"
    main_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    order = {}
    for sub in ast.walk(main_node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            if sub.func.attr in ("_run_control_command",):
                order.setdefault("control", sub.lineno)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
            if sub.func.id in ("_run_control_command",):
                order.setdefault("control", sub.lineno)
            if sub.func.id in ("preflight",):
                order.setdefault("preflight", sub.lineno)
    assert order.get("control", 10**9) < order.get("preflight", -1)
    cmd_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_control_command"
    )
    requests = [
        sub
        for sub in ast.walk(cmd_node)
        if isinstance(sub, ast.Call)
        and isinstance(sub.func, ast.Attribute)
        and sub.func.attr == "request"
    ]
    assert len(requests) == 1  # zero retry: a single POST, no loop resend
    assert thin_mod.CONTROL_COMMANDS == ("close", "install", "ready", "open", "panic-close")
    assert thin_mod.CONTROL_ENDPOINT == "/internal/sa-thin/control"
