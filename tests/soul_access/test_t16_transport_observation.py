"""SA-TRANSPORT-OBS-1 T16 transport-observation tests (sandbox only).

Decision-complete scope (SA-TRANSPORT-OBS-1): exit-7 transport failures gain
persisted, whitelisted error evidence without changing transport behavior.
Product edits are ``src/llm/proxy.py`` (per-attempt timing/count, safe
snapshot capture, exhausted-raise attach), ``src/soul_access/wiring.py``
(three wrapper pass-throughs marking complete/submit/result boundaries),
``src/soul_access/evidence.py`` (whitelisted diagnosis builders + atomic
landing) and ``verification/sa-open-fire-1/live-fire-2/live_fire_b2.py``
(exit-7 landing into ``transport/``, never ``evidence/``). Timeouts, retry /
backoff, transport path and send order are untouched.

All test names carry the t16 marker: ``pytest -q tests/soul_access -k t16``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected via
``monkeypatch`` (the module-global fake never leaves the test; no real
request, no server, no port is ever bound). ``asyncio.run`` is never used:
the in-process coroutine pump from T14 drives fakes loop-free. Only synthetic
data and sandboxes (``tmp_path``) are used.

Coverage (ticket acceptance mapping):
  a. isolated parametrized drill over the five mapped types + unknown; fake
     transport + execution-time socket hard block; synthetic data only;
  b. read-back of the landed file: five fields, cause chain, errno, elapsed,
     call count, dialog/evidence request_id association;
  c. the three wiring excepts, concurrency isolation, landing-failure and
     unconfigured-observation compatibility;
  d. AST pins on timeout / retry / path (+ wiring triple-boundary pin);
  e. bidirectional mutations: missing field, wrong mapping, wrong count, leak
     refusal, retry change -- each RED then GREEN, DUD stops the run.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import socket
import sys
import threading
from pathlib import Path
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

NON_ROUTABLE_BASE = "http://t16-invalid.invalid/v1/chat/completions"
SENTINEL_SECRET = "t16-sandbox-secret-4b1e0d9a"
MODEL = "do-not-deploy-model-t16"
AGENT = "agent_rem"

LIVE_B2_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-2" / "live_fire_b2.py"
)


def _load_b2():
    spec = importlib.util.spec_from_file_location("live_fire_b2_t16", LIVE_B2_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _FakeResp:
    def __init__(self, raw_text, body):
        self._raw_text = raw_text
        self._body = body
        self.status_code = 200

    @property
    def text(self):
        return self._raw_text

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class _FakeClient:
    """httpx.AsyncClient stand-in: records ACTUAL post calls, one list."""

    def __init__(self, behavior, calls, lock):
        self._behavior = behavior
        self._calls = calls
        self._lock = lock

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        with self._lock:
            self._calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        return self._behavior(self._calls[-1])


def _install_fake(monkeypatch, behavior):
    calls = []
    lock = threading.Lock()

    def _factory(*args, **kwargs):
        return _FakeClient(behavior, calls, lock)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls


def _run(coro):
    """Loop-free coroutine pump (T14 idiom: no event loop, no I/O)."""
    send_value = None
    throw_exc = None
    started = False
    while True:
        try:
            if throw_exc is not None:
                exc, throw_exc = throw_exc, None
                yielded = coro.throw(exc)
            elif not started:
                started = True
                yielded = coro.send(None)
            else:
                yielded = coro.send(send_value)
        except StopIteration as done:
            return done.value
        try:
            if asyncio.iscoroutine(yielded):
                send_value = _run(yielded)
            else:
                raise AssertionError(
                    "t16: fake suspended on %r (a real loop would be needed)" % (yielded,)
                )
        except BaseException as exc:
            send_value = None
            throw_exc = exc


def _complete(backend, **over):
    kw = {
        "messages": [{"role": "user", "content": "t16 probe"}],
        "model": MODEL,
        "max_tokens": 16,
        "temperature": 0.5,
        "max_retries": 0,
    }
    kw.update(over)
    return _run(backend.complete(**kw))


def _snapshot(**over):
    kw = {
        "identity": sa_schema.RequestIdentity(
            request_id="req-t16-0001",
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-t16-0001",
            idempotency_key="key-t16-0001",
        ),
        "dedup": sa_schema.DedupKey(
            principal_id="t16-offline-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t16-0001",
        ),
        "backend_alias": "primary",
        "model": MODEL,
        "params": (("temperature", "0.5"), ("max_tokens", "8")),
        "budget_tokens": 512,
        "deadline_utc": "2026-11-12T00:00:00Z",
        "personality_version": "personality-t16-v1",
        "emotion_state": "calm",
        "relationship_state": "trusted",
        "chrono_state": "2026-11-11T17:00:00-05:00",
        "inner_life_state": "inner-t16-v1",
        "projection_state": "projection-t16-v1",
        "recall_version": "recall-t16-v1",
        "recall_items": ("recall-t16-item-1",),
        "history_pool_version": "pool-t16-v1",
        "clean_history": ("hist-t16-item-1",),
    }
    kw.update(over)
    return sa_schema.SoulSnapshot(**kw)


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _run_submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


# -- T16-a: isolated parametrized drill (five types + unknown) -----------------

_TRANSPORT_CASES = [
    ("ConnectError", httpx.ConnectError, "connect"),
    ("ConnectTimeout", httpx.ConnectTimeout, "connect_timeout"),
    ("ReadTimeout", httpx.ReadTimeout, "read_timeout"),
    ("WriteTimeout", httpx.WriteTimeout, "write_timeout"),
    ("PoolTimeout", httpx.PoolTimeout, "pool_timeout"),
    ("TimeoutException", httpx.TimeoutException, "unknown"),
]


@pytest.mark.parametrize(
    "cls_name,cls,label",
    [pytest.param(c[0], c[1], c[2], id=c[0]) for c in _TRANSPORT_CASES],
)
def test_t16_transport_drill_type_label_phase_count(tmp_path, monkeypatch, cls_name, cls, label):
    """Each transport class maps to its label; single attempt; real count."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise cls("t16 simulated %s" % cls_name)

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")

    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    assert "exhausted 1 attempt" in str(raised)  # evidence mode: zero retry

    assert len(calls) == 1  # exactly one POST
    found = sa_evidence.find_transport_obs(raised)
    assert found is not None
    assert found["kind"] == "sa-transport-diagnosis"
    assert found["version"] == "sa-transport-obs-1"
    assert found["observation_ok"] is True
    assert found["transport_calls"] == 1 == len(calls)
    assert found["backend_observations"] is None  # no channel: never 0/count
    assert isinstance(found["evidence_request_id"], str)
    assert found["evidence_request_id"].startswith("req-")
    assert len(found["attempts"]) == 1
    attempt = found["attempts"][0]
    assert set(attempt.keys()) == {"seq", "type", "chain", "errno", "elapsed_ms", "phase"}
    assert attempt["seq"] == 0
    assert attempt["type"] == label  # type inference, not DNS/TCP/TLS measurement
    assert attempt["chain"][0] == cls_name
    assert attempt["phase"] == "post"
    assert isinstance(attempt["elapsed_ms"], (int, float)) and attempt["elapsed_ms"] >= 0


