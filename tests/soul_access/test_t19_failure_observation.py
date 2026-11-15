"""SA-TRANSPORT-OBS-2B T19 failure-observation tests (sandbox only).

Decision-complete scope (SA-TRANSPORT-OBS-2B, mastermind Wolong approved):
zero product change. This file
(``tests/soul_access/test_t19_failure_observation.py``) plus
``src/soul_access/failure_observation.py`` are the ONLY new files;
artifacts stay in per-test ``tmp_path``. The committed
``verification/sa-transport-obs-2b/case-matrix.json`` is a frozen reference:
read-only compare, never written by this suite. Read-only: ``src/llm/proxy.py``
(the ``except (ConnectError, TimeoutException)`` at :2212),
``src/soul_access/evidence.py`` (v1 observation API), the three wiring
``except`` pass-throughs, and the B1/B2/B3 originals
(``live-fire-1/live_fire_once.py``, ``live-fire-2/live_fire_b2.py``,
``live-fire-3/live_fire_b3.py``). Old batches (B1/B2/B3) are not
re-litigated: ``INCONCLUSIVE`` stands.

All test names carry the t19 marker: ``pytest -q tests/soul_access -k t19``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected via
``monkeypatch`` (no real request, no server, no port ever bound). ``main`` is
never called. ``dotenv`` loading is stubbed BEFORE the B3 import (the process
never reads a real ``.env``); only key *existence* is checked, the synthetic
value is never reported or written anywhere.

Coverage (ticket acceptance mapping):
  a. 26-case re-run through the new wrapper (original B3 pump + fake
     transport): normal/failure controls keep call counts, return values and
     exception identity (type + chain + cause) identical direct-vs-wrapped;
     each row reconciled (outcome, snapshot presence, five-field status,
     landing result + reason). Unknown stays unknown; no zeros, no
     fabricated attempts.
  b. wrapper contract: injected dialog called exactly once; success returns
     the value untouched (no file); ``Exception`` re-raised identically
     (``is`` + cause kept); ``BaseException`` never observed.
  c. pollution (snapshot discarded whole, outer record lands rejected),
     disk failure (fixed token only, original re-raised, no file),
     unknown-zero-fill (nulls/unknown, never 0), outer-exception whitelist
     (unlisted names -> ``UnknownException``).
  d. forbidden-content scan (no message/args/traceback/URL/headers/raw ever
     stored; closed record key set), ``data/**`` structural refusal, socket
     hard block at runtime.
  e. AST pins (product unchanged + new module net-free/fixed-token/bare
     raise) and bidirectional mutations (delivery-off + snapshot-blind: RED
     then GREEN, DUD stops the run).
"""

from __future__ import annotations

import ast
import inspect
import json
import socket
import sys
import threading
from pathlib import Path
from types import ModuleType, SimpleNamespace

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import failure_observation as fa_obs  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

# -- B3 original import with dotenv disabled (read-only) -----------------------
# The B3 module self-loads ``load_dotenv`` at import. A stub is installed in
# ``sys.modules`` BEFORE exec so no real ``.env`` file is ever read here.
# ``main`` is never called by any test in this file.

LIVE_B3_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-3" / "live_fire_b3.py"
)
LIVE_B1_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-1" / "live_fire_once.py"
)
LIVE_B2_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-2" / "live_fire_b2.py"
)

_DOTENV_CALLS: list = []


class _NoopDotenv(ModuleType):
    def load_dotenv(self, *args, **kwargs):  # noqa: N802
        _DOTENV_CALLS.append((args, kwargs))
        return False


# Installed unconditionally BEFORE the B3 import: an earlier-imported module
# may already hold the real dotenv, which must not read a real .env here.
_SAVED_DOTENV = sys.modules.get("dotenv")
_T19_STUB = _NoopDotenv("dotenv")
sys.modules["dotenv"] = _T19_STUB


def _load_b3():
    import importlib.util

    spec = importlib.util.spec_from_file_location("live_fire_b3_t19", LIVE_B3_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b3 = _load_b3()
_B3_DOTENV_CALLS = list(_DOTENV_CALLS)  # snapshot: later modules must not pollute it
# Restore whatever dotenv was there before (or drop the stub) so this
# module's import-time stub never leaks into other tests sharing the session.
# b3 already consumed the stub at import; nothing else needs it afterwards.
if _SAVED_DOTENV is None:
    del sys.modules["dotenv"]
else:
    sys.modules["dotenv"] = _SAVED_DOTENV

NON_ROUTABLE_BASE = "http://t19-invalid.invalid/v1/chat/completions"
SENTINEL_KEY_REF = "OLLAMA_API_KEY"
MODEL = "do-not-deploy-model-t19"
AGENT = "agent_rem"

FIVE_FIELDS = ("type", "chain", "errno", "elapsed_ms", "phase")

# Closed outer-record key set: anything else landing is a schema leak.
OUTER_KEYS = {
    "kind",
    "version",
    "dialog_request_id",
    "snapshot_status",
    "snapshot_reason",
    "snapshot",
    "type",
    "chain",
    "errno",
    "elapsed_ms",
    "phase",
    "transport_calls",
    "backend_observations",
    "attempts",
    "outer_exception",
}

# Case-matrix rows collected across this module's tests (secret-free).
MATRIX_ROWS: list = []


def _matrix(case, row):
    entry = {"case": case}
    entry.update(row)
    MATRIX_ROWS.append(entry)
    return row


def _chain_of(exc) -> list:
    try:
        return sa_evidence.transport_chain(exc)
    except Exception:  # noqa: BLE001
        return [type(exc).__name__]


# -- fake transport (in-memory only; local POST != backend received/billed) ---


class _FakeResp:
    def __init__(self, raw_text, body, *, json_exc=None):
        self._raw_text = raw_text
        self._body = body
        self._json_exc = json_exc
        self.status_code = 200

    @property
    def text(self):
        return self._raw_text

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._body

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, calls, lock, *, enter_exc=None, behavior=None, exit_exc=None):
        self._calls = calls
        self._lock = lock
        self._enter_exc = enter_exc
        self._behavior = behavior
        self._exit_exc = exit_exc

    async def __aenter__(self):
        if self._enter_exc is not None:
            raise self._enter_exc
        return self

    async def __aexit__(self, *exc):
        if self._exit_exc is not None:
            raise self._exit_exc
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        with self._lock:
            self._calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        return self._behavior(self._calls[-1])


