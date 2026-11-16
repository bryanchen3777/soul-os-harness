"""THIN-PUMP-SSE-DISCONNECT-FIX — 薄端泵串流中斷回歸守衛。

背景：``scripts/serve_sa_thin.py`` 的 ``_isolate_business_asgi`` 內，
``_fast_receive`` / ``_mem_receive`` 在緩衝耗盡後捏造
``{"type": "http.disconnect"}``。真 uvicorn 的 ASGI ``spec_version`` 是
2.3，Starlette 對 <2.4 的串流用 task group 並跑 ``stream_response`` 與
``listen_for_disconnect``；假 disconnect 讓後者瞬時返回並取消前者，
``StreamingResponse`` 的 body 被中間截斷（客戶端讀到
IncompleteRead/RemoteProtocolError）。``TestClient`` 預設走 spec 2.4，
從不觸發該路徑，所以歷史測試全綠也蓋不住——本文件一律用真實
uvicorn + 真實 httpx 串流客戶端（禁用 TestClient 做串流成幀斷言）。

傳輸面（全部本地回環、零真實上游）：
  - backend 永遠是注入的假 carrier（純文字回 str；富回包回 BackendReply
    帶 tool_calls＋usage，計數 calls）；
  - uvicorn 綁 ``127.0.0.1``、端口由 OS 自動分配（``port=0`` 回讀實得端口，
    永不碰 8000/8765/8766/8767/18765 等生產端口）；
  - 線程拉起 ``uvicorn.Server``，teardown 置 ``should_exit`` + join，
    不殺進程、不留孤兒；
  - 不讀真實 key（``issue_credential``＋monkeypatch env sentinel）。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest
import uvicorn

import scripts.serve_sa_thin as thin_mod
from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.external_gateway.backends import BackendReply
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider

OWNER = "thin-pump-fix-owner"
REF = "THIN_PUMP_FIX_REF"
SENTINEL = "THIN-PUMP-FIX-SENTINEL-KEY"
FAKE_TEXT = "thin-pump-fix-fake-reply"
TOOL_CALLS = [
    {
        "id": "call_thin_pump_1",
        "type": "function",
        "function": {"name": "get_weather", "arguments": '{"city": "Taipei"}'},
    }
]
USAGE = {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12}

CHAT_PATH = "/rem/primary/v1/chat/completions"


class _FakeBackend:
    """注入的 carrier 替身：計數 calls；plain 回 str，rich 回 BackendReply。"""

    def __init__(self, rich=False):
        self.rich = rich
        self.text = FAKE_TEXT
        self.calls = []

    def complete(self, route, preset, messages, params, passthrough=None):
        self.calls.append((route, preset, messages, params, passthrough))
        if self.rich:
            return BackendReply(text=self.text, tool_calls=TOOL_CALLS, usage=USAGE)
        return self.text


def _raw(token, soul="rem", preset="primary"):
    route = {
        "soul_alias": soul,
        "backend_alias": preset,
        "agent_id": "agent_" + soul,
        "public_model_id": "do-not-deploy-%s-%s" % (soul, preset),
        "backend_ref": "port-a",
        "context_window_tokens": 4096,
    }
    return {
        "owner_principal_id": OWNER,
        "backends": ["port-a"],
        "routes": [route],
        "clients": [
            {
                "client_id": "client-thin-pump-fix",
                "principal_id": OWNER,
                "purpose": "interactive",
                "allowed_routes": ["%s/%s" % (soul, preset)],
                "scope": "owner_private",
                "allow_persistent_memory": True,
                "credential_sha256": digest_of(token),
            }
        ],
    }


def _write_presets(path):
    path.write_text(
        json.dumps(
            {
                "presets": [
                    {
                        "name": "primary",
                        "provider": "deepseek",
                        "model": "do-not-deploy-model-thin-pump-fix",
                        "params": {"temperature": 0.5, "max_tokens": 512},
                        "key_ref": REF,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _build(tmp_path, monkeypatch, rich=False):
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json")
    provider = RegistryProvider(_raw(token))
    backend = _FakeBackend(rich=rich)
    app = create_external_app(provider, file_preset_loader(preset_path), backend)
    return (app, token, backend)


def _wrap(app):
    """包一層薄端泵隔離（測試走 pump 路徑；關門快徑關掉）。"""
    return thin_mod._isolate_business_asgi(
        app, control=None, reject_app=None, enable_reject_fast_path=False
    )


def _serve(app):
    """真實 uvicorn 起服（線程內），返回 (server, thread, 實得端口)。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="thin-pump-fix-uvicorn")
    thread.start()
    deadline = time.time() + 20.0
    while not server.started:
        if time.time() > deadline:
            raise AssertionError("uvicorn 在 20s 內未起服")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    assert port not in (8000, 8765, 8766, 8767, 18765), "實得端口命中生產端口: %r" % port
    return (server, thread, port)


