"""
test_stale_filter_v2.py — 修法 7 後: stale Bry user 訊息被過濾

Bry 拍板 2026-08-04 13:24:
- 修法: proxy.py 內 _build_messages_group / _build_messages_private 加過濾邏輯
  - 從 memory.db 取含 timestamp 的 history
  - 透過 _is_bry_online 判定 Bry 是否在線 (看 Bry 最新一條 user 訊息 timestamp)
  - STALE_THRESHOLD_SEC = 30 * 60 (30 分鐘, 跟 proactive_dm silence_timeout 對齊)
  - Bry 不在線 → 過濾所有 Bry user 訊息 (group 內其他角色訊息保留)
- 範圍: 只動 proxy.py 的 _build_messages_group / _build_messages_private
  (Bry 派工原話列的 4 個 function 中, _load_group / _load_private 純讀 JSON 沒 timestamp,
   過濾邏輯必須在 _build_* 內做用 memory.db 的 timestamp)

這個 v2 驗證修法後邏輯:
- (a) 群聊場景: Bry 不在線時, group 內 stale Bry user 訊息被過濾掉
- (b) 私聊場景: Bry 不在線時, private 內 stale Bry user 訊息被過濾掉
- (c) 群聊場景: 過濾後 group 整段空 (Bry 也不在場, 沒其他 agent 訊息), prompt 組裝仍正常
  (Bry 派工原話邊界情況要求 mock test 涵蓋)
- (d) 私聊場景: 過濾後 private 整段空, prompt 組裝仍正常
- (e) 群聊場景: Bry 在線 (< 30 分鐘), Bry 訊息全部保留 (沒被過濾, 跟現狀行為一致)

Mock 範圍:
- mock memory.get_group_history 回傳含 timestamp 的 history
- mock memory.get_recent_with_meta 回傳含 timestamp 的 history (修法後用)
- patch time.time 回傳 NOW, 讓 proxy 內 time.time() 跟 mock history 內 timestamp 比較
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm import proxy


# 模擬 8/4 07:30 EDT — Bry 還在睡覺
NOW = 1_700_000_000
# Bry 不在線: 4h42m 前 (16920 秒)
BRY_STALE_TS = NOW - 16920
# Bry 在線: 5 分鐘前 (300 秒)
BRY_FRESH_TS = NOW - 300
# 角色訊息 fresh: 5 分鐘前
AGENT_FRESH_TS = NOW - 300


class TestStaleFilterPost(unittest.TestCase):
    """驗證修法 7 後 — stale Bry user 訊息被過濾"""

    def setUp(self):
        self.soul_text = "你是 Miku。... (Miku 的人格設定) ..."

    def _make_group_history_with_stale_bry(self) -> list:
        """group 內有 1 條 stale Bry 訊息 (4h42m 前) + 1 條 fresh agent 訊息 + 18 條其他"""
        history = [{
            "role": "user", "speaker": "bryan",
            "content": "Bry 8/4 02:48 講的話 (stale)",
            "timestamp": BRY_STALE_TS, "is_private": False,
        }]
        history.append({
            "role": "assistant", "speaker": "agent_yua",
            "content": "yua 8/4 07:25 講的話 (fresh)",
            "timestamp": AGENT_FRESH_TS, "is_private": False,
        })
        for i in range(18):
            history.append({
                "role": "assistant", "speaker": "agent_other",
                "content": f"other agent msg {i}",
                "timestamp": NOW - 600 - i * 30, "is_private": False,
            })
        return history

    def _make_private_history_with_stale_bry(self) -> list:
        """private 內有 1 條 stale Bry 訊息 (3.5 天前) + 1 條 fresh agent 訊息 + 8 條其他"""
        bry_super_stale_ts = NOW - 302400  # 3.5 天前
        history = [{
            "role": "user", "content": "Bry 8/2 07:17 講的話 (stale)",
            "timestamp": bry_super_stale_ts,
        }]
        history.append({
            "role": "assistant", "content": "mahiru 8/4 07:25 講的話 (fresh)",
            "timestamp": AGENT_FRESH_TS,
        })
        for i in range(8):
            history.append({
                "role": "assistant", "content": f"mahiru msg {i}",
                "timestamp": NOW - 700 - i * 40,
            })
        return history

    def test_a_post_group_stale_bry_filtered(self):
        """修法後: group 內 stale Bry 訊息被過濾掉
        Bry 4h42m 前講過一句, Bry 不在線, 過濾掉, 角色看到的 user 訊息是空的
        """
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = self._make_group_history_with_stale_bry()

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=BRY_STALE_TS,  # 修法 9: 跨 session Bry 最後時間, 這裡 Bry 不在線
            )

            # 找 user role 訊息 (Bry 訊息)
            user_msgs = [m for m in messages if m["role"] == "user"]
            stale_bry_found = any(
                "Bry 8/4 02:48 講的話 (stale)" in m.get("content", "")
                for m in user_msgs
            )
            self.assertFalse(
                stale_bry_found,
                "修法後 (v2) 期望 group 內 stale Bry 訊息被過濾, 實際 user msgs: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            # agent_yua fresh 訊息應該保留 (過濾只對 Bry user 訊息生效)
            yua_fresh_found = any(
                "yua 8/4 07:25 講的話 (fresh)" in m.get("content", "")
                for m in messages
            )
            self.assertTrue(
                yua_fresh_found,
                "修法後 (v2) 期望 agent_yua fresh 訊息保留, 實際 messages: "
                f"{[m['content'][:50] for m in messages]}"
            )
            print(f"[v2 (a)] group 內 stale Bry 訊息被過濾, agent_yua fresh 保留")

    def test_b_post_private_stale_bry_filtered(self):
        """修法後: private 內 stale Bry 訊息被過濾掉
        Bry 3.5 天前講過一句, Bry 不在線, 過濾掉
        """
        with patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            # 修法後用 get_recent_with_meta (含 timestamp)
            fake_memory.get_recent_with_meta.return_value = self._make_private_history_with_stale_bry()

            messages = proxy._build_messages_private(
                agent_id="agent_mahiru",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=BRY_STALE_TS,  # 修法 9: 跨 session Bry 最後時間, Bry 3.5 天前講過
            )

            user_msgs = [m for m in messages if m["role"] == "user"]
            stale_bry_found = any(
                "Bry 8/2 07:17 講的話 (stale)" in m.get("content", "")
                for m in user_msgs
            )
            self.assertFalse(
                stale_bry_found,
                "修法後 (v2) 期望 private 內 stale Bry 訊息被過濾, 實際 user msgs: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            # agent_mahiru fresh 訊息應該保留
            mahiru_fresh_found = any(
                "mahiru 8/4 07:25 講的話 (fresh)" in m.get("content", "")
                for m in messages
            )
            self.assertTrue(
                mahiru_fresh_found,
                "修法後 (v2) 期望 agent_mahiru fresh 訊息保留"
            )
            print(f"[v2 (b)] private 內 stale Bry 訊息被過濾, agent_mahiru fresh 保留")

    def test_c_post_group_empty_history_after_filter_safe(self):
        """修法後邊界情況: group 過濾後整段空, prompt 組裝仍正常
        (Bry 派工原話: 過濾後 history 空要確保 prompt 組裝不出錯)
        場景: Bry 8/4 02:48 講一句後 Bry 不在線, group 只有 Bry 訊息 + 1 條 agent_other,
        過濾 Bry 後只剩 agent_other, 仍 OK

        更極端: group 只有 Bry 訊息, 過濾後空, prompt 組裝仍正常 (只有 system 訊息)
        """
        # 最極端: group 只有 Bry 訊息, 過濾後空
        history_with_only_stale_bry = [{
            "role": "user", "speaker": "bryan",
            "content": "Bry 很久以前講的話 (stale)",
            "timestamp": BRY_STALE_TS, "is_private": False,
        }]
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = history_with_only_stale_bry

            # 應該不出錯
            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",  # proactive 觸發
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=BRY_STALE_TS,  # 修法 9: Bry 不在線
            )

            # 至少要有 system 訊息 (SOUL + 當下時間)
            system_msgs = [m for m in messages if m["role"] == "system"]
            self.assertGreater(
                len(system_msgs), 0,
                "修法後 (v2) 期望 prompt 至少包含 system 訊息, 實際 messages 為空"
            )
            # user 訊息應該只有 stale Bry 被過濾後是空的 (但 proactive current_input='' 也空)
            user_msgs = [m for m in messages if m["role"] == "user"]
            self.assertEqual(
                len(user_msgs), 0,
                "修法後 (v2) 期望過濾後 user 訊息為空, 實際: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            print(f"[v2 (c)] group 過濾後空, prompt 組裝仍正常 (system msgs={len(system_msgs)})")

    def test_d_post_private_empty_history_after_filter_safe(self):
        """修法後邊界情況: private 過濾後整段空, prompt 組裝仍正常
        場景: private 只有 Bry 訊息 (Bry 不在線), 過濾後空
        """
        history_with_only_stale_bry = [{
            "role": "user", "content": "Bry 很久以前講的話 (stale)",
            "timestamp": BRY_STALE_TS,
        }]
        with patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            fake_memory.get_recent_with_meta.return_value = history_with_only_stale_bry

            messages = proxy._build_messages_private(
                agent_id="agent_mahiru",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=BRY_STALE_TS,  # 修法 9: Bry 不在線
            )

            system_msgs = [m for m in messages if m["role"] == "system"]
            self.assertGreater(
                len(system_msgs), 0,
                "修法後 (v2) 期望 prompt 至少包含 system 訊息, 實際 messages 為空"
            )
            user_msgs = [m for m in messages if m["role"] == "user"]
            self.assertEqual(
                len(user_msgs), 0,
                "修法後 (v2) 期望過濾後 user 訊息為空, 實際: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            print(f"[v2 (d)] private 過濾後空, prompt 組裝仍正常 (system msgs={len(system_msgs)})")

    def test_e_post_fresh_bry_messages_kept(self):
        """修法後: Bry 在線 (< 30 分鐘), Bry 訊息全部保留 (沒被過濾, 跟現狀行為一致)
        場景: Bry 5 分鐘前講一句, Bry 在線, Bry 訊息保留
        """
        history_with_fresh_bry = [
            {
                "role": "user", "speaker": "bryan",
                "content": "Bry 5 分鐘前講的話 (fresh)",
                "timestamp": BRY_FRESH_TS, "is_private": False,
            },
            {
                "role": "assistant", "speaker": "agent_yua",
                "content": "yua 5 分鐘前講的話 (fresh)",
                "timestamp": AGENT_FRESH_TS, "is_private": False,
            },
        ]
        for i in range(18):
            history_with_fresh_bry.append({
                "role": "assistant", "speaker": "agent_other",
                "content": f"other agent msg {i}",
                "timestamp": NOW - 600 - i * 30, "is_private": False,
            })
        with patch.object(proxy, "_load_bry_recent", return_value=[]), \
             patch.object(proxy, "MAX_GROUP", 20), \
             patch("time.time", return_value=NOW):
            fake_memory = MagicMock()
            fake_memory.get_group_history.return_value = history_with_fresh_bry

            messages = proxy._build_messages_group(
                agent_id="agent_miku",
                soul=self.soul_text,
                current_input="",
                memory_context="",
                memory=fake_memory,
                mood=0.0,
                user_id="bryan",
                current_time="2026-08-04 07:30 (EDT)",
                bry_latest_ts=BRY_FRESH_TS,  # 修法 9: Bry 在線 (5 分鐘前)
            )

            user_msgs = [m for m in messages if m["role"] == "user"]
            fresh_bry_found = any(
                "Bry 5 分鐘前講的話 (fresh)" in m.get("content", "")
                for m in user_msgs
            )
            self.assertTrue(
                fresh_bry_found,
                "修法後 (v2) 期望 Bry 在線時 fresh Bry 訊息保留, 實際 user msgs: "
                f"{[m['content'][:50] for m in user_msgs]}"
            )
            print(f"[v2 (e)] Bry 在線 (5 分鐘前) fresh 訊息保留, 過濾邏輯不誤觸")


if __name__ == "__main__":
    unittest.main(verbosity=2)