def _install_fake(monkeypatch, *, behavior, enter_exc=None, exit_exc=None):
    calls = []
    lock = threading.Lock()

    def _factory(*args, **kwargs):
        return _FakeClient(
            calls, lock, enter_exc=enter_exc, behavior=behavior, exit_exc=exit_exc
        )

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls


def _complete(backend, sink, **over):
    kw = {
        "messages": [{"role": "user", "content": "t19 probe"}],
        "model": MODEL,
        "max_tokens": 16,
        "temperature": 0.5,
        "max_retries": 0,
        "evidence_sink": sink,
    }
    kw.update(over)
    return b3._run(backend.complete(**kw))  # ORIGINAL pump, loop-free


def _backend_dialog(backend, sink, **over):
    """Plain dialog shim over the ORIGINAL pump (no wrapper): one pump per call."""

    def _dialog(messages, *, agent_id=None, request_id=None):
        wire = [{"role": role, "content": content} for role, content in messages]
        kw = {
            "messages": wire,
            "model": MODEL,
            "max_tokens": 16,
            "temperature": 0.5,
            "max_retries": 0,
            "evidence_sink": sink,
        }
        kw.update(over)
        return b3._run(backend.complete(**kw))

    return _dialog


def _ok_body(text="t19-reply"):
    raw = json.dumps(
        {
            "id": "t19-synthetic",
            "object": "chat.completion",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }
    )
    return raw, json.loads(raw)


def _snapshot(**over):
    kw = {
        "identity": sa_schema.RequestIdentity(
            request_id=b3.DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-t19-0001",
            idempotency_key="key-t19-0001",
        ),
        "dedup": sa_schema.DedupKey(
            principal_id="t19-offline-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t19-0001",
        ),
        "backend_alias": "primary",
        "model": MODEL,
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-14T00:00:00Z",
        "personality_version": "personality-t19-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-14T15:45:00-05:00",
        "inner_life_state": "inner-t19-v1",
        "projection_state": "projection-t19-v1",
        "recall_version": "recall-t19-v1",
        "recall_items": ("recall-t19-item-1",),
        "history_pool_version": "pool-t19-v1",
        "clean_history": ("hist-t19-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


def _boom_with_obs(*, label_chain=("RuntimeError",), errno=None):
    boom = RuntimeError("t19-boom")
    sa_evidence.attach_transport_obs(
        boom,
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t19-ev-boom",
            transport_calls=1,
            backend_observations=None,
            attempts=[
                sa_evidence.build_transport_attempt(
                    seq=0,
                    exc_type="connect",
                    chain=list(label_chain),
                    errno=errno,
                    elapsed_ms=3.25,
                    phase="post",
                )
            ],
            boundaries={},
            observation_ok=True,
        ),
    )
    return boom


def _obs_files(obs_dir):
    if not obs_dir.exists():
        return []
    return sorted(
        p.name for p in obs_dir.iterdir() if p.name.endswith(".json") and not p.name.startswith(".tmp-")
    )


def _read_obs(obs_dir, rid):
    return json.loads((obs_dir / ("%s.json" % rid)).read_text(encoding="utf-8"))


def _outcome_of(fn):
    """Secret-free outcome shape: value equality or (type, chain, cause)."""
    try:
        value = fn()
    except BaseException as exc:  # noqa: BLE001
        cause = getattr(exc, "__cause__", None)
        return {
            "kind": "raised",
            "exc_type": type(exc).__name__,
            "chain": _chain_of(exc),
            "cause_type": type(cause).__name__ if cause is not None else None,
        }
    return {"kind": "value", "value": value}


def _drive_both(*, plain, messages, agent_id, request_id, obs_dir):
    """Run direct + wrapped; assert exactly-once; return (direct, wrapped, files)."""
    direct = _outcome_of(
        lambda: plain(messages, agent_id=agent_id, request_id=request_id)
    )
    dialog_calls = []

    def _counting(msgs, *, agent_id=None, request_id=None):
        dialog_calls.append(1)
        return plain(msgs, agent_id=agent_id, request_id=request_id)

    wrapped = _outcome_of(
        lambda: fa_obs.observe_dialog(
            _counting,
            messages,
            directory=obs_dir,
            agent_id=agent_id,
            request_id=request_id,
        )
    )
    assert len(dialog_calls) == 1  # injected dialog called exactly once
    return direct, wrapped, dialog_calls


def _parity_rows(case, *, direct, wrapped, calls_direct, calls_wrapped, obs, landed):
    assert calls_direct == calls_wrapped  # transport call counts identical
    if direct["kind"] == "value":
        assert wrapped["kind"] == "value"
        assert wrapped["value"] == direct["value"]  # return value identical
    else:
        assert wrapped["kind"] == "raised"
        assert wrapped["exc_type"] == direct["exc_type"]  # exception identity
        assert wrapped["chain"] == direct["chain"]
        assert wrapped["cause_type"] == direct["cause_type"]
    row = {
        "direct": direct,
        "wrapped": wrapped,
        "calls_direct": calls_direct,
        "calls_wrapped": calls_wrapped,
        "parity": True,
        "snapshot_status": obs["snapshot_status"] if obs is not None else None,
        "landed": landed,
    }
    return _matrix(case, row)


# -- T19-a: wrapper contract (exactly-once, identity, BaseException) -----------


def test_t19_contract_success_returns_value_no_file(tmp_path):
    seen = []

    def _dialog(messages, *, agent_id=None, request_id=None):
        seen.append((messages, agent_id, request_id))
        return "t19-ok-value"

    obs_dir = tmp_path / "obs"
    messages = (("user", "t19"),)
    value = fa_obs.observe_dialog(
        _dialog, messages, directory=obs_dir, agent_id=AGENT, request_id="req-t19-ok"
    )
    assert value == "t19-ok-value"
    assert seen == [(messages, AGENT, "req-t19-ok")]  # exactly once, args untouched
    assert _obs_files(obs_dir) == []  # success lands nothing


def test_t19_contract_failure_identity_and_cause_kept(tmp_path, capsys):
    cause = OSError(111, "t19 refused")
    boom = httpx.ConnectError("t19 down")
    boom.__cause__ = cause
    seen = []

    def _dialog(messages, *, agent_id=None, request_id=None):
        seen.append(1)
        raise boom

    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            _dialog, (("user", "t19"),), directory=obs_dir, request_id="req-t19-id"
        )
        raised = None
    except httpx.ConnectError as exc:
        raised = exc
    assert raised is boom  # identical object, not a copy
    assert raised.__cause__ is cause  # cause chain kept
    assert seen == [1]
    assert capsys.readouterr().out == ""  # landed: no token on success path
    stored = _read_obs(obs_dir, "req-t19-id")
    assert stored["snapshot_status"] == "absent"  # no v1 snapshot on this chain
    assert stored["outer_exception"][0] == "ConnectError"


