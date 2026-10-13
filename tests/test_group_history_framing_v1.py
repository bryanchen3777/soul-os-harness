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
import time
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
        """2026-10-12 更新: v1 前提「還沒套修法 3」已過期, 改驗現行生產契約。

        兩處過期:
        1. 修法 3 本身已套用 —— proxy.py:882-890 現在無條件在 group 歷史組裝前
           注入 [使用說明] 反框架語句。所以「不得包含反框架語句」是錯的期望。
        2. 修法 7 (Bry 拍板 2026-08-04 13:24, 修法 3 後一天) 會在 Bry 不在線時
           過濾 stale 的 Bry user 訊息 (proxy.py:895-900)。原測試沒傳
           bry_latest_ts (預設 0 = Bry 從未講過話 → 不在線), 那些「豬頭」訊息
           早被丟掉, 所以 pig_msgs = 0。

        現行契約 (兩種 Bry 在線狀態都要驗, 這才是真正的迴歸防護):
        - Bry 在線: 「豬頭」訊息進得去 **而且** 反框架語句同時存在
          → 這正是修法 3 要解決的原始風險, 必須成立
        - Bry 不在線: stale Bry 訊息被過濾 (修法 7), 但反框架語句仍存在
        """
        anti_framing_keywords = [
            "不代表 Bry 對你也用",
            "也不代表你該模仿",
            "那是那段對話雙方的相處方式",
        ]

        def _build(bry_latest_ts):
            with patch.object(proxy, "_load_bry_recent", return_value=[]), \
                 patch.object(proxy, "MAX_GROUP", 20):
                fake_memory = MagicMock()
                fake_memory.get_group_history.return_value = self.fake_group_history
                return proxy._build_messages_group(
                    agent_id="agent_miku",
                    soul=self.soul_text,
                    current_input="",  # proactive 觸發
                    memory_context="",
                    memory=fake_memory,
                    mood=0.0,
                    user_id="bryan",
                    current_time="2026-08-02 21:42 週六 晚上 (EDT)",
                    bry_latest_ts=bry_latest_ts,
                )

        # ── 情況 A: Bry 在線 (剛講過話) ──
        messages_online = _build(int(time.time()))
        all_system_online = "\n".join(
            m["content"] for m in messages_online if m["role"] == "system"
        )
        pig_msgs_online = [
            m for m in messages_online if "豬頭" in m.get("content", "")
        ]
        self.assertGreater(
            len(pig_msgs_online), 0,
            "Bry 在線時 group 歷史應該保留 Bry 說過的「豬頭」字眼 (跟查證事實一致)"
        )
        # 修法 3 的核心: LLM 看得到「豬頭」時, 反框架語句必須同時在場
        for keyword in anti_framing_keywords:
            self.assertIn(
                keyword, all_system_online,
                f"修法 3 期望 Bry 在線時 (「豬頭」字眼可見) 反框架語句「{keyword}」"
                f"必須同時存在, 否則 LLM 會模仿 Bry 罵人的用詞"
            )

        # ── 情況 B: Bry 不在線 (bry_latest_ts=0) ──
        # 修法 7: stale Bry user 訊息被過濾, 但反框架語句仍在
        messages_offline = _build(0)
        all_system_offline = "\n".join(
            m["content"] for m in messages_offline if m["role"] == "system"
        )
        self.assertEqual(
            len([m for m in messages_offline if "豬頭" in m.get("content", "")]), 0,
            "修法 7 期望 Bry 不在線時過濾 stale Bry user 訊息 (「豬頭」字眼不該出現)"
        )
        for keyword in anti_framing_keywords:
            self.assertIn(
                keyword, all_system_offline,
                f"修法 3 期望群聊歷史反框架語句「{keyword}」在 Bry 不在線時依然存在 "
                f"(因為仍有 agent_mai 的群聊訊息需要框架)"
            )

        print(
            "[現行契約] 修法 3 反框架語句在 Bry 在線/不在線兩種狀態下都存在; "
            "修法 7 在 Bry 不在線時過濾 stale Bry「豬頭」訊息"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
