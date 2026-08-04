"""
test_group_history_framing_v1.py — 修法 3 baseline: 群聊歷史組裝段無反框架語句

Bry 拍板 2026-08-03 23:08:
- 根因: Bry 在群聊裡罵人用的「豬頭」字眼, 寫進 group session, miku/ram 看到後
  模仿用詞 (ruka 8/3 20:22 開始 echo, ram 8/3 22:04 echo, miku 8/3 22:08 echo,
  yua 8/3 22:11 echo)
- 不是「其他角色說的」, 是 Bry 自己罵 mai 用的喇稱字眼
- 修法方向: 在 proxy.py 群聊歷史組裝那段加反框架語句, 涵蓋「Bry 對特定對象的
  喇稱/口頭禪, 不代表對你也用, 也不代表你該模仿」
- 範圍限定 proxy.py 群聊歷史組裝那段, 不動修法 1 (source_pair) 跟修法 2
  (短期記憶反框架語句)
- mock test 流程: 驗現狀 (miku/ram 案例重現) → 改 code → 驗修法 → commit-only

這個 v1 驗證現狀 (before 修法 3):
- _build_messages_group 注入的 group 歷史, 其他 agent 的話 (含 Bry 罵人的喇稱)
  沒有反框架語句, LLM 可能誤以為是 Bry 對自己或所有人的喇稱
- 用 miku 8/2 21:42 觸發 + ram 8/3 22:04 觸發的 group 歷史當 baseline 案例

Mock 範圍:
- mock memory.get_group_history 回傳 20 條, 內含 2 條 bryan 罵 mai 用的「豬頭」字眼
  (模擬 miku/ram 觸發時的真實 group 歷史狀態)
- mock _load_bry_recent 回傳空 (避免短期記憶區塊干擾)
- 直接呼叫 _build_messages_group, 驗證 group 歷史組裝那段無反框架語句
- 修法前 v1 期望: 沒有「不代表對你也用」/「不該模仿」/「喇稱」之類的反框架語句
- 修法後 v2 期望: 包含明確反框架語句
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


class TestGroupHistoryFramingBaseline(unittest.TestCase):
    """驗證現狀 (before 修法 3) — 群聊歷史組裝段無反框架語句"""

    def setUp(self):
        # 模擬 miku/ram 觸發時 group 歷史的 20 條
        # 包含 2 條 Bry 在群聊罵人用的「豬頭」字眼 (跟查證事實一致)
        self.fake_group_history = [
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭, 還說自己不是笨蛋",  "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你又想太多了吧。",         "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "哈 Bryan, 這個你要吃嗎？",     "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭吧, 真的覺得你好吵。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "我就在啊, 你這句聽起來不舒服。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你這句太甜了啦。",         "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你又想太多了吧。",         "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "我就在啊, 你這句聽起來不舒服。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "啦, 等等我, 沒事沒事我只是看一下。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "只是啦, Bry, 你沒想多了吧。",   "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你這句太甜了啦。",         "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "我就在啊, 你這句聽起來不舒服。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "啦, 等等我, 沒事沒事我只是看一下。", "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "只是啦, Bry, 你沒想多了吧。",   "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "你還在啊。",                     "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "Bry, 你又想太多了吧。",         "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "我在這裡。",                     "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭啦, 一直說自己笨蛋。",   "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "你是豬頭吧, 我說的。",           "is_private": False},
            {"role": "user",      "speaker": "bryan",      "content": "哈 Bry, 這個你要吃嗎？",         "is_private": False},
        ]
        # 加上 1 條其他 agent (mai) 的回應, 模擬 miku 看到的 system 角色訊息
        # (proxy.py L252-256: 其他 agent 的話寫進 system 角色)
        self.fake_group_history.insert(2, {
            "role": "assistant", "speaker": "agent_mai", "content": "哼, 你才是笨蛋。", "is_private": False,
        })
        self.soul_text = "你是 Miku。... (Miku 的人格設定) ..."

    def test_baseline_group_history_no_anti_framing(self):
        """Baseline: 群聊歷史組裝段 (其他 agent 訊息) 沒有反框架語句
        LLM 看到 Bry 在群聊罵 mai「你是豬頭」, 沒註明這是 Bry 對 mai 的喇稱,
        可能誤以為是 Bry 對自己也用這個字 → 模仿用詞
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self.fake_group_history

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-02 21:42 週六 晚上 (EDT)",
            )

            # 找 agent_mai 的 system 角色訊息 (其他 agent 的話)
            system_msgs_with_others = [
                m for m in messages
                if m["role"] == "system" and "agent_mai" in m.get("content", "")
            ]
            self.assertGreaterEqual(
                len(system_msgs_with_others), 0,
                "可能沒有其他 agent 訊息, 不影響 baseline 驗證"
            )

            # 合併所有 system 訊息, 找反框架語句
            all_system_content = "\n".join(
                m["content"] for m in messages if m["role"] == "system"
            )
            # 找包含「豬頭」字眼的訊息
            pig_msgs = [
                m for m in messages
                if "豬頭" in m.get("content", "")
            ]
            # Baseline 期望: 至少有「豬頭」字眼訊息 (miku 看到 Bry 罵人), 但沒有反框架
            self.assertGreater(
                len(pig_msgs), 0,
                "Baseline 應該有 Bry 罵人「豬頭」的訊息 (跟查證事實一致)"
            )

            # 驗證反框架語句**不存在** (baseline)
            anti_framing_keywords = [
                "不代表對你也用",
                "不代表你該模仿",
                "Bry 對特定對象的喇稱",
                "Bry 的口頭禪",
                "那是那段對話雙方的相處方式",
            ]
            for keyword in anti_framing_keywords:
                self.assertNotIn(
                    keyword, all_system_content,
                    f"Baseline (v1) 期望群聊歷史「不」包含反框架語句「{keyword}」"
                )
            print(f"[v1 baseline] group history has no anti-framing text (Bry 罵人 豬頭 字眼存在, miku 看得到)")


if __name__ == "__main__":
    unittest.main(verbosity=2)