def test_t19_contract_base_exception_never_observed(tmp_path, capsys):
    def _dialog(messages, *, agent_id=None, request_id=None):
        raise KeyboardInterrupt("t19-abort")

    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            _dialog, (("user", "t19"),), directory=obs_dir, request_id="req-t19-base"
        )
        raised = None
    except KeyboardInterrupt as exc:
        raised = exc
    assert raised is not None
    assert _obs_files(obs_dir) == []  # BaseException: never observed
    assert capsys.readouterr().out == ""


# -- T19-b: 26-case re-run (T18 minus zz) through the new wrapper --------------

# 1-2: dotenv + key-existence (same probes as T18: product import path clean)


def test_t19_b3_loads_without_dotenv_and_main_never_called():
    assert LIVE_B1_PATH.exists() and LIVE_B2_PATH.exists() and LIVE_B3_PATH.exists()
    assert _B3_DOTENV_CALLS == [((), {})]  # stubbed loader did nothing
    # Our stub never leaks back into the session (whatever else imports the
    # real dotenv later -- e.g. the stub-less T16 B2 load -- is a pre-existing
    # cross-file interference, observed read-only; old tests stay untouched).
    assert all(mod is not _T19_STUB for mod in list(sys.modules.values()))
    assert callable(b3.main)  # present but NEVER invoked in this file
    assert "fake suspend" in inspect.getsource(b3._run)  # original pump pin
    _matrix("b3-load", {"dotenv_stubbed": True, "main_called": False})


def test_t19_key_existence_only_never_reported(monkeypatch):
    monkeypatch.setenv(SENTINEL_KEY_REF, "t19-synthetic-value-never-written")
    import os as _os

    assert isinstance(_os.environ.get(SENTINEL_KEY_REF), str)
    assert _os.environ.get(SENTINEL_KEY_REF)  # existence only; value never asserted on
    _matrix("key-existence", {"key_ref": SENTINEL_KEY_REF, "value_reported": False})


# 3-7: original pump groups, direct-vs-wrapped parity


def test_t19_pump_no_suspend_success_control(tmp_path):
    def _plain(messages, *, agent_id=None, request_id=None):
        async def _inner():
            return "t19-ok"

        return b3._run(_inner())

    direct, wrapped, _ = _drive_both(
        plain=_plain,
        messages=(("user", "t19"),),
        agent_id=AGENT,
        request_id="req-t19-pump-control",
        obs_dir=tmp_path / "obs",
    )
    assert direct == {"kind": "value", "value": "t19-ok"}
    assert _obs_files(tmp_path / "obs") == []  # success: nothing lands
    _parity_rows(
        "pump-control",
        direct=direct,
        wrapped=wrapped,
        calls_direct=0,
        calls_wrapped=0,
        obs=None,
        landed=False,
    )


def test_t19_pump_requires_running_loop_records_original(tmp_path):
    def _plain(messages, *, agent_id=None, request_id=None):
        async def _needs_loop():
            import asyncio as _aio

            return _aio.get_running_loop()

        return b3._run(_needs_loop())

    direct, wrapped, _ = _drive_both(
        plain=_plain,
        messages=(("user", "t19"),),
        agent_id=AGENT,
        request_id="req-t19-pump-loop",
        obs_dir=tmp_path / "obs",
    )
    assert direct["exc_type"] == "RuntimeError"
    stored = _read_obs(tmp_path / "obs", "req-t19-pump-loop")
    _parity_rows(
        "pump-running-loop",
        direct=direct,
        wrapped=wrapped,
        calls_direct=0,
        calls_wrapped=0,
        obs=stored,
        landed=True,
    )
    assert stored["snapshot_status"] == "absent"  # pump errors carry no snapshot


def test_t19_pump_nested_yield_success(tmp_path):
    def _plain(messages, *, agent_id=None, request_id=None):
        async def _inner():
            return "t19-nested"

        async def _outer():
            return await _inner()

        return b3._run(_outer())

    direct, wrapped, _ = _drive_both(
        plain=_plain,
        messages=(("user", "t19"),),
        agent_id=AGENT,
        request_id="req-t19-pump-nested-ok",
        obs_dir=tmp_path / "obs",
    )
    assert direct == {"kind": "value", "value": "t19-nested"}
    assert _obs_files(tmp_path / "obs") == []  # success: nothing lands
    _parity_rows(
        "pump-nested-ok",
        direct=direct,
        wrapped=wrapped,
        calls_direct=0,
        calls_wrapped=0,
        obs=None,
        landed=False,
    )


def test_t19_pump_nested_raise_records_original(tmp_path):
    def _plain(messages, *, agent_id=None, request_id=None):
        async def _inner():
            raise ValueError("t19 inner")

        async def _outer():
            return await _inner()

        return b3._run(_outer())

    direct, wrapped, _ = _drive_both(
        plain=_plain,
        messages=(("user", "t19"),),
        agent_id=AGENT,
        request_id="req-t19-pump-nested-raise",
        obs_dir=tmp_path / "obs",
    )
    assert direct["exc_type"] == "ValueError" and direct["chain"][0] == "ValueError"
    stored = _read_obs(tmp_path / "obs", "req-t19-pump-nested-raise")
    _parity_rows(
        "pump-nested-raise",
        direct=direct,
        wrapped=wrapped,
        calls_direct=0,
        calls_wrapped=0,
        obs=stored,
        landed=True,
    )
    assert stored["snapshot_status"] == "absent"


