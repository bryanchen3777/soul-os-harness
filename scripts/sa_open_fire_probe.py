#!/usr/bin/env python3
"""SA-OPEN-FIRE-1 one-time single-fire probe (closed by default).

Decision-complete scope (SA-OPEN-FIRE-1, mastermind Sol approved): this file
is new. ``scripts/serve_sa_thin.py``, ``src/**``, ``configs/**``,
``data/**``, ``release/sa.json`` and every Frozen contract are untouched.
No HTTP opener is added, no ``--host`` / ``--port`` flags exist; the
loopback bind (``127.0.0.1:18765``) is structural (mirrors the thin entry,
asserted equal in tests).

What this probe does (single route, single attempt, fully guarded):

  - Fixed ``rem/primary``, one fixed ``request_id``
    (``req-sa-open-fire-1-0001``), one fixed message (``"\\u4f60\\u597d\\u3002"``).
    No concurrency, no auto-retry, no補發. A timeout counts as consumed.
  - The single attempt is fenced by an atomically created sent marker
    (``O_CREAT | O_EXCL``) written BEFORE any send. A re-run with the marker
    present refuses to resend. The second quota is never enabled: at most one
    billed call can ever leave this probe in this ticket (in practice zero,
    see the evidence-channel stop below).
  - Before sending it persists a replayable request (route, model, params,
    messages, sha256, credential reference by VARIABLE NAME only --
    ``OLLAMA_API_KEY``; the secret value is never read, never saved, never
    printed).
  - After returning it requires the ORIGINAL ``CallResult`` raw + ``usage``
    and saves both. A missing raw is NOT qualified: the probe stops instead
    of substituting answer text or estimates.
  - Offline proof first: the current online dialog
    (``src/soul_access/wiring.py`` ``build_dialog_factory``) returns only
    ``str`` -- it carries no raw / usage channel. The default sender
    therefore refuses WITHOUT touching the network (``EvidenceGap``), the
    doors stay closed, and the ticket stops here. Extending the online chain
    needs a new decision-complete ticket; this probe must not do it.

Exit codes: 0 check-complete / sent | 2 usage | 3 EVIDENCE_GAP (not
qualified) | 4 ALREADY_SENT / OVER_QUOTA | 5 BLOCKED_PORT | 6 GATE_CLOSED.

Default action is ``--check`` (offline only: port preflight read, dialog
str-only proof, gate read-back; writes the check report, creates NO sent
marker, sends NOTHING). ``--fire`` is the single-fire path; in this ticket
it stops at the evidence gap (or earlier at BLOCKED_PORT / GATE_CLOSED)
with zero billed calls.

Importing this module has zero side effects (same idiom as
``scripts/serve_sa_thin.py``): every effect lives in ``main()``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

#: Fixed route (the only route this ticket may ever fire).
SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"

#: Fixed model: pinned production preset for rem/primary
#: (``configs/sa/presets.json``, e351541 pin; read-only reference).
MODEL = "deepseek-v4.1-flash:1017"

#: Fixed single request identity (one ticket = one request_id).
REQUEST_ID = "req-sa-open-fire-1-0001"

#: Fixed single message.
MESSAGE = "你好。"

#: Fixed params (mirror the pinned preset).
TEMPERATURE = 0.5
MAX_TOKENS = 8

#: Credential reference: VARIABLE NAME ONLY. The value is never read.
KEY_REF = "OLLAMA_API_KEY"

#: Single-attempt budget: the second quota is never enabled.
MAX_ATTEMPTS = 1

#: Loopback bind mirror (structural; asserted equal to the thin entry).
THIN_HOST = "127.0.0.1"
THIN_PORT = 18765

#: Default evidence root (tests inject a tmp dir; never ``data/**``).
EVIDENCE_DEFAULT = _REPO_ROOT / "verification" / "sa-open-fire-1"

#: Pinned production gate (READ-ONLY in this ticket; never written here).
RELEASE_DEFAULT = _REPO_ROOT / "release" / "sa.json"


class AlreadySent(Exception):
    """A sent marker already exists: refuse to resend (exit 4)."""


class OverQuota(Exception):
    """The single attempt is consumed: the 2nd call is refused (exit 4)."""


class EvidenceGap(Exception):
    """No raw + usage channel: result is NOT qualified (exit 3)."""


class BlockedPort(Exception):
    """``127.0.0.1:18765`` is not held by this ticket: refuse (exit 5)."""


class GateClosed(Exception):
    """Shared gate is not open: refuse to send (exit 6)."""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def marker_path(evidence_dir: Path) -> Path:
    return Path(evidence_dir) / "sent.json"


def request_path(evidence_dir: Path) -> Path:
    return Path(evidence_dir) / "request.json"


def result_path(evidence_dir: Path) -> Path:
    return Path(evidence_dir) / "result.json"


def build_request() -> dict:
    """Build the fixed replayable request (no secrets, key ref is a name)."""
    body = {
        "soul_alias": SOUL_ALIAS,
        "backend_alias": BACKEND_ALIAS,
        "model": MODEL,
        "params": {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
        "messages": [{"role": "user", "content": MESSAGE}],
        "request_id": REQUEST_ID,
        "key_ref": KEY_REF,
    }
    canonical = json.dumps(body, ensure_ascii=False, sort_keys=True)
    body["sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return body


def create_marker_atomic(evidence_dir: Path, *, now: str | None = None) -> Path:
    """Atomically create the sent marker BEFORE any send (O_EXCL).

    Raises ``AlreadySent`` when the marker exists (re-run must not resend)
    and ``OverQuota`` is enforced by the caller via ``MAX_ATTEMPTS``.
    """
    path = marker_path(evidence_dir)
    payload = json.dumps(
        {"request_id": REQUEST_ID, "attempt": MAX_ATTEMPTS, "created_utc": now or _utcnow()},
        ensure_ascii=False,
        sort_keys=True,
    )
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags, 0o600)
    except FileExistsError:
        raise AlreadySent("marker exists: refusing to resend %s" % REQUEST_ID)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    return path


def save_request(evidence_dir: Path) -> Path:
    path = request_path(evidence_dir)
    path.write_text(
        json.dumps(build_request(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def validate_evidence(result: Any) -> Mapping:
    """Require original CallResult raw + usage. str-only text is rejected.

    The current dialog returns ``str`` (wiring ``build_dialog_factory``):
    it cannot be mistaken for evidence. Anything without a non-empty
    ``raw`` and a ``usage`` mapping carrying ``prompt_tokens`` /
    ``completion_tokens`` raises ``EvidenceGap`` (NOT qualified, stop).
    """
    if not isinstance(result, Mapping):
        raise EvidenceGap("result is %s, not a CallResult mapping" % type(result).__name__)
    raw = result.get("raw")
    usage = result.get("usage")
    if raw is None or raw == "" or raw == {}:
        raise EvidenceGap("missing raw: answer text cannot substitute evidence")
    if not isinstance(usage, Mapping):
        raise EvidenceGap("missing usage mapping")
    for field in ("prompt_tokens", "completion_tokens"):
        value = usage.get(field)
        if not isinstance(value, int) or value < 0:
            raise EvidenceGap("usage.%s is not a token count" % field)
    return result


def save_result(evidence_dir: Path, result: Mapping) -> Path:
    validated = validate_evidence(result)
    path = result_path(evidence_dir)
    path.write_text(
        json.dumps(
            {
                "request_id": REQUEST_ID,
                "raw": validated["raw"],
                "usage": dict(validated["usage"]),
                "billed_attempts": 1,
                "saved_utc": _utcnow(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def is_loopback_free(_socket_factory: Callable | None = None) -> bool:
    """Read-only probe: can THIS process bind ``THIN_HOST:THIN_PORT``?"""
    factory = _socket_factory if _socket_factory is not None else socket.socket
    try:
        probe = factory()
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


def read_gate_status(gate_path: Path | str) -> str:
    """Read the shared gate (read-only). Anything but open == closed."""
    try:
        data = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "closed"
    if isinstance(data, dict) and data.get("status") == "open":
        return "open"
    return "closed"


class InstanceDoor:
    """Process-local single-fire door: closed by default, finally-closed.

    In SA-OPEN-FIRE-1 the real fire path never opens this door (the
    evidence gap stops first). The context manager exists so tests can
    prove the finally-close property on sandbox doubles.
    """

    def __init__(self) -> None:
        self._open = False

    def is_open(self) -> bool:
        return self._open

    @contextmanager
    def open_for_single_fire(self):
        self._open = True
        try:
            yield self
        finally:
            self._open = False


def _default_sender(request: Mapping) -> Any:
    """Default sender: refuse WITHOUT touching the network.

    The only wired dialog returns ``str`` -- there is no raw / usage
    channel to capture. Sending text-only evidence would be an
    unknowable gap, so the probe stops here (exit 3). Wiring a real
    evidence-carrying sender needs a new decision-complete ticket.
    """
    raise EvidenceGap(
        "dialog returns str-only (no raw/usage channel); "
        "refusing to fire without evidence (wiring.py build_dialog_factory)"
    )


def attempt_fire(
    evidence_dir: Path | str,
    *,
    sender: Callable[[Mapping], Any] | None = None,
    socket_factory: Callable | None = None,
    gate_path: Path | str | None = None,
    now: str | None = None,
    door: InstanceDoor | None = None,
) -> Path:
    """Run the guarded single fire. Returns the saved result path.

    Order: marker check -> port preflight -> atomic marker -> request
    save -> gate read (must be open) -> ONE sender call (no retry) ->
    evidence validation -> result save. Any exception after the marker
    leaves the attempt CONSUMED (no补发). ``door`` is opened only around
    the sender call and is finally-closed by construction.
    """
    evidence = Path(evidence_dir)
    gate = Path(gate_path) if gate_path is not None else RELEASE_DEFAULT
    send = sender if sender is not None else _default_sender
    latch = door if door is not None else InstanceDoor()

    if marker_path(evidence).exists():
        raise AlreadySent("marker exists: refusing to resend %s" % REQUEST_ID)
    if not is_loopback_free(socket_factory):
        raise BlockedPort("%s:%d is not free for this ticket" % (THIN_HOST, THIN_PORT))
    create_marker_atomic(evidence, now=now)
    request = build_request()
    save_request(evidence)
    if read_gate_status(gate) != "open":
        raise GateClosed("shared gate is not open: refusing to send")
    with latch.open_for_single_fire():
        result = send(request)
    return save_result(evidence, result)


def offline_evidence_gap_proof() -> dict:
    """Prove the str-only gap WITHOUT any network: build the real dialog
    factory over an in-memory fake backend and record what it returns."""
    from src.soul_access import wiring as _wiring

    seen: dict = {}

    class _FakeFuture:
        def __init__(self, value):
            self.value = value

        def result(self):
            return self.value

    class _FakeBackend:
        def complete(self, *, messages, model, max_tokens, temperature, max_retries):
            seen["call"] = {
                "model": model,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "max_retries": max_retries,
            }
            return "fake-reply-text"

    factory = _wiring.build_dialog_factory(
        llm=type("LLM", (), {"backend": _FakeBackend()})(),
        loop=object(),
        submit=lambda loop, coro: _FakeFuture(coro),
    )
    from src.soul_access import schema as _schema

    snapshot = _schema.SoulSnapshot(
        identity=_schema.RequestIdentity(
            request_id=REQUEST_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-fire-1",
            idempotency_key="key-fire-1",
        ),
        dedup=_schema.DedupKey(
            principal_id="bryan",
            agent_id="agent_rem",
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fire-1",
        ),
        backend_alias=BACKEND_ALIAS,
        model=MODEL,
        params=(("temperature", str(TEMPERATURE)), ("max_tokens", str(MAX_TOKENS))),
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
    dialog = factory(snapshot)
    text = dialog((("user", MESSAGE),), agent_id="agent_rem", request_id=REQUEST_ID)
    return {
        "dialog_return_type": type(text).__name__,
        "dialog_returns_str_only": isinstance(text, str),
        "has_raw": hasattr(text, "raw") if not isinstance(text, str) else False,
        "has_usage": hasattr(text, "usage") if not isinstance(text, str) else False,
        "evidence_channel_available": False,
        "backend_call": seen.get("call"),
        "conclusion": (
            "dialog returns str-only; no raw/usage channel exists, "
            "so --fire must stop (EVIDENCE_GAP) without sending"
        ),
    }


def run_check(evidence_dir: Path | str, *, socket_factory: Callable | None = None) -> dict:
    """Offline check: port preflight + str-only proof + gate read-back."""
    evidence = Path(evidence_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    report = {
        "ticket": "SA-OPEN-FIRE-1",
        "request_id": REQUEST_ID,
        "route": "%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS),
        "checked_utc": _utcnow(),
        "port": {"host": THIN_HOST, "port": THIN_PORT, "free_for_this_ticket": is_loopback_free(socket_factory)},
        "evidence_gap": offline_evidence_gap_proof(),
        "shared_gate": read_gate_status(RELEASE_DEFAULT),
        "sent_marker_present": marker_path(evidence).exists(),
        "billed_attempts": 0,
    }
    (evidence / "check-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="SA-OPEN-FIRE-1 one-time single-fire probe (closed by default)."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--check", action="store_true", help="Offline check only (default).")
    action.add_argument("--fire", action="store_true", help="Guarded single fire (stops: see exit codes).")
    parser.add_argument(
        "--evidence-dir",
        default=str(EVIDENCE_DEFAULT),
        help="Evidence root (default: %(default)s; tests use a tmp dir; never data/**).",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.fire:
        try:
            saved = attempt_fire(args.evidence_dir)
        except AlreadySent as exc:
            print("already-sent: %s" % exc, file=sys.stderr)
            return 4
        except OverQuota as exc:
            print("over-quota: %s" % exc, file=sys.stderr)
            return 4
        except BlockedPort as exc:
            print("blocked-port: %s" % exc, file=sys.stderr)
            return 5
        except GateClosed as exc:
            print("gate-closed: %s" % exc, file=sys.stderr)
            return 6
        except EvidenceGap as exc:
            print("evidence-gap (not qualified, stopped): %s" % exc, file=sys.stderr)
            return 3
        print("sent once: %s" % saved)
        return 0
    report = run_check(args.evidence_dir)
    print(
        "check complete: port_free=%s evidence_channel=%s gate=%s billed=%d"
        % (
            report["port"]["free_for_this_ticket"],
            "available" if report["evidence_gap"]["evidence_channel_available"] else "GAP",
            report["shared_gate"],
            report["billed_attempts"],
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
