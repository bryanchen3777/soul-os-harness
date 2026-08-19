"""
test_m7_judge_batch.py
M7-judge-batch (Bry 拍板 2026-08-18): 把 LLM judge 從逐條串行改成批次。

驗證:
  A. 批次解析 (_parse_stance_batch_output / _parse_content_batch_output)
  B. extract_and_judge 用批次 (LLM 呼叫 1+N+2M=13 → 1+1+2=4)
  C. 輸出介面不變 (仍回 {subject,predicate,object,category,judgment,reason,confidence,stance})
"""
import asyncio

from src.memory.llm_judge import (
    LLMJudge,
    _parse_content_batch_output,
    _parse_stance_batch_output,
)


class MockBackend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def complete(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return self.responses.pop(0)


class MockProxy:
    def __init__(self, responses):
        self.model = "test-model"
        self.backend = MockBackend(responses)


class TestBatchParsers:
    def test_stance_batch_parses_and_aligns_by_index(self):
        raw = (
            '{"results": ['
            '{"index": 1, "stance": "self_directed", "judgment": "SUPPORTED", "reason": "r1"},'
            '{"index": 0, "stance": "other_directed", "judgment": "WEAK", "reason": "r2"}'
            "]}"
        )
        out = _parse_stance_batch_output(raw, 2)
        assert len(out) == 2
        assert out[0]["stance"] == "other_directed"
        assert out[0]["judgment"] == "WEAK"
        assert out[1]["stance"] == "self_directed"
        assert out[1]["judgment"] == "SUPPORTED"

    def test_stance_batch_missing_index_gets_default(self):
        raw = '{"results": [{"index": 0, "stance": "self_directed", "judgment": "SUPPORTED"}]}'
        out = _parse_stance_batch_output(raw, 3)
        assert len(out) == 3
        assert out[1]["stance"] == "other_directed"  # default
        assert out[1]["judgment"] == "UNSUPPORTED"
        assert out[2]["judgment"] == "UNSUPPORTED"

    def test_stance_batch_garbage_gets_all_default(self):
        out = _parse_stance_batch_output("not json at all", 2)
        assert len(out) == 2
        assert all(r["stance"] == "other_directed" for r in out)
        assert all(r["judgment"] == "UNSUPPORTED" for r in out)

    def test_content_batch_parses(self):
        raw = '{"results": [{"index": 0, "judgment": "SUPPORTED", "reason": "x"}]}'
        out = _parse_content_batch_output(raw, 2)
        assert out[0]["judgment"] == "SUPPORTED"
        assert out[1]["judgment"] == "UNSUPPORTED"  # default


class TestBatchJudgeCallCount:
    def test_extract_and_judge_uses_4_calls_not_13(self):
        """2 triples (1 other + 1 self) → 1 extract + 1 stance batch + 2 content = 4 calls。"""
        async def _run():
            proxy = MockProxy([
                # 1. extract → 2 triples
                '{"triples": ['
                '{"subject":"Bry","predicate":"吃了","object":"晚餐"},'
                '{"subject":"Bry","predicate":"感覺","object":"累"}'
                "]}",
                # 2. stance batch → 2 results (idx0 other, idx1 self)
                '{"results": ['
                '{"index":0,"stance":"other_directed","judgment":"SUPPORTED","reason":"r0"},'
                '{"index":1,"stance":"self_directed","judgment":"SUPPORTED","reason":"r1"}'
                "]}",
                # 3. content batch (preference) → 1 candidate (idx0)
                '{"results": [{"index":0,"judgment":"SUPPORTED","reason":"pref"}]}',
                # 4. content batch (milestone) → 1 candidate (idx0)
                '{"results": [{"index":0,"judgment":"WEAK","reason":"mile"}]}',
            ])
            judge = LLMJudge(proxy)
            facts = await judge.extract_and_judge(
                "Bry 吃了晚餐，感覺累", "", "agent_ruka"
            )
            return len(proxy.backend.calls), facts

        n_calls, facts = asyncio.run(_run())
        assert n_calls == 4, f"批次化後應 4 次 LLM call, 實際 {n_calls}"
        # 輸出介面不變: 2 facts (1 diary + 1 content)
        assert len(facts) == 2
        categories = {f["category"] for f in facts}
        assert "diary" in categories
        assert any(f["category"] in ("preference_plan_event_fact", "milestone") for f in facts)
        # 每個 fact 有 confidence / judgment / stance
        for f in facts:
            assert "confidence" in f
            assert "judgment" in f
            assert "stance" in f

    def test_extract_empty_returns_zero_results_one_call(self):
        """無三元組 → 只 1 次 extract call, 0 facts。"""
        async def _run():
            proxy = MockProxy([
                '{"triples": []}',  # extract → 0 triples
            ])
            judge = LLMJudge(proxy)
            facts = await judge.extract_and_judge("今天天氣很好", "", "agent_ruka")
            return len(proxy.backend.calls), facts

        n_calls, facts = asyncio.run(_run())
        assert n_calls == 1
        assert facts == []