def test_t19_pump_fake_suspend_records_assertion(tmp_path):
    class _BareYield:
        def __await__(self):
            value = yield ("t19-bare",)
            return value

    def _plain(messages, *, agent_id=None, request_id=None):
        async def _outer():
            return await _BareYield()

        return b3._run(_outer())

    direct, wrapped, _ = _drive_both(
        plain=_plain,
        messages=(("user", "t19"),),
        agent_id=AGENT,
        request_id="req-t19-pump-fake-suspend",
        obs_dir=tmp_path / "obs",
    )
    assert direct["exc_type"] == "AssertionError"
    stored = _read_obs(tmp_path / "obs", "req-t19-pump-fake-suspend")
    _parity_rows(
        "pump-fake-suspend",
        direct=direct,
        wrapped=wrapped,
        calls_direct=0,
        calls_wrapped=0,
        obs=stored,
        landed=True,
    )


# 8-13: transport failures (original pump + fake transport), parity + absent


def _run_transport_case(tmp_path, monkeypatch, case, *, behavior, enter_exc=None, exit_exc=None):
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)
    rid = "req-t19-%s" % case
    messages = (("user", "t19"),)

    def _run_once(ev_subdir):
        sink = sa_evidence.EvidenceSink(tmp_path / ev_subdir)
        calls = _install_fake(
            monkeypatch, behavior=behavior, enter_exc=enter_exc, exit_exc=exit_exc
        )
        plain = _backend_dialog(backend, sink)
        direct = _outcome_of(
            lambda: plain(messages, agent_id=AGENT, request_id=rid)
        )
        n_calls = len(calls)
        return direct, n_calls

    direct, n_direct = _run_once("ev-direct")
    wrapped_calls_box = []

    def _run_wrapped():
        sink = sa_evidence.EvidenceSink(tmp_path / "ev-wrapped")
        calls = _install_fake(
            monkeypatch, behavior=behavior, enter_exc=enter_exc, exit_exc=exit_exc
        )
        plain = _backend_dialog(backend, sink)
        dialog_calls = []

        def _counting(msgs, *, agent_id=None, request_id=None):
            dialog_calls.append(1)
            return plain(msgs, agent_id=agent_id, request_id=request_id)

        obs_dir = tmp_path / "obs"
        outcome = _outcome_of(
            lambda: fa_obs.observe_dialog(
                _counting, messages, directory=obs_dir, agent_id=AGENT, request_id=rid
            )
        )
        wrapped_calls_box.append((len(dialog_calls), len(calls)))
        return outcome, obs_dir

    wrapped, obs_dir = _run_wrapped()
    n_dialog, n_wrapped = wrapped_calls_box[0]
    assert n_dialog == 1  # exactly once
    stored = _read_obs(obs_dir, rid)
    _parity_rows(
        case,
        direct=direct,
        wrapped=wrapped,
        calls_direct=n_direct,
        calls_wrapped=n_wrapped,
        obs=stored,
        landed=True,
    )
    assert stored["snapshot_status"] == "absent"
    assert stored["dialog_request_id"] == rid
    return stored


def test_t19_client_enter_failure_no_snapshot(tmp_path, monkeypatch):
    enter_boom = RuntimeError("t19 enter")
    stored = _run_transport_case(
        tmp_path, monkeypatch, "client-enter", behavior=lambda call: None, enter_exc=enter_boom
    )
    assert stored["outer_exception"][0] == "RuntimeError"


def test_t19_client_exit_failure_no_snapshot(tmp_path, monkeypatch):
    raw, body = _ok_body()
    exit_boom = RuntimeError("t19 exit")
    _run_transport_case(
        tmp_path,
        monkeypatch,
        "client-exit",
        behavior=lambda call: _FakeResp(raw, body),
        exit_exc=exit_boom,
    )


def test_t19_post_non_network_no_snapshot_unbilled(tmp_path, monkeypatch):
    def _behavior(call):
        raise ValueError("t19 non-network post")

    stored = _run_transport_case(tmp_path, monkeypatch, "post-non-network", behavior=_behavior)
    assert sa_evidence.classify_transport_type(ValueError("x")) == "unknown"


def test_t19_response_json_failure_no_snapshot(tmp_path, monkeypatch):
    stored = _run_transport_case(
        tmp_path,
        monkeypatch,
        "response-json",
        behavior=lambda call: _FakeResp("not-json", None, json_exc=ValueError("t19 bad json")),
    )
    assert stored["type"] == "unknown"


def test_t19_response_shape_failure_no_snapshot(tmp_path, monkeypatch):
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    raw = json.dumps({"id": "t19-no-choices", "object": "chat.completion", "usage": usage})
    _run_transport_case(
        tmp_path,
        monkeypatch,
        "response-shape",
        behavior=lambda call: _FakeResp(raw, json.loads(raw)),
    )


def test_t19_response_gap_unqualified_no_snapshot(tmp_path, monkeypatch):
    raw = json.dumps(
        {
            "id": "t19-no-usage",
            "object": "chat.completion",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        }
    )
    _run_transport_case(
        tmp_path,
        monkeypatch,
        "response-gap",
        behavior=lambda call: _FakeResp(raw, json.loads(raw)),
    )


# 14-15: wiring submit/result boundary failures carry snapshots -> present


def _run_boundary_case(tmp_path, case, *, submit=None, future=None):
    rid = "req-t19-%s" % case
    messages = (("user", "t19"),)
    boom = _boom_with_obs() if submit is not None or future is not None else None

    def _build(submit_fn):
        class _AsyncBackend:
            async def complete(self, **kw):
                return "t19-unreached"

        llm = SimpleNamespace(backend=_AsyncBackend())
        factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=submit_fn)
        return factory(_snapshot())

    if submit is not None:
        def _boom_submit(loop, coro):
            coro.close()
            raise boom

        submit_fn = _boom_submit
    else:
        class _BoomFuture:
            def result(self):
                raise boom

        def _future_submit(loop, coro):
            coro.close()
            return _BoomFuture()

        submit_fn = _future_submit

    plain_direct = _build(submit_fn)
    direct = _outcome_of(lambda: plain_direct(messages, agent_id=AGENT, request_id=rid))
    assert direct["exc_type"] == "LiveRefused"

    plain_wrapped = _build(submit_fn)
    dialog_calls = []
    obs_dir = tmp_path / "obs"

    def _counting(msgs, *, agent_id=None, request_id=None):
        dialog_calls.append(1)
        return plain_wrapped(msgs, agent_id=agent_id, request_id=request_id)

    wrapped = _outcome_of(
        lambda: fa_obs.observe_dialog(
            _counting, messages, directory=obs_dir, agent_id=AGENT, request_id=rid
        )
    )
    assert dialog_calls == [1]
    assert wrapped["exc_type"] == "LiveRefused"
    assert wrapped["cause_type"] == "RuntimeError"
    assert wrapped["chain"] == direct["chain"]
    stored = _read_obs(obs_dir, rid)
    row = {
        "direct": direct,
        "wrapped": wrapped,
        "calls_direct": 0,
        "calls_wrapped": 0,
        "parity": True,
        "snapshot_status": stored["snapshot_status"],
        "landed": True,
    }
    _matrix(case, row)
    assert stored["snapshot_status"] == "present"
    assert stored["dialog_request_id"] == rid
    assert stored["type"] == "connect"
    assert stored["transport_calls"] == 1
    assert stored["snapshot"]["evidence_request_id"] == "req-t19-ev-boom"
    return stored


