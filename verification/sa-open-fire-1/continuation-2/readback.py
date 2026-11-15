"""SA-ISOLATED-RECOVERY-1 continuation-2 readback（獨立程序跨行程讀回對帳）。

本行程與 drill 行程無任何共享記憶體物件或快取：全新直譯器、只讀 run 目錄
落檔（transport／evidence／門檔／合成配置／模板複本）與 ``drill-report.json``
的記錄值，逐項重算雜湊並比對。只印計數與雜湊，不印 raw 全文。

驗證項：
  1. 六案 transport 檔：五欄（type／chain／errno／elapsed_ms／phase）、
     兩種 request_id 關聯、恰一次傳輸；失敗案 evidence 只留 request。
  2. 成功案 evidence 判準：恰有同 ID 的 request／response 兩個一般檔
     （兄弟 transport／其他目錄不計入）且 pair_ok；payload／raw／usage
     與釘選值及舊證據一致；額外檔案、缺檔反例必須被判準拒絕。
  3. 模板：run 內複本 bytes 與生產模板一致、內嵌摘要一致；舊證據及雙 sent
     前後 SHA 不變。
  4. 脫密掃描：落檔內無 sentinel 明文、無 Bearer 形態文字；只記 key_ref。
  5. 薄端復閉：新行程 ``is_thin_open() is False``＋沙盒門讀回 closed，
     以 run 內合成配置重建薄端 app，loopback 再驗 403 且零傳輸。

用法：``python readback.py [--run-id <run_id>]``（省略時僅當恰有一個 run
方可自動選取，否則拒絕，避免讀錯 run）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CONT_DIR = Path(__file__).resolve().parent
TRANSIENT = CONT_DIR / "transient"
TEMPLATE_PATH = REPO_ROOT / "verification" / "sa-open-fire-1" / "request.template.json"
CONT1_EV = REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-evidence"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.soul_access import evidence as sa_evidence  # noqa: E402

#: 與 drill.py 同源的釘選值（讀回側獨立重述；任一漂移即 FAIL）。
PIN_TEMPLATE_INNER_SHA = "418af81353a2457d0f3fae7459c6931e6fadb16afcbb41261cc9916d1915eeb1"
PIN_PAYLOAD_SHA = "7043a3ffb0ce7a170c690feae6611a0231418ecb37996ca2978fa2474e81475c"
PIN_RAW_SHA = "8860307705e5bde75d4e9c2b2395fd1c4c8aa325f46334a19fec35f2eb0b25dd"
PIN_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"

EXPECTED_CASES = (
    ("connect", "ConnectError", "connect"),
    ("connect-timeout", "ConnectTimeout", "connect_timeout"),
    ("read-timeout", "ReadTimeout", "read_timeout"),
    ("write-timeout", "WriteTimeout", "write_timeout"),
    ("pool-timeout", "PoolTimeout", "pool_timeout"),
    ("unknown", "TimeoutException", "unknown"),
)

_TYPE_NAME_RE = None


def _fail(msg: str) -> int:
    print("readback FAIL: %s" % msg)
    return 1


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_evidence_pair(evidence_dir: Path):
    """成功判準：evidence 內恰有同 ID 的 request／response 兩個一般檔且 pair_ok。

    只掃 ``evidence_dir`` 本級（兄弟 transport／其他目錄不計入）；``.tmp-*``
    殘留不計入。回傳 ``(ok, detail)``。
    """
    names = sorted(
        p.name for p in Path(evidence_dir).iterdir() if not p.name.startswith(".tmp-")
    )
    if len(names) != 2:
        return (False, "count!=2: %s" % (names,))
    stem = names[0].split(".request.json")[0]
    if names != ["%s.request.json" % stem, "%s.response.json" % stem]:
        return (False, "stem/pair mismatch: %s" % (names,))
    try:
        report = sa_evidence.EvidenceSink(evidence_dir).verify_pair(stem)
    except Exception as exc:
        return (False, "verify raised %s" % type(exc).__name__)
    if report.get("pair_ok") is not True:
        return (False, "pair_ok false: %s" % (report,))
    return (True, stem)


def _check_attempt(attempt: dict, exc_class: str, label: str):
    if not isinstance(attempt, dict):
        return "attempt not a dict"
    if set(attempt.keys()) != {"seq", "type", "chain", "errno", "elapsed_ms", "phase"}:
        return "attempt keys drift: %s" % sorted(attempt.keys())
    if attempt["seq"] != 0:
        return "seq!=0"
    if attempt["type"] != label:
        return "type %r != %r (no guessing from text)" % (attempt["type"], label)
    chain = attempt["chain"]
    if not isinstance(chain, list) or not chain or chain[0] != exc_class:
        return "chain head %r != %r" % (chain, exc_class)
    errno = attempt["errno"]
    if errno is not None and (isinstance(errno, bool) or not isinstance(errno, int)):
        return "errno not int-or-None"
    elapsed = attempt["elapsed_ms"]
    if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)):
        return "elapsed_ms not numeric"
    if not (elapsed >= 0):
        return "elapsed_ms negative"
    if attempt["phase"] != "post":
        return "phase %r != post" % (attempt["phase"],)
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="continuation-2 independent readback")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    if args.run_id:
        rundir = TRANSIENT / args.run_id
        if not rundir.is_dir():
            return _fail("run dir missing: %s" % rundir)
    else:
        runs = sorted(p for p in TRANSIENT.iterdir() if p.is_dir()) if TRANSIENT.is_dir() else []
        if len(runs) != 1:
            return _fail("ambiguous run selection (n=%d); pass --run-id" % len(runs))
        rundir = runs[0]
    run_id = rundir.name

    report = json.loads((rundir / "drill-report.json").read_text(encoding="utf-8"))
    if report.get("run_id") != run_id or report.get("ticket") != "SA-ISOLATED-RECOVERY-1":
        return _fail("drill-report run/ticket mismatch")
    checks: dict = {}

    # -- 1. 六案 transport＋evidence --
    old_request_payload: bytes | None = None
    old_raw_bytes: bytes | None = None
    for child in sorted(CONT1_EV.iterdir()):
        if child.name.endswith(".request.json"):
            old_request_payload = sa_evidence.canonical_bytes(
                json.loads(child.read_text(encoding="utf-8"))["payload"]
            )
        elif child.name.endswith(".response.json"):
            old_raw_bytes = json.loads(child.read_text(encoding="utf-8"))["raw_text"].encode("utf-8")
    if old_request_payload is None or old_raw_bytes is None:
        return _fail("old continuation-1 evidence unreadable")

    for short, exc_class, label in EXPECTED_CASES:
        tag = "case:%s" % short
        case_dir = rundir / "cases" / short
        dialog_id = "req-sa-open-fire-1-c2-%s" % short
        tr_path = case_dir / "transport" / ("%s.json" % dialog_id)
        try:
            stored = json.loads(tr_path.read_text(encoding="utf-8"))
        except OSError:
            return _fail("%s transport file missing" % tag)
        if stored.get("kind") != "sa-transport-diagnosis":
            return _fail("%s kind drift" % tag)
        if stored.get("dialog_request_id") != dialog_id:
            return _fail("%s dialog_request_id mismatch" % tag)
        ev_id = stored.get("evidence_request_id")
        if not isinstance(ev_id, str) or not ev_id.startswith("req-"):
            return _fail("%s evidence_request_id unfit" % tag)
        if stored.get("transport_calls") != 1:
            return _fail("%s transport_calls!=1" % tag)
        if stored.get("backend_observations") is not None:
            return _fail("%s backend_observations must stay None" % tag)
        attempts = stored.get("attempts")
        if not isinstance(attempts, list) or len(attempts) != 1:
            return _fail("%s attempts!=1" % tag)
        err = _check_attempt(attempts[0], exc_class, label)
        if err is not None:
            return _fail("%s %s" % (tag, err))
        # 兩種 request_id 關聯：transport 登記的 evidence id 必須對應 evidence 實檔。
        ev_names = sorted(
            p.name for p in (case_dir / "evidence").iterdir() if not p.name.startswith(".tmp-")
        )
        if ev_names != ["%s.request.json" % ev_id]:
            return _fail("%s evidence assoc mismatch: %s" % (tag, ev_names))
        req_obj = json.loads((case_dir / "evidence" / ev_names[0]).read_text(encoding="utf-8"))
        if req_obj.get("request_id") != ev_id:
            return _fail("%s evidence request_id mismatch" % tag)
        if sa_evidence.canonical_bytes(req_obj["payload"]) != old_request_payload:
            return _fail("%s payload canonical bytes drift vs continuation-1" % tag)
        if req_obj.get("payload_sha256") != PIN_PAYLOAD_SHA:
            return _fail("%s payload sha drift" % tag)
        rep_entry = next(
            (c for c in report.get("error_cases", []) if c.get("case") == short), None
        )
        if rep_entry is None or rep_entry.get("evidence_request_id") != ev_id:
            return _fail("%s drill-report association mismatch" % tag)
        checks[tag] = True

    # -- 2. 成功案判準＋釘選 --
    success_ev = rundir / "success" / "evidence"
    ok, detail = check_evidence_pair(success_ev)
    if not ok:
        return _fail("success pair criterion rejected: %s" % (detail,))
    success_ev_id = detail
    if success_ev_id != report["success"]["evidence_request_id"]:
        return _fail("success evidence_request_id assoc mismatch")
    s_request = json.loads((success_ev / ("%s.request.json" % success_ev_id)).read_text(encoding="utf-8"))
    s_response = json.loads((success_ev / ("%s.response.json" % success_ev_id)).read_text(encoding="utf-8"))
    if s_request.get("payload_sha256") != PIN_PAYLOAD_SHA:
        return _fail("success payload sha drift")
    if sa_evidence.canonical_bytes(s_request["payload"]) != old_request_payload:
        return _fail("success payload canonical bytes drift vs continuation-1")
    if s_response.get("raw_sha256") != PIN_RAW_SHA:
        return _fail("success raw sha drift")
    if s_response.get("raw_text", "").encode("utf-8") != old_raw_bytes:
        return _fail("success raw bytes drift vs continuation-1")
    if dict(s_response.get("usage") or {}) != dict(PIN_USAGE):
        return _fail("success usage drift")
    checks["success:pair+assoc+pins"] = True

    # -- 2b. 反例：判準必須不誤放行 --
    cex_base = rundir / "counterexamples"
    if cex_base.exists():
        return _fail("counterexamples dir already exists (no overwrite)")
    cex_base.mkdir(parents=True, exist_ok=False)
    extra_dir = cex_base / "extra-file"
    extra_dir.mkdir()
    for name in ("%s.request.json" % success_ev_id, "%s.response.json" % success_ev_id):
        (extra_dir / name).write_bytes((success_ev / name).read_bytes())
    (extra_dir / "junk.txt").write_text("extra", encoding="utf-8")
    ok_extra, _ = check_evidence_pair(extra_dir)
    if ok_extra:
        return _fail("criterion false-passed with extra file")
    missing_dir = cex_base / "missing-response"
    missing_dir.mkdir()
    (missing_dir / ("%s.request.json" % success_ev_id)).write_bytes(
        (success_ev / ("%s.request.json" % success_ev_id)).read_bytes()
    )
    ok_missing, _ = check_evidence_pair(missing_dir)
    if ok_missing:
        return _fail("criterion false-passed with missing response")
    checks["counterexamples:extra+missing-rejected"] = True

    # -- 3. 模板＋舊檔 SHA --
    replayed = (rundir / "request.replayable.json").read_bytes()
    if replayed != TEMPLATE_PATH.read_bytes():
        return _fail("template replay bytes drift")
    replayed_obj = json.loads(replayed.decode("utf-8-sig"))
    if replayed_obj.get("sha256") != PIN_TEMPLATE_INNER_SHA:
        return _fail("template inner sha drift")
    if report["template"]["file_sha256"] != _sha_bytes(TEMPLATE_PATH.read_bytes()):
        return _fail("template file sha record mismatch")
    for rel, before in report.get("old_shas_before", {}).items():
        now = _sha_bytes((REPO_ROOT / rel).read_bytes())
        if now != before:
            return _fail("old file changed during drill: %s" % rel)
    checks["template+old-shas-unchanged"] = True

    # -- 4. 脫密掃描 --
    #     正式傳輸觀測 schema 本身不含 key_ref 欄位（白名單外欄位寫入即拒；
    #     credential 形態文字在 serialize 期即拒），故 transport/ 下檔案只做
    #     否定掃描；其餘落檔（evidence）必須帶 key_ref 且不得帶明文。
    for sub in ("cases", "success"):
        base = rundir / sub
        for path in sorted(base.rglob("*.json")):
            if ".tmp-" in path.name:
                continue
            text = path.read_text(encoding="utf-8")
            if SENTINEL_API_KEY in text:
                return _fail("secret present in %s" % path.relative_to(rundir))
            if "Bearer " in text:
                return _fail("credential-shaped text in %s" % path.relative_to(rundir))
            if "transport" in path.relative_to(rundir).parts:
                continue
            if sa_evidence.KEY_REF not in text:
                return _fail("key_ref missing in %s" % path.relative_to(rundir))
    checks["redaction:key_ref-only"] = True

    # -- 5. 薄端復閉（新行程獨立再驗；絕不開門） --
    import scripts.serve_sa_thin as thin_mod  # noqa: E402
    from src.soul_access import api as sa_api  # noqa: E402
    from src.soul_access import schema as sa_schema  # noqa: E402

    if thin_mod.is_thin_open() is not False:
        return _fail("thin instance door not closed in fresh process")
    for gate_name in ("gate-open.json", "gate-closed.json"):
        gate_obj = json.loads((rundir / gate_name).read_text(encoding="utf-8"))
        if gate_obj != {"status": "closed"}:
            return _fail("sandbox gate not re-closed: %s" % gate_name)

    class _ZeroSender:
        def __init__(self):
            self.calls = []

        def __call__(self, url, body):
            self.calls.append((url, body))
            raise AssertionError("reclose must not transmit")

    from src.soul_access import wiring as _wiring  # noqa: E402

    # 與 drill 行程一致的合成 preset key（非密文；正式 preset 解析要求
    # 該 env 變數存在，否則在門前即 500；此處只為重建同一正式 assembly）。
    os.environ["SA_C2_THIN_KEY"] = "c2-sandbox-fake-key"
    forwarder = _wiring.build_thin_forwarder(
        release_path=str(rundir / "gate-closed.json"),
        target_base="http://c2-thin.invalid",
        sender=_ZeroSender(),
    )
    app = thin_mod.build_thin_app(
        str(rundir / "config" / "registry.json"),
        str(rundir / "config" / "presets.json"),
        release_path=str(rundir / "gate-closed.json"),
        _forwarder=forwarder,
    )
    import httpx as _httpx  # noqa: E402

    async def _post_closed():
        client = _httpx.AsyncClient(
            transport=_httpx.ASGITransport(app=app), base_url="http://test"
        )
        try:
            envelope = sa_api.build_envelope(
                snapshot=sa_schema.SoulSnapshot(
                    identity=sa_schema.RequestIdentity(
                        request_id="req-c2-readback-1",
                        conversation_id="dm",
                        branch_id="root",
                        turn_id="turn-req-c2-readback-1",
                        idempotency_key="key-c2-readback-1",
                    ),
                    dedup=sa_schema.DedupKey(
                        principal_id="c2-readback-owner",
                        agent_id="agent_rem",
                        scope="owner_private",
                        conversation_id="dm",
                        branch_id="root",
                        idempotency_key="key-c2-readback-1",
                    ),
                    backend_alias="primary",
                    model="sa-c2-primary",
                    params=(("temperature", "0.5"), ("max_tokens", "8")),
                    budget_tokens=512,
                    deadline_utc="2026-11-15T00:00:00Z",
                    personality_version="personality-c2-v1",
                    emotion_state="calm",
                    relationship_state="trusted",
                    chrono_state="2026-11-15T15:45:00-05:00",
                    inner_life_state="inner-c2-v1",
                    projection_state="projection-c2-v1",
                    recall_version="recall-c2-v1",
                    recall_items=("recall-c2-item-1",),
                    history_pool_version="pool-c2-v1",
                    clean_history=("hist-c2-item-1",),
                ),
                messages=(("user", "你好。"),),
                created_utc="2026-11-15T00:00:00Z",
            )
            import hashlib as _hashlib

            token = json.loads(
                (rundir / "config" / "registry.json").read_text(encoding="utf-8")
            )["clients"][0]["credential_sha256"]
            _ = token  # digest only; the sandbox token itself is never stored
            from src.interfaces.credentials import digest_of as _digest_of

            _ = _digest_of
            return await client.post(
                "/rem/primary/v1/chat/completions",
                headers={"Authorization": "Bearer c2-thin-sandbox-token"},
                json=sa_api.envelope_to_body(envelope),
            )
        finally:
            await client.aclose()

    import asyncio as _asyncio

    response = _asyncio.run(_post_closed())
    if response.status_code != 403:
        return _fail("reclose readback status=%s (want 403)" % response.status_code)
    if len(forwarder.__closure__ or []) == -1:  # unreachable; keep structure explicit
        return _fail("unreachable")
    checks["thin:reclose-403-zero-transmit"] = True

    (rundir / "readback-report.json").write_text(
        json.dumps(
            {
                "ticket": "SA-ISOLATED-RECOVERY-1",
                "readback": "independent-cross-process-readback",
                "run_id": run_id,
                "checks": checks,
                "overall": "PASS",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    print(
        "readback PASS: run=%s cases=6 pair=%s old=%d reclose=403 redacted=True"
        % (run_id, success_ev_id[:16], len(report.get("old_shas_before", {})))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
