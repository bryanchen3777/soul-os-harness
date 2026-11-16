"""Manual single-shot Responses check (SA-RESPONSES-NATIVE-1, NOT run by pytest).

Requires explicit owner authorization before every run. Reads the live
``OPENCODE_GO_API_KEY`` from the environment, sends ONE minimal native
``POST {base_url}/responses`` via
``src.soul_access.responses_backend.forward_responses_request``, prints the
status code plus the reply ``id``/``model`` fields only, and exits.
Never prints the key, the request body, or the full reply.

Usage (repo root, authorized runs only):
  .\\.venv\\Scripts\\python.exe scripts\\manual_verify_responses_opencode.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.interfaces.schema import InterfaceError
from src.soul_access import responses_backend as resp_mod


def main() -> int:
    try:
        key = resp_mod.load_opencode_go_key()
    except InterfaceError as exc:
        print("refused: status=%s code=%s message=%s" % (exc.status, exc.code, exc.message))
        return 1
    body = {"model": resp_mod.RESPONSES_MODEL, "input": "manual ping", "stream": False}
    try:
        status, data = resp_mod.forward_responses_request(
            body, "https://opencode.ai/zen/go/v1", key
        )
    except InterfaceError as exc:
        print("upstream refused: status=%s code=%s message=%s" % (exc.status, exc.code, exc.message))
        return 2
    summary = {"status": status}
    if isinstance(data, dict):
        for field in ("id", "model", "object"):
            if field in data:
                summary[field] = data[field]
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
