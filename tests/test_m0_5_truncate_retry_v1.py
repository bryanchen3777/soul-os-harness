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

    def test_02_diary_over_80_writes_truncated_llm(self):
        """現況: 81+ chars clean 走截斷 (保留 LLM 內容), 不是整段丟棄走 placeholder.

        修法 M0.5 (Bry 派工 2026-08-06 21:44): 「把『超過 80 字』的處理方式從整段
        丟棄改模板, 改成沿用修法 10 那個截斷邏輯」。
        生產證據: src/soul/diary.py:409-421 —
            if len(clean) > DIARY_MAX_CLEAN_CHARS:
                truncated = _safe_truncate_on_length(clean, max_chars=DIARY_MAX_CLEAN_CHARS)
                return writer.write_entry(..., truncated, source="llm")
        截斷語義 (src/llm/proxy.py:2836-2856): 取最後 max_chars, 有標點切在最後
        標點, 沒標點才 fallback 硬切。本例是 100 個「あ」沒標點 → fallback 硬切 80。
        """
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
                # M0.5 後: 超長 → 截斷保留 LLM 真實內容, source 仍是 llm
                self.assertEqual(
                    entry["source"], "llm",
                    "M0.5 後 100 chars 應截斷保留 (source=llm), 不是整段 placeholder",
                )
                self.assertNotEqual(
                    entry["content"], clean_100,
                    "M0.5 後超長內容會被截斷, 不會整段 100 chars 原樣寫入",
                )
                self.assertLessEqual(len(entry["content"]), 80)
                # 截斷取的是 clean 的最後 80 字 (修法 10 語義)
                self.assertEqual(entry["content"], clean_100[-80:])
        asyncio.run(run_test())

    def test_03_diary_think_only_retries_once(self):
        """現況: think_only 會 retry 一次, retry 仍失敗才寫 placeholder.

        修法 M0.5 A2 (Bry 派工 2026-08-06 21:44): 「think_only 那部分才值得加
        retry — 這種情況沒東西可搶救, retry 一次是唯一辦法」。
        生產證據: src/soul/diary.py:391-408 —
            if not clean:  content = await _call_llm_for_diary(system, user + RETRY_HINT, ...)
            if not clean:  return writer.write_entry(..., placeholder, source="placeholder")
        """
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
                # M0.5 後: 1 次 content + 1 次 retry = 2 次 (只 retry 一次, 不無限迴圈)
                self.assertEqual(call_count["n"], 2,
                                 "M0.5 A2 後 think_only 應 retry 一次, 共 call 2 次")
                # retry 還是 think_only → 才落到 placeholder
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
                self.assertNotIn("<think>", entry["content"])
        asyncio.run(run_test())

    def test_04_dream_event_think_only_retries_once(self):
        """現況: write_dream 對 think_only retry 一次.

        計算 LLM 呼叫次數:
        - M0.5 前: 1 次 content + 1 次 impression = 2 次
        - M0.5 後: 1 次 content + 1 次 retry + 1 次 impression = 3 次
        生產證據: src/soul/dream_event.py:572-586 —
            if not clean_check:  content = await _call_llm_for_dream_event(system, user + RETRY_HINT, ...)
            if not clean:  fallback placeholder
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
                # M0.5 後: 1 content + 1 retry + 1 impression = 3 次
                self.assertEqual(call_count["n"], 3,
                                 "M0.5 A2 後 dream think_only 應 retry 一次, 共 call 3 次")
        asyncio.run(run_test())

    def test_05_dream_event_over_80_is_truncated(self):
        """現況: write_dream 對超長 clean 走截斷 (≤ 80), 不是整段寫入.

        修法 M0.5 A1 (Bry 派工 2026-08-06 21:44): 沿用修法 10 截斷。
        生產證據: src/soul/dream_event.py:591-596 (dream) / :707-711 (event) —
            elif len(clean) > DREAM_EVENT_MAX_CLEAN_CHARS:
                truncated = _safe_truncate_on_length(clean, max_chars=DREAM_EVENT_MAX_CLEAN_CHARS)
        常數 DREAM_EVENT_MAX_CLEAN_CHARS = 80 (src/soul/dream_event.py:65-67)。
        """
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
                # M0.5 後: 截斷到 80 字內 (修法 10 取最後 80 字, 沒標點 fallback 硬切)
                self.assertLessEqual(len(entry["content"]), 80)
                self.assertEqual(entry["content"], clean_120[-80:])
                self.assertEqual(entry["source"], "llm")
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

    def test_07_over_80_rescued_as_llm_and_retry_rescues_think_only(self):
        """M0.5 的實際價值: 超長截斷救回 + think_only retry 救回 (都算 llm)。

        舊版這支是掃 data/soul/agent_rem/diary/2026-08-*.jsonl 抓
        llm=15 / placeholder=13 / has_think=0 的 8/6 sim 快照。那是 M0.4 修法當天
        的生產資料快照, data/ 會隨時間累積漂移 (實測已變 54/9/1), 拿它當斷言來源
        既不穩定也證明不了現行行為。

        改成直接驗現行契約, 而且補上舊版沒驗到的兩件事:
        - A1 截斷走「句號斷點」分支 (test_02 驗的是沒標點的 fallback 硬切分支)
        - A2 retry 成功 → 真的救回成 source=llm (test_03 驗的是 retry 仍失敗 → placeholder)
        生產證據: src/llm/proxy.py:2847-2850 (有標點切在最後標點)、
                  src/soul/diary.py:391-421 (retry + 截斷都保留 LLM 內容)。
        """
        # ── A1: 超長且有句號 → 截斷在句號斷點, 不是硬切也不是 placeholder ──
        # raw 長度 90 + 1 + 40 = 131; 最後 80 字 = 39 個「あ」+「。」+ 40 個「い」
        punctuated = "あ" * 90 + "。" + "い" * 40
        # ── A2: 第一次 think_only, retry 回真內容 → 救回 llm ──
        think_only = "<think>\n只有推理\n</think>"
        rescued = "起き上がると、窓の外が白くなっていた。"

        async def run_test():
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(return_value=punctuated)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm",
                                 "M0.5 A1 後超長應截斷保留 (llm), 不是整段 placeholder")
                self.assertEqual(entry["content"], "あ" * 39 + "。",
                                 "應截在最後一個句號斷點 (修法 10 語義), 保留 LLM 原句")

            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                replies = [think_only, rescued]
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(side_effect=replies)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="night",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm",
                                 "M0.5 A2 後 retry 拿到真內容, 應救回成 llm 不是 placeholder")
                self.assertEqual(entry["content"], rescued)
                self.assertNotIn("<think>", entry["content"])
        asyncio.run(run_test())


if __name__ == "__main__":
    print("=" * 60)
    print("M0.5 v1 baseline (Bry 派工 2026-08-06 21:44)")
    print("=" * 60)
    unittest.main(verbosity=2)
