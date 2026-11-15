"""SA-BYOK-VERIFY-1 single BYOK-channel probe (Owner-authorized one-shot).

Sends exactly ONE plain OpenAI chat-completions body -- the exact shape a
BYOK client (MiniMax Code custom_provider, openai-completions) sends:
``{"model": "sa-rem-primary", "messages": [...], "max_tokens": N,
"stream": false}`` -- through the OPEN thin entry
(127.0.0.1:18765/rem/primary/v1/chat/completions, Authorization: Bearer
with the client secret read locally at runtime). The secret value is
never printed, never saved, never logged: stdout and result.json carry
only counts, hashes and the key/client reference NAME.

Order: gate-open check (read-only) -> port-occupied check (read-only
listener list; the thin entry must hold :18765) -> atomic marker
(O_EXCL; a present marker refuses resend, exit 4) -> ONE POST (no
retry; timeout counts as consumed) -> require HTTP 200 (else exit 7,
consumed) -> print + save counts/hashes only.

Exit codes: 0 sent-200 | 4 ALREADY_SENT | 5 PORT_EMPTY | 6 GATE_NOT_OPEN |
7 TRANSPORT_OR_NON200 (consumed, no retry). Any non-0 exit means the
caller must panic-close and report BLOCKED per the ticket.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import subprocess
import sys
from pathlib import Path

LIVE_DIR = Path(__file__).resolve().parent
MARKER = LIVE_DIR / "sent.json"
RESULT = LIVE_DIR / "result.json"

SOUL = "rem"
ALIAS = "primary"
PUBLIC_MODEL_ID = "sa-rem-primary"
MESSAGE = "你好"
DIALOG_REQUEST_ID = "req-sa-byok-verify-1-0001"
KEY_PATH = Path(r"C:\Users\bbfcc\.soul\client-bryan-01.key")
CLIENT_ID = "client-bryan-01"
THIN_PORT = 18765
RELEASE_PATH = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\release\sa.json")


def _fail(code: int, msg: str) -> int:
    print("probe refused/failed: %s" % msg, file=sys.stderr)
    return code


def _gate_open_readonly() -> bool:
    try:
        return json.loads(RELEASE_PATH.read_text(encoding="utf-8")).get("status") == "open"
    except (OSError, ValueError):
        return False


def _port_held_readonly() -> bool:
    """True when something LISTENs on :18765 (read-only listener list)."""
    try:
        proc = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, timeout=10
        )
    except Exception:
        return False
    for line in (proc.stdout or "").splitlines():
        if ":%d" % THIN_PORT in line and "LISTENING" in line.upper():
            return True
    return False


def main() -> int:
    if MARKER.exists():
        return _fail(4, "marker exists: refusing to resend %s" % DIALOG_REQUEST_ID)
    if not _gate_open_readonly():
        return _fail(6, "shared gate not open at send time (no send, unconsumed)")
    if not _port_held_readonly():
        return _fail(5, "thin entry not holding :18765 (no send, unconsumed)")

    try:
        secret = KEY_PATH.read_bytes().decode("utf-8").strip()
    except (OSError, ValueError):
        return _fail(6, "client key file unreadable (no send, unconsumed)")
    if not secret:
        return _fail(6, "client key empty (no send, unconsumed)")

    # Plain OpenAI body: exactly what a BYOK openai-completions client sends.
    body = json.dumps(
        {
            "model": PUBLIC_MODEL_ID,
            "messages": [{"role": "user", "content": MESSAGE}],
            "max_tokens": 64,
            "stream": False,
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(MARKER), flags, 0o600)
    except FileExistsError:
        return _fail(4, "marker raced: refusing to resend %s" % DIALOG_REQUEST_ID)
    os.write(fd, json.dumps({"request_id": DIALOG_REQUEST_ID, "attempt": 1}).encode("utf-8"))
    os.close(fd)

    try:
        conn = http.client.HTTPConnection("127.0.0.1", THIN_PORT, timeout=120)
        conn.request(
            "POST",
            "/%s/%s/v1/chat/completions" % (SOUL, ALIAS),
            body=body,
            headers={
                "Authorization": "Bearer " + secret,
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
            },
        )
        response = conn.getresponse()
        status = response.status
        raw = response.read()
    except Exception as exc:  # noqa: BLE001 -- consumed, no retry
        return _fail(7, "transport failed (%s): consumed, no retry" % type(exc).__name__)
    if status != 200:
        return _fail(7, "non-200 status %d: consumed, no retry" % status)
    try:
        payload = json.loads(raw.decode("utf-8"))
        text = payload["choices"][0]["message"]["content"]
        usage = payload.get("usage", {})
    except (ValueError, KeyError, IndexError, TypeError):
        return _fail(7, "200 body unparseable: consumed, no retry")
    if not isinstance(text, str) or not text:
        return _fail(7, "200 with empty reply: consumed, no retry")

    reply_sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    summary = {
        "ticket": "SA-BYOK-VERIFY-1",
        "route": "%s/%s" % (SOUL, ALIAS),
        "public_model_id": PUBLIC_MODEL_ID,
        "body_shape": "plain-openai",
        "dialog_request_id": DIALOG_REQUEST_ID,
        "client_id": CLIENT_ID,
        "message": MESSAGE,
        "http_status": status,
        "reply_chars": len(text),
        "reply_sha256": reply_sha,
        "usage": usage,
        "transport_calls": 1,
        "real_billing": 1,
    }
    RESULT.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "probe SENT once through open door: http=200 reply_chars=%d "
        "reply_sha=%s usage=%s"
        % (len(text), reply_sha[:16], json.dumps(usage, sort_keys=True))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
