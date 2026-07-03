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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.llm_judge import LLMJudge
from configs.loader import load_config, create_llm_proxy
from src.eventbus import SoulEventBus


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
