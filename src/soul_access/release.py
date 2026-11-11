"""SA-1 soul-access release gate (thin-entry inbound switch).

Hot-read per inbound request: every call re-reads the release file from disk,
so flipping the file takes effect on the very next request with no restart
and no in-process cache to go stale.

Fail-closed: a missing file, an unreadable file, invalid JSON, a non-object
payload, or any status other than ``"open"`` all mean CLOSED (new inbound
refused). The gate never raises for gate I/O or content faults; refusal is
reported by the caller (bridge handler -> 403, see ``bridge.py``).

Purity: stdlib only (json + pathlib). No network, no clock, no global
mutable state, no EI-1 imports, no provider imports.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "RELEASE_OPEN_STATUS",
    "is_live_inbound_open",
]

#: The only release-file status that admits new soul-access inbound.
RELEASE_OPEN_STATUS = "open"


def is_live_inbound_open(release_path) -> bool:
    """Hot-read the release file; True only when it says open right now.

    Any fault (missing / unreadable / bad JSON / non-dict / status !=
    ``"open"``) returns False. Never raises for gate faults.
    """
    try:
        raw = Path(release_path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return False
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(data, dict):
        return False
    return data.get("status") == RELEASE_OPEN_STATUS
