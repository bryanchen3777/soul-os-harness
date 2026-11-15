"""SA-TRANSPORT-OBS-2A T18 exit-7 offline coverage (sandbox only).

Decision-complete scope (SA-TRANSPORT-OBS-2A, mastermind Wolong approved):
zero product change. This file (``tests/soul_access/test_t18_exit7_coverage.py``)
is the ONLY new file; artifacts stay in ``verification/sa-transport-obs-2a/``
(self-built sandbox) plus per-test ``tmp_path``. Read-only: ``src/llm/proxy.py``
(esp. the ``except (ConnectError, TimeoutException)`` at :2212),
``src/soul_access/evidence.py`` (esp. ``propagate_transport_obs`` at :593),
the three wiring ``except`` pass-throughs, and the B1/B2/B3 originals
(``live-fire-1/live_fire_once.py``, ``live-fire-2/live_fire_b2.py``,
``live-fire-3/live_fire_b3.py``).

All test names carry the t18 marker: ``pytest -q tests/soul_access -k t18``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected via
``monkeypatch`` (no real request, no server, no port ever bound). ``main`` is
never called. ``dotenv`` loading is stubbed BEFORE the B3 import (the process
never reads a real ``.env``); only key *existence* is checked, the synthetic
value is never reported or written anywhere.

Coverage (ticket acceptance mapping):
  a. original pump (``live_fire_b3._run``, no event loop): no-suspend success
     control, running-loop requirement, three actual-yield groups; the latter
     groups record the ORIGINAL exception (type + chain), never a prefilled
     root cause;
  b. client entry/exit injection (``__aenter__`` / ``__aexit__`` failures);
  c. post non-network exception (local POST call != backend received/billed;
     no response is ever fabricated);
  d. response parsing failures (``json()`` raise, shape ``KeyError``,
     ``EvidenceGap`` on missing usage);
  e. wiring submit/result failures (boundary marked, cause kept);
  f. snapshot-less landing, pollution refusal, landing failure;
  g. per-case reconcile: exception class + cause chain, wrapper boundary,
     snapshot presence, five-field status, landing result + reason.
     Unknown stays unknown.
  h. AST pins (product unchanged) + bidirectional observer mutations
     (delete-evidence / misclassify: RED then GREEN, DUD stops the run).
  i. at least one reproducible counterexample driven through the ORIGINAL
     pump (+ original submit/snapshot/lander). Same-shape reproduction only:
     this NEVER claims a historical root cause is proven.
"""

from __future__ import annotations

import asyncio
import ast
import importlib.util
import inspect
import json
import sys
import threading
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
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


#Installed unconditionally BEFORE the B3 import: an earlier-imported module
# may already hold the real dotenv, which must not read a real .env here.
_SAVED_DOTENV = sys.modules.get("dotenv")
sys.modules["dotenv"] = _NoopDotenv("dotenv")


def _load_b3():
    spec = importlib.util.spec_from_file_location("live_fire_b3_t18", LIVE_B3_PATH)
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

NON_ROUTABLE_BASE = "http://t18-invalid.invalid/v1/chat/completions"
SENTINEL_KEY_REF = "OLLAMA_API_KEY"
MODEL = "do-not-deploy-model-t18"
AGENT = "agent_rem"

FIVE_FIELDS = ("type", "chain", "errno", "elapsed_ms", "phase")

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


def _reconcile(exc, *, landed=None, land_reason=""):
    """One reconcile row: class+chain, boundary, snapshot, five fields, landing."""
    obs = sa_evidence.find_transport_obs(exc)
    if obs is None:
        five = "absent"
    else:
        attempts = obs.get("attempts") or []
        if len(attempts) == 1 and set(attempts[0].keys()) == {"seq"} | set(FIVE_FIELDS):
            five = {name: attempts[0][name] for name in FIVE_FIELDS}
        else:
            five = "malformed:%r" % (attempts,)
    return {
        "exc_type": type(exc).__name__,
        "chain": _chain_of(exc),
        "boundaries": dict(obs.get("boundaries", {})) if obs is not None else {},
        "snapshot_present": obs is not None,
        "five_fields": five,
        "landed": landed,
        "land_reason": land_reason,
    }


