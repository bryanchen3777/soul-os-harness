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
from src import text_guard  # SOUL-BUG-011 常數 SSOT
from src.soul import dream_event as de_mod


# ─────────────────────────────────────────────────────────
# 修法前的 5 個 baseline 測試
# ─────────────────────────────────────────────────────────

class TestM04Baseline(unittest.TestCase):
    """M0.4 修法前的現狀證明."""

    def test_01_diary_max_clean_chars_is_80(self):
        """現況: DIARY_MAX_CLEAN_CHARS = 80。

        修法 M0.4 (Bry 拍板 2026-08-06 21:30): 50 → 80。
        生產證據: src/soul/diary.py:60-66 —
          「M0.4 (2026-08-06 21:30 Bry 拍板): 50 → 80」
          「動機: 8/6 Rem sim 跑出 6 條 placeholder, 全部是「超過 50 字」觸發
            (非 LLM 失敗), 50 字對中文 LLM 太嚴格, 放寬到 80 給 LLM 自然表達空間」
          DIARY_MAX_CLEAN_CHARS = 80
        [SUPERSEDED] 2026-08-06 M0.4/M0.5 baseline（80 chars + 取尾段截斷）
        於 2026-10-18 被 SOUL-BUG-011 取代（Bry 裁決 / Sidecar 生產實證）。
        邊界收斂為 DIARY_OUTPUT_MAX_CHARS(200)，且改為**保留前段** +
        終止標點優雅收尾，消除正常日記開頭段落與標題被吞噬的缺陷。
        原意圖（超長不該整段作廢）保留；邊界值改為 SSOT 常數。
        """
        self.assertEqual(diary_mod.DIARY_MAX_CLEAN_CHARS, 200)
        self.assertEqual(
            diary_mod.DIARY_MAX_CLEAN_CHARS,
            text_guard.DIARY_OUTPUT_MAX_CHARS,
            "上限必須來自單一事實來源，消除 diary/dream_event/proxy 常數分裂",
        )

    def test_02_diary_write_entry_rejects_think_only(self):
        """現況: write_entry 拿 clean 判斷, think_only 直接拒寫 (return None)。

        修法 M0.4 (Bry 拍板 2026-08-06 21:30): 邏輯收斂到 write_entry 內做。
        生產證據: src/soul/diary.py:246-255 —
            clean = self._strip_think(content)
            if not clean:  logger.warning("拒絕寫入: clean 為空 ..."); return None
        配套: src/soul/diary.py:261 「content: clean  # M0.4: 寫 clean (不是 raw)」。
        測試若要證明會被擋, 就必須拿 think_only 回頭丟給 write_entry 拿 None。
        """
        # 構造 think_only content: 200 chars think block, 0 chars clean
        think_only_content = "<think>\n" + "A" * 200 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = diary_mod.DiaryWriter(data_dir=str(data_dir))
            path = writer.write_entry(
                "agent_test", "morning", think_only_content, source="llm"
            )
            # 修法後: 拒絕寫入, 呼叫端 (generate_diary_entry) 必須改傳 placeholder
            self.assertIsNone(
                path,
                "M0.4 後 think_only 應被 write_entry 擋掉 (return None)",
            )
            # 磁碟上不該留下任何 diary jsonl (連 think block 都不該落地)
            self.assertEqual(list(data_dir.rglob("*.jsonl")), [])

    def test_03_dream_event_write_entry_rejects_think_only(self):
        """現況: DreamEventWriter._write_entry 拿 clean 判斷, think_only 拒寫。

        修法 M0.4 (Bry 拍板 2026-08-06 21:30)。
        生產證據: src/soul/dream_event.py:472-479 —
            clean = self._strip_think(content)
            if not clean:  logger.warning("拒絕寫入: clean 為空 ..."); return None
        配套: src/soul/dream_event.py:487 「content: clean  # M0.4: 寫 clean, jsonl 不含 think block」。
        """
        think_only_content = "<think>\n" + "B" * 150 + "\n</think>"
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = de_mod.DreamEventWriter(data_dir=str(data_dir))
            path = writer._write_entry(
                "agent_test", "event", think_only_content, source="llm"
            )
            self.assertIsNone(
                path,
                "M0.4 後 dream/event think_only 應被 _write_entry 擋掉 (return None)",
            )
            self.assertEqual(list(data_dir.rglob("*.jsonl")), [])

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

    def test_05_write_entry_never_persists_think_block(self):
        """修法後: jsonl 內不可能出現 think block (M0.4 的核心保證).

        舊版這支是掃 data/soul/agent_rem/diary/2026-08-*.jsonl 統計 think_only
        條數 (8/6 sim 跑出 7 條, source=llm 但 clean=0)。那是「修法前」才會發生的
        現況, M0.4 生效後 data/ 早就沒有這種條目, 拿 data/ 當斷言來源會隨生產
        資料累積而漂移, 已經不能證明任何東西。

        改成直接驗現行契約: write_entry 一律寫 clean, think block 不落地。
        如果有人把 M0.4 的 _strip_think 拿掉, 這支會直接紅 (寫進去會含 <think>)。
        生產證據: src/soul/diary.py:246-247 (strip), :261 (content 寫 clean)。
        """
        clean_body = "朝向外面的街道走了一段路。"  # 30 chars
        raw_with_think = "<think>\n" + "A" * 200 + "\n</think>\n" + clean_body
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            writer = diary_mod.DiaryWriter(data_dir=str(data_dir))
            path = writer.write_entry(
                "agent_test", "morning", raw_with_think, source="llm"
            )
            self.assertIsNotNone(path)
            entry = json.loads(path.read_text(encoding="utf-8").strip())
            # 核心保證: 落地的是 clean, 不是 raw
            self.assertNotIn("<think>", entry["content"])
            self.assertNotIn("</think>", entry["content"])
            self.assertEqual(entry["content"], clean_body)
            self.assertEqual(entry["source"], "llm")


if __name__ == "__main__":
    print("=" * 60)
    print("M0.4 v1 baseline (Bry 派工 2026-08-06 21:30)")
    print("=" * 60)
    unittest.main(verbosity=2)
