"""SA-ISOLATED-RECOVERY-1 T17 isolated-recovery acceptance (sandbox only).

Decision-complete scope (SA-ISOLATED-RECOVERY-1, Sol approved): with zero
external connections and zero billed calls, walk the formal
dialog -> backend -> observation chain plus the thin-side formal flow with
new observation readback. Product code (``src/``, ``configs/``,
``scripts/``) is used read-only and stays untouched; every gate / synthetic
config / control file lives under the per-test ``tmp_path`` sandbox.
Credentials are sentinel-only; the target is always ``.invalid``; no
production credential is loaded, no ``.env`` value is read, nothing under
``data/**`` is touched.

All test names carry the t17 marker: ``pytest -q tests/soul_access -k t17``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected
via ``monkeypatch`` (the module-global fake never leaves the test; no real
request, no server, no port is ever bound). The dialog path uses the
loop-free coroutine pump (T14/T16 idiom); the thin ASGI loopback uses the
own-thread loop driver (T12 idiom) because ``httpx.ASGITransport`` needs a
real event loop -- still in-process, never a port. Sync tests only. Only
synthetic data and sandboxes (``tmp_path``) are used.

Coverage (ticket acceptance mapping):
  a. isolated parametrized drill over the six mapped error types through
     the FORMAL dialog path (build_dialog_factory); fake transport +
     execution-time socket hard block; synthetic data only;
  b. read-back of the landed file: five fields, cause chain, errno
     (nullable int), elapsed (non-negative), call count, both request_id
     associations; failure cases keep request only;
  c. success pair criterion (exactly one same-ID request/response pair in
     the evidence dir, sibling transport/ excluded, pair_ok) plus
     extra-file / missing-file counterexamples that the criterion must
     reject (no false-pass);
  d. thin side: 403 + zero transmission per gate (G1/G2), temporary open
     of instance + sandbox gates, exactly one success, re-close +
     re-verify, restore in finally; ThinControl graceful shutdown;
  e. bidirectional mutations: missing field, wrong pairing, gate-relaxed,
     gate-forced-closed -- each RED then GREEN, DUD stops the run;
  f. pins: template inner/file SHA, payload/raw/usage pins vs
     continuation-1 canonical bytes, old evidence + dual sent.json SHA
     unchanged, AST zero-retry + wiring triple-boundary pins, socket-block
     proof, redaction scan.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import queue
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]

if str(_REPO_ROOT) not in sys.path:  # noqa: E402
    sys.path.append(str(_REPO_ROOT))

from src.interfaces.credentials import digest_of  # noqa: E402
from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

import scripts.serve_sa_thin as thin_mod  # noqa: E402

#: Non-routable upstream (structural cut; the fake asserts this suffix).
NON_ROUTABLE_BASE = "http://t17-invalid.invalid/v1/chat/completions"
#: Isolation sentinel: placeholder string, never a usable credential.
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"
#: Original-ticket fixed route pins (read-only reference).
MODEL = "deepseek-v4.1-flash:1017"
SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
TEMPERATURE = 0.5
MAX_TOKENS = 8
SUCCESS_DIALOG_ID = "req-sa-open-fire-1-0001"
SYNTHETIC_MARK = "合成資料（隔離演練，非實彈）"
THIN_SUCCESS_TEXT = "合成回覆：薄端隔離成功一次。"
#: Pinned digests (read-only comparison; from continuation-1 artifacts).
PIN_TEMPLATE_INNER_SHA = "418af81353a2457d0f3fae7459c6931e6fadb16afcbb41261cc9916d1915eeb1"
PIN_PAYLOAD_SHA = "7043a3ffb0ce7a170c690feae6611a0231418ecb37996ca2978fa2474e81475c"
PIN_RAW_SHA = "8860307705e5bde75d4e9c2b2395fd1c4c8aa325f46334a19fec35f2eb0b25dd"
PIN_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}

#: Six cases: short name / raised exception class / expected obs type label.
ERROR_CASES = (
    ("connect", "ConnectError", httpx.ConnectError, "connect"),
    ("connect-timeout", "ConnectTimeout", httpx.ConnectTimeout, "connect_timeout"),
    ("read-timeout", "ReadTimeout", httpx.ReadTimeout, "read_timeout"),
    ("write-timeout", "WriteTimeout", httpx.WriteTimeout, "write_timeout"),
    ("pool-timeout", "PoolTimeout", httpx.PoolTimeout, "pool_timeout"),
    ("unknown", "TimeoutException", httpx.TimeoutException, "unknown"),
)

#: Thin-side synthetic identity (never production identity / memory).
THIN_OWNER = "t17-isolated-thin-owner"
THIN_TOKEN = "t17-thin-sandbox-token"
THIN_KEY_REF = "SA_T17_THIN_KEY"
THIN_FAKE_KEY = "t17-sandbox-fake-key"
THIN_BACKEND = "t17-thin"
THIN_PRIMARY_MODEL = "sa-t17-primary"
THIN_STANDBY_MODEL = "sa-t17-standby"

TEMPLATE_PATH = _REPO_ROOT / "verification" / "sa-open-fire-1" / "request.template.json"
CONT1_EV = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-evidence"
)

#: Old evidence + dual sent: read-only files whose SHA must not move.
OLD_WATCH_PATHS = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "request.template.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-gate-open.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-gate-closed.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "fire-report.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "crosscheck-report.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "negative-report.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "preflight-continuation-1.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "STATUS-continuation-1.md",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-1" / "sent.json",
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-2" / "sent.json",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot_old_shas() -> dict:
    out = {}
    for path in OLD_WATCH_PATHS:
        out[str(path.relative_to(_REPO_ROOT))] = _sha_file(path)
    for sub in ("live-fire-1/evidence", "live-fire-2/evidence"):
        evdir = _REPO_ROOT / "verification" / "sa-open-fire-1" / sub
        for child in sorted(evdir.iterdir()):
            if child.is_file() and not child.name.startswith(".tmp-"):
                out[str(child.relative_to(_REPO_ROOT))] = _sha_file(child)
    for child in sorted(CONT1_EV.iterdir()):
        if child.is_file() and not child.name.startswith(".tmp-"):
            out[str(child.relative_to(_REPO_ROOT))] = _sha_file(child)
    return out


def _synthetic_body() -> dict:
    """Bit-identical synthetic reply (raw/usage pins derive from this)."""
    return {
        "id": "chatcmpl-synthetic-isolation-0001",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "synthetic": SYNTHETIC_MARK,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "合成回覆：隔離演練單發成功。"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


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


_BEHAVIOR = {"kind": "unset", "exc": None}


def _dump_canonical(body) -> str:
    """Canonical dump for the fake transport (module-level: no `json` shadow)."""
    return json.dumps(body, ensure_ascii=False, sort_keys=True)


class _FakeClient:
    """Fake transport: only .invalid upstream; behavior set per case."""

    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        if ".invalid" not in str(url):
            raise RuntimeError("isolation breach: non-.invalid upstream refused")
        self._calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        if _BEHAVIOR["kind"] == "raise":
            raise _BEHAVIOR["exc"]
        if _BEHAVIOR["kind"] == "ok":
            body = _synthetic_body()
            return _FakeResp(_dump_canonical(body), body)
        raise RuntimeError("isolation breach: fake behavior unset")


def _install_fake(monkeypatch):
    calls = []

    def _factory(*args, **kwargs):
        return _FakeClient(calls)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return calls


def _run(coro):
    """Loop-free coroutine pump (T14/T16 idiom: fakes complete immediately)."""
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
                    "t17: fake suspended on %r (a real loop would be needed)" % (yielded,)
                )
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _run_submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


def _snapshot(principal: str) -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=SUCCESS_DIALOG_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-t17-1",
            idempotency_key="key-t17-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id=principal,
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-t17-1",
        ),
        backend_alias=BACKEND_ALIAS,
        model=MODEL,
        params=(("temperature", str(TEMPERATURE)), ("max_tokens", str(MAX_TOKENS))),
        budget_tokens=512,
        deadline_utc="2026-11-15T00:00:00Z",
        personality_version="personality-t17-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-15T15:45:00-05:00",
        inner_life_state="inner-t17-v1",
        projection_state="projection-t17-v1",
        recall_version="recall-t17-v1",
        recall_items=("recall-t17-item-1",),
        history_pool_version="pool-t17-v1",
        clean_history=("hist-t17-item-1",),
    )


def _old_payload_and_raw():
    old_request_payload = None
    old_raw_bytes = None
    for child in sorted(CONT1_EV.iterdir()):
        if child.name.endswith(".request.json"):
            old_request_payload = sa_evidence.canonical_bytes(
                json.loads(child.read_text(encoding="utf-8"))["payload"]
            )
        elif child.name.endswith(".response.json"):
            old_raw_bytes = json.loads(child.read_text(encoding="utf-8"))["raw_text"].encode(
                "utf-8"
            )
    assert old_request_payload is not None and old_raw_bytes is not None
    return (old_request_payload, old_raw_bytes)


def _check_pair(evidence_dir: Path):
    """Success criterion: exactly one same-ID request/response pair + pair_ok.

    Scans ONLY ``evidence_dir`` (sibling transport/ or other dirs never
    count); ``.tmp-*`` residue never counts. Returns ``(ok, detail)``.
    """
    names = sorted(
        p.name for p in Path(evidence_dir).iterdir() if not p.name.startswith(".tmp-")
    )
    if len(names) != 2:
        return (False, "count!=2: %s" % (names,))
    stem = names[0].split(".request.json")[0]
    if names != ["%s.request.json" % stem, "%s.response.json" % stem]:
        return (False, "stem/pair mismatch: %s" % (names,))
    try:
        report = sa_evidence.EvidenceSink(evidence_dir).verify_pair(stem)
    except Exception as exc:  # noqa: BLE001
        return (False, "verify raised %s" % type(exc).__name__)
    if report.get("pair_ok") is not True:
        return (False, "pair_ok false: %s" % (report,))
    return (True, stem)


# -- module-level loop driver for ASGI loopback (T12 idiom: in-process) ------

_T17_LOOP = asyncio.new_event_loop()


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


_T17_RUNNER = _Runner(_T17_LOOP, "t17-outward")


def _asgi_run(coro):
    return _T17_RUNNER.run(coro)


class _CountingSender:
    """Thin-side formal forward bottom-I/O stand-in: counts, fixed text."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body):
        self.calls.append({"url": url, "body": body})
        return THIN_SUCCESS_TEXT


