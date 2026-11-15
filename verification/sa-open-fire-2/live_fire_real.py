"""SA-OPEN-FIRE-2 single real fire (B1, Owner-authorized one-shot, direct route).

Byte-level successor of ``verification/sa-open-fire-1/live-fire-4/live_fire_b4_real.py``
with the ticket-mandated changes only:

  - batch dir is ``verification/sa-open-fire-2`` (``sent.json`` O_EXCL
    anti-replay; absent = unconsumed);
  - dialog_request_id ``req-sa-open-fire-2-0001``; template
    ``verification/sa-open-fire-2/request.template.json`` inner sha
    ``d7b660e672a1f4d2d0a62e8459ea6ae1b53a7c090b1e19e69ebf753830cb1489``;
  - NO bind probe anywhere: ``:18765`` emptiness is proven read-only from the
    OS listener list (``netstat -ano`` parsed, never bound, never listened,
    never connected). This module imports no ``socket`` at all;
  - pre-send pins: formal registry sha256
    ``89c306660a7d57050f00a8fe45d4dab0f784a63dc23b0c03cf7d6db3ef759128``,
    formal presets sha256
    ``c37de506f9ab8f6d46d613f97ade6ae5642157b7b3a19ba7ed165d092b58cdc1``
    (read-only, drift refuses);
  - order: marker-absent check -> read-only port check -> dual-gate closed
    check -> preset pins + formal hash pins -> preflight file (key_ref NAME
    only, never the secret) + replayable template byte-copy -> atomic marker
    (O_EXCL) -> ONE send -> evidence pair verify (pair_ok else unqualified,
    no resend) -> summary.

Formal sender, real program path (no raw-HTTP bypass around the thin end):

  1. ``src.soul_access.wiring.build_dialog_factory(..., evidence_sink=sink)``
     per-request explicit pass-through (fixed ``max_retries=0``);
  2. ``src.llm.proxy.OpenAIBackend.complete(..., evidence_sink=sink)``
     evidence mode (fixed zero retry; pre-send payload+hash, post-return
     verbatim raw + native usage, correlated by one request_id).

The single dialog call goes through
``failure_observation.observe_dialog`` into ``LIVE_DIR/"failure"``
(original messages / agent_id / request_id untouched), same as B4-real.

One process, one dialog call, one transport POST
(``range(max_retries+1) == range(1)``), synchronous pump, no threads,
no background inference, no retry, no fallback
(unknown alias refuses 500, never primary), timeout counts as consumed.

Exit codes: 0 sent | 3 EVIDENCE_GAP (unqualified) | 4 ALREADY_SENT |
5 BLOCKED_PORT (read-only listener check) | 6 GATE_NOT_CLOSED / PIN_DRIFT |
7 TRANSPORT_FAIL | 8 KEY_MISSING. Stdout prints counts + hashes only
(never raw, never key).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = LIVE_DIR / "evidence"
TEMPLATE_PATH = REPO_ROOT / "verification" / "sa-open-fire-2" / "request.template.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from dotenv import load_dotenv  # process self-loads; never reports values

    load_dotenv()
except Exception:
    pass

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import failure_observation as fa_obs  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

import scripts.serve_sa_thin as thin_mod  # noqa: E402 (import-only, zero side effects)

SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
DIALOG_REQUEST_ID = "req-sa-open-fire-2-0001"
KEY_REF = "OLLAMA_API_KEY"

THIN_PORT = 18765

RELEASE_PATH = REPO_ROOT / "release" / "sa.json"
REGISTRY_PATH = REPO_ROOT / "configs" / "sa" / "registry.json"
PRESETS_PATH = REPO_ROOT / "configs" / "sa" / "presets.json"

REGISTRY_PIN = "89c306660a7d57050f00a8fe45d4dab0f784a63dc23b0c03cf7d6db3ef759128"
PRESETS_PIN = "c37de506f9ab8f6d46d613f97ade6ae5642157b7b3a19ba7ed165d092b58cdc1"
TEMPLATE_INNER_PIN = "d7b660e672a1f4d2d0a62e8459ea6ae1b53a7c090b1e19e69ebf753830cb1489"


def _fail(code: int, msg: str) -> int:
    print("live-fire-2 refused/failed: %s" % msg, file=sys.stderr)
    return code


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
            principal_id="bryan",
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fire-2",
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
    """Drive one coroutine on a fresh real event loop (same thread).

    Sync entry: exactly one ``asyncio.run`` per call when no loop is
    running (loop created, run to completion, closed; same thread).
    A running loop refuses instead of nesting (await instead). No hand
    pump, no background threads. The backend client is created and
    closed inside the driven coroutine (``async with`` in
    ``OpenAIBackend.complete``), so creation and close always share
    this loop; nothing is cached across calls.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        try:
            coro.close()
        except Exception:  # noqa: BLE001
            pass
        raise RuntimeError("live-fire-2: running loop present; await instead of nesting")
    return asyncio.run(coro)


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _submit(loop, coro):
    """Submit to the fresh-loop driver (``loop`` kept for wiring signature)."""
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


def _port_empty_readonly() -> bool:
    """Read-only ``:18765`` emptiness from the OS listener list.

    Parses ``netstat -ano`` output; LISTENING on ``:18765`` present means
    occupied. Never binds, never listens, never connects.
    """
    try:
        proc = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return False
    text = proc.stdout or ""
    for line in text.splitlines():
        if ":%d" % THIN_PORT in line and "LISTENING" in line.upper():
            return False
    return True


def _gates_closed() -> tuple:
    """Read-only dual-gate read-back. Returns (ok, release_status, thin_open)."""
    try:
        release_status = json.loads(RELEASE_PATH.read_text(encoding="utf-8")).get("status")
    except (OSError, ValueError):
        release_status = None
    thin_open = thin_mod.is_thin_open()
    return (release_status == "closed" and thin_open is False, release_status, thin_open)


