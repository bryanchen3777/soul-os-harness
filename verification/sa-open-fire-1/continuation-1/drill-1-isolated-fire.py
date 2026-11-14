"""SA-OPEN-FIRE-1 續行演練 drill-1：隔離開門單發（合成資料，零真實外呼）。

正式 sender 真實程式路徑（兩層 evidence_sink 皆行經）：
  1. ``src.soul_access.wiring.build_dialog_factory(..., evidence_sink=sink)``
     逐請求顯式透傳（未配置時完全不帶參數，舊行為逐位元一致）。
  2. ``src.llm.proxy.OpenAIBackend.complete(..., evidence_sink=sink)``
     取證模式固定零重試；送前交付實際 provider payload＋雜湊，
     回來時交付同次回應原文及原生 usage。

結構性截斷（本票零真實外呼的構造保證）：
  - 行程內把 ``httpx.AsyncClient`` 整個換成假 transport（執行期硬攔；
    真實 ``httpx`` 建構路徑在本行程不存在）。
  - 假 transport 斷言 URL 含 ``.invalid``，否則拒絕；只回合成 body。
  - ``api_key`` 為隔離 sentinel（非可用生產憑證），``base_url`` 不可路由。
  - 不啟服務、不綁任何埠、不讀生產憑證、不寫 ``data/**``。

合成聲明：回包 raw/usage 為假 transport 合成，內文明標「合成資料」，
不算實彈成果；真實計費為 0。

本行程 stdout 只印計數與雜湊，不印 raw 全文（raw 只落取證檔）。
"""

from __future__ import annotations

import asyncio
import json
import json as _jsonlib
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
CONT_DIR = Path(__file__).resolve().parent
SANDBOX = CONT_DIR / "sandbox-evidence"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

#: 不可路由上游（結構性截斷；假 transport 另行斷言此後綴）。
NON_ROUTABLE_BASE = "http://sa-open-fire-1-invalid.invalid/v1/chat/completions"
#: 隔離 sentinel：佔位字串，不是可用生產憑證；取證檔內不得出現。
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"
#: 原票固定路由 rem/primary 的釘選 preset 值（唯讀引用）。
MODEL = "deepseek-v4.1-flash:1017"
SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
TEMPERATURE = 0.5
MAX_TOKENS = 8
DIALOG_REQUEST_ID = "req-sa-open-fire-1-0001"
SYNTHETIC_MARK = "合成資料（隔離演練，非實彈）"


def _synthetic_body() -> dict:
    return {
        "id": "chatcmpl-synthetic-isolation-0001",
        "object": "chat.completion",
        "created": 1790000000,
        "model": MODEL,
        "synthetic": SYNTHETIC_MARK,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "合成回覆：隔離演練單發成功。"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


class _FakeResp:
    def __init__(self, raw_text, body):
        self._raw_text = raw_text
        self._body = body
        self.status_code = 200

    @property
    def text(self):
        return self._raw_text

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class _FakeClient:
    """假 transport：只認 .invalid 上游，回合成 body，並計數。"""

    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):  # noqa: A002
        if ".invalid" not in str(url):
            raise RuntimeError("isolation breach: non-.invalid upstream refused")
        self._calls.append({"url": url, "headers": dict(headers or {}), "json": json})
        body = _synthetic_body()
        return _FakeResp(_jsonlib.dumps(body, ensure_ascii=False, sort_keys=True), body)


def _run(coro):
    """無事件循環協程泵（同 T14：假 await 物皆即時完成，不碰真實 I/O）。"""
    send_value = None
    throw_exc = None
    started = False
    while True:
        try:
            if throw_exc is not None:
                exc, throw_exc = throw_exc, None
                yielded = coro.throw(exc)
            elif not started:
                started = True
                yielded = coro.send(None)
            else:
                yielded = coro.send(send_value)
        except StopIteration as done:
            return done.value
        try:
            if asyncio.iscoroutine(yielded):
                send_value = _run(yielded)
            else:
                raise AssertionError("drill-1: fake suspended on %r" % (yielded,))
        except BaseException as exc:  # noqa: BLE001
            send_value = None
            throw_exc = exc


class _ImmediateFuture:
    def __init__(self, value):
        self._value = value

    def result(self):
        return self._value


def _submit(loop, coro):
    if asyncio.iscoroutine(coro):
        return _ImmediateFuture(_run(coro))
    return _ImmediateFuture(coro)


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


def main() -> int:
    calls = []

    def _factory(*args, **kwargs):
        return _FakeClient(calls)

    httpx.AsyncClient = _factory  # 行程內硬攔：真實傳輸路徑不存在

    sink = sa_evidence.EvidenceSink(SANDBOX)
    backend = OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE)
    llm = SimpleNamespace(backend=backend)
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_submit, evidence_sink=sink
    )
    dialog = factory(_snapshot())
    text = dialog((("user", MESSAGE),), agent_id=AGENT, request_id=DIALOG_REQUEST_ID)

    expected_text = "合成回覆：隔離演練單發成功。"
    assert text == expected_text, "unexpected dialog text"
    assert len(calls) == 1, "single-fire: exactly one transport attempt, got %d" % len(calls)
    sent_body = calls[0]["json"]

    names = sorted(p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-"))
    assert len(names) == 2, "one request/response pair, got %s" % (names,)
    evidence_request_id = names[0].split(".request.json")[0]
    assert names == [
        "%s.request.json" % evidence_request_id,
        "%s.response.json" % evidence_request_id,
    ]

    request = json.loads((sink.directory / names[0]).read_text(encoding="utf-8"))
    response = json.loads((sink.directory / names[1]).read_text(encoding="utf-8"))

    assert request["payload"] == sent_body, "recorded payload != actual sent body"
    assert request["payload_sha256"] == sa_evidence.sha256_hex(
        sa_evidence.canonical_bytes(sent_body)
    )
    assert SYNTHETIC_MARK in response["raw_text"], "synthetic mark missing from raw"
    assert response["usage"] == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    assert response["request_id"] == evidence_request_id == request["request_id"]
    for name in names:
        content = (sink.directory / name).read_text(encoding="utf-8")
        assert sa_evidence.KEY_REF in content
        assert SENTINEL_API_KEY not in content, "secret leaked into evidence"
    report = sink.verify_pair(evidence_request_id)
    assert report["pair_ok"] is True

    fire_report = {
        "ticket": "SA-OPEN-FIRE-1-continuation-1",
        "drill": "isolated-single-fire",
        "route": "%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS),
        "dialog_request_id": DIALOG_REQUEST_ID,
        "message": MESSAGE,
        "model": MODEL,
        "params": {"temperature": TEMPERATURE, "max_tokens": MAX_TOKENS},
        "key_ref": sa_evidence.KEY_REF,
        "transport_calls": len(calls),
        "evidence_request_id": evidence_request_id,
        "pair_ok": report["pair_ok"],
        "payload_sha256": request["payload_sha256"],
        "raw_sha256": response["raw_sha256"],
        "usage": dict(response["usage"]),
        "synthetic": True,
        "synthetic_mark": SYNTHETIC_MARK,
        "real_billing": 0,
        "secret_present_in_evidence": False,
        "note": "合成資料：假 transport 回包，非實彈成果；零真實外呼、零計費。",
    }
    (CONT_DIR / "fire-report.json").write_text(
        json.dumps(fire_report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "drill-1 PASS: transport_calls=1 pair_ok=True "
        "payload_sha256=%s raw_sha256=%s synthetic=True real_billing=0"
        % (request["payload_sha256"][:16], response["raw_sha256"][:16])
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
