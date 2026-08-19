"""
test_m0_4_strip_think_v2.py — M0.4 verify (Bry 拍板 2026-08-06 21:30)

Bry 派工原文:
- think-only 的 7 條: write_entry 要拿 clean 判斷是否為空
- 50 字上限偏緊: 放寬到 80 字

v2 驗證修法:
1. DIARY_MAX_CLEAN_CHARS = 80 (從 50)
2. DiaryWriter.write_entry 寫入前 strip think, clean 空 → 拒絕寫入 (return None)
3. DiaryWriter.write_entry 寫入的是 clean (非 raw), jsonl 內不會有 think block
4. DreamEventWriter._write_entry 同樣邏輯
5. write_dream / write_event 對 LLM think_only 結果 fallback 寫 placeholder
6. generate_diary_entry 對 LLM 超過 80 chars 走 placeholder
7. 正常 LLM 輸出 (< 80 chars, 無 think) 仍寫 source=llm

預期 v2 結果: 8/6 重跑 sim 應有 80%+ 真實產出率 (從 54% 拉上來)
"""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul import diary as diary_mod
from src.soul import dream_event as de_mod


class TestM04Fix(unittest.TestCase):
    """M0.4 修法驗證."""

    # ─────────────────────────────────────────
    # 修法 1: DIARY_MAX_CLEAN_CHARS 50 → 80
    # ─────────────────────────────────────────
    def test_01_diary_max_clean_chars_is_80(self):
        """Bry 拍板: 50 → 80."""
        self.assertEqual(diary_mod.DIARY_MAX_CLEAN_CHARS, 80)

    # ─────────────────────────────────────────
    # 修法 2: write_entry 拒絕 think_only 寫入
    # ─────────────────────────────────────────
    def test_02_diary_write_entry_rejects_think_only(self):
        """think_only (raw 有 think, clean 空) 應該被拒絕寫入 (return None)."""
        think_only = "<think>\n" + "A" * 200 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            writer = diary_mod.DiaryWriter(data_dir=tmp)
            result = writer.write_entry("a1", "morning", think_only, source="llm")
            self.assertIsNone(result, "think_only 應該被擋掉 (return None)")

    def test_03_dream_event_write_entry_rejects_think_only(self):
        """DreamEventWriter 同樣擋 think_only."""
        think_only = "<think>\n" + "B" * 150 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            writer = de_mod.DreamEventWriter(data_dir=tmp)
            result = writer._write_entry("a1", "event", think_only, source="llm")
            self.assertIsNone(result, "think_only 應該被擋掉 (return None)")

    # ─────────────────────────────────────────
    # 修法 3: 寫入的是 clean (非 raw), jsonl 內不會有 think block
    # ─────────────────────────────────────────
    def test_04_diary_write_entry_strips_think_from_written_content(self):
        """LLM 輸出有 think + 實際 diary, write_entry 寫入的應該是 clean (無 think)."""
        raw = "<think>\nreasoning... 推理痕跡\n</think>\n\n今日の朝は静かだった。"
        with tempfile.TemporaryDirectory() as tmp:
            writer = diary_mod.DiaryWriter(data_dir=tmp)
            path = writer.write_entry("a1", "morning", raw, source="llm")
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            # 寫入的不該有 think block
            self.assertNotIn("<think>", entry["content"])
            # 寫入的是 clean
            self.assertEqual(entry["content"], "今日の朝は静かだった。")
            # source 保留為 LLM
            self.assertEqual(entry["source"], "llm")

    def test_05_dream_event_write_entry_strips_think_from_written_content(self):
        """DreamEventWriter 同樣寫 clean (非 raw)."""
        raw = "<think>\n推理痕跡\n</think>\n\n三玖がヘッドホンをしたまま、縁側で何かを探していた。"
        with tempfile.TemporaryDirectory() as tmp:
            writer = de_mod.DreamEventWriter(data_dir=tmp)
            path = writer._write_entry("a1", "dream", raw, source="llm")
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertNotIn("<think>", entry["content"])
            self.assertEqual(entry["content"], "三玖がヘッドホンをしたまま、縁側で何かを探していた。")

    # ─────────────────────────────────────────
    # 修法 4: 正常 LLM 輸出 (< 80 chars, 無 think) 仍寫 source=llm
    # ─────────────────────────────────────────
    def test_06_diary_normal_llm_passes(self):
        """正常 LLM 短輸出 (例 30 chars) 寫入 source=llm."""
        normal = "今朝は涼しかった。台所でラキの寝顔を見守った。"  # ~22 chars
        with tempfile.TemporaryDirectory() as tmp:
            writer = diary_mod.DiaryWriter(data_dir=tmp)
            path = writer.write_entry("a1", "morning", normal, source="llm")
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["content"], normal)
            self.assertEqual(entry["source"], "llm")

    def test_07_diary_60_chars_passes_now_50_would_have_failed(self):
        """60 chars 修法前 (50 上限) 走 placeholder, 修法後 (80 上限) 寫 llm."""
        clean_60 = "あ" * 60
        with tempfile.TemporaryDirectory() as tmp:
            writer = diary_mod.DiaryWriter(data_dir=tmp)
            # write_entry 自己不擋長度 (那是 generate_diary_entry 的事)
            # 這裡只驗證 write_entry 不擋 60 chars
            path = writer.write_entry("a1", "morning", clean_60, source="llm")
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["source"], "llm")

    def test_08_diary_85_chars_truncated_in_generate(self):
        """M0.5 (Bry 派工 2026-08-06 21:44): 85 chars 超 80 上限, 截斷保留 LLM 內容.

        修法前 (M0.4): 85 chars → placeholder
        修法後 (M0.5): 85 chars → 截斷到 ≤80 chars, 寫 source=llm (沿用修法 10)
        """
        async def run_test():
            clean_85 = "あ" * 84 + "。"  # 85 chars, 句號在最後
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(return_value=clean_85)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                # M0.5: 超長 → 截斷, 寫 llm
                self.assertEqual(entry["source"], "llm")
                self.assertLessEqual(len(entry["content"]), 80)
                # placeholder 內容不該出現
                self.assertNotIn("起牀了", entry["content"])
        asyncio.run(run_test())

    def test_09_diary_70_chars_passes_in_generate(self):
        """70 chars 在 80 上限內, 應寫 source=llm (修法前會被擋)."""
        async def run_test():
            clean_70 = "あ" * 70
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(return_value=clean_70)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
        asyncio.run(run_test())

    def test_10_diary_think_only_in_generate_falls_back_to_placeholder(self):
        """LLM 只回 think block, generate_diary_entry 應走 placeholder."""
        async def run_test():
            think_only = "<think>\nreasoning only, no actual diary\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                with patch.object(diary_mod, "_call_llm_for_diary",
                                  new=AsyncMock(return_value=think_only)):
                    path = await diary_mod.generate_diary_entry(
                        agent_id="a1", slot="morning",
                        persona_prompt="test", recent_memories=[],
                        writer=writer,
                    )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # 修法 5: write_dream / write_event 對 think_only fallback 寫 placeholder
    # ─────────────────────────────────────────
    def test_11_dream_write_think_only_falls_back_to_placeholder(self):
        """LLM dream 回 think_only, write_dream 應寫 placeholder (不是留空)."""
        async def run_test():
            think_only = "<think>\nI should describe a dream about Mai\nBut I forgot to write the dream\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(return_value=think_only)):
                    # mock on_dream / impression 跳過
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                self.assertIsNotNone(path, "應該寫 placeholder, 不能 return None")
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
                # 寫的應該是 dream fallback 模板
                self.assertIn("夢", entry["content"])
        asyncio.run(run_test())

    def test_12_event_write_think_only_falls_back_to_placeholder(self):
        """LLM event 回 think_only, write_event 應寫 placeholder."""
        async def run_test():
            think_only = "<think>\nI'll describe a kitchen event\nBut I forgot\n</think>"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(return_value=think_only)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_event(agent_id="a1")
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "placeholder")
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # 修法 6: 正常 dream / event LLM 仍寫 source=llm
    # ─────────────────────────────────────────
    def test_13_dream_write_normal_passes(self):
        """正常 dream LLM 輸出應寫 source=llm."""
        async def run_test():
            normal = "麻衣さんの声だけが聞こえた。何を言ったかは…思い出せない。"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(return_value=normal)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_dream(
                            agent_id="a1", target_agent_id="a2",
                            all_agents=["a1", "a2"],
                        )
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
                self.assertEqual(entry["content"], normal)
        asyncio.run(run_test())

    def test_14_event_write_normal_passes(self):
        """正常 event LLM 輸出應寫 source=llm."""
        async def run_test():
            normal = "玄関で夕飯の匂いにふと足を止めた。"
            with tempfile.TemporaryDirectory() as tmp:
                writer = de_mod.DreamEventWriter(data_dir=tmp)
                with patch.object(de_mod, "_call_llm_for_dream_event",
                                  new=AsyncMock(return_value=normal)):
                    with patch("src.soul.relationships.get_relationships_manager"):
                        path = await writer.write_event(agent_id="a1")
                self.assertIsNotNone(path)
                entry = json.loads(path.read_text(encoding="utf-8").strip())
                self.assertEqual(entry["source"], "llm")
                self.assertEqual(entry["content"], normal)
        asyncio.run(run_test())

    # ─────────────────────────────────────────
    # 修法 7: placeholder 自己寫入仍正常 (M0.4 不該擋 placeholder)
    # ─────────────────────────────────────────
    def test_15_placeholder_passes_strip_think(self):
        """placeholder 內容 (短中文) 寫入時 strip_think 不影響, 仍正常寫入."""
        with tempfile.TemporaryDirectory() as tmp:
            writer = diary_mod.DiaryWriter(data_dir=tmp)
            path = writer.write_entry(
                "a1", "morning", "（2026-08-09 早上）起牀了。窗外還沒什麼聲音。",
                source="placeholder",
            )
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["source"], "placeholder")
            self.assertIn("起牀了", entry["content"])

    # ─────────────────────────────────────────
    # 修法 8: jsonl 不會有 think_only 條目
    # ─────────────────────────────────────────
    def test_16_no_think_block_in_jsonl(self):
        """模擬跑 4 個 slot 各種情境, 最後 jsonl 內不該有任何 entry 含 <think>."""
        async def run_test():
            scenarios = [
                ("think_only", "<think>\nreasoning only\n</think>", "llm"),
                ("normal", "今朝は静かだった。", "llm"),
                ("think_with_content", "<think>\nreasoning\n</think>\n\n今日の朝は良い天気。", "llm"),
                ("placeholder", "（早上）起牀了。", "placeholder"),
            ]
            with tempfile.TemporaryDirectory() as tmp:
                writer = diary_mod.DiaryWriter(data_dir=tmp)
                for i, (name, content, source) in enumerate(scenarios):
                    p = writer.write_entry("a1", "morning", content, source=source)
                    if name == "think_only":
                        self.assertIsNone(p, f"{name} 應被擋")
                    else:
                        self.assertIsNotNone(p, f"{name} 應寫入")
                # 寫完讀回來
                jsonl_path = Path(tmp) / "a1" / "diary" / f"{__import__('datetime').datetime.now().strftime('%Y-%m-%d')}.jsonl"
                contents = jsonl_path.read_text(encoding="utf-8")
                self.assertNotIn("<think>", contents, "jsonl 內不該有 think block")
        asyncio.run(run_test())


if __name__ == "__main__":
    print("=" * 60)
    print("M0.4 v2 verify (Bry 派工 2026-08-06 21:30)")
    print("=" * 60)
    unittest.main(verbosity=2)