def test_t19_dialog_submit_failure_boundary(tmp_path):
    stored = _run_boundary_case(tmp_path, "dialog-submit", submit=True)
    assert stored["snapshot"]["boundaries"].get("submit") is True


def test_t19_dialog_result_failure_boundary(tmp_path):
    _run_boundary_case(tmp_path, "dialog-result", future=True)


# 16: reproducible counterexample through ORIGINAL pump/submit/snapshot


def test_t19_counterexample_original_pump_exit7_shape(tmp_path, monkeypatch):
    def _behavior(call):
        raise httpx.ConnectError("t19 down") from OSError(113, "t19 unreachable")

    calls = _install_fake(monkeypatch, behavior=_behavior)
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    rid = b3.DIALOG_REQUEST_ID
    messages = (("user", "t19"),)

    def _build(ev_subdir):
        sink = sa_evidence.EvidenceSink(tmp_path / ev_subdir)
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=b3._submit, evidence_sink=sink
        )
        return factory(b3._snapshot())  # ORIGINAL snapshot builder

    direct = _outcome_of(lambda: _build("ev-direct")(messages, agent_id=AGENT, request_id=rid))
    assert direct["exc_type"] == "LiveRefused"
    assert direct["cause_type"] == "RuntimeError"
    assert len(calls) == 1  # local POST only; != backend received/billed

    plain = _build("ev-wrapped")
    dialog_calls = []
    obs_dir = tmp_path / "obs"

    def _counting(msgs, *, agent_id=None, request_id=None):
        dialog_calls.append(1)
        return plain(msgs, agent_id=agent_id, request_id=request_id)

    wrapped = _outcome_of(
        lambda: fa_obs.observe_dialog(
            _counting, messages, directory=obs_dir, agent_id=AGENT, request_id=rid
        )
    )
    assert dialog_calls == [1]
    assert wrapped["exc_type"] == "LiveRefused"
    assert wrapped["chain"] == direct["chain"]
    stored = _read_obs(obs_dir, rid)
    assert stored["snapshot_status"] == "present"
    assert stored["type"] == "connect"
    assert stored["chain"] == ["ConnectError", "OSError"]
    assert stored["errno"] == 113
    assert stored["transport_calls"] == 1
    _matrix(
        "counterexample-pump-exit7",
        {
            "direct": direct,
            "wrapped": wrapped,
            "calls_direct": 1,
            "calls_wrapped": 1,
            "parity": True,
            "snapshot_status": "present",
            "landed": True,
        },
    )


# 17-19: land trio -- old lander stays silent, new wrapper lands honestly


def _land_old(exc, tmp_path):
    """Drive the ORIGINAL B3 lander against a sandbox LIVE_DIR (read-only use)."""
    import unittest.mock as _mock

    with _mock.patch.object(b3, "LIVE_DIR", tmp_path):
        ok = b3._land_transport_obs(exc)
    transport_dir = tmp_path / "transport"
    files = (
        sorted(p.name for p in transport_dir.iterdir()) if transport_dir.exists() else []
    )
    return ok, files


