"""
tests/test_dream_event_max_tokens.py — DREAM-MAXTOKENS-1

驗證 _call_llm_for_dream_event 的 max_tokens 一律被 REASONING_SAFE_MIN_TOKENS(400)
clamp 到 reasoning 安全下限, 消除 reasoning 模型 (deepseek-v4.1-flash) 因
max_tokens 過低吃光預算而「content 空字串 + finish_reason=length」靜默空回的缺陷:

  傳入 50  → 實際送出 400  (impression 呼叫端, 原本 50)
  傳入 120 → 實際送出 400  (函式預設值)
  傳入 800 → 實際送出 800  (clamp 只抬升, 不縮減)

用 fake proxy / fake httpx client 攔截實際送出的 max_tokens, 不打網路。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul.dream_event import (
    REASONING_SAFE_MIN_TOKENS,
    DreamEventWriter,
    _call_llm_for_dream_event,
)


# ─── Fake backends ──────────────────────────────────────────


class _FakeProxy:
    """攔截 LLMProxy.generate_text 的實際送出參數 (生產主路徑)."""

    def __init__(self):
        self.sent_max_tokens = None
        self.sent_messages = None

    async def generate_text(
        self, messages=None, agent_id="", max_tokens=None, temperature=None
    ):
        self.sent_max_tokens = max_tokens
        self.sent_messages = messages
        return "fake reply"


def _run(coro):
    return asyncio.run(coro)


# ─── 驗收: clamp 行為 (proxy 主路徑) ────────────────────────


def test_constant_floor_is_400():
    # 契約: REASONING_SAFE_MIN_TOKENS 必須是 400 (工單 DREAM-MAXTOKENS-1 拍板值)
    assert REASONING_SAFE_MIN_TOKENS == 400


def test_max_tokens_50_clamped_to_floor(monkeypatch):
    proxy = _FakeProxy()
    monkeypatch.setattr("src.soul.dream_event._find_llm_proxy", lambda: proxy)
    _run(_call_llm_for_dream_event("system", "user", "key", max_tokens=50))
    assert proxy.sent_max_tokens == 400


def test_max_tokens_120_default_clamped_to_floor(monkeypatch):
    proxy = _FakeProxy()
    monkeypatch.setattr("src.soul.dream_event._find_llm_proxy", lambda: proxy)
    # 不傳 max_tokens → 預設 120, 也要被 clamp 到 400
    _run(_call_llm_for_dream_event("system", "user", "key"))
    assert proxy.sent_max_tokens == 400


def test_max_tokens_800_not_shrunk(monkeypatch):
    proxy = _FakeProxy()
    monkeypatch.setattr("src.soul.dream_event._find_llm_proxy", lambda: proxy)
    _run(_call_llm_for_dream_event("system", "user", "key", max_tokens=800))
    assert proxy.sent_max_tokens == 800  # clamp 只抬升, 不縮減


def test_impression_call_site_50_becomes_400(monkeypatch):
    """生產呼叫端 _extract_impression (max_tokens=50) 實際送出 400."""
    proxy = _FakeProxy()
    monkeypatch.setattr("src.soul.dream_event._find_llm_proxy", lambda: proxy)
    writer = DreamEventWriter(data_dir=str(Path(__file__).parent), api_key="key")
    _run(
        writer._extract_impression(
            observer_id="agent_yua",
            target_id="agent_ruka",
            diary_content="做了個夢",
            kind="dream",
        )
    )
    assert proxy.sent_max_tokens == 400


def test_legacy_minimax_payload_clamped(monkeypatch):
    """Legacy minimax 直連路徑 (無 proxy 時) 的 JSON payload 也要 clamp (50 → 400)."""
    captured = {}

    class _FakeResponse:
        status_code = 200
        text = "ok"

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return _FakeResponse()

    import httpx

    monkeypatch.setattr("src.soul.dream_event._find_llm_proxy", lambda: None)
    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)
    _run(_call_llm_for_dream_event("system", "user", "key", max_tokens=50))
    assert captured["json"]["max_tokens"] == 400