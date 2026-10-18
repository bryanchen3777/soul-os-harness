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

🔴 2026-10-17 SOUL-BUG-008 (Owner P0 私域外洩修補) —— 本檔斷言**整體翻轉**:
「## Bry 最近訊息 (短期記憶)」區塊在公開群聊路徑已被徹底切除。複核發現它
的資料源是 `_load_bry_recent()` → `_load_private()`, 等於把 Bry 與該角色的
私聊原文逐字搬進公開 prompt。修法 2 的反框架語句隨區塊一併失效 —— 留著它
只會製造「修法仍在、內容已死」的幽靈契約。

Mock 範圍:
- mock _load_bry_recent 回傳 3 條假 Bry user 訊息 (**刻意提供**, 證明即使
  私域資料可得, 群聊路徑也不會取用)
- mock memory.get_group_history 回傳空
- 直接呼叫 _build_messages_group, 驗證 system prompt 內容
- 現況期望: 私域內容、區塊標題、反框架語句**一律不在** prompt 內
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

    def test_v2_group_has_no_bry_recent_block(self):
        """🔴 SOUL-BUG-008 (Owner 2026-10-17 P0) 契約翻轉:
        群聊 prompt **不含**「## Bry 最近訊息」區塊。

        修法 2 的反框架語句是為了讓這個區塊「不被誤讀」而存在; SOUL-BUG-008
        認定該區塊本身就是 P0 私域外洩 (資料源 `_load_bry_recent()` →
        `_load_private()`), 於是整段切除。區塊不存在 ⇒ 反框架語句不需要。
        """
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
            joined = "\n".join(m["content"] for m in system_msgs)

            self.assertNotIn("## Bry 最近訊息", joined, "群聊 prompt 不得有該區塊標題")
            self.assertNotIn("叫妳老婆", joined, "群聊 prompt 不得洩漏私聊原文")
            print("[v2 SOUL-BUG-008] group prompt carries zero Bry private content")

    def test_v2_group_has_no_stale_anti_framing_text(self):
        """區塊切除後, 反框架語句必須一併消失。

        沒有內容就不需要防 LLM 誤讀。若日後有人只把反框架語句加回來 (以為
        它是獨立功能), 這裡會紅 —— 避免留下「修法仍在、內容已死」的幽靈契約。
        """
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
            stale_keywords = [
                "不代表 Bry 現在正在跟你對話",
                "不是要立即回應的問題",
                "除非有主動觸發標記",
            ]
            for keyword in stale_keywords:
                self.assertNotIn(
                    keyword, first_system,
                    f"短期記憶區塊已切除, 反框架語句「{keyword}」不該單獨存在",
                )

    def test_v2_no_agent_leaks_bry_private_content(self):
        """群聊私域隔離對**所有** agent 一致 (爆炸半徑 = 全部 10 隻角色)"""
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

                joined = "\n".join(
                    m["content"] for m in messages if m["role"] == "system"
                )
                self.assertNotIn(
                    "叫妳老婆", joined,
                    f"{agent_id} 的群聊 prompt 洩漏了 Bry 私聊原文 (SOUL-BUG-008)"
                )
        print("[v2 SOUL-BUG-008] zero private leak across 4 agents (yua/akane/mahiru/mai)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