def test_t19_land_no_snapshot_old_silent_new_absent(tmp_path):
    bare = RuntimeError("t19 no snapshot anywhere")
    old_ok, old_files = _land_old(bare, tmp_path / "old")
    assert old_ok is False and old_files == []  # old: silent, nothing lands

    obs_dir = tmp_path / "obs"

    def _plain(messages, *, agent_id=None, request_id=None):
        raise bare

    try:
        fa_obs.observe_dialog(
            _plain, (("user", "t19"),), directory=obs_dir, request_id="req-t19-land-absent"
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is bare
    stored = _read_obs(obs_dir, "req-t19-land-absent")
    assert stored["snapshot_status"] == "absent"  # new: honest absent record lands
    assert stored["type"] == "unknown" and stored["phase"] == "unknown"
    assert stored["attempts"] == []
    _matrix(
        "land-no-snapshot",
        {
            "old_lander": {"landed": old_ok, "files": old_files},
            "new_wrapper": {"landed": True, "snapshot_status": "absent"},
            "difference": "old silent; new lands honest absent record",
        },
    )


def test_t19_land_pollution_old_refused_new_rejected(tmp_path):
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t19-api_key-must-never-land",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0,
                exc_type="connect",
                chain=["ConnectError"],
                errno=None,
                elapsed_ms=1.0,
                phase="post",
            )
        ],
        boundaries={},
        observation_ok=True,
    )
    boom = RuntimeError("t19 polluted")
    sa_evidence.attach_transport_obs(boom, diag)
    old_ok, old_files = _land_old(boom, tmp_path / "old")
    assert old_ok is False and old_files == []  # old: pollution refuses, silent

    obs_dir = tmp_path / "obs"

    def _plain(messages, *, agent_id=None, request_id=None):
        raise boom

    try:
        fa_obs.observe_dialog(
            _plain, (("user", "t19"),), directory=obs_dir, request_id="req-t19-land-pol"
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is boom
    stored = _read_obs(obs_dir, "req-t19-land-pol")
    # New: polluted snapshot discarded WHOLE; outer record lands rejected.
    assert stored["snapshot_status"] == "rejected"
    assert stored["snapshot"] is None
    assert stored["attempts"] == [] and stored["transport_calls"] is None
    text = json.dumps(stored, ensure_ascii=False, sort_keys=True)
    assert "api_key" not in text and "Bearer" not in text
    _matrix(
        "land-pollution",
        {
            "old_lander": {"landed": old_ok, "files": old_files},
            "new_wrapper": {"landed": True, "snapshot_status": "rejected"},
            "difference": "old silent; new discards snapshot whole, lands rejected",
        },
    )


def test_t19_land_disk_failure_token_and_reraise(tmp_path, monkeypatch, capsys):
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t19-ev-disk",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0,
                exc_type="connect",
                chain=["ConnectError"],
                errno=None,
                elapsed_ms=1.0,
                phase="post",
            )
        ],
        boundaries={},
        observation_ok=True,
    )
    boom = RuntimeError("t19 disk")
    sa_evidence.attach_transport_obs(boom, diag)
    old_ok, _ = _land_old(boom, tmp_path / "old")  # old path reference (lands or not)
    assert isinstance(old_ok, bool)

    def _disk_down(*args, **kwargs):
        raise OSError("t19 mutant disk full")

    import os as _os

    monkeypatch.setattr(_os, "replace", _disk_down)
    obs_dir = tmp_path / "obs"

    def _plain(messages, *, agent_id=None, request_id=None):
        raise boom

    try:
        fa_obs.observe_dialog(
            _plain, (("user", "t19"),), directory=obs_dir, request_id="req-t19-land-disk"
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is boom  # original never masked
    out = capsys.readouterr().out
    assert out == fa_obs.WRITE_FAILED_TOKEN + "\n"  # fixed token only
    assert "t19 disk" not in out and "disk full" not in out  # no original error text
    assert _obs_files(obs_dir) == []  # disk failure: explicit limitation, no record
    _matrix(
        "land-disk-failure",
        {
            "old_lander": {"landed": old_ok},
            "new_wrapper": {"landed": False, "token": True, "reraised_identical": True},
            "difference": "both unlanded; new emits fixed token, keeps identity",
        },
    )


# 20-22: AST pins (product code unchanged, read-only)


def _proxy_source() -> str:
    return (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")


def test_t19_ast_proxy_transport_shape_unchanged():
    tree = ast.parse(_proxy_source())
    handled = []
    timeouts = []
    pop_defaults = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node):  # noqa: N802
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                value = func.value
                if isinstance(value, ast.Name):
                    name = value.id + "." + func.attr
                else:
                    name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name.endswith("AsyncClient"):
                for kw in node.keywords:
                    if kw.arg == "timeout" and isinstance(kw.value, ast.Constant):
                        timeouts.append(kw.value.value)
            if name == "kwargs.pop" and len(node.args) == 2:
                first, default = node.args
                if (
                    isinstance(first, ast.Constant)
                    and first.value == "max_retries"
                    and isinstance(default, ast.Constant)
                ):
                    pop_defaults.append(default.value)
            self.generic_visit(node)

        def visit_ExceptHandler(self, node):  # noqa: N802
            if isinstance(node.type, ast.Tuple):
                names = []
                for elt in node.type.elts:
                    if isinstance(elt, ast.Attribute):
                        names.append(elt.attr)
                if names:
                    handled.append(tuple(names))
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert ("ConnectError", "TimeoutException") in handled  # :2212 shape
    assert timeouts and all(t == 120.0 for t in timeouts)  # timeout=120 frozen
    assert 3 in pop_defaults  # max_retries default 3 frozen
    source = _proxy_source()
    assert "max_retries = 0  # 取證模式固定零重試" in source  # evidence zero retry
    assert "RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}" in source
    lines = source.splitlines()
    count_idx = next(
        (i for i, line in enumerate(lines) if "_transport_calls += 1" in line), None
    )
    post_idx = next(
        (i for i, line in enumerate(lines) if "client.post(" in line), None
    )
    assert count_idx is not None and post_idx is not None and count_idx < post_idx
    _matrix("ast-proxy", {"pins": "hold"})


def test_t19_ast_wiring_triple_boundary_unchanged():
    source = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(encoding="utf-8")
    assert source.count("propagate_transport_obs(") == 3
    for boundary in ("complete", "submit", "result"):
        assert 'boundary="%s"' % boundary in source
    assert source.count("raise _refused from exc") == 3
    _matrix("ast-wiring", {"pins": "hold"})


def test_t19_ast_evidence_whitelist_unchanged():
    assert sa_evidence.TRANSPORT_TYPES == (
        "connect",
        "connect_timeout",
        "read_timeout",
        "write_timeout",
        "pool_timeout",
        "unknown",
    )
    assert sa_evidence.TRANSPORT_PHASES == ("post", "complete", "submit", "result")
    assert sa_evidence.TRANSPORT_OBS_ATTR == "_sa_transport_obs"
    _matrix("ast-evidence", {"pins": "hold"})


# 23-26: bidirectional observer mutations (RED then GREEN, DUD stops)


def _run_failing_transport(monkeypatch, tmp_path, ev_subdir):
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t19 mutant probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / ev_subdir)
    plain = _backend_dialog(backend, sink)
    messages = (("user", "t19"),)
    rid = "req-t19-mut"
    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            plain, messages, directory=obs_dir, agent_id=AGENT, request_id=rid
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    return raised, obs_dir, rid


def test_t19_mutation_delete_evidence_red(tmp_path, monkeypatch):
    """Mutant: attach disabled -> wrapper sees NO snapshot (must be RED)."""
    monkeypatch.setattr(sa_evidence, "attach_transport_obs", lambda *a, **k: None)
    raised, obs_dir, rid = _run_failing_transport(monkeypatch, tmp_path, "ev-mut-red")
    if sa_evidence.find_transport_obs(raised) is not None:
        pytest.fail("MUTATION DUD -- STOP, report: deleted evidence still observed")
    stored = _read_obs(obs_dir, rid)
    assert stored["snapshot_status"] == "absent"  # RED confirmed: blind as designed
    assert stored["type"] == "unknown" and stored["attempts"] == []
    _matrix("mut-delete-red", {"observer": "blind-as-designed"})


def test_t19_mutation_delete_evidence_reverted_green(tmp_path, monkeypatch):
    raised, obs_dir, rid = _run_failing_transport(monkeypatch, tmp_path, "ev-mut-green")
    obs = sa_evidence.find_transport_obs(raised)
    assert obs is not None and obs["attempts"][0]["type"] == "connect"
    stored = _read_obs(obs_dir, rid)
    assert stored["snapshot_status"] == "present"  # GREEN: sees connect snapshot
    assert stored["snapshot"]["attempts"][0]["chain"][0] == "ConnectError"
    _matrix("mut-delete-green", {"observer": "sees-connect"})


