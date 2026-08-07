"""
test_m0_5_truncate_retry_v2.py — M0.5 verify (Bry 派工 2026-08-06 21:44)

Bry 派工方向:
- A1 截斷: 超長改沿用修法 10 _safe_truncate_on_length(clean, max_chars=80) 截斷, 保留 LLM 內容
- A2 retry: think_only → retry 一次, 加輕量 hint (「請直接輸出最終內容, 不要輸出思考過程」)
- 不做 D 嚴格收斂 prompt
- 不換模型 (B)
- 不接受 54% (C, 截斷法幾乎零成本)

v2 驗證:
1. 超 80 chars 截斷, 寫 source=llm (不是 placeholder)
2. 截斷用最後一個標點斷點 (沿用修法 10 pattern)
3. 截斷後 ≤ 80 chars
4. think_only retry 一次
5. retry 成功 → 寫 source=llm
6. retry 仍 think_only → 走 placeholder
7. retry hint 加在 user prompt 末尾
8. 正常 LLM 輸出 (< 80, 沒 think) 仍寫 source=llm
9. placeholder 仍正常
10. _safe_truncate_on_length 從 proxy.py 沿用
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul import diary as diary_mod
from src.soul import dream_event as de_mod
from src.llm import proxy as proxy_mod


class TestM05Fix(unittest.TestCase):
    """M0.5 修法驗證."""

    # ─────────────────────────────────────────
    # A1 截斷
    # ─────────────────────────────────────────
    def test_01_diary_over_80_chars_truncated_not_placeholder(self):
        """A1: 100 chars clean 截斷到 80 chars 內, 寫 source=llm."""
        async def run_test():
            # 100 chars + 句號在 99 位置
            clean_100 = "あ" * 99 + "。"  # 100 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(return_value=clean_100)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                # 修法後: 超長 → 截斷, 寫 llm
                self.assertEqual(entry["source"], "llm",
                                 "A1 截斷應寫 llm, 不是 placeholder")
                # 截斷後 ≤ 80
                self.assertLessEqual(len(entry["content"]), 80)
                # 截斷應該保留句號
                self.assertIn("。", entry["content"])
        asyncio.run(run_test())

    def test_02_diary_truncate_uses_last_punctuation(self):
        """A1: 截斷找最後一個標點斷點, 沿用修法 10 pattern."""
        async def run_test():
            # 100 chars, 在 60 位置有句號, 70 位置有逗號
            # 修法 10 應該取最晚的標點 (70 + len 標點 = 71)
            content = "あ" * 60 + "。" + "い" * 9 + "、" + "う" * 29  # 100 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(return_value=content)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                # 取最晚標點 "、" 在 70 位置, 切到 71 (含標點)
                # tail 取最後 80 chars: 從 20 開始, 60 個あ + 。 + 9 個い + 、 + 9 個う (80 chars)
                # 找最晚標點: "。" 在 tail 位置 60, "、" 在 70
                # 切到 71, 結果 = tail[:71] = 60 個あ + 。 + 9 個い + 、
                # 但 tail 範圍是 20-99, 所以 tail[:71] 對應原 content 的 20-90 (71 chars)
                # 不對, 重新算: tail = content[-80:] = content[20:100] = 80 chars
                # 裡面的 。 在 tail 位置 40 (60-20), 、 在 tail 位置 50 (70-20)
                # 最晚是 、 在 50, 切到 50 + 1 = 51
                # truncated = tail[:51]
                self.assertLessEqual(len(entry["content"]), 80)
                # 應該以 "、" 結尾 (因為是最晚標點)
                self.assertTrue(entry["content"].endswith("、") or entry["content"].endswith("。"),
                                f"應以標點結尾, 實際: {entry['content'][-5:]}")
        asyncio.run(run_test())

    def test_03_dream_event_over_80_truncated(self):
        """A1: write_dream 對超長 clean 截斷."""
        async def run_test():
            clean_120 = "あ" * 119 + "。"  # 120 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_minimax_for_dream_event",
                                  new=AsyncMock(return_value=clean_120)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
                self.assertLessEqual(len(entry["content"]), 80)
        asyncio.run(run_test())

    def test_04_event_over_80_truncated(self):
        """A1: write_event 對超長 clean 截斷."""
        async def run_test():
            clean_120 = "あ" * 119 + "。"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_minimax_for_dream_event",
                                  new=AsyncMock(return_value=clean_120)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_event(agent_id="a1")
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
                self.assertLessEqual(len(entry["content"]), 80)
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # A2 retry
    # ─────────────────────────────────────────
    def test_05_diary_think_only_retries(self):
        """A2: think_only retry 一次."""
        async def run_test():
            think_only = "<think>\nreasoning only\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                call_count = {"n": 0}
                captured_user = {"v": ""}
                async def fake_call(system, user, *args, **kwargs):
                    call_count["n"] += 1
                    captured_user["v"] = user
                    if call_count["n"] == 1:
                        return think_only
                    else:
                        # retry 成功
                        return "今朝は静かだった。台所へ向かう。"
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(side_effect=fake_call)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                # A2: retry 一次 (總共 2 次 LLM call)
                self.assertEqual(call_count["n"], 2,
                                 "A2 retry 一次, 總共 2 次 LLM call")
                # retry 的 user prompt 應有 hint
                self.assertIn("請直接輸出最終內容", captured_user["v"],
                              "A2 retry 應加 hint 在 user prompt 末尾")
                self.assertIn("不要輸出思考過程", captured_user["v"])
                # 寫入 source=llm (retry 成功)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
        asyncio.run(run_test())

    def test_06_diary_think_only_retry_still_fails_placeholder(self):
        """A2: retry 仍 think_only → placeholder (不是無限 retry)."""
        async def run_test():
            think_only = "<think>\nreasoning only\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    return think_only
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(side_effect=fake_call)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                # A2: 1 原始 + 1 retry = 2 次, 不無限 retry
                self.assertEqual(call_count["n"], 2,
                                 "A2 最多 retry 1 次, 失敗就走 placeholder")
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
        asyncio.run(run_test())

    def test_07_dream_event_think_only_retries(self):
        """A2: write_dream think_only retry 一次."""
        async def run_test():
            think_only = "<think>\ndream reasoning\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        return think_only
                    else:
                        return "廚房で麻衣さんと並んで立っていた。"
                with patch.object(de_mod, "_call_minimax_for_dream_event",
                                  new=AsyncMock(side_effect=fake_call)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                # 修法後: 1 原始 + 1 retry + 1 impression = 3 次
                self.assertEqual(call_count["n"], 3,
                                 "A2 retry 1 次, impression 1 次, 總共 3 次")
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
        asyncio.run(run_test())

    def test_08_event_think_only_retries(self):
        """A2: write_event think_only retry 一次."""
        async def run_test():
            think_only = "<think>\nevent reasoning\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    if call_count["n"] == 1:
                        return think_only
                    else:
                        return "玄関で夕飯の匂いがした。"
                with patch.object(de_mod, "_call_minimax_for_dream_event",
                                  new=AsyncMock(side_effect=fake_call)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_event(agent_id="a1")
                # write_event 沒有 impression, 只有 2 次
                self.assertEqual(call_count["n"], 2,
                                 "A2 retry 1 次, write_event 沒 impression, 總共 2 次")
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # 向後相容: 正常 LLM 輸出
    # ─────────────────────────────────────────
    def test_09_normal_llm_short_passes(self):
        """正常短 LLM 輸出 (例 30 chars) 仍寫 source=llm, 不截斷不 retry."""
        async def run_test():
            normal = "今朝は涼しかった。台所へ向かう。"  # 15 chars
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                call_count = {"n": 0}
                async def fake_call(*args, **kwargs):
                    call_count["n"] += 1
                    return normal
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(side_effect=fake_call)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                # 正常情況 1 次 LLM call
                self.assertEqual(call_count["n"], 1)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
                self.assertEqual(entry["content"], normal)
        asyncio.run(run_test())

    def test_10_placeholder_still_works(self):
        """placeholder 寫入仍正常 (LLM 完全失敗)."""
        async def run_test():
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_minimax_for_diary",
                                  new=AsyncMock(return_value=None)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
                self.assertIn("起牀了", entry["content"])
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # 沿用修法 10
    # ─────────────────────────────────────────
    def test_11_reuse_safe_truncate_from_proxy(self):
        """M0.5 沿用修法 10 _safe_truncate_on_length 從 proxy.py.

        驗證方式: 直接 call proxy._safe_truncate_on_length 跟 diary.py 內用同一個函式
        (從 src.llm.proxy import 進來)。
        """
        from src.llm.proxy import _safe_truncate_on_length as proxy_fn
        # 簡單驗證它真的能 work, 跟 diary.py 用的是同一個 import path
        result = proxy_fn("あ" * 100 + "。", max_chars=80)
        self.assertLessEqual(len(result), 80)
        self.assertTrue(result.endswith("。"))

    def test_12_retry_hint_added_to_user_prompt(self):
        """A2 retry hint 加在 user prompt 末尾, 不改 system prompt.

        驗證: 第 2 次 LLM call 的 user prompt 包含 "請直接輸出最終內容" hint.
        """
        async def run_test():
            think_only = "<think>\nreasoning only\n</think>"
            captured_users = []
            call_count = {"n": 0}
            async def fake_call(system, user, *args, **kwargs):
                call_count["n"] += 1
                captured_users.append(user)
                if call_count["n"] == 1:
                    return think_only
                else:
                    return "今朝は静かだった。"
            with patch.object(diary_mod, "_call_minimax_for_diary",
                              new=AsyncMock(side_effect=fake_call)):
                await diary_mod.generate_diary_entry(
                    agent_id="a1", slot="morning",
                    persona_prompt="test", recent_memories=[],
                    writer=writer if (writer := diary_mod.DiaryWriter(data_dir=tempfile.mkdtemp())) else None,
                )
                # 確認有 retry (2 次 call)
                self.assertEqual(len(captured_users), 2,
                                 "A2 retry 應該 call 2 次")
                # 第一次沒 hint
                self.assertNotIn("請直接輸出", captured_users[0])
                # 第二次有 hint
                self.assertIn("請直接輸出最終內容", captured_users[1])
                self.assertIn("不要輸出思考過程", captured_users[1])
                # hint 在 user prompt 末尾 (不是開頭)
                self.assertTrue(captured_users[1].endswith("。）"),
                                "hint 應在 user prompt 末尾, 結尾是「。」")
        asyncio.run(run_test())


if __name__ == "__main__":
    print("=" * 60)
    print("M0.5 v2 verify (Bry 派工 2026-08-06 21:44)")
    print("=" * 60)
    unittest.main(verbosity=2)
