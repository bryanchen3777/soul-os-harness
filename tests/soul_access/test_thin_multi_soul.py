"""THIN-ROUTING-MULTI-SOUL-1 thin multi-soul routing acceptance (sandbox only).

Decision-complete scope (mastermind Sol approved): product change is
confined to ``scripts/serve_sa_thin.py`` (per-soul overlays + per-soul
apps + first-segment dispatch) and ``configs/sa/registry.json`` (mai x4
plus ruka/akane/mahiru x4 bindings + widened allowed_routes); ``presets.json``,
frozen gateway assembly, ``:8000`` and every Frozen contract are untouched.

Transport is memory-only: the thin app is driven through
``httpx.ASGITransport`` (in-process, never a port). The upstream carrier
is a recording mock (``PresetRoutingBackend`` patched at the thin module
attribute) so no real request ever leaves the process and the injected
persona text plus the resolved preset can be asserted per call. Storage
is sandbox-only: tmp registry / presets / release copies (never
production state, never the pinned path). The only key material is a
synthetic sandbox token minted by ``issue_credential``.

Coverage (ticket acceptance mapping, probes A-E):
  A. /mai/primary carries the Mai persona (contains Sakurajima Mai
     markers, zero Rem markers);
  A2. /ruka/primary carries the Ruka persona (contains Ruka markers,
     zero other-soul markers); /akane/primary and /mahiru/primary
     likewise with their own markers;
  B. /rem/glm-5.3-flash resolves exactly the glm preset (name + model)
     and carries the Rem persona (contains Rem markers, zero Mai markers);
  C. alternating souls keep their own persona (no cross-smell between
     consecutive calls), extended to all five souls;
  D. unlisted-but-registered route refuses 403, unregistered route
     refuses 404, both with zero new upstream calls (403 probe: rem/mimo
     and ruka/glm; 404 probe: akane/glm and mai/mimo with no binding);
  E. pin guard: production registry/presets bytes still match the pinned
     dual hashes and preflight accepts the pinned copies; production
     registry binds all five souls x4 presets with six allowed routes.
"""

from __future__ import annotations

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
from src.interfaces.credentials import issue_credential  # noqa: E402

OWNER = "multi-soul-owner"
SCOPE = "owner_private"
KEY_REF = "SA_THIN_MULTI_SOUL_KEY"
FAKE_KEY = "multi-soul-sandbox-fake-key"

PRIMARY = "primary"
GLM = "glm-5.3-flash"
MIMO = "mimo-v2.6-flash"

PRIMARY_MODEL = "sandbox-model-primary"
GLM_MODEL = "sandbox-model-glm-5.3-flash"
MIMO_MODEL = "sandbox-model-mimo"

RUN_ID = "run-multi-soul-1"
DOOR_TOKEN = "multi-soul-control-token"

# Persona markers (verified mutually exclusive across the five files):
# agent_mai.md carries these, agent_rem.md carries none of them.
MAI_MARKERS = ("櫻島麻衣", "兔女郎")
# agent_rem.md carries these, agent_mai.md carries none of them.
REM_MARKERS = ("雷姆", "羅茲瓦爾公館")
# Mai-side absence markers (agent_mai.md carries none of these).
MAI_ABSENT = ("雷姆", "羅茲瓦爾")
# Rem-side absence marker: agent_rem.md never mentions the bunny-girl
# anchor (its single Mai reference is an explicit do-not-imitate rule,
# so only the positive Mai anchor is asserted absent here).
REM_ABSENT = ("兔女郎",)
# agent_ruka.md carries these; rem/mai/akane/mahiru files carry none.
# (agent_ruka.md mentions 雷姆醬 twice in a do-not-imitate rule, so 雷姆
# is NOT asserted absent -- same idiom as REM_ABSENT above.)
RUKA_MARKERS = ("更科瑠夏", "瑠夏")
RUKA_ABSENT = ("櫻島麻衣", "兔女郎", "羅茲瓦爾", "黑川茜", "星野", "真昼")
# agent_akane.md carries these; rem/mai/ruka/mahiru files carry none.
# (agent_akane.md mentions 雷姆醬 twice in a naming rule, so 雷姆 is NOT
# asserted absent.)
AKANE_MARKERS = ("あかね", "星野")
AKANE_ABSENT = ("櫻島麻衣", "兔女郎", "羅茲瓦爾", "瑠夏", "真昼")
# agent_mahiru.md carries these; the other four files carry none.
MAHIRU_MARKERS = ("椎名真昼", "真昼")
MAHIRU_ABSENT = ("雷姆", "羅茲瓦爾", "櫻島麻衣", "兔女郎", "瑠夏", "黑川茜", "星野", "あかね")


