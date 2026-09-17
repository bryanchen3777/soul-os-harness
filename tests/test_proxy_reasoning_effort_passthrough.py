# tests/test_proxy_reasoning_effort_passthrough.py
# LIFE-THREAD-M2-WIRING-1 (D1) — `src/llm/proxy.py` 的 `reasoning_effort` 透傳
# 與「零額外重試」開關；**同時釘死共用通道的零行為變更不變量**。
#
# 🔴 本檔最高優先的不變量
# ─────────────────────
# **未傳新參數時，所有既有呼叫路徑的請求 body 與改動前逐位元相同。**
# 這是「全體 agent 共用生產 LLM 通道」的硬性要求，故本檔以**三重**釘死：
#   1. `json_body` 的**鍵順序集合**逐字相等（`_BASE_BODY_KEYS`）；
#   2. 序列化後的 JSON **字串**逐字相等（`_BASE_BODY_JSON`，含鍵序）；
#   3. `backend.complete(...)` 收到的**關鍵字引數集合**逐字相等（無多餘參數）。
#
# 本檔紅線自證：
#   * **0 真實 LLM、0 網路**：HTTP 層一律 `httpx.MockTransport`（記憶體內回應）。
#     本檔**不含**任何真實 endpoint／服務位址字面量（唯一 URL 是 `offline.invalid`）。
#   * 0 服務啟停、0 埠綁定、0 行程命令、0 `data/**` 寫入。
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import src.llm.proxy as proxy_mod  # noqa: E402

OFFLINE_URL = "http://offline.invalid/v1/chat/completions"

#: 改動前 `OpenAIBackend.complete` 的 `json_body` **鍵序**（逐字，不得多／少／換序）。
_BASE_BODY_KEYS = ["model", "messages", "max_completion_tokens", "temperature"]

#: 改動前 body 的**凍結字面值**（鍵序即此 dict 的插入序）。序列化後即為逐位元基準。
_FROZEN_BASE_BODY: Dict[str, Any] = {
    "model": "m",
    "messages": [{"role": "user", "content": "hi"}],
    "max_completion_tokens": 300,
    "temperature": 0.85,
}

_MESSAGES = [{"role": "user", "content": "hi"}]


def _serialize(body: Dict[str, Any]) -> bytes:
    """用 **httpx 0.28.1 同一套**序列化參數（compact separators、`ensure_ascii=False`）。

    實測：`httpx.Request(json=...)` 產生
    `{"model":"m",...,"temperature":0.85}`（**無空白**）⇒ 本函式產出即為位元基準。
    """
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


# ══════════════════════════════════════════════════════════════
# helpers：記憶體內 capture（**0 網路**）
# ══════════════════════════════════════════════════════════════


class _Capture:
    """捕獲每次 HTTP 請求的 raw body（不解析成 dict 之前先留存 bytes）。"""

    def __init__(self, responses: Optional[List[int]] = None):
        self.requests: List[httpx.Request] = []
        self._responses = list(responses or [200])

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        index = min(len(self.requests) - 1, len(self._responses) - 1)
        status = self._responses[index]
        if status != 200:
            return httpx.Response(status, json={"error": "boom"})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    def bodies(self) -> List[Dict[str, Any]]:
        return [json.loads(r.content.decode("utf-8")) for r in self.requests]

    def body_texts(self) -> List[str]:
        return [r.content.decode("utf-8") for r in self.requests]


@pytest.fixture()
def capture_factory(monkeypatch: pytest.MonkeyPatch):
    """把 `httpx.AsyncClient` 換成帶 `MockTransport` 的實例（**0 網路**）。"""
    real_client = httpx.AsyncClient

    def make(responses: Optional[List[int]] = None) -> _Capture:
        capture = _Capture(responses)

        def factory(*a, **k):
            k["transport"] = httpx.MockTransport(capture.handler)
            return real_client(*a, **k)

        monkeypatch.setattr(proxy_mod.httpx, "AsyncClient", factory)
        return capture

    return make


def _openai_backend() -> Any:
    return proxy_mod.OpenAIBackend(api_key="test-key", base_url=OFFLINE_URL)


async def _complete(backend: Any, **kwargs) -> Any:
    params: Dict[str, Any] = {
        "messages": _MESSAGES,
        "model": "m",
        "max_tokens": 300,
        "temperature": 0.85,
    }
    params.update(kwargs)
    return await backend.complete(**params)


# ══════════════════════════════════════════════════════════════
# §1 不變量：未傳新參數 ⇒ body 逐位元相同
# ══════════════════════════════════════════════════════════════


def test_invariant_openai_body_key_order_is_unchanged(capture_factory):
    """🔴 未傳新參數 ⇒ `json_body` 的**鍵序**與改動前逐字相同。"""
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend()))

    assert len(capture.requests) == 1
    body = capture.bodies()[0]
    assert list(body.keys()) == _BASE_BODY_KEYS, list(body.keys())
    assert "reasoning_effort" not in body