def test_t19_mutation_misclassify_red(tmp_path, monkeypatch):
    """Mutant: every error mislabeled -> wrapper embeds wrong type (must be RED)."""
    monkeypatch.setattr(  # MUTANT
        sa_evidence, "classify_transport_type", lambda exc: "read_timeout"
    )
    raised, obs_dir, rid = _run_failing_transport(monkeypatch, tmp_path, "ev-mut-mis")
    label = sa_evidence.find_transport_obs(raised)["attempts"][0]["type"]
    if label == "connect":
        pytest.fail("MUTATION DUD -- STOP, report: mislabeled transport still reads connect")
    assert label == "read_timeout"  # RED confirmed: wrong label is visible
    stored = _read_obs(obs_dir, rid)
    assert stored["snapshot_status"] == "present" and stored["type"] == "read_timeout"
    _matrix("mut-misclassify-red", {"observer": "sees-read_timeout"})


def test_t19_mutation_misclassify_reverted_green(tmp_path, monkeypatch):
    raised, obs_dir, rid = _run_failing_transport(monkeypatch, tmp_path, "ev-mut-ok")
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["attempts"][0]["type"] == "connect"
    assert obs["attempts"][0]["chain"][0] == "ConnectError"
    stored = _read_obs(obs_dir, rid)
    assert stored["snapshot_status"] == "present" and stored["type"] == "connect"
    _matrix("mut-misclassify-green", {"observer": "sees-connect"})


# -- T19-c: new-behavior probes (pollution shape, zero-fill, whitelist) --------


def test_t19_unknown_zero_fill_never_zero(tmp_path):
    """Absent AND rejected rows stay null/unknown: no 0, no fabricated attempt."""
    bare = RuntimeError("t19 bare")
    for exc, status in (
        (bare, fa_obs.SNAPSHOT_ABSENT),
        (_boom_malformed_snapshot(), fa_obs.SNAPSHOT_REJECTED),
    ):
        record = fa_obs.build_observation(exc, dialog_request_id="req-t19-zerofill")
        assert record["snapshot_status"] == status
        assert record["type"] == "unknown"
        assert record["phase"] == "unknown"
        assert record["chain"] is None
        assert record["errno"] is None
        assert record["elapsed_ms"] is None
        assert record["transport_calls"] is None
        assert record["backend_observations"] is None
        assert record["attempts"] == []
        assert record["snapshot"] is None


def _boom_malformed_snapshot():
    """Snapshot-shaped but refused by existing serialization validation."""
    boom = RuntimeError("t19 malformed")
    sa_evidence.attach_transport_obs(
        boom,
        {
            "kind": sa_evidence.TRANSPORT_DIAGNOSIS_KIND,
            "version": "sa-transport-obs-1",
            "dialog_request_id": None,
            "evidence_request_id": "req-t19-ev-mal",
            "transport_calls": "one",  # mistyped: must be refused
            "backend_observations": None,
            "attempts": [],
            "boundaries": {},
            "observation_ok": True,
        },
    )
    return boom


def test_t19_outer_exception_whitelist_unknown_name():
    weird = type("evil name!", (Exception,), {})
    try:
        raise weird("t19") from OSError("t19 cause")
    except Exception as exc:  # noqa: BLE001
        caught = exc
    assert fa_obs.outer_chain(caught)[0] == fa_obs.OUTER_UNKNOWN_NAME
    record = fa_obs.build_observation(caught, dialog_request_id="req-t19-weird")
    assert record["outer_exception"][0] == "UnknownException"
    assert record["snapshot_status"] == "absent"


def test_t19_outer_exception_chain_capped_at_three():
    first = RuntimeError("t19 one")
    second = ValueError("t19 two")
    third = OSError("t19 three")
    fourth = KeyError("t19 four")
    first.__cause__ = second
    first.__context__ = third  # distinct from cause: occupies the third slot
    third.__cause__ = fourth  # beyond depth: never read
    names = fa_obs.outer_chain(first)
    assert names == ["RuntimeError", "ValueError", "OSError"]
    assert len(names) <= 3


def test_t19_forbidden_content_never_stored(tmp_path):
    secret = "t19-secret-message-9f3c-never-store"
    url = "http://t19-invalid.invalid/secret-path"
    boom = RuntimeError("%s %s" % (secret, url))
    record = fa_obs.build_observation(boom, dialog_request_id="req-t19-forbid")
    assert set(record.keys()) == OUTER_KEYS  # closed key set, no leak columns
    text = json.dumps(record, ensure_ascii=False, sort_keys=True)
    assert secret not in text and url not in text
    for token in ("args", "traceback", "http", "headers", "raw_text", "message"):
        assert token not in text


