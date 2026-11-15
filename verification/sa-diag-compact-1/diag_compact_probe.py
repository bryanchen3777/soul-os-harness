"""SA-DIAG-COMPACT-01 single diagnostic packet (Sol-approved one-shot).

Sends exactly ONE plain OpenAI chat-completions body (short system+user,
stream:false, max_tokens:32, zero retry) through the OPEN thin entry
(127.0.0.1:18765/rem/primary/v1/chat/completions). Precondition: application-
level trace of the ~18:50 desktop retry window is absent (thin entry stdout
is lost by hidden-start design; only TCP TIME_WAITs remain), so this ticket
authorizes exactly one packet. Sent = consumed, never resent.

Exit codes: 0 got-HTTP-reply (any status, recorded) | 4 ALREADY_SENT |
5 PORT_EMPTY | 6 GATE_NOT_OPEN. Transport fault also recorded, no retry.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import sys
from pathlib import Path

LIVE_DIR = Path(__file__).resolve().parent
MARKER = LIVE_DIR / "sent.json"
RESULT = LIVE_DIR / "result.json"

DIALOG_REQUEST_ID = "req-sa-diag-compact-1-0001"
KEY_PATH = Path(r"C:\Users\bbfcc\.soul\client-bryan-01.key")
THIN_PORT = 18765
RELEASE_PATH = Path(r"C:\Users\bbfcc\.local\bin\soul-os-harness\release\sa.json")


def _fail(code: int, msg: str) -> int:
    print("diag probe refused/failed: %s" % msg, file=sys.stderr)
    return code


def _gate_open_readonly() -> bool:
    try:
        return json.loads(RELEASE_PATH.read_text(encoding="utf-8")).get("status") == "open"
    except (OSError, ValueError):
        return False


def _port_held_readonly() -> bool:
    s = None
    try:
        s = socket.create_connection(("127.0.0.1", THIN_PORT), timeout=3)
        return True
    except OSError:
        return False
    finally:
        if s is not None:
            s.close()


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

    body = json.dumps(
        {
            "model": "sa-rem-primary",
            "messages": [
                {"role": "system", "content": "你是Rem，用中文简短回答。"},
                {"role": "user", "content": "你好"},
            ],
            "max_tokens": 32,
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

    status = -1
    raw = b""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", THIN_PORT, timeout=120)
        conn.request(
            "POST",
            "/rem/primary/v1/chat/completions",
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
        RESULT.write_text(
            json.dumps(
                {
                    "ticket": "SA-DIAG-COMPACT-01",
                    "dialog_request_id": DIALOG_REQUEST_ID,
                    "transport": "fault",
                    "fault_type": type(exc).__name__,
                    "transport_calls": 1,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print("diag probe SENT once: transport fault (%s): consumed, no retry" % type(exc).__name__)
        return 0

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = "<non-utf8:%d bytes>" % len(raw)
    RESULT.write_text(
        json.dumps(
            {
                "ticket": "SA-DIAG-COMPACT-01",
                "dialog_request_id": DIALOG_REQUEST_ID,
                "http_status": status,
                "raw_body": text,
                "transport_calls": 1,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print("diag probe SENT once: http=%d raw_chars=%d" % (status, len(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
