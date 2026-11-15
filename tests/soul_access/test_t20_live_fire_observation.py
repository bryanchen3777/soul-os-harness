"""SA-TRANSPORT-OBS-2C T20 live-fire-4 observation-wiring tests (sandbox only).

T20-ISOLATION-1 (2026-11-15): the formal B4 live fire (commit 17c11d6, Owner
one-shot authorization consumed) permanently created the formal marker
``verification/sa-open-fire-1/live-fire-4/sent.json``. The pre-isolation T20
asserted that formal marker ABSENT, so every full-repo run went red. This
file no longer depends on the formal marker in either direction:

  - Formal ``live-fire-4/`` artifacts (``sent.json``, ``result.json``,
    ``request.replayable.json``, ``evidence/``, ``transport/``,
    ``failure/``, ``offline/case-matrix.json``) are NEVER read nor written
    here. Marker content shapes used in sandboxes are hand-written fixtures
    (same ``request_id`` shape), never copies of the formal file.
  - The ONLY formal ``live-fire-4/`` reads are the B4/B3 ``.py`` sources
    (module import + AST wiring pins): product-code reads, not artifacts.
  - Every marker / matrix output lands under per-test ``tmp_path``.
  - Every ``_drive_main`` run records all write-ish filesystem calls
    (``os.open`` create/write, ``os.replace``, ``Path.write_text``,
    ``Path.write_bytes``, ``Path.mkdir``) and the unsent/sent tests assert
    all recorded writes resolve inside the test sandbox.

Decision-complete scope (SA-TRANSPORT-OBS-2C, mastermind Wolong approved):
B4 offline wiring + verification. New files are ONLY
``verification/sa-open-fire-1/live-fire-4/live_fire_b4.py``,
``verification/sa-open-fire-1/live-fire-4/negative_check_b4.py``,
``live-fire-4/offline/`` artifacts, and this file
(``tests/soul_access/test_t20_live_fire_observation.py``).
Read-only: ``src/llm/proxy.py``, ``src/soul_access/evidence.py`` (v1
observation API), ``src/soul_access/wiring.py``, ``src/soul_access/
failure_observation.py``, and the B1/B2/B3 originals. Old batches
(B1/B2/B3) are not re-litigated: ``INCONCLUSIVE`` stands.

All test names carry the t20 marker: ``pytest -q tests/soul_access -k t20``
must collect > 0. The SA-0 conftest in this directory hard-blocks real
sockets, so every transport here is a fake ``httpx.AsyncClient`` injected via
``monkeypatch`` (no real request, no server, no port ever bound). ``main``
is only ever driven against a self-built sandbox (``LIVE_DIR`` patched to
per-test ``tmp_path``); the B4 CLI is never run bare. ``dotenv`` loading is
stubbed BEFORE the B4 import (the process never reads a real ``.env``);
only key *existence* is checked, the synthetic value is never reported or
written anywhere.

Coverage (ticket acceptance mapping):
  a. B4 loads with dotenv disabled; ``main`` present; module-level
     ``LIVE_DIR`` default untouched (per-test sandbox patching only);
     formal artifacts never referenced.
  b. Real ``main`` wiring: success (evidence exactly 2 + pair_ok, no failure
     file, summary marks OFFLINE / real_billing 0), ConnectError, four
     timeouts, TimeoutException (exit 7, dual landing, dialog exactly once,
     original exception + cause kept, two observation contents reconciled).
  c. No-snapshot (absent, no fabricated transport), pollution (rejected, no
     transport), observation-landing failure (fixed token only, original
     unmasked), missing-file / extra-file (exit 3), sent-state marker-exists
     (exit 4, sandbox marker byte-identical, zero consumption, send entry
     hard-blocked), negative dual doors (both 403, zero upstream).
  d. AST pins (wrapper wiring, independent dirs, two-file criterion,
     original control flow incl. ``_land_transport_obs`` identical to B3).
  e. Bidirectional sandbox mutations (unwrapped / double-call /
     failure-mixed-into-evidence: RED then GREEN, DUD stops the run).
  f. Socket hard block at runtime; zz case-matrix artifact to the
     per-test ``tmp_path`` sandbox (secret-free, formal matrix untouched).
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import os
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

from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import failure_observation as fa_obs  # noqa: E402
from src.soul_access import wiring  # noqa: E402

# -- B4 original import with dotenv disabled (read-only) -----------------------
# The B4 module self-loads ``load_dotenv`` at import. A stub is installed in
# ``sys.modules`` BEFORE exec so no real ``.env`` file is ever read here.

LIVE_B4_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-4" / "live_fire_b4.py"
)
LIVE_B3_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-3" / "live_fire_b3.py"
)
NEG_B4_PATH = (
    _REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-4" / "negative_check_b4.py"
)

_DOTENV_CALLS: list = []


class _NoopDotenv(ModuleType):
    def load_dotenv(self, *args, **kwargs):  # noqa: N802
        _DOTENV_CALLS.append((args, kwargs))
        return False


_SAVED_DOTENV = sys.modules.get("dotenv")
_T20_STUB = _NoopDotenv("dotenv")
sys.modules["dotenv"] = _T20_STUB


def _load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


b4 = _load_module("live_fire_b4_t20", LIVE_B4_PATH)
_B4_DOTENV_CALLS = list(_DOTENV_CALLS)  # snapshot: later modules must not pollute it
b3 = _load_module("live_fire_b3_t20pin", LIVE_B3_PATH)
# Restore whatever dotenv was there before (or drop the stub) so this
# module's import-time stub never leaks into other tests sharing the session.
if _SAVED_DOTENV is None:
    del sys.modules["dotenv"]
else:
    sys.modules["dotenv"] = _SAVED_DOTENV

SENTINEL_KEY_REF = "OLLAMA_API_KEY"
FAKE_KEY_VALUE = "t20-fake-key-never-written"
MODEL = "do-not-deploy-model-t20"
AGENT = "agent_rem"
RID = "req-sa-open-fire-1-0001"

# Case-matrix rows collected across this module's tests (secret-free).
MATRIX_ROWS: list = []


def _matrix(case, row):
    entry = {"case": case}
    entry.update(row)
    MATRIX_ROWS.append(entry)
    return row


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


def _ok_body(text="t20-reply"):
    raw = json.dumps(
        {
            "id": "t20-synthetic",
            "object": "chat.completion",
            "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
        }
    )
    return raw, json.loads(raw)


def _ok_behavior(call):
    raw, body = _ok_body()
    return _FakeResp(raw, body)


def _raise_behavior(exc):
    def _behavior(call):
        raise exc

    return _behavior


def _files(directory):
    if not directory.exists():
        return []
    return sorted(
        p.name for p in directory.iterdir() if not p.name.startswith(".tmp-")
    )


def _install_write_recorder(monkeypatch, ledger):
    """Record every write-ish filesystem call; behavior delegates unchanged.

    Installed at test-call time (inside ``_drive_main``), i.e. AFTER the
    SA-0 conftest autouse fixture, so the captured originals already carry
    the SA-0 ``data/**`` hard blocks. Covers every write API the B4 flow
    and the evidence/transport/failure landers use: ``os.open``
    (create/write), ``os.replace`` (atomic landing), ``Path.write_text``,
    ``Path.write_bytes``, ``Path.mkdir``. Reads are never recorded.
    """
    real_os_open = os.open
    real_os_replace = os.replace
    real_write_text = Path.write_text
    real_write_bytes = Path.write_bytes
    real_mkdir = Path.mkdir

    def _record(path_like):
        try:
            ledger.append(str(Path(path_like).resolve(strict=False)))
        except Exception:
            ledger.append(str(path_like))

    def _guarded_os_open(path, flags, *args, **kwargs):
        if flags & (os.O_CREAT | os.O_WRONLY | os.O_RDWR | os.O_APPEND):
            _record(path)
        return real_os_open(path, flags, *args, **kwargs)

    def _guarded_os_replace(src, dst, *args, **kwargs):
        _record(dst)
        return real_os_replace(src, dst, *args, **kwargs)

    def _guarded_write_text(self, *args, **kwargs):
        _record(self)
        return real_write_text(self, *args, **kwargs)

    def _guarded_write_bytes(self, *args, **kwargs):
        _record(self)
        return real_write_bytes(self, *args, **kwargs)

    def _guarded_mkdir(self, *args, **kwargs):
        _record(self)
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(os, "open", _guarded_os_open)
    monkeypatch.setattr(os, "replace", _guarded_os_replace)
    monkeypatch.setattr(Path, "write_text", _guarded_write_text)
    monkeypatch.setattr(Path, "write_bytes", _guarded_write_bytes)
    monkeypatch.setattr(Path, "mkdir", _guarded_mkdir)
    return ledger


def _assert_writes_confined(writes, sandbox):
    """T20-ISOLATION-1: every recorded write must resolve inside the sandbox."""
    root = str(sandbox.resolve(strict=False))
    outsiders = [
        w for w in writes if not (w == root or w.startswith(root + os.sep))
    ]
    assert outsiders == [], (
        "T20-ISOLATION-1: writes outside the test sandbox: %r" % (outsiders,)
    )


def _drive_main(
    monkeypatch,
    tmp_path,
    capsys,
    *,
    behavior=None,
    port_open=True,
    pre_marker=False,
    seed_evidence=(),
    fail_obs_landing=False,
    factory=None,
    observe_passthrough=None,
):
    """Drive the REAL ``b4.main`` against a self-built sandbox.

    ``observe_passthrough`` optionally replaces ``observe_dialog`` with a
    mutant (unwrapped / double-call). Returns a namespace with rc, captured
    output, sandbox paths, transport POST calls and observe calls.
    """
    sandbox = tmp_path / "live"
    sandbox.mkdir(parents=True, exist_ok=True)
    writes = _install_write_recorder(monkeypatch, [])
    monkeypatch.setenv(SENTINEL_KEY_REF, FAKE_KEY_VALUE)
    # NOTE: EVIDENCE_DIR is bound at B4 import time from the real LIVE_DIR,
    # so it must be redirected too -- patching LIVE_DIR alone would leak
    # evidence writes into the repo batch dir.
    monkeypatch.setattr(b4, "LIVE_DIR", sandbox)
    monkeypatch.setattr(b4, "EVIDENCE_DIR", sandbox / "evidence")
    monkeypatch.setattr(b4, "_port_empty_now", lambda: port_open)
    calls = _install_fake(monkeypatch, behavior=behavior or _ok_behavior)
    if pre_marker:
        # Hand-written formal request_id shape; never a copy of any formal file.
        (sandbox / "sent.json").write_text(
            json.dumps({"request_id": RID, "attempt": 1}), encoding="utf-8"
        )
    if seed_evidence:
        ev_dir = sandbox / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        for name, content in seed_evidence:
            (ev_dir / name).write_text(content, encoding="utf-8")
    if fail_obs_landing:
        def _boom_record(**kw):
            raise sa_evidence.EvidenceStoreError("t20 mutant landing off")

        monkeypatch.setattr(fa_obs, "record_observation", _boom_record)
    if factory is not None:
        monkeypatch.setattr(wiring, "build_dialog_factory", factory)
    observe_calls = []
    if observe_passthrough is not None:
        monkeypatch.setattr(fa_obs, "observe_dialog", observe_passthrough)
    else:
        orig = fa_obs.observe_dialog

        def _counting(dialog, messages, *, directory, agent_id=None, request_id=None):
            observe_calls.append(
                {
                    "messages": [list(m) for m in messages],
                    "directory": str(directory),
                    "agent_id": agent_id,
                    "request_id": request_id,
                }
            )
            return orig(
                dialog,
                messages,
                directory=directory,
                agent_id=agent_id,
                request_id=request_id,
            )

        monkeypatch.setattr(fa_obs, "observe_dialog", _counting)
    rc = b4.main()
    out = capsys.readouterr()
    if observe_passthrough is not None:
        observe_calls.extend(getattr(observe_passthrough, "_t20_calls", []))
    return SimpleNamespace(
        rc=rc,
        out=out.out,
        err=out.err,
        sandbox=sandbox,
        calls=calls,
        observe_calls=observe_calls,
        writes=writes,
    )


def _raising_factory(exc):
    """Two-level fake: build_dialog_factory(kwargs) -> maker(snapshot) -> dialog."""
    def _factory(*args, **kwargs):
        def _maker(snapshot):
            def _dialog(messages, *, agent_id=None, request_id=None):
                raise exc

            return _dialog

        return _maker

    return _factory


def _boom_polluted():
    boom = RuntimeError("t20 polluted probe")
    sa_evidence.attach_transport_obs(
        boom,
        sa_evidence.build_transport_diagnosis(
            dialog_request_id=None,
            evidence_request_id="req-t20-ev-api_key-1",
            transport_calls=1,
            backend_observations=None,
            attempts=[
                sa_evidence.build_transport_attempt(
                    seq=0,
                    exc_type="connect",
                    chain=["ConnectError"],
                    errno=None,
                    elapsed_ms=3.25,
                    phase="post",
                )
            ],
            boundaries={},
            observation_ok=True,
        ),
    )
    return boom


# -- T20-a: load, key, formal-artifacts-untouched ------------------------------


def test_t20_b4_loads_without_dotenv_and_formal_artifacts_untouched():
    # T20-ISOLATION-1: no formal marker/matrix assertion in either direction.
    # The formal live fire (commit 17c11d6) owns the formal marker; this file
    # never reads nor writes formal artifacts. Only the B4/B3 *sources* are
    # read (import + AST pins below).
    assert LIVE_B4_PATH.exists() and NEG_B4_PATH.exists() and LIVE_B3_PATH.exists()
    assert _B4_DOTENV_CALLS == [((), {})]  # stubbed loader did nothing
    assert sys.modules.get("dotenv") is not _T20_STUB  # stub never leaks
    assert callable(b4.main)  # present; tests only drive it against sandboxes
    assert "fake suspend" in inspect.getsource(b4._run)  # original pump pin
    # Module default still points at the formal batch dir object: per-test
    # sandboxing is done exclusively via monkeypatch, never by mutating it.
    assert b4.LIVE_DIR == LIVE_B4_PATH.parent
    assert b4.EVIDENCE_DIR == LIVE_B4_PATH.parent / "evidence"
    _matrix("b4-load", {"dotenv_stubbed": True, "formal_artifacts": "untouched"})


def test_t20_key_existence_only_never_reported(monkeypatch):
    monkeypatch.setenv(SENTINEL_KEY_REF, FAKE_KEY_VALUE)
    import os as _os

    assert isinstance(_os.environ.get(SENTINEL_KEY_REF), str)
    assert _os.environ.get(SENTINEL_KEY_REF)  # existence only; value never asserted on
    _matrix("key-existence", {"key_ref": SENTINEL_KEY_REF, "value_reported": False})


# -- T20-b: real main wiring -- success ---------------------------------------


def test_t20_success_offline_pair_ok_no_failure(monkeypatch, tmp_path, capsys):
    run = _drive_main(monkeypatch, tmp_path, capsys)
    assert run.rc == 0
    assert len(run.calls) == 1  # single consumption, zero retry
    ev_dir = run.sandbox / "evidence"
    names = _files(ev_dir)
    assert len(names) == 2  # exactly two evidence files
    rid = names[0].split(".request.json")[0]
    report = sa_evidence.EvidenceSink(ev_dir).verify_pair(rid)
    assert report.get("pair_ok") is True
    assert _files(run.sandbox / "failure") == []  # success adds no failure
    assert _files(run.sandbox / "transport") == []  # success adds no transport
    # T20-ISOLATION-1 unsent-state: the offline run creates its marker ONLY
    # in the sandbox, with the hand-written request_id shape, and performs
    # zero writes outside the sandbox (recorded write APIs, not prediction).
    marker_text = (run.sandbox / "sent.json").read_text(encoding="utf-8")
    assert marker_text == json.dumps({"request_id": RID, "attempt": 1})
    _assert_writes_confined(run.writes, run.sandbox)
    summary = json.loads((run.sandbox / "result.json").read_text(encoding="utf-8"))
    assert summary["ticket"] == "SA-OPEN-FIRE-1-live-fire-4"
    assert summary["offline"] is True  # fake run marks OFFLINE
    assert summary["real_billing"] == 0  # not billing evidence
    assert summary["pair_ok"] is True
    assert "OFFLINE" in run.out  # stdout carries the OFFLINE tag
    assert len(run.observe_calls) == 1  # dialog called exactly once
    call = run.observe_calls[0]
    assert call["messages"] == [["user", "你好。"]]  # original messages untouched
    assert call["agent_id"] == AGENT
    assert call["request_id"] == RID
    assert call["directory"] == str(run.sandbox / "failure")
    _matrix(
        "success",
        {
            "rc": 0,
            "offline": True,
            "real_billing": 0,
            "pair_ok": True,
            "observe_calls": 1,
        },
    )


# -- T20-b: real main wiring -- transport failures ----------------------------

_TRANSPORT_CASES = [
    ("ConnectError", httpx.ConnectError("t20 down"), "connect"),
    ("ConnectTimeout", httpx.ConnectTimeout("t20 cto"), "connect_timeout"),
    ("ReadTimeout", httpx.ReadTimeout("t20 rto"), "read_timeout"),
    ("WriteTimeout", httpx.WriteTimeout("t20 wto"), "write_timeout"),
    ("PoolTimeout", httpx.PoolTimeout("t20 pto"), "pool_timeout"),
    ("TimeoutException", httpx.TimeoutException("t20 generic"), "unknown"),
]


@pytest.mark.parametrize(
    "cls_name,exc,label",
    [pytest.param(c[0], c[1], c[2], id=c[0]) for c in _TRANSPORT_CASES],
)
def test_t20_transport_exit7_dual_landing(monkeypatch, tmp_path, capsys, cls_name, exc, label):
    run = _drive_main(monkeypatch, tmp_path, capsys, behavior=_raise_behavior(exc))
    assert run.rc == 7  # exit-7 handler, original error path
    assert len(run.calls) == 1  # single POST, no retry
    ev_names = _files(run.sandbox / "evidence")
    assert len(ev_names) == 1 and ev_names[0].endswith(".request.json")
    t_names = _files(run.sandbox / "transport")
    assert t_names == [RID + ".json"]  # transport lands under the dialog request id
    transport = json.loads(
        (run.sandbox / "transport" / t_names[0]).read_text(encoding="utf-8")
    )
    assert transport["kind"] == "sa-transport-diagnosis"
    assert transport["dialog_request_id"] == RID
    assert transport["transport_calls"] == 1
    assert transport["backend_observations"] is None
    assert len(transport["attempts"]) == 1
    assert transport["attempts"][0]["type"] == label
    assert transport["attempts"][0]["chain"][0] == cls_name
    f_names = _files(run.sandbox / "failure")
    assert f_names == [RID + ".json"]  # failure lands under the same request id
    failure = json.loads(
        (run.sandbox / "failure" / f_names[0]).read_text(encoding="utf-8")
    )
    assert failure["kind"] == "sa-exit7-observation"
    assert failure["dialog_request_id"] == RID
    assert failure["snapshot_status"] == "present"  # valid snapshot embedded
    assert failure["type"] == label  # same five-column content as transport
    assert failure["snapshot"]["evidence_request_id"] == transport["evidence_request_id"]
    assert failure["outer_exception"][0] == "LiveRefused"  # original chain kept
    assert run.out == ""  # landing success: silent, fixed token only on failure
    assert "transport failed" in run.err and "consumed, no retry" in run.err
    assert len(run.observe_calls) == 1  # dialog called exactly once
    _matrix(
        "transport-%s" % cls_name,
        {"rc": 7, "label": label, "snapshot": "present", "observe_calls": 1},
    )


# -- T20-c: no-snapshot / pollution / landing-failure / pair-shape / marker ---


def test_t20_no_snapshot_absent_no_transport(monkeypatch, tmp_path, capsys):
    bare = RuntimeError("t20 bare, no snapshot anywhere")
    run = _drive_main(
        monkeypatch, tmp_path, capsys, factory=_raising_factory(bare)
    )
    assert run.rc == 7  # exit 7 on the ORIGINAL error
    assert _files(run.sandbox / "transport") == []  # no fabricated transport
    f_names = _files(run.sandbox / "failure")
    assert f_names == [RID + ".json"]
    failure = json.loads(
        (run.sandbox / "failure" / f_names[0]).read_text(encoding="utf-8")
    )
    assert failure["snapshot_status"] == "absent"  # T19 absent, zero-fill
    assert failure["type"] == "unknown" and failure["attempts"] == []
    assert failure["transport_calls"] is None
    assert failure["outer_exception"][0] == "RuntimeError"
    _matrix("no-snapshot", {"rc": 7, "snapshot": "absent", "transport": "none"})


def test_t20_pollution_rejected_no_transport(monkeypatch, tmp_path, capsys):
    run = _drive_main(
        monkeypatch, tmp_path, capsys, factory=_raising_factory(_boom_polluted())
    )
    assert run.rc == 7  # exit 7 on the ORIGINAL error
    assert _files(run.sandbox / "transport") == []  # polluted: no transport
    f_names = _files(run.sandbox / "failure")
    assert f_names == [RID + ".json"]
    failure = json.loads(
        (run.sandbox / "failure" / f_names[0]).read_text(encoding="utf-8")
    )
    assert failure["snapshot_status"] == "rejected"  # T19 rejected, snapshot dropped
    assert failure["snapshot"] is None
    assert failure["type"] == "unknown" and failure["attempts"] == []
    assert "Bearer" not in json.dumps(failure) and "api_key" not in json.dumps(failure)
    _matrix("pollution", {"rc": 7, "snapshot": "rejected", "transport": "none"})


def test_t20_obs_landing_failure_token_only_original_unmasked(
    monkeypatch, tmp_path, capsys
):
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(httpx.ConnectError("t20 down")),
        fail_obs_landing=True,
    )
    assert run.rc == 7  # original error unmasked: still exit 7, not a write code
    assert run.out == fa_obs.WRITE_FAILED_TOKEN + "\n"  # fixed token only
    assert _files(run.sandbox / "failure") == []  # nothing landed
    # The ORIGINAL transport diagnosis still lands via the exit-7 handler.
    t_names = _files(run.sandbox / "transport")
    assert t_names == [RID + ".json"]
    assert "transport failed (LiveRefused)" in run.err
    _matrix("obs-landing-failure", {"rc": 7, "token_only": True, "transport": "landed"})


def test_t20_missing_file_exit3_no_failure(monkeypatch, tmp_path, capsys):
    # Simulate on-disk loss: run success, then replay with the response file
    # removed right after the dialog returns, before the pair check.
    run = _drive_main(monkeypatch, tmp_path, capsys)
    assert run.rc == 0  # baseline sanity: sandbox success first
    sandbox2 = tmp_path / "live2"
    sandbox2.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(b4, "LIVE_DIR", sandbox2)
    monkeypatch.setattr(b4, "EVIDENCE_DIR", sandbox2 / "evidence")
    calls2 = _install_fake(monkeypatch, behavior=_ok_behavior)
    real_observe = fa_obs.observe_dialog

    def _lossy(dialog, messages, *, directory, agent_id=None, request_id=None):
        text = real_observe(
            dialog, messages, directory=directory, agent_id=agent_id, request_id=request_id
        )
        ev_dir = sandbox2 / "evidence"
        for victim in ev_dir.glob("*.response.json"):
            victim.unlink()
        return text

    monkeypatch.setattr(fa_obs, "observe_dialog", _lossy)
    rc2 = b4.main()
    out2 = capsys.readouterr()
    assert rc2 == 3  # missing file: unqualified, no resend
    assert len(_files(sandbox2 / "evidence")) == 1
    assert _files(sandbox2 / "failure") == []  # dialog succeeded: no failure file
    assert len(calls2) == 1
    assert "evidence pair missing" in out2.err
    _matrix("missing-file", {"rc": 3, "failure": "none"})


def test_t20_extra_file_exit3(monkeypatch, tmp_path, capsys):
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        seed_evidence=(("junk.txt", '{"note": "t20 foreign file"}'),),
    )
    assert run.rc == 3  # foreign file breaks the two-file criterion
    assert len(run.calls) == 1  # the shot was still consumed once
    assert _files(run.sandbox / "failure") == []  # dialog succeeded: no failure file
    assert (run.sandbox / "evidence" / "junk.txt").read_text(
        encoding="utf-8"
    ) == '{"note": "t20 foreign file"}'
    _matrix("extra-file", {"rc": 3})


def test_t20_marker_exists_exit4_no_consumption(monkeypatch, tmp_path, capsys):
    # T20-ISOLATION-1 sent-state: the sandbox carries a hand-written marker
    # of the formal request_id shape (never a copy of the formal file). The
    # send entry is hard-blocked at execution time: any dialog build or
    # transport POST raises instead of sending. Refusal must therefore show
    # as rc==4 with zero consumption, never as a swallowed error.
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(RuntimeError("t20 must not send when marked")),
        factory=_raising_factory(RuntimeError("t20 must not build dialog when marked")),
        pre_marker=True,
    )
    seed = json.dumps({"request_id": RID, "attempt": 1})
    assert run.rc == 4  # ALREADY_SENT: refusing to resend
    assert len(run.calls) == 0  # nothing consumed
    assert (run.sandbox / "sent.json").read_text(encoding="utf-8") == seed
    assert not (run.sandbox / "evidence").exists()
    assert not (run.sandbox / "transport").exists()
    assert not (run.sandbox / "failure").exists()
    assert len(run.observe_calls) == 0  # dialog never reached
    _assert_writes_confined(run.writes, run.sandbox)
    _matrix("marker-exists", {"rc": 4, "calls": 0, "marker": "retained"})


def test_t20_negative_dual_doors(monkeypatch, tmp_path, capsys):
    neg = _load_module("live_fire_b4_negative_t20", NEG_B4_PATH)
    (tmp_path / "neg").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(neg, "LIVE_DIR", tmp_path / "neg")
    rc = neg.main()
    out = capsys.readouterr().out
    assert rc == 0
    report = json.loads(
        (tmp_path / "neg" / "negative-report.json").read_text(encoding="utf-8")
    )
    assert report["ticket"] == "SA-OPEN-FIRE-1-live-fire-4"
    assert report["door1_thin_instance"]["refused_403"] is True
    assert report["door1_thin_instance"]["upstream_calls"] == 0
    assert report["door2_shared_release"]["refused_403"] is True
    assert report["door2_shared_release"]["upstream_calls"] == 0
    assert report["production_release"] == {"status": "closed"}
    assert report["thin_open"] is False
    assert "negative PASS" in out
    _matrix("negative", {"rc": 0, "door1_403": True, "door2_403": True})


# -- T20-d: AST pins (wrapper wiring, independent dirs, pair, control flow) ---


def _b4_source() -> str:
    return LIVE_B4_PATH.read_text(encoding="utf-8")


def test_t20_ast_wrapper_wiring_observe_dialog_failure_dir():
    tree = ast.parse(_b4_source())
    calls = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node):  # noqa: N802
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "observe_dialog":
                calls.append(node)
            self.generic_visit(node)

    _Visitor().visit(tree)
    assert len(calls) == 1  # exactly one wrapper call site (the dialog site)
    call = calls[0]
    assert len(call.args) == 2  # (dialog, messages) positional, unchanged
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "dialog"
    kw = {k.arg: k.value for k in call.keywords}
    assert set(kw) == {"directory", "agent_id", "request_id"}
    directory = kw["directory"]  # directory=LIVE_DIR/"failure"
    assert (
        isinstance(directory, ast.BinOp)
        and isinstance(directory.op, ast.Div)
        and isinstance(directory.left, ast.Name)
        and directory.left.id == "LIVE_DIR"
        and isinstance(directory.right, ast.Constant)
        and directory.right.value == "failure"
    )
    assert isinstance(kw["agent_id"], ast.Name) and kw["agent_id"].id == "AGENT"
    assert isinstance(kw["request_id"], ast.Name) and kw["request_id"].id == "DIALOG_REQUEST_ID"
    source = _b4_source()
    assert "failure_observation" in source  # wrapper import present
    assert "\n        text = dialog(" not in source  # no unwrapped dialog call remains
    _matrix("ast-wrapper", {"pins": "hold"})


def test_t20_ast_independent_dirs_and_two_file_criterion():
    source = _b4_source()
    assert 'LIVE_DIR / "failure"' in source  # observation dir: independent
    assert 'LIVE_DIR / "transport"' in source  # transport dir: independent
    assert source.count("EVIDENCE_DIR") == 2  # def + EvidenceSink: never mixed
    assert "EvidenceSink(EVIDENCE_DIR)" in source
    assert "len(names) != 2" in source  # two-file criterion kept
    assert 'evidence pair missing: unqualified, no resend' in source
    pair_block = source.split("names = sorted")[1].split("evidence_request_id")[0]
    assert "failure" not in pair_block  # pair count reads the evidence dir only
    assert "transport" not in pair_block
    _matrix("ast-dirs-pair", {"pins": "hold"})


def test_t20_ast_original_control_flow_and_lander_identical_to_b3():
    source = _b4_source()
    for code in ("_fail(3", "_fail(4", "_fail(5", "_fail(6", "_fail(7", "_fail(8"):
        assert code in source  # exit codes unchanged
    assert "os.O_EXCL" in source  # single-consumption marker kept
    assert "zero retry" in source  # zero-retry semantics documented + kept
    assert "\n    return 0\n" in source
    assert "real_billing" in source and '"offline": True' in source  # OFFLINE summary
    assert "NEVER authorized to send" in source  # no authorized-send claim carried over
    assert "已授權發送" not in source
    # The exit-7 lander is byte-identical to B3 (ticket: _land_transport_obs 不動).
    assert inspect.getsource(b4._land_transport_obs) == inspect.getsource(
        b3._land_transport_obs
    )
    _matrix("ast-control-flow", {"pins": "hold"})


# -- T20-e: bidirectional sandbox mutations (RED then GREEN) -------------------


def test_t20_mutation_unwrapped_red(monkeypatch, tmp_path, capsys):
    """Mutant: wrapper removed -> transport failure lands NOTHING (RED)."""
    calls_made = []

    def _passthrough(dialog, messages, *, directory, agent_id=None, request_id=None):
        calls_made.append(1)
        return dialog(messages, agent_id=agent_id, request_id=request_id)

    _passthrough._t20_calls = []  # type: ignore[attr-defined]
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(httpx.ConnectError("t20 mutant probe")),
        observe_passthrough=_passthrough,
    )
    assert run.rc == 7
    if _files(run.sandbox / "failure"):
        pytest.fail("MUTATION DUD -- STOP, report: unwrapped path still observed")
    assert calls_made == [1]  # dialog still reached exactly once
    t_names = _files(run.sandbox / "transport")
    assert t_names == [RID + ".json"]  # exit-7 transport lander unaffected
    _matrix("mut-unwrapped-red", {"observer": "silent-as-designed"})


def test_t20_mutation_unwrapped_reverted_green(monkeypatch, tmp_path, capsys):
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(httpx.ConnectError("t20 mutant probe")),
    )
    assert run.rc == 7
    stored = json.loads(
        (run.sandbox / "failure" / (RID + ".json")).read_text(encoding="utf-8")
    )
    assert stored["snapshot_status"] == "present"  # GREEN: wrapper restored
    assert stored["type"] == "connect"
    _matrix("mut-unwrapped-green", {"observer": "lands-present"})


def test_t20_mutation_double_call_red(monkeypatch, tmp_path, capsys):
    """Mutant: dialog invoked twice -> two POSTs (RED: not single)."""
    def _double(dialog, messages, *, directory, agent_id=None, request_id=None):
        try:
            dialog(messages, agent_id=agent_id, request_id=request_id)
        except Exception:
            pass
        return dialog(messages, agent_id=agent_id, request_id=request_id)

    _double._t20_calls = []  # type: ignore[attr-defined]
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(httpx.ConnectError("t20 double probe")),
        observe_passthrough=_double,
    )
    assert run.rc == 7
    if len(run.calls) != 2:
        pytest.fail(
            "MUTATION DUD -- STOP, report: double-call mutant did not emit 2 POSTs"
        )
    assert len(run.calls) == 2  # RED confirmed: single consumption violated
    _matrix("mut-double-red", {"posts": 2})


def test_t20_mutation_double_call_reverted_green(monkeypatch, tmp_path, capsys):
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        behavior=_raise_behavior(httpx.ConnectError("t20 double probe")),
    )
    assert run.rc == 7
    assert len(run.calls) == 1  # GREEN: exactly one POST restored
    _matrix("mut-double-green", {"posts": 1})


def test_t20_mutation_failure_in_evidence_red(monkeypatch, tmp_path, capsys):
    """Mutant: failure-shaped file mixed into evidence/ -> pair broken (RED)."""
    foreign = json.dumps({"kind": "sa-exit7-observation", "version": "t20-mutant"})
    run = _drive_main(
        monkeypatch,
        tmp_path,
        capsys,
        seed_evidence=(("req-t20-mutant-failure.json", foreign),),
    )
    assert run.rc == 3  # RED confirmed: foreign file breaks the two-file criterion
    assert (run.sandbox / "evidence" / "req-t20-mutant-failure.json").read_text(
        encoding="utf-8"
    ) == foreign  # foreign file untouched, never parsed as pair
    if (run.sandbox / "result.json").exists():
        pytest.fail("MUTATION DUD -- STOP, report: broken pair still summarized")
    _matrix("mut-evidence-mix-red", {"rc": 3})


def test_t20_mutation_failure_in_evidence_reverted_green(monkeypatch, tmp_path, capsys):
    run = _drive_main(monkeypatch, tmp_path, capsys)
    assert run.rc == 0  # GREEN: clean evidence dir restores the pair
    assert len(_files(run.sandbox / "evidence")) == 2
    _matrix("mut-evidence-mix-green", {"rc": 0})


def test_t20_socket_hard_blocked_at_runtime():
    """Execution-time proof: real sockets cannot be opened in this suite."""
    with pytest.raises(RuntimeError):
        socket.socket()


# -- T20-zz: sandbox case-matrix artifact (secret-free) ------------------------


def test_t20_zz_matrix_artifact(tmp_path):
    """Persist the secret-free case matrix to the per-test tmp sandbox.

    T20-ISOLATION-1: the formal ``live-fire-4/offline/case-matrix.json`` is
    never written here (it stays byte-identical, hash-pinned by the worker).
    """
    out_dir = tmp_path / "t20-case-matrix"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticket": "SA-TRANSPORT-OBS-2C",
        "isolation": "T20-ISOLATION-1",
        "note": (
            "offline only; local POST != backend received/billed; "
            "B4 main wiring through observe_dialog into sandbox LIVE_DIR/failure; "
            "B1/B2/B3 originals untouched, INCONCLUSIVE stands; "
            "formal live-fire-4 marker/matrix never read nor written, "
            "offline markers in per-test sandboxes only"
        ),
        "formal_marker": "isolated-not-absent",
        "cases": MATRIX_ROWS,
    }
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)
    assert FAKE_KEY_VALUE not in text
    assert "Bearer" not in text
    path = out_dir / "case-matrix.json"
    path.write_text(text, encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["ticket"] == "SA-TRANSPORT-OBS-2C"
    assert len(MATRIX_ROWS) > 0  # matrix actually accumulated across this file