def _registry_raw(token_digest):
    def _route(soul, alias):
        return {
            "soul_alias": soul,
            "backend_alias": alias,
            "agent_id": "agent_" + soul,
            "public_model_id": "multi-%s-%s" % (soul, alias),
            "backend_ref": "multi",
            "context_window_tokens": 4096,
        }

    return {
        "owner_principal_id": OWNER,
        "backends": ["multi"],
        "routes": [
            _route("rem", PRIMARY),
            _route("rem", GLM),
            _route("rem", MIMO),
            _route("mai", PRIMARY),
            _route("mai", GLM),
            _route("ruka", PRIMARY),
            _route("ruka", GLM),
            _route("akane", PRIMARY),
            _route("mahiru", PRIMARY),
        ],
        "clients": [
            {
                "client_id": "client-multi-soul",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": [
                    "rem/%s" % PRIMARY,
                    "mai/%s" % PRIMARY,
                    "rem/%s" % GLM,
                    "ruka/%s" % PRIMARY,
                    "akane/%s" % PRIMARY,
                    "mahiru/%s" % PRIMARY,
                ],
                "scope": SCOPE,
                "allow_persistent_memory": True,
                "credential_sha256": token_digest,
            }
        ],
    }


def _preset_entries():
    return [
        {
            "name": PRIMARY,
            "provider": "openai",
            "model": PRIMARY_MODEL,
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": "https://multi-soul.invalid/v1/chat/completions",
            },
            "key_ref": KEY_REF,
        },
        {
            "name": GLM,
            "provider": "openai",
            "model": GLM_MODEL,
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": "https://multi-soul.invalid/v1/chat/completions",
            },
            "key_ref": KEY_REF,
        },
        {
            "name": MIMO,
            "provider": "openai",
            "model": MIMO_MODEL,
            "params": {
                "temperature": 0.5,
                "max_tokens": 8,
                "base_url": "https://multi-soul.invalid/v1/chat/completions",
            },
            "key_ref": KEY_REF,
        },
    ]


def _sandbox_files(tmp_path: Path):
    registry_path = tmp_path / "registry.json"
    presets_path = tmp_path / "presets.json"
    release_path = tmp_path / "release.json"
    token, digest = issue_credential()
    registry_path.write_text(json.dumps(_registry_raw(digest)), encoding="utf-8")
    presets_path.write_text(
        json.dumps({"presets": _preset_entries()}), encoding="utf-8"
    )
    release_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    return (registry_path, presets_path, release_path, token)


def _pin_to_files(monkeypatch, registry_path: Path, presets_path: Path):
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


class _RecordingBackend:
    """Upstream stand-in: records every call, answers fixed text."""

    def __init__(self, calls):
        self.calls = calls

    def complete(self, route, preset, messages, params, passthrough=None):
        first = messages[0] if messages else None
        self.calls.append(
            {
                "soul": route.soul_alias,
                "backend_alias": route.backend_alias,
                "agent_id": route.agent_id,
                "preset_name": preset.name,
                "preset_model": preset.model,
                "first_role": getattr(first, "role", None),
                "first_content": getattr(first, "content", None),
                "n_messages": len(messages),
            }
        )
        return "multi-soul-reply"


# -- module-level loop driver for ASGI loopback (door-mech idiom) -------------

_LOOP = asyncio.new_event_loop()


class _Runner:
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


_RUNNER = _Runner(_LOOP, "multi-soul-outward")


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

    return _RUNNER.run(_go())


def _chat_body(preset, text="multi-soul probe"):
    return {"model": preset, "messages": [{"role": "user", "content": text}]}


def _open_app(monkeypatch, tmp_path):
    """Build the thin app with doors opened (install -> ready -> open)."""
    monkeypatch.setenv(KEY_REF, FAKE_KEY)
    registry_path, presets_path, release_path, token = _sandbox_files(tmp_path)
    _pin_to_files(monkeypatch, registry_path, presets_path)
    calls: list = []
    monkeypatch.setattr(
        thin_mod, "PresetRoutingBackend", lambda: _RecordingBackend(calls)
    )
    control = thin_mod.ThinControl(
        run_id=RUN_ID,
        token=DOOR_TOKEN,
        control_path=str(tmp_path / "control.json"),
        release_path=str(release_path),
        registry_path=str(registry_path),
        presets_path=str(presets_path),
    )
    app = thin_mod.build_thin_app(
        str(registry_path),
        str(presets_path),
        release_path=str(release_path),
        control=control,
    )
    assert control.install_registry()[0] == 200
    assert control.ready()[0] == 200
    assert control.open_door()[0] == 200
    return (app, token, calls)


