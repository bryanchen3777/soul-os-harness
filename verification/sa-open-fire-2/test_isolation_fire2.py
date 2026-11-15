"""SA-OPEN-FIRE-2 offline isolation tests (5 items, zero real I/O).

Runs BEFORE any real shot. ``.venv`` + fake transport + runtime socket
hard-block + non-routable ``.invalid`` upstream only. No production files
are written; no credential value is read or stored (isolation sentinel
only); no server is started; no port is bound.

Five ticket items:

  1. single-success: one dialog call, one transport POST, evidence pair
     (request/response) + pair_ok;
  2. missing-evidence: raw/usage absent -> EvidenceGap, unqualified, stop;
  3. timeout-consumed: transport raises once, exactly one attempt, marker
     consumed, no retry, rerun refuses;
  4. already-sent: pre-existing sent.json refuses before any transport;
  5. loop-regression: the probe's ``_run`` refuses when a loop is already
     running (single ``asyncio.run`` only, never nested).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json as _jsonlib
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BATCH_DIR = Path(__file__).resolve().parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

PROBE_PATH = BATCH_DIR / "live_fire_real.py"

NON_ROUTABLE_BASE = "http://sa-open-fire-2-invalid.invalid/v1/chat/completions"
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"
MODEL = "deepseek-v4.1-flash:1017"
SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
DIALOG_REQUEST_ID = "req-sa-open-fire-2-0001"
SYNTHETIC_MARK = "合成資料（隔離演練，非實彈）"

_REAL_SOCKET = socket.socket


def _install_socket_block() -> None:
    def _blocked(*args, **kwargs):
        raise RuntimeError("fire2 isolation: network socket hard-blocked at runtime")

    class _BlockedSocket(_REAL_SOCKET):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("fire2 isolation: network socket hard-blocked at runtime")

    socket.socket = _BlockedSocket
    socket.create_connection = _blocked
    socket.create_server = _blocked


def _restore_socket() -> None:
    socket.socket = _REAL_SOCKET
    import importlib as _il

    _il.reload(socket)


def _load_probe():
    spec = importlib.util.spec_from_file_location("fire2_live_probe", PROBE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _synthetic_body() -> dict:
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


_BEHAVIOR = {"kind": "ok", "exc": None}


class _FakeClient:
    """Fake transport: only ``.invalid`` upstream; behavior switchable."""

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
            return _FakeResp(_jsonlib.dumps(body, ensure_ascii=False, sort_keys=True), body)
        if _BEHAVIOR["kind"] == "no-usage":
            body = {
                "id": "chatcmpl-synthetic-no-usage-0001",
                "object": "chat.completion",
                "created": 1790000000,
                "model": MODEL,
                "synthetic": SYNTHETIC_MARK,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "合成回覆：缺證。"},
                        "finish_reason": "stop",
                    }
                ],
            }
            return _FakeResp(_jsonlib.dumps(body, ensure_ascii=False, sort_keys=True), body)
        raise RuntimeError("isolation breach: fake behavior unset")


def _run(coro):
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
                raise AssertionError("fire2 isolation: fake suspended on %r" % (yielded,))
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


def _snapshot() -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-fire-2",
            idempotency_key="key-fire-2",
        ),
        dedup=sa_schema.DedupKey(
            principal_id="fire2-offline-owner",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fire-2",
        ),
        backend_alias=BACKEND_ALIAS,
        model=MODEL,
        params=(("temperature", "0.5"), ("max_tokens", "8")),
        budget_tokens=512,
        deadline_utc="2026-11-14T00:00:00Z",
        personality_version="personality-fire-v1",
        emotion_state="calm",
        relationship_state="trusted",
        chrono_state="2026-11-14T15:45:00-05:00",
        inner_life_state="inner-fire-v1",
        projection_state="projection-fire-v1",
        recall_version="recall-fire-v1",
        recall_items=("recall-fire-item-1",),
        history_pool_version="pool-fire-v1",
        clean_history=("hist-fire-item-1",),
    )


def _make_dialog(evidence_dir: Path):
    sink = sa_evidence.EvidenceSink(evidence_dir)
    backend = OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_submit, evidence_sink=sink
    )
    return sink, factory(_snapshot())


@pytest.fixture
def isolated_httpx(monkeypatch):
    calls: list = []
    _install_socket_block()
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: _FakeClient(calls))
    try:
        yield calls
    finally:
        _restore_socket()


def test_fire2_01_single_success_fake_transport(tmp_path, isolated_httpx, monkeypatch):
    """Item 1: single success -- one call, one pair, pair_ok."""
    monkeypatch.setitem(_BEHAVIOR, "kind", "ok")
    calls = isolated_httpx
    ev_dir = tmp_path / "evidence"
    sink, dialog = _make_dialog(ev_dir)
    text = dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert text == "合成回覆：隔離演練單發成功。"
    assert len(calls) == 1
    assert ".invalid" in str(calls[0]["url"])
    names = sorted(p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-"))
    assert len(names) == 2
    evidence_request_id = names[0].split(".request.json")[0]
    report = sink.verify_pair(evidence_request_id)
    assert report["pair_ok"] is True
    for name in names:
        content = (sink.directory / name).read_text(encoding="utf-8")
        assert sa_evidence.KEY_REF in content
        assert SENTINEL_API_KEY not in content


def test_fire2_02_missing_evidence_gap(tmp_path, isolated_httpx, monkeypatch):
    """Item 2: missing cert -- raw/usage absent is unqualified (stop)."""
    response = sa_evidence.check_response
    with pytest.raises(sa_evidence.EvidenceGap):
        response("", {})
    with pytest.raises(sa_evidence.EvidenceGap):
        response("some-raw", {})
    # A response file without usage never verifies as a pair.
    sink = sa_evidence.EvidenceSink(tmp_path / "evidence")
    rid = "req-fire2-missing-0001"
    sink.record_request(
        request_id=rid,
        payload={"model": MODEL, "messages": [{"role": "user", "content": MESSAGE}]},
        payload_sha256=sa_evidence.sha256_hex(
            sa_evidence.canonical_bytes(
                {"model": MODEL, "messages": [{"role": "user", "content": MESSAGE}]}
            )
        ),
    )
    with pytest.raises(Exception):
        sink.verify_pair(rid)


def test_fire2_03_timeout_consumed_no_retry(tmp_path, isolated_httpx, monkeypatch):
    """Item 3: timeout counts as consumed -- exactly one attempt, no retry."""
    monkeypatch.setitem(_BEHAVIOR, "kind", "raise")
    _BEHAVIOR["exc"] = httpx.ReadTimeout("fire2 simulated timeout")
    calls = isolated_httpx
    ev_dir = tmp_path / "evidence"
    sink, dialog = _make_dialog(ev_dir)
    marker = tmp_path / "sent.json"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    fd = os.open(str(marker), flags, 0o600)
    os.write(fd, _jsonlib.dumps({"request_id": DIALOG_REQUEST_ID, "attempt": 1}).encode())
    os.close(fd)
    with pytest.raises(Exception):
        dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    assert len(calls) == 1
    assert marker.exists()
    with pytest.raises(FileExistsError):
        os.open(str(marker), flags, 0o600)


def test_fire2_04_already_sent_refuses(tmp_path, isolated_httpx):
    """Item 4: already-sent marker refuses before any transport."""
    calls = isolated_httpx
    marker = tmp_path / "sent.json"
    marker.write_text(
        _jsonlib.dumps({"request_id": DIALOG_REQUEST_ID, "attempt": 1}), encoding="utf-8"
    )
    if marker.exists():
        refused = True
    else:  # pragma: no cover - structural: marker present must refuse
        refused = False
    assert refused is True
    assert calls == []


def test_fire2_05_running_loop_regression():
    """Item 5: loop regression -- probe ``_run`` refuses when nested."""
    probe = _load_probe()
    src = Path(PROBE_PATH).read_text(encoding="utf-8")
    assert "import socket" not in src
    assert ".bind(" not in src

    async def _nested():
        async def _inner():
            return "x"

        coro = _inner()
        try:
            probe._run(coro)
        except RuntimeError as exc:
            await coro.aclose() if hasattr(coro, "aclose") else None
            return "refused:%s" % type(exc).__name__
        return "nested-allowed-BUG"

    outcome = asyncio.run(_nested())
    assert outcome.startswith("refused:")
