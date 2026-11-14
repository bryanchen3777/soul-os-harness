"""SA-THIN-ENTRY-1 T12 thin-entry closed acceptance (sandbox only).

Decision-complete scope (SA-THIN-ENTRY-1): this file is new; the only
tracked product file is the new ``scripts/serve_sa_thin.py``. Frozen files
(``src/external_gateway/*``, ``src/soul_access/*`` except the new entry's
own import surface, ``configs/sa/*``, ``release/sa.json``, canonical) are
untouched; no server is started, no production port is bound, no real
request leaves the process.

All test names carry the t12 marker: ``pytest -q tests/soul_access -k t12``
must collect > 0. Transport is memory-only: the thin app is driven through
``httpx.ASGITransport`` (in-process, never a port). The sandbox release
file is deliberately set OPEN in the closed tests: a 403 there proves the
instance door (not the shared file) refused. The only key value here is a
synthetic sandbox token; production digests are never read except as a
read-only pin comparison (test 9). Storage is sandbox-only: tmp registry /
preset / release copies (never production state, never the pinned path).
A counting forwarder stand-in proves zero upstream calls; the legacy
carrier raises if ever touched. Sync tests only.

Coverage:
  a. closed door refuses even when the release file says open (403 +
     zero forwarder calls + zero carrier calls);
  b. wrong credential refuses (401, zero calls);
  c. non-rem/primary routes refuse (unknown soul route 404, unlisted
     preset 403, zero calls);
  d. restart stays closed (two fresh builds = two instances, both 403);
  e. preflight accepts pinned copies / refuses drift, missing files,
     template marker, occupied port;
  f. loopback bind is pinned (127.0.0.1:18765, no --host/--port flags).
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import queue
import sys
import threading
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.external_gateway.app import EXTERNAL_PORT  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402

SERVE_THIN_PATH = _REPO_ROOT / "scripts" / "serve_sa_thin.py"


def _load_thin_module():
    spec = importlib.util.spec_from_file_location("serve_sa_thin_t12", SERVE_THIN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


thin_mod = _load_thin_module()

# -- module-level loop (import time; same idiom as the T10/T11 files) ---------

_T12_LOOP = asyncio.new_event_loop()


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
            except BaseException as exc:
                out_q.put((False, exc))

    def run(self, coro):
        out_q = queue.Queue(maxsize=1)
        self.jobs.put((coro, out_q))
        ok, val = out_q.get()
        if ok:
            return val
        raise val


_OUTWARD_RUNNER = _Runner(_T12_LOOP, "t12-outward")


def _run(coro):
    return _OUTWARD_RUNNER.run(coro)


# -- synthetic T12 fixtures (never production identity) -----------------------

OWNER = "t12-thin-owner"
SOUL = "rem"
AGENT = "agent_rem"
SCOPE = "owner_private"
TOKEN = "t12-thin-sandbox-token"
WRONG_TOKEN = "t12-wrong-token"
TOKEN_DIGEST = digest_of(TOKEN)
PRIMARY = "primary"
STANDBY = "standby"
ALIASES = (PRIMARY, STANDBY)
MODELS = {
    PRIMARY: "sa-t12-primary",
    STANDBY: "sa-t12-standby",
}
KEY_REF = "SA_T12_THIN_KEY"
FAKE_KEY = "t12-sandbox-fake-key"


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
    req = "req-t12-%04d" % n
    key = "key-t12-%04d" % n
    return sa_schema.SoulSnapshot(
        identity=_identity(req, key),
        dedup=_dedup(AGENT, req, key, principal),
        backend_alias=alias,
        model=model,
        params=(("temperature", "0.5"), ("max_tokens", "8"),),
        budget_tokens=512,
        deadline_utc="2026-11-12T00:00:00Z",
        personality_version="personality-t12-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-11T17:00:00-05:00",
        inner_life_state="inner-t12-v1",
        projection_state="projection-t12-v1",
        recall_version="recall-t12-v1",
        recall_items=("recall-t12-item-1",),
        history_pool_version="pool-t12-v1",
        clean_history=("hist-t12-item-1",),
    )


def _envelope(snapshot=None, messages=(("user", "t12 probe"),)):
    snap = (
        snapshot
        if snapshot is not None
        else _snapshot_for(PRIMARY, MODELS[PRIMARY], 1)
    )
    return sa_api.build_envelope(
        snapshot=snap,
        messages=messages,
        created_utc="2026-11-11T17:00:00-05:00",
    )


def _write_text(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _preset_entries():
    return [
        {
            "name": alias,
            "provider": "openai",
            "model": MODELS[alias],
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": "https://t12-thin.invalid/v1/chat/completions",
            },
            "key_ref": KEY_REF,
        }
        for alias in ALIASES
    ]


def _registry_raw(token_digest=TOKEN_DIGEST):
    return {
        "owner_principal_id": OWNER,
        "backends": ["t12-thin"],
        "routes": [
            {
                "soul_alias": SOUL,
                "backend_alias": alias,
                "agent_id": AGENT,
                "public_model_id": "sa-t12-%s-%s" % (SOUL, alias),
                "backend_ref": "t12-thin",
                "context_window_tokens": 4096,
            }
            for alias in ALIASES
        ],
        "clients": [
            {
                "client_id": "client-t12-thin",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (SOUL, PRIMARY)],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _sandbox_files(tmp_path: Path, *, release_status="open"):
    """Write sandbox registry / presets / release copies. Returns paths."""
    registry_path = tmp_path / "registry.json"
    presets_path = tmp_path / "presets.json"
    release_path = tmp_path / "release.json"
    registry_path.write_text(json.dumps(_registry_raw()), encoding="utf-8")
    presets_path.write_text(
        json.dumps({"presets": _preset_entries()}), encoding="utf-8"
    )
    release_path.write_text(json.dumps({"status": release_status}), encoding="utf-8")
    return (registry_path, presets_path, release_path)


class _CountingForwarder:
    """Upstream stand-in: records every call, answers nothing (must stay 0)."""

    def __init__(self):
        self.calls = []

    def __call__(self, envelope, *, route=None, preset=None):
        self.calls.append(envelope)
        raise AssertionError("thin door must refuse before any forward")


def _build_thin_app(registry_path, presets_path, release_path, forwarder):
    return thin_mod.build_thin_app(
        str(registry_path),
        str(presets_path),
        release_path=str(release_path),
        _forwarder=forwarder,
    )


def _post(app, soul, alias, token, body):
    async def _go():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        return await client.post(
            "/%s/%s/v1/chat/completions" % (soul, alias),
            headers={"Authorization": "Bearer " + token},
            json=body,
        )

    return _run(_go())


def _post_envelope(app, soul, alias, token, n=1):
    snapshot = _snapshot_for(alias, MODELS[alias], n)
    return _post(app, soul, alias, token, sa_api.envelope_to_body(_envelope(snapshot)))


# -- T12-a: closed door refuses even when the release file says open --------

def test_t12_closed_refuses_even_when_release_open_zero_upstream(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    forwarder = _CountingForwarder()
    app = _build_thin_app(registry_path, presets_path, release_path, forwarder)
    response = _post_envelope(app, SOUL, PRIMARY, TOKEN)
    assert response.status_code == 403
    assert forwarder.calls == []


def test_t12_legacy_branch_intact_parse_before_carrier(monkeypatch, tmp_path):
    # Non-envelope bodies stay on the untouched legacy branch: a malformed
    # body refuses 400 at parse time, before any carrier is ever touched.
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    forwarder = _CountingForwarder()
    app = _build_thin_app(registry_path, presets_path, release_path, forwarder)
    response = _post(app, SOUL, PRIMARY, TOKEN, {"messages": "not-a-list"})
    assert response.status_code == 400
    assert forwarder.calls == []
    assert thin_mod.is_thin_open() is False


# -- T12-b: wrong credential refuses ------------------------------------------

def test_t12_wrong_credential_refused_zero_upstream(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    forwarder = _CountingForwarder()
    app = _build_thin_app(registry_path, presets_path, release_path, forwarder)
    response = _post_envelope(app, SOUL, PRIMARY, WRONG_TOKEN)
    assert response.status_code == 401
    assert forwarder.calls == []


# -- T12-c: non-rem/primary routes refuse --------------------------------------

def test_t12_unknown_soul_route_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    forwarder = _CountingForwarder()
    app = _build_thin_app(registry_path, presets_path, release_path, forwarder)
    response = _post_envelope(app, "mai", PRIMARY, TOKEN)
    assert response.status_code == 404
    assert forwarder.calls == []


def test_t12_unlisted_preset_refused(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    forwarder = _CountingForwarder()
    app = _build_thin_app(registry_path, presets_path, release_path, forwarder)
    # standby exists as a route + preset but is NOT in allowed_routes.
    response = _post_envelope(app, SOUL, STANDBY, TOKEN, n=2)
    assert response.status_code == 403
    assert forwarder.calls == []


# -- T12-d: restart stays closed -----------------------------------------------

def test_t12_restart_stays_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path = _sandbox_files(
        tmp_path, release_status="open"
    )
    # Two fresh builds = two instances (a restart re-executes the same code).
    first_calls = _CountingForwarder()
    second_calls = _CountingForwarder()
    first = _build_thin_app(registry_path, presets_path, release_path, first_calls)
    second = _build_thin_app(registry_path, presets_path, release_path, second_calls)
    first_response = _post_envelope(first, SOUL, PRIMARY, TOKEN, n=3)
    second_response = _post_envelope(second, SOUL, PRIMARY, TOKEN, n=4)
    assert first_response.status_code == 403
    assert second_response.status_code == 403
    assert first_calls.calls == []
    assert second_calls.calls == []
    assert thin_mod.is_thin_open() is False


# -- T12-e: preflight pins ------------------------------------------------------

class _FreeSocket:
    def __init__(self):
        self.bound = None

    def bind(self, addr):
        self.bound = addr

    def close(self):
        pass


def _occupied_factory(*args, **kwargs):
    raise OSError("t12 simulated occupation")


def _free_factory(*args, **kwargs):
    return _FreeSocket()


def test_t12_preflight_accepts_pinned_copies(tmp_path):
    registry_src = _REPO_ROOT / "configs" / "sa" / "registry.json"
    presets_src = _REPO_ROOT / "configs" / "sa" / "presets.json"
    registry_copy = tmp_path / "registry.json"
    presets_copy = tmp_path / "presets.json"
    registry_copy.write_bytes(registry_src.read_bytes())
    presets_copy.write_bytes(presets_src.read_bytes())
    assert (
        thin_mod.preflight(
            str(registry_copy), str(presets_copy), _socket_factory=_free_factory
        )
        == 0
    )


def test_t12_production_files_match_pins():
    registry_raw = (_REPO_ROOT / "configs" / "sa" / "registry.json").read_bytes()
    presets_raw = (_REPO_ROOT / "configs" / "sa" / "presets.json").read_bytes()
    assert hashlib.sha256(registry_raw).hexdigest() == thin_mod.REGISTRY_SHA256
    assert hashlib.sha256(presets_raw).hexdigest() == thin_mod.PRESETS_SHA256


def test_t12_preflight_refuses_drifted_or_missing(tmp_path):
    registry_src = _REPO_ROOT / "configs" / "sa" / "registry.json"
    presets_src = _REPO_ROOT / "configs" / "sa" / "presets.json"
    good_registry = tmp_path / "registry.json"
    good_presets = tmp_path / "presets.json"
    good_registry.write_bytes(registry_src.read_bytes())
    good_presets.write_bytes(presets_src.read_bytes())
    # One drifted byte refuses.
    drifted = tmp_path / "registry-drift.json"
    raw = bytearray(good_registry.read_bytes())
    raw[-2] ^= 0x01
    drifted.write_bytes(bytes(raw))
    assert (
        thin_mod.preflight(
            str(drifted), str(good_presets), _socket_factory=_free_factory
        )
        == 2
    )
    # Missing files refuse.
    assert (
        thin_mod.preflight(
            str(tmp_path / "no-such-registry.json"),
            str(good_presets),
            _socket_factory=_free_factory,
        )
        == 2
    )
    assert (
        thin_mod.preflight(
            str(good_registry),
            str(tmp_path / "no-such-presets.json"),
            _socket_factory=_free_factory,
        )
        == 2
    )


def test_t12_preflight_marker_refuses_before_schema(monkeypatch, tmp_path):
    marker_registry = tmp_path / "registry-marker.json"
    marker_registry.write_text(
        json.dumps(_registry_raw()) + "\n<!-- do-not-deploy -->\n", encoding="utf-8"
    )
    good_presets = tmp_path / "presets.json"
    good_presets.write_bytes(
        (_REPO_ROOT / "configs" / "sa" / "presets.json").read_bytes()
    )
    monkeypatch.setattr(
        thin_mod,
        "REGISTRY_SHA256",
        hashlib.sha256(marker_registry.read_bytes()).hexdigest(),
    )
    monkeypatch.setattr(
        thin_mod,
        "PRESETS_SHA256",
        hashlib.sha256(good_presets.read_bytes()).hexdigest(),
    )
    assert (
        thin_mod.preflight(
            str(marker_registry), str(good_presets), _socket_factory=_free_factory
        )
        == 2
    )


def test_t12_preflight_refuses_occupied_port(tmp_path):
    registry_src = _REPO_ROOT / "configs" / "sa" / "registry.json"
    presets_src = _REPO_ROOT / "configs" / "sa" / "presets.json"
    registry_copy = tmp_path / "registry.json"
    presets_copy = tmp_path / "presets.json"
    registry_copy.write_bytes(registry_src.read_bytes())
    presets_copy.write_bytes(presets_src.read_bytes())
    assert (
        thin_mod.preflight(
            str(registry_copy), str(presets_copy), _socket_factory=_occupied_factory
        )
        == 2
    )


# -- T12-f: loopback bind is pinned ---------------------------------------------

def test_t12_loopback_pinned():
    assert thin_mod.THIN_HOST == "127.0.0.1"
    assert thin_mod.THIN_PORT == 18765
    assert thin_mod.THIN_PORT == EXTERNAL_PORT
    option_strings = [
        text
        for action in thin_mod.build_parser()._actions
        for text in action.option_strings
    ]
    assert "--host" not in option_strings
    assert "--port" not in option_strings
    assert thin_mod.is_thin_open() is False


def test_t12_closed_copy_matches_frozen_bridge():
    assert thin_mod._CLOSED_MESSAGE == "soul-access inbound closed"
    with pytest.raises(sa_bridge.LiveRefused) as exc_info:
        thin_mod.build_thin_closed_handler(_forwarder=_CountingForwarder())(
            object(), route=None, preset=None
        )
    assert exc_info.value.status == 403