def _assert_mai_persona(call):
    assert call["soul"] == "mai"
    assert call["agent_id"] == "agent_mai"
    assert call["first_role"] == "system"
    content = call["first_content"]
    assert isinstance(content, str)
    for marker in MAI_MARKERS:
        assert marker in content
    for marker in MAI_ABSENT:
        assert marker not in content


def _assert_rem_persona(call):
    assert call["soul"] == "rem"
    assert call["agent_id"] == "agent_rem"
    assert call["first_role"] == "system"
    content = call["first_content"]
    assert isinstance(content, str)
    for marker in REM_MARKERS:
        assert marker in content
    for marker in REM_ABSENT:
        assert marker not in content


def _assert_ruka_persona(call):
    assert call["soul"] == "ruka"
    assert call["agent_id"] == "agent_ruka"
    assert call["first_role"] == "system"
    content = call["first_content"]
    assert isinstance(content, str)
    for marker in RUKA_MARKERS:
        assert marker in content
    for marker in RUKA_ABSENT:
        assert marker not in content


def _assert_akane_persona(call):
    assert call["soul"] == "akane"
    assert call["agent_id"] == "agent_akane"
    assert call["first_role"] == "system"
    content = call["first_content"]
    assert isinstance(content, str)
    for marker in AKANE_MARKERS:
        assert marker in content
    for marker in AKANE_ABSENT:
        assert marker not in content


def _assert_mahiru_persona(call):
    assert call["soul"] == "mahiru"
    assert call["agent_id"] == "agent_mahiru"
    assert call["first_role"] == "system"
    content = call["first_content"]
    assert isinstance(content, str)
    for marker in MAHIRU_MARKERS:
        assert marker in content
    for marker in MAHIRU_ABSENT:
        assert marker not in content


# -- probe A: /mai/primary carries the Mai persona, zero Rem smell ---------