def test_t16_transport_socket_hard_blocked_at_runtime():
    """Execution-time proof: real sockets cannot be opened in this suite."""
    with pytest.raises(RuntimeError):
        socket.socket()


def test_t16_transport_errno_self_cause_context(tmp_path, monkeypatch):
    """errno: self -> cause -> context first int wins; missing stays None."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 connect") from OSError(111, "t16 refused")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    found = sa_evidence.find_transport_obs(raised)
    attempt = found["attempts"][0]
    assert attempt["type"] == "connect"
    assert attempt["chain"] == ["ConnectError", "OSError"]
    assert attempt["errno"] == 111


def test_t16_transport_errno_missing_is_none(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ReadTimeout("t16 bare timeout")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    attempt = sa_evidence.find_transport_obs(raised)["attempts"][0]
    assert attempt["errno"] is None  # no guessing, no coercion from messages


def test_t16_transport_errno_non_int_ignored(tmp_path, monkeypatch):
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        err = httpx.WriteTimeout("t16 odd errno")
        err.errno = "EPIPE"  # non-int: must be ignored, never parsed
        raise err

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    attempt = sa_evidence.find_transport_obs(raised)["attempts"][0]
    assert attempt["errno"] is None


# -- T16-b: read-back + association --------------------------------------------

def test_t16_transport_wiring_association_and_readback(tmp_path, monkeypatch):
    """Dialog request_id binds on pass-through; landed file reads back whole."""
    def _behavior(call):
        raise httpx.ConnectError("t16 down") from OSError(113, "t16 unreachable")

    _install_fake(monkeypatch, _behavior)
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
    )
    dialog = factory(_snapshot())
    # Single call: capture the refusal (each call mints its own evidence id).
    try:
        dialog((("user", "t16 assoc"),), agent_id=AGENT, request_id="req-t16-dialog-1")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None
    obs = sa_evidence.find_transport_obs(raised)
    assert obs is not None
    assert obs["dialog_request_id"] == "req-t16-dialog-1"  # caller association
    assert obs["boundaries"].get("submit") is True  # pump surfaces at submit
    assert isinstance(raised.__cause__, RuntimeError)  # cause chain kept

    landed = sa_evidence.record_transport_diagnosis(
        directory=tmp_path / "transport",
        dialog_request_id=obs["dialog_request_id"],
        diagnosis=obs,
    )
    assert landed == tmp_path / "transport" / "req-t16-dialog-1.json"
    stored = json.loads(landed.read_text(encoding="utf-8"))
    assert stored["kind"] == "sa-transport-diagnosis"
    assert stored["transport_calls"] == 1
    assert stored["backend_observations"] is None
    assert stored["dialog_request_id"] == "req-t16-dialog-1"
    assert stored["evidence_request_id"] == obs["evidence_request_id"]
    assert len(stored["attempts"]) == 1
    attempt = stored["attempts"][0]
    assert set(attempt.keys()) == {"seq", "type", "chain", "errno", "elapsed_ms", "phase"}
    assert (attempt["type"], attempt["chain"], attempt["errno"], attempt["phase"]) == (
        "connect",
        ["ConnectError", "OSError"],
        113,
        "post",
    )
    # The pre-send evidence request record exists; the pair stays broken
    # (response never arrived): exactly one evidence file, transport separate.
    ev_names = sorted(p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-"))
    assert len(ev_names) == 1 and ev_names[0].endswith(".request.json")


def test_t16_transport_live_fire_landing_and_unqualified(tmp_path, monkeypatch):
    """Live-fire helper lands transport/ file; snapshot-less errors stay exit-7."""
    b2 = _load_b2()
    monkeypatch.setattr(b2, "LIVE_DIR", tmp_path)

    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t16-ev-1",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0,
                exc_type="read_timeout",
                chain=["ReadTimeout"],
                errno=None,
                elapsed_ms=12.5,
                phase="post",
            )
        ],
        boundaries={"submit": True},
        observation_ok=True,
    )
    boom = RuntimeError("OpenAIBackend exhausted 1 attempt, last error: t16")
    sa_evidence.attach_transport_obs(boom, diag)
    refused = sa_bridge.LiveRefused(500, "internal", "soul-access dialog failed: RuntimeError")
    sa_evidence.propagate_transport_obs(refused, boom, boundary="result", request_id="req-t16-x")

    assert b2._land_transport_obs(refused) is True
    landed = tmp_path / "transport" / ("%s.json" % b2.DIALOG_REQUEST_ID)
    stored = json.loads(landed.read_text(encoding="utf-8"))
    assert stored["dialog_request_id"] == b2.DIALOG_REQUEST_ID
    assert stored["evidence_request_id"] == "req-t16-ev-1"
    assert stored["attempts"][0]["type"] == "read_timeout"
    assert stored["boundaries"] == {"submit": True, "result": True}
    # Never mixed into the evidence pair dir (success criterion: exactly 2).
    assert not (tmp_path / "evidence").exists()

    bare = RuntimeError("t16 no snapshot anywhere")
    assert b2._land_transport_obs(bare) is False
    assert len(list((tmp_path / "transport").iterdir())) == 1  # no second file


# -- T16-c: three excepts, concurrency, landing-failure, unconfigured ---------

def _boom_with_obs(label_chain=("RuntimeError",), errno=None):
    boom = RuntimeError("t16-boom")
    sa_evidence.attach_transport_obs(
        boom,
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t16-ev-boom",
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


def test_t16_transport_except_complete_boundary():
    """Sync-raising backend.complete -> complete boundary marked, cause kept."""

    class _SyncBoom:
        def complete(self, **kw):
            raise _boom_with_obs()

    llm = SimpleNamespace(backend=_SyncBoom())
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_run_submit)
    dialog = factory(_snapshot())
    try:
        dialog((("user", "t16"),), agent_id=AGENT, request_id="req-t16-c")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None
    assert isinstance(raised.__cause__, RuntimeError)
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["boundaries"].get("complete") is True
    assert obs["dialog_request_id"] == "req-t16-c"


def test_t16_transport_except_submit_boundary():
    """submit() raising -> submit boundary marked, original cause kept."""
    boom = _boom_with_obs()

    class _AsyncBackend:
        async def complete(self, **kw):
            return "t16-unreached"

    def _boom_submit(loop, coro):
        coro.close()
        raise boom

    llm = SimpleNamespace(backend=_AsyncBackend())
    factory = wiring.build_dialog_factory(llm=llm, loop=object(), submit=_boom_submit)
    dialog = factory(_snapshot())
    try:
        dialog((("user", "t16"),), agent_id=AGENT, request_id="req-t16-s")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None
    assert raised.__cause__ is boom
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["boundaries"].get("submit") is True
    assert obs["dialog_request_id"] == "req-t16-s"


def test_t16_transport_except_result_boundary():
    """future.result() raising -> result boundary marked, original cause kept."""
    boom = _boom_with_obs()

    class _AsyncBackend:
        async def complete(self, **kw):
            return "t16-unreached"

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
        dialog((("user", "t16"),), agent_id=AGENT, request_id="req-t16-r")
        raised = None
    except sa_bridge.LiveRefused as exc:
        raised = exc
    assert raised is not None
    assert raised.__cause__ is boom
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["boundaries"].get("result") is True
    assert obs["dialog_request_id"] == "req-t16-r"


def test_t16_transport_concurrent_shared_backend_isolated(tmp_path, monkeypatch):
    """One shared backend, six threads: per-call records, no cross-talk."""

    def _behavior(call):
        index = int(call["json"]["messages"][-1]["content"].rsplit("-", 1)[-1])
        cls = _TRANSPORT_CASES[index % len(_TRANSPORT_CASES)][1]
        raise cls("t16 conc %d" % index)

    _install_fake(monkeypatch, _behavior)
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    out = [{} for _ in range(6)]
    errors = {}

    def _work(index):
        try:
            _run(backend.complete(
                messages=[{"role": "user", "content": "t16-conc-%d" % index}],
                model=MODEL,
                max_tokens=16,
                temperature=0.5,
                max_retries=0,
                evidence_sink=sink,
            ))
        except RuntimeError as exc:
            out[index]["obs"] = sa_evidence.find_transport_obs(exc)
        except Exception as exc:  # noqa: BLE001
            errors[index] = exc

    threads = [threading.Thread(target=_work, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == {}
    labels = []
    ev_ids = set()
    for i in range(6):
        obs = out[i]["obs"]
        assert obs is not None
        assert obs["transport_calls"] == 1
        assert len(obs["attempts"]) == 1 and obs["attempts"][0]["seq"] == 0
        assert obs["attempts"][0]["phase"] == "post"
        labels.append(obs["attempts"][0]["type"])
        ev_ids.add(obs["evidence_request_id"])
    assert labels == [c[2] for c in _TRANSPORT_CASES]  # each thread kept its own label
    assert len(ev_ids) == 6  # independent records, never a shared cache


def test_t16_transport_observation_failure_never_masks_error(tmp_path, monkeypatch):
    """Broken observation machinery still surfaces the ORIGINAL error."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 down")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    monkeypatch.setattr(
        sa_evidence,
        "build_transport_diagnosis",
        lambda **kw: (_ for _ in ()).throw(ValueError("t16 mutant obs broken")),
    )
    with pytest.raises(RuntimeError, match="exhausted 1 attempt"):
        _complete(backend, evidence_sink=sink)


