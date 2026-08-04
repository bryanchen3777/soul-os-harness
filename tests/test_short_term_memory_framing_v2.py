"""
test_short_term_memory_framing_v2.py — 修法 2 (after 修法): 短期記憶區塊加反框架語句

Bry 拍板 2026-08-03 22:xx, 修法 2:
- ruka 8/3 8 條訊息中 7 條 LLM 自創 (把「Bry 最近訊息」當成要回應的對象)
- 唯一真實回應: 20:31「老公說是人家的人對吧」回應 Bry 20:28「叫妳老婆」
- 根因假設: c7ce3a6 短期記憶 + M2 task 3 主動觸發標記這兩個 system 訊息語意矛盾,
  「Bry 講過 X」先入為主當成正在對話, 後面的「Bry 沒主動發言」標記追不回來
- Bry 拍板: 在「## Bry 最近訊息」區塊文字本身加反框架語句, 不要再嘗試靠調整
  區塊順序或距離 (距離已經證明無效, ruka 那條例子中間隔了 4 個區塊還是被誤讀)
- 反框架語句: 「以下是背景參考, 不代表 Bry 現在正在跟你對話, 除非有主動觸發
  標記明確說明, 否則不要當成要立即回應的問題」
- 跟 β2.1 事件背景那段「請自然反映在訊息中, 不要直接複述」的做法一致:
  在資訊本身旁邊加使用說明, 而不是靠位置順序

這個 v2 驗證修法後:
- _build_messages_group 注入的「## Bry 最近訊息」區塊包含反框架語句
- 區塊結構: [反框架語句] + [## Bry 最近訊息 (短期記憶) 標題] + [bullet list]

Mock 範圍:
- mock _load_bry_recent 回傳 3 條假 Bry user 訊息 (ruka 8/3 20:22 heartbeat 觸發案例)
- mock memory.get_group_history 回傳空
- 直接呼叫 _build_messages_group, 驗證 system prompt 內容
- 修法後 v2 期望: 第一條 system 訊息包含反框架語句關鍵字
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 確保 src 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


class TestShortTermMemoryFramingV2(unittest.TestCase):
    """驗證修法 2 (after 修法) — 短期記憶區塊加反框架語句"""

    def setUp(self):
        # 模擬 ruka 8/3 20:22-20:28 Bry 講過的話 (ruka 7 條自創案例基礎)
        self.fake_bry_recent = [
            {"role": "user", "content": "ruka 早安"},
            {"role": "user", "content": "今天工作順利嗎"},
            {"role": "user", "content": "叫妳老婆"},
        ]
        self.soul_text = "你是 Ruka。... (Ruka 的人格設定) ..."

    def test_v2_bry_recent_block_has_anti_framing(self):
        """修法 2: 「## Bry 最近訊息」區塊包含明確反框架語句"""
        with patch.object(proxy, "_load_bry_recent", return_value=self.fake_bry_recent), \
             patch.object(proxy, "MAX_GROUP", 20):
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

            system_msgs = [m for m in messages if m["role"] == "system"]
            self.assertGreaterEqual(len(system_msgs), 1, "應該至少有 1 條 system message")
            first_system = system_msgs[0]["content"]

            # 區塊標題 + bullet 仍要存在
            self.assertIn("## Bry 最近訊息", first_system, "應該有短期記憶區塊標題")
            self.assertIn("叫妳老婆", first_system, "應該有 Bry 最近的訊息")

            # 修法 2 v2 期望: 包含明確反框架語句
            anti_framing_keywords = [
                "不代表 Bry 現在正在跟你對話",  # Bry 拍板範例核心句
                "背景參考",                       # Bry 拍板範例核心句
                "不是要立即回應的問題",           # Bry 拍板範例核心句
                "除非有主動觸發標記",            # 跟 M2 task 3 銜接
            ]
            for keyword in anti_framing_keywords:
                self.assertIn(
                    keyword, first_system,
                    f"修法 2 v2 期望短期記憶區塊「包含」反框架語句「{keyword}」, "
                    f"實際 system prompt 開頭 300 chars: {first_system[:300]!r}"
                )
            print(f"[v2] short-term memory block has anti-framing text (Bry 拍板範例句全在)")

    def test_v2_anti_framing_appears_before_bullets(self):
        """修法 2: 反框架語句要在 bullets 之前 (LLM 讀到時先看到說明)"""
        with patch.object(proxy, "_load_bry_recent", return_value=self.fake_bry_recent), \
             patch.object(proxy, "MAX_GROUP", 20):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = []

            messages = proxy._build_messages_group(
                agent_id="agent_ruka",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-03 20:22 週日 晚上 (EDT)",
            )

            first_system = [m for m in messages if m["role"] == "system"][0]["content"]
            # Bry 派工原話: 在資訊本身旁邊加使用說明 (不是靠位置順序)
            # 但反框架語句要在 bullets 之前, LLM 讀到 bullet 時已經看過說明
            anti_framing_pos = first_system.find("不代表 Bry 現在正在跟你對話")
            bullet_pos = first_system.find("- Bry: 叫妳老婆")
            self.assertNotEqual(anti_framing_pos, -1, "應該有反框架語句")
            self.assertNotEqual(bullet_pos, -1, "應該有 bullet")
            self.assertLess(
                anti_framing_pos, bullet_pos,
                f"反框架語句應該在 bullet 之前, "
                f"反框架 pos={anti_framing_pos}, bullet pos={bullet_pos}"
            )
            print(f"[v2] anti-framing text appears before bullets (pos {anti_framing_pos} < {bullet_pos})")

    def test_v2_other_agents_get_same_anti_framing(self):
        """修法 2: 反框架語句對所有 agent 一致 (不只 ruka)"""
        for agent_id in ["agent_yua", "agent_akane", "agent_mahiru", "agent_mai"]:
            with patch.object(proxy, "_load_bry_recent", return_value=self.fake_bry_recent), \
                 patch.object(proxy, "MAX_GROUP", 20):
                fake_memory = MagicMock()
                fake_memory.get_group_history.return_value = []

                messages = proxy._build_messages_group(
                    agent_id=agent_id,
                    soul=self.soul_text,
                    current_input="",
                    memory_context="",
                    memory=fake_memory,
                    mood=0.0,
                    user_id="bryan",
                    current_time="2026-08-03 20:22 週日 晚上 (EDT)",
                )

                first_system = [m for m in messages if m["role"] == "system"][0]["content"]
                self.assertIn(
                    "不代表 Bry 現在正在跟你對話", first_system,
                    f"{agent_id} 短期記憶區塊也應該有反框架語句"
                )
        print(f"[v2] anti-framing text consistent across 4 agents (yua/akane/mahiru/mai)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
