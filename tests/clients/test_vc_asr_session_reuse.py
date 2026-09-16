"""
tests/clients/test_vc_asr_session_reuse.py
VC-ASR-REUSE — ASR Refiner LLM 呼叫改用「行程級共用連線池 Session」的驗收測試。

背景：`clients/voice_companion/asr_refiner.py` 的 `build_llm_call()` 之真實
`call()` 路徑原本每回合走**裸 `requests.post`**，每回合多付一次 TLS 握手
（約 18–20ms）。本票改為共用 `requests.Session`（連線池 4 / 上限 8、keep-alive）。

本檔是本票**唯一**覆蓋真實 `call()` 路徑的測試（既有 `tests/**` 全部注入
`llm_call`）。逐支對應工單 §4：

  1. `test_session_object_reused_across_two_real_calls`      — 連兩次呼叫 `is` 同一 session，`Session.post` 呼叫 2 次
  2. `test_bare_requests_post_is_never_called`               — 護欄：裸 `requests.post` 0 次（變異 (a) 的咬點）
  3. `test_request_semantics_unchanged_with_api_key`         — endpoint/body/headers/timeout=30/raise_for_status 語意不變
  4. `test_no_authorization_header_when_api_key_empty`       — api_key 空 ⇒ 不帶 Authorization
  5. `test_raise_for_status_error_propagates`                — HTTP 錯誤照原語意往上拋（不被吞）
  6. `test_success_payload_is_parsed`                        — OpenAI 相容 JSON ⇒ choices[0].message.content
  7. `test_close_llm_session_is_idempotent_and_rebuilds`     — 清理冪等；關閉後重建新 session（不重用已關的）
  8. `test_pool_adapter_is_mounted_on_both_schemes`          — https/http 皆掛 HTTPAdapter，連線池 4／8
  9. `test_pool_constants_match_sibling_convention`          — 常數值沿用 akane_voice_brain 慣例
 10. `test_module_has_no_top_level_requests_attribute`       — 懶載入契約：模組頂層無 `requests`
 11. `test_module_executes_and_raises_without_requests`      — 沒有 requests 時 import 不炸；建 session 時 ImportError 上拋（不靜默 None）
 12. `test_atexit_registration_is_idempotent_and_safe`       — atexit 註冊一次且回呼不炸（可重複呼叫）

全離線：所有 HTTP 一律在 `requests.Session.post` / `requests.post` 層被 mock，
endpoint 用 loopback 假位址，0 真實外呼、0 寫生產 `data/**`。
"""

from __future__ import annotations

import atexit
import importlib.util
import sys
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
import requests

from clients.voice_companion import asr_refiner as asr_refiner_mod
from clients.voice_companion.asr_refiner import (
    ASR_HTTP_POOL_CONNECTIONS,
    ASR_HTTP_POOL_MAXSIZE,
    build_llm_call,
    close_llm_session,
)

MODULE_PATH = Path(asr_refiner_mod.__file__).resolve()

# loopback 假位址（全程 mock，永不真的連線；非任何 LLM 供應商端點）
ENDPOINT_CFG = "http://127.0.0.1:9/v1"
ENDPOINT_EXPECTED = "http://127.0.0.1:9/v1/chat/completions"
MODEL = "qwen-test-model"
API_KEY = "sk-test-not-a-real-key"
CLEAN_TEXT = "淨化後的文字。"


class _FakeResponse:
    """假的 `requests.Response`：只實作被呼叫到的三個介面。"""

    def __init__(self, payload: Optional[dict] = None, status_error: Optional[Exception] = None) -> None:
        self._payload = payload if payload is not None else {
            "choices": [{"index": 0, "message": {"role": "assistant", "content": CLEAN_TEXT}}]
        }
        self._status_error = status_error
        self.raise_for_status_calls = 0
        self.json_calls = 0
        self.closed = False

    def raise_for_status(self) -> None:
        self.raise_for_status_calls += 1
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> Any:
        self.json_calls += 1
        return self._payload

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _isolate_llm_session():
    """每個測試前後都清掉行程級 singleton，避免測試互相污染。"""
    close_llm_session()
    try:
        yield
    finally:
        close_llm_session()


def _make_call(**overrides) -> Any:
    cfg = {"endpoint": ENDPOINT_CFG, "model": MODEL, "api_key": API_KEY}
    cfg.update(overrides)
    return build_llm_call(cfg)


def _explode_bare_post(*args, **kwargs):
    raise AssertionError("裸 requests.post 被呼叫 —— 連線池重用已失效（迴歸為 VC-ASR-REUSE 前行為）")


# ─────────────────────────────────────────────────────────────
# §4-1 Session 重用（變異 (b) 的咬點）
# ─────────────────────────────────────────────────────────────

