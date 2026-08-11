"""
tests/_helpers/mock_llm_backend.py

M6.0-2 (Bry 派工 2026-08-11): Deterministic Mock LLM Backend.

Purpose:
  - 不呼叫任何真實 LLM / API
  - 可控制 deterministic response
  - 不修改 production LLM code

設計原則:
  - MockLLMBackend 實作 LLMBackend abstract class (src/llm/proxy.py:904)
  - response_strategy 函式 (messages, model) -> str
  - 預設策略: 根據 messages 內容產生 deterministic 文字
  - 可注入自訂策略做更精細的測試

使用範例:
    from tests._helpers.mock_llm_backend import MockLLMBackend, default_strategy

    backend = MockLLMBackend(response_strategy=default_strategy)
    proxy = LLMProxy(bus=bus, backend=backend)

    # 驗證 mock 被調用
    assert backend.call_count > 0
    assert backend.last_messages == expected_messages
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional

from src.llm.proxy import LLMBackend

logger = logging.getLogger("tests.helpers.mock_llm_backend")


def default_strategy(messages: List[Dict[str, str]], model: str) -> str:
    """
    Default deterministic response strategy.

    Rules:
      1. If messages contain LLM Judge extract_triples request → return JSON triples
      2. If messages contain stance judgment request → return "self_directed"
      3. Otherwise → return generic acknowledgment

    For M6.0-2 PoC, we don't need detailed LLM Judge simulation.
    Scenarios A/B/C use simplified paths that don't require the full LLM Judge flow.
    """
    user_msg = messages[-1].get("content", "") if messages else ""

    # LLM Judge pattern detection (for Scenario C if needed)
    if "三元組" in user_msg or "triples" in user_msg.lower():
        # Simple extract response
        return json.dumps({
            "triples": [
                {"subject": "Bry", "predicate": "提及", "object": "test event"},
            ]
        }, ensure_ascii=False)

    if "stance" in user_msg.lower() or "self_directed" in user_msg.lower():
        return json.dumps({
            "judgment": "SELF_DIRECTED",
            "reason": "Mock stance: self_directed",
        }, ensure_ascii=False)

    # Default: simple acknowledgment
    return "（Mock LLM response: deterministic placeholder）"


def fixed_response_strategy(text: str) -> Callable:
    """
    Build a strategy that always returns the same text.

    Usage:
        strategy = fixed_response_strategy("早安，今天過得如何？")
        backend = MockLLMBackend(response_strategy=strategy)
    """
    def strategy_fn(messages, model):
        return text
    return strategy_fn


def cycle_aware_strategy(responses: List[str]) -> Callable:
    """
    Build a strategy that returns different responses per call.

    Usage:
        strategy = cycle_aware_strategy([
            "第一次回應",
            "第二次回應",
            "第三次回應",
        ])
        backend = MockLLMBackend(response_strategy=strategy)
        # First call → "第一次回應"
        # Second call → "第二次回應"
        # ...
    """
    state = {"index": 0}

    def strategy_fn(messages, model):
        if state["index"] >= len(responses):
            return responses[-1]  # reuse last if exhausted
        text = responses[state["index"]]
        state["index"] += 1
        return text

    return strategy_fn


class MockLLMBackend(LLMBackend):
    """
    Deterministic mock of LLMBackend.

    Implements LLMBackend.complete() with a configurable response strategy.
    Tracks all calls for inspection.
    """

    def __init__(
        self,
        response_strategy: Optional[Callable[[List[Dict[str, str]], str], str]] = None,
    ):
        self.response_strategy = response_strategy or default_strategy
        self.call_count = 0
        self.last_messages: List[Dict[str, str]] = []
        self.last_model: str = ""
        self.call_log: List[Dict[str, Any]] = []

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        max_tokens: int = 500,
        temperature: float = 0.85,
        **kwargs: Any,
    ) -> str:
        """
        Return deterministic text based on messages and model.

        Records call for verification.
        """
        self.call_count += 1
        self.last_messages = list(messages)
        self.last_model = model
        self.call_log.append({
            "call_id": self.call_count,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": list(messages),
        })

        response = self.response_strategy(messages, model)
        logger.debug(
            f"[MockLLMBackend] call #{self.call_count} model={model} → {len(response)} chars"
        )
        return response

    def reset(self) -> None:
        """Reset call tracking (useful between scenarios)."""
        self.call_count = 0
        self.last_messages = []
        self.last_model = ""
        self.call_log = []

    def get_user_message(self) -> Optional[str]:
        """Helper: get the last user message from last call."""
        if not self.last_messages:
            return None
        for msg in reversed(self.last_messages):
            if msg.get("role") == "user":
                return msg.get("content", "")
        return None

    def get_system_message(self) -> Optional[str]:
        """Helper: get the system message from last call (for context inspection)."""
        if not self.last_messages:
            return None
        for msg in self.last_messages:
            if msg.get("role") == "system":
                return msg.get("content", "")
        return None
