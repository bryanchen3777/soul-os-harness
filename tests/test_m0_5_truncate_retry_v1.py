"""
test_m0_5_truncate_retry_v1.py — M0.5 baseline (Bry 拍板 2026-08-06 21:44)

Bry 派工原文 (2026-08-06 21:44):
「把『超過 80 字』的處理方式從整段丟棄改模板，改成沿用修法 10 那個截斷邏輯
（取最後一個完整句子/標點斷點），保留 LLM 真正寫出來的內容，只是裁短，不是整段作廢」

「think_only 那部分（LLM 只在腦內想、什麼都沒寫出來）才值得加 retry —
這種情況沒東西可搶救，retry 一次是唯一辦法。但 retry 的提示只加
『請直接輸出最終內容，不要輸出思考過程』這種輕量提醒」

v1 證明現狀 (before M0.5):
- generate_diary_entry 對 len(clean) > 80 走 placeholder (整段丟棄)
- generate_diary_entry 對 think_only 走 placeholder (不 retry)
- write_dream / write_event 同樣邏輯
- _call_llm_for_diary / _call_llm_for_dream_event 沒 retry 邏輯

Bry 派工精神 (跟修法 10 對齊):
- 修法 10 在 proxy.py:1610 _safe_truncate_on_length(raw, max_chars=200)
  Bry 派工 200 字, 因為主對話 path max=97
- M0.5 在 diary / dream_event, max_chars=80 (Bry 8/6 21:30 從 50 放寬到 80)
"""
import asyncio
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul import diary as diary_mod
from src.soul import dream_event as de_mod
from src.llm import proxy as proxy_mod


class TestM05Baseline(unittest.TestCase):
    """M0.5 修法前的現狀證明."""

    def test_01_diary_max_clean_chars_is_80(self):
        """M0.4 已經從 50 改到 80, M0.5 沿用 80."""
        self.assertEqual(diary_mod.DIARY_MAX_CLEAN_CHARS, 80)

    def test_02_diary_over_80_writes_placeholder_not_truncated(self):
        """現狀: 81+ chars clean 走 placeholder (整段丟棄), 修法後應截斷保留 LLM 內容."""
        async def run_test():
            clean_100 = "あ" * 100  # 100 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(return_value=clean_100)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                # 修法前: 超長 → placeholder (LLM 內容整段丟棄)
                self.assertEqual(entry["source"], "placeholder")
                self.assertNotEqual(entry["content"], clean_100,
                                    "修法前 100 chars 直接被 placeholder 頂替, 不是寫 llm")
        asyncio.run(run_test())

    def test_03_diary_think_only_no_retry(self):
        """現狀: think_only 直接走 placeholder, 修法後應 retry 一次."""
        async def run_test():
            think_only = "<think>\nreasoning only\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                # 第一次 LLM call 計數器
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    return think_only
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(side_effect=fake_call)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                # 修法前: 1 次 LLM call 就走 placeholder
                self.assertEqual(call_count["n"], 1, "修法前 think_only 沒 retry, 只 call 1 次")
        asyncio.run(run_test())

    def test_04_dream_event_think_only_no_retry(self):
        """現狀: write_dream 對 think_only 直接 placeholder, 修法後應 retry.

        計算 LLM 呼叫次數:
        - 修法前: 1 次 content + 1 次 impression = 2 次
        - 修法後: 1 次 content + 1 次 retry + 1 次 impression = 3 次 (retry 真的發生)
        """
        async def run_test():
            think_only = "<think>\ndream reasoning\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    return think_only
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(side_effect=fake_call)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                # 修法前: 1 次 content + 1 次 impression extraction = 2 次
                self.assertEqual(call_count["n"], 2,
                                 "修法前 think_only 沒 retry, 2 次 (1 content + 1 impression)")
                # 修法後: 應該 3 次 (1 content + 1 retry + 1 impression)
        asyncio.run(run_test())

    def test_05_dream_event_over_80_writes_placeholder(self):
        """現狀: write_dream 對超長 clean 走 placeholder, 修法後應截斷."""
        async def run_test():
            clean_120 = "あ" * 120  # 120 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(return_value=clean_120)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                # 修法前: 120 chars 沒截斷, 整段寫 llm
                # 修法後: 應該被截斷到 80 chars 內
                self.assertGreater(len(entry["content"]), 80,
                                   "修法前沒截斷, 120 chars 整段寫進去; 修法後應 ≤ 80")
        asyncio.run(run_test())

    def test_06_proxy_has_safe_truncate_helper(self):
        """驗證修法 10 的 _safe_truncate_on_length 還在 proxy.py, M0.5 要重用."""
        self.assertTrue(hasattr(proxy_mod, "_safe_truncate_on_length"),
                        "proxy.py 應該有 _safe_truncate_on_length 修法 10 的 helper")
        # 抽 100 chars 全日文 + 一個句號測試截斷
        sample = "あ" * 100 + "。"  # 101 chars, 句號在 101
        truncated = proxy_mod._safe_truncate_on_length(sample, max_chars=80)
        # 取最後 80 字範圍是 21 個あ + "あ" * 79 + "。" = 80 chars
        # tail 範圍內找 "。" 的位置 = 80 (在最後一個字)
        # 截斷後長度 = 80
        self.assertLessEqual(len(truncated), 80)
        # 應該保留結尾的 "。"
        self.assertTrue(truncated.endswith("。") or "。" in truncated)

    def test_07_real_sim_post_m0_4_stats(self):
        """M0.4 修法後 8/6 sim 真實產出率: 54% (15 llm / 13 placeholder / 0 think_only).

        M0.5 預期:
        - 截斷 A1 應救回 ~4 條超長 placeholder → llm (約 80→80 chars)
        - retry A2 應救回 ~8 條 think_only placeholder → 至少 4 條 llm (50% retry 成功率)
        - 預期真實產出率: 54% + 4 + 4 = 23/28 = 82% 達 Bry 80% 門檻
        """
        workspace = Path("C:/Users/bbfcc/.local/bin/soul-os-harness")
        diary_dir = workspace / "data" / "soul" / "agent_rem" / "diary"
        llm, placeholder, think_only, has_think = 0, 0, 0, 0
        for f in sorted(diary_dir.glob("2026-08-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip(): continue
                e = json.loads(line)
                if "<think>" in e["content"]: has_think += 1
                if e.get("source") == "placeholder": placeholder += 1
                else: llm += 1
        print(f"\n  [baseline M0.4 後 8/6 sim] LLM: {llm} / placeholder: {placeholder} / think_in_jsonl: {has_think}")
        self.assertEqual(llm, 15)
        self.assertEqual(placeholder, 13)
        self.assertEqual(has_think, 0)


if __name__ == "__main__":
    print("=" * 60)
    print("M0.5 v1 baseline (Bry 派工 2026-08-06 21:44)")
    print("=" * 60)
    unittest.main(verbosity=2)
