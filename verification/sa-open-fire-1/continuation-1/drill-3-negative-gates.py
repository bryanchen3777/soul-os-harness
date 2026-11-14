"""SA-OPEN-FIRE-1 續行演練 drill-3：雙門負向驗證＋復閉（隔離環境）。

第一門＝薄端實例門（``serve_sa_thin._THIN_OPEN`` 常閉，本票無 opener）；
第二門＝共享門（生產 ``release/sa.json``；本演練只用沙盒複本，生產檔唯讀）。

  - 僅第一門閉：沙盒門=open，但實例門常閉 → handler 先行 403，
    計數 forwarder 零呼叫（上游計數零）。
  - 僅第二門閉：沙盒門=closed 的 forwarder（真實釋放檢查路徑）→ 403，
    計數 sender 零呼叫（上游計數零）。
  - 雙門復閉：兩者再驗一次皆 403 且零呼叫；另讀回生產門 closed 與
    ``is_thin_open() is False``（持久＋運行雙重核對）。

不啟服務、不綁埠、不寫 ``data/**``、不寫生產門；只印狀態與計數。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONT_DIR = Path(__file__).resolve().parent

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import scripts.serve_sa_thin as thin_mod  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

DIALOG_REQUEST_ID = "req-sa-open-fire-1-0001"
AGENT = "agent_rem"


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
            principal_id="continuation-1-offline-owner",
            agent_id=AGENT,
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


class _CountingForwarder:
    def __init__(self):
        self.calls = []

    def __call__(self, envelope, *, route=None, preset=None):
        self.calls.append(envelope)
        raise AssertionError("isolation breach: closed thin door forwarded")


class _CountingSender:
    def __init__(self):
        self.calls = []

    def __call__(self, url, body):
        self.calls.append((url, body))
        raise AssertionError("isolation breach: closed release gate sent")


def _is_403(exc) -> bool:
    return getattr(exc, "status", None) == 403


def main() -> int:
    gate_open = CONT_DIR / "sandbox-gate-open.json"
    gate_closed = CONT_DIR / "sandbox-gate-closed.json"
    gate_open.write_text(json.dumps({"status": "open"}), encoding="utf-8")
    gate_closed.write_text(json.dumps({"status": "closed"}), encoding="utf-8")
    envelope = sa_api.build_envelope(
        snapshot=_snapshot(),
        messages=(("user", "你好。"),),
        created_utc="2026-11-14T22:00:00Z",
    )

    # -- 僅第一門閉：實例門常閉，沙盒門=open 亦攔截 --
    forwarder_a = _CountingForwarder()
    handler = thin_mod.build_thin_closed_handler(
        release_path=str(gate_open), _forwarder=forwarder_a
    )
    assert thin_mod.is_thin_open() is False
    try:
        handler(object(), route=None, preset=None)
        raise AssertionError("door-1-only-closed: unexpectedly admitted")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, AssertionError) and "unexpectedly" in str(exc):
            raise
        door1_403 = _is_403(exc)
    case_door1 = {
        "refused_403": bool(door1_403),
        "upstream_calls": len(forwarder_a.calls),
        "pass": bool(door1_403) and len(forwarder_a.calls) == 0,
    }

    # -- 僅第二門閉：沙盒門=closed 的真實釋放檢查路徑 --
    sender_b = _CountingSender()
    forwarder_b = wiring.build_thin_forwarder(
        release_path=str(gate_closed), sender=sender_b
    )
    try:
        forwarder_b(envelope, route="rem/primary", preset="primary")
        raise AssertionError("door-2-only-closed: unexpectedly admitted")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, AssertionError) and "unexpectedly" in str(exc):
            raise
        door2_403 = _is_403(exc)
    case_door2 = {
        "refused_403": bool(door2_403),
        "upstream_calls": len(sender_b.calls),
        "pass": bool(door2_403) and len(sender_b.calls) == 0,
    }

    # -- 雙門復閉再驗 --
    forwarder_c = _CountingForwarder()
    handler_c = thin_mod.build_thin_closed_handler(
        release_path=str(gate_closed), _forwarder=forwarder_c
    )
    try:
        handler_c(object(), route=None, preset=None)
        raise AssertionError("both-closed handler: unexpectedly admitted")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, AssertionError) and "unexpectedly" in str(exc):
            raise
        reclose_handler_403 = _is_403(exc)
    sender_d = _CountingSender()
    forwarder_d = wiring.build_thin_forwarder(
        release_path=str(gate_closed), sender=sender_d
    )
    try:
        forwarder_d(envelope, route="rem/primary", preset="primary")
        raise AssertionError("both-closed forwarder: unexpectedly admitted")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, AssertionError) and "unexpectedly" in str(exc):
            raise
        reclose_forwarder_403 = _is_403(exc)

    production_release = json.loads(
        (REPO_ROOT / "release" / "sa.json").read_text(encoding="utf-8")
    )
    case_reclose = {
        "handler_refused_403": bool(reclose_handler_403),
        "forwarder_refused_403": bool(reclose_forwarder_403),
        "upstream_calls": len(forwarder_c.calls) + len(sender_d.calls),
        "production_release": production_release,
        "thin_open": thin_mod.is_thin_open(),
        "pass": bool(reclose_handler_403)
        and bool(reclose_forwarder_403)
        and len(forwarder_c.calls) + len(sender_d.calls) == 0
        and production_release == {"status": "closed"}
        and thin_mod.is_thin_open() is False,
    }

    overall = case_door1["pass"] and case_door2["pass"] and case_reclose["pass"]
    (CONT_DIR / "negative-report.json").write_text(
        json.dumps(
            {
                "ticket": "SA-OPEN-FIRE-1-continuation-1",
                "drill": "negative-gates-and-reclose",
                "door1_only_closed": case_door1,
                "door2_only_closed": case_door2,
                "both_closed_reverified": case_reclose,
                "overall": "PASS" if overall else "FAIL",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        "drill-3 %s: door1_403=%s up1=%d door2_403=%s up2=%d "
        "reclose=%s up=%d prod_release=%s thin_open=%s"
        % (
            "PASS" if overall else "FAIL",
            case_door1["refused_403"],
            case_door1["upstream_calls"],
            case_door2["refused_403"],
            case_door2["upstream_calls"],
            case_reclose["pass"],
            case_reclose["upstream_calls"],
            production_release,
            thin_mod.is_thin_open(),
        )
    )
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