class _ZeroSender:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body):
        self.calls.append({"url": url, "body": body})
        raise AssertionError("t17 reclose must not transmit")


class _FakeListener:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeClock:
    def __init__(self):
        self.t = 3000.0

    def __call__(self):
        return self.t


def _thin_files(tmp_path: Path, gate_status: str = "closed"):
    """Synthetic registry/presets/gate files, all under the test sandbox."""
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    registry_path = cfg / "registry.json"
    presets_path = cfg / "presets.json"
    gate_path = tmp_path / "gate.json"
    registry_path.write_text(
        json.dumps(
            {
                "owner_principal_id": THIN_OWNER,
                "backends": [THIN_BACKEND],
                "routes": [
                    {
                        "soul_alias": SOUL_ALIAS,
                        "backend_alias": BACKEND_ALIAS,
                        "agent_id": AGENT,
                        "public_model_id": "sa-t17-%s-%s" % (SOUL_ALIAS, BACKEND_ALIAS),
                        "backend_ref": THIN_BACKEND,
                        "context_window_tokens": 4096,
                    }
                ],
                "clients": [
                    {
                        "client_id": "client-t17-thin",
                        "principal_id": THIN_OWNER,
                        "purpose": "interactive",
                        "allowed_routes": ["%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS)],
                        "scope": "owner_private",
                        "allow_persistent_memory": True,
                        "credential_sha256": digest_of(THIN_TOKEN),
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    presets_path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": BACKEND_ALIAS,
                        "provider": "openai",
                        "model": THIN_PRIMARY_MODEL,
                        "params": {
                            "temperature": 0.5,
                            "max_tokens": 8,
                            "base_url": "https://t17-thin.invalid/v1/chat/completions",
                        },
                        "key_ref": THIN_KEY_REF,
                    },
                    {
                        "name": "standby",
                        "provider": "openai",
                        "model": THIN_STANDBY_MODEL,
                        "params": {
                            "temperature": 0.5,
                            "max_tokens": 8,
                            "base_url": "https://t17-thin.invalid/v1/chat/completions",
                        },
                        "key_ref": THIN_KEY_REF,
                    },
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    gate_path.write_text(json.dumps({"status": gate_status}), encoding="utf-8")
    return (registry_path, presets_path, gate_path)


def _thin_snapshot() -> sa_schema.SoulSnapshot:
    req = "req-t17-thin-0001"
    key = "key-t17-thin-0001"
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=req,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-%s" % req,
            idempotency_key=key,
        ),
        dedup=sa_schema.DedupKey(
            principal_id=THIN_OWNER,
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key=key,
        ),
        backend_alias=BACKEND_ALIAS,
        model=THIN_PRIMARY_MODEL,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-15T00:00:00Z",
        personality_version="personality-t17-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-15T15:45:00-05:00",
        inner_life_state="inner-t17-v1",
        projection_state="projection-t17-v1",
        recall_version="recall-t17-v1",
        recall_items=("recall-t17-item-1",),
        history_pool_version="pool-t17-v1",
        clean_history=("hist-t17-item-1",),
    )


def _post_thin(app):
    async def _go():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        try:
            envelope = sa_api.build_envelope(
                snapshot=_thin_snapshot(),
                messages=(("user", MESSAGE),),
                created_utc="2026-11-15T00:00:00Z",
            )
            return await client.post(
                "/%s/%s/v1/chat/completions" % (SOUL_ALIAS, BACKEND_ALIAS),
                headers={"Authorization": "Bearer " + THIN_TOKEN},
                json=sa_api.envelope_to_body(envelope),
            )
        finally:
            await client.aclose()

    return _asgi_run(_go())


# -- T17-a: formal dialog path, six error types -------------------------------

_ERROR_IDS = [pytest.param(c[0], c[1], c[2], c[3], id=c[0]) for c in ERROR_CASES]


@pytest.mark.parametrize("short,cls_name,cls,label", _ERROR_IDS)
def test_t17_recovery_formal_dialog_error_obs(tmp_path, monkeypatch, short, cls_name, cls, label):
    """Each transport class surfaces through the formal dialog path with its
    own label; single attempt; whitelisted five-field observation lands via
    the formal record fn; failure evidence keeps request only."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "raise"
    _BEHAVIOR["exc"] = cls("t17 simulated %s" % cls_name)
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        dialog_id = "req-t17-dialog-%s" % short
        dialog = factory(_snapshot("t17-offline-owner"))
        try:
            dialog((("user", MESSAGE),), agent_id=AGENT, request_id=dialog_id)
            raised = None
        except sa_bridge.LiveRefused as exc:
            raised = exc
        assert raised is not None
        obs = sa_evidence.find_transport_obs(raised)
        assert obs is not None
        assert obs["kind"] == "sa-transport-diagnosis"
        assert obs["transport_calls"] == 1 == len(calls)
        assert obs["backend_observations"] is None
        assert isinstance(obs["evidence_request_id"], str)
        assert obs["evidence_request_id"].startswith("req-")
        assert len(obs["attempts"]) == 1
        attempt = obs["attempts"][0]
        assert set(attempt.keys()) == {"seq", "type", "chain", "errno", "elapsed_ms", "phase"}
        assert attempt["seq"] == 0
        assert attempt["type"] == label  # mapped from class, never text-guessed
        assert isinstance(attempt["chain"], list) and attempt["chain"][0] == cls_name
        errno = attempt["errno"]
        assert errno is None or (isinstance(errno, int) and not isinstance(errno, bool))
        assert isinstance(attempt["elapsed_ms"], (int, float)) and attempt["elapsed_ms"] >= 0
        assert attempt["phase"] == "post"
        assert obs["dialog_request_id"] == dialog_id
        assert obs["boundaries"].get("submit") is True  # pump surfaces at submit

        carried = dict(obs)
        landed = sa_evidence.record_transport_diagnosis(
            directory=tmp_path / "transport",
            dialog_request_id=dialog_id,
            diagnosis=carried,
        )
        assert landed == tmp_path / "transport" / ("%s.json" % dialog_id)
        stored = json.loads(landed.read_text(encoding="utf-8"))
        assert stored["kind"] == "sa-transport-diagnosis"
        assert stored["dialog_request_id"] == dialog_id
        assert stored["evidence_request_id"] == obs["evidence_request_id"]
        assert stored["transport_calls"] == 1
        assert len(stored["attempts"]) == 1
        assert stored["attempts"][0]["type"] == label

        ev_names = sorted(
            p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
        )
        assert len(ev_names) == 1 and ev_names[0].endswith(".request.json")
        assert ev_names[0] == "%s.request.json" % obs["evidence_request_id"]
        request_obj = json.loads((sink.directory / ev_names[0]).read_text(encoding="utf-8"))
        assert request_obj["request_id"] == obs["evidence_request_id"]
        assert request_obj["payload_sha256"] == PIN_PAYLOAD_SHA
        old_payload, _ = _old_payload_and_raw()
        assert sa_evidence.canonical_bytes(request_obj["payload"]) == old_payload
    finally:
        _BEHAVIOR["kind"] = "unset"
        _BEHAVIOR["exc"] = None


def test_t17_recovery_socket_hard_blocked_at_runtime():
    """Execution-time proof: real sockets cannot be opened in this suite."""
    with pytest.raises(RuntimeError):
        socket.socket()


# -- T17-b/c: success pair criterion + counterexamples ------------------------

def test_t17_recovery_success_pair_criterion_pins_and_sibling_excluded(
    tmp_path, monkeypatch
):
    """Formal success lands exactly one same-ID request/response pair with
    pair_ok; sibling transport/ files never count; payload/raw/usage match
    the pins and continuation-1 canonical bytes."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "ok"
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        text = factory(_snapshot("t17-offline-owner"))(
            (("user", MESSAGE),), agent_id=AGENT, request_id=SUCCESS_DIALOG_ID
        )
        assert text == "合成回覆：隔離演練單發成功。"
        assert len(calls) == 1
        ok, ev_id = _check_pair(sink.directory)
        assert ok, ev_id
        s_request = json.loads(
            (sink.directory / ("%s.request.json" % ev_id)).read_text(encoding="utf-8")
        )
        s_response = json.loads(
            (sink.directory / ("%s.response.json" % ev_id)).read_text(encoding="utf-8")
        )
        assert s_request["payload_sha256"] == PIN_PAYLOAD_SHA
        assert s_response["raw_sha256"] == PIN_RAW_SHA
        assert dict(s_response["usage"]) == dict(PIN_USAGE)
        assert SYNTHETIC_MARK in s_response["raw_text"]
        old_payload, old_raw = _old_payload_and_raw()
        assert sa_evidence.canonical_bytes(s_request["payload"]) == old_payload
        assert s_response["raw_text"].encode("utf-8") == old_raw
        # Sibling transport/ dir is never scanned by the criterion.
        sib = tmp_path / "transport"
        sib.mkdir()
        (sib / ("%s.json" % SUCCESS_DIALOG_ID)).write_text(
            json.dumps({"kind": "sa-transport-diagnosis"}), encoding="utf-8"
        )
        ok2, _ = _check_pair(sink.directory)
        assert ok2 is True
    finally:
        _BEHAVIOR["kind"] = "unset"


def test_t17_recovery_pair_criterion_rejects_extra_and_missing(tmp_path, monkeypatch):
    """Counterexamples: the criterion must not false-pass with an extra file
    or a missing response (RED for both mutants, GREEN for the pristine dir)."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "ok"
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        factory(_snapshot("t17-offline-owner"))(
            (("user", MESSAGE),), agent_id=AGENT, request_id=SUCCESS_DIALOG_ID
        )
        assert len(calls) == 1
        ok, ev_id = _check_pair(sink.directory)
        assert ok is True  # GREEN baseline
        extra_dir = tmp_path / "cex-extra"
        extra_dir.mkdir()
        for name in ("%s.request.json" % ev_id, "%s.response.json" % ev_id):
            (extra_dir / name).write_bytes((sink.directory / name).read_bytes())
        (extra_dir / "junk.txt").write_text("extra", encoding="utf-8")
        ok_extra, _ = _check_pair(extra_dir)
        assert ok_extra is False  # RED: extra file refused
        missing_dir = tmp_path / "cex-missing"
        missing_dir.mkdir()
        (missing_dir / ("%s.request.json" % ev_id)).write_bytes(
            (sink.directory / ("%s.request.json" % ev_id)).read_bytes()
        )
        ok_missing, _ = _check_pair(missing_dir)
        assert ok_missing is False  # RED: missing response refused
    finally:
        _BEHAVIOR["kind"] = "unset"


def test_t17_recovery_redaction_key_ref_only(tmp_path, monkeypatch):
    """No sentinel明文, no Bearer-shaped text in landed files; key_ref only."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "ok"
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        factory(_snapshot("t17-offline-owner"))(
            (("user", MESSAGE),), agent_id=AGENT, request_id=SUCCESS_DIALOG_ID
        )
        assert len(calls) == 1
        for path in sorted(sink.directory.rglob("*.json")):
            if ".tmp-" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            assert SENTINEL_API_KEY not in text
            assert "Bearer " not in text
            assert sa_evidence.KEY_REF in text
    finally:
        _BEHAVIOR["kind"] = "unset"


# -- T17-d: thin side (formal forwarder + formal thin app, ASGI loopback) -----

def test_t17_recovery_thin_g1_instance_closed_refuses(tmp_path, monkeypatch):
    """G1: instance door closed refuses even when the sandbox gate says open."""
    assert thin_mod.is_thin_open() is False
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "open")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    assert response.status_code == 403
    assert sender.calls == []
    assert thin_mod.is_thin_open() is False


def test_t17_recovery_thin_g2_shared_closed_refuses(tmp_path, monkeypatch):
    """G2: shared (sandbox release) gate closed refuses with instance open."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "closed")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    assert response.status_code == 403
    assert sender.calls == []


def test_t17_recovery_thin_open_once_then_reclose(tmp_path, monkeypatch):
    """Both gates briefly open: exactly one success; re-close re-verified;
    doors restored closed (monkeypatch + explicit re-verify)."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "open")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)
    try:
        app_ok = thin_mod.build_thin_app(
            str(registry_path), str(presets_path),
            release_path=str(gate_path), _forwarder=forwarder,
        )
        response = _post_thin(app_ok)
        assert response.status_code == 200
        assert len(sender.calls) == 1
        assert response.json()["choices"][0]["message"]["content"] == THIN_SUCCESS_TEXT
    finally:
        monkeypatch.setattr(thin_mod, "_THIN_OPEN", False)
        gate_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    app_re = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response_re = _post_thin(app_re)
    assert response_re.status_code == 403
    assert len(sender.calls) == 1  # reclose transmitted nothing more
    assert thin_mod.is_thin_open() is False


def test_t17_recovery_thin_control_graceful_shutdown(tmp_path):
    """T15 fake listener: graceful shutdown is pure logic, no port/process."""
    listener = _FakeListener()
    control = thin_mod.ThinControl(
        run_id="run-t17-1",
        token="t17-control-token",
        control_path=str(tmp_path / "control.json"),
        clock=_FakeClock(),
        drain_timeout_s=5.0,
        listener=listener,
        sleeper=lambda s: None,
    )
    control.write_control_file()
    status, payload = control.handle_post(
        "127.0.0.1", {"token": "t17-control-token", "command": "shutdown"}
    )
    assert status == 200 and payload.get("state") == thin_mod.STATE_DRAINING
    assert control.state == thin_mod.STATE_SHUT
    assert listener.closed == 1 and control.result_code == 0
    assert all(ev["run_id"] == "run-t17-1" for ev in control.events)


# -- T17-e: bidirectional mutations (RED then GREEN, DUD stops) ---------------

def test_t17_mutation_missing_field_red():
    """Relaxed mutant: attempt missing one of the five fields -> refused."""
    with pytest.raises(ValueError):
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t17-m",
            transport_calls=1,
            backend_observations=None,
            attempts=[{"seq": 0, "type": "connect", "chain": ["ConnectError"],
                       "elapsed_ms": 1.0, "phase": "post"}],
            boundaries={},
            observation_ok=True,
        )
    # RED confirmed: the malformed record never builds.


def test_t17_mutation_missing_field_reverted_green(tmp_path):
    """Revert: honest five-field attempt builds and lands (GREEN)."""
    diag = sa_evidence.build_transport_diagnosis(
        dialog_request_id="req-t17-clean",
        evidence_request_id="req-t17-ev-clean",
        transport_calls=1,
        backend_observations=None,
        attempts=[
            sa_evidence.build_transport_attempt(
                seq=0,
                exc_type="connect",
                chain=["ConnectError"],
                errno=None,
                elapsed_ms=2.5,
                phase="post",
            )
        ],
        boundaries={"submit": True},
        observation_ok=True,
    )
    landed = sa_evidence.record_transport_diagnosis(
        directory=tmp_path / "transport",
        dialog_request_id="req-t17-clean",
        diagnosis=diag,
    )
    stored = json.loads(landed.read_text(encoding="utf-8"))
    assert set(stored["attempts"][0].keys()) == {
        "seq", "type", "chain", "errno", "elapsed_ms", "phase",
    }


def test_t17_mutation_wrong_pairing_red(tmp_path, monkeypatch):
    """Corrupt mutant: transport file names dialog X but points at evidence
    id Y (no such evidence file) -> association check trips (RED)."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "raise"
    _BEHAVIOR["exc"] = httpx.ConnectError("t17 mutant mispair")
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        try:
            factory(_snapshot("t17-offline-owner"))(
                (("user", MESSAGE),), agent_id=AGENT, request_id="req-t17-dialog-mispair"
            )
            raised = None
        except sa_bridge.LiveRefused as exc:
            raised = exc
        assert raised is not None
        assert len(calls) == 1
        obs = sa_evidence.find_transport_obs(raised)
        assert obs is not None
        # Mutant: point the landed record at a foreign evidence id.
        carried = dict(obs)
        carried["evidence_request_id"] = "req-t17-foreign-evidence"
        landed = sa_evidence.record_transport_diagnosis(
            directory=tmp_path / "transport",
            dialog_request_id="req-t17-dialog-mispair",
            diagnosis=carried,
        )
        stored = json.loads(landed.read_text(encoding="utf-8"))
        ev_names = sorted(
            p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
        )
        if ev_names == ["%s.request.json" % stored["evidence_request_id"]]:
            pytest.fail("MUTATION DUD -- STOP, report: foreign evidence id matched a file")
        assert ev_names != ["%s.request.json" % stored["evidence_request_id"]]  # RED
    finally:
        _BEHAVIOR["kind"] = "unset"
        _BEHAVIOR["exc"] = None


def test_t17_mutation_wrong_pairing_reverted_green(tmp_path, monkeypatch):
    """Revert: landed evidence id matches the real evidence file (GREEN)."""
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "raise"
    _BEHAVIOR["exc"] = httpx.ReadTimeout("t17 green mispair revert")
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        try:
            factory(_snapshot("t17-offline-owner"))(
                (("user", MESSAGE),), agent_id=AGENT, request_id="req-t17-dialog-pair"
            )
            raised = None
        except sa_bridge.LiveRefused as exc:
            raised = exc
        assert len(calls) == 1
        obs = sa_evidence.find_transport_obs(raised)
        landed = sa_evidence.record_transport_diagnosis(
            directory=tmp_path / "transport",
            dialog_request_id="req-t17-dialog-pair",
            diagnosis=dict(obs),
        )
        stored = json.loads(landed.read_text(encoding="utf-8"))
        ev_names = sorted(
            p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
        )
        assert ev_names == ["%s.request.json" % stored["evidence_request_id"]]
        assert stored["dialog_request_id"] == "req-t17-dialog-pair"
    finally:
        _BEHAVIOR["kind"] = "unset"
        _BEHAVIOR["exc"] = None


def test_t17_mutation_gate_relaxed_red(tmp_path, monkeypatch):
    """Relaxed mutant: instance door forced open while the shared sandbox
    gate stays closed -> still 403 with zero transmission (RED for anyone
    expecting the relaxed door to admit)."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)  # relaxed mutant
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "closed")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    if response.status_code != 403 or sender.calls != []:
        pytest.fail("MUTATION DUD -- STOP, report: relaxed instance door admitted")
    assert response.status_code == 403 and sender.calls == []  # RED confirmed


