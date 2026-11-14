"""SA-OPEN-FIRE-1 續行演練 drill-2：獨立程序跨行程讀回對帳。

本行程與 drill-1 寫入行程無任何共享記憶體物件或快取：全新直譯器、
只讀取證檔（請求／回應 JSON）與 ``fire-report.json`` 的記錄值，逐項
重算雜湊並比對。只印計數與雜湊，不印 raw 全文。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONT_DIR = Path(__file__).resolve().parent
SANDBOX = CONT_DIR / "sandbox-evidence"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.soul_access import evidence as sa_evidence  # noqa: E402

SYNTHETIC_MARK = "合成資料（隔離演練，非實彈）"
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"


def main() -> int:
    sink = sa_evidence.EvidenceSink(SANDBOX)  # 新行程重新解析目錄，無共享物件
    names = sorted(p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-"))
    assert len(names) == 2, "expected one pair, got %s" % (names,)
    evidence_request_id = names[0].split(".request.json")[0]

    report = sink.verify_pair(evidence_request_id)
    request = json.loads((sink.directory / names[0]).read_text(encoding="utf-8"))
    response = json.loads((sink.directory / names[1]).read_text(encoding="utf-8"))

    recomputed_payload = sa_evidence.sha256_hex(sa_evidence.canonical_bytes(request["payload"]))
    recomputed_raw = sa_evidence.sha256_hex(str(response.get("raw_text", "")).encode("utf-8"))

    fire_report = json.loads((CONT_DIR / "fire-report.json").read_text(encoding="utf-8"))

    checks = {
        "pair_ok": report["pair_ok"] is True,
        "ids_match_request_response": (
            request.get("request_id") == evidence_request_id
            and response.get("request_id") == evidence_request_id
        ),
        "payload_sha256_ok": request.get("payload_sha256") == recomputed_payload,
        "raw_sha256_ok": response.get("raw_sha256") == recomputed_raw,
        "usage_ok": isinstance(response.get("usage"), dict) and bool(response.get("usage")),
        "synthetic_mark_ok": SYNTHETIC_MARK in str(response.get("raw_text", "")),
        "secret_absent_ok": all(
            SENTINEL_API_KEY not in (sink.directory / n).read_text(encoding="utf-8")
            for n in names
        ),
        "key_ref_ok": all(
            sa_evidence.KEY_REF in (sink.directory / n).read_text(encoding="utf-8")
            for n in names
        ),
        "report_match_ok": (
            fire_report.get("evidence_request_id") == evidence_request_id
            and fire_report.get("payload_sha256") == recomputed_payload
            and fire_report.get("raw_sha256") == recomputed_raw
            and fire_report.get("usage") == response.get("usage")
            and fire_report.get("transport_calls") == 1
        ),
    }
    overall = all(checks.values())
    (CONT_DIR / "crosscheck-report.json").write_text(
        json.dumps(
            {
                "ticket": "SA-OPEN-FIRE-1-continuation-1",
                "drill": "independent-cross-process-readback",
                "evidence_request_id": evidence_request_id,
                "dialog_request_id": fire_report.get("dialog_request_id"),
                "checks": checks,
                "overall": "PASS" if overall else "FAIL",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        "drill-2 %s: pair=%s ids=%s payload=%s raw=%s usage=%s synthetic=%s report=%s"
        % (
            "PASS" if overall else "FAIL",
            checks["pair_ok"],
            checks["ids_match_request_response"],
            checks["payload_sha256_ok"],
            checks["raw_sha256_ok"],
            checks["usage_ok"],
            checks["synthetic_mark_ok"],
            checks["report_match_ok"],
        )
    )
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