def test_t16_transport_unconfigured_observation_compatible(tmp_path, monkeypatch):
    """No sink: same exhausted error, zero new files, evidence id honestly None."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ReadTimeout("t16 slow")

    calls = _install_fake(monkeypatch, _behavior)
    before = {p.name for p in tmp_path.iterdir()}
    try:
        _complete(backend)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None
    assert "exhausted 1 attempt" in str(raised)
    assert len(calls) == 1
    assert {p.name for p in tmp_path.iterdir()} == before  # nothing landed anywhere
    obs = sa_evidence.find_transport_obs(raised)
    assert obs is not None
    assert obs["evidence_request_id"] is None
    assert obs["attempts"][0]["type"] == "read_timeout"


def test_t16_transport_landing_failure_compat(tmp_path, monkeypatch):
    """Landing refusal stays an observation miss, never an exit-code change."""
    b2 = _load_b2()
    monkeypatch.setattr(b2, "LIVE_DIR", tmp_path)
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id=None,
        evidence_request_id="req-t16-ev-2",
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
    boom = RuntimeError("OpenAIBackend exhausted 1 attempt, last error: t16")
    sa_evidence.attach_transport_obs(boom, diag)
    monkeypatch.setattr(
        sa_evidence,
        "record_transport_diagnosis",
        lambda **kw: (_ for _ in ()).throw(
            sa_evidence.EvidenceStoreError("t16 mutant disk full")
        ),
    )
    assert b2._land_transport_obs(boom) is False  # unqualified, original error kept
    assert not (tmp_path / "transport").exists()


# -- T16-d: AST pins (timeout / retry / path) ----------------------------------

def _proxy_source() -> str:
    return (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")


def _pin_results(source_text) -> dict:
    """Guard checks for the frozen transport shape; {name: passed}."""
    tree = ast.parse(source_text)
    found_timeout = []
    found_pop_default = []
    found_zero_retry = []
    found_status_set = []
    found_sleep = []
    found_post = []

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
                        found_timeout.append(kw.value.value)
            if name == "kwargs.pop" and len(node.args) == 2:
                first, default = node.args
                if (
                    isinstance(first, ast.Constant)
                    and first.value == "max_retries"
                    and isinstance(default, ast.Constant)
                ):
                    found_pop_default.append(default.value)
            if name == "client.post":
                found_post.append(True)
            if name in ("asyncio.sleep", "sleep"):
                found_sleep.append(True)
            self.generic_visit(node)

        def visit_If(self, node):  # noqa: N802
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "_evidence"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.IsNot)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value is None
            ):
                for stmt in node.body:
                    if (
                        isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                        and stmt.targets[0].id == "max_retries"
                        and isinstance(stmt.value, ast.Constant)
                    ):
                        found_zero_retry.append(stmt.value.value)
            self.generic_visit(node)

        def visit_Assign(self, node):  # noqa: N802
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "RETRYABLE_STATUS"
                and isinstance(node.value, ast.Set)
            ):
                codes = set()
                for elt in node.value.elts:
                    if isinstance(elt, ast.Constant):
                        codes.add(elt.value)
                found_status_set.append(codes)
            self.generic_visit(node)

    _Visitor().visit(tree)
    lines = source_text.splitlines()
    count_idx = next(
        (i for i, line in enumerate(lines) if "_transport_calls += 1" in line), None
    )
    post_idx = next(
        (i for i, line in enumerate(lines) if "client.post(" in line), None
    )
    return {
        "timeout==120": bool(found_timeout) and all(t == 120.0 for t in found_timeout),
        "retry-default-3": 3 in found_pop_default,
        "evidence-zero-retry": found_zero_retry == [0],
        "retryable-set": any(s == {429, 500, 502, 503, 504, 529} for s in found_status_set),
        "backoff-sleep": bool(found_sleep),
        "post-path": bool(found_post),
        "count-before-post": (
            count_idx is not None and post_idx is not None and count_idx < post_idx
        ),
    }


def test_t16_ast_transport_pins_hold():
    pins = _pin_results(_proxy_source())
    failed = sorted(name for name, ok in pins.items() if not ok)
    assert not failed, "transport pins drifted: %r" % (failed,)


def test_t16_ast_wiring_triple_boundary():
    source = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(encoding="utf-8")
    assert source.count("propagate_transport_obs(") == 3
    for boundary in ("complete", "submit", "result"):
        assert 'boundary="%s"' % boundary in source
    assert source.count("raise _refused from exc") == 3


# -- T16-e: bidirectional mutations (RED then GREEN, DUD stops) ---------------

def test_t16_mutation_missing_field_red(tmp_path, monkeypatch):
    """Relaxed mutant: attempt missing one of the five fields -> refused (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 mutant missing-field")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    real_attempt = sa_evidence.build_transport_attempt

    def _mutant_attempt(**kw):  # MUTANT: drop errno from the record
        record = real_attempt(**kw)
        record.pop("errno")
        return record

    monkeypatch.setattr(sa_evidence, "build_transport_attempt", _mutant_attempt)
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    assert raised is not None  # original error still surfaces ...
    if sa_evidence.find_transport_obs(raised) is not None:
        pytest.fail("MUTATION DUD -- STOP, report: field-less attempt was accepted")
    # ... and the malformed record is refused loudly at build time:
    with pytest.raises(ValueError):
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t16-m",
            transport_calls=1,
            backend_observations=None,
            attempts=[{"seq": 0, "type": "connect", "chain": ["ConnectError"],
                       "elapsed_ms": 1.0, "phase": "post"}],
            boundaries={},
            observation_ok=True,
        )
    # RED confirmed: nothing half-landed.


