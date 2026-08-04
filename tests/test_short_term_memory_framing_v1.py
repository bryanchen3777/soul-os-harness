"""
test_short_term_memory_framing_v1.py — 修法 2 baseline: 短期記憶區塊無反框架語句

Bry 拍板 2026-08-03 22:21:
- ruka 8/3 8 條訊息中 7 條 LLM 自創 (把「Bry 最近訊息」當成要回應的對象)
- 唯一真實回應: 20:31「老公說是人家的人對吧」回應 Bry 20:28「叫妳老婆」
- 根因假設: c7ce3a6 短期記憶 + M2 task 3 主動觸發標記這兩個 system 訊息語意矛盾,
  「Bry 講過 X」先入為主當成正在對話, 後面的「Bry 沒主動發言」標記追不回來
- Bry 拍板: 在「## Bry 最近訊息」區塊文字本身加反框架語句, 不要再嘗試靠調整區塊順序或距離
  (距離已經證明無效, ruka 那條例子中間隔了 4 個區塊還是被誤讀)

這個 v1 驗證現狀 (before 修法):
- _build_messages_group 注入的「## Bry 最近訊息」區塊沒有反框架語句
- 只有「## Bry 最近訊息 (短期記憶)」標題 + bullet list, 沒說明「這是背景參考不代表 Bry 正在對話」

Mock 範圍:
- mock _load_bry_recent 回傳 3 條假 Bry user 訊息
- mock memory.get_group_history 回傳空 (避免 group history 路徑干擾)
- 直接呼叫 _build_messages_group, 驗證 system prompt 內容
- 修法前 v1 期望: 第一條 system 訊息沒有「不代表 Bry 現在正在跟你對話」之類的反框架句子
- 修法後 v2 期望: 包含明確反框架句子
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 確保 src 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


class TestShortTermMemoryFramingBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 2) — 短期記憶區塊無反框架語句"""

    def setUp(self):
        # ruka 8/3 20:28 Bry 講「叫妳老婆」 — 模擬 Bry 跟 ruka 的 private history
        self.fake_bry_recent = [
            {"role": "user", "content": "ruka 早安"},
            {"role": "user", "content": "今天工作順利嗎"},
            {"role": "user", "content": "叫妳老婆"},
        ]
        # 模擬 ruka 8/3 20:22 heartbeat 觸發, 沒有 Bry 當下訊息 (Bry 沒主動發言)
        self.soul_text = "你是 Ruka。... (Ruka 的人格設定) ..."

    def test_baseline_bry_recent_block_has_no_anti_framing(self):
        """Baseline: 「## Bry 最近訊息」區塊沒有反框架語句, 只有 bullet list"""
        # mock _load_bry_recent + memory.get_group_history
        with patch.object(proxy, "_load_bry_recent", return_value=self.fake_bry_recent), \
             patch.object(proxy, "MAX_GROUP", 20):
            # 構造 mock memory 物件
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = []

            messages = proxy._build_messages_group(
                agent_id="agent_ruka",
                soul=self.soul_text,
                current_input="",  # proactive 觸發, 沒有 Bry 當下輸入
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-03 20:22 週日 晚上 (EDT)",
            )

            # 找第一條 system message
            system_msgs = [m for m in messages if m["role"] == "system"]
            self.assertGreaterEqual(len(system_msgs), 1, "應該至少有 1 條 system message")
            first_system = system_msgs[0]["content"]

            # Baseline 期望: 包含「## Bry 最近訊息」標題 + 3 條 bullet, 但沒有反框架語句
            self.assertIn("## Bry 最近訊息", first_system, "應該有短期記憶區塊標題")
            self.assertIn("叫妳老婆", first_system, "應該有 Bry 最近的訊息")

            # 驗證反框架語句**不存在** (baseline)
            anti_framing_keywords = [
                "不代表 Bry 現在正在跟你對話",
                "背景參考",
                "不是要立即回應的問題",
                "除非有主動觸發標記",
            ]
            for keyword in anti_framing_keywords:
                self.assertNotIn(
                    keyword, first_system,
                    f"Baseline (v1) 期望短期記憶區塊「不」包含反框架語句「{keyword}」"
                    f", 實際 system prompt 開頭: {first_system[:300]!r}"
                )
            print(f"[v1 baseline] short-term memory block has no anti-framing text (v2 should add it)")
            print(f"  system prompt total chars: {len(first_system)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