def test_multi_soul_a_mai_primary_carries_mai_persona(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    resp = _post(app, "mai", PRIMARY, token, _chat_body(PRIMARY))
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "multi-soul-reply"
    assert len(calls) == 1
    call = calls[0]
    assert call["preset_name"] == PRIMARY
    assert call["preset_model"] == PRIMARY_MODEL
    _assert_mai_persona(call)


# -- probe A2: new souls carry their own persona, zero other-soul smell ---


def test_multi_soul_a2_ruka_primary_carries_ruka_persona(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    resp = _post(app, "ruka", PRIMARY, token, _chat_body(PRIMARY))
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "multi-soul-reply"
    assert len(calls) == 1
    call = calls[0]
    assert call["preset_name"] == PRIMARY
    assert call["preset_model"] == PRIMARY_MODEL
    _assert_ruka_persona(call)


def test_multi_soul_a2_akane_primary_carries_akane_persona(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    resp = _post(app, "akane", PRIMARY, token, _chat_body(PRIMARY))
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "multi-soul-reply"
    assert len(calls) == 1
    call = calls[0]
    assert call["preset_name"] == PRIMARY
    assert call["preset_model"] == PRIMARY_MODEL
    _assert_akane_persona(call)


def test_multi_soul_a2_mahiru_primary_carries_mahiru_persona(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    resp = _post(app, "mahiru", PRIMARY, token, _chat_body(PRIMARY))
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "multi-soul-reply"
    assert len(calls) == 1
    call = calls[0]
    assert call["preset_name"] == PRIMARY
    assert call["preset_model"] == PRIMARY_MODEL
    _assert_mahiru_persona(call)


# -- probe B: /rem/glm-5.3-flash resolves the GLM preset + Rem persona ----


def test_multi_soul_b_rem_glm_resolves_glm_preset_with_rem_persona(
    monkeypatch, tmp_path
):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    resp = _post(app, "rem", GLM, token, _chat_body(GLM))
    assert resp.status_code == 200
    assert len(calls) == 1
    call = calls[0]
    assert call["preset_name"] == GLM
    assert call["preset_model"] == GLM_MODEL
    _assert_rem_persona(call)


# -- probe C: alternating souls keep their own persona (no cross-smell) -----


def test_multi_soul_c_alternating_souls_keep_own_persona(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    for soul, alias in (("mai", PRIMARY), ("rem", GLM), ("mai", PRIMARY)):
        resp = _post(app, soul, alias, token, _chat_body(alias))
        assert resp.status_code == 200
    assert [c["soul"] for c in calls] == ["mai", "rem", "mai"]
    _assert_mai_persona(calls[0])
    _assert_rem_persona(calls[1])
    _assert_mai_persona(calls[2])
    assert calls[0]["preset_name"] == PRIMARY
    assert calls[1]["preset_name"] == GLM
    assert calls[1]["preset_model"] == GLM_MODEL


# -- probe C2: alternating across all five souls keeps each persona ------


def test_multi_soul_c2_five_soul_alternation_no_cross_smell(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    seq = (
        ("ruka", PRIMARY),
        ("akane", PRIMARY),
        ("mahiru", PRIMARY),
        ("mai", PRIMARY),
        ("rem", GLM),
        ("ruka", PRIMARY),
    )
    for soul, alias in seq:
        resp = _post(app, soul, alias, token, _chat_body(alias))
        assert resp.status_code == 200
    assert [c["soul"] for c in calls] == [s for (s, _a) in seq]
    _assert_ruka_persona(calls[0])
    _assert_akane_persona(calls[1])
    _assert_mahiru_persona(calls[2])
    _assert_mai_persona(calls[3])
    _assert_rem_persona(calls[4])
    _assert_ruka_persona(calls[5])
    assert calls[4]["preset_name"] == GLM
    assert calls[4]["preset_model"] == GLM_MODEL


# -- probe D: unlisted route 403, unregistered route 404, zero upstream -----


def test_multi_soul_d_auth_matrix_403_404_zero_upstream(monkeypatch, tmp_path):
    app, token, calls = _open_app(monkeypatch, tmp_path)
    # rem/mimo-v2.6-flash is registered and has a preset but is NOT in
    # allowed_routes: 403 without touching upstream.
    resp = _post(app, "rem", MIMO, token, _chat_body(MIMO))
    assert resp.status_code == 403
    # ruka/glm-5.3-flash is registered and has a preset but is NOT in
    # allowed_routes: 403 without touching upstream.
    resp = _post(app, "ruka", GLM, token, _chat_body(GLM))
    assert resp.status_code == 403
    # akane/glm-5.3-flash has no route binding at all: 404.
    resp = _post(app, "akane", GLM, token, _chat_body(GLM))
    assert resp.status_code == 404
    # mai/mimo-v2.6-flash has no route binding at all: 404.
    resp = _post(app, "mai", MIMO, token, _chat_body(MIMO))
    assert resp.status_code == 404
    assert calls == []


# -- probe E: pin guard over the production files ----------------------------


class _FreeSocket:
    def __init__(self):
        self.bound = None

    def bind(self, addr):
        self.bound = addr

    def close(self):
        pass


def _free_factory(*args, **kwargs):
    return _FreeSocket()


def test_multi_soul_e_production_pins_match_and_preflight_passes(tmp_path):
    registry_raw = (_REPO_ROOT / "configs" / "sa" / "registry.json").read_bytes()
    presets_raw = (_REPO_ROOT / "configs" / "sa" / "presets.json").read_bytes()
    assert hashlib.sha256(registry_raw).hexdigest() == thin_mod.REGISTRY_SHA256
    assert hashlib.sha256(presets_raw).hexdigest() == thin_mod.PRESETS_SHA256
    registry = json.loads(registry_raw.decode("utf-8"))
    souls = {(r["soul_alias"], r["backend_alias"]) for r in registry["routes"]}
    for soul in ("rem", "mai", "ruka", "akane", "mahiru"):
        for alias in ("primary", "minimax-m3.1-flash", "glm-5.3-flash", "mimo-v2.6-flash"):
            assert (soul, alias) in souls
    assert len(registry["routes"]) == 20
    for route in registry["routes"]:
        assert route["backend_ref"] == "sa-ollama-cloud"
        assert route["agent_id"] == "agent_" + route["soul_alias"]
        assert route["context_window_tokens"] == 4096
    allowed = registry["clients"][0]["allowed_routes"]
    assert allowed == [
        "rem/primary",
        "rem/minimax-m3.1-flash",
        "rem/glm-5.3-flash",
        "rem/mimo-v2.6-flash",
        "mai/primary",
        "mai/minimax-m3.1-flash",
        "mai/glm-5.3-flash",
        "mai/mimo-v2.6-flash",
        "ruka/primary",
        "ruka/minimax-m3.1-flash",
        "ruka/glm-5.3-flash",
        "ruka/mimo-v2.6-flash",
        "akane/primary",
        "akane/minimax-m3.1-flash",
        "akane/glm-5.3-flash",
        "akane/mimo-v2.6-flash",
        "mahiru/primary",
        "mahiru/minimax-m3.1-flash",
        "mahiru/glm-5.3-flash",
        "mahiru/mimo-v2.6-flash",
    ]
    registry_copy = tmp_path / "registry.json"
    presets_copy = tmp_path / "presets.json"
    registry_copy.write_bytes(registry_raw)
    presets_copy.write_bytes(presets_raw)
    assert (
        thin_mod.preflight(
            str(registry_copy), str(presets_copy), _socket_factory=_free_factory
        )
        == 0
    )