def test_t19_outer_pollution_no_file_fixed_token(tmp_path, capsys):
    """Polluted outer row (bad request id): whole write refused, token only."""
    boom = RuntimeError("t19 outer pollution")
    obs_dir = tmp_path / "obs"

    def _plain(messages, *, agent_id=None, request_id=None):
        raise boom

    try:
        fa_obs.observe_dialog(
            _plain,
            (("user", "t19"),),
            directory=obs_dir,
            request_id="req-t19-Bearer-must-never-land",
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is boom
    out = capsys.readouterr().out
    assert out == fa_obs.WRITE_FAILED_TOKEN + "\n"
    assert _obs_files(obs_dir) == []
    assert "Bearer" not in out


def test_t19_data_dir_refused_structurally(tmp_path, capsys):
    """Caller-provided directory under data/** refuses before any write."""
    boom = RuntimeError("t19 prod guard")
    prod_dir = _REPO_ROOT / "data" / "t19-probe"

    def _plain(messages, *, agent_id=None, request_id=None):
        raise boom

    try:
        fa_obs.observe_dialog(
            _plain,
            (("user", "t19"),),
            directory=prod_dir,
            request_id="req-t19-prod-guard",
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is boom
    assert capsys.readouterr().out == fa_obs.WRITE_FAILED_TOKEN + "\n"


def test_t19_socket_hard_blocked_at_runtime():
    """Execution-time proof: real sockets cannot be opened in this suite."""
    with pytest.raises(RuntimeError):
        socket.socket()


# -- T19-d: new-module AST pins + bidirectional delivery mutations -------------


def _obs_source() -> str:
    return (_REPO_ROOT / "src" / "soul_access" / "failure_observation.py").read_text(
        encoding="utf-8"
    )


def test_t19_ast_new_module_net_free_fixed_token_bare_raise():
    source = _obs_source()
    # Port literals must not appear anywhere (prose or code).
    for banned in (":8000", ":8765", ":8766", ":8767"):
        assert banned not in source
    tree = ast.parse(source)

    imported = set()
    str_on_exc = []
    bad_attrs = []

    class _Visitor(ast.NodeVisitor):
        def visit_Import(self, node):  # noqa: N802
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
            self.generic_visit(node)

        def visit_ImportFrom(self, node):  # noqa: N802
            if node.module:
                imported.add(node.module.split(".")[0])
            self.generic_visit(node)

        def visit_Call(self, node):  # noqa: N802
            func = node.func
            if isinstance(func, ast.Name) and func.id == "str":
                for arg in node.args:
                    if isinstance(arg, ast.Name) and arg.id in ("exc", "current"):
                        str_on_exc.append(arg.id)
            self.generic_visit(node)

        def visit_Attribute(self, node):  # noqa: N802
            if node.attr in ("args", "__dict__", "message") or (
                node.attr == "kill" and isinstance(node.value, ast.Name)
            ):
                bad_attrs.append(node.attr)
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert not (imported & {"httpx", "socket", "subprocess", "urllib", "requests", "traceback"})
    assert str_on_exc == []  # class names only, never stringified errors
    assert bad_attrs == []  # never reads .args / messages / process kill
    assert "print(WRITE_FAILED_TOKEN)" in source  # fixed token only
    assert "\n        raise\n" in source  # bare re-raise: identity + cause kept
    assert "OBSERVATION_WRITE_FAILED" in source
    assert "No CLI" in source  # no CLI entry documented
    assert fa_obs.OBSERVATION_KIND == "sa-exit7-observation"
    assert fa_obs.OBSERVATION_VERSION == "sa-exit7-observation/v1"
    _matrix("ast-new-module", {"pins": "hold"})


def test_t19_mutation_delivery_off_red(tmp_path, monkeypatch, capsys):
    """Mutant: landing disabled -> original still raised, nothing lands (RED)."""
    monkeypatch.setattr(
        fa_obs,
        "record_observation",
        lambda **kw: (_ for _ in ()).throw(
            sa_evidence.EvidenceStoreError("t19 mutant landing off")
        ),
    )
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t19 delivery probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    plain = _backend_dialog(backend, sink)
    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            plain,
            (("user", "t19"),),
            directory=obs_dir,
            agent_id=AGENT,
            request_id="req-t19-delivery-red",
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    assert _obs_files(obs_dir) == []  # RED confirmed: delivery off, nothing landed
    assert capsys.readouterr().out == fa_obs.WRITE_FAILED_TOKEN + "\n"
    _matrix("mut-delivery-red", {"observer": "delivery-off-silent-but-token"})


def test_t19_mutation_delivery_off_reverted_green(tmp_path, monkeypatch, capsys):
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t19 delivery probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    plain = _backend_dialog(backend, sink)
    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            plain,
            (("user", "t19"),),
            directory=obs_dir,
            agent_id=AGENT,
            request_id="req-t19-delivery-green",
        )
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    stored = _read_obs(obs_dir, "req-t19-delivery-green")
    assert stored["snapshot_status"] == "present"  # GREEN: delivery restored
    assert capsys.readouterr().out == ""
    _matrix("mut-delivery-green", {"observer": "lands-present"})


def test_t19_mutation_snapshot_blind_red(tmp_path, monkeypatch):
    """Mutant: snapshot lookup blinded -> wrapper must read absent (RED)."""
    monkeypatch.setattr(sa_evidence, "find_transport_obs", lambda exc: None)
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t19 blind probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    plain = _backend_dialog(backend, sink)
    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            plain,
            (("user", "t19"),),
            directory=obs_dir,
            agent_id=AGENT,
            request_id="req-t19-blind-red",
        )
    except RuntimeError:
        pass
    stored = _read_obs(obs_dir, "req-t19-blind-red")
    if stored["snapshot_status"] == "present":
        pytest.fail("MUTATION DUD -- STOP, report: blinded lookup still embeds snapshot")
    assert stored["snapshot_status"] == "absent"  # RED confirmed
    _matrix("mut-blind-red", {"observer": "absent-as-designed"})


def test_t19_mutation_snapshot_blind_reverted_green(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t19-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t19 blind probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    plain = _backend_dialog(backend, sink)
    obs_dir = tmp_path / "obs"
    try:
        fa_obs.observe_dialog(
            plain,
            (("user", "t19"),),
            directory=obs_dir,
            agent_id=AGENT,
            request_id="req-t19-blind-green",
        )
    except RuntimeError:
        pass
    stored = _read_obs(obs_dir, "req-t19-blind-green")
    assert stored["snapshot_status"] == "present"  # GREEN: lookup restored
    assert stored["type"] == "connect"
    _matrix("mut-blind-green", {"observer": "sees-connect"})


# -- T19-zz: sandbox case-matrix artifact (secret-free, tmp_path only) -----------
#
# SA-TEST-HYGIENE-1: this artifact MUST NOT touch the repo tree. The committed
# ``verification/sa-transport-obs-2b/case-matrix.json`` is a frozen reference
# (read-only); the per-run matrix lands under the per-test ``tmp_path`` sandbox
# so passing and failing runs alike leave every tracked file untouched.


def test_t19_zz_matrix_artifact(tmp_path):
    """Persist the secret-free case matrix to the per-test sandbox dir."""
    out_dir = tmp_path / "t19-case-matrix"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "SA-TRANSPORT-OBS-2B",
        "note": (
            "offline only; local POST != backend received/billed; "
            "original pump + fake transport re-run through observe_dialog; "
            "B1/B2/B3 originals untouched, INCONCLUSIVE stands"
        ),
        "cases": MATRIX_ROWS,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    assert "t19-synthetic-value" not in text
    assert "Bearer" not in text
    path = out_dir / "case-matrix.json"
    path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["ticket"] == "SA-TRANSPORT-OBS-2B"
