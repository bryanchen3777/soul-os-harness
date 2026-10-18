"""
test_short_term_memory_framing_v1.py — 修法 2 現況契約: 短期記憶區塊**有**反框架語句

Bry 拍板 2026-08-03 22:21:
- ruka 8/3 8 條訊息中 7 條 LLM 自創 (把「Bry 最近訊息」當成要回應的對象)
- 唯一真實回應: 20:31「老公說是人家的人對吧」回應 Bry 20:28「叫妳老婆」
- 根因假設: c7ce3a6 短期記憶 + M2 task 3 主動觸發標記這兩個 system 訊息語意矛盾,
  「Bry 講過 X」先入為主當成正在對話, 後面的「Bry 沒主動發言」標記追不回來
- Bry 拍板: 在「## Bry 最近訊息」區塊文字本身加反框架語句, 不要再嘗試靠調整區塊順序或距離
  (距離已經證明無效, ruka 那條例子中間隔了 4 個區塊還是被誤讀)

2026-10-12 校準: 本檔原為「修法前」baseline (斷言反框架語句**不存在**), 但修法早已落地。
production 正確, 測試過期 —— 校準為驗證現況契約 (不刪測試):
- proxy.py L860-864 `_build_messages_group` 內 bry_block_lines 首行即
  「[使用說明] 以下是背景參考, 不代表 Bry 現在正在跟你對話。」
- 修法 3 (Bry 2026-08-03 23:08) 另有群聊歷史段的 [使用說明] (proxy.py L884-889),
  與修法 2 同方向: 在資訊本身旁邊加使用說明, 不是靠位置順序。

🔴 2026-10-17 SOUL-BUG-008 (Owner P0 私域外洩修補) —— 本檔斷言**整體翻轉**:
「## Bry 最近訊息 (短期記憶)」區塊在**公開群聊**路徑已被徹底切除。
複核發現該區塊資料源 `_load_bry_recent()` → `_load_private()`, 等於把 Bry
與該角色的私聊原文逐字搬進公開 prompt (爆炸半徑 = 10 隻角色每次群聊發言)。
當初的修法 2 反框架語句是為了讓這個區塊「不被誤讀」而存在 —— 區塊沒了,
語句也就沒有存在理由, 留著只會誤導後人。故本檔改為守「公開頻道零私域」。

Mock 範圍:
- mock _load_bry_recent 回傳 3 條假 Bry user 訊息 (**刻意提供**, 證明即使
  私域資料可得, 群聊路徑也不會去取用)
- mock memory.get_group_history 回傳空 (避免 group history 路徑干擾)
- 直接呼叫 _build_messages_group, 驗證 system prompt 內容
- 現況期望: 私域內容與區塊標題**一律不在** prompt 內; 修法 3 的群聊歷史
  [使用說明] 仍完整保留
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# 確保 src 可 import
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


class TestShortTermMemoryFramingBaseline(unittest.TestCase):
    """SOUL-BUG-008 契約 (2026-10-17): 群聊 prompt 零 Bry 私域內容"""

    def setUp(self):
        # ruka 8/3 20:28 Bry 講「叫妳老婆」 — 模擬 Bry 跟 ruka 的 private history
        self.fake_bry_recent = [
            {"role": "user", "content": "ruka 早安"},
            {"role": "user", "content": "今天工作順利嗎"},
            {"role": "user", "content": "叫妳老婆"},
        ]
        # 模擬 ruka 8/3 20:22 heartbeat 觸發, 沒有 Bry 當下訊息 (Bry 沒主動發言)
        self.soul_text = "你是 Ruka。... (Ruka 的人格設定) ..."

    def test_group_prompt_has_no_bry_private_recent_block(self):
        """🔴 SOUL-BUG-008 (Owner 2026-10-17 P0) 契約翻轉: 群聊 prompt **不含**
        「## Bry 最近訊息」短期記憶區塊。

        沿革 (為什麼這支測試的斷言方向必須翻轉, 而非當成退化):
        - 修法 2 (Bry 2026-08-03 22:21) 當初為了治「ruka 把 Bry 最近訊息當成
          正在對話而自創發言」, 在該區塊旁加了反框架語句 —— 前提是這個
          區塊**繼續存在**。
        - SOUL-BUG-008 複核發現: 該區塊的資料源是 `_load_bry_recent()` →
          `_load_private()`, 等於把 Bry 與該角色的**私聊原文逐字**搬進
          **公開群聊** system prompt。爆炸半徑 = 10 隻角色每次群聊發言。
        - Owner 裁示: 公開路徑徹底切除。**沒有內容就不需要防誤讀**,
          反框架語句隨之失效 —— 留著它只會讓後人誤以為該區塊還在。

        本測試守的不變量是「公開頻道零私域」, 不是實作形狀。
        """
        with patch.object(proxy, "_load_bry_recent", return_value=self.fake_bry_recent), \
             patch.object(proxy, "MAX_GROUP", 20):
            # mock memory 物件 —— **刻意給一則非私聊群聊訊息**:
            # 修法 3 的 [使用說明] 只在「group history 內有非 private 訊息」時
            # 才注入 (proxy.py `if group and not all(m.get("is_private") ...)`),
            # 若給空 history 會讓那條斷言 vacuous pass, 等於什麼都沒驗到。
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = [
                {
                    "speaker": "bryan",
                    "content": "豬頭你又在偷懶",
                    "is_private": False,
                    "timestamp": 1_700_000_000,
                },
            ]

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

            # 🔴 核心斷言: 區塊標題與私聊原文都不得出現在群聊 prompt
            self.assertNotIn(
                "## Bry 最近訊息", first_system,
                "公開群聊 prompt 不得包含 Bry 私聊短期記憶區塊標題 (SOUL-BUG-008)"
            )
            for secret in ("ruka 早安", "今天工作順利嗎", "叫妳老婆"):
                self.assertNotIn(
                    secret, first_system,
                    f"公開群聊 prompt 不得洩漏私聊原文「{secret}」 (SOUL-BUG-008)"
                )

            # 反框架語句隨區塊一併失效 —— 若有人日後把它加回來, 這裡會紅
            self.assertNotIn("不代表 Bry 現在正在跟你對話", first_system)

            # 修法 3 的**群聊歷史** [使用說明] 不受影響 (那條講的是群聊裡
            # 別人的話, 本來就是公開資訊), 必須仍然存在 —— 確保我沒有
            # 把整個「使用說明」機制一起誤刪。
            self.assertIn(
                "[使用說明] 以下是群聊中其他人", "\n".join(
                    m["content"] for m in system_msgs
                ),
                "修法 3 的群聊歷史使用說明必須保留 (只切 bry_recent, 不動修法 3)",
            )
            print("[v1 SOUL-BUG-008] group prompt carries ZERO Bry private content; 修法 3 framing intact")


if __name__ == "__main__":
    unittest.main(verbosity=2)