def test_invariant_openai_body_is_byte_identical_to_pre_change(capture_factory):
    """🔴 未傳新參數 ⇒ 序列化後的請求 body **逐位元相同**（含鍵序與序列化空白）。"""
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend()))

    assert capture.requests[0].content == _serialize(_FROZEN_BASE_BODY)
    assert capture.bodies()[0] == _FROZEN_BASE_BODY


def test_invariant_optional_penalties_and_response_format_still_conditional(capture_factory):
    """既有條件式欄位（penalty／response_format）行為不變：未傳 ⇒ 不出現在 body。"""
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend()))
    body = capture.bodies()[0]
    for absent in ("frequency_penalty", "presence_penalty", "response_format", "tools"):
        assert absent not in body

    capture2 = capture_factory()
    asyncio.run(
        _complete(
            _openai_backend(),
            frequency_penalty=0.3,
            presence_penalty=0.4,
            response_format={"type": "json_object"},
        )
    )
    body2 = capture2.bodies()[0]
    assert list(body2.keys()) == _BASE_BODY_KEYS + [
        "frequency_penalty",
        "presence_penalty",
        "response_format",
    ]
    assert body2["frequency_penalty"] == 0.3
    assert body2["presence_penalty"] == 0.4


def test_invariant_generate_text_sends_no_extra_kwargs_by_default():
    """🔴 `generate_text` 未傳新參數 ⇒ 傳給 backend 的**引數集合**與改動前相同。"""
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            return "ok"

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _Backend()
            self.model = "m"

    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES))
    assert out == "ok"
    assert len(recorded) == 1
    assert sorted(recorded[0].keys()) == [
        "max_tokens",
        "messages",
        "model",
        "temperature",
        "thinking",
    ], sorted(recorded[0].keys())
    assert recorded[0]["thinking"] is None


# ══════════════════════════════════════════════════════════════
# §2 透傳：`reasoning_effort` 只在非 None 時出現（僅 OpenAI 相容 body）
# ══════════════════════════════════════════════════════════════


def test_reasoning_effort_is_added_when_passed(capture_factory):
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend(), reasoning_effort="none"))

    body = capture.bodies()[0]
    assert body["reasoning_effort"] == "none"
    assert list(body.keys()) == _BASE_BODY_KEYS + ["reasoning_effort"]
    assert body["model"] == "m"
    assert body["max_completion_tokens"] == 300


def test_reasoning_effort_none_literal_is_not_sent_as_string(capture_factory):
    """`None`（Python 空值）＝**不送**；只有字串 `"none"` 才送（兩者不可混淆）。"""
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend(), reasoning_effort=None))
    assert "reasoning_effort" not in capture.bodies()[0]


@pytest.mark.parametrize("value", ["none", "minimal", "low", "medium", "high"])
def test_reasoning_effort_values_pass_through_verbatim(value: str, capture_factory):
    capture = capture_factory()
    asyncio.run(_complete(_openai_backend(), reasoning_effort=value))
    assert capture.bodies()[0]["reasoning_effort"] == value


def test_generate_text_passes_reasoning_effort_downstream():
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            return "ok"

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _Backend()
            self.model = "m"

    asyncio.run(_Shim().generate_text(messages=_MESSAGES, reasoning_effort="none"))
    assert recorded[0]["reasoning_effort"] == "none"


def test_claude_backend_body_never_contains_reasoning_effort(capture_factory):
    """🔴 跨後端隔離：`ClaudeBackend` 走自己的 `thinking`，body **永不**含該鍵。"""
    capture = capture_factory()
    backend = proxy_mod.ClaudeBackend(api_key="test-key", base_url=OFFLINE_URL)
    asyncio.run(
        _complete(backend, reasoning_effort="none", thinking={"type": "enabled", "budget_tokens": 64})
    )

    body = capture.bodies()[0]
    assert "reasoning_effort" not in body, body
    assert body["thinking"] == {"type": "enabled", "budget_tokens": 64}
    assert set(body.keys()) == {"model", "system", "messages", "max_tokens", "temperature", "thinking"}


def test_claude_backend_body_is_unchanged_without_reasoning_effort(capture_factory):
    """Claude 未傳新參數 ⇒ body 與改動前逐位元相同。"""
    capture = capture_factory()
    backend = proxy_mod.ClaudeBackend(api_key="test-key", base_url=OFFLINE_URL)
    asyncio.run(_complete(backend))

    assert capture.requests[0].content == _serialize(
        {
            "model": "m",
            "system": "",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 300,
            "temperature": 0.85,
        }
    )


# ══════════════════════════════════════════════════════════════
# §3 重試：預設路徑不變；明確要求零重試 ⇒ 恰 1 次 HTTP 嘗試
# ══════════════════════════════════════════════════════════════


