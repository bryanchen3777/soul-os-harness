"""SA-ISOLATED-RECOVERY-1 continuation-2 drill（隔離演練，零外連、零計費）。

承接 1d250f8（continuation-1 ISOLATED-PASS 的正式鏈路），以零外連、零計費
串起正式處理鏈及新觀測讀回。驗收只代表離線流程通過；B1/B2 維持
INCONCLUSIVE，不宣告實彈成功或網路恢復。

正式程式路徑（本票唯讀沿用，產品碼零修改）：
  1. ``src.soul_access.wiring.build_dialog_factory(..., evidence_sink=sink)``
     逐請求顯式透傳；
  2. ``src.llm.proxy.OpenAIBackend.complete(..., evidence_sink=sink)``
     取證模式固定零重試；
  3. ``src.soul_access.evidence.record_transport_diagnosis`` 正式落檔；
  4. ``src.soul_access.wiring.build_thin_forwarder`` 正式轉送 assembly；
  5. ``scripts/serve_sa_thin.build_thin_app`` 正式薄端 assembly。

結構性截斷（本票零真實外呼的構造保證）：
  - 每次自建 ``continuation-2/transient/<run_id>/``，禁止沿用或覆寫舊輸出；
    門檔、合成配置及控制檔全部置於其中；已存在同名 run_id 即拒絕啟動。
  - 先裝執行期 socket 硬攔（本行程 ``socket.socket/create_connection/
    create_server`` 一律 raise），再注入假 HTTP transport。
  - 憑證只用隔離 sentinel（非可用生產憑證）；目標固定 ``.invalid``；
    不載入生產憑證、不讀 ``.env``、不寫 ``data/**``。
  - 薄端成功路徑的底層 I/O 只換成計數合成 sender（回固定合成文字）；
    合成 live 物件不存在——薄端本來就不持有 live／記憶物件，不接生產記憶。
  - 不啟服務、不綁任何埠（薄端走行程內 ASGI loopback，不啟 listener）。

輸出（全部位於自建 run 目錄）：
  - ``cases/<case>/{evidence,transport}/``：六案逐案觸發 ConnectError /
    ConnectTimeout / ReadTimeout / WriteTimeout / PoolTimeout / unknown，
    每案獨立目錄、恰一次假傳輸；失敗案 evidence 只留 request、不補 response。
  - ``success/evidence/``：成功案恰有同 ID 的 request／response 兩個一般檔。
  - ``request.replayable.json``：模板原 bytes 複製。
  - ``drill-report.json``：計數、雜湊、門禁與舊檔 SHA 快照（讀回比對用）。

本行程 stdout 只印計數與雜湊，不印 raw 全文（raw 只落取證檔）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json as _jsonlib
import os
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx

REPO_ROOT = Path(__file__).resolve().parents[3]
CONT_DIR = Path(__file__).resolve().parent
TRANSIENT = CONT_DIR / "transient"
TEMPLATE_PATH = REPO_ROOT / "verification" / "sa-open-fire-1" / "request.template.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.external_gateway.app import create_external_app  # noqa: E402
from src.external_gateway.backends import PresetRoutingBackend  # noqa: E402
from src.interfaces.credentials import digest_of  # noqa: E402
from src.interfaces.external_auth import RegistryProvider  # noqa: E402
from src.llm.proxy import OpenAIBackend  # noqa: E402
from src.soul_access import api as sa_api  # noqa: E402
from src.soul_access import bridge as sa_bridge  # noqa: E402
from src.soul_access import evidence as sa_evidence  # noqa: E402
from src.soul_access import schema as sa_schema  # noqa: E402
from src.soul_access import wiring  # noqa: E402

import scripts.serve_sa_thin as thin_mod  # noqa: E402

#: 不可路由上游（結構性截斷；假 transport 另行斷言此後綴）。
NON_ROUTABLE_BASE = "http://sa-isolated-recovery-1-invalid.invalid/v1/chat/completions"
#: 隔離 sentinel：佔位字串，不是可用生產憑證；取證檔內不得出現。
SENTINEL_API_KEY = "SYNTHETIC-ISOLATION-NO-REAL-CREDENTIAL"
#: 原票固定路由 rem/primary 的釘選值（唯讀引用）。
MODEL = "deepseek-v4.1-flash:1017"
SOUL_ALIAS = "rem"
BACKEND_ALIAS = "primary"
AGENT = "agent_rem"
MESSAGE = "你好。"
TEMPERATURE = 0.5
MAX_TOKENS = 8
SUCCESS_DIALOG_ID = "req-sa-open-fire-1-0001"
SYNTHETIC_MARK = "合成資料（隔離演練，非實彈）"
THIN_SUCCESS_TEXT = "合成回覆：薄端隔離成功一次。"
#: 釘選摘要（唯讀比對；沿用 continuation-1 實測值）。
PIN_TEMPLATE_INNER_SHA = "418af81353a2457d0f3fae7459c6931e6fadb16afcbb41261cc9916d1915eeb1"
PIN_PAYLOAD_SHA = "7043a3ffb0ce7a170c690feae6611a0231418ecb37996ca2978fa2474e81475c"
PIN_RAW_SHA = "8860307705e5bde75d4e9c2b2395fd1c4c8aa325f46334a19fec35f2eb0b25dd"
PIN_USAGE = {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}

#: 六案：短名／觸發異常類／預期觀測 type 標籤。
ERROR_CASES = (
    ("connect", "ConnectError", httpx.ConnectError, "connect"),
    ("connect-timeout", "ConnectTimeout", httpx.ConnectTimeout, "connect_timeout"),
    ("read-timeout", "ReadTimeout", httpx.ReadTimeout, "read_timeout"),
    ("write-timeout", "WriteTimeout", httpx.WriteTimeout, "write_timeout"),
    ("pool-timeout", "PoolTimeout", httpx.PoolTimeout, "pool_timeout"),
    ("unknown", "TimeoutException", httpx.TimeoutException, "unknown"),
)

#: 薄端合成身分（從不使用生產身分／生產記憶）。
THIN_OWNER = "c2-isolated-thin-owner"
THIN_TOKEN = "c2-thin-sandbox-token"
THIN_KEY_REF = "SA_C2_THIN_KEY"
THIN_FAKE_KEY = "c2-sandbox-fake-key"
THIN_BACKEND = "c2-thin"
THIN_PRIMARY_MODEL = "sa-c2-primary"
THIN_STANDBY_MODEL = "sa-c2-standby"

#: 舊證據＋雙 sent：演練前後 SHA 必須不變的唯讀檔案。
OLD_WATCH_PATHS = (
    REPO_ROOT / "verification" / "sa-open-fire-1" / "request.template.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-gate-open.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-gate-closed.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "fire-report.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "crosscheck-report.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "negative-report.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "preflight-continuation-1.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "STATUS-continuation-1.md",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-1" / "sent.json",
    REPO_ROOT / "verification" / "sa-open-fire-1" / "live-fire-2" / "sent.json",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _snapshot_old_shas() -> dict:
    """唯讀擷取舊證據＋雙 sent 的 SHA（前快照；讀回比對後快照）。"""
    out = {}
    for path in OLD_WATCH_PATHS:
        out[str(path.relative_to(REPO_ROOT))] = _sha_file(path)
    for sub in ("live-fire-1/evidence", "live-fire-2/evidence"):
        evdir = REPO_ROOT / "verification" / "sa-open-fire-1" / sub
        for child in sorted(evdir.iterdir()):
            if child.is_file() and not child.name.startswith(".tmp-"):
                out[str(child.relative_to(REPO_ROOT))] = _sha_file(child)
    cont_ev = (
        REPO_ROOT / "verification" / "sa-open-fire-1" / "continuation-1" / "sandbox-evidence"
    )
    for child in sorted(cont_ev.iterdir()):
        if child.is_file() and not child.name.startswith(".tmp-"):
            out[str(child.relative_to(REPO_ROOT))] = _sha_file(child)
    return out


def _synthetic_body() -> dict:
    """與 drill-1 位元一致的合成回包（raw／usage 釘選值即源於此）。"""
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


_BEHAVIOR = {"kind": "unset", "exc": None}


class _FakeClient:
    """假 transport：只認 .invalid 上游；行為由當案設定（抛錯或回合成包）。"""

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
        if _BEHAVIOR["kind"] == "raise":
            raise _BEHAVIOR["exc"]
        if _BEHAVIOR["kind"] == "ok":
            body = _synthetic_body()
            return _FakeResp(_jsonlib.dumps(body, ensure_ascii=False, sort_keys=True), body)
        raise RuntimeError("isolation breach: fake behavior unset")


def _run(coro):
    """無事件循環協程泵（同 drill-1：假 await 物皆即時完成，不碰真實 I/O）。"""
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
                raise AssertionError("drill: fake suspended on %r" % (yielded,))
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


def _dialog_snapshot(principal: str) -> sa_schema.SoulSnapshot:
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=SUCCESS_DIALOG_ID,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-c2-1",
            idempotency_key="key-c2-1",
        ),
        dedup=sa_schema.DedupKey(
            principal_id=principal,
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key="key-c2-1",
        ),
        backend_alias=BACKEND_ALIAS,
        model=MODEL,
        params=(("temperature", str(TEMPERATURE)), ("max_tokens", str(MAX_TOKENS))),
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
    )


def _install_socket_block() -> None:
    """執行期 socket 硬攔（先於假 transport 注入；觸達即 raise）。

    ``socket.socket`` 以「建構即 raise 的子類」替換（而非普通函式），
    使 loop 機制內的 ``isinstance(conn, socket.socket)`` 仍合法；
    loop 本體必須在此之前建好（見 ``main``）。
    """

    def _blocked(*args, **kwargs):
        raise RuntimeError("c2 drill: network socket hard-blocked at runtime")

    class _BlockedSocket(_REAL_SOCKET):
        def __init__(self, *args, **kwargs):
            raise RuntimeError("c2 drill: network socket hard-blocked at runtime")

    socket.socket = _BlockedSocket
    socket.create_connection = _blocked
    socket.create_server = _blocked


_REAL_SOCKET = socket.socket


class _CountingSender:
    """薄端正式轉送底層 I/O 替身：只計數、回固定合成文字，從不外呼。"""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body):
        self.calls.append({"url": url, "body": body})
        return THIN_SUCCESS_TEXT


class _FakeListener:
    def __init__(self):
        self.closed = 0

    def close(self):
        self.closed += 1


class _FakeClock:
    def __init__(self):
        self.t = 2000.0

    def __call__(self):
        return self.t


def _thin_snapshot() -> sa_schema.SoulSnapshot:
    req = "req-c2-thin-0001"
    key = "key-c2-thin-0001"
    return sa_schema.SoulSnapshot(
        identity=sa_schema.RequestIdentity(
            request_id=req,
            conversation_id="dm",
            branch_id="root",
            turn_id="turn-%s" % req,
            idempotency_key=key,
        ),
        dedup=sa_schema.DedupKey(
            principal_id=THIN_OWNER,
            agent_id=AGENT,
            scope="owner_private",
            conversation_id="dm",
            branch_id="root",
            idempotency_key=key,
        ),
        backend_alias=BACKEND_ALIAS,
        model=THIN_PRIMARY_MODEL,
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
    )


def _thin_sandbox_files(rundir: Path):
    """合成配置（registry／presets）＋門檔：全部置於自建 run 目錄。"""
    cfg = rundir / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    registry_path = cfg / "registry.json"
    presets_path = cfg / "presets.json"
    gate_open = rundir / "gate-open.json"
    gate_closed = rundir / "gate-closed.json"
    registry_path.write_text(
        _jsonlib.dumps(
            {
                "owner_principal_id": THIN_OWNER,
                "backends": [THIN_BACKEND],
                "routes": [
                    {
                        "soul_alias": SOUL_ALIAS,
                        "backend_alias": BACKEND_ALIAS,
                        "agent_id": AGENT,
                        "public_model_id": "sa-c2-%s-%s" % (SOUL_ALIAS, BACKEND_ALIAS),
                        "backend_ref": THIN_BACKEND,
                        "context_window_tokens": 4096,
                    }
                ],
                "clients": [
                    {
                        "client_id": "client-c2-thin",
                        "principal_id": THIN_OWNER,
                        "purpose": "interactive",
                        "allowed_routes": ["%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS)],
                        "scope": "owner_private",
                        "allow_persistent_memory": True,
                        "credential_sha256": digest_of(THIN_TOKEN),
                    }
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    presets_path.write_text(
        _jsonlib.dumps(
            {
                "presets": [
                    {
                        "name": BACKEND_ALIAS,
                        "provider": "openai",
                        "model": THIN_PRIMARY_MODEL,
                        "params": {
                            "temperature": 0.5,
                            "max_tokens": 8,
                            "base_url": "https://c2-thin.invalid/v1/chat/completions",
                        },
                        "key_ref": THIN_KEY_REF,
                    },
                    {
                        "name": "standby",
                        "provider": "openai",
                        "model": THIN_STANDBY_MODEL,
                        "params": {
                            "temperature": 0.5,
                            "max_tokens": 8,
                            "base_url": "https://c2-thin.invalid/v1/chat/completions",
                        },
                        "key_ref": THIN_KEY_REF,
                    },
                ]
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    gate_open.write_text(_jsonlib.dumps({"status": "open"}), encoding="utf-8")
    gate_closed.write_text(_jsonlib.dumps({"status": "closed"}), encoding="utf-8")
    return (registry_path, presets_path, gate_open, gate_closed)


def _asgi_post(loop, app, body):
    """行程內 ASGI loopback 請求（不啟 listener、不綁埠）。

    ``loop`` 必須在執行期 socket 硬攔安裝之前建好（Windows Proactor
    建 loop 需 socketpair；建好後 ASGI 傳輸本身不再開 socket）。
    """

    async def _go():
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        try:
            return await client.post(
                "/%s/%s/v1/chat/completions" % (SOUL_ALIAS, BACKEND_ALIAS),
                headers={"Authorization": "Bearer " + THIN_TOKEN},
                json=body,
            )
        finally:
            await client.aclose()

    return loop.run_until_complete(_go())


def main() -> int:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rundir = TRANSIENT / run_id
    if rundir.exists():
        print("drill REFUSED: run dir exists (no overwrite): %s" % rundir)
        return 4
    rundir.mkdir(parents=True, exist_ok=False)

    old_shas_before = _snapshot_old_shas()

    # -- 模板原 bytes 複製（不變）＋內嵌摘要＋實際檔案 SHA --
    template_raw = TEMPLATE_PATH.read_bytes()
    template = _jsonlib.loads(template_raw.decode("utf-8-sig"))
    assert template.get("request_id") == SUCCESS_DIALOG_ID, "template request_id drift"
    assert template.get("sha256") == PIN_TEMPLATE_INNER_SHA, "template inner sha drift"
    (rundir / "request.replayable.json").write_bytes(template_raw)
    template_file_sha = _sha_file(TEMPLATE_PATH)

    # -- 先建好薄端 loop，再裝執行期 socket 硬攔，最後注入假 HTTP transport --
    thin_loop = asyncio.new_event_loop()
    _install_socket_block()
    calls: list = []

    def _factory(*args, **kwargs):
        return _FakeClient(calls)

    _real_async_client = httpx.AsyncClient
    httpx.AsyncClient = _factory

    llm = SimpleNamespace(backend=OpenAIBackend(api_key=SENTINEL_API_KEY, base_url=NON_ROUTABLE_BASE))

    # -- 六案：正式 dialog→backend→觀測路徑，每案獨立目錄、恰一次假傳輸 --
    case_reports = []
    for short, cls_name, cls, label in ERROR_CASES:
        case_dir = rundir / "cases" / short
        ev_dir = case_dir / "evidence"
        tr_dir = case_dir / "transport"
        dialog_id = "req-sa-open-fire-1-c2-%s" % short
        calls.clear()
        _BEHAVIOR["kind"] = "raise"
        _BEHAVIOR["exc"] = cls("c2 simulated %s" % cls_name)
        sink = sa_evidence.EvidenceSink(ev_dir)
        factory = wiring.build_dialog_factory(
            llm=llm, loop=object(), submit=_submit, evidence_sink=sink
        )
        dialog = factory(_dialog_snapshot("continuation-2-offline-owner"))
        try:
            dialog((("user", MESSAGE),), agent_id=AGENT, request_id=dialog_id)
            raise AssertionError("case %s: unexpectedly admitted" % short)
        except sa_bridge.LiveRefused as refused:
            obs = sa_evidence.find_transport_obs(refused)
            assert obs is not None, "case %s: no transport snapshot" % short
            carried = dict(obs)
            carried["dialog_request_id"] = dialog_id
            landed = sa_evidence.record_transport_diagnosis(
                directory=tr_dir,
                dialog_request_id=dialog_id,
                diagnosis=carried,
            )
        assert len(calls) == 1, "case %s: exactly one transport, got %d" % (short, len(calls))
        assert obs["transport_calls"] == 1 == len(calls)
        ev_names = sorted(
            p.name for p in sink.directory.iterdir() if not p.name.startswith(".tmp-")
        )
        # 失敗案只留 request，不補 response。
        assert len(ev_names) == 1 and ev_names[0].endswith(".request.json"), ev_names
        request_obj = _jsonlib.loads((sink.directory / ev_names[0]).read_text(encoding="utf-8"))
        assert request_obj["payload_sha256"] == PIN_PAYLOAD_SHA, "case %s payload drift" % short
        case_reports.append(
            {
                "case": short,
                "exc_class": cls_name,
                "type": label,
                "dialog_request_id": dialog_id,
                "evidence_request_id": obs["evidence_request_id"],
                "transport_calls": obs["transport_calls"],
                "transport_file": str(landed.relative_to(rundir)),
                "evidence_request_file": ev_names[0],
            }
        )

    # -- 成功案：evidence 恰有同 ID 的 request／response 兩個一般檔 --
    success_dir = rundir / "success"
    calls.clear()
    _BEHAVIOR["kind"] = "ok"
    _BEHAVIOR["exc"] = None
    success_sink = sa_evidence.EvidenceSink(success_dir / "evidence")
    factory = wiring.build_dialog_factory(
        llm=llm, loop=object(), submit=_submit, evidence_sink=success_sink
    )
    text = factory(_dialog_snapshot("continuation-2-offline-owner"))(
        (("user", MESSAGE),), agent_id=AGENT, request_id=SUCCESS_DIALOG_ID
    )
    assert text == "合成回覆：隔離演練單發成功。"
    assert len(calls) == 1, "success: exactly one transport, got %d" % len(calls)
    s_names = sorted(
        p.name for p in success_sink.directory.iterdir() if not p.name.startswith(".tmp-")
    )
    assert len(s_names) == 2, "success evidence must be exactly 2 files, got %s" % (s_names,)
    success_ev_id = s_names[0].split(".request.json")[0]
    assert s_names == ["%s.request.json" % success_ev_id, "%s.response.json" % success_ev_id], s_names
    s_request = _jsonlib.loads((success_sink.directory / s_names[0]).read_text(encoding="utf-8"))
    s_response = _jsonlib.loads((success_sink.directory / s_names[1]).read_text(encoding="utf-8"))
    assert s_request["payload_sha256"] == PIN_PAYLOAD_SHA, "success payload drift"
    assert s_response["usage"] == dict(PIN_USAGE)
    assert SYNTHETIC_MARK in s_response["raw_text"]
    for name in s_names:
        content = (success_sink.directory / name).read_text(encoding="utf-8")
        assert sa_evidence.KEY_REF in content
        assert SENTINEL_API_KEY not in content, "secret leaked into evidence"
        assert "Bearer " not in content, "credential-shaped text in evidence"
    assert success_sink.verify_pair(success_ev_id)["pair_ok"] is True

    # -- 薄端：恢復真實 httpx（ASGI loopback 不走網路），socket 硬攔保留 --
    httpx.AsyncClient = _real_async_client
    registry_path, presets_path, gate_open, gate_closed = _thin_sandbox_files(rundir)
    os.environ[THIN_KEY_REF] = THIN_FAKE_KEY
    thin_open_before = thin_mod.is_thin_open()
    assert thin_open_before is False
    sender = _CountingSender()
    formal_forwarder = wiring.build_thin_forwarder(
        release_path=str(gate_open),
        target_base="http://c2-thin.invalid",
        sender=sender,
    )
    envelope = sa_api.build_envelope(
        snapshot=_thin_snapshot(),
        messages=(("user", MESSAGE),),
        created_utc="2026-11-15T00:00:00Z",
    )
    thin_body = sa_api.envelope_to_body(envelope)
    thin_report: dict = {}
    try:
        # G1：僅實例門閉（沙盒門=open 亦攔截）。
        app_g1 = thin_mod.build_thin_app(
            str(registry_path), str(presets_path),
            release_path=str(gate_open), _forwarder=formal_forwarder,
        )
        r_g1 = _asgi_post(thin_loop, app_g1, thin_body)
        thin_report["door1_only_closed"] = {
            "status": r_g1.status_code, "sender_calls": len(sender.calls)
        }
        assert r_g1.status_code == 403 and len(sender.calls) == 0

        # G2：僅共享門閉（暫開實例門；真實釋放檢查路徑 403）。
        thin_mod._THIN_OPEN = True
        gate2_forwarder = wiring.build_thin_forwarder(
            release_path=str(gate_closed),
            target_base="http://c2-thin.invalid",
            sender=sender,
        )
        app_g2 = thin_mod.build_thin_app(
            str(registry_path), str(presets_path),
            release_path=str(gate_closed), _forwarder=gate2_forwarder,
        )
        r_g2 = _asgi_post(thin_loop, app_g2, thin_body)
        thin_report["door2_only_closed"] = {
            "status": r_g2.status_code, "sender_calls": len(sender.calls)
        }
        assert r_g2.status_code == 403 and len(sender.calls) == 0

        # S：雙門暫開，成功一次。
        gate_open.write_text(_jsonlib.dumps({"status": "open"}), encoding="utf-8")
        app_ok = thin_mod.build_thin_app(
            str(registry_path), str(presets_path),
            release_path=str(gate_open), _forwarder=formal_forwarder,
        )
        r_ok = _asgi_post(thin_loop, app_ok, thin_body)
        ok_json = r_ok.json()
        thin_report["both_open_success"] = {
            "status": r_ok.status_code, "sender_calls": len(sender.calls)
        }
        assert r_ok.status_code == 200 and len(sender.calls) == 1
        assert ok_json["choices"][0]["message"]["content"] == THIN_SUCCESS_TEXT

        # R：雙門復閉再驗。
        thin_mod._THIN_OPEN = False
        gate_open.write_text(_jsonlib.dumps({"status": "closed"}), encoding="utf-8")
        app_re = thin_mod.build_thin_app(
            str(registry_path), str(presets_path),
            release_path=str(gate_open), _forwarder=formal_forwarder,
        )
        r_re = _asgi_post(thin_loop, app_re, thin_body)
        thin_report["both_closed_reverified"] = {
            "status": r_re.status_code, "sender_calls": len(sender.calls)
        }
        assert r_re.status_code == 403 and len(sender.calls) == 1
    finally:
        thin_mod._THIN_OPEN = False
        try:
            gate_open.write_text(_jsonlib.dumps({"status": "closed"}), encoding="utf-8")
        except Exception:
            pass
        os.environ.pop(THIN_KEY_REF, None)
    thin_report["thin_open_restored_false"] = thin_mod.is_thin_open() is False
    assert thin_report["thin_open_restored_false"] is True

    # -- T15 假 listener：優雅關停（純邏輯，不碰真實埠／行程） --
    listener = _FakeListener()
    control = thin_mod.ThinControl(
        run_id="run-%s" % run_id,
        token="c2-control-token",
        control_path=str(rundir / "control.json"),
        clock=_FakeClock(),
        drain_timeout_s=5.0,
        listener=listener,
        sleeper=lambda s: None,
    )
    control.write_control_file()
    c_status, c_payload = control.handle_post(
        "127.0.0.1", {"token": "c2-control-token", "command": "shutdown"}
    )
    control_report = {
        "reply_status": c_status,
        "reply_state": c_payload.get("state"),
        "final_state": control.state,
        "listener_closed": listener.closed,
        "result_code": control.result_code,
        "run_id_consistent": all(ev["run_id"] == "run-%s" % run_id for ev in control.events),
    }
    assert c_status == 200 and c_payload.get("state") == thin_mod.STATE_DRAINING
    assert control.state == thin_mod.STATE_SHUT
    assert listener.closed == 1 and control.result_code == 0
    assert control_report["run_id_consistent"] is True
    thin_loop.close()

    report = {
        "ticket": "SA-ISOLATED-RECOVERY-1",
        "drill": "continuation-2-isolated-recovery",
        "run_id": run_id,
        "base_commit": "1d250f8",
        "python": sys.version.split()[0],
        "route": "%s/%s" % (SOUL_ALIAS, BACKEND_ALIAS),
        "key_ref": sa_evidence.KEY_REF,
        "template": {
            "inner_sha256": PIN_TEMPLATE_INNER_SHA,
            "file_sha256": template_file_sha,
            "bytes_len": len(template_raw),
        },
        "old_shas_before": old_shas_before,
        "error_cases": case_reports,
        "success": {
            "dialog_request_id": SUCCESS_DIALOG_ID,
            "evidence_request_id": success_ev_id,
            "transport_calls": 1,
            "pair_ok": True,
            "payload_sha256": s_request["payload_sha256"],
            "raw_sha256": s_response["raw_sha256"],
            "usage": dict(s_response["usage"]),
        },
        "thin": thin_report,
        "control": control_report,
        "real_billing": 0,
        "synthetic": True,
    }
    (rundir / "drill-report.json").write_text(
        _jsonlib.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        "drill PASS: run=%s cases=6 success_pair=True thin=%s/%s/%s/%s control=shut "
        "payload=%s old_watched=%d real_billing=0"
        % (
            run_id,
            thin_report["door1_only_closed"]["status"],
            thin_report["door2_only_closed"]["status"],
            thin_report["both_open_success"]["status"],
            thin_report["both_closed_reverified"]["status"],
            s_request["payload_sha256"][:16],
            len(old_shas_before),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
