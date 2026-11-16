"""SHIM-STREAM-SSE-FIX-001 — 真实 uvicorn 流式成帧守卫。

背景：``StreamingResponse`` 若手动带 ``"Connection": "keep-alive"`` 头，
在真实 uvicorn/h11 下会破坏 chunked 成帧（客户端读到 IncompleteRead）。
``TestClient`` 走 ASGI 内存传输、从不经过真实成帧，所以历史测试全绿也
盖不住这个 bug。本文件用**真实 uvicorn + 真实 httpx 流式客户端**钉住它。

传输面（全部本地回环、零真实上游）：
  - backend 永远是注入的假 carrier（返回固定串、计数 calls）；
  - uvicorn 绑 ``127.0.0.1``、端口由 OS 自动分配（``port=0`` 回读实得端口，
    永不碰 8000/8765/8766/8767/18765 等生产端口）；
  - 线程拉起 ``uvicorn.Server``，teardown 置 ``should_exit`` + join，
    不杀进程、不留孤儿。

断言：200 ＋ content-type 为 event-stream ＋ 4 帧完整
（role/Content/stop 三个 chunk ＋ ``[DONE]``）＋ 干净关闭（无
IncompleteRead/RemoteProtocolError）＋ provider 恰被调用一次。
"""
from __future__ import annotations

import json
import threading
import time

import httpx
import pytest
import uvicorn

from src.external_gateway.app import create_external_app
from src.external_gateway.app import file_preset_loader
from src.interfaces.credentials import digest_of
from src.interfaces.credentials import issue_credential
from src.interfaces.external_auth import RegistryProvider

OWNER = "shim-stream-fix-owner"
REF = "SHIM_STREAM_FIX_REF"
SENTINEL = "SHIM-STREAM-FIX-SENTINEL-KEY"
FAKE_TEXT = "shim-stream-fix-fake-reply"


class _FakeBackend:
    """注入的 carrier 替身：计数 calls，返回固定串。"""

    def __init__(self, text=FAKE_TEXT):
        self.text = text
        self.calls = []

    def complete(self, route, preset, messages, params, passthrough=None):
        self.calls.append((route, preset, messages, params, passthrough))
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
                "client_id": "client-shim-stream-fix",
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
                        "model": "do-not-deploy-model-shim-stream-fix",
                        "params": {"temperature": 0.5, "max_tokens": 512},
                        "key_ref": REF,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


def _build(tmp_path, monkeypatch):
    monkeypatch.setenv(REF, SENTINEL)
    token, _digest = issue_credential()
    preset_path = _write_presets(tmp_path / "presets.json")
    provider = RegistryProvider(_raw(token))
    backend = _FakeBackend()
    app = create_external_app(provider, file_preset_loader(preset_path), backend)
    return (app, token, backend)


def _serve(app):
    """真实 uvicorn 起服（线程内），返回 (server, thread, 实得端口)。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="critical", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True, name="shim-stream-fix-uvicorn")
    thread.start()
    deadline = time.time() + 20.0
    while not server.started:
        if time.time() > deadline:
            raise AssertionError("uvicorn 在 20s 内未起服")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    assert port not in (8000, 8765, 8766, 8767, 18765), "实得端口命中生产端口: %r" % port
    return (server, thread, port)


def _stop(server, thread):
    server.should_exit = True
    thread.join(timeout=20.0)
    assert not thread.is_alive(), "uvicorn 线程未干净退出（孤儿进程风险）"


def test_real_uvicorn_stream_full_sse_frames_and_clean_close(tmp_path, monkeypatch):
    app, token, backend = _build(tmp_path, monkeypatch)
    server, thread, port = _serve(app)
    try:
        url = "http://127.0.0.1:%d/rem/primary/v1/chat/completions" % port
        with httpx.Client(timeout=15.0) as client:
            with client.stream(
                "POST",
                url,
                headers={"Authorization": "Bearer " + token},
                json={"model": "primary", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers["content-type"]
                # 逐块消费：chunked 成帧断裂会在这里抛
                # RemoteProtocolError（IncompleteRead），而不是静默截断。
                chunks = list(resp.iter_bytes())
        raw = b"".join(chunks).decode("utf-8")
    finally:
        _stop(server, thread)

    lines = [ln for ln in raw.splitlines() if ln.startswith("data: ")]
    assert len(lines) == 4, "期望 4 帧（3 chunk + [DONE]），实得 %d：%r" % (len(lines), raw[:500])
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
    assert len(backend.calls) == 1


def test_real_uvicorn_stream_headers_no_manual_connection(tmp_path, monkeypatch):
    """标头回归钉：成帧交给 uvicorn/Starlette，不手动加 Connection 头。"""
    app, token, _backend = _build(tmp_path, monkeypatch)
    server, thread, port = _serve(app)
    try:
        url = "http://127.0.0.1:%d/rem/primary/v1/chat/completions" % port
        with httpx.Client(timeout=15.0) as client:
            with client.stream(
                "POST",
                url,
                headers={"Authorization": "Bearer " + token},
                json={"model": "primary", "messages": [{"role": "user", "content": "hi"}], "stream": True},
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers.get("cache-control") == "no-cache"
                assert resp.headers.get("x-accel-buffering") == "no"
                assert "connection" not in {k.lower() for k in resp.headers.keys()}, (
                    "手动 Connection 头回来了：%r" % dict(resp.headers)
                )
                list(resp.iter_bytes())
    finally:
        _stop(server, thread)