def _land_in_tmp(exc, tmp_path):
    """Drive the ORIGINAL B3 lander against a sandbox LIVE_DIR."""
    import unittest.mock as _mock

    with _mock.patch.object(b3, "LIVE_DIR", tmp_path):
        ok = b3._land_transport_obs(exc)
    transport_dir = tmp_path / "transport"
    files = (
        sorted(p.name for p in transport_dir.iterdir())
        if transport_dir.exists()
        else []
    )
    return ok, files


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


def _complete(backend, **over):
    kw = {
        "messages": [{"role": "user", "content": "t18 probe"}],
        "model": MODEL,
        "max_tokens": 16,
        "temperature": 0.5,
        "max_retries": 0,
    }
    kw.update(over)
    return b3._run(backend.complete(**kw))  # ORIGINAL pump, loop-free


def _ok_body(text="t18-reply"):
    raw = json.dumps(
        {
            "id": "t18-synthetic",
            "object": "chat.completion",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }
    )
    return raw, json.loads(raw)


def _boom_with_obs(*, label_chain=("RuntimeError",), errno=None):
    boom = RuntimeError("t18-boom")
    sa_evidence.attach_transport_obs(
        boom,
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t18-ev-boom",
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


# -- T18-a: dotenv disabled, key-existence only, main never called --------------


def test_t18_b3_loads_without_dotenv_and_main_never_called():
    assert LIVE_B1_PATH.exists() and LIVE_B2_PATH.exists() and LIVE_B3_PATH.exists()
    # The stubbed loader did nothing (no file read, no env injection).
    assert _B3_DOTENV_CALLS == [((), {})]
    # ... and the stub never leaks: sys.modules holds whatever was there
    # before this module (real dotenv or nothing), never our stub.
    assert sys.modules.get("dotenv") is _SAVED_DOTENV
    assert callable(b3.main)  # present but NEVER invoked in this file
    assert "fake suspend" in inspect.getsource(b3._run)  # original pump pin
    _matrix("b3-load", {"dotenv_stubbed": True, "main_called": False})


def test_t18_key_existence_only_never_reported(monkeypatch):
    monkeypatch.setenv(SENTINEL_KEY_REF, "t18-synthetic-value-never-written")
    import os as _os

    assert isinstance(_os.environ.get(SENTINEL_KEY_REF), str)
    assert _os.environ.get(SENTINEL_KEY_REF)  # existence only; value never asserted on
    _matrix("key-existence", {"key_ref": SENTINEL_KEY_REF, "value_reported": False})


# -- T18-b: original pump groups -------------------------------------------------


def test_t18_pump_no_suspend_success_control():
    async def _plain():
        return "t18-ok"

    assert b3._run(_plain()) == "t18-ok"
    _matrix("pump-control", {"shape": "no-suspend", "result": "value"})


def test_t18_pump_requires_running_loop_records_original():
    async def _needs_loop():
        return asyncio.get_running_loop()

    try:
        b3._run(_needs_loop())
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None  # no running loop inside the original pump
    row = _reconcile(raised, landed=False, land_reason="pump-level: no transport snapshot")
    assert row["exc_type"] == "RuntimeError" and row["chain"][0] == "RuntimeError"
    assert row["snapshot_present"] is False  # honest: pump errors carry no snapshot
    _matrix("pump-running-loop", row)


def test_t18_pump_nested_yield_success():
    async def _inner():
        return "t18-nested"

    async def _outer():
        return await _inner()

    assert b3._run(_outer()) == "t18-nested"
    _matrix("pump-nested-ok", {"shape": "actual-yield", "result": "value"})


def test_t18_pump_nested_raise_records_original():
    async def _inner():
        raise ValueError("t18 inner")

    async def _outer():
        return await _inner()

    try:
        b3._run(_outer())
        raised = None
    except ValueError as exc:
        raised = exc
    assert raised is not None
    # Record the ORIGINAL exception; the root cause is not prefilled here.
    assert type(raised).__name__ == "ValueError"
    assert _chain_of(raised)[0] == "ValueError"
    row = _reconcile(raised, landed=False, land_reason="pump-level: no transport snapshot")
    assert row["snapshot_present"] is False
    _matrix("pump-nested-raise", row)


def test_t18_pump_fake_suspend_records_assertion():
    class _BareYield:
        def __await__(self):
            value = yield ("t18-bare",)
            return value

    async def _outer():
        return await _BareYield()

    try:
        b3._run(_outer())
        raised = None
    except AssertionError as exc:
        raised = exc
    assert raised is not None
    assert "fake suspend" in str(raised)
    row = _reconcile(raised, landed=False, land_reason="pump-level: no transport snapshot")
    _matrix("pump-fake-suspend", row)


# -- T18-c: client entry/exit injection ------------------------------------------


def test_t18_client_enter_failure_no_snapshot(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    enter_boom = RuntimeError("t18 enter")
    calls = _install_fake(monkeypatch, behavior=lambda call: None, enter_exc=enter_boom)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is enter_boom
    assert calls == []  # entry failed: zero local POSTs, nothing billed
    row = _reconcile(raised)
    assert row["snapshot_present"] is False  # observation starts after entry
    ok, files = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False and files == []
    _matrix("client-enter", row)


def test_t18_client_exit_failure_no_snapshot(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    raw, body = _ok_body()
    exit_boom = RuntimeError("t18 exit")
    calls = _install_fake(
        monkeypatch,
        behavior=lambda call: _FakeResp(raw, body),
        exit_exc=exit_boom,
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is exit_boom
    assert len(calls) == 1  # local POST only; != received/billed
    row = _reconcile(raised)
    assert row["snapshot_present"] is False  # success path attaches no snapshot
    ok, files = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False
    _matrix("client-exit", row)


# -- T18-d: post non-network + response parse -------------------------------------


def test_t18_post_non_network_no_snapshot_unbilled(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise ValueError("t18 non-network post")

    calls = _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except ValueError as exc:
        raised = exc
    assert raised is not None
    assert len(calls) == 1  # local call happened; proves nothing about backend/billing
    row = _reconcile(raised)
    assert row["exc_type"] == "ValueError"
    assert row["snapshot_present"] is False  # non-network: honestly "unknown"/absent
    assert sa_evidence.classify_transport_type(raised) == "unknown"
    ok, files = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False and files == []
    # No response fabricated: only the pre-send request record exists.
    ev_names = sorted(
        p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
    )
    assert len(ev_names) == 1 and ev_names[0].endswith(".request.json")
    _matrix("post-non-network", row)


def test_t18_response_json_failure_no_snapshot(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    calls = _install_fake(
        monkeypatch,
        behavior=lambda call: _FakeResp("not-json", None, json_exc=ValueError("t18 bad json")),
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except ValueError as exc:
        raised = exc
    assert raised is not None and len(calls) == 1
    row = _reconcile(raised)
    assert row["snapshot_present"] is False
    ok, _ = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False
    _matrix("response-json", row)


def test_t18_response_shape_failure_no_snapshot(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    usage = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    raw = json.dumps({"id": "t18-no-choices", "object": "chat.completion", "usage": usage})
    calls = _install_fake(
        monkeypatch, behavior=lambda call: _FakeResp(raw, json.loads(raw))
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except KeyError as exc:
        raised = exc
    assert raised is not None and len(calls) == 1
    row = _reconcile(raised)
    assert row["exc_type"] == "KeyError"
    assert row["snapshot_present"] is False  # parse errors carry no snapshot
    ok, _ = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False
    _matrix("response-shape", row)


def test_t18_response_gap_unqualified_no_snapshot(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    raw = json.dumps(
        {
            "id": "t18-no-usage",
            "object": "chat.completion",
            "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
        }
    )
    calls = _install_fake(
        monkeypatch, behavior=lambda call: _FakeResp(raw, json.loads(raw))
    )
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except sa_evidence.EvidenceGap as exc:
        raised = exc
    assert raised is not None and len(calls) == 1
    row = _reconcile(raised)
    assert row["snapshot_present"] is False  # gap before any transport snapshot
    ok, _ = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False
    _matrix("response-gap", row)


# -- T18-e: wiring submit/result + original-path counterexample --------------------


def _snapshot(**over):
    kw = {
        "identity": sa_schema.RequestIdentity(
            request_id=b3.DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-t18-0001",
            idempotency_key="key-t18-0001",
        ),
        "dedup": sa_schema.DedupKey(
            principal_id="t18-offline-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t18-0001",
        ),
        "backend_alias": "primary",
        "model": MODEL,
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-14T00:00:00Z",
        "personality_version": "personality-t18-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-14T15:45:00-05:00",
        "inner_life_state": "inner-t18-v1",
        "projection_state": "projection-t18-v1",
        "recall_version": "recall-t18-v1",
        "recall_items": ("recall-t18-item-1",),
        "history_pool_version": "pool-t18-v1",
        "clean_history": ("hist-t18-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


def test_t18_dialog_submit_failure_boundary():
    boom = _boom_with_obs()

    class _AsyncBackend:
        async def complete(self, **kw):
            return "t18-unreached"

    def _boom_submit(loop, coro):
        coro.close()
        raise boom

    llm = SimpleNamespace(backend=_AsyncBackend())
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_boom_submit)
    dialog = factory(_snapshot())
    try:
        dialog((("user", "t18"),), agent_id=AGENT, request_id="req-t18-s")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None and raised.__cause__ is boom
    row = _reconcile(raised, landed=False, land_reason="boundary-only case")
    assert row["boundaries"].get("submit") is True
    assert row["snapshot_present"] is True
    assert row["five_fields"]["type"] == "connect"
    _matrix("dialog-submit", row)


def test_t18_dialog_result_failure_boundary():
    boom = _boom_with_obs(label_chain=("TimeoutError",))

    class _AsyncBackend:
        async def complete(self, **kw):
            return "t18-unreached"

    class _BoomFuture:
        def result(self):
            raise boom

    def _future_submit(loop, coro):
        coro.close()
        return _BoomFuture()

    llm = SimpleNamespace(backend=_AsyncBackend())
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_future_submit)
    dialog = factory(_snapshot())
    try:
        dialog((("user", "t18"),), agent_id=AGENT, request_id="req-t18-r")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None and raised.__cause__ is boom
    row = _reconcile(raised, landed=False, land_reason="boundary-only case")
    assert row["boundaries"].get("result") is True
    assert row["snapshot_present"] is True
    _matrix("dialog-result", row)


def test_t18_counterexample_original_pump_exit7_shape(tmp_path, monkeypatch):
    """Reproducible counterexample through the ORIGINAL pump/submit/lander.

    Same-shape reproduction only: a fake ConnectError driven by ``b3._run``
    via ``b3._submit`` surfaces at the wiring ``result`` boundary and lands
    through ``b3._land_transport_obs``. This does NOT prove any historical
    root cause.
    """

    def _behavior(call):
        raise httpx.ConnectError("t18 down") from OSError(113, "t18 unreachable")

    calls = _install_fake(monkeypatch, behavior=_behavior)
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=b3._submit, evidence_sink=sink
    )
    dialog = factory(b3._snapshot())  # ORIGINAL snapshot builder
    try:
        dialog((("user", "t18"),), agent_id=AGENT, request_id=b3.DIALOG_REQUEST_ID)
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None
    assert isinstance(raised.__cause__, RuntimeError)  # exhausted, cause kept
    assert len(calls) == 1  # local POST only; != backend received/billed
    row = _reconcile(raised)
    assert row["snapshot_present"] is True
    # Observed: the ORIGINAL submitter is synchronous (runs the pump inline),
    # so the exhausted error surfaces at the SUBMIT call, not future.result.
    assert row["boundaries"].get("submit") is True
    assert row["five_fields"]["type"] == "connect"
    assert row["five_fields"]["chain"] == ["ConnectError", "OSError"]
    assert row["five_fields"]["errno"] == 113
    ok, files = _land_in_tmp(raised, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "landed" if ok else "lander refused"
    assert ok is True
    assert files == ["%s.json" % b3.DIALOG_REQUEST_ID]
    stored = json.loads((tmp_path / "transport" / files[0]).read_text(encoding="utf-8"))
    assert stored["dialog_request_id"] == b3.DIALOG_REQUEST_ID
    assert stored["transport_calls"] == 1 == len(calls)
    _matrix("counterexample-pump-exit7", row)


# -- T18-f: snapshot-less / pollution / disk-failure landing ------------------------


def test_t18_land_no_snapshot_unqualified(tmp_path):
    bare = RuntimeError("t18 no snapshot anywhere")
    row = _reconcile(bare)
    assert row["snapshot_present"] is False
    ok, files = _land_in_tmp(bare, tmp_path)
    row["landed"] = ok
    row["land_reason"] = "no snapshot" if not ok else "unexpected landing"
    assert ok is False and files == []
    _matrix("land-no-snapshot", row)


def test_t18_land_pollution_refused(tmp_path):
    # Observed: the B3 lander OVERWRITES dialog_request_id with its fixed
    # ticket constant, so that pollution vector is scrubbed by design. The
    # surviving vector is evidence_request_id (passed through untouched).
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t18-api_key-must-never-land",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0, exc_type="connect", chain=["ConnectError"],
                errno=None, elapsed_ms=1.0, phase="post",
            )
        ],
        boundaries={},
        observation_ok=True,
    )
    boom = RuntimeError("t18 polluted")
    sa_evidence.attach_transport_obs(boom, diag)
    assert sa_evidence.find_transport_obs(boom) is not None
    ok, files = _land_in_tmp(boom, tmp_path)
    assert ok is False  # serializer refuses credential-shaped text
    assert files == [] and not (tmp_path / "transport").exists()
    row = _reconcile(boom, landed=ok, land_reason="pollution refusal")
    _matrix("land-pollution", row)


def test_t18_land_disk_failure_unqualified(tmp_path, monkeypatch):
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t18-ev-disk",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0, exc_type="connect", chain=["ConnectError"],
                errno=None, elapsed_ms=1.0, phase="post",
            )
        ],
        boundaries={},
        observation_ok=True,
    )
    boom = RuntimeError("t18 disk")
    sa_evidence.attach_transport_obs(boom, diag)
    monkeypatch.setattr(
        sa_evidence,
        "record_transport_diagnosis",
        lambda **kw: (_ for _ in ()).throw(
            sa_evidence.EvidenceStoreError("t18 mutant disk full")
        ),
    )
    ok, files = _land_in_tmp(boom, tmp_path)
    assert ok is False  # observation miss; original error kept
    assert files == []
    row = _reconcile(boom, landed=ok, land_reason="landing failure")
    _matrix("land-disk-failure", row)


# -- T18-g: AST pins (product code unchanged, read-only) ------------------------------


def _proxy_source() -> str:
    return (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")


def test_t18_ast_proxy_transport_shape_unchanged():
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


def test_t18_ast_wiring_triple_boundary_unchanged():
    source = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(encoding="utf-8")
    assert source.count("propagate_transport_obs(") == 3
    for boundary in ("complete", "submit", "result"):
        assert 'boundary="%s"' % boundary in source
    assert source.count("raise _refused from exc") == 3
    _matrix("ast-wiring", {"pins": "hold"})


def test_t18_ast_evidence_whitelist_unchanged():
    assert sa_evidence.TRANSPORT_TYPES == (
        "connect", "connect_timeout", "read_timeout",
        "write_timeout", "pool_timeout", "unknown",
    )
    assert sa_evidence.TRANSPORT_PHASES == ("post", "complete", "submit", "result")
    assert sa_evidence.TRANSPORT_OBS_ATTR == "_sa_transport_obs"
    _matrix("ast-evidence", {"pins": "hold"})


# -- T18-h: bidirectional observer mutations (RED then GREEN, DUD stops) -------------


def _run_failing_transport(monkeypatch, tmp_path):
    backend = OpenAIBackend(api_key="t18-synthetic", base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t18 mutant probe")

    _install_fake(monkeypatch, behavior=_behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    return raised


def test_t18_mutation_delete_evidence_red(tmp_path, monkeypatch):
    """Mutant: attach disabled -> observer sees NO snapshot (must be RED)."""
    monkeypatch.setattr(sa_evidence, "attach_transport_obs", lambda *a, **k: None)
    raised = _run_failing_transport(monkeypatch, tmp_path)
    if sa_evidence.find_transport_obs(raised) is not None:
        pytest.fail("MUTATION DUD -- STOP, report: deleted evidence still observed")
    _matrix("mut-delete-red", {"observer": "blind-as-designed"})


def test_t18_mutation_delete_evidence_reverted_green(tmp_path, monkeypatch):
    raised = _run_failing_transport(monkeypatch, tmp_path)
    obs = sa_evidence.find_transport_obs(raised)
    assert obs is not None and obs["attempts"][0]["type"] == "connect"
    _matrix("mut-delete-green", {"observer": "sees-connect"})


def test_t18_mutation_misclassify_red(tmp_path, monkeypatch):
    """Mutant: every error mislabeled -> observer sees wrong type (must be RED)."""
    monkeypatch.setattr(  # MUTANT
        sa_evidence, "classify_transport_type", lambda exc: "read_timeout"
    )
    raised = _run_failing_transport(monkeypatch, tmp_path)
    label = sa_evidence.find_transport_obs(raised)["attempts"][0]["type"]
    if label == "connect":
        pytest.fail("MUTATION DUD -- STOP, report: mislabeled transport still reads connect")
    assert label == "read_timeout"  # RED confirmed: wrong label is visible
    _matrix("mut-misclassify-red", {"observer": "sees-read_timeout"})


def test_t18_mutation_misclassify_reverted_green(tmp_path, monkeypatch):
    raised = _run_failing_transport(monkeypatch, tmp_path)
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["attempts"][0]["type"] == "connect"
    assert obs["attempts"][0]["chain"][0] == "ConnectError"
    _matrix("mut-misclassify-green", {"observer": "sees-connect"})


# -- T18-i: sandbox case-matrix artifact (secret-free) ---------------------------------


def test_t18_zz_matrix_artifact():
    """Persist the secret-free case matrix to the ticket sandbox dir."""
    out_dir = _REPO_ROOT / "verification" / "sa-transport-obs-2a"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "SA-TRANSPORT-OBS-2A",
        "note": (
            "offline only; local POST != backend received/billed; "
            "same-shape reproduction, no historical root-cause claim"
        ),
        "cases": MATRIX_ROWS,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    assert "t18-synthetic-value" not in text
    assert "Bearer" not in text
    path = out_dir / "case-matrix.json"
    path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["ticket"] == "SA-TRANSPORT-OBS-2A"
