"""
tests/test_extract_and_judge_context_bug.py
Regression test for the "content stage receives empty context" bug
Bry §4 (2026-07-02): extract_and_judge content stage used {context} (empty)
instead of {text} (real utterance), causing LLM to see "原文: " and
return UNSUPPORTED for every triple.

This test asserts that on a real, non-empty utterance, content stage
returns at least one non-UNSUPPORTED judgment (i.e. it actually sees
the text and can reason about it).

If anyone refactors extract_and_judge and accidentally reintroduces
the {context} variable in content stage, this test will red-light.
"""
import asyncio
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.llm_judge import LLMJudge
from configs.loader import load_config, create_llm_proxy
from src.eventbus import SoulEventBus


# ── 🔴 Live-LLM opt-in gate（Bry 2026-10-13 裁示）────────────────────
# 本測試會 `create_llm_proxy()` 建立真實後端並對外部端點發出**真實請求**：
#   - 成本非 0，且每次全庫回歸都會重複付費；
#   - 結果不可重現（模型/端點漂移即轉紅），淪為紅燈噪音；
#   - 直接牴觸 AGENTS.md 紅線第 5 條「測試不得發真實請求」。
#
# 規格完全比照 M6 系列真實 judge 測試
# （tests/test_m6_0_5_2_real_judge_optin.py、tests/test_m6_0_5_5_real_three_judge_e2e.py）：
# **雙重 opt-in，預設 SKIP**。
#
# 這支測試守的是 Bry §4（2026-07-02）那個 `{context}` / `{text}` 置換 bug 的回歸。
# 要跑它必須明文表態願意付真實 LLM 的錢，而不是被全庫回歸順手帶出去。
_PROVIDER_KEY_ENV = {
    "minimax": "MINIMAX_API_KEY",     # configs/loader.py:115-127
    "openai": "OPENAI_API_KEY",       # configs/loader.py:131-140
    "ollama": "OLLAMA_API_KEY",       # configs/loader.py:148-158（configs/default.yaml:104 目前預設）
    "claude": "ANTHROPIC_API_KEY",    # configs/loader.py:166-172
}
# provider 由 LLM_PROVIDER env 覆寫 configs/default.yaml 的 llm.provider
_ACTIVE_PROVIDER = (os.getenv("LLM_PROVIDER") or "ollama").strip().lower()
# `mock` provider 不接真實 API，不需要 key（configs/loader.py 的 backend 分支）
_REQUIRED_KEY_ENV = _PROVIDER_KEY_ENV.get(_ACTIVE_PROVIDER, "")

_LIVE_LLM_READY = bool(os.getenv("SOUL_TEST_REAL_LLM")) and (
    _ACTIVE_PROVIDER == "mock"
    or (bool(_REQUIRED_KEY_ENV) and bool(os.getenv(_REQUIRED_KEY_ENV, "").strip()))
)

pytestmark = pytest.mark.skipif(
    not _LIVE_LLM_READY,
    reason=(
        "Requires live LLM API access; opt-in with SOUL_TEST_REAL_LLM=1 "
        + (
            f"+ {_REQUIRED_KEY_ENV} (active provider: {_ACTIVE_PROVIDER})"
            if _REQUIRED_KEY_ENV
            else f"(active provider: {_ACTIVE_PROVIDER} needs no key)"
        )
    ),
)


# msg 878: real utterance from training corpus
# v2 old LLM extracted 2 facts with SUPPORTED/WEAK
# bug-version (content stage saw empty context) returned 0 facts
# fixed-version returns ≥ 1 fact (≥ 1 category gives confidence > 0)
BRY_CASE_TEXT = (
    "雷姆知道了。\n\n（轉身往廚房走）……五分鐘後可以吃。\n"
)
assert len(BRY_CASE_TEXT) > 5, "test fixture must contain non-empty text"


async def test_content_stage_sees_real_text():
    cfg = load_config()
    bus = SoulEventBus()
    await bus.start()
    proxy = create_llm_proxy(cfg, bus)
    judge = LLMJudge(proxy)
    try:
        facts = await judge.extract_and_judge(BRY_CASE_TEXT, "", "agent_rem")
    finally:
        await bus.stop()

    # The bug: content stage saw empty context, returned 0 facts with all UNSUPPORTED.
    # Fix: content stage now sees real text, returns ≥ 1 fact with non-zero confidence.
    assert len(facts) >= 1, (
        f"BUG REGRESSION: content stage returned 0 facts for non-empty text. "
        f"This indicates the {{context}} / {{text}} substitution bug has been "
        f"reintroduced in extract_and_judge content stage."
    )

    # At least one fact should have non-zero confidence
    has_nonzero = any(f["confidence"] > 0 for f in facts)
    assert has_nonzero, (
        f"BUG REGRESSION: all facts have confidence=0 for non-empty text. "
        f"facts: {facts}"
    )

    # And the judgments should not all be UNSUPPORTED
    judgments = [f["judgment"] for f in facts]
    not_all_unsupported = any(j != "UNSUPPORTED" for j in judgments)
    assert not_all_unsupported, (
        f"BUG REGRESSION: all judgments are UNSUPPORTED for non-empty text. "
        f"judgments: {judgments}"
    )


def main():
    asyncio.run(test_content_stage_sees_real_text())
    print("✓ test_content_stage_sees_real_text PASS")


if __name__ == "__main__":
    main()