def test_openai_backend_default_still_retries_three_times(capture_factory):
    """`OpenAIBackend` 未傳 `max_retries` ⇒ 既有 `range(3+1)` ＝ 4 次 HTTP（行為不變）。"""
    capture = capture_factory(responses=[500])
    with pytest.raises(RuntimeError, match="exhausted 4 attempts"):
        asyncio.run(_complete(_openai_backend()))
    assert len(capture.requests) == 4


def test_openai_backend_max_retries_zero_is_exactly_one_attempt(capture_factory):
    """明確要求零重試 ⇒ `range(0+1)` ＝ **恰 1 次** HTTP 嘗試。"""
    capture = capture_factory(responses=[500])
    with pytest.raises(RuntimeError, match="exhausted 1 attempts"):
        asyncio.run(_complete(_openai_backend(), max_retries=0))
    assert len(capture.requests) == 1


def test_generate_text_zero_retry_hits_each_layer_exactly_once(capture_factory):
    """🔴 端到端：`generate_text(max_retries=0)` ⇒ 兩層皆恰 1 次 ⇒ HTTP 恰 1 次。

    （舊路徑最壞 ＝ 本層 `range(2)` × backend `range(3+1)` ＝ **8 次**。）
    """
    capture = capture_factory(responses=[500])

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _openai_backend()
            self.model = "m"

    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES, max_retries=0))
    assert out is None  # 失敗一律回 None（既有契約）
    assert len(capture.requests) == 1, f"必須恰 1 次 HTTP 嘗試：{len(capture.requests)}"
    assert capture.bodies()[0]["max_completion_tokens"] == 200  # generate_text 預設


def test_generate_text_default_path_is_two_layer_attempts(monkeypatch: pytest.MonkeyPatch):
    """對照組：未傳 `max_retries` ⇒ **本層 2 次嘗試**（既有行為不變）。

    用**假 backend**（拋可重試的 `HTTPStatusError`）把重試留在本層觀測；
    退避縮為 0（不動生產碼），故不影響任何真實請求。
    """
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", OFFLINE_URL),
                response=httpx.Response(500),
            )

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _Backend()
            self.model = "m"

    monkeypatch.setattr(proxy_mod.asyncio, "sleep", _no_sleep)
    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES))
    assert out is None
    assert len(recorded) == 2, "預設 ⇒ 本層 2 次嘗試（既有行為）"
    assert "max_retries" not in recorded[0], "未傳時不得多送參數（逐位元不變）"
    assert "reasoning_effort" not in recorded[0]


def test_generate_text_zero_retry_collapses_the_retry_loop(monkeypatch: pytest.MonkeyPatch):
    """🔴 `max_retries=0` ⇒ 本層嘗試次數由 2 收斂為 **1**（零額外重試的牙齒）。"""
    recorded: List[Dict[str, Any]] = []

    class _Backend:
        async def complete(self, **kwargs):
            recorded.append(dict(kwargs))
            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("POST", OFFLINE_URL),
                response=httpx.Response(500),
            )

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _Backend()
            self.model = "m"

    monkeypatch.setattr(proxy_mod.asyncio, "sleep", _no_sleep)
    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES, max_retries=0))
    assert out is None
    assert len(recorded) == 1, "max_retries=0 ⇒ 本層恰 1 次嘗試"
    assert recorded[0]["max_retries"] == 0, "必須把零重試透傳給 backend 層"


async def _no_sleep(_seconds):
    """把重試退避縮成 0（測試用；不動生產碼）。"""
    return None


def test_generate_text_zero_retry_hits_each_layer_exactly_once(capture_factory):
    """🔴 端到端（真實 backend ＋ 記憶體 transport）：`generate_text(max_retries=0)`
    ⇒ 兩層皆恰 1 次 ⇒ **HTTP 恰 1 次**。

    （舊路徑最壞 ＝ 本層 `range(2)` × backend `range(3+1)` ＝ **8 次**；
    實測 500 情境舊路徑為 backend 4 次，見上一個測試。）
    """
    capture = capture_factory(responses=[500])

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _openai_backend()
            self.model = "m"

    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES, max_retries=0))
    assert out is None  # 失敗一律回 None（既有契約）
    assert len(capture.requests) == 1, f"必須恰 1 次 HTTP 嘗試：{len(capture.requests)}"
    assert capture.bodies()[0]["max_completion_tokens"] == 200  # generate_text 預設


def test_generate_text_rejects_non_int_max_retries(capture_factory):
    """非整數 `max_retries` ⇒ 退化為既有行為（2 次），不炸。"""
    capture = capture_factory(responses=[200])

    class _Shim:
        generate_text = proxy_mod.LLMProxy.generate_text

        def __init__(self):
            self.backend = _openai_backend()
            self.model = "m"

    out = asyncio.run(_Shim().generate_text(messages=_MESSAGES, max_retries="nope"))
    assert out == "ok"
    assert len(capture.requests) == 1