def test_t16_mutation_missing_field_reverted_green(tmp_path, monkeypatch):
    """Revert: honest five-field attempt lands (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 green missing-field revert")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    obs = sa_evidence.find_transport_obs(raised)
    assert obs is not None
    assert set(obs["attempts"][0].keys()) == {
        "seq", "type", "chain", "errno", "elapsed_ms", "phase",
    }


def test_t16_mutation_wrong_mapping_red(tmp_path, monkeypatch):
    """Corrupt mutant: ConnectError mislabeled -> detector trips (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 mutant mismap")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    monkeypatch.setattr(  # MUTANT: every error reads as read_timeout
        sa_evidence, "classify_transport_type", lambda exc: "read_timeout"
    )
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    label = sa_evidence.find_transport_obs(raised)["attempts"][0]["type"]
    if label == "connect":
        pytest.fail("MUTATION DUD -- STOP, report: mislabeled transport still reads connect")
    assert label == "read_timeout"  # RED confirmed: the wrong label is visible.


def test_t16_mutation_wrong_mapping_reverted_green(tmp_path, monkeypatch):
    """Revert: ConnectError reads connect (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectError("t16 green mismap revert")

    _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["attempts"][0]["type"] == "connect"
    assert obs["attempts"][0]["chain"][0] == "ConnectError"


def test_t16_mutation_wrong_count_red(tmp_path, monkeypatch):
    """Corrupt mutant: transport_calls inflated -> count check trips (RED)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ReadTimeout("t16 mutant miscount")

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    real_diagnosis = sa_evidence.build_transport_diagnosis

    def _mutant_diagnosis(**kw):  # MUTANT: inflate the call count by one
        kw = dict(kw)
        kw["transport_calls"] = kw["transport_calls"] + 1
        return real_diagnosis(**kw)

    monkeypatch.setattr(sa_evidence, "build_transport_diagnosis", _mutant_diagnosis)
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    recorded = sa_evidence.find_transport_obs(raised)["transport_calls"]
    if recorded == len(calls) == 1:
        pytest.fail("MUTATION DUD -- STOP, report: inflated count reads as 1")
    assert recorded == 2 != len(calls)  # RED confirmed: count != reality.


