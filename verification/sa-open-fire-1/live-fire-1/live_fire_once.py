"""SA-OPEN-FIRE-1 live-fire-1: Mavis-controlled single live fire (exactly 1 attempt).

Decision-complete scope (Owner + Wolong CONDITIONAL, Mavis controlled sender):
this file is NEW under ``verification/sa-open-fire-1/live-fire-1/`` (evidence
scope only). ``src/**``, ``configs/**``, ``scripts/**``, ``data/**``,
``release/sa.json`` and every Frozen contract are untouched by this file;
it only READS them (pins, registry, release gate) and READS the credential
from the process environment (``load_dotenv()`` self-loaded; the value is
never printed, never written, never returned).

Formal sender, real program path (no raw-HTTP bypass around the thin end):
  1. ``src.soul_access.wiring.build_dialog_factory(..., evidence_sink=sink)``
     per-request explicit pass-through (fixed ``max_retries=0``);
  2. ``src.llm.proxy.OpenAIBackend.complete(..., evidence_sink=sink)``
     evidence mode (fixed zero retry; pre-send payload+hash, post-return
     verbatim raw + native usage, correlated by one request_id).

Fixed ticket input (unchanged characters): rem/primary, ``你好。``,
model ``deepseek-v4.1-flash:1017``, temperature 0.5, max_tokens 8,
dialog_request_id ``req-sa-open-fire-1-0001``. Concurrency is 1 by
construction: one process, one dialog call, one transport POST
(``range(max_retries+1) == range(1)``), synchronous pump, no threads,
no background inference, no auto-probe, no retry, no fallback
(unknown alias refuses 500, never primary), timeout counts as consumed.

Order: marker-absent check -> send-time port probe (:18765 must be empty;
this ticket never serves persistently because no stop exists that is not
a kill) -> release gate must read open -> atomic marker (O_EXCL) ->
save replayable provider payload (via sink pre-send record) -> ONE send ->
evidence pair verify (pair_ok else unqualified, no resend) -> summary.
Any exception after the marker is CONSUMED: no resend, ever.

Exit codes: 0 sent | 3 EVIDENCE_GAP (unqualified) | 4 ALREADY_SENT |
5 BLOCKED_PORT (send-time probe) | 6 GATE_CLOSED | 7 TRANSPORT_FAIL |
8 KEY_MISSING. Stdout prints counts + hashes only (never raw, never key).
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = LIVE_DIR / "evidence"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # process self-loads; never reports values

    load_dotenv()
except Exception:
    pass

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
DIALOG_REQUEST_ID = "req-sa-open-fire-1-0001"
KEY_REF = "OLLAMA_API_KEY"

THIN_HOST = "127.0.0.1"
THIN_PORT = 18765

RELEASE_PATH = REPO_ROOT / "release" / "sa.json"
PRESETS_PATH = REPO_ROOT / "configs" / "sa" / "presets.json"


def _fail(code: int, msg: str) -> int:
    print("live-fire-1 refused/failed: %s" % msg, file=sys.stderr)
    return code


def _snapshot() -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=DIALOG_REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-fire-1",
            idempotency_key="key-fire-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id="bryan",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fire-1",
        ),
        backend_alias=BACKEND_ALIAS,
        model="deepseek-v4.1-flash:1017",
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


def _run(coro):
    """Synchronous coroutine pump (no loop creation, no threads)."""
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
                raise AssertionError("live-fire-1: fake suspend on %r" % (yielded,))
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


def _port_empty_now() -> bool:
    try:
        probe = socket.socket()
    except OSError:
        return False
    try:
        probe.bind((THIN_HOST, THIN_PORT))
        return True
    except OSError:
        return False
    finally:
        try:
            probe.close()
        except Exception:
            pass


def _release_is_open() -> bool:
    try:
        return json.loads(RELEASE_PATH.read_text(encoding="utf-8")).get("status") == "open"
    except (OSError, ValueError):
        return False


def main() -> int:
    marker = LIVE_DIR / "sent.json"
    if marker.exists():
        return _fail(4, "marker exists: refusing to resend %s" % DIALOG_REQUEST_ID)
    if not _port_empty_now():
        return _fail(5, "%s:%d not empty at send time" % (THIN_HOST, THIN_PORT))
    if not _release_is_open():
        return _fail(6, "shared gate is not open: refusing to send")

    presets = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    primaries = [e for e in presets["presets"] if e.get("name") == "primary"]
    if len(primaries) != 1:
        return _fail(6, "primary preset not unique")
    primary = primaries[0]
    if primary.get("model") != "deepseek-v4.1-flash:1017":
        return _fail(6, "primary model drift")
    if primary.get("key_ref") != KEY_REF:
        return _fail(6, "primary key_ref drift")
    if primary.get("params", {}).get("temperature") != 0.5:
        return _fail(6, "primary temperature drift")
    if primary.get("params", {}).get("max_tokens") != 8:
        return _fail(6, "primary max_tokens drift")
    base_url = primary.get("params", {}).get("base_url")
    if not isinstance(base_url, str) or not base_url or ".invalid" in base_url:
        return _fail(6, "primary base_url unfit for live fire")

    api_key = os.environ.get(KEY_REF)
    if not isinstance(api_key, str) or not api_key:
        return _fail(8, "credential %s not present in process environment" % KEY_REF)

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(marker), flags, 0o600)
    except FileExistsError:
        return _fail(4, "marker raced: refusing to resend %s" % DIALOG_REQUEST_ID)
    os.write(fd, json.dumps({"request_id": DIALOG_REQUEST_ID, "attempt": 1}).encode("utf-8"))
    os.close(fd)

    sink = sa_evidence.EvidenceSink(EVIDENCE_DIR)
    backend = OpenAIBackend(api_key=api_key, base_url=base_url)
    llm = SimpleNamespace(backend=backend)
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_submit, evidence_sink=sink
    )
    dialog = factory(_snapshot())
    try:
        text = dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)
    except Exception as exc:  # noqa: BLE001
        return _fail(7, "transport failed (%s): consumed, no retry" % type(exc).__name__)
    if not isinstance(text, str) or not text:
        return _fail(3, "dialog mistyped: unqualified, no resend")

    names = sorted(
        p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
    )
    if len(names) != 2:
        return _fail(3, "evidence pair missing: unqualified, no resend")
    evidence_request_id = names[0].split(".request.json")[0]
    try:
        report = sink.verify_pair(evidence_request_id)
    except Exception as exc:  # noqa: BLE001
        return _fail(3, "evidence pair unreadable (%s)" % type(exc).__name__)
    if not report.get("pair_ok"):
        return _fail(3, "evidence pair not ok: unqualified, no resend")

    request = json.loads((sink.directory / names[0]).read_text(encoding="utf-8"))
    response = json.loads((sink.directory / names[1]).read_text(encoding="utf-8"))
    for name in names:
        content = (sink.directory / name).read_text(encoding="utf-8")
        if KEY_REF not in content:
            return _fail(3, "key_ref name missing from evidence")
        if "Bearer" in content or "api_key" in content:
            return _fail(3, "credential-shaped text in evidence: stop")
    summary = {
        "ticket": "SA-OPEN-FIRE-1-live-fire-1",
        "route": "%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS),
        "dialog_request_id": DIALOG_REQUEST_ID,
        "evidence_request_id": evidence_request_id,
        "message": MESSAGE,
        "model": primary["model"],
        "params": {"temperature": 0.5, "max_tokens": 8},
        "key_ref": KEY_REF,
        "transport_calls": 1,
        "pair_ok": True,
        "payload_sha256": request["payload_sha256"],
        "raw_sha256": response["raw_sha256"],
        "usage": dict(response["usage"]),
        "reply_chars": len(text),
        "real_billing": 1,
        "secret_present_in_evidence": False,
    }
    (LIVE_DIR / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "live-fire-1 SENT once: transport_calls=1 pair_ok=True "
        "payload_sha256=%s raw_sha256=%s usage=%s reply_chars=%d"
        % (
            summary["payload_sha256"][:16],
            summary["raw_sha256"][:16],
            json.dumps(summary["usage"], sort_keys=True),
            len(text),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