def test_session_object_reused_across_two_real_calls(monkeypatch):
    """連續兩次走真實 call() ⇒ 同一個 session 物件，且 Session.post 被呼叫兩次。"""
    seen_sessions = []
    fake_resp = _FakeResponse()
    delegate = MagicMock(return_value=fake_resp)

    # 注意：直接掛 MagicMock 到類別屬性不會綁定 self（MagicMock 非 descriptor），
    # 故以一般函式攔截（會綁定 self）再委派給 MagicMock 計數。
    def _interceptor(self, url, **kwargs):
        seen_sessions.append(self)
        return delegate(self, url, **kwargs)

    monkeypatch.setattr(requests.Session, "post", _interceptor)
    monkeypatch.setattr(requests, "post", _explode_bare_post)

    call = _make_call()
    assert call("第一回合") == CLEAN_TEXT
    assert call("第二回合") == CLEAN_TEXT

    assert delegate.call_count == 2, "兩次呼叫必須各發一次請求"
    assert delegate.call_args[0][1] == ENDPOINT_EXPECTED
    assert len(seen_sessions) == 2
    assert seen_sessions[0] is seen_sessions[1], "兩次呼叫必須重用同一個 Session 物件"
    assert seen_sessions[0] is asr_refiner_mod._get_llm_session()


# ─────────────────────────────────────────────────────────────
# §4-2 護欄：裸 requests.post 從未被呼叫（變異 (a) 的咬點）
# ─────────────────────────────────────────────────────────────

def test_bare_requests_post_is_never_called(monkeypatch):
    """patch requests.post 後走真實 call() ⇒ 斷言未被呼叫（0 每回合新建連線）。"""
    bare_guard = MagicMock(side_effect=_explode_bare_post)
    monkeypatch.setattr(requests, "post", bare_guard)
    post_mock = MagicMock(return_value=_FakeResponse())
    monkeypatch.setattr(requests.Session, "post", post_mock)

    call = _make_call()
    assert call("護欄測試") == CLEAN_TEXT

    assert bare_guard.call_count == 0, "call() 不得落到裸 requests.post（連線池重用護欄）"
    assert post_mock.call_count == 1
    assert post_mock.call_args[0][0] == ENDPOINT_EXPECTED


# ─────────────────────────────────────────────────────────────
# §4-3 請求語意不變（變異 (c) 的咬點）
# ─────────────────────────────────────────────────────────────

def test_request_semantics_unchanged_with_api_key(monkeypatch):
    """endpoint／body／headers／timeout=30／raise_for_status 與原實作逐項一致。"""
    fake_resp = _FakeResponse()
    post_mock = MagicMock(return_value=fake_resp)
    monkeypatch.setattr(requests.Session, "post", post_mock)

    call = _make_call()
    assert call("你好，茜") == CLEAN_TEXT

    args, kwargs = post_mock.call_args
    assert args == (ENDPOINT_EXPECTED,), "endpoint 需經 normalize_chat_endpoint 正規化"
    assert kwargs["json"] == {
        "model": MODEL,
        "messages": [{"role": "user", "content": "你好，茜"}],
        "stream": False,
    }
    assert kwargs["headers"] == {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
    }
    assert kwargs["timeout"] == 30, "timeout=30 逐字不變"
    assert fake_resp.raise_for_status_calls == 1
    assert fake_resp.json_calls == 1


def test_no_authorization_header_when_api_key_empty(monkeypatch):
    """api_key 為空 ⇒ 不得帶 Authorization（與原實作一致）。"""
    post_mock = MagicMock(return_value=_FakeResponse())
    monkeypatch.setattr(requests.Session, "post", post_mock)

    call = _make_call(api_key="")
    assert call("無金鑰") == CLEAN_TEXT

    headers = post_mock.call_args[1]["headers"]
    assert headers == {"Content-Type": "application/json"}
    assert "Authorization" not in headers


def test_raise_for_status_error_propagates(monkeypatch):
    """HTTP 錯誤（raise_for_status 拋出）照原語意往上拋，不被吞掉。"""
    err = requests.HTTPError("500 Server Error")
    fake_resp = _FakeResponse(status_error=err)
    monkeypatch.setattr(requests.Session, "post", MagicMock(return_value=fake_resp))

    call = _make_call()
    with pytest.raises(requests.HTTPError):
        call("錯誤路徑")
    assert fake_resp.raise_for_status_calls == 1
    assert fake_resp.json_calls == 0, "raise_for_status 失敗後不得再解析 body"


# ─────────────────────────────────────────────────────────────
# §4-4 成功解析
# ─────────────────────────────────────────────────────────────

def test_success_payload_is_parsed(monkeypatch):
    """OpenAI 相容回應 ⇒ 回傳 choices[0].message.content。"""
    payload = {"choices": [{"message": {"role": "assistant", "content": "茜，我在。"}}]}
    monkeypatch.setattr(requests.Session, "post", MagicMock(return_value=_FakeResponse(payload=payload)))

    call = _make_call()
    assert call("茜？") == "茜，我在。"


# ─────────────────────────────────────────────────────────────
# §4-5 生命週期與清理
# ─────────────────────────────────────────────────────────────