def test_t16_mutation_wrong_count_reverted_green(tmp_path, monkeypatch):
    """Revert: transport_calls == actual POSTs (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ReadTimeout("t16 green miscount revert")

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    try:
        _complete(backend, evidence_sink=sink)
        raised = None
    except RuntimeError as exc:
        raised = exc
    obs = sa_evidence.find_transport_obs(raised)
    assert obs["transport_calls"] == len(calls) == 1


def test_t16_mutation_leak_refused_red(tmp_path):
    """Leak mutant: credential-shaped text in the diagnosis -> refused (RED)."""
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id="Bearer t16-secret-should-never-land",
        evidence_request_id="req-t16-m",
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
    try:
        sa_evidence.serialize_transport_diagnosis(diag)
        serialized = True
    except sa_evidence.EvidenceStoreError:
        serialized = False
    if serialized:
        pytest.fail("MUTATION DUD -- STOP, report: Bearer text serialized without refusal")
    with pytest.raises(sa_evidence.EvidenceStoreError):
        sa_evidence.record_transport_diagnosis(
            directory=tmp_path / "transport",
            dialog_request_id="req-t16-m",
            diagnosis=diag,
        )
    assert not (tmp_path / "transport").exists()  # RED confirmed: nothing landed.
    assert SENTINEL_SECRET not in json.dumps(diag)  # key_ref-style name only, no value


def test_t16_mutation_leak_reverted_green(tmp_path):
    """Revert: clean diagnosis serializes and lands (GREEN)."""
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id="req-t16-clean",
        evidence_request_id="req-t16-ev-clean",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0, exc_type="pool_timeout", chain=["PoolTimeout"],
                errno=None, elapsed_ms=2.5, phase="post",
            )
        ],
        boundaries={"complete": True},
        observation_ok=True,
    )
    text = sa_evidence.serialize_transport_diagnosis(diag)
    assert "Bearer" not in text and "api_key" not in text
    landed = sa_evidence.record_transport_diagnosis(
        directory=tmp_path / "transport",
        dialog_request_id="req-t16-clean",
        diagnosis=diag,
    )
    assert json.loads(landed.read_text(encoding="utf-8"))["attempts"][0]["type"] == "pool_timeout"


def test_t16_mutation_retry_change_red():
    """Guard-sensitivity mutant: zero-retry pin edited -> pins fail (RED).

    String-level only (never executed, production untouched): proves the AST
    guard would catch a retry edit.
    """
    source = _proxy_source()
    assert _pin_results(source)["evidence-zero-retry"] is True  # baseline GREEN
    mutated = source.replace(
        "max_retries = 0  # 取證模式固定零重試",
        "max_retries = 1  # 取證模式固定零重試",
        1,
    )
    assert mutated != source
    if _pin_results(mutated)["evidence-zero-retry"] is True:
        pytest.fail("MUTATION DUD -- STOP, report: retry edit invisible to pins")
    # RED confirmed: the edited source no longer passes the zero-retry pin.


def test_t16_mutation_retry_change_reverted_green(tmp_path, monkeypatch):
    """Revert: evidence mode stays single-attempt, no backoff sleep (GREEN)."""
    backend = OpenAIBackend(api_key=SENTINEL_SECRET, base_url=NON_ROUTABLE_BASE)

    def _behavior(call):
        raise httpx.ConnectTimeout("t16 green retry revert")

    calls = _install_fake(monkeypatch, _behavior)
    sink = sa_evidence.EvidenceSink(tmp_path / "ev")
    slept = []
    monkeypatch.setattr(asyncio, "sleep", lambda delay: slept.append(delay) or _noop())

    def _noop():
        async def _inner():
            return None

        return _inner()

    with pytest.raises(RuntimeError, match="exhausted 1 attempt"):
        _complete(backend, evidence_sink=sink)
    assert len(calls) == 1  # zero retry: one POST ever
    assert slept == []  # no backoff wait inside the timed path
    assert _pin_results(_proxy_source())["evidence-zero-retry"] is True