def _stop(server, thread):
    server.should_exit = True
    thread.join(timeout=20.0)
    assert not thread.is_alive(), "uvicorn 線程未乾淨退出（孤兒進程風險）"


def _stream_once(token, port):
    """真 httpx client.stream 完整讀 body；成幀斷裂直接拋（不吞掉）。"""
    url = "http://127.0.0.1:%d%s" % (port, CHAT_PATH)
    with httpx.Client(timeout=15.0) as client:
        with client.stream(
            "POST",
            url,
            headers={"Authorization": "Bearer " + token},
            json={"model": "primary", "messages": [{"role": "user", "content": "hi"}], "stream": True},
        ) as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            # 逐塊消費：假 disconnect 造成的中斷會在這裡拋
            # IncompleteRead/RemoteProtocolError，而不是靜默截斷。
            chunks = list(resp.iter_bytes())
    return b"".join(chunks).decode("utf-8")


def _data_lines(raw):
    return [ln for ln in raw.splitlines() if ln.startswith("data: ")]


def _assert_plain_frames(raw):
    lines = _data_lines(raw)
    assert len(lines) == 4, "期望 4 幀（3 chunk + [DONE]），實得 %d：%r" % (len(lines), raw[:500])
    payloads = [json.loads(ln[len("data: "):]) for ln in lines[:3]]
    assert lines[3] == "data: [DONE]"
    role_chunk, content_chunk, stop_chunk = payloads
    assert role_chunk["choices"][0]["delta"] == {"role": "assistant"}
    assert content_chunk["choices"][0]["delta"] == {"content": FAKE_TEXT}
    assert content_chunk["choices"][0]["finish_reason"] is None
    assert stop_chunk["choices"][0]["delta"] == {}
    assert stop_chunk["choices"][0]["finish_reason"] == "stop"
    cid = role_chunk["id"]
    assert content_chunk["id"] == cid and stop_chunk["id"] == cid


def _assert_rich_frames(raw):
    lines = _data_lines(raw)
    assert len(lines) == 5, "期望 5 幀（3 chunk + usage + [DONE]），實得 %d：%r" % (
        len(lines), raw[:600])
    payloads = [json.loads(ln[len("data: "):]) for ln in lines[:4]]
    assert lines[4] == "data: [DONE]"
    role_chunk, content_chunk, stop_chunk, usage_chunk = payloads
    assert role_chunk["choices"][0]["delta"] == {"role": "assistant"}
    assert content_chunk["choices"][0]["delta"] == {"content": FAKE_TEXT, "tool_calls": TOOL_CALLS}
    assert content_chunk["choices"][0]["finish_reason"] is None
    assert stop_chunk["choices"][0]["delta"] == {}
    assert stop_chunk["choices"][0]["finish_reason"] == "stop"
    assert usage_chunk["choices"] == []
    assert usage_chunk["usage"] == USAGE
    cid = role_chunk["id"]
    assert content_chunk["id"] == cid and stop_chunk["id"] == cid and usage_chunk["id"] == cid


def _quadrant(tmp_path, monkeypatch, rich, wrapped):
    app, token, backend = _build(tmp_path, monkeypatch, rich=rich)
    if wrapped:
        app = _wrap(app)
    server, thread, port = _serve(app)
    try:
        raw = _stream_once(token, port)
    finally:
        _stop(server, thread)
    if rich:
        _assert_rich_frames(raw)
    else:
        _assert_plain_frames(raw)
    assert len(backend.calls) == 1
    return raw


def test_quadrant_direct_plain(tmp_path, monkeypatch):
    _quadrant(tmp_path, monkeypatch, rich=False, wrapped=False)


def test_quadrant_direct_rich(tmp_path, monkeypatch):
    _quadrant(tmp_path, monkeypatch, rich=True, wrapped=False)


def test_quadrant_wrapped_plain(tmp_path, monkeypatch):
    _quadrant(tmp_path, monkeypatch, rich=False, wrapped=True)


def test_quadrant_wrapped_rich(tmp_path, monkeypatch):
    _quadrant(tmp_path, monkeypatch, rich=True, wrapped=True)


def _http_scope(token, body: bytes, path=CHAT_PATH):
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", ("Bearer " + token).encode("ascii")),
            (b"content-type", b"application/json"),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("127.0.0.1", 80),
    }


