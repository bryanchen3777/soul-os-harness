"""
test_group_history_framing_v2.py — 修法 3 (after 修法): 群聊歷史反框架語句

Bry 拍板 2026-08-03 23:08, 修法 3 措辭調整版:
- 反框架語句涵蓋「Bry 對特定對象的喇稱/口頭禪, 不代表對你也用, 也不代表你該模仿」
- 不只寫「其他角色說的話」
- 位置: proxy.py 群聊歷史組裝那段
- 範圍: 不動修法 1 (source_pair) 跟修法 2 (短期記憶反框架語句)
- 跟修法 2 做法一致: 在資訊本身旁邊加使用說明

這個 v2 驗證修法後:
- _build_messages_group 注入的 group 歷史前面有 [使用說明] 反框架語句
- 反框架語句涵蓋 Bry 對特定對象的喇稱/口頭禪, 不只其他角色

Mock 範圍 (跟 v1 一致):
- mock memory.get_group_history 回傳 20 條, 內含 Bry 罵人用的「豬頭」字眼
- mock _load_bry_recent 回傳空
- 直接呼叫 _build_messages_group, 驗證 group 歷史前面有反框架語句
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


class TestGroupHistoryFramingV2(unittest.TestCase):
    """驗證修法 3 (after 修法) — 群聊歷史反框架語句"""

    def setUp(self):
        # 跟 v1 一致的 mock 群聊歷史
        self.fake_group_history = [
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭, 還說自己不是笨蛋",  "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你又想太多了吧。",         "is_private": False},
            {"role": "assistant", "speaker": "agent_mai",  "content": "哼, 你才是笨蛋。",             "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭吧, 真的覺得你好吵。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "我就在啊, 你這句聽起來不舒服。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你這句太甜了啦。",         "is_private": False},
        ] * 3  # 18 條 + mai 1 條 = 19 條, 再加 1 條到 20 條
        self.fake_group_history = self.fake_group_history[:20]
        self.soul_text = "你是 Miku。... (Miku 的人格設定) ..."

    def test_v2_group_history_has_anti_framing(self):
        """修法 3 v2: 群聊歷史前面有反框架語句, 涵蓋 Bry 對特定對象的喇稱"""
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-02 21:42 週六 晚上 (EDT)",
            )

            # Bry 拍板原話: 在資訊本身旁邊加使用說明
            # 找包含「Bry 對特定對象的喇稱」的 system 訊息 (不一定是第一條, identity_anchor 在最前)
            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            anti_framing_keywords = [
                "Bry 對特定對象的喇稱",  # Bry 拍板原話核心
                "不代表 Bry 對你也用",     # 修法 3 措辭
                "不代表你該模仿",            # Bry 拍板原話
                "那段對話雙方的相處方式",   # 強調是特定對話, 不是普世
                "用你自己的方式回應",      # 跟修法 2 一致, 自然運用
            ]
            for keyword in anti_framing_keywords:
                self.assertIn(
                    keyword, all_system_content,
                    f"修法 3 v2 期望 system 訊息「包含」反框架語句「{keyword}」, "
                    f"實際合併內容: {all_system_content[:500]!r}"
                )
            print(f"[v2] group history has anti-framing text (Bry 拍板 5 個 keyword 全在 system 訊息)")

    def test_v2_anti_framing_appears_before_pig_content(self):
        """修法 3: 反框架語句在「豬頭」字眼之前 (LLM 讀到 Bry 罵人內容前先看說明)

        SOUL-TECH-DEBT-001 校準（test-owned bug, 非 production defect）:
        本測試的前提是「LLM 讀得到 Bry 罵人的『豬頭』內容」, 但修法 7
        (Bry 拍板 2026-08-04 13:24, proxy.py:896-899) 在 Bry 不在線時
        會 `continue` 掉 group history 裡**所有** speaker=='bryan' 的訊息。
        本檔寫於 2026-08-03 23:08, 早於修法 7, 建構 _build_messages_group
        時沒帶新參數 bry_latest_ts, 預設 0 (proxy.py:692) →
        _is_bry_online(0, now) 恆 False (proxy.py:946) → Bry 的「豬頭」
        兩條全被丟掉, pig_pos 恆 -1。

        修法: 明確給一個「在線」的 bry_latest_ts, 讓修法 7 的過濾條件不成立,
        恢復本測試原本要驗的 ordering。反框架語句本身 (proxy.py:882-890)
        本來就在 Bry 過濾之前, 所以另外兩支測試一直綠 —— 這也證明紅的是
        Bry 內容缺席, 不是反框架語句缺席。
        """
        import time
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-02 21:42 週六 晚上 (EDT)",
                # 修法 7+9: Bry 在線 (now - ts <= STALE_THRESHOLD_SEC) 才不過濾
                bry_latest_ts=int(time.time()),
            )

            all_content = "\n".join(m["content"] for m in messages)
            anti_framing_pos = all_content.find("Bry 對特定對象的喇稱")
            pig_pos = all_content.find("你是豬頭")
            self.assertNotEqual(anti_framing_pos, -1, "應該有反框架語句")
            self.assertNotEqual(pig_pos, -1, "應該有 Bry 罵人「豬頭」內容")
            self.assertLess(
                anti_framing_pos, pig_pos,
                f"反框架語句應該在「豬頭」字眼之前, "
                f"反框架 pos={anti_framing_pos}, pig pos={pig_pos}"
            )
            print(f"[v2] anti-framing text appears before pig content (pos {anti_framing_pos} < {pig_pos})")

    def test_v2_bry_offline_boundary_drops_bry_lines_only(self):
        """邊界 (SOUL-TECH-DEBT-001 新增): 修法 7 只丟 Bry 的行, 其他角色的行必須留下。

        兩側都釘死, 避免日後有人「順手」把整段 group history 一起丟掉 ——
        那是 修法 7 明文禁止的（proxy.py:896-899 只 continue speaker=='bryan'）。
        """
        import time
        now = int(time.time())
        stale_ts = now - proxy.STALE_THRESHOLD_SEC - 1   # 明確超過 30 分鐘門檻

        def _build(bry_latest_ts):
            with patch.object(proxy, "_load_bry_recent", return_value=[]), \
                 patch.object(proxy, "MAX_GROUP", 20):
                fake_memory = MagicMock()
                fake_memory.get_group_history.return_value = self.fake_group_history
                return proxy._build_messages_group(
                    agent_id="agent_miku",
                    soul=self.soul_text,
                    current_input="",
                    memory_context="",
                    memory=fake_memory,
                    mood=0.0,
                    user_id="bryan",
                    current_time="2026-08-02 21:42 週六 晚上 (EDT)",
                    bry_latest_ts=bry_latest_ts,
                )

        online = "\n".join(m["content"] for m in _build(now))
        offline = "\n".join(m["content"] for m in _build(stale_ts))

        # Bry 在線 → Bry 的「豬頭」行在
        self.assertIn("你是豬頭", online, "Bry 在線時 Bry 的群聊行不該被丟")
        # Bry 不在線（超門檻）→ Bry 的行被丟, 其他角色 (agent_mai) 的行保留
        self.assertNotIn("你是豬頭", offline, "Bry 不在線時 Bry 的群聊行應被丟")
        self.assertIn("你才是笨蛋", offline,
                      "Bry 不在線時, 其他角色 (agent_mai) 的行必須保留 —— "
                      "修法 7 只過濾 speaker=='bryan'")
        # 反框架語句兩種情況都在 (它掛在 proxy.py:882 的 raw group 上, 早於 Bry 過濾)
        self.assertIn("Bry 對特定對象的喇稱", offline,
                      "反框架語句不該被 Bry 過濾連帶刪掉")
        print("[v2] 修法 7 邊界: Bry 在線→留 Bry 行 / Bry 離線→只丟 Bry 行, 其他角色行保留")

    def test_v2_does_not_break_other_agents(self):
        """修法 3: 反框架語句對其他角色 (yua/akane/mahiru/mai) 一致 (不限 miku)"""
        for agent_id in ["agent_yua", "agent_akane", "agent_mahiru", "agent_mai", "agent_ram"]:
            with patch.object(proxy, "_load_bry_recent", return_value=[]), \
                 patch.object(proxy, "MAX_GROUP", 20):
                fake_memory = MagicMock()
                fake_memory.get_group_history.return_value = self.fake_group_history

                messages = proxy._build_messages_group(
                    agent_id=agent_id,
                    soul=self.soul_text,
                    current_input="",
                    memory_context="",
                    memory=fake_memory,
                    mood=0.0,
                    user_id="bryan",
                    current_time="2026-08-02 21:42 週六 晚上 (EDT)",
                )

                all_system_content = "\n".join(
                    m["content"] for m in messages if m["role"] == "system"
                )
                self.assertIn(
                    "Bry 對特定對象的喇稱", all_system_content,
                    f"{agent_id} 群聊歷史也應該有反框架語句"
                )
        print(f"[v2] anti-framing text consistent across 5 agents (yua/akane/mahiru/mai/ram)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
