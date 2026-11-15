"""SA-DOOR-OPEN-1 graceful thin-entry shutdown (no kills).

Reads the loopback control file (run_id + token + endpoint), POSTs one
``shutdown`` command to the fixed loopback endpoint, and exits 0 only on
a 200 reply (reply-first drain: the server closes its own listener, then
exits its serving loop so the process ends normally and releases :18765).
The token value is never printed, never saved, never logged.

This is the ONLY approved way for this ticket to release :18765: no
Stop-Process / taskkill / pkill / kill anywhere in this file.

Exit codes: 0 shut-200 | 2 no-contact / bad control file.
"""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
from pathlib import Path

THIN_HOST = "127.0.0.1"
THIN_PORT = 18765
CONTROL_ENDPOINT = "/internal/sa-thin/control"
CONTROL_FILE_DEFAULT = str(
    Path(tempfile.gettempdir()) / "sa-thin-18765.control.json"
)


def main(argv=None) -> int:
    control_file = argv[1] if argv and len(argv) > 1 else CONTROL_FILE_DEFAULT
    try:
        filed = json.loads(Path(control_file).read_text(encoding="utf-8"))
        token = filed.get("token")
        endpoint = filed.get("endpoint", CONTROL_ENDPOINT)
    except Exception:
        print("thin shutdown: unreadable control file.", file=sys.stderr)
        return 2
    if endpoint != CONTROL_ENDPOINT or not isinstance(token, str) or not token:
        print("thin shutdown: control file refused.", file=sys.stderr)
        return 2
    body = json.dumps({"token": token, "command": "shutdown"})
    try:
        conn = http.client.HTTPConnection(THIN_HOST, THIN_PORT, timeout=10)
        conn.request(
            "POST",
            CONTROL_ENDPOINT,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body.encode("utf-8"))),
            },
        )
        response = conn.getresponse()
        status = response.status
        response.read()
    except Exception:
        print("thin shutdown: no contact.", file=sys.stderr)
        return 2
    if status == 200:
        print("thin shutdown: ok (200, draining).")
        return 0
    print("thin shutdown: refused (status %d)." % status, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