def test_t17_mutation_gate_relaxed_reverted_green(tmp_path, monkeypatch):
    """Revert: both doors legitimately open -> exactly one success (GREEN)."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "open")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    assert response.status_code == 200 and len(sender.calls) == 1


def test_t17_mutation_gate_forced_closed_red(tmp_path, monkeypatch):
    """Forced-closed mutant: on the success configuration the shared gate is
    forced closed at the last moment -> 403 with zero transmission (RED)."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "open")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    gate_path.write_text(json.dumps({"status": "closed"}), encoding="utf-8")  # forced
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    if response.status_code != 403 or sender.calls != []:
        pytest.fail("MUTATION DUD -- STOP, report: forced-closed gate admitted")
    assert response.status_code == 403 and sender.calls == []  # RED confirmed


def test_t17_mutation_gate_forced_closed_reverted_green(tmp_path, monkeypatch):
    """Revert: gate restored open with instance open -> success (GREEN)."""
    monkeypatch.setenv(THIN_KEY_REF, THIN_FAKE_KEY)
    monkeypatch.setattr(thin_mod, "_THIN_OPEN", True)
    registry_path, presets_path, gate_path = _thin_files(tmp_path, "open")
    sender = _CountingSender()
    forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_path),
        target_base="http://t17-thin.invalid",
        sender=sender,
    )
    app = thin_mod.build_thin_app(
        str(registry_path), str(presets_path),
        release_path=str(gate_path), _forwarder=forwarder,
    )
    response = _post_thin(app)
    assert response.status_code == 200 and len(sender.calls) == 1
    assert response.json()["choices"][0]["message"]["content"] == THIN_SUCCESS_TEXT