def _sha_file(path: Path) -> str | None:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return None


def _land_transport_obs(exc) -> bool:
    """Persist the exit-7 transport diagnosis; never masks the failure.

    Reads the whitelisted snapshot carried on ``exc`` (or its cause chain),
    binds ``dialog_request_id`` and lands it atomically at
    ``LIVE_DIR/transport/<dialog_request_id>.json`` -- never inside the
    ``evidence/`` pair dir (the success criterion of exactly 2 files there
    is untouched). Returns True only when the file landed; False means the
    observation is unqualified (no snapshot, pollution refusal, disk error)
    and the caller must still exit 7 on the ORIGINAL error.
    """
    try:
        obs = sa_evidence.find_transport_obs(exc)
        if obs is None:
            return False
        carried = dict(obs)
        carried["dialog_request_id"] = DIALOG_REQUEST_ID
        sa_evidence.record_transport_diagnosis(
            directory=LIVE_DIR / "transport",
            dialog_request_id=DIALOG_REQUEST_ID,
            diagnosis=carried,
        )
    except Exception:
        return False
    return True


def main() -> int:
    marker = LIVE_DIR / "sent.json"
    if marker.exists():
        return _fail(4, "marker exists: refusing to resend %s" % DIALOG_REQUEST_ID)
    if not _port_empty_readonly():
        return _fail(5, "127.0.0.1:%d not empty at send time (read-only listener check)" % THIN_PORT)
    gates_ok, release_status, thin_open = _gates_closed()
    if not gates_ok:
        return _fail(
            6,
            "both gates must read closed pre-fire: release=%r thin_open=%r"
            % (release_status, thin_open),
        )

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

    registry_sha = _sha_file(REGISTRY_PATH)
    presets_sha = _sha_file(PRESETS_PATH)
    if registry_sha != REGISTRY_PIN:
        return _fail(6, "formal registry hash drift: %s" % (registry_sha,))
    if presets_sha != PRESETS_PIN:
        return _fail(6, "formal presets hash drift: %s" % (presets_sha,))

    # Re-playable request: byte-identical copy of the fixed ticket template.
    template_raw = TEMPLATE_PATH.read_bytes()
    template = json.loads(template_raw.decode("utf-8-sig"))
    if template.get("request_id") != DIALOG_REQUEST_ID:
        return _fail(6, "template request_id drift")
    if template.get("sha256") != TEMPLATE_INNER_PIN:
        return _fail(6, "template inner sha drift")

    # Preflight record BEFORE the marker: replayable input, params, hashes,
    # and the credential VARIABLE NAME only (never the secret value).
    preflight = {
        "ticket": "SA-OPEN-FIRE-2",
        "route": "%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS),
        "dialog_request_id": DIALOG_REQUEST_ID,
        "message": MESSAGE,
        "model": primary["model"],
        "params": {"temperature": 0.5, "max_tokens": 8},
        "key_ref": KEY_REF,
        "template_sha256": TEMPLATE_INNER_PIN,
        "registry_sha256": registry_sha,
        "presets_sha256": presets_sha,
        "gates_pre_fire": {"release": release_status, "thin_open": thin_open},
        "port_empty_readonly": True,
    }
    preflight_text = json.dumps(preflight, ensure_ascii=False, sort_keys=True, indent=2)
    if KEY_REF not in preflight_text:
        return _fail(6, "preflight must carry the key_ref name")
    (LIVE_DIR / "request.replayable.json").write_bytes(template_raw)
    (LIVE_DIR / "preflight-fire2.json").write_text(preflight_text, encoding="utf-8")

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
        text = fa_obs.observe_dialog(
            dialog,
            (("user", MESSAGE),),
            directory=LIVE_DIR / "failure",
            agent_id=AGENT,
            request_id=DIALOG_REQUEST_ID,
        )
    except Exception as exc:  # noqa: BLE001
        if _land_transport_obs(exc):
            return _fail(7, "transport failed (%s): consumed, no retry" % type(exc).__name__)
        return _fail(
            7,
            "transport failed (%s): consumed, no retry; transport observation unqualified"
            % type(exc).__name__,
        )
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
    if not isinstance(response.get("raw_text"), str) or not response["raw_text"]:
        return _fail(3, "raw missing: unqualified, no resend")
    if not isinstance(response.get("usage"), dict) or not response["usage"]:
        return _fail(3, "usage missing: unqualified, no resend")
    for name in names:
        content = (sink.directory / name).read_text(encoding="utf-8")
        if KEY_REF not in content:
            return _fail(3, "key_ref name missing from evidence")
        if "Bearer" in content or "api_key" in content:
            return _fail(3, "credential-shaped text in evidence: stop")

    gates_ok, release_status, thin_open = _gates_closed()
    if not gates_ok:
        return _fail(
            6,
            "post-fire gates drifted: release=%r thin_open=%r (consumed, no retry)"
            % (release_status, thin_open),
        )

    summary = {
        "ticket": "SA-OPEN-FIRE-2",
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
        "offline": False,
        "real_billing": 1,
        "secret_present_in_evidence": False,
        "gates_post_fire": {"release": release_status, "thin_open": thin_open},
    }
    (LIVE_DIR / "result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "live-fire-2 SENT once: transport_calls=1 pair_ok=True "
        "payload_sha256=%s raw_sha256=%s usage=%s reply_chars=%d gates=%s/%s"
        % (
            summary["payload_sha256"][:16],
            summary["raw_sha256"][:16],
            json.dumps(summary["usage"], sort_keys=True),
            len(text),
            release_status,
            thin_open,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