def test_close_llm_session_is_idempotent_and_rebuilds(monkeypatch):
    """close_llm_session() 冪等；關閉後下一次呼叫建立新 session（不重用已關閉者）。"""
    post_mock = MagicMock(side_effect=lambda *a, **k: _FakeResponse())
    monkeypatch.setattr(requests.Session, "post", post_mock)

    closed_by_us = []
    real_close = requests.Session.close

    def _tracking_close(self):
        closed_by_us.append(self)
        return real_close(self)

    monkeypatch.setattr(requests.Session, "close", _tracking_close)

    call = _make_call()
    call("關閉前")
    first_session = asr_refiner_mod._get_llm_session()

    close_llm_session()
    close_llm_session()  # 冪等：不炸、且不重複關閉
    assert closed_by_us == [first_session], "重複呼叫不得重複關閉（且必須真的關過一次）"
    assert asr_refiner_mod._LLM_SESSION is None

    second_session = asr_refiner_mod._get_llm_session()
    assert second_session is not first_session, "關閉後必須重建，不得重用已關閉的 session"
    assert second_session not in closed_by_us

    call("關閉後")
    assert post_mock.call_count == 2
    assert post_mock.call_args_list[1][1]["timeout"] == 30  # 重建後語意不變


def test_pool_adapter_is_mounted_on_both_schemes():
    """https:// 與 http:// 都掛上 HTTPAdapter，且連線池為 4／上限 8。"""
    session = asr_refiner_mod._get_llm_session()
    try:
        for prefix in ("https://", "http://"):
            adapter = session.get_adapter(prefix + "llm.invalid")
            assert isinstance(adapter, requests.adapters.HTTPAdapter)
            assert adapter._pool_connections == ASR_HTTP_POOL_CONNECTIONS
            assert adapter._pool_maxsize == ASR_HTTP_POOL_MAXSIZE
        assert ASR_HTTP_POOL_CONNECTIONS == 4
        assert ASR_HTTP_POOL_MAXSIZE == 8
    finally:
        close_llm_session()


def test_pool_constants_match_sibling_convention():
    """數值沿用 akane_voice_brain 的 VC_HTTP_POOL_* 慣例（4／8），且不跨模組 import。"""
    from clients.voice_companion import akane_voice_brain as brain_mod

    assert ASR_HTTP_POOL_CONNECTIONS == brain_mod.VC_HTTP_POOL_CONNECTIONS
    assert ASR_HTTP_POOL_MAXSIZE == brain_mod.VC_HTTP_POOL_MAXSIZE


# ─────────────────────────────────────────────────────────────
# §4-6 懶載入契約
# ─────────────────────────────────────────────────────────────

def test_module_has_no_top_level_requests_attribute():
    """模組頂層不得有 `requests` 屬性 ⇒ 證明沒有 module-level import。"""
    assert "requests" not in vars(asr_refiner_mod)


def _load_probe_module(name: str):
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # 不註冊進 sys.modules：完全隔離的探針副本
    return mod


def test_module_executes_and_raises_without_requests(monkeypatch):
    """沒有 requests 可用時：模組 import 不炸；建 session 時 ImportError 上拋（不靜默 None）。

    手法：把 `sys.modules['requests*']` 暫時換成 None 哨兵（`import requests` 立即
    ImportError），載入**探針副本**驗證；finally 逐鍵還原，絕不真的卸載 requests
    而影響其他測試。
    """
    sentinel_keys = [k for k in sys.modules if k == "requests" or k.startswith("requests.")]
    saved = {k: sys.modules[k] for k in sentinel_keys}
    for k in sentinel_keys:
        sys.modules[k] = None
    try:
        probe = _load_probe_module("_vc_asr_refiner_probe_offline")
        assert "requests" not in vars(probe)

        with pytest.raises(ImportError):
            probe._get_llm_session()
        assert probe._LLM_SESSION is None, "建構失敗不得留下殘缺的 session"
    finally:
        for k, v in saved.items():
            sys.modules[k] = v
        for k in sentinel_keys:
            if k not in saved:
                sys.modules.pop(k, None)

    # 還原後正式模組仍可正常建 session（證明隔離乾淨）
    assert asr_refiner_mod._get_llm_session() is not None
    close_llm_session()


# ─────────────────────────────────────────────────────────────
# atexit 清理註冊（工單 §3.2 可選項；加了就要測它不會炸）
# ─────────────────────────────────────────────────────────────

def test_atexit_registration_is_idempotent_and_safe(monkeypatch):
    """模組載入時恰好註冊一次 atexit 回呼；回呼可重複呼叫且不炸。"""
    registered = []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **k: registered.append(fn))

    probe = _load_probe_module("_vc_asr_refiner_probe_atexit")
    assert len(registered) == 1, "atexit 回呼須恰好註冊一次"
    callback = registered[0]

    callback()  # 從未建立 session → 不炸
    callback()  # 冪等

    probe._get_llm_session()
    callback()
    callback()
    assert probe._LLM_SESSION is None