def test_isolated_inprocess_stream_spec23_full_body(tmp_path, monkeypatch):
    """行程內驅動包泵版網關（scope 帶 asgi spec_version 2.3）。

    斷言：捕到 http.response.start ＋ 全部 body 幀 ＋ 結尾
    more_body=False，且 SSE 正文含 FAKE_TEXT 與 [DONE]。
    """
    app, token, backend = _build(tmp_path, monkeypatch, rich=False)
    isolated = _wrap(app)
    payload = json.dumps(
        {"model": "primary", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    ).encode("utf-8")
    scope = _http_scope(token, payload)
    sent: list = []

    async def _run():
        messages = [{"type": "http.request", "body": payload, "more_body": False}]
        idx = 0

        async def receive():
            nonlocal idx
            if idx < len(messages):
                msg = messages[idx]
                idx += 1
                return msg
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        await isolated(scope, receive, send)

    asyncio.run(_run())
    assert sent, "包泵版網關行程內無任何 response（評估未觸發？）"
    assert sent[0]["type"] == "http.response.start", sent[0]
    assert sent[0]["status"] == 200
    bodies = [m for m in sent[1:] if m["type"] == "http.response.body"]
    assert bodies, "沒有 body 幀：%r" % ([m.get("type") for m in sent],)
    assert bodies[-1].get("more_body", True) is False, "結尾幀缺 more_body=False"
    raw = b"".join(m.get("body", b"") for m in bodies).decode("utf-8")
    assert FAKE_TEXT in raw, raw[:500]
    assert "data: [DONE]" in raw, raw[:500]
    assert len(backend.calls) == 1


def _probe_stub(probed: dict):
    async def _stub(scope, receive, send):
        first = await receive()
        probed["first"] = first
        try:
            second = await asyncio.wait_for(receive(), timeout=0.5)
        except (asyncio.TimeoutError, TimeoutError):
            probed["pended"] = True
        else:
            probed["second"] = second
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    # _isolate_business_asgi 會鏡像 app.routes/router（T15 註冊證明用）；樁補空值即可。
    _stub.routes = []
    _stub.router = None
    return _stub


def _drive_isolated(isolated, body: bytes, path="/probe"):
    sent: list = []

    async def _run():
        messages = [{"type": "http.request", "body": body, "more_body": False}]
        idx = 0

        async def receive():
            nonlocal idx
            if idx < len(messages):
                msg = messages[idx]
                idx += 1
                return msg
            await asyncio.Event().wait()

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": [(b"content-type", b"application/json")],
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 80),
        }
        await isolated(scope, receive, send)

    asyncio.run(_run())
    return sent


def test_mem_receive_exhaustion_pends_instead_of_disconnect():
    """單元守衛（_mem_receive）：緩衝耗盡後必須永久等待，不得捏造 disconnect。

    有 bug 時第二次 receive() 會瞬時回 {"type": "http.disconnect"}，
    probed 記下 second 且測試變紅；修復後第二次 receive() 懸置
    0.5s 超時，probed["pended"] 為 True。
    """
    probed: dict = {}
    isolated = thin_mod._isolate_business_asgi(
        _probe_stub(probed), control=None, reject_app=None, enable_reject_fast_path=False
    )
    sent = _drive_isolated(isolated, b'{"hello": "world"}')
    assert probed.get("pended") is True, "耗盡後沒有懸置，反而返回：%r" % (probed.get("second"),)
    assert "second" not in probed
    assert sent[0]["type"] == "http.response.start"
    assert sent[-1].get("more_body") is False


def test_fast_receive_exhaustion_pends_instead_of_disconnect():
    """單元守衛（_fast_receive）：關門快徑耗盡後同樣必須永久等待。"""
    probed: dict = {}

    class _ClosedControl:
        _door_lock = threading.Lock()
        _instance_open = False

    async def _never_runs(scope, receive, send):
        raise AssertionError("關門＋SA 標記 body 必須走快徑，不該進 pump")

    _never_runs.routes = []
    _never_runs.router = None

    isolated = thin_mod._isolate_business_asgi(
        _never_runs,
        control=_ClosedControl(),
        reject_app=_probe_stub(probed),
        enable_reject_fast_path=True,
    )
    body = json.dumps({"soul_access_v1": {"probe": True}}).encode("utf-8")
    sent = _drive_isolated(isolated, body)
    assert probed.get("pended") is True, "快徑耗盡後沒有懸置，反而返回：%r" % (probed.get("second"),)
    assert "second" not in probed
    assert sent[0]["type"] == "http.response.start"
    assert sent[-1].get("more_body") is False
