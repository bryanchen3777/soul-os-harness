"""SA-OPEN-FIRE-1 live-fire-1: post-fire closed-door negative verification.

Reads production ``release/sa.json`` (read-only) and the thin instance door
(import-only, zero side effects). Proves, with counting doubles:
  - door 1 (thin instance, ``_THIN_OPEN`` False): handler 403, zero forward;
  - door 2 (shared release, must read closed): real release-check path
    (``wiring.build_thin_forwarder``) 403, zero upstream calls.
Writes only ``negative-report.json`` in this directory. No sends, no prod writes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_DIR = Path(__file__).resolve().parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.serve_sa_thin as thin_mod  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402


def main() -> int:
    production_release = json.loads(
        (REPO_ROOT / "release" / "sa.json").read_text(encoding="utf-8")
    )

    snapshot = sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id="req-sa-open-fire-1-0001",
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-fire-1",
            idempotency_key="key-fire-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id="bryan",
            agent_id="agent_rem",
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-fire-1",
        ),
        backend_alias="primary",
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
    envelope = sa_api.build_envelope(
        snapshot=snapshot,
        messages=(("user", "你好。"),),
        created_utc="2026-11-14T22:00:00Z",
    )

    forwards = []

    def _counting_forwarder(e, *, route=None, preset=None):
        forwards.append((route, preset))
        raise AssertionError("closed thin door forwarded")

    upstream = []

    def _counting_sender(url, body):
        upstream.append(url)
        raise AssertionError("closed release gate sent")

    handler = thin_mod.build_thin_closed_handler(_forwarder=_counting_forwarder)
    try:
        handler(object(), route=None, preset=None)
        door1 = {"refused_403": False, "upstream_calls": len(forwards)}
    except Exception as exc:  # noqa: BLE001
        door1 = {
            "refused_403": getattr(exc, "status", None) == 403,
            "upstream_calls": len(forwards),
        }
    door1["pass"] = door1["refused_403"] and door1["upstream_calls"] == 0

    forwarder = wiring.build_thin_forwarder(
        release_path=str(REPO_ROOT / "release" / "sa.json"), sender=_counting_sender
    )
    try:
        forwarder(envelope, route="rem/primary", preset="primary")
        door2 = {"refused_403": False, "upstream_calls": len(upstream)}
    except Exception as exc:  # noqa: BLE001
        door2 = {
            "refused_403": getattr(exc, "status", None) == 403,
            "upstream_calls": len(upstream),
        }
    door2["pass"] = door2["refused_403"] and door2["upstream_calls"] == 0

    overall = (
        door1["pass"]
        and door2["pass"]
        and production_release == {"status": "closed"}
        and thin_mod.is_thin_open() is False
    )
    (LIVE_DIR / "negative-report.json").write_text(
        json.dumps(
            {
                "ticket": "SA-OPEN-FIRE-1-live-fire-1",
                "door1_thin_instance": door1,
                "door2_shared_release": door2,
                "production_release": production_release,
                "thin_open": thin_mod.is_thin_open(),
                "overall": "PASS" if overall else "FAIL",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        "negative %s: door1_403=%s up1=%d door2_403=%s up2=%d prod=%s thin=%s"
        % (
            "PASS" if overall else "FAIL",
            door1["refused_403"],
            door1["upstream_calls"],
            door2["refused_403"],
            door2["upstream_calls"],
            production_release,
            thin_mod.is_thin_open(),
        )
    )
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
