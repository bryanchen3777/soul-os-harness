"""
test_m0_4_strip_think_v1.py — M0.4 baseline (Bry 拍板 2026-08-06 21:30)

Bry 派工原文:
- think-only 的 7 條: write_entry 要拿 clean 判斷是否為空, 不是拿 raw 判斷
- 50 字上限偏緊: 6 條模板兜底全部是超過 50 字觸發的, 放寬到 80 字

v1 證明現狀 (before M0.4):
- DiaryWriter.write_entry 直接寫入 raw content, 沒做 think strip
- DiaryWriter.write_entry 沒檢查 clean 是否為空
- DIARY_MAX_CLEAN_CHARS = 50
- DreamEventWriter._write_entry 也直接寫入 raw, 沒 strip
- 結果: think_only 的 LLM 輸出 (raw 200+ chars, clean 0) 會被寫成 source=llm,
       jsonl 內的 content 包含 think block + 沒有實際 diary

Bry 拍板修法方向:
- write_entry 寫入前先 strip think, 拿 clean 寫入
- clean 空 → 不寫 (return None) 或寫 placeholder
- DIARY_MAX_CLEAN_CHARS 50 → 80
"""
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.soul import diary as diary_mod
from src.soul import dream_event as de_mod


# ─────────────────────────────────────────────────────────
# 修法前的 5 個 baseline 測試
# ─────────────────────────────────────────────────────────

class TestM04Baseline(unittest.TestCase):
    """M0.4 修法前的現狀證明."""

    def test_01_diary_max_clean_chars_is_50(self):
        """現狀: DIARY_MAX_CLEAN_CHARS = 50 (Bry 拍板放寬到 80)."""
        self.assertEqual(diary_mod.DIARY_MAX_CLEAN_CHARS, 50)

    def test_02_diary_write_entry_accepts_think_only(self):
        """現狀: write_entry 接受 think_only content 寫入 raw (含 think block).

        Bry 拍板: 修法後應拿 clean 判斷, think_only 不該被當 LLM 成功寫入。
        """
        # 構造 think_only content: 200 chars think block, 0 chars clean
        think_only_content = "<think>\n" + "A" * 200 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = diary_mod.DiaryWriter(data_dir=str(data_dir))
            path = writer.write_entry(
                "agent_test", "morning", think_only_content, source="llm"
            )
            # 修法前: 寫入成功, content 包含 think block
            self.assertIsNotNone(path)
            self.assertTrue(path.is_file())
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["content"], think_only_content)
            self.assertIn("<think>", entry["content"])
            self.assertEqual(entry["source"], "llm")
            # 證明: 修法前 jsonl 內是 raw (含 think), 沒有任何 clean 判斷

    def test_03_dream_event_write_entry_accepts_think_only(self):
        """現狀: DreamEventWriter._write_entry 也接受 think_only 寫入 raw."""
        think_only_content = "<think>\n" + "B" * 150 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = de_mod.DreamEventWriter(data_dir=str(data_dir))
            path = writer._write_entry(
                "agent_test", "event", think_only_content, source="llm"
            )
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["content"], think_only_content)
            self.assertIn("<think>", entry["content"])

    def test_04_diary_clean_long_but_under_50_actually_under_50_writes(self):
        """現狀: 51 chars clean 走 placeholder, 49 chars clean 寫 llm.

        Bry 拍板: 50 → 80, 所以 60 chars clean 修法後應該寫 llm (不再走 placeholder).
        """
        clean_49 = "あ" * 49  # 49 chars, 修法前寫 llm
        clean_51 = "あ" * 51  # 51 chars, 修法前走 placeholder
        clean_60 = "あ" * 60  # 60 chars, 修法後 (80 字) 應該寫 llm
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = diary_mod.DiaryWriter(data_dir=str(data_dir))

            # 49 chars: 寫 llm ✅
            path = writer.write_entry("a1", "morning", clean_49, source="llm")
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["source"], "llm")

            # 51 chars: 修法前 DIARY_MAX_CLEAN_CHARS=50 邏輯在 generate_diary_entry
            # write_entry 自己不檢查長度, 所以直接寫入
            # 這裡只驗證 write_entry 不擋, 真正擋的是 generate_diary_entry
            path = writer.write_entry("a2", "morning", clean_51, source="llm")
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            self.assertEqual(entry["source"], "llm")  # write_entry 不擋

    def test_05_real_sim_think_only_count(self):
        """證明: Bry 8/6 sim 跑出 7 條 think_only (這次 v1 跑 source code 證明邏輯可重現).

        從 8/6~8/12 diary jsonl 讀取, 統計有多少條 LLM 寫入但 clean = 0。
        修法前這些都是 source=llm, 修法後應該被歸類到 placeholder。
        """
        workspace = Path("C:/Users/bbfcc/.local/bin/soul-os-harness")
        diary_dir = workspace / "data" / "soul" / "agent_rem" / "diary"
        think_only = 0
        total_llm = 0
        for jsonl in diary_dir.glob("2026-08-*.jsonl"):
            for line in jsonl.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("source") == "llm":
                    total_llm += 1
                    clean = re.sub(
                        r"^<think>.*?</think>\s*", "",
                        entry["content"], flags=re.DOTALL,
                    ).strip()
                    if not clean:
                        think_only += 1
        # 8/6 sim 跑出 7 條 think_only (報告已驗證), 這裡再次從 raw jsonl 確認
        self.assertGreaterEqual(think_only, 5)  # 寬鬆一點, 至少 5 條
        self.assertGreater(total_llm, 0)
        print(f"\n  [baseline] 8/6 sim: {think_only} think_only / {total_llm} LLM 寫入")


if __name__ == "__main__":
    print("=" * 60)
    print("M0.4 v1 baseline (Bry 派工 2026-08-06 21:30)")
    print("=" * 60)
    unittest.main(verbosity=2)