# -- T17-f: pins (template, old SHAs, AST) ------------------------------------

def test_t17_recovery_template_pins_hold():
    """Template bytes carry the pinned inner digest and request_id."""
    raw = TEMPLATE_PATH.read_bytes()
    obj = json.loads(raw.decode("utf-8-sig"))
    assert obj.get("request_id") == SUCCESS_DIALOG_ID
    assert obj.get("sha256") == PIN_TEMPLATE_INNER_SHA
    assert len(raw) > 0


def test_t17_recovery_old_evidence_shas_unchanged(tmp_path, monkeypatch):
    """Old evidence + dual sent.json are byte-identical before/after this
    ticket's formal-path traffic (sandbox-only writes elsewhere)."""
    before = _snapshot_old_shas()
    assert len(before) > 0
    calls = _install_fake(monkeypatch)
    _BEHAVIOR["kind"] = "raise"
    _BEHAVIOR["exc"] = httpx.ConnectError("t17 sha-watch probe")
    try:
        llm = SimpleNamespace(
            backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
        )
        sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_run_submit, evidence_sink=sink
        )
        try:
            factory(_snapshot("t17-offline-owner"))(
                (("user", MESSAGE),), agent_id=AGENT, request_id="req-t17-sha-watch"
            )
        except sa_bridge.LiveRefused:
            pass
        assert len(calls) == 1
    finally:
        _BEHAVIOR["kind"] = "unset"
        _BEHAVIOR["exc"] = None
    after = _snapshot_old_shas()
    assert after == before


def _proxy_source() -> str:
    return (_REPO_ROOT / "src" / "llm" / "proxy.py").read_text(encoding="utf-8")


def _pin_evidence_zero_retry(source_text) -> bool:
    tree = ast.parse(source_text)
    found = []

    class _Visitor(ast.NodeVisitor):
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
                        found.append(stmt.value.value)
            self.generic_visit(node)

    _Visitor().visit(tree)
    return found == [0]


def test_t17_recovery_ast_pins_hold():
    """Frozen transport shape: evidence mode stays zero-retry; the wiring
    keeps the three pass-through boundaries (complete/submit/result)."""
    assert _pin_evidence_zero_retry(_proxy_source()) is True
    wiring_source = (_REPO_ROOT / "src" / "soul_access" / "wiring.py").read_text(
        encoding="utf-8"
    )
    assert wiring_source.count("propagate_transport_obs(") == 3
    for boundary in ("complete", "submit", "result"):
        assert 'boundary="%s"' % boundary in wiring_source